"""The movement, tape and spectral FX units: auto-filter, tremolo / auto-
pan, tape, ping-pong delay, spring, formant shifter, frequency shifter,
exciter and the rack's vocoder. Each is causal (a change at sample k moves
nothing before k), silent in silence, bounded, deterministic and shaped as
its input, and does the one thing its name says — measured on tones,
noise and clicks, not by ear. The numbers `docs/live-voice.md` quotes for
these units are the ones asserted here."""

import numpy as np
import pytest

from synthlab import effects as fx
from synthlab.core import SR, biquad
from synthlab.studio.fxrack import FX_UNITS, LFO_BEATS, build_chain, resolve_unit
from synthlab.studio.state import Arrangement

ARR = Arrangement(bpm=120.0, root="C3", scale_name="major")
KINDS = (
    "autofilter",
    "tremolo",
    "tape",
    "pingpong",
    "spring",
    "formant_shift",
    "freqshift",
    "exciter",
    "vocoder_fx",
)


def _unit(kind: str, **params):
    (f,) = build_chain([{"kind": kind, "params": params}], ARR)
    return f


def saw(freq: float, n: int, amp: float = 0.3, top: float = 10000.0) -> np.ndarray:
    """A band-limited saw (additive, under `top`)."""
    ph = 2 * np.pi * freq * np.arange(n) / SR
    out = np.zeros(n)
    for k in range(1, int(top // freq) + 1):
        out += np.sin(k * ph) / k
    return amp * out / 1.7


def tone(freq: float, n: int, amp: float = 0.3) -> np.ndarray:
    return amp * np.sin(2 * np.pi * freq * np.arange(n) / SR)


def _st(x: np.ndarray) -> np.ndarray:
    return np.column_stack([x, x])


def _level(x: np.ndarray, f0: float) -> float:
    """dB of the strongest bin within 3 of `f0` (Hann, amplitude-scaled)."""
    p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) * 4 / len(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    i = int(np.argmin(np.abs(f - f0)))
    return float(20 * np.log10(p[max(0, i - 3) : i + 4].max() + 1e-20))


def _rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(x))))


def _db(v: float) -> float:
    return float(20 * np.log10(max(v, 1e-20)))


def _input(n: int = SR) -> np.ndarray:
    t = np.arange(n) / SR
    x = saw(110.0, n) * ((t * 2) % 1.0 < 0.6)
    x += 0.02 * np.random.default_rng(3).standard_normal(n)
    return np.column_stack([x, 0.8 * x])


# --- the contract, unit by unit -----------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_each_unit_is_finite_bounded_deterministic_shaped_and_causal(kind):
    x = _input()
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


