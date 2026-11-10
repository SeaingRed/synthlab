"""FX lanes on the clocked units (`curves.FX_LANES`, `fxauto`; studio
docs/song.md, "FX-parameter lanes"): the trance gate's, the sidechain's
and the pump's `depth`, and the `mix` of the two time remaps, beat repeat
and tape stop.

Each is the unit with its slider moving and nothing else:

- a depth scales an envelope that is computed once and does not depend on
  it — gain = 1 - depth * (1 - g) — so a moving lane is that formula per
  sample, to rounding;
- a time remap's mix is its own blend inside its regions, read at each
  output sample; which steps repeat and where the tape stops never move
  with it, so each output sample is the scalar unit's at that sample's mix.

Measured (125 bpm, a 2-bar cycle of noise or plucked saws; the
worst absolute error against the reference named, full scale = 1):

    lane             still lane  moving lane       ring -> own       ring -> another
                     vs the unit vs the reference  next pass         section (at rest)
    gate depth       0 (bitwise) 0     (formula)   6e-17             6e-17
    sidechain depth  0 (bitwise) 6e-17 (formula)   2e-4, -99.6 dB *  7e-4, -91.4 dB *
    pump depth       0 (bitwise) 6e-17 (formula)   3e-4, -98.6 dB *  8e-4, -90.0 dB *
    beat_repeat mix  0 (bitwise) 0     (per sample) 6e-17            6e-17
    stop mix         0 (bitwise) 0     (per sample) 0                0

"Formula" is x * (1 - d[i] * (1 - g[i])) with g taken from the unit at
depth 1 on a signal of ones; "per sample" is the scalar unit rendered at
the lane's value at 24 instants and read at those instants. The rings are
exact because each unit is linear, has no memory beyond its own period,
and a section's cycle is a whole number of periods.

(*) A duck's first trigger, at time 0, lands on sample 1 (`core.n_samples`
never answers 0) and every later one on its own sample, so the head of a
pass is ducked one sample later than the same duck met mid-buffer. Past
that first duck both rings are exact (0); the first duck's own difference
is the figure above, and it is the unit's with or without a lane (-84 dB
between two passes of a pumped stem with the depth standing still).
The gate has its own: its 16th is a whole number of samples, exact at
125 bpm; at 120 it slips 8 samples a bar against the clock, lane or no
lane — pinned below as what it is)."""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import effects as fx
from synthlab.core import SR, saw
from synthlab.studio import fxauto, render
from synthlab.studio import song_model as sm
from synthlab.studio.curves import FX_LANES, fx_target, lane_range, parse_fx
from synthlab.studio.fxrack import FX_UNITS, build_chain, clean_chain, unit_id
from synthlab.studio.render import section_audio, section_windows
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from tests.studio.test_song_automation import _only, _parts, _rms, _sec, _song
from tests.studio.test_song_fx_lanes import BRIGHT
from tests.studio.test_song_routes import HX, post, refused, song, use_song

UID = "a1b2c3d4"
BPM = 125.0  # a 16th is 5292 samples: every clock here is exact
BAR = int(round(4 * 60.0 / BPM * SR))  # 84672
N = 2 * BAR
# two bars, four on the floor: what a sidechain ducks on
ARR = Arrangement(
    bpm=BPM,
    bars=2,
    drum_bars=2,
    drum_steps=16,
    drums=[["kick", s] for s in range(0, 32, 4)],
)
RISE = [[0.0, 0.0], [1.0, 1.0]]
TRI = [[0.0, 0.1], [0.5, 1.0], [1.0, 0.1]]
DEPTHS = [("gate", "depth"), ("sidechain", "depth"), ("pump", "depth")]
MIXES = [("beat_repeat", "mix"), ("stop", "mix")]
CLOCKED = DEPTHS + MIXES
# rack settings that make each unit audible in every bar
BASE = {
    "gate": {},
    "sidechain": {},
    "pump": {"release": 0.2},
    "beat_repeat": {"grid": 3.0, "repeats": 4.0},
    "stop": {"beats": 2.0},
}
PATTERN = {"gate": "x.xxx.x.x.xxx.x.", "beat_repeat": "x.....x...x....."}


def _unit(kind: str, **params) -> dict:
    raw = {"kind": kind, "params": BASE[kind] | params, "id": UID}
    if kind in PATTERN:
        raw["pattern"] = PATTERN[kind]
    (u,) = clean_chain([raw])
    return u


def _lane(kind: str, param: str, points=RISE) -> list:
    return [fx_target(UID, kind, param), points]


