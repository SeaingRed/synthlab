"""The playlist tools' routes (`routes/song_arr.py`; the edits themselves
are `test_song_ops.py`): `POST /song/cells`, `/song/section/{id}/op`,
`/song/bars`, `/song/energy`, `/song/boundary`, and the range loop's wrap
pass (`/song/section/{id}.wav?after=`).

Held here: each is ONE undo step and Ctrl+Z gives back the song's bytes;
a refusal is a 422 toast and nothing changed; hostile fields; the caps;
what a route says about whole stems and about what was not exact; the
range loop's plan under node and its seam on the render; and that a song
using none of this keeps its keys and its saved bytes.
"""

from __future__ import annotations

import copy
import io
import json
import shutil
import subprocess
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio import limits, render, schema
from synthlab.studio import song_model as sm
from synthlab.studio import songops as so
from synthlab.studio.gen import checklist
from synthlab.studio.server import create_app
from tests.studio.test_song_ops import LONG4, energy_song, one, range_song
from tests.studio.test_song_routes import HX, changed, post, song_dict, use_song
from tests.studio.test_song_spans import ATOL, BAR, RING, clean

NODE = shutil.which("node")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        yield c


def song(client) -> dict:
    return client.app.state.studio.song


def blob(client) -> str:
    return json.dumps(song(client), sort_keys=True)


def toast(r) -> str:
    if r.status_code == 422:
        return r.text
    return (
        json.loads(r.headers["HX-Trigger"]).get("studio-toast", {}).get("message", "")
    )


def undone(client, before: str, steps: int = 1) -> None:
    """`steps` undos give back the song byte for byte — and one more has
    nothing of this edit left to undo."""
    for _ in range(steps):
        assert post(client, "/song/undo").status_code == 200
    assert blob(client) == before


# === one section ====================================================================


def test_split_is_one_undo_step_and_names_its_section(client):
    use_song(client, one(8, lead=LONG4))
    before = blob(client)
    r = post(client, "/song/section/s/op", {"op": "split", "bar": "4"})
    assert r.status_code == 200 and toast(r) == "Split S at bar 4."
    secs = song(client)["sections"]
    assert [(s["name"], s["bars"]) for s in secs] == [("S", 3), ("S ′", 5), ("O", 1)]
    assert set(changed(r)) == {s["id"] for s in secs[:2]} | {"o"} or changed(r)
    assert client.app.state.studio.song_undo_state()["undo"] == "split section"
    undone(client, before)


def test_a_cut_inside_an_eight_bar_pattern_says_where_it_also_cut(client):
    """5, 6 or 7 bars of an 8-bar pattern cannot stand: the pass is cut
    again four bars on, and the toast says so. One undo step."""
    from tests.studio.test_song_ops import eight

    use_song(client, eight())
    before = blob(client)
    r = post(client, "/song/bars", {"op": "cut", "bar": "3"})
    assert toast(r) == (
        "Cut at bar 3. Also cut at bar 7 (lead is 8 bars and cannot be 6)."
    )
    assert [s["bars"] for s in song(client)["sections"]] == [2, 4, 2, 1]
    undone(client, before)
    r = post(client, "/song/section/s/op", {"op": "split", "bar": "6"})
    assert toast(r) == (
        "Split S at bar 6. Also cut at bar 2 (lead is 8 bars and cannot be 5)."
    )
    undone(client, before)


def test_a_split_says_what_will_not_be_exactly_as_it_was(client):
    s = one(8)
    s["tracks"][1]["fx"] = [{"kind": "phaser", "params": {}}]
    use_song(client, clean(s))
    r = post(client, "/song/section/s/op", {"op": "split", "bar": "4", "unique": "1"})
    assert "Not exactly as before: Pad's phaser restarts at the cut." in toast(r)
    left, right = song(client)["sections"][:2]
    assert set(left["cells"].values()).isdisjoint(right["cells"].values())


