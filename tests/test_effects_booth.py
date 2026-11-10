"""The studio tricks (the vocal booth's units): ADT double, rotary, tape
echo, telephone, pitch shift, plate. Each is causal (an input's change at
sample k moves nothing before k), bounded, deterministic, shape-keeping,
an exact bypass at mix 0, and does the one thing its name says — measured
on an impulse or a sine, not by ear."""

import numpy as np
import pytest

from synthlab import effects as fx
from synthlab.core import SR
from synthlab.studio.fxrack import FX_UNITS, build_chain, resolve_unit
from synthlab.studio.state import Arrangement

ARR = Arrangement(bpm=120.0)
KINDS = ("doubler", "rotary", "tape_echo", "telephone", "pitch", "plate")


def _voiceish(n: int = SR, seed: int = 0) -> np.ndarray:
    """A buzzy, vowel-ish stereo signal with syllables: a saw at 160 Hz,
    gated in 120 ms bursts, plus a little noise."""
    t = np.arange(n) / SR
    saw = 2 * ((160 * t) % 1.0) - 1
    gate = ((t * 4) % 1.0 < 0.5).astype(float)
    rng = np.random.default_rng(seed)
    x = 0.4 * saw * gate + 0.02 * rng.standard_normal(n)
    return np.column_stack([x, 0.9 * x])


def _unit(kind: str, **params):
    (f,) = build_chain([{"kind": kind, "params": params}], ARR)
    return f


def _centroid(x: np.ndarray) -> float:
    p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return float(np.sum(f * p) / (np.sum(p) + 1e-30))


@pytest.mark.parametrize("kind", KINDS)
def test_each_unit_is_finite_bounded_deterministic_and_keeps_its_shape(kind):
    x = _voiceish()
    f = _unit(kind)
    y = f(x)
    assert y.shape == x.shape
    assert np.all(np.isfinite(y))
    # no unit at its defaults makes a voice much louder than it came in
    assert np.max(np.abs(y)) < 2.5 * np.max(np.abs(x)), kind
    np.testing.assert_array_equal(y, f(x))  # no random stream: the same samples
    mono = f(x[:, 0])
    assert mono.shape == (len(x),) and np.all(np.isfinite(mono))


@pytest.mark.parametrize("kind", KINDS)
def test_each_unit_is_causal(kind):
    """The causal contract (engine.md): what comes later never reaches
    earlier output. The second half changed, the first half is the same."""
    x = _voiceish()
    later = x.copy()
    k = len(x) // 2
    later[k:] = np.random.default_rng(9).standard_normal((len(x) - k, 2)) * 0.3
    f = _unit(kind, mix=0.8)
    np.testing.assert_allclose(f(x)[:k], f(later)[:k], atol=1e-12, err_msg=kind)