def _noise(n: int, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).uniform(-0.5, 0.5, (n, 2))


def _dry(n: int, seed: int = 0) -> np.ndarray:
    """A cycle of `n` samples of plucked saw notes whose last one rings past
    it, in a buffer two cycles long (what `_cycle_stems` hands a chain: the
    cycle and room for its ring)."""
    rng = np.random.default_rng(seed)
    x = np.zeros(2 * n)
    m = int(0.8 * SR)
    for k in range(8):
        f = 110.0 * 2 ** (int(rng.integers(0, 12)) / 12)
        start = k * n // 8
        x[start : start + m] += saw(f, m) * np.exp(-np.arange(m) / (0.25 * SR)) * 0.4
    return np.column_stack([x, 0.9 * x])


# --- the whitelist -----------------------------------------------------------------


def test_the_five_lanes_are_listed_and_no_time_parameter_is():
    assert FX_LANES["gate"] == FX_LANES["sidechain"] == FX_LANES["pump"] == ("depth",)
    assert FX_LANES["beat_repeat"] == FX_LANES["stop"] == ("mix",)
    for kind, param in CLOCKED:
        lo, hi, _default = FX_UNITS[kind]["params"][param]
        assert lane_range(fx_target(UID, kind, param)) == (lo, hi) == (0.0, 1.0)
    for bad in (
        "fx:a1b2c3d4:gate:pattern",
        "fx:a1b2c3d4:gate:smooth",
        "fx:a1b2c3d4:sidechain:release",
        "fx:a1b2c3d4:pump:beats",
        "fx:a1b2c3d4:pump:release",
        "fx:a1b2c3d4:beat_repeat:grid",
        "fx:a1b2c3d4:beat_repeat:repeats",
        "fx:a1b2c3d4:beat_repeat:pitch",
        "fx:a1b2c3d4:beat_repeat:seed",
        "fx:a1b2c3d4:stop:beats",
        "fx:a1b2c3d4:stop:fall",
        "fx:a1b2c3d4:stop:period",
    ):
        assert parse_fx(bad) is None, bad


# --- a still lane is the unit ------------------------------------------------------


@pytest.mark.parametrize(("kind", "param"), CLOCKED)
def test_a_lane_that_stands_still_is_the_unit_bit_for_bit(kind, param):
    x = _noise(2 * N)
    for v in (0.0, 0.37, 1.0):
        lanes = [_lane(kind, param, [[0.0, v], [1.0, v]])]
        got, _out = fxauto.render(x, [_unit(kind)], ARR, lanes, N, 2 * N / SR)
        want = fx.chain(x, build_chain([_unit(kind, **{param: v})], ARR))
        assert np.array_equal(got, want), (kind, v)
        if v:  # ... and the unit is doing something there
            assert np.max(np.abs(want - x)) > 0.05, (kind, v)


def test_the_array_paths_leave_the_scalar_units_alone():
    """`effects` with an array that stands still against the scalar call,
    with no studio in between (the row test_effects_swept would hold)."""
    x = _noise(N)
    times = [k * 60.0 / BPM for k in range(8)]
    for d in (0.0, 0.25, 1.0):
        full = np.full(N, d)
        assert np.array_equal(fx.gate("x.xx", BPM, full)(x), fx.gate("x.xx", BPM, d)(x))
        assert np.array_equal(
            fx.sidechain(times, full, 0.2)(x), fx.sidechain(times, d, 0.2)(x)
        )
        assert np.array_equal(
            fx.beat_repeat(BPM, mix=full, pattern="x...")(x),
            fx.beat_repeat(BPM, mix=d, pattern="x...")(x),
        )
        assert np.array_equal(fx.stop(BPM, mix=full)(x), fx.stop(BPM, mix=d)(x))
    # all-zero arrays are the bypass the scalar zero is
    zero = np.zeros(N)
    assert fx.gate("x.", BPM, zero)(x) is x and fx.stop(BPM, mix=zero)(x) is x
    assert fx.beat_repeat(BPM, mix=zero)(x) is x
    # mono keeps its shape under an array
    mono = x[:, 0]
    ramp = np.linspace(0.0, 1.0, N)
    for unit in (
        fx.gate("x.", BPM, ramp),
        fx.sidechain(times, ramp),
        fx.beat_repeat(BPM, mix=ramp),
        fx.stop(BPM, mix=ramp),
    ):
        assert unit(mono).shape == mono.shape


# --- a moving lane is the formula --------------------------------------------------


