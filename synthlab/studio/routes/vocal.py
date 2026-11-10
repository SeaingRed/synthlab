"""The live vocal chain (`vocal.py`; static/vocal.js sings through it):
its panel, its rack, a vocal style loaded whole, the chain as the browser
builds it, and ⏺'s take — the processed voice and the dry one beside it.

- `GET /fragment/vocal`: the panel (`#vox-panel`), drawn when its popover
  opens (it is on every page, outside `#view`).
- `GET /voice/live.json`: the chain resolved for the browser.
- `POST /voice/live/fx`: the rack (`fxrack.edit_chain`, the booth's edit).
- `POST /voice/live/style`: a vocal style's chain, loaded whole.
- `POST /take/vocal`: ⏺'s take — two WAVs (`file` processed, `dry`), its
  bpm and bars — into the clip store as `<name>` and `<name> dry`.
- `POST /take/vocal/song`: a take placed on the song (`section_id`), as the
  booth's "→ Song" places its audition. "→ booth" is the clip library's own
  "Use as take" (`/voice/take/clip`, `booth=1`) on the dry twin.

Every answer that redraws is the panel itself; its state is
`voice_lab.json`'s, so no region moves (the clip library comes back by its
own watch when a take is kept)."""

from __future__ import annotations

import wave

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from ...audio import read_pcm16
from .. import limits, vocal
from .. import song_model as sm
from .. import voice as lab
from ..clips import clip_dir, clip_safe, free_clip_name, save_clip
from ..fxrack import RACK_OPS, RackError, edit_chain
from ..routing import ClipUpload, DryUpload, StrForm, Svc, studio_router
from ..service import StudioService
from ..state import StudioState
from .clips import kept_as
from .song_record import place_clip

router = studio_router()


def vocal_panel(svc: StudioService) -> str:
    s: StudioState = svc.state
    live = lab.read_lab(s.data_dir)["live"]
    return svc.templates.get_template("fragments/vocal_panel.html").render(
        vx=vocal.panel_context(s.data_dir, live, s.song), limits=limits
    )


def _panel(svc: StudioService) -> HTMLResponse:
    return HTMLResponse(vocal_panel(svc))


@router.get("/fragment/vocal")
def vocal_fragment(svc: Svc) -> Response:
    return _panel(svc)


@router.get("/voice/live.json")
def vocal_json(svc: Svc) -> Response:
    s: StudioState = svc.state
    live = lab.read_lab(s.data_dir)["live"]
    return JSONResponse(vocal.live_json(live, s), headers={"Cache-Control": "no-store"})


@router.post("/voice/live/fx")
def vocal_fx(form: StrForm, svc: Svc) -> Response:
    """The vocal rack: `op` add (`kind`), remove (`index`), move (`index`,
    `dir`) or set (`index`, `name`, `value`). A running chain re-reads
    `/voice/live.json` when the panel comes back (vocal.js)."""
    s: StudioState = svc.state
    op = form.get("op", "")
    if op not in RACK_OPS:
        return svc.toast("No such rack edit.")
    live = lab.read_lab(s.data_dir)["live"]
    try:
        chain, _said, _at = edit_chain(live["chain"], op, form)
    except RackError as err:
        return svc.toast(str(err))
    lab.write_lab(s.data_dir, live={**live, "chain": chain})
    return _panel(svc)


@router.post("/voice/live/style")
def vocal_style(form: StrForm, svc: Svc) -> Response:
    """A vocal style (`vocal.STYLES`, `name`): its chain on the rack."""
    picked = vocal.style(form.get("name", ""))
    if picked is None:
        return svc.toast("No such vocal style.")
    lab.write_lab(svc.state.data_dir, live=picked)
    return _panel(svc)


def _bars(raw: str, lo: float, hi: float) -> float | None:
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if lo <= v <= hi else None  # NaN is neither


def _audio(data: bytes | None):
    if data is None:
        return None
    try:
        audio, sr = read_pcm16(data)
    except (wave.Error, EOFError, ValueError):
        return None
    return (audio, sr) if limits.MIN_SR <= sr <= limits.MAX_SR and len(audio) else None


@router.post("/take/vocal")
def take_vocal(
    request: Request, form: StrForm, upload: ClipUpload, dry: DryUpload, svc: Svc
) -> Response:
    """⏺'s take: the processed voice (`file`) as `<name>`, its meta the
    chain it was sung through, and the dry voice (`dry`) beside it as
    `<name> dry`, both cut by the browser to the take's bars at the loop's
    tempo (`bpm`, `bars`). Everything is checked before anything is
    written; a refusal is a 422 toast. The answer is the panel."""
    if upload.too_large or dry.too_large:
        return svc.toast("That take is too large.")
    wet, raw = _audio(upload.data), _audio(dry.data)
    if wet is None or raw is None:
        return svc.toast("Couldn't read that take as two 16-bit WAVs.")
    bpm = _bars(form.get("bpm", ""), *limits.BPM_RANGE)
    bars = _bars(form.get("bars", ""), 0.01, limits.MAX_TAKE_BARS)
    if bpm is None or bars is None:
        return svc.toast("A take needs its tempo and its length in bars.")
    s: StudioState = svc.state
    asked = clip_safe(form.get("name", "").strip() or "vocal")
    name = free_clip_name(s.data_dir, asked)
    twin = free_clip_name(s.data_dir, vocal.dry_name(name))
    live = lab.read_lab(s.data_dir)["live"]
    meta = vocal.take_meta(live, bpm, bars, twin, s)
    dry_meta = {k: meta[k] for k in ("at", "bpm", "bars", "root", "scale")}
    save_clip(
        s.data_dir,
        twin,
        raw[0],
        sr=raw[1],
        meta={**dry_meta, "source": vocal.DRY_SOURCE, "of": name},
    )
    save_clip(s.data_dir, name, wet[0], sr=wet[1], meta=meta)
    said = f"Took “{name}” (and “{twin}”, the dry voice): Clips → Voice takes."
    return kept_as(_panel(svc), asked, name, said)


@router.post("/take/vocal/song")
def take_vocal_song(form: StrForm, svc: Svc) -> Response:
    """→ Song: the take `name` placed in `section_id` (default the song's
    first) at its first bar, on the song's first audio track — the
    booth's "→ Song", one Song undo step."""
    s: StudioState = svc.state
    name = clip_safe(form.get("name", "").strip())
    if not name or not (clip_dir(s.data_dir) / f"{name}.wav").is_file():
        return svc.toast("That take is gone.")
    sections = s.song["sections"]
    section_id = str(form.get("section_id") or (sections[0]["id"] if sections else ""))
    if sm.section_index(s.song, section_id) is None:
        return svc.toast("The song has no section yet — add one on Song first.")
    return place_clip(svc, section_id, "", name, 0)
