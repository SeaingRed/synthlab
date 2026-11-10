"""MIDI export (`studio/midi_bridge.py`, `GET /export/loop.mid` and
`/export/song.mid`): the file holds the schedule the renderer plays — note
starts and gates, held notes, cuts, swing, velocities, the beat's own
clock — and nothing the grid hides."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from synthlab import midifile as mf
from synthlab.core import SR, n_samples
from synthlab.sequencer import midi as number
from synthlab.sequencer import note_span
from synthlab.studio import arrange, midi_bridge, schema
from synthlab.studio import song_model as sm
from synthlab.studio.arrange import NOTE_GATE, build_song, drum_hits, note_schedule
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch

P = Patch(name="short", release=0.05)
TICK = 1 / mf.PPQ

# One bar of 8 steps (a step is half a beat), swing 0.2 (an odd step starts
# 0.1 beat late): A1 held over steps 0-2; C2 on 4 and, cut, again on 5; and
# an A1 on step 12, which a one-bar grid hides.
LOOP = Arrangement(
    bpm=120.0,
    bars=1,
    steps_per_bar=8,
    swing=0.2,
    cells=[["A1", 0], ["A1", 1], ["A1", 2], ["C2", 4], ["C2", 5], ["A1", 12]],
    cuts=[["C2", 5]],
    velocities={"0": 0.5},
    drums=[["kick", 0], ["snare", 4], ["hat", 3], ["kick", 20]],
    drum_bars=1,
    drum_steps=16,
    drum_swing=0.4,
    drum_vel={"4": 0.5},
)
# (start beat, sounding beats, pitch, velocity): what LOOP's main lane plays
LOOP_NOTES = [
    (0.0, 1.5 * NOTE_GATE, number("A1"), 64),
    (2.0, 0.5 * NOTE_GATE, number("C2"), 114),
    (2.6, 0.4 * NOTE_GATE, number("C2"), 114),
]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _loop(client, arr=LOOP, parts=("synth", "drums")) -> mf.MidiFile:
    s = client.app.state.studio
    s.patch, s.arrangement, s.perform_parts = P, arr, list(parts)
    r = client.get("/export/loop.mid")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/midi"
    assert 'filename="synthlab-loop.mid"' in r.headers["content-disposition"]
    return mf.read(r.content)


def _facts(track: mf.Track) -> list[tuple]:
    return [(n.start, n.length, n.pitch, n.velocity) for n in track.notes]


def _same(got: list[tuple], want: list[tuple]) -> None:
    assert len(got) == len(want), (got, want)
    for g, w in zip(got, want, strict=True):
        assert g[0] == pytest.approx(w[0], abs=TICK), (g, w)
        assert g[1] == pytest.approx(w[1], abs=TICK), (g, w)
        assert g[2:] == w[2:], (g, w)


def test_the_schedule_is_what_build_song_places():
    """`note_schedule` restates the per-row scheduling as a timeline: every
    note it lists is one `build_song` placed, at that sample, in a buffer
    of that length (`note_span`), and there are no others."""
    song = build_song(P, LOOP)
    placed = sorted(
        (at, len(audio))
        for name, track in song.tracks.items()
        if name.startswith("synth")
        for at, audio, _gain in track.events
    )
    (lane,) = note_schedule(LOOP)
    listed = sorted(
        (
            song.beats_to_samples(start),
            n_samples(note_span(dur * song.beat_seconds, P), SR),
        )
        for start, dur, _pitch, _vel, _slide in lane
    )
    assert placed == listed and len(placed) == 3
    hits = sorted(
        (at, round(gain, 6))
        for name, track in song.tracks.items()
        if name.startswith("drum_")
        for at, _audio, gain in track.events
    )
    assert hits == sorted(
        (song.beats_to_samples(beat), round(gain, 6))
        for _voice, voice_hits in drum_hits(LOOP)
        for beat, gain in voice_hits
    )


def test_the_loop_exports_its_held_note_its_cut_and_its_swing(client):
    file = _loop(client)
    assert file.bpm == pytest.approx(120.0, abs=1e-3)
    synth = file.tracks[0]
    assert (synth.name, synth.channel) == ("short", 0)
    _same(_facts(synth), LOOP_NOTES)


def test_a_note_hidden_past_a_shrunk_grid_is_not_exported(client):
    """Step 12 of a one-bar, eight-step grid is kept and hidden; it does
    not play (`_playing_steps`) and is not in the file. The same grid
    grown to two bars shows it, and then it is."""
    assert len(_loop(client).tracks[0].notes) == 3
    grown = _loop(client, replace(LOOP, bars=2), parts=("synth",)).tracks[0]
    assert (6.0, number("A1")) in [(n.start, n.pitch) for n in grown.notes]


def test_the_beat_is_a_channel_ten_track_at_general_midi_keys(client):
    drums = _loop(client).tracks[-1]
    assert (drums.name, drums.channel) == ("drums", mf.DRUM_CHANNEL)
    # a 16-step bar: a step is a quarter beat; step 3 is odd, so swung 0.1
    _same(
        _facts(drums),
        [(0.0, 0.25, 36, 127), (0.85, 0.25, 42, 127), (1.0, 0.25, 38, 64)],
    )  # and the kick on step 20, past the drum grid, is not there


def test_each_clock_tiles_over_the_loops_bars(client):
    """A two-bar note grid over a one-bar beat loops over two bars
    (`loop_bars`): the beat plays twice, the notes once."""
    file = _loop(client, replace(LOOP, bars=2))
    assert [n.start for n in file.tracks[-1].notes if n.pitch == 36] == [0.0, 4.0]
    assert len(file.tracks[0].notes) == 4  # the three, and step 12 now shown


def test_only_the_parts_the_loop_carries_are_exported(client):
    assert [t.channel for t in _loop(client, parts=("drums",)).tracks] == [9]
    assert [t.channel for t in _loop(client, parts=("synth",)).tracks] == [0]


def test_a_loop_with_no_notes_is_a_404(client):
    s = client.app.state.studio
    s.arrangement = replace(LOOP, cells=[], drums=[])
    assert client.get("/export/loop.mid").status_code == 404


def test_layers_are_tracks_of_their_own(client):
    """A following layer plays the main lane's notes at its octave: its
    own track, on the next channel, an octave down."""
    s = client.app.state.studio
    s.patch, s.arrangement, s.perform_parts = P, LOOP, ["synth"]
    s.add_layer(Patch(name="sub"), select=False, octave=-1)
    file = mf.read(client.get("/export/loop.mid").content)
    assert [(t.name, t.channel) for t in file.tracks] == [("short", 0), ("sub", 1)]
    main, sub = file.tracks
    assert [n.pitch - 12 for n in main.notes] == [n.pitch for n in sub.notes]
    assert [n.start for n in main.notes] == [n.start for n in sub.notes]
    # a muted layer is not heard, so it is not written
    s.set_layer_muted(1, True)
    file = mf.read(client.get("/export/loop.mid").content)
    assert [t.name for t in file.tracks] == ["short"]


def test_an_arp_exports_the_notes_it_plays(client):
    arr = replace(LOOP, arp_mode="up", swing=0.0, cells=[["A1", 0], ["C2", 0]])
    notes = _loop(client, arr, parts=("synth",)).tracks[0].notes
    assert [n.start for n in notes] == [i * 0.5 for i in range(8)]
    assert [n.pitch for n in notes] == [number("A1"), number("C2")] * 4


def test_a_drawn_pitch_bend_is_written_as_the_wheel(client):
    s = client.app.state.studio
    s.patch, s.arrangement, s.perform_parts = P, LOOP, ["synth"]
    s.curves = {"pitch_bend": [[0.0, 0.5], [1.0, 1.0]]}  # centre -> full up
    track = mf.read(client.get("/export/loop.mid").content).tracks[0]
    assert track.bends[0] == (0.0, pytest.approx(0.0, abs=1e-3))
    beats = [b for b, _v in track.bends]
    values = [v for _b, v in track.bends]
    assert beats == sorted(beats) and beats[-1] < 4.0
    assert values == sorted(values) and values[-1] > 0.95
    # no curve, no bend events
    s.curves = {}
    assert mf.read(client.get("/export/loop.mid").content).tracks[0].bends == []


def test_humanise_and_probability_export_the_roll_the_loop_plays():
    """The file reads `drum_hits`, the seeded roll `_schedule_drums`
    places, so a probable hit that did not play is not in the file and a
    humanised one sits where it sounded."""
    arr = replace(
        LOOP,
        drums=[["hat", s] for s in range(16)],
        drum_humanize=0.8,
        drum_mix={"hat": {"prob": 0.5}},
    )
    song = build_song(P, replace(arr, cells=[]))
    placed = sorted(at for at, _a, _g in song.tracks["drum_hat"].events)
    notes = midi_bridge._drum_notes(arr, 0.0)
    assert sorted(song.beats_to_samples(n.start) for n in notes) == placed
    assert 0 < len(placed) < 16


# --- the song ---------------------------------------------------------------------


def _notes(cells, bars=1, spb=4) -> dict:
    return {"cells": [list(c) for c in cells], "bars": bars, "steps_per_bar": spb}


def _song() -> dict:
    """Intro: 2 bars of a 1-bar riff (two cycles), played twice. Verse: 2
    bars, a 2-bar line over a 1-bar beat, three times. A third track is
    muted, and an audio track holds nothing MIDI can say."""
    song = sm.new_song(120.0, "A1", "minor")
    lead = sm.add_track(song, "synth", "Lead", patch=P.to_json())
    bass = sm.add_track(song, "synth", "Bass", patch=P.to_json())
    quiet = sm.add_track(song, "synth", "Quiet", patch=P.to_json())
    drums = sm.add_track(song, "drums", "Kit")
    sm.add_track(song, "audio", "Vox")
    intro = sm.add_section(song, "Intro", 2)
    verse = sm.add_section(song, "Verse", 2)
    riff = sm.add_pattern(song, "notes", "r", _notes((("A2", 0), ("E3", 3))))
    line = sm.add_pattern(song, "notes", "l", _notes((("C3", 1), ("G2", 6)), bars=2))
    beat = sm.add_pattern(
        song, "drums", "b", {"drums": [["kick", 0], ["snare", 8]], "bars": 1}
    )
    sm.set_cell(song, intro, lead, riff)
    sm.set_cell(song, verse, lead, riff)
    sm.set_cell(song, verse, bass, line)
    sm.set_cell(song, verse, quiet, riff)
    sm.set_cell(song, verse, drums, beat)
    song["sections"][0]["repeats"] = 2
    song["sections"][1]["repeats"] = 3
    next(t for t in song["tracks"] if t["id"] == quiet)["mute"] = True
    return song


def _use(client, song: dict) -> dict:
    s = client.app.state.studio
    s.song = schema.conform(song, schema.nodes().SONG)
    return s.song


def test_the_song_exports_one_track_per_sounding_track(client):
    _use(client, _song())
    r = client.get("/export/song.mid")
    assert r.status_code == 200
    assert 'filename="synthlab-song.mid"' in r.headers["content-disposition"]
    file = mf.read(r.content)
    assert file.bpm == pytest.approx(120.0, abs=1e-3)
    # the muted track and the audio track are not there
    assert [(t.name, t.channel) for t in file.tracks] == [
        ("Lead", 0),
        ("Bass", 1),
        ("Kit", mf.DRUM_CHANNEL),
    ]


def test_sections_are_laid_end_to_end_with_their_repeats(client):
    """Intro is 2 bars x 2 = beats 0-16; the verse 2 bars x 3 = 16-40.
    The 1-bar riff attacks on every bar of both; the 2-bar line once a
    pass of the verse; the beat on every bar of the verse."""
    song = _use(client, _song())
    lead, bass, kit = mf.read(client.get("/export/song.mid").content).tracks
    a2, e3 = number("A2"), number("E3")
    assert [n.start for n in lead.notes if n.pitch == a2] == [
        4.0 * bar for bar in range(10)
    ]
    assert [n.start for n in lead.notes if n.pitch == e3] == [
        4.0 * bar + 3.0 for bar in range(10)
    ]
    assert [n.start for n in bass.notes if n.pitch == number("C3")] == [17, 25, 33]
    assert [n.start for n in bass.notes if n.pitch == number("G2")] == [22, 30, 38]
    assert [n.start for n in kit.notes if n.pitch == 36] == [
        16.0 + 4 * b for b in range(6)
    ]
    assert [n.start for n in kit.notes if n.pitch == 38] == [
        18.0 + 4 * b for b in range(6)
    ]
    # the file ends where the song does
    beats = sm.song_seconds(song) * song["bpm"] / 60
    assert beats == 40 and max(n.start for t in (lead, bass, kit) for n in t.notes) < 40


def test_the_songs_note_starts_are_the_section_passes_the_player_queues(client):
    """Every pass of `section_timeline` starts a cycle; the file's first
    note of each pass sits at that pass's own start sample."""
    song = _use(client, _song())
    lead = mf.read(client.get("/export/song.mid").content).tracks[0]
    beat_samples = SR * 60.0 / song["bpm"]
    starts, at = [], 0
    for section in song["sections"]:
        timeline = sm.section_timeline(song, section)
        starts += [at + s for s, _n in timeline]
        at += sum(n for _s, n in timeline)
    firsts = {
        round(n.start * beat_samples) for n in lead.notes if n.pitch == number("A2")
    }
    assert set(starts) <= firsts


