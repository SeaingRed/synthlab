"""Undo and redo on the Song page (studio docs/song.md, "Undo and redo").

Every song edit funnels through `routes/song.py`'s `_commit`, which pushes
the song it replaced onto `StudioState`'s song ring (`state/history.py`):
so each kind of edit is one step that undoes and redoes to the same JSON,
without per-route code. Pinned here: the ring itself (depth, byte bound,
coalescing, redo emptied by a new edit, emptied when the song is replaced
behind it), every kind of edit through its route, the take and track-sound
interplay, the two routes and what the page draws, and that none of it is
ever saved.
"""

from __future__ import annotations

import copy
import itertools
import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from synthlab.studio import schema
from synthlab.studio import song_model as sm
from synthlab.studio.clips import clip_dir, save_clip
from synthlab.studio.state import History, StudioState
from tests.studio import test_song_routes as sr
from tests.studio.test_song_routes import (
    changed,
    oob_ids,
    post,
    refused,
    song,
    song_dict,
    trigger,
)

client = sr.client  # the song-routes fixture: Intro (s1), Verse (s2)

EDITOR = {"HX-Request": "true", "HX-Current-URL": "http://testserver/"}


def raw(song: dict) -> str:
    """The song as it is saved: key order included."""
    return json.dumps(song)


def st(client) -> StudioState:
    return client.app.state.studio


def undo(client):
    return post(client, "/song/undo")


def redo(client):
    return post(client, "/song/redo")


def labels(client) -> tuple[str | None, str | None]:
    state = st(client).song_undo_state()
    return state["undo"], state["redo"]


# --- the ring ------------------------------------------------------------------


def _state(tmp_path) -> StudioState:
    s = StudioState(tmp_path)
    s.song = schema.conform(song_dict(), schema.nodes().SONG)
    return s


def _edit(s: StudioState, n: int, key=None, window=None) -> None:
    """One committed edit, as `_commit` makes it: a fresh song, then a push."""
    old = s.song
    new = copy.deepcopy(old)
    new["sections"][0]["name"] = f"Intro {n}"
    s.song = schema.conform(new, schema.nodes().SONG)
    s.song_undo_push(old, f"rename {n}", key, window)


def _name(s: StudioState) -> str:
    return s.song["sections"][0]["name"]


def test_a_step_is_the_song_before_one_edit_and_undo_then_redo_walk_it(tmp_path):
    s = _state(tmp_path)
    assert s.song_undo() is None and s.song_redo() is None
    for n in (1, 2, 3):
        _edit(s, n)
    assert s.song_undo_state() == {
        "undo": "rename 3",
        "redo": None,
        "depth": 3,
        "undone": [],
    }
    assert s.song_undo() == "rename 3" and _name(s) == "Intro 2"
    assert s.song_undo() == "rename 2" and _name(s) == "Intro 1"
    assert s.song_undo_state()["redo"] == "rename 2"
    assert s.song_redo() == "rename 2" and _name(s) == "Intro 2"
    assert s.song_redo() == "rename 3" and _name(s) == "Intro 3"
    assert s.song_redo() is None


def test_a_new_edit_after_an_undo_leaves_nothing_to_redo(tmp_path):
    s = _state(tmp_path)
    _edit(s, 1)
    _edit(s, 2)
    s.song_undo()
    _edit(s, 9)
    assert s.song_redo() is None and _name(s) == "Intro 9"
    assert s.song_undo() == "rename 9" and _name(s) == "Intro 1"


def test_the_ring_keeps_its_newest_steps_up_to_its_depth(tmp_path):
    s = _state(tmp_path)
    for n in range(History.SONG_UNDO_CAP + 7):
        _edit(s, n)
    assert s.song_undo_state()["depth"] == History.SONG_UNDO_CAP
    for _ in range(History.SONG_UNDO_CAP):
        assert s.song_undo() is not None
    # the oldest seven edits are past reach
    assert s.song_undo() is None and _name(s) == "Intro 6"


