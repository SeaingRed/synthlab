"""Live keys through the loop's FX, measured against effects.py.

`studio/fxlive.py` classifies every FX unit; each one it calls "js" or
"clock" runs here — static/fx-worklet.js's own code under node — on an
engine-rendered note, against `effects.chain(note, fxrack.build_chain(...))`
with the same arguments (`fxrack.resolve_unit` feeds both). The "native"
ones are checked at their seam: the served IR is `effects.reverb_ir`, and
the reverb is exactly dry + that convolution; a lowpass/highpass node's Q
is the RBJ Q in dB. Then a whole key — the voice worklet, a layer chain
and a master chain — against the engine's note through the same chains:
1/3-octave bands within 1.5 dB.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from scipy.io import wavfile
from scipy.signal import fftconvolve

from synthlab import effects as fx
from synthlab.core import SR, note_to_hz
from synthlab.studio import fxlive, voicetables, worklet
from synthlab.studio.fxrack import FX_UNITS, build_chain, clean_chain, kick_steps
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch, Synth

FXW = Path("synthlab/studio/static/fx-worklet.js").resolve()
EQW = Path("synthlab/studio/static/eq-worklet.js").resolve()
VOICE = Path("synthlab/studio/static/voice-worklet.js").resolve()
LIVEFX = Path("synthlab/studio/static/livefx.js").resolve()
node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the worklets")

HARNESS = """
globalThis.sampleRate = 44100;
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { onmessage: null, postMessage() {} }; }
};
const reg = {};
globalThis.registerProcessor = (name, cls) => { reg[name] = cls; };
const fs = require("fs");
// eval, deliberately: both worklets are classic AudioWorklet scripts, the
// first-party files the browser loads, run here under the shims above
eval(fs.readFileSync(process.argv[2], "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "sampler-worklet"), "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "grain-worklet"), "utf8"));
eval(fs.readFileSync(process.argv[3], "utf8"));
eval(fs.readFileSync(process.argv[6], "utf8")); // eq-worklet.js: the same scope
// vox-worklet.js (the pitch shifter, the vocal chain): the same scope too
eval(fs.readFileSync(process.argv[3].replace("fx-worklet", "vox-worklet"), "utf8"));
// fx2-worklet.js (the FX2 units): the same scope too
eval(fs.readFileSync(process.argv[3].replace("fx-worklet", "fx2-worklet"), "utf8"));
const jobs = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const bin = fs.readFileSync(process.argv[5]);
const hl = bin.readUInt32LE(0);
const meta = JSON.parse(bin.subarray(4, 4 + hl).toString("utf8"));
const data = new Float32Array(
  bin.buffer.slice(bin.byteOffset + 4 + hl, bin.byteOffset + bin.length));
