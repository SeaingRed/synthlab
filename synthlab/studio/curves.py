"""Breakpoint curves: draw a shape over 0..1 x 0..1, map into a param's
range, and expand to a per-sample array applied via `patch.tweak(...)`.
"""

from __future__ import annotations

import numpy as np

from ..params import PARAMS

# param -> the range a curve's 0..1 maps onto: every Param with a `curve`
# (synthlab.params). Each accepts per-sample arrays in the engine —
# tests/test_synth.py renders every one with a ramp to keep that honest.
CURVABLE: dict[str, tuple[float, float]] = {
    name: param.curve for name, param in PARAMS.items() if param.curve is not None
}

# --- section automation lanes ---------------------------------------------
# A song section's lanes (`song_model.set_lane`) are the same breakpoint
# curves, drawn over one pass of the section instead of one loop. `gain` is
# the one target that is not a Patch field: the track's level, a fader after
# its FX. Its 0..1 maps onto x0..x2, so 0.5 is unity (0.5 * 2.0 is exactly
# 1.0) and a fade-in is a lane from 0 to 0.5.
GAIN = "gain"
GAIN_MAX = 2.0
GAIN_UNITY = 0.5
LANE_TARGETS: tuple[str, ...] = (GAIN, *CURVABLE)


# --- FX-parameter lanes ----------------------------------------------------
# The parameters of a unit in the TRACK's FX chain a lane can move: a
# whitelist, each entry with an implementation that is what the unit would
# do if its slider moved (`fxauto.py` says how, and how exactly). Nothing
# else is offered: time parameters (a delay's time, a beat repeat's grid, a
# tape stop's length) are out on purpose, and so is anything without an
# honest swept form yet.
#
# A lane names its unit by the unit's `id` (`fxrack.clean_chain` keeps one;
# a unit gets it when a lane first targets it, so a chain no lane ever
# touched saves exactly what it saved) and by its kind:
# "fx:<unit id>:<kind>:<param>". The kind makes the target readable on its
# own — its range, its label — and a lane whose unit is gone is *missing*:
# inert, never applied to another unit.
FX_LANES: dict[str, tuple[str, ...]] = {
    "drive": ("amount", "mix"),
    "clip": ("drive", "mix"),
    "phaser": ("mix",),
    "chorus": ("mix",),
    "flanger": ("mix",),
    "delay": ("feedback", "mix"),
    "reverb": ("mix",),
    "fold": ("mix",),
    "ringmod": ("mix",),
    "comb": ("mix",),
    "lowpass": ("cutoff", "q"),
    "highpass": ("cutoff", "q"),
    "eq": ("low", "mid_freq", "mid_gain", "high"),
    "granular": ("mix",),
    "shimmer": ("mix",),
    # a gain on the clock: the envelope is computed once, the depth scales
    # it per sample (gain = 1 - depth * (1 - g): exact)
    "gate": ("depth",),
    "sidechain": ("depth",),
    "pump": ("depth",),
    # the two time remaps, by their blend only (never a time parameter):
    # which steps repeat, and where the tape stops, never move with it
    "beat_repeat": ("mix",),
    "stop": ("mix",),
    # the studio tricks: each blend, the tape's feedback (its loop gain per
    # sample, as the delay's) and the rotary's speed — the switch from
    # chorale to tremolo, the rotors ramping to it (`effects.rotor_rate`)
    "doubler": ("mix",),
    "rotary": ("speed", "mix"),
    "tape_echo": ("feedback", "mix"),
    "telephone": ("mix",),
    "pitch": ("mix",),
    "plate": ("mix",),
    # the FX2 units: each blend, and the parameter you would ride — the
    # auto-filter's cutoff and depth (its LFO's reach), the tremolo's depth,
    # the tape's drive, the ping-pong's feedback, the exciter's amount
    "autofilter": ("cutoff", "depth", "mix"),
    "tremolo": ("depth",),
    "tape": ("drive", "mix"),
    "pingpong": ("feedback", "mix"),
    "spring": ("mix",),
    "formant_shift": ("mix",),
    "freqshift": ("mix",),
    "exciter": ("amount",),
    "vocoder_fx": ("mix",),
    # the dynamics: the OTT's depth (its blend, band by band), the limiter's
    # gain into it (a build pushed louder), the transient shaper's two
    "ott": ("depth",),
    "limiter": ("gain",),
    "transient": ("attack", "sustain"),
}
# A lane stores 0..1 of its range, so a slider widened later would rescale
# every lane drawn on it: such a lane keeps the range it was drawn in. The
# drive's amount went from 1-4 to 1-12; its lane still spans 1-4.
FX_LANE_RANGES: dict[tuple[str, str], tuple[float, float]] = {
    ("drive", "amount"): (1.0, 4.0),
}
FX_PREFIX = "fx:"
_UNIT_ID = frozenset("0123456789abcdef")
UNIT_ID_LEN = 8