def test_the_ring_is_bounded_by_bytes_and_always_keeps_one_step(tmp_path, monkeypatch):
    s = _state(tmp_path)
    one = len(json.dumps(s.song, separators=(",", ":")))
    monkeypatch.setattr(History, "SONG_UNDO_BYTES", int(one * 3.5))
    for n in range(10):
        _edit(s, n)
    assert s.song_undo_state()["depth"] == 3
    assert s.song_undo_bytes() <= one * 3.5
    # the redo stack counts too: an undo moves a step, it does not free one
    s.song_undo()
    assert s.song_undo_bytes() <= one * 3.5 + 20
    monkeypatch.setattr(History, "SONG_UNDO_BYTES", 1)
    _edit(s, 99)
    assert s.song_undo_state()["depth"] == 1
    assert s.song_undo() == "rename 99"


def test_pushes_with_one_key_inside_the_window_are_one_step(tmp_path, monkeypatch):
    """A fader drag posts every ~150 ms: one gesture, one Ctrl+Z."""
    clock = iter([0.0, 0.2, 0.4, 0.6, 5.0, 5.1, 9.0, 9.1, 9.2])
    monkeypatch.setattr("synthlab.studio.state.monotonic", lambda: next(clock))
    s = _state(tmp_path)
    for n in (1, 2, 3, 4):  # one drag
        _edit(s, n, key="gain:t1")
    assert s.song_undo_state()["depth"] == 1
    _edit(s, 5, key="gain:t1")  # the same fader, seconds later: another gesture
    _edit(s, 6, key="gain:t2")  # another control straight after: its own step
    assert s.song_undo_state()["depth"] == 3
    _edit(s, 7)  # an edit with no key never joins, and nothing joins it
    _edit(s, 8)
    _edit(s, 9, key="gain:t2")
    assert s.song_undo_state()["depth"] == 6
    assert s.song_undo() == "rename 9" and _name(s) == "Intro 8"
    for _ in range(4):
        s.song_undo()
    # the whole first drag is the oldest step
    assert _name(s) == "Intro 4" and s.song_undo() == "rename 1"
    assert _name(s) == "Intro"


def test_an_undo_closes_the_step_a_drag_was_joining(tmp_path, monkeypatch):
    clock = itertools.count(0.0, 0.1)
    monkeypatch.setattr("synthlab.studio.state.monotonic", lambda: next(clock))
    s = _state(tmp_path)
    _edit(s, 1, key="k")
    _edit(s, 2, key="k")
    _edit(s, 3)
    s.song_undo()
    _edit(s, 4, key="k")  # the newest step is "k" again, but it was sealed
    assert s.song_undo_state()["depth"] == 2
    assert s.song_undo() == "rename 4" and _name(s) == "Intro 2"


def test_a_take_joins_its_own_posts_however_long_it_records(tmp_path, monkeypatch):
    clock = itertools.count(0.0, 8.0)  # a loop wrap every 8 s
    monkeypatch.setattr("synthlab.studio.state.monotonic", lambda: next(clock))
    s = _state(tmp_path)
    for n in (1, 2, 3):
        _edit(s, n, key="take:a", window=math.inf)
    _edit(s, 4, key="take:b", window=math.inf)
    assert s.song_undo_state()["depth"] == 2
    s.song_undo()
    assert s.song_undo_state()["undone"] == ["take:b"]


def test_an_edit_that_moved_nothing_is_no_step(tmp_path):
    s = _state(tmp_path)
    _edit(s, 1)
    old = s.song
    s.song = schema.conform(copy.deepcopy(old), schema.nodes().SONG)
    s.song_undo_push(old, "nothing")
    assert s.song_undo_state()["depth"] == 1
    assert s.song_undo() == "rename 1"  # and the ring followed the new object


