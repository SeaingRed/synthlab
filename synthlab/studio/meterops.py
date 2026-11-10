"""A meter change on the working loop: every grid it holds fitted bar by
bar (`song_model.fit_pattern`, the one fitting a song section does to the
patterns it plays). A bar keeps its first beats and where everything sits
in them, the step length kept: 4/4 -> 3/4 drops each bar's fourth beat,
3/4 -> 4/4 gives each bar an empty one. Pure: arrangement and lane dicts
in, new ones out.
"""

from __future__ import annotations

from dataclasses import replace

from . import meter as _meter
from .song_model import fit_pattern

_NOTE_KEYS = ("cells", "velocities", "slides", "cuts")
_DRUM_KEYS = ("drums", "drum_vel", "drum_hits")


def _fit_notes(lane: dict, spb: int, frm: str, to: str) -> dict:
    """A note lane's cells / velocities / slides / cuts (what `lane` holds
    of them) on a `spb`-step grid in `frm`, as the grid of `to`."""
    pat = {"kind": "notes", "meter": frm, "steps_per_bar": spb}
    pat |= {k: lane.get(k) or ([] if k != "velocities" else {}) for k in _NOTE_KEYS}
    got = fit_pattern(pat, to)
    return {k: got[k] for k in _NOTE_KEYS if k in lane} | {
        "steps_per_bar": got["steps_per_bar"]
    }


def fit_drum_lane(lane: dict, frm: str, to: str) -> dict:
    """A drum lane (`drums`, `drum_vel`, `drum_steps`, `drum_hits`, as an
    arrangement or a beat variation holds it) in another meter."""
    pat = {
        "kind": "drums",
        "meter": frm,
        "steps": int(lane.get("drum_steps", 16)),
        "drums": lane.get("drums") or [],
        "drum_vel": lane.get("drum_vel") or {},
        "hits": lane.get("drum_hits") or {},
    }
    got = fit_pattern(pat, to)
    out = dict(lane)
    out |= {
        "drums": got["drums"],
        "drum_vel": got["drum_vel"],
        "drum_steps": got["steps"],
    }
    if "drum_hits" in lane or got.get("hits"):
        out["drum_hits"] = got.get("hits") or {}
    return out


def fit_arrangement(arr, to: str):
    """The loop's arrangement in meter `to`: both grids fitted, the step
    counts the new bar's (`meter.like`: 16 sixteenths a 4/4 bar -> 12 in
    3/4, 14 in 7/8)."""
    frm = _meter.clean(arr.meter)
    to = _meter.clean(to)
    if frm == to:
        return arr
    notes = _fit_notes(
        {k: getattr(arr, k) for k in _NOTE_KEYS}, arr.steps_per_bar, frm, to
    )
    drums = fit_drum_lane(
        {
            "drums": arr.drums,
            "drum_vel": arr.drum_vel,
            "drum_steps": arr.drum_steps,
            "drum_hits": arr.drum_hits,
        },
        frm,
        to,
    )
    return replace(
        arr,
        meter=to,
        **notes,
        drums=drums["drums"],
        drum_vel=drums["drum_vel"],
        drum_steps=drums["drum_steps"],
        drum_hits=drums.get("drum_hits") or {},
    )


def fit_layer(lay: dict, spb: int, frm: str, to: str) -> dict:
    """A layer slot (its cells on the loop's grid, its own `seq`) in `to`."""
    if frm == to:
        return lay
    out = dict(lay)
    seq = dict(lay.get("seq") or {})
    lane = {"cells": lay.get("cells") or []}
    lane |= {k: seq[k] for k in ("velocities", "slides", "cuts") if k in seq}
    got = _fit_notes(lane, spb, frm, to)
    out["cells"] = got["cells"]
    if lay.get("seq") is not None:
        out["seq"] = seq | {
            k: got[k] for k in ("velocities", "slides", "cuts") if k in seq
        }
    return out


def counts(arr) -> int:
    """What a grid holds that a meter change may drop: its notes and hits."""
    return len(arr.cells) + len(arr.drums)


def beat_fit(lane: dict, to: str) -> dict:
    """A generated note lane (`gen/notes.py`: 4/4, sixteenths, `bars`
    bars) on the beats of meter `to` (`meter.beat_step`), at that meter's
    sixteenth grid: each held note keeps its start's beat and ends where
    its end's beat is (at least a step), velocities and slides on the
    starts. 4/4 is the lane itself."""
    to = _meter.clean(to)
    if to == _meter.DEFAULT:
        return lane
    six = _meter.parse(to).sixteenths
    src = int(lane.get("steps_per_bar", 16))

    def at(s: int, end: bool = False) -> int | None:
        bar, i = divmod(int(s), src)
        if end and i == 0:  # a bar line: the end of the bar before
            return bar * six
        got = _meter.beat_step(i * 16 // src, to)
        if got is None:
            return (bar + 1) * six if end else None
        return bar * six + got

    by_pitch: dict[str, list[int]] = {}
    for p, s in lane.get("cells") or []:
        by_pitch.setdefault(p, []).append(int(s))
    cuts = {(p, int(s)) for p, s in lane.get("cuts") or []}
    cells: set[tuple[str, int]] = set()
    new_cuts = []
    for p, steps in by_pitch.items():
        runs: list[list[int]] = []
        for s in sorted(set(steps)):
            if not runs or s != runs[-1][1] + 1 or (p, s) in cuts:
                runs.append([s, s])
            runs[-1][1] = s
        for a, b in runs:
            na = at(a)
            if na is None:
                continue
            nb = max(na + 1, at(b + 1, end=True) or na + 1)
            cells |= {(p, x) for x in range(na, nb)}
            if (p, a) in cuts:
                new_cuts.append([p, na])
    vel = {}
    for k, v in (lane.get("velocities") or {}).items():
        x = at(int(k)) if str(k).isdigit() else None
        if x is not None:
            vel.setdefault(str(x), v)
    slides = sorted({at(s) for s in lane.get("slides") or []} - {None})
    return {
        **lane,
        "steps_per_bar": six,
        "cells": sorted([list(c) for c in cells], key=lambda c: (c[0], c[1])),
        "cuts": sorted(new_cuts, key=lambda c: (c[0], c[1])),
        "velocities": vel,
        "slides": slides,
    }
