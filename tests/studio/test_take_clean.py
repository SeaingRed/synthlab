"""Finger drums and "✦ Clean up" on the Song page: a drums
take recorded onto the drums track (`record.quantize_hits`/`merge_hits`
through `/song/pattern/{id}/record`), the take's raw events kept, and
`/song/take/clean` — `takeclean.clean` (the lean, a latency shift read
off the kick, doubles, velocities, the key) and Gemini's feedback and
edits (`takecoach`, a stand-in client here: no test reaches Gemini)."""

from __future__ import annotations

import json
import types

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import record, takeclean, takecoach
from synthlab.studio.agent.config import AgentConfig
from synthlab.studio.server import create_app

from .test_song_routes import HX, post, use_song

BPM = 120.0
UNIT = record.bar_seconds(BPM) / 16  # a 16th at 120 bpm: 125 ms


def _drums(bars=2) -> dict:
    return {"id": "p", "kind": "drums", "name": "x", "bars": bars, "steps": 16}


def _beat(late: float, bars: int = 2) -> list[dict]:
    """Four on the floor, a backbeat and 8th hats, every hit `late` of a
    16th behind the grid."""
    out = []
    for b in range(bars):
        for k in (0, 4, 8, 12):
            out.append({"voice": "kick", "t": (b * 16 + k + late) * UNIT, "vel": 0.9})
        for k in (4, 12):
            out.append({"voice": "snare", "t": (b * 16 + k + late) * UNIT, "vel": 0.8})
        for k in range(0, 16, 2):
            out.append({"voice": "hat", "t": (b * 16 + k + late) * UNIT, "vel": 0.5})
    return out


def _clean(raw, kind="drums", **kw) -> takeclean.Cleaned:
    base = kw.pop("base", _drums() if kind == "drums" else {
        "id": "n", "kind": "notes", "name": "n", "bars": 1, "steps_per_bar": 16,
        "cells": [], "velocities": {}, "cuts": [],
    })  # fmt: skip
    bars = base["bars"]
    return takeclean.clean(takeclean.Take(kind, raw, BPM, bars, 16, "1/16", base, **kw))


def _kicks(pattern) -> list[int]:
    return sorted(int(s) for v, s in pattern["drums"] if v == "kick")


# --- the pure half ----------------------------------------------------------------


def test_a_take_a_16th_late_lands_one_step_late_without_the_clean_up():
    """Why it exists: 60% of a 16th behind (75 ms at 120 bpm — a wireless
    headset's unreported latency) snaps every hit onto the NEXT step."""
    hits = record.quantize_hits(_beat(0.6), bpm=BPM, bars=2, steps=16)
    assert sorted(s for v, s, _ in hits if v == "kick") == [1, 5, 9, 13, 17, 21, 25, 29]
    c = _clean(_beat(0.6))
    assert _kicks(c.pattern) == [0, 4, 8, 12, 16, 20, 24, 28]
    assert "75 ms late" in c.fixes[0] and "latency" in c.fixes[0]


def test_a_lean_inside_half_a_step_is_moved_back_and_said():
    c = _clean(_beat(0.3))
    assert _kicks(c.pattern) == [0, 4, 8, 12, 16, 20, 24, 28]
    assert c.fixes[0].startswith("The take sat about 38 ms late")


def test_a_tight_take_is_left_alone_and_says_so():
    c = _clean(_beat(0.04))
    assert c.fixes == ["Already tight: 28 hits, all on the 1/16 grid."]


def test_a_flam_is_one_hit_and_overdubbed_passes_are_one_hit():
    raw = _beat(0.0, bars=1)
    raw.append({"voice": "snare", "t": (4 + 0.3) * UNIT, "vel": 0.3})  # a flam
    cyc = 2 * record.bar_seconds(BPM)  # a second pass over the same bar
    raw += [{"voice": "kick", "t": cyc + k * UNIT, "vel": 0.5} for k in (0, 4, 8, 12)]
    c = _clean(raw, base=_drums(1), cycle_bars=2)
    snares = [s for v, s in c.pattern["drums"] if v == "snare"]
    assert sorted(snares) == [4, 12]
    assert any("Merged 1 double hit" in f for f in c.fixes)
    assert _kicks(c.pattern) == [0, 4, 8, 12]  # both passes: one kick a step


