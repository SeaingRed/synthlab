"""
synthlab.core — the DSP foundation.

Everything here is plain NumPy. Signals are float32/float64 arrays in the
range [-1, 1]. Mono signals are shape (n,), stereo signals are shape (n, 2).

The design rule: every parameter that can be a constant can also be an
array of per-sample values. That is what makes modulation (LFOs, envelopes,
glide, vibrato) work everywhere for free.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import cache
from typing import NamedTuple

import numpy as np
from scipy.signal import lfilter, lfilter_zi

from ._native import kernel

SR = 44100  # global sample rate


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


def n_samples(seconds: float, sr: int = SR) -> int:
    return max(1, int(round(seconds * sr)))


def time_axis(n: int, sr: int = SR) -> np.ndarray:
    return np.arange(n, dtype=np.float64) / sr


def as_array(value, n: int) -> np.ndarray:
    """Accept a scalar or an array and always return an array of length n."""
    arr = np.asarray(value, dtype=np.float64)
    if arr.ndim == 0:
        return np.full(n, float(arr))
    if len(arr) == n:
        return arr
    # resample by linear interpolation so control-rate signals can be used
    return np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(arr)), arr)


def note_to_hz(note: str | float | int) -> float:
    """'A4' -> 440.0, 'C#3' -> 138.59, 69 -> 440.0 (MIDI numbers work too)."""
    if isinstance(note, (int, float)):
        return 440.0 * 2 ** ((float(note) - 69) / 12)
    name = note.strip()
    table = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    semis = table[name[0].upper()]
    i = 1
    while i < len(name) and name[i] in "#b":
        semis += 1 if name[i] == "#" else -1
        i += 1
    octave = int(name[i:])
    midi = 12 * (octave + 1) + semis
    return 440.0 * 2 ** ((midi - 69) / 12)


def db(gain_db: float) -> float:
    """Decibels to linear gain. db(-6) -> 0.501"""
    return float(10 ** (gain_db / 20))


class Scan(NamedTuple):
    """What one pass over a buffer found (`scan`). `peak`, `sumsq` and
    `body` mean nothing when `finite` is false."""

    finite: bool
    peak: float  # the largest |x|
    sumsq: float  # the sum of x², to rounding
    body: float  # the q-th percentile of the frames' peaks (0 without q)


def scan(x: np.ndarray, q: float | None = None) -> Scan:
    """Whether every sample of `x` ((n,) or (n, ch)) is finite, its peak
    and its sum of squares — in one pass natively, where the render's NaN
    check, its meter levels and a section's levels were each their own —
    and with `q` the `q`th percentile of each frame's peak over its
    channels, as `np.percentile` takes it (exactly: the same two order
    statistics, the same interpolation)."""
    native = kernel("scan")
    if native is None or np.ndim(x) not in (1, 2):
        return scan_numpy(x, q)
    xs = np.ascontiguousarray(x, dtype=np.float64)
    return Scan(*native(xs, None if q is None else float(q)))


def scan_numpy(x: np.ndarray, q: float | None = None) -> Scan:
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return Scan(True, 0.0, 0.0, 0.0)
    if not np.all(np.isfinite(x)):
        return Scan(False, float("nan"), float("nan"), 0.0)
    peak = float(np.max(np.abs(x)))
    sumsq = float(np.sum(np.square(x)))
    body = 0.0
    if q is not None:
        mag = np.max(np.abs(x), axis=1) if x.ndim == 2 else np.abs(x)
        body = float(np.percentile(mag, q))
    return Scan(True, peak, sumsq, body)


def soft_clip(x: np.ndarray, drive: float = 1.0) -> np.ndarray:
    """Musical saturation. Adds harmonics instead of hard digital clipping.
    `drive` may be a scalar or a per-sample array (drawn automation)."""
    d = np.asarray(drive, dtype=np.float64)
    native = kernel("soft_clip")
    if native is not None and d.ndim <= 1 and np.ndim(x) in (1, 2):
        if d.ndim == 0 and not float(d) > 0:
            return x
        if d.ndim == 0 or len(d) == len(x):
            xs = np.ascontiguousarray(x, dtype=np.float64)
            return native(xs, _drive_series(d))
    return soft_clip_numpy(x, drive)


def _drive_series(d: np.ndarray) -> np.ndarray:
    """A drive as the native `soft_clip` takes it: one value, or one per
    frame. The kernel reads one value as a constant, which it does not
    floor — so a one-frame curve is floored here, as `soft_clip_numpy`
    floors every curve (a drawn drive of 0 on a one-sample note divided by
    tanh(0))."""
    if d.ndim == 1 and len(d) == 1:
        d = np.maximum(d, 1e-6)
    return np.ascontiguousarray(d.reshape(-1))


def soft_clip_numpy(x: np.ndarray, drive: float = 1.0) -> np.ndarray:
    d = np.asarray(drive, dtype=np.float64)
    if d.ndim == 0:
        dv = float(d)
        return np.tanh(x * dv) / np.tanh(dv) if dv > 0 else x
    d = np.maximum(d, 1e-6)
    if x.ndim == 2 and d.ndim == 1:
        d = d[:, None]
    return np.tanh(x * d) / np.tanh(d)


def _lfilter(b, a, x: np.ndarray, zi=None) -> np.ndarray:
    """scipy's `lfilter` along axis 0, output only — natively for the
    short filters the engine runs (up to order 2), bit-identical to scipy's
    loop; anything else goes to scipy. `zi` as scipy takes it for axis 0:
    (order,) for a mono x, (order, ch) for a stereo one."""
    b = np.atleast_1d(np.asarray(b, dtype=np.float64))
    a = np.atleast_1d(np.asarray(a, dtype=np.float64))
    x = np.asarray(x)
    native = kernel("lfilter")
    k = max(len(b), len(a))
    if (
        native is not None
        and x.ndim in (1, 2)
        and len(x)
        and 1 <= k <= 3
        and a[0] != 0
        and x.dtype == np.float64
    ):
        if zi is None:
            z = np.empty(0)
        else:
            z = np.asarray(zi, dtype=np.float64).reshape(k - 1, -1).T  # channel-major
        z = np.ascontiguousarray(z).reshape(-1)
        return native(b, a, np.ascontiguousarray(x), z)
    if zi is None:
        return lfilter(b, a, x, axis=0)
    return lfilter(b, a, x, axis=0, zi=zi)[0]


# ----------------------------------------------------------------------------
# oscillators  (band-limited with PolyBLEP — no harsh digital aliasing)
# ----------------------------------------------------------------------------


def _phase(freq, n: int, sr: int = SR, phase0: float = 0.0) -> np.ndarray:
    f = as_array(freq, n)
    c = np.cumsum(f) / sr + phase0
    # c - floor(c) is `c % 1.0` for every finite c, at a fraction of the cost
    # (numpy's float mod does a sign fix-up per element)
    return c - np.floor(c)


def _polyblep(t: np.ndarray, dt: np.ndarray) -> np.ndarray:
    """Polynomial band-limited step — smooths the discontinuity in saw/square."""
    out = np.zeros_like(t)
    m1 = t < dt
    x = t[m1] / np.maximum(dt[m1], 1e-12)
    out[m1] = x + x - x * x - 1.0
    m2 = t > (1.0 - dt)
    x = (t[m2] - 1.0) / np.maximum(dt[m2], 1e-12)
    out[m2] = x * x + x + x + 1.0
    return out


def _series(value, n: int) -> np.ndarray:
    """A native kernel's per-sample parameter: one value, or `n` of them
    (`as_array`'s resampling included), contiguous float64."""
    arr = np.asarray(value, dtype=np.float64)
    if arr.ndim == 0:
        return arr.reshape(1)
    return np.ascontiguousarray(as_array(arr, n))


def _native_osc(shape: str, freq, n: int, sr: int, phase0: float, width=0.5):
    """The native one-pass oscillator, or None to take the NumPy path."""
    osc = kernel("osc")
    if osc is None:
        return None
    return osc(shape, _series(freq, n), _series(width, n), n, float(sr), float(phase0))


def sine(freq, n: int, sr: int = SR, phase0: float = 0.0) -> np.ndarray:
    y = _native_osc("sine", freq, n, sr, phase0)
    return y if y is not None else sine_numpy(freq, n, sr, phase0)


def sine_numpy(freq, n: int, sr: int = SR, phase0: float = 0.0) -> np.ndarray:
    return np.sin(2 * np.pi * _phase(freq, n, sr, phase0))


def saw(freq, n: int, sr: int = SR, phase0: float = 0.0) -> np.ndarray:
    y = _native_osc("saw", freq, n, sr, phase0)
    return y if y is not None else saw_numpy(freq, n, sr, phase0)


def saw_numpy(freq, n: int, sr: int = SR, phase0: float = 0.0) -> np.ndarray:
    t = _phase(freq, n, sr, phase0)
    dt = as_array(freq, n) / sr
    return (2.0 * t - 1.0) - _polyblep(t, dt)


def square(
    freq, n: int, sr: int = SR, phase0: float = 0.0, width: float = 0.5
) -> np.ndarray:
    y = _native_osc("square", freq, n, sr, phase0, width)
    return y if y is not None else square_numpy(freq, n, sr, phase0, width)


def square_numpy(
    freq, n: int, sr: int = SR, phase0: float = 0.0, width: float = 0.5
) -> np.ndarray:
    t = _phase(freq, n, sr, phase0)
    dt = as_array(freq, n) / sr
    y = np.where(t < width, 1.0, -1.0)
    y += _polyblep(t, dt)
    y -= _polyblep((t + (1.0 - width)) % 1.0, dt)
    return y


def triangle(freq, n: int, sr: int = SR, phase0: float = 0.0) -> np.ndarray:
    y = _native_osc("triangle", freq, n, sr, phase0)
    return y if y is not None else triangle_numpy(freq, n, sr, phase0)


def triangle_numpy(freq, n: int, sr: int = SR, phase0: float = 0.0) -> np.ndarray:
    t = _phase(freq, n, sr, phase0)
    return 4.0 * np.abs(t - 0.5) - 1.0


# Pink and brown noise are white noise through a fixed causal filter, so the
# live voice (static/voice-worklet.js) runs the same process sample by
# sample. They used to be shaped over the whole buffer by FFT and scaled by
# the buffer's own peak: the level then depended on the note's length (brown
# moved ~10 dB between a 0.3 s and a 4 s note, pink ~1.7 dB) and nothing
# realtime could reproduce it. Pink is Paul Kellet's refined -3 dB/oct
# filter (parallel one-poles + a direct and a one-sample term), brown a
# leaky integrator with its corner at 20 Hz; the gains put both at the level
# the old code gave a 1.3 s note, band for band.
PINK_POLES = (0.99886, 0.99332, 0.96900, 0.86650, 0.55000, -0.7616)
PINK_TAPS = (0.0555179, 0.0750759, 0.1538520, 0.3104856, 0.5329522, -0.0168980)
PINK_DIRECT, PINK_LAST = 0.5362, 0.115926
PINK_GAIN, BROWN_GAIN, BROWN_CORNER = 0.0703, 0.00559, 20.0


def colour_noise(w: np.ndarray, color: str, sr: int = SR) -> np.ndarray:
    """White noise `w` -> "pink" / "brown" (causal, zero initial state);
    anything else passes through. Unscaled: `noise` applies the level."""
    if color == "pink":
        y = PINK_DIRECT * w
        y[1:] += PINK_LAST * w[:-1]
        for p, g in zip(PINK_POLES, PINK_TAPS, strict=True):
            y += _lfilter([g], [1.0, -p], w)
        return y * PINK_GAIN
    if color == "brown":
        r = float(np.exp(-2 * np.pi * BROWN_CORNER / sr))
        return _lfilter([BROWN_GAIN], [1.0, -r], w)
    return w


def noise(n: int, color: str = "white", seed: int | None = None) -> np.ndarray:
    rng = np.random.default_rng(seed)
    w = rng.standard_normal(n)
    if color in ("white", "pink", "brown"):
        return colour_noise(w, color) * 0.3
    # "blue" (no Patch reaches it: noise_color is white/pink/brown) keeps
    # the whole-buffer FFT shaping
    spec = np.fft.rfft(w)
    f = np.fft.rfftfreq(n, 1 / SR)
    f[0] = f[1] if len(f) > 1 else 1.0
    if color == "blue":
        spec *= np.sqrt(f)
    out = np.fft.irfft(spec, n)
    peak = np.max(np.abs(out)) or 1.0
    return out / peak * 0.3


def supersaw(
    freq,
    n: int,
    sr: int = SR,
    voices: int = 7,
    detune: float = 0.18,
    spread: float = 1.0,
    seed: int = 0,
) -> np.ndarray:
    """The big trance/EDM sound: many slightly detuned saws stacked."""
    native = kernel("supersaw")
    if native is None or voices < 1:
        return supersaw_numpy(freq, n, sr, voices, detune, spread, seed)
    # the same detune ratios and seeded start phases the NumPy loop draws
    rng = np.random.default_rng(seed)
    offs = [(i - (voices - 1) / 2) / max(1, (voices - 1) / 2) for i in range(voices)]
    ratios = np.array([2 ** (o * detune * 100 * spread / 1200) for o in offs])
    phases = np.array([rng.random() for _ in range(voices)])
    return native(_series(freq, n), ratios, phases, n, float(sr))


def supersaw_numpy(
    freq,
    n: int,
    sr: int = SR,
    voices: int = 7,
    detune: float = 0.18,
    spread: float = 1.0,
    seed: int = 0,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    f = as_array(freq, n)
    out = np.zeros(n)
    for i in range(voices):
        offset = (i - (voices - 1) / 2) / max(1, (voices - 1) / 2)
        cents = offset * detune * 100 * spread
        out += saw(f * 2 ** (cents / 1200), n, sr, phase0=rng.random())
    return out / np.sqrt(voices)


def fm(
    freq,
    n: int,
    sr: int = SR,
    ratio: float = 2.0,
    index: float = 2.0,
    index_decay: float = 0.0,
    phase0: float = 0.0,
) -> np.ndarray:
    """Two-operator FM: sin(φc + index·sin(φm)), modulator at freq·ratio.
    `index_decay` > 0 fades the index over that many seconds — bright
    attack, mellow tail (electric pianos, bells). `index`/`ratio` may be
    per-sample arrays."""
    native = kernel("fm")
    if native is None:
        return fm_numpy(freq, n, sr, ratio, index, index_decay, phase0)
    return native(
        _series(freq, n),
        _series(ratio, n),
        _series(index, n),
        float(index_decay),
        n,
        float(sr),
        float(phase0),
    )


def fm_numpy(
    freq,
    n: int,
    sr: int = SR,
    ratio: float = 2.0,
    index: float = 2.0,
    index_decay: float = 0.0,
    phase0: float = 0.0,
) -> np.ndarray:
    carrier = _phase(freq, n, sr, phase0)
    modulator = _phase(as_array(freq, n) * as_array(ratio, n), n, sr)
    idx = as_array(index, n)
    if index_decay > 0:
        idx = idx * np.exp(-np.arange(n) / (index_decay * sr))
    return np.sin(2 * np.pi * carrier + idx * np.sin(2 * np.pi * modulator))


# 4-operator FM (shape "fm4"): each algorithm is {operator: the operators
# modulating it} and the carriers summed to the output. Operator 4 is the
# top of every algorithm (nothing modulates it) and the one with feedback.
FM4_ALGOS: dict[str, tuple[dict[int, tuple[int, ...]], tuple[int, ...]]] = {
    "stack": ({3: (4,), 2: (3,), 1: (2,)}, (1,)),  # 4 -> 3 -> 2 -> 1
    "pairs": ({1: (2,), 3: (4,)}, (1, 3)),  # (2 -> 1) + (4 -> 3)
    "three": ({1: (2, 3, 4)}, (1,)),  # 2 + 3 + 4 -> 1
    "pair_plus": ({1: (2,)}, (1, 3, 4)),  # 2 -> 1, with 3 and 4 as carriers
    "additive": ({}, (1, 2, 3, 4)),  # four sines: drawbars
}
FM4_ALGO_NAMES = tuple(FM4_ALGOS)
FM4_INDEX = 6.0  # a modulator at level 1 bends its target by 6 radians
FM4_FEEDBACK = 1.5  # radians of operator 4's own output at feedback 1


def _fm4_feedback(arg: np.ndarray, beta: float) -> np.ndarray:
    """sin(arg + beta * the mean of the last two outputs), sample by sample
    (the classic two-sample feedback average, which keeps it from
    chattering). Operator 4 is never modulated, so this is its whole loop;
    the other three stay vectorised."""
    out = []
    y1 = y2 = 0.0
    for a in arg.tolist():
        y = math.sin(a + beta * (y1 + y2) * 0.5)
        out.append(y)
        y2, y1 = y1, y
    return np.asarray(out)


def fm4(
    freq,
    n: int,
    sr: int = SR,
    algo: str = "stack",
    ratios=(1.0, 2.0, 1.0, 1.0),
    levels=(1.0, 0.3, 0.0, 0.0),
    decays=(0.0, 0.0, 0.0, 0.0),
    feedback: float = 0.0,
    phase0: float = 0.0,
) -> np.ndarray:
    """Four sine operators in one of `FM4_ALGOS`. Operator k runs at freq x
    ratios[k]; its level decays over decays[k] seconds (0 holds it) — a
    modulator that fades is an e-piano's tine, a bell's strike, a mallet.
    A modulator's output (level x its sine) bends its target's phase by
    `FM4_INDEX` radians per unit; the carriers are averaged. `feedback`
    (0..1) feeds operator 4 back into itself. `phase0` starts operator 1
    (the oscillator's own start phase, as `fm`'s carrier). Scalars, but for
    a level a route moves (per sample)."""
    mods, carriers = FM4_ALGOS.get(str(algo), FM4_ALGOS["stack"])
    f = as_array(freq, n)
    t = np.arange(n, dtype=np.float64)
    out: dict[int, np.ndarray] = {}
    for k in (4, 3, 2, 1):
        arg = 2 * np.pi * _phase(f * float(ratios[k - 1]), n, sr, phase0 * (k == 1))
        for m in mods.get(k, ()):
            arg = arg + FM4_INDEX * out[m]
        beta = float(feedback) * FM4_FEEDBACK if k == 4 else 0.0
        s = _fm4_feedback(arg, beta) if beta > 0 else np.sin(arg)
        d = float(decays[k - 1])
        # a level is a scalar, or per sample when a route moves it (the mod
        # matrix reaches the operators)
        lvl = np.asarray(levels[k - 1], dtype=np.float64)
        lvl = float(lvl) if lvl.ndim == 0 else as_array(lvl, n)
        out[k] = lvl * np.exp(t * (-1.0 / (d * sr))) * s if d > 0 else lvl * s
    total = out[carriers[0]]
    for c in carriers[1:]:
        total = total + out[c]
    return total / len(carriers)


# Hard sync: oscillator 2 restarts its cycle whenever a master phase at
# oscillator 1's pitch wraps. The shapes it applies to (the others ignore it).
SYNC_SHAPES = ("saw", "square", "triangle", "sine")


def sync(
    shape: str,
    master,
    freq,
    n: int,
    sr: int = SR,
    width=0.5,
    phase0: float = 0.0,
    master0: float = 0.0,
    naive: bool = False,
) -> np.ndarray:
    """`shape` at `freq`, hard-synced to a phase running at `master` (Hz,
    both per-sample arrays or scalars), band-limited.

    Every step in the slave's wave — its own wraps (a saw's fall, a
    square's two edges) and each reset, of whatever height the reset
    happens to cut it at — is an event at a fractional time with a jump J;
    the polyBLEP residual is spread over the two samples around it
    (+J/2 x^2 on the one before, -J/2 (1-x)^2 on the one after, x the
    fraction of a sample since the event). The square is a saw less a saw
    `width` behind it, so its edges are two saws' wraps. The sample before
    an event is only known once the event is, so the output runs one
    sample late — the live voice (which cannot reach back into a block it
    has handed on) holds one sample the same way. `naive=True` is the
    plain reset (no residuals), for measuring what the residuals buy.

    Vectorised between resets (a segment's phase is a cumulative sum from
    where the reset left it); the resets — a few hundred a second — are a
    loop."""
    dm = as_array(master, n) / sr
    ds = np.maximum(as_array(freq, n) / sr, 1e-12)
    w = as_array(width, n) if shape == "square" else np.full(n, 0.5)
    cm = master0 + np.cumsum(dm)
    fl = np.floor(cm)
    reset = fl > np.concatenate(([np.floor(master0)], fl[:-1]))
    xr = np.clip((cm - fl) / np.maximum(dm, 1e-12), 0.0, 1.0)
    rs = np.flatnonzero(reset)
    cs = np.cumsum(ds)
    off = np.empty(len(rs) + 1)
    off[0] = phase0
    off[1:] = ds[rs] * xr[rs] - cs[rs]
    u = off[np.cumsum(reset)] + cs  # each segment's unwrapped phase
    prev = np.concatenate(([phase0], u[:-1]))
    t = u - np.floor(u)
    now = np.zeros(n)
    pre = np.zeros(n)

    def event(j, x, jump):
        x = min(1.0, max(0.0, x))
        now[j] -= jump / 2 * (1 - x) ** 2
        pre[j] += jump / 2 * x * x

    if not naive and shape in ("saw", "square"):
        # the slave's own wraps, between resets: saw falls 2, square rises 2
        wrap = (np.floor(u) > np.floor(prev)) & ~reset
        x = t / ds
        j_wrap = -2.0 if shape == "saw" else 2.0
        now[wrap] -= j_wrap / 2 * (1 - np.clip(x[wrap], 0, 1)) ** 2
        pre[wrap] += j_wrap / 2 * np.clip(x[wrap], 0, 1) ** 2
        if shape == "square":  # the falling edge: phase crossing the width
            b = u + 1 - w
            edge = (np.floor(b) > np.floor(prev + 1 - w)) & ~reset
            xb = np.clip((b - np.floor(b)) / ds, 0, 1)
            now[edge] += (1 - xb[edge]) ** 2
            pre[edge] -= xb[edge] ** 2
    for r in rs.tolist():
        x, d, wr = float(xr[r]), float(ds[r]), float(w[r])
        tp = float(prev[r] - np.floor(prev[r]))
        tb = tp + d * (1 - x)  # the slave just before the reset
        tbw = tb - 1 if tb >= 1 else tb
        after = d * x  # and just after it
        if naive:
            continue
        if shape in ("saw", "square") and tb >= 1:  # it wrapped first
            event(r, x + (tb - 1) / d, -2.0 if shape == "saw" else 2.0)
        if shape == "saw":
            event(r, x, -2.0 * tbw)
        elif shape == "square":
            if np.floor(tp + 1 - wr) < np.floor(tb + 1 - wr):
                event(r, x + (tb + 1 - wr - np.floor(tb + 1 - wr)) / d, -2.0)
            jb = 2 * ((1 - wr) % 1.0 - (tbw + 1 - wr) % 1.0)
            event(r, x, jb + 2.0 * tbw)
            if after >= wr:
                event(r, (after - wr) / d, -2.0)
        else:
            event(r, x, _naive(shape, 0.0, wr) - _naive(shape, tbw, wr))
    y = _naive(shape, t, w) + now
    return np.concatenate(([0.0], y[:-1])) + pre


def _naive(shape: str, t, w):
    """The four syncable shapes with no band-limiting, at phase `t`."""
    if shape == "saw":
        return 2.0 * t - 1.0
    if shape == "square":
        return np.where(t < w, 1.0, -1.0) if np.ndim(t) else (1.0 if t < w else -1.0)
    if shape == "triangle":
        return 4.0 * np.abs(t - 0.5) - 1.0
    return np.sin(2 * np.pi * t)


# --- phase warp --------------------------------------------------------------
# An oscillator's phase bent before the shape reads it. A bent phase puts
# corners and steps in the wave that PolyBLEP never planned for, so the
# warped oscillator runs at WARP_OS x the rate — its own wrap still
# polyBLEP'd there, with the step the warp leaves at the cycle's end — and
# comes back down through WARP_TAPS, a minimum-phase FIR (about a sample of
# delay at the output rate, so a warped oscillator beside a plain one does
# not comb). Every mode is + - x / and floor on the phase and the amount:
# the live voice computes the same numbers.
WARP_MODES = (
    "off",
    "bend_pos",
    "bend_neg",
    "bend_both",
    "pd",
    "mirror",
    "asym",
    "quantize",
    "pwm",
)
# the shapes a warp bends; supersaw, fm, fm4, pluck, noise and the sampler
# ignore it (a synced osc2 too: sync already restarts its phase)
WARP_SHAPES = ("sine", "saw", "square", "triangle", "wavetable")
WARP_OS = 4
WARP_BEND = 15.0  # a bend's slope ratio at amount 1 is 1 + WARP_BEND
WARP_RING = 64  # the live voice's history per oscillator (>= the taps)


def _warp_taps() -> np.ndarray:
    """The decimator: a 95-tap Kaiser lowpass at 20.5 kHz on the WARP_OS x
    rate, made minimum-phase (48 taps), unity at DC."""
    from scipy.signal import firwin, minimum_phase

    lin = firwin(95, 20500.0, fs=SR * WARP_OS, window=("kaiser", 8.0))
    h = minimum_phase(lin, method="homomorphic")
    return h / h.sum()


WARP_TAPS = _warp_taps()


def _bend(t, k):
    """t lingers at 0 and rushes into 1 (slope 1/k there, k at the end)."""
    return t / (k - (k - 1.0) * t)


def warp_phase(mode: str, t, a):
    """The warped phase of `t` (0..1) at amount `a` (0..1, per sample or one)."""
    a = np.clip(a, 0.0, 1.0)
    k = 1.0 + WARP_BEND * a
    if mode == "bend_pos":  # toward the end of the cycle
        return _bend(t, k)
    if mode == "bend_neg":  # toward its start
        return k * t / (1.0 + (k - 1.0) * t)
    if mode == "bend_both":  # each half bent toward the middle
        lo, hi = 2.0 * t, 2.0 * t - 1.0
        return np.where(
            t < 0.5, 0.5 * _bend(lo, k), 0.5 + 0.5 * (k * hi / (1.0 + (k - 1.0) * hi))
        )
    if mode == "pd":  # two straight segments: half the cycle by d
        d = 0.5 - 0.49 * a
        return np.where(t < d, 0.5 * t / d, 0.5 + 0.5 * (t - d) / (1.0 - d))
    if mode == "mirror":  # forward to c, then back
        c = 1.0 - 0.5 * a
        return np.where(t < c, t / c, (1.0 - t) / np.maximum(1.0 - c, 1e-9))
    if mode == "asym":  # the first half bent, the second as it was
        return np.where(t < 0.5, 0.5 * _bend(2.0 * t, k), t)
    if mode == "quantize":  # 256 steps at 0 down to 2 at 1
        q = 1.0 - a
        steps = 2.0 + np.floor(254.0 * q * q * q)
        return np.floor(t * steps) / steps
    if mode == "pwm":  # the whole cycle in d of it, then held
        return np.minimum(t / (1.0 - 0.95 * a), 1.0)
    return t


def warp_end(mode: str, a):
    """Where the warped phase stands just before the cycle wraps: the
    step left at the wrap is shape(0) - shape(this)."""
    a = np.clip(a, 0.0, 1.0)
    if mode == "mirror":
        return np.where(a > 0, 0.0, 1.0)
    if mode == "quantize":
        q = 1.0 - a
        steps = 2.0 + np.floor(254.0 * q * q * q)
        return (steps - 1.0) / steps
    return np.ones_like(a, dtype=np.float64)


def warp_slope(mode: str, a):
    """The steepest the warp runs its phase (x the plain rate), at most
    1 + WARP_BEND: a warped wavetable takes the mip that many times
    darker, so what the warp crowds into a cycle stays under the
    oversampled Nyquist (quantize's steps are the FIR's)."""
    a = np.clip(a, 0.0, 1.0)
    if mode in ("bend_pos", "bend_neg", "bend_both", "asym"):
        s = 1.0 + WARP_BEND * a
    elif mode == "pd":
        s = 0.5 / (0.5 - 0.49 * a)
    elif mode == "mirror":
        s = 1.0 / np.maximum(0.5 * a, 1e-9)
    elif mode == "pwm":
        s = 1.0 / (1.0 - 0.95 * a)
    else:
        s = np.ones_like(a, dtype=np.float64)
    return np.minimum(np.maximum(s, 1.0), 1.0 + WARP_BEND)


def warped(
    shape: str,
    freq,
    n: int,
    sr: int = SR,
    mode: str = "off",
    amount=0.0,
    width=0.5,
    table="basic",
    pos=0.0,
    phase0: float = 0.0,
    naive: bool = False,
) -> np.ndarray:
    """`shape` (one of WARP_SHAPES) with its phase warped by `mode` at
    `amount` (per sample or one), oversampled WARP_OS x and decimated
    through WARP_TAPS. The phase runs at WARP_OS x the rate (each output
    sample's frequency held over its sub-samples, so sub-sample WARP_OS - 1
    is the plain oscillator's phase), the shape reads the warped phase, the
    cycle's own wrap is polyBLEP'd with the step the warp leaves there, and
    output sample j is the FIR over sub-samples up to WARP_OS j + WARP_OS - 1
    (zeros before the note). `naive=True` is the warp at the output rate
    with no band-limiting, for measuring what this buys."""
    os_ = 1 if naive else WARP_OS
    f = as_array(freq, n)
    d = np.repeat(f / (sr * os_), os_)
    t = np.cumsum(d) + phase0
    t -= np.floor(t)
    a = np.repeat(np.clip(as_array(amount, n), 0.0, 1.0), os_)
    u = warp_phase(mode, t, a)
    if shape == "wavetable":
        stack = _wt_stack(table)
        nf = stack.shape[1]
        # the mip per 64-sample block of output from its highest pitch, at
        # the oversampled rate (the FIR takes what the warp adds above)
        # and the warp's steepest slope over the block's amounts
        nb = -(-n // _WT_MIP_BLOCK)
        pad = (0, nb * _WT_MIP_BLOCK - n)
        peak = np.pad(np.abs(f), pad, mode="edge").reshape(nb, -1).max(axis=1)
        top = np.pad(as_array(amount, n), pad, mode="edge").reshape(nb, -1).max(axis=1)
        peak = peak * warp_slope(mode, top)
        mip = np.repeat(_wt_mip_index(peak, sr * os_), _WT_MIP_BLOCK)[:n]
        mip = np.repeat(mip, os_)
        fp = np.repeat(np.clip(as_array(pos, n), 0.0, 1.0) * (nf - 1), os_)

        def read(x, sel=slice(None)):
            return _wt_read(stack, mip[sel], fp[sel], x)

    elif shape == "square":
        w = np.repeat(as_array(width, n), os_)

        def read(x, sel=slice(None)):
            return np.where(x < w[sel], 1.0, -1.0)

    else:

        def read(x, sel=slice(None)):
            return _naive(shape, x, 0.5)

    y = read(u)
    if naive:
        return y
    edge = np.flatnonzero((t < d) | (t > 1.0 - d))
    if len(edge):
        jump = read(np.zeros(len(edge)), edge) - read(warp_end(mode, a[edge]), edge)
        y[edge] += jump / 2 * _polyblep(t[edge], d[edge])
    from scipy.signal import upfirdn

    return upfirdn(WARP_TAPS, np.concatenate(([0.0], y)), down=os_)[1 : n + 1]


def pluck(
    freq,
    n: int,
    sr: int = SR,
    damping: float = 0.5,
    bright: float = 0.9,
    seed: int | None = 0,
) -> np.ndarray:
    """Karplus-Strong string: a noise burst circulating in a delay line of
    `sr/freq` samples with a lowpass in the loop. Pitch is fixed per note
    (the first value of an array `freq`) — a delay line can't follow a
    curve. `damping` 0..1 = how fast the highs die; `bright` 0..1 = how
    much of the initial burst's top end is kept."""
    f0 = float(np.asarray(as_array(freq, n)).ravel()[0])
    f0 = max(20.0, min(f0, sr / 4))
    period = sr / f0
    length = int(np.floor(period))
    frac = period - length  # fractional delay via linear interpolation
    rng = np.random.default_rng(seed)
    burst = rng.uniform(-1, 1, length + 1)
    # initial burst lowpass: bright 1 -> raw noise, 0 -> heavily smoothed
    a = 1.0 - 0.97 * (1.0 - bright)
    for i in range(1, len(burst)):
        burst[i] = a * burst[i] + (1 - a) * burst[i - 1]
    burst -= burst.mean()  # the loop filter passes DC: a biased burst would never decay
    # One period per block (vectorised KS): the fractional tap reads the
    # next sample, the loop filter averages with the previous one. The
    # wrap sample of each block uses the previous period's value, which is
    # the classic block-KS approximation (inaudible, ~100x faster than a
    # per-sample Python loop).
    buf = burst[:length].copy()
    nxt = burst[1:][:length]
    d = 0.5 * damping  # averaging strength -> how fast highs die
    loss = 0.996 - 0.03 * damping  # a little broadband loss keeps it stable
    blocks = []
    total = 0
    prev_last = buf[-1]
    while total < n:
        y = buf * (1 - frac) + nxt * frac
        blocks.append(y)
        total += length
        prev = np.concatenate(([prev_last], y[:-1]))
        prev_last = y[-1]
        buf = loss * ((1 - d) * y + d * prev)
        nxt = np.concatenate((buf[1:], buf[:1]))
    out = np.concatenate(blocks)[:n]
    # Normalised by the first period's peak, which is known the moment the
    # string is plucked (the live voice needs it then). It is the whole
    # note's peak too, the number this used to divide by: every later sample
    # is a convex mix of earlier ones times loss < 1 (checked over 5120
    # pitch/seed/damping/brightness cases — none exceeded it).
    peak = float(np.max(np.abs(blocks[0]))) or 1.0
    return out / peak * 0.8


WAVEFORMS: dict[str, Callable[..., np.ndarray]] = {
    "sine": sine,
    "saw": saw,
    "square": square,
    "triangle": triangle,
    "supersaw": supersaw,
}


# Vowel formants: (frequency Hz, relative amplitude) for F1, F2, F3.
# Classic sung-vowel values; F1 rises as the jaw opens and F2 rises as the
# tongue moves forward, which is what makes the five distinguishable.
VOWELS: dict[str, tuple[tuple[float, float], ...]] = {
    "a": ((730.0, 1.0), (1090.0, 0.45), (2440.0, 0.15)),
    "e": ((530.0, 1.0), (1840.0, 0.40), (2480.0, 0.18)),
    "i": ((270.0, 1.0), (2290.0, 0.35), (3010.0, 0.20)),
    "o": ((570.0, 1.0), (840.0, 0.50), (2410.0, 0.10)),
    "u": ((300.0, 1.0), (870.0, 0.35), (2240.0, 0.08)),
}


_WT_LEN = 2048  # samples per single-cycle frame
# Harmonic caps for the mip stack, brightest first. A frame is rendered from
# its harmonic spectrum truncated to the cap, so nothing above Nyquist is
# ever synthesised — the same anti-aliasing bar PolyBLEP sets for saw/square.
_WT_MIPS = (256, 128, 64, 32, 16, 8, 4, 2, 1)


def _wt_spectra() -> dict[str, list[np.ndarray]]:
    """Each frame as a harmonic amplitude vector (index 0 = fundamental).
    Defining tables spectrally rather than as sampled waveforms is what makes
    exact band-limiting possible."""
    h = np.arange(1, _WT_MIPS[0] + 1)
    odd = (h % 2 == 1).astype(float)

    def spec(amps):
        kind = np.complex128 if np.iscomplexobj(amps) else np.float64
        a = np.asarray(amps, dtype=kind)
        return a / max(float(np.abs(a).sum()), 1e-12)

    # sine -> triangle -> saw -> square: the classic brightness ramp
    basic = [
        spec(np.where(h == 1, 1.0, 0.0)),
        spec(odd / h**2),
        spec(1.0 / h),
        spec(odd / h),
    ]
    # additive stack: 1 -> 2 -> 4 -> 8 partials, an organ-ish swell
    harmonic = [spec((h <= m) / h) for m in (1, 2, 4, 8)]
    # vocal: a gaussian bump of harmonics that slides upward = formant sweep
    vocal = [spec(np.exp(-(((h - c) / 3.0) ** 2)) / np.sqrt(h)) for c in (1, 4, 9, 16)]
    # digital: odd-heavy and increasingly toothy
    digital = [
        spec(odd / h),
        spec(odd / np.sqrt(h)),
        spec(np.where(h % 3 == 1, 1.0, 0.0) / np.sqrt(h)),
        spec(np.ones_like(h, dtype=float) / np.sqrt(h)),
    ]
    return {
        "basic": basic,
        "harmonic": harmonic,
        "vocal": vocal,
        "digital": digital,
        # appended, so the first four keep their
        # indexes (and their samples: real amplitudes take the same path)
        **_wt_spectra_r5(h, odd, spec),
    }


def _wt_spectra_r5(h: np.ndarray, odd: np.ndarray, spec) -> dict[str, list]:
    """The eight appended tables, four frames each like the first four.
    Still spectral: a complex amplitude is a sine part (real) and a cosine
    part (imaginary) — what a pulse or a quantised wave needs, which an odd
    (sine-only) waveform cannot be — so every mip stays band-limited."""
    # scan: one narrow band of harmonics swept up the spectrum, its odd
    # harmonics lifted — the hard, glassy sweep of the 80s digital synths
    scan = [
        spec(np.exp(-(((h - c) / (0.3 * c + 1.0)) ** 2)) * (1.0 + 0.6 * odd))
        for c in (2.0, 6.0, 15.0, 36.0)
    ]
    # organ: drawbar registrations on the 8' fundamental (harmonics 1, 2, 3,
    # 4, 6, 8: 8', 4', 2 2/3', 2', 1 1/3', 1') — flute, jazz, gospel, full
    bars = (
        {1: 1.0},
        {1: 1.0, 2: 0.8, 3: 0.8},
        {1: 1.0, 2: 1.0, 3: 0.8, 4: 0.8, 6: 0.5},
        {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 6: 1.0, 8: 1.0, 5: 0.6},
    )
    organ = [spec(np.array([b.get(int(k), 0.0) for k in h])) for b in bars]
    # bell: sparse, widely spaced partials over a quiet fundamental (strike
    # partials at roughly 2, 3, 4.2, 5.4 and 6.8 x a hum, rounded onto the
    # table's harmonics, then ever more of them, higher)
    sets = (
        (1, 4, 6),
        (1, 4, 6, 9, 11),
        (1, 4, 6, 9, 11, 14, 17),
        (1, 5, 9, 13, 17, 22, 27),
    )
    bell = [
        spec(np.array([(k in s) * (0.35 if k == 1 else 1.0) / k**0.4 for k in h]))
        for s in sets
    ]
    # stack: a saw, then a saw with saws an octave, a twelfth and two octaves
    # above it laid on — the stacked-oscillator lead, in one cycle
    gains = (1.0, 0.7, 0.55, 0.45)
    stack = [
        spec(sum((h % m == 0) * (m / h) * gains[i] for i, m in enumerate(ms)))
        for ms in ((1,), (1, 2), (1, 2, 3), (1, 2, 3, 4))
    ]
    # pulse: a pulse wave narrowing from square to a thin pulse (its DC
    # term left out: the voice's dc_block would take it anyway)
    pulse = [
        spec(((1 - np.cos(2 * np.pi * h * d)) + 1j * np.sin(2 * np.pi * h * d)) / h)
        for d in (0.5, 0.3, 0.15, 0.06)
    ]

    # formant: harmonics of a 110 Hz voice under the a, e, i, u resonances
    # (core.VOWELS) — the tract fixed in the table, so it moves with the
    # pitch (a wavetable's formants always do)
    def vowel(name):
        env = sum(
            a * np.exp(-(((h * 110.0 - f) / (60.0 + 0.08 * f)) ** 2))
            for f, a in VOWELS[name]
        )
        return spec((0.03 + env) / np.sqrt(h))

    formant = [vowel(v) for v in ("a", "e", "i", "u")]

    # crush: a sine and a saw quantised and sample-held (bit and rate
    # reduction) — taken through the FFT to their harmonics, so the crush is
    # in the spectrum and the mip stack still band-limits it
    def crushed(wave, levels, hold):
        t = np.arange(_WT_LEN) / _WT_LEN
        x = np.sin(2 * np.pi * t) if wave == "sine" else 2 * t - 1
        x = np.round(x * levels) / levels
        x = x[(np.arange(_WT_LEN) // hold) * hold]
        c = np.fft.rfft(x)[1 : len(h) + 1] * (2 / _WT_LEN)
        return spec(-c.imag + 1j * c.real)

    crush = [
        crushed("sine", 7, 1),
        crushed("saw", 5, 16),
        crushed("saw", 3, 32),
        crushed("sine", 1, 64),
    ]
    # analog: a sine warming into a soft saw — even harmonics a little under
    # the odd, the top rolled off as a real oscillator's is
    analog = [
        spec(np.where(h == 1, 1.0, 0.0)),
        spec(np.exp(-(h - 1) * 1.4) * (1 - 0.3 * (1 - odd))),
        spec(1.0 / h**2 * (1 - 0.25 * (1 - odd))),
        spec(np.exp(-h / 48.0) / h * (1 - 0.15 * (1 - odd))),
    ]
    return {
        "scan": scan,
        "organ": organ,
        "bell": bell,
        "stack": stack,
        "pulse": pulse,
        "formant": formant,
        "crush": crush,
        "analog": analog,
    }


def _build_wavetables():
    """Render every (table, mip) pair once at import: (frames, _WT_LEN)."""
    t = np.linspace(0.0, 1.0, _WT_LEN, endpoint=False)
    out: dict[str, dict[int, np.ndarray]] = {}
    for name, frames in _wt_spectra().items():
        per_mip: dict[int, np.ndarray] = {}
        for cap in _WT_MIPS:
            rendered = []
            for amps in frames:
                w = np.zeros(_WT_LEN)
                for k, a in enumerate(amps[:cap], start=1):
                    if isinstance(a, np.complexfloating):  # sine + cosine
                        if a:
                            w += a.real * np.sin(2 * np.pi * k * t)
                            w += a.imag * np.cos(2 * np.pi * k * t)
                    elif a:
                        w += a * np.sin(2 * np.pi * k * t)
                peak = float(np.max(np.abs(w)))
                rendered.append(w / peak if peak > 1e-12 else w)
            per_mip[cap] = np.array(rendered)
        out[name] = per_mip
    return out


_WT_BANK = _build_wavetables()
# public view: the brightest mip, which is what callers inspect
WAVETABLES: dict[str, np.ndarray] = {k: v[_WT_MIPS[0]] for k, v in _WT_BANK.items()}
WAVETABLE_NAMES = tuple(WAVETABLES)


def _wt_mip_index(f: np.ndarray, sr: int) -> np.ndarray:
    """Index into _WT_MIPS of the brightest cap under Nyquist, per pitch."""
    allowed = (sr * 0.5) / np.maximum(np.abs(f), 1e-6)
    caps = np.asarray(_WT_MIPS)  # descending
    # first cap <= allowed; caps is descending, so count the ones above it
    idx = np.sum(caps[None, :] > np.asarray(allowed)[:, None], axis=1)
    return np.minimum(idx, len(_WT_MIPS) - 1)


def _wt_mip(freq, sr: int) -> int:
    """Brightest harmonic cap that stays under Nyquist for this pitch (the
    highest pitch in `freq`, when it is an array)."""
    f = float(np.max(np.abs(np.atleast_1d(np.asarray(freq, dtype=np.float64)))))
    return _WT_MIPS[int(_wt_mip_index(np.array([f]), sr)[0])]


_WT_MIP_BLOCK = 64  # samples per mip decision (the swept filter's block)
# (table, mips, frames, _WT_LEN): one array per table so a per-block mip index
# can be gathered in a single fancy-index read
_WT_STACK = {k: np.stack([v[c] for c in _WT_MIPS]) for k, v in _WT_BANK.items()}
# the same stacks and caps as the native kernel takes them: flat, float64
_WT_FLAT = {k: np.ascontiguousarray(v).reshape(-1) for k, v in _WT_STACK.items()}
_WT_CAPS = np.asarray(_WT_MIPS, dtype=np.float64)


def _wt_stack(table) -> np.ndarray:
    """A table's mip stack (mips, frames, _WT_LEN): a built-in by name (an
    unknown name is "basic": corrupt preset JSON must never crash a
    render), or a stack itself — a user's own tables
    (`synthlab.wavetables`) arrive built."""
    if isinstance(table, np.ndarray):
        return table
    return _WT_STACK.get(str(table), _WT_STACK["basic"])


def _wt_read(stack: np.ndarray, mip, p, x) -> np.ndarray:
    """`wavetable_numpy`'s bilinear read at phase `x` (0..1), frame
    position `p` (0..frames - 1) and mip index `mip`, per sample."""
    nf = stack.shape[1]
    ph = x * _WT_LEN
    i0 = np.floor(ph).astype(int) % _WT_LEN
    i1 = (i0 + 1) % _WT_LEN
    fr = ph - np.floor(ph)
    f0 = np.floor(p).astype(int)
    f1 = np.minimum(f0 + 1, nf - 1)
    fm = p - f0
    a = stack[mip, f0, i0] * (1 - fr) + stack[mip, f0, i1] * fr
    b = stack[mip, f1, i0] * (1 - fr) + stack[mip, f1, i1] * fr
    return a * (1 - fm) + b * fm


def wavetable_stack(spectra) -> np.ndarray:
    """A mip stack from frames given as harmonic spectra ((frames, k)
    complex: sine part real, cosine part imaginary, harmonic 1 first, as
    `_wt_spectra`'s), each frame truncated to every cap in `_WT_MIPS` and
    put to its own peak as `_build_wavetables` does — through the inverse
    FFT, so a 64-frame table builds in milliseconds."""
    s = np.asarray(spectra, dtype=np.complex128)
    s = s.reshape(-1, s.shape[-1])[:, : _WT_MIPS[0]]
    out = np.zeros((len(_WT_MIPS), len(s), _WT_LEN))
    for m, cap in enumerate(_WT_MIPS):
        y = np.zeros((len(s), _WT_LEN // 2 + 1), dtype=np.complex128)
        c = s[:, :cap]
        y[:, 1 : c.shape[1] + 1] = (c.imag - 1j * c.real) * (_WT_LEN / 2)
        w = np.fft.irfft(y, _WT_LEN, axis=1)
        peak = np.max(np.abs(w), axis=1, keepdims=True)
        out[m] = np.where(peak > 1e-12, w / np.maximum(peak, 1e-12), w)
    return out


def _wt_native(table):
    """(table's flat mip stack, caps, frames) for the native wavetable; an
    unknown table is "basic", as in `wavetable_numpy`."""
    if isinstance(table, np.ndarray):
        return np.ascontiguousarray(table).reshape(-1), _WT_CAPS, table.shape[1]
    name = str(table) if str(table) in _WT_STACK else "basic"
    return _WT_FLAT[name], _WT_CAPS, _WT_STACK[name].shape[1]


def wavetable(freq, n: int, sr: int = SR, table="basic", pos=0.0, phase0=0.0):
    """Morphable wavetable oscillator (see `wavetable_numpy`, the
    reference); the native kernel is the same read, bit for bit."""
    native = kernel("wavetable")
    if native is None or n < 1 or np.ndim(freq) > 1 or np.ndim(pos) > 1:
        return wavetable_numpy(freq, n, sr, table, pos, phase0)
    flat, caps, frames = _wt_native(table)
    return native(
        _series(freq, n),
        _series(pos, n),
        flat,
        caps,
        frames,
        n,
        float(sr),
        float(phase0),
    )


def wavetable_numpy(freq, n: int, sr: int = SR, table="basic", pos=0.0, phase0=0.0):
    """Morphable wavetable oscillator.

    `pos` walks across the table's frames and may be a per-sample array, so a
    drawn curve sweeps the timbre across a note — the point of the shape.
    Reads are bilinear (phase within a frame, position between frames), and
    the frame set is chosen from a mip stack by pitch so the oscillator never
    aliases. The mip is picked per 64-sample block from that block's highest
    pitch: picking it once from the note's peak meant a +24 st pitch envelope
    dulled the whole note to the mip its first few milliseconds needed. An
    unknown table falls back to "basic" rather than raising: corrupt preset
    JSON must never crash a render.
    """
    stack = _wt_stack(table)
    nf = stack.shape[1]
    f = as_array(freq, n)
    ph = _phase(f, n, sr, phase0) * _WT_LEN
    i0 = np.floor(ph).astype(int) % _WT_LEN
    i1 = (i0 + 1) % _WT_LEN
    fr = ph - np.floor(ph)

    nb = -(-n // _WT_MIP_BLOCK)
    padded = np.pad(np.abs(f), (0, nb * _WT_MIP_BLOCK - n), mode="edge")
    peak = padded.reshape(nb, _WT_MIP_BLOCK).max(axis=1)
    mip = np.repeat(_wt_mip_index(peak, sr), _WT_MIP_BLOCK)[:n]

    p = np.clip(as_array(pos, n), 0.0, 1.0) * (nf - 1)
    f0 = np.floor(p).astype(int)
    f1 = np.minimum(f0 + 1, nf - 1)
    fm = p - f0
    a = stack[mip, f0, i0] * (1 - fr) + stack[mip, f0, i1] * fr
    b = stack[mip, f1, i0] * (1 - fr) + stack[mip, f1, i1] * fr
    return a * (1 - fm) + b * fm


def osc(shape: str, freq, n: int, sr: int = SR, **kw) -> np.ndarray:
    """Dispatch by name: osc('saw', 220, 44100). `warp=(mode, amount)`
    bends a WARP_SHAPES phase (`warped`); "off" and the other shapes
    ignore it."""
    warp = kw.get("warp")
    if warp and warp[0] != "off" and shape in WARP_SHAPES:
        return warped(
            shape,
            freq,
            n,
            sr,
            warp[0],
            warp[1],
            width=kw.get("width", 0.5),
            table=kw.get("table", "basic"),
            pos=kw.get("pos", 0.0),
            phase0=kw.get("phase0", 0.0),
        )
    if shape == "wavetable":
        return wavetable(
            freq,
            n,
            sr,
            table=kw.get("table", "basic"),
            pos=kw.get("pos", 0.0),
            phase0=kw.get("phase0", 0.0),
        )
    if shape == "noise":
        return noise(n, kw.get("color", "white"), kw.get("seed"))
    if shape == "sampler":
        # recorded audio (synthlab.sampler): stereo (n, 2) when it plays
        from . import sampler

        return sampler.render(freq, n, sr, kw.get("sampler"), kw.get("key_hz"))
    if shape == "fm":
        return fm(
            freq,
            n,
            sr,
            ratio=kw.get("ratio", 2.0),
            index=kw.get("index", 2.0),
            index_decay=kw.get("index_decay", 0.0),
            phase0=kw.get("phase0", 0.0),
        )
    if shape == "fm4":
        return fm4(
            freq,
            n,
            sr,
            algo=kw.get("fm4_algo", "stack"),
            ratios=kw.get("fm4_ratios", (1.0, 2.0, 1.0, 1.0)),
            levels=kw.get("fm4_levels", (1.0, 0.3, 0.0, 0.0)),
            decays=kw.get("fm4_decays", (0.0, 0.0, 0.0, 0.0)),
            feedback=kw.get("fm4_feedback", 0.0),
            phase0=kw.get("phase0", 0.0),
        )
    if shape == "pluck":
        return pluck(
            freq,
            n,
            sr,
            damping=kw.get("damping", 0.5),
            bright=kw.get("bright", 0.9),
            seed=kw.get("seed", 0),
        )
    fn = WAVEFORMS[shape]
    if shape == "supersaw":
        return fn(
            freq,
            n,
            sr,
            voices=kw.get("voices", 7),
            detune=kw.get("detune", 0.18),
            seed=kw.get("seed", 0),
        )
    if shape == "square":
        return fn(freq, n, sr, phase0=kw.get("phase0", 0.0), width=kw.get("width", 0.5))
    return fn(freq, n, sr, phase0=kw.get("phase0", 0.0))


# ----------------------------------------------------------------------------
# envelopes and LFOs
# ----------------------------------------------------------------------------


def adsr(
    n: int,
    attack=0.01,
    decay=0.1,
    sustain=0.7,
    release=0.2,
    gate: float | None = None,
    sr: int = SR,
    curve: float = 2.0,
) -> np.ndarray:
    """
    Classic ADSR over `n` samples. `gate` is how long the key is held in
    seconds (defaults to everything except the release tail).
    `curve` shapes decay/release: 1.0 = linear, higher = snappier.

    The release takes `release` seconds from the end of the gate, and the
    envelope is exactly zero after it. It used to span whatever was left
    of the buffer, so how long a note rang was set by how long its buffer
    happened to be (`sequencer.note_span`), not by the patch: a 0.02 s
    release on a 2 s note was still at 45 % of its level 150 ms after
    key-up. A buffer that ends before the release does still lands on
    silence: the release is fitted into what is left of it.
    """
    native = kernel("adsr")
    times = (attack, decay, sustain, release, curve)
    if native is None or n < 1 or any(np.ndim(v) for v in times):
        return adsr_numpy(n, attack, decay, sustain, release, gate, sr, curve)
    # the sample counts round here, with Python's round, as adsr_numpy's do
    rel_n = n_samples(release, sr)
    gate_n = n - rel_n if gate is None else min(n, n_samples(gate, sr))
    return native(
        n,
        max(1, gate_n),
        n_samples(attack, sr),
        n_samples(decay, sr),
        float(sustain),
        float(curve),
        min(n, n_samples(0.005, sr)),
        max(0, rel_n),
    )


def adsr_numpy(
    n: int,
    attack=0.01,
    decay=0.1,
    sustain=0.7,
    release=0.2,
    gate: float | None = None,
    sr: int = SR,
    curve: float = 2.0,
) -> np.ndarray:
    env = np.zeros(n)
    rel_n = n_samples(release, sr)
    gate_n = n - rel_n if gate is None else min(n, n_samples(gate, sr))
    gate_n = max(1, gate_n)

    # Each ramp is built at its full length and the gate cuts it. Fitting the
    # ramps into the gate instead squeezed them: a 1 s attack on a 0.1 s gate
    # reached full level at the release, ten times too fast and too loud.
    a_full = n_samples(attack, sr)
    d_full = n_samples(decay, sr)
    a_n = min(a_full, gate_n)
    d_n = min(d_full, gate_n - a_n)
    s_n = max(0, gate_n - a_n - d_n)

    i = 0
    if a_n:
        env[i : i + a_n] = np.linspace(0, 1, a_full)[:a_n]
        i += a_n
    if d_n:
        shape = np.linspace(0, 1, d_full)[:d_n] ** (1 / curve)
        env[i : i + d_n] = 1 + (sustain - 1) * shape
        i += d_n
    if s_n:
        env[i : i + s_n] = sustain
        i += s_n
    if i < n:
        start = env[i - 1] if i > 0 else sustain
        # the release lasts `release`, then silence (the zeros `env` began
        # as); a buffer too short for it has it fitted, so the last sample
        # is still zero. Without a gate, `n - i` is the release itself.
        tail = min(max(0, rel_n), n - i)
        env[i : i + tail] = start * (1 - np.linspace(0, 1, tail) ** (1 / curve))
    else:
        # The gate ran to the end of the buffer, so there is no room for a
        # release and the envelope used to stop dead at the sustain level —
        # a step to silence, which is a click. Land it instead, the same
        # 5 ms cosine ponytail perc_env uses.
        k = min(n, n_samples(0.005, sr))
        if k > 1:
            env[n - k :] *= 0.5 * (1 + np.cos(np.pi * np.linspace(0, 1, k)))
        else:
            env[-1] = 0.0
    return env


def perc_env(
    n: int, decay: float = 0.3, curve: float = 4.0, sr: int = SR
) -> np.ndarray:
    """Instant attack, exponential decay — drums, plucks, stabs."""
    t = time_axis(n, sr)
    env = np.exp(-t / max(decay, 1e-4) * curve / 4)
    # ponytail: a buffer shorter than the decay would end mid-tail and step to
    # zero — an audible click. 5 ms cosine fade lands it on silence instead.
    k = min(n, n_samples(0.005, sr))
    if k > 1:
        env[n - k :] *= 0.5 * (1 + np.cos(np.pi * np.linspace(0, 1, k)))
    return env


def sample_hold(rate: float, n: int, sr: int = SR, seed: int = 0) -> np.ndarray:
    """Random steps held at `rate` per second, in [-1, 1]. Seeded, because a
    modulation source that re-rolls itself makes the patch un-renderable
    twice and quietly invalidates every content-addressed cache upstream."""
    rate = max(float(rate), 1e-6)
    steps = int(np.ceil(rate * n / sr)) + 1
    vals = np.random.default_rng(seed).uniform(-1.0, 1.0, steps)
    idx = np.minimum((np.arange(n) * rate / sr).astype(np.int64), steps - 1)
    return vals[idx]


def lfo(
    rate: float,
    n: int,
    shape: str = "sine",
    sr: int = SR,
    depth: float = 1.0,
    phase0: float = 0.0,
    bipolar: bool = True,
    seed: int = 0,
) -> np.ndarray:
    """A modulation source. "noise" is a sample-and-hold at `rate`, not audio
    noise: it used to call `noise(n)`, which ignores the rate entirely, so
    asking for 2 Hz sprayed 22,000 zero crossings a second into whatever it
    modulated — hiss on the cutoff rather than a stepped wobble."""
    if shape == "noise":
        w = sample_hold(rate, n, sr, seed)
    else:
        w = osc(shape, rate, n, sr, phase0=phase0)
    if not bipolar:
        w = (w + 1) * 0.5
    return w * depth


# --- the general LFOs' extra shapes ------------------------------------------
# Every one is a function of the LFO's own phase, counted from the note's
# start (never a random stream), so a note renders the same every time and
# the live voice (voice-worklet.js `lfoX`) draws the same numbers: the drawn
# curve and the smooth random by the same arithmetic, the chaotic map by the
# same float64 recurrence.

_M32 = 0xFFFFFFFF


def hash_u(seed: int, key: int) -> float:
    """A uniform 0..1 from (seed, key): a 32-bit integer hash (lowbias32),
    written so JS's Math.imul computes the same bits (synth.drift_u)."""
    x = (seed ^ ((key * 0x9E3779B9) & _M32)) & _M32
    x ^= x >> 16
    x = (x * 0x7FEB352D) & _M32
    x ^= x >> 15
    x = (x * 0x846CA68B) & _M32
    x ^= x >> 16
    return x / 4294967296.0


DRAW_MAX_POINTS = 32
# an empty drawing plays as a triangle (the editor's first shape)
DRAW_DEFAULT = ((0.0, -1.0), (0.5, 1.0))
CHAOS_R = 3.99  # the logistic map's parameter: well inside its chaos


def draw_parse(s) -> list[tuple[float, float]]:
    pts: dict[float, float] = {}
    if not isinstance(s, str) or len(s) > 2048:
        return []
    for part in s.split(",")[: DRAW_MAX_POINTS * 2]:
        try:
            xs, ys = part.split(":")
            x, y = float(xs), float(ys)
        except ValueError:
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        x = round(min(1.0, max(0.0, x)), 3)
        if x >= 1.0:  # one cycle: 1 is the next cycle's 0
            continue
        pts[x] = round(min(1.0, max(-1.0, y)), 3)
    return sorted(pts.items())[:DRAW_MAX_POINTS]


def clean_draw(s) -> str:
    """A drawn LFO shape as stored: "x:y,x:y,..." — up to DRAW_MAX_POINTS
    points, x in 0..1 (one cycle, sorted, one point an x), y in -1..1, three
    decimals. Anything unreadable is dropped; "" plays DRAW_DEFAULT
    (params.META_CLEAN cleans it inside clamp)."""
    return ",".join(f"{x:g}:{y:g}" for x, y in draw_parse(s))


def draw_points(s) -> tuple[np.ndarray, np.ndarray]:
    """The drawing as one periodic polyline: its points with the last one a
    cycle before the first and the first a cycle after the last, so linear
    interpolation over [0, 1) wraps."""
    pts = draw_parse(s) or list(DRAW_DEFAULT)
    xs = [pts[-1][0] - 1.0, *(x for x, _ in pts), pts[0][0] + 1.0]
    ys = [pts[-1][1], *(y for _, y in pts), pts[0][1]]
    return np.asarray(xs), np.asarray(ys)


def chaos_x0(seed: int) -> float:
    return 0.1 + 0.8 * hash_u(seed, 0)


def lfo_ext(
    shape: str,
    rate,
    n: int,
    sr: int = SR,
    phase0: float = 0.0,
    seed: int = 0,
    draw: str = "",
    table_seed: int = 0,
) -> np.ndarray:
    """A general LFO: -1..1 at `rate` (Hz, scalar or per
    sample), its phase the cumulative sum of rate / sr from `phase0` (as an
    oscillator's). "drawn" reads `draw` (clean_draw's format) along the
    phase; "chaos" is the logistic map stepped once a cycle and joined by
    straight lines (its start from `seed`); "smooth" a seeded random value
    a cycle, smoothstepped from one to the next; "noise" the stepped random
    of `sample_hold`'s stream `table_seed` (the live voice's table holds the
    streams 0..7), stepped at each whole cycle of the phase; the rest are
    `osc` shapes from `phase0`."""
    if shape not in ("noise", "drawn", "chaos", "smooth"):
        return osc(shape, rate, n, sr, phase0=phase0)
    acc = np.cumsum(as_array(rate, n)) / sr + phase0
    k = np.floor(acc)
    fr = acc - k
    k = k.astype(np.int64)
    top = int(k[-1]) + 2 if n else 2
    if shape == "drawn":
        xs, ys = draw_points(draw)
        return np.interp(fr, xs, ys)
    if shape == "noise":
        vals = np.random.default_rng(table_seed).uniform(-1.0, 1.0, top)
        return vals[k]
    if shape == "chaos":
        x = chaos_x0(seed)
        seq = [x]
        for _ in range(top):
            x = CHAOS_R * x * (1.0 - x)
            seq.append(x)
        xk = np.asarray(seq)
        return 2.0 * (xk[k] + (xk[k + 1] - xk[k]) * fr) - 1.0
    v = np.asarray([2.0 * hash_u(seed, j) - 1.0 for j in range(top + 1)])
    s = fr * fr * (3.0 - 2.0 * fr)
    return v[k] + (v[k + 1] - v[k]) * s


# ----------------------------------------------------------------------------
# filters  (time-varying — cutoff can be an envelope or LFO)
# ----------------------------------------------------------------------------


def biquad_coeffs(kind: str, fc: float, q: float, sr: int = SR):
    """One biquad's (b, a) coefficients for a single, static cutoff — the
    building block `biquad()` calls every `block` samples for a sweep, and
    the public name for what other modules want when they need real
    coefficients rather than a filtered signal (e.g. `soundmeta.py`
    drawing the filter-response curve). `_biquad` stays as the historical
    internal alias other engine code and tests already spell that way."""
    fc = float(np.clip(fc, 20.0, sr * 0.49))
    w0 = 2 * np.pi * fc / sr
    alpha = np.sin(w0) / (2 * max(q, 0.05))
    cw = np.cos(w0)
    if kind == "lowpass":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "highpass":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "bandpass":
        b = [alpha, 0.0, -alpha]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "notch":
        b = [1.0, -2 * cw, 1.0]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    else:
        raise ValueError(f"unknown filter kind: {kind}")
    b = np.asarray(b) / a[0]
    a = np.asarray(a) / a[0]
    return b, a


_biquad = biquad_coeffs  # back-compat: engine code and tests already import this name


def _biquad_blocks(kind: str, fc: np.ndarray, q, sr: int = SR):
    """`_biquad` for many cutoffs at once: (nb,) cutoff array in,
    (nb, 3) b and a coefficient arrays out. Same math, vectorized.
    `q` may be a scalar or a per-block array (drawn resonance curves)."""
    fc = np.clip(fc, 20.0, sr * 0.49)
    w0 = 2 * np.pi * fc / sr
    qb = np.maximum(np.asarray(q, dtype=np.float64), 0.05)
    alpha = np.sin(w0) / (2 * qb)
    cw = np.cos(w0)
    zero = np.zeros_like(fc)
    one = np.ones_like(fc)
    if kind == "lowpass":
        b = np.stack([(1 - cw) / 2, 1 - cw, (1 - cw) / 2], axis=1)
    elif kind == "highpass":
        b = np.stack([(1 + cw) / 2, -(1 + cw), (1 + cw) / 2], axis=1)
    elif kind == "bandpass":
        b = np.stack([alpha, zero, -alpha], axis=1)
    elif kind == "notch":
        b = np.stack([one, -2 * cw, one], axis=1)
    else:
        raise ValueError(f"unknown filter kind: {kind}")
    a = np.stack([1 + alpha, -2 * cw, 1 - alpha], axis=1)
    a0 = a[:, :1]
    return b / a0, a / a0


def _svf_blocks(kind: str, fc: np.ndarray, q, sr: int = SR):
    """The TPT (topology-preserving, Zavalishin/Simper) state-variable filter
    as a per-block linear state-space system: state s = (ic1eq, ic2eq),
    s' = A s + B x, y = C s + D x. Its transfer function is exactly the RBJ
    biquad `_biquad` builds (both are the prewarped bilinear transform of the
    same analog prototype); only the state differs. Returns component arrays
    (a11, a12, a21, a22), (b1, b2), (c1, c2), d, each (nb,)."""
    fc = np.clip(fc, 20.0, sr * 0.49)
    g = np.tan(np.pi * fc / sr)
    k = 1.0 / np.maximum(np.asarray(q, dtype=np.float64), 0.05)
    k = np.broadcast_to(k, g.shape)
    a1 = 1.0 / (1.0 + g * (g + k))
    a2 = g * a1
    a3 = g * a2
    A = (2 * a1 - 1, -2 * a2, 2 * a2, 1 - 2 * a3)
    B = (2 * a2, 2 * a3)
    # outputs: low = v2, band = v1, high = x - k v1 - v2
    if kind == "lowpass":
        C, D = (a2, 1 - a3), a3
    elif kind == "highpass":
        C, D = (-k * a1 - a2, k * a2 - 1 + a3), 1 - k * a2 - a3
    elif kind == "bandpass":  # RBJ's 0 dB-peak bandpass = k * band
        C, D = (k * a1, -k * a2), k * a2
    elif kind == "notch":  # low + high
        C, D = (-k * a1, k * a2), 1 - k * a2
    else:
        raise ValueError(f"unknown filter kind: {kind}")
    return A, B, C, D


def _mat2_mul(p, q):
    """Batched 2x2 product p @ q, each a (m11, m12, m21, m22) tuple."""
    return (
        p[0] * q[0] + p[1] * q[2],
        p[0] * q[1] + p[1] * q[3],
        p[2] * q[0] + p[3] * q[2],
        p[2] * q[1] + p[3] * q[3],
    )


def _mat2_pow(a, e: int):
    """Batched 2x2 power a**e (e >= 1) by repeated squaring."""
    result = None
    base = a
    while e:
        if e & 1:
            result = base if result is None else _mat2_mul(result, base)
        e >>= 1
        if e:
            base = _mat2_mul(base, base)
    return result


def _svf_run(X: np.ndarray, A, B, C, D, s0: np.ndarray):
    """Run `X` (ch, nb, block) through per-block SVF coefficients. Every
    block's response is built in closed form at once — zero-state part by
    FFT convolution with the block's impulse response, zero-input part from
    the block's starting state — and the block-to-block state recursion
    s[k+1] = M_k s[k] + u_k is an affine map composed by a parallel prefix
    scan, so nothing loops per block in Python. `s0` is (2, ch)."""
    a11, a12, a21, a22 = A
    b1, b2 = B
    c1, c2 = C
    _, nb, block = X.shape

    # r_n = C A^n (row, zero-input response), w_m = A^m B (column)
    r1 = np.empty((block, nb))
    r2 = np.empty((block, nb))
    w1 = np.empty((block, nb))
    w2 = np.empty((block, nb))
    r1[0], r2[0], w1[0], w2[0] = c1, c2, b1, b2
    for m in range(1, block):
        r1[m] = r1[m - 1] * a11 + r2[m - 1] * a21
        r2[m] = r1[m - 1] * a12 + r2[m - 1] * a22
        w1[m] = a11 * w1[m - 1] + a12 * w2[m - 1]
        w2[m] = a21 * w1[m - 1] + a22 * w2[m - 1]
    # M = A^block, the state transition across a whole block
    M = _mat2_pow(A, block)

    # impulse response: h[0] = D, h[n] = C A^(n-1) B
    h = np.empty((nb, block))
    h[:, 0] = D
    h[:, 1:] = (r1[:-1] * b1 + r2[:-1] * b2).T
    nfft = 2 * block
    zs = np.fft.irfft(
        np.fft.rfft(h, nfft, axis=-1) * np.fft.rfft(X, nfft, axis=-1), nfft, axis=-1
    )[..., :block]

    # state input over a block: u = sum_j A^(block-1-j) B x[j]   -> (ch, nb)
    u1 = (X * np.ascontiguousarray(w1[::-1].T)).sum(-1)
    u2 = (X * np.ascontiguousarray(w2[::-1].T)).sum(-1)

    # inclusive scan of the affine maps (M_k, u_k): afterwards block k's map
    # takes the first block's start state to the state *after* block k
    M = list(M)
    U = [u1, u2]
    d = 1
    while d < nb:
        p = tuple(m[:-d] for m in M)  # earlier map
        q = tuple(m[d:] for m in M)  # later map
        pu1, pu2 = (u[:, :-d] for u in U)
        new_u1 = q[0] * pu1 + q[1] * pu2 + U[0][:, d:]
        new_u2 = q[2] * pu1 + q[3] * pu2 + U[1][:, d:]
        prod = _mat2_mul(q, p)
        for i in range(4):
            M[i] = np.concatenate([M[i][:d], prod[i]])
        U = [
            np.concatenate([U[0][:, :d], new_u1], axis=1),
            np.concatenate([U[1][:, :d], new_u2], axis=1),
        ]
        d *= 2
    s1, s2 = s0[0][:, None], s0[1][:, None]  # (ch, 1)
    after1 = M[0] * s1 + M[1] * s2 + U[0]  # (ch, nb)
    after2 = M[2] * s1 + M[3] * s2 + U[1]
    start1 = np.concatenate([s1, after1[:, :-1]], axis=1)
    start2 = np.concatenate([s2, after2[:, :-1]], axis=1)

    zi = (
        np.ascontiguousarray(r1.T) * start1[..., None]
        + np.ascontiguousarray(r2.T) * start2[..., None]
    )
    return zs + zi, np.stack([after1[:, -1], after2[:, -1]])


def _swept_biquad(x: np.ndarray, kind: str, fc, q, sr: int, block: int):
    """A sweeping filter that stays bounded under any modulation (see
    `_swept_biquad_numpy`, the reference). The native kernel runs the SVF as
    the plain per-sample recursion it is — ~30x the closed-form NumPy path."""
    native = kernel("swept_svf")
    if native is None:
        return _swept_biquad_numpy(x, kind, fc, q, sr, block)
    return native(
        np.ascontiguousarray(x, dtype=np.float64),
        kind,
        np.ascontiguousarray(fc, dtype=np.float64).reshape(-1),
        np.ascontiguousarray(q, dtype=np.float64).reshape(-1),
        float(sr),
        int(block),
    )


def _swept_biquad_numpy(x: np.ndarray, kind: str, fc, q, sr: int, block: int):
    """A sweeping filter that stays bounded under any modulation.

    It used to run the RBJ biquad in transposed direct form, block by block,
    carrying the state across coefficient changes. That state only means
    anything for the coefficients that produced it: a cutoff step from 6.5 kHz
    to 20 Hz turned it into a 76x spike, and a square LFO on the default
    patch's cutoff peaked at 35x. The TPT state-variable filter's state is
    its integrators — the band and low outputs themselves — which stay
    meaningful whatever the cutoff does next, so a jump cannot inject energy.
    Same transfer function per block, so a steady cutoff sounds identical.
    """
    n = len(x)
    stereo = x.ndim == 2
    xs = x if stereo else x[:, None]
    ch = xs.shape[1]
    nb = -(-n // block)
    # channel-major (ch, nb, block): every per-block FFT and sum runs along
    # the contiguous last axis
    X = np.zeros((ch, nb * block))
    X[:, :n] = xs.T
    X = X.reshape(ch, nb, block)
    A, B, C, D = _svf_blocks(kind, fc, q, sr)
    out = np.empty_like(X)
    s = np.zeros((2, ch))
    chunk = 2048  # blocks per pass: bounds the scratch arrays on long input
    for c0 in range(0, nb, chunk):
        sl = slice(c0, min(nb, c0 + chunk))
        out[:, sl], s = _svf_run(
            X[:, sl],
            tuple(a[sl] for a in A),
            tuple(b[sl] for b in B),
            tuple(c[sl] for c in C),
            D[sl],
            s,
        )
    out = out.reshape(ch, nb * block)[:, :n]
    return np.ascontiguousarray(out.T) if stereo else out[0].copy()


def biquad(
    x: np.ndarray,
    kind: str = "lowpass",
    cutoff=1000.0,
    q: float = 0.707,
    sr: int = SR,
    block: int = 64,
) -> np.ndarray:
    """
    Filter with a cutoff that may sweep. Coefficients are recomputed every
    `block` samples (all blocks in one vectorized pass) and the swept path is
    a state-variable filter whose state survives coefficient changes, so
    envelopes and LFOs on the cutoff sound continuous and never blow up.
    """
    plan = _biquad_plan(len(x), kind, cutoff, q, sr, block)
    if plan[0] == "lfilter":  # static — one shot, much faster
        return _lfilter(plan[1], plan[2], x)
    return _swept_biquad(x, kind, plan[2], plan[3], sr, block)


def _biquad_plan(n: int, kind: str, cutoff, q, sr: int = SR, block: int = 64):
    """What `biquad` runs over `n` samples: ("lfilter", b, a) when neither
    the cutoff nor the resonance moves, else ("svf", kind, fc, q) with one
    cutoff per block and one q, or one per block. The one decision, shared
    with the native post chain (`_biquad_stage`)."""
    fc = as_array(cutoff, n)
    qa = np.asarray(q, dtype=np.float64)
    q_varies = qa.ndim > 0 and np.ptp(qa) > 1e-9
    if np.ptp(fc) < 1e-9 and not q_varies:
        q0 = float(qa) if qa.ndim == 0 else float(qa.flat[0])
        b, a = _biquad(kind, fc[0], q0, sr)
        return ("lfilter", b, a)
    if q_varies:
        qs = as_array(q, n)[::block]
    else:
        qs = float(qa) if qa.ndim == 0 else float(qa.flat[0])
    return ("svf", kind, fc[::block], qs)


# ----------------------------------------------------------------------------
# stages of the native post chain (`synthlab_dsp.post`, `Synth._native_post`):
# each function below is one of this module's filters or shapers as the
# tuple the kernel runs, with every decision made here, where its twin
# makes it
# ----------------------------------------------------------------------------


def _biquad_stage(n: int, kind: str, cutoff, q, sr: int = SR, block: int = 64):
    """`biquad(x, kind, cutoff, q, sr)` on an `n`-sample x."""
    plan = _biquad_plan(n, kind, cutoff, q, sr, block)
    if plan[0] == "lfilter":
        return plan
    return (
        "svf",
        kind,
        np.ascontiguousarray(plan[2], dtype=np.float64).reshape(-1),
        np.ascontiguousarray(plan[3], dtype=np.float64).reshape(-1),
        int(block),
    )


def _clip_stages(drive, n: int):
    """`soft_clip(x, drive)`: no stage when a constant drive leaves x alone,
    None when the drive is a shape only the NumPy path takes."""
    d = np.asarray(drive, dtype=np.float64)
    if d.ndim == 0:
        return [("clip", d.reshape(1))] if float(d) > 0 else []
    if d.ndim == 1 and len(d) == n:
        return [("clip", _drive_series(d))]
    return None


def _ladder_stages(n: int, cutoff, resonance, drive, sr: int = SR):
    """`ladder(x, cutoff, resonance, drive, sr)`, or None (see `_clip_stages`)."""
    clip = _clip_stages(drive, n)
    if clip is None:
        return None
    q = 0.707 + np.asarray(resonance, dtype=np.float64) * 6.0
    return [
        _biquad_stage(n, "lowpass", cutoff, q, sr),
        *clip,
        _biquad_stage(n, "lowpass", cutoff, 0.707, sr),
    ]


_VOWEL_F = np.array([[f for f, _ in v] for v in VOWELS.values()])  # (5, 3)
_VOWEL_A = np.array([[a for _, a in v] for v in VOWELS.values()])  # (5, 3)
# Bandwidth of each formant. Narrow enough to read as a vowel, wide enough
# not to ring like a comb.
_FORMANT_Q = 9.0
# Ceiling on the wet-path makeup gain (see formant()).
_FORMANT_MAKEUP_MAX = 12.0
# The makeup restores the level of a reference excitation: a saw's spectral
# envelope (power ~ 1/f^2) from C3 up. A grid over vowel position (every
# 1/32 of a..u) and tract size (log-spaced over formant()'s 0.1..4 clip),
# read bilinearly, by the engine and by the live voice alike (the table
# rides /wavetables.bin).
_FORMANT_REF_HZ = 130.81
FORMANT_MAKEUP_VOWELS = 33
FORMANT_MAKEUP_SIZES = (0.1, 4.0, 49)  # geomspace(lo, hi, count)


@cache
def formant_makeup_table(sr: int = SR) -> np.ndarray:
    """(vowels, sizes) makeup gains: sqrt(dry / wet power) of the reference
    excitation through the three-band tract, capped at _FORMANT_MAKEUP_MAX.
    It used to be the whole note's own RMS ratio, which nothing realtime can
    know at the first sample — and which renormalised every note, so a
    harmonic that happened to land on a formant peak (C5 on "i") came back
    18 dB quieter than one that missed it rather than louder."""
    f = np.geomspace(_FORMANT_REF_HZ, sr * 0.49, 512)
    w = np.gradient(f) / f**2  # the reference's power in each grid cell
    z1 = np.exp(-2j * np.pi * f / sr)  # z^-1 on the unit circle
    pos = np.linspace(0.0, 1.0, FORMANT_MAKEUP_VOWELS) * (len(VOWELS) - 1)
    lo = np.floor(pos).astype(int)
    hi = np.minimum(lo + 1, len(VOWELS) - 1)
    fr = (pos - lo)[:, None]
    centres = _VOWEL_F[lo] * (1 - fr) + _VOWEL_F[hi] * fr  # (vowels, 3)
    amps = _VOWEL_A[lo] * (1 - fr) + _VOWEL_A[hi] * fr
    sizes = np.geomspace(*FORMANT_MAKEUP_SIZES)
    fc = np.clip(centres[:, None, :] * sizes[None, :, None], 20.0, sr * 0.49)
    w0 = 2 * np.pi * fc / sr
    alpha = np.sin(w0) / (2 * _FORMANT_Q)
    a0 = 1 + alpha
    # RBJ bandpass b = (alpha, 0, -alpha), a = (1 + alpha, -2 cos w0, 1 - alpha)
    num = (alpha / a0)[..., None] * (1 - z1**2)
    den = (
        1
        + (-2 * np.cos(w0) / a0)[..., None] * z1
        + ((1 - alpha) / a0)[..., None] * z1**2
    )
    h = (amps[:, None, :, None] * num / den).sum(axis=2)  # (vowels, sizes, f)
    wet = (np.abs(h) ** 2 * w).sum(axis=-1)
    return np.minimum(np.sqrt(w.sum() / np.maximum(wet, 1e-30)), _FORMANT_MAKEUP_MAX)


def formant_makeup(vowel, size, sr: int = SR) -> np.ndarray:
    """The makeup gain for each (vowel, size) pair, read bilinearly off
    `formant_makeup_table`. Arrays in, array out."""
    table = formant_makeup_table(sr)
    lo_s, hi_s, count = FORMANT_MAKEUP_SIZES
    v = np.clip(np.asarray(vowel, dtype=np.float64), 0.0, 1.0) * (table.shape[0] - 1)
    s = np.clip(np.asarray(size, dtype=np.float64), lo_s, hi_s)
    s = np.log(s / lo_s) / np.log(hi_s / lo_s) * (count - 1)
    v0 = np.minimum(np.floor(v).astype(int), table.shape[0] - 2)
    s0 = np.minimum(np.floor(s).astype(int), count - 2)
    fv, fs = v - v0, s - s0
    top = table[v0, s0] * (1 - fs) + table[v0, s0 + 1] * fs
    bot = table[v0 + 1, s0] * (1 - fs) + table[v0 + 1, s0 + 1] * fs
    return top * (1 - fv) + bot * fv


def formant(x: np.ndarray, vowel=0.0, amount=1.0, size=1.0, sr: int = SR):
    """Vocal-tract filter: three parallel bandpass resonators whose centres
    are read from VOWELS and interpolated along a -> e -> i -> o -> u.

    `vowel`, `amount` and `size` each accept a scalar or a per-sample array,
    so a drawn curve can sweep the vowel across a note — that is the whole
    reason this lives on the Patch rather than in the FX rack. The sweep
    itself is free: `biquad` already recomputes coefficients per block.
    """
    if not np.any(np.asarray(amount) > 0):
        return x
    n = len(x)
    pos = np.clip(as_array(vowel, n), 0.0, 1.0) * (len(VOWELS) - 1)
    lo = np.floor(pos).astype(int)
    hi = np.minimum(lo + 1, len(VOWELS) - 1)
    frac = (pos - lo)[:, None]
    # interpolate between the two neighbouring vowels, per sample
    freqs = _VOWEL_F[lo] * (1 - frac) + _VOWEL_F[hi] * frac  # (n, 3)
    amps = _VOWEL_A[lo] * (1 - frac) + _VOWEL_A[hi] * frac
    sizes = np.clip(as_array(size, n), 0.1, 4.0)
    freqs = freqs * sizes[:, None]

    wet = np.zeros_like(x, dtype=np.float64)
    for band in range(freqs.shape[1]):
        centre = freqs[:, band]
        gain = amps[:, band]
        filtered = biquad(x, "bandpass", centre, _FORMANT_Q, sr)
        wet += filtered * (gain[:, None] if x.ndim == 2 else gain)
    # bandpass sums lose level against the dry signal; restore it so `amount`
    # is a genuine wet/dry blend rather than also a volume drop. The makeup
    # is the tract's, not the note's (formant_makeup_table): a saw through
    # the five vowels needs 5-8x. The cap matters for small tracts, whose
    # formants sit where a saw has little left. The tract is a filter, and a
    # filter is allowed to be quiet — a 45 Hz sine comes back quieter.
    makeup = formant_makeup(pos / (len(VOWELS) - 1), sizes, sr)
    wet *= makeup[:, None] if x.ndim == 2 else makeup
    a = as_array(amount, n)
    a = np.clip(a, 0.0, 1.0)[:, None] if x.ndim == 2 else np.clip(a, 0.0, 1.0)
    return x * (1 - a) + wet * a


def ladder(
    x: np.ndarray,
    cutoff=1000.0,
    resonance=0.4,
    drive=1.2,
    sr: int = SR,
) -> np.ndarray:
    """
    Moog-flavoured 24 dB/octave lowpass: two resonant poles in series with a
    little saturation. This is the filter that makes a synth sound *alive* —
    sweep the cutoff with an envelope and you have the classic acid/bass sound.
    `resonance` and `drive` accept scalars or per-sample arrays (automation).
    """
    q = 0.707 + np.asarray(resonance, dtype=np.float64) * 6.0
    y = biquad(x, "lowpass", cutoff, q, sr)
    y = soft_clip(y, drive)
    y = biquad(y, "lowpass", cutoff, 0.707, sr)
    return y


# --- more filter types -------------------------------------------------------
# Each is built from what is here — the swept SVF (`biquad`, whose bandpass
# output also makes an all-pass, x - 2 bp, and a bell, x + g bp) and one new
# kernel, the tuned comb — so each one has the same block-rate cutoff (every
# 64 samples) as the rest, and the live voice runs the same SVF steps.

# the 24 dB slopes: two SVFs at the cutoff, Butterworth at resonance 0 (q
# 0.5412 then 1.3066), the second's q raised by the resonance
LP24_Q1, LP24_Q2, LP24_RES = 0.5412, 1.3066, 5.0
# the diode ladder: a resonant SVF (q 0.5 + 5.5 res) into an asymmetric
# tanh (even harmonics: the bias), then a second SVF at q 0.5 — softer knee,
# grittier than the ladder's symmetric clip
DIODE_Q, DIODE_RES, DIODE_Q2, DIODE_BIAS = 0.5, 5.5, 0.5, 0.25
# the phaser: three second-order all-passes at the cutoff (q 0.5 + 3 res),
# averaged with the input — three notches, drawn together as q rises
PHASER_STAGES, PHASER_Q, PHASER_RES = 3, 0.5, 3.0
# the bell: +18 dB x resonance at the cutoff, q 1
PEAK_DB, PEAK_Q = 18.0, 1.0
# the comb: a feedback comb tuned to the cutoff (its delay 1 / cutoff), the
# resonance its feedback (up to COMB_FB); its delay at most COMB_MAX
# samples (the live voice's ring is 2048), so it tunes down to sr / 2000.
# Its output gain sqrt((1 - |g|) sqrt(1 - g^2)) splits the difference
# between keeping a broadband input's level and keeping the peaks' (a
# tuned comb on a tone's harmonics): at feedback 0.9 the peaks sit 6.4 dB
# up, noise 6.4 dB down; at 0.97, 9 dB
COMB_FB, COMB_MAX = 0.97, 2000
FILTER_BLOCK = 64


def _blocks(v, n: int, block: int = FILTER_BLOCK) -> np.ndarray:
    """A parameter's value at the start of each `block` (as `biquad` samples
    its cutoff)."""
    return as_array(v, n)[::block]


def diode_clip(x: np.ndarray, drive) -> np.ndarray:
    """The diode ladder's saturation: tanh(d x + b) - tanh(b), over its
    slope at 0 (so a quiet signal passes at unity), d the drive. Drive at
    or under 0 passes x."""
    d = np.asarray(drive, dtype=np.float64)
    if d.ndim and x.ndim == 2:
        d = d[:, None]
    tb = math.tanh(DIODE_BIAS)
    safe = np.where(d > 0, d, 1.0)
    y = (np.tanh(safe * x + DIODE_BIAS) - tb) / (safe * (1.0 - tb * tb))
    return np.where(d > 0, y, x)


def comb(x: np.ndarray, cutoff, resonance, sign: float, sr: int = SR) -> np.ndarray:
    """A feedback comb: y[n] = x[n] + g y[n - D], D = sr / cutoff (linear
    interpolation between the two samples either side), g = sign x the
    resonance (up to COMB_FB), out y x the gain above. Cutoff and feedback
    held per 64-sample block, as the SVF's are. Vectorised in runs of
    floor(D) samples (no sample in a run reads another), so a low comb
    costs a step a block."""
    n = len(x)
    if not n:
        return x
    fc = np.clip(_blocks(cutoff, n), sr / COMB_MAX, sr * 0.49)
    g = sign * np.clip(_blocks(resonance, n), 0.0, COMB_FB)
    head = COMB_MAX + 2
    y = np.zeros((head + n, *x.shape[1:]))
    for b, (f, gb) in enumerate(zip(fc.tolist(), g.tolist(), strict=True)):
        d = sr / f
        i = int(d)
        fr = d - i
        s, e = b * FILTER_BLOCK, min(n, (b + 1) * FILTER_BLOCK)
        j = s
        while j < e:
            k = min(e, j + i)
            a = head + j
            yd = (1.0 - fr) * y[a - i : a - i + k - j] + fr * y[
                a - i - 1 : a - i - 1 + k - j
            ]
            y[a : a + k - j] = x[j:k] + gb * yd
            j = k
    g = np.sqrt((1.0 - np.abs(g)) * np.sqrt(1.0 - g * g))
    gain = np.repeat(g, FILTER_BLOCK)[:n]
    out = y[head:]
    return out * (gain[:, None] if x.ndim == 2 else gain)


def _allpass(x, cutoff, q, sr):
    return x - 2.0 * biquad(x, "bandpass", cutoff, q, sr)


def filter_response(kind: str, fc: float, res: float, f, sr: int = SR) -> np.ndarray:
    """`filt`'s complex response at the frequencies `f` for a steady cutoff
    and resonance (the drive's clip left out): the Filter panel's drawing."""
    f = np.asarray(f, dtype=np.float64)
    z1 = np.exp(-2j * np.pi * f / sr)

    def bq(k, q):
        b, a = biquad_coeffs(k, fc, q, sr)
        return (b[0] + b[1] * z1 + b[2] * z1**2) / (a[0] + a[1] * z1 + a[2] * z1**2)

    if kind == "none":
        return np.ones_like(z1)
    if kind == "ladder":
        return bq("lowpass", 0.707 + res * 6) * bq("lowpass", 0.707)
    if kind in ("lowpass", "highpass", "bandpass", "notch"):
        return bq(kind, 0.707 + res * 6)
    if kind in ("lowpass24", "highpass24"):
        return bq(kind[:-2], LP24_Q1) * bq(kind[:-2], LP24_Q2 + res * LP24_RES)
    if kind == "diode":
        return bq("lowpass", DIODE_Q + res * DIODE_RES) * bq("lowpass", DIODE_Q2)
    if kind == "phaser":
        ap = 1 - 2 * bq("bandpass", PHASER_Q + res * PHASER_RES)
        return 0.5 * (1 + ap**PHASER_STAGES)
    if kind == "peak":
        return 1 + (10 ** (PEAK_DB * res / 20) - 1) * bq("bandpass", PEAK_Q)
    if kind in ("comb_plus", "comb_minus"):
        g = (1.0 if kind == "comb_plus" else -1.0) * min(COMB_FB, max(0.0, res))
        d = sr / min(sr * 0.49, max(sr / COMB_MAX, fc))
        s = math.sqrt((1 - abs(g)) * math.sqrt(1 - g * g))
        return s / (1 - g * np.exp(-2j * np.pi * f * d / sr))
    raise ValueError(f"unknown filter type: {kind}")


def filt(x: np.ndarray, kind: str, cutoff, resonance, drive=1.0, sr: int = SR):
    """Any of the synth's filter types (params.FILTER_TYPES_ALL) on `x`.
    The first six are the calls `Synth.note` always made (the ladder; one
    SVF at q 0.707 + 6 res); the rest are built above."""
    res = np.asarray(resonance, dtype=np.float64)
    if kind == "none":
        return x
    if kind == "ladder":
        return ladder(x, cutoff, resonance, drive, sr)
    if kind in ("lowpass", "highpass", "bandpass", "notch"):
        return biquad(x, kind, cutoff, 0.707 + res * 6, sr)
    if kind in ("lowpass24", "highpass24"):
        k = kind[:-2]
        y = biquad(x, k, cutoff, LP24_Q1, sr)
        return biquad(y, k, cutoff, LP24_Q2 + res * LP24_RES, sr)
    if kind == "diode":
        y = biquad(x, "lowpass", cutoff, DIODE_Q + res * DIODE_RES, sr)
        return biquad(diode_clip(y, drive), "lowpass", cutoff, DIODE_Q2, sr)
    if kind == "phaser":
        y = x
        for _ in range(PHASER_STAGES):
            y = _allpass(y, cutoff, PHASER_Q + res * PHASER_RES, sr)
        return 0.5 * (x + y)
    if kind == "peak":
        g = np.repeat(10 ** (PEAK_DB * _blocks(res, len(x)) / 20) - 1, FILTER_BLOCK)
        g = g[: len(x)]
        bp = biquad(x, "bandpass", cutoff, PEAK_Q, sr)
        return x + bp * (g[:, None] if x.ndim == 2 else g)
    if kind in ("comb_plus", "comb_minus"):
        return comb(x, cutoff, resonance, 1.0 if kind == "comb_plus" else -1.0, sr)
    raise ValueError(f"unknown filter type: {kind}")


def shelf(
    x: np.ndarray,
    kind: str = "high",
    fc: float = 6000.0,
    gain_db: float = 3.0,
    slope: float = 0.7,
    sr: int = SR,
) -> np.ndarray:
    """Low/high shelving EQ — gentle tone shaping for mixes and busses."""
    b, a = shelf_coeffs(kind, fc, gain_db, slope, sr)
    return _lfilter(b, a, x)


def shelf_coeffs(
    kind: str = "high",
    fc: float = 6000.0,
    gain_db: float = 3.0,
    slope: float = 0.7,
    sr: int = SR,
):
    """One RBJ shelf's (b, a): `gain_db` at the far end of the band ("low":
    DC, "high": Nyquist), half of it (in dB) at `fc`, unity at the other
    end. `shelf()`'s own coefficients, by name, for whatever needs the
    numbers rather than a filtered signal (`effects.eq`, the live keys)."""
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * float(np.clip(fc, 20, sr * 0.49)) / sr
    cw, sw = np.cos(w0), np.sin(w0)
    alpha = sw / 2 * np.sqrt((A + 1 / A) * (1 / slope - 1) + 2)
    tsa = 2 * np.sqrt(A) * alpha
    if kind == "high":
        b = [
            A * ((A + 1) + (A - 1) * cw + tsa),
            -2 * A * ((A - 1) + (A + 1) * cw),
            A * ((A + 1) + (A - 1) * cw - tsa),
        ]
        a = [
            (A + 1) - (A - 1) * cw + tsa,
            2 * ((A - 1) - (A + 1) * cw),
            (A + 1) - (A - 1) * cw - tsa,
        ]
    else:
        b = [
            A * ((A + 1) - (A - 1) * cw + tsa),
            2 * A * ((A - 1) - (A + 1) * cw),
            A * ((A + 1) - (A - 1) * cw - tsa),
        ]
        a = [
            (A + 1) + (A - 1) * cw + tsa,
            -2 * ((A - 1) + (A + 1) * cw),
            (A + 1) + (A - 1) * cw - tsa,
        ]
    b = np.asarray(b) / a[0]
    a = np.asarray(a) / a[0]
    return b, a


def peaking_coeffs(fc: float, gain_db: float, q: float, sr: int = SR):
    """One RBJ peaking band's (b, a): exactly `gain_db` at `fc`, unity at DC
    and Nyquist, `q` = fc over the bandwidth between the half-gain (dB)
    points. Clipped like `biquad_coeffs`."""
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * float(np.clip(fc, 20.0, sr * 0.49)) / sr
    alpha = np.sin(w0) / (2 * max(q, 0.05))
    cw = np.cos(w0)
    b = np.array([1 + alpha * A, -2 * cw, 1 - alpha * A])
    a = np.array([1 + alpha / A, -2 * cw, 1 - alpha / A])
    return b / a[0], a / a[0]


def one_pole(x: np.ndarray, coeff: float) -> np.ndarray:
    """Smoothing filter, used for envelope followers and glide."""
    return _lfilter([1 - coeff], [1, -coeff], x)


def dc_block(x: np.ndarray, fc: float = 12.0, sr: int = SR) -> np.ndarray:
    """Strip DC and subsonic wander from a voice.

    A square of duty d carries a DC term of 2d-1, so pulse-width modulation
    walks the whole voice off centre: `stab_hoover` was putting 32.7% of its
    energy below 20 Hz. None of it is audible, and all of it is paid for out
    of the headroom the loop normalizer later takes off the music.

    The filter's state is initialised from the first sample, so its own
    settling never becomes the thump it was added to remove — a bare
    first-order highpass this low rings for ~80 ms when handed a step.
    """
    r = _dc_pole(fc, sr)
    native = kernel("dc_block")
    if native is not None and np.ndim(x) in (1, 2) and len(x):
        return native(np.ascontiguousarray(x, dtype=np.float64), r)
    return dc_block_numpy(x, fc, sr)


def _dc_pole(fc: float = 12.0, sr: int = SR) -> float:
    """`dc_block`'s pole."""
    return float(np.exp(-2 * np.pi * fc / sr))


def dc_block_numpy(x: np.ndarray, fc: float = 12.0, sr: int = SR) -> np.ndarray:
    r = _dc_pole(fc, sr)
    b = np.array([1.0, -1.0])
    a = np.array([1.0, -r])
    zi0 = lfilter_zi(b, a)
    if x.ndim == 2:
        out = np.empty_like(x, dtype=np.float64)
        for ch in range(x.shape[1]):
            out[:, ch], _ = lfilter(b, a, x[:, ch], zi=zi0 * x[0, ch])
        return out
    y, _ = lfilter(b, a, x, zi=zi0 * float(x[0]))
    return y
