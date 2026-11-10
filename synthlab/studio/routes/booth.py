"""The vocal booth (the Voice page's "4 · Treat", `booth.py`): pick a
treatment and hear it, edit its rack and its moves, keep the audition as a
clip or put it on the song. Every answer is the Voice panel, which holds
the booth; a render is outside the lock, as the vocoder's is."""

from __future__ import annotations

import json

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ...audio import read_pcm16
from ...core import SR
from .. import booth, events
from .. import song_model as sm
from .. import voice as lab
from ..clips import delete_clip, free_clip_name, save_clip
from ..fxrack import RACK_OPS, RackError, edit_chain
from ..render import RenderResult, _to_wav_bytes
from ..routing import (
    OutsideLock,
    RawForm,
    StrForm,
    Svc,
    prompt_value,
    render_outside_lock,
    studio_router,
)
from ..service import StudioService
from ..state import StudioState
from .song_record import place_clip
from .voice import voice_panel_fragment

router = studio_router()


def _panel(svc: StudioService, said: str = "", **headers) -> HTMLResponse:
    trig = dict(headers)
    if said:
        trig["studio-toast"] = {"message": said}
    extra = {"HX-Trigger": json.dumps(trig)} if trig else {}
    return HTMLResponse(voice_panel_fragment(svc), headers=extra)


def _source(svc: StudioService, was: dict, which: str):
    """(the audio a booth treats, None) or (None, why not): the take, or
    the vocoder's last audition while it is held."""
    if which == "result":
        held = lab.RESULTS.get(was["last"]) if was["last"] else None
        if held is None:
            return (
                None,
                "The vocoded result is gone — Vocode it again, or treat the take.",
            )
        audio, _sr = read_pcm16(held[0])
        return audio, ""
    take = lab.load_take(svc.state.data_dir)
    if take is None:
        return None, "Record or drop in a take first."
    return take, ""


@router.post("/voice/booth/fx")
def booth_fx(form: StrForm, svc: Svc) -> Response:
    """The booth's rack (`fxrack.edit_chain`, the Song audio track's edit):
    `op` add (`kind`), remove (`index`), move (`index`, `dir`) or set
    (`index`, `name`, `value`). Nothing renders: ▶ hears it."""
    s: StudioState = svc.state
    op = form.get("op", "")
    if op not in RACK_OPS:
        return svc.toast("No such rack edit.")
    b = lab.read_lab(s.data_dir)["booth"]
    try:
        chain, _said, _at = edit_chain(b["chain"], op, form)
    except RackError as err:
        return svc.toast(str(err))
    lab.write_lab(s.data_dir, booth={**b, "chain": chain})
    return _panel(svc)


@router.post("/voice/booth/moves")
def booth_moves(form: StrForm, svc: Svc) -> Response:
    """The offline moves (`reverse` forwards | backwards, `swell` 0..1,
    `rate` the tape's speed), what is treated (`source` take | result) and
    whose tempo a synced unit reads (`tempo` auto | loop | song), each kept
    only when the form carries it."""
    s: StudioState = svc.state
    b = lab.read_lab(s.data_dir)["booth"]
    lab.write_lab(s.data_dir, booth=_posted(b, form))
    return _panel(svc)


def _picks(b: dict, form) -> dict:
    """The booth with the source and the tempo the form carries."""
    picked = dict(b)
    if form.get("source") in booth.SOURCES:
        picked["source"] = form["source"]
    if form.get("tempo") in booth.TEMPOS:
        picked["tempo"] = form["tempo"]
    return picked


def _posted(b: dict, form) -> dict:
    """The booth with the moves, the source and the tempo the form carries."""
    return _picks({**b, "moves": booth.clean_moves(form, b["moves"])}, form)


