"""A sung line read for the sampler (the Sample Lab's "vocal instrument"
and "vocal chops"): where the voice holds one note, and where its syllables
start.

NumPy only, on top of the sampler's own YIN frame (`sampler._yin`).

- **`pitch_track`** runs YIN every 11.6 ms over the voice's range and keeps,
  per frame, the note (a MIDI float), how periodic the frame was (1 - the
  normalised difference at the period: `clarity`) and its level. A frame is
  *voiced* when it is periodic and not much quieter than the line's loud
  frames — a breath, a consonant and the reverb between words are none of
  the three.
- **`steady_runs`** walks the voiced frames and cuts a run wherever the
  pitch leaves `STEADY_SEMITONES` of the run's own median: a held note with
  its vibrato is one run, a scoop into it or a slide out is not part of it.
  `best_run` is the one to build an instrument on: the longest, weighted by
  how periodic it is. The whole region's median pitch is the wrong answer
  for a melody (it is whichever note was sung most), and its start is
  usually a consonant.
- **`syllables`** cuts a line into spans to put on keys: the level gate
  finds the phrases (a vocal stem really is silent between them, which a
  mix never is), and the onset strength inside a phrase splits it where a
  new syllable starts. Spans, not just starts: a chop that ran on through
  the silence to the next word would play that silence.

Every number here is about one voice on its own. On a full mix the pitch
track follows whatever is loudest and the gate never closes; the Lab says
so and uses the vocal stem when there is one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core import SR
from ..sampler import _yin
from .rhythm import FPS, onset_envelope

VOICE_HZ = (70.0, 1100.0)  # a low male chest note .. a soprano's top
_WINDOW = 2048  # the YIN window: three periods of the lowest note
_HOP = 512
CLEAR = 0.8  # clarity (1 - YIN's dip) from which a frame is voiced
_GATE_DB = -30.0  # below the loud frames' level, a frame is not the voice
STEADY_SEMITONES = 0.6  # a run holds within this of its own median (vibrato does)
MIN_RUN_SECONDS = 0.12  # shorter is a passing note, not one to loop
# syllables
_PHRASE_GATE_DB = -36.0  # under the line's peak level: silence between phrases
_MIN_GAP = 0.08  # seconds of silence that end a phrase
_MIN_SPAN = 0.06  # seconds: shorter is a click or a breath's edge
_MIN_SYLLABLE = 0.12  # seconds between two syllable starts
_ONSET_FLOOR = 0.25  # of the phrase's strongest onset: weaker is no new syllable


@dataclass(frozen=True)
class Track:
    notes: np.ndarray  # MIDI float per frame
    clarity: np.ndarray  # 0..1 per frame
    voiced: np.ndarray  # bool per frame
    level: np.ndarray  # per frame, 1 = the line's loud frames
    hop: int  # samples between frames
    span: int  # samples one frame reads


@dataclass(frozen=True)
class Run:
    """A stretch of one held note, in samples of the audio it was found in."""

    start: int
    end: int
    midi: float  # the run's median pitch
    clarity: float  # mean periodicity, 0..1
    wobble: float  # the pitch's standard deviation, semitones
    level: float = 1.0  # against the line's loud frames, at most 1

    @property
    def seconds(self) -> float:
        return (self.end - self.start) / SR

    @property
    def root(self) -> int:
        """The key the run plays at its own pitch."""
        return min(127, max(0, round(self.midi)))

    @property
    def cents(self) -> float:
        """How far the sung note sits from that key (+ is sharp)."""
        return (self.midi - round(self.midi)) * 100.0


def _mono(audio: np.ndarray) -> np.ndarray:
    x = np.asarray(audio, dtype=np.float64)
    x = x.mean(axis=1) if x.ndim == 2 else x.ravel()
    return np.clip(np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0), -4.0, 4.0)


def pitch_track(audio: np.ndarray, sr: int = SR) -> Track:
    """The line's pitch, frame by frame (the module's docstring). An empty
    track for audio shorter than one frame."""
    mono = _mono(audio)
    tau_min = int(sr / VOICE_HZ[1])
    tau_max = int(math.ceil(sr / VOICE_HZ[0]))
    span = _WINDOW + tau_max
    starts = np.arange(0, len(mono) - span + 1, _HOP)
    empty = np.zeros(0)
    if not len(starts):
        return Track(empty, empty, empty.astype(bool), empty, _HOP, span)
    energy = np.concatenate([[0.0], np.cumsum(mono * mono)])
    rms = np.sqrt(np.maximum(energy[starts + span] - energy[starts], 0.0) / span)
    loud = float(np.percentile(rms, 95))
    audible = rms > max(loud * 10.0 ** (_GATE_DB / 20.0), 1e-4)
    notes = np.full(len(starts), np.nan)
    clarity = np.zeros(len(starts))
    for i in np.flatnonzero(audible):
        s = starts[i]
        period, dip = _yin(mono[s : s + span], _WINDOW, tau_min, tau_max)
        notes[i] = 69.0 + 12.0 * math.log2(sr / period / 440.0)
        clarity[i] = min(1.0, max(0.0, 1.0 - dip))
    voiced = audible & (clarity >= CLEAR)
    level = np.minimum(rms / max(loud, 1e-9), 1.0)
    return Track(notes, clarity, voiced, level, _HOP, span)


def steady_runs(track: Track, sr: int = SR) -> list[Run]:
    """Every held note in the track, in time order: maximal runs of voiced
    frames that stay within `STEADY_SEMITONES` of their own median, at
    least `MIN_RUN_SECONDS` long."""
    out: list[Run] = []
    n = len(track.notes)
    need = max(2, int(math.ceil(MIN_RUN_SECONDS * sr / track.hop)))
    i = 0
    while i < n:
        if not track.voiced[i]:
            i += 1
            continue
        j = i + 1
        centre = track.notes[i]
        while j < n and track.voiced[j]:
            if abs(track.notes[j] - centre) > STEADY_SEMITONES:
                break
            j += 1
            centre = float(np.median(track.notes[i:j]))
        if j - i >= need:
            held = track.notes[i:j]
            out.append(
                Run(
                    start=i * track.hop,
                    # the last frame's window, less the lag YIN looked ahead by
                    end=(j - 1) * track.hop + _WINDOW,
                    midi=float(np.median(held)),
                    clarity=float(np.mean(track.clarity[i:j])),
                    wobble=float(np.std(held)),
                    level=float(np.mean(track.level[i:j])),
                )
            )
        i = j
    return out


def best_run(audio: np.ndarray, sr: int = SR) -> Run | None:
    """The held note to build an instrument on: the longest steady run,
    each second of it weighted by how periodic and how loud it was (the
    tail of a note dying away is long and steady, and a poor thing to
    loop). None when the audio holds no steady pitched note (speech, a
    whisper, drums, silence)."""
    runs = steady_runs(pitch_track(audio, sr), sr)
    if not runs:
        return None
    return max(runs, key=lambda r: (r.seconds * r.clarity * r.level, -r.start))


def _phrases(mono: np.ndarray, sr: int) -> list[tuple[int, int]]:
    """Spans (samples) where the line is above the gate, short gaps bridged
    and scraps dropped."""
    hop = sr // FPS
    frames = len(mono) // hop
    if not frames:
        return []
    level = np.sqrt(np.mean(mono[: frames * hop].reshape(frames, hop) ** 2, axis=1))
    peak = float(np.percentile(level, 98))
    if peak < 1e-4:
        return []
    on = level > peak * 10.0 ** (_PHRASE_GATE_DB / 20.0)
    edges = np.flatnonzero(np.diff(np.concatenate([[0], on.astype(int), [0]])))
    spans = [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2], strict=True)]
    merged: list[tuple[int, int]] = []
    for a, b in spans:
        if merged and a - merged[-1][1] < _MIN_GAP * FPS:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(a * hop, b * hop) for a, b in merged if b - a >= _MIN_SPAN * FPS]


def syllables(
    audio: np.ndarray, max_spans: int = 32, sr: int = SR
) -> list[tuple[int, int]]:
    """The line as spans to chop, (start, end) in samples, in time order:
    each phrase the level gate finds, split where the onset strength says a
    new syllable starts. When there are more than `max_spans` the weakest
    splits are undone first (a phrase is never dropped for a syllable)."""
    mono = _mono(audio)
    phrases = _phrases(mono, sr)
    if not phrases:
        return []
    env = onset_envelope(mono, sr)
    hop = sr // FPS
    gap = int(_MIN_SYLLABLE * FPS)
    cuts: list[tuple[float, int, int]] = []  # (strength, phrase index, sample)
    for p, (a, b) in enumerate(phrases):
        lo, hi = a // hop + gap, min(len(env), b // hop) - gap // 2
        seg = env[a // hop : min(len(env), b // hop)]
        if hi <= lo or not len(seg) or seg.max() <= 0:
            continue
        floor = _ONSET_FLOOR * float(seg.max())
        last = -gap
        for i in range(lo, hi):
            peak = env[i] > floor and env[i] >= env[i - 1] and env[i] > env[i + 1]
            if peak and i - last >= gap:
                # the flux rises a frame after the sound does
                cuts.append((float(env[i]), p, max(a, (i - 1) * hop)))
                last = i
    cuts.sort(reverse=True)
    kept = sorted(cuts[: max(0, max_spans - len(phrases))], key=lambda c: c[2])
    out: list[tuple[int, int]] = []
    for p, (a, b) in enumerate(phrases[:max_spans]):
        marks = [a, *(s for _e, q, s in kept if q == p), b]
        out.extend((s, e) for s, e in zip(marks, marks[1:], strict=False) if e > s)
    return out[:max_spans]


# --- the sung melody, sample by sample (the Voice lab's "follow" carrier) -----

_MELODY_MEDIAN = 5  # voiced frames: an octave blip is one or two
_MELODY_RUN = 3  # voiced frames in a row (35 ms) before any of them is a note
# On a real vocal stem YIN lands an octave (or an octave and a fifth) off
# for a few frames at a time — 3 to 15% of the voiced frames of "Circles",
# in runs of 3-4 (at most 14). A voiced frame further than this from the
# median of the `_MELODY_AROUND` voiced frames either side (half a second
# in all) is such an error, not a note.
_MELODY_LEAP = 7.0  # semitones
_MELODY_AROUND = 21  # voiced frames either side


def _running_median(x: np.ndarray, width: int) -> np.ndarray:
    """The median of `width` (odd) values centred on each, the ends padded
    with their own value."""
    padded = np.pad(x, width // 2, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, width), axis=1)


@dataclass(frozen=True)
class Melody:
    midi: np.ndarray  # the note per sample of the audio (a MIDI float)
    centre: float  # the median sung note
    voiced: float  # the share of the audio's frames that were voiced, 0..1


def snap(notes: np.ndarray, pitch_classes) -> np.ndarray:
    """Each note moved to the nearest note whose pitch class is allowed
    (0 = C); a tie goes down. No allowed class: the notes as they are."""
    allowed = sorted({int(pc) % 12 for pc in pitch_classes})
    if not allowed:
        return np.asarray(notes, dtype=np.float64)
    grid = np.array([o * 12 + pc for o in range(-1, 12) for pc in allowed], float)
    at = np.clip(np.searchsorted(grid, notes), 1, len(grid) - 1)
    below, above = grid[at - 1], grid[at]
    return np.where(notes - below <= above - notes, below, above)


def melody(
    audio: np.ndarray, pitch_classes=None, glide: float = 0.03, sr: int = SR
) -> Melody | None:
    """The line's pitch as a per-sample curve a synth can follow: the
    voiced frames of `pitch_track`, the ones far off the line around them
    put back on it (`_MELODY_LEAP`), a median over `_MELODY_MEDIAN` of
    them, each unvoiced frame holding the last sung note
    (the first ones the first), snapped to `pitch_classes` when given
    (`snap`), then eased over `glide` seconds so a step from one note to
    the next is a slide rather than a jump. None when nothing in the audio
    is voiced (speech without pitch, drums, silence).

    A voiced frame counts only inside a run of `_MELODY_RUN`: the edge of
    a noise burst reads as one perfectly periodic frame on its own (a
    frame that is mostly silence), which is no note."""
    track = pitch_track(audio, sr)
    sung = track.voiced.astype(int)
    edges = np.flatnonzero(np.diff(np.concatenate([[0], sung, [0]])))
    keep = np.zeros(len(sung), dtype=bool)
    for a, b in zip(edges[::2], edges[1::2], strict=True):
        keep[a:b] = b - a >= _MELODY_RUN
    at = np.flatnonzero(keep)
    if not len(at):
        return None
    notes = track.notes[at]
    # a frame more than `_MELODY_LEAP` from everything sung around it is a
    # tracking error (an octave, an octave and a fifth): it takes the
    # surrounding line's note. The window is centred, so a real leap that
    # is held keeps its place to the frame
    around = _running_median(notes, 2 * _MELODY_AROUND + 1)
    notes = np.where(np.abs(notes - around) > _MELODY_LEAP, around, notes)
    notes = _running_median(notes, _MELODY_MEDIAN)
    if pitch_classes is not None:
        notes = snap(notes, pitch_classes)
    # every frame takes the last voiced frame's note (the first voiced one
    # before any): a step function over the frames
    last = np.maximum(np.searchsorted(at, np.arange(len(track.notes)), "right") - 1, 0)
    frames = notes[last]
    n = len(_mono(audio))
    centres = np.arange(len(frames)) * track.hop + _WINDOW // 2
    if pitch_classes is None:
        curve = np.interp(np.arange(n), centres, frames)
    else:  # a snapped line steps between frames; the glide below slides it
        edges = (centres[:-1] + centres[1:]) // 2
        curve = frames[np.searchsorted(edges, np.arange(n), "right")]
    if glide > 0:
        from ..core import one_pole

        coeff = math.exp(-1.0 / (glide / 3.0 * sr))  # settled after `glide`
        curve = one_pole(curve - curve[0], coeff) + curve[0]
    return Melody(curve, float(np.median(notes)), len(at) / len(track.notes))
