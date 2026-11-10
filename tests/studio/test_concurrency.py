"""One concurrency model.

Every route handler runs in the threadpool, never on the event loop, and
holds `StudioState.lock` across its mutation *and* its render, so two
requests can never interleave a half-applied edit with a render of it.
`save()` snapshots the state under the same lock; the caches carry their
own. A cheap request (`/health`) must never wait on a render.
"""

import concurrent.futures as cf
import inspect
import sys
import threading
import time

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from synthlab.studio import service
from synthlab.studio.server import create_app
from synthlab.studio.state import StudioState


@pytest.fixture
def app(tmp_path):
    return create_app(data_dir=tmp_path)


@pytest.fixture
def client(app):
    # `with`: one event loop for every request, as under uvicorn. Without
    # it TestClient spins up a fresh loop per request, and a render that
    # blocks one loop could never be seen stalling another request.
    with TestClient(app) as c:
        yield c


def test_save_during_mutation_does_not_raise(tmp_path):
    s = StudioState(tmp_path)
    # switch threads as often as possible, so a save that walks live state
    # outside the lock meets the churn mid-iteration
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    stop = threading.Event()
    errors: list[Exception] = []

    def churn():
        try:
            _churn()
        except Exception as e:  # noqa: BLE001 — a dead churner proves nothing
            errors.append(e)

    def _churn():
        i = 0
        while not stop.is_set():
            with s.lock:
                s.arrangement.drum_params[f"v{i % 500}"] = {"level": i}
                s.arrangement.drum_params.pop(f"v{(i + 250) % 500}", None)
                s.curves[f"c{i % 500}"] = [[0.0, float(i)]]
                s.curves.pop(f"c{(i + 250) % 500}", None)
            i += 1
            if i % 64 == 0:
                time.sleep(0)  # let the saver in: locks are not fair

    t = threading.Thread(target=churn)
    t.start()
    try:
        for _ in range(200):
            try:
                s.save()
            except Exception as e:  # noqa: BLE001 — any escape is the bug
                errors.append(e)
    finally:
        stop.set()
        t.join()
        sys.setswitchinterval(old_interval)
    assert not errors


def test_render_does_not_block_health(client, monkeypatch):
    """A render in flight must not stall a cheap request. The render is
    slowed deliberately so the window is wide enough to measure."""
    real = service.render_loop  # StudioService._ensure looks it up here
    started = threading.Event()

    def slow_render(*args, **kwargs):
        started.set()
        time.sleep(0.8)
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "render_loop", slow_render)
    with cf.ThreadPoolExecutor(2) as ex:
        slow = ex.submit(client.post, "/fragment/preview", data={"view": "perform"})
        assert started.wait(10)
        t0 = time.perf_counter()
        assert client.get("/health").status_code == 200
        dt = time.perf_counter() - t0
        assert slow.result().status_code == 200
    assert dt < 0.5


def _one_section_song():
    from synthlab.studio import schema
    from synthlab.studio import song_model as sm
    from synthlab.synth import Patch

    song = sm.new_song(120.0, "A1", "minor")
    t = sm.add_track(song, "synth", "Lead", patch=Patch(release=0.05).to_json())
    sid = sm.add_section(song, "A", 1)
    riff = {"cells": [["A2", 0], ["E3", 2]], "bars": 1, "steps_per_bar": 4}
    sm.set_cell(song, sid, t, sm.add_pattern(song, "notes", "r", riff))
    return schema.conform(song, schema.nodes().SONG)


