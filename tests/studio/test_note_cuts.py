"""Cuts: two notes side by side on one row (`Arrangement.cuts`). A run of
adjacent steps is one held note; a cut cell attacks again. The grid posts
a cut as the cell's value ("cut", Shift/Alt-click in grid.js)."""

from dataclasses import replace

import numpy as np
from fastapi.testclient import TestClient

from synthlab.studio.arrange import arrangement_from_form, build_song
from synthlab.studio.render import RenderInputs
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import preset

PERFORM = {"HX-Current-URL": "http://testserver/view/perform"}
P = preset("pluck_glass")


def _attacks(song, track="synth0") -> int:
    return len(song.tracks[track].events)


def test_a_cut_splits_a_held_run_into_two_notes():
    held = Arrangement(steps_per_bar=8, cells=[["A1", s] for s in range(4)])
    cut = replace(held, cuts=[["A1", 2]])
    assert _attacks(build_song(P, held)) == 1
    assert _attacks(build_song(P, cut)) == 2
    a = build_song(P, held).render(normalize=0)
    b = build_song(P, cut).render(normalize=0)
    assert a.shape != b.shape or not np.allclose(a, b)


def test_a_cut_is_per_row():
    arr = Arrangement(
        steps_per_bar=8,
        cells=[["A1", 0], ["A1", 1], ["C2", 0], ["C2", 1]],
        cuts=[["A1", 1]],
    )
    song = build_song(P, arr)
    counts = sorted(
        len(t.events) for n, t in song.tracks.items() if n.startswith("synth")
    )
    assert counts == [1, 2]


def test_no_cuts_keeps_every_render_key():
    """An empty `cuts` is dropped from the key: no cache identity moved."""
    arr = Arrangement(steps_per_bar=8, cells=[["A1", 0]])
    key = RenderInputs(patch=P, arr=arr).key()
    assert "cuts" not in repr(key)
    assert RenderInputs(patch=P, arr=replace(arr, cuts=[["A1", 0]])).key() != key


def test_a_cut_in_perform_moves_no_bench_render(tmp_path):
    """Synths plays the reference phrase, not the grid: the grid's cuts are
    not its notes'. `phrase_arrangement` kept them, so drawing a cut in
    Perform changed the key of every bench render (▶ on Synths, every card's
    audition) and re-rendered its main stem, for the same audio."""
    with TestClient(create_app(data_dir=tmp_path)) as client:
        svc = client.app.state.svc
        s = svc.state
        s.arrangement = replace(s.arrangement, cells=[["A1", 0], ["A1", 1]])
        plain = svc.render_inputs("editor")
        s.arrangement = replace(s.arrangement, cuts=[["A1", 1]])
        cut = svc.render_inputs("editor")
        assert cut.arr.cuts == [] and cut.key() == plain.key()
        # the loop itself still plays (and keys) the cut
        assert svc.render_inputs("perform").arr.cuts == [["A1", 1]]


def test_the_form_posts_a_cut_as_the_cells_value():
    arr = arrangement_from_form(
        {"steps": "8", "cell.A1.0": "on", "cell.A1.1": "cut", "cell.C2.3": "on"}
    )
    assert arr.cuts == [["A1", 1]] and ["A1", 1] in arr.cells


def _grid(extra: dict) -> dict:
    return {
        "bars": "1",
        "steps": "8",
        "root": "A1",
        "scale_name": "pentatonic_minor",
        "swing": "0",
        "arp_mode": "off",
        "arp_octaves": "1",
        **extra,
    }


def test_the_grid_post_keeps_and_drops_cuts(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        s = c.app.state.studio
        c.post(
            "/fragment/arrangement",
            data=_grid({"cell.A1.0": "on", "cell.A1.1": "cut"}),
            headers=PERFORM,
        )
        assert s.arrangement.cuts == [["A1", 1]]
        # the cell goes: its cut goes with it
        c.post(
            "/fragment/arrangement", data=_grid({"cell.A1.0": "on"}), headers=PERFORM
        )
        assert s.arrangement.cuts == []
        html = c.get("/view/perform").text
        assert 'value="cut"' not in html


def test_the_perform_grid_draws_a_cut(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        s = c.app.state.studio
        s.arrangement = replace(
            s.arrangement,
            steps_per_bar=8,
            cells=[["A1", 0], ["A1", 1]],
            cuts=[["A1", 1]],
        )
        html = c.get("/view/perform").text
        assert 'name="cell.A1.1"' in html
        tag = html.split('name="cell.A1.1"')[1].split(">")[0]
        assert 'value="cut"' in tag and "checked" in tag


def test_cuts_travel_with_their_layer_and_into_the_song(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        s = c.app.state.studio
        s.arrangement = replace(
            s.arrangement,
            steps_per_bar=8,
            cells=[["A1", 0], ["A1", 1], ["A1", 2]],
            cuts=[["A1", 2]],
        )
        c.post("/fragment/layer/select", data={"index": "1"}, headers=PERFORM)
        assert s.arrangement.cuts == []  # the other slot's lane
        c.post("/fragment/layer/select", data={"index": "0"}, headers=PERFORM)
        assert s.arrangement.cuts == [["A1", 2]]
        sid = s.loop_to_section("cut")
        sec = next(x for x in s.song["sections"] if x["id"] == sid)
        pat = s.song["patterns"][next(iter(sec["cells"].values()))]
        pats = [p for p in s.song["patterns"].values() if p["kind"] == "notes"]
        assert any(p.get("cuts") == [["A1", 2]] for p in pats), pat
        s.arrangement = replace(s.arrangement, cells=[], cuts=[])
        assert s.section_to_loop(sid)
        assert s.arrangement.cuts == [["A1", 2]]


def test_reverse_keeps_two_notes_two_notes(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        s = c.app.state.studio
        s.arrangement = replace(
            s.arrangement,
            steps_per_bar=8,
            cells=[["A1", 0], ["A1", 1], ["A1", 2], ["A1", 3]],
            cuts=[["A1", 1]],
        )
        c.post("/fragment/pattern/notes/reverse", headers=PERFORM)
        a = s.arrangement
        assert sorted(map(tuple, a.cells)) == [
            ("A1", 4),
            ("A1", 5),
            ("A1", 6),
            ("A1", 7),
        ]
        # [0][1 2 3] mirrored is [4 5 6][7]: the cut now starts the last step
        assert a.cuts == [["A1", 7]]


def test_recorded_repeats_are_separate_notes():
    """Four played 16ths on one pitch record as four attacks, not one run."""
    from synthlab.studio import record

    pat = {"cells": [], "velocities": {}, "cuts": []}
    record.merge(pat, [("A1", s, 1.0) for s in range(4)])
    assert sorted(map(tuple, pat["cells"])) == [("A1", s) for s in range(4)]
    assert sorted(map(tuple, pat["cuts"])) == [("A1", 1), ("A1", 2), ("A1", 3)]


def test_an_absurdly_long_step_number_is_ignored_not_a_500(tmp_path):
    """`int()` refuses a digit string past Python's 4300-digit limit with a
    ValueError: `cell.C4.<5000 nines>` got through `isdigit()` to it."""
    with TestClient(create_app(data_dir=tmp_path)) as c:
        s = c.app.state.studio
        nines = "9" * 5000
        form = {"cell.A1.0": "on", f"cell.A1.{nines}": "on", f"vel.{nines}": "0.5"}
        r = c.post("/fragment/arrangement", data=_grid(form), headers=PERFORM)
        assert r.status_code == 200
        assert s.arrangement.cells == [["A1", 0]]