def test_velocities_are_drawn_to_the_median_keeping_accents():
    vels = (1.0, 0.5, 0.5, 0.5, 1.0, 0.5, 0.05, 0.5)  # two accents, one ghost
    raw = [{"voice": "hat", "t": 2 * i * UNIT, "vel": v} for i, v in enumerate(vels)]
    c = _clean(raw, base=_drums(1))
    v = {int(k.split(".")[1]): d["v"] for k, d in c.pattern["hits"].items()}
    assert v[0] > v[2] and v[8] > v[2]  # the accents stay accents
    assert v[12] >= takeclean.VEL_FLOOR and v[12] > 0.05  # the ghost is tamed
    assert any("Evened out" in f for f in c.fixes)


def test_wrong_notes_move_into_the_key():
    raw = [
        {"midi": 61, "t": 0.0},
        {"midi": 67, "t": 4 * UNIT},
        {"midi": 70, "t": 8 * UNIT},
    ]
    c = _clean(raw, kind="notes", root="G1", scale_name="minor")
    assert sorted(c.pattern["cells"]) == [["A#4", 8], ["C4", 0], ["G4", 4]]
    assert any("Moved 1 note into G minor: C#4→C4" in f for f in c.fixes)


def test_gemini_edits_are_checked_before_they_apply():
    c = _clean(_beat(0.0))
    done = takeclean.apply_edits(
        c.pattern,
        "drums",
        [
            {"op": "add", "voice": "clap", "step": 4, "velocity": 0.7, "why": "lift"},
            {"op": "remove", "voice": "kick", "step": 3},  # not there
            {"op": "add", "voice": "cowbell", "step": 1},  # not a voice
            {"op": "add", "voice": "hat", "step": 99},  # past the pattern
            {"op": "velocity", "voice": "snare", "step": 12, "velocity": 1.0},
        ],
        steps=16,
        root="G1",
        scale_name="minor",
    )
    assert done == [
        "Added a clap at bar 1, step 5 — lift",
        "Set the snare at bar 1, step 13 to 100%",
    ]
    assert ["clap", 4] in c.pattern["drums"] and c.pattern["hits"]["snare.12"][
        "v"
    ] == 1.0


# --- the routes ---------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        yield c


def _take(client, raw, take="tk1", t1=None, section="s1", pid="pd", **form):
    data = {
        "events": json.dumps(raw),
        "take": take,
        "t0": "0",
        "t1": str(t1 if t1 is not None else 2 * record.bar_seconds(BPM)),
        "section_id": section,
        "track_id": "td",
        **form,
    }
    return client.post(f"/song/pattern/{pid}/record", data=data, headers=HX)


def test_the_drums_track_records_hits_with_their_own_velocity(client):
    s = client.app.state.studio
    raw = [
        {"voice": "snare", "t": 4 * UNIT, "vel": 0.6},
        {"voice": "hat", "t": 2 * UNIT},
    ]
    r = _take(client, raw)
    assert r.status_code == 200
    p = s.song["patterns"]["pd"]
    assert ["snare", 4] in p["drums"] and ["hat", 2] in p["drums"]
    assert p["hits"]["snare.4"]["v"] == 0.6
    assert [e["voice"] for e in s.take_history[-1]["raw"]] == ["snare", "hat"]
    # a note-on is not a drum hit
    bad = _take(client, [{"midi": 40, "t": 0.1}], take="tk2")
    assert bad.status_code == 422


def test_an_empty_drums_cell_gets_a_drums_take_pattern(client):
    s = client.app.state.studio
    s.song["sections"][0]["cells"].pop("td")
    r = _take(client, [{"voice": "kick", "t": 0.0}], pid="new")
    assert r.status_code == 200
    pid = s.song["sections"][0]["cells"]["td"]
    assert s.song["patterns"][pid]["kind"] == "drums"
    assert ["kick", 0] in s.song["patterns"][pid]["drums"]


def test_clean_up_rewrites_the_take_as_one_undo_step(client):
    s = client.app.state.studio
    before = json.loads(json.dumps(s.song["patterns"]["pd"]))  # kick 0, snare 8
    _take(client, _beat(0.6, bars=1), t1=record.bar_seconds(BPM))
    assert ["kick", 1] in s.song["patterns"]["pd"]["drums"]  # recorded late
    r = client.post("/song/take/clean", data={"ai": "0"}, headers=HX)
    assert r.status_code == 200
    report = json.loads(r.headers["HX-Trigger"])["take-cleaned"]
    assert "75 ms late" in report["fixes"][0] and report["ai"] == "off"
    p = s.song["patterns"]["pd"]
    assert _kicks(p) == [0, 4, 8, 12] and ["snare", 8] in p["drums"]  # the old hit kept
    # Ctrl+Z gives the take back as recorded; once more, the pattern before it
    client.post("/song/undo", headers=HX)
    assert ["kick", 1] in s.song["patterns"]["pd"]["drums"]
    client.post("/song/undo", headers=HX)
    assert s.song["patterns"]["pd"]["drums"] == before["drums"]


