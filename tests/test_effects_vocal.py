"""The live vocal chain's units: auto-tune, harmonizer, vocal compressor,
de-esser, noise gate. Each is causal (the causal contract: a change at
sample k moves nothing before k), bounded and deterministic, and does the
one thing its name says — measured on band-limited tones, glides and noise,
not by ear. The numbers the docs quote (`docs/live-voice.md`, "The live
vocal chain's units") are the ones asserted here."""

import numpy as np
import pytest

from synthlab import effects as fx
from synthlab.core import SR, biquad
from synthlab.studio.fxrack import FX_UNITS, build_chain, resolve_unit, tune_key
from synthlab.studio.state import Arrangement

ARR = Arrangement(bpm=120.0, root="C3", scale_name="major")
KINDS = ("autotune", "harmony", "vox_comp", "deess", "vgate")
HOP = max(8, int(fx.PITCH_GRAIN * SR) // 2)


def saw(freq, n: int, amp: float = 0.3, top: float = 8000.0) -> np.ndarray:
    """A band-limited saw (additive, under `top`), its pitch per sample."""
    f = np.broadcast_to(np.asarray(freq, dtype=float), (n,))
    ph = 2 * np.pi * np.cumsum(f) / SR
    out = np.zeros(n)
    for k in range(1, int(top // f.min()) + 1):
        out += np.where(k * f < top, np.sin(k * ph) / k, 0.0)
    return amp * out / 1.7


def _unit(kind: str, **params):
    (f,) = build_chain([{"kind": kind, "params": params}], ARR)
    return f


def _f0(x: np.ndarray) -> float:
    """A steady stretch's pitch: the strongest peak 60-1200 Hz, zero padded."""
    nfft = 1 << 20
    p = np.abs(np.fft.rfft(x * np.hanning(len(x)), nfft))
    f = np.fft.rfftfreq(nfft, 1 / SR)
    idx = np.flatnonzero((f > 60) & (f < 1200))
    return float(f[idx[np.argmax(p[idx])]])


def _cents_off_a_note(hz: float) -> float:
    m = 69 + 12 * np.log2(hz / 440.0)
    return float((m - round(m)) * 100)


def _voice(n: int = SR) -> np.ndarray:
    t = np.arange(n) / SR
    x = saw(150 * 2 ** (0.3 * np.sin(2 * np.pi * 0.7 * t) / 12), n)
    x *= (t * 3) % 1.0 < 0.7
    x += 0.01 * np.random.default_rng(3).standard_normal(n)
    return np.column_stack([x, 0.8 * x])


@pytest.mark.parametrize("kind", KINDS)
def test_each_unit_is_finite_bounded_deterministic_and_causal(kind):
    x = _voice()
    f = _unit(kind)
    y = f(x)
    assert y.shape == x.shape and np.all(np.isfinite(y))
    assert np.max(np.abs(y)) < 3.0 * np.max(np.abs(x)), kind
    np.testing.assert_array_equal(y, f(x))
    assert f(x[:, 0]).shape == (len(x),)
    later = x.copy()
    k = len(x) // 2
    later[k:] = np.random.default_rng(9).standard_normal((len(x) - k, 2)) * 0.3
    np.testing.assert_allclose(f(later)[:k], y[:k], atol=1e-12, err_msg=kind)


@pytest.mark.parametrize("cents", [40, -35, 20, -45])
@pytest.mark.parametrize("base", [110.0, 196.0, 440.0])
def test_autotune_at_speed_zero_puts_a_detuned_held_note_on_its_note(base, cents):
    """A held note sung off its note comes out on it: measured within 0.4
    cents (110-440 Hz, 20-45 cents off), asserted within 2."""
    x = saw(base * 2 ** (cents / 1200), 2 * SR)
    y = _unit("autotune", scale=1.0, speed=0.0)(x)  # chromatic
    got = _f0(y[SR // 2 :])
    assert abs(_cents_off_a_note(got)) < 2.0, (base, cents, got)
    assert abs(1200 * np.log2(got / base)) < 2.0


def test_autotune_snaps_a_glide_onto_the_scale():
    """A glide 400 -> 500 Hz through C major comes out as G4, A4 and B4 —
    never G#4 or A#4 — each grain within 6 cents of its note in the
    median (measured 4.1: the glide moves on through each 20 ms grain)."""
    n = 2 * SR
    x = saw(np.geomspace(400, 500, n), n)
    y = _unit("autotune", speed=0.0)(x)  # the session's: C major
    f0 = fx.pitch_track(y, fx.grain_count(n, HOP), HOP)
    m = 69 + 12 * np.log2(f0[f0 > 0] / 440)
    assert set(np.round(m).astype(int)) == {67, 69, 71}
    assert np.median(np.abs(m - np.round(m))) * 100 < 6.0


def test_autotune_speed_glides_and_humanize_leaves_the_wander():
    x = saw(440 * 2 ** (40 / 1200), 2 * SR)
    hard = _f0(_unit("autotune", scale=1.0, speed=0.0)(x)[SR // 2 :])
    half = _f0(_unit("autotune", scale=1.0, speed=0.0, humanize=0.5)(x)[SR // 2 :])
    assert abs(_cents_off_a_note(hard)) < 2.0
    assert 15 < _cents_off_a_note(half) < 25  # half the 40 cents left in
    # at speed 1 the correction is still on its way 100 ms in
    f0 = fx.pitch_track(x, fx.grain_count(2 * SR, HOP), HOP)
    slow, _ = fx.autotune_ratios(f0, HOP, 0, fx.CHROMATIC, speed=1.0)
    fast, _ = fx.autotune_ratios(f0, HOP, 0, fx.CHROMATIC, speed=0.0)
    i = 2 + int(0.1 * SR / HOP)
    assert abs(1200 * np.log2(fast[i])) == pytest.approx(40, abs=1)
    assert 5 < abs(1200 * np.log2(slow[i])) < 30


def test_autotune_passes_unvoiced_input_through_untouched():
    """Noise has no period: every grain unvoiced, so the output is the
    input, sample for sample (a run of unvoiced grains is not shifted or
    spliced)."""
    x = 0.2 * np.random.default_rng(1).standard_normal((SR, 2))
    np.testing.assert_allclose(_unit("autotune", speed=0.0)(x), x, atol=1e-12)
    np.testing.assert_array_equal(_unit("autotune")(np.zeros((SR, 2))), 0.0)


def test_the_tracker_reads_a_held_note_and_calls_noise_unvoiced():
    n = SR
    for hz in (82.0, 147.0, 330.0, 880.0):
        f0 = fx.pitch_track(saw(hz, n), fx.grain_count(n, HOP), HOP)
        assert np.all(np.abs(1200 * np.log2(f0[5:] / hz)) < 1.0), hz
    noise = np.random.default_rng(2).standard_normal(n) * 0.3
    assert np.mean(fx.pitch_track(noise, fx.grain_count(n, HOP), HOP) > 0) < 0.05


def test_the_harmonizer_adds_a_third_in_key_over_the_voice():
    """C major: a sung E4 gets G4 (a minor third), a sung C4 gets E4 (a
    major third) — the key's thirds; a fifth up of A4 is E5."""
    for sung, want, voice in (
        (329.63, 392.0, 0),
        (261.63, 329.63, 0),
        (440.0, 659.26, 1),
    ):
        x = saw(sung, 2 * SR)
        y = _unit("harmony", first=float(voice), first_level=1.0, first_pan=0.0)(x)
        extra = y[SR // 2 :] - x[SR // 2 :]  # the harmony voice alone
        assert abs(1200 * np.log2(_f0(extra) / want)) < 3.0, (sung, want)


def test_harmony_steps_scale_with_the_scale():
    assert fx.harmony_steps(0, 7) == 2 and fx.harmony_steps(1, 7) == 4
    assert fx.harmony_steps(2, 5) == 5 and fx.harmony_steps(5, 12) == -12
    # before any note: the interval from the key's root (C major's third: 4)
    assert fx.harmony_semis(np.zeros(3), 0, (0, 2, 4, 5, 7, 9, 11), 0)[0] == 4


def test_the_vocal_compressor_evens_the_level():
    """A phrase 20 dB louder than the last comes out 12.9 dB louder
    (ratio 4, threshold -30 dB: measured; the follower reads about the
    mean of |x|, some 6 dB under a saw's peak, so less is over)."""
    n = SR
    x = saw(220.0, 2 * n, amp=1.0)
    x[:n] *= 0.02
    x[n:] *= 0.2
    y = _unit("vox_comp", threshold=-30.0, ratio=4.0, makeup=0.0)(x)
    rms = lambda a: np.sqrt(np.mean(a**2))  # noqa: E731
    before = 20 * np.log10(rms(x[n + SR // 4 :]) / rms(x[SR // 4 : n]))
    after = 20 * np.log10(rms(y[n + SR // 4 :]) / rms(y[SR // 4 : n]))
    assert before == pytest.approx(20, abs=0.1) and 11.0 < after < 14.0
    # mix 0 is the dry signal
    np.testing.assert_array_equal(_unit("vox_comp", mix=0.0)(x), x)


def test_the_deesser_turns_down_the_esses_and_leaves_the_vowel():
    """An "s" (hiss over 9 kHz, -17 dB RMS) comes out 6.8 dB down at depth
    12 (measured): the split is x - HP(x), which sums back to the input
    exactly but lets some of the band through the complement's phase. A
    vowel under the band is left (its top under 3 kHz: within 2%)."""
    n = SR
    vowel = saw(200.0, n, amp=0.3, top=3000.0)
    hiss = np.random.default_rng(4).standard_normal(n) * 0.2
    ess = biquad(biquad(hiss, "highpass", 9000.0, 0.707), "highpass", 9000.0, 0.707)
    f = _unit("deess", threshold=-40.0, depth=12.0)
    assert np.max(np.abs(f(vowel) - vowel)) < 0.02 * np.max(np.abs(vowel))
    out = f(ess)
    rms = lambda a: np.sqrt(np.mean(a[SR // 10 :] ** 2))  # noqa: E731
    assert -9.0 < 20 * np.log10(rms(out) / rms(ess)) < -5.0
    # at no reduction (a threshold over everything) it is the input
    np.testing.assert_allclose(_unit("deess", threshold=0.0)(vowel), vowel, atol=1e-12)


def test_the_gate_closes_between_phrases_and_opens_on_the_words():
    n = SR
    words = saw(180.0, n, amp=0.3)
    room = np.random.default_rng(5).standard_normal(n) * 0.001  # -60 dB spill
    x = room.copy()
    x[n // 2 :] += words[n // 2 :]
    y = _unit("vgate", threshold=-40.0, ratio=10.0, depth=40.0)(x)
    gap = np.sqrt(np.mean(y[SR // 10 : n // 2] ** 2)) / np.sqrt(
        np.mean(x[SR // 10 : n // 2] ** 2)
    )
    assert 20 * np.log10(gap) < -30
    sung = y[n // 2 + SR // 10 :] / x[n // 2 + SR // 10 :]
    assert np.allclose(sung, 1.0, atol=1e-9)


def test_tune_key_resolves_the_session_and_the_named_choices():
    p = {"key": 0.0, "scale": 0.0}
    assert tune_key(p, ARR) == (0, [0, 2, 4, 5, 7, 9, 11])
    a_minor = Arrangement(root="A1", scale_name="minor")
    assert tune_key(p, a_minor) == (9, [0, 2, 3, 5, 7, 8, 10])
    assert tune_key({"key": 3.0, "scale": 1.0}, ARR) == (2, list(range(12)))
    name, kw = resolve_unit(
        {
            "kind": "harmony",
            "params": dict.fromkeys(FX_UNITS["harmony"]["params"], 0.0),
        },
        ARR,
    )
    assert name == "harmony" and kw["key"] == 0 and kw["first"] == 0
