"""FX-parameter automation lanes (studio docs/song.md, "FX-parameter
lanes"): a section lane on a parameter of a unit in the
TRACK's FX chain, target "fx:<unit id>:<kind>:<param>" (`curves.FX_LANES`,
rendered by `fxauto`).

Pinned here: each whitelisted parameter is the unit itself with its slider
moving (a lane that stands still IS the unit; the swept filters against a
coefficient set per sample), the two tails a moving parameter leaves (into
the section's own next pass, into another section) with their measured
error, the unit's identity (an id given when a lane first targets it, a
missing unit, a reordered rack), keys and saved bytes of everything that
does not use the feature, and the route, the drawer, undo and the project
file."""

from __future__ import annotations

import io
import json
import wave
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import effects as fx
from synthlab.core import SR, biquad, saw
from synthlab.studio import fxauto, limits, render, schema
from synthlab.studio import song_model as sm
from synthlab.studio.curves import FX_LANES, fx_target, lane_range, parse_fx
from synthlab.studio.fxrack import FX_UNITS, build_chain, clean_chain, unit_id
from synthlab.studio.render import (
    StemCache,
    section_audio,
    section_key,
    section_master_audio,
    section_windows,
)
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch
from tests.studio.test_song_automation import (
    _centroid,
    _only,
    _parts,
    _rms,
    _sec,
    _song,
)
from tests.studio.test_song_routes import (
    HX,
    changed,
    oob_ids,
    post,
    refused,
    song,
    use_song,
)

N = schema.nodes()
ARR = Arrangement(bpm=120.0)
UID = "a1b2c3d4"
RISE = [[0.0, 0.0], [1.0, 1.0]]
TRI = [[0.0, 0.1], [0.5, 1.0], [1.0, 0.1]]
BRIGHT = Patch(
    name="bright", osc1="saw", cutoff=12000.0, filter_env=0.0, release=0.05
).to_json()
EVERY = [(kind, param) for kind, params in FX_LANES.items() for param in params]
# the units that are linear and time-invariant but for the lane: for these
# the two tails are exact, not close. (The others' own folds are not exact
# with their sliders still either: a drive is not linear, a phaser's,
# chorus's or flanger's LFO and a ring mod's carrier restart with the
# stem, granular and shimmer read the whole buffer.)
LINEAR = [
    ("reverb", "mix"),
    ("delay", "mix"),
    ("delay", "feedback"),
    ("comb", "mix"),
]
SWEPT = [
    ("lowpass", "cutoff"),
    ("lowpass", "q"),
    ("highpass", "cutoff"),
    ("highpass", "q"),
    ("eq", "low"),
    ("eq", "mid_gain"),
    ("eq", "high"),
    ("eq", "mid_freq"),
]


def _db(err, ref) -> float:
    """`err`'s level against `ref`'s, in dB."""
    return 20 * np.log10(max(_rms(err), 1e-300) / _rms(ref))


def _unit(kind: str, uid: str | None = UID, **params) -> dict:
    (u,) = clean_chain([{"kind": kind, "params": params, "id": uid}])
    return u


def _lane(kind: str, param: str, points=RISE, uid: str = UID) -> list:
    return [fx_target(uid, kind, param), points]


def _dry(n: int, seed: int = 0) -> np.ndarray:
    """A cycle of `n` samples of plucked saw notes whose last one rings past
    it, in a buffer two cycles long (what `_cycle_stems` hands a chain: the
    cycle and room for its ring)."""
    rng = np.random.default_rng(seed)
    x = np.zeros(2 * n)
    m = int(0.4 * SR)
    for k in range(8):
        f = 110.0 * 2 ** (int(rng.integers(0, 12)) / 12)
        start = k * n // 8
        x[start : start + m] += saw(f, m) * np.exp(-np.arange(m) / (0.12 * SR)) * 0.4
    return np.column_stack([x, 0.9 * x])


# --- the whitelist -----------------------------------------------------------------


def test_the_whitelist_names_real_parameters_and_no_time_ones():
    for kind, params in FX_LANES.items():
        assert set(params) <= set(FX_UNITS[kind]["params"]), kind
    assert "time" not in FX_LANES["delay"] and "sync" not in FX_LANES["delay"]
    # the two time remaps are there by their blend alone
    # (test_song_fx_lanes_clock.py), never by a time parameter
    assert FX_LANES["beat_repeat"] == FX_LANES["stop"] == ("mix",)
    # a target reads on its own: the unit's id, its kind, the parameter
    assert parse_fx("fx:a1b2c3d4:reverb:mix") == ("a1b2c3d4", "reverb", "mix")
    assert lane_range("fx:a1b2c3d4:lowpass:cutoff") == (100.0, 12000.0)
    # the drive's slider grew from 1-4 to 1-12; a lane keeps 1-4, so a
    # lane drawn before plays the values it did (`curves.FX_LANE_RANGES`)
    assert FX_UNITS["drive"]["params"]["amount"][:2] == (1.0, 12.0)
    assert lane_range("fx:a1b2c3d4:drive:amount") == (1.0, 4.0)
    assert lane_range("fx:a1b2c3d4:clip:drive") == (0.0, 24.0)
    for bad in (
        "fx:a1b2c3d4:reverb:decay",  # not on the whitelist
        "fx:a1b2c3d4:beat_repeat:grid",
        "fx:a1b2c3d4:stop:beats",
        "fx:a1b2c3d4:delay:time",
        "fx:A1B2C3D4:reverb:mix",  # not an id
        "fx:a1b2c3:reverb:mix",
        "fx:@0:reverb:mix",  # a menu slot is never a stored target
        "fx:a1b2c3d4:reverb",
        "fx:a1b2c3d4:reverb:mix:x",
        "reverb:mix",
        None,
        7,
    ):
        assert parse_fx(bad) is None, bad


@pytest.mark.parametrize(("kind", "param"), EVERY)
def test_a_lane_that_stands_still_is_the_unit_with_its_slider_there(kind, param):
    """The honesty check on every whitelisted parameter: no crossfade, no
    second render — the lane's value IS the unit's parameter. The mix, the
    drive, the feedback and the filters' cutoff / q are the scalar unit bit
    for bit; the EQ's gains run its swept form, equal to rounding."""
    n = SR
    x = _dry(n)
    lo, hi = lane_range(fx_target(UID, kind, param))
    base = {"mid_gain": 6.0} if param == "mid_freq" else {}
    for v in (0.0, 0.37, 1.0):
        lanes = [_lane(kind, param, [[0.0, v], [1.0, v]])]
        got, _out = fxauto.render(x, [_unit(kind, **base)], ARR, lanes, n, 2 * n / SR)
        static = [_unit(kind, **base, **{param: lo + v * (hi - lo)})]
        want = fx.chain(x, build_chain(static, ARR))
        tol = 1e-12 if kind == "eq" else 0.0
        assert np.max(np.abs(got - want)) <= tol, (kind, param, v)


