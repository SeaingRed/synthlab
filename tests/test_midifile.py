"""Standard MIDI Files (`synthlab.midifile`): what `write` puts in a file
`read` gets back out, and `read` survives bytes that were never a file."""

from __future__ import annotations

import struct
import time

import pytest

from synthlab import midifile as mf
from synthlab.midifile import MidiError, MidiFile, Note, Track


def _file() -> MidiFile:
    return MidiFile(
        bpm=124.0,
        tracks=[
            Track(
                name="bass",
                channel=0,
                notes=[Note(0.0, 0.9, 45, 114), Note(1.5, 0.225, 57, 64)],
            ),
            Track(
                name="drums",
                channel=mf.DRUM_CHANNEL,
                notes=[Note(0.0, 0.25, 36, 127), Note(1.0, 0.25, 38, 90)],
            ),
        ],
    )


def test_a_written_file_reads_back_as_the_notes_tempo_and_names_it_was_given():
    back = mf.read(mf.write(_file()))
    assert back.bpm == pytest.approx(124.0, abs=1e-3)
    assert (back.numerator, back.denominator, back.ppq) == (4, 4, mf.PPQ)
    assert [(t.name, t.channel) for t in back.tracks] == [("bass", 0), ("drums", 9)]
    for got, want in zip(back.tracks, _file().tracks, strict=True):
        assert [(n.pitch, n.velocity) for n in got.notes] == [
            (n.pitch, n.velocity) for n in want.notes
        ]
        for g, w in zip(got.notes, want.notes, strict=True):
            assert g.start == pytest.approx(w.start, abs=1 / mf.PPQ)
            assert g.length == pytest.approx(w.length, abs=1 / mf.PPQ)


def test_the_file_is_format_one_with_a_tempo_track_first():
    data = mf.write(_file())
    assert data[:4] == b"MThd"
    fmt, count, division = struct.unpack(">HHH", data[8:14])
    assert (fmt, count, division) == (1, 3, mf.PPQ)
    # 124 bpm = 483871 microseconds a quarter note, in the first chunk
    first = data[14 : data.index(b"MTrk", 18)]
    assert bytes([0xFF, 0x51, 0x03]) + (483871).to_bytes(3, "big") in first
    assert bytes([0xFF, 0x58, 0x04, 4, 2, 24, 8]) in first


def test_a_note_still_sounding_when_its_pitch_starts_again_ends_there():
    """A tiled pattern's held note would otherwise write two note-ons of a
    pitch with no note-off between."""
    ticks = mf.note_ticks([Note(0.0, 3.0, 60, 100), Note(2.0, 1.0, 60, 100)])
    assert ticks == [(0, 2 * mf.PPQ, 60, 100), (2 * mf.PPQ, 3 * mf.PPQ, 60, 100)]
    # and the same pitch twice on one tick is one note
    assert len(mf.note_ticks([Note(1.0, 1.0, 60), Note(1.0, 0.5, 60)])) == 1


def test_a_note_off_comes_before_a_note_on_of_the_same_tick():
    track = Track(notes=[Note(0.0, 1.0, 60, 100), Note(1.0, 1.0, 62, 100)])
    back = mf.read(mf.write(MidiFile(tracks=[track]))).tracks[0]
    assert [(n.start, n.length) for n in back.notes] == [(0.0, 1.0), (1.0, 1.0)]


def test_pitch_bend_round_trips_with_its_range():
    track = Track(
        channel=2,
        notes=[Note(0.0, 4.0, 60, 100)],
        bends=[(0.0, 0.0), (1.0, 1.0), (2.0, -1.0), (3.0, 0.5)],
        bend_range=2.5,
    )
    data = mf.write(MidiFile(tracks=[track]))
    # RPN 0 = 2 semitones + 50 cents on channel 3
    for cc in ((101, 0), (100, 0), (6, 2), (38, 50)):
        assert bytes([0xB2, *cc]) in data
    back = mf.read(data).tracks[0]
    assert back.channel == 2
    assert [b for b, _v in back.bends] == [0.0, 1.0, 2.0, 3.0]
    assert [v for _b, v in back.bends] == pytest.approx([0, 1, -1, 0.5], abs=1e-3)


def test_out_of_range_pitches_and_velocities_never_reach_the_file():
    track = Track(notes=[Note(0, 1, 200, 100), Note(0, 1, 60, 900), Note(1, 1, 61, 0)])
    back = mf.read(mf.write(MidiFile(tracks=[track]))).tracks[0]
    assert [(n.pitch, n.velocity) for n in back.notes] == [(60, 127), (61, 1)]


def _smf(tracks: list[bytes], fmt: int = 1, division: int = 96) -> bytes:
    head = b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), division)
    return head + b"".join(
        b"MTrk" + struct.pack(">I", len(body)) + body for body in tracks
    )


END = bytes([0x00, 0xFF, 0x2F, 0x00])


