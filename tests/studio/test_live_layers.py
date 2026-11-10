"""Live keys play the layers ▶ plays (live keys x the layer strip).

Picking "unmuted, with a patch and cells" in `fxlive.live_stack` would
leave a following layer (no cells of its own) silent from the keys with
its octave never applied, and ignore solo, the "This layer" focus and the
pinned main lane. One
predicate, `state.audible_layers(state, view, for_keys=)`, decides both
what `render_inputs` renders and what a key sounds on:

- a key is the main lane's note played by hand, so it sounds on the edited
  slot, the main lane and every follower (each at its follow octave, gain
  and FX) — never on a plain slot playing its own pattern;
- mute, solo (mute wins) and "This layer" (bench views: the edited slot
  only) apply exactly as they do to ▶;
- the WAV fallback (`/note`) renders that same set, dry.
"""

from __future__ import annotations

import io
import itertools
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from scipy.io import wavfile

from synthlab.effects import to_stereo
from synthlab.sequencer import midi
from synthlab.studio import fxlive
from synthlab.studio.audition import phrase_arrangement
from synthlab.studio.routes.audio import render_note
from synthlab.studio.server import create_app
from synthlab.studio.state import audible_layers
from synthlab.synth import Patch, Synth

LIVEFX = Path("synthlab/studio/static/livefx.js").resolve()
node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs livefx.js")

CELLS = {0: [["C4", 0], ["E4", 4]], 1: [["G3", 2]], 2: [["A4", 8]]}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return TestClient(create_app(data_dir=tmp_path_factory.mktemp("live_layers")))


def _setup(s, *, active, follow1, follow2, mute, solo, focus, main=0):
    """Three sounding slots named L0..L2, each with notes of its own; slot 1
    and 2 optionally following the main lane at an octave."""
    layers = []
    for i, follow in enumerate((None, follow1, follow2)):
        lay = {
            "patch": Patch(name=f"L{i}", osc1="saw").to_json(),
            "cells": [list(c) for c in CELLS[i]],
            "muted": i in mute,
            "gain": 1.0 - 0.1 * i,
            "fx": [],
            "silent": False,
            "seq": None,
            "curves": None,
        }
        if follow is not None:
            lay["follow"] = {"octave": follow}
        if i in solo:
            lay["solo"] = True
        layers.append(lay)
    s.layers = layers
    s.active_layer = active
    s.main_layer = main
    s.patch = Patch(name=f"L{active}", osc1="saw")
    s.arrangement = replace(s.arrangement, cells=[list(c) for c in CELLS[active]])
    s.layer_focus = focus


def _low(cells) -> int:
    return min(midi(p) for p, _ in cells)


def _render_set(svc, view: str) -> dict[str, int | None]:
    """What `view`'s ▶ hears, read off its RenderInputs: slot name -> the
    octave it plays the main lane's notes at (None: its own notes)."""
    s = svc.state
    inp = svc.render_inputs(view)
    bench = view == "editor"
    src = (
        phrase_arrangement(s.arrangement, s.audition, keep_drums=s.vs_beat)
        if bench
        else s.main_arrangement()
    )
    out: dict[str, int | None] = {}

    def played(name: str, cells) -> int | None:
        """None for the slot's own notes, else the octave of `src` it plays."""
        cells = [list(c) for c in cells]
        if cells == CELLS[int(name[1])]:
            return None
        d = _low(cells) - _low(src.cells)
        assert d % 12 == 0, (name, cells, src.cells)
        return d // 12

    if not inp.main_muted:
        out[inp.patch.name] = played(inp.patch.name, inp.arr.cells)
    for lay in inp.layers:
        name = lay["patch"]["name"]
        if "follow" in lay:  # followed lazily at render time
            out[name] = lay["follow"]["octave"]
        else:
            out[name] = played(name, lay["cells"])
    return out


COMBOS = list(
    itertools.product(
        (0, 1),  # the edited slot
        (None, -1, 1),  # slot 1's follow octave
        (None, 0),  # slot 2's
        ((), (0,), (1,), (2,)),  # muted
        ((), (1,), (2,)),  # soloed
        (False, True),  # "This layer"
        ("editor", "perform"),
    )
)


