"""The playlist's structural edits (`studio/songops.py`): a cut at a bar, unrolled
repeats, inserted and deleted bars, a duplicate with patterns of its own,
a range of cells, the energy lane.

Three things are held here:

- the model: what each edit does to patterns, spans, lanes, takes,
  repeats, chords, energy and whole stems, and what it refuses;
- **exactness**: a cut section renders what the uncut one did — compared
  through the service's own stitching (`svc._song_raw`: heads, repeats and
  tails), one piece and chunked, at the tolerance the span tests hold
  (1e-9) — and where it cannot, the size of the difference is pinned and
  the edit says so in the notes it returns;
- insert and delete move the timeline and nothing else.

The routes over these are `test_song_arr.py`.
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.core import SR
from synthlab.studio import clips, limits, render
from synthlab.studio import song_model as sm
from synthlab.studio import songops as so
from synthlab.studio.gen import chordtrack
from synthlab.studio.server import create_app
from tests.studio.test_song_routes import song_dict, use_song
from tests.studio.test_song_spans import (
    ATOL,
    BAR,
    BEAT,
    LINE,
    REVERB,
    RIFF,
    SLID,
    build,
    clean,
)

# a 4-bar line: a held note, a note late in every bar, nothing over a bar line
LONG4 = {
    "bars": 4,
    "steps_per_bar": 16,
    "cells": [
        ["A2", 0],
        ["A2", 1],
        ["E3", 20],
        ["G3", 37],
        ["G3", 38],
        ["C3", 52],
        ["D3", 60],
        ["D3", 61],
    ],
    "velocities": {"0": 0.7, "20": 1.0, "37": 0.6},
}
# nothing sounds in the last quarter of any bar: nothing rings over a cut
EARLY = {
    "bars": 2,
    "steps_per_bar": 16,
    "cells": [["A2", 0], ["E3", 4], ["E3", 5], ["C3", 18], ["G2", 22]],
    "velocities": {"0": 0.8, "18": 0.6},
}
KICKS = {"bars": 1, "steps": 16, "drums": [["kick", 0], ["hat", 4]], "drum_vel": {}}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        yield c


def svc_of(client):
    return client.app.state.svc


def outro(song: dict) -> dict:
    """An Outro after the section: what rings past its end is heard."""
    song["patterns"]["out"] = {"id": "out", "kind": "notes", "name": "out", **RIFF}
    song["sections"].append(
        {"id": "o", "name": "O", "bars": 1, "cells": {"lead": "out"}}
    )
    return clean(song)


def one(bars: int = 8, repeats: int = 1, **more) -> dict:
    """`test_song_spans.build`'s section — a Lead, a Pad that rings 0.7 s
    and Drums — `bars` long, `repeats` times, then the Outro."""
    spans = more.pop("spans", {})
    song = build(spans, hand=False, bars=bars, **more)
    song["sections"][0]["repeats"] = repeats
    return outro(song)


def cut_copy(song: dict, bar: int, **kw) -> tuple[dict, list[str]]:
    work = copy.deepcopy(song)
    _index, notes = so.cut(work, bar, **kw)
    return clean(work), notes


def diff(a: np.ndarray, b: np.ndarray) -> float:
    assert a.shape == b.shape
    return float(np.max(np.abs(a - b)))


def below(a: np.ndarray, b: np.ndarray) -> float:
    """How far under the mix's peak two renders differ, in dB."""
    return 20 * np.log10(max(diff(a, b), 1e-300) / float(np.max(np.abs(a))))


# === the timeline ===================================================================


def test_locate_reads_every_bar_of_every_repeat():
    song = clean(song_dict())  # Intro 2 bars; Verse 2 bars x 2
    assert so.starts(song) == [0, 2, 6]
    assert so.locate(song, 0) == (0, 0, 0)
    assert so.locate(song, 1) == (0, 0, 1)
    assert so.locate(song, 2) == (1, 0, 0)
    assert so.locate(song, 5) == (1, 1, 1)
    assert so.locate(song, 6) == (2, 0, 0)  # the song's end
    for bad in (-1, 7, 10**6):
        with pytest.raises(sm.SongError, match="past the song"):
            so.locate(song, bad)


def test_a_cut_on_a_boundary_is_no_edit():
    song = clean(song_dict())
    before = copy.deepcopy(song)
    for bar in (0, 2, 6):
        assert so.cut(song, bar)[1] == []
    assert song == before


def test_a_cut_between_two_passes_divides_the_repeats():
    song = clean(song_dict())
    song["sections"][1]["repeats"] = 4
    pool = dict(song["patterns"])
    index, notes = so.cut(song, 2 + 2 * 3)  # the Verse's fourth pass
    assert (index, notes) == (2, [])
    a, b = song["sections"][1:]
    assert (a["id"], a["repeats"], b["repeats"]) == ("s2", 3, 1)
    assert b["id"] != "s2" and b["cells"] == a["cells"]  # by reference
    assert song["patterns"] == pool  # no pattern was made
    assert so.starts(song)[-1] == 10  # the timeline did not move
    assert clean(copy.deepcopy(song)) == song


def test_a_cut_inside_a_pass_of_a_repeated_section_keeps_every_bar_in_order():
    """Verse (2 bars x 4) cut one bar into its third pass: the two passes
    before it stay one section, the cut pass is two, the pass after it
    one — the same bars in the same order."""
    song = clean(song_dict())
    song["sections"][1]["repeats"] = 4
    index, _notes = so.cut(song, 2 + 2 * 2 + 1)
    secs = song["sections"]
    assert [(s["bars"], s["repeats"]) for s in secs] == [
        (2, 1),
        (2, 2),
        (1, 1),
        (1, 1),
        (2, 1),
    ]
    assert secs[1]["id"] == "s2" and index == 3
    assert so.starts(song) == [0, 2, 6, 7, 8, 10]
    assert len({s["id"] for s in secs}) == 5
    assert clean(copy.deepcopy(song)) == song


def test_split_wants_a_bar_inside_its_section():
    song = clean(song_dict())
    for bar in (0, 2, 6, 9, -3):
        with pytest.raises(sm.SongError, match="inside Verse"):
            so.split(song, "s2", bar)
    with pytest.raises(sm.SongError, match="No such section"):
        so.split(song, "zz", 3)
    new, _notes = so.split(song, "s2", 3)
    assert song["sections"][2]["id"] == new


# === a window of a pattern ==========================================================


def by_ear(p: dict, phase: int, bars: int) -> set:
    """What bars [phase, phase + bars) of a pattern played round hold,
    written with a loop of its own: {(pitch or voice, step)}."""
    per = p.get("steps", p.get("steps_per_bar"))
    period = p["bars"] * per
    notes = p.get("drums", p.get("cells"))
    return {
        (a, x)
        for x in range(bars * per)
        for a, s in notes
        if s == (phase * per + x) % period
    }


