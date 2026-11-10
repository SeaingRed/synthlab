"""Live keys run one lane in the worklet; the rest play pre-rendered notes.

Three heavy layers held 16 voices deep took 62% of the audio thread's
realtime budget (tools/bench_live_voice.py). Only the EDITED slot needs to
answer a slider at once, so only it runs in the worklet: every other lane a
key sounds on (the main lane, the followers — `audible_layers(for_keys=)`,
unchanged) plays `/note/{pitch}.wav?view=&layer=N`, that lane's note at its
octave and gain with its own FX baked in (`render.lane_note`, the loop's
layer stem for that note), through the live master chain. keys.js warms and
swaps those renders exactly as it does the key WAVs; livefx.js routes each
note to a lane's renders — or, while none is at hand, to its voice node.

- a lane render IS the loop's layer render for that note (FX and all);
- /patch.json's `layers[].note_key` is the key those renders are filed under
  (one definition, `fxlive.lane_key`), and moves only with that lane;
- a lane whose chain is clocked (gate / pump / sidechain) stays live;
- `?live=all` (the A/B dev flag) puts every lane back in the worklet.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from scipy.io import wavfile

from synthlab.sequencer import midi
from synthlab.studio import fxlive
from synthlab.studio.factory import FACTORY
from synthlab.studio.render import _render_job, _stem_jobs, chain_arr, lane_note
from synthlab.studio.routes.audio import LANE_SETTLE, render_lane
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch, Synth, at_bpm

from .test_hold_seams import FLOOR_DB, TOLERANCE_DB, WIN, _step_db

LIVEFX = Path("synthlab/studio/static/livefx.js").resolve()
node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs livefx.js")

FX = [
    {"kind": "drive", "params": {"amount": 2.5, "mix": 0.7}},
    {"kind": "delay", "params": {"time": 0.18, "feedback": 0.4, "mix": 0.3}},
    {"kind": "reverb", "params": {"mix": 0.3}},
]


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path))


def _setup(s, *, fx=FX, gain=0.7, octave=-1, cells=(("C4", 0),)):
    """Slot 0 edited and main, playing `cells`; slot 1 following at
    `octave` with `fx` and `gain`; slot 2 a plain slot with its own notes."""
    edited = Patch(name="L0", osc1="saw", release=0.3)
    s.patch = edited
    s.layers = [
        {"patch": edited.to_json(), "cells": [], "muted": False, "gain": 1.0, "fx": []},
        {
            "patch": Patch(name="L1", osc1="square", unison=2, release=0.5).to_json(),
            "cells": [],
            "follow": {"octave": octave},
            "muted": False,
            "gain": gain,
            "fx": fx,
        },
        {
            "patch": Patch(name="L2", osc1="sine").to_json(),
            "cells": [["A4", 8]],
            "muted": False,
            "gain": 1.0,
            "fx": [],
        },
    ]
    s.active_layer = 0
    s.main_layer = 0
    s.layer_focus = False
    s.arrangement = replace(s.arrangement, cells=[list(c) for c in cells])


def _wav(r) -> np.ndarray:
    _, x = wavfile.read(io.BytesIO(r.content))
    return x.astype(np.float64) / (32768.0 if x.dtype == np.int16 else 1.0)


def test_a_lane_note_is_the_loops_layer_render_of_that_note(client, monkeypatch):
    """The loop's layer stem for a lone note, FX baked in, is exactly
    `lane_note` of the note Synth.note was asked for — the same path the
    keys' pre-rendered lanes take."""
    svc = client.app.state.svc
    _setup(svc.state)
    inp = svc.render_inputs("perform")
    job = next(j for j in _stem_jobs(inp) if j.name == "layer2")
    calls = []
    real = Synth.note

    def spy(self, pitch, duration, velocity=1.0, gate=None, **kw):
        if self.patch.name == "L1":
            calls.append((self.patch, pitch, duration, velocity, gate))
        return real(self, pitch, duration, velocity, gate=gate, **kw)

    monkeypatch.setattr(Synth, "note", spy)
    stem = _render_job(job)
    ((p, pitch, span, vel, gate),) = calls
    assert midi(pitch) == midi("C3")  # the main lane's C4, an octave down
    lay = svc.state.layers[1]
    got = lane_note(p, pitch, span, gate, vel, lay["gain"], lay["fx"], chain_arr(inp))
    np.testing.assert_array_equal(got, stem)
    assert np.max(np.abs(got)) > 0.05


