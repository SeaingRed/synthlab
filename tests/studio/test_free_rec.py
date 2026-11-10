"""Free record: ⏺ Rec's free mode, the shaper, and the note
grid's timing inside a step (`Arrangement.note_nudge`).

What is held here:
- the note grid plays a step's `note_nudge` (a share of a step) in the
  ONE note schedule (`arrange._lane_timeline`): the render places the note
  there — its onset moves in the audio —, its end stays, a note nudged
  before the loop's first beat wraps to its end, an arp ignores it, a
  layer plays its own; the MIDI file writes it; and an arrangement without
  one renders, keys and writes down exactly as before (absent when unset);
- `loops.place_free` places a take as shaped keeping its timing (the rest
  of each start a step's nudge; 100% on the step; swing on the odd ones),
  and the shaper's preview (shaper.js `shapedStart`) is the same function;
- the routes: a free take is kept with its arp flags and opens the shaper
  (no loop), the shaper's body, ▶ renders the shaped loop, Make loop keeps
  a free loop (into a slot when one is named) whose Q keeps its timing,
  and the Loops list shows ✂ Shape on the take and then on its loop;
- the grid keeps it with its note: a removed note drops its nudge, a move
  carries it, the roll draws a nudged note off its step line.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import wave
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import midifile as mf
from synthlab.sequencer import midi as number
from synthlab.studio import arrange, keytakes, looplib, loops, schema
from synthlab.studio import patternops as po
from synthlab.studio.arrange import NOTE_GATE, build_song, note_schedule
from synthlab.studio.render import RenderInputs, _stem_jobs
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.studio.state.model import seq_changes, seq_of
from synthlab.synth import Patch

P = Patch(name="short", release=0.05)
TICK = 1 / mf.PPQ

# one bar of 8 steps (a step is half a beat) at 120 bpm: A1 on step 0, C2
# on step 4 nudged a quarter of a step late, E2 on step 6 a fifth early
NUDGED = Arrangement(
    bpm=120.0,
    bars=1,
    steps_per_bar=8,
    cells=[["A1", 0], ["C2", 4], ["E2", 6]],
    note_nudge={"4": 0.25, "6": -0.2},
)


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _st(client):
    return client.app.state.studio


# --- the schedule ----------------------------------------------------------------


def test_a_steps_nudge_moves_its_notes_start_and_keeps_their_end():
    (lane,) = note_schedule(NUDGED)
    got = {pitch: (round(s, 6), round(d, 6)) for s, d, pitch, _v, _sl in lane}
    assert got == {
        "A1": (0.0, 0.5),
        "C2": (2.125, 0.375),  # step 4 = beat 2, a quarter of a half beat late
        "E2": (2.9, 0.6),  # step 6 = beat 3, a fifth early: longer, same end
    }


def test_a_nudge_before_the_first_beat_wraps_to_the_loops_end():
    arr = replace(NUDGED, note_nudge={"0": -0.5})
    (lane,) = note_schedule(arr)
    a1 = next(e for e in lane if e[2] == "A1")
    assert a1[0] == pytest.approx(4.0 - 0.25)  # the bar's last quarter of a step


def test_a_chanced_offset_note_rolls_its_chance_then_moves():
    """Note chance and the timing compose: `_chanced` drops a resting
    note in front of the schedule, and a note that plays still lands where
    its step's nudge says — on the step's own roll (the same hash with or
    without the nudge)."""
    rests, plays = None, None
    for step in range(8):  # a step whose roll rests at 0.5, one that plays
        r = arrange.note_roll((number("C2"),), step)
        if r >= 0.5 and rests is None:
            rests = step
        if r < 0.5 and plays is None:
            plays = step
    assert rests is not None and plays is not None
    arr = Arrangement(
        bpm=120.0,
        bars=1,
        steps_per_bar=8,
        cells=[["C2", rests], ["C2", plays]],
        cuts=[["C2", max(rests, plays)]],  # two notes, side by side or not
        note_chance={str(rests): 0.5, str(plays): 0.5},
        note_nudge={str(rests): 0.25, str(plays): 0.25},
    )
    (lane,) = note_schedule(arr)
    assert [round(e[0], 6) for e in lane] == [plays * 0.5 + 0.125]
    unchanced = replace(arr, note_chance={})
    starts = sorted(round(e[0], 6) for e in note_schedule(unchanced)[0])
    assert starts == sorted(s * 0.5 + 0.125 for s in (rests, plays))
    # the MIDI file and the render read the same schedule: one note, moved
    song = build_song(P, arr)
    placed = [
        at
        for n, t in song.tracks.items()
        if n.startswith("synth")
        for at, *_ in t.events
    ]
    assert placed == [song.beats_to_samples(plays * 0.5 + 0.125)]


def test_an_arp_plays_on_the_steps_and_a_layer_plays_its_own_nudge():
    arped = replace(NUDGED, arp_mode="up")
    assert arrange.nudge_of(arped) == {}
    starts = [e[0] for e in note_schedule(arped)[0]]
    assert starts == [k * 0.5 for k in range(8)]
    lay = {"patch": P.to_json(), "cells": [["C3", 2]], "note_nudge": {"2": 0.5}}
    _main, layer = note_schedule(replace(NUDGED, note_nudge={}), [lay])
    assert [round(e[0], 6) for e in layer] == [1.25]


def _onset(audio: np.ndarray, after: int = 0) -> int:
    mono = np.abs(audio if audio.ndim == 1 else audio.mean(axis=1))
    return after + int(np.argmax(mono[after:] > 0.02 * mono.max()))


def test_the_render_places_the_note_where_its_nudge_says():
    """The timing is in the render: one note on step 4, nudged a quarter
    step (1/16 of a beat at 8 steps a bar: 31.25 ms at 120 bpm) late, sounds
    that much later than on the step."""
    plain = Arrangement(bpm=120.0, bars=1, steps_per_bar=8, cells=[["A3", 4]])
    late = replace(plain, note_nudge={"4": 0.25})
    on = build_song(P, plain).render()
    off = build_song(P, late).render()
    sr = 44100
    shift = (_onset(off) - _onset(on)) / sr
    assert shift == pytest.approx(0.25 * 0.5 * 0.5, abs=0.002)  # step 0.5 beat x 0.5 s


def test_the_midi_file_writes_the_nudged_start(client):
    s = _st(client)
    s.patch, s.arrangement, s.perform_parts = P, NUDGED, ["synth"]
    r = client.get("/export/loop.mid")
    assert r.status_code == 200
    notes = {n.pitch: (n.start, n.length) for n in mf.read(r.content).tracks[0].notes}
    assert notes[number("C2")][0] == pytest.approx(2.125, abs=TICK)
    assert notes[number("C2")][1] == pytest.approx(0.375 * NOTE_GATE, abs=TICK)
    assert notes[number("E2")][0] == pytest.approx(2.9, abs=TICK)


# --- absent when unset ------------------------------------------------------------


def test_no_nudge_is_absent_wherever_an_arrangement_is_written_down():
    plain = Arrangement()
    assert "note_nudge" not in schema.arr_dict(plain)
    assert "note_nudge" not in schema.conform({}, schema.nodes().ARRANGEMENT)
    inp = RenderInputs(patch=P, arr=plain, parts=("synth",))
    (main,) = [j for j in _stem_jobs(inp) if j.kind == "main"]
    assert "note_nudge" not in main.payload
    nudged = RenderInputs(patch=P, arr=NUDGED, parts=("synth",))
    (main,) = [j for j in _stem_jobs(nudged) if j.kind == "main"]
    assert main.payload["note_nudge"] == NUDGED.note_nudge
    assert (
        inp.key()
        != RenderInputs(
            patch=P, arr=replace(plain, note_nudge={"0": 0.1}), parts=("synth",)
        ).key()
    )


def test_a_nudge_is_kept_only_on_a_step_a_note_starts_and_never_at_zero():
    raw = schema.arr_dict(NUDGED) | {
        "note_nudge": {"4": 0.25, "5": 0.3, "6": 0.0, "9": 9}
    }
    out = schema.conform(raw, schema.nodes().ARRANGEMENT)
    assert out["note_nudge"] == {"4": 0.25}
    assert schema.clean_nudge({"1": 0.2, "4": 0.1}, [["A1", 4]]) == {"4": 0.1}


def test_a_slots_seq_holds_its_nudge_only_when_set():
    assert "note_nudge" not in seq_of(Arrangement())
    assert seq_of(NUDGED)["note_nudge"] == NUDGED.note_nudge
    assert seq_changes({})["note_nudge"] == {}
    assert seq_changes(seq_of(NUDGED))["note_nudge"] == NUDGED.note_nudge


# --- the grid keeps it with its note -----------------------------------------------


def test_the_roll_moves_a_nudge_with_its_note_and_drops_a_removed_ones():
    lane = {
        k: getattr(NUDGED, k)
        for k in (
            "bars",
            "steps_per_bar",
            "cells",
            "cuts",
            "velocities",
            "slides",
            "note_nudge",
        )
    }
    rows = ["E2", "C2", "A1"]
    moved, _hit = po.move_notes(lane, rows, (4, 4, 1, 1), 1, 0)  # C2: step 4 -> 5
    assert moved["note_nudge"] == {"5": 0.25, "6": -0.2}
    gone, _ = po.delete_notes(lane, rows, (6, 6, 0, 0))  # E2
    assert gone["note_nudge"] == {"4": 0.25}
    shifted, _note = po.notes_tool(
        "shift_right", lane, rows=set(rows), ladder=["A1"], octave=5, choices=(1, 2)
    )
    assert shifted["note_nudge"] == {"5": 0.25, "7": -0.2}


def test_a_grid_post_drops_the_nudge_of_a_note_drawn_off(client):
    s = _st(client)
    s.arrangement = NUDGED
    form = {
        "span": "1",
        "bars": "1",
        "steps": "8",
        "cell.A1.0": "on",
        "cell.C2.4": "on",
    }
    r = client.post("/fragment/arrangement", data=form, headers=_hx("perform"))
    assert r.status_code == 200, r.text
    assert s.arrangement.note_nudge == {"4": 0.25}


def test_the_roll_draws_a_nudged_note_off_its_step_line(client):
    s = _st(client)
    s.arrangement = NUDGED
    html = client.get("/view/perform").text
    assert 'name="cell.C2.4" class="cell nudged"' not in html  # (a beat mark first)
    cell = html.split('name="cell.C2.4"')[1].split(">")[0]
    assert "nudged" in cell and 'style="--nudge: 0.25"' in cell and "late" in cell
    plain = html.split('name="cell.A1.0"')[1].split(">")[0]
    assert "nudged" not in plain and "--nudge" not in plain


# --- the placement ------------------------------------------------------------


def test_a_free_loop_keeps_its_timing_as_each_steps_nudge():
    # 4/4, 16 steps a bar (a step a quarter beat): 0.1 beat late of step 0,
    # 0.55 beat (step 2 + 0.2), and one at 3.95 beats — past the last step
    # of a 1-bar loop: step 0, early
    played = [[0.025, 0.5, 57, 0.8], [0.55, 0.25, 60, 0.6], [3.95, 0.2, 64, 1.0]]
    lane = loops.place_free(played, "4/4", 1, 16)
    assert sorted(map(tuple, lane["cells"]))[:2] == [("A3", 0), ("A3", 1)]
    assert lane["note_nudge"]["2"] == pytest.approx(0.2)
    # the first and the wrapped last share step 0: the mean of +0.1 and -0.2
    assert lane["note_nudge"]["0"] == pytest.approx(-0.05)
    snapped = loops.place_free(played, "4/4", 1, 16, strength=100)
    assert snapped["note_nudge"] == {}
    swung = loops.place_free([[0.25, 0.25, 60, 1.0]], "4/4", 1, 16, 100, 0.3)
    assert swung["note_nudge"] == {"1": pytest.approx(0.3)}


def test_the_shaped_start_pulls_by_its_strength():
    assert loops.shaped_start(2.4, 0) == 2.4
    assert loops.shaped_start(2.4, 50) == pytest.approx(2.2)
    assert loops.shaped_start(2.4, 100) == 2.0
    assert loops.shaped_start(2.9, 100, swing=0.3) == pytest.approx(3.3)  # odd: late


# --- shaper.js's pure half ----------------------------------------------------

SHAPER = """
globalThis.window = { synthlab: {} };
const fs = require("fs");
eval(fs.readFileSync(process.argv[2], "utf8")); // no document: the pure half only
const S = window.synthlab.shaper;
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const O = { bpm: 120, bars: 1, barBeats: 4 };
process.stdout.write(JSON.stringify({
  starts: cases.starts.map(([t, q, sw]) => S.shapedStart(t, q, sw)),
  tempo: cases.tempo.map(([on, span]) => S.guessTempo(on, span, 4)),
  fit: S.shape(cases.notes, { ...O, a: 0.5, b: 2.5, mode: "fit" }),
  crop: S.shape(cases.notes, { ...O, a: 0.5, b: 9, mode: "crop" }),
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs shaper.js")
def test_the_shapers_preview_is_the_placement_and_its_guesses_hold(tmp_path):
    rng = np.random.default_rng(7)
    starts = [
        [float(t), int(q), float(sw)]
        for t, q, sw in zip(
            rng.uniform(0, 63, 60),
            rng.integers(0, 101, 60),
            rng.uniform(0, 0.5, 60),
            strict=True,
        )
    ]

    def phrase(bpm: float, eighths) -> list[float]:
        half = 30.0 / bpm
        return [k * half + float(rng.normal(0, 0.008)) for k in eighths]

    riff = [0, 2, 3, 4, 6, 8, 10, 11, 12, 14, 16, 18, 19, 20, 22, 24, 26, 28, 30]
    tempo = [[phrase(bpm, riff), 32 * 30.0 / bpm] for bpm in (96.0, 124.0, 140.0)]
    notes = [
        {"t": 0.4, "d": 0.2, "m": 60, "v": 0.5},  # before the start handle
        {"t": 0.5, "d": 0.5, "m": 62, "v": 0.8},
        {"t": 1.5, "d": 2.0, "m": 64, "v": 1.0},  # runs past the end handle
        {"t": 2.6, "d": 0.2, "m": 65, "v": 1.0},  # after it
    ]
    cases = {"starts": starts, "tempo": tempo, "notes": notes}
    (tmp_path / "s.cjs").write_text(SHAPER, encoding="utf-8")
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    shaper = Path("synthlab/studio/static/shaper.js").resolve()
    out = subprocess.run(
        ["node", str(tmp_path / "s.cjs"), str(shaper), str(tmp_path / "cases.json")],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    got = json.loads(out.stdout)
    for (t, q, sw), js in zip(starts, got["starts"], strict=True):
        assert js == pytest.approx(loops.shaped_start(t, q, sw), abs=1e-9)
    for bpm, guess in zip((96.0, 124.0, 140.0), got["tempo"], strict=True):
        assert guess == pytest.approx(bpm, abs=1.5), got["tempo"]
    # fit: 2 s between the handles onto a 4-beat bar (2 beats a second)
    assert got["fit"] == [[0, 1, 62, 0.8], [2, 2, 64, 1]]
    # crop at 120: a beat a half second from 0.5 s; past the bar: cut
    assert got["crop"] == [[0, 1, 62, 0.8], [2, 2, 64, 1], [4.2, 0.4, 65, 1]][:2]


# --- the routes ---------------------------------------------------------------


def _wav(seconds: float = 1.0, sr: int = 44100) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x10" * int(seconds * sr) * 2)
    return buf.getvalue()


# played free at 120 bpm (beats): irregular, the third by the keys arp
FREE = [
    [0, 0.4, 57, 0.8],
    [0.93, 0.3, 60, 0.7],
    [1.62, 0.2, 64, 0.9, 1],
    [2.71, 0.6, 62, 1],
]


def _free(client, view="perform", notes=FREE, **over):
    data = {
        "name": "keys-20261108-101010",
        "bpm": "120",
        "bars": "0.9",
        "notes": json.dumps(notes),
    }
    files = {"file": ("take.wav", _wav(), "audio/wav")}
    return client.post(
        "/take/keys/free", data=data | over, files=files, headers=_hx(view)
    )


def _shape(**over) -> dict:
    played = [[0, 0.5, 57, 0.8], [1.1, 0.4, 60, 0.7], [2.3, 0.5, 62, 1.0]]
    return {
        "take": "keys-20261108-101010",
        "played": json.dumps(played),
        "bpm": "110",
        "bars": "1",
        "steps": "16",
        "strength": "50",
        "swing": "0",
    } | over


def test_a_free_take_is_kept_with_its_arp_notes_and_opens_the_shaper(client):
    r = _free(client)
    assert r.status_code == 200, r.text
    name = r.headers["X-Clip-Name"]
    trig = json.loads(r.headers["HX-Trigger"])
    assert trig["free-shape"] == {"take": name}
    meta = keytakes.read_meta(_st(client).data_dir, name)
    assert meta["free"] is True and meta["bpm"] == 120
    doc = keytakes.read_notes(_st(client).data_dir, name)
    assert doc["arp"] == [2] and len(doc["notes"]) == 4
    assert loops.names(_st(client).data_dir) == []  # no loop until Make loop
    rows = looplib.rows(_st(client).data_dir)
    assert [(r["kind"], r["shape"]) for r in rows] == [("take", name)]
    html = client.get("/view/perform").text
    assert f'data-free-take="{name}"' in html


def test_a_free_take_keeps_up_to_sixty_seconds(client):
    """Deliberately, a free take stops at 60 s, not the
    clip cap (30); its clip is kept that long, cut there."""
    from synthlab.studio import limits
    from synthlab.studio.clips import load_clip

    assert limits.MAX_FREE_TAKE_SECONDS == 60.0 and limits.MAX_CLIP_SECONDS == 30.0
    for seconds, kept in ((45.0, 45.0), (62.0, 60.0)):
        files = {"file": ("take.wav", _wav(seconds, sr=8000), "audio/wav")}
        data = {
            "name": "keys-long",
            "bpm": "120",
            "bars": "22",
            "notes": json.dumps(FREE),
        }
        r = client.post(
            "/take/keys/free", data=data, files=files, headers=_hx("perform")
        )
        assert r.status_code == 200, r.text
        audio, sr = load_clip(_st(client).data_dir, r.headers["X-Clip-Name"])
        assert len(audio) / sr == pytest.approx(kept, abs=0.01)
    html = client.get("/view/perform").text
    assert 'data-max-free-seconds="60"' in html and "up to 60 s" in html


def test_a_free_take_that_holds_nothing_is_refused(client):
    assert _free(client, notes=[]).status_code == 422
    assert _free(client, notes=[[0, 1, 60, 2.0]]).status_code == 422
    assert list((_st(client).data_dir / "clips").glob("*.wav")) == []


def test_the_shaper_body_carries_the_take_in_seconds(client):
    name = _free(client).headers["X-Clip-Name"]
    r = client.get(f"/fragment/free/shaper?take={name}", headers=_hx("perform"))
    assert r.status_code == 200
    raw = r.text.split("data-shaper='")[1].split("'")[0]
    data = json.loads(raw.replace("&#34;", '"').replace("&quot;", '"'))
    assert data["notes"][1] == [0.465, 0.15, 60, 0.7, 0]  # 0.93 beats at 120 bpm
    assert data["notes"][2][4] == 1 and data["bpm"] == 120
    assert ' name="' not in r.text  # no named field (htmx.ajax sweeps them)
    assert client.get("/fragment/free/shaper?take=nope").status_code == 422


def test_hear_renders_the_shaped_loop(client):
    _free(client)
    r = client.post("/take/keys/free/hear", data=_shape(), headers=_hx("perform"))
    assert r.status_code == 200, r.text
    url = r.headers["X-Preview-Url"]
    assert url.startswith("/preview/") and client.get(url).status_code == 200
    bad = client.post(
        "/take/keys/free/hear", data=_shape(bars="5"), headers=_hx("perform")
    )
    assert bad.status_code == 422


def test_make_loop_keeps_a_free_loop_and_its_q_keeps_the_timing(client):
    take = _free(client).headers["X-Clip-Name"]
    r = client.post(
        "/take/keys/free/loop", data=_shape(strength="0"), headers=_hx("perform")
    )
    assert r.status_code == 200, r.text
    name = unquote(r.headers["X-Loop-Name"])
    loop = loops.load(_st(client).data_dir, name)
    assert loop["free"] == {"swing": 0.0, "take": take}
    assert loop["take"] is None and loop["key"]["bpm"] == 110 and loop["quantise"] == 0
    # 1.1 beats = step 4 + 0.4; 2.3 = step 9 + 0.2
    assert loop["lane"]["note_nudge"] == {
        "4": pytest.approx(0.4),
        "9": pytest.approx(0.2),
    }
    rows = looplib.rows(_st(client).data_dir)
    assert [(r["kind"], r["shape"]) for r in rows] == [
        ("loop", take)
    ]  # the take is the loop's
    q = client.post(
        "/loop/quantise", data={"name": name, "strength": "50"}, headers=_hx("perform")
    )
    assert q.status_code == 200
    again = loops.load(_st(client).data_dir, name)["lane"]["note_nudge"]
    assert again == {"4": pytest.approx(0.2), "9": pytest.approx(0.1)}
    q = client.post(
        "/loop/quantise", data={"name": name, "strength": "100"}, headers=_hx("perform")
    )
    assert "note_nudge" not in loops._raw(_st(client).data_dir, name)["arr"]


def test_make_loop_lands_in_the_selected_slot_and_edit_puts_its_timing_on_the_grid(
    client,
):
    _free(client)
    s = _st(client)
    timing = {"4": pytest.approx(0.2), "9": pytest.approx(0.1)}
    r = client.post(
        "/take/keys/free/loop",
        data=_shape(lane="1", row="0", how="new"),
        headers=_hx("perform"),
    )
    assert r.status_code == 200, r.text
    name = unquote(r.headers["X-Loop-Name"])
    assert s.slot_at(1, 0) == name
    # the launch lays it on lane 2 (a slot's own sequencing: its seq)
    assert s.layers[1]["seq"]["note_nudge"] == timing

    e = client.post("/loop/edit", data={"name": name}, headers=_hx("perform"))
    assert e.status_code == 200
    assert s.arrangement.note_nudge == timing
    # Ctrl+Z past the edit's opening: the edited lane as it was, no timing;
    # the loop keeps its own
    client.post("/fragment/pattern/undo", headers=_hx("perform"))
    assert s.arrangement.note_nudge == {} and s.loop_edit_of() is None
    assert loops.load(s.data_dir, name)["lane"]["note_nudge"] == timing
    # (Edit loads its sound with its lanes over the launched one: that lane's
    # open loop ends there, never written over — it used to be emptied)
    assert len(loops.load(s.data_dir, name)["lane"]["cells"]) == 6  # 3 notes, 2 steps
