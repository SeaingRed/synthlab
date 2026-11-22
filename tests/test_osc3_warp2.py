"""Oscillator 3 and a second warp per oscillator. Each is opt-in —
oscillator 3 silent and unread at its level's default, 0; a second warp
"off" — so a patch without them renders bit for bit as if they did not
exist (the gates below) and every older key stands. Each is held to what
it claims: a third layer summed as osc2 is, in every unison copy, at its
own octave, semitones and cents; two warps composed on one phase, on the
single warp's oversampled path, band-limited against a naive pair."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from synthlab import core, params
from synthlab import synth as synth_mod
from synthlab.core import SR
from synthlab.params import (
    MOD_DESTINATIONS,
    MOD_GENE_DESTINATIONS,
    OSC3_SHAPES,
    PARAMS,
    osc3_on,
    shapes_of,
)
from synthlab.studio.factory import FACTORY, get_factory
from synthlab.studio.library import LIBRARY, get_library
from synthlab.synth import Patch, Synth

OSC3_FIELDS = (
    "osc3 osc3_level osc3_octave osc3_semi osc3_detune osc3_warp osc3_warp_amt"
).split()
# what a patch at osc3 level 0 may hold without being heard
QUIET = dict(
    osc3="fm4", osc3_octave=2, osc3_semi=-5, osc3_detune=33.0, osc3_warp="pd",
    osc3_warp_amt=0.7,
)  # fmt: skip


def _all_shipped() -> list[Patch]:
    rows = [get_factory(e["name"]) for e in FACTORY]
    rows += [get_library(e["name"]) for e in LIBRARY]
    return [*rows, *synth_mod.PRESETS.values(), Patch()]


# --- the defaults: the old engine, untouched ---------------------------------------


def test_patches_without_osc3_never_reach_it(monkeypatch):
    """Every factory and library sound, every PRESET and the default patch
    render without touching oscillator 3: its fields cannot move their
    audio, so RENDER_REV stands."""

    def boom(*a, **k):
        raise AssertionError("oscillator 3 ran on a patch with its level at 0")

    monkeypatch.setattr(Synth, "_osc3", boom)
    for p in _all_shipped():
        assert not osc3_on(p)
        Synth(p).note("C3", 0.2, gate=0.15)
    # nor does a silent osc3 holding other settings, and it renders the same
    quiet = replace(Patch(), **QUIET)
    a = Synth(quiet).note("C3", 0.2, gate=0.15)
    assert np.array_equal(a, Synth(Patch()).note("C3", 0.2, gate=0.15))


def test_the_fields_are_appended_hand_made_and_out_of_older_keys():
    from synthlab.studio.render import KEY_ADDED_FIELDS, keyed_patch

    names = list(PARAMS)
    at = names.index("osc3")
    assert names[at : at + len(OSC3_FIELDS)] == OSC3_FIELDS  # one run, in order
    assert at > names.index("grain_scan")  # appended after the granular run
    for name in OSC3_FIELDS:
        assert PARAMS[name].gene is None and PARAMS[name].nogene, name
        assert KEY_ADDED_FIELDS[name] == getattr(Patch(), name)
    assert not set(OSC3_FIELDS) & set(keyed_patch(Patch()))
    assert Patch().osc3_level == 0.0 and PARAMS["osc3"].cap == OSC3_SHAPES
    # the recordings stay osc1 / osc2's
    assert "sampler" not in OSC3_SHAPES and "granular" not in OSC3_SHAPES
    for name in ("osc3_level", "osc3_detune", "osc3_warp_amt"):
        assert name in MOD_DESTINATIONS and name not in MOD_GENE_DESTINATIONS
        assert PARAMS[name].curve == PARAMS[name].cap


# --- what it plays -------------------------------------------------------------------


@pytest.mark.parametrize("unison", [1, 3])
def test_osc3_sums_as_osc2_does(unison):
    """A saw on osc3 at octave 1, a fifth and 10 cents is osc2's saw at
    octave 1 and 710 cents, sample for sample (the native stack plays
    osc2, the Python loop osc3: within 1e-9), in every unison copy."""
    base = Patch(osc1="sine", unison=unison, unison_detune=14.0)
    three = replace(base, osc3="saw", osc3_level=0.4, osc3_octave=1, osc3_semi=7,
                    osc3_detune=10.0)  # fmt: skip
    two = replace(base, osc2="saw", osc2_level=0.4, osc2_octave=1, osc2_detune=710.0)
    a = Synth(three).note("C3", 0.3, gate=0.2)
    b = Synth(two).note("C3", 0.3, gate=0.2)
    assert np.abs(a).max() > 0.05
    assert np.max(np.abs(a - b)) < 1e-9


def test_all_three_play_together():
    p = Patch(osc1="saw", osc2="square", osc2_level=0.5, osc3="triangle",
              osc3_level=0.5, osc3_octave=-1)  # fmt: skip
    without = replace(p, osc3_level=0.0)
    a = Synth(p).note("C3", 0.3, gate=0.2)
    b = Synth(without).note("C3", 0.3, gate=0.2)
    assert np.all(np.isfinite(a)) and np.max(np.abs(a - b)) > 0.05


@pytest.mark.parametrize("shape", OSC3_SHAPES)
def test_every_osc3_shape_renders(shape):
    p = Patch(osc1="sine", osc1_level=0.1, osc3=shape, osc3_level=0.8)
    y = Synth(p).note("A3", 0.25, gate=0.2)
    assert np.all(np.isfinite(y)) and np.abs(y).max() > 0.01, shape


def test_the_level_is_array_safe_and_routable():
    """A drawn level rising from 0 opens osc3 (the gate is `np.any`), and a
    route onto a silent osc3's level lifts it into hearing."""
    n = 22050
    curve = Patch(osc1="sine", osc3="saw", osc3_level=np.linspace(0, 1, n))
    flat = replace(curve, osc3_level=0.0)
    a = Synth(curve).note("C3", 0.5, gate=0.4)
    b = Synth(flat).note("C3", 0.5, gate=0.4)
    assert np.max(np.abs(a[: n // 50] - b[: n // 50])) < 0.05  # quiet at first
    assert np.max(np.abs(a - b)) > 0.1
    routed = Patch(osc1="sine", osc3="saw", mod1_src="lfo", mod1_dst="osc3_level",
                   mod1_amt=0.8)  # fmt: skip
    still = replace(routed, mod1_amt=0.0)
    a = Synth(routed).note("C3", 0.5, gate=0.4)
    b = Synth(still).note("C3", 0.5, gate=0.4)
    assert np.max(np.abs(a - b)) > 0.05


def test_osc3_warps_drifts_and_reads_the_shape_fields():
    p = Patch(osc1="sine", osc3="saw", osc3_level=0.6)
    plain = Synth(p).note("C3", 0.3, gate=0.2)
    warped = Synth(replace(p, osc3_warp="pd", osc3_warp_amt=0.6)).note(
        "C3", 0.3, gate=0.2
    )
    assert np.max(np.abs(plain - warped)) > 0.05
    # a shape the warp does not bend is the same samples
    fm = replace(p, osc3="fm")
    assert np.array_equal(
        Synth(fm).note("C3", 0.2),
        Synth(replace(fm, osc3_warp="pd", osc3_warp_amt=0.6)).note("C3", 0.2),
    )
    # the fm shape's own fields reach osc3
    a = Synth(replace(fm, fm_ratio=3.0)).note("C3", 0.2)
    assert np.max(np.abs(a - Synth(fm).note("C3", 0.2))) > 0.01
    # a drifting note starts osc3 at its own seeded phase (slot 2)
    seed = synth_mod.drift_seed(synth_mod.note_to_hz("C3"))
    assert synth_mod.drift_phase(seed, 2, 0) not in {
        synth_mod.drift_phase(seed, s, 0) for s in (0, 1)
    }
    d = replace(p, drift=0.4)
    y = Synth(d).note("C3", 0.3, gate=0.2)
    assert np.all(np.isfinite(y)) and np.max(np.abs(y - plain)) > 0.01


def test_liveness_and_shapes_follow_the_level():
    off = Patch(osc3="wavetable")
    on = replace(off, osc3_level=0.5)
    assert shapes_of(off) == ("saw", None) and shapes_of(on) == (
        "saw",
        None,
        "wavetable",
    )
    assert not PARAMS["wt_pos"].live(off) and PARAMS["wt_pos"].live(on)
    assert "wt_pos" in params.live(on) and "wt_pos" not in params.live(off)


# --- two warps at once ---------------------------------------------------------------

WARP2_FIELDS = [f"osc{k}_{part}" for k in (1, 2, 3) for part in ("warp2", "warp2_amt")]
MODES = core.WARP_MODES[1:]


def test_patches_without_a_second_warp_never_reach_it(monkeypatch):
    """Every shipped patch (the single-warp demos included) renders without
    the second warp's code: RENDER_REV stands."""

    def boom(*a, **k):
        raise AssertionError("a second warp ran on a patch without one")

    monkeypatch.setattr(core, "warp_twice", boom)
    monkeypatch.setattr(core, "warp_end_twice", boom)
    for p in _all_shipped():
        Synth(p).note("C3", 0.2, gate=0.15)
    # an amount with its warp off is no warp
    p = Patch(osc1_warp="pd", osc1_warp_amt=0.5)
    held = replace(p, osc1_warp2_amt=0.8)
    assert np.array_equal(Synth(p).note("C3", 0.2), Synth(held).note("C3", 0.2))


def test_the_second_warps_are_appended_hand_made_and_out_of_older_keys():
    from synthlab.studio.render import KEY_ADDED_FIELDS, keyed_patch

    names = list(PARAMS)
    at = names.index(WARP2_FIELDS[0])
    assert names[at : at + len(WARP2_FIELDS)] == WARP2_FIELDS  # one run, in order
    assert at > names.index("osc3_warp_amt")  # appended after osc 3's run
    for name in WARP2_FIELDS:
        assert PARAMS[name].gene is None and PARAMS[name].nogene, name
        assert KEY_ADDED_FIELDS[name] == getattr(Patch(), name)
    assert not set(WARP2_FIELDS) & set(keyed_patch(Patch()))
    for k in (1, 2, 3):
        assert getattr(Patch(), f"osc{k}_warp2") == "off"
        assert PARAMS[f"osc{k}_warp2"].cap == core.WARP_MODES
        dst = f"osc{k}_warp2_amt"
        assert dst in MOD_DESTINATIONS and dst not in MOD_GENE_DESTINATIONS


def test_the_second_warp_alone_is_the_first_set_to_it():
    a = Patch(osc1_warp2="mirror", osc1_warp2_amt=0.6)
    b = Patch(osc1_warp="mirror", osc1_warp_amt=0.6)
    assert synth_mod.warp_of(a, 1) == synth_mod.warp_of(b, 1) == ("mirror", 0.6)
    assert np.array_equal(Synth(a).note("C3", 0.2), Synth(b).note("C3", 0.2))
    both = Patch(
        osc1_warp="pd", osc1_warp_amt=0.3, osc1_warp2="pwm", osc1_warp2_amt=0.4
    )
    assert synth_mod.warp_of(both, 1) == ("pd", 0.3, "pwm", 0.4)
    # what a warp ignores, the second ignores too
    assert synth_mod.warp_of(replace(both, osc1="fm"), 1) is None
    synced = Patch(osc2="saw", osc2_sync="on", osc2_warp2="pd", osc2_warp2_amt=0.5)
    assert synth_mod.warp_of(synced, 2) is None


@pytest.mark.parametrize("first", MODES)
@pytest.mark.parametrize("second", MODES)
def test_two_warps_keep_the_phase_in_a_cycle_and_land_where_the_wrap_says(
    first, second
):
    """The composition stays in [0, 1] from 0, and `warp_end_twice` is the
    composed phase's left limit at the wrap (what its polyBLEP corrects):
    approached from below, held (quantize's last step, pwm's hold) or from
    above at 0 (the mirror)."""
    t = np.linspace(0, 1, 4001, endpoint=False)
    for a, a2 in ((0.0, 0.0), (0.3, 0.7), (1.0, 1.0), (0.7, 0.0)):
        u = core.warp_twice(first, np.full_like(t, a), second, np.full_like(t, a2), t)
        assert u[0] == 0.0 and np.all((u >= 0) & (u <= 1)), (a, a2)
        end = core.warp_end_twice(first, np.array([a]), second, np.array([a2]))[0]
        near = core.warp_twice(first, a, second, a2, 1.0 - 1e-11)
        assert abs(near - end) < 1e-6, (a, a2, near, end)


def test_the_mirror_at_zero_is_the_identity_at_the_cycles_end():
    """The one change to a single warp: the mirror at amount 0 maps 1 to 1
    (a second warp's input reaches 1). A single warp never sees 1."""
    assert core.warp_phase("mirror", 1.0, 0.0) == 1.0
    assert core.warp_phase("mirror", 1.0, 0.5) == 0.0
    t = np.linspace(0, 1, 4001, endpoint=False)
    for a in (0.0, 0.4, 1.0):
        c = 1.0 - 0.5 * a
        old = np.where(t < c, t / c, (1.0 - t) / np.maximum(1.0 - c, 1e-9))
        assert np.array_equal(core.warp_phase("mirror", t, np.full_like(t, a)), old)


def _alias_db(y: np.ndarray, f0: float) -> float:
    """Energy away from the multiples of f0 over the energy on them, dB."""
    p = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
    f = np.fft.rfftfreq(len(y), 1 / SR)
    near = np.abs(f / f0 - np.round(f / f0)) * f0 < 25
    return float(10 * np.log10(p[~near].sum() / p[near].sum()))


def _two(shape, first, second, naive=False):
    f0 = 880.0
    y = core.warped(shape, f0, SR, SR, first, 0.7, pos=0.6, naive=naive, mode2=second,
                    amount2=0.5)  # fmt: skip
    return _alias_db(y[2000:], f0)


SMOOTH_PAIRS = [
    ("bend_pos", "pd"),
    ("pd", "mirror"),
    ("mirror", "pd"),
    ("pwm", "bend_pos"),
    ("bend_both", "asym"),
    ("mirror", "mirror"),
    ("bend_pos", "bend_pos"),
    ("asym", "pwm"),
]


@pytest.mark.parametrize("shape", ["saw", "sine", "triangle", "square", "wavetable"])
@pytest.mark.parametrize("pair", SMOOTH_PAIRS)
def test_two_warps_are_band_limited_against_a_naive_pair(shape, pair):
    """The same 4x path: 10-26 dB less alias than the pair at the rate with
    no band-limiting (measured at 880 Hz, 0.7 then 0.5: saw bend + then PD
    -43.9 / -19.1 dB, PD then mirror -70.7 / -48.0, bend + twice -48.8 /
    -23.5; sine bend + twice -32.9 / -13.1 — its slope at the cycle's end
    is 11.5 x 8.5, and the wavetable takes the mip that much darker, here
    its first harmonic: the sine's numbers; the square's mid edge, never
    polyBLEP'd, -27.4 to -35.6)."""
    got, naive = _two(shape, *pair), _two(shape, *pair, naive=True)
    assert got < naive - 9.0, (got, naive)
    assert got < (-26.0 if shape == "square" else -32.0), got


@pytest.mark.parametrize("shape", ["saw", "sine", "wavetable"])
@pytest.mark.parametrize("pair", [("quantize", "bend_neg"), ("pwm", "quantize"),
                                  ("bend_neg", "quantize")])  # fmt: skip
def test_quantize_steps_in_a_pair_are_the_firs(shape, pair):
    """A quantize's steps are not polyBLEP'd (as alone): the FIR is what
    takes them, so a pair holding one gains less — down to 6.7 dB on a sine
    whose pwm crowds 33 steps into a third of the cycle (-31.7 / -25.0)."""
    got, naive = _two(shape, *pair), _two(shape, *pair, naive=True)
    assert got < naive - 5.0, (got, naive)


def test_the_second_amount_is_array_safe_and_routable():
    n = SR // 2
    base = Patch(osc1_warp="pd", osc1_warp_amt=0.4, osc1_warp2="bend_pos")
    sweep = replace(base, osc1_warp2_amt=np.linspace(0, 1, n))
    y = Synth(sweep).note("C3", 0.5, gate=0.4)
    assert np.all(np.isfinite(y)) and np.abs(y).max() > 0.05
    routed = replace(base, mod1_src="lfo", mod1_dst="osc1_warp2_amt", mod1_amt=0.5)
    still = replace(routed, mod1_amt=0.0)
    a = Synth(routed).note("C3", 0.5, gate=0.4)
    b = Synth(still).note("C3", 0.5, gate=0.4)
    assert np.max(np.abs(a - b)) > 1e-3
    # every oscillator has one, osc3 included
    for k, extra in ((2, {"osc2": "saw"}), (3, {"osc3_level": 0.6})):
        one = Patch(
            osc1="sine", **extra, **{f"osc{k}_warp": "pd", f"osc{k}_warp_amt": 0.5}
        )
        two = replace(one, **{f"osc{k}_warp2": "mirror", f"osc{k}_warp2_amt": 0.5})
        diff = Synth(two).note("C3", 0.2) - Synth(one).note("C3", 0.2)
        assert np.max(np.abs(diff)) > 0.01, k
