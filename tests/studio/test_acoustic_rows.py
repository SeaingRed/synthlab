"""The acoustic kit's data on the studio side: the new drum rows, a hit's
articulation (`a`) and a beat's kit and mics (`drum_kit`, `drum_mics`) —
all absent while unused, so no stored pattern, key or schedule moves."""

from __future__ import annotations

from dataclasses import replace

import pytest

from synthlab.studio import loops, schema, song_bridge
from synthlab.studio.arrange import drum_hits
from synthlab.studio.drumfactory import DRUM_FACTORY
from synthlab.studio.render import RenderInputs
from synthlab.studio.schema import arr_dict
from synthlab.studio.state import (
    ACOUSTIC_ROWS,
    ALL_DRUM_ROWS,
    ARTICULATIONS,
    DRUM_ROWS,
    NEW_DRUM_ROWS,
    Arrangement,
    StudioState,
)
from synthlab.synth import Patch
from tests.studio.test_drum_hits import _legacy_hits
from tests.studio.test_render import LEGACY_KEY

N = schema.nodes()


def _conform(**fields) -> dict:
    return schema.conform(fields, N.ARRANGEMENT)


def test_the_rows_and_their_articulations():
    assert DRUM_ROWS == ["kick", "snare", "hat", "ohat", "clap", "tom", "rim", "crash"]
    assert NEW_DRUM_ROWS == [
        *("ride", "tom2", "tom3", "tom4", "tom5", "crash2", "splash", "china"),
    ]
    assert ALL_DRUM_ROWS == DRUM_ROWS + NEW_DRUM_ROWS
    assert set(ACOUSTIC_ROWS) <= set(ALL_DRUM_ROWS)
    assert ARTICULATIONS["hat"][0] == "closed" and "open" in ARTICULATIONS["hat"]
    assert ARTICULATIONS["snare"] == ("centre", "side")
    assert ARTICULATIONS["ride"] == ("bow", "bell", "choke")
    assert (
        ARTICULATIONS["crash"] == ARTICULATIONS["crash2"] == ("edge", "bell", "choke")
    )
    assert ARTICULATIONS["splash"] == ARTICULATIONS["china"] == ("hit", "choke")
    # rows without articulations take none (they play as "hit")
    for row in ("kick", "ohat", "clap", "rim", "tom", "tom2", "tom5"):
        assert row not in ARTICULATIONS


def test_new_rows_take_cells_and_hits():
    out = _conform(
        drums=[["ride", 3], ["tom4", 2], ["kick", 0]],
        drum_hits={"tom4.2": {"v": 0.5}},
        drum_mix={"china": {"level": 0.5}},
        drum_samples={"splash": "my clip"},
        drum_row_swing={"crash2": 0.2},
    )
    assert ["ride", 3] in out["drums"] and ["tom4", 2] in out["drums"]
    assert out["drum_hits"] == {"tom4.2": {"v": 0.5}}
    assert out["drum_mix"] == {"china": {"level": 0.5}}
    assert out["drum_samples"] == {"splash": "my clip"}
    assert out["drum_row_swing"] == {"crash2": 0.2}
    # an unknown row is still refused
    assert _conform(drums=[["cowbell", 0]])["drums"] == []


def test_articulation_is_kept_only_on_its_row():
    drums = [["hat", 2], ["kick", 0], ["ride", 4], ["crash2", 6]]
    out = _conform(
        drums=drums,
        drum_hits={
            "hat.2": {"a": "open"},
            "kick.0": {"a": "open", "v": 0.5},
            "ride.4": {"a": "open"},  # not a ride articulation
            "crash2.6": {"a": "choke"},
        },
    )
    assert out["drum_hits"] == {
        "hat.2": {"a": "open"},
        "kick.0": {"v": 0.5},
        "crash2.6": {"a": "choke"},
    }
    # the row's default says nothing: not written down
    out = _conform(drums=drums, drum_hits={"hat.2": {"a": "closed"}})
    assert "drum_hits" not in out
    out = _conform(drums=drums, drum_hits={"hat.2": {"a": "closed", "v": 0.4}})
    assert out["drum_hits"] == {"hat.2": {"v": 0.4}}
    # a nonsense name is dropped, the rest of the hit kept
    out = _conform(drums=drums, drum_hits={"hat.2": {"a": "loud", "r": 2}})
    assert out["drum_hits"] == {"hat.2": {"r": 2}}