@pytest.mark.parametrize("kind", KINDS)
def test_mix_zero_is_the_dry_signal_and_the_blend_is_linear_in_mix(kind):
    """Every one ends in dry * (1 - mix) + wet * mix with a wet that does
    not depend on the mix (what makes `curves.FX_LANES`' mix lanes exact)."""
    x = _voiceish(SR // 2)
    np.testing.assert_array_equal(_unit(kind, mix=0.0)(x), x)
    wet = _unit(kind, mix=1.0)(x)
    half = _unit(kind, mix=0.5)(x)
    np.testing.assert_allclose(half, 0.5 * x + 0.5 * wet, atol=1e-12)


def test_the_doubler_puts_a_second_copy_time_late_and_pans_them_apart():
    x = np.zeros((SR // 2, 2))
    x[100] = 1.0
    y = fx._doubler(x, time=0.03, wobble=0.0, spread=1.0, mix=1.0)
    d = int(round(0.03 * SR))
    # spread 1: the original hard left, the copy hard right, each at the
    # pan law's sqrt 2 x 1/sqrt 2 = 1
    assert y[100, 0] == pytest.approx(1.0)
    assert abs(y[100, 1]) < 1e-12
    assert y[100 + d, 1] == pytest.approx(1.0)
    assert abs(y[100 + d, 0]) < 1e-12
    # wobble moves the copy (and so bends its pitch a little): never further
    # than ADT_WOBBLE either way
    w = fx._doubler(x, time=0.03, wobble=1.0, spread=1.0, mix=1.0)
    at = 100 + np.flatnonzero(np.abs(w[100:, 1]) > 1e-6)
    assert np.all(np.abs(at - (100 + d)) <= fx.ADT_WOBBLE * SR + 1)


def test_the_rotary_swirls_faster_at_tremolo_and_ramps_between():
    """A steady 2 kHz tone (the horn's) through the cabinet: its level swings
    at the horn's rate — about 0.8 Hz at chorale, 6.8 at tremolo."""
    n = 4 * SR
    t = np.arange(n) / SR
    tone = np.column_stack([0.5 * np.sin(2 * np.pi * 2000 * t)] * 2)

    def swing_hz(y):
        env = np.abs(y[:, 0]).reshape(-1, 441).max(axis=1)  # 100 per second
        env = env - env.mean()
        spec = np.abs(np.fft.rfft(env * np.hanning(len(env))))
        f = np.fft.rfftfreq(len(env), 1 / 100)
        return f[1 + np.argmax(spec[1:])]

    slow = fx._rotary(tone, speed=0.0, drive=0.0, mix=1.0)
    fast = fx._rotary(tone, speed=1.0, drive=0.0, mix=1.0)
    assert swing_hz(slow) == pytest.approx(fx.ROTARY_HORN[0], abs=0.3)
    assert swing_hz(fast) == pytest.approx(fx.ROTARY_HORN[1], abs=0.3)
    # the switch: a speed lane stepping to 1 at 1 s; the horn ramps there
    # with its time constant, the drum much more slowly
    step = np.where(t < 1.0, 0.0, 1.0)
    horn = fx.rotor_rate(step, n, *fx.ROTARY_HORN[:3])
    drum = fx.rotor_rate(step, n, *fx.ROTARY_DRUM[:3])
    one_tau = SR + int(fx.ROTARY_HORN[2] * SR)
    lo, hi = fx.ROTARY_HORN[:2]
    assert horn[SR - 1] == pytest.approx(lo)
    assert horn[one_tau] == pytest.approx(lo + (hi - lo) * (1 - np.exp(-1)), rel=1e-3)
    dlo, dhi = fx.ROTARY_DRUM[:2]
    assert drum[one_tau] < dlo + 0.4 * (dhi - dlo)
    # a lane standing still is the scalar unit, sample for sample
    np.testing.assert_array_equal(
        fx._rotary(tone[:SR], speed=np.full(SR, 0.4)), fx._rotary(tone[:SR], speed=0.4)
    )


def test_the_tape_echo_repeats_on_time_each_repeat_darker_and_quieter():
    n = 2 * SR
    x = np.zeros((n, 2))
    x[:64, :] = np.random.default_rng(1).standard_normal((64, 2)) * 0.5
    y = fx._tape_echo(x, time=0.25, feedback=0.6, wow=0.0, tone=0.4, drive=0.3, mix=1.0)
    d = int(round(0.25 * SR))
    win = 2048
    echoes = [y[k * d : k * d + win, 0] for k in (1, 2, 3)]
    levels = [float(np.sqrt(np.mean(e**2))) for e in echoes]
    cents = [_centroid(e) for e in echoes]
    assert levels[0] > levels[1] > levels[2] > 0
    assert cents[0] > cents[1] > cents[2]
    # nothing between the burst and the first echo
    assert np.max(np.abs(y[200 : d - 10, 0])) < 1e-9
    # a feedback lane standing still is the scalar unit, sample for sample
    np.testing.assert_array_equal(
        fx._tape_echo(x, feedback=np.full(n, 0.5)), fx._tape_echo(x, feedback=0.5)
    )


def test_the_tape_echo_syncs_like_the_delay():
    name, kw = resolve_unit(
        {"kind": "tape_echo", "params": {**_defaults("tape_echo"), "sync": 3.0}}, ARR
    )
    assert name == "tape_echo" and kw["time"] == pytest.approx(0.75 * 60 / 120)
    assert "sync" not in kw


def _defaults(kind: str) -> dict:
    return {k: d for k, (_lo, _hi, d) in FX_UNITS[kind]["params"].items()}


def test_the_telephone_keeps_the_band_and_drops_the_ends():
    t = np.arange(SR) / SR

    def gain(hz):
        x = 0.3 * np.sin(2 * np.pi * hz * t)
        y = fx._telephone(x, centre=1200.0, width=2.5, drive=0.0, mix=1.0)
        return np.sqrt(np.mean(y[SR // 2 :] ** 2)) / np.sqrt(np.mean(x[SR // 2 :] ** 2))

    assert gain(1200) == pytest.approx(1.0, abs=0.02)  # 0 dB at the centre
    assert gain(80) < 0.05  # a voice's body: gone
    assert gain(9000) < 0.15  # its air: mostly gone
    assert fx.bandwidth_q(1.0) == pytest.approx(np.sqrt(2.0))


def _harmonic(f0: float, n: int) -> np.ndarray:
    """A held tone: twelve harmonics at 1/h."""
    t = np.arange(n) / SR
    return 0.3 * sum(np.sin(2 * np.pi * h * f0 * t) / h for h in range(1, 13))


def _comb(y: np.ndarray, target: float) -> tuple[float, float]:
    """(the share of `y`'s energy below 8 kHz within 6 Hz of the target's
    harmonics, the loudest line between them in dB under the loudest on)."""
    p = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
    f = np.fft.rfftfreq(len(y), 1 / SR)
    h = np.round(f / target)
    on = (np.abs(f - h * target) <= 6) & (h >= 1) & (f < 8000)
    off = (f < 8000) & ~on & (f > target / 2)
    share = p[on].sum() / p[f < 8000].sum()
    return float(share), float(10 * np.log10(p[off].max() / p[on].max()))


def test_the_pitch_shift_moves_a_held_tone_onto_its_shifted_harmonics():
    """Each grain is spliced in phase with the one it fades out of
    (`_pitch_splices`), so a held tone comes out on the target's harmonics
    and nothing between. Before the splice every grain restarted on its
    own clock and a steady tone came out as lines 1/hop = 50 Hz apart round
    the target (220 Hz up an octave: 4-5% of the energy on the harmonics,
    a line between them 10-13 dB *over* the loudest; a 165 Hz vowel 16-24%).
    Measured with the splice: >= 99.99% on, the worst line -58 dB (vowel
    99.85%, -33 dB) — `docs/sampler-and-clips.md`, "The vocal booth"."""
    x = _harmonic(220.0, 2 * SR)
    for semis in (12.0, -12.0, 7.0):
        want = 220 * 2 ** (semis / 12)
        y = fx._pitch(x, semis=semis, mix=1.0)[SR // 2 : SR // 2 + SR]
        share, between = _comb(y, want)
        assert share > 0.999, semis
        assert between < -45.0, (semis, between)
    # a sine: the original pitch is gone
    t = np.arange(SR) / SR
    sine = 0.5 * np.sin(2 * np.pi * 220 * t)
    for semis in (12.0, -12.0, 7.0):
        y = fx._pitch(sine, semis=semis, mix=1.0)[SR // 4 :]
        p = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
        f = np.fft.rfftfreq(len(y), 1 / SR)
        assert p[np.abs(f - 220 * 2 ** (semis / 12)) <= 6].sum() > 0.99 * p.sum()
        assert p[np.abs(f - 220) < 5].sum() < 0.01 * p.sum(), semis


def test_the_splice_stays_within_its_reach_and_rests_in_silence():
    """A grain starts at most PITCH_SEARCH later than its place (the extra
    latency the splice costs), and on silence exactly on it."""
    hop = int(fx.PITCH_GRAIN * SR) // 2
    x = _voiceish()[:, 0]
    x[SR // 2 :] = 0.0
    n_grains = -(-len(x) // hop) + 1
    for ratio in (0.5, 2 ** (7 / 12), 2.0, 4.0):
        extra = fx._pitch_splices(x, n_grains, hop, ratio)
        assert extra.min() >= 0.0 and extra.max() <= round(fx.PITCH_SEARCH * SR)
        quiet = (SR // 2) // hop + 2  # the first grain whose window is all silence
        assert np.all(extra[quiet:] == 0.0), ratio
        assert np.any(extra[1 : quiet - 1] > 0), ratio  # it does move them
    # the rack's knob plays whole semitones
    _name, kw = resolve_unit({"kind": "pitch", "params": {"semis": 6.7, "mix": 1}}, ARR)
    assert kw["semis"] == 7.0


def test_the_plate_is_silent_before_its_predelay_and_decays_on_time():
    ir = fx.plate_ir(decay=2.0, tone=0.6, predelay=0.02)
    pre = int(round(0.02 * SR))
    assert np.all(ir[:pre] == 0.0)
    assert np.max(np.abs(ir[pre : pre + 400])) > 0
    # energy in 50 ms windows: ~60 dB down by the decay (below the split)
    env = np.sqrt(np.mean(ir[pre:, 0].reshape(-1, 2205)[:50] ** 2, axis=1))
    at = int(2.0 / 0.05) - 1
    drop = 20 * np.log10(env[at] / env[1])
    assert -70 < drop < -45
    # wet gain is the room's own, as the reverb's: unit energy less 1.5 dB
    energy = np.sqrt(np.mean(np.sum(ir**2, axis=0)))
    assert energy == pytest.approx(10 ** (-1.5 / 20), rel=1e-9)
    # brighter with tone
    dark = fx.plate_ir(2.0, 0.0, 0.0)[:, 0]
    bright = fx.plate_ir(2.0, 1.0, 0.0)[:, 0]
    assert _centroid(bright[: SR // 2]) > _centroid(dark[: SR // 2]) * 1.3
