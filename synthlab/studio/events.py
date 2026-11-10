"""Server push: an in-process event bus, served to each open page as
Server-Sent Events by `GET /events` (`routes/events.py`).

Two things ride it. **Progress** of the long renders (`Task`: a song
export, the stems, the plan's cold sections) — `progress` {id, label,
done, total}, then one `done` or `failed`. **Change notices**: after a
state-changing request commits, `changed` {origin, view, regions, whole,
rev}, so a page open somewhere else (a second tab, a phone on --share)
re-fetches instead of showing stale state until its next request.

The contract:

- **Publishing never waits and never grows.** `publish` runs on whatever
  thread has something to say (a threadpool handler, a render) and only
  appends to each connection's bounded queue and pokes its event loop. A
  full queue drops its oldest event and marks the connection `lost`; the
  stream then says `resync` and the page re-fetches everything, so a slow
  client costs a render nothing and misses nothing for good. Events with a
  `key` coalesce: a queued `progress` of the same task is replaced, a
  queued `changed` from the same page and view is merged.
- **Nobody listening costs nothing**: `publish` returns on an empty tuple,
  and a `Task` step is a counter and an attribute read.
- **Connections are capped** (`limits.MAX_EVENT_STREAMS`); the route
  answers 503 past it. The route reserves its slot (`reserve`) before it
  answers, so the cap is exact; the stream gives the slot back.
- **A stream ends** when its client goes (the response is cancelled and
  the generator's `finally` unsubscribes) and when the bus closes. Closing
  matters: uvicorn waits for open connections *before* it runs the
  lifespan's shutdown, so an open stream would hold Ctrl+C until a second
  one. `close()` is therefore lock-free (it runs inside a signal handler,
  `server.StudioServer.handle_exit`) and is also the lifespan's last act.

Nothing here is audio, HTML or state: the events are small JSON, the pages
fetch what they need over the ordinary routes, and the last write still
wins.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from . import limits

# what a page calls itself on its requests (static/events.js): the tab that
# made an edit ignores the notice of it
ORIGIN_HEADER = "X-Studio-Page"
HEARTBEAT_S = 15.0  # a comment line, so proxies and the browser keep the stream
RETRY_MS = 3000  # EventSource's reconnect delay, sent once as `retry:`
QUEUE_SIZE = 64  # events one connection may fall behind by
PROGRESS_GAP_S = 0.25  # at most four progress events a second per task
# how long a slot the route reserved may wait for its stream to start
# before the next connection may have it (`EventBus.reserve`). The body
# starts in the same request, milliseconds later; this only ever frees a
# slot whose client left in between.
RESERVE_S = 10.0


@dataclass(frozen=True)
class Event:
    kind: str
    data: dict
    # events with the same key coalesce while queued (None: never)
    key: tuple | None = None
    # (queued data, new data) -> the data that replaces both; default: the new
    merge: Callable[[dict, dict], dict] | None = None

    def frame(self) -> str:
        return frame(self.kind, self.data)


def frame(kind: str, data: dict) -> str:
    """One SSE message. The JSON is one line (no raw newline can end the
    `data:` field early)."""
    return f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _merge_changed(old: dict, new: dict) -> dict:
    regions = list(dict.fromkeys([*old["regions"], *new["regions"]]))
    return new | {"regions": regions, "whole": old["whole"] or new["whole"]}


class Subscription:
    """One connection's bounded queue. `put` is called from any thread,
    `drain` from the connection's event loop."""

    def __init__(self, size: int = QUEUE_SIZE) -> None:
        self._size = size
        self._queue: deque[Event] = deque()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self.lost = False  # an event was dropped: the page must resync
        self.dropped = 0
        # when the route reserved this slot for a stream that has not
        # started yet (`EventBus.reserve`); None once it has, and for a
        # queue taken with `subscribe` (which is never reclaimed)
        self.reserved_at: float | None = None

    def bind(self) -> None:
        """Called on the connection's event loop, before the first wait."""
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self.reserved_at = None

    def put(self, event: Event) -> None:
        with self._lock:
            if event.key is not None:
                for i, old in enumerate(self._queue):
                    if old.key == event.key:
                        if event.merge is not None:
                            data = event.merge(old.data, event.data)
                            event = Event(event.kind, data, event.key, event.merge)
                        self._queue[i] = event
                        break
                else:
                    self._queue.append(event)
            else:
                self._queue.append(event)
            while len(self._queue) > self._size:
                self._queue.popleft()
                self.dropped += 1
                self.lost = True
        self.wake()

    def wake(self) -> None:
        """Poke the connection's loop. Safe from any thread and from a
        signal handler; a loop that has already closed is not an error."""
        loop, wake = self._loop, self._wake
        if loop is None or wake is None:
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            pass

    def drain(self) -> tuple[bool, list[Event]]:
        """(whether events were lost since the last drain, what is queued)."""
        with self._lock:
            lost, self.lost = self.lost, False
            events = list(self._queue)
            self._queue.clear()
        return lost, events

    async def wait(self, timeout: float) -> bool:
        """True when woken, False when `timeout` passed in silence."""
        assert self._wake is not None, "bind() first"
        try:
            await asyncio.wait_for(self._wake.wait(), timeout)
        except TimeoutError:
            return False
        self._wake.clear()
        return True


