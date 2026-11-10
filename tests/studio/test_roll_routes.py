"""The piano roll over the routes: the two
pattern tool routes take a roll edit and answer with the selection, the
grid's window (rows, octave) is posted with it, the lane switch adds a
lane that plays its own grid, and the fragments carry what the roll draws.

The ops themselves are `test_roll_ops.py`; the gestures that make these
posts are `tests/browser/test_input_matrix.py`.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import rollview, schema
from synthlab.studio.server import create_app
from synthlab.studio.state import StudioState, pitch_rows
from synthlab.studio.state.model import grid_rows
from tests.studio.test_song_routes import HX as SONG
from tests.studio.test_song_routes import use_song

PERFORM = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/perform"}
NOTES = "/fragment/pattern/notes/"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        s = c.app.state.studio
        s.grid_span = 2
        s.arrangement = replace(
            s.arrangement,
            root="A1",
            scale_name="minor",
            bars=1,
            steps_per_bar=16,
            cells=[["A1", 0], ["A1", 1], ["C2", 4]],
            cuts=[],
            velocities={"0": 0.4},
            slides=[4],
        )
        yield c


def arr(client):
    return client.app.state.studio.arrangement


def cells(client) -> list[tuple]:
    return sorted(map(tuple, arr(client).cells))


def trig(r) -> dict:
    return json.loads(r.headers.get("HX-Trigger") or "{}")


# --- the loop's tool route ----------------------------------------------------


def test_a_move_carries_the_note_and_answers_with_where_it_went(client):
    s = client.app.state.studio
    data = {"sel": "0:1:A1:A1", "dx": "8", "dy": "2", "span": "2", "oct": "0"}
    r = client.post(NOTES + "move", data=data, headers=PERFORM)
    assert r.status_code == 200
    assert cells(client) == [("C2", 4), ("C2", 8), ("C2", 9)]
    assert arr(client).velocities == {"8": 0.4}  # the velocity went with it
    assert trig(r)["roll-sel"] == {"sel": "8:9:C2:C2"}
    # one pattern-undo step, and it brings the lane's notes AND lanes back
    assert len(s._pattern_undo) == 1
    client.post("/fragment/pattern/undo", headers=PERFORM)
    assert cells(client) == [("A1", 0), ("A1", 1), ("C2", 4)]
    assert arr(client).velocities == {"0": 0.4}


def test_a_copy_and_a_resize_and_a_remove(client):
    data = {"sel": "0:1:A1:A1", "dx": "8"}
    r = client.post(NOTES + "copy", data=data, headers=PERFORM)
    assert cells(client) == [("A1", 0), ("A1", 1), ("A1", 8), ("A1", 9), ("C2", 4)]
    assert trig(r)["roll-sel"] == {"sel": "8:9:A1:A1"}
    r = client.post(
        NOTES + "resize", data={"sel": "8:9:A1:A1", "by": "2"}, headers=PERFORM
    )
    assert ("A1", 11) in cells(client) and trig(r)["roll-sel"] == {"sel": "8:11:A1:A1"}
    r = client.post(NOTES + "remove", data={"sel": "8:8:A1:A1"}, headers=PERFORM)
    assert cells(client) == [("A1", 0), ("A1", 1), ("C2", 4)]
    assert trig(r)["roll-sel"] == {"sel": ""}  # nothing is selected any more


def test_the_patterns_shift_moves_a_selection_when_the_post_carries_one(client):
    r = client.post(NOTES + "shift_right", data={"sel": "4:4:C2:C2"}, headers=PERFORM)
    assert cells(client) == [("A1", 0), ("A1", 1), ("C2", 5)]
    assert arr(client).slides == [5] and trig(r)["roll-sel"] == {"sel": "5:5:C2:C2"}
    # without one it is the tool it always was: every note, wrapping
    client.post(NOTES + "shift_left", headers=PERFORM)
    assert cells(client) == [("A1", 0), ("A1", 15), ("C2", 4)]


def test_legato_half_and_double_take_the_window_or_a_selection(client):
    client.post(NOTES + "legato", headers=PERFORM)
    assert cells(client) == [
        *[("A1", s) for s in range(4)],
        *[("C2", s) for s in range(4, 16)],
    ]
    r = client.post(NOTES + "half", data={"sel": "4:4:C2:C2"}, headers=PERFORM)
    assert sum(c[0] == "C2" for c in cells(client)) == 6
    assert sum(c[0] == "A1" for c in cells(client)) == 4  # not selected: untouched
    assert trig(r)["roll-sel"] == {"sel": "4:9:C2:C2"}


def test_a_stamp_writes_the_chord_in_the_key_on_the_clicked_row(client):
    data = {"row": "D2", "step": "8", "q": "7th", "len": "4"}
    r = client.post(NOTES + "stamp", data=data, headers=PERFORM)
    assert r.status_code == 200 and "roll-sel" not in trig(r)
    new = {c for c in cells(client) if c[1] >= 8}
    assert new == {(p, s) for p in ("D2", "F2", "A2", "C3") for s in range(8, 12)}


def test_a_stamp_says_which_tones_the_rows_had_no_place_for(client):
    data = {"row": "G3", "step": "0", "q": "triad", "len": "1"}
    r = client.post(NOTES + "stamp", data=data, headers=PERFORM)
    assert "past the grid's rows" in trig(r)["studio-toast"]["message"]
    assert ("G3", 0) in cells(client) and not any(c[0] > "G3" for c in cells(client))


@pytest.mark.parametrize(
    ("op", "data"),
    [
        ("move", {}),  # no selection
        ("move", {"sel": "0:1:Z9:A1"}),  # not a row of this grid
        ("resize", {"sel": "0:1:A1:A1", "by": "lots"}),
        ("stamp", {"row": "A1", "step": "99"}),
        ("stamp", {"row": "A1", "step": "0", "q": "cluster"}),
        ("copy", {"sel": "1:2:A1:A1", "dx": "9" * 30}),
    ],
)
def test_a_roll_edit_that_cannot_apply_is_a_toast_and_changes_nothing(client, op, data):
    before = (cells(client), dict(arr(client).velocities))
    r = client.post(NOTES + op, data=data, headers=PERFORM)
    assert r.status_code == 422 and "toast" in r.text
    assert (cells(client), dict(arr(client).velocities)) == before
    assert not client.app.state.studio._pattern_undo


def test_an_unknown_tool_is_still_a_404(client):
    assert client.post(NOTES + "explode", headers=PERFORM).status_code == 404


# --- rows and the octave window -------------------------------------------------


def test_chromatic_rows_are_24_semitones_and_the_window_moves_by_octaves(client):
    s = client.app.state.studio
    a = s.arrangement
    assert pitch_rows(a, 3)[-1] == "A1" and pitch_rows(a, 3)[0] == "G#3"
    assert len(pitch_rows(a, 3)) == 24
    assert grid_rows(a, 2, 1)[-1] == "A2" and grid_rows(a, 2, -1)[-1] == "A0"
    assert grid_rows(a, 2, 0) == pitch_rows(a, 2)  # no window: every grid before
    r = client.post("/fragment/grid/span", data={"span": "3"}, headers=PERFORM)
    # (204 from the route; 200 once the wrapper adds the redrawn transport)
    assert r.status_code == 200 and 'id="transport"' in r.text and s.grid_span == 3
    for _ in range(6):  # ... and stops at its range
        client.post("/fragment/grid/span", data={"oct_by": "1"}, headers=PERFORM)
    assert s.grid_oct == 3
    for _ in range(6):
        client.post("/fragment/grid/span", data={"oct_by": "-1"}, headers=PERFORM)
    assert s.grid_oct == -1


@pytest.mark.parametrize("data", [{}, {"span": "4"}, {"oct_by": "2"}, {"span": "x"}])
def test_a_hostile_window_is_refused(client, data):
    s = client.app.state.studio
    r = client.post("/fragment/grid/span", data=data, headers=PERFORM)
    assert r.status_code == 422 and (s.grid_span, s.grid_oct) == (2, 0)


def test_the_octave_window_is_a_view_setting_and_is_not_saved(client, tmp_path):
    s = client.app.state.studio
    client.post("/fragment/grid/span", data={"oct_by": "1"}, headers=PERFORM)
    client.post("/fragment/grid/span", data={"span": "3"}, headers=PERFORM)
    s.save()
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "grid_oct" not in saved and saved["grid_span"] == 3
    again = StudioState(tmp_path)
    assert (again.grid_span, again.grid_oct) == (3, 0)


def test_a_grid_post_keeps_the_notes_its_window_could_not_show(client):
    """The roll posts the `span` and `oct` it was drawn with: a note outside
    that window was never a checkbox, so an unticked one is not a delete."""
    s = client.app.state.studio
    s.grid_oct = 1  # the window shows A2..A4: the loop's A1 and C2 are hidden
    form = {"bars": "1", "steps": "16", "span": "2", "oct": "1", "cell.E4.2": "on"}
    r = client.post("/fragment/arrangement", data=form, headers=PERFORM)
    assert r.status_code == 200
    assert cells(client) == [("A1", 0), ("A1", 1), ("C2", 4), ("E4", 2)]
    # a post from a page drawn at the root's window (A1..A3) replaces what IT
    # shows, and keeps the E4 it could not
    form = {"bars": "1", "steps": "16", "span": "2", "oct": "0", "cell.B1.3": "on"}
    client.post("/fragment/arrangement", data=form, headers=PERFORM)
    assert cells(client) == [("B1", 3), ("E4", 2)]


def test_a_roll_edit_reads_the_window_its_grid_was_drawn_at(client):
    s = client.app.state.studio
    s.grid_span = 3  # the session moved on; the page that posts is older
    data = {"sel": "0:1:A1:A1", "dy": "1", "span": "2", "oct": "0"}
    client.post(NOTES + "move", data=data, headers=PERFORM)
    assert ("B1", 0) in cells(client)  # a row of the KEY up, not a semitone


# --- the lane switch --------------------------------------------------------------


def test_a_lane_added_on_perform_has_its_own_grid_and_the_page_stays(client):
    s = client.app.state.studio
    was = sorted(map(tuple, s.main_arrangement().cells))
    r = client.post("/fragment/layer/add", data={"own": "1"}, headers=PERFORM)
    assert r.status_code == 200 and "HX-Push-Url" not in r.headers
    assert 'data-view="perform"' in r.text
    assert s.active_layer == 1 and not s.follows(1)
    assert s.arrangement.cells == []  # its own grid, empty
    # the lane that was there is still the main lane, playing what it played
    assert s.main_index() == 0
    assert sorted(map(tuple, s.main_arrangement().cells)) == was
    assert s.lane_arrangement().cells == []  # ... and the new one is silent
    # a note drawn on it is what it plays; the first lane keeps its own
    form = {"bars": "1", "steps": "16", "span": "2", "oct": "0", "cell.E2.8": "on"}
    client.post("/fragment/arrangement", data=form, headers=PERFORM)
    assert s.lane_arrangement().cells == [["E2", 8]]
    assert sorted(map(tuple, s.layers[0]["cells"])) == was


def test_a_layer_added_on_synths_still_follows(client):
    s = client.app.state.studio
    editor = {**PERFORM, "HX-Current-URL": "http://testserver/view/editor"}
    client.post("/fragment/layer/add", headers=editor)
    assert s.active_layer == 1 and s.follows(1)


def test_the_transport_draws_the_lane_switch_and_the_follow_note(client):
    s = client.app.state.studio
    s.add_layer(s.patch, select=False)
    html = client.get("/view/perform").text
    seg = html[html.index('class="lane-seg"') :]
    seg = seg[: seg.index("</fieldset>")]
    assert seg.count('hx-post="/fragment/layer/select"') == 2
    names = [b for b in seg.split("</button>") if "lh-name" in b]
    assert 'aria-pressed="true"' in names[0]  # lane 1 is edited
    assert "↳" in names[1]  # lane 2 follows
    assert 'hx-vals=\'{"own": "1"}\'' in seg
    assert "lane-follow-note" not in html
    s.select_layer(1)
    html = client.get("/view/perform").text
    note = html[html.index('class="lane-follow-note"') :]
    note = note[: note.index("</p>")]
    assert "follows Layer 1" in note and "draw its own" in note
    assert 'hx-vals=\'{"index": "1", "on": "0"}\'' in note


# --- what the grid draws --------------------------------------------------------------


def _grid(html: str, start: str = '<form id="transport"') -> str:
    part = html[html.index(start) :]
    return part[part.index('class="grid roll"') : part.index('class="velrow"')]


def test_the_roll_marks_rows_out_of_the_key_and_the_other_lanes_as_ghosts(client):
    s = client.app.state.studio
    s.add_layer(s.patch, select=False)  # a follower: it plays lane 1's notes
    s.set_follow(1, None)  # ... no longer: these are its own
    s.layers[1]["cells"] = [["E2", 3], ["F#2", 5]]
    s.grid_span = 3
    grid = _grid(client.get("/view/perform").text)
    rows = re.findall(
        r'<div class="gridrow( off-key)?" role="group" aria-label="(\S+) row"', grid
    )
    assert len(rows) == 24
    assert {p for off, p in rows if off} == set(
        "A#1 C#2 D#2 F#2 G#2 A#2 C#3 D#3 F#3 G#3".split()
    )
    ghost = re.findall(r'name="cell\.(\S+?)\.(\d+)"\s+class="cell [^"]*ghost g1', grid)
    assert sorted(ghost) == [("E2", "3"), ("F#2", "5")]
    assert 'data-span="3" data-oct="0"' in grid and "var(--zoom-perform)" in grid
    # the ruler counts the bars
    assert re.search(r'<div class="ruler"[^>]*>.*?<b [^>]*>1</b>', grid, re.S)


def test_a_muted_lane_and_the_edited_one_cast_no_ghost(client):
    s = client.app.state.studio
    s.add_layer(s.patch, select=False)
    s.set_follow(1, None)
    s.layers[1]["cells"] = [["E2", 3]]
    s.set_layer_muted(1, True)
    assert "ghost" not in _grid(client.get("/view/perform").text)


def test_rollview_marks_chord_tones_and_roots_per_step():
    rows = ["E2", "D2", "C2", "B1", "A1"]
    # A minor: under steps 0-3 the chord is A-C-E, under 4-7 it is D-F-A
    ctx = rollview.context(
        rows,
        8,
        root="A1",
        scale_name="minor",
        ghosts=[(2, "Pad", [["C2", 1], ["C2", 9], ["F#4", 0]])],
        chords=[(0, 4, 9, [0, 3, 7]), (4, 8, 2, [5, 8, 12])],
        chord_track=True,
    )
    m = ctx["marks"]
    assert m[("A1", 0)] == "ct cr" and m[("E2", 3)] == "ct" and ("D2", 0) not in m
    assert m[("D2", 4)] == "ct cr" and m[("A1", 7)] == "ct" and ("C2", 4) not in m
    assert m[("C2", 1)] == "ct ghost g2"  # a ghost on a chord tone is both
    assert ctx["ghost_names"] == ["Pad"] and ctx["off"] == set()
    assert ctx["roots"] == {"A1"} and ctx["chord_track"] is True
    # steps past the grid and rows off it are not marked
    assert not any(k[1] >= 8 or k[0] not in rows for k in m)


# --- the Song drawer ----------------------------------------------------------------


@pytest.fixture
def drawer(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        c.app.state.studio.grid_span = 2
        c.post(
            "/song/drawer",
            data={"section_id": "s2", "track_id": "t1"},
            headers=SONG,
        )
        yield c


def pat(client, pid: str = "p1") -> dict:
    return client.app.state.studio.song["patterns"][pid]


def test_the_drawers_roll_moves_a_pool_pattern_in_one_song_undo_step(drawer):
    s = drawer.app.state.studio
    undo = len(s._song_undo)
    data = {"sel": "0:0:A1:A1", "dx": "2", "dy": "1", "span": "2", "oct": "0"}
    r = drawer.post("/song/pattern/p1/tool/move", data=data, headers=SONG)
    assert r.status_code == 200
    assert sorted(map(tuple, pat(drawer)["cells"])) == [("B1", 2), ("E2", 4)]
    assert trig(r)["roll-sel"] == {"sel": "2:2:B1:B1"}
    assert set(trig(r)["song-changed"]["sections"]) == {"s1", "s2"}  # it is shared
    assert len(s._song_undo) == undo + 1
    drawer.post("/song/undo", headers=SONG)
    assert sorted(map(tuple, pat(drawer)["cells"])) == [("A1", 0), ("E2", 4)]


def test_a_refused_drawer_edit_is_a_toast(drawer):
    r = drawer.post("/song/pattern/p1/tool/remove", headers=SONG)
    assert r.status_code == 422 and "Select some notes first" in r.text


def test_the_drawer_stamps_the_chord_the_chord_track_holds_under_the_step(drawer):
    s = drawer.app.state.studio
    # a progression of a bar: i for two beats, then VI (A minor: Am, then F)
    slots = [
        {"at": 0, "deg": 1, "q": "triad", "inv": 0, "len": 2},
        {"at": 2, "deg": 6, "q": "triad", "inv": 0, "len": 2},
    ]
    held = {**s.song, "chords": {"bars": 1, "slots": slots}}
    s.song = schema.conform(held, schema.nodes().SONG)
    assert len(s.song["chords"]["slots"]) == 2
    for step, want in ((1, {"A1", "C2", "E2"}), (9, {"F2", "A2", "C3"})):
        data = {"row": "A2", "step": str(step), "q": "track", "len": "1"}
        data |= {"span": "2", "oct": "0"}
        drawer.post("/song/pattern/p1/tool/stamp", data=data, headers=SONG)
        got = {c[0] for c in pat(drawer)["cells"] if c[1] == step}
        # root position from the chord's root at or below the clicked row
        assert {p[:-1] for p in got} == {p[:-1] for p in want}, (step, got)


def test_the_drawer_draws_ghosts_of_the_sections_other_tracks_and_chord_tones(drawer):
    html = drawer.get("/view/song").text
    grid = _grid(html, 'id="drawer-grid"')
    # Verse: t2 plays Hook (C2 on step 2 of a 2-bar pattern) beside the open t1 cell
    ghost = re.findall(r'name="cell\.(\S+?)\.(\d+)"\s+class="cell [^"]*ghost g\d', grid)
    assert ghost == [("C2", "2")]
    assert 'class="cell beat ct cr"' in grid  # the key's first chord, by default
    assert "var(--zoom-drawer)" in grid
    bar = html[html.index('aria-label="Piano roll tools"') :]
    bar = bar[: bar.index('id="drawer-grid"')]
    assert 'hx-post="/song/pattern/p1/tool/legato"' in bar
    assert '<option value="track">chord track</option>' in bar
    # one range control: the roll's own (six, the key, all)
    assert html.count('hx-vals=\'{"span": "2"}\'') == 1


def test_the_drawers_octave_window_is_the_open_drawers_own(drawer):
    s = drawer.app.state.studio
    svc = drawer.app.state.svc
    assert svc._song_drawer()["oct"] == 0
    r = drawer.post("/fragment/grid/span", data={"oct_by": "1"}, headers=SONG)
    assert r.status_code == 200 and 'id="song-drawer"' in r.text
    assert svc._song_drawer()["oct"] == 1 and s.grid_oct == 0  # not the loop's
    assert svc._song_drawer()["rows"][-1] == "A2"
    # a roll edit from that grid reads its window: A2 is a row of it, A1 is not
    data = {"row": "A2", "step": "6", "q": "power", "len": "1", "span": "2", "oct": "1"}
    drawer.post("/song/pattern/p1/tool/stamp", data=data, headers=SONG)
    assert {c[0] for c in pat(drawer)["cells"] if c[1] == 6} == {"A2", "E3", "A3"}
    # opening another cell forgets it: the window is derived again
    drawer.post(
        "/song/drawer", data={"section_id": "s2", "track_id": "t2"}, headers=SONG
    )
    assert "oct" not in s.song_drawer and svc._song_drawer()["oct"] == 0