for (const job of jobs) {
  const Fx = reg["synthlab-fx"];
  const chains = job.chains.map((units) => new Fx({ processorOptions: { units } }));
  let voice = null, src = null;
  if (job.p) {
    voice = new reg["synthlab-voice"]();
    voice.port.onmessage({ data: { type: "tables", meta, data } });
    const on = { type: "noteOn", id: "k", freq: job.freq, vel: 1, p: job.p };
    voice.port.onmessage({ data: on });
  } else {
    const raw = new Float32Array(fs.readFileSync(job.input).buffer.slice(0));
    src = [raw.subarray(0, job.total), raw.subarray(job.total)];
  }
  const L = new Float32Array(job.total), R = new Float32Array(job.total);
  let i = 0, off = false;
  while (i < job.total) {
    let n = Math.min(128, job.total - i);
    if (voice && !off && i < job.gate && i + n > job.gate) n = job.gate - i;
    if (voice && !off && i >= job.gate) {
      voice.port.onmessage({ data: { type: "noteOff", id: "k" } });
      off = true;
    }
    globalThis.currentFrame = i; // the transport clock: the loop started at frame 0
    let l = new Float32Array(n), r = new Float32Array(n);
    if (voice) voice.process([], [[l, r]]);
    else { l.set(src[0].subarray(i, i + n)); r.set(src[1].subarray(i, i + n)); }
    for (const c of chains) {
      const ol = new Float32Array(n), or = new Float32Array(n);
      c.process([[l, r]], [[ol, or]]);
      l = ol; r = or;
    }
    L.set(l, i); R.set(r, i); i += n;
  }
  const both = [Buffer.from(L.buffer), Buffer.from(R.buffer)];
  fs.writeFileSync(job.out, Buffer.concat(both));
}
"""

ARR = Arrangement(bpm=128.0, drums=[("kick", 0), ("kick", 4), ("kick", 10)])
# one case per JS/clock unit, params off their defaults
UNITS: dict[str, list[dict]] = {
    "drive": [{"kind": "drive", "params": {"amount": 3.2, "mix": 0.8}}],
    # every mode (fxrack's index: effects.DRIVE_MODES), past the old 4 max
    "drive_hard": [{"kind": "drive", "params": {"amount": 6.0, "mix": 0.9, "mode": 1}}],
    "drive_fuzz": [{"kind": "drive", "params": {"amount": 9.0, "mix": 1.0, "mode": 2}}],
    "drive_tube": [{"kind": "drive", "params": {"amount": 2.5, "mix": 0.7, "mode": 3}}],
    "clip": [{"kind": "clip", "params": {"drive": 14.0, "ceiling": -3.0, "soft": 0.4}}],
    "clip_hard": [{"kind": "clip", "params": {"drive": 20.0, "soft": 0.0, "mix": 0.8}}],
    "clip_round": [{"kind": "clip", "params": {"drive": 9.0, "soft": 1.0}}],
    "fold": [{"kind": "fold", "params": {"amount": 2.2, "mix": 0.6}}],
    "bitcrush": [{"kind": "bitcrush", "params": {"bits": 6, "downsample": 3}}],
    "ringmod": [{"kind": "ringmod", "params": {"freq": 330.0, "mix": 0.7}}],
    "comb": [{"kind": "comb", "params": {"tune": 180.0, "feedback": 0.85, "mix": 0.6}}],
    "width": [{"kind": "width", "params": {"amount": 1.8}}],
    "delay": [
        {
            "kind": "delay",
            "params": {"time": 0.21, "feedback": 0.5, "mix": 0.4, "ping_pong": 0.0},
        }
    ],
    "delay_pingpong_sync": [
        {
            "kind": "delay",
            "params": {"feedback": 0.4, "mix": 0.5, "ping_pong": 1.0, "sync": 2},
        }
    ],
    "chorus": [{"kind": "chorus", "params": {"rate": 0.9, "depth": 5.0, "mix": 0.6}}],
    "flanger": [
        {
            "kind": "flanger",
            "params": {"rate": 0.4, "depth": 4.0, "feedback": 0.7, "mix": 0.6},
        }
    ],
    "flanger_dry": [{"kind": "flanger", "params": {"feedback": 0.0}}],
    "phaser": [{"kind": "phaser", "params": {"rate": 0.8, "depth": 0.9, "mix": 0.7}}],
    "gate": [{"kind": "gate", "params": {"depth": 0.8}, "pattern": "x.xxx..x"}],
    "pump": [{"kind": "pump", "params": {"depth": 0.8, "release": 0.3, "beats": 0.5}}],
    "sidechain": [{"kind": "sidechain", "params": {"depth": 0.9, "release": 0.2}}],
    "eq": [
        {
            "kind": "eq",
            "params": {
                "low": 6.0,
                "mid_freq": 420.0,
                "mid_gain": -9.0,
                "mid_q": 4.0,
                "high": 4.5,
            },
        }
    ],
    # the narrowest, lowest, loudest band the rack allows, on its own
    "eq_narrow": [
        {"kind": "eq", "params": {"mid_freq": 100.0, "mid_gain": 12.0, "mid_q": 8.0}}
    ],
    "eq_flat": [{"kind": "eq", "params": {"mid_freq": 300.0, "mid_q": 6.0}}],
    # the studio tricks (the vocal booth)
    "doubler": [
        {
            "kind": "doubler",
            "params": {"time": 0.022, "wobble": 0.9, "spread": 0.8, "mix": 0.85},
        }
    ],
    "doubler_centred": [{"kind": "doubler", "params": {"spread": 0.0}}],
    "rotary": [{"kind": "rotary", "params": {}}],
    "rotary_fast": [
        {"kind": "rotary", "params": {"speed": 1.0, "drive": 2.5, "mix": 0.9}}
    ],
    "tape_echo": [
        {
            "kind": "tape_echo",
            "params": {
                "time": 0.12,
                "feedback": 0.6,
                "wow": 0.8,
                "tone": 0.3,
                "drive": 0.7,
                "mix": 0.5,
            },
        }
    ],
    "tape_echo_sync": [
        {"kind": "tape_echo", "params": {"sync": 2, "feedback": 0.3, "wow": 0.0}}
    ],
    "telephone": [
        {
            "kind": "telephone",
            "params": {"centre": 900.0, "width": 1.2, "drive": 4.0, "mix": 0.9},
        }
    ],
    "telephone_clean": [{"kind": "telephone", "params": {"drive": 0.0}}],
    "pitch": [{"kind": "pitch", "params": {"semis": 12.0, "mix": 0.6}}],
    "pitch_down": [{"kind": "pitch", "params": {"semis": -12.0}}],
    "pitch_fifth": [{"kind": "pitch", "params": {"semis": 7.0, "mix": 1.0}}],
    "pitch_two_up": [{"kind": "pitch", "params": {"semis": 24.0, "mix": 1.0}}],
    # the live vocal chain (vox-worklet.js); the pitched two on a detuned
    # input as well: `test_live_vox.py`
    "autotune": [{"kind": "autotune", "params": {"speed": 0.0}}],
    "autotune_glide": [
        {
            "kind": "autotune",
            "params": {
                "key": 3.0,
                "scale": 3.0,
                "speed": 0.6,
                "humanize": 0.3,
                "mix": 0.8,
            },
        }
    ],
    "harmony": [{"kind": "harmony", "params": {"first": 0.0, "second": 4.0}}],
    "harmony_octave": [
        {"kind": "harmony", "params": {"scale": 1.0, "first": 2.0, "first_pan": 0.9}}
    ],
    "vox_comp": [
        {"kind": "vox_comp", "params": {"threshold": -30.0, "ratio": 6.0, "mix": 0.8}}
    ],
    "vox_comp_knee": [{"kind": "vox_comp", "params": {"threshold": -12.0}}],
    "deess": [{"kind": "deess", "params": {"freq": 2000.0 * 2, "threshold": -45.0}}],
    "vgate": [
        {"kind": "vgate", "params": {"threshold": -20.0, "ratio": 8.0, "release": 0.05}}
    ],
    "vocal": [
        {"kind": "vgate", "params": {}},
        {"kind": "deess", "params": {}},
        {"kind": "vox_comp", "params": {}},
        {"kind": "autotune", "params": {"speed": 0.2}},
        {"kind": "doubler", "params": {}},
    ],
    # the second round (fx2-worklet.js); the spring is a ConvolverNode
    "autofilter": [{"kind": "autofilter", "params": {}}],
    "autofilter_env": [
        {
            "kind": "autofilter",
            "params": {
                "mode": 1.0,
                "q": 6.0,
                "sync": 7.0,
                "shape": 5.0,
                "env": 0.6,
                "sens": 12.0,
                "attack": 0.003,
                "release": 0.1,
                "mix": 0.8,
            },
        }
    ],
    "autofilter_hp_down": [
        {
            "kind": "autofilter",
            "params": {
                "mode": 2.0,
                "sync": 0.0,
                "rate": 3.0,
                "shape": 3.0,
                "env": -1.0,
            },
        }
    ],
    "autofilter_still": [{"kind": "autofilter", "params": {"depth": 0.0}}],
    "tremolo": [{"kind": "tremolo", "params": {}}],
    "tremolo_pan": [
        {
            "kind": "tremolo",
            "params": {
                "sync": 0.0,
                "rate": 7.0,
                "depth": 0.9,
                "shape": 4.0,
                "phase": 180.0,
            },
        }
    ],
    "tremolo_tri": [{"kind": "tremolo", "params": {"shape": 1.0, "phase": 90.0}}],
    "tape": [{"kind": "tape", "params": {}}],
    "tape_hiss": [
        {
            "kind": "tape",
            "params": {
                "drive": 14.0,
                "bump": 5.0,
                "tone": 0.1,
                "wow": 1.0,
                "hiss": 1.0,
            },
        }
    ],
    "tape_still": [
        {"kind": "tape", "params": {"bump": 0.0, "wow": 0.0, "flutter": 0.0}}
    ],
    "pingpong": [{"kind": "pingpong", "params": {}}],
    "pingpong_free": [
        {
            "kind": "pingpong",
            "params": {
                "time": 0.13,
                "sync": 0.0,
                "feedback": 0.8,
                "lowcut": 400.0,
                "highcut": 2000.0,
                "width": 0.3,
                "mix": 0.6,
            },
        }
    ],
    "formant_shift": [{"kind": "formant_shift", "params": {}}],
    "formant_down": [{"kind": "formant_shift", "params": {"shift": -7.0, "mix": 0.7}}],
    "freqshift": [{"kind": "freqshift", "params": {}}],
    "freqshift_spiral": [
        {"kind": "freqshift", "params": {"shift": -140.0, "feedback": 0.7, "mix": 0.8}}
    ],
    "exciter": [{"kind": "exciter", "params": {}}],
    "exciter_hot": [
        {"kind": "exciter", "params": {"freq": 1500.0, "drive": 18.0, "amount": 0.9}}
    ],
    "vocoder_fx": [{"kind": "vocoder_fx", "params": {}}],
    "vocoder_fx_shift": [
        {
            "kind": "vocoder_fx",
            "params": {
                "key": 5.0,
                "scale": 3.0,
                "chord": 1.0,
                "octave": 2.0,
                "bands": 11.0,
                "shift": 5.0,
                "noise": 0.5,
                "mix": 0.7,
            },
        }
    ],
    # fx2-worklet.js: the mix's dynamics
    "ott": [{"kind": "ott", "params": {}}],
    "ott_hard": [
        {
            "kind": "ott",
            "params": {
                "depth": 1.0,
                "time": 0.25,
                "upward": 0.7,
                "downward": 0.9,
                "low": 4.0,
                "mid": -3.0,
                "high": 6.0,
                "output": -6.0,
            },
        }
    ],
    "limiter": [{"kind": "limiter", "params": {"gain": 18.0, "ceiling": -6.0}}],
    "limiter_long": [
        {
            "kind": "limiter",
            "params": {
                "gain": 24.0,
                "ceiling": -3.0,
                "release": 0.4,
                "lookahead": 0.01,
            },
        }
    ],
    "limiter_instant": [
        {"kind": "limiter", "params": {"gain": 12.0, "lookahead": 0.0, "release": 0.01}}
    ],
    "transient": [{"kind": "transient", "params": {"attack": 1.0, "sustain": 1.0}}],
    "transient_soft": [
        {
            "kind": "transient",
            "params": {"attack": -0.8, "sustain": -1.0, "output": 4.0},
        }
    ],
    # the dynamics chains as they stack (Drum Punch's live units; Master Glue)
    "stack_dynamics": [
        {"kind": "transient", "params": {"attack": 0.6, "sustain": -0.2}},
        {"kind": "clip", "params": {"drive": 3.0, "soft": 0.5}},
        {"kind": "eq", "params": {"low": 1.0, "mid_gain": -1.0, "high": 1.5}},
        {"kind": "ott", "params": {"depth": 0.2, "time": 1.5}},
        {"kind": "limiter", "params": {"gain": 9.0}},
        {"kind": "autofilter", "params": {"env": 1.0, "sens": 12.0, "mode": 1.0}},
    ],
    "stack": [
        {"kind": "drive", "params": {"amount": 2.0}},
        {"kind": "chorus", "params": {}},
        {"kind": "delay", "params": {"time": 0.15}},
        {"kind": "width", "params": {"amount": 1.4}},
    ],
    # a real Layer 2 rack with many effects stacked: its worklet units in
    # its order — the
    # highpass between drive and clipper is a WebAudio biquad, not this
    # worklet's
    "stack_owner": [
        {"kind": "chorus", "params": {"rate": 0.8, "depth": 3.0, "mix": 0.35}},
        {
            "kind": "tape_echo",
            "params": {
                "time": 0.2,
                "feedback": 0.3,
                "wow": 0.6,
                "tone": 0.5,
                "drive": 0.5,
                "mix": 0.25,
                "sync": 2.0,
            },
        },
        {"kind": "drive", "params": {"amount": 1.5, "mix": 0.4, "mode": 3}},
        {
            "kind": "clip",
            "params": {"drive": 0.0, "ceiling": -1.0, "soft": 0.6, "mix": 1.0},
        },
    ],
}
GATE, PITCH = 1.2, "C3"
DRY = Patch(sustain=0.6, release=0.4)


def test_livefx_sends_every_worklet_unit_to_the_worklet():
    """livefx.js's `JS` set — the units it runs in a synthlab-fx node — is
    FX_LIVE's "js" and "clock" units, and fx2-worklet.js owns the rest,
    the dynamics among them (the processor hands it a unit of one of its
    `kinds`)."""
    js = LIVEFX.read_text(encoding="utf-8")
    body = js.split("const JS = new Set([", 1)[1].split("]);", 1)[0]
    live = {k for k, v in fxlive.FX_LIVE.items() if v in ("js", "clock")}
    assert set(re.findall(r'"(\w+)"', body)) == live
    fx2 = FXW.with_name("fx2-worklet.js").read_text(encoding="utf-8")
    own = fx2.split("const kinds = new Set([", 1)[1].split("]);", 1)[0]
    assert set(re.findall(r'"(\w+)"', own)) == {
        "autofilter",
        "tremolo",
        "tape",
        "pingpong",
        "formant_shift",
        "freqshift",
        "exciter",
        "vocoder_fx",
        "ott",
        "limiter",
        "transient",
    }


def test_every_unit_is_classified_and_the_live_ones_have_a_case():
    assert set(fxlive.FX_LIVE) == set(FX_UNITS)
    live = {k for k, v in fxlive.FX_LIVE.items() if v in ("js", "clock")}
    covered = {u["kind"] for units in UNITS.values() for u in units}
    assert live <= covered, f"live without a parity case: {sorted(live - covered)}"


def _bands_db(x):
    p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    cs = [1000.0 * 2 ** (k / 3) for k in range(-12, 13)]
    return np.array(
        [
            10
            * np.log10(
                p[(f >= c * 2 ** (-1 / 6)) & (f < c * 2 ** (1 / 6))].sum() + 1e-24
            )
            for c in cs
        ]
    )


def _band_err(eng, live):
    worst = 0.0
    for ch in (0, 1):
        E, W = _bands_db(eng[:, ch]), _bands_db(live[:, ch])
        loud = E > E.max() - 40
        worst = max(worst, float(np.max(np.abs(W - E)[loud])))
    return worst


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("livefx")
    dry = fx.to_stereo(Synth(DRY).note(PITCH, GATE + DRY.release, gate=GATE))
    n = len(dry)
    (tmp / "dry.f32").write_bytes(
        np.concatenate([dry[:, 0], dry[:, 1]]).astype("<f4").tobytes()
    )
    dry32 = dry.astype(np.float32).astype(np.float64)  # what the harness feeds in
    jobs, want = [], {}
    for i, (name, units) in enumerate(UNITS.items()):
        jobs.append(
            {
                "chains": [fxlive.live_chain(units, ARR, drums=True)],
                "input": str(tmp / "dry.f32"),
                "total": n,
                "out": str(tmp / f"{i}.f32"),
            }
        )
        # the engine over a longer buffer, cut: a buffer's last trigger is
        # truncated there (a live key's never is)
        padded = np.vstack([dry32, np.zeros((SR, 2))])
        want[name] = (fx.chain(padded, build_chain(units, ARR))[:n], jobs[-1]["out"])
    # a whole key: the voice, a layer chain, a master chain
    layer = UNITS["stack"]
    master = [{"kind": "phaser", "params": {}}, {"kind": "pump", "params": {}}]
    p = Patch(osc1="square", unison=3, noise_level=0.0)
    eng = Synth(p).note(PITCH, GATE + p.release, gate=GATE)
    eng = fx.chain(fx.chain(eng, build_chain(layer, ARR)), build_chain(master, ARR))
    jobs.append(
        {
            "chains": [
                fxlive.live_chain(layer, ARR, True),
                fxlive.live_chain(master, ARR, True),
            ],
            "p": {**p.to_json(), **worklet.voice_tables()},
            "freq": note_to_hz(PITCH),
            "gate": round(GATE * SR),
            "total": len(eng),
            "out": str(tmp / "key.f32"),
        }
    )
    want["key"] = (eng, jobs[-1]["out"])
    (tmp / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    (tmp / "h.cjs").write_text(HARNESS, encoding="utf-8")
    (tmp / "t.bin").write_bytes(voicetables.payload())
    cmd = ["node", str(tmp / "h.cjs"), str(VOICE), str(FXW)]
    subprocess.run(
        [*cmd, str(tmp / "jobs.json"), str(tmp / "t.bin"), str(EQW)],
        check=True,
        timeout=600,
    )
    out = {}
    for name, (eng, path) in want.items():
        raw = np.fromfile(path, dtype="<f4").astype(np.float64)
        out[name] = (eng, np.column_stack([raw[: len(raw) // 2], raw[len(raw) // 2 :]]))
    return out


@node
@pytest.mark.parametrize("name", list(UNITS))
def test_each_live_unit_is_the_engines(runs, name):
    """Sample for sample (float32 rounding: measured <= 1.2e-7 of peak),
    except the gate's first edge, where the engine's smoothing reads zeros
    before the buffer and the live pattern the loop before it (0.6%); and
    within 1.5 dB per 1/3-octave band everywhere (measured 0.000)."""
    eng, live = runs[name]
    err = np.max(np.abs(live - eng)) / (np.max(np.abs(eng)) + 1e-12)
    assert _band_err(eng, live) <= 1.5, name
    assert err < {"gate": 0.01}.get(name, 1e-6), (
        name,
        err,
    )


@node
def test_a_flat_eq_passes_the_key_through_untouched(runs):
    """Every gain at 0 dB: the engine returns its input and the live unit
    runs no band — what went in (float32) comes out, sample for sample."""
    eng, live = runs["eq_flat"]
    np.testing.assert_array_equal(live, eng.astype(np.float32).astype(np.float64))


@node
def test_a_live_key_through_layer_and_master_fx_is_the_loops(runs):
    eng, live = runs["key"]
    assert _band_err(eng, live) <= 1.5


DRAG = """
globalThis.sampleRate = 44100;
globalThis.AudioWorkletProcessor = class { constructor() { this.port = {}; } };
let Fx; globalThis.registerProcessor = (_n, c) => { Fx = c; };
const fs = require("fs");
eval(fs.readFileSync(process.argv[2], "utf8"));
eval(fs.readFileSync(process.argv[3], "utf8"));
const unit = (g) => ({ kind: "eq", low: 0, mid_freq: 200, mid_gain: g, mid_q: 4,
  high: 0, low_hz: 120, high_hz: 8000, slope: 0.7 });
