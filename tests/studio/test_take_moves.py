"""What was turned during a take, kept with its loop as curves
(`studio/takemoves.py`, keyrec.js `S.keyMove`).

What is held here: a Patch control moved during a take is a curve on that
field over the loop's grid — its start value held to the first move, then
the moves, the last held — and the loop's sound holds the START value; the
pitch bend, mod wheel and aftertouch are offsets on the patch's own value; a
macro's movement is its targets'; a field no curve can carry (not
CURVABLE) and an FX unit's slider are not recorded and said so in the
take's toast; the session's own sound is left as the knobs are; hostile
moves are bounded and never refuse the take.
"""

from __future__ import annotations

import io
import json
import wave
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import limits, loops, takemoves
from synthlab.studio.curves import CURVABLE
from synthlab.studio.server import create_app


def test_a_moved_control_is_a_curve_from_its_start_value():
    lo, hi = CURVABLE["cutoff"]
    y = lambda v: (v - lo) / (hi - lo)  # noqa: E731
    moves = {
        "from": {"cutoff": 1000.0},
        "moves": {"cutoff": [[2.0, 3000.0], [3.0, 5000.0]]},
    }
    sound = {"patch": {"cutoff": 5000.0}, "curves": {}}
    out, kept, missed = takemoves.apply(sound, moves, 8.0)
    assert kept == ["cutoff"] and missed == []
    assert out["patch"]["cutoff"] == 1000.0  # the loop's sound: as it began
    assert sound["patch"]["cutoff"] == 5000.0  # (the session's, untouched)
    assert out["curves"]["cutoff"] == [
        [0.0, y(1000)],
        [0.25, y(1000)],  # held until it moved, then a step
        [0.25, y(3000)],
        [0.375, y(5000)],
        [1.0, y(5000)],  # the last move held to the loop's end
    ]


def test_the_wheels_are_offsets_on_the_patchs_own_value():
    moves = {"from": {}, "moves": {"pitch_bend": [[1.0, 0.5], [2.0, 0.0]]}}
    out, kept, _ = takemoves.apply({"patch": {"pitch_bend": 0.25}}, moves, 4.0)
    assert kept == ["pitch_bend"]
    # -1..1 onto 0..1: base 0.25 + 0.5 = 0.75 -> 0.875; back to 0.25 -> 0.625
    assert out["curves"]["pitch_bend"] == [
        [0.0, 0.625],
        [0.25, 0.625],
        [0.25, 0.875],
        [0.5, 0.625],
        [1.0, 0.625],
    ]
    assert out["patch"]["pitch_bend"] == 0.25


def test_a_macro_moves_its_targets_and_what_cannot_be_a_curve_is_said():
    macros = [{"value": 0.0, "targets": [{"param": "cutoff", "amt": 1.0}]}]
    moves = {
        "from": {"cutoff": 500.0},
        "moves": {
            "macro:0": [[1.0, 0.5]],
            "release": [[1.0, 0.8]],  # no curve on an envelope time
            "fx:mix": [[1.0, 0.3]],
            "nonsense": [[1.0, 1.0]],
        },
    }
    out, kept, missed = takemoves.apply({"patch": {}}, moves, 4.0, macros)
    assert kept == ["cutoff"] and "cutoff" in out["curves"]
    assert any("FX mix" in m for m in missed) and len(missed) == 2
    said = takemoves.said(kept, missed)
    assert "as automation" in said and "not recorded" in said


def test_moves_past_the_grid_are_not_kept_and_a_long_sweep_is_thinned():
    pts = [[i * 0.01, 60.0 + i] for i in range(3000)]
    moves = {"from": {"cutoff": 60.0}, "moves": {"cutoff": pts}}
    out, kept, _ = takemoves.apply({"patch": {}}, moves, 16.0)
    curve = out["curves"]["cutoff"]
    assert len(curve) <= limits.MAX_CURVE_POINTS and curve[-1][0] == 1.0
    assert all(0 <= x <= 1 for x, _y in curve)
    # one wholly past the grid: nothing kept
    late = {"from": {}, "moves": {"cutoff": [[20.0, 900.0]]}}
    assert takemoves.apply({"patch": {}}, late, 16.0)[1] == []


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[1, 2]",
        json.dumps({"moves": {"cutoff": "x"}}),
        json.dumps({"moves": {"cutoff": [[float("nan"), 1]]}}).replace("NaN", "1e999"),
        json.dumps({"moves": {"cutoff": [[1, True]]}}),
        "x" * (limits.MAX_TAKE_MOVES_CHARS + 1),
        json.dumps({"moves": {f"f{i}": [[0, 1]] for i in range(500)}}),
    ],
    ids=["empty", "text", "list", "points", "huge", "bool", "long", "fields"],
)
def test_hostile_moves_are_bounded_never_an_error(text):
    got = takemoves.parse(text)
    assert set(got) == {"from", "moves", "capped"}
    assert len(got["moves"]) <= limits.MAX_TAKE_MOVE_FIELDS
    for pts in got["moves"].values():
        assert all(isinstance(t, float) and isinstance(v, float) for t, v in pts)


