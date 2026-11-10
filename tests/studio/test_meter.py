"""Time signatures and tempo changes (`studio/meter.py`): a
loop and a song section in 3/4, 6/8, 5/4, 7/8 (and 2/4, 12/8); a section
at its own tempo, and a ramp across one. 4/4 at one tempo is the
arithmetic it always was, float for float — every key and every render of
a song or loop without a meter or a tempo of its own is unchanged (the 18
starters were hashed before and after meters and tempos existed)."""

from __future__ import annotations

import io
import json
import math
import wave
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import midifile as mf
from synthlab.core import SR, n_samples
from synthlab.studio import (
    arrange,
    beatgen,
    meter,
    meterops,
    midi_bridge,
    render,
    schema,
)
from synthlab.studio import song_model as sm
from synthlab.studio.render import RenderInputs, section_audio
from synthlab.studio.server import create_app
from synthlab.studio.service.songpage import ruler, time_true
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch

from .test_song_routes import post, song_dict, use_song

N = schema.nodes()


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        yield c


def _studio(client):
    return client.app.state.studio


# --- the meter itself ---------------------------------------------------------


def test_a_bar_of_each_meter_in_beats_and_sixteenths():
    want = {
        "4/4": (4.0, 16, (4, 8, 16, 32)),
        "2/4": (2.0, 8, (2, 4, 8, 16)),
        "3/4": (3.0, 12, (3, 6, 12, 24)),
        "5/4": (5.0, 20, (5, 10, 20)),
        "6/8": (3.0, 12, (6, 12, 24)),
        "7/8": (3.5, 14, (7, 14, 28)),
        "7/8:3+2+2": (3.5, 14, (7, 14, 28)),
        "12/8": (6.0, 24, (12, 24)),
    }
    assert set(want) == set(meter.METERS)
    for name, (beats, six, steps) in want.items():
        m = meter.parse(name)
        assert (m.bar_beats, m.sixteenths, meter.step_choices(name)) == (
            beats,
            six,
            steps,
        )
    # anything else is 4/4; a 4/4 grid has the four it always had
    assert meter.parse("9/8").name == "4/4" and meter.clean(None) == "4/4"
    assert meter.step_choices("4/4") == (4, 8, 16, 32)


def test_the_accents_are_the_bar_line_the_groups_heads_and_the_beats():
    # 6/8: 12 sixteenths grouped 3 + 3 eighths; 7/8 2+2+3 and 3+2+2
    assert meter.beats("6/8") == [(0, 2), (2, 0), (4, 0), (6, 1), (8, 0), (10, 0)]
    heads = lambda name: [at for at, lv in meter.beats(name) if lv == 1]  # noqa: E731
    assert heads("7/8") == [4.0, 8.0] and heads("7/8:3+2+2") == [6.0, 10.0]
    assert meter.beats("3/4") == [(0, 2), (4, 0), (8, 0)]
    # 4/4 clicks the bar line and three beats, as the metronome always did
    assert meter.beats("4/4") == [(0, 2), (4, 0), (8, 0), (12, 0)]
    assert meter.clicks_attr("3/4") == "0:2 1:0 2:0"
    acc = meter.accents("6/8", 12)
    assert acc[0] == 2 and acc[6] == 1 and acc[2] == 0 and acc[1] == -1
    assert meter.mark(acc, 6) == "beat group" and meter.mark(acc, 7) == ""
    assert meter.grid_accents("4/4", 16) is None


@pytest.mark.parametrize("bpm", [40.0, 97.3, 124.0, 133.33, 200.0])
@pytest.mark.parametrize("bars", [1, 3, 8, 64])
def test_four_four_at_one_tempo_is_the_old_arithmetic_float_for_float(bpm, bars):
    c = meter.Clock(bpm)
    assert c.seconds(bars) == bars * 4.0 * (60.0 / bpm)
    assert c.samples(bars) == n_samples(bars * 4.0 * (60.0 / bpm), SR)
    assert c.bar_sample(bars) == int(round(bars * 4.0 * (60.0 / bpm) * SR))
    assert c.samples(bars) == sm.cycle_samples(bars, bpm)
    assert c.bar_sample(bars) == sm.bar_sample(bars, bpm)


