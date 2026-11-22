"""The granular oscillator (synthlab/granular.py): the sampler's sample as a
cloud of grains on the output clock. Opt-in — a patch that plays no
"granular" oscillator never reaches its code (pinned by the gate below),
so every older render and key stands — and held to what each field says,
read off a sample whose left channel is a ramp (its value IS the position
a grain reads) and whose right channel is a steady level."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from synthlab import granular, sampler
from synthlab import synth as synth_mod
from synthlab.core import SR, note_to_hz
from synthlab.granular import Settings
from synthlab.params import HAND_SHAPES, MOD_DESTINATIONS, MOD_GENE_DESTINATIONS, PARAMS
from synthlab.studio.factory import FACTORY, get_factory
from synthlab.studio.library import LIBRARY, get_library
from synthlab.synth import Patch, Synth

GRAIN_FIELDS = (
    "grain_pos grain_size grain_density grain_spray grain_pitch_spray "
    "grain_shape grain_dir grain_scan"
).split()


def _ramp_sample(seconds: float = 2.0) -> str:
    """Left: 0 -> 1 over the sample (a grain's value is where it reads);
    right: a steady 0.5."""
    n = int(seconds * SR)
    left = np.linspace(0.0, 1.0, n, endpoint=False)
    return sampler.register(np.column_stack([left, np.full(n, 0.5)]))


RAMP = _ramp_sample()
ROOT = 60
KEY = note_to_hz(ROOT)


def _cloud(n: int = SR, key: float = KEY, **kw) -> np.ndarray:
    st = Settings(sample=RAMP, root=ROOT, **kw)
    return granular.render(np.full(n, key), n, SR, st, key)


def _grains(y: np.ndarray) -> list[tuple[int, int]]:
    """Each grain's [start, end) from the right channel (steady 0.5)."""
    on = np.abs(y[:, 1]) > 1e-9
    edges = np.flatnonzero(np.diff(on.astype(int)))
    starts = [0] if on[0] else []
    starts += [e + 1 for e in edges if not on[e]]
    ends = [e + 1 for e in edges if on[e]]
    if len(ends) < len(starts):
        ends.append(len(y))
    return list(zip(starts, ends, strict=True))


# --- the defaults: the old engine, untouched -------------------------------------


def test_patches_without_the_shape_never_reach_it(monkeypatch):
    """Every factory and library sound, every PRESET and the default patch
    render without touching the granular code: the new fields cannot move
    their audio, so RENDER_REV stands."""

    def boom(*a, **k):
        raise AssertionError("the granular path ran on a patch without the shape")

    monkeypatch.setattr(granular, "render", boom)
    monkeypatch.setattr(granular.Settings, "of", boom)
    rows = [get_factory(e["name"]) for e in FACTORY]
    rows += [get_library(e["name"]) for e in LIBRARY]
    for p in [*rows, *synth_mod.PRESETS.values(), Patch()]:
        assert "granular" not in (p.osc1, p.osc2)
        Synth(p).note("C3", 0.2, gate=0.15)


def test_the_fields_are_appended_hand_made_and_out_of_older_keys():
    from synthlab.studio.render import KEY_ADDED_FIELDS, keyed_patch

    names = list(PARAMS)
    assert names[-len(GRAIN_FIELDS) :] == GRAIN_FIELDS  # appended, in order
    for name in GRAIN_FIELDS:
        assert PARAMS[name].gene is None and PARAMS[name].nogene
        assert KEY_ADDED_FIELDS[name] == getattr(Patch(), name)
    assert not set(GRAIN_FIELDS) & set(keyed_patch(Patch()))
    assert "granular" in HAND_SHAPES
    # a route target, never a gene's choice; drawable
    assert "grain_pos" in MOD_DESTINATIONS and "grain_pos" not in MOD_GENE_DESTINATIONS
    assert PARAMS["grain_pos"].curve == (0.0, 1.0)


# --- what it plays -------------------------------------------------------------------


def test_no_sample_or_no_region_is_silence():
    n = 1000
    assert np.array_equal(granular.render(np.full(n, KEY), n), np.zeros(n))
    st = Settings(sample="0123456789abcdef")  # a hash this machine doesn't hold
    assert np.array_equal(granular.render(np.full(n, KEY), n, SR, st), np.zeros(n))
    st = Settings(sample=RAMP, start=0.5, end=0.5)
    assert np.array_equal(granular.render(np.full(n, KEY), n, SR, st), np.zeros(n))
    p = Patch(osc1="granular", osc2=None, filter_type="none")
    assert not np.any(Synth(p).note("C4", 0.2))


def test_length_stereo_and_determinism():
    kw = {"density": 80.0, "size": 0.05, "spray": 0.4, "pitch_spray": 2.0}
    a, b = _cloud(12345, **kw), _cloud(12345, **kw)
    assert a.shape == (12345, 2)
    assert np.array_equal(a, b)  # a hash, never a random stream
    assert not np.allclose(a[:, 0], a[:, 1])  # the sample's two channels
    # another key is another cloud (the seed is the pitch: key track off
    # here, so only the draws differ), and so is another oscillator copy
    flat = Settings(sample=RAMP, root=ROOT, keytrack="off", **kw)
    here = granular.render(np.full(12345, KEY), 12345, SR, flat, KEY)
    there = granular.render(np.full(12345, KEY * 2), 12345, SR, flat, KEY * 2)
    assert not np.allclose(here, there)
    st = Settings(sample=RAMP, root=ROOT, **kw)
    copy = granular.render(np.full(12345, KEY), 12345, SR, st, KEY, copy=1)
    assert not np.allclose(a, copy)


def test_the_seed_and_draws_are_pinned():
    assert granular.grain_seed(KEY, 0) == granular.grain_seed(KEY * 1.001, 0)
    assert granular.grain_seed(KEY, 0) != granular.grain_seed(KEY, 1)
    u = granular.grain_draws(granular.grain_seed(KEY, 0), 4)
    assert u.shape == (4, 3) and np.all((u >= 0) & (u < 1))
    assert np.array_equal(u, granular.grain_draws(granular.grain_seed(KEY, 0), 4))


@pytest.mark.parametrize("pos", [0.1, 0.45, 0.8])
def test_position_says_where_the_grains_read(pos):
    # one grain in the note, its window divided out: the ramp's value at
    # its first sample is the position, and at the root it reads at the
    # recording's own speed (the ramp's slope)
    size = 0.02
    y = _cloud(4000, density=1.0, size=size, pos=pos)
    g = granular.grain_length(size)
    win = granular.window(g, 0.0)
    read = y[:g, 0] / win
    assert read[0] == pytest.approx(pos, abs=1e-3)
    slope = (read[-1] - read[0]) / (g - 1)
    assert slope == pytest.approx(1.0 / (2.0 * SR), rel=1e-2)


def test_size_and_density_do_what_they_say():
    y = _cloud(SR, density=10.0, size=0.01)
    spans = _grains(y)
    assert len(spans) == 10
    for a, b in spans:
        assert b - a == granular.grain_length(0.01)
    assert [a for a, _ in spans] == list(granular.onsets(SR, 10.0))
    # density is held to MAX_SOUNDING overlapping
    g = granular.grain_length(1.0)
    assert granular.effective_density(500.0, g) * g / SR == pytest.approx(256)
    g = granular.grain_length(0.1)
    assert granular.effective_density(500.0, g) == 500.0


def test_window_shape_runs_from_hann_to_flat():
    soft, flat = granular.window(1000, 0.0), granular.window(1000, 1.0)
    hann = 0.5 - 0.5 * np.cos(2 * np.pi * (np.arange(1000) + 0.5) / 1000)
    np.testing.assert_allclose(soft, hann, atol=1e-12)
    assert np.all(flat[60:-60] == 1.0) and flat[0] < 0.01
    assert np.mean(flat) > np.mean(soft)


def test_an_array_position_moves_the_cloud():
    n = SR
    pos = np.linspace(0.0, 0.9, n)
    y = _cloud(n, density=10.0, size=0.005, pos=pos)
    firsts = [y[a, 0] / granular.window(b - a, 0.0)[0] for a, b in _grains(y)]
    want = [pos[t] for t in granular.onsets(n, 10.0)]
    np.testing.assert_allclose(firsts, want, atol=2e-3)


def test_scan_walks_the_region_and_wraps():
    # scan 1 crosses the 2 s region in 2 s: at 0.5 s a grain begins half a
    # second further in; from 0.7, at 1 s it has wrapped to 0.2
    y = _cloud(SR + 100, density=2.0, size=0.005, pos=0.7, scan=1.0)
    (a0, b0), (a1, b1), (a2, b2) = _grains(y)
    w = granular.window(b0 - a0, 0.0)[0]
    assert y[a0, 0] / w == pytest.approx(0.7, abs=1e-3)
    assert y[a1, 0] / w == pytest.approx(0.95, abs=1e-3)
    assert y[a2, 0] / w == pytest.approx(0.2, abs=1e-3)


def test_spray_scatters_within_its_width():
    y = _cloud(SR, density=50.0, size=0.004, pos=0.5, spray=0.2)
    firsts = [y[a, 0] / granular.window(b - a, 0.0)[0] for a, b in _grains(y)]
    assert min(firsts) >= 0.4 - 1e-3 and max(firsts) <= 0.6 + 1e-3
    assert np.std(firsts) > 0.03  # actually scattered


def test_direction_and_pitch():
    g = granular.grain_length(0.02)
    win = granular.window(g, 0.0)
    fwd = _cloud(3000, density=1.0, size=0.02, pos=0.3)[:g, 0] / win
    back = _cloud(3000, density=1.0, size=0.02, pos=0.3, direction="reverse")[:g, 0]
    np.testing.assert_allclose(back / win, fwd[::-1], atol=1e-6)  # the same audio
    # an octave over the root reads twice as fast
    up = _cloud(3000, key=KEY * 2, density=1.0, size=0.02, pos=0.3)[:g, 0] / win
    assert (up[-1] - up[0]) == pytest.approx(2 * (fwd[-1] - fwd[0]), rel=1e-2)
    # key track off: every key reads at the recording's speed
    st = Settings(
        sample=RAMP, root=ROOT, keytrack="off", density=1.0, size=0.02, pos=0.3
    )
    flat = granular.render(np.full(3000, KEY * 2), 3000, SR, st, KEY * 2)[:g, 0] / win
    np.testing.assert_allclose(flat, fwd, atol=1e-6)


def test_both_and_pitch_spray_vary_grain_by_grain():
    y = _cloud(SR, density=40.0, size=0.01, pos=0.5, direction="both")
    slopes = [np.sign(y[b - 2, 0] / y[b - 2, 1] - y[a + 1, 0] / y[a + 1, 1])
              for a, b in _grains(y)]  # fmt: skip
    assert 1.0 in slopes and -1.0 in slopes
    st = 3.0
    y = _cloud(SR, density=40.0, size=0.01, pos=0.5, pitch_spray=st)
    rates = []
    for a, b in _grains(y):
        r = (y[b - 2, 0] / y[b - 2, 1] - y[a + 1, 0] / y[a + 1, 1]) / (b - a - 3)
        rates.append(r * 0.5 * 2 * SR)  # source samples per output sample
    rates = np.array(rates)
    assert (
        rates.min() >= 2 ** (-st / 12) - 0.01 and rates.max() <= 2 ** (st / 12) + 0.01
    )
    assert rates.std() > 0.03


def test_the_region_is_the_span_or_the_loop():
    L = 1000
    assert granular.region(L, Settings(start=0.2, end=0.6)) == (200, 600)
    loop = Settings(start=0.2, end=0.6, loop="on", loop_start=0.3, loop_end=0.5)
    assert granular.region(L, loop) == (300, 500)
    tiny = replace(loop, loop_end=0.31)  # under MIN_LOOP: the span
    assert granular.region(L, tiny) == (200, 600)


# --- through the synth ----------------------------------------------------------------


def _granular_patch(**kw) -> Patch:
    return Patch(
        osc1="granular",
        osc2=None,
        filter_type="none",
        sampler_sample=RAMP,
        sampler_root=ROOT,
        attack=0.002,
        sustain=1.0,
        release=0.05,
        **kw,
    )


def test_the_patch_plays_it_through_the_voice():
    p = _granular_patch(grain_density=60.0, grain_size=0.05, grain_spray=0.3)
    y = Synth(p).note("C4", 0.5)
    assert y.shape[1] == 2 and np.all(np.isfinite(y)) and np.abs(y).max() > 0.05
    assert np.array_equal(y, Synth(p).note("C4", 0.5))
    # NumPy's own loop (the native stack never takes the shape)
    s = Synth(p)
    s._native_stack = lambda *a, **k: None
    np.testing.assert_array_equal(s.note("C4", 0.5), y)


def test_a_route_and_a_curve_move_the_position():
    p = _granular_patch(grain_density=40.0, grain_size=0.02, grain_pos=0.2)
    still = Synth(p).note("C4", 0.5)
    routed = replace(p, mod1_src="lfo", mod1_dst="grain_pos", mod1_amt=0.5)
    assert not np.allclose(Synth(routed).note("C4", 0.5), still)
    drawn = replace(p, grain_pos=np.linspace(0.2, 0.9, 512))
    assert not np.allclose(Synth(drawn).note("C4", 0.5), still)