def test_the_lane_route_serves_that_render_under_patch_jsons_key(client):
    s = client.app.state.svc.state
    _setup(s)
    j = client.get("/patch.json?view=editor").json()
    (lane,) = j["layers"]  # the plain slot 2 is never a key's
    assert lane["slot"] == 2 and lane["mode"] == "buffer" and lane["live_ok"] is True
    r = client.get("/note/C4.wav?view=editor&layer=2")
    assert r.status_code == 200 and r.headers["x-patch-key"] == lane["note_key"]
    lay = s.layers[1]
    voice = (1, at_bpm(Patch.from_dict(lay["patch"]), s.arrangement.bpm), -1, 0.7)
    arr = fxlive.lane_arr(s.arrangement)
    assert (
        r.content
        == render_lane(voice, lay["fx"], arr, "C4", False, lane["note_key"]).wav
    )
    # the lane's note an octave down with its FX, only its silent padding cut
    got = _wav(r)
    ref = lane_note(
        voice[1], midi("C3"), _note_dur(voice[1]), None, 1.0, 0.7, lay["fx"], arr
    )
    n = len(got)
    assert n <= len(ref) and np.max(np.abs(ref[n:]), initial=0) < 1e-4
    assert np.max(np.abs(got - ref[:n] / max(1.0, np.max(np.abs(ref))))) < 1e-4
    # never a lane the key's pre-rendered stack doesn't hold
    assert client.get("/note/C4.wav?view=editor&layer=1").status_code == 404  # edited
    assert client.get("/note/C4.wav?view=editor&layer=3").status_code == 404  # plain


def _note_dur(p):
    from synthlab.studio.soundmeta import live_note_duration

    return live_note_duration(p, False)


def test_a_held_lane_loops_after_its_fx_have_built_up(client):
    s = client.app.state.svc.state
    _setup(s)
    r = client.get("/note/C4.wav?view=editor&layer=2&hold=1")
    start, end, gate = map(float, r.headers["x-loop"].split(","))
    p = Patch.from_dict(s.layers[1]["patch"])
    assert start >= p.attack + p.decay + LANE_SETTLE - 1e-3
    assert start < end <= gate
    # dry: loops where a key's own render would
    _setup(s, fx=[])
    r = client.get("/note/C4.wav?view=editor&layer=2&hold=1")
    assert float(r.headers["x-loop"].split(",")[0]) < p.attack + p.decay + 0.1


def test_a_lanes_key_moves_with_that_lane_only(client):
    s = client.app.state.svc.state
    _setup(s)

    def key():
        return client.get("/patch.json?view=editor").json()["layers"][0]["note_key"]

    k0 = key()
    s.patch = replace(s.patch, cutoff=900.0)  # an edit of the edited slot
    assert key() == k0
    s.layers[1]["gain"] = 0.5
    k1 = key()
    assert k1 != k0
    s.layers[1]["fx"] = FX[:1]
    assert key() not in (k0, k1)
    s.layers[1]["follow"] = {"octave": 0}
    assert len({k0, k1, key()}) == 3


def test_a_clocked_lane_stays_live_and_all_live_is_the_ab_flag(client):
    s = client.app.state.svc.state
    _setup(s, fx=[{"kind": "pump", "params": {}}])
    j = client.get("/patch.json?view=editor").json()
    assert j["layers"][0]["mode"] == "live"
    assert client.get("/note/C4.wav?view=editor&layer=2").status_code == 404
    _setup(s)
    j = client.get("/patch.json?view=editor&live=all").json()
    assert j["layers"][0]["mode"] == "live" and j["worklet_ok"] is True
    # the audible set is the same either way (test_live_layers pins it)
    base = client.get("/patch.json?view=editor").json()
    assert [(x["slot"], x["octave"]) for x in j["layers"]] == [
        (x["slot"], x["octave"]) for x in base["layers"]
    ]


