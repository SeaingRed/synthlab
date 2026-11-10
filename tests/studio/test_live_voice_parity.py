"""What you play must be what you hear.

The live AudioWorklet voice and the rendered engine are two separate
implementations of the same patch, and they had drifted: the worklet's
decay and release were straight lines while `core.adsr` shapes both by
`env_curve`. `workletCanPlay` let any patch near the 2.0 default through,
so pressing a key gave a different envelope from the loop playing the same
sound — the exact failure the whole worklet gate exists to prevent.

These tests run the worklet's own code under node rather than grepping it,
so a formula that merely *looks* right still has to produce the right
numbers.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from synthlab.core import SR, adsr

WORKLET = Path("synthlab/studio/static/voice-worklet.js")
PIANO = Path("synthlab/studio/static/piano.js")

HARNESS = """
globalThis.sampleRate = 44100;
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { onmessage: null, postMessage() {} }; }
};
let Voice = null;
globalThis.registerProcessor = (_name, cls) => { Voice = cls; };
const fs = require("fs");
// eval, deliberately: voice-worklet.js is a classic AudioWorklet script, not
// a module, so there is nothing to require(). It is a first-party file from
// this repo, read from a path the test supplies, and it runs under the shims
// above rather than against a real audio context. Testing the shipped file
// itself is the point — a reimplementation here could agree with the server
// while the worklet the browser loads does not.
eval(fs.readFileSync(process.argv[2], "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "sampler-worklet"), "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "grain-worklet"), "utf8"));
const v = new Voice();
const spec = JSON.parse(process.argv[3]);
const out = spec.times.map((t) =>
  v.env(t, spec.attack, spec.decay, spec.sustain, spec.curve)
);
process.stdout.write(JSON.stringify(out));
"""

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the worklet's own code"
)


def worklet_env(times, attack, decay, sustain, curve, tmp_path):
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    spec = json.dumps(
        {
            "times": list(times),
            "attack": attack,
            "decay": decay,
            "sustain": sustain,
            "curve": curve,
        }
    )
    res = subprocess.run(
        ["node", str(harness), str(WORKLET.resolve()), spec],
        capture_output=True,
        text=True,
        check=True,
    )
    return np.array(json.loads(res.stdout), dtype=float)


@pytest.mark.parametrize("curve", [1.0, 2.0, 3.5])
def test_the_live_voice_decays_on_the_same_curve_as_the_render(curve, tmp_path):
    """core.adsr shapes the decay as (t/d) ** (1/curve); the worklet drew a
    straight line, which is only correct at curve == 1."""
    attack, decay, sustain = 0.02, 0.4, 0.3
    times = np.linspace(attack, attack + decay, 9)[1:-1]
    got = worklet_env(times, attack, decay, sustain, curve, tmp_path)

    n = SR
    rendered = adsr(n, attack, decay, sustain, 0.2, gate=10.0, curve=curve)
    want = rendered[(times * SR).astype(int)]
    np.testing.assert_allclose(got, want, atol=0.02)


def test_piano_sends_the_envelope_curve_to_the_worklet(tmp_path):
    """The worklet cannot honour a curve it is never told about."""
    # piano.js sends the whole patch on noteOn, livefx.js every layer's in
    # its "params" block; the worklet reads env_curve off it by its Patch name
    piano = PIANO.read_text(encoding="utf-8")
    livefx = Path("synthlab/studio/static/livefx.js").read_text(encoding="utf-8")
    assert (
        '{ type: "params", p: lp }' in livefx and "p: patch, hold: holdMode };" in piano
    )
    assert "T.env_curve" in WORKLET.read_text(encoding="utf-8")


def test_the_worklet_gate_no_longer_rejects_a_shaped_envelope():
    """Now that the curve is reproduced, holding patches back for it would
    push them onto the WAV fallback for no reason."""
    js = PIANO.read_text(encoding="utf-8")
    assert "env_curve" not in js.split("function workletCanPlay")[1].split("}")[0]
