"""More modulation: a route's switch and the curve on its source, two
chaotic flows as LFO shapes.

Each is opt-in — a route "on" with its curve at 0 is the route as it always
was, so a patch that leaves them at their defaults renders bit for bit as
before and never reaches the new code (the gates below; RENDER_REV stands).
Each is held to what it claims: a bypassed route routes nothing and keeps
its settings, a curve bends the source's size and keeps its sign, 0 at 0
and 1 at 1, the two directions each other's inverse."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from synthlab import core
from synthlab import synth as synth_mod
from synthlab.core import SR
from synthlab.params import (
    GENERAL_LFO_SHAPES,
    MOD_SLOTS,
    PARAMS,
    clamp,
    live,
    slot_active,
)
from synthlab.studio.factory import FACTORY, get_factory
from synthlab.studio.library import LIBRARY, get_library
from synthlab.synth import Patch, Synth, route_curve

ROUTE_FIELDS = [f"{s}_{k}" for k in ("on", "curve") for s in MOD_SLOTS]


def _all_shipped() -> list[Patch]:
    rows = [get_factory(e["name"]) for e in FACTORY]
    rows += [get_library(e["name"]) for e in LIBRARY]
    return [*rows, *synth_mod.PRESETS.values(), Patch()]


def _note(p: Patch, **kw) -> np.ndarray:
    return Synth(p).note("C3", 0.4, gate=0.3, **kw)


# --- the defaults: the old engine, untouched ---------------------------------------


def test_routes_left_on_and_straight_never_reach_the_curve(monkeypatch):
    """Every factory and library sound, every PRESET and the default patch
    render without touching a route's curve: their audio cannot move."""

    def boom(*a, **k):
        raise AssertionError("a route curve ran on a patch whose curves are 0")

    monkeypatch.setattr(synth_mod, "route_curve", boom)
    for p in _all_shipped():
        assert all(getattr(p, f"{s}_on") == "on" for s in MOD_SLOTS)
        Synth(p).note("C3", 0.2, gate=0.15)


def test_the_fields_are_appended_hand_made_and_out_of_older_keys():
    from synthlab.studio.render import KEY_ADDED_FIELDS, keyed_patch

    names = list(PARAMS)
    at = names.index("mod1_on")
    assert names[at : at + len(ROUTE_FIELDS)] == ROUTE_FIELDS  # one run, in order
    assert at > names.index("osc3_warp2_amt")  # appended after the second warps
    for name in ROUTE_FIELDS:
        assert PARAMS[name].gene is None and PARAMS[name].nogene, name
        assert PARAMS[name].curve is None and PARAMS[name].mod is None, name
        assert KEY_ADDED_FIELDS[name] == getattr(Patch(), name)
    assert not set(ROUTE_FIELDS) & set(keyed_patch(Patch()))
    assert PARAMS["mod1_on"].cap == ("on", "off")
    assert PARAMS["mod7_curve"].cap == (-1.0, 1.0)


# --- the switch ----------------------------------------------------------------------


@pytest.mark.parametrize("slot", ["mod1", "mod9"])
def test_a_bypassed_route_routes_nothing_and_keeps_its_settings(slot):
    base = Patch(cutoff=700.0, filter_type="lowpass")
    wired = base.tweak(
        **{f"{slot}_src": "lfo", f"{slot}_dst": "cutoff", f"{slot}_amt": 0.8}
    )
    off = wired.tweak(**{f"{slot}_on": "off"})
    assert slot_active(wired, slot) and not slot_active(off, slot)
    assert np.array_equal(_note(off), _note(base))
    assert not np.array_equal(_note(wired), _note(base))
    # the breeder's liveness rule hears no LFO through a bypassed route
    assert "lfo_shape" in live(wired) and "lfo_shape" not in live(off)


# --- the curve -----------------------------------------------------------------------


def test_the_curve_keeps_its_ends_its_sign_and_inverts_itself():
    x = np.linspace(-1.0, 1.0, 401)
    for c in (0.3, 1.0):
        up, down = route_curve(x, c), route_curve(x, -c)
        assert np.allclose(route_curve(up, -c), x, atol=1e-12)  # inverses
        assert np.array_equal(np.sign(up), np.sign(x))
        assert np.array_equal(np.sign(down), np.sign(x))
        assert route_curve(np.array([1.0, -1.0, 0.0]), c).tolist() == [1.0, -1.0, 0.0]
        assert np.all(np.diff(up) >= 0) and np.all(np.diff(down) >= 0)  # monotone
        half = route_curve(np.array([0.5]), c)[0]
        assert half > 0.5 and route_curve(np.array([0.5]), -c)[0] < 0.5
    # at +1 the steepest: k = 16, half way up is 16 / 17 of the way
    assert route_curve(np.array([0.5]), 1.0)[0] == pytest.approx(8.0 / 8.5)
    # a 0..1 source stays 0..1; past 1 is held at 1
    assert route_curve(np.array([0.0, 0.25, 2.0]), -0.5).min() >= 0.0
    assert route_curve(np.array([2.0]), 0.5)[0] == 1.0


