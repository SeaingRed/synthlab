"""Phaser, vinyl channel, and trance gate."""

import numpy as np

from synthlab import effects as fx
from synthlab.core import SR


def _saw_stereo(seconds=0.5, freq=110.0):
    t = np.arange(int(SR * seconds)) / SR
    x = 0.4 * (2 * ((t * freq) % 1.0) - 1)
    return np.column_stack([x, x])


def test_phaser_changes_signal_and_stays_finite():
    x = _saw_stereo()
    y = fx.chain(x, [fx.phaser(rate=0.8, depth=0.9, mix=0.6)])
    assert y.shape == x.shape
    assert np.all(np.isfinite(y))
    assert not np.allclose(y, x, atol=1e-4)
    # stereo LFO offset: channels should decorrelate
    assert not np.allclose(y[:, 0], y[:, 1], atol=1e-4)


def test_phaser_mix_zero_is_dry():
    x = _saw_stereo()
    y = fx.chain(x, [fx.phaser(mix=0.0)])
    np.testing.assert_allclose(y, x, atol=1e-9)


def test_vinyl_adds_noise_floor_and_stays_finite():
    x = np.zeros((SR // 2, 2))
    y = fx.chain(x, [fx.vinyl(crackle=0.8, hiss=0.5)])
    assert y.shape == x.shape
    assert np.all(np.isfinite(y))
    assert float(np.max(np.abs(y))) > 1e-4  # crackle/hiss present on silence


def test_vinyl_is_deterministic():
    x = _saw_stereo()
    a = fx.chain(x, [fx.vinyl()])
    b = fx.chain(x, [fx.vinyl()])
    np.testing.assert_allclose(a, b)


def test_gate_carves_silence_into_held_audio():
    x = np.ones((SR, 2)) * 0.5
    y = fx.chain(x, [fx.gate(pattern="x.x.x.x.", bpm=120, depth=1.0)])
    assert y.shape == x.shape
    step = int(round(60 / 120 / 4 * SR))  # 16th-note steps
    on_rms = float(np.sqrt(np.mean(y[step // 4 : step // 2, 0] ** 2)))
    off_rms = float(np.sqrt(np.mean(y[step + step // 4 : step + step // 2, 0] ** 2)))
    assert on_rms > 0.3
    assert off_rms < 0.1 * on_rms


def test_gate_depth_zero_is_transparent():
    x = _saw_stereo()
    y = fx.chain(x, [fx.gate(pattern="x...", bpm=124, depth=0.0)])
    np.testing.assert_allclose(y, x, atol=1e-9)
