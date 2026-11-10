"""A step's chance on the note grid: `Arrangement.note_chance`, step ->
0..1, the odds that the
notes starting on that step play — rolled on a hash, never the dice (the
drum grid's rule, drums.md "Chance: a hash"), read by the one schedule.

Held here:

- absent means absent: with no chance the field is in no session file,
  no loop key, no stem key, no slot `seq`, no loop file, and the schedule
  and the audio are the old ones to the sample (no `RENDER_REV` move: the
  pinned key literals of `test_render.py` / `test_song_routes.py` are the
  other half of that);
- the roll is a hash: the same in every call, monotone in the chance (a
  note that plays at 0.5 plays at 0.8), a chord one event, a follower an
  octave away rolling what its main lane rolls; 0 never plays;
- a dropped note is just not there: the render with chance is the render
  of the grid without those notes, to the sample, and the loop's MIDI file
  holds the notes that play;
- the grid form writes it (a step at 100 % is not stored), a form without
  the lane leaves it, the pattern tools carry it with its notes, a layer
  switch keeps each slot's own, an undo puts it back, and a saved loop
  keeps it (one without it opens with none).

Nobody listened: whether thinning a riff this way sounds musical is the
ear's.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import midifile as mf
from synthlab.sequencer import midi as number
from synthlab.studio import arrange, loops, render, schema
from synthlab.studio.arrange import build_song, note_plays, note_roll, note_schedule
from synthlab.studio.render import RenderInputs
from synthlab.studio.schema import arr_dict
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.studio.state.model import render_layer
from synthlab.synth import Patch

P = Patch(name="short", release=0.05)
HX = {"HX-Request": "true"}
# 120 bpm, one bar of 16 steps: a note every other step (step 4 held two
# steps), and a triad on step 8
RIFF = Arrangement(
    bpm=120.0,
    bars=1,
    steps_per_bar=16,
    cells=[
        ["A2", 0],
        ["C3", 2],
        ["E3", 4],
        ["E3", 5],
        ["A2", 6],
        ["A2", 8],
        ["C3", 8],
        ["E3", 8],
        ["G3", 10],
        ["A3", 12],
        ["C3", 14],
    ],
)
HALF = {str(s): 0.5 for s in range(16)}


def _starts(lane) -> list[float]:
    return sorted({round(s, 6) for s, *_ in lane})


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


# --- absent means absent ----------------------------------------------------------


def test_with_no_chance_the_field_is_written_nowhere_and_no_key_moves():
    assert "note_chance" not in arr_dict(RIFF)
    assert arr_dict(replace(RIFF, note_chance={"2": 0.5}))["note_chance"] == {"2": 0.5}
    conformed = schema.conform(arr_dict(RIFF), schema.nodes().ARRANGEMENT)
    assert "note_chance" not in conformed
    assert schema.nodes().ARRANGEMENT.default().get("note_chance") is None
    plain = RenderInputs(patch=P, arr=RIFF, parts=("synth",))
    (job,) = render._stem_jobs(plain)
    assert "note_chance" not in job.payload
    chanced = RenderInputs(
        patch=P, arr=replace(RIFF, note_chance=HALF), parts=("synth",)
    )
    assert chanced.key() != plain.key()
    assert render._stem_jobs(chanced)[0].payload["note_chance"] == HALF
    # the beat alone never reads the note grid's chance
    drums = replace(RIFF, drums=[["kick", 0]])
    a = RenderInputs(patch=P, arr=drums, parts=("drums",)).key()
    b = RenderInputs(patch=P, arr=replace(drums, note_chance=HALF), parts=("drums",))
    assert a == b.key()


def test_render_rev_did_not_move():
    assert render.RENDER_REV == 3


def test_with_no_chance_the_schedule_and_the_audio_are_the_old_ones():
    assert arrange._chanced({"A2": [0, 1]}, RIFF) == {"A2": [0, 1]}
    a = build_song(P, RIFF).render(normalize=0)
    b = build_song(P, replace(RIFF, note_chance={})).render(normalize=0)
    assert np.array_equal(a, b)
    # a step at 100 % is no chance at all
    full = replace(RIFF, note_chance={str(s): 1.0 for s in range(16)})
    assert note_schedule(full) == note_schedule(RIFF)


def test_the_schema_keeps_only_what_says_something():
    node = schema.nodes().ARRANGEMENT
    raw = arr_dict(RIFF) | {"note_chance": {"0": 1.0, "2": 0.3, "x": 0.2, "4": 7}}
    assert schema.conform(raw, node)["note_chance"] == {"2": 0.3}
    raw["note_chance"] = {"0": 1.0}
    assert "note_chance" not in schema.conform(raw, node)


# --- the roll ---------------------------------------------------------------------


def test_the_roll_is_a_hash_and_monotone_in_the_chance():
    pcs = (number("A2"),)
    assert note_roll(pcs, 3) == note_roll(pcs, 3)
    assert 0.0 <= note_roll(pcs, 3) < 1.0
    for step in range(64):
        r = note_roll(pcs, step)
        assert note_plays({str(step): 1.0}, step, pcs)
        assert not note_plays({str(step): 0.0}, step, pcs)
        assert note_plays({str(step): r + 1e-9}, step, pcs)
        assert not note_plays({str(step): r}, step, pcs)
    # an octave away it is the same roll (a follower drops what its lane does)
    assert note_roll((number("A3"),), 5) == note_roll((number("A2"),), 5)


def test_half_chance_drops_some_notes_the_same_ones_every_time():
    arr = replace(RIFF, note_chance=HALF)
    (lane,) = note_schedule(arr)
    (again,) = note_schedule(arr)
    assert lane == again
    kept = _starts(lane)
    (all_,) = note_schedule(RIFF)
    assert 0 < len(kept) < len(_starts(all_))
    # a higher chance keeps every note the lower one kept
    (more,) = note_schedule(replace(RIFF, note_chance={k: 0.8 for k in HALF}))
    assert set(kept) <= set(_starts(more))
    # chance 0 rests every note
    (none,) = note_schedule(replace(RIFF, note_chance={k: 0.0 for k in HALF}))
    assert none == []


def test_a_chord_plays_or_rests_whole_and_a_held_note_by_its_start():
    for p in (0.1, 0.3, 0.5, 0.7, 0.9):
        (lane,) = note_schedule(replace(RIFF, note_chance={"8": p, "5": 0.0}))
        chord = [ev for ev in lane if ev[0] == pytest.approx(8 * 0.25)]
        assert len(chord) in (0, 3)
        # the chance on step 5 (inside the note held from 4) is not its own
        assert any(ev[0] == pytest.approx(1.0) for ev in lane)


def test_editing_another_step_leaves_a_steps_roll_alone():
    a = replace(RIFF, note_chance=HALF)
    b = replace(a, cells=[c for c in RIFF.cells if c[1] != 14])
    keep = {round(s, 6) for s, *_ in note_schedule(a)[0] if s < 14 * 0.25}
    also = {round(s, 6) for s, *_ in note_schedule(b)[0] if s < 14 * 0.25}
    assert keep == also


def test_an_arp_rolls_step_by_step():
    arp = replace(RIFF, arp_mode="up")
    (every,) = note_schedule(arp)
    (some,) = note_schedule(replace(arp, note_chance=HALF))
    assert len(every) == 16 and 0 < len(some) < 16
    assert {e[0] for e in some} <= {e[0] for e in every}


# --- what plays -------------------------------------------------------------------


def _without_dropped(arr: Arrangement) -> Arrangement:
    """`arr` with the notes its chance rests taken off the grid."""
    plays = arrange._chanced(arrange._playing_steps(arr), arr)
    cells = [[p, s] for p, steps in plays.items() for s in steps]
    return replace(arr, cells=sorted(cells), note_chance={})


def test_the_render_is_the_grid_without_the_rested_notes():
    arr = replace(RIFF, note_chance=HALF)
    bare = _without_dropped(arr)
    assert len(bare.cells) < len(RIFF.cells)
    a = build_song(P, arr).render(normalize=0)
    b = build_song(P, bare).render(normalize=0)
    assert np.array_equal(a, b)
    # the timeline path (a slide) rests the same notes
    slid = replace(arr, slides=[10])
    assert note_schedule(slid)[0] == note_schedule(replace(bare, slides=[10]))[0]


def test_a_layer_rolls_its_own_chance_and_a_follower_the_main_lanes():
    main = replace(RIFF, note_chance=HALF)
    own = {"patch": P.to_json(), "cells": [["A3", s] for s in range(0, 16, 2)]}
    own_chance = own | {"note_chance": {str(s): 0.0 for s in range(8)}}
    follower = {"patch": P.to_json(), "cells": [], "follow": {"octave": 1}}
    m, a, b, c = note_schedule(main, [own, own_chance, follower])
    assert len(a) == 8  # never the main lane's chance
    assert len(b) == 4 and min(s for s, *_ in b) == pytest.approx(2.0)
    assert _starts(c) == _starts(m)  # an octave up, the same notes rest
    jobs = render._stem_jobs(
        RenderInputs(patch=P, arr=main, layers=(own, own_chance), parts=("synth",))
    )
    assert "note_chance" not in jobs[1].payload["layer"]
    assert jobs[2].payload["layer"]["note_chance"] == own_chance["note_chance"]


def test_the_loops_midi_file_holds_the_notes_that_play(client):
    s = client.app.state.studio
    arr = replace(RIFF, note_chance=HALF)
    s.patch, s.arrangement, s.perform_parts = P, arr, ["synth"]
    file = mf.read(client.get("/export/loop.mid").content)
    got = sorted((round(n.start, 3), n.pitch) for n in file.tracks[0].notes)
    # the loop plays (and writes) four passes of its grid, each rolled
    # afresh; the first is the grid's own roll
    first = [(t, p) for t, p in got if t < 4.0]
    want = sorted((round(st, 3), number(p)) for st, _d, p, *_ in note_schedule(arr)[0])
    assert first == want and len(first) < len(RIFF.cells) - 1
    passes = [
        [(round(t - 4 * k, 3), p) for t, p in got if 4 * k <= t < 4 * k + 4]
        for k in range(4)
    ]
    assert max(t for t, _p in got) < 16.0
    assert len({tuple(x) for x in passes}) > 1  # the passes differ


# --- the forms and the edits ------------------------------------------------------


def test_perform_draws_the_lane_and_the_song_drawer_does_not(client):
    page = client.get("/view/perform").text
    assert 'class="velrow chancerow"' in page and 'name="nchance.0"' in page
    assert 'name="nchance.' not in client.get("/view/song").text


def test_the_grid_form_writes_the_chance_and_other_forms_leave_it(client):
    s = client.app.state.studio
    s.arrangement = replace(s.arrangement, steps_per_bar=8, cells=[["A1", 0]])
    form = {"bars": "1", "steps": "8", "span": "1", "cell.A1.0": "on"}
    form |= {f"nchance.{i}": "1" for i in range(8)} | {"nchance.0": "0.25"}
    r = client.post("/fragment/arrangement", data=form, headers=HX)
    assert r.status_code == 200 and s.arrangement.note_chance == {"0": 0.25}
    # the sound bar posts the key and the tempo alone: the lane stays
    client.post("/fragment/arrangement", data={"bpm": "100", "root": "A1"}, headers=HX)
    assert s.arrangement.note_chance == {"0": 0.25}
    # rubbish and out-of-range are held; 100 % clears the step
    form |= {"nchance.0": "nan", "nchance.3": "-4"}
    client.post("/fragment/arrangement", data=form, headers=HX)
    assert s.arrangement.note_chance == {"3": 0.0}
    form |= {"nchance.3": "1"}
    client.post("/fragment/arrangement", data=form, headers=HX)
    assert s.arrangement.note_chance == {} and "note_chance" not in arr_dict(
        s.arrangement
    )


def test_a_shift_carries_the_chance_with_its_notes_and_undo_puts_it_back(client):
    s = client.app.state.studio
    s.arrangement = replace(RIFF, root="A1", scale_name="minor", note_chance={"2": 0.4})
    s.grid_span = 3
    r = client.post(
        "/fragment/pattern/notes/shift_right", data={"span": "3"}, headers=HX
    )
    assert r.status_code == 200
    assert s.arrangement.note_chance == {"3": 0.4}
    client.post("/fragment/pattern/undo", headers=HX)
    assert s.arrangement.note_chance == {"2": 0.4}


def test_each_slot_keeps_its_own_chance(client):
    s = client.app.state.studio
    s.arrangement = replace(RIFF, note_chance={"2": 0.4})
    s.select_layer(1)
    assert s.layers[0]["seq"]["note_chance"] == {"2": 0.4}
    s.arrangement = replace(s.arrangement, cells=[["A3", 0]], note_chance={})
    s.select_layer(0)
    assert s.arrangement.note_chance == {"2": 0.4}
    assert "note_chance" not in s.layers[1]["seq"]  # absent while unset
    s.select_layer(1)
    assert s.arrangement.note_chance == {}
    s.select_layer(0)
    layers = schema.conform(s.layers, schema.nodes().PAYLOAD.fields["layers"])
    assert "note_chance" not in layers[1]["seq"]
    # an inactive slot's chance is in its render dict, on its note steps
    s.select_layer(1)
    s.arrangement = replace(s.arrangement, note_chance={"0": 0.5, "9": 0.5})
    s.select_layer(0)
    assert render_layer(s.layers[1], s.arrangement)["note_chance"] == {"0": 0.5}


def test_a_saved_loop_keeps_its_chance_and_one_without_opens_with_none(client):
    s = client.app.state.studio
    d = s.data_dir
    with_c = loops.save(d, "thin", replace(RIFF, note_chance={"2": 0.4}), None)
    plain = loops.save(d, "full", RIFF, None)
    assert loops.load(d, with_c)["lane"]["note_chance"] == {"2": 0.4}
    assert loops.load(d, plain)["lane"]["note_chance"] == {}
    raw = (d / "loops" / f"{plain}.json").read_text(encoding="utf-8")
    assert "note_chance" not in raw  # absent from the file when none
    r = client.post("/loop/edit", data={"name": with_c}, headers=HX)
    assert r.status_code == 200 and s.arrangement.note_chance == {"2": 0.4}
    client.post("/loop/edit", data={"name": plain}, headers=HX)
    assert s.arrangement.note_chance == {}