def test_a_ramp_integrates_its_tempo_and_a_window_is_the_same_ramp():
    c = meter.Clock(120.0, 4.0, (140.0, 16.0, 0.0))
    # 16 beats from 120 to 140, linear per beat: 60/k * ln(140/120)
    k = 20.0 / 16.0
    assert c.at(16.0) == pytest.approx(60.0 / k * math.log(140.0 / 120.0))
    assert c.tempo(0.0) == 120.0 and c.tempo(8.0) == 130.0 and c.tempo(99) == 140.0
    for b in (0.0, 3.3, 15.9, 20.0):  # past its end it holds the last tempo
        assert c.beat_at(c.at(b)) == pytest.approx(b)
    w = c.window(2)  # seen from bar 3
    for x in (0.0, 1.5, 7.0):
        assert w.at(x) == pytest.approx(c.at(8.0 + x) - c.at(8.0))
    got = meter.beats_at(c, np.array([c.at(3.0), c.at(18.0)]))
    assert got == pytest.approx([3.0, 18.0])
    assert meter.ramp_of(120.0, None, 4) == [] and meter.ramp_of(120.0, 120.0, 4) == []
    assert meter.ramp_of(120.0, 90.0, 4, "6/8") == [90.0, 12.0, 0.0]


def test_a_grid_is_fitted_bar_by_bar_the_step_length_kept():
    # 16 sixteenths of 4/4 -> 12 in 3/4: each bar keeps its first 3 beats
    assert meter.refit(16, "4/4", "3/4") == (12, 1)
    assert meter.refit_step(13, 16, "4/4", 12, "3/4") is None  # beat 4 of bar 1
    assert meter.refit_step(17, 16, "4/4", 12, "3/4") == 13  # bar 2, step 2
    # a quarter grid has no 7/8 step: eighths, two to each quarter
    assert meter.refit(4, "4/4", "7/8") == (7, 2)
    assert meter.refit(12, "3/4", "4/4") == (16, 1)


# --- the loop -----------------------------------------------------------------


def test_an_unset_meter_and_ramp_are_absent_and_no_key_moves():
    a = Arrangement()
    d = schema.arr_dict(a)
    assert "meter" not in d and "ramp" not in d
    assert "meter" not in N.ARRANGEMENT.default()
    clean = schema.conform({**d, "meter": "4/4", "ramp": []}, N.ARRANGEMENT)
    assert "meter" not in clean and "ramp" not in clean
    inp = RenderInputs(patch=Patch(), arr=a)
    assert inp.key() == RenderInputs(patch=Patch(), arr=replace(a, meter="4/4")).key()
    # another meter keys apart, and is written down
    assert inp.key() != RenderInputs(patch=Patch(), arr=replace(a, meter="3/4")).key()
    assert schema.arr_dict(replace(a, meter="3/4"))["meter"] == "3/4"


def test_a_grid_is_held_to_its_meter_s_step_counts():
    # 12 steps is no 4/4 grid: the field's default, as any stray value was
    four = schema.conform({"steps_per_bar": 12, "drum_steps": 12}, N.ARRANGEMENT)
    assert (four["steps_per_bar"], four["drum_steps"]) == (4, 16)
    three = schema.conform(
        {"meter": "3/4", "steps_per_bar": 12, "drum_steps": 16}, N.ARRANGEMENT
    )
    assert (three["steps_per_bar"], three["drum_steps"]) == (12, 12)


