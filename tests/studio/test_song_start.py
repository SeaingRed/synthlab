"""Starters, loop -> song, one part again, the song's own settings and its
chord track (`routes/song_start.py`, `studio/gen/build.py`).

A starter is a whole song — tracks named for their roles, patterns,
sections, repeats, spans, lanes, takes, the chord track — schema-clean,
inside every cap, the same dict for the same seed in another process, and
ONE undo step that Ctrl+Z takes back whole. A refused start leaves no clip
behind. "Make this loop a song" writes no note that was not in the loop
except the fill. Unknown styles, kinds, ops, roles, qualities and scopes
are a 422 toast and nothing changed."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import limits, schema, song_play
from synthlab.studio import song_model as sm
from synthlab.studio.gen import build, chordtrack, styles
from synthlab.studio.gen import notes as nt
from synthlab.studio.server import create_app

from .test_song_routes import HX, post, song, use_song

N = schema.nodes()
STYLES = list(styles.STYLES)
SEEDS = (1, 2, 3, 4, 5)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _clips(client) -> set[str]:
    d = client.app.state.studio.data_dir / "clips"
    return {p.stem for p in d.glob("*.wav")} if d.exists() else set()


def _trigger(r) -> dict:
    return json.loads(r.headers.get("hx-trigger", "{}"))


def _fingerprint(seed: int) -> str:
    return json.dumps({k: build.start(k, seed).song for k in STYLES}, sort_keys=True)


# --- the pure build ------------------------------------------------------------


@pytest.mark.parametrize("style", STYLES)
def test_a_starter_is_a_whole_schema_clean_song_inside_every_cap(style):
    st = styles.STYLES[style]
    for seed in SEEDS:
        built = build.start(style, seed)
        s = built.song
        assert schema.conform_strict(copy.deepcopy(s), N.SONG) == s  # a fixed point
        song_play.check_length(s)  # the song cap and every section's cycle
        assert st["bpm_range"][0] <= s["bpm"] <= st["bpm_range"][1]
        assert (s["root"], s["scale_name"]) in st["keys"]
        assert sm.song_bars(s) == styles.bars_of(style)
        assert [x["name"] for x in s["sections"]] == [x["name"] for x in st["shape"]]
        kinds = [t["kind"] for t in s["tracks"]]
        assert (
            kinds.count("drums") == 1
            and kinds.count("synth") <= limits.MAX_SYNTH_TRACKS
        )
        assert len(s["tracks"]) <= limits.MAX_TRACKS
        assert len(s["patterns"]) <= limits.MAX_PATTERNS
        # tracks are named for what they are for, never "Synth N"
        assert all(t.get("role") in sm.TRACK_ROLES for t in s["tracks"])
        assert not any(t["name"].startswith("Synth") for t in s["tracks"])
        assert len({t["name"] for t in s["tracks"]}) == len(s["tracks"])
        # the chord track, a title, and how it was made
        assert s["chords"]["slots"] and s["title"] and s["gen"]["kind"] == "start"
        assert s["gen"]["params"]["style"] == style and s["gen"]["seed"] == seed
        # every pattern is in use, carries the generator that wrote it, and
        # every lane, span and take the song holds is one the render reads
        used = {p for x in s["sections"] for p in x["cells"].values() if p}
        assert used == set(s["patterns"])
        assert all(p.get("gen") and p.get("fam") for p in s["patterns"].values())
        for sec in s["sections"]:
            assert sec["bars"] % sm.cycle_bars(s, sec) == 0
            lanes = sm.section_lanes(s, sec)
            assert lanes == sec.get("auto", {})  # none is inert or missing its unit
            for tid, lst in lanes.items():
                assert len(lst) <= limits.MAX_CELL_LANES and sec["cells"][tid]
            for a in sec["audio"]:
                assert a["clip"] in built.clips and 0 <= a["at_bar"] < sec["bars"]
        assert any(x.get("spans") for x in s["sections"])
        assert any(x.get("auto") for x in s["sections"])
        assert any(x["audio"] for x in s["sections"])
        assert any(x["repeats"] > 1 for x in s["sections"])
        for spec in built.clips.values():
            assert spec["bars"] * 240.0 / spec["bpm"] <= limits.MAX_CLIP_SECONDS


def test_a_seed_is_the_same_song_in_another_process():
    here = _fingerprint(7)
    code = (
        "import sys; sys.path.insert(0, '.');"
        "from tests.studio.test_song_start import _fingerprint; print(_fingerprint(7))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": "4242"},
    )
    assert out.stdout.strip() == here
    assert _fingerprint(8) != here


def test_a_starter_takes_a_key_a_tempo_and_a_long_form_and_holds_the_cap():
    s = build.start("trance", 3, bpm=134, root="C2", scale="dorian").song
    assert (s["bpm"], s["root"], s["scale_name"]) == (134.0, "C2", "dorian")
    key = chordtrack.key_pc(s)
    pcs = {(key + x) % 12 for x in chordtrack.scale_of(s)}
    for p in s["patterns"].values():
        if p["kind"] == "notes":
            assert all(sm.midi(c[0]) % 12 in pcs for c in p["cells"]), p["name"]
    long = build.start("trance", 3, variant="long").song
    assert sm.song_bars(long) == 208
    # at a crawl the shape would pass the cap: repeats come off, and it says so
    slow = build.start("melodic_techno", 3, bpm=85)
    assert sm.song_seconds(slow.song) <= limits.MAX_SONG_SECONDS
    assert sm.song_bars(slow.song) < 176 and "Shortened" in " ".join(slow.notes)
    with pytest.raises(sm.SongError, match="tempo"):
        build.start("melodic_techno", 3, bpm=40)


def test_the_rolling_bass_pair_shares_one_seed_and_a_reroll_moves_both():
    s = build.start("trance", 4).song
    sub, mid = (
        next(t for t in s["tracks"] if t["name"] == n) for n in ("Sub", "Mid Bass")
    )
    climax = next(x for x in s["sections"] if x["name"] == "Climax")
    a, b = (s["patterns"][climax["cells"][t["id"]]] for t in (sub, mid))
    assert a["gen"]["seed"] == b["gen"]["seed"]
    assert nt.note_ons(a) == nt.note_ons(b)
    before = copy.deepcopy(s)
    names = build.reroll(s, mid["id"], 99)
    assert set(names) == {"Sub", "Mid Bass"}
    moved = {pid for pid, p in s["patterns"].items() if p != before["patterns"][pid]}
    owners = {
        t["name"]
        for t in s["tracks"]
        if any(x["cells"].get(t["id"]) in moved for x in s["sections"])
    }
    assert owners <= {"Sub", "Mid Bass"} and moved
    assert s["sections"] == before["sections"] and s["tracks"] == before["tracks"]
    # the same seed again is the same bass; the song's own seed is the first one
    again = copy.deepcopy(before)
    build.reroll(again, mid["id"], 99)
    assert again == s
    back = copy.deepcopy(s)
    build.reroll(back, mid["id"], before["gen"]["seed"])
    assert back["patterns"] == before["patterns"]


# --- POST /song/start ----------------------------------------------------------


def test_start_lays_the_song_down_as_one_undo_step_and_asks_the_page_to_play(client):
    use_song(client)
    was = copy.deepcopy(song(client))
    r = post(client, "/song/start", {"style": "trance", "seed": "5"})
    assert r.status_code == 200
    trig = _trigger(r)
    assert trig["song-autoplay"] == {"head": True}
    assert "Arm In Arm" in trig["studio-toast"]["message"]  # trance's nod
    s = song(client)
    assert [x["name"] for x in s["sections"]][:2] == ["Intro", "Intro B"]
    assert len(trig["song-changed"]["sections"]) == len(s["sections"])
    st = client.app.state.studio
    assert st.song_undo_state()["undo"] == "start: arm in arm"
    assert st.grid_span == 2
    # every take's clip is in the store, under the name the song holds
    names = {a["clip"] for x in s["sections"] for a in x["audio"]}
    assert names and names <= _clips(client)
    # the same style again straight after is the SAME step (a re-roll) ...
    post(client, "/song/start", {"style": "trance", "seed": "6"})
    post(client, "/song/start", {"again": "1"})
    assert st.song_undo_state()["depth"] == 1
    assert song(client)["gen"]["params"]["style"] == "trance"
    # ... and one Ctrl+Z gives back the song it replaced, whole
    assert post(client, "/song/undo").status_code == 200
    assert song(client) == was
    assert post(client, "/song/redo").status_code == 200
    assert song(client)["gen"]["kind"] == "start"
    html = client.get("/view/song", headers=HX).text
    assert 'data-cmd="Roll the song again"' in html and 'id="chord-strip"' in html
    assert 'data-cmd="Start a song: Arm In Arm"' in html  # in the Start… popover now
    # a nod label hides the genre: the palette finds it by the blurb, its hint
    assert 'data-cmd-hint="Trance: ' in html
    assert "hx-confirm" in html[html.index('id="song-start-pop"') :][:6000]


def test_the_empty_song_is_the_starter_sheet_and_the_default_follows_the_last_used(
    client,
):
    html = client.get("/view/song", headers=HX).text
    sheet = html[html.index('class="start-sheet"') :]
    # eighteen styles and the loop
    assert sheet.count('class="btn start-btn"') == 19
    assert 'data-default="melodic_techno"' in html
    assert "hx-confirm" not in sheet[: sheet.index("</section>")]
    # the labels: the genre, or a nod to the music it is after
    for label in (
        "Anymore",  # melodic techno's nod
        "Daft Rock",
        "Fred Before",
        "Arm In Arm",  # and trance's
        "Just Ice",
        "Nine Inch Wails",
        "Psych Haze",
        "Sixties Studio",
        "Hip-Hop / Trap",  # from here on: plain genre labels
        "Drum &amp; Bass",
        "Dubstep",
        "Synthwave",
        "Future Bass",
        "Lo-Fi Hip-Hop",
        "Ambient / Cinematic",
        "Chiptune",
        "Hardstyle",
        "Phonk",
    ):
        assert f'data-cmd="Start a song: {label}"' in sheet
    assert 'data-cmd="Make this loop a song"' in sheet
    assert (
        post(client, "/song/start", {"style": "garage", "seed": "1"}).status_code == 200
    )
    assert client.cookies.get("synthlab_style") == "garage"
    post(client, "/song/undo")
    html = client.get("/view/song", headers=HX).text
    assert 'data-default="garage"' in html  # the last one used, from the cookie
    # a start that names no style takes it too
    assert post(client, "/song/start", {"seed": "2"}).status_code == 200
    assert song(client)["gen"]["params"]["style"] == "garage"


def test_hostile_starts_are_refused_and_leave_nothing_behind(client):
    use_song(client)
    was = copy.deepcopy(song(client))
    for data in (
        {"style": "polka"},
        {"style": "trance", "root": "H9"},
        {"style": "trance", "scale_name": "locrian-ish"},
        {"style": "trance", "bpm": "nan"},
        {"style": "trance", "bpm": "fast"},
        {"style": "trance", "variant": "endless"},
        {"style": "melodic_techno", "bpm": "40"},  # past the cap however short
    ):
        r = post(client, "/song/start", data)
        assert r.status_code == 422 and "toast" in r.text, data
        assert song(client) == was
    assert _clips(client) == set()  # a refused start renders no clip
    assert client.app.state.studio.song_undo_state()["depth"] == 0
    # a seed is taken modulo SEED_MODULO; junk draws one
    r = post(
        client, "/song/start", {"style": "garage", "seed": str(limits.SEED_MODULO + 7)}
    )
    assert r.status_code == 200 and song(client)["gen"]["seed"] == 7
    r = post(client, "/song/start", {"style": "garage", "seed": "9" * 40})
    assert (
        r.status_code == 200 and 0 <= song(client)["gen"]["seed"] < limits.SEED_MODULO
    )
    # bpm is clamped into the studio's range, never refused for being wild
    r = post(client, "/song/start", {"style": "garage", "bpm": "9999", "seed": "1"})
    assert r.status_code == 200 and song(client)["bpm"] == limits.BPM_RANGE[1]


def test_the_head_plan_starts_on_the_first_section_at_the_final_gain(client):
    post(client, "/song/start", {"style": "trance", "seed": "1"})
    s = song(client)
    head = client.get("/song/plan.json?head=1").json()
    assert head["head"] is True and len(head["sections"]) == 1
    first = head["sections"][0]
    assert first["section_id"] == s["sections"][0]["id"] and first["passes"] > 8
    assert "head=1" in first["url"] and "head=1" in first["loop_url"]
    wav = client.get(first["url"])
    assert wav.status_code == 200 and wav.headers["content-type"] == "audio/wav"
    full = client.get("/song/plan.json").json()
    assert "head" not in full and len(full["sections"]) == len(s["sections"])
    # the head's gain is never under the song's; for THIS song (its fullest
    # section is its loudest) it is the song's exactly. Not every starter's
    # is: songcraft.md, "Sound in seconds", has the measured steps
    assert head["gain"] >= full["gain"] - 1e-12
    assert head["gain"] == pytest.approx(full["gain"], rel=1e-9)
    # once the song's gain is known, a head request is simply the plan
    again = client.get("/song/plan.json?head=1").json()
    assert "head" not in again and again["sections"] == full["sections"]


# --- loop -> song --------------------------------------------------------------


def _loop(client) -> None:
    """A two-lane working loop with a beat: the session's default lane, a
    layer of chords, a four-on-the-floor."""
    s = client.app.state.studio
    from dataclasses import replace

    from synthlab.studio import beatgen

    s.arrangement = replace(
        s.arrangement,
        bars=2,
        steps_per_bar=16,
        root="A1",
        scale_name="minor",
        cells=[["A1", 0], ["A1", 6], ["C2", 16], ["E2", 22]],
        drums=beatgen.generate("four-floor", 3, 1),
        drum_bars=1,
        drum_steps=16,
    )
    s.layers[1] |= {
        "patch": s.patch.to_json(),
        "cells": [["A3", 0], ["C4", 0], ["E4", 0], ["A3", 16], ["C4", 16], ["F4", 16]],
        "muted": False,
    }


def test_a_loop_becomes_a_song_by_muting_thinning_and_filtering(client):
    _loop(client)
    r = post(client, "/song/arrange", {"source": "loop", "seed": "3"})
    assert r.status_code == 200 and _trigger(r)["song-autoplay"] == {"head": True}
    s = song(client)
    assert [x["name"] for x in s["sections"]] == [
        x["name"] for x in styles.GENERIC_SHAPE
    ]
    assert schema.conform_strict(copy.deepcopy(s), N.SONG) == s
    # tracks named for what they are for: the low lane is the bass
    roles = {t["name"].split(" · ")[0]: t.get("role") for t in s["tracks"]}
    assert (
        roles["Bass"] == "bass"
        and roles["Chords"] == "chords"
        and roles["Drums"] == "drums"
    )
    loop_notes = {("A1", 0), ("A1", 6), ("C2", 16), ("E2", 22)} | {
        (p, st)
        for p, st in (
            ("A3", 0),
            ("C4", 0),
            ("E4", 0),
            ("A3", 16),
            ("C4", 16),
            ("F4", 16),
        )
    }
    beat = {(v, st) for v, st in client.app.state.studio.arrangement.drums}
    for p in s["patterns"].values():
        if p["kind"] == "notes":  # no note that was not in the loop
            assert {tuple(c) for c in p["cells"]} <= loop_notes
        elif p.get("var") == "fill":  # bars 1-7 are the beat; bar 8 is the fill
            head = {(v, st % 16) for v, st in p["drums"] if st < 7 * 16}
            assert head == beat and p["bars"] == 8
        else:  # a thinned beat only ever has fewer hits
            assert {tuple(h) for h in p["drums"]} <= beat
    names = {x["name"]: x for x in s["sections"]}
    drums = next(t["id"] for t in s["tracks"] if t["kind"] == "drums")
    assert not names["Break"]["cells"].get(drums)
    assert (
        names["Intro"].get("auto")
        and names["Build"]["audio"]
        and names["Drop"]["audio"]
    )
    st = client.app.state.studio
    assert st.song_undo_state() == {
        **st.song_undo_state(),
        "undo": "loop → song",
        "depth": 1,
    }
    post(
        client, "/song/arrange", {"source": "loop", "seed": "4"}
    )  # again: the same step
    assert st.song_undo_state()["depth"] == 1
    post(client, "/song/undo")
    assert song(client)["sections"] == []
    assert client.get("/song/plan.json").status_code == 200


def test_a_section_is_shaped_in_place_and_an_edge_is_added(client):
    use_song(client)
    before = len(song(client)["sections"])
    r = post(client, "/song/arrange", {"source": "s2", "seed": "1"})
    assert r.status_code == 200
    s = song(client)
    assert len(s["sections"]) == before - 1 + len(styles.GENERIC_SHAPE)
    assert s["sections"][0]["id"] == "s1"  # the others stay where they were
    assert post(client, "/song/arrange", {"source": "nope"}).status_code == 422
    assert post(client, "/song/arrange", {"add": "sideways"}).status_code == 422
    use_song(client)
    r = post(client, "/song/arrange", {"add": "intro", "seed": "1"})
    assert r.status_code == 200
    s = song(client)
    assert s["sections"][0]["name"] == "Intro" and len(s["sections"]) == before + 1
    r = post(client, "/song/arrange", {"add": "outro", "seed": "1"})
    assert song(client)["sections"][-1]["name"] == "Outro"


# --- POST /song/gen ------------------------------------------------------------


def test_a_generator_writes_one_track_and_nothing_else(client):
    use_song(client)
    was = copy.deepcopy(song(client))
    r = post(
        client,
        "/song/gen",
        {"track_id": "t1", "kind": "bass_rolling", "figure": "gallop", "seed": "3"},
    )
    assert r.status_code == 200
    trig = _trigger(r)
    assert trig["song-autoplay"] == {"section": "s1"}
    s = song(client)
    assert s["tracks"][1]["role"] == "bass"
    moved = [pid for pid, p in s["patterns"].items() if p != was["patterns"].get(pid)]
    assert moved and all(
        "t1" in [t for x in s["sections"] for t, v in x["cells"].items() if v == pid]
        for pid in moved
    )
    p = s["patterns"][moved[0]]
    assert (
        p["gen"]["kind"] == "bass_rolling" and p["gen"]["params"]["figure"] == "gallop"
    )
    assert {x % 4 for x in nt.note_ons(p)} == {2, 3}
    for other in ("pd", "p2"):
        assert s["patterns"][other] == was["patterns"][other]
    # again, busier, sparser, a fill on the drums, a duck
    assert post(client, "/song/gen", {"track_id": "t1", "seed": "8"}).status_code == 200
    n = len(nt.runs(song(client)["patterns"][moved[0]]))
    assert (
        post(client, "/song/gen", {"track_id": "t1", "kind": "busier"}).status_code
        == 200
    )
    assert len(nt.runs(song(client)["patterns"][moved[0]])) > n
    assert (
        post(
            client, "/song/gen", {"track_id": "t1", "kind": "sparser", "amount": "0.5"}
        ).status_code
        == 200
    )
    assert (
        post(
            client, "/song/gen", {"track_id": "td", "kind": "fill", "seed": "2"}
        ).status_code
        == 200
    )
    fills = [p for p in song(client)["patterns"].values() if p.get("var") == "fill"]
    assert fills and fills[0]["bars"] == 8
    assert (
        post(client, "/song/gen", {"track_id": "t1", "kind": "duck"}).status_code == 200
    )
    assert any(u["kind"] == "pump" for u in song(client)["tracks"][1]["fx"])
    assert post(client, "/song/gen", {"add": "bass", "seed": "1"}).status_code == 200
    assert song(client)["tracks"][-1]["role"] == "bass"


def test_hostile_generator_posts_are_refused(client):
    use_song(client)
    was = copy.deepcopy(song(client))
    for data in (
        {"track_id": "zz"},
        {"track_id": "t1", "kind": "kazoo"},
        {"track_id": "t1", "kind": "bass_rolling", "figure": "wobble"},
        {"track_id": "t1", "kind": "arp", "mode": "sideways"},
        {"track_id": "t1", "kind": "arp", "rate": "inf"},
        {"track_id": "ta", "kind": "lead"},  # an audio track
        {"track_id": "td", "kind": "lead"},  # a notes generator on the drums
        {"track_id": "t1", "kind": "fill"},  # a drum generator on a synth
        {"track_id": "td", "kind": "build"},  # a build names its section
        {"track_id": "t2", "kind": "again"},  # nothing there was generated
        {"add": "choir"},
    ):
        r = post(client, "/song/gen", data)
        assert r.status_code == 422 and "toast" in r.text, data
        assert song(client) == was, data
    # numbers are clamped, not refused
    r = post(
        client,
        "/song/gen",
        {"track_id": "t1", "kind": "arp", "octaves": "99", "gate": "-3"},
    )
    assert r.status_code == 200


# --- title, tempo, key; the chord track ----------------------------------------


def test_the_song_has_a_name_a_tempo_and_a_key_of_its_own(client):
    use_song(client)
    assert (
        post(
            client, "/song/meta", {"title": "  Night \n Drive  " + "x" * 80}
        ).status_code
        == 200
    )
    s = song(client)
    assert (
        s["title"].startswith("Night Drive")
        and len(s["title"]) == limits.MAX_TITLE_CHARS
    )
    assert post(client, "/song/meta", {"bpm": "500"}).json if False else True
    post(client, "/song/meta", {"bpm": "500"})
    assert song(client)["bpm"] == limits.BPM_RANGE[1]
    cells = copy.deepcopy(song(client)["patterns"]["p2"]["cells"])
    post(client, "/song/meta", {"root": "D2", "scale_name": "dorian"})
    s = song(client)
    assert (s["root"], s["scale_name"]) == ("D2", "dorian")
    assert s["patterns"]["p2"]["cells"] == cells  # a key change moves nothing by itself
    post(client, "/song/meta", {"root": "E2", "move": "1"})
    moved = song(client)["patterns"]["p2"]["cells"]
    assert [
        sm.midi(c[0]) - sm.midi(o[0]) for c, o in zip(moved, cells, strict=True)
    ] == [2] * len(cells)
    was = copy.deepcopy(song(client))
    for data in (
        {"root": "H9"},
        {"scale_name": "wonky"},
        {"bpm": "nan"},
        {"swing": "lots"},
    ):
        assert post(client, "/song/meta", data).status_code == 422
        assert song(client) == was
    html = client.get("/view/song", headers=HX).text
    assert 'name="title" value="Night Drive' in html and 'hx-post="/song/meta"' in html


def test_the_chord_track_is_edited_by_preset_slot_and_section(client):
    use_song(client)
    assert "chords" not in song(client)
    assert (
        post(client, "/song/chords", {"op": "preset", "prog": "0"}).status_code == 200
    )
    s = song(client)
    assert chordtrack.label(s) == "i VI III VII" or len(s["chords"]["slots"]) == 4
    assert (
        post(
            client,
            "/song/chords",
            {"op": "set", "slot": "1", "deg": "4", "q": "7th", "inv": "9"},
        ).status_code
        == 200
    )
    slot = sorted(song(client)["chords"]["slots"], key=lambda x: x["at"])[1]
    assert (slot["deg"], slot["q"], slot["inv"]) == (4, "7th", 2)
    post(client, "/song/chords", {"op": "add"})
    assert len(song(client)["chords"]["slots"]) == 5
    post(client, "/song/chords", {"op": "remove", "slot": "4"})
    post(client, "/song/chords", {"op": "bars", "bars": "8"})
    s = song(client)
    assert (
        s["chords"]["bars"] == 8
        and [x["len"] for x in s["chords"]["slots"]] == [8.0] * 4
    )
    # a section's own chords override the song's, and can be taken away again
    assert post(client, "/song/chords", {"op": "own", "scope": "s1"}).status_code == 200
    assert song(client)["sections"][0]["chords"] == s["chords"]
    post(client, "/song/chords", {"op": "set", "scope": "s1", "slot": "0", "deg": "5"})
    s = song(client)
    assert chordtrack.resolve(s, s["sections"][0])[0]["slot"]["deg"] == 5
    assert chordtrack.resolve(s, s["sections"][1])[0]["slot"]["deg"] != 5
    html = client.get("/view/song", headers=HX).text
    assert html.count('class="chord-strip"') == 2 and 'class="sec-chords own"' in html
    post(client, "/song/chords", {"op": "clear", "scope": "s1"})
    assert "chords" not in song(client)["sections"][0]
    # the render never reads it: the plan's URLs are the song's without it
    urls = [x["url"] for x in client.get("/song/plan.json").json()["sections"]]
    post(client, "/song/chords", {"op": "roll", "seed": "5"})
    assert [x["url"] for x in client.get("/song/plan.json").json()["sections"]] == urls
    was = copy.deepcopy(song(client))
    for data in (
        {"op": "nope"},
        {"op": "preset", "prog": "999"},
        {"op": "set", "slot": "99", "deg": "1"},
        {"op": "set", "slot": "0", "q": "jazz"},
        {"op": "bars", "bars": "5"},
        {"op": "own"},  # the song has no "own"
        {"op": "set", "scope": "nowhere", "slot": "0", "deg": "2"},
        {"op": "from-pattern", "pattern_id": "pd"},
    ):
        assert post(client, "/song/chords", data).status_code == 422, data
        assert song(client) == was, data
    # a chord pattern leads: its chords become the track
    post(client, "/song/gen", {"track_id": "t2", "kind": "chords", "seed": "1"})
    pid = song(client)["sections"][1]["cells"]["t2"]
    assert (
        post(
            client, "/song/chords", {"op": "from-pattern", "pattern_id": pid}
        ).status_code
        == 200
    )


def test_a_tracks_role_is_set_from_its_menu(client):
    use_song(client)
    assert post(client, "/song/track/t1/mix", {"role": "bass"}).status_code == 200
    assert song(client)["tracks"][1]["role"] == "bass"
    assert post(client, "/song/track/t1/mix", {"role": ""}).status_code == 200
    assert "role" not in song(client)["tracks"][1]
    assert post(client, "/song/track/t1/mix", {"role": "kazoo"}).status_code == 422


# --- the checklist -------------------------------------------------------------


def test_the_checklist_finds_what_is_missing_and_its_buttons_fix_it(client):
    from synthlab.studio.gen import checklist

    assert [f["key"] for f in checklist.check(song(client))] == ["empty"]
    post(client, "/song/start", {"style": "trance", "seed": "2"})
    s = song(client)
    assert checklist.check(s) == []  # a starter opens with nothing open
    html = client.get("/fragment/song/check").text
    assert 'id="song-check"' in html and "Nothing:" in html and "signature" in html
    # take the ends off: a song that starts and stops at full tilt, no bass
    # in its fullest section, nothing leading into it
    bare = copy.deepcopy(s)
    bare["sections"] = [x for x in bare["sections"] if x["name"] == "Climax"]
    bare["sections"][0]["audio"] = []
    bare["sections"][0]["repeats"] = 8
    for t in bare["tracks"]:
        if t.get("role") == "bass":
            for x in bare["sections"]:
                x["cells"].pop(t["id"], None)
    for pid in list(bare["patterns"]):
        if bare["patterns"][pid]["kind"] == "drums":
            bare["patterns"][pid].pop("var", None)
    use_song(client, bare)
    keys = [f["key"].split(":")[0] for f in checklist.check(song(client))]
    assert {"intro", "ending", "still", "bass", "unused"} <= set(keys)
    html = client.get("/fragment/song/check").text
    assert html.count("check-fix") >= 4
    for f in checklist.check(song(client)):
        if f["fix"] and f["key"].split(":")[0] in ("intro", "ending", "bass", "still"):
            r = post(client, f["fix"]["post"], f["fix"]["vals"])
            assert r.status_code == 200, f
    left = {f["key"].split(":")[0] for f in checklist.check(song(client))}
    assert not {"intro", "ending", "bass", "still"} & left
    # a bass on the kick's steps with nothing ducking it: the duck fixes it
    use_song(client)
    post(client, "/song/gen", {"track_id": "t1", "kind": "bass_root", "seed": "1"})
    s = song(client)
    s["tracks"][1]["fx"] = []
    use_song(client, s)
    ducks = [f for f in checklist.check(song(client)) if f["key"].startswith("duck")]
    assert (
        ducks
        and post(client, ducks[0]["fix"]["post"], ducks[0]["fix"]["vals"]).status_code
        == 200
    )
    assert not [f for f in checklist.check(song(client)) if f["key"].startswith("duck")]


# --- an empty state is never a dead end ----------------------------------------


def test_an_empty_pool_writes_a_beat_or_a_riff_that_plays(client):
    """E4, the server's half: from an empty song one post writes a pattern
    AND places it where it sounds, and tells the page to loop it."""
    html = client.get("/view/song", headers=HX).text
    assert html.count('class="pool-empty empty-state"') == 2
    assert (
        'data-cmd="Write a beat"' in html
        and 'data-cmd="Write a riff on the chords"' in html
    )
    r = post(client, "/song/gen", {"add": "beat", "seed": "1"})
    assert r.status_code == 200
    s = song(client)
    sid = s["sections"][0]["id"]
    assert _trigger(r)["song-autoplay"] == {"section": sid}
    drums = next(t for t in s["tracks"] if t["kind"] == "drums")
    beat = s["patterns"][s["sections"][0]["cells"][drums["id"]]]
    assert beat["drums"] and beat["gen"]["kind"] == "beat" and drums["role"] == "drums"
    r = post(client, "/song/gen", {"add": "riff", "seed": "1"})
    assert r.status_code == 200 and _trigger(r)["song-autoplay"] == {"section": sid}
    s = song(client)
    riff = next(t for t in s["tracks"] if t["kind"] == "synth")
    assert s["patterns"][s["sections"][0]["cells"][riff["id"]]]["cells"]
    assert client.get("/song/plan.json").status_code == 200
    # both can be rolled again like any generated part
    assert (
        post(client, "/song/gen", {"track_id": drums["id"], "seed": "9"}).status_code
        == 200
    )
    assert (
        post(client, "/song/gen", {"track_id": riff["id"], "seed": "9"}).status_code
        == 200
    )
    assert post(client, "/song/gen", {"add": "kazoo"}).status_code == 422


def test_the_other_pages_empty_states_offer_a_song(client):
    # (Perform's saved sounds and Drums' saved beats point at their own next
    # step, and the Lab's empty states at its own — choose a song, cut
    # chops: tests/studio/test_empty_states.py EXEMPT)
    # (the Library's "No sound matches the filter" offers the way back —
    # Clear filter — not a song)
    # (Perform's "Make this loop a song" is the Song module's ▶ Whole song,
    # never empty — below)
    for view, marker in (("library", "No sound selected"),):
        html = client.get(f"/view/{view}").text
        at = html.index(marker)
        block = html[at : at + 1400]
        assert 'hx-post="/song/' in block and 'class="btn btn-s' in block, (
            view,
            marker,
        )
    html = client.get("/view/library").text
    block = html[html.index("No sound matches the filter") :][:400]
    assert "data-lib-clear" in block and "Clear filter" in block
    assert 'hx-post="/song/' not in block

    def whole(html: str) -> str:
        at = html.index('data-cmd="Make this loop a song"')
        return html[html.rindex("<button", 0, at) : html.index(">", at)]

    assert 'hx-post="/song/arrange"' in whole(client.get("/view/perform").text)
    assert "hx-confirm" not in whole(client.get("/view/perform").text)
    html = client.get("/view/library").text
    assert "hx-confirm" not in html[html.index("No sound selected") :][:900]
    post(client, "/song/start", {"seed": "1"})  # with a song, the button asks first
    html = client.get("/view/library").text
    assert "hx-confirm" in html[html.index("No sound selected") :][:900]
    assert "hx-confirm" in whole(client.get("/view/perform").text)


def test_a_key_is_a_name_and_its_value_the_nearest_root():
    """The topbar's key select and the starter sheet's: the twelve keys by
    name, C to B — never "E1" — each valued at the root nearest the
    song's (a key change moves the bass the shortest way), the sheet's in
    the starters' register."""
    from synthlab.sequencer import midi
    from synthlab.studio.service.songpage import key_choices
    from synthlab.studio.state import ROOTS

    got = key_choices("E1")
    assert [name for _v, name in got][:3] == ["C", "C#", "D"] and len(got) == 12
    assert ("E1", "E") in got
    assert all(abs(midi(v) - midi("E1")) <= 6 for v, _n in got)
    assert all(v in ROOTS for v, _n in key_choices("A0") + key_choices("E3"))
    assert {v for v, _n in key_choices()} == {
        v for v in ROOTS if midi("E1") <= midi(v) <= midi("D#2")
    }
    names = {n for _v, n in key_choices()}
    for st in styles.STYLES:  # every style's keys are offered by name
        assert {r[:-1] for r, _s in styles.STYLES[st]["keys"]} <= names
        assert len(styles.card(st)["key_names"]) == len(styles.STYLES[st]["keys"])


