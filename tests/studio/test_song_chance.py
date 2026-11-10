"""The Song's patterns and the chance lane:

- **a song pattern carries timing inside a step and the chance lane**
  (`note_nudge`, `note_chance`): a loop sent to the Song as a pattern
  plays there exactly what it plays as a loop — the section's onsets are
  the loop's, a pattern without them gains no key, its content hash and
  every section key from before hold;
- **chance varies every pass**: `arrange.note_roll(..., at)` rolls a
  pattern's step afresh on each of its passes (seeded: the same render
  re-renders identically). A section with chance is one cycle and always
  tiled, and each of its `repeats` is its own pass (`song_model.repass`,
  the plan's `pass_urls`); the song's MIDI file writes what each pass
  plays; the working loop plays `LOOP_CHANCE_PASSES` passes as its cycle
  (`song_model.loop_passes`), said on its URL (`?passes=N`).

Nobody listened: the onsets, the rolls and the keys are held here, not
how a thinned riff sounds.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio import render, schema, song_bridge
from synthlab.studio import song_model as sm
from synthlab.studio.arrange import chance_tile, note_roll, note_schedule
from synthlab.studio.render import RenderInputs, section_key
from synthlab.studio.schema import arr_dict
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch

P = Patch(name="short", release=0.05)
HX = {"HX-Request": "true"}
# one bar of 16 steps at 120 bpm: a free-played riff — step 2 a quarter of
# a step late, step 10 a fifth early — and every step at 50 %
RIFF = Arrangement(
    bpm=120.0,
    bars=1,
    steps_per_bar=16,
    cells=[
        ["A2", 0],
        ["C3", 2],
        ["E3", 4],
        ["A2", 8],
        ["C3", 8],
        ["G3", 10],
        ["A3", 12],
        ["C3", 14],
    ],
    note_nudge={"2": 0.25, "10": -0.2},
)
HALF = {str(s): 0.5 for s in range(16)}


def _song(arr: Arrangement, bars: int = 4, repeats: int = 1):
    song = sm.new_song(arr.bpm, "A1", "minor")
    tid = sm.add_track(song, "synth", "lead", patch=P.to_json())
    a = arr_dict(arr)
    pid = sm.add_pattern(song, "notes", "riff", song_bridge.note_data(a, a["cells"]))
    sid = sm.add_section(song, "A", bars)
    sm.set_cell(song, sid, tid, pid)
    song["sections"][0]["repeats"] = repeats
    return song, pid


def _onsets(parts, k: int | None = None) -> list[list[tuple]]:
    """Each pass of the main lane's pattern (one bar) as (beat, pitch)."""
    w = parts.window(0, parts.cycle_bars)
    lane = note_schedule(w.arr, list(w.layers), w.main_muted, w.breaks, w.arp)[0]
    n = parts.cycle_bars if k is None else k
    return [
        sorted(
            (round(s - 4 * r, 4), p) for s, _d, p, *_ in lane if 4 * r <= s < 4 * r + 4
        )
        for r in range(n)
    ]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


# --- a pattern carries them, absent when unset -------------------------------------


def test_a_pattern_keeps_its_timing_and_chance_and_one_without_gains_no_key():
    song, pid = _song(replace(RIFF, note_chance=HALF))
    p = song["patterns"][pid]
    assert p["note_nudge"] == {"2": 0.25, "10": -0.2}
    assert p["note_chance"] == HALF
    plain, pid2 = _song(replace(RIFF, note_nudge={}))
    q = plain["patterns"][pid2]
    assert "note_nudge" not in q and "note_chance" not in q
    # the schema keeps a step's timing only on a note, and no chance of 1
    raw = {
        **q,
        "note_nudge": {"2": 0.25, "3": 0.1},
        "note_chance": {"0": 1.0, "2": 0.3},
    }
    node = schema.nodes().SONG_PATTERN
    got = schema.conform(raw, node)
    assert got["note_nudge"] == {"2": 0.25} and got["note_chance"] == {"2": 0.3}


def test_a_section_without_either_is_the_section_it_was():
    """No key moves for a song without them: the section is native, its
    cycle the pattern's, and its arrangement carries neither."""
    song, _pid = _song(replace(RIFF, note_nudge={}))
    sec = song["sections"][0]
    assert sm.cycle_bars(song, sec) == 1 and not sm.has_chance(song, sec)
    parts = sm.section_arrangement(song, sec)
    assert not parts.flat
    assert "note_nudge" not in arr_dict(parts.arr)
    assert "note_chance" not in arr_dict(parts.arr)
    assert render.RENDER_REV == 3


