"""Song creation, the foundations everything else builds on: the model's
new nodes are optional and ABSENT WHEN UNSET — so no song saved before
them gains a key and no key or content hash moves — hostile values are
clamped or dropped, a pool pattern may be 8 bars while it fits the grid,
and the drawer opens a pattern on the octave its notes are in."""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio import limits, schema
from synthlab.studio import song_model as sm
from synthlab.studio.render import section_audio
from synthlab.studio.server import create_app
from synthlab.studio.state import ROOTS, Arrangement, pitch_rows

from .test_song_routes import HX, post, song, song_dict, use_song

N = schema.nodes()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        yield c


def _clean(raw: dict) -> dict:
    return schema.conform(copy.deepcopy(raw), N.SONG)


# --- absent when unset ---------------------------------------------------------


def test_a_song_from_before_the_new_nodes_keeps_its_bytes_and_its_hashes():
    old = _clean(song_dict())
    blob = json.dumps(old, sort_keys=True)
    for key in ("title", "chords", "returns", "gen"):
        assert key not in old
    for t in old["tracks"]:
        assert not {"role", "pan", "sends"} & set(t)
    for sec in old["sections"]:
        assert not {"energy", "chords"} & set(sec)
    for p in old["patterns"].values():
        assert not {"gen", "fam", "var", "strum", "hits", "swing"} & set(p)
    # ... and unset values written out explicitly conform back to nothing
    loud = copy.deepcopy(old)
    loud |= {"title": "", "chords": None, "returns": [], "gen": None}
    for t in loud["tracks"]:
        t |= {"role": None, "pan": 0.0, "sends": {}}
    for sec in loud["sections"]:
        sec |= {"energy": None, "chords": {"bars": 4, "slots": []}}
    for p in loud["patterns"].values():
        p |= {"gen": None, "fam": None, "var": None}
        p |= {"hits": {}, "swing": {}} if p["kind"] == "drums" else {"strum": 0.0}
    assert json.dumps(_clean(loud), sort_keys=True) == blob
    assert "title" not in N.SONG.default() and "chords" not in N.SECTION.default()


def test_the_new_nodes_are_no_render_input():
    """A role, a title, a chord track, a pattern's `gen`: the section's
    render key and audio are the section's without them. (A track's pan
    and sends were on this list until the mixer rendered them:
    tests/studio/test_mixer_render.py holds them to "absent means absent"
    instead.)"""
    from synthlab.studio.render import section_key

    plain = _clean(song_dict())
    rich = copy.deepcopy(plain)
    rich["title"] = "Night Drive"
    rich["chords"] = {"bars": 4, "slots": [{"at": 0, "deg": 1, "len": 16}]}
    rich["gen"] = {"kind": "start", "seed": 7, "params": {"style": "trance"}}
    rich["tracks"][1] |= {"role": "bass"}
    rich["sections"][0] |= {"energy": 0.8}
    for p in rich["patterns"].values():
        p["gen"] = {"kind": "bass_rolling", "seed": 3, "params": {"figure": "roll"}}
        p["var"] = "A"
    rich = _clean(rich)
    assert rich["tracks"][1]["role"] == "bass" and rich["title"] == "Night Drive"
    for a, b in zip(plain["sections"], rich["sections"], strict=True):
        ka = section_key(sm.section_arrangement(plain, a), None)
        kb = section_key(sm.section_arrangement(rich, b), None)
        assert ka == kb


# --- hostile values ------------------------------------------------------------


