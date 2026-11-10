"""Section automation lanes (`song_model.set_lane`): a (section, track)
cell carries a few breakpoint lanes over ONE PASS of the section — the
track's level (`gain`, a fader after its FX) or a curvable field of its
sound. A lane makes the section render as one flat cycle; a field lane is
resolved per note through the machinery a loop's curves use, a gain lane
scales the finished stem. A song without lanes renders, and keys, exactly
as it did before they existed."""

from __future__ import annotations

import io
import json
import wave
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.core import SR
from synthlab.studio import limits, render, schema
from synthlab.studio import song_model as sm
from synthlab.studio.curves import CURVABLE, GAIN_MAX, lane_array
from synthlab.studio.render import (
    StemCache,
    section_audio,
    section_key,
    section_windows,
)
from synthlab.studio.server import create_app
from synthlab.synth import Patch
from tests.studio.test_song_routes import (
    HX,
    changed,
    oob_ids,
    post,
    refused,
    song,
    use_song,
)

N = schema.nodes()
# a bright saw through an open-able filter, short release: what a cutoff
# lane is heard on
SAW = Patch(
    name="saw", osc1="saw", cutoff=400.0, resonance=0.1, filter_env=0.0, release=0.05
).to_json()
PAD = Patch(name="pad", osc1="square", release=0.2).to_json()
REVERB = [{"kind": "reverb", "params": {"decay": 1.5, "mix": 0.4}}]
RISE = [[0.0, 0.0], [1.0, 1.0]]
FADE_IN = [[0.0, 0.0], [1.0, 0.5]]  # silence to unity


def _notes(cells, bars=1, spb=4) -> dict:
    return {"cells": [list(c) for c in cells], "bars": bars, "steps_per_bar": spb}


def _song(bars=4, bpm=120.0, **lead) -> tuple[dict, str, dict]:
    """A 4-bar section: a Lead playing a 1-bar riff of eighths, a Pad
    holding a 2-bar chord tone, a 1-bar beat. Returns (song, section id,
    track ids)."""
    s = sm.new_song(bpm, "A1", "minor")
    ids = {
        "lead": sm.add_track(s, "synth", "Lead", **({"patch": SAW} | lead)),
        "pad": sm.add_track(s, "synth", "Pad", patch=PAD),
        "drums": sm.add_track(s, "drums", "Drums"),
        "vox": sm.add_track(s, "audio", "Vox"),
    }
    sid = sm.add_section(s, "Build", bars)
    riff = _notes([("A2", st) for st in range(0, 8)], spb=8)
    held = _notes([("C3", st) for st in range(1, 7)], bars=2)
    beat = {"drums": [["kick", 0], ["snare", 2]], "bars": 1, "steps": 4}
    sm.set_cell(s, sid, ids["lead"], sm.add_pattern(s, "notes", "riff", riff))
    sm.set_cell(s, sid, ids["pad"], sm.add_pattern(s, "notes", "held", held))
    sm.set_cell(s, sid, ids["drums"], sm.add_pattern(s, "drums", "beat", beat))
    return s, sid, ids


def _sec(s, sid) -> dict:
    return s["sections"][sm.section_index(s, sid)]


def _parts(s, sid):
    return sm.section_arrangement(s, _sec(s, sid))


def _only(s, ids, *keep) -> None:
    """Mute every track but `keep` (their cells and clocks stay)."""
    for t in s["tracks"]:
        t["mute"] = t["id"] not in {ids[k] for k in keep}


def _one_pass(s, sid, ids, via="pad"):
    """The section rendered as ONE whole-section pass with nothing audibly
    automated: the reference an automated render is measured against. A
    lane on a muted track (`via`) makes the cycle the section — a silenced
    track keeps its lanes, as it keeps its clocks."""
    ref = json.loads(json.dumps(s))
    assert next(t for t in ref["tracks"] if t["id"] == ids[via])["mute"]
    target = "gain" if via == "drums" else "cutoff"
    sm.set_lane(ref, sid, ids[via], target, RISE)
    return section_audio(_parts(ref, sid))


def _rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(x))))


def _centroid(x) -> float:
    """Spectral centroid of a stretch of audio, in Hz."""
    mono = x.mean(axis=1) * np.hanning(len(x))
    mag = np.abs(np.fft.rfft(mono))
    return float(np.sum(np.fft.rfftfreq(len(mono), 1 / SR) * mag) / np.sum(mag))


# --- the model ---------------------------------------------------------------------


