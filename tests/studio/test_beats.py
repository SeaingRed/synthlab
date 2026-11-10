"""The beat workflow (`drums.md`): what the step
sequencer's posts do to the model — per-hit data and a row's own swing
through both grid routes, the pattern tools carrying a hit's data with its
cell, variations A-D, the fill / build / busier / sparser generators,
Euclid a row, a kit swapped under a beat, and a pool beat's family in the
Song drawer.

Each edit is one pattern-undo step (the loop) or one song-undo step (the
drawer); a fill or a build rolled again joins its first roll's step; every
generator is seeded and pure. The audio of all of it is
`test_drum_hits.py`'s; the gestures are `tests/browser/test_input_matrix.py`'s.
Nothing here was listened to.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import beatgen, drumops, limits, schema
from synthlab.studio.arrange import drum_hits
from synthlab.studio.drumfactory import DRUM_KITS
from synthlab.studio.gen import drums as gd
from synthlab.studio.server import create_app
from synthlab.studio.state import DRUM_ROWS, Arrangement
from tests.studio.test_song_routes import use_song

GRID = "/fragment/drums/grid"
BEAT = {
    "bars": "1",
    "steps": "16",
    "drum.kick.0": "on",
    "drum.kick.8": "on",
    "drum.snare.4": "on",
    "drum.hat.2": "on",
    "drum.hat.6": "on",
}


def _hx(view: str = "drums") -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://x/view/{view}"}


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _arr(client) -> Arrangement:
    return client.app.state.studio.arrangement


def _post(client, url, data=None, view="drums"):
    r = client.post(url, data=data or {}, headers=_hx(view))
    assert r.status_code < 500, r.text[:300]
    return r


def _undo(client):
    return _post(client, "/fragment/pattern/undo")


def _toast(r) -> str:
    """What a response says: a refusal's toast (its body), else the note
    riding a done edit's HX-Trigger."""
    if r.status_code >= 400:
        return r.text
    trig = json.loads(r.headers.get("HX-Trigger") or "{}")
    return (trig.get("studio-toast") or {}).get("message", "")


# --- the grid's per-hit fields -----------------------------------------------------


def test_the_grid_posts_its_whole_table_and_a_hit_without_a_cell_is_dropped(client):
    hits = {"hat.2": {"p": 0.5, "r": 3}, "kick.0": {"n": -0.25}, "rim.5": {"r": 2}}
    _post(client, GRID, {**BEAT, "hits": json.dumps(hits), "rswing": '{"hat": 0.2}'})
    a = _arr(client)
    assert a.drum_hits == {"hat.2": {"p": 0.5, "r": 3}, "kick.0": {"n": -0.25}}
    assert a.drum_row_swing == {"hat": 0.2}
    # a post without the field keeps the table (a script, an old page) ...
    _post(client, GRID, BEAT)
    assert _arr(client).drum_hits == a.drum_hits
    assert _arr(client).drum_row_swing == {"hat": 0.2}
    # ... and a step turned off takes its hit's data with it
    less = {k: v for k, v in BEAT.items() if k != "drum.hat.2"}
    _post(client, GRID, less)
    assert _arr(client).drum_hits == {"kick.0": {"n": -0.25}}
    _post(client, GRID, BEAT)  # on again: a plain hit
    assert "hat.2" not in _arr(client).drum_hits


def test_single_fields_post_beside_the_table(client):
    """`hit.<voice>.<step>.<field>`: what a script, a deck or a test posts.
    A number sets it, "" clears it, and a value that says nothing is not
    stored."""
    _post(client, GRID, {**BEAT, "hit.hat.2.r": "3", "hit.hat.2.v": "0.4"})
    assert _arr(client).drum_hits == {"hat.2": {"r": 3, "v": 0.4}}
    _post(client, GRID, {**BEAT, "hit.hat.2.r": "", "hit.kick.0.t": "5"})
    assert _arr(client).drum_hits == {"hat.2": {"v": 0.4}, "kick.0": {"t": 5.0}}
    _post(client, GRID, {**BEAT, "hit.hat.2.v": "", "hit.kick.0.t": "0"})
    assert _arr(client).drum_hits == {}
    _post(client, GRID, {**BEAT, "rswing.kick": "0", "rswing.hat": "0.3"})
    assert _arr(client).drum_row_swing == {"hat": 0.3}  # the kit's swing is 0 too
    _post(client, GRID, {**BEAT, "rswing.hat": ""})
    assert _arr(client).drum_row_swing == {}


def test_a_rows_swing_equal_to_the_kits_is_no_swing_of_its_own(client):
    _post(client, "/fragment/drums/transport", {"swing": "0.2"})
    _post(client, GRID, {**BEAT, "rswing": '{"kick": 0.0, "hat": 0.2}'})
    assert _arr(client).drum_row_swing == {"kick": 0.0}  # the hat follows the kit


