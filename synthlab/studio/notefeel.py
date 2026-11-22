"""A note's own feel: where it really starts, where it really ends and how
hard it was struck — per NOTE, not per step (`Arrangement.note_feel`).

The note grid is a step grid and stays one: a note is a run of one row's
cells (`arrange._merge_runs`), its velocity sits on its start step and is
shared by every note starting there, and it sounds `arrange.NOTE_GATE` of
its run. A take played on the keys is not on the steps: a chord's notes are
struck at different strengths, a little apart, and let go between the
lines. `note_feel` keeps that, per note, beside the cells:

    "<pitch>.<step>": [start, end, share]

keyed by the note's row and its START step (the cell its run starts on),

- `start`: where it starts, in steps from its start step's line
  (-0.5..0.5) — not moved by swing or by the step's `note_nudge`;
- `end`: where it ends, in steps from its run's END line (the line after
  its last cell: -1.5..0.5), with no gate — so a note made a step longer
  or shorter on the grid keeps where between the lines it is let go;
- `share`: its velocity as a share of its start step's (0..1, the step
  holding the loudest note starting there), so the velocity lane and its
  tools still move a chord as one and keep its balance.

ABSENT when unset (`schema.ARR_OPTIONAL`): no saved state, loop, pattern or
render key gains it, and a note without an entry plays exactly as it always
did. A key that names no note's start is dropped wherever the lane is
written down (`clean`). Every tool that moves a note carries its entry
(`patternops`); a note drawn fresh has none; Q at 100 % clears them.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection

from ..sequencer import midi, name_of

FIELD = "note_feel"
START_RANGE = (-0.5, 0.5)  # steps from the start step's line
END_RANGE = (-1.5, 0.5)  # steps from the run's end line
MIN_STEPS = 1.0 / 64.0  # the shortest a felt note sounds, in steps
DIGITS = 6  # a millionth of a step: far under a sample at any tempo


def key(pitch: str, step: int) -> str:
    return f"{pitch}.{int(step)}"


def split(k) -> tuple[str, int] | None:
    """`"A1.4"` -> ("A1", 4); None when it is not one."""
    if not isinstance(k, str) or len(k) > 16:
        return None
    pitch, dot, raw = k.rpartition(".")
    if not dot or not pitch or not raw.isascii() or not raw.isdigit():
        return None
    return pitch, int(raw)


def value(v) -> list | None:
    """One entry, clamped to its ranges and rounded (`DIGITS`); None when
    it is not three finite numbers or its share is not above 0."""
    if not isinstance(v, list | tuple) or len(v) != 3:
        return None
    try:
        s, e, share = (float(x) for x in v)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(x) for x in (s, e, share)) or share <= 0:
        return None
    return [
        round(min(START_RANGE[1], max(START_RANGE[0], s)), DIGITS),
        round(min(END_RANGE[1], max(END_RANGE[0], e)), DIGITS),
        round(min(1.0, share), DIGITS),
    ]


def starts(cells, cuts=()) -> set[tuple[str, int]]:
    """Every note's (row, start step): a cell whose step before is empty on
    its row, or that is a cut (two notes side by side)."""
    on = {(c[0], int(c[1])) for c in cells or ()}
    cut = {(c[0], int(c[1])) for c in cuts or ()}
    return {(p, s) for p, s in on if (p, s - 1) not in on or (p, s) in cut}


def clean(feel, cells, cuts=()) -> dict:
    """`feel` as it is kept: only on a note's start (`starts`), each value
    clamped (`value`) — so a note erased, merged into another or drawn
    over leaves none behind (the cuts' and the nudge's rule)."""
    if not feel:
        return {}
    on = starts(cells, cuts)
    out: dict = {}
    for k, v in feel.items():
        at = split(k)
        if at is None or at not in on:
            continue
        if (clean_v := value(v)) is not None:
            out[key(*at)] = clean_v
    return out


def of(arr) -> dict:
    """The lane's feel as it plays: none under an arp, which plays one note
    a step on the grid (as `note_nudge`, `arrange.nudge_of`)."""
    if getattr(arr, "arp_mode", "off") != "off":
        return {}
    return getattr(arr, FIELD, None) or {}


def end_line(cells, cuts, pitch: str, step: int) -> int:
    """The END line of the note that starts on (`pitch`, `step`): the step
    after its last cell (its run, split at the row's cuts)."""
    on = {int(c[1]) for c in cells if c[0] == pitch}
    cut = {int(c[1]) for c in cuts or () if c[0] == pitch}
    s = int(step) + 1
    while s in on and s not in cut:
        s += 1
    return s


# -- step and row remaps: the tools' (patternops) and the song's tiling ------


def _in(step: int, total: int) -> bool:
    return 0 <= step < total


def remap(feel: dict, total: int, fn: Callable[[int], int | None], rows=None) -> dict:
    """Each in-window note's entry moved to `fn(step)` (a shift); one
    outside the window (or off `rows`) is kept where it is."""
    out: dict = {}
    for k, v in (feel or {}).items():
        at = split(k)
        if at is None:
            continue
        p, s = at
        if _in(s, total) and (rows is None or p in rows):
            t = fn(s)
            if t is not None:
                out[key(p, t)] = v
        else:
            out[k] = v
    return out


def reverse(feel: dict, cells, cuts, total: int, rows=None) -> dict:
    """Each in-window note's entry on its mirror image's start, early for
    late: the note that ended `e` past its end line now starts `-e` from
    its start line, and ends `-start` from its end line (clamped to the
    ranges). Entries outside the window are kept."""
    out: dict = {}
    for k, v in (feel or {}).items():
        at = split(k)
        if at is None:
            continue
        p, s = at
        if not (_in(s, total) and (rows is None or p in rows)):
            out[k] = v
            continue
        end = end_line(cells, cuts, p, s)
        mirrored = value([-float(v[1]), -float(v[0]), v[2]])
        if mirrored is not None:
            out[key(p, total - end)] = mirrored
    return out


def duplicate(feel: dict, total: int, rows=None) -> dict:
    """The window [0, total) copied to [total, 2 total), as
    `patternops.duplicate` copies its cells; what was there is replaced."""
    out = {
        k: v
        for k, v in (feel or {}).items()
        if (at := split(k)) is None
        or not (total <= at[1] < 2 * total and (rows is None or at[0] in rows))
    }
    for k, v in (feel or {}).items():
        at = split(k)
        if at and _in(at[1], total) and (rows is None or at[0] in rows):
            out[key(at[0], at[1] + total)] = v
    return out


def repeat(feel: dict, old_total: int, new_total: int) -> dict:
    """The window repeated into a grown grid (`patternops.repeat_into`):
    unless the new steps already hold entries (hidden notes, shown again)."""
    feel = feel or {}
    if any((at := split(k)) and old_total <= at[1] < new_total for k in feel):
        return dict(feel)
    out = dict(feel)
    for k, v in feel.items():
        at = split(k)
        if at and _in(at[1], old_total):
            for t in range(at[1] + old_total, new_total, old_total):
                out[key(at[0], t)] = v
    return out


def transpose(feel: dict, total: int, ladder: list[str], by: int, rows=None) -> dict:
    """Each in-window note's entry on its new row, `by` places along
    `ladder` (`patternops.transpose`); a note that cannot move keeps it."""
    index = {p: i for i, p in enumerate(ladder)}
    out: dict = {}
    for k, v in (feel or {}).items():
        at = split(k)
        if at is None:
            continue
        p, s = at
        i = index.get(p)
        if (
            _in(s, total)
            and (rows is None or p in rows)
            and i is not None
            and 0 <= i + by < len(ladder)
        ):
            out[key(ladder[i + by], s)] = v
        else:
            out[k] = v
    return out


def shifted(feel: dict, semis: int) -> dict:
    """Every entry `semis` semitones away (a following layer's octave);
    one that would leave MIDI 0..127 is dropped, as its note is."""
    out: dict = {}
    for k, v in (feel or {}).items():
        at = split(k)
        if at is None:
            continue
        m = midi(at[0]) + semis
        if 0 <= m <= 127:
            out[key(name_of(m), at[1])] = v
    return out


def tile(feel: dict, period: int, total: int, lo: int = 0, hi: int | None = None):
    """The entries looping every `period` steps out to `total` (from `lo` up
    to `hi`), as `song_model._tile` tiles the cells they sit on."""
    hi = total if hi is None else min(hi, total)
    out: dict = {}
    for k, v in (feel or {}).items():
        at = split(k)
        if at is None or not 0 <= at[1] < period:
            continue
        for r in range(-(-(hi - lo) // period)):
            t = lo + r * period + at[1]
            if t < hi:
                out[key(at[0], t)] = v
    return out


def crop(feel: dict, lo: int, hi: int) -> dict:
    """The entries on steps [lo, hi), moved to start at 0."""
    out: dict = {}
    for k, v in (feel or {}).items():
        at = split(k)
        if at and lo <= at[1] < hi:
            out[key(at[0], at[1] - lo)] = v
    return out


def regrid(feel: dict, bars: int, spb: int, to: int) -> dict:
    """A lane's entries on its `spb` grid moved onto a `to`-step grid, as
    `song_model._regrid` moves its cells: onto a finer grid (a coarse step
    is `to / spb` fine ones, its run as many times longer) the same time,
    in the finer steps; onto a coarser one the entries are dropped — a
    run's end there is not a line of the old grid, so the note plays as a
    grid note (gated) rather than at a wrong end."""
    if not feel or spb == to:
        return dict(feel or {})
    if spb > to:
        return {}
    k_ = max(1, round(to / spb))
    out: dict = {}
    for k, v in feel.items():
        at = split(k)
        if at and 0 <= at[1] < max(1, bars) * spb:
            out[key(at[0], at[1] * k_)] = [float(v[0]) * k_, float(v[1]) * k_, v[2]]
    return out


def heard(feel: dict, rows: Collection[str] | None = None) -> dict:
    """The entries on `rows` (None: all)."""
    if rows is None:
        return dict(feel or {})
    return {k: v for k, v in (feel or {}).items() if (at := split(k)) and at[0] in rows}


# -- the roll's drawing -----------------------------------------------------


def _pct(x: float) -> str:
    return f"{round(x * 100):+d}%"


def marks(feel, cells, cuts=()) -> dict[tuple[str, int], tuple[str, str, str]]:
    """What the piano roll draws of each felt note (grids.html), on its
    start cell and its last cell: `(class, style, title)`. Custom properties
    on the cells it already has — no new element: `--fs` its start off its
    step's line (a share of a step: `felt-s`), `--fe` how much of its last
    cell it leaves silent (`felt-e`; let go past the line: `felt-past`).
    The title on its start cell says it in words, its velocity share too
    (the lane under the grid is the step's)."""
    if not feel:
        return {}
    on = starts(cells, cuts)
    out: dict = {}
    for k, v in feel.items():
        at = split(k)
        if at is None or at not in on:
            continue
        p, s = at
        o, e, share = (float(x) for x in v)
        last = end_line(cells, cuts, p, s) - 1
        title = (
            f"played {_pct(o)} of a step {'late' if o >= 0 else 'early'} · let go "
            f"{_pct(abs(e))} of a step {'past' if e > 0 else 'before'} the line · "
            f"{round(share * 100)}% of the step's velocity"
        )
        cut = (o if last == s else 0.0) - e
        end = (
            "felt-e" + (" felt-past" if cut < 0 else ""),
            f"--fe: {max(0.0, cut):.3g}",
        )
        if last == s:
            out[(p, s)] = (f"felt-s {end[0]}", f"--fs: {o:.3g}; {end[1]}", title)
        else:
            out[(p, s)] = ("felt-s", f"--fs: {o:.3g}", title)
            out[(p, last)] = (end[0], end[1], "")
    return out