# --- the route ----------------------------------------------------------------


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * 44100)
    return buf.getvalue()


def test_a_take_keeps_its_turns_as_its_loops_curves(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        s = c.app.state.studio
        before = s.patch.cutoff
        moves = {
            "from": {"cutoff": 800.0},
            "moves": {"cutoff": [[1.0, 2000.0]], "fx:feedback": [[1.0, 0.4]]},
        }
        r = c.post(
            "/take/keys",
            data={
                "name": "keys-1",
                "bpm": "120",
                "bars": "1",
                "notes": json.dumps([[0, 1, 57, 0.8]]),
                "moves": json.dumps(moves),
            },
            files={"file": ("take.wav", _wav(), "audio/wav")},
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200, r.text[:300]
        said = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
        assert "Took" in said and "not recorded" in said and "FX feedback" in said
        loop = loops.load(s.data_dir, unquote(r.headers["X-Loop-Name"]))
        patch, extras = loop["sound"]
        assert patch.cutoff == 800.0  # the loop starts where the take did
        assert extras["curves"]["cutoff"][0] == [0.0, pytest.approx((800 - 60) / 15940)]
        assert s.patch.cutoff == before  # the session's sound: as the knobs are


# --- bounded work (a take post is untrusted input) -----------------------------


def test_the_most_a_post_may_carry_is_read_and_thinned_in_bounded_time():
    import time

    per = limits.MAX_TAKE_MOVE_TOTAL // 8  # 8 controls at the total's limit
    fields = ["cutoff", "resonance", "pitch_bend", "mod_wheel"] + [
        f"x{i}" for i in range(4)
    ]
    sent = {
        name: [[round(i * 0.007, 3), i % 10] for i in range(per)] for name in fields
    }
    sent["drive"] = [[0, 1]]  # past the total: not read, said
    text = json.dumps({"moves": sent}, separators=(",", ":"))
    assert len(text) <= limits.MAX_TAKE_MOVES_CHARS
    t0 = time.perf_counter()
    got = takemoves.parse(text)
    out, kept, _missed = takemoves.apply({"patch": {}}, got, 4 * 64.0)
    took = time.perf_counter() - t0
    assert took < 1.0, took
    # the total is shared: only the first eight controls are read whole
    assert sum(len(p) for p in got["moves"].values()) <= limits.MAX_TAKE_MOVE_TOTAL
    assert got["capped"]  # the rest: named for the toast
    # on the take clock's grid: never two points in one 1/50 of a beat
    for pts in got["moves"].values():
        cells = [int(t / limits.TAKE_MOVE_GRID) for t, _v in pts]
        assert len(cells) == len(set(cells))
    assert kept and all(len(out["curves"][k]) <= limits.MAX_CURVE_POINTS for k in kept)
    # the thinning alone, on the longest take's whole grid: one pass
    dense = [[i * 0.02, float((i * 7919) % 101)] for i in range(12_800)]
    t0 = time.perf_counter()
    thinned = takemoves.thin(dense, limits.MAX_CURVE_POINTS)
    assert time.perf_counter() - t0 < 0.5 and len(thinned) <= limits.MAX_CURVE_POINTS


def test_a_post_past_the_raw_limits_is_capped_and_said():
    over = [[i * 0.05, 100.0 + i] for i in range(limits.MAX_TAKE_MOVE_POINTS + 500)]
    got = takemoves.parse(json.dumps({"moves": {"cutoff": over}}))
    assert got["capped"] == ["cutoff"]
    assert got["moves"]["cutoff"][-1][0] < over[-1][0]  # the rest not read
    said = takemoves.said(["cutoff"], [], got["capped"])
    assert "too many moves sent" in said and "Cutoff" in said
    huge = "x" * (limits.MAX_TAKE_MOVES_CHARS + 1)
    assert takemoves.parse(huge)["capped"] and not takemoves.parse(huge)["moves"]


def test_a_thinned_sweep_keeps_its_ends_and_turning_points():
    # up and down three times, 3000 points: thinned to the curve's cap
    pts = []
    for i in range(3000):
        x = i / 2999
        phase = (x * 3) % 1.0
        pts.append([x, phase * 2 if phase < 0.5 else 2 - phase * 2])
    out = takemoves.thin(pts, limits.MAX_CURVE_POINTS)
    assert len(out) <= limits.MAX_CURVE_POINTS
    assert out[0] == pts[0] and out[-1] == pts[-1]
    assert max(y for _x, y in out) == max(y for _x, y in pts)  # the peaks
    assert min(y for _x, y in out[1:-1]) == min(y for _x, y in pts[1:-1])
    # nowhere further from the sweep than one bucket's y-range
    xs, ys = [p[0] for p in out], [p[1] for p in out]
    import numpy as np

    away = np.abs(np.interp([p[0] for p in pts], xs, ys) - [p[1] for p in pts])
    bucket = 1 / ((limits.MAX_CURVE_POINTS - 2) // 2)
    assert away.max() <= 6 * bucket + 1e-9  # slope 6 over one bucket's width