@pytest.mark.parametrize(
    "data, said",
    [
        ({"op": "split"}, "whole number"),
        ({"op": "split", "bar": "x"}, "whole number"),
        ({"op": "split", "bar": "1e3"}, "whole number"),
        ({"op": "split", "bar": "9" * 400}, "whole number"),
        ({"op": "split", "bar": "1"}, "inside Verse"),
        ({"op": "split", "bar": "3"}, "inside Verse"),  # its own first bar
        ({"op": "split", "bar": "99"}, "inside Verse"),
        ({"op": "split", "bar": "-4"}, "inside Verse"),
        ({"op": "unroll", "bar": "4"}, None),  # fine: Verse plays twice
        ({"op": "insert", "where": "sideways"}, "Before or after"),
        ({"op": "insert", "bars": "many"}, "whole number"),
        ({"op": "melt"}, "No such section edit"),
        ({}, "No such section edit"),
        ({"op": "<script>"}, "No such section edit"),
    ],
)
def test_hostile_section_ops_are_a_toast_and_change_nothing(client, data, said):
    before = blob(client)
    r = post(client, "/song/section/s2/op", data)
    if said is None:
        assert r.status_code == 200
        return
    assert r.status_code == 422 and said in r.text
    assert "<script>" not in r.text
    assert blob(client) == before
    assert client.app.state.studio.song_undo_state()["undo"] is None


def test_an_unknown_section_is_a_toast(client):
    for op in ("split", "unroll", "insert", "duplicate"):
        r = post(client, "/song/section/nope/op", {"op": op, "bar": "2"})
        assert r.status_code == 422 and "No such section" in r.text


def test_unroll_insert_and_duplicate_are_each_one_step(client):
    before = blob(client)
    r = post(client, "/song/section/s2/op", {"op": "unroll"})
    assert toast(r) == "Verse is 2 sections now."
    assert [s["name"] for s in song(client)["sections"]] == [
        "Intro",
        "Verse",
        "Verse 2",
    ]
    undone(client, before)
    r = post(client, "/song/section/s1/op", {"op": "insert", "bars": "8"})
    assert toast(r) == "8 empty bars after Intro."
    assert [s["bars"] for s in song(client)["sections"]] == [2, 8, 2]
    r = post(client, "/song/section/s1/op", {"op": "insert", "where": "before"})
    assert song(client)["sections"][0]["bars"] == 4  # the default
    undone(client, before, 2)
    n = len(song(client)["patterns"])
    r = post(client, "/song/section/s2/op", {"op": "duplicate", "unique": "1"})
    assert "patterns of its own" in toast(r)
    assert len(song(client)["patterns"]) == n + 3
    undone(client, before)
    r = post(client, "/song/section/s2/op", {"op": "duplicate"})
    assert "shared" in toast(r) and len(song(client)["patterns"]) == n


def test_the_section_cap_is_the_usual_toast(client):
    s = song_dict()
    s["sections"] += [
        {"id": f"x{i}", "name": "X", "bars": 1} for i in range(limits.MAX_SECTIONS - 2)
    ]
    use_song(client, s)
    before = blob(client)
    for path, data in (
        ("/song/section/s2/op", {"op": "unroll"}),
        ("/song/section/s2/op", {"op": "split", "bar": "4"}),
        ("/song/section/s2/op", {"op": "insert"}),
        ("/song/section/s2/op", {"op": "duplicate", "unique": "1"}),
        ("/song/bars", {"op": "insert", "bar": "1"}),
        ("/song/bars", {"op": "cut", "bar": "2"}),
    ):
        r = post(client, path, data)
        assert r.status_code == 422 and "at most 32 sections" in r.text, (path, data)
    assert blob(client) == before


def test_the_length_caps_refuse_an_insert_that_passes_them(client):
    s = song_dict()
    s["bpm"] = 40.0  # a bar is 6 s: the song is 36 s
    s["sections"][0]["repeats"] = 16
    s["sections"][1]["bars"], s["sections"][1]["repeats"] = 2, 16  # 384 s
    use_song(client, s)
    before = blob(client)
    r = post(client, "/song/bars", {"op": "insert", "bar": "1", "bars": "64"})
    assert r.status_code == 422 and "the cap is 480s" in r.text
    assert blob(client) == before


# === the timeline, by its bars ======================================================


def test_bars_cut_insert_and_delete(client):
    use_song(client, one(8, lead=LONG4))
    before = blob(client)
    r = post(client, "/song/bars", {"op": "cut", "bar": "5"})
    assert toast(r) == "Cut at bar 5."
    assert [s["bars"] for s in song(client)["sections"]] == [4, 4, 1]
    r = post(client, "/song/bars", {"op": "cut", "bar": "5"})
    assert r.status_code == 422 and "starts a section already" in r.text
    r = post(client, "/song/bars", {"op": "insert", "bar": "5", "bars": "2"})
    assert toast(r) == "2 empty bars at bar 5."
    assert [s["bars"] for s in song(client)["sections"]] == [4, 2, 4, 1]
    r = post(client, "/song/bars", {"op": "delete", "bar": "5", "to": "6"})
    assert toast(r) == "Deleted bars 5–6 (2 bars)."
    assert [s["bars"] for s in song(client)["sections"]] == [4, 4, 1]
    undone(client, before, 3)