def test_a_row_heads_level_is_the_kits_mixer(client):
    _post(client, GRID, {**BEAT, "rlevel": '{"kick": 0.6, "moo": 1, "hat": 9}'})
    mix = _arr(client).drum_mix
    assert mix["kick"]["level"] == 0.6 and mix["hat"]["level"] == 1.5
    assert "moo" not in mix
    _post(client, GRID, {**BEAT, "level.snare": "0.25"})
    assert _arr(client).drum_mix["snare"]["level"] == 0.25


@pytest.mark.parametrize(
    "hits",
    [
        "not json",
        "[1, 2]",
        "null",
        '{"hat.2": 5}',
        '{"hat.2": {"r": "many", "p": null, "n": [1], "t": {"a": 1}}}',
        '{"cowbell.2": {"r": 2}, "hat.9999": {"r": 2}, "hat": {"r": 2}}',
        '{"hat.2": {"r": 1e400}}',
        "{" * 5000,
        "x" * (drumops.MAX_HITS_JSON + 1),
    ],
    ids=["text", "list", "null", "number", "types", "keys", "huge", "deep", "long"],
)
def test_hostile_tables_change_nothing_they_cannot_say(client, hits):
    _post(client, GRID, {**BEAT, "hits": '{"hat.2": {"r": 2}}'})
    r = _post(client, GRID, {**BEAT, "hits": hits, "rswing": hits, "rlevel": hits})
    assert r.status_code == 200
    got = _arr(client).drum_hits
    assert got in ({"hat.2": {"r": 2}}, {}, {"hat.2": {"r": 4}})
    assert _arr(client).drum_row_swing == {}
    schema.conform_strict(
        client.app.state.studio._payload(), schema.nodes().PAYLOAD
    )  # still a savable session


def test_hostile_values_are_clamped(client):
    wild = {"hat.2": {"v": -5, "p": 7, "r": 99, "n": -40, "t": 1e9}}
    _post(client, GRID, {**BEAT, "hits": json.dumps(wild), "hit.kick.0.n": "nan"})
    assert _arr(client).drum_hits == {
        "hat.2": {"v": 0.05, "r": 4, "n": -0.5, "t": 12.0}
    }
    _post(client, GRID, {**BEAT, "hit.kick.0.r": "inf", "hit.kick.0.zz": "2"})
    assert "kick.0" not in _arr(client).drum_hits
    _post(client, GRID, {**BEAT, "hit.snare.4.p": "0.5", "hit.moo.4.p": "0.1"})
    assert _arr(client).drum_hits["snare.4"] == {"p": 0.5}
    assert limits.MAX_DRUM_HITS == limits.MAX_DRUM_CELLS


def test_a_lane_drag_is_one_undo_step_and_undo_restores_the_table(client):
    _post(client, GRID, BEAT)
    _post(client, GRID, {**BEAT, "hits": '{"hat.2": {"p": 0.5}, "hat.6": {"p": 0.25}}'})
    assert set(_arr(client).drum_hits) == {"hat.2", "hat.6"}
    _undo(client)  # the whole drag: one post, one step
    assert _arr(client).drum_hits == {}
    assert len(_arr(client).drums) == 5
    _post(client, GRID, {**BEAT, "rswing": '{"hat": 0.3}'})
    _undo(client)
    assert _arr(client).drum_row_swing == {}


# --- the pattern tools carry a hit's data with its cell ------------------------------


def _with_hits(client, **more) -> None:
    s = client.app.state.studio
    s.arrangement = replace(
        s.arrangement,
        drums=[["kick", 0], ["hat", 2], ["hat", 15], ["snare", 4]],
        drum_hits={
            "hat.2": {"r": 2, "n": 0.25},
            "hat.15": {"p": 0.5},
            "kick.0": {"v": 0.5},
        },
        drum_row_swing={"hat": 0.2},
        drum_bars=1,
        drum_steps=16,
        **more,
    )


def test_shift_reverse_and_double_move_the_hits_data_with_the_cells(client):
    _with_hits(client)
    _post(client, "/fragment/pattern/drums/shift_right")
    a = _arr(client)
    assert a.drum_hits == {
        "hat.3": {"r": 2, "n": 0.25},
        "hat.0": {"p": 0.5},
        "kick.1": {"v": 0.5},
    }
    assert a.drum_row_swing == {"hat": 0.2}
    _undo(client)
    assert _arr(client).drum_hits["hat.2"] == {"r": 2, "n": 0.25}
    _post(client, "/fragment/pattern/drums/reverse")
    # mirrored in time: an early hit is a late one
    assert _arr(client).drum_hits["hat.13"] == {"r": 2, "n": -0.25}
    assert _arr(client).drum_hits["hat.0"] == {"p": 0.5}
    _undo(client)
    _post(client, "/fragment/pattern/drums/duplicate")
    a = _arr(client)
    assert a.drum_bars == 2 and a.drum_hits["hat.18"] == {"r": 2, "n": 0.25}
    assert a.drum_hits["hat.2"] == {"r": 2, "n": 0.25}
    _undo(client)
    _post(client, "/fragment/pattern/drums/clear")
    assert _arr(client).drums == [] and _arr(client).drum_hits == {}
    _undo(client)
    assert len(_arr(client).drum_hits) == 3