def test_a_nudged_pattern_plays_the_loops_onsets_in_every_pass():
    song, _pid = _song(RIFF)
    # a 4-bar beat makes the cycle four of the riff's bars; tiled, each
    # pass of the riff keeps its timing (`_flat`, `window`)
    drums = sm.add_track(song, "drums", "kit")
    four = {"bars": 4, "steps": 16, "drums": [["kick", 0]]}
    sm.set_cell(
        song, song["sections"][0]["id"], drums, sm.add_pattern(song, "drums", "b", four)
    )
    parts = sm.section_arrangement(song, song["sections"][0]).flatten()
    assert parts.cycle_bars == 4 and parts.flat
    loop = sorted((round(s, 4), p) for s, _d, p, *_ in note_schedule(RIFF)[0])
    assert 0.5625 in [s for s, _p in loop]  # step 2 (0.5 beat), a quarter late
    w = parts.window(0, parts.cycle_bars)
    lane = note_schedule(w.arr, list(w.layers), w.main_muted, w.breaks, w.arp)[0]
    for r in range(4):
        got = sorted(
            (round(s - 4 * r, 4), p) for s, _d, p, *_ in lane if 4 * r <= s < 4 * r + 4
        )
        assert got == loop


def test_a_sections_render_places_the_nudged_note_as_the_loop_does():
    """The audio, not only the schedule: one note on step 4 nudged a
    quarter step late sounds as late in the section as in the loop."""
    one = Arrangement(bpm=120.0, bars=1, steps_per_bar=8, cells=[["A3", 4]])
    late = replace(one, note_nudge={"4": 0.25})
    sr = 44100

    def onset(song) -> float:
        parts = sm.section_arrangement(song, song["sections"][0])
        audio = render.section_audio(parts).base
        mono = np.abs(audio.mean(axis=1))
        return int(np.argmax(mono > 0.02 * mono.max())) / sr

    shift = onset(_song(late, bars=1)[0]) - onset(_song(one, bars=1)[0])
    assert shift == pytest.approx(0.25 * 0.5 * 0.5, abs=0.002)


def test_a_layer_pattern_plays_its_own_timing_and_chance():
    seq = song_bridge.pattern_seq(
        {
            "bars": 1,
            "steps_per_bar": 8,
            "velocities": {},
            "slides": [],
            "arp_mode": "off",
            "arp_octaves": 1,
            "note_nudge": {"2": 0.2},
            "note_chance": {"2": 0.5},
        },
        16,
    )
    # an 8-step pattern on a 16-step grid: the step doubles, so its share
    assert seq["note_nudge"] == {"4": pytest.approx(0.4)}
    assert seq["note_chance"] == {"4": 0.5}
    out = song_bridge.lane_render(seq, [["A2", 4]], None, 16)
    assert out["note_nudge"] == {"4": pytest.approx(0.4)}
    assert out["note_chance"] == {"4": 0.5}


def test_the_song_drawers_tools_carry_them_with_the_notes(client):
    s = client.app.state.studio
    song, pid = _song(replace(RIFF, note_chance={"2": 0.4}))
    s.song = song
    r = client.post(f"/song/pattern/{pid}/tool/shift_right", headers=HX)
    assert r.status_code == 200
    p = s.song["patterns"][pid]
    assert p["note_nudge"].get("3") == 0.25 and p["note_chance"].get("3") == 0.4


def test_a_loop_sent_as_a_pattern_carries_them(client):
    from synthlab.studio import loops

    s = client.app.state.studio
    name = loops.save(s.data_dir, "free", replace(RIFF, note_chance={"4": 0.5}), None)
    r = client.post("/loop/song", data={"name": name}, headers=HX)
    assert r.status_code == 200, r.text
    (pat,) = [p for p in s.song["patterns"].values() if p["kind"] == "notes"]
    assert pat["note_nudge"] == {"2": 0.25, "10": -0.2}
    assert pat["note_chance"] == {"4": 0.5}


# --- every pass rolls afresh ---------------------------------------------------


def test_the_first_pass_rolls_the_grids_roll_and_later_ones_afresh():
    pcs = (45,)
    assert note_roll(pcs, 3, 0) == note_roll(pcs, 3)
    rolls = {note_roll(pcs, 3, at) for at in (0, 16, 32, 48)}
    assert len(rolls) == 4
    tiled = chance_tile({"3": 0.5}, 16, 64)
    assert tiled == {
        "3": [0.5, 3, 0],
        "19": [0.5, 3, 16],
        "35": [0.5, 3, 32],
        "51": [0.5, 3, 48],
    }


