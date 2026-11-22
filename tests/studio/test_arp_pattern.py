"""The arp pattern (`arppattern.py`; studio docs/loop-and-perform.md, "The
arp pattern"): one pattern — up to 16 steps of on / velocity / transpose /
gate / chance / repeats, a rate, a direction, octaves and a retrigger —
that the grid arp renders (`Arrangement.arp_mode` "pattern",
`arrange._lane_timeline`) and the keys arp plays (static/arppat.js).

Held here:

- absent means absent: unset, the field is in no session file, loop key,
  stem key or pattern, and every arp renders as it did; set, it is in the
  loop key and the main stem's payload — only while the grid arp plays it;
- the default pattern on the grid IS the "up" arp, note for note;
- each step's values do what they say (a rest, velocity, tones of the key,
  gate, repeats), chance is a hash (the same every render, a share near
  its odds), the rate and the retrigger count from the meter's bar line
  (7/8 here), and a 16-bar grid is walked to its end;
- the keys arp's walk (arppat.js, under node) is arppattern.py's, step for
  step, over the same cases: the agreement the two arps rest on;
- a section rendered in chunks walks as the cycle whole; the MIDI file
  holds the notes the pattern plays; a layer keeps its own pattern and a
  follower plays its main lane's; the Song carries it;
- hostile input is held to the schema (16 steps, clamped values, 12 slots),
  and the editor's routes write the lane's pattern and the slots.

Nobody has listened: whether a pattern grooves is the ear's.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import midifile as mf
from synthlab.sequencer import midi as number
from synthlab.studio import arppattern as ap
from synthlab.studio import arrange, render, schema, song_bridge
from synthlab.studio import song_model as sm
from synthlab.studio.arrange import NOTE_GATE, build_song, note_schedule
from synthlab.studio.render import RenderInputs
from synthlab.studio.schema import arr_dict
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch

P = Patch(name="short", release=0.05)
HX = {"HX-Request": "true"}
# an A-minor triad held across a 2-bar, 16-step grid (and a velocity)
TRIAD = Arrangement(
    bpm=120.0,
    bars=2,
    steps_per_bar=16,
    root="A1",
    scale_name="minor",
    cells=[["A2", 0], ["C3", 2], ["E3", 5]],
    velocities={"3": 0.5},
)


def pattern(**kw) -> dict:
    """The default pattern with `kw` over it; `steps` a list of overrides."""
    pat = ap.default()
    over = kw.pop("steps", None)
    if over is not None:
        pat["steps"] = [{**ap.STEP_DEFAULT, **o} for o in over]
    return {**pat, **kw}


def lane_of(arr: Arrangement) -> list[tuple]:
    (lane,) = note_schedule(arr)
    return sorted(lane)


# --- absent means absent ----------------------------------------------------------


def test_unset_the_field_is_written_nowhere_and_no_key_moves():
    assert "arp_pattern" not in arr_dict(TRIAD)
    conformed = schema.conform(arr_dict(TRIAD), schema.nodes().ARRANGEMENT)
    assert "arp_pattern" not in conformed
    assert schema.nodes().ARRANGEMENT.default().get("arp_pattern") is None
    for mode in ("off", "up"):
        arr = replace(TRIAD, arp_mode=mode)
        (job,) = render._stem_jobs(RenderInputs(patch=P, arr=arr, parts=("synth",)))
        assert "arp_pattern" not in job.payload and "arp_key" not in job.payload
        # a pattern kept for the keys arp alone re-keys nothing the grid plays
        kept = replace(arr, arp_pattern=pattern(rate="1/8"))
        assert (
            RenderInputs(patch=P, arr=kept, parts=("synth",)).key()
            == RenderInputs(patch=P, arr=arr, parts=("synth",)).key()
        )
        (k,) = render._stem_jobs(RenderInputs(patch=P, arr=kept, parts=("synth",)))
        assert k.payload == job.payload


def test_set_and_played_it_is_in_the_loop_key_and_the_stem_key():
    on = replace(TRIAD, arp_mode="pattern")
    a = RenderInputs(patch=P, arr=on, parts=("synth",))
    b = RenderInputs(
        patch=P, arr=replace(on, arp_pattern=pattern(rate="1/8")), parts=("synth",)
    )
    assert a.key() != b.key()
    (job,) = render._stem_jobs(b)
    assert job.payload["arp_pattern"]["rate"] == "1/8"
    # the key a step's transpose counts in is the stem's too
    assert job.payload["arp_key"] == ["A1", "minor"]
    c = RenderInputs(patch=P, arr=replace(b.arr, scale_name="dorian"), parts=("synth",))
    assert render._stem_jobs(c)[0].payload != job.payload
    assert "arp_pattern" in arr_dict(b.arr)


# --- the default is the "up" arp -------------------------------------------------


@pytest.mark.parametrize("bars,spb", [(2, 16), (16, 16), (4, 8)])
def test_the_default_pattern_is_the_up_arp_note_for_note(bars, spb):
    arr = replace(TRIAD, bars=bars, steps_per_bar=spb)
    up = lane_of(replace(arr, arp_mode="up"))
    assert lane_of(replace(arr, arp_mode="pattern")) == up
    assert lane_of(replace(arr, arp_mode="pattern", arp_pattern=ap.default())) == up
    assert len(up) == bars * spb  # a 16-bar grid walked to its end


def test_the_default_pattern_renders_the_up_arps_audio():
    a = build_song(P, replace(TRIAD, arp_mode="up")).render(normalize=0)
    b = build_song(P, replace(TRIAD, arp_mode="pattern")).render(normalize=0)
    assert np.array_equal(a, b)


# --- a step's values --------------------------------------------------------------


def test_a_rest_takes_no_note_and_the_run_goes_on():
    pat = pattern(steps=[{}, {"on": False}, {}, {}])
    lane = lane_of(replace(TRIAD, arp_mode="pattern", arp_pattern=pat, bars=1))
    # steps 0, 2, 3 of each 4 play the run A C E A C E...
    assert [(s, p) for s, _d, p, _v, _sl in lane[:6]] == [
        (0.0, "A2"),
        (0.5, "C3"),
        (0.75, "E3"),
        (1.0, "A2"),
        (1.5, "C3"),
        (1.75, "E3"),
    ]


def test_velocity_transpose_gate_and_repeats():
    pat = pattern(steps=[{"vel": 0.5, "tr": 2, "gate": 0.4, "rep": 2}])
    lane = lane_of(replace(TRIAD, arp_mode="pattern", arp_pattern=pat, bars=1))
    # A2 two tones up A minor is C3; two hits in the step, each 0.4 of half
    s0, s1 = lane[0], lane[1]
    assert (s0[0], s0[2], s1[0], s1[2]) == (0.0, "C3", 0.125, "C3")
    assert s0[1] * NOTE_GATE == pytest.approx(0.4 * 0.125)
    assert s0[3] == pytest.approx(0.9 * 0.5)  # the grid's 0.9 times the step's
    # the grid's own velocity lane still reads at the step the note is on
    third = [e for e in lane if e[0] == 0.75]
    assert third and third[0][3] == pytest.approx(0.5 * 0.5)


def test_transpose_counts_tones_of_the_key():
    key = ap.key_of("A1", "minor")
    assert ap.scale_shift(number("A2"), 2, key) == number("C3")
    assert ap.scale_shift(number("A2"), 7, key) == number("A3")
    assert ap.scale_shift(number("A2"), -1, key) == number("G2")
    # off the scale (A# in A minor) it keeps its distance above A
    assert ap.scale_shift(number("A#2"), 1, key) == number("B2") + 1
    pent = ap.key_of("C2", "pentatonic_major")
    assert ap.scale_shift(number("E3"), 1, pent) == number("G3")


def test_chance_is_a_hash_the_same_every_render_and_near_its_odds():
    pat = pattern(steps=[{"chance": 0.5}], retrig="free")
    arr = replace(TRIAD, arp_mode="pattern", arp_pattern=pat, bars=16)
    a, b = lane_of(arr), lane_of(arr)
    assert a == b
    assert 0.35 < len(a) / (16 * 16) < 0.65
    # the rolls are the pattern's passes: arppattern.roll, on its own stream
    assert ap.roll(0, 3) == ap.roll(0, 3) and ap.roll(0, 3) != ap.roll(0, 4)


def test_rate_and_bar_retrigger_count_from_the_meters_bar_line():
    # 7/8 is 3.5 beats: at 1/4 a bar holds 4 steps (0, 1, 2, 3), the last
    # running past the bar line; the next bar's count starts on it
    arr = Arrangement(
        bpm=120.0,
        meter="7/8",
        bars=2,
        steps_per_bar=7,
        root="A1",
        scale_name="minor",
        cells=[["A2", 0], ["C3", 0], ["E3", 0]],
        arp_mode="pattern",
    )
    free = lane_of(replace(arr, arp_pattern=pattern(rate="1/4", retrig="free")))
    assert [s for s, *_ in free] == [0.0, 1.0, 2.0, 3.0, 3.5, 4.5, 5.5, 6.5]
    # free: the run goes on across the bar (A C E A | C E A C)
    assert [p for _s, _d, p, *_ in free] == [
        "A2",
        "C3",
        "E3",
        "A2",
        "C3",
        "E3",
        "A2",
        "C3",
    ]
    bar = lane_of(replace(arr, arp_pattern=pattern(rate="1/4", retrig="bar")))
    assert [p for _s, _d, p, *_ in bar] == ["A2", "C3", "E3", "A2"] * 2


def test_as_played_is_the_order_the_rows_first_play_and_random_is_seeded():
    arr = replace(TRIAD, cells=[["E3", 0], ["A2", 1], ["C3", 2]], arp_mode="pattern")
    played = lane_of(replace(arr, arp_pattern=pattern(mode="played")))
    assert [p for _s, _d, p, *_ in played[:4]] == ["E3", "A2", "C3", "E3"]
    rnd = lane_of(replace(arr, arp_pattern=pattern(mode="random", oct=2)))
    assert rnd == lane_of(replace(arr, arp_pattern=pattern(mode="random", oct=2)))
    names = [p for _s, _d, p, *_ in rnd]
    for i in range(0, 24, 6):  # each note once a cycle
        assert sorted(names[i : i + 6], key=number) == [
            "A2",
            "C3",
            "E3",
            "A3",
            "C4",
            "E4",
        ]


# --- the keys arp walks the same ---------------------------------------------------

ARP = Path("synthlab/studio/static/arp.js").resolve()
ARPPAT = Path("synthlab/studio/static/arppat.js").resolve()
NODE = """
globalThis.window = { synthlab: {} };
const fs = require("fs");
// eval, deliberately: the shipped classic scripts; with no `document` each
// publishes its pure half and wires nothing
eval(fs.readFileSync(process.argv[2], "utf8"));
eval(fs.readFileSync(process.argv[3], "utf8"));
const P = window.synthlab.arpPattern;
const cases = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const out = cases.map(([held, pat, key, n, restarts]) => {
  const r = P.fresh(), hits = [];
  for (let k = 0; k < n; k++) {
    if (restarts.includes(k)) P.restart(r);
    hits.push(P.step(r, held, pat, key));
  }
  return hits;
});
const rolls = [], shifts = [];
for (let i = 0; i < 16; i++) for (let c = 0; c < 40; c++) rolls.push(P.roll(i, c));
const minor = [9, [0, 2, 3, 5, 7, 8, 10]];
for (let m = 20; m < 100; m += 3)
  for (let tr = -12; tr <= 12; tr += 5) shifts.push(P.shift(m, tr, minor));
