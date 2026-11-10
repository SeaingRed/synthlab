# ruff: noqa: E501  (the node harness reads better on one line)
"""One voice on the live keys plays what the loop renders: a mono or
legato phrase run through voice-worklet.js's own
code under node — JS and the WebAssembly engine — against the engine's
lines (`sequencer.mono_lines`: one `Synth.note` a line, cut where the next
takes the voice), sample for sample.

The keys' side is noteOn / noteOff at the samples the phrase puts them;
the engine's is what `arrange._mono_timeline` does with the same notes.
Measured: every phrase within 3.5e-3 of its peak, sample for sample, on
both engines, and its 95th percentile under 1e-4 — the error a lone poly
note already has (2.7e-3 in its attack's first samples, 7e-5 after).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from synthlab.core import SR, note_to_hz
from synthlab.sequencer import cut_line, mono_lines, note_span
from synthlab.studio import voicetables, worklet
from synthlab.synth import Patch, Synth

WORKLET = Path("synthlab/studio/static/voice-worklet.js").resolve()
WASM = Path("synthlab/studio/static/voice-dsp.wasm").resolve()

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the worklet's own code"
)

HARNESS = """
globalThis.sampleRate = 44100;
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { onmessage: null, postMessage() {} }; }
};
let Voice = null;
globalThis.registerProcessor = (_name, cls) => { Voice = cls; };
const fs = require("fs");
// the shipped file, run under the shims above (as the parity corpus runs it)
eval(fs.readFileSync(process.argv[2], "utf8"));
const mod = process.argv[5] ? new WebAssembly.Module(fs.readFileSync(process.argv[5])) : null;
const job = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const bin = fs.readFileSync(process.argv[4]);
const hl = bin.readUInt32LE(0);
const meta = JSON.parse(bin.subarray(4, 4 + hl).toString("utf8"));
const data = new Float32Array(bin.buffer.slice(bin.byteOffset + 4 + hl, bin.byteOffset + bin.length));
const v = new Voice();
v.port.onmessage({ data: { type: "tables", meta, data } });
if (mod) v.port.onmessage({ data: { type: "wasm", module: mod } });
const L = new Float32Array(job.total);
let i = 0, e = 0;
while (i < job.total) {
  while (e < job.events.length && job.events[e].at <= i) {
    const ev = job.events[e++];
    v.port.onmessage({ data: ev.on
      ? { type: "noteOn", id: ev.id, freq: ev.freq, vel: 1, p: job.p, seed: 11 }
      : { type: "noteOff", id: ev.id } });
  }
  let n = Math.min(128, job.total - i);
  if (e < job.events.length) n = Math.min(n, job.events[e].at - i);
  const l = new Float32Array(n), r = new Float32Array(n);
  v.process([], [[l, r]]);
  L.set(l, i); i += n;
}
fs.writeFileSync(job.out, Buffer.from(L.buffer));
"""

# (start, end) in samples and the pitch: what a hand plays on the keys
PHRASES = {
    # overlapping keys glide (always), the next line glides in from E4
    "legato": (
        Patch(voice_mode="legato", glide=0.08, cutoff=1800.0),
        [(0, 26460, "C4"), (17640, 37485, "E4"), (44100, 63945, "G4")],
    ),
    # last-note priority: E4 up while C4 is held goes back to C4, at once
    "legato_return": (
        Patch(voice_mode="legato", osc1="square", key_track=1.0),
        [(0, 52920, "C4"), (13230, 26460, "E4")],
    ),
    # mono, gliding only while a key is down: D4 glides, F4 retriggers flat
    "mono_legato_glide": (
        Patch(voice_mode="mono", glide_mode="legato", glide=0.05, release=0.3),
        [(0, 22050, "C4"), (17640, 39690, "D4"), (52920, 66150, "F4")],
    ),
    # mono through a warp and unison (the module's stack), always gliding
    "mono_warp": (
        Patch(
            voice_mode="mono",
            glide=0.04,
            unison=2,
            osc1_warp="pd",
            osc1_warp_amt=0.6,
            release=0.15,
        ),
        [(0, 15435, "A3"), (11025, 26460, "C4"), (30870, 41895, "E4")],
    ),
}


def _engine(p: Patch, notes: list, total: int) -> np.ndarray:
    """What the loop renders: the lines, each cut where the next starts."""
    events = [
        (a / SR, (b - a) / SR, (b - a) / SR, pitch, False) for a, b, pitch in notes
    ]
    out = np.zeros(total)
    lines = mono_lines(events, p)
    for k, ln in enumerate(lines):
        a, _b, pitch = notes[ln.first]
        y = Synth(p).note(
            pitch,
            note_span(ln.dur, p),
            1.0,
            gate=ln.gate,
            from_pitch=ln.from_pitch,
            path=ln.path,
        )
        cut = None if ln.cut is None else (notes[lines[k + 1].first][0] - a) / SR
        y = cut_line(y, cut)[:, 0]
        m = min(len(y), total - a)
        out[a : a + m] += y[:m]
    return out


def _live(tmp: Path, name: str, p: Patch, notes: list, total: int, wasm: bool):
    events = []
    for k, (a, b, pitch) in enumerate(notes):
        events.append({"at": a, "on": True, "id": f"k{k}", "freq": note_to_hz(pitch)})
        events.append({"at": b, "on": False, "id": f"k{k}"})
    events.sort(key=lambda e: (e["at"], e["on"]))
    out = tmp / f"{name}.{'wasm' if wasm else 'js'}.f32"
    job = {
        "p": {**p.to_json(), **worklet.voice_tables()},
        "events": events,
        "total": total,
        "out": str(out),
    }
    (tmp / "job.json").write_text(json.dumps(job), encoding="utf-8")
    cmd = ["node", str(tmp / "h.cjs"), str(WORKLET), str(tmp / "job.json"),
           str(tmp / "tables.bin")]  # fmt: skip
    if wasm:
        cmd.append(str(WASM))
    subprocess.run(cmd, check=True, timeout=300)
    return np.fromfile(out, dtype=np.float32).astype(np.float64)


@pytest.mark.parametrize("wasm", [False, True], ids=["js", "wasm"])
@pytest.mark.parametrize("name", list(PHRASES))
def test_a_one_voice_phrase_plays_what_the_loop_renders(tmp_path, name, wasm):
    p, notes = PHRASES[name]
    assert worklet.can_play(p)
    (tmp_path / "h.cjs").write_text(HARNESS, encoding="utf-8")
    (tmp_path / "tables.bin").write_bytes(voicetables.payload())
    total = max(b for _a, b, _p in notes) + int(0.5 * SR)
    eng = _engine(p, notes, total)
    live = _live(tmp_path, name, p, notes, total, wasm)
    peak = float(np.max(np.abs(eng)))
    err = np.abs(eng - live) / peak
    # a lone poly note sits at 2.7e-3 in its first milliseconds (the
    # attack's first samples) and under 7e-5 after; a retrigger where the
    # loop glides, or a glide where it retriggers, is off by tenths
    assert float(err.max()) < 5e-3, float(err.max())
    assert float(np.percentile(err, 95)) < 1e-4, float(np.percentile(err, 95))


def test_a_legato_line_glides_without_retriggering():
    """The second key of a legato line re-aims the one voice: its pitch
    moves there and the amp envelope does not start again (no dip, no
    second attack) — where a mono line's second note does."""
    notes = [(0.0, 0.6, 0.6, "C4", False), (0.3, 0.6, 0.6, "G4", False)]
    leg = mono_lines(notes, Patch(voice_mode="legato"))
    mono = mono_lines(notes, Patch(voice_mode="mono"))
    assert len(leg) == 1 and leg[0].path == [(0.3, "G4")]
    assert len(mono) == 2 and mono[0].cut == pytest.approx(0.3)


