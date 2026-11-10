"""Perform's launcher: slots and scenes (studio `docs/loop-and-perform.md`,
"Slots and scenes").

The slots compile to the working loop (`StudioState.launch`): a launch is
one pattern undo step holding the whole loop, a launched slot is the loop
open on its lane (`loop_edit`, one a lane) and an edit there saves to its
loop — a lane at a time, only the lanes that moved. `perform_slots` is
persisted, absent while empty, and in the "Sequence" reset scope."""

from __future__ import annotations

import io
import json
import os
import wave
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import loops, schema
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement, StudioState
from synthlab.synth import Patch

DRUMS = 4


def _hx(view: str = "perform") -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _st(client) -> StudioState:
    return client.app.state.studio


def _post(client, path: str, **data):
    return client.post(f"/fragment/perform/{path}", data=data, headers=_hx())


def _loop(
    s: StudioState, name: str, cells, *, bars=1, spb=16, sound="Acid", **kw
) -> str:
    arr = replace(s.arrangement, bars=bars, steps_per_bar=spb, cells=cells, **kw)
    pay = {
        "format": 2,
        "patch": Patch(name=sound, cutoff=1234.0).to_json(),
        "layers": [],
        "main_fx": [],
        "curves": {},
    }
    return loops.save(s.data_dir, name, arr, pay)


def _beat(s: StudioState, name: str = "techno") -> str:
    return loops.save_beat(s.data_dir, name, s.drum_bank[0], s.arrangement)


def _cells(cells) -> list[tuple[str, int]]:
    return sorted((p, int(st)) for p, st in cells)


def _queued(r) -> None:
    assert r.status_code == 200, r.text
    assert json.loads(r.headers["HX-Trigger"])["preview-ready"]["queue"] is True


# --- the grid ----------------------------------------------------------------------


def test_a_loop_dropped_on_a_slot_holds_it_and_the_grid_is_saved_absent_when_empty(
    client, tmp_path
):
    s = _st(client)
    assert s.perform_slots is None and "perform_slots" not in s._payload()
    _loop(s, "hook", [["A2", 0]])
    r = _post(client, "slot/place", lane="1", row="2", name="hook")
    assert r.status_code == 200
    assert s.slot_at(1, 2) == "hook" and len(s.perform_slots) == 3
    s.save()
    again = StudioState(tmp_path)
    assert again.slot_at(1, 2) == "hook"
    # a beat goes in the drums, notes in a lane of notes
    _beat(s)
    r = _post(client, "slot/place", lane="0", row="0", name="techno")
    assert r.status_code == 422 and "drums" in r.text
    assert (
        _post(client, "slot/place", lane="4", row="0", name="techno").status_code == 200
    )
    # emptied, the trailing rows go and so does the key
    for lane, row in ((1, 2), (DRUMS, 0)):
        _post(client, "slot/clear", lane=str(lane), row=str(row))
    assert s.perform_slots is None and "perform_slots" not in s._payload()


def test_the_launcher_draws_lanes_scenes_and_slots(client):
    s = _st(client)
    _loop(s, "hook", [["A2", 0]])
    _post(client, "slot/place", lane="0", row="0", name="hook")
    html = client.get("/view/perform").text
    assert 'id="perform-slots"' in html and 'data-module="perform.slots"' in html
    assert 'id="slots"' in html and html.count('class="sl-row') == 1 + 4 + 1
    assert 'data-cmd="Launch scene 1"' in html and 'data-keys="Alt+1"' in html
    assert 'data-cmd="Stop all"' in html and 'data-keys="Alt+0"' in html
    assert 'hx-post="/fragment/perform/slot/launch"' in html and ">hook<" in html
    # the names: the scenes' capture is "Scene → Song"
    # (⟲ Capture is the keys'), and the clip drawer's button "Clips"
    assert ">Scene → Song</button>" in html and "⟲ Capture → Song" not in html
    assert 'data-cmd="Scene → Song: the scenes you launched, as sections"' in html
    assert '<span class="cb-label">Clips <span class="loop-count">' in html
    assert (
        "Drawer" not in html.split('id="clip-library"', 1)[1].split("</button>", 1)[0]
    )


