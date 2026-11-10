"""MIDI import (`POST /fragment/pattern/import`, `midi_bridge.to_grid`): a
.mid file's notes onto the note grid — quantised to its steps, clamped to
its rows and bars, one pattern undo step — and a hostile file is a 422
toast that changes nothing."""

from __future__ import annotations

import json
import struct

import pytest
from fastapi.testclient import TestClient

from synthlab import midifile as mf
from synthlab.midifile import MidiFile, Note, Track
from synthlab.sequencer import midi as number
from synthlab.studio import limits, midi_bridge, security
from synthlab.studio.security import BodySizeLimit
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement, pitch_rows

PERFORM = {"HX-Current-URL": "http://testserver/view/perform"}
# A1 pentatonic minor, one bar of 8 steps (a step is half a beat). It shows
# A1 C2 D2 E2 G2 A2, and keeps two notes it cannot show: one written under
# another key (F#3) and one past the bar (step 9).
GRID = Arrangement(
    bpm=120.0,
    bars=1,
    steps_per_bar=8,
    cells=[["A1", 0], ["F#3", 2], ["C2", 9]],
    velocities={"0": 0.4, "9": 0.7},
    slides=[0, 9],
)


def _n(pitch: str, start: float, length: float = 0.5, vel: int = 100) -> Note:
    return Note(start, length, number(pitch), vel)


def _mid(*tracks: Track) -> bytes:
    return mf.write(MidiFile(bpm=98.0, tracks=list(tracks)))


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        c.app.state.studio.arrangement = GRID
        yield c


def _post(client, data: bytes, **form):
    return client.post(
        "/fragment/pattern/import",
        files={"file": ("riff.mid", data, "audio/midi")},
        data=form,
        headers=PERFORM,
    )


def _toast(r) -> str:
    return json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]


def test_a_file_lands_on_the_grid_quantised_to_its_steps(client):
    """A note a hair off the step starts on it; a long one is held for its
    length in steps; its velocity goes on its start step."""
    track = Track(
        name="Riff",
        notes=[_n("C2", 0.02, 1.4, 64), _n("E2", 1.48, 0.5, 127), _n("A2", 3.5, 0.1)],
    )
    r = _post(client, _mid(track))
    assert r.status_code == 200, r.text
    a = client.app.state.studio.arrangement
    shown = [c for c in a.cells if c[1] < 8 and c[0] != "F#3"]
    assert shown == [["A2", 7], ["C2", 0], ["C2", 1], ["C2", 2], ["E2", 3]]
    assert a.velocities["0"] == pytest.approx(64 / 127, abs=1e-3)
    assert a.velocities["3"] == 1.0
    assert _toast(r) == "Imported 3 notes from “Riff”."
    assert "preview-ready" in r.headers["HX-Trigger"]  # the loop re-rendered


def test_the_import_replaces_what_the_grid_shows_and_keeps_what_it_hides(client):
    _post(client, _mid(Track(notes=[_n("D2", 1.0)])))
    a = client.app.state.studio.arrangement
    assert ["A1", 0] not in a.cells  # shown: replaced
    assert ["F#3", 2] in a.cells and ["C2", 9] in a.cells  # hidden: kept
    assert a.velocities == {"9": 0.7, "2": pytest.approx(100 / 127, abs=1e-3)}
    assert a.slides == [9]
    assert (a.bars, a.steps_per_bar, a.bpm) == (1, 8, 120.0)  # the grid's own


def test_notes_off_the_rows_or_past_the_last_bar_are_dropped_and_counted(client):
    track = Track(
        notes=[
            _n("A1", 0.0),
            _n("B1", 1.0),  # not in A minor pentatonic: no row
            _n("A3", 2.0),  # above the six rows
            _n("C2", 4.0),  # bar 2 of a one-bar grid
            _n("C2", 9.5),
        ]
    )
    r = _post(client, _mid(track))
    a = client.app.state.studio.arrangement
    assert [c for c in a.cells if c[1] < 8 and c[0] != "F#3"] == [["A1", 0]]
    assert _toast(r) == (
        "Imported 1 note · dropped 2 off the grid's rows, 2 past bar 1."
    )