process.stdout.write(JSON.stringify({ out, rolls, shifts }));
"""


def _cases() -> list:
    steps = [
        {"on": True, "vel": 0.8, "tr": 0, "gate": 0.5, "chance": 1.0, "rep": 1},
        {"on": False, "vel": 1.0, "tr": 0, "gate": 0.9, "chance": 1.0, "rep": 1},
        {"on": True, "vel": 1.0, "tr": 3, "gate": 0.3, "chance": 0.6, "rep": 3},
        {"on": True, "vel": 0.4, "tr": -2, "gate": 1.0, "chance": 0.35, "rep": 2},
        {"on": True, "vel": 1.0, "tr": 12, "gate": 0.05, "chance": 0.9, "rep": 4},
    ]
    key = [9, [0, 2, 3, 5, 7, 8, 10]]
    out = []
    for mode in ap.MODES:
        for octaves in (1, 3):
            pat = {
                "steps": steps,
                "rate": "1/16",
                "mode": mode,
                "oct": octaves,
                "retrig": "bar",
            }
            out.append([[64, 57, 60, 72], pat, key, 70, [16, 32, 48]])
            out.append([[45], pat, key, 23, []])
    return out


@pytest.mark.skipif(
    shutil.which("node") is None, reason="node runs the island's own code"
)
def test_the_keys_arp_walks_the_pattern_as_the_grid_does(tmp_path):
    cases = _cases()
    (tmp_path / "w.cjs").write_text(NODE, encoding="utf-8")
    (tmp_path / "c.json").write_text(json.dumps(cases), encoding="utf-8")
    got = json.loads(
        subprocess.run(
            [
                "node",
                str(tmp_path / "w.cjs"),
                str(ARP),
                str(ARPPAT),
                str(tmp_path / "c.json"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
    )
    for (held, pat, key, n, restarts), js in zip(cases, got["out"], strict=True):
        run, py = ap.Run(), []
        for k in range(n):
            if k in restarts:
                run.restart()
            py.append(
                [list(h) for h in ap.step(run, held, pat, (key[0], tuple(key[1])))]
            )
        assert py == js, (pat["mode"], pat["oct"], held)
    assert got["rolls"] == [ap.roll(i, c) for i in range(16) for c in range(40)]
    key = (9, (0, 2, 3, 5, 7, 8, 10))
    assert got["shifts"] == [
        ap.scale_shift(m, tr, key)
        for m in range(20, 100, 3)
        for tr in range(-12, 13, 5)
    ]


def test_the_grid_lays_the_walk_on_its_steps():
    """The grid's timeline is `arppattern.step` over its chord, a step a
    `rate`, restarted where `retrig` says: the same walk the keys take."""
    pat = pattern(
        steps=_cases()[0][1]["steps"], rate="1/8", retrig="bar", mode="updown"
    )
    arr = replace(TRIAD, arp_mode="pattern", arp_pattern=pat)
    lane = lane_of(arr)
    run, want = ap.Run(), []
    for bar in range(2):
        run.restart()
        for j in range(8):
            for off, m, scale, gate in ap.step(
                run, [45, 48, 52], pat, ap.key_of("A1", "minor")
            ):
                at = bar * 4.0 + j * 0.5
                vel = TRIAD.velocities.get(str(int(at / 0.25)), 0.9)
                want.append((at + off * 0.5, gate / NOTE_GATE * 0.5, m, vel * scale))
    assert [(s, d, number(p), v) for s, d, p, v, _sl in lane] == sorted(want)


# --- a section, the MIDI file, layers, the song --------------------------------------


def _song(pat: dict | None, mode: str = "pattern") -> dict:
    song = sm.new_song(120.0, "A1", "minor")
    tid = sm.add_track(song, "synth", "Arp", patch=P.to_json())
    data = {"cells": [["A2", 0], ["C3", 4], ["E3", 9]], "bars": 2, "steps_per_bar": 16}
    pid = sm.add_pattern(song, "notes", "Arp", data)
    song["patterns"][pid]["arp_mode"] = mode
    if pat:
        song["patterns"][pid]["arp_pattern"] = pat
    sid = sm.add_section(song, "A", 8)
    sm.set_cell(song, sid, tid, pid)
    return schema.conform(song, schema.nodes().SONG)


@pytest.mark.parametrize("retrig", ["note", "bar", "free"])
def test_a_section_in_chunks_walks_as_the_cycle_whole(retrig):
    pat = pattern(steps=_cases()[0][1]["steps"], rate="1/16", retrig=retrig)
    song = _song(pat)
    parts = sm.section_arrangement(song, song["sections"][0]).flatten()
    cycle = parts.cycle_bars
    assert cycle >= 2
    whole = sorted(note_schedule(parts.arr, [], False, parts.breaks, parts.arp)[0])
    beats = arrange.bar_beats(parts.arr)
    chunked = []
    for b0 in range(0, cycle, 1):
        w = parts.window(b0, b0 + 1)
        for s, d, p, v, sl in note_schedule(w.arr, [], False, w.breaks, w.arp)[0]:
            chunked.append((s + b0 * beats, d, p, v, sl))
    assert len(whole) > 0
    assert [(round(s, 9), d, p, v) for s, d, p, v, _ in sorted(chunked)] == [
        (round(s, 9), d, p, v) for s, d, p, v, _ in whole
    ]


def test_the_song_carries_the_pattern_and_a_sections_key_moves_with_it():
    none = _song(None)
    plain = render.section_key(sm.section_arrangement(none, none["sections"][0]), None)
    pat = pattern(rate="1/8")
    s = _song(pat)
    parts = sm.section_arrangement(s, s["sections"][0])
    assert parts.arr.arp_pattern == pat
    assert render.section_key(parts, None) != plain
    # a section opened as the loop keeps it; a loop's lane as a pattern too
    assert song_bridge.section_loop(s, s["sections"][0])["arr"]["arp_pattern"] == pat
    arr = arr_dict(replace(TRIAD, arp_mode="pattern", arp_pattern=pat))
    assert song_bridge.note_data(arr, arr["cells"])["arp_pattern"] == pat
    assert "arp_pattern" not in song_bridge.note_data(arr_dict(TRIAD), TRIAD.cells)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def test_the_loops_midi_file_holds_the_notes_the_pattern_plays(client):
    s = client.app.state.studio
    pat = pattern(steps=[{"tr": 2}, {"on": False}], rate="1/8")
    s.patch = P
    s.arrangement = replace(TRIAD, arp_mode="pattern", arp_pattern=pat)
    s.perform_parts = ["synth"]
    file = mf.read(client.get("/export/loop.mid").content)
    notes = sorted((n.start, n.pitch) for n in file.tracks[0].notes)
    want = sorted((round(st, 6), number(p)) for st, _d, p, *_ in lane_of(s.arrangement))
    assert [(round(a, 6), b) for a, b in notes] == want
    assert notes[0] == (0.0, number("C3"))  # A2 two tones up


def test_a_layer_plays_its_own_pattern_and_a_follower_its_main_lanes():
    main = replace(TRIAD, arp_mode="pattern", arp_pattern=pattern(rate="1/8"))
    own = {
        "patch": P.to_json(),
        "cells": [["A3", 0]],
        "arp_mode": "pattern",
        "arp_pattern": pattern(rate="1/4"),
    }
    follower = {"patch": P.to_json(), "cells": [], "follow": {"octave": 1}}
    m, a, b = note_schedule(main, [own, follower])
    assert [s for s, *_ in a][:3] == [0.0, 1.0, 2.0]  # its own: a quarter a step
    assert [s for s, *_ in b][:3] == [0.0, 0.5, 1.0]  # the main lane's eighths
    jobs = render._stem_jobs(
        RenderInputs(patch=P, arr=main, layers=(own,), parts=("synth",))
    )
    assert jobs[1].payload["layer"]["arp_pattern"]["rate"] == "1/4"
    assert jobs[1].payload["arp_key"] == ["A1", "minor"]


def test_each_slot_keeps_its_own_pattern(client):
    s = client.app.state.studio
    pat = pattern(rate="1/8")
    s.arrangement = replace(TRIAD, arp_mode="pattern", arp_pattern=pat)
    s.select_layer(1)
    assert s.layers[0]["seq"]["arp_pattern"] == pat
    s.arrangement = replace(s.arrangement, cells=[["A3", 0]], arp_pattern={})
    s.select_layer(0)
    assert s.arrangement.arp_pattern == pat
    assert "arp_pattern" not in s.layers[1]["seq"]  # absent while unset
    layers = schema.conform(s.layers, schema.nodes().PAYLOAD.fields["layers"])
    assert "arp_pattern" not in layers[1]["seq"]


def test_a_working_loop_with_chance_plays_four_passes():
    arr = replace(
        TRIAD, arp_mode="pattern", arp_pattern=pattern(steps=[{"chance": 0.5}])
    )
    inp = RenderInputs(patch=P, arr=arr, parts=("synth",))
    assert sm.loop_passes(inp, 72.0).passes == 4
    assert (
        sm.loop_passes(replace(inp, arr=replace(arr, arp_mode="up")), 72.0).passes == 1
    )


# --- the schema and the routes ----------------------------------------------------


def test_hostile_input_is_held_to_the_schema():
    node = schema.nodes().ARP_PATTERN
    bad = {
        "steps": [{"on": 1, "vel": 9, "tr": 99, "gate": -1, "chance": "x", "rep": 1e9}]
        * 400,
        "rate": "1/3",
        "mode": ["up"],
        "oct": float("inf"),
        "retrig": None,
        "extra": "dropped",
    }
    got = schema.conform(bad, node)
    assert len(got["steps"]) == ap.STEPS_MAX
    assert got["steps"][0] == {
        "on": True,
        "vel": 1.0,
        "tr": 12,
        "gate": 0.05,
        "chance": 1.0,
        "rep": 4,
    }
    assert (got["rate"], got["mode"], got["oct"], got["retrig"]) == (
        "grid",
        "up",
        1,
        "note",
    )
    assert "extra" not in got
    assert schema.conform({"steps": []}, node)["steps"] == ap.default()["steps"]
    assert schema.conform("nonsense", node) == ap.default()
    # the Arrangement's field: {} stays {} (absent), rubbish is none
    arr = schema.conform(
        {**arr_dict(TRIAD), "arp_pattern": 7}, schema.nodes().ARRANGEMENT
    )
    assert "arp_pattern" not in arr
    # 12 slots, a bad one empty; none held: absent from the session
    slots = schema.nodes().PAYLOAD.fields["arp_slots"]
    got = schema.conform([ap.default(), "bad"] + [None] * 40, slots)
    assert len(got) == ap.SLOTS and got[0] == ap.default() and got[1] is None


def test_the_editor_writes_the_lanes_pattern(client):
    s = client.app.state.studio
    page = client.get("/view/perform").text
    assert 'id="arp-pat"' in page and 'popovertarget="arp-pat"' in page
    assert 'value="pattern"' in page  # the grid arp's ▦ pattern mode
    form = {"n": "3", "rate": "1/16", "mode": "down", "oct": "2", "retrig": "bar"}
    form |= {
        "on.0": "on",
        "vel.0": "50",
        "tr.0": "2",
        "gate.0": "40",
        "chance.0": "70",
        "rep.0": "2",
    }
    form |= {
        "vel.1": "100",
        "tr.1": "0",
        "gate.1": "90",
        "chance.1": "100",
        "rep.1": "1",
    }
    r = client.post("/fragment/arp/pattern", data=form, headers=HX)
    assert r.status_code == 200
    pat = s.arrangement.arp_pattern
    assert (pat["rate"], pat["mode"], pat["oct"], pat["retrig"]) == (
        "1/16",
        "down",
        2,
        "bar",
    )
    assert len(pat["steps"]) == 3
    assert pat["steps"][0] == {
        "on": True,
        "vel": 0.5,
        "tr": 2,
        "gate": 0.4,
        "chance": 0.7,
        "rep": 2,
    }
    assert pat["steps"][1]["on"] is False  # unticked: a rest
    assert pat["steps"][2] == ap.STEP_DEFAULT  # new: the default
    # the editor comes back (its region) with the pattern the keys arp reads
    assert 'id="arp-pattern"' in r.text and "&#34;rate&#34;:&#34;1/16&#34;" in r.text
    # out of range is held; reset is none again
    client.post(
        "/fragment/arp/pattern", data={**form, "tr.0": "40", "n": "99"}, headers=HX
    )
    assert s.arrangement.arp_pattern["steps"][0]["tr"] == 12
    assert len(s.arrangement.arp_pattern["steps"]) == 16
    client.post("/fragment/arp/pattern", data={"action": "clear"}, headers=HX)
    assert s.arrangement.arp_pattern == {}
    # the transport's post keeps it (no form carries it)
    s.arrangement = replace(s.arrangement, arp_pattern=pattern(rate="1/8"))
    client.post(
        "/fragment/arrangement",
        data={
            "bars": "1",
            "steps": "16",
            "span": "1",
            "arp_mode": "pattern",
            "cell.A1.0": "on",
        },
        headers=HX,
    )
    assert s.arrangement.arp_mode == "pattern"
    assert s.arrangement.arp_pattern["rate"] == "1/8"


def test_the_twelve_slots_save_recall_and_clear(client):
    s = client.app.state.studio
    s.arrangement = replace(s.arrangement, arp_pattern=pattern(rate="1/8"))
    assert (
        client.post(
            "/fragment/arp/slot", data={"slot": "4", "action": "save"}, headers=HX
        ).status_code
        == 200
    )
    assert s.arp_slots[4]["rate"] == "1/8" and s.arp_slots[0] is None
    s.arrangement = replace(s.arrangement, arp_pattern={})
    client.post(
        "/fragment/arp/slot", data={"slot": "4", "action": "recall"}, headers=HX
    )
    assert s.arrangement.arp_pattern["rate"] == "1/8"
    # kept with the session, absent when no slot holds one
    s.save()
    data = json.loads((s.data_dir / "state.json").read_text(encoding="utf-8"))
    assert data["arp_slots"][4]["rate"] == "1/8"
    client.post("/fragment/arp/slot", data={"slot": "4", "action": "clear"}, headers=HX)
    assert s.arp_slots is None
    s.save()
    data = json.loads((s.data_dir / "state.json").read_text(encoding="utf-8"))
    assert "arp_slots" not in data
    # an empty slot recalls nothing; a slot past 12 is no slot
    assert (
        client.post(
            "/fragment/arp/slot", data={"slot": "4", "action": "recall"}, headers=HX
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/fragment/arp/slot", data={"slot": "12", "action": "save"}, headers=HX
        ).status_code
        == 422
    )