def test_a_filled_slots_click_selects_and_its_own_play_button_launches(client):
    """Deliberately, a filled slot's own button carries no
    post (slots.js selects it: `/fragment/perform/slot/select`); its ▶
    (`.sl-go`, which keeps `select`) launches — so do a double-click and
    Enter on the selected slot (slots.js, tests/browser/test_slots.py)."""
    s = _st(client)
    _loop(s, "hook", [["A2", 0]])
    s.add_layer(replace(s.patch, name="Pad"), select=False)
    _post(client, "slot/place", lane="1", row="0", name="hook")
    html = client.get("/view/perform").text
    slot = html.split('class="sl-slot', 2)[1].split(">", 1)[0]
    assert "hx-post" not in slot and 'data-menu="Select the slot"' in slot
    go = html.split('class="sl-go"', 1)[1].split(">", 1)[0]
    assert 'hx-post="/fragment/perform/slot/launch"' in go and '"select": "1"' in go
    assert 'data-menu="Launch"' in go
    # the click's post: its lane edited, nothing launched, no undo step
    steps = len(s._pattern_undo)
    r = _post(client, "slot/select", lane="1", row="0")
    assert r.status_code == 200 and s.active_layer == 1
    assert not s.loop_edit and len(s._pattern_undo) == steps
    assert s.slot_playing(1) is None
    _queued(_post(client, "slot/launch", lane="1", row="0", select="1"))
    assert s.slot_playing(1) == 0


def test_ctrl_z_puts_a_slot_back_and_touches_nothing_that_plays(client):
    s = _st(client)
    _loop(s, "hook", [["A2", 0]])
    cells = list(s.arrangement.cells)
    _post(client, "slot/place", lane="0", row="0", name="hook")
    _post(client, "slot/move", lane="0", row="0", to_lane="2", to_row="1")
    assert s.slot_at(0, 0) is None and s.slot_at(2, 1) == "hook"
    client.post("/fragment/pattern/undo", headers=_hx())
    assert s.slot_at(0, 0) == "hook" and s.slot_at(2, 1) is None
    client.post("/fragment/pattern/undo", headers=_hx())
    assert s.perform_slots is None and s.arrangement.cells == cells


def test_a_renamed_or_deleted_loop_follows_into_its_slots(client):
    s = _st(client)
    _loop(s, "hook", [["A2", 0]])
    _post(client, "slot/place", lane="0", row="0", name="hook")
    client.post("/loop/rename", data={"name": "hook", "new": "the hook"}, headers=_hx())
    assert s.slot_at(0, 0) == "the hook"
    client.post("/loop/delete", data={"name": "the hook"}, headers=_hx())
    assert s.slot_at(0, 0) is None


# --- launching ---------------------------------------------------------------------


def test_a_slot_launches_its_loop_on_its_lane_and_one_undo_puts_it_back(client):
    s = _st(client)
    s.arrangement = replace(s.arrangement, bars=1, steps_per_bar=16, cells=[["A1", 0]])
    _loop(s, "lead", [["C3", 2], ["E3", 6]], sound="Lead")
    _post(client, "slot/place", lane="1", row="0", name="lead")
    before = (list(s.arrangement.cells), json.dumps(s.layers, sort_keys=True))
    _queued(_post(client, "slot/launch", lane="1", row="0"))
    lane = s.layers[1]
    assert _cells(lane["cells"]) == [("C3", 2), ("E3", 6)]
    assert lane["patch"]["name"] == "Lead" and not lane.get("muted")
    assert s.arrangement.cells == [["A1", 0]]  # the edited lane: untouched
    assert s.loop_edit_of(1)["row"] == 0 and s.slot_playing(1) == 0
    html = client.get("/view/perform").text
    assert html.count('data-state="playing"') >= 1
    client.post("/fragment/pattern/undo", headers=_hx())
    assert (list(s.arrangement.cells), json.dumps(s.layers, sort_keys=True)) == before
    assert not s.loop_edit


