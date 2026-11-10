"""CPU cost of the live key engine (static/voice-worklet.js, fx-worklet.js)
under node, on both voice engines: the JS DSP (the reference and the
fallback) and the WebAssembly one (static/voice-dsp.wasm posted to each
voice node, as livefx.js does).

    uv run python tools/bench_live_voice.py [--voices 16] [--layers 3]
        [--engine js wasm] [--only NAME ...]

Runs the shipped worklet files at 48 kHz (a browser's usual rate) with the
engine's tables and reports time per 128-sample render quantum as a share
of its realtime budget (128 / 48000 s = 2.67 ms):

1. every factory + library sound, `--voices` notes held across two octaves
   (a layer node holds 16 voices — MAXV — so more than 16 play on as many
   nodes of the same sound as it takes, like layers of it);
2. `--layers` layers at once (the heaviest sounds, one processor per lane)
   two ways: all live (every lane in the worklet — `?live=all`, the A/B
   dev flag) and pre-rendered (the default: only the edited lane runs in
   the worklet; the others play server renders on native
   AudioBufferSourceNodes, which cost the worklet thread nothing — their
   idle voice nodes, kept to stand in before a render lands, are counted);
3. silence culling, on vs off (`proc.cull`): half the voices held and half
   released into a long release, measured over the release — a sustained
   pad (nothing to cull until its envelope lands) and a plucked string
   (under -120 dBFS seconds before its release ends) — and an FX chain
   after its input stops (its tail, then nothing);
4. the live sampler: a looping two-second tone rooted on C2, `--voices`
   keys from the root to two octaves over it — the tape read
   (sampler-worklet.js), and formant mode (grain-worklet.js: 2 x the
   pitch ratio grains sounding per voice, so 2 at the root and 8 two
   octaves up), each also as a true-stereo sample.

Node's V8 is the same engine as Chrome's AudioWorklet thread (its
WebAssembly and SIMD included), so the shares carry over to a desktop
browser.

Recorded on a Surface Pro 11 (Snapdragon X, node 24), JS ->
WebAssembly (share of the realtime budget; "x" the speedup):

    16 voices:  median sound   10.3% -> 5.2%  (x1.97)
                worst          23.8% -> 11.5% (Slate Ray, x2.07)
                3 layers all live (?live=all)   60.9% -> 28.3% (x2.15)
                3 layers pre-rendered (default) 24.0% -> 11.7% (x2.06)
                per sound: median x1.96, min ~x1.2 (wavetable / pluck:
                their oscillator loop stays JS), max x3.0
    32 voices (two nodes of 16):
                median sound   20.5% -> 11.0% (x1.87)
                worst          47.7% -> 24.1% (Slate Ray, x1.98)
                3 layers all live               126.4% -> 57.1% (x2.21)
                3 layers pre-rendered            48.5% -> 26.9% (x1.80)
    silence culling, off -> on (WebAssembly, 16 voices):
                pad release 6.1% -> 5.9%, pluck release 4.9% -> 3.5%,
                FX chain at rest 0.9% -> 0.1% (no voice DSP: engine-blind)
    Run-to-run noise is a few points per sound; a sound's single run can
    land well off its ratio.
    (before the WebAssembly engine: median 10.0%,
    worst 23.6%, 3 layers x 16 all live 62.2% -> 63.9%, pre-rendered 24.0%)

Case 4, recorded on an i9-14900K desktop (node 24), JS ->
WebAssembly (the sampler's loops are JS on both; the module runs post()):

    16 keys:    tape read  mono 4.1% -> 3.6%, stereo 4.2% -> 3.7%
                formant    mono 7.8% -> 7.2%, stereo 7.9% -> 7.6%
                (the same run: median sound 7.1% -> 4.6%, worst 17.8% ->
                10.4%, 3 layers all live 43.7% -> 25.0%)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl

import numpy as np

from synthlab import sampler
from synthlab.studio import voicetables, worklet
from synthlab.studio.factory import FACTORY, get_factory
from synthlab.studio.library import LIBRARY, get_library
from synthlab.synth import Patch, at_bpm

STATIC = Path(__file__).resolve().parents[1] / "synthlab/studio/static"

BENCH = r"""
globalThis.sampleRate = 48000;
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { onmessage: null, postMessage() {} }; }
};
const reg = {};
globalThis.registerProcessor = (n, c) => { reg[n] = c; };
const fs = require("fs");
eval(fs.readFileSync(process.argv[2], "utf8")); // the shipped files, under shims
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "sampler-worklet"), "utf8"));
eval(fs.readFileSync(
  process.argv[2].replace("voice-worklet", "grain-worklet"), "utf8"));
