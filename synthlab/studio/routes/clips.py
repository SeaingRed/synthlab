"""The clip store: bounce, upload, serve, rename and delete clips, and
the clip library drawer that lists them on every page (`cliplib.py`,
region `clip-library`). Clips are placed on the song's audio tracks
(routes/song_record.py: the drawer's "Place"), not on Perform."""

from __future__ import annotations

import json
import wave

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ...audio import read_pcm16
from ...core import SR
from .. import limits
from ..clips import (
    clip_dir,
    clip_safe,
    delete_clip,
    free_clip_name,
    save_clip,
)
from ..render import RenderError
from ..routing import ClipUpload, RawForm, StrForm, Svc, prompt_value, studio_router
from ..state import StudioState
from ..words import plural
from .lab import chops_oob

router = studio_router()


def kept_as(resp: Response, asked: str, kept: str, said: str = "") -> Response:
    """Name the clip a save landed on. A bounce, a kept take and a jam
    never write over a clip that is there (`free_clip_name`): when the
    name asked for was taken, the answer says which one it became — a
    toast, and `X-Clip-Name` for a caller that isn't htmx. `said` is the
    toast a save that worked always lands with (a bounce: nothing else on
    the page shows it happened — the drawer it went to stays shut)."""
    resp.headers["X-Clip-Name"] = kept
    message = said
    if kept != clip_safe(asked):
        message = f"“{clip_safe(asked)}” is taken — kept as “{kept}”. {said}".strip()
    if message:
        toast = {"message": message, "kind": "done"}
        resp.headers["HX-Trigger"] = json.dumps({"studio-toast": toast})
    return resp


@router.post("/clip/bounce")
def clip_bounce(request: Request, form: RawForm, svc: Svc) -> Response:
    """Persist the current view's preview — the exact samples ▶ plays
    (`svc.render(view).audio()`, the same cached result) — as a named clip.
    The durable 'record a sound' primitive every recording feature
    builds on."""
    name = prompt_value(request) or str(form.get("name") or "")
    if not name.strip():
        name = "bounce"
    s: StudioState = svc.state
    v = svc.view_of(request)
    try:
        stereo = svc.render(v).audio()
    except RenderError as err:
        return svc.toast(str(err))
    arr = svc.render_inputs(v).arr  # the loop's clock, for the clip's meta
    meta = {"bpm": arr.bpm, "bars": arr.bars, "source": s.patch.name}
    kept = free_clip_name(s.data_dir, name)
    save_clip(s.data_dir, kept, stereo, meta=meta)
    secs = len(stereo) / SR
    said = f"Bounced “{kept}” ({plural(arr.bars, 'bar')}, {secs:.1f} s) to Clips."
    return kept_as(svc.clips_fragment(request), name, kept, said)


# a keys take posts to /take/keys (routes/takes.py); the dock's recordings
# from before takes said "keys" here, and the clip library files them with
# the jams (`cliplib.kind_of`)
UPLOAD_SOURCES = frozenset({"jam", "talkbox"})


@router.post("/clip/upload")
def clip_upload(
    request: Request, form: RawForm, upload: ClipUpload, svc: Svc
) -> Response:
    """A browser-recorded jam (or any PCM WAV) into the clip store
    Performance FX are captured where they happen — on
    the client's master bus — so they finally become loop material
    without ever rendering server-side."""
    name = str(form.get("name") or "jam")
    if upload.data is None and not upload.too_large:
        return Response(status_code=422)
    if upload.data is None:
        return svc.toast("That upload is too large.")
    try:
        audio_arr, sr = read_pcm16(upload.data)
    except (wave.Error, EOFError, ValueError):
        return svc.toast("Couldn't read that as a 16-bit WAV.")
    if not limits.MIN_SR <= sr <= limits.MAX_SR:
        return svc.toast(f"Unsupported sample rate: {sr} Hz.")
    s: StudioState = svc.state
    kept = free_clip_name(s.data_dir, name)
    # who recorded it, from a short known list (the talkbox's takes said "jam")
    source = str(form.get("source") or "")
    source = source if source in UPLOAD_SOURCES else "jam"
    save_clip(s.data_dir, kept, audio_arr, sr=sr, meta={"source": source})
    # the jam posts with nothing to swap into: the library comes back out
    # of band, as every region whose state moved does
    return kept_as(HTMLResponse(""), name, kept)