def test_a_curved_route_bends_its_source_before_the_amount():
    """Velocity 0.5 -> osc1 level, curve +1: the level moves by 8 / 8.5 of
    the amount's span, not half."""
    base = Patch(
        osc1_level=0.1, mod1_src="velocity", mod1_dst="osc1_level", mod1_amt=0.5
    )
    lo, hi = PARAMS["osc1_level"].mod
    for c, want in ((0.0, 0.5), (1.0, 8.0 / 8.5), (-1.0, 0.5 / 8.5)):
        p = base.tweak(mod1_curve=c)
        routed, _ = synth_mod._mod_routes(p, 64, 44100, 0.5, None)
        got = np.asarray(routed.osc1_level)
        assert got == pytest.approx(np.full(64, 0.1 + 0.5 * (hi - lo) * want))


def test_a_curve_on_a_pitch_route_and_a_bipolar_lfo():
    p = Patch(osc1="sine", mod1_src="lfo", mod1_dst="pitch", mod1_amt=0.1)
    _, straight = synth_mod._mod_routes(p, 44100, 44100, 1.0, None)
    _, bent = synth_mod._mod_routes(replace(p, mod1_curve=0.6), 44100, 44100, 1.0, None)
    assert np.array_equal(np.sign(bent), np.sign(straight))
    assert np.max(np.abs(bent)) == pytest.approx(np.max(np.abs(straight)), abs=1e-9)
    assert np.mean(np.abs(bent)) > np.mean(np.abs(straight))  # rises early


# --- the chaotic flows -------------------------------------------------------------


def test_no_shipped_patch_reaches_the_flows(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a chaotic flow ran on a patch that has none")

    monkeypatch.setattr(core, "attractor_points", boom)
    for p in _all_shipped():
        Synth(p).note("C3", 0.2, gate=0.15)


@pytest.mark.parametrize("shape", ["lorenz", "rossler"])
def test_a_flow_is_bounded_continuous_seeded_and_the_same_every_render(shape):
    assert GENERAL_LFO_SHAPES[-2:] == ("lorenz", "rossler")  # appended
    n = 20 * SR
    a = core.lfo_ext(shape, 3.0, n, SR, 0.0, 7)
    assert np.array_equal(a, core.lfo_ext(shape, 3.0, n, SR, 0.0, 7))
    assert np.all(np.abs(a) <= 1.0) and a.max() > 0.5 and a.min() < -0.5
    # joined by lines, 8 points a cycle: no step bigger than a point's move
    assert np.max(np.abs(np.diff(a))) < 0.01
    # another seed, another path (the LFOs each have their own)
    assert not np.allclose(a, core.lfo_ext(shape, 3.0, n, SR, 0.0, 8))
    # a function of the phase: twice the rate reads the same path twice as fast
    b = core.lfo_ext(shape, 6.0, n // 2, SR, 0.0, 7)
    assert np.allclose(b, a[1::2][: len(b)], atol=1e-9)  # (a cumsum: phase at i + 1)
    # it never settles: a long read keeps moving
    tail = core.lfo_ext(shape, 50.0, n, SR, 0.0, 7)[-SR:]
    assert tail.std() > 0.2


def test_the_flows_points_are_one_cached_trajectory():
    first = core.attractor_points("lorenz", 123, 50)
    longer = core.attractor_points("lorenz", 123, 400)
    assert np.array_equal(longer[:50], first)
    # the cache holds the same numbers a fresh walk computes
    s = core.attractor_start("lorenz", 123)
    vals = [core.attractor_value("lorenz", s[0])]
    for _ in range(399):
        s = core.attractor_step("lorenz", s)
        vals.append(core.attractor_value("lorenz", s[0]))
    assert np.array_equal(np.asarray(vals), longer)
    # the Lorenz flow swaps lobes (x changes sign) — a chaotic flow, not a cycle
    assert (longer > 0.3).any() and (longer < -0.3).any()


@pytest.mark.parametrize("k", [1, 2])
def test_the_lfos_take_the_flows(k):
    name = "lfo" if k == 1 else f"lfo{k}"
    shape = "lfo_shape" if k == 1 else f"lfo{k}_shape"
    p = Patch(
        **{shape: "rossler", "mod1_src": name, "mod1_dst": "cutoff", "mod1_amt": 0.5}
    )
    assert clamp(p) == p
    assert not np.array_equal(_note(p), _note(replace(p, **{shape: "sine"})))