def test_hostile_creation_values_are_clamped_or_dropped():
    raw = song_dict()
    raw["title"] = "x" * 500
    raw["chords"] = {
        "bars": 7,  # not a choice: the default 4
        "slots": [
            {"at": 99, "deg": 3, "len": 4},  # past the end: dropped
            {"at": 4, "deg": 99, "q": "nope", "inv": 9, "len": 400},
            {"at": 4.1, "deg": 2},  # the same quarter beat: one a start
            {"at": 0, "deg": 1, "len": float("nan")},
            {"deg": 5},  # no `at`
            "junk",
        ],
    }
    raw["returns"] = [
        {"id": "aaaaaaaa", "name": "A", "fx": [{"kind": "reverb"}]},
        {"id": "aaaaaaaa"},
        {"id": "bbbbbbbb"},
        {"id": "cccccccc"},
        {"id": "nope"},
    ]
    raw["gen"] = {"kind": "", "seed": 1}
    raw["tracks"][1] |= {
        "role": "kazoo",
        "pan": 9,
        "sends": {"aaaaaaaa": 2.0, "zzzzzzzz": 0.5, "bbbbbbbb": 0.0},
    }
    raw["sections"][0] |= {"energy": 7, "chords": "nope"}
    raw["patterns"]["pd"] |= {
        "hits": {
            "kick.0": {"v": 9, "p": -1, "r": 99, "n": 3, "t": 99, "x": 1},
            "kick.5": {"v": 0.3},  # no cell on that step: dropped
            "kazoo.0": {"v": 0.5},
            "kick.9999": {"v": 0.5},
            "kick": 1,
        },
        "swing": {"hat": 3, "kazoo": 0.2},
        "var": "Z",
        "fam": 12,
        "gen": {
            "kind": "beat",
            "seed": -5,
            "params": {"a" * 40: 1, "ok": [1], "d": 0.5},
        },
    }
    raw["patterns"]["p1"] |= {"strum": 999, "bars": 8, "steps_per_bar": 32}
    out = _clean(raw)
    assert schema.conform_strict(out, N.SONG) == out  # a fixed point
    assert len(out["title"]) == limits.MAX_TITLE_CHARS
    slots = out["chords"]["slots"]
    assert out["chords"]["bars"] == 4
    assert [(s["at"], s["deg"]) for s in slots] == [(0.0, 1), (4.0, 7)]
    assert slots[1] == {"at": 4.0, "deg": 7, "q": "triad", "inv": 2, "len": 12.0}
    assert [r["id"] for r in out["returns"]] == ["aaaaaaaa", "bbbbbbbb"]
    assert "gen" not in out
    t = out["tracks"][1]
    assert "role" not in t and t["pan"] == 1.0 and t["sends"] == {"aaaaaaaa": 1.0}
    assert out["sections"][0]["energy"] == 1.0 and "chords" not in out["sections"][0]
    pd = out["patterns"]["pd"]
    assert pd["hits"] == {"kick.0": {"v": 1.0, "p": 0.0, "r": 4, "n": 0.5, "t": 12.0}}
    assert pd["swing"] == {"hat": 0.6}
    assert "var" not in pd and "fam" not in pd
    assert pd["gen"] == {"kind": "beat", "seed": 0, "params": {"a" * 16: 1, "d": 0.5}}
    p1 = out["patterns"]["p1"]
    assert p1["strum"] == 60.0
    assert p1["bars"] == 4  # 8 bars x 32 steps does not fit the grid
    events = schema.conform(
        [{"bar": 3, "track": "aaaaaaaa", "target": "bbbbbbbb"}, {"track": 1}, 7],
        N.JAM_EVENTS,
    )
    assert events == [
        {"bar": 3, "track": "aaaaaaaa", "scene": None, "target": "bbbbbbbb"}
    ]
    assert (
        len(schema.conform([{"bar": 1}] * 9999, N.JAM_EVENTS)) == limits.MAX_JAM_EVENTS
    )


def test_a_send_to_a_return_the_song_does_not_have_is_dropped():
    raw = song_dict()
    raw["tracks"][1]["sends"] = {"aaaaaaaa": 0.5}
    assert "sends" not in _clean(raw)["tracks"][1]


def test_every_semitone_is_a_root():
    for root in ROOTS:
        s = sm.new_song(120.0, root, "phrygian")
        assert s["root"] == root and s["scale_name"] == "phrygian"
        assert len(pitch_rows(Arrangement(root=root, scale_name="minor"), 2)) == 15
    assert sm.new_song(120.0, "H9", "minor")["root"] == "A1"  # the default


# --- 8-bar pool patterns -------------------------------------------------------


def _eight() -> dict:
    """One section whose main lane is an 8-bar pattern: bars 1-4 and 5-8
    differ, so a render that read only four would be heard."""
    raw = song_dict()
    raw["patterns"]["p8"] = {
        "id": "p8",
        "kind": "notes",
        "name": "Long",
        "bars": 8,
        "steps_per_bar": 16,
        "cells": [["A2", 0], ["C3", 32], ["E3", 64], ["A2", 127]],
    }
    raw["sections"] = [
        {"id": "s1", "name": "A", "bars": 8, "cells": {"td": "pd", "t1": "p8"}}
    ]
    return _clean(raw)


