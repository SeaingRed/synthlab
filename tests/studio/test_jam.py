"""A jam written back as sections (`studio/jam.py`, `POST
/song/jam/commit`).

Held here:

- the log read as parts (`jam.timeline`): a change every bar, none at
  all, a stop, a scene, a rest, an event that has not happened yet, the
  cap, an id the song does not have;
- **what a captured section plays**: bar for bar the notes the launcher
  looped (`heard` against `played`, both read off the patterns — no
  render: the launcher's audio is the cell render the Song page already
  pins), by reference where a pattern fits, a window where it does not,
  repeats where a loop went round, two parts for 5-7 bars of an 8-bar
  pattern, several for a part longer than a section may be;
- lanes and trimmed blocks come along only with a whole pass;
- the route: one undo step, the toast, `jam-captured`, the refusals (a
  422, the song untouched).

Nobody has listened: this says the captured NOTES are the launched ones.
The launcher's quantised scheduling is `test_jam_schedule.py` (node).
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import jam, limits, schema
from synthlab.studio import song_model as sm
from synthlab.studio.server import create_app
from tests.studio.test_song_routes import HX, song_dict, use_song


def clean(song: dict) -> dict:
    return schema.conform(song, schema.nodes().SONG)


def wide() -> dict:
    """The fixture song plus an 8-bar section (an 8-bar line on t2, a
    4-bar one on t1) and a 4-bar one with a lane and a trimmed block."""
    song = song_dict()
    song["patterns"]["p8"] = {
        "id": "p8",
        "kind": "notes",
        "name": "Long",
        "bars": 8,
        "steps_per_bar": 16,
        "cells": [[f"A{1 + b % 3}", b * 16 + b] for b in range(8)],
    }
    song["patterns"]["p4"] = {
        "id": "p4",
        "kind": "notes",
        "name": "Four",
        "bars": 4,
        "steps_per_bar": 16,
        "cells": [["C2", b * 16 + 2 * b] for b in range(4)],
    }
    song["sections"].append(
        {
            "id": "s3",
            "name": "Drop",
            "bars": 8,
            "cells": {"td": "pd", "t1": "p4", "t2": "p8"},
        }
    )
    song["sections"].append(
        {
            "id": "s4",
            "name": "Break",
            "bars": 4,
            "cells": {"t1": "p4", "t2": "p2"},
            "auto": {"t1": [{"target": "cutoff", "points": [[0, 0.2], [1, 0.9]]}]},
            "spans": {"t2": [2, 2]},
        }
    )
    return clean(song)


def bar_of(p: dict, bar: int) -> frozenset:
    """What one bar of a pattern holds: (voice or pitch, step in the bar)."""
    per = int(p["steps"] if p["kind"] == "drums" else p["steps_per_bar"])
    cells = p["drums"] if p["kind"] == "drums" else p["cells"]
    lo = (bar % sm.pattern_bars(p)) * per
    return frozenset(
        (c[0], int(c[1]) - lo) for c in cells if lo <= int(c[1]) < lo + per
    )


def heard(song: dict, sid: str, tid: str, launched: int, bar: int) -> frozenset:
    """What the launcher plays on `tid` at jam bar `bar`: its cell of
    `sid`, looped as the section's cycle from bar `launched`."""
    sec = next(s for s in song["sections"] if s["id"] == sid)
    p = song["patterns"][sec["cells"][tid]]
    return bar_of(p, (bar - launched) % sm.cycle_bars(song, sec))


def played(song: dict, ids: list[str]) -> list[dict]:
    """The new sections, a bar at a time: {track: that bar's notes}."""
    out = []
    for sid in ids:
        sec = next(s for s in song["sections"] if s["id"] == sid)
        for _rep in range(int(sec["repeats"])):
            for bar in range(int(sec["bars"])):
                row = {}
                for tid, pid in sec["cells"].items():
                    if pid is not None:
                        row[tid] = bar_of(song["patterns"][pid], bar)
                out.append(row)
    return out


def check(song: dict, events: list[dict], bars: int):
    """Capture, then hold every bar of every part to what was launched."""
    before = copy.deepcopy(song)
    parts = jam.timeline(before, events, bars)
    ids, loose = jam.commit(song, events, bars)
    clean(song)  # what the route would commit is schema-clean
    rows = played(song, ids)
    at = parts[0][0]
    assert len(rows) == parts[-1][1] - at
    for a, b, now in parts:
        for bar in range(a, b):
            want = {
                t: heard(before, sid, t, launched, bar)
                for t, (sid, launched) in now.items()
            }
            assert rows[bar - at] == want, f"bar {bar}"
    return ids, loose


