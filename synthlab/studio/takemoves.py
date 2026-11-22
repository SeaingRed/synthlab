"""What was turned during a take, kept with its loop as curves.

While ⏺ Rec runs, keyrec.js stamps every change to a control on the take's
clock, as it stamps the notes (beats from the take's first downbeat): a
Patch control's slider or knob (dragged, stepped, typed, a deck dial or a
MIDI-learned CC: they all end in the control's `input` event), the XY pad's
axes, a macro knob, and the three performance controls the hands move —
the pitch bend, the mod wheel and aftertouch. It posts them beside the
notes (`moves`, JSON):

    {"from": {field: value}, "moves": {name: [[beat, value], ...]}}

`from` is each moved Patch field's value when the take began (the live
patch, piano.js); `name` is a Patch field, `macro:<i>` or `fx:<param>`.

`apply` makes them part of the take's sound — the loop's: what the sound
was at the take's start is the loop's sound (each moved field set back to
its `from`), and the movement is a drawn curve on that field
(`curves.CURVABLE`, x over the loop's grid, y over the field's curve range),
which the loop render, Edit and → Song play. A curve holds the start value
until the first move, steps there, follows the moves (linear between them)
and holds the last to the loop's end; the loop starts over at the start
value. The three performance controls are offsets the hardware adds to the
patch's own value (`keys-and-midi.md`), so their curve is the patch's value
plus the move. A macro's movement is its targets' (`routes.macros`'s
mapping). A field that cannot carry a curve — not `CURVABLE`, or an FX
unit's parameter (a loop has no FX lanes: those are a song section's) — is
not recorded, and `apply` names it for the take's toast. Moves past the
loop's grid (a take longer than 16 bars) are not kept.

The session's own sound is never touched here: the knobs stay where the
owner left them, which is what he hears now; the loop holds the start and
the movement.
"""

from __future__ import annotations

import json
import math

from ..params import PARAMS, denorm
from ..synth import Patch
from . import limits
from .curves import CURVABLE

GESTURES = ("pitch_bend", "mod_wheel", "aftertouch")
_DEFAULTS = Patch()


def _default(name: str) -> float:
    v = getattr(_DEFAULTS, name, 0.0)
    return float(v) if isinstance(v, int | float) else 0.0


def _num(x) -> float | None:
    if isinstance(x, bool) or not isinstance(x, int | float):
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def parse(text: str) -> dict:
    """The moves a take posts, held to their shape and bounds; anything
    unreadable is no moves (the take itself is never refused for them).
    Past `limits.MAX_TAKE_MOVE_POINTS` a control, or `MAX_TAKE_MOVE_TOTAL`
    in all, the rest is not read and the control is named in `capped` (the
    take's toast says so). Each control's points are then put on the take
    clock's grid (`limits.TAKE_MOVE_GRID`: closer ones are one, the last
    value wins), so the work after is bounded by the take's length."""
    empty = {"from": {}, "moves": {}, "capped": []}
    if not text or len(text) > limits.MAX_TAKE_MOVES_CHARS:
        return (
            empty | {"capped": ["all of them (too much was sent)"]} if text else empty
        )
    try:
        raw = json.loads(text)
    except (ValueError, RecursionError):
        return empty
    if not isinstance(raw, dict):
        return empty
    moves: dict = {}
    capped: list[str] = []
    budget = limits.MAX_TAKE_MOVE_TOTAL
    sent = raw.get("moves") if isinstance(raw.get("moves"), dict) else {}
    for name, pts in list(sent.items())[: limits.MAX_TAKE_MOVE_FIELDS]:
        if not isinstance(name, str) or not 0 < len(name) <= 64:
            continue
        if not isinstance(pts, list):
            continue
        room = min(limits.MAX_TAKE_MOVE_POINTS, budget)
        if len(pts) > room:
            capped.append(name)
        clean = []
        for p in pts[:room]:
            if isinstance(p, list) and len(p) == 2:
                t, v = _num(p[0]), _num(p[1])
                if (
                    t is not None
                    and v is not None
                    and 0 <= t <= 4 * limits.MAX_TAKE_BARS
                ):
                    clean.append([t, v])
        budget -= min(len(pts), room)
        if clean:
            moves[name] = on_grid(sorted(clean, key=lambda p: p[0]))
    if len(sent) > limits.MAX_TAKE_MOVE_FIELDS:
        capped.append(f"{len(sent) - limits.MAX_TAKE_MOVE_FIELDS} more controls")
    start: dict = {}
    if isinstance(raw.get("from"), dict):
        for name, v in list(raw["from"].items())[: 4 * limits.MAX_TAKE_MOVE_FIELDS]:
            if isinstance(name, str) and name in PARAMS and (x := _num(v)) is not None:
                start[name] = x
    return {"from": start, "moves": moves, "capped": capped}


def on_grid(points: list[list]) -> list[list]:
    """Sorted points as the take clock resolves them: one a
    `limits.TAKE_MOVE_GRID` of a beat, the last value in each (a knob's
    latest within it), at its time. One pass."""
    out: list[list] = []
    cell = None
    for t, v in points:
        k = math.floor(t / limits.TAKE_MOVE_GRID)
        if out and k == cell:
            out[-1] = [t, v]
        else:
            out.append([t, v])
            cell = k
    return out


