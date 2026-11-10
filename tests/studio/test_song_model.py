"""The song model (studio/song_model.py): pure functions over the SONG
schema's plain dicts — tracks (rows), a pattern pool, sections (columns).

Every operation must leave the song a schema fixed point, so the model can
never hand state something the loader would silently rewrite."""

import pytest

from synthlab.studio import limits, schema
from synthlab.studio import song_model as sm
from synthlab.synth import Patch

N = schema.nodes()


def _fixed(song):
    assert schema.conform_strict(song, N.SONG) == song
    return song


def _song():
    return sm.new_song(120.0, "A1", "minor")


def _drums(steps=((("kick", 0)),), bars=1):
    return {"drums": [list(c) for c in steps], "bars": bars, "steps": 16}


def _notes(cells=(("A1", 0),), bars=1):
    return {"cells": [list(c) for c in cells], "bars": bars, "steps_per_bar": 4}


def _rich():
    """A song with one track of each kind, a pattern of each kind, one
    section using both, and a take on the audio track."""
    song = _song()
    d = sm.add_track(song, "drums", "Drums")
    s = sm.add_track(song, "synth", "Bass")
    a = sm.add_track(song, "audio", "Vox")
    dp = sm.add_pattern(song, "drums", "Beat", _drums())
    np_ = sm.add_pattern(song, "notes", "Riff", _notes())
    sec = sm.add_section(song, "Verse", 4)
    sm.set_cell(song, sec, d, dp)
    sm.set_cell(song, sec, s, np_)
    song["sections"][0]["audio"].append(
        {"track_id": a, "clip": "take", "at_bar": 0, "gain": 1.0}
    )
    return song, {"d": d, "s": s, "a": a, "dp": dp, "np": np_, "sec": sec}


# --- construction ---------------------------------------------------------------


def test_new_song_is_empty_and_conformant():
    song = _fixed(_song())
    assert (song["bpm"], song["root"], song["scale_name"]) == (120.0, "A1", "minor")
    assert song["tracks"] == [] and song["patterns"] == {} and song["sections"] == []


def test_new_song_clamps_hostile_arguments():
    song = _fixed(sm.new_song(10_000.0, "Z9", "nope"))
    assert song["bpm"] == limits.BPM_RANGE[1]


def test_rich_song_is_a_fixed_point():
    _fixed(_rich()[0])


# --- tracks ---------------------------------------------------------------------


def test_add_track_returns_a_short_hex_id_and_keeps_kind_fields():
    song = _song()
    tid = sm.add_track(song, "synth", "Bass", patch=Patch(cutoff=900.0).to_json())
    assert len(tid) == 8 and int(tid, 16) >= 0
    (t,) = song["tracks"]
    assert (t["id"], t["name"], t["kind"]) == (tid, "Bass", "synth")
    assert t["patch"]["cutoff"] == 900.0
    assert "drum_params" not in t
    dt = song["tracks"][sm.track_index(song, sm.add_track(song, "drums", "Kit"))]
    assert "patch" not in dt and "drum_params" in dt
    _fixed(song)


def test_add_track_validates_init_through_the_schema():
    song = _song()
    tid = sm.add_track(song, "synth", "S", patch={"cutoff": 1e12}, gain=99)
    t = song["tracks"][sm.track_index(song, tid)]
    assert t["gain"] == 1.5
    assert t["patch"]["cutoff"] < 1e12
    _fixed(song)


@pytest.mark.parametrize(
    ("kind", "cap"),
    [
        ("drums", limits.MAX_DRUM_TRACKS),
        ("synth", limits.MAX_SYNTH_TRACKS),
        ("audio", limits.MAX_AUDIO_TRACKS),
    ],
)
def test_add_track_enforces_per_kind_caps(kind, cap):
    song = _song()
    for i in range(cap):
        sm.add_track(song, kind, f"t{i}")
    with pytest.raises(sm.SongError):
        sm.add_track(song, kind, "one too many")
    assert len(song["tracks"]) == cap


