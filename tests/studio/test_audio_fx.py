"""An audio track's rack and lanes (Oct 2026; studio docs/song.md, "An
audio track's rack and lanes"): a track of takes — a mic take, a clip
placed from the library, a chop from the Sample Lab — through an FX chain
of its own, with a level lane and FX-parameter lanes on its row in a
section. The filter-house move: a resonant low-pass swept open across a
sampled loop.

Pinned here: the render (a low-pass darkens the takes, a cutoff lane opens
them over the pass, a level lane and a reverb's ring, another audio track
left in the shared takes stem), keys and audio of a song whose audio
tracks have neither — byte for byte what they were (literals computed on
the code before the feature), the model and the schema (lanes on a row
need a take there; hostile input refused or dropped), and the routes: the
rack's add / set / move / remove (one undo step a gesture), a lane added
from the drawer, the drawer's rack and the arranger's way in."""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.core import SR
from synthlab.studio import schema
from synthlab.studio import song_model as sm
from synthlab.studio.clips import save_clip
from synthlab.studio.curves import fx_target
from synthlab.studio.fxrack import MAX_UNITS, clean_chain, unit_id
from synthlab.studio.render import StemCache, section_audio, section_key
from synthlab.studio.server import create_app
from tests.studio.test_song_automation import _centroid, _rms
from tests.studio.test_song_routes import (
    changed,
    oob_ids,
    post,
    refused,
    song,
    song_dict,
    use_song,
)

N = schema.nodes()
RISE = [[0.0, 0.0], [1.0, 1.0]]


def _noise(root, name: str = "loop", seconds: float = 2.0, seed: int = 1) -> None:
    """A clip of white noise: every frequency, so a filter shows."""
    rng = np.random.default_rng(seed)
    save_clip(root, name, rng.standard_normal((int(seconds * SR), 2)) * 0.2)


def _vox(root, bars: int = 4) -> tuple[dict, str, str]:
    """A song of one audio track ("Vox") with a take of noise filling a
    `bars`-bar section (8 s at 120 bpm: the clip is placed every 2 bars).
    Returns (song, section id, track id)."""
    _noise(root, seconds=4.0)
    s = sm.new_song(120.0, "A1", "minor")
    tid = sm.add_track(s, "audio", "Vox")
    sid = sm.add_section(s, "Drop", bars)
    for bar in range(0, bars, 2):
        sm.place_take(s, sid, tid, "loop", bar)
    return schema.conform(s, N.SONG), sid, tid


def _rack(s: dict, tid: str, *units: dict) -> dict:
    out = json.loads(json.dumps(s))
    sm._track(out, tid)["fx"] = clean_chain(list(units))
    return schema.conform(out, N.SONG)


def _audio(s: dict, sid: str, root, cache=None):
    parts = sm.section_arrangement(s, s["sections"][sm.section_index(s, sid)])
    return parts, section_audio(parts, clip_root=root, stem_cache=cache)


# --- the render -----------------------------------------------------------------------


def test_a_lowpass_on_an_audio_track_darkens_its_takes(tmp_path):
    s, sid, tid = _vox(tmp_path)
    dry_parts, dry = _audio(s, sid, tmp_path)
    assert dry_parts.takes == ()  # no rack: the shared takes stem, as ever
    dark = _rack(s, tid, {"kind": "lowpass", "params": {"cutoff": 500.0, "q": 0.7}})
    parts, wet = _audio(dark, sid, tmp_path)
    assert [name for name, _c, _fx in parts.takes] == [f"clips:{tid}"]
    assert parts.arr.clips == []  # its takes left the shared stem
    assert parts.tracks == {f"clips:{tid}": [tid]}  # a meter of its own
    lo, hi = _centroid(wet.base), _centroid(dry.base)
    assert lo < hi / 4, (lo, hi)  # measured 950 Hz against 11 kHz
    assert set(wet.levels) == {f"clips:{tid}"}
    # the key moved, and moves again with the cutoff
    assert section_key(parts, tmp_path) != section_key(dry_parts, tmp_path)
    darker = _rack(s, tid, {"kind": "lowpass", "params": {"cutoff": 300.0, "q": 0.7}})
    moved, _a = _audio(darker, sid, tmp_path)
    assert section_key(moved, tmp_path) != section_key(parts, tmp_path)