@pytest.mark.parametrize("pattern", [RIFF, SLID, LINE, LONG4, BEAT])
def test_a_window_holds_the_patterns_notes_from_its_phase(pattern):
    kind = "drums" if "drums" in pattern else "notes"
    p = {"kind": kind, **copy.deepcopy(pattern)}
    for phase in range(p["bars"] + 1):
        for bars in range(1, p["bars"] + 1):
            w = so.window(p, phase, bars)
            got = {(a, s) for a, s in w.get("drums", w.get("cells"))}
            assert got == by_ear(p, phase, bars), (phase, bars)
            assert w["bars"] == bars


def test_a_rotated_window_cuts_the_note_a_repeat_attacks_again():
    """RIFF holds C3 over its last two steps AND on its first: played
    round, the repeat attacks C3 again. Rotated, that seam is inside the
    pattern, so the window says it with a cut."""
    two_bars = {"kind": "notes", **RIFF, "bars": 2}
    two_bars["cells"] = [*RIFF["cells"], ["C3", 30], ["C3", 31]]
    w = so.window(two_bars, 1, 2)
    assert ["C3", 16] in w["cuts"]  # the pattern's own step 0 lands on 16
    assert so.window({"kind": "notes", **RIFF}, 0, 1)["cuts"] == []


def test_a_window_carries_velocities_slides_and_a_beats_hits():
    w = so.window({"kind": "notes", **SLID, "bars": 1}, 0, 1)
    assert w["velocities"] == SLID["velocities"] and w["slides"] == [5]
    beat = {"kind": "drums", **BEAT, "bars": 2}
    beat["drums"] = [["kick", 0], ["snare", 24]]
    beat["hits"] = {"snare.24": {"v": 0.3, "r": 2}}
    beat["swing"] = {"hat": 0.2}
    w = so.window(beat, 1, 1)
    assert w["drums"] == [["snare", 8]] and w["hits"] == {"snare.8": {"v": 0.3, "r": 2}}
    assert w["swing"] == {"hat": 0.2}


def test_held_across_reads_a_run_over_the_bar_line():
    p = {"kind": "notes", "bars": 2, "steps_per_bar": 16, "cells": [], "cuts": []}
    p["cells"] = [["A2", 15], ["A2", 16], ["C3", 31], ["C3", 0]]
    assert so.held_across(p, 1)
    assert not so.held_across(p, 0)  # the pattern's start attacks anyway
    p["cuts"] = [["A2", 16]]
    assert not so.held_across(p, 1)
    assert not so.held_across({"kind": "drums", **BEAT}, 1)


# === exactness: a cut renders what the uncut section rendered ========================


@pytest.mark.parametrize("lead", [RIFF, SLID, LONG4], ids=["riff", "slides", "4 bars"])
@pytest.mark.parametrize("repeats", [1, 2])
def test_a_cut_at_any_bar_renders_the_uncut_song(client, lead, repeats):
    """EVERY BAR, REPEATS AND TAILS. A 1-bar riff, a 2-bar Pad that rings
    0.7 s over the cut and a beat, under a 1-bar and a 4-bar lead: a cut
    that divides every pattern shares them, one that does not gives the
    right half a window. Dry stems: exact."""
    svc = svc_of(client)
    song = one(8, repeats, lead=lead)
    whole = svc._song_raw(song)
    assert whole.shape == ((8 * repeats + 1) * BAR, 2)
    for bar in range(1, 8 * repeats):
        cut, notes = cut_copy(song, bar)
        assert notes == [], (bar, notes)
        assert so.starts(cut)[-1] == so.starts(song)[-1]
        assert bar in so.starts(cut)
        assert diff(whole, svc._song_raw(cut)) < ATOL, bar


def test_a_cut_chunked_is_the_uncut_song_in_one_piece(client, monkeypatch):
    """CHUNKED. With the cap at 4 s every flat cycle renders in 2-bar
    chunks: the cut halves chunked, against the uncut section whole."""
    svc = svc_of(client)
    song = one(8, lead=LONG4)
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 10.0)
    whole = svc._song_raw(song)
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 4.0)
    for bar in (3, 5):
        cut, _notes = cut_copy(song, bar)
        parts = sm.section_arrangement(cut, cut["sections"][1])
        assert len(render.section_windows(parts)) > 1  # it IS chunked
        assert diff(whole, svc._song_raw(cut)) < ATOL, bar


def test_a_cut_with_trimmed_blocks_keeps_each_block_where_it_was(client):
    """WITH SPANS. A block that ends before the cut stays on the left,
    one that starts after it goes right, one across it is two blocks —
    the right one a window from the cut's phase of ITS pattern."""
    svc = svc_of(client)
    song = one(8, spans={"lead": (1, 5), "pad": (2, 6), "drums": (0, 7)})
    whole = svc._song_raw(song)
    for bar in range(1, 8):
        cut, notes = cut_copy(song, bar)
        assert notes == []
        assert diff(whole, svc._song_raw(cut)) < ATOL, bar
    cut, _notes = cut_copy(song, 3)
    left, right = cut["sections"][:2]
    assert left["spans"] == {"lead": [1, 2], "pad": [2, 1]}  # the drums fill it
    assert right["spans"] == {"lead": [0, 3], "drums": [0, 4]}  # the pad fills it
    # a block wholly on one side leaves the other cell empty
    cut, _notes = cut_copy(one(8, spans={"lead": (5, 2)}), 4)
    assert cut["sections"][0]["cells"]["lead"] is None
    assert cut["sections"][1]["spans"]["lead"] == [1, 2]


def blip(data_dir, seconds: float = 1.0) -> None:
    t = np.arange(int(seconds * SR)) / SR
    tone = 0.3 * np.sin(2 * np.pi * 220 * t)
    clips.save_clip(data_dir, "blip", np.stack([tone, tone], axis=1))


