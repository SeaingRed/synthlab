"""The Song page's "Add riser / impact" (a section's ⋯ menu,
`POST /song/section/{id}/transition`): the generator renders at the song's
bpm into the clip store and the clip is placed as a take — a lead-in (up)
ending on the section's last bar line, anything else from its first bar.
The export plays it exactly as the plan does."""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import transitions
from synthlab.audio import load as load_wav
from synthlab.core import SR
from synthlab.studio.clips import clip_dir
from synthlab.studio.server import create_app
from tests.studio.test_song_export import _assemble, _pcm
from tests.studio.test_song_routes import HX, song_dict, use_song

RISER = {
    "kind": "riser",
    "direction": "up",
    "bars": "1",
    "brightness": "0.7",
    "tail": "0.2",
}


def make(tmp_path, song=None) -> TestClient:
    c = TestClient(create_app(data_dir=tmp_path))
    use_song(c, song)
    return c


def takes(client, sid="s1") -> list[dict]:
    s = client.app.state.studio.song
    return next(x for x in s["sections"] if x["id"] == sid)["audio"]


def test_a_riser_lands_in_the_clip_store_and_ends_on_the_downbeat(tmp_path):
    client = make(tmp_path)
    r = client.post(
        "/song/section/s1/transition", data=RISER | {"track_id": "ta"}, headers=HX
    )
    assert r.status_code == 200, r.text[:300]
    trig = json.loads(r.headers["HX-Trigger"])
    assert trig["song-changed"]["sections"][0] == "s1"
    [take] = takes(client)
    assert take["track_id"] == "ta" and take["at_bar"] == 1  # 2-bar section
    path = clip_dir(tmp_path) / f"{take['clip']}.wav"
    audio, sr = load_wav(str(path))
    assert sr == SR and len(audio) == 2 * SR  # one bar at 120 bpm
    expect = transitions.riser(2.0, "up", 0.7, 0.2)
    assert np.max(np.abs(audio - expect)) < 2 / 32768  # 16-bit
    meta = json.loads(path.with_name(f"{take['clip']}.meta.json").read_text())
    assert meta["source"] == "transition" and meta["transition"]["bars"] == 1


def test_the_same_settings_place_the_same_clip_again(tmp_path):
    client = make(tmp_path)
    for sid in ("s1", "s2"):
        r = client.post(f"/song/section/{sid}/transition", data=RISER, headers=HX)
        assert r.status_code == 200
    assert takes(client, "s1")[0]["clip"] == takes(client, "s2")[0]["clip"]
    assert len(list(clip_dir(tmp_path).glob("*.wav"))) == 1


def test_an_impact_starts_the_section_and_a_new_fx_track_appears(tmp_path):
    song = song_dict()
    song["tracks"] = [t for t in song["tracks"] if t["kind"] != "audio"]
    client = make(tmp_path, song)
    data = {"kind": "impact", "direction": "down", "bars": "8", "tail": "0.8"}
    r = client.post("/song/section/s2/transition", data=data, headers=HX)
    assert r.status_code == 200, r.text[:300]
    s = client.app.state.studio.song
    fx = [t for t in s["tracks"] if t["kind"] == "audio"]
    assert [t["name"] for t in fx] == ["FX"]
    [take] = takes(client, "s2")
    assert take["at_bar"] == 0 and take["track_id"] == fx[0]["id"]
    # 8 bars asked, the section has 2: cut to fit
    audio, _ = load_wav(str(clip_dir(tmp_path) / f"{take['clip']}.wav"))
    assert len(audio) == 4 * SR


def test_refusals_change_nothing(tmp_path):
    client = make(tmp_path)
    before = json.dumps(client.app.state.studio.song, sort_keys=True)
    bad = [
        ("s1", {"kind": "laser", "direction": "up"}),
        ("s1", {"kind": "riser", "direction": "sideways"}),
        ("s1", RISER | {"brightness": "nan"}),
        ("s1", RISER | {"track_id": "t1"}),  # a synth track
        ("nope", RISER),
    ]
    for sid, data in bad:
        r = client.post(f"/song/section/{sid}/transition", data=data, headers=HX)
        assert r.status_code == 422, (sid, data)
    assert json.dumps(client.app.state.studio.song, sort_keys=True) == before
    assert not list(clip_dir(tmp_path).glob("*.wav"))