def test_the_live_stack_is_the_renders_audible_set_for_keys(client):
    """For every combination of edited slot, follow on/off and octave, mute,
    solo, focus and view: /patch.json's lanes are the render's audible set
    minus the plain slots that play their own notes (the key isn't theirs),
    each at the octave the render plays it at."""
    svc = client.app.state.svc
    s = svc.state
    checked = 0
    for active, f1, f2, mute, solo, focus, view in COMBOS:
        _setup(
            s, active=active, follow1=f1, follow2=f2, mute=mute, solo=solo, focus=focus
        )
        heard = _render_set(svc, view)
        main = s.main_index()
        want = {}
        for name, octv in heard.items():
            i = int(name[1])
            if i == active:
                want[i] = octv or 0  # the key, at its octave if it follows
            elif octv is not None:
                want[i] = octv  # the main lane (bench) or a follower
            elif i == main:
                want[i] = 0  # loop view: the key stands in for its notes
            # else: a plain slot on its own pattern — not the keys'
        j = client.get(f"/patch.json?view={view}").json()
        got = {lay["slot"] - 1: lay["octave"] for lay in j["layers"]}
        if j["heard"]:
            got[active] = j["octave"]
        combo = (active, f1, f2, mute, solo, focus, view)
        assert got == want, combo
        assert j["slot"] == active + 1
        # the same predicate, read directly
        lanes = audible_layers(s, view, for_keys=True)
        assert {ln.slot: ln.octave for ln in lanes} == want, combo
        # gains ride along
        for lay in j["layers"]:
            assert lay["gain"] == pytest.approx(1.0 - 0.1 * (lay["slot"] - 1))
        checked += 1
    assert checked == len(COMBOS)


def test_rows_by_sound_puts_every_layer_under_the_keys(client):
    """ "rows → sounds" (rowsounds.js): /patch.json?rows=1 hands the keys
    every unmuted slot that holds a sound — a plain one playing its own
    notes too, which the keys otherwise never sound — and the view it
    answers with carries the flag, so the /note renders keys.js asks for
    with that view are of the same set."""
    s = client.app.state.svc.state
    _setup(s, follow1=-1, follow2=None, mute=(), solo=(), focus=False, active=0)
    plain = client.get("/patch.json?view=editor").json()
    assert plain["view"] == "editor"
    assert {lay["slot"] for lay in plain["layers"]} == {2}  # the follower
    j = client.get("/patch.json?view=editor&rows=1").json()
    assert j["view"] == "editor" + fxlive.ROWS
    with_sound = {i + 1 for i, lay in enumerate(s.layers) if lay.get("patch")}
    assert {lay["slot"] for lay in j["layers"]} == with_sound - {1} > {2}
    assert j["slot"] == 1 and j["heard"]
    octaves = {lay["slot"]: lay["octave"] for lay in j["layers"]}
    assert octaves[2] == -1 and all(o == 0 for n, o in octaves.items() if n != 2)
    # mute still silences; solo is not read (a row names its layer outright)
    _setup(s, follow1=-1, follow2=None, mute=(1,), solo=(0,), focus=False, active=0)
    lanes = fxlive.key_lanes(s, "editor" + fxlive.ROWS)
    assert {ln.slot + 1 for ln in lanes} == with_sound - {2}
    # the WAV twin: one lane's render exists for a plain slot in rows mode only
    _setup(s, follow1=None, follow2=None, mute=(), solo=(), focus=False, active=0)
    lane = max(with_sound)
    q = f"/note/C4.wav?layer={lane}&view=editor"
    assert client.get(q).status_code == 404
    assert client.get(q + "%3Arows").status_code == 200