def test_a_note_held_past_the_grids_end_is_cut_there(client):
    _post(client, _mid(Track(notes=[_n("E2", 3.0, 8.0)])))
    a = client.app.state.studio.arrangement
    assert [c for c in a.cells if c[0] == "E2"] == [["E2", 6], ["E2", 7]]


def test_two_notes_side_by_side_on_one_row_import_as_a_cut(client):
    """A held run is one note; the file had two, so the second's start is
    a cut (`Arrangement.cuts`) and the grid plays two."""
    _post(client, _mid(Track(notes=[_n("G2", 0.0, 1.0), _n("G2", 1.0, 1.0)])))
    a = client.app.state.studio.arrangement
    assert [c for c in a.cells if c[0] == "G2"] == [["G2", s] for s in range(4)]
    assert a.cuts == [["G2", 2]]


def test_a_track_written_octaves_away_is_moved_to_the_grid(client):
    """A file around C4 holds nothing on a grid rooted at A1: it is moved
    by whole octaves to where most of it fits, and the toast says so."""
    track = Track(notes=[_n("A3", 0.0), _n("C4", 1.0), _n("E4", 2.0)])
    r = _post(client, _mid(track))
    a = client.app.state.studio.arrangement
    assert [c for c in a.cells if c[1] < 8 and c[0] != "F#3"] == [
        ["A1", 0],
        ["C2", 2],
        ["E2", 4],
    ]
    assert _toast(r) == "Imported 3 notes · moved down 2 octaves."


def test_the_two_octave_range_takes_what_six_rows_cannot(client):
    client.app.state.studio.grid_span = 2
    assert "A2" in pitch_rows(GRID, 2) and "G3" in pitch_rows(GRID, 2)
    r = _post(client, _mid(Track(notes=[_n("A1", 0.0), _n("G3", 1.0)])))
    assert _toast(r) == "Imported 2 notes."


def test_the_first_melodic_track_is_imported_unless_one_is_chosen(client):
    drums = Track(name="Kit", channel=mf.DRUM_CHANNEL, notes=[_n("C2", 0.0)])
    bass = Track(name="Bass", channel=0, notes=[_n("A1", 1.0)])
    lead = Track(name="Lead", channel=1, notes=[_n("E2", 2.0)])
    data = _mid(drums, bass, lead)
    assert "from “Bass”" in _toast(_post(client, data))
    assert "from “Lead”" in _toast(_post(client, data, track="3"))
    assert "from “Kit”" in _toast(_post(client, data, track="1"))
    # the last: all digits, and past what int() converts (a ValueError that
    # is no ImportRefused: it was a 500)
    for bad in ("4", "0", "x", "-1", "１", "9" * 5000):
        r = _post(client, data, track=bad)
        assert r.status_code == 422, bad[:8]


def test_an_import_is_one_pattern_undo_step(client):
    """It goes through `routing.pattern_edit`, as every grid edit does:
    Ctrl+Z puts the grid back."""
    s = client.app.state.studio
    assert not s.can_undo_pattern()
    _post(client, _mid(Track(notes=[_n("D2", 1.0), _n("E2", 2.0)])))
    assert s.arrangement.cells != GRID.cells and s.can_undo_pattern()
    r = client.post("/fragment/pattern/undo", headers=PERFORM)
    assert r.status_code == 200
    a = s.arrangement
    assert (a.cells, a.velocities, a.slides) == (
        GRID.cells,
        GRID.velocities,
        GRID.slides,
    )


def test_a_file_with_nothing_for_this_grid_is_refused_and_changes_nothing(client):
    s = client.app.state.studio
    r = _post(client, _mid(Track(notes=[_n("B1", 0.0), _n("C2", 6.0)])))
    assert r.status_code == 422
    assert "1 off the grid" in r.text and "1 past bar 1" in r.text
    assert s.arrangement == GRID and not s.can_undo_pattern()
    r = _post(client, mf.write(MidiFile()))
    assert r.status_code == 422 and "no notes" in r.text