class EventBus:
    def __init__(
        self,
        *,
        max_streams: int | None = None,
        queue_size: int = QUEUE_SIZE,
        heartbeat: float = HEARTBEAT_S,
    ) -> None:
        self.max_streams = (
            limits.MAX_EVENT_STREAMS if max_streams is None else max_streams
        )
        self.queue_size = queue_size
        self.heartbeat = heartbeat
        # replaced, never mutated: `publish` and `close` read it without a
        # lock (close runs inside a signal handler)
        self._subs: tuple[Subscription, ...] = ()
        self._lock = threading.Lock()
        self.closed = False
        # which run of the server this is + how many changes it has
        # announced: a page that reconnects compares it with the last it saw
        self.boot = secrets.token_hex(4)
        self.changes = 0
        self.published = 0
        self.dropped = 0

    # -- connections -------------------------------------------------------
    @property
    def active(self) -> bool:
        return bool(self._subs)

    @property
    def full(self) -> bool:
        return self.closed or len(self._subs) >= self.max_streams

    @property
    def rev(self) -> str:
        return f"{self.boot}:{self.changes}"

    def subscribe(self) -> Subscription | None:
        """A new connection's queue, or None at the cap or once closed."""
        with self._lock:
            if self.full:
                return None
            sub = Subscription(self.queue_size)
            self._subs = (*self._subs, sub)
            return sub

    def reserve(self) -> Subscription | None:
        """`subscribe` for a stream that starts later (`routes/events.py`):
        the slot is taken here, under the lock, so the cap is exact — the
        route used to read `full` and the body subscribed when it started,
        and two requests could both pass the check. `stream(sub)` takes
        the slot up and gives it back when it ends. A slot never taken up
        (the client went before the body started, so no generator ran and
        no `finally` did) is given back the next time someone asks, once
        it is `RESERVE_S` old."""
        with self._lock:
            now = time.monotonic()
            stale = [
                s
                for s in self._subs
                if s.reserved_at is not None and now - s.reserved_at > RESERVE_S
            ]
            if stale:
                self._subs = tuple(s for s in self._subs if s not in stale)
            if self.full:
                return None
            sub = Subscription(self.queue_size)
            sub.reserved_at = now
            self._subs = (*self._subs, sub)
            return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subs = tuple(s for s in self._subs if s is not sub)
            self.dropped += sub.dropped

    def close(self) -> None:
        """End every stream, now and for good. No lock: this is called from
        uvicorn's signal handler, which may have interrupted the very code
        that holds one."""
        self.closed = True
        for sub in self._subs:
            sub.wake()

    def stats(self) -> dict:
        live = sum(s.dropped for s in self._subs)
        return {
            "streams": len(self._subs),
            "published": self.published,
            "dropped": self.dropped + live,
            "changes": self.changes,
        }

    # -- publishing --------------------------------------------------------
    def publish(
        self,
        kind: str,
        data: dict,
        *,
        key: tuple | None = None,
        merge: Callable[[dict, dict], dict] | None = None,
    ) -> None:
        subs = self._subs
        if not subs or self.closed:
            return
        event = Event(kind, data, key, merge)
        self.published += 1
        for sub in subs:
            sub.put(event)

    def changed(self, origin: str, view: str, regions, whole: bool = False) -> None:
        """A state-changing request committed. `regions` is the set the
        request's own OOB refresh was computed from (`StudioService.moved`:
        the regions of `view`'s page whose watch moved), `whole` says the
        answer was a whole page; `origin` is the page that asked."""
        self.changes += 1
        self.publish(
            "changed",
            {
                "origin": origin,
                "view": view,
                "regions": list(regions),
                "whole": bool(whole),
                "rev": self.rev,
            },
            key=("changed", origin, view),
            merge=_merge_changed,
        )

    def task(self, label: str, total: int = 1) -> Task:
        """A long render's progress: `with bus.task("Exporting song", 8) as
        t: ... t.step()`."""
        return Task(self, label, total)

    # -- the stream --------------------------------------------------------
    async def stream(self, sub: Subscription | None = None) -> AsyncIterator[str]:
        """One connection's SSE body: `retry:` and `hello` {rev}, then
        every event published while it is open, a comment line each
        `heartbeat` seconds of silence, `resync` after an overflow. Ends
        when the bus closes; cancelled (the `finally` still runs) when the
        client goes. `sub` is the slot the route reserved (`reserve`);
        without one the stream takes its own, and is empty at the cap. A
        reserved slot that was reclaimed before the stream started is
        gone: the stream ends at once rather than run past the cap."""
        if sub is None:
            sub = self.subscribe()
            if sub is None:
                return
        try:
            with self._lock:
                held = sub in self._subs
                if held:
                    sub.reserved_at = None  # taken up: never reclaimed now
            if not held:
                return
            sub.bind()
            sub.wake()  # anything published between subscribe and bind
            yield f"retry: {RETRY_MS}\n\n" + frame("hello", {"rev": self.rev})
            while not self.closed:
                if not await sub.wait(self.heartbeat):
                    yield ": hb\n\n"
                    continue
                lost, events = sub.drain()
                out = [frame("resync", {"rev": self.rev})] if lost else []
                out += [e.frame() for e in events]
                if out and not self.closed:
                    yield "".join(out)
        finally:
            self.unsubscribe(sub)


