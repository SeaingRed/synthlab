"""One ⏺ Rec = a take that is both its audio and an editable loop
(`loops.py`, `looplib.py`, `routes/takes.py`).

What is held here: a take posted keeps its clip AND makes its loop — the
notes as played kept as they were, placed on a grid of the take's bars at
the meter's sixteenths — kept from the start; the placement's strength (0 =
each note in the step it began in, 100 = the nearest step) and rate; Edit
opens the loop on Perform's grid AS the loop (every edit saves to it; Done,
another loop or section, or Ctrl+Z past its opening end it); Quantise places
it again from the notes as played, never touching them; → Song as a pattern
(a track with its sound) or as audio (its take); one Loops list (the dock's
strip, Perform's bin, the drawer's group) holding loops and the takes from
before — which their first action makes a loop of; delete takes its audio
unless something uses it; a loop from before these keys reads as one.
"""

from __future__ import annotations

import io
import json
import re
import time
import wave
from dataclasses import asdict, replace
from urllib.parse import unquote

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio import keytakes, looplib, loops, schema
from synthlab.studio.clips import save_clip
from synthlab.studio.server import create_app
from synthlab.studio.state.model import Arrangement


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


PERFORM = _hx("perform")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _st(client):
    return client.app.state.studio


def _wav(seconds: float = 0.5, sr: int = 44100) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x10" * int(seconds * sr) * 2)
    return buf.getvalue()


# played, a hair off the grid: A1 a touch late on beat 1, C2 a touch early
# on beat 2 (0.96: 3.84 sixteenths), E2 on bar 2's downbeat
NOTES = [[0.02, 1, 33, 0.8], [0.96, 0.5, 36, 0.6], [4, 2, 40, 1]]


def _take(client, notes=NOTES, view="editor", **over):
    data = {"name": "keys-1", "bpm": "120", "bars": "2", "notes": json.dumps(notes)}
    files = {"file": ("take.wav", _wav(), "audio/wav")}
    return client.post("/take/keys", data=data | over, files=files, headers=_hx(view))


def _cells(lane) -> list:
    return sorted((p, int(s)) for p, s in lane["cells"])


# --- one Rec: the take and its loop ---------------------------------------------------


def test_a_take_is_kept_with_its_loop_the_notes_as_played_and_placed(client):
    r = _take(client)
    assert r.status_code == 200, r.text[:300]
    assert r.headers["X-Clip-Name"] == "keys-1"
    assert r.headers["X-Loop-Name"] == "keys-1"
    s = _st(client)
    meta = json.loads((s.data_dir / "clips" / "keys-1.meta.json").read_text())
    assert meta["kept"] is True  # no unkept limbo: ✕ is the way a take goes
    loop = loops.load(s.data_dir, "keys-1")
    assert loop["take"] == "keys-1"
    assert loop["played"] == NOTES  # exactly as played: never rewritten
    lane = loop["lane"]
    assert (lane["bars"], lane["steps_per_bar"]) == (2, 16)  # the take's bars, 1/16
    # the nearest step: A1 on 0, C2 on 4 (beat 2), E2 on 16 (bar 2)
    assert ("A1", 0) in _cells(lane) and ("C2", 4) in _cells(lane)
    assert ("E2", 16) in _cells(lane)
    # as played, unasked: each note on its nearest step with its own feel
    # (`note_feel`) — where it starts and ends between the lines
    assert loop["quantise"] == 0 and loop["edited"] is False
    assert lane["note_feel"]["C2.4"] == [-0.16, -0.16, 1.0]
    assert loop["key"]["bpm"] == 120
    assert loop["sound"] is not None  # the sound that played it


def test_the_strength_keeps_each_notes_feel_below_100():
    late, early = [[0.06, 0.25, 60, 1]], [[0.96, 0.25, 62, 1]]  # 0.24 / 3.84 steps

    def placed(notes, q):
        lane = loops.place(notes, "4/4", 1, 16, q)
        return sorted(int(s) for _p, s in lane["cells"]), lane["note_feel"]

    # 100: on the nearest step, no feel of its own (as it always was)
    assert placed(late, 100) == ([0], {}) and placed(early, 100) == ([4], {})
    # as played (0): the nearest step, and the note's own feel — where it
    # starts from its step's line, where it ends from its run's end line, its
    # share of the step's velocity — a hair late on 0, a hair early on beat 2
    assert placed(late, 0) == ([0], {"C4.0": [0.24, 0.24, 1.0]})
    assert placed(early, 0) == ([4], {"D4.4": [-0.16, -0.16, 1.0]})
    # between: start and end pulled that share of the way to their lines
    assert placed(early, 50) == ([4], {"D4.4": [-0.08, -0.08, 1.0]})
    assert placed(early, 25) == ([4], {"D4.4": [-0.12, -0.12, 1.0]})
    assert loops.place(early, "4/4", 1, 16, 0)["note_nudge"] == {}
    # a held note runs to the line nearest where it ended (0.4 + 3.4 steps
    # ends at 3.8: four steps) and is let go 0.2 before it
    held = [[0.1, 0.85, 64, 1]]
    assert placed(held, 0) == ([0, 1, 2, 3], {"E4.0": [0.4, -0.2, 1.0]})
    assert placed(held, 100)[0] == [0, 1, 2]
    # played early onto the grid's end line: its first step, early (the
    # render wraps it to the end, where it was played) — a loop is a circle
    pickup = [[0, 1, 60, 1], [3.95, 0.05, 64, 1]]  # 15.8 steps of 16
    cells, feel = placed(pickup, 0)
    assert ["E4", 0] in loops.place(pickup, "4/4", 1, 16, 0)["cells"]
    assert feel["E4.0"] == [-0.2, -1.0, 1.0]  # 0.05 beats: 0.2 of a step
    assert ["E4", 0] not in loops.place(pickup, "4/4", 1, 16, 100)["cells"]
    # a chord of mixed velocities: the step holds the loudest exactly, each
    # note its share of it
    chord = [[0, 1, 60, 0.9], [0.01, 1, 64, 0.45]]
    lane = loops.place(chord, "4/4", 1, 16, 0)
    assert lane["velocities"] == {"0": 0.9}
    assert lane["note_feel"]["E4.0"] == [0.04, 0.04, 0.5]
    # the rate: eighths in 4/4, and in 3/4 the meter's own steps
    assert loops.place(early, "4/4", 1, 8, 100)["cells"] == [["D4", 2]]
    assert [n for n, _w in loops.rates("3/4")] == [3, 6, 9, 12, 18, 24]
    words = ["1/4", "1/8", "1/8T", "1/16", "1/16T", "1/32"]
    assert [w for _n, w in loops.rates("4/4")] == words
    assert [w for _n, w in loops.rates("3/4")][2] == "1/8T"  # 9 a bar
    assert loops.grid_bars(2.3, 16) == 3 and loops.grid_bars(8, 16) == 8
    # 1/16T: 24 a bar, so 8 bars is its longest grid (16 x 24 = 384 > 256)
    assert loops.grid_bars(12.5, 24) == 8 and loops.grid_bars(12.5, 12) == 16
    # past 4 bars the grid rounds up to 8 or 16 (a beat of 1, 2 or 4 bars
    # divides it); 256 steps is the grid's cap, so 1/32 stops at 8
    assert loops.grid_bars(5, 16) == 8 and loops.grid_bars(12.5, 16) == 16
    assert loops.grid_bars(30, 16) == 16 and loops.grid_bars(12.5, 32) == 8
    assert loops.grid_bars(4, 32) == 4


