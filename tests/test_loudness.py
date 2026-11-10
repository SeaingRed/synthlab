"""Integrated loudness (`analysis.loudness`, ITU-R BS.1770-4) against the
values the standards publish.

The signals are EBU Tech 3341's conformance cases, generated rather than
downloaded: stereo sines, in phase, whose level in dBFS (0 dBFS = a
full-scale sine) is by construction their loudness in LUFS. Each must read
within the document's own tolerance, 0.1 LU — at 48 kHz, where the
standard tabulates the filter, and at the engine's 44.1 kHz, where the
filter is designed from the same prototypes.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from synthlab import _native
from synthlab.analysis import integrated_loudness, peak_db, true_peak_db
from synthlab.analysis.loudness import block_loudness, k_weighting
from synthlab.core import SR

RATES = [48000, SR]


def _tone(sections, sr, freq=1000.0, channels=2):
    """Sections of (seconds, dBFS) as one phase-continuous sine, the same
    in every channel."""
    amp = np.concatenate(
        [np.full(int(round(s * sr)), 10 ** (db / 20)) for s, db in sections]
    )
    x = amp * np.sin(2 * np.pi * freq * np.arange(len(amp)) / sr)
    return np.column_stack([x] * channels) if channels > 1 else x


def test_k_weighting_at_48k_is_the_standards_table():
    """BS.1770-4 Tables 1 and 2: the shelf, then the RLB high-pass."""
    (b1, a1), (b2, a2) = k_weighting(48000)
    np.testing.assert_allclose(
        b1, [1.53512485958697, -2.69169618940638, 1.19839281085285], atol=1e-9
    )
    np.testing.assert_allclose(
        a1, [1.0, -1.69065929318241, 0.73248077421585], atol=1e-9
    )
    np.testing.assert_allclose(b2, [1.0, -2.0, 1.0])
    np.testing.assert_allclose(
        a2, [1.0, -1.99004745483398, 0.99007225036621], atol=1e-9
    )


@pytest.mark.parametrize("sr", RATES)
def test_a_full_scale_997_hz_sine_in_one_channel_reads_minus_3_01(sr):
    """The standard's own calibration point (what its -0.691 is for)."""
    x = _tone([(20.0, 0.0)], sr, freq=997.0)
    x[:, 1] = 0.0
    assert integrated_loudness(x, sr) == pytest.approx(-3.01, abs=0.02)


@pytest.mark.parametrize("sr", RATES)
@pytest.mark.parametrize("level", [-23.0, -33.0])
def test_a_stereo_sine_reads_its_own_level(sr, level):
    """Tech 3341 cases 1 and 2: 1 kHz, 20 s, at -23 and -33 dBFS."""
    got = integrated_loudness(_tone([(20.0, level)], sr), sr)
    assert got == pytest.approx(level, abs=0.1)


@pytest.mark.parametrize("sr", RATES)
def test_the_relative_gate_drops_the_quiet_sections(sr):
    """Tech 3341 case 3: 10 s at -36, 60 s at -23, 10 s at -36 reads -23.
    Ungated, the same signal reads -24.2: the quiet quarter of it pulls
    the mean down."""
    x = _tone([(10.0, -36.0), (60.0, -23.0), (10.0, -36.0)], sr)
    assert integrated_loudness(x, sr) == pytest.approx(-23.0, abs=0.1)
    ungated = -0.691 + 10 * math.log10(block_loudness(x, sr).mean())
    assert ungated == pytest.approx(-24.15, abs=0.1)


@pytest.mark.parametrize("sr", RATES)
def test_the_absolute_gate_drops_what_is_all_but_silent(sr):
    """Tech 3341 case 4: case 3 between two 10 s stretches at -72 dBFS."""
    quiet = [(10.0, -72.0), (10.0, -36.0)]
    x = _tone([*quiet, (60.0, -23.0), *reversed(quiet)], sr)
    assert integrated_loudness(x, sr) == pytest.approx(-23.0, abs=0.1)


@pytest.mark.parametrize("sr", RATES)
def test_sections_inside_the_relative_gate_average_as_power(sr):
    """Tech 3341 case 5: 20 s at -26, 20.1 s at -20, 20 s at -26 is -23."""
    x = _tone([(20.0, -26.0), (20.1, -20.0), (20.0, -26.0)], sr)
    assert integrated_loudness(x, sr) == pytest.approx(-23.0, abs=0.1)


def test_twice_the_amplitude_is_six_db_louder_and_mono_is_one_channel():
    x = _tone([(5.0, -20.0)], SR)
    base = integrated_loudness(x)
    assert integrated_loudness(2 * x) == pytest.approx(base + 6.0206, abs=1e-6)
    # one channel carries half the power of the same signal in two
    assert integrated_loudness(x[:, 0]) == pytest.approx(base - 3.0103, abs=1e-6)


def test_silence_and_too_short_a_signal_have_no_loudness():
    assert integrated_loudness(np.zeros((SR * 2, 2))) == -math.inf
    assert integrated_loudness(np.zeros((0, 2))) == -math.inf
    assert integrated_loudness(_tone([(0.3, -10.0)], SR)) == -math.inf  # < 400 ms
    assert integrated_loudness(_tone([(3.0, -90.0)], SR)) == -math.inf  # under -70
    assert len(block_loudness(_tone([(0.4, -10.0)], SR))) == 1


def test_native_and_numpy_read_the_same(monkeypatch):
    x = np.random.default_rng(5).standard_normal((3 * SR, 2)) * 0.1
    here = integrated_loudness(x)
    monkeypatch.setattr(_native, "ENABLED", False)
    # equal on x86; ARM64 differs in the 14th decimal
    assert integrated_loudness(x) == pytest.approx(here, abs=1e-9)


def test_true_peak_finds_what_lies_between_the_samples():
    """A sine at a quarter of the rate, sampled 45 degrees off its crests:
    every sample is 3 dB under the wave's real peak."""
    n = np.arange(SR)
    x = np.sin(2 * np.pi * n / 4 + np.pi / 4)
    assert peak_db(x) == pytest.approx(-3.01, abs=0.01)
    # (0.12 dB over: the interpolator's ripple on the tone's abrupt start)
    assert true_peak_db(x) == pytest.approx(0.0, abs=0.2)
    assert true_peak_db(np.column_stack([x, 0.5 * x])) == pytest.approx(0.0, abs=0.2)
    assert (
        peak_db(np.zeros(8)) == -math.inf
        and true_peak_db(np.zeros((0, 2))) == -math.inf
    )


def test_true_peak_of_a_long_mix_never_holds_its_oversampled_copy():
    """A song at the length cap is 170 MB of float64; oversampled whole it
    was 4 x that, read up to five times an export (1.5 GB at the peak).
    Read in blocks it is the same number, bit for bit — each block is
    resampled with its neighbours' frames either side of it — and holds
    less than the mix itself."""
    import tracemalloc

    from scipy.signal import resample_poly

    rng = np.random.default_rng(3)
    x = rng.standard_normal((20 * SR, 2)) * 0.2
    x[len(x) // 2 + 17] = (0.9, -0.95)  # the peak, mid-way and off a block edge
    whole = peak_db(resample_poly(x, 4, 1, axis=0))
    tracemalloc.start()
    try:
        got = true_peak_db(x)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert got == whole
    assert peak < x.nbytes
