"""Bass-specific analysis: will this low end translate?

sub_spectrum  high-resolution look at 10-120 Hz
mono_compat   does the sub survive a mono fold-down?
phone_test    what's left after a 200 Hz highpass (small-speaker reality)
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfiltfilt

from ..core import SR

_EPS = 1e-12


def _mono(audio: np.ndarray) -> np.ndarray:
    x = np.asarray(audio, dtype=np.float64)
    return x if x.ndim == 1 else x.mean(axis=1)


def sub_spectrum(
    audio, sr: int = SR, lo: float = 10.0, hi: float = 120.0
) -> list[tuple[float, float]]:
    """(Hz, dBFS) pairs over `lo`..`hi` at <= 0.5 Hz resolution. dBFS is
    relative to a full-scale sine: a sine of amplitude A reads 20*log10(A)
    at its frequency (0 dB at A = 1), whatever the buffer's length. The raw
    FFT magnitude grows with the window's sum — a 0.3 sine over 2 s read
    +76 dB, and the chart's 0 dB ceiling flattened every sound into a line."""
    mono = _mono(audio)
    nfft = max(len(mono), int(2 * sr))  # >= 0.5 Hz resolution
    win = np.hanning(len(mono))
    mag = np.abs(np.fft.rfft(mono * win, n=nfft))
    # a sine of amplitude A peaks at A * sum(win) / 2 in a one-sided rfft
    full_scale = max(float(np.sum(win)) / 2.0, _EPS)
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    m = (freqs >= lo) & (freqs <= hi)
    db = 20 * np.log10(mag[m] / full_scale + _EPS)
    return [(float(f), float(d)) for f, d in zip(freqs[m], db, strict=False)]


def mono_compat(audio, sr: int = SR) -> dict:
    x = np.asarray(audio, dtype=np.float64)
    if x.ndim == 1:
        return {"correlation": 1.0, "low_correlation": 1.0, "mono_sub_ok": True}
    left, right = x[:, 0], x[:, 1]

    def _corr(a, b):
        if np.std(a) < _EPS or np.std(b) < _EPS:
            return 1.0
        return float(np.corrcoef(a, b)[0, 1])

    sos = butter(4, 150.0, btype="lowpass", fs=sr, output="sos")
    low_corr = _corr(sosfiltfilt(sos, left), sosfiltfilt(sos, right))
    return {
        "correlation": _corr(left, right),
        "low_correlation": low_corr,
        "mono_sub_ok": bool(low_corr > 0.9),
    }


def phone_test(audio, sr: int = SR, cutoff: float = 200.0) -> dict:
    mono = _mono(audio)
    sos = butter(4, cutoff, btype="highpass", fs=sr, output="sos")
    hp = sosfiltfilt(sos, mono)
    rms_full = float(np.sqrt(np.mean(mono**2))) + _EPS
    rms_hp = float(np.sqrt(np.mean(hp**2))) + _EPS
    return {
        "survives_ratio": float(rms_hp / rms_full),
        "rms_db_full": float(20 * np.log10(rms_full)),
        "rms_db_hp": float(20 * np.log10(rms_hp)),
    }
