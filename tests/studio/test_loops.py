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
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import keytakes, looplib, loops
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
    assert loop["quantise"] == 100 and loop["edited"] is False
    assert loop["key"]["bpm"] == 120
    assert loop["sound"] is not None  # the sound that played it


def test_the_strength_places_a_note_from_where_it_began_to_its_nearest_step():
    late, early = [[0.06, 0.25, 60, 1]], [[0.96, 0.25, 62, 1]]  # 0.24 / 3.84 steps

    def starts(notes, q):
        lane = loops.place(notes, "4/4", 1, 16, q)
        return sorted(int(s) for _p, s in lane["cells"])

    assert starts(late, 0) == [0] and starts(late, 100) == [0]
    # as played, a note a hair before beat 2 is in the step it began in
    assert starts(early, 0) == [3]
    assert starts(early, 25) == [3]  # 0.16 off is past 25% of half a step
    assert starts(early, 50) == [4] and starts(early, 100) == [4]
    # the rate: eighths in 4/4, and in 3/4 the meter's own steps
    assert loops.place(early, "4/4", 1, 8, 100)["cells"] == [["D4", 2]]
    assert [n for n, _w in loops.rates("3/4")] == [3, 6, 12, 24]
    assert [w for _n, w in loops.rates("4/4")] == ["1/4", "1/8", "1/16", "1/32"]
    assert loops.grid_bars(2.3, 16) == 3 and loops.grid_bars(8, 16) == 4
    assert loops.grid_bars(4, 32) == 4  # 128 steps: the grid's cap


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
    assert "lp-edit-note" in r.text and "keys-1" in r.text  # the banner
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
    assert ("C2", 3) in _cells(loop["lane"])  # where it began, a hair early
    assert loop["quantise"] == 0 and loop["played"] == NOTES
    assert ("C2", 3) in [(p, int(st)) for p, st in s.arrangement.cells]
    client.post(
        "/loop/quantise",
        data={"name": "keys-1", "steps": "8", "strength": "100"},
        headers=PERFORM,
    )
    loop = loops.load(s.data_dir, "keys-1")
    assert loop["lane"]["steps_per_bar"] == 8 and ("C2", 2) in _cells(loop["lane"])
    assert loop["edited"] is False  # placed, not edited by hand
    # a rate the meter has not, a strength past 100: refused, nothing moved
    for bad in ({"steps": "12"}, {"strength": "101"}, {"steps": "x"}):
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
    assert 'class="lp-roll"' in page  # each row's notes, a mini piano roll
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
    plays = re.findall(r"<button[^>]*aria-label=\"hear old\"[^>]*>", page)
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
