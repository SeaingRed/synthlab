"""The piano roll's edits are run ops over the step grid
(`studio/patternops.py`, "the piano roll: notes as runs").

A note is a run of one row's adjacent cells, split at the row's cuts; its
velocity and its slide sit on its start step. Every roll gesture — a note
dragged, its end pulled, a selection copied, a chord stamped — is one of
these ops, so it writes nothing but cells, cuts, the velocity lane and the
slide list, and the engine, the MIDI export and the undo ring read what
they always read.
"""

from __future__ import annotations

import pytest

from synthlab.studio import patternops as po
from synthlab.studio.arrange import _merge_runs, _row_breaks

ROWS = ["E2", "D2", "C2", "B1", "A1"]  # top to bottom, as the grid draws them
LADDER = ["A1", "B1", "C2", "D2", "E2", "F2", "G2", "A2", "B2", "C3"]


def lane(cells, cuts=(), vel=None, slides=(), bars=1, spb=16) -> dict:
    return {
        "bars": bars,
        "steps_per_bar": spb,
        "cells": [list(c) for c in cells],
        "cuts": [list(c) for c in cuts],
        "velocities": dict(vel or {}),
        "slides": list(slides),
    }


def run(row: str, a: int, b: int) -> list[list]:
    return [[row, s] for s in range(a, b)]


def notes(ln: dict) -> list[tuple]:
    """What the ENGINE plays: `_merge_runs` over each row with its cuts."""
    out = []
    for row in {c[0] for c in ln["cells"]}:
        steps = [c[1] for c in ln["cells"] if c[0] == row]
        breaks = _row_breaks((), ln["cuts"], row)
        out += [(row, a, a + n) for a, n in _merge_runs(steps, breaks)]
    return sorted(out, key=lambda n: (n[1], n[0]))


def sel(s0, s1, top, low) -> tuple:
    return (s0, s1, ROWS.index(top), ROWS.index(low))


# --- reading ------------------------------------------------------------------


def test_the_roll_reads_the_notes_the_engine_plays():
    ln = lane(run("A1", 0, 4) + run("A1", 4, 6) + run("C2", 2, 3), cuts=[["A1", 4]])
    assert po.notes_in(ln["cells"], ln["cuts"], 16, ROWS) == [
        ("A1", 0, 4),
        ("C2", 2, 3),
        ("A1", 4, 6),
    ]
    assert sorted(po.notes_in(ln["cells"], ln["cuts"], 16, ROWS)) == sorted(notes(ln))


def test_a_selection_holds_every_note_it_touches():
    ln = lane(run("A1", 0, 4) + run("C2", 6, 8))
    all_ = po.notes_in(ln["cells"], ln["cuts"], 16, ROWS)
    assert po._picked(all_, sel(3, 3, "A1", "A1"), ROWS) == [True, False]
    assert po._picked(all_, sel(4, 5, "E2", "A1"), ROWS) == [False, False]
    assert po._picked(all_, None, ROWS) == [True, True]


@pytest.mark.parametrize(
    "raw", ["1:2:Z9:A1", "x:2:A1:A1", "1:2:A1", "9" * 40 + ":1:A1:A1"]
)
def test_a_hostile_selection_is_refused_not_an_error(raw):
    with pytest.raises(po.ToolRefused):
        po.parse_sel(raw, 16, ROWS)


def test_a_selection_is_clamped_to_the_grid_and_takes_either_order():
    assert po.parse_sel("12:-3:A1:E2", 8, ROWS) == (0, 7, 0, 4)
    assert po.parse_sel("", 8, ROWS) is None


# --- move / copy ----------------------------------------------------------------


def test_a_moved_note_carries_its_velocity_and_its_slide():
    ln = lane(run("A1", 2, 5), vel={"2": 0.4}, slides=[2])
    out, n = po.move_notes(ln, ROWS, sel(2, 2, "A1", "A1"), 4, 2)
    assert len(n) == 1 and notes(out) == [("C2", 6, 9)]
    assert out["velocities"] == {"6": 0.4} and out["slides"] == [6]


def test_a_note_at_the_default_velocity_does_not_take_a_stale_one():
    ln = lane(run("A1", 0, 2), vel={"8": 0.3})  # 0.3 is parked on an empty step
    out, _n = po.move_notes(ln, ROWS, sel(0, 0, "A1", "A1"), 8, 0)
    assert notes(out) == [("A1", 8, 10)] and out["velocities"] == {}