def test_a_cutoff_lane_sweeps_the_filter_open_across_the_section(tmp_path):
    """The filter-house move: the cutoff from its floor to its top over
    the section — dark at its start, bright at its end — through the
    unit's own swept filter (`fxauto`), with its two rings."""
    s, sid, tid = _vox(tmp_path)
    s = _rack(s, tid, {"kind": "lowpass", "params": {"cutoff": 500.0, "q": 2.0}})
    track = sm._track(s, tid)
    target = fx_target(unit_id(track["fx"], 0), "lowpass", "cutoff")
    sm.set_lane(s, sid, tid, target, RISE)
    s = schema.conform(s, N.SONG)
    parts, audio = _audio(s, sid, tmp_path)
    assert parts.fx_auto == {f"clips:{tid}": [[target, RISE]]}
    q = len(audio.base) // 4
    early, late = _centroid(audio.base[:q]), _centroid(audio.base[-q:])
    assert late > 3 * early, (early, late)  # measured 2.2 kHz -> 7.8 kHz
    assert audio.out is not None  # the ring into another section rests
    # closed again: the other way round
    sm.set_lane(s, sid, tid, target, [[0.0, 1.0], [1.0, 0.0]])
    _p, shut = _audio(s, sid, tmp_path)
    assert _centroid(shut.base[:q]) > 3 * _centroid(shut.base[-q:])
    # the lane is in the section's key
    still = json.loads(json.dumps(s))
    sm.set_lane(still, sid, tid, target, [[0.0, 0.5], [1.0, 0.5]])
    assert section_key(sm.section_arrangement(still, still["sections"][0]), None) != (
        section_key(parts, None)
    )