def test_a_cut_with_takes_puts_each_on_its_side(client, tmp_path):
    """WITH TAKES. A take is cut at its section's end, so one that would
    play across the cut refuses it, by name; any other goes to its half
    at the bar it was on."""
    blip(tmp_path)  # half a bar at 120 bpm
    svc = svc_of(client)
    song = one(8)
    song["sections"][0]["audio"] = [
        {"track_id": "aud", "clip": "blip", "at_bar": 1},
        {"track_id": "aud", "clip": "blip", "at_bar": 5, "gain": 0.5},
    ]
    song = clean(song)
    whole = svc._song_raw(song)
    for bar in (1, 2, 4, 5, 6):
        cut, _notes = cut_copy(song, bar, take_bars={"blip": 0.5})
        assert diff(whole, svc._song_raw(cut)) < ATOL, bar
    cut, _notes = cut_copy(song, 4, take_bars={"blip": 0.5})
    assert [a["at_bar"] for a in cut["sections"][0]["audio"]] == [1]
    assert [(a["at_bar"], a["gain"]) for a in cut["sections"][1]["audio"]] == [(1, 0.5)]
    before = copy.deepcopy(song)
    with pytest.raises(sm.SongError, match="blip plays across bar 3"):
        so.cut(song, 2, take_bars={"blip": 1.5})
    assert song == before


def test_a_cut_under_a_whole_stem_leaves_it_where_it_is(client, tmp_path):
    """WITH A LONG ITEM. A cut moves no bar, so the stem's cuts fall on
    the same samples: each new pass plays the slice it played."""
    from tests.studio.test_song_long import make_source, place

    sid = make_source(tmp_path, seconds=20)
    svc = svc_of(client)
    song = one(8)
    song["tracks"][3]["id"] = "ta"  # `place` puts the stem on "ta"
    song = clean(song)
    place(song, tmp_path, sid, at_bar=1)
    held = copy.deepcopy(song["long"])
    whole = svc._song_raw(song)
    for bar in (3, 4):
        cut, _notes = cut_copy(song, bar)
        assert cut["long"] == held
        assert diff(whole, svc._song_raw(cut)) < ATOL, bar
    assert so.long_under(song, 4) and not so.long_under(song, 60)


def quiet_song(lanes: dict) -> dict:
    """Nothing sounds in the last quarter of any bar (EARLY, a kick on
    the one), on a sound that rings 50 ms: nothing rings over a cut."""
    song = build({}, hand=False, bars=8, lead=EARLY, without=("pad", "drums"))
    song["patterns"]["drums"] = {"id": "drums", "kind": "drums", "name": "k", **KICKS}
    song["sections"][0]["cells"]["drums"] = "drums"
    song["tracks"][2]["drum_params"] = {"kick": {"decay": 0.12}}
    song["sections"][0]["auto"] = lanes
    return outro(song)


LANES = {
    "lead": [{"target": "cutoff", "points": [[0.0, 0.1], [0.5, 0.9], [1.0, 0.3]]}],
    "drums": [{"target": "gain", "points": [[0.0, 0.2], [1.0, 0.9]]}],
}


def test_a_cut_under_a_lane_is_the_same_curve(client):
    """WITH A LANE. The lane's points are divided at the cut by linear
    interpolation: the same curve over the two halves. Where nothing
    rings over the cut the render is the uncut one."""
    svc = svc_of(client)
    song = quiet_song(LANES)
    whole = svc._song_raw(song)
    plain = svc._song_raw(quiet_song({}))
    assert diff(whole, plain) > 1e-3  # the lanes are heard
    for bar in (2, 3, 5):
        cut, notes = cut_copy(song, bar)
        assert notes == ["a lane holds its value at the cut for what rings on"]
        assert diff(whole, svc._song_raw(cut)) < ATOL, bar
    cut, _notes = cut_copy(song, 4)  # the cutoff lane's own breakpoint
    left, right = cut["sections"][:2]

    def lane(sec, tid):
        return np.array(sec["auto"][tid][0]["points"])

    # (a sample short of the round numbers: see `lane_halves`)
    np.testing.assert_allclose(lane(left, "lead"), [[0, 0.1], [1, 0.9]], atol=1e-5)
    np.testing.assert_allclose(lane(right, "lead"), [[0, 0.9], [1, 0.3]], atol=1e-5)
    np.testing.assert_allclose(lane(left, "drums"), [[0, 0.2], [1, 0.55]], atol=1e-5)


@pytest.mark.parametrize(
    "tid, target, points",
    [
        ("pad", "gain", [[0.0, 0.2], [1.0, 0.9]]),
        ("lead", "cutoff", [[0.0, 0.1], [0.5, 0.9], [1.0, 0.3]]),
        ("pad", "cutoff", [[0.0, 0.8], [1.0, 0.2]]),
    ],
    ids=["level", "field, main lane", "field, a layer"],
)
def test_what_rings_over_a_cut_holds_the_lanes_value_there(client, tid, target, points):
    """NOT EXACT, and said so: a release or a tail that rings past the
    cut plays at the lane's value AT the cut, where the uncut section's
    went on following the lane. Measured on a Pad that rings 0.7 s under a
    lane crossing its whole range: 50 dB under the mix's peak or lower."""
    svc = svc_of(client)
    song = one(8)
    song["sections"][0]["auto"] = {tid: [{"target": target, "points": points}]}
    song = clean(song)
    whole = svc._song_raw(song)
    for bar in (3, 4):
        cut, notes = cut_copy(song, bar)
        assert "a lane holds its value at the cut for what rings on" in notes
        assert below(whole, svc._song_raw(cut)) < -45.0, bar


def test_a_cut_through_linear_effects_is_exact_and_others_say_they_restart(client):
    """FX. Each stem's chain runs once over its cycle and what rings past
    it; a reverb, a filter and a comb are linear, so the left half's ring
    folded into the right half is the chain over the whole. (Both sections are
    flat cycles here — a trimmed beat makes them so.) A drive or a phaser
    is not linear, and a delay's echoes outlast the ring a stem is rendered
    for (four beats and a second past its last note): the cut says so, and
    it is not exact."""
    svc = svc_of(client)

    def with_fx(pad_fx, lead_fx=()):
        song = one(8, spans={"drums": (0, 7)})
        song["tracks"][1]["fx"] = pad_fx
        song["tracks"][0]["fx"] = list(lead_fx)
        return clean(song)

    lowpass = [{"kind": "lowpass", "params": {"cutoff": 900.0}}]
    song = with_fx(REVERB + [{"kind": "comb", "params": {}}], lowpass)
    whole = svc._song_raw(song)
    assert diff(whole, svc._song_raw(with_fx([]))) > 1e-3  # the chains are heard
    for bar in (3, 4):
        cut, notes = cut_copy(song, bar)
        assert notes == []
        assert diff(whole, svc._song_raw(cut)) < ATOL, bar
    for kind in ("drive", "phaser", "delay"):
        song = with_fx([{"kind": kind, "params": {}}])
        cut, notes = cut_copy(song, 3)
        assert notes == [f"Pad's {kind} restarts at the cut"]
        if kind != "drive":  # (memoryless: it differs only where rings overlap)
            assert diff(svc._song_raw(song), svc._song_raw(cut)) > 1e-4