def test_a_lane_is_added_replaced_and_removed():
    s, sid, ids = _song()
    assert "auto" not in _sec(s, sid)
    sm.set_lane(s, sid, ids["lead"], "cutoff", [[1.0, 0.9], [0.0, 0.1]])
    sm.set_lane(s, sid, ids["lead"], "gain", FADE_IN)
    lanes = sm.cell_lanes(_sec(s, sid), ids["lead"])
    # kept in position order, one lane a target, in the order they were added
    assert lanes == [
        {"target": "cutoff", "points": [[0.0, 0.1], [1.0, 0.9]]},
        {"target": "gain", "points": FADE_IN},
    ]
    sm.set_lane(s, sid, ids["lead"], "cutoff", [[0.0, 0.5], [0.5, 1.0], [1.0, 0.5]])
    lanes = sm.cell_lanes(_sec(s, sid), ids["lead"])
    assert [ln["target"] for ln in lanes] == ["cutoff", "gain"]
    assert len(lanes[0]["points"]) == 3
    assert schema.conform_strict(s, N.SONG) == s  # a schema fixed point
    sm.remove_lane(s, sid, ids["lead"], "cutoff")
    sm.remove_lane(s, sid, ids["lead"], "resonance")  # not there: no change
    assert [ln["target"] for ln in sm.cell_lanes(_sec(s, sid), ids["lead"])] == ["gain"]
    sm.remove_lane(s, sid, ids["lead"], "gain")
    assert "auto" not in _sec(s, sid)  # absent again, not an empty dict


def test_a_drums_cell_automates_its_level_only_and_an_audio_track_its_takes():
    s, sid, ids = _song()
    sm.set_lane(s, sid, ids["drums"], "gain", FADE_IN)
    with pytest.raises(sm.SongError, match="automate that"):
        sm.set_lane(s, sid, ids["drums"], "cutoff", RISE)
    # an audio track's lanes (its level, its rack's units) move its takes:
    # a section without one of them has nothing to automate
    with pytest.raises(sm.SongError, match="no take in Build"):
        sm.set_lane(s, sid, ids["vox"], "gain", RISE)
    with pytest.raises(sm.SongError, match="automate that"):
        sm.set_lane(s, sid, ids["vox"], "cutoff", RISE)
    with pytest.raises(sm.SongError, match="automate that"):
        sm.set_lane(s, sid, ids["lead"], "attack", RISE)  # not CURVABLE
    assert sm.lane_targets(s["tracks"][0]) == ("gain", *CURVABLE)


def test_a_lane_needs_a_cell_that_plays_and_a_start_and_an_end():
    s, sid, ids = _song()
    empty = sm.add_section(s, "Empty", 2)
    with pytest.raises(sm.SongError, match="empty"):
        sm.set_lane(s, empty, ids["lead"], "cutoff", RISE)
    for bad in ([[0.0, 0.5]], [], "rise", None, [[0.0, float("nan")], [1.0, 1.0]]):
        with pytest.raises(sm.SongError, match="start and an end"):
            sm.set_lane(s, sid, ids["lead"], "cutoff", bad)
    assert "auto" not in _sec(s, sid)


def test_a_cell_holds_at_most_max_cell_lanes():
    s, sid, ids = _song()
    targets = list(CURVABLE)[: limits.MAX_CELL_LANES]
    for t in targets:
        sm.set_lane(s, sid, ids["lead"], t, RISE)
    with pytest.raises(sm.SongError, match="at most"):
        sm.set_lane(s, sid, ids["lead"], "gain", FADE_IN)
    sm.set_lane(s, sid, ids["lead"], targets[0], FADE_IN)  # replacing is fine


def test_lanes_follow_their_cell_when_it_is_cleared_copied_or_its_track_goes():
    s, sid, ids = _song()
    sm.set_lane(s, sid, ids["lead"], "cutoff", RISE)
    sm.set_lane(s, sid, ids["pad"], "gain", FADE_IN)
    dup = sm.duplicate_section(s, sid)
    assert _sec(s, dup)["auto"] == _sec(s, sid)["auto"]
    assert _sec(s, dup)["auto"] is not _sec(s, sid)["auto"]
    sm.set_cell(s, sid, ids["lead"], None)  # a lane rides a cell that plays
    assert list(_sec(s, sid)["auto"]) == [ids["pad"]]
    for sec in (sid, dup):
        sm.set_cell(s, sec, ids["pad"], None)
    assert sm.remove_track(s, ids["pad"]) == []
    assert "auto" not in _sec(s, sid) and list(_sec(s, dup)["auto"]) == [ids["lead"]]
    assert schema.conform_strict(s, N.SONG) == s


def test_a_lane_makes_the_whole_section_one_flat_cycle():
    """A 1-bar riff over a 2-bar pad in a 4-bar section loops every 2 bars.
    A lane runs across the whole pass, so the cycle becomes the section and
    every repeat is one pass of it."""
    s, sid, ids = _song()
    sec = _sec(s, sid)
    sec["repeats"] = 3
    assert sm.cycle_bars(s, sec) == 2 and len(sm.section_timeline(s, sec)) == 6
    sm.set_lane(s, sid, ids["lead"], "cutoff", RISE)
    n = sm.cycle_samples(4, 120.0)
    assert sm.cycle_bars(s, sec) == 4
    assert sm.section_timeline(s, sec) == [(i * n, n) for i in range(3)]
    parts = _parts(s, sid)
    assert parts.flat and parts.cycle_bars == 4 and parts.arr.bars == 4
    assert parts.auto == {"curves": {"cutoff": RISE}, "at": 0, "n": n}
    # a muted track keeps its lanes, as it keeps its clocks: a stem export
    # (every other track muted) keeps the mix's cycle
    _only(s, ids, "pad")
    assert sm.cycle_bars(s, sec) == 4


