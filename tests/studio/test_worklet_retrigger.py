"""Hitting the same key again must not make the note lurch or thump.

"Interstellar Whoop" (a sine lead, 2.2 s release) on the live worklet:
re-hitting a key while its tail still rang started a *second* voice on the
same pitch at phase 0, beside the old one. Two same-frequency sines add or
cancel by their phase offset — random per hit — so each repeat snapped the
level up or down: recorded in the browser at the master, 0.19 -> 0.09 in
one 20 ms frame, then back up to 0.35. Worst on low keys, where the offset
is a large fraction of a cycle. Heard as clipping / thumping.

Now a re-hit on the same key and pitch revives the ringing voice — same
phase and filter state, envelope restarting from its current level — so
there is never a second copy to interfere with. These tests run
voice-worklet.js's own code under node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from synthlab.studio.factory import get_factory

WORKLET = Path("synthlab/studio/static/voice-worklet.js")
SR = 48000

HARNESS = """
globalThis.sampleRate = 48000;
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { onmessage: null, postMessage() {} }; }
};
let Voice = null;
globalThis.registerProcessor = (_name, cls) => { Voice = cls; };
const fs = require("fs");
// eval, deliberately: voice-worklet.js is a classic AudioWorklet script, not
// a module. It is the first-party file the browser loads, run here under
// the shims above rather than reimplemented (see test_live_voice_parity.py).
eval(fs.readFileSync(process.argv[2], "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "sampler-worklet"), "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "grain-worklet"), "utf8"));
const spec = JSON.parse(process.argv[3]);
const v = new Voice();
const N = Math.round(spec.seconds * sampleRate), out = new Float32Array(N);
const ev = spec.events.sort((a, b) => a[0] - b[0]);
let e = 0, maxVoices = 0;
for (let s = 0; s < N; s += 128) {
  while (e < ev.length && ev[e][0] * sampleRate <= s) {
    v.port.onmessage({ data: ev[e++][1] });
  }
  const blk = new Float32Array(128);
  v.process([], [[blk]]);
  maxVoices = Math.max(maxVoices, v.voices.size);
  out.set(blk.subarray(0, Math.min(128, N - s)), s);
}
process.stdout.write(JSON.stringify({ out: Array.from(out), maxVoices }));
"""

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the worklet's own code"
)


def _whoop() -> dict:
    # what piano.js sends: the whole patch (Patch field names)
    return get_factory("Interstellar Whoop").to_json()


def run(events, seconds, tmp_path):
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    spec = json.dumps({"events": events, "seconds": seconds})
    res = subprocess.run(
        ["node", str(harness), str(WORKLET), spec],
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    got = json.loads(res.stdout)
    return np.array(got["out"]), got["maxVoices"]


def taps(midi: int, n: int, gap: float, hold: float, vid="kbd:a", vel=1.0):
    p, hz = _whoop(), 440.0 * 2 ** ((midi - 69) / 12)
    ev = []
    for i in range(n):
        t = i * gap
        ev.append([t, {"type": "noteOn", "id": vid, "freq": hz, "vel": vel, "p": p}])
        ev.append([t + hold, {"type": "noteOff", "id": vid}])
    return ev


def envelope(x: np.ndarray, frame=0.02) -> np.ndarray:
    fr = int(frame * SR)
    return np.array([np.abs(x[i : i + fr]).max() for i in range(0, len(x) - fr, fr)])


@pytest.mark.parametrize("midi", [36, 43, 48])  # C2, G2, C3: the low keys
@pytest.mark.parametrize("gap", [0.25, 0.37])  # different phase offsets per hit
def test_repeated_low_key_never_lurches(midi, gap, tmp_path):
    x, voices = run(taps(midi, 6, gap, 0.14), 6 * gap + 0.3, tmp_path)
    # past the first attack, up to the last key-up: the final 2.2 s release
    # tail is the patch's own envelope, not a re-hit
    env = envelope(x)[3 : int((5 * gap + 0.14) / 0.02)]
    # was 0.19 -> 0.09 in one frame (cancellation) and up to 1.5x (stacking)
    assert env.min() > 0.5 * env.max(), (env.min(), env.max())
    drops = env[1:] / np.maximum(env[:-1], 1e-9)
    assert drops.min() > 0.8, drops.min()  # no frame-to-frame collapse
    assert voices == 1  # one key, one voice: nothing to interfere with


def test_repeat_is_sample_continuous(tmp_path):
    x, _ = run(taps(36, 4, 0.3, 0.15), 1.4, tmp_path)
    one, _ = run(taps(36, 1, 1.0, 0.15), 1.0, tmp_path)
    # a re-hit is just another sample step, no bigger than the note's own
    assert np.abs(np.diff(x)).max() <= 1.1 * np.abs(np.diff(one)).max()


def test_a_different_pitch_on_the_same_id_still_rings_out(tmp_path):
    # a mouse pointer keeps one id across keys: C3 then E3 must not revive
    # the C3 voice at E3's pitch — the C3 tail finishes on its own
    p = _whoop()
    ev = [
        [0.0, {"type": "noteOn", "id": "1", "freq": 130.81, "vel": 1, "p": p}],
        [0.2, {"type": "noteOff", "id": "1"}],
        [0.3, {"type": "noteOn", "id": "1", "freq": 164.81, "vel": 1, "p": p}],
        [0.6, {"type": "noteOff", "id": "1"}],
    ]
    _, voices = run(ev, 0.8, tmp_path)
    assert voices == 2