def sections(song: dict, ids: list[str]) -> list[dict]:
    return [s for s in song["sections"] if s["id"] in ids]


# --- the log as parts -------------------------------------------------------------


def test_nothing_launched_is_no_part_and_a_refusal():
    song = wide()
    assert jam.timeline(song, [], 8) == []
    with pytest.raises(sm.SongError, match="Nothing has been launched"):
        jam.commit(song, [], 8)
    # an event at or past the end has not happened yet
    assert jam.timeline(song, [{"bar": 8, "scene": "s1"}], 8) == []


def test_a_scene_sets_every_track_and_a_stop_ends_the_jam():
    song = wide()
    events = [{"bar": 0, "scene": "s1"}, {"bar": 4, "scene": "s2"}, {"bar": 8}]
    parts = jam.timeline(song, events, 32)
    assert [(a, b) for a, b, _ in parts] == [(0, 4), (4, 8)]  # no trailing rest
    assert parts[0][2] == {"td": ("s1", 0), "t1": ("s1", 0)}  # t2 is empty in s1
    assert parts[1][2] == {"td": ("s2", 4), "t1": ("s2", 4), "t2": ("s2", 4)}


def test_a_track_event_a_track_stop_and_a_rest_between():
    song = wide()
    events = [
        {"bar": 2, "track": "t1", "target": "s1"},
        {"bar": 3, "track": "td", "target": "s2"},
        {"bar": 5, "track": "t1"},
        {"bar": 6, "track": "td"},
        {"bar": 8, "track": "t2", "target": "s2"},
    ]
    parts = jam.timeline(song, events, 10)
    assert [(a, b, sorted(now)) for a, b, now in parts] == [
        (2, 3, ["t1"]),
        (3, 5, ["t1", "td"]),
        (5, 6, ["td"]),
        (6, 8, []),  # silence between two launches is a part
        (8, 10, ["t2"]),
    ]


def test_an_event_that_changes_nothing_is_no_boundary_but_a_relaunch_is():
    song = wide()
    quiet = [{"bar": 0, "track": "t1", "target": "s1"}, {"bar": 2, "track": "t2"}]
    assert len(jam.timeline(song, quiet, 4)) == 1
    again = [{"bar": 0, "scene": "s1"}, {"bar": 3, "scene": "s1"}]
    assert [(a, b) for a, b, _ in jam.timeline(song, again, 4)] == [(0, 3), (3, 4)]


def test_an_empty_cell_launched_is_a_stop_and_unknown_ids_refuse():
    song = wide()
    events = [{"bar": 0, "scene": "s2"}, {"bar": 2, "track": "t2", "target": "s1"}]
    assert sorted(jam.timeline(song, events, 4)[1][2]) == ["t1", "td"]
    for bad in (
        {"bar": 0, "scene": "nope"},
        {"bar": 0, "track": "nope", "target": "s1"},
        {"bar": 0, "track": "t1", "target": "nope"},
        {"bar": 0, "track": "ta", "target": "s1"},  # an audio track: no cells
    ):
        with pytest.raises(sm.SongError, match="no longer has"):
            jam.timeline(song, [bad], 4)


def test_events_out_of_order_are_read_in_bar_order_and_the_cap_refuses():
    song = wide()
    events = [{"bar": 4, "scene": "s2"}, {"bar": 0, "scene": "s1"}]
    assert [a for a, _b, _n in jam.timeline(song, events, 8)] == [0, 4]
    many = [{"bar": i, "scene": "s1"} for i in range(limits.MAX_JAM_EVENTS + 1)]
    with pytest.raises(sm.SongError, match="at most"):
        jam.timeline(song, many, 9999)


# --- what a captured section plays ------------------------------------------------


def test_a_scene_looped_is_its_section_repeated_by_reference():
    song = wide()
    pool = set(song["patterns"])
    ids, loose = check(song, [{"bar": 0, "scene": "s3"}], 16)
    (sec,) = sections(song, ids)
    assert (sec["bars"], sec["repeats"], sec["name"]) == (8, 2, "Drop")
    assert sec["cells"] == {"td": "pd", "t1": "p4", "t2": "p8"}
    assert set(song["patterns"]) == pool and not loose  # the pool gained nothing