def test_a_scene_is_one_launch_one_render_and_one_undo_and_an_empty_slot_stops(client):
    s = _st(client)
    s.arrangement = replace(s.arrangement, bars=1, steps_per_bar=16, cells=[["A1", 0]])
    s.add_layer(replace(s.patch, name="Pad"), select=False)
    s.set_follow(1, None)
    s.layers[1]["cells"] = [["E2", 0]]
    _loop(s, "lead", [["C3", 2]], sound="Lead")
    _beat(s)
    for lane, name in ((0, "lead"), (DRUMS, "techno")):
        _post(client, "slot/place", lane=str(lane), row="1", name=name)
    ring = len(s._pattern_undo)
    renders = []
    real = client.app.state.svc.render

    def counted(*a, **kw):
        renders.append(a)
        return real(*a, **kw)

    client.app.state.svc.render = counted
    try:
        _queued(_post(client, "scene/launch", row="1"))
    finally:
        client.app.state.svc.render = real
    assert len(renders) == 1  # one render for the scene, not one a lane
    assert len(s._pattern_undo) == ring + 1
    assert _cells(s.arrangement.cells) == [("C3", 2)]
    assert s.layers[1]["muted"] is True  # its slot in the scene is empty
    assert s.arrangement.drums and "drums" in s.perform_parts
    assert {k: e["name"] for k, e in s.loop_edit.items()} == {
        0: "lead",
        DRUMS: "techno",
    }
    client.post("/fragment/pattern/undo", headers=_hx())
    assert s.arrangement.cells == [["A1", 0]] and not s.layers[1].get("muted")
    assert not s.loop_edit


def test_the_playing_slot_is_open_and_an_edit_saves_only_the_lane_that_moved(client):
    s = _st(client)
    s.arrangement = replace(s.arrangement, bars=1, steps_per_bar=16, cells=[])
    s.add_layer(replace(s.patch, name="Pad"), select=False)
    s.set_follow(1, None)
    _loop(s, "lead", [["C3", 2]])
    _loop(s, "pad", [["E3", 0]], sound="Pad")  # (in the window C3 opened)
    _post(client, "slot/place", lane="0", row="0", name="lead")
    _post(client, "slot/place", lane="1", row="0", name="pad")
    _queued(_post(client, "scene/launch", row="0"))
    pad = loops._path(s.data_dir, "pad")
    os.utime(pad, (1_000_000, 1_000_000))
    # an edit on the edited lane (lane 1): its loop is written, lane 2's not
    client.post("/fragment/pattern/notes/shift_right", headers=_hx())
    assert _cells(loops.load(s.data_dir, "lead")["lane"]["cells"]) == [("C3", 3)]
    assert loops.load(s.data_dir, "lead")["edited"] is True
    assert pad.stat().st_mtime == 1_000_000
    # the other lane edited on screen: its own loop follows
    client.post("/fragment/layer/select", data={"index": "1"}, headers=_hx())
    client.post("/fragment/pattern/notes/shift_right", headers=_hx())
    assert _cells(loops.load(s.data_dir, "pad")["lane"]["cells"]) == [("E3", 1)]


def test_a_stopped_lane_is_muted_until_a_slot_plays_it_again(client):
    s = _st(client)
    _loop(s, "lead", [["C3", 2]])
    _post(client, "slot/place", lane="0", row="0", name="lead")
    _queued(_post(client, "slot/launch", lane="0", row="0"))
    _queued(_post(client, "slot/stop", lane="0"))
    assert s.layers[0]["muted"] is True and s.loop_edit_of(0) is None
    _queued(_post(client, "slot/stop", lane="4"))
    assert s.perform_parts == ["synth"]
    assert _post(client, "slot/stop", lane="4").status_code == 422  # nothing plays
    _queued(_post(client, "slot/launch", lane="0", row="0"))
    assert not s.layers[0]["muted"]


