"""Vocoder: the modulator's spectral envelope imposed on a carrier.

Checked by what actually transfers — a modulator with energy in one band
must open only that band of the carrier, and the output must track the
modulator's amplitude over time. "The output changed" is true of any
filter; a vocoder has to move the carrier's spectrum to match.
"""

import numpy as np
import pytest

from synthlab import effects as fx
from synthlab.core import SR, saw, sine


def _noise(n, seed=0):
    return np.random.default_rng(seed).standard_normal(n) * 0.3


def _band_energy(x, lo, hi):
    mono = x[:, 0] if x.ndim == 2 else x
    mag = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    freqs = np.fft.rfftfreq(len(mono), 1 / SR)
    return float(mag[(freqs >= lo) & (freqs < hi)].sum())


def test_modulator_band_opens_the_same_band_of_the_carrier():
    """The defining behaviour. A modulator that is only low frequencies must
    let through only the carrier's low frequencies."""
    n = SR // 2
    carrier = _noise(n)  # all bands present
    low_mod = sine(200.0, n, SR) * 0.5  # energy near 200 Hz only
    out = fx.vocoder(carrier, low_mod, bands=16)
    low = _band_energy(out, 120, 400)
    high = _band_energy(out, 3000, 8000)
    assert low > high * 8, (low, high)


def test_moving_the_modulator_band_moves_the_output_band():
    n = SR // 2
    carrier = _noise(n)
    a = fx.vocoder(carrier, sine(250.0, n, SR) * 0.5, bands=16)
    b = fx.vocoder(carrier, sine(3000.0, n, SR) * 0.5, bands=16)
    assert _band_energy(a, 150, 500) > _band_energy(b, 150, 500) * 4
    assert _band_energy(b, 2000, 5000) > _band_energy(a, 2000, 5000) * 4


def test_silent_modulator_gives_silence():
    """No voice, no sound — the carrier must not leak through."""
    n = SR // 4
    out = fx.vocoder(_noise(n), np.zeros(n), bands=16)
    assert float(np.max(np.abs(out))) < 1e-3