@router.post("/voice/booth/render")
@render_outside_lock
def booth_render(form: StrForm, svc: Svc) -> Response | OutsideLock:
    """Hear the booth: `treatment` (a name) loads that treatment's chain
    and moves first — one press from a treatment to hearing it; without
    one, the moves the form carries are kept; the source and the tempo it
    follows (`booth.follows`: the loop's or the song's) always are. Under
    the lock only to snapshot (the booth, the source's samples, the tempo); the
    treatment runs released, under a progress task. An audition already
    held is answered without rendering. The answer is the panel plus
    `preview-ready` (it plays, and loops, as every preview does)."""
    s: StudioState = svc.state
    was = lab.read_lab(s.data_dir)
    b = was["booth"]
    name = form.get("treatment", "")
    if name:
        picked = booth.treatment(name)
        if picked is None:
            return svc.toast("No such treatment.")
        b = _picks({**b, **picked}, form)
    else:
        b = _posted(b, form)
    lab.write_lab(s.data_dir, booth=b)
    audio, why = _source(svc, was, b["source"])
    if audio is None:
        return svc.toast(why)
    which = booth.follows(b, was["take"], s.song, s.song_edit_track is not None)
    arr = booth.tempo_arr(s.arrangement, s.song, which)
    key = booth.audition_key(audio, b, arr)
    origin = was["take"]

    def answer() -> Response:
        lab.write_lab(s.data_dir, booth_last=key)
        # `plays`: what Now playing calls it — the booth's take, never
        # "Loop · Voice · this page's mix"
        ready = {
            "url": f"/preview/{key}.wav",
            "autoplay": True,
            "plays": f"Booth · {booth.label(b)}",
        }
        with s.lock:
            return _panel(svc, **{"preview-ready": ready})

    held = booth.RESULTS.get(key)
    if held is not None:
        svc.cache.put(RenderResult(key=key, wav=held[0], analysis={}))
        return OutsideLock(answer)

    def run() -> Response:
        with events.Task(svc.events, "Treating", 3) as task:
            out = booth.treat(audio, b["chain"], b["moves"], arr, step=task.step)
            task.step()
        wav = _to_wav_bytes(out)
        booth.RESULTS.put(key, (wav, booth.result_meta(origin, b, len(out))))
        svc.cache.put(RenderResult(key=key, wav=wav, analysis={}))
        return answer()

    return OutsideLock(run)


def _held(svc: StudioService, form) -> tuple[tuple | None, str]:
    """(the audition `key` names — default the last — or None, its key)."""
    key = str(form.get("key") or lab.read_lab(svc.state.data_dir)["booth_last"])
    return (booth.RESULTS.get(key) if key else None), key


def _keep(svc: StudioService, request: Request, form, held) -> str:
    """The held audition into the clip store, never over a clip that is
    there (`free_clip_name`); the name it landed on."""
    wav, meta = held
    audio, _sr = read_pcm16(wav)
    take = meta["booth"]["take"]
    what = meta["booth"]["treatment"] or "treated"
    default = f"{take['name']} {what}" if take.get("name") else what
    name = prompt_value(request) or str(form.get("name") or default)
    kept = free_clip_name(svc.state.data_dir, name)
    save_clip(svc.state.data_dir, kept, audio, sr=SR, meta=meta, cap=booth.MAX_SECONDS)
    return kept


@router.post("/voice/booth/keep")
def booth_keep(request: Request, form: RawForm, svc: Svc) -> Response:
    """ "Keep as clip": the audition that was heard into the clip library
    (group "Voice takes"), with a meta saying what made it."""
    held, _key = _held(svc, form)
    if held is None:
        return svc.toast("That audition is gone — press ▶ Hear it again, then keep.")
    kept = _keep(svc, request, form, held)
    resp = HTMLResponse("", headers={"X-Clip-Name": kept})
    message = f"Kept as “{kept}” — it is in the clip library."
    resp.headers["HX-Trigger"] = json.dumps({"studio-toast": {"message": message}})
    return resp


@router.post("/voice/booth/song")
def booth_song(request: Request, form: RawForm, svc: Svc) -> Response:
    """ "→ Song": the audition kept as a clip, then placed as a take in
    `section_id` (default the song's first) at its first bar, on the song's
    first audio track — the song's own placement (`routes.song_record.
    place_clip`), one Song undo step. A clip the song refuses is not left
    in the store."""
    s: StudioState = svc.state
    held, _key = _held(svc, form)
    if held is None:
        return svc.toast("That audition is gone — press ▶ Hear it again first.")
    sections = s.song["sections"]
    section_id = str(form.get("section_id") or (sections[0]["id"] if sections else ""))
    if sm.section_index(s.song, section_id) is None:
        return svc.toast("The song has no section yet — add one on Song first.")
    kept = _keep(svc, request, form, held)
    resp = place_clip(svc, section_id, "", kept, 0)
    if resp.status_code >= 400:
        delete_clip(s.data_dir, kept)  # nothing refers to it
    return resp