def test_a_step_another_note_still_starts_on_keeps_its_velocity():
    # a chord on step 0: one of its notes moves away, the other stays
    ln = lane(run("A1", 0, 2) + run("C2", 0, 2), vel={"0": 0.5})
    out, _n = po.move_notes(ln, ROWS, sel(0, 0, "C2", "C2"), 4, 0)
    assert out["velocities"] == {"0": 0.5, "4": 0.5}


def test_a_note_moved_against_another_stays_its_own_note():
    ln = lane(run("A1", 0, 2) + run("A1", 6, 8))
    out, _n = po.move_notes(ln, ROWS, sel(6, 6, "A1", "A1"), -4, 0)
    assert notes(out) == [("A1", 0, 2), ("A1", 2, 4)]
    assert out["cuts"] == [["A1", 2]]  # side by side: a cut, not one long note


def test_a_cut_that_no_longer_splits_anything_goes():
    ln = lane(run("A1", 0, 4), cuts=[["A1", 2]])  # two notes, 0-2 and 2-4
    out, _n = po.move_notes(ln, ROWS, sel(2, 3, "A1", "A1"), 4, 0)
    assert notes(out) == [("A1", 0, 2), ("A1", 6, 8)] and out["cuts"] == []


def test_a_note_dropped_on_another_takes_the_steps_it_covers():
    ln = lane(run("A1", 0, 2) + run("C2", 1, 5))
    out, _n = po.move_notes(ln, ROWS, sel(0, 0, "A1", "A1"), 2, 2)
    # the C2 note keeps its first step, the mover lands on 2-4, C2's last
    # step is left as a note of its own
    assert notes(out) == [("C2", 1, 2), ("C2", 2, 4), ("C2", 4, 5)]


def test_a_selection_moves_as_far_as_all_of_it_can():
    ln = lane(run("E2", 0, 2) + run("A1", 13, 16))
    out, n = po.move_notes(ln, ROWS, None, 5, 3)  # the right edge and the top row
    assert len(n) == 0 and notes(out) == notes(ln)
    out, n = po.move_notes(ln, ROWS, None, -5, -3)  # the left edge, the bottom row
    assert len(n) == 0 and notes(out) == notes(ln)
    out, n = po.move_notes(ln, ROWS, sel(0, 0, "E2", "E2"), 99, -99)
    # clamped to the last steps and the bottom row, over the note there
    assert len(n) == 1 and notes(out) == [("A1", 13, 14), ("A1", 14, 16)]


def test_a_copy_leaves_the_original_and_its_velocity():
    ln = lane(run("A1", 0, 2), vel={"0": 0.35}, slides=[0])
    out, n = po.move_notes(ln, ROWS, sel(0, 1, "A1", "A1"), 8, 1, copy=True)
    assert len(n) == 1 and notes(out) == [("A1", 0, 2), ("B1", 8, 10)]
    assert out["velocities"] == {"0": 0.35, "8": 0.35} and out["slides"] == [0, 8]


def test_a_move_keeps_what_the_window_hides():
    hidden = [["F#4", 3], ["A1", 40]]  # another key's row; a step past the grid
    ln = lane(run("A1", 0, 2) + hidden, cuts=[["F#4", 3]], vel={"40": 0.2})
    out, _n = po.move_notes(ln, ROWS, None, 2, 0)
    assert ["F#4", 3] in out["cells"] and ["A1", 40] in out["cells"]
    assert ["F#4", 3] in out["cuts"] and out["velocities"]["40"] == 0.2


# --- delete / length --------------------------------------------------------------


def test_delete_takes_the_whole_note_and_its_lane_values():
    ln = lane(run("A1", 0, 4) + run("C2", 8, 9), vel={"0": 0.4, "8": 0.6}, slides=[0])
    out, n = po.delete_notes(ln, ROWS, sel(3, 3, "A1", "A1"))
    assert len(n) == 1 and notes(out) == [("C2", 8, 9)]
    assert out["velocities"] == {"8": 0.6} and out["slides"] == []


def test_an_end_dragged_longer_stops_at_the_next_note_on_its_row():
    ln = lane(run("A1", 0, 2) + run("A1", 6, 8) + run("C2", 3, 4))
    out, n = po.resize_notes(ln, ROWS, sel(0, 0, "A1", "A1"), 9)
    assert len(n) == 1 and notes(out) == [("A1", 0, 6), ("C2", 3, 4), ("A1", 6, 8)]
    assert out["cuts"] == [["A1", 6]]


