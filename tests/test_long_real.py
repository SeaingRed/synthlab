"""Opt-in: a real song's whole vocal stem under a Song, end to end, with the
real separation model (long audio).

    SYNTHLAB_STEMS_MODEL_DIR=<dir with the model>  SYNTHLAB_TEST_AUDIO=<dir
    with an .mp3>  uv run pytest tests/test_long_real.py -q -s

Import -> separate -> place the whole vocal stem at the stem's tempo ->
export. Proves, on real audio: the export is the stem **sample for sample**
from the bar it was placed on (to the export's 16-bit rounding), through 80
passes of five sections — no seam shifts or drops a sample — and the song
took the Lab's tempo. Prints the export's time. Skipped without both
variables (`tests/test_lab_real.py`'s). The audio never enters the repo.
"""

from __future__ import annotations

import io
import os
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import stems
from synthlab.core import SR

MODEL_DIR = os.environ.get("SYNTHLAB_STEMS_MODEL_DIR", "")
AUDIO_DIR = os.environ.get("SYNTHLAB_TEST_AUDIO", "")
pytestmark = pytest.mark.skipif(
    not (MODEL_DIR and AUDIO_DIR),
    reason="set SYNTHLAB_STEMS_MODEL_DIR and SYNTHLAB_TEST_AUDIO",
)
HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/song"}


def test_a_real_vocal_stem_exports_sample_aligned_under_a_song(tmp_path):
    from synthlab.studio import lab, limits, sources
    from synthlab.studio import song_model as sm
    from synthlab.studio.labjobs import LabJobs
    from synthlab.studio.server import create_app

    full = next(
        p for p in sorted(Path(AUDIO_DIR).glob("*.mp3")) if "NoVox" not in p.name
    )
    meta = sources.add(tmp_path, full.read_bytes(), full.name, analyse=lab.analyse)
    jobs = LabJobs(tmp_path)
    jobs.model_dir = Path(MODEL_DIR)
    assert jobs.start(meta["id"], stems.HTDEMUCS.id) == ""
    jobs.join(1800)
    assert jobs.last and jobs.last["error"] == "", jobs.last
    meta = sources.read_meta(tmp_path, meta["id"])
    grid = lab.grid(meta)
    assert grid["set"]

    with TestClient(create_app(data_dir=tmp_path)) as client:
        for i in range(5):  # 80 bars of empty sections: 80 one-bar passes
            data = {"name": f"S{i + 1}", "bars": "16"}
            assert (
                client.post("/song/section/add", data=data, headers=HX).status_code
                == 200
            )
        add = {"op": "add", "source": meta["id"], "model": stems.HTDEMUCS.id}
        r = client.post(
            "/song/long", data=add | {"stems": "vocals", "tempo": "on"}, headers=HX
        )
        assert r.status_code == 200, r.text
        song = client.app.state.studio.song
        assert song["bpm"] == pytest.approx(grid["bpm"], abs=0.001)
        assert sm.song_seconds(song) <= limits.MAX_SONG_SECONDS
        plan = client.get("/song/plan.json").json()
        assert sum(len(s.get("pass_urls", ())) for s in plan["sections"]) >= 78

        t0 = time.perf_counter()
        r = client.get("/export/song.wav")
        took = time.perf_counter() - t0
        assert r.status_code == 200
        with wave.open(io.BytesIO(r.content)) as w:
            assert w.getframerate() == SR and w.getnchannels() == 2
            raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        out = raw.reshape(-1, 2) / 32767.0
        print(f"\nexport of {len(out) / SR:.0f} s under a whole stem: {took:.1f} s")

    item = song["long"][0]
    stem = sources.read(tmp_path, meta, "vocals").astype(np.float64)
    s0 = int(round(item["start"] * SR))  # the source sample on the bar line
    lead = max(0, -s0)  # a bar line a hair before the audio: that much silence
    n = min(len(out) - lead, len(stem) - max(0, s0))
    got, want = out[lead : lead + n], stem[max(0, s0) : max(0, s0) + n]
    fade = int(limits.LONG_FADE_SECONDS * SR)
    gain = plan["gain"]
    err = np.abs(got[fade : n - fade] - want[fade : n - fade] * gain)
    assert err.max() <= 1.0 / 32767 + 1e-9  # the export's own 16-bit rounding
    # and not by accident of level: one sample either way is far off
    for lag in (-1, 1):
        a, b = got[SR : n - SR], want[SR + lag : n - SR + lag] * gain
        assert np.abs(a - b).max() > 100 * err.max()
