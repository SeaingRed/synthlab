"""Piano-roll grid -> Song. 3a subset: one synth track per pitch row,
pitch_rows() rows, 1 bar x steps_per_bar steps (4/8/16/32, default 4).
Consecutive checked cells in a row merge into one held note.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import zlib
from collections.abc import Mapping
from dataclasses import replace
from functools import partial

import numpy as np

from .. import _native, core
from .. import drums as dr
from .. import sequencer as _seq
from ..core import SR, n_samples
from ..kits import fetch as _kit_fetch
from ..kits import play as _kit_play
from ..kits import sfz as _kit_sfz
from ..sampler import Strikes
from ..sequencer import (
    Song,
    cut_line,
    midi,
    mono_lines,
    name_of,
    note_span,
    render_each,
)
from ..synth import Patch, Synth, at_bpm
from . import arppattern, limits, notefeel
from . import meter as _meter
from .curves import CURVABLE, curve_array, lane_array
from .drumlab import specs_of
from .lru import LRU
from .state import (
    ACOUSTIC_ROWS,
    ALL_DRUM_ROWS,
    ARTICULATIONS,
    DRUM_ROWS,
    ROOTS,
    SCALE_CHOICES,
    Arrangement,
    drum_row_sounds,
    pitch_rows,
)

# the grid arp's modes (`Arrangement.arp_mode`): "pattern" plays the lane's
# `arp_pattern` (arppattern.py), the rest the arp they always were
ARP_MODES = ("off", "up", "down", "updown", "pattern")

# -- note memo -------------------------------------------------------------
# A loop edit moves one note; the stem it lives in re-renders whole. Every
# other note is the same render as last time (Synth.note is deterministic:
# noise is seeded), so the studio's loop renders keep the notes themselves,
# by content, and a one-cell edit costs about one note (a 4-note pluck loop:
# ~120 ms -> ~35 ms). Opt-in (`build_song(memo=...)`): the engine's own
# callers and the parity corpus never see it.


def _engine_sig() -> tuple:
    """What a note's samples depend on beyond its arguments: the functions
    in the render path (a test's spy, a swapped noise source or note pool,
    the native kernels switched off) — any swap misses the memo rather
    than serving a render the swapped engine would not make."""
    return (
        id(Synth.note),
        id(Synth.chord),
        id(core.noise),
        id(_seq.render_each),
        id(_native.kernel),
        _native.ENABLED,
        _native.OFF,
    )


class NoteMemo:
    """Rendered notes by (patch content, engine, arguments), byte-bounded.
    Buffers are shared read-only: `Track.render` only ever reads them."""

    def __init__(self, max_bytes: int = 96 * 1024 * 1024, max_entries: int = 4096):
        self._bounds = (max_entries, max_bytes)
        self.clear()

    def clear(self) -> None:
        """Forget every note. The key is the patch's content, and a sampler
        patch names its audio by id: when a sample the store lacked arrives
        (a project bundle imported after its project.json), the notes
        rendered without it — silence — are no longer what the patch plays."""
        entries, max_bytes = self._bounds
        self.lru: LRU = LRU(entries, max_bytes=max_bytes, sizeof=lambda a: a.nbytes)

    def synth(self, patch: Patch) -> MemoSynth:
        blob = json.dumps(patch.to_json(), sort_keys=True, default=repr).encode()
        return MemoSynth(Synth(patch), self, hashlib.sha1(blob).hexdigest())

    def get_or(self, key: str, render) -> np.ndarray:
        hit = self.lru.get(key)
        if hit is not None:
            return hit
        audio = np.asarray(render())
        audio.setflags(write=False)
        self.lru.put(key, audio)
        return audio


class MemoSynth:
    """A `Synth` for `Song.notes`: `note`/`chord` through a `NoteMemo`."""

    def __init__(self, synth: Synth, memo: NoteMemo, patch_key: str):
        self.synth, self.patch, self.memo = synth, synth.patch, memo
        self.key = patch_key

    def _key(self, kind: str, args: tuple) -> str:
        return repr((self.key, _engine_sig(), kind, args))

    def note(
        self, pitch, duration, velocity=1.0, gate=None, pan=0.0, from_pitch=None, rr=0
    ):
        args = (pitch, duration, velocity, gate, pan, from_pitch)
        args += (rr,) if rr else ()  # a zone map's round-robin strike
        return self.memo.get_or(
            self._key("note", args),
            lambda: self.synth.note(
                pitch,
                duration,
                velocity,
                gate=gate,
                pan=pan,
                from_pitch=from_pitch,
                rr=rr,
            ),
        )

    def chord(self, *args, **kwargs):
        key = self._key("chord", (repr(args), repr(sorted(kwargs.items()))))
        return self.memo.get_or(key, lambda: self.synth.chord(*args, **kwargs))


_CELL_PREFIX = "cell."
_VEL_PREFIX = "vel."
_DRUM_PREFIX = "drum."
_SLIDE_PREFIX = "slide."
_CHANCE_PREFIX = "nchance."  # the note grid's chance lane (`note_chance`)


def arrangement_from_form(
    form: Mapping[str, str],
    bar_choices: tuple[int, ...] = limits.LOOP_BAR_CHOICES,
    meter: str = "4/4",
) -> Arrangement:
    """A grid form as an Arrangement. `bar_choices`: the bars the grid
    may have — the loop's by default (`limits.LOOP_BAR_CHOICES`, up to 16);
    the Song drawer passes a pool pattern's (`song_model.bar_choices`).
    Only those whose bars x steps fit `MAX_GRID_STEPS` are taken: a value
    between two choices, or past what fits, is the longest choice under
    it. `meter`: the grid's bar (a form never carries it: the loop's own,
    or the pattern's), whose step counts `steps` must be one of
    (`meter.step_choices`)."""
    meter = _meter.clean(meter)
    step_choices = _meter.step_choices(meter)
    try:
        bpm = float(form.get("bpm", 124.0))
    except ValueError:
        bpm = 124.0
    bpm = min(limits.BPM_RANGE[1], max(limits.BPM_RANGE[0], bpm))

    try:
        bars = int(form.get("bars", 1))
    except ValueError:
        bars = 1
    bars = min(max(bar_choices), max(min(bar_choices), bars))

    try:
        steps_per_bar = int(form.get("steps", step_choices[0]))
    except ValueError:
        steps_per_bar = step_choices[0]
    if steps_per_bar not in step_choices:
        steps_per_bar = step_choices[0]
    fits = [b for b in bar_choices if b * steps_per_bar <= limits.MAX_GRID_STEPS]
    fits = fits or [min(bar_choices)]
    bars = max((b for b in fits if b <= bars), default=min(fits))

    root = str(form.get("root", "A1"))
    if root not in ROOTS:
        root = "A1"

    scale_name = str(form.get("scale_name", "pentatonic_minor"))
    if scale_name not in SCALE_CHOICES:
        scale_name = "pentatonic_minor"

    try:
        swing = float(form.get("swing", 0.0))
    except ValueError:
        swing = 0.0
    swing = min(0.6, max(0.0, swing))

    total_steps = bars * steps_per_bar
    valid_pitches = set(pitch_rows(Arrangement(root=root, scale_name=scale_name)))

    cells: list[list] = []
    cuts: list[list] = []
    for key in form:
        if not key.startswith(_CELL_PREFIX):
            continue
        parts = key.split(".")
        if len(parts) != 3 or parts[1] not in valid_pitches:
            continue
        try:
            step = int(parts[2])
        except ValueError:
            continue
        if 0 <= step < total_steps:
            cells.append([parts[1], step])
            # a cell posted as "cut" starts a new note (Arrangement.cuts)
            if form[key] == "cut":
                cuts.append([parts[1], step])
    cells.sort(key=lambda c: (c[0], c[1]))
    cuts.sort(key=lambda c: (c[0], c[1]))

    velocities: dict[str, float] = {}
    for key, value in form.items():
        if not key.startswith(_VEL_PREFIX):
            continue
        # the lane's own key, never the field's spelling: "vel.03" and
        # "vel. 3 " were stored as "03" and " 3 ", which nothing reads
        step_str = limits.step_key(key[len(_VEL_PREFIX) :])
        try:
            vel = float(value)
        except ValueError:
            continue
        if step_str is not None and int(step_str) < total_steps:
            velocities[step_str] = min(1.0, max(0.0, vel))

    # a step's chance: only a value under 1 says something (1 always plays)
    note_chance: dict[str, float] = {}
    for key, value in form.items():
        if not key.startswith(_CHANCE_PREFIX):
            continue
        step_str = limits.step_key(key[len(_CHANCE_PREFIX) :])
        try:
            p = float(value)
        except ValueError:
            continue
        if step_str is not None and int(step_str) < total_steps and math.isfinite(p):
            p = min(1.0, max(0.0, p))
            if p < 1.0:
                note_chance[step_str] = p

    drums: list[list] = []
    for key in form:
        if not key.startswith(_DRUM_PREFIX):
            continue
        parts = key.split(".")
        if len(parts) != 3 or parts[1] not in ALL_DRUM_ROWS:
            continue
        try:
            step = int(parts[2])
        except ValueError:
            continue
        if 0 <= step < total_steps:
            drums.append([parts[1], step])
    drums.sort(key=lambda d: (d[0], d[1]))

    slides: list[int] = []
    for key in form:
        if not key.startswith(_SLIDE_PREFIX):
            continue
        try:
            step = int(key[len(_SLIDE_PREFIX) :])
        except ValueError:
            continue
        if 0 <= step < total_steps:
            slides.append(step)
    slides.sort()

    arp_mode = str(form.get("arp_mode", "off"))
    if arp_mode not in ARP_MODES:
        arp_mode = "off"
    try:
        arp_octaves = int(form.get("arp_octaves", 1))
    except ValueError:
        arp_octaves = 1
    arp_octaves = min(3, max(1, arp_octaves))
    try:
        strum = float(form.get("strum", 0.0))
    except ValueError:
        strum = 0.0
    lo, hi = limits.STRUM_MS_RANGE
    strum = min(hi, max(lo, strum)) if math.isfinite(strum) else 0.0

    return Arrangement(
        meter=meter,
        strum=strum,
        slides=slides,
        arp_mode=arp_mode,
        arp_octaves=arp_octaves,
        bpm=bpm,
        bars=bars,
        steps_per_bar=steps_per_bar,
        root=root,
        scale_name=scale_name,
        cells=cells,
        cuts=cuts,
        velocities=velocities,
        note_chance=note_chance,
        drums=drums,
        swing=swing,
    )


def loop_bars(arr: Arrangement, parts: tuple[str, ...] = ("synth", "drums")) -> int:
    """The synth grid and the drum machine keep their own bar counts; a
    mix of both loops over the smallest length that holds whole repeats
    of each (lcm), so neither pattern is ever cut mid-phrase."""
    lengths = []
    if "synth" in parts:
        lengths.append(max(1, int(arr.bars)))
    if "drums" in parts:
        lengths.append(max(1, int(getattr(arr, "drum_bars", arr.bars))))
    total = 1
    for n in lengths or [max(1, int(arr.bars))]:
        total = total * n // math.gcd(total, n)
    return total


def loop_seconds(
    arr: Arrangement, parts: tuple[str, ...] = ("synth", "drums")
) -> float:
    # A bar is its meter's beats regardless of steps_per_bar (grid
    # resolution doesn't change tempo/duration — see build_song's
    # step_beats below): 4 in 4/4, `bars * 4.0 * (60.0 / bpm)` as ever
    return _meter.arr_clock(arr).seconds(loop_bars(arr, parts))


def bar_beats(arr) -> float:
    """A bar of `arr`'s meter in beats: 4.0 in 4/4 (`meter.py`)."""
    return _meter.bar_beats(getattr(arr, "meter", _meter.DEFAULT))