def _smf(body: bytes, division: int = 96, count: int = 1) -> bytes:
    head = b"MThd" + struct.pack(">IHHH", 6, 1, count, division)
    return head + b"MTrk" + struct.pack(">I", len(body)) + body


HOSTILE = {
    "empty": b"",
    "text": b"not a midi file at all",
    "a zip": b"PK\x03\x04" + bytes(40),
    "header only": b"MThd" + struct.pack(">IHHH", 6, 1, 1, 96),
    "truncated track": _smf(bytes([0x00, 0x90, 60])),
    "track length past the file": b"MThd"
    + struct.pack(">IHHH", 6, 1, 1, 96)
    + b"MTrk"
    + struct.pack(">I", 2**31),
    "smpte time": _smf(bytes([0x00, 0xFF, 0x2F, 0x00]), division=0xE728),
    "endless vlq": _smf(bytes([0xFF] * 64)),
    "more tracks than the cap": _smf(b"", count=limits.MAX_MIDI_TRACKS + 1),
    "more events than the cap": _smf(
        bytes([0x00, 0x90, 60, 100, 0x00, 0x80, 60, 0])
        * (limits.MAX_MIDI_EVENTS // 2 + 1)
    ),
}


@pytest.mark.parametrize("name", HOSTILE)
def test_a_hostile_file_is_a_422_toast_never_a_500(client, name):
    s = client.app.state.studio
    r = _post(client, HOSTILE[name])
    assert r.status_code == 422, (name, r.status_code)
    assert 'class="toast error"' in r.text
    assert s.arrangement == GRID and not s.can_undo_pattern()


def test_a_post_without_a_file_is_a_422(client):
    r = client.post("/fragment/pattern/import", headers=PERFORM)
    assert r.status_code == 422
    r = client.post("/fragment/pattern/import", data={"file": "x"}, headers=PERFORM)
    assert r.status_code == 422


def test_the_upload_is_capped_by_the_limits_module(client, monkeypatch):
    """The path has its own body cap (`security._PATH_UPLOAD_LIMITS`), far
    under the generic upload ceiling, and the route's reader holds the
    file part to the same number."""
    assert (
        BodySizeLimit._limit_for("/fragment/pattern/import")
        == limits.MAX_MIDI_UPLOAD_BYTES
        < limits.MAX_UPLOAD_BYTES
    )
    big = _mid(Track(notes=[_n("A1", 0.0)])) + bytes(limits.MAX_MIDI_UPLOAD_BYTES)
    assert _post(client, big).status_code == 413  # the middleware
    # under the body cap, over the part's own: the route says so
    monkeypatch.setattr(limits, "MAX_MIDI_UPLOAD_BYTES", 64)
    monkeypatch.setitem(
        security._PATH_UPLOAD_LIMITS, "/fragment/pattern/import", "MAX_UPLOAD_BYTES"
    )
    r = _post(client, _mid(Track(notes=[_n("A1", s * 0.5) for s in range(8)])))
    assert r.status_code == 413 and "too large" in r.text
    assert client.app.state.studio.arrangement == GRID


def test_the_import_control_is_a_plain_form_on_perform(client):
    """No script: a multipart htmx form beside the loop's MIDI export,
    outside #transport (whose own `input` trigger would post the old grid
    over the import)."""
    html = client.get("/view/perform").text
    assert 'hx-post="/fragment/pattern/import"' in html
    assert 'hx-encoding="multipart/form-data"' in html
    head, _sep, rest = html.partition('id="transport"')
    assert 'hx-post="/fragment/pattern/import"' in head
    assert "/fragment/pattern/import" not in rest


def test_to_grid_prefers_the_nearest_octave_on_a_tie():
    """A1 and A2 are both rows: a lone A2 stays where it was written."""
    rows = pitch_rows(GRID, 1)
    lane = {
        "bars": 1,
        "steps_per_bar": 8,
        "cells": [],
        "cuts": [],
        "velocities": {},
        "slides": [],
    }
    out, note = midi_bridge.to_grid(Track(notes=[_n("A2", 0.0)]), lane, rows)
    assert out["cells"] == [["A2", 0]] and "moved" not in note