@pytest.mark.parametrize("how", ["import", "revert", "defaults", "migration note"])
def test_a_song_replaced_behind_the_ring_empties_it(tmp_path, how):
    """An import, a revert or reset of the "song" scope, the v2 migration's
    note: the steps lead to a song that is gone (the same reason the take
    undo is emptied). The ring notices by itself — nothing that replaces
    the song has to know it exists."""
    s = _state(tmp_path)
    s.mark_baseline("song", "start")
    _edit(s, 1)
    _edit(s, 2)
    s.song_undo()
    assert s.song_undo_state()["depth"] == 1 and s.song_undo_state()["redo"]
    if how == "import":
        s.load_project(json.loads(json.dumps(s.project_payload())))
    elif how == "revert":
        assert s.revert("song")
    elif how == "defaults":
        assert s.reset_defaults("song")
    else:
        s.song = {**s.song, "migration_note": "3 scenes became sections"}
        assert s.clear_migration_note()
    was = raw(s.song)
    assert s.song_undo_state() == {
        "undo": None,
        "redo": None,
        "depth": 0,
        "undone": [],
    }
    assert s.song_undo() is None and s.song_redo() is None
    assert raw(s.song) == was
    # and it starts again from the new song
    old = s.song
    s.song = schema.conform({**copy.deepcopy(old), "bpm": 97.0}, schema.nodes().SONG)
    s.song_undo_push(old, "tempo")
    assert s.song_undo() == "tempo" and raw(s.song) == was


def test_a_failed_import_leaves_the_ring_as_it_was(tmp_path):
    s = _state(tmp_path)
    _edit(s, 1)
    with pytest.raises(ValueError):
        s.load_project({"format": "nope"})
    assert s.song_undo() == "rename 1"


def test_the_ring_is_never_saved(tmp_path):
    """Undo is runtime state: a session that edited, undid and redid writes
    the bytes of one that only edited (tests/fixtures/state_split pins the
    bytes of a rich session, untouched by this feature)."""
    plain, ringed = _state(tmp_path / "a"), _state(tmp_path / "b")
    _edit(plain, 1)
    plain.save()
    _edit(ringed, 5)
    ringed.song_undo()
    _edit(ringed, 1)
    _edit(ringed, 2)
    ringed.song_undo()
    ringed.song_redo()
    ringed.song_undo()
    ringed.save()
    assert ringed.song_undo_state()["depth"] == 1 and ringed.song_undo_state()["redo"]
    assert ringed._state_path.read_bytes() == plain._state_path.read_bytes()
    assert "undo" not in ringed._state_path.read_text(encoding="utf-8").lower()
    again = StudioState(tmp_path / "b")
    assert again.song_undo() is None and raw(again.song) == raw(ringed.song)


def test_a_renamed_clip_is_renamed_in_every_step(tmp_path):
    s = _state(tmp_path)
    new = copy.deepcopy(s.song)
    sm.place_take(new, "s1", "ta", "jam", 0)
    old, s.song = s.song, schema.conform(new, schema.nodes().SONG)
    s.song_undo_push(old, "place clip")
    _edit(s, 1)
    s.rename_clip_refs("jam", "vox")
    s.song_undo_remap(lambda c: "vox" if c == "jam" else c)
    s.song_undo()
    assert s.song["sections"][0]["audio"][0]["clip"] == "vox"
    s.song_undo()
    s.song_redo()
    assert s.song["sections"][0]["audio"][0]["clip"] == "vox"


# --- every kind of edit, through its route -----------------------------------------

CELL = {"section_id": "s1", "track_id": "t1"}
EMPTY = {"section_id": "s1", "track_id": "t2"}
LANE = {**CELL, "target": "cutoff"}


def _lane(client):
    post(client, "/song/cell/auto", {**LANE, "op": "add"})


def _nothing(client):
    pass