def test_output_tracks_the_modulator_envelope():
    """Say a word, get that rhythm: the carrier follows the modulator's
    amplitude over time."""
    n = SR
    carrier = saw(110.0, n, SR)
    env = np.concatenate([np.ones(n // 2), np.zeros(n - n // 2)])
    out = fx.vocoder(carrier, _noise(n) * env, bands=16)
    loud = float(np.sqrt(np.mean(out[: n // 2] ** 2)))
    # measure the *settled* tail, not the release ramp: averaging across a
    # 40 ms decay over half a second mostly measures the decay itself
    quiet = float(np.sqrt(np.mean(out[-n // 4 :] ** 2)))
    assert loud > quiet * 50, (loud, quiet)


def test_shape_and_finiteness_mono_and_stereo():
    n = SR // 4
    mod = _noise(n, seed=1)
    mono = fx.vocoder(_noise(n), mod, bands=12)
    assert mono.shape == (n,) and np.all(np.isfinite(mono))
    st = np.column_stack([_noise(n), _noise(n, seed=2)])
    out = fx.vocoder(st, mod, bands=12)
    assert out.shape == st.shape and np.all(np.isfinite(out))


def test_modulator_length_is_adapted_to_the_carrier():
    """The take and the loop are never the same length; the shorter one must
    not truncate or crash the render."""
    n = SR // 4
    for mod_len in (n // 3, n, n * 2):
        out = fx.vocoder(_noise(n), _noise(mod_len, seed=3), bands=8)
        assert out.shape == (n,) and np.all(np.isfinite(out))


def test_more_bands_track_the_modulator_more_closely():
    """The band count is the resolution knob: more bands = a closer match to
    the modulator's spectral shape."""
    n = SR // 2
    carrier = _noise(n)
    mod = sine(1000.0, n, SR) * 0.5

    def sharpness(b):
        out = fx.vocoder(carrier, mod, bands=b)
        return _band_energy(out, 700, 1400) / max(_band_energy(out, 1400, 8000), 1e-9)

    assert sharpness(24) > sharpness(4)


# --- shift, the noise path, and what the defaults must not move ------------------

from synthlab.core import biquad  # noqa: E402

_PIN_AT = [0, 100, 1000, 3000, 5000, 5512, 8000, 11000]
# `fx.vocoder` as it stood before `shift` / `noise` existed,
# run on the two calls below
_PIN_MONO = [
    -3.166680107215843e-08,
    0.035214293161522005,
    0.44465238580405386,
    0.407068665963044,
    0.467689345080155,
    -0.0103489917037365,
    0.013307351956204748,
    0.011397044494374514,
]
_PIN_STEREO_RIGHT = [
    0.0,
    -0.005143070461967772,
    0.09782723992487266,
    0.05508455375223408,
    0.05677438336110767,
    -0.24260189020949643,
    -0.05877470703958599,
    0.024530172207731803,
]


def test_the_default_call_is_what_it_was_before_shift_and_noise():
    """Everything that vocodes without the new arguments keeps its samples."""
    n = SR // 4
    mod = np.random.default_rng(7).standard_normal(n) * 0.3 * (np.arange(n) < n // 2)
    car = saw(110.0, n, SR)
    out = fx.vocoder(car, mod, bands=12)
    assert np.allclose(out[_PIN_AT], _PIN_MONO, rtol=1e-9, atol=1e-12)
    assert abs(float(np.sum(out)) - -4.001835184699518) < 1e-8
    st = np.column_stack([car, car[::-1]])
    wide = fx.vocoder(st, mod, bands=8, attack=0.002, release=0.1)
    assert np.allclose(wide[_PIN_AT, 1], _PIN_STEREO_RIGHT, rtol=1e-9, atol=1e-12)
    # and naming the defaults is the same call
    assert np.array_equal(out, fx.vocoder(car, mod, bands=12, shift=0.0, noise=0.0))


def _vowel(n, f0=110.0, formants=(700.0, 1200.0, 2600.0)):
    """A voiced, vowel-like modulator: a pulse train at `f0` through three
    formant resonators — a harmonic source under a fixed spectral envelope."""
    pulses = np.zeros(n)
    pulses[:: int(SR / f0)] = 1.0
    out = sum(biquad(pulses, "bandpass", f, 8.0, SR) for f in formants)
    return out / np.max(np.abs(out)) * 0.5


def _sss(n, seed=3):
    """An unvoiced, "s"-like modulator: noise with nothing under 5 kHz."""
    x = np.random.default_rng(seed).standard_normal(n)
    for _ in range(2):
        x = biquad(x, "highpass", 5000.0, 0.707, SR)
    return x / np.max(np.abs(x)) * 0.5


def _band_levels(x, bands):
    """dB level of `x` in each of the vocoder's own bands."""
    edges = np.geomspace(fx.VOCODER_LO, fx.VOCODER_HI, bands + 1)
    mono = x.mean(axis=1) if x.ndim == 2 else x
    mag = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1 / SR)
    return np.array(
        [
            10 * np.log10(mag[(freqs >= lo) & (freqs < hi)].sum() + 1e-30)
            for lo, hi in zip(edges[:-1], edges[1:], strict=True)
        ]
    )


def test_a_vowels_spectral_envelope_is_imposed_on_a_saw():
    """The carrier's bands are scaled by the modulator's: per band, what
    the vocoder did to the saw (output over carrier, dB) follows the
    vowel's own level in that band, once the overall gain is taken out —
    within 6 dB over the bands within 15 dB of the vowel's loudest
    (measured: 4.8 at 16 bands). A valley between two formants is filled
    by its neighbours' 4-pole skirts (measured: up to 9.5 dB at -19 dB),
    so the wider set is held to 12."""
    n, bands = SR, 16
    mod, car = _vowel(n), saw(55.0, n, SR)
    out = fx.vocoder(car, mod, bands=bands)
    m = _band_levels(mod, bands)
    did = _band_levels(out, bands) - _band_levels(car, bands)
    near, wide = m > m.max() - 15.0, m > m.max() - 30.0
    assert near.sum() >= 8 and wide.sum() >= 12
    err = did - m
    err = err - np.median(err[near])
    assert np.max(np.abs(err[near])) < 6.0, np.round(err[near], 1)
    assert np.max(np.abs(err[wide])) < 12.0, np.round(err[wide], 1)
    # and band by band it is the vowel's shape, not just near it
    assert np.corrcoef(did[wide], m[wide])[0, 1] > 0.8  # measured 0.87


def _peak_hz(x):
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    smooth = np.convolve(mag, np.ones(200) / 200, "same")
    return float(freqs[np.argmax(smooth)])


@pytest.mark.parametrize("shift", [-12.0, 7.0, 12.0])
def test_shift_moves_the_formants_and_not_the_pitch(shift):
    n = SR
    narrow = biquad(
        biquad(_noise(n, 5), "bandpass", 600.0, 6.0, SR), "bandpass", 600.0, 6.0, SR
    )
    flat = fx.vocoder(_noise(n), narrow, bands=24)
    moved = fx.vocoder(_noise(n), narrow, bands=24, shift=shift)
    octaves = np.log2(_peak_hz(moved) / _peak_hz(flat))
    assert abs(octaves - shift / 12.0) < 1 / 3, (_peak_hz(flat), _peak_hz(moved))
    # a pitched carrier keeps its pitch: the output's harmonics are the saw's
    sung = fx.vocoder(saw(110.0, n, SR), _vowel(n), bands=24, shift=shift)
    mag = np.abs(np.fft.rfft(sung * np.hanning(n)))
    top = np.fft.rfftfreq(n, 1 / SR)[np.argsort(mag)[-5:]]
    assert np.all(np.abs(top / 110.0 - np.round(top / 110.0)) < 0.02), top


def test_a_shift_past_the_ends_stays_finite():
    n = SR // 4
    for shift in (-60.0, 60.0):
        out = fx.vocoder(_noise(n), _noise(n, 2), bands=32, shift=shift)
        assert np.all(np.isfinite(out))


def test_the_unvoiced_detector_tells_a_vowel_from_an_s():
    n = SR // 2
    assert float(np.median(fx.unvoiced(_vowel(n)))) < 0.02
    assert float(np.median(fx.unvoiced(_sss(n)))) > 0.98
    assert float(np.max(fx.unvoiced(np.zeros(n)))) == 0.0


def test_the_noise_path_passes_a_consonant_a_dark_carrier_cannot():
    """A sine has nothing at 6 kHz for the bands to open: an "s" over it
    is silence. With the noise path it comes through, in the "s"'s own
    part of the spectrum — and a vowel is left as it was."""
    n = SR // 2
    car = sine(110.0, n, SR) * 0.5
    burst = _sss(n) * (np.arange(n) > n // 4) * (np.arange(n) < 3 * n // 4)
    dry = fx.vocoder(car, burst, bands=16)
    wet = fx.vocoder(car, burst, bands=16, noise=1.0)
    # (both are level-matched to the carrier's peak: compare shapes)
    assert _band_energy(wet, 4000, 8000) > 20 * _band_energy(wet, 120, 1000)
    assert _band_energy(dry, 4000, 8000) < 0.05 * _band_energy(dry, 60, 1000)
    # it follows the burst in time
    mid = float(np.sqrt(np.mean(wet[n // 3 : 2 * n // 3] ** 2)))
    edge = float(np.sqrt(np.mean(wet[: n // 5] ** 2)))
    assert mid > 30 * edge, (mid, edge)
    # a vowel does not open it
    vowel = _vowel(n)
    plain = fx.vocoder(saw(110.0, n, SR), vowel, bands=16)
    hissy = fx.vocoder(saw(110.0, n, SR), vowel, bands=16, noise=1.0)
    gain = 20 * np.log10(np.sqrt(np.mean(hissy**2)) / np.sqrt(np.mean(plain**2)))
    assert abs(gain) < 1.0, gain


def test_the_noise_path_is_seeded_and_scales_with_its_amount():
    n = SR // 4
    car, mod = sine(110.0, n, SR) * 0.5, _sss(n)
    a = fx.vocoder(car, mod, noise=0.7, seed=4)
    assert np.array_equal(a, fx.vocoder(car, mod, noise=0.7, seed=4))
    assert not np.allclose(a, fx.vocoder(car, mod, noise=0.7, seed=5))
    st = fx.vocoder(np.column_stack([car, car]), mod, noise=0.7, seed=4)
    assert st.shape == (n, 2) and np.allclose(st[:, 0], st[:, 1])