def test_a_whole_pass_brings_its_lane_and_its_trimmed_block():
    song = wide()
    ids, loose = jam.commit(song, [{"bar": 0, "scene": "s4"}], 8)
    (sec,) = sections(song, ids)
    assert (sec["bars"], sec["repeats"]) == (4, 2) and not loose
    assert sec["spans"] == {"t2": [2, 2]}
    assert sec["auto"]["t1"][0]["target"] == "cutoff"
    assert sec["auto"]["t1"] is not song["sections"][3]["auto"]["t1"]  # a copy
    clean(song)


def test_a_part_that_is_not_a_whole_pass_leaves_the_lane_and_says_so():
    song = wide()
    ids, loose = jam.commit(song, [{"bar": 0, "scene": "s4"}], 3)
    (sec,) = sections(song, ids)
    assert sec["bars"] == 3 and loose
    assert "auto" not in sec and "spans" not in sec


def test_a_change_every_bar_windows_what_is_mid_loop():
    song = wide()
    events = [
        {"bar": 0, "track": "t2", "target": "s3"},  # the 8-bar line
        {"bar": 1, "track": "td", "target": "s1"},
        {"bar": 2, "track": "t1", "target": "s3"},  # the 4-bar line
        {"bar": 3, "track": "td"},
    ]
    ids, _loose = check(song, events, 4)
    secs = sections(song, ids)
    assert [s["bars"] for s in secs] == [1, 1, 1, 1]
    # bar 0 of the line is a crop: the line itself (a 1-bar section plays
    # its first bar), no copy in the pool; bars 1-3 are windows from their phase
    names = [song["patterns"][s["cells"]["t2"]]["name"] for s in secs]
    assert names == ["Long", "Long ↻", "Long ↻", "Long ↻"]
    assert len({s["cells"]["t2"] for s in secs}) == 4
    assert secs[1]["cells"]["td"] == "pd"  # a 1-bar beat fits: by reference
    assert [s["name"] for s in secs] == ["Drop", "Jam 2", "Jam 3", "Drop"]


def test_a_rotation_keeps_a_loop_that_was_joined_late():
    song = wide()
    events = [
        {"bar": 0, "track": "t1", "target": "s3"},
        {"bar": 2, "track": "td", "target": "s3"},  # t1 is two bars into four
    ]
    ids, _loose = check(song, events, 10)
    secs = sections(song, ids)
    assert [(s["bars"], s["repeats"]) for s in secs] == [(2, 1), (8, 1)]
    rot = song["patterns"][secs[1]["cells"]["t1"]]
    assert rot["bars"] == 4 and rot["id"] != "p4"  # p4 from its third bar


def test_five_to_seven_bars_of_an_eight_bar_pattern_are_two_parts():
    for n in (5, 6, 7):
        song = wide()
        events = [{"bar": 0, "scene": "s3"}, {"bar": n, "scene": "s1"}]
        ids, _loose = check(song, events, n + 2)
        got = [(s["bars"], s["repeats"]) for s in sections(song, ids)]
        assert got == [(4, 1), (n - 4, 1), (1, 2)]  # s1 loops a bar: twice


def test_a_part_longer_than_a_section_is_several():
    song = wide()
    cap = jam._cap(song)
    assert cap == 60  # 120 s at 120 bpm, in fours (MAX_SECTION_BARS is 64)
    # t1 is one bar into its 4-bar loop for good: no repeats, so the caps
    events = [
        {"bar": 0, "track": "t1", "target": "s3"},
        {"bar": 1, "track": "td", "target": "s1"},
    ]
    ids, _loose = check(song, events, 1 + cap + 6)
    assert [s["bars"] for s in sections(song, ids)] == [1, cap, 6]


def test_repeats_past_the_cap_carry_on_in_another_section():
    song = wide()
    n = limits.MAX_SECTION_REPEATS + 3
    ids, _loose = check(song, [{"bar": 0, "scene": "s1"}], n)
    assert [(s["bars"], s["repeats"]) for s in sections(song, ids)] == [
        (1, limits.MAX_SECTION_REPEATS),
        (1, 3),
    ]


def test_a_rest_is_an_empty_section():
    song = wide()
    events = [{"bar": 0, "scene": "s1"}, {"bar": 2}, {"bar": 5, "scene": "s1"}]
    ids, _loose = check(song, events, 6)
    rest = sections(song, ids)[1]
    assert (rest["name"], rest["bars"]) == ("Jam rest", 3)
    assert not any(rest["cells"].values())


