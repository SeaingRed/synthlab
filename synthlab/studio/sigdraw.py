"""The one signal graphic: a smooth area with a top stroke, as ONE SVG path.

Every spectrum spark, energy shape and the like is drawn the same way
(style.css section 32, "one signal graphic"): a smooth curve through the
values, filled with a fade (`url(#sig-fade)`, base.html) and stroked on top.
The path is closed BELOW and BESIDE its viewBox, so the stroke that rides
the whole outline is only ever seen along the top: one path does the area
and the line, and a row of a hundred sparks costs no more than the bars did
(tests/studio/test_browse_views.py holds the Library's weight per row).

A curve through midpoints: each value is a quadratic control point and the
curve passes halfway between neighbours, so it never overshoots a value
(a spectrum never reads above its loudest band).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

# the top of a full-scale value sits this far down the box, so the stroke is not clipped
HEADROOM = 0.92


def _n(v: float, prec: int = 1) -> str:
    """A coordinate, short: `prec` decimals, no trailing zero, no leading zero."""
    if prec == 0:
        return str(round(v))
    s = f"{round(v, prec):.{prec}f}".rstrip("0").rstrip(".")
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s or "0"


def _clamp(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if x != x else min(1.0, max(0.0, x))  # NaN -> 0


def smooth_path(
    points: Sequence[tuple[float, float]],
    width: float,
    height: float = 10.0,
    prec: int = 1,
) -> str:
    """`points` are (x, level 0..1) in x order, x in 0..width; the path for a
    `viewBox="0 0 {width} {height}"`, its numbers to `prec` decimals."""
    if not points:
        points = [(width / 2, 0.0)]
    pts = [(float(x), height - _clamp(v) * height * HEADROOM) for x, v in points]
    below, (x0, y0) = height + 1, pts[0]
    d = [f"M-1 {_n(below, prec)}V{_n(y0, prec)}"]
    if len(pts) > 1:
        x1, y1 = pts[1]
        d.append(f"L{_n((x0 + x1) / 2, prec)} {_n((y0 + y1) / 2, prec)}Q")
        quads = []
        for i in range(1, len(pts) - 1):
            (x, y), (xn, yn) = pts[i], pts[i + 1]
            mid = f"{_n((x + xn) / 2, prec)} {_n((y + yn) / 2, prec)}"
            quads.append(f"{_n(x, prec)} {_n(y, prec)} {mid}")
        if quads:
            d.append(" ".join(quads))
        else:
            d[-1] = d[-1][:-1]  # no Q segments: drop the command letter
    # level out to the far edge on the last value, then down outside the box and home
    d.append(f"L{_n(width + 1, prec)} {_n(pts[-1][1], prec)}V{_n(below, prec)}Z")
    return "".join(d)


def sig_path(values: Iterable) -> str:
    """Evenly spaced levels (a spectrum's bands) for `sig_box`'s viewBox: in
    whole units (value i at x = 2i + 1 of 2n, levels of 100), so a row of a
    hundred sparks is short numbers that compress."""
    vals = list(values or [])
    return smooth_path(
        [(2 * i + 1, v) for i, v in enumerate(vals)], 2 * max(1, len(vals)), 100, 0
    )


def sig_box(values: Iterable) -> str:
    """The viewBox `sig_path` draws in."""
    return f"0 0 {2 * max(1, len(list(values or [])))} 100"