def test_an_end_dragged_shorter_keeps_one_step_and_its_start():
    ln = lane(run("A1", 4, 8), vel={"4": 0.5})
    out, _n = po.resize_notes(ln, ROWS, sel(4, 4, "A1", "A1"), -99)
    assert notes(out) == [("A1", 4, 5)] and out["velocities"] == {"4": 0.5}


def test_a_note_grows_to_the_end_of_the_grid_and_no_further():
    out, _n = po.resize_notes(
        lane(run("A1", 12, 13)), ROWS, sel(12, 12, "A1", "A1"), 99
    )
    assert notes(out) == [("A1", 12, 16)]


def test_legato_runs_each_note_to_the_next_and_the_last_to_the_end():
    ln = lane(run("A1", 0, 1) + run("C2", 4, 5) + run("E2", 4, 6) + run("A1", 10, 11))
    out, n = po.legato(ln, ROWS, None)
    assert len(n) == 4
    assert notes(out) == [("A1", 0, 4), ("C2", 4, 10), ("E2", 4, 10), ("A1", 10, 16)]


def test_legato_on_a_selection_reads_only_the_selected_notes():
    ln = lane(run("A1", 0, 1) + run("C2", 2, 3) + run("A1", 8, 9))
    out, _n = po.legato(ln, ROWS, sel(0, 15, "A1", "A1"))
    assert notes(out) == [("A1", 0, 8), ("C2", 2, 3), ("A1", 8, 16)]


def test_half_and_double_change_lengths_never_starts():
    ln = lane(run("A1", 0, 4) + run("C2", 4, 5) + run("A1", 8, 14))
    half, _n = po.scale_lengths(ln, ROWS, None, 0.5)
    assert notes(half) == [("A1", 0, 2), ("C2", 4, 5), ("A1", 8, 11)]
    double, _n = po.scale_lengths(ln, ROWS, None, 2)
    # the first A1 stops at the second's start; the second at the grid's end
    assert notes(double) == [("A1", 0, 8), ("C2", 4, 6), ("A1", 8, 16)]


# --- stamps -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("q", "want"),
    [
        ("triad", ["A1", "C2", "E2"]),
        ("7th", ["A1", "C2", "E2", "G2"]),
        ("sus2", ["A1", "B1", "E2"]),
        ("sus4", ["A1", "D2", "E2"]),
        ("power", ["A1", "E2", "A2"]),
    ],
)
def test_a_stamp_is_the_chord_in_the_key_on_the_clicked_row(q, want):
    assert po.stamp_tones(LADDER, "A1", q) == want


def test_a_stamp_on_a_row_outside_the_key_starts_from_the_tone_below():
    assert po.stamp_tones(LADDER, "A#1", "triad") == ["A1", "C2", "E2"]


def test_a_stamp_writes_simultaneous_notes_and_keeps_the_ones_beside_it():
    ln = lane(run("C2", 0, 4))
    out, n = po.stamp(ln, ROWS, ["A1", "C2", "E2"], 4, 4)
    assert len(n) == 3
    assert notes(out) == [("C2", 0, 4), ("A1", 4, 8), ("C2", 4, 8), ("E2", 4, 8)]
    assert out["cuts"] == [["C2", 4]]


def test_a_stamp_leaves_out_the_tones_the_window_has_no_row_for():
    ln, said, _sel = po.roll_tool(
        "stamp",
        lane([]),
        rows=ROWS,
        ladder=LADDER,
        args={"row": "D2", "step": "0", "len": "2", "q": "triad"},
    )
    assert notes(ln) == [("D2", 0, 2)] and said.startswith("2 chord tones")


# --- the one entry point --------------------------------------------------------------


def test_every_roll_tool_is_reachable_and_an_unknown_one_is_a_key_error():
    ln = lane(run("A1", 0, 2))
    for op in sorted(po.ROLL_TOOLS - {"stamp"}):
        args = {"sel": "0:1:A1:A1", "dx": "2", "dy": "1", "by": "1"}
        out, _said, _sel = po.roll_tool(op, ln, rows=ROWS, ladder=LADDER, args=args)
        assert set(out) == set(ln)
    with pytest.raises(KeyError):
        po.roll_tool("explode", ln, rows=ROWS, ladder=LADDER, args={})


@pytest.mark.parametrize("op", ["move", "copy", "remove", "resize"])
def test_a_selection_tool_without_a_selection_is_refused(op):
    with pytest.raises(po.ToolRefused):
        po.roll_tool(op, lane(run("A1", 0, 2)), rows=ROWS, ladder=LADDER, args={})


