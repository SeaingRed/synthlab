"""Preset sound metadata: tag derivation + sparkline spectrum.

Pure NumPy in/out (Rust-ready seam): no FastAPI types, no I/O.
"""

from __future__ import annotations

import numpy as np

from ..analysis.features import Features, extract
from ..audio import spectrum as _spectrum
from ..synth import Patch, Synth

META_NOTE = "C3"
META_DURATION = 1.5


def band_magnitudes(
    audio: np.ndarray, bands: int = 24, relative: bool = True
) -> list[float]:
    """24 log-spaced band magnitudes normalized 0..1 (−60dB floor).

    When `relative` (default), the bands are then rescaled by their own
    max so the loudest band is exactly 1.0 — quiet renders still show
    their spectral shape instead of pinning near zero.
    """
    out = _spectrum(audio, bands=bands)
    mags = [float(np.clip((db + 60.0) / 60.0, 0.0, 1.0)) for _, db in out]
    if relative:
        peak = max(max(mags), 1e-6)
        mags = [float(np.clip(m / peak, 0.0, 1.0)) for m in mags]
    return mags


def derive_tags(f: Features) -> list[str]:
    tags: list[str] = []
    if f.sub_20_60 + f.sub_60_120 > 0.5:
        tags.append("bass")
    if f.attack_time > 0.15:
        tags.append("pad")
    if f.flatness > 0.35:
        tags.append("fx")
    if not tags:
        tags.append("lead")
    return tags


def note_duration(patch: Patch, floor: float = META_DURATION) -> float:
    """Render length that survives the engine's release >= duration silence
    edge (hold segment collapses to zero): always leave the envelope room."""
    return max(
        floor, float(patch.attack) + float(patch.decay) + float(patch.release) + 0.25
    )


# Seconds of extra sustain in a held key's render (`/note/X.wav?hold=1`).
# Hold mode loops the sustain segment; note_duration() leaves ~0.25 s of it,
# so a unison pad's beating wrapped ~3x a second at a new level each time
# (4.5 dB steps on "Velvet Line", heard as clicking). 3 s -> ~0.3 wraps/s.
LIVE_HOLD_WINDOW = 3.0


def live_note_duration(patch: Patch, hold: bool = False) -> float:
    """Render length of a live key: the one-shot length, plus a long
    sustain window when the browser will loop it (see LIVE_HOLD_WINDOW)."""
    return note_duration(patch) + (LIVE_HOLD_WINDOW if hold else 0.0)


def preset_meta(patch: Patch) -> dict:
    audio = Synth(patch).note(META_NOTE, note_duration(patch))
    return {
        "tags": derive_tags(extract(audio)),
        "spectrum": band_magnitudes(audio),
        "v": 2,
    }


def filter_response_svg(patch: Patch) -> str:
    """Inline magnitude-response polyline for the Filter fieldset. Real
    biquad coefficients from core.biquad_coeffs; the ladder draws
    as its 2-pole cousin (a display, not a render path). 20 Hz-20 kHz
    log-f, +12..-36 dB vertical. The other types and filter 2 (serial:
    the product, parallel: the mean) draw from `core.filter_response`."""
    from ..core import SR, biquad_coeffs, filter_response
    from ..params import FILTER_TYPES

    kind = patch.filter_type
    route = patch.filter2_route
    two = route in ("serial", "parallel") and patch.filter2_type != "none"

    def med(v) -> float:
        return float(np.median(np.asarray(v, dtype=np.float64)))

    # the drawing on a coarse grid for the first six alone; the rest
    # (and a second filter: a comb's teeth) at a finer grid
    old = kind in FILTER_TYPES and not two
    f = np.geomspace(20.0, 20000.0, 64 if old else 160)
    if kind == "none":
        h = np.ones_like(f, dtype=complex)
    elif old:
        cut = med(patch.cutoff)
        q = 0.707 + med(patch.resonance) * 6
        b, a = biquad_coeffs("lowpass" if kind == "ladder" else kind, cut, q)
        z = np.exp(-1j * 2 * np.pi * f / SR)
        h = (b[0] + b[1] * z + b[2] * z**2) / (a[0] + a[1] * z + a[2] * z**2)
    else:
        h = filter_response(kind, med(patch.cutoff), med(patch.resonance), f, SR)
    if two:
        h2 = filter_response(
            patch.filter2_type,
            med(patch.filter2_cutoff),
            med(patch.filter2_resonance),
            f,
            SR,
        )
        h = h * h2 if route == "serial" else 0.5 * (h + h2)
    mag = np.abs(h)
    db = 20 * np.log10(np.maximum(mag, 1e-4))
    y = np.clip((12.0 - db) / 48.0, 0.0, 1.0) * 30 + 1
    x = np.linspace(0.0, 120.0, len(f))
    pts = " ".join(f"{xi:.1f},{yi:.1f}" for xi, yi in zip(x, y, strict=True))
    return (
        '<svg id="filter-response" viewBox="0 0 120 32" preserveAspectRatio="none"'
        f' aria-hidden="true"><polyline points="{pts}" fill="none"/></svg>'
    )


def envelope_svg(
    svg_id: str, a: float, d: float, s: float, r: float, curve: float = 2.0
) -> str:
    """Inline ADSR polyline. Time axis is proportional with a
    fixed sustain hold of 30% of the drawn width; the attack/decay/release
    segments apply the patch's env_curve exponent so the drawing matches
    core.adsr's actual shape."""

    def seg(x0: float, x1: float, y0: float, y1: float, rising: bool) -> list[str]:
        ts = np.linspace(0.0, 1.0, 12)
        shaped = ts**curve if rising else 1.0 - (1.0 - ts) ** curve
        return [
            f"{x0 + (x1 - x0) * t:.1f},{31 - (y0 + (y1 - y0) * v) * 30:.1f}"
            for t, v in zip(ts, shaped, strict=True)
        ]

    a, d, r = max(float(a), 1e-3), max(float(d), 1e-3), max(float(r), 1e-3)
    s = float(np.clip(s, 0.0, 1.0))
    total = a + d + r
    w = 120.0
    hold = w * 0.3
    ax = w * 0.7 * (a / total)
    dx = ax + w * 0.7 * (d / total)
    pts = (
        seg(0, ax, 0.0, 1.0, rising=True)
        + seg(ax, dx, 1.0, s, rising=False)
        + [f"{dx + hold:.1f},{31 - s * 30:.1f}"]
        + seg(dx + hold, w, s, 0.0, rising=False)
    )
    return (
        f'<svg id="{svg_id}" viewBox="0 0 120 32" preserveAspectRatio="none"'
        f' aria-hidden="true"><polyline points="{" ".join(pts)}" fill="none"/></svg>'
    )
