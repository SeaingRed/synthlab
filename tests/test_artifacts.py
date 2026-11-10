"""The artifact contract, held down by tests.

Each test here is one measured defect: a click, a
rumble, a mono-collapse, a gain that ran away. They are grouped in one file
because they share a diagnosis — something in the signal path was producing
audio nobody asked for — and because the measurements are the point. Every
threshold below was chosen from a reading taken against the broken code, so
a regression moves the number, not just the verdict.
"""

from __future__ import annotations

import numpy as np
import pytest

from synthlab import effects as fx
from synthlab.core import SR, adsr, formant, lfo, osc
from synthlab.synth import PRESETS, Synth


def _mono(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=1) if x.ndim == 2 else x


def sub_energy_pct(x: np.ndarray, hi: float = 20.0) -> float:
    """Share of the signal's energy below `hi` Hz — inaudible weight that
    still costs headroom."""
    m = _mono(x)
    spec = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    freqs = np.fft.rfftfreq(len(m), 1 / SR)
    low = freqs < hi
    return 100 * float(
        np.sqrt((spec[low] ** 2).sum()) / max(np.sqrt((spec**2).sum()), 1e-12)
    )


def inharmonic_pct(y: np.ndarray, f0: float) -> float:
    """Energy that is not on a harmonic of f0. `f0` must divide the buffer
    into whole cycles, so the transform needs no window and leaks nothing —
    what is left off the harmonic series is aliasing, not measurement."""
    spec = np.abs(np.fft.rfft(y))
    freqs = np.fft.rfftfreq(len(y), 1 / SR)
    on_harmonic = np.zeros_like(freqs, dtype=bool)
    for k in range(1, int(SR / 2 / f0) + 1):
        on_harmonic |= np.isclose(freqs, k * f0)
    return 100 * float(
        np.sqrt((spec[~on_harmonic] ** 2).sum()) / max(np.sqrt((spec**2).sum()), 1e-12)
    )


# ---------------------------------------------------------------- modulation


def test_noise_lfo_holds_its_value_at_the_lfo_rate():
    """`lfo(rate, n, "noise")` ignored `rate` and returned full-band audio
    noise: asking for 2 Hz gave 22,092 zero crossings a second, which sprays
    broadband hiss into whatever it modulates. A noise LFO is a sample-and-
    hold — it steps at the rate it was given."""
    rate = 4.0
    w = lfo(rate, SR, "noise")
    crossings = int(np.sum(np.diff(np.sign(w)) != 0))
    assert crossings <= 2 * rate + 2, f"{crossings} crossings for a {rate} Hz LFO"
    assert np.max(np.abs(w)) > 0.2, "a noise LFO still has to move"


def test_noise_lfo_is_reproducible():
    """It seeded from entropy, so a patch using it rendered differently every
    time — which quietly invalidates every content-addressed cache in the
    studio and makes the shape useless as a breeding gene."""
    assert np.array_equal(lfo(3.0, 4000, "noise"), lfo(3.0, 4000, "noise"))


# ----------------------------------------------------------------- envelopes


def test_adsr_lands_on_silence_when_the_gate_fills_the_buffer():
    """With `gate >= duration` the release branch never ran and the envelope
    ended at the sustain level — a step straight to zero, which is a click."""
    env = adsr(SR, attack=0.01, decay=0.1, sustain=0.7, release=0.2, gate=2.0)
    assert env[-1] == pytest.approx(0.0, abs=1e-3), f"ends at {env[-1]:.3f}"
    assert env.max() > 0.5, "the note still has to sound"