@pytest.mark.parametrize("field", ["dx", "dy", "by"])
def test_a_number_that_is_not_one_is_refused(field):
    args = {"sel": "0:1:A1:A1", field: "1e9999" if field == "dx" else "x"}
    op = "resize" if field == "by" else "move"
    with pytest.raises(po.ToolRefused):
        po.roll_tool(op, lane(run("A1", 0, 2)), rows=ROWS, ladder=LADDER, args=args)


def test_no_roll_tool_leaves_a_cut_without_a_cell():
    ln = lane(run("A1", 0, 6) + run("C2", 2, 9), cuts=[["A1", 3], ["C2", 5]])
    for op, args in (
        ("move", {"sel": "0:15:C2:A1", "dx": "3", "dy": "1"}),
        ("remove", {"sel": "3:3:A1:A1"}),
        ("half", {}),
        ("legato", {}),
    ):
        out, _said, _sel = po.roll_tool(op, ln, rows=ROWS, ladder=LADDER, args=args)
        on = {(c[0], c[1]) for c in out["cells"]}
        assert all((c[0], c[1]) in on for c in out["cuts"]), op


# --- the selection afterwards, and the pattern's own tools on one -------------------


def test_a_move_answers_with_the_rectangle_round_where_the_notes_landed():
    ln = lane(run("A1", 0, 2) + run("C2", 1, 4) + run("E2", 9, 10))
    args = {"sel": "0:3:C2:A1", "dx": "4", "dy": "1"}
    out, _said, now = po.roll_tool("move", ln, rows=ROWS, ladder=LADDER, args=args)
    assert notes(out) == [("B1", 4, 6), ("D2", 5, 8), ("E2", 9, 10)]
    assert now == "4:7:D2:B1"
    # the same rectangle selects the same notes again: a second move follows
    out, _said, now = po.roll_tool(
        "move", out, rows=ROWS, ladder=LADDER, args={"sel": now, "dx": "-1"}
    )
    assert notes(out) == [("B1", 3, 5), ("D2", 4, 7), ("E2", 9, 10)]


def test_a_move_that_cannot_move_leaves_the_selection_alone():
    ln = lane(run("A1", 0, 2))
    args = {"sel": "0:1:A1:A1", "dx": "-1"}
    out, _said, now = po.roll_tool("move", ln, rows=ROWS, ladder=LADDER, args=args)
    assert notes(out) == notes(ln) and now is None


def test_remove_empties_the_selection_and_a_tool_on_the_window_leaves_it():
    ln = lane(run("A1", 0, 2))
    args = {"sel": "0:1:A1:A1"}
    assert po.roll_tool("remove", ln, rows=ROWS, ladder=LADDER, args=args)[2] == ""
    assert po.roll_tool("legato", ln, rows=ROWS, ladder=LADDER, args={})[2] is None


@pytest.mark.parametrize(
    ("op", "want"),
    [
        ("shift_right", ("C2", 5, 7)),
        ("shift_left", ("C2", 3, 5)),
        ("up", ("D2", 4, 6)),
        ("down", ("B1", 4, 6)),
    ],
)
def test_the_patterns_move_tools_move_a_selection_not_the_window(op, want):
    ln = lane(run("C2", 4, 6) + run("A1", 0, 1))
    args = {"sel": "4:5:C2:C2"}
    assert po.is_roll(op, args) and not po.is_roll(op, {})
    out, _said, _now = po.roll_tool(op, ln, rows=ROWS, ladder=LADDER, args=args)
    assert notes(out) == sorted([("A1", 0, 1), want], key=lambda n: (n[1], n[0]))


def test_an_octave_is_as_many_rows_as_the_window_has_in_one():
    chromatic = [
        f"{n}{o}" for o in (3, 2) for n in "B A# A G# G F# F E D# D C# C".split()
    ]
    ln = lane(run("C2", 0, 2))
    args = {"sel": "0:1:C2:C2"}
    out, _said, _now = po.roll_tool("oct_up", ln, rows=chromatic, ladder=[], args=args)
    assert notes(out) == [("C3", 0, 2)]
    key = ["A2", "G2", "E2", "D2", "C2", "A1"]  # a pentatonic window, an octave
    out, _said, _now = po.roll_tool(
        "oct_up", lane(run("A1", 0, 2)), rows=key, ladder=[], args={"sel": "0:1:A1:A1"}
    )
    assert notes(out) == [("A2", 0, 2)]