# (label, path, form, setup): one row per kind of song edit
EDITS = [
    ("set cell", "/song/cell", {**EMPTY, "pattern_id": "p3"}, _nothing),
    ("clear cell", "/song/cell", {**CELL, "pattern_id": ""}, _nothing),
    ("new pattern", "/song/cell/new", EMPTY, _nothing),
    ("make unique", "/song/cell/unique", CELL, _nothing),
    ("variation", "/song/cell/variation", {**CELL, "seed": "3"}, _nothing),
    (
        "edit pattern",
        "/song/pattern/p1/grid",
        {"bars": "1", "steps": "16", "cell.A1.0": "on", "cell.C2.5": "on"},
        _nothing,
    ),
    (
        "pattern velocity",
        "/song/pattern/p1/grid",
        {
            "bars": "1",
            "steps": "16",
            "cell.A1.0": "on",
            "cell.E2.4": "on",
            "vel.4": "0.4",
        },
        _nothing,
    ),
    (
        "pattern length",
        "/song/pattern/p1/grid",
        {"bars": "2", "steps": "16", "cell.A1.0": "on"},
        _nothing,
    ),
    (
        "edit pattern",
        "/song/pattern/pd/grid",
        {"bars": "1", "steps": "16", "drum.kick.0": "on", "drum.hat.2": "on"},
        _nothing,
    ),
    ("pattern shift right", "/song/pattern/p1/tool/shift_right", {}, _nothing),
    ("pattern duplicate", "/song/pattern/p1/tool/duplicate", {}, _nothing),
    ("pattern oct up", "/song/pattern/p1/tool/oct_up", {}, _nothing),
    ("clear pattern", "/song/pattern/p1/tool/clear", {}, _nothing),
    ("clear pattern", "/song/pattern/pd/tool/clear", {}, _nothing),
    ("add section", "/song/section/add", {"name": "Bridge", "bars": "4"}, _nothing),
    ("duplicate section", "/song/section/s2/duplicate", {}, _nothing),
    ("delete section", "/song/section/s1/delete", {}, _nothing),
    ("move section", "/song/section/s2/move", {"delta": "-1"}, _nothing),
    ("rename section", "/song/section/s1/rename", {"name": "Opening"}, _nothing),
    ("section repeats", "/song/section/s1/repeats", {"repeats": "3"}, _nothing),
    ("section length", "/song/section/s1/bars", {"bars": "4"}, _nothing),
    ("add track", "/song/track/add", {"kind": "synth", "name": "Pad"}, _nothing),
    ("delete track", "/song/track/ta/delete", {}, _nothing),
    ("rename track", "/song/track/t1/rename", {"name": "Sub"}, _nothing),
    ("track level", "/song/track/t1/mix", {"gain": "0.4"}, _nothing),
    ("mute track", "/song/track/t1/mix", {"mute": "on"}, _nothing),
    ("solo track", "/song/track/t2/mix", {"solo": "on"}, _nothing),
    ("add automation lane", "/song/cell/auto", {**LANE, "op": "add"}, _nothing),
    (
        "automation",
        "/song/cell/auto",
        {**LANE, "op": "set", "start": "0.1", "end": "0.9", "mid_on": "on"},
        _lane,
    ),
    ("remove automation lane", "/song/cell/auto", {**LANE, "op": "remove"}, _lane),
    ("add pattern", "/song/pattern/add", {"kind": "notes", "name": "Fill"}, _nothing),
    ("duplicate pattern", "/song/pattern/p2/duplicate", {}, _nothing),
    ("delete pattern", "/song/pattern/p3/delete", {}, _nothing),
    ("rename pattern", "/song/pattern/p1/rename", {"name": "Bassline"}, _nothing),
    ("add loop to song", "/song/from-loop", {"name": "Jam"}, _nothing),
    (
        "add riser",
        "/song/section/s1/transition",
        {"kind": "riser", "direction": "up"},
        _nothing,
    ),
]


@pytest.mark.parametrize(
    ("label", "path", "form", "setup"), EDITS, ids=[f"{e[0]} {e[1]}" for e in EDITS]
)
def test_every_song_edit_is_one_step_that_undoes_and_redoes_to_the_same_json(
    client, label, path, form, setup
):
    setup(client)
    depth = st(client).song_undo_state()["depth"]
    before = raw(song(client))
    r = post(client, path, form)
    assert r.status_code == 200, r.text[:300]
    after = raw(song(client))
    assert after != before
    assert st(client).song_undo_state()["depth"] == depth + 1
    assert labels(client) == (label, None)

    r = undo(client)
    assert r.status_code == 200
    assert raw(song(client)) == before
    assert trigger(r)["studio-toast"] == {"message": f"Undid: {label}"}
    assert labels(client)[1] == label

    r = redo(client)
    assert r.status_code == 200
    assert raw(song(client)) == after
    assert trigger(r)["studio-toast"] == {"message": f"Redid: {label}"}
    assert labels(client) == (label, None)
    # and round again: redo left the ring where the edit had
    undo(client)
    assert raw(song(client)) == before