def test_named_cases(client):
    """The rules in words, one case each (bench view)."""
    svc = client.app.state.svc
    s = svc.state

    def lanes(**kw):
        _setup(s, **{"follow1": None, "follow2": None, "mute": (), "solo": (),
                     "focus": False, "active": 0, **kw})  # fmt: skip
        return {ln.slot: ln.octave for ln in audible_layers(s, "editor", for_keys=True)}

    # a follower plays the key at its octave; a plain slot with notes doesn't
    assert lanes(follow1=-1) == {0: 0, 1: -1}
    # editing a follower: it keeps following, the main lane plays the key too
    assert lanes(active=1, follow1=1) == {0: 0, 1: 1}
    # editing a plain slot: it plays the key; the main lane still does
    assert lanes(active=2) == {0: 0, 2: 0}
    # solo: only the soloed; mute wins over solo
    assert lanes(follow1=-1, solo=(1,)) == {1: -1}
    assert lanes(follow1=-1, solo=(1,), mute=(1,)) == {}
    # a muted edited slot is silent, its followers aren't
    assert lanes(follow1=-1, mute=(0,)) == {1: -1}
    # "This layer": only the edited slot, even muted
    assert lanes(follow1=-1, focus=True, mute=(0,)) == {0: 0}
    # the pinned main lane stays the main lane while another slot is edited
    _setup(s, active=2, follow1=0, follow2=None, mute=(), solo=(), focus=False, main=0)
    assert s.main_index() == 0
    # ...and "This layer" is a bench toggle: a loop view ignores it
    _setup(s, active=0, follow1=-1, follow2=None, mute=(), solo=(), focus=True)
    assert {ln.slot for ln in audible_layers(s, "perform", for_keys=True)} == {0, 1}


def _wav(r) -> np.ndarray:
    sr, x = wavfile.read(io.BytesIO(r.content))
    return x.astype(np.float64) / (32768.0 if x.dtype == np.int16 else 1.0)


def test_the_wav_fallback_plays_the_same_lanes(client):
    """/note (the keys' WAV fallback) renders the /patch.json set: every
    lane at its octave and gain, dry; its X-Patch-Key is /patch.json's
    note_key. Alone at unity, a key keeps the single patch's key."""
    svc = client.app.state.svc
    s = svc.state
    _setup(s, active=0, follow1=-1, follow2=None, mute=(), solo=(), focus=False)
    j = client.get("/patch.json?view=editor").json()
    r = client.get("/note/C4.wav?view=editor")
    assert r.headers["x-patch-key"] == j["note_key"]
    p0, p1 = s.patch, Patch.from_dict(s.layers[1]["patch"])
    want = render_note([(0, p0, 0, 1.0), (1, p1, -1, 0.9)], "C4", False)
    assert r.content == want.wav
    # the mix is what it says: slot 0 at C4 plus slot 1 at C3 x 0.9
    dur = len(_wav(r)) / 44100
    rel = max(p0.release, p1.release)
    mix = to_stereo(Synth(p0).note(60, dur, gate=dur - rel))
    mix = mix + 0.9 * to_stereo(Synth(p1).note(48, dur, gate=dur - rel))
    mix = mix / max(1.0, np.max(np.abs(mix)))  # wav_bytes fits a hot peak
    got = _wav(r)
    n = min(len(got), len(mix))
    assert np.max(np.abs(got[:n] - mix[:n])) < 1e-4
    # alone at unity: the old single-patch key and audio
    _setup(s, active=0, follow1=None, follow2=None, mute=(), solo=(), focus=True)
    j = client.get("/patch.json?view=editor").json()
    r = client.get("/note/C4.wav?view=editor")
    assert j["note_key"] == r.headers["x-patch-key"]
    assert r.content == render_note(s.patch, "C4", False).wav
    # nothing heard: /patch.json has no lanes and the key is silent
    _setup(s, active=0, follow1=None, follow2=None, mute=(0,), solo=(), focus=False)
    j = client.get("/patch.json?view=editor").json()
    assert j["heard"] is False and j["layers"] == [] and j["worklet_ok"] is True
    assert not np.any(_wav(client.get("/note/C4.wav?view=editor")))
    # hold renders loop over the whole stack
    _setup(s, active=0, follow1=1, follow2=None, mute=(), solo=(), focus=False)
    r = client.get("/note/C4.wav?view=editor&hold=1")
    assert r.status_code == 200 and "x-loop" in r.headers