class TempoSong(Song):
    """A `Song` whose beats follow a ramp (`meter.Clock`): a section that
    speeds up or slows down across its bars. Every placement goes through
    `beats_to_samples`, so a note or a hit lands where its beat sounds;
    the schedulers here take a note's length in seconds from the clock
    (`_secs`). Only ever built for an arrangement with a `ramp` — every
    other render is the plain `Song` it always was."""

    def __init__(self, clock: _meter.Clock):
        super().__init__(bpm=clock.bpm)
        self.clock = clock

    def beats_to_samples(self, beats: float) -> int:
        return int(round(self.clock.at(beats) * self.sr))


def _song(arr) -> Song:
    """The Song a render schedules into: plain at one tempo."""
    if getattr(arr, "ramp", None):
        return TempoSong(_meter.arr_clock(arr))
    return Song(bpm=arr.bpm)


def _secs(song: Song, start: float, beats: float) -> float:
    """`beats` beats from beat `start`, in seconds: under a ramp the
    clock's; at one tempo `beats * song.beat_seconds`, as ever."""
    if isinstance(song, TempoSong):
        return song.clock.span(start, beats)
    return beats * song.beat_seconds


def _row_breaks(breaks, cuts, pitch) -> set[int]:
    """Where one row's held notes attack again: the lane's `breaks` (a
    tiled section's repeat starts) and the row's own cuts (two notes side
    by side, `Arrangement.cuts`)."""
    return set(breaks) | {int(s) for p, s in cuts or () if p == pitch}


def _merge_runs(steps: list[int], breaks=()) -> list[tuple[int, int]]:
    """[0,1,2,8] -> [(0, 3), (8, 1)]: (start_step, run_length). A step in
    `breaks` always starts a new run: a song section tiles a pattern over
    its cycle, and the pattern's first step must attack again on every
    repeat, as it does when the pattern loops on its own."""
    runs: list[tuple[int, int]] = []
    for s in sorted(steps):
        if runs and s == runs[-1][0] + runs[-1][1] and s not in breaks:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((s, 1))
    return runs


# -- a step's chance (the note grid's chance lane) ---------------------------
def note_roll(pcs, step: int, at: int = 0) -> float:
    """The roll of a step's chance, 0 <= x < 1: `hit_roll` (drum chance's
    hash, never an rng stream) of the step in its pattern, a seed of the
    pitch classes that start on it and `at`, where this pass of the
    pattern starts (in steps: 0 for its first pass, `r * period` for its
    r-th repeat, plus the section's pass — `chance_passes`). So a chord
    is one event (it plays or rests whole), a following layer an octave
    away rolls what its main lane rolls, editing another step or the
    chance itself leaves the roll alone (a note that plays at 0.5 plays
    at any higher chance), changing what starts on the step rolls it
    again — and every pass rolls afresh (deliberately: a 1-bar riff at
    50 % varies bar to bar). The first pass (`at` 0) rolls
    what the grid always rolled."""
    pcs = ",".join(str(p) for p in sorted({int(p) % 12 for p in pcs}))
    seed = f"notes:{pcs}" if not at else f"notes:{pcs}@{int(at)}"
    return hit_roll(zlib.crc32(seed.encode()), step)


def note_plays(chance: Mapping, step: int, pcs) -> bool:
    """Do the notes that start on `step` (their pitch classes `pcs`) play
    under the lane's `chance` table (`Arrangement.note_chance`)? A step
    with no chance of its own always plays. A value is the chance (rolled
    on `step`, its first pass) or, in a tiled cycle (`chance_tile`),
    `[chance, its step in the pattern, where its pass starts]`."""
    p = chance.get(str(step)) if chance else None
    if p is None:
        return True
    if isinstance(p, list | tuple):
        return note_roll(pcs, int(p[1]), int(p[2])) < float(p[0])
    return note_roll(pcs, step) < float(p)


