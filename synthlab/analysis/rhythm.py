"""Tempo and a beat grid for a whole recording (the Sample Lab's analysis).

Pure NumPy. The question the Lab asks is narrow: "what constant grid of
bars and beats lies over this song, so a region can be cut on it?" — so the
answer is a *constant* tempo, a beat phase and a guess at which beat is the
one, not a per-beat tracker that follows a drummer.

1. **Onset strength** (`onset_envelope`): log-magnitude spectral flux in
   log-spaced bands at 100 frames a second, each frame centred on its time.
2. **Coarse tempo**: the envelope's autocorrelation, scored per candidate
   tempo as a comb over 1, 2 and 4 beats and weighted by a log-normal prior
   around 120 bpm — the autocorrelation alone is as happy with half or
   double the tempo, and the prior is what picks an octave.
3. **Phase fit** (`_fold`): the envelope folded at a candidate beat period;
   the right period piles every beat's onsets onto one phase. A search over
   periods on a short stretch, then on longer and longer ones with a finer
   step, gives the period to about a thousandth of a percent on material
   that holds its tempo — and stops early, with what it has, on material
   that drifts (the contrast of the fold falls when the grid stops fitting).
4. **Downbeat** (`_downbeat`): of the beats-per-bar phases, the one where
   the harmony changes most (`key.pitchgram`) and the low band lands hardest. A guess: a
   four-on-the-floor kick says nothing about where "one" is, so the Lab
   lets the user nudge it.

5. **A meter guess** (`_meter_guess`): three beats a bar or
   four, by which bar-level period the beat-by-beat novelty (the same
   harmony change and low-band attack) repeats at more strongly — a cheap
   read for a waltz or a 6/8 felt in two, never a per-bar tracker.
   `Grid.meter_guess` is 3 or 4 (0: too short to say), `meter_sure` how
   much one period beat the other (0..1). The grid itself is still
   `beats_per_bar` (the Lab's choice): the guess only says what it heard.

`confidence` is the fold's contrast (the share of the onset energy that
sits near the beat phase, over what an even spread would put there): a
click track is near 1, a pad, noise or silence near 0. Low confidence
means "set the tempo by hand", never a crash.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise

import numpy as np

from ..core import SR
from .key import pitchgram

FPS = 100  # onset-envelope frames a second
_N_FFT = 1024
_BANDS = 24
BPM_RANGE = (60.0, 200.0)  # what `tempo` answers within
_PRIOR_BPM = 120.0
_PRIOR_OCTAVES = 0.9  # the prior's spread, in octaves
_PHASES = 64  # fold resolution, bins per beat
_STAGES = (24.0, 96.0, 400.0)  # seconds of envelope each refinement folds
# what a frame centred at t reports of an onset at t: the flux peaks in the
# frame whose window first holds the attack, about a hop before it
_ONSET_LAG = 0.011
MAX_SECONDS = 900.0  # the analysis reads no further (a guard, not a UI limit)


@dataclass(frozen=True)
class Grid:
    """A constant beat grid over a recording."""

    bpm: float
    confidence: float  # 0..1: how much of the onset energy sits on the grid
    beat_offset: float  # seconds: the first beat at or after 0
    downbeat_offset: float  # seconds: the first bar line at or after 0 (a guess)
    beats_per_bar: int = 4
    # three or four beats a bar, as heard (`_meter_guess`; 0: no guess),
    # and how far ahead the winner was (0..1)
    meter_guess: int = 0
    meter_sure: float = 0.0

    @property
    def beat_seconds(self) -> float:
        return 60.0 / self.bpm

    @property
    def bar_seconds(self) -> float:
        return self.beat_seconds * self.beats_per_bar

    def beats(self, duration: float) -> np.ndarray:
        """Beat times in [0, duration)."""
        if self.bpm <= 0 or duration <= self.beat_offset:
            return np.zeros(0)
        n = int(math.floor((duration - self.beat_offset) / self.beat_seconds)) + 1
        t = self.beat_offset + np.arange(n) * self.beat_seconds
        return t[t < duration]


NO_GRID = Grid(0.0, 0.0, 0.0, 0.0)
"""What silence, or audio too short to hold two beats, analyses to."""


def _mono(audio: np.ndarray) -> np.ndarray:
    x = np.asarray(audio, dtype=np.float32)
    x = x.mean(axis=1) if x.ndim == 2 else x
    x = x[: int(MAX_SECONDS * SR)]
    # a NaN or an inf in an upload must not reach the answer
    return np.clip(np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0), -4.0, 4.0)


def _band_matrix(sr: int) -> np.ndarray:
    """(bins, bands): each rfft bin's share of each log-spaced band,
    30 Hz .. 16 kHz, plain rectangles (a flux needs no smooth overlap)."""
    freqs = np.fft.rfftfreq(_N_FFT, 1.0 / sr)
    edges = np.geomspace(30.0, min(16000.0, sr / 2), _BANDS + 1)
    idx = np.clip(np.searchsorted(edges, freqs) - 1, -1, _BANDS)
    m = np.zeros((len(freqs), _BANDS), dtype=np.float32)
    ok = (idx >= 0) & (idx < _BANDS)
    m[np.nonzero(ok)[0], idx[ok]] = 1.0
    return m


def band_flux(audio: np.ndarray, sr: int = SR) -> np.ndarray:
    """(frames, bands) half-wave-rectified rise of the log band magnitudes,
    `FPS` frames a second, frame i centred on i / FPS. Computed in blocks:
    a ten-minute song is 60,000 frames, and its whole STFT at once would be
    a quarter of a gigabyte."""
    x = _mono(audio)
    hop = sr // FPS
    if len(x) < _N_FFT:
        return np.zeros((0, _BANDS), dtype=np.float32)
    x = np.concatenate([np.zeros(_N_FFT // 2, np.float32), x])
    n = 1 + (len(x) - _N_FFT) // hop
    bands = _band_matrix(sr)
    win = np.hanning(_N_FFT).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(x, _N_FFT)[::hop][:n]
    logs = np.empty((n, _BANDS), dtype=np.float32)
    for a in range(0, n, 2048):
        mag = np.abs(np.fft.rfft(frames[a : a + 2048] * win, axis=1)).astype(np.float32)
        logs[a : a + 2048] = np.log1p(100.0 * (mag @ bands) / _N_FFT)
    flux = np.zeros_like(logs)
    flux[1:] = np.maximum(logs[1:] - logs[:-1], 0.0)
    return flux


def onset_envelope(audio: np.ndarray, sr: int = SR) -> np.ndarray:
    """Onset strength per frame (`FPS` a second), >= 0: the band flux
    summed, less its own slow average (so a loud passage does not outvote
    a quiet one)."""
    return _envelope(band_flux(audio, sr))


def _envelope(flux: np.ndarray) -> np.ndarray:
    env = flux.sum(axis=1).astype(np.float64)
    if not len(env):
        return env
    k = FPS  # a one-second moving average
    pad = np.pad(env, (k // 2, k - k // 2 - 1), mode="edge")
    mean = np.convolve(pad, np.ones(k) / k, mode="valid")
    return np.maximum(env - mean, 0.0)


def _autocorr(env: np.ndarray, max_lag: int) -> np.ndarray:
    n = len(env)
    size = 1 << int(math.ceil(math.log2(2 * n)))
    spec = np.fft.rfft(env - env.mean(), size)
    ac = np.fft.irfft(spec * np.conj(spec))[: max_lag + 1]
    # unbiased: a long lag overlaps fewer frames
    return ac / np.maximum(n - np.arange(max_lag + 1), 1)


def _at(ac: np.ndarray, lag: float) -> float:
    i = int(math.floor(lag))
    if i + 1 >= len(ac):
        return 0.0
    f = lag - i
    return float(ac[i] * (1 - f) + ac[i + 1] * f)


def _coarse(env: np.ndarray) -> list[tuple[float, float]]:
    """Candidate tempos, best first, as (bpm, score): the autocorrelation
    combed at 1, 2 and 4 beats, times the prior."""
    lo, hi = BPM_RANGE
    max_lag = int(4 * FPS * 60.0 / lo) + 2
    if len(env) < max_lag // 2:
        max_lag = len(env) - 1
    ac = _autocorr(env, max_lag)
    if ac[0] <= 0:
        return []
    ac = ac / ac[0]
    bpms = np.geomspace(lo, hi, 360)
    scores = []
    for bpm in bpms:
        lag = FPS * 60.0 / bpm
        comb = _at(ac, lag) + 0.5 * _at(ac, 2 * lag) + 0.25 * _at(ac, 4 * lag)
        prior = math.exp(-0.5 * (math.log2(bpm / _PRIOR_BPM) / _PRIOR_OCTAVES) ** 2)
        scores.append(comb * prior)
    scores = np.asarray(scores)
    order = [
        i
        for i in np.argsort(-scores)
        if 0 < i < len(bpms) - 1
        and scores[i] >= scores[i - 1]
        and scores[i] >= scores[i + 1]
    ]
    return [(float(bpms[i]), float(scores[i])) for i in order[:4]]


def _fold(env: np.ndarray, period: float) -> np.ndarray:
    """The envelope summed by beat phase (`_PHASES` bins) at a beat period
    of `period` frames, lightly smoothed around the circle."""
    phase = np.mod(np.arange(len(env)) / period, 1.0)
    idx = np.minimum((phase * _PHASES).astype(np.int64), _PHASES - 1)
    f = np.bincount(idx, weights=env, minlength=_PHASES)
    return (np.roll(f, 1) + 2 * f + np.roll(f, -1)) / 4.0


def _contrast(fold: np.ndarray) -> float:
    """0..1: the share of the onset energy within an eighth of a beat of
    the best phase, over what an even spread would put there (a quarter).
    A click track scores 1; noise, folded at whatever period flatters it
    most, about 0.1."""
    total = float(fold.sum())
    if total <= 0:
        return 0.0
    i = int(np.argmax(fold))
    w = _PHASES // 8
    near = float(np.take(fold, np.arange(i - w, i + w + 1), mode="wrap").sum())
    share = near / total
    even = (2 * w + 1) / _PHASES
    return float(np.clip((share - even) / (1.0 - even), 0.0, 1.0))


def _busiest(env: np.ndarray, frames: int) -> np.ndarray:
    """The `frames`-long stretch of the envelope with the most onset energy
    (an intro of pads tells the fold nothing)."""
    if len(env) <= frames:
        return env
    total = np.concatenate([[0.0], np.cumsum(env)])
    step = FPS  # candidate starts, a second apart
    starts = np.arange(0, len(env) - frames + 1, step)
    a = int(starts[np.argmax(total[starts + frames] - total[starts])])
    return env[a : a + frames]


def _refine(env: np.ndarray, period: float) -> tuple[float, float]:
    """(beat period in frames, contrast): the fold's best period near
    `period`, searched on longer and longer stretches. A stage whose
    contrast collapses (the tempo drifts: no one grid fits that much) is
    not taken."""
    best_p, best_c = period, 0.0
    span = 0.03  # +- 3 % around the coarse tempo
    for seconds in _STAGES:
        frames = int(seconds * FPS)
        seg = _busiest(env, frames)
        beats = max(2.0, len(seg) / best_p)
        # a period error of 1/(4 beats) of a period smears the fold by a
        # quarter beat over the stretch: step well inside that
        step = 1.0 / (16.0 * beats)
        steps = int(math.ceil(span / step))
        cand = best_p * (1.0 + np.arange(-steps, steps + 1) * step)
        scores = [float(_fold(seg, p).max()) for p in cand]
        p = float(cand[int(np.argmax(scores))])
        c = _contrast(_fold(seg, p))
        if best_c and c < 0.6 * best_c:
            break
        best_p, best_c = p, c
        if len(seg) == len(env):
            break
        span = 2.0 * step
    return best_p, best_c


def _phase(env: np.ndarray, period: float) -> float:
    """The beat phase, in frames: where in a period the onsets pile up
    (parabolic interpolation around the fold's peak)."""
    f = _fold(env, period)
    i = int(np.argmax(f))
    a, b, c = f[i - 1], f[i], f[(i + 1) % _PHASES]
    den = a - 2 * b + c
    shift = 0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0
    # bin i holds phases [i, i + 1) / _PHASES: its centre is i + 0.5
    return ((i + 0.5 + float(np.clip(shift, -0.5, 0.5))) / _PHASES) * period


def _beat_novelty(
    audio: np.ndarray, sr: int, flux: np.ndarray, beat: float, grid: float, least: int
):
    """Per beat from the first: how much the harmony changed from the beat
    before, and the low band's attack on it — or None for fewer than
    `least` beats. What the downbeat and the meter guess read."""
    gram, fps = pitchgram(audio, sr)
    count = int((len(gram) / fps - beat) / grid)
    if count < least:
        return None
    edges = np.round((beat + np.arange(count + 1) * grid) * fps).astype(int)
    edges = np.clip(edges, 0, len(gram))
    notes = np.array([gram[a : max(a + 1, b)].mean(axis=0) for a, b in pairwise(edges)])
    norm = np.linalg.norm(notes, axis=1)
    change = np.zeros(count)
    den = norm[1:] * norm[:-1]
    cos = np.where(den > 1e-12, (notes[1:] * notes[:-1]).sum(axis=1) / (den + 1e-12), 1)
    change[1:] = 1.0 - cos
    at = np.clip(np.round((beat + np.arange(count) * grid) * FPS).astype(int), 0, None)
    at = at[at < len(flux)]
    w = 3  # +- 30 ms around the beat
    low = np.zeros(count)
    low[: len(at)] = [flux[max(0, i - w) : i + w + 1, :4].sum() for i in at]
    return change, low


def _z(v):
    sd = v.std()
    return (v - v.mean()) / sd if sd > 1e-12 else np.zeros_like(v)


def _downbeat(
    audio: np.ndarray, sr: int, flux: np.ndarray, beat: float, grid: float, per_bar: int
) -> int:
    """Which beat (0 .. per_bar - 1, counted from the first) is the bar
    line: the phase where the harmony changes most from the beat before
    (chords and bass notes turn over on "one"), with the low band's attack
    as a lesser vote. `beat` and `grid` are seconds: the first beat and
    the beat length."""
    got = _beat_novelty(audio, sr, flux, beat, grid, 2 * per_bar)
    if got is None:
        return 0
    change, low = got
    score = _z(change) + 0.5 * _z(low)
    return int(np.argmax([float(score[k::per_bar].mean()) for k in range(per_bar)]))


def _meter_guess(
    audio: np.ndarray, sr: int, flux: np.ndarray, beat: float, grid: float
) -> tuple[int, float]:
    """Three beats a bar or four: the beat-by-beat novelty (`_beat_novelty`)
    correlated with itself one bar on and two bars on, for a bar of three
    and of four; the stronger is the guess, `sure` how far ahead it was
    (0..1). (0, 0.0) under 16 beats."""
    got = _beat_novelty(audio, sr, flux, beat, grid, 16)
    if got is None:
        return 0, 0.0
    change, low = got
    v = _z(change) + 0.5 * _z(low)

    def ac(lag: int) -> float:
        return float(np.mean(v[lag:] * v[:-lag])) if len(v) > lag else 0.0

    three, four = ac(3) + ac(6), ac(4) + ac(8)
    if max(three, four) <= 0:
        return 4, 0.0
    sure = abs(three - four) / (abs(three) + abs(four) + 1e-12)
    return (3 if three > four else 4), round(float(min(1.0, sure)), 3)


def tempo(audio: np.ndarray, sr: int = SR, beats_per_bar: int = 4) -> Grid:
    """The constant beat grid that fits `audio` best (mono or stereo at
    `sr`): its tempo within `BPM_RANGE`, how sure the fit is, and where the
    first beat and the first bar line fall. Silence, or audio under about
    two seconds, is `NO_GRID` (bpm 0, confidence 0)."""
    flux = band_flux(audio, sr)
    env = _envelope(flux)
    if len(env) < 2 * FPS or float(env.max()) <= 1e-9:
        return NO_GRID
    cands = _coarse(env)
    if not cands:
        return NO_GRID
    period, contrast = _refine(env, FPS * 60.0 / cands[0][0])
    # how sure: the tightest of the beat, eighth and sixteenth folds. Hats and
    # bass notes between the beats are on the grid too, and counted against
    # the beat phase alone they made a clean loop read as a weak one.
    contrast = max(_contrast(_fold(env, period / d)) for d in (1, 2, 4))
    # frames the fold was fitted on drift least near the busiest stretch,
    # but the phase is the whole recording's
    phase = _phase(env, period)
    beat = (phase / FPS - _ONSET_LAG) % (period / FPS)
    per_bar = max(1, int(beats_per_bar))
    k = _downbeat(audio, sr, flux, beat, period / FPS, per_bar)
    guess, sure = _meter_guess(audio, sr, flux, beat, period / FPS)
    bpm = 60.0 * FPS / period
    return Grid(
        bpm=round(bpm, 3),
        confidence=round(contrast, 3),
        beat_offset=round(beat, 4),
        downbeat_offset=round(beat + k * period / FPS, 4),
        beats_per_bar=per_bar,
        meter_guess=guess,
        meter_sure=sure,
    )