def test_the_lanes_share_one_grid_the_longest_and_finest_of_what_plays(client):
    """A 4-bar loop on a 1-bar grid grows it, and the lane that keeps
    playing repeats; a 4-step grid takes a 16-step loop's sixteenths, every
    lane regridded (nothing lost); a 1-bar slot on the 4-bar grid repeats,
    and an edit inside its first bar saves its one bar."""
    s = _st(client)
    s.arrangement = replace(s.arrangement, bars=1, steps_per_bar=4, cells=[["A1", 0]])
    _loop(s, "long", [["C3", 0], ["C3", 60]], bars=4)
    _loop(s, "short", [["E3", 4]], bars=1)
    _post(client, "slot/place", lane="1", row="0", name="long")
    _queued(_post(client, "slot/launch", lane="1", row="0"))
    a = s.arrangement
    assert (a.bars, a.steps_per_bar) == (4, 16)
    # lane 1 (no slot) kept playing: its one note, regridded, then repeated
    assert _cells(a.cells) == [("A1", 0), ("A1", 1), ("A1", 2), ("A1", 3)] + [
        ("A1", b * 16 + i) for b in (1, 2, 3) for i in range(4)
    ]
    _post(client, "slot/place", lane="0", row="1", name="short")
    _queued(_post(client, "slot/launch", lane="0", row="1"))
    assert _cells(s.arrangement.cells) == [("E3", 4 + 16 * b) for b in range(4)]
    client.post("/fragment/pattern/notes/shift_right", headers=_hx())
    lane = loops.load(s.data_dir, "short")["lane"]
    assert lane["bars"] == 1 and _cells(lane["cells"]) == [("E3", 5)]


def test_a_beat_slot_plays_on_the_drums_and_its_edits_save_to_the_beat(client):
    s = _st(client)
    _beat(s)
    _post(client, "slot/place", lane="4", row="0", name="techno")
    _queued(_post(client, "slot/launch", lane="4", row="0"))
    assert s.loop_edit_of(DRUMS)["name"] == "techno"
    s.arrangement = replace(s.arrangement, drums=[*s.arrangement.drums, ["clap", 3]])
    assert s.mirror_loop_edit()
    beat = loops.load(s.data_dir, "techno")["beat"]
    assert ["clap", 3] in beat["steps"] and beat["name"] == s.drum_bank[0]["name"]


def test_an_empty_slots_plus_keeps_what_its_lane_plays(client):
    s = _st(client)
    s.arrangement = replace(s.arrangement, cells=[["A1", 0], ["C2", 2]])
    r = _post(client, "slot/keep", lane="0", row="0")
    assert r.status_code == 200
    name = s.slot_at(0, 0)
    assert name and _cells(loops.load(s.data_dir, name)["lane"]["cells"]) == [
        ("A1", 0),
        ("C2", 2),
    ]
    assert s.slot_playing(0) == 0  # it is what plays: open on its lane
    r = _post(client, "slot/keep", lane="2", row="0")
    assert r.status_code == 422 and "no notes" in r.text


def test_a_launch_is_refused_whole_another_meter_or_kind(client):
    s = _st(client)
    _loop(s, "waltz", [["C3", 0]], spb=12, meter="3/4")
    _post(client, "slot/place", lane="0", row="0", name="waltz")
    cells = list(s.arrangement.cells)
    r = _post(client, "slot/launch", lane="0", row="0")
    assert r.status_code == 422 and "3/4" in r.text
    assert s.arrangement.cells == cells and not s.loop_edit


def test_an_unlaunched_launcher_moves_no_render_key(client):
    s = _st(client)
    svc = client.app.state.svc
    key = svc.render_inputs("perform").key()
    _loop(s, "lead", [["C3", 2]])
    _post(client, "slot/place", lane="0", row="0", name="lead")
    _post(client, "slot/place", lane="1", row="3", name="lead")
    assert svc.render_inputs("perform").key() == key