def test_a_cut_names_a_held_note_a_slide_an_arp_and_a_curve():
    """What a section boundary always does, said when a cut makes one."""
    song = build({}, hand=False, bars=4, without=("pad", "drums"))
    p = song["patterns"]["lead"]
    p.update(bars=2, cells=[["A2", 15], ["A2", 16], ["E3", 20]], slides=[20])
    song = clean(song)
    _cut, notes = cut_copy(song, 1)
    assert notes == ["Lead holds a note across"]
    p = song["patterns"]["lead"]
    p.update(cells=[["A2", 2], ["E3", 20]], cuts=[])
    song["tracks"][0]["curves"] = {"cutoff": [[0.0, 0.2], [1.0, 0.9]]}
    _cut, notes = cut_copy(clean(song), 1)
    assert notes == ["Lead's slide starts again", "Lead's curve restarts"]
    p.update(arp_mode="up", slides=[])
    song["tracks"][0]["curves"] = {}
    _cut, notes = cut_copy(clean(song), 2)
    assert notes == ["Lead's arp counts again"]


def eight() -> dict:
    """An 8-bar section over one 8-bar pattern (what every generated part
    is): a note early in every bar, and a held one."""
    song = build({}, hand=False, bars=8, without=("pad", "drums"))
    cells = [["A2", 16 * bar + 2] for bar in range(8)] + [["E3", 70], ["E3", 71]]
    song["patterns"]["lead"].update(bars=8, cells=cells)
    return outro(song)


def test_an_eight_bar_pattern_cuts_at_any_bar_with_a_second_cut(client):
    """A pattern is 1, 2, 3, 4 or 8 bars, so a cut that would leave 5, 6 or
    7 bars of an 8-bar one cannot stand alone: the pass is also cut four
    bars on (or back), every part gets a window it can have, and the cut
    says so. It still renders the uncut section."""
    svc = svc_of(client)
    song = eight()
    whole = svc._song_raw(song)
    shapes = {}
    for bar in range(1, 8):
        cut, notes = cut_copy(song, bar)
        shapes[bar] = [s["bars"] for s in cut["sections"][:-1]]
        assert bar in so.starts(cut)  # the bar asked for IS a boundary
        assert diff(whole, svc._song_raw(cut)) < ATOL, bar
        if bar == 4:
            assert notes == []
        else:
            assert len(notes) == 1 and notes[0].startswith("also cut at bar "), notes
            left = max(bar, 8 - bar)
            assert notes[0].endswith(f"(lead is 8 bars and cannot be {left})")
    assert shapes == {
        1: [1, 4, 3],
        2: [2, 4, 2],
        3: [3, 4, 1],
        4: [4, 4],
        5: [1, 4, 3],
        6: [2, 4, 2],
        7: [3, 4, 1],
    }
    cut, notes = cut_copy(song, 2)
    assert notes == ["also cut at bar 7 (lead is 8 bars and cannot be 6)"]
    names = [s["name"] for s in cut["sections"][:3]]
    assert names == ["S", "S ′", "S ′′"]
    parts = [cut["patterns"][s["cells"]["lead"]] for s in cut["sections"][:3]]
    assert [p["bars"] for p in parts] == [2, 4, 2]
    assert all(p["name"] == "lead ↻" and "gen" not in p for p in parts)
    # the index it answers with is the section that starts at the bar
    work = copy.deepcopy(song)
    index, _notes = so.cut(work, 6)
    assert so.starts(work)[index] == 6


def test_a_cut_no_second_cut_can_mend_is_refused_and_changes_nothing():
    """`_cut` alone (no further cut) refuses, by the pattern's name."""
    song = eight()
    before = copy.deepcopy(song)
    with pytest.raises(so.PatternLength, match="lead is 8 bars: a cut that leaves 6"):
        so._cut(copy.deepcopy(song), 2)
    assert so._cut(copy.deepcopy(song), 2, (9,)) is None  # outside the pass
    assert song == before


def test_a_cut_right_half_can_play_patterns_of_its_own():
    """`unique`: the right half's shared patterns are copies (one a
    pattern), so an edit there no longer reaches the left."""
    song = one(8)
    shared, _notes = cut_copy(song, 4)
    assert shared["sections"][0]["cells"] == shared["sections"][1]["cells"]
    own, _notes = cut_copy(song, 4, unique=True)
    left, right = own["sections"][:2]
    assert set(left["cells"].values()).isdisjoint(right["cells"].values())
    for tid, pid in right["cells"].items():
        was = own["patterns"][left["cells"][tid]]
        assert sm.content_hash(own["patterns"][pid]) == sm.content_hash(was)


def test_a_cut_carries_energy_and_the_chords_phase():
    """The harmony reads on from where it was: a section's chord track
    restarts with the section, so the right half gets the one in force,
    rotated to the cut."""
    song = one(8)
    song["chords"] = chordtrack.from_degrees((1, 6, 3, 7), 4)
    song["sections"][0]["energy"] = 0.7
    song = clean(song)
    sec = song["sections"][0]
    whole = chordtrack.resolve(song, sec, 8, 16)

    def degrees(resolved, lo, hi):
        return [chordtrack.chord_at(resolved, s)["slot"]["deg"] for s in range(lo, hi)]

    for bar in (1, 2, 4, 5, 6):
        cut, _notes = cut_copy(song, bar)
        left, right = cut["sections"][:2]
        assert left["energy"] == right["energy"] == 0.7
        a = chordtrack.resolve(cut, left, left["bars"], 16)
        b = chordtrack.resolve(cut, right, right["bars"], 16)
        got = degrees(a, 0, bar * 16) + degrees(b, 0, (8 - bar) * 16)
        assert got == degrees(whole, 0, 128), bar
        assert ("chords" in right) == (bar % 4 != 0)


def test_a_divided_lane_reads_sample_for_sample_what_the_whole_read():
    """`curves.lane_array` puts sample i at i / (n - 1). The halves are
    cut so each of their samples sits on the whole lane's own position —
    not at k / bars, which is a sample off by the end of a half."""
    from synthlab.studio.curves import lane_array

    pts = [[0.0, 0.1], [0.25, 0.9], [0.6, 0.2], [1.0, 0.7]]
    n = 8 * BAR
    whole = lane_array(pts, "gain", n)
    for bars in (1, 2, 3, 5, 7):
        cut = bars * BAR
        left, right = so.lane_halves(pts, cut, n)
        got = np.concatenate(
            [lane_array(left, "gain", cut), lane_array(right, "gain", n - cut)]
        )
        np.testing.assert_allclose(got, whole, atol=1e-12)
    # ... against the naive division, which is not
    naive = lane_array(so.cut_lane(pts, 0.0, 3 / 8), "gain", 3 * BAR)
    assert np.max(np.abs(naive - whole[: 3 * BAR])) > 1e-7


