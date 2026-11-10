"""The mod matrix: four slots, not two.

Two slots is the ceiling you hit first when a patch starts to become a
*sound* rather than a preset — an LFO on the cutoff and velocity on the
drive, and you are out of routing. The slots are uniform, so everything
that knows about them (the engine, the breeder's gene ranges and its
"don't mutate what you can't hear" rules, the UI, the worklet gate) reads
one list rather than repeating itself per slot.
"""

from __future__ import annotations

import numpy as np

from synthlab.params import MOD_GENE_SLOTS
from synthlab.params import live as _active_genes
from synthlab.synth import MOD_SLOTS, Patch, Synth


def render(patch: Patch) -> np.ndarray:
    return Synth(patch).note("C3", 0.6)


def test_there_are_twelve_mod_slots_and_the_breeder_walks_four():
    """Twelve slots; the first four are the breeder's genes, so seeded
    litters stand."""
    assert MOD_SLOTS == tuple(f"mod{k}" for k in range(1, 13))
    assert MOD_GENE_SLOTS == ("mod1", "mod2", "mod3", "mod4")
    for slot in MOD_SLOTS:
        for suffix in ("_src", "_dst", "_amt"):
            assert slot + suffix in Patch.__dataclass_fields__


def test_a_patch_written_before_the_extra_slots_still_loads_and_sounds_the_same():
    """Slots 3 and 4 default to off, so nothing that exists today moves."""
    old = Patch(name="legacy", mod1_src="lfo", mod1_dst="cutoff", mod1_amt=0.4)
    assert old.mod3_src == "none" and old.mod3_amt == 0.0
    assert old.mod4_src == "none" and old.mod4_amt == 0.0


def test_each_slot_reaches_the_sound_on_its_own():
    """A slot that is wired but silent is worse than no slot at all."""
    base = Patch(name="m", cutoff=800.0, filter_type="lowpass")
    dry = render(base)
    for slot in MOD_SLOTS:
        wired = base.tweak(
            **{f"{slot}_src": "lfo", f"{slot}_dst": "cutoff", f"{slot}_amt": 0.9}
        )
        wet = render(wired)
        moved = float(np.abs(wet - dry).max())
        assert moved > 0.01, f"{slot} changed nothing (max delta {moved:.4f})"


def test_slots_stack_rather_than_overwrite_each_other():
    """Two slots aimed at one destination must both contribute."""
    base = Patch(name="m", cutoff=600.0, filter_type="lowpass")
    one = base.tweak(mod1_src="lfo", mod1_dst="cutoff", mod1_amt=0.3)
    four = one.tweak(
        mod2_src="aenv",
        mod2_dst="cutoff",
        mod2_amt=0.3,
        mod3_src="velocity",
        mod3_dst="cutoff",
        mod3_amt=0.3,
        mod4_src="fenv",
        mod4_dst="cutoff",
        mod4_amt=0.3,
    )
    assert float(np.abs(render(four) - render(one)).max()) > 0.01


def test_a_silent_slot_spends_no_mutation_budget():
    """`_active_genes` exists so mutation never wastes a roll on a gene that
    cannot be heard — a slot at zero amount is exactly that."""
    quiet = Patch(name="m")
    live = _active_genes(quiet)
    for slot in MOD_SLOTS:
        assert f"{slot}_src" not in live, slot
        assert f"{slot}_dst" not in live, slot
    loud = quiet.tweak(mod4_src="lfo", mod4_dst="cutoff", mod4_amt=0.5)
    assert "mod4_dst" in _active_genes(loud)


def test_the_assignable_lfo_stays_live_when_any_slot_uses_it():
    """The LFO's own rate/shape genes were kept alive by checking slots 1
    and 2; a patch whose only LFO routing sits in slot 4 must not have its
    LFO controls frozen out of breeding."""
    p = Patch(name="m", mod4_src="lfo", mod4_dst="cutoff", mod4_amt=0.5)
    assert "lfo_shape" in _active_genes(p)


def test_the_worklet_gate_covers_as_many_slots_as_exist():
    """The live voice runs the mod matrix but refuses one
    route per slot: LFO -> drive on a non-ladder filter at drive <= 1. The
    invariant that matters is the count: add a fifth slot and leave the
    gate checking four, and a slot-5 routing of that kind would play live
    as though it were not there.

    The gate lives server-side (studio/worklet.py, /patch.json's
    worklet_ok) so piano.js can no longer drift from what the worklet
    actually does; the sweep lives in worklet.GATED, derived from
    synth.MOD_SLOTS rather than a literal per-slot list."""
    from synthlab.studio import worklet

    for slot in MOD_SLOTS:
        assert f"{slot}_src" in worklet.GATED, slot
    active = {f"{slot}_src" for slot in MOD_SLOTS}
    assert {k for k in worklet.GATED if k.endswith("_src")} == active

    for slot in MOD_SLOTS:
        refused = Patch(name="m", filter_type="lowpass").tweak(
            **{f"{slot}_src": "lfo", f"{slot}_dst": "drive", f"{slot}_amt": 0.4}
        )
        assert not worklet.can_play(refused), slot
    wired = Patch(name="m", mod3_src="lfo", mod3_dst="cutoff", mod3_amt=0.4)
    assert worklet.can_play(wired)
    silent = Patch(
        name="m", filter_type="lowpass", mod3_src="lfo", mod3_dst="drive", mod3_amt=0.0
    )
    assert worklet.can_play(silent)