@pytest.mark.parametrize("kind", KINDS)
def test_silence_in_is_silence_out_at_every_units_loudest(kind):
    """The tape's hiss rides under the signal and the vocoder's carrier
    sounds only through the input's bands: a unit at rest is silent, with
    its hiss and noise up."""
    loud = {"tape": {"hiss": 1.0, "drive": 24.0}, "vocoder_fx": {"noise": 1.0}}
    y = _unit(kind, **loud.get(kind, {}))(np.zeros((SR // 2, 2)))
    assert np.max(np.abs(y)) == 0.0, kind


def test_every_unit_is_registered_with_its_choices_and_presets():
    for kind in KINDS:
        spec = FX_UNITS[kind]
        for name, (lo, hi, default) in spec["params"].items():
            assert lo <= default <= hi, (kind, name)
        for name, labels in spec.get("choices", {}).items():
            assert spec["params"][name][1] == len(labels) - 1, (kind, name)


# --- the auto-filter --------------------------------------------------------------


def test_the_autofilter_sweeps_its_cutoff_up_depth_octaves_on_the_lfo():
    """The LFO's sine starts at its trough on the bar: the cutoff runs from
    `cutoff` to `cutoff` x 2^depth (400 -> 3200 Hz at the defaults), and a
    synced rate is one cycle a note length (1 bar at 120 bpm: 2 s)."""
    name, kw = resolve_unit({"kind": "autofilter", "params": _clean("autofilter")}, ARR)
    assert name == "autofilter" and kw["rate"] == pytest.approx(0.5)
    assert LFO_BEATS[3] == 4.0  # the default sync: a bar
    fc = fx.autofilter_cutoff(np.zeros(2 * SR), kw["cutoff"], kw["depth"], kw["rate"])
    assert fc.min() == pytest.approx(400.0) and fc.max() == pytest.approx(
        3200.0, rel=1e-6
    )
    assert fc[0] == pytest.approx(400.0) and int(np.argmax(fc)) == pytest.approx(
        SR, abs=2
    )
    # what it does to noise: the top's share low on the bar, high mid-cycle
    noise = np.random.default_rng(1).standard_normal(2 * SR) * 0.1
    y = _unit("autofilter", q=0.707)(_st(noise))[:, 0]

    def bright(at: float) -> float:
        seg = y[int(at * SR) : int(at * SR) + 4096]
        p = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
        f = np.fft.rfftfreq(len(seg), 1 / SR)
        return float((f * p).sum() / p.sum())

    assert bright(1.0) > 4 * bright(0.0)  # measured 2082 vs 255 Hz centroid


def test_the_autofilter_envelope_opens_on_a_hit_or_closes_with_a_negative_env():
    n = SR
    x = np.zeros(n)
    x[SR // 4 : SR // 2] = tone(220.0, SR // 4, 0.5)
    lvl = np.abs(x)
    up = fx.autofilter_cutoff(lvl, 400.0, 3.0, 0.5, env=1.0, sens=12.0)
    down = fx.autofilter_cutoff(lvl, 400.0, 3.0, 0.5, env=-1.0, sens=12.0)
    k = int(0.3 * SR)
    assert up[k] == pytest.approx(3200.0, rel=1e-3)  # wide open on the hit
    assert down[k] == pytest.approx(50.0, rel=1e-3)  # 3 octaves down
    assert up[10] == pytest.approx(400.0) == down[10]  # nothing in: at rest


def test_an_autofilter_that_stands_still_is_the_static_filter():
    x = _input()
    for mode in ("lowpass", "bandpass", "highpass"):
        got = fx.autofilter(mode=mode, cutoff=800.0, q=2.0, depth=0.0)(x)
        np.testing.assert_allclose(got, biquad(x, mode, 800.0, 2.0), atol=1e-12)


def _clean(kind: str) -> dict:
    return {k: d for k, (_lo, _hi, d) in FX_UNITS[kind]["params"].items()}


# --- the tremolo / auto-pan -------------------------------------------------------


def test_the_tremolo_dips_by_its_depth_and_pans_at_180_degrees():
    x = _st(tone(440.0, 4 * SR))
    blk = 441

    def env(y, c=0):
        return np.abs(y[: 2 * SR, c]).reshape(-1, blk).max(axis=1)

    for depth, want in ((0.5, 6.02), (0.25, 2.5)):
        e = env(_unit("tremolo", depth=depth, sync=0.0, rate=4.0)(x))
        assert _db(e.max() / e.min()) == pytest.approx(want, abs=0.1)
    e = env(_unit("tremolo", depth=1.0, sync=0.0, rate=4.0)(x))
    assert _db(e.max() / e.min()) > 40.0  # measured 48.5 (a 10 ms block's peak)
    pan = _unit("tremolo", depth=1.0, phase=180.0)(x)
    assert np.corrcoef(env(pan, 0), env(pan, 1))[0, 1] < -0.95  # measured -0.995
    np.testing.assert_array_equal(_unit("tremolo", depth=0.0)(x), x)


def test_the_lfo_shapes_start_at_their_foot_on_the_bar():
    ph = np.linspace(0.0, 1.0, 9, endpoint=False)
    for shape in fx.LFO_SHAPES:
        w = fx.lfo_wave(ph, shape)
        assert w.min() >= 0.0 and w.max() <= 1.0, shape
    assert fx.lfo_wave(np.zeros(1), "sine")[0] == 0.0
    assert fx.lfo_wave(np.array([0.5]), "sine")[0] == 1.0
    assert fx.lfo_wave(np.array([0.25]), "square")[0] == 0.0
    assert fx.lfo_wave(np.array([0.75]), "square")[0] == 1.0


# --- the tape -------------------------------------------------------------------


def test_the_tape_saturates_more_as_it_is_driven():
    """A 1 kHz sine at -6 dBFS, flat tape (no wow, flutter or bump, top
    open): the third harmonic against the first, measured -34.1 / -23.5 /
    -15.5 dB at 0 / 6 / 12 dB of drive."""
    x = tone(1000.0, 2 * SR, 0.5)
    third = []
    for drive in (0.0, 6.0, 12.0):
        y = _unit("tape", drive=drive, wow=0.0, flutter=0.0, bump=0.0, tone=1.0)(x)
        s = y[SR // 2 :]
        third.append(_level(s, 3000.0) - _level(s, 1000.0))
    assert third[0] < -30.0 and third[1] > third[0] + 6 and third[2] > third[1] + 6


def test_the_tapes_response_bump_top_and_low_cut():
    """An impulse through the tape at rest (bump 2 dB, tone 0.6): +2 dB at
    the head bump (90 Hz), flat through the mids, -1.4 dB at 10 kHz, the
    lows under 25 Hz going (a tape passes no DC)."""
    imp = np.zeros(SR)
    imp[0] = 1e-3  # small: the tanh's linear region
    y = _unit("tape", drive=0.0, wow=0.0, flutter=0.0)(imp)
    h = np.abs(np.fft.rfft(y)) / 1e-3
    f = np.fft.rfftfreq(len(y), 1 / SR)

    def at(hz):
        return _db(h[int(np.argmin(np.abs(f - hz)))])

    assert at(90.0) == pytest.approx(2.0, abs=0.3)
    assert abs(at(1000.0)) < 0.1
    assert -3.0 < at(10000.0) < -0.5
    assert at(10.0) < -10.0
    assert abs(float(y.sum())) < 1e-6  # no DC: the response sums to nothing


def test_the_tapes_wow_and_flutter_waver_the_pitch_and_are_functions_of_time():
    x = tone(440.0, 4 * SR)
    for knob in ("wow", "flutter"):
        y = _unit(
            "tape",
            drive=0.0,
            bump=0.0,
            tone=1.0,
            **{"wow": 0.0, "flutter": 0.0, knob: 1.0},
        )(x)
        zc = np.flatnonzero((y[:-1] <= 0) & (y[1:] > 0))
        at = zc + y[zc] / (
            y[zc] - y[zc + 1]
        )  # each upward crossing, to the sample's fraction
        cents = 1200 * np.log2((SR / np.diff(at)) / 440.0)[100:]
        # measured +-8 cents (wow, three slow sines) and +-6 (flutter, 7.3 Hz)
        assert 8.0 < cents.max() - cents.min() < 30.0, knob
    # a function of time: the first half of a buffer is the first half of a
    # longer one, sample for sample
    f = _unit("tape", hiss=0.5)
    long = _st(saw(110.0, 2 * SR))
    np.testing.assert_array_equal(f(long[:SR]), f(long)[:SR])


def test_the_tapes_hiss_is_a_function_of_the_sample_and_rides_the_signal():
    a = fx.tick_noise(np.arange(1000, 2000))
    b = fx.tick_noise(np.arange(0, 3000))[1000:2000]
    np.testing.assert_array_equal(a, b)
    assert abs(float(a.mean())) < 0.1 and 0.5 < float(a.std()) < 0.65  # white, -1..1
    assert not np.array_equal(
        fx.tick_noise(np.arange(64), 0), fx.tick_noise(np.arange(64), 1)
    )
    x = tone(440.0, 2 * SR)
    still = {"drive": 6.0, "wow": 0.0, "flutter": 0.0}
    hiss = _unit("tape", hiss=1.0, **still)(x) - _unit("tape", hiss=0.0, **still)(x)
    assert -65.0 < _db(_rms(hiss[SR // 2 :])) < -52.0  # measured -58.5 dBFS


# --- the ping-pong --------------------------------------------------------------------


def test_the_pingpong_bounces_left_then_right_each_echo_down_by_the_feedback():
    """A 1 kHz blip (both channels) 0.25 s apart: the echoes take turns,
    left first, each `feedback` (0.5: -6 dB) under the last."""
    m = int(0.01 * SR)
    blip = np.zeros((3 * SR, 2))
    blip[:m] = (tone(1000.0, m, 1.0) * np.hanning(m))[:, None]
    y = _unit(
        "pingpong",
        sync=0.0,
        time=0.25,
        feedback=0.5,
        lowcut=20.0,
        highcut=16000.0,
        mix=1.0,
    )(blip)
    echo = []
    for k in range(1, 5):
        at = int(0.25 * k * SR)
        echo.append([_rms(y[at - 50 : at + m + 400, c]) for c in (0, 1)])
    (l1, r1), (l2, r2), (l3, r3), (l4, r4) = echo
    assert max(r1, l2, r3, l4) < 1e-6 * l1  # side to side
    assert _db(r2 / l1) == pytest.approx(-6.0, abs=0.3)
    assert _db(l3 / r2) == pytest.approx(-6.0, abs=0.3)
    assert _db(r4 / l3) == pytest.approx(-6.0, abs=0.3)
    # synced: dotted eighths at 120 bpm
    _name, kw = resolve_unit({"kind": "pingpong", "params": _clean("pingpong")}, ARR)
    assert kw["time"] == pytest.approx(0.375)


# --- the spring -----------------------------------------------------------------------


def _arrival_ms(ir: np.ndarray, hz: float) -> float:
    seg = biquad(ir[: int(0.085 * SR), 0], "bandpass", hz, 4.0)
    return float(np.argmax(np.abs(seg)) / SR * 1000)


def test_the_spring_chirps_down_and_drips_more_with_drip():
    """The first pass's lows come round after its highs (a falling chirp):
    300 Hz against 3 kHz, measured 9.2 ms later at drip 0, 24.3 ms at 1."""
    spread = []
    for drip in (0.0, 0.5, 1.0):
        ir = fx.spring_ir(2.0, 0.5, drip)
        spread.append(_arrival_ms(ir, 300.0) - _arrival_ms(ir, 3000.0))
    assert 5.0 < spread[0] < spread[1] < spread[2]
    assert spread[2] > 18.0


def test_the_spring_decays_over_its_decay_and_is_deterministic():
    for decay in (1.0, 2.0, 4.0):
        ir = fx.spring_ir(decay, 0.5, 0.5)
        e = np.convolve(ir[:, 0] ** 2, np.ones(2205) / 2205, "valid")
        env = 10 * np.log10(e + 1e-30)
        t60 = float(np.argmax(env < env[: SR // 10].max() - 60) / SR)
        assert 0.7 * decay < t60 < 1.1 * decay, (decay, t60)  # measured 0.91 x
    np.testing.assert_array_equal(
        fx.spring_ir(2.0, 0.5, 0.5), fx.spring_ir(2.0, 0.5, 0.5)
    )


# --- the formant shifter --------------------------------------------------------------


def _vowel(src: np.ndarray) -> np.ndarray:
    return (
        biquad(src, "bandpass", 700, 5.0)
        + 0.7 * biquad(src, "bandpass", 1200, 6.0)
        + 0.4 * biquad(src, "bandpass", 2600, 6.0)
    )


GRID = np.geomspace(250, 5000, 400)


def _logspec(y: np.ndarray) -> np.ndarray:
    s = y[SR // 2 :]
    nper = 4096
    segs = [
        s[i : i + nper] * np.hanning(nper) for i in range(0, len(s) - nper, nper // 2)
    ]
    p = np.mean([np.abs(np.fft.rfft(g)) ** 2 for g in segs], axis=0)
    f = np.fft.rfftfreq(nper, 1 / SR)
    return np.array(
        [
            10 * np.log10(p[(f > g * 2 ** (-1 / 12)) & (f < g * 2 ** (1 / 12))].mean())
            for g in GRID
        ]
    )


def _moved(x: np.ndarray, y: np.ndarray) -> float:
    """The semitones that best line `y`'s smoothed spectrum up with `x`'s."""
    a, b = _logspec(x), _logspec(y)
    a, b = a - a.mean(), b - b.mean()
    lags = range(-80, 81)

    def fit(k: int) -> float:
        return (
            float(np.dot(a[: len(a) - k], b[k:]) / (len(a) - k))
            if k >= 0
            else fit_neg(k)
        )

    def fit_neg(k: int) -> float:
        return float(np.dot(a[-k:], b[: len(b) + k]) / (len(a) + k))

    best = max(lags, key=fit)
    return best * 12 * float(np.log2(GRID[1] / GRID[0]))


@pytest.mark.parametrize("shift", [-8.0, -4.0, 4.0, 8.0])
def test_the_formant_shifter_moves_the_envelope_by_about_its_shift(shift):
    """Noise through three formants: the output's spectrum lines up with
    the input's moved by -8.7 / -3.6 / +3.1 / +8.2 st for -8 / -4 / +4 / +8
    (measured, 1/6-octave smoothed)."""
    x = _vowel(np.random.default_rng(4).standard_normal(3 * SR) * 0.2)
    got = _moved(x, fx._formant_shift(x, shift=shift))
    assert abs(got - shift) < 1.5, (shift, got)


def test_the_formant_shifter_keeps_the_pitch_and_passes_at_zero():
    x = _vowel(saw(98.0, 2 * SR))
    y = fx._formant_shift(x, shift=5.0)[SR // 2 :]
    p = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
    f = np.fft.rfftfreq(len(y), 1 / SR)
    on = np.zeros(len(f), dtype=bool)
    for k in range(1, 60):
        on |= np.abs(f - 98.0 * k) < 4.0
    assert p[on].sum() / p.sum() > 0.99  # every partial where it was
    np.testing.assert_array_equal(fx._formant_shift(_st(x), shift=0.0), _st(x))


# --- the frequency shifter ------------------------------------------------------------


@pytest.mark.parametrize(
    ("f0", "shift"),
    [
        (1000, 100.0),
        (1000, -100.0),
        (200, 30.0),
        (5000, 250.0),
        (60, 30.0),
        (12000, -400.0),
    ],
)
def test_the_frequency_shifter_keeps_one_sideband(f0, shift):
    """A sine moved `shift` Hz: the image (f0 - shift) measured 44 dB down
    at 60 Hz, 51-74 dB from 200 Hz to 12 kHz."""
    y = _unit("freqshift", shift=shift, mix=1.0)(tone(f0, 2 * SR))[SR // 2 :]
    want, image = _level(y, f0 + shift), _level(y, f0 - shift)
    assert want == pytest.approx(_db(0.3), abs=0.5)
    assert want - image > (40.0 if f0 < 100 else 48.0), (f0, shift, want - image)


def test_the_frequency_shifters_feedback_climbs_another_shift_each_pass():
    y = _unit("freqshift", shift=100.0, feedback=0.6, mix=1.0)(tone(1000.0, 2 * SR))[
        SR // 2 :
    ]
    one, two, three = (_level(y, 1000.0 + 100.0 * k) for k in (1, 2, 3))
    assert two == pytest.approx(one + _db(0.6), abs=1.5)
    assert three == pytest.approx(two + _db(0.6), abs=1.5)


# --- the exciter ----------------------------------------------------------------------


def test_the_exciter_makes_harmonics_over_its_frequency_and_nothing_under():
    """A 4 kHz sine (freq 3 kHz): second and third harmonics ~28 dB under
    it; a 500 Hz one gains nothing (measured -82 dBFS added)."""
    hi = tone(4000.0, 2 * SR)
    y = _unit("exciter", freq=3000.0, drive=6.0, amount=0.5)(hi)[SR // 2 :]
    assert _level(y, 8000.0) - _level(y, 4000.0) > -35.0
    assert _level(y, 12000.0) - _level(y, 4000.0) > -35.0
    lo = tone(500.0, 2 * SR)
    added = _unit("exciter", freq=3000.0, amount=0.5)(lo) - lo
    assert _db(_rms(added[SR // 2 :])) < -70.0
    np.testing.assert_array_equal(_unit("exciter", amount=0.0)(_st(hi)), _st(hi))


def test_the_exciters_small_signal_slope_is_unity():
    """The shaper's slope at rest is divided out: a quiet band comes back
    at its own level, twice high-passed."""
    x = tone(8000.0, SR, 1e-4)
    add = fx._exciter(x, freq=1000.0, drive=10.0, amount=1.0) - x
    assert _db(_rms(add[SR // 2 :]) / _rms(x[SR // 2 :])) == pytest.approx(0.0, abs=0.3)


# --- the rack's vocoder ---------------------------------------------------------------


def test_the_vocoder_speaks_on_its_chord_in_the_key():
    _name, kw = resolve_unit(
        {"kind": "vocoder_fx", "params": _clean("vocoder_fx")}, ARR
    )
    assert kw["notes"] == pytest.approx([130.81, 164.81, 196.0], abs=0.01)  # C E G
    src = saw(98.0, 2 * SR, 0.3)
    voice = (
        biquad(src, "bandpass", 700, 4.0) * 3 + biquad(src, "bandpass", 1500, 4.0) * 2
    )
    y = _unit("vocoder_fx", noise=0.0)(_st(voice))[SR // 2 :, 0]
    p = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
    f = np.fft.rfftfreq(len(y), 1 / SR)
    on = np.zeros(len(f), dtype=bool)
    for hz in kw["notes"]:
        for k in range(1, 80):
            on |= np.abs(f - hz * k) < 3.0
    assert p[on].sum() / p.sum() > 0.95  # the chord's partials, not the voice's
    # the level stays near the input's (measured -0.9 dB)
    assert abs(_db(_rms(y) / _rms(voice[SR // 2 :]))) < 4.0
