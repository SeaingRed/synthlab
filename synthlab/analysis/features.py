"""Audio -> a small vector of perceptually meaningful numbers.

Pure functions: same audio in, same Features out. Used by patch breeding
(scoring candidates), the sound-space map (PCA input), and the studio UI
(meters: the lean `meter`). NumPy, plus scipy's FFT for `meter`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

import numpy as np
from scipy import fft as sp_fft

from ..core import SR

FEATURE_NAMES = [
    "centroid",
    "rolloff",
    "flatness",
    "crest",
    "sub_20_60",
    "sub_60_120",
    "low_120_250",
    "attack_time",
    "stereo_width",
]

_EPS = 1e-12


@dataclass
class Features:
    centroid: float
    rolloff: float
    flatness: float
    crest: float
    sub_20_60: float
    sub_60_120: float
    low_120_250: float
    attack_time: float
    stereo_width: float

    def to_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in asdict(self).items()}

    def vector(self) -> np.ndarray:
        """Fixed-order vector for PCA. Hz-scaled fields go log2 so a
        100 Hz shift near 200 Hz counts like a 1 kHz shift near 2 kHz."""
        d = self.to_dict()
        out = []
        for name in FEATURE_NAMES:
            v = d[name]
            if name in ("centroid", "rolloff"):
                v = float(np.log2(max(v, 20.0)))
            out.append(v)
        return np.asarray(out, dtype=np.float64)


assert [f.name for f in fields(Features)] == FEATURE_NAMES


def _band_fraction(freqs, power, lo, hi):
    total = float(np.sum(power)) + _EPS
    m = (freqs >= lo) & (freqs < hi)
    return float(np.sum(power[m])) / total


def _moving_average(x: np.ndarray, win: int) -> np.ndarray:
    """`np.convolve(x, np.ones(win) / win, mode="same")` from a running
    sum: O(n) instead of O(n * win) (~9x faster on a loop at a 5 ms
    window). Same windows, same edges (the kernel hangs off both ends and
    the missing samples count as zero); only float rounding differs."""
    n = len(x)
    if n < win:  # "same" is then as long as the kernel: keep numpy's
        return np.convolve(x, np.ones(win) / win, mode="same")
    csum = np.concatenate([[0.0], np.cumsum(x)])
    i = np.arange(n)
    hi = np.minimum(i + (win - 1) // 2, n - 1) + 1
    lo = np.maximum(i + (win - 1) // 2 - win + 1, 0)
    return (csum[hi] - csum[lo]) / win


def _attack_time(mono: np.ndarray, sr: int) -> float:
    win = max(1, sr // 200)  # 5 ms envelope follower
    env = _moving_average(np.abs(mono), win)
    peak = float(np.max(env))
    if peak < _EPS:
        return 0.0
    idx = int(np.argmax(env >= 0.9 * peak))
    return idx / sr


def meter(audio: np.ndarray, sr: int = SR) -> tuple[float, float]:
    """The two spectral numbers the studio's meter shows, `(sub, centroid)`:
    the power share below 120 Hz (`sub_20_60 + sub_60_120`) and the
    spectral centroid, from ONE real FFT of the Hann-windowed mono mix
    zero-padded to `scipy.fft.next_fast_len`. `extract` computes all nine
    features at the signal's own length (an FFT whose size can be a large
    prime); a render's meter needs two, so every loop render paid for the
    other seven. Zero padding only interpolates the spectrum, so both
    agree with `extract` to within its bin spacing."""
    x = np.asarray(audio, dtype=np.float64)
    mono = x.mean(axis=1) if x.ndim == 2 else x
    if mono.size == 0:
        return 0.0, 0.0
    nfft = sp_fft.next_fast_len(len(mono), real=True)
    power = np.abs(sp_fft.rfft(mono * np.hanning(len(mono)), n=nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    total = float(np.sum(power)) + _EPS
    centroid = float(np.sum(freqs * power) / total)
    sub = _band_fraction(freqs, power, 20, 120)
    return sub, centroid


def extract(audio: np.ndarray, sr: int = SR) -> Features:
    x = np.asarray(audio, dtype=np.float64)
    if x.ndim == 1:
        stereo = np.column_stack([x, x])
    else:
        stereo = x
    mono = stereo.mean(axis=1)

    windowed = mono * np.hanning(len(mono))
    mag = np.abs(np.fft.rfft(windowed))
    power = mag**2
    freqs = np.fft.rfftfreq(len(mono), 1 / sr)

    total = float(np.sum(power)) + _EPS
    centroid = float(np.sum(freqs * power) / total)

    cum = np.cumsum(power) / total
    rolloff = float(freqs[int(np.argmax(cum >= 0.95))])

    audible = (freqs >= 20) & (freqs <= 16_000)
    p = power[audible] + _EPS
    flatness = float(np.exp(np.mean(np.log(p))) / np.mean(p))

    rms = float(np.sqrt(np.mean(mono**2))) + _EPS
    peak = float(np.max(np.abs(mono))) + _EPS
    crest = float(20 * np.log10(peak / rms))

    if np.std(stereo[:, 0]) > _EPS and np.std(stereo[:, 1]) > _EPS:
        corr = float(np.corrcoef(stereo[:, 0], stereo[:, 1])[0, 1])
    else:
        corr = 1.0

    return Features(
        centroid=centroid,
        rolloff=rolloff,
        flatness=flatness,
        crest=crest,
        sub_20_60=_band_fraction(freqs, power, 20, 60),
        sub_60_120=_band_fraction(freqs, power, 60, 120),
        low_120_250=_band_fraction(freqs, power, 120, 250),
        attack_time=_attack_time(mono, sr),
        stereo_width=float(1.0 - corr),
    )