def test_a_lane_moves_only_its_own_unit_and_the_units_before_it_run_once():
    """Two reverbs and a drive between them: a lane on the second reverb's
    mix leaves the first two units' output exactly the plain chain's."""
    n = SR
    x = _dry(n)
    units = clean_chain(
        [
            {"kind": "reverb", "params": {"decay": 0.5}},
            {"kind": "drive", "params": {}},
            {"kind": "reverb", "params": {"decay": 1.5, "mix": 0.5}, "id": UID},
        ]
    )
    flat = [_lane("reverb", "mix", [[0.0, 0.5], [1.0, 0.5]])]
    got, _out = fxauto.render(x, units, ARR, flat, n, 2 * n / SR)
    assert np.array_equal(got, fx.chain(x, build_chain(units, ARR)))
    moved, _out = fxauto.render(x, units, ARR, [_lane("reverb", "mix")], n, 2 * n / SR)
    head = fx.chain(x, build_chain(units[:2], ARR))
    wet = fx.reverb(decay=1.5, mix=1.0, size=0.8)(head)
    # the lane's first sample is mix 0 (the head, dry), its last mix 1
    np.testing.assert_allclose(moved[0], head[0], atol=1e-15)
    np.testing.assert_allclose(moved[n - 1], wet[n - 1], atol=1e-12)


def test_a_lane_whose_unit_is_gone_or_another_kind_finds_nothing():
    units = clean_chain(
        [{"kind": "reverb", "params": {}, "id": UID}, {"kind": "delay", "params": {}}]
    )
    assert fxauto.resolve(units, [_lane("reverb", "mix")]) == {0: {"mix": RISE}}
    for lane in (
        _lane("reverb", "mix", uid="ffffffff"),  # no such unit
        _lane("delay", "mix"),  # the id is a reverb's: never the delay beside it
        ["fx:a1b2c3d4:reverb:decay", RISE],  # not whitelisted
        ["cutoff", RISE],
        _lane("reverb", "mix", [[0.0, 1.0]]),  # no shape
    ):
        assert fxauto.resolve(units, [lane]) == {}, lane
    x = _dry(SR)
    got, out = fxauto.render(x, units, ARR, [_lane("delay", "mix")], SR, 2.0)
    assert out is None and np.array_equal(got, fx.chain(x, build_chain(units, ARR)))


# --- accuracy: the swept filters against a coefficient set per sample --------------


def _per_sample(x, kind, param, values, **base):
    """The unit with `param` = `values`, one coefficient set PER SAMPLE:
    the reference the 64-sample blocks are measured against."""
    p = {k: d for k, (_lo, _hi, d) in FX_UNITS[kind]["params"].items()} | base
    p[param] = values
    if kind == "eq":
        return fx._eq_swept(x, block=1, **p)
    return np.column_stack(
        [biquad(x[:, c], kind, p["cutoff"], p["q"], SR, 1) for c in (0, 1)]
    )


# measured (dB below the signal, a full-range lane over a 2 s
# cycle of plucked saws; rise = start to end of the range, with the jump
# back at the loop point, tri = up and down): the pass itself, and the
# second of two passes against our fold. The fold adds nothing: a block
# starts on the cycle's end, so the lane restarts on that sample.
#   lowpass cutoff  -45.7 / -60.9     highpass cutoff -48.4 / -47.4
#   lowpass q       -63.9 / -64.1     highpass q      -61.3 / -58.3
#   eq low          -62.5 / -57.5     eq mid_gain     -67.1 / -61.2
#   eq high         -78.4 / -72.3     eq mid_freq     -39.2 / -52.9
BLOCK_ERROR_DB = -38.0


@pytest.mark.parametrize(("kind", "param"), SWEPT)
@pytest.mark.parametrize("shape", [RISE, TRI], ids=["rise", "tri"])
def test_a_swept_filter_lane_is_the_per_sample_sweep_within_its_blocks(
    kind, param, shape
):
    """One pass, the second of two passes (the loop: our fold of the ring
    onto the head) and the ring into a section at rest, each against the
    same thing rendered in one piece with a coefficient set per sample. The
    cycle is deliberately not a multiple of 64 samples."""
    n = 2 * SR + 8
    base = {"mid_gain": 9.0} if param == "mid_freq" else {}
    unit = _unit(kind, **base)
    lo, hi, _default = FX_UNITS[kind]["params"][param]
    x = _dry(n)
    loop, out = fxauto.render(x, [unit], ARR, [_lane(kind, param, shape)], n, 4.0)
    two = np.zeros((3 * n, 2))
    two[: 2 * n] += x
    two[n:] += x
    truth = _per_sample(
        two, kind, param, fxauto.values(shape, lo, hi, n, 3 * n), **base
    )
    assert _db(truth[:n] - loop[:n], truth[:n]) < BLOCK_ERROR_DB
    folded = loop[:n] + loop[n:]
    assert _db(truth[n : 2 * n] - folded, truth[n : 2 * n]) < BLOCK_ERROR_DB
    # ... and into another section, which plays its own notes at rest
    y = _dry(n, seed=5)
    seq = np.zeros((3 * n, 2))
    seq[: 2 * n] += x
    seq[n:] += y
    rest = unit["params"][param]
    resting = fxauto.values(shape, lo, hi, n, 3 * n, rest)
    truth = _per_sample(seq, kind, param, resting, **base)
    ours = _per_sample(y, kind, param, np.full(2 * n, rest), **base)[:n] + out[n:]
    assert _db(truth[n : 2 * n] - ours, truth[n : 2 * n]) < -80.0


# --- the two tails -----------------------------------------------------------------


@pytest.mark.parametrize(("kind", "param"), LINEAR)
def test_the_ring_into_the_next_pass_replays_the_lane_exactly(kind, param):
    """A stem's ring is folded onto the head of the next pass, where the
    lane has started again. For a linear unit that fold IS two passes
    rendered in one piece (measured: -250 dB or better, i.e. rounding)."""
    n = 2 * SR + 8
    x = _dry(n)
    lanes = [_lane(kind, param)]
    loop, _out = fxauto.render(x, [_unit(kind)], ARR, lanes, n, 4.0)
    two = np.zeros((3 * n, 2))
    two[: 2 * n] += x
    two[n:] += x
    truth, _ = fxauto.render(two, [_unit(kind)], ARR, lanes, n, 6.0)
    folded = loop[:n] + loop[n:]
    assert _db(truth[n : 2 * n] - folded, truth[n : 2 * n]) < -200.0


