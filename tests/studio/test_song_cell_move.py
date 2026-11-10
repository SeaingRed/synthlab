"""Dragging a block onto another cell (studio docs/song.md, "Drags onto a
lane"): a plain drop MOVES it, `POST /song/cell/move` — the destination
plays the pattern and the source is cleared in ONE commit, so one Ctrl+Z
takes back both; with Ctrl or Alt it copies the pattern by reference
through the existing `POST /song/cell`. (For its first week the rule was
the other way round.) Along a track the cell's automation lanes go with
it; across tracks they are left behind. The pointer side is
`static/songdrag.js` (tests/browser/test_song.py drives it)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synthlab.studio import schema
from synthlab.studio import song_model as sm
from synthlab.studio.fxrack import clean_chain
from tests.studio import test_song_routes as sr
from tests.studio.test_song_routes import (
    HX,
    changed,
    oob_ids,
    post,
    refused,
    song,
    song_dict,
    trigger,
)
from tests.studio.test_song_undo import labels, redo, undo

client = sr.client  # Intro (s1): td, t1 · Verse (s2): td, t1, t2 (the 2-bar Hook)
N = schema.nodes()
RISE = [[0.0, 0.0], [1.0, 1.0]]


def _cells(c) -> dict:
    return {sec["id"]: dict(sec["cells"]) for sec in song(c)["sections"]}


def _move(c, src, dst, **more):
    data = {
        "from_section": src[0],
        "from_track": src[1],
        "section_id": dst[0],
        "track_id": dst[1],
    }
    return post(c, "/song/cell/move", data | more)


# --- the model ---------------------------------------------------------------------


def _song() -> dict:
    return schema.conform(song_dict(), N.SONG)


def test_a_move_points_the_destination_at_the_pattern_and_clears_the_source():
    s = _song()
    assert sm.move_cell(s, "s2", "t2", "s1", "t2") is True
    assert s["sections"][0]["cells"]["t2"] == "p2"
    assert s["sections"][1]["cells"]["t2"] is None
    assert "p2" in s["patterns"]  # the pattern itself never moves
    assert schema.conform(s, N.SONG) == s
    # onto a filled cell: it plays the moved pattern; what it played stays
    # in the pool
    s = _song()
    sm.move_cell(s, "s2", "t2", "s2", "t1")
    assert s["sections"][1]["cells"] == {"td": "pd", "t1": "p2", "t2": None}
    assert "p1" in s["patterns"]
    # onto itself: nothing
    s = _song()
    before = json.dumps(s)
    assert sm.move_cell(s, "s1", "t1", "s1", "t1") is False
    assert json.dumps(s) == before


def test_a_moved_pattern_longer_than_its_new_section_grows_it():
    s = _song()
    s["sections"][0]["bars"] = 1
    sm.move_cell(s, "s2", "t2", "s1", "t2")  # the 2-bar Hook
    assert s["sections"][0]["bars"] == 2


@pytest.mark.parametrize(
    ("args", "word"),
    [
        (("s1", "t2", "s2", "t2"), "empty"),  # nothing to move
        (("s1", "t1", "s2", "td"), "can't go on"),  # notes onto drums
        (("s1", "td", "s2", "t1"), "can't go on"),
        (("s1", "t1", "s2", "ta"), "takes"),  # an audio track
        (("zz", "t1", "s2", "t1"), "section"),
        (("s1", "t1", "zz", "t1"), "section"),
        (("s1", "zz", "s2", "t1"), "track"),
        (("s1", "t1", "s2", "zz"), "track"),
        ((None, None, None, None), "section"),
    ],
)
def test_a_move_that_cannot_be_made_raises_and_changes_nothing(args, word):
    s = _song()
    before = json.dumps(s)
    with pytest.raises(sm.SongError, match=word):
        sm.move_cell(s, *args)
    assert json.dumps(schema.conform(s, N.SONG)) == before


def test_lanes_travel_along_a_track_and_stay_behind_across_tracks():
    """A lane is its (section, track)'s: its targets are that track's sound
    and FX units. Along the track they go with the cell, in place of the
    destination's own; to another track they are dropped with the source."""
    s = _song()
    s["tracks"][1]["fx"] = clean_chain([{"kind": "reverb", "params": {}}])
    fx = sm.claim_fx_target(s["tracks"][1], "fx:@0:reverb:mix")
    sm.set_lane(s, "s1", "t1", "cutoff", RISE)
    sm.set_lane(s, "s1", "t1", fx, RISE)
    sm.set_lane(s, "s2", "t1", "gain", [[0.0, 0.2], [1.0, 0.5]])
    lanes = json.loads(json.dumps(sm.cell_lanes(s["sections"][0], "t1")))
    along = json.loads(json.dumps(s))
    sm.move_cell(along, "s1", "t1", "s2", "t1")
    assert "auto" not in along["sections"][0]
    assert along["sections"][1]["auto"] == {"t1": lanes}  # not s2's old level lane
    assert schema.conform(along, N.SONG) == along
    assert set(sm.section_lanes(along, along["sections"][1])) == {"t1"}
    # across tracks: the destination keeps its own lanes, the source's go
    across = json.loads(json.dumps(s))
    sm.set_lane(across, "s2", "t2", "resonance", RISE)
    sm.move_cell(across, "s1", "t1", "s2", "t2")
    assert "auto" not in across["sections"][0]
    assert across["sections"][1]["auto"] == {
        "t1": [{"target": "gain", "points": [[0.0, 0.2], [1.0, 0.5]]}],
        "t2": [{"target": "resonance", "points": RISE}],
    }
    # a copy (set_cell) carries none
    copied = json.loads(json.dumps(s))
    sm.set_cell(copied, "s2", "t2", "p1")
    assert "t2" not in copied["sections"][1]["auto"]
    assert copied["sections"][0]["auto"] == s["sections"][0]["auto"]