def clean_lane(points: list) -> list:
    from synthlab.studio import schema

    lane = {"target": "gain", "points": points}
    return schema.conform(lane, schema.nodes().LANE)["points"]


def test_a_divided_lane_never_grows_past_its_cap():
    full = [[i / 20, (i % 3) / 2] for i in range(limits.MAX_LANE_POINTS)]
    left, right = so.lane_halves(full, 9 * BAR, 10 * BAR)  # every point is left
    assert len(left) == limits.MAX_LANE_POINTS and len(right) == 2
    assert right[0][1] == right[1][1] == full[-1][1]  # held
    assert clean_lane(left) == left and clean_lane(right) == right


# === unroll =========================================================================


def test_unroll_makes_a_section_a_pass_and_renders_the_same(client):
    """A section x3 with a lane, a reverb and a trimmed block: three
    sections, each its one pass. Exact — a later pass folded its own tail,
    and now folds the same cycle's tail as the section before it."""
    svc = svc_of(client)
    song = one(4, 3, spans={"lead": (1, 3)})
    song["sections"][0]["auto"] = {
        "pad": [{"target": "gain", "points": [[0.0, 0.2], [1.0, 0.9]]}]
    }
    song["sections"][0]["energy"] = 0.4
    song["tracks"][1]["fx"] = REVERB
    song = clean(song)
    whole = svc._song_raw(song)
    ids = so.unroll(song, "s")
    song = clean(song)
    secs = song["sections"][:3]
    assert ids == [s["id"] for s in secs] and ids[0] == "s" and len(set(ids)) == 3
    assert [s["name"] for s in secs] == ["S", "S 2", "S 3"]
    assert all(s["repeats"] == 1 and s["energy"] == 0.4 for s in secs)
    assert all(s["auto"] == secs[0]["auto"] for s in secs)
    assert all(s["spans"] == {"lead": [1, 3]} for s in secs)
    assert all(s["cells"] == secs[0]["cells"] for s in secs)  # by reference
    assert diff(whole, svc._song_raw(song)) < ATOL


def test_unroll_refuses_a_section_that_plays_once_and_one_past_the_cap():
    song = clean(song_dict())
    with pytest.raises(sm.SongError, match="plays once"):
        so.unroll(song, "s1")
    song["sections"] += [
        {"id": f"x{i}", "name": "X", "bars": 1} for i in range(limits.MAX_SECTIONS - 2)
    ]
    song = clean(song)
    before = copy.deepcopy(song)
    with pytest.raises(sm.SongError, match="at most 32 sections"):
        so.unroll(song, "s2")  # x2: one more than fits
    assert song == before


# === insert and delete: the timeline moves and nothing else =========================


def test_inserted_bars_are_silence_the_song_rings_into(client):
    """Two bars at a boundary: everything before it is the same samples,
    everything after it the same two bars later; in between, only what
    the section before it left ringing."""
    svc = svc_of(client)
    song = one(4)
    whole = svc._song_raw(song)
    sid, notes = so.insert_bars(song, 4, 2)
    song = clean(song)
    new = song["sections"][1]
    assert (new["id"], new["bars"], new["repeats"], notes) == (sid, 2, 1, [])
    assert not any(new["cells"].values()) and new["audio"] == []
    assert new["name"] == "2 bars"
    got = svc._song_raw(song)
    cut, gap = 4 * BAR, 2 * BAR
    assert got.shape == (whole.shape[0] + gap, 2)
    np.testing.assert_allclose(got[:cut], whole[:cut], atol=ATOL)
    ring = got[cut : cut + gap]
    assert 0 < np.max(np.abs(ring)) and not np.any(ring[SR:])  # the Pad's 0.7 s
    late = got[cut + gap :].copy()
    late[: len(ring)] += ring[: len(late)]  # the ring fell on the Outro's head
    np.testing.assert_allclose(late, whole[cut:], atol=ATOL)


def test_insert_inside_a_section_cuts_it_first_and_delete_takes_it_back():
    song = one(8)
    before = copy.deepcopy(song)
    sid, _notes = so.insert_bars(song, 4, 4)  # a cut that divides every pattern
    assert [(s["bars"]) for s in song["sections"]] == [4, 4, 4, 1]
    assert song["sections"][1]["id"] == sid
    gone, _notes = so.delete_bars(song, 4, 8)
    assert gone == 4 and so.starts(song)[-1] == 9
    # the two halves are what a cut at bar 5 alone leaves
    want, _notes = cut_copy(before, 4)
    new_id = {song["sections"][1]["id"]: want["sections"][1]["id"]}
    got = json.loads(json.dumps(clean(song)))
    for sec in got["sections"]:
        sec["id"] = new_id.get(sec["id"], sec["id"])
    assert got == want


def test_delete_bars_cuts_at_both_ends_and_drops_only_what_it_made(client):
    """Bars 3-6 of an 8-bar section over a 4-bar lead: the left keeps two
    bars of it, the right gets bars 7-8 — and the window made for bars
    3-4, which nothing plays, is not left in the pool."""
    svc = svc_of(client)
    song = one(8, lead=LONG4)
    whole = svc._song_raw(song)
    pool = set(song["patterns"])
    gone, notes = so.delete_bars(song, 2, 6)
    song = clean(song)
    assert (gone, notes) == (4, [])
    assert [s["bars"] for s in song["sections"]] == [2, 2, 1]
    used = {p for s in song["sections"] for p in s["cells"].values() if p}
    assert set(song["patterns"]) - pool <= used  # no orphan window
    got = svc._song_raw(song)
    np.testing.assert_allclose(got[: 2 * BAR], whole[: 2 * BAR], atol=ATOL)
    with pytest.raises(sm.SongError, match="Pick the bars"):
        so.delete_bars(song, 3, 3)
    with pytest.raises(sm.SongError, match="Pick the bars"):
        so.delete_bars(song, 0, 99)


def test_structural_edits_never_move_a_whole_stem(tmp_path):
    from tests.studio.test_song_long import audio_song, make_source, place

    sid = make_source(tmp_path, seconds=20)
    song = audio_song((4, 2), (2, 1))
    place(song, tmp_path, sid, at_bar=3)
    held = copy.deepcopy(song["long"])
    names = [x["name"] for x in held]
    assert so.long_under(song, 0) == names and so.long_under(song, 3) == names
    assert so.long_under(song, 60) == []  # the stem is 20 s: it ends before
    so.unroll(song, "s1")
    so.cut(song, 5)
    so.insert_bars(song, 2, 4)
    so.delete_bars(song, 0, 1)
    assert clean(song)["long"] == held  # at its bar: the sections moved under it


