"""Bass lab view: sub-spectrum SVG, mono-compat lamp, phone-test meter.

Pure string building for the SVG (a handful of points, no templating engine
needed) — every interpolated value is numerically coerced (float/round +
format specs) before it reaches the markup; nothing free-text ever lands in
this string.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfiltfilt

from ..analysis.bass import mono_compat, phone_test, sub_spectrum
from ..core import SR
from .sigdraw import HEADROOM, smooth_path

_DB_FLOOR = -80.0
_DB_CEIL = 0.0
# below this the sub has nothing to show: the page says so instead of a
# flat line along the floor
_DB_QUIET = -70.0


def _level(d: float) -> float:
    """dBFS as the plot's 0..1 height (`_DB_FLOOR`..`_DB_CEIL`)."""
    d = min(_DB_CEIL, max(_DB_FLOOR, float(d)))
    return (d - _DB_FLOOR) / (_DB_CEIL - _DB_FLOOR)


def db_top(db: float) -> float:
    """Where a dB line sits on the plot, in % from its top: `smooth_path`
    keeps `HEADROOM` above full scale, and the ticks follow it."""
    return round(100.0 - _level(db) * HEADROOM * 100.0, 2)


def sub_spectrum_svg(audio: np.ndarray, sr: int = SR) -> str:
    """10-120 Hz as THE signal graphic (`svg.sig`, `sigdraw.smooth_path`:
    a smooth area under its stroke), viewBox 0 0 100 40. -80..0 dBFS
    (`sub_spectrum` reads a full-scale sine as 0 dB) is the height from
    the floor up (louder = higher on the chart; `db_top` places a dB
    line)."""
    pairs = sub_spectrum(audio, sr)
    if len(pairs) < 2:
        return (
            '<svg class="sub-spectrum sig" viewBox="0 0 100 40" '
            'aria-hidden="true"></svg>'
        )
    lo, hi = float(pairs[0][0]), float(pairs[-1][0])
    span = (hi - lo) or 1.0
    points = [((float(f) - lo) / span * 100.0, _level(d)) for f, d in pairs]
    return (
        '<svg class="sub-spectrum sig" viewBox="0 0 100 40" '
        'preserveAspectRatio="none" aria-hidden="true">'
        f'<path d="{smooth_path(points, 100.0, 40.0, 2)}"></path>'
        "</svg>"
    )


def bass_panels(audio: np.ndarray, sr: int = SR) -> dict:
    """{"svg": str, "quiet": nothing in the sub to draw, "mono":
    mono_compat(...) dict, "phone": phone_test(...) dict}"""
    phone = dict(phone_test(audio, sr))
    phone["survives_ratio"] = min(1.0, float(phone["survives_ratio"]))
    pairs = sub_spectrum(audio, sr)
    return {
        "svg": sub_spectrum_svg(audio, sr),
        "quiet": not pairs or max(float(d) for _, d in pairs) < _DB_QUIET,
        "db_top": db_top,
        "mono": mono_compat(audio, sr),
        "phone": phone,
    }


def phone_speaker(audio: np.ndarray, sr: int = SR) -> np.ndarray:
    """What a phone speaker leaves of a mix: a 4th-order 200 Hz high-pass,
    zero-phase, per channel. Bass Lab's "hear it on a phone" post-process
    (`StudioService.POSTS["phone"]`)."""
    sos = butter(4, 200.0, btype="highpass", fs=sr, output="sos")
    if audio.ndim == 1:
        return sosfiltfilt(sos, audio)
    return np.stack(
        [sosfiltfilt(sos, audio[:, ch]) for ch in range(audio.shape[1])], axis=1
    )