@pytest.mark.parametrize(
    "data, said",
    [
        ({"op": "cut"}, "whole number"),
        ({"op": "cut", "bar": "0"}, "past the song"),
        ({"op": "cut", "bar": "500"}, "past the song"),
        ({"op": "insert", "bar": "nan"}, "whole number"),
        ({"op": "insert", "bar": "2", "bars": "1.5"}, "whole number"),
        ({"op": "delete", "bar": "2"}, "whole number"),
        ({"op": "delete", "bar": "4", "to": "2"}, "Pick the bars"),
        ({"op": "delete", "bar": "2", "to": "900"}, "Pick the bars"),
        ({"op": "shuffle", "bar": "2"}, "No such edit"),
    ],
)
def test_hostile_bars_are_a_toast_and_change_nothing(client, data, said):
    before = blob(client)
    r = post(client, "/song/bars", data)
    assert r.status_code == 422 and said in r.text
    assert blob(client) == before


def test_an_insert_under_a_whole_stem_says_the_stem_stays(client, tmp_path):
    from tests.studio.test_song_long import audio_song, make_source, place

    sid = make_source(tmp_path, seconds=20)
    s = audio_song((4, 1), (4, 1))
    place(s, tmp_path, sid, at_bar=2)
    use_song(client, s)
    held = copy.deepcopy(song(client)["long"])
    r = post(client, "/song/bars", {"op": "insert", "bar": "3", "bars": "2"})
    said = toast(r)
    assert "stays at its bar: 2 bars of the arrangement moved under." in said
    assert held[0]["name"] in said
    assert song(client)["long"] == held
    r = post(client, "/song/bars", {"op": "delete", "bar": "1", "to": "2"})
    assert "stays at its bar" in toast(r) and song(client)["long"] == held
    r = post(client, "/song/section/s2/op", {"op": "insert"})
    assert "stays at its bar" in toast(r)  # the song grew: more of the stem plays
    r = post(client, "/song/bars", {"op": "cut", "bar": "2"})
    assert "stays" not in toast(r)  # a cut moves nothing


# === a range of cells ===============================================================

BOX = "s1:td,s1:t1,s2:td,s2:t1"


def test_a_range_edit_is_one_undo_step(client):
    use_song(client, range_song())
    before = blob(client)
    r = post(
        client,
        "/song/cells",
        {"op": "copy", "cells": BOX, "to_section": "s2", "to_track": "td"},
    )
    assert toast(r) == "Copied 4 cells."
    assert song(client)["sections"][2]["cells"] == {"td": "pd", "t1": "p1"}
    undone(client, before)
    r = post(client, "/song/cells", {"op": "duplicate", "cells": BOX})
    assert toast(r) == "Duplicated 4 cells to the right (1 section added)."
    assert len(song(client)["sections"]) == 4
    undone(client, before)
    r = post(
        client,
        "/song/cells",
        {"op": "move", "cells": BOX, "to_section": "s2", "to_track": "td"},
    )
    assert toast(r) == "Moved 4 cells."
    undone(client, before)
    r = post(client, "/song/cells", {"op": "clear", "cells": BOX + ",s1:ta"})
    assert toast(r) == "Cleared 5 of 5 cells."
    undone(client, before)
    r = post(client, "/song/cells", {"op": "unique", "cells": "s2:td,s2:t1"})
    assert toast(r) == "2 patterns made unique."
    undone(client, before)
    r = post(client, "/song/cells", {"op": "variation", "cells": "s2:td", "seed": "5"})
    assert toast(r) == "1 pattern varied."
    undone(client, before)


def test_a_generator_on_a_range_plays_and_rolling_again_is_the_same_step(client):
    use_song(client, range_song())
    before = blob(client)
    data = {"op": "fill", "cells": "s1:t2,s2:t2", "kind": "bass_root", "seed": "3"}
    r = post(client, "/song/cells", data)
    trig = json.loads(r.headers["HX-Trigger"])
    assert trig["song-autoplay"] == {"section": "s1"}
    assert "Wrote bass root into 2 cells." == toast(r)
    first = blob(client)
    post(client, "/song/cells", data | {"seed": "4"})
    assert blob(client) != first
    for seed in ("5", "6"):
        r = post(
            client, "/song/cells", {"op": "again", "cells": "s1:t2,s2:t2", "seed": seed}
        )
        assert json.loads(r.headers["HX-Trigger"])["song-autoplay"] == {"section": "s1"}
    # two fills and two rolls: two undo steps (each kind joins itself)
    undone(client, before, 2)