# --- the schema --------------------------------------------------------------------


def test_a_song_saved_before_lanes_loads_unchanged():
    """No section gains an `auto` key: a song without lanes conforms to
    exactly what it did, and the default section has none either."""
    s, sid, _ids = _song()
    assert all("auto" not in sec for sec in s["sections"])
    assert "auto" not in N.SECTION.default()
    again = schema.conform_strict(json.loads(json.dumps(s)), N.SONG)
    assert again == s and "auto" not in again["sections"][0]
    assert "auto" not in schema.conform({"id": "ab12cd34", "auto": {}}, N.SECTION)


def _with_auto(auto) -> dict:
    s, sid, ids = _song()
    _sec(s, sid)["auto"] = auto(ids) if callable(auto) else auto
    out = schema.conform(json.loads(json.dumps(s)), N.SONG)
    assert schema.conform_strict(out, N.SONG) == out
    return out["sections"][0].get("auto", {}), ids


def test_hostile_lane_values_are_clamped_or_dropped():
    lane = lambda t, pts: {"target": t, "points": pts}  # noqa: E731
    nan, inf = float("nan"), float("inf")
    auto, ids = _with_auto(
        lambda ids: {
            ids["lead"]: [
                lane("cutoff", [[2.0, 9.0], [-1.0, -3.0], [0.5, 0.5]]),  # clamped
                lane("resonance", [[0.9, 0.1], [0.1, 0.9]]),  # sorted
                lane("cutoff", RISE),  # a second lane on one target: dropped
                lane("attack", RISE),  # not CURVABLE
                lane("nope", RISE),
                lane("wt_pos", [[0.0, nan], [1.0, inf], [0.5, 0.5]]),  # one left
                lane("drive", [[0.0, 0.2]]),  # no end
                lane("pitch_bend", [[0.0, True], [1.0, "x"], [0.2, 0.3], [0.8, 0.4]]),
                {"points": RISE},
                "lane",
            ],
            ids["drums"]: [lane("cutoff", RISE), lane("gain", FADE_IN)],
            ids["vox"]: [lane("gain", FADE_IN)],  # an audio track has none
            "nosuch00": [lane("gain", FADE_IN)],
        }
    )
    assert auto == {
        ids["lead"]: [
            lane("cutoff", [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]]),
            lane("resonance", [[0.1, 0.9], [0.9, 0.1]]),
            lane("pitch_bend", [[0.2, 0.3], [0.8, 0.4]]),
        ],
        ids["drums"]: [lane("gain", FADE_IN)],
    }


def test_too_many_lanes_or_points_are_capped():
    many = [[i / 99, (i % 7) / 7] for i in range(100)]
    auto, ids = _with_auto(
        lambda ids: {
            ids["lead"]: [{"target": t, "points": many} for t in CURVABLE],
        }
    )
    lanes = auto[ids["lead"]]
    assert len(lanes) == limits.MAX_CELL_LANES
    assert all(len(ln["points"]) == limits.MAX_LANE_POINTS for ln in lanes)


@pytest.mark.parametrize("bad", [5, "x", None, [], [1, 2], {"a": 1}, {"a": []}, True])
def test_a_hostile_auto_field_is_dropped_never_an_error(bad):
    auto, _ids = _with_auto(bad)
    assert auto == {}


def test_a_lane_on_a_cell_that_holds_no_pattern_is_dropped():
    s, sid, ids = _song()
    sm.set_lane(s, sid, ids["lead"], "cutoff", RISE)
    raw = json.loads(json.dumps(s))
    raw["sections"][0]["cells"][ids["lead"]] = "gone0000"  # a dangling pattern
    assert "auto" not in schema.conform(raw, N.SONG)["sections"][0]


# --- rendering ---------------------------------------------------------------------


def test_a_song_without_lanes_keeps_its_keys_and_its_audio():
    """Adding a lane and taking it off again leaves nothing behind: the
    section's key and its render are the ones from before lanes existed.
    (The pinned key literals in test_render.py / test_song_routes.py hold
    the absolute values; this holds the round trip.)"""
    s, sid, ids = _song()
    before = _parts(s, sid)
    key, audio = section_key(before, None), section_audio(before)
    sm.set_lane(s, sid, ids["lead"], "cutoff", RISE)
    assert section_key(_parts(s, sid), None) != key
    sm.remove_lane(s, sid, ids["lead"], "cutoff")
    after = _parts(s, sid)
    assert after == before and section_key(after, None) == key
    again = section_audio(after)
    assert np.array_equal(again.base, audio.base)
    assert np.array_equal(again.tail, audio.tail)
    assert "auto" not in json.dumps(before.layers)