def test_the_slots_are_in_the_sequence_scope_and_revert_with_it(client):
    s = _st(client)
    s.mark_baseline("sequence", "start")
    assert "state.perform_slots" not in s.baselines["sequence"]["data"]
    _loop(s, "lead", [["C3", 2]])
    _post(client, "slot/place", lane="0", row="0", name="lead")
    assert s.is_dirty("sequence")
    assert s.revert("sequence") and s.perform_slots is None


def test_an_open_loop_is_named_as_the_loops_list_names_it_never_by_its_clock(client):
    """The edit banner must not say "editing the loop
    'keys-20261106-005322'" while the Loops panel's row says "Wide Unison
    Stab riff · …". Every place that names an open loop — the
    banner, the launcher's slot, the toasts — uses the lists' name."""
    s = _st(client)
    clock = _loop(
        s, "keys-20261106-005322", [["C3", 2], ["E3", 6]], sound="Wide Unison Stab"
    )
    shown = client.app.state.svc.loops_context("perform", bank=True)["rows"][0]["title"]
    assert shown.startswith("Wide Unison Stab riff") and shown != clock
    r = client.post("/loop/edit", data={"name": clock}, headers=_hx())
    assert shown in r.text and f"loop “{clock}”" not in r.text
    assert shown in json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    _post(client, "slot/place", lane="1", row="0", name=clock)
    html = client.get("/view/perform").text
    assert f">{shown}<" in html and f">{clock}<" not in html
    said = json.loads(
        _post(client, "slot/launch", lane="1", row="0").headers["HX-Trigger"]
    )
    assert shown in said["studio-toast"]["message"]


# --- ideas and sections ----------------------------------------------------------


def test_a_scene_kept_is_an_idea_and_an_idea_opens_as_a_scene(client):
    s = _st(client)
    _loop(s, "lead", [["C3", 2]], bpm=101.0)
    _beat(s)
    _post(client, "slot/place", lane="0", row="0", name="lead")
    _post(client, "slot/place", lane="4", row="0", name="techno")
    r = _post(client, "scene/keep", row="0", new="verse")
    assert r.status_code == 200
    idea = loops.load(s.data_dir, "verse")
    assert idea["kind"] == "idea" and idea["idea"] == [
        "lead",
        None,
        None,
        None,
        "techno",
    ]
    raw = json.loads(loops._path(s.data_dir, "verse").read_text(encoding="utf-8"))
    assert raw["kind"] == "idea" and "beat" not in raw
    panel = client.get("/view/perform").text
    assert (
        'data-what="idea"' in panel
        and 'hx-post="/fragment/perform/scene/idea"' in panel
    )
    # the drawer (every page) lists loops and beats, not ideas
    assert "verse" not in [
        r["name"] for r in client.app.state.svc.loops_context("editor")["rows"]
    ]
    s.arrangement = replace(s.arrangement, bpm=90.0)
    _queued(_post(client, "scene/idea", name="verse"))
    assert s.slot_rows()[1] == {"slots": idea["idea"], "name": "verse"}
    assert s.arrangement.bpm == idea["key"]["bpm"] and s.slot_playing(0) == 1
    client.post("/fragment/pattern/undo", headers=_hx())
    assert s.arrangement.bpm == 90.0 and len(s.slot_rows()) == 1


