"""The convolution unit (`effects.convolve`): a sample from the store as the
impulse response, named by its content, its gain the IR's own."""

from __future__ import annotations

import numpy as np
from scipy.signal import fftconvolve

from synthlab import effects as fx
from synthlab import sampler
from synthlab.core import SR


def _ir(seconds: float = 0.5, level: float = 0.3, seed: int = 1) -> str:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    ir = rng.standard_normal((n, 2)) * np.exp(-np.arange(n) / (0.1 * SR))[:, None]
    return sampler.register(ir * level)


def _noise(n: int = SR // 2, seed: int = 4) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal((n, 2)) * 0.1


def test_no_impulse_is_a_dry_pass_at_any_mix():
    x = _noise()
    for ir in ("", "0123456789abcdef", "../../etc", None, 7):
        for mix in (0.0, 0.5, 1.0, np.linspace(0, 1, len(x))):
            assert fx.convolve(ir=ir, mix=mix)(x) is x
    silent = sampler.register(np.zeros((1000, 2)))
    assert fx.convolve_ir(silent) is None and fx.convolve(ir=silent)(x) is x


def test_the_wet_is_each_channel_through_its_ir_at_the_irs_own_gain():
    sid = _ir()
    x = _noise()
    room = fx.convolve_ir(sid)
    raw = sampler.get(sid).astype(np.float64)
    np.testing.assert_allclose(room, raw / np.sqrt(np.mean(np.sum(raw**2, axis=0))))
    wet = fx.convolve(ir=sid, mix=1.0)(x)
    want = np.column_stack([fftconvolve(x[:, c], room[:, c])[: len(x)] for c in (0, 1)])
    np.testing.assert_allclose(wet, want, atol=1e-12)
    half = fx.convolve(ir=sid, mix=0.25)(x)
    np.testing.assert_allclose(half, x * 0.75 + want * 0.25, atol=1e-12)


def test_the_gain_does_not_depend_on_the_input_or_the_recordings_level():
    """1 / ||ir||: an impulse's wet carries unit energy whatever level the
    IR was recorded at, and twice the input is twice the output."""
    loud, quiet = _ir(level=0.9, seed=2), _ir(level=0.05, seed=2)
    imp = np.zeros((SR, 2))
    imp[0] = 1.0
    for sid in (loud, quiet):
        y = fx.convolve(ir=sid, mix=1.0)(imp)
        assert abs(np.sqrt(np.mean(np.sum(y**2, axis=0))) - 1.0) < 1e-3
    x = _noise()
    u = fx.convolve(ir=loud, mix=0.6)
    np.testing.assert_allclose(u(2 * x), 2 * u(x), atol=1e-12)


def test_predelay_and_the_cuts():
    sid = _ir()
    base = fx.convolve_ir(sid)
    late = fx.convolve_ir(sid, predelay=0.02)
    pre = int(round(0.02 * SR))
    assert not np.any(late[:pre]) and np.allclose(late[pre:], base, atol=1e-12)
    # the ends of the cuts' ranges run no filter
    assert np.array_equal(fx.convolve_ir(sid, lowcut=20.0, highcut=20000.0), base)
    spec = lambda a: np.abs(np.fft.rfft(a[:, 0], 1 << 16))  # noqa: E731
    f = np.fft.rfftfreq(1 << 16, 1 / SR)
    low = lambda a: spec(a)[f < 60].mean() / spec(a)[(f > 2000) & (f < 3000)].mean()  # noqa: E731
    high = lambda a: spec(a)[f > 15000].mean() / spec(a)[(f > 2000) & (f < 3000)].mean()  # noqa: E731
    assert low(fx.convolve_ir(sid, lowcut=600.0)) < 0.1 * low(base)
    assert high(fx.convolve_ir(sid, highcut=2000.0)) < 0.1 * high(base)


def test_an_ir_is_cut_at_its_cap():
    long = _ir(seconds=12.0)
    room = fx.convolve_ir(long)
    assert len(room) == int(fx.CONVOLVE_MAX_SECONDS * SR)


def test_causal_silent_mono_and_another_rate():
    sid = _ir()
    u = fx.convolve(ir=sid, mix=0.5)
    assert not np.any(u(np.zeros((4096, 2))))
    x = _noise(8192)
    y = x.copy()
    y[4096:] *= -5
    # (an FFT's rounding reaches across the buffer: 1e-16, as the reverb's)
    np.testing.assert_allclose(u(x)[:4096], u(y)[:4096], rtol=0, atol=1e-12)
    assert u(x[:, 0]).shape == (8192,)
    at48 = fx.convolve_ir(sid, sr=48000)
    assert abs(len(at48) - len(fx.convolve_ir(sid)) * 48000 / SR) <= 1
