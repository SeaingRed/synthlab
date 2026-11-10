"""Form parsing for the studio's parameter controls: which value a posted
control means, and how a slider is drawn so it posts that value back.

What the controls *are* (labels, panels, ranges) is `paramui.py`, over the
registry in `synthlab.params`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace

from .. import wavetables
from ..params import META_CLEAN, PARAMS
from ..synth import Patch
from .paramui import _SPEC_BY_NAME, ParamSpec


def slider_bounds(spec: ParamSpec, value) -> tuple[float, float]:
    """The min/max a linear range slider is drawn with for `value`: the
    spec's, widened to take in a value outside it (Laser Zap's -2600 Hz
    filter env, a 4 s pad release). A browser clamps an <input type=range>
    to its own min/max, so a narrower slider would post the clamped value
    back on the next edit of *any* control and silently rewrite the sound."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return spec.min, spec.max
    if not math.isfinite(v):
        return spec.min, spec.max
    return min(spec.min, v), max(spec.max, v)


def _on_slider(spec: ParamSpec, value: float) -> float:
    """`value` as the slider drawn for it posts it back untouched: a linear
    range input snaps to `min + k * step` (HTML's step base is `min`); a
    log slider posts its hidden field, the exact value."""
    if spec.log or not spec.step:
        return value
    lo, hi = slider_bounds(spec, value)
    k = math.floor((value - lo) / spec.step + 0.5)
    if lo + k * spec.step > hi + 1e-12:  # never past max: the step below it
        k -= 1
    return lo + k * spec.step


def _unmoved(spec: ParamSpec, posted: float, current) -> bool:
    """Did the form just post the control's current value back? Every
    patch post carries every control (hx-include="#patch"), so only the
    one the user moved may be applied — re-parsing the rest would clamp
    and step-snap values a factory sound holds on purpose."""
    try:
        cur = float(current)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(cur):
        return False
    tol = 1e-9 * max(1.0, abs(cur), abs(spec.step or 0.0))
    return abs(posted - cur) <= tol or abs(posted - _on_slider(spec, cur)) <= tol


def patch_from_form(form: Mapping[str, str], base: Patch) -> Patch:
    """The controls a post moved, onto `base`. A control posting the value
    it was drawn with is left alone (`_unmoved`), and a moved one clamps to
    its `params` cap — the same range `params.clamp` accepts everywhere
    else — so nudging one slider never rewrites a sound's other fields.

    A step-1.0 slider posts an int. That includes cutoff (step 1 Hz, not an
    int gene): its edited value has always been stored as an int, and the
    render keys of every saved sound depend on that, so it stays."""
    changes: dict = {}
    for name, spec in _SPEC_BY_NAME.items():
        if name not in form:
            continue
        raw = form[name]
        current = getattr(base, name)
        if name == "wt_table" and (raw.startswith("own:") or base.wt_custom):
            # the picker's own tables: "own:<id>" picks one, a
            # built-in posted while one plays goes back to the built-ins
            own = wavetables.clean_id(raw[4:]) if raw.startswith("own:") else ""
            if own != base.wt_custom and (own or raw in {v for v, _ in spec.options}):
                changes["wt_custom"] = own
            if own:
                continue
        if spec.kind in ("pills", "select"):
            valid = {v for v, _ in spec.options}
            if raw in valid:
                value = None if raw == "" else raw
                if value != current:
                    changes[name] = value
        else:
            try:
                v = float(raw)
            except ValueError:
                continue
            if not math.isfinite(v) or _unmoved(spec, v, current):
                continue
            lo, hi = PARAMS[name].cap
            v = min(float(hi), max(float(lo), v))
            changes[name] = int(v) if spec.step == 1.0 else v
    # the drawn LFO shapes: a hidden field each LFO's drawing writes,
    # cleaned as `clamp` cleans it (core.clean_draw)
    for name in DRAWN:
        if name in form:
            value = META_CLEAN[name](form[name])
            if value != getattr(base, name):
                changes[name] = value
    return replace(base, **changes) if changes else base


# the general LFOs' drawn shapes (meta strings, no slider)
DRAWN = ("lfo_draw", "lfo2_draw", "lfo3_draw")