def test_add_track_enforces_the_total_cap():
    assert (
        limits.MAX_DRUM_TRACKS + limits.MAX_SYNTH_TRACKS + limits.MAX_AUDIO_TRACKS
        == limits.MAX_TRACKS
    )
    song = _song()
    for kind, cap in (
        ("drums", limits.MAX_DRUM_TRACKS),
        ("synth", limits.MAX_SYNTH_TRACKS),
        ("audio", limits.MAX_AUDIO_TRACKS),
    ):
        for _ in range(cap):
            sm.add_track(song, kind, "t")
    assert len(song["tracks"]) == limits.MAX_TRACKS
    _fixed(song)


def test_add_track_rejects_an_unknown_kind():
    with pytest.raises(sm.SongError):
        sm.add_track(_song(), "theremin", "x")


def test_remove_track_is_refused_while_a_cell_uses_it():
    song, ids = _rich()
    assert sm.remove_track(song, ids["s"]) == ["Verse"]
    assert sm.track_index(song, ids["s"]) is not None


def test_remove_track_is_refused_while_a_take_uses_it():
    song, ids = _rich()
    assert sm.remove_track(song, ids["a"]) == ["Verse"]


def test_remove_track_succeeds_and_clears_empty_cells():
    song, ids = _rich()
    sm.set_cell(song, ids["sec"], ids["s"], None)
    assert sm.remove_track(song, ids["s"]) == []
    assert sm.track_index(song, ids["s"]) is None
    assert ids["s"] not in song["sections"][0]["cells"]
    _fixed(song)


def test_remove_unknown_track_raises():
    with pytest.raises(sm.SongError):
        sm.remove_track(_song(), "deadbeef")


# --- patterns -------------------------------------------------------------------


def test_add_pattern_keeps_arrangement_field_names():
    song = _song()
    dp = sm.add_pattern(
        song,
        "drums",
        "Beat",
        {"drums": [["kick", 0], ["hat", 2]], "drum_vel": {"2": 0.5}, "bars": 2},
    )
    np_ = sm.add_pattern(
        song,
        "notes",
        "Riff",
        {
            "cells": [["A1", 0], ["C2", 3]],
            "velocities": {"3": 0.4},
            "slides": [3],
            "bars": 1,
            "steps_per_bar": 8,
            "arp_mode": "up",
            "arp_octaves": 2,
        },
    )
    d, n = song["patterns"][dp], song["patterns"][np_]
    assert d["kind"] == "drums" and d["id"] == dp and d["name"] == "Beat"
    assert d["drums"] == [["kick", 0], ["hat", 2]] and d["drum_vel"] == {"2": 0.5}
    assert (d["bars"], d["steps"]) == (2, 16)
    assert n["kind"] == "notes" and n["cells"] == [["A1", 0], ["C2", 3]]
    assert (n["velocities"], n["slides"]) == ({"3": 0.4}, [3])
    assert (n["steps_per_bar"], n["arp_mode"], n["arp_octaves"]) == (8, "up", 2)
    assert d["color"] and n["color"]
    _fixed(song)


def test_add_pattern_drops_hostile_data():
    song = _song()
    pid = sm.add_pattern(
        song, "notes", "x", {"cells": [["C99999", 0], ["A1", 999]], "bars": "lots"}
    )
    p = song["patterns"][pid]
    assert p["cells"] == [] and p["bars"] == 1
    _fixed(song)


def test_add_pattern_rejects_an_unknown_kind():
    with pytest.raises(sm.SongError):
        sm.add_pattern(_song(), "synth", "x", {})


def test_add_pattern_enforces_the_pool_cap():
    song = _song()
    for _ in range(limits.MAX_PATTERNS):
        sm.add_pattern(song, "drums", "p", {})
    with pytest.raises(sm.SongError):
        sm.add_pattern(song, "drums", "p", {})
    assert len(song["patterns"]) == limits.MAX_PATTERNS


def test_pattern_usage_and_refused_removal():
    song, ids = _rich()
    sec2 = sm.duplicate_section(song, ids["sec"])
    assert sm.pattern_usage(song, ids["dp"]) == [ids["sec"], sec2]
    names = [s["name"] for s in song["sections"]]
    assert sm.remove_pattern(song, ids["dp"]) == names
    assert ids["dp"] in song["patterns"]