def test_a_three_four_loop_is_three_beats_a_bar():
    a = Arrangement(
        bpm=120.0,
        meter="3/4",
        bars=2,
        steps_per_bar=12,
        drum_bars=1,
        drum_steps=12,
        cells=[["A1", 0], ["A1", 12]],
        drums=[["kick", 0], ["snare", 4], ["snare", 8]],
    )
    assert arrange.loop_seconds(a) == 2 * 3.0 * 0.5
    hits = dict(arrange.drum_hits(a))
    assert [b for b, _g in hits["snare"]] == [1.0, 2.0]  # beats 2 and 3
    lane = arrange.note_schedule(a)[0]
    assert [round(e[0], 6) for e in lane] == [0.0, 3.0]  # bar 2 starts on beat 4
    x = render.render_audio(RenderInputs(patch=Patch(), arr=a))
    assert len(x) == int(round(3.0 * SR))


def test_set_meter_fits_every_grid_and_one_undo_puts_it_back(client):
    s = _studio(client)
    s.arrangement = replace(
        s.arrangement,
        bars=2,
        steps_per_bar=16,
        cells=[["A1", 0], ["A1", 12], ["A1", 16]],
        drum_steps=16,
        drums=[["kick", 0], ["kick", 12], ["hat", 2]],
        velocities={"16": 0.5},
    )
    s.beat_vars = {
        "B": {
            "drums": [["kick", 12], ["snare", 4]],
            "drum_vel": {},
            "drum_bars": 1,
            "drum_steps": 16,
            "drum_hits": {},
            "drum_row_swing": {},
        }
    }
    other = 1 if s.active_layer != 1 else 2
    s.layers[other] = {**s.layers[other], "cells": [["C2", 4], ["C2", 13]]}
    before = s.pattern_snapshot()
    r = client.post("/fragment/meter", data={"meter": "3/4"}, headers=_hx("perform"))
    assert r.status_code == 200
    toast = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert "3/4" in toast and "2 notes and hits" in toast
    a = s.arrangement
    assert (a.meter, a.steps_per_bar, a.drum_steps) == ("3/4", 12, 12)
    assert a.cells == [["A1", 0], ["A1", 12]] and a.velocities == {"12": 0.5}
    assert sorted(a.drums) == [["hat", 2], ["kick", 0]]
    assert s.layers[other]["cells"] == [["C2", 4]]
    assert s.beat_vars["B"]["drums"] == [["snare", 4]]
    assert s.beat_vars["B"]["drum_steps"] == 12
    # one Ctrl+Z: the grids and the meter as they were; the lanes the
    # step does not hold come back into 4/4, and what fell off them is gone
    client.post("/fragment/pattern/undo", headers=_hx("perform"))
    assert s.arrangement.meter == "4/4"
    assert s.pattern_snapshot()["arr"] == before["arr"]
    assert s.layers[other]["cells"] == [["C2", 4]]
    assert s.beat_vars["B"]["drum_steps"] == 16


def test_the_meter_route_refuses_a_meter_it_has_not_and_a_loop_past_its_cap(client):
    s = _studio(client)
    r = client.post("/fragment/meter", data={"meter": "9/8"}, headers=_hx("perform"))
    assert r.status_code == 422 and "not a meter" in r.text
    s.arrangement = replace(s.arrangement, bpm=40.0, bars=4, drum_bars=3)
    r = client.post("/fragment/meter", data={"meter": "5/4"}, headers=_hx("perform"))
    assert s.arrangement.meter == "4/4"
    assert r.status_code == 422 and "cap" in r.text


def test_the_pages_draw_the_meter_picker_and_the_bar_as_it_is(client):
    s = _studio(client)
    client.post("/fragment/meter", data={"meter": "6/8"}, headers=_hx("drums"))
    html = client.get("/view/drums").text
    assert 'name="meter"' in html and 'value="6/8" selected' in html
    assert 'data-clicks="0:2 0.5:0 1:0 1.5:1 2:0 2.5:0"' in html
    # 12 steps a bar; the second group's head is marked
    assert 'name="drum.kick.11"' in html and 'name="drum.kick.12"' not in html
    assert 'class="cell beat group "' in html
    steps = s.arrangement.drum_steps
    assert steps == 12
    perform = client.get("/view/perform").text
    # the note grid's quarters have no 6/8 step: its eighths, six a bar
    assert 'id="meter-pick"' in perform and "<option selected>6</option>" in perform


