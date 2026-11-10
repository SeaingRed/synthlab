"""Sound map: a 2D scatter of every audition, rendered as inline SVG.

Pure string building — no templating engine needed for a handful of
elements per point. Position comes from SpaceStore.project_2d() (PCA),
size from sub-bass fraction, and color from spectral centroid: each dot
carries its brightness (`--b`, 0..1) and the stylesheet turns it into a
hue between the map's two (style.css section 1, `--map-dark` ..
`--map-bright`), so the Colours popover and a theme move the dots too.

The PCA axes have no fixed meaning of their own — each is whatever
direction in feature-space happens to explain the most variance in
whatever's been auditioned so far. To give the axes a human label, we
correlate each axis's coordinates against the known feature columns
(already present on every row) and name the axis after whichever
feature best predicts position on it, oriented so the correlation's
sign reads left-to-right / bottom-to-top. With fewer than two points,
or no feature that varies, we fall back to a generic brightness/texture
label.
"""

from __future__ import annotations

import functools
import html
import json
import math
import re
from pathlib import Path

from ..analysis.features import FEATURE_NAMES

_ID_RE = re.compile(r"^[a-f0-9]{12}$")

_VIEW_W = 100.0
# 100 x 54: the well the plot fills on a desktop window is about 1.85 : 1
# at 1920 and 2560, so a 100 x 70 plot would sit as a square inset with a
# dark band down each side
_VIEW_H = 54.0
# The plot band starts clear of the chrome: the legend sits at y=4 and the
# y-axis caption at x=4, and points used to be laid over both.
_X_LO, _X_HI = 9.0, 95.0
_Y_LO, _Y_HI = 7.0, 48.0

# Points plotted at most. The log is capped too (SpaceStore.MAX_ROWS), but
# even a few hundred dots overlap into an unreadable blob, so the map shows
# the most recent MAX_POINTS and the PCA is fitted to exactly those.
MAX_POINTS = 300

_X_DEFAULT_LABEL = "dark ← brightness → bright"
_Y_DEFAULT_LABEL = "simple ← texture → rich"

# Axis-label font size, in viewBox user units, targeting a rendered
# caption size (~11-14px) once the SVG is stretched full-bleed by CSS.
# ~1.8% of the viewBox's larger dimension (_VIEW_W).
_AXIS_LABEL_FONT_SIZE = 1.8

# You-are-here ring: point radius plus a small constant, ~0.6% of the
# viewBox width, so the "bullseye" stays a thin halo, not a target.
_RING_PAD = 0.6

# The shipped sounds — the factory's, the recipes' and the library's — as a
# faint field behind the logged points (so the map is never an empty well
# round a few dots), each measured on the breeder's probe like a logged
# row (a recipe by its main patch) and frozen in this file, so drawing the
# map renders nothing: `tools/refresh_sound_meta.py` rewrites it after an
# engine change (`measure_shelf`), `tests/studio/test_map_view.py` holds it
# to the shelves. A point is the sound's own Use (`/fragment/<source>/load`).
SHELF_PATH = Path(__file__).with_name("presets") / "map_points.json"
SHELF_PREFIX = "shelf:"
_LOAD = {"factory": "factory", "recipes": "recipe", "library": "library"}
# the points nearest the sound on Synths, drawn bright: its neighbourhood
NEIGHBOURS = 8


def shelf_sounds() -> list[tuple[str, str, object]]:
    """Every shipped sound as (source, name, Patch): the factory's, the
    recipes' (the main patch) and the library's, once each by name."""
    from ..synth import Patch
    from .factory import FACTORY, get_factory
    from .library import LIBRARY
    from .recipes import RECIPES, recipe_patch

    out, seen = [], set()
    rows = [("factory", e["name"], lambda e=e: get_factory(e["name"])) for e in FACTORY]
    rows += [("recipes", e["name"], lambda e=e: recipe_patch(e)) for e in RECIPES]
    rows += [("library", e["name"], lambda e=e: Patch(**e["patch"])) for e in LIBRARY]
    for source, name, make in rows:
        if name in seen:
            continue
        seen.add(name)
        out.append((source, name, make()))
    return out