def test_remove_unused_pattern():
    song, ids = _rich()
    spare = sm.add_pattern(song, "drums", "Spare", {})
    assert sm.pattern_usage(song, spare) == []
    assert sm.remove_pattern(song, spare) == []
    assert spare not in song["patterns"]
    _fixed(song)


def test_remove_unknown_pattern_raises():
    with pytest.raises(sm.SongError):
        sm.remove_pattern(_song(), "deadbeef")


# --- sections -------------------------------------------------------------------


def test_add_section_appends_or_inserts():
    song = _song()
    a = sm.add_section(song, "A", 4)
    b = sm.add_section(song, "B", 8)
    c = sm.add_section(song, "C", 2, at=0)
    assert [s["id"] for s in song["sections"]] == [c, a, b]
    sec = song["sections"][1]
    assert (sec["bars"], sec["repeats"], sec["cells"], sec["audio"]) == (4, 1, {}, [])
    _fixed(song)


def test_add_section_clamps_bars():
    song = _song()
    sm.add_section(song, "big", 10_000)
    sm.add_section(song, "tiny", 0)
    assert [s["bars"] for s in song["sections"]] == [limits.MAX_SECTION_BARS, 1]


def test_add_section_enforces_the_cap():
    song = _song()
    for _ in range(limits.MAX_SECTIONS):
        sm.add_section(song, "s", 1)
    with pytest.raises(sm.SongError):
        sm.add_section(song, "s", 1)


def test_duplicate_section_shares_patterns_and_sits_after_its_source():
    song, ids = _rich()
    tail = sm.add_section(song, "Outro", 2)
    dup = sm.duplicate_section(song, ids["sec"])
    assert [s["id"] for s in song["sections"]] == [ids["sec"], dup, tail]
    src, copy_ = song["sections"][0], song["sections"][1]
    assert copy_["cells"] == src["cells"] and copy_["audio"] == src["audio"]
    assert copy_["cells"] is not src["cells"]
    assert copy_["name"] != src["name"]
    _fixed(song)


def test_duplicate_section_enforces_the_cap():
    song = _song()
    sid = sm.add_section(song, "s", 1)
    for _ in range(limits.MAX_SECTIONS - 1):
        sm.duplicate_section(song, sid)
    with pytest.raises(sm.SongError):
        sm.duplicate_section(song, sid)


def test_move_section():
    song = _song()
    a, b, c = (sm.add_section(song, n, 1) for n in "ABC")
    sm.move_section(song, a, 2)
    assert [s["id"] for s in song["sections"]] == [b, c, a]
    sm.move_section(song, a, -50)  # clamped
    assert [s["id"] for s in song["sections"]] == [a, b, c]
    sm.move_section(song, c, 99)
    assert [s["id"] for s in song["sections"]] == [a, b, c]


def test_unknown_section_raises():
    song, ids = _rich()
    with pytest.raises(sm.SongError):
        sm.move_section(song, "nope", 0)
    with pytest.raises(sm.SongError):
        sm.set_cell(song, "nope", ids["d"], ids["dp"])


# --- cells ----------------------------------------------------------------------


def test_set_cell_is_kind_checked():
    song, ids = _rich()
    with pytest.raises(sm.SongError):
        sm.set_cell(song, ids["sec"], ids["s"], ids["dp"])  # drums on synth
    with pytest.raises(sm.SongError):
        sm.set_cell(song, ids["sec"], ids["d"], ids["np"])  # notes on drums
    with pytest.raises(sm.SongError):
        sm.set_cell(song, ids["sec"], ids["a"], ids["np"])  # audio has no cells
    with pytest.raises(sm.SongError):
        sm.set_cell(song, ids["sec"], ids["d"], "missing")
    with pytest.raises(sm.SongError):
        sm.set_cell(song, ids["sec"], "missing", ids["dp"])


def test_set_cell_none_clears_and_grows_bars_to_fit():
    song, ids = _rich()
    sm.set_cell(song, ids["sec"], ids["d"], None)
    assert song["sections"][0]["cells"][ids["d"]] is None
    short = sm.add_section(song, "Short", 1)
    long_ = sm.add_pattern(song, "notes", "Long", _notes(bars=3))
    sm.set_cell(song, short, ids["s"], long_)
    assert song["sections"][-1]["bars"] == 3
    _fixed(song)


