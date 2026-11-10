"""Song mode: scene capture/load, sequencing, exports."""

import io
import time
import wave
import zipfile
from dataclasses import replace
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab.studio.clips import save_clip
from synthlab.studio.fxrack import clean_chain
from synthlab.studio.server import create_app
from synthlab.studio.state import StudioState
from tests.studio.song_helpers import load_section


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_capture_load_roundtrip(tmp_path):
    s = StudioState(tmp_path)
    s.patch = s.patch.tweak(cutoff=555.0)
    sid = s.loop_to_section("intro")
    s.patch = s.patch.tweak(cutoff=999.0)
    s.fx_master = [{"kind": "reverb", "params": {}}]
    assert load_section(s, 0) and s.song["sections"][0]["id"] == sid
    assert s.patch.cutoff == 555.0
    # v3: the master FX are the session's (run once over the whole song),
    # not frozen per section as scenes froze them
    assert s.fx_master == [{"kind": "reverb", "params": {}}]
    assert not load_section(s, 99)


def _repeat(client, index=0):
    sid = client.app.state.studio.song["sections"][index]["id"]
    return client.post(f"/song/section/{sid}/repeats", data={"delta": "1"})


def test_song_endpoints(tmp_path):
    """The capture -> repeat -> open -> delete story on the Song page's
    routes (Perform has no /fragment/song/* routes of its own)."""
    client = make_client(tmp_path)
    s = client.app.state.studio
    assert client.post("/song/from-loop", data={"name": "drop"}).status_code == 200
    assert [x["name"] for x in s.song["sections"]] == ["drop"]
    sid = s.song["sections"][0]["id"]
    assert _repeat(client).status_code == 200
    assert s.song["sections"][0]["repeats"] == 2
    r = client.post(f"/song/section/{sid}/to-loop")
    assert r.status_code == 200 and "HX-Location" in r.headers
    assert client.post(f"/song/section/{sid}/delete").status_code == 200
    assert s.song["sections"] == []


def test_export_song_wav(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/export/song.wav").status_code == 404  # empty song
    client.post("/song/from-loop", data={})
    _repeat(client)
    r = client.get("/export/song.wav")
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF"
    with wave.open(io.BytesIO(r.content)) as w:
        seconds = w.getnframes() / w.getframerate()
    # default arrangement: 1 bar at 124 bpm ~= 1.935s, x2 repeats
    assert abs(seconds - 2 * (4 * 60 / 124)) < 0.01


def test_export_song_rejects_long_song_before_rendering(tmp_path, monkeypatch):
    """/export/song.wav used to render every entry before checking
    MAX_SONG_SECONDS, so rejecting an over-long song took as long as
    rendering it (~29s). The length comes from the song's numbers alone,
    checked before any section renders (song_play.check_length)."""
    from synthlab.studio import render

    client = make_client(tmp_path)
    s = client.app.state.studio
    # 4 bars @ 40bpm = 24s/loop; an 8-bar section 16 times (the max) is
    # 768s > the 480s cap
    s.arrangement = replace(s.arrangement, bpm=40.0, bars=4, drum_bars=4)
    client.post("/song/from-loop", data={"name": "long"})
    # the repeats route refuses an over-cap edit, so set it the way an
    # over-long import arrives
    s.song["sections"][0]["repeats"] = 16
    s.song["sections"][0]["bars"] = 8

    def boom(*a, **kw):
        raise AssertionError("nothing may render once the cap is exceeded")

    monkeypatch.setattr(render, "build_song", boom)
    t0 = time.perf_counter()
    r = client.get("/export/song.wav")
    elapsed = time.perf_counter() - t0
    assert r.status_code == 422
    assert "toast" in r.text
    assert elapsed < 1.0


def test_export_stems_zip(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/export/stems.zip").status_code == 404  # empty song
    client.post("/song/from-loop", data={})
    r = client.get("/export/stems.zip")
    assert r.status_code == 200  # the default loop has a main lane
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = z.namelist()
        assert "synth-1.wav" in names
        assert z.read("synth-1.wav")[:4] == b"RIFF"


def test_export_stems_zip_names_are_fixed_not_patch_derived(tmp_path):
    """Stem entry names are fixed by track kind and index (`drums`,
    `synth-N`, `audio-N`; Ruling 6's spirit) — never text pulled from a
    patch or track name. A patch named `../../evil/x` used to flow straight
    into the zip entry name via `layer-0-../../evil/x.wav`; sanitizing that
    name is moot once no user text reaches an entry name at all."""
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        s.layers[1] = {
            "patch": {"name": "../../x"},
            "cells": [["A1", 0]],
            "muted": False,
            "gain": 1.0,
        }
        s.layers[2] = {
            "patch": {"name": "A / B"},
            "cells": [["C2", 0]],
            "muted": False,
            "gain": 1.0,
        }
        s.arrangement.drums = [["kick", 0], ["kick", 4]]
        save_clip(tmp_path, "riser", 0.1 * np.ones((4410, 2)))
        s.arrangement.clips = [{"clip": "riser", "at_bar": 0, "gain": 1.0}]
        s.loop_to_section("all lanes")
        r = client.get("/export/stems.zip")
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names = z.namelist()
        assert sorted(names) == [
            "audio-1.wav",
            "drums.wav",
            "synth-1.wav",
            "synth-2.wav",
            "synth-3.wav",
        ]


def test_scene_captures_and_exports_drum_bus_fx(tmp_path):
    """Scenes must freeze fx_drums or /export/song.wav renders the drum bus
    dry — audibly different from what the studio played."""
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        s.fx_drums = [{"kind": "bitcrush", "params": {"bits": 6.0, "mix": 1.0}}]
        s.arrangement.drums = [["kick", 0], ["kick", 4], ["kick", 8], ["kick", 12]]
        s.loop_to_section("with drum fx")
        drums = next(t for t in s.song["tracks"] if t["kind"] == "drums")
        assert drums["fx"] == clean_chain(s.fx_drums)  # v3: the drums track's FX
        r = client.get("/export/song.wav")
        assert r.status_code == 200