# --- the route ---------------------------------------------------------------------


def test_the_route_moves_a_cell_and_redraws_both(client):
    r = _move(client, ("s2", "t2"), ("s1", "t2"))
    assert r.status_code == 200, r.text[:300]
    assert _cells(client) == {
        "s1": {"td": "pd", "t1": "p1", "t2": "p2"},
        "s2": {"td": "pd", "t1": "p1", "t2": None},
    }
    assert changed(r) == ["s1", "s2"]
    assert "song-arranger" in oob_ids(r.text)
    html = client.get("/view/song", headers=HX).text
    cell = html[html.index('id="cell-s1-t2"') :].split("</td>")[0]
    assert 'data-pattern-id="p2"' in cell and 'draggable="true"' in cell
    gone = html[html.index('id="cell-s2-t2"') :].split("</td>")[0]
    assert 'data-pattern-id=""' in gone and "draggable" not in gone


def test_a_move_is_one_undo_step(client):
    """What the route is for: a copy and a clear were two posts, and two
    steps. One Ctrl+Z puts the pattern back where it was."""
    before = json.dumps(song(client))
    _move(client, ("s2", "t2"), ("s1", "t2"))
    moved = json.dumps(song(client))
    assert labels(client) == ("move cell", None)
    r = undo(client)
    assert r.status_code == 200 and json.dumps(song(client)) == before
    assert trigger(r)["studio-toast"]["message"] == "Undid: move cell"
    redo(client)
    assert json.dumps(song(client)) == moved
    undo(client)
    assert undo(client).status_code == 422  # ... and it was the only step


def test_a_move_grows_the_section_and_says_so(client):
    client.app.state.studio.song["sections"][0]["bars"] = 1
    r = _move(client, ("s2", "t2"), ("s1", "t2"))
    assert "Intro grew to 2 bars" in trigger(r)["studio-toast"]["message"]


def test_the_open_drawer_follows_a_moved_cell(client):
    s = client.app.state.studio
    post(client, "/song/drawer", {"section_id": "s2", "track_id": "t2"})
    r = _move(client, ("s2", "t2"), ("s1", "t2"))
    assert s.song_drawer == {"section_id": "s1", "track_id": "t2"}
    assert "song-drawer" in oob_ids(r.text)
    # a drawer on another cell stays where it is
    post(client, "/song/drawer", {"section_id": "s1", "track_id": "t1"})
    _move(client, ("s1", "t2"), ("s2", "t2"))
    assert s.song_drawer == {"section_id": "s1", "track_id": "t1"}


@pytest.mark.parametrize(
    ("src", "dst", "word"),
    [
        (("s1", "t2"), ("s2", "t2"), "empty"),
        (("s1", "t1"), ("s2", "td"), "go on a drums track"),
        (("s1", "t1"), ("s2", "ta"), "takes"),
        (("zz", "t1"), ("s2", "t1"), "No such section"),
        (("s1", "t1"), ("s2", "zz"), "No such track"),
        (("", ""), ("", ""), "No such section"),
    ],
)
def test_a_refused_move_is_a_toast_no_step_and_no_change(client, src, dst, word):
    before = json.dumps(song(client))
    refused(_move(client, src, dst), word)
    assert json.dumps(song(client)) == before and labels(client) == (None, None)


