"""The parametric EQ (`effects.eq`): a low shelf, a swept mid peak and a
high shelf, measured against the curve they were designed to.

The response is measured, not read off the coefficients: an impulse goes
through the effect itself and its FFT is the curve (the filters are
short IIRs: 2 s holds every tail). So a band wired to the wrong corner, a
gain in the wrong unit or a band run twice all fail here.
"""

from __future__ import annotations

import numpy as np
import pytest

from synthlab import _native
from synthlab import effects as fx
from synthlab.core import SR
from synthlab.studio.fxrack import FX_UNITS, build_chain, clean_chain
from synthlab.studio.render import RenderInputs, _wrap_tile
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch

N = 2 * SR


def _response_db(effect, freqs):
    """The effect's gain in dB at each of `freqs`, from its impulse response."""
    x = np.zeros(N)
    x[0] = 1.0
    h = np.fft.rfft(effect(x))
    bins = np.round(np.asarray(freqs, dtype=float) * N / SR).astype(int)
    return 20 * np.log10(np.abs(h[bins]))


def _noise(stereo=True, seed=3):
    rng = np.random.default_rng(seed)
    return rng.standard_normal((SR, 2) if stereo else SR) * 0.2


@pytest.mark.parametrize("gain", [-12.0, -4.5, 3.0, 12.0])
@pytest.mark.parametrize("freq", [100.0, 1000.0, 8000.0])
def test_the_mid_band_has_its_gain_at_its_centre_and_none_far_away(freq, gain):
    got = _response_db(fx.eq(mid_freq=freq, mid_gain=gain, mid_q=2.0), [freq])
    assert got[0] == pytest.approx(gain, abs=0.01)
    # two decades away the peak is gone (measured < 0.02 dB)
    far = _response_db(fx.eq(mid_freq=1000.0, mid_gain=gain, mid_q=2.0), [20.0, 18000])
    assert np.max(np.abs(far)) < 0.05


@pytest.mark.parametrize("q", [0.5, 1.0, 4.0])
def test_mid_q_is_the_centre_over_the_half_gain_bandwidth(q):
    """RBJ's peaking Q: the two frequencies where the gain is half the
    peak's (in dB) are fc / Q apart."""
    f = np.arange(20.0, 20000.0, 0.5)
    db = _response_db(fx.eq(mid_freq=1000.0, mid_gain=6.0, mid_q=q), f)
    inside = f[db >= 3.0]
    assert (inside[-1] - inside[0]) == pytest.approx(1000.0 / q, rel=0.02)


@pytest.mark.parametrize("gain", [-12.0, -6.0, 6.0, 12.0])
def test_the_shelves_reach_their_gain_at_their_end_and_leave_the_other_alone(gain):
    low = _response_db(fx.eq(low=gain), [20.0, fx.EQ_LOW_HZ, 5000.0, 20000.0])
    assert low[0] == pytest.approx(gain, abs=0.35)  # 20 Hz: nearly the plateau
    assert low[1] == pytest.approx(gain / 2, abs=0.01)  # the corner: half, in dB
    assert np.max(np.abs(low[2:])) < 0.05
    high = _response_db(fx.eq(high=gain), [20.0, 200.0, fx.EQ_HIGH_HZ, 21000.0])
    assert np.max(np.abs(high[:2])) < 0.05
    assert high[2] == pytest.approx(gain / 2, abs=0.01)
    assert high[3] == pytest.approx(gain, abs=0.35)


def test_the_bands_add_in_db():
    """In series: the whole curve is the three bands' curves summed."""
    f = np.geomspace(20.0, 20000.0, 60)
    whole = _response_db(
        fx.eq(low=5.0, mid_freq=700.0, mid_gain=-8.0, mid_q=3.0, high=-3.0), f
    )
    parts = (
        _response_db(fx.eq(low=5.0), f)
        + _response_db(fx.eq(mid_freq=700.0, mid_gain=-8.0, mid_q=3.0), f)
        + _response_db(fx.eq(high=-3.0), f)
    )
    np.testing.assert_allclose(whole, parts, atol=1e-6)


@pytest.mark.parametrize("stereo", [False, True])
def test_every_gain_at_zero_is_an_exact_bypass(stereo):
    """Bit for bit, whatever the mid band's frequency and Q say: adding the
    unit at its defaults changes no render."""
    x = _noise(stereo)
    assert fx.eq()(x) is x
    np.testing.assert_array_equal(fx.eq(mid_freq=250.0, mid_q=6.0)(x), x)
    rack = build_chain([{"kind": "eq"}], Arrangement())
    np.testing.assert_array_equal(fx.chain(x, rack), x)