def test_a_waltz_style_takes_the_loop_to_three_four():
    cells = beatgen.generate("waltz", 1, 1, 12, meter="3/4")
    assert ["kick", 0] in cells and ["snare", 4] in cells and ["snare", 8] in cells
    assert all(s < 12 for _v, s in cells)
    # a 4/4 style on a 4/4 grid draws exactly what it always did
    assert beatgen.generate("techno", 7, 2, 16) == beatgen.generate(
        "techno", 7, 2, 16, meter="4/4"
    )
    # ... and on a 3/4 grid keeps its first three beats
    assert all(
        s < 12 for _v, s in beatgen.generate("four-floor", 1, 1, 12, meter="3/4")
    )
    assert dict(beatgen.style_groups())["Other meters"][:2] == ["waltz", "jazz-waltz"]


def test_randomise_in_a_waltz_puts_the_loop_in_three_four(client):
    s = _studio(client)
    r = client.post(
        "/fragment/drums/randomise",
        data={"style": "waltz", "seed": "3"},
        headers=_hx("drums"),
    )
    assert r.status_code == 200
    assert s.arrangement.meter == "3/4" and s.arrangement.drum_steps == 12


def test_a_generator_on_a_six_eight_loop_falls_on_its_beats():
    lane = {"bars": 1, "steps_per_bar": 16, "cells": [["A1", 0], ["C2", 4], ["E2", 8]]}
    got = meterops.beat_fit(lane, "6/8")
    # 4/4 beats 1 and 2 on 6/8's two dotted quarters; 3 and 4 have none
    assert got["steps_per_bar"] == 12
    assert [c for c in got["cells"] if c[0] != "A1"] == [["C2", 6]]
    assert meterops.beat_fit(lane, "4/4") is lane


# --- the song -------------------------------------------------------------------


def _timed_song() -> dict:
    """song_dict with its Verse in 6/8 at 100 bpm."""
    d = song_dict()
    d["sections"][1] |= {"meter": "6/8", "bpm": 100.0}
    return schema.conform(d, N.SONG)


def test_a_section_s_own_meter_tempo_and_ramp_are_absent_when_unset():
    plain = schema.conform(song_dict(), N.SONG)
    assert all(
        k not in sec for sec in plain["sections"] for k in ("meter", "bpm", "ramp")
    )
    assert "meter" not in plain
    # one equal to the song's is none
    d = song_dict()
    d["sections"][0] |= {"meter": "4/4", "bpm": 120.0, "ramp": 120.0}
    clean = schema.conform(d, N.SONG)
    assert all(k not in clean["sections"][0] for k in ("meter", "bpm", "ramp"))


def test_a_six_eight_section_at_its_own_tempo_times_and_renders_as_it_lasts():
    song = _timed_song()
    verse = song["sections"][1]
    # 2 bars of 6/8 (three beats) at 100 bpm: 3.6 s a pass, twice
    assert sm.section_seconds(song, verse) == pytest.approx(2 * 2 * 3 * 0.6)
    assert sm.section_timeline(song, verse)[0][1] == n_samples(3.6, SR)
    parts = sm.section_arrangement(song, verse)
    assert (parts.arr.meter, parts.arr.bpm) == ("6/8", 100.0)
    # the 4/4 patterns fitted: 12 steps a bar, beat 4 of the Riff gone
    assert parts.arr.steps_per_bar == 12 and parts.arr.drum_steps == 12
    assert len(section_audio(parts).base) == n_samples(3.6, SR)
    # the 4/4 Intro is the section it always was
    plain = schema.conform(song_dict(), N.SONG)
    assert render.section_key(
        sm.section_arrangement(song, song["sections"][0]), None
    ) == (render.section_key(sm.section_arrangement(plain, plain["sections"][0]), None))
    # the pool keeps the patterns as written
    assert "meter" not in song["patterns"]["p1"]