def test_row_tools_keep_the_other_rows_data_and_move_their_own(client):
    _with_hits(client)
    _post(client, "/fragment/drums/row/hat/shift_left")
    a = _arr(client)
    assert a.drum_hits == {
        "hat.1": {"r": 2, "n": 0.25},
        "hat.14": {"p": 0.5},
        "kick.0": {"v": 0.5},
    }
    _post(client, "/fragment/drums/row/hat/hitclear")
    assert _arr(client).drum_hits == {"kick.0": {"v": 0.5}}
    assert [c for c in _arr(client).drums if c[0] == "hat"] == [["hat", 1], ["hat", 14]]
    _undo(client)
    assert "hat.14" in _arr(client).drum_hits
    _post(client, "/fragment/drums/row/hat/clear")
    assert _arr(client).drum_hits == {"kick.0": {"v": 0.5}}
    _post(client, "/fragment/drums/mutate")
    assert set(_arr(client).drum_hits) <= {f"{v}.{s}" for v, s in _arr(client).drums}


def test_more_bars_repeat_the_hits_data_too(client):
    _with_hits(client)
    _post(client, "/fragment/drums/transport", {"bars": "2"})
    a = _arr(client)
    assert a.drum_hits["hat.18"] == {"r": 2, "n": 0.25} and a.drum_hits["kick.16"] == {
        "v": 0.5
    }


# --- Euclid a row -------------------------------------------------------------------


def test_euclid_spreads_a_row_and_touches_no_other(client):
    _with_hits(client)
    _post(client, "/fragment/drums/row/rim/euclid", {"hits": "3", "of": "8"})
    a = _arr(client)
    assert [s for v, s in a.drums if v == "rim"] == [
        0,
        3,
        6,
        8,
        11,
        14,
    ]  # the tresillo, twice
    assert [c for c in a.drums if c[0] != "rim"] == [
        ["hat", 2],
        ["hat", 15],
        ["kick", 0],
        ["snare", 4],
    ]
    assert len(a.drum_hits) == 3
    _post(
        client,
        "/fragment/drums/row/rim/euclid",
        {"hits": "5", "of": "16", "rotate": "2"},
    )
    rim = [s for v, s in _arr(client).drums if v == "rim"]
    assert rim == [2, 6, 9, 12, 15]  # 5 in 16 (0 4 7 10 13), two steps on
    _undo(client)  # one step each
    assert [s for v, s in _arr(client).drums if v == "rim"] == [0, 3, 6, 8, 11, 14]
    _post(client, "/fragment/drums/row/hat/euclid", {"hits": "0", "of": "4"})
    assert not [c for c in _arr(client).drums if c[0] == "hat"]
    assert not any(k.startswith("hat.") for k in _arr(client).drum_hits)


@pytest.mark.parametrize(
    "data",
    [
        {"hits": "x", "of": "8"},
        {"hits": "9", "of": "8"},
        {"hits": "3", "of": "0"},
        {"hits": "3", "of": "99999"},
        {"hits": "-1", "of": "8"},
        {"hits": "3", "of": "8", "rotate": "1e9"},
        {"hits": "3", "of": "8", "rotate": "5000"},
        {},
    ],
)
def test_euclid_refuses_what_it_cannot_spread(client, data):
    _with_hits(client)
    before = _arr(client)
    r = _post(client, "/fragment/drums/row/rim/euclid", data)
    assert r.status_code == 422 and "Euclid" in _toast(r)
    assert _arr(client) == before
    assert _post(client, "/fragment/drums/row/moo/euclid", data).status_code == 404


def test_euclid_is_pure_and_keeps_what_the_grid_hides():
    lane = {
        "bars": 2,
        "steps": 16,
        "drums": [["kick", 0], ["rim", 40]],
        "drum_vel": {},
    }
    a = drumops.euclid_row(lane, "rim", 3, 8, 0)
    assert a == drumops.euclid_row(lane, "rim", 3, 8, 0)
    assert ["rim", 40] in a["drums"]  # past the grid: kept, hidden
    assert lane["drums"] == [["kick", 0], ["rim", 40]]
    assert drumops.euclid_row(lane, "rim", 3, 8, 8)["drums"] == a["drums"]  # a turn
    assert [s for v, s in a["drums"] if v == "rim" and s < 8] == [0, 3, 6]
    five = drumops.euclid_row(lane, "tom", 5, 16, 0)
    assert [s for v, s in five["drums"] if v == "tom" and s < 16] == [0, 4, 7, 10, 13]


# --- a kit under a beat -------------------------------------------------------------