ROUTE = r"""
globalThis.window = {};
const posted = [], lanes = [];
let nodes = 0;
const meterEl = { hidden: true, textContent: "",
  classList: { on: false, toggle(_c, v) { this.on = v; } } };
globalThis.document = { addEventListener() {}, getElementById: () => meterEl };
globalThis.performance = { now: (() => { let t = 0; return () => (t += 300); })() };
globalThis.AudioWorkletNode = class {
  constructor(_c, name) {
    this.i = name === "synthlab-voice" ? nodes++ : -1;
    const i = this.i;
    const note = (m) => i >= 0 && m.type !== "tables" && m.type !== "params";
    this.port = { postMessage: (m) => { if (note(m)) posted.push([i, m]); } };
    (globalThis.ports ??= []).push(this.port);
  }
  connect(x) { return x; }
  disconnect() {}
};
const gainNode = () => ({ gain: { setTargetAtTime() {} },
  connect(x) { return x; }, disconnect() {} });
const ctx = { currentTime: 0, sampleRate: 48000, createGain: gainNode,
  audioWorklet: { addModule: async () => {} } };
const hdr = Buffer.from("{}  ");
const bin = new Uint8Array(4 + hdr.length);
new DataView(bin.buffer).setUint32(0, hdr.length, true); bin.set(hdr, 4);
globalThis.fetch = async () => ({ arrayBuffer: async () => bin.buffer });
// eval, deliberately: the shipped first-party island, as the browser runs it
eval(require("fs").readFileSync(process.argv[2], "utf8"));
const S = window.synthlab;
const warm = new Set(JSON.parse(process.argv[4]));
Object.assign(S, {
  getCtx: () => ctx, keyBus: () => gainNode(),
  holdOf: (p, h) => !!(h && p.sustain > 0.05),
  hasLane: (slot, midi, hold) => warm.has(`${slot}:${midi}|${hold ? 1 : 0}`),
  laneOn: (id, ls, midi, vel, hold) =>
    lanes.push(["on", id, ls.map((l) => l.slot), midi, vel, hold]),
  laneOff: (id) => lanes.push(["off", id]),
  laneIds: () => ["k"],
});
(async () => {
  const voice = await S.live.start(JSON.parse(process.argv[3]));
  const on = { type: "noteOn", id: "k", freq: 440, vel: 0.8, hold: true, p: {} };
  voice.port.postMessage(on);
  voice.port.postMessage({ type: "bend", id: "k", freq: 880, time: 0.02 });
  voice.port.postMessage({ type: "noteOff", id: "k" });
  voice.port.postMessage({ type: "allOff" });
  const notes = posted.map(([i, m]) => [i, m.type, m.freq ?? null]);
  // the meter: two voice nodes report 0.5 and 0.4 of their budget
  const q = 64, ms = (share) => share * q * 128 / 48000 * 1000;
  ports[0].onmessage({ data: { type: "load", ms: ms(0.5), quanta: q, voices: 12 } });
  ports[1].onmessage({ data: { type: "load", ms: ms(0.4), quanta: q, voices: 4 } });
  ports[1].onmessage({ data: { type: "load", ms: ms(0.4), quanta: q, voices: 4 } });
  const meter = [meterEl.hidden, meterEl.textContent, meterEl.classList.on];
  process.stdout.write(JSON.stringify({ notes, lanes, meter }));
})();
"""