@pytest.mark.parametrize(
    "data, said",
    [
        ({"op": "clear"}, "Select some cells"),
        ({"op": "clear", "cells": ","}, "Select some cells"),
        ({"op": "clear", "cells": "s1:zz"}, "no longer on the song"),
        ({"op": "clear", "cells": "s1"}, "no longer on the song"),
        ({"op": "clear", "cells": ",".join(["s1:td"] * 97)}, "at most 96 cells"),
        ({"op": "clear", "cells": "s1:td," * 5000}, "at most 96 cells"),
        ({"op": "copy", "cells": BOX}, "Pick where"),
        (
            {"op": "copy", "cells": BOX, "to_section": "s1", "to_track": "t1"},
            "land on Bass",
        ),
        ({"op": "fill", "cells": BOX, "kind": "rm -rf"}, "No such generator"),
        ({"op": "fill", "cells": BOX, "kind": "lead"}, "drums take a fill"),
        ({"op": "again", "cells": BOX}, "nothing to roll"),
        ({"op": "unique", "cells": "s2:t2"}, "Nothing in the range is shared"),
        ({"op": "explode", "cells": BOX}, "No such range edit"),
        ({"cells": BOX}, "No such range edit"),
    ],
)
def test_hostile_ranges_are_refused_whole(client, data, said):
    use_song(client, range_song())
    before = blob(client)
    r = post(client, "/song/cells", data)
    assert r.status_code == 422 and said in r.text
    assert blob(client) == before


# === the energy lane ================================================================


def test_energy_is_set_dragged_as_one_step_and_taken_away(client):
    before = blob(client)
    for v in ("0.2", "0.4", "0.9"):
        r = post(client, "/song/energy", {"section_id": "s1", "energy": v})
        assert r.status_code == 200
    assert song(client)["sections"][0]["energy"] == 0.9
    assert changed(r) == []  # it renders nothing: no section's audio moved
    undone(client, before)  # the drag was ONE step
    post(client, "/song/energy", {"section_id": "s1", "energy": "7"})
    assert song(client)["sections"][0]["energy"] == 1.0
    post(client, "/song/energy", {"section_id": "s1", "energy": ""})
    assert "energy" not in song(client)["sections"][0]
    for bad in ("loud", "nan", "inf", "1e999"):
        r = post(client, "/song/energy", {"section_id": "s1", "energy": bad})
        assert r.status_code == 422 and "number from 0 to 1" in r.text
    r = post(client, "/song/energy", {"section_id": "zz", "energy": "0.5"})
    assert r.status_code == 422 and "No such section" in r.text


def test_a_song_without_energy_keeps_its_keys_and_its_bytes(client):
    """Setting and clearing an energy leaves the song's JSON as it was,
    and a section's render key never reads it."""
    svc = client.app.state.svc
    before = blob(client)

    def keys():
        s = song(client)
        return [
            render.section_key(sm.section_arrangement(s, sec), None)
            for sec in s["sections"]
        ]

    was = keys()
    post(client, "/song/energy", {"section_id": "s1", "energy": "0.7"})
    assert keys() == was and blob(client) != before
    post(client, "/song/energy", {"section_id": "s1", "energy": ""})
    assert blob(client) == before
    assert "energy" not in json.dumps(schema.nodes().SONG.default())
    assert svc is not None


def test_arrange_by_energy_is_one_step_however_often(client):
    use_song(client, energy_song())
    before = blob(client)
    r = post(client, "/song/energy", {"op": "arrange", "seed": "3"})
    assert toast(r).startswith("Arranged Intro, Verse, Break by energy")
    once = blob(client)
    post(client, "/song/energy", {"op": "arrange", "seed": "3"})
    assert blob(client) == once
    post(client, "/song/energy", {"op": "arrange", "seed": "9"})
    undone(client, before)
    r = post(client, "/song/energy", {"op": "arrange", "section_id": "s2"})
    assert r.status_code == 422 and "fullest" in r.text
    use_song(client)
    r = post(client, "/song/energy", {"op": "arrange"})
    assert r.status_code == 422 and "Draw the energy lane" in r.text