def test_a_flat_gain_lane_at_unity_is_no_lane_at_all():
    """A level lane resting at 100% end to end: the section keeps its
    cycle, its key and its samples, bit for bit."""
    s, sid, ids = _song()
    before = _parts(s, sid)
    key, audio = section_key(before, None), section_audio(before)
    for t in ("lead", "pad", "drums"):
        sm.set_lane(s, sid, ids[t], "gain", [[0.0, 0.5], [0.4, 0.5], [1.0, 0.5]])
    assert sm.section_lanes(s, _sec(s, sid)) == {}
    after = _parts(s, sid)
    assert after == before and after.cycle_bars == 2 and after.gains == {}
    assert section_key(after, None) == key
    assert np.array_equal(section_audio(after).base, audio.base)


def test_a_gain_lane_ramps_the_stem_and_renders_no_note_again():
    """The Lead alone, fading in from silence to unity over the section:
    its stem is the un-automated stem times the ramp, sample for sample —
    and a second shape of the lane re-uses every cached stem (the lane is a
    fader after the FX, applied to the finished stem)."""
    s, sid, ids = _song()
    _only(s, ids, "lead")
    s["tracks"][0]["fx"] = REVERB
    plain = _one_pass(s, sid, ids)
    sm.set_lane(s, sid, ids["lead"], "gain", FADE_IN)
    parts = _parts(s, sid)
    assert parts.gains == {"main": FADE_IN} and not parts.auto
    cache = StemCache()
    faded = section_audio(parts, stem_cache=cache)
    n = len(plain.base)
    env = np.linspace(0.0, 1.0, n)
    np.testing.assert_allclose(faded.base, plain.base * env[:, None], atol=1e-12)
    # what rings on leaves at the level the section ended on (unity here)
    np.testing.assert_allclose(faded.tail, plain.tail, atol=1e-12)
    q = n // 4
    first, last = _rms(faded.base[:q]), _rms(faded.base[-q:])
    assert first < 0.2 * last, (first, last)
    assert abs(_rms(faded.base[-q:]) / _rms(plain.base[-q:]) - 0.875) < 0.03
    # another shape: same stems, another section key
    stems = set(cache.keys())
    sm.set_lane(s, sid, ids["lead"], "gain", [[0.0, 0.5], [1.0, 0.0]])
    moved = _parts(s, sid)
    assert section_key(moved, None) != section_key(parts, None)
    out = section_audio(moved, stem_cache=cache)
    assert set(cache.keys()) == stems
    assert _rms(out.base[:q]) > 5 * _rms(out.base[-q:])
    assert not np.any(out.tail)  # faded to nothing: nothing rings on


def test_a_gain_lane_scales_only_its_own_track():
    """The drums fade out under a Lead that stays put: the mix is the
    un-automated Lead plus the ramped drums."""
    s, sid, ids = _song()
    _only(s, ids, "lead", "drums")
    lead_only = json.loads(json.dumps(s))
    _only(lead_only, ids, "lead")
    drums_only = json.loads(json.dumps(s))
    _only(drums_only, ids, "drums")
    sm.set_lane(s, sid, ids["drums"], "gain", [[0.0, 0.5], [1.0, 0.0]])
    assert _parts(s, sid).gains == {"drums": [[0.0, 0.5], [1.0, 0.0]]}
    mix = section_audio(_parts(s, sid)).base
    lead = _one_pass(lead_only, sid, ids).base
    drums = _one_pass(drums_only, sid, ids).base
    env = np.linspace(1.0, 0.0, len(mix))[:, None]
    np.testing.assert_allclose(mix, lead + drums * env, atol=1e-9)


def test_a_cutoff_lane_opens_the_filter_across_the_section():
    """The saw riff with its cutoff swept 60 Hz -> 16 kHz over four bars:
    the last bar is far brighter than the first (and without the lane the
    two bars are the same riff, the same brightness)."""
    s, sid, ids = _song()
    _only(s, ids, "lead")
    plain = _one_pass(s, sid, ids).base
    sm.set_lane(s, sid, ids["lead"], "cutoff", RISE)
    swept = section_audio(_parts(s, sid)).base
    bar = len(swept) // 4
    dark, bright = _centroid(swept[:bar]), _centroid(swept[-bar:])
    still = (_centroid(plain[:bar]), _centroid(plain[-bar:]))
    assert bright > 3 * dark, (dark, bright)
    assert abs(still[1] / still[0] - 1.0) < 0.02, still
    # each bar brighter than the one before: the lane runs across the pass,
    # it does not restart with the 1-bar riff
    bars = [_centroid(swept[i * bar : (i + 1) * bar]) for i in range(4)]
    assert bars == sorted(bars), bars


def test_a_lane_value_maps_onto_the_registrys_curve_range():
    lo, hi = CURVABLE["cutoff"]
    vals = lane_array([[0.0, 0.0], [0.5, 1.0], [1.0, 0.25]], "cutoff", 101)
    assert vals[0] == lo and vals[50] == hi and vals[100] == lo + 0.25 * (hi - lo)
    assert np.all((vals >= lo) & (vals <= hi))
    # the level: 0..1 is x0..x2, and 0.5 is exactly unity
    gain = lane_array([[0.0, 0.0], [1.0, 0.5]], "gain", 5)
    assert gain[0] == 0.0 and gain[-1] == 1.0 and GAIN_MAX == 2.0
    # a slice of the lane is the lane: a chunk reads the values the cycle does
    assert np.array_equal(
        lane_array(RISE, "cutoff", 1000, 400), lane_array(RISE, "cutoff", 1000)[400:]
    )
    # values held out to the edges before the first / after the last point
    held = lane_array([[0.25, 0.2], [0.75, 0.8]], "wt_pos", 9)
    assert held[0] == held[2] == 0.2 and held[-1] == held[6] == 0.8


