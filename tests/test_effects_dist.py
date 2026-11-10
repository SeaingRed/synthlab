"""Distortion and dynamics: the drive's modes, the clipper, the compressor's
attack / release / makeup — and that the defaults are the units as they
were before any of these existed.

The pinned samples were rendered by the code before these options existed,
through `fxrack.build_chain` at each unit's defaults and at
one setting off them; native and NumPy kernels agree to an ulp, so they
are held to 1e-12."""

import numpy as np
import pytest

from synthlab import effects as fx
from synthlab.core import SR
from synthlab.studio.fxrack import build_chain
from synthlab.studio.state import Arrangement

ARR = Arrangement(bpm=128.0)


def _signal() -> np.ndarray:
    t = np.arange(4096) / SR
    x = 0.9 * np.sin(2 * np.pi * 110 * t) + 0.3 * np.sin(2 * np.pi * 1234 * t) * np.exp(
        -t * 8
    )
    return np.column_stack([x, 0.7 * x[::-1]])


# (chain, samples [7, 300, 1501, 3999] of the left channel, [2222] of the right)
BEFORE = {
    "drive_def": (
        [{"kind": "drive", "params": {}}],
        [
            0.6662145337360835,
            -0.9291944287049537,
            -0.9817589653051726,
            -0.44190995104526015,
        ],
        -0.7678875596239693,
    ),
    "drive_hot": (
        [{"kind": "drive", "params": {"amount": 3.2, "mix": 0.8}}],
        [
            0.7499662117664068,
            -0.9323647498031185,
            -0.9772140370842807,
            -0.544642262568028,
        ],
        -0.8247223734390813,
    ),
    "comp_def": (
        [{"kind": "compress", "params": {}}],
        [
            0.697301817954661,
            -0.6432840240176346,
            -0.6338900305915767,
            -0.16047584649137,
        ],
        -0.337679350843026,
    ),
    "comp_hot": (
        [{"kind": "compress", "params": {"threshold": -24.0, "ratio": 8.0}}],
        [
            1.2762060451624206,
            -0.3810462181166214,
            -0.36143751683671704,
            -0.09149454118298297,
        ],
        -0.1927414964932881,
    ),
}


@pytest.mark.parametrize("name", list(BEFORE))
def test_a_stored_unit_renders_what_it_rendered_before_the_new_params(name):
    """A drive or a compressor stored without a mode / attack / release /
    makeup gets the defaults, and the defaults are the old unit."""
    units, left, right = BEFORE[name]
    y = fx.chain(_signal(), build_chain(units, ARR))
    got = [y[i, 0] for i in (7, 300, 1501, 3999)] + [y[2222, 1]]
    np.testing.assert_allclose(got, [*left, right], rtol=1e-12, atol=1e-15)


def test_soft_is_the_old_drive_bit_for_bit():
    x = _signal()
    for amount in (1.0, 2.0, 3.7):
        old = x * (1 - 0.7) + fx.soft_clip(x, amount) * 0.7
        np.testing.assert_array_equal(fx._drive(x, amount, 0.7), old)
        np.testing.assert_array_equal(fx._drive(x, amount, 0.7, mode="soft"), old)


def _harmonics(y: np.ndarray, f0: float) -> np.ndarray:
    """Magnitudes at f0 x 1..6 (a whole number of cycles: no window)."""
    mag = np.abs(np.fft.rfft(y)) / len(y)
    k = round(f0 * len(y) / SR)
    return np.array([mag[k * h] for h in range(1, 7)])


@pytest.mark.parametrize("mode", fx.DRIVE_MODES)
def test_every_mode_distorts_and_stays_in_bounds(mode):
    f0 = SR / 400  # 400 samples a cycle: the harmonics land on bins
    x = 0.8 * np.sin(2 * np.pi * f0 * np.arange(400 * 55) / SR)
    y = fx._drive(x, 6.0, 1.0, mode=mode)
    h = _harmonics(y, f0)
    assert h[1:].max() > 0.05 * h[0], mode  # new harmonics, well above rounding
    assert np.max(np.abs(y)) <= 1.0 + 1e-12
    assert fx._drive(np.zeros(64), 6.0, 1.0, mode=mode).tolist() == [0.0] * 64
    # silence in, silence out: the live worklet's culling relies on it


