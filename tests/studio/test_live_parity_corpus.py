"""The live voice plays what the engine renders — measured, not asserted.

A review found only 5 of 114 factory + library sounds passed the worklet
gate and none of them matched the engine: the worklet ran a 4-pole
"ladder-ish" filter where core.py runs SVF -> soft_clip -> SVF, a linear key
track where the engine's is exponential, ignored velocity on the filter
envelope, played supersaw as a plain saw, and scaled its output by a
constant of its own. The gate (`studio/worklet.py`) now admits a field only
when this file proves the worklet reproduces it:

- every factory + library sound the gate admits, at C2, C4 and C6;
- one synthetic case per admitted field (`FIELD_CASES`), so a field is
  exercised even when no shipped sound moves it off its default.

Each runs voice-worklet.js's own code under node (the shipped file, not a
reimplementation, fed the engine's own tables from /wavetables.bin) — twice:
the JS DSP (the reference and the fallback) and the WebAssembly engine
(static/voice-dsp.wasm posted to the voice, as livefx.js does) — against
`Synth.note(...)` with the same gate, and must hold: sustain level within
0.5 dB; harmonic magnitudes (relative, to 12 kHz) within 1 dB RMS and 3 dB
max; the 10 ms level envelope within 1 dB at the 95th percentile.

Random sources (the noise layer and the noise oscillator: numpy's
generator is not realtime) are held statistically instead. The sound with
its noise stripped must pass everything above; the whole sound, over a
longer note, must hold its level within 0.5 dB, every 1/3-octave band
within 40 dB of the loudest within 1.5 dB, and the 50 ms envelope within
1 dB at the 95th percentile — each side averaged over four seeds.
Pluck and the stepped LFO are seeded too, but their streams ride
/wavetables.bin, so they are held to samples like everything else.

The two engines must also agree with each other sample for sample
(`test_wasm_engine_matches_the_js_voice`): the module runs the JS voice's
own float64 math, so their f32 outputs differ by float rounding at most.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qsl

import numpy as np
import pytest

from synthlab import core, sampler, wavetables
from synthlab.core import SR, note_to_hz
from synthlab.params import MOD_SLOTS, slot_active
from synthlab.studio import voicetables, worklet
from synthlab.studio.factory import FACTORY, get_factory
from synthlab.studio.library import LIBRARY, get_library
from synthlab.studio.recipes import RECIPES
from synthlab.synth import Patch, Synth, at_bpm

WORKLET = Path("synthlab/studio/static/voice-worklet.js").resolve()
WASM = Path("synthlab/studio/static/voice-dsp.wasm").resolve()
ENGINES = ("js", "wasm")

HARNESS = """
globalThis.sampleRate = 44100;
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { onmessage: null, postMessage() {} }; }
};
let Voice = null;
globalThis.registerProcessor = (_name, cls) => { Voice = cls; };
const fs = require("fs");
// eval, deliberately: voice-worklet.js is a classic AudioWorklet script,
// not a module. It is the first-party file the browser loads, run here
// under the shims above rather than reimplemented.
eval(fs.readFileSync(process.argv[2], "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "sampler-worklet"), "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "grain-worklet"), "utf8"));
// the WebAssembly engine, when a module path is given: compiled here as
// livefx.js compiles it, posted to each voice as livefx.js posts it
const mod = process.argv[5]
  ? new WebAssembly.Module(fs.readFileSync(process.argv[5])) : null;
const ext = mod ? ".wasm" : ".js";
const jobs = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
// /wavetables.bin, split the way piano.js splits it
const bin = fs.readFileSync(process.argv[4]);
const hl = bin.readUInt32LE(0);
const meta = JSON.parse(bin.subarray(4, 4 + hl).toString("utf8"));
const data = new Float32Array(
  bin.buffer.slice(bin.byteOffset + 4 + hl, bin.byteOffset + bin.length));
