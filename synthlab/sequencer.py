"""
synthlab.sequencer — turning sounds into music.

`Song` is a multitrack timeline measured in beats. Tracks own their own
effects chain and gain, and everything is mixed down to a stereo buffer at
the end. Patterns can be written as strings ("x..x..x.") or generated
algorithmically (Euclidean rhythms, scales, chord progressions).
"""

from __future__ import annotations

import os
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache, partial

import numpy as np

from . import drums as dr
from . import effects as fx
from .core import SR
from .sampler import Strikes

# ---------------------------------------------------------------------------
# music theory helpers
# ---------------------------------------------------------------------------

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "pentatonic_minor": [0, 3, 5, 7, 10],
    "pentatonic_major": [0, 2, 4, 7, 9],
    "blues": [0, 3, 5, 6, 7, 10],
    "japanese": [0, 1, 5, 7, 8],
    "whole_tone": [0, 2, 4, 6, 8, 10],
}

CHORDS = {
    "maj": [0, 4, 7],
    "min": [0, 3, 7],
    "dim": [0, 3, 6],
    "aug": [0, 4, 8],
    "maj7": [0, 4, 7, 11],
    "min7": [0, 3, 7, 10],
    "dom7": [0, 4, 7, 10],
    "min9": [0, 3, 7, 10, 14],
    "maj9": [0, 4, 7, 11, 14],
    "sus2": [0, 2, 7],
    "sus4": [0, 5, 7],
    "add9": [0, 4, 7, 14],
    "min11": [0, 3, 7, 10, 14, 17],
    "5": [0, 7],
}


def midi(name: str) -> int:
    """'C4' -> 60"""
    n = name.strip()
    base = NOTE_NAMES.index(n[0].upper())
    i = 1
    while i < len(n) and n[i] in "#b":
        base += 1 if n[i] == "#" else -1
        i += 1
    return 12 * (int(n[i:]) + 1) + base


def name_of(m: int) -> str:
    return f"{NOTE_NAMES[int(m) % 12]}{int(m) // 12 - 1}"


def scale(root: str, mode: str = "minor", octaves: int = 2) -> list[int]:
    """Return MIDI numbers of a scale, e.g. scale('A3', 'minor')."""
    r = midi(root)
    steps = SCALES[mode]
    return [r + o * 12 + s for o in range(octaves) for s in steps] + [r + octaves * 12]


def chord(
    root: str, quality: str = "min", inversion: int = 0, octave_shift: int = 0
) -> list[int]:
    """chord('C4', 'min7') -> [60, 63, 67, 70]"""
    notes = [midi(root) + i + 12 * octave_shift for i in CHORDS[quality]]
    for _ in range(inversion):
        notes = notes[1:] + [notes[0] + 12]
    return notes


def euclid(hits: int, steps: int, rotate: int = 0) -> str:
    """
    Euclidean rhythm — spreads `hits` as evenly as possible over `steps`.
    euclid(3, 8) -> 'x..x..x.'  (the tresillo). Used everywhere in dance music.
    """
    pattern = []
    bucket = 0
    for _ in range(steps):
        bucket += hits
        if bucket >= steps:
            bucket -= steps
            pattern.append("x")
        else:
            pattern.append(".")
    r = rotate % steps
    return "".join(pattern[-r:] + pattern[:-r]) if r else "".join(pattern)


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------


# Longest release a single note is allowed to add to its buffer. Without a
# cap one extreme patch balloons every note in the loop.
MAX_NOTE_TAIL = 4.0


def render_each(thunks: list) -> list:
    """`[f() for f in thunks]`, on the note pool when there's more than one.

    Notes are independent renders (a slide or glide needs only the previous
    note's *pitch*, never its audio), and the native kernels release the
    GIL, so the notes of a lane render on several cores at once. Results
    keep their order; the first exception propagates as from the loop."""
    if len(thunks) < 2:
        return [f() for f in thunks]
    return list(_note_pool().map(lambda f: f(), thunks))