def test_a_section_lane_wins_over_the_sounds_own_curve():
    """The Lead's sound carries a drawn cutoff curve (one loop long) and a
    resonance curve. A section's cutoff lane replaces the cutoff curve
    there — the resonance curve still plays, and so does the cutoff curve
    in a section without the lane."""
    drawn = {"cutoff": [[0.0, 1.0], [1.0, 0.0]], "resonance": [[0.0, 0.0], [1.0, 0.5]]}
    s, sid, ids = _song(curves=drawn)
    other = sm.duplicate_section(s, sid)
    sm.set_lane(s, sid, ids["lead"], "cutoff", RISE)
    parts = _parts(s, sid)
    assert list(parts.curves) == ["resonance"]
    assert parts.auto["curves"] == {"cutoff": RISE}
    assert set(_parts(s, other).curves) == {"cutoff", "resonance"}
    _only(s, ids, "lead")
    swept = section_audio(_parts(s, sid)).base
    bar = len(swept) // 4
    assert _centroid(swept[-bar:]) > 2.5 * _centroid(swept[:bar])  # the lane's rise


def test_a_layer_track_plays_its_lane_and_only_its_lane():
    """A layer ignores its sound's own curves (in a song as in a loop); a
    section lane is the one automation it plays, and it rides the layer's
    render dict, so the layer's stem key holds it."""
    s, sid, ids = _song()
    s["tracks"][1]["patch"] = SAW  # the Pad: a filter to hear
    s["tracks"][1]["curves"] = {"cutoff": [[0.0, 1.0], [1.0, 0.0]]}
    sm.set_lane(s, sid, ids["pad"], "cutoff", RISE)
    parts = _parts(s, sid)
    n = sm.cycle_samples(4, 120.0)
    assert not parts.auto and parts.curves == {}
    (lay,) = parts.layers
    assert lay["auto"] == {"curves": {"cutoff": RISE}, "at": 0, "n": n}
    _only(s, ids, "pad")
    swept = section_audio(_parts(s, sid)).base
    half = len(swept) // 2  # the 2-bar pad pattern, played twice
    assert _centroid(swept[half:]) > 1.5 * _centroid(swept[:half])


def _automated(fx=()) -> tuple[dict, str]:
    """Every kind of lane at once, with a note held over the bar-2 chunk
    seam (the Pad's) and a reverb on the Lead."""
    s, sid, ids = _song()
    s["tracks"][0]["fx"] = list(fx)
    s["tracks"][1]["patch"] = SAW
    sm.set_lane(s, sid, ids["lead"], "cutoff", [[0.0, 0.1], [0.6, 1.0], [1.0, 0.3]])
    sm.set_lane(s, sid, ids["lead"], "pitch_bend", [[0.0, 0.5], [1.0, 1.0]])
    sm.set_lane(s, sid, ids["lead"], "gain", FADE_IN)
    sm.set_lane(s, sid, ids["pad"], "resonance", RISE)
    sm.set_lane(s, sid, ids["pad"], "gain", [[0.0, 0.5], [1.0, 0.1]])
    sm.set_lane(s, sid, ids["drums"], "gain", [[0.0, 0.0], [0.5, 0.5], [1.0, 0.5]])
    return s, sid


@pytest.mark.parametrize("fx", [(), REVERB], ids=["dry", "reverb"])
def test_an_automated_cycle_in_chunks_is_the_cycle_in_one_piece(monkeypatch, fx):
    """A lane's position is over the section pass, so a chunk takes its
    slice: the 4-bar cycle rendered as two 2-bar chunks is the one-piece
    render, at the tolerance the un-automated chunk tests hold (1e-9) —
    notes ringing past the seam keep following their lane."""
    s, sid = _automated(fx)
    parts = _parts(s, sid)
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 10.0)
    assert len(section_windows(parts)) == 1
    whole = section_audio(parts)
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 4.0)
    assert section_windows(parts) == [(0, 2), (2, 4)]
    chunked = section_audio(parts)
    assert np.max(np.abs(whole.base)) > 0.1
    np.testing.assert_allclose(chunked.base, whole.base, atol=1e-9)
    m = max(len(chunked.tail), len(whole.tail))
    pad = lambda t: np.vstack([t, np.zeros((m - len(t), 2))])  # noqa: E731
    np.testing.assert_allclose(pad(chunked.tail), pad(whole.tail), atol=1e-9)
    # the second chunk reads its lanes from its own first sample
    win = parts.window(2, 4)
    at = sm.bar_sample(2, 120.0)
    assert win.auto["at"] == at and win.layers[0]["auto"]["at"] == at
    assert win.auto["curves"] == parts.auto["curves"]