def test_a_blocked_lane_blocks_only_when_it_is_heard(client):
    """A muted or soloed-out slot the worklet can't play no longer forces
    the whole keyboard to the WAV fallback; a heard one still does."""
    s = client.app.state.svc.state
    _setup(s, active=0, follow1=-1, follow2=None, mute=(), solo=(), focus=False)
    s.layers[1]["fx"] = [{"kind": "granular", "params": {}}]
    # all-live (the dev flag): every lane runs in the worklet, as before
    j = client.get("/patch.json?view=editor&live=all").json()
    assert j["worklet_ok"] is False and "fx:granular" in j["worklet_blockers"]
    s.layers[1]["muted"] = True
    assert client.get("/patch.json?view=editor&live=all").json()["worklet_ok"]
    s.layers[1]["muted"] = False
    s.layers[2]["fx"] = [{"kind": "granular", "params": {}}]  # plain: not played
    s.layers[1]["fx"] = []
    assert client.get("/patch.json?view=editor&live=all").json()["worklet_ok"]
    # pre-rendered (the default): a lane's own blockers never block the keys
    s.layers[1]["fx"] = [{"kind": "granular", "params": {}}]
    j = client.get("/patch.json?view=editor").json()
    assert j["worklet_ok"] is True and j["layers"][0]["live_ok"] is False
    # a clocked chain can't be pre-rendered: that lane stays live, and blocks
    s.layers[1]["fx"] = [{"kind": "granular", "params": {}}, {"kind": "pump"}]
    j = client.get("/patch.json?view=editor").json()
    assert j["layers"][0]["mode"] == "live" and j["worklet_ok"] is False
    s.layers[1]["fx"] = []


FANOUT = r"""
globalThis.window = {};
const posted = [];
let nodes = 0;
globalThis.AudioWorkletNode = class {
  constructor(_c, name) {
    this.i = name === "synthlab-voice" ? nodes++ : -1;
    const i = this.i;
    const keep = (m) => { if (i >= 0 && m.type !== "tables") posted.push([i, m]); };
    this.port = { postMessage: keep, close() {} }; // a dropped lane's port is closed
  }
  connect(x) { return x; }
  disconnect() {}
};
const gainNode = () => ({ gain: { setTargetAtTime() {} },
  connect(x) { return x; }, disconnect() {} });
const ctx = { currentTime: 0, sampleRate: 48000, createGain: gainNode,
  audioWorklet: { addModule: async () => {} } };
const bus = gainNode();
globalThis.document = { addEventListener() {} };
const hdr = Buffer.from("{}  "); // 4 bytes: the tables start aligned
const bin = new Uint8Array(4 + hdr.length);
new DataView(bin.buffer).setUint32(0, hdr.length, true); bin.set(hdr, 4);
globalThis.fetch = async () => ({ arrayBuffer: async () => bin.buffer });
// eval, deliberately: the shipped first-party island, as the browser runs it
eval(require("fs").readFileSync(process.argv[2], "utf8"));
const S = window.synthlab;
Object.assign(S, { getCtx: () => ctx, keyBus: () => bus });
const stacks = JSON.parse(process.argv[3]);
(async () => {
  const out = [];
  const voice = await S.live.start(stacks[0]);
  for (const p of stacks) {
    S.live.setStack(p);
    posted.length = 0;
    voice.port.postMessage({ type: "noteOn", id: "k", freq: 440, fromFreq: 220,
      vel: 1, p: { edited: "piano" } });
    voice.port.postMessage({ type: "bend", id: "k", freq: 880, time: 0.02 });
    voice.port.postMessage({ type: "mod", param: "cutoff", value: 900 });
    voice.port.postMessage({ type: "noteOff", id: "k" });
    out.push(posted.map(([i, m]) => [i, m.type, m.freq ?? null, m.fromFreq ?? null,
      m.p ? (m.p.edited === "piano" ? "piano" : m.p.slot) : null]));
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


@node
def test_livefx_fans_a_key_out_at_each_lanes_octave(tmp_path):
    """livefx.js (the shipped file, under node with Web Audio shims): a
    noteOn/bend reaches every lane at 2**octave its frequency, with that
    lane's own patch (the edited slot keeps piano.js's live one); "mod"
    reaches only the edited slot; with the edited slot not heard, only the
    other lanes play and "mod" reaches none."""
    base = {"fx": [], "master": [], "gain": 1, "tables": "/t.bin"}
    stacks = [
        {**base, "heard": True, "slot": 1, "octave": 0,
         "layers": [{"slot": 2, "octave": -1, "gain": 0.5, "fx": []},
                    {"slot": 3, "octave": 1, "gain": 1, "fx": []}]},
        {**base, "heard": False, "slot": 1, "octave": 0,
         "layers": [{"slot": 2, "octave": -1, "gain": 0.5, "fx": []}]},
        {**base, "heard": True, "slot": 2, "octave": -1, "layers": []},
    ]  # fmt: skip
    h = tmp_path / "fan.cjs"
    h.write_text(FANOUT, encoding="utf-8")
    got = json.loads(
        subprocess.run(
            ["node", str(h), str(LIVEFX), json.dumps(stacks)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert got[0] == [
        [0, "noteOn", 440, 220, "piano"],
        [1, "noteOn", 220, 110, 2],
        [2, "noteOn", 880, 440, 3],
        [0, "bend", 880, None, None],
        [1, "bend", 440, None, None],
        [2, "bend", 1760, None, None],
        [0, "mod", None, None, None],
        [0, "noteOff", None, None, None],
        [1, "noteOff", None, None, None],
        [2, "noteOff", None, None, None],
    ]
    assert got[1] == [  # node 0 now carries slot 2's lane; node 2 was dropped
        [0, "noteOn", 220, 110, 2],
        [0, "bend", 440, None, None],
        [0, "noteOff", None, None, None],
    ]
    # the edited slot following at -1: the key an octave down, "mod" reaches it
    assert got[2] == [
        [0, "noteOn", 220, 110, "piano"],
        [0, "bend", 440, None, None],
        [0, "mod", None, None, None],
        [0, "noteOff", None, None, None],
    ]


OFFLINE_HARNESS = FANOUT.replace(
    "Object.assign(S, { getCtx: () => ctx, keyBus: () => bus });",
    "Object.assign(S, { getCtx: () => ctx, keyBus: () => bus, laneOn() {},"
    " laneOff() {}, laneIds: () => [], holdOf: () => true, hasLane: () => false });",
).replace(
    "const bus = gainNode();",
    """const bus = gainNode();