def test_a_song_section_opens_as_a_scene_of_new_loops_and_ctrl_z_takes_it_back(client):
    s = _st(client)
    s.arrangement = replace(
        s.arrangement, bars=1, steps_per_bar=16, cells=[["A1", 0]], drums=[["kick", 0]]
    )
    sid = s.loop_to_section("Verse")
    s.arrangement = replace(s.arrangement, cells=[["G2", 3]], drums=[], bpm=133.0)
    before = (list(s.arrangement.cells), s.arrangement.bpm)
    _queued(_post(client, "scene/section", section=sid))
    row = s.slot_rows()[0]
    assert row["name"] == "Verse" and row["slots"][0] and row["slots"][DRUMS]
    assert _cells(s.arrangement.cells) == [("A1", 0)] and s.arrangement.drums
    assert loops.load(s.data_dir, row["slots"][DRUMS])["kind"] == "beat"
    # opened again: the same loops, not copies
    client.post("/fragment/pattern/undo", headers=_hx())
    assert (list(s.arrangement.cells), s.arrangement.bpm) == before
    assert s.perform_slots is None
    n = len(loops.listing(s.data_dir))
    _queued(_post(client, "scene/section", section=sid))
    assert len(loops.listing(s.data_dir)) == n


def test_the_schema_keeps_a_launcher_and_an_idea_honest():
    n = schema.nodes()
    node = n.PAYLOAD.fields["perform_slots"]
    # a row of another width is no row of these lanes: its slots are empty
    rows = schema.conform([{"slots": ["a"] * 6, "name": "v"}], node)
    assert rows == [{"slots": [None] * 5, "name": "v"}]
    # a name the loop store could not hold is cleaned; nothing is no name
    rows = schema.conform([{"slots": ["a", "", None, 7, "../x"]}], node)
    assert rows[0]["slots"][:3] == ["a", None, None] and "/" not in rows[0]["slots"][4]
    loop = schema.conform({"v": 1, "name": "i", "arr": {}, "idea": [None] * 5}, n.LOOP)
    assert "idea" not in loop and "kind" not in loop  # a scene of nothing is no idea


def test_rows_of_nothing_are_no_launcher_in_the_file(tmp_path):
    s = StudioState(tmp_path)
    s.perform_slots = [{"slots": ["a", None, None, None, None], "name": ""}] + [
        {"slots": [None] * 5, "name": ""}
    ] * 3
    assert len(s._payload()["perform_slots"]) == 1  # the empty rows past it go
    s.perform_slots = [{"slots": [None] * 5, "name": ""}]
    assert "perform_slots" not in s._payload()


# --- the selected slot, and a take into it --------------------


def _wav(seconds: float = 0.5, sr: int = 44100) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x10" * int(seconds * sr) * 2)
    return buf.getvalue()


def _into(client, lane, row, how="new", bar="0", notes=None):
    """⏺ Rec's post (a take and its audio) into slot (lane, row): new,
    overdub or replace (⟲ Capture never overwrites: `_capture`)."""
    data = {"name": "keys-20261108-101010", "bpm": "124", "bars": "1"}
    data |= {"lane": str(lane), "row": str(row), "how": how, "bar": bar}
    data["notes"] = json.dumps(notes or [[0, 1, 60, 0.8]])
    files = {"file": ("take.wav", _wav(), "audio/wav")}
    return client.post("/take/keys", data=data, files=files, headers=_hx())


def _capture(client, lane, row, how="replace", notes=None):
    """⟲ Capture's post (a take without its audio) with slot (lane, row)
    selected; `how` is the dock's choice, which a capture does not read."""
    data = {"name": "keys-20261108-101010", "bpm": "124", "bars": "1"}
    data |= {"lane": str(lane), "row": str(row), "how": how, "bar": "0"}
    data["notes"] = json.dumps(notes or [[0, 1, 60, 0.8]])
    return client.post("/take/keys/capture", data=data, headers=_hx())