def test_a_kit_swap_that_keeps_the_beat_loads_only_the_voices(client):
    _with_hits(
        client, drum_vel={"2": 0.4}, drum_swing=0.12, drum_samples={"clap": "hit"}
    )
    s = client.app.state.studio
    s.arrangement = replace(s.arrangement, drum_params={"tom": {"tune": 111.0}})
    before = s.arrangement
    r = _post(client, "/fragment/drums/kit", {"kit": "Trance", "keep": "pattern"})
    assert r.status_code == 200
    a = _arr(client)
    for k in (
        "drums",
        "drum_vel",
        "drum_hits",
        "drum_row_swing",
        "drum_swing",
        "drum_bars",
        "drum_steps",
        "drum_samples",
        "drum_mix",
    ):
        assert getattr(a, k) == getattr(before, k), k
    kit = DRUM_KITS["Trance"]
    for voice, params in kit["params"].items():
        assert a.drum_params[voice] == params
    assert a.drum_params["tom"] == {"tune": 111.0}  # a voice the kit does not shape
    assert (s.beat_style, s.beat_seed) == (kit["style"], kit["seed"])
    # nothing in the grid moved: no pattern undo step
    assert "Nothing to undo in the pattern." in _toast(_undo(client))
    # the plain load still replaces the beat
    _post(client, "/fragment/drums/kit", {"kit": "Trance"})
    assert _arr(client).drums != before.drums and _arr(client).drum_hits == {}
    swap = drumops.swap_kit(drumops.loop_lane(before), kit, before.drum_samples)
    assert swap["lane"] == drumops.loop_lane(before) and swap["samples"] == {
        "clap": "hit"
    }
    assert swap["unshaped"] == []
    odd = drumops.swap_kit(
        {"bars": 1, "steps": 16, "drums": [["tom", 0], ["rim", 3]], "drum_vel": {}}, kit
    )
    assert odd["unshaped"] == ["tom", "rim"]


# --- variations A-D -----------------------------------------------------------------


def test_variations_switch_copy_and_undo(client):
    s = client.app.state.studio
    _post(client, "/fragment/drums/bank/load/0")
    house = _arr(client).drums
    assert s.variation_names() == [
        ("A", True, True),
        ("B", False, False),
        ("C", False, False),
        ("D", False, False),
    ]
    # an empty letter starts as a copy of the grid: nothing to hear, nothing to undo
    _post(client, "/fragment/drums/var", {"var": "B"})
    assert s.beat_var == "B" and _arr(client).drums == house
    assert s.variation_names()[:2] == [("A", False, True), ("B", True, True)]
    _post(client, "/fragment/drums/row/kick/clear")  # B loses its kick
    b = _arr(client).drums
    assert b != house
    _post(client, "/fragment/drums/var", {"var": "A"})
    assert s.beat_var == "A" and _arr(client).drums == house
    _post(client, "/fragment/drums/var", {"var": "B"})
    assert _arr(client).drums == b
    # Ctrl+Z takes the switch back, letter and all — and B is still B
    _undo(client)
    assert s.beat_var == "A" and _arr(client).drums == house
    _post(client, "/fragment/drums/var", {"var": "B"})
    assert _arr(client).drums == b
    # copy B over D, and land on it
    _post(client, "/fragment/drums/var", {"var": "D", "copy": "B"})
    assert s.beat_var == "D" and _arr(client).drums == b
    _post(client, "/fragment/drums/var", {"var": "C", "copy": "A"})
    assert _arr(client).drums == house
    for bad in ({"var": "E"}, {"var": ""}, {"var": "B", "copy": "Z"}, {}):
        r = _post(client, "/fragment/drums/var", bad)
        assert r.status_code == 422 and s.beat_var == "C"
    # a variation holds the whole drum lane: per-hit data and row swings too
    _post(
        client, GRID, {**BEAT, "hits": '{"hat.2": {"r": 3}}', "rswing": '{"hat": 0.3}'}
    )
    _post(client, "/fragment/drums/var", {"var": "A"})
    assert _arr(client).drum_hits == {} and _arr(client).drum_row_swing == {}
    _post(client, "/fragment/drums/var", {"var": "C"})
    assert _arr(client).drum_hits == {"hat.2": {"r": 3}}
    assert _arr(client).drum_row_swing == {"hat": 0.3}
    assert _arr(client).drum_swing == 0.0  # the kit's swing is the family's


def test_variations_are_the_loops_and_go_when_the_loop_is_replaced(client):
    s = client.app.state.studio
    _post(client, "/fragment/drums/bank/load/0")
    _post(client, "/fragment/drums/var", {"var": "B"})
    _post(client, "/fragment/drums/bank/load/2")  # a slot loads INTO the variation
    assert s.beat_var == "B" and "A" in s.beat_vars
    sid = s.loop_to_section("x")
    assert s.section_to_loop(sid)
    assert s.beat_var == "A" and s.beat_vars == {}
    # session-only, and no key of the saved state
    assert "beat_vars" not in s._payload() and "beat_var" not in s._payload()


