# Events (server push)

`GET /events`: the Server-Sent Events stream for render progress and
change notices to other pages. Index: `README.md`.

**Events** (server push: `events.py`, `routes/events.py`,
`static/events.js`; `tests/studio/test_events.py`,
`test_events_island.py`): `GET /events` is a Server-Sent Events stream
from an in-process bus (`svc.events`). Small JSON only.

## Bus contract

**Bus contract**: `publish` runs on any thread and never waits: it
appends to each connection's bounded queue (`QUEUE_SIZE` 64) and pokes
its loop. A full queue drops its oldest and the stream says `resync`
(the page re-fetches); a queued `progress` of the same task is
replaced, a queued `changed` from the same page + view merged. No
subscriber: `publish` returns at once. Cap `limits.MAX_EVENT_STREAMS`
(12), 503 past it — **exactly**: the route takes its slot with
`EventBus.reserve()` (under the bus lock) and hands it to `stream(sub)`,
which gives it back when it ends. Reading `bus.full` in the route and
subscribing when the body started let two connections both pass, and the
loser's empty 200 kept EventSource reconnecting where the 503's
`Retry-After` backs it off. A reserved slot whose stream never starts
(the client left first, so no generator and no `finally` ran) is free
again after `RESERVE_S` (10 s), reclaimed by the next `reserve`.
Heartbeat comment every 15 s, `retry: 3000`.

## The route

**The route** is a plain `def`, `@lock_free`, returning a
`StreamingResponse` over an async generator: no thread and no studio
lock per open page. Both `BaseHTTPMiddleware`s (boundary, timing) and
gzip pass it through unbuffered (measured on a real socket), so it is
not exempted from anything: Host, share cookie and `_foreign_read`
apply as to any read. Perf counts it as one row, `GET /events`, whose
time is the handshake; `/debug/stats` has `events`.

## Shutdown

**Shutdown**: uvicorn waits for open connections *before* it runs the
lifespan's shutdown, so the lifespan alone cannot end a stream.
`server.make_server` subclasses `uvicorn.Server`: `handle_exit` calls
`EventBus.close()` (lock-free: it runs in the signal handler) first.
The lifespan closes it too (TestClient, other servers). Launch through
`make_server`, never bare `uvicorn.run`, or one open tab holds Ctrl+C.

## Progress

**Progress** (`events.Task`, `QUIET` by default): `progress` {id,
label, done, total} then `done` / `failed`, at most 4 a second. The
song export (a step per pass for the gain, per section stitched, one
for mastering), the loudness measure, the stems (a step per stem),
`/song/plan.json` (where an edited song's sections render) and a cold
`section_result`, and the Voice lab's vocoder (`routes/voice.py`:
"Vocoding", a step for the carrier, the vocoder and the FX). The song's
steps sit in `service/song.py` only; nothing in
`render.py` or the engine knows. A task reads no audio and no key.

## Change notices

**Change notices**: `routing._settle` computes
`StudioService.moved(page, before)` once per non-GET request, hands it
to `refresh` (the OOB set) and to `announce`, which publishes `changed`
{origin, view, regions, whole, rev}. `regions` is the moved set
*before* suppression and "already drawn" are taken out (another page
has neither), so it is a superset of the response's OOB regions.
`whole`: a whole-page answer or `HX-Refresh`. Also announced, with no
regions: a request that saved (`StudioState.edits`) but moved no watch
(the Synths sliders are not regions). `origin` is the asking page's
`X-Studio-Page` (`events.ORIGIN_HEADER`).

## The client

**The client** (`events.js`, <= 110): sets `X-Studio-Page` on every
htmx request (`htmx:config:request`) and the islands' `fetch()` posts
(`window.synthlab.pageHeaders`, which also carries the page's
`HX-Current-URL` as htmx sends it — `view_of` reads the asking page off
it, so a fetch post gets that page's regions back: without it a ⏺ Rec
on Perform would read as Synths' and Perform's Loops bin would stay
stale); ignores its own echo; on anyone
else's `changed` re-fetches its view with `GET /view/<name>` morphed
into `#view` (there is no per-region GET; the regions only decide how
soon: 400 ms when it is this view, whole, or a region drawn here,
2.5 s otherwise), never while a pointer is down, a request is in
flight, a text field has focus or something records. Progress is one
`.toast.progress` line in `#toasts`, shown after 400 ms. The stream is
closed while the tab is hidden (six connections per origin) and
`hello`'s `rev` tells a returning page whether it missed anything.
No EventSource, a 503 or a dead server: silent, the page is as before.

## Out of scope

**Out of scope**: audio or HTML over the stream, WebSockets, conflict
resolution (last write wins), replay (`Last-Event-ID`), GETs that
change state (`/view/breed` on an empty board) and `/theme`.