def test_a_take_in_three_four_is_a_loop_in_three_four(client):
    s = _st(client)
    client.post("/fragment/meter", data={"meter": "3/4"}, headers=PERFORM)
    assert s.arrangement.meter == "3/4"
    assert _take(client, bars="2").status_code == 200
    loop = loops.load(s.data_dir, "keys-1")
    assert loop["meter"] == "3/4" and loop["lane"]["steps_per_bar"] == 12
    # E2 at beat 4 is bar 2's second beat in 3/4: step 12 + 4
    assert ("E2", 16) in _cells(loop["lane"])


def test_the_take_stays_when_the_loop_store_is_full(client, monkeypatch):
    monkeypatch.setattr(loops.limits, "MAX_LOOPS", 0)
    r = _take(client)
    assert r.status_code == 200 and "X-Loop-Name" not in r.headers
    assert "no loop of it" in r.headers["HX-Trigger"]
    rows = looplib.rows(_st(client).data_dir)
    assert [(x["kind"], x["name"]) for x in rows] == [("take", "keys-1")]


# --- Edit: the loop on the grid, as the loop ------------------------------------------


def _edit(client, **ref):
    return client.post("/loop/edit", data=ref or {"name": "keys-1"}, headers=PERFORM)


def test_edit_opens_it_on_the_grid_and_every_change_saves_to_it(client):
    _take(client)
    s = _st(client)
    r = _edit(client)
    assert r.status_code == 200
    assert "Editing the loop" in r.headers["HX-Trigger"]
    assert "ed-head is-saved" in r.text and "keys-1" in r.text  # the editor's head
    a = s.arrangement
    assert (a.bars, a.steps_per_bar) == (2, 16)
    assert ("A1", 0) in [(p, int(st)) for p, st in a.cells]
    assert s.loop_edit_of()["name"] == "keys-1"
    # an edit on the grid: written back to the loop, the notes as played kept
    client.post("/fragment/pattern/notes/shift_right", headers=PERFORM)
    loop = loops.load(s.data_dir, "keys-1")
    assert ("A1", 1) in _cells(loop["lane"]) and ("A1", 0) not in _cells(loop["lane"])
    assert loop["edited"] is True and loop["played"] == NOTES
    # Done: the loop keeps it; the grid is the working loop again
    r = client.post("/loop/edit/done", headers=PERFORM)
    assert r.status_code == 200 and not s.loop_edit
    client.post("/fragment/pattern/notes/shift_right", headers=PERFORM)
    assert ("A1", 1) in _cells(loops.load(s.data_dir, "keys-1")["lane"])


def test_ctrl_z_past_its_opening_ends_the_edit_and_the_loop_keeps_its_edits(client):
    _take(client)
    s = _st(client)
    before = list(s.arrangement.cells)
    _edit(client)
    client.post("/fragment/pattern/notes/shift_right", headers=PERFORM)
    client.post("/fragment/pattern/undo", headers=PERFORM)  # the shift
    assert s.loop_edit
    assert ("A1", 0) in _cells(loops.load(s.data_dir, "keys-1")["lane"])
    client.post("/fragment/pattern/undo", headers=PERFORM)  # the opening
    assert not s.loop_edit
    assert sorted(map(tuple, s.arrangement.cells)) == sorted(map(tuple, before))
    # the working loop's notes came back: none of them went into the loop
    assert ("A1", 0) in _cells(loops.load(s.data_dir, "keys-1")["lane"])


def test_a_section_or_another_loop_on_the_grid_ends_the_edit(client):
    _take(client)
    s = _st(client)
    _edit(client)
    client.post("/song/from-loop", headers=PERFORM)
    sid = s.song["sections"][0]["id"]
    assert s.loop_edit  # adding the loop to the song reads it
    client.post(f"/song/section/{sid}/to-loop", headers=PERFORM)
    assert not s.loop_edit
    _edit(client)
    client.post("/loop/load", data={"name": "keys-1"}, headers=PERFORM)
    assert not s.loop_edit  # → Perform puts a copy on the grid


def test_another_lane_on_screen_writes_nothing_to_the_loop(client):
    _take(client)
    s = _st(client)
    _edit(client)
    was = loops.load(s.data_dir, "keys-1")["lane"]
    client.post("/fragment/layer/add", data={"own": "1"}, headers=PERFORM)
    assert s.active_layer not in s.loop_edit
    client.post("/fragment/pattern/notes/shift_right", headers=PERFORM)
    assert loops.load(s.data_dir, "keys-1")["lane"] == was


# --- Quantise: placed again, never destructive ----------------------------------------


