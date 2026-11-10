"""Render the studio loop to WAV bytes, with content-keyed LRU caching.

Guards: 10 s length cap, NaN/inf -> RenderError (a user-facing
message, never a broken WAV), hard safety clip on the way out.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..analysis import meter
from ..audio import read_pcm16, wav_bytes
from ..core import SR, n_samples, scan
from ..effects import chain as fx_chain
from ..effects import limit as fx_limit
from ..effects import pan as fx_pan
from ..effects import to_stereo
from ..sequencer import Song
from ..synth import Patch, Synth
from .arrange import (
    NoteMemo,
    bar_beats,
    build_clips_song,
    build_song,
    follow_layer,
    loop_bars,
    loop_seconds,
    nudge_of,
    strum_beats,
)
from .curves import GAIN, lane_array
from .fxrack import (
    build_chain,
    clean_chain,
    kick_steps,
    repeat_period,
    tune_key,
    unit_args,
)
from .lru import LRU
from .schema import arr_dict
from .state import Arrangement, lane_fields

# The longest stretch rendered in one piece: a song section's cycle past it
# renders in chunks of whole bars (`section_windows`).
MAX_LOOP_SECONDS = 10.0
# The longest loop the studio plays. Every tempo and bar count the controls
# offer fits: the slowest tempo (40 bpm) under the longest loop, 12 bars
# (a 3-bar synth grid against a 4-bar beat), is 72 s. It was 10 s — the
# section chunk above — which refused 4 bars under 96 bpm: a beat read
# from a slow song could not be played at its own tempo, and a tempo typed
# under the line left every later edit a 422. Measured on the Surface
# (ARM): a 13 s loop renders in 0.3-0.5 s, a 24 s one in 0.5-0.8 s.
LOOP_CAP_SECONDS = 72.0


def loop_refusal(arr: Arrangement, asked: Arrangement) -> str:
    """Why the loop may not go from `arr` to `asked`, or "": the change
    makes a loop longer and takes it past `LOOP_CAP_SECONDS` — the synth
    grid alone (Synths), the beat alone (Drums) or both (Perform). Every
    route that stores a tempo or a bar count asks this *before* it stores:
    one that stored first and failed to render left the page showing the
    new tempo over the old loop, and every later edit a 422. A change
    that shortens a loop, or leaves its length alone, always goes through,
    so a session that is already too long can be brought back."""
    for parts in (("synth",), ("drums",), ("synth", "drums")):
        seconds = loop_seconds(asked, parts)
        if seconds > LOOP_CAP_SECONDS and seconds > loop_seconds(arr, parts) + 1e-9:
            bars = loop_bars(asked, parts)
            slowest = math.ceil(bars * (bar_beats(asked) * 60.0) / LOOP_CAP_SECONDS)
            return (
                f"{bars} bars at {asked.bpm:g} bpm is a {seconds:.1f} s loop; "
                f"the cap is {LOOP_CAP_SECONDS:g} s. {bars} bars play from "
                f"{slowest} bpm up. Nothing was changed: the tempo is still "
                f"{arr.bpm:g} bpm"
            )
    return ""


# What a preview contains. Each view renders only its own parts: the
# Editor/Bass Lab play synth layers, Drums plays the kit, Perform plays
# everything — so a problem in one workshop can't break another.
ALL_PARTS: tuple[str, ...] = ("synth", "drums")


# every stem render's notes, by content (`arrange.NoteMemo`): a one-cell
# edit re-renders its stem, but only the note that changed. Shared by the
# loop, the bench phrase and song sections; /debug/stats reports it.
NOTE_MEMO = NoteMemo()


class RenderError(Exception):
    """User-facing render failure; str(err) is shown as a toast."""


@dataclass
class RenderResult:
    key: str
    wav: bytes
    analysis: dict
    # the gain `normalize_master` applied to reach `wav`, in dB (a loop
    # from `render_loop`; None for anything else). The sound agent's
    # loudness subtracts it: every loop is brought to one level, so the
    # finished render's own loudness barely moves when a sound gets louder.
    norm_gain_db: float | None = None
    # how many passes of the loop's grid the WAV holds: 1, except a loop
    # whose notes rest by chance (`song_model.loop_passes`). The player
    # reads it off the URL (`preview_url`) to keep its playheads on one
    # pass while the buffer loops all of them
    passes: int = 1

    def audio(self) -> np.ndarray:
        """The render as the stereo float array its WAV holds. Decoded from
        the WAV rather than kept beside it, so a reader (Bass Lab's panels,
        a post-process, a bounce) hears exactly the bytes ▶ plays and the
        cache holds no second copy. `read_pcm16` pairs with the encoder, so
        re-encoding this array gives the same samples back."""
        return read_pcm16(self.wav)[0]


@dataclass(frozen=True)
class RenderInputs:
    """Everything that decides what a loop sounds like. The cache key is
    derived from this object alone, so an input can't be rendered without
    also being keyed.

    `layers` and the fx lists are held as tuples so the dataclass is
    hashable-ish; the constructor accepts lists too and normalizes them.
    """

    patch: Patch
    arr: Arrangement
    curves: dict | None = None
    layers: tuple = ()  # other layers' dicts (stash format)
    main_muted: bool = False
    main_fx: tuple = ()
    master_fx: tuple = ()
    drums_fx: tuple = ()
    parts: tuple[str, ...] = ALL_PARTS
    clip_root: Path | None = None
    # steps where a held main-lane note attacks again: set only by a song
    # section's tiled cycle (song_model.SectionParts), never by a loop
    breaks: tuple = ()
    # the main lane's track gain (song sections; a loop's main lane has none)
    main_gain: float = 1.0
    # (pitches, start_step, period): a section chunk's arp (build_song)
    arp: tuple = ()
    # the pitch a section chunk's first main-lane note slides from
    # (song_model.SectionParts.window); None for a loop and a whole cycle
    lead: str | None = None
    # a song section's automation lanes for the main lane, {curves, at, n}
    # (song_model.SectionParts.auto; a layer's ride its dict as "auto")
    auto: dict | None = None
    # passes of the loop's grid these inputs are tiled to (1: the loop as
    # it is). Only a loop whose notes rest by chance has more
    # (`song_model.loop_passes`: each pass rolls afresh)
    passes: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "layers", tuple(self.layers) if self.layers else ())
        object.__setattr__(self, "main_fx", tuple(self.main_fx) if self.main_fx else ())
        object.__setattr__(
            self, "master_fx", tuple(self.master_fx) if self.master_fx else ()
        )
        object.__setattr__(
            self, "drums_fx", tuple(self.drums_fx) if self.drums_fx else ()
        )
        object.__setattr__(self, "parts", tuple(self.parts))
        object.__setattr__(self, "breaks", tuple(self.breaks))

    def key(self) -> str:
        """Everything the render reads, and nothing it does not — an
        unrelated part of the render never invalidates a cache it doesn't
        affect, and clips key on content, not name. (This was, byte for
        byte, the `render_key(...)` function it replaced until
        `RENDER_REV` joined the payload.)"""
        parts = self.parts
        arr_d = arr_dict(self.arr)
        # the loop's chord track renders nothing (guidance for the
        # generators and Perform's lane): never a render input
        arr_d.pop("chords", None)
        if "drums" not in parts:
            # the drum machine is not playing: none of its lane is an
            # input (its grid, kit, clock, velocities, mixer). A drum
            # velocity edit used to re-key the Synths bench and every card
            # audition for identical audio. What an FX chain still reads
            # off the drum clock joins the key below (`chain_clock`).
            for name in lane_fields("drum"):
                arr_d.pop(name, None)
        if "synth" not in parts:
            arr_d["cells"], arr_d["velocities"], arr_d["slides"] = [], {}, []
            arr_d.pop("note_chance", None)  # the note grid's, as its velocities
        # absent unless a lane has one: every key from before cuts holds
        if not arr_d.get("cuts"):
            arr_d.pop("cuts", None)
        if "clips" not in parts:
            arr_d["clips"] = []
        payload = {
            "rev": RENDER_REV,
            "parts": sorted(parts),
            "arr": arr_d,
            "master_fx": list(self.master_fx),
            "drums_fx": list(self.drums_fx) if "drums" in parts else [],
        }
        if "drums" not in parts:
            clock = self._chain_clock()
            if clock:  # absent unless a chain reads the drum clock
                payload["chain_clock"] = clock
        # a re-recorded clip (same name, new audio) must change the key
        names = set(arr_d.get("drum_samples", {}).values()) | {
            str(c.get("clip", "")) for c in arr_d.get("clips", [])
        }
        if self.clip_root is not None and names:
            from .clips import clip_hash

            payload["clip_hashes"] = {
                n: clip_hash(self.clip_root, n) for n in sorted(names)
            }
        if "synth" in parts:
            payload |= {
                "patch": keyed_patch(self.patch),
                "curves": self.curves or {},
                "layers": [_keyed_layer(lay) for lay in self.layers],
                "main_muted": self.main_muted,
                "main_fx": list(self.main_fx),
            }
            # absent for every loop, so their keys are unchanged
            if self.breaks:
                payload["breaks"] = list(self.breaks)
            if self.main_gain != 1.0:
                payload["main_gain"] = self.main_gain
            # A layer's gain was once silently dropped, so the
            # same inputs now render differently. /preview/{key}.wav is
            # served immutable: salt the key of any loop that plays a layer
            # gain, so no browser keeps the pre-fix audio. Keys of loops
            # whose layers all sit at 1.0 are unchanged.
            if any(_layer_gain(lay) != 1.0 for lay in self.layers):
                payload["layer_gain_v"] = 2
            if self.arp:
                payload["arp"] = [list(self.arp[0]), *self.arp[1:]]
            if self.lead:
                payload["lead"] = self.lead
            if self.auto:
                payload["auto"] = self.auto
            # a loop with chance plays several passes, each rolled afresh
            # only its keys gain this, so no loop without a
            # chance lane moved, and one keyed the old way (one pass, the
            # same roll each repeat) is never served again
            if self.passes > 1:
                payload["passes"] = self.passes
        blob = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha1(blob).hexdigest()[:12]

    def _chain_clock(self) -> dict:
        """What this render's FX chains read off the drum machine's clock
        (`_chain_clock`, the stem keys' own rule, over `chain_arr`): a
        sidechain's kick hits and grid, a beat repeat's period — which is
        gcd(bars, drum_bars) whether or not the drums play. The chains are
        the ones the parts run: the master's, and with the synth the main
        lane's and each layer's."""
        chains: list = [self.master_fx]
        if "synth" in self.parts:
            chains.append(self.main_fx)
            chains += [lay.get("fx") for lay in self.layers if isinstance(lay, dict)]
        fx_arr = chain_arr(self)
        clock: dict = {}
        for units in chains:
            clock |= _chain_clock(clean_chain(list(units or [])), fx_arr)
        return clock


# The engine's and the render pipeline's revision, as every render key sees
# it. `/preview/{key}.wav` is served `immutable` for a year, so a key must
# never come to mean different audio: BUMP THIS whenever a change — in the
# engine (`synthlab/`), `arrange.py`, the FX units or this module — moves
# what the same key inputs render to (a note's release, a filter's law, a
# default level...). Every loop key, and through it every section key and
# post-processed key, then changes once, and no browser replays the old
# audio. One number instead of a salt per fix: the per-fix salts
# (`layer_gain_v` above, `_cycle_fixes`) predate it and stay as they are.
# Bumping it moves the pinned literals in tests/studio/test_render.py and
# test_song_routes.py: update them in the same change. Stem keys and note
# keys do not carry it — they address in-memory caches and `no-store`
# responses, which a restart empties.
#   1  a gated note's release lasts the patch's release time
#   2  a sidechain / pump duck falls over 6 ms, not one sample
#   3  the pitch unit splices each grain in phase with the last
RENDER_REV = 3


def _layer_gain(lay) -> float:
    """A layer dict's gain as `build_song` reads it (1.0 when absent)."""
    try:
        return float(lay.get("gain", 1.0)) if isinstance(lay, dict) else 1.0
    except (TypeError, ValueError):
        return 1.0


# Patch fields added after the cache keys were pinned, with their defaults.
# At its default such a field changes nothing a render hears, so it stays out
# of the key: adding a field must not invalidate every existing cache
# identity (tests/studio/test_render.py pins them). Append new fields here.
KEY_ADDED_FIELDS = {
    "pitch_bend": 0.0,
    "bend_range": 2.0,
    # the sampler (synthlab/sampler.py). `sampler_sample` is the audio's
    # content hash, so a sampler sound's keys carry its audio by content
    "sampler_root": 60,
    "sampler_start": 0.0,
    "sampler_end": 1.0,
    "sampler_loop": "off",
    "sampler_loop_start": 0.0,
    "sampler_loop_end": 1.0,
    "sampler_xfade": 0.01,
    "sampler_keytrack": "on",
    "sampler_mode": "resample",
    "sampler_stretch": 1.0,
    "sampler_sample": "",
    "sampler_slices": "",
    # hard sync, the fm4 shape, analog drift — all
    # off at these, so every older key stays the key it was
    "osc2_sync": "off",
    "sync_pitch": 0.0,
    "fm4_algo": "stack",
    "fm4_ratio1": 1.0,
    "fm4_ratio2": 2.0,
    "fm4_ratio3": 1.0,
    "fm4_ratio4": 1.0,
    "fm4_level1": 1.0,
    "fm4_level2": 0.3,
    "fm4_level3": 0.0,
    "fm4_level4": 0.0,
    "fm4_decay1": 0.0,
    "fm4_decay2": 0.0,
    "fm4_decay3": 0.0,
    "fm4_decay4": 0.0,
    "fm4_feedback": 0.0,
    "drift": 0.0,
    # warp, your own wavetable, voicing —
    # off at these, so every older key stays the key it was
    "osc1_warp": "off",
    "osc1_warp_amt": 0.0,
    "osc2_warp": "off",
    "osc2_warp_amt": 0.0,
    "wt_custom": "",
    "voice_mode": "poly",
    "glide_mode": "always",
    # filter 2, envelopes 3 / 4, LFOs 2 / 3,
    # the drawn shapes, the wheels and route slots 5-12 — all off at their
    # Patch defaults, so every older key stays the key it was
    **{
        name: getattr(Patch(), name)
        for name in (
            "filter2_route filter2_type filter2_cutoff filter2_resonance "
            "filter2_key_track filter2_env "
            + " ".join(
                f"env{k}_{t}"
                for k in (3, 4)
                for t in ("attack", "decay", "sustain", "release", "curve")
            )
            + " "
            + " ".join(
                f"lfo{k}_{t}"
                for k in (2, 3)
                for t in ("rate", "shape", "sync", "phase", "mode")
            )
            + " lfo_draw lfo2_draw lfo3_draw mod_wheel aftertouch "
            + " ".join(
                f"mod{k}_{t}" for k in range(5, 13) for t in ("src", "dst", "amt")
            )
        ).split()
    },
    # a multi-sample instrument: no zone map plays the one
    # sample, as before. A map names every sample by its content hash
    "sampler_zones": "",
}


def keyed_patch(patch: Patch | dict) -> dict:
    """A patch (or a stash's patch dict) as the render keys hash it."""
    d = dict(patch) if isinstance(patch, dict) else patch.to_json()
    for k, default in KEY_ADDED_FIELDS.items():
        if k in d and np.ndim(d[k]) == 0 and d[k] == default:
            del d[k]
    return d


def _keyed_layer(lay):
    if isinstance(lay, dict) and isinstance(lay.get("patch"), dict):
        return {**lay, "patch": keyed_patch(lay["patch"])}
    return lay


def patch_key(patch: Patch) -> str:
    blob = json.dumps(keyed_patch(patch), sort_keys=True).encode()
    return hashlib.sha1(blob).hexdigest()[:12]


# The one in-memory WAV encoder is `audio.wav_bytes` (no module carries a
# copy of its own). `wav_bytes` keeps whatever channel count it's given;
# every render path here hands it a mix that must reach the browser/export
# as stereo
# even when a single voice rendered mono, so this wrapper forces that
# before encoding and keeps the old name — `server.py` and a couple of
# tests still import `_to_wav_bytes` from here.
def _to_wav_bytes(audio: np.ndarray, sr: int = SR) -> bytes:
    return wav_bytes(to_stereo(np.asarray(audio, dtype=np.float64)), sr)


def _physical_ram() -> int:
    """This machine's RAM in bytes, 0 when it cannot be read."""
    try:
        if os.name == "nt":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong)
                    for name in ("phys", "phys_free", "page", "page_free")
                    + ("virt", "virt_free", "ext")
                ]

            st = Status()
            st.length = ctypes.sizeof(st)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.phys)
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        return 0