@pytest.mark.parametrize(
    ("kind", "direction", "at_bar"),
    [
        ("sweep", "up", 1),  # an uplifter ends on the bar line
        ("sweep", "down", 0),  # a downlifter starts on it
        ("swell", "up", 1),
        ("swell", "down", 0),
        ("subdrop", "down", 0),
        ("subdrop", "up", 1),
        ("bed", "down", 0),
        ("bed", "up", 0),  # a bed has no direction: it starts the section
    ],
)
def test_the_four_new_kinds_are_takes_like_the_first_two(
    tmp_path, kind, direction, at_bar
):
    client = make(tmp_path)
    data = {"kind": kind, "direction": direction, "bars": "1", "track_id": "ta"}
    r = client.post("/song/section/s1/transition", data=data, headers=HX)
    assert r.status_code == 200, r.text[:300]
    [take] = takes(client)
    assert take["at_bar"] == at_bar
    path = clip_dir(tmp_path) / f"{take['clip']}.wav"
    audio, sr = load_wav(str(path))
    assert sr == SR and len(audio) == 2 * SR  # one bar at 120 bpm
    spec = json.loads(path.with_name(f"{take['clip']}.meta.json").read_text())
    spec = spec["transition"]
    assert spec["kind"] == kind
    expect = transitions.generate(
        kind, 2.0, spec["direction"], spec["brightness"], spec["tail"]
    )
    assert np.max(np.abs(audio - expect)) < 2 / 32768
    # the same settings again: the same clip, nothing rendered twice
    r = client.post("/song/section/s2/transition", data=data, headers=HX)
    assert r.status_code == 200
    assert takes(client, "s2")[0]["clip"] == take["clip"]
    assert len(list(clip_dir(tmp_path).glob("*.wav"))) == 1


def test_a_bed_is_one_clip_whichever_direction_is_posted(tmp_path):
    client = make(tmp_path)
    for sid, direction in (("s1", "up"), ("s2", "down")):
        data = {"kind": "bed", "direction": direction, "bars": "2"}
        r = client.post(f"/song/section/{sid}/transition", data=data, headers=HX)
        assert r.status_code == 200
    assert takes(client, "s1")[0]["clip"] == takes(client, "s2")[0]["clip"]
    [wav] = clip_dir(tmp_path).glob("*.wav")
    audio, _ = load_wav(str(wav))
    assert np.max(np.abs(audio)) == pytest.approx(10 ** (-18 / 20), abs=2 / 32768)


def test_a_kind_posted_without_a_direction_takes_its_own(tmp_path):
    """No direction in the form: a riser rises into the bar line, an impact
    comes off it — the way each name reads."""
    client = make(tmp_path)
    for kind, at_bar in (("riser", 1), ("impact", 0), ("swell", 1), ("subdrop", 0)):
        data = {"kind": kind, "bars": "1", "track_id": "ta"}
        r = client.post("/song/section/s1/transition", data=data, headers=HX)
        assert r.status_code == 200, kind
        take = takes(client)[-1]
        assert take["at_bar"] == at_bar, kind
        assert f"-{transitions.DEFAULT_DIRECTION[kind]}-" in take["clip"]


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "sweep", "direction": "sideways"},
        {"kind": "bed", "direction": "<script>"},
        {"kind": "swell\x00", "direction": "up"},
        {"kind": "", "direction": "up"},
        {"kind": "SWEEP", "direction": "up"},
        {"kind": "subdrop", "direction": "down", "tail": "inf"},
        {"kind": "subdrop", "direction": "down", "bars": "many"},
        {"kind": "bed", "direction": "down", "brightness": "1e999"},
    ],
)
def test_hostile_settings_for_the_new_kinds_are_refused_whole(tmp_path, data):
    client = make(tmp_path)
    before = json.dumps(client.app.state.studio.song, sort_keys=True)
    r = client.post("/song/section/s1/transition", data=data, headers=HX)
    assert r.status_code == 422, data
    assert json.dumps(client.app.state.studio.song, sort_keys=True) == before
    assert not list(clip_dir(tmp_path).glob("*.wav"))


def test_wild_numbers_are_clamped_into_the_clip_name(tmp_path):
    client = make(tmp_path)
    data = {"kind": "sweep", "direction": "up", "brightness": "9", "tail": "-4"}
    r = client.post(
        "/song/section/s1/transition", data=data | {"bars": "64"}, headers=HX
    )
    assert r.status_code == 200
    assert takes(client)[0]["clip"] == "sweep-up-2bar-b100-t0-120bpm"


def test_the_export_plays_the_riser_as_the_plan_does(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        use_song(client)
        for sid, kind in (("s1", "riser"), ("s2", "impact")):
            data = {"kind": kind, "direction": "up" if kind == "riser" else "down"}
            r = client.post(
                f"/song/section/{sid}/transition",
                data=data | {"bars": "2", "track_id": "ta"},
                headers=HX,
            )
            assert r.status_code == 200
        plan = client.get("/song/plan.json").json()
        played = _assemble(client, plan)
        exported = _pcm(client.get("/export/song.wav").content)
        assert exported.shape == played.shape
        assert np.max(np.abs(exported - played)) <= 1
        # the riser is in the intro: its last half-bar is loud
        n = plan["sections"][0]["cycle_samples"]
        assert np.max(np.abs(exported[n - SR // 4 : n])) > 3000
