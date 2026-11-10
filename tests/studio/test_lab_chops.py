"""Chops in the Sample Lab (`studio/labchops.py`, `POST /lab/chop`,
`/lab/chop/keep`, the chop audition, the stem downloads, "Open in Lab").

- the functions on a meta: add, split, the edge rule, select, snap, auto;
- `chops` in a meta anyone can edit (`sources.clean_meta`);
- the routes, Auto by each rule on engine-rendered songs (`tests/songs.py`);
- a chop keeps its own mix; Keep and Update clip; what is refused;
- whole stems out: FLAC, WAV, the zip.

Separation is a fake behind the real separator's interface (`lab_helpers`).
Cleaning itself is `test_lab_clean.py`. Not proven here: how any of it
sounds.
"""

from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pytest
import soundfile
from fastapi.testclient import TestClient

from synthlab import stems
from synthlab.audio import read_pcm16, wav_bytes
from synthlab.core import SR
from synthlab.studio import lab, labchops, labclean, limits, sources
from synthlab.studio.clips import clip_hash, list_clips, load_clip
from synthlab.studio.server import create_app
from synthlab.studio.service import StudioService
from tests.songs import drum_loop, loop, voice_line
from tests.studio.lab_helpers import (
    LAB,
    OneStem,
    SixBands,
    fake_separation,
    separate,
    song_wav,
    source_id,
    upload,
)

BAR, BEAT = 2.0, 0.5  # seconds at the 120 bpm every song here is put on
SID = "0" * 16
ROWS = {
    "kick": "x.....x...x.....x.....x...x.....",
    "snare": "....x.......x.......x.......x...",
    "hat": "..x...x...x...x...x...x...x...x.",
}


@pytest.fixture(scope="module")
def wav() -> bytes:
    return song_wav()  # 8 bars at 120 bpm: 16 s


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


@pytest.fixture
def loaded(client, wav) -> TestClient:
    assert upload(client, wav).status_code == 200
    _grid(client)
    return client


def _grid(client, bpm: float = 120.0) -> None:
    """Put the song on a typed grid from 0 (a test of chops is not a test of
    the tempo detector)."""
    sources.update_meta(
        client.app.state.svc.data_dir,
        source_id(client),
        lambda m: m["user"].update(bpm=bpm, offset=0.0),
    )


def _no_tempo(client) -> None:
    """A song the analysis found no tempo in (and nobody typed one)."""
    sources.update_meta(
        client.app.state.svc.data_dir,
        source_id(client),
        lambda m: m["analysis"].update(bpm=0.0),
    )


def _meta(client) -> dict:
    return sources.list_sources(client.app.state.svc.data_dir)[0]


def _post(client, path: str, **data):
    return client.post(path, data={"source": source_id(client), **data}, headers=LAB)


def _chop(client, **data):
    return _post(client, "/lab/chop", **data)


def _chops(client) -> list[dict]:
    return _meta(client)["chops"]


def _spans(client) -> list[tuple[float, float]]:
    return [(c["start"], c["end"]) for c in _chops(client)]


def _toast(r) -> str:
    assert r.status_code == 422, r.text
    return r.text


def _song(seconds: float = 16.0, **extra) -> dict:
    """A meta as the store hands it over: 120 bpm from 0, no stems."""
    raw = {"seconds": seconds, "user": {"bpm": 120.0, "offset": 0.0}, **extra}
    return sources.clean_meta(raw, SID)


# --- the functions on a meta -------------------------------------------------


def test_add_makes_a_chop_with_a_short_hex_id_inside_the_gap_it_starts_in():
    m = _song()
    a = labchops.add(m, 2.0, 4.0)
    assert (a["start"], a["end"], a["name"]) == (2.0, 4.0, "chop 1")
    assert len(a["id"]) == 4 and int(a["id"], 16) >= 0
    assert a["stems"] == [] and a["model"] == "" and a["clip"] == ""
    assert a["clean"] == labclean.DEFAULTS
    b = labchops.add(m, 1.0, 9.0)  # runs into the first: cut back
    assert (b["start"], b["end"]) == (1.0, 2.0) and b["id"] != a["id"]
    assert labchops.add(m, 3.0, 5.0) is None  # starts inside a chop
    assert labchops.add(m, 5.0, 5.01) is None  # shorter than a chop may be
    assert [c["start"] for c in m["chops"]] == [1.0, 2.0]  # kept in time order
    assert labchops.add(m, 15.0, 99.0)["end"] == 16.0  # the song's end


def test_ids_stay_unique_through_a_full_source():
    m = _song(600.0)
    for i in range(limits.MAX_LAB_CHOPS):
        assert labchops.add(m, i * 1.0, i * 1.0 + 0.5) is not None
    assert len({c["id"] for c in m["chops"]}) == limits.MAX_LAB_CHOPS
    assert labchops.add(m, 500.0, 501.0) is None  # the source is full
    assert labchops.split(m, 0.25) is None


def test_split_cuts_a_chop_in_two_or_starts_one_to_the_next_chop_or_region_end():
    m = _song()
    lab.set_region(m, "seconds", 4.0, 8.0)  # 4 .. 12
    first = labchops.split(m, 6.0)  # outside any chop: runs to the region's end
    assert (first["start"], first["end"]) == (6.0, 12.0)
    before = labchops.split(m, 4.5)  # ... or to the next chop
    assert (before["start"], before["end"]) == (4.5, 6.0)
    first["clean"]["trim_db"], first["clip"] = -30.0, "kept"
    second = labchops.split(m, 9.0)  # inside: the chop is cut there
    assert (first["start"], first["end"]) == (6.0, 9.0)
    assert (second["start"], second["end"]) == (9.0, 12.0)
    assert second["clean"]["trim_db"] == -30.0  # the cleaning goes with it
    assert second["clip"] == "" and first["clip"] == "kept"  # the clip does not
    assert labchops.split(m, 9.01) is None  # too near an edge to cut
    past = labchops.split(m, 13.0)  # past the region: to the song's end here
    assert (past["start"], past["end"]) == (13.0, 16.0)
    assert [c["start"] for c in m["chops"]] == [4.5, 6.0, 9.0, 13.0]


