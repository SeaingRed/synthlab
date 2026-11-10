"""The live voice's WebAssembly engine (static/voice-dsp.wasm, built from
synthlab/studio/wasm/ — AssemblyScript + SIMD) and its JS glue.

- the served module is the one the build recorded (its sha256), and a fresh
  build from the committed sources reproduces it byte for byte (when the
  pinned toolchain is installed: `npm ci` in synthlab/studio/wasm);
- vmath.ts's SIMD sin/tanh/exp/2^x agree with V8's Math to a few ulp;
- the engine plays what the JS voice plays through everything a live
  voice goes through — stealing, re-hits, bends, live param changes that
  switch its path (unison 1 <-> 3, a JS-only shape), odd block sizes, a
  slot pool run dry (those voices run JS), a module arriving mid-note;
- anything that goes wrong falls back to the JS voice, and the `load`
  reports name the engine (the CPU meter shows it).

The parity corpus (test_live_parity_corpus.py) holds both engines to the
Python engine; this file holds them to each other.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import voicetables, worklet
from synthlab.studio.server import create_app
from synthlab.synth import Patch

STATIC = Path("synthlab/studio/static")
WASM = (STATIC / "voice-dsp.wasm").resolve()
WORKLET = (STATIC / "voice-worklet.js").resolve()
SRC = Path("synthlab/studio/wasm").resolve()
ASC = SRC / "node_modules" / "assemblyscript" / "bin" / "asc.js"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the worklet")
toolchain = pytest.mark.skipif(
    shutil.which("node") is None or not ASC.exists(),
    reason="no AssemblyScript toolchain (npm ci in synthlab/studio/wasm)",
)


def test_served_module_is_the_recorded_build():
    recorded = (SRC / "voice-dsp.wasm.sha256").read_text(encoding="utf-8").strip()
    assert hashlib.sha256(WASM.read_bytes()).hexdigest() == recorded


def test_module_is_served_as_wasm(tmp_path):
    r = TestClient(create_app(data_dir=tmp_path)).get("/static/voice-dsp.wasm")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/wasm"
    assert r.content == WASM.read_bytes()


def test_toolchain_is_pinned():
    """An exact AssemblyScript version, locked (binaryen with it), so a
    rebuild reproduces the committed module."""
    pkg = json.loads((SRC / "package.json").read_text(encoding="utf-8"))
    want = pkg["devDependencies"]["assemblyscript"]
    assert want[0].isdigit(), "pin an exact version, not a range"
    lock = json.loads((SRC / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["packages"]["node_modules/assemblyscript"]["version"] == want
    assert "node_modules/binaryen" in lock["packages"]
    assert pkg["scripts"]["build"] == "node build.mjs"
    ignored = Path(".gitignore").read_text(encoding="utf-8").split()
    assert "node_modules/" in ignored


@toolchain
def test_fresh_build_reproduces_the_committed_module(tmp_path):
    out = tmp_path / "voice-dsp.wasm"
    subprocess.run(["node", "build.mjs", str(out)], cwd=SRC, check=True, timeout=300)
    assert out.read_bytes() == WASM.read_bytes(), (
        "static/voice-dsp.wasm is stale: run `npm run build` in synthlab/studio/wasm"
    )


MATHCHECK = r"""
const ex = new WebAssembly.Instance(
  new WebAssembly.Module(require("fs").readFileSync(process.argv[2])), {}).exports;
const dv = new DataView(new ArrayBuffer(8));
const bits = (x) => { dv.setFloat64(0, x); return dv.getBigInt64(0); };
const ulp = (a, b) => { const d = bits(a) - bits(b); return Number(d < 0n ? -d : d); };
let seed = 12345;
const rnd = () =>
  ((seed = (Math.imul(seed, 1103515245) + 12345) >>> 0) / 4294967296) * 2 - 1;