def test_a_refused_edit_and_an_edit_that_changed_nothing_are_no_steps(client):
    refused(post(client, "/song/track/td/delete"), "used in")
    refused(post(client, "/song/pattern/p1/tool/nope"))
    assert post(client, "/song/section/s1/rename", {"name": "Intro"}).status_code == 200
    assert post(client, "/song/drawer", CELL).status_code == 200  # no edit at all
    assert labels(client) == (None, None)
    refused(undo(client), "Nothing to undo")
    refused(redo(client), "Nothing to redo")


def test_a_fader_drag_a_lane_drag_and_a_run_of_clicks_are_each_one_step(
    client, monkeypatch
):
    clock = itertools.count(0.0, 0.15)  # a post every 150 ms
    monkeypatch.setattr("synthlab.studio.state.monotonic", lambda: next(clock))
    start = raw(song(client))
    for g in ("0.9", "0.8", "0.7", "0.6"):
        post(client, "/song/track/t1/mix", {"gain": g})
    assert st(client).song_undo_state()["depth"] == 1
    post(client, "/song/track/t2/mix", {"gain": "0.5"})  # another fader
    _lane(client)
    for v in ("0.2", "0.3", "0.4"):
        form = {**LANE, "op": "set", "start": v, "end": "0.9"}
        post(client, "/song/cell/auto", form)
    for _ in range(3):
        post(client, "/song/section/s1/repeats", {"delta": "1"})
    names = []
    while (r := undo(client)).status_code == 200:
        names.append(trigger(r)["studio-toast"]["message"])
    assert names == [
        "Undid: section repeats",
        "Undid: automation",
        "Undid: add automation lane",
        "Undid: track level",
        "Undid: track level",
    ]
    assert raw(song(client)) == start


def test_a_dragged_velocity_bar_is_one_step_and_a_stroke_is_its_own(
    client, monkeypatch
):
    clock = itertools.count(0.0, 0.15)
    monkeypatch.setattr("synthlab.studio.state.monotonic", lambda: next(clock))
    grid = {"bars": "1", "steps": "16", "cell.A1.0": "on", "cell.E2.4": "on"}
    for v in ("0.9", "0.7", "0.5"):
        post(client, "/song/pattern/p1/grid", {**grid, "vel.4": v})
    assert st(client).song_undo_state()["depth"] == 1
    post(client, "/song/pattern/p1/grid", {**grid, "cell.C2.8": "on", "vel.4": "0.5"})
    post(client, "/song/pattern/p1/grid", {**grid, "vel.4": "0.5"})
    assert st(client).song_undo_state()["depth"] == 3
    assert labels(client)[0] == "edit pattern"


# --- the routes and what the page draws ---------------------------------------------


def test_an_undo_answers_like_an_edit(client):
    """`song-changed` names the sections whose audio moved, and the regions
    come back by their watches: the arranger and the topbar (its buttons),
    the topbar as a morph so the pressed button keeps keyboard focus."""
    post(client, "/song/cell", {"section_id": "s2", "track_id": "t2", "pattern_id": ""})
    r = undo(client)
    assert changed(r) == ["s2"]  # the last section: no head after it follows
    assert {"song-arranger", "song-topbar"} <= oob_ids(r.text)
    assert 'id="song-topbar"' in r.text and 'hx-swap-oob="outerMorph"' in r.text
    r = redo(client)
    assert changed(r) == ["s2"]
    # a rename moves no audio
    post(client, "/song/section/s1/rename", {"name": "Opening"})
    assert changed(undo(client)) == []