def test_quantise_places_the_notes_as_played_again_and_the_grid_follows(client):
    _take(client)
    s = _st(client)
    _edit(client)
    r = client.post(
        "/loop/quantise",
        data={"name": "keys-1", "steps": "16", "strength": "0"},
        headers=PERFORM,
    )
    assert r.status_code == 200 and "as played" in r.headers["HX-Trigger"]
    loop = loops.load(s.data_dir, "keys-1")
    # on its step, a hair early: the timing inside the step kept
    assert ("C2", 4) in _cells(loop["lane"])
    feel = {"A1.0": [0.08, 0.08, 1.0], "C2.4": [-0.16, -0.16, 1.0]}
    assert loop["lane"]["note_feel"] == feel | {"E2.16": [0.0, 0.0, 1.0]}
    assert loop["quantise"] == 0 and loop["played"] == NOTES
    assert ("C2", 4) in [(p, int(st)) for p, st in s.arrangement.cells]
    assert s.arrangement.note_feel == loop["lane"]["note_feel"]
    client.post(
        "/loop/quantise",
        data={"name": "keys-1", "steps": "8", "strength": "100"},
        headers=PERFORM,
    )
    loop = loops.load(s.data_dir, "keys-1")
    assert loop["lane"]["steps_per_bar"] == 8 and ("C2", 2) in _cells(loop["lane"])
    assert loop["lane"]["note_feel"] == {} == s.arrangement.note_feel  # on the steps
    assert loop["edited"] is False  # placed, not edited by hand
    # a rate the meter has not, a strength past 100: refused, nothing moved
    for bad in ({"steps": "9"}, {"strength": "101"}, {"steps": "x"}):
        r = client.post("/loop/quantise", data={"name": "keys-1"} | bad)
        assert r.status_code == 422
    assert loops.load(s.data_dir, "keys-1")["lane"] == loop["lane"]


# --- → Song --------------------------------------------------------------------------


def test_to_song_as_a_pattern_is_a_track_with_its_sound_and_a_section(client):
    _take(client)
    s = _st(client)
    loop = loops.load(s.data_dir, "keys-1")
    r = client.post("/loop/song", data={"name": "keys-1"}, headers=PERFORM)
    assert r.status_code == 200, r.text[:300]
    assert "as a pattern" in r.headers["HX-Trigger"]
    song = s.song
    (sec,) = song["sections"]
    assert sec["name"] == "keys-1" and sec["bars"] == 2
    assert song["bpm"] == 120  # the first loop into an empty song
    (tid, pid), *_ = sec["cells"].items()
    track = next(t for t in song["tracks"] if t["id"] == tid)
    assert track["patch"]["name"] == loop["sound"][0].name
    assert not any(t["kind"] == "drums" for t in song["tracks"])  # no beat
    pattern = song["patterns"][pid]
    assert sorted(map(tuple, pattern["cells"])) == _cells(loop["lane"])
    # one song undo step
    client.post("/song/undo", headers=_hx("song"))
    assert s.song["sections"] == []


def test_to_song_as_audio_is_its_take_on_an_audio_track(client):
    _take(client)
    s = _st(client)
    r = client.post(
        "/loop/song",
        data={"take": "", "name": "keys-1", "as": "audio"},
        headers=PERFORM,
    )
    assert r.status_code == 200, r.text[:300]
    (sec,) = s.song["sections"]
    (take,) = sec["audio"]
    assert take["clip"] == "keys-1" and take["at_bar"] == 0
    assert any(
        t["kind"] == "audio" and t["id"] == take["track_id"] for t in s.song["tracks"]
    )


def test_a_loop_with_no_audio_is_rendered_into_its_audio_first(client):
    s = _st(client)
    s.arrangement = Arrangement(cells=[["A2", 0], ["C3", 2]])
    client.post(
        "/loop/save", data={"lane": "1", "bars": "1", "loop": "riff"}, headers=PERFORM
    )
    assert loops.load(s.data_dir, "riff")["take"] is None
    r = client.post("/loop/song", data={"name": "riff", "as": "audio"}, headers=PERFORM)
    assert r.status_code == 200, r.text[:300]
    made = loops.load(s.data_dir, "riff")["take"]
    assert made and (s.data_dir / "clips" / f"{made}.wav").is_file()
    assert s.song["sections"][0]["audio"][0]["clip"] == made


# --- one list ------------------------------------------------------------------------


def test_one_list_newest_first_in_the_drawer_the_panel_and_the_strip(client):
    s = _st(client)
    _take(client, name="first")
    time.sleep(0.01)
    _take(client, name="second")
    names = [r["name"] for r in looplib.rows(s.data_dir)]
    assert names == ["second", "first"]
    page = client.get("/view/perform").text
    for region in ('id="key-takes"', 'id="loops-panel"', 'data-group="loops"'):
        assert region in page
    assert page.count('hx-post="/loop/edit"') >= 6  # two rows, three lists
    # the drawer files a take with its loop, never in a Takes group as well
    assert 'data-group="take"' not in page and 'data-group="take-new"' not in page
    # a row is quiet: ▷, its name, its facts, ★ — its actions are its
    # right-click menu's (never drawn: `.lp-acts`), no mini piano roll
    assert 'class="lp-roll"' not in page and 'class="kt-acts lp-acts"' in page
    # the drawer's search narrows the Loops too
    found = client.get("/fragment/clips", params={"q": "seco"}).text
    assert "second" in found and "first" not in found


def test_a_take_from_before_one_rec_made_both_is_listed_and_made_a_loop(client):
    s = _st(client)
    meta = {"source": "keys", "kept": False, "bpm": 100, "bars": 1, "at": 1.0}
    save_clip(s.data_dir, "old", [0.0] * 4410, meta=meta)
    keytakes.write_notes(s.data_dir, "old", {"notes": [[0, 1, 45, 0.5]]})
    (row,) = looplib.rows(s.data_dir)
    assert row["kind"] == "take" and row["audio"] == "old" and row["roll"]["d"]
    assert row["ref"] == {"take": "old"}
    # its first action makes its loop, from the notes as played
    r = client.post("/loop/edit", data={"take": "old"}, headers=PERFORM)
    assert r.status_code == 200
    loop = loops.load(s.data_dir, "old")
    assert loop["take"] == "old" and loop["played"] == [[0, 1, 45, 0.5]]
    assert loop["key"]["bpm"] == 100
    (row,) = looplib.rows(s.data_dir)
    assert row["kind"] == "loop"