def test_make_unique_copies_and_repoints_one_cell():
    song, ids = _rich()
    other = sm.duplicate_section(song, ids["sec"])
    new = sm.make_unique(song, ids["sec"], ids["s"])
    assert new != ids["np"] and new in song["patterns"]
    assert song["patterns"][new]["name"] == "Riff ′"
    assert song["sections"][0]["cells"][ids["s"]] == new
    assert song["sections"][1]["cells"][ids["s"]] == ids["np"]  # still shared
    assert sm.pattern_usage(song, ids["np"]) == [other]
    assert sm.content_hash(song["patterns"][new]) == sm.content_hash(
        song["patterns"][ids["np"]]
    )
    _fixed(song)


def test_make_unique_of_an_empty_cell_raises():
    song, ids = _rich()
    sm.set_cell(song, ids["sec"], ids["s"], None)
    with pytest.raises(sm.SongError):
        sm.make_unique(song, ids["sec"], ids["s"])


def test_make_unique_respects_the_pool_cap():
    song, ids = _rich()
    while len(song["patterns"]) < limits.MAX_PATTERNS:
        sm.add_pattern(song, "drums", "p", {})
    with pytest.raises(sm.SongError):
        sm.make_unique(song, ids["sec"], ids["s"])


@pytest.mark.parametrize("which", ["d", "s"])
def test_variation_differs_from_its_source_and_is_deterministic(which):
    results = []
    for _ in range(2):
        song, ids = _rich()
        src_pid = song["sections"][0]["cells"][ids[which]]
        src = sm.content_hash(song["patterns"][src_pid])
        new = sm.variation(song, ids["sec"], ids[which], seed=7)
        assert new != src_pid
        assert song["sections"][0]["cells"][ids[which]] == new
        assert sm.content_hash(song["patterns"][new]) != src
        _fixed(song)
        p = dict(song["patterns"][new])
        results.append({k: v for k, v in p.items() if k not in ("id", "color")})
    assert results[0] == results[1]


def test_variation_differs_for_many_seeds_and_note_shapes():
    for cells in ([], [("A1", 0)], [("A1", s) for s in range(4)], [("E3", 1)]):
        for seed in range(25):
            song = _song()
            t = sm.add_track(song, "synth", "S")
            p = sm.add_pattern(song, "notes", "P", _notes(cells=cells))
            sec = sm.add_section(song, "S", 1)
            sm.set_cell(song, sec, t, p)
            new = sm.variation(song, sec, t, seed)
            assert sm.content_hash(song["patterns"][new]) != sm.content_hash(
                song["patterns"][p]
            ), (cells, seed)
            _fixed(song)


def test_variation_seeds_differ():
    outs = set()
    for seed in range(6):
        song, ids = _rich()
        new = sm.variation(song, ids["sec"], ids["d"], seed)
        outs.add(sm.content_hash(song["patterns"][new]))
    assert len(outs) > 1


# --- timing ---------------------------------------------------------------------


def test_pattern_bars():
    assert sm.pattern_bars({"bars": 3}) == 3
    assert sm.pattern_bars({}) == 1


@pytest.mark.parametrize(
    ("a", "b", "bars", "want"),
    [(1, 4, 24, 4), (3, 4, 24, 12), (2, 4, 24, 4), (3, 4, 16, 16), (3, 1, 4, 4)],
)
def test_cycle_bars_is_the_lcm_of_used_patterns(a, b, bars, want):
    """The lcm when it divides the section's bars; otherwise the whole
    section is one cycle (every pass is a whole cycle, never a short one)."""
    song = _song()
    d = sm.add_track(song, "drums", "D")
    s = sm.add_track(song, "synth", "S")
    sec = sm.add_section(song, "x", bars)
    sm.set_cell(song, sec, d, sm.add_pattern(song, "drums", "d", _drums(bars=a)))
    sm.set_cell(song, sec, s, sm.add_pattern(song, "notes", "n", _notes(bars=b)))
    assert sm.cycle_bars(song, song["sections"][0]) == want