@pytest.mark.parametrize(("kind", "param"), LINEAR)
def test_the_ring_into_another_section_rests_at_the_racks_value_exactly(kind, param):
    """Into a section that does not automate the parameter, the unit sits
    at the rack's value: the ring (`out`) plus that section's own plain
    render is the two sections rendered in one piece."""
    n = 2 * SR + 8
    unit = _unit(kind)
    x, y = _dry(n), _dry(n, seed=5)
    lane = _lane(kind, param)
    _loop, out = fxauto.render(x, [unit], ARR, [lane], n, 4.0)
    lo, hi, _default = FX_UNITS[kind]["params"][param]
    seq = np.zeros((3 * n, 2))
    seq[: 2 * n] += x
    seq[n:] += y
    # in one piece: the lane over the first section, the rack's value after
    arrays = {param: fxauto.values(RISE, lo, hi, n, 3 * n, unit["params"][param])}
    truth = fxauto._unit(seq, unit, ARR, arrays, n)
    ours = fx.chain(y, build_chain([unit], ARR))[:n] + out[n:]
    assert _db(truth[n : 2 * n] - ours, truth[n : 2 * n]) < -200.0
    # the other tail is not this one: the lane restarts at 0, the rack rests
    assert _db(_loop[n:] - out[n:], out[n:]) > -40.0