class Task:
    """One long render's progress. `step()` at each natural boundary; the
    `with` block ends it with `done`, or `failed` when it raised. Steps are
    throttled (`PROGRESS_GAP_S`), and a queued one is replaced by the next,
    so the render never waits on its audience and tells it nothing it
    can't use. It reads no audio and touches no key: rendering with or
    without one gives the same bytes."""

    def __init__(self, bus: EventBus | None, label: str, total: int = 1) -> None:
        self.bus = bus
        self.id = secrets.token_hex(4)
        self.label = label
        self.total = max(1, int(total))
        self.done = 0
        self._last = 0.0

    def plan(self, total: int) -> None:
        """The number of steps, once it is known."""
        self.total = max(1, int(total))

    def step(self, n: int = 1, label: str | None = None) -> None:
        self.done = min(self.total, self.done + n)
        if label is not None:
            self.label = label
        self._emit(force=label is not None)

    def say(self, label: str) -> None:
        """What the next step is doing ("Mastering"), at once."""
        self.label = label
        self._emit(force=True)

    def _emit(self, force: bool = False) -> None:
        bus = self.bus
        if bus is None or not bus.active:
            return
        now = time.monotonic()
        if not force and now - self._last < PROGRESS_GAP_S:
            return
        self._last = now
        bus.publish("progress", self._data(), key=("progress", self.id))

    def _data(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "done": self.done,
            "total": self.total,
        }

    def __enter__(self) -> Task:
        self._emit(force=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.bus is not None and self.bus.active:
            kind = "done" if exc_type is None else "failed"
            self.bus.publish(kind, self._data())


QUIET = Task(None, "")
"""The task of a render nobody asked to follow: every call a no-op."""