def unit_id_ok(uid) -> bool:
    """An FX unit's id as `fxrack.clean_chain` keeps it: 8 lowercase hex."""
    return isinstance(uid, str) and len(uid) == UNIT_ID_LEN and set(uid) <= _UNIT_ID


def fx_target(uid: str, kind: str, param: str) -> str:
    return f"{FX_PREFIX}{uid}:{kind}:{param}"


def parse_fx(target) -> tuple[str, str, str] | None:
    """(unit id, kind, param) of a well-formed FX lane target on the
    whitelist, else None. Says nothing about whether the unit exists."""
    if not isinstance(target, str) or not target.startswith(FX_PREFIX):
        return None
    bits = target[len(FX_PREFIX) :].split(":")
    if len(bits) != 3 or not unit_id_ok(bits[0]):
        return None
    return (bits[0], bits[1], bits[2]) if bits[2] in FX_LANES.get(bits[1], ()) else None


def lane_range(target: str) -> tuple[float, float]:
    """The range a lane's 0..1 maps onto: the registry's curve range for a
    Patch field (`CURVABLE`), x0..x2 for `gain`, the rack slider's own range
    for an FX parameter (`fxrack.FX_UNITS`; `FX_LANE_RANGES` where the
    slider has grown past the lane's)."""
    if target == GAIN:
        return (0.0, GAIN_MAX)
    fx = parse_fx(target)
    if fx is None:
        return CURVABLE[target]
    return fx_lane_range(fx[1], fx[2])


def fx_lane_range(kind: str, param: str) -> tuple[float, float]:
    """The range an FX lane's 0..1 maps onto (`lane_range`)."""
    if (kind, param) in FX_LANE_RANGES:
        return FX_LANE_RANGES[kind, param]
    from .fxrack import FX_UNITS  # lazy: fxrack imports state

    lo, hi, _default = FX_UNITS[kind]["params"][param]
    return (lo, hi)


def lane_array(
    points: list[list], target: str, n: int, start: int = 0
) -> np.ndarray | None:
    """Samples [start, n) of a lane drawn over `n` samples, mapped into
    `lane_range(target)`. <2 points -> None, as `curve_array`.

    Sample i sits at `i / (n - 1)` whatever `start` is, so a chunk of a
    section's cycle (`start` = where the chunk begins) reads exactly the
    values the cycle rendered in one piece reads there — `curve_array`'s
    linspace over a cropped curve only comes close."""
    if len(points) < 2:
        return None
    lo, hi = lane_range(target)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    v = np.interp(np.arange(start, n) / max(1, n - 1), xs, ys)
    return lo + v * (hi - lo)


def toggle_point(points: list[list], t: float, v: float) -> list[list]:
    """Click near an existing point (|dt| < 0.04) removes it; else inserts
    [t, v]. Result sorted by t; t, v clamped to [0, 1]."""
    t = min(1.0, max(0.0, t))
    v = min(1.0, max(0.0, v))
    for p in points:
        if abs(p[0] - t) < 0.04:
            return [p2 for p2 in points if p2 is not p]
    return sorted([*points, [t, v]], key=lambda p: p[0])


def curve_array(points: list[list], param: str, n: int) -> np.ndarray | None:
    """np.interp over n samples; v 0..1 mapped into CURVABLE[param] range.
    <2 points -> None (caller skips)."""
    if len(points) < 2:
        return None
    lo, hi = CURVABLE[param]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    v = np.interp(np.linspace(0.0, 1.0, n), xs, ys)
    return lo + v * (hi - lo)


def curve_svg(points: list[list], param: str) -> str:
    """Server-rendered inline SVG (viewBox 0 0 100 40): polyline through
    points + circles."""
    pts = [(t * 100, 40 - v * 40) for t, v in points]
    poly = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
    circles = "".join(
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.5"></circle>' for x, y in pts
    )
    return (
        f'<svg class="curve" data-param="{param}" viewBox="0 0 100 40" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{poly}" fill="none"></polyline>'
        f"{circles}"
        f"</svg>"
    )
