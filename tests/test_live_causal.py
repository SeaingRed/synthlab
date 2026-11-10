"""Engine rules a realtime voice can follow.

The live voice (studio/static/voice-worklet.js) renders one sample at a
time, so it can only reproduce what the engine decides from the past. Four
places used to look at the whole note first; each is now defined causally,
and these tests pin the definitions (test_live_parity_corpus.py then holds
the worklet to them)."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import lfilter

from synthlab import core
from synthlab.core import SR, colour_noise, formant, formant_makeup, noise, pluck, saw
from synthlab.synth import Patch, _apply_mod_matrix


def _band_db(x: np.ndarray, lo: float, hi: float) -> float:
    """Mean power spectral density across [lo, hi) Hz, in dB."""
    p = np.abs(np.fft.rfft(x)) ** 2 / len(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return 10 * np.log10(p[(f >= lo) & (f < hi)].mean())


@pytest.mark.parametrize("color", ["pink", "brown"])
def test_coloured_noise_level_does_not_depend_on_the_note_length(color):
    """It was shaped over the whole buffer and scaled by that buffer's own
    peak: brown moved ~10 dB between a 0.3 s and a 4 s note."""
    short = np.mean(
        [_band_db(noise(int(0.3 * SR), color, s), 800, 1250) for s in range(24)]
    )
    long = np.mean(
        [_band_db(noise(int(4.0 * SR), color, s), 800, 1250) for s in range(6)]
    )
    assert abs(short - long) < 0.5, (short, long)


def test_pink_is_kellets_filter_sample_by_sample():
    w = np.random.default_rng(3).standard_normal(4000)
    b = np.zeros(6)
    last = 0.0
    want = np.empty_like(w)
    for i, x in enumerate(w):
        b = np.array(core.PINK_POLES) * b + np.array(core.PINK_TAPS) * x
        want[i] = (
            b.sum() + core.PINK_LAST * last + core.PINK_DIRECT * x
        ) * core.PINK_GAIN
        last = x
    np.testing.assert_allclose(colour_noise(w, "pink"), want, atol=1e-12)


def test_brown_is_a_leaky_integrator():
    w = np.random.default_rng(4).standard_normal(4000)
    r = np.exp(-2 * np.pi * core.BROWN_CORNER / SR)
    want = lfilter([core.BROWN_GAIN], [1.0, -r], w)
    np.testing.assert_allclose(colour_noise(w, "brown"), want, atol=1e-12)


def test_white_noise_is_unchanged():
    np.testing.assert_array_equal(
        noise(1000, "white", 5), np.random.default_rng(5).standard_normal(1000) * 0.3
    )


@pytest.mark.parametrize("freq", [55.0, 440.0, 3000.0])
def test_pluck_is_normalised_by_its_first_period(freq):
    """The first period's peak is known when the string is plucked, and it
    is the whole note's peak: nothing later can exceed it."""
    y = pluck(freq, SR, damping=0.4, bright=0.8, seed=2)
    period = int(SR / freq)
    assert np.max(np.abs(y[:period])) == pytest.approx(0.8)
    assert np.max(np.abs(y)) == pytest.approx(0.8)


def test_formant_makeup_is_the_tracts_not_the_notes():
    """A fixed gain per vowel/size, so the filter is linear: it used to
    divide by the note's own RMS ratio, which only the whole note knows."""
    x = 0.5 * saw(165.0, SR // 2, SR)
    a = formant(x, 0.3, 1.0, 1.1)
    np.testing.assert_allclose(formant(0.25 * x, 0.3, 1.0, 1.1), 0.25 * a, atol=1e-12)


def test_formant_makeup_table_is_capped_and_read_bilinearly():
    table = core.formant_makeup_table()
    assert table.shape == (core.FORMANT_MAKEUP_VOWELS, core.FORMANT_MAKEUP_SIZES[2])
    assert table.max() <= core._FORMANT_MAKEUP_MAX
    lo, hi, n = core.FORMANT_MAKEUP_SIZES
    sizes = np.geomspace(lo, hi, n)
    # on the grid it is the table itself
    assert formant_makeup(0.5, sizes[20]) == pytest.approx(table[16, 20])
    assert formant_makeup(1.0, hi) == pytest.approx(table[-1, -1])
    # a saw through any of the five vowels at a normal tract needs 3-8x
    for v in np.linspace(0, 1, 5):
        assert 2.5 < formant_makeup(v, 1.0) < 8.0


def test_a_second_route_clips_to_the_patchs_own_value():
    """The span each route clips to is widened to the patch's value — not
    to an earlier route's output, whose min/max over the note the live
    voice can't know at the first sample."""
    p = Patch(
        cutoff=12000.0,
        mod1_src="velocity",
        mod1_dst="cutoff",
        mod1_amt=-1.0,
        mod2_src="velocity",
        mod2_dst="cutoff",
        mod2_amt=0.1,
    )
    out = _apply_mod_matrix(p, 64, SR, 1.0, None)
    # route 1: 12000 - 7940 = 4060; route 2 adds 794 inside [60, 12000]
    np.testing.assert_allclose(out.cutoff, 4060.0 + 794.0)