const f = new Fx({ processorOptions: { units: [unit(9)] } });
const set = (units) => f.port.onmessage({ data: { type: "units", units } });
const block = () => {
  const l = new Float32Array(128).fill(0.5);
  f.process([[l, l]], [[new Float32Array(128), new Float32Array(128)]]);
};
for (let i = 0; i < 20; i++) block();
const before = f.units[0].eq[0][1].z1;
set([unit(9.2)]);
const kept = f.units[0].eq[0][1].z1;
block();
set([{ kind: "drive", amount: 2, mix: 1 }]);
const fresh = f.units[0].eq === undefined;
process.stdout.write(JSON.stringify({ before, kept, fresh }));
"""


@node
def test_dragging_an_eq_slider_keeps_the_bands_ringing(tmp_path):
    """New numbers for the same chain keep each band's filter state (a
    drag would otherwise restart the filter from silence: a click)."""
    h = tmp_path / "drag.cjs"
    h.write_text(DRAG, encoding="utf-8")
    cmd = ["node", str(h), str(FXW), str(EQW)]
    got = json.loads(
        subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    )
    assert got["before"] != 0 and got["kept"] == got["before"]
    assert got["fresh"] is True  # a different chain starts clean


def test_an_eq_in_the_rack_keeps_the_keys_live(tmp_path):
    """Added from the rack it is a unit like any other, and /patch.json
    hands the worklet the engine's own corners with the rack's numbers."""
    client = TestClient(create_app(data_dir=tmp_path))
    r = client.post("/fragment/fx/add", data={"scope": "layer", "kind": "eq"})
    assert r.status_code == 200 and "<strong>EQ</strong>" in r.text
    form = {"scope": "layer", "index": "0", "name": "mid_gain", "value": "-40"}
    assert client.post("/fragment/fx/set", data=form).status_code == 200
    j = client.get("/patch.json?view=editor").json()
    assert j["worklet_ok"] is True and j["worklet_blockers"] == []
    assert j["fx"] == [
        {
            "kind": "eq",
            "low": 0.0,
            "mid_freq": 1000.0,
            "mid_gain": -12.0,  # clamped
            "mid_q": 1.0,
            "high": 0.0,
            "low_hz": fx.EQ_LOW_HZ,
            "high_hz": fx.EQ_HIGH_HZ,
            "slope": fx.EQ_SLOPE,
        }
    ]
    assert client.get("/static/eq-worklet.js").status_code == 200
    assert 'addModule("/static/eq-worklet.js")' in LIVEFX.read_text(encoding="utf-8")


def test_reverb_is_dry_plus_the_served_room(tmp_path):
    """effects._reverb = st * (1 - mix) + conv(st, reverb_ir) * mix — a
    fixed room, so the ConvolverNode loaded with /fx/ir.wav is the same
    reverb. Its level used to be matched to the buffer's own peak."""
    x = fx.to_stereo(Synth(Patch()).note("C4", 0.6))
    ir = fx.reverb_ir(1.5, 0.6)
    wet = np.column_stack([fftconvolve(x[:, c], ir[:, c])[: len(x)] for c in (0, 1)])
    np.testing.assert_allclose(
        fx._reverb(x, 1.5, 0.3, 0.6), x * 0.7 + wet * 0.3, atol=1e-12
    )
    # linear: a quieter loop no longer gets a louder room
    np.testing.assert_allclose(
        fx._reverb(0.1 * x, 1.5, 0.3, 0.6),
        0.1 * fx._reverb(x, 1.5, 0.3, 0.6),
        atol=1e-12,
    )
    client = TestClient(create_app(data_dir=tmp_path))
    r = client.get("/fx/ir.wav?decay=1.5&size=0.6&sr=48000")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    sr, served = wavfile.read(io.BytesIO(r.content))
    assert sr == 48000 and served.dtype == np.float32
    np.testing.assert_allclose(served, fx.reverb_ir(1.5, 0.6, sr=48000), atol=1e-7)
    assert client.get("/fx/ir.wav?decay=99").status_code == 422