def _route(tmp_path, warm):
    lane = {"fx": [], "gain": 1, "sustain": 0.7}
    stack = {
        "fx": [], "master": [], "gain": 1, "tables": "/t.bin", "heard": True,
        "slot": 1, "octave": 0,
        "layers": [
            {**lane, "slot": 2, "octave": -1, "mode": "buffer", "live_ok": True},
            {**lane, "slot": 3, "octave": 1, "mode": "buffer", "live_ok": False},
            {**lane, "slot": 4, "octave": 0, "mode": "live", "live_ok": True},
        ],
    }  # fmt: skip
    h = tmp_path / "route.cjs"
    h.write_text(ROUTE, encoding="utf-8")
    out = subprocess.run(
        ["node", str(h), str(LIVEFX), json.dumps(stack), json.dumps(warm)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return json.loads(out.stdout)


@node
def test_livefx_routes_a_key_to_renders_or_the_worklet(tmp_path):
    """Cold: a buffer lane whose voice can stand in plays live; one that
    can't waits on its render (laneOn); a live lane is always live. Warm:
    both buffer lanes play renders, at the key's own pitch (the server
    applies the octave), and their voice nodes get no note; a bend
    re-strikes them at the new pitch; noteOff/allOff release them."""
    cold = _route(tmp_path, [])
    assert cold["notes"] == [
        [0, "noteOn", 440], [1, "noteOn", 220], [3, "noteOn", 440],
        [0, "bend", 880], [1, "bend", 440], [3, "bend", 880],
        [0, "noteOff", None], [1, "noteOff", None], [2, "noteOff", None],
        [3, "noteOff", None],
        [0, "allOff", None], [1, "allOff", None], [2, "allOff", None],
        [3, "allOff", None],
    ]  # fmt: skip
    assert cold["lanes"] == [
        ["on", "k", [3], 69, 0.8, True],
        ["on", "k", [3], 81, 0.8, True],
        ["off", "k"],
        ["off", "k"],
    ]
    warm = _route(tmp_path, ["2:69|1"])
    assert [n for n in warm["notes"] if n[1] != "noteOff" and n[1] != "allOff"] == [
        [0, "noteOn", 440], [3, "noteOn", 440], [0, "bend", 880], [3, "bend", 880],
    ]  # fmt: skip
    assert warm["lanes"][:2] == [
        ["on", "k", [2, 3], 69, 0.8, True],
        ["on", "k", [2, 3], 81, 0.8, True],
    ]


@node
def test_the_cpu_meter_sums_every_nodes_share_and_counts_voices(tmp_path):
    """Worklet nodes share one audio thread: their shares add (0.5 + 0.4),
    smoothed; the voices add; past 80% the meter turns red."""
    hidden, text, hot = _route(tmp_path, [])["meter"]
    assert hidden is False
    assert text.startswith("CPU ") and text.endswith("· 16 voices")
    share = int(text.split()[1].rstrip("%"))
    assert 60 <= share <= 90  # 0.9, smoothed from 0 over the reports
    assert hot == (share > 80)


SEAM_FX = {
    "reverb": [{"kind": "reverb", "params": {"mix": 0.35, "decay": 2.5}}],
    "delay": [{"kind": "delay", "params": {"time": 0.3, "feedback": 0.5, "mix": 0.35}}],
    "chorus+verb": [
        {"kind": "chorus", "params": {"mix": 0.5}},
        {"kind": "reverb", "params": {"mix": 0.25}},
    ],
}


@pytest.mark.parametrize("name", list(SEAM_FX))
def test_a_held_lane_with_fx_meets_itself(name):
    """test_hold_seams' bar (the seam's level step within the sound's own
    30 ms movement + 0.6 dB) for pre-rendered lanes whose FX build up over
    the note: their loop starts LANE_SETTLE later, once the room has filled."""
    over = []
    for p in SEAM_PADS:
        for pitch in ("C3", "C4"):
            voice = (1, at_bpm(p, 120.0), 0, 1.0)
            r = render_lane(voice, SEAM_FX[name], Arrangement(), pitch, True, "k")
            out = r.audio()
            a, b, _ = (round(float(t) * 44100) for t in r.analysis["loop"].split(","))
            for c in range(2):
                seg = out[a:b, c]
                seam = _step_db(seg[:WIN], seg[-WIN:])
                own = [
                    _step_db(seg[i + WIN : i + 2 * WIN], seg[i : i + WIN])
                    for i in range(0, len(seg) - 2 * WIN, WIN // 2)
                ]
                d = seam - max(FLOOR_DB, float(np.percentile(own, 95)))
                if d > TOLERANCE_DB:
                    over.append(f"{p.name} @ {pitch} ch{c}: {d:+.1f} dB")
    assert not over, over


SEAM_PADS = [  # the slow-attack sustained factory sounds (3 today)
    replace(Patch.from_dict(f["patch"]), name=f["name"])
    for f in FACTORY
    if f["patch"].get("sustain", 0.7) > 0.3 and f["patch"].get("attack", 0) > 0.05
][:4]