for (const job of jobs) {
  const L = new Float32Array(job.total), R = new Float32Array(job.total);
  for (const seed of job.seeds) {
    const v = new Voice();
    v.port.onmessage({ data: { type: "tables", meta, data } });
    if (mod) {
      v.port.onmessage({ data: { type: "wasm", module: mod } });
      if (!v.wasm) throw new Error("the WebAssembly engine did not start");
    }
    if (job.sampler) { // the live sampler's buffers, split as livefx.js splits them
      const sb = fs.readFileSync(job.sampler), sn = sb.readUInt32LE(0);
      v.port.onmessage({ data: { type: "sampler", url: job.p.sampler_live,
        meta: JSON.parse(sb.subarray(4, 4 + sn).toString("utf8")),
        data: new Float32Array(
          sb.buffer.slice(sb.byteOffset + 4 + sn, sb.byteOffset + sb.length)) } });
    }
    if (job.wtable) { // your own wavetable's mip stack, as livefx.js posts it
      const wb = fs.readFileSync(job.wtable), wn = wb.readUInt32LE(0);
      v.port.onmessage({ data: { type: "wtable", url: job.p.wt_live,
        meta: JSON.parse(wb.subarray(4, 4 + wn).toString("utf8")),
        data: new Float32Array(
          wb.buffer.slice(wb.byteOffset + 4 + wn, wb.byteOffset + wb.length)) } });
    }
    const on = {
      type: "noteOn", id: "k", freq: job.freq, vel: job.vel, p: job.p, seed };
    if (job.fromFreq) { on.fromFreq = job.fromFreq; on.glideTime = job.p.glide; }
    if (job.rr != null) on.rr = job.rr; // a zone map's strike
    v.port.onmessage({ data: on });
    const total = job.total, gate = job.gate;
    let i = 0, off = false, bent = job.wheelAt == null, moved = job.ccAt == null;
    while (i < total) {
      // the bend wheel moves (livefx.js fans a "wheel" out to every voice)
      if (!bent && i >= job.wheelAt) {
        v.port.onmessage({ data: { type: "wheel", st: job.wheelSt } });
        bent = true;
      }
      // the mod wheel or aftertouch moves ("modwheel" / "aftertouch")
      if (!moved && i >= job.ccAt) {
        v.port.onmessage({ data: { type: job.ccType, v: job.ccV } });
        moved = true;
      }
      // blocks of 128, split so the key-up lands on the engine's gate sample
      let n = Math.min(128, total - i);
      if (!off && i < gate && i + n > gate) n = gate - i;
      if (!bent && i < job.wheelAt && i + n > job.wheelAt) n = job.wheelAt - i;
      if (!moved && i < job.ccAt && i + n > job.ccAt) n = job.ccAt - i;
      if (!off && i >= gate) {
        v.port.onmessage({ data: { type: "noteOff", id: "k" } });
        off = true;
      }
      const l = new Float32Array(n), r = new Float32Array(n);
      v.process([], [[l, r]]);
      // several seeds: store them one after another
      L.set(l, i); R.set(r, i); i += n;
    }
    const both = [Buffer.from(L.buffer), Buffer.from(R.buffer)];
    fs.appendFileSync(job.out + ext, Buffer.concat(both));
  }
}
"""

pytestmark = [
    pytest.mark.skipif(
        shutil.which("node") is None,
        reason="node is needed to run the worklet's own code",
    ),
    # the heaviest file in the suite (two node processes over every sound):
    # `pytest -m "not slow"` is the quick tier, the bare run still includes it
    pytest.mark.slow,
]

GATE = 1.0
GATE_RANDOM = 3.0  # random sources: a longer note, so 1/3-octave bands settle
SEEDS = (11, 23, 37, 53)
PITCHES = ("C2", "C4", "C6")
BPM = 120.0  # what the synced cases resolve against, as /patch.json does

# One synthetic patch per behaviour the worklet claims. Values sit well off
# the defaults, so a field the worklet ignored would fail its case.
CASES: dict[str, tuple[Patch, dict]] = {
    "default": (Patch(), {}),
    "osc_sine": (Patch(osc1="sine"), {}),
    "osc_square": (Patch(osc1="square"), {}),
    "osc_triangle": (Patch(osc1="triangle"), {}),
    "osc_supersaw": (Patch(osc1="supersaw"), {}),
    "osc_fm": (Patch(osc1="fm", fm_ratio=3.0, fm_index=4.0, fm_decay=0.3), {}),
    "osc2_supersaw": (Patch(osc1="sine", osc2="supersaw", unison=2), {}),
    "osc2_fm_cross": (Patch(osc2="fm", fm_amount=0.2, ring_mod=0.3), {}),
    "level": (Patch(osc1_level=0.35), {}),
    "octave_detune": (Patch(osc1_octave=1, osc1_detune=-23.0), {}),
    "amp_env": (
        Patch(attack=0.08, decay=0.45, sustain=0.35, release=0.6, env_curve=3.5),
        {},
    ),
    "env_linear": (Patch(decay=0.5, sustain=0.2, env_curve=1.0), {}),
    "filter_env": (
        Patch(
            filter_env=6000.0,
            filter_attack=0.06,
            filter_decay=0.5,
            filter_sustain=0.15,
            filter_release=0.7,
        ),
        {},
    ),  # fmt: skip
    # the filter shuts over its own release, well inside the amp's: both
    # envelopes used to fall over the amp release (the note's tail), and a
    # voice that still did would ring on bright here
    "filter_release": (
        Patch(
            cutoff=300.0,
            filter_env=7000.0,
            filter_sustain=0.9,
            filter_release=0.08,
            release=0.8,
        ),
        {},
    ),  # fmt: skip
    "cutoff_reso": (Patch(cutoff=500.0, resonance=0.85), {}),
    "key_track": (Patch(cutoff=900.0, key_track=1.0), {}),
    "velocity": (Patch(filter_env=5000.0), {"vel": 0.45}),
    "lowpass": (Patch(filter_type="lowpass", resonance=0.6), {}),
    "highpass": (Patch(filter_type="highpass", cutoff=800.0, filter_env=0.0), {}),
    "bandpass": (Patch(filter_type="bandpass", cutoff=1200.0, resonance=0.4), {}),
    "notch": (Patch(filter_type="notch", cutoff=1500.0), {}),
    "filter_none": (Patch(filter_type="none"), {}),
    "drive_ladder": (Patch(drive=3.5, resonance=0.5), {}),
    "drive_soft": (Patch(drive=0.5), {}),
    "drive_post": (Patch(filter_type="lowpass", drive=4.0), {}),
    "osc2": (Patch(osc2="square", osc2_level=0.6, osc2_octave=-1, osc2_detune=9.0), {}),
    "osc2_sine_up": (Patch(osc1="triangle", osc2="sine", osc2_octave=1), {}),
    "fm": (Patch(osc1="sine", osc2="sine", osc2_octave=0, fm_amount=0.35), {}),
    "ring": (Patch(osc2="triangle", osc2_octave=1, ring_mod=0.7), {}),
    "sub": (Patch(sub_level=0.6), {}),
    "vibrato": (Patch(vibrato_depth=0.4, vibrato_rate=5.5, vibrato_delay=0.1), {}),
    "pwm": (Patch(osc1="square", pwm_depth=0.6, pwm_rate=0.9), {}),
    "tremolo": (Patch(tremolo_depth=0.6, tremolo_rate=4.0), {}),
    "filter_lfo": (Patch(filter_lfo_depth=900.0, filter_lfo_rate=2.5), {}),
    "pitch_env": (Patch(pitch_env=12.0, pitch_env_decay=0.25), {}),
    "unison": (Patch(unison=3, unison_detune=15.0, unison_spread=0.8), {}),
    "unison_wide": (Patch(osc1="square", unison=5, unison_detune=25.0), {}),
    "glide": (Patch(glide=0.2), {"from": -7}),
    # --- noise, pluck, wavetable, formant, the mod matrix ---
    "pluck": (Patch(osc1="pluck", pluck_damping=0.3, pluck_bright=0.6), {}),
    "pluck_osc2": (
        Patch(osc1="sine", osc2="pluck", osc2_octave=0, osc2_level=0.7, unison=3),
        {},
    ),
    "pluck_glide_fm": (
        Patch(osc1="pluck", osc2="sine", fm_amount=0.3, pitch_env=5.0, glide=0.1),
        {"from": 4},
    ),
    "wavetable": (Patch(osc1="wavetable", wt_table="vocal", wt_pos=0.4), {}),
    "wavetable_osc2": (
        Patch(osc1="sine", osc2="wavetable", wt_table="digital", wt_pos=0.8, unison=2),
        {},
    ),
    # a two-octave pitch drop sweeps the mip choice per 64-sample block
    "wavetable_mips": (
        Patch(osc1="wavetable", wt_table="basic", wt_pos=0.7, pitch_env=24.0),
        {},
    ),
    "wavetable_fm": (
        Patch(
            osc1="wavetable",
            wt_table="harmonic",
            wt_pos=1.0,
            osc2="sine",
            fm_amount=0.4,
        ),
        {},
    ),
    "formant": (
        Patch(formant_amount=0.8, formant_vowel=0.3, formant_size=1.2),
        {},
    ),
    "formant_small_tract": (
        Patch(
            filter_type="none", formant_amount=1.0, formant_vowel=0.9, formant_size=0.6
        ),
        {},
    ),
    # the mod matrix: every slot, every source, every target somewhere
    "mod1": (
        Patch(mod1_src="lfo", mod1_dst="osc1_detune", mod1_amt=0.5, mod_lfo_rate=6.0),
        {},
    ),
    "mod2": (Patch(mod2_src="aenv", mod2_dst="cutoff", mod2_amt=0.6), {}),
    "mod3": (Patch(mod3_src="fenv", mod3_dst="osc1_level", mod3_amt=-0.5), {}),
    "mod4": (
        Patch(osc1="square", mod4_src="velocity", mod4_dst="pwm_depth", mod4_amt=0.7),
        {"vel": 0.8},
    ),
    "mod_filter": (
        Patch(
            filter_lfo_depth=300.0,
            mod1_src="lfo",
            mod1_dst="resonance",
            mod1_amt=0.4,
            mod2_src="fenv",
            mod2_dst="filter_env",
            mod2_amt=-0.3,
            mod3_src="aenv",
            mod3_dst="drive",
            mod3_amt=0.5,
            mod4_src="lfo",
            mod4_dst="filter_lfo_depth",
            mod4_amt=0.5,
        ),
        {},
    ),  # fmt: skip
    "mod_mix": (
        Patch(
            osc2="saw",
            osc2_octave=0,
            mod1_src="lfo",
            mod1_dst="osc2_level",
            mod1_amt=-0.5,
            mod2_src="aenv",
            mod2_dst="sub_level",
            mod2_amt=0.6,
            mod3_src="velocity",
            mod3_dst="osc1_detune",
            mod3_amt=-0.6,
            mod4_src="lfo",
            mod4_dst="osc2_detune",
            mod4_amt=0.4,
        ),
        {"vel": 0.7},
    ),  # fmt: skip
    "mod_motion": (
        Patch(
            mod1_src="aenv",
            mod1_dst="vibrato_depth",
            mod1_amt=0.8,
            mod2_src="lfo",
            mod2_dst="tremolo_depth",
            mod2_amt=0.7,
            mod_lfo_rate=3.0,
        ),
        {},
    ),  # fmt: skip
    "mod_chain": (
        Patch(
            mod1_src="lfo",
            mod1_dst="cutoff",
            mod1_amt=0.3,
            mod2_src="aenv",
            mod2_dst="cutoff",
            mod2_amt=0.4,
        ),
        {},
    ),  # fmt: skip
    "mod_post_drive": (
        Patch(filter_type="lowpass", mod1_src="aenv", mod1_dst="drive", mod1_amt=0.5),
        {},
    ),
    # every LFO shape, on both the route LFO and the filter LFO
    "lfo_square": (
        Patch(lfo_shape="square", mod1_src="lfo", mod1_dst="cutoff", mod1_amt=0.4),
        {},
    ),
    "lfo_saw": (
        Patch(lfo_shape="saw", mod1_src="lfo", mod1_dst="osc1_detune", mod1_amt=0.6),
        {},
    ),
    "lfo_triangle": (
        Patch(
            lfo_shape="triangle", mod1_src="lfo", mod1_dst="osc1_level", mod1_amt=0.3
        ),
        {},
    ),
    "lfo_noise": (
        Patch(
            lfo_shape="noise",
            mod_lfo_rate=8.0,
            mod1_src="lfo",
            mod1_dst="cutoff",
            mod1_amt=0.5,
        ),
        {},
    ),
    "lfo_sync": (
        Patch(lfo_sync="1/8", mod1_src="lfo", mod1_dst="osc1_detune", mod1_amt=0.4),
        {},
    ),
    "flfo_noise": (
        Patch(filter_lfo_shape="noise", filter_lfo_depth=1500.0, filter_lfo_rate=6.0),
        {},
    ),
    "flfo_square": (
        Patch(filter_lfo_shape="square", filter_lfo_depth=1200.0, filter_lfo_rate=3.0),
        {},
    ),
    "flfo_sync": (
        Patch(filter_lfo_sync="1/4", filter_lfo_depth=1000.0, filter_lfo_shape="saw"),
        {},
    ),
    # random sources: statistical parity
    "noise_white": (Patch(noise_level=0.3), {}),
    "noise_pink": (Patch(noise_level=0.4, noise_color="pink"), {}),
    "noise_brown": (
        Patch(noise_level=0.6, noise_color="brown", filter_type="none"),
        {},
    ),
    "noise_unison": (Patch(noise_level=0.3, unison=3), {}),
    "osc_noise": (Patch(osc1="noise", filter_type="bandpass", cutoff=1500.0), {}),
    "mod_noise": (
        Patch(noise_level=0.1, mod1_src="fenv", mod1_dst="noise_level", mod1_amt=0.5),
        {},
    ),
    # --- pitch bend: a held bend, the wheel moving mid-note, routes to "pitch"
    "pitch_bend": (Patch(pitch_bend=0.5, bend_range=7.0), {}),
    "pitch_bend_down": (
        Patch(osc1="square", pwm_depth=0.3, pitch_bend=-1.0, bend_range=12.0, unison=3),
        {},
    ),
    # the live wheel at +5 st from 0.3 s == a drawn bend stepping there
    "wheel": (Patch(osc2="saw", osc2_octave=0, bend_range=12.0), {"wheel": (0.3, 5.0)}),
    "wheel_wavetable": (
        Patch(osc1="wavetable", wt_table="digital", wt_pos=0.5, pitch_bend=0.25),
        {"wheel": (0.25, -7.0)},
    ),
    "mod_pitch": (
        Patch(mod1_src="lfo", mod1_dst="pitch", mod1_amt=0.15, mod_lfo_rate=4.0),
        {},
    ),
    "mod_pitch_env": (
        Patch(
            osc1="triangle",
            mod1_src="fenv",
            mod1_dst="pitch",
            mod1_amt=-0.25,
            mod2_src="aenv",
            mod2_dst="pitch",
            mod2_amt=0.1,
            pitch_env=4.0,
            vibrato_depth=0.3,
        ),
        {},
    ),  # fmt: skip
    "mod_pitch_glide": (
        Patch(glide=0.15, mod3_src="velocity", mod3_dst="pitch", mod3_amt=0.4),
        {"from": 5, "vel": 0.6},
    ),
    # --- hard sync, 4-op FM, the newer wavetables,
    # analog drift
    "sync_saw": (
        Patch(osc2="saw", osc2_octave=0, osc2_sync="on", sync_pitch=7.0),
        {},
    ),
    "sync_square_pwm": (
        Patch(
            osc1="triangle",
            osc2="square",
            osc2_sync="on",
            sync_pitch=12.0,
            pwm_depth=0.4,
            pwm_rate=1.5,
            unison=3,
        ),
        {},
    ),  # fmt: skip
    "sync_triangle": (
        Patch(osc1="sine", osc2="triangle", osc2_sync="on", sync_pitch=5.0),
        {},
    ),
    "sync_sine_unison": (
        Patch(osc2="sine", osc2_sync="on", sync_pitch=17.0, unison=2),
        {},
    ),
    # the sweep: the filter envelope routed to sync_pitch (the JS control
    # pass), with cross-FM and ring on the synced osc2
    "sync_sweep": (
        Patch(
            osc2="saw",
            osc2_octave=0,
            osc2_sync="on",
            sync_pitch=2.0,
            mod1_src="fenv",
            mod1_dst="sync_pitch",
            mod1_amt=0.7,
            filter_env=3000.0,
            fm_amount=0.1,
            ring_mod=0.3,
        ),
        {},
    ),  # fmt: skip
    "sync_glide_bend": (
        Patch(
            osc2="square", osc2_sync="on", sync_pitch=9.0, glide=0.12, pitch_bend=0.4
        ),
        {"from": -5},
    ),
    "fm4_stack": (
        Patch(osc1="fm4", fm4_level3=0.3, fm4_level4=0.2, fm4_ratio3=3.0),
        {},
    ),
    # every fm4 field off its default
    "fm4_all": (
        Patch(
            osc1="fm4",
            fm4_algo="pairs",
            fm4_ratio1=2.0,
            fm4_ratio2=3.5,
            fm4_ratio3=0.5,
            fm4_ratio4=14.0,
            fm4_level1=0.8,
            fm4_level2=0.4,
            fm4_level3=0.7,
            fm4_level4=0.35,
            fm4_decay1=1.5,
            fm4_decay2=0.3,
            fm4_decay3=1.1,
            fm4_decay4=0.15,
            fm4_feedback=0.4,
        ),
        {},
    ),  # fmt: skip
    "fm4_three": (
        Patch(
            osc1="fm4",
            fm4_algo="three",
            fm4_level2=0.2,
            fm4_level3=0.15,
            fm4_level4=0.1,
            fm4_ratio4=7.0,
            fm4_feedback=0.6,
        ),
        {},
    ),  # fmt: skip
    "fm4_pair_plus": (
        Patch(
            osc1="fm4",
            fm4_algo="pair_plus",
            fm4_level3=0.6,
            fm4_level4=0.4,
            fm4_ratio3=2.0,
            fm4_ratio4=4.0,
            fm4_decay2=0.4,
        ),
        {},
    ),  # fmt: skip
    "fm4_additive": (
        Patch(
            osc1="fm4",
            fm4_algo="additive",
            fm4_ratio2=2.0,
            fm4_ratio3=3.0,
            fm4_ratio4=4.0,
            fm4_level2=0.7,
            fm4_level3=0.5,
            fm4_level4=0.4,
            fm4_feedback=0.3,
            unison=2,
        ),
        {},
    ),  # fmt: skip
    "fm4_osc2": (
        Patch(osc1="saw", osc2="fm4", osc2_octave=0, fm4_level2=0.5, fm_amount=0.1),
        {},
    ),
    **{
        f"wavetable_{t}": (
            Patch(osc1="wavetable", wt_table=t, wt_pos=pos, filter_type="none"),
            {},
        )
        for t, pos in (
            ("scan", 0.6),
            ("organ", 0.8),
            ("bell", 0.4),
            ("stack", 1.0),
            ("pulse", 0.7),
            ("formant", 0.3),
            ("crush", 0.5),
            ("analog", 0.9),
        )
    },
    "drift": (Patch(drift=0.8, unison=3, osc2="square", osc2_octave=0), {}),
    # every drifting shape and path: a synced osc2 (its master phase), a
    # wavetable, fm4, the JS control pass (a route), glide
    "drift_sync": (
        Patch(
            osc1="wavetable",
            wt_table="pulse",
            osc2="saw",
            osc2_sync="on",
            sync_pitch=5.0,
            drift=1.0,
        ),
        {},
    ),  # fmt: skip
    "drift_fm": (
        Patch(osc1="fm", osc2="fm4", fm4_level2=0.4, drift=0.7, unison=2),
        {},
    ),
    "drift_routes": (
        Patch(
            drift=0.9,
            glide=0.1,
            mod1_src="lfo",
            mod1_dst="cutoff",
            mod1_amt=0.3,
            cutoff=900.0,
        ),
        {"from": 3},
    ),  # fmt: skip
}

# --- the warp, your own wavetable, voicing
# Your own table for the cases: a sung-ish cycle morphing into a stepped one
# (8 frames), in the memory store as a made table is on disk
_CYC = np.linspace(0, 1, 2048, endpoint=False)
OWN_TABLE = wavetables.register(
    wavetables.spectra(
        [
            np.sin(2 * np.pi * _CYC) * (1 - m) + np.sign(np.sin(6 * np.pi * _CYC)) * m
            for m in np.linspace(0, 1, 8)
        ]
    )
)
CASES.update(
    {
        "warp_saw_bend": (Patch(osc1_warp="bend_pos", osc1_warp_amt=0.6), {}),
        "warp_sine_pd": (
            Patch(osc1="sine", osc1_warp="pd", osc1_warp_amt=0.8, filter_type="none"),
            {},
        ),
        "warp_square_mirror": (
            Patch(
                osc1="square",
                osc1_warp="mirror",
                osc1_warp_amt=0.5,
                pwm_depth=0.4,
                unison=3,
            ),
            {},
        ),  # fmt: skip
        "warp_triangle_quantize": (
            Patch(osc1="triangle", osc1_warp="quantize", osc1_warp_amt=0.6),
            {},
        ),
        "warp_bend_neg_glide": (
            Patch(osc1_warp="bend_neg", osc1_warp_amt=0.9, glide=0.1, pitch_env=7.0),
            {"from": -5},
        ),
        "warp_wavetable_asym": (
            Patch(
                osc1="wavetable",
                wt_table="vocal",
                wt_pos=0.4,
                osc1_warp="asym",
                osc1_warp_amt=0.7,
            ),
            {},
        ),  # fmt: skip
        # osc2's warp under a route (the JS control pass), cross-FM into osc1
        "warp_osc2": (
            Patch(
                osc2="sine",
                osc2_octave=0,
                osc2_warp="bend_both",
                osc2_warp_amt=0.5,
                fm_amount=0.1,
                mod1_src="lfo",
                mod1_dst="osc2_warp_amt",
                mod1_amt=0.4,
                mod_lfo_rate=3.0,
            ),
            {},
        ),  # fmt: skip
        "warp_both_pwm": (
            Patch(
                osc1="square",
                osc1_warp="bend_neg",
                osc1_warp_amt=0.3,
                osc2="saw",
                osc2_warp="pwm",
                osc2_warp_amt=0.5,
                unison=2,
            ),
            {},
        ),  # fmt: skip
        # what a warp does not bend: a synced osc2, the fm shape
        "warp_ignored": (
            Patch(
                osc1="fm",
                osc1_warp="mirror",
                osc1_warp_amt=0.5,
                osc2="saw",
                osc2_sync="on",
                sync_pitch=5.0,
                osc2_warp="pd",
                osc2_warp_amt=0.7,
            ),
            {},
        ),  # fmt: skip
        "wt_custom": (
            Patch(
                osc1="wavetable", wt_custom=OWN_TABLE, wt_pos=0.6, filter_type="none"
            ),
            {},
        ),
        "wt_custom_warp": (
            Patch(
                osc1="sine",
                osc2="wavetable",
                osc2_octave=0,
                wt_custom=OWN_TABLE,
                wt_pos=0.3,
                osc2_warp="pwm",
                osc2_warp_amt=0.4,
            ),
            {},
        ),  # fmt: skip
        # a table this machine doesn't hold plays "basic", on both sides
        "wt_custom_missing": (
            Patch(osc1="wavetable", wt_custom="0123456789abcdef", wt_pos=0.5),
            {},
        ),
        # one voice, on one note: no other key to take it from or glide from
        # (a phrase: tests/studio/test_live_voicing.py)
        "voice_legato": (
            Patch(voice_mode="legato", glide_mode="legato", glide=0.1),
            {},
        ),
        "voice_mono": (Patch(voice_mode="mono", glide=0.05), {}),
    }
)

# --- the general filter types, filter 2, and the
# modulation — LFO 2 / 3 and the general shapes, envelopes 3 / 4, the
# wheels, the key, random, route slots 5-12 and their new targets
_DRAW = "0:-1,0.1:1,0.3:0.2,0.6:-0.4"
CASES.update(
    {
        "f_lowpass24": (
            Patch(filter_type="lowpass24", cutoff=800.0, resonance=0.7),
            {},
        ),
        "f_highpass24": (
            Patch(
                filter_type="highpass24", cutoff=600.0, resonance=0.5, filter_env=0.0
            ),
            {},
        ),
        # tuned to the key (key track 1), and swept by the envelope
        "f_comb_plus": (
            Patch(
                filter_type="comb_plus",
                cutoff=261.63,
                key_track=1.0,
                resonance=0.85,
                filter_env=0.0,
            ),
            {},
        ),  # fmt: skip
        "f_comb_minus": (
            Patch(
                filter_type="comb_minus",
                cutoff=400.0,
                resonance=0.7,
                filter_env=1500.0,
                filter_decay=0.4,
            ),
            {},
        ),  # fmt: skip
        "f_phaser": (
            Patch(
                filter_type="phaser",
                cutoff=900.0,
                resonance=0.6,
                filter_lfo_depth=600.0,
                filter_lfo_rate=1.5,
            ),
            {},
        ),  # fmt: skip
        "f_diode": (
            Patch(filter_type="diode", cutoff=700.0, resonance=0.8, drive=2.5),
            {},
        ),
        "f_peak": (Patch(filter_type="peak", cutoff=1500.0, resonance=0.6), {}),
        "f2_serial": (
            Patch(
                filter2_route="serial",
                filter2_type="highpass",
                filter2_cutoff=300.0,
                filter2_resonance=0.4,
                filter2_env=800.0,
                filter2_key_track=0.5,
            ),
            {},
        ),  # fmt: skip
        # unison 3: both channels through each filter
        "f2_parallel": (
            Patch(
                filter_type="lowpass",
                cutoff=500.0,
                filter2_route="parallel",
                filter2_type="bandpass",
                filter2_cutoff=2500.0,
                filter2_resonance=0.6,
                unison=3,
            ),
            {},
        ),  # fmt: skip
        "f2_comb_vowel": (
            Patch(
                filter_type="none",
                filter2_route="serial",
                filter2_type="comb_plus",
                filter2_cutoff=261.63,
                filter2_key_track=1.0,
                filter2_resonance=0.9,
                formant_amount=0.5,
                drift=0.5,
            ),
            {},
        ),  # fmt: skip
        # a post drive (filter 1 is no ladder) and filter 2's diode reading it
        "f2_diode_drive": (
            Patch(
                filter_type="lowpass",
                drive=2.0,
                filter2_route="serial",
                filter2_type="diode",
                filter2_cutoff=1500.0,
                filter2_resonance=0.7,
            ),
            {},
        ),  # fmt: skip
        "f2_phaser_peak": (
            Patch(
                filter_type="phaser",
                cutoff=700.0,
                filter2_route="parallel",
                filter2_type="peak",
                filter2_cutoff=3000.0,
                filter2_resonance=0.5,
            ),
            {},
        ),  # fmt: skip
        "f2_routes": (
            Patch(
                filter2_route="serial",
                filter2_type="lowpass24",
                mod1_src="lfo2",
                mod1_dst="filter2_cutoff",
                mod1_amt=0.4,
                lfo2_rate=3.0,
                mod2_src="env3",
                mod2_dst="filter2_resonance",
                mod2_amt=0.6,
                env3_attack=0.2,
                env3_sustain=0.4,
            ),
            {},
        ),  # fmt: skip
        # LFO 2 and 3: shapes, phases, free running
        "lfo23": (
            Patch(
                lfo2_rate=4.0,
                lfo2_shape="triangle",
                lfo2_phase=0.25,
                mod1_src="lfo2",
                mod1_dst="osc1_detune",
                mod1_amt=0.5,
                lfo3_rate=1.5,
                lfo3_shape="square",
                lfo3_phase=0.6,
                lfo3_mode="free",
                mod2_src="lfo3",
                mod2_dst="cutoff",
                mod2_amt=0.3,
            ),
            {},
        ),  # fmt: skip
        "lfo_free2": (
            Patch(
                lfo2_mode="free",
                lfo2_shape="saw",
                mod1_src="lfo2",
                mod1_dst="osc1_level",
                mod1_amt=-0.4,
            ),
            {"pitch": "D4"},
        ),
        "lfo_sync23": (
            Patch(
                lfo2_sync="1/8",
                mod1_src="lfo2",
                mod1_dst="osc1_detune",
                mod1_amt=0.4,
                lfo3_sync="1/4",
                mod2_src="lfo3",
                mod2_dst="cutoff",
                mod2_amt=0.3,
            ),
            {},
        ),  # fmt: skip
        "lfo_drawn": (
            Patch(
                lfo_shape="drawn",
                lfo_draw=_DRAW,
                mod_lfo_rate=3.0,
                mod1_src="lfo",
                mod1_dst="cutoff",
                mod1_amt=0.5,
            ),
            {},
        ),  # fmt: skip
        "lfo_drawn23": (
            Patch(
                lfo2_shape="drawn",
                lfo2_draw="0:0,0.25:1,0.5:0,0.75:-1",
                lfo2_rate=5.0,
                mod1_src="lfo2",
                mod1_dst="osc1_detune",
                mod1_amt=0.6,
                lfo3_shape="drawn",
                lfo3_draw="0.2:1,0.7:-1",
                lfo3_rate=2.0,
                mod2_src="lfo3",
                mod2_dst="osc1_level",
                mod2_amt=-0.3,
            ),
            {},
        ),  # fmt: skip
        "lfo_chaos": (
            Patch(
                lfo2_shape="chaos",
                lfo2_rate=9.0,
                mod1_src="lfo2",
                mod1_dst="cutoff",
                mod1_amt=0.4,
                lfo_shape="chaos",
                mod_lfo_rate=5.0,
                mod2_src="lfo",
                mod2_dst="osc1_detune",
                mod2_amt=0.4,
            ),
            {},
        ),  # fmt: skip
        "lfo_smooth": (
            Patch(
                lfo3_shape="smooth",
                lfo3_rate=6.0,
                mod1_src="lfo3",
                mod1_dst="pitch",
                mod1_amt=0.05,
                lfo_shape="smooth",
                mod_lfo_rate=4.0,
                mod2_src="lfo",
                mod2_dst="cutoff",
                mod2_amt=0.4,
            ),
            {},
        ),  # fmt: skip
        "lfo_noise2": (
            Patch(
                lfo2_shape="noise",
                lfo2_rate=7.0,
                mod1_src="lfo2",
                mod1_dst="cutoff",
                mod1_amt=0.5,
            ),
            {},
        ),
        # every env 3 / 4 field, and their releases from the gate
        "env34": (
            Patch(
                env3_attack=0.15,
                env3_decay=0.25,
                env3_sustain=0.3,
                env3_release=0.4,
                env3_curve=3.5,
                mod1_src="env3",
                mod1_dst="cutoff",
                mod1_amt=0.5,
                env4_attack=0.05,
                env4_decay=0.6,
                env4_sustain=0.5,
                env4_release=0.15,
                env4_curve=1.0,
                mod2_src="env4",
                mod2_dst="osc1_level",
                mod2_amt=-0.5,
                release=0.5,
            ),
            {},
        ),  # fmt: skip
        "wheels": (
            Patch(
                mod_wheel=0.4,
                mod1_src="wheel",
                mod1_dst="vibrato_depth",
                mod1_amt=0.6,
                aftertouch=0.7,
                formant_amount=0.6,
                mod2_src="aftertouch",
                mod2_dst="formant_vowel",
                mod2_amt=0.8,
            ),
            {},
        ),  # fmt: skip
        # the live CC: the wheel moved mid-note, as a drawn curve stepping there
        "wheel_live": (
            Patch(mod_wheel=0.1, mod1_src="wheel", mod1_dst="cutoff", mod1_amt=0.5),
            {"cc": (0.3, "modwheel", 0.7)},
        ),
        "aftertouch_live": (
            Patch(
                osc1="square", mod1_src="aftertouch", mod1_dst="pwm_depth", mod1_amt=0.8
            ),
            {"cc": (0.25, "aftertouch", 0.6)},
        ),
        "key_random": (
            Patch(
                mod1_src="key",
                mod1_dst="cutoff",
                mod1_amt=0.5,
                mod2_src="random",
                mod2_dst="osc1_detune",
                mod2_amt=0.7,
            ),
            {"pitch": "C6"},
        ),
        # route slots 5-12, each to one of the later targets
        "mod5": (
            Patch(
                osc1="wavetable",
                wt_table="scan",
                mod5_src="lfo2",
                mod5_dst="wt_pos",
                mod5_amt=0.6,
            ),
            {},
        ),
        "mod6": (
            Patch(
                formant_amount=0.6,
                mod6_src="env3",
                mod6_dst="formant_vowel",
                mod6_amt=0.9,
            ),
            {},
        ),
        "mod7": (
            Patch(
                aftertouch=0.4, mod7_src="aftertouch", mod7_dst="pitch", mod7_amt=0.3
            ),
            {},
        ),
        "mod8": (
            Patch(mod8_src="key", mod8_dst="resonance", mod8_amt=0.7),
            {"pitch": "C2"},
        ),
        "mod9": (
            Patch(
                osc1="fm",
                mod9_src="env4",
                mod9_dst="fm_index",
                mod9_amt=0.4,
                env4_attack=0.2,
            ),
            {},
        ),
        "mod10": (
            Patch(
                osc1="sine",
                osc2="sine",
                osc2_octave=0,
                fm_amount=0.05,
                mod10_src="lfo3",
                mod10_dst="fm_amount",
                mod10_amt=0.3,
            ),
            {},
        ),
        "mod11": (
            Patch(mod11_src="random", mod11_dst="osc1_detune", mod11_amt=-0.8),
            {"pitch": "E4"},
        ),
        "mod12": (
            Patch(
                osc1="fm4",
                fm4_level2=0.2,
                mod_wheel=0.6,
                mod12_src="wheel",
                mod12_dst="fm4_level2",
                mod12_amt=0.5,
            ),
            {},
        ),
        "fm4_routes": (
            Patch(
                osc1="fm4",
                fm4_level3=0.2,
                fm4_level4=0.1,
                mod1_src="env3",
                mod1_dst="fm4_level1",
                mod1_amt=-0.4,
                mod2_src="lfo2",
                mod2_dst="fm4_level3",
                mod2_amt=0.3,
                mod3_src="velocity",
                mod3_dst="fm4_level4",
                mod3_amt=0.3,
                env3_sustain=0.5,
            ),
            {"vel": 0.8},
        ),  # fmt: skip
    }
)

# The live sampler's cases play this: a C4 tone (harmonics 1/k, a slow
# decay), the same in both channels — one channel on the wire. 2.4 s, so a
# note still sounds through the window `metrics` measures (0.5 - 0.95 s)
# an octave up and from a chop: at 0.8 s those cases compared silence with
# silence there, and only the envelope held them.
_T = np.arange(int(2.4 * SR)) / SR
_TONE = sum(np.sin(2 * np.pi * 261.63 * k * _T) / k for k in range(1, 9)) * 0.3
SAMPLE = sampler.register(_TONE * np.exp(-_T / 1.5))
_SMP = {"osc1": "sampler", "sampler_sample": SAMPLE}
# A true-stereo sample: the left is that tone, the right a quieter one a
# fifth up (odd harmonics, a faster decay) — so a voice that summed the
# channels, swapped them or played one on both sides fails level and
# harmonics on each side.
_RIGHT = sum(np.sin(2 * np.pi * 392.445 * k * _T) / k for k in (1, 3, 5, 7)) * 0.2
STEREO = sampler.register(
    np.column_stack([_TONE * np.exp(-_T / 1.5), _RIGHT * np.exp(-_T / 0.9)])
)
_ST = {"osc1": "sampler", "sampler_sample": STEREO}
CASES.update(
    {
        # root C3 under a C4 key: an octave up, onto the key's even harmonics
        "sampler": (
            Patch(**_SMP, sampler_root=48, sampler_start=0.05, sampler_end=0.9),
            {},
        ),
        "sampler_loop": (
            Patch(
                **_SMP,
                sampler_loop="on",
                sampler_loop_start=0.3,
                sampler_loop_end=0.7,
                sampler_xfade=0.02,
            ),
            {},
        ),  # fmt: skip
        "sampler_keytrack": (Patch(**_SMP, sampler_keytrack="off"), {}),
        "sampler_chops": (Patch(**_SMP, sampler_slices="0,0.5,0,0;0.5,1,0,1"), {}),
        # a start point inside the loop: `_loop_source` unrolls the first
        # pass ([x[:s0], the faded first pass, the loop]) and the voice
        # wraps behind it
        "sampler_loop_inside": (
            Patch(
                **_SMP,
                sampler_start=0.2,
                sampler_loop="on",
                sampler_loop_start=0.15,
                sampler_loop_end=0.3,
                sampler_xfade=0.02,
            ),
            {},
        ),  # fmt: skip
        # --- true stereo: both channels on the wire, a stereo pair in the voice
        "sampler_stereo": (
            Patch(**_ST, sampler_root=48, sampler_start=0.05, sampler_end=0.9),
            {},
        ),
        "sampler_stereo_loop": (
            Patch(
                **_ST,
                sampler_start=0.2,
                sampler_loop="on",
                sampler_loop_start=0.15,
                sampler_loop_end=0.3,
                sampler_xfade=0.02,
                filter_type="none",
            ),
            {},
        ),  # fmt: skip
        # root B3: the C4 key is the second chop, an octave down, reversed
        "sampler_stereo_chops": (
            Patch(**_ST, sampler_slices="0,0.5,0,0;0.5,1,-12,1", sampler_root=59),
            {},
        ),
        # the stereo pair through everything _osc_stack does with it: osc2
        # ringing and cross-modulating it, the sub, unison copies panned by
        # the stereo law, a driven ladder per channel, the tract
        "sampler_stereo_stack": (
            Patch(
                **_ST,
                osc1_level=0.7,
                osc2="sine",
                osc2_octave=0,
                osc2_level=0.3,
                ring_mod=0.4,
                fm_amount=0.1,
                sub_level=0.3,
                unison=3,
                unison_detune=10.0,
                unison_spread=0.6,
                drive=2.5,
                resonance=0.4,
                formant_amount=0.5,
                formant_vowel=0.4,
            ),
            {},
        ),  # fmt: skip
        # the read rate following the live pitch: a glide in, the wheel an
        # octave up mid-note
        "sampler_stereo_bend": (
            Patch(**_ST, filter_type="lowpass", glide=0.1, bend_range=12.0),
            {"from": 3, "wheel": (0.3, 12.0), "vel": 0.7},
        ),
        # the grain engines' fields, while no sampler plays: never read
        "sampler_unheard": (Patch(sampler_mode="formant", sampler_stretch=2.0), {}),
    }
)

# --- formant mode: the engine's grain train in the voice (grain-worklet.js)
# A voice-like source for it: a sung "ah" on C4 — a pulse train (harmonics
# 1/k) through three vowel resonances, with a 5.5 Hz vibrato of a third of
# a semitone and a slow swell, so the grains are cut from audio whose own
# period wanders about the root's, as a recording's does. The right channel
# is the same voice a little later and darker: true stereo.
_VIB = 261.63 * 2 ** (0.33 * np.sin(2 * np.pi * 5.5 * _T) / 12)
_PH = 2 * np.pi * np.cumsum(_VIB) / SR


def _vowel(phase: np.ndarray, tilt: float) -> np.ndarray:
    out = np.zeros(len(phase))
    for k in range(1, 40):
        hz = 261.63 * k
        gain = sum(
            a / (1 + ((hz - f) / bw) ** 2)
            for f, bw, a in ((730, 90, 1.0), (1090, 110, 0.5), (2440, 160, 0.25))
        )
        out += (0.04 + gain) / k**tilt * np.sin(k * phase)
    return out * (0.25 / np.max(np.abs(out)))


_SWELL = (0.7 + 0.3 * np.sin(2 * np.pi * 0.8 * _T)) * np.exp(-_T / 2.5)
VOICE = sampler.register(_vowel(_PH, 0.6) * _SWELL)
VOICE_ST = sampler.register(
    np.column_stack(
        [_vowel(_PH, 0.6) * _SWELL, np.roll(_vowel(_PH, 1.1) * _SWELL, 441) * 0.8]
    )
)
_FM = {**_SMP, "sampler_mode": "formant"}
_FV = {"osc1": "sampler", "sampler_mode": "formant", "sampler_sample": VOICE}
_FVS = {**_FV, "sampler_sample": VOICE_ST}
CASES.update(
    {
        # at the root: the grains tile the source, the voice as recorded
        "sampler_formant_root": (Patch(**_FV), {}),
        # root C3 under a C4 key: an octave up, the grains twice as close;
        # a start point, and the end
        "sampler_formant_up": (
            Patch(**_FM, sampler_root=48, sampler_start=0.05, sampler_end=0.9),
            {},
        ),
        # root G4: a fifth down, the gaps made up by the overlap's root
        "sampler_formant_down": (Patch(**_FM, sampler_root=67), {}),
        "sampler_formant_voice_up": (Patch(**_FVS, sampler_root=48), {}),
        "sampler_formant_voice_down": (
            Patch(**_FVS, sampler_root=67, filter_type="none"),
            {},
        ),
        "sampler_formant_loop": (
            Patch(
                **_FM,
                sampler_root=53,
                sampler_loop="on",
                sampler_loop_start=0.1,
                sampler_loop_end=0.25,
                sampler_xfade=0.02,
            ),
            {},
        ),  # fmt: skip
        # a start point inside the loop (the unrolled first pass), stereo
        "sampler_formant_stereo_loop": (
            Patch(
                **_FVS,
                sampler_root=55,
                sampler_start=0.2,
                sampler_loop="on",
                sampler_loop_start=0.15,
                sampler_loop_end=0.3,
                sampler_xfade=0.02,
                filter_type="none",
            ),
            {},
        ),  # fmt: skip
        "sampler_formant_stereo": (
            Patch(**{**_ST, "sampler_mode": "formant"}, sampler_root=48),
            {},
        ),
        # the source clock at half and at double speed under the same pitch
        "sampler_formant_stretch": (
            Patch(**_FV, sampler_root=55, sampler_stretch=2.0),
            {},
        ),
        "sampler_formant_squeeze": (
            Patch(**_FVS, sampler_root=65, sampler_stretch=0.6),
            {},
        ),
        # root B3: the C4 key is the second chop, an octave down, reversed
        "sampler_formant_chops": (
            Patch(
                **{**_ST, "sampler_mode": "formant"},
                sampler_slices="0,0.5,0,0;0.5,1,-12,1",
                sampler_root=59,
            ),
            {},
        ),
        "sampler_formant_keytrack": (
            Patch(**_FV, sampler_keytrack="off", unison=3),
            {},
        ),
        # The pitch moving under the grain schedule. `_psola` reads it one
        # root period ahead of the sample it is laying (at each grain's
        # centre); the voice foresees its own motion that far
        # (grain-worklet.js `ahead`), so all of it is the engine's samples:
        # a glide in, vibrato, the pitch envelope, a held bend, unison
        # copies detuned apart — on the voice, and with a low root (C2: a
        # root period of 15 ms to see across).
        "sampler_formant_voice_glide": (
            Patch(
                **_FVS,
                glide=0.15,
                vibrato_depth=0.4,
                vibrato_rate=5.5,
                vibrato_delay=0.1,
                pitch_env=-3.0,
                pitch_env_decay=0.3,
                pitch_bend=-0.5,
                bend_range=7.0,
                unison=2,
                unison_detune=14.0,
            ),
            {"from": -5},
        ),  # fmt: skip
        "sampler_formant_low_root": (
            Patch(
                **_FM,
                sampler_root=36,
                glide=0.1,
                vibrato_depth=0.6,
                vibrato_rate=4.0,
                vibrato_delay=0.0,
                pitch_env=7.0,
                osc1_detune=-31.0,
            ),
            {"from": 7},
        ),  # fmt: skip
        # The wheel mid-note is the one motion nobody foresees: it reaches
        # the schedule up to a root period late (and smoothed, as it reaches
        # every oscillator), and from then on the grains run at the right
        # spacing from another phase than the engine's. Bent up on a tone
        # that is periodic at the root — grains overlapping, each like the
        # last — a phase is all it is, and the numbers hold: this case.
        # Bent down (the gaps ripple the 10 ms envelope at the played
        # period) or on a voice whose period wanders (each grain is cut
        # somewhere else), they do not, for the voice and for the engine
        # alike: `_engine` against itself with the wheel's step one render
        # quantum (2.9 ms) later is off by 2.3 dB RMS / 7.4 dB max of
        # harmonics and 2.4 dB of envelope p95 on VOICE_ST bent 7 st down
        # (the voice against it: 2.4 / 6.8 / 2.5), and exact when the step
        # moves by less than the gap between two grain centres.
        "sampler_formant_bend": (
            Patch(
                **{**_ST, "sampler_mode": "formant"},
                filter_type="lowpass",
                glide=0.1,
                bend_range=12.0,
            ),
            {"from": 3, "wheel": (0.3, 12.0), "vel": 0.7},
        ),  # fmt: skip
    }
)

# A zone map (a multi-sample instrument): the mono tone low,
# the stereo pair on the high keys as take 0, the voice source as take 1,
# and a hard layer (the tone, its own span and loop) from velocity 90 —
# so the key, the velocity crossfade and the strike (`rr`: the noteOn's
# own, as sampler.Strikes counts it in a phrase) each pick other audio.
ZONES = (
    f"{SAMPLE},0,59,48,0,127,0,-2;{STEREO},60,127,60,0,127,0,0,15;"
    f"{VOICE},60,127,57,0,127,1;{SAMPLE},60,127,60,90,127,0,1.5,0,0.05,0.9,0.3,0.6"
)
_ZN = {"osc1": "sampler", "sampler_zones": ZONES, "filter_type": "lowpass"}
CASES.update(
    {
        "sampler_zones_low": (Patch(**_ZN), {"pitch": "C3", "vel": 0.6}),
        "sampler_zones_take": (Patch(**_ZN), {"vel": 0.6, "rr": 1}),
        "sampler_zones_xfade": (Patch(**_ZN), {"vel": 0.8, "rr": 2}),
        "sampler_zones_formant": (
            Patch(**_ZN, sampler_mode="formant"),
            {"vel": 0.8, "rr": 2},
        ),
    }
)

# Every field the gate lets through (WORKLET_FIELDS, and every GATED
# predicate) names the case that proves it.
FIELD_CASES: dict[str, str] = {
    "osc1": "osc_square",
    "osc1_level": "level",
    "osc1_octave": "octave_detune",
    "osc1_detune": "octave_detune",
    "osc2": "osc2",
    "osc2_level": "osc2",
    "osc2_octave": "osc2_sine_up",
    "osc2_detune": "osc2",
    "sub_level": "sub",
    "noise_level": "noise_white",
    "noise_color": "noise_pink",
    "attack": "amp_env",
    "decay": "amp_env",
    "sustain": "amp_env",
    "release": "amp_env",
    "env_curve": "env_linear",
    "cutoff": "cutoff_reso",
    "resonance": "cutoff_reso",
    "filter_type": "bandpass",
    "filter_env": "filter_env",
    "filter_attack": "filter_env",
    "filter_decay": "filter_env",
    "filter_sustain": "filter_env",
    "filter_release": "filter_release",
    "key_track": "key_track",
    "drive": "drive_ladder",
    "glide": "glide",
    "unison": "unison",
    "unison_detune": "unison_wide",
    "unison_spread": "unison",
    "vibrato_depth": "vibrato",
    "vibrato_rate": "vibrato",
    "vibrato_delay": "vibrato",
    "pwm_depth": "pwm",
    "pwm_rate": "pwm",
    "tremolo_depth": "tremolo",
    "tremolo_rate": "tremolo",
    "filter_lfo_depth": "filter_lfo",
    "filter_lfo_rate": "filter_lfo",
    "filter_lfo_shape": "flfo_noise",
    "filter_lfo_sync": "flfo_sync",
    "pitch_env": "pitch_env",
    "pitch_env_decay": "pitch_env",
    "fm_amount": "fm",
    "ring_mod": "ring",
    "fm_ratio": "osc_fm",
    "fm_index": "osc_fm",
    "fm_decay": "osc_fm",
    "pluck_damping": "pluck",
    "pluck_bright": "pluck",
    "wt_table": "wavetable",
    "wt_pos": "wavetable",
    "formant_amount": "formant",
    "formant_vowel": "formant",
    "formant_size": "formant",
    "mod_lfo_rate": "mod1",
    "lfo_shape": "lfo_square",
    "lfo_sync": "lfo_sync",
    "pitch_bend": "pitch_bend",
    "bend_range": "pitch_bend",
    "sampler_sample": "sampler",
    "sampler_root": "sampler",
    "sampler_start": "sampler",
    "sampler_end": "sampler",
    "sampler_keytrack": "sampler_keytrack",
    "sampler_loop": "sampler_loop",
    "sampler_loop_start": "sampler_loop",
    "sampler_loop_end": "sampler_loop",
    "sampler_xfade": "sampler_loop",
    "sampler_slices": "sampler_chops",
    "sampler_mode": "sampler_formant_root",
    "sampler_stretch": "sampler_formant_stretch",
    "sampler_zones": "sampler_zones_xfade",
    "osc2_sync": "sync_saw",
    "sync_pitch": "sync_saw",
    "drift": "drift",
    "osc1_warp": "warp_saw_bend",
    "osc1_warp_amt": "warp_saw_bend",
    "osc2_warp": "warp_osc2",
    "osc2_warp_amt": "warp_osc2",
    "wt_custom": "wt_custom",
    "voice_mode": "voice_legato",
    "glide_mode": "voice_legato",
    **{
        f: "fm4_all"
        for f in ("fm4_algo", "fm4_feedback")
        + tuple(f"fm4_{k}{i}" for k in ("ratio", "level", "decay") for i in range(1, 5))
    },
    **{f"{s}_{k}": s for s in MOD_SLOTS for k in ("src", "dst", "amt")},
    # filter 2, the general modulators
    **{
        f"filter2_{k}": "f2_serial"
        for k in ("route", "type", "cutoff", "resonance", "key_track", "env")
    },
    **{
        f"env{e}_{k}": "env34"
        for e in (3, 4)
        for k in ("attack", "decay", "sustain", "release", "curve")
    },
    **{f"lfo{e}_{k}": "lfo23" for e in (2, 3) for k in ("rate", "shape", "phase")},
    "lfo2_sync": "lfo_sync23",
    "lfo3_sync": "lfo_sync23",
    "lfo2_mode": "lfo_free2",
    "lfo3_mode": "lfo23",
    "lfo_draw": "lfo_drawn",
    "lfo2_draw": "lfo_drawn23",
    "lfo3_draw": "lfo_drawn23",
    "mod_wheel": "wheels",
    "aftertouch": "wheels",
}


def _recipe_rows() -> list[tuple[str, Patch]]:
    """Every genre recipe's main patch and each layer's (a key plays the
    edited lane in the worklet, whichever lane of a recipe that is)."""
    rows = []
    for e in RECIPES:
        rows.append((f"recipe:{e['name']}", Patch.from_dict(e["patch"])))
        for lay in e["layers"] or []:
            rows.append(
                (f"recipe:{e['name']}/{lay['role']}", Patch.from_dict(lay["patch"]))
            )
    return rows


def _all_rows() -> list[tuple[str, Patch]]:
    rows = [(e["name"], get_factory(e["name"])) for e in FACTORY]
    rows += [(e["name"], get_library(e["name"])) for e in LIBRARY]
    return rows + _recipe_rows()


def corpus() -> list[tuple[str, Patch]]:
    return [(n, p) for n, p in _all_rows() if worklet.can_play(p)]


def is_random(p: Patch) -> bool:
    """Does any sample of this patch come from numpy's generator?"""
    noisy_route = any(
        slot_active(p, s) and getattr(p, f"{s}_dst") == "noise_level" for s in MOD_SLOTS
    )
    return p.noise_level > 0 or "noise" in (p.osc1, p.osc2) or noisy_route


