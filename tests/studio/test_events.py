"""Server push: the event bus and `GET /events` (studio/events.py).

The bus is a bounded queue per connection that a publisher only ever
appends to; the route is a Server-Sent Events stream. What is pinned here:

- the bus: bounded queues that drop their oldest and say `resync`,
  coalescing, no cross-talk, nothing done when nobody listens;
- the stream over a REAL socket (uvicorn on a thread, an ephemeral port):
  framing, heartbeat, that neither middleware buffers it, the connection
  cap, and that a client going and the server stopping both end it at once
  and leave no subscriber behind;
- the boundary: a cross-site read is refused, share mode wants its cookie;
- progress: an export's steps in order, the exported bytes the same with
  and without an audience;
- change notices: the regions the request's own OOB refresh was computed
  from, and the asking page's id.

Starlette's TestClient runs the app to completion before it hands a
response back, so it can only be given a stream that ends; every read of
an open stream goes through a socket with a timeout. Nothing here can hang.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import signal
import threading
import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import events
from synthlab.studio.events import QUIET, Event, EventBus, Subscription, Task
from synthlab.studio.security import TOKEN_COOKIE
from synthlab.studio.server import create_app, make_server
from synthlab.studio.service import REGIONS
from tests.studio.test_oob_refresh import _hx, _oob_ids
from tests.studio.test_song_export import _three_section_song, _use

TIMEOUT = 5.0  # every socket read and every wait: a broken stream fails, never hangs


def _until(cond, timeout: float = TIMEOUT) -> float:
    """Seconds until `cond()` held; fails past `timeout`."""
    t0 = time.perf_counter()
    while not cond():
        assert time.perf_counter() - t0 < timeout, "timed out"
        time.sleep(0.005)
    return time.perf_counter() - t0


def _kinds(sub: Subscription) -> list[str]:
    return [e.kind for e in sub.drain()[1]]


# --- the bus --------------------------------------------------------------------


def test_nobody_listening_costs_nothing():
    bus = EventBus()
    bus.publish("progress", {"id": "a"})
    with bus.task("Exporting", 3) as task:
        task.step()
    assert not bus.active and bus.published == 0
    # the default task of every render: no bus at all
    QUIET.step()
    QUIET.say("x")
    with QUIET:
        pass


def test_every_connection_gets_every_event_and_only_while_subscribed():
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish("done", {"id": "1"})
    bus.unsubscribe(b)
    bus.publish("done", {"id": "2"})
    assert [e.data["id"] for e in a.drain()[1]] == ["1", "2"]
    assert [e.data["id"] for e in b.drain()[1]] == ["1"]  # none after it left
    # draining one never empties the other, and a drained queue is empty
    assert a.drain() == (False, [])


def test_a_full_queue_drops_its_oldest_and_asks_for_a_resync():
    bus = EventBus(queue_size=4)
    slow, quick = bus.subscribe(), bus.subscribe()
    for i in range(3):
        bus.publish("done", {"i": i})
    assert [e.data["i"] for e in quick.drain()[1]] == [0, 1, 2]
    for i in range(3, 10):
        bus.publish("done", {"i": i})
    lost, kept = slow.drain()
    assert lost and [e.data["i"] for e in kept] == [6, 7, 8, 9]  # bounded, newest
    assert quick.drain()[0] is True  # 7 into a queue of 4
    assert slow.drain() == (False, [])  # said once
    assert bus.stats()["dropped"] == 6 + 3


def test_a_slow_client_never_blocks_a_publisher_or_grows():
    """A connection nobody drains: 10,000 events from four threads cost a
    bounded queue and no waiting."""
    bus = EventBus(queue_size=8)
    stuck = bus.subscribe()

    def flood():
        for i in range(2500):
            bus.publish("done", {"i": i})

    threads = [threading.Thread(target=flood) for _ in range(4)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join(TIMEOUT)
    assert not any(t.is_alive() for t in threads)
    assert time.perf_counter() - t0 < TIMEOUT
    assert len(stuck.drain()[1]) == 8


def test_progress_of_one_task_coalesces_and_keeps_its_place():
    bus = EventBus()
    sub = bus.subscribe()
    bus.publish("progress", {"id": "a", "done": 1}, key=("progress", "a"))
    bus.publish("progress", {"id": "b", "done": 1}, key=("progress", "b"))
    bus.publish("progress", {"id": "a", "done": 2}, key=("progress", "a"))
    got = [(e.data["id"], e.data["done"]) for e in sub.drain()[1]]
    assert got == [("a", 2), ("b", 1)]


def test_changes_from_one_page_merge_and_others_stay_apart():
    bus = EventBus()
    sub = bus.subscribe()
    bus.changed("tab1", "editor", ["sound-id"])
    bus.changed("tab2", "editor", ["reset-menu"])
    bus.changed("tab1", "editor", ["signal-chain", "sound-id"], whole=True)
    bus.changed("tab1", "drums", ["drum-bank"])
    got = [e.data for e in sub.drain()[1]]
    assert [(d["origin"], d["view"], d["regions"], d["whole"]) for d in got] == [
        ("tab1", "editor", ["sound-id", "signal-chain"], True),
        ("tab2", "editor", ["reset-menu"], False),
        ("tab1", "drums", ["drum-bank"], False),
    ]
    # the merged notice carries the newest count
    assert got[0]["rev"] == f"{bus.boot}:3" and bus.rev == f"{bus.boot}:4"


def test_changes_are_counted_with_nobody_connected():
    """A page that reconnects compares `hello`'s rev with the last it saw."""
    bus = EventBus()
    bus.changed("tab", "editor", ["sound-id"])
    assert bus.rev.endswith(":1") and bus.published == 0