def test_a_loop_from_before_these_keys_reads_as_one_with_no_audio(client):
    s = _st(client)
    raw = {
        "v": 1,
        "name": "old",
        "at": 5.0,
        "arr": {"bars": 1, "steps_per_bar": 16, "cells": [["A1", 0]], "bpm": 110},
        "sound": None,
    }
    (loops.loop_dir(s.data_dir) / "old.json").write_text(json.dumps(raw))
    loop = loops.load(s.data_dir, "old")
    assert loop["take"] is None and loop["played"] == [] and loop["quantise"] == 100
    (row,) = looplib.rows(s.data_dir)
    assert row["audio"] == "" and row["detail"].startswith("1 bar · 1/16 · 110 bpm")
    page = client.get("/view/perform").text
    # no audio: its ▶ asks for a render (/loop/preview), no take to fetch
    plays = re.findall(r"<button[^>]*aria-label=\"listen to old\"[^>]*>", page)
    assert plays and all("data-take-url" not in b for b in plays)
    assert all("data-preview=" in b for b in plays)


def test_delete_takes_its_audio_unless_something_uses_it(client):
    s = _st(client)
    _take(client, name="a")
    _take(client, name="b")
    client.post("/loop/delete", data={"name": "a", "audio": "1"}, headers=PERFORM)
    assert not loops.exists(s.data_dir, "a")
    assert not (s.data_dir / "clips" / "a.wav").exists()
    client.post("/loop/song", data={"name": "b", "as": "audio"}, headers=PERFORM)
    r = client.post("/loop/delete", data={"name": "b", "audio": "1"}, headers=PERFORM)
    assert "is in use" in r.headers["HX-Trigger"]
    assert (s.data_dir / "clips" / "b.wav").exists() and not loops.exists(
        s.data_dir, "b"
    )


def test_a_renamed_clip_takes_its_loop_with_it_and_an_open_loop_its_name(client):
    s = _st(client)
    _take(client)
    client.post("/fragment/clip/rename", data={"name": "keys-1", "new": "riff"})
    assert loops.load(s.data_dir, "keys-1")["take"] == "riff"
    _edit(client)
    client.post("/loop/rename", data={"name": "keys-1", "new": "hook"}, headers=PERFORM)
    assert s.loop_edit_of()["name"] == "hook" and loops.exists(s.data_dir, "hook")


def test_a_stroke_on_the_open_loop_resends_no_list(client):
    """The lists watch the open loop by name: a grid edit writes it, and
    the dock's strip, the bin and the drawer stay where they are."""
    _take(client)
    _edit(client)
    r = client.post("/fragment/pattern/notes/shift_right", headers=PERFORM)
    for region in ('id="key-takes"', 'id="loops-panel"', 'id="clip-library"'):
        assert region not in r.text
    r = client.post("/loop/edit/done", headers=PERFORM)
    assert 'id="loops-panel"' in r.text  # the edit ended: the list catches up


def test_a_hostile_loop_name_cannot_leave_its_attribute(client):
    s = _st(client)
    name = "x'\"><img src=x onerror=1>"
    s.arrangement = Arrangement(cells=[["A2", 0]])
    client.post("/loop/save", data={"lane": "1", "loop": name}, headers=PERFORM)
    page = client.get("/view/perform").text
    assert "<img src=x" not in page


# --- the Loops panel: names, search, ★, tags, beats, ▶ in time --------


def _file(client, name: str) -> dict:
    path = loops.loop_dir(_st(client).data_dir) / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _key(s) -> str:
    return loops.key_short(s.arrangement.root, s.arrangement.scale_name)


def test_a_rec_named_by_the_clock_is_named_from_what_it_is(client):
    s = _st(client)
    r = _take(client, name="keys-20261107-150616")
    name = unquote(r.headers["X-Loop-Name"])
    # plainly: its key, tempo and bars are the lists' cells
    assert name == f"{s.patch.name} riff"
    assert r.headers["X-Clip-Name"] == "keys-20261107-150616"  # the audio's own
    assert loops.load(s.data_dir, name)["take"] == "keys-20261107-150616"
    # the same again is told apart
    r = _take(client, name="keys-20261107-150700")
    assert unquote(r.headers["X-Loop-Name"]) == f"{name} 2"
    # three notes starting together are chords; a sound's word is said once
    lane = {"cells": [["A2", 0], ["C3", 0], ["E3", 0]], "cuts": []}
    assert loops.plays(lane) == "chords"
    assert loops.plays({"cells": [], "cuts": []}) == "loop"
    key = {"bpm": 90, "root": "C3", "scale_name": "major"}
    assert loops.title("Pad chords", lane, key, 4) == "Pad chords"


def test_a_loop_named_by_the_clock_before_shows_what_it_is_until_renamed(client):
    s = _st(client)
    arr = Arrangement(bpm=124, root="A2", scale_name="minor", bars=2, cells=[["A2", 0]])
    loops.save(s.data_dir, "keys-20261101-120000", arr, s.sound_payload())
    (row,) = looplib.rows(s.data_dir)
    assert row["name"] == "keys-20261101-120000"  # its file is as it was
    assert row["title"] == f"{s.patch.name} riff"
    assert row["facts"] | {"time": "", "more": ""} == {
        "len": "2 bars",
        "grid": "1/4",
        "bpm": "124",
        "key": "Am",
        "time": "",
        "more": "",
    }
    page = client.get("/view/perform").text
    assert f"{s.patch.name} riff" in page and '<span class="fc c-key">Am</span>' in page
    ref = {"name": "keys-20261101-120000", "new": "hook"}
    client.post("/loop/rename", data=ref, headers=PERFORM)
    (row,) = looplib.rows(s.data_dir)
    assert row["name"] == row["title"] == "hook"