def strip(p: Patch) -> Patch | None:
    """`p` with every random source silenced (None: nothing would be left)."""
    kw: dict = {"noise_level": 0.0}
    for s in MOD_SLOTS:
        if getattr(p, f"{s}_dst") == "noise_level":
            kw[f"{s}_amt"] = 0.0
    if p.osc2 == "noise":
        kw.update(osc2=None, fm_amount=0.0, ring_mod=0.0)
    if p.osc1 == "noise":
        if kw.get("osc2", p.osc2) is None and p.sub_level <= 0:
            return None
        kw["osc1_level"] = 0.0
    return replace(p, **kw)


def _jobs():
    """(tag, patch, pitch, opts, random): deterministic jobs at GATE, random
    ones twice — stripped (deterministic) and whole (statistical)."""
    rows = [
        (f"case:{cid}", p, o.get("pitch", "C4"), o) for cid, (p, o) in CASES.items()
    ]
    for name, p in corpus():
        rows += [(f"sound:{name}", p, pitch, {}) for pitch in PITCHES]
    jobs = []
    for tag, p, pitch, opts in rows:
        p = at_bpm(p, BPM)  # what /patch.json and /note both hand over
        if not is_random(p):
            jobs.append((tag, p, pitch, opts, False))
            continue
        stripped = strip(p)
        if stripped is not None:
            jobs.append((tag + "|stripped", stripped, pitch, opts, False))
        jobs.append((tag, p, pitch, opts, True))
    return jobs