def test_insert_is_refused_past_the_section_cap_and_clamps_its_bars():
    song = clean(song_dict())
    sid, _notes = so.insert_bars(song, 0, 10**6)
    assert sm._section(song, sid)["bars"] == limits.MAX_SECTION_BARS
    assert song["sections"][0]["id"] == sid  # before the Intro
    sid, _notes = so.insert_bars(song, so.starts(song)[-1], -5)
    assert (song["sections"][-1]["id"], song["sections"][-1]["bars"]) == (sid, 1)
    song["sections"] += [
        {"id": f"x{i}", "name": "X", "bars": 1} for i in range(limits.MAX_SECTIONS - 4)
    ]
    song = clean(song)
    before = copy.deepcopy(song)
    with pytest.raises(sm.SongError, match="at most 32 sections"):
        so.insert_bars(song, 0, 4)
    assert song == before


# === sections: a duplicate with patterns of its own, twins, energy ==================


def test_duplicate_as_a_variation_copies_each_pattern_once():
    song = clean(song_dict())
    song["sections"][1]["cells"]["t2"] = "p1"  # two tracks share Riff
    song = clean(song)
    plain = copy.deepcopy(song)
    assert so.duplicate(plain, "s2") and len(plain["patterns"]) == len(song["patterns"])
    new = so.duplicate(song, "s2", unique=True)
    sec, src = sm._section(song, new), sm._section(song, "s2")
    assert sec["cells"]["t1"] == sec["cells"]["t2"] != "p1"  # still shared, its own
    assert set(sec["cells"].values()).isdisjoint(src["cells"].values())
    assert len(song["patterns"]) == len(plain["patterns"]) + 2
    assert song["patterns"][sec["cells"]["td"]]["name"] == "Beat ′"
    assert clean(copy.deepcopy(song)) == song


def test_twins_names_the_sections_that_share_every_block():
    song = clean(song_dict())
    assert so.twins(song) == {}
    new = so.duplicate(song, "s2")
    assert so.twins(song) == {"s2": ["Verse ′"], new: ["Verse"]}
    sm.make_unique(song, new, "t1")
    assert so.twins(song) == {}
    song["sections"][0]["cells"] = {}
    assert so.twins(clean(song)) == {}  # an empty section is nobody's twin


def test_energy_is_clamped_and_taken_away():
    song = clean(song_dict())
    assert so.set_energy(song, "s1", 7) == 1.0
    assert so.set_energy(song, "s1", -1) == 0.0
    assert so.set_energy(song, "s1", 0.456) == 0.46
    assert so.set_energy(song, "s1", None) is None
    assert "energy" not in song["sections"][0]
    with pytest.raises(sm.SongError):
        so.set_energy(song, "zz", 0.5)


@pytest.mark.parametrize(
    "before, after, trend, kinds",
    [
        (0.3, 0.9, "rising", [("riser", "up", 0), ("impact", "down", 1)]),
        (0.9, 0.3, "falling", [("sweep", "down", 1)]),
        (0.5, 0.55, "level", [("impact", "down", 1)]),
        (None, 0.9, "level", [("impact", "down", 1)]),
    ],
)
def test_a_boundarys_default_transition_follows_the_energy(before, after, trend, kinds):
    """Rising: a riser that ENDS on the section before's last bar line and
    an impact on this one's first; falling: a downlifter off the bar line;
    level or undrawn: an impact."""
    song = clean(song_dict())
    for sec, e in zip(song["sections"], (before, after), strict=True):
        so.set_energy(song, sec["id"], e)
    got = so.boundary_moves(song, 1)
    ids = [s["id"] for s in song["sections"]]
    assert got["trend"] == trend
    assert [
        (m["kind"], m["direction"], ids.index(m["section_id"])) for m in got["default"]
    ] == kinds
    assert {m["kind"] for m in got["all"]} == {
        "riser",
        "impact",
        "sweep",
        "swell",
        "subdrop",
        "bed",
    }
    assert all(m["bars"] <= 2 for m in got["all"])  # both sections are 2 bars
    for bad in (0, 2, -1):
        with pytest.raises(sm.SongError):
            so.boundary_moves(song, bad)


# === arrange by energy ==============================================================


def energy_song() -> dict:
    """Four 4-bar sections, each playing a beat, a bass, chords and a
    lead; energies 0.1, 0.5, 1.0, 0.3."""
    song = sm.new_song(120.0, "A1", "minor")
    song["tracks"] = [
        {"id": "td", "kind": "drums", "name": "Drums"},
        {"id": "tb", "kind": "synth", "name": "Bass", "role": "bass"},
        {"id": "tc", "kind": "synth", "name": "Chords", "role": "chords"},
        {"id": "tl", "kind": "synth", "name": "Lead", "role": "lead"},
        {"id": "ta", "kind": "audio", "name": "FX"},
    ]
    song["patterns"] = {
        "pd": {"id": "pd", "kind": "drums", "name": "Beat", **BEAT},
        "pb": {"id": "pb", "kind": "notes", "name": "Bass", **RIFF},
        "pc": {"id": "pc", "kind": "notes", "name": "Chords", **LINE},
        "pl": {"id": "pl", "kind": "notes", "name": "Lead", **SLID},
    }
    cells = {"td": "pd", "tb": "pb", "tc": "pc", "tl": "pl"}
    song["sections"] = [
        {"id": f"s{i}", "name": n, "bars": 4, "cells": dict(cells), "energy": e}
        for i, (n, e) in enumerate(
            (("Intro", 0.1), ("Verse", 0.5), ("Drop", 1.0), ("Break", 0.3))
        )
    ]
    song["sections"][3]["audio"] = [{"track_id": "ta", "clip": "blip", "at_bar": 0}]
    song["sections"][1]["repeats"] = 2
    return clean(song)


def hits(song: dict, pid) -> set:
    p = song["patterns"][pid]
    return {tuple(x) for x in p.get("drums", p.get("cells"))}


