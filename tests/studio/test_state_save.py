"""save_soon(): rapid live tweaks collapse into one trailing write."""

import json
import os
import threading
import time
from dataclasses import replace

from synthlab.studio.state import StudioState


def _mtime_or_none(state):
    p = state._state_path
    return p.stat().st_mtime_ns if p.exists() else None


def test_save_soon_writes_once_after_delay(tmp_path):
    s = StudioState(tmp_path)
    for _ in range(5):
        s.save_soon(delay=0.1)
    assert _mtime_or_none(s) is None  # nothing written yet
    deadline = time.monotonic() + 3.0
    while _mtime_or_none(s) is None and time.monotonic() < deadline:
        time.sleep(0.02)
    data = json.loads(s._state_path.read_text(encoding="utf-8"))
    assert "patch" in data


def test_direct_save_cancels_pending_timer(tmp_path):
    s = StudioState(tmp_path)
    s.save_soon(delay=0.1)
    s.save()
    first = _mtime_or_none(s)
    assert first is not None
    time.sleep(0.3)  # timer would have fired by now if not cancelled
    assert _mtime_or_none(s) == first


def test_concurrent_saves_never_land_out_of_order(tmp_path, monkeypatch):
    """IMPORTANT #1: save() used to serialize the payload under state.lock
    but write the tempfile + os.replace outside it. A debounce-timer save
    (old data) that stalls between its snapshot and its write could let a
    request-path save (newer data) finish first, and the timer's stale
    os.replace would then land last and clobber it. Moving the write
    inside `with self.lock:` makes the two saves mutually exclusive end to
    end, so the one with the newer data is always the one still runnable
    (and therefore the one that lands) once the stalled save resumes."""
    s = StudioState(tmp_path)
    s.arrangement = replace(s.arrangement, bpm=100.0)
    s.save()  # baseline write, so state.json exists with bpm=100

    real_replace = os.replace
    reached_replace = threading.Event()
    resume_replace = threading.Event()
    calls: list[str] = []

    def slow_replace(src, dst):
        calls.append(src)
        if len(calls) == 1:  # only the first (timer-path) call stalls
            reached_replace.set()
            resume_replace.wait(timeout=5)
        real_replace(src, dst)

    monkeypatch.setattr("synthlab.studio.state.os.replace", slow_replace)

    timer_done = threading.Event()

    def timer_path():
        s.save()  # snapshots bpm=100, then stalls in os.replace
        timer_done.set()

    t = threading.Thread(target=timer_path)
    t.start()
    assert reached_replace.wait(timeout=5), "timer save never reached os.replace"

    # newer state arrives from a request handler while the timer save is stalled
    s.arrangement = replace(s.arrangement, bpm=150.0)
    request_done = threading.Event()

    def request_path():
        s.save()
        request_done.set()

    r = threading.Thread(target=request_path)
    r.start()
    # With the bug, self.lock is already free by the time the timer stalls,
    # so the request-path save runs and completes with no contention at
    # all — comfortably inside this generous window. With the fix, the
    # timer save still holds self.lock while stalled, so the request save
    # blocks here and this must time out.
    finished_early = request_done.wait(timeout=0.5)

    resume_replace.set()
    assert timer_done.wait(timeout=5)
    assert request_done.wait(timeout=5)
    t.join(timeout=5)
    r.join(timeout=5)

    saved = json.loads(s._state_path.read_text(encoding="utf-8"))
    assert saved["arrangement"]["bpm"] == 150.0, (
        "the timer's stale save landed after the request's newer save "
        f"(request finished before the timer resumed: {finished_early})"
    )


# --- a failed write stays pending; the temp file is synced --------------------


def _flaky_replace(monkeypatch, failures: int) -> list:
    """os.replace raising PermissionError `failures` times (Windows, while
    an indexer or a virus scanner holds state.json), then working."""
    real, calls = os.replace, []

    def replace_(src, dst):
        calls.append(src)
        if len(calls) <= failures:
            raise PermissionError(13, "state.json is in use")
        real(src, dst)

    monkeypatch.setattr("synthlab.studio.state.os.replace", replace_)
    return calls


def test_a_failed_debounced_save_is_tried_again(tmp_path, monkeypatch):
    """The debounce timer's save raised on its own thread, with the timer
    already cleared: nothing was pending, nothing retried, and the last
    edits were never written."""
    s = StudioState(tmp_path)
    monkeypatch.setattr(StudioState, "SAVE_RETRY_S", 0.05)
    calls = _flaky_replace(monkeypatch, 2)
    s.arrangement = replace(s.arrangement, bpm=111.0)
    s.save_soon(delay=0.05)
    deadline = time.monotonic() + 5.0
    while _mtime_or_none(s) is None and time.monotonic() < deadline:
        time.sleep(0.02)
    saved = json.loads(s._state_path.read_text(encoding="utf-8"))
    assert saved["arrangement"]["bpm"] == 111.0 and len(calls) == 3
    assert not s._unsaved
    assert not list(tmp_path.glob("*.tmp"))  # no temp file left behind