def test_running_status_and_a_velocity_zero_note_off_are_read():
    """What other programs write: one status byte for a run of note-ons,
    and note-on at velocity 0 as the note-off."""
    body = bytes([0x00, 0x90, 60, 100, 0x60, 60, 0, 0x00, 64, 80, 0x30, 64, 0])
    track = mf.read(_smf([body + END])).tracks[0]
    assert [(n.start, n.length, n.pitch, n.velocity) for n in track.notes] == [
        (0.0, 1.0, 60, 100),
        (1.0, 0.5, 64, 80),
    ]


def test_a_format_zero_file_comes_back_as_one_track_per_channel():
    body = bytes([0x00, 0x90, 60, 100, 0x00, 0x99, 36, 100])
    body += bytes([0x60, 0x80, 60, 0, 0x00, 0x89, 36, 0])
    tracks = mf.read(_smf([body + END], fmt=0)).tracks
    assert [(t.channel, t.notes[0].pitch) for t in tracks] == [(0, 60), (9, 36)]


def test_a_note_never_released_ends_with_its_track():
    body = bytes([0x00, 0x90, 60, 100, 0x60, 0x90, 62, 100])
    notes = mf.read(_smf([body + END])).tracks[0].notes
    assert [(n.pitch, n.length) for n in notes] == [(60, 1.0), (62, 1 / 96)]


def test_unknown_chunks_sysex_and_other_metas_are_skipped():
    body = bytes([0x00, 0xF0, 0x02, 0x7E, 0xF7, 0x00, 0xFF, 0x7F, 0x01, 0x00])
    body += bytes([0x00, 0xC0, 5, 0x00, 0x90, 60, 100, 0x60, 0x80, 60, 0])
    data = _smf([body + END])
    data = data[:14] + b"XFIH" + struct.pack(">I", 2) + b"\x00\x00" + data[14:]
    assert len(mf.read(data).tracks[0].notes) == 1


HOSTILE = {
    "empty": b"",
    "not midi": b"RIFF" + bytes(64),
    "header only": b"MThd",
    "short header": b"MThd" + struct.pack(">I", 6) + b"\x00\x01",
    "header length past the file": b"MThd" + struct.pack(">I", 2**31) + bytes(6),
    "smpte time": _smf([END], division=0xE728),
    "zero division": _smf([END], division=0),
    "format nine": _smf([END], fmt=9),
    "track length past the file": _smf([END])[:-4] + struct.pack(">I", 2**31)[:4],
    "track cut mid event": _smf([bytes([0x00, 0x90, 60])]),
    "data byte with no status": _smf([bytes([0x00, 60, 100]) + END]),
    "endless vlq": _smf([bytes([0xFF] * 8) + END]),
    "meta length past the track": _smf([bytes([0x00, 0xFF, 0x03, 0x7F, 0x41])]),
    "sysex length past the track": _smf([bytes([0x00, 0xF0, 0xFF, 0xFF, 0x7F])]),
    "high bit in a data byte": _smf([bytes([0x00, 0x90, 60, 0x90]) + END]),
    "system realtime in a track": _smf([bytes([0x00, 0xF8]) + END]),
}


@pytest.mark.parametrize("name", HOSTILE)
def test_bytes_that_are_not_a_usable_file_raise_midi_error(name):
    with pytest.raises(MidiError):
        mf.read(HOSTILE[name])


def test_track_and_event_counts_are_bounded():
    on_off = bytes([0x00, 0x90, 60, 100, 0x01, 0x80, 60, 0])
    assert mf.read(_smf([on_off * 50 + END]), max_events=200).tracks
    with pytest.raises(MidiError, match="too many events"):
        mf.read(_smf([on_off * 50 + END]), max_events=99)
    with pytest.raises(MidiError, match="too many tracks"):
        mf.read(_smf([END] * 5), max_tracks=4)
    # a header that under-declares its chunks does not get past the cap
    lying = _smf([END] * 5)
    lying = lying[:10] + struct.pack(">H", 1) + lying[12:]
    with pytest.raises(MidiError, match="too many tracks"):
        mf.read(lying, max_tracks=4)


def test_closing_many_notes_of_one_key_is_not_quadratic():
    """150,000 note-ons that are never released is inside every cap. Held in
    a list per key, closing them took the first of the list each time —
    quadratic when they are all one key: seconds, under the studio lock.
    Measured against the same number spread over 120 keys (short lists
    either way), so the bound holds on any machine."""
    n = 150_000

    def seconds(keys: int) -> float:
        body = bytes([0x00, 0x90]) + b"".join(
            bytes([i % keys, 100, 0x00]) for i in range(n)
        )
        data = _smf([body[:-1] + END])
        best = float("inf")
        for _ in range(2):
            t0 = time.perf_counter()
            assert len(mf.read(data).tracks[0].notes) == n
            best = min(best, time.perf_counter() - t0)
        return best

    assert seconds(1) < 2.5 * seconds(120)


def test_no_prefix_of_a_real_file_raises_anything_but_midi_error():
    data = mf.write(_file())
    for cut in range(len(data)):
        try:
            mf.read(data[:cut])
        except MidiError:
            pass
