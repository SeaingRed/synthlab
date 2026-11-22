"""A take recorded with the keys arp on lands on the grid it was played on,
every row of it shows, and "the keys I held" plays back through the grid
arp (the owner, 2026-11-22: "it doesn't seem to ever place into the bar
grid all of the keys pressed to align with the exact way we had recorded").

- **The rate**: a take placed as played picks the rate its note starts sit
  on best (`loops.pick_steps`): a 1/8T arp lands on 12 steps a bar, its
  notes on the lines but for its swing; a riff of sixteenths stays on 16.
- **The rows**: a lane over five octaves shows on one roll (span 4,
  `state.model.note_rows`), never in a window hiding most of it.
- **The keys I held**: the take keeps the keys pressed and its loop plays
  them through the grid arp set as the keys arp was
  (`arppattern.from_keys`, `arrange._held_timeline`). Held here to a model
  of static/arp.js's clock (`_keys_arp`: its 20 ms pump, posting 60 ms
  ahead, `stepAt`, the take's bar lines, the walk — arppat.js's `hits` for
  ▦) — the same pitches at the same times within a render quantum.
"""

from __future__ import annotations

import io
import json
import math
import wave
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from synthlab.core import SR
from synthlab.sequencer import midi, name_of
from synthlab.studio import arppattern, loops, meter, schema
from synthlab.studio.arrange import NOTE_GATE, note_schedule
from synthlab.studio.server import create_app
from synthlab.studio.state.model import (
    SPAN_NOTES,
    Arrangement,
    grid_rows,
    note_rows,
    notes_window,
)

