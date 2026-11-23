"""The granular oscillator on the live keys: static/grain-worklet.js's
`cloud` is `granular.render` grain for grain — the same onsets, the same
hashed draws (crc32 of "grain:<MIDI>:<copy>", then hash_u(seed, 3k + i):
voice-worklet.js's own crc32 and drift_u), the same window, region, read
and gain — over the payload `sampler.live_url` names for it (the sample as
stored, `raw=1`). Held here sample for sample on the oscillator alone; the
parity corpus (`granular_*`) holds the whole voice on both engines."""

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qsl

import numpy as np
import pytest

from synthlab import granular, sampler
from synthlab.studio.worklet import blockers
from synthlab.synth import Patch

SR = sampler.SR
STATIC = Path("synthlab/studio/static").resolve()
C4 = sampler.midi_hz(60)
_T = np.arange(int(1.2 * SR)) / SR
_SAW = sum(np.sin(2 * np.pi * C4 * k * _T) / k for k in range(1, 9)) * 0.3
TONE = _SAW * np.exp(-_T / 1.5) * (1 + 0.5 * np.sin(2 * np.pi * 3 * _T))
TONE_ST = np.column_stack(
    [TONE, np.sin(2 * np.pi * 1.5 * C4 * _T) * 0.3 * np.exp(-_T / 0.9)]
)
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs it")

CLOUD = """
const fs = require("fs");
const job = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
globalThis.sampleRate = job.sr;
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { onmessage: null, postMessage() {} }; }
};
globalThis.registerProcessor = () => {};
// the shipped files, as the worklet scope holds them (eval: classic scripts);
// voice-worklet.js hands the cloud its hashes (synthlabHash)
for (const f of ["voice-worklet.js", "sampler-worklet.js", "grain-worklet.js"]) {
  eval(fs.readFileSync(process.argv[2] + "/" + f, "utf8"));
}
const sb = fs.readFileSync(job.payload), sn = sb.readUInt32LE(0);
const meta = JSON.parse(sb.subarray(4, 4 + sn).toString("utf8"));
const data = new Float32Array(
  sb.buffer.slice(sb.byteOffset + 4 + sn, sb.byteOffset + sb.length));
const m = globalThis.synthlabSampler.set("u", meta, data, job.sr);
const n = job.n, L = new Float32Array(n), R = new Float32Array(n);
const F = new Float64Array(64), o = new Float64Array(64), oR = new Float64Array(64);
const v = { sp: [], target: job.key };
const P = { idx: job.routed ? { grain_pos: 0 } : {}, GP: new Float64Array(64) };
for (let i = 0; i < n; i += job.block) {
  const c = Math.min(job.block, n - i);
  for (let j = 0; j < c; j++) {
    F[j] = job.f[i + j]; P.GP[j] = job.pos ? job.pos[i + j] : 0;
  }
  globalThis.synthlabGrains.cloud(m, job.T, v, 0, job.copy, F, o, oR, c, P);
  L.set(o.subarray(0, c), i); R.set(oR.subarray(0, c), i);
}
fs.writeFileSync(
  job.out, Buffer.concat([Buffer.from(L.buffer), Buffer.from(R.buffer)]));
"""


def _live(tmp_path, p: Patch, f: np.ndarray, key: float, copy=0, sr=SR, pos=None):
    url = sampler.live_url(p)
    assert url and url.endswith("?raw=1"), url
    query = dict(parse_qsl(url.split("?", 1)[1]))
    payload = sampler.live_payload(p.sampler_sample, sampler.live_settings(query))
    (tmp_path / "p.bin").write_bytes(payload)
    job = {
        "sr": sr,
        "n": len(f),
        "f": [float(x) for x in f],
        "key": key,
        "copy": copy,
        "block": 64,
        "routed": pos is not None,
        "pos": None if pos is None else [float(x) for x in pos],
        "payload": str(tmp_path / "p.bin"),
        "T": p.to_json(),
        "out": str(tmp_path / "o.f32"),
    }
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    (tmp_path / "cloud.cjs").write_text(CLOUD, encoding="utf-8")
    cmd = ["node", str(tmp_path / "cloud.cjs"), str(STATIC)]
    subprocess.run([*cmd, str(tmp_path / "job.json")], check=True, timeout=120)
    raw = np.fromfile(tmp_path / "o.f32", dtype=np.float32).astype(np.float64)
    return raw.reshape(2, -1).T


def _engine(p, f, key, copy=0, sr=SR):
    st = granular.Settings.of(p)
    return granular.render(f, len(f), sr, st, key, copy)


_G = {"osc1": "granular"}
CASES = {
    "defaults": {},
    "dense": {"grain_density": 300.0, "grain_size": 0.03},
    "long": {"grain_size": 0.6, "grain_density": 6.0, "grain_shape": 0.7},
    "scan": {"grain_scan": 1.0, "grain_pos": 0.1},
    "scan_back": {"grain_scan": -0.6, "grain_pos": 0.8, "grain_spray": 0.2},
    "reverse": {"grain_dir": "reverse", "grain_pos": 0.3},
    "both": {"grain_dir": "both", "grain_spray": 0.5},
    "pitch_spray": {"grain_pitch_spray": 7.0, "grain_spray": 0.1},
    "loop": {
        "sampler_loop": "on",
        "sampler_loop_start": 0.3,
        "sampler_loop_end": 0.6,
        "grain_scan": 1.5,
    },
    "span": {"sampler_start": 0.2, "sampler_end": 0.7, "grain_pos": 0.9},
    "keytrack_off": {"sampler_keytrack": "off", "grain_spray": 0.3},
    "root": {"sampler_root": 48, "grain_density": 60.0},
    "flat": {"grain_shape": 1.0, "grain_size": 0.005, "grain_density": 500.0},
    "capped": {"grain_size": 1.0, "grain_density": 500.0},
}