@lru_cache(maxsize=1)
def _note_pool() -> ThreadPoolExecutor:
    # Its own pool, separate from the studio's stem pool: a stem's thread
    # waits on its notes, which must never queue behind that same stem.
    return ThreadPoolExecutor(
        max_workers=min(8, os.cpu_count() or 2), thread_name_prefix="note"
    )


def note_span(dur: float, patch) -> float:
    """How long a buffer a note of `dur` seconds needs.

    Its own length plus the patch's release. This used to be a flat
    `dur * 1.15` against a 0.9 gate, which squeezed every release into the
    25% that was left — a pad asking for 2.2 s got 0.125 s of it on a
    half-second note, so notes never rang into one another and `release`
    was a dead control everywhere except a one-shot.

    The buffer only makes room: the release itself lasts the patch's
    release from the key-up and is silence after (`core.adsr`). It used to
    be stretched over whatever this left past the 0.9 gate — `0.1 * dur`
    more than the release, and the whole `0.15 * dur` floor for a short
    one, so a 0.02 s release on a 2 s note rang for half a second. With
    the gate at or under `dur` there is always room for the whole release
    up to `MAX_NOTE_TAIL`; a longer one is fitted into the buffer, as all
    of them used to be, so it still lands on silence.
    """
    tail = min(float(getattr(patch, "release", 0.0) or 0.0), MAX_NOTE_TAIL)
    return dur + max(tail, 0.15 * dur)


# --- one voice: mono and legato ----------------------------------------------
# A "mono" or "legato" patch (`Patch.voice_mode`) plays one voice, as the
# live keys do (voice-worklet.js noteOn / noteOff): a line of its notes
# renders as one `Synth.note` and the next line takes the voice from it —
# the line's buffer fades over MONO_FADE from where the next one starts
# (the worklet's steal fade, the same samples) and is silent after. Mono:
# every note is a line, retriggered. Legato: a note that starts while one
# of the line's keys is still down (or is a slide) joins the line — the
# voice is re-aimed at it (gliding over `glide`, else at once), the
# envelopes run on, and when the newest key lets go while an older one is
# held the voice goes back to that one. A new line glides in from the last
# note when `glide` > 0 and `glide_mode` is "always" (or, "legato", while
# a key is still down) — and a slide always does, as on a poly patch.
MONO_FADE = 0.003


@dataclass
class Line:
    """One render of a one-voice patch: `first` is the event it starts
    with (its pitch, velocity and start), `path` the (seconds after that,
    pitch) points the voice is re-aimed at, `gate` how long a key of it is
    down, `dur` to the end of its last note, `cut` where the next line
    takes the voice (seconds after its start; None: nothing follows)."""

    first: int
    path: list = field(default_factory=list)
    gate: float = 0.0
    dur: float = 0.0
    from_pitch: object = None
    cut: float | None = None
    slide: bool = False


def mono_lines(events, patch, lead=None) -> list[Line]:
    """`events`: (start, dur, gate, pitch, slide) in seconds, in play order
    (a tie at one start: the later event wins the voice, as a chord's last
    key does live). The lines `patch`'s voice plays them as. `lead`: what
    sounded before the first (a section chunk's predecessor)."""
    legato = getattr(patch, "voice_mode", "poly") == "legato"
    glides = float(patch.glide) > 0
    always = getattr(patch, "glide_mode", "always") != "legato"
    lines: list[Line] = []
    members: list[list] = []  # per line: [start, end, pitch] relative to it
    prev = lead
    for i, (start, dur, gate, pitch, slide) in enumerate(events):
        cur = lines[-1] if lines else None
        t0 = events[cur.first][0] if cur else 0.0
        held = cur is not None and start < t0 + cur.gate
        if legato and cur is not None and (held or slide):
            mine = members[-1]
            if not held:  # a slide holds the last key down into it
                top = max(mine, key=lambda m: m[0])
                top[1] = max(top[1], start - t0)
            mine.append([start - t0, start - t0 + gate, pitch])
            cur.gate = max(m[1] for m in mine)
            cur.dur = max(cur.dur, start - t0 + dur)
        else:
            glide_in = slide or (glides and (always or held))
            if cur is not None:
                cur.cut = start - t0
            lines.append(
                Line(
                    i,
                    gate=gate,
                    dur=dur,
                    from_pitch=prev if glide_in and prev is not None else None,
                    slide=bool(slide) and prev is not None,
                )
            )
            members.append([[0.0, gate, pitch]])
        prev = pitch
    for line, mine in zip(lines, members, strict=True):
        line.path = _held_path(mine)
    return lines