def test_an_edge_is_held_by_its_own_chop_and_a_touching_neighbour_follows():
    """The sampler's slice rule (`sampler.nudge`), for both edges."""
    m = _song()
    a = labchops.add(m, 2.0, 4.0)
    b = labchops.add(m, 4.0, 6.0)  # touches a
    c = labchops.add(m, 8.0, 10.0)  # does not touch b
    # b's start moves and a's end follows it
    labchops.move_edge(m, b["id"], "start", 3.0)
    assert (a["end"], b["start"]) == (3.0, 3.0)
    # ... the same seam from the other side: a's end carries b's start
    labchops.move_edge(m, a["id"], "end", 3.5)
    assert (a["end"], b["start"]) == (3.5, 3.5)
    # the follower is never made shorter than a chop may be
    labchops.move_edge(m, b["id"], "start", 0.0)
    assert b["start"] == a["end"] == pytest.approx(2.0 + limits.MIN_CHOP_SECONDS)
    # its own other edge wins
    labchops.move_edge(m, b["id"], "start", 99.0)
    assert b["start"] == pytest.approx(6.0 - limits.MIN_CHOP_SECONDS)
    # a neighbour that did not touch is a wall, and stays where it was
    labchops.move_edge(m, b["id"], "end", 9.0)
    assert b["end"] == 8.0 and c["start"] == 8.0
    labchops.move_edge(m, c["id"], "end", 99.0)
    assert c["end"] == 16.0  # the song's end
    labchops.move_edge(m, a["id"], "start", -5.0)
    assert a["start"] == 0.0
    assert labchops.move_edge(m, "nope", "start", 1.0) is None
    assert labchops.move_edge(m, a["id"], "middle", 1.0) is None
    assert labchops.move_edge(m, a["id"], "start", float("nan")) is None
    spans = [(c["start"], c["end"]) for c in m["chops"]]
    assert all(e - s >= limits.MIN_CHOP_SECONDS - 1e-9 for s, e in spans)
    assert all(
        a_end <= b_start + 1e-9
        for (_, a_end), (b_start, _) in zip(spans, spans[1:], strict=False)
    )


def test_an_edge_rule_matches_the_samplers_on_the_same_seam():
    from synthlab import sampler

    m = _song(10.0)
    a = labchops.add(m, 2.0, 4.0)
    b = labchops.add(m, 4.0, 6.0)
    slices = [sampler.Slice(0.2, 0.4), sampler.Slice(0.4, 0.6)]
    for at in (3.0, 5.0, 1.0, 7.5, 2.02, 5.99):
        labchops.move_edge(m, b["id"], "start", at)
        slices = sampler.move_start(
            slices, 1, at / 10.0, limits.MIN_CHOP_SECONDS / 10.0
        )
        assert b["start"] == pytest.approx(slices[1].a * 10.0, abs=1e-4)
        assert a["end"] == pytest.approx(slices[0].b * 10.0, abs=1e-4)


def test_remove_rename_and_clean_change_only_their_chop():
    m = _song()
    a, b = labchops.add(m, 0.0, 1.0), labchops.add(m, 2.0, 3.0)
    assert labchops.rename(m, a["id"], "  verse   one  ")["name"] == "verse one"
    assert labchops.rename(m, a["id"], "   ")["name"] == "verse one"  # a name stays
    assert (
        len(labchops.rename(m, a["id"], "x" * 500)["name"])
        == limits.MAX_CHOP_NAME_CHARS
    )
    labchops.set_clean(
        m, b["id"], {"gate_db": "-200", "fit": "sideways", "fade_in": 20}
    )
    assert b["clean"] == {**labclean.DEFAULTS, "gate_db": -60.0, "fade_in": 20.0}
    assert a["clean"] == labclean.DEFAULTS
    assert labchops.remove(m, a["id"]) and not labchops.remove(m, a["id"])
    assert [c["id"] for c in m["chops"]] == [b["id"]]
    assert labchops.rename(m, "gone", "x") is None
    assert labchops.set_clean(m, "gone", {}) is None


def test_select_makes_the_chop_the_region_in_bars_when_it_sits_on_them():
    m = _song()
    on, off = labchops.add(m, 4.0, 8.0), labchops.add(m, 9.1, 10.3)
    labchops.select(m, on["id"])
    assert m["region"] == {"mode": "bars", "start": 2.0, "length": 2.0}
    assert lab.selected_chop(m) == on["id"]
    labchops.select(m, off["id"])
    assert m["region"]["mode"] == "seconds" and lab.span(m) == pytest.approx((9.1, 1.2))
    assert lab.selected_chop(m) == off["id"]
    lab.set_region(m, "seconds", 1.0, 1.0)  # the region moved on: none is it
    assert lab.selected_chop(m) == ""
    assert labchops.select(m, "gone") is None


def test_snap_goes_to_a_beat_line_within_1_percent_of_what_is_shown():
    m = _song()  # 16 s on screen: 1% is 0.16 s
    assert labchops.snap(m, 4.1) == 4.0 and labchops.snap(m, 3.9) == 4.0
    assert labchops.snap(m, 4.2) == 4.2  # too far from a line
    assert labchops.snap(m, 4.4) == 4.5  # the nearest line is a beat, not a bar
    assert labchops.snap(m, 4.1, free=True) == 4.1  # Alt held
    m["view"] = {"start": 4.0, "length": 2.0}  # zoomed x8: 1% is 0.02 s
    assert labchops.snap(m, 4.1) == 4.1 and labchops.snap(m, 4.015) == 4.0
    bare = sources.clean_meta({"seconds": 16.0}, SID)  # no tempo: nothing to snap to
    assert labchops.snap(bare, 4.1) == 4.1


def test_auto_replaces_only_the_chops_wholly_inside_the_region():
    m = _song()
    outside = labchops.add(m, 0.0, 1.0)
    straddles = labchops.add(m, 3.0, 4.5)  # half in the region: it stays
    inside = labchops.add(m, 5.0, 6.0)
    lab.set_region(m, "bars", 2, 2)  # 4 .. 8
    made, left = labchops.auto(m, "bars", labchops.grid_spans(m, "bars"))
    assert (made, left) == (2, 0)
    ids = {c["id"] for c in m["chops"]}
    assert outside["id"] in ids and straddles["id"] in ids and inside["id"] not in ids
    spans = [(c["start"], c["end"], c["name"]) for c in m["chops"]]
    assert spans == [
        (0.0, 1.0, "chop 1"),
        (3.0, 4.5, "chop 2"),
        (4.5, 6.0, "bar 3"),  # cut back where the chop that stays ends
        (6.0, 8.0, "bar 4"),
    ]


def test_auto_past_the_cap_keeps_the_first_ones_and_counts_the_rest():
    m = _song(600.0)
    lab.set_region(m, "seconds", 0.0, 100.0)
    spans = [(i * 1.0, i * 1.0 + 1.0) for i in range(100)]
    made, left = labchops.auto(m, "transients", spans)
    assert (made, left) == (limits.MAX_LAB_CHOPS, 100 - limits.MAX_LAB_CHOPS)
    assert m["chops"][0]["name"] == "hit 1" and m["chops"][-1]["end"] == 64.0


# --- a meta anyone can edit --------------------------------------------------


def test_a_meta_from_before_chops_loads_as_it_was():
    old = {
        "name": "song",
        "seconds": 16.0,
        "region": {"mode": "bars", "start": 2, "length": 2},
    }
    m = sources.clean_meta(old, SID)
    assert m["chops"] == [] and m["chop"] == ""
    assert m["region"] == {"mode": "bars", "start": 2.0, "length": 2.0}


GOOD = {"id": "ab12", "name": "v", "start": 1.0, "end": 2.0}


