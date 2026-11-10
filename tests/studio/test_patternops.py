"""patternops: the step grids' pattern tools, as pure functions."""

from synthlab.studio import patternops as po


def test_shift_rotates_inside_the_window_and_keeps_hidden_cells():
    cells = [["A1", 0], ["A1", 3], ["C2", 1], ["B9", 2], ["A1", 9]]
    out = po.shift(cells, 4, 1, rows={"A1", "C2"})
    # 3 wraps to 0; B9 (not a shown row) and step 9 (past the grid) stay put
    assert out == [["A1", 0], ["A1", 1], ["A1", 9], ["B9", 2], ["C2", 2]]
    assert po.shift(out, 4, -1, rows={"A1", "C2"}) == sorted(cells)


def test_reverse_mirrors_the_window():
    assert po.reverse([["kick", 0], ["kick", 1], ["snare", 4]], 8) == [
        ["kick", 6],
        ["kick", 7],
        ["snare", 3],
    ]


def test_lanes_follow_the_same_remap():
    lane = {"0": 0.2, "3": 0.8, "9": 0.5}
    assert po.remap_lane(lane, 4, po.shift_fn(4, 1)) == {"1": 0.2, "0": 0.8, "9": 0.5}
    assert po.remap_steps([0, 3, 9], 4, po.reverse_fn(4)) == [0, 3, 9]
    assert po.remap_steps([1], 4, po.reverse_fn(4)) == [2]


def test_duplicate_doubles_the_window_and_replaces_what_was_hidden_there():
    cells = [["A1", 0], ["C2", 2], ["A1", 5]]  # step 5: hidden past a 1x4 grid
    out = po.duplicate(cells, 1, 4)
    assert out == [["A1", 0], ["A1", 4], ["C2", 2], ["C2", 6]]
    assert po.duplicate_lane({"0": 0.3, "5": 0.9}, 4) == {"0": 0.3, "4": 0.3}
    assert po.duplicate_steps([2, 5], 4) == [2, 6]
    assert po.can_duplicate(2, (1, 2, 3, 4)) and not po.can_duplicate(3, (1, 2, 3, 4))


def test_growing_repeats_unless_the_new_bars_hold_hidden_notes():
    cells = [["A1", 0], ["C2", 3]]
    assert po.repeat_into(cells, 4, 12) == [
        ["A1", 0],
        ["A1", 4],
        ["A1", 8],
        ["C2", 3],
        ["C2", 7],
        ["C2", 11],
    ]
    # a shrink hid bar 2's notes: growing back shows them, copies nothing
    hidden = [["A1", 0], ["E2", 5]]
    assert po.repeat_into(hidden, 4, 8) == hidden
    assert po.repeat_lane({"0": 0.4}, 4, 8) == {"0": 0.4, "4": 0.4}
    assert po.repeat_lane({"0": 0.4, "6": 1.0}, 4, 8) == {"0": 0.4, "6": 1.0}
    assert po.repeat_steps([1], 4, 8) == [1, 5]


def test_fill_every_n_replaces_the_row_in_the_window_only():
    cells = [["hat", 1], ["hat", 17], ["kick", 0]]
    out = po.fill(cells, "hat", 16, 4, offset=2)
    assert out == [
        ["hat", 2],
        ["hat", 6],
        ["hat", 10],
        ["hat", 14],
        ["hat", 17],
        ["kick", 0],
    ]


def test_clear_keeps_what_the_grid_cannot_show():
    assert po.clear([["A1", 0], ["B9", 1], ["A1", 7]], 4, rows={"A1"}) == [
        ["A1", 7],
        ["B9", 1],
    ]


def test_transpose_walks_the_ladder_and_reports_notes_that_cannot_move():
    ladder = ["A1", "C2", "D2", "E2", "G2", "A2"]
    cells, stuck = po.transpose([["A1", 0], ["G2", 1], ["F#2", 2]], 4, ladder, 1)
    assert cells == [["A2", 1], ["C2", 0], ["F#2", 2]] and stuck == 1
    cells, stuck = po.transpose([["A2", 0]], 4, ladder, 1)
    assert cells == [["A2", 0]] and stuck == 1  # off the top: stays