def test_the_loop_plays_a_one_voice_patch_as_its_lines(monkeypatch):
    """The loop's note schedule (`arrange.build_song`): a mono / legato
    patch takes the time-ordered timeline even with no slide, arp or strum,
    and renders its lines — a chord on it is one voice, not three."""
    from dataclasses import replace

    from synthlab.studio import arrange
    from synthlab.studio.state import Arrangement

    arr = Arrangement(
        bpm=120.0,
        bars=1,
        steps_per_bar=8,
        cells=[["A2", 0], ["C3", 0], ["E3", 0], ["G3", 4]],
    )
    seen = []
    real = arrange._mono_timeline
    monkeypatch.setattr(
        arrange,
        "_mono_timeline",
        lambda *a, **k: seen.append(len(a[3])) or real(*a, **k),
    )
    p = Patch(voice_mode="mono", release=0.05)
    mono = arrange.build_song(p, arr).render(normalize=0)
    assert seen == [4]
    poly = arrange.build_song(replace(p, voice_mode="poly"), arr).render(normalize=0)
    assert seen == [4]  # poly: the per-row path, as ever
    # the chord on step 0: one voice (the last of it) against three
    head = slice(int(0.05 * SR), int(0.4 * SR))
    assert np.sqrt(np.mean(mono[head] ** 2)) < 0.75 * np.sqrt(np.mean(poly[head] ** 2))