def _engine(
    p: Patch, pitch: str, opts: dict, gate: float, reseed: int = 0
) -> np.ndarray:
    """Synth.note as /note renders it. `reseed` shifts every noise seed the
    engine draws (core.noise), for more realisations of a random source."""
    dur = gate + float(p.release)
    frm = None
    if "from" in opts:  # a MIDI number: note_to_hz reads any float as one
        frm = 69 + 12 * np.log2(note_to_hz(pitch) / 440) + opts["from"]
    if "wheel" in opts:  # the wheel's move as the drawn bend it records
        at, st = opts["wheel"]
        n = core.n_samples(dur, SR)
        base = float(p.pitch_bend)
        bend = np.where(np.arange(n) >= round(at * SR), base + st / p.bend_range, base)
        p = replace(p, pitch_bend=bend)
    if "cc" in opts:  # the mod wheel / aftertouch's move: the live value adds
        at, kind, v = opts["cc"]
        field = "mod_wheel" if kind == "modwheel" else "aftertouch"
        n = core.n_samples(dur, SR)
        base = float(getattr(p, field))
        moved = np.where(np.arange(n) >= round(at * SR), min(1.0, base + v), base)
        p = replace(p, **{field: moved})
    plain = core.noise

    def noise(n, color="white", seed=None):
        return plain(n, color, None if seed is None else seed + reseed)

    core.noise = noise
    try:
        return Synth(p).note(
            pitch,
            dur,
            velocity=opts.get("vel", 1.0),
            gate=gate,
            from_pitch=frm,
            rr=opts.get("rr", 0),
        )
    finally:
        core.noise = plain


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """(tag, pitch) -> {"random": bool, "js": metrics, "wasm": metrics,
    "js_wasm": worst sample error / peak}. Each job is reduced to its numbers
    as soon as it is read back and its audio dropped: holding every job's
    engine, JS and WASM renders at once (float64 stereo, x4 seeds for the
    random ones) took the suite to ~8 GB."""
    tmp = tmp_path_factory.mktemp("parity")
    specs, want = [], {}
    for i, (tag, p, pitch, opts, rand) in enumerate(_jobs()):
        gate = GATE_RANDOM if rand else GATE
        eng = _engine(p, pitch, opts, gate)
        if rand:  # as many engine realisations as live ones
            eng = [eng] + [
                _engine(p, pitch, opts, gate, 7919 * k) for k in range(1, len(SEEDS))
            ]
        spec = {
            "freq": note_to_hz(pitch),
            "vel": opts.get("vel", 1.0),
            "p": {
                **p.to_json(),
                **worklet.voice_tables(),
                "sampler_live": sampler.live_url(p),  # as /patch.json sends it
                "wt_live": wavetables.live_url(p),
            },
            "gate": round(gate * SR),
            "total": len(eng[0] if rand else eng),
            "seeds": list(SEEDS) if rand else [SEEDS[0]],
            "out": str(tmp / f"{i}.f32"),
        }
        if "wheel" in opts:
            spec["wheelAt"] = round(opts["wheel"][0] * SR)
            spec["wheelSt"] = opts["wheel"][1]
        if "cc" in opts:
            spec["ccAt"] = round(opts["cc"][0] * SR)
            spec["ccType"], spec["ccV"] = opts["cc"][1], opts["cc"][2]
        if "from" in opts:
            spec["fromFreq"] = note_to_hz(pitch) * 2 ** (opts["from"] / 12)
        if "rr" in opts:  # the strike, as the noteOn may carry it
            spec["rr"] = opts["rr"]
        url = spec["p"]["sampler_live"]
        if url:  # what GET <url> answers
            query = dict(parse_qsl(url.split("?", 1)[1]))
            path = tmp / f"{i}.smp"
            path.write_bytes(
                sampler.live_payload(p.sampler_sample, sampler.live_settings(query))
            )
            spec["sampler"] = str(path)
        if spec["p"]["wt_live"]:  # what GET /wavetable/live/<id>.bin answers
            path = tmp / f"{i}.wt"
            path.write_bytes(wavetables.live_payload(p.wt_custom))
            spec["wtable"] = str(path)
        specs.append(spec)
        np.save(tmp / f"{i}.eng.npy", np.stack(eng) if rand else eng)
        want[(tag, pitch)] = (tmp / f"{i}.eng.npy", spec["out"], len(spec["seeds"]))
        del eng
    (tmp / "jobs.json").write_text(json.dumps(specs), encoding="utf-8")
    (tmp / "harness.cjs").write_text(HARNESS, encoding="utf-8")
    (tmp / "tables.bin").write_bytes(voicetables.payload())
    cmd = ["node", str(tmp / "harness.cjs"), str(WORKLET), str(tmp / "jobs.json"),
           str(tmp / "tables.bin")]  # fmt: skip
    # both engines at once: the JS voice, and the same file with the module
    procs = [subprocess.Popen(cmd), subprocess.Popen([*cmd, str(WASM)])]
    for proc in procs:
        assert proc.wait(timeout=900) == 0, "the node harness failed"
    out = {}
    for key, (eng_path, path, k) in want.items():
        eng = np.load(eng_path)
        n = eng.shape[1] if k > 1 else len(eng)
        live = {}
        for engine in ENGINES:
            raw = np.fromfile(f"{path}.{engine}", dtype=np.float32).astype(np.float64)
            runs = raw.reshape(k, 2, n)  # seeds x (L, R) x samples
            live[engine] = [np.column_stack([r[0], r[1]]) for r in runs]
        res = {"random": k > 1, "js_wasm": 0.0}
        for engine in ENGINES:
            runs = live[engine]
            res[engine] = (
                stat_metrics(list(eng), runs, GATE_RANDOM)
                if k > 1
                else metrics(eng, runs[0], key[1])
            )
        for js, wasm in zip(live["js"], live["wasm"], strict=True):
            peak = max(float(np.max(np.abs(js))), 1e-9)
            err = float(np.max(np.abs(js - wasm))) / peak
            # a NaN would vanish in max(): make it the worst error there is
            res["js_wasm"] = max(res["js_wasm"], err if np.isfinite(err) else np.inf)
        out[key] = res
    return out


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x)))) + 1e-12