@pytest.mark.parametrize(
    ("mode", "even"), [("soft", False), ("hard", False), ("fuzz", True), ("tube", True)]
)
def test_fuzz_and_tube_are_asymmetric_soft_and_hard_are_not(mode, even):
    """Even harmonics come only from an asymmetric curve: fuzz and tube
    have them, soft and hard (odd curves) none."""
    f0 = SR / 400
    x = 0.8 * np.sin(2 * np.pi * f0 * np.arange(400 * 55) / SR)
    h = _harmonics(fx._drive(x, 3.0, 1.0, mode=mode), f0)
    if even:
        assert h[1] > 0.02 * h[0], mode
    else:
        assert h[1] < 1e-9 * h[0], mode


def test_fuzz_is_the_heaviest_and_tube_is_soft_plus_even_harmonics():
    """At one amount (measured THD: soft 0.21, tube 0.22, hard 0.27, fuzz
    0.34): tube's odd series is soft's, with a 2nd under fuzz's."""
    f0 = SR / 400
    x = 0.8 * np.sin(2 * np.pi * f0 * np.arange(400 * 55) / SR)
    thd, h = {}, {}
    for mode in fx.DRIVE_MODES:
        h[mode] = _harmonics(fx._drive(x, 3.0, 1.0, mode=mode), f0)
        thd[mode] = np.sqrt((h[mode][1:] ** 2).sum()) / h[mode][0]
    assert thd["fuzz"] > thd["hard"] > thd["tube"] > thd["soft"]
    rel = {m: v / v[0] for m, v in h.items()}
    np.testing.assert_allclose(rel["tube"][2], rel["soft"][2], rtol=0.05)
    assert 0.02 < rel["tube"][1] < rel["fuzz"][1]
    hard = fx._drive(x, 3.0, 1.0, mode="hard")
    assert np.max(hard) == 1.0 and np.min(hard) == -1.0


@pytest.mark.parametrize("mode", fx.DRIVE_MODES)
def test_a_drive_amount_array_is_the_scalar_where_it_stands_still(mode):
    """A lane's per-sample amount, in every mode, on mono and stereo."""
    x = _signal()
    for sig in (x, x[:, 0]):
        flat = np.full(len(sig), 5.5)
        np.testing.assert_allclose(
            fx._drive(sig, flat, 1.0, mode=mode),
            fx._drive(sig, 5.5, 1.0, mode=mode),
            rtol=0,
            atol=1e-15,
        )


def test_an_unknown_drive_mode_is_an_error():
    with pytest.raises(ValueError):
        fx._drive(np.zeros(4), 2.0, 1.0, mode="nope")


# --- the clipper -------------------------------------------------------------


@pytest.mark.parametrize("soft", [0.0, 0.3, 1.0])
@pytest.mark.parametrize("ceiling", [-12.0, -1.0, 0.0])
def test_the_clipper_never_passes_its_ceiling(soft, ceiling):
    x = _signal() * 1.5
    y = fx._clip(x, drive=24.0, ceiling=ceiling, soft=soft, mix=1.0)
    assert np.max(np.abs(y)) <= 10 ** (ceiling / 20) + 1e-12
    if soft == 0.0:  # a hard clip reaches it
        assert np.isclose(np.max(np.abs(y)), 10 ** (ceiling / 20))


def test_the_clipper_is_linear_below_its_knee():
    c = 10 ** (-1.0 / 20)
    x = np.linspace(-1, 1, 1001) * c * 0.69  # under t = c (1 - 0.3)
    np.testing.assert_allclose(fx._clip(x, drive=0.0, soft=0.3), x, rtol=0, atol=1e-15)


