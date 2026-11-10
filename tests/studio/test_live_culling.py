"""Silence culling in the live key engine costs nothing you can hear.

`voice-worklet.js` frees a released voice once its output has sat under
-120 dBFS for two render quanta, when nothing can bring it back up (no
tremolo, filter LFO, mod route, or a filter envelope that rises through the
filter as it falls); `fx-worklet.js` skips its units while the input is
silent and every unit's tail bound has run out, resuming in the first
quantum that carries signal again. Both are run here — the shipped files
under node — with culling on and off (`proc.cull = false`): up to the
moment a voice is culled the output is bit-identical, and nothing culled
differs from the full render by -100 dBFS or more.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from synthlab.core import SR, note_to_hz
from synthlab.studio import voicetables, worklet
from synthlab.synth import Patch, Synth

VOICE = Path("synthlab/studio/static/voice-worklet.js").resolve()
FXW = Path("synthlab/studio/static/fx-worklet.js").resolve()
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node runs the worklets"
)

HARNESS = """
globalThis.sampleRate = 44100;
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { onmessage: null, postMessage() {} }; }
};
const reg = {};
globalThis.registerProcessor = (name, cls) => { reg[name] = cls; };
const fs = require("fs");
// eval, deliberately: the shipped first-party worklet files, under shims
eval(fs.readFileSync(process.argv[2], "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "sampler-worklet"), "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "grain-worklet"), "utf8"));
eval(fs.readFileSync(process.argv[3], "utf8"));
const jobs = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const bin = fs.readFileSync(process.argv[5]);
const hl = bin.readUInt32LE(0);
const meta = JSON.parse(bin.subarray(4, 4 + hl).toString("utf8"));
const data = new Float32Array(
  bin.buffer.slice(bin.byteOffset + 4 + hl, bin.byteOffset + bin.length));