def test_empty_kit_fields_are_absent():
    d = arr_dict(Arrangement())
    assert "drum_kit" not in d and "drum_mics" not in d
    assert "drum_kit" not in N.ARRANGEMENT.default()
    assert "drum_mics" not in N.ARRANGEMENT.default()
    inp = RenderInputs(patch=Patch(), arr=Arrangement(), parts=("synth", "drums"))
    assert inp.key() == LEGACY_KEY
    # set, they are written down (and so in every key hashing the arrangement)
    a = Arrangement(drum_kit="naked", drum_mics={"overheads": -3.0})
    d = arr_dict(a)
    assert d["drum_kit"] == "naked" and d["drum_mics"] == {"overheads": -3.0}
    assert RenderInputs(patch=Patch(), arr=a, parts=("synth", "drums")).key() != (
        LEGACY_KEY
    )


def test_the_kit_fields_are_cleaned():
    out = _conform(drum_kit="naked", drum_mics={})
    assert out["drum_kit"] == "naked" and "drum_mics" not in out
    assert "drum_kit" not in _conform(drum_kit="808 of doom")
    mics = _conform(
        drum_mics={
            "preset": "studio",  # the default preset: says nothing
            "kick_in": 0.0,  # studio's own level: says nothing
            "overheads": -3.0,
            "far_room": None,  # off
            "mid_side": 1e9,  # clamped
            "bogus": 2.0,
        }
    )["drum_mics"]
    assert mics == {"overheads": -3.0, "far_room": None, "mid_side": 12.0}
    room = _conform(drum_mics={"preset": "room", "far_room": -3.0, "toms": -6.0})
    assert room["drum_mics"] == {"preset": "room", "toms": -6.0}
    assert "drum_mics" not in _conform(drum_mics={"preset": "nope"})


def _acoustic(s: StudioState) -> None:
    s.arrangement = replace(
        s.arrangement,
        drums=[["kick", 0], ["ride", 2], ["hat", 4]],
        drum_hits={"hat.4": {"a": "open"}},
        drum_kit="naked",
        drum_mics={"preset": "close", "overheads": -9.0},
    )


def test_the_kit_field_travels_with_the_beat(tmp_path):
    s = StudioState(tmp_path)
    _acoustic(s)
    assert s.bank_save(3, "acoustic")
    slot = s.drum_bank[3]
    assert slot["kit"] == "naked"
    assert slot["mics"] == {"preset": "close", "overheads": -9.0}
    assert s.bank_load(0)  # a factory beat: electronic
    assert s.arrangement.drum_kit == "" and s.arrangement.drum_mics == {}
    assert "kit" not in s.drum_bank[0] and "mics" not in s.drum_bank[0]
    assert s.bank_load(3)
    a = s.arrangement
    assert a.drum_kit == "naked"
    assert a.drum_mics == {"preset": "close", "overheads": -9.0}
    assert ["ride", 2] in a.drums and a.drum_hits == {"hat.4": {"a": "open"}}
    # the bank survives a restart
    s.save()
    again = StudioState(tmp_path)
    assert again.drum_bank[3]["kit"] == "naked"

    # a beat loop keeps them, and opening it puts them back
    loops.save_beat(tmp_path, "acoustic beat", slot, s.arrangement)
    loop = loops.load(tmp_path, "acoustic beat")
    assert loop["beat"]["kit"] == "naked"
    assert loop["beat"]["mics"] == {"preset": "close", "overheads": -9.0}
    s.apply_pattern(DRUM_FACTORY[0])
    assert s.arrangement.drum_kit == ""
    s.apply_pattern(loop["beat"])
    assert s.arrangement.drum_kit == "naked"
    assert s.arrangement.drum_mics == {"preset": "close", "overheads": -9.0}