def test_a_chunks_key_holds_its_slice_of_the_lane():
    s, sid = _automated()
    parts = _parts(s, sid)
    keys = {parts.window(b, b + 2).inputs().key() for b in (0, 2)}
    assert len(keys) == 2
    # ... and the section's key moves with every lane, gain lanes included
    k = section_key(parts, None)
    ids = {t["name"]: t["id"] for t in s["tracks"]}
    for track, target in (("Lead", "cutoff"), ("Pad", "resonance"), ("Drums", "gain")):
        other = json.loads(json.dumps(s))
        sm.remove_lane(other, sid, ids[track], target)
        assert section_key(_parts(other, sid), None) != k, (track, target)


# --- the song: repeats, plan, export, stems ----------------------------------------


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _pcm(wav: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2)


def _faded_song() -> tuple[dict, str, dict]:
    """The Lead (dry, short release) fading in over a 2-bar section played
    three times, under the beat."""
    s, sid, ids = _song(bars=2)
    _only(s, ids, "lead", "drums")
    _sec(s, sid)["repeats"] = 3
    sm.set_lane(s, sid, ids["lead"], "gain", FADE_IN)
    return s, sid, ids


def test_every_repeat_replays_the_lane_in_the_plan_and_the_export(client):
    """Three repeats of a faded-in section: the plan queues three passes of
    one whole-section cycle, the export is those passes stitched, and the
    Lead's stem rises from silence in every one of them."""
    s, sid, _ids = _faded_song()
    use_song(client, s)
    plan = client.get("/song/plan.json").json()
    (entry,) = plan["sections"]
    n = sm.cycle_samples(2, 120.0)
    assert (entry["cycle_bars"], entry["cycles"], entry["passes"]) == (2, 1, 3)
    assert entry["cycle_samples"] == n and plan["song_samples"] == 3 * n
    export = _pcm(client.get("/export/song.wav").content)
    first = _pcm(client.get(entry["url"]).content)
    loop = _pcm(client.get(entry["loop_url"]).content)
    assert np.array_equal(export, np.vstack([first, loop, loop]))
    with zipfile.ZipFile(io.BytesIO(client.get("/export/stems.zip").content)) as z:
        assert z.namelist() == ["synth-1.wav", "drums.wav"]
        lead = _pcm(z.read("synth-1.wav")).astype(float)
        drums = _pcm(z.read("drums.wav")).astype(float)
    assert lead.shape == export.shape
    q = n // 4
    for i in range(3):
        one = lead[i * n : (i + 1) * n]
        assert _rms(one[:q]) < 0.25 * _rms(one[-q:]), i
    # the drums carry no lane: every pass at one level
    levels = [_rms(drums[i * n : (i + 1) * n]) for i in range(3)]
    assert max(levels) / min(levels) < 1.01
    assert np.max(np.abs(lead + drums - export)) <= 2  # the stems add up


def test_a_lane_changes_the_plans_urls_and_only_its_sections(client):
    s, sid, ids = _song(bars=2)
    other = sm.duplicate_section(s, sid)
    use_song(client, s)
    urls = lambda: {  # noqa: E731
        e["section_id"]: (e["url"], e["loop_url"])
        for e in client.get("/song/plan.json").json()["sections"]
    }
    before = urls()
    r = post(
        client,
        "/song/cell/auto",
        {
            "section_id": other,
            "track_id": ids["lead"],
            "op": "set",
            "target": "cutoff",
            "start": "0",
            "end": "1",
        },
    )
    assert r.status_code == 200 and changed(r) == [other]
    after = urls()
    # the second section's own WAVs moved (the first's URLs may too: they
    # carry the song-wide gain, which `song-changed` deliberately leaves to
    # the plan)
    assert after[other] != before[other]
    assert sm.section_index(song(client), sid) == 0 and before[sid]


def test_the_midi_export_carries_no_automation(client):
    """Notes only: a lane changes how the section is cut into cycles, not
    what it plays, so the file is the one the song gives without lanes."""
    s, sid, ids = _song(bars=2)
    use_song(client, s)
    plain = client.get("/export/song.mid")
    assert plain.status_code == 200
    sm.set_lane(client.app.state.studio.song, sid, ids["lead"], "cutoff", RISE)
    sm.set_lane(client.app.state.studio.song, sid, ids["drums"], "gain", FADE_IN)
    assert client.get("/export/song.mid").content == plain.content


# --- the route and the drawer ------------------------------------------------------


def _auto(client, **data):
    base = {"section_id": "s1", "track_id": "t1"}
    return post(client, "/song/cell/auto", base | {k: str(v) for k, v in data.items()})


def _lanes(client, sid="s1", tid="t1") -> list[dict]:
    sec = next(x for x in song(client)["sections"] if x["id"] == sid)
    return sm.cell_lanes(sec, tid)


@pytest.fixture
def studio(tmp_path):
    """The song routes' fixture song (sections s1/s2, tracks td/t1/t2/ta)."""
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        yield c