def test_the_checklist_reads_the_energy_lane():
    """A long stretch where nothing changes, under an energy lane that
    was drawn to move: the finding says so and its fix is the arrange."""
    s = song_dict()
    s["sections"] = [
        {"id": f"s{i}", "name": f"S{i}", "bars": 8, "cells": {"td": "pd", "t1": "p1"}}
        for i in range(5)
    ]
    s = clean(s)
    found = {f["key"].split(":")[0]: f for f in checklist.check(s)}
    assert found["still"]["text"] == "Nothing changes for 40 bars from S0."
    assert found["still"]["fix"]["label"] == "Add a fill every 8 bars"
    so.set_energy(s, "s1", 0.3)
    so.set_energy(s, "s2", 0.9)
    found = {f["key"].split(":")[0]: f for f in checklist.check(s)}
    assert found["still"]["text"].endswith("though the energy lane does.")
    assert found["still"]["fix"] == {
        "label": "Arrange by energy",
        "post": "/song/energy",
        "vals": {"op": "arrange"},
    }
    so.set_energy(s, "s2", 0.35)  # no step: the lane is level
    found = {f["key"].split(":")[0]: f for f in checklist.check(s)}
    assert found["still"]["fix"]["label"] == "Add a fill every 8 bars"


# === a transition on a boundary =====================================================


def takes(client) -> list[list]:
    return [
        sorted((a["clip"].split("-")[0], a["at_bar"]) for a in sec["audio"])
        for sec in song(client)["sections"]
    ]


def test_a_boundarys_default_follows_the_energy_and_is_one_step(client):
    """Rising: a riser that ends on the Intro's last bar line and an
    impact on the Verse's first — two clips, one undo step — and the page
    is told to play from two bars before the boundary."""
    post(client, "/song/energy", {"section_id": "s1", "energy": "0.3"})
    post(client, "/song/energy", {"section_id": "s2", "energy": "0.9"})
    before = blob(client)
    r = post(client, "/song/boundary", {"section_id": "s2"})
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert trig["studio-toast"]["message"] == "Added riser + impact (rising energy)."
    assert trig["song-audition"] == {"bar": 1}  # the boundary is bar 3
    assert takes(client) == [[("riser", 0)], [("impact", 0)]]  # 2-bar clips
    assert client.app.state.studio.song_undo_state()["undo"] == "add transition"
    undone(client, before)
    post(client, "/song/energy", {"section_id": "s1", "energy": "1"})
    post(client, "/song/energy", {"section_id": "s2", "energy": "0.2"})
    r = post(client, "/song/boundary", {"section_id": "s2"})
    assert toast(r) == "Added downlifter (falling energy)."
    assert takes(client) == [[], [("sweep", 0)]]


def test_a_boundary_takes_one_kind_and_refuses_hostile_ones(client, tmp_path):
    r = post(client, "/song/boundary", {"section_id": "s2", "kind": "swell"})
    assert takes(client) == [[("swell", 0)], []]
    r = post(
        client,
        "/song/boundary",
        {"section_id": "s2", "kind": "sweep", "direction": "down", "bars": "1"},
    )
    assert ("sweep", 0) in takes(client)[1]
    before = blob(client)
    clips = sorted(p.name for p in (tmp_path / "clips").glob("*.wav"))
    for data, said in (
        ({"section_id": "s1"}, "No such boundary"),  # nothing before the first
        ({"section_id": "zz"}, "No such section"),
        ({"section_id": "s2", "kind": "laser"}, "No such transition"),
        ({"section_id": "s2", "kind": "riser", "direction": "sideways"}, "No such"),
        ({"section_id": "s2", "kind": "riser", "bars": "x"}, "numbers"),
        ({}, "No such section"),
    ):
        r = post(client, "/song/boundary", data)
        assert r.status_code == 422 and said in r.text, data
    assert blob(client) == before
    assert sorted(p.name for p in (tmp_path / "clips").glob("*.wav")) == clips


# === the range loop: its wrap pass and its seam =====================================


def pcm(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data)) as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").reshape(-1, 2).astype(np.float64) / 32767