def test_the_ring_into_a_section_that_automates_the_parameter_too_is_approximate():
    """The one case the rule does not get exactly: the ring of section A
    enters section B, which has its own lane on the same parameter. The ring
    plays at the rack's value (0.25 here), not at B's lane (falling from 1).
    Measured on about the worst case, a reverb's mix rising 0 -> 1 in A and
    falling 1 -> 0 in B (the ring should be all room, and is a quarter
    room): B's first pass differs from the one-piece render by -11.5 dB
    over the pass, -5.9 dB in its first quarter second — the ring alone;
    B's own notes follow B's lane exactly."""
    n = 2 * SR + 8
    unit = _unit("reverb")
    x, y = _dry(n), _dry(n, seed=5)
    fall = [[0.0, 1.0], [1.0, 0.0]]
    _loop, out = fxauto.render(x, [unit], ARR, [_lane("reverb", "mix")], n, 4.0)
    own, _ = fxauto.render(y, [unit], ARR, [_lane("reverb", "mix", fall)], n, 4.0)
    ours = own[:n] + out[n:]
    seq = np.zeros((3 * n, 2))
    seq[: 2 * n] += x
    seq[n:] += y
    mix = np.concatenate(
        [fxauto.values(RISE, 0.0, 1.0, n, n), fxauto.values(fall, 0.0, 1.0, n, 2 * n)]
    )
    truth = fxauto._unit(seq, unit, ARR, {"mix": mix}, n)[n : 2 * n]
    whole = _db(truth - ours, truth)
    head = _db((truth - ours)[: SR // 4], truth[: SR // 4])
    assert -15.0 < whole < -8.0 and -10.0 < head < -3.0, (whole, head)


# --- a section: the lane is heard --------------------------------------------------


def _fx_song(kind: str, param: str, points=RISE, bars=2, **params):
    """The Lead alone (bright, short release) through one FX unit with a
    lane on `param`. Returns (song, section id, ids, the unit's id)."""
    s, sid, ids = _song(bars=bars, patch=BRIGHT)
    _only(s, ids, "lead")
    lead = s["tracks"][0]
    lead["fx"] = clean_chain([{"kind": kind, "params": params}])
    uid = unit_id(lead["fx"], 0)
    sm.set_lane(s, sid, ids["lead"], fx_target(uid, kind, param), points)
    return s, sid, ids, uid


def _static(s, sid, ids, uid, kind, param, value):
    """The same section with the unit's parameter standing still at
    `value` (0..1 of its range): one whole-section pass, as a lane makes."""
    ref = json.loads(json.dumps(s))
    sm.set_lane(
        ref, sid, ids["lead"], fx_target(uid, kind, param), [[0, value], [1, value]]
    )
    return section_audio(_parts(ref, sid))


def test_a_reverb_mix_lane_brings_the_room_up_across_the_pass():
    """Mix 0 -> 1 over the section: it starts as the dry notes and ends as
    the room alone; what is not the dry signal grows through the pass, and
    the section's ring is the wet one."""
    s, sid, ids, uid = _fx_song("reverb", "mix", decay=1.5)
    swept = section_audio(_parts(s, sid))
    dry = _static(s, sid, ids, uid, "reverb", "mix", 0.0)
    wet = _static(s, sid, ids, uid, "reverb", "mix", 1.0)
    n = len(swept.base)
    q = n // 4
    room = swept.base - dry.base
    assert _rms(room[:q]) < 0.3 * _rms(room[-q:])  # measured 0.18
    np.testing.assert_allclose(swept.base[:64], dry.base[:64], atol=1e-3)
    np.testing.assert_allclose(swept.base[-64:], wet.base[-64:], atol=1e-3)
    # late energy: the room is in the last quarter, not in the first
    assert _rms(room[-q:]) > 0.5 * _rms(wet.base[-q:])
    # between the notes' own level and the room's, everywhere
    assert _rms(swept.base) > 0.2 * _rms(dry.base)


def test_a_lowpass_cutoff_lane_opens_the_spectrum():
    s, sid, _ids, _uid = _fx_song("lowpass", "cutoff", q=0.7)
    swept = section_audio(_parts(s, sid)).base
    q = len(swept) // 4
    lo, hi = _centroid(swept[:q]), _centroid(swept[-q:])
    assert hi > 2 * lo, (lo, hi)  # measured 785 Hz -> 2.0 kHz
    # ... and closing it does the opposite
    s, sid, _ids, _uid = _fx_song("lowpass", "cutoff", [[0.0, 1.0], [1.0, 0.0]])
    shut = section_audio(_parts(s, sid)).base
    assert _centroid(shut[:q]) > 2 * _centroid(shut[-q:])


def _band_db(x, lo: float, hi: float) -> float:
    mono = x.mean(axis=1) * np.hanning(len(x))
    mag = np.abs(np.fft.rfft(mono)) ** 2
    f = np.fft.rfftfreq(len(mono), 1 / SR)
    return 10 * np.log10(np.sum(mag[(f >= lo) & (f < hi)]) + 1e-30)


def test_an_eq_gain_lane_lifts_its_band_and_leaves_the_rest():
    """The mid band at 1 kHz from -12 to +12 dB: against the flat EQ, the
    band around 1 kHz is cut at the start and lifted at the end, by most of
    the 12 dB each way; two octaves below it nothing moves."""
    s, sid, ids, uid = _fx_song("eq", "mid_gain", mid_freq=1000.0, mid_q=1.0)
    swept = section_audio(_parts(s, sid)).base
    flat = _static(s, sid, ids, uid, "eq", "mid_gain", 0.5).base  # 0 dB
    q = len(swept) // 8
    first = _band_db(swept[:q], 800, 1250) - _band_db(flat[:q], 800, 1250)
    last = _band_db(swept[-q:], 800, 1250) - _band_db(flat[-q:], 800, 1250)
    assert first < -7.0 and last > 7.0, (first, last)  # measured -9.8, +9.8
    low = _band_db(swept[-q:], 60, 200) - _band_db(flat[-q:], 60, 200)
    assert abs(low) < 1.0, low


def test_a_delay_feedback_lane_lengthens_the_echoes():
    """Feedback 0 -> 0.9: the section's ring into ANOTHER section is at the
    rack's feedback (0.35), into its own next pass at the lane's — and the
    last quarter of the pass carries more echo than the first."""
    s, sid, ids, uid = _fx_song("delay", "feedback", time=0.2, mix=0.5, sync=0.0)
    audio = section_audio(_parts(s, sid))
    none = _static(s, sid, ids, uid, "delay", "feedback", 0.0)
    q = len(audio.base) // 4
    echo = audio.base - none.base
    assert _rms(echo[-q:]) > 4 * _rms(echo[:q])  # measured 15x
    assert audio.out is not None and len(audio.out) == len(audio.tail)
    assert not np.allclose(audio.out, audio.tail)


def test_a_drive_amount_lane_adds_harmonics_as_it_rises():
    s, sid, _ids, _uid = _fx_song("drive", "amount")
    s["tracks"][0]["patch"] = Patch(
        name="sine", osc1="sine", cutoff=16000.0, release=0.05
    ).to_json()
    swept = section_audio(_parts(s, sid)).base
    q = len(swept) // 4
    assert _centroid(swept[-q:]) > 1.2 * _centroid(swept[:q])  # measured 1.35


# --- a section: cycles, chunks, repeats --------------------------------------------


def _busy() -> tuple[dict, str, dict]:
    """FX lanes of every method at once, beside phase-1 lanes: the Lead's
    reverb mix and lowpass cutoff (and its level), the Pad's delay feedback
    (a note held over the bar-2 seam), the drums' EQ."""
    s, sid, ids = _song(patch=BRIGHT)
    lead, pad, drums = s["tracks"][0], s["tracks"][1], s["tracks"][2]
    lead["fx"] = clean_chain(
        [{"kind": "lowpass", "params": {}}, {"kind": "reverb", "params": {}}]
    )
    pad["fx"] = clean_chain([{"kind": "delay", "params": {"sync": 4.0}}])
    drums["fx"] = clean_chain([{"kind": "eq", "params": {}}])
    lanes = (
        ("lead", lead, 0, "lowpass", "cutoff", TRI),
        ("lead", lead, 1, "reverb", "mix", RISE),
        ("pad", pad, 0, "delay", "feedback", RISE),
        ("drums", drums, 0, "eq", "high", TRI),
    )
    for name, track, i, kind, param, points in lanes:
        target = fx_target(unit_id(track["fx"], i), kind, param)
        sm.set_lane(s, sid, ids[name], target, points)
    sm.set_lane(s, sid, ids["lead"], "gain", [[0.0, 0.2], [1.0, 0.5]])
    sm.set_lane(s, sid, ids["lead"], "resonance", RISE)
    return s, sid, ids


def test_an_fx_lane_makes_the_section_one_flat_cycle_and_rides_its_stem():
    s, sid, ids, uid = _fx_song("reverb", "mix", bars=4)
    sec = _sec(s, sid)
    assert sm.cycle_bars(s, sec) == 4  # the 1-bar riff no longer loops
    parts = _parts(s, sid)
    assert parts.flat and parts.cycle_bars == 4
    assert parts.fx_auto == {"main": [[fx_target(uid, "reverb", "mix"), RISE]]}
    assert not parts.auto and not parts.gains
    # the Pad is a layer: its lanes ride its own stem's name
    s, sid, ids = _busy()
    fx_auto = _parts(s, sid).fx_auto
    assert set(fx_auto) == {"main", "layer2", "drums"}
    assert [parse_fx(t)[1:] for t, _pts in fx_auto["main"]] == [
        ("lowpass", "cutoff"),
        ("reverb", "mix"),
    ]


def test_an_fx_automated_cycle_in_chunks_is_the_cycle_in_one_piece(monkeypatch):
    """The chain runs once over the stitched cycle and the lane is read
    over the cycle, never the chunk: two 2-bar chunks are the one-piece
    render at the tolerance the un-automated chunk tests hold (1e-9) — the
    cycle, its ring and its ring into another section."""
    s, sid, _ids = _busy()
    parts = _parts(s, sid)
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


def test_the_stem_cache_serves_an_fx_automated_section_again():
    s, sid, _ids = _busy()
    parts = _parts(s, sid)
    cache = StemCache()
    first = section_audio(parts, stem_cache=cache)
    again = section_audio(parts, stem_cache=cache)
    assert np.array_equal(first.base, again.base)
    assert np.array_equal(first.tail, again.tail)
    assert np.array_equal(first.out, again.out)
    # a level lane holds its last value over both rings
    lead = [k for k in cache.keys() if k.startswith("cycle-main")]
    assert len(lead) == 1 and isinstance(cache.get(lead[0]), render._LaneStem)


def test_the_master_chain_runs_over_both_rings():
    s, sid, _ids, _uid = _fx_song("reverb", "mix", decay=2.0)
    parts = _parts(s, sid)
    plain = section_audio(parts)
    master = [{"kind": "drive", "params": {"amount": 1.5}}]
    out = section_master_audio(parts, master)
    assert out.out is not None and not np.allclose(out.out[:4096], out.tail[:4096])
    assert np.array_equal(out.handoff, out.out)
    chain = build_chain(clean_chain(master), parts.arr)
    n = len(plain.base)
    want = fx.chain(np.concatenate([plain.base, plain.out]), chain)[n:]
    np.testing.assert_allclose(out.out, want[: len(out.out)], atol=1e-12)
    # without FX lanes there is one ring, as ever
    bare = json.loads(json.dumps(s))
    sm.remove_lane(
        bare,
        sid,
        bare["tracks"][0]["id"],
        _sec(s, sid)["auto"].popitem()[1][0]["target"],
    )
    one = section_master_audio(_parts(bare, sid), master)
    assert one.out is None and one.handoff is one.tail


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _pcm(wav: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2)


def test_every_repeat_replays_the_lane_and_the_next_section_gets_the_resting_ring(
    client,
):
    """A reverb-mix lane on a section played three times, then a second
    section: the plan is three passes of one whole-section cycle, the export
    is those passes stitched, the room comes up in every pass, the loop pass
    folds the ring that replays the lane and the next section's head folds
    the ring at rest."""
    s, sid, ids, uid = _fx_song("reverb", "mix", decay=1.5)
    _sec(s, sid)["repeats"] = 3
    after = sm.add_section(s, "After", 1)
    use_song(client, s)
    plan = client.get("/song/plan.json").json()
    build, tail = plan["sections"]
    n = sm.cycle_samples(2, 120.0)
    assert (build["cycle_bars"], build["cycles"], build["passes"]) == (2, 1, 3)
    export = _pcm(client.get("/export/song.wav").content)
    first = _pcm(client.get(build["url"]).content)
    loop = _pcm(client.get(build["loop_url"]).content)
    rest = _pcm(client.get(tail["url"]).content)
    assert np.array_equal(export, np.vstack([first, loop, loop, rest]))
    assert after and len(rest) == n // 2
    audio = section_audio(_parts(s, sid))
    gain = plan["gain"]
    want_loop = render.fold_tail(audio.base, audio.tail) * gain
    assert np.max(np.abs(loop / 32767.0 - want_loop)) < 2e-4
    # the empty section after it is nothing but the ring at rest
    want_rest = audio.out[: n // 2] * gain
    assert np.max(np.abs(rest / 32767.0 - want_rest)) < 2e-4
    half = n // 2  # ... which is not the ring the loop pass folds
    assert _rms(audio.out[:half] - audio.tail[:half]) > 0.5 * _rms(audio.out[:half])
    # the stems export and the solo audition play the lane too
    with zipfile.ZipFile(io.BytesIO(client.get("/export/stems.zip").content)) as z:
        lead = _pcm(z.read("synth-1.wav"))
    assert np.array_equal(lead, export)  # the one sounding track, no master
    solo = _pcm(client.get(f"/song/cell/{sid}/{ids['lead']}.wav").content)
    assert np.array_equal(solo, loop)
    # the MIDI export carries no automation
    mid = client.get("/export/song.mid").content
    sm.remove_lane(
        client.app.state.studio.song, sid, ids["lead"], fx_target(uid, "reverb", "mix")
    )
    sec = client.app.state.studio.song["sections"][0]
    sm.set_lane(
        client.app.state.studio.song, sid, ids["lead"], "gain", [[0, 0.4], [1, 0.4]]
    )
    assert sm.cycle_bars(client.app.state.studio.song, sec) == 2
    assert client.get("/export/song.mid").content == mid


# --- keys: absent means absent -----------------------------------------------------


def test_a_song_without_fx_lanes_keeps_its_keys_and_its_stems():
    """Nothing about a lane-free section moves: no `fx_auto`, no `out`, no
    id on any unit, the same stem keys (a plain ndarray in the cache)."""
    s, sid, ids = _song(patch=BRIGHT)
    s["tracks"][0]["fx"] = clean_chain([{"kind": "reverb", "params": {}}])
    sm.set_lane(s, sid, ids["lead"], "cutoff", RISE)  # a patch lane: a flat cycle
    s = schema.conform(s, N.SONG)
    assert all("id" not in u for t in s["tracks"] for u in t["fx"])
    parts = _parts(s, sid)
    assert parts.fx_auto == {}
    cache = StemCache()
    audio = section_audio(parts, stem_cache=cache)
    assert audio.out is None and audio.handoff is audio.tail
    assert all(isinstance(cache.get(k), np.ndarray) for k in cache.keys())
    key, stems = section_key(parts, None), sorted(cache.keys())
    # an FX lane moves the section's key and its stem's; removing it (the
    # unit keeps its id) moves the stem's key only by that id
    with_lane = json.loads(json.dumps(s))
    target = sm.claim_fx_target(with_lane["tracks"][0], "fx:@0:reverb:mix")
    sm.set_lane(with_lane, sid, ids["lead"], target, RISE)
    moved = _parts(with_lane, sid)
    assert section_key(moved, None) != key
    cache2 = StemCache()
    section_audio(moved, stem_cache=cache2)
    assert set(cache2.keys()) & set(stems) == set(stems) - {
        k for k in stems if k.startswith("cycle-main")
    }


def test_a_missing_unit_leaves_the_section_as_if_the_lane_were_not_there():
    """The unit is removed from the rack: the lane stays in the song, is
    read by nothing, and the section renders and keys exactly as the one
    without it — even its cycle (the lane no longer makes it one pass)."""
    s, sid, ids, uid = _fx_song("reverb", "mix", bars=4)
    target = fx_target(uid, "reverb", "mix")
    bare = json.loads(json.dumps(s))
    bare["tracks"][0]["fx"] = []
    sm.remove_lane(bare, sid, ids["lead"], target)
    gone = json.loads(json.dumps(s))
    gone["tracks"][0]["fx"] = []
    gone = schema.conform(gone, N.SONG)
    assert sm.cell_lanes(_sec(gone, sid), ids["lead"])[0]["target"] == target
    assert sm.section_lanes(gone, _sec(gone, sid)) == {}
    assert sm.cycle_bars(s, _sec(s, sid)) == 4
    assert sm.cycle_bars(gone, _sec(gone, sid)) == 2  # the patterns' own again
    a, b = _parts(gone, sid), _parts(bare, sid)
    assert section_key(a, None) == section_key(b, None)
    assert np.array_equal(section_audio(a).base, section_audio(b).base)
    # another unit of the same kind in its place is not the lane's unit
    again = json.loads(json.dumps(gone))
    again["tracks"][0]["fx"] = clean_chain([{"kind": "reverb", "params": {}}])
    assert sm.section_lanes(again, _sec(again, sid)) == {}
    # a unit carrying the id but of another kind is not it either
    other = json.loads(json.dumps(gone))
    other["tracks"][0]["fx"] = clean_chain([{"kind": "delay", "params": {}, "id": uid}])
    assert sm.section_lanes(other, _sec(other, sid)) == {}
    assert _parts(other, sid).fx_auto == {}


def test_a_lane_follows_its_unit_through_a_reordered_rack():
    """Two reverbs: the lane is on the long one. Swapped in the rack, the
    lane still plays the long one — the render is the swapped chain's with
    the lane on that unit, and it is not the render with the lane on the
    short one."""
    s, sid, ids = _song(bars=2, patch=BRIGHT)
    _only(s, ids, "lead")
    lead = s["tracks"][0]
    lead["fx"] = clean_chain(
        [
            {"kind": "reverb", "params": {"decay": 0.3, "mix": 0.5}},
            {"kind": "drive", "params": {"amount": 3.0}},
            {"kind": "reverb", "params": {"decay": 3.0, "mix": 0.5}},
        ]
    )
    long_id = unit_id(lead["fx"], 2)
    target = fx_target(long_id, "reverb", "mix")
    sm.set_lane(s, sid, ids["lead"], target, RISE)
    assert [label for label, _c in sm.fx_lane_menu(lead)] == [
        "Reverb 1",
        "Drive",
        "Reverb 2",
    ]
    before = section_audio(_parts(s, sid)).base
    lead["fx"][0], lead["fx"][2] = lead["fx"][2], lead["fx"][0]
    s2 = schema.conform(s, N.SONG)
    assert s2["tracks"][0]["fx"][0]["id"] == long_id  # the id moved with it
    parts = _parts(s2, sid)
    assert parts.fx_auto == {"main": [[target, RISE]]}
    swapped = section_audio(parts).base
    assert not np.allclose(swapped, before)  # the chain's order is heard
    # by hand: the long reverb first, its mix rising, then drive, then short
    units = clean_chain(s2["tracks"][0]["fx"])
    assert fxauto.resolve(units, [[target, RISE]]) == {0: {"mix": RISE}}
    short = json.loads(json.dumps(s2))
    short_id = unit_id(short["tracks"][0]["fx"], 2)
    sm.remove_lane(short, sid, ids["lead"], target)
    sm.set_lane(short, sid, ids["lead"], fx_target(short_id, "reverb", "mix"), RISE)
    assert not np.allclose(section_audio(_parts(short, sid)).base, swapped)


# --- the model and the schema ------------------------------------------------------


def test_a_unit_gets_its_id_when_a_lane_first_targets_it_and_keeps_it():
    s, sid, ids = _song()
    lead = s["tracks"][0]
    lead["fx"] = clean_chain(
        [{"kind": "width", "params": {}}, {"kind": "delay", "params": {}}]
    )
    saved = json.dumps(schema.conform(s, N.SONG))
    # the menu: width has nothing a lane can move; the delay is named by place
    assert sm.fx_lane_menu(lead) == [
        ("Delay", [("fx:@1:delay:feedback", "feedback"), ("fx:@1:delay:mix", "mix")])
    ]
    assert sm.lane_targets(lead)[-1] == "pitch_bend" or "fx:" not in "".join(
        sm.lane_targets(lead)
    )
    assert json.dumps(schema.conform(s, N.SONG)) == saved  # reading changes nothing
    target = sm.claim_fx_target(lead, "fx:@1:delay:mix")
    uid = lead["fx"][1]["id"]
    assert target == f"fx:{uid}:delay:mix" and "id" not in lead["fx"][0]
    assert (
        sm.claim_fx_target(lead, "fx:@1:delay:feedback") == f"fx:{uid}:delay:feedback"
    )
    assert (
        sm.claim_fx_target(lead, target) == target
        and sm.claim_fx_target(lead, "gain") == "gain"
    )
    assert sm.lane_targets(lead)[-2:] == (f"fx:{uid}:delay:feedback", target)
    assert sm.fx_lane_menu(lead)[0][1][1] == (target, "mix")
    # the lane starts where the unit's slider is
    lo, hi = lane_range(target)
    assert sm.lane_rest(lead, target) == (lead["fx"][1]["params"]["mix"] - lo) / (
        hi - lo
    )
    sm.set_lane(s, sid, ids["lead"], target, RISE)
    assert schema.conform(s, N.SONG)["tracks"][0]["fx"][1]["id"] == uid
    for bad in (
        "fx:@0:delay:mix",
        "fx:@1:reverb:mix",
        "fx:@9:delay:mix",
        "fx:@x:delay:mix",
        "fx:@1:delay:time",
        "fx:@1:delay",
    ):
        with pytest.raises(sm.SongError):
            sm.claim_fx_target(lead, bad)
    # drums: the level and its units
    drums = s["tracks"][2]
    drums["fx"] = clean_chain([{"kind": "eq", "params": {}}])
    assert len(sm.fx_lane_menu(drums)[0][1]) == 4
    eq = sm.claim_fx_target(drums, "fx:@0:eq:low")
    assert sm.lane_targets(drums)[0] == "gain" and eq in sm.lane_targets(drums)
    sm.set_lane(s, sid, ids["drums"], eq, TRI)
    assert schema.conform(s, N.SONG)["sections"][0]["auto"][ids["drums"]] == [
        {"target": eq, "points": TRI}
    ]
    # audio: its level, and its rack's units once it has some (its lanes
    # move its takes: `test_audio_fx.py`)
    assert sm.fx_lane_menu(s["tracks"][3]) == []
    assert sm.lane_targets(s["tracks"][3]) == ("gain",)
    with pytest.raises(sm.SongError):  # a unit the track does not have
        sm.set_lane(s, sid, ids["lead"], "fx:ffffffff:reverb:mix", RISE)


def test_a_cell_still_holds_four_lanes():
    s, sid, ids, uid = _fx_song("delay", "mix")
    sm.set_lane(s, sid, ids["lead"], fx_target(uid, "delay", "feedback"), RISE)
    sm.set_lane(s, sid, ids["lead"], "gain", RISE)
    sm.set_lane(s, sid, ids["lead"], "cutoff", RISE)
    with pytest.raises(sm.SongError, match="at most"):
        sm.set_lane(s, sid, ids["lead"], "resonance", RISE)
    assert len(sm.cell_lanes(_sec(s, sid), ids["lead"])) == limits.MAX_CELL_LANES


def test_clean_chain_keeps_a_units_id_and_only_a_real_one():
    keep = clean_chain([{"kind": "reverb", "params": {}, "id": "0123abcd"}])
    assert keep[0]["id"] == "0123abcd" and list(keep[0]) == ["kind", "params", "id"]
    for bad in ("0123ABCD", "0123abc", "0123abcde", "<script>", "", 7, None, ["a"], {}):
        (u,) = clean_chain([{"kind": "reverb", "params": {}, "id": bad}])
        assert "id" not in u, bad
    # an id names one unit: the second claim loses it
    a, b = clean_chain([{"kind": "reverb", "id": UID}, {"kind": "delay", "id": UID}])
    assert a["id"] == UID and "id" not in b
    # a chain no lane touched is stored as it always was
    plain = clean_chain([{"kind": "reverb", "params": {"mix": 0.3}}])
    assert list(plain[0]) == ["kind", "params"]
    assert clean_chain(keep) == keep


def test_hostile_fx_lanes_are_dropped_or_kept_inert_never_a_crash():
    s, sid, ids, uid = _fx_song("reverb", "mix")
    good = fx_target(uid, "reverb", "mix")
    sec = _sec(s, sid)
    sec["auto"][ids["lead"]] = [
        {"target": good, "points": [[2.0, 9.0], [-1.0, -3.0]]},  # clamped, sorted
        {"target": "fx:ffffffff:reverb:mix", "points": RISE},  # no such unit: kept
        {"target": "fx:a1b2c3d4:reverb:decay", "points": RISE},  # not whitelisted
        {"target": "fx:@0:reverb:mix", "points": RISE},  # a menu slot
        {"target": good, "points": RISE},  # a second lane on the target
        {"target": ["fx"], "points": RISE},
        {"target": "fx:" + "a" * 5000, "points": RISE},
    ]
    # an audio track with no take in the section: nothing to automate
    sec["auto"][ids["vox"]] = [{"target": good, "points": RISE}]
    out = schema.conform(s, N.SONG)
    assert schema.conform(out, N.SONG) == out
    assert out["sections"][0]["auto"] == {
        ids["lead"]: [
            {"target": good, "points": [[0.0, 0.0], [1.0, 1.0]]},
            {"target": "fx:ffffffff:reverb:mix", "points": RISE},
        ]
    }
    # only the lane whose unit exists is read
    assert list(sm.section_lanes(out, out["sections"][0])[ids["lead"]]) == [
        {"target": good, "points": [[0.0, 0.0], [1.0, 1.0]]}
    ]
    section_audio(_parts(out, sid))  # and it renders


# --- the route, the drawer, undo ---------------------------------------------------


def _auto(client, **data):
    base = {"section_id": "s1", "track_id": "t1"}
    return post(client, "/song/cell/auto", base | {k: str(v) for k, v in data.items()})


def _lanes(client, sid="s1", tid="t1") -> list[dict]:
    sec = next(x for x in song(client)["sections"] if x["id"] == sid)
    return sm.cell_lanes(sec, tid)


def _fx(client, tid="t1") -> list[dict]:
    return next(t for t in song(client)["tracks"] if t["id"] == tid)["fx"]


@pytest.fixture
def studio(tmp_path):
    """The song routes' fixture song with a reverb and a lowpass on the
    Bass (t1) and an EQ on the drums (td); no unit has an id."""
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        s = c.app.state.studio
        s.song["tracks"][1]["fx"] = clean_chain(
            [
                {"kind": "reverb", "params": {"mix": 0.25}},
                {"kind": "lowpass", "params": {"cutoff": 1000.0}},
            ]
        )
        s.song["tracks"][0]["fx"] = clean_chain([{"kind": "eq", "params": {}}])
        yield c


def test_adding_an_fx_lane_names_the_unit_and_starts_at_its_slider(studio):
    assert all("id" not in u for u in _fx(studio))
    r = _auto(studio, op="add", target="fx:@0:reverb:mix")
    assert r.status_code == 200, r.text[:300]
    uid = _fx(studio)[0]["id"]
    assert "id" not in _fx(studio)[1]  # only the unit the lane targets
    target = f"fx:{uid}:reverb:mix"
    assert _lanes(studio) == [{"target": target, "points": [[0.0, 0.25], [1.0, 0.25]]}]
    assert {"song-drawer", "song-arranger"} & oob_ids(r.text) == {"song-arranger"}
    refused(_auto(studio, op="add", target="fx:@0:reverb:mix"), "already")
    refused(_auto(studio, op="add", target=target), "already")
    r = _auto(studio, op="set", target=target, start="0", end="1", mid_on="on")
    assert r.status_code == 200 and "s1" in changed(r)
    assert _lanes(studio)[0]["points"] == [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]]
    r = _auto(studio, op="remove", target=target)
    assert r.status_code == 200 and _lanes(studio) == []
    assert _fx(studio)[0]["id"] == uid  # the unit keeps its name


@pytest.mark.parametrize(
    ("data", "word"),
    [
        ({"op": "add", "target": "fx:@5:reverb:mix"}, "no such effect"),
        ({"op": "add", "target": "fx:@1:reverb:mix"}, "no such effect"),
        ({"op": "add", "target": "fx:@0:reverb:decay"}, "no such effect"),
        ({"op": "add", "target": "fx:@-1:reverb:mix"}, "no such effect"),
        ({"op": "add", "target": "fx:@0"}, "no such effect"),
        ({"op": "add", "target": "fx:@0:reverb:mix:x"}, "no such effect"),
        ({"op": "add", "target": "fx:ffffffff:reverb:mix"}, "automate that"),
        ({"op": "add", "target": "fx:zz:reverb:mix"}, "automate that"),
        (
            {"op": "set", "target": "fx:@0:reverb:mix", "start": "0", "end": "1"},
            "automate",
        ),
        (
            {"op": "set", "target": "fx:ffffffff:reverb:mix", "start": "0", "end": "1"},
            "automate",
        ),
        ({"op": "add", "target": "fx:@0:eq:low", "track_id": "ta"}, "no such effect"),
    ],
)
def test_a_hostile_fx_lane_post_is_a_toast_and_changes_nothing(studio, data, word):
    before = json.dumps(song(studio))
    refused(_auto(studio, **data), word)
    assert json.dumps(song(studio)) == before  # no id was handed out either


def test_the_drawer_offers_each_units_parameters_and_draws_the_lane(studio):
    post(studio, "/song/drawer", {"section_id": "s1", "track_id": "t1"})
    html = studio.get("/view/song", headers=HX).text
    menu = html[html.index('class="lane-add"') :]
    menu = menu[: menu.index("</form>")]
    assert '<optgroup label="Reverb">' in menu and '<optgroup label="Lowpass">' in menu
    assert '<option value="fx:@0:reverb:mix">Reverb mix</option>' in menu
    assert '<option value="fx:@1:lowpass:cutoff">Lowpass cutoff</option>' in menu
    assert "reverb:decay" not in menu  # not on the whitelist
    assert '<option value="gain">Level</option>' in menu
    _auto(studio, op="add", target="fx:@1:lowpass:cutoff")
    uid = _fx(studio)[1]["id"]
    target = f"fx:{uid}:lowpass:cutoff"
    _auto(studio, op="set", target=target, start="0", end="1")
    _auto(studio, op="add", target="fx:@0:reverb:mix")
    html = studio.get("/view/song", headers=HX).text
    drawer = html[html.index('id="song-drawer"') :]
    assert f'data-target="{target}"' in drawer and ">Lowpass cutoff<" in drawer
    plain = drawer.replace("<output data-untyped>", "<output>")
    assert "<output>100 Hz</output>" in plain and "<output>12000 Hz</output>" in plain
    assert ">Reverb mix<" in plain and "<output>25%</output>" in plain
    menu = drawer[drawer.index('class="lane-add"') :]
    # what is automated leaves the menu; the unit's other parameter stays,
    # now under the unit's id
    assert "lowpass:cutoff" not in menu and f"fx:{uid}:lowpass:q" in menu
    cell = html[html.index('id="cell-s1-t1"') :].split("</td>")[0]
    assert 'title="Automated: Lowpass cutoff, Reverb mix"' in cell
    # the drums' EQ, beside its level
    post(studio, "/song/drawer", {"section_id": "s1", "track_id": "td"})
    html = studio.get("/view/song", headers=HX).text
    menu = html[html.index('class="lane-add"') :]
    assert '<option value="fx:@0:eq:mid_gain">EQ mid gain</option>' in menu


def test_a_lane_whose_unit_was_removed_is_shown_missing_with_only_its_removal(studio):
    post(studio, "/song/drawer", {"section_id": "s1", "track_id": "t1"})
    _auto(studio, op="add", target="fx:@0:reverb:mix")
    target = _lanes(studio)[0]["target"]
    _auto(studio, op="set", target=target, start="0", end="1")
    s = studio.app.state.studio
    key = lambda: section_key(  # noqa: E731
        sm.section_arrangement(s.song, s.song["sections"][0]), None
    )
    with_lane = key()
    del s.song["tracks"][1]["fx"][0]  # the rack lost the reverb
    assert key() != with_lane
    html = studio.get("/view/song", headers=HX).text
    drawer = html[html.index('id="song-drawer"') :]
    row = drawer[drawer.index("lane-missing") :]
    row = row[: row.index("</div>")]
    assert "data-missing" in row and ">Reverb mix<" in row and "missing" in row
    assert "<input" not in row and 'aria-label="remove the Reverb mix lane"' in row
    assert studio.get("/song/plan.json").status_code == 200  # and it plays
    # its sliders are gone, so nothing can post a set; a hand-made one is refused
    refused(_auto(studio, op="set", target=target, start="0", end="1"), "automate")
    r = _auto(studio, op="remove", target=target)
    assert r.status_code == 200 and _lanes(studio) == []
    assert "lane-missing" not in studio.get("/view/song", headers=HX).text


def test_reordering_the_rack_on_synths_keeps_the_lane_on_its_unit(studio):
    """The track's sound opened on Synths, its rack reordered there (the
    units move as they are, id included) and mirrored back: the lane still
    names the reverb, now second."""
    _auto(studio, op="add", target="fx:@0:reverb:mix")
    target = _lanes(studio)[0]["target"]
    uid = _fx(studio)[0]["id"]
    assert post(studio, "/song/track/t1/edit-sound").status_code == 200
    ed = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
    r = studio.post(
        "/fragment/fx/move",
        data={"scope": "layer", "index": "0", "dir": "1"},
        headers=ed,
    )
    assert r.status_code == 200
    assert [(u["kind"], u.get("id")) for u in _fx(studio)] == [
        ("lowpass", None),
        ("reverb", uid),
    ]
    # a slider on the unit keeps its id too (clean_chain)
    studio.post(
        "/fragment/fx/set",
        data={"scope": "layer", "index": "1", "name": "decay", "value": "3"},
        headers=ed,
    )
    assert _fx(studio)[1] == {
        "kind": "reverb",
        "params": {"decay": 3.0, "mix": 0.25, "size": 0.8},
        "id": uid,
    }
    s = studio.app.state.studio
    assert sm.section_lanes(s.song, s.song["sections"][0])["t1"][0]["target"] == target
    # removed there: the lane goes missing
    studio.post(
        "/fragment/fx/remove", data={"scope": "layer", "index": "1"}, headers=ed
    )
    assert [u["kind"] for u in _fx(studio)] == ["lowpass"]
    assert sm.section_lanes(s.song, s.song["sections"][0]) == {}
    assert _lanes(studio)[0]["target"] == target


def test_an_fx_lane_is_one_undo_step_with_its_units_id(studio):
    from tests.studio.test_song_undo import labels, redo, undo

    before = json.dumps(song(studio))
    _auto(studio, op="add", target="fx:@0:reverb:mix")
    added = json.dumps(song(studio))
    target = _lanes(studio)[0]["target"]
    assert labels(studio)[0] == "add automation lane"
    # a dragged slider: many posts, one step (the key holds the target)
    for v in ("0.1", "0.4", "0.9"):
        _auto(studio, op="set", target=target, start="0", end=v)
    assert labels(studio)[0] == "automation"
    assert undo(studio).status_code == 200 and json.dumps(song(studio)) == added
    undo(studio)
    assert json.dumps(song(studio)) == before  # the lane and the id, together
    redo(studio)
    assert json.dumps(song(studio)) == added
    redo(studio)
    assert _lanes(studio)[0]["points"] == [[0.0, 0.0], [1.0, 0.9]]


# --- persistence -------------------------------------------------------------------


def test_fx_lanes_and_unit_ids_ride_the_project_file_and_a_restart(tmp_path):
    s, sid, _ids = _busy()
    want = schema.conform(s, N.SONG)
    with TestClient(create_app(data_dir=tmp_path / "a")) as a:
        use_song(a, s)
        a.app.state.studio.save()
        proj = a.get("/project.json").json()
        audio = a.get("/export/song.wav").content
    assert proj["song"]["sections"][0]["auto"] == want["sections"][0]["auto"]
    assert [t["fx"] for t in proj["song"]["tracks"]] == [
        t["fx"] for t in want["tracks"]
    ]
    with TestClient(create_app(data_dir=tmp_path / "a")) as again:  # a restart
        assert again.app.state.studio.song == want
    with TestClient(create_app(data_dir=tmp_path / "b")) as b:
        r = b.post(
            "/project/import",
            files={"file": ("x.synthlab.json", json.dumps(proj), "application/json")},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert b.app.state.studio.song == want
        b.app.state.studio.fx_master = []
        assert b.get("/export/song.wav").content == audio
        # the lanes are heard: without them the export is another one
        for sec in b.app.state.studio.song["sections"]:
            sec.pop("auto", None)
        assert b.get("/export/song.wav").content != audio