@pytest.mark.parametrize(
    ("chops", "kept"),
    [
        ("nope", 0),
        ({"id": "ab12"}, 0),
        ([None, 7, "x", [], {}], 0),
        ([{**GOOD, "id": 7}, {**GOOD, "id": "../x"}, {**GOOD, "id": "XYZW"}], 0),
        ([{**GOOD, "start": "1"}, {**GOOD, "end": None}, {**GOOD, "start": True}], 0),
        ([{**GOOD, "start": float("nan")}, {**GOOD, "end": 10**400}], 0),
        (
            [{**GOOD, "start": 2.0, "end": 1.0}, {**GOOD, "end": 1.01}],
            0,
        ),  # backwards, tiny
        ([{**GOOD, "start": 50.0, "end": 60.0}], 0),  # past the song's end
        ([{**GOOD, "start": -5.0, "end": 99.0}], 1),  # clamped into it
        ([GOOD, {**GOOD, "start": 5.0, "end": 6.0}], 1),  # one id, twice
        ([GOOD, {**GOOD, "id": "ab13", "start": 1.5, "end": 3.0}], 1),  # overlapping
        (
            [
                {**GOOD, "id": f"{i:04x}", "start": i * 0.1, "end": i * 0.1 + 0.1}
                for i in range(1000)
            ],
            64,
        ),
    ],
)
def test_hostile_chops_are_dropped_or_clamped_never_a_500(
    loaded, tmp_path, chops, kept
):
    path = tmp_path / "sources" / source_id(loaded) / "meta.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(
        json.dumps({**raw, "chops": chops, "chop": "ab12"}), encoding="utf-8"
    )
    assert loaded.get("/view/lab").status_code == 200
    got = _chops(loaded)
    assert len(got) == kept <= limits.MAX_LAB_CHOPS
    total = _meta(loaded)["seconds"]
    for c in got:
        assert 0.0 <= c["start"] < c["end"] <= total
        assert c["end"] - c["start"] >= limits.MIN_CHOP_SECONDS - 1e-6
    ends = [c["end"] for c in got]
    assert all(c["start"] >= e - 1e-6 for c, e in zip(got[1:], ends, strict=False))
    assert _meta(loaded)["chop"] in {"", *(c["id"] for c in got)}
    assert loaded.get("/fragment/lab", headers=LAB).status_code == 200


def test_a_chops_own_fields_are_each_held_to_type(loaded, tmp_path):
    path = tmp_path / "sources" / source_id(loaded) / "meta.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    hostile = {
        **GOOD,
        "name": {"x": 1},
        "stems": ["vocals", "../x", 7],
        "model": "../../etc",
        "levels": {"vocals": "loud", "nope": 3},
        "clean": {"gate_db": "x", "fit": ["a"], "fade_in": -9},
        "notes": ["n"] * 9,
        "clip": "../../secrets",
    }
    named = {**GOOD, "id": "ab13", "start": 3.0, "end": 4.0, "name": "<b>x</b>" * 40}
    named |= {"stems": ["vocals", "nope"], "model": stems.HTDEMUCS.id}
    named |= {"levels": {"vocals": 99, "bass": 3}, "clip": "ok name"}
    path.write_text(json.dumps({**raw, "chops": [hostile, named]}), encoding="utf-8")
    page = loaded.get("/view/lab")
    assert page.status_code == 200 and "<b>x</b>" not in page.text
    a, b = _chops(loaded)
    assert a["name"] == "chop" and a["stems"] == [] and a["model"] == ""
    assert a["levels"] == {} and a["clip"] == "" and a["notes"] == ""
    assert a["clean"] == {**labclean.DEFAULTS, "fade_in": 0.0}
    assert len(b["name"]) == limits.MAX_CHOP_NAME_CHARS
    # a stem its set does not make is dropped; a level is clamped; a level
    # of a stem the chop does not play is dropped
    assert b["stems"] == ["vocals"] and b["model"] == stems.HTDEMUCS.id
    assert (
        b["levels"] == {"vocals": limits.LAB_LEVEL_DB_RANGE[1]}
        and b["clip"] == "ok name"
    )
    # its stem set is not on disk: the row says so, and it cannot be played
    assert "needs the 4-stem separation" in page.text
    assert loaded.get(f"/lab/{source_id(loaded)}/chop/ab13.wav").status_code == 404
    assert "needs the 4-stem" in _toast(_post(loaded, "/lab/chop/keep", chop="ab13"))
    assert list_clips(loaded.app.state.svc.data_dir) == []


# --- the routes --------------------------------------------------------------


def test_a_double_click_cuts_a_chop_and_the_page_draws_it_with_its_markers(loaded):
    lab_before = _meta(loaded)["region"]
    r = _chop(loaded, op="split", at="4.05")  # 1% of 16 s from the bar line: snaps
    assert r.status_code == 200
    (c,) = _chops(loaded)
    assert (c["start"], c["end"]) == (4.0, 8.0)  # to the default region's end
    assert _meta(loaded)["region"] == lab_before  # cutting is not selecting
    page = r.text
    assert f'id="chop-{c["id"]}"' in page and 'class="lw-chop"' in page
    # one lane (the mix) and the overview strip, one span each
    assert page.count('class="lw-chop"') == 2
    for edge in ("start", "end"):
        assert f'id="chop-mark-{c["id"]}-{edge}"' in page
    # two edges; the third slider is the audition's seek bar (the shared .player)
    assert page.count('role="slider"') == 3 and 'tabindex="0"' in page
    assert page.count('class="player-bar" role="slider"') == 1
    assert f"/lab/{source_id(loaded)}/chop/{c['id']}.wav?v=" in page
    assert "0:04.0 · 4.0 s · 2 bars" in page and ">Keep</button>" in page
    _chop(loaded, op="split", at="5.3", free="1")  # Alt: where it was dropped
    assert _spans(loaded) == [(4.0, 5.3), (5.3, 8.0)]


def test_the_selection_is_cut_as_a_chop_named_for_it(loaded):
    """✂ Chop on the selection bar (`op=add`): the region as a chop, named
    for what it plays and where, marked (the region is that chop), with the
    lanes' mix; refused when it starts inside a chop."""
    _post(loaded, "/lab/region", mode="bars", bar="3", bars="2")  # 4..8 s
    r = _chop(loaded, op="add")
    (c,) = _chops(loaded)
    assert (c["start"], c["end"], c["name"]) == (4.0, 8.0, "mix bars 3-4")
    assert lab.selected_chop(_meta(loaded)) == c["id"]
    assert "Chop “mix bars 3-4” cut" in r.text and 'aria-current="true"' in r.text
    r = _chop(loaded, op="add")  # the same span again: it is in the list already
    assert len(_chops(loaded)) == 1 and "inside a chop" in _toast(r)
    # one that runs into a chop is cut back at it
    _post(loaded, "/lab/region", mode="bars", bar="2", bars="4")  # 2..10 s
    _chop(loaded, op="add")
    assert _spans(loaded) == [(2.0, 4.0), (4.0, 8.0)]


def test_a_marker_moves_its_edge_snapped_by_the_server_unless_free(loaded):
    _chop(loaded, op="split", at="4")
    _chop(loaded, op="split", at="6")
    a, b = _chops(loaded)
    _chop(loaded, op="move", chop=b["id"], edge="start", at="5.07")
    assert _spans(loaded) == [(4.0, 5.0), (5.0, 8.0)]  # snapped; a's end followed
    _chop(loaded, op="move", chop=b["id"], edge="start", at="5.07", free="1")
    assert _spans(loaded) == [(4.0, 5.07), (5.07, 8.0)]
    _chop(loaded, op="move", chop=a["id"], edge="start", at="-3")
    assert _spans(loaded)[0][0] == 0.0
    # the position is seconds of the song, whatever the lanes show
    _post(loaded, "/lab/region", mode="bars", bar="3", bars="1")
    _post(loaded, "/lab/view", action="in")
    shown = lab.view(_meta(loaded))[0]
    assert shown > 0
    _chop(loaded, op="move", chop=b["id"], edge="end", at="7.3", free="1")
    assert _spans(loaded)[1] == (5.07, 7.3)