@needs_node
@pytest.mark.parametrize("audio", [TONE, TONE_ST], ids=["mono", "stereo"])
@pytest.mark.parametrize("case", list(CASES))
def test_the_cloud_is_the_engines_own_samples(tmp_path, audio, case):
    """Every grain where the engine lays it, read where it reads it: float32
    apart (the payload's samples are float32), the whole note."""
    p = Patch(**_G, sampler_sample=sampler.register(audio), **CASES[case])
    n = int(1.0 * SR)
    for st, copy in ((0, 0), (7, 101), (-12, 3)):
        hz = C4 * 2 ** (st / 12)
        f = np.full(n, hz)
        eng, live = _engine(p, f, hz, copy), _live(tmp_path, p, f, hz, copy)
        assert eng.shape == live.shape
        peak = float(np.max(np.abs(eng)))
        assert peak > 0.01, (case, st)
        err = float(np.max(np.abs(eng - live)))
        assert err < 2e-6 * peak, (case, st, err / peak)


@needs_node
def test_a_moving_pitch_and_a_routed_position(tmp_path):
    """A grain reads at the pitch and the position at its first sample: a
    glide (the per-sample pitch) and a per-sample `grain_pos` (a route)
    land on the same grains in both."""
    p = Patch(**_G, sampler_sample=sampler.register(TONE_ST), grain_spray=0.1)
    n = int(0.8 * SR)
    f = C4 * 2 ** (np.linspace(-5, 9, n) / 12)
    pos = 0.5 + 0.45 * np.sin(2 * np.pi * 1.7 * np.arange(n) / SR)
    eng = _engine(replace(p, grain_pos=pos), f, C4)
    live = _live(tmp_path, p, f, C4, pos=pos)
    err = float(np.max(np.abs(eng - live)))
    assert err < 2e-6 * float(np.max(np.abs(eng))), err


@needs_node
def test_a_48k_context_lays_the_engines_cloud_at_its_rate(tmp_path):
    """The engine renders at any rate it is asked for: the voice at a
    48 kHz context is `granular.render(..., sr=48000)` exactly."""
    p = Patch(**_G, sampler_sample=sampler.register(TONE), grain_scan=0.5)
    n = int(0.6 * 48000)
    f = np.full(n, C4 * 1.5)
    eng = _engine(p, f, C4 * 1.5, sr=48000)
    live = _live(tmp_path, p, f, C4 * 1.5, sr=48000)
    err = float(np.max(np.abs(eng - live)))
    assert err < 2e-6 * float(np.max(np.abs(eng))), err


def test_the_payload_is_the_sample_as_stored():
    """`raw=1`: one source, the whole sample, nothing faded — whatever the
    span or loop (the grain schedule applies those), so one URL serves
    every setting."""
    sid = sampler.register(TONE_ST)
    a = Patch(**_G, sampler_sample=sid)
    b = replace(a, sampler_start=0.3, sampler_loop="on", sampler_xfade=0.05)
    assert (
        sampler.live_url(a) == sampler.live_url(b) == f"/sampler/live/{sid}.bin?raw=1"
    )
    st = sampler.live_settings({"raw": "1", "start": "0.3", "loop": "on"})
    body = sampler.live_payload(sid, st)
    n = int.from_bytes(body[:4], "little")
    meta = json.loads(body[4 : 4 + n])
    (src,) = meta["sources"]
    x = sampler.get(sid)
    assert meta["ch"] == 2 and src["n"] == len(x) and not src["looping"]
    data = np.frombuffer(body[4 + n :], dtype="<f4").reshape(2, -1).T
    assert np.array_equal(data, x.astype(np.float32))
    # the sampler's URL is untouched by the new key
    s = Patch(osc1="sampler", sampler_sample=sid)
    assert "raw" not in sampler.live_url(s)


def test_the_gate_admits_the_shape_on_osc1_and_osc2():
    sid = sampler.register(TONE)
    g = Patch(**_G, sampler_sample=sid, grain_density=200.0, grain_dir="both")
    assert blockers(g) == []
    assert blockers(Patch(osc2="granular", sampler_sample=sid, grain_scan=1.0)) == []
    # osc1's sampler reads the sample prepared: osc2's cloud would need a
    # second payload, so the pair keeps the WAV path
    both = Patch(osc1="sampler", osc2="granular", sampler_sample=sid)
    assert "osc2" in blockers(both)
    # nor a stereo cloud ringing a mono osc1 (the engine renders no such sound)
    ringing = Patch(osc2="granular", sampler_sample=sid, ring_mod=0.3)
    assert "osc2" in blockers(ringing)
    assert blockers(replace(ringing, osc1="granular")) == []


def test_the_gate_holds_the_grains_a_voice_keeps_sounding():
    """At most LIVE_GRAINS grains at once in a voice (overlap x unison x
    clouds): past it the keys take the WAV, size and density named."""
    sid = sampler.register(TONE)
    p = Patch(**_G, sampler_sample=sid, grain_density=200.0, grain_size=0.3)
    assert blockers(p) == []  # 60 at once
    assert blockers(replace(p, unison=2)) == ["grain_size", "grain_density"]
    assert blockers(replace(p, osc2="granular")) == ["grain_size", "grain_density"]
    # the engine's own cap counts: 1 s grains at 500 a second are 256
    capped = replace(p, grain_size=1.0, grain_density=500.0)
    assert blockers(capped) == ["grain_size", "grain_density"]
    # no cloud plays: the fields are never read
    assert blockers(replace(capped, osc1="saw")) == []