def test_a_move_onto_itself_and_a_post_with_no_fields_change_nothing(client):
    before = json.dumps(song(client))
    r = _move(client, ("s1", "t1"), ("s1", "t1"))
    assert r.status_code == 200 and changed(r) == []
    refused(post(client, "/song/cell/move"), "No such section")
    assert json.dumps(song(client)) == before and labels(client) == (None, None)


def test_a_move_ignores_the_fields_the_page_sends_along(client):
    """`htmx.ajax` posts every named input on the page: the drawer's
    `pattern_id` select and rename box ride with the drop."""
    r = _move(client, ("s2", "t2"), ("s1", "t2"), pattern_id="p3", name="x", op="add")
    assert r.status_code == 200
    assert song(client)["sections"][0]["cells"]["t2"] == "p2"
    assert song(client)["patterns"]["p2"]["name"] == "Hook"


def test_a_move_that_would_break_the_length_cap_is_refused(client, monkeypatch):
    """Lanes travel along a track, and a lane makes its section one pass:
    a destination too long to render as one is refused, like any edit that
    takes the song past its caps."""
    s = client.app.state.studio
    sm.set_lane(s.song, "s1", "t1", "cutoff", RISE)
    s.song["sections"][1].update(bars=64, repeats=1)  # 128 s as one pass
    before = json.dumps(song(client))
    refused(_move(client, ("s1", "t1"), ("s2", "t1")))
    assert json.dumps(song(client)) == before


# --- the island --------------------------------------------------------------------


def test_the_drag_island_moves_by_default_and_copies_with_ctrl_or_alt():
    js = Path("synthlab/studio/static/songdrag.js").read_text(encoding="utf-8")
    assert '"/song/cell/move"' in js and '"/song/cell"' in js
    assert "from_section" in js and "from_track" in js
    # the one predicate: a drag moves, Ctrl or Alt copies (it was the other
    # way round, with Shift, for a week)
    assert "const copies = (e) => e.ctrlKey || e.altKey;" in js
    # Shift is the RANGE's modifier since the playlist tools (songsel.js):
    # it is read here once, where a drag stands down for it — and for the
    # range mode a finger or a pen selects in
    assert js.count("shiftKey") == 1
    stand_down = js[js.index("e.shiftKey") :].split("\n")[0]
    assert '".song-view.selecting"' in stand_down and "preventDefault()" in stand_down
    # it acts only on a drag it saw start: every handler asks for `from`
    for event in ("dragover", "drop"):
        handler = js[js.index(f'addEventListener("{event}"') :].split("});")[0]
        assert "from && plan(e)" in handler, event
    assert "getData(" not in js  # never dataTransfer text from outside the page
    # the pool's drag lives here now, not in song.js
    assert '".pool-pattern"' in js
    song = Path("synthlab/studio/static/song.js").read_text(encoding="utf-8")
    assert "dragstart" not in song and "dataTransfer" not in song
    page = Path("synthlab/studio/templates/base.html").read_text(encoding="utf-8")
    assert page.index("songcell.js") < page.index("songdrag.js")


def test_the_drag_island_is_every_drop_on_a_lane():
    """A pool pattern, a block, a library clip, a placed take, a whole
    stem's strip and a stem of the library: each a row in one table, each a
    post through an existing or a new route. Posts go out from an element
    with no field under it — htmx.ajax sweeps in every named input beneath
    its source, and `/song/long` would read one as the stem's new name."""
    js = Path("synthlab/studio/static/songdrag.js").read_text(encoding="utf-8")
    for selector in (
        ".pool-pattern",
        ".take-chip",
        ".long-part",
        ".arr-cell.filled",
        ".lp-row[data-drag-loop]",  # a drawer loop (/loop/song)
        ".cd-clip",
        ".cd-stem",
    ):
        assert f'["{selector}", (el)' in js, selector
    for path in ("/clip`", '"/song/take/edit"', '"/song/long"'):
        assert path in js, path
    assert 'op: "move"' in js and 'op: "set"' in js and 'op: "add"' in js
    # one post, from the empty #post-from (audio.js's synthlab.post)
    assert js.count("S().post(") == 1 and "htmx.ajax(" not in js
    # the drop preview, and the drawer that lets a drag through
    assert '"drop-at"' in js and '"dragging-out"' in js
    assert "requestAnimationFrame" in js  # after the drag image is taken
    # 175: the drawer's loops and the song's questions take room;
    # test_island.py holds the island to it
    assert "Budget: <= 175 code lines" in js
    css = Path("synthlab/studio/static/style.css").read_text(encoding="utf-8")
    assert ".clip-library.dragging-out .clip-drawer { opacity: 0.25;" in css
    assert ".drop-at {" in css and ".dragging-audio .arr-new-audio" in css