def test_arrange_by_energy_thins_and_mutes_and_writes_no_music():
    song = energy_song()
    before = copy.deepcopy(song)
    names = so.arrange_by_energy(song, 3)
    song = clean(song)
    assert names == ["Intro", "Verse", "Break"]
    intro, verse, drop, brk = song["sections"]
    # the timeline, the ids, the names, the takes and the fullest section stand
    assert so.starts(song) == so.starts(before)
    assert [s["id"] for s in song["sections"]] == ["s0", "s1", "s2", "s3"]
    assert drop == before["sections"][2]
    assert brk["audio"] == before["sections"][3]["audio"] and verse["repeats"] == 2
    # what plays follows the energy: fewer parts where it is lower
    assert intro["cells"].get("td") is None and intro["cells"].get("tb") is None
    assert intro["cells"]["tc"] == "pc" and intro["cells"].get("tl") is None
    assert verse["cells"]["tb"] == "pb" and verse["cells"].get("tl") is None
    assert brk["cells"]["tc"] == "pc" and brk["cells"].get("tb") is None
    # every note is the source's: a part by reference, a beat only thinner
    full = hits(before, "pd")
    for sec in (intro, verse, brk):
        for tid, pid in sec["cells"].items():
            if not pid:
                continue
            if tid == "td":
                assert hits(song, pid) <= full
            else:
                assert pid == before["sections"][2]["cells"][tid]
    assert hits(song, brk["cells"]["td"]) < full  # hats alone
    # a filter opens where the lane steps up, closes where it came down
    assert any(ln["target"].startswith("fx:") for ln in intro["auto"]["tc"])
    assert "auto" in brk and "auto" not in drop


def test_arranging_by_energy_again_fills_no_pool_and_one_section_alone():
    song = energy_song()
    so.arrange_by_energy(song, 3)
    song = clean(song)
    once = copy.deepcopy(song)
    so.arrange_by_energy(song, 3)
    assert clean(song) == once  # the same derived patterns, not copies of them
    so.set_energy(song, "s0", 0.7)
    assert so.arrange_by_energy(song, 3, only="s0") == ["Intro"]
    song = clean(song)
    assert song["sections"][0]["cells"]["tb"] == "pb"  # the bass is back
    assert song["sections"][1:] == once["sections"][1:]
    with pytest.raises(sm.SongError, match="fullest"):
        so.arrange_by_energy(song, 3, only="s2")


def test_arrange_by_energy_needs_a_drawn_lane():
    song = clean(song_dict())
    before = copy.deepcopy(song)
    with pytest.raises(sm.SongError, match="Draw the energy lane"):
        so.arrange_by_energy(song)
    so.set_energy(song, "s1", 1.0)
    with pytest.raises(sm.SongError, match="as full as the fullest"):
        so.arrange_by_energy(song)
    so.set_energy(song, "s1", None)
    assert song == before


# === a range of cells ===============================================================


def test_parse_cells_refuses_a_range_over_the_cap_whole():
    song = clean(song_dict())
    assert so.parse_cells(song, "s1:td, s2:t1,s1:td") == [("s1", "td"), ("s2", "t1")]
    many = ",".join(["s1:td"] * (limits.MAX_RANGE_CELLS + 1))
    with pytest.raises(sm.SongError, match="at most 96 cells"):
        so.parse_cells(song, many)
    with pytest.raises(sm.SongError, match="at most 96 cells"):
        so.parse_cells(song, "x" * 100_000)
    for bad in ("", " , ", None, "s1", "s1:zz", "zz:td", "s1:td:extra", "<b>:td"):
        with pytest.raises(sm.SongError):
            so.parse_cells(song, bad)


def range_song() -> dict:
    """Three sections; the drums and the bass play in the first two."""
    song = song_dict()
    song["sections"][1]["repeats"] = 1
    song["sections"].append({"id": "s3", "name": "Out", "bars": 2, "cells": {}})
    song["sections"][0]["spans"] = {"t1": [1, 1]}
    song["sections"][0]["auto"] = {
        "t1": [{"target": "gain", "points": [[0, 0.2], [1, 0.8]]}]
    }
    song["sections"][0]["audio"] = [{"track_id": "ta", "clip": "blip", "at_bar": 1}]
    return clean(song)


BOX = [("s1", "td"), ("s1", "t1"), ("s2", "td"), ("s2", "t1")]


def test_clearing_a_range_empties_blocks_lanes_spans_and_takes():
    song = range_song()
    assert so.clear_cells(song, [*BOX, ("s1", "ta"), ("s3", "td")]) == 5
    song = clean(song)
    assert song["sections"][0]["cells"] == {"td": None, "t1": None}
    assert song["sections"][1]["cells"] == {"td": None, "t1": None, "t2": "p2"}
    assert "auto" not in song["sections"][0] and "spans" not in song["sections"][0]
    assert song["sections"][0]["audio"] == []


def test_pasting_a_range_is_by_reference_and_adds_sections_as_needed():
    """The 2 x 2 box put down on the second section: it lands on sections
    2 and 3, one short of nothing — and pasted on the third it needs a
    fourth, which is added with the source section's length."""
    song = range_song()
    assert so.paste_cells(song, BOX, "s2", "td") == (4, 0)
    song = clean(song)
    assert song["sections"][1]["cells"]["t1"] == "p1"
    assert song["sections"][2]["cells"] == {"td": "pd", "t1": "p1"}
    assert song["sections"][1]["spans"] == {"t1": [1, 1]}  # the span came along
    assert "auto" not in song["sections"][1]  # a copy carries no lanes
    assert "auto" in song["sections"][0]  # ... and takes none away
    assert so.paste_cells(song, BOX, "s3", "td") == (4, 1)
    song = clean(song)
    assert len(song["sections"]) == 4 and song["sections"][3]["bars"] == 2
    assert song["sections"][3]["cells"] == {"td": "pd", "t1": "p1"}
    assert so.paste_cells(song, BOX, "s1", "td") == (0, 0)  # onto itself


def test_an_empty_cell_of_the_range_empties_where_it_lands():
    song = range_song()
    so.paste_cells(song, [("s3", "td"), ("s3", "t1")], "s1", "td")
    assert clean(song)["sections"][0]["cells"] == {"td": None, "t1": None}


def test_moving_a_range_along_its_tracks_carries_its_lanes():
    song = range_song()
    assert so.paste_cells(song, BOX, "s2", "td", move=True) == (4, 0)
    song = clean(song)
    assert song["sections"][0]["cells"] == {"td": None, "t1": None}
    assert song["sections"][1]["auto"]["t1"][0]["target"] == "gain"
    assert song["sections"][1]["spans"] == {"t1": [1, 1]}
    assert song["sections"][2]["cells"] == {"td": "pd", "t1": "p1"}


def test_a_range_goes_to_tracks_of_its_own_kind_or_nowhere():
    song = range_song()
    before = copy.deepcopy(song)
    with pytest.raises(sm.SongError, match="Drums's cells can't land on Bass"):
        so.paste_cells(song, BOX, "s1", "t1")
    with pytest.raises(sm.SongError, match="does not fit"):
        so.paste_cells(song, [("s1", "ta"), ("s1", "t2")], "s1", "ta")
    with pytest.raises(sm.SongError, match="Pick where"):
        so.paste_cells(song, BOX, "zz", "td")
    assert song == before
    # a notes block may go one synth track down; takes go with an audio cell
    so.paste_cells(song, [("s1", "t1")], "s1", "t2")
    assert song["sections"][0]["cells"]["t2"] == "p1"
    so.paste_cells(song, [("s1", "ta")], "s2", "ta")
    assert [a["clip"] for a in song["sections"][1]["audio"]] == ["blip"]