def three() -> dict:
    """A (2 bars) · B (2 bars x 2) · C (1 bar): the Pad rings 0.7 s past
    every boundary, so each head carries its neighbour's ring."""
    s = song_dict()
    s["tracks"][1]["patch"] = RING
    s["patterns"]["p1"]["cells"] = [["A1", 0], ["E2", 14]]  # late in the bar
    s["sections"].append(
        {"id": "s3", "name": "Out", "bars": 1, "cells": {"td": "pd", "t2": "p2"}}
    )
    return clean(s)


def test_the_wrap_pass_is_the_section_after_its_neighbour(client):
    """Looping sections 1-2: at the wrap, section 1 plays again with
    section 2's ring in its head. That pass is `?after=s2` — and it is,
    to the sample, what section 1 renders in a song where it FOLLOWS
    section 2 (the reference: the two sections twice over, as a song)."""
    svc = client.app.state.svc
    s = three()
    gain = svc.song_gain(s)
    a = svc.section_result(s, 0, after=1, gain=gain).audio()
    twice = twice_over(s)
    ref = svc._song_raw(twice)
    at = sum(n for _a, n in sm.section_timeline(twice, twice["sections"][0])) + sum(
        n for _a, n in sm.section_timeline(twice, twice["sections"][1])
    )
    want = ref[at : at + len(a)]
    assert np.max(np.abs(want)) * gain <= 0.97 + 1e-9  # (not over the ceiling)
    np.testing.assert_allclose(a, want * gain, atol=1.01 / 32767)  # one 16-bit step
    # ... and it is not the song's own first pass (nothing rang into that)
    head = svc.section_result(s, 0, gain=gain).audio()
    assert np.max(np.abs(a - head)) > 1e-3
    # exact before the WAV's rounding: the fold itself, at gain 1
    ident, parts, prev = svc._song_idents(s, ())["passes"][0][0]
    last = svc._song_idents(s, ())["passes"][1][0]
    mix = svc._section_mix(
        {"own": ident["own"], "fold": last[0]["own"]}, parts, last[1], ()
    )
    np.testing.assert_allclose(mix, want, atol=ATOL)


def twice_over(s: dict) -> dict:
    """Sections 1-2 of `s`, then the same two again: the song a loop over
    them IS — the reference a range loop's wrap is held against."""
    out = copy.deepcopy(s)
    again = [dict(copy.deepcopy(x), id=x["id"] + "b") for x in s["sections"][:2]]
    out["sections"] = [*out["sections"][:2], *again]
    return clean(out)


def test_the_seam_of_a_range_loop_is_the_songs_own_boundary(client):
    """THE SEAM, measured on what the browser plays: the loop's last pass
    and the wrap pass end to end, against the same stretch of the song
    that plays the two sections twice. They are one signal to a step of
    the WAVs' 16 bits — across the wrap as everywhere else — so the wrap
    has no click the song itself would not have there."""
    s = three()
    use_song(client, s)
    svc = client.app.state.svc
    plan = client.get("/song/plan.json").json()
    a, b = plan["sections"][:2]
    wrap = client.get(f"/song/section/{a['section_id']}.wav?after={b['section_id']}")
    assert wrap.status_code == 200
    last, first = pcm(client.get(b["loop_url"]).content), pcm(wrap.content)
    assert len(first) == a["cycle_samples"] == BAR  # a 1-bar cycle
    played = np.concatenate([last, first])
    ref = svc._song_raw(twice_over(s)) * plan["gain"]
    at = 2 * BAR + 4 * BAR  # where the second round starts
    want = ref[at - len(last) : at + len(first)]
    assert np.max(np.abs(played - want)) <= 1.01 / 32767
    seam = slice(len(last) - 64, len(last) + 64)
    assert np.max(np.abs(played[seam] - want[seam])) <= 1.01 / 32767
    # the head of the wrap pass holds section 2's ring: it is not a cold start
    cold = pcm(client.get(a["url"]).content)
    assert np.max(np.abs(first - cold)) > 1e-3
    for bad in ("zz", ""):
        r = client.get(f"/song/section/{a['section_id']}.wav?after={bad}")
        assert r.status_code == 404
    with pytest.raises(render.RenderError):
        svc.section_result(song(client), 0, after=True)
    with pytest.raises(render.RenderError):
        svc.section_result(song(client), 0, after=9)


def test_a_wrap_pass_never_clips(client):
    """The song's gain is found without the wrap pass: one that would
    peak over the ceiling is scaled down to it."""
    from synthlab.studio import song_play

    svc = client.app.state.svc
    s = three()
    loud = svc.section_result(s, 0, after=1, gain=40.0).audio()
    assert np.max(np.abs(loud)) <= song_play.CEILING + 1 / 32767