def test_a_ramped_section_is_one_cycle_and_chunks_are_the_whole():
    d = song_dict()
    d["sections"][0] |= {"bars": 4, "ramp": 150.0}
    song = schema.conform(d, N.SONG)
    sec = song["sections"][0]
    assert sm.cycle_bars(song, sec) == 4
    clock = sm.section_clock(song, sec)
    assert clock.ramp == (150.0, 16.0, 0.0)
    assert sm.section_seconds(song, sec) == pytest.approx(clock.at(16.0))
    assert sm.section_seconds(song, sec) < 4 * 2.0  # faster than 120 throughout
    parts = sm.section_arrangement(song, sec)
    whole = section_audio(parts).base
    assert len(whole) == clock.samples(4)
    # a window of the ramp is the same ramp from its own first bar
    w = parts.window(2, 4)
    assert w.arr.ramp == [150.0, 16.0, 8.0]
    assert w.clock.seconds(2) == pytest.approx(clock.seconds(4) - clock.seconds(2))
    # its drums land where the ramp's beats are
    hits = dict(arrange.drum_hits(parts.arr))["kick"]
    song_ = arrange._song(parts.arr)
    assert song_.beats_to_samples(hits[1][0]) == clock.bar_sample(1)


def test_the_plan_says_a_timed_section_s_clock_and_nothing_for_the_rest(client):
    use_song(client, _timed_song())
    plan = client.get("/song/plan.json").json()
    intro, verse = plan["sections"]
    assert not {"bpm", "meter", "clicks", "bar_starts"} & set(intro)
    assert (
        verse["bpm"] == 100.0 and verse["meter"] == "6/8" and verse["bar_beats"] == 3.0
    )
    assert verse["clicks"][3] == [1.5, 1]  # the second group's head
    assert verse["cycle_samples"] == n_samples(3.6, SR)
    # the Intro's one 4 s pass, then the Verse's two
    assert plan["song_samples"] == n_samples(4.0, SR) + 2 * n_samples(3.6, SR)


def test_the_export_lasts_what_the_sections_last(client):
    use_song(client, _timed_song())
    r = client.get("/export/song.wav")
    assert r.status_code == 200
    with wave.open(io.BytesIO(r.content)) as w:
        seconds = w.getnframes() / w.getframerate()
    # Intro 2 bars of 4/4 at 120 (4 s) + Verse 2 x 2 bars of 6/8 at 100 (7.2 s)
    assert seconds == pytest.approx(4.0 + 7.2, abs=0.01)


def test_the_section_time_route_sets_and_clears_and_refuses_nonsense(client):
    r = post(
        client, "/song/section/s2/time", {"meter": "7/8", "bpm": "90", "ramp": "110"}
    )
    assert r.status_code == 200
    sec = client.app.state.studio.song["sections"][1]
    assert (sec["meter"], sec["bpm"], sec["ramp"]) == ("7/8", 90.0, 110.0)
    post(client, "/song/section/s2/time", {"meter": "", "bpm": "", "ramp": "off"})
    sec = client.app.state.studio.song["sections"][1]
    assert not {"meter", "bpm", "ramp"} & set(sec)
    for bad in ({"meter": "9/8"}, {"bpm": "nan"}, {"bpm": "500"}, {"ramp": "x"}):
        r = post(client, "/song/section/s2/time", bad)
        assert r.status_code == 422, bad
        assert not {"meter", "bpm", "ramp"} & set(
            client.app.state.studio.song["sections"][1]
        )


def test_the_song_meter_is_the_sections_without_one(client):
    post(client, "/song/meta", {"meter": "3/4"})
    song = client.app.state.studio.song
    assert song["meter"] == "3/4"
    assert sm.section_meter(song, song["sections"][0]) == "3/4"
    post(client, "/song/meta", {"meter": "4/4"})
    assert "meter" not in client.app.state.studio.song