def test_a_capture_onto_a_filled_slot_lands_in_the_lanes_next_empty_one(client):
    """Deliberately, ⟲ Capture never overwrites — the
    selected slot when it is empty, else the lane's next empty one (the
    toast says where), whatever the dock's overdub | replace says."""
    s = _st(client)
    s.arrangement = replace(s.arrangement, bars=1, steps_per_bar=16)
    _loop(s, "lead", [["E3", 0], ["G3", 8]])
    _loop(s, "bass", [["A1", 0]])
    _post(client, "slot/place", lane="0", row="0", name="lead")
    _post(client, "slot/place", lane="0", row="1", name="bass")
    r = _capture(client, 0, 0, how="replace")
    _queued(r)
    assert s.slot_at(0, 0) == "lead" and s.slot_at(0, 1) == "bass"
    made = s.slot_at(0, 2)
    assert made and made not in ("lead", "bass") and s.slot_playing(0) == 2
    msg = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert "scene 3" in msg and "holds a loop" in msg
    assert loops.load(s.data_dir, "lead")["lane"]["cells"] == [["E3", 0], ["G3", 8]]
    # an empty selected slot takes it, as before
    _queued(_capture(client, 0, 5))
    assert s.slot_at(0, 5) not in (None, made)


def test_a_capture_with_no_empty_slot_in_the_lane_keeps_the_loop_only(client):
    s = _st(client)
    _loop(s, "lead", [["E3", 0]])
    for row in range(8):  # limits.MAX_SLOT_ROWS
        _post(client, "slot/place", lane="0", row=str(row), name="lead")
    before = s.slot_rows()
    r = _capture(client, 0, 3)
    assert r.status_code == 200
    msg = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert "no empty slot" in msg and "nothing was replaced" in msg
    assert s.slot_rows() == before
    made = unquote(r.headers["X-Loop-Name"])
    assert loops.exists(s.data_dir, made) and not s.loop_edit


def test_an_empty_slot_selected_edits_its_lane_only_when_it_has_a_sound(client):
    s = _st(client)
    r = _post(client, "slot/select", lane="2", row="0")  # no sound there
    assert r.status_code == 200 and s.active_layer == 0
    s.add_layer(replace(s.patch, name="Pad"), select=False)
    r = _post(client, "slot/select", lane="1", row="3")
    assert r.status_code == 200 and s.active_layer == 1
    assert s.perform_slots is None and not s._pattern_undo  # nothing kept, no step
    assert _post(client, "slot/select", lane="9", row="0").status_code == 422
    html = client.get("/view/perform").text
    assert 'class="sl-plus"' in html and 'data-menu="Select the slot"' in html


def test_a_take_into_an_empty_slot_is_a_new_loop_there_launched_one_undo(client):
    s = _st(client)
    s.arrangement = replace(s.arrangement, bars=1, steps_per_bar=16, cells=[["A1", 0]])
    r = _into(client, 1, 2)
    _queued(r)
    name = s.slot_at(1, 2)
    assert name and s.slot_playing(1) == 2 and s.loop_edit_of(1)["name"] == name
    assert _cells(s.layers[1]["cells"]) == [("C4", 0), ("C4", 1), ("C4", 2), ("C4", 3)]
    assert s.arrangement.cells == [["A1", 0]]  # the edited lane: untouched
    client.post("/fragment/pattern/undo", headers=_hx())
    assert s.perform_slots is None and not s.layers[1]["cells"] and not s.loop_edit
    assert loops.exists(s.data_dir, name)  # the take's loop stays in the Loops


def test_replace_puts_the_take_in_a_filled_slot_and_keeps_the_old_loop(client):
    s = _st(client)
    s.arrangement = replace(s.arrangement, bars=1, steps_per_bar=16)
    _loop(s, "lead", [["E3", 0], ["G3", 8]])
    _post(client, "slot/place", lane="0", row="0", name="lead")
    _queued(_into(client, 0, 0, how="replace"))
    assert s.slot_at(0, 0) not in (None, "lead") and loops.exists(s.data_dir, "lead")
    assert {c[0] for c in s.arrangement.cells} == {"C4"}