def test_a_name_with_its_facts_baked_in_shows_plainly_and_keeps_its_file(client):
    """An older saved auto-name with its facts baked in ("… · Am · 124 · 2b") is shown
    without the suffix while it says exactly the loop's own key, tempo and
    bars — told apart when two read the same — and its file is untouched;
    a suffix that is not the loop's own stays (it is a name someone gave)."""
    s = _st(client)
    for bpm in (124, 132):
        arr = Arrangement(
            bpm=bpm, root="A2", scale_name="minor", bars=1, cells=[["A2", 0]]
        )
        loops.save(
            s.data_dir, f"Acid bass riff · Am · {bpm} · 1b", arr, s.sound_payload()
        )
    arr = Arrangement(bpm=100, root="C3", scale_name="major", bars=1, cells=[["C3", 0]])
    loops.save(s.data_dir, "Acid bass riff · Am · 124 · 1b 2", arr, s.sound_payload())
    shown = {r["name"]: r["title"] for r in looplib.rows(s.data_dir)}
    assert (
        shown["Acid bass riff · Am · 124 · 1b 2"] == "Acid bass riff · Am · 124 · 1b 2"
    )
    assert sorted(shown[f"Acid bass riff · Am · {b} · 1b"] for b in (124, 132)) == [
        "Acid bass riff",
        "Acid bass riff 2",
    ]
    assert (
        loops.plain(
            "Pad · C · 90 · 4b 3", {"root": "C3", "scale_name": "major", "bpm": 90}, 4
        )
        == "Pad 3"
    )
    assert (
        loops.plain("Techno beat · 124 · 1b", {"bpm": 124}, 1, beat=True)
        == "Techno beat"
    )
    assert loops.exists(s.data_dir, "Acid bass riff · Am · 132 · 1b")


def test_search_reads_name_sound_key_tempo_kind_and_tags(client):
    s = _st(client)
    _take(client, name="first")
    tags = {"name": "first", "tags": "Dark, #wide dark"}
    client.post("/loop/tag", data=tags, headers=PERFORM)
    assert loops.load(s.data_dir, "first")["tags"] == ["dark", "wide"]
    (row,) = [r for r in looplib.rows(s.data_dir) if r["kind"] == "loop"]
    for q in ("first", s.patch.name.split()[0], "120", "#dark", "wide", "notes"):
        assert looplib.matches(row, q), q
    assert looplib.matches(row, _key(s)) and not looplib.matches(row, "#nope")
    assert looplib.matches(row, "first 120") and not looplib.matches(row, "first 99")
    # the drawer's search is the same words
    found = client.get("/fragment/clips", params={"q": "#dark"}).text
    assert 'data-ctx="loop first"' in found
    found = client.get("/fragment/clips", params={"q": "beat"}).text
    assert 'data-ctx="loop first"' not in found


def test_star_and_tags_are_absent_until_set_and_gone_when_cleared(client):
    _take(client, name="first")
    assert {"star", "tags", "kind", "beat"}.isdisjoint(_file(client, "first"))
    client.post("/loop/star", data={"name": "first"}, headers=PERFORM)
    assert _file(client, "first")["star"] is True
    page = client.get("/view/perform").text
    assert re.search(r'data-ctx="loop first"[^>]*data-star', page)
    client.post("/loop/star", data={"name": "first"}, headers=PERFORM)
    assert "star" not in _file(client, "first")
    client.post("/loop/tag", data={"name": "first", "tags": "dark"}, headers=PERFORM)
    assert _file(client, "first")["tags"] == ["dark"]
    client.post("/loop/tag", data={"name": "first", "tags": ""}, headers=PERFORM)
    assert "tags" not in _file(client, "first")


def test_an_older_loop_file_is_written_back_as_it_was(client):
    s = _st(client)
    raw = {
        "v": 1,
        "name": "old",
        "at": 5.0,
        "arr": {"bars": 1, "steps_per_bar": 16, "cells": [["A1", 0]], "bpm": 110},
        "sound": None,
    }
    (loops.loop_dir(s.data_dir) / "old.json").write_text(json.dumps(raw))
    assert loops.load(s.data_dir, "old")["kind"] == "notes"
    loops.update(s.data_dir, "old", quantise=50)
    assert {"star", "tags", "kind", "beat"}.isdisjoint(_file(client, "old"))


def test_the_bank_shows_as_beats_and_a_kept_one_once(client):
    s = _st(client)
    bank = [r for r in looplib.rows(s.data_dir, s.drum_bank) if r["kind"] == "bank"]
    assert len(bank) == sum(p is not None for p in s.drum_bank)
    assert all(r["what"] == "beat" and r["ref"] == {"slot": r["slot"]} for r in bank)
    assert 'data-kind="bank"' in client.get("/view/perform").text
    # ★ on a slot keeps it as a beat loop, which the slot then is
    client.post("/loop/star", data={"slot": "0"}, headers=PERFORM)
    rows = looplib.rows(s.data_dir, s.drum_bank)
    kept = [r for r in rows if r["kind"] == "loop"]
    assert len(kept) == 1 and kept[0]["what"] == "beat" and kept[0]["star"]
    assert kept[0]["name"] == s.drum_bank[0]["name"]
    assert not [r for r in rows if r["kind"] == "bank" and r["slot"] == 0]
    assert s.drum_bank[0] is not None  # the bank is as it was
    loop = loops.load(s.data_dir, kept[0]["name"])
    assert loop["kind"] == "beat" and loop["lane"]["cells"] == []
    assert loops.beat_key(loop["beat"]) == loops.beat_key(s.drum_bank[0])