def _topbar(client) -> str:
    html = client.get("/view/song", headers=sr.HX).text
    # the undo / redo group: after the transport, which is first in the bar
    return html[html.index('class="song-undo"') : html.index('class="song-meta"')]


OFF = 'aria-disabled="true"'


def test_the_topbar_buttons_say_what_they_would_move(client):
    bar = _topbar(client)
    # aria-disabled, never `disabled`: a button that disables itself under
    # the keyboard drops focus to <body> (tests/browser/test_song.py)
    assert bar.count(OFF) == 2 and " disabled" not in bar
    assert "Nothing to undo" in bar and "Nothing to redo" in bar
    post(client, "/song/cell", {**CELL, "pattern_id": ""})
    bar = _topbar(client)
    assert 'title="Undo: clear cell (Ctrl+Z)"' in bar
    assert 'aria-label="Undo: clear cell"' in bar
    assert bar.count(OFF) == 1 and "Nothing to redo" in bar
    undo(client)
    bar = _topbar(client)
    assert 'title="Redo: clear cell (Ctrl+Shift+Z)"' in bar
    assert bar.count(OFF) == 1 and "Nothing to undo" in bar
    assert 'hx-post="/song/undo"' in bar and 'hx-post="/song/redo"' in bar


def test_the_open_drawer_shows_the_restored_pattern(client):
    post(client, "/song/drawer", CELL)
    post(client, "/song/pattern/p1/tool/clear")
    assert song(client)["patterns"]["p1"]["cells"] == []
    r = undo(client)
    assert "song-drawer" in oob_ids(r.text)
    drawer = r.text[r.text.index('id="song-drawer"') :]
    assert 'name="cell.A1.0"' in drawer and 'name="cell.E2.4"' in drawer
    assert drawer.count(" checked") >= 2
    assert st(client).song_drawer == CELL


def test_the_drawer_closes_when_its_pattern_is_undone_away(client):
    post(client, "/song/pattern/p2/duplicate")  # opens the drawer on the copy
    made = st(client).song_drawer["pattern_id"]
    assert made in song(client)["patterns"]
    r = undo(client)
    assert made not in song(client)["patterns"]
    assert st(client).song_drawer is None
    drawer = r.text[r.text.index('id="song-drawer"') :]
    assert "hidden" in drawer[: drawer.index(">")]
    redo(client)  # the pattern is back; the drawer stays closed
    assert made in song(client)["patterns"] and st(client).song_drawer is None


def test_a_cells_drawer_stays_on_its_cell_across_an_undo(client):
    post(client, "/song/cell/new", EMPTY)  # fills the cell, opens its drawer
    undo(client)
    assert st(client).song_drawer == EMPTY  # the cell is still there, empty
    assert song(client)["sections"][0]["cells"].get("t2") is None


def test_clear_pattern_asks_nothing_because_undo_covers_it(client):
    post(client, "/song/drawer", CELL)
    html = client.get("/view/song", headers=sr.HX).text
    drawer = html[html.index('id="song-drawer"') :]
    clear = drawer[drawer.index("/tool/clear") - 80 :][:400]
    assert "hx-confirm" not in clear and "Ctrl+Z" in clear


# --- takes ---------------------------------------------------------------------------


def _record(client, take: str, midi: int = 45, pid: str = "p2"):
    events = [{"midi": midi, "t": 0.5, "vel": 0.7}]
    data = {"events": json.dumps(events), "take": take, "t0": "0", "t1": "2"}
    data |= {"section_id": "s2", "track_id": "t2"}
    return post(client, f"/song/pattern/{pid}/record", data)


def test_a_note_take_is_one_song_step_however_many_wraps_it_posts(client):
    before = raw(song(client))
    assert _record(client, "a", 45).status_code == 200
    assert _record(client, "a", 48).status_code == 200
    assert _record(client, "a", 52).status_code == 200
    assert st(client).song_undo_state()["depth"] == 1
    assert labels(client)[0] == "record take"
    undo(client)
    assert raw(song(client)) == before


