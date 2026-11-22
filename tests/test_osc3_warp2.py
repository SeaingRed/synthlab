"""Oscillator 3. Opt-in — silent and unread at its level's default, 0, so
a patch without it renders bit for bit as if it did not exist (the gate
below) and every older key stands — and held to what it claims: a third
layer summed as osc2 is, in every unison copy, at its own octave,
semitones and cents."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from synthlab import params
from synthlab import synth as synth_mod
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
