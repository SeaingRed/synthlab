"""The utility unit (`effects.utility`): polarity, width, bass mono, pan
and gain — at its defaults the input itself."""

from __future__ import annotations

import numpy as np
import pytest

from synthlab import effects as fx
from synthlab.core import SR


def _stereo(n: int = SR) -> np.ndarray:
    t = np.arange(n) / SR
    left = 0.3 * np.sin(2 * np.pi * 50 * t) + 0.2 * np.sin(2 * np.pi * 2000 * t)
    right = 0.3 * np.sin(2 * np.pi * 50 * t + 1.9) + 0.1 * np.sin(2 * np.pi * 2000 * t)
    return np.column_stack([left, right])


def _tone_db(x: np.ndarray, f: float) -> float:
    y = x[len(x) // 2 :]
    t = np.arange(len(y)) / SR
    return 20 * np.log10(2 * abs(np.mean(y * np.exp(-2j * np.pi * f * t))) + 1e-30)


def test_at_its_defaults_it_is_the_input_bit_for_bit():
    x = _stereo()
    assert fx.utility()(x) is x
    # a lane standing at the defaults is the same
    still = fx.utility(gain=np.zeros(len(x)), width=np.ones(len(x)))(x)
    assert still is x


def test_gain_and_pan():
    x = _stereo()
    y = fx.utility(gain=-6.0)(x)
    np.testing.assert_allclose(y, x * 10 ** (-6 / 20), atol=1e-15)
    gl, gr = fx._pan_gains(0.5)
    y = fx.utility(pan=0.5)(x)
    np.testing.assert_allclose(y, x * [gl, gr], atol=1e-15)


@pytest.mark.parametrize("w", [0.0, 0.5, 2.0])
def test_width_scales_the_side_and_keeps_the_mid(w):
    x = _stereo()
    y = fx.utility(width=w)(x)
    mid, side = (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2
    np.testing.assert_allclose((y[:, 0] + y[:, 1]) / 2, mid, atol=1e-15)
    np.testing.assert_allclose((y[:, 0] - y[:, 1]) / 2, w * side, atol=1e-15)


def test_polarity_flips_a_channel():
    x = _stereo()
    np.testing.assert_allclose(fx.utility(flip_l=1.0)(x), x * [-1, 1], atol=1e-15)
    np.testing.assert_allclose(fx.utility(flip_r=1.0)(x), x * [1, -1], atol=1e-15)


def test_bass_mono_makes_the_lows_mono_and_keeps_the_highs_wide():
    """Under 120 Hz the side is gone (a 50 Hz tone's side 30 dB down or
    more), over it the side stays (2 kHz within 0.05 dB); the mid is
    all-passed, flat in level (the 50 Hz mid within 0.01 dB)."""
    x = _stereo(2 * SR)
    y = fx.utility(bass_mono=120.0)(x)
    side = lambda a: (a[:, 0] - a[:, 1]) / 2  # noqa: E731
    mid = lambda a: (a[:, 0] + a[:, 1]) / 2  # noqa: E731
    assert _tone_db(side(y), 50) - _tone_db(side(x), 50) < -30
    assert abs(_tone_db(side(y), 2000) - _tone_db(side(x), 2000)) < 0.05
    assert abs(_tone_db(mid(y), 50) - _tone_db(mid(x), 50)) < 0.01
    imp = np.zeros((1 << 14, 2))
    imp[0] = 1.0  # a centred impulse: mid only, through the all-pass
    mag = np.abs(np.fft.rfft(fx.utility(bass_mono=120.0)(imp)[:, 0]))
    assert np.max(np.abs(mag - 1.0)) < 1e-9


def test_lanes_take_arrays_and_a_still_lane_is_the_scalar_unit():
    x = _stereo()
    n = len(x)
    for kw in ({"gain": 4.0}, {"width": 1.6}):
        arr = {k: np.full(n, v) for k, v in kw.items()}
        both = {"bass_mono": 90.0, "pan": -0.3}
        assert np.array_equal(fx.utility(**arr, **both)(x), fx.utility(**kw, **both)(x))
    ramp = np.linspace(-24.0, 0.0, n)
    y = fx.utility(gain=ramp)(x)
    np.testing.assert_allclose(y, x * 10 ** (ramp / 20)[:, None], atol=1e-15)


def test_silent_causal_and_mono_in_mono_out():
    assert not np.any(fx.utility(gain=12.0, bass_mono=200.0)(np.zeros((512, 2))))
    x = _stereo(4096)
    y = x.copy()
    y[2048:] = -y[2048:] * 4
    u = fx.utility(gain=2.0, width=1.5, bass_mono=150.0, pan=0.2, flip_r=1.0)
    np.testing.assert_array_equal(u(x)[:2048], u(y)[:2048])
    assert u(x[:, 0]).shape == (4096,)