def test_the_cap_refuses_and_a_closed_bus_takes_nobody():
    bus = EventBus(max_streams=2)
    a, b = bus.subscribe(), bus.subscribe()
    assert a and b and bus.full and bus.subscribe() is None
    bus.unsubscribe(a)
    assert not bus.full and bus.subscribe() is not None
    bus.close()
    assert bus.full and bus.subscribe() is None


def test_a_task_throttles_its_steps_and_always_ends(monkeypatch):
    bus = EventBus()
    sub = bus.subscribe()
    with bus.task("Exporting song", 200) as task:
        for _ in range(200):
            task.step()
    lost, got = sub.drain()
    # 200 steps in no time: the first line, then `done` — never 200 events
    assert not lost and [e.kind for e in got] == ["progress", "done"]
    assert got[-1].data == {
        "id": task.id,
        "label": "Exporting song",
        "done": 200,
        "total": 200,
    }
    with pytest.raises(ZeroDivisionError), bus.task("Exporting stems", 2):
        1 / 0  # noqa: B018
    assert _kinds(sub) == ["progress", "failed"]


def test_a_task_never_counts_past_its_total():
    task = Task(EventBus(), "x", 2)
    task.step(5)
    assert (task.done, task.total) == (2, 2)
    task.plan(4)
    assert task.total == 4


def test_frames_are_one_line_of_json():
    out = Event("progress", {"label": "a\nb", "done": 1}).frame()
    assert out == 'event: progress\ndata: {"label":"a\\nb","done":1}\n\n'


# --- the stream, on the event loop -------------------------------------------------


def _collect(bus: EventBus, script, n: int) -> list[str]:
    """`n` chunks of one stream, with `script(bus)` run once it is open."""

    async def run():
        out = []
        gen = bus.stream()
        try:
            out.append(await gen.__anext__())
            script(bus)
            while len(out) < n:
                out.append(await gen.__anext__())
        except StopAsyncIteration:
            pass
        finally:
            await gen.aclose()
        return out

    return asyncio.run(asyncio.wait_for(run(), TIMEOUT))


def test_the_stream_says_hello_then_what_is_published_then_heartbeats():
    bus = EventBus(heartbeat=0.05)
    out = _collect(bus, lambda b: b.publish("done", {"id": "t"}), 3)
    assert out[0] == (
        f'retry: {events.RETRY_MS}\n\nevent: hello\ndata: {{"rev":"{bus.rev}"}}\n\n'
    )
    assert out[1] == 'event: done\ndata: {"id":"t"}\n\n'
    assert out[2] == ": hb\n\n"  # a comment: no event, the connection stays warm
    assert not bus.active  # closing the generator unsubscribed it