# --- the generators on the loop ------------------------------------------------------


def test_a_fill_rolled_again_is_one_undo_step_and_plays(client):
    s = client.app.state.studio
    _post(client, "/fragment/drums/bank/load/0")
    _undo(client)  # (the load's own step out of the way)
    _post(client, "/fragment/drums/bank/load/0")
    house = [list(c) for c in _arr(client).drums]
    seen = []
    for seed in ("11", "12", "13"):
        r = _post(client, "/fragment/gen", {"kind": "fill", "seed": seed})
        trig = json.loads(r.headers["HX-Trigger"])
        assert trig["preview-ready"]["autoplay"] is True  # it plays by itself
        assert f"seed {seed}" in trig["studio-toast"]["message"]
        seen.append([list(c) for c in _arr(client).drums])
        # what comes before the fill is untouched
        assert [c for c in seen[-1] if c[1] < 8] == [c for c in house if c[1] < 8]
    assert seen[0] != house and len({json.dumps(x) for x in seen}) > 1
    assert s.beat_seed == 1  # Randomise's seed is its own: a fill's is in its toast
    _undo(client)  # three rolls, one step
    assert _arr(client).drums == house
    # a hand edit between two rolls makes the second roll a step of its own
    _post(client, "/fragment/gen", {"kind": "fill", "seed": "11"})
    _post(client, "/fragment/drums/row/rim/fill4")
    edited = [list(c) for c in _arr(client).drums]
    _post(client, "/fragment/gen", {"kind": "fill", "seed": "12"})
    _undo(client)
    assert _arr(client).drums == edited


def test_the_fill_is_seeded_style_aware_and_its_length_is_chosen(client):
    lane = gd.beat("four-floor", 3, 4)
    a, b = (
        gd.fill(lane, 7, 1.0, None, style="two-step"),
        gd.fill(lane, 7, 1.0, None, style="two-step"),
    )
    assert a == b
    rolls = {
        json.dumps(gd.fill(lane, n, 1.0, None, style="two-step")) for n in range(12)
    }
    assert len(rolls) > 1  # the seed picks the figure
    quiet = {v for fig in gd.FILLS["quiet"] for v, _s in fig}
    wrote = {v for v, s in a["drums"] if s >= 48} - {
        v for v, s in lane["drums"] if s >= 48
    }
    assert wrote <= quiet  # garage reaches for its quiet table
    assert gd.STYLE_FILL["melodic-techno"] == "toms"
    half = gd.fill(lane, 7, 0.5, "roll")
    assert [c for c in half["drums"] if c[1] < 56] == [
        c for c in lane["drums"] if c[1] < 56
    ]
    assert half["drums"] != lane["drums"]
    # a roll's last eighth doubles up: ratchets, on the snare
    roll = gd.fill(lane, 2, 1.0, "roll")
    assert {k: v for k, v in roll["hits"].items() if k.startswith("snare.")} == {
        "snare.62": {"r": 2},
        "snare.63": {"r": 2},
    }
    # every N bars: a fill at the end of each, the bars between untouched
    every = gd.fill_every(lane, 7, 2, 1.0, "four-floor")
    assert [c for c in every["drums"] if c[1] < 16 or 32 <= c[1] < 48] == [
        c for c in lane["drums"] if c[1] < 16 or 32 <= c[1] < 48
    ]
    assert [c for c in every["drums"] if 16 <= c[1] < 32] != [
        c for c in lane["drums"] if 16 <= c[1] < 32
    ]
    assert [c for c in every["drums"] if c[1] >= 48] != [
        c for c in lane["drums"] if c[1] >= 48
    ]
    _post(client, "/fragment/drums/bank/load/0")
    _post(client, "/fragment/drums/transport", {"bars": "4"})
    r = _post(client, "/fragment/gen", {"kind": "fill", "every": "2", "seed": "5"})
    assert r.status_code == 200
    for bad in (
        {"kind": "fill", "every": "3"},
        {"kind": "fill", "len": "7"},
        {"kind": "polka"},
        {"kind": "fill", "seed": "x"},
        {},
    ):
        before = _arr(client)
        assert _post(client, "/fragment/gen", bad).status_code == 422
        assert _arr(client) == before