def test_the_plate_is_dry_plus_the_served_plate(tmp_path):
    """The plate unit is the reverb's shape on its own IR (`effects.plate_ir`):
    dry * (1 - mix) + conv(dry, ir) * mix, linear in its input, and the live
    keys' ConvolverNode loads that IR from /fx/ir.wav?kind=plate, which
    `fxlive.live_chain` names with the unit's own numbers."""
    x = fx.to_stereo(Synth(Patch()).note("C4", 0.6))
    ir = fx.plate_ir(1.8, 0.4, 0.02)
    wet = np.column_stack([fftconvolve(x[:, c], ir[:, c])[: len(x)] for c in (0, 1)])
    np.testing.assert_allclose(
        fx._plate(x, 1.8, 0.4, 0.02, 0.3), x * 0.7 + wet * 0.3, atol=1e-12
    )
    np.testing.assert_allclose(
        fx._plate(0.1 * x, 1.8, 0.4, 0.02, 0.3),
        0.1 * fx._plate(x, 1.8, 0.4, 0.02, 0.3),
        atol=1e-12,
    )
    units = [{"kind": "plate", "params": {"decay": 1.8, "tone": 0.4, "predelay": 0.02}}]
    (unit,) = fxlive.live_chain(units, ARR, drums=False)
    assert unit["ir"] == "/fx/ir.wav?kind=plate&decay=1.8&tone=0.4&predelay=0.02"
    client = TestClient(create_app(data_dir=tmp_path))
    r = client.get(unit["ir"] + "&sr=48000")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    sr, served = wavfile.read(io.BytesIO(r.content))
    assert sr == 48000 and served.dtype == np.float32
    np.testing.assert_allclose(served, fx.plate_ir(1.8, 0.4, 0.02, sr=48000), atol=1e-7)
    assert client.get("/fx/ir.wav?kind=hall").status_code == 422
    assert client.get("/fx/ir.wav?kind=plate&predelay=1").status_code == 422