def test_an_overflowing_stream_says_resync_first():
    bus = EventBus(queue_size=2, heartbeat=0.05)

    def flood(b):
        for i in range(5):
            b.publish("done", {"i": i})

    out = _collect(bus, flood, 2)
    assert out[1].startswith(f'event: resync\ndata: {{"rev":"{bus.rev}"}}\n\n')
    assert '"i":3' in out[1] and '"i":4' in out[1] and '"i":0' not in out[1]


def test_closing_the_bus_ends_the_stream():
    bus = EventBus(heartbeat=30)  # no heartbeat will end it: close must wake it
    out = _collect(bus, lambda b: b.close(), 5)
    assert len(out) == 1 and not bus.active


# --- over a real socket --------------------------------------------------------------


@contextmanager
def serving(tmp_path, **app_kw):
    """The studio on a uvicorn thread at an ephemeral loopback port."""
    app = create_app(data_dir=tmp_path, **app_kw)
    app.state.svc.events.heartbeat = 0.2
    server = make_server(app, host="127.0.0.1", port=0, log_level="warning")
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _until(lambda: server.started)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield app, server, thread, port
    finally:
        server.handle_exit(signal.SIGINT, None)
        thread.join(TIMEOUT)
        assert not thread.is_alive(), "the server did not stop"


class Stream:
    """`GET /events` on a socket with a timeout; `message()` is one SSE
    block (up to its blank line), read as it arrives."""

    def __init__(self, port: int, headers: dict | None = None, path="/events"):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
        self.conn.request("GET", path, headers=headers or {})
        self.resp = self.conn.getresponse()
        self.status = self.resp.status

    def message(self) -> str:
        lines = []
        while True:
            line = self.resp.readline().decode()
            if line in ("\n", ""):  # the blank line, or the stream's end
                return "".join(lines)
            lines.append(line)

    def event(self) -> tuple[str, dict]:
        """The next named event, heartbeats skipped."""
        while True:
            msg = self.message()
            assert msg, "the stream ended"
            if msg.startswith("event: "):
                kind, _, data = msg.partition("\n")
                return kind[len("event: ") :], json.loads(data[len("data: ") :])

    def close(self) -> None:
        self.conn.close()


def test_the_stream_is_served_unbuffered_through_the_middlewares(tmp_path):
    """The boundary and the timing middleware are BaseHTTPMiddleware and
    gzip sits under them: none of them may hold the stream back. Each
    message is read off the socket while the response is still open."""
    with serving(tmp_path) as (app, _server, _thread, port):
        bus = app.state.svc.events
        t0 = time.perf_counter()
        s = Stream(port)
        assert s.status == 200
        h = s.resp.headers
        assert h["content-type"].startswith("text/event-stream")
        assert h["cache-control"] == "no-cache, no-transform"
        assert h.get("content-encoding") is None  # gzip leaves it alone
        assert "content-length" not in h
        # the boundary still dresses it
        assert h["x-frame-options"] == "SAMEORIGIN" and "content-security-policy" in h
        assert s.message() == f"retry: {events.RETRY_MS}\n"
        assert s.message() == f'event: hello\ndata: {{"rev":"{bus.rev}"}}\n'
        assert time.perf_counter() - t0 < 2.0
        assert s.message() == ": hb\n"  # 0.2 s of silence
        t1 = time.perf_counter()
        bus.publish("progress", {"id": "x", "done": 1, "total": 3})
        assert s.event() == ("progress", {"id": "x", "done": 1, "total": 3})
        assert time.perf_counter() - t1 < 1.0  # as it is published, not at the end
        assert bus.stats()["streams"] == 1  # and the stream is still open
        s.close()


def test_a_client_that_goes_leaves_no_subscriber_and_no_thread(tmp_path):
    with serving(tmp_path) as (app, _server, _thread, port):
        bus = app.state.svc.events
        first = Stream(port)
        first.event()
        first.close()
        _until(lambda: not bus.active)
        threads = threading.active_count()  # the pool is warm now
        streams = [Stream(port) for _ in range(5)]
        for s in streams:
            assert s.event()[0] == "hello"
        assert bus.stats()["streams"] == 5
        # an open stream holds no thread: the generator lives on the loop
        assert threading.active_count() <= threads
        for s in streams:
            s.close()
        assert _until(lambda: not bus.active) < 2.0
        assert threading.active_count() <= threads


