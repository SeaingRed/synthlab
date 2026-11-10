"""Your own wavetables (`synthlab/wavetables.py`): made from a clip or a
sample, or drawn, then
picked like a built-in one.

Making a table is a patch edit (one undo step): the table is stored
content-addressed in `studio_data/wavetables/`, the edited sound's
`wt_custom` names it (and an oscillator plays the wavetable shape, if none
did), and the answer is the whole page with its preview, as the mod
matrix's mixes answer. Picking one is the wavetable picker's own radio
(`forms.patch_from_form`: "own:<id>"). The live voice reads a table from
`GET /wavetable/live/<id>.bin` — its own small payload, never
/wavetables.bin."""

from __future__ import annotations

import json
from dataclasses import replace

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ... import wavetables
from ...params import clamp
from .. import cliphub, limits
from ..clips import list_clips
from ..routing import StrForm, Svc, lock_free, studio_router
from ..state import StudioState

router = studio_router()


@router.get("/wavetable/live/{tid}.bin")
@lock_free
def wavetable_live(tid: str) -> Response:
    """The table's mip stack for the live voice (`wavetables.live_payload`).
    Content-addressed, so immutable; a table this machine doesn't hold is a
    404 (never cached as nothing). Lock-free: it reads only the table
    store, which locks itself."""
    payload = wavetables.live_payload(tid) if wavetables.valid_id(tid) else None
    if payload is None:
        return Response(status_code=404)
    return Response(
        payload,
        media_type="application/octet-stream",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def sources(s: StudioState) -> list[tuple[str, str]]:
    """What a table can be made from: (ref, label) — the clip store's clips,
    then the sampler's samples (cliphub's references)."""
    out = [(f"clip:{c['name']}", c["name"]) for c in list_clips(s.data_dir)]
    for sid, _mt, _size in cliphub._sample_listing(s.data_dir):
        out.append((f"sample:{sid}", f"sample {sid[:8]}"))
    return out


@router.get("/fragment/wavetable/sources")
def wavetable_sources(request: Request, svc: Svc) -> HTMLResponse:
    """The "+ from clip…" form's source menu, drawn when it opens."""
    html = svc.templates.get_template("fragments/wt_sources.html").render(
        sources=sources(svc.state)
    )
    return HTMLResponse(html)


def _int(raw, lo: int, hi: int, default: int) -> int:
    try:
        return min(hi, max(lo, int(float(raw))))
    except (TypeError, ValueError, OverflowError):
        return default


def _use(request: Request, svc: Svc, spec, name: str, source: str) -> Response:
    """Store `spec` and make it the edited sound's table (one undo step)."""
    s: StudioState = svc.state
    if len(wavetables.listing(s.wavetable_dir)) >= limits.MAX_WT_TABLES:
        known = wavetables.table_id(spec)
        if wavetables.info(known) is None:
            return svc.toast(
                f"{limits.MAX_WT_TABLES} tables is the most a studio keeps."
            )
    tid = wavetables.save(s.wavetable_dir, spec, name, source)
    s.push_history()
    p = s.patch
    fields: dict = {"wt_custom": tid}
    if "wavetable" not in (p.osc1, p.osc2):
        fields["osc1"] = "wavetable"
    s.patch = clamp(replace(p, **fields))
    s.save()
    resp = svc.full_page_with_preview(request)
    trig = json.loads(resp.headers.get("HX-Trigger") or "{}")
    said = (
        f"Wavetable “{wavetables.clean_name(name)}”: {len(spec)} frames"
        " (Ctrl+Z undoes it)"
    )
    resp.headers["HX-Trigger"] = json.dumps(trig | {"studio-toast": {"message": said}})
    return resp


@router.post("/fragment/wavetable/clip")
def wavetable_from_clip(request: Request, form: StrForm, svc: Svc) -> Response:
    """A clip or a sample sliced into single cycles (`wavetables.from_audio`:
    equal slices, or one period of its pitch at each point), its first
    MAX_WT_SOURCE_SECONDS."""
    s: StudioState = svc.state
    ref = cliphub.parse_ref(form.get("source", ""))
    got = cliphub.read(s.data_dir, ref) if ref and ref.kind != "stem" else None
    if got is None:
        return svc.toast("Pick a clip or a sample to make the table from.")
    audio, sr = got
    audio = cliphub.at_sr(audio[: int(limits.MAX_WT_SOURCE_SECONDS * sr)], sr)
    if float(abs(audio).max(initial=0.0)) < 1e-4:
        return svc.toast("That audio is silent: nothing to make a table of.")
    lo, hi = limits.WT_FRAMES_RANGE
    frames = _int(form.get("frames"), lo, hi, 16)
    mode = form.get("mode", "slice")
    cycles = wavetables.from_audio(audio, frames, mode)
    label = ref.name if ref.kind == "clip" else f"sample {ref.name[:8]}"
    name = form.get("name", "").strip() or label
    return _use(request, svc, wavetables.spectra(cycles), name, ref.text)


@router.post("/fragment/wavetable/draw")
def wavetable_draw(request: Request, form: StrForm, svc: Svc) -> Response:
    """Up to four drawn cycles (`wavetables.parse_drawing`), each a frame."""
    cycles = wavetables.parse_drawing(form.get("points", ""))
    if cycles is None:
        return svc.toast("Draw a cycle first (up to four frames).")
    if float(abs(cycles).max(initial=0.0)) < 1e-4:
        return svc.toast("That drawing is flat: nothing to hear.")
    name = form.get("name", "").strip() or "Drawn table"
    return _use(request, svc, wavetables.spectra(cycles), name, "drawn")