def test_duplicating_a_range_puts_it_straight_after_itself():
    song = range_song()
    assert so.duplicate_cells(song, [("s2", "td"), ("s2", "t1")]) == (2, 0)
    assert song["sections"][2]["cells"] == {"td": "pd", "t1": "p1"}
    assert so.duplicate_cells(song, BOX[:2] + BOX[2:]) == (4, 1)  # past the end
    assert [s["cells"].get("td") for s in clean(song)["sections"]] == ["pd"] * 4


def test_a_range_is_refused_past_the_section_cap_whole():
    song = range_song()
    song["sections"] += [
        {"id": f"x{i}", "name": "X", "bars": 1} for i in range(limits.MAX_SECTIONS - 3)
    ]
    song = clean(song)
    before = copy.deepcopy(song)
    last = song["sections"][-1]["id"]
    with pytest.raises(sm.SongError, match="at most 32 sections"):
        so.paste_cells(song, BOX, last, "td")
    assert song == before


def test_make_unique_on_a_range_shares_inside_it_and_not_outside():
    song = range_song()
    with pytest.raises(sm.SongError, match="Nothing in the range is shared"):
        so.unique_cells(song, BOX + [("s3", "td"), ("s2", "t2")])
    assert so.unique_cells(song, [("s2", "td"), ("s2", "t1")]) == 2
    a, b = song["sections"][:2]
    assert a["cells"]["td"] == "pd" and b["cells"]["td"] not in ("pd", None)
    assert hits(song, b["cells"]["td"]) == hits(song, "pd")
    # two cells of the range that shared a pattern still share its copy
    song = range_song()
    so.paste_cells(song, BOX, "s2", "td")
    assert so.unique_cells(song, [("s2", "t1"), ("s3", "t1")]) == 1
    assert song["sections"][1]["cells"]["t1"] == song["sections"][2]["cells"]["t1"]
    assert song["sections"][0]["cells"]["t1"] == "p1"


def test_a_variation_on_a_range_changes_only_the_range():
    song = range_song()
    assert so.unique_cells(song, [("s2", "td"), ("s2", "t1")], seed=5) == 2
    b = song["sections"][1]
    assert hits(song, b["cells"]["td"]) != hits(song, "pd")
    assert hits(song, song["sections"][0]["cells"]["td"]) == hits(song, "pd")
    # a pattern only the range plays is varied in place: no copy is left
    song = range_song()
    n = len(song["patterns"])
    was = hits(song, "p2")
    assert so.unique_cells(song, [("s2", "t2")], seed=5) == 1
    assert len(song["patterns"]) == n and song["sections"][1]["cells"]["t2"] == "p2"
    assert hits(song, "p2") != was


def test_a_generator_fills_a_range_and_again_rolls_only_the_range():
    song = range_song()
    cells = [("s1", "t2"), ("s2", "t2")]
    assert so.fill_cells(song, cells, "bass_root", 7) == 2
    song = clean(song)
    a, b = (s["cells"]["t2"] for s in song["sections"][:2])
    assert a == b and song["patterns"][a]["gen"]["kind"] == "bass_root"
    assert song["sections"][0]["bars"] == 8  # a generated part is eight bars
    was = hits(song, a)
    assert so.again_cells(song, [("s2", "t2")], 99) == 1
    assert hits(song, song["sections"][0]["cells"]["t2"]) == was  # a copy rolled
    assert song["sections"][1]["cells"]["t2"] != a
    with pytest.raises(sm.SongError, match="nothing to roll"):
        so.again_cells(song, [("s1", "td")], 1)
    with pytest.raises(sm.SongError, match="drums take a fill"):
        so.fill_cells(song, [("s1", "td"), ("s1", "t2")], "bass_root", 7)


# === the chord track's two edits ======================================================


def test_a_chord_tiles_edge_moves_where_its_chord_starts():
    """Four chords over four bars, a bar each: the second one's start
    dragged two beats later — the first is that much longer — held a
    quarter of a beat clear of its neighbours."""
    track = chordtrack.from_degrees((1, 6, 3, 7), 4)
    got = so.chord_edge(track, 1, 6.0)
    assert [(x["at"], x["len"], x["deg"]) for x in got["slots"]] == [
        (0.0, 6.0, 1),
        (6.0, 2.0, 6),
        (8.0, 4.0, 3),
        (12.0, 4.0, 7),
    ]
    assert track["slots"][1]["at"] == 4.0  # a copy: the track handed in is whole
    assert so.chord_edge(track, 1, 99)["slots"][1]["at"] == 7.75
    assert so.chord_edge(track, 1, -5)["slots"][1]["at"] == 0.25
    assert so.chord_edge(track, 3, 15.9)["slots"][3]["at"] == 15.75  # the end
    assert so.chord_edge(track, 2, 9.1)["slots"][2]["at"] == 9.0  # the grid
    for bad in (0, 4, -1):
        with pytest.raises(sm.SongError):
            so.chord_edge(track, bad, 2.0)
    assert clean_chords(got) == got


def clean_chords(track: dict) -> dict:
    from synthlab.studio import schema

    song = clean(song_dict() | {"chords": track})
    assert schema.conform(song, schema.nodes().SONG) == song
    return song["chords"]


def test_a_chord_splits_in_two_and_the_progression_keeps_its_length():
    track = chordtrack.from_degrees((1, 6), 4)
    got = so.chord_split(track, 0)
    assert [(x["at"], x["len"], x["deg"]) for x in got["slots"]] == [
        (0.0, 4.0, 1),
        (4.0, 4.0, 1),
        (8.0, 8.0, 6),
    ]
    assert clean_chords(got) == got
    short = {"bars": 1, "slots": [{**track["slots"][0], "len": 0.25}]}
    short["slots"].append({**track["slots"][1], "at": 0.25, "len": 3.75})
    with pytest.raises(sm.SongError, match="too short"):
        so.chord_split(short, 0)
    with pytest.raises(sm.SongError, match="No such chord"):
        so.chord_split(track, 9)
    full = chordtrack.from_degrees([1] * limits.MAX_CHORD_SLOTS, 16)
    with pytest.raises(sm.SongError, match="at most 64 chords"):
        so.chord_split(full, 0)