def test_connections_past_the_cap_get_a_503(tmp_path, monkeypatch):
    with serving(tmp_path) as (app, _server, _thread, port):
        bus = app.state.svc.events
        bus.max_streams = 2
        a, b = Stream(port), Stream(port)
        assert a.event()[0] == b.event()[0] == "hello"
        c = Stream(port)
        assert c.status == 503 and c.resp.headers["retry-after"] == "30"
        assert c.resp.read() == b""
        a.close()
        _until(lambda: bus.stats()["streams"] == 1)
        d = Stream(port)
        assert d.status == 200 and d.event()[0] == "hello"  # room again
        for s in (b, c, d):
            s.close()


def _race(bus: EventBus, n: int) -> list:
    """`n` requests whose handlers all run before any body starts — the
    order a threadpool gives two EventSources connecting together."""
    from types import SimpleNamespace

    from synthlab.studio.routes.events import events as route

    return [route(SimpleNamespace(events=bus)) for _ in range(n)]


def _first_chunk(resp) -> str | None:
    async def run():
        gen = resp.body_iterator
        try:
            return await gen.__anext__()
        except StopAsyncIteration:
            return None
        finally:
            await gen.aclose()

    return asyncio.run(asyncio.wait_for(run(), TIMEOUT))


def test_two_racers_for_the_last_slot_get_one_stream_and_one_503():
    """`bus.full` was read in the handler and the slot taken when the body
    started, so two requests could both pass the check: the loser got a
    200 with an empty body, which EventSource takes as "reconnect" — for
    ever, where the 503's Retry-After backs it off."""
    bus = EventBus(max_streams=1)
    won, lost = _race(bus, 2)
    assert won.status_code == 200 and lost.status_code == 503
    assert lost.headers["retry-after"] == "30"
    assert bus.stats()["streams"] == 1  # the slot is held before the body starts
    assert "event: hello" in _first_chunk(won)
    assert not bus.active  # and given back when the stream ends
    assert [r.status_code for r in _race(bus, 2)] == [200, 503]


def test_a_reserved_slot_whose_stream_never_starts_is_given_back(monkeypatch):
    """The client went between the handler and the body: no generator ran,
    so no `finally` did either. A slot not taken up in `RESERVE_S` is
    free again; a stream arriving after that ends at once (and the page
    reconnects) instead of running past the cap."""
    now = [100.0]
    monkeypatch.setattr(events.time, "monotonic", lambda: now[0])
    bus = EventBus(max_streams=1)
    (stale,) = _race(bus, 1)
    assert stale.status_code == 200 and _race(bus, 1)[0].status_code == 503
    now[0] += events.RESERVE_S + 0.1
    (fresh,) = _race(bus, 1)
    assert fresh.status_code == 200 and bus.stats()["streams"] == 1
    assert _first_chunk(stale) is None  # its slot went to `fresh`
    assert bus.stats()["streams"] == 1
    # a stream that did start is never reclaimed, however long it runs
    sub = bus._subs[0]
    sub.reserved_at = None  # bound, as `stream` leaves it
    now[0] += 10 * events.RESERVE_S
    assert _race(bus, 1)[0].status_code == 503
    assert "event: hello" in _first_chunk(fresh)
    assert not bus.active


def test_stopping_the_server_ends_open_streams_at_once(tmp_path):
    """uvicorn waits for open connections before it runs the lifespan's
    shutdown; an open stream would hold Ctrl+C until a second one. The
    server's signal handler closes the bus first."""
    with serving(tmp_path) as (app, server, thread, port):
        streams = [Stream(port) for _ in range(3)]
        for s in streams:
            s.event()
        t0 = time.perf_counter()
        server.handle_exit(signal.SIGINT, None)  # what Ctrl+C calls
        thread.join(TIMEOUT)
        took = time.perf_counter() - t0
        assert not thread.is_alive() and took < 3.0, took
        assert not server.force_exit  # the graceful path, not a second Ctrl+C
        assert app.state.svc.events.closed and not app.state.svc.events.active
        for s in streams:
            assert s.message() == ""  # each read its end, none was cut mid-frame
            s.close()