def test_the_playlist_is_time_true_a_slower_section_wider(client):
    plain = schema.conform(song_dict(), N.SONG)
    assert time_true(plain) is None and "segs" not in ruler(plain)
    song = _timed_song()
    axis = time_true(song)
    # a 6/8 bar at 100 bpm lasts 1.8 s; a 4/4 bar at 120, 2 s
    assert axis["s1"] == {"x0": 0.0, "bs": 1.0, "w": 2.0}
    assert axis["s2"] == {"x0": 2.0, "bs": 0.9, "w": 3.6}
    r = ruler(song)
    assert r["width"] == 5.6 and [g["bs"] for g in r["segs"]] == [1.0, 0.9]
    use_song(client, song)
    html = client.get("/view/song").text
    assert 'data-x0="2.0" data-bs="0.9"' in html and "--bs: 0.9" in html
    assert 'class="sec-time"' in html and "data-timed" in html
    # the jam cannot launch it: its one bar clock is the song's
    scene = html[html.index('id="jam-scene-s2"') :][:400]
    assert 'aria-disabled="true"' in scene


def test_recording_a_take_into_a_timed_section_is_refused_plainly(client):
    use_song(client, _timed_song())
    r = post(
        client,
        "/song/pattern/p1/record",
        {"take": "t1", "events": "[]", "section_id": "s2", "track_id": "t1"},
    )
    assert r.status_code == 422 and "own meter or tempo" in r.text


# --- MIDI -----------------------------------------------------------------------


def test_the_song_s_midi_writes_its_time_signatures_and_tempo_map():
    song = _timed_song()
    file = midi_bridge.song_file(song)
    assert (file.bpm, file.numerator, file.denominator) == (120.0, 4, 4)
    assert file.meters == [(8.0, 6, 8)] and file.tempos == [(8.0, 100.0)]
    back = mf.read(mf.write(file))
    assert back.meters == [(8.0, 6, 8)] and back.tempos == [pytest.approx((8.0, 100.0))]
    # a 4/4 song at one tempo writes neither: the file it always was
    plain = midi_bridge.song_file(schema.conform(song_dict(), N.SONG))
    assert plain.meters == [] and plain.tempos == []


def test_a_ramp_is_a_tempo_a_beat_in_the_midi_file():
    d = song_dict()
    d["sections"][0] |= {"ramp": 140.0}
    song = schema.conform(d, N.SONG)
    tempos = midi_bridge.song_file(song).tempos
    first = [t for b, t in tempos if b < 8.0]
    # a tempo each of its 8 beats, rising, each the tempo half-way through it
    assert len(first) == 8 and first == sorted(first) and first[-1] < 140.0
    # the section after it goes back to the song's tempo
    assert (8.0, 120.0) in tempos
    # the file's beats last what the render's do
    clock = sm.section_clock(song, song["sections"][0])
    assert sum(60.0 / t for t in first) == pytest.approx(clock.at(8.0), abs=0.002)


def test_a_three_four_loop_exports_and_imports_three_four(client):
    s = _studio(client)
    s.arrangement = replace(
        s.arrangement, meter="3/4", steps_per_bar=12, drum_steps=12, cells=[["A1", 0]]
    )
    data = client.get("/export/loop.mid").content
    assert mf.read(data).numerator == 3
    # a 3/4 file into a 4/4 loop takes the loop to 3/4 first
    s.arrangement = Arrangement()
    out = mf.MidiFile(bpm=90.0, numerator=3, denominator=4)
    out.tracks.append(mf.Track(notes=[mf.Note(0.0, 1.0, 45), mf.Note(3.0, 1.0, 45)]))
    r = client.post(
        "/fragment/pattern/import",
        files={"file": ("w.mid", mf.write(out), "audio/midi")},
        headers=_hx("perform"),
    )
    assert r.status_code == 200
    assert s.arrangement.meter == "3/4"
    msg = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert "3/4" in msg