BPM = 120.0
BEAT = 60.0 / BPM
QUANTUM_BEATS = 128 / SR / BEAT  # one render quantum, in beats


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _wav(seconds: float = 0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x10" * int(seconds * 44100) * 2)
    return buf.getvalue()


def _take(client, notes, bars=2, **over):
    data = {"name": "keys-1", "bpm": str(BPM), "bars": str(bars)}
    data |= {"notes": json.dumps(notes)} | over
    files = {"file": ("take.wav", _wav(), "audio/wav")}
    return client.post("/take/keys", data=data, files=files, headers=_hx("perform"))


def _arp_notes(rate: float, swing: float = 0.0, bars: int = 2, late: float = 0.0):
    """A keys arp's notes on the take's bars: a step every `rate` beats from
    each bar line, the odd ones `swing` of a step late, C E G up three
    octaves; `late`: a constant offset (beats)."""
    run = [60 + 12 * o + d for o in range(3) for d in (0, 4, 7)]
    out = []
    for k in range(round(bars * 4 / rate)):
        t = k * rate + (swing * rate if k % 2 else 0.0) + late
        out.append([round(t, 3), round(rate * 0.5, 3), run[k % len(run)], 0.8])
    return out


# --- the rate a take lands on ---------------------------------------------------------


def test_a_take_picks_the_rate_its_notes_sit_on():
    four = "4/4"
    assert loops.pick_order(four) == [16, 12, 24, 32]
    # a 1/8T arp, straight or swung: 12 a bar
    assert loops.pick_steps(_arp_notes(1 / 3), four, BPM) == 12
    assert loops.pick_steps(_arp_notes(1 / 3, swing=0.15), four, BPM) == 12
    # a 1/16T roll: 24; sixteenths, eighths and quarters: the sixteenths
    assert loops.pick_steps(_arp_notes(1 / 6), four, BPM) == 24
    for rate in (0.25, 0.5, 1.0):
        assert loops.pick_steps(_arp_notes(rate), four, BPM) == 16
    # thirty-seconds: 32 (the sixteenths are off by half a step on every other)
    assert loops.pick_steps(_arp_notes(1 / 8), four, BPM) == 32
    # played loosely around sixteenths (+-12 ms): a tie with the finer rates
    # goes to the sixteenths
    jitter = [0.012, -0.008, 0.01, -0.012, 0.004, -0.01]
    loose = [[k * 0.25 + jitter[k % 6] / BEAT, 0.2, 60, 0.8] for k in range(32)]
    assert loops.pick_steps(loose, four, BPM) == 16
    # no notes: the sixteenths; 3/4's 1/8T is 9 a bar; 6/8 has no triplets
    assert loops.pick_steps([], four, BPM) == 16
    trip34 = [[k / 3, 0.15, 60, 0.8] for k in range(18)]
    assert loops.pick_steps(trip34, "3/4", BPM) == 9
    assert loops.pick_order("6/8") == [12, 24]


def test_a_1_8t_arp_take_lands_on_12_steps_with_every_note_on_a_line(client):
    s = client.app.state.studio
    notes = _arp_notes(1 / 3, bars=2)
    r = _take(client, notes)
    assert r.status_code == 200
    assert unquote(r.headers["X-Loop-Rate"]) == "on 1/8T (12 a bar)"
    loop = loops.load(s.data_dir, unquote(r.headers["X-Loop-Name"]))
    lane = loop["lane"]
    assert (lane["bars"], lane["steps_per_bar"]) == (2, 12)
    # every note started on its line: no note's feel moves its start more
    # than the take's own rounding (a thousandth of a beat: 0.003 of a step)
    assert len(lane["note_feel"]) == len(notes)
    assert all(abs(f[0]) <= 0.005 for f in lane["note_feel"].values())
    starts = sorted({int(st) for _p, st in lane["cells"]} - set())
    assert len(notes) == 24 and len({n[2] for n in notes}) == 9
    assert loop["played"] == notes  # never rewritten
    # its rate is the loop's own from then on: Q offers the triplets
    assert (12, "1/8T") in loops.rates("4/4") and starts


def test_the_owners_take_the_arp_counted_from_each_press():
    """The owner's take (2026-11-22, an arp at 1/8T over three octaves with
    nothing playing): the keys arp counted its steps from each press, not
    from the take's bars, so no grid holds its notes; the sixteenths miss
    by up to half a step, and the pick goes to the rate nearest them. A take
    from silence now counts from its own bars (keyrec.js S.keyRecGrid)."""
    owner = [  # its first notes (beats from the take's downbeat, 100 bpm)
        [1.252, 0.333, 98, 1.0],
        [1.635, 0.333, 74, 1.0],
        [1.918, 0.333, 86, 1.0],
        [3.535, 0.333, 98, 1.0],
        [3.918, 0.333, 74, 1.0],
        [4.202, 0.333, 86, 1.0],
    ]
    picked = loops.pick_steps(owner, "4/4", 100.0)
    worst = max(abs(x) for x in loops.offsets_ms(owner, "4/4", picked, 100.0))
    sixteenths = max(abs(x) for x in loops.offsets_ms(owner, "4/4", 16, 100.0))
    assert worst < sixteenths


def test_a_triplet_grid_holds_its_lines_through_the_tools():
    # a start moved between a triplet and a straight grid keeps its time
    lane = loops.place(_arp_notes(1 / 3, bars=1), "4/4", 1, 12, 0)
    assert all(int(st) % 1 == 0 for _p, st in lane["cells"])
    assert meter.common_steps("4/4", [12, 8]) == 24
    # MIDI out: a 1/8T step is 160 ticks at 480 a quarter — exact
    assert (480 * meter.step_length("4/4", 12) / 4) == 160


# --- every row the notes use --------------------------------------------------------


def test_a_lane_over_five_octaves_shows_every_row():
    a = Arrangement(root="A1", scale_name="minor")
    pitches = [57, 60, 69, 72, 74, 76, 77, 81, 84, 86, 88, 89, 98, 100, 101]
    cells = [[name_of(m), i] for i, m in enumerate(pitches)]
    a = Arrangement(root="A1", scale_name="minor", cells=cells)
    rows = note_rows(a)
    # the key's rows from A3 (the root under the lowest) to A7 (over the
    # highest): four octaves of seven tones and the top root
    assert rows[0] == "A7" and rows[-1] == "A3" and len(rows) == 29
    assert {name_of(m) for m in pitches} <= set(rows)
    assert grid_rows(a, SPAN_NOTES) == rows
    # no window of 24 rows shows them all: the roll goes to every row
    span, _oct, shown = notes_window(a, sorted(pitches), 1, 0)
    assert span == SPAN_NOTES and set(shown) >= {name_of(m) for m in pitches}
    # a note out of the key gets its own row, not the chromatic run
    off = Arrangement(root="A1", scale_name="minor", cells=[*cells, ["A#4", 99]])
    assert "A#4" in note_rows(off) and "C#5" not in note_rows(off)
    # notes that fit a window keep it (span 2: the key over two octaves)
    few = [69, 72, 76, 81]
    assert notes_window(a, few, 1, 0)[0] in (1, 2)


def test_the_span_route_takes_every_row_and_the_schema_keeps_it(client):
    s = client.app.state.studio
    r = client.post("/fragment/grid/span", data={"span": "4"}, headers=_hx("perform"))
    assert r.status_code in (200, 204) and s.grid_span == SPAN_NOTES
    assert client.post("/fragment/grid/span", data={"span": "5"}).status_code == 422
    payload = schema.nodes().PAYLOAD.fields
    assert payload["grid_span"].conform(9) == 4 and payload["grid_span"].conform(4) == 4


# --- the keys I held -----------------------------------------------------------------


def _keys_arp(presses, setting, bars, pattern=None, key=None):
    """static/arp.js's clock, modelled: `presses` [start, end, midi, vel]
    in beats from the take's downbeat (its bar lines the arp counts from:
    keyrec.js S.keyRecGrid), `setting` the ⚙'s {mode, rate, oct, gate,
    swing}; a 20 ms pump posting every step due within 60 ms (`LOOK`), the
    first step of a run the first at or after its press less 30 ms. The
    notes it plays: (start, held length, midi, velocity) in beats."""
    look, tick = 0.06 / BEAT, 0.02 / BEAT
    pat = pattern
    rate = (
        arppattern.RATES[pat["rate"]]
        if pat
        else {"1/4": 1, "1/8": 0.5, "1/8T": 1 / 3, "1/16": 0.25, "1/16T": 1 / 6}[
            setting["rate"]
        ]
    )
    bar = 4.0
    swing = setting["swing"]

    def step_at(ref, after):
        k = max(0, math.ceil((after - ref) / rate - 1e-9))
        return k, ref + k * rate, ref + k * rate + (swing * rate if k % 2 else 0)

    def bar_line(t):
        return math.floor(t / bar + 1e-6) * bar

    events = sorted(
        [(p[0], 0, i) for i, p in enumerate(presses)]
        + [(p[0] + p[1], 1, i) for i, p in enumerate(presses)]
    )
    held: list[int] = []  # indexes, in the order struck
    out, run, count, kept = [], None, 0, None
    times = sorted(
        {e[0] for e in events} | {k * tick for k in range(int(bars * 4 / tick) + 8)}
    )
    for now in times:
        for _t, kind, i in [e for e in events if abs(e[0] - now) < 1e-12]:
            if kind == 0:
                held.append(i)
            elif i in held:
                held.remove(i)
            # changed()
            if run and len(held) > count:
                run["struck"] = now
            count = len(held)
            if held and not run:
                ref = bar_line(now)
                k, base, tt = step_at(ref, now - 0.03 / BEAT)
                run = {
                    "n": 0,
                    "ref": ref,
                    "k": k,
                    "base": base,
                    "t": tt,
                    "pat": None,
                    "struck": None,
                }
            if not held:
                run = None
        while run and run["t"] - now < look and run["t"] < bars * 4:
            if not held:
                run = None
                break
            at = max(run["t"], now)
            notes = [presses[i] for i in held]
            vel = notes[-1][3]
            if pat:
                hits = _hits(run, [n[2] for n in notes], pat, key, kept)
                kept = run["pat"]
            else:
                m = arppattern.pick(
                    [n[2] for n in notes], setting["mode"], setting["oct"], run["n"]
                )
                hits = [(0, m, 1.0, setting["gate"])]
            run["n"] += 1
            for off, m, scale, g in hits:
                t0 = at + off * rate
                out.append((t0, g * rate, m, vel * scale))
            after = run["base"] + rate * 0.5
            run["ref"] = bar_line(run["base"] + rate + 1e-6)
            run["k"], run["base"], run["t"] = step_at(run["ref"], after)
    return out


def _hits(run, held, pat, key, kept):
    """arppat.js `hits`, gridded (the take's bars)."""
    first = run["pat"] is None
    if first:
        if pat["retrig"] == "free" and kept is not None:
            run["pat"] = kept
        else:
            run["pat"] = {"run": arppattern.Run(), "from": run["t"]}
    r = run["pat"]
    struck = run["struck"] is not None and run["struck"] > r["from"] + 0.03 / BEAT
    run["struck"] = None
    at_bar = run["k"] == 0
    if (pat["retrig"] == "note" and struck) or (pat["retrig"] == "bar" and at_bar):
        r["run"].restart()
        r["from"] = run["t"]
    elif first and pat["retrig"] == "bar":
        for _ in range(run["k"]):
            arppattern.step(r["run"], held, pat, key)
    return arppattern.step(r["run"], held, pat, key)


def _loop_timeline(client, presses, arp: dict, bars=2):
    s = client.app.state.studio
    r = _take(client, presses, bars, arp=json.dumps(arp))
    assert r.status_code == 200
    loop = loops.load(s.data_dir, unquote(r.headers["X-Loop-Name"]))
    arr = Arrangement(meter=loop["meter"], **loop["lane"], **loop["key"])
    return loop, [
        (at, dur * NOTE_GATE, midi(p), vel)
        for at, dur, p, vel, _sl in note_schedule(arr)[0]
    ]


def _same(model, got):
    assert len(model) == len(got), (len(model), len(got))
    for (t0, g0, m0, v0), (t1, g1, m1, v1) in zip(
        sorted(model), sorted(got), strict=True
    ):
        assert m0 == m1, (t0, m0, m1)
        assert abs(t0 - t1) <= QUANTUM_BEATS, (t0, t1)
        assert abs(g0 - g1) <= QUANTUM_BEATS, (t0, g0, g1)
        assert abs(v0 - v1) <= 1e-3, (t0, v0, v1)


# a chord held from the downbeat, then another struck legato, a gap, a
# third: presses off the arp's lines by a few ms, as hands play. (Each key
# is pressed and let go clear of the 40-60 ms before a step, where the keys
# arp's 20 ms pump decides whether it reads the key: `HELD_READ_S`)
PRESSES = [
    [0.004, 2.5, 60, 0.8],
    [0.012, 2.5, 64, 0.7],
    [0.02, 2.5, 67, 0.75],
    [2.4, 2.1, 65, 0.9],  # struck while C E G still sound (legato)
    [2.41, 2.1, 69, 0.6],
    [4.99, 2.46, 62, 0.85],  # after a gap: a run of its own, a hair early
    [4.995, 2.46, 65, 0.5],
]


@pytest.mark.parametrize(
    "setting",
    [
        {"mode": "up", "rate": "1/8T", "oct": 3, "gate": 0.5, "swing": 0.0},
        {"mode": "updown", "rate": "1/16", "oct": 2, "gate": 0.8, "swing": 0.2},
        {"mode": "played", "rate": "1/8", "oct": 1, "gate": 0.35, "swing": 0.0},
        {"mode": "random", "rate": "1/16T", "oct": 2, "gate": 0.5, "swing": 0.0},
    ],
)
def test_the_keys_i_held_render_what_the_keys_arp_played(client, setting):
    loop, got = _loop_timeline(client, PRESSES, setting)
    a = loop["lane"]
    assert a["arp_mode"] == "pattern" and a["arp_pattern"]["held"] == "plain"
    assert loop["played"] == PRESSES  # the keys, not the arpeggio
    model = _keys_arp(PRESSES, setting, bars=2)
    _same(model, got)


@pytest.mark.parametrize("retrig", ["note", "bar", "free"])
def test_the_keys_i_held_through_a_pattern_render_what_it_played(client, retrig):
    pat = arppattern.default() | {
        "rate": "1/8T",
        "mode": "up",
        "oct": 2,
        "retrig": retrig,
    }
    pat["steps"][2] = dict(pat["steps"][2], on=False)  # a rest
    pat["steps"][5] = dict(
        pat["steps"][5], tr=2, vel=0.5, rep=2
    )  # a ratchet up two tones
    setting = {"mode": "pattern", "rate": "1/8", "oct": 1, "gate": 0.5, "swing": 0.1}
    arp = setting | {"pattern": pat, "grid": 0.25}
    loop, got = _loop_timeline(client, PRESSES, arp)
    held = loop["lane"]["arp_pattern"]
    assert held["held"] == "pattern" and held["swing"] == 0.1
    key = arppattern.key_of("A1", "pentatonic_minor")
    s = client.app.state.studio
    key = arppattern.key_of(s.arrangement.root, s.arrangement.scale_name)
    model = _keys_arp(PRESSES, setting, bars=2, pattern=held, key=key)
    _same(model, got)


def test_a_held_take_s_arp_is_the_loop_s_alone(client):
    s = client.app.state.studio
    setting = {"mode": "up", "rate": "1/8T", "oct": 3, "gate": 0.5, "swing": 0.0}
    _take(client, PRESSES, arp=json.dumps(setting))
    name = loops.names(s.data_dir)[0]
    r = client.post("/loop/edit", data={"name": name}, headers=_hx("perform"))
    assert r.status_code == 200
    assert s.arrangement.arp_mode == "pattern"
    assert s.arrangement.arp_pattern["held"] == "plain"
    # another loop, with no arp of its own, opened next: the held arp goes
    _take(client, [[0, 1, 60, 0.8]], name="keys-2")
    other = [n for n in loops.names(s.data_dir) if n != name][0]
    client.post("/loop/edit", data={"name": other}, headers=_hx("perform"))
    assert s.arrangement.arp_mode == "off" and not s.arrangement.arp_pattern
    # the chord stabs, or an arp field that is no keys arp: the arp's notes
    for bad in ({"mode": "chord", "rate": "1/8"}, {"mode": "up", "rate": "x"}, [1]):
        assert arppattern.from_keys(bad) is None
    assert takes_arp("{" * 5000) is None and takes_arp("not json") is None


def takes_arp(raw):
    from synthlab.studio.routes.takes import _keys_arp

    return _keys_arp(raw)
