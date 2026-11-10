"""FX parameters as per-sample arrays (the studio's FX automation lanes,
`synthlab/studio/fxauto.py`): a delay's feedback and the EQ's gains. The
scalar paths are untouched; an array is the unit with that parameter
moving."""

from __future__ import annotations

import numpy as np
import pytest

from synthlab import effects as fx
from synthlab.core import SR, n_samples

RNG = np.random.default_rng(11)
NOISE = RNG.standard_normal((SR, 2)) * 0.2


def test_a_feedback_array_is_the_delay_line_sample_for_sample():
    """y[i] = x[i] + feedback[i] * y[i - d], against the loop written out."""
    x = RNG.standard_normal(6000)
    fb = np.linspace(0.0, 0.9, len(x))
    d = n_samples(0.02)
    want = x.copy()
    for i in range(d, len(x)):
        want[i] += fb[i] * want[i - d]
    np.testing.assert_allclose(fx._delay_line(x, 0.02, fb, SR), want, atol=1e-12)


@pytest.mark.parametrize("ping_pong", [True, False])
def test_a_feedback_array_that_stands_still_is_the_scalar_delay(ping_pong):
    still = fx.delay(0.2, np.full(SR, 0.5), 0.4, ping_pong=ping_pong)(NOISE)
    assert np.array_equal(still, fx.delay(0.2, 0.5, 0.4, ping_pong=ping_pong)(NOISE))


@pytest.mark.parametrize(
    "bands",
    [
        {"low": 7.0},
        {"low": -12.0},
        {"high": 9.0},
        {"high": -12.0},
        {"mid_gain": 12.0, "mid_freq": 800.0, "mid_q": 2.0},
        {"mid_gain": -9.0, "mid_freq": 3000.0, "mid_q": 0.3},
        {"low": 5.0, "mid_gain": -4.0, "high": 3.0},
    ],
)
def test_the_swept_eq_at_rest_is_the_eq(bands):
    """Another realisation (a state-variable filter, the band's gain mixed
    from its outputs) of the same transfer functions: with the gains
    standing still it is `eq` to rounding — measured 6e-14 at worst."""
    want = fx.eq(**bands)(NOISE)
    gains = ("low", "mid_gain", "high")
    arrays = {k: np.full(SR, v) if k in gains else v for k, v in bands.items()}
    np.testing.assert_allclose(fx._eq_swept(NOISE, **arrays), want, atol=1e-12)
    # ... and it is what `eq` runs when handed an array
    assert np.array_equal(fx.eq(**arrays)(NOISE), fx._eq_swept(NOISE, **arrays))
    # a moving gain takes the filter's swept path: a hair's wobble is close
    wob = {k: v + 1e-11 * np.arange(SR) if k in gains else v for k, v in arrays.items()}
    np.testing.assert_allclose(fx.eq(**wob)(NOISE), want, atol=1e-6)


def test_a_band_at_zero_throughout_is_left_out_and_mono_keeps_its_shape():
    zero = np.zeros(SR)
    assert fx.eq(low=zero, mid_gain=zero, high=zero)(NOISE) is NOISE
    mono = NOISE[:, 0]
    out = fx.eq(mid_gain=np.linspace(-12.0, 12.0, SR))(mono)
    assert out.shape == mono.shape and np.all(np.isfinite(out))


def _duck_times(bpm: float = 125.0) -> list[float]:
    return [k * 60.0 / bpm for k in range(4)]


# the clocked units' lanes: (unit with the parameter an array, the same
# unit with it a scalar) — a depth for the gains, a mix for the time remaps
STILL = {
    "gate depth": lambda v: fx.gate("x.xxx.x.", 125.0, v),
    "sidechain depth": lambda v: fx.sidechain(_duck_times(), v, 0.2),
    "beat_repeat mix": lambda v: fx.beat_repeat(125.0, mix=v, pattern="x..."),
    "stop mix": lambda v: fx.stop(125.0, beats=0.5, mix=v, period=1.0),
}


@pytest.mark.parametrize("name", list(STILL))
@pytest.mark.parametrize("value", [0.0, 0.3, 1.0])
def test_a_depth_or_mix_array_that_stands_still_is_the_scalar_unit(name, value):
    """Bit for bit: the array path does the scalar path's arithmetic per
    sample (the sidechain by way of its shape — 1 - d * s never rises with
    s, so the lowest gain is the one at the deepest shape)."""
    unit = STILL[name]
    still = unit(np.full(SR, value))(NOISE)
    assert np.array_equal(still, unit(value)(NOISE))
    if value:
        assert not np.array_equal(still, NOISE)  # and it is doing something


def test_a_sidechain_depth_array_is_the_gain_formula_per_sample():
    """gain = 1 - depth[i] * shape[i], the shape being the duck at depth 1
    turned over; overlapping ducks (a release longer than the gap) included."""
    times = [k * 0.1 for k in range(10)]  # 0.1 s apart, 0.3 s release
    depth = np.linspace(0.0, 1.0, SR)
    ones = np.ones((SR, 2))
    shape = 1.0 - fx.sidechain(times, 1.0, 0.3)(ones)[:, 0]
    assert shape.min() >= 0.0 and shape.max() == 1.0
    got = fx.sidechain(times, depth, 0.3)(NOISE)
    np.testing.assert_allclose(got, NOISE * (1.0 - depth * shape)[:, None], atol=1e-15)


def test_a_gain_that_jumps_does_not_ring_up():
    """The reason for the state-variable form: its state survives a
    coefficient change. A square wave between -12 and +12 dB on every band
    stays within the level a +12 dB EQ gives."""
    sq = np.where((np.arange(SR) // 2000) % 2 == 0, -12.0, 12.0)
    out = fx.eq(low=sq, mid_gain=sq, high=-sq, mid_freq=np.linspace(100, 8000, SR))(
        NOISE
    )
    loud = fx.eq(low=12.0, mid_gain=12.0, high=12.0)(NOISE)
    assert np.all(np.isfinite(out)) and np.max(np.abs(out)) < 1.5 * np.max(np.abs(loud))


def test_the_blocks_are_close_to_a_coefficient_set_per_sample():
    """-12 to +12 dB across one second: the 64-sample blocks against a
    block of one, on white noise. Measured -71 dB (low), -63 dB (mid),
    -54 dB (high)."""
    ramp = np.linspace(-12.0, 12.0, SR)
    for band in ("low", "mid_gain", "high"):
        a = fx._eq_swept(NOISE, **{band: ramp})
        b = fx._eq_swept(NOISE, **{band: ramp}, block=1)
        err = 20 * np.log10(np.sqrt(np.mean((a - b) ** 2) / np.mean(b**2)))
        assert err < -50.0, (band, err)
