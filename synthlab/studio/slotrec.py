"""A take into Perform's launcher (studio `docs/loop-and-perform.md`,
"Rec into a slot"): ⏺ Rec, ⟲ Capture and the
keys arp's print land in the SELECTED slot (slots.js keeps the selection
in the page, per tab; keyrec.js posts it as `lane`, `row`, `how`, `bar`).

- **new** (an empty slot) and **replace** (a filled one): the take's own
  loop — made as every take's is (`routes/takes.py` `_loop_from_take`) —
  goes in the slot and launches there (`StudioState.launch`, its `setup`
  filling the slot inside the launch's one undo step): the take plays from
  the slot at the next quantise point. Replace leaves the slot's old loop
  in the Loops.
- **overdub** (a filled one): the slot's loop launches first when it is
  not what its lane plays (its own undo step), then the take's notes fold
  onto that loop's own bars from the bar the take began on (`bar`, of the
  loop that played: `fold`) and merge into the lane by `record.merge`'s
  rules — a note on a (pitch, step) the lane holds rewrites its velocity,
  a repeated note is a cut — one whole-loop undo step
  (`StudioState.overdub_lane`); the loop open on the lane (`loop_edit`)
  saves it, as any edit there. The take is still kept as its own loop
  (and its audio, for ⏺ Rec).

Notes only: a beat's slot (the drums' lane) never takes one — keyrec.js
posts a free take there, and `target` refuses the lane. Pure but for
`land`, which moves the session; the routes answer.
"""

from __future__ import annotations

import math

from ..sequencer import name_of
from . import limits, looplib, loops, record
from . import meter as _meter
from .state import StudioState
from .words import plural

HOWS = ("new", "overdub", "replace")


class Refused(Exception):
    """The take stays in the Loops, but not in the slot: why."""


def target(s: StudioState, form) -> tuple[int, int, str, int] | None:
    """The slot a take's post names — (lane, row, how, bar) — or None: no
    slot asked, a lane of the drums, or a value out of range."""
    try:
        lane, row = int(form.get("lane", "")), int(form.get("row", ""))
        bar = int(form.get("bar", "") or 0)
    except ValueError:
        return None
    if not (0 <= lane < s.DRUM_LANE and 0 <= row < limits.MAX_SLOT_ROWS):
        return None
    how = form.get("how", "")
    return lane, row, how if how in HOWS else "new", max(0, min(bar, 255))


def free_row(s: StudioState, lane: int, row: int) -> int | None:
    """The lane's next empty slot after `row` (round to its first scene
    again), or None when every scene of the lane holds a loop: where ⟲
    Capture lands when the selected slot is filled (deliberately: a
    capture never overwrites)."""
    n = limits.MAX_SLOT_ROWS
    for k in range(1, n):
        r = (row + k) % n
        if not s.slot_at(lane, r):
            return r
    return None


def capture_target(s: StudioState, form) -> tuple[tuple | None, str]:
    """Where a capture (⟲ Capture, the arp's print) lands: (the slot, as
    `target` gives it, or None; what the toast adds). The selected slot
    when it is empty; else the lane's next empty one, as a new loop (never
    an overdub or a replace: nothing played is lost, nothing kept is
    overwritten); none empty: no slot, the loop only in the Loops."""
    at = target(s, form)
    if at is None or not s.slot_at(at[0], at[1]):
        return (at[:2] + ("new", at[3]) if at else None), ""
    lane, row, _how, bar = at
    free = free_row(s, lane, row)
    if free is None:
        return None, (
            f"Lane {lane + 1} has no empty slot: it is only in the Loops panel "
            "(nothing was replaced)."
        )
    return (lane, free, "new", bar), (
        f"The selected slot holds a loop: this went into scene {free + 1}."
    )


def _round(x: float) -> int:
    return math.floor(x + 0.5)  # half up, as record.py rounds


