"""Musical key of a recording (the Sample Lab's analysis): a chroma vector
from the STFT's spectral peaks, matched against the Krumhansl-Schmuckler
key profiles.

NumPy and SciPy only. Pitch is read from the audio decimated to 5.5 kHz
(nothing above 2 kHz is asked of it) with a 186 ms window, 5.4 Hz a bin,
so neighbouring semitones separate down to the bass register. Only each
frame's *peaks* count — local maxima well above the frame's median, in a
spectrogram first held to its own median over half a second — because a
drum hit is a wall of bins with no lasting pitch, and summing it in
flattens the chroma toward "every key at once". A peak counts for its note
(A = 440 Hz; a recording tuned a quarter-tone off will read less sure, not
wrong).

The answer is the best of the 24 rotated profiles by Pearson correlation,
and then one more question the profiles answer badly: a key and its
relative (F major / D minor) share all seven notes, so which of the two it
is comes down to where the music *rests*. Given the **bass** on its own (a
separated stem) that is asked of the bass line — `tonic_weights`: the pitch
class the bass plays, counted harder on bar and phrase downbeats and over
the final bars, the places a bass states the tonic — and a relative within
`_RELATIVE_REACH` takes the key when the bass sits on its tonic instead
(`_BASS_PULL`). Without a bass, a near-tie (`_RELATIVE_TIE`) goes to the
one whose tonic is the stronger note of the whole chroma. The bass is asked
about the relative pair and nothing else: over all 24 keys "the bass's
favourite note" is as often the fifth or the flat seventh as the tonic.

`confidence` is about the *scale*: how well the better of the pair fits,
times how far that stands above the best key that is neither its relative
nor its parallel, 0..1. `margin` is about the *tonic*: how far the answer
leads its relative, in correlation units after the bass's say (negative
when the chroma tie rule overruled the raw scores). `scores` keeps the
ranking, the relative second, so a caller can show "A minor (or C major)".

Known limits, measured while building it (`tests/test_song_analysis.py`):
a long, driven kick is a sustained note with harmonics (52 Hz puts a D# at
156 Hz) and can flip C major to C minor; a sub oscillator two octaves under
`LOW_NOTE` leaves its fifth harmonic — a major third — uncancelled; and a
real song in D minor read as its relative, F major — by 0.03 on the mix
(the tie rule caught it) and by 0.08 without its drums (nothing did), which
is what the bass cue is for: its bass plays D a fifth of the time and F
almost never. The Lab shows the reading as a starting point the user can
overrule, and re-reads it from the stems once they exist.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import resample_poly

from ..core import SR

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MODES = ("major", "minor")
# Krumhansl & Kessler's probe-tone ratings (1982), tonic first
_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
_DECIMATE = 8  # read pitch at 5.5 kHz: everything under 2 kHz, an eighth the work
_N_FFT = 1024  # at the decimated rate: 186 ms, 5.4 Hz a bin
_HOP = 256  # 46 ms
# MIDI C2 (65 Hz) .. C7. Not lower: a four-on-the-floor kick (45-60 Hz)
# would be the loudest "note" in the song
LOW_NOTE, HIGH_NOTE = 36, 96
_SUSTAIN_FRAMES = 11  # ~0.5 s: the median over time that tells a note from a hit
MAX_SECONDS = 900.0
_COMPRESS = 10.0  # log1p(_COMPRESS * amplitude / the loud notes' level)
_RELATIVE_TIE = 0.05  # correlation: closer than this, the tonic's weight decides
_RELATIVE_REACH = 0.2  # correlation: a relative this close can win on the bass
_BASS_PULL = 1.0  # correlation a tonic gains for a bass that plays nothing else
# how much harder a bass frame counts on the first beat of a bar, of every
# fourth bar, and over the last bars it plays (each added to 1)
_DOWNBEAT, _PHRASE, _FINAL = 2.0, 2.0, 2.0
_PHRASE_BARS = 4
_FINAL_BARS = 4
_BASS_FLOOR = 0.1  # of the loud frames' level: quieter is not a bass note


@dataclass(frozen=True)
class Key:
    tonic: str  # one of NOTE_NAMES, "" when nothing was heard
    mode: str  # "major" | "minor"
    confidence: float  # 0..1: how sure the scale is
    scores: tuple[tuple[str, float], ...] = ()  # ("A minor", r): the key, then rivals
    margin: float = 0.0  # its lead over its relative (correlation, bass included)
    tonic_from: str = ""  # "bass" when the bass line settled the relative pair

    @property
    def name(self) -> str:
        return f"{self.tonic} {self.mode}" if self.tonic else ""


NO_KEY = Key("", "major", 0.0)
"""What silence or unpitched audio analyses to."""


def midi_hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69.0) / 12.0)


def pitchgram(audio: np.ndarray, sr: int = SR) -> tuple[np.ndarray, float]:
    """(frames x notes, frames per second): how much of each semitone
    (`LOW_NOTE`..`HIGH_NOTE`) each frame holds — the amplitude of its
    spectral peaks, each on the note nearest its interpolated frequency.
    Frame i is centred on i / fps. The beat tracker reads the same picture
    for its bar line (`rhythm._downbeat`: where the harmony changes)."""
    x = np.asarray(audio, dtype=np.float32)
    x = x.mean(axis=1) if x.ndim == 2 else x
    x = x[: int(MAX_SECONDS * sr)]
    x = np.clip(np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0), -4.0, 4.0)
    notes = HIGH_NOTE - LOW_NOTE + 1
    rate = sr / _DECIMATE
    fps = rate / _HOP
    if len(x) < _DECIMATE * 16:
        return np.zeros((0, notes)), fps
    x = resample_poly(x, 1, _DECIMATE).astype(np.float32)
    x = np.concatenate(
        [np.zeros(_N_FFT // 2, np.float32), x, np.zeros(_N_FFT, np.float32)]
    )
    df = rate / _N_FFT
    lo = max(1, int(midi_hz(LOW_NOTE - 0.5) / df))
    hi = min(_N_FFT // 2 - 1, int(midi_hz(HIGH_NOTE + 0.5) / df) + 1)
    win = np.hanning(_N_FFT).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(x, _N_FFT)[::_HOP]
    band = np.empty((len(frames), hi - lo + 2), dtype=np.float32)
    floor = np.empty((len(frames), 1), dtype=np.float32)
    for a in range(0, len(frames), 1024):
        mag = np.abs(np.fft.rfft(frames[a : a + 1024] * win, axis=1))
        band[a : a + 1024] = mag[:, lo - 1 : hi + 1]
        floor[a : a + 1024] = 4.0 * np.median(mag, axis=1, keepdims=True) + 1e-7
    # what holds for half a second is a note; what does not is a drum. A
    # kick is a pitched thump and a snare rings near 200 Hz: each bin keeps
    # no more than its own median over time, which a short hit never moves
    band = np.minimum(band, median_filter(band, size=(_SUSTAIN_FRAMES, 1)))
    mid, left, right = band[:, 1:-1], band[:, :-2], band[:, 2:]
    fr, k = np.nonzero((mid > left) & (mid >= right) & (mid > floor))
    out = np.zeros((len(frames), notes))
    if len(fr):
        # a bin is 5.4 Hz and a semitone at A1 is 3.3: place each peak by
        # the parabola through its log magnitude and its neighbours'
        la, lb, lc = (np.log(m[fr, k] + 1e-12) for m in (left, mid, right))
        den = la - 2.0 * lb + lc
        shift = np.where(np.abs(den) > 1e-9, 0.5 * (la - lc) / den, 0.0)
        hz = (lo + k + np.clip(shift, -0.5, 0.5)) * df
        note = np.round(69.0 + 12.0 * np.log2(hz / 440.0)).astype(int) - LOW_NOTE
        ok = (note >= 0) & (note < notes)
        np.add.at(out, (fr[ok], note[ok]), mid[fr, k][ok])
    return out, fps


# semitones above a note where its harmonics 2..8 land, and how much of the
# note's own amplitude each is taken to be (a driven bass is richer than a
# sawtooth's 1/h, so twice that, capped at 1)
_HARMONICS = tuple((round(12 * np.log2(h)), min(1.0, 2.0 / h)) for h in range(2, 9))
# ... and where they land when the note is itself the octave of a
# fundamental under `LOW_NOTE` (a sub oscillator at 27 Hz is out of the
# picture's reach, its fifth harmonic is not): the major third most of all
_SUB_HARMONICS = ((7, 0.67), (16, 0.4), (22, 0.29))


def _fundamentals(gram: np.ndarray) -> np.ndarray:
    """The pitchgram with each note's harmonics taken back out, low notes
    first. A harmonic series is not a chord: an A's fifth harmonic is a C#,
    and a bass line on A, D and E so spells out A major (C#, F#, G#) over
    whatever the chords are doing — it turned every minor key major."""
    out = gram.copy()
    for n in range(out.shape[1]):
        for semis, share in _HARMONICS + _SUB_HARMONICS:
            if n + semis < out.shape[1]:
                out[:, n + semis] = np.maximum(
                    out[:, n + semis] - share * out[:, n], 0.0
                )
    return out


def chroma(audio: np.ndarray, sr: int = SR) -> np.ndarray:
    """The recording's 12 pitch-class weights (C first), summing to 1, or
    zeros when it holds no pitched peak: the fundamentals (`_fundamentals`),
    log-compressed, summed over time and octaves."""
    gram, _fps = pitchgram(audio, sr)
    if not len(gram):
        return np.zeros(12)
    notes = _fundamentals(gram)
    loud = notes[notes > 0]
    if not len(loud):
        return np.zeros(12)
    # compressed: a bass line is the loudest thing in most mixes and plays
    # roots only, and at face value its I-IV-V outvotes the thirds the
    # chords carry (it read every minor loop as major)
    per_note = np.log1p(_COMPRESS * notes / np.percentile(loud, 95)).sum(axis=0)
    pc = np.mod(np.arange(LOW_NOTE, HIGH_NOTE + 1), 12)
    out = np.bincount(pc, weights=per_note, minlength=12)
    total = out.sum()
    return out / total if total > 0 else out


def _related(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Relative (C major / A minor) or parallel (C major / C minor)."""
    (ta, ma), (tb, mb) = a, b
    if ma == mb:
        return False
    if ta == tb:
        return True
    major, minor = (ta, tb) if ma == 0 else (tb, ta)
    return (major - minor) % 12 == 3


def tonic_weights(
    bass: np.ndarray, sr: int = SR, bar: float = 0.0, offset: float = 0.0
) -> np.ndarray:
    """Where a bass line rests: 12 pitch-class weights (C first) summing to
    1, or zeros when the audio holds no bass note. Each frame counts for
    its strongest fundamental — a bass plays one note at a time — and
    counts harder where a bass states the tonic: the first beat of a bar
    (`bar` seconds long, the first at `offset`), of every fourth bar, and
    the last bars it plays. Without a grid (`bar` <= 0) every frame counts
    the same.

    A note under `LOW_NOTE` is read by its octave, which is in reach and
    the same pitch class."""
    gram, fps = pitchgram(bass, sr)
    if not len(gram):
        return np.zeros(12)
    notes = _fundamentals(gram)
    level = notes.max(axis=1)
    if not np.any(level > 0):
        return np.zeros(12)
    sounding = level > _BASS_FLOOR * np.percentile(level[level > 0], 90)
    if not sounding.any():
        return np.zeros(12)
    weight = np.ones(len(notes))
    if np.isfinite(bar) and np.isfinite(offset) and bar > 0:
        at = (np.arange(len(notes)) / fps - offset) / bar
        index = np.floor(at).astype(int)
        first_beat = (at - index) < 0.25
        weight += _DOWNBEAT * first_beat
        weight += _PHRASE * (first_beat & (index % _PHRASE_BARS == 0))
        last = index[np.flatnonzero(sounding)[-1]]
        weight += _FINAL * (index > last - _FINAL_BARS)
    pc = np.mod(LOW_NOTE + notes.argmax(axis=1), 12)
    out = np.bincount(pc[sounding], weights=weight[sounding], minlength=12)
    return out / out.sum()


def detect_key(
    audio: np.ndarray,
    sr: int = SR,
    bass: np.ndarray | None = None,
    bar: float = 0.0,
    offset: float = 0.0,
) -> Key:
    """The key of `audio` (mono or stereo at `sr`): the Krumhansl-Schmuckler
    profile that correlates best with its chroma. `NO_KEY` for silence and
    for audio with no pitched peaks (a drum loop reads low confidence, or
    nothing).

    `bass` — the bass line alone, with the bar grid it is played on when
    there is one — settles the one question the profiles leave open, the
    key or its relative (the module's docstring)."""
    c = chroma(audio, sr)
    if c.sum() <= 0:
        return NO_KEY
    ranked = []
    for mode, prof in enumerate((_MAJOR, _MINOR)):
        p = np.asarray(prof)
        for tonic in range(12):
            r = float(np.corrcoef(c, np.roll(p, tonic))[0, 1])
            ranked.append((r if np.isfinite(r) else -1.0, tonic, mode))
    ranked.sort(reverse=True)
    fit, tonic, mode = ranked[0]
    # its relative: the minor a minor third under a major, and back
    rel = ((tonic + 9) % 12, 1) if mode == 0 else ((tonic + 3) % 12, 0)
    at = next(i for i, (_r, t, m) in enumerate(ranked) if (t, m) == rel)
    lead = fit - ranked[at][0]
    rest = tonic_weights(bass, sr, bar, offset) if bass is not None else np.zeros(12)
    tonic_from = ""
    if rest.sum() > 0 and lead < _RELATIVE_REACH:
        # the bass line's resting note, asked of this pair and no other
        lead += _BASS_PULL * float(rest[tonic] - rest[rel[0]])
        swap = lead < 0
        tonic_from = "bass"
    elif rest.sum() > 0:
        swap = False  # the relative is nowhere near: nothing to settle
    else:
        # no bass to ask: within `_RELATIVE_TIE` the one whose own tonic
        # the recording leans on harder is the key
        swap = lead < _RELATIVE_TIE and c[rel[0]] > c[tonic]
    ranked.insert(1, ranked.pop(at))  # the pair first: "D minor (or F major)"
    if swap:
        ranked[0], ranked[1] = ranked[1], ranked[0]
        tonic, mode = rel
        lead = -lead
    rival = next(
        (r for r, t, m in ranked[2:] if not _related((tonic, mode), (t, m))), -1.0
    )
    # the scale: how well the better of the pair fits, times how far that
    # leads the next unrelated key
    confidence = float(np.clip(fit, 0.0, 1.0) * np.clip((fit - rival) / 0.25, 0.0, 1.0))
    scores = tuple(
        (f"{NOTE_NAMES[t]} {MODES[m]}", round(r, 3)) for r, t, m in ranked[:4]
    )
    return Key(
        NOTE_NAMES[tonic],
        MODES[mode],
        round(confidence, 3),
        scores,
        round(float(lead), 3),
        tonic_from,
    )