@pytest.mark.parametrize("stereo", [False, True])
def test_it_takes_mono_or_stereo_and_keeps_the_shape(stereo):
    x = _noise(stereo)
    y = fx.eq(low=4.0, mid_gain=-6.0, high=2.0)(x)
    assert y.shape == x.shape and np.all(np.isfinite(y))
    assert not np.allclose(y, x)
    if stereo:  # each channel is filtered on its own
        mono = fx.eq(low=4.0, mid_gain=-6.0, high=2.0)(x[:, 1].copy())
        np.testing.assert_array_equal(y[:, 1], mono)


def test_it_is_causal_and_never_normalises():
    """What comes out up to sample n depends on nothing after it, and
    twice the input is twice the output (the live keys run it sample by
    sample: the causal contract)."""
    x = _noise()
    e = fx.eq(low=6.0, mid_freq=400.0, mid_gain=9.0, mid_q=4.0, high=-6.0)
    cut = SR // 3
    later = x.copy()
    later[cut:] *= 5.0
    np.testing.assert_array_equal(e(x)[:cut], e(later)[:cut])
    np.testing.assert_allclose(e(2.0 * x), 2.0 * e(x), rtol=1e-12, atol=1e-15)


@pytest.mark.parametrize("stereo", [False, True])
def test_native_and_numpy_agree_bit_for_bit(stereo, monkeypatch):
    """The bands run through `core._lfilter`: the native kernel when it is
    loaded, scipy's loop under SYNTHLAB_NATIVE=0. Bit for bit on x86; on
    Windows ARM the two round differently (3e-14 measured on ARM64),
    hence the tolerance."""
    x = _noise(stereo)
    e = fx.eq(low=-7.0, mid_freq=2200.0, mid_gain=8.0, mid_q=5.0, high=4.0)
    here = e(x)
    monkeypatch.setattr(_native, "ENABLED", False)
    np.testing.assert_allclose(e(x), here, rtol=0, atol=1e-12)


def test_a_loops_folded_tail_is_what_a_looping_player_hears():
    """The stem's FX run over the un-tiled render and `_wrap_tile` folds
    what rings past the loop's end onto its start. The EQ is linear and
    time-invariant, so that fold IS its steady state on the looping
    signal: the last of many repeats run straight through it."""
    own = SR // 2
    x = _noise()[:own]
    e = fx.eq(low=9.0, mid_freq=180.0, mid_gain=12.0, mid_q=8.0, high=-9.0)
    folded = _wrap_tile(e(np.vstack([x, np.zeros_like(x)])), own, own)
    steady = e(np.vstack([x] * 6))[-own:]
    np.testing.assert_allclose(folded, steady, atol=1e-9)


def test_the_rack_clamps_the_eq_and_fills_its_defaults():
    dirty = [
        {
            "kind": "eq",
            "params": {
                "low": 99.0,
                "mid_freq": 5.0,
                "mid_gain": "loud",
                "mid_q": float("inf"),
                "low_hz": 5000.0,  # the corners are not the rack's to move
            },
        }
    ]
    (unit,) = clean_chain(dirty)
    assert unit["params"] == {
        "low": 12.0,
        "mid_freq": 100.0,
        "mid_gain": 0.0,
        "mid_q": 8.0,
        "high": 0.0,
    }
    assert FX_UNITS["eq"]["label"] == "EQ"
    (default,) = clean_chain([{"kind": "eq"}])
    assert [default["params"][k] for k in ("low", "mid_gain", "high")] == [0, 0, 0]
    (built,) = build_chain(dirty, Arrangement())
    # 1:1 onto effects.eq, whose corners stay its own
    fixed = {"low_hz": fx.EQ_LOW_HZ, "high_hz": fx.EQ_HIGH_HZ, "slope": fx.EQ_SLOPE}
    assert built.keywords == {**unit["params"], **fixed}


def test_an_eq_setting_is_in_the_render_key():
    """The unit dict rides the stem and master keys like every unit's: a
    moved band can never be served another setting's audio."""

    def key(scope, **params):
        units = [{"kind": "eq", "params": params}]
        return RenderInputs(patch=Patch(), arr=Arrangement(), **{scope: units}).key()

    for scope in ("main_fx", "master_fx"):
        assert key(scope, low=3.0) != key(scope, low=3.5)
        assert key(scope, mid_freq=900.0) != key(scope, mid_freq=1000.0)
        assert key(scope, low=3.0) == key(scope, low=3.0)