const worst = { sin: 0, tanh: 0, exp: 0, exp2: 0 };
const cases = [
  ["sin", ex.sin2, Math.sin, [80, 7, 1e-3]],
  ["tanh", ex.tanh2, Math.tanh, [30, 2, 1e-4]],
  ["exp", ex.expL, Math.exp, [700, 5, 1e-3]],
  ["exp2", ex.exp2L, (x) => Math.pow(2, x), [1000, 3, 1e-4]],
];
for (let i = 0; i < 300000; i++) {
  for (const [name, f, ref, spans] of cases) {
    const x = rnd() * spans[i % 3], lane = i & 1;
    worst[name] = Math.max(worst[name], ulp(f(x, -x, lane), ref(lane ? -x : x)));
  }
}
process.stdout.write(JSON.stringify(worst));
"""


@toolchain
def test_simd_math_kernels_agree_with_v8(tmp_path):
    out = tmp_path / "mathcheck.wasm"
    subprocess.run(
        ["node", str(ASC), "assembly/mathcheck.ts", "--config", "asconfig.json",
         "--outFile", str(out)],
        cwd=SRC, check=True, timeout=300,
    )  # fmt: skip
    (tmp_path / "check.cjs").write_text(MATHCHECK, encoding="utf-8")
    got = subprocess.run(
        ["node", str(tmp_path / "check.cjs"), str(out)],
        capture_output=True, text=True, check=True, timeout=300,
    )  # fmt: skip
    worst = json.loads(got.stdout)
    print("max ulp vs V8:", worst)
    assert worst["sin"] <= 2 and worst["exp"] <= 2 and worst["exp2"] <= 2
    assert worst["tanh"] <= 8


# Every path the engine can take, and every event that moves a voice
# between them; each patch is played by the JS voice and the WebAssembly
# one through the same script, and must come out the same.
SCENARIO = r"""
globalThis.sampleRate = 48000;
const posted = [];
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { onmessage: null, postMessage: (m) => posted.push(m) }; }
};
let Voice = null;
globalThis.registerProcessor = (_n, cls) => { Voice = cls; };
const fs = require("fs");
eval(fs.readFileSync(process.argv[2], "utf8")); // the shipped worklet, under shims
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "sampler-worklet"), "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "grain-worklet"), "utf8"));
const mod = new WebAssembly.Module(fs.readFileSync(process.argv[3]));
const spec = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const bin = fs.readFileSync(process.argv[5]);
const hl = bin.readUInt32LE(0);
const meta = JSON.parse(bin.subarray(4, 4 + hl).toString("utf8"));
const data = new Float32Array(
  bin.buffer.slice(bin.byteOffset + 4 + hl, bin.byteOffset + bin.length));