# --- pitch: a route target that is not a field, and the bend -----------------


def _f0(x: np.ndarray, sr: int = 44100) -> float:
    """The loudest line of a sine's spectrum, interpolated (Hz)."""
    w = np.abs(np.fft.rfft(x * np.hanning(len(x)), 8 * len(x)))
    k = int(np.argmax(w))
    a, b, c = np.log(w[k - 1 : k + 2] + 1e-30)
    return (k + 0.5 * (a - c) / (a - 2 * b + c)) * sr / (8 * len(x))


def _sine(**kw) -> Patch:
    return Patch(name="p", osc1="sine", filter_type="none", **kw)


def test_a_pitch_route_bends_by_amount_times_twelve_semitones():
    """amount x PITCH_ROUTE_ST (12) x the source: velocity is a constant
    source, so the note sits exactly that many semitones away."""
    from synthlab.params import MOD_RANGES, PITCH_ROUTE_ST

    assert MOD_RANGES["pitch"] == (-12.0, 12.0) and PITCH_ROUTE_ST == 12.0
    for amt, vel in ((0.5, 1.0), (-0.25, 1.0), (1.0, 0.5)):
        p = _sine(mod1_src="velocity", mod1_dst="pitch", mod1_amt=amt)
        x = Synth(p).note("A3", 0.5, velocity=vel)[4000:20000, 0]
        want = 220.0 * 2 ** (amt * 12 * vel / 12)
        assert abs(_f0(x) / want - 1) < 1e-3, (amt, vel, _f0(x), want)


def test_pitch_routes_sweep_and_add_up_without_a_clip():
    """An LFO route sweeps the pitch across +-amount x 12 st; two routes
    add (an octave and a half is not clipped to any span)."""
    lfo = _sine(mod1_src="lfo", mod1_dst="pitch", mod1_amt=0.5, mod_lfo_rate=2.0)
    x = Synth(lfo).note("A2", 1.0)[:, 0]
    # instantaneous frequency from zero crossings (interpolated), over two
    # LFO cycles
    up = np.flatnonzero((x[:-1] < 0) & (x[1:] >= 0))
    at = up + x[up] / (x[up] - x[up + 1])
    f = 44100 / np.diff(at)
    ratio = f.max() / f.min()
    assert 2 ** (11.5 / 12) < ratio < 2 ** (12.5 / 12), ratio  # +-6 st
    both = _sine(
        mod1_src="velocity", mod1_dst="pitch", mod1_amt=1.0,
        mod2_src="velocity", mod2_dst="pitch", mod2_amt=0.5,
    )  # fmt: skip
    y = Synth(both).note("A2", 0.5)[4000:20000, 0]
    assert abs(_f0(y) / (110.0 * 2**1.5) - 1) < 1e-3


def test_pitch_bend_is_the_wheel_times_its_range_on_every_oscillator():
    """pitch_bend x bend_range semitones, on osc1, osc2 and the sub alike
    (the whole stack moves, its intervals kept)."""
    x = Synth(_sine(pitch_bend=0.5, bend_range=7.0)).note("A3", 0.5)
    assert abs(_f0(x[4000:20000, 0]) / (220.0 * 2 ** (3.5 / 12)) - 1) < 1e-3
    flat = Patch(name="s", osc1="saw", osc2="square", sub_level=0.5, key_track=0.0)
    bent = flat.tweak(pitch_bend=-1.0, bend_range=12.0)
    a = Synth(flat).note("A3", 0.5)[:, 0]
    b = Synth(bent).note("A4", 0.5)[:, 0]  # an octave up, bent an octave down
    assert float(np.max(np.abs(a - b))) < 1e-6
    # no range, no bend
    np.testing.assert_array_equal(
        Synth(flat.tweak(pitch_bend=0.7, bend_range=0.0)).note("A3", 0.5),
        Synth(flat).note("A3", 0.5),
    )


def test_a_drawn_bend_is_array_safe_and_lands_where_drawn():
    """A curve hands note() one bend value per sample: the pitch follows."""
    n = int(0.6 * 44100)
    bend = np.where(np.arange(n) < n // 2, 0.0, 1.0)
    x = Synth(_sine(pitch_bend=bend, bend_range=12.0)).note("A3", 0.6)[:, 0]
    assert abs(_f0(x[2000 : n // 2 - 500]) / 220.0 - 1) < 2e-3
    assert abs(_f0(x[n // 2 + 500 : n - 2000]) / 440.0 - 1) < 2e-3


def test_breeding_never_routes_to_pitch():
    from synthlab.explore.breed import _route_on, random_patch

    r = np.random.default_rng(3)
    for _ in range(400):
        assert _route_on("mod1", r)["mod1_dst"] != "pitch"
    for _ in range(100):
        p = random_patch(Patch(), rng=r)
        assert "pitch" not in {getattr(p, f"{s}_dst") for s in MOD_SLOTS}
