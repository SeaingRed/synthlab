"""Automation moves (`synthlab/studio/moves.py`, studio docs/song.md,
"Automation moves"): four ready-made lanes on one track in one section.

Pinned here: each move's target is one the lane machinery accepts and its
lane spans the section (also in 7/8 and under a tempo ramp), a unit the
rack lacks is added AT REST (the section with the lane held at its resting
value renders as it did before), a full rack refuses and changes nothing,
applying twice replaces its own lane and unit, and the route (one undo step,
refusals as toasts)."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from synthlab.studio import fxrack, moves
from synthlab.studio import song_model as sm
from synthlab.studio.curves import GAIN, lane_range, parse_fx
from synthlab.studio.render import section_audio
from tests.studio.test_song_automation import RISE, _only, _parts, _sec, _song
from tests.studio.test_song_routes import HX, post, refused, song, use_song
from tests.studio.test_song_routes import client as client  # noqa: F401

KIND = {"open_filter": "lowpass", "wash_out": "reverb", "pump_harder": "pump"}
FX_MOVES = tuple(KIND)


def _lanes(s, sid, tid) -> list[dict]:
    return sm.cell_lanes(_sec(s, sid), tid)


def _lane(s, sid, tid, move) -> dict:
    """The lane `move` wrote: the level lane, or the only FX lane of its kind."""
    if move == "drop_cut":
        (lane,) = [ln for ln in _lanes(s, sid, tid) if ln["target"] == GAIN]
        return lane
    (lane,) = [
        ln
        for ln in _lanes(s, sid, tid)
        if (parse_fx(ln["target"]) or (0, 0))[1] == KIND[move]
    ]
    return lane


def _track(s, tid) -> dict:
    return next(t for t in s["tracks"] if t["id"] == tid)


def _units(s, tid, kind) -> list[dict]:
    return [u for u in _track(s, tid).get("fx") or [] if u["kind"] == kind]


def test_the_moves_are_in_order():
    assert list(moves.MOVES) == ["open_filter", "wash_out", "pump_harder", "drop_cut"]


@pytest.mark.parametrize("move", list(moves.MOVES))
def test_a_move_writes_a_lane_the_machinery_accepts(move):
    s, sid, ids = _song()
    says = moves.apply(s, sid, ids["lead"], move)
    assert "Lead" in says and "Build" in says
    lane = _lane(s, sid, ids["lead"], move)
    assert lane["target"] in sm.lane_targets(_track(s, ids["lead"]))
    lo, hi = lane_range(lane["target"])
    assert hi > lo
    xs = [p[0] for p in lane["points"]]
    assert xs[0] == 0.0 and xs[-1] == 1.0 and xs == sorted(xs)
    assert all(0.0 <= p[1] <= 1.0 for p in lane["points"])
    # the section's render reads it
    assert lane in sm.section_lanes(s, _sec(s, sid))[ids["lead"]]


def test_the_shapes():
    s, sid, ids = _song()
    for move in moves.MOVES:
        moves.apply(s, sid, ids["lead"], move)
    opens = [p[1] for p in _lane(s, sid, ids["lead"], "open_filter")["points"]]
    assert opens[-1] == 1.0 and opens[0] < opens[1] < 0.5  # slow at first
    wash = _lane(s, sid, ids["lead"], "wash_out")["points"]
    assert wash[0][1] == wash[1][1] == 0.0 and wash[1][0] == 0.5 and wash[2][1] > 0
    pump = _lane(s, sid, ids["lead"], "pump_harder")["points"]
    assert pump[0][1] == 0.0 and pump[-1][1] > 0.5
    cut = _lane(s, sid, ids["lead"], "drop_cut")["points"]
    assert cut[0][1] == 0.5 and cut[-1][1] == 0.0
    zero_at = next(p[0] for p in cut if p[1] == 0.0)
    assert zero_at == pytest.approx(1 - 1 / (_sec(s, sid)["bars"] * 4))


@pytest.mark.parametrize("move", FX_MOVES)
def test_a_missing_unit_is_added_at_rest(move):
    s, sid, ids = _song()
    assert not _track(s, ids["lead"]).get("fx")
    moves.apply(s, sid, ids["lead"], move)
    (u,) = _units(s, ids["lead"], KIND[move])
    lane = _lane(s, sid, ids["lead"], move)
    fx = parse_fx(lane["target"])
    assert fx[0] == u["id"] and fx[1] == KIND[move]
    name, v = {
        "open_filter": ("cutoff", 12000.0),
        "wash_out": ("mix", 0.0),
        "pump_harder": ("depth", 0.0),
    }[move]
    assert u["params"][name] == v
    # the lane's resting end is the unit's own value: the filter's top, the
    # reverb's and the pump's start
    lo, hi = lane_range(lane["target"])
    at = lane["points"][-1][1] if move == "open_filter" else lane["points"][0][1]
    assert lo + at * (hi - lo) == v


@pytest.mark.parametrize("move", ["wash_out", "pump_harder"])
def test_the_added_unit_at_rest_changes_no_sample(move):
    """The section with the lane held at its resting value renders as it
    did before the move (reverb mix 0 and pump depth 0 are exact bypasses)."""
    s, sid, ids = _song(bars=2)
    _only(s, ids, "lead")
    ref = copy.deepcopy(s)  # a lane on the muted pad makes the cycle the section
    sm.set_lane(ref, sid, ids["pad"], "cutoff", RISE)
    before = section_audio(_parts(ref, sid))
    moves.apply(s, sid, ids["lead"], move)
    target = _lane(s, sid, ids["lead"], move)["target"]
    sm.set_lane(s, sid, ids["lead"], target, [[0.0, 0.0], [1.0, 0.0]])
    after = section_audio(_parts(s, sid))
    assert before.base.shape == after.base.shape
    assert np.max(np.abs(before.base - after.base)) < 1e-6
    n = min(len(before.tail), len(after.tail))
    assert np.max(np.abs(before.tail[:n] - after.tail[:n])) < 1e-6


def test_an_existing_unit_is_reused_and_untouched():
    s, sid, ids = _song()
    _track(s, ids["lead"])["fx"] = fxrack.clean_chain(
        [{"kind": "reverb", "params": {"mix": 0.3, "decay": 3.0}}]
    )
    moves.apply(s, sid, ids["lead"], "wash_out")
    (u,) = _units(s, ids["lead"], "reverb")
    assert u["params"]["mix"] == 0.3 and u["params"]["decay"] == 3.0
    pts = _lane(s, sid, ids["lead"], "wash_out")["points"]
    assert pts[0][1] == pytest.approx(0.3) and pts[-1][1] > 0.3  # from where it rests


def test_a_bypassed_unit_keeps_its_flag():
    s, sid, ids = _song()
    _track(s, ids["lead"])["fx"] = fxrack.clean_chain(
        [{"kind": "reverb", "params": {}, "off": True}]
    )
    moves.apply(s, sid, ids["lead"], "wash_out")
    lead = _track(s, ids["lead"])
    assert (
        lead["fx"][0].get("off") is True and len(lead["fx"]) == 2
    )  # one sounding added


def _full(s, tid) -> None:
    skip = {*KIND.values(), "stop"}
    kinds = [k for k in fxrack.FX_UNITS if k not in skip][: fxrack.MAX_UNITS]
    _track(s, tid)["fx"] = fxrack.clean_chain(
        [{"kind": k, "params": {}} for k in kinds]
    )
    assert len(_track(s, tid)["fx"]) == fxrack.MAX_UNITS


@pytest.mark.parametrize("move", FX_MOVES)
def test_a_full_rack_refuses_and_changes_nothing(move):
    s, sid, ids = _song()
    _full(s, ids["lead"])
    before = copy.deepcopy(s)
    with pytest.raises(moves.MoveError, match="full"):
        moves.apply(s, sid, ids["lead"], move)
    assert s == before
    # the level move needs no unit: a full rack still takes it
    moves.apply(s, sid, ids["lead"], "drop_cut")
    assert len(_track(s, ids["lead"])["fx"]) == fxrack.MAX_UNITS


@pytest.mark.parametrize("move", list(moves.MOVES))
def test_twice_is_once(move):
    s, sid, ids = _song()
    moves.apply(s, sid, ids["lead"], move)
    once = copy.deepcopy(s)
    moves.apply(s, sid, ids["lead"], move)
    assert s == once
    assert len(_lanes(s, sid, ids["lead"])) == 1
    if move in KIND:
        assert len(_units(s, ids["lead"], KIND[move])) == 1


def test_every_kind_of_track_that_can_take_a_lane():
    s, sid, ids = _song()
    for tid in (ids["pad"], ids["drums"]):  # drums take FX lanes too
        for move in moves.MOVES:
            moves.apply(s, sid, tid, move)
        assert len(_lanes(s, sid, tid)) == 4
    # an audio track with no take in the section: refused in words
    with pytest.raises(moves.MoveError, match="no take"):
        moves.apply(s, sid, ids["vox"], "drop_cut")
    # with a take it takes all four
    _sec(s, sid)["audio"] = [{"track_id": ids["vox"], "clip": "take", "at_bar": 0}]
    for move in moves.MOVES:
        moves.apply(s, sid, ids["vox"], move)
    assert len(_lanes(s, sid, ids["vox"])) == 4


def test_unknown_move_track_section_and_an_empty_cell_are_refused_in_words():
    s, sid, ids = _song()
    with pytest.raises(moves.MoveError, match="No such move"):
        moves.apply(s, sid, ids["lead"], "nope")
    with pytest.raises(moves.MoveError, match="No such track"):
        moves.apply(s, sid, "nope", "wash_out")
    with pytest.raises(moves.MoveError, match="No such section"):
        moves.apply(s, "nope", ids["lead"], "wash_out")
    _sec(s, sid)["cells"].pop(ids["lead"])
    before = copy.deepcopy(s)
    with pytest.raises(moves.MoveError, match="no pattern"):
        moves.apply(s, sid, ids["lead"], "open_filter")
    assert s == before


@pytest.mark.parametrize("move", list(moves.MOVES))
@pytest.mark.parametrize(
    "timing", [{"meter": "7/8"}, {"ramp": 150.0}, {"meter": "3/4"}]
)
def test_the_lane_spans_a_section_in_any_meter_or_ramp(move, timing):
    s, sid, ids = _song(bars=3)
    _sec(s, sid).update(timing)
    moves.apply(s, sid, ids["lead"], move)
    lane = _lane(s, sid, ids["lead"], move)
    assert lane["points"][0][0] == 0.0 and lane["points"][-1][0] == 1.0
    if move == "drop_cut":
        bar = {"7/8": 3.5, "3/4": 3.0}.get(timing.get("meter"), 4.0)
        zero = next(p[0] for p in lane["points"] if p[1] == 0.0)
        assert zero == pytest.approx(1 - 1 / (3 * bar))  # one quarter note, in bars
    assert lane in sm.section_lanes(s, _sec(s, sid))[ids["lead"]]


def test_a_section_under_a_beat_and_a_half_is_cut_whole():
    assert moves._cut(0.0, 1.0) == [[0.0, 0.0], [1.0, 0.0]]


# --- the route -------------------------------------------------------------------


@pytest.fixture
def mclient(client):  # noqa: F811
    s, sid, ids = _song()
    use_song(client, s)
    client.sid, client.ids = sid, ids
    return client


def _move(c, **form):
    return post(c, f"/song/section/{c.sid}/move", form)


def test_the_route_applies_a_move_as_one_undo_step(mclient):
    c = mclient
    before = copy.deepcopy(song(c))
    depth = c.app.state.studio.song_undo_state()["depth"]
    r = _move(c, track=c.ids["lead"], move="wash_out")
    assert r.status_code == 200, r.text[:300]
    assert "Washed out" in r.headers["HX-Trigger"]
    assert len(_units(song(c), c.ids["lead"], "reverb")) == 1
    assert _lane(song(c), c.sid, c.ids["lead"], "wash_out")
    assert c.app.state.studio.song_undo_state()["depth"] == depth + 1
    post(c, "/song/undo")
    assert song(c) == before  # the unit and the lane go together


def test_the_route_refuses_in_words_and_changes_nothing(mclient):
    c = mclient
    before = copy.deepcopy(song(c))
    refused(_move(c, track=c.ids["lead"], move="nope"), "No such move")
    refused(_move(c, track="nope", move="wash_out"), "No such track")
    bad = post(
        c, "/song/section/nope/move", {"track": c.ids["lead"], "move": "wash_out"}
    )
    refused(bad, "No such section")
    refused(_move(c, track=c.ids["vox"], move="drop_cut"), "no take")
    assert song(c) == before
    _full(song(c), c.ids["lead"])
    before = copy.deepcopy(song(c))
    refused(_move(c, track=c.ids["lead"], move="wash_out"), "full")
    assert song(c) == before


def test_the_sections_own_move_still_reorders(mclient):
    c = mclient
    r = post(c, f"/song/section/{c.sid}/move", {"delta": "1"})  # one section: a no-op
    assert r.status_code in (200, 422)
    assert song(c)["sections"][0]["id"] == c.sid


def test_the_menu_rows_are_in_the_page(mclient):
    html = mclient.get("/view/song", headers=HX).text
    for cmd in (
        "Move: open the filter over this section",
        "Move: wash out into the next section",
        "Move: pump harder to the end",
        "Move: cut the last beat",
    ):
        assert f'data-cmd="{cmd}"' in html
