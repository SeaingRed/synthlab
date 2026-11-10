"""Ring mod, comb resonator, and the wavefolder — the tone-shaping trio.

Each is checked by what it does to the *spectrum*, not just "the output
changed": a ring mod that fails to produce sidebands, or a comb that
doesn't resonate at its tune, is still a signal that differs from dry.
"""

import numpy as np

from synthlab import effects as fx
from synthlab.core import SR
from synthlab.studio.fxrack import FX_UNITS

FX_MAX_FEEDBACK = FX_UNITS["comb"]["params"]["feedback"][1]


def _sine(freq=1000.0, seconds=0.5, amp=0.5):
    t = np.arange(int(SR * seconds)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


def _stereo(x):
    return np.column_stack([x, x])


def _spectrum(x):
    mono = x[:, 0] if x.ndim == 2 else x
    mag = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    freqs = np.fft.rfftfreq(len(mono), 1 / SR)
    return freqs, mag


def _energy_near(freqs, mag, target, width=25.0):
    band = np.abs(freqs - target) <= width
    return float(mag[band].sum())


# ---------------------------------------------------------------- ring mod


def test_ringmod_makes_sum_and_difference_sidebands():
    """A 1000 Hz tone ring-modulated at 300 Hz becomes 700 + 1300 Hz, and the
    original carrier is suppressed — that is what makes it sound metallic
    rather than merely distorted."""
    x = _sine(1000.0)
    y = fx.chain(x, [fx.ringmod(freq=300.0, mix=1.0)])
    freqs, mag = _spectrum(y)
    lower = _energy_near(freqs, mag, 700.0)
    upper = _energy_near(freqs, mag, 1300.0)
    carrier = _energy_near(freqs, mag, 1000.0)
    assert lower > carrier * 10, (lower, carrier)
    assert upper > carrier * 10, (upper, carrier)


def test_ringmod_mix_zero_is_dry():
    x = _stereo(_sine())
    np.testing.assert_allclose(fx.chain(x, [fx.ringmod(mix=0.0)]), x, atol=1e-9)


def test_ringmod_keeps_shape_mono_and_stereo():
    mono = _sine()
    assert fx.chain(mono, [fx.ringmod()]).shape == mono.shape
    st = _stereo(mono)
    out = fx.chain(st, [fx.ringmod()])
    assert out.shape == st.shape and np.all(np.isfinite(out))


# -------------------------------------------------------------------- comb


def test_comb_resonates_at_its_tune_frequency():
    """White noise through a comb should grow a peak at the tune and its
    harmonics — the tuned-resonator behaviour that gives it a pitch."""
    rng = np.random.default_rng(0)
    noise = 0.2 * rng.standard_normal(int(SR * 0.5))
    tune = 220.0
    y = fx.chain(noise, [fx.comb(tune=tune, feedback=0.85, mix=1.0)])
    freqs, wet = _spectrum(y)
    _, dry = _spectrum(noise)
    # a narrow band: at this feedback the teeth are sharp, and averaging over
    # a wide window measures the gaps between them as much as the peak
    tooth = 6.0
    gain = _energy_near(freqs, wet, tune, tooth) / max(
        _energy_near(freqs, dry, tune, tooth), 1e-9
    )
    # and a frequency deliberately between two comb teeth must not be boosted
    between = tune * 1.5
    null = _energy_near(freqs, wet, between, tooth) / max(
        _energy_near(freqs, dry, between, tooth), 1e-9
    )
    assert gain > 3.0, gain
    assert gain > null * 2, (gain, null)


def test_comb_is_stable_at_maximum_feedback():
    """Feedback is the one param that can run away; the render must stay
    finite and bounded however hard it is pushed."""
    x = _sine(110.0)
    hi = FX_MAX_FEEDBACK
    y = fx.chain(x, [fx.comb(tune=110.0, feedback=hi, mix=1.0)])
    assert np.all(np.isfinite(y))
    assert np.max(np.abs(y)) < 50.0, np.max(np.abs(y))


def test_comb_mix_zero_is_dry():
    x = _stereo(_sine())
    np.testing.assert_allclose(fx.chain(x, [fx.comb(mix=0.0)]), x, atol=1e-9)


def test_comb_keeps_shape_mono_and_stereo():
    mono = _sine()
    assert fx.chain(mono, [fx.comb()]).shape == mono.shape
    st = _stereo(mono)
    out = fx.chain(st, [fx.comb()])
    assert out.shape == st.shape and np.all(np.isfinite(out))


# -------------------------------------------------------------------- fold


def test_fold_adds_harmonics_a_clean_sine_does_not_have():
    """The wavefolder was written but never reachable from the UI. Pin what
    it is for: folding a pure sine has to create overtones."""
    x = _sine(200.0, amp=0.9)
    y = fx.chain(x, [fx.fold(amount=1.8)])
    freqs, wet = _spectrum(y)
    _, dry = _spectrum(x)
    third = 600.0
    assert _energy_near(freqs, wet, third) > _energy_near(freqs, dry, third) * 10
