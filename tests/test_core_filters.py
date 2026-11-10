"""Pin the swept-biquad behavior so the vectorized implementation in
core.biquad stays sample-accurate against a plain per-sample reference: the
TPT state-variable filter (Simper), coefficients recomputed every `block`
samples, integrator state carried across blocks.

(The reference used to be the RBJ biquad in transposed direct form with its
state carried across coefficient changes — the topology that spiked 76x on a
cutoff step. The SVF has the same transfer function at a steady cutoff; see
tests/test_engine_contracts.py.)"""

import numpy as np
import pytest
from scipy.signal import lfilter

from synthlab.core import SR, _biquad, biquad, ladder


def _reference_biquad(x, kind, fc, q, sr, block=64):
    n = len(x)
    out = np.empty(n)
    ic1 = ic2 = 0.0
    k = 1.0 / q
    for i in range(n):
        if i % block == 0:
            g = np.tan(np.pi * float(np.clip(fc[i], 20.0, sr * 0.49)) / sr)
            a1 = 1.0 / (1.0 + g * (g + k))
            a2 = g * a1
            a3 = g * a2
        v3 = x[i] - ic2
        v1 = a1 * ic1 + a2 * v3
        v2 = ic2 + a2 * ic1 + a3 * v3
        ic1 = 2 * v1 - ic1
        ic2 = 2 * v2 - ic2
        low, band, high = v2, v1, x[i] - k * v1 - v2
        out[i] = {
            "lowpass": low,
            "highpass": high,
            "bandpass": k * band,
            "notch": low + high,
        }[kind]
    return out


@pytest.fixture
def swept_input():
    rng = np.random.default_rng(7)
    n = SR // 2  # 0.5s, not a multiple of 64: exercises the ragged tail
    x = rng.standard_normal(n + 13)
    fc = np.geomspace(150.0, 6000.0, n + 13)
    return x, fc


@pytest.mark.parametrize("kind", ["lowpass", "highpass", "bandpass", "notch"])
def test_swept_biquad_matches_reference(swept_input, kind):
    x, fc = swept_input
    got = biquad(x, kind, fc, q=2.0)
    want = _reference_biquad(x, kind, fc, q=2.0, sr=SR)
    np.testing.assert_allclose(got, want, rtol=1e-9, atol=1e-12)


def test_static_cutoff_single_shot(swept_input):
    x, _ = swept_input
    b, a = _biquad("lowpass", 800.0, 0.707, SR)
    np.testing.assert_allclose(
        # atol: near zero crossings ARM64 rounding (1.5e-15) beats rtol
        biquad(x, "lowpass", 800.0),
        lfilter(b, a, x),
        rtol=1e-12,
        atol=1e-13,
    )


def test_swept_ladder_finite_and_matches_shape(swept_input):
    x, fc = swept_input
    y = ladder(x, fc, resonance=0.5, drive=1.5)
    assert y.shape == x.shape
    assert np.all(np.isfinite(y))


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        biquad(np.zeros(128), "allpass", np.linspace(100, 200, 128))


def test_stereo_biquad_matches_per_channel(swept_input):
    """One 2-D call must be sample-identical to filtering each channel
    separately — that's the contract that lets Synth.note drop its
    per-channel loop."""
    x, fc = swept_input
    st = np.column_stack([x, x[::-1]])
    got = biquad(st, "lowpass", fc, q=2.0)
    for ch in (0, 1):
        want = biquad(st[:, ch], "lowpass", fc, q=2.0)
        np.testing.assert_allclose(got[:, ch], want, rtol=1e-12, atol=1e-15)


def test_stereo_ladder_matches_per_channel(swept_input):
    x, fc = swept_input
    st = np.column_stack([x, x[::-1]])
    got = ladder(st, fc, resonance=0.5, drive=1.5)
    for ch in (0, 1):
        want = ladder(st[:, ch], fc, resonance=0.5, drive=1.5)
        np.testing.assert_allclose(got[:, ch], want, rtol=1e-12, atol=1e-15)