eval(fs.readFileSync(process.argv[3], "utf8"));
const Voice = reg["synthlab-voice"], Fx = reg["synthlab-fx"];
const spec = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const bin = fs.readFileSync(process.argv[5]);
// the WebAssembly engine (a module path) or the JS one (none)
const mod = process.argv[6]
  ? new WebAssembly.Module(fs.readFileSync(process.argv[6])) : null;
const hl = bin.readUInt32LE(0);
const meta = JSON.parse(bin.subarray(4, 4 + hl).toString("utf8"));
const data = new Float32Array(
  bin.buffer.slice(bin.byteOffset + 4 + hl, bin.byteOffset + bin.length));
const BUDGET = 128 / 48000 * 1e6;
const MAXV = 16; // a layer node's voices
const out = [[new Float32Array(128), new Float32Array(128)]];
// GET /sampler/live/<id>.bin for a sampler sound, split as livefx.js splits it
function samplerOf(p) {
  if (!p.sampler_bin) return null;
  const sb = fs.readFileSync(p.sampler_bin), sn = sb.readUInt32LE(0);
  return { type: "sampler", url: p.sampler_live,
    meta: JSON.parse(sb.subarray(4, 4 + sn).toString("utf8")),
    data: new Float32Array(
      sb.buffer.slice(sb.byteOffset + 4 + sn, sb.byteOffset + sb.length)) };
}
function node(p, cull) {
  const v = new Voice();
  v.cull = cull;
  v.port.onmessage({ data: { type: "tables", meta, data } });
  const smp = samplerOf(p);
  if (smp) v.port.onmessage({ data: smp });
  if (mod) {
    v.port.onmessage({ data: { type: "wasm", module: mod } });
    if (!v.wasm) throw new Error("the WebAssembly engine did not start");
  }
  return v;
}
// `voices` notes, C2 (or base) and up over two octaves, 16 to a node
function voice(p, voices, cull = true, base = 65.4) {
  const nodes = [node(p, cull)];
  for (let i = 0; i < voices; i++) {
    if (i && i % MAXV === 0) nodes.push(node(p, cull));
    const freq = base * Math.pow(2, (i * 25 / voices) / 12);
    const on = { type: "noteOn", id: "k" + i, freq, vel: 1, p };
    nodes[nodes.length - 1].port.onmessage({ data: on });
  }
  return nodes;
}
function time(procs, blocks, feed) {
  const t = process.hrtime.bigint();
  for (let b = 0; b < blocks; b++) {
    for (const v of procs) v.process(feed ? feed(b) : [], out);
  }
  const us = Number(process.hrtime.bigint() - t) / 1e3 / blocks;
  return { us, share: us / BUDGET };
}
function held(procs) {
  for (let b = 0; b < 400; b++) for (const v of procs) v.process([], out); // JIT
  return time(procs, 1500);
}
// half the voices held, half released: the release, 3 s of it (C3 and up)
function tail(p, voices, cull) {
  const vs = voice(p, voices, cull, 130.8);
  for (let b = 0; b < 188; b++) for (const v of vs) v.process([], out); // 0.5 s held
  for (let i = 0; i < voices / 2; i++) {
    vs[Math.floor(i / MAXV)].port.onmessage({ data: { type: "noteOff", id: "k" + i } });
  }
  return time(vs, 1125);
}
// an FX chain fed 0.3 s of a saw, then silence: at rest, 5 s on
function fxTail(units, cull) {
  const f = new Fx({ processorOptions: { units } });
  f.cull = cull;
  const l = new Float32Array(128), r = new Float32Array(128);
  let ph = 0;
  for (let b = 0; b < 113; b++) {
    for (let i = 0; i < 128; i++) {
      ph = (ph + 220 / 48000) % 1;
      l[i] = r[i] = 0.5 * (2 * ph - 1);
    }
    f.process([[l, r]], out);
  }
  l.fill(0); r.fill(0);
  for (let b = 0; b < 1875; b++) f.process([[l, r]], out); // its tail rings out
  return time([f], 1500, () => [[l, r]]);
}
const res = {};
for (const [name, p] of Object.entries(spec.sounds)) {
  res[name] = held(voice(p, spec.voices));
}
// the heaviest sounds: fixed by the spec (the JS engine's ranking), so both
// engines layer the same three
const heavy = spec.heavy.map((n) => spec.sounds[n]);
const allLive = held(heavy.flatMap((p) => voice(p, spec.voices)));
const idle = heavy.slice(1).flatMap((p) => voice(p, 0)); // stand-in nodes, no notes
const prerendered = held([...voice(heavy[0], spec.voices), ...idle]);
const idleOn = held(voice(heavy[0], 0, true));
const idleOff = held(voice(heavy[0], 0, false));
const cull = {};
for (const [name, p] of Object.entries(spec.tails)) {
  cull[name] = [tail(p, spec.voices, false).share, tail(p, spec.voices, true).share];
}
cull["FX chain at rest (input silent 5 s)"] = [
  fxTail(spec.fx, false).share, fxTail(spec.fx, true).share];