def test_undoing_a_takes_step_retires_its_take_undo(client):
    """The take undo restores ITS pattern by id from before the take; once
    Ctrl+Z has rewound the song past the take, that would be a second undo
    of the same thing. The entry goes, the "↶ take" button with it."""
    _record(client, "a")
    post(client, "/song/section/s1/rename", {"name": "Opening"})
    _record(client, "b", 50)
    assert [h["take"] for h in st(client).take_history] == ["a", "b"]
    undo(client)  # take b
    assert [h["take"] for h in st(client).take_history] == ["a"]
    undo(client)  # the rename: take a's own undo still applies
    assert [h["take"] for h in st(client).take_history] == ["a"]
    undo(client)  # take a
    assert st(client).take_history == []
    refused(post(client, "/song/record/undo"), "No take")
    redo(client)  # the notes come back; the take's own undo does not
    assert st(client).take_history == []
    assert labels(client)[0] == "record take"


def test_the_take_undo_is_itself_a_song_step(client):
    before = raw(song(client))
    _record(client, "a")
    taken = raw(song(client))
    assert post(client, "/song/record/undo").status_code == 200
    assert raw(song(client)) == before
    assert labels(client)[0] == "undo take"
    undo(client)  # the take is back
    assert raw(song(client)) == taken
    undo(client)
    assert raw(song(client)) == before


def test_undoing_a_placed_clip_drops_the_reference_and_keeps_the_audio(client):
    data_dir = st(client).data_dir
    save_clip(data_dir, "loop", np.full(22050, 0.2))
    before = raw(song(client))
    assert post(client, "/song/section/s1/clip", {"clip": "loop"}).status_code == 200
    assert song(client)["sections"][0]["audio"][0]["clip"] == "loop"
    assert labels(client)[0] == "place clip"
    undo(client)
    assert raw(song(client)) == before
    assert (clip_dir(data_dir) / "loop.wav").is_file()  # the clip store keeps it
    redo(client)
    assert song(client)["sections"][0]["audio"][0]["clip"] == "loop"


def test_a_take_renamed_after_it_was_placed_undoes_and_redoes_by_its_new_name(client):
    save_clip(st(client).data_dir, "loop", np.full(22050, 0.2))
    post(client, "/song/section/s1/clip", {"clip": "loop"})
    post(client, "/song/section/s1/rename", {"name": "Opening"})
    r = post(client, "/song/take/loop/rename", {"name": "vox"})
    assert r.status_code == 200
    assert st(client).song_undo_state()["depth"] == 2  # the rename is no step
    undo(client)
    assert song(client)["sections"][0]["audio"][0]["clip"] == "vox"
    undo(client)
    assert song(client)["sections"][0]["audio"] == []
    redo(client)
    assert song(client)["sections"][0]["audio"][0]["clip"] == "vox"


# --- the other undo domains: Synths and Perform -------------------------------------


def _track(client, tid: str) -> dict:
    return next(t for t in song(client)["tracks"] if t["id"] == tid)


def test_a_track_sound_edited_on_synths_is_one_step_when_you_come_back(client):
    """Synths writes the track's sound into the song in place, tweak by
    tweak (its own undo ring is Synths'). Back on the Song page the whole
    visit is ONE song step."""
    post(client, "/song/cell", {**EMPTY, "pattern_id": "p3"})
    before = raw(song(client))
    was = _track(client, "t1")["patch"]["cutoff"]
    assert post(client, "/song/track/t1/edit-sound").status_code == 200
    for cutoff in ("900", "700", "500"):
        r = client.post("/fragment/patch", data={"cutoff": cutoff}, headers=EDITOR)
        assert r.status_code == 200
    assert _track(client, "t1")["patch"]["cutoff"] == 500.0
    # back on Song (any request from there ends the edit)
    assert labels(client) == ("sound of Bass", None)
    assert st(client).song_undo_state()["depth"] == 2
    r = undo(client)
    assert trigger(r)["studio-toast"] == {"message": "Undid: sound of Bass"}
    assert _track(client, "t1")["patch"]["cutoff"] == was
    assert raw(song(client)) == before
    redo(client)
    assert _track(client, "t1")["patch"]["cutoff"] == 500.0
    undo(client)
    undo(client)  # the cell before it
    assert labels(client) == (None, "set cell")


