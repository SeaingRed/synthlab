"""GET /wavetables.bin: the engine's own tables, served once to the live
voice (studio/voicetables.py), and the mod matrix rule the worklet runs.

The file is data the worklet reads instead of numbers it would otherwise
retype — so it is checked against the engine itself, not against itself.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import core
from synthlab.params import MOD_DESTINATIONS, MOD_SLOTS, MOD_SOURCES, slot_active
from synthlab.studio import voicetables, worklet
from synthlab.studio.server import create_app
from synthlab.synth import Patch

WORKLET = Path("synthlab/studio/static/voice-worklet.js").resolve()


def _split(blob: bytes) -> tuple[dict, dict[str, np.ndarray]]:
    """The file as piano.js splits it: header JSON, then float32 arrays."""
    n = int.from_bytes(blob[:4], "little")
    meta = json.loads(blob[4 : 4 + n])
    data = np.frombuffer(blob[4 + n :], dtype="<f4")
    out, o = {}, 0
    for name, shape in meta["arrays"]:
        size = int(np.prod(shape))
        out[name] = data[o : o + size].reshape(shape)
        o += size
    assert o == len(data)
    return meta, out


def test_route_serves_the_tables_and_caches_only_the_hashed_url(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    url = client.get("/patch.json").json()["tables"]
    r = client.get(url)
    assert r.status_code == 200 and r.content == voicetables.payload()
    assert "immutable" in r.headers["cache-control"]
    assert r.headers["content-type"] == "application/octet-stream"
    assert client.get("/wavetables.bin").headers["cache-control"] == "no-cache"


def test_tables_are_the_engines_own():
    meta, arr = _split(voicetables.payload())
    assert meta["tables"] == list(core.WAVETABLE_NAMES)
    assert meta["mips"] == list(core._WT_MIPS)
    stack = np.stack([core._WT_STACK[n] for n in core.WAVETABLE_NAMES])
    np.testing.assert_allclose(arr["wt"], stack, atol=1e-6)
    np.testing.assert_allclose(arr["formant"], core.formant_makeup_table(), rtol=1e-6)
    assert meta["pink"]["poles"] == list(core.PINK_POLES)
    np.testing.assert_allclose(meta["vowel_f"], core._VOWEL_F)


def test_uniform_streams_are_the_prefix_pluck_and_sample_hold_read():
    """core.pluck draws uniform(-1, 1, length + 1) from its seed and
    core.sample_hold uniform(-1, 1, steps) from seed 0: both a prefix of the
    same stream, so the served prefix is exact for any length it covers."""
    meta, arr = _split(voicetables.payload())
    for i, seed in enumerate(meta["seeds"]):
        want = np.random.default_rng(seed).uniform(-1.0, 1.0, 300)
        np.testing.assert_allclose(arr["uniform"][i, :300], want, atol=1e-7)
    held = core.sample_hold(4.0, 50 * SR_STEP, core.SR)[::SR_STEP]
    np.testing.assert_allclose(arr["uniform"][0, :50], held[:50], atol=1e-7)
    # a pluck at 20 Hz (its lowest) needs floor(sr / 20) + 1 values at 48 kHz
    assert arr["uniform"].shape[1] >= 48000 // 20 + 1


SR_STEP = core.SR // 4  # sample_hold at 4 Hz steps every SR / 4 samples

HARNESS = """
globalThis.sampleRate = 44100;
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { onmessage: null, postMessage() {} }; }
};
let Voice = null;
globalThis.registerProcessor = (_n, c) => { Voice = c; };
const fs = require("fs");
eval(fs.readFileSync(process.argv[2], "utf8")); // the shipped file, under shims
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "sampler-worklet"), "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "grain-worklet"), "utf8"));
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = cases.map((p) => {
  const v = new Voice();
  v.port.onmessage({ data: { type: "params", p } });
  v.wire();
  return v.routes.map((r) => r.amt.replace("_amt", ""));
});
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the worklet's code")
def test_the_worklet_wires_exactly_the_slots_the_engine_applies(tmp_path):
    """voice-worklet.js decides which routes to run from the tables
    /patch.json sends; over a sweep of slot settings (unknown sources and
    targets, zero and tiny amounts) it must pick exactly the slots
    params.slot_active does."""
    rng = np.random.default_rng(0)
    srcs = [*MOD_SOURCES, "bogus"]
    dsts = [*MOD_DESTINATIONS, "nowhere"]
    amts = [0.0, 5e-4, -5e-4, 1e-3, -0.2, 0.7]
    patches, want = [], []
    for _ in range(300):
        kw = {}
        for s in MOD_SLOTS:
            kw[f"{s}_src"] = srcs[rng.integers(len(srcs))]
            kw[f"{s}_dst"] = dsts[rng.integers(len(dsts))]
            kw[f"{s}_amt"] = amts[rng.integers(len(amts))]
        p = Patch(**kw)
        patches.append({**p.to_json(), **worklet.voice_tables()})
        want.append([s for s in MOD_SLOTS if slot_active(p, s)])
    (tmp_path / "cases.json").write_text(json.dumps(patches), encoding="utf-8")
    (tmp_path / "h.cjs").write_text(HARNESS, encoding="utf-8")
    res = subprocess.run(
        ["node", str(tmp_path / "h.cjs"), str(WORKLET), str(tmp_path / "cases.json")],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert json.loads(res.stdout) == want