def measure_shelf() -> list[dict]:
    """The frozen file's rows: each shipped sound's probe features, rounded
    (a few hundred probes, ~10 ms each — a tool's job, never a page's)."""
    from ..explore.probe import probe

    out = []
    for source, name, patch in shelf_sounds():
        got = probe(patch)
        if got is None:
            continue
        feats = {k: float(f"{v:.4g}") for k, v in got.features.to_dict().items()}
        out.append({"source": source, "name": name, "features": feats})
    return out


@functools.lru_cache(maxsize=1)
def _shelf_file() -> tuple:
    try:
        return tuple(json.loads(SHELF_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return ()


def shelf_rows() -> list[dict]:
    """The frozen shelf as map rows (`SpaceStore.project_2d(background=)`):
    an id no logged row can have, `bg` set, the source a Use posts to."""
    return [
        {
            "id": f"{SHELF_PREFIX}{e['source']}:{e['name']}",
            "bg": True,
            "source": e["source"],
            "patch": {"name": e["name"]},
            "features": e["features"],
        }
        for e in _shelf_file()
        if e.get("source") in _LOAD and isinstance(e.get("features"), dict)
    ]


# The sound on Synths when it was never logged (routes/views.py measures it
# and projects it with the logged rows): plotted as "you are here", never a
# link — it has no row to load.
HERE_ID = "here"

# (low-end word, axis word, high-end word) per feature, used to build a
# label like "thin ← sub weight → sub-heavy".
_FEATURE_AXIS_WORDS: dict[str, tuple[str, str, str]] = {
    "centroid": ("dark", "brightness", "bright"),
    "rolloff": ("dull", "brightness", "airy"),
    "flatness": ("tonal", "texture", "noisy"),
    "crest": ("smooth", "punch", "punchy"),
    "sub_20_60": ("thin", "sub weight", "sub-heavy"),
    "sub_60_120": ("thin", "sub weight", "sub-heavy"),
    "low_120_250": ("light", "low-mid weight", "thick"),
    "attack_time": ("snappy", "attack", "slow"),
    "stereo_width": ("narrow", "width", "wide"),
}


# Scale to this inner percentile band rather than min/max: a single freak
# patch out at the edge of feature space used to set the whole range and
# squash every other point into a corner. Outliers clamp to the border
# instead of dictating where everything else sits.
_SPREAD_PCTL = 2.0


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted list."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (pct / 100.0) * (len(sorted_vals) - 1)
    lo_i = int(math.floor(pos))
    hi_i = min(lo_i + 1, len(sorted_vals) - 1)
    frac = pos - lo_i
    return sorted_vals[lo_i] * (1 - frac) + sorted_vals[hi_i] * frac


def _normalize(values: list[float], lo: float, hi: float) -> list[float]:
    if not values:
        return []
    ordered = sorted(values)
    vmin = _percentile(ordered, _SPREAD_PCTL)
    vmax = _percentile(ordered, 100.0 - _SPREAD_PCTL)
    if vmax - vmin < 1e-9:  # percentile band collapsed: fall back to full range
        vmin, vmax = min(values), max(values)
    span = vmax - vmin
    if span < 1e-9:
        mid = (lo + hi) / 2
        return [mid for _ in values]
    # clamp: points outside the band sit on the edge, never off-canvas
    return [min(hi, max(lo, lo + (v - vmin) / span * (hi - lo))) for v in values]


def _bright(centroid: float) -> float:
    """A spectral centre on the map's colour scale, 0 (dark) .. 1 (bright):
    log, 60 Hz .. 7.7 kHz. The dot's `--b`; the stylesheet picks its hue."""
    if centroid <= 0:
        return 0.0
    return min(1.0, max(0.0, math.log2(centroid / 60.0) / 7.0))


def bright_pct(centroid: float) -> int:
    """A spectral centre as a meter's length: the same log scale the dots'
    hue reads (60 Hz .. 7.7 kHz), so a card's "bright" bar and its dot on
    the map say the same thing."""
    return round(_bright(centroid) * 100)


def _pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma = sum(a) / n
    mb = sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va < 1e-12 or vb < 1e-12:
        return 0.0
    return cov / math.sqrt(va * vb)


def _dominant_feature(rows: list[dict], axis_key: str) -> tuple[str, float] | None:
    axis_vals = [float(row[axis_key]) for row in rows]
    if len(set(axis_vals)) < 2:
        return None
    best: tuple[str, float] | None = None
    for name in FEATURE_NAMES:
        feat_vals = [float((row.get("features") or {}).get(name, 0.0)) for row in rows]
        if len(set(feat_vals)) < 2:
            continue
        corr = _pearson(axis_vals, feat_vals)
        if best is None or abs(corr) > abs(best[1]):
            best = (name, corr)
    return best


def _axis_label(rows: list[dict], axis_key: str, default: str) -> str:
    best = _dominant_feature(rows, axis_key)
    if best is None:
        return default
    name, corr = best
    lo, word, hi = _FEATURE_AXIS_WORDS.get(name, ("low", name, "high"))
    if corr < 0:
        lo, hi = hi, lo
    return f"{lo} ← {word} → {hi}"


def _near(rows_2d, xs, ys, at: tuple[float, float] | None) -> list[int]:
    """The indices of the NEIGHBOURS points nearest `at` (the sound on
    Synths), on the plot as drawn, nearest first."""
    if at is None:
        return []
    d = sorted(
        ((x - at[0]) ** 2 + (y - at[1]) ** 2, i)
        for i, (x, y) in enumerate(zip(xs, ys, strict=True))
        if rows_2d[i].get("id") != HERE_ID and (x, y) != at
    )
    return [i for _, i in d[:NEIGHBOURS]]


def _shelf_point(row: dict, cx: float, cy: float, near: bool) -> str:
    """A shipped sound, faint (bright when near the sound on Synths): its
    name on hover, a click its Use."""
    features = row.get("features") or {}
    sub = float(features.get("sub_20_60", 0.0)) + float(features.get("sub_60_120", 0.0))
    radius = 0.8 + 1.6 * sub
    b = _bright(float(features.get("centroid", 0.0)))
    name = str(row.get("patch", {}).get("name", ""))
    said = html.escape(name, quote=True)
    vals = html.escape(json.dumps({"name": name}), quote=True)
    load = _LOAD[row["source"]]
    cls = "point shelf near" if near else "point shelf"
    return (
        f'<a class="shelf-pt" hx-post="/fragment/{load}/load" hx-vals="{vals}" '
        'hx-target="body" hx-swap="innerMorph" href="#" role="button" '
        f'aria-label="{said} — use on Synths">'
        f'<circle class="{cls}" cx="{cx:.2f}" cy="{cy:.2f}" r="{radius:.2f}" '
        f'style="--b: {b:.4f}"><title>{said} — {row["source"]}</title></circle>'
        f'<text class="point-name" x="{cx:.2f}" y="{cy - radius - 1.1:.2f}" '
        f'text-anchor="middle" style="font-size:2.4px">{said}</text></a>'
    )


def _layout(
    rows_2d: list[dict], active_id: str | None
) -> tuple[list[float], list[float], list[int]]:
    """Where each row is drawn (the plot band's x and y) and the indices of
    the points nearest the sound on Synths (its measured point, else its
    logged row), nearest first — drawn bright, and listed in the rack."""
    xs = _normalize([row["x"] for row in rows_2d], _X_LO, _X_HI)
    # up is more: SVG y grows downward, and the rotated label reads its low
    # word at the bottom (a sub-heavy sound sat at the top under "sub-heavy
    # ← … →" read bottom-up)
    ys = [
        _Y_LO + _Y_HI - y
        for y in _normalize([row["y"] for row in rows_2d], _Y_LO, _Y_HI)
    ]
    at = next(
        (
            (x, y)
            for r, x, y in zip(rows_2d, xs, ys, strict=True)
            if r.get("id") == HERE_ID or (active_id and r.get("id") == active_id)
        ),
        None,
    )
    return xs, ys, _near(rows_2d, xs, ys, at)


def map_svg(rows_2d: list[dict], active_id: str | None = None) -> str:
    if len(rows_2d) < 3:
        return (
            f'<svg viewBox="0 0 {_VIEW_W:g} {_VIEW_H:g}" '
            'xmlns="http://www.w3.org/2000/svg">'
            f'<text class="empty-hint" x="{_VIEW_W / 2:g}" y="{_VIEW_H / 2:g}" '
            f'text-anchor="middle" style="font-size:3px" fill="var(--text-dim)">'
            "Audition sounds in Breed to fill the map.</text></svg>"
        )

    xs, ys, near = _layout(rows_2d, active_id)
    logged = [r for r in rows_2d if r.get("id") != HERE_ID]
    x_label = html.escape(_axis_label(logged, "x", _X_DEFAULT_LABEL))
    y_label = html.escape(_axis_label(logged, "y", _Y_DEFAULT_LABEL))

    parts = [
        f'<svg viewBox="0 0 {_VIEW_W:g} {_VIEW_H:g}" xmlns="http://www.w3.org/2000/svg">',
        f'<text class="axis-label" x="{_VIEW_W / 2:g}" y="{_VIEW_H - 2:g}" '
        f'text-anchor="middle" style="font-size:{_AXIS_LABEL_FONT_SIZE:g}px">'
        f"{x_label}</text>",
        f'<text class="axis-label" x="4" y="{_VIEW_H / 2:g}" text-anchor="middle" '
        f'style="font-size:{_AXIS_LABEL_FONT_SIZE:g}px" '
        f'transform="rotate(-90 4 {_VIEW_H / 2:g})">{y_label}</text>',
    ]
    # a quiet grid over the plot band: tenths across and up
    grid = [
        f'<line x1="{x:.1f}" y1="{_Y_LO:g}" x2="{x:.1f}" y2="{_Y_HI:g}"/>'
        for x in (_X_LO + (_X_HI - _X_LO) * i / 10 for i in range(11))
    ] + [
        f'<line x1="{_X_LO:g}" y1="{y:.1f}" x2="{_X_HI:g}" y2="{y:.1f}"/>'
        for y in (_Y_LO + (_Y_HI - _Y_LO) * i / 6 for i in range(7))
    ]
    parts.append(f'<g class="map-grid" aria-hidden="true">{"".join(grid)}</g>')
    plotted: list[tuple[str, float, float, float]] = []
    shelf = [
        _shelf_point(row, cx, cy, i in near)
        for i, (row, cx, cy) in enumerate(zip(rows_2d, xs, ys, strict=True))
        if row.get("bg") and row.get("source") in _LOAD
    ]
    if shelf:  # behind the logged points: drawn first
        parts.append(f'<g class="map-shelf">{"".join(shelf)}</g>')
    for i, (row, cx, cy) in enumerate(zip(rows_2d, xs, ys, strict=True)):
        if row.get("bg"):
            continue
        row_id = row["id"]
        if row_id == HERE_ID:
            features = row.get("features") or {}
            sub_frac = float(features.get("sub_20_60", 0.0)) + float(
                features.get("sub_60_120", 0.0)
            )
            radius = 1.2 + 2.5 * sub_frac
            b = _bright(float(features.get("centroid", 0.0)))
            name = html.escape(
                str(row.get("patch", {}).get("name", "your sound")), quote=True
            )
            parts.append(
                '<g class="here-point"><circle class="point active here" '
                f'cx="{cx:.2f}" cy="{cy:.2f}" r="{radius:.2f}" style="--b: {b:.4f}">'
                f"<title>{name} — the sound on Synths</title></circle>"
                f'<text class="point-name here-name" x="{cx:.2f}" '
                f'y="{cy - radius - 1.1:.2f}" text-anchor="middle" '
                f'style="font-size:2.4px">{name}</text></g>'
            )
            plotted.append((row_id, cx, cy, radius))
            continue
        if not isinstance(row_id, str) or not _ID_RE.fullmatch(row_id):
            continue  # malformed id: never interpolate into markup/attrs
        features = row.get("features") or {}
        sub_frac = float(features.get("sub_20_60", 0.0)) + float(
            features.get("sub_60_120", 0.0)
        )
        centroid = float(features.get("centroid", 0.0))
        radius = 1.2 + 2.5 * sub_frac
        b = _bright(centroid)
        raw_name = row.get("patch", {}).get("name", row_id)
        name = html.escape(str(raw_name), quote=True)
        sub_pct = round(sub_frac * 100, 1)
        cls = " active" if row_id == active_id else " near" if i in near else ""
        # overlapping dots read as density instead of one opaque mass; the
        # active point stays solid so "you are here" still reads at a glance
        opacity = "" if cls else ' fill-opacity="0.62"'
        parts.append(
            f'<a hx-post="/fragment/load" hx-vals=\'{{"id": "{row_id}"}}\' '
            'hx-target="body" hx-swap="innerHTML" href="#" role="button" '
            f'aria-label="{name} — {sub_pct}% sub">'
            f'<circle class="point{cls}" cx="{cx:.2f}" cy="{cy:.2f}" '
            f'r="{radius:.2f}" style="--b: {b:.4f}"{opacity}>'
            f"<title>{name} — {sub_pct}% sub</title>"
            "</circle>"
            # the native <title> needs a hover pause; this one is instant
            f'<text class="point-name" x="{cx:.2f}" y="{cy - radius - 1.1:.2f}" '
            f'text-anchor="middle" style="font-size:2.4px">{name}</text>'
            "</a>"
        )
        plotted.append((row_id, cx, cy, radius))

    if plotted:
        # The current sound's exact projected position isn't computed
        # separately from the logged points, so we mark the nearest
        # logged point of the current patch key: an exact id match when
        # the caller knows it (active_id), else the most recently
        # logged point stands in for "where you are now".
        marker = next((p for p in plotted if p[0] == active_id), plotted[-1])
        _, mx, my, mr = marker
        parts.append(
            f'<circle class="you-are-here" cx="{mx:.2f}" cy="{my:.2f}" '
            f'r="{mr + _RING_PAD:.2f}"></circle>'
        )

    parts.append(
        f'<text class="map-legend" x="{_VIEW_W - 4:g}" y="4" text-anchor="end" '
        'style="font-size:2.4px">size = sub weight · colour = brightness</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


# Rows listed in the rack beside the plot: the newest, so the list is a
# bounded second reading of the same points (name, numbers, keyboard), not
# a second page of 300.
RACK_ROWS = 48


def _row_facts(row: dict) -> dict:
    """A row as the rack lists it; a shipped sound's (`bg`) with the route
    its Use posts to (`load`) — a logged one loads by its id."""
    features = row.get("features") or {}
    sub = float(features.get("sub_20_60", 0.0)) + float(features.get("sub_60_120", 0.0))
    centroid = float(features.get("centroid", 0.0))
    return {
        "id": row["id"],
        "name": str(row.get("patch", {}).get("name", row["id"])),
        "sub_pct": round(sub * 100, 1),
        "sub_level": round(min(1.0, sub) * 100),
        "centroid": round(centroid),
        "bright_level": bright_pct(centroid),
        "b": round(_bright(centroid), 4),
        "load": _LOAD.get(row.get("source", "")) if row.get("bg") else "",
    }


def map_facts(rows_2d: list[dict], active_id: str | None = None) -> dict:
    """What the rack beside the plot says in words and meters: the axes'
    names, the point that is "you are here" (as `map_svg` picks it) and the
    newest rows. Everything is read off the rows the plot draws."""
    rows = [
        r for r in rows_2d if isinstance(r.get("id"), str) and _ID_RE.fullmatch(r["id"])
    ]
    plotted = len(rows_2d) >= 3
    logged = [r for r in rows_2d if r.get("id") != HERE_ID]
    # the sound on Synths, measured and projected (HERE_ID), else its logged
    # row, else the newest stands in
    here = next(
        (r for r in rows_2d if r.get("id") == HERE_ID),
        next((r for r in rows if r["id"] == active_id), rows[-1] if rows else None),
    )
    newest = sorted(rows, key=lambda r: r.get("t", 0), reverse=True)[:RACK_ROWS]
    # the sound's neighbours, as the plot lights them: its
    # neighbourhood in words, a click each to load
    near = _layout(rows_2d, active_id)[2] if plotted else []
    here_name = (here or {}).get("patch", {}).get("name")
    return {
        "plotted": plotted,
        "count": len(rows),
        "shelf": sum(1 for r in rows_2d if r.get("bg")),
        "max_points": MAX_POINTS,
        "x_label": _axis_label(logged, "x", _X_DEFAULT_LABEL) if plotted else "",
        "y_label": _axis_label(logged, "y", _Y_DEFAULT_LABEL) if plotted else "",
        "here": _row_facts(here) if here and plotted else None,
        "rows": [_row_facts(r) for r in newest] if plotted else [],
        "near": [
            _row_facts(rows_2d[i])
            for i in near
            if (rows_2d[i].get("bg") or _ID_RE.fullmatch(str(rows_2d[i].get("id"))))
            # the sound on Synths is a shipped one too: not its own neighbour
            and not (here and rows_2d[i].get("patch", {}).get("name") == here_name)
        ],
    }