@router.get("/clip/{name}.wav")
def clip_wav(name: str, svc: Svc) -> Response:
    # sanitized + containment check: the raw path param must never
    # escape the clip dir (encoded ../ or \ would traverse)
    root = clip_dir(svc.state.data_dir).resolve()
    path = (root / f"{clip_safe(name)}.wav").resolve()
    if not path.is_relative_to(root) or not path.exists():
        return Response(status_code=404)
    return Response(
        path.read_bytes(),
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/fragment/clips")
def clips_list(request: Request, svc: Svc) -> Response:
    """The clip library for the asking page. A search (`q`: cut to
    `cliplib.MAX_QUERY_CHARS`, matched on the name whatever the case)
    answers the LIST only, so the box being typed in is never redrawn;
    `whole=1` the drawer itself, morphed in — opening it, and the Song
    page's "Place into" section, ask that way."""
    whole = request.query_params.get("whole") == "1"
    part = "fragments/clip_library.html" if whole else "fragments/clip_list.html"
    return HTMLResponse(svc.clip_library_html(request, part=part))


def _with_chops(svc, request: Request, resp: HTMLResponse) -> HTMLResponse:
    """On the Lab page, the open song's chop list rides along out of band
    (`routes.lab.chops_oob`): a chop kept as the clip just renamed or
    deleted names it, and offers "Update clip" only while it is there."""
    extra = chops_oob(svc, request)
    if not extra:
        return resp
    return HTMLResponse(bytes(resp.body).decode("utf-8") + extra)


@router.post("/fragment/clip/delete")
def clip_delete(request: Request, form: RawForm, svc: Svc) -> Response:
    """Delete a clip. Never refused: a clip the session still holds (a
    take, a drum row, a step of the song's undo ring) says where in the
    button's own confirm (`cliplib._used`, from `StudioState.clip_usage`),
    and what points at it afterwards plays silence, as a missing sample
    does. A Lab chop that was kept as it offers "Keep" again
    (`labchops.own_clip`)."""
    delete_clip(svc.state.data_dir, str(form.get("name", "")))
    return _with_chops(svc, request, svc.clips_fragment(request))


@router.post("/fragment/clip/rename")
def clip_rename(request: Request, form: StrForm, svc: Svc) -> Response:
    """Rename a clip in place (a jam is kept under a timestamped
    name the moment it stops, and named afterwards — never behind a
    prompt whose Cancel threw the take away). Both names go through
    `clip_safe`, like every other clip path; a taken or blank name is a
    422 toast, never an overwrite or a silent "clip". Every persisted
    reference follows the clip, and so does the Lab chop it was kept from:
    its "Update clip" goes on writing over this clip under its new name
    (`StudioState.rename_clip_everywhere`, ★ Keep's rename too)."""
    s: StudioState = svc.state
    old = clip_safe(form.get("name", "").strip())
    raw_new = form.get("new", "").strip()
    if not (clip_dir(s.data_dir) / f"{old}.wav").exists():
        return Response(status_code=404)
    if not raw_new:  # clip_safe would quietly turn it into "clip"
        return svc.toast("A clip needs a name.")
    new = clip_safe(raw_new)
    if new == old:
        return svc.clips_fragment(request)
    if not s.rename_clip_everywhere(old, new):
        return svc.toast(f"There is already a clip called “{new}”.")
    return _with_chops(svc, request, svc.clips_fragment(request))
