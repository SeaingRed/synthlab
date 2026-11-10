"""Wavetable oscillator: a morphable table of single-cycle waveforms.

The point is the morph — `wt_pos` sweeping across a table is the modern
sound this synth had no way to make. Tests check that the morph actually
travels (endpoints differ, middle is between them) and that a per-sample
position array sweeps, because that is what a drawn curve needs.
"""

import numpy as np
import pytest

from synthlab.core import SR, WAVETABLES, osc, wavetable


def _spec(x):
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return mag / max(mag.max(), 1e-12)


def _brightness(x):
    """Spectral centroid — a cheap 'how bright' number."""
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    return float((mag * freqs).sum() / max(mag.sum(), 1e-12))


def test_tables_are_named_and_each_has_several_frames():
    assert len(WAVETABLES) >= 3
    for name, frames in WAVETABLES.items():
        assert len(frames) >= 3, name  # something to morph between
        assert all(len(f) == len(frames[0]) for f in frames), name
        for f in frames:
            assert abs(float(np.mean(f))) < 0.15, f"{name} frame has DC offset"
            assert float(np.max(np.abs(f))) > 0.5, f"{name} frame is near-silent"


@pytest.mark.parametrize("table", sorted(WAVETABLES))
def test_morph_endpoints_are_different_sounds(table):
    a = wavetable(220.0, SR // 2, SR, table=table, pos=0.0)
    b = wavetable(220.0, SR // 2, SR, table=table, pos=1.0)
    assert not np.allclose(_spec(a), _spec(b), atol=0.05), table


def test_morph_middle_lies_between_the_endpoints():
    """A morph that jumps rather than travels is a switch, not a morph."""
    n = SR // 2
    lo = _brightness(wavetable(220.0, n, SR, table="basic", pos=0.0))
    mid = _brightness(wavetable(220.0, n, SR, table="basic", pos=0.5))
    hi = _brightness(wavetable(220.0, n, SR, table="basic", pos=1.0))
    assert min(lo, hi) <= mid <= max(lo, hi), (lo, mid, hi)
    assert lo != hi


def test_position_can_be_a_per_sample_array():
    """The reason wt_pos is CURVABLE: a drawn curve sweeps the table."""
    n = SR // 2
    sweep = np.linspace(0.0, 1.0, n)
    out = wavetable(220.0, n, SR, table="basic", pos=sweep)
    assert out.shape == (n,) and np.all(np.isfinite(out))
    half = n // 2
    assert _brightness(out[:half]) != _brightness(out[half:])


def test_frequency_can_be_an_array_like_every_other_shape():
    n = SR // 4
    glide = np.linspace(110.0, 440.0, n)
    out = wavetable(glide, n, SR, table="basic", pos=0.3)
    assert out.shape == (n,) and np.all(np.isfinite(out))


def test_output_is_bounded_and_finite_for_every_table():
    for name in WAVETABLES:
        for pos in (0.0, 0.33, 0.66, 1.0):
            y = wavetable(220.0, 4096, SR, table=name, pos=pos)
            assert np.all(np.isfinite(y)), (name, pos)
            assert np.max(np.abs(y)) <= 1.5, (name, pos, float(np.max(np.abs(y))))


def test_reachable_through_the_osc_dispatcher():
    """Every other shape is reached by name through osc(); this must be too."""
    y = osc("wavetable", 220.0, 4096, SR, table="basic", pos=0.4)
    assert y.shape == (4096,) and np.all(np.isfinite(y))


def test_unknown_table_falls_back_rather_than_raising():
    """Corrupt or future preset JSON must never crash a render."""
    y = wavetable(220.0, 2048, SR, table="no-such-table", pos=0.5)
    assert y.shape == (2048,) and np.all(np.isfinite(y))


def test_does_not_alias_at_high_pitches():
    """The engine band-limits saw/square with PolyBLEP; a wavetable built
    from sampled frames would alias badly against that bar. Frames are built
    additively and picked from a mip stack by pitch, so every significant
    partial must land on a harmonic of f0 — inharmonic peaks are folded
    (aliased) content.
    """
    f0, n = 2000.0, SR // 2
    for table in sorted(WAVETABLES):
        y = wavetable(f0, n, SR, table=table, pos=1.0)  # brightest frame
        mag = np.abs(np.fft.rfft(y * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, 1 / SR)
        strong = freqs[mag > mag.max() * 0.02]
        # distance from each strong peak to the nearest harmonic of f0
        off = np.abs(strong - np.round(strong / f0) * f0)
        assert off.max() < 60.0, (table, float(off.max()))


def test_mip_stack_dulls_rather_than_folds_as_pitch_rises():
    """Rising pitch must cost harmonics, not gain inharmonic junk."""
    from synthlab.core import _wt_mip

    assert _wt_mip(50.0, SR) > _wt_mip(2000.0, SR)
    assert _wt_mip(10000.0, SR) <= 4
