"""The note generators on the working loop (`gen/loop.py`,
`POST /fragment/gen` with `lane=notes`): a bass, chords, a pad, an arp or
a lead written into the octaves Perform's roll is showing.

Held here: every kind writes notes in key, on the rows on screen, inside
the grid, on any step grid and bar count; what the grid hides is kept;
the same seed is the same part; the harmony is the song's chord track in
its key and a seeded progression otherwise; a roll again is the SAME
pattern undo step and one Ctrl+Z puts the hand-drawn notes back; busier
only adds and sparser only removes; the six-row view opens two octaves;
the answer plays; a follower and an arp refuse with a reason.

Nobody listened: that a generated bass is a bass line is the ear's.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from synthlab.sequencer import SCALES, midi
from synthlab.studio.gen import chordtrack
from synthlab.studio.gen import loop as gl
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.studio.state.model import grid_rows

HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/perform"}
LOOP = Arrangement(
    bpm=120.0, root="A1", scale_name="minor", bars=4, steps_per_bar=16, cells=[]
)
KINDS = [k for k, _label in gl.KINDS]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        s = c.app.state.studio
        s.arrangement, s.grid_span, s.grid_oct = LOOP, 2, 0
        s._pattern_undo.clear()
        yield c


def gen(client, kind: str, **more):
    data = {"lane": "notes", "kind": kind, **{k: str(v) for k, v in more.items()}}
    return client.post("/fragment/gen", data=data, headers=HX)


def toast(r) -> str:
    """What the answer says: a done edit's note rides HX-Trigger, a refusal
    is the 422 toast fragment."""
    if r.status_code == 422:
        return r.text
    return json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]


def lane(client) -> list[list]:
    return client.app.state.studio.arrangement.cells


def depth(client) -> int:
    return len(client.app.state.studio._pattern_undo)


# --- the pure half ----------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("scale", ["minor", "pentatonic_minor", "major"])
@pytest.mark.parametrize("bars,spb", [(1, 16), (2, 8), (4, 16), (2, 32), (4, 4)])
def test_every_kind_lands_in_key_on_the_rows_and_inside_the_grid(
    kind, scale, bars, spb
):
    arr = replace(LOOP, scale_name=scale, bars=bars, steps_per_bar=spb)
    for octave in (0, 2):
        rows = grid_rows(arr, 2, octave)
        fields, note = gl.write(arr, rows, kind, 7)
        assert fields["cells"], (kind, scale, bars, spb)
        pcs = {(midi("A1") + t) % 12 for t in SCALES[scale]}
        # a key that is not seven-tone plays its parent's chords: their
        # tones outside it land between the rows (the route then opens the
        # chromatic ones), never left out
        song = gl.song_of(arr)
        held = gl.harmony(arr, None, 7)[0]
        parent = set().union(
            *(chordtrack.pitch_classes(song, x) for x in held["slots"])
        )
        lo, hi = min(map(midi, rows)), max(map(midi, rows))
        for pitch, step in fields["cells"]:
            assert lo <= midi(pitch) <= hi
            assert pitch in rows if midi(pitch) % 12 in pcs else len(pcs) < 7
            assert midi(pitch) % 12 in pcs | parent
            assert 0 <= step < bars * spb
        on = {(c[0], c[1]) for c in fields["cells"]}
        assert all((c[0], c[1]) in on for c in fields["cuts"])
        assert all(0 <= int(k) < bars * spb for k in fields["velocities"])
        assert gl.LABELS[kind] in note and "seed 7" in note
        assert "left out" not in note  # two octaves hold every kind


def test_the_same_seed_is_the_same_part_and_another_seed_is_another():
    rows = grid_rows(LOOP, 2, 0)
    a, _ = gl.write(LOOP, rows, "lead", 3)
    b, _ = gl.write(LOOP, rows, "lead", 3)
    assert a == b
    assert any(gl.write(LOOP, rows, "lead", s)[0] != a for s in range(4, 12))


def test_what_the_grid_hides_is_kept():
    hidden = [["F#5", 0], ["A1", 70]]  # off the rows; past the grid's end
    arr = replace(LOOP, cells=[["A1", 0], *hidden], cuts=[["A1", 70]])
    fields, _ = gl.write(arr, grid_rows(arr, 2, 0), "bass_root", 1)
    assert all(c in fields["cells"] for c in hidden)
    assert ["A1", 70] in fields["cuts"]


def test_the_harmony_is_the_songs_in_its_key_and_a_seeded_preset_otherwise():
    held = chordtrack.from_degrees((1, 4), 2)
    song = {"root": "A1", "scale_name": "minor", "chords": held}
    assert gl.harmony(LOOP, song, 1) == (held, "the song's chords")
    # another key: the song's chords are not this loop's
    track, said = gl.harmony(replace(LOOP, root="C2"), song, 1)
    assert track != held and "–" in said
    assert [s["deg"] for s in track["slots"]] in [list(p) for p in chordtrack.PRESETS]
    # a loop shorter than a progression plays its opening, a chord a bar
    short, said = gl.harmony(replace(LOOP, bars=2), None, 1)
    assert short["bars"] == 2 and len(short["slots"]) == 2
    assert gl.harmony(LOOP, None, 1) == gl.harmony(LOOP, None, 1)


def test_busier_only_adds_and_sparser_only_removes():
    rows = grid_rows(LOOP, 2, 0)
    fields, _ = gl.write(LOOP, rows, "lead", 5)
    arr = replace(LOOP, **fields)
    was = {(c[0], c[1]) for c in arr.cells}
    more, _ = gl.vary(arr, rows, "busier", 0.5, 2)
    got = {(c[0], c[1]) for c in more["cells"]}
    assert was < got and all(c[0] in rows for c in more["cells"])
    less, _ = gl.vary(arr, rows, "sparser", 0.5, 2)
    got = {(c[0], c[1]) for c in less["cells"]}
    assert got < was and got


def test_refusals_say_why():
    rows = grid_rows(LOOP, 2, 0)
    with pytest.raises(gl.Refused, match="arp"):
        gl.write(replace(LOOP, arp_mode="up"), rows, "lead", 1)
    with pytest.raises(gl.Refused, match="no notes"):
        gl.vary(LOOP, rows, "busier", 0.25, 1)
    with pytest.raises(gl.Refused, match="16-step"):
        gl.vary(
            replace(LOOP, steps_per_bar=8, cells=[["A1", 0]]), rows, "sparser", 0.2, 1
        )
    with pytest.raises(gl.Refused):
        gl.write(LOOP, rows, "polka", 1)


# --- the route --------------------------------------------------------------------


def test_perform_draws_the_generators_as_commands(client):
    page = client.get("/view/perform").text
    for _kind, label in gl.KINDS:
        assert f'data-cmd="Write a {label}"' in page
    for cmd in ("Roll the part again", "Busier notes", "Sparser notes"):
        assert f'data-cmd="{cmd}"' in page
    assert page.count('"lane": "notes"') == len(gl.KINDS) + 3


@pytest.mark.parametrize("kind", KINDS)
def test_a_part_is_written_plays_and_is_one_undo_step(client, kind):
    s = client.app.state.studio
    s.arrangement = replace(LOOP, cells=[["A1", 0], ["C2", 4]])
    r = gen(client, kind, seed=11)
    assert r.status_code == 200, r.text
    trig = json.loads(r.headers["HX-Trigger"])
    assert trig["preview-ready"]["autoplay"] is True
    assert "seed 11" in toast(r) and "Ctrl+Z" in toast(r)
    assert len(lane(client)) > 2 and depth(client) == 1
    client.post("/fragment/pattern/undo", headers=HX)
    assert lane(client) == [["A1", 0], ["C2", 4]] and depth(client) == 0


def test_rolling_again_is_the_same_undo_step_and_another_part(client):
    s = client.app.state.studio
    s.arrangement = replace(LOOP, cells=[["E2", 2]])
    assert "nothing to roll again" in toast(gen(client, "again"))
    gen(client, "lead", seed=1)
    first = [list(c) for c in lane(client)]
    for seed in (2, 3, 4):
        r = gen(client, "again", seed=seed)
        assert r.status_code == 200 and "lead" in toast(r)
    assert lane(client) != first and depth(client) == 1
    # ... until a hand edits the grid: the next roll is a step of its own
    s.arrangement = replace(s.arrangement, cells=[*lane(client), ["A1", 63]])
    gen(client, "again", seed=5)
    assert depth(client) == 2
    client.post("/fragment/pattern/undo", headers=HX)
    client.post("/fragment/pattern/undo", headers=HX)
    assert lane(client) == [["E2", 2]]


def test_busier_and_sparser_are_a_step_each_and_keep_the_last_part_for_again(client):
    gen(client, "lead", seed=1)
    n = len(lane(client))
    assert gen(client, "sparser", seed=1).status_code == 200
    assert len(lane(client)) < n and depth(client) == 2
    assert gen(client, "busier", seed=1).status_code == 200
    assert depth(client) == 3
    assert "lead" in toast(gen(client, "again", seed=9))
    assert depth(client) == 4  # the grid is no longer what the roll left


def test_the_six_row_view_opens_two_octaves_and_the_window_is_where_it_lands(client):
    s = client.app.state.studio
    s.grid_span, s.grid_oct = 1, 2
    r = gen(client, "arp", seed=3)
    assert r.status_code == 200 and s.grid_span == 2 and s.grid_oct == 2
    rows = grid_rows(s.arrangement, 2, 2)
    assert lane(client) and all(c[0] in rows for c in lane(client))
    # the transport comes back by its watch, redrawn at the new rows
    assert 'id="transport"' in r.text


def test_a_pentatonic_loop_plays_its_parents_vi_and_the_roll_shows_it(client):
    """A pentatonic minor's i VI III VII is Am F C G: the
    chords part writes the F a five-tone row cannot show, none of it is
    left out, and the roll opens its chromatic rows where it sits."""
    s = client.app.state.studio
    s.arrangement = replace(
        LOOP,
        scale_name="pentatonic_minor",
        chords=chordtrack.from_degrees((1, 6, 3, 7), 4),
    )
    r = gen(client, "chords", seed=3)
    assert r.status_code == 200 and "left out" not in toast(r)
    bar2 = {midi(c[0]) % 12 for c in lane(client) if 16 <= c[1] < 32}
    assert bar2 == {5, 9, 0}  # F A C
    assert s.grid_span == 3
    assert all(c[0] in grid_rows(s.arrangement, 3, s.grid_oct) for c in lane(client))


def test_the_route_refuses_what_it_cannot_do(client):
    s = client.app.state.studio
    assert "No such generator" in toast(gen(client, "polka"))
    assert "numbers" in toast(gen(client, "lead", seed="x"))
    s.arrangement = replace(LOOP, arp_mode="up")
    assert "arp" in toast(gen(client, "lead"))
    assert depth(client) == 0 and lane(client) == []
    # the drum generator is still the default lane
    assert "no beat to fill" in toast(
        client.post("/fragment/gen", data={"kind": "fill"}, headers=HX)
    )