def test_the_lifespan_closes_the_bus(tmp_path):
    """Any server (and the TestClient) that runs the lifespan ends the
    streams, and a closed bus refuses new ones."""
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert not app.state.svc.events.closed
    assert app.state.svc.events.closed
    assert TestClient(app).get("/events").status_code == 503


def test_a_stream_that_ends_is_whole_through_the_test_client(tmp_path):
    """The same route through the TestClient, which waits for the body: the
    bus closes under it (a timer, so the request cannot hang)."""
    app = create_app(data_dir=tmp_path)
    bus = app.state.svc.events
    timer = threading.Timer(0.3, bus.close)
    timer.start()
    try:
        r = TestClient(app).get("/events")
    finally:
        timer.cancel()
    assert r.status_code == 200
    assert r.text == f'retry: 3000\n\nevent: hello\ndata: {{"rev":"{bus.rev}"}}\n\n'
    assert not bus.active


# --- the boundary --------------------------------------------------------------------


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_another_site_cannot_read_the_stream(tmp_path, site):
    """`_foreign_read`: an EventSource or a fetch from another origin."""
    app = create_app(data_dir=tmp_path)
    headers = {"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "cors"}
    r = TestClient(app).get("/events", headers=headers)
    assert r.status_code == 403
    assert not app.state.svc.events.active


def test_a_foreign_host_cannot_read_the_stream(tmp_path):
    r = TestClient(create_app(data_dir=tmp_path)).get(
        "/events", headers={"Host": "evil.example"}
    )
    assert r.status_code == 400


def test_the_pages_own_event_source_is_a_same_origin_read(tmp_path):
    browser = {
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Accept": "text/event-stream",
    }
    with serving(tmp_path) as (_app, _server, _thread, port):
        s = Stream(port, browser)
        assert s.status == 200 and s.event()[0] == "hello"
        s.close()


def test_share_mode_wants_its_cookie(tmp_path):
    """EventSource sends the page's cookies on a same-origin request; a
    stream without the share token is the 401 every other route gives."""
    with serving(tmp_path, share_token="tok") as (app, _server, _thread, port):
        bare = Stream(port)
        assert bare.status == 401
        wrong = Stream(port, {"Cookie": f"{TOKEN_COOKIE}_{port}=nope"})
        assert wrong.status == 401
        assert not app.state.svc.events.active
        s = Stream(port, {"Cookie": f"{TOKEN_COOKIE}_{port}=tok"})
        assert s.status == 200 and s.event()[0] == "hello"
        for x in (bare, wrong, s):
            x.close()


# --- perf --------------------------------------------------------------------------


def test_the_stream_is_one_perf_row_and_its_time_is_the_handshake(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SYNTHLAB_DEBUG", "1")
    with serving(tmp_path) as (app, _server, _thread, port):
        s = Stream(port)
        s.event()
        time.sleep(0.3)  # the stream stays open; that is not a request's cost
        rows = app.state.svc.perf.snapshot()["requests"]
        assert rows["GET /events"]["count"] == 1
        assert rows["GET /events"]["max_ms"] < 250
        stats = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
        stats.request("GET", "/debug/stats")
        body = json.loads(stats.getresponse().read())
        assert body["events"] == {
            "streams": 1,
            "published": 0,
            "dropped": 0,
            "changes": 0,
        }
        stats.close()
        s.close()


# --- progress ----------------------------------------------------------------------


@pytest.fixture
def every_step(monkeypatch):
    monkeypatch.setattr(events, "PROGRESS_GAP_S", 0.0)


def _song_client(tmp_path) -> TestClient:
    client = TestClient(create_app(data_dir=tmp_path))
    _use(client, _three_section_song())
    return client


def _progress(sub: Subscription) -> list[tuple]:
    lost, got = sub.drain()
    assert not lost
    return [(e.kind, e.data["label"], e.data["done"], e.data["total"]) for e in got]


def test_an_export_reports_its_steps_in_order_and_the_same_bytes(tmp_path, every_step):
    quiet = _song_client(tmp_path / "quiet").get("/export/song.wav")
    assert quiet.status_code == 200

    client = _song_client(tmp_path / "watched")
    svc = client.app.state.svc
    # as many events as there are steps: a queued one would be replaced
    sub = svc.events.subscribe()
    seen: list[tuple] = []
    put = sub.put
    sub.put = lambda e: (seen.append((e.kind, e.data["done"], e.data["total"])), put(e))
    watched = client.get("/export/song.wav")
    assert watched.status_code == 200
    # three sections: two passes each for the gain, then each one stitched
    assert seen == [("progress", n, 9) for n in range(10)] + [("done", 9, 9)]
    ids = {e.data["id"] for e in sub.drain()[1]}
    assert len(ids) == 1
    # an audience changes nothing that is rendered or how it is filed
    assert watched.content == quiet.content
    assert (
        watched.headers["content-disposition"] == quiet.headers["content-disposition"]
    )

    # the same export again: nothing renders, the steps are the same
    seen.clear()
    assert client.get("/export/song.wav").content == quiet.content
    assert seen[0] == ("progress", 0, 9) and seen[-1] == ("done", 9, 9)
    assert [d for _k, d, _t in seen] == sorted(d for _k, d, _t in seen)


def test_progress_changes_no_cache_key(tmp_path, every_step):
    def keys(client) -> tuple:
        plan = client.get("/song/plan.json").json()
        # the render keys (a section's id is random per song built)
        return tuple(
            (s["url"].split("?")[1], s["loop_url"].split("?")[1])
            for s in plan["sections"]
        )

    quiet = _song_client(tmp_path / "quiet")
    watched = _song_client(tmp_path / "watched")
    sub = watched.app.state.svc.events.subscribe()
    assert keys(watched) == keys(quiet)
    # the plan is where the sections render: a step per pass
    assert _progress(sub) == [("progress", "Rendering song", 6, 6)] + [
        ("done", "Rendering song", 6, 6)
    ]


def test_a_mastered_export_says_it_is_mastering(tmp_path, every_step):
    client = _song_client(tmp_path)
    sub = client.app.state.svc.events.subscribe()
    labels: list[str] = []
    put = sub.put
    sub.put = lambda e: (labels.append(f"{e.kind}:{e.data['label']}"), put(e))
    r = client.get("/export/song.wav?loudness=streaming")
    assert r.status_code == 200
    assert labels[0] == "progress:Exporting song"
    assert labels[-3:] == ["progress:Mastering", "progress:Mastering", "done:Mastering"]
    assert sub.drain()[1][-1].data["done"] == 10  # 9 + the mastering


def test_stems_report_a_step_per_stem(tmp_path, every_step):
    quiet = _song_client(tmp_path / "quiet").get("/export/stems.zip")
    client = _song_client(tmp_path / "watched")
    sub = client.app.state.svc.events.subscribe()
    seen: list[tuple] = []
    put = sub.put
    sub.put = lambda e: (seen.append((e.kind, e.data["done"], e.data["total"])), put(e))
    r = client.get("/export/stems.zip")
    assert r.status_code == 200 and r.content == quiet.content
    # the mix's gain, then drums, synth-1, synth-2
    assert seen[1:] == [("progress", n, 4) for n in range(1, 5)] + [("done", 4, 4)]
    assert _kinds(sub) == ["progress", "done"]  # queued ones coalesced


def test_a_cold_section_reports_and_a_cached_one_is_silent(tmp_path, every_step):
    client = _song_client(tmp_path)
    svc = client.app.state.svc
    sub = svc.events.subscribe()
    sid = svc.state.song["sections"][1]["id"]
    assert client.get(f"/song/section/{sid}.wav").status_code == 200
    # the Verse's first pass: its own cycle, and the Intro's for its tail
    assert _progress(sub) == [
        ("progress", "Rendering Verse", 2, 2),
        ("done", "Rendering Verse", 2, 2),
    ]
    assert client.get(f"/song/section/{sid}.wav").status_code == 200
    assert _progress(sub) == []  # served from the cache: nothing to say


def test_a_failed_render_ends_its_task(tmp_path, every_step, monkeypatch):
    client = _song_client(tmp_path)
    svc = client.app.state.svc
    sub = svc.events.subscribe()

    def boom(*a, **kw):
        raise events_error

    events_error = RuntimeError("no")
    monkeypatch.setattr(svc, "song_mixdown", boom)
    with pytest.raises(RuntimeError):
        client.get("/export/song.wav")
    assert _kinds(sub) == ["progress", "failed"]


# --- change notices ------------------------------------------------------------------


def _changes(sub: Subscription) -> list[dict]:
    return [e.data for e in sub.drain()[1] if e.kind == "changed"]


def test_a_change_names_the_regions_its_own_refresh_sent_and_who_asked(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    svc = client.app.state.svc
    sub = svc.events.subscribe()
    headers = _hx("editor") | {events.ORIGIN_HEADER: "tab-1"}
    r = client.post(
        "/fragment/fx/add", data={"scope": "layer", "kind": "reverb"}, headers=headers
    )
    assert r.status_code == 200
    (note,) = _changes(sub)
    sent = _oob_ids(r.text) & set(REGIONS)
    assert sent and set(note["regions"]) == sent  # the response's own OOB set
    assert note["origin"] == "tab-1" and note["view"] == "editor"
    assert note["whole"] is False and note["rev"] == svc.events.rev


def test_a_suppressed_region_is_still_announced(tmp_path):
    """The form that posted is not sent back to its own page (it already
    shows the edit); every other page needs it."""
    client = TestClient(create_app(data_dir=tmp_path))
    sub = client.app.state.svc.events.subscribe()
    r = client.post(
        "/fragment/arrangement", data={"phrase": "riff"}, headers=_hx("editor")
    )
    assert r.status_code == 200
    (note,) = _changes(sub)
    assert "sound-bar" in note["regions"] and "sound-bar" not in _oob_ids(r.text)
    # (the route also draws `analysis` itself: not a region)
    assert _oob_ids(r.text) & set(REGIONS) <= set(note["regions"])
    assert note["origin"] == ""  # a request that named no page


def test_a_whole_page_answer_says_whole(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    sub = client.app.state.svc.events.subscribe()
    client.post(
        "/fragment/fx/add",
        data={"scope": "layer", "kind": "reverb"},
        headers=_hx("editor"),
    )
    sub.drain()
    r = client.post(
        "/fragment/reset",
        data={"scope": "all", "mode": "defaults"},
        headers=_hx("editor"),
    )
    assert r.status_code == 200
    (note,) = _changes(sub)
    assert note["whole"] is True and note["view"] == "editor"


def test_reads_and_refused_requests_announce_nothing(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    sub = client.app.state.svc.events.subscribe()
    assert client.get("/view/drums", headers=_hx("drums")).status_code == 200
    assert client.get("/patch.json").status_code == 200
    r = client.post("/fragment/reset", data={"scope": "nope"}, headers=_hx("editor"))
    assert r.status_code == 422
    assert _changes(sub) == []
    assert client.app.state.svc.events.changes == 0


def test_an_edit_no_region_shows_is_still_a_change(tmp_path):
    """The regions are summaries; the panels' own sliders are not regions.
    A save with no watch moved is announced with no regions named."""
    client = TestClient(create_app(data_dir=tmp_path))
    svc = client.app.state.svc
    r = client.post(
        "/fragment/xy", data={"x": "0.31", "y": "0.62"}, headers=_hx("editor")
    )
    assert r.status_code == 200
    sub = svc.events.subscribe()
    before = svc.state.edits
    r = client.post(
        "/fragment/xy", data={"x": "0.32", "y": "0.63"}, headers=_hx("editor")
    )
    assert r.status_code == 200 and svc.state.edits > before
    (note,) = _changes(sub)
    assert note["whole"] is False


def test_a_long_origin_id_is_cut(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    sub = client.app.state.svc.events.subscribe()
    headers = _hx("editor") | {events.ORIGIN_HEADER: "x" * 500}
    client.post(
        "/fragment/fx/add", data={"scope": "layer", "kind": "reverb"}, headers=headers
    )
    assert _changes(sub)[0]["origin"] == "x" * 64
