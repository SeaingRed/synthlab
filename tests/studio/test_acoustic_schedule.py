"""The one drum schedule plays the acoustic kit (`arrange.drum_hits`,
`_schedule_drums`): an acoustic hit carries its articulation, velocity and
round robin beside `(beat, gain)`; the render asks the kit for it, cuts a
ringing hat or cymbal, and falls back to the electronic voices when the kit
is not here. An electronic beat is scheduled and rendered as it always was.

The kit is a fake here (`render_hit` returns a constant buffer a second long,
its level naming the row, articulation and round robin); the real kit's check
is `test_the_real_kit_plays_a_beat` (slow, skipped without the kit)."""

from __future__ import annotations

import os
import zlib
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from synthlab.core import SR, n_samples
from synthlab.kits import fetch, play, sfz
from synthlab.studio import arrange, render
from synthlab.studio import song_model as sm
from synthlab.studio.arrange import build_song, drum_hits
from synthlab.studio.render import RenderInputs, section_windows
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch
from tests.studio.test_drum_hits import FACTORY, _legacy_hits, _legacy_schedule

FAKE_LEN = SR  # a fake hit rings a whole second


def _level(row: str, art: str, rr: int) -> float:
    return 0.05 + (zlib.crc32(f"{row}/{art}/{rr}".encode()) % 900) / 1000.0


@pytest.fixture
def kit(monkeypatch):
    """A ready fake kit; yields the `render_hit` calls."""
    calls: list[tuple] = []

    def render_hit(kmap, root, row, art, velocity, rr, mics, sr=SR):
        calls.append((row, art, velocity, rr, mics))
        return np.full((FAKE_LEN, 2), _level(row, art, rr))

    monkeypatch.setattr(play, "render_hit", render_hit)
    monkeypatch.setattr(
        fetch, "availability", lambda *a, **k: {"ready": True, "megabytes": 0}
    )
    monkeypatch.setattr(sfz, "read_kit", lambda root: sfz.KitMap({}, ()))
    arrange.forget_kit()
    yield calls
    arrange.forget_kit()


@pytest.fixture
def no_kit(monkeypatch):
    monkeypatch.setattr(
        fetch,
        "availability",
        lambda *a, **k: {"ready": False, "megabytes": 1300, "reason": "no kit"},
    )

    def render_hit(*a, **k):
        raise AssertionError("the kit is not here")

    monkeypatch.setattr(play, "render_hit", render_hit)
    arrange.forget_kit()
    yield
    arrange.forget_kit()


def _arr(drums, hits=None, **kw) -> Arrangement:
    kw.setdefault("bpm", 120.0)
    kw.setdefault("drum_kit", "naked")
    return Arrangement(
        cells=[], drums=[list(d) for d in drums], drum_hits=dict(hits or {}), **kw
    )


def _events(arr, voice):
    song = build_song(Patch(), arr, None)
    return sorted(song.tracks[f"drum_{voice}"].events, key=lambda e: e[0])


def _marks(arr) -> dict:
    return {v: [(h.art, h.rr) for h in hs] for v, hs in drum_hits(arr)}


# --- an electronic beat is what it always was ------------------------------------


@pytest.mark.parametrize("name,arr", FACTORY, ids=[n for n, _a in FACTORY])
def test_an_electronic_beat_renders_as_before(name, arr):
    rough = replace(arr, drum_humanize=0.5, drum_mix={"hat": {"prob": 0.6}})
    for a in (arr, rough):
        got = drum_hits(a)
        assert got == _legacy_hits(a)
        assert all(
            h.art == "" and h.vel is None and h.rr == 0 for _v, hs in got for h in hs
        )
    if name.split("/")[-1] == "1x16" or "/" not in name[5:]:
        np.testing.assert_array_equal(
            build_song(Patch(), arr, None).render(normalize=0), _legacy_schedule(arr)
        )