@pytest.mark.parametrize(("kind", "param"), DEPTHS)
@pytest.mark.parametrize("shape", [RISE, TRI], ids=["rise", "tri"])
def test_a_depth_lane_scales_the_envelope_per_sample_exactly(kind, param, shape):
    """gain = 1 - depth * (1 - g): `g` is the unit's own envelope, read off
    a signal of ones at depth 1. Measured: 2e-16 at worst."""
    x = _noise(N)
    unit = _unit(kind)
    got, _out = fxauto.render(x, [unit], ARR, [_lane(kind, param, shape)], N, N / SR)
    g = fx.chain(np.ones((N, 2)), build_chain([_unit(kind, depth=1.0)], ARR))[:, 0]
    assert g.min() < 0.05 and g.max() > 0.9  # an envelope, shut and open
    d = fxauto.values(shape, 0.0, 1.0, N, N)
    want = x * (1.0 - d * (1.0 - g))[:, None]
    assert np.max(np.abs(got - want)) < 1e-12
    # heard: at depth 0 nothing happens, at depth 1 the envelope shuts
    shut = g < 0.02
    early, late = slice(0, N // 8), slice(N - N // 8, N)
    if shape is RISE:
        np.testing.assert_allclose(got[early], x[early], atol=0.07)
        assert _rms(got[late][shut[late]]) < 0.1 * _rms(x[late][shut[late]])


@pytest.mark.parametrize(("kind", "param"), MIXES)
@pytest.mark.parametrize("shape", [RISE, TRI], ids=["rise", "tri"])
def test_a_remaps_mix_lane_is_the_scalar_unit_at_each_samples_mix(kind, param, shape):
    """24 instants across the cycle: the lane's output there is the scalar
    unit's, rendered with the mix the lane holds at that instant. Which
    steps repeat (or where the tape stops) is the same in every render —
    the mix never decides it."""
    x = _dry(N)[:N]
    unit = _unit(kind)
    got, _out = fxauto.render(x, [unit], ARR, [_lane(kind, param, shape)], N, N / SR)
    m = fxauto.values(shape, 0.0, 1.0, N, N)
    wet = fx.chain(x, build_chain([_unit(kind, mix=1.0)], ARR))
    moved = np.flatnonzero(np.abs(wet - x).max(axis=1) > 1e-3)
    assert len(moved) > N // 50  # the unit works on a good part of the cycle
    for i in moved[:: max(1, len(moved) // 24)][:24]:
        scalar = fx.chain(x, build_chain([_unit(kind, mix=float(m[i]))], ARR))
        assert np.max(np.abs(got[i] - scalar[i])) < 1e-12, i
    # outside its regions the unit passes the signal, whatever the mix
    still = np.abs(wet - x).max(axis=1) == 0.0
    assert still.sum() > N // 4
    assert np.max(np.abs(got[still] - x[still])) < 1e-12


# --- the two tails -----------------------------------------------------------------

# A duck's very first trigger, at time 0, lands on sample 1, not 0
# (`core.n_samples` never answers 0), where every later trigger lands on
# its own sample. So a duck's gain repeats from its second trigger on, and
# the head of a pass (its first duck: the 6 ms fall and the release) is a
# sample later than the same duck met mid-buffer. That is the unit's, lane
# or no lane; what is pinned here is that past the first duck the tails are
# exact, and how small the first duck's own difference is.
FIRST_DUCK = {
    "sidechain": int(0.28 * SR) + 2,  # the default release
    "pump": int(0.2 * SR) + 2,  # BASE's
}


def _first_duck_is_all_there_is(kind: str, err, ref) -> None:
    """The first duck's one-sample lateness against the pass: measured
    -90 dB or lower — and the unit's own (-84 / -83 dB between two passes
    with the depth standing still)."""
    db = 20 * np.log10(max(_rms(err), 1e-300) / _rms(ref))
    assert db < -55.0, (kind, db)
    x = np.vstack([_dry(N)[:N]] * 2)
    still = fx.chain(x, build_chain([_unit(kind, depth=0.6)], ARR))
    own = 20 * np.log10(_rms(still[N:] - still[:N]) / _rms(still[:N]))
    assert -120.0 < own < -55.0, (kind, own)  # the unit's own, with no lane


@pytest.mark.parametrize(("kind", "param"), CLOCKED)
def test_the_ring_into_the_next_pass_replays_the_lane_exactly(kind, param):
    """Our fold (the ring that replays the lane, onto the head of the next
    pass) against two passes rendered in one piece."""
    x = _dry(N)
    unit = _unit(kind)
    lanes = [_lane(kind, param, TRI)]
    loop, _out = fxauto.render(x, [unit], ARR, lanes, N, 2 * N / SR)
    two = np.zeros((3 * N, 2))
    two[: 2 * N] += x
    two[N:] += x
    truth, _ = fxauto.render(two, [unit], ARR, lanes, N, 3 * N / SR)
    folded = loop[:N] + loop[N:]
    assert _rms(x[N:]) > 1e-3  # there is a ring to fold
    err = truth[N : 2 * N] - folded
    assert np.max(np.abs(err[FIRST_DUCK.get(kind, 0) :])) < 1e-9
    if kind in FIRST_DUCK:
        _first_duck_is_all_there_is(kind, err, truth[N : 2 * N])


@pytest.mark.parametrize(("kind", "param"), CLOCKED)
def test_the_ring_into_another_section_rests_at_the_racks_value_exactly(kind, param):
    """Into a section that does not automate the parameter the unit sits at
    the rack's value: the ring (`out`) plus that section's own plain render
    is the two sections in one piece."""
    rest = 0.3
    unit = _unit(kind, **{param: rest})
    x, y = _dry(N), _dry(N, seed=5)
    loop, out = fxauto.render(x, [unit], ARR, [_lane(kind, param)], N, 2 * N / SR)
    seq = np.zeros((3 * N, 2))
    seq[: 2 * N] += x
    seq[N:] += y
    arrays = {param: fxauto.values(RISE, 0.0, 1.0, N, 3 * N, rest)}
    truth = fxauto._unit(seq, unit, ARR, arrays, N)
    ours = fx.chain(y, build_chain([unit], ARR))[:N] + out[N:]
    err = truth[N : 2 * N] - ours
    assert np.max(np.abs(err[FIRST_DUCK.get(kind, 0) :])) < 1e-9
    if kind in FIRST_DUCK:
        _first_duck_is_all_there_is(kind, err, truth[N : 2 * N])
    # the other tail is not this one: the lane restarts at 0, the rack
    # rests (the stop's window is the END of the period, where this ring
    # has died away: there both tails are the dry ring)
    if kind != "stop":
        assert np.max(np.abs(loop[N:] - out[N:])) > 1e-3


def test_the_gates_sixteenth_is_whole_samples_at_any_tempo():
    """What the table's footnote says, measured: at 120 bpm a 16th is
    5512.5 samples and the gate steps every 5512, so its pattern is 8
    samples early after a bar. That is the unit's (and was before lanes);
    at 125 bpm, where a 16th is 5292 samples, there is no slip."""
    for bpm, slip in ((120.0, 8), (125.0, 0)):
        bar = int(round(4 * 60.0 / bpm * SR))
        g = fx.gate("x...............", bpm)(np.ones(3 * bar))
        edges = np.flatnonzero(np.diff((g > 0.5).astype(int)) == 1) + 1
        assert bar - (edges[1] - edges[0]) == slip, bpm


# --- a section ---------------------------------------------------------------------


def _fx_song(kind: str, param: str, points=RISE, bars=2, **params):
    """The Lead alone through one clocked unit with a lane on `param`."""
    s, sid, ids = _song(bars=bars, patch=BRIGHT)
    _only(s, ids, "lead")
    lead = s["tracks"][0]
    raw = {"kind": kind, "params": BASE[kind] | params}
    if kind in PATTERN:
        raw["pattern"] = PATTERN[kind]
    lead["fx"] = clean_chain([raw])
    uid = unit_id(lead["fx"], 0)
    sm.set_lane(s, sid, ids["lead"], fx_target(uid, kind, param), points)
    return s, sid, ids, uid


def _static(s, sid, ids, uid, kind, param, value):
    ref = json.loads(json.dumps(s))
    target = fx_target(uid, kind, param)
    sm.set_lane(ref, sid, ids["lead"], target, [[0, value], [1, value]])
    return section_audio(_parts(ref, sid))


def test_a_gate_depth_lane_brings_the_chop_in_across_the_section():
    """Depth 0 -> 1 over two bars: it starts as the ungated notes and ends
    as the full gate; the gate's closed steps go from the notes' level to
    silence."""
    s, sid, ids, uid = _fx_song("gate", "depth")
    swept = section_audio(_parts(s, sid)).base
    open_ = _static(s, sid, ids, uid, "gate", "depth", 0.0).base
    shut = _static(s, sid, ids, uid, "gate", "depth", 1.0).base
    n = len(swept)
    np.testing.assert_allclose(swept[:64], open_[:64], atol=1e-3)
    np.testing.assert_allclose(swept[-64:], shut[-64:], atol=1e-3)
    q = n // 4
    lost = open_ - swept  # what the gate took away
    assert _rms(lost[:q]) < 0.4 * _rms(lost[-q:])
    assert _rms(lost[-q:]) > 0.5 * _rms((open_ - shut)[-q:])


def test_a_stop_mix_lane_places_the_stop_in_this_section_only():
    """The rack rests at mix 0 (the unit does nothing anywhere) and the
    section's lane holds it at 1: the section's last two beats stop, and
    the same song without the lane is the plain notes."""
    s, sid, ids, uid = _fx_song("stop", "mix", [[0.0, 1.0], [1.0, 1.0]], mix=0.0)
    stopped = section_audio(_parts(s, sid)).base
    # the same pass with the lane at the rack's 0: the unit is a bypass
    plain = _static(s, sid, ids, uid, "stop", "mix", 0.0).base
    n = len(plain)
    beat = int(60.0 / 120.0 * SR)
    assert n == 8 * beat and len(stopped) == n
    assert np.array_equal(stopped[: n - 2 * beat], plain[: n - 2 * beat])
    window = stopped[n - 2 * beat :]
    assert not np.allclose(window, plain[n - 2 * beat :], atol=1e-3)
    # it has halted: the last 20 ms before the fade back is a tenth of the
    # window's level or less (the level follows the speed down)
    assert np.max(np.abs(window[-1100:-200])) < 0.1 * np.max(np.abs(window))
    # without the lane the section is the plain notes: nothing in the rack
    # sounds at mix 0
    bare = json.loads(json.dumps(s))
    sm.remove_lane(bare, sid, ids["lead"], fx_target(uid, "stop", "mix"))
    cycle = section_audio(_parts(bare, sid)).base
    np.testing.assert_allclose(cycle, plain, atol=1e-9)


def test_a_beat_repeat_mix_lane_brings_the_stutter_in():
    s, sid, ids, uid = _fx_song("beat_repeat", "mix")
    swept = section_audio(_parts(s, sid)).base
    dry = _static(s, sid, ids, uid, "beat_repeat", "mix", 0.0).base
    wet = _static(s, sid, ids, uid, "beat_repeat", "mix", 1.0).base
    q = len(swept) // 4
    assert _rms((swept - dry)[:q]) < 0.4 * _rms((wet - dry)[:q])
    assert _rms((swept - wet)[-q:]) < 0.4 * _rms((wet - dry)[-q:])


def _busy() -> tuple[dict, str, dict]:
    """All five at once: the Lead through a gate, a stop and a beat repeat,
    the Pad through a pump, the drums through a sidechain."""
    s, sid, ids = _song(patch=BRIGHT)
    lead, pad, drums = s["tracks"][0], s["tracks"][1], s["tracks"][2]
    lead["fx"] = clean_chain(
        [
            {"kind": "gate", "params": {}, "pattern": PATTERN["gate"]},
            {"kind": "beat_repeat", "params": {"grid": 3.0}, "pattern": "x......."},
            {"kind": "stop", "params": {"beats": 2.0}},
        ]
    )
    pad["fx"] = clean_chain([{"kind": "pump", "params": {"beats": 0.5}}])
    drums["fx"] = clean_chain([{"kind": "sidechain", "params": {}}])
    lanes = (
        ("lead", lead, 0, "gate", "depth", TRI),
        ("lead", lead, 1, "beat_repeat", "mix", RISE),
        ("lead", lead, 2, "stop", "mix", RISE),
        ("pad", pad, 0, "pump", "depth", RISE),
        ("drums", drums, 0, "sidechain", "depth", TRI),
    )
    for name, track, i, kind, param, points in lanes:
        target = fx_target(unit_id(track["fx"], i), kind, param)
        sm.set_lane(s, sid, ids[name], target, points)
    return s, sid, ids


def test_a_cycle_in_chunks_is_the_cycle_in_one_piece(monkeypatch):
    """The chain runs once over the stitched cycle and each lane is read
    over the cycle, never the chunk: two 2-bar chunks are the one-piece
    render at 1e-9 — the cycle, its ring and its ring into another
    section."""
    s, sid, _ids = _busy()
    parts = _parts(s, sid)
    assert set(parts.fx_auto) == {"main", "layer2", "drums"}
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 10.0)
    assert len(section_windows(parts)) == 1
    whole = section_audio(parts)
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 4.0)
    assert section_windows(parts) == [(0, 2), (2, 4)]
    chunked = section_audio(parts)
    assert np.max(np.abs(whole.base)) > 0.1 and whole.out is not None
    np.testing.assert_allclose(chunked.base, whole.base, atol=1e-9)
    for a, b in ((chunked.tail, whole.tail), (chunked.out, whole.out)):
        m = max(len(a), len(b))
        pad = lambda t: np.vstack([t, np.zeros((m - len(t), 2))])  # noqa: E731, B023
        np.testing.assert_allclose(pad(a), pad(b), atol=1e-9)


def test_a_section_without_these_lanes_keeps_its_key_and_its_audio():
    """A gate, a pump, a sidechain, a beat repeat in the rack and no lane:
    nothing about the section moves — no `fx_auto`, no second ring."""
    s, sid, _ids = _busy()
    _sec(s, sid).pop("auto")
    parts = _parts(s, sid)
    assert parts.fx_auto == {}
    audio = section_audio(parts)
    assert audio.out is None and audio.handoff is audio.tail


# --- the route and the drawer ------------------------------------------------------


@pytest.fixture
def studio(tmp_path):
    """The song routes' fixture song with a gate, a stop and a beat repeat
    on the Bass (t1) and a sidechain on the Lead (t2)."""
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        s = c.app.state.studio
        s.song["tracks"][1]["fx"] = clean_chain(
            [
                {"kind": "gate", "params": {"depth": 0.8}},
                {"kind": "stop", "params": {"mix": 0.0}},
                {"kind": "beat_repeat", "params": {}},
            ]
        )
        s.song["tracks"][2]["fx"] = clean_chain(
            [{"kind": "sidechain", "params": {}}, {"kind": "pump", "params": {}}]
        )
        yield c


def _auto(client, **data):
    base = {"section_id": "s1", "track_id": "t1"}
    return post(client, "/song/cell/auto", base | {k: str(v) for k, v in data.items()})


def test_the_route_adds_each_lane_at_its_units_slider(studio):
    for target, rest in (
        ("fx:@0:gate:depth", 0.8),
        ("fx:@1:stop:mix", 0.0),
        ("fx:@2:beat_repeat:mix", 1.0),
    ):
        r = _auto(studio, op="add", target=target)
        assert r.status_code == 200, r.text[:300]
        sec = song(studio)["sections"][0]
        assert sm.cell_lanes(sec, "t1")[-1]["points"] == [[0.0, rest], [1.0, rest]]
    fx_units = song(studio)["tracks"][1]["fx"]
    assert all("id" in u for u in fx_units)
    assert studio.get("/song/plan.json").status_code == 200  # and it plays
    html = studio.get("/view/song", headers=HX).text
    post(studio, "/song/drawer", {"section_id": "s1", "track_id": "t1"})
    html = studio.get("/view/song", headers=HX).text
    drawer = html[html.index('id="song-drawer"') :]
    for label in ("Trance gate depth", "Tape stop mix", "Beat repeat mix"):
        assert f">{label}<" in drawer, label
    # a depth reads as a percentage (the read-out carries `data-untyped`
    # since the UI integration: a lane point takes no typed value)
    assert ">80%</output>" in drawer


@pytest.mark.parametrize(
    "target",
    [
        "fx:@0:gate:pattern",
        "fx:@0:gate:smooth",
        "fx:@1:stop:beats",
        "fx:@1:stop:fall",
        "fx:@2:beat_repeat:grid",
        "fx:@2:beat_repeat:chance",
        "fx:@0:stop:mix",  # the unit at 0 is the gate
        "fx:@3:stop:mix",
        "fx:ffffffff:gate:depth",
    ],
)
def test_a_hostile_lane_target_is_a_toast_and_changes_nothing(studio, target):
    before = json.dumps(song(studio))
    refused(_auto(studio, op="add", target=target), "")
    assert json.dumps(song(studio)) == before


def test_the_menu_offers_the_depths_and_the_mixes(studio):
    lead = song(studio)["tracks"][2]
    assert sm.fx_lane_menu(lead) == [
        ("Sidechain", [("fx:@0:sidechain:depth", "depth")]),
        ("Pump", [("fx:@1:pump:depth", "depth")]),
    ]
    bass = song(studio)["tracks"][1]
    assert [label for label, _c in sm.fx_lane_menu(bass)] == [
        "Trance gate",
        "Tape stop",
        "Beat repeat",
    ]