def test_clean_up_refuses_a_pattern_edited_since_the_take(client):
    s = client.app.state.studio
    _take(client, _beat(0.0, bars=1))
    post(client, "/song/pattern/pd/rename", {"name": "Edited"})
    r = client.post("/song/take/clean", data={"ai": "0"}, headers=HX)
    assert r.status_code == 422 and "edited since" in r.text
    assert s.song["patterns"]["pd"]["name"] == "Edited"


def test_without_a_key_clean_up_is_deterministic_and_says_why(client):
    _take(client, _beat(0.0, bars=1))
    r = client.post("/song/take/clean", headers=HX)  # ai defaults on
    report = json.loads(r.headers["HX-Trigger"])["take-cleaned"]
    assert report["ai"] and "GEMINI_API_KEY" in report["ai"] and not report["feedback"]


class _FakeGemini:
    """Stands in for `genai.Client`: answers one JSON reply, and keeps
    what it was asked."""

    def __init__(self, reply: dict | str):
        self.asked: dict = {}
        text = reply if isinstance(reply, str) else json.dumps(reply)

        def generate_content(**kw):
            self.asked = kw
            usage = types.SimpleNamespace(
                prompt_token_count=900, candidates_token_count=120
            )
            return types.SimpleNamespace(text=text, usage_metadata=usage)

        self.models = types.SimpleNamespace(generate_content=generate_content)


def test_gemini_feedback_and_edits_ride_the_clean_up(client, monkeypatch):
    s = client.app.state.studio
    fake = _FakeGemini({
        "feedback": ["Solid four on the floor — the hats breathe nicely."],
        "edits": [{"op": "add", "voice": "clap", "step": 12, "velocity": 0.8,
                   "why": "double the backbeat"}],
    })  # fmt: skip
    monkeypatch.setattr(takecoach, "client_for", lambda config: fake)
    monkeypatch.setattr(takecoach, "sdk_available", lambda: True)
    monkeypatch.setattr(
        takecoach, "load_config", lambda: AgentConfig(api_key="k-secret", source="env")
    )
    _take(client, _beat(0.0, bars=1))
    r = client.post("/song/take/clean", headers=HX)
    report = json.loads(r.headers["HX-Trigger"])["take-cleaned"]
    assert report["feedback"] == ["Solid four on the floor — the hats breathe nicely."]
    assert report["edits"] == ["Added a clap at bar 1, step 13 — double the backbeat"]
    assert report["tokens"] == 1020 and report["ai"] == ""
    assert ["clap", 12] in s.song["patterns"]["pd"]["drums"]
    asked = fake.asked["contents"]
    assert "120 bpm, 4/4, key A minor" in asked and "kick   8...8...8...8..." in asked
    assert "k-secret" not in asked


def test_a_broken_gemini_answer_still_cleans_and_says_so(client, monkeypatch):
    fake = _FakeGemini("not json k-secret")
    monkeypatch.setattr(takecoach, "client_for", lambda config: fake)
    monkeypatch.setattr(takecoach, "sdk_available", lambda: True)
    monkeypatch.setattr(
        takecoach, "load_config", lambda: AgentConfig(api_key="k-secret", source="env")
    )
    _take(client, _beat(0.6, bars=1))
    r = client.post("/song/take/clean", headers=HX)
    report = json.loads(r.headers["HX-Trigger"])["take-cleaned"]
    assert "Gemini" in report["ai"] and "k-secret" not in report["ai"]
    assert "75 ms late" in report["fixes"][0]


def test_the_finger_drums_play_the_drums_tracks_own_kit(client):
    s = client.app.state.studio
    track = next(t for t in s.song["tracks"] if t["kind"] == "drums")
    track["drum_params"] = {"kick": {"tune": 80.0}}
    a = client.get("/song/track/td/hit/kick.wav")
    assert a.status_code == 200 and a.content[:4] == b"RIFF"
    loop = client.get("/drum/kick/hit.wav")
    assert a.content != loop.content  # another kit than the loop's
    assert client.get("/song/track/td/hit/cowbell.wav").status_code == 404
    assert client.get("/song/track/t1/hit/kick.wav").status_code == 404  # a synth track