def test_flush_writes_what_a_failed_save_left_pending(tmp_path, monkeypatch):
    s = StudioState(tmp_path)
    monkeypatch.setattr(StudioState, "SAVE_RETRIES", 0)  # the timer gives up
    calls = _flaky_replace(monkeypatch, 1)
    s.arrangement = replace(s.arrangement, bpm=111.0)
    s.save_soon(delay=0.01)
    deadline = time.monotonic() + 5.0
    while not calls and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.05)
    assert _mtime_or_none(s) is None and s._unsaved
    monkeypatch.setattr(StudioState, "SAVE_RETRIES", 4)
    s.flush()  # the shutdown hook
    saved = json.loads(s._state_path.read_text(encoding="utf-8"))
    assert saved["arrangement"]["bpm"] == 111.0
    s.flush()  # nothing pending now: no write
    assert len(calls) == 2


def test_flush_retries_a_write_that_fails_once(tmp_path, monkeypatch):
    s = StudioState(tmp_path)
    s.save_soon(delay=60.0)
    calls = _flaky_replace(monkeypatch, 1)
    s.flush()
    assert len(calls) == 2 and _mtime_or_none(s) is not None


def test_the_temp_file_is_synced_before_it_replaces_state_json(tmp_path, monkeypatch):
    """Without an fsync a power loss after the rename can leave an empty
    state.json, which the next start sets aside as corrupt."""
    s = StudioState(tmp_path)
    order: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(
        "synthlab.studio.state.os.fsync",
        lambda fd: order.append("fsync") or real_fsync(fd),
    )
    monkeypatch.setattr(
        "synthlab.studio.state.os.replace",
        lambda a, b: order.append("replace") or real_replace(a, b),
    )
    s.save()
    assert order == ["fsync", "replace"]


# --- a failed import's rollback leaves the save's own fields alone -------------


class _FakeTimer:
    """threading.Timer that only fires when the test says so."""

    made: list = []

    def __init__(self, delay, fn, args=()):
        self.delay, self.fn, self.args = delay, fn, args
        self.cancelled = self.daemon = False
        _FakeTimer.made.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


def test_a_failed_imports_rollback_keeps_the_save_fields_as_they_are_now(
    tmp_path, monkeypatch
):
    """`_apply_or_restore` restored `__dict__` wholesale, the debounce
    timer's fields included. A timer that fired during a failing import
    came back as "pending": `flush` wrote for nothing, and a later timed
    save that failed saw "a newer timer" and skipped its own retry."""
    monkeypatch.setattr("synthlab.studio.state.threading.Timer", _FakeTimer)
    monkeypatch.setattr(_FakeTimer, "made", [])
    s = StudioState(tmp_path)
    s.arrangement = replace(s.arrangement, bpm=111.0)
    s.save_soon(60)
    (timer,) = _FakeTimer.made
    lock = s._save_lock

    def apply_while_the_timer_fires(data):
        s.patch = replace(s.patch, name="half applied")
        timer.fn(*timer.args)  # the debounced save lands mid-import
        raise ValueError("rejected")

    monkeypatch.setattr(s, "_apply", apply_while_the_timer_fires)
    name = s.patch.name
    try:
        s.load_project({"format": "synthlab-project"})
    except ValueError:
        pass
    else:
        raise AssertionError("the import was meant to fail")
    assert s.patch.name == name  # the session itself is rolled back
    # the save's fields are as the timer left them, not as they were
    assert s._save_timer is None and not s._unsaved and s._save_lock is lock
    written = s._state_path.stat().st_mtime_ns
    s.flush()  # nothing pending: no write
    assert s._state_path.stat().st_mtime_ns == written
    # a later timed save that fails still retries (no stale "newer" timer)
    monkeypatch.setattr(StudioState, "SAVE_RETRY_S", 0.05)
    _flaky_replace(monkeypatch, 1)
    s._timed_save(0)
    assert len(_FakeTimer.made) == 2 and s._save_timer is _FakeTimer.made[1]


def test_the_rollback_spares_exactly_the_fields_persist_owns_alone():
    from synthlab.studio import state
    from synthlab.studio.state.persist import Persistence

    # `coach` (the loop coach's runtime thread, written by loopcoach.py
    # outside the package) is persist's alone too, but as a field a load
    # drops: a failed load must put it back like the rest of the session
    load_drops = {"coach"}
    assert (
        set(Persistence._SAVE_FIELDS)
        == {f for f, owners in state.OWNERS.items() if owners == ("persist",)}
        - load_drops
    )