def test_moving_the_selected_chops_edge_keeps_it_the_region(loaded):
    _chop(loaded, op="split", at="4")
    (c,) = _chops(loaded)
    assert _chop(loaded, op="select", chop=c["id"]).status_code == 200
    assert lab.span(_meta(loaded)) == pytest.approx((4.0, 4.0))
    page = _chop(loaded, op="move", chop=c["id"], edge="end", at="6.3", free="1").text
    assert lab.span(_meta(loaded)) == pytest.approx((4.0, 2.3))
    assert lab.selected_chop(_meta(loaded)) == c["id"]
    assert 'class="chop-row on"' in page and 'class="lw-chop lw-chop-on"' in page


def test_rename_remove_and_the_cleaning_are_one_post_each(loaded):
    _chop(loaded, op="split", at="4")
    (c,) = _chops(loaded)
    _chop(loaded, op="rename", chop=c["id"], name="verse <1>")
    assert _chops(loaded)[0]["name"] == "verse <1>"
    r = _chop(
        loaded, op="clean", chop=c["id"], trim_db="-30", fade_in="20", level="normalise"
    )
    (c,) = _chops(loaded)
    assert c["clean"] == {
        **labclean.DEFAULTS,
        "trim_db": -30.0,
        "fade_in": 20.0,
        "level": "normalise",
    }
    # what the cleaning did is on the row, in numbers
    assert c["notes"].startswith("trimmed ") and "normalised" in c["notes"]
    assert c["notes"] in r.text.replace("&#43;", "+")
    assert "verse &lt;1&gt;" in r.text and "verse <1>" not in r.text
    assert _chop(loaded, op="remove", chop=c["id"]).status_code == 200
    assert _chops(loaded) == []


@pytest.mark.parametrize(
    "data",
    [
        {"op": "explode"},
        {"op": "auto", "rule": "vibes"},
        {"op": ""},
    ],
)
def test_an_unknown_op_or_rule_is_refused(loaded, data):
    assert _chop(loaded, **data).status_code == 422
    assert _chops(loaded) == []


@pytest.mark.parametrize("at", ["x", "", "nan", "1e400", "-1e99"])
def test_a_position_that_is_not_one_changes_nothing_harmful(loaded, at):
    r = _chop(loaded, op="split", at=at)
    assert r.status_code in (200, 422)
    for c in _chops(loaded):
        assert 0.0 <= c["start"] < c["end"] <= 16.0


def test_a_missing_song_or_chop_is_a_toast(client, loaded):
    gone = {"source": "f" * 16}
    for path, data in (
        ("/lab/chop", {"op": "split", "at": "1"}),
        ("/lab/chop", {"op": "auto", "rule": "bars"}),
        ("/lab/chop/keep", {"chop": "ab12"}),
    ):
        r = client.post(path, data={**gone, **data}, headers=LAB)
        assert "gone" in _toast(r)
    for op in ("move", "remove", "rename", "select", "lanes", "clean"):
        assert "That chop is gone" in _toast(
            _chop(loaded, op=op, chop="ab12", at="1", edge="start")
        )
    assert "That chop is gone" in _toast(_post(loaded, "/lab/chop/keep", chop="ab12"))
    sid = source_id(loaded)
    assert loaded.get(f"/lab/{sid}/chop/ab12.wav").status_code == 404
    assert loaded.get(f"/lab/{'f' * 16}/chop/ab12.wav").status_code == 404
    assert loaded.get("/lab/..%2F..%2Fx/chop/ab12.wav").status_code == 404


# --- auto --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "step", "count"), [("bars", BAR, 2), ("beats", BEAT, 8)]
)
def test_auto_by_bars_and_beats_lands_on_the_grid_lines(loaded, rule, step, count):
    _post(loaded, "/lab/region", mode="bars", bar="3", bars="2")  # 4 .. 8 s
    r = _chop(loaded, op="auto", rule=rule)
    assert r.status_code == 200 and f"{count} chops cut by {rule}" in r.text
    spans = _spans(loaded)
    assert len(spans) == count
    edges = [s for s, _ in spans] + [spans[-1][1]]
    assert np.allclose(edges, 4.0 + step * np.arange(count + 1), atol=1e-4)
    names = [c["name"] for c in _chops(loaded)]
    assert names[0] == ("bar 3" if rule == "bars" else "beat 3.1")
    # again: the chops inside the region are replaced, not added to
    _chop(loaded, op="auto", rule=rule)
    assert _spans(loaded) == spans


def test_auto_by_bars_needs_a_tempo(client):
    t = np.arange(SR * 3) / SR
    tone = np.column_stack([np.sin(2 * np.pi * 220 * t)] * 2) * 0.5
    upload(client, wav_bytes(tone), "tone.wav")
    _no_tempo(client)
    assert not lab.grid(_meta(client))["set"]
    assert "Set a tempo first" in _toast(_chop(client, op="auto", rule="bars"))
    page = client.get("/view/lab").text
    assert 'id="chop-auto-bars"' in page
    button = page.split('id="chop-auto-bars"')[1].split("</button>")[0]
    assert "disabled" in button


def test_auto_by_transients_starts_a_chop_within_20_ms_of_each_rendered_hit(client):
    audio, _steps = drum_loop(ROWS, bpm=120)
    upload(client, wav_bytes(audio), "beat.wav")
    _grid(client)
    _post(client, "/lab/region", mode="seconds", start="0", length="4")
    r = _chop(client, op="auto", rule="transients")
    assert r.status_code == 200
    starts = np.array([c["start"] for c in _chops(client)])
    steps = sorted(
        {i for row in ROWS.values() for i, ch in enumerate(row) if ch == "x"}
    )
    hits = np.array(steps) * (BAR / 16)
    assert len(starts) >= len(hits) - 2
    for s in starts:
        assert np.min(np.abs(hits - s)) < 0.02, s
    found = sum(np.min(np.abs(starts - h)) < 0.02 for h in hits)
    assert found >= len(hits) - 2  # nearly every hit starts a chop
    spans = _spans(client)
    assert all(
        b == pytest.approx(c, abs=1e-4)
        for (_, b), (c, _) in zip(spans, spans[1:], strict=False)
    )
    assert _chops(client)[0]["name"].startswith("hit ")


