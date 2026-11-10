"""The sampler instrument: turn a clip or a render into a playable sound,
and chop it into slices across the keys (`samplerview.py`, the engine's
`synthlab/sampler.py`).

Making a sampler sound is a load (like Use): "Before last load" and one
undo step get the outgoing patch, and the page lands on Synths. Chop and
slice edits are patch edits (one undo step each) answered like a panel
post: the preview, plus the regions that moved (the sampler block among
them).

● Record sample (`/fragment/sampler/record`, static/samplerec.js) is a mic
take timed to the session's bpm: the browser counts a bar in, records the
bars chosen, cuts the take to the downbeat and posts it; it is kept in the
clip store and then used exactly as "Use as sampler" uses a clip."""

from __future__ import annotations

import json
import math
import time
import wave
from dataclasses import replace

from fastapi import Request, Response

from ... import params, sampler
from ...audio import read_pcm16
from .. import limits
from .. import samplerview as sv
from ..clips import free_clip_name, load_clip, save_clip
from ..render import RenderError
from ..routing import RawForm, StrForm, Svc, TakeUpload, lock_free, studio_router
from ..state import StudioState
from ..words import plural
from .macros import param_rows_oob

router = studio_router()


def adopt_audio(
    request: Request,
    svc: Svc,
    audio,
    sr: int,
    name: str,
    chop: str = "",
    *,
    fields: dict | None = None,
    root: int | None = None,
    say: str = "",
    land: str = "editor",
    kind: str = "",
) -> Response:
    """Store `audio` and make it the edited sound, landing on Synths.
    `chop` is "" (the whole sample), "transients", or a slices string
    (`sampler.format_slices`: the Sample Lab's one-per-beat / per-bar).
    `fields` are Patch fields set on top of the fresh sampler sound (the
    Lab's vocal instrument: start, loop, root, formant mode — clamped like
    any patch); `root` is the root to fall back on when the sample has no
    sure pitch of its own (the song's key); `say` is a toast to land with;
    `land` the page it lands on (a keys take "→ Perform" as a sample lands
    on Perform, whose grid then plays it); `kind` the toast's kind."""
    s: StudioState = svc.state
    if sv.silent(audio):
        return svc.toast("That audio is silent: nothing to sample.")
    sid = sampler.save(s.sample_dir, audio, sr)
    before = s.patch_snapshot()
    found = sv.detected_root(sid)
    patch = sv.sampler_patch(sid, name, found if found is not None else root)
    if chop == "transients":
        patch, _note = sv.chop(patch, "transients")
    elif chop:
        patch = replace(patch, sampler_slices=sampler.clean_slices(chop))
    if fields:
        patch = params.clamp(replace(patch, **fields))
    s.load_sound(patch, label=name)
    s.push_undo_step(before)
    s.save()
    return svc.land_on(request, land, say, kind=kind)