def test_keep_this_beat_keeps_the_grids_beat_once(client):
    s = _st(client)
    client.post("/fragment/drums/bank/load/2", headers=PERFORM)
    r = client.post("/loop/beat/keep", headers=PERFORM)
    assert r.status_code == 200 and "Kept the beat" in r.headers["HX-Trigger"]
    (kept,) = [r for r in looplib.rows(s.data_dir) if r["kind"] == "loop"]
    assert kept["what"] == "beat"
    assert kept["name"] == f"{s.drum_bank[2]['name']} beat"
    beat = loops.load(s.data_dir, kept["name"])["beat"]
    assert sorted(map(tuple, beat["steps"])) == sorted(map(tuple, s.arrangement.drums))
    again = client.post("/loop/beat/keep", headers=PERFORM)
    assert again.status_code == 200 and "already" in again.headers["HX-Trigger"]
    s.arrangement = Arrangement()
    assert client.post("/loop/beat/keep", headers=PERFORM).status_code == 422


def test_drums_save_lands_in_the_loops_too(client):
    s = _st(client)
    drums = _hx("drums")
    client.post("/fragment/drums/bank/load/1", headers=drums)
    mine = {"name": "mine", "slot": "5"}
    client.post("/fragment/drums/bank/save/5", data=mine, headers=drums)
    assert loops.load(s.data_dir, "mine")["kind"] == "beat"
    client.post("/fragment/drums/bank/save/5", data=mine, headers=drums)
    assert [r["name"] for r in loops.listing(s.data_dir)] == ["mine"]  # once
    client.post("/fragment/drums/lib/save", data={"name": "lib one"}, headers=drums)
    assert loops.load(s.data_dir, "lib one")["kind"] == "beat"


def test_open_a_beat_puts_it_on_the_drum_grid_and_the_notes_stay(client):
    s = _st(client)
    s.arrangement = Arrangement(cells=[["A2", 0]], drums=[])
    loops.save_beat(s.data_dir, "techno", s.drum_bank[2], s.arrangement)
    r = client.post("/loop/beat", data={"name": "techno"}, headers=PERFORM)
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert trig["preview-ready"]["queue"] is True  # at the wrap, as a launch
    assert s.arrangement.cells == [["A2", 0]] and s.arrangement.drums
    client.post("/fragment/pattern/undo", headers=PERFORM)
    assert s.arrangement.drums == [] and s.arrangement.cells == [["A2", 0]]
    # Edit on a beat opens it on the drum grid too; from Synths, onto Perform
    r = client.post("/loop/edit", data={"name": "techno"}, headers=_hx("editor"))
    assert r.headers.get("HX-Push-Url") == "/view/perform" and s.arrangement.drums
    # a bank slot opens as the bank loads one
    client.post("/loop/beat", data={"slot": "4"}, headers=PERFORM)
    assert s.active_slot == 4


def test_preview_is_its_audio_at_its_tempo_and_a_render_at_another(client):
    s = _st(client)
    s.arrangement = Arrangement(bpm=110, cells=[["A2", 0], ["C3", 4]])
    lane = {"lane": "1", "bars": "1", "loop": "riff"}
    client.post("/loop/save", data=lane, headers=PERFORM)
    assert loops.load(s.data_dir, "riff")["take"] is None
    r = client.post("/loop/preview", data={"name": "riff"}, headers=PERFORM)
    url = r.headers["X-Preview-Url"]
    take = loops.load(s.data_dir, "riff")["take"]
    assert take and url == f"/clip/{take}.wav"  # made once, kept as its audio
    assert client.get(url).status_code == 200
    same = {"name": "riff", "bpm": "110"}
    again = client.post("/loop/preview", data=same, headers=PERFORM)
    assert again.headers["X-Preview-Url"] == url  # its own tempo: no new render
    fast = {"name": "riff", "bpm": "128"}
    other = client.post("/loop/preview", data=fast, headers=PERFORM)
    at = other.headers["X-Preview-Url"]
    assert at.startswith("/preview/") and client.get(at).status_code == 200
    slot = client.post("/loop/preview", data={"slot": "0"}, headers=PERFORM)
    assert slot.headers["X-Preview-Url"].startswith("/preview/")
    assert [r["name"] for r in loops.listing(s.data_dir)] == ["riff"]  # kept none


# --- a take longer than the grid: every bar played reaches the song ------------------

# eighteen bars: a chord held over bar 16's line (beats 63-66), a note in
# bar 18 — past the longest grid (16 bars at 1/16)
LONG = [[0, 1, 45, 1], [63, 3, 48, 1], [63, 3, 52, 1], [69.01, 0.5, 57, 0.5]]
# six bars: on one grid of 8 since grids hold 8 and 16 (it was 4 + 2 played)
SIX = [[0, 1, 45, 1], [15, 3, 48, 1], [15, 3, 52, 1], [21.01, 0.5, 57, 0.5]]


def test_a_take_of_six_bars_is_one_grid_of_eight_holding_every_note(client):
    r = _take(client, notes=SIX, bars="6")
    assert "HX-Trigger" not in r.headers  # nothing kept off the grid to say
    loop = loops.load(_st(client).data_dir, unquote(r.headers["X-Loop-Name"]))
    lane = loop["lane"]
    assert (lane["bars"], lane["steps_per_bar"]) == (8, 16)
    assert loops.rest_bars(loop) == 0
    # the chord held over bar 4's line stays one note; the late note in bar 6
    assert {("C3", st) for st in range(60, 72)} <= set(_cells(lane))
    assert ("A3", 84) in _cells(lane)
    # Edit puts all eight bars on the grid, and an edit there keeps them
    s = _st(client)
    assert _edit(client, name=loop["name"]).status_code == 200
    assert (s.arrangement.bars, s.arrangement.steps_per_bar) == (8, 16)
    client.post("/fragment/pattern/notes/shift_right", headers=PERFORM)
    after = loops.load(s.data_dir, loop["name"])["lane"]
    assert after["bars"] == 8 and ("A3", 85) in _cells(after)