def test_velocity_tools():
    h1 = po.humanise({}, [0, 2], 0.2, seed=7, default=0.9)
    assert h1 == po.humanise({}, [0, 2], 0.2, seed=7, default=0.9)  # seeded
    assert set(h1) == {"0", "2"} and all(0.7 <= v <= 1.0 for v in h1.values())
    assert po.ramp({}, [0, 3], 4, 0.2, 1.0) == {"0": 0.2, "3": 1.0}
    assert po.reset_lane({"0": 0.1, "9": 0.3}, 4) == {"9": 0.3}
    assert po.accent({}, [0, 1, 4], 4, 1.0, 0.6) == {"0": 1.0, "1": 0.6, "4": 1.0}


# --- reverse on the note grid: velocities and slides follow their notes -----


def _reversed(cells, velocities, slides, cuts=(), **kw):
    lane = {
        "bars": 1,
        "steps_per_bar": 16,
        "cells": [list(c) for c in cells],
        "cuts": [list(c) for c in cuts],
        "velocities": velocities,
        "slides": slides,
    }
    out, _note = po.notes_tool(
        "reverse",
        lane,
        rows={"A1", "C2"},
        ladder=["A1", "C2"],
        octave=5,
        choices=(1, 2, 4),
        **kw,
    )
    return out


def test_reverse_moves_a_held_notes_velocity_and_slide_to_its_new_start():
    """A velocity and a slide are keyed by their note's START step. Mirrored
    step by step they landed on the mirrored note's END (15 here), where
    nothing reads them: the note came back at the default, unslid."""
    out = _reversed([("A1", s) for s in range(4)], {"0": 0.4}, [0])
    assert sorted(c[1] for c in out["cells"]) == [12, 13, 14, 15]
    assert out["velocities"] == {"12": 0.4} and out["slides"] == [12]


def test_reverse_remaps_per_note_across_rows_and_cuts():
    cells = [("A1", 0), ("A1", 1), ("A1", 2), ("C2", 4), ("C2", 5), ("A1", 9)]
    # A1: [0][1 2] (a cut at 1), then a single at 9; C2: [4 5]
    out = _reversed(
        cells,
        {"0": 0.2, "1": 0.3, "4": 0.5, "9": 0.9, "30": 0.7},
        [1, 4, 30],
        cuts=[("A1", 1)],
    )
    # [0] -> 15, [1 2] -> [13 14], [4 5] -> [10 11], [9] -> 6; step 30 is
    # past the grid and stays
    assert out["velocities"] == {"15": 0.2, "13": 0.3, "10": 0.5, "6": 0.9, "30": 0.7}
    assert out["slides"] == [10, 13, 30]
    assert out["cuts"] == [["A1", 15]]


def test_reverse_keeps_a_notes_default_velocity():
    """A value painted on a step that starts no note is mirrored in place,
    but never onto a note that had none: that note keeps the default."""
    out = _reversed([("A1", 0), ("A1", 1), ("C2", 8)], {"14": 0.3, "5": 0.6}, [14])
    # A1 [0 1] -> [14 15] (no velocity of its own); 14 -> 1; 5 -> 10
    assert out["velocities"] == {"1": 0.3, "10": 0.6} and out["slides"] == [1]


def test_reverse_under_an_arp_mirrors_the_lane_step_by_step():
    """An arp reads every step's velocity, so there the lane mirrors as the
    steps do; the drum grid (no held notes) always did."""
    out = _reversed([("A1", s) for s in range(4)], {"0": 0.4}, [], arp_on=True)
    assert out["velocities"] == {"15": 0.4}
    drums = {"bars": 1, "steps": 16, "drums": [["kick", 0]], "drum_vel": {"0": 0.4}}
    assert po.drums_tool("reverse", drums, choices=(1, 2, 4))["drum_vel"] == {"15": 0.4}