def test_an_electronic_beat_never_asks_for_the_kit(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("asked")

    monkeypatch.setattr(fetch, "availability", boom)
    monkeypatch.setattr(play, "render_hit", boom)
    arrange.forget_kit()
    arr = _arr([["kick", 0], ["ride", 2], ["tom3", 4]], drum_kit="")
    build_song(Patch(), arr, None).render(normalize=0)
    RenderInputs(patch=Patch(), arr=arr, parts=("drums",)).key()


# --- the schedule's marks: articulation, velocity, round robin -----------------------


def test_sixteen_closed_hats_never_repeat_a_take_back_to_back(kit):
    arr = _arr([["hat", s] for s in range(16)])
    ((voice, hits),) = drum_hits(arr)
    assert voice == "hat"
    assert [h.rr for h in hits] == list(range(16))
    assert all(h.art == "closed" for h in hits)
    assert all(a.rr != b.rr for a, b in zip(hits, hits[1:], strict=False))
    # and the render asks the kit for each take, in turn
    build_song(Patch(), arr, None)
    assert [c[3] for c in kit] == list(range(16))


def test_a_ratchet_is_that_many_takes_and_an_articulation_counts_on_its_own(kit):
    arr = _arr(
        [["hat", 0], ["hat", 2], ["hat", 4], ["hat", 6]],
        {"hat.2": {"r": 3}, "hat.4": {"a": "open"}},
    )
    ((_v, hits),) = drum_hits(arr)
    assert [(h.art, h.rr) for h in hits] == [
        ("closed", 0),
        ("closed", 1),
        ("closed", 2),
        ("closed", 3),
        ("open", 0),
        ("closed", 4),
    ]
    assert [h.vel for h in hits[1:4]] == pytest.approx([1.0, 0.85, 0.85**2])


def test_ohat_cells_play_as_open_hats_on_the_acoustic_kit(kit):
    arr = _arr(
        [["hat", 0], ["ohat", 2], ["hat", 4], ["ohat", 6]],
        drum_mix={"ohat": {"level": 0.5}},
    )
    got = drum_hits(arr)
    assert [v for v, _h in got] == ["hat"]
    ((_v, hits),) = got
    assert [(h.art, h.rr) for h in hits] == [
        ("closed", 0),
        ("open", 0),
        ("closed", 1),
        ("open", 1),
    ]
    assert [h[1] for h in hits] == [1.0, 0.5, 1.0, 0.5]  # the ohat row's level
    assert [h.vel for h in hits] == [1.0] * 4  # which never picks the layer
    # electronic: the two rows as they always were
    assert [v for v, _h in drum_hits(replace(arr, drum_kit=""))] == ["hat", "ohat"]


def test_an_ohat_rows_chance_goes_onto_its_hits(kit):
    arr = _arr([["ohat", s] for s in range(16)], drum_mix={"ohat": {"prob": 0.0}})
    assert drum_hits(arr) == [("hat", [])]
    muted = _arr([["ohat", 0]], drum_mix={"ohat": {"mute": True}})
    assert drum_hits(muted) == []


def test_row_level_scales_but_does_not_pick_the_layer(kit):
    drums = [["snare", 4], ["snare", 12]]
    vel = {"4": 0.7}
    loud = _arr(drums, drum_vel=vel)
    soft = _arr(drums, drum_vel=vel, drum_mix={"snare": {"level": 0.3}})
    a = _events(loud, "snare")
    calls_loud = list(kit)
    kit.clear()
    b = _events(soft, "snare")
    assert [c[2] for c in kit] == [c[2] for c in calls_loud]
    assert [c[2] for c in kit] == pytest.approx([round(0.7 * 127) / 127, 1.0])
    g = arrange.KIT_GAIN  # the kit's one fixed make-up
    assert [e[2] for e in a] == pytest.approx([g, g])
    assert [e[2] for e in b] == pytest.approx([0.3 * g, 0.3 * g])


def test_the_mic_mix_and_its_preset_reach_the_kit(kit):
    arr = _arr([["kick", 0]], drum_mics={"preset": "room", "kick_in": -2.0})
    build_song(Patch(), arr, None)
    ((_row, _art, _v, _rr, mics),) = kit
    assert mics == play.levels({"preset": "room", "kick_in": -2.0})
    assert mics["kick_in"] == -2.0 and mics["far_room"] == -3.0


def test_levels_resolves_a_preset_then_its_overrides():
    assert play.levels(None) == play.MIC_PRESETS["studio"]
    assert play.levels({"preset": "close"}) == play.MIC_PRESETS["close"]
    got = play.levels({"preset": "room", "mid_side": None, "toms": -9})
    assert got == {**play.MIC_PRESETS["room"], "mid_side": None, "toms": -9.0}
    assert "preset" not in got
    assert play.levels({"preset": "nope"}) == play.MIC_PRESETS["studio"]


def test_one_hit_is_rendered_once_a_render(kit):
    """A tiled cycle repeats its pattern's takes: each (row, art, velocity,
    take, mics) is asked of the kit once however often it plays."""
    loop = _arr(BEAT["drums"], BEAT["hits"])
    build_song(Patch(), loop, None)
    once = sorted(kit)
    kit.clear()
    flat = _section(lane=True)
    song = build_song(Patch(), flat.arr, None)
    assert sorted(kit) == once
    placed = sum(len(t.events) for n, t in song.tracks.items() if n.startswith("drum"))
    assert placed > 3 * len(once)


# --- cuts -----------------------------------------------------------------------


def _silent_after(audio: np.ndarray, n: int) -> bool:
    return n >= len(audio) or not np.any(audio[n:])


def test_an_open_hat_is_cut_by_the_next_closed_one(kit):
    for closer in ("closed", "pedal", "tight"):
        hits = {"hat.0": {"a": "open"}}
        if closer != "closed":
            hits["hat.4"] = {"a": closer}
        arr = _arr([["hat", 0], ["hat", 4]], hits)
        (opened, closed) = _events(arr, "hat")
        gap = closed[0] - opened[0]
        assert gap == n_samples(4 * 0.125, SR)
        assert _silent_after(opened[1], gap + n_samples(0.008, SR))
        assert np.any(opened[1][gap - n_samples(0.008, SR) - 1])
        assert len(closed[1]) == FAKE_LEN  # the closer rings on


def test_a_half_open_hat_is_cut_too_and_an_open_one_is_not_cut_by_another(kit):
    arr = _arr(
        [["hat", 0], ["hat", 4], ["hat", 8]],
        {"hat.0": {"a": "half"}, "hat.4": {"a": "open"}},
    )
    half, opened, closed = _events(arr, "hat")
    assert len(half[1]) == closed[0] - half[0]  # the next closer, not the open hat
    assert len(opened[1]) == closed[0] - opened[0]


def test_an_open_hat_wraps_to_the_loops_first_closer(kit):
    arr = _arr([["hat", 4], ["hat", 12]], {"hat.12": {"a": "open"}})
    _closed, opened = _events(arr, "hat")
    assert len(opened[1]) == n_samples(8 * 0.125, SR)


def test_a_choke_cuts_the_crash_and_makes_no_sound(kit):
    arr = _arr([["crash", 0], ["crash", 6]], {"crash.6": {"a": "choke"}})
    marks = _marks(arr)
    assert marks["crash"] == [("edge", 0), ("choke", 0)]
    (ev,) = _events(arr, "crash")
    assert len(ev[1]) == n_samples(6 * 0.125, SR)
    assert all(c[1] != "choke" for c in kit)
    # a choke cuts its own row alone
    both = _arr(
        [["crash", 0], ["crash2", 0], ["crash", 6]], {"crash.6": {"a": "choke"}}
    )
    (other,) = _events(both, "crash2")
    assert len(other[1]) == FAKE_LEN


def test_a_row_with_a_clip_plays_its_clip(kit, tmp_path):
    from synthlab.studio.clips import save_clip

    save_clip(tmp_path, "thud", np.full(2000, 0.25), SR)
    arr = _arr([["snare", 0]], drum_samples={"snare": "thud"})
    song = build_song(Patch(), arr, None, clip_root=tmp_path)
    ((_s, audio, _g),) = song.tracks["drum_snare"].events
    assert kit == [] and len(audio) == 2000


def test_a_tune_resamples_an_acoustic_hit(kit):
    arr = _arr([["tom", 0]], {"tom.0": {"t": 12}})
    ((_s, audio, _g),) = _events(arr, "tom")
    assert audio.shape == (FAKE_LEN // 2, 2)


# --- one schedule: the loop, a section, its chunks ------------------------------------

BEAT = {
    "drums": [
        *(["hat", s] for s in (0, 2, 4, 6, 8, 10, 12, 14)),
        ["ohat", 7],
        ["snare", 4],
        ["snare", 12],
        ["kick", 0],
        ["kick", 8],
        ["crash", 0],
        ["crash", 10],
        ["ride", 3],
        ["ride", 11],
    ],
    "hits": {
        "hat.2": {"p": 0.5},
        "hat.6": {"a": "open", "r": 2},
        "hat.10": {"a": "pedal"},
        "hat.12": {"p": 0.5},
        "hat.14": {"n": 0.25},
        "hat.0": {"n": -0.25},
        "crash.10": {"a": "choke"},
        "ride.11": {"a": "bell"},
        "ride.3": {"p": 0.6},
        "snare.12": {"a": "side"},
    },
}


def _section(bars=4, lane=False):
    song = sm.new_song(120.0, "A1", "minor")
    tid = sm.add_track(song, "drums", "Drums")
    sid = sm.add_section(song, "S", bars)
    data = {
        "drums": BEAT["drums"],
        "bars": 1,
        "steps": 16,
        "hits": BEAT["hits"],
        "kit": "naked",
    }
    sm.set_cell(song, sid, tid, sm.add_pattern(song, "drums", "beat", data))
    if lane:
        sm.set_lane(song, sid, tid, "gain", [[0.0, 0.2], [1.0, 0.2]])
    return sm.section_arrangement(song, song["sections"][0])


def _placed(arr, offset=0.0, reps=1) -> list:
    length = arr.drum_bars * 4.0
    return sorted(
        (v, h.art, h.rr, round(h[0] + offset + r * length, 9))
        for r in range(reps)
        for v, hs in drum_hits(arr)
        for h in hs
    )


def _windows(parts) -> list:
    out = []
    for b0, b1 in section_windows(parts):
        out += _placed(parts.window(b0, b1).arr, b0 * 4.0)
    return sorted(out)


def test_loop_section_and_chunks_agree(kit, monkeypatch):
    loop = _arr(BEAT["drums"], BEAT["hits"])
    want = _placed(loop, reps=4)
    assert {(v, a) for v, a, _r, _b in want} >= {
        ("hat", "open"),
        ("hat", "pedal"),
        ("ride", "bell"),
        ("crash", "choke"),
        ("snare", "side"),
    }
    assert max(r for _v, _a, r, _b in want) >= 5
    native = _section()
    assert not native.flat and native.arr.drum_kit == "naked"
    assert _placed(native.arr, reps=4) == want
    flat = _section(lane=True)
    assert flat.flat and flat.arr.drum_bars == 4
    assert _placed(flat.arr) == want
    for cap in (2.0, 6.0):
        monkeypatch.setattr(render, "MAX_LOOP_SECONDS", cap)
        assert len(section_windows(flat)) > 1
        assert _windows(flat) == want


def test_an_electronic_section_carries_no_round_robin_marks():
    song = sm.new_song(120.0, "A1", "minor")
    tid = sm.add_track(song, "drums", "Drums")
    sid = sm.add_section(song, "S", 4)
    data = {"drums": [["hat", 0], ["hat", 4]], "bars": 1, "steps": 16, "hits": {}}
    sm.set_cell(song, sid, tid, sm.add_pattern(song, "drums", "beat", data))
    sm.set_lane(song, sid, tid, "gain", [[0.0, 0.2], [1.0, 0.2]])
    flat = sm.section_arrangement(song, song["sections"][0])
    assert flat.flat and flat.arr.drum_hits == {}


# --- the kit not here: fallbacks --------------------------------------------------


def test_new_rows_fall_back_when_the_kit_is_missing(no_kit):
    rows = ["ride", "crash2", "splash", "china", "tom2", "tom3", "tom4", "tom5"]
    arr = _arr([[r, 0] for r in rows] + [["hat", 4], ["ohat", 6], ["hat", 8]])
    assert _marks(arr)["hat"] == [("closed", 0), ("open", 0), ("closed", 1)]
    song = build_song(Patch(), arr, None)
    for r in rows:
        ((_s, audio, gain),) = song.tracks[f"drum_{r}"].events
        assert gain == 1.0 and np.max(np.abs(audio)) > 0.1, r
    lens = {r: len(song.tracks[f"drum_{r}"].events[0][1]) for r in rows[:4]}
    assert lens["splash"] < lens["ride"] < lens["china"] < lens["crash2"]
    # the toms three semitones apart, each lower than the last
    toms = [song.tracks[f"drum_{t}"].events[0][1] for t in ("tom2", "tom3", "tom5")]
    assert len({a.tobytes() for a in toms}) == 3
    # the open hat plays the open hat voice, choked by the next closed hat
    hat = sorted(song.tracks["drum_hat"].events, key=lambda e: e[0])
    assert len(hat) == 3
    assert len(hat[1][1]) == hat[2][0] - hat[1][0]


def test_the_electronic_kit_plays_new_rows_on_the_fallback_voices():
    arr = _arr([["ride", 0], ["tom3", 4]], drum_kit="")
    song = build_song(Patch(), arr, None)
    for r in ("ride", "tom3"):
        ((_s, audio, _g),) = song.tracks[f"drum_{r}"].events
        assert np.max(np.abs(audio)) > 0.1


# --- keys ---------------------------------------------------------------------


def _stem_keys(arr) -> dict:
    inp = RenderInputs(patch=Patch(), arr=arr, parts=("synth", "drums"))
    return {
        j.kind: render._stem_key(j.kind, j.cache_payload())
        for j in render._stem_jobs(inp)
    }


def test_the_mics_and_the_kit_key_the_drum_stem_alone(kit):
    base = replace(_arr([["kick", 0], ["hat", 4]], drum_kit=""), cells=[["A1", 0]])
    acoustic = replace(base, drum_kit="naked")
    mics = replace(acoustic, drum_mics={"kick_in": -3.0})
    a, b, c = _stem_keys(base), _stem_keys(acoustic), _stem_keys(mics)
    assert set(a) == {"main", "drums"}
    assert a["main"] == b["main"] == c["main"]
    assert len({a["drums"], b["drums"], c["drums"]}) == 3


def test_the_kit_arriving_moves_the_acoustic_keys(kit, monkeypatch):
    arr = replace(_arr([["kick", 0]]), cells=[["A1", 0]])
    inp = RenderInputs(patch=Patch(), arr=arr, parts=("synth", "drums"))
    ready, stems = inp.key(), _stem_keys(arr)
    monkeypatch.setattr(
        fetch, "availability", lambda *a, **k: {"ready": False, "megabytes": 9}
    )
    arrange.forget_kit()
    assert inp.key() != ready
    assert _stem_keys(arr)["drums"] != stems["drums"]
    assert _stem_keys(arr)["main"] == stems["main"]


def test_the_drum_cap_holds_every_row_on_eight_bars_of_32_steps():
    from synthlab.studio import limits
    from synthlab.studio.state import ALL_DRUM_ROWS

    assert limits.MAX_DRUM_CELLS >= len(ALL_DRUM_ROWS) * 8 * 32


# --- the real kit -----------------------------------------------------------------

REAL = Path(
    os.environ.get(
        "SYNTHLAB_TEST_KIT",
        Path(os.environ.get("TEMP", "/nonexistent")) / "naked-drums-src",
    )
)


@pytest.mark.slow
@pytest.mark.skipif(not REAL.is_dir(), reason="the acoustic kit is not on this machine")
def test_the_real_kit_plays_a_beat(monkeypatch):
    monkeypatch.setenv(fetch.KIT_ENV, str(REAL))
    arrange.forget_kit()
    if not fetch.availability()["ready"]:
        pytest.skip("the kit there is incomplete")
    arr = _arr(BEAT["drums"], BEAT["hits"], drum_bars=1)
    out = build_song(Patch(), arr, None).render(normalize=0)
    assert np.all(np.isfinite(out)) and np.max(np.abs(out)) > 0.05
    arrange.forget_kit()
