"""The native kernels (crates/, `synthlab_dsp`) against their NumPy twins.

Each kernel's NumPy version is the reference; the native one must match it
to rounding. And the module must actually be in use: a checkout whose
extension didn't build would pass every other test on the NumPy fallback
and simply feel slow, so that is a failure here (set SYNTHLAB_NATIVE=0 to
run the suite on the fallback deliberately).
"""

import hashlib
import importlib.util
import os
import platform
from pathlib import Path

import numpy as np
import pytest

from synthlab import _native, core
from synthlab import effects as fx

native_off = os.environ.get("SYNTHLAB_NATIVE") == "0"
needs_native = pytest.mark.skipif(native_off, reason="SYNTHLAB_NATIVE=0")


def test_native_module_is_built_and_enabled():
    if native_off:
        assert not _native.ENABLED
        return
    assert _native.ENABLED, (
        "synthlab_dsp didn't import — install Rust and `uv sync --all-extras` "
        "(crates/README.md)"
    )


def _swept_case(rng, n, ch, per_block_q):
    shape = (n,) if ch == 1 else (n, ch)
    x = rng.standard_normal(shape)
    nb = -(-n // 64)
    fc = np.geomspace(40.0, 18000.0, nb) * (1 + 0.3 * np.sin(np.arange(nb)))
    q = rng.uniform(0.3, 12.0, nb) if per_block_q else 2.5
    return x, fc, q


@needs_native
@pytest.mark.parametrize("kind", ["lowpass", "highpass", "bandpass", "notch"])
@pytest.mark.parametrize(
    "n, ch, per_block_q",
    [(64, 1, False), (1000, 1, True), (44100 + 13, 2, False), (5000, 2, True)],
)
def test_swept_svf_matches_numpy(kind, n, ch, per_block_q):
    x, fc, q = _swept_case(np.random.default_rng(n + ch), n, ch, per_block_q)
    got = core._swept_biquad(x, kind, fc, q, core.SR, 64)
    want = core._swept_biquad_numpy(x, kind, fc, q, core.SR, 64)
    assert got.shape == want.shape
    np.testing.assert_allclose(got, want, rtol=1e-9, atol=1e-10)


@needs_native
def test_swept_svf_accepts_non_contiguous_input():
    x = np.random.default_rng(1).standard_normal((2000, 2))[::-1]  # negative stride
    fc = np.full(32, 900.0)
    got = core._swept_biquad(x, "lowpass", fc, 0.9, core.SR, 64)
    want = core._swept_biquad_numpy(x, "lowpass", fc, 0.9, core.SR, 64)
    np.testing.assert_allclose(got, want, rtol=1e-9, atol=1e-10)


@needs_native
def test_swept_svf_rejects_unknown_kind():
    with pytest.raises(ValueError):
        core._swept_biquad(
            np.zeros(128), "allpass", np.full(2, 100.0), 1.0, core.SR, 64
        )


def _freqs(n):
    t = np.arange(n) / core.SR
    return {
        "const": 220.0,
        "vibrato": 220.0 * 2 ** (0.3 * np.sin(2 * np.pi * 5.5 * t) / 12),
        "short": np.array([110.0, 440.0, 55.0]),  # resampled by as_array
    }


@needs_native
@pytest.mark.parametrize("shape", ["sine", "saw", "square", "triangle"])
@pytest.mark.parametrize("freq", ["const", "vibrato", "short"])
@pytest.mark.parametrize("phase0", [0.0, 0.37])
def test_osc_matches_numpy(shape, freq, phase0):
    n = 44100 + 7
    f = _freqs(n)[freq]
    got = getattr(core, shape)(f, n, core.SR, phase0)
    want = getattr(core, f"{shape}_numpy")(f, n, core.SR, phase0)
    assert got.shape == want.shape == (n,)
    # saw/square/triangle share the phase arithmetic exactly; sine differs
    # only by the two sin() implementations' last ulp
    tol = 1e-12 if shape == "sine" else 0.0
    np.testing.assert_allclose(got, want, rtol=0, atol=tol)


@needs_native
def test_square_pwm_matches_numpy():
    n = 20000
    width = 0.5 + 0.4 * np.sin(np.linspace(0, 20, n))
    got = core.square(330.0, n, core.SR, 0.1, width)
    want = core.square_numpy(330.0, n, core.SR, 0.1, width)
    np.testing.assert_allclose(got, want, rtol=0, atol=0)


@needs_native
@pytest.mark.parametrize("delay_s, feedback", [(0.003, 0.5), (0.375, 0.35), (1.0, 0.9)])
def test_feedback_delay_matches_numpy(delay_s, feedback):
    x = np.random.default_rng(3).standard_normal(30000)
    got = fx._delay_line(x, delay_s, feedback, core.SR)
    want = fx._delay_line_numpy(x, delay_s, feedback, core.SR)
    np.testing.assert_allclose(got, want, rtol=0, atol=0)


@needs_native
@pytest.mark.parametrize(
    "base, depth, rate, phase, feedback",
    [
        (18.0, 3.0, 0.6, 0.0, 0.0),
        (19.8, 3.0, 0.64, 1.1, 0.0),
        (3.0, 6.0, 0.25, 0.0, 0.5),
    ],
)
def test_mod_delay_matches_numpy(base, depth, rate, phase, feedback):
    x = np.random.default_rng(4).standard_normal(44100)
    got = fx._mod_delay(x, base, depth, rate, core.SR, phase, feedback)
    want = fx._mod_delay_numpy(x, base, depth, rate, core.SR, phase, feedback)
    # the delay time goes through sin(), taken in turns natively and in
    # radians by NumPy: ~1e-15 apart, times 44 samples per ms of depth,
    # times this white-noise input's steep slope between samples — ~2e-11
    # at worst, which is -210 dB
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-10)