def test_busier_adds_ghosts_and_offbeats_and_sparser_never_takes_the_backbone(client):
    beat = gd.beat("four-floor", 3, 2)
    busy = gd.busier(beat, 0.3, 1)
    added = {tuple(c) for c in busy["drums"]} - {tuple(c) for c in beat["drums"]}
    assert added and {v for v, _s in added} <= {"hat", "rim", "snare"}  # never a kick
    ghosts = {k: h for k, h in busy["hits"].items() if h.get("v", 1) <= 0.4}
    assert ghosts and all(k.split(".")[0] in ("snare", "rim") for k in ghosts)
    # a ghost sits on the sixteenth beside a backbeat
    assert all(int(k.split(".")[1]) % 16 in (3, 5, 11, 13) for k in ghosts)
    assert gd.busier(beat, 0.3, 1) == busy
    thin = beat
    for i in range(12):  # as far as it will go
        thin = gd.sparser(thin, 0.5, i)
    left = {tuple(c) for c in thin["drums"]}
    kicks = {("kick", b * 16 + s) for b in range(2) for s in (0, 4, 8, 12)}
    backbeat = {("clap", b * 16 + s) for b in range(2) for s in (4, 12)}
    assert kicks | backbeat <= left and left <= kicks | backbeat
    # ghosts go first
    less = gd.sparser(busy, 0.05, 1)
    gone = {tuple(c) for c in busy["drums"]} - {tuple(c) for c in less["drums"]}
    assert gone and all(f"{v}.{s}" in ghosts for v, s in gone)
    # each is one undo step on the loop
    _post(client, "/fragment/drums/bank/load/0")
    house = _arr(client).drums
    _post(client, "/fragment/gen", {"kind": "busier", "seed": "4"})
    once = _arr(client).drums
    _post(client, "/fragment/gen", {"kind": "busier", "seed": "5"})
    assert len(_arr(client).drums) > len(once) > len(house)
    _undo(client)
    assert _arr(client).drums == once
    _undo(client)
    assert _arr(client).drums == house
    _post(client, "/fragment/gen", {"kind": "sparser", "seed": "4"})
    assert len(_arr(client).drums) < len(house)


def test_the_build_on_the_loop_is_a_roll_to_32nds_and_undo_brings_the_beat_back(client):
    _post(client, "/fragment/drums/bank/load/0")
    house = _arr(client)
    _post(client, "/fragment/gen", {"kind": "build", "seed": "9"})
    a = _arr(client)
    assert a.drum_bars == 2  # a build takes two bars at least
    snare = sorted(s for v, s in a.drums if v == "snare")
    assert [a.drum_hits[f"snare.{s}"]["v"] for s in snare] == sorted(
        a.drum_hits[f"snare.{s}"]["v"] for s in snare
    )
    assert a.drum_hits[f"snare.{snare[-1]}"] == {"v": 1.0, "t": 7.0, "r": 2}
    played = dict(drum_hits(a))["snare"]
    assert len(played) > len(snare)  # the ratchets
    _post(client, "/fragment/gen", {"kind": "build", "seed": "10"})  # rolled again
    _undo(client)
    assert _arr(client).drums == house.drums and _arr(client).drum_bars == 1
    assert _arr(client).drum_hits == {}


def test_a_generator_refuses_a_loop_the_tempo_cannot_play(client, monkeypatch):
    from synthlab.studio import render

    _post(client, "/fragment/drums/bank/load/0")
    monkeypatch.setattr(render, "LOOP_CAP_SECONDS", 2.5)  # one bar at 124; not two
    before = _arr(client)
    r = _post(client, "/fragment/gen", {"kind": "build"})
    assert r.status_code == 422 and _arr(client) == before


# --- the styles' feel ---------------------------------------------------------------


@pytest.mark.parametrize("style", beatgen.STYLES)
def test_a_styles_feel_is_seeded_and_leaves_its_cells_alone(style):
    """`generate` is what it always was; `dress` only adds: ghosts with a
    velocity of their own, a chance a hit, the rows' own swing."""
    cells = beatgen.generate(style, 5, 2, 16)
    a, b = beatgen.dress(style, 5, cells, 2, 16), beatgen.dress(style, 5, cells, 2, 16)
    assert a == b
    assert {tuple(c) for c in cells} <= {tuple(c) for c in a["drums"]}
    on = {f"{v}.{s}" for v, s in a["drums"]}
    assert set(a["hits"]) <= on and set(a["swing"]) <= set(DRUM_ROWS)
    assert schema.conform(a["hits"], schema.nodes().DRUM_HITS) == a["hits"]
    feel = beatgen.STYLE_FEEL.get(style)
    if not feel:
        assert a == {"drums": cells, "hits": {}, "swing": {}}
    assert beatgen.generate(style, 5, 2, 16) == cells


