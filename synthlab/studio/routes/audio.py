"""Audio endpoints: cached previews (lock-free), live single notes, and
the patch JSON the AudioWorklet reads."""

from __future__ import annotations

import copy
import json
import re
import struct
import threading
from dataclasses import replace
from typing import Annotated, Literal

import numpy as np
from fastapi import Query, Response
from fastapi.responses import RedirectResponse

from ... import sampler
from ...core import SR, note_to_hz
from ...effects import plate_ir, reverb_ir, spring_ir, to_stereo
from ...sequencer import midi
from ...synth import Patch, Synth, at_bpm
from ...wavetables import live_url as wt_live_url
from .. import fxlive, voicetables, worklet
from ..fxlive import live_stack
from ..holdloop import bake_loop, loop_points
from ..render import (
    RenderResult,
    _one_at_a_time,
    _to_wav_bytes,
    lane_note,
    patch_key,
)
from ..routing import OutsideLock, Svc, lock_free, render_outside_lock, studio_router
from ..soundmeta import live_note_duration
from ..state import StudioState

router = studio_router()


@router.get("/preview/{key}.wav")
@lock_free
def preview(key: str, svc: Svc) -> Response:
    result = svc.cache.get(key)
    if result is None:
        return Response(status_code=404)
    return Response(
        result.wav,
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/beat.wav")
def beat_wav(svc: Svc) -> Response:
    """Synths' ▶ beat: the working loop's beat on its own — the Drums
    view's ▶ render (`svc.render("drums")`: the drum lane through its kit,
    the drum bus and the master FX, looped over its own bars at the loop's
    tempo) — answered as a redirect to that render's content-keyed
    `/preview/{key}.wav` — the key the sound bar draws on `#play-beat`
    (`svc.beat_key`), by which the player (audio.js) asks again only for a
    beat that moved. 404 while the loop has no beat."""
    if not svc.state.arrangement.drums:
        return Response(status_code=404)
    result = svc.render("drums")
    return RedirectResponse(f"/preview/{result.key}.wav", status_code=303)


NOTE_RE = re.compile(r"^[A-G]#?-?[0-9]$")

# How many /note renders run at once, outside the studio lock. keys.js warms
# all 24 visible keys after every patch change; unbounded, a warm-up would
# be 24 renders fighting the loop render for the cores. Four keeps a warm-up
# to a few hundred ms without starving everything else.
NOTE_RENDERS = threading.BoundedSemaphore(4)


def note_key(patch: Patch, pitch: str, hold: bool) -> str:
    return stack_note_key(patch_key(patch), pitch, hold)


def stack_note_key(pk: str, pitch: str, hold: bool) -> str:
    return f"note:{pk}:{pitch}" + (":hold" if hold else "")


def render_note(voices, pitch: str, hold: bool, pk: str | None = None) -> RenderResult:
    """One key's WAV — pure in its inputs, so safe outside the lock.
    `voices` is `fxlive.key_voices`: every slot the key sounds on (the
    view's `audible_layers(..., for_keys=True)`, as the worklet plays
    them), each at its octave and gain, dry — the WAV fallback mirrors the
    worklet's set, not its FX. A bare Patch is one voice at unity. None
    sounding: silence. hold=1: the buffer a held key loops, with a long
    sustain window to loop over (soundmeta.LIVE_HOLD_WINDOW), its seam
    crossfade baked in and the loop points in X-Loop (holdloop.py); the
    one-shot stays as it was."""
    if isinstance(voices, Patch):
        voices = [(0, voices, 0, 1.0)]
    pk = pk or fxlive.voices_key(voices, voices[0][0] if voices else -1)
    base = midi(pitch)
    if len(voices) == 1:
        # one voice renders exactly as a single patch always did
        _, patch, octave, gain = voices[0]
        audio = Synth(patch).note(base + 12 * octave, live_note_duration(patch, hold))
        audio = audio * gain if gain != 1.0 else audio
        hz = note_to_hz(base + 12 * octave)
    else:
        patch = voices[0][1] if voices else Patch()
        # one gate for the stack, so a held key's loop is one span of all
        dur = max((live_note_duration(v[1], hold) for v in voices), default=0.5)
        rel = max((float(np.max(v[1].release)) for v in voices), default=0.0)
        audio = np.zeros((round(dur * SR), 2))
        for _, p, octave, gain in voices:
            x = to_stereo(Synth(p).note(base + 12 * octave, dur, gate=dur - rel))
            n = min(len(x), len(audio))
            audio[:n] += x[:n] * gain
        if voices:
            patch = replace(
                patch,
                attack=max(float(np.max(v[1].attack)) for v in voices),
                decay=max(float(np.max(v[1].decay)) for v in voices),
                release=rel,
            )
        hz = note_to_hz(base + 12 * min((v[2] for v in voices), default=0))
    analysis = {}
    if hold:
        loop = loop_points(patch, len(audio), hz)
        audio = bake_loop(audio, loop)
        analysis = {"loop": loop.header()}
    return RenderResult(
        key=stack_note_key(pk, pitch, hold), wav=_to_wav_bytes(audio), analysis=analysis
    )


# A pre-rendered lane's hold loop starts this much later (holdloop
# `settle`) when the lane has FX: its reverb or echoes are still building
# over the first second of the note.
LANE_SETTLE = 1.0
# Where a lane render's tail is cut: under -100 dBFS nothing is heard, and
# `Song.render`'s padding (4 beats + 1 s) would otherwise ride every key.
LANE_FLOOR = 1e-5


def render_lane(voice, units, arr, pitch: str, hold: bool, pk: str) -> RenderResult:
    """One key of a pre-rendered lane (`fxlive.lane_mode` "buffer"): the
    lane's note at its octave and gain through its own FX — the loop's
    layer stem for that note (`render.lane_note`) — its silent tail cut.
    hold=1: the long sustain window and its baked seam, as `render_note`,
    the loop points taken from the note (not the FX tail)."""
    _, p, octave, gain = voice
    m = midi(pitch) + 12 * octave
    dur = live_note_duration(p, hold)
    audio = to_stereo(lane_note(p, m, dur, None, 1.0, gain, units, arr))
    n_note = round(dur * SR)
    loud = np.flatnonzero(np.max(np.abs(audio), axis=1) > LANE_FLOOR)
    audio = audio[: max(n_note, loud[-1] + 1 if len(loud) else 0)]
    analysis = {}
    if hold:
        settle = LANE_SETTLE if units else 0.0
        loop = loop_points(p, n_note, note_to_hz(m), settle=settle)
        audio = bake_loop(audio, loop)
        analysis = {"loop": loop.header()}
    return RenderResult(
        key=stack_note_key(pk, pitch, hold), wav=_to_wav_bytes(audio), analysis=analysis
    )


def _note_response(result: RenderResult) -> Response:
    # X-Patch-Key: which patch this WAV is. keys.js keeps an older key's
    # buffer playing until the render for the current patch lands, and needs
    # to tell the two apart (the URL is the same for both).
    headers = {"Cache-Control": "no-store", "X-Patch-Key": result.key.split(":")[1]}
    if "loop" in result.analysis:
        headers["X-Loop"] = result.analysis["loop"]  # seconds: start,end,gate
    return Response(result.wav, media_type="audio/wav", headers=headers)


@router.get("/note/{pitch}.wav")
@render_outside_lock
def note(
    pitch: str,
    svc: Svc,
    hold: Annotated[int, Query(ge=0, le=1)] = 0,
    view: Annotated[str, Query(max_length=32)] = "",
    layer: Annotated[int, Query(ge=0, le=16)] = 0,
):
    """A live key. It renders outside the studio lock: a first press used to
    queue behind (and then block) every edit, and a warm-up of 24 keys ran
    one at a time. Under the lock this only snapshots the patch — a copy,
    with any synced LFO resolved against the bpm, or a live key plays at a
    different rate than the loop. `layer=N` (1-based slot): that one
    pre-rendered lane of the key's stack, with its FX (`render_lane`);
    404 when the view's keys don't sound it pre-rendered."""
    if not NOTE_RE.match(pitch):
        return Response(status_code=404)
    s: StudioState = svc.state
    lanes = fxlive.key_lanes(s, view or "editor")
    if layer:
        mine = [ln for ln in lanes if ln.slot == layer - 1 and not ln.edited]
        lane = mine[0] if mine else None
        units = (s.layers[lane.slot].get("fx") or []) if lane else []
        if lane is None or fxlive.lane_mode(units) != "buffer":
            return Response(status_code=404)
        voice = fxlive.key_voices(s, [lane])[0]
        arr = fxlive.lane_arr(s.arrangement)
        pk = fxlive.lane_key(voice, units, arr)
        units = copy.deepcopy(units)  # rendered outside the lock

        def make() -> RenderResult:
            return render_lane(voice, units, arr, pitch, bool(hold), pk)
    else:
        # every slot the key sounds on (the view's ▶ set, `for_keys`), each a
        # copy with synced LFOs resolved: the WAV twin of /patch.json's stack
        voices = fxlive.key_voices(s, lanes)
        pk = fxlive.voices_key(voices, s.active_layer)

        def make() -> RenderResult:
            return render_note(voices, pitch, bool(hold), pk)

    key = stack_note_key(pk, pitch, bool(hold))
    cached = svc.notes.get(key)
    if cached is not None:
        return _note_response(cached)

    def run() -> Response:
        # a press racing the warm-up of the same key waits for that render
        # (`render._one_at_a_time`, the stems' rule) instead of starting a
        # second copy of it, and finds it in the cache
        with _one_at_a_time(f"note|{key}"):
            result = svc.notes.peek(key)
            if result is None:
                with NOTE_RENDERS:
                    result = make()
                svc.notes.put(result)
        return _note_response(result)

    return OutsideLock(run)


@router.get("/wavetables.bin")
def wavetables(v: str = "") -> Response:
    """The engine's fixed tables for the live voice (voicetables.py). The
    URL /patch.json hands out carries the content hash: that one is
    immutable, any other asks the browser to revalidate."""
    cache = (
        "public, max-age=31536000, immutable"
        if v == voicetables.version()
        else "no-cache"
    )
    return Response(
        voicetables.payload(),
        media_type="application/octet-stream",
        headers={"Cache-Control": cache},
    )


@router.get("/fx/ir.wav")
def fx_ir(
    decay: Annotated[float, Query(ge=0.2, le=6.0)] = 2.0,
    size: Annotated[float, Query(ge=0.2, le=1.0)] = 0.8,
    sr: Annotated[int, Query(ge=8000, le=192000)] = 44100,
    kind: Literal["room", "plate", "spring"] = "room",
    tone: Annotated[float, Query(ge=0.0, le=1.0)] = 0.65,
    predelay: Annotated[float, Query(ge=0.0, le=0.08)] = 0.012,
    drip: Annotated[float, Query(ge=0.0, le=1.0)] = 0.5,
) -> Response:
    """A reverb unit's room, wet gain included (`effects.reverb_ir`), for the
    live keys' ConvolverNode: the engine's own IR at the browser's own
    rate, 32-bit float so nothing is requantised. Pure in its query.
    `kind=plate` is the plate unit's (`effects.plate_ir`: decay, tone,
    predelay; `size` unread), `kind=spring` the spring's (`effects.spring_ir`:
    decay, tone, drip)."""
    if kind == "plate":
        ir = plate_ir(decay, tone, predelay, sr=sr)
    elif kind == "spring":
        ir = spring_ir(decay, tone, drip, sr=sr)
    else:
        ir = reverb_ir(decay, size, sr=sr)
    return Response(
        _float_wav(ir, sr),
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def _float_wav(x, sr: int) -> bytes:
    data = np.ascontiguousarray(x, dtype="<f4").tobytes()
    ch = x.shape[1]
    fmt = struct.pack("<HHIIHH", 3, ch, sr, sr * ch * 4, ch * 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


@router.get("/patch.json")
def patch_json(svc: Svc, view: str = "", live: str = "", rows: str = "") -> Response:
    """`rows=1`: the typing rows play one layer each (rowsounds.js), so the
    stack is every slot with a sound (`fxlive.key_lanes`)."""
    s: StudioState = svc.state
    resolved = at_bpm(s.patch, fxlive.keys_bpm(s))
    view = view or "editor"
    keys_view = view + (fxlive.ROWS if rows == "1" else "")
    payload = {
        # synced LFO rates resolved against the bpm, as /note and the loop
        # render them: the worklet has no clock of its own
        **resolved.to_json(),
        **worklet.voice_tables(),
        # osc1's sampler buffers for the live voice (None: no live sampler)
        "sampler_live": sampler.live_url(resolved),
        # your own wavetable's mip stack (None: none plays, or not here)
        "wt_live": wt_live_url(resolved),
        # the slots a key sounds on — the view's ▶ set (`audible_layers`)
        # — plus `note_key`, the X-Patch-Key a /note render of that stack
        # carries: keys.js treats a buffer with any other key as a
        # stand-in until it lands; `view` goes back on every /note fetch
        **live_stack(
            s,
            fxlive.key_lanes(s, keys_view),
            "drums" in svc.view_parts(view),
            all_live=live == "all",  # the A/B dev flag: every lane in the worklet
        ),
        "view": keys_view,
    }
    return Response(
        json.dumps(payload),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )
