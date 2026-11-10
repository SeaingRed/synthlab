"""Route plumbing shared by every studio router: the concurrency model
(`StudioRoute`, `lock_free`), the async form/upload dependencies, and the
`Svc` dependency that hands a handler the app's `StudioService`.

The concurrency model: every route handler is
a plain `def`. Starlette runs it in the threadpool, so a render never runs
on the event loop, where it stalled every other request (/health included)
for as long as it took. `StudioRoute` runs each handler under
`StudioState.lock`, so a request's mutation and the render of it happen as
one step and two requests never interleave. What needs the event loop —
parsing the form, reading an upload — happens first, in the async
dependencies below, which FastAPI resolves before it hands the sync handler
to the threadpool. So an upload is fully read before the lock is taken.

The one exception to "the render happens under the lock" is a route marked
`@render_outside_lock` (the song exports, its plan and section WAVs, a breed
generation): renders of seconds, which used to stall every other request.
Its handler snapshots under the lock and renders after releasing it.
"""

from __future__ import annotations

import functools
import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.routing import APIRoute
from starlette.datastructures import FormData

from . import limits
from .events import ORIGIN_HEADER
from .service import StudioService


def get_svc(request: Request) -> StudioService:
    return request.app.state.svc


Svc = Annotated[StudioService, Depends(get_svc)]
"""The app's `StudioService` — state, caches, templates and the shared
helpers every router uses."""