const stats = [];
for (const job of jobs) {
  const L = new Float32Array(job.total), R = new Float32Array(job.total);
  let freed = -1, skipped = 0, proc, src = null;
  if (job.p) {
    proc = new reg["synthlab-voice"]();
    proc.port.onmessage({ data: { type: "tables", meta, data } });
    const on = { type: "noteOn", id: "k", freq: job.freq, vel: 1, p: job.p };
    proc.port.onmessage({ data: on });
  } else {
    proc = new reg["synthlab-fx"]({ processorOptions: { units: job.units } });
    const raw = new Float32Array(fs.readFileSync(job.input).buffer.slice(0));
    src = [raw.subarray(0, job.total), raw.subarray(job.total)];
  }
  proc.cull = job.cull;
  for (let i = 0; i < job.total; i += 128) {
    const n = Math.min(128, job.total - i);
    const off = { type: "noteOff", id: "k" };
    if (job.p && i === job.gate) proc.port.onmessage({ data: off });
    const l = new Float32Array(n), r = new Float32Array(n);
    if (job.p) proc.process([], [[l, r]]);
    else {
      const ins = [src[0].subarray(i, i + n), src[1].subarray(i, i + n)];
      proc.process([ins], [[l, r]]);
    }
    if (job.p && freed < 0 && proc.voices.size === 0) freed = i + n;
    if (!job.p && proc.still - n > proc.tail) skipped++;
    L.set(l, i); R.set(r, i);
  }
  stats.push({ freed, skipped });
  const both = [Buffer.from(L.buffer), Buffer.from(R.buffer)];
  fs.writeFileSync(job.out, Buffer.concat(both));
}
process.stdout.write(JSON.stringify(stats));
"""


def _run(jobs, tmp_path):
    for i, j in enumerate(jobs):
        j["out"] = str(tmp_path / f"out{i}.f32")
    (tmp_path / "h.cjs").write_text(HARNESS, encoding="utf-8")
    (tmp_path / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    (tmp_path / "tables.bin").write_bytes(voicetables.payload())
    cmd = ["node", str(tmp_path / "h.cjs"), str(VOICE), str(FXW)]
    cmd += [str(tmp_path / "jobs.json"), str(tmp_path / "tables.bin")]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
    outs = []
    for j in jobs:
        x = np.fromfile(j["out"], dtype=np.float32).astype(np.float64)
        outs.append(x.reshape(2, -1).T)
    return json.loads(res.stdout), outs


def _voice_jobs(p: Patch, pitch: int, hold: float, total: float):
    spec = {**p.to_json(), **worklet.voice_tables()}
    gate = int(hold * SR) // 128 * 128
    job = {"p": spec, "freq": note_to_hz(pitch), "gate": gate, "total": int(total * SR)}
    return [{**job, "cull": True}, {**job, "cull": False}]


PLUCK = Patch(osc1="pluck", pluck_damping=0.6, sustain=0.9, release=4.0, cutoff=5000)
PAD = Patch(osc1="saw", unison=3, attack=0.05, sustain=0.7, release=1.5, env_curve=1.0)
# a ladder whose filter env closes on the release: calm (it only falls)
CLOSING = Patch(osc1="square", filter_env=3000, filter_release=0.3, release=2.5)


@pytest.mark.parametrize(
    "patch", [PLUCK, PAD, CLOSING], ids=["pluck", "pad", "closing"]
)
def test_a_culled_voice_is_bit_identical_until_culled_then_under_100_dbfs(
    tmp_path, patch
):
    (on, off), (a, b) = _run(_voice_jobs(patch, 57, 0.5, 5.0), tmp_path)
    assert off["freed"] > 0 and 0 < on["freed"] <= off["freed"]
    cut = on["freed"]
    np.testing.assert_array_equal(a[:cut], b[:cut])  # the normal tail: untouched
    assert not np.any(a[cut:])  # culled: silence
    assert np.max(np.abs(a - b)) < 1e-5  # what it would still have played
    assert 20 * np.log10(np.max(np.abs(b[cut:])) + 1e-30) < -100


def test_a_decaying_string_is_freed_seconds_before_its_release_ends(tmp_path):
    """The saving: a pluck held 0.5 s with a 4 s release used to be processed
    for all 4 s; its string is under -120 dBFS long before that."""
    (on, off), _ = _run(_voice_jobs(PLUCK, 57, 0.5, 5.0), tmp_path)
    assert (off["freed"] - on["freed"]) / SR > 1.0


@pytest.mark.parametrize(
    "patch",
    [
        Patch(osc1="pluck", tremolo_depth=1.0, tremolo_rate=0.1, release=4.0),
        Patch(osc1="pluck", filter_lfo_depth=800, release=4.0),
        Patch(osc1="pluck", filter_type="highpass", filter_env=2000, release=4.0),
        Patch(
            osc1="pluck", mod1_src="lfo", mod1_dst="cutoff", mod1_amt=0.5, release=4.0
        ),
    ],
    ids=["tremolo", "filter-lfo", "highpass-env", "route"],
)
def test_a_voice_something_could_bring_back_is_never_culled_early(tmp_path, patch):
    (on, off), (a, b) = _run(_voice_jobs(patch, 57, 0.5, 5.0), tmp_path)
    assert on["freed"] == off["freed"]
    np.testing.assert_array_equal(a, b)


def _burst_input(tmp_path, total: float, at: tuple[float, ...]) -> tuple[str, int]:
    """A 0.3 s saw note at each of `at` seconds, silence between."""
    n = int(total * SR)
    x = np.zeros((n, 2))
    note = Synth(Patch(osc1="saw", release=0.05)).note(57, 0.3)
    note = note if note.ndim == 2 else np.stack([note, note], axis=1)
    for t in at:
        i = int(t * SR)
        x[i : i + len(note)] += note[: n - i]
    path = tmp_path / "in.f32"
    np.concatenate([x[:, 0], x[:, 1]]).astype(np.float32).tofile(path)
    return str(path), n


def _u(kind, **kw):
    return {"kind": kind, **kw}


CHAINS = {
    "delay": [_u("delay", time=0.25, feedback=0.5, mix=0.4, ping_pong=0.0)],
    "pingpong": [_u("delay", time=0.2, feedback=0.4, mix=0.5, ping_pong=1.0)],
    "comb": [_u("comb", tune=180.0, feedback=0.85, mix=0.6)],
    "chorus": [_u("chorus", rate=0.9, depth=5.0, base=12.0, mix=0.6)],
    "flanger": [_u("flanger", rate=0.4, depth=4.0, base=2.0, feedback=0.7, mix=0.6)],
    "phaser": [_u("phaser", rate=0.8, depth=0.9, mix=0.7)],
    "bitcrush": [_u("bitcrush", bits=6, downsample=3)],
    "drive": [_u("drive", amount=3.0, mix=0.8)],
    "stack": [
        _u("drive", amount=2.0, mix=1.0),
        _u("chorus", rate=0.9, depth=3.0, base=12.0, mix=0.5),
        _u("delay", time=0.15, feedback=0.35, mix=0.35, ping_pong=0.0),
    ],
}


@pytest.mark.parametrize("name", list(CHAINS))
def test_an_fx_chain_rests_once_its_tail_is_out_and_resumes_at_once(tmp_path, name):
    """A burst, a long silence, a second burst: culled, the chain skips its
    units for most of the silence, and still differs from the full run by
    less than -100 dBFS — the second burst included, from its first
    quantum."""
    path, n = _burst_input(tmp_path, 9.0, (0.1, 8.0))
    job = {"units": CHAINS[name], "input": path, "total": n}
    (on, off), (a, b) = _run([{**job, "cull": True}, {**job, "cull": False}], tmp_path)
    assert off["skipped"] == 0 and on["skipped"] > 0
    assert np.max(np.abs(a - b)) < 1e-5
    i = int(8.0 * SR)  # the second burst sounds the same, at once
    assert np.max(np.abs(a[i : i + 4096] - b[i : i + 4096])) < 1e-5
    assert np.max(np.abs(b[i : i + 4096])) > 0.01