def test_overdub_merges_the_take_into_the_slots_loop_by_record_merges_rules(client):
    s = _st(client)
    s.arrangement = replace(s.arrangement, bars=1, steps_per_bar=16)
    _loop(s, "lead", [["E3", 0], ["G3", 8]], bars=1)
    _post(client, "slot/place", lane="0", row="0", name="lead")
    # not playing: it launches first (queued), then the notes merge in
    notes = [[1, 0.25, 64, 0.5], [1.25, 0.25, 64, 0.9], [2, 0.5, 52, 1.0]]
    r = _into(client, 0, 0, how="overdub", bar="3", notes=notes)
    _queued(r)
    assert s.slot_at(0, 0) == "lead" and s.slot_playing(0) == 0
    cells = _cells(s.arrangement.cells)
    # E4 twice in a row: two notes (a cut), E3 at beat 3 held two steps
    assert ("E4", 4) in cells and ("E4", 5) in cells
    assert ["E4", 5] in s.arrangement.cuts
    assert ("E3", 0) in cells and ("E3", 8) in cells and ("E3", 9) in cells
    assert s.arrangement.velocities["8"] == 1.0  # the step's velocity rewritten
    # (the request's own mirror saved it to the open loop, as any edit there)
    saved = loops.load(s.data_dir, "lead")
    assert ("E4", 5) in _cells(saved["lane"]["cells"]) and saved["edited"]
    # one undo: the merge; another: the launch
    client.post("/fragment/pattern/undo", headers=_hx())
    assert _cells(s.arrangement.cells) == [("E3", 0), ("G3", 8)]
    assert s.slot_playing(0) == 0
    # the slot playing: the preview swaps now, nothing waits
    r = _into(client, 0, 0, how="overdub", notes=[[0, 0.25, 67, 0.7]])
    assert r.status_code == 200
    assert "queue" not in json.loads(r.headers["HX-Trigger"])["preview-ready"]
    assert ("G4", 0) in _cells(s.arrangement.cells)


def test_an_overdub_folds_onto_the_loops_own_bars_from_the_bar_it_began(client):
    s = _st(client)
    s.arrangement = replace(s.arrangement, bars=2, steps_per_bar=16)
    _loop(s, "one", [["A2", 0]], bars=1)
    _loop(s, "two", [["C3", 0]], bars=2)
    _post(client, "slot/place", lane="0", row="0", name="one")
    _post(client, "slot/place", lane="1", row="0", name="two")
    _post(client, "scene/launch", row="0")
    # begun on bar 2 of the loop: a 1-bar slot folds it onto its bar, a
    # 2-bar slot lands it in its second bar
    _into(client, 0, 0, how="overdub", bar="1", notes=[[0, 0.25, 64, 1]])
    _into(client, 1, 0, how="overdub", bar="1", notes=[[0, 0.25, 64, 1]])
    assert ("E4", 0) in _cells(s.arrangement.cells)  # lane 1, the edited one
    assert ("E4", 16) in _cells(s.arrangement.cells)  # ...and its repeat
    assert ("E4", 16) in _cells(s.layers[1]["cells"])
    assert ("E4", 0) not in _cells(s.layers[1]["cells"])
    s.mirror_loop_edit()
    one = loops.load(s.data_dir, "one")["lane"]
    assert one["bars"] == 1 and ("E4", 0) in _cells(one["cells"])  # its own bar


def test_a_take_refused_by_its_slot_stays_a_loop_and_says_why(client):
    s = _st(client)
    s.arrangement = replace(s.arrangement, meter="3/4", bars=1, steps_per_bar=12)
    # (a loop in 4/4: the launch an overdub needs first is refused)
    _loop(s, "lead", [["E3", 0]], spb=16, meter="4/4")
    _post(client, "slot/place", lane="0", row="0", name="lead")
    r = _into(client, 0, 0, how="overdub")
    assert r.status_code == 200
    msg = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert "not in the slot" in msg and "4/4" in msg
    assert s.slot_at(0, 0) == "lead"


def test_a_fresh_session_conforms_without_the_launcher(tmp_path):
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "perform_slots" not in data
    assert Arrangement(
        **schema.conform(data["arrangement"], schema.nodes().ARRANGEMENT)
    )
