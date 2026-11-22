"""The multiband drive (`effects.mbdrive`, FX unit `mbdrive`): the OTT's
three Linkwitz-Riley bands (`effects.ott_bands`, 120 Hz / 2.5 kHz), each
through the drive's shape (`effects.drive_shape`) at its own amount, a band
at amount 1 left as it is. Causal, silent in silence, shaped as its input;
at its defaults it IS its input, and a driven band moves its own octaves
and nothing else — measured on tones, not by ear."""

import numpy as np
import pytest

from synthlab import effects as fx
from synthlab.core import SR
from synthlab.studio.fxrack import (
    FX_PRESETS,
    FX_UNITS,
    build_chain,
    clean_chain,
    resolve_unit,
)
from synthlab.studio.state import Arrangement

ARR = Arrangement(bpm=120.0)


def _unit(**params):
    (f,) = build_chain([{"kind": "mbdrive", "params": params}], ARR)
    return f


def _st(x: np.ndarray) -> np.ndarray:
    return np.column_stack([x, 0.8 * x])


def tone(freq: float, n: int, amp: float) -> np.ndarray:
    return amp * np.sin(2 * np.pi * freq * np.arange(n) / SR)


def _spectrum(a: np.ndarray):
    p = np.abs(np.fft.rfft(a * np.hanning(len(a)))) ** 2
    return np.fft.rfftfreq(len(a), 1 / SR), p


def _db(v: float) -> float:
    return float(10 * np.log10(max(float(v), 1e-30)))


def _at(a: np.ndarray, f0: float) -> float:
    """The power (dB) in the bins within 10 Hz of `f0`."""
    f, p = _spectrum(a)
    return _db(p[np.abs(f - f0) <= 10.0].sum())