def _spectrum_db(x):
    return 20 * np.log10(np.abs(np.fft.rfft(x * np.hanning(len(x)))) + 1e-12)


def metrics(eng: np.ndarray, live: np.ndarray, pitch: str) -> dict:
    """(level dB, harmonic RMS dB, harmonic max dB, envelope p95 dB), per
    channel, worst of the two. "Harmonics" are the multiples of f0 / 4:
    an octave-down oscillator, the sub (f0 / 2) and osc2 two octaves down
    all sit on that grid, and at C6 they are most of the sound. A line
    counts when it is within 60 dB of the loudest line and 80 dB of the
    spectrum's peak — below that both sides are numerical floor (float32
    out of the worklet, float64 out of numpy), not sound.
    The window is the held note's middle (0.5-0.95 s); a sound that has
    died away by then (a strike with no sustain: Struck Metal sat 82-90 dB
    under its attack there, and its "harmonics" were both sides' floor) is
    measured from just after its attack instead (`_window`)."""
    worst = {"level": 0.0, "harm_rms": 0.0, "harm_max": 0.0, "env_p95": 0.0}
    a, b = _window(eng)
    f0 = note_to_hz(pitch) / 4
    for ch in (0, 1):
        e, w = eng[:, ch], live[:, ch]
        lvl = 20 * np.log10(_rms(w[a:b]) / _rms(e[a:b]))
        E, W = _spectrum_db(e[a:b]), _spectrum_db(w[a:b])
        f = np.fft.rfftfreq(b - a, 1 / SR)
        hs = []
        for h in range(1, 4000):
            if h * f0 > 12000:
                break
            k = int(np.argmin(np.abs(f - h * f0)))
            hs.append((E[k - 2 : k + 3].max(), W[k - 2 : k + 3].max()))
        hs = np.array(hs)
        # the engine's audible lines
        loud = (hs[:, 0] > hs[:, 0].max() - 60) & (hs[:, 0] > E.max() - 80)
        d = hs[:, 1] - hs[:, 0]
        d = d - d[np.argmax(hs[:, 0])]  # shape, not level (level is above)
        d = d[loud]
        env = _env_p95([e], [w], int(0.01 * SR))
        vals = {
            "level": abs(lvl),
            "harm_rms": float(np.sqrt(np.mean(d**2))),
            "harm_max": float(np.max(np.abs(d))),
            "env_p95": env,
        }
        worst = {k: max(worst[k], vals[k]) for k in worst}
    return worst