def test_adding_a_lane_starts_flat_where_the_sound_rests(studio):
    """A new lane moves nothing until it is shaped: the level at unity, a
    field at the sound's own value."""
    lo, hi = CURVABLE["cutoff"]
    rest = (Patch().cutoff - lo) / (hi - lo)
    r = _auto(studio, op="add", target="cutoff")
    assert r.status_code == 200, r.text[:300]
    assert _lanes(studio) == [
        {"target": "cutoff", "points": [[0.0, rest], [1.0, rest]]}
    ]
    assert "song-arranger" in oob_ids(r.text)  # the cell's marker
    r = _auto(studio, op="add", target="gain")
    assert _lanes(studio)[1] == {"target": "gain", "points": [[0.0, 0.5], [1.0, 0.5]]}
    assert changed(r) == []  # a level lane at unity changes no audio
    refused(_auto(studio, op="add", target="gain"), "already")


def test_setting_a_lane_writes_its_breakpoints(studio):
    r = _auto(studio, op="set", target="gain", start="0", end="0.5")
    assert r.status_code == 200 and changed(r) == ["s1", "s2"]  # s2's head folds s1
    assert _lanes(studio) == [{"target": "gain", "points": FADE_IN}]
    # the optional mid breakpoint: halfway, at the middle, until it is moved
    _auto(studio, op="set", target="gain", start="0", end="0.5", mid_on="on")
    assert _lanes(studio)[0]["points"] == [[0.0, 0.0], [0.5, 0.25], [1.0, 0.5]]
    _auto(
        studio,
        op="set",
        target="gain",
        start="0.2",
        end="0.4",
        mid_on="on",
        mid="1",
        mid_at="0.75",
    )
    assert _lanes(studio)[0]["points"] == [[0.0, 0.2], [0.75, 1.0], [1.0, 0.4]]
    # unticking "mid" drops it; out-of-range values clamp
    _auto(studio, op="set", target="gain", start="-4", end="9", mid="0.5")
    assert _lanes(studio)[0]["points"] == [[0.0, 0.0], [1.0, 1.0]]
    r = _auto(studio, op="remove", target="gain")
    assert r.status_code == 200 and _lanes(studio) == []
    assert "auto" not in song(studio)["sections"][0]


@pytest.mark.parametrize(
    ("data", "word"),
    [
        ({"op": "set", "target": "cutoff", "start": "nan", "end": "1"}, "number"),
        ({"op": "set", "target": "cutoff", "start": "0", "end": "inf"}, "number"),
        ({"op": "set", "target": "cutoff", "start": "0"}, "number"),
        ({"op": "set", "target": "cutoff", "start": "0", "end": "x"}, "number"),
        ({"op": "add", "target": "attack"}, "automate that"),
        ({"op": "add", "target": ""}, "automate that"),
        ({"op": "add"}, "automate that"),
        ({"op": "nope", "target": "cutoff"}, "No such lane edit"),
        ({"target": "cutoff"}, "No such lane edit"),
        ({"op": "add", "target": "cutoff", "track_id": "td"}, "automate that"),
        ({"op": "add", "target": "cutoff", "track_id": "ta"}, "automate that"),
        ({"op": "add", "target": "gain", "track_id": "ta"}, "no take in"),
        ({"op": "add", "target": "gain", "track_id": "t2"}, "empty"),  # s1 has no t2
        ({"op": "add", "target": "gain", "track_id": "zz"}, "No such track"),
        ({"op": "add", "target": "gain", "section_id": "zz"}, "No such section"),
        ({"op": "remove", "target": "gain", "section_id": "zz"}, "No such section"),
    ],
)
def test_a_hostile_lane_post_is_a_toast_and_changes_nothing(studio, data, word):
    before = json.dumps(song(studio), sort_keys=True)
    refused(_auto(studio, **data), word)
    assert json.dumps(song(studio), sort_keys=True) == before


def test_a_lane_is_refused_on_a_section_too_long_to_render_as_one_pass(studio):
    """64 bars at 120 bpm is a 128 s pass, over the 120 s cap on a cycle:
    fine as 1-bar cycles, refused as one automated pass."""
    s = studio.app.state.studio
    s.song["sections"][0]["bars"] = 64
    r = _auto(studio, op="set", target="cutoff", start="0", end="1")
    refused(r, "one 128s pass", "Shorten the section")
    assert _lanes(studio) == []
    # a lane the render ignores (the level at unity) is no such pass
    assert _auto(studio, op="add", target="gain").status_code == 200


