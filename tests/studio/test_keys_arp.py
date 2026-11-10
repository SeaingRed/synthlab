"""The keys arp's two halves that run without a browser, under node from the
shipped files:

- `window.synthlab.arpSchedule` (static/arp.js's pure half): what each step
  of a run plays — up, down, up-down, random (a seeded shuffle a cycle: the
  same run every time it starts), as played, chord — over 1-4 octaves, and
  where a step falls on the grid (`stepAt`: from a bar line or the press,
  swing moving the odd steps later, as `arrange.py`'s swing does).
- voice-worklet.js's `due` queue: a noteOn / noteOff with `at` (the
  context's time) waits for the render quantum nearest that time, and the
  voice is then the one a plain noteOn at that quantum makes — sample for
  sample; an allOff drops what waits.

What this cannot hold: the main thread's timers (the browser journeys,
`tests/browser/test_keys_arp.py`, read every `at` the voice is sent) or how
an arpeggio feels (nobody has listened).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from synthlab.studio import voicetables, worklet
from synthlab.synth import Patch

ARP = Path("synthlab/studio/static/arp.js").resolve()
VOICE = Path("synthlab/studio/static/voice-worklet.js").resolve()
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the islands' own code"
)

SCHEDULE = """
globalThis.window = { synthlab: {} };
const fs = require("fs");
// eval, deliberately: arp.js is a classic script; with no `document` it
// publishes its pure half and wires nothing
eval(fs.readFileSync(process.argv[2], "utf8"));
const A = window.synthlab.arpSchedule;
const calls = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
process.stdout.write(JSON.stringify(calls.map(([op, ...args]) => A[op](...args))));
"""


def schedule(calls, tmp_path):
    (tmp_path / "s.cjs").write_text(SCHEDULE, encoding="utf-8")
    (tmp_path / "calls.json").write_text(json.dumps(calls), encoding="utf-8")
    out = subprocess.run(
        ["node", str(tmp_path / "s.cjs"), str(ARP), str(tmp_path / "calls.json")],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(out.stdout)


# a triad as piano.js hands it over (`arpHeld`): in the order played
E_C_G = [{"midi": 64, "vel": 1}, {"midi": 60, "vel": 1}, {"midi": 67, "vel": 1}]


def run_of(mode, oct_, n, tmp_path, held=E_C_G):
    got = schedule([["pick", held, mode, oct_, i] for i in range(n)], tmp_path)
    return [x if len(x) > 1 else x[0] for x in got]


def test_the_modes_play_the_held_notes_in_their_order(tmp_path):
    assert run_of("up", 1, 7, tmp_path) == [60, 64, 67, 60, 64, 67, 60]
    assert run_of("down", 1, 4, tmp_path) == [67, 64, 60, 67]
    # up-down: the top and bottom once a cycle
    assert run_of("updown", 1, 8, tmp_path) == [60, 64, 67, 64, 60, 64, 67, 64]
    assert run_of("played", 1, 4, tmp_path) == [64, 60, 67, 64]
    assert run_of("chord", 1, 2, tmp_path) == [[60, 64, 67], [60, 64, 67]]


def test_octaves_carry_the_run_up(tmp_path):
    assert run_of("up", 2, 7, tmp_path) == [60, 64, 67, 72, 76, 79, 60]
    assert run_of("down", 2, 3, tmp_path) == [79, 76, 72]
    assert run_of("updown", 2, 10, tmp_path) == [60, 64, 67, 72, 76, 79, 76, 72, 67, 64]
    assert run_of("chord", 3, 4, tmp_path) == [
        [60, 64, 67],
        [72, 76, 79],
        [84, 88, 91],
        [60, 64, 67],
    ]
    # four octaves of a single note: a run of octaves
    assert run_of("up", 4, 5, tmp_path, [{"midi": 48}]) == [48, 60, 72, 84, 48]


def test_random_is_each_note_once_a_cycle_and_the_same_every_time(tmp_path):
    a = run_of("random", 2, 24, tmp_path)
    assert a == run_of("random", 2, 24, tmp_path)  # seeded: repeatable
    cycles = [a[i : i + 6] for i in range(0, 24, 6)]
    for c in cycles:
        assert sorted(c) == [60, 64, 67, 72, 76, 79]  # a shuffle, nothing lost
    assert len({tuple(c) for c in cycles}) > 1  # another order each cycle


def test_a_step_falls_on_the_grid_and_swing_moves_the_odd_ones(tmp_path):
    got = schedule(
        [
            ["stepAt", 10.0, 10.0, 0.25, 0.0],  # the press is step 0
            ["stepAt", 10.0, 10.01, 0.25, 0.0],  # just after: the next
            ["stepAt", 10.0, 10.25, 0.25, 0.0],  # exactly on one: that one
            ["stepAt", 10.0, 10.26, 0.25, 0.3],  # an odd step, swung
            ["stepAt", 10.0, 10.6, 0.25, 0.3],  # an even step: as it was
        ],
        tmp_path,
    )
    assert [g["k"] for g in got] == [0, 1, 1, 2, 3]
    assert got[0]["t"] == 10.0 and got[1]["t"] == 10.25 and got[2]["t"] == 10.25
    assert abs(got[3]["t"] - 10.5) < 1e-9  # k = 2: even
    assert abs(got[4]["t"] - (10.75 + 0.3 * 0.25)) < 1e-9  # k = 3: odd, swung
    assert abs(got[4]["base"] - 10.75) < 1e-9


WORKLET = """
globalThis.sampleRate = 44100;
globalThis.currentTime = 0;
globalThis.AudioWorkletProcessor = class {
  constructor() { this.port = { onmessage: null, postMessage() {} }; }
};
const reg = {};
globalThis.registerProcessor = (name, cls) => { reg[name] = cls; };
const fs = require("fs");
// eval, deliberately: the shipped first-party worklet files, under shims
eval(fs.readFileSync(process.argv[2], "utf8"));
for (const f of ["sampler-worklet", "grain-worklet"])
  eval(fs.readFileSync(process.argv[2].replace("voice-worklet", f), "utf8"));