def _window(eng: np.ndarray) -> tuple[int, int]:
    """Where `metrics` reads a note: the held middle, or — when that is more
    than 60 dB under the note's loudest — 30-480 ms, after the attack."""
    a, b = int(0.5 * SR), int(0.95 * SR)
    if _rms(eng[a:b]) < np.abs(eng).max() * 1e-3:
        return int(0.03 * SR), int(0.48 * SR)
    return a, b


def _env_p95(es: list[np.ndarray], ws: list[np.ndarray], fr: int) -> float:
    """p95 of |live - engine| over frames within 40 dB of the engine's
    loudest; each side's frame power averaged over its runs."""
    m = len(es[0]) // fr

    def frames(xs):
        return np.mean(
            [np.mean(np.square(x[: m * fr]).reshape(m, fr), axis=1) for x in xs], axis=0
        )

    ee = 10 * np.log10(frames(es) + 1e-24)
    ww = 10 * np.log10(frames(ws) + 1e-24)
    mask = ee > ee.max() - 40
    return float(np.percentile(np.abs(ww[mask] - ee[mask]), 95))


THIRD_OCTAVES = [1000.0 * 2 ** (k / 3) for k in range(-12, 13)]  # 63 Hz .. 16 kHz


def _bands_db(x: np.ndarray) -> np.ndarray:
    p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.array(
        [
            10
            * np.log10(
                p[(f >= c * 2 ** (-1 / 6)) & (f < c * 2 ** (1 / 6))].sum() + 1e-24
            )
            for c in THIRD_OCTAVES
        ]
    )


