import numpy as np

from synthlab.analysis.bass import mono_compat, phone_test, sub_spectrum
from synthlab.core import SR


def _tone(freq, seconds=2.0, sr=SR):
    t = np.arange(int(sr * seconds)) / sr
    return 0.5 * np.sin(2 * np.pi * freq * t)


def test_sub_spectrum_peaks_at_the_tone():
    spec = sub_spectrum(_tone(45.0))
    freqs = np.array([f for f, _ in spec])
    mags = np.array([m for _, m in spec])
    assert freqs[0] >= 10.0 and freqs[-1] <= 120.0
    assert np.all(np.diff(freqs) <= 0.5 + 1e-9), "resolution coarser than 0.5 Hz"
    assert abs(freqs[int(np.argmax(mags))] - 45.0) < 1.0


def test_mono_compat_flags_phase_flipped_sub():
    x = _tone(50.0)
    good = mono_compat(np.column_stack([x, x]))
    bad = mono_compat(np.column_stack([x, -x]))
    assert good["mono_sub_ok"] is True
    assert bad["mono_sub_ok"] is False
    assert bad["low_correlation"] < -0.9


def test_phone_test_pure_sub_dies_harmonics_survive():
    pure_sub = phone_test(_tone(40.0))
    assert pure_sub["survives_ratio"] < 0.1
    bright = phone_test(_tone(800.0))
    assert bright["survives_ratio"] > 0.9
