"""A unit's band (`effects.band_apply`): any unit on the lows, mids or highs
of a signal (the OTT's Linkwitz-Riley crossover) or on its mid or side
channel, the rest passed and summed back in phase with it."""

from __future__ import annotations

from functools import partial

import numpy as np
import pytest

from synthlab import effects as fx
from synthlab.core import SR

FREQ = ("low", "mid", "high")


def _tones(n: int = SR, freqs=(30.0, 550.0, 12000.0)) -> np.ndarray:
    t = np.arange(n) / SR
    left = sum(np.sin(2 * np.pi * f * t) for f in freqs) * 0.25
    right = sum(np.sin(2 * np.pi * f * t + 0.7) for f in freqs) * 0.2
    return np.column_stack([left, right])


def _level_db(x: np.ndarray, f: float) -> float:
    """A tone's level in the second half of `x` (its first channel)."""
    y = x[len(x) // 2 :, 0]
    t = np.arange(len(y)) / SR
    amp = 2 * abs(np.mean(y * np.exp(-2j * np.pi * f * t)))
    return 20 * np.log10(amp + 1e-30)


def _same(y):
    return y


def _half(y):
    return 0.5 * y


def test_the_bands_are_the_five_and_an_unknown_one_is_the_whole_signal():
    assert fx.BANDS == ("low", "mid", "high", "ms_mid", "ms_side")
    x = _tones()
    assert np.array_equal(fx.band_apply(x, "nope", _half), 0.5 * x)
    assert np.array_equal(fx.band_apply(x, None, _half), 0.5 * x)


@pytest.mark.parametrize("band", FREQ)
def test_a_band_left_as_it_is_gives_the_all_passed_input_flat(band):
    """The passed part is all-passed as the band is: a unit that changes
    nothing on its band (at mix 0, say) is the bands summed — the input
    through two all-passes, flat to rounding — never the raw input."""
    imp = np.zeros((1 << 15, 2))
    imp[0] = 1.0
    y = fx.band_apply(imp, band, _same)
    np.testing.assert_allclose(y, sum(fx.ott_bands(imp)), atol=1e-15)
    mag = np.abs(np.fft.rfft(y[:, 0]))
    assert np.max(np.abs(mag - 1.0)) < 1e-9  # measured 4e-14
    # and the same at mix 0 through a real unit, any band
    x = _tones()
    at_rest = fx.band_apply(x, band, fx.drive(amount=8.0, mix=0.0))
    np.testing.assert_allclose(at_rest, sum(fx.ott_bands(x)), atol=1e-14)
    # not the raw input: the all-passes turn the phase round the splits
    assert np.max(np.abs(at_rest - x)) > 0.05


@pytest.mark.parametrize(
    ("band", "moved"), [("low", 30.0), ("mid", 550.0), ("high", 12000.0)]
)
def test_the_unit_works_on_its_band_and_the_others_pass(band, moved):
    """Half the band's level (-6 dB) on a tone in it; the tones in the other
    bands within 0.05 dB of the input. Each tone sits two octaves or more
    from a split: nearer, a crossover's skirt carries part of it into the
    next band (a 60 Hz tone, an octave under 120 Hz, is -5.5 dB, not -6)."""
    x = _tones()
    y = fx.band_apply(x, band, _half)
    for f in (30.0, 550.0, 12000.0):
        d = _level_db(y, f) - _level_db(x, f)
        if f == moved:
            assert abs(d + 6.02) < 0.15, (band, f, d)
        else:
            assert abs(d) < 0.05, (band, f, d)


def test_mid_and_side_are_exact():
    x = _tones() * [1.0, 0.6]
    mid = (x[:, 0] + x[:, 1]) * 0.5
    side = (x[:, 0] - x[:, 1]) * 0.5
    # left as they are: the input to rounding (no filter)
    for band in ("ms_mid", "ms_side"):
        np.testing.assert_allclose(fx.band_apply(x, band, _same), x, atol=1e-15)
    # the mid taken out leaves the side alone; the side taken out, a mono mid
    no_mid = fx.band_apply(x, "ms_mid", lambda y: 0.0 * y)
    np.testing.assert_allclose(no_mid, np.column_stack([side, -side]), atol=1e-15)
    no_side = fx.band_apply(x, "ms_side", lambda y: 0.0 * y)
    np.testing.assert_allclose(no_side, np.column_stack([mid, mid]), atol=1e-15)


def test_a_unit_on_mid_or_side_hears_its_channel_on_both_sides():
    """Its two outputs are averaged back to one: a unit that pans or spreads
    (the doubler's spread, a width) meets one channel on both sides."""
    seen = []

    def spy(y):
        seen.append(y.copy())
        return y

    x = _tones()
    fx.band_apply(x, "ms_side", spy)
    (heard,) = seen
    assert heard.shape == x.shape and np.array_equal(heard[:, 0], heard[:, 1])
    np.testing.assert_allclose(heard[:, 0], (x[:, 0] - x[:, 1]) * 0.5)


def test_a_mono_input_has_no_side():
    x = _tones()[:, 0]
    assert fx.band_apply(x, "ms_side", _half) is x
    np.testing.assert_allclose(fx.band_apply(x, "ms_mid", _half), 0.5 * x, atol=1e-15)
    for band in FREQ:  # the bands of a mono signal are mono
        assert fx.band_apply(x, band, _same).shape == x.shape


def test_wet_is_the_band_output_alone():
    x = _tones()
    lo, mid, hi = fx.ott_bands(x)
    np.testing.assert_allclose(fx.band_apply(x, "mid", _half, wet=True), 0.5 * mid)
    m = (x[:, 0] + x[:, 1]) * 0.5
    s = (x[:, 0] - x[:, 1]) * 0.5
    got = fx.band_apply(x, "ms_side", _half, wet=True)
    np.testing.assert_allclose(got, np.column_stack([0.5 * s, -0.5 * s]), atol=1e-15)
    got = fx.band_apply(x, "ms_mid", _half, wet=True)
    np.testing.assert_allclose(got, np.column_stack([0.5 * m, 0.5 * m]), atol=1e-15)


@pytest.mark.parametrize("band", fx.BANDS)
def test_silent_in_silent_out_and_causal(band):
    """Nothing reads ahead: the first half of a buffer comes out the same
    whatever its second half holds."""
    assert not np.any(fx.band_apply(np.zeros((4096, 2)), band, fx.drive(4.0)))
    x = _tones(8192)
    y = x.copy()
    y[4096:] *= -3.0
    unit = partial(fx.band_apply, band=band, fn=fx.chorus(depth=5.0, mix=0.7))
    np.testing.assert_array_equal(unit(x)[:4096], unit(y)[:4096])


def test_banded_is_band_apply_round_a_prepared_unit():
    x = _tones()
    unit = fx.drive(amount=5.0)
    np.testing.assert_array_equal(
        fx.banded(unit, "high")(x), fx.band_apply(x, "high", unit)
    )
    np.testing.assert_array_equal(
        fx.banded(unit, "low", wet=True)(x), unit(fx.ott_bands(x)[0])
    )
