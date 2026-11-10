"""The drawn faces of the oscillator controls
(studio docs/sounds-and-breeding.md): each fm4
algorithm's wiring as a small diagram, each wavetable's frames as a
thumbnail, and the ratio presets the FM4 panel offers.

Drawn from what the engine already holds — `core.FM4_ALGOS` (which
operator modulates which, and the carriers) and `core.WAVETABLES` (every
table's frames at the brightest mip) — once, at import: SVG geometry with
no colour in it (style.css section 17.1 strokes it from the tokens), so a
page carries a few kB of paths and never the 3.8 MB `/wavetables.bin`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .. import wavetables
from ..core import FM4_ALGOS, WAVETABLES, draw_parse, draw_points

# --- fm4: an algorithm's wiring -----------------------------------------------

_CELL = 11  # one operator's cell in the diagram's grid
_BOX_W, _BOX_H = 9, 8
_TOP, _BOTTOM = 5, 4  # room for operator 4's feedback loop / the output bus
_ROWS = 4  # the tallest algorithm ("stack"): every diagram sits in this height
FM4_VIEW = (_CELL * 4 + 4, _TOP + _CELL * _ROWS + _BOTTOM)  # (w, h): 48 x 53


@dataclass(frozen=True)
class Fm4Face:
    name: str
    ops: tuple[
        tuple[int, float, float, bool], ...
    ]  # (op, x, y, carrier): a box's corner
    wires: str  # one path: every modulator into its target, every carrier to the bus
    loop: str  # operator 4's feedback loop


def _fm4_face(name: str) -> Fm4Face:
    """Carriers on the bottom line, each modulator above what it modulates
    (a tree laid out by its leaves, centred over them), op 4's feedback a
    loop over its box. Every diagram shares one frame, bottom-aligned, so
    the strip reads as one row of tiles."""
    mods, carriers = FM4_ALGOS[name]
    at: dict[int, tuple[float, int]] = {}
    cols = 0

    def place(op: int, depth: int) -> float:
        nonlocal cols
        kids = mods.get(op, ())
        if kids:
            xs = [place(k, depth + 1) for k in kids]
            x = (xs[0] + xs[-1]) / 2
        else:
            x, cols = float(cols), cols + 1
        at[op] = (x, depth)
        return x

    for c in carriers:
        place(c, 0)
    off = 2 + (_CELL * 4 - cols * _CELL) / 2 + (_CELL - _BOX_W) / 2

    def corner(op: int) -> tuple[float, float]:
        x, depth = at[op]
        return off + x * _CELL, _TOP + (_ROWS - 1 - depth) * _CELL + (
            _CELL - _BOX_H
        ) / 2

    def g(v: float) -> str:
        return f"{v:g}"

    wires = []
    for target, sources in mods.items():
        tx, ty = corner(target)
        for s in sources:
            sx, sy = corner(s)
            wires.append(
                f"M{g(sx + _BOX_W / 2)} {g(sy + _BOX_H)}L{g(tx + _BOX_W / 2)} {g(ty)}"
            )
    bus = FM4_VIEW[1] - _BOTTOM / 2
    xs = [corner(c)[0] + _BOX_W / 2 for c in carriers]
    for c, x in zip(carriers, xs, strict=True):
        wires.append(f"M{g(x)} {g(corner(c)[1] + _BOX_H)}V{g(bus)}")
    wires.append(f"M{g(min(xs) - 2)} {g(bus)}H{g(max(xs) + 2)}")
    fx, fy = corner(4)
    loop = f"M{g(fx + _BOX_W)} {g(fy + 2)}h2V{g(fy - 2.5)}H{g(fx + _BOX_W / 2)}V{g(fy)}"
    ops = tuple((op, *corner(op), op in carriers) for op in sorted(at))
    return Fm4Face(name, ops, "".join(wires), loop)


FM4_FACES: dict[str, Fm4Face] = {name: _fm4_face(name) for name in FM4_ALGOS}
# which operators are heard in each algorithm (the panel lights their lamps)
FM4_CARRIERS: dict[str, tuple[int, ...]] = {n: c for n, (_, c) in FM4_ALGOS.items()}

# The FM4 panel's ratio presets: operator 1 to 4's pitch as a multiple of
# the note (a menu of starting points: the rest of the sound is untouched).
FM4_RATIOS: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("Unison", (1.0, 1.0, 1.0, 1.0)),
    ("Harmonics", (1.0, 2.0, 3.0, 4.0)),
    ("E-piano tine", (1.0, 1.0, 1.0, 14.0)),
    ("Bell", (1.0, 3.5, 1.0, 7.0)),
    ("Brass", (1.0, 1.0, 1.0, 2.0)),
    ("Hollow", (1.0, 2.0, 1.0, 3.0)),
)

# --- wavetables: each table's frames -----------------------------------------

WT_VIEW = (48, 20)  # (w, h) of a thumbnail's viewBox
_WT_POINTS = 48


def _frame_path(frame: np.ndarray) -> str:
    """One frame as a polyline across the thumbnail: sampled at
    `_WT_POINTS` + 1 points (the cycle and its first sample again), each
    point the frame's mean over its slice (a bright table's top end never
    aliases into the drawing); whole units, so a path is short."""
    w, h = WT_VIEW
    n = len(frame)
    edges = np.linspace(0, n, _WT_POINTS + 1).astype(int)
    pts = [float(frame[a:b].mean()) for a, b in zip(edges[:-1], edges[1:], strict=True)]
    pts.append(pts[0])
    peak = max(1e-9, max(abs(p) for p in pts))
    mid, amp = h / 2, h / 2 - 1.5
    ys = [round(mid - amp * p / peak) for p in pts]
    step = w / _WT_POINTS
    return (
        "M0 "
        + str(ys[0])
        + "".join(f"L{round(i * step)} {y}" for i, y in enumerate(ys) if i)
    )


# table -> its frames' paths, first to last
WT_FACES: dict[str, tuple[str, ...]] = {
    name: tuple(_frame_path(f) for f in frames) for name, frames in WAVETABLES.items()
}


# --- your own tables (synthlab/wavetables.py) --------------------------------
_OWN_FRAMES = 8  # a thumbnail draws at most this many of a table's frames
_OWN_CACHE: dict[str, tuple[str, ...]] = {}


@dataclass(frozen=True)
class OwnFace:
    id: str
    name: str
    frames: int
    paths: tuple[str, ...]  # up to _OWN_FRAMES frames, first to last


def own_faces(directory) -> list[OwnFace]:
    """The tables in `directory` (the studio's own), newest first, each
    drawn as the built-ins are (its paths cached by id: a table never
    changes)."""
    out = []
    for t in wavetables.listing(directory):
        paths = _OWN_CACHE.get(t["id"])
        if paths is None:
            st = wavetables.stack(t["id"])
            if st is None:
                continue
            pick = np.unique(np.linspace(0, st.shape[1] - 1, _OWN_FRAMES).round())
            paths = tuple(_frame_path(st[0, int(i)]) for i in pick)
            _OWN_CACHE[t["id"]] = paths
        out.append(OwnFace(t["id"], t["name"], t["frames"], paths))
    return out


# --- a general LFO's drawn shape (the drawing on its row) ---------------------

DRAW_VIEW = (120, 40)  # (w, h): one cycle across, -1..1 up the middle
# the starting shapes the drawing's menu offers (core.clean_draw's format)
DRAW_PRESETS: tuple[tuple[str, str], ...] = (
    ("Triangle", "0:-1,0.5:1"),
    ("Ramp up", "0:-1,0.99:1"),
    ("Ramp down", "0:1,0.99:-1"),
    ("Steps", "0:-1,0.24:-1,0.25:-0.33,0.49:-0.33,0.5:0.33,0.74:0.33,0.75:1,0.99:1"),
    ("Gate", "0:1,0.45:1,0.5:-1,0.95:-1"),
    ("Swell", "0:-1,0.6:0.3,0.8:1,0.9:-0.6"),
)


def draw_face(s: str) -> dict:
    """The drawing of `s` as the row shows it: the one cycle as a polyline
    (`line`, from x 0 to the width, wrapping as the engine reads it) and
    each point's handle (`pts`, (x, y) in the view's units)."""
    w, h = DRAW_VIEW
    xs, ys = draw_points(s)
    pts = draw_parse(s) or list(zip(xs[1:-1].tolist(), ys[1:-1].tolist(), strict=True))
    t = [0.0, *(x for x, _ in pts), 1.0]
    v = np.interp(t, xs, ys)

    def at(x: float, y: float) -> tuple[float, float]:
        return round(x * w, 1), round(h / 2 - y * (h / 2 - 2), 1)

    line = " ".join("{:g},{:g}".format(*at(x, y)) for x, y in zip(t, v, strict=True))
    return {"line": line, "pts": [at(x, y) for x, y in pts]}