const samplers = {};
for (const [name, p] of Object.entries(spec.samplers)) {
  samplers[name] = held(voice(p, spec.voices)).share;
}
const shares = {
  allLive, prerendered, cull, idle: [idleOff.share, idleOn.share], samplers };
process.stdout.write(JSON.stringify({ res, ...shares }));
"""

# a sustained pad and a plucked string, each with a long release
TAILS = {
    "pad release (8 held + 8 released)": Patch(
        osc1="supersaw", unison=2, attack=0.05, sustain=0.8, release=3.0
    ),
    "pluck release (8 held + 8 released)": Patch(
        osc1="pluck", pluck_damping=0.9, sustain=0.9, release=3.0
    ),
}
FX_CHAIN = [
    {"kind": "drive", "amount": 2.0, "mix": 1.0},
    {"kind": "chorus", "rate": 0.9, "depth": 3.0, "base": 12.0, "mix": 0.5},
    {"kind": "delay", "time": 0.15, "feedback": 0.35, "mix": 0.35, "ping_pong": 0.0},
    {"kind": "phaser", "rate": 0.5, "depth": 0.7, "mix": 0.5},
]


def _samplers(tmp: Path, extra: dict) -> dict:
    """Case 4's sounds: name -> the patch as /patch.json sends it, plus the
    file its `sampler_live` URL would answer with (`sampler_bin`)."""
    t = np.arange(2 * sampler.SR) / sampler.SR
    tone = sum(np.sin(2 * np.pi * 65.406 * k * t) / k for k in range(1, 30)) * 0.2
    ids = {
        "mono": sampler.register(tone),
        "stereo": sampler.register(np.column_stack([tone, np.roll(tone, 200)])),
    }
    out = {}
    for mode in sampler.MODES:
        for kind, sid in ids.items():
            p = Patch(
                osc1="sampler",
                sampler_sample=sid,
                sampler_root=36,
                sampler_mode=mode,
                sampler_loop="on",
                filter_type="none",
            )
            url = sampler.live_url(p)
            query = dict(parse_qsl(url.split("?", 1)[1]))
            path = tmp / f"{mode}-{kind}.smp"
            path.write_bytes(sampler.live_payload(sid, sampler.live_settings(query)))
            name = f"sampler, {'tape read' if mode == 'resample' else mode}, {kind}"
            out[name] = {
                **p.to_json(),
                **extra,
                "sampler_live": url,
                "sampler_bin": str(path),
            }
    return out


# the three heaviest sounds on the JS engine (16 voices), layered by case 2
# (the results recorded above call them by earlier names: "Slate Ray",
# "Deep Beam" and "Fathom Depth")
HEAVY = [
    "Bright Sweeping Supersaw Pad",
    "Dark Vibrato Sine Pad",
    "Dark Driven Sine Bass",
]


# `--round6`: each warp-able shape plain and warped, unison 1 and 4
ROUND6 = {
    f"r6 {shape}{' x4' if u > 1 else ''}{', ' + warp if warp else ''}": Patch(
        osc1=shape, osc1_warp=warp or "off", osc1_warp_amt=0.6, unison=u
    )
    for shape in ("saw", "sine", "wavetable")
    for u in (1, 4)
    for warp in ("", "bend_pos")
}


def _run(tmp: Path, engine: str) -> dict:
    cmd = ["node", str(tmp / "bench.cjs")]
    cmd += [str(STATIC / "voice-worklet.js"), str(STATIC / "fx-worklet.js")]
    cmd += [str(tmp / "spec.json"), str(tmp / "tables.bin")]
    if engine == "wasm":
        cmd.append(str(STATIC / "voice-dsp.wasm"))
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=1800)
    return json.loads(out.stdout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", type=int, default=16)
    ap.add_argument("--layers", type=int, default=3)
    ap.add_argument(
        "--engine", nargs="*", default=["js", "wasm"], choices=["js", "wasm"]
    )
    ap.add_argument("--only", nargs="*", help="sound names (default: every sound)")
    ap.add_argument(
        "--round6",
        action="store_true",
        help="add the warp pairs: an oscillator plain and warped",
    )
    args = ap.parse_args()
    rows = [(e["name"], get_factory(e["name"])) for e in FACTORY]
    rows += [(e["name"], get_library(e["name"])) for e in LIBRARY]
    if args.only:
        rows = [(n, p) for n, p in rows if n in args.only or n in HEAVY]
    extra = worklet.voice_tables()
    # a note held forever: sustain from the envelope, no release inside the run
    sounds = {
        n: {**at_bpm(p, 120.0).to_json(), **extra}
        for n, p in rows
        if worklet.can_play(p)
    }
    if args.round6:
        sounds.update({n: {**p.to_json(), **extra} for n, p in ROUND6.items()})
    tails = {n: {**p.to_json(), **extra} for n, p in TAILS.items()}
    tmp = Path(tempfile.mkdtemp())
    (tmp / "bench.cjs").write_text(BENCH, encoding="utf-8")
    spec = {
        "sounds": sounds,
        "voices": args.voices,
        "heavy": HEAVY[: args.layers],
        "tails": tails,
        "fx": FX_CHAIN,
        "samplers": _samplers(tmp, extra),
    }
    (tmp / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    (tmp / "tables.bin").write_bytes(voicetables.payload())
    runs = {engine: _run(tmp, engine) for engine in args.engine}
    engines = list(runs)
    k, v = args.layers, args.voices

    head = "".join(f"{e:>8s}" for e in engines) + (
        "   speedup" if len(runs) == 2 else ""
    )
    print(f"{len(sounds)} sounds x {v} voices at 48 kHz, share of the realtime budget:")
    print(f"  {'':38s}{head}")

    def row(label, shares):
        speed = f"   x{shares[0] / shares[1]:.2f}" if len(shares) == 2 else ""
        print(f"  {label:38s}" + "".join(f"{s:8.1%}" for s in shares) + speed)

    by = {
        e: sorted(r["res"].values(), key=lambda x: x["share"]) for e, r in runs.items()
    }
    n = len(sounds)
    row("median sound", [by[e][n // 2]["share"] for e in engines])
    row("p90", [by[e][int(n * 0.9)]["share"] for e in engines])
    ref = engines[0]
    worst = sorted(runs[ref]["res"], key=lambda s: -runs[ref]["res"][s]["share"])
    for name in worst[:5]:
        row(name, [runs[e]["res"][name]["share"] for e in engines])
    js_res, wasm_res = runs.get("js", {}).get("res"), runs.get("wasm", {}).get("res")
    speed = sorted(
        (js_res[s]["share"] / wasm_res[s]["share"], s) for s in sounds
    ) if js_res and wasm_res else []  # fmt: skip
    if speed:
        print(
            f"  per-sound speedup: median x{speed[n // 2][0]:.2f}"
            f"  min x{speed[0][0]:.2f} ({speed[0][1]})"
            f"  max x{speed[-1][0]:.2f} ({speed[-1][1]})"
        )
    row(f"{k} layers ({', '.join(HEAVY[:k])}), all live",
        [runs[e]["allLive"]["share"] for e in engines])  # fmt: skip
    row(f"{k} layers, pre-rendered", [runs[e]["prerendered"]["share"] for e in engines])
    for e in engines:
        print(f"silence culling, off -> on ({e}):")
        for name, (off, on) in runs[e]["cull"].items():
            print(f"  {name:38s} {off:.1%} -> {on:.1%}")
        off, on = runs[e]["idle"]
        print(f"  {'idle voice node':38s} {off:.2%} -> {on:.2%}")
    if args.round6:
        print("phase warp, off -> on:")
        for name in ROUND6:
            row(name, [runs[e]["res"][name]["share"] for e in engines])
    print(f"the live sampler, {v} keys from the root to two octaves over it:")
    for name in spec["samplers"]:
        row(name, [runs[e]["samplers"][name] for e in engines])


if __name__ == "__main__":
    main()