const SIZES = [128, 128, 100, 28, 128, 64, 1, 127, 128, 128];
function play(p, engine) {
  const v = new Voice(), send = (m) => v.port.onmessage({ data: m });
  send({ type: "tables", meta, data });
  const wasmAt = engine === "wasm" ? 0 : engine === "late" ? 6 : -1;
  const out = [], hz = (k) => 55 * Math.pow(2, k / 7);
  let wasmVoices = 0, jsVoices = 0;
  for (let q = 0; q < 170; q++) {
    if (q === wasmAt) send({ type: "wasm", module: mod });
    if (q === 0) for (let k = 0; k < 6; k++) {
      const vel = 0.4 + k / 10;
      send({ type: "noteOn", id: "k" + k, freq: hz(k), vel, p, seed: k + 1 });
    }
    if (q === 12) for (const id of ["k1", "k3"]) send({ type: "noteOff", id });
    // a re-hit revives
    if (q === 20) send({ type: "noteOn", id: "k1", freq: hz(1), vel: 0.9, seed: 99 });
    if (q === 24) send({ type: "bend", id: "k0", freq: hz(3), time: 0.05 });
    const uni = p.unison === 1 ? 3 : 1;
    if (q === 30) send({ type: "params", p: { unison: uni, cutoff: p.cutoff * 1.7 } });
    if (q === 40) send({ type: "mod", param: "resonance", value: 0.6 });
    if (q === 50) send({ type: "params", p: { osc1: "wavetable" } }); // a JS-only shape
    if (q === 60) send({ type: "params", p: { osc1: p.osc1, unison: p.unison } });
    if (q === 70) for (let k = 0; k < 14; k++) { // stealing
      send({ type: "noteOn", id: "s" + k, freq: hz(k + 3), vel: 0.7, seed: 200 + k });
    }
    if (q === 90) for (let k = 0; k < 70; k++) { // the slot pool runs dry
      send({ type: "noteOn", id: "x" + k, freq: hz(k % 20), vel: 0.5, seed: 400 + k });
    }
    if (q === 100) for (const id of [...v.voices.keys()]) send({ type: "noteOff", id });
    if (q === 140) send({ type: "allOff" });
    if (q === 150) send({ type: "noteOn", id: "z", freq: hz(4), vel: 1, seed: 7 });
    for (const x of v.voices.values()) { if (x.w >= 0) wasmVoices++; else jsVoices++; }
    const n = SIZES[q % SIZES.length], l = new Float32Array(n), r = new Float32Array(n);
    v.process([], [[l, r]]);
    out.push(...l, ...r);
  }
  return { out, wasmVoices, jsVoices, engine: v.wasm ? "wasm" : "js" };
}
const res = {};
for (const [name, p] of Object.entries(spec)) {
  const a = play(p, "js"), b = play(p, "wasm"), c = play(p, "late");
  let peak = 1e-9, db = 0, dc = 0;
  for (let i = 0; i < a.out.length; i++) {
    peak = Math.max(peak, Math.abs(a.out[i]));
    db = Math.max(db, Math.abs(a.out[i] - b.out[i]));
    dc = Math.max(dc, Math.abs(a.out[i] - c.out[i]));
  }
  res[name] = { wasm: db / peak, late: dc / peak, peak, engine: b.engine,
    wasmVoices: b.wasmVoices,
    jsVoices: b.jsVoices, jsOnly: a.wasmVoices, lateJs: c.jsVoices };
}
process.stdout.write(JSON.stringify(res));
"""

SCENARIO_PATCHES = {
    "default": Patch(),
    "mono_pair_fm": Patch(
        osc1="fm", fm_decay=0.4, osc2="sine", fm_amount=0.2, sub_level=0.3
    ),
    "supersaw_stereo": Patch(
        osc1="supersaw", osc2="square", unison=3, ring_mod=0.3, pwm_depth=0.4
    ),
    "pluck": Patch(osc1="pluck", osc2="saw", noise_level=0.1, noise_color="pink"),
    "routes": Patch(
        mod1_src="lfo",
        mod1_dst="cutoff",
        mod1_amt=0.5,
        mod2_src="aenv",
        mod2_dst="osc1_detune",
        mod2_amt=0.4,
        vibrato_depth=0.3,
    ),  # fmt: skip
    "routed_drive": Patch(
        filter_type="lowpass", mod1_src="aenv", mod1_dst="drive", mod1_amt=0.6
    ),
    "tract_noise": Patch(
        filter_type="bandpass",
        formant_amount=0.8,
        noise_level=0.2,
        noise_color="brown",
        tremolo_depth=0.5,
        pitch_env=7.0,
    ),  # fmt: skip
    "wavetable_notch": Patch(
        osc1="wavetable", wt_pos=0.6, filter_type="notch", unison=2
    ),
    "glide_highpass": Patch(
        filter_type="highpass", glide=0.1, drive=2.5, osc1="square"
    ),
}


@node
def test_engines_agree_through_everything_a_voice_goes_through(tmp_path):
    extra = worklet.voice_tables()
    spec = {n: {**p.to_json(), **extra} for n, p in SCENARIO_PATCHES.items()}
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    (tmp_path / "tables.bin").write_bytes(voicetables.payload())
    (tmp_path / "scenario.cjs").write_text(SCENARIO, encoding="utf-8")
    got = subprocess.run(
        ["node", str(tmp_path / "scenario.cjs"), str(WORKLET), str(WASM),
         str(tmp_path / "spec.json"), str(tmp_path / "tables.bin")],
        capture_output=True, text=True, check=True, timeout=600,
    )  # fmt: skip
    res = json.loads(got.stdout)
    for name, r in res.items():
        print(name, r)
        assert r["engine"] == "wasm" and r["peak"] > 0.05, name
        assert r["jsOnly"] == 0  # no module, no slots
        # the module ran voices, and the dry pool ran some in JS beside them
        assert r["wasmVoices"] > 0 and r["jsVoices"] > 0, name
        # voices already sounding when the module arrived stayed JS
        assert r["lateJs"] > 0, name
        assert r["wasm"] < 1e-6, f"{name}: wasm vs js {r['wasm']:.2e} of the peak"
        assert r["late"] < 1e-6, f"{name}: module mid-note {r['late']:.2e} of the peak"


FALLBACK = r"""
globalThis.sampleRate = 48000;
const posted = [];
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { onmessage: null, postMessage: (m) => posted.push(m) }; }
};
let Voice = null;
globalThis.registerProcessor = (_n, cls) => { Voice = cls; };
const fs = require("fs");
eval(fs.readFileSync(process.argv[2], "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "sampler-worklet"), "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "grain-worklet"), "utf8"));
const mod = new WebAssembly.Module(fs.readFileSync(process.argv[3]));
const res = {};
function run(label, module, hide, opts) {
  posted.length = 0;
  const saved = globalThis.WebAssembly;
  if (hide) delete globalThis.WebAssembly; // a scope without WebAssembly
  const v = new Voice(opts);
  if (module !== undefined) v.port.onmessage({ data: { type: "wasm", module } });
  if (hide) globalThis.WebAssembly = saved;
  v.port.onmessage({ data: { type: "noteOn", id: "a", freq: 220, vel: 1, p: {} } });
  const l = new Float32Array(128), r = new Float32Array(128);
  let peak = 0;
  for (let q = 0; q < 64; q++) {
    v.process([], [[l, r]]);
    for (const x of l) peak = Math.max(peak, Math.abs(x));
  }
  res[label] = { engine: posted.find((m) => m.type === "engine")?.engine ?? null,
    load: posted.find((m) => m.type === "load")?.engine ?? null, wasm: !!v.wasm, peak };
}
run("none", undefined, false);
run("module", mod, false);
run("options", undefined, false, { processorOptions: { wasm: mod } }); // livefx.js
run("bytes", fs.readFileSync(process.argv[3]), false);
run("not a module", {}, false);
run("no WebAssembly", mod, true);
process.stdout.write(JSON.stringify(res));
"""


@node
def test_anything_wrong_falls_back_to_the_js_voice(tmp_path):
    (tmp_path / "fallback.cjs").write_text(FALLBACK, encoding="utf-8")
    got = subprocess.run(
        ["node", str(tmp_path / "fallback.cjs"), str(WORKLET), str(WASM)],
        capture_output=True, text=True, check=True, timeout=120,
    )  # fmt: skip
    res = json.loads(got.stdout)
    assert res["none"] == {**res["none"], "engine": None, "load": "js", "wasm": False}
    for label in ("module", "options", "bytes"):
        assert (res[label]["engine"], res[label]["load"]) == ("wasm", "wasm"), label
    for label in ("not a module", "no WebAssembly"):
        r = res[label]
        assert (r["engine"], r["load"], r["wasm"]) == ("js", "js", False), label
    # every one of them still plays
    peaks = {k: round(r["peak"], 5) for k, r in res.items()}
    assert len(set(peaks.values())) == 1 and peaks["none"] > 0.1, peaks


def test_main_thread_compiles_and_posts_the_module():
    """livefx.js compiles the module (a worklet scope can't fetch), hands it
    to each new voice node, skips it under ?nowasm, and the meter names the
    engine the nodes report."""
    js = (STATIC / "livefx.js").read_text(encoding="utf-8")
    assert 'fetch("/static/voice-dsp.wasm")' in js
    assert "WebAssembly.compile(" in js
    # in processorOptions: Chrome drops a Module posted over the port
    # (browser-checked); the node falls back to plain JS if it can't clone
    assert "make(wasm ? { wasm } : {})" in js and "catch { node = make({}); }" in js
    assert "nowasm" in js
    assert "engine: d.engine" in js
    worklet_js = WORKLET.read_text(encoding="utf-8")
    assert 'engine: w ? "wasm" : "js"' in worklet_js
    assert "new WebAssembly.Instance(mod, {})" in worklet_js
    assert "opts?.processorOptions?.wasm" in worklet_js
