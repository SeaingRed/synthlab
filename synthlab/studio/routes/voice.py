"""Voice lab: store a take, vocode it over one of four carriers, keep the
result as a clip (`voice.py`)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ...audio import read_pcm16
from ...core import SR
from ...synth import at_bpm
from .. import events, limits, sounds
from .. import voice as lab
from ..chords import CodeError, parse_code
from ..clips import clip_dir, clip_safe, free_clip_name, list_clips, save_clip
from ..fxrack import build_chain, clean_chain
from ..render import RenderError, RenderResult, _to_wav_bytes, patch_key
from ..routing import (
    OutsideLock,
    RawForm,
    StrForm,
    Svc,
    TakeUpload,
    prompt_value,
    render_outside_lock,
    studio_router,
)
from ..service import StudioService
from ..state import StudioState
from ..voice import TakeError, load_take, read_wav, save_take, take_path
from .clips import kept_as

router = studio_router()

_SETTING_FIELDS = tuple(lab.DEFAULTS)


def voice_panel_fragment(svc: StudioService) -> str:
    return svc.templates.get_template("fragments/voice_panel.html").render(
        **svc.voice_context()
    )


def _toast_header(message: str) -> dict:
    return {"HX-Trigger": json.dumps({"studio-toast": {"message": message}})}


def _posted_settings(form, saved: dict) -> dict:
    """The vocoder's settings as this form asks for them, over the saved
    ones: only the fields the form carries move (a drawer button, a test,
    an old page posting `pitch` for the one carrier note)."""
    raw = {k: form[k] for k in _SETTING_FIELDS if isinstance(form.get(k), str)}
    if "notes" not in raw and isinstance(form.get("pitch"), str):
        raw["notes"] = form["pitch"]
    return lab.clean_settings(raw, saved)


def _remember(s: StudioState, form, **changes) -> dict:
    """Write what the form holds (and `changes`) to voice_lab.json, so the
    panel that comes back shows what was just typed."""
    saved = lab.read_lab(s.data_dir)["settings"]
    settings = {**_posted_settings(form, saved), **changes.pop("settings", {})}
    return lab.write_lab(s.data_dir, settings=settings, **changes)


@router.post("/voice/keep")
def voice_keep(request: Request, form: RawForm, svc: Svc) -> Response:
    """Promote the current take to the clip store: named,
    multi-slot, in the export by reference. The vocoder's single
    working slot is untouched."""
    s: StudioState = svc.state
    take = load_take(s.data_dir)
    if take is None:
        return svc.toast("No take yet — record or drop one in first.")
    name = prompt_value(request) or str(form.get("name") or "take")
    kept = free_clip_name(s.data_dir, name)
    save_clip(s.data_dir, kept, take, meta={"source": "voice take"})
    # the button swaps nothing (hx-swap="none"): the clip library comes back
    # out of band, as every region whose state moved does. Answering with
    # the library itself, as this did while the shelf was not on this page,
    # would have it thrown away with the body.
    return kept_as(HTMLResponse(""), name, kept)


@router.post("/voice/take")
def voice_take(upload: TakeUpload, form: RawForm, svc: Svc) -> Response:
    """Store an uploaded take. The browser has already decoded whatever it
    was given (a dragged file, or MediaRecorder's webm) down to PCM WAV,
    so there is no codec surface here. voice.js sends the vocoder's form
    with the file, so the panel that comes back has not forgotten it."""
    if upload.too_large:
        return svc.toast("Take is too large.")
    if upload.data is None:
        return svc.toast("No take was sent.")
    try:
        samples = read_wav(upload.data)
    except TakeError as err:
        return svc.toast(str(err).capitalize() + ".")
    save_take(svc.state.data_dir, samples)
    _remember(svc.state, form, take=lab.clean_origin(None))
    return HTMLResponse(voice_panel_fragment(svc))


@router.post("/voice/take/clip")
def voice_take_clip(form: StrForm, svc: Svc) -> Response:
    """ "Use as take" in the clip library: the clip `name`, summed to mono,
    becomes the take — read exactly as an uploaded take is (`read_wav`:
    the same size cap, sample-rate range and 30 s cut), so what an upload
    would refuse this refuses with the same words. The clip is untouched;
    the take remembers which clip it was (`voice_lab.json`). `booth`
    (the live talkbox's "⏺ → booth": its take kept, then sent here) also
    points the booth at the take, not a vocoded result of the last one."""
    s: StudioState = svc.state
    name = form.get("name", "").strip()
    root = clip_dir(s.data_dir).resolve()
    path = (root / f"{clip_safe(name)}.wav").resolve()
    if not name or not path.is_relative_to(root) or not path.is_file():
        return svc.toast("That clip is gone.")
    if path.stat().st_size > limits.MAX_TAKE_UPLOAD_BYTES:
        return svc.toast("Take is too large.")
    try:
        samples = read_wav(path.read_bytes())
    except TakeError as err:
        return svc.toast(str(err).capitalize() + ".")
    save_take(s.data_dir, samples)
    changes = {"take": lab.clip_origin(s.data_dir, name)}
    if form.get("booth"):
        changes["booth"] = {**lab.read_lab(s.data_dir)["booth"], "source": "take"}
    _remember(s, form, **changes)
    said = _toast_header(
        f"“{clip_safe(name)}” is the Voice take now — treat it in 4 · Treat."
        if form.get("booth")
        else f"“{clip_safe(name)}” is the take now."
    )
    return HTMLResponse(voice_panel_fragment(svc), headers=said)


@router.post("/voice/carrier/clip")
def voice_carrier_clip(form: StrForm, svc: Svc) -> Response:
    """ "as carrier" in the clip library: the clip `name` is what plays
    under the voice (the carrier kind becomes "clip"). Checked as the
    render will read it, so a clip that cannot be one says so now."""
    s: StudioState = svc.state
    name = form.get("name", "").strip()
    try:
        lab.load_carrier_clip(s.data_dir, name)
    except TakeError as err:
        return svc.toast(str(err).capitalize() + ".")
    _remember(s, form, settings={"carrier": "clip", "clip": clip_safe(name)})
    said = _toast_header(f"“{clip_safe(name)}” is the carrier now.")
    return HTMLResponse(voice_panel_fragment(svc), headers=said)


@router.post("/voice/preset")
def voice_preset(form: StrForm, svc: Svc) -> Response:
    """A named starting point for the vocoder (`voice.PRESETS`, `name`):
    its settings laid over the form's, and the carrier it was made for —
    a recipe Sound — loaded on Synths the way Use loads one (the sound it
    replaces is "before last load" there, and one Ctrl+Z away). Needs no
    take: the panel comes back set, ready for one."""
    s: StudioState = svc.state
    name = form.get("name", "")
    preset = lab.PRESETS.get(name)
    if preset is None:
        return svc.toast("No such Voice preset.")
    saved = lab.read_lab(s.data_dir)["settings"]
    settings = lab.preset_settings(name, _posted_settings(form, saved))
    lab.write_lab(s.data_dir, settings=settings)
    said = f"{preset['label']}: the vocoder is set"
    sound = sounds.find(svc, "recipes", preset.get("sound", ""))
    if sound is not None:
        s.push_history()
        s.load_sound(sound.patch, sound.chain, label=sound.label)
        s.save()
        said += f", and Synths plays “{sound.label}” — Ctrl+Z there brings yours back"
    return HTMLResponse(voice_panel_fragment(svc), headers=_toast_header(said + "."))


@router.get("/voice/take.wav")
def voice_take_wav(svc: Svc) -> Response:
    path = take_path(svc.state.data_dir)
    if not path.exists():
        return Response(status_code=404)
    return Response(
        path.read_bytes(),
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


def _clip_tempo(data_dir, name: str) -> tuple[float | None, bool]:
    """(the bpm a clip's meta states, whether it is a loop: bpm and bars)."""
    meta = next((c for c in list_clips(data_dir) if c["name"] == name), {})
    bpm = meta.get("bpm")
    bpm = float(bpm) if isinstance(bpm, (int, float)) and 20 <= bpm <= 400 else None
    return bpm, bpm is not None and "bars" in meta


@router.post("/voice/render")
@render_outside_lock
def voice_render(request: Request, form: StrForm, svc: Svc) -> Response | OutsideLock:
    """Vocode the take over the carrier the form asks for. Under the lock
    only to snapshot: the settings (saved as they are read), the patch at
    the tempo, the master chain, the key, and the carrier's source — the
    Synths loop is rendered here (`svc.render`, cached), a clip read from
    the store. The vocoding itself, seconds for a long take, runs with the
    lock released under a progress task. A render already held is answered
    without rendering."""
    s: StudioState = svc.state
    take = load_take(s.data_dir)
    if take is None:
        return svc.toast("Record or drop in a take first.")
    was = lab.read_lab(s.data_dir)
    st = _posted_settings(form, was["settings"])
    lab.write_lab(s.data_dir, settings=st)
    arr = s.arrangement
    kind, n, bpm = st["carrier"], len(take), arr.bpm
    patch = at_bpm(replace(s.patch), bpm)
    chords: list[list[int]] = []
    source, ident, tempo, is_loop = None, "", None, True
    try:
        if kind == "sound":
            chords = parse_code(st["notes"], arr.root, arr.scale_name)
            if len(chords) > 1 and st["step"] != "even":
                tempo = arr.bpm
        elif kind == "loop":
            loop = svc.render("editor")
            source, ident, tempo = loop.audio(), loop.key, arr.bpm
        elif kind == "clip":
            source = lab.load_carrier_clip(s.data_dir, st["clip"])
            ident = hashlib.sha1(source.tobytes()).hexdigest()
            tempo, is_loop = _clip_tempo(s.data_dir, st["clip"])
    except CodeError as err:
        return svc.toast(f"Carrier notes: {err}.")
    except RenderError as err:
        return svc.toast(str(err))
    except TakeError as err:
        return svc.toast(str(err).capitalize() + ".")
    master = clean_chain(s.fx_master)
    chain = build_chain(master, arr)
    classes = lab.pitch_classes(arr.root, arr.scale_name)
    # everything the WAV depends on: a re-recorded take of the same length,
    # a master FX edit, a tempo or key change must not replay the old render
    blob = json.dumps(
        {
            "patch": patch_key(patch),
            "settings": st,
            "master": master,
            "bpm": arr.bpm,
            "key": [arr.root, arr.scale_name],
            "carrier": ident,
        },
        sort_keys=True,
    ).encode()
    key = "voice:" + hashlib.sha1(take.tobytes() + b"\0" + blob).hexdigest()[:24]
    origin = was["take"]

    def answer() -> Response:
        lab.write_lab(s.data_dir, last=key)
        # `plays`: what Now playing calls it (not "this page's mix")
        plays = f"Vocoder · {lab.carrier_label(kind)}"
        ready = {
            "preview-ready": {
                "url": f"/preview/{key}.wav",
                "autoplay": True,
                "plays": plays,
            }
        }
        with s.lock:
            html = voice_panel_fragment(svc)
        return HTMLResponse(html, headers={"HX-Trigger": json.dumps(ready)})

    held = lab.RESULTS.get(key)
    if held is not None:
        svc.cache.put(RenderResult(key=key, wav=held[0], analysis={}))
        return OutsideLock(answer)

    def run() -> Response:
        share = None
        try:
            with events.Task(svc.events, "Vocoding", 3) as task:
                if kind == "sound":
                    carrier = lab.chord_carrier(patch, chords, n, bpm, st["step"])
                elif kind == "follow":
                    snap = classes if st["snap"] == "scale" else None
                    carrier, share = lab.follow_carrier(patch, take, snap, st["octave"])
                else:
                    carrier = lab.tiled(source, n, loop=is_loop)
                audio = lab.render_voice(
                    patch,
                    take,
                    bands=st["bands"],
                    chain=chain,
                    carrier=carrier,
                    shift=st["shift"],
                    smooth=st["smooth"],
                    sibilance=st["sibilance"],
                    step=task.step,
                )
                task.step()
        except TakeError as err:
            return svc.toast(str(err).capitalize() + ".")
        wav = _to_wav_bytes(audio)
        facts = lab.carrier_facts(st, patch.name, len(chords), share)
        meta = lab.result_meta(origin, st, facts, n, tempo)
        lab.RESULTS.put(key, (wav, meta))
        svc.cache.put(RenderResult(key=key, wav=wav, analysis={}))
        return answer()

    return OutsideLock(run)


@router.post("/voice/result/keep")
def voice_result_keep(request: Request, form: RawForm, svc: Svc) -> Response:
    """ "Keep result": the audition `key` names (default: the last one) into
    the clip store — the samples that were heard, with a meta saying what
    made them. Never over a clip that is there (`free_clip_name`). An
    audition no longer held (a restart, four newer ones) is a toast."""
    s: StudioState = svc.state
    was = lab.read_lab(s.data_dir)
    key = str(form.get("key") or was["last"])
    held = lab.RESULTS.get(key) if key else None
    if held is None:
        return svc.toast("That render is gone — press Vocode it again, then keep.")
    wav, meta = held
    audio, _sr = read_pcm16(wav)
    take = meta["vocoder"]["take"]
    default = f"{take['name']} vocoded" if take.get("name") else "vocoded"
    name = prompt_value(request) or str(form.get("name") or default)
    kept = free_clip_name(s.data_dir, name)
    save_clip(s.data_dir, kept, audio, sr=SR, meta=meta)
    resp = HTMLResponse("", headers={"X-Clip-Name": kept})
    resp.headers.update(_toast_header(f"Kept as “{kept}” — it is in the clip library."))
    return resp