def test_the_spring_is_dry_plus_the_served_spring(tmp_path):
    """The spring is the plate's shape on its own IR (`effects.spring_ir`):
    dry * (1 - mix) + conv(dry, ir) * mix, linear, and the live keys'
    ConvolverNode loads that IR from /fx/ir.wav?kind=spring."""
    x = fx.to_stereo(Synth(Patch()).note("C4", 0.6))
    ir = fx.spring_ir(1.5, 0.4, 0.8)
    wet = np.column_stack([fftconvolve(x[:, c], ir[:, c])[: len(x)] for c in (0, 1)])
    np.testing.assert_allclose(
        fx._spring(x, 1.5, 0.4, 0.8, 0.3), x * 0.7 + wet * 0.3, atol=1e-12
    )
    np.testing.assert_allclose(
        fx._spring(0.1 * x, 1.5, 0.4, 0.8, 0.3),
        0.1 * fx._spring(x, 1.5, 0.4, 0.8, 0.3),
        atol=1e-12,
    )
    units = [{"kind": "spring", "params": {"decay": 1.5, "tone": 0.4, "drip": 0.8}}]
    (unit,) = fxlive.live_chain(units, ARR, drums=False)
    assert unit["ir"] == "/fx/ir.wav?kind=spring&decay=1.5&tone=0.4&drip=0.8"
    client = TestClient(create_app(data_dir=tmp_path))
    r = client.get(unit["ir"] + "&sr=48000")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    sr, served = wavfile.read(io.BytesIO(r.content))
    assert sr == 48000 and served.dtype == np.float32
    np.testing.assert_allclose(served, fx.spring_ir(1.5, 0.4, 0.8, sr=48000), atol=1e-7)
    assert client.get("/fx/ir.wav?kind=spring&drip=2").status_code == 422
    assert 'u.kind === "spring"' in LIVEFX.read_text(encoding="utf-8")
    assert 'addModule("/static/fx2-worklet.js")' in LIVEFX.read_text(encoding="utf-8")


