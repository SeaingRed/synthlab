"""The working loop's chord track: `Arrangement.chords`,
Perform's chord lane, `POST /fragment/chords/track`.

Held here: the field is absent when unset wherever an arrangement is
written down (the session file, `arr_dict`) and never in a render key —
it renders nothing, so a chord edit re-renders nothing and every loop
key from before it holds; the route takes the Song page's chord edits
(`songops.edit_chords`) on the loop's track and is one pattern undo
step; a grid post keeps it; the generators on the loop follow it first
(and a lane edit changes what "⚄ write" writes); Perform draws its tiles,
the roll its tones, and the keys' chord bar carries it for "follow"; the
stamp's "chord track" writes the chord under the step.

Nobody has listened to a part written over the loop's chords.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from synthlab.sequencer import midi
from synthlab.studio import schema
from synthlab.studio.gen import chordtrack
from synthlab.studio.gen import loop as gl
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.studio.state.model import grid_rows

HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/perform"}
LOOP = Arrangement(
    bpm=120.0, root="A1", scale_name="minor", bars=4, steps_per_bar=16, cells=[]
)
I_VI_III_VII = {
    "bars": 4,
    "slots": [
        {"at": 0.0, "deg": 1, "q": "triad", "inv": 0, "len": 4.0},
        {"at": 4.0, "deg": 6, "q": "triad", "inv": 0, "len": 4.0},
        {"at": 8.0, "deg": 3, "q": "triad", "inv": 0, "len": 4.0},
        {"at": 12.0, "deg": 7, "q": "triad", "inv": 0, "len": 4.0},
    ],
}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        s = c.app.state.studio
        s.arrangement, s.grid_span, s.grid_oct = LOOP, 2, 0
        s._pattern_undo.clear()
        yield c


def chords(client, **data):
    return client.post(
        "/fragment/chords/track",
        data={k: str(v) for k, v in data.items()},
        headers=HX,
    )


def held(client) -> dict | None:
    return client.app.state.studio.arrangement.chords


def test_absent_when_unset_and_never_a_render_input(client, tmp_path):
    s = client.app.state.studio
    assert "chords" not in schema.arr_dict(s.arrangement)
    s.save()
    saved = json.loads((tmp_path / "state.json").read_text("utf-8"))
    assert "chords" not in saved["arrangement"]
    before = client.app.state.svc.render_inputs("perform").key()
    assert chords(client, op="roll", seed=3).status_code == 200
    assert held(client)["slots"]
    # the loop's chords render nothing: the loop's key is the key it was
    assert client.app.state.svc.render_inputs("perform").key() == before
    s.save()
    saved = json.loads((tmp_path / "state.json").read_text("utf-8"))
    assert saved["arrangement"]["chords"] == held(client)
    # ... and come back with the session
    with TestClient(create_app(data_dir=tmp_path)) as again:
        assert again.app.state.studio.arrangement.chords == held(client)


def test_the_route_takes_the_songs_chord_edits_on_the_loops_track(client):
    r = chords(client, op="preset", prog=0)
    assert r.status_code == 200
    said = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert said.startswith("The loop's chords: ")
    n = len(held(client)["slots"])
    assert chords(client, op="set", slot=0, deg=4, q="7th").status_code == 200
    first = sorted(held(client)["slots"], key=lambda x: x["at"])[0]
    assert (first["deg"], first["q"]) == (4, "7th")
    assert chords(client, op="add").status_code == 200
    assert len(held(client)["slots"]) == n + 1
    assert chords(client, op="remove", slot=0).status_code == 200
    assert len(held(client)["slots"]) == n
    assert chords(client, op="split", slot=0).status_code == 200
    assert len(held(client)["slots"]) == n + 1
    assert chords(client, op="bars", bars=8).status_code == 200
    assert held(client)["bars"] == 8
    assert chords(client, op="clear").status_code == 200
    assert held(client) is None
    # with none, an edit starts from one bar of the key's first chord
    assert chords(client, op="set", slot=0, deg=5).status_code == 200
    assert [x["deg"] for x in held(client)["slots"]] == [5]


def test_refusals_change_nothing(client):
    chords(client, op="roll", seed=1)
    was = held(client)
    for bad in (
        {"op": "nope"},
        {"op": "set", "slot": "9", "deg": "2"},
        {"op": "set", "slot": "0", "q": "eleventh"},
        {"op": "bars", "bars": "3"},
        {"op": "set", "slot": "x"},
    ):
        r = chords(client, **bad)
        assert r.status_code == 422, bad
        assert held(client) == was


def test_a_chord_edit_is_one_pattern_undo_step(client):
    chords(client, op="roll", seed=1)
    first = held(client)
    chords(client, op="set", slot=0, deg=2)
    assert held(client) != first
    client.post("/fragment/pattern/undo", headers=HX)
    assert held(client) == first
    client.post("/fragment/pattern/undo", headers=HX)
    assert held(client) is None


def test_a_grid_post_keeps_the_chords(client):
    chords(client, op="roll", seed=1)
    was = held(client)
    client.post(
        "/fragment/arrangement",
        data={"bars": "4", "steps": "16", "span": "2", "cell.A2.0": "on"},
        headers=HX,
    )
    assert held(client) == was


def test_the_generators_follow_the_loops_chords_first():
    rows = grid_rows(LOOP, 2, 0)
    track, said = gl.harmony(replace(LOOP, chords=I_VI_III_VII), None, 7)
    assert track == I_VI_III_VII and said == "the loop's chords"
    # ... before the song's in its key
    song = {"root": "A1", "scale_name": "minor", "chords": chordtrack.DEFAULT}
    track, said = gl.harmony(replace(LOOP, chords=I_VI_III_VII), song, 7)
    assert said == "the loop's chords"
    # a part over them: its chord tones are the track's, bar by bar
    fields, note = gl.write(replace(LOOP, chords=I_VI_III_VII), rows, "chords", 7)
    assert "over the loop's chords" in note
    want = chordtrack.resolve(gl.song_of(replace(LOOP, chords=I_VI_III_VII)), None, 4)
    key = midi("A1") % 12
    for c in want:
        pcs = {(key + t) % 12 for t in c["tones"]}
        notes = [cell for cell in fields["cells"] if c["lo"] <= int(cell[1]) < c["hi"]]
        assert notes and all(midi(p) % 12 in pcs for p, _ in notes), c


def test_a_lane_edit_changes_what_write_writes(client):
    chords(client, op="roll", seed=1)
    w = {"lane": "notes", "kind": "chords", "seed": "11"}
    client.post("/fragment/gen", data=w, headers=HX)
    before = client.app.state.studio.arrangement.cells
    chords(client, op="set", slot=0, deg=4)  # the first chord another
    client.post("/fragment/gen", data=w, headers=HX)
    after = client.app.state.studio.arrangement.cells
    assert before != after
    # the first bar's notes are the new chord's
    a = client.app.state.studio.arrangement
    c = chordtrack.chord_at(chordtrack.resolve(gl.song_of(a), None, 4), 0)
    key = midi(a.root) % 12
    pcs = {(key + t) % 12 for t in c["tones"]}
    bar1 = [p for p, st in after if int(st) < 16]
    assert bar1 and all(midi(p) % 12 in pcs for p in bar1)


def test_perform_draws_the_lane_the_tones_and_the_keys_carry_it(client):
    page = client.get("/view/perform").text
    assert 'class="lchord-empty"' in page and "ctile lctile" not in page
    assert ' ct"' not in page and " ct " not in page  # no tones without chords
    assert "data-track=" not in page
    s = client.app.state.studio
    s.arrangement = replace(s.arrangement, chords=I_VI_III_VII)
    page = client.get("/view/perform").text
    assert page.count("ctile lctile") == 4  # a tile a chord, one a bar
    assert 'data-post="/fragment/chords/track"' in page
    assert " ct" in page and " cr" in page  # the roll's chord tones
    assert '<option value="track">chord track</option>' in page
    assert "data-track=" in page and 'id="chords-follow"' in page


def test_a_pentatonic_loop_reads_its_parent_on_the_lane_and_the_pads(client):
    """A pentatonic minor's i VI III VII is Am F C G: the
    lane's tiles name it so, "follow the loop's chords" plays it, and the
    pad it lights is the key's own pad on that root — none for the F, the
    five pads having none."""
    s = client.app.state.studio
    s.arrangement = replace(LOOP, scale_name="pentatonic_minor", chords=I_VI_III_VII)
    tiles = gl.lane(s.arrangement)
    assert [(t["roman"], t["name"]) for t in tiles] == [
        ("i", "Am"),
        ("VI", "F"),
        ("III", "C"),
        ("VII", "G"),
    ]
    track = client.app.state.svc.loop_track("A3")
    # the five pads: A C D E G — F has none, C is the 2nd, G the 5th
    assert [t[2] for t in track["tiles"]] == [1, 0, 2, 5]
    assert [t[5][0] % 12 for t in track["tiles"]] == [9, 5, 0, 7]
    # in a seven-tone key the pad is the degree itself
    s.arrangement = replace(LOOP, chords=I_VI_III_VII)
    track = client.app.state.svc.loop_track("A3")
    assert [t[2] for t in track["tiles"]] == [1, 6, 3, 7]


def test_the_stamp_writes_the_chord_under_the_step(client):
    s = client.app.state.studio
    s.arrangement = replace(s.arrangement, chords=I_VI_III_VII)
    r = client.post(
        "/fragment/pattern/notes/stamp",
        data={
            "row": "A2",  # (F at or below it: F2, inside the window)
            "step": "16",
            "q": "track",
            "len": "4",
            "span": "2",
            "oct": "0",
        },
        headers=HX,
    )
    assert r.status_code == 200
    # bar 2 holds VI: F A C in A minor, its root the nearest F at or below the row
    got = sorted(midi(p) for p, st in s.arrangement.cells if int(st) == 16)
    assert [m % 12 for m in got] == [5, 9, 0]


def test_in_another_meter_a_chord_is_still_a_bar_of_it():
    """A chord track's beats are quarters of the loop's bar (`resolve`
    reads `steps_per_bar / 4` a beat): over a 3/4 or 7/8 grid a chord a bar
    is a tile a bar of THAT meter — as the generators fit their 4/4 lanes
    bar to bar (`meterops.beat_fit`), so a part's first bar is the first
    chord's tones."""
    for meter, spb in (("3/4", 12), ("7/8", 14)):
        a = replace(LOOP, meter=meter, steps_per_bar=spb, chords=I_VI_III_VII)
        tiles = gl.lane(a)
        assert [(t["lo"], t["span"]) for t in tiles] == [
            (i * spb, spb) for i in range(4)
        ]
        fields, note = gl.write(a, grid_rows(a, 2, 0), "chords", 7)
        assert "over the loop's chords" in note
        c = chordtrack.chord_at(chordtrack.resolve(gl.song_of(a), None, 4, spb), 0)
        pcs = {(midi("A1") + t) % 12 for t in c["tones"]}
        bar1 = [p for p, st in fields["cells"] if int(st) < spb]
        assert bar1 and all(midi(p) % 12 in pcs for p in bar1), meter


def test_a_meter_change_and_a_chord_edit_are_a_step_each(client):
    chords(client, op="roll", seed=1)
    first = held(client)
    s = client.app.state.studio
    r = client.post("/fragment/meter", data={"meter": "3/4"}, headers=HX)
    assert r.status_code == 200 and s.arrangement.meter == "3/4"
    assert held(client) == first  # the meter moves no chord
    chords(client, op="set", slot=0, deg=2)
    client.post("/fragment/pattern/undo", headers=HX)
    assert held(client) == first and s.arrangement.meter == "3/4"
    client.post("/fragment/pattern/undo", headers=HX)
    assert s.arrangement.meter == "4/4" and held(client) == first
    # both are written down only when set
    a = schema.arr_dict(s.arrangement)
    assert "meter" not in a and a["chords"] == first