def test_cycle_bars_empty_and_capped():
    song = _song()
    sm.add_section(song, "empty", 8)
    assert sm.cycle_bars(song, song["sections"][0]) == 1
    d = sm.add_track(song, "drums", "D")
    s = sm.add_track(song, "synth", "S")
    sec = sm.add_section(song, "x", 4)
    sm.set_cell(song, sec, d, sm.add_pattern(song, "drums", "d", _drums(bars=3)))
    sm.set_cell(song, sec, s, sm.add_pattern(song, "notes", "n", _notes(bars=4)))
    assert sm.cycle_bars(song, song["sections"][1]) == 4  # the section, not 12


def test_section_and_song_seconds():
    song = _song()  # 120 bpm: a bar is 2 s
    sm.add_section(song, "a", 4)
    sm.add_section(song, "b", 2)
    song["sections"][1]["repeats"] = 3
    assert sm.section_seconds(song, song["sections"][0]) == pytest.approx(8.0)
    assert sm.section_seconds(song, song["sections"][1]) == pytest.approx(12.0)
    assert sm.song_seconds(song) == pytest.approx(20.0)

    assert sm.song_seconds(_song()) == 0.0


# --- content hash ---------------------------------------------------------------


def test_content_hash_ignores_identity_and_order():
    a = {"id": "1", "name": "A", "color": "#111111", "kind": "drums"}
    a |= {"bars": 1, "steps": 16, "drums": [["kick", 0], ["hat", 2]], "drum_vel": {}}
    b = dict(a, id="2", name="B", color="#222222", drums=[["hat", 2], ["kick", 0]])
    assert sm.content_hash(a) == sm.content_hash(b)
    assert sm.content_hash(a) != sm.content_hash(dict(a, drums=[["kick", 0]]))
    assert sm.content_hash(a) != sm.content_hash(dict(a, kind="notes"))


_VARY_SCRIPT = """
import json
from synthlab.studio import song_model as sm
out = []
for seed in range(12):
    song = sm.new_song(120.0, "A1", "minor")
    t = sm.add_track(song, "synth", "S")
    cells = [["C#2", 0], ["Db2", 0], ["A#1", 1], ["Bb1", 1], ["G#1", 2], ["Ab1", 2],
             ["D#2", 3], ["Eb2", 3], ["A1", 0], ["E2", 1]]
    p = sm.add_pattern(song, "notes", "P",
                       {"cells": cells, "bars": 1, "steps_per_bar": 4})
    sec = sm.add_section(song, "S", 1)
    sm.set_cell(song, sec, t, p)
    new = sm.variation(song, sec, t, seed)
    out.append(song["patterns"][new]["cells"])
print(json.dumps(out))
"""


def test_variation_is_deterministic_across_hash_seeds():
    """Enharmonic spellings (C#2 / Db2) tie on MIDI number; the result must
    not fall back to set iteration order, which PYTHONHASHSEED changes."""
    import os
    import subprocess
    import sys

    outs = set()
    for hs in ("1", "2", "3", "4"):
        env = dict(os.environ, PYTHONHASHSEED=hs)
        r = subprocess.run(
            [sys.executable, "-c", _VARY_SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        outs.add(r.stdout)
    assert len(outs) == 1


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), "x", None, [1]])
def test_hostile_indices_raise_song_error(bad):
    song = _song()
    sid = sm.add_section(song, "a", 1)
    if bad is not None:  # at=None means "append"
        with pytest.raises(sm.SongError):
            sm.add_section(song, "b", 1, at=bad)
    with pytest.raises(sm.SongError):
        sm.move_section(song, sid, bad)
    assert [s["id"] for s in song["sections"]] == [sid]


def test_remove_section_keeps_its_patterns_in_the_pool():
    song, ids = _rich()
    pool = dict(song["patterns"])
    sm.remove_section(song, ids["sec"])
    assert song["sections"] == [] and song["patterns"] == pool
    _fixed(song)
    with pytest.raises(sm.SongError):
        sm.remove_section(song, ids["sec"])