def stat_metrics(engs: list[np.ndarray], runs: list[np.ndarray], gate: float) -> dict:
    """Random sources: level, 1/3-octave bands (within 40 dB of the loudest)
    and the 50 ms envelope; each side's power averaged over its runs (the
    engine's with its noise seeds shifted, the worklet's with its own)."""
    worst = {"level": 0.0, "bands": 0.0, "env_p95": 0.0}
    a, b = int(0.5 * SR), int((gate - 0.05) * SR)

    def power(xs):
        return np.mean([_rms(x[a:b]) ** 2 for x in xs])

    def bands(xs):
        return 10 * np.log10(
            np.mean([10 ** (_bands_db(x[a:b]) / 10) for x in xs], axis=0)
        )

    for ch in (0, 1):
        es = [r[:, ch] for r in engs]
        ws = [r[:, ch] for r in runs]
        E, W = bands(es), bands(ws)
        loud = E > E.max() - 40
        vals = {
            "level": abs(10 * np.log10(power(ws) / power(es))),
            "bands": float(np.max(np.abs(W - E)[loud])),
            "env_p95": _env_p95(es, ws, int(0.05 * SR)),
        }
        worst = {k: max(worst[k], vals[k]) for k in worst}
    return worst


LIMITS = {"level": 0.5, "harm_rms": 1.0, "harm_max": 3.0, "env_p95": 1.0}
STAT_LIMITS = {"level": 0.5, "bands": 1.5, "env_p95": 1.0}