def test_an_eight_bar_pattern_is_kept_and_renders_all_eight_bars():
    s = _eight()
    assert s["patterns"]["p8"]["bars"] == 8 and s["sections"][0]["bars"] == 8
    parts = sm.section_arrangement(s, s["sections"][0])
    assert parts.cycle_bars == 8 and parts.arr.bars == 8
    assert [c[1] for c in parts.arr.cells] == [0, 32, 64, 127]
    audio = section_audio(parts)
    n = sm.cycle_samples(8, s["bpm"])
    assert len(audio.base) == n
    half = n // 2
    late = np.abs(audio.base[half:]).max()
    assert late > 1e-3  # bars 5-8 sound: the E3 at bar 5, the A2 at the end
    # the same notes by hand in two 4-bar sections' worth of grid: the 8-bar
    # pattern is not a 4-bar one played twice
    four = copy.deepcopy(s)
    four["patterns"]["p8"] |= {"bars": 4}
    four = _clean(four)
    assert sm.cycle_bars(four, four["sections"][0]) == 4


def test_the_drawer_offers_eight_bars_only_where_they_fit(client):
    use_song(client, _eight())
    post(client, "/song/drawer", {"section_id": "s1", "track_id": "t1"})
    html = client.get("/view/song", headers=HX).text
    drawer = html[html.index('id="song-drawer"') :]
    assert drawer.count('class="barchip') >= 9  # 1 2 3 4 8 and four step chips
    assert 'hx-vals=\'{"bars": "8"}\'' in drawer
    # finer steps: the pattern is held to four bars, never past the grid
    r = post(client, "/song/pattern/p8/grid", {"bars": "8", "steps": "32"})
    assert r.status_code == 200
    p = song(client)["patterns"]["p8"]
    assert (p["bars"], p["steps_per_bar"]) == (4, 32)
    post(client, "/song/drawer", {"section_id": "s1", "track_id": "td"})
    r = post(
        client,
        "/song/pattern/pd/grid",
        {"bars": "8", "steps": "16", "drum.kick.100": "on"},
    )
    assert r.status_code == 200
    pd = song(client)["patterns"]["pd"]
    assert pd["bars"] == 8 and ["kick", 100] in pd["drums"]


def test_open_in_perform_opens_an_eight_bar_pattern_as_its_first_four(client):
    use_song(client, _eight())
    r = post(client, "/song/section/s1/to-loop")
    assert r.status_code == 200
    arr = client.app.state.studio.arrangement
    assert arr.bars == 4
    assert ["E3", 64] in arr.cells  # kept, hidden past the loop's grid


# --- the drawer's octave window ------------------------------------------------


def test_the_drawer_opens_a_pattern_on_the_octave_its_notes_are_in(client):
    raw = song_dict()
    raw["root"], raw["scale_name"] = "A1", "minor"
    raw["patterns"]["p1"] |= {
        "bars": 1,
        "steps_per_bar": 16,
        "cells": [["A4", 0], ["C5", 4], ["E5", 8], ["A1", 12]],
    }
    use_song(client, raw)
    s = song(client)
    p = s["patterns"]["p1"]
    assert sm.window_octave(s, p, 2) == 2  # A3..A5 holds three of the four
    assert sm.window_octave(s, {"cells": []}, 2) == 0
    assert sm.window_rows(s, 2, 0) == pitch_rows(
        Arrangement(root="A1", scale_name="minor"), 2
    )
    client.app.state.studio.grid_span = 2
    post(client, "/song/drawer", {"section_id": "s1", "track_id": "t1"})
    html = client.get("/view/song", headers=HX).text
    drawer = html[html.index('id="song-drawer"') :]
    assert 'name="oct" value="2"' in drawer
    assert 'name="cell.C5.4"' in drawer and "checked" in drawer
    assert 'data-hidden-count="1"' in drawer  # the low A1 is the one outside
    # a post from that window edits those rows and keeps the hidden note
    form = {"bars": "1", "steps": "16", "span": "2", "oct": "2"}
    form |= {"cell.A4.0": "on", "cell.G5.2": "on"}
    assert post(client, "/song/pattern/p1/grid", form).status_code == 200
    cells = sorted(map(tuple, song(client)["patterns"]["p1"]["cells"]))
    assert cells == [("A1", 12), ("A4", 0), ("G5", 2)]
    # a hostile window is held to -1..+3 and a row outside it is not written
    form = {"bars": "1", "steps": "16", "span": "2", "oct": "99", "cell.A9.0": "on"}
    assert post(client, "/song/pattern/p1/grid", form).status_code == 200
    assert ("A9", 0) not in set(map(tuple, song(client)["patterns"]["p1"]["cells"]))