def test_an_empty_song_is_a_404_and_an_over_long_one_a_toast(client):
    assert client.get("/export/song.mid").status_code == 404
    song = _song()
    song["sections"][0]["bars"], song["sections"][0]["repeats"] = 64, 16
    _use(client, song)
    r = client.get("/export/song.mid")
    assert r.status_code == 422 and "cap" in r.text


def test_the_export_links_are_where_the_exports_are(client):
    perform = client.get("/view/perform").text
    assert 'href="/export/loop.mid"' in perform
    assert client.get("/view/song").text.count('href="/export/') == 0  # no sections
    _use(client, _song())
    page = client.get("/view/song").text
    for href in ("/export/stems.zip", "/export/song.mid"):
        assert f'href="{href}"' in page, href
    # the audio is the export form's: its loudness target and its format
    assert 'action="/export/song.wav"' in page
    for fmt in ("wav", "flac", "mp3"):
        assert f'<option value="{fmt}"' in page, fmt


def test_gate_is_the_schedulers_own():
    """One number: `Song.notes` gates at 0.9 by default and the studio's
    schedulers pass `dur * NOTE_GATE`."""
    import inspect

    from synthlab.sequencer import Song

    assert inspect.signature(Song.notes).parameters["gate"].default == NOTE_GATE
    assert arrange.NOTE_GATE == 0.9