def test_a_level_lane_fades_the_takes_without_a_rack(tmp_path):
    s, sid, tid = _vox(tmp_path)
    _p, flat = _audio(s, sid, tmp_path)
    sm.set_lane(s, sid, tid, "gain", [[0.0, 0.0], [1.0, 0.5]])  # silence -> unity
    parts, faded = _audio(s, sid, tmp_path)
    assert parts.gains == {f"clips:{tid}": [[0.0, 0.0], [1.0, 0.5]]}
    n = len(flat.base)
    assert _rms(faded.base[: n // 16]) < 0.1 * _rms(flat.base[: n // 16])
    # at its end the lane is at unity: the takes as they were (dry, cut at
    # the section's end — no ring of their own)
    np.testing.assert_allclose(faded.base[-64:], flat.base[-64:], atol=1e-3)
    assert len(faded.tail) == len(flat.tail) == 0


def test_a_reverb_rings_on_past_the_section_and_into_its_next_pass(tmp_path):
    s, sid, tid = _vox(tmp_path, bars=2)
    s = _rack(s, tid, {"kind": "reverb", "params": {"mix": 0.5, "decay": 1.5}})
    _p, audio = _audio(s, sid, tmp_path)
    assert len(audio.tail) > SR // 2 and _rms(audio.tail[: SR // 4]) > 1e-3


def test_another_audio_track_stays_in_the_shared_takes_stem(tmp_path):
    """Two audio tracks, one with a rack: the other's takes render in the
    one shared stem exactly as before, and the two stems sum to the mix."""
    s, sid, vox = _vox(tmp_path)
    _noise(tmp_path, "hats", seconds=1.0, seed=7)
    gtr = sm.add_track(s, "audio", "Gtr")
    sm.place_take(s, sid, gtr, "hats", 1)
    s = schema.conform(s, N.SONG)
    wet = _rack(s, vox, {"kind": "highpass", "params": {"cutoff": 4000.0}})
    parts, both = _audio(wet, sid, tmp_path)
    assert parts.tracks == {"clips": [gtr], f"clips:{vox}": [vox]}
    assert [c["clip"] for c in parts.arr.clips] == ["hats"]
    alone = json.loads(json.dumps(wet))
    sm._track(alone, vox)["mute"] = True
    _p, gtr_only = _audio(alone, sid, tmp_path)
    alone = json.loads(json.dumps(wet))
    sm._track(alone, gtr)["mute"] = True
    _p, vox_only = _audio(alone, sid, tmp_path)
    np.testing.assert_allclose(both.base, gtr_only.base + vox_only.base, atol=1e-12)


def test_a_song_whose_audio_tracks_have_no_rack_keeps_its_keys_and_audio(tmp_path):
    """The song routes' fixture song with two takes on Vox in each section:
    the section keys are the literals the code before the audio rack gave
    (computed on that commit, 664d4e2, as was the audio, which came out
    byte for byte the same). An audio track with an empty rack and no lane
    is no change at all."""
    rng = np.random.default_rng(1)
    save_clip(tmp_path, "loop", rng.standard_normal((2 * SR, 2)) * 0.2)
    save_clip(tmp_path, "vox", rng.standard_normal((SR, 2)) * 0.1)
    s = schema.conform(song_dict(), N.SONG)
    for sec in s["sections"]:
        sm.place_take(s, sec["id"], "ta", "loop", 0)
        sm.place_take(s, sec["id"], "ta", "vox", 1)
    s = schema.conform(s, N.SONG)
    keys = {}
    for sec in s["sections"]:
        parts = sm.section_arrangement(s, sec)
        assert parts.takes == () and "clips" in parts.tracks
        keys[sec["id"]] = section_key(parts, tmp_path)
    # RENDER_REV 3 (the booth's spliced pitch shifter) moved every key:
    # these are the keys at that rev
    assert keys == {"s1": "c3def15f33ec1721", "s2": "db35d4e315545d64"}
    # a stored empty rack saves as the track always did
    assert sm._track(s, "ta")["fx"] == []


def test_the_stem_cache_serves_an_audio_tracks_stem_again(tmp_path):
    s, sid, tid = _vox(tmp_path)
    s = _rack(s, tid, {"kind": "lowpass", "params": {"cutoff": 800.0}})
    cache = StemCache()
    _p, first = _audio(s, sid, tmp_path, cache)
    keys = set(cache.keys())
    assert any(k.startswith("cycle-takes") for k in keys)
    _p, again = _audio(s, sid, tmp_path, cache)
    assert set(cache.keys()) == keys
    assert np.array_equal(first.base, again.base)
    # a level lane sits past the cache: no new stem
    sm.set_lane(s, sid, tid, "gain", [[0.0, 0.2], [1.0, 0.5]])
    _audio(s, sid, tmp_path, cache)
    assert set(cache.keys()) == keys


# --- the model and the schema -------------------------------------------------------


def test_an_audio_tracks_rack_and_lanes_round_trip(tmp_path):
    s, sid, tid = _vox(tmp_path)
    s = _rack(s, tid, {"kind": "lowpass", "params": {"cutoff": 600.0}})
    track = sm._track(s, tid)
    assert sm.lane_targets(track) == ("gain",)  # the unit has no id yet
    menu = sm.fx_lane_menu(track)
    assert menu[0][0] == "Lowpass" and menu[0][1][0] == (
        "fx:@0:lowpass:cutoff",
        "cutoff",
    )
    target = sm.claim_fx_target(track, "fx:@0:lowpass:cutoff")
    assert target in sm.lane_targets(track)
    sm.set_lane(s, sid, tid, target, RISE)
    sm.set_lane(s, sid, tid, "gain", [[0.0, 0.5], [1.0, 0.25]])
    out = schema.conform(json.loads(json.dumps(s)), N.SONG)
    assert out == s and schema.conform(out, N.SONG) == out
    assert out["sections"][0]["auto"][tid] == [
        {"target": target, "points": RISE},
        {"target": "gain", "points": [[0.0, 0.5], [1.0, 0.25]]},
    ]
    assert out["tracks"][0]["fx"][0]["id"] == target.split(":")[1]


def test_an_audio_rows_lanes_need_a_take_there_and_never_a_field(tmp_path):
    s, sid, tid = _vox(tmp_path)
    other = sm.add_section(s, "Break", 2)
    with pytest.raises(sm.SongError, match="no take in Break"):
        sm.set_lane(s, other, tid, "gain", RISE)
    with pytest.raises(sm.SongError, match="automate that"):
        sm.set_lane(s, sid, tid, "cutoff", RISE)  # a sound's field: none here
    sm.set_lane(s, sid, tid, "gain", [[0.0, 0.2], [1.0, 0.8]])
    # hostile: a field lane, a lane on a row with no take, garbage — dropped
    s["sections"][0]["auto"][tid].append({"target": "cutoff", "points": RISE})
    s["sections"][1]["auto"] = {tid: [{"target": "gain", "points": RISE}]}
    s["sections"][0]["auto"]["nosuch00"] = [{"target": "gain", "points": RISE}]
    s["tracks"][0]["fx"] = [
        {"kind": "lowpass", "params": {"cutoff": 1e9, "q": "x"}},
        {"kind": "nope"},
        "unit",
    ]
    out = schema.conform(s, N.SONG)
    assert out["sections"][0]["auto"] == {
        tid: [{"target": "gain", "points": [[0.0, 0.2], [1.0, 0.8]]}]
    }
    assert "auto" not in out["sections"][1]
    (lp,) = out["tracks"][0]["fx"]
    assert lp["kind"] == "lowpass" and lp["params"]["cutoff"] == 12000.0
    # its last take gone, the row's lanes go with it
    sm.remove_take(out, sid, tid, "loop", 0)
    sm.remove_take(out, sid, tid, "loop", 2)
    assert "auto" not in schema.conform(out, N.SONG)["sections"][0]


# --- the routes -----------------------------------------------------------------------


@pytest.fixture
def studio(tmp_path):
    """The song routes' fixture song with a take of noise on Vox ("ta") in
    Intro ("s1")."""
    with TestClient(create_app(data_dir=tmp_path)) as c:
        _noise(tmp_path)
        s = schema.conform(song_dict(), N.SONG)
        sm.place_take(s, "s1", "ta", "loop", 0)
        use_song(c, s)
        yield c


def _fx(client, **data):
    return post(client, "/song/track/ta/fx", {k: str(v) for k, v in data.items()})


def _chain(client, tid="ta") -> list[dict]:
    return sm._track(song(client), tid)["fx"]


def test_the_rack_is_edited_unit_by_unit_and_each_edit_is_one_step(studio):
    r = _fx(studio, op="add", kind="lowpass")
    assert r.status_code == 200, r.text[:300]
    assert [u["kind"] for u in _chain(studio)] == ["lowpass"]
    assert "s1" in changed(r) and "song-arranger" in oob_ids(r.text)
    _fx(studio, op="add", kind="reverb")
    # a knob's drag: many posts, one undo step
    for v in ("900", "700", "500"):
        assert _fx(studio, op="set", index=0, name="cutoff", value=v).status_code == 200
    assert _chain(studio)[0]["params"]["cutoff"] == 500.0
    _fx(studio, op="move", index=1, dir=-1)
    assert [u["kind"] for u in _chain(studio)] == ["reverb", "lowpass"]
    post(studio, "/song/undo")
    assert [u["kind"] for u in _chain(studio)] == ["lowpass", "reverb"]
    post(studio, "/song/undo")  # the whole drag
    assert _chain(studio)[0]["params"]["cutoff"] != 500.0
    _fx(studio, op="remove", index=1)
    assert [u["kind"] for u in _chain(studio)] == ["lowpass"]


def test_an_empty_racks_reverb_is_heard_at_once_and_only_for_a_real_section(studio):
    """The drawer's empty state ("+ Reverb"): the unit goes on and the
    section loops through it (`song-autoplay`); a section that is not the
    song's plays nothing, and the unit still goes on."""
    r = _fx(studio, op="add", kind="reverb", play=1, section_id="s1")
    assert json.loads(r.headers["HX-Trigger"])["song-autoplay"] == {"section": "s1"}
    assert [u["kind"] for u in _chain(studio)] == ["reverb"]
    r = _fx(studio, op="add", kind="lowpass", play=1, section_id="nope")
    assert "song-autoplay" not in json.loads(r.headers.get("HX-Trigger") or "{}")
    assert [u["kind"] for u in _chain(studio)] == ["reverb", "lowpass"]


@pytest.mark.parametrize(
    ("data", "word"),
    [
        ({"op": "add", "kind": "nope"}, "No such effect"),
        ({"op": "add"}, "No such effect"),
        ({"op": "remove", "index": "0"}, "no longer in the rack"),
        ({"op": "set", "index": "x", "name": "cutoff", "value": "1"}, "no longer"),
        ({"op": "boom"}, "No such rack edit"),
        ({}, "No such rack edit"),
    ],
)
def test_a_hostile_rack_post_is_a_toast_and_changes_nothing(studio, data, word):
    before = json.dumps(song(studio), sort_keys=True)
    refused(_fx(studio, **data), word)
    assert json.dumps(song(studio), sort_keys=True) == before


def test_the_rack_refuses_a_bad_setting_a_full_rack_and_a_synth_track(studio):
    _fx(studio, op="add", kind="lowpass")
    refused(_fx(studio, op="set", index=0, name="decay", value="1"), "no such")
    refused(_fx(studio, op="set", index=0, name="cutoff", value="nan"), "number")
    refused(_fx(studio, op="move", index=0, dir=1), "end of the rack")
    for _ in range(MAX_UNITS - 1):
        _fx(studio, op="add", kind="eq")
    refused(_fx(studio, op="add", kind="eq"), "full")
    # a synth track's rack is its sound's: the dock edits it
    refused(post(studio, "/song/track/t1/fx", {"op": "add", "kind": "eq"}), "dock")
    refused(post(studio, "/song/track/zz/fx", {"op": "add", "kind": "eq"}), "track")


def test_a_cutoff_lane_from_the_drawer_on_the_audio_row(studio):
    _fx(studio, op="add", kind="lowpass")
    r = post(studio, "/song/drawer", {"section_id": "s1", "track_id": "ta"})
    html = r.text
    assert "song-drawer" in oob_ids(html)
    drawer = html.split('id="song-drawer"')[1].split("</section>")[0]
    # the rack, and the lanes' menu: the level and the unit's parameters
    assert 'hx-post="/song/track/ta/fx"' in drawer and "Vox FX" in drawer
    assert 'value="fx:@0:lowpass:cutoff"' in drawer and 'value="gain"' in drawer
    assert "+ New" not in drawer  # no pattern tools on an audio row
    r = post(
        studio,
        "/song/cell/auto",
        {
            "section_id": "s1",
            "track_id": "ta",
            "op": "add",
            "target": "fx:@0:lowpass:cutoff",
        },
    )
    assert r.status_code == 200, r.text[:300]
    uid = _chain(studio)[0]["id"]
    (lane,) = song(studio)["sections"][0]["auto"]["ta"]
    assert lane["target"] == f"fx:{uid}:lowpass:cutoff"
    assert "song-drawer" in oob_ids(r.text) and "Lowpass cutoff" in r.text
    r = post(
        studio,
        "/song/cell/auto",
        {
            "section_id": "s1",
            "track_id": "ta",
            "op": "set",
            "target": lane["target"],
            "start": "0",
            "end": "1",
        },
    )
    assert r.status_code == 200 and "s1" in changed(r)
    # one Ctrl+Z takes the lane and the unit's id back together
    post(studio, "/song/undo")
    post(studio, "/song/undo")
    assert "auto" not in song(studio)["sections"][0]
    assert "id" not in _chain(studio)[0]
    # Verse has no take of Vox: its row offers no lane
    r = post(studio, "/song/drawer", {"section_id": "s2", "track_id": "ta"})
    drawer = r.text.split('id="song-drawer"')[1].split("</section>")[0]
    assert "lane-add" not in drawer and "no take of Vox" in drawer


def test_the_arranger_opens_an_audio_tracks_rack_and_marks_its_lanes(studio):
    page = studio.get("/view/song").text
    assert 'class="btn btn-s menu-btn track-fx"' in page  # the ⋯ menu
    assert 'class="btn btn-s menu-btn take-fx"' in page  # a take's menu
    sm.set_lane(song(studio), "s1", "ta", "gain", [[0, 0.2], [1, 0.6]])
    page = studio.get("/view/song").text
    cell = page.split('id="cell-s1-ta"')[1].split("</td>")[0]
    assert 'class="cell-auto"' in cell


def test_the_mix_views_eq_reaches_an_audio_track(studio):
    r = post(studio, "/song/mix/strip/ta", {"eq.low": "-6"})
    assert r.status_code == 200, r.text[:300]
    assert [u["kind"] for u in _chain(studio)] == ["eq"]
    assert _chain(studio)[0]["params"]["low"] == -6.0
    refused(post(studio, "/song/mix/strip/ta", {"pan": "0.5"}), "no pan")