def test_the_kit_travels_with_a_variation_and_a_song_pattern(tmp_path):
    s = StudioState(tmp_path)
    _acoustic(s)
    assert s.beat_variation("B")  # B starts as a copy of the grid
    s.arrangement = replace(s.arrangement, drum_kit="", drum_mics={})
    assert s.beat_variation("A")
    assert s.arrangement.drum_kit == "naked"
    # a loop's drum lane as a song pattern, and back
    d = arr_dict(s.arrangement)
    data = song_bridge.drum_data(d)
    assert data["kit"] == "naked"
    assert data["mics"] == {"preset": "close", "overheads": -9.0}
    lane = song_bridge.beat_lane(data)
    assert lane["drum_kit"] == "naked" and lane["drum_mics"] == data["mics"]
    # an electronic lane: neither key, so a pattern's content hash holds
    plain = song_bridge.drum_data(arr_dict(Arrangement(drums=[["kick", 0]])))
    assert "kit" not in plain and "mics" not in plain
    assert song_bridge.beat_lane(plain)["drum_kit"] == ""
    # the song's pool keeps them
    p = schema.conform(
        {"id": "p1", "kind": "drums", "name": "x", "bars": 1, **data},
        N.SONG_PATTERN,
    )
    assert p["kit"] == "naked" and p["mics"] == data["mics"]
    p = schema.conform(
        {"id": "p1", "kind": "drums", "name": "x", "bars": 1, **plain},
        N.SONG_PATTERN,
    )
    assert "kit" not in p and "mics" not in p


@pytest.mark.parametrize("slot", range(len(DRUM_FACTORY)))
def test_an_electronic_beat_schedules_as_before(slot):
    """The frozen scheduler is the reference: every factory beat, rough
    (humanise, a row's chance), plays exactly as it did — and a new row's
    cells beside it move none of the electronic rows' draws."""
    p = DRUM_FACTORY[slot]
    arr = Arrangement(
        cells=[],
        drums=[list(c) for c in p["steps"]],
        drum_bars=p["bars"],
        drum_steps=p["steps_per_bar"],
        drum_humanize=0.6,
        drum_mix={"hat": {"prob": 0.7}, "kick": {"level": 0.8}},
    )
    assert drum_hits(arr) == _legacy_hits(arr)
    more = replace(arr, drums=sorted([*arr.drums, ["ride", 1], ["tom3", 5]]))
    got = drum_hits(more)
    assert [(v, h) for v, h in got if v in DRUM_ROWS] == _legacy_hits(arr)
    assert {v for v, _ in got} - set(DRUM_ROWS) == {"ride", "tom3"}


def test_a_new_row_renders_its_fallback():
    """On an electronic kit a new row's cells play the nearest electronic
    voice (`arrange.FALLBACK_VOICES`: the ride the crash voice, short) on a
    track of their own, and the beat beside them renders exactly as without
    them. Their MIDI keys are the electronic kit's own."""
    import numpy as np

    from synthlab.studio import midi_bridge
    from synthlab.studio.arrange import build_song

    arr = Arrangement(bpm=120.0, cells=[], drums=[["kick", 0], ["hat", 2]])
    more = replace(arr, drums=[["kick", 0], ["hat", 2], ["ride", 4]])
    a, b = build_song(Patch(), arr, None), build_song(Patch(), more, None)
    for name, track in a.tracks.items():
        assert all(
            x[0] == y[0] and np.array_equal(x[1], y[1]) and x[2] == y[2]
            for x, y in zip(b.tracks[name].events, track.events, strict=True)
        )
    ((_at, ride, gain),) = b.tracks["drum_ride"].events
    assert gain == 1.0 and np.max(np.abs(ride)) > 0.1
    got = midi_bridge._drum_notes(more, 0.0)
    assert sorted(n.pitch for n in got) == [36, 42, 51]  # the ride at its GM key
    assert [n for n in got if n.pitch != 51] == midi_bridge._drum_notes(arr, 0.0)