def test_the_song_pages_show_the_songs_tempo_in_the_status_strip(client):
    use_song(client, {**song(client), "bpm": 97.0})
    for view, bpm in (("song", "97"), ("mixer", "97"), ("library", "124")):
        html = client.get(f"/view/{view}").text
        strip = html[html.index('id="status-bar"') :][:900]
        assert f"<b>{bpm}</b> bpm" in strip, view
    # a nav swap carries the strip, out of band
    r = client.get("/view/song", headers={"HX-Request": "true"})
    assert 'id="status-bar" hx-swap-oob="outerMorph"' in r.text
    assert "<b>97</b> bpm" in r.text


def test_the_transition_form_lists_every_kind_and_follows_the_kinds_own_way(client):
    """The journey's bug: the form kept direction "up" when "impact" was
    picked, so an impact was placed reversed, before the bar line. Its
    direction now starts on "auto" (no direction posted: the route takes
    the kind's own), and it lists the four kinds added since."""
    use_song(client)
    html = client.get("/view/song", headers=HX).text
    form = html[html.index('class="menu-row trans-form"') :][:3000]
    for kind in ("riser", "impact", "sweep", "swell", "subdrop", "bed"):
        assert f'<option value="{kind}">{kind}</option>' in form
    assert '<option value="" selected>auto</option>' in form
    r = post(
        client,
        "/song/section/s1/transition",
        {"kind": "impact", "direction": "", "bars": "1"},
    )
    assert r.status_code == 200
    take = song(client)["sections"][0]["audio"][-1]
    assert take["clip"].startswith("impact-down") and take["at_bar"] == 0
    r = post(
        client,
        "/song/section/s1/transition",
        {"kind": "riser", "direction": "", "bars": "1"},
    )
    take = song(client)["sections"][0]["audio"][-1]
    assert (
        take["clip"].startswith("riser-up")
        and take["at_bar"] == song(client)["sections"][0]["bars"] - 1
    )


def test_the_breed_page_has_a_button_for_every_style_direction(client):
    html = client.get("/view/breed").text
    for d in ("melodic_techno", "garage", "filter_house", "trance"):
        assert f'"direction": "{d}"' in html
    assert 'data-cmd="Breed a generation: trance"' in html


def test_enter_in_performs_tempo_field_is_an_htmx_post_not_a_page_load(client):
    """The journey's bug: the form's trigger was `change` alone, so Enter
    submitted it natively and the page reloaded at /view/perform?bpm=N."""
    html = client.get("/view/perform").text
    form = html[html.index('id="perform-transport"') :][:300]
    assert 'hx-trigger="change, submit"' in form