def fold(notes, bar: int, meter: str, bars: int, spb: int) -> tuple[list, list]:
    """A take's notes ([start, length, midi, velocity], beats from its
    first downbeat) on a lane of `bars` x `spb` in `meter`, the take begun
    on bar `bar` of the loop: each note's attack on its nearest step,
    folded onto the lane (a take longer than the loop lands each pass where
    it sounds) — `[(pitch, step, velocity)]`, a later one on the same
    (pitch, step) winning — and the steps it holds after its attack
    `[(pitch, step)]` (at most the lane's length; never another attack's)."""
    step = _meter.bar_beats(meter) / spb
    total = max(1, bars * spb)
    off = (bar % max(1, bars)) * _meter.bar_beats(meter)
    attacks: dict[tuple[str, int], float] = {}
    held: set[tuple[str, int]] = set()
    for start, length, midi, vel in notes:
        at = _round((start + off) / step) % total
        pitch = name_of(int(midi))
        attacks.pop((pitch, at), None)
        attacks[(pitch, at)] = float(vel)
        for k in range(1, min(total, max(1, _round(length / step)))):
            held.add((pitch, (at + k) % total))
    tails = sorted(held - set(attacks))
    return [(p, st, v) for (p, st), v in attacks.items()], tails


def overdub(lane: dict, attacks, tails) -> dict:
    """`lane` (a note lane's fields) with a take merged in: the held steps
    first (a note's length; never a cut), then the attacks by
    `record.merge` (velocity rewritten on a step they share, a repeat on
    its own pitch a cut)."""
    pat = {
        "cells": [list(c) for c in lane["cells"]],
        "cuts": [list(c) for c in lane.get("cuts") or []],
        "velocities": dict(lane.get("velocities") or {}),
    }
    have = {(c[0], int(c[1])) for c in pat["cells"]}
    for pitch, st in tails:
        if (pitch, st) not in have:
            pat["cells"].append([pitch, st])
            have.add((pitch, st))
    record.merge(pat, attacks)
    return {**lane, **pat}


def _title(s: StudioState, name: str) -> str:
    return looplib.titles(s.data_dir).get(name, name)


def _word(s: StudioState, lane: int, row: int) -> str:
    return f"lane {lane + 1}, scene {row + 1}"


def land(s: StudioState, made: str, at: tuple, notes) -> tuple[str, bool]:
    """The loop `made` (a take's) into slot `at` = (lane, row, how, bar):
    what it did, in words, and whether the preview waits for the quantise
    point (a launch) or swaps now (an overdub of the slot playing).
    Refused (nothing moved) when the launch is."""
    lane, row, how, bar = at
    have = s.slot_at(lane, row)
    held = loops.load(s.data_dir, have) if have else None
    if held is None or held["kind"] != "notes" or how != "overdub":
        lp = loops.load(s.data_dir, made)
        if lp is None:
            raise Refused("its loop is gone.")
        lp["title"] = _title(s, made)

        def setup() -> None:
            s.set_slot(lane, row, made, undo=False)

        refused = s.launch({lane: lp}, {lane: row}, setup=setup)
        if refused:
            raise Refused(refused)
        verb = "Replaced" if held is not None else "Recorded"
        said = f"{verb} {_word(s, lane, row)} with “{lp['title']}”"
        return f"{said}: it plays from the slot. Ctrl+Z takes it back.", True
    queue = False
    if s.slot_playing(lane) != row or s.lane_stopped(lane):
        held["title"] = _title(s, held["name"])
        refused = s.launch({lane: held}, {lane: row})
        if refused:
            raise Refused(refused)
        queue = True
    n = s.overdub_lane(lane, notes, bar)
    if not n:
        raise Refused("nothing was played.")
    onto, take = _title(s, held["name"]), _title(s, made)
    said = f"Overdubbed {plural(n, 'note')} onto “{onto}” ({_word(s, lane, row)})"
    said += f"; the take is kept too, as “{take}”. Ctrl+Z takes it back."
    return said, queue