def test_auto_by_syllables_reads_the_vocals_stem_and_only_when_it_plays_alone(
    client, monkeypatch
):
    line = np.concatenate([voice_line(), np.zeros((SR // 2, 2))])
    upload(client, wav_bytes(line * 0.8), "voice.wav")
    _post(client, "/lab/region", mode="seconds", start="0", length=f"{len(line) / SR}")
    assert 'id="chop-auto-syllables"' not in client.get("/view/lab").text
    assert "vocals stem alone" in _toast(_chop(client, op="auto", rule="syllables"))
    separate(client, monkeypatch, bank=OneStem("vocals"))
    _post(client, "/lab/region", **{"stems": "1", "stem.vocals": "on"})
    assert 'id="chop-auto-syllables"' in client.get("/view/lab").text
    r = _chop(client, op="auto", rule="syllables")
    assert r.status_code == 200
    chops = _chops(client)
    assert len(chops) == 3  # the line's three sung notes
    assert [c["stems"] for c in chops] == [["vocals"]] * 3
    assert chops[0]["name"] == "syllable 1"
    # each is a note of the line: 0.3 s, 1.5 s and 0.4 s long, in that order
    lengths = [c["end"] - c["start"] for c in chops]
    assert lengths[1] == max(lengths) and abs(lengths[1] - 1.5) < 0.25


def test_auto_that_finds_nothing_is_a_toast_and_keeps_the_chops(client):
    quiet = np.zeros((SR * 3, 2))
    quiet[: SR // 2] = 0.3 * np.sin(2 * np.pi * 220 * np.arange(SR // 2) / SR)[:, None]
    upload(client, wav_bytes(quiet), "quiet.wav")
    _post(client, "/lab/region", mode="seconds", start="1.5", length="1")
    _chop(client, op="split", at="1.7", free="1")
    assert "No transients found" in _toast(_chop(client, op="auto", rule="transients"))
    assert len(_chops(client)) == 1


# --- a chop keeps its own mix ------------------------------------------------


def _chop_wav(client, cid: str) -> np.ndarray:
    r = client.get(f"/lab/{source_id(client)}/chop/{cid}.wav")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    return read_pcm16(r.content)[0]


def _stem(
    client, name: str, start: float, length: float, model: str = ""
) -> np.ndarray:
    meta = _meta(client)
    meta = sources.with_model(meta, model) if model else meta
    data_dir = client.app.state.svc.data_dir
    return sources.read(data_dir, meta, name, int(start * SR), int(length * SR))


def _plain(client, cid: str) -> None:
    """No fades either: the chop's audio is then its stems' own samples."""
    _chop(client, op="clean", chop=cid, fade_in="0", fade_out="0")


def test_a_chop_plays_its_own_stems_at_its_own_levels_not_the_lanes(
    loaded, monkeypatch
):
    separate(loaded, monkeypatch)
    _post(
        loaded, "/lab/region", **{"stems": "1", "stem.bass": "on", "level.bass": "-6"}
    )
    _chop(loaded, op="split", at="4")  # 4 .. 8 s, made with the lanes as they are
    (c,) = _chops(loaded)
    assert (c["stems"], c["levels"]) == (["bass"], {"bass": -6.0})
    assert c["model"] == stems.HTDEMUCS.id
    _plain(loaded, c["id"])
    want = _stem(loaded, "bass", 4.0, 4.0) * 10 ** (-6 / 20)
    assert np.allclose(_chop_wav(loaded, c["id"]), want, atol=2e-4)
    # the lanes move on: the chop does not follow
    _post(
        loaded, "/lab/region", **{"stems": "1", "stem.drums": "on", "level.drums": "3"}
    )
    assert _meta(loaded)["picked"] == ["drums"]
    assert np.allclose(_chop_wav(loaded, c["id"]), want, atol=2e-4)
    page = loaded.get("/view/lab").text
    assert "bass (bass −6 dB)" in page
    # ... until "use the lanes' mix" copies them onto it
    assert _chop(loaded, op="lanes", chop=c["id"]).status_code == 200
    (c,) = _chops(loaded)
    assert (c["stems"], c["levels"]) == (["drums"], {"drums": 3.0})
    # and selecting it sets the lanes from the chop
    _post(loaded, "/lab/region", **{"stems": "1", "stem.vocals": "on"})
    _chop(loaded, op="select", chop=c["id"])
    meta = _meta(loaded)
    assert meta["picked"] == ["drums"] and sources.levels(meta)["drums"] == 3.0


def test_a_chop_with_no_stems_is_the_recording_itself(loaded, monkeypatch):
    _chop(loaded, op="split", at="4")
    (c,) = _chops(loaded)
    _plain(loaded, c["id"])
    separate(loaded, monkeypatch)
    _post(loaded, "/lab/region", **{"stems": "1", "stem.bass": "on"})
    mix = _stem(loaded, sources.MIX, 4.0, 4.0)
    assert np.allclose(_chop_wav(loaded, c["id"]), mix, atol=2e-4)


def test_a_chop_of_the_other_stem_set_still_plays_while_that_set_is_on_disk(
    loaded, monkeypatch, tmp_path
):
    six = stems.HTDEMUCS_6S
    sid = separate(loaded, monkeypatch)
    fake_separation(loaded, monkeypatch, SixBands())
    _post(loaded, "/lab/separate", model=six.id)
    loaded.app.state.svc.lab.join()
    _post(loaded, "/lab/region", **{"stems": "1", "stem.guitar": "on"})
    _chop(loaded, op="split", at="4")
    (c,) = _chops(loaded)
    assert (c["stems"], c["model"]) == (["guitar"], six.id)
    _plain(loaded, c["id"])
    want = _stem(loaded, "guitar", 4.0, 4.0)
    # back to the 4-stem set: the chop is of the other one, and says so
    _post(loaded, "/lab/separate", model=stems.HTDEMUCS.id)
    assert _meta(loaded)["stems"]["model"] == stems.HTDEMUCS.id
    assert np.allclose(_chop_wav(loaded, c["id"]), want, atol=2e-4)
    page = loaded.get("/view/lab").text
    assert six.label in page.split(f'id="chop-{c["id"]}"')[1]
    # selecting it brings its own set back up, with its stem ticked
    _chop(loaded, op="select", chop=c["id"])
    meta = _meta(loaded)
    assert meta["stems"]["model"] == six.id and meta["picked"] == ["guitar"]
    # the set gone from disk: never played from the wrong one
    _post(loaded, "/lab/separate", model=stems.HTDEMUCS.id)
    import shutil

    shutil.rmtree(tmp_path / "sources" / sid / f"stems-{six.id}")
    assert loaded.get(f"/lab/{sid}/chop/{c['id']}.wav").status_code == 404
    page = loaded.get("/view/lab").text
    row = page.split(f'id="chop-{c["id"]}"')[1].split("</li>")[0]
    assert "needs the 6-stem separation" in row
    assert row.count("disabled") >= 2  # its ▶ and its Keep
    assert "needs the 6-stem separation" in _toast(
        _post(loaded, "/lab/chop/keep", chop=c["id"])
    )


# --- keep and update ---------------------------------------------------------


def _keep(client, cid: str, **data):
    return _post(client, "/lab/chop/keep", chop=cid, **data)


def test_keep_bakes_the_chop_into_the_clip_store_with_where_it_came_from(
    loaded, monkeypatch
):
    separate(loaded, monkeypatch)
    _post(
        loaded,
        "/lab/region",
        **{"stems": "1", "stem.vocals": "on", "level.vocals": "-3"},
    )
    _chop(loaded, op="split", at="4")
    (c,) = _chops(loaded)
    _chop(loaded, op="rename", chop=c["id"], name="verse 1")
    r = _keep(loaded, c["id"])
    assert r.status_code == 200 and r.headers["X-Clip-Name"] == "verse 1"
    assert (
        "Kept as clip" in json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    )
    data_dir = loaded.app.state.svc.data_dir
    (clip,) = list_clips(data_dir)
    assert clip["name"] == "verse 1" and clip["source"] == "lab: my song"
    assert clip["bpm"] == 120.0 and clip["bars"] == 2  # 4 s on the grid
    assert clip["lab"] == {
        "source": source_id(loaded),
        "chop": c["id"],
        "start": 4.0,
        "seconds": 4.0,
        "stems": ["vocals"],
        "model": stems.HTDEMUCS.id,
        "levels": {"vocals": -3.0},
        "source_bpm": 120.0,
        "fitted": False,
        "key": lab.key_in_force(_meta(loaded)),
        "clean": labclean.DEFAULTS,
    }
    # the audio: the chop's own mix, with the default 5 ms fades
    audio, _sr = load_clip(data_dir, "verse 1")
    want = _stem(loaded, "vocals", 4.0, 4.0) * 10 ** (-3 / 20)
    k = int(0.005 * SR)
    assert (
        np.allclose(audio[k:-k], want[k:-k], atol=2e-4) and abs(audio[0]).max() < 1e-4
    )
    # the chop remembers its clip, and the row offers Update
    assert _chops(loaded)[0]["clip"] == "verse 1"
    assert ">Update clip</button>" in r.text and "Keep as new" in r.text
    # kept again without `update`: another clip, never over the first
    again = _keep(loaded, c["id"])
    assert again.headers["X-Clip-Name"] == "verse 1 2"
    assert {x["name"] for x in list_clips(data_dir)} == {"verse 1", "verse 1 2"}


def test_a_chop_off_the_grid_or_with_no_tempo_says_no_bars(client):
    t = np.arange(SR * 3) / SR
    upload(
        client,
        wav_bytes(np.column_stack([np.sin(2 * np.pi * 220 * t)] * 2) * 0.5),
        "t.wav",
    )
    _no_tempo(client)
    _chop(client, op="split", at="0.5", free="1")
    (c,) = _chops(client)
    assert _keep(client, c["id"]).status_code == 200
    (clip,) = list_clips(client.app.state.svc.data_dir)
    assert "bpm" not in clip and "bars" not in clip and clip["lab"]["source_bpm"] == 0.0
    assert clip["lab"]["stems"] == [] and clip["lab"]["model"] == ""
    assert clip["lab"]["levels"] == {}


def test_update_writes_over_the_chops_own_clip_and_forgets_every_render(
    loaded, monkeypatch
):
    svc = loaded.app.state.svc
    _chop(loaded, op="split", at="4")
    (c,) = _chops(loaded)
    assert _keep(loaded, c["id"]).headers["X-Clip-Name"] == "chop 1"
    before = clip_hash(svc.data_dir, "chop 1")
    resets = []
    monkeypatch.setattr(StudioService, "reset_caches", lambda self: resets.append(1))
    # the chop is cut again and cleaned; Update makes its clip that
    _chop(loaded, op="move", chop=c["id"], edge="end", at="6", free="1")
    _chop(loaded, op="clean", chop=c["id"], level="gain", gain_db="-6")
    r = _keep(loaded, c["id"], update="1")
    assert r.status_code == 200 and r.headers["X-Clip-Name"] == "chop 1"
    assert (
        "Updated clip" in json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    )
    assert [x["name"] for x in list_clips(svc.data_dir)] == ["chop 1"]  # no second clip
    assert clip_hash(svc.data_dir, "chop 1") != before  # so every render re-renders
    assert resets == [1]
    (clip,) = list_clips(svc.data_dir)
    assert clip["seconds"] == 2.0 and clip["lab"]["clean"]["gain_db"] == -6.0
    assert clip["bars"] == 1
    # a plain keep forgets nothing
    _keep(loaded, c["id"])
    assert resets == [1]


def test_update_names_how_many_places_use_the_clip(loaded):
    from dataclasses import replace

    s = loaded.app.state.svc.state
    _chop(loaded, op="split", at="4")
    (c,) = _chops(loaded)
    _keep(loaded, c["id"])
    page = loaded.get("/view/lab").text
    assert "it is used: nowhere in this session yet." in page
    s.arrangement = replace(s.arrangement, drum_samples={"kick": "chop 1"})
    page = loaded.get("/view/lab").text
    # `StudioState.clip_usage`, in the library's words (test_lab_library.py
    # holds it to the drawer's delete, the song's undo steps included)
    assert "Write over the clip “chop 1”" in page and "it is used: 1 drum row." in page
    assert "· used: 1 drum row" in page


def test_update_never_writes_over_a_clip_that_is_not_this_chops(loaded):
    """The chop's `clip` must still name a clip that says it is this chop's:
    one deleted, replaced by another of that name, or named by hand in the
    meta is not it — `update=1` then keeps a new clip instead."""
    from synthlab.studio.clips import delete_clip, save_clip

    svc = loaded.app.state.svc
    _chop(loaded, op="split", at="4")
    _chop(loaded, op="split", at="6")
    a, b = _chops(loaded)
    _keep(loaded, a["id"])  # "chop 1"
    # someone else's clip under the name a chop points at
    other = np.full((SR, 2), 0.25)
    save_clip(svc.data_dir, "precious", other, meta={"source": "jam"})
    sources.update_meta(
        svc.data_dir, source_id(loaded), lambda m: m["chops"][1].update(clip="precious")
    )
    was = clip_hash(svc.data_dir, "precious")
    page = loaded.get("/view/lab").text
    assert "Write over the clip “precious”" not in page  # Update is not offered
    r = _keep(loaded, b["id"], update="1")
    assert r.headers["X-Clip-Name"] == "chop 2"  # a new clip
    assert clip_hash(svc.data_dir, "precious") == was
    # another chop's clip is not this chop's either
    sources.update_meta(
        svc.data_dir, source_id(loaded), lambda m: m["chops"][1].update(clip="chop 1")
    )
    first = clip_hash(svc.data_dir, "chop 1")
    assert _keep(loaded, b["id"], update="1").headers["X-Clip-Name"] == "chop 2 2"
    assert clip_hash(svc.data_dir, "chop 1") == first
    # its own clip deleted: Update is gone, and `update=1` keeps anew
    delete_clip(svc.data_dir, "chop 1")
    assert (
        ">Update clip</button>"
        not in loaded.get("/view/lab")
        .text.split(f'id="chop-{a["id"]}"')[1]
        .split("</li>")[0]
    )
    assert _keep(loaded, a["id"], update="1").headers["X-Clip-Name"] == "chop 1"


def test_a_chop_over_30_s_cannot_be_kept_and_its_row_offers_the_stem(client):
    long = np.tile(np.sin(2 * np.pi * 220 * np.arange(SR) / SR) * 0.4, 40)
    upload(client, wav_bytes(np.column_stack([long, long])), "long.wav")
    _post(client, "/lab/region", mode="seconds", start="0", length="36")
    _chop(client, op="split", at="1", free="1")
    (c,) = _chops(client)
    assert c["end"] - c["start"] == pytest.approx(35.0)
    text = _toast(_keep(client, c["id"]))
    assert "35.0 s" in text and "30 s" in text and "download the stem" in text
    assert list_clips(client.app.state.svc.data_dir) == []
    row = (
        client.get("/view/lab").text.split(f'id="chop-{c["id"]}"')[1].split("</li>")[0]
    )
    assert "a clip holds 30 s" in row and ">Keep</button>" not in row
    # the audition still plays: its first 30 s
    assert len(_chop_wav(client, c["id"])) == 30 * SR


def test_a_chop_longer_than_30_s_once_fitted_is_refused_with_nothing_written(loaded):
    """16 s at 120 bpm fitted to a 60 bpm loop is 32 s."""
    svc = loaded.app.state.svc
    svc.state.arrangement.bpm = 60.0
    _post(loaded, "/lab/region", mode="bars", bar="1", bars="8")
    _chop(loaded, op="split", at="0")
    (c,) = _chops(loaded)
    _chop(loaded, op="clean", chop=c["id"], fit="session")
    text = _toast(_keep(loaded, c["id"]))
    assert "Fitted to 60 bpm" in text and "32.0 s" in text
    assert list_clips(svc.data_dir) == [] and _chops(loaded)[0]["clip"] == ""


def test_a_chop_that_is_silent_is_refused_by_name(client):
    audio = np.concatenate([np.zeros((SR * 2, 2)), loop(120, "A minor", 2)])
    upload(client, wav_bytes(audio * 0.8), "late.wav")
    _post(client, "/lab/region", mode="seconds", start="0", length="1.5")
    _chop(client, op="split", at="0.2", free="1")
    (c,) = _chops(client)
    assert "silent after cleaning" in _toast(_keep(client, c["id"]))
    assert list_clips(client.app.state.svc.data_dir) == []


def test_fit_makes_the_clip_the_loops_or_the_songs_bars(loaded):
    """Two bars at 120 bpm fitted to the loop's 100 bpm are two bars at
    100; fitted to the song's 90 they are two bars at 90."""
    svc = loaded.app.state.svc
    svc.state.arrangement.bpm = 100.0
    svc.state.song["bpm"] = 90.0
    _post(loaded, "/lab/region", mode="bars", bar="3", bars="2")
    _chop(loaded, op="split", at="4")
    (c,) = _chops(loaded)
    r = _chop(loaded, op="clean", chop=c["id"], fit="session")
    assert "stretched 120 → 100 bpm · held to 2 bars" in r.text
    _keep(loaded, c["id"])
    _chop(loaded, op="clean", chop=c["id"], fit="song")
    _keep(loaded, c["id"])
    by_name = {x["name"]: x for x in list_clips(svc.data_dir)}
    for name, bpm in (("chop 1", 100.0), ("chop 1 2", 90.0)):
        clip = by_name[name]
        assert clip["bpm"] == bpm and clip["bars"] == 2 and clip["lab"]["fitted"]
        assert clip["lab"]["source_bpm"] == 120.0
        audio, _sr = load_clip(svc.data_dir, name)
        assert len(audio) == int(round(2 * 240.0 / bpm * SR))
    # the tempo it is fitted to is part of the audition's address
    page = loaded.get("/view/lab").text
    # the chop's own audition: the clip library (on every page) auditions
    # the kept clips with the same attribute, earlier in the markup
    url = page.split('data-note="/lab/')[1].split('"')[0]
    svc.state.song["bpm"] = 80.0
    assert url not in loaded.get("/view/lab").text


def test_chops_and_a_kept_chop_touch_no_session_state_and_no_render_key(loaded):
    svc = loaded.app.state.svc
    before = (svc.render_inputs("perform").key(), json.dumps(svc.state._payload()))
    _chop(loaded, op="auto", rule="bars")
    a, b = _chops(loaded)[:2]
    _chop(loaded, op="move", chop=b["id"], edge="start", at="5.1", free="1")
    _chop(loaded, op="clean", chop=a["id"], trim_db="-40")
    _chop(loaded, op="select", chop=a["id"])
    _keep(loaded, a["id"])
    _keep(loaded, a["id"], update="1")
    assert len(list_clips(svc.data_dir)) == 1
    after = (svc.render_inputs("perform").key(), json.dumps(svc.state._payload()))
    assert before == after


# --- whole stems out ---------------------------------------------------------


def test_a_stem_downloads_as_the_stored_flac_and_as_a_wav_of_it(
    loaded, monkeypatch, tmp_path
):
    sid = separate(loaded, monkeypatch)
    folder = tmp_path / "sources" / sid
    stored = (folder / f"stems-{stems.HTDEMUCS.id}" / "vocals.flac").read_bytes()
    r = loaded.get(f"/lab/{sid}/stem/vocals.flac")
    assert r.status_code == 200 and r.content == stored
    assert r.headers["content-type"] == "audio/flac"
    assert (
        r.headers["content-disposition"]
        == 'attachment; filename="my song - vocals.flac"'
    )
    assert "gzip" not in r.headers.get("content-encoding", "")
    mix = loaded.get(f"/lab/{sid}/stem/mix.flac")
    assert mix.content == (folder / "mix.flac").read_bytes()
    assert 'filename="my song - mix.flac"' in mix.headers["content-disposition"]
    # the WAV is that file, decoded: sample for sample, the whole stem
    w = loaded.get(f"/lab/{sid}/stem/vocals.wav")
    assert w.status_code == 200 and w.headers["content-type"] == "audio/wav"
    assert 'filename="my song - vocals.wav"' in w.headers["content-disposition"]
    assert int(w.headers["content-length"]) == len(w.content)
    pcm, sr = soundfile.read(io.BytesIO(w.content), dtype="int16")
    want, _ = soundfile.read(io.BytesIO(stored), dtype="int16")
    assert sr == SR and np.array_equal(pcm, want) and len(pcm) == 16 * SR
    page = loaded.get("/view/lab").text
    assert f'href="/lab/{sid}/stem/vocals.flac"' in page
    assert (
        f'href="/lab/{sid}/stem/mix.wav"' in page
        and f'href="/lab/{sid}/stems.zip"' in page
    )


def test_the_wav_is_written_in_blocks_never_from_the_whole_stem(
    loaded, monkeypatch, tmp_path
):
    sid = separate(loaded, monkeypatch)
    path = sources.stem_path(tmp_path, sid, "drums")
    size, blocks = sources.wav_blocks(path)
    parts = list(blocks)
    assert len(parts[0]) == 44 and sum(map(len, parts)) == size
    assert len(parts) == 1 + -(
        -16 * SR // (1 << 16)
    )  # the header, then 64k frames each
    assert max(map(len, parts[1:])) == (1 << 16) * 4


def test_the_zip_holds_the_sets_stems_stored_under_the_songs_name(
    loaded, monkeypatch, tmp_path
):
    sid = separate(loaded, monkeypatch)
    r = loaded.get(f"/lab/{sid}/stems.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert (
        r.headers["content-disposition"] == 'attachment; filename="my song - stems.zip"'
    )
    assert int(r.headers["content-length"]) == len(r.content)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.namelist() == [f"my song - {s}.flac" for s in stems.HTDEMUCS.sources]
    folder = tmp_path / "sources" / sid / f"stems-{stems.HTDEMUCS.id}"
    for info in z.infolist():
        assert info.compress_type == zipfile.ZIP_STORED  # FLAC does not deflate
        stem = info.filename.split(" - ")[1]
        assert z.read(info) == (folder / stem).read_bytes()
    assert loaded.get(f"/lab/{sid}/stems.zip").content == r.content  # deterministic


def test_a_download_names_its_stem_set_and_defaults_to_the_one_shown(
    loaded, monkeypatch
):
    six, four = stems.HTDEMUCS_6S, stems.HTDEMUCS
    sid = separate(loaded, monkeypatch)
    fake_separation(loaded, monkeypatch, SixBands())
    _post(loaded, "/lab/separate", model=six.id)
    loaded.app.state.svc.lab.join()
    assert (
        loaded.get(f"/lab/{sid}/stem/guitar.flac").status_code == 200
    )  # the set shown
    assert loaded.get(f"/lab/{sid}/stem/guitar.flac?model={four.id}").status_code == 404
    a = loaded.get(f"/lab/{sid}/stem/vocals.flac?model={four.id}").content
    b = loaded.get(f"/lab/{sid}/stem/vocals.flac?model={six.id}").content
    assert a != b and b == loaded.get(f"/lab/{sid}/stem/vocals.flac").content
    names = zipfile.ZipFile(
        io.BytesIO(loaded.get(f"/lab/{sid}/stems.zip").content)
    ).namelist()
    assert len(names) == 6
    z4 = loaded.get(f"/lab/{sid}/stems.zip?model={four.id}")
    assert len(zipfile.ZipFile(io.BytesIO(z4.content)).namelist()) == 4
    # what the library's Stems group lists
    (row,) = sources.stem_sets(loaded.app.state.svc.data_dir)
    assert row["id"] == sid and row["name"] == "my song" and row["model"] == six.id
    assert row["sets"] == {four.id: four.sources, six.id: six.sources}
    assert sources.models_on_disk(loaded.app.state.svc.data_dir, sid) == (
        four.id,
        six.id,
    )


@pytest.mark.parametrize(
    "path",
    [
        "/lab/{sid}/stem/nope.flac",
        "/lab/{sid}/stem/nope.wav",
        "/lab/{sid}/stem/..%2Fmeta.flac",
        "/lab/{sid}/stem/vocals.flac?model=nope",
        "/lab/{sid}/stem/vocals.flac?model=..%2F..%2Fx",
        "/lab/{sid}/stem/vocals.wav?model=" + "x" * 5000,
        "/lab/{sid}/stems.zip?model=nope",
        "/lab/ffffffffffffffff/stem/mix.flac",
        "/lab/ffffffffffffffff/stem/vocals.wav",
        "/lab/ffffffffffffffff/stems.zip",
        "/lab/..%2F..%2Fx/stem/mix.flac",
        "/lab/nope/stems.zip",
    ],
)
def test_a_bad_id_stem_or_model_is_a_404(loaded, monkeypatch, path):
    sid = separate(loaded, monkeypatch)
    assert loaded.get(path.format(sid=sid)).status_code == 404


def test_a_song_with_no_stems_downloads_its_mix_and_nothing_else(loaded):
    sid = source_id(loaded)
    assert loaded.get(f"/lab/{sid}/stem/mix.flac").status_code == 200
    assert loaded.get(f"/lab/{sid}/stem/mix.wav").status_code == 200
    assert loaded.get(f"/lab/{sid}/stem/vocals.flac").status_code == 404
    assert loaded.get(f"/lab/{sid}/stems.zip").status_code == 404
    assert "stems.zip" not in loaded.get("/view/lab").text
    assert sources.stem_sets(loaded.app.state.svc.data_dir) == []


def test_downloads_and_the_chop_audition_are_refused_to_another_site(loaded):
    sid = source_id(loaded)
    _chop(loaded, op="split", at="4")
    cid = _chops(loaded)[0]["id"]
    for path in (f"/lab/{sid}/stem/mix.flac", f"/lab/{sid}/chop/{cid}.wav"):
        r = loaded.get(path, headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403


# --- "Open in Lab" -----------------------------------------------------------


def test_open_in_lab_opens_the_source_with_the_chop_selected(client, wav):
    upload(client, wav)
    _grid(client)
    first = source_id(client)
    _chop(client, op="split", at="9.1", free="1")
    (c,) = _chops(client)
    t = np.arange(SR * 3) / SR
    upload(
        client,
        wav_bytes(np.column_stack([np.sin(2 * np.pi * 330 * t)] * 2) * 0.5),
        "b.wav",
    )
    assert source_id(client) != first  # the newer song is the one the Lab opens on
    page = client.get(f"/view/lab?source={first}&chop={c['id']}")
    assert page.status_code == 200 and "lab-notice" not in page.text
    assert source_id(client) == first
    assert 'class="chop-row on"' in page.text
    meta = sources.read_meta(client.app.state.svc.data_dir, first)
    assert lab.selected_chop(meta) == c["id"]
    assert lab.span(meta) == pytest.approx((9.1, c["end"] - 9.1))
    # as an htmx view swap too
    assert (
        client.get(f"/view/lab?source={first}&chop={c['id']}", headers=LAB).status_code
        == 200
    )


@pytest.mark.parametrize(
    ("query", "says"),
    [
        ("source=ffffffffffffffff&chop=ab12", "no longer in the Lab"),
        ("source=..%2F..%2Fx", "no longer in the Lab"),
        ("source=&chop=", "no longer in the Lab"),
        ("source={sid}&chop=ffff", "no longer in the song"),
        ("source={sid}&chop=" + "x" * 3000, "no longer in the song"),
    ],
)
def test_open_in_lab_on_something_gone_opens_the_lab_and_says_so(loaded, query, says):
    sid = source_id(loaded)
    full = loaded.get("/view/lab?" + query.format(sid=sid))
    assert full.status_code == 200 and says in full.text
    assert 'id="lab-panel"' in full.text and "my song" in full.text  # the Lab, as it is
    swap = loaded.get("/view/lab?" + query.format(sid=sid), headers=LAB)
    assert swap.status_code == 200
    assert says in json.loads(swap.headers["HX-Trigger"])["studio-toast"]["message"]


def test_open_in_lab_brings_a_chop_into_sight_when_the_waveform_is_zoomed_elsewhere(
    loaded,
):
    _chop(loaded, op="split", at="12.2", free="1")
    (c,) = _chops(loaded)
    _post(loaded, "/lab/region", mode="bars", bar="1", bars="1")
    for _ in range(3):
        _post(loaded, "/lab/view", action="in")
    assert sum(lab.view(_meta(loaded))) < 12.0  # the chop is off screen
    loaded.get(f"/view/lab?source={source_id(loaded)}&chop={c['id']}")
    shown = lab.view(_meta(loaded))
    assert shown[0] <= c["start"] and c["end"] <= sum(shown)
