"""The live vocal chain's server half (vocal.py, routes/vocal.py): the
styles are live chains, the panel draws the shared rack, a style loads
whole and stays editable, /voice/live.json resolves it as the live keys
would, and ⏺'s take lands as two clips — the processed one with its chain
in the meta, the dry one beside it — that "→ Song" places."""

from __future__ import annotations

import io
import wave

import numpy as np
from fastapi.testclient import TestClient

from synthlab.studio import cliplib, vocal
from synthlab.studio import voice as lab
from synthlab.studio.clips import list_clips
from synthlab.studio.fxlive import FX_LIVE
from synthlab.studio.fxrack import MAX_UNITS, clean_chain
from synthlab.studio.server import create_app
from tests.studio.test_song_routes import use_song


def _wav(seconds: float = 0.5, channels: int = 2) -> bytes:
    n = int(44100 * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(
            (np.sin(np.arange(n * channels) * 0.05) * 8000).astype("<i2").tobytes()
        )
    return buf.getvalue()


def test_every_style_is_a_clean_live_chain_of_techniques():
    for key, st in vocal.STYLES.items():
        assert 0 < len(st["chain"]) <= MAX_UNITS, key
        assert clean_chain(st["chain"]) == vocal.style(key)["chain"]
        assert all(FX_LIVE[u["kind"]] in ("js", "native") for u in st["chain"]), key
        assert st["label"] and st["title"]
    # the brief's six, by technique
    kinds = {k: {u["kind"] for u in s["chain"]} for k, s in vocal.STYLES.items()}
    assert {"doubler", "phaser", "tape_echo", "vox_comp", "plate"} <= kinds[
        "psych_haze"
    ]
    assert {"doubler", "rotary", "tape_echo", "plate"} <= kinds["sixties"]
    assert {"autotune", "harmony"} <= kinds["robot"]
    assert {"autotune", "vox_comp", "deess", "plate"} <= kinds["pop"]
    assert "telephone" in kinds["telephone"] and "harmony" in kinds["choir"]


def test_the_panel_draws_the_rack_and_a_style_loads_whole(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    html = client.get("/fragment/vocal").text
    assert (
        'id="vox-panel"' in html and 'id="vox-btn"' in html and 'id="vox-rec"' in html
    )
    assert (
        "Psych Haze vocal" in html and 'data-unit="vox_comp"' in html
    )  # the default: Clean
    r = client.post("/voice/live/style", data={"name": "robot"})
    assert r.status_code == 200 and 'data-unit="autotune"' in r.text
    live = lab.read_lab(tmp_path)["live"]
    assert live["style"] == "robot" and not vocal.edited(live)
    r = client.post(
        "/voice/live/fx",
        data={"op": "set", "index": "0", "name": "speed", "value": "0.5"},
    )
    assert r.status_code == 200 and "Robot / filter house vocal (edited)" in r.text
    assert client.post("/voice/live/style", data={"name": "nope"}).status_code == 422
    # the adder offers live units only
    assert (
        '<option value="compress">' not in html and '<option value="autotune">' in html
    )


def test_live_json_resolves_the_chain_on_the_session_key(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    client.post("/fragment/arrangement", data={"root": "D2", "scale_name": "minor"})
    client.post("/voice/live/style", data={"name": "pop"})
    j = client.get("/voice/live.json").json()
    tune = next(u for u in j["fx"] if u["kind"] == "autotune")
    assert tune["key"] == 2 and tune["scale"] == [0, 2, 3, 5, 7, 8, 10]
    assert (
        tune["grain"] > 0 and tune["track_lo"] > 0
    )  # the engine's constants ride along
    assert [u["kind"] for u in j["fx"]] == [
        u["kind"] for u in vocal.STYLES["pop"]["chain"]
    ]
    assert j["label"] == "Pop polish"


def test_a_take_lands_as_the_processed_clip_and_the_dry_one(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    client.post("/voice/live/style", data={"name": "sixties"})
    files = {
        "file": ("v.wav", _wav(), "audio/wav"),
        "dry": ("d.wav", _wav(), "audio/wav"),
    }
    r = client.post(
        "/take/vocal", data={"name": "verse", "bpm": "96", "bars": "2"}, files=files
    )
    assert r.status_code == 200, r.text
    assert r.headers["X-Clip-Name"] == "verse" and 'id="vox-panel"' in r.text
    clips = {c["name"]: c for c in list_clips(tmp_path)}
    wet, dry = clips["verse"], clips["verse dry"]
    assert wet["source"] == "vocal" and wet["dry"] == "verse dry"
    assert wet["vocal"]["style"] == "sixties" and wet["bpm"] == 96 and wet["bars"] == 2
    assert [u["kind"] for u in wet["vocal"]["chain"]] == [
        u["kind"] for u in vocal.STYLES["sixties"]["chain"]
    ]
    assert dry["source"] == "vocal dry" and dry["of"] == "verse"
    assert cliplib.kind_of(wet)[0] == cliplib.kind_of(dry)[0] == "voice"
    # the panel lists it, with → booth on its dry twin
    assert "→ booth" in r.text and "verse dry" in r.text
    # refused: one file, no tempo
    one = {"file": ("v.wav", _wav(), "audio/wav")}
    assert (
        client.post(
            "/take/vocal", data={"bpm": "96", "bars": "2"}, files=one
        ).status_code
        == 422
    )
    assert (
        client.post("/take/vocal", data={"bars": "2"}, files=files).status_code == 422
    )


def test_a_take_goes_to_the_song_and_its_dry_twin_to_the_booth(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    use_song(client)
    files = {
        "file": ("v.wav", _wav(), "audio/wav"),
        "dry": ("d.wav", _wav(), "audio/wav"),
    }
    client.post(
        "/take/vocal", data={"name": "hook", "bpm": "120", "bars": "1"}, files=files
    )
    r = client.post("/take/vocal/song", data={"name": "hook", "section_id": "s2"})
    assert r.status_code == 200
    state = client.app.state.studio
    assert any("hook" in str(sec) for sec in state.song["sections"])
    r = client.post("/voice/take/clip", data={"name": "hook dry", "booth": "1"})
    assert r.status_code == 200 and lab.read_lab(tmp_path)["take"]["name"] == "hook dry"
    assert client.post("/take/vocal/song", data={"name": "gone"}).status_code == 422