def _y(name: str, value: float) -> float:
    lo, hi = CURVABLE[name]
    return min(1.0, max(0.0, (value - lo) / (hi - lo))) if hi > lo else 0.0


def thin(points: list[list], cap: int) -> list[list]:
    """At most `cap` points, in one pass: the first and the last kept, the
    rest bucketed by x into `(cap - 2) // 2` buckets of equal width, each
    keeping its lowest and its highest point (in x order) — so a sweep's
    turning points survive, and the thinned curve is nowhere further from
    the moves than the y-range inside one bucket (a 1/255 of the loop at
    the curve cap of 512)."""
    if len(points) <= cap:
        return points
    first, last, inner = points[0], points[-1], points[1:-1]
    n = max(1, (cap - 2) // 2)
    x0, x1 = first[0], last[0]
    width = (x1 - x0) / n or 1.0
    lo: dict[int, list] = {}
    hi: dict[int, list] = {}
    for p in inner:
        b = min(n - 1, int((p[0] - x0) / width))
        if b not in lo or p[1] < lo[b][1]:
            lo[b] = p
        if b not in hi or p[1] > hi[b][1]:
            hi[b] = p
    kept = {id(p): p for b in lo for p in sorted((lo[b], hi[b]), key=lambda q: q[0])}
    return [first, *sorted(kept.values(), key=lambda q: q[0]), last]


def curve(start: float, moves: list[list], name: str, loop_beats: float) -> list:
    """One field's curve over a loop of `loop_beats`: its start value held
    to the first move, then the moves, the last held to the end."""
    y0 = _y(name, start)
    pts = [[min(1.0, t / loop_beats), _y(name, v)] for t, v in moves if t < loop_beats]
    if not pts:
        return []
    out = [[0.0, y0]]
    if pts[0][0] > 0:
        out.append([pts[0][0], y0])  # held until it moved, then a step
    out += pts
    if out[-1][0] < 1.0:
        out.append([1.0, out[-1][1]])
    return thin(out, limits.MAX_CURVE_POINTS)


def _label(name: str) -> str:
    from .paramui import spec_of

    spec = spec_of(name)
    return spec.label if spec else name


def apply(
    payload: dict, moves: dict, loop_beats: float, macros=()
) -> tuple[dict, list[str], list[str]]:
    """`payload` (a format-2 sound) with the take's moves made its curves
    (module docstring). Returns (the sound, the fields kept as curves, the
    controls that moved but could not be recorded, as words)."""
    if not moves.get("moves") or loop_beats <= 0:
        return payload, [], []
    out = json.loads(json.dumps(payload))
    patch, curves = out.setdefault("patch", {}), out.setdefault("curves", {})
    start = dict(moves.get("from") or {})
    fields: dict[str, list] = {}
    missed: list[str] = []
    for name, pts in moves["moves"].items():
        if name.startswith("macro:"):
            for field, mapped in _macro_targets(name, pts, macros):
                fields[field] = mapped
            continue
        if name.startswith("fx:"):
            missed.append(f"FX {name[3:]} (a loop has no FX lanes)")
            continue
        if name not in PARAMS:
            continue
        if name not in CURVABLE:
            missed.append(_label(name))
            continue
        if name in GESTURES:  # the hardware's offset on the patch's own value
            base = float(patch.get(name, _default(name)) or 0.0)
            lo, hi = CURVABLE[name]
            pts = [[t, min(hi, max(lo, base + v))] for t, v in pts]
            start.setdefault(name, base)
        fields[name] = pts
    kept: list[str] = []
    for name, pts in fields.items():
        if name not in CURVABLE:
            missed.append(_label(name))
            continue
        base = start.get(name, patch.get(name, _default(name)))
        pts_curve = curve(float(base), pts, name, loop_beats)
        if not pts_curve:
            continue
        curves[name] = pts_curve
        if name not in GESTURES:  # the loop's sound: as it was at the start
            patch[name] = float(base)
        kept.append(name)
    return out, sorted(kept), sorted(set(missed))


def _macro_targets(name: str, pts: list[list], macros) -> list[tuple[str, list]]:
    """A macro knob's movement as its targets' (`routes.macros.apply_macro`:
    a signed share of each target's range, log-aware)."""
    try:
        m = macros[int(name.split(":", 1)[1])]
    except (ValueError, IndexError, TypeError):
        return []
    out = []
    for t in m.get("targets") or []:
        field, amt = t.get("param"), float(t.get("amt") or 0.0)
        if field not in PARAMS:
            continue
        mapped = []
        for beat, v in pts:
            v = min(1.0, max(0.0, v))
            n = v * amt if amt >= 0 else 1.0 + v * amt
            mapped.append([beat, denorm(field, min(1.0, max(0.0, n)))])
        out.append((field, mapped))
    return out


def said(kept: list[str], missed: list[str], capped=()) -> str:
    """The take's toast's words for them ("" when nothing moved)."""
    bits = []
    if capped:
        bits.append(
            "too many moves sent — only the first read of "
            + ", ".join(_label(c) for c in capped)
        )
    if kept:
        bits.append("kept " + ", ".join(_label(k) for k in kept) + " as automation")
    if missed:
        bits.append(
            "moved but not recorded (no automation for them): " + ", ".join(missed)
        )
    return ("Controls: " + "; ".join(bits) + ".") if bits else ""