def chance_tile(chance: Mapping, period: int, total: int, lo=0, hi=None) -> dict:
    """A lane's chance table looping every `period` steps out to `total`
    (from `lo` up to `hi`, as `song_model._tile` tiles its cells): each
    repeat's step carries `[chance, its step in the pattern, where its
    pass starts]`, so pass r rolls `note_roll(..., at=r * period)` — the
    pattern's first pass rolls what it rolls looping on its own, every
    later one afresh. A value already in that form keeps its own step and
    adds the repeat's offset."""
    hi = total if hi is None else min(hi, total)
    out: dict = {}
    for k, v in (chance or {}).items():
        s = int(k)
        if not 0 <= s < period:
            continue
        p, own, at = (
            (v[0], int(v[1]), int(v[2])) if isinstance(v, list | tuple) else (v, s, 0)
        )
        for r in range(-(-(hi - lo) // period)):
            t = lo + r * period + s
            if t < hi:
                out[str(t)] = [float(p), own, at + r * period]
    return out


def chance_passes(chance: Mapping, at: int) -> dict:
    """A chance table `at` steps further on (a later pass of a section's
    cycle: `song_model.repass`): every value in `chance_tile`'s form, its
    pass start moved by `at`. 0: the table itself."""
    if not chance or not at:
        return chance
    return {
        k: (
            [float(v[0]), int(v[1]), int(v[2]) + int(at)]
            if isinstance(v, list | tuple)
            else [float(v), int(k), int(at)]
        )
        for k, v in chance.items()
    }


def _chanced(by_pitch: dict, arr, breaks=()) -> dict:
    """`by_pitch` (a lane's playing steps) less the notes whose step's
    chance rests them (`note_plays`). A note is a run of its row's steps
    (split at the row's cuts and `breaks`), whose chance is its START
    step's; dropping a whole run leaves every other run as it was, so
    this is a filter in front of every scheduler — the per-row path and
    `_lane_timeline` alike, and the MIDI export through `note_schedule`.
    Under an arp every step plays the cycle's next pitch: `_lane_timeline`
    rolls those step by step instead. No chance: `by_pitch` itself."""
    chance = getattr(arr, "note_chance", None)
    if not chance or arr.arp_mode != "off":
        return by_pitch
    runs = {
        p: _merge_runs(steps, _row_breaks(breaks, arr.cuts, p))
        for p, steps in by_pitch.items()
    }
    starts: dict[int, set[int]] = {}
    for p, rs in runs.items():
        for start, _n in rs:
            starts.setdefault(start, set()).add(midi(p))
    out: dict = {}
    for p, rs in runs.items():
        kept = [
            s
            for start, n in rs
            if note_plays(chance, start, starts[start])
            for s in range(start, start + n)
        ]
        if kept:
            out[p] = kept
    return out


# How much of its length a grid note is held before its release: every
# scheduler below gates at this (it is `Song.notes`' default gate too), and
# the MIDI export ends its note-offs there.
NOTE_GATE = 0.9


def _curve_slice(full_arr: np.ndarray, start_sample: int, note_n: int) -> np.ndarray:
    """The note's own segment of a loop-length curve array, held at the
    edge value past the array's end (a note's release tail can run past
    the loop boundary)."""
    if start_sample >= len(full_arr):
        return np.full(note_n, full_arr[-1])
    seg = full_arr[start_sample : start_sample + note_n]
    if len(seg) < note_n:
        seg = np.pad(seg, (0, note_n - len(seg)), mode="edge")
    return seg


def _auto_arrays(auto: dict | None, arr=None) -> dict[str, np.ndarray]:
    """A song section's automation lanes for one synth lane
    (`song_model.SectionParts.auto`: {curves, at, n}) as the per-sample
    arrays `_curved_notes` / `_timeline_notes` slice, like a loop's curves.

    Each array runs from this render's first sample (`at`, a chunk's place
    in its cycle) to the END OF THE CYCLE (`n`), not of the chunk: a note
    ringing past its chunk keeps following the lane, exactly as it does in
    the cycle rendered in one piece (`_curve_slice` holds the last value
    only past the cycle's own end). Under a ramp (`arr.ramp`: a ramped
    section is one cycle, so the ramp starts at the cycle's first bar) the
    lane is read in bars, as it is drawn (`meter.warp`)."""
    if not auto:
        return {}
    n = max(1, int(auto["n"]))
    at = min(max(0, int(auto.get("at", 0))), n - 1)
    ramp = list(getattr(arr, "ramp", None) or ())
    out: dict[str, np.ndarray] = {}
    for param, points in auto["curves"].items():
        if param not in CURVABLE:
            continue
        vals = lane_array(points, param, n, at)
        if vals is not None:
            if ramp:
                cycle = _meter.Clock(arr.bpm, bar_beats(arr), (*ramp[:2], 0.0))
                vals = _meter.warp(vals, cycle, ramp[1], at)
            out[param] = vals
    return out


def _curved_notes(
    song: Song,
    track: str,
    patch: Patch,
    events: list,
    curve_arrays: dict[str, np.ndarray],
    gain: float = 1.0,
) -> None:
    """`Song.notes`, reimplemented so each note gets its own patch —
    param values sliced from the (loop-length) curve array at that note's
    absolute position — instead of one shared `Synth`. Mirrors
    `Song.notes`'s defaults (gate=0.9, velocity=0.9, gain=1.0) and its
    `note_span` buffer length exactly, so scheduling matches — a flat
    `dur * 1.15` here chopped every release the moment a curve was drawn.
    """
    t = 0.0
    prev = None
    jobs, starts = [], []
    strike = Strikes(patch)  # a zone map's round robin (0 for any other)
    for pitch, length, *rest in events:
        vel = rest[0] if rest else 0.9
        if pitch not in (None, "-", "."):
            dur = _secs(song, t, length)
            span = note_span(dur, patch)
            start_sample = song.beats_to_samples(t)
            note_n = n_samples(span, SR)
            tweaks = {
                param: _curve_slice(full_arr, start_sample, note_n)
                for param, full_arr in curve_arrays.items()
            }
            note_synth = Synth(patch.tweak(**tweaks) if tweaks else patch)
            jobs.append(
                partial(
                    note_synth.note,
                    pitch,
                    span,
                    vel,
                    gate=dur * NOTE_GATE,
                    from_pitch=prev,
                    rr=strike(pitch),
                )
            )
            starts.append(t)
            prev = pitch
        t += length
    # every note has its own patch (its slice of the curves): all distinct,
    # all independent — render them together (`render_each`), then place
    for audio, at in zip(render_each(jobs), starts, strict=True):
        song.play(track, audio, at, gain)


def _timeline_notes(
    song: Song,
    track: str,
    patch: Patch,
    timeline: list[tuple],
    curve_arrays: dict[str, np.ndarray],
    gain: float = 1.0,
    lead: str | None = None,
    ordered: bool = False,
) -> None:
    """Schedule (start_beat, dur_beats, pitch, vel, slide) tuples one note
    at a time, in time order, so a slide note can glide from whatever
    sounded before it — across rows, 303-style. Mirrors _curved_notes'
    `note_span` buffer and 0.9 gate. `lead`: what sounded before the first
    note (a section chunk's predecessor, `build_song`); a loop has none.
    `ordered`: the timeline is already in the grid's order and is taken as
    it stands — a strummed lane's (`_strummed`), whose slides glide from
    the note the grid puts before them, not from wherever the strum moved
    a chord's notes to."""
    if patch.voice_mode != "poly":  # one voice: its lines
        _mono_timeline(song, track, patch, timeline, curve_arrays, gain, lead, ordered)
        return
    prev = lead
    ramped = isinstance(song, TempoSong)
    # No drawn curves => one shared patch, so notes agreeing on
    # (pitch, length, velocity, glide-from) are sample-identical. Render once.
    # Two passes: collect the distinct notes, render them together
    # (`render_each`), then place every event.
    jobs: dict = {}
    placed: list = []
    strike = Strikes(patch)  # a zone map's round robin (0 for any other)
    for start, dur_beats, pitch, vel, slide in (
        timeline if ordered else sorted(timeline)
    ):
        dur = _secs(song, start, dur_beats)
        span = note_span(dur, patch)
        note_n = n_samples(span, SR)
        start_sample = song.beats_to_samples(start)
        tweaks = {
            param: _curve_slice(full_arr, start_sample, note_n)
            for param, full_arr in curve_arrays.items()
        }
        p = patch.tweak(**tweaks) if tweaks else patch
        if slide and prev is not None and p.glide <= 0:
            p = p.tweak(glide=0.06)  # a slide must audibly glide
        from_pitch = prev if slide else None
        rr = strike(pitch)
        # a curved note is its own render: key it by its place in the list
        key = (
            ("at", len(placed))
            if tweaks
            # under a ramp a beat's length depends on where it is
            else (pitch, dur if ramped else dur_beats, vel, from_pitch, p.glide, rr)
        )
        if key not in jobs:
            jobs[key] = partial(
                Synth(p).note,
                pitch,
                span,
                vel,
                gate=dur * NOTE_GATE,
                from_pitch=from_pitch,
                rr=rr,
            )
        placed.append((key, start))
        prev = pitch
    audio = dict(zip(jobs, render_each(list(jobs.values())), strict=True))
    for key, start in placed:
        song.play(track, audio[key], start, gain)


def _mono_timeline(
    song: Song,
    track: str,
    patch: Patch,
    timeline: list[tuple],
    curve_arrays: dict[str, np.ndarray],
    gain: float,
    lead: str | None,
    ordered: bool,
) -> None:
    """`_timeline_notes` for a one-voice patch (`voice_mode` mono / legato):
    the lines `sequencer.mono_lines` makes of the notes, each one render
    (its slice of the curves from where it starts), cut where the next
    takes the voice — what the live keys play (voice-worklet.js)."""
    items = timeline if ordered else sorted(timeline)
    if isinstance(song, TempoSong):
        c = song.clock
        events = [
            (c.at(s), c.span(s, d), c.span(s, d) * NOTE_GATE, pitch, bool(slide))
            for s, d, pitch, _vel, slide in items
        ]
    else:
        bs = song.beat_seconds
        events = [
            (start * bs, dur * bs, dur * bs * NOTE_GATE, pitch, bool(slide))
            for start, dur, pitch, _vel, slide in items
        ]
    lines = mono_lines(events, patch, lead)
    jobs: dict = {}
    placed: list = []
    strike = Strikes(patch)  # each line one strike of its first key
    for k, ln in enumerate(lines):
        start, _dur, pitch, vel, _slide = items[ln.first]
        rr = strike(pitch)
        span = note_span(ln.dur, patch)
        n = n_samples(span, SR)
        at = song.beats_to_samples(start)
        tweaks = {
            param: _curve_slice(full, at, n) for param, full in curve_arrays.items()
        }
        p = patch.tweak(**tweaks) if tweaks else patch
        if ln.slide and p.glide <= 0:
            p = p.tweak(glide=0.06)  # a slide must audibly glide
        # the cut in samples between the two starts, as the keys hear it
        cut = None
        if ln.cut is not None:
            nxt = items[lines[k + 1].first][0]
            cut = (song.beats_to_samples(nxt) - at) / SR
        key = (
            ("at", k)
            if tweaks
            else (
                pitch,
                ln.dur,
                vel,
                ln.from_pitch,
                p.glide,
                ln.gate,
                tuple(ln.path),
                cut,
                rr,
            )
        )
        if key not in jobs:
            jobs[key] = (
                partial(
                    Synth(p).note,
                    pitch,
                    span,
                    vel,
                    gate=ln.gate,
                    from_pitch=ln.from_pitch,
                    path=ln.path,
                    rr=rr,
                ),
                cut,
            )
        placed.append((key, start))
    keys = list(jobs)
    audio = render_each([jobs[k][0] for k in keys])
    out = {k: cut_line(a, jobs[k][1]) for k, a in zip(keys, audio, strict=True)}
    for key, start in placed:
        song.play(track, out[key], start, gain)


def _arp_sequence(pitches: list[str], mode: str, octaves: int) -> list[str]:
    """Cycle order for the arp: base pitches sorted low->high, octave
    copies appended, then shaped by mode."""
    base = sorted(set(pitches), key=midi)
    seq = [name_of(midi(p) + 12 * o) for o in range(octaves) for p in base]
    if mode == "down":
        return seq[::-1]
    if mode == "updown" and len(seq) > 2:
        return seq + seq[-2:0:-1]
    return seq


def _row_events(
    steps: list[int], arr: Arrangement, step_beats: float, breaks=()
) -> list:
    """Per-row (pitch-less) event shapes shared by the main lane and extra
    layers: (None, rest_beats) / (PITCH_SENTINEL, dur_beats, velocity) with
    swing applied to odd start steps. Caller substitutes the pitch."""
    events: list = []
    cursor = 0
    for start, run in _merge_runs(steps, breaks):
        shift = arr.swing * step_beats if start % 2 == 1 else 0.0
        rest_beats = (start - cursor) * step_beats + shift
        if rest_beats > 0:
            events.append((None, rest_beats))
        duration_beats = run * step_beats
        if shift:
            duration_beats = max(duration_beats - shift, step_beats / 8)
        events.append((start, duration_beats, arr.velocities.get(str(start), 0.9)))
        cursor = start + run
    return events


def _uses_timeline(
    by_pitch: dict, arr: Arrangement, arp: tuple, patch: Patch | None = None
) -> bool:
    """Arp, slides, a strum or a one-voice patch (`voice_mode`) need
    cross-row, time-ordered scheduling (`_lane_timeline`); anything else
    plays per row."""
    mono = patch is not None and patch.voice_mode != "poly"
    # a ramp: every note one at a time, its length from where it starts
    ramp = bool(getattr(arr, "ramp", None))
    return bool(by_pitch or arp) and (
        arr.arp_mode != "off"
        or bool(arr.slides)
        or bool(arr.strum)
        or bool(nudge_of(arr))
        or bool(notefeel.of(arr))
        or mono
        or ramp
    )


def nudge_of(arr) -> dict:
    """The lane's timing inside a step (`Arrangement.note_nudge`: step ->
    a share of a step) as it plays: none under an arp, which plays one
    note a step on the grid (as the strum, `strum_beats`)."""
    if arr.arp_mode != "off":
        return {}
    return getattr(arr, "note_nudge", None) or {}


def strum_beats(arr: Arrangement) -> float:
    """`arr.strum` (ms between a chord's notes, signed) in beats at its
    tempo; 0 under an arp, which plays one note a step."""
    if not arr.strum or arr.arp_mode != "off":
        return 0.0
    return float(arr.strum) / 1000.0 * float(arr.bpm) / 60.0


def _strummed(timeline: list[tuple], gap: float, floor: float) -> list[tuple]:
    """`timeline` with every chord strummed: notes that
    start together are started `|gap|` beats apart, low to high (`gap` < 0:
    high to low). Each still ENDS where it did — it is shortened by what it
    waited, never under `floor` beats — so a strummed chord releases as one
    and nothing is pushed past its step's end. A note alone on its start is
    not moved.

    The list comes back in the order `_timeline_notes` would have played
    the unstrummed notes (sorted), which is the order a slide reads its
    predecessor in: a strum moves when a note starts, not which note the
    grid says came before it (`song_model._runs` says the same thing for a
    section's chunks)."""
    base = sorted(timeline)
    chords: dict[float, list[int]] = {}
    for i, ev in enumerate(base):
        chords.setdefault(ev[0], []).append(i)
    out = list(base)
    for start, members in chords.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda i: midi(base[i][2]), reverse=gap < 0)
        for k, i in enumerate(members):
            wait = k * abs(gap)
            _s, dur, *rest = base[i]
            out[i] = (start + wait, max(dur - wait, floor), *rest)
    return out


def _lane_timeline(
    by_pitch: dict[str, list[int]],
    arr: Arrangement,
    step_beats: float,
    breaks=(),
    arp: tuple = (),
) -> list[tuple]:
    """One lane's notes as (start_beat, dur_beats, pitch, vel, slide)
    tuples for `_timeline_notes`, read off `arr`'s own sequencing: its arp
    (`arp_mode`/`arp_octaves`: every step of the grid plays the next pitch
    of the cycle) or else its rows with `slides`; `velocities` and `swing`
    either way, and without an arp its `strum` (`_strummed`: the list is
    then in the grid's order, for `_timeline_notes(ordered=True)`). The
    main lane and a layer both come through here, so a
    layer plays its own arp and slides exactly as it would being edited.
    `arp` = (pitches, start_step, period): see `build_song`. A song block
    that does not fill its section gives five, (pitches, start_step,
    period, lo, hi): the arp plays only the cycle's steps [lo, hi) and
    counts its phase from `lo` (`song_model._flat`). The three-value form
    plays as it always did. A step's chance (`note_chance`) rests the
    notes its roll says (`note_plays`): under an arp, step by step."""
    timeline: list[tuple] = []
    chance = getattr(arr, "note_chance", None)
    if arr.arp_mode == "pattern":
        if arppattern.of(arr).get("held"):  # the keys held: a take's arp
            return _held_timeline(by_pitch, arr, step_beats, breaks)
        return _pattern_timeline(by_pitch, arr, step_beats, arp)
    if arr.arp_mode != "off":
        pitches = list(arp[0]) if arp else list(by_pitch)
        seq = _arp_sequence(pitches, arr.arp_mode, arr.arp_octaves)
        gate = tuple(arp[3:5]) if len(arp) >= 5 else None
        for s_i in range(arr.bars * arr.steps_per_bar):
            if gate and not gate[0] <= arp[1] + s_i < gate[1]:
                continue  # outside the block: silent
            shift = arr.swing * step_beats if s_i % 2 == 1 else 0.0
            dur = max(step_beats - shift, step_beats / 8)
            vel = arr.velocities.get(str(s_i), 0.9)
            phase = (arp[1] + s_i - (gate[0] if gate else 0)) % arp[2] if arp else s_i
            pitch = seq[phase % len(seq)]
            if chance and not note_plays(chance, s_i, (midi(pitch),)):
                continue
            timeline.append((s_i * step_beats + shift, dur, pitch, vel, False))
        return timeline
    by_pitch = _chanced(by_pitch, arr, breaks)
    slide_steps = set(arr.slides)
    nudge = nudge_of(arr)
    feel = notefeel.of(arr)
    loop_beats = arr.bars * arr.steps_per_bar * step_beats
    for pitch, steps in sorted(by_pitch.items()):
        for start, run in _merge_runs(steps, _row_breaks(breaks, arr.cuts, pitch)):
            vel = arr.velocities.get(str(start), 0.9)
            if feel and (f := feel.get(f"{pitch}.{start}")):
                # a note's own feel (notefeel.py): it starts and ends where
                # it was played — no swing, no step nudge, no gate — and
                # takes its share of the step's velocity. `dur` is its
                # sounding length over NOTE_GATE, so every reader of the
                # timeline (the gate, a mono line, the MIDI note-off) gets
                # that length back
                # (in beats to a billionth: so a take's own decimals come
                # back as the very floats it was posted in, and a gate on a
                # half-sample tie rounds as the take's would)
                o, e, share = (float(x) for x in f)
                at = round((start + o) * step_beats, 9)
                held = round(max(run + e - o, notefeel.MIN_STEPS) * step_beats, 9)
                dur = held / NOTE_GATE
                vel = min(1.0, vel * share)
            else:
                shift = arr.swing * step_beats if start % 2 == 1 else 0.0
                # timing inside a step: the start moves (after swing), the
                # end stays where it was (as a strum's), never under an eighth
                n = float(nudge.get(str(start), 0.0)) * step_beats if nudge else 0.0
                dur = max(run * step_beats - shift - n, step_beats / 8)
                at = start * step_beats + shift + n
            if at < 0.0:  # before the loop's first beat: its last (a circle)
                at += loop_beats
            timeline.append((at, dur, pitch, vel, start in slide_steps))
    if gap := strum_beats(arr):
        return _strummed(timeline, gap, step_beats / 8)
    return timeline


def played_order(by_pitch: dict[str, list[int]]) -> list[str]:
    """A lane's rows in the order they first play (ties low to high): the
    grid arp's "as played" chord (arppattern.cycle)."""
    return sorted(by_pitch, key=lambda p: (min(by_pitch[p]), midi(p)))


def _pattern_timeline(
    by_pitch: dict[str, list[int]], arr: Arrangement, step_beats: float, arp: tuple
) -> list[tuple]:
    """The grid arp in "pattern" mode (`arr.arp_pattern`, arppattern.py):
    `_lane_timeline`'s tuples. The chord it holds is the grid arp's — every
    row with a note, the whole loop (`arp[0]`, a section's, in the order
    they first play) — and the pattern walks it step by step: a step every
    `rate` beats, counted from each bar line (the meter's bar,
    `bar_beats`: a step past the bar's end starts the next bar's count, as
    the keys arp re-anchors at every bar line), the odd steps of a bar
    late by the loop's swing. `retrig` "note" starts the walk again at
    every pass of the loop's pattern (its chord is struck there: `arp[2]`
    steps a pass, from the block's start in a trimmed one), "bar" at
    every bar, "free" never. A section's chunk walks from the cycle's
    start (`arp[1]`: where the chunk sits) and keeps its own bars' notes,
    so the chunks rendered apart are the cycle rendered whole. A hit
    sounds `gate` of its step (`dur` is that over `NOTE_GATE`); its
    velocity is the grid's lane at the step it falls on times the step's
    `vel`; the grid's chance lane rolls there too, after the step's own."""
    pat = arppattern.of(arr)
    held = [midi(p) for p in (arp[0] if arp else played_order(by_pitch))]
    if not held:
        return []
    spb = int(arr.steps_per_bar)
    beats = bar_beats(arr)
    rate = arppattern.rate_beats(pat, step_beats)
    per_bar = arppattern.steps_in(beats, rate)
    start_bar, period, lo, hi = 0, max(1, arr.bars), 0, None
    if arp:
        start_bar, period = int(arp[1]) // spb, max(1, int(arp[2]) // spb)
        if len(arp) >= 5:  # a trimmed block: from its first bar, silent past it
            lo, hi = int(arp[3]) // spb, int(arp[4]) // spb
    key = arppattern.key_of(arr.root, arr.scale_name)
    chance = getattr(arr, "note_chance", None)
    run = arppattern.Run()
    out: list[tuple] = []
    for bar in range(lo, start_bar + arr.bars):
        if hi is not None and bar >= hi:
            break
        if pat["retrig"] == "bar" or (
            pat["retrig"] == "note" and (bar - lo) % period == 0
        ):
            run.restart()
        for j in range(per_bar):
            hits = arppattern.step(run, held, pat, key)
            if bar < start_bar or not hits:
                continue
            base = (bar - start_bar) * beats + j * rate
            shift = arr.swing * rate if j % 2 else 0.0
            g = int(base / step_beats + 1e-9)  # the grid step it falls on
            vel = arr.velocities.get(str(g), 0.9)
            for off, m, scale, gate in hits:
                if chance and not note_plays(chance, g, (m,)):
                    continue
                at = base + shift + off * rate
                dur = gate / NOTE_GATE * rate
                out.append((at, dur, name_of(m), vel * scale, False))
    return out


# the keys arp's clock (static/arp.js): a note struck up to this long after
# a step starts a run on it ("one struck up to 30 ms late sounds at once"),
# and a step's notes are read this long before it is due (LOOK, 60 ms
# ahead, by a pump every 20 ms: 40-60 ms, the middle taken)
HELD_LATE_S = 0.03
HELD_READ_S = 0.05


def held_notes(by_pitch: dict, arr: Arrangement, step_beats: float, breaks=()):
    """A lane's notes as the keys held them: (start, end, midi, velocity)
    in beats, sorted by start — each note's own feel where it has one (a
    take placed as played), else its run; a start a hair before the loop's
    first beat counts from it. The chord the "held" arp reads
    (`_held_timeline`)."""
    feel = getattr(arr, "note_feel", None) or {}
    out = []
    for pitch, steps in _chanced(by_pitch, arr, breaks).items():
        for start, run in _merge_runs(steps, _row_breaks(breaks, arr.cuts, pitch)):
            vel = arr.velocities.get(str(start), 0.9)
            f = feel.get(f"{pitch}.{start}")
            if f:
                o, e, share = (float(x) for x in f)
                at = (start + o) * step_beats
                end = at + max(run + e - o, notefeel.MIN_STEPS) * step_beats
                vel = min(1.0, vel * share)
            else:
                at, end = start * step_beats, (start + run) * step_beats
            out.append((max(0.0, at), end, midi(pitch), vel))
    return sorted(out)


def _held_timeline(
    by_pitch: dict[str, list[int]], arr: Arrangement, step_beats: float, breaks=()
) -> list[tuple]:
    """The grid arp in "pattern" mode with a HELD pattern (`arp_pattern
    ["held"]`: a take recorded as "the keys I held", loop-and-perform.md,
    "The arp pattern"): the walk the keys arp made over the keys as they
    were held — `_lane_timeline`'s tuples. Not the grid's chord (every row,
    the whole loop): at each of the pattern's steps (every `rate` beats
    from each bar line, the odd ones late by the pattern's own `swing`, the
    keys arp's) the chord is the notes held when the keys arp read it
    (`HELD_READ_S` before the step; a run's first step, the first at or
    after its first press less `HELD_LATE_S`, reads the chord struck). A
    run is a stretch with a key down: the walk starts afresh with each
    ("free": a ▦ pattern goes on where the last run left it). Within one,
    "plain" (the keys arp's up / down / … modes) never starts again; a ▦
    pattern's `retrig` "note" starts again at the first step read after a
    note is struck (not one joining within `HELD_LATE_S` of where the walk
    started: a chord's notes), "bar" at every bar line (a run begun
    mid-bar walked to its place). A hit's velocity is the newest held
    note's times the step's `vel`; "played" walks the notes in the order
    they were struck."""
    pat = arppattern.of(arr)
    notes = held_notes(by_pitch, replace(arr, arp_mode="off"), step_beats, breaks)
    if not notes:
        return []
    bps = float(arr.bpm) / 60.0
    late, read = HELD_LATE_S * bps, HELD_READ_S * bps
    beats = bar_beats(arr)
    rate = arppattern.rate_beats(pat, step_beats)
    per_bar = arppattern.steps_in(beats, rate)
    swing = float(pat.get("swing", arr.swing))
    key = arppattern.key_of(arr.root, arr.scale_name)
    plain = pat["held"] == "plain"
    # the runs: the stretches some key is down
    runs: list[list[float]] = []
    for at, end, _m, _v in notes:
        if runs and at <= runs[-1][1]:
            runs[-1][1] = max(runs[-1][1], end)
        else:
            runs.append([at, end])
    grid = [
        (bar * beats + j * rate, j)
        for bar in range(max(1, int(arr.bars)))
        for j in range(per_bar)
    ]
    out: list[tuple] = []
    kept: arppattern.Run | None = None
    for r0, r1 in runs:
        steps = [(b, j) for b, j in grid if b >= r0 - late - 1e-9 and b - read < r1]
        if not steps:
            continue
        first = steps[0][0]
        if pat["retrig"] == "free" and kept is not None and not plain:
            run = kept
        else:
            run = arppattern.Run()
        kept, since, prev = run, first, None
        for n, (b, j) in enumerate(steps):
            t = max(r0, b - read) if n == 0 else b - read
            held = [x for x in notes if x[0] <= t + 1e-9 and x[1] > t]
            if not held:
                continue
            if n == 0 and pat["retrig"] == "bar" and not plain:
                run.restart()
                for _ in range(j):  # begun mid-bar: walked to its place
                    arppattern.step(run, [x[2] for x in held], pat, key)
            elif not plain and prev is not None:
                struck = any(prev < x[0] <= t and x[0] > since + late for x in held)
                if (pat["retrig"] == "note" and struck) or (
                    pat["retrig"] == "bar" and j == 0
                ):
                    run.restart()
                    since = b
            prev = t
            hits = arppattern.step(run, [x[2] for x in held], pat, key)
            vel = max(held, key=lambda x: x[0])[3]  # the newest note's
            shift = swing * rate if j % 2 else 0.0
            for off, m, scale, gate in hits:
                at = b + shift + off * rate
                out.append(
                    (at, gate / NOTE_GATE * rate, name_of(m), vel * scale, False)
                )
    return out


FOLLOW_OCTAVES = (-2, -1, 0, 1)  # how far a following layer may transpose
# what a bred Sound's layer was made as (studio/genome.py's role recipes);
# a label only — the render never reads it
LAYER_ROLES = ("sub", "pad", "pluck", "air")


def follow_octave(lay) -> int | None:
    """The octave a layer follows the main lane at, or None for a layer
    that plays its own cells. `{"follow": {"octave": -1}}`."""
    f = lay.get("follow") if isinstance(lay, dict) else None
    if not isinstance(f, dict):
        return None
    try:
        octave = int(f.get("octave", 0))
    except (TypeError, ValueError):
        return 0
    return min(FOLLOW_OCTAVES[-1], max(FOLLOW_OCTAVES[0], octave))


def _shift(pitch: str, semis: int) -> str | None:
    m = midi(pitch) + semis
    return name_of(m) if 0 <= m <= 127 else None


def follow_layer(
    lay: dict, arr: Arrangement, breaks=(), arp: tuple = (), lead: str | None = None
) -> dict:
    """A following layer as a plain one: the main lane's notes moved by its
    octave, and the main lane's own sequencing — slides, arp, breaks, the
    section arp tuple and a chunk's `lead` (velocities are the main lane's
    by default:
    `layer_arrangement`). A bred Sound's layers follow, so the card, "Use
    with its layers" and any later edit of the main grid all play the same
    stack. Resolved at render time, from the arrangement being rendered, so
    the stem's cache payload carries the notes it plays. Any other layer is
    returned as it is."""
    octave = follow_octave(lay)
    if octave is None:
        return lay
    semis = 12 * octave
    cells = [
        [q, step]
        for pitch, step in arr.cells
        if (q := _shift(pitch, semis)) is not None
    ]
    out = {k: v for k, v in lay.items() if k != "follow"}
    out["cells"] = cells
    out.pop("velocities", None)  # absent: the main lane's
    # only the slides on its note steps are heard (`lane_render`'s rule, so
    # a materialised copy of this layer renders the same)
    slides = sorted({int(x) for x in arr.slides} & {int(c[1]) for c in cells})
    if slides:
        out["slides"] = slides
    else:
        out.pop("slides", None)
    cuts = [
        [q, step] for pitch, step in arr.cuts if (q := _shift(pitch, semis)) is not None
    ]
    if cuts:
        out["cuts"] = cuts
    else:
        out.pop("cuts", None)
    if arr.arp_mode != "off":
        out["arp_mode"], out["arp_octaves"] = arr.arp_mode, arr.arp_octaves
    else:
        out.pop("arp_mode", None)
        out.pop("arp_octaves", None)
    if arr.arp_mode == "pattern" and arr.arp_pattern:  # the pattern it plays
        out["arp_pattern"] = arr.arp_pattern
    else:
        out.pop("arp_pattern", None)
    if arr.strum and arr.arp_mode == "off":
        out["strum"] = arr.strum
    else:
        out.pop("strum", None)
    if nudge := nudge_of(arr):  # the main lane's timing, as its notes'
        out["note_nudge"] = dict(nudge)
    else:
        out.pop("note_nudge", None)
    if feel := notefeel.of(arr):  # each note's own feel, at its octave
        out["note_feel"] = notefeel.shifted(feel, semis)
    else:
        out.pop("note_feel", None)
    # the main lane's chance on its steps: the roll is the pitch classes',
    # so an octave away the same notes rest (`note_roll`)
    if arr.note_chance:
        out["note_chance"] = dict(arr.note_chance)
    else:
        out.pop("note_chance", None)
    if breaks:
        out["breaks"] = sorted(breaks)
    if arp and arp[0]:
        moved = [q for p in arp[0] if (q := _shift(p, semis)) is not None]
        out["arp"] = [moved, *arp[1:]]
    if lead and (q := _shift(lead, semis)) is not None:
        out["lead"] = q
    return out


def layer_arrangement(arr: Arrangement, lay: dict) -> Arrangement:
    """The arrangement a layer plays: the loop's clock and swing with the
    layer's own sequencing. A layer dict carries `velocities` only when
    they differ from the main lane's (`song_bridge.lane_render`; without
    them it plays the main lane's), and `slides`/`arp_mode`/`arp_octaves`/
    `arp_pattern`/`strum`/`note_chance`/`note_feel` only when set — never
    the main lane's."""
    vel = lay.get("velocities")
    return replace(
        arr,
        velocities=vel if vel is not None else arr.velocities,
        slides=[int(x) for x in lay.get("slides") or []],
        cuts=[list(c) for c in lay.get("cuts") or []],
        arp_mode=str(lay.get("arp_mode", "off")),
        arp_octaves=int(lay.get("arp_octaves", 1)),
        strum=float(lay.get("strum") or 0.0),
        note_nudge=dict(lay.get("note_nudge") or {}),
        note_chance=dict(lay.get("note_chance") or {}),
        arp_pattern=dict(lay.get("arp_pattern") or {}),
        note_feel=dict(lay.get("note_feel") or {}),
    )


def _playing_steps(arr: Arrangement, cells=None) -> dict[str, list[int]]:
    """The note grid's steps per pitch that play: a run (a held note) must
    START inside the grid, `0 <= step < bars * steps_per_bar`. A note stored
    past a shrunk grid is hidden, and used to play anyway — rendered past
    the loop's end, `_wrap_tile` folded it onto the start, so bar 2 of a
    grid cut to one bar sounded on top of bar 1. The drum machine always
    clipped its hits this way. A run that starts inside keeps its steps
    past the end: a song section's chunk (`SectionParts.window`) carries a
    held note whole into the chunk it starts in. `cells`: a layer's own,
    on `arr`'s grid (default: the main lane's)."""
    total = arr.bars * arr.steps_per_bar
    by_pitch: dict[str, list[int]] = {}
    for pitch, step in arr.cells if cells is None else cells:
        by_pitch.setdefault(pitch, []).append(int(step))
    # a note on the grid's END line whose feel starts it early sounds
    # inside the grid (a section chunk's next downbeat, played early:
    # `song_model.SectionParts.window` hands it to the chunk it sounds in)
    feel = notefeel.of(arr)
    early = {
        p
        for k, v in feel.items()
        if (at := notefeel.split(k)) and at[1] == total and float(v[0]) < 0
        for p in (at[0],)
    }
    out: dict[str, list[int]] = {}
    for pitch, steps in by_pitch.items():
        kept: list[int] = []
        for s in sorted(set(steps)):
            if (
                0 <= s < total
                or (kept and kept[-1] == s - 1)
                or (s == total and pitch in early)
            ):
                kept.append(s)
        if kept:
            out[pitch] = kept
    return out


def note_schedule(
    arr: Arrangement,
    layers: list[dict] | None = None,
    main_muted: bool = False,
    breaks=(),
    arp: tuple = (),
    lead: str | None = None,
) -> list[list[tuple]]:
    """What `build_song` schedules for the synth lanes, as facts instead of
    audio: one list per lane — the main lane first (empty when muted), then
    each of `layers` in order, a follower resolved (`follow_layer`) — of
    (start_beat, dur_beats, pitch, velocity, slide) in `_lane_timeline`'s
    shape. Same arguments, same rules: only steps that play
    (`_playing_steps`: nothing hidden past a shrunk grid), runs merged into
    held notes and split at cuts and breaks, swing on odd start steps, the
    arp when on, a chord's notes apart by the lane's `strum`. A note
    sounds for `NOTE_GATE` of `dur_beats`.

    A lane without an arp, slides or a strum plays per row in
    `build_song`; its
    starts and lengths are the same arithmetic as the timeline's (a rest
    plus the swing shift, the note shortened by the shift), which
    `tests/studio/test_midi_export.py` holds against the built song.
    The MIDI export (`midi_bridge`) is the reader."""
    step_beats = bar_beats(arr) / arr.steps_per_bar
    out: list[list[tuple]] = []
    by_pitch = _playing_steps(arr) if not main_muted else {}
    arp = arp if arp and arp[0] and not main_muted else ()
    main = _lane_timeline(by_pitch, arr, step_beats, breaks, arp)
    out.append(main if by_pitch or arp else [])
    for lay in layers or []:
        lay = follow_layer(lay, arr, breaks, arp, lead)
        larr = layer_arrangement(arr, lay)
        lby = _playing_steps(larr, lay.get("cells", []))
        larp = tuple(lay.get("arp") or ())
        larp = larp if larp and larp[0] else ()
        lane = _lane_timeline(lby, larr, step_beats, lay.get("breaks", ()), larp)
        out.append(lane if lby or larp else [])
    return out


def build_song(
    patch: Patch,
    arr: Arrangement,
    curves: dict | None = None,
    layers: list[dict] | None = None,
    main_muted: bool = False,
    clip_root=None,
    breaks=(),
    main_gain: float = 1.0,
    arp: tuple = (),
    memo: NoteMemo | None = None,
    lead: str | None = None,
    auto: dict | None = None,
    kit_ready: bool | None = None,
) -> Song:
    """The loop as a Song. `breaks` (steps where a held main-lane note is
    re-attacked) and a layer dict's own `"breaks"` are only ever set by a
    song section's tiled cycle (`song_model.SectionParts`); a loop has none.
    `main_gain` is the main lane's track gain, applied per note event (a
    section's; a loop's main lane has none, and 1.0 multiplies exactly as
    before). Per event, because `Song.play` -> `Song.track(name)` resets a
    track's own gain to 1.0. `arp` =
    (pitches, start_step, period): a chunk of a section's cycle arpeggiates
    the whole cycle's pitches, its phase counted from the cycle's start
    (`start_step`) and restarting every `period` steps, as the pattern
    does looping on its own; a loop passes none. `lead` (a layer dict's
    own `"lead"`): the pitch played just before a chunk whose first note
    slides (`song_model.SectionParts.window`) — each chunk is a song of
    its own, and the slide glides from it as it does in the cycle rendered
    whole. `auto` (a layer dict's own `"auto"`): a song section's
    automation lanes for the lane (`_auto_arrays`); a lane wins over a
    drawn curve on the same field, and is the one automation a layer
    plays. `kit_ready`: whether the acoustic kit is here, as the render's
    key saw it (`RenderInputs.kit_ready`; None asks now)."""
    song = _song(arr)
    # one place: every scheduler below reads `patch`, so synced LFO rates
    # resolve here rather than being plumbed through each note() call
    patch = at_bpm(patch, arr.bpm)
    synth = memo.synth(patch) if memo else Synth(patch)
    # (a step's chance rests its notes here, for the per-row path below as
    # for the timeline: `_chanced`)
    by_pitch = _chanced(_playing_steps(arr), arr, breaks) if not main_muted else {}

    # Precompute one loop-length array per curved param, shared by every
    # note; each note slices its own segment out of it (see _curve_slice).
    curve_arrays: dict[str, np.ndarray] = {}
    if curves:
        loop_n = n_samples(loop_seconds(arr, ("synth",)), SR)
        for param, points in curves.items():
            if param not in CURVABLE:
                continue
            vals = curve_array(points, param, loop_n)
            if vals is not None:
                # drawn over the loop's bars: under a ramp, still its bars
                curve_arrays[param] = _meter.warp(
                    vals,
                    _meter.arr_clock(arr),
                    loop_bars(arr, ("synth",)) * bar_beats(arr),
                )
    curve_arrays.update(_auto_arrays(auto, arr))  # a section lane wins

    # A bar is its meter's beats (4 in 4/4); a step is 1/steps_per_bar of
    # a bar, so: 16 steps/bar -> 0.25 beats/step, 12 in 3/4 -> 0.25 too.
    step_beats = bar_beats(arr) / arr.steps_per_bar

    # Arp or slides need cross-row, time-ordered scheduling: build one
    # global timeline instead of the per-row event lists below.
    arp = arp if arp and arp[0] and not main_muted else ()
    if _uses_timeline(by_pitch, arr, arp, patch):
        timeline = _lane_timeline(by_pitch, arr, step_beats, breaks, arp)
        song.track("synthlane")
        _timeline_notes(
            song,
            "synthlane",
            patch,
            timeline,
            curve_arrays,
            main_gain,
            lead,
            ordered=bool(strum_beats(arr)),
        )
        by_pitch = {}  # rows are consumed; fall through to drums only

    # Build (pitch, beats, velocity) events with rests, per pitch row, each
    # row as its own track so rows overlap freely. `Song.notes` treats a
    # pitch of None as a rest (see sequencer.py). Velocity is taken from
    # `arr.velocities` keyed by the run's start step (default 0.9). Swing
    # delays odd-numbered start steps by `arr.swing * step_beats`, taken
    # from lengthening the preceding rest and shortening the note by the
    # same amount (floored so the note never drops below step_beats / 8).
    for i, (pitch, steps) in enumerate(sorted(by_pitch.items())):
        events: list[tuple[str | None, float] | tuple[str | None, float, float]] = []
        cursor = 0
        for start, run in _merge_runs(steps, _row_breaks(breaks, arr.cuts, pitch)):
            shift = arr.swing * step_beats if start % 2 == 1 else 0.0
            rest_beats = (start - cursor) * step_beats + shift
            if rest_beats > 0:
                events.append((None, rest_beats))  # rest
            duration_beats = run * step_beats
            if shift:
                duration_beats = max(duration_beats - shift, step_beats / 8)
            velocity = arr.velocities.get(str(start), 0.9)
            events.append((pitch, duration_beats, velocity))
            cursor = start + run
        song.track(f"synth{i}")
        if curve_arrays:
            _curved_notes(song, f"synth{i}", patch, events, curve_arrays, main_gain)
        else:
            song.notes(f"synth{i}", synth, events, gain=main_gain)

    # Extra layers: inactive lanes stacked under the live one, each with its
    # own patch, gain and sequencing (`layer_arrangement`: its velocities,
    # slides and arp, which state.render_layer / song_model carry). A layer
    # with an arp or slides takes the main lane's timeline path
    # (`_lane_timeline`), a section chunk's `"arp"` included; any other
    # plays per row, as it always did. Curves belong to the lane being
    # edited — a layer ignores them, in a song section too
    # (song_bridge._track_candidate); what it does play is its section's
    # automation lanes (`"auto"`).
    for k, lay in enumerate(layers or []):
        # a following layer plays the main lane's notes (`render_stems`
        # resolves it first; a direct caller gets the same here)
        lay = follow_layer(lay, arr, breaks, arp, lead)
        larr = layer_arrangement(arr, lay)
        lpatch = at_bpm(Patch.from_dict(lay["patch"]), arr.bpm)
        lsynth = memo.synth(lpatch) if memo else Synth(lpatch)
        lgain = float(lay.get("gain", 1.0))
        lbreaks = lay.get("breaks", ())
        lby = _chanced(_playing_steps(larr, lay.get("cells", [])), larr, lbreaks)
        larp = tuple(lay.get("arp") or ())
        larp = larp if larp and larp[0] else ()
        lcurves = _auto_arrays(lay.get("auto"), arr)
        if _uses_timeline(lby, larr, larp, lpatch):
            timeline = _lane_timeline(lby, larr, step_beats, lbreaks, larp)
            song.track(f"layer{k}_lane")
            _timeline_notes(
                song,
                f"layer{k}_lane",
                lpatch,
                timeline,
                lcurves,
                lgain,
                lay.get("lead"),
                ordered=bool(strum_beats(larr)),
            )
            continue
        for i, (pitch, steps) in enumerate(sorted(lby.items())):
            events = [
                ev if ev[0] is None else (pitch, ev[1], ev[2])
                for ev in _row_events(
                    steps, larr, step_beats, _row_breaks(lbreaks, larr.cuts, pitch)
                )
            ]
            # the gain rides each note event: `Song.play` -> `Song.track(name)`
            # resets a track's own gain to 1.0, which silently dropped it
            # At 1.0 this multiplies exactly as before.
            song.track(f"layer{k}_{i}")
            if lcurves:
                _curved_notes(song, f"layer{k}_{i}", lpatch, events, lcurves, lgain)
            else:
                song.notes(f"layer{k}_{i}", lsynth, events, gain=lgain)

    _schedule_drums(song, arr, clip_root, kit_ready)

    return song


def build_clips_song(
    arr: Arrangement, clip_root, parts: tuple[str, ...] = ("synth", "drums")
) -> Song:
    """The clip lane as its own Song: each placed clip starts at its bar
    (stereo, clip's own length — Track.render pads/cuts at the loop).
    `parts`: the render's parts, whose loop the clips are placed in."""
    song = _song(arr)
    song.track("clips")
    if clip_root is None:
        return song
    from .clips import load_clip

    # Defence in depth: the add route clamps at_bar to the loop, but old
    # persisted state (a shrunk grid, a hand-edited project file) could
    # still carry a clip past the current loop length. The loop is the one
    # being rendered (`parts`): the cap read both clocks whatever played,
    # so with drums off a clip past the synth loop was placed when the idle
    # drum machine happened to be longer, and `_wrap_tile` folded it onto
    # bar 1 — under a stem key (the loop's length) that never saw drum_bars.
    bars_cap = loop_bars(arr, parts)
    for c in getattr(arr, "clips", []) or []:
        if int(c.get("at_bar", 0)) >= bars_cap:
            continue
        loaded = load_clip(clip_root, str(c.get("clip", "")))
        if loaded is None:
            continue
        audio, csr = loaded
        if csr != SR and csr > 0:
            n = int(round(len(audio) * SR / csr))
            idx = np.linspace(0, len(audio) - 1, n)
            if audio.ndim == 2:
                audio = np.column_stack(
                    [
                        np.interp(idx, np.arange(len(audio)), audio[:, ch])
                        for ch in (0, 1)
                    ]
                )
            else:
                audio = np.interp(idx, np.arange(len(audio)), audio)
        beat = max(0, int(c.get("at_bar", 0))) * bar_beats(arr)
        song.play("clips", audio, beat, float(c.get("gain", 1.0)))
    return song


def _voice_sample(arr: Arrangement, voice: str, clip_root) -> np.ndarray | None:
    """The clip a drum row references, mono, capped at 2s —
    or None to fall back to the synthesized hit."""
    name = (getattr(arr, "drum_samples", {}) or {}).get(voice)
    if not name or clip_root is None:
        return None
    from .clips import load_clip

    loaded = load_clip(clip_root, name)
    if loaded is None:
        return None
    audio, csr = loaded
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if csr != SR and csr > 0:  # linear resample: one-shots, not masters
        n = int(round(len(audio) * SR / csr))
        idx = np.linspace(0, len(audio) - 1, n)
        audio = np.interp(idx, np.arange(len(audio)), audio)
    return audio[: n_samples(2.0, SR)]


class Hit(tuple):
    """One hit of the drum schedule. It IS the pair `(beat, gain)` — it
    unpacks, compares and hashes as one, so everything that read the
    schedule before per-hit data reads it still — and carries what only a
    hit with data has: `tune` (semitones on that hit) and `until` (the beat
    at which its own next retrigger cuts it: a ratchet is one voice struck
    again, not voices piling up). On the acoustic kit alone it also carries
    its articulation (`art`), its velocity (`vel`: what picks the recorded
    layer, where `gain` is the row's level x it) and its round robin
    (`rr`: a strike count, `rr_marks`)."""

    tune: float = 0.0
    until: float | None = None
    art: str = ""
    vel: float | None = None
    rr: int = 0

    def __new__(
        cls,
        beat: float,
        gain: float,
        tune: float = 0.0,
        until=None,
        art: str = "",
        vel: float | None = None,
        rr: int = 0,
    ):
        self = super().__new__(cls, (beat, gain))
        if tune:
            self.tune = tune
        if until is not None:
            self.until = until
        if art:
            self.art = art
        if vel is not None:
            self.vel = vel
        if rr:
            self.rr = rr
        return self


RATCHET_FALL = 0.85  # each retrigger of a ratchet, against the one before


def chance_seed(voice: str, steps) -> int:
    """What a row's per-hit chance is rolled from: the row's own cells.
    Editing another row, a velocity, a nudge or a chance leaves this row's
    rolls alone; adding or removing one of its hits rolls it again."""
    cells = ",".join(str(s) for s in sorted({int(s) for s in steps}))
    return zlib.crc32(f"{voice}:{cells}".encode())


def hit_roll(seed: int, step: int) -> float:
    """The roll of one hit's chance, 0 <= x < 1: a hash of (the row's seed,
    the hit's step in its PATTERN) — never an rng stream, so it is the same
    in every process, for the loop, for a section's native cycle and for
    each chunk of a tiled one (`song_model._flat` hands the pattern's seed
    and step down as the hit's `k`). The hit plays when the roll is under
    its chance."""
    digest = hashlib.sha1(f"{int(seed)}:{int(step)}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2.0**64


def clean_hits(hits, drums, total: int | None = None) -> dict:
    """A per-hit table as it is stored: only hits with a cell (inside
    `total` steps, when given), only values that say something
    (`schema.HIT_NEUTRAL`), the caps and clamps of `schema.DRUM_HITS`."""
    from . import schema

    out = schema.conform(
        hits if isinstance(hits, dict) else {}, schema.nodes().DRUM_HITS
    )
    on = {f"{v}.{int(s)}" for v, s in drums if total is None or 0 <= int(s) < total}
    return {k: v for k, v in out.items() if k in on}


def drum_hits(arr: Arrangement) -> list[tuple[str, list[Hit]]]:
    """The drum machine's schedule, as facts: per sounding voice (sorted, so
    "hat" comes before "ohat"), its hits as (beat, gain) — after the grid's
    clip (a hit past a shrunk grid is hidden and does not play), mute and
    solo, the probability roll, swing and humanise; gain = the voice's
    level x the step's velocity x humanise. `_schedule_drums` places
    exactly these, the MIDI export (`midi_bridge`) writes them and the
    sidechain reads the same per-hit facts (`fxrack.kick_steps`), so all
    read one schedule. Deterministic per arrangement (the seed is derived
    from the grid), so a loop re-renders identically until something
    changes.

    Per-hit data (`arr.drum_hits`, "<voice>.<step>" -> {v, p, r, n, t};
    drums.md, "The per-hit model") and a row's own swing
    (`arr.drum_row_swing`) change a hit only where they are set — with
    neither, this is the schedule it always was, float for float:
    - `v`: the hit's velocity in place of the step's `drum_vel`;
    - `p`: its chance, on top of the voice's (`hit_roll`);
    - `n`: its start moved by that share of a step, before swing. A hit
      nudged to before the loop's first beat wraps to its END (the loop
      and a section's cycle are circles); one nudged or ratcheted past the
      end rings on into the tail the renderers fold back;
    - `r`: that many hits inside the step, evenly, each `RATCHET_FALL` of
      the one before and cut by the next (`Hit.until`);
    - `t`: semitones on that hit (`Hit.tune`).
    A window of a tiled section (`SectionParts.window`) marks the next
    window's early first hit `pk` and keeps it one step past its own grid,
    so a chunked cycle is the cycle rendered whole."""
    steps_per_bar = max(1, int(getattr(arr, "drum_steps", arr.steps_per_bar)))
    total = max(1, int(getattr(arr, "drum_bars", arr.bars))) * steps_per_bar
    step_beats = bar_beats(arr) / steps_per_bar
    swing = float(getattr(arr, "drum_swing", 0.0))
    human = float(getattr(arr, "drum_humanize", 0.0))
    mix = getattr(arr, "drum_mix", {}) or {}
    vel = getattr(arr, "drum_vel", {}) or {}
    data = getattr(arr, "drum_hits", None) or {}
    row_swing = getattr(arr, "drum_row_swing", None) or {}
    by_voice: dict[str, list[int]] = {}
    for name, step in arr.drums:
        s = int(step)
        if name in ALL_DRUM_ROWS and (
            0 <= s < total or (s == total and (data.get(f"{name}.{s}") or {}).get("pk"))
        ):
            by_voice.setdefault(name, []).append(s)
    # the seed and the draw order are today's eight rows' as they always
    # were: the acoustic kit's further rows (`NEW_DRUM_ROWS`) are left out
    # of the seed and drawn after them, so a beat with none is the schedule
    # it always was, and one with some moves none of the eight's draws
    seed = zlib.crc32(
        json.dumps(
            [
                [c for c in arr.drums if c[0] in DRUM_ROWS],
                human,
                {k: v for k, v in mix.items() if k in DRUM_ROWS},
            ],
            sort_keys=True,
        ).encode()
    )
    rng = np.random.default_rng(seed)
    acoustic = getattr(arr, "drum_kit", "") == "naked"
    marks = rr_marks(arr) if acoustic else {}
    out: list[tuple[str, list[Hit]]] = []
    for voice, steps in sorted(by_voice.items(), key=_row_order):
        m = mix.get(voice, {})
        if not drum_row_sounds(arr, voice):  # muted, or soloed out
            continue
        level = float(m.get("level", 1.0))
        prob = float(m.get("prob", 1.0))
        sw = float(row_swing.get(voice, swing))
        own_seed: int | None = None
        hits: list[Hit] = []
        for s in sorted(set(steps)):
            if prob < 1.0 and rng.random() > prob:
                continue
            h = data.get(f"{voice}.{s}") if data else None
            beat = s * step_beats + (sw * step_beats if s % 2 == 1 else 0.0)
            if human > 0:
                beat += float(rng.normal(0.0, 0.04 * human)) * step_beats
            v = float(vel.get(str(s), 1.0))
            if h and "v" in h:
                v = float(h["v"])
            gain = level * v
            if human > 0:
                lift = float(np.clip(1.0 + rng.normal(0.0, 0.12 * human), 0.4, 1.3))
                gain *= lift
                v *= lift
            # the acoustic kit's marks: what it plays, how hard, which take
            mark = (
                {
                    "art": _art_of(voice, h),
                    "vel": v,
                    "rr": int((h or {}).get("q", marks.get(f"{voice}.{s}", 0))),
                }
                if acoustic
                # a choke never sounds, on any kit (a beat moved back from
                # the acoustic kit keeps its `a`): marked, the render skips it
                else {"art": "choke"}
                if h and h.get("a") == "choke"
                else {}
            )
            if not h:
                hits.append(Hit(max(0.0, beat), gain, **mark))
                continue
            # the draws above are made whatever the roll says: a chance on
            # one hit never re-humanises the hits after it
            p = float(h.get("p", 1.0))
            if p < 1.0:
                if h.get("k"):
                    roll_seed, roll_step = h["k"]
                else:
                    if own_seed is None:
                        own_seed = chance_seed(voice, (x for x in steps if x < total))
                    roll_seed, roll_step = own_seed, s
                if hit_roll(roll_seed, roll_step) >= p:
                    continue
            n = float(h.get("n", 0.0))
            beat += n * step_beats
            if beat < 0.0:  # before the loop's first beat: its last
                beat = beat + total * step_beats if n < 0.0 else 0.0
            r = max(1, min(4, int(h.get("r", 1))))
            tune = float(h.get("t", 0.0))
            sub = step_beats / r
            for j in range(r):
                if acoustic:
                    mark = {
                        **mark,
                        "vel": v * RATCHET_FALL**j,
                        "rr": int(h.get("q", marks.get(f"{voice}.{s}", 0))) + j,
                    }
                hits.append(
                    Hit(
                        beat + j * sub,
                        gain * RATCHET_FALL**j,
                        tune,
                        beat + (j + 1) * sub if j < r - 1 else None,
                        **mark,
                    )
                )
        out.append((voice, hits))
    return _open_hats(out) if acoustic else out


def _art_of(voice: str, h) -> str:
    """A hit's articulation on the acoustic kit: an `ohat` cell is the hat's
    "open"; else its own `a`, or its row's default ("hit" for a row with
    none)."""
    if voice == "ohat":
        return "open"
    arts = ARTICULATIONS.get(voice)
    a = (h or {}).get("a")
    if arts:
        return a if a in arts else arts[0]
    return "hit"


def _open_hats(out: list) -> list:
    """The acoustic kit has one hi-hat: the `ohat` row's hits (each with its
    row's level and chance, marked "open") join the hat row's, in time."""
    rows = dict(out)
    if "ohat" not in rows:
        return out
    hat = sorted([*rows.get("hat", []), *rows.pop("ohat")], key=lambda h: h[0])
    rows["hat"] = hat
    return sorted(rows.items(), key=_row_order)


def rr_marks(arr: Arrangement) -> dict[str, int]:
    """The round robin each cell of a beat starts at, as `"<voice>.<step>"`:
    how many strikes of its (row, articulation) come before it in its
    pattern, in step order (a ratchet strikes `r` times; an `ohat` cell is
    the hat's "open"). A count of the pattern's own cells, never of what a
    roll let play, so every pass of a pattern — the loop, each repeat of a
    section's tiled cycle (`song_model._tile_rr`) and each chunk of it —
    takes the same takes at the same steps."""
    steps_per_bar = max(1, int(getattr(arr, "drum_steps", arr.steps_per_bar)))
    total = max(1, int(getattr(arr, "drum_bars", arr.bars))) * steps_per_bar
    data = getattr(arr, "drum_hits", None) or {}
    cells = sorted(
        {
            (int(s), v)
            for v, s in arr.drums
            if v in ALL_DRUM_ROWS and 0 <= int(s) < total
        }
    )
    count: dict[tuple[str, str], int] = {}
    out: dict[str, int] = {}
    for s, v in cells:
        h = data.get(f"{v}.{s}") or {}
        key = ("hat" if v == "ohat" else v, _art_of(v, h))
        out[f"{v}.{s}"] = count.get(key, 0)
        count[key] = count.get(key, 0) + max(1, min(4, int(h.get("r", 1))))
    return out


def _row_order(item) -> tuple:
    """`drum_hits`' voice order: today's eight by name (so "hat" before
    "ohat"), then the acoustic kit's further rows by name."""
    return (item[0] not in DRUM_ROWS, item[0])


def _tuned(voice: str, params: dict, sample, semitones: float) -> np.ndarray:
    """A voice's hit `semitones` away from its own: a synthesized voice
    with a `tune` (kick, snare, tom, rim: `drumlab.DRUM_PARAMS`) is struck
    at that pitch; one without (the hats, the clap, the crash) is itself; a
    sampled row (`sample`: a clip, or an acoustic hit, stereo) is resampled
    — linear, as its sample-rate conversion is (`_voice_sample`: one-shots,
    not masters)."""
    ratio = 2.0 ** (float(semitones) / 12.0)
    if sample is not None:
        n = max(2, int(round(len(sample) / ratio)))
        idx = np.linspace(0, len(sample) - 1, n)
        base = np.arange(len(sample))
        if sample.ndim == 2:
            return np.stack(
                [np.interp(idx, base, sample[:, c]) for c in range(sample.shape[1])],
                axis=1,
            )
        return np.interp(idx, base, sample)
    spec = next((s for s in specs_of(voice) if s["name"] == "tune"), None)
    if spec is None:
        return dr.hit(voice, **params)
    base = float(params.get("tune", spec["default"]))
    return dr.hit(voice, **{**params, "tune": base * ratio})


# The electronic voice a row with none of its own plays — an acoustic-kit
# row on an electronic kit, or on the acoustic kit while it is not here: the
# crash voice at another decay, the tom retuned three semitones a drum down
# (its own tune, `drum_params["tom"]`, times the factor). Nothing is ever
# silently dropped.
FALLBACK_VOICES: dict[str, tuple[str, dict]] = {
    "ride": ("crash", {"decay": 0.9}),
    "crash2": ("crash", {"decay": 1.4}),
    "splash": ("crash", {"decay": 0.6}),
    "china": ("crash", {"decay": 1.2}),
}
TOM_STEPS: dict[str, float] = {f"tom{k + 1}": 2.0 ** (-k / 12 * 3) for k in range(1, 5)}
# a hat articulation that closes the hat (cuts a ringing half / open one),
# and one that rings until it is closed. "" is a hat hit on an electronic
# kit: every one closes, as it always did
HAT_CLOSERS = ("", "closed", "pedal", "tight")
HAT_RINGERS = ("half", "open")
# The acoustic kit's level against the electronic voices: one fixed make-up
# gain on every acoustic hit, never a per-buffer normalisation. Measured
# (2026-11-28, studio mics, every `beatgen.STYLES` beat, seed 1, 2 bars, its
# kick / snare / hat / ohat / tom / crash cells, four passes, BS.1770): the
# kit sat a median 11.2 LU under the electronic voices (7.7..15.6; alone,
# kick -14.3, snare -3.2, hat -4.4, tom -14.8, crash -12.3), its sample peaks
# a median only 1.5 dB under — the recordings' crest factor, not a quiet kit.
# +11 dB matches the kit by LOUDNESS, so the drums sit against the synths as
# the electronic kit's do. The trade-off: at the same loudness the acoustic
# transients peak ~9.5 dB over the electronic kit's. Nothing that levels a
# render reads loudness — the loop (`render._normalize`) scales by the 99.5th
# percentile of its frames' peaks and limits at 0.97, the song as played
# (`song_play.gain_of`) is capped by its peak (CEILING / peak); only the
# export's -14 / -9 LUFS targets measure loudness — so those references key
# on the drum transients and an acoustic loop or song comes out quieter
# overall than an electronic one, and every level-dependent unit on the drum
# bus or the master (drive, clip, compress, ott, limiter, transient, vgate,
# sidechain) hears hotter drums. Pinned by
# `test_acoustic_schedule.py::test_the_kits_make_up_gain_is_pinned`.
KIT_GAIN_DB = 11.0
KIT_GAIN = 10.0 ** (KIT_GAIN_DB / 20.0)
KIT_READY_SECONDS = 2.0  # how long a "the kit is here" answer is believed
_KIT_READY: dict[str, tuple[float, bool]] = {}


def kit_ready() -> bool:
    """Is the acoustic kit on this machine (`kits.fetch.availability`: a
    stat a file, ~50 ms) — believed for `KIT_READY_SECONDS`, since every
    acoustic key asks. `forget_kit` drops the answer (a download ends)."""
    d = _kit_fetch.kit_dir()
    now = time.monotonic()
    got = _KIT_READY.get(str(d))
    if got is None or now - got[0] > KIT_READY_SECONDS:
        got = (now, bool(_kit_fetch.availability(directory=d).get("ready")))
        _KIT_READY[str(d)] = got
    return got[1]


def forget_kit() -> None:
    """Drop `kit_ready`'s answer: the next one asks the disk."""
    _KIT_READY.clear()


def acoustic_kit(arr: Arrangement, ready: bool | None = None):
    """`(KitMap, root)` when `arr` plays the acoustic kit and it is here —
    `ready`, the answer the render's key was made with (`RenderInputs.
    kit_ready`), else asked now — or None (its rows play `_electronic`'s
    voices)."""
    if getattr(arr, "drum_kit", "") != "naked":
        return None
    if not (kit_ready() if ready is None else ready):
        return None
    root = _kit_fetch.kit_dir()
    return _kit_sfz.read_kit(root), root


def _electronic(arr: Arrangement, voice: str) -> tuple[str, dict]:
    """The electronic voice a row plays and its params: its own, or its
    `FALLBACK_VOICES` / `TOM_STEPS` stand-in."""
    base, over = FALLBACK_VOICES.get(
        voice, ("tom" if voice in TOM_STEPS else voice, {})
    )
    valid = {spec["name"] for spec in specs_of(base)}
    params = {k: v for k, v in (arr.drum_params.get(base) or {}).items() if k in valid}
    if voice in TOM_STEPS:
        tune = next(s["default"] for s in specs_of("tom") if s["name"] == "tune")
        params["tune"] = float(params.get("tune", tune)) * TOM_STEPS[voice]
    return base, {**params, **over}


def _next_after(beats: list[float], beat: float, loop_beats: float) -> float | None:
    """The first of `beats` after `beat`, or the first of the next repeat."""
    if not beats:
        return None
    nxt = min((c for c in beats if c > beat), default=None)
    return min(beats) + loop_beats if nxt is None else nxt


def _schedule_drums(
    song: Song, arr: Arrangement, clip_root=None, ready: bool | None = None
) -> None:
    """The drum machine: its own bars/steps/swing, a velocity lane, per-
    voice level / mute / solo / probability, humanise (`drum_hits`: what
    plays when), and the open hat choked by the next closed hat.

    On the acoustic kit (`acoustic_kit`) each hit of its rows is the kit's
    recording (`kits.play.render_hit`) of its articulation at its velocity
    and round robin, through the beat's mic mix (`play.levels` of
    `drum_mics`), scaled by the row's level (`gain / vel`: the level never
    picks the layer) and `KIT_GAIN`; each distinct hit is rendered once a
    render. A pedal, tight or closed hat cuts a ringing half / open one; a
    `choke` cuts its row's ringing hit and sounds nothing. A row with a clip
    plays its clip; clap and rim, and every row while the kit is not here,
    play the electronic voices (`_electronic`; a half / open hat the open
    hat voice)."""
    steps_per_bar = max(1, int(getattr(arr, "drum_steps", arr.steps_per_bar)))
    total = max(1, int(getattr(arr, "drum_bars", arr.bars))) * steps_per_bar
    loop_beats = total * (bar_beats(arr) / steps_per_bar)
    kit = acoustic_kit(arr, ready)
    mics = _kit_play.levels(arr.drum_mics) if kit else {}
    mic_key = tuple(sorted(mics.items()))
    made: dict[tuple, np.ndarray] = {}  # each distinct acoustic hit, once
    # Beats at which a closed hat actually sounded: after mute/solo, the
    # probability roll, swing and humanise. The open hat chokes on these —
    # choking on the raw grid cut it early against a swung hat, and a muted
    # closed-hat row still silenced every open hat it no longer played over.
    # `drum_hits` walks "hat" before "ohat", so this is filled first.
    closed: list[float] = []
    for voice, hits in drum_hits(arr):
        clip = _voice_sample(arr, voice, clip_root)
        acoustic = kit is not None and clip is None and voice in ACOUSTIC_ROWS
        if voice == "hat":
            closed += [h[0] for h in hits if h.art in HAT_CLOSERS]
        # a choke cuts its row's ring on the acoustic kit (its fallbacks
        # too); on an electronic kit it cuts nothing (and sounds nothing)
        chokes = (
            [h[0] for h in hits if h.art == "choke"]
            if getattr(arr, "drum_kit", "") == "naked"
            else []
        )
        voices: dict[str, tuple] = {}  # an electronic voice: (sample, base, params)
        tuned: dict[tuple, np.ndarray] = {}  # a hit's own pitch (`Hit.tune`)
        track = f"drum_{voice}"
        song.track(track)
        for h in hits:
            beat, gain = h
            if h.art == "choke":  # cuts the ring (below), sounds nothing
                continue
            if acoustic:
                vel = max(0.0, float(h.vel if h.vel is not None else 1.0))
                q = round(min(1.0, vel) * 127)
                if q == 0:  # silent: nothing to ask the kit for
                    continue
                key = (voice, h.art, q, h.rr, mic_key)
                if key not in made:
                    made[key] = _kit_play.render_hit(
                        kit[0], kit[1], voice, h.art, q / 127, h.rr, mics
                    )
                src, which = made[key], key
                gain = gain / min(1.0, vel) * KIT_GAIN
            elif clip is not None:
                src, which = clip, "clip"
            else:
                which = "ohat" if voice == "hat" and h.art in HAT_RINGERS else voice
                if which not in voices:
                    base, params = _electronic(arr, which)
                    voices[which] = (dr.hit(base, **params), base, params)
                src = voices[which][0]
            if h.tune:
                if (which, h.tune) not in tuned:
                    tuned[(which, h.tune)] = (
                        _tuned("", {}, src, h.tune)
                        if acoustic or clip is not None
                        else _tuned(voices[which][1], voices[which][2], None, h.tune)
                    )
                src = tuned[(which, h.tune)]
            cut = h.until  # a ratchet's next retrigger cuts this one
            rings = voice == "ohat" or (voice == "hat" and h.art in HAT_RINGERS)
            for nxt in (
                _next_after(closed, beat, loop_beats) if rings else None,
                _next_after(chokes, beat, loop_beats),
            ):
                if nxt is not None:
                    cut = nxt if cut is None else min(cut, nxt)
            hit = src
            if cut is not None:
                secs = (
                    song.clock.span(beat, cut - beat)
                    if isinstance(song, TempoSong)
                    else (cut - beat) * 60.0 / arr.bpm
                )
                n = n_samples(secs, SR)
                if n < len(src):
                    hit = src[:n].copy()
                    fade = min(n, n_samples(0.008, SR))
                    hit[-fade:] *= (
                        np.linspace(1.0, 0.0, fade)[:, None]
                        if hit.ndim == 2
                        else np.linspace(1.0, 0.0, fade)
                    )
            song.play(track, hit, beat, gain)