def test_the_note_grid_holds_eight_and_sixteen_bars_while_they_fit(client):
    """`limits.LOOP_BAR_CHOICES` on the working loop: 8 and 16 while bars
    x steps fits `MAX_GRID_STEPS` (256); the drum grid keeps its 1-4."""
    node = schema.nodes().ARRANGEMENT

    def conformed(**kw):
        return schema.conform(asdict(Arrangement()) | kw, node)

    assert conformed(bars=16, steps_per_bar=16)["bars"] == 16
    assert conformed(bars=16, steps_per_bar=32)["bars"] == 8  # the longest that fits
    assert conformed(bars=5)["bars"] == Arrangement().bars  # no such choice
    assert conformed(drum_bars=8)["drum_bars"] == Arrangement().drum_bars
    # a loop file's `arr` carries no drum clock: the drum lane takes 4 at most
    assert schema.conform({"bars": 16, "cells": []}, node)["drum_bars"] == 4
    assert schema.conform({"bars": 3, "cells": []}, node)["drum_bars"] == 3
    # the grid's own tools and form: ×2 to 8 and 16, then refused
    s = _st(client)
    s.arrangement = Arrangement(bars=4, steps_per_bar=16, cells=[["A1", 0], ["C2", 60]])
    for bars in (8, 16):
        r = client.post("/fragment/pattern/notes/duplicate", headers=PERFORM)
        assert r.status_code == 200 and s.arrangement.bars == bars
    assert ["C2", 252] in s.arrangement.cells
    r = client.post("/fragment/pattern/notes/duplicate", headers=PERFORM)
    assert r.status_code == 422 and "16" in r.text
    # the transport's chips post the hidden `bars` with the steps: 16 bars
    # stay 16, and 1/32 keeps the longest grid that fits it
    form = {"span": "1", "bars": "16", "steps": "16", "cell.A1.0": "on"}
    client.post("/fragment/arrangement", data=form, headers=PERFORM)
    assert (s.arrangement.bars, s.arrangement.steps_per_bar) == (16, 16)
    client.post("/fragment/arrangement", data=form | {"steps": "32"}, headers=PERFORM)
    assert (s.arrangement.bars, s.arrangement.steps_per_bar) == (8, 32)
    # past the loop cap the doubling is refused in words, nothing moved
    s.arrangement = Arrangement(bars=8, steps_per_bar=16, bpm=50, cells=[["A1", 0]])
    r = client.post("/fragment/pattern/notes/duplicate", headers=PERFORM)
    assert r.status_code == 422 and "the cap is" in r.text
    assert s.arrangement.bars == 8


def test_a_long_loop_goes_to_the_song_and_back_whole(client):
    """A 16-bar loop → Song is one 16-bar pattern, and "Open in Perform"
    puts all 16 bars back on the grid (`song_bridge.to_loop`)."""
    notes = [[0, 1, 45, 1], [61, 2, 48, 1]]  # bar 1 and bar 16
    name = unquote(_take(client, notes=notes, bars="16").headers["X-Loop-Name"])
    s = _st(client)
    lane = loops.load(s.data_dir, name)["lane"]
    assert lane["bars"] == 16
    r = client.post("/loop/song", data={"name": name}, headers=PERFORM)
    assert r.status_code == 200
    (sec,) = s.song["sections"]
    assert sec["bars"] == 16
    client.post(f"/song/section/{sec['id']}/to-loop", headers=PERFORM)
    assert (s.arrangement.bars, s.arrangement.steps_per_bar) == (16, 16)
    assert _cells(s.arrangement.__dict__) == _cells(lane)


def test_a_take_longer_than_the_grid_says_what_the_grid_holds(client):
    r = _take(client, notes=LONG, bars="18")
    said = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert "18 bars" in said and "bars 1–16" in said and "bars 17–18" in said
    loop = loops.load(_st(client).data_dir, unquote(r.headers["X-Loop-Name"]))
    assert loop["lane"]["bars"] == 16 and loops.rest_bars(loop) == 2
    (row,) = [x for x in looplib.rows(_st(client).data_dir) if x["kind"] == "loop"]
    assert "+2 bars played" in row["facts"]["more"]
    # one that fits says nothing of the kind
    assert "HX-Trigger" not in _take(client, name="keys-2").headers


def test_to_song_lays_out_every_bar_played_a_section_each_grid(client):
    name = unquote(_take(client, notes=LONG, bars="18").headers["X-Loop-Name"])
    s = _st(client)
    r = client.post("/loop/song", data={"name": name}, headers=_hx("song"))
    assert r.status_code == 200
    said = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert "sections 1–2" in said and "18 bars" in said
    first, second = s.song["sections"]
    assert (first["bars"], second["bars"]) == (16, 2)
    assert (first["name"], second["name"]) == (f"{name} 1", f"{name} 2")
    (pid,) = [p for p in first["cells"].values() if p]
    assert s.song["patterns"][pid]["bars"] == 16  # the loop's grid: one pattern
    (pid,) = [p for p in second["cells"].values() if p]
    cells = {tuple(c) for c in s.song["patterns"][pid]["cells"]}
    # the chord held over the bar line (beats 63-66) goes on from the
    # section's first step for its last two beats; the late note is in bar 18
    assert {("C3", 0), ("C3", 7), ("E3", 0), ("E3", 7)} <= cells
    assert ("C3", 8) not in cells
    assert ("A3", 20) in cells  # beat 69 of the take: bar 18's second beat
    # one song undo step takes both sections back
    client.post("/song/undo", headers=_hx("song"))
    assert s.song["sections"] == []


def test_as_played_the_timing_reaches_the_rest_and_the_song(client):
    """Below 100 the bars past the grid keep their timing too (`rest_lanes`:
    a note held over a lane's line starts again on it), and → Song's
    patterns carry every lane's `note_feel`."""
    (lane,) = loops.rest_lanes(LONG, "4/4", 16, 16, 0)
    assert lane["note_feel"]["A3.20"] == [0.04, 0.04, 1.0]  # beat 69.01: late
    # the chord held over the line starts again on it, and ends where it did
    assert lane["note_feel"]["C3.0"] == [0.0, 0.0, 1.0]
    assert loops.rest_lanes(LONG, "4/4", 16, 16, 100)[0]["note_feel"] == {}
    name = unquote(_take(client, notes=LONG, bars="18").headers["X-Loop-Name"])
    s = _st(client)
    client.post("/loop/quantise", data={"name": name, "strength": "0"}, headers=PERFORM)
    loop = loops.load(s.data_dir, name)
    # the chord held over the grid's last line ends on it (cut there)
    assert loop["lane"]["note_feel"]["C3.252"] == [0.0, 0.0, 1.0]
    client.post("/loop/song", data={"name": name}, headers=PERFORM)
    first, second = s.song["sections"]
    pats = [
        s.song["patterns"][p]
        for sec in (first, second)
        for p in sec["cells"].values()
        if p
    ]
    assert [p.get("note_feel", {}) for p in pats] == [
        loop["lane"]["note_feel"],
        lane["note_feel"],
    ]