def test_a_section_with_chance_varies_pass_to_pass_and_rerenders_identically():
    song, _pid = _song(replace(RIFF, note_chance=HALF))
    sec = song["sections"][0]
    assert sm.cycle_bars(song, sec) == 4 and sm.has_chance(song, sec)
    parts = sm.section_arrangement(song, sec)
    assert parts.flat
    passes = _onsets(parts)
    loop = sorted(
        (round(s, 4), p)
        for s, _d, p, *_ in note_schedule(replace(RIFF, note_chance=HALF))[0]
    )
    assert passes[0] == loop  # the first pass is the loop's
    assert len({tuple(x) for x in passes}) > 1
    again = sm.section_arrangement(song, sec)
    assert section_key(parts, None) == section_key(again, None)
    a = render.section_audio(parts).base
    b = render.section_audio(again).base
    assert np.array_equal(a, b)


def test_each_repeat_of_a_section_with_chance_is_its_own_pass(client):
    svc = client.app.state.svc
    song, _pid = _song(replace(RIFF, note_chance=HALF), bars=2, repeats=3)
    entry = svc._song_idents(song, ())
    row = entry["each"][0]
    assert len(row) == 3
    assert len({ident["own"] for ident, _p, _prev in row}) == 3
    plan = svc.song_plan(song)
    assert len(plan["sections"][0]["pass_urls"]) == 3
    # a section without chance keeps its one loop WAV
    plain, _ = _song(RIFF, bars=2, repeats=3)
    assert svc._song_idents(plain, ())["each"] == {}


def test_the_songs_midi_file_rolls_each_pass(client):
    from synthlab.studio import midi_bridge

    song, _pid = _song(replace(RIFF, note_chance=HALF), bars=1, repeats=4)
    notes = midi_bridge.song_file(song).tracks[0].notes
    bars = [
        sorted(
            (round(n.start - 4 * r, 3), n.pitch)
            for n in notes
            if 4 * r <= n.start < 4 * r + 4
        )
        for r in range(4)
    ]
    assert len({tuple(b) for b in bars}) > 1
    again = midi_bridge.song_file(song).tracks[0].notes
    assert [(n.start, n.pitch) for n in again] == [(n.start, n.pitch) for n in notes]


# --- the working loop plays several passes -------------------------------------


def test_a_loop_with_chance_renders_four_passes_and_says_so(client):
    s = client.app.state.studio
    svc = client.app.state.svc
    s.patch, s.perform_parts = P, ["synth"]
    s.arrangement = RIFF
    plain = svc.render_inputs("perform")
    assert plain.passes == 1 and "passes" not in str(plain.key())
    s.arrangement = replace(RIFF, note_chance=HALF)
    inp = svc.render_inputs("perform")
    assert inp.passes == sm.LOOP_CHANCE_PASSES and inp.arr.bars == 4
    assert inp.key() == svc.render_inputs("perform").key()  # seeded: the same key
    one = RenderInputs(patch=P, arr=replace(RIFF, note_chance=HALF), parts=("synth",))
    assert inp.key() != one.key()  # the one-pass key is never served again
    result = svc.render("perform")
    assert result.passes == 4
    assert render.preview_url(result) == f"/preview/{result.key}.wav?passes=4"
    trig = svc.preview_trigger(view="perform")["HX-Trigger"]
    assert "?passes=4" in trig
    audio = result.audio()
    n = len(audio) // 4
    quarters = [audio[i * n : (i + 1) * n] for i in range(4)]
    assert any(not np.allclose(quarters[0], q, atol=1e-3) for q in quarters[1:])


def test_a_loop_past_the_cap_plays_fewer_passes():
    slow = replace(RIFF, bpm=40.0, bars=4, note_chance=HALF)
    inp = RenderInputs(patch=P, arr=slow, parts=("synth",))
    # 4 bars at 40 bpm is 24 s: three passes fit 72 s, four do not
    assert sm.loop_passes(inp, render.LOOP_CAP_SECONDS).passes == 3
    assert sm.loop_passes(replace(inp, arr=RIFF), 72.0) is not None
    assert sm.loop_passes(replace(inp, arr=RIFF), 72.0).passes == 1