async def read_capped(upload, limit: int) -> bytes:
    """Read an upload in bounded 64 KiB chunks, raising ``ValueError`` past
    ``limit`` instead of ever buffering an unbounded body into memory.
    Checks a declared size first (``UploadFile.size`` or a Content-Length
    header) when one is available, so an honestly-labelled oversized
    upload is refused without reading a byte of it."""
    declared = getattr(upload, "size", None)
    if declared is None:
        headers = getattr(upload, "headers", None)
        raw_length = headers.get("content-length") if headers else None
        if raw_length is not None:
            try:
                declared = int(raw_length)
            except ValueError:
                declared = None
    if declared is not None and declared > limit:
        raise ValueError("upload too large")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError("upload too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _form(request: Request) -> FormData:
    """The parsed form, left on `request.state.form` as well: a region's
    context is handed the request, not the handler's form
    (`StudioService.asked`)."""
    form = request.state.form = await request.form()
    return form


async def _str_form(request: Request) -> dict[str, str]:
    return {k: v for k, v in (await _form(request)).items() if isinstance(v, str)}


async def _raw_form(request: Request) -> FormData:
    return await _form(request)


StrForm = Annotated[dict[str, str], Depends(_str_form)]
"""The form's string fields (file parts dropped)."""
RawForm = Annotated[FormData, Depends(_raw_form)]
"""The whole form, for `getlist` and file parts."""


def prompt_value(request: Request) -> str:
    """What the user typed into an `hx-prompt`, or "" when there was none.
    The official hx-prompt extension sends the `HX-Prompt` header as
    `encodeURI(value)` (headers are ASCII), so a rename to "wt renamed"
    arrived as "wt%20renamed" and `safe_name` saved it as "wt_20renamed".
    Every reader of the header goes through here."""
    raw = request.headers.get("HX-Prompt")
    if not raw:
        return ""
    return unquote(raw, errors="replace")


@dataclass(frozen=True)
class Upload:
    """The form's `file` part, read (capped) before the handler runs.
    `data` is None when no file was sent or it was over the limit."""

    data: bytes | None
    too_large: bool = False
    name: str = ""  # the file name the browser sent: untrusted, sanitise it


def _capped_upload(limit_name: str, field: str = "file"):
    async def read(request: Request) -> Upload:
        up = (await request.form()).get(field)
        if up is None or isinstance(up, str):
            return Upload(None)
        try:
            # looked up per request, so a test's monkeypatch of the limit
            # takes effect (the same convention as security.py)
            data = await read_capped(up, getattr(limits, limit_name))
            return Upload(data, name=str(getattr(up, "filename", "") or ""))
        except ValueError:
            return Upload(None, too_large=True)

    return read


TakeUpload = Annotated[Upload, Depends(_capped_upload("MAX_TAKE_UPLOAD_BYTES"))]
# a project.json or a bundle (/project.zip): the route caps a JSON body tighter
ProjectUpload = Annotated[Upload, Depends(_capped_upload("MAX_PROJECT_ZIP_BYTES"))]
ClipUpload = Annotated[Upload, Depends(_capped_upload("MAX_UPLOAD_BYTES"))]
MidiUpload = Annotated[Upload, Depends(_capped_upload("MAX_MIDI_UPLOAD_BYTES"))]
SourceUpload = Annotated[Upload, Depends(_capped_upload("MAX_SOURCE_UPLOAD_BYTES"))]
# a phrase said to the loop coach (POST /fragment/coach/talk)
TalkUpload = Annotated[Upload, Depends(_capped_upload("MAX_COACH_TALK_BYTES"))]
# the live vocal take's second file (POST /take/vocal): the dry voice
DryUpload = Annotated[Upload, Depends(_capped_upload("MAX_UPLOAD_BYTES", "dry"))]


def lock_free(endpoint):
    """Mark a route that must never wait on the studio lock. Only for
    handlers that touch nothing but the (self-locking) caches or nothing
    at all — /health must answer while a render is in flight."""
    endpoint.lock_free = True
    return endpoint


@dataclass(frozen=True)
class OutsideLock:
    """What a `@render_outside_lock` handler returns once it has read what
    it needs under the lock: `run()`, called after the lock is released,
    renders from that snapshot and returns the Response."""

    run: Callable[[], Response]


def render_outside_lock(endpoint):
    """Mark a long render route (a song export, a section WAV, the plan, a
    breed generation). Its handler still runs under the studio lock like
    every other route, but only to *snapshot* its inputs — a deep copy of
    the song, the master chain — and returns `OutsideLock(run)`; the
    wrapper releases the lock before calling `run()`. A 1.6 ms request
    used to wait 3.85 s behind `/export/stems.zip`.

    `run()` must read nothing from `StudioState` except through that
    snapshot. What it writes goes to the self-locking caches (`svc.cache`,
    `svc.stems`, the song memos), keyed by content derived from the same
    snapshot, so an edit landing mid-render can neither be seen half-applied
    nor file audio under the wrong key; it just renders the edited song
    under its own new keys next time. Anything that writes session state
    afterwards re-takes the lock itself (`with svc.state.lock:`). The route
    set is pinned by `test_routes.py::RENDER_OUTSIDE_LOCK`. A handler may
    also return a plain Response (a 404, a toast) to answer under the lock."""
    endpoint.render_outside_lock = True
    return endpoint


def pattern_edit(endpoint):
    """Mark a route that can change the step grids: whatever it moved
    (cells, velocity lanes, slides, either grid's length) becomes ONE
    pattern undo step (`StudioState.push_pattern_undo`, Ctrl+Z on Perform
    and Drums). The handler must take `svc: Svc`. A request that moved
    nothing pushes nothing."""

    @functools.wraps(endpoint)
    def run(*args, **kwargs):
        state = kwargs["svc"].state
        before = state.pattern_snapshot()
        result = endpoint(*args, **kwargs)
        if state.pattern_snapshot()["arr"] != before["arr"]:
            state.push_pattern_undo(before)
        return result

    return run


def autoplay(resp: Response) -> Response:
    """`resp` with its `preview-ready` told to start the transport
    (audio.js: a stopped loop plays instead of stashing the URL) — for a
    press whose point is to be heard: an empty state's button. A response
    that cues no preview (a refusal, the Mix view) is returned as it is."""
    try:
        trig = json.loads(resp.headers.get("HX-Trigger") or "{}")
    except ValueError:
        return resp
    if isinstance(trig.get("preview-ready"), dict):
        trig["preview-ready"]["autoplay"] = True
        resp.headers["HX-Trigger"] = json.dumps(trig)
    return resp


_LOCK_SVC = "_studio_lock_svc"


_LOCK_REQ = "_studio_lock_request"
_VIEW_PATH = re.compile(r"/view/([a-z]+)")
# methods that change nothing: no region watch around them
_READS = frozenset({"GET", "HEAD"})


def request_view(request: Request) -> str | None:
    """The view a request comes from: the page itself for `/` and
    `/view/<name>`, else the page htmx says it was sent from
    (HX-Current-URL). None when nothing says (a plain fetch, a download)."""
    path = request.url.path
    if path == "/":
        return "editor"
    if m := _VIEW_PATH.fullmatch(path):
        return m.group(1)
    url = request.headers.get("HX-Current-URL")
    if not url:
        return None
    m = _VIEW_PATH.search(url)
    return m.group(1) if m else "editor"


def _track_edit_before(state, view: str | None, req: Request | None = None) -> None:
    """A track-sound edit belongs to Synths and to the Song page, which
    docks Synths beside the playlist (`fragments/song_dock.html`): a
    request from any other view — another tab included — ends it before
    its handler runs, so that handler sees the loop's own patch. The Song
    page's own routes read the song, never the working patch; the one
    that reads the loop ("Add loop to song") ends the edit itself.
    The drums track's kit is the same edit made from the Drum Lab
    (`track_edit_view`): then Drums and Song are the two views that keep it."""
    if not getattr(state, "song_edit_track", None) or view in (
        None,
        "song",
        state.track_edit_view(),
    ):
        return
    # Another page looking on is not the user leaving: a second tab on
    # another view refreshes itself at every change notice (events.js: its
    # view again, a preview), and that used to end the edit the Song page's
    # dock had just opened — the dock closed by itself. A read, or the
    # preview's render, from a page that is not the one that opened the
    # edit leaves it; anything that changes state from another view, and
    # the opening page going elsewhere, ends it as before.
    owner = (getattr(state, "_edit_saved", None) or {}).get("page")
    origin = req.headers.get(ORIGIN_HEADER) if req is not None else None
    if owner and origin and origin != owner and _looks_on(req):
        return
    state.end_track_edit()


def _looks_on(req: Request) -> bool:
    """A request that reads: a GET, or the preview's render (a POST that
    changes nothing but the caches)."""
    return req.method in _READS or req.url.path == "/fragment/preview"


def _track_edit_after(state, view: str | None) -> None:
    """While Synths edits a song track's sound (`song_edit_track`), every
    patch change made there is that track's too. The patch is set in a
    dozen places across seven route modules (sliders, morph, dice, A/B,
    macros, presets, library, breed, undo); every one of them runs under
    this wrapper, so this is the one choke point — a route added later
    mirrors too. Only a request from Synths mirrors, and only after its
    handler succeeded."""
    mirror = getattr(state, "mirror_patch_to_track", None)
    made_from = getattr(state, "track_edit_view", lambda: None)()
    if view is not None and view == made_from and mirror is not None and mirror():
        state.save_soon()
    # the other way round: the Song page changed the track under the edit
    # (an undo, a new song, a rack edited from the mixer's strip) — the
    # working sound follows the track, or the next mirror would undo it
    follow = getattr(state, "follow_track_edit", None)
    if view == "song" and follow is not None:
        follow()
    # a saved loop open on the note grid (loops.py, `loop_edit`): what the
    # request did to the grid's notes is the loop's too — written back here,
    # the one choke point, so every grid edit saves it as it goes
    mirror_loop = getattr(state, "mirror_loop_edit", None)
    if mirror_loop is not None:
        mirror_loop()


def _settle(svc, req, page, before, mark, result, suppressed):
    """A state-changing request's last step, under the lock: the regions
    of its page whose watch moved go back with the answer
    (`StudioService.refresh`) and out to the pages open elsewhere
    (`StudioService.announce`) — one set, computed once."""
    moved = svc.moved(page, before)
    svc.announce(req, page, moved, mark, result)
    return svc.refresh(req, page, before, result, suppressed, moved)


def _under_state_lock(endpoint):
    """Wrap a sync handler so it runs holding the studio lock.

    The lock belongs to whichever app serves the request, and a router is
    built before any app exists, so the wrapper asks FastAPI for the
    request's `StudioService` through one extra keyword-only dependency
    appended to the handler's own signature. FastAPI resolves the
    handler's (possibly string) annotations against `inspect.unwrap(...)`,
    i.e. the handler's own module, so the added parameter is the only
    thing the wrapper changes about how the handler is called."""
    if getattr(endpoint, "_studio_locked", False):  # already wrapped
        return endpoint
    sig = inspect.signature(endpoint)

    # FastAPI hands the Request to one parameter only: reuse the handler's
    # own when it has one, else ask for it under a private name
    own_req = next(
        (
            name
            for name, prm in sig.parameters.items()
            if prm.annotation in (Request, "Request")
        ),
        None,
    )

    deferred = getattr(endpoint, "render_outside_lock", False)

    @functools.wraps(endpoint)
    def run(*args, **kwargs):
        svc = kwargs.pop(_LOCK_SVC)
        req = kwargs[own_req] if own_req else kwargs.pop(_LOCK_REQ)
        view = request_view(req)
        # no stale regions: a state-changing request's answer carries every
        # region of its page whose state it moved (`StudioService.refresh`)
        page = svc.view_of(req) if req.method not in _READS else None
        with svc.state.lock:
            _track_edit_before(svc.state, view, req)
            before = svc.watch_page(page) if page else None
            mark = svc.state.edits
            try:
                result = endpoint(*args, **kwargs)
            except Exception:
                # refused, but it may have changed (and saved) something first
                if before is not None:
                    svc.announce(req, page, [], mark)
                raise
            finally:
                suppressed = svc.take_suppressed()
            _track_edit_after(svc.state, view)
            if before is not None and not isinstance(result, OutsideLock):
                result = _settle(svc, req, page, before, mark, result, suppressed)
        if deferred and isinstance(result, OutsideLock):
            result = result.run()  # the lock is released: render
            with svc.state.lock:
                # what the deferred half changed (the sound agent applies
                # its patch edits there) is the track's too: mirrored here,
                # or the Song page's next request would take it for a
                # change to undo (`follow_track_edit`)
                _track_edit_after(svc.state, view)
                if before is not None:
                    result = _settle(svc, req, page, before, mark, result, suppressed)
        return result

    extra = [
        inspect.Parameter(_LOCK_SVC, inspect.Parameter.KEYWORD_ONLY, annotation=Svc)
    ]
    if own_req is None:
        extra.append(
            inspect.Parameter(
                _LOCK_REQ, inspect.Parameter.KEYWORD_ONLY, annotation=Request
            )
        )
    # what FastAPI sees the wrapper return: a deferred handler's own
    # annotation names OutsideLock, which is no response model
    ret = Response if deferred else sig.return_annotation
    run.__signature__ = sig.replace(
        parameters=[*sig.parameters.values(), *extra], return_annotation=ret
    )
    run._studio_locked = True
    return run


class StudioRoute(APIRoute):
    """Every route: sync (threadpool) and under the studio lock, unless
    marked `lock_free`. An `async def` handler is refused outright — it
    would run on the event loop, where one render stalls every request —
    unless it is lock-free and does no work (/health)."""

    def __init__(self, path: str, endpoint, **kwargs):
        if not getattr(endpoint, "lock_free", False):
            if inspect.iscoroutinefunction(endpoint):
                raise TypeError(
                    f"{path}: handlers must be plain `def` (see the "
                    "concurrency model in studio/routing.py)"
                )
            endpoint = _under_state_lock(endpoint)
        super().__init__(path, endpoint, **kwargs)


def studio_router() -> APIRouter:
    """A router whose every route is a `StudioRoute`."""
    return APIRouter(route_class=StudioRoute)


def mount(app: FastAPI, router: APIRouter) -> None:
    """Add a router's routes to the app as plain routes.

    Not `app.include_router`: since FastAPI 0.14x that appends one opaque
    `_IncludedRouter` entry, so `app.routes` stops listing the APIRoutes —
    and the route inventory, the lock-policy pin and
    `test_every_route_runs_off_the_event_loop` all introspect `app.routes`
    (the last would silently check nothing). The routes are already
    `StudioRoute`s, built by the router, and request matching walks
    `app.router.routes` in order, so this is all including them needs."""
    for route in router.routes:
        if not isinstance(route, StudioRoute):
            raise TypeError(f"{route!r}: studio routers use route_class=StudioRoute")
    app.router.routes.extend(router.routes)