def _held_path(mine: list) -> list:
    """Where a legato line's voice is re-aimed: each later key as it goes
    down, and, when the key it follows lets go while older ones are held,
    the newest of those (last-note priority)."""
    marks = sorted(
        [(m[0], 1, k) for k, m in enumerate(mine)]
        + [(m[1], 0, k) for k, m in enumerate(mine)]
    )
    held: list[int] = []
    path = []
    for t, down, k in marks:
        if down:
            held.append(k)
            if k:
                path.append((t, mine[k][2]))
        elif k in held:
            top = held[-1] == k
            held.remove(k)
            if top and held:
                path.append((t, mine[held[-1]][2]))
    return path


def cut_line(audio: np.ndarray, at: float | None, sr: int = SR) -> np.ndarray:
    """A line's buffer as the next line takes its voice at `at` seconds:
    MONO_FADE's linear fade (the first sample whole, the last 1/f), then
    nothing."""
    if at is None:
        return audio
    i = max(0, int(round(at * sr)))
    if i >= len(audio):
        return audio
    f = max(1, round(MONO_FADE * sr))
    m = min(f, len(audio) - i)
    out = audio[: i + m].copy()
    ramp = (f - np.arange(m)) / f
    out[i:] *= ramp[:, None] if out.ndim == 2 else ramp
    return out


@dataclass
class Track:
    name: str
    gain: float = 1.0
    pan: float = 0.0
    effects: list = field(default_factory=list)
    events: list = field(default_factory=list)  # (start_sample, stereo_audio)

    def add(self, audio: np.ndarray, at_sample: int, gain: float = 1.0):
        self.events.append((int(at_sample), audio, float(gain)))

    def render(self, total: int, sr: int = SR) -> np.ndarray:
        """`total` samples plus a second (`sr` samples) of tail — the
        song's own rate: at the global SR a `Song(sr=22050)` mixed tracks
        a second longer than its own buffer."""
        buf = np.zeros((total + sr, 2))
        for start, audio, g in self.events:
            a = fx.to_stereo(audio)
            if start < 0:  # placed before beat 0: its head is never heard
                a = a[-start:]
                start = 0
            end = start + len(a)
            if start >= len(buf):
                continue
            if end > len(buf):
                a = a[: len(buf) - start]
                end = len(buf)
            buf[start:end] += a if g == 1.0 else a * g  # x * 1.0 is x
        buf = buf[: total + sr]
        if self.effects:
            buf = fx.chain(buf, self.effects)
        buf = fx.pan(buf, self.pan) if abs(self.pan) > 1e-6 else buf
        return buf if self.gain == 1.0 else buf * self.gain