@router.get("/sampler/live/{sid}.bin")
@lock_free
def sampler_live(sid: str, request: Request) -> Response:
    """The live voice's sampler buffers (`sampler.live_payload`): the
    prepared sources a key reads, for the query `sampler.live_url` wrote.
    Content-addressed — the id is the audio — so immutable. Lock-free: it
    reads only the sample store, which locks itself."""
    # a sample this machine doesn't hold (a project.json imported without
    # its audio) is a 404, never an empty payload: the answer is cached for
    # good, so "nothing to play" would outlive the sample's arrival (the
    # bundle imported afterwards) and the live keys stayed silent
    if not sampler.valid_id(sid) or sampler.get(sid) is None:
        return Response(status_code=404)
    st = sampler.live_settings(dict(request.query_params))
    # a zone map: every zone's sample, for the same reason
    if any(sampler.get(z) is None for z in sampler.zone_samples(st.zones)):
        return Response(status_code=404)
    try:
        payload = sampler.live_payload(sid, st)
    except ValueError:  # past LIVE_MAX_SAMPLES: live_ok never sends a key here
        return Response(status_code=413)
    return Response(
        payload,
        media_type="application/octet-stream",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def use_clip(
    request: Request, svc: Svc, name: str, say: str = "", land: str = "editor"
) -> Response:
    """The clip `name` becomes the sampler sound (`adopt_audio`): "Use as
    sampler", and a recorded sample once it is in the clip store."""
    loaded = load_clip(svc.state.data_dir, name) if name else None
    if loaded is None:
        return svc.toast("That clip is gone.")
    audio, sr = loaded
    return adopt_audio(request, svc, audio, sr, name, say=say, land=land)


@router.post("/fragment/sampler/clip")
def sampler_from_clip(request: Request, form: StrForm, svc: Svc) -> Response:
    """ "Use as sampler" on a clip (the library) or a take (the Song page): its
    audio, snapshotted into the sample store by content."""
    return use_clip(request, svc, form.get("name", "").strip())


REC_BARS = (1, 2, 4)  # the panel's lengths (samplerec.js sends one)


@router.post("/fragment/sampler/record")
def sampler_record(
    request: Request, form: RawForm, upload: TakeUpload, svc: Svc
) -> Response:
    """● Record sample: a mic take of `bars` bars at the session's bpm (a
    16-bit PCM WAV, already cut to the downbeat after the count-in and
    shifted by the latency in the browser: samplerec.js). Kept in the clip
    store as `rec-YYYYmmdd-HHMMSS` (meta: source "sampler-rec", bpm,
    bars), then used as "Use as sampler" uses a clip: the same load, one
    undo step, the page answered whole. The body is capped by BodySizeLimit
    (the general cap); the file itself at MAX_TAKE_UPLOAD_BYTES, a toast."""
    s = svc.state
    try:
        bars = int(str(form.get("bars", "1")))
    except ValueError:
        bars = 0
    if bars not in REC_BARS:
        return svc.toast("A recorded sample is 1, 2 or 4 bars.")
    if upload.data is None:
        too = upload.too_large
        return svc.toast("That recording is too large." if too else "No recording.")
    try:
        audio, sr = read_pcm16(upload.data)
    except (wave.Error, EOFError, ValueError):
        return svc.toast("Couldn't read that recording as a 16-bit WAV.")
    if not limits.MIN_SR <= sr <= limits.MAX_SR or not len(audio):
        return svc.toast(f"Unsupported recording ({sr} Hz).")
    if sv.silent(audio):  # refused before anything is written
        return svc.toast("The recording is silent: is the microphone on?")
    bpm = float(s.arrangement.bpm)
    name = free_clip_name(s.data_dir, time.strftime("rec-%Y%m%d-%H%M%S"))
    meta = {"source": "sampler-rec", "bpm": bpm, "bars": bars}
    save_clip(s.data_dir, name, audio, sr=sr, meta=meta)
    many = "bar" if bars == 1 else "bars"
    say = f"Recorded {name} ({bars} {many} at {bpm:g} bpm): play it from the keys."
    return use_clip(request, svc, name, say=say)


@router.post("/fragment/sampler/resample")
def sampler_resample(request: Request, form: StrForm, svc: Svc) -> Response:
    """Resample: `source=loop` is the Perform loop as ▶ plays it there
    (synths, drums, clips, FX), `phrase` the Synths ▶ render — the same
    audio ⏺ Bounce keeps, straight into a sampler."""
    source = form.get("source", "loop")
    view = "perform" if source == "loop" else "editor"
    try:
        audio = svc.render(view).audio()
    except RenderError as err:
        return svc.toast(str(err))
    if sv.silent(audio):
        what = "loop" if source == "loop" else "phrase"
        return svc.toast(
            f"The {what} is silent: put notes or a beat on the Loops page first."
        )
    # it replaces the edited layer's sound (one undo step): say so, what it
    # took and the way back — nothing else on the page names the change
    s: StudioState = svc.state
    what = "the working loop" if source == "loop" else "the phrase"
    bars = plural(svc.render_inputs(view).arr.bars, "bar")
    say = (
        f"Layer {s.active_layer + 1} is now a sampler of {what} ({bars}): "
        f"chop it in Source. Ctrl+Z gives “{s.patch.name or 'the sound'}” back."
    )
    return adopt_audio(
        request, svc, audio, sampler.SR, f"resampled {source}", say=say, kind="done"
    )


@router.post("/fragment/sampler/chop")
def sampler_chop(request: Request, form: StrForm, svc: Svc) -> Response:
    """Chop the sample into slices on the keys: an equal grid of 8 or 16
    over start..end, the transients, or clear back to the whole sample."""
    s: StudioState = svc.state
    mode = form.get("mode", "")
    if mode not in sv.CHOPS or not sv.sampling(s.patch):
        return Response(status_code=422)
    if not s.patch.sampler_sample:
        return svc.toast("Load a sample first.")
    before = s.patch_snapshot()
    s.patch, note = sv.chop(s.patch, mode)
    # after the edit: push_undo_step keeps a step only once the patch moved
    # off it (called first, it kept nothing, and Ctrl+Z skipped past the
    # chop to the sound before the sampler)
    s.push_undo_step(before)
    resp = svc.do_render(request)
    if note and resp.status_code == 200:
        trig = json.loads(resp.headers["HX-Trigger"])
        trig["studio-toast"] = {"message": note}
        resp.headers["HX-Trigger"] = json.dumps(trig)
    return resp


@router.post("/fragment/sampler/slice")
def sampler_slice(request: Request, form: StrForm, svc: Svc) -> Response:
    """One slice's pitch (±1 st), reverse, start nudge (±5 ms), or `move`:
    a dragged marker's start, `at` = 0..1 of the sample (layout.js; the
    server clamps it, and the touching neighbour follows)."""
    s: StudioState = svc.state
    action = form.get("action", "")
    try:
        index = int(form.get("index", ""))
        at = float(form["at"]) if action == "move" else None
    except (KeyError, ValueError):
        return Response(status_code=422)
    if action not in sv.SLICE_ACTIONS or not sv.sampling(s.patch):
        return Response(status_code=422)
    if at is not None and not math.isfinite(at):
        return Response(status_code=422)
    s.push_history()
    s.patch = sv.edit_slice(s.patch, index, action, at)
    return svc.do_render(request)


# --- zones: a multi-sample instrument ------------------------------------------


def _clip_sample(svc: Svc, name: str) -> str | None:
    """The clip `name` snapshotted into the sample store (its id), None
    when it is gone or silent."""
    loaded = load_clip(svc.state.data_dir, name) if name else None
    if loaded is None or sv.silent(loaded[0]):
        return None
    return sampler.save(svc.state.sample_dir, loaded[0], loaded[1])


@router.post("/fragment/sampler/zone/add")
def sampler_zone_add(request: Request, form: StrForm, svc: Svc) -> Response:
    """＋ zone on a clip (the library drawer, the clip hub): the clip as one
    more zone of the edited sampler sound (`samplerview.add_zone`: placed
    by its detected pitch without moving a zone already set), one undo
    step. A sound that samples nothing yet becomes a sampler of that one
    zone first (a load, as "Use as sampler"). Either way the answer is
    Synths' whole page (morphed in place there, so the drawer stays open
    for the next clip)."""
    s: StudioState = svc.state
    name = form.get("name", "").strip()
    sid = _clip_sample(svc, name)
    if sid is None:
        return svc.toast("That clip is gone or silent: nothing to add.")
    if len(sv.zones_of(s.patch)) >= sampler.MAX_ZONES:
        return svc.toast(f"A sound holds {sampler.MAX_ZONES} zones at most.")
    before = s.patch_snapshot()
    if not sv.sampling(s.patch):
        patch = sv.sampler_patch(sid, name)
        s.load_sound(sv.with_zones(patch, sv.as_zones(patch)), label=name)
        say = f"{name} is the sampler's first zone: ＋ zone on another adds the next."
    else:
        s.patch = sv.add_zone(s.patch, sid)
        z = sv.zones_of(s.patch)[-1]
        say = f"{name} is zone {len(sv.zones_of(s.patch))}: {sv.zone_keys(z)}."
    s.push_undo_step(before)
    s.save()
    return svc.land_on(request, "editor", say)


@router.post("/fragment/sampler/zones/map")
def sampler_zones_map(request: Request, form: StrForm, svc: Svc) -> Response:
    """Auto-map: several clips (`names`, one per line — a drawer group's)
    as one sampler sound, a zone each (`samplerview.auto_map`: `mode`
    "pitch" spreads them over the keys by their detected pitch, a shared
    pitch its round-robin takes; "keys" puts them a key each from C4).
    A load, landing on Synths, one undo step."""
    s: StudioState = svc.state
    mode = form.get("mode", "pitch")
    if mode not in sv.MAP_MODES:
        return Response(status_code=422)
    names = [n.strip() for n in form.get("names", "").splitlines() if n.strip()]
    sids = [sid for n in names[: sampler.MAX_ZONES] if (sid := _clip_sample(svc, n))]
    if not sids:
        return svc.toast("None of those clips has audio to map.")
    zones = sv.auto_map(list(dict.fromkeys(sids)), mode)
    label = form.get("label", "").strip()[:60] or names[0]
    before = s.patch_snapshot()
    s.load_sound(
        sv.with_zones(sv.sampler_patch(zones[0].sample, label), zones), label=label
    )
    s.push_undo_step(before)
    s.save()
    how = "by pitch" if mode == "pitch" else "a key each from C4"
    say = f"{plural(len(zones), 'zone')} mapped {how}: play them from the keys."
    return svc.land_on(request, "editor", say, kind="done")


@router.post("/fragment/sampler/zone")
def sampler_zone(request: Request, form: StrForm, svc: Svc) -> Response:
    """One zone's edit (`samplerview.edit_zone`): `set` any of its fields
    (the zone map's drags post the keys and velocities), `step` one
    (`field` by `by`: a row's − / + on the root, take, gain and tune),
    `move` it (`dk` keys, `dv` velocities), `remove` it, or `detect` its
    root. One undo step."""
    s: StudioState = svc.state
    action = form.get("action", "")
    if action not in sv.ZONE_ACTIONS or not sv.sampling(s.patch):
        return Response(status_code=422)
    try:
        index = int(form.get("index", ""))
        values = {
            k: float(form[k])
            for k in (*sv.ZONE_FIELDS, "dk", "dv", "by")
            if form.get(k, "") != ""
        }
    except ValueError:
        return Response(status_code=422)
    if not all(math.isfinite(v) for v in values.values()):
        return Response(status_code=422)
    if action == "step":
        if form.get("field") not in sv.ZONE_FIELDS:
            return Response(status_code=422)
        values["field"] = form["field"]
    before = s.patch_snapshot()
    s.patch = sv.edit_zone(s.patch, index, action, values)
    s.push_undo_step(before)  # after the edit: it only keeps a step that moved
    return svc.do_render(request)


@router.post("/fragment/sampler/zones")
def sampler_zones(request: Request, form: StrForm, svc: Svc) -> Response:
    """The zone map's own tools (`samplerview.zones_tool`): `spread` the
    layers over the keys by their roots, `chops` (the chops as zones, one
    key each), `clear` (back to the single sample). One undo step."""
    s: StudioState = svc.state
    action = form.get("action", "")
    if action not in sv.ZONES_ACTIONS or not sv.sampling(s.patch):
        return Response(status_code=422)
    before = s.patch_snapshot()
    s.patch, note = sv.zones_tool(s.patch, action)
    s.push_undo_step(before)
    resp = svc.do_render(request)
    if note and resp.status_code == 200:
        trig = json.loads(resp.headers["HX-Trigger"])
        trig["studio-toast"] = {"message": note}
        resp.headers["HX-Trigger"] = json.dumps(trig)
    return resp


@router.post("/fragment/sampler/root")
def sampler_root(request: Request, svc: Svc) -> Response:
    """The panel's "use": the sample's detected pitch becomes its root
    (`sampler_root`), one undo step. The root's slider row comes back
    OOB, or the #patch form's next post would send the old root back."""
    s: StudioState = svc.state
    if not sv.sampling(s.patch) or not s.patch.sampler_sample:
        return Response(status_code=422)
    key = sv.detected_root(s.patch.sampler_sample, sv.PITCH_SHOWN)
    if key is None:
        return svc.toast("No clear pitch in this sample: set the root by ear.")
    before = s.patch_snapshot()
    s.patch = replace(s.patch, sampler_root=key)
    s.push_undo_step(before)  # after the edit: it only keeps a step that moved
    return svc.do_render(request, param_rows_oob(svc, ["sampler_root"]))
