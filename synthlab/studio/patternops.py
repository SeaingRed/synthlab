"""Pattern tools: the channel-rack / piano-roll edits a step grid needs
beyond toggling cells — shift, reverse, duplicate, fill every N, transpose,
and the velocity lane's humanise / ramp / reset.

Pure functions over the grids' stored shapes, shared by the note grid
(``[pitch, step]`` cells, a ``{step: vel}`` lane, a ``[step]`` slide list)
and the drum grid (``[voice, step]`` cells, ``{step: vel}``). Every edit
acts on the **window** — the steps the grid shows (``0 <= step < total``)
and, when ``rows`` is given, only those rows. A cell outside it (a note
written under another key, a hit past a shrunk grid) is kept untouched and
hidden, the same rule `_carry_over` follows: no tool ever deletes what you
cannot see.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Collection

from ..sequencer import midi
from . import schema

Cells = list[list]


def _split(cells, total: int, rows: Collection | None):
    """(in-window cells as (row, step) tuples, everything else as-is)."""
    inside, outside = [], []
    for c in cells:
        row, step = c[0], int(c[1])
        if 0 <= step < total and (rows is None or row in rows):
            inside.append((row, step))
        else:
            outside.append([row, step])
    return inside, outside


def _join(moved, outside) -> Cells:
    seen = {(r, s) for r, s in moved} | {(c[0], c[1]) for c in outside}
    return [list(c) for c in sorted(seen)]


def remap(cells, total, rows, fn: Callable[[int], int | None]) -> Cells:
    inside, outside = _split(cells, total, rows)
    moved = [(r, t) for r, s in inside if (t := fn(s)) is not None]
    return _join(moved, outside)


# -- step remaps: one function of the step, applied to cells and lanes alike


def shift_fn(total: int, by: int) -> Callable[[int], int]:
    """Rotate by `by` steps inside the window (FL's shift left/right):
    what falls off one end comes back at the other, so a loop stays whole."""
    return lambda s: (s + by) % total


def reverse_fn(total: int) -> Callable[[int], int]:
    return lambda s: total - 1 - s


def shift(cells, total: int, by: int, rows=None) -> Cells:
    return remap(cells, total, rows, shift_fn(total, by))


def reverse(cells, total: int, rows=None) -> Cells:
    """Mirror the window in time. A held note (a run of adjacent cells) is
    still one run, just played backwards along the bar."""
    return remap(cells, total, rows, reverse_fn(total))


def remap_lane(lane: dict, total: int, fn) -> dict:
    """A `{str(step): value}` lane through the same step remap: in-window
    steps move, the rest are kept."""
    out: dict = {}
    for k, v in lane.items():
        s = int(k) if str(k).isascii() and str(k).isdigit() else -1
        out[str(fn(s)) if 0 <= s < total else k] = v
    return out


def remap_steps(steps, total: int, fn) -> list[int]:
    """A `[step]` list (the slide lane) through the same step remap."""
    return sorted({fn(s) if 0 <= s < total else s for s in map(int, steps)})


# -- reversing notes: a note's velocity and slide live on its START step.
# Mirrored step by step they land on the mirrored note's END, where nothing
# reads them (a held A1 on 0-3 at 0.4 came back on 12-15 at the default,
# its 0.4 parked on step 15). So they move per note: old start -> the
# mirrored note's new start.


def reversed_starts(cells, cuts, total: int, rows) -> dict[int, list[int]]:
    """{a note's start step: where its mirror image starts} for the notes
    in the window — runs of one row on adjacent steps, split at the row's
    cuts (as `arrange._merge_runs` joins them). Rows are separate notes, so
    one start step can have several."""
    inside, _outside = _split(cells, total, rows)
    cut = {(c[0], int(c[1])) for c in cuts}
    by_row: dict = {}
    for row, step in inside:
        by_row.setdefault(row, set()).add(step)
    moved: dict[int, list[int]] = {}
    for row, steps in by_row.items():
        start = prev = None
        for s in [*sorted(steps), None]:
            if start is not None and (s is None or s != prev + 1 or (row, s) in cut):
                moved.setdefault(start, []).append(total - 1 - prev)
                start = None
            if start is None:
                start = s
            prev = s
    return moved


def reverse_note_lane(lane: dict, total: int, moved: dict[int, list[int]]) -> dict:
    """A notes velocity lane, reversed: a note's value goes to its mirror
    image's start (the earliest note wins a step two of them land on); a
    value on a step that starts no note is mirrored in place, unless a
    note now starts there — that note keeps what it had, the default
    included. Out-of-window steps are kept, as in `remap_lane`."""
    taken = {t for starts in moved.values() for t in starts}
    kept: dict = {}
    notes: dict = {}
    loose: dict = {}
    for k, v in sorted(lane.items(), key=lambda kv: _step(kv[0])):
        s = _step(k)
        if not 0 <= s < total:
            kept[k] = v
        elif s in moved:
            for t in moved[s]:
                notes.setdefault(str(t), v)
        elif total - 1 - s not in taken:
            loose[str(total - 1 - s)] = v
    return kept | loose | notes


def reverse_note_steps(steps, total: int, moved: dict[int, list[int]]) -> list[int]:
    """The slide lane, reversed the same way (`reverse_note_lane`)."""
    taken = {t for starts in moved.values() for t in starts}
    out: set[int] = set()
    for s in map(int, steps):
        if not 0 <= s < total:
            out.add(s)
        elif s in moved:
            out.update(moved[s])
        elif total - 1 - s not in taken:
            out.add(total - 1 - s)
    return sorted(out)


def _step(k) -> int:
    k = str(k)
    return int(k) if k.isascii() and k.isdigit() else -1


# -- length --------------------------------------------------------------


def can_duplicate(bars: int, choices: Collection[int]) -> bool:
    return bars * 2 in choices


def duplicate(cells, bars: int, steps_per_bar: int, rows=None) -> Cells:
    """Twice as long, the second half a copy of the first (FL's "clone"):
    the window [0, total) is copied to [total, 2*total). What was stored past
    the old end (hidden by a shrink) is replaced by the copy — the grid now
    shows those steps, and it shows the copy."""
    total = bars * steps_per_bar
    inside, outside = _split(cells, total, rows)
    outside = [
        c
        for c in outside
        if not (total <= c[1] < 2 * total and (rows is None or c[0] in rows))
    ]
    return _join(inside + [(r, s + total) for r, s in inside], outside)


def duplicate_lane(lane: dict, total: int) -> dict:
    out = {k: v for k, v in lane.items() if not _in(k, total, 2 * total)}
    for k, v in lane.items():
        if _in(k, 0, total):
            out[str(int(k) + total)] = v
    return out


def duplicate_steps(steps, total: int) -> list[int]:
    kept = {s for s in map(int, steps) if not total <= s < 2 * total}
    return sorted(kept | {s + total for s in kept if 0 <= s < total})


def _in(k, lo: int, hi: int) -> bool:
    k = str(k)
    return k.isascii() and k.isdigit() and lo <= int(k) < hi


def repeat_into(cells, old_total: int, new_total: int, rows=None) -> Cells:
    """Grow a window by repeating it (1 -> 4 bars copies bar 1 into 2-4),
    the loop-extension rule `beatgen.extend_bars` gives the drums. Steps in
    the new range that already hold cells (hidden by an earlier shrink) are
    left as they were: growing back shows them rather than overwriting."""
    inside, outside = _split(cells, old_total, rows)
    held = any(
        old_total <= c[1] < new_total and (rows is None or c[0] in rows)
        for c in outside
    )
    if held or new_total <= old_total:
        return [list(c) for c in cells]
    copies = [
        (r, s + k)
        for r, s in inside
        for k in range(old_total, new_total, old_total)
        if s + k < new_total
    ]
    return _join(inside + copies, outside)


def repeat_lane(lane: dict, old_total: int, new_total: int) -> dict:
    if any(_in(k, old_total, new_total) for k in lane):
        return dict(lane)
    out = dict(lane)
    for k, v in lane.items():
        if _in(k, 0, old_total):
            for dst in range(int(k) + old_total, new_total, old_total):
                out[str(dst)] = v
    return out


def repeat_steps(steps, old_total: int, new_total: int) -> list[int]:
    steps = sorted(set(map(int, steps)))
    if any(old_total <= s < new_total for s in steps):
        return steps
    base = [s for s in steps if 0 <= s < old_total]
    extra = {s + k for s in base for k in range(old_total, new_total, old_total)}
    return sorted(set(steps) | {s for s in extra if s < new_total})


# -- rows ----------------------------------------------------------------


def fill(cells, row, total: int, every: int, offset: int = 0) -> Cells:
    """One row set to a hit every `every` steps from `offset` (FL's "fill
    each 2/4/8 steps"); the row's hits outside the window are kept."""
    every = max(1, int(every))
    others = [list(c) for c in cells if c[0] != row or not 0 <= int(c[1]) < total]
    return _join([(row, s) for s in range(offset % every, total, every)], others)


def clear(cells, total: int, rows=None) -> Cells:
    return _join([], _split(cells, total, rows)[1])


def transpose(
    cells, total: int, ladder: list[str], by: int, rows=None
) -> tuple[Cells, int]:
    """Move every in-window note `by` places along `ladder` (the key's
    tones over several octaves, low to high): +1 is the next scale tone, a
    ladder-length step an octave. Returns (cells, notes that could not move
    — off the ladder's ends, or not in the key — which stay where they
    were). Two notes landing on one cell merge."""
    index = {p: i for i, p in enumerate(ladder)}
    inside, outside = _split(cells, total, rows)
    moved, stuck = [], 0
    for r, s in inside:
        i = index.get(r)
        if i is None or not 0 <= i + by < len(ladder):
            moved.append((r, s))
            stuck += 1
        else:
            moved.append((ladder[i + by], s))
    return _join(moved, outside), stuck


# -- the velocity lane ---------------------------------------------------


def humanise(
    lane: dict, steps: Collection[int], amount: float, seed: int, default: float
) -> dict:
    """Jitter the velocity of each step that plays, by up to ±`amount`,
    seeded (a re-roll is a new seed; the same seed is the same feel)."""
    rng = random.Random(seed)
    out = dict(lane)
    for s in sorted(steps):
        v = float(lane.get(str(s), default))
        out[str(s)] = round(min(1.0, max(0.05, v + rng.uniform(-amount, amount))), 3)
    return out


def ramp(lane: dict, steps: Collection[int], total: int, lo: float, hi: float) -> dict:
    """A crescendo (lo < hi) or fade across the window, on the steps that
    play: the build-up an FL user draws in the graph editor."""
    out = dict(lane)
    span = max(1, total - 1)
    for s in sorted(steps):
        out[str(s)] = round(lo + (hi - lo) * (s / span), 3)
    return out


def reset_lane(lane: dict, total: int) -> dict:
    """The window back to the default velocity (absent keys); steps past
    it keep theirs, like any hidden cell."""
    return {k: v for k, v in lane.items() if not _in(k, 0, total)}


def accent(
    lane: dict, steps: Collection[int], every: int, hi: float, lo: float
) -> dict:
    """Beat accents: steps on the `every` grid at `hi`, the rest at `lo`."""
    out = dict(lane)
    for s in sorted(steps):
        out[str(s)] = hi if s % every == 0 else lo
    return out


# -- whole-lane tools: the note grid's and the drum grid's buttons -------
# One implementation for every surface that edits a pattern: Perform's
# grids (the working loop, `routes/pattern.py`) and the Song drawer's pool
# patterns (`routes/song.py`). A lane is a plain dict: notes carry `bars`,
# `steps_per_bar`, `cells`, `cuts`, `velocities`, `slides`; drums carry
# `bars`, `steps`, `drums`, `drum_vel`.

HUMANISE = 0.15  # how far humanise jitters a velocity
RAMP = (0.35, 1.0)  # a ramp's ends
ACCENT = (1.0, 0.65)  # on the beat, between
NOTE_VEL = 0.9  # a note step's velocity when its lane holds none (build_song)

NOTE_TOOLS = frozenset(
    {
        "shift_left",
        "shift_right",
        "reverse",
        "duplicate",
        "clear",
        "up",
        "down",
        "oct_up",
        "oct_down",
        "vel_humanise",
        "vel_ramp",
        "vel_accent",
        "vel_reset",
        "tidy",
    }
)


class ToolRefused(ValueError):
    """A tool that cannot apply here (the message says why): a toast."""


def _steps_fn(op: str, total: int):
    if op == "reverse":
        return reverse_fn(total)
    return shift_fn(total, -1 if op == "shift_left" else 1)


def _vel_tool(op, lane: dict, hits, total: int, per_beat: int, default, seed) -> dict:
    if op == "vel_humanise":
        return humanise(lane, hits, HUMANISE, seed, default)
    if op == "vel_ramp":
        return ramp(lane, hits, total, *RAMP)
    if op == "vel_accent":
        return accent(lane, hits, max(1, per_beat), *ACCENT)
    return reset_lane(lane, total)


def notes_tool(
    op: str,
    lane: dict,
    *,
    rows: Collection,
    ladder: list[str],
    octave: int,
    choices: Collection[int],
    seed: int = 0,
    arp_on: bool = False,
) -> tuple[dict, str]:
    """`op` on a notes lane, over the window (`rows` x the lane's steps).
    Returns (the new lane, a note for the user or ""). `ladder` and
    `octave` (tones per octave) are the key's, for transposition. `arp_on`:
    the lane is arpeggiated, so every step reads its own velocity and a
    reverse mirrors the lane step by step (otherwise per note:
    `reverse_note_lane`). Raises `ToolRefused` when the op can't apply,
    KeyError for an unknown op."""
    if op not in NOTE_TOOLS:
        raise KeyError(op)
    out = dict(lane)
    bars, spb = int(lane["bars"]), int(lane["steps_per_bar"])
    total = bars * spb
    cells, cuts = lane["cells"], lane.get("cuts") or []
    note = ""
    if op in ("shift_left", "shift_right", "reverse"):
        fn = _steps_fn(op, total)
        # a cut sits between two steps (c-1 | c): mirrored, it lands at
        # total - c, the new boundary between the same two notes
        cut_fn = (lambda c: total - c if c > 0 else None) if op == "reverse" else fn
        out |= {
            "cells": remap(cells, total, rows, fn),
            "cuts": remap(cuts, total, rows, cut_fn),
            "velocities": remap_lane(lane["velocities"], total, fn),
            "slides": remap_steps(lane["slides"], total, fn),
        }
        if "note_chance" in lane:
            out["note_chance"] = remap_lane(lane["note_chance"], total, fn)
        if op == "reverse" and not arp_on:
            moved = reversed_starts(cells, cuts, total, rows)
            out |= {
                "velocities": reverse_note_lane(lane["velocities"], total, moved),
                "slides": reverse_note_steps(lane["slides"], total, moved),
            }
            if "note_chance" in lane:
                out["note_chance"] = reverse_note_lane(
                    lane["note_chance"], total, moved
                )
    elif op == "duplicate":
        if not can_duplicate(bars, choices):
            raise ToolRefused(
                f"{bars} bars can't double — the grid's longest is {max(choices)}."
            )
        out |= {
            "bars": bars * 2,
            "cells": duplicate(cells, bars, spb, rows),
            "cuts": duplicate(cuts, bars, spb, rows),
            "velocities": duplicate_lane(lane["velocities"], total),
            "slides": duplicate_steps(lane["slides"], total),
        }
        if "note_chance" in lane:
            out["note_chance"] = duplicate_lane(lane["note_chance"], total)
    elif op == "clear":
        out |= {
            "cells": clear(cells, total, rows),
            "cuts": clear(cuts, total, rows),
            "velocities": reset_lane(lane["velocities"], total),
            "slides": [x for x in lane["slides"] if not 0 <= int(x) < total],
        }
        if "note_chance" in lane:
            out["note_chance"] = reset_lane(lane["note_chance"], total)
    elif op in ("up", "down", "oct_up", "oct_down"):
        by = octave if op.startswith("oct") else 1
        by = by if op.endswith("up") else -by
        moved, stuck = transpose(cells, total, ladder, by, rows)
        out |= {"cells": moved, "cuts": transpose(cuts, total, ladder, by, rows)[0]}
        if stuck:
            plural = "s" if stuck > 1 else ""
            note = f"{stuck} note{plural} couldn't move that far — left in place."
        elif any(c[0] not in rows and 0 <= int(c[1]) < total for c in moved):
            note = "Some notes moved past the grid's rows: try the 2 oct range."
    elif op.startswith("vel_"):
        hits = {int(c[1]) for c in cells if c[0] in rows and 0 <= int(c[1]) < total}
        out["velocities"] = _vel_tool(
            op, lane["velocities"], hits, total, spb // 4, NOTE_VEL, seed
        )
    else:  # tidy
        kept = [c for c in cells if c[0] in rows and 0 <= int(c[1]) < total]
        gone = len(cells) - len(kept)
        out["cells"] = kept
        note = f"Removed {gone} hidden note{'s' if gone != 1 else ''}." if gone else ""
    on = {(c[0], int(c[1])) for c in out["cells"]}  # a cut belongs to a note
    out["cuts"] = [list(c) for c in out.get("cuts") or [] if (c[0], int(c[1])) in on]
    if lane.get("note_nudge"):  # timing inside a step travels as a velocity does
        out["note_nudge"] = schema.clean_nudge(
            _nudge_tool(op, lane, total, rows, arp_on), out["cells"]
        )
    return out, note


def _nudge_tool(op: str, lane: dict, total: int, rows, arp_on: bool) -> dict:
    """The lane's `note_nudge` through `op` (`notes_tool`): moved, mirrored
    and doubled with the velocities; a step's timing reads early for late
    when its note runs backwards; a clear or a transpose (no step moves)
    keeps what is left on a note."""
    nudge = lane["note_nudge"]
    if op in ("shift_left", "shift_right"):
        return remap_lane(nudge, total, _steps_fn(op, total))
    if op == "reverse":
        flip = {k: -v for k, v in nudge.items()}
        if arp_on:
            return remap_lane(flip, total, reverse_fn(total))
        moved = reversed_starts(lane["cells"], lane.get("cuts") or [], total, rows)
        return reverse_note_lane(flip, total, moved)
    if op == "duplicate":
        return duplicate_lane(nudge, total)
    return dict(nudge)


# -- the piano roll: notes as runs ------------------------------------------
# A note is a run of one row's adjacent cells, split at the row's cuts
# (`arrange._merge_runs`). The roll's edits — move, copy, delete, resize,
# legato, halve, double, stamp — read the window as notes, change the
# notes, and write cells and cuts back, so a note carries what belongs to
# it: its velocity and its slide sit on its START step (per step, shared
# by every note starting there: spec decision 3), and a cut is written
# wherever two different notes touch on one row.

ROLL_TOOLS = frozenset(
    {"move", "copy", "remove", "resize", "legato", "half", "double", "stamp"}
)
# chord shapes as scale steps above the stamped row (`gen/chordtrack.TONES`
# spells the chord track's the same way)
STAMPS = {
    "triad": (0, 2, 4),
    "7th": (0, 2, 4, 6),
    "9th": (0, 2, 4, 6, 8),
    "sus2": (0, 1, 4),
    "sus4": (0, 3, 4),
    "power": (0, 4, 7),
}
_NO_SEL = "That selection isn't on this grid any more."


def notes_in(cells, cuts, total: int, rows) -> list[tuple]:
    """The window's notes as (row, start, end), `end` exclusive, by start
    step and then top row first. `rows`: the window's rows, top to bottom."""
    order = {r: i for i, r in enumerate(rows)}
    inside, _outside = _split(cells, total, order)
    cut = {(c[0], int(c[1])) for c in cuts}
    by_row: dict = {}
    for row, step in inside:
        by_row.setdefault(row, set()).add(step)
    out = []
    for row, steps in by_row.items():
        start = prev = None
        for s in [*sorted(steps), None]:
            if start is not None and (s is None or s != prev + 1 or (row, s) in cut):
                out.append((row, start, prev + 1))
                start = None
            if start is None:
                start = s
            prev = s
    return sorted(out, key=lambda n: (n[1], order[n[0]], n[2]))


def _int(raw) -> int:
    raw = str(raw).strip()
    if len(raw) > 6:  # int() refuses a number past 4300 digits
        raise ValueError(raw)
    return int(raw)


def parse_sel(raw: str, total: int, rows) -> tuple[int, int, int, int] | None:
    """`sel=<step0>:<step1>:<pitch>:<pitch>` (inclusive both ways) as
    (step0, step1, row0, row1) indices into the window, clamped to it;
    None when there is none. Raises `ToolRefused` for a rectangle that
    names no row of the window or is not numbers."""
    if not raw:
        return None
    parts = raw.split(":")
    order = {r: i for i, r in enumerate(rows)}
    if len(parts) != 4 or parts[2] not in order or parts[3] not in order:
        raise ToolRefused(_NO_SEL)
    try:
        s0, s1 = sorted((_int(parts[0]), _int(parts[1])))
    except ValueError:
        raise ToolRefused(_NO_SEL) from None
    r0, r1 = sorted((order[parts[2]], order[parts[3]]))
    return max(0, s0), min(total - 1, s1), r0, r1


def _picked(notes, sel, rows) -> list[bool]:
    """Which notes the selection holds: every one a cell of which lies in
    the rectangle (all of them when there is no selection)."""
    if sel is None:
        return [True] * len(notes)
    s0, s1, r0, r1 = sel
    order = {r: i for i, r in enumerate(rows)}
    return [r0 <= order[r] <= r1 and a <= s1 and b > s0 for r, a, b in notes]


def _write(lane: dict, total: int, rows, stay, moved, origin=()) -> dict:
    """The window rewritten from notes: `stay` as they are, then `moved`
    painted over them in start order (a later note takes the steps it
    covers; what sticks out of a covered note is left as a shorter one).
    `moved` is [(row, start, end, from_start | None)]: a note that came
    from another start step brings that step's velocity and slide — or
    their absence — to its new one. `origin`: the start steps the moved
    notes left; one that no note starts on any more gives its velocity
    and slide up. Cells and cuts outside the window are kept."""
    order = {r: i for i, r in enumerate(rows)}
    moved = sorted(moved, key=lambda n: n[1])
    owner: dict[tuple, int] = {}
    for i, (row, a, b, *_from) in enumerate([*stay, *moved]):
        for s in range(max(0, a), min(total, b)):
            owner[(row, s)] = i
    cells_out = _split(lane["cells"], total, order)[1]
    cuts_out = _split(lane.get("cuts") or [], total, order)[1]
    # a cut wherever two different notes touch on a row
    cuts = [
        cell for cell, i in owner.items() if owner.get((cell[0], cell[1] - 1), i) != i
    ]
    was_vel = lane.get("velocities") or {}
    was_nudge = lane.get("note_nudge") or {}  # timing inside a step: as a velocity
    was_slides = {int(s) for s in lane.get("slides") or []}
    vel, nudge, slides = dict(was_vel), dict(was_nudge), set(was_slides)
    starts = {s for (row, s), i in owner.items() if owner.get((row, s - 1)) != i}
    # a step's chance (the working loop's chance lane) travels as its velocity
    was_chance = lane.get("note_chance") or {}
    chance = dict(was_chance)
    for s in set(origin) - starts:
        vel.pop(str(s), None)
        nudge.pop(str(s), None)
        chance.pop(str(s), None)
        slides.discard(s)
    for _row, a, _b, frm in moved:
        if frm is None or not 0 <= a < total:
            continue
        for was, now in ((was_vel, vel), (was_nudge, nudge), (was_chance, chance)):
            if str(frm) in was:
                now[str(a)] = was[str(frm)]
            else:
                now.pop(str(a), None)
        (slides.add if frm in was_slides else slides.discard)(a)
    cells = _join(list(owner), cells_out)
    return {
        **lane,
        "cells": cells,
        "cuts": _join(cuts, cuts_out),
        "velocities": vel,
        "slides": sorted(slides),
        **(
            {"note_nudge": schema.clean_nudge(nudge, cells)}
            if "note_nudge" in lane
            else {}
        ),
        **({"note_chance": chance} if "note_chance" in lane else {}),
    }


def _window(lane: dict, rows, sel):
    total = int(lane["bars"]) * int(lane["steps_per_bar"])
    notes = notes_in(lane["cells"], lane.get("cuts") or [], total, rows)
    pick = _picked(notes, sel, rows)
    chosen = [n for n, p in zip(notes, pick, strict=True) if p]
    rest = [n for n, p in zip(notes, pick, strict=True) if not p]
    return total, notes, chosen, rest


def move_notes(
    lane: dict, rows, sel, dx: int, dy: int, copy: bool = False
) -> tuple[dict, list[tuple]]:
    """The selected notes `dx` steps later and `dy` rows UP (towards the
    window's top), each with its velocity, its slide and its cuts; `copy`
    leaves the originals. The move stops at the window's edges: the
    selection travels as far as all of it can. Returns (lane, the notes
    where they landed — none when nothing could move)."""
    total, notes, chosen, rest = _window(lane, rows, sel)
    if not chosen:
        return dict(lane), []
    order = {r: i for i, r in enumerate(rows)}
    first, last = min(n[1] for n in chosen), max(n[2] for n in chosen)
    dx = max(-first, min(dx, total - last))
    top, low = min(order[n[0]] for n in chosen), max(order[n[0]] for n in chosen)
    dy = max(low - (len(rows) - 1), min(dy, top))
    if not dx and not dy:
        return dict(lane), []
    moved = [(rows[order[r] - dy], a + dx, b + dx, a) for r, a, b in chosen]
    origin = () if copy else {n[1] for n in chosen}
    out = _write(lane, total, rows, notes if copy else rest, moved, origin)
    return out, [n[:3] for n in moved]


def delete_notes(lane: dict, rows, sel) -> tuple[dict, list[tuple]]:
    """The selected notes gone, with the velocity and slide of every start
    step no note starts on any more. Returns (lane, the notes removed)."""
    total, _notes, chosen, rest = _window(lane, rows, sel)
    out = _write(lane, total, rows, rest, [], {n[1] for n in chosen})
    return out, chosen


def relength(lane: dict, rows, sel, end_of) -> tuple[dict, list[tuple]]:
    """Every selected note given a new end, `end_of(note) -> step`: at
    least one step long, never past the window, and never over a note
    that starts later on its row (that note keeps its start). A start
    never moves, so velocities and slides stay where they are. Returns
    (lane, the selected notes as they now are)."""
    total, notes, chosen, _rest = _window(lane, rows, sel)
    by_row: dict = {}
    for row, a, _b in notes:
        by_row.setdefault(row, []).append(a)
    out, after = [], []
    for n in notes:
        row, a, b = n
        if n in chosen:
            b = max(a + 1, min(total, int(end_of(n))))
            b = min([b, *(s for s in by_row[row] if s > a)])
            after.append((row, a, b))
        out.append((row, a, b, None))
    return _write(lane, total, rows, [], out), after


def resize_notes(lane: dict, rows, sel, by: int) -> tuple[dict, list[tuple]]:
    """The selected notes' ends moved `by` steps (negative: shorter)."""
    return relength(lane, rows, sel, lambda n: n[2] + by)


def legato(lane: dict, rows, sel) -> tuple[dict, list[tuple]]:
    """Each selected note runs to where the next one starts — the next
    later start among the selected notes, on any row — and the last to
    the end of the window: no gaps, a line that sings."""
    total, _notes, chosen, _rest = _window(lane, rows, sel)
    starts = sorted({n[1] for n in chosen})
    return relength(
        lane, rows, sel, lambda n: next((s for s in starts if s > n[1]), total)
    )


def scale_lengths(lane: dict, rows, sel, factor: float) -> tuple[dict, list[tuple]]:
    """Halve (0.5) or double (2) the selected notes' lengths."""
    return relength(
        lane, rows, sel, lambda n: n[1] + max(1, int((n[2] - n[1]) * factor))
    )


def stamp(
    lane: dict, rows, tones: list[str], step: int, length: int
) -> tuple[dict, list[tuple]]:
    """Write `tones` (pitch names) as notes of `length` steps starting on
    `step`: a chord in one click. A tone the window has no row for is left
    out. Returns (lane, the notes written)."""
    total, notes, _chosen, _rest = _window(lane, rows, None)
    if not 0 <= step < total:
        raise ToolRefused("That step isn't on this grid.")
    end = min(total, step + max(1, length))
    new = [(t, step, end, None) for t in dict.fromkeys(tones) if t in set(rows)]
    return _write(lane, total, rows, notes, new), [n[:3] for n in new]


def stamp_tones(ladder: list[str], row: str, quality: str) -> list[str]:
    """The in-key chord built on `row`: `STAMPS[quality]` scale steps up
    `ladder` (the key's tones, low to high). A row outside the key takes
    the nearest ladder tone below it."""
    shape = STAMPS.get(quality)
    if shape is None:
        raise ToolRefused("No such chord.")
    at = max((i for i, p in enumerate(ladder) if midi(p) <= midi(row)), default=0)
    return [ladder[at + k] for k in shape if at + k < len(ladder)]


# the pattern's own move tools, on a selection: (steps later, rows up); an
# octave is however many rows of the window one octave is
NUDGES = {
    "shift_left": (-1, 0),
    "shift_right": (1, 0),
    "up": (0, 1),
    "down": (0, -1),
    "oct_up": (0, 8),
    "oct_down": (0, -8),
}


def is_roll(op: str, args) -> bool:
    """Is `op` the roll's (`roll_tool`)? Its own tools always; a shift or
    a transpose when the request carries a selection (it then moves the
    selected notes, not the whole window)."""
    return op in ROLL_TOOLS or (op in NUDGES and bool(args.get("sel")))


def bounds(notes, rows) -> str:
    """The rectangle round `notes`, as a `sel` ("" for none)."""
    if not notes:
        return ""
    order = {r: i for i, r in enumerate(rows)}
    top = min(notes, key=lambda n: order[n[0]])[0]
    low = max(notes, key=lambda n: order[n[0]])[0]
    return f"{min(n[1] for n in notes)}:{max(n[2] for n in notes) - 1}:{top}:{low}"


def roll_tool(
    op: str, lane: dict, *, rows: list[str], ladder: list[str], args
) -> tuple[dict, str, str | None]:
    """A piano-roll edit on a notes lane (`is_roll`), over the window
    (`rows`, top to bottom, x the lane's steps). `args` is the request's
    form: `sel` (a rectangle; none = every note the window shows, except
    for move / copy / remove / resize, which need one), `dx` / `dy`
    (steps later, rows up), `by` (steps a note's end moves), and for a
    stamp `row` / `step` / `len` with `q` (the in-key chord on that row)
    or `tones` (comma-separated pitches). Returns (the new lane, a note
    for the user or "", the selection afterwards — the rectangle round
    the notes it touched, "" for none, None to leave the page's alone).
    Raises `ToolRefused`, KeyError for an unknown op."""
    total = int(lane["bars"]) * int(lane["steps_per_bar"])
    sel = parse_sel(args.get("sel", ""), total, rows)

    def num(name: str, default: int = 0) -> int:
        try:
            return _int(args.get(name) or default)
        except ValueError:
            raise ToolRefused(f"{name} must be a whole number.") from None

    if op in ("move", "copy", "remove", "resize") and sel is None:
        raise ToolRefused("Select some notes first.")
    if op in ("move", "copy") or op in NUDGES:
        dx, dy = NUDGES[op] if op in NUDGES else (num("dx"), num("dy"))
        if abs(dy) == 8:  # an octave, in this window's rows
            low = midi(rows[-1])
            dy = (dy // 8) * sum(low < midi(r) <= low + 12 for r in rows)
        out, hit = move_notes(lane, rows, sel, dx, dy, copy=op == "copy")
        if not hit:  # at an edge: the selection stays where it is
            return out, "", None
    elif op == "remove":
        out, _gone = delete_notes(lane, rows, sel)
        hit = []
    elif op == "resize":
        out, hit = resize_notes(lane, rows, sel, num("by"))
    elif op == "legato":
        out, hit = legato(lane, rows, sel)
    elif op in ("half", "double"):
        out, hit = scale_lengths(lane, rows, sel, 0.5 if op == "half" else 2)
    elif op == "stamp":
        row = args.get("row", "")
        if row not in rows:
            raise ToolRefused("That row isn't on this grid.")
        named = [t for t in args.get("tones", "").split(",") if t]
        tones = named or stamp_tones(ladder, row, args.get("q") or "triad")
        out, new = stamp(lane, rows, tones, num("step"), num("len", 1))
        left = len(dict.fromkeys(tones)) - len(new)
        plural = "s" if left != 1 else ""
        said = f"{left} chord tone{plural} past the grid's rows left out."
        return out, said if left else "", None
    else:
        raise KeyError(op)
    return out, "", bounds(hit, rows) if sel is not None else None


def __getattr__(name: str):
    """`drums_tool` and `DRUM_TOOLS` live in `drumops` (the beat workflow
    owns that file); the old names still resolve here."""
    if name in ("drums_tool", "DRUM_TOOLS"):
        from . import drumops

        return getattr(drumops, name)
    raise AttributeError(name)