def _over(got: dict, limits: dict) -> dict:
    """The metrics past their limits. `not v <= limit`, never `v > limit`:
    a NaN (a live voice that output NaN) compares false both ways, and the
    second form passed it — a sampler read at a NaN rate once did."""
    return {k: round(v, 2) for k, v in got.items() if not v <= limits[k]}


def _check(rendered, tag, pitch, engine):
    if (tag + "|stripped", pitch) in rendered:
        got = rendered[(tag + "|stripped", pitch)][engine]
        bad = _over(got, LIMITS)
        assert not bad, f"{tag} {pitch} (noise stripped): {bad} (limits {LIMITS})"
    res = rendered[(tag, pitch)]
    got = res[engine]
    if res["random"]:
        bad = _over(got, STAT_LIMITS)
        assert not bad, f"{tag} {pitch} (statistical): {bad} (limits {STAT_LIMITS})"
    else:
        bad = _over(got, LIMITS)
        assert not bad, f"{tag} {pitch}: {bad} (limits {LIMITS})"


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("case", list(CASES))
def test_each_field_case_matches_the_engine(rendered, case, engine):
    _check(rendered, f"case:{case}", CASES[case][1].get("pitch", "C4"), engine)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("pitch", PITCHES)
@pytest.mark.parametrize("name", [n for n, _ in corpus()])
def test_every_admitted_sound_matches_the_engine(rendered, name, pitch, engine):
    _check(rendered, f"sound:{name}", pitch, engine)


def test_wasm_engine_matches_the_js_voice(rendered):
    """The module runs the JS voice's float64 math in the same order (its
    sin/tanh/exp/2^x within a few ulp of V8's), so the two engines' f32
    output differs by rounding at most: every job, every seed, every
    sample within 1e-6 of the job's peak (-120 dB)."""
    errs = {key: res["js_wasm"] for key, res in rendered.items()}
    bad = [(key, e) for key, e in errs.items() if not e <= 1e-6]
    print(f"wasm vs js: worst sample error {max(errs.values()):.2e} of the peak")
    assert not bad, bad[:10]


def test_parity_errors_are_reported(rendered):
    """Not a threshold (the tests above are): each engine's worst numbers
    against the engine across the corpus, for the record."""
    for engine in ENGINES:
        det = {"level": 0.0, "harm_rms": 0.0, "harm_max": 0.0, "env_p95": 0.0}
        stat = {"level": 0.0, "bands": 0.0, "env_p95": 0.0}
        for res in rendered.values():
            got = res[engine]
            if res["random"]:
                stat = {k: max(stat[k], got[k]) for k in stat}
            else:
                det = {k: max(det[k], got[k]) for k in det}
        det_r = {k: round(float(v), 4) for k, v in det.items()}
        stat_r = {k: round(float(v), 4) for k, v in stat.items()}
        print(f"{engine}: deterministic {det_r}  statistical {stat_r}")
        for (tag, _pitch), res in rendered.items():  # the live sampler, case by case
            if tag.startswith("case:sampler"):
                got = {k: round(float(v), 4) for k, v in res[engine].items()}
                print(f"{engine}: {tag} {got} js-wasm {res['js_wasm']:.1e}")


def test_every_admitted_field_maps_to_a_parity_case():
    """A field joins WORKLET_FIELDS or GATED only with a case here proving
    the worklet reproduces it. A new admission without one fails this test."""
    admitted = set(worklet.WORKLET_FIELDS) | set(worklet.GATED)
    missing = {f for f in admitted if f not in FIELD_CASES and not _default_only(f)}
    assert not missing, f"admitted without a parity case: {sorted(missing)}"
    assert set(FIELD_CASES.values()) <= set(CASES)
    for field, case in FIELD_CASES.items():
        p, _ = CASES[case]
        assert field in admitted, f"{field} has a case but the gate refuses it"
        assert worklet.can_play(p), f"case {case} ({field}) is not admitted"
        moved = getattr(p, field) != getattr(Patch(), field)
        # the case must actually move the field it proves (osc2_octave's
        # case moves it too)
        assert moved, f"case {case} leaves {field} at its default"


def _default_only(field: str) -> bool:
    """A GATED predicate that refuses every non-default value tried."""
    pred = worklet.GATED.get(field)
    if pred is None:
        return False
    default = getattr(Patch(), field)
    probes = {
        str: ["square", "lfo", "1/4", "noise", "x"],
        float: [0.3, 1.5, -0.4],
        int: [2, -1],
    }.get(type(default), [])
    for v in probes:
        try:
            p = replace(Patch(), **{field: v})
        except TypeError:
            continue
        if field.endswith("_src"):
            p = replace(p, **{field.replace("_src", "_amt"): 0.5})
        if pred(p):
            return False
    return True


def test_supersaw_phase_table():
    """The worklet's supersaw starts its saws where core.supersaw's seeded
    RNG does: seeds 0..7 (osc1, per unison copy) and 100..107 (osc2)."""
    js = WORKLET.read_text(encoding="utf-8")
    body = js[js.index("const SUPERSAW_PHASE = [") :]
    body = body[len("const SUPERSAW_PHASE = ") : body.index("];") + 1]
    table = np.array(json.loads(body.replace(",\n]", "]")))
    seeds = [*range(worklet.MAX_UNISON), *range(100, 100 + worklet.MAX_UNISON)]
    want = np.array([np.random.default_rng(s).random(7) for s in seeds])
    np.testing.assert_allclose(table, want, atol=1e-6)


def test_coverage_is_reported():
    """Not a threshold: the number the gate admits, for the record."""
    rows = [p for _n, p in _all_rows()]
    ok = sum(worklet.can_play(p) for p in rows)
    print(f"worklet coverage: {ok}/{len(rows)} factory+library+recipe patches")
    assert ok == len(corpus())