@needs_native
@pytest.mark.parametrize("shape", [(1,), (7,), (5000,), (5000, 2), (4097, 3)])
def test_read_back_matches_numpy_bit_for_bit(shape):
    rng = np.random.default_rng(len(shape) + shape[0])
    x = rng.standard_normal(shape)
    n = shape[0]
    # fractional, whole, negative (the present), past the start
    d = np.concatenate([rng.uniform(-3, 40, n - n // 4), np.arange(n // 4) * 1.0])
    for delay in (d, 12.5, 0.0):
        got = fx._read_back(x, delay)
        want = fx._read_back_numpy(x, delay)
        assert got.shape == want.shape
        np.testing.assert_array_equal(got, want)


@needs_native
@pytest.mark.parametrize("n, sr", [(1, 44100), (999, 44100), (300_000, 48000)])
def test_sines_match_numpy(n, sr):
    got = fx._sines(n, sr, fx._WANDER)
    want = fx._sines_numpy(n, sr, fx._WANDER)
    # sin in turns natively, in radians by NumPy: the phase's last bit at
    # ~6 s of 1.61 Hz
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-12)


@needs_native
@pytest.mark.parametrize("feedback", [0.45, "lane", 0.95])
@pytest.mark.parametrize("time, wow", [(0.3, 0.3), (0.0001, 0.0), (0.004, 1.0)])
def test_tape_echo_taps_match_numpy(feedback, time, wow):
    n = 30_000
    x = np.random.default_rng(9).standard_normal((n, 2)) * 0.5
    fb = np.linspace(0.0, 0.9, n) if feedback == "lane" else feedback
    head = fx.tape_head(n, time, wow, core.SR)
    a, g = fx.tape_pole(0.4), 1.0 + 4.0 * 0.6
    got = synthlab_dsp().tape_echo(
        x, head, np.ascontiguousarray(core.as_array(fb, n)), a, g
    )
    want = fx._tape_echo_taps_numpy(x, head, fb, a, g)
    # tanh (`dsp::math::tanh`, a few ulp from NumPy's) inside the loop
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-13)


@needs_native
@pytest.mark.parametrize("speed", [0.0, 1.0, "lane"])
def test_rotor_matches_numpy(speed):
    n = 60_000
    band = np.random.default_rng(5).standard_normal(n)
    speed = np.linspace(0, 1, n) if speed == "lane" else speed
    for slow, fast, ramp, doppler, dip in (fx.ROTARY_HORN, fx.ROTARY_DRUM):
        rate = fx.rotor_rate(speed, n, slow, fast, ramp, core.SR)
        got = fx._rotor(band, rate, doppler, dip, core.SR)
        want = fx._rotor_numpy(band, rate, doppler, dip, core.SR)
        # the rotor's sine in turns vs radians, through the Doppler read
        # of white noise (as `_mod_delay`'s: -200 dB)
        np.testing.assert_allclose(got, want, rtol=0, atol=1e-10)


@needs_native
@pytest.mark.parametrize("q", [None, 99.5, 0.0, 50.0, 100.0])
@pytest.mark.parametrize(
    "shape, kind",
    [((0, 2), "noise"), ((1,), "noise"), ((2, 2), "noise"), ((1001,), "ties")]
    + [((n, 2), k) for n in (7, 200, 44100 + 13) for k in ("noise", "ties", "half")],
)
def test_scan_matches_numpy(shape, kind, q):
    """`core.scan` finds what its twin does: the same finite-ness and peak,
    the same percentile bit for bit (the two order statistics and NumPy's
    interpolation), the sum of squares to rounding."""
    rng = np.random.default_rng(shape[0])
    x = rng.standard_normal(shape) * 0.7
    if kind == "ties":
        x = np.round(x, 1)
    if kind == "half":
        x[: len(x) // 2] = 0.0
    got, want = core.scan(x, q), core.scan_numpy(x, q)
    assert (got.finite, got.peak, got.body) == (want.finite, want.peak, want.body)
    assert got.sumsq == pytest.approx(want.sumsq, rel=1e-12, abs=1e-300)


@needs_native
@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("at", [0, 13, -1])
def test_scan_finds_a_non_finite_sample_anywhere(bad, at):
    x = np.random.default_rng(2).standard_normal((1000, 2))
    x.reshape(-1)[at] = bad
    assert not core.scan(x).finite
    assert not core.scan(x, 99.5).finite
    assert not core.scan_numpy(x).finite


def synthlab_dsp():
    return _native._dsp


def test_cached_reverb_room_is_the_room_and_read_only():
    room = fx._room(1.7, 0.6, 0.35, 0.02, core.SR)
    np.testing.assert_array_equal(room, fx.reverb_ir(1.7, 0.6, 0.35, 0.02, core.SR))
    assert fx._room(1.7, 0.6, 0.35, 0.02, core.SR) is room
    with pytest.raises(ValueError):
        room[0, 0] = 1.0


@needs_native
@pytest.mark.parametrize("stereo", [False, True])
@pytest.mark.parametrize("drive", ["scalar", "curve"])
def test_soft_clip_matches_numpy(stereo, drive):
    rng = np.random.default_rng(5)
    n = 30000
    x = rng.standard_normal((n, 2) if stereo else n) * 1.5
    d = 2.7 if drive == "scalar" else np.linspace(0.0, 6.0, n)  # 0 hits the floor
    got = core.soft_clip(x, d)
    want = core.soft_clip_numpy(x, d)
    np.testing.assert_allclose(got, want, rtol=1e-14, atol=1e-15)


def test_soft_clip_zero_drive_is_identity():
    x = np.linspace(-2, 2, 100)
    assert core.soft_clip(x, 0.0) is x


@needs_native
@pytest.mark.parametrize(
    "voices, detune, spread", [(7, 0.18, 1.0), (5, 0.4, 0.6), (1, 0.3, 1.0)]
)
@pytest.mark.parametrize("freq", ["const", "vibrato"])
def test_supersaw_matches_numpy(voices, detune, spread, freq):
    n = 30000
    f = _freqs(n)[freq]
    got = core.supersaw(f, n, core.SR, voices, detune, spread, seed=3)
    want = core.supersaw_numpy(f, n, core.SR, voices, detune, spread, seed=3)
    np.testing.assert_allclose(got, want, rtol=0, atol=0)


@needs_native
@pytest.mark.parametrize("index_decay", [0.0, 0.4])
@pytest.mark.parametrize("args", ["const", "curves"])
def test_fm_matches_numpy(index_decay, args):
    n = 30000
    if args == "const":
        f, ratio, index = 180.0, 2.0, 3.0
    else:
        f = _freqs(n)["vibrato"]
        ratio = np.linspace(1.0, 3.5, n)
        index = np.linspace(0.0, 8.0, n)
    got = core.fm(f, n, core.SR, ratio, index, index_decay, 0.2)
    want = core.fm_numpy(f, n, core.SR, ratio, index, index_decay, 0.2)
    # sin taken in turns (math::sin_turns) vs np.sin in radians: a phase
    # of a few radians, rounded differently, is ~1e-15 apart
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-12)


@needs_native
@pytest.mark.parametrize("stereo", [False, True])
def test_dc_block_matches_numpy(stereo):
    rng = np.random.default_rng(6)
    n = 30000
    x = rng.standard_normal((n, 2) if stereo else n) + 0.4  # off centre
    got = core.dc_block(x)
    want = core.dc_block_numpy(x)
    # the same transposed-direct-form arithmetic scipy's lfilter runs
    np.testing.assert_array_equal(got, want)


@needs_native
@pytest.mark.parametrize("table", [*core.WAVETABLE_NAMES, "no-such-table"])
@pytest.mark.parametrize("freq", ["const", "vibrato", "short", "sweep"])
@pytest.mark.parametrize("pos", ["scalar", "curve", "short"])
def test_wavetable_matches_numpy(table, freq, pos):
    n = 44100 + 7
    # the sweep crosses every mip level, a block at a time
    f = {**_freqs(n), "sweep": np.geomspace(25.0, 15000.0, n)}[freq]
    p = {
        "scalar": 0.37,
        "curve": np.linspace(0.0, 1.0, n),
        "short": np.array([-0.2, 0.5, 1.3, 0.1]),  # resampled, then clipped
    }[pos]
    for phase0 in (0.0, 0.37):
        got = core.wavetable(f, n, core.SR, table, p, phase0)
        want = core.wavetable_numpy(f, n, core.SR, table, p, phase0)
        assert got.shape == want.shape == (n,)
        # the same read, the same arithmetic in the same order
        np.testing.assert_array_equal(got, want)


@needs_native
@pytest.mark.parametrize("n", [1, 63, 64, 65, 200])
def test_wavetable_matches_numpy_on_short_buffers(n):
    f = np.linspace(100.0, 9000.0, n)
    got = core.wavetable(f, n, core.SR, "vocal", np.linspace(1.0, 0.0, n))
    want = core.wavetable_numpy(f, n, core.SR, "vocal", np.linspace(1.0, 0.0, n))
    np.testing.assert_array_equal(got, want)


def _post_case(n, ch, seed=12):
    """A voice-shaped input and the curves a post chain reads."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, ch) if ch > 1 else n) * 0.6 + 0.2  # off centre
    return {
        "x": x,
        "cutoff": np.geomspace(90.0, 14000.0, n) * (1 + 0.2 * np.sin(np.arange(n))),
        "res": rng.uniform(0.0, 0.95, n),
        "drive": np.linspace(0.0, 5.0, n),  # 0 hits soft_clip's floor
        "gain": np.linspace(1.0, 0.0, n) ** 2,
    }


def _post_stages(c, n, kind, static, curves, sr=core.SR):
    """(the kernel's stages, the same chain as loose engine calls) for one
    filter `kind` ("ladder", a biquad kind, or "none")."""
    cutoff = 1234.5 if static else c["cutoff"]
    res = c["res"] if curves else 0.4
    drive = c["drive"] if curves else 2.2
    q = 0.707 + np.asarray(res) * 6

    def loose(x, dc, sweep, lfilt, clip):
        y = dc(x)
        if kind == "ladder":
            y = sweep(y, "lowpass", cutoff, q, lfilt)
            y = sweep(clip(y, drive), "lowpass", cutoff, 0.707, lfilt)
        elif kind != "none":
            y = sweep(y, kind, cutoff, q, lfilt)
        y = y * (c["gain"][:, None] if y.ndim == 2 else c["gain"])
        return clip(y, drive)

    stages = [("dc", core._dc_pole(sr=sr))]
    if kind == "ladder":
        stages += core._ladder_stages(n, cutoff, res, drive, sr)
    elif kind != "none":
        stages.append(core._biquad_stage(n, kind, cutoff, q, sr))
    stages.append(("gain", c["gain"]))
    stages += core._clip_stages(drive, n)
    return stages, loose


def _sweep_native(x, kind, cutoff, q, _lfilt):
    return core.biquad(x, kind, cutoff, q)


def _sweep_numpy(x, kind, cutoff, q, lfilt):
    plan = core._biquad_plan(len(x), kind, cutoff, q)
    if plan[0] == "lfilter":
        return lfilt(plan[1], plan[2], x, axis=0)
    return core._swept_biquad_numpy(x, kind, plan[2], plan[3], core.SR, 64)


@needs_native
@pytest.mark.parametrize(
    "kind", ["ladder", "lowpass", "highpass", "bandpass", "notch", "none"]
)
@pytest.mark.parametrize(
    "n, ch", [(1, 2), (63, 2), (64, 2), (65, 2), (9000, 2), (777, 1)]
)
@pytest.mark.parametrize("static", [False, True], ids=["swept", "static"])
@pytest.mark.parametrize("curves", [False, True], ids=["scalars", "curves"])
def test_post_chain_matches_its_stages_and_numpy(kind, n, ch, static, curves):
    """`synthlab_dsp.post` against the engine's own calls, one per stage
    (bit for bit: they are the same kernels), and against the NumPy twins
    of every stage (to rounding)."""
    import synthlab_dsp
    from scipy.signal import lfilter

    c = _post_case(n, ch)
    stages, loose = _post_stages(c, n, kind, static, curves)
    got = synthlab_dsp.post(c["x"], float(core.SR), stages)
    assert got.shape == c["x"].shape
    native = loose(c["x"], core.dc_block, _sweep_native, None, core.soft_clip)
    np.testing.assert_array_equal(got, native)
    twin = loose(
        c["x"], core.dc_block_numpy, _sweep_numpy, lfilter, core.soft_clip_numpy
    )
    # the swept filter's own bar (test_swept_svf_matches_numpy), through two
    # stages and a saturation
    np.testing.assert_allclose(got, twin, rtol=1e-8, atol=1e-9)


@needs_native
def test_post_chain_takes_an_empty_buffer_and_refuses_bad_stages():
    import synthlab_dsp

    post = synthlab_dsp.post
    empty = np.zeros((0, 2))
    stages = [("dc", 0.998), ("svf", "lowpass", np.zeros(0), np.ones(1), 64)]
    assert post(empty, 44100.0, [*stages, ("gain", np.zeros(0))]).shape == (0, 2)
    x = np.zeros((130, 2))
    for bad in (
        [("svf", "allpass", np.full(3, 500.0), np.ones(1), 64)],  # unknown kind
        [("svf", "lowpass", np.full(2, 500.0), np.ones(1), 64)],  # a block short
        [("svf", "lowpass", np.full(3, 500.0), np.ones(2), 64)],  # q per block
        [("gain", np.ones(129))],
        [("clip", np.ones(7))],
        [("lfilter", np.ones(4), np.ones(1))],
        [("reverb", 1.0)],
    ):
        with pytest.raises(ValueError):
            post(x, 44100.0, bad)


def _build_wheels():
    spec = importlib.util.spec_from_file_location(
        "build_wheels", Path(__file__).parents[1] / "tools" / "build_wheels.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_wheels_match_the_rust_sources():
    bw = _build_wheels()
    assert bw.recorded() == bw.source_hash(), (
        "crates/ changed since the wheels were built: run "
        "`uv run python tools/build_wheels.py` and commit crates/wheels/"
    )
    names = sorted(w.name for w in bw.WHEELS.glob("*.whl"))
    assert names == [
        "synthlab_dsp-0.1.0-cp313-abi3-win_amd64.whl",
        "synthlab_dsp-0.1.0-cp313-abi3-win_arm64.whl",
    ]


def test_uv_lock_pins_the_committed_wheels():
    # uv verifies these on install: a wheel rebuilt without re-locking
    # would fail `uv sync` on the other machines
    bw = _build_wheels()
    lock = (bw.ROOT / "uv.lock").read_text(encoding="utf-8")
    for w in bw.WHEELS.glob("*.whl"):
        digest = hashlib.sha256(w.read_bytes()).hexdigest()
        assert f'"{w.name}", hash = "sha256:{digest}"' in lock, w.name


@needs_native
def test_loaded_module_was_built_from_these_sources():
    import synthlab_dsp

    built = synthlab_dsp.build_info()["source"]
    assert built in (_build_wheels().source_hash(), "dev"), (
        f"the installed synthlab_dsp was built from {built}: "
        "`uv sync --all-extras --reinstall-package synthlab-dsp`"
    )


@needs_native
@pytest.mark.parametrize(
    "n, a, d, s, r, gate, curve",
    [
        (44100, 0.01, 0.1, 0.7, 0.2, None, 2.0),
        (44100, 0.3, 0.5, 0.4, 0.6, 0.5, 3.0),  # gate cuts the decay
        (22050, 1.0, 0.1, 0.5, 0.2, 0.1, 1.0),  # gate cuts the attack
        (22050, 0.01, 0.05, 0.8, 0.3, 0.5, 2.0),  # gate to the end: ponytail
        (4410, 0.0, 0.0, 1.0, 0.0, None, 2.0),  # zero-length segments
        (3, 0.01, 0.01, 0.5, 0.01, None, 2.0),  # tiny buffer
        # a gated release lasts the release time, then silence:
        (44100, 0.01, 0.1, 0.7, 0.02, 0.5, 2.0),  # shorter than what is left
        (44100, 0.01, 0.1, 0.7, 0.5, 0.5, 2.0),  # exactly what is left
        (44100, 0.01, 0.1, 0.7, 2.0, 0.5, 3.5),  # longer: fitted to the buffer
        (44100, 0.01, 0.1, 0.7, 0.3, 0.5, 1.0),  # linear
        (22050, 1.0, 0.1, 0.5, 0.05, 0.1, 2.7),  # released out of the attack
        (22050, 0.01, 1.0, 0.2, 0.05, 0.1, 2.0),  # ... and out of the decay
        (44100, 0.01, 0.1, 0.7, 0.0, 0.5, 2.0),  # no release at all: a cut
        (44100, 0.01, 0.1, 0.7, 1 / 44100, 0.5, 2.0),  # one sample of it
        (22050, 0.01, 0.1, 0.7, 0.2, 22049 / 44100, 2.0),  # one sample left
        (1, 0.01, 0.1, 0.7, 0.2, 0.5, 2.0),  # a one-sample buffer
        (1, 0.01, 0.1, 0.7, 0.2, None, 2.0),
    ],
)
def test_adsr_matches_numpy(n, a, d, s, r, gate, curve):
    got = core.adsr(n, a, d, s, r, gate, core.SR, curve)
    want = core.adsr_numpy(n, a, d, s, r, gate, core.SR, curve)
    np.testing.assert_allclose(got, want, rtol=1e-14, atol=1e-15)


def _assert_scipy_equal(got, want):
    """Bit for bit where it was measured (x86-64). On ARM64 scipy's own
    build rounds its filter and FFT loops differently (<= 8e-16 measured on
    Windows on ARM), so there the bar is 1e-12 — far under anything audible."""
    if platform.machine().lower() in ("amd64", "x86_64"):
        np.testing.assert_array_equal(got, want)
    else:
        np.testing.assert_allclose(got, want, rtol=0, atol=1e-12)


@needs_native
@pytest.mark.parametrize(
    "b, a",
    [
        ([0.2], [1.0]),  # gain only
        ([0.3], [1.0, -0.7]),  # one-pole (b shorter than a)
        ([1.0, -1.0], [1.0, -0.99]),  # dc blocker
        ([0.1, 0.2, 0.1], [1.0, -1.2, 0.5]),  # biquad
        ([0.2, 0.4, 0.2], [2.0, -2.4, 1.0]),  # a[0] != 1: normalised
    ],
)
@pytest.mark.parametrize("stereo", [False, True])
@pytest.mark.parametrize("with_zi", [False, True])
def test_lfilter_matches_scipy_bit_for_bit(b, a, stereo, with_zi):
    from scipy.signal import lfilter

    x = np.random.default_rng(8).standard_normal((5000, 2) if stereo else 5000)
    k = max(len(b), len(a)) - 1
    zi = None
    if with_zi and k:
        zi = np.random.default_rng(9).standard_normal((k, 2) if stereo else k)
    got = core._lfilter(b, a, x, zi=zi)
    want = (
        lfilter(b, a, x, axis=0) if zi is None else lfilter(b, a, x, axis=0, zi=zi)[0]
    )
    _assert_scipy_equal(got, want)


@needs_native
def test_comb_is_the_all_pole_lfilter_bit_for_bit():
    from scipy.signal import lfilter

    x = np.random.default_rng(10).standard_normal(20000)
    d, fb = 200, 0.8
    a = np.zeros(d + 1)
    a[0], a[d] = 1.0, -fb
    np.testing.assert_array_equal(fx._comb_mono(x, d, fb), lfilter([1.0], a, x))


@needs_native
@pytest.mark.parametrize("n, stages", [(1, 6), (64, 1), (1000, 4), (44100 + 13, 6)])
def test_allpass_sweep_matches_scipy_bit_for_bit(n, stages):
    """The phaser's cascade: one native call against the twin's scipy call
    a block a stage."""
    assert _native.kernel("allpass_sweep") is not None
    x = np.random.default_rng(12).standard_normal(n)
    centers = 1300.0 + 1100.0 * np.sin(np.arange(n) / 900.0)
    got = fx._allpass_sweep(x, centers, stages, core.SR)
    _assert_scipy_equal(got, fx._allpass_sweep_numpy(x, centers, stages, core.SR))


@needs_native
def test_phaser_is_its_numpy_twin_bit_for_bit(monkeypatch):
    x = np.random.default_rng(13).standard_normal((30000, 2))
    got = fx._phaser(x, rate=0.2, depth=0.8, mix=0.5)
    monkeypatch.setattr(fx, "_allpass_sweep", fx._allpass_sweep_numpy)
    _assert_scipy_equal(got, fx._phaser(x, rate=0.2, depth=0.8, mix=0.5))


@pytest.mark.parametrize("n", [1000, 44100 * 3 + 17])
def test_reverb_is_fftconvolve_bit_for_bit(n):
    from scipy.signal import fftconvolve

    st = np.random.default_rng(11).standard_normal((n, 2))
    got = fx._reverb(st, decay=1.8, mix=1.0, size=0.7)
    ir = fx._room(1.8, 0.7, 0.35, 0.02, core.SR)
    want = np.column_stack([fftconvolve(st[:, c], ir[:, c])[:n] for c in range(2)])
    _assert_scipy_equal(got, want)
