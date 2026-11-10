import numpy as np

from synthlab.analysis.features import FEATURE_NAMES, extract
from synthlab.core import SR


def _tone(freq, seconds=1.0, sr=SR):
    t = np.arange(int(sr * seconds)) / sr
    return 0.5 * np.sin(2 * np.pi * freq * t)


def test_sub_bass_sine_has_high_sub_energy():
    f = extract(_tone(50.0))
    assert f.sub_20_60 > 0.8
    assert f.sub_60_120 < 0.1
    assert f.centroid < 120


def test_midrange_sine_has_no_sub_energy():
    f = extract(_tone(1000.0))
    assert f.sub_20_60 < 0.01
    assert 800 < f.centroid < 1300


def test_white_noise_is_flat_and_wide_spectrum():
    rng = np.random.default_rng(0)
    f = extract(rng.uniform(-0.5, 0.5, SR))
    assert f.flatness > 0.5
    assert f.rolloff > 10_000


def test_sine_is_not_flat():
    assert extract(_tone(440.0)).flatness < 0.05


def test_attack_time_slow_vs_fast():
    x = _tone(220.0, seconds=1.0)
    ramp = np.linspace(0, 1, len(x))
    slow = extract(x * ramp)
    fast = extract(x)
    assert slow.attack_time > 0.5
    assert fast.attack_time < 0.1


def test_stereo_width_mono_is_zero():
    x = _tone(300.0)
    f = extract(np.column_stack([x, x]))
    assert f.stereo_width < 0.01


def test_vector_matches_names_and_is_finite():
    f = extract(_tone(100.0))
    v = f.vector()
    assert v.shape == (len(FEATURE_NAMES),)
    assert np.all(np.isfinite(v))
    assert isinstance(f.to_dict()["centroid"], float)


def test_moving_average_is_the_convolution_it_replaced():
    from synthlab.analysis.features import _moving_average

    rng = np.random.default_rng(3)
    for n, win in ((SR, SR // 200), (1001, 240), (239, 240), (5, 1), (240, 240)):
        x = np.abs(rng.standard_normal(n))
        want = np.convolve(x, np.ones(win) / win, mode="same")
        np.testing.assert_allclose(_moving_average(x, win), want, rtol=1e-9, atol=1e-12)


def test_meter_agrees_with_extract():
    """The render meter's lean path: sub share and centroid as `extract`
    measures them, to within the rounding the meter displays."""
    from synthlab.analysis import meter

    rng = np.random.default_rng(1)
    # 7919 samples: a prime length, the case zero padding exists for
    for x in (
        _tone(50.0),
        _tone(1000.0, seconds=7919 / SR),
        np.column_stack([_tone(80.0) + _tone(3000.0), rng.uniform(-0.3, 0.3, SR)]),
    ):
        f = extract(x)
        sub, centroid = meter(x)
        assert abs(100 * sub - 100 * (f.sub_20_60 + f.sub_60_120)) < 0.5
        assert abs(centroid - f.centroid) < 0.01 * f.centroid + 2