@node
def test_eq_q_reaches_web_audio_in_db(tmp_path):
    """Web Audio's lowpass/highpass take Q in dB (alpha = sin w0 / (2 *
    10^(Q/20))): livefx.js converts the rack's RBJ Q so the node is exactly
    core.biquad's filter."""
    h = tmp_path / "q.cjs"
    h.write_text(
        "globalThis.window = {}; globalThis.document = { addEventListener() {} };\n"
        'eval(require("fs").readFileSync(process.argv[2], "utf8"));\n'
        "const qdb = window.synthlab.live.qdb;\n"
        "process.stdout.write(JSON.stringify([0.5, 0.707, 3, 8].map(qdb)));",
        encoding="utf-8",
    )
    got = json.loads(
        subprocess.run(
            ["node", str(h), str(LIVEFX)], capture_output=True, text=True, check=True
        ).stdout
    )
    np.testing.assert_allclose([10 ** (g / 20) for g in got], [0.5, 0.707, 3, 8])


def test_patch_json_carries_every_layer_and_chain(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.svc.state
    s.layers[s.active_layer]["fx"] = [{"kind": "delay", "params": {"sync": 1}}]
    s.fx_master = [{"kind": "sidechain", "params": {}}]
    s.arrangement.drums = [("kick", 0), ("kick", 8)]
    s.layers.append(
        {
            "patch": Patch(osc1="sine").to_json(),
            "cells": [],
            "follow": {"octave": 0},  # plays the key (a plain slot wouldn't)
            "gain": 0.5,
            "fx": [{"kind": "reverb", "params": {"mix": 0.4}}],
        }
    )
    s.layers.append({"patch": Patch().to_json(), "cells": [["C4", 0]], "muted": True})
    s.layers.append(
        {"patch": Patch().to_json(), "cells": [["C4", 0]]}
    )  # its own notes: the loop plays them, a key doesn't
    j = client.get("/patch.json?view=editor").json()
    assert j["worklet_ok"] is True and len(j["layers"]) == 1
    lay = j["layers"][0]
    assert (
        lay["osc1"] == "sine"
        and lay["gain"] == 0.5
        and lay["fx"][0]["ir"].startswith("/fx/ir.wav?")
    )
    assert j["fx"][0]["time"] == pytest.approx(
        60.0 / s.arrangement.bpm
    )  # synced, resolved
    assert j["master"][0]["hits"] == []  # the editor's loop renders no drums
    jp = client.get("/patch.json?view=perform").json()
    assert (
        len(jp["master"][0]["hits"]) == 2
    )  # Perform's loop does: it ducks on the kicks
    s.layers[-3]["fx"] = [{"kind": "granular", "params": {}}]  # the sine layer
    # a pre-rendered lane bakes an offline unit in: it blocks nothing, and
    # its voice node can't stand in for it (live_ok) ...
    j = client.get("/patch.json").json()
    assert j["worklet_ok"] is True and j["layers"][0]["mode"] == "buffer"
    assert j["layers"][0]["live_ok"] is False
    # ... but all-live (the A/B dev flag) plays it in the worklet: blocked
    j = client.get("/patch.json?live=all").json()
    assert j["worklet_ok"] is False and "fx:granular" in j["worklet_blockers"]
    assert j["layers"][0]["mode"] == "live"


def test_a_live_sidechain_ducks_on_the_kicks_the_loop_plays():
    """A kick kept, hidden, past a shrunk drum grid sounds in no loop and
    the render's sidechain never ducks on it (`fxrack.kick_steps`). The
    live keys repeat every hit each drum loop, so a hit past the loop's end
    ducked them where no kick plays: step 20 of a 16-step bar, at step 4."""
    arr = Arrangement(
        bpm=120.0, drum_bars=1, drum_steps=16, drums=[["kick", 0], ["kick", 20]]
    )
    unit = fxlive.live_chain([{"kind": "sidechain", "params": {}}], arr, True)[0]
    assert unit["hits"] == [0.0]
    assert all(h < unit["loop"] for h in unit["hits"])
    # the render reads the same steps
    assert kick_steps(arr) == [0]


def test_a_live_sidechain_does_not_duck_on_a_kick_that_does_not_sound():
    """A muted kick row, or one silenced by another row's solo, ducked the
    live keys as it did the loop: the worklet's `duck` is handed the hits
    `fxrack.kick_steps` leaves, the ones the render ducks on."""
    arr = Arrangement(
        bpm=120.0,
        drum_bars=1,
        drum_steps=16,
        drums=[["kick", 0], ["kick", 8], ["snare", 4]],
    )
    chain = [{"kind": "sidechain", "params": {}}]

    def hits(mix, drums=True):
        unit = fxlive.live_chain(chain, replace(arr, drum_mix=mix), drums)[0]
        assert unit["loop"] == 2.0  # the clock is the drum loop's either way
        return unit["hits"]

    assert hits({}) == [0.0, 1.0]  # as before
    assert hits({"kick": {"level": 0.5}, "hat": {"mute": True}}) == [0.0, 1.0]
    assert hits({"kick": {"solo": True}}) == [0.0, 1.0]
    assert hits({"kick": {"mute": True}}) == []
    assert hits({"snare": {"solo": True}}) == []
    assert hits({"kick": {"prob": 0.0}}) == []
    # a probability is not followed (the render's rule): every step ducks
    assert hits({"kick": {"prob": 0.5}}) == [0.0, 1.0]
    # drums off (Perform's parts toggle): no kicks, as the render has none
    assert hits({}, drums=False) == []
    for mix in ({}, {"kick": {"mute": True}}, {"snare": {"solo": True}}):
        a = replace(arr, drum_mix=mix)
        assert hits(mix) == [s * 0.125 for s in kick_steps(a)]


CAP = """
globalThis.sampleRate = 44100;
globalThis.AudioWorkletProcessor = class { constructor() { this.port = {}; } };
let V; globalThis.registerProcessor = (_n, c) => { V = c; };
eval(require("fs").readFileSync(process.argv[2], "utf8")); // the shipped file
eval(require("fs").readFileSync(
  process.argv[2].replace("voice-worklet", "sampler-worklet"), "utf8"));
eval(require("fs").readFileSync(
  process.argv[2].replace("voice-worklet", "grain-worklet"), "utf8"));
const v = new V(), out = [[new Float32Array(128), new Float32Array(128)]];
const msg = (data) => v.port.onmessage({ data });
const on = (i) => msg({ type: "noteOn", id: "k" + i, freq: 110 + i, vel: 1 });
for (let i = 0; i < 16; i++) { on(i); v.process([], out); }
msg({ type: "noteOff", id: "k3" }); v.process([], out);
msg({ type: "noteOff", id: "k9" }); v.process([], out);
on(16);
const fading = [...v.voices].filter(([, x]) => x.fadeN).map(([k]) => k);
for (let b = 0; b < 4; b++) v.process([], out);
const left = [...v.voices.keys()].length;
process.stdout.write(JSON.stringify({ fading, left }));
"""


@node
def test_a_layer_node_caps_its_voices_and_steals_the_oldest_released(tmp_path):
    h = tmp_path / "cap.cjs"
    h.write_text(
        CAP,
        encoding="utf-8",
    )
    got = json.loads(
        subprocess.run(
            ["node", str(h), str(VOICE)], capture_output=True, text=True, check=True
        ).stdout
    )
    assert got["fading"] == ["k3"]  # released longest ago, not the held ones
    assert got["left"] == 16  # it faded out within a few ms


def test_clean_chain_keeps_offline_units_visible_to_the_gate():
    units = clean_chain([{"kind": "shimmer"}, {"kind": "delay"}])
    assert fxlive.offline_units(units) == ["shimmer"]