def _harmonics_under(a: np.ndarray, f0: float, top: float) -> float:
    """The power (dB) of f0's harmonics 2..k under `top` Hz."""
    f, p = _spectrum(a)
    k = np.arange(2, int(top // f0) + 1) * f0
    return _db(sum(p[np.abs(f - h) <= 10.0].sum() for h in k))


MODES = range(len(fx.DRIVE_MODES))


@pytest.mark.parametrize("mode", MODES)
def test_at_its_defaults_it_is_its_input(mode):
    """Every band at 1: no band is shaped and nothing is split — the input
    itself, at any mix."""
    x = _st(np.random.default_rng(1).standard_normal(SR // 2) * 0.3)
    for mix in (0.0, 0.5, 1.0):
        y = _unit(mode=float(mode), mix=mix)(x)
        np.testing.assert_allclose(y, x, atol=1e-9)
        np.testing.assert_allclose(
            fx.mbdrive(mode=fx.DRIVE_MODES[mode])(x), x, atol=1e-9
        )


@pytest.mark.parametrize("mode", MODES)
def test_a_driven_low_band_adds_low_harmonics_and_leaves_the_top(mode):
    """60 Hz + 6 kHz, the low band at 8: the 60 Hz tone's harmonics under
    500 Hz come up (by tens of dB) while the 6 kHz partial, in the high
    band, stays within 0.5 dB."""
    n = SR
    x = _st(tone(60.0, n, 0.3) + tone(6000.0, n, 0.1))
    y = _unit(low=8.0, mode=float(mode))(x)
    a, b = x[n // 2 :, 0], y[n // 2 :, 0]
    assert _harmonics_under(b, 60.0, 500.0) - _harmonics_under(a, 60.0, 500.0) > 20.0
    assert abs(_at(b, 6000.0) - _at(a, 6000.0)) < 0.5


def test_a_driven_high_band_leaves_the_low_tone():
    n = SR
    x = _st(tone(60.0, n, 0.3) + tone(6000.0, n, 0.1))
    y = _unit(high=8.0)(x)
    a, b = x[n // 2 :, 0], y[n // 2 :, 0]
    assert abs(_at(b, 60.0) - _at(a, 60.0)) < 0.5
    assert _at(b, 6000.0) - _at(a, 6000.0) > 3.0  # the band driven: louder


@pytest.mark.parametrize("mode", MODES)
def test_silence_in_is_silence_out_at_every_bands_hardest(mode):
    loud = {"low": 12.0, "mid": 12.0, "high": 12.0, "mode": float(mode)}
    y = _unit(**loud)(np.zeros((SR // 2, 2)))
    assert np.max(np.abs(y)) == 0.0


@pytest.mark.parametrize("mode", MODES)
def test_mono_and_stereo_keep_their_shape_and_it_is_causal(mode):
    """Its band split and its shapes run forward from silence: a change at
    sample k moves nothing before k (the all-passes delay, they do not read
    ahead)."""
    rng = np.random.default_rng(3)
    x = _st(tone(110.0, SR, 0.4) + rng.standard_normal(SR) * 0.05)
    f = _unit(low=6.0, mid=3.0, high=9.0, mode=float(mode), mix=0.7)
    y = f(x)
    assert y.shape == x.shape and np.all(np.isfinite(y))
    np.testing.assert_array_equal(y, f(x))
    mono = f(x[:, 0])
    assert mono.shape == (len(x),)
    np.testing.assert_allclose(mono, f(_st(x[:, 0]) * [1, 1.25])[:, 0], atol=1e-12)
    later = x.copy()
    k = len(x) // 2
    later[k:] = rng.standard_normal((len(x) - k, 2)) * 0.5
    np.testing.assert_allclose(f(later)[:k], y[:k], atol=1e-12)


def test_the_dry_is_the_bands_unshaped_so_a_blend_never_combs():
    """The dry under a mix below 1 is the three bands summed (the input
    through the two all-passes, flat in level), in phase with the wet: at
    mix 0 the unit is that sum, and between the two a 2.5 kHz tone — where
    the raw input and the all-passed one are 180 degrees apart — keeps its
    level when no band near it is driven."""
    n = SR
    x = _st(np.random.default_rng(2).standard_normal(n) * 0.2)
    low, mid, high = fx.ott_bands(x)
    np.testing.assert_allclose(_unit(low=4.0, mix=0.0)(x), low + mid + high, atol=1e-12)
    wet = _unit(low=4.0, mix=1.0)(x)
    half = _unit(low=4.0, mix=0.5)(x)
    np.testing.assert_allclose(half, 0.5 * (low + mid + high) + 0.5 * wet, atol=1e-12)
    t = _st(tone(2500.0, n, 0.3) + tone(50.0, n, 0.3))
    y = _unit(low=4.0, mix=0.5)(t)
    assert abs(_at(y[n // 2 :, 0], 2500.0) - _at(t[n // 2 :, 0], 2500.0)) < 0.5


def test_a_band_at_one_is_untouched_and_a_lane_is_the_scalar_arithmetic():
    """An amount array (a lane) is the band's own sample where it reads 1
    and the shape at its value elsewhere; standing still it is the scalar
    unit bit for bit, and a mix array is the scalar blend sample by sample."""
    n = SR // 2
    x = _st(tone(80.0, n, 0.4) + tone(900.0, n, 0.2))
    for mode in fx.DRIVE_MODES:
        for v in (1.0, 2.5, 12.0):
            got = fx.mbdrive(low=np.full(n, v), mid=3.0, mode=mode)(x)
            want = fx.mbdrive(low=v, mid=3.0, mode=mode)(x)
            assert np.array_equal(got, want), (mode, v)
        m = fx.mbdrive(mid=5.0, mix=np.full(n, 0.3), mode=mode)(x)
        assert np.array_equal(m, fx.mbdrive(mid=5.0, mix=0.3, mode=mode)(x)), mode
    # a lane through 1: the first half driven, the second the band itself
    lane = np.where(np.arange(n) < n // 2, 6.0, 1.0)
    got = fx.mbdrive(low=lane, mid=3.0)(x)
    np.testing.assert_array_equal(
        got[: n // 2], fx.mbdrive(low=6.0, mid=3.0)(x)[: n // 2]
    )
    np.testing.assert_array_equal(
        got[n // 2 :], fx.mbdrive(low=1.0, mid=3.0)(x)[n // 2 :]
    )


def test_the_unit_is_registered_with_its_mode_a_choice_and_three_presets():
    spec = FX_UNITS["mbdrive"]
    assert spec["label"] == "Multiband drive"
    assert spec["params"] == {
        "low": (1.0, 12.0, 1.0),
        "mid": (1.0, 12.0, 1.0),
        "high": (1.0, 12.0, 1.0),
        "mode": (0.0, 3.0, 0.0),
        "mix": (0.0, 1.0, 1.0),
    }
    assert spec["choices"] == {"mode": fx.DRIVE_MODES}
    assert {"Low growl", "Mid bite", "Air fizz"} <= set(FX_PRESETS["mbdrive"])
    (u,) = clean_chain([{"kind": "mbdrive", "params": {"mode": 2.4, "low": 30}}])
    assert u["params"]["mode"] == 2.0 and u["params"]["low"] == 12.0
    assert resolve_unit(u, ARR) == (
        "mbdrive",
        {"low": 12.0, "mid": 1.0, "high": 1.0, "mode": "fuzz", "mix": 1.0},
    )
    # appended: every unit before it keeps its place (the utility and the
    # convolution came after it, appended in their turn)
    units = list(FX_UNITS)
    assert units[units.index("transient") + 1] == "mbdrive"
    assert units[units.index("mbdrive") + 1 :] == ["utility", "convolve"]
    assert list(FX_PRESETS)[-2:] == ["mbdrive", "utility"]
    # kept from the sound agent, as the dynamics units are (its schema's budget)
    from synthlab.studio.agent.tools import AGENT_FX

    assert "mbdrive" not in AGENT_FX