def test_a_sound_visit_that_changed_nothing_is_no_step(client):
    post(client, "/song/track/t1/edit-sound")
    client.get("/view/editor", headers=EDITOR)
    assert labels(client) == (None, None)
    # and an undo of something else is not mistaken for a sound edit
    post(client, "/song/track/t1/mix", {"gain": "0.4"})
    undo(client)
    assert labels(client) == (None, "track level")


def test_open_in_perform_is_no_step_and_adding_the_loop_back_is_one(client):
    before = raw(song(client))
    r = post(client, "/song/section/s1/to-loop")
    assert r.status_code == 200 and "HX-Location" in r.headers
    assert raw(song(client)) == before and labels(client) == (None, None)
    post(client, "/song/from-loop", {"name": "Take two"})
    assert len(song(client)["sections"]) == 3
    assert labels(client) == ("add loop to song", None)
    undo(client)
    assert raw(song(client)) == before


def test_a_reset_of_the_song_scope_through_its_route_empties_the_ring(client):
    st(client).mark_baseline("song", "start")
    post(client, "/song/cell", {**CELL, "pattern_id": ""})
    post(client, "/song/section/s1/rename", {"name": "Opening"})
    undo(client)
    r = post(client, "/fragment/reset", {"scope": "song", "mode": "revert"})
    assert r.status_code == 200
    assert labels(client) == (None, None)
    refused(undo(client), "Nothing to undo")


# --- the keys (static/songedit.js's own code, under node) ---------------------------

SONGEDIT = Path("synthlab/studio/static/songedit.js")

HARNESS = """
globalThis.window = { synthlab: {} };
const fs = require("fs");
// eval, deliberately: songedit.js is a classic script (an IIFE merging onto
// window.synthlab). With no `document` it publishes the pure key rule and
// wires nothing, so the browser's own file runs here.
eval(fs.readFileSync(process.argv[2], "utf8"));
const key = window.synthlab.songEdit.key;
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = cases.map(([e, typing, busy]) => key(e, typing, busy));
process.stdout.write(JSON.stringify(out));
"""

Z, Y = {"key": "z", "ctrlKey": True}, {"key": "y", "ctrlKey": True}
KEYS = [
    # (event, typing in a text field, a pointer down / a drag under way) -> button
    ((Z, False, False), "song-undo"),
    (({"key": "Z", "ctrlKey": True, "shiftKey": True}, False, False), "song-redo"),
    ((Y, False, False), "song-redo"),
    (({"key": "z", "metaKey": True}, False, False), "song-undo"),  # Cmd on a Mac
    (({"key": "Z", "metaKey": True, "shiftKey": True}, False, False), "song-redo"),
    # not ours: left for the piano's octave key, the browser, the text field
    (({"key": "z"}, False, False), ""),
    (({"key": "z", "ctrlKey": True, "altKey": True}, False, False), ""),
    (({"key": "y", "ctrlKey": True, "shiftKey": True}, False, False), ""),
    (({"key": "x", "ctrlKey": True}, False, False), ""),
    ((Z, True, False), ""),  # typing: the field's own undo
    ((Y, True, False), ""),
    # ours, but not now: swallowed, no step taken
    ((Z, False, True), "wait"),  # mid-drag
    (({**Z, "repeat": True}, False, False), "wait"),  # a held key
    (({**Y, "repeat": True}, False, True), "wait"),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs songedit.js")
def test_the_undo_keys_under_node(tmp_path):
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([case for case, _want in KEYS]), encoding="utf-8")
    out = subprocess.run(
        ["node", str(tmp_path / "harness.js"), str(SONGEDIT), str(cases)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout) == [want for _case, want in KEYS]
