"""The dynamics FX units: the mix's dynamics — the OTT (a
three-band upward and downward compressor), the rack's limiter and a
transient shaper — and the envelope filter, which is the auto-filter's
`env`. Each is causal (a change at sample k moves nothing before k),
silent in silence, deterministic and shaped as its input, and does the one
thing its name says — measured on tones, bursts and noise, not by ear.
The numbers `docs/live-voice.md` quotes for these units are the ones
asserted here."""

import numpy as np
import pytest
from scipy.signal import freqz

from synthlab import effects as fx
from synthlab.core import SR, _lfilter
from synthlab.studio.fxrack import (
    DYNAMICS_UNITS,
    FX_PRESETS,
    FX_UNITS,
    build_chain,
    resolve_unit,
    unit_preset,
)
from synthlab.studio.state import Arrangement

ARR = Arrangement(bpm=120.0)
KINDS = ("ott", "limiter", "transient")


def _unit(kind: str, **params):
    (f,) = build_chain([{"kind": kind, "params": params}], ARR)
    return f


def _st(x: np.ndarray) -> np.ndarray:
    return np.column_stack([x, 0.8 * x])


def _db(v: float) -> float:
    return float(20 * np.log10(max(float(v), 1e-20)))


def _peak(x) -> float:
    return float(np.max(np.abs(x)))


def _hits(n: int = SR, every: float = 0.25, f0: float = 180.0) -> np.ndarray:
    """Decaying tone bursts (a drum-ish hit every `every` s) over a little
    noise: fronts, tails and a floor."""
    t = np.arange(n) / SR
    ph = t % every
    x = 0.6 * np.sin(2 * np.pi * f0 * t) * np.exp(-ph / 0.06)
    x += 0.003 * np.random.default_rng(4).standard_normal(n)
    return x


def tone(freq: float, n: int, amp: float) -> np.ndarray:
    return amp * np.sin(2 * np.pi * freq * np.arange(n) / SR)


# --- the contract, unit by unit -----------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_each_unit_is_finite_deterministic_shaped_and_causal(kind):
    loud = {
        "ott": {"depth": 1.0},
        "limiter": {"gain": 12.0},
        "transient": {"attack": 1.0, "sustain": 1.0},
    }
    x = _st(_hits())
    f = _unit(kind, **loud[kind])
    y = f(x)
    assert y.shape == x.shape and np.all(np.isfinite(y))
    np.testing.assert_array_equal(y, f(x))
    assert f(x[:, 0]).shape == (len(x),)
    later = x.copy()
    k = len(x) // 2
    later[k:] = np.random.default_rng(9).standard_normal((len(x) - k, 2)) * 0.5
    np.testing.assert_allclose(f(later)[:k], y[:k], atol=1e-12, err_msg=kind)