@pytest.mark.parametrize(
    "url", ["/export/stems.zip", "/export/song.wav", "/song/plan.json"]
)
def test_a_long_render_does_not_block_a_fast_request(client, app, monkeypatch, url):
    """The song exports and the plan snapshot the song under the lock and
    render with it released (`routing.render_outside_lock`): a 1.6 ms
    request used to wait 3.85 s behind /export/stems.zip. The render is
    slowed so the window is wide enough to measure."""
    s: StudioState = app.state.studio
    s.song = _one_section_song()
    real = service.section_master_audio
    started = threading.Event()

    def slow(*args, **kwargs):
        started.set()
        time.sleep(0.8)
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "section_master_audio", slow)
    with cf.ThreadPoolExecutor(2) as ex:
        long = ex.submit(client.get, url)
        assert started.wait(10)
        t0 = time.perf_counter()
        assert client.get("/patch.json").status_code == 200
        # and an edit lands mid-render without touching the render's copy
        assert client.post("/fragment/patch", data={"cutoff": "777"}).status_code == 200
        dt = time.perf_counter() - t0
        assert long.result().status_code == 200
    assert dt < 0.5
    assert s.patch.cutoff == pytest.approx(777)


@pytest.mark.parametrize("hold", [0, 1])
def test_a_key_render_does_not_block_an_edit(client, app, monkeypatch, hold):
    """/note renders outside the lock: a first press used to
    hold the studio lock for its whole render (20-224 ms), so a slider post
    queued behind it, and a 24-key warm-up serialised on the lock. The key
    renders the patch as it was when the press arrived."""
    from synthlab.studio.routes import audio

    s: StudioState = app.state.studio
    real = audio.render_note
    started, go = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        started.set()
        assert go.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(audio, "render_note", slow)
    before = audio.note_key(s.patch, "C4", bool(hold)).split(":")[1]
    with cf.ThreadPoolExecutor(2) as ex:
        key = ex.submit(client.get, f"/note/C4.wav?hold={hold}")
        assert started.wait(10)
        t0 = time.perf_counter()
        r = client.post("/fragment/patch", data={"cutoff": "777"})
        dt = time.perf_counter() - t0
        go.set()
        res = key.result()
    assert r.status_code == 200 and dt < 0.5
    assert s.patch.cutoff == pytest.approx(777)
    # the key is the patch it was asked for, and says so
    assert res.status_code == 200 and res.headers["x-patch-key"] == before


def test_key_renders_run_concurrently_and_coalesce(client, app, monkeypatch):
    """Up to four key renders at once (the warm-up's four workers), and two
    requests for the same key share one render."""
    from synthlab.studio.routes import audio

    real = audio.render_note
    lock = threading.Lock()
    state = {"now": 0, "peak": 0, "calls": 0}

    def counted(*args, **kwargs):
        with lock:
            state["now"] += 1
            state["calls"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.3)
        try:
            return real(*args, **kwargs)
        finally:
            with lock:
                state["now"] -= 1

    monkeypatch.setattr(audio, "render_note", counted)
    urls = [f"/note/{n}4.wav" for n in ("C", "D", "E", "F", "G", "A")]
    with cf.ThreadPoolExecutor(8) as ex:
        rs = list(ex.map(client.get, urls + ["/note/C4.wav", "/note/C4.wav"]))
    assert all(r.status_code == 200 for r in rs)
    assert 2 <= state["peak"] <= 4
    assert state["calls"] == 6  # the two repeats of C4 rendered nothing new


def test_an_edit_during_an_export_does_not_reach_it(client, app, monkeypatch):
    """The export renders the song as it was when it started: an edit that
    lands mid-render changes the session, not the snapshot."""
    s: StudioState = app.state.studio
    s.song = _one_section_song()
    before = client.get("/export/song.wav").content
    real = service.section_master_audio
    go, started = threading.Event(), threading.Event()

    def gated(*args, **kwargs):
        started.set()
        assert go.wait(10)
        return real(*args, **kwargs)

    s.song = _one_section_song()  # same content, so the memo keys match
    client.app.state.svc.song_memo = type(client.app.state.svc.song_memo)(16)
    client.app.state.svc.stems = type(client.app.state.svc.stems)()
    client.app.state.svc.cache = type(client.app.state.svc.cache)()
    monkeypatch.setattr(service, "section_master_audio", gated)
    with cf.ThreadPoolExecutor(1) as ex:
        export = ex.submit(client.get, "/export/song.wav")
        assert started.wait(10)
        with s.lock:  # a song edit, as a route would make it
            s.song = {**s.song, "sections": []}
        go.set()
        assert export.result().content == before


