"""A note's own feel (`Arrangement.note_feel`, `studio/notefeel.py`): where
it starts and ends between the lines and how hard it was struck, per note.

What is held here: absent means absent (a lane without it schedules what it
always did); present, THE note schedule (`arrange._lane_timeline`) plays the
note where it was played — no swing, no step nudge, no gate — at its share of
its step's velocity, and so do the MIDI file and a section's chunks (a note
felt early onto a chunk's first step plays in the chunk before); every
scheduler path keeps working with it (arp ignores it, strum, chance, a
following layer, a mono line); every grid tool carries it with its note
(moved keeps it, erased loses it, drawn fresh has none); hostile input is
conformed.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from synthlab.sequencer import Song
from synthlab.studio import notefeel, patternops, schema, songops
from synthlab.studio import song_model as sm
from synthlab.studio.arrange import NOTE_GATE, build_song, note_schedule
from synthlab.studio.render import RenderInputs
from synthlab.studio.state.model import Arrangement
from synthlab.synth import Patch, Synth

SB = 0.25  # a sixteenth at 4/4, in beats


def _arr(**kw) -> Arrangement:
    base = dict(
        bpm=120.0,
        bars=1,
        steps_per_bar=16,
        cells=[["A2", 0], ["A2", 1], ["A2", 2], ["C3", 0], ["E3", 6]],
        velocities={"0": 0.8, "6": 0.5},
        note_feel={
            "A2.0": [0.1, -0.6, 1.0],
            "C3.0": [0.18, -0.3, 0.5],
            "E3.6": [-0.25, -0.4, 1.0],
        },
    )
    return Arrangement(**(base | kw))


def _render(song: Song) -> np.ndarray:
    return song.render(normalize=0)


def _same(a: np.ndarray, b: np.ndarray) -> float:
    n = max(len(a), len(b))
    a = np.pad(a, ((0, n - len(a)), (0, 0)))
    b = np.pad(b, ((0, n - len(b)), (0, 0)))
    return float(np.abs(a - b).max())


def _played(events, patch=None, bpm=120.0) -> np.ndarray:
    """The notes as played, straight through the engine: each at its own
    start, held its own length (gate 1), at its own velocity."""
    patch = patch or Patch()
    song = Song(bpm=bpm)
    for start, length, pitch, vel in events:
        song.notes(
            "x", Synth(patch), [(pitch, length, vel)], start_beat=start, gate=1.0
        )
    return _render(song)


# --- the schedule -------------------------------------------------------------


def test_a_felt_note_plays_where_it_was_played():
    arr = _arr()
    events = [
        ((0 + 0.1) * SB, (3 - 0.6 - 0.1) * SB, "A2", 0.8),
        ((0 + 0.18) * SB, (1 - 0.3 - 0.18) * SB, "C3", 0.4),
        ((6 - 0.25) * SB, (7 - 0.4 - 5.75) * SB, "E3", 0.5),
    ]
    assert _same(_render(build_song(Patch(), arr)), _played(events)) < 1e-12
    # the MIDI file reads the same schedule: each note's own length
    (main,) = note_schedule(arr)
    got = sorted(
        (round(s, 9), round(d * NOTE_GATE, 9), p, round(v, 9)) for s, d, p, v, _ in main
    )
    want = sorted((round(s, 9), round(ln, 9), p, round(v, 9)) for s, ln, p, v in events)
    assert got == want


def test_without_feel_nothing_moves():
    arr = _arr(note_feel={})
    plain = Arrangement(**{k: v for k, v in arr.__dict__.items() if k != "note_feel"})
    a, b = _render(build_song(Patch(), arr)), _render(build_song(Patch(), plain))
    np.testing.assert_array_equal(a, b)
    key = RenderInputs(patch=Patch(), arr=arr).key()
    assert key == RenderInputs(patch=Patch(), arr=plain).key()
    assert "note_feel" not in schema.arr_dict(arr)
    # a note without an entry beside one with: as it always plays (gated)
    part = _arr(note_feel={"A2.0": [0.1, -0.6, 1.0]})
    (main,) = note_schedule(part)
    c3 = next(e for e in main if e[2] == "C3")
    assert c3[:2] == (0.0, SB) and c3[3] == 0.8


def test_feel_is_in_the_render_key_only_when_set():
    with_ = RenderInputs(patch=Patch(), arr=_arr()).key()
    without = RenderInputs(patch=Patch(), arr=_arr(note_feel={})).key()
    assert with_ != without


def test_swing_and_the_step_nudge_do_not_move_a_felt_note():
    arr = _arr(swing=0.4, note_nudge={"0": 0.3})
    (main,) = note_schedule(arr)
    e3 = next(e for e in main if e[2] == "E3")
    assert e3[0] == pytest.approx(5.75 * SB)
    a2 = next(e for e in main if e[2] == "A2")
    assert a2[0] == pytest.approx(0.1 * SB)


def test_an_arp_ignores_feel_and_a_strum_and_chance_still_play():
    arp = _arr(arp_mode="up")
    assert note_schedule(arp) == note_schedule(replace(arp, note_feel={}))
    # strum: two felt notes do not start together, so nothing is strummed
    strummed = _arr(strum=30.0)
    assert note_schedule(strummed) == note_schedule(_arr())
    # chance rests a felt note whole; the others keep their feel
    rested = _arr(note_chance={"6": 0.0})
    (main,) = note_schedule(rested)
    assert sorted(e[2] for e in main) == ["A2", "C3"]


def test_a_following_layer_plays_the_main_lanes_feel_at_its_octave():
    arr = _arr()
    lay = {"patch": Patch().to_json(), "cells": [], "follow": {"octave": -1}}
    main, follow = note_schedule(arr, [lay])
    assert [(s, d, v) for s, d, _p, v, _ in sorted(main)] == [
        (s, d, v) for s, d, _p, v, _ in sorted(follow)
    ]
    assert sorted(e[2] for e in follow) == ["A1", "C2", "E2"]


def test_a_mono_patch_plays_felt_notes_as_lines():
    mono = Patch().tweak(voice_mode="mono")
    out = _render(build_song(mono, _arr()))
    assert np.isfinite(out).all() and np.abs(out).max() > 0


def test_a_layer_with_its_own_notes_plays_its_own_feel():
    arr = _arr(cells=[], note_feel={})
    lay = {
        "patch": Patch().to_json(),
        "cells": [["E3", 6]],
        "note_feel": {"E3.6": [-0.25, -0.4, 1.0]},
    }
    _main, own = note_schedule(arr, [lay])
    assert own[0][0] == pytest.approx(5.75 * SB)


# --- a section's chunks -------------------------------------------------------


def _parts(arr: Arrangement) -> sm.SectionParts:
    return sm.SectionParts(
        arr=arr,
        main_patch=Patch().to_json(),
        curves={},
        main_fx=(),
        main_muted=False,
        layers=(),
        drums_fx=(),
        cycle_bars=arr.bars,
    )


def _abs_schedule(parts: sm.SectionParts, windows) -> list:
    out = []
    for b0, b1 in windows:
        w = parts.window(b0, b1)
        for e in note_schedule(w.arr, breaks=w.breaks)[0]:
            out.append((round(e[0] + b0 * 4.0, 9), round(e[1], 9), e[2], e[3]))
    return sorted(out)


def test_a_note_felt_early_onto_a_chunks_first_step_plays_in_the_chunk_before():
    # bar 2's downbeat played early, bar 1's downbeat early too (the circle:
    # it sounds at the cycle's end), a note held over the bar line
    arr = Arrangement(
        bpm=120.0,
        bars=4,
        steps_per_bar=16,
        cells=[["A2", 0], ["C3", 16], ["C3", 17], ["E3", 14], ["E3", 15], ["E3", 16]],
        velocities={"0": 0.7, "16": 0.9, "14": 0.6},
        note_feel={
            "A2.0": [-0.2, -0.5, 1.0],
            "C3.16": [-0.3, 0.2, 1.0],
            "E3.14": [0.1, -0.1, 1.0],
        },
    )
    parts = _parts(arr)
    whole = _abs_schedule(parts, [(0, 4)])
    chunks = _abs_schedule(parts, [(0, 1), (1, 2), (2, 3), (3, 4)])
    assert chunks == whole
    # and the audio, chunk by chunk added at its offset, is the whole render
    n = int(round(16 * 0.5 * 44100))
    full = _render(build_song(Patch(), parts.window(0, 4).arr))
    stitched = np.zeros((len(full) + n, 2))
    for b in range(4):
        w = parts.window(b, b + 1)
        r = _render(build_song(Patch(), w.arr, breaks=w.breaks))
        off = int(round(b * 2.0 * 44100))
        stitched[off : off + len(r)] += r
    assert _same(full, stitched[: len(full)]) < 1e-9


# --- the grid tools -------------------------------------------------------------

ROWS = ["E3", "D3", "C3", "B2", "A2"]
LADDER = ["A2", "B2", "C3", "D3", "E3", "F3", "G3", "A3"]


def _lane(cells, feel, cuts=()) -> dict:
    return {
        "bars": 1,
        "steps_per_bar": 16,
        "cells": [list(c) for c in cells],
        "cuts": [list(c) for c in cuts],
        "velocities": {},
        "slides": [],
        "note_nudge": {},
        "note_feel": dict(feel),
    }


F = [0.2, -0.3, 0.6]
NOTE = [["A2", 2], ["A2", 3], ["A2", 4]]


def _tool(op, ln, **kw):
    return patternops.notes_tool(
        op, ln, rows=set(ROWS), ladder=LADDER, octave=7, choices=(1, 2, 4), **kw
    )[0]


def test_the_pattern_tools_carry_each_notes_feel():
    ln = _lane(NOTE, {"A2.2": F})
    assert _tool("shift_right", ln)["note_feel"] == {"A2.3": F}
    assert _tool("shift_left", ln)["note_feel"] == {"A2.1": F}
    # reversed: the note on 2-4 runs 11-13, early for late
    assert _tool("reverse", ln)["note_feel"] == {"A2.11": [0.3, -0.2, 0.6]}
    assert _tool("duplicate", ln)["note_feel"] == {"A2.2": F, "A2.18": F}
    assert _tool("up", ln)["note_feel"] == {"B2.2": F}
    assert _tool("clear", ln)["note_feel"] == {}
    assert _tool("vel_ramp", ln)["note_feel"] == {"A2.2": F}


def _roll(op, ln, **args):
    return patternops.roll_tool(op, ln, rows=ROWS, ladder=LADDER, args=args)[0]


def test_the_rolls_edits_carry_each_notes_feel():
    ln = _lane(NOTE + [["C3", 8]], {"A2.2": F, "C3.8": [0.0, 0.0, 1.0]})
    moved = _roll("move", ln, sel="2:2:A2:A2", dx="3", dy="2")
    assert moved["note_feel"] == {"C3.5": F, "C3.8": [0.0, 0.0, 1.0]}
    copied = _roll("copy", ln, sel="2:2:A2:A2", dx="8", dy="0")
    assert copied["note_feel"]["A2.2"] == F and copied["note_feel"]["A2.10"] == F
    assert _roll("remove", ln, sel="2:2:A2:A2")["note_feel"] == {
        "C3.8": [0.0, 0.0, 1.0]
    }
    # a length change keeps where between the lines it is let go
    longer = _roll("resize", ln, sel="2:2:A2:A2", by="2")
    assert longer["note_feel"]["A2.2"] == F
    assert _roll("double", ln, sel="2:2:A2:A2")["note_feel"]["A2.2"] == F
    # a chord stamped over a felt note's start replaces it with fresh notes
    stamped = _roll("stamp", ln, row="A2", step="2", q="triad", len="1")
    assert "A2.2" not in stamped["note_feel"]
    # snapped: on the grid again
    snapped = _roll("snap", ln, sel="2:2:A2:A2")
    assert snapped["note_feel"] == {"C3.8": [0.0, 0.0, 1.0]}


def test_a_cell_drawn_before_a_felt_note_starts_a_fresh_one():
    feel = {"A2.2": F}
    assert schema.clean_feel(feel, NOTE + [["A2", 1]]) == {}  # merged: fresh start
    assert schema.clean_feel(feel, NOTE + [["A2", 1]], [["A2", 2]]) == feel  # a cut
    assert schema.clean_feel(feel, NOTE[1:]) == {}  # its first cell erased


def test_songops_window_carries_feel_and_drops_a_cut_notes():
    p = {
        "kind": "notes",
        "bars": 2,
        "steps_per_bar": 16,
        "cells": NOTE + [["C3", 20]],
        "cuts": [],
        "velocities": {},
        "slides": [],
        "note_feel": {"A2.2": F, "C3.20": [0.1, 0.0, 1.0]},
    }
    out = songops.window(p, 1, 1)
    assert out["note_feel"] == {"C3.4": [0.1, 0.0, 1.0]}


def test_tiling_and_regridding_carry_feel():
    assert notefeel.tile({"A2.2": F}, 16, 48) == {"A2.2": F, "A2.18": F, "A2.34": F}
    # onto a finer grid: the same time, in its steps
    assert notefeel.regrid({"A2.2": F}, 1, 16, 32) == {"A2.4": [0.4, -0.6, 0.6]}
    assert notefeel.regrid({"A2.2": F}, 1, 16, 8) == {}


# --- hostile input --------------------------------------------------------------


def test_hostile_feel_is_conformed():
    raw = {
        "cells": [["A2", 0], ["A2", 1], ["C3", 3]],
        "note_feel": {
            "A2.0": [9, -9, 7],  # clamped
            "A2.1": [0, 0, 1],  # not a note's start
            "C3.3": ["x", 0, 1],
            "C3.3.3": [0, 0, 1],
            "Q9.3": [0, 0, 1],
            "C3.99999": [0, 0, 1],
            "C3.-3": [0, 0, 1],
            "C3.3 ": [0, 0, 1],
            7: [0, 0, 1],
            "E3.4": [float("nan"), 0, 1],
        },
    }
    out = schema.conform(raw, schema.nodes().ARRANGEMENT)
    assert out["note_feel"] == {"A2.0": [0.5, -1.5, 1.0]}
    assert notefeel.value([0, 0, 0]) is None and notefeel.value([0, 0]) is None
    big = {f"A2.{i}": [0, 0, 1] for i in range(20000)}
    out = schema.conform({"cells": [], "note_feel": big}, schema.nodes().ARRANGEMENT)
    assert "note_feel" not in out  # bounded, then none names a note