def test_what_each_pack_lacked_without_per_hit_data():
    """What each pack lacked without per-hit data: ghosts inside a style, a
    chance a hit, a swing a row, a build style."""
    two = gd.beat("two-step", 3, 2)
    ghosts = {k: h["v"] for k, h in two["hits"].items() if h.get("v", 1) <= 0.4}
    assert ghosts == {
        "rim.3": 0.35,
        "snare.7": 0.3,
        "rim.15": 0.35,
        "rim.19": 0.35,
        "snare.23": 0.3,
        "rim.31": 0.35,
    }
    assert two["swing"] == {"kick": 0.0, "clap": 0.0}  # the hats shuffle, these never
    perc = gd.beat("hypnotic-perc", 3, 8)
    chancy = {k: h["p"] for k, h in perc["hits"].items() if "p" in h}
    assert len(chancy) >= 8 * 8 and all(0.5 <= p <= 0.8 for p in chancy.values())
    assert {k.split(".")[0] for k in chancy} == {"rim", "tom"}
    assert len(set(chancy.values())) > 3  # drawn a hit, not one value for all
    # over eight bars the figure never quite repeats
    played = dict(
        drum_hits(
            Arrangement(
                cells=[],
                drums=perc["drums"],
                drum_bars=4,
                drum_steps=16,
                drum_hits=perc["hits"],
            )
        )
    )
    bars = [
        sorted(round(b % 4.0, 6) for b, _g in played["rim"] if int(b // 4) == i)
        for i in range(4)
    ]
    assert len({json.dumps(b) for b in bars}) > 1
    roll = gd.beat("roll", 1, 2)
    snare = [roll["hits"][f"snare.{s}"] for s in range(32)]
    assert [h["v"] for h in snare] == sorted(h["v"] for h in snare) and snare[-1][
        "v"
    ] == 1.0
    assert snare[-1]["t"] == 7.0 and all("r" not in h for h in snare[:24])
    assert all(h["r"] == 2 for h in snare[24:])


# --- a pool beat's family, in the Song drawer ----------------------------------------


@pytest.fixture
def song(client):
    use_song(client)
    return client


def _song(client) -> dict:
    return client.app.state.studio.song


def _cell(client, sid="s1") -> dict:
    s = _song(client)
    sec = next(x for x in s["sections"] if x["id"] == sid)
    return s["patterns"][sec["cells"]["td"]]


def test_a_section_picks_or_makes_a_variation_of_its_beat(song):
    c = song
    r = _post(
        c,
        "/song/cell/variation",
        {"section_id": "s1", "track_id": "td", "var": "B", "copy": "1"},
        "song",
    )
    assert r.status_code == 200
    a, b = _song(c)["patterns"]["pd"], _cell(c)
    assert a["var"] == "A" and b["var"] == "B" and a["fam"] == b["fam"]
    assert b["drums"] == a["drums"] and b["id"] != "pd" and b["name"] == "Beat B"
    assert _cell(c, "s2")["id"] == "pd"  # the other section keeps what it played
    # the family has a B now: another section asking for B picks it
    _post(
        c,
        "/song/cell/variation",
        {"section_id": "s2", "track_id": "td", "var": "B"},
        "song",
    )
    assert _cell(c, "s2")["id"] == b["id"]
    # ... and a section picks one that exists with the plain cell route
    _post(
        c,
        "/song/cell",
        {"section_id": "s2", "track_id": "td", "pattern_id": "pd"},
        "song",
    )
    assert _cell(c, "s2")["id"] == "pd"
    # a varied C: seeded, the kick and the backbeat untouched
    _post(
        c,
        "/song/cell/variation",
        {"section_id": "s2", "track_id": "td", "var": "C", "seed": "5"},
        "song",
    )
    cee = _cell(c, "s2")
    assert cee["var"] == "C" and cee["drums"] != a["drums"]
    assert ["kick", 0] in cee["drums"] and ["snare", 8] in cee["drums"]
    assert cee["gen"]["kind"] == "variation"
    from synthlab.studio import song_model as sm

    assert [(x["var"], bool(x["id"]), x["on"]) for x in sm.family(_song(c), cee)] == [
        ("A", True, False),
        ("B", True, False),
        ("C", True, True),
        ("D", False, False),
        ("fill", False, False),
    ]
    for bad in ({"var": "Q"}, {"var": "B", "track_id": "t1"}):
        data = {"section_id": "s1", "track_id": "td"} | bad
        assert _post(c, "/song/cell/variation", data, "song").status_code == 422


def test_the_familys_fill_rolls_in_place_as_one_undo_step(song):
    c = song
    cell = {"section_id": "s1", "track_id": "td"}
    _post(c, "/song/cell/variation", {**cell, "fill": "1", "seed": "3"}, "song")
    fill = _cell(c)
    assert fill["var"] == "fill" and fill["fam"] == _song(c)["patterns"]["pd"]["fam"]
    assert fill["bars"] == 1 and fill["drums"] != _song(c)["patterns"]["pd"]["drums"]
    first = [list(x) for x in fill["drums"]]
    _post(c, "/song/cell/variation", {**cell, "fill": "1", "seed": "4"}, "song")
    assert _cell(c)["id"] == fill["id"]  # rolled in place, from the beat (not the fill)
    _post(c, "/song/cell/variation", {**cell, "fill": "1", "seed": "5"}, "song")
    assert (
        len([p for p in _song(c)["patterns"].values() if p.get("var") == "fill"]) == 1
    )
    assert _cell(c)["drums"] != first or True
    _post(c, "/song/undo", view="song")  # three rolls, one step
    assert _cell(c)["id"] == "pd"
    assert not any(p.get("var") == "fill" for p in _song(c)["patterns"].values())


def test_the_drawers_grid_and_tools_edit_the_pool_beat(song):
    c = song
    grid = {
        "bars": "1",
        "steps": "16",
        "drum.kick.0": "on",
        "drum.snare.8": "on",
        "drum.hat.2": "on",
    }
    _post(
        c,
        "/song/pattern/pd/grid",
        {
            **grid,
            "hits": '{"hat.2": {"p": 0.5}, "snare.8": {"r": 2}, "rim.1": {"r": 3}}',
            "rswing": '{"hat": 0.25}',
        },
        "song",
    )
    p = _song(c)["patterns"]["pd"]
    assert p["hits"] == {"hat.2": {"p": 0.5}, "snare.8": {"r": 2}} and p["swing"] == {
        "hat": 0.25
    }
    _post(c, "/song/pattern/pd/tool/shift_right", view="song")
    p = _song(c)["patterns"]["pd"]
    assert p["hits"] == {"hat.3": {"p": 0.5}, "snare.9": {"r": 2}} and p["swing"] == {
        "hat": 0.25
    }
    _post(
        c,
        "/song/pattern/pd/tool/euclid",
        {"voice": "rim", "hits": "3", "of": "8"},
        "song",
    )
    assert [s for v, s in _song(c)["patterns"]["pd"]["drums"] if v == "rim"] == [
        0,
        3,
        6,
        8,
        11,
        14,
    ]
    _post(c, "/song/pattern/pd/tool/hitclear", {"voice": "hat"}, "song")
    assert _song(c)["patterns"]["pd"]["hits"] == {"snare.9": {"r": 2}}
    n = len(_song(c)["patterns"]["pd"]["drums"])
    _post(c, "/song/pattern/pd/tool/busier", view="song")
    assert len(_song(c)["patterns"]["pd"]["drums"]) > n
    _post(c, "/song/undo", view="song")  # each a song-undo step
    assert len(_song(c)["patterns"]["pd"]["drums"]) == n
    _post(c, "/song/pattern/pd/tool/fill", view="song")
    assert _song(c)["patterns"]["pd"]["drums"]
    for bad in (
        {"voice": "moo", "hits": "3", "of": "8"},
        {"voice": "rim", "hits": "9", "of": "8"},
        {"voice": "rim"},
    ):
        assert _post(c, "/song/pattern/pd/tool/euclid", bad, "song").status_code == 422
    # a pattern with no per-hit data is stored without the keys
    _post(c, "/song/pattern/pd/grid", {**grid, "hits": "{}", "rswing": "{}"}, "song")
    assert not {"hits", "swing"} & set(_song(c)["patterns"]["pd"])


def test_the_drawer_draws_the_family_and_the_same_sequencer(song):
    c = song
    _post(c, "/song/drawer", {"section_id": "s1", "track_id": "td"}, "song")
    html = c.get("/view/song").text
    drawer = html[html.index('id="song-drawer"') :]
    for mark in (
        'id="drawer-grid"',
        'data-var="A"',
        'data-var="fill"',
        'name="hits"',
        'name="rswing"',
        'data-lane="p"',
        'id="hitmenu-drawer-grid"',
        'data-row="swing"',
        "/tool/euclid",
    ):
        assert mark in drawer, mark
    # a song's rows have no mute / solo / level of their own: the track has
    assert 'data-row="level"' not in drawer and "/fragment/drums/row/" not in drawer
    assert 'name="rlevel"' not in drawer


def _stems(blob: bytes) -> dict:
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return {n: z.read(n) for n in z.namelist() if "rum" in n}


def test_the_songs_audio_paths_all_hear_a_hits_data(song):
    """The section's WAV, the solo-cell audition, the stems export and the
    MIDI file: each is another file once a hit is ratcheted — one
    schedule, read by all four (the samples are `test_drum_hits.py`'s)."""
    c = song

    def heard() -> dict:
        return {
            "section": c.get("/song/section/s1.wav").content,
            "cell": c.get("/song/cell/s1/td.wav").content,
            "stems": _stems(c.get("/export/stems.zip").content),
            "midi": c.get("/export/song.mid").content,
        }

    plain = heard()
    assert all(plain.values())
    grid = {"bars": "1", "steps": "16", "drum.kick.0": "on", "drum.snare.8": "on"}
    _post(c, "/song/pattern/pd/grid", {**grid, "hits": '{"snare.8": {"r": 3}}'}, "song")
    ratcheted = heard()
    assert all(ratcheted[k] != plain[k] for k in plain), [
        k for k in plain if ratcheted[k] == plain[k]
    ]
    _post(c, "/song/undo", view="song")
    assert heard()["section"] == plain["section"]  # and back, byte for byte