def test_handlers_hold_the_state_lock(client, app):
    """Mutation plus render happen under `s.lock`: while something else
    holds it, an edit cannot land."""
    s: StudioState = app.state.studio
    done = threading.Event()

    def post():
        client.post("/fragment/patch", data={"cutoff": "777"})
        done.set()

    with s.lock:
        t = threading.Thread(target=post)
        t.start()
        assert not done.wait(0.5), "handler ran without taking s.lock"
    t.join(10)
    assert done.is_set()
    assert s.patch.cutoff == pytest.approx(777)


def test_health_and_preview_do_not_take_the_lock(client, app):
    s: StudioState = app.state.studio
    with s.lock:
        with cf.ThreadPoolExecutor(1) as ex:
            r = ex.submit(client.get, "/health").result(timeout=5)
            assert r.status_code == 200
            r = ex.submit(client.get, "/preview/nope.wav").result(timeout=5)
            assert r.status_code == 404


def test_every_route_runs_off_the_event_loop(app):
    """No handler is `async def` unless it is lock-free and does no work
    (only /health): an async handler runs on the event loop, where one
    render stalls every other request."""
    offenders = [
        r.path
        for r in app.routes
        if isinstance(r, APIRoute)
        and inspect.iscoroutinefunction(inspect.unwrap(r.endpoint))
        and r.path != "/health"
    ]
    assert offenders == []


def test_lru_is_thread_safe():
    from synthlab.studio.lru import LRU

    c = LRU(max_entries=8)
    errs: list[Exception] = []

    def w(n):
        try:
            for i in range(2000):
                c.put(f"{n}-{i}", i)
                c.get(f"{n}-{i - 1}")
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=w, args=(n,)) for n in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    stats = c.stats()
    assert not errs and stats["entries"] <= 8
    assert stats["hits"] + stats["misses"] == 8 * 2000


def test_lru_stats_keys_are_unchanged():
    from synthlab.studio.lru import LRU

    c = LRU(max_entries=2)
    assert c.get("a") is None
    c.put("a", 1)
    assert c.get("a") == 1
    c.put("b", 2)
    c.put("c", 3)
    assert c.stats() == {"hits": 1, "misses": 1, "evictions": 1, "entries": 2}


# -- the guard itself, on a fresh router ----------------------
# Every router the studio builds uses `route_class=StudioRoute`, so the
# refusal and the lock apply per router, not only to the app's own router.


def test_studio_route_refuses_an_unmarked_async_handler():
    from fastapi import APIRouter

    from synthlab.studio.routing import StudioRoute

    router = APIRouter(route_class=StudioRoute)
    with pytest.raises(TypeError, match="plain `def`"):

        @router.get("/nope")
        async def nope() -> dict:
            return {}


def test_studio_route_accepts_a_lock_free_async_handler():
    from fastapi import APIRouter

    from synthlab.studio.routing import StudioRoute, lock_free

    router = APIRouter(route_class=StudioRoute)

    @router.get("/fine")
    @lock_free
    async def fine() -> dict:
        return {"ok": True}

    (route,) = router.routes
    assert route.endpoint is fine  # lock-free: never wrapped in the lock


def test_studio_route_wraps_a_sync_handler_in_the_lock(tmp_path):
    """A plain `def` on a fresh router, mounted on the studio app, runs
    under the studio's lock like every built-in route."""
    from fastapi import APIRouter

    from synthlab.studio.routing import StudioRoute, mount

    app = create_app(data_dir=tmp_path)
    router = APIRouter(route_class=StudioRoute)
    seen = []

    @router.get("/probe")
    def probe() -> dict:
        seen.append(app.state.studio.lock._is_owned())
        return {}

    mount(app, router)
    assert TestClient(app).get("/probe").status_code == 200
    assert seen == [True]