const job = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const bin = fs.readFileSync(process.argv[4]);
const hl = bin.readUInt32LE(0);
const meta = JSON.parse(bin.subarray(4, 4 + hl).toString("utf8"));
const data = new Float32Array(
  bin.buffer.slice(bin.byteOffset + 4 + hl, bin.byteOffset + bin.length));
const outs = [];
for (const plan of job.plans) {
  const proc = new reg["synthlab-voice"]();
  proc.port.onmessage({ data: { type: "tables", meta, data } });
  const L = new Float32Array(job.blocks * 128);
  for (let b = 0; b < job.blocks; b++) {
    globalThis.currentTime = (b * 128) / sampleRate;
    for (const [at, m] of plan)
      if (at === b) proc.port.onmessage({ data: { ...m, p: job.p } });
    const l = new Float32Array(128), r = new Float32Array(128);
    proc.process([], [[l, r]]);
    L.set(l, b * 128);
  }
  outs.push(Array.from(L));
}
process.stdout.write(JSON.stringify(outs));
"""


def test_a_note_due_later_starts_in_the_block_nearest_its_time(tmp_path):
    """Posted at block 0 with `at` = block 40 and a bit (under half a block:
    it rounds down) — and its noteOff likewise at block 80 — the voice is
    the voice a plain noteOn at block 40 and a noteOff at block 80 make,
    sample for sample; an allOff before it is due drops it."""
    p = {**Patch().to_json(), **worklet.voice_tables()}
    q = 128 / 44100
    on = {"type": "noteOn", "id": "v1", "freq": 261.63, "vel": 1}
    off = {"type": "noteOff", "id": "v1"}
    plans = [
        [[0, {**on, "at": 40 * q + 0.3 * q}], [0, {**off, "at": 80 * q + 0.2 * q}]],
        [[40, on], [80, off]],
        [[0, {**on, "at": 40 * q + 0.7 * q}], [0, {**off, "at": 80 * q}]],  # rounds up
        [[41, on], [80, off]],
        [[0, {**on, "at": 40 * q}], [10, {"type": "allOff"}]],  # dropped
    ]
    job = {"p": p, "blocks": 160, "plans": plans}
    (tmp_path / "w.cjs").write_text(WORKLET, encoding="utf-8")
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    (tmp_path / "tables.bin").write_bytes(voicetables.payload())
    res = subprocess.run(
        [
            "node",
            str(tmp_path / "w.cjs"),
            str(VOICE),
            str(tmp_path / "job.json"),
            str(tmp_path / "tables.bin"),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    timed, plain, late, plain_late, dropped = (
        np.array(x) for x in json.loads(res.stdout)
    )
    assert np.abs(timed[: 40 * 128]).max() == 0  # nothing before its block
    assert np.abs(timed[40 * 128 : 41 * 128]).max() > 0  # ... and it starts there
    assert np.array_equal(timed, plain)
    assert np.array_equal(late, plain_late)
    assert np.abs(dropped).max() == 0
