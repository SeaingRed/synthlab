"""The vowel filter: three parallel formant resonators over a five-vowel
table. Checked by where the spectral peaks land, because "the output
changed" is true of any filter — a vowel filter has to put energy at the
formant frequencies that make that vowel *that* vowel.
"""

import numpy as np
import pytest

from synthlab.core import SR, VOWELS, formant, saw


def _source(seconds=0.5, freq=110.0):
    """A saw is the right excitation: dense harmonics for formants to shape."""
    return 0.5 * saw(freq, int(SR * seconds), SR)


def _peaks(x, top=6):
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    keep = freqs < 4000
    mag, freqs = mag[keep], freqs[keep]
    idx = np.argsort(mag)[-top:]
    return sorted(float(freqs[i]) for i in idx)


def _energy_near(x, target, width=90.0):
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    return float(mag[np.abs(freqs - target) <= width].sum())


def test_vowel_table_is_five_vowels_of_three_formants():
    assert list(VOWELS) == ["a", "e", "i", "o", "u"]
    for name, formants in VOWELS.items():
        assert len(formants) == 3, name
        f1, f2, f3 = (f for f, _amp in formants)
        assert 200 <= f1 <= 900, (name, f1)
        assert f1 < f2 < f3, (name, f1, f2, f3)


@pytest.mark.parametrize("vowel", ["a", "e", "i", "o", "u"])
def test_each_vowel_puts_energy_at_its_own_formants(vowel):
    """The defining test: filtering to vowel X must boost X's F1 and F2
    relative to the rest of the spectrum. Relative, not absolute: the
    makeup gain is the tract's own (core.formant_makeup_table), so a 110 Hz
    saw with no harmonic on "i"'s narrow 270 Hz formant comes out quieter
    overall — what makes it "i" is where the energy sits."""
    src = _source()
    pos = list(VOWELS).index(vowel) / (len(VOWELS) - 1)
    out = formant(src, pos, amount=1.0)
    f1, f2 = VOWELS[vowel][0][0], VOWELS[vowel][1][0]
    for f in (f1, f2):
        share_out = _energy_near(out, f) / _energy_near(out, 2000, width=2000)
        share_src = _energy_near(src, f) / _energy_near(src, 2000, width=2000)
        assert share_out / max(share_src, 1e-9) > 1.5, (vowel, f, share_out, share_src)


def test_vowels_are_distinguishable_from_each_other():
    """ "a" and "i" must not render to the same spectrum, or the morph knob
    is decoration."""
    src = _source()
    a = formant(src, 0.0, amount=1.0)
    i = formant(src, 0.5, amount=1.0)  # "i" is the middle of a,e,i,o,u
    assert not np.allclose(a, i, atol=1e-3)
    # "a" has a high F1 (~730) and "i" a low one (~270): the balance flips
    a_ratio = _energy_near(a, VOWELS["a"][0][0]) / max(
        _energy_near(a, VOWELS["i"][0][0]), 1e-9
    )
    i_ratio = _energy_near(i, VOWELS["a"][0][0]) / max(
        _energy_near(i, VOWELS["i"][0][0]), 1e-9
    )
    assert a_ratio > i_ratio * 2, (a_ratio, i_ratio)


def test_amount_zero_is_bypass():
    src = _source()
    np.testing.assert_allclose(formant(src, 0.4, amount=0.0), src, atol=1e-9)


def test_vowel_position_can_be_a_per_sample_array():
    """The whole reason this is a Patch field and not an FX unit: a drawn
    curve sweeps the vowel across the note."""
    src = _source()
    sweep = np.linspace(0.0, 1.0, len(src))
    out = formant(src, sweep, amount=1.0)
    assert out.shape == src.shape and np.all(np.isfinite(out))
    # first half should read as "a"-ish, last half as "u"-ish
    half = len(out) // 2
    early = _energy_near(out[:half], VOWELS["a"][0][0])
    late = _energy_near(out[half:], VOWELS["a"][0][0])
    assert early > late, (early, late)


def test_amount_can_be_a_per_sample_array():
    src = _source()
    ramp = np.linspace(0.0, 1.0, len(src))
    out = formant(src, 0.0, amount=ramp)
    assert out.shape == src.shape and np.all(np.isfinite(out))
    np.testing.assert_allclose(out[0], src[0], atol=1e-6)  # starts dry


def test_size_scales_the_formants():
    """Vocal-tract size: doubling it moves the peaks up an octave."""
    src = _source()
    normal = formant(src, 0.0, amount=1.0, size=1.0)
    big = formant(src, 0.0, amount=1.0, size=2.0)
    f1 = VOWELS["a"][0][0]
    assert _energy_near(big, f1 * 2) / max(_energy_near(normal, f1 * 2), 1e-9) > 1.5


def test_stereo_in_stereo_out():
    src = _source()
    st = np.column_stack([src, src])
    out = formant(st, 0.3, amount=1.0)
    assert out.shape == st.shape and np.all(np.isfinite(out))


# ------------------------------------------------- integration with the voice


def test_patch_defaults_leave_the_voice_untouched():
    """formant_amount defaults to 0, so every patch written before the vowel
    filter existed must render bit-identically."""
    from synthlab import Patch, Synth

    p = Patch()
    assert p.formant_amount == 0.0
    plain = Synth(Patch()).note("A2", 0.4)
    explicit_off = Synth(Patch(formant_amount=0.0, formant_vowel=0.7)).note("A2", 0.4)
    np.testing.assert_allclose(plain, explicit_off, atol=0.0)


def test_old_preset_json_without_formant_fields_still_loads(tmp_path):
    """Defaults must keep pre-existing preset JSON loading — the fields are
    simply absent from every file saved before today."""
    import json

    from synthlab import Patch

    path = tmp_path / "old.json"
    path.write_text(json.dumps({"name": "old", "cutoff": 900.0}), encoding="utf-8")
    p = Patch.load(str(path))
    assert p.cutoff == 900.0
    assert (p.formant_amount, p.formant_vowel, p.formant_size) == (0.0, 0.0, 1.0)


def test_vowel_is_audible_through_the_whole_voice():
    """End to end, not just the DSP function: a vowelled patch must differ
    from the same patch dry, and two vowels must differ from each other."""
    from synthlab import Patch, Synth

    base = Patch(filter_type="none")
    dry = Synth(base).note("A2", 0.5)
    ah = Synth(base.tweak(formant_amount=1.0, formant_vowel=0.0)).note("A2", 0.5)
    oo = Synth(base.tweak(formant_amount=1.0, formant_vowel=1.0)).note("A2", 0.5)
    assert not np.allclose(dry, ah, atol=1e-3)
    assert not np.allclose(ah, oo, atol=1e-3)
    assert np.all(np.isfinite(ah)) and np.all(np.isfinite(oo))
