"""A released held key fades with the engine's release curve.

`releaseKey` faded a held WAV key's loop along a straight line, while the
same note in the loop render releases along `core.adsr`'s curve
(`1 - u ** (1 / env_curve)`) — the class of mismatch
`test_live_voice_parity.py` closed for the worklet. The fade now follows
that curve.

Why not crossfade into the render's own release instead? Measured on
"Velvet Line": the jump target just before the
gate sits at an arbitrary phase of the unison beating, so the first 150 ms
after key-up stepped 2.1 dB (median) / 6.2 dB (p90) per 10 ms, against
1.8 / 2.5 dB for a gain fade — the seam problem again, moved to key-up.
With the loop wrapping ~0.3x/s (holdloop.py), fading the loop is smoother.

The shape test runs keys.js's own `releaseCurve` under node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from synthlab.core import adsr

KEYS = Path("synthlab/studio/static/keys.js")
PIANO = Path("synthlab/studio/static/piano.js")

HARNESS = """
globalThis.window = { synthlab: {} };
globalThis.document = { addEventListener() {} };
const fs = require("fs");
// eval, deliberately: keys.js is a classic script (an IIFE publishing onto
// window.synthlab), not a module. It is the first-party file the browser
// loads, run here against the stub window above rather than reimplemented.
eval(fs.readFileSync(process.argv[2], "utf8"));
const spec = JSON.parse(process.argv[3]);
const out = spec.map((c) => Array.from(window.synthlab.releaseCurve(c.n, c.curve)));
process.stdout.write(JSON.stringify(out));
"""

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run keys.js's own code"
)


def release_curves(cases, tmp_path):
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    out = subprocess.run(
        ["node", str(harness), str(KEYS), json.dumps(cases)],
        capture_output=True, text=True, check=True, timeout=30,
    )  # fmt: skip
    return [np.array(c) for c in json.loads(out.stdout)]


@pytest.mark.parametrize("curve", [1.0, 2.0, 3.5])
def test_release_fade_matches_the_engine_release(curve, tmp_path):
    n = 200
    (got,) = release_curves([{"n": n, "curve": curve}], tmp_path)
    # core.adsr with sustain 1 and a 2-sample gate (every segment is at
    # least one sample): its tail is the pure release from 1.0 to 0.0, n
    # samples of it (the release's own length; `release=0` here relied on
    # every gated release spanning the rest of the buffer)
    env = adsr(n + 2, attack=0, decay=0, sustain=1.0, release=n / 44100,
               gate=2 / 44100, curve=curve)  # fmt: skip
    assert env[1] == 1.0
    want = env[2:]
    assert got[0] == pytest.approx(1.0) and got[-1] == pytest.approx(0.0)
    # setValueCurveAtTime takes a Float32Array: agreement to float32 precision
    assert np.max(np.abs(got - want)) < 1e-6


def test_release_key_schedules_the_curve():
    js = KEYS.read_text(encoding="utf-8")
    body = js[js.index("function releaseKey") :]
    body = body[: body.index("\n  }\n")]
    assert "setValueCurveAtTime(" in body and "releaseCurve(" in body
    assert "linearRampToValueAtTime(0, t + r)" not in body  # not a straight line
    # piano.js hands playKey the patch's env_curve with the hold spec
    assert "curve: patch.env_curve" in PIANO.read_text(encoding="utf-8")