def test_the_knee_meets_the_line_smoothly():
    """Continuous and with a continuous slope at t: no kink to alias."""
    c, k = 10 ** (-1.0 / 20), 0.3
    t = c * (1 - k)
    e = 1e-7
    lo, at, hi = fx._clip(np.array([t - e, t, t + e]), drive=0.0, soft=k)
    assert abs(at - t) < 1e-15
    assert abs((hi - at) / e - 1.0) < 1e-5 and abs((at - lo) / e - 1.0) < 1e-5


def test_the_clipper_is_causal_and_stereo_safe():
    """Sample n depends on sample n alone: changing what comes after n
    leaves 0..n as it was, and each channel is clipped on its own."""
    x = _signal()
    y = fx._clip(x, drive=12.0, ceiling=-3.0, soft=0.5, mix=0.8)
    later = x.copy()
    later[2000:] = np.random.default_rng(1).normal(size=(len(x) - 2000, 2))
    y2 = fx._clip(later, drive=12.0, ceiling=-3.0, soft=0.5, mix=0.8)
    np.testing.assert_array_equal(y[:2000], y2[:2000])
    left = fx._clip(x[:, 0], drive=12.0, ceiling=-3.0, soft=0.5, mix=0.8)
    np.testing.assert_array_equal(y[:, 0], left)


@pytest.mark.parametrize("mode", fx.DRIVE_MODES)
def test_the_drive_is_causal(mode):
    x = _signal()
    later = x.copy()
    later[1000:] *= -3.0
    a = fx._drive(x, 7.0, 0.6, mode=mode)
    b = fx._drive(later, 7.0, 0.6, mode=mode)
    np.testing.assert_array_equal(a[:1000], b[:1000])


def test_the_clipper_mix_blends_dry_and_a_drive_array_is_the_scalar():
    x = _signal()
    np.testing.assert_array_equal(fx._clip(x, mix=0.0), x)
    flat = np.full(len(x), 9.0)
    np.testing.assert_allclose(
        fx._clip(x, drive=flat), fx._clip(x, drive=9.0), atol=1e-15
    )


# --- the compressor ----------------------------------------------------------


def test_compressor_attack_and_release_change_the_envelope():
    """A burst over the threshold: a slower attack lets more of its onset
    through, a slower release holds the gain down longer after it."""
    n = SR // 2
    x = np.zeros(n)
    x[1000:10000] = 0.9 * np.sin(2 * np.pi * 220 * np.arange(9000) / SR)
    x[10000:] = 0.05 * np.sin(2 * np.pi * 220 * np.arange(n - 10000) / SR)
    fast = fx._compress(x, -20.0, 8.0, attack=0.0005, release=0.12)
    slow = fx._compress(x, -20.0, 8.0, attack=0.05, release=0.12)
    onset = slice(1000, 1400)
    assert np.max(np.abs(slow[onset])) > 1.5 * np.max(np.abs(fast[onset]))
    # (`_envelope_follower` is the max of a one-pole at each rate, so a
    # release much longer than the burst never charges: 1 s holds less
    # than 0.12 s here — the detector as it always was)
    short = fx._compress(x, -20.0, 8.0, release=0.02)
    held = fx._compress(x, -20.0, 8.0, release=0.12)
    after = slice(11000, 14000)

    def rms(y):
        return np.sqrt(np.mean(y[after] ** 2))

    assert rms(short) > 2.0 * rms(held)  # measured 0.091 vs 0.037


def test_compressor_makeup_is_a_plain_gain_and_none_is_automatic():
    x = _signal()
    unity = fx.compress(-20.0, 4.0, makeup=1.0)(x)
    twice = fx.compress(-20.0, 4.0, makeup=2.0)(x)
    np.testing.assert_allclose(twice, 2.0 * unity, rtol=1e-15)
    auto = fx.compress(-20.0, 4.0)(x)
    np.testing.assert_array_equal(auto, fx._compress(x, -20.0, 4.0))
    np.testing.assert_allclose(auto, unity * 10 ** (20 * 0.75 * 0.5 / 20), rtol=1e-14)