def test_more_parts_than_the_song_has_room_for_refuses_and_says_how_many():
    song = wide()
    room = limits.MAX_SECTIONS - len(song["sections"])
    events = [
        {"bar": i, "track": "t1", "target": ("s1", "s3")[i % 2]}
        for i in range(room + 1)
    ]
    with pytest.raises(sm.SongError, match=f"{room} sections the song has room for"):
        jam.commit(copy.deepcopy(song), events, room + 1)
    ids, _loose = check(song, events[:room], room)
    assert len(ids) == room


# --- the route --------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c, wide())
        yield c


def post(client, events, bars):
    data = {"events": json.dumps(events), "bars": str(bars)}
    return client.post("/song/jam/commit", data=data, headers=HX)


def test_capture_is_one_undo_step_and_says_what_it_made(client):
    s = client.app.state.studio
    before = copy.deepcopy(s.song)
    depth = s.song_undo_state()["depth"]
    events = [
        {"bar": 0, "scene": "s1"},
        {"bar": 2, "track": "t2", "target": "s2"},
        {"bar": 4, "scene": "s3"},
    ]
    r = post(client, events, 12)
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert trig["studio-toast"]["message"] == "3 sections added from the jam."
    ids = trig["jam-captured"]["sections"]
    assert [x["id"] for x in s.song["sections"][-3:]] == ids
    assert "song-changed" in trig
    assert 'id="song-arranger"' in r.text  # the playlist comes back with them
    assert s.song_undo_state()["depth"] == depth + 1
    assert s.song_undo_state()["undo"] == "capture the jam"
    assert client.post("/song/undo").status_code == 200
    assert s.song == before


def test_capture_says_when_a_lane_stayed_behind(client):
    r = post(client, [{"bar": 0, "scene": "s4"}], 3)
    said = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert said.startswith("1 section added from the jam.")
    assert "lane or a trimmed block stayed behind" in said


@pytest.mark.parametrize(
    "events, bars, why",
    [
        ("[]", "8", "Nothing has been launched"),
        ("", "8", "Nothing has been launched"),
        ("{", "8", "could not be read"),
        ('{"bar": 0}', "8", "could not be read"),
        ("[1, 2]", "8", "could not be read"),
        ('[{"bar": 0, "scene": "s1"}]', "many", "whole number"),
        ('[{"bar": 0, "scene": "s1"}]', "-4", "whole number"),
        ('[{"bar": 0, "scene": "s1"}]', "0", "Nothing has been launched"),
        ('[{"bar": 0, "scene": "gone"}]', "8", "no longer has"),
        # an id the schema would drop must not become "stop everything"
        ('[{"bar": 0, "scene": "s1"}, {"bar": 2, "track": "<b>"}]', "8", "not be read"),
        ("[" * 5000, "8", "could not be read"),
    ],
)
def test_capture_refuses_what_it_cannot_read_and_changes_nothing(
    client, events, bars, why
):
    s = client.app.state.studio
    before, depth = copy.deepcopy(s.song), s.song_undo_state()["depth"]
    r = client.post("/song/jam/commit", data={"events": events, "bars": bars})
    assert r.status_code == 422 and why in r.text
    assert "HX-Trigger" not in r.headers  # no jam-captured: the page keeps its log
    assert s.song == before and s.song_undo_state()["depth"] == depth


def test_capture_refuses_a_log_past_the_cap_whole(client):
    s = client.app.state.studio
    before = copy.deepcopy(s.song)
    many = [{"bar": i, "scene": "s1"} for i in range(limits.MAX_JAM_EVENTS + 1)]
    r = post(client, many, 600)
    assert r.status_code == 422 and f"at most {limits.MAX_JAM_EVENTS}" in r.text
    assert s.song == before
    r = client.post("/song/jam/commit", data={"events": " " * 70_000, "bars": "8"})
    assert r.status_code == 422 and "at most" in r.text


def test_capture_refuses_a_jam_past_the_songs_length(client):
    s = client.app.state.studio
    before = copy.deepcopy(s.song)
    bars = int(limits.MAX_SONG_SECONDS / 2.0) + 8  # 2 s a bar at 120 bpm
    r = post(client, [{"bar": 0, "scene": "s1"}], bars)
    assert r.status_code == 422
    assert s.song == before