// the browser's own check: a biquad of an unknown type or a non-finite Q throws
globalThis.BiquadFilterNode = class {
  constructor(_c, o) {
    const ok = ["lowpass", "highpass"];
    if (!ok.includes(o.type)) throw new TypeError("bad type " + o.type);
    if (!Number.isFinite(o.Q)) throw new TypeError("Q is non-finite");
    this.frequency = { setTargetAtTime() {} }; this.Q = { setTargetAtTime() {} };
  }
  connect(x) { return x; }
  disconnect() {}
};
globalThis.ConvolverNode = class { connect(x) { return x; } disconnect() {} };""",
)


@node
def test_an_offline_unit_never_silences_the_keyboard(tmp_path):
    """A layered Breed sound put `compress` on a layer: a pre-rendered lane
    that can't stand in live still reached livefx.js, whose chain builder
    took every unknown kind for a biquad — `new BiquadFilterNode({type:
    "compress", Q: NaN})` threw inside setStack and the keys went silent.
    Offline units pass through, a lane's failure is its own, and the edited
    voice still gets its notes."""
    base = {
        "fx": [{"kind": "highpass", "cutoff": 130.0, "q": 0.63}],
        "master": [{"kind": "compress"}],
        "gain": 1,
        "tables": "/t.bin",
        "heard": True,
        "slot": 1,
        "octave": 0,
    }
    stacks = [{**base, "layers": [
        {"slot": 2, "octave": -1, "gain": 0.5, "mode": "buffer", "live_ok": False,
         "fx": [{"kind": "compress"}]},
        {"slot": 3, "octave": 0, "gain": 1, "mode": "live", "live_ok": True,
         "fx": [{"kind": "shimmer"}, {"kind": "highpass", "cutoff": 200.0, "q": 0.97}]},
    ]}]  # fmt: skip
    h = tmp_path / "offline.cjs"
    h.write_text(OFFLINE_HARNESS, encoding="utf-8")
    run = subprocess.run(
        ["node", str(h), str(LIVEFX), json.dumps(stacks)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout)
    # the edited voice (node 0) still receives the key
    assert any(i == 0 and t == "noteOn" for i, t, *_ in got[0])
