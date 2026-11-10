"""From the Loops page to the Song (studio `docs/song.md`, "Bridges",
"Drags onto a lane" and "The jam"): a scene →
a section, a loop dropped on a lane or on the timeline (`/loop/song` with
`track_id` / `at_bar` / `at_section`), the 8-track cap's choice, "already
in the song", and ⟲ Capture scenes. Every edit is one song undo step; a
question (`song-ask`) changes nothing."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import jam, limits, loops, song_bridge
from synthlab.studio import song_model as sm
from synthlab.studio.server import create_app
from synthlab.studio.state import StudioState
from synthlab.synth import Patch

DRUMS = 4


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


PERFORM, SONG = _hx("perform"), _hx("song")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _st(client) -> StudioState:
    return client.app.state.studio


def _loop(s, name, cells, *, bars=1, sound="Acid", cutoff=1234.0, **kw) -> str:
    arr = replace(s.arrangement, bars=bars, steps_per_bar=16, cells=cells, **kw)
    pay = {
        "format": 2,
        "patch": Patch(name=sound, cutoff=cutoff).to_json(),
        "layers": [],
        "main_fx": [],
        "curves": {},
    }
    return loops.save(s.data_dir, name, arr, pay)


def _beat(s, name="techno") -> str:
    return loops.save_beat(s.data_dir, name, s.drum_bank[0], s.arrangement)


def _scene(client, row=0, slots=None) -> None:
    s = _st(client)
    for lane, name in enumerate(slots or []):
        if name:
            s.set_slot(lane, row, name)


def _trig(r) -> dict:
    return json.loads(r.headers.get("HX-Trigger") or "{}")


def _ask(r) -> dict:
    assert r.status_code == 200, r.text
    return _trig(r)["song-ask"]


def _undo(client) -> None:
    assert client.post("/song/undo", headers=SONG).status_code == 200


# --- a scene → a section ----------


def test_a_scene_is_a_section_its_lanes_on_tracks_with_their_sounds_and_its_beat(
    client,
):
    s = _st(client)
    _loop(s, "hook", [["A4", 0]], sound="Lead", bars=2)
    _loop(s, "bass", [["A2", 0], ["A2", 8]], sound="Bass")
    _beat(s)
    _scene(client, 0, ["hook", "bass", None, None, "techno"])
    s.name_scene(0, "Drop")
    r = client.post("/fragment/perform/scene/song", data={"row": "0"}, headers=PERFORM)
    assert r.status_code == 200, r.text
    assert "section 1" in _trig(r)["studio-toast"]["message"]
    song = s.song
    (sec,) = song["sections"]
    assert sec["name"] == "Drop" and sec["bars"] == 2  # its longest loop
    kinds = {t["id"]: t for t in song["tracks"]}
    sounds = sorted(
        kinds[t]["patch"]["name"]
        for t, p in sec["cells"].items()
        if p and kinds[t]["kind"] == "synth"
    )
    assert sounds == ["Bass", "Lead"]
    drums = next(t for t in song["tracks"] if t["kind"] == "drums")
    beat = song["patterns"][sec["cells"][drums["id"]]]
    assert beat["kind"] == "drums" and beat["drums"]
    # the first into an empty song gives it its tempo and key
    assert song["bpm"] == s.arrangement.bpm and song["root"] == s.arrangement.root
    assert "bpm" not in sec
    _undo(client)
    assert s.song["sections"] == []


def test_a_scene_at_another_tempo_keeps_it_and_says_so(client):
    s = _st(client)
    _loop(s, "a", [["C4", 0]])
    _scene(client, 0, ["a"])
    client.post("/fragment/perform/scene/song", data={"row": "0"}, headers=PERFORM)
    s.arrangement = replace(s.arrangement, bpm=97.0)
    s.set_slot(0, 1, "a")
    s.set_slot(1, 1, _loop(s, "b", [["E4", 4]], sound="Pad"))
    r = client.post("/fragment/perform/scene/song", data={"row": "1"}, headers=PERFORM)
    assert "at its own 97 bpm" in _trig(r)["studio-toast"]["message"]
    assert s.song["sections"][1]["bpm"] == 97.0


def test_an_idea_goes_to_the_song_at_its_own_key(client):
    s = _st(client)
    _loop(s, "a", [["C4", 0]])
    idea = loops.save_idea(
        s.data_dir,
        "Verse idea",
        ["a", None, None, None, None],
        replace(s.arrangement, root="D2", scale_name="minor"),
    )
    r = client.post(
        "/fragment/perform/scene/song", data={"idea": idea}, headers=PERFORM
    )
    assert r.status_code == 200, r.text
    assert s.song["sections"][0]["name"] == "Verse idea"
    assert s.song["root"] == "D2" and s.song["scale_name"] == "minor"


def test_an_empty_scene_or_a_gone_idea_is_a_toast_and_nothing_moves(client):
    s = _st(client)
    r = client.post("/fragment/perform/scene/song", data={"row": "0"}, headers=PERFORM)
    assert r.status_code == 422
    r = client.post(
        "/fragment/perform/scene/song", data={"idea": "nope"}, headers=PERFORM
    )
    assert r.status_code == 422 and s.song["sections"] == []


# --- already in the song ----------


def test_to_song_twice_asks_to_place_it_again_and_again_reuses_the_pattern(client):
    s = _st(client)
    _loop(s, "riff", [["C4", 0], ["E4", 4]])
    assert (
        client.post("/loop/song", data={"name": "riff"}, headers=PERFORM).status_code
        == 200
    )
    pool = dict(s.song["patterns"])
    ask = _ask(client.post("/loop/song", data={"name": "riff"}, headers=PERFORM))
    assert "already in the song" in ask["message"] and "section 1" in ask["message"]
    assert len(s.song["sections"]) == 1  # a question changes nothing
    again = next(c for c in ask["choices"] if "vals" in c)
    assert again["path"] == "/loop/song" and again["vals"]["again"] == "1"
    r = client.post(again["path"], data=again["vals"], headers=PERFORM)
    assert r.status_code == 200 and len(s.song["sections"]) == 2
    assert s.song["patterns"].keys() == pool.keys()  # the same pattern, by reference
    a, b = (sec["cells"] for sec in s.song["sections"])
    assert a == b


def test_a_scene_already_in_the_song_asks_too(client):
    s = _st(client)
    _loop(s, "a", [["C4", 0]])
    _scene(client, 0, ["a"])
    client.post("/fragment/perform/scene/song", data={"row": "0"}, headers=PERFORM)
    ask = _ask(
        client.post("/fragment/perform/scene/song", data={"row": "0"}, headers=PERFORM)
    )
    assert "Place it again" in [c["label"] for c in ask["choices"]]
    assert len(s.song["sections"]) == 1


# --- the 8-track cap ----------


def _full(client) -> list[str]:
    s = _st(client)
    for i in range(limits.MAX_SYNTH_TRACKS):
        _loop(s, f"l{i}", [["C4", i]], sound=f"S{i}", cutoff=300.0 + i)
        client.post("/loop/song", data={"name": f"l{i}"}, headers=PERFORM)
    return [t["id"] for t in s.song["tracks"] if t["kind"] == "synth"]


def test_the_track_cap_is_a_choice_replace_or_keep_one_track(client):
    s = _st(client)
    synth = _full(client)
    assert len(synth) == limits.MAX_SYNTH_TRACKS
    _loop(s, "new", [["G4", 0]], sound="Fresh", cutoff=999.0)
    n = len(s.song["sections"])
    ask = _ask(client.post("/loop/song", data={"name": "new"}, headers=PERFORM))
    assert "where should “Fresh” go" in ask["message"]
    assert len(s.song["sections"]) == n
    picks = [c for c in ask["choices"] if "vals" in c]
    assert len(picks) == 2 * limits.MAX_SYNTH_TRACKS
    assert any(c.get("href") == "/view/song" for c in ask["choices"])
    keep = next(
        c for c in picks if json.loads(c["vals"]["onto"]) == [[synth[2], "keep"]]
    )
    r = client.post(keep["path"], data=keep["vals"], headers=PERFORM)
    assert r.status_code == 200, r.text
    track = s.song["tracks"][sm.track_index(s.song, synth[2])]
    assert track["patch"]["name"] == "S2"  # kept its sound
    assert s.song["sections"][-1]["cells"][synth[2]]
    _undo(client)
    rep = next(
        c for c in picks if json.loads(c["vals"]["onto"]) == [[synth[3], "replace"]]
    )
    client.post(rep["path"], data=rep["vals"], headers=PERFORM)
    track = s.song["tracks"][sm.track_index(s.song, synth[3])]
    assert track["patch"]["name"] == "Fresh"  # its sound replaced, everywhere
    _undo(client)
    assert s.song["tracks"][sm.track_index(s.song, synth[3])]["patch"]["name"] == "S3"


def test_a_hostile_onto_is_a_toast(client):
    _st(client)
    _full(client)
    _loop(_st(client), "new", [["G4", 0]], sound="Fresh", cutoff=999.0)
    for bad in (
        "[[1,2]]",
        "{}",
        "nope",
        json.dumps([["zz", "keep"]]),
        json.dumps([["x", "eat"]]),
    ):
        r = client.post(
            "/loop/song", data={"name": "new", "onto": bad}, headers=PERFORM
        )
        assert r.status_code == 422, bad


def test_the_cap_unit_names_the_lane_and_the_free_tracks():
    song = sm.new_song(120.0, "C", "major")
    for i in range(limits.MAX_SYNTH_TRACKS):
        sm.add_track(song, "synth", f"T{i}", patch={"name": f"P{i}"})
    lane = song_bridge.main_lane({"name": "New"}, {"cells": [["C4", 0]]}, {}, {})
    with pytest.raises(song_bridge.TrackCap) as err:
        song_bridge.place_lanes(song, [lane], {})
    assert err.value.sound == "New" and err.value.lane == 0
    assert len(err.value.free) == limits.MAX_SYNTH_TRACKS


# --- drops: on a lane, on the timeline ----------


def test_a_loop_dropped_on_a_lane_is_a_pattern_there_from_the_bar_in_its_sound(client):
    s = _st(client)
    _loop(s, "a", [["C4", 0]], bars=4)
    client.post("/loop/song", data={"name": "a"}, headers=PERFORM)
    sec = s.song["sections"][0]
    tid = next(t for t, p in sec["cells"].items() if p)
    _loop(s, "b", [["E4", 2]], sound="Other")
    tracks = len(s.song["tracks"])
    r = client.post(
        "/loop/song",
        data={"name": "b", "section_id": sec["id"], "track_id": tid, "at_bar": "2"},
        headers=SONG,
    )
    assert r.status_code == 200, r.text
    assert "from bar 3" in _trig(r)["studio-toast"]["message"]
    sec = s.song["sections"][0]
    assert len(s.song["tracks"]) == tracks  # the track's own sound plays it
    assert sm.cell_span(sec, tid) == (2, 2)
    assert s.song["patterns"][sec["cells"][tid]]["cells"] == [["E4", 2]]
    _undo(client)
    assert sm.cell_span(s.song["sections"][0], tid) == (0, 4)


def test_a_drop_at_bar_one_fills_the_section_and_a_beat_goes_on_the_drums(client):
    s = _st(client)
    _loop(s, "a", [["C4", 0]], bars=2)
    _beat(s)
    _scene(client, 0, ["a", None, None, None, "techno"])
    client.post("/fragment/perform/scene/song", data={"row": "0"}, headers=PERFORM)
    sec = s.song["sections"][0]
    drums = next(t["id"] for t in s.song["tracks"] if t["kind"] == "drums")
    synth = next(t["id"] for t in s.song["tracks"] if t["kind"] == "synth")
    _loop(s, "c", [["D4", 0]])
    r = client.post(
        "/loop/song",
        data={"name": "c", "section_id": sec["id"], "track_id": synth, "at_bar": "0"},
        headers=SONG,
    )
    assert r.status_code == 200 and sm.cell_span(s.song["sections"][0], synth) == (0, 2)
    r = client.post(
        "/loop/song",
        data={"name": "techno", "section_id": sec["id"], "track_id": synth},
        headers=SONG,
    )
    assert r.status_code == 422  # a beat goes on the drums
    r = client.post(
        "/loop/song",
        data={"name": "techno", "section_id": sec["id"], "track_id": drums},
        headers=SONG,
    )
    assert r.status_code == 200, r.text


def test_a_loop_dropped_on_the_timeline_is_a_new_section_there(client):
    s = _st(client)
    for n in ("a", "b"):
        _loop(s, n, [["C4", ord(n) - 96]])
        client.post("/loop/song", data={"name": n}, headers=PERFORM)
    _loop(s, "mid", [["G4", 0]])
    r = client.post("/loop/song", data={"name": "mid", "at_section": "1"}, headers=SONG)
    assert r.status_code == 200, r.text
    assert [x["name"] for x in s.song["sections"]] == ["a", "mid", "b"]
    # an explicit place never asks "already in the song"
    r = client.post("/loop/song", data={"name": "mid", "at_section": "0"}, headers=SONG)
    assert "song-ask" not in _trig(r)
    assert [x["name"] for x in s.song["sections"]][0] == "mid"
    _undo(client)
    assert [x["name"] for x in s.song["sections"]] == ["a", "mid", "b"]


def test_a_beat_to_song_is_a_section_with_it_on_the_drums(client):
    s = _st(client)
    _beat(s)
    r = client.post("/loop/song", data={"name": "techno"}, headers=PERFORM)
    assert r.status_code == 200, r.text
    (sec,) = s.song["sections"]
    drums = next(t["id"] for t in s.song["tracks"] if t["kind"] == "drums")
    assert sec["cells"][drums]


# --- ⟲ Capture scenes ----------


def _capture(client, events, bars):
    return client.post(
        "/fragment/perform/scene/capture",
        data={"events": json.dumps(events), "bars": str(bars)},
        headers=PERFORM,
    )


def test_capture_makes_the_scenes_as_played_sections_in_order(client):
    s = _st(client)
    _loop(s, "a", [["C4", 0]])
    _loop(s, "b", [["E4", 0]], sound="Pad", bars=2)
    one = ["a", None, None, None, None]
    two = ["a", "b", None, None, None]
    log = [
        {"bar": 0, "slots": one, "name": "Intro"},
        {"bar": 4, "slots": two, "name": "Drop"},
        {"bar": 8, "slots": one, "name": "Intro"},
        {"bar": 9},
    ]
    r = _capture(client, log, 10)
    assert r.status_code == 200, r.text
    trig = _trig(r)
    secs = s.song["sections"]
    assert trig["scenes-captured"]["sections"] == [x["id"] for x in secs]
    assert [(x["name"], x["bars"], x["repeats"]) for x in secs] == [
        ("Intro", 1, 4),
        ("Drop", 2, 2),
        ("Intro", 1, 1),
    ]
    assert secs[0]["cells"] == secs[2]["cells"]  # by reference
    _undo(client)
    assert s.song["sections"] == []


def test_capture_refuses_a_bad_log_whole_and_keeps_it(client):
    s = _st(client)
    _loop(s, "a", [["C4", 0]])
    for bad in (
        "nope",
        json.dumps([{"bar": -1, "slots": ["a", None, None, None, None]}]),
        json.dumps([{"bar": 0, "slots": ["a"]}]),
        json.dumps([{"bar": 0, "slots": ["gone", None, None, None, None]}]),
        json.dumps([{"bar": "0"}]),
    ):
        r = client.post(
            "/fragment/perform/scene/capture",
            data={"events": bad, "bars": "4"},
            headers=PERFORM,
        )
        assert r.status_code == 422, bad
        assert "scenes-captured" not in _trig(r)
    r = _capture(client, [], 4)
    assert r.status_code == 422 and s.song["sections"] == []


def test_the_jam_capture_takes_sections_from_outside_the_song():
    song = sm.new_song(120.0, "C", "major")
    tid = sm.add_track(song, "synth", "S", patch={"name": "P"})
    pid = sm.add_pattern(
        song, "notes", "p", {"bars": 1, "steps_per_bar": 16, "cells": [["C4", 0]]}
    )
    sid = sm.add_section(song, "Scene", 1)
    sm.set_cell(song, sid, tid, pid)
    out = song["sections"].pop()
    out["bpm"] = 90.0
    ids, _loose = jam.commit(song, [{"bar": 0, "scene": sid}], 3, {sid: out})
    (sec,) = song["sections"]
    assert sec["id"] == ids[0] != sid and sec["repeats"] == 3 and sec["bpm"] == 90.0
    assert sec["cells"][tid] == pid