# --- like for like: ▶, Edit and → Song hear the same loop ---------------------------

# eight bars as played, off the grid: a held note, a hair late and early
PLAYED = [
    [0.02, 1.5, 45, 0.8],
    [0.96, 0.5, 48, 0.6],
    [2.1, 0.7, 52, 1.0],
    [12, 2, 57, 0.9],
    [29.5, 2.4, 55, 0.7],
]


def _three_renders(client, own_cells, *, follow=True, curve=True):
    """A loop recorded over a sound with FX on the main lane (drive, a
    delay), optionally a curve and a following layer, and a layer with
    notes of its own (`own_cells` on the loop's grid; it copies the FX);
    placed as played (Q 0). Its ▶ render (`takes._render_loop`: what ▶
    plays without audio of its own, or at another tempo, and what → Song
    as audio bounces), Edit's (Perform's render of the edited lane's
    synths) and its → Song section's — the section raw, so normalized as a
    loop is (`render._normalize`: a section is not, the song normalizes
    once, song-wide); at a gain under 1, so its WAV does not clip."""
    from synthlab.studio import render
    from synthlab.studio.routes import takes

    s, svc = _st(client), client.app.state.svc
    s.layers[s.active_layer]["fx"] = [{"kind": "drive"}, {"kind": "delay"}]
    if curve:
        s.curves = {"cutoff": [[0.0, 0.3], [1.0, 0.8]]}
    if follow:
        client.post("/fragment/layer/add", headers=PERFORM)  # follows the main lane
        s.set_follow(s.active_layer, -1)
    client.post("/fragment/layer/add", data={"own": "1"}, headers=PERFORM)
    s.arrangement = replace(s.arrangement, cells=own_cells)
    s.select_layer(0)
    name = unquote(_take(client, notes=PLAYED, bars="8").headers["X-Loop-Name"])
    r = client.post("/loop/quantise", data={"name": name, "strength": "0"})
    assert r.status_code == 200
    loop = loops.load(s.data_dir, name)
    assert loop["lane"]["bars"] == 8 and loop["lane"]["note_feel"]
    extras = loop["sound"][1]
    assert len(extras["layers"]) == 1 + follow and extras["main_fx"]
    assert bool(extras["curves"]) == curve

    played = takes._render_loop(svc, loop).audio()
    r = client.post("/loop/edit", data={"name": name}, headers=PERFORM)
    assert r.status_code == 200
    assert s.arrangement.note_feel == loop["lane"]["note_feel"]
    edited = svc.render("perform", parts=("synth",)).audio()

    r = client.post("/loop/song", data={"name": name}, headers=PERFORM)
    assert r.status_code == 200
    song, master = svc.song_snapshot()
    (sec,) = song["sections"]
    pats = [song["patterns"][p] for p in sec["cells"].values() if p]
    assert len(pats) == 2 + follow and sec["bars"] == 8
    assert any(p.get("note_feel") == loop["lane"]["note_feel"] for p in pats)
    raw = svc.section_result(song, 0, loop=True, gain=0.25, master_fx=master)
    heard = render._normalize(raw.audio() / 0.25)[0]
    return played, edited, heard


def test_like_for_like_play_edit_and_song_render_the_same_loop(client):
    """A loop's ▶, its Edit on the grid and its → Song section render the
    same notes — as played, each with its own feel (`note_feel`) — in the
    same sound:
    its patch, a following layer, a layer with notes of its own, the main
    lane's FX (the layer's copy too) and a curve. ▶ and Edit are one
    render; the section is the same samples, up to the 16-bit WAVs."""
    own = [["E2", 0], ["E2", 1], ["A2", 6], ["E2", 120], ["E2", 121]]
    played, edited, heard = _three_renders(client, own)
    np.testing.assert_array_equal(played, edited)  # one render
    assert played.shape == heard.shape
    assert np.abs(played - heard).max() < 1e-3


@pytest.mark.xfail(
    strict=True,
    reason="a lane's FX ring past its last note + 4 beats + 1 s is cut in a "
    "loop render (render.stem_fx runs over the dry render, Song.render's "
    "length) and rings on in a section rendered in chunks (render."
    "_cycle_stems: to its last chunk's start + 4 beats + 1 s). Either fix "
    "moves renders of existing keys: RENDER_REV",
)
def test_a_lanes_fx_ring_past_its_notes_is_not_yet_like_for_like(client):
    """Not built: the layer's notes are all in bar 1, its delay rings past
    bar 3 — cut there in ▶ and Edit, heard in the song's section."""
    played, edited, heard = _three_renders(
        client, [["E2", 0], ["E2", 1], ["A2", 6]], follow=False, curve=False
    )
    np.testing.assert_array_equal(played, edited)
    assert np.abs(played - heard).max() < 1e-3


def test_rest_lanes_are_empty_when_the_take_fits():
    assert loops.rest_lanes(NOTES, "4/4", 4, 16, 100) == []
    assert loops.played_bars(NOTES, "4/4") == 2
    assert loops.played_bars([], "4/4") == 0


def test_edit_from_a_following_lane_opens_on_the_main_lane(client):
    name = unquote(_take(client).headers["X-Loop-Name"])
    s = _st(client)
    s.select_layer(2)
    s.set_follow(2, 0)
    assert s.follows(s.active_layer)
    r = client.post("/loop/edit", data={"name": name}, headers=PERFORM)
    assert r.status_code == 200
    assert s.active_layer == s.main_index() == 0
    assert s.loop_edit_of()["name"] == name
    said = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert "on layer 1: layer 3 follows it" in said