class Song:
    """
    A beat-based multitrack timeline.

        song = Song(bpm=124)
        song.play('lead', synth.note('C4', 1.0), beat=0)
        song.drum('drums', 'kick', 'x...x...x...x...', bars=4)
        audio = song.render()
    """

    def __init__(self, bpm: float = 120.0, sr: int = SR, beats_per_bar: int = 4):
        self.bpm = float(bpm)
        self.sr = sr
        self.beats_per_bar = beats_per_bar
        self.tracks: dict[str, Track] = {}

    # -- timing ---
    @property
    def beat_seconds(self) -> float:
        return 60.0 / self.bpm

    def beats_to_samples(self, beats: float) -> int:
        return int(round(beats * self.beat_seconds * self.sr))

    def bar(self, index: float) -> float:
        """Beat number at the start of bar `index` (0-based)."""
        return index * self.beats_per_bar

    # -- tracks ---
    def track(
        self,
        name: str,
        gain: float = 1.0,
        pan: float = 0.0,
        effects: list | None = None,
    ) -> Track:
        if name not in self.tracks:
            self.tracks[name] = Track(name, gain, pan, effects or [])
        else:
            t = self.tracks[name]
            t.gain, t.pan = gain, pan
            if effects is not None:
                t.effects = effects
        return self.tracks[name]

    def play(self, track: str, audio: np.ndarray, beat: float, gain: float = 1.0):
        self.track(track).add(audio, self.beats_to_samples(beat), gain)
        return self

    # -- note-level helpers ---
    def notes(
        self,
        track: str,
        synth,
        sequence,
        start_beat: float = 0.0,
        gate: float = 0.9,
        velocity: float = 0.9,
        gain: float = 1.0,
    ):
        """
        sequence: list of (pitch, length_in_beats) or (pitch, length, velocity).
        pitch may be None or '-' for a rest, or a list for a chord.
        A one-voice patch (`voice_mode` mono / legato) plays it as
        `mono_lines` (a chord: its notes in order, the last one kept).
        """
        if getattr(synth.patch, "voice_mode", "poly") != "poly":
            return self._mono_notes(
                track, synth, sequence, start_beat, gate, velocity, gain
            )
        t = start_beat
        prev = None
        # One `synth` renders every note here, and Synth.note/.chord are
        # deterministic (noise is seeded), so two notes agreeing on pitch,
        # length, velocity and glide-from are sample-identical. A loop is
        # mostly repeats — render each distinct note once and reuse the
        # buffer. Track.render only ever reads it, never writes in place.
        # Two passes: collect the distinct notes, render them (in parallel,
        # `render_each`), then place every event.
        jobs: dict[tuple, Callable[[], np.ndarray]] = {}
        placed: list[tuple[tuple, float]] = []
        # a zone map's round robin: which strike of its key a note is (0,
        # and nothing passed, for every other patch)
        strike = Strikes(synth.patch)
        for item in sequence:
            pitch, length = item[0], item[1]
            vel = item[2] if len(item) > 2 else velocity
            if pitch not in (None, "-", "."):
                dur = length * self.beat_seconds
                chord = isinstance(pitch, (list, tuple))
                rr = 0 if chord else strike(pitch)
                key = (tuple(pitch) if chord else pitch, length, vel, prev)
                key += (rr,) if rr else ()
                if key not in jobs:
                    span = note_span(dur, synth.patch)
                    if chord:
                        jobs[key] = partial(
                            synth.chord, pitch, span, vel, gate=dur * gate
                        )
                    else:
                        jobs[key] = partial(
                            synth.note,
                            pitch,
                            span,
                            vel,
                            gate=dur * gate,
                            from_pitch=prev,
                            **({"rr": rr} if rr else {}),
                        )
                if not chord:
                    prev = pitch
                placed.append((key, t))
            t += length
        audio = dict(zip(jobs, render_each(list(jobs.values())), strict=True))
        for key, at in placed:
            self.play(track, audio[key], at, gain)
        return self

    def _mono_notes(self, track, synth, sequence, start_beat, gate, velocity, gain):
        """`notes` for a one-voice patch: its lines, each one render."""
        events, t = [], start_beat
        for item in sequence:
            pitch, length = item[0], item[1]
            vel = item[2] if len(item) > 2 else velocity
            if pitch not in (None, "-", "."):
                dur = length * self.beat_seconds
                for p in pitch if isinstance(pitch, (list, tuple)) else (pitch,):
                    events.append(
                        (t * self.beat_seconds, dur, dur * gate, p, False, vel)
                    )
            t += length
        lines = mono_lines([e[:5] for e in events], synth.patch)
        strike = Strikes(synth.patch)  # each line one strike of its first key
        rrs = [strike(events[ln.first][3]) for ln in lines]
        jobs = [
            partial(
                synth.note,
                events[ln.first][3],
                note_span(ln.dur, synth.patch),
                events[ln.first][5],
                gate=ln.gate,
                from_pitch=ln.from_pitch,
                path=ln.path,
                **({"rr": rr} if rr else {}),
            )
            for ln, rr in zip(lines, rrs, strict=True)
        ]
        for ln, audio in zip(lines, render_each(jobs), strict=True):
            at = events[ln.first][0] / self.beat_seconds
            self.play(track, cut_line(audio, ln.cut, synth.sr), at, gain)
        return self

    def arp(
        self,
        track: str,
        synth,
        pitches,
        start_beat: float,
        beats: float,
        step: float = 0.25,
        mode: str = "up",
        velocity: float = 0.8,
        gate: float = 0.6,
        gain: float = 1.0,
        octaves: int = 1,
    ):
        """Arpeggiate a chord across `beats` beats."""
        seq = list(pitches)
        if octaves > 1:
            seq = [p + 12 * o for o in range(octaves) for p in seq]
        if mode == "down":
            seq = seq[::-1]
        elif mode == "updown":
            seq = seq + seq[-2:0:-1]
        elif mode == "random":
            rng = np.random.default_rng(int(start_beat * 97) + len(seq))
            seq = list(rng.permutation(seq))
        n_steps = int(round(beats / step))
        for i in range(n_steps):
            p = seq[i % len(seq)]
            dur = step * self.beat_seconds
            # the buffer is the gated note plus the patch's own release — the
            # one definition every other scheduler uses (was a flat 2.2x)
            audio = synth.note(
                p, note_span(dur * gate, synth.patch), velocity, gate=dur * gate
            )
            self.play(track, audio, start_beat + i * step, gain)
        return self

    # -- drum patterns ---
    def drum(
        self,
        track: str,
        name: str,
        pattern: str,
        start_beat: float = 0.0,
        step: float = 0.25,
        bars: int = 1,
        gain: float = 1.0,
        accent: str | None = None,
        humanize: float = 0.0,
        **kw,
    ):
        """
        pattern: 'x' = hit, '.' = rest, 'o' = accent, 'g' = ghost (quiet)
        step:    length of one character in beats (0.25 = 16th notes)
        """
        sample = dr.hit(name, **kw)
        # stable, unlike hash(), which Python salts per process: the same
        # humanized pattern rendered differently on every run
        rng = np.random.default_rng(zlib.crc32(f"{name}:{pattern}".encode()))
        levels = {"x": 1.0, "o": 1.25, "g": 0.35, "s": 0.6}
        for b in range(bars):
            base = start_beat + b * len(pattern) * step
            for i, ch in enumerate(pattern):
                if ch in levels:
                    jitter = rng.normal(0, humanize) if humanize else 0.0
                    beat = base + i * step + jitter
                    v = levels[ch]
                    if accent and i < len(accent) and accent[i] == "^":
                        v *= 1.2
                    self.play(track, sample, max(0.0, beat), gain * v)
        return self

    # -- mixdown ---
    def render(
        self,
        master_fx: list | None = None,
        normalize: float = 0.89,
        tail_beats: float = 4.0,
    ) -> np.ndarray:
        end = 0
        for t in self.tracks.values():
            for start, audio, _ in t.events:
                end = max(end, start + len(audio))
        end += self.beats_to_samples(tail_beats)
        mix = np.zeros((end + self.sr, 2))
        for t in self.tracks.values():
            buf = t.render(end, self.sr)
            mix[: len(buf)] += buf
        if master_fx:
            mix = fx.chain(mix, master_fx)
        if normalize:  # the peak only when it is used: a stem renders at 0
            peak = np.max(np.abs(mix)) or 1.0
            mix = mix / peak * normalize
        return mix

    def kick_beats(
        self, pattern: str, bars: int, step: float = 0.25, start_beat: float = 0.0
    ) -> list[float]:
        """Times (in seconds) of each hit — feed to effects.sidechain()."""
        out = []
        for b in range(bars):
            base = start_beat + b * len(pattern) * step
            for i, ch in enumerate(pattern):
                if ch in "xo":
                    out.append((base + i * step) * self.beat_seconds)
        return out