@pytest.mark.parametrize("kind", KINDS)
def test_silence_in_is_silence_out_at_every_units_loudest(kind):
    """The OTT lifts a quiet band up to 24 dB and the limiter takes 24 dB
    in: neither makes anything of nothing."""
    loud = {
        "ott": {"depth": 1.0, "output": 12.0, "high": 12.0},
        "limiter": {"gain": 24.0},
        "transient": {"attack": 1.0, "sustain": 1.0, "output": 12.0},
    }
    y = _unit(kind, **loud[kind])(np.zeros((SR // 2, 2)))
    assert np.max(np.abs(y)) == 0.0, kind


def test_every_unit_is_registered_with_its_presets_and_kept_from_the_agent():
    from synthlab.studio.agent.tools import AGENT_FX

    assert DYNAMICS_UNITS == set(KINDS)
    for kind in KINDS:
        spec = FX_UNITS[kind]
        for name, (lo, hi, default) in spec["params"].items():
            assert lo <= default <= hi, (kind, name)
        assert len(FX_PRESETS[kind]) >= 4
        for name in FX_PRESETS[kind]:
            assert unit_preset(kind, name)
        # every parameter maps 1:1 onto its factory
        p = {k: d for k, (_lo, _hi, d) in spec["params"].items()}
        assert resolve_unit({"kind": kind, "params": p}, ARR) == (kind, p)
        assert kind not in AGENT_FX
    # the envelope filter is the auto-filter's `env`, both ways
    assert unit_preset("autofilter", "Envelope funk")["env"] == 1.0
    assert unit_preset("autofilter", "Envelope down")["env"] == -1.0


# --- the OTT ------------------------------------------------------------------------


def test_the_bands_sum_to_the_input_through_two_all_passes():
    """Linkwitz-Riley crossovers: low + mid + high is the input through the
    two splits' all-passes (flat in level), to rounding; at depth 0 the
    unit is that sum."""
    x = _st(np.random.default_rng(2).standard_normal(SR // 2) * 0.2)
    low, mid, high = fx.ott_bands(x)
    lo_f, hi_f = fx.OTT_SPLIT
    ap = _lfilter(
        *fx.allpass_coeffs(hi_f, fx.OTT_Q),
        _lfilter(*fx.allpass_coeffs(lo_f, fx.OTT_Q), x),
    )
    np.testing.assert_allclose(low + mid + high, ap, atol=1e-9)
    np.testing.assert_allclose(_unit("ott", depth=0.0)(x), ap, atol=1e-9)
    # flat: an all-pass moves no level at any frequency
    for fc in fx.OTT_SPLIT:
        _w, h = freqz(*fx.allpass_coeffs(fc, fx.OTT_Q), worN=4096, fs=SR)
        np.testing.assert_allclose(np.abs(h), 1.0, atol=1e-12)
    # each band holds its own octaves: a 60 Hz tone is the low band's, 8 kHz
    # the high band's (>= 95 % of the energy)
    for f0, band in ((60.0, 0), (800.0, 1), (8000.0, 2)):
        parts = fx.ott_bands(_st(tone(f0, SR, 0.3)))
        e = [float(np.sum(p[SR // 4 :] ** 2)) for p in parts]
        assert e[band] / sum(e) > 0.95, (f0, e)


def test_the_ott_pulls_quiet_up_and_loud_down():
    """A tone at -50 dBFS comes up and one at -6 comes down, a band at a
    time: at depth 1 the 44 dB between them is 15.6 (-50 comes out at
    -31.9, -6 at -16.3)."""
    n = SR
    f = _unit("ott", depth=1.0)
    quiet = f(_st(tone(800.0, n, 10 ** (-50 / 20))))
    loud = f(_st(tone(800.0, n, 10 ** (-6 / 20))))
    q, lo = _db(_peak(quiet[n // 2 :, 0])), _db(_peak(loud[n // 2 :, 0]))
    assert q > -50.0 + 12.0 and lo < -6.0 - 8.0
    assert lo - q < 17.0
    # half the depth, half-way (parallel): more range than at depth 1
    half = _unit("ott", depth=0.5)
    q2 = _db(_peak(half(_st(tone(800.0, n, 10 ** (-50 / 20))))[n // 2 :, 0]))
    lo2 = _db(_peak(half(_st(tone(800.0, n, 10 ** (-6 / 20))))[n // 2 :, 0]))
    assert lo - q < lo2 - q2 < 44.0
    # upward 0: the quiet tone is left alone; downward 0: the loud one is
    # not turned down (+0.45 dB: the two empty bands' slivers of it, -39 dB
    # and under, lifted with the bands — the squash's own hiss)
    up0 = _unit("ott", depth=1.0, upward=0.0)(_st(tone(800.0, n, 10 ** (-50 / 20))))
    assert abs(_db(_peak(up0[n // 2 :, 0])) + 50.0) < 0.1
    dn0 = _unit("ott", depth=1.0, downward=0.0)(_st(tone(800.0, n, 10 ** (-6 / 20))))
    assert -6.1 < _db(_peak(dn0[n // 2 :, 0])) < -5.0


def test_a_quiet_passage_after_a_loud_one_comes_up_within_the_release():
    """A second at -6 dBFS, a second at -46, twice: once the followers
    have let go (each passage's last quarter) the 40 dB between them is
    under 17 at depth 1 — the quiet passage comes up as a quiet tone on
    its own does."""
    n = SR
    amp = np.concatenate(
        [np.full(n, 10 ** (-6 / 20)), np.full(n, 10 ** (-46 / 20))] * 2
    )
    x = _st(tone(500.0, 4 * n, 1.0) * amp)
    y = _unit("ott", depth=1.0)(x)[:, 0]
    loud = _db(_peak(y[2 * n + 3 * n // 4 : 3 * n]))
    quiet = _db(_peak(y[3 * n + 3 * n // 4 :]))
    assert loud - quiet < 17.0


def test_a_bands_gain_and_the_output_move_only_their_part():
    x = _st(tone(60.0, SR, 0.05) + tone(8000.0, SR, 0.05))
    # (upward off: the mid band's sliver of 8 kHz, -40 dB and in phase,
    # would be lifted with the empty band and blur the step)
    base = _unit("ott", depth=1.0, upward=0.0)(x)
    up = _unit("ott", depth=1.0, upward=0.0, high=6.0)(x)

    def at(a, f0):
        p = np.abs(np.fft.rfft(a[SR // 2 :, 0] * np.hanning(SR // 2)))
        f = np.fft.rfftfreq(SR // 2, 1 / SR)
        return _db(p[np.argmin(np.abs(f - f0))])

    assert at(up, 8000.0) - at(base, 8000.0) == pytest.approx(6.0, abs=0.1)
    assert abs(at(up, 60.0) - at(base, 60.0)) < 0.05
    out = _unit("ott", depth=1.0, upward=0.0, output=-6.0)(x)
    np.testing.assert_allclose(out, base * 10 ** (-6 / 20), atol=1e-12)


# --- the limiter ----------------------------------------------------------------


def test_the_limiter_holds_its_ceiling_and_is_late_by_its_lookahead():
    """Under the ceiling it is the input `lookahead` late, sample for
    sample; pushed 18 dB into it, no sample passes the ceiling and the
    gain is down before each front arrives (no overshoot, so no clip)."""
    x = _st(_hits())
    w = round(0.002 * SR)
    quiet = _unit("limiter")(x * 0.1)
    np.testing.assert_allclose(quiet[w:], x[:-w] * 0.1, atol=1e-15)
    assert np.all(quiet[:w] == 0.0)
    for ceiling in (-1.0, -6.0):
        y = _unit("limiter", gain=18.0, ceiling=ceiling)(x)
        assert _peak(y) <= 10 ** (ceiling / 20) * (1 + 1e-9)
        assert _peak(y) > 10 ** (ceiling / 20) * 0.98  # and it gets there
    # no lookahead: no latency, the peak caught on its own sample
    y0 = _unit("limiter", gain=18.0, lookahead=0.0)(x)
    assert _peak(y0) <= 10 ** (-1 / 20) * (1 + 1e-9)
    assert np.argmax(np.abs(y0[:, 0]) > 1e-6) == np.argmax(np.abs(x[:, 0]) > 1e-6)


def test_the_limiters_release_lets_the_gain_back_up():
    """A loud burst then a quiet tone: the tone comes back to unity over
    the release (a one-pole), not at once — half way back within ~0.7 of
    a release, all the way within six."""
    n = SR
    burst = np.zeros(n)
    burst[: SR // 10] = 0.9 * np.sin(2 * np.pi * 300 * np.arange(SR // 10) / SR)
    x = _st(burst + tone(300.0, n, 0.05) * (np.arange(n) >= SR // 10))
    rel = 0.1
    y = _unit("limiter", gain=12.0, ceiling=-12.0, release=rel)(x)
    w = round(0.002 * SR)
    ref = 0.05 * 10 ** (12 / 20)  # the tone at unity gain, after the limiter's gain

    def gain_at(t: float) -> float:
        k = int(t * SR) + w
        return _peak(y[k : k + 200, 0]) / ref

    after = SR // 10 / SR
    assert gain_at(after + 0.01) < 0.5
    assert gain_at(after + 6 * rel) > 0.99
    assert gain_at(after + 0.7 * rel) > 0.3


# --- the transient shaper ---------------------------------------------------------


def test_the_transient_shaper_lifts_or_softens_the_front():
    x = _st(_hits())
    w = int(0.008 * SR)

    def fronts(a):
        return np.mean([_db(_peak(a[k : k + w, 0])) for k in range(0, SR, SR // 4)])

    up = _unit("transient", attack=1.0)(x)
    down = _unit("transient", attack=-1.0)(x)
    assert fronts(up) - fronts(x) > 6.0  # measured +12 (the whole range at once)
    assert fronts(down) - fronts(x) < -3.0
    # and leaves the tails as they were (100 ms on)
    k = int(0.1 * SR)
    np.testing.assert_allclose(up[k : k + 1000], x[k : k + 1000], rtol=1e-6)


def test_the_transient_shaper_lifts_or_tightens_the_tail():
    x = _st(_hits())
    k = int(0.15 * SR)
    up = _unit("transient", sustain=1.0)(x)
    down = _unit("transient", sustain=-1.0)(x)

    def tail(a):
        return _db(_peak(a[k : k + 400, 0]))

    assert tail(up) - tail(x) > 3.0
    assert tail(down) - tail(x) < -3.0
    # the front is the sustain's to leave alone
    w = int(0.004 * SR)
    np.testing.assert_allclose(up[:w], x[:w], rtol=1e-9)


@pytest.mark.parametrize("f0", [30.0, 50.0, 200.0, 2000.0])
def test_a_steady_tone_passes_the_transient_shaper_untouched(f0):
    """Its detectors' own ripple sits under their floors: a held tone at
    either knob's end comes out at its level (after the onset's 100 ms)."""
    x = _st(tone(f0, SR, 0.3))
    for a, s in ((1.0, 1.0), (-1.0, -1.0)):
        y = _unit("transient", attack=a, sustain=s)(x)
        k = SR // 4
        np.testing.assert_allclose(y[k:], x[k:], rtol=1e-6, err_msg=str(f0))


def test_the_peak_follower_is_its_recursion():
    """`peak_follow` as a running maximum in the log domain is e[n] =
    max(l[n], e[n - 1] r) — what the live twin runs — to rounding."""
    lev = np.abs(_hits(SR // 4))
    r = np.exp(-1.0 / (0.1 * SR))
    want, e = np.empty_like(lev), 0.0
    for i, v in enumerate(lev):
        e = max(max(v, 1e-12), e * r)
        want[i] = e
    np.testing.assert_allclose(fx.peak_follow(lev, 0.1), want, rtol=1e-10)