def _stem_cache_bytes() -> int:
    """StemCache's byte budget: an eighth of the machine's RAM, at least
    256 MB and at most 4 GB (`SYNTHLAB_STEM_CACHE_MB` sets it outright).

    It was a flat 256 MB, sized for loops (a loop stem is at most ~7 MB:
    10 s, float64 stereo). A song section's stems run to a whole cycle plus
    its tail (a 48 s cycle is ~68 MB a stem), and a song is only quick to
    edit while ALL its stems are held: measured on a 283 s, 6-track song
    (750 MB of stems), 256 MB evicted 29 of every 30, so a fader move
    re-rendered the whole song (16 s) where a cache that holds it renders
    the one stem that moved (under 1 s)."""
    asked = os.environ.get("SYNTHLAB_STEM_CACHE_MB", "")
    if asked.isdigit() and int(asked) > 0:
        return int(asked) << 20
    return min(4 << 30, max(256 << 20, _physical_ram() // 8))


STEM_CACHE_BYTES = _stem_cache_bytes()


class StemCache(LRU["np.ndarray | SectionAudio | _LaneStem"]):
    """Content-keyed stereo stems. Sections of the loop (main lane, each
    layer, drums) render independently, so a live tweak to one lane reuses
    every other section's cached audio. Bounded in bytes
    (`STEM_CACHE_BYTES`, which is the bound that matters) as well as
    entries. Also holds each song section's
    master-processed cycle (`SectionAudio`, `StudioService._master_audio`),
    which a song reads for every pass that plays or folds it."""

    def __init__(
        self, max_entries: int = 1024, max_bytes: int | None = STEM_CACHE_BYTES
    ):
        super().__init__(max_entries, max_bytes, sizeof=lambda a: a.nbytes)


def _lane_hashes(arr: Arrangement, clip_root) -> list[str]:
    if clip_root is None:
        return []
    from .clips import clip_hash

    return [clip_hash(clip_root, str(c.get("clip", ""))) for c in arr.clips]


def _sample_hashes(arr: Arrangement, clip_root) -> dict:
    """Content hashes of referenced drum clips — the stem cache must not
    outlive a re-recorded clip of the same name."""
    if clip_root is None or not getattr(arr, "drum_samples", None):
        return {}
    from .clips import clip_hash

    return {v: clip_hash(clip_root, n) for v, n in arr.drum_samples.items()}


def _wrap_tile(audio: np.ndarray, own: int, n: int) -> np.ndarray:
    """Cut one section's render to its own loop length with the release
    tail folded back onto the start (a looping stem, not a truncated one),
    then repeat it to fill `n` samples."""
    core = _pad_trim(audio, own).copy()
    tail = audio[own : 2 * own]
    if len(tail):
        core[: len(tail)] += tail
    reps = max(1, -(-n // own))
    return _pad_trim(np.concatenate([core] * reps), n)


def _pad_trim(audio: np.ndarray, n: int) -> np.ndarray:
    stereo = to_stereo(np.asarray(audio, dtype=np.float64))
    if len(stereo) < n:
        stereo = np.vstack([stereo, np.zeros((n - len(stereo), 2))])
    return stereo[:n]


def _stem_key(kind: str, payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True).encode()
    return f"{kind}:" + hashlib.sha1(blob).hexdigest()[:16]


def _bar_clock(arr: Arrangement) -> dict:
    """The meter and the ramp, for a stem key: absent at 4/4 and one
    tempo, so every stem key from before them holds."""
    out: dict = {}
    if getattr(arr, "meter", "4/4") != "4/4":
        out["meter"] = arr.meter
    if getattr(arr, "ramp", None):
        out["ramp"] = list(arr.ramp)
    return out


def _timing(arr: Arrangement) -> dict:
    return {
        "bpm": arr.bpm,
        "bars": arr.bars,
        "steps_per_bar": arr.steps_per_bar,
        "swing": arr.swing,
        **_bar_clock(arr),
    }


def _drum_timing(arr: Arrangement) -> dict:
    return {
        "bpm": arr.bpm,
        "bars": arr.drum_bars,
        "steps_per_bar": arr.drum_steps,
        "swing": arr.drum_swing,
        **_bar_clock(arr),
    }


def chain_arr(inp: RenderInputs) -> Arrangement:
    """The arrangement an FX chain (a stem's or the master's) is built
    from. A sidechain ducks on the drum machine's kicks, and those only
    play when drums are among the parts: without them it is handed no
    kicks, so "sidechain does nothing without drums" holds — the render
    agrees with `RenderInputs.key()`, which drops the drums then too (it
    used to duck on the kit it wasn't playing, under a key that didn't
    carry it)."""
    return inp.arr if "drums" in inp.parts else dc_replace(inp.arr, drums=[])


def _chain_clock(units: list, arr: Arrangement) -> dict:
    """What `build_chain(units, arr)` reads from `arr` beyond the stem's
    own timing, for its cache key: a sidechain's kick hits and the drum
    machine's clock (`fxrack._kick_times`). Empty for any other chain, so
    no other stem's key changes. (The drum swing is not here because the
    trigger times ignore it; a muted or soloed-out kick row is, as the
    empty hit list `fxrack.kick_steps` leaves.) A beat repeat reads both clocks' bars
    (`fxrack.repeat_period`) — a synth stem's own payload has no drum
    bars — so its period joins the key. A tape stop (`stop`) restarts on
    the same period and keys the same way."""
    out: dict = {}
    if any(u.get("kind") in ("beat_repeat", "stop") for u in units):
        out = {"repeat_period": repeat_period(arr), "repeat_bpm": arr.bpm}
        out |= _bar_clock(arr)
    # an auto-tune, a harmonizer or the rack's vocoder (its chord) on the
    # song's key reads the session's key and scale (`fxrack.tune_key`):
    # they join the key, as resolved
    keyed = ("autotune", "harmony", "vocoder_fx")
    tuned = [u for u in clean_chain(units) if u["kind"] in keyed]
    if tuned:
        out["tune"] = [list(tune_key(u["params"], arr)) for u in tuned]
    if not any(u.get("kind") == "sidechain" for u in units):
        return out
    return out | {
        "kicks": sorted(kick_steps(arr)),
        "kick_bpm": arr.bpm,
        **_bar_clock(arr),
        "kick_bars": arr.drum_bars,
        "kick_steps": arr.drum_steps,
    }


@dataclass(frozen=True)
class _StemJob:
    """One stem of a render: what it is keyed by and how it is built. Both
    `render_stems` (the loop, wrap-tiled) and `render_stems_raw` (song
    sections, un-folded) walk the same list, so they can't disagree about
    what a stem is."""

    kind: str
    name: str
    payload: dict
    build: Callable
    fx_units: list
    own_parts: tuple[str, ...]
    # what the stem's FX chain is built from (`chain_arr`)
    chain_arr: Arrangement

    def cache_payload(self) -> dict:
        """The stem's key payload: its own inputs, its chain, and whatever
        that chain reads off the arrangement (`_chain_clock`)."""
        return {
            **self.payload,
            "fx": self.fx_units,
            **_chain_clock(self.fx_units, self.chain_arr),
        }


def _stem_jobs(inp: RenderInputs) -> list[_StemJob]:
    """The stems `inp` renders, in mix order. Names are `"main"`,
    `"layer2"`/`"layer3"`/... per other layer (1-based, main counts as
    layer 1 — the user-visible export filenames from before this refactor),
    `"drums"` and `"clips"`. Never a patch name: an unsanitized
    `patch.name` in a layer once flowed straight into the exported zip's
    entry name (a patch named `../../evil/x` wrote `layer-0-../../evil/x.wav`).
    """
    arr, parts = inp.arr, inp.parts
    patch_json = inp.patch.to_json()
    jobs: list[_StemJob] = []
    fx_arr = chain_arr(inp)

    def job(kind, payload, build, fx_units=None, own_parts=parts, name=None):
        # clean_chain only recognizes a list (a bare `for u in units if
        # isinstance(units, list) else []`), and RenderInputs holds its fx
        # tuples as tuples — pass it one or the chain silently vanishes.
        fx = clean_chain(list(fx_units) if fx_units else [])
        jobs.append(_StemJob(kind, name or kind, payload, build, fx, own_parts, fx_arr))

    bare = dc_replace(arr, drums=[])
    synth = "synth" in parts
    arp = inp.arp if inp.arp and arr.arp_mode != "off" else ()
    if synth and not inp.main_muted and (arr.cells or arp):
        breaks = frozenset(inp.breaks)
        job(
            "main",
            {
                "patch": patch_json,
                "cells": arr.cells,
                "velocities": arr.velocities,
                "slides": arr.slides,
                **({"cuts": arr.cuts} if arr.cuts else {}),
                "arp_mode": arr.arp_mode,
                "arp_octaves": arr.arp_octaves,
                # absent at 0: every stem key from before the strum holds
                **({"strum": arr.strum} if strum_beats(arr) else {}),
                # timing inside a step: absent when unset (or under an arp)
                **({"note_nudge": nudge} if (nudge := nudge_of(arr)) else {}),
                # absent when no step has a chance: every stem
                # key from before the chance lane holds
                **({"note_chance": arr.note_chance} if arr.note_chance else {}),
                "curves": inp.curves or {},
                **_timing(arr),
                **({"breaks": sorted(breaks)} if breaks else {}),
                **({"gain": inp.main_gain} if inp.main_gain != 1.0 else {}),
                **({"arp": [list(arp[0]), *arp[1:]]} if arp else {}),
                # two chunks with the same notes and another predecessor
                # are different renders; absent unless a slide reads it
                **({"lead": inp.lead} if inp.lead else {}),
                # a section's lanes: the whole shape and where this chunk
                # reads it from, so a chunk's key holds its slice
                **({"auto": inp.auto} if inp.auto else {}),
            },
            lambda: build_song(
                inp.patch,
                bare,
                inp.curves,
                breaks=breaks,
                main_gain=inp.main_gain,
                arp=arp,
                memo=NOTE_MEMO,
                lead=inp.lead,
                auto=inp.auto,
            ),
            fx_units=inp.main_fx,
            own_parts=("synth",),
        )
    empty_main = dc_replace(arr, cells=[], drums=[])
    for i, lay in enumerate(inp.layers if synth else ()):
        # a following layer plays the main lane's notes: resolved here, so
        # its stem key carries the notes it plays (a main-grid edit moves it)
        lay = follow_layer(lay, arr, inp.breaks, arp, inp.lead)
        job(
            "layer",
            # a layer reads the main velocity lane when it carries none of its
            # own (`arrange.layer_arrangement`); its own velocities, slides and
            # arp ride in `lay` itself
            {"layer": lay, "velocities": arr.velocities, **_timing(arr)},
            lambda lay=lay: build_song(
                inp.patch, empty_main, None, layers=[lay], memo=NOTE_MEMO
            ),
            fx_units=lay.get("fx"),
            own_parts=("synth",),
            name=f"layer{i + 2}",
        )
    if "drums" in parts and arr.drums:
        job(
            "drums",
            {
                "drums": arr.drums,
                "drum_params": arr.drum_params,
                "drum_vel": arr.drum_vel,
                "drum_mix": arr.drum_mix,
                "drum_humanize": arr.drum_humanize,
                "drum_samples": arr.drum_samples,
                "sample_hashes": _sample_hashes(arr, inp.clip_root),
                **_drum_timing(arr),
                # per-hit data and the rows' own swing: absent when unset,
                # so every stem key from before them holds
                **({"drum_hits": arr.drum_hits} if arr.drum_hits else {}),
                **({"row_swing": arr.drum_row_swing} if arr.drum_row_swing else {}),
            },
            lambda: build_song(
                inp.patch, dc_replace(arr, cells=[]), None, clip_root=inp.clip_root
            ),
            fx_units=inp.drums_fx,
            own_parts=("drums",),
        )
    if "clips" in parts and getattr(arr, "clips", None):
        job(
            "clips",
            {
                "clips": arr.clips,
                "hashes": _lane_hashes(arr, inp.clip_root),
                "bpm": arr.bpm,
                **_bar_clock(arr),
            },
            # the bar cap is these parts' loop: the length both stem keys
            # carry (`n` / `own`), so the key holds what the placement reads
            lambda: build_clips_song(arr, inp.clip_root, parts),
            own_parts=parts,  # clips place inside the full mixed loop
        )
    return jobs


def _render_job(job: _StemJob) -> np.ndarray:
    """A stem's un-tiled render with its FX chain baked in: its own loop
    plus whatever rings on past it."""
    # normalize=0: stems stay raw so the final sum normalizes once
    # FX first, THEN wrap. Running the chain on the already-tiled stem cut
    # every reverb and delay tail off at the loop point; the un-tiled render
    # still carries its natural tail, so it can be folded onto the start
    # where a looping player expects to hear it.
    return stem_fx(job.build().render(normalize=0), job.fx_units, job.chain_arr)


def stem_fx(rendered: np.ndarray, fx_units, arr: Arrangement) -> np.ndarray:
    """A stem's own FX chain over its un-tiled render — the one way a lane's
    FX are applied, to a loop stem (`_render_job`) and to a pre-rendered
    key (`lane_note`) alike."""
    if not fx_units:
        return rendered
    return fx_chain(
        to_stereo(np.asarray(rendered, dtype=np.float64)), build_chain(fx_units, arr)
    )


def lane_note(
    patch: Patch,
    pitch,
    span: float,
    gate: float | None,
    velocity: float,
    gain: float,
    fx_units,
    arr: Arrangement,
) -> np.ndarray:
    """One note of a layer through that layer's FX: exactly what the loop's
    layer stem holds for a lone note at its start (`arrange.build_song`
    places `Synth.note(pitch, span, velocity, gate)` at the lane's gain;
    `Song.render` pads the tail; `stem_fx` runs the chain) — so a key's
    pre-rendered layer (`/note/{pitch}.wav?layer=`) is the loop's layer,
    FX and all (`tests/studio/test_live_prerender.py`). `patch` has its
    synced rates resolved (`at_bpm`); `arr` is the chain's arrangement."""
    song = Song(bpm=arr.bpm)
    song.track("lane")
    song.play("lane", Synth(patch).note(pitch, span, velocity, gate=gate), 0.0, gain)
    return stem_fx(song.render(normalize=0), fx_units, arr)


def _check_finite(
    audio: np.ndarray,
    why: str = "The render produced NaN/inf — patch settings too extreme.",
) -> None:
    """Raise `why` when any sample is NaN or inf (`core.scan`: one native
    pass, where `np.all(np.isfinite(...))` made a boolean copy of the
    buffer first)."""
    if not scan(audio).finite:
        raise RenderError(why)


_MASTER_NAN = "Master FX produced NaN/inf — back an effect off."


def _each(fn: Callable, items: list) -> list:
    """`[fn(x) for x in items]`, the items on the stem pool when there is
    more than one. The native kernels release the GIL, so stems that miss
    the cache now render on several cores; results keep their order and the
    first exception propagates as it would from the loop."""
    if len(items) < 2:
        return [fn(x) for x in items]
    return list(_stem_pool().map(fn, items))


@lru_cache(maxsize=1)
def _stem_pool() -> ThreadPoolExecutor:
    # A handful, not every core: /note warm-ups (NOTE_RENDERS) and breed
    # probes share the machine, and a loop has only a few stems anyway.
    # Half the cores on a big machine: a song's sections render side by
    # side (`section_pool`) and each brings its own stems.
    cores = os.cpu_count() or 2
    return ThreadPoolExecutor(
        max_workers=max(min(6, cores), cores // 2), thread_name_prefix="stem"
    )


_INFLIGHT: dict[str, threading.Lock] = {}
_INFLIGHT_GUARD = threading.Lock()


@contextmanager
def _one_at_a_time(key: str):
    """Hold `key`'s lock: whoever comes for the same key meanwhile waits.
    The lock is dropped from the table as its holder leaves; a waiter that
    already has it goes next, and finds the cache filled."""
    with _INFLIGHT_GUARD:
        lock = _INFLIGHT.setdefault(key, threading.Lock())
    with lock:
        try:
            yield
        finally:
            with _INFLIGHT_GUARD:
                _INFLIGHT.pop(key, None)


@lru_cache(maxsize=1)
def section_pool() -> ThreadPoolExecutor:
    """A song's uncached sections, side by side (`SongRender._warm`). A
    third pool on purpose: a section waits on its stems, a stem on its
    notes. Few workers: each holds a section's buffers (two cycles of
    float64 stereo a stem, and the master chain's)."""
    return ThreadPoolExecutor(
        max_workers=max(1, min(6, (os.cpu_count() or 2) // 4)),
        thread_name_prefix="section",
    )


def render_stems(
    inp: RenderInputs, *, cache: StemCache | None = None
) -> list[tuple[str, np.ndarray]]:
    """One loop-length stereo stem per active section, cache-aware, paired
    with the name that identifies it (`_stem_jobs`). The final mix is the
    sum of the stems, so a cached section costs nothing to reuse. Each
    section's user FX chain is baked into its stem (and its cache key).

    A section renders at its *own* loop length (the synth grid's bars or
    the drum machine's), its tail wrapped to the start, then tiles up to
    the mixed loop so whole repeats land on the bar. Stems missing from the
    cache render in parallel (`_each`).
    """
    arr, parts = inp.arr, inp.parts
    n = n_samples(loop_seconds(arr, parts), SR)
    jobs = list(_stem_jobs(inp))
    keys = [_stem_key(job.kind, {**job.cache_payload(), "n": n}) for job in jobs]
    stems = [cache.get(key) if cache is not None else None for key in keys]

    def render(i: int) -> np.ndarray:
        own = n_samples(loop_seconds(arr, jobs[i].own_parts), SR)
        stem = _wrap_tile(_render_job(jobs[i]), own, n)
        _check_finite(stem)
        return stem

    missing = [i for i, stem in enumerate(stems) if stem is None]
    for i, stem in zip(missing, _each(render, missing), strict=True):
        stems[i] = stem
        if cache is not None:
            cache.put(keys[i], stem)
    return [(job.name, stem) for job, stem in zip(jobs, stems, strict=True)]


def render_stems_raw(
    inp: RenderInputs, *, cache: StemCache | None = None
) -> list[tuple[str, int, np.ndarray]]:
    """`render_stems` before `_wrap_tile`: per stem `(name, own, raw)`.
    `own` is its loop length in samples and `raw` its render cut to
    `2 * own`: one loop and the one loop-length of tail that `_wrap_tile`
    folds (it drops anything later, and so does a section). The takes
    (clips) stem keeps only `own`: a take is cut at the section's end and
    hands nothing on. Cached in the same StemCache under `raw-<kind>:`
    keys."""
    arr = inp.arr
    jobs = list(_stem_jobs(inp))
    owns = [n_samples(loop_seconds(arr, job.own_parts), SR) for job in jobs]
    keys = [
        _stem_key(f"raw-{job.kind}", {**job.cache_payload(), "own": own})
        for job, own in zip(jobs, owns, strict=True)
    ]
    raws = [cache.get(key) if cache is not None else None for key in keys]

    def render(i: int) -> np.ndarray:
        keep = owns[i] if jobs[i].kind == "clips" else 2 * owns[i]
        raw = _pad_trim(_render_job(jobs[i]), keep).copy()
        _check_finite(raw)
        return raw

    missing = [i for i, raw in enumerate(raws) if raw is None]
    for i, raw in zip(missing, _each(render, missing), strict=True):
        raws[i] = raw
        if cache is not None:
            cache.put(keys[i], raw)
    return [
        (job.name, own, raw) for job, own, raw in zip(jobs, owns, raws, strict=True)
    ]


# --- song sections ------------------------------------------------------------
#
# A section renders ONE cycle (song_model.section_arrangement). What it
# hands on is its TAIL: the samples its stems ring on past the cycle's end,
# summed, never longer than one cycle.
# - A stem looping at its own length inside the cycle (a non-flat section):
#   its last repeat's raw render beyond its own loop, `raw[own:2 * own]`.
#   That is exactly what `_wrap_tile` folds onto a loop's start.
# - A flat cycle's stem (FX run once over the whole stitched cycle): its
#   render past the cycle's end, `stem[n:2 * n]`.
# - A take is cut at the section's end: the clips stem hands on
#   nothing.
# Folding a tail onto a cycle's head gives:
# - `prev_tail=None`: its own tail, i.e. the section as a loop (what every
#   pass after the first hears, and ⟳ Loop section);
# - the previous section's tail: the section's first pass in the song;
# - an empty array: nothing before it (the song's first section).


@dataclass(frozen=True)
class SectionAudio:
    """One cycle of a section with nothing folded into its head (`base`,
    `(cycle_samples, 2)`) and what rings on past its end (`tail`, at most
    as long as `base`).

    `out` is set only by a section with an FX-parameter lane (`fxauto`):
    `tail` is then the ring into the section's own next pass, where the
    lane has started again, and `out` the ring into another section, where
    the unit rests at the rack's value. `handoff` is the one to fold into
    the next section either way.

    `levels` is what the render saw of each stem while it had the array:
    {stem name: (peak, RMS)} over the cycle, before the master chain and
    at gain 1 (`_level`; a return's stem is `return:<id>`). The mixer's
    meters and Balance read it; empty when nothing recorded any."""

    base: np.ndarray
    tail: np.ndarray
    out: np.ndarray | None = None
    levels: dict = field(default_factory=dict, compare=False)

    @property
    def handoff(self) -> np.ndarray:
        """What rings on into the NEXT section (`tail` unless `out`)."""
        return self.tail if self.out is None else self.out

    @property
    def nbytes(self) -> int:  # StemCache's byte budget
        return (
            self.base.nbytes
            + self.tail.nbytes
            + (0 if self.out is None else self.out.nbytes)
        )


@dataclass(frozen=True)
class _LaneStem:
    """A cycle stem under FX-parameter lanes, as the StemCache holds it:
    the stem (`_cycle_stems`' `(n..2n, 2)`, its ring replaying the lanes)
    and the other ring, `out` (from the cycle's end; the lanes at rest)."""

    stem: np.ndarray
    out: np.ndarray

    @property
    def nbytes(self) -> int:
        return self.stem.nbytes + self.out.nbytes


def section_windows(parts) -> list[tuple[int, int]]:
    """The bar ranges a section's cycle renders in: the whole cycle when
    it fits MAX_LOOP_SECONDS, else consecutive chunks of as many whole bars
    as fit (at least one: a bar is at most 6 s, at the 40 bpm floor)."""
    cycle = parts.cycle_bars
    if cycle * parts.bar_seconds <= MAX_LOOP_SECONDS:
        return [(0, cycle)]
    step = max(1, int(MAX_LOOP_SECONDS // parts.bar_seconds))
    return [(b, min(b + step, cycle)) for b in range(0, cycle, step)]


def section_audio(
    parts, *, stem_cache: StemCache | None = None, clip_root=None
) -> SectionAudio:
    """Render `parts` (song_model.SectionParts) as one un-folded cycle and
    its tail.

    - Not flat and short enough: the section's own stems, each looping at
      its own length inside the cycle exactly as `_wrap_tile` tiles a loop
      (each repeat hears the one before it; the last one rings on).
    - Otherwise (flat, or past MAX_LOOP_SECONDS): `_cycle_stems`, which
      runs each stem's FX once over its whole stitched cycle.
    """
    n = parts.clock.samples(parts.cycle_bars)
    buf = np.zeros((2 * n, 2))
    end = n
    out = None  # the ring into another section, when FX lanes make it differ
    levels: dict = {}
    try:
        if _own_stems(parts):
            inp = parts.inputs(clip_root=clip_root)
            for name, own, raw in render_stems_raw(inp, cache=stem_cache):
                core, tail = raw[:own], raw[own:]
                reps = max(1, -(-n // own))
                body = np.concatenate([core] * reps)[:n].copy()
                for r in range(1, reps):  # each repeat hears the one before
                    seg = body[r * own : r * own + len(tail)]
                    seg += tail[: len(seg)]
                buf[:n] += body
                buf[n : n + len(tail)] += tail  # the last repeat rings on
                end = max(end, n + len(tail))
                levels[name] = _level(body)
        else:
            stems = _cycle_stems(parts, n, stem_cache, clip_root)
            for name, stem, _ring in stems:
                buf[: len(stem)] += stem
                end = max(end, len(stem))
                levels[name] = _level(stem[:n])
            if any(ring is not None for _name, _stem, ring in stems):
                out = np.zeros((end - n, 2))
                for _name, stem, ring in stems:
                    ring = stem[n:] if ring is None else ring
                    out[: len(ring)] += ring
    except (ValueError, TypeError) as err:
        raise RenderError(f"Curve couldn't be applied — {err}") from err
    if parts.under:
        # this pass's slice of the song's long audio (`sm.pass_cuts`): dry,
        # read by range from the source store, with no tail of its own
        from . import longaudio

        buf[:n] += longaudio.under(clip_root, parts.under, n)
    return SectionAudio(
        base=buf[:n].copy(), tail=buf[n:end].copy(), out=out, levels=levels
    )


def _own_stems(parts) -> bool:
    """Whether a section renders as its own stems, each looping at its own
    length inside the cycle (`render_stems_raw`): not flat, short enough
    for one piece, and nothing of the mixer's on it — a pan or a send is
    applied to a finished cycle stem, so such a section takes
    `_cycle_stems` (which flattens it), and so does one whose audio track
    has a rack or a lane (`parts.takes`: its takes' own stem)."""
    return (
        not parts.flat
        and len(section_windows(parts)) == 1
        and not parts.mix
        and not parts.takes
    )


def _level(x: np.ndarray) -> tuple[float, float]:
    """(peak, RMS) of a stem's cycle, both channels: one `core.scan`."""
    if x.size == 0:
        return 0.0, 0.0
    s = scan(x)
    return s.peak, math.sqrt(s.sumsq / x.size)


def section_master_audio(
    parts, master_fx, *, stem_cache: StemCache | None = None, clip_root=None
) -> SectionAudio:
    """`section_audio` through the master chain: ONE run, from silence,
    over the cycle followed by its tail (padded to one cycle of master
    tail), split back at the cycle's end. The song folds these master tails
    exactly as it folds the stems' tails, so a song (export and playback
    alike) is these renders stitched, and master FX are part of each
    section's render. A sidechain or pump reads the section's own kicks.
    With no master chain this is `section_audio` itself, unchanged.

    Accepted caveats, each a consequence of running the chain per section:
    - chorus, flanger, phaser and other LFO-driven units restart their LFO
      at every section's start;
    - a master tail longer than one cycle is cut at one cycle (a looped
      section folds only that much of its own tail back in)."""
    audio = section_audio(parts, stem_cache=stem_cache, clip_root=clip_root)
    units = clean_chain(list(master_fx))
    if not units:
        return audio
    levels = audio.levels
    n = len(audio.base)
    x = _pad_trim(np.concatenate([audio.base, audio.tail]), 2 * n)
    chain = build_chain(units, parts.arr, span=2 * n / SR)
    try:
        y = _pad_trim(np.asarray(fx_chain(x, chain)), 2 * n)
    except (ValueError, TypeError) as err:
        raise RenderError(f"Master FX couldn't be applied — {err}") from err
    _check_finite(y, _MASTER_NAN)
    out = None
    if audio.out is not None:
        # the ring into another section differs (FX lanes): the same run
        # over the cycle and THAT ring. The chain is causal, so its cycle
        # is the one above
        x2 = _pad_trim(np.concatenate([audio.base, audio.out]), 2 * n)
        try:
            out = _pad_trim(np.asarray(fx_chain(x2, chain)), 2 * n)[n:]
        except (ValueError, TypeError) as err:
            raise RenderError(f"Master FX couldn't be applied — {err}") from err
        _check_finite(out, _MASTER_NAN)
        out = _trim_ring(out)
    return SectionAudio(
        base=y[:n].copy(), tail=_trim_ring(y[n:]), out=out, levels=levels
    )


def _trim_ring(tail: np.ndarray) -> np.ndarray:
    """A tail cut after its last sounding sample (a copy)."""
    ring = np.flatnonzero(np.any(tail != 0.0, axis=1))
    return tail[: int(ring[-1]) + 1 if len(ring) else 0].copy()


def _events_end(song) -> int:
    """Where a Song's last event ends, in samples (Song.render's `end`)."""
    return max(
        (start + len(a) for t in song.tracks.values() for start, a, _g in t.events),
        default=0,
    )


def _cycle_stems(parts, n: int, cache: StemCache | None, clip_root) -> list:
    """A flat cycle's stems, each `(name, stem, out)`. `stem` is `(n..2n, 2)`:
    the cycle, then its tail (as long as it rings, at most one cycle).
    `out` is None, except for a stem under FX-parameter lanes
    (`parts.fx_auto`): then `stem`'s tail is the ring into the section's
    own next pass and `out` the ring into another section (`fxauto`).

    The notes render DRY in `section_windows` chunks (each <= the cap).
    Each chunk's whole dry render, note tails included, is added in at its
    offset, which gives back the one-piece dry render. Each stem's FX chain
    then runs ONCE over its whole stitched cycle. Reverb, drive and
    compression are not linear in their input, so running them per chunk
    stepped their level at every seam. The takes render once over the whole
    cycle, cut at its end.

    A section's gain lanes (`parts.gains`) scale the finished stems, after
    the cache: a fader past the FX, so moving one re-renders no note and no
    chain, and chunking can't reach it. Its FX lanes are in the chain, so
    they are in the stem's key (absent without one).

    The mixer's pan and sends (`parts.mix`, `parts.returns`) come last, on
    the finished stem past its fader: the drums' own level (a synth's is
    in its stem, before its chain), the pan (`effects.pan`, which keeps a
    stereo image), then each return's input, the sum of its senders' stems
    times their send levels, through the return's chain fully wet
    (`_return_stems`) — one more stem, named `return:<id>`. Neither is in
    a track's stem key (a pan or a send re-renders no note and no chain);
    the return's own key holds its inputs' keys, levels, pans and lanes."""
    from . import fxauto

    flat = parts.flatten()
    clock = flat.clock
    groups: dict[str, list[tuple[int, _StemJob]]] = {}
    for b0, b1 in section_windows(parts):
        win = flat.window(b0, b1).inputs(clip_root=clip_root, parts=("synth", "drums"))
        off = clock.bar_sample(b0)
        for job in _stem_jobs(win):
            groups.setdefault(job.name, []).append((off, job))
    groups_ = list(groups.values())
    # each stem's FX lanes that find their unit in its chain ([] for most)
    moving = [
        [
            [target, points]
            for target, points in flat.fx_auto.get(name, ())
            if fxauto.resolve(pieces[-1][1].fx_units, [[target, points]])
        ]
        for name, pieces in groups.items()
    ]
    keys = [
        _stem_key(
            f"cycle-{pieces[0][1].kind}",
            {
                "pieces": [[o, j.payload] for o, j in pieces],
                "fx": pieces[-1][1].fx_units,
                "n": n,
                **_chain_clock(pieces[-1][1].fx_units, flat.arr),
                **({"fx_auto": lanes} if lanes else {}),
            },
        )
        for pieces, lanes in zip(groups_, moving, strict=True)
    ]
    stems = [cache.get(key) if cache is not None else None for key in keys]

    def render(i: int) -> np.ndarray | _LaneStem:
        pieces = groups_[i]
        fx_units = pieces[-1][1].fx_units
        songs = [(o, j.build()) for o, j in pieces]
        end = max(o + _events_end(song) for o, song in songs)
        # the length Song.render gives the one-piece render
        dry = np.zeros((end + songs[0][1].beats_to_samples(4.0) + SR, 2))
        for o, song in songs:
            r = song.render(normalize=0)
            dry[o : o + len(r)] += r
        ring = None
        if moving[i]:
            # FX lanes: the chain with those parameters moving, and the
            # two rings a moving parameter leaves (`fxauto.render`)
            dry, ring = fxauto.render(
                dry, fx_units, flat.arr, moving[i], n, span=len(dry) / SR
            )
        elif fx_units:
            # span: the clocked triggers (pump, sidechain) must reach the
            # buffer's end, or a pump stops partway through a long cycle
            dry = fx_chain(dry, build_chain(fx_units, flat.arr, span=len(dry) / SR))
        # kept to its real ring (last note + 4 beats + 1 s, as
        # Song.render sized it), at least the cycle, at most one cycle
        # of tail: zero padding to 2n doubled a short stem's footprint
        keep = max(n, min(len(dry), 2 * n))
        stem = _pad_trim(dry, keep).copy()
        _check_finite(stem)
        if ring is None:
            return stem
        ring = _pad_trim(ring, keep)[n:].copy()
        _check_finite(ring)
        return _LaneStem(stem, ring)

    def once(i: int) -> np.ndarray | _LaneStem:
        """`render(i)`, one render a key at a time: a song's sections
        render side by side (`section_pool`) and two of them often hold
        the same stem (the same pattern over the same bars) — the second
        waits for the first's and takes it from the cache."""
        if cache is None:
            return render(i)
        with _one_at_a_time(keys[i]):
            held = cache.peek(keys[i])
            if held is None:
                held = render(i)
                cache.put(keys[i], held)
            return held

    missing = [i for i, stem in enumerate(stems) if stem is None]
    for i, stem in zip(missing, _each(once, missing), strict=True):
        stems[i] = stem
    out, feeds = [], []
    for name, key, stem in zip(groups, keys, stems, strict=True):
        points = flat.gains.get(name)
        ring = None
        if isinstance(stem, _LaneStem):
            # the level lane holds its last value over either ring
            ring = stem.out
            if points:
                whole = np.concatenate([stem.stem[:n], ring])
                ring = _lane_gain(whole, points, n)[n:]
            stem = stem.stem
        stem = _lane_gain(stem, points, n)
        own = flat.mix.get(name) or {}
        if "gain" in own:  # the drums' fader (`song_model._with_mix`)
            stem = stem * own["gain"]
            ring = None if ring is None else ring * own["gain"]
        if own.get("pan"):
            stem = fx_pan(stem, own["pan"])
            ring = None if ring is None else fx_pan(ring, own["pan"])
        out.append((name, stem, ring))
        if own.get("sends"):
            feeds.append((name, key, stem, ring))
    if feeds:
        out += _return_stems(flat, feeds, n, cache)
    if flat.arr.clips:
        takes = flat.inputs(clip_root=clip_root, parts=("clips",))
        out += [
            (name, raw, None)
            for name, _own, raw in render_stems_raw(takes, cache=cache)
        ]
    if flat.takes:
        out += _take_stems(flat, n, cache, clip_root)
    return out


def _take_stems(flat, n: int, cache: StemCache | None, clip_root) -> list:
    """The stems of the audio tracks with a rack or a lane in this section
    (`flat.takes`, `song_model._own_takes`), `(name, stem, out)` like any
    other. A track's takes are placed as the shared takes stem places them
    (`build_clips_song`) and cut at the cycle's end; then its chain runs
    ONCE over the cycle and one cycle more, so a reverb or a delay rings on
    into the next pass like any stem's — with its FX-parameter lanes
    (`flat.fx_auto`, `fxauto.render`: two rings, as a synth stem's). Its
    level lane (`flat.gains`) scales the finished stem, past the cache.
    Keyed by the takes' content, the chain and its clock, the cycle and
    the lanes the chain plays."""
    from . import fxauto

    span = 2 * n / SR
    out = []
    for name, clips, units in flat.takes:
        fx_units = clean_chain(list(units))
        lanes = [
            [target, points]
            for target, points in flat.fx_auto.get(name, ())
            if fxauto.resolve(fx_units, [[target, points]])
        ]
        arr = dc_replace(flat.arr, clips=list(clips))
        key = _stem_key(
            "cycle-takes",
            {
                "clips": list(clips),
                "hashes": _lane_hashes(arr, clip_root) if clip_root else [],
                "bpm": arr.bpm,
                **_bar_clock(arr),
                "fx": fx_units,
                "n": n,
                **_chain_clock(fx_units, arr),
                **({"fx_auto": lanes} if lanes else {}),
            },
        )
        got = cache.get(key) if cache is not None else None
        if got is None:
            raw = build_clips_song(arr, clip_root, ("clips",)).render(normalize=0)
            dry = np.zeros((2 * n, 2))
            dry[:n] = _pad_trim(to_stereo(np.asarray(raw, dtype=np.float64)), n)
            ring = None
            if lanes:
                dry, ring = fxauto.render(dry, fx_units, arr, lanes, n, span=span)
            elif fx_units:
                dry = fx_chain(dry, build_chain(fx_units, arr, span=span))
            y = _pad_trim(np.asarray(dry), 2 * n)
            if ring is not None:
                ring = _pad_trim(np.asarray(ring), 2 * n)[n:]
            # kept to what rings (nothing, dry): a take hands on no more
            keep = max(
                len(_trim_ring(y[n:])), len(_trim_ring(ring)) if ring is not None else 0
            )
            got = y[: n + keep].copy()
            _check_finite(got)
            if ring is not None:
                ring = ring[:keep].copy()
                _check_finite(ring)
                got = _LaneStem(got, ring)
            if cache is not None:
                cache.put(key, got)
        stem, ring = (got.stem, got.out) if isinstance(got, _LaneStem) else (got, None)
        points = flat.gains.get(name)
        if points and ring is not None:
            ring = _lane_gain(np.concatenate([stem[:n], ring]), points, n)[n:]
        out.append((name, _lane_gain(stem, points, n), ring))
    return out


def _return_stems(flat, feeds: list, n: int, cache: StemCache | None) -> list:
    """The returns' stems, `(name, stem, out)` like any other. `feeds` are
    the sending stems as they sit in the mix (past FX, level lane and pan):
    `(name, stem key, stem, ring)`.

    A return's input is each sender's whole stem — the cycle AND its tail —
    times its send level, summed; its chain runs once, from silence, over
    two cycles (so the room can ring one cycle past the end, the most any
    stem hands on). Folding its tail onto the next pass is then exact for
    a linear return: what a sender's tail would have sent there is already
    in it. A sender under an FX lane has two rings, and so has its return.
    Cached under the senders' stem keys and everything applied since."""
    out = []
    for ret in flat.returns:
        rid = ret["id"]
        ins = [
            (name, key, stem, ring, flat.mix[name]["sends"][rid])
            for name, key, stem, ring in feeds
            if flat.mix[name]["sends"].get(rid)
        ]
        if not ins:
            continue
        units = clean_chain(list(ret["fx"]))
        key = _stem_key(
            "return",
            {
                "in": [
                    [k, level, _past_fader(flat, name)]
                    for name, k, _stem, _ring, level in ins
                ],
                "fx": units,
                "gain": ret["gain"],
                "n": n,
                **_chain_clock(units, flat.arr),
            },
        )
        got = cache.get(key) if cache is not None else None
        if got is None:
            got = _render_return(ins, units, ret["gain"], flat.arr, n)
            if cache is not None:
                cache.put(key, got)
        if isinstance(got, _LaneStem):
            out.append((f"return:{rid}", got.stem, got.out))
        else:
            out.append((f"return:{rid}", got, None))
    return out


def _past_fader(flat, name: str) -> dict:
    """What is applied to a cached stem before a return reads it: its
    level lane, the drums' fader, its pan."""
    own = {k: v for k, v in flat.mix[name].items() if k != "sends"}
    points = flat.gains.get(name)
    return own | ({"lane": points} if points else {})


def _render_return(ins: list, units: list, gain: float, arr, n: int):
    def run(rings: bool) -> np.ndarray:
        x = np.zeros((2 * n, 2))
        for _name, _key, stem, ring, level in ins:
            if rings and ring is not None:
                stem = np.concatenate([stem[:n], ring])
            m = min(len(stem), 2 * n)
            x[:m] += level * stem[:m]
        y = _pad_trim(_wet(x, units, arr, 2 * n / SR), 2 * n) * gain
        _check_finite(y)
        return y

    y = run(False)
    tail = _trim_ring(y[n:])
    if all(ring is None for _name, _key, _stem, ring, _level in ins):
        return np.concatenate([y[:n], tail])
    # a sender under an FX lane: the ring into another section differs.
    # The chain is causal, so the cycle is the one above
    other = _trim_ring(run(True)[n:])
    keep = max(len(tail), len(other))
    return _LaneStem(
        np.concatenate([y[:n], _pad_trim(tail, keep)]), _pad_trim(other, keep).copy()
    )


def _wet(x: np.ndarray, units: list, arr, span: float) -> np.ndarray:
    """`x` through a return's chain, wet only. Every unit is at `mix` 1
    (`mixer.wet_chain`), which for most is the wet path alone; the delay's
    wet path also carries its input undelayed (`effects._delay`: the line
    is y = x + feedback * y[-d]), so that direct term — the same unit with
    no feedback, fed only what it passes straight through — is taken out:
    a send to a delay return adds echoes, never level to the dry track."""
    from .. import effects

    for u in units:
        y = to_stereo(np.asarray(fx_chain(x, build_chain([u], arr, span=span))))
        if u["kind"] == "delay":
            _name, kw = unit_args(u, arr, span)
            direct = to_stereo(x).copy()
            if kw["ping_pong"]:
                direct[:, 1] = 0.0  # the right line starts one echo late
            y = y - effects._delay(direct, **{**kw, "feedback": 0.0, "mix": 1.0})
        x = y
    return x


def _lane_gain(stem: np.ndarray, points, n: int) -> np.ndarray:
    """A cycle stem `(n..2n, 2)` under its track's gain lane: the lane
    across the cycle's `n` samples, its last value held over the tail (what
    rings into the next pass leaves at the level the section ended on).
    No lane: the stem itself, untouched. A copy otherwise — the cache's
    stem is shared."""
    env = lane_array(points, GAIN, n) if points else None
    if env is None:
        return stem
    out = stem.copy()
    out[:n] *= env[:, None]
    out[n:] *= env[-1]
    return out


def fold_tail(base: np.ndarray, tail: np.ndarray) -> np.ndarray:
    """`base` with `tail` added onto its head. A tail longer than the
    cycle is cut at the cycle's end, as `_wrap_tile` cuts one past a loop."""
    out = base.copy()
    m = min(len(tail), len(out))
    out[:m] += tail[:m]
    return out


def render_section(
    parts,
    *,
    prev_tail: np.ndarray | None,
    stem_cache: StemCache | None = None,
    clip_root=None,
    gain: float = 1.0,
) -> np.ndarray:
    """One cycle of a section, raw (no master FX and no normalization: both
    are song-wide), `(cycle_samples, 2)`, with `prev_tail` folded into its
    head and scaled by `gain`. None folds the section's own tail (a loop);
    an empty array folds nothing (the song's first section). Raises
    RenderError, a non-finite result included."""
    audio = section_audio(parts, stem_cache=stem_cache, clip_root=clip_root)
    if prev_tail is None:
        tail = audio.tail
    else:
        tail = to_stereo(np.asarray(prev_tail, dtype=np.float64))
    out = fold_tail(audio.base, tail)
    if gain != 1.0:
        out = out * gain
    _check_finite(out)
    return out


def section_key(parts, clip_root) -> str:
    """What a section's un-folded cycle depends on: its `RenderInputs` key
    (patch, grid, layers, kit, FX, takes by content) plus the cycle's shape
    and the chunks it renders in."""
    blob = json.dumps(
        {
            "inp": parts.inputs(clip_root=clip_root).key(),
            "cycle": parts.cycle_bars,
            "flat": parts.flat,
            "windows": section_windows(parts),
            **_cycle_fixes(parts),
            # level lanes scale the finished stems, so no stem key holds
            # them (field lanes ride `inp`); absent without one
            **({"gains": parts.gains} if parts.gains else {}),
            # FX-parameter lanes ride the stems' chains, not `inp`
            **({"fx_auto": parts.fx_auto} if parts.fx_auto else {}),
            # the mixer: pans and sends sit on the finished stems, the
            # returns are stems of their own; absent without any
            **({"mix": parts.mix} if parts.mix else {}),
            **({"returns": list(parts.returns)} if parts.returns else {}),
            # an audio track's own takes stem (its rack, its lanes): the
            # takes by content and the chain; absent without one
            **_takes_key(parts, clip_root),
            # the long audio this pass plays, by content address and by
            # what is on disk (`longaudio.keyed`); absent without any
            **_under_key(parts, clip_root),
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha1(blob).hexdigest()[:16]


def _takes_key(parts, clip_root) -> dict:
    if not parts.takes:
        return {}
    from .clips import clip_hash

    def hashes(clips) -> list:  # by content, as `RenderInputs.key` keys takes
        if clip_root is None:
            return []
        return [clip_hash(clip_root, str(c.get("clip", ""))) for c in clips]

    return {
        "takes": [
            [name, list(clips), hashes(clips), clean_chain(list(units))]
            for name, clips, units in parts.takes
        ]
    }


def _under_key(parts, clip_root) -> dict:
    if not parts.under:
        return {}
    from . import longaudio

    return {"under": longaudio.keyed(clip_root, parts.under)}


def _cycle_fixes(parts) -> dict:
    """Salts for the sections two `_cycle_stems` fixes re-render, so no
    browser keeps the old audio (`/preview/{key}.wav` is served immutable;
    `layer_gain_v` above is the same move). Empty — the key unchanged —
    for every section they did not touch:

    - `leads`: a chunk whose first note slides now glides from the note
      before the chunk (`SectionParts.window`);
    - `pump_span_v`: a lane's pump whose 256 triggers ran out before the
      end of the cycle and its tail now keeps ducking (`build_chain`'s
      `span`)."""
    windows = section_windows(parts)
    if _own_stems(parts):
        return {}  # the section's own stems: neither fix reaches it
    out: dict = {}
    flat = parts.flatten()
    wins = [flat.window(b0, b1) for b0, b1 in windows]
    leads = [[w.lead, *(lay.get("lead") for lay in w.layers)] for w in wins]
    if any(any(row) for row in leads):
        out["leads"] = leads
    span = 2 * parts.cycle_bars * parts.bar_seconds
    chains = [parts.main_fx, parts.drums_fx, *(lay.get("fx") for lay in parts.layers)]
    beat = 60.0 / parts.arr.bpm
    if any(
        u["kind"] == "pump" and 256 * max(0.25, u["params"]["beats"]) * beat < span
        for fx in chains
        for u in clean_chain(list(fx or ()))
    ):
        out["pump_span_v"] = 2
    return out


@dataclass(frozen=True)
class SectionJob:
    """A keyed render that is not a plain loop, in the form
    `StudioService._ensure` takes: its cache key, and how to make the
    RenderResult from the studio's StemCache. A song section (`section:`
    keys) or a post-processed loop (`StudioService.render(post=...)`)."""

    section_key: str
    make: Callable[[StemCache | None], RenderResult]

    def key(self) -> str:
        return self.section_key


def normalize_master(
    stereo: np.ndarray, target: float = 0.89, ceiling: float = 0.97
) -> np.ndarray:
    """Bring the finished loop to a consistent level.

    Scaling by the single loudest sample handed the volume of the whole mix
    to its worst transient: a resonant filter tick or one stacked attack
    made everything around it 20 dB quieter, and two sounds A/B'd in the
    library came back 8.7 dB apart despite both being "normalized". The
    reference is the body of the mix (a high percentile) and the limiter
    catches whatever pokes above it — the same order a mastering chain uses.
    """
    return _normalize(stereo, target, ceiling)[0]


def _normalize(
    stereo: np.ndarray, target: float = 0.89, ceiling: float = 0.97
) -> tuple[np.ndarray, float]:
    """`normalize_master`, and the gain it applied in dB (0 for silence)."""
    if stereo.size == 0:
        return stereo, 0.0
    s = scan(stereo, 99.5)  # the frames' peaks' 99.5th percentile, exactly
    ref = (s.body or s.peak) if s.finite else math.nan
    gain_db = 0.0
    if ref > 1e-9:
        stereo = stereo * (target / ref)
        gain_db = 20 * math.log10(target / ref)
    # The gain envelope has to be periodic too. Limiting a single pass in
    # isolation starts from full gain and ducks the loop's opening attack,
    # so the loop came back quieter at its start than at its end — a step in
    # level at the seam, on every repeat. Limit two passes, keep the second.
    doubled = fx_limit(ceiling=ceiling)(np.vstack([stereo, stereo]))
    return np.ascontiguousarray(doubled[len(stereo) :]), gain_db


def render_audio(
    inp: RenderInputs, *, stem_cache: StemCache | None = None
) -> np.ndarray:
    """Render the loop to a padded/trimmed/limited stereo array — everything
    render_loop does up to (but not including) analysis + WAV encoding.
    Raises RenderError on cap-exceeded, curve-apply failure, or NaN/inf."""
    return _render(inp, stem_cache)[0]


def _render(
    inp: RenderInputs, stem_cache: StemCache | None
) -> tuple[np.ndarray, float]:
    """`render_audio`, and the gain its normalization applied in dB."""
    seconds = loop_seconds(inp.arr, inp.parts)
    if seconds > LOOP_CAP_SECONDS:
        raise RenderError(
            f"Loop is {seconds:.1f}s — the cap is {LOOP_CAP_SECONDS:.0f}s. "
            "Raise the bpm or shorten the loop."
        )
    try:
        stems = render_stems(inp, cache=stem_cache)
    except (ValueError, TypeError) as err:
        raise RenderError(f"Curve couldn't be applied — {err}") from err

    n = int(round(seconds * SR))
    if not stems:
        return np.zeros((n, 2)), 0.0
    stereo = stems[0][1].copy()
    for _, stem in stems[1:]:
        stereo += stem

    master = clean_chain(list(inp.master_fx))
    if master:
        stereo = _pad_trim(stereo, n)
        # The loop is periodic, so run two repeats through the chain and keep
        # the second: by then the tail from the previous repeat has arrived
        # and the chain is in the steady state a looping player actually
        # hears. Processing one repeat and truncating chopped the tail dead
        # at the seam instead.
        doubled = fx_chain(
            np.vstack([stereo, stereo]), build_chain(master, chain_arr(inp))
        )
        stereo = _pad_trim(np.asarray(doubled), 2 * n)[n : 2 * n]
        _check_finite(stereo, _MASTER_NAN)

    # one normalization of the summed loop (stems render raw, so this
    # happens exactly once)
    return _normalize(stereo)


def render_loop(
    inp: RenderInputs, *, stem_cache: StemCache | None = None
) -> RenderResult:
    stereo, gain_db = _render(inp, stem_cache)
    result = result_of(inp.key(), stereo)
    result.norm_gain_db = round(gain_db, 3)
    result.passes = int(inp.passes)
    return result


def preview_url(result: RenderResult) -> str:
    """Where the player fetches `result`: `/preview/{key}.wav`, and for a
    loop of several passes (`RenderResult.passes`) `?passes=N`, which
    audio.js reads to keep its playheads on one pass of the grid."""
    url = f"/preview/{result.key}.wav"
    return f"{url}?passes={result.passes}" if result.passes > 1 else url


def result_of(key: str, stereo: np.ndarray, *, analyse: bool = True) -> RenderResult:
    """A finished render as a RenderResult: its WAV and the meter's
    analysis (peak, rms, sub share, centroid). The meter needs two spectral
    numbers, so it takes `analysis.meter`'s one FFT rather than the full
    `extract` (16-19 ms a loop, 87 ms a section, for seven features
    nothing read). `analyse=False` (song sections: the player never shows
    a meter for one) leaves the analysis empty."""
    analysis: dict = {}
    if analyse:
        sub, centroid = meter(stereo)
        s = scan(stereo)
        rms = math.sqrt(s.sumsq / stereo.size) if stereo.size else 0.0
        rms = rms or 1e-9
        analysis = {
            "peak_db": round(20 * np.log10(s.peak or 1e-9), 1),
            "rms_db": round(20 * np.log10(rms), 1),
            "sub_pct": round(100 * sub, 1),
            "centroid": round(centroid),
        }
    return RenderResult(key=key, wav=_to_wav_bytes(stereo), analysis=analysis)


class RenderCache(LRU[RenderResult]):
    """Finished loops by render key; a RenderResult carries its own key.
    `/preview/{key}.wav` serves only what is still in here, so this cache
    holds previews alone — key and pad one-shots live in `NoteCache`, or a
    burst of keys evicted the loop the browser was about to fetch (404).
    Bounded in bytes: a 16-bar loop is ~3 MB and a song section up to ~2 MB,
    so 256 small edits fit while long renders stay under ~192 MB.
    """

    def __init__(self, max_entries: int = 256, max_bytes: int = 192 << 20):
        super().__init__(max_entries, max_bytes, sizeof=lambda r: len(r.wav))

    def put(self, result: RenderResult) -> None:  # type: ignore[override]
        super().put(result.key, result)


class NoteCache(RenderCache):
    """Live key and pad one-shots (`/note/`, `/breed/note/`, drum hits),
    served by their own routes. A held key's `?hold=1` render is ~1.3 MB."""

    def __init__(self, max_entries: int = 256, max_bytes: int = 64 << 20):
        super().__init__(max_entries, max_bytes)