def test_a_gated_release_lasts_the_release_time_not_the_buffer():
    """With a gate the release ramp spanned whatever was left of the buffer,
    so the buffer's length (`note_span`: 2.3 s for this 2 s note) set how
    long a note rang: a 0.02 s release was still at 68 % / 45 % / 23 % of
    its gate level 50 / 150 / 300 ms after key-up."""
    gate, rel = 1.8, 0.02
    env = adsr(int(2.3 * SR), 0.01, 0.1, 0.7, rel, gate=gate)
    up = round(gate * SR)
    assert env[up - 1] == pytest.approx(0.7)
    for ms in (50, 150, 300):
        at = env[up + int(ms / 1000 * SR)]
        assert at == 0.0, f"{at / 0.7:.0%} of the gate level {ms} ms after key-up"
    # the release itself: `release` seconds of the curve, landing on zero
    tail = env[up : up + round(rel * SR)]
    assert np.all(np.diff(tail) < 0) and tail[0] == pytest.approx(0.7)
    assert tail[-1] == 0.0 and not np.any(env[up + len(tail) :])
    # curve 2: 1 - sqrt(x), so a quarter of the way through it is at half
    assert tail[len(tail) // 4] == pytest.approx(0.35, abs=0.01)


def test_a_release_longer_than_the_buffer_still_lands_on_silence():
    """The buffer ends before the release would (`note_span` caps a tail at
    MAX_NOTE_TAIL): the release is fitted into what is left, as every
    gated release used to be, so the note never stops on a step."""
    n, up = int(1.0 * SR), round(0.5 * SR)
    env = adsr(n, 0.01, 0.1, 0.7, release=3.0, gate=0.5)
    assert env[-1] == 0.0 and env[-2] > 0.0
    assert np.all(np.diff(env[up:]) < 0)
    # exactly the release that fits: the same curve at the buffer's length
    np.testing.assert_array_equal(env, adsr(n, 0.01, 0.1, 0.7, release=0.5, gate=0.5))


def test_a_gateless_envelope_is_unchanged_by_the_release_rule():
    """Without a gate the hold is whatever the release leaves, so the
    release already ran for its own time to the last sample."""
    env = adsr(SR, 0.01, 0.1, 0.7, release=0.2)
    rel = round(0.2 * SR)
    assert np.all(env[: SR - rel][-100:] == 0.7) and env[-1] == 0.0
    assert np.all(np.diff(env[SR - rel :]) < 0)


# --------------------------------------------------------------- oscillators


def test_a_pwm_voice_leaves_no_subsonic_rumble():
    """Pulse-width modulation moves a square's duty cycle, and a square of
    duty d carries a DC term of 2d-1. Nothing in the path blocked it, so
    `stab_hoover` put 32.7% of its energy below 20 Hz: inaudible, and paid
    for out of the headroom the loop normalizer then takes off the music."""
    voice = Synth(PRESETS["stab_hoover"]).note("C3", 1.0)
    assert sub_energy_pct(voice) < 5.0, f"{sub_energy_pct(voice):.1f}% below 20 Hz"


def test_a_dc_free_patch_is_not_thinned_by_the_dc_blocker():
    """The blocker must not eat the bottom of a bass that legitimately lives
    down there — 40 Hz is a real note, not rumble."""
    quiet = Synth(PRESETS["bass_reese"].tweak(pwm_depth=0.0)).note("E1", 1.0)
    band = _mono(quiet)
    spec = np.abs(np.fft.rfft(band * np.hanning(len(band))))
    freqs = np.fft.rfftfreq(len(band), 1 / SR)
    keep = (freqs > 35) & (freqs < 90)
    share = float(np.sqrt((spec[keep] ** 2).sum()) / np.sqrt((spec**2).sum()))
    assert share > 0.2, f"the fundamental was filtered away ({share:.2f})"


def test_triangle_is_band_limited_like_the_other_shapes():
    """saw and square get PolyBLEP; triangle was the raw `4|t-0.5|-1`, whose
    corners alias. Measured on an exact bin so nothing leaks."""
    f0 = 3000.0
    tri = inharmonic_pct(osc("triangle", f0, SR), f0)
    saw = inharmonic_pct(osc("saw", f0, SR), f0)
    assert tri <= max(saw, 0.5), f"triangle {tri:.2f}% vs saw {saw:.2f}%"


# ------------------------------------------------------------------- effects


def test_pan_keeps_a_stereo_signal_stereo():
    """`fx.pan` mono-summed its input first, so panning a track threw away
    unison spread, chorus and reverb width. Track.render calls it on every
    track with a pan."""
    st = np.column_stack([np.ones(256), -np.ones(256)])  # pure side content
    out = fx.pan(st, 0.3)
    side = float(np.abs(out[:, 0] - out[:, 1]).mean())
    assert side > 0.5, f"stereo collapsed to mono (side energy {side:.2f})"


def test_shimmer_keeps_its_tail_alive_to_the_end_of_the_buffer():
    """The octave-up read index ran past the source and was clipped to the
    last sample, so the back half of every shimmer fed a held constant — a
    DC step — into the tank."""
    n = SR
    src = np.column_stack([osc("saw", 220.0, n)] * 2)
    wet = fx._shimmer(src, decay=1.0, mix=1.0, shift=12.0)
    first = float(np.sqrt((wet[: n // 2] ** 2).mean()))
    second = float(np.sqrt((wet[n // 2 :] ** 2).mean()))
    assert second > first * 0.75, f"tail decayed away: {first:.4f} -> {second:.4f}"


def test_limiter_holds_the_ceiling_without_hard_clipping():
    """Its "lookahead" rolled the block maxima backwards, so the gain arrived
    after the peak and a final np.clip did the actual limiting — distortion
    sold as a limiter."""
    n = SR // 2
    quiet = 0.2 * osc("sine", 220.0, n)
    spike = quiet.copy()
    spike[n // 2 : n // 2 + 40] += 3.0  # a transient the limiter must catch
    out = fx.limit(ceiling=0.9)(np.column_stack([spike, spike]))
    assert np.max(np.abs(out)) <= 0.9 + 1e-6
    clipped = int(np.sum(np.abs(out) >= 0.9 - 1e-9))
    assert clipped < 200, f"{clipped} samples sitting on the ceiling = clipping"


def test_limiter_does_not_duck_a_one_shots_ending_for_its_own_start():
    """The lookahead wraps: the last block's "block ahead" is the first.
    Right for a loop, but a one-shot with a loud start had its quiet final
    5 ms turned down to match (gain 0.24 on this signal)."""
    y = np.full(SR, 0.5)
    y[:100] = 4.0
    st = np.column_stack([y, y])
    one = fx.limit(loop=False)(st)
    assert np.max(np.abs(one)) <= 0.97 + 1e-9
    assert np.min(one[-400:, 0]) == pytest.approx(0.5, abs=1e-3)
    # a loop does meet its start again, and is limited as it always was
    looped = fx.limit()(st)
    assert np.min(looped[-200:, 0]) < 0.13
    np.testing.assert_array_equal(looped[:-400], one[:-400])
    np.testing.assert_array_equal(looped, fx.limit(loop=True)(st))


def test_formant_does_not_amplify_what_it_cannot_pass():
    """The wet path is level-matched to the dry by RMS, with no ceiling: feed
    it a tone below every formant and the makeup gain runs away."""
    tone = np.sin(2 * np.pi * 45 * np.arange(SR) / SR)
    out = formant(tone, vowel=0.5, amount=1.0)
    assert np.max(np.abs(out)) <= 1.05, f"peak {np.max(np.abs(out)):.2f}"


@pytest.mark.parametrize("stereo", [False, True])
def test_a_sidechain_duck_never_steps(stereo):
    """The duck used to fall from wherever the gain was to 1 - depth in one
    sample. Under a kick on a synth bus the kick hides it; on anything
    still sounding at the trigger (a pump on the drum bus, a long 36 Hz
    kick tail) it is a step in the waveform at every beat: a click. The
    gain now falls over the `SIDECHAIN_ATTACK` before the trigger, so the
    bottom is still on the beat."""
    x = np.ones(SR) if not stereo else np.ones((SR, 2))  # the gain itself
    g = fx.sidechain([0.25, 0.5, 1.0], depth=0.8, release=0.3)(x)
    g = g if g.ndim == 1 else g[:, 0]
    assert np.max(np.abs(np.diff(g))) < 0.01, "the gain jumps: a click"
    # full depth, on the trigger
    assert g[int(0.25 * SR)] == pytest.approx(0.2, abs=1e-9)
    assert int(np.argmin(g[: int(0.4 * SR)])) == int(0.25 * SR)
    # a trigger on the buffer's end brings the gain down into it: a loop's
    # last samples meet its first, which a trigger at 0 starts ducked
    assert g[-1] < 0.21
    assert fx.sidechain([0.0], depth=0.8, release=0.3)(np.ones(SR))[0] < 0.21