def test_the_drawer_draws_a_cells_lanes_and_the_arranger_marks_the_cell(studio):
    post(studio, "/song/drawer", {"section_id": "s1", "track_id": "t1"})
    r = _auto(studio, op="set", target="cutoff", start="0", end="1", mid_on="on")
    assert {"song-drawer", "song-arranger"} <= oob_ids(r.text)
    _auto(studio, op="set", target="gain", start="0", end="0.5")
    html = studio.get("/view/song", headers=HX).text
    drawer = html[html.index('id="song-drawer"') :]
    assert drawer.count('class="lane-row"') == 2
    assert 'data-target="cutoff"' in drawer and 'data-target="gain"' in drawer
    # labelled from paramui, read out in the field's unit / as a percentage
    assert ">Cutoff<" in drawer and ">Level<" in drawer
    # (a lane's read-out is the parameter's own units while its slider is
    # 0..1: `data-untyped`, so it takes no typed value — tweaktype.js)
    plain = drawer.replace("<output data-untyped>", "<output>")
    plain = plain.replace("<output data-pct>", "<output>")
    assert "<output>60 Hz</output>" in plain and "<output>16000 Hz</output>" in plain
    assert "<output>8030 Hz</output>" in plain  # the mid point, halfway
    assert "<output>0%</output>" in plain and "<output>100%</output>" in plain
    assert "<output data-untyped>60 Hz</output>" in drawer
    assert 'name="mid_on" checked' in drawer and 'name="mid_at"' in drawer
    assert 'points="0.0,40.0 50.0,20.0 100.0,0.0"' in drawer  # the thumbnail
    # the menu offers what is not automated yet
    menu = drawer[drawer.index('class="lane-add"') :]
    assert '<option value="resonance">Resonance</option>' in menu
    assert 'value="cutoff"' not in menu and 'value="gain"' not in menu
    cell = html[html.index('id="cell-s1-t1"') :]
    cell = cell[: cell.index("</td>")]
    assert 'class="cell-auto" data-lanes="2"' in cell
    assert 'title="Automated: Cutoff, Level"' in cell and ", automated" in cell
    assert "cell-auto" not in html[html.index('id="cell-s2-t1"') :].split("</td>")[0]


def test_a_drums_cell_offers_its_level_and_a_pool_pattern_no_lanes(studio):
    post(studio, "/song/drawer", {"section_id": "s1", "track_id": "td"})
    html = studio.get("/view/song", headers=HX).text
    menu = html[html.index('class="lane-add"') :]
    menu = menu[: menu.index("</form>")]
    assert menu.count("<option") == 1 and ">Level<" in menu
    post(studio, "/song/drawer", {"pattern_id": "p1"})
    assert "drawer-lanes" not in studio.get("/view/song", headers=HX).text


def test_a_full_cell_offers_no_more_lanes(studio):
    for t in list(CURVABLE)[: limits.MAX_CELL_LANES]:
        _auto(studio, op="add", target=t)
    post(studio, "/song/drawer", {"section_id": "s1", "track_id": "t1"})
    html = studio.get("/view/song", headers=HX).text
    assert html.count('class="lane-row"') == limits.MAX_CELL_LANES
    assert 'class="lane-add"' not in html
    refused(_auto(studio, op="add", target="gain"), "at most")


# --- persistence: project round trip, save, revert ---------------------------------


def test_lanes_ride_the_project_export_and_import(tmp_path):
    s, sid = _automated()
    ids = {"lead": s["tracks"][0]["id"]}
    want = _sec(schema.conform(s, N.SONG), sid)["auto"]
    with TestClient(create_app(data_dir=tmp_path / "a")) as a:
        use_song(a, s)
        proj = a.get("/project.json").json()
    assert proj["song"]["sections"][0]["auto"] == want
    with TestClient(create_app(data_dir=tmp_path / "b")) as b:
        r = b.post(
            "/project/import",
            files={"file": ("x.synthlab.json", json.dumps(proj), "application/json")},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert b.app.state.studio.song["sections"][0]["auto"] == want
        # a hostile import is cleaned, not refused
        proj["song"]["sections"][0]["auto"] = {ids["lead"]: [{"target": "x"}], "q": 5}
        r = b.post(
            "/project/import",
            files={"file": ("x.synthlab.json", json.dumps(proj), "application/json")},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "auto" not in b.app.state.studio.song["sections"][0]


def test_lanes_survive_a_restart(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        _auto(c, op="set", target="cutoff", start="0.1", end="0.9")
    with TestClient(create_app(data_dir=tmp_path)) as c:
        assert _lanes(c) == [{"target": "cutoff", "points": [[0.0, 0.1], [1.0, 0.9]]}]


def test_reverting_the_song_takes_a_lane_back_like_any_song_edit(studio):
    """A song edit is undone through the reset menu's "song" scope (there
    is no per-edit song undo; a lane is part of the song it snapshots): the
    lane goes with the revert, and the section is back on its old key."""
    s = studio.app.state.studio
    s.mark_baseline("song", "as opened")
    key = section_key(sm.section_arrangement(s.song, s.song["sections"][0]), None)
    _auto(studio, op="set", target="cutoff", start="0", end="1")
    assert _lanes(studio)
    r = post(studio, "/fragment/reset", {"scope": "song", "mode": "revert"})
    assert r.status_code == 200, r.text[:300]
    assert _lanes(studio) == [] and "auto" not in s.song["sections"][0]
    assert (
        section_key(sm.section_arrangement(s.song, s.song["sections"][0]), None) == key
    )
