"""Strum: a chord's notes start a few ms
apart, low to high — `Arrangement.strum` on the loop, a notes pattern's
`strum` in a song, read by ONE schedule (`arrange._lane_timeline`), so the
loop's render, a section's render and the MIDI file agree.

Held here:

- absent means absent: at 0 the field is in no session file, no loop key,
  no stem key and no pattern, and the audio is the old audio to the sample
  (the pinned key literals of `test_render.py` / `test_song_routes.py` are
  the other half of that — they did not move);
- 20 ms is each chord note 20 ms after the one below it, in samples;
  negative runs high to low; a note alone on its step is not moved; every
  note of a chord still ends where it did;
- a layer strums by its own value, a follower by the main lane's, an arp
  not at all;
- a section rendered in chunks is the section rendered whole (< 1e-9), a
  section's key moves with its pattern's strum, and the song's MIDI file
  carries the offsets;
- the two forms that hold the knob write it, and a form without the field
  leaves it alone; a layer switch keeps each slot's own.

Nobody listened: whether 20 ms sounds like a strum is the ear's.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import midifile as mf
from synthlab.core import SR
from synthlab.sequencer import midi as number
from synthlab.studio import arrange, render, schema
from synthlab.studio import song_model as sm
from synthlab.studio.arrange import NOTE_GATE, build_song, note_schedule
from synthlab.studio.render import RenderInputs, section_audio, section_windows
from synthlab.studio.schema import arr_dict
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch

P = Patch(name="short", release=0.05)
HX = {"HX-Request": "true"}
# 120 bpm: a beat is 0.5 s, so 20 ms is 0.04 beat. One bar of 8 steps: an
# A-minor triad on step 0 (held two steps), a lone E3 on step 4, and a
# two-note chord on step 6.
CHORDS = Arrangement(
    bpm=120.0,
    bars=1,
    steps_per_bar=8,
    cells=[
        ["A2", 0],
        ["A2", 1],
        ["C3", 0],
        ["C3", 1],
        ["E3", 0],
        ["E3", 1],
        ["E3", 4],
        ["A2", 6],
        ["E3", 6],
    ],
)
GAP = 0.02 * 120.0 / 60.0  # 20 ms in beats


def _starts(lane) -> dict[tuple[str, float], float]:
    """(pitch, the grid's start beat rounded to its step) -> start beat."""
    return {(p, round(s * 2) / 2): s for s, _d, p, _v, _sl in lane}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


# --- absent means absent ----------------------------------------------------------


def test_at_zero_the_field_is_written_nowhere_and_no_key_moves():
    assert "strum" not in arr_dict(CHORDS)
    assert "strum" in arr_dict(replace(CHORDS, strum=20.0))
    conformed = schema.conform(arr_dict(CHORDS), schema.nodes().ARRANGEMENT)
    assert "strum" not in conformed
    assert schema.nodes().ARRANGEMENT.default().get("strum") is None
    plain = RenderInputs(patch=P, arr=CHORDS, parts=("synth",))
    # the main stem's payload has no such key (the stem key from before)
    (job,) = render._stem_jobs(plain)
    assert "strum" not in job.payload
    strummed = RenderInputs(patch=P, arr=replace(CHORDS, strum=20.0), parts=("synth",))
    assert strummed.key() != plain.key()
    assert render._stem_jobs(strummed)[0].payload["strum"] == 20.0
    # under an arp there is nothing to strum: the stem is the arp's own
    arp = replace(CHORDS, arp_mode="up")
    (a,) = render._stem_jobs(RenderInputs(patch=P, arr=arp, parts=("synth",)))
    (b,) = render._stem_jobs(
        RenderInputs(patch=P, arr=replace(arp, strum=20.0), parts=("synth",))
    )
    assert a.payload == b.payload


def test_at_zero_the_schedule_and_the_audio_are_the_old_ones():
    assert not arrange._uses_timeline({"A2": [0]}, CHORDS, ())  # the per-row path
    (lane,) = note_schedule(CHORDS)
    assert sorted({s for s, *_ in lane}) == [0.0, 2.0, 3.0]
    a = build_song(P, CHORDS).render(normalize=0)
    b = build_song(P, replace(CHORDS, strum=0.0)).render(normalize=0)
    assert np.array_equal(a, b)


# --- the offsets ------------------------------------------------------------------


def test_twenty_ms_puts_each_chord_note_twenty_ms_after_the_one_below():
    (lane,) = note_schedule(replace(CHORDS, strum=20.0))
    at = _starts(lane)
    assert at[("A2", 0.0)] == 0.0
    assert at[("C3", 0.0)] == pytest.approx(GAP)
    assert at[("E3", 0.0)] == pytest.approx(2 * GAP)
    assert at[("E3", 2.0)] == 2.0  # alone on its step: not moved
    assert at[("A2", 3.0)] == 3.0 and at[("E3", 3.0)] == pytest.approx(3.0 + GAP)
    # every note of a chord ends where it did
    ends = {(p, round(s * 2) / 2): s + d for s, d, p, _v, _sl in lane}
    assert ends[("A2", 0.0)] == ends[("C3", 0.0)] == ends[("E3", 0.0)] == 1.0


def test_a_negative_strum_runs_high_to_low():
    (lane,) = note_schedule(replace(CHORDS, strum=-20.0))
    at = _starts(lane)
    assert at[("E3", 0.0)] == 0.0
    assert at[("C3", 0.0)] == pytest.approx(GAP)
    assert at[("A2", 0.0)] == pytest.approx(2 * GAP)


def test_the_render_places_the_notes_where_the_schedule_says():
    arr = replace(CHORDS, strum=20.0)
    song = build_song(P, arr)
    placed = sorted(at for t in song.tracks.values() for at, _a, _g in t.events)
    (lane,) = note_schedule(arr)
    assert placed == sorted(song.beats_to_samples(s) for s, *_ in lane)
    # in samples: 20 ms is 882 at 44.1 kHz
    assert placed[:3] == [0, round(0.02 * SR), round(0.04 * SR)]
    # and it is heard: the second note is not there yet 10 ms in
    dry = build_song(P, CHORDS).render(normalize=0)
    wet = song.render(normalize=0)
    n = round(0.01 * SR)
    assert np.sqrt(np.mean(wet[:n] ** 2)) < 0.8 * np.sqrt(np.mean(dry[:n] ** 2))


def test_a_long_strum_never_empties_a_short_note():
    arr = replace(CHORDS, strum=60.0, bpm=180.0, steps_per_bar=8)
    (lane,) = note_schedule(arr)
    assert all(d >= (4.0 / 8) / 8 for _s, d, *_ in lane)


def test_swing_and_strum_add():
    arr = replace(CHORDS, strum=20.0, swing=0.2, cells=[["A2", 1], ["E3", 1]])
    (lane,) = note_schedule(arr)
    base = 0.5 + 0.2 * 0.5
    assert sorted(s for s, *_ in lane) == pytest.approx([base, base + GAP])


def test_an_arp_is_not_strummed():
    arp = replace(CHORDS, arp_mode="up")
    assert note_schedule(replace(arp, strum=20.0)) == note_schedule(arp)


def test_a_slide_still_glides_from_the_note_the_grid_puts_before_it():
    """A strum moves when a chord's notes start, not which note a slide
    reads as its predecessor (`_strummed` keeps the grid's order)."""
    arr = replace(CHORDS, slides=[4])
    plain = arrange._lane_timeline(arrange._playing_steps(arr), arr, 0.5)
    strummed_arr = replace(arr, strum=20.0)
    moved = arrange._lane_timeline(
        arrange._playing_steps(strummed_arr), strummed_arr, 0.5
    )
    assert [e[2] for e in moved] == [e[2] for e in sorted(plain)]


# --- layers -----------------------------------------------------------------------


def test_a_layer_strums_by_its_own_value_and_a_follower_by_the_main_lanes():
    main = replace(CHORDS, strum=20.0)
    own = {"patch": P.to_json(), "cells": [["A3", 0], ["C4", 0]], "strum": 40.0}
    plain = {"patch": P.to_json(), "cells": [["A3", 0], ["C4", 0]]}
    follower = {"patch": P.to_json(), "cells": [], "follow": {"octave": 1}}
    _main, a, b, c = note_schedule(main, [own, plain, follower])
    assert sorted(s for s, *_ in a) == pytest.approx([0.0, 2 * GAP])
    assert sorted(s for s, *_ in b) == [0.0, 0.0]  # never the main lane's
    assert _starts(c)[("C4", 0.0)] == pytest.approx(GAP)  # the main lane's notes
    # and the stem key of the layer that strums carries it
    jobs = render._stem_jobs(
        RenderInputs(patch=P, arr=main, layers=(own, plain), parts=("synth",))
    )
    assert jobs[1].payload["layer"]["strum"] == 40.0
    assert "strum" not in jobs[2].payload["layer"]


def test_each_slot_keeps_its_own_strum(client):
    s = client.app.state.studio
    s.arrangement = replace(CHORDS, strum=30.0)
    s.select_layer(1)
    assert s.layers[0]["seq"]["strum"] == 30.0
    # (a slot never stashed starts from the live feel, as it does the arp)
    assert s.arrangement.strum == 30.0
    s.arrangement = replace(s.arrangement, cells=[["A3", 0]], strum=0.0)
    s.select_layer(0)
    assert s.arrangement.strum == 30.0
    assert "strum" not in s.layers[1]["seq"]  # absent while unset
    s.select_layer(1)
    assert s.arrangement.strum == 0.0
    s.select_layer(0)
    # and the slot's seq survives the schema as it was written
    layers = schema.conform(s.layers, schema.nodes().PAYLOAD.fields["layers"])
    assert "strum" not in layers[1]["seq"]


# --- the forms --------------------------------------------------------------------


def test_the_loops_grid_form_writes_the_strum_and_other_forms_leave_it(client):
    s = client.app.state.studio
    page = client.get("/view/perform").text
    assert 'name="strum"' in page and 'id="t-strum"' in page
    r = client.post(
        "/fragment/arrangement",
        data={"bars": "1", "steps": "8", "span": "1", "strum": "25", "cell.A1.0": "on"},
        headers=HX,
    )
    assert r.status_code == 200 and s.arrangement.strum == 25.0
    # the sound bar posts the key and the tempo alone
    client.post("/fragment/arrangement", data={"bpm": "100", "root": "A1"}, headers=HX)
    assert s.arrangement.strum == 25.0 and s.arrangement.bpm == 100.0
    # out of range is held to the range; rubbish is 0
    client.post(
        "/fragment/arrangement",
        data={"bars": "1", "steps": "8", "strum": "900"},
        headers=HX,
    )
    assert s.arrangement.strum == 60.0
    client.post(
        "/fragment/arrangement",
        data={"bars": "1", "steps": "8", "strum": "nan"},
        headers=HX,
    )
    assert s.arrangement.strum == 0.0
    assert "strum" not in arr_dict(s.arrangement)


def _strum_song(strum: float | None) -> dict:
    song = sm.new_song(120.0, "A1", "minor")
    tid = sm.add_track(song, "synth", "Keys", patch=P.to_json())
    cells = [[p, s] for s in range(0, 64, 4) for p in ("A2", "C3", "E3")]
    data = {"cells": cells, "bars": 4, "steps_per_bar": 16}
    pid = sm.add_pattern(song, "notes", "Chords", data)
    if strum:
        song["patterns"][pid]["strum"] = strum
    sid = sm.add_section(song, "A", 4)
    sm.set_cell(song, sid, tid, pid)
    return schema.conform(song, schema.nodes().SONG)


def test_the_drawers_grid_form_writes_the_patterns_strum(client):
    s = client.app.state.studio
    s.song = _strum_song(None)
    (pid,) = s.song["patterns"]
    assert "strum" not in s.song["patterns"][pid]
    form = {"bars": "4", "steps": "16", "span": "1", "strum": "-15"}
    r = client.post(f"/song/pattern/{pid}/grid", data=form, headers=HX)
    assert r.status_code < 300
    assert s.song["patterns"][pid]["strum"] == -15.0
    r = client.post(
        f"/song/pattern/{pid}/grid", data={**form, "strum": "0"}, headers=HX
    )
    assert "strum" not in s.song["patterns"][pid]  # absent again


# --- a song -----------------------------------------------------------------------


def _parts(song):
    return sm.section_arrangement(song, song["sections"][0])


def test_a_sections_key_moves_with_its_patterns_strum_and_not_without():
    plain = render.section_key(_parts(_strum_song(None)), None)
    assert render.section_key(_parts(_strum_song(0.0)), None) == plain
    assert render.section_key(_parts(_strum_song(20.0)), None) != plain
    assert _parts(_strum_song(20.0)).arr.strum == 20.0


def test_a_section_opened_as_a_loop_and_put_back_keeps_its_strum():
    from synthlab.studio import song_bridge

    song = _strum_song(20.0)
    assert song_bridge.section_loop(song, song["sections"][0])["arr"]["strum"] == 20.0
    plain = _strum_song(None)
    assert "strum" not in song_bridge.section_loop(plain, plain["sections"][0])["arr"]
    # a loop's main lane as a pattern: set when set, absent when not
    arr = arr_dict(replace(CHORDS, strum=20.0))
    assert song_bridge.note_data(arr, arr["cells"])["strum"] == 20.0
    assert "strum" not in song_bridge.note_data(arr_dict(CHORDS), CHORDS.cells)
    # a slot's own, as a layer's pattern and as the render reads it
    seq = {"velocities": {}, "slides": [], "cuts": [], "arp_mode": "off", "strum": 40.0}
    assert song_bridge.layer_note_data(arr, [["A3", 0]], seq)["strum"] == 40.0
    assert song_bridge.lane_render(seq, [["A3", 0]], {}, 8)["strum"] == 40.0
    arp = {**seq, "arp_mode": "up", "arp_octaves": 1}
    assert "strum" not in song_bridge.lane_render(arp, [["A3", 0]], {}, 8)


def test_a_strummed_section_in_chunks_is_the_section_whole(monkeypatch):
    parts = _parts(_strum_song(20.0)).flatten()
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 10.0)
    assert len(section_windows(parts)) == 1
    whole = section_audio(parts)
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 4.0)
    assert len(section_windows(parts)) == 2
    chunked = section_audio(parts)
    np.testing.assert_allclose(chunked.base, whole.base, atol=1e-9)
    # and it is not the unstrummed section
    dry = section_audio(_parts(_strum_song(None)).flatten())
    assert np.max(np.abs(dry.base - whole.base)) > 1e-3


def test_the_songs_midi_file_carries_the_strum(client):
    s = client.app.state.studio
    s.song = _strum_song(20.0)
    file = mf.read(client.get("/export/song.mid").content)
    (track,) = file.tracks
    first = sorted((n.start, n.pitch) for n in track.notes if n.start < 0.5)
    tick = 1 / mf.PPQ
    assert [p for _s, p in first] == [number("A2"), number("C3"), number("E3")]
    assert [s for s, _p in first] == pytest.approx([0.0, GAP, 2 * GAP], abs=tick)
    # each ends with the chord: a 16th's gate from the step's start
    top = next(n for n in track.notes if n.start < 0.5 and n.pitch == number("E3"))
    assert top.length == pytest.approx((0.25 - 2 * GAP) * NOTE_GATE, abs=tick)


def test_the_loops_midi_file_carries_the_strum(client):
    s = client.app.state.studio
    s.patch, s.arrangement, s.perform_parts = P, replace(CHORDS, strum=20.0), ["synth"]
    file = mf.read(client.get("/export/loop.mid").content)
    starts = sorted(n.start for n in file.tracks[0].notes)[:3]
    assert starts == pytest.approx([0.0, GAP, 2 * GAP], abs=1 / mf.PPQ)