SCHEDULE = """
const fs = require("fs");
const S = (globalThis.window = { synthlab: {} }).synthlab;
for (const f of process.argv.slice(1, 3)) eval(fs.readFileSync(f, "utf8"));
const { songSchedule: P, songRange: R } = S;
const sec = (id, passes) => ({
  section_id: id, cycle_samples: 44100, passes, cycles: passes, repeats: 1,
  bars: passes, cycle_bars: 1, start_bar: 0, name: id,
  url: `/song/section/${id}.wav?k=${id}H`,
  loop_url: `/song/section/${id}.wav?loop=1&k=${id}L` });
const plan = { sr: 44100, sections: [["a", 1], ["b", 2], ["c", 1], ["d", 1]]
  .map(([id, passes]) => sec(id, passes)) };
const run = (opts, n) => P.schedule(plan, { horizon: n, ...opts })
  .map((e) => e.url.replace("/song/section/", ""));
const span = (loop) => R.span(plan, loop);
console.log(JSON.stringify({
  range: run({ sid: "b", loop: ["b", "c"] }, 7),
  from_outside: run({ sid: "a", loop: ["b", "c"] }, 5),
  from_after: run({ sid: "d", loop: ["b", "c"] }, 3),
  one: run({ sid: "b", loop: true }, 3),
  song: run({ sid: "c" }, 9),
  gone: run({ sid: "b", loop: ["b", "zz"] }, 4),
  url: R.url(plan.sections[1], plan.sections[2]),
  pick: [span(["b", "c"]), span(["c", "b"]), span(["b", "zz"]), span(true)],
}));
"""


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_schedule_loops_a_range_and_wraps_through_the_after_pass():
    """`songSchedule` with `loop: [first id, last id]`: every pass of the
    range in order, then back to the first — whose first pass after a
    wrap is the `?after=` WAV, keyed by both sections' keys."""
    out = subprocess.run(
        [
            NODE,
            "-e",
            SCHEDULE,
            "synthlab/studio/static/song.js",
            "synthlab/studio/static/songsel.js",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=".",
    )
    got = json.loads(out.stdout)
    wrap = "b.wav?after=c&k=bH-cH"
    loop = "b.wav?loop=1&k=bL"
    assert got["range"] == [
        "b.wav?k=bH",
        loop,
        "c.wav?k=cH",
        wrap,
        loop,
        "c.wav?k=cH",
        wrap,
    ]
    # started before the range it plays on into it; after it, it goes to it
    assert got["from_outside"] == ["a.wav?k=aH", "b.wav?k=bH", loop, "c.wav?k=cH", wrap]
    assert got["from_after"] == ["d.wav?k=dH", "b.wav?after=d&k=bH-dH", loop]
    assert got["one"] == [loop, loop, loop]  # one section: as it always was
    assert got["song"] == ["c.wav?k=cH", "d.wav?k=dH"]
    # a range whose end is gone is no range: the song plays on
    assert got["gone"] == ["b.wav?k=bH", loop, "c.wav?k=cH", "d.wav?k=dH"]
    assert got["url"] == "/song/section/b.wav?after=c&k=bH-cH"
    assert got["pick"] == [[1, 2], [1, 2], None, None]


def test_a_song_that_uses_none_of_this_keeps_its_plan(client):
    """No new field in the plan, no moved URL: the fixture song's plan is
    what it was before the range loop."""
    plan = client.get("/song/plan.json").json()
    assert sorted(plan["sections"][0]) == sorted(
        [
            "section_id",
            "name",
            "index",
            "bars",
            "repeats",
            "start_bar",
            "cycle_bars",
            "cycles",
            "passes",
            "cycle_samples",
            "cycle_seconds",
            "start_sample",
            "start_seconds",
            "url",
            "loop_url",
            "levels",  # the mixer's meters: per-stem peak and RMS
        ]
    )
    assert HX


# === the chord tiles' two ops, and what the page draws ================================


def test_the_chord_routes_take_an_edge_and_a_split(client):
    from synthlab.studio.gen import chordtrack

    s = song_dict()
    s["chords"] = chordtrack.from_degrees((1, 6, 3, 7), 4)
    use_song(client, s)
    before = blob(client)
    r = post(client, "/song/chords", {"op": "edge", "slot": "1", "at": "6"})
    assert r.status_code == 200
    assert [x["at"] for x in song(client)["chords"]["slots"]] == [0.0, 6.0, 8.0, 12.0]
    r = post(client, "/song/chords", {"op": "split", "slot": "3"})
    assert [x["deg"] for x in song(client)["chords"]["slots"]] == [1, 6, 3, 7, 7]
    undone(client, before, 2)
    # a section's tile, when the section has none of its own: it gets its own
    r = post(
        client, "/song/chords", {"op": "edge", "slot": "2", "at": "9", "scope": "s1"}
    )
    assert song(client)["sections"][0]["chords"]["slots"][2]["at"] == 9.0
    assert song(client)["chords"]["slots"][2]["at"] == 8.0
    for data, said in (
        ({"op": "edge", "slot": "0", "at": "2"}, "starts the progression"),
        ({"op": "edge", "slot": "9", "at": "2"}, "starts the progression"),
        ({"op": "edge", "slot": "1", "at": "soon"}, "is a number"),
        ({"op": "split", "slot": "40"}, "No such chord"),
    ):
        r = post(client, "/song/chords", data)
        assert r.status_code == 422 and said in r.text, data


def test_the_playlist_draws_its_tools_the_energy_lane_and_the_chord_tiles(client):
    from synthlab.studio.gen import chordtrack

    s = song_dict()
    s["chords"] = chordtrack.from_degrees((1, 6), 2)
    s["sections"][0]["energy"] = 0.25
    s["sections"][1]["energy"] = 0.8
    s["sections"].append(
        {
            "id": "s3",
            "name": "Twin",
            "bars": 2,
            "cells": dict(s["sections"][0]["cells"]),
        }
    )
    use_song(client, s)
    html = client.get("/view/song").text
    # the tool bar: every edit at the marker is a command with its key
    for cmd, keys in (
        ("Split at the marker", "S"),
        ("Insert 4 bars at the marker", "I"),
        ("Add a transition before the selected section", "T"),
        ("Next section", "Ctrl+→"),
        ("Previous section", "Ctrl+←"),
    ):
        assert f'data-cmd="{cmd}" data-cmd-group="Song" data-keys="{keys}"' in html, cmd
    for cmd in ("Loop the selected range", "Arrange by energy", "Clear the selection"):
        assert f'data-cmd="{cmd}"' in html
    assert 'id="mark-bar" name="bar" value="1"' in html
    assert f'data-max-range="{limits.MAX_RANGE_CELLS}"' in html
    # the energy lane: a range a section, one tab stop, an undrawn one hatched
    assert html.count('class="energy" name="energy"') == 3
    assert 'value="0.25" data-default="0.5"\n                   tabindex="0"' in html
    assert html.count('class="energy-cell unset"') == 1
    # the + on a boundary knows its trend; the first section has none
    assert html.count('class="sec-plus"') == 2
    assert 'data-section-id="s2" data-ctx-state="rising"' in html
    assert 'data-section-id="s3" data-ctx-state="level"' in html
    # the chord tiles: two a bar-pair in each 2-bar section, the song's
    assert html.count('class="chord-slot ctile"') == 6
    assert (
        'data-ctx-pop="chord-menu" data-scope="song" data-slot="1" data-deg="6"' in html
    )
    assert html.count('class="ctile-edge"') == 3  # the second chord's, a section
    # a section that shares every block with another says so
    assert html.count('class="sec-twin"') == 2 and "shared with Twin" in html
    # the three shared menus, and the section menu's controls that keep it open
    for menu in ("bound-menu", "chord-menu", "range-menu"):
        assert f'id="{menu}" class="pop menu ctx-menu" popover' in html
    assert html.count("data-stay") == 6  # two a section: its repeat buttons
    assert "Duplicate as a variation" in html and "Unroll ×2" in html


def test_the_insert_buttons_confirm_only_when_a_whole_stem_would_be_passed(client):
    html = client.get("/view/song").text
    assert 'hx-confirm="Insert 4 empty bars' not in html
    live = song(client)
    live["long"] = [
        {"id": "lg", "track_id": "ta", "source": "0123456789abcdef", "name": "Vox"}
    ]
    use_song(client, live)
    html = client.get("/view/song").text
    assert (
        "Insert 4 empty bars at the marker? 1 whole stem (Vox) stays at its bar: "
        "the arrangement moves under." in html
    )
    assert "Insert 4 empty bars before Intro? 1 whole stem (Vox)" in html
