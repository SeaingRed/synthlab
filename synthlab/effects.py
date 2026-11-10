"""
synthlab.effects — the outboard rack.

Every effect takes mono (n,) or stereo (n, 2) audio and returns the same
shape, so you can chain them freely:

    y = fx.chain(x, [fx.drive(2.0), fx.delay(0.375, 0.4), fx.reverb(2.5, 0.25)])
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from functools import lru_cache, partial

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy import fft as sp_fft
from scipy.ndimage import minimum_filter1d
from scipy.signal import lfilter_zi
from scipy.signal._sigtools import _linear_filter  # lfilter minus the dispatch

from ._native import kernel
from .core import (
    SR,
    _lfilter,
    _polyblep,
    as_array,
    biquad,
    biquad_coeffs,
    n_samples,
    one_pole,
    peaking_coeffs,
    shelf_coeffs,
    soft_clip,
    time_axis,
)
from .core import shelf as core_shelf

# ----------------------------------------------------------------------------
# mono / stereo plumbing
# ----------------------------------------------------------------------------


def to_stereo(x: np.ndarray) -> np.ndarray:
    if x.ndim == 2:
        return x
    return np.column_stack([x, x])


def is_stereo(x: np.ndarray) -> bool:
    return x.ndim == 2


def _per_channel(fn, x: np.ndarray, *a, **kw) -> np.ndarray:
    if is_stereo(x):
        return np.column_stack([fn(x[:, 0], *a, **kw), fn(x[:, 1], *a, **kw)])
    return fn(x, *a, **kw)


def chain(x: np.ndarray, effects: list) -> np.ndarray:
    """Run audio through a list of prepared effects, in order."""
    for f in effects:
        x = f(x)
    return x


# ----------------------------------------------------------------------------
# saturation / destruction
# ----------------------------------------------------------------------------


DRIVE_MODES = ("soft", "hard", "fuzz", "tube")  # the rack's `mode` 0..3
FUZZ_BIAS = 0.5  # fuzz: the shaper's offset (its asymmetry)
FUZZ_GAIN = 2.0  # fuzz: extra gain into the shaper on top of `amount`
TUBE_EVEN = 0.2  # tube: the squared term's share (the even harmonics)


def _per_sample(v, x: np.ndarray):
    """A scalar, or a per-sample array shaped to broadcast against `x`
    (mono or stereo): a lane's values."""
    v = np.asarray(v, dtype=np.float64)
    return v[:, None] if v.ndim == 1 and x.ndim == 2 else v


def drive_shape(x, amount=2.0, mode="soft"):
    """The drive's wet signal, sample by sample (memoryless, causal; no
    whole-buffer normalisation). Every mode maps silence to silence and a
    full-scale input to a peak of about 1:

    - soft: `core.soft_clip` (tanh(a x) / tanh(a)), the unit's sound since
      the start and its default — the same call, so the same samples.
    - hard: clip(a x) at +-1: a full-scale input peaks at 1, as soft's does.
    - fuzz: tanh of the signal driven FUZZ_GAIN x harder and biased by
      FUZZ_BIAS, less tanh(bias) (silence stays at 0), over the negative
      side's reach: the negative half runs to -1, the positive one is
      squashed flat at 0.37 — half-rectified. Asymmetric, so even
      harmonics and some signal-dependent DC (a note's, never silence's).
    - tube: soft's curve s plus TUBE_EVEN s^2, over 1 + TUBE_EVEN: gentle,
      the positive half to 1, the negative one rounder (to -0.67).

    `amount` may be a per-sample array (a lane) in every mode."""
    if mode == "soft":
        return soft_clip(x, amount)
    a = _per_sample(amount, x)
    if mode == "hard":
        return np.clip(x * a, -1.0, 1.0)
    if mode == "fuzz":
        g, b = a * FUZZ_GAIN, FUZZ_BIAS
        tb = np.tanh(b)
        return (np.tanh(x * g + b) - tb) / (np.tanh(g - b) + tb)
    if mode == "tube":
        a = np.maximum(a, 1e-6)
        s = np.tanh(x * a) / np.tanh(a)
        return (s + TUBE_EVEN * s * s) / (1.0 + TUBE_EVEN)
    raise ValueError(f"drive mode {mode!r}: one of {DRIVE_MODES}")


def _drive(x, amount=2.0, mix=1.0, mode="soft"):
    wet = drive_shape(x, amount, mode)
    return x * (1 - mix) + wet * mix


def _clip(x, drive=6.0, ceiling=-1.0, soft=0.3, mix=1.0):
    """A clipper: `drive` dB of gain, then nothing past `ceiling` dB. With
    `soft` = 0 it is a hard clip at the ceiling; above 0 the last `soft`
    share of the ceiling is a knee — linear up to t = c (1 - soft), then
    t + (c - t) tanh((|u| - t) / (c - t)), which meets the line with the
    same slope and never reaches c. Per sample (memoryless, causal),
    stereo-safe; `drive` and `mix` may be per-sample arrays (lanes). The
    dry under a `mix` below 1 is not clipped."""
    u = x * 10 ** (_per_sample(drive, x) / 20)
    c = 10 ** (float(ceiling) / 20)
    k = min(1.0, max(0.0, float(soft)))
    if k <= 0.0:
        wet = np.clip(u, -c, c)
    else:
        t, room = c * (1 - k), c * k
        mag = np.abs(u)
        over = t + room * np.tanh((mag - t) / room)
        wet = np.where(mag > t, np.sign(u) * over, u)
    return x * (1 - mix) + wet * mix


def _bitcrush(x, bits=8, downsample=4):
    levels = 2 ** int(bits)
    y = np.round(x * levels) / levels
    if downsample > 1:
        d = int(downsample)
        n = len(y)
        idx = (np.arange(n) // d) * d
        y = y[idx] if y.ndim == 1 else y[idx, :]
    return y


def _fold(x, amount=1.5, mix=1.0):
    """Wavefolding — harsh, west-coast-synth character. `mix` blends the
    folded signal back against dry, which is how it stays usable on a lead
    instead of only ever sounding shredded."""
    wet = np.sin(x * amount * np.pi) * 0.8
    return x * (1 - mix) + wet * mix


def drive(amount=2.0, mix=1.0, mode="soft"):
    return partial(_drive, amount=amount, mix=mix, mode=mode)


def clip(drive=6.0, ceiling=-1.0, soft=0.3, mix=1.0):
    return partial(_clip, drive=drive, ceiling=ceiling, soft=soft, mix=mix)


def bitcrush(bits=8, downsample=4):
    return partial(_bitcrush, bits=bits, downsample=downsample)


def fold(amount=1.5, mix=1.0):
    return partial(_fold, amount=amount, mix=mix)


def _ringmod(x, freq=220.0, mix=1.0, sr=SR):
    """Multiply by a sine. The carrier vanishes and its sum/difference
    sidebands take its place, which is why this reads as metallic and
    inharmonic rather than as distortion."""
    car = np.sin(2 * np.pi * float(freq) * time_axis(len(x), sr))
    wet = x * (car[:, None] if is_stereo(x) else car)
    return x * (1 - mix) + wet * mix


def ringmod(freq=220.0, mix=1.0):
    return partial(_ringmod, freq=freq, mix=mix)


# Feedback below 1.0 keeps the comb a decaying resonator; the UI clamps to
# COMB_MAX_FEEDBACK so a dragged slider can never build a runaway.
COMB_MAX_FEEDBACK = 0.95


def _comb_mono(mono, delay: int, feedback: float):
    # y[n] = x[n] + fb * y[n-d]: peaks every sr/d Hz, fundamental at the tune.
    # That is the feedback delay line exactly (the all-pole lfilter's zero
    # taps pass the state through unrounded), and scipy ran it in O(n * d).
    native = kernel("feedback_delay")
    if native is not None and delay >= 1:
        return native(
            np.ascontiguousarray(mono, dtype=np.float64), delay, float(feedback)
        )
    a = np.zeros(delay + 1)
    a[0], a[delay] = 1.0, -feedback
    return _lfilter([1.0], a, mono)


def _comb(x, tune=220.0, feedback=0.8, mix=0.5, sr=SR):
    """Tuned resonator: a feedback delay one period long. Sweeping the tune
    gives the throaty, vowel-ish, robotic voice this rack had no way to make."""
    delay = max(1, int(round(sr / max(float(tune), 1e-6))))
    fb = float(np.clip(feedback, 0.0, COMB_MAX_FEEDBACK))
    wet = _per_channel(_comb_mono, x, delay, fb)
    # Broadband gain of this comb is 1/sqrt(1 - fb^2); normalise by that so
    # the level stays sane as feedback rises. Normalising by the *peak* gain
    # (1 - fb) instead would flatten the teeth to unity and remove the
    # resonance that makes it a comb at all.
    wet *= np.sqrt(1.0 - fb * fb)
    return x * (1 - mix) + wet * mix


def comb(tune=220.0, feedback=0.8, mix=0.5):
    return partial(_comb, tune=tune, feedback=feedback, mix=mix)


# ----------------------------------------------------------------------------
# EQ / filtering as an effect
# ----------------------------------------------------------------------------


def _filt(x, kind="lowpass", cutoff=1000.0, q=0.707, sr=SR):
    return _per_channel(biquad, x, kind, cutoff, q, sr)


def lowpass(cutoff=1000.0, q=0.707):
    return partial(_filt, kind="lowpass", cutoff=cutoff, q=q)


def highpass(cutoff=200.0, q=0.707):
    return partial(_filt, kind="highpass", cutoff=cutoff, q=q)


def bandpass(cutoff=800.0, q=2.0):
    return partial(_filt, kind="bandpass", cutoff=cutoff, q=q)


def _shelf(x, kind="high", fc=6000.0, gain_db=3.0, slope=0.7, sr=SR):
    return _per_channel(core_shelf, x, kind, fc, gain_db, slope, sr)


def air(fc=7000.0, gain_db=3.0):
    """High shelf — adds sparkle and openness to a mix."""
    return partial(_shelf, kind="high", fc=fc, gain_db=gain_db)


def weight(fc=140.0, gain_db=2.0):
    """Low shelf — adds body and warmth."""
    return partial(_shelf, kind="low", fc=fc, gain_db=gain_db)


# The parametric EQ's fixed shelf corners and slope: the low shelf turns
# under the bass's body, the high one over the presence band, and the swept
# mid band has everything between them. 0.7 is `core.shelf`'s own gentle
# slope (no overshoot at any gain).
EQ_LOW_HZ = 120.0
EQ_HIGH_HZ = 8000.0
EQ_SLOPE = 0.7


def eq_bands(
    low=0.0,
    mid_freq=1000.0,
    mid_gain=0.0,
    mid_q=1.0,
    high=0.0,
    low_hz=EQ_LOW_HZ,
    high_hz=EQ_HIGH_HZ,
    slope=EQ_SLOPE,
    sr=SR,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """The EQ's biquads, (b, a) each, in the order they run: low shelf,
    mid peak, high shelf — **only the bands whose gain isn't 0 dB**. A 0 dB
    RBJ band is unity on paper (its zeros sit on its poles) but not in
    floating point, so it is left out rather than run: that is what makes
    the unit at its defaults an exact bypass. The live keys skip the same
    bands (static/eq-worklet.js)."""
    bands = []
    if low != 0:
        bands.append(shelf_coeffs("low", low_hz, low, slope, sr))
    if mid_gain != 0:
        bands.append(peaking_coeffs(mid_freq, mid_gain, mid_q, sr))
    if high != 0:
        bands.append(shelf_coeffs("high", high_hz, high, slope, sr))
    return bands


def _eq(x, sr=SR, **bands):
    if any(np.ndim(v) for v in bands.values()):
        return _eq_swept(x, sr=sr, **bands)
    for b, a in eq_bands(sr=sr, **bands):
        x = _lfilter(b, a, x)
    return x


def _eq_swept(
    x,
    low=0.0,
    mid_freq=1000.0,
    mid_gain=0.0,
    mid_q=1.0,
    high=0.0,
    low_hz=EQ_LOW_HZ,
    high_hz=EQ_HIGH_HZ,
    slope=EQ_SLOPE,
    sr=SR,
    block=64,
):
    """`_eq` with a gain (or the mid band's frequency) that moves: any of
    `low`, `mid_gain`, `high`, `mid_freq` a per-sample array (a song's
    automation lane). Each band is the state-variable filter `core.biquad`
    sweeps — its state is its integrators, so it survives a coefficient
    change, where the RBJ biquad's direct-form state does not — with the
    band's gain mixed from its outputs (Simper's bell and shelves):

        bell        x + (A^2 - 1) bp                     k = 1 / (Q A)
        low shelf   x + (A - 1) bp + (A^2 - 1) lp        g / sqrt(A)
        high shelf  A^2 x + (1 - A) A bp + (1 - A^2) lp  g * sqrt(A)

    (A = 10^(dB / 40), bp = k * band, the 0 dB-peak bandpass `biquad`
    gives). Per block these are the transfer functions `eq_bands` builds —
    both are the prewarped bilinear transform of RBJ's analog prototypes —
    so a gain that stands still is the static EQ (to rounding), and one
    that moves is the static EQ of each 64-sample block's first value. A
    band whose gain is 0 throughout is left out, as `eq_bands` leaves it."""
    n = len(x)
    col = (lambda a: a[:, None]) if x.ndim == 2 else (lambda a: a)

    def held(v):  # the block's first value, as `biquad` reads fc and q
        return np.repeat(as_array(v, n)[::block], block)[:n]

    def moving(v) -> bool:
        return bool(np.any(np.asarray(v) != 0))

    def shelf(x, gain_db, hz, kind):
        a = 10 ** (held(gain_db) / 40)
        q = 1.0 / np.sqrt((a + 1 / a) * (1 / slope - 1) + 2)
        g = np.tan(np.pi * float(np.clip(hz, 20, sr * 0.49)) / sr)
        g = g / np.sqrt(a) if kind == "low" else g * np.sqrt(a)
        fc = np.arctan(g) * sr / np.pi
        bp = biquad(x, "bandpass", fc, q, sr, block)
        lp = biquad(x, "lowpass", fc, q, sr, block)
        if kind == "low":
            return x + col(a - 1) * bp + col(a * a - 1) * lp
        return col(a * a) * x + col((1 - a) * a) * bp + col(1 - a * a) * lp

    if moving(low):
        x = shelf(x, low, low_hz, "low")
    if moving(mid_gain):
        a = 10 ** (held(mid_gain) / 40)
        q = np.maximum(mid_q, 0.05) * a
        x = x + col(a * a - 1) * biquad(x, "bandpass", held(mid_freq), q, sr, block)
    if moving(high):
        x = shelf(x, high, high_hz, "high")
    return x


def eq(
    low=0.0,
    mid_freq=1000.0,
    mid_gain=0.0,
    mid_q=1.0,
    high=0.0,
    low_hz=EQ_LOW_HZ,
    high_hz=EQ_HIGH_HZ,
    slope=EQ_SLOPE,
):
    """Three-band parametric EQ: a low shelf and a high shelf at fixed
    corners (gains in dB) around one swept peaking band (`mid_freq` Hz,
    `mid_gain` dB, `mid_q`). RBJ biquads in series through the same short
    `lfilter` every static filter here runs, so it is causal, linear and
    time-invariant: live-playable, and safe to wrap-tile. Every gain at 0
    returns its input untouched."""
    return partial(
        _eq,
        low=low,
        mid_freq=mid_freq,
        mid_gain=mid_gain,
        mid_q=mid_q,
        high=high,
        low_hz=low_hz,
        high_hz=high_hz,
        slope=slope,
    )


# ----------------------------------------------------------------------------
# delay
# ----------------------------------------------------------------------------


def _delay_line(x: np.ndarray, delay_s: float, feedback: float, sr: int) -> np.ndarray:
    """Feedback delay: y[i] = x[i] + feedback * y[i - d]. `feedback` may be
    a per-sample array (a song's automation lane): y[i] = x[i] +
    feedback[i] * y[i - d], sample for sample, on the NumPy path."""
    native = kernel("feedback_delay")
    if native is None or np.ndim(feedback):
        return _delay_line_numpy(x, delay_s, feedback, sr)
    d = max(1, n_samples(delay_s, sr))
    return native(np.ascontiguousarray(x, dtype=np.float64), d, float(feedback))


def _delay_line_numpy(
    x: np.ndarray, delay_s: float, feedback: float, sr: int
) -> np.ndarray:
    """Feedback delay, computed a delay-length block at a time (fast in NumPy)."""
    d = max(1, n_samples(delay_s, sr))
    y = np.array(x, dtype=np.float64, copy=True)
    n = len(x)
    swept = np.ndim(feedback) > 0
    for i in range(d, n, d):
        j = min(i + d, n)
        fb = feedback[i:j] if swept else feedback
        y[i:j] += fb * y[i - d : i - d + (j - i)]
    return y


def _delay(
    x, time=0.375, feedback=0.35, mix=0.3, damping=4000.0, ping_pong=False, sr=SR
):
    st = to_stereo(x)
    if ping_pong:
        left = _delay_line(st[:, 0], time * 2, feedback, sr)
        right_in = np.concatenate([np.zeros(n_samples(time, sr)), st[:, 1]])[: len(x)]
        right = _delay_line(right_in, time * 2, feedback, sr)
        wet = np.column_stack([left, right])
    else:
        wet = np.column_stack(
            [
                _delay_line(st[:, 0], time, feedback, sr),
                _delay_line(st[:, 1], time * 1.02, feedback, sr),
            ]
        )
    wet = np.column_stack(
        [
            biquad(wet[:, 0], "lowpass", damping, 0.707, sr),
            biquad(wet[:, 1], "lowpass", damping, 0.707, sr),
        ]
    )
    out = st * (1 - mix) + wet * mix
    return out if is_stereo(x) else out.mean(axis=1)


def delay(time=0.375, feedback=0.35, mix=0.3, damping=4000.0, ping_pong=False):
    return partial(
        _delay,
        time=time,
        feedback=feedback,
        mix=mix,
        damping=damping,
        ping_pong=ping_pong,
    )


# ----------------------------------------------------------------------------
# reverb  (convolution with a synthesised impulse response)
# ----------------------------------------------------------------------------


def impulse_response(
    decay=2.0, size=0.8, damping=0.35, predelay=0.02, sr=SR, seed=7
) -> np.ndarray:
    """
    Build a stereo room IR: exponentially decaying, lowpass-shaped noise with
    a few early reflections. Cheap, controllable, and sounds convincingly like
    a real space once convolved.
    """
    rng = np.random.default_rng(seed)
    n = n_samples(decay * 1.5, sr)
    t = time_axis(n, sr)
    env = np.exp(-t * (5.0 / max(decay, 0.05)))
    ir = rng.standard_normal((n, 2)) * env[:, None]

    # early reflections give the room its size
    for k in range(1, 9):
        d = n_samples(size * 0.011 * k * (0.8 + 0.4 * rng.random()), sr)
        if d < n:
            g = 0.55 / k
            ir[d:, 0] += ir[: n - d, 0] * g * rng.choice([-1, 1])
            ir[d:, 1] += ir[: n - d, 1] * g * rng.choice([-1, 1])

    cut = 1200 + (1 - damping) * 12000
    ir[:, 0] = biquad(ir[:, 0], "lowpass", cut, 0.707, sr)
    ir[:, 1] = biquad(ir[:, 1], "lowpass", cut, 0.707, sr)
    ir[:, 0] = biquad(ir[:, 0], "highpass", 90, 0.707, sr)
    ir[:, 1] = biquad(ir[:, 1], "highpass", 90, 0.707, sr)

    pre = n_samples(predelay, sr)
    if pre:
        ir = np.vstack([np.zeros((pre, 2)), ir])
    ir /= np.max(np.abs(ir)) or 1.0
    return ir


# The wet path's gain: 1/||ir|| (unit energy per channel), less 1.5 dB —
# the median of the old peak-matched gain over the factory sounds.
_REVERB_WET = 10 ** (-1.5 / 20)


def reverb_ir(decay=2.0, size=0.8, damping=0.35, predelay=0.02, sr=SR) -> np.ndarray:
    """The room the reverb convolves with, wet gain included: the live
    keys' ConvolverNode loads exactly this (GET /fx/ir.wav). The wet level
    used to be matched to the dry signal's peak over the whole buffer, so a
    loud hit anywhere in a loop turned the room down on every other note —
    and nothing realtime could know it at the first sample."""
    ir = impulse_response(decay, size, damping, predelay, sr)
    energy = float(np.sqrt(np.mean(np.sum(ir**2, axis=0)))) or 1.0
    return ir * (_REVERB_WET / energy)


@lru_cache(maxsize=16)
def _room(decay, size, damping, predelay, sr) -> np.ndarray:
    """`reverb_ir`, cached for the render path: the room is a pure function
    of its arguments (its noise is seeded), and building one cost ~20 ms on
    every reverb of every render. Read-only because the renders share it.
    Deliberately not behind GET /fx/ir.wav, whose query picks any rate up
    to 192 kHz: a cache there could be walked to gigabytes."""
    ir = reverb_ir(decay, size, damping, predelay, sr)
    ir.flags.writeable = False
    return ir


# The room spectra kept, by (kind, the IR's arguments, FFT length): at most
# _IR_SPECTRA_BYTES in all, the most recently used first, and none over
# _IR_SPECTRUM_MAX_BYTES (computed and dropped: a five-minute export's whole
# transform is ~210 MB). A loop keeps its length from edit to edit, and a
# song's sections share theirs — 2 to 3 asks per transform over the four
# starters' exports, a master reverb running twice over a section whose
# ring into the next differs — so one cached transform saves one of the
# convolution's three.
_IR_SPECTRA_BYTES = 128 << 20
_IR_SPECTRUM_MAX_BYTES = 40 << 20
_IR_SPECTRA: OrderedDict[tuple, np.ndarray] = OrderedDict()
_IR_SPECTRA_LOCK = threading.Lock()


def _ir_spectrum(kind: str, args: tuple, ir: np.ndarray, nfft: int) -> np.ndarray:
    """`rfft(ir, nfft)` of the cached IR `kind(*args)` — what `fftconvolve`
    recomputed on every call — from the cache when it has it: the same
    numbers either way (read-only: renders share it)."""
    key = (kind, args, int(nfft))
    with _IR_SPECTRA_LOCK:
        spec = _IR_SPECTRA.get(key)
        if spec is not None:
            _IR_SPECTRA.move_to_end(key)
            return spec
    spec = sp_fft.rfft(ir, nfft, axis=0)
    if spec.nbytes > _IR_SPECTRUM_MAX_BYTES:
        return spec
    spec.flags.writeable = False
    with _IR_SPECTRA_LOCK:
        _IR_SPECTRA[key] = spec
        total = sum(v.nbytes for v in _IR_SPECTRA.values())
        while total > _IR_SPECTRA_BYTES:
            _k, dropped = _IR_SPECTRA.popitem(last=False)
            total -= dropped.nbytes
    return spec


def _room_spectrum(room: tuple, nfft: int) -> np.ndarray:
    """The reverb room's spectrum at one FFT length (`_ir_spectrum`)."""
    return _ir_spectrum("reverb", room, _room(*room), nfft)


def _reverb(x, decay=2.0, mix=0.25, size=0.8, damping=0.35, predelay=0.02, sr=SR):
    st = to_stereo(x)
    room = (float(decay), float(size), float(damping), float(predelay), int(sr))
    # `fftconvolve(st[:, c], ir[:, c])[:n]` per channel, as its own calls
    # make it — the same length, the same rfft/irfft — so the result is
    # identical; both channels in one batched transform, the room's own
    # half from the cache
    n = len(st)
    nfft = sp_fft.next_fast_len(n + len(_room(*room)) - 1, True)
    spec = sp_fft.rfft(st, nfft, axis=0) * _room_spectrum(room, nfft)
    wet = sp_fft.irfft(spec, nfft, axis=0)[:n]
    out = st * (1 - mix) + wet * mix
    return out if is_stereo(x) else out.mean(axis=1)


def reverb(decay=2.0, mix=0.25, size=0.8, damping=0.35, predelay=0.02):
    return partial(
        _reverb, decay=decay, mix=mix, size=size, damping=damping, predelay=predelay
    )


# ----------------------------------------------------------------------------
# modulation: chorus / flanger / phaser-ish
# ----------------------------------------------------------------------------


def _mod_delay(mono: np.ndarray, base_ms, depth_ms, rate, sr, phase=0.0, feedback=0.0):
    native = kernel("swept_read")
    if native is None:
        return _mod_delay_numpy(mono, base_ms, depth_ms, rate, sr, phase, feedback)
    src = mono if feedback == 0 else _delay_line(mono, base_ms * 1e-3, feedback, sr)
    return native(
        np.ascontiguousarray(src, dtype=np.float64),
        float(base_ms),
        float(depth_ms),
        float(rate),
        float(phase),
        float(sr),
    )


def _mod_delay_numpy(
    mono: np.ndarray, base_ms, depth_ms, rate, sr, phase=0.0, feedback=0.0
):
    n = len(mono)
    t = time_axis(n, sr)
    d = (base_ms + depth_ms * np.sin(2 * np.pi * rate * t + phase)) * 1e-3 * sr
    # never ahead of now: a flanger swept deeper than its base delay (depth
    # 6 ms on a 3 ms base) used to read up to 3 ms of the future, which the
    # live keys can't; the sweep now bottoms out at zero delay
    idx = np.arange(n) - np.maximum(d, 0.0)
    idx = np.clip(idx, 0, n - 1)
    src = mono if feedback == 0 else _delay_line(mono, base_ms * 1e-3, feedback, sr)
    return np.interp(idx, np.arange(n), src)


def _chorus(x, rate=0.6, depth=3.0, base=18.0, mix=0.45, voices=3, sr=SR):
    st = to_stereo(x)
    out = np.zeros_like(st)
    for v in range(voices):
        ph = 2 * np.pi * v / voices
        out[:, 0] += _mod_delay(st[:, 0], base, depth, rate, sr, ph)
        out[:, 1] += _mod_delay(st[:, 1], base * 1.1, depth, rate * 1.07, sr, ph + 1.1)
    out /= voices
    res = st * (1 - mix) + out * mix
    return res if is_stereo(x) else res.mean(axis=1)


def _flanger(x, rate=0.25, depth=2.5, base=3.0, feedback=0.5, mix=0.5, sr=SR):
    st = to_stereo(x)
    left = _mod_delay(st[:, 0], base, depth, rate, sr, 0.0, feedback)
    right = _mod_delay(st[:, 1], base, depth, rate, sr, np.pi / 2, feedback)
    wet = np.column_stack([left, right])
    res = st * (1 - mix) + wet * mix
    return res if is_stereo(x) else res.mean(axis=1)


def chorus(rate=0.6, depth=3.0, base=18.0, mix=0.45, voices=3):
    return partial(_chorus, rate=rate, depth=depth, base=base, mix=mix, voices=voices)


def flanger(rate=0.25, depth=2.5, base=3.0, feedback=0.5, mix=0.5):
    return partial(
        _flanger, rate=rate, depth=depth, base=base, feedback=feedback, mix=mix
    )


_ALLPASS_BLOCK = 64


def _allpass_gs(centers: np.ndarray, sr: int) -> np.ndarray:
    """The allpass coefficient of each block of `_allpass_sweep`."""
    fc = np.clip(centers[::_ALLPASS_BLOCK], 40.0, sr * 0.45)
    tans = np.tan(np.pi * fc / sr)
    return (tans - 1.0) / (tans + 1.0)


def _allpass_sweep(mono: np.ndarray, centers: np.ndarray, stages: int, sr: int):
    """Cascade of first-order allpasses whose center follows `centers`
    (Hz, per-sample). Coefficients update per 64-sample block, state
    carried across blocks. One native call (bit-identical to the twin,
    tests/test_native.py): the twin calls scipy once a block a stage, which
    was half of what a song with two phasers took to render."""
    native = kernel("allpass_sweep")
    if native is None:
        return _allpass_sweep_numpy(mono, centers, stages, sr)
    x = np.ascontiguousarray(mono, dtype=np.float64)
    return native(x, _allpass_gs(centers, sr), int(stages), _ALLPASS_BLOCK)


def _allpass_sweep_numpy(mono: np.ndarray, centers: np.ndarray, stages: int, sr: int):
    """The reference. Calls scipy's C filter directly: the public `lfilter`
    wrapper's array-API dispatch was ~75% of this loop's cost, and the
    output is identical (tests/test_effects_mod.py pins that)."""
    block = _ALLPASS_BLOCK
    n = len(mono)
    gs = _allpass_gs(centers, sr)
    # _linear_filter wants one float dtype across b, a, x and zi
    y = np.ascontiguousarray(mono, dtype=np.float64)
    for _ in range(stages):
        out = np.empty(n)
        zi = np.zeros(1)
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        for k, i in enumerate(range(0, n, block)):
            j = min(i + block, n)
            a[1] = b[0] = gs[k]
            out[i:j], zi = _linear_filter(b, a, y[i:j], -1, zi)
        y = out
    return y


def _phaser(x, rate=0.4, depth=0.7, stages=6, mix=0.5, sr=SR):
    """Classic swept-allpass phaser; right channel's LFO runs in
    quadrature so the sweep breathes across the stereo field."""
    if mix <= 0:
        return x
    st = to_stereo(x)
    n = len(st)
    t = time_axis(n, sr)
    lo, hi = 300.0, 300.0 + 2200.0 * depth
    mid, span = (hi + lo) / 2, (hi - lo) / 2
    sweep_l = mid + span * np.sin(2 * np.pi * rate * t)
    sweep_r = mid + span * np.sin(2 * np.pi * rate * t + np.pi / 2)
    wet = np.column_stack(
        [
            _allpass_sweep(st[:, 0], sweep_l, stages, sr),
            _allpass_sweep(st[:, 1], sweep_r, stages, sr),
        ]
    )
    # phaser notches come from summing dry + allpassed
    res = st * (1 - mix) + 0.5 * (st + wet) * mix
    return res if is_stereo(x) else res.mean(axis=1)


def phaser(rate=0.4, depth=0.7, stages=6, mix=0.5):
    return partial(_phaser, rate=rate, depth=depth, stages=stages, mix=mix)


def _vinyl(x, crackle=0.4, hiss=0.15, wow=0.3, warmth=0.5, sr=SR):
    """Old-record channel: sparse crackle + hiss bed + slow pitch wow +
    top-end rolloff. Deterministic (seeded) so cached renders are stable."""
    st = to_stereo(x)
    n = len(st)
    rng = np.random.default_rng(1972)

    out = st
    if wow > 0:
        depth_ms = 0.6 * wow
        out = np.column_stack(
            [
                _mod_delay(out[:, 0], 4.0, depth_ms, 0.6, sr, 0.0),
                _mod_delay(out[:, 1], 4.0, depth_ms, 0.6, sr, 0.3),
            ]
        )
    if warmth > 0:
        out = _per_channel(core_shelf, out, "high", 7500.0, -6.0 * warmth)

    bed = np.zeros(n)
    if crackle > 0:
        pops = rng.random(n) < (crackle * 8.0 / sr * 60)  # ~pops per second
        amps = rng.uniform(0.05, 0.35, size=n) * pops
        signs = rng.choice([-1.0, 1.0], size=n)
        clicks = amps * signs
        bed += _per_channel(lambda m: biquad(m, "lowpass", 5200.0), clicks)
    if hiss > 0:
        bed += biquad(rng.standard_normal(n), "lowpass", 4500.0) * 0.006 * hiss * 10
    if np.any(bed):
        out = out + np.column_stack([bed, bed])
    return out if is_stereo(x) else out.mean(axis=1)


def vinyl(crackle=0.4, hiss=0.15, wow=0.3, warmth=0.5):
    return partial(_vinyl, crackle=crackle, hiss=hiss, wow=wow, warmth=warmth)


def _gate(x, pattern="x.x.x.x.", bpm=124.0, depth=1.0, smooth=0.004, sr=SR):
    """Trance gate: chop sustained audio with a 16th-note step pattern.
    `pattern` is 'x'/'.' per 16th; smooth is the attack/release ramp.
    `depth` may be a per-sample array (a song's FX lane): the gate's
    envelope is computed once and the depth scales it sample by sample."""
    if not np.any(np.asarray(depth) > 0) or not pattern:
        return x
    st = to_stereo(x)
    n = len(st)
    step_n = max(1, int(round(60.0 / bpm / 4.0 * sr)))  # a 16th note
    steps = np.array([1.0 if c == "x" else 0.0 for c in pattern])
    env = np.repeat(steps, step_n)
    reps = int(np.ceil(n / len(env))) if len(env) else 1
    env = np.tile(env, max(1, reps))[:n]
    win = max(1, int(smooth * sr))
    kernel = np.hanning(2 * win + 1)
    env = np.convolve(env, kernel / kernel.sum(), mode="same")
    gained = 1.0 - depth * (1.0 - env)
    res = st * gained[:, None]
    return res if is_stereo(x) else res.mean(axis=1)


def gate(pattern="x.x.x.x.", bpm=124.0, depth=1.0, smooth=0.004):
    return partial(_gate, pattern=pattern, bpm=bpm, depth=depth, smooth=smooth)


# beat-repeat slice lengths in beats: 1/4, 1/8, 1/16, 1/32
REPEAT_GRID_BEATS = (1.0, 0.5, 0.25, 0.125)
_REPEAT_FADE = 0.0015  # seconds: the declick ramp at every slice edge


def _repeat_roll(seed: int, step: int) -> float:
    """The chance roll of one grid step: a pure function of (seed, step),
    so the same step decides the same way on every render and every
    repeat of a loop (the drum machine's humanise rule)."""
    return float(np.random.default_rng([int(seed) & 0xFFFFFFFF, int(step)]).random())


def _ramp(n: int, fade: int) -> np.ndarray:
    """A 0 -> 1 -> 0 declick envelope `n` long with `fade`-sample
    raised-cosine edges (shorter edges when `n` is short)."""
    env = np.ones(n)
    f = min(fade, n // 2)
    if f > 0:
        edge = 0.5 - 0.5 * np.cos(np.pi * (np.arange(f) + 0.5) / f)
        env[:f] = edge
        env[n - f :] = edge[::-1]
    return env


def _beat_repeat(
    x,
    bpm=124.0,
    grid=2,
    repeats=3,
    chance=1.0,
    pattern="............x...",
    pitch=0.0,
    decay=0.0,
    gate=1.0,
    mix=1.0,
    period=4.0,
    seed=0,
    sr=SR,
):
    """Beat repeat / stutter. On each 16th step the pattern marks ('x',
    and the step's seeded roll under `chance`), the next `grid` slice
    (REPEAT_GRID_BEATS) plays as written and is then repeated `repeats`
    times in its place, each repeat `pitch` semitones lower than the one
    before (read slower, so it drops like a slowed tape), `decay` quieter
    and cut to `gate` of its length. A positive `pitch` reads faster and
    runs out of slice early: the repeat ends there, faded like any other
    (it used to read on past the capture — an IndexError).

    Loop-safe by construction: the pattern and the rolls restart every
    `period` beats (fxrack passes the gcd of the synth and drum loops, so
    the period divides every stem's own loop), and a repeat never runs
    past the end of its period. The effect is then a periodic time remap,
    which commutes with `_wrap_tile`'s tail fold: running it on a stem's
    un-tiled render and folding the tail gives what a looping player
    would hear from the effect on the loop itself. Causal too: a repeat
    only reads its own slice, which has already played.

    `mix` may be a per-sample array (a song's FX lane): which steps fire
    never depends on it; each output sample blends the dry signal and the
    repeats by the mix at that sample, the arithmetic of the scalar unit."""
    swept = np.ndim(mix) > 0
    if swept:
        mix = np.asarray(mix, dtype=np.float64)
    if not np.any(mix > 0) or repeats < 1 or "x" not in pattern or chance <= 0:
        return x
    st = to_stereo(np.asarray(x, dtype=np.float64))
    n = len(st)
    step = 60.0 / float(bpm) / 4.0 * sr  # a 16th, in (fractional) samples
    per = max(1, int(round(float(period) * 4.0)))  # 16ths per period
    s_len = max(8, int(round(REPEAT_GRID_BEATS[int(grid) % 4] * 60.0 / bpm * sr)))
    fade = max(1, int(_REPEAT_FADE * sr))
    reps = int(repeats)
    # zero-padded source: a slice near the buffer end reads silence past it
    src = np.vstack([st, np.zeros((s_len + 2, 2))])
    out = st.copy()
    rolls: dict[int, float] = {}
    busy = 0  # a step inside a running repeat can't start another
    for k in range(int(np.ceil(n / step)) + 1):
        t0 = int(round(k * step))
        if t0 >= n:
            break
        kk = k % per
        if t0 < busy or pattern[kk % len(pattern)] != "x":
            continue
        if chance < 1.0:
            if kk not in rolls:
                rolls[kk] = _repeat_roll(seed, kk)
            if rolls[kk] >= chance:
                continue
        end = min(n, int(round((k - kk + per) * step)))  # the period's end
        start = t0 + s_len
        if start >= end:
            continue
        busy = min(end, start + reps * s_len)
        cap = src[t0 : t0 + s_len + 1]
        wet = np.zeros((busy - start, 2))
        for r in range(1, reps + 1):
            a = (r - 1) * s_len
            if a >= len(wet):
                break
            seg = min(s_len, len(wet) - a)
            heard = max(1, min(seg, int(round(float(gate) * s_len))))
            rate = 2.0 ** (float(pitch) * r / 12.0)
            if rate > 1.0:  # no further than the slice it captured
                heard = max(1, min(heard, int(s_len / rate)))
            pos = np.arange(heard) * rate
            lo = pos.astype(np.int64)
            frac = (pos - lo)[:, None]
            piece = cap[lo] * (1.0 - frac) + cap[lo + 1] * frac
            gain = (1.0 - float(decay)) ** r
            wet[a : a + heard] = piece * (gain * _ramp(heard, fade))[:, None]
        # the dry signal hands over to the repeats (and back) over one fade
        if swept:
            mx = mix[start:busy]
            m = _ramp(busy - start, fade) * mx
            out[start:busy] = st[start:busy] * (1.0 - m)[:, None] + wet * mx[:, None]
            continue
        m = _ramp(busy - start, fade) * float(mix)
        out[start:busy] = st[start:busy] * (1.0 - m)[:, None] + wet * float(mix)
    return out if is_stereo(x) else out.mean(axis=1)


def beat_repeat(
    bpm=124.0,
    grid=2,
    repeats=3,
    chance=1.0,
    pattern="............x...",
    pitch=0.0,
    decay=0.0,
    gate=1.0,
    mix=1.0,
    period=4.0,
    seed=0,
):
    return partial(
        _beat_repeat,
        bpm=bpm,
        grid=grid,
        repeats=repeats,
        chance=chance,
        pattern=pattern,
        pitch=pitch,
        decay=decay,
        gate=gate,
        mix=mix,
        period=period,
        seed=seed,
    )


_STOP_FLOOR = 0.12  # the speed under which a stopping tape fades to nothing


def _stop(x, bpm=124.0, beats=1.0, fall=1.0, mix=1.0, period=4.0, sr=SR):
    """Tape stop. The last `beats` of every `period` (both in beats) slow
    to a halt: playback speed — and with it the pitch — falls in a straight
    line from 1 to 0 over the first `fall` share of that window, then there
    is silence until the period ends and the signal comes back on the bar
    line. A window of W samples whose fall takes F = fall * W reads the
    input at t0 + u - u^2 / 2F for u < F: it starts in step with the dry
    signal (no seam going in), covers F / 2 samples of it in all, and under
    `_STOP_FLOOR` of full speed the level follows the speed down to zero,
    so it never holds a DC value.

    Loop-safe the way `beat_repeat` is, and for the same reason: the
    windows restart every `period` beats (fxrack passes `repeat_period`,
    which divides every stem's own loop) and a window reads and writes
    inside its own period only. The effect is a periodic time remap, which
    commutes with `_wrap_tile`'s tail fold. Causal: it only ever reads
    behind the playhead.

    `mix` may be a per-sample array (a song's FX lane — how a stop is
    placed on one bar of a section and nowhere else): the blend at each
    sample, the scalar unit's own arithmetic."""
    swept = np.ndim(mix) > 0
    if swept:
        mix = np.asarray(mix, dtype=np.float64)
    if not np.any(mix > 0) or beats <= 0:
        return x
    st = to_stereo(np.asarray(x, dtype=np.float64))
    n = len(st)
    per = max(1.0, float(period)) * 60.0 / float(bpm) * sr  # samples, fractional
    win = min(float(beats) * 60.0 / float(bpm) * sr, per)
    fade = max(1, int(_REPEAT_FADE * sr))
    src = np.vstack([st, np.zeros((2, 2))])
    out = st.copy()
    for j in range(int(np.ceil(n / per)) + 1):
        end_p = int(round((j + 1) * per))  # the period's end
        t0 = int(round((j + 1) * per - win))
        if t0 >= n:
            break
        end = min(n, end_p)
        w = end_p - t0  # the whole window, cut or not
        f = max(2, min(w, int(round(min(1.0, max(0.05, float(fall))) * w))))
        u = np.arange(min(end - t0, f), dtype=np.float64)
        pos = u - u * u / (2.0 * f)  # from the window's start: every period alike
        lo = pos.astype(np.int64)
        frac = (pos - lo)[:, None]
        lo += t0
        speed = 1.0 - u / f
        level = np.clip(speed / _STOP_FLOOR, 0.0, 1.0)
        level = 0.5 - 0.5 * np.cos(np.pi * level)
        wet = np.zeros((end - t0, 2))
        wet[: len(u)] = (src[lo] * (1.0 - frac) + src[lo + 1] * frac) * level[:, None]
        # the dry signal comes back over one fade, just before the bar line
        edge = np.ones(w)
        k = min(fade, w // 2)
        if k > 0:
            edge[w - k :] = 0.5 + 0.5 * np.cos(np.pi * (np.arange(k) + 0.5) / k)
        edge = edge[: end - t0]
        if swept:
            mx = mix[t0:end]
            m = edge * mx
            out[t0:end] = st[t0:end] * (1.0 - m)[:, None] + wet * mx[:, None]
            continue
        m = edge * float(mix)
        out[t0:end] = st[t0:end] * (1.0 - m)[:, None] + wet * float(mix)
    return out if is_stereo(x) else out.mean(axis=1)


def stop(bpm=124.0, beats=1.0, fall=1.0, mix=1.0, period=4.0):
    return partial(_stop, bpm=bpm, beats=beats, fall=fall, mix=mix, period=period)


# ----------------------------------------------------------------------------
# dynamics
# ----------------------------------------------------------------------------


def _envelope_follower(mono, attack=0.005, release=0.08, sr=SR):
    a = np.exp(-1.0 / (attack * sr))
    r = np.exp(-1.0 / (release * sr))
    rect = np.abs(mono)
    # fast attack via one-pole on the way up, slower on the way down
    up = one_pole(rect, a)
    down = one_pole(rect, r)
    return np.maximum(up, down)


# Vocoder band edges span the range that carries speech intelligibility;
# below ~120 Hz there is little articulation and above ~8 kHz mostly sibilance.
VOCODER_LO, VOCODER_HI = 120.0, 8000.0


def _band(x: np.ndarray, centre: float, q: float, sr: int) -> np.ndarray:
    """Two cascaded bandpasses = 4-pole skirts. A single biquad rolls off at
    only ~6 dB/oct, so a 200 Hz modulator still leaked into the 7 kHz band at
    -47 dB and every band opened at once — the carrier never actually took
    the modulator's shape. Cascading squares the rejection."""
    return biquad(biquad(x, "bandpass", centre, q, sr), "bandpass", centre, q, sr)


# The noise path's unvoiced detector: the share of the modulator's level
# above VOCODER_UNVOICED_HZ. A vowel's energy sits under it (measured share
# ~0.05); an "s" or white noise sits over it (~0.9). The path opens between
# the two bounds.
VOCODER_UNVOICED_HZ = 4000.0
VOCODER_UNVOICED_SHARE = (0.25, 0.6)
_SHIFT_HZ = (20.0, 0.45)  # a shifted carrier band's centre: Hz, and x sr


def unvoiced(modulator: np.ndarray, sr: int = SR) -> np.ndarray:
    """0..1 per sample: how much of the modulator is noise-like right now
    (a consonant) rather than voiced. The level above
    `VOCODER_UNVOICED_HZ` over the full-band level, both followed over
    20 ms, mapped through `VOCODER_UNVOICED_SHARE`. Silence is 0."""
    mod = np.asarray(modulator, dtype=np.float64)
    top = biquad(
        biquad(mod, "highpass", VOCODER_UNVOICED_HZ, 0.707, sr),
        "highpass",
        VOCODER_UNVOICED_HZ,
        0.707,
        sr,
    )
    hi = _envelope_follower(top, 0.005, 0.02, sr)
    full = _envelope_follower(mod, 0.005, 0.02, sr)
    lo_share, hi_share = VOCODER_UNVOICED_SHARE
    share = hi / np.maximum(full, 1e-6)
    return np.clip((share - lo_share) / (hi_share - lo_share), 0.0, 1.0)


def vocoder(
    carrier: np.ndarray,
    modulator: np.ndarray,
    bands: int = 16,
    attack: float = 0.005,
    release: float = 0.04,
    sr: int = SR,
    *,
    shift: float = 0.0,
    noise: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """Impose the modulator's spectral envelope on the carrier.

    Both signals are split into `bands` log-spaced bandpasses; each
    modulator band's envelope becomes the gain of the matching carrier band,
    and the bands are summed. That is what makes the carrier "speak": the
    articulation is the modulator's, the pitch and timbre are the carrier's.

    Unlike the rack units this takes *two* signals, so it is not an FX_UNIT —
    callers pair it with whatever supplies the modulator. A modulator of a
    different length is resampled to the carrier's, since a take and a loop
    are never the same length.

    `shift` (semitones) moves every carrier band that far from the
    modulator band that drives it: the formants go up or down, the pitch
    (the carrier's) stays. `noise` (0..1) is the consonant path: where the
    modulator is unvoiced (`unvoiced`), seeded white noise at the carrier's
    RMS goes through the same bands under the same envelopes, so an "s"
    comes through a carrier that has nothing up there. With both at 0 —
    the defaults — the output is what it always was, sample for sample.

    An offline bake: the level match below reads the whole buffer, which
    nothing `Synth.note` reaches may do (the causal contract) and this is
    not that.
    """
    n = len(carrier)
    mod = np.asarray(modulator, dtype=np.float64)
    if mod.ndim == 2:
        mod = mod.mean(axis=1)
    if len(mod) != n:
        mod = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(mod)), mod)

    car = np.asarray(carrier, dtype=np.float64)
    edges = np.geomspace(VOCODER_LO, min(VOCODER_HI, sr * 0.45), int(bands) + 1)
    out = np.zeros_like(car)
    ratio = 2.0 ** (float(shift) / 12.0)
    hiss = None
    if noise > 0:
        level = float(np.sqrt(np.mean(car * car)))
        hiss = np.random.default_rng(seed).standard_normal(n) * level
        spoken = np.zeros(n)
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        centre = float(np.sqrt(lo * hi))
        q = centre / max(hi - lo, 1e-6)  # constant-Q: each band equally wide
        env = _envelope_follower(_band(mod, centre, q, sr), attack, release, sr)
        at = centre
        if ratio != 1.0:
            at = min(max(centre * ratio, _SHIFT_HZ[0]), sr * _SHIFT_HZ[1])
        band = _per_channel(_band, carrier, at, q, sr)
        out += band * (env[:, None] if out.ndim == 2 else env)
        if hiss is not None:
            spoken += _band(hiss, at, q, sr) * env
    if hiss is not None:
        spoken *= unvoiced(mod, sr) * min(1.0, float(noise))
        out += spoken[:, None] if out.ndim == 2 else spoken
    # the summed bands are far quieter than the carrier; match levels so
    # `vocoder` is a transform rather than also a volume drop
    peak = float(np.max(np.abs(out)))
    ref = float(np.max(np.abs(carrier)))
    if peak > 1e-9 and ref > 1e-9:
        out *= ref / peak
    return out


def _compress(
    x, threshold=-14.0, ratio=4.0, attack=0.005, release=0.12, makeup=None, sr=SR
):
    st = to_stereo(x)
    det = _envelope_follower(np.max(np.abs(st), axis=1), attack, release, sr)
    thr = 10 ** (threshold / 20)
    over = np.maximum(det / max(thr, 1e-9), 1.0)
    gain = over ** (1.0 / ratio - 1.0)
    if makeup is None:
        makeup = (1.0 / (1.0 ** (1.0 / ratio - 1.0))) * (
            thr ** (1.0 / ratio - 1.0)
        ) ** -0.0
        makeup = 10 ** ((-threshold * (1 - 1 / ratio) * 0.5) / 20)
    out = st * gain[:, None] * makeup
    return out if is_stereo(x) else out.mean(axis=1)


# Seconds a duck takes to fall to its depth, *before* its trigger. 6 ms is
# slow enough that the gain is never a step (a 0.7 duck moves at most 0.4%
# a sample). Not 5 ms: that is 220.5 samples, which Python rounds to 220
# and the live worklet's Math.round to 221.
SIDECHAIN_ATTACK = 0.006


def _sidechain(
    x, trigger_times, bpm=120, depth=0.75, release=0.28, attack=SIDECHAIN_ATTACK, sr=SR
):
    """The pumping 'ducking under the kick' effect that defines modern dance music.

    Each trigger's gain is at 1 - depth on the trigger and recovers along
    t ** 0.55 over `release`, as it always has. What is new is the way
    down: a half cosine over the `attack` seconds *before* the trigger.
    The gain used to fall in one sample — a step in whatever was still
    sounding there (a pump on the drum bus, a long kick tail), heard as a
    click on every beat, and at the seam of every loop
    (`tests/test_artifacts.py`). The triggers are a clock or a pattern,
    known ahead, so looking 6 ms ahead of one costs no latency, live
    included, and a note that starts on the beat still starts ducked.
    Overlapping ducks take the lower gain. A trigger just past the
    buffer's end still brings the gain down into it, and the curve is cut
    — not squeezed — by either end.

    `depth` may be a per-sample array (a song's FX lane): the duck's shape
    (0 at rest, 1 on a trigger; overlapping ducks take the deeper) is
    computed once and the gain is 1 - depth * shape per sample. With the
    depth standing still that is the scalar unit bit for bit: 1 - d * s
    never rises with s, so the lowest gain is the one at the deepest
    shape."""
    st = to_stereo(x)
    n = len(st)
    a, seg = n_samples(attack, sr), n_samples(release, sr)
    fall = 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)  # 0 .. just under 1
    rise = np.linspace(0, 1, seg) ** 0.55
    shape = np.concatenate([fall, 1 - rise])
    swept = np.ndim(depth) > 0
    curve = shape if swept else 1 - depth * shape
    gain = np.zeros(n) if swept else np.ones(n)
    pick = np.maximum if swept else np.minimum
    for tt in trigger_times:
        i = n_samples(tt, sr) - a  # where this duck's curve starts
        lo, hi = max(i, 0), min(n, i + len(curve))
        if lo < hi:
            gain[lo:hi] = pick(gain[lo:hi], curve[lo - i : hi - i])
    if swept:
        gain = 1 - np.asarray(depth, dtype=np.float64) * gain
    out = st * gain[:, None]
    return out if is_stereo(x) else out.mean(axis=1)


def compress(threshold=-14.0, ratio=4.0, attack=0.005, release=0.12, makeup=None):
    """`makeup` is a linear gain; None is the automatic one (half the gain
    reduction at the threshold, as it always was)."""
    return partial(
        _compress,
        threshold=threshold,
        ratio=ratio,
        attack=attack,
        release=release,
        makeup=makeup,
    )


def sidechain(trigger_times, depth=0.75, release=0.28, attack=SIDECHAIN_ATTACK):
    return partial(
        _sidechain,
        trigger_times=trigger_times,
        depth=depth,
        release=release,
        attack=attack,
    )


def _granular(
    x, grain=0.08, scatter=0.4, pitch=0.0, density=2.0, mix=0.6, seed=7, sr=SR
):
    """Grain cloud: chop the input into overlapping windowed grains and
    re-emit them from scattered read positions. `scatter` 0 is a straight
    (pitch-shiftable) re-read, higher smears the source into a texture.

    Seeded, so a loop re-renders identically — the same rule the drum
    machine's humanise follows."""
    st = to_stereo(x)
    n = len(st)
    g = max(64, int(grain * sr))
    if n <= g * 2:
        return x
    hop = max(32, int(g / max(0.5, density)))
    win = np.hanning(g)[:, None]
    ratio = 2.0 ** (pitch / 12.0)
    rng = np.random.default_rng(int(seed))
    out = np.zeros_like(st)
    reach = int(scatter * sr * 0.5)
    for start in range(0, n - g, hop):
        src = start + (int(rng.integers(-reach, reach + 1)) if reach else 0)
        src = int(np.clip(src, 0, n - g - 1))
        if abs(ratio - 1.0) < 1e-6:
            gr = st[src : src + g]
        else:
            idx = np.clip((np.arange(g) * ratio).astype(np.int64) + src, 0, n - 1)
            gr = st[idx]
        out[start : start + g] += gr * win
    # overlapping Hann grains sum above unity; normalise by the overlap factor
    out /= max(1.0, g / hop) * 0.5
    wet = out * mix + st * (1 - mix)
    return wet if is_stereo(x) else wet.mean(axis=1)


_SHIMMER_GRAIN = 0.06  # seconds per pitch-shift grain

# The pitch unit's splice (`_pitch_splices`): how much later than its place
# a grain may start, to land in phase (a whole period of 62.5 Hz); the
# recent past the match is measured on (read every other sample); and what
# the search's whole reach costs, as a share of that window's energy, so of
# two in-phase starts the earlier wins (less delay, and a tie is never left
# to rounding: the live twin must pick the same one).
PITCH_SEARCH = 0.016
PITCH_WINDOW = 0.012
PITCH_BIAS = 0.1


def _pitch_splices(
    mono: np.ndarray,
    n_grains: int,
    hop: int,
    ratio,
    sr: int = SR,
    free: np.ndarray | None = None,
) -> np.ndarray:
    """Each grain's start, as extra delay over the shifter's `lag` (0 ..
    PITCH_SEARCH s): where it reads the source in phase with the grain it
    fades out of. Over a crossfade the two grains read the source a fixed
    `L` samples apart, `L` set by where the new one starts; on a periodic
    sound, an `L` that is a whole number of periods makes the pair one
    signal, so the crossfade is seamless — no lines 1/hop apart round the
    target. Picked at the grain's start from the past alone (causal: the
    window ends at the start) as the `L` whose difference function
    sum (x[t] - x[t - |L|])^2 over the window is least (as
    sum x^2 + sum x[t - |L|]^2 - 2 sum x[t] x[t - |L|]), plus PITCH_BIAS,
    then refined between samples on a parabola. A silent window keeps the
    grain on its place. Sequential (each start follows the last), and the
    same arithmetic as vox-worklet.js's `splice`, which picks live.

    `ratio` is one number, or one a grain (the auto-tune's and the
    harmonizer's, `_shift_grains`): then the old grain read at its own
    ratio, from its own lag, and the gap the new one closes is reckoned
    from both. A grain `free` marks (the auto-tune's unvoiced ones) is not
    spliced: it starts on its place, so a run of them is the input."""
    reach = int(round(PITCH_SEARCH * sr))
    m = max(1, int(round(PITCH_WINDOW * sr)) // 2)
    q = np.broadcast_to(np.asarray(ratio, dtype=np.float64), (n_grains,))
    lags = np.maximum(0.0, (q - 1.0) * (2 * hop - 1))
    q0 = np.concatenate([q[:1], q[:-1]])  # each grain's predecessor (grain 0 its own)
    l0 = np.concatenate([lags[:1], lags[:-1]])
    # how far the old grain's read gains on the new place: for one ratio,
    # hop * (ratio - 1) (the lags cancel)
    runs = hop * (q0 - 1.0) + (lags - l0)
    top = int(np.max(np.abs(runs), initial=0.0)) + reach + 2  # the furthest |L|
    pad = hop + 2 * m + top + 4
    y = np.concatenate([np.zeros(pad), np.asarray(mono, dtype=np.float64)])
    span = 2 * m - 1 + top  # the past one search reads, newest first
    extra = np.zeros(n_grains)
    sums = np.zeros(span + 2)
    d = 0.0
    for i in range(n_grains):
        at = pad + (i - 1) * hop
        past = y[at - span + 1 : at + 1][::-1]  # past[k] = x[start - k]
        # row L: the window L samples back, x[start - L - 2j] for j < m (a view)
        rows = sliding_window_view(past, 2 * m - 1)[:, ::2]
        a = rows[0]  # the window: the start, then every other sample before
        energy = float(a @ a)
        if energy == 0.0 or (free is not None and free[i]):  # nothing to match
            d = 0.0
        else:
            c = runs[i] - d  # the old grain's read, against the new grain's place
            lags = np.arange(np.ceil(c), np.floor(c + reach) + 1)
            at_l = np.abs(lags).astype(np.int64)
            lo_l = int(at_l.min())
            corr = (rows[lo_l : int(at_l.max()) + 1] @ a)[at_l - lo_l]
            # each row's energy: running sums over every other sample
            sq = past * past
            sums[2::2] = np.cumsum(sq[0::2])
            sums[3::2] = np.cumsum(sq[1::2])
            rows_e = sums[at_l + 2 * m] - sums[at_l]
            cost = energy + rows_e - 2.0 * corr
            cost = cost + PITCH_BIAS * energy * (lags - c) / reach
            j = int(np.argmin(cost))
            lag = float(lags[j])
            if 0 < j < len(lags) - 1:
                lo, mid, hi = cost[j - 1], cost[j], cost[j + 1]
                den = lo - 2.0 * mid + hi
                if den > 0:
                    lag += min(0.5, max(-0.5, 0.5 * (lo - hi) / den))
            d = min(max(lag - c, 0.0), float(reach))
        extra[i] = d
    return extra


def grain_count(n: int, hop: int) -> int:
    """How many grains `_pitch_shift_causal` lays over `n` samples: grain i
    starts at (i - 1) * hop, the first a hop before the buffer."""
    return -(-n // hop) + 1


def _pitch_shift_causal(
    st: np.ndarray,
    ratio,
    sr: int = SR,
    grain: float = _SHIMMER_GRAIN,
    spliced: bool = False,
    free: np.ndarray | None = None,
) -> np.ndarray:
    """Delay-line granular pitch shift: overlap-added Hann grains, each
    read at `ratio` speed from a start far enough back that the whole grain
    reads only samples at or before the output time. Nothing from the
    future, so a sound is never heard before it is played. `grain` is the
    grain's length in seconds (the shimmer's 60 ms; the pitch unit's
    shorter one lags a voice less).

    `spliced` (the pitch unit's) starts each grain where it lands in phase
    with the one it fades out of (`_pitch_splices`); without it every grain
    starts on its own clock, so on a held tone the two in a crossfade are
    out of phase by whatever the hop leaves and the result is lines 1/hop
    apart round the target (the shimmer's, which a reverb smears).

    `ratio` may be one a grain (`grain_count` of them: the auto-tune's and
    the harmonizer's, which move it note by note); each grain then reads
    at its own, from its own lag. `free` is `_pitch_splices`'."""
    n, ch = st.shape
    hop = max(8, int(grain * sr) // 2)
    g = 2 * hop  # two halves per grain: overlap-add is two slice adds
    n_grains = grain_count(n, hop)  # grain i starts at (i - 1) * hop
    q = np.broadcast_to(np.asarray(ratio, dtype=np.float64), (n_grains,))
    # a grain starting at output t reads src[s + k*ratio] for k < g; causal
    # iff s + k*ratio <= t + k, i.e. s <= t - (ratio - 1)*(g - 1) when ratio > 1
    lag = np.maximum(0.0, (q - 1.0) * (g - 1))
    win = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(g) / g)  # periodic: sums to 1
    k = np.arange(g)
    padded = np.concatenate([st, np.zeros((1, ch))])  # index n reads silence
    # each grain's delay at its start: `lag`, plus the splice's extra
    extra = (
        _pitch_splices((st[:, 0] + st[:, -1]) * 0.5, n_grains, hop, q, sr, free)
        if spliced
        else np.zeros(n_grains)
    )
    frames = np.zeros((n_grains + 1, hop, ch))
    per_pass = 256  # grains per pass: bounds the (grains, g, ch) scratch
    for g0 in range(0, n_grains, per_pass):
        idx = np.arange(g0, min(n_grains, g0 + per_pass))
        start = (idx - 1) * hop - (lag[idx] + extra[idx])
        read = start[:, None] + k[None, :] * q[idx][:, None]
        lo = np.floor(read).astype(np.int64)
        frac = (read - lo)[..., None]
        lo0 = np.where((lo >= 0) & (lo < n), lo, n)
        lo1 = np.where((lo + 1 >= 0) & (lo + 1 < n), lo + 1, n)
        grains = (padded[lo0] * (1 - frac) + padded[lo1] * frac) * win[:, None]
        frames[idx] += grains[:, :hop]
        frames[idx + 1] += grains[:, hop:]
    # frame j covers output samples [(j - 1) * hop, j * hop)
    return frames.reshape(-1, ch)[hop : hop + n]


def _shimmer(x, decay=3.0, mix=0.3, shift=12.0, size=0.9, sr=SR):
    """Reverb whose tail is pitched up an octave — the big bright Anyma wash.
    The shift is a causal granular pitch shifter ahead of the tank.

    It used to be a plain resample at `ratio` speed, wrapped at the buffer
    end: an octave up reads sample 2t at time t, so a blip at 1.6 s was
    heard shimmering at 0.8 s — before it was played."""
    st = to_stereo(x)
    n = len(st)
    ratio = 2.0 ** (shift / 12.0)
    shifted = _pitch_shift_causal(st, ratio, sr)
    tail = _reverb(shifted, decay=decay, mix=1.0, size=size, sr=sr)
    tail = tail[:n] if len(tail) >= n else np.pad(tail, ((0, n - len(tail)), (0, 0)))
    wet = st * (1 - mix) + tail * mix
    return wet if is_stereo(x) else wet.mean(axis=1)


def granular(grain=0.08, scatter=0.4, pitch=0.0, density=2.0, mix=0.6, seed=7):
    return partial(
        _granular,
        grain=grain,
        scatter=scatter,
        pitch=pitch,
        density=density,
        mix=mix,
        seed=seed,
    )


def shimmer(decay=3.0, mix=0.3, shift=12.0, size=0.9):
    return partial(_shimmer, decay=decay, mix=mix, shift=shift, size=size)


def _limit(x, ceiling=0.97, lookahead=0.005, release=0.05, sr=SR, loop=True):
    """Peak limiter with real lookahead.

    The old one rolled the block maxima *backwards*, so the gain reduction
    arrived after the peak it was meant to catch; it then smoothed the
    detector (smearing peaks rather than the gain) and let a final
    `np.clip` do the actual limiting. That is a hard clipper wearing a
    limiter's name. Here each block's gain accounts for the block ahead of
    it, so the reduction is already in place when the transient lands, and
    only the recovery is smoothed — attack is instant, release is gentle.

    `loop` says what lies ahead of the last block. On a loop it is the
    first block again (the studio limits two passes of a loop and keeps the
    second: `render._normalize`). On a one-shot — a take, an export, a song
    with its tail — it is nothing: pass `loop=False`, or a loud start turns
    the final `lookahead` of a quiet ending down with it.
    """
    st = to_stereo(x)
    n = len(st)
    peak = np.max(np.abs(st), axis=1)
    w = max(1, n_samples(lookahead, sr))
    pad = (-n) % w
    p = np.concatenate([peak, np.zeros(pad)])
    blocks = p.reshape(-1, w).max(axis=1)
    # look one block *forward*: the gain must be down before the peak lands
    ahead = np.roll(blocks, -1)
    if not loop:
        ahead[-1] = 0.0
    env = np.repeat(np.maximum(blocks, ahead), w)[:n]
    gain = np.minimum(1.0, ceiling / np.maximum(env, 1e-9))
    r = float(np.exp(-1.0 / max(release * sr, 1.0)))
    # instant duck, slow recovery. The smoother has to *start* at the first
    # gain value: from a zero state it ramps up from silence and swallows
    # the first release-worth of the buffer — 50 ms of every loop.
    b, a = [1.0 - r], [1.0, -r]
    smoothed = _lfilter(b, a, gain, zi=lfilter_zi(b, a) * float(gain[0]))
    gain = np.minimum(gain, smoothed)
    out = st * gain[:, None]
    return out if is_stereo(x) else out.mean(axis=1)


def limit(ceiling=0.97, lookahead=0.005, release=0.05, loop=True):
    return partial(
        _limit, ceiling=ceiling, lookahead=lookahead, release=release, loop=loop
    )


# ----------------------------------------------------------------------------
# stereo
# ----------------------------------------------------------------------------


_SQRT2 = float(np.sqrt(2.0))


def _width(x, amount=1.5):
    st = to_stereo(x)
    mid = (st[:, 0] + st[:, 1]) * 0.5
    side = (st[:, 0] - st[:, 1]) * 0.5 * amount
    return np.column_stack([mid + side, mid - side])


def width(amount=1.5):
    return partial(_width, amount=amount)


def pan(x: np.ndarray, position: float = 0.0) -> np.ndarray:
    """-1 = hard left, 0 = centre, +1 = hard right (equal-power).

    A stereo input keeps its image: this used to mono-sum first, so panning
    a track threw away every bit of width the voice had earned — unison
    spread, chorus, the reverb's stereo tail — and Track.render calls it on
    any track with a pan. Mono in still fans out to both channels, which is
    what Synth.note's per-voice placement relies on.
    """
    angle = (np.clip(position, -1, 1) + 1) * np.pi / 4
    left, right = np.cos(angle), np.sin(angle)
    if is_stereo(x):
        return np.column_stack([x[:, 0] * left * _SQRT2, x[:, 1] * right * _SQRT2])
    return np.column_stack([x * left, x * right])


# ----------------------------------------------------------------------------
# studio tricks (the vocal booth): artificial double tracking, a
# rotary speaker, a tape echo, a telephone line, a pitch shifter, a plate.
# Each is causal — a sample reads only its own past, every gain is fixed,
# nothing is matched to the buffer — and ends in `dry * (1 - mix) + wet *
# mix` with a wet that does not depend on the mix, so a song's mix lane is
# the unit with its slider moving (`studio/fxauto.py`). The modulations are
# functions of time alone (sines, never a random stream), so the live keys'
# twins in static/fx-worklet.js compute the same numbers.
# ----------------------------------------------------------------------------


def _read_back(x: np.ndarray, d) -> np.ndarray:
    """`x` (n,) or (n, ch) read `d` samples behind each sample (per sample,
    fractional), linearly interpolated as `a + (b - a) * frac`; before the
    first sample is silence. Never ahead of now: a negative `d` reads the
    present (the flanger's rule, `_mod_delay_numpy`). Natively the same
    arithmetic sample for sample (`read_back`), without the twin's padded
    copy and its gathers."""
    native = kernel("read_back")
    if native is None or len(x) == 0 or np.ndim(x) not in (1, 2):
        return _read_back_numpy(x, d)
    n = len(x)
    d = np.ascontiguousarray(np.broadcast_to(np.asarray(d, dtype=np.float64), (n,)))
    return native(np.ascontiguousarray(x, dtype=np.float64), d)


def _read_back_numpy(x: np.ndarray, d) -> np.ndarray:
    n = len(x)
    if n == 0:
        return np.array(x, dtype=np.float64, copy=True)
    d = np.maximum(np.broadcast_to(np.asarray(d, dtype=np.float64), (n,)), 0.0)
    pos = np.arange(n) - d
    lo = np.floor(pos)
    frac = pos - lo
    lead = int(max(0.0, -float(lo.min()))) + 1
    edge = np.zeros((1, *x.shape[1:]))
    pad = np.concatenate([np.zeros((lead, *x.shape[1:])), x, edge])
    i = lo.astype(np.int64) + lead
    a, b = pad[i], pad[i + 1]
    return a + (b - a) * (frac[:, None] if x.ndim == 2 else frac)


# a slow wander: three incommensurate sines (Hz, phase, weight), at most +-1.
# ADT's wobble and the tape echo's wow; a function of time alone.
_WANDER = ((0.37, 0.0, 1.0), (0.83, 1.3, 0.6), (1.61, 2.1, 0.35))
_WANDER_NORM = 1.95


def wander(n: int, sr: int = SR) -> np.ndarray:
    """The wander over `n` samples from the buffer's start, in -1..1."""
    return _sines(n, sr, _WANDER) / _WANDER_NORM


def _sines(n: int, sr: int, waves) -> np.ndarray:
    """`Σ amp sin(2π hz t + ph)` over (hz, ph, amp) `waves`, t from the
    buffer's start, added in order — natively in one pass (`sines`)."""
    native = kernel("sines")
    if native is None:
        return _sines_numpy(n, sr, waves)
    hz, ph, amp = (np.array(w, dtype=np.float64) for w in zip(*waves, strict=True))
    return native(hz, ph, amp, int(n), float(sr))


def _sines_numpy(n: int, sr: int, waves) -> np.ndarray:
    t = time_axis(n, sr)
    w = np.zeros(n)
    for hz, ph, amp in waves:
        w += amp * np.sin(2 * np.pi * hz * t + ph)
    return w


def _pan_gains(position: float) -> tuple[float, float]:
    """`pan`'s law for one position: (left, right) gains, unity at the centre."""
    angle = (min(1.0, max(-1.0, float(position))) + 1) * np.pi / 4
    return float(np.cos(angle) * _SQRT2), float(np.sin(angle) * _SQRT2)


ADT_WOBBLE = 0.002  # seconds the double's delay wanders either way at wobble 1
ADT_LEVEL = 0.5**0.5  # two copies, one level: the sum of the pair x 1/sqrt 2


def _doubler(x, time=0.03, wobble=0.5, spread=0.6, mix=1.0, sr=SR):
    """ADT, artificial double tracking (the tape trick of 1966): the voice
    and a second copy of it `time` s late, that delay wandering slowly by
    up to `wobble` x ADT_WOBBLE (a moving delay is a pitch drift too: a few
    cents), the pair panned apart by `spread` (the original left, the copy
    right; 0 both centred, where it combs like a flanger at rest)."""
    st = to_stereo(x)
    n = len(st)
    d = (time + wobble * ADT_WOBBLE * wander(n, sr)) * sr
    double = _read_back(st, d)
    dl, dr = _pan_gains(-spread)
    wl, wr = _pan_gains(spread)
    wet = np.column_stack(
        [st[:, 0] * dl + double[:, 0] * wl, st[:, 1] * dr + double[:, 1] * wr]
    )
    wet *= ADT_LEVEL
    res = st * (1 - mix) + wet * mix
    return res if is_stereo(x) else res.mean(axis=1)


def doubler(time=0.03, wobble=0.5, spread=0.6, mix=1.0):
    return partial(_doubler, time=time, wobble=wobble, spread=spread, mix=mix)


# The rotary speaker: a horn above the crossover, a drum below it, each
# (chorale Hz, tremolo Hz, ramp s — the time constant it changes speed
# with —, Doppler s — its radius over the speed of sound —, level dip)
ROTARY_CROSSOVER = 800.0
ROTARY_HORN = (0.8, 6.8, 0.6, 0.00045, 0.45)
ROTARY_DRUM = (0.65, 5.9, 2.2, 0.0006, 0.25)
ROTARY_MIC = np.pi / 2  # the right mic's angle round the cabinet from the left's
ROTARY_MIN_DELAY = 0.0002  # s: the rotor's nearest point to a mic


def rotor_rate(speed, n: int, slow: float, fast: float, ramp: float, sr: int = SR):
    """A rotor's rate in Hz, per sample: chorale..tremolo by `speed` 0..1
    (a lane may move it), through a one-pole of `ramp` s — the motor's
    inertia, so a switch speeds up and slows down as a cabinet does. It
    starts at the first sample's speed."""
    target = slow + (fast - slow) * np.clip(as_array(speed, n), 0.0, 1.0)
    a = 1.0 - np.exp(-1.0 / (ramp * sr))
    return _lfilter([a], [1.0, -(1.0 - a)], target, zi=[(1.0 - a) * target[0]])


def _rotor(band: np.ndarray, rate: np.ndarray, doppler: float, dip: float, sr: int):
    """One rotor heard by two mics: its phase is the running sum of its
    rate; per mic a delay that swings over 2 x `doppler` s (the pitch rises
    as it comes round) and a level that falls by `dip` as it turns away.
    Natively in one call (`rotor`: the sum, the sines and the reads)."""
    native = kernel("rotor")
    if native is not None and len(band):
        return native(
            np.ascontiguousarray(band, dtype=np.float64),
            np.ascontiguousarray(rate, dtype=np.float64),
            float(doppler),
            float(dip),
            float(ROTARY_MIC),
            float(ROTARY_MIN_DELAY),
            float(sr),
        )
    return _rotor_numpy(band, rate, doppler, dip, sr)


def _rotor_numpy(band, rate, doppler: float, dip: float, sr: int) -> np.ndarray:
    phase = 2 * np.pi * np.concatenate([[0.0], np.cumsum(rate[:-1])]) / sr
    out = np.empty((len(band), 2))
    for c, off in enumerate((0.0, ROTARY_MIC)):
        s = np.sin(phase + off)
        d = (doppler * (1.0 + s) + ROTARY_MIN_DELAY) * sr
        out[:, c] = _read_back(band, d) * (1.0 - dip * 0.5 * (1.0 + s))
    return out


def _rotary(x, speed=0.0, drive=0.8, mix=1.0, sr=SR):
    """A rotating speaker cabinet: the input (summed to one channel, as a
    cabinet takes it) through its amp (`drive`, `core.soft_clip`; 0 clean),
    split at ROTARY_CROSSOVER (a low-pass and what it leaves, so the two
    sum back to the input), the horn and the drum each turning — Doppler
    and a level that swings — heard by two mics a quarter turn apart.
    `speed` 0 is chorale, 1 tremolo; between, between. A lane on it is the
    switch: the rotors ramp to the new speed (horn quick, drum slow)."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    mono = soft_clip(st.mean(axis=1), float(drive))
    low = biquad(mono, "lowpass", ROTARY_CROSSOVER, 0.707, sr)
    high = mono - low
    wet = np.zeros((n, 2))
    for band, (slow, fast, ramp, doppler, dip) in (
        (high, ROTARY_HORN),
        (low, ROTARY_DRUM),
    ):
        rate = rotor_rate(speed, n, slow, fast, ramp, sr)
        wet += _rotor(band, rate, doppler, dip, sr)
    res = st * (1 - mix) + wet * mix
    return res if is_stereo(x) else res.mean(axis=1)


def rotary(speed=0.0, drive=0.8, mix=1.0):
    return partial(_rotary, speed=speed, drive=drive, mix=mix)


# The tape echo's read head at wow 1: the wander's depth in s, then the
# flutter's depth in s and its rate in Hz; and the loop's low-pass, Hz at
# tone 0 and the octaves above it at tone 1
TAPE_WOW = (0.0012, 0.00008, 7.3)
TAPE_TONE = (1200.0, 3.0)


def tape_head(n: int, time: float, wow: float, sr: int = SR) -> np.ndarray:
    """The tape echo's delay in samples, per sample: `time` plus its wow
    and flutter (a moving head is a wavering pitch)."""
    depth, flutter, hz = TAPE_WOW
    move = depth * wander(n, sr) + flutter * _sines(n, sr, [(hz, 0.0, 1.0)])
    return (time + wow * move) * sr


def tape_pole(tone: float, sr: int = SR) -> float:
    """The tape loop's one-pole low-pass coefficient for `tone` 0..1."""
    hz = TAPE_TONE[0] * 2.0 ** (TAPE_TONE[1] * float(tone))
    return float(np.exp(-2 * np.pi * hz / sr))


def _tape_echo(
    x, time=0.3, feedback=0.45, wow=0.3, tone=0.5, drive=0.3, mix=0.35, sr=SR
):
    """A tape echo: the input onto a loop of tape read back `time` s later
    by a head that wavers (`wow`: wow and flutter), and every pass through
    the loop darker (a one-pole low-pass, `tone`) and a little saturated
    (tanh at 1 + 4 `drive`, unity for a quiet signal, so the loop's gain is
    never past `feedback`). wet = the input + the echoes, as the delay
    unit's is, so `mix` is the echoes' level over the dry. `feedback` may
    be a per-sample array (a lane). Computed a block shorter than the
    shortest delay at a time: a block reads only tape already written
    (natively `tape_echo`: the unit 3x, 5x under a feedback lane)."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    head = tape_head(n, time, wow, sr)
    a = tape_pole(tone, sr)
    g = 1.0 + 4.0 * float(drive)
    native = kernel("tape_echo")
    if native is not None:
        fb = np.ascontiguousarray(as_array(feedback, n), dtype=np.float64)
        tap = native(np.ascontiguousarray(st, dtype=np.float64), head, fb, a, g)
    else:
        tap = _tape_echo_taps_numpy(st, head, feedback, a, g)
    wet = st + tap
    res = st * (1 - mix) + wet * mix
    return res if is_stereo(x) else res.mean(axis=1)


def _tape_echo_taps_numpy(st, head, feedback, a: float, g: float) -> np.ndarray:
    """The tape echo's taps (what the head reads, filtered and saturated),
    a block shorter than the shortest delay at a time: the native
    `tape_echo` is this a sample at a time."""
    n = len(st)
    fb = as_array(feedback, n)[:, None]
    pos = np.arange(n) - np.maximum(head, 0.0)
    lo = np.floor(pos).astype(np.int64)
    frac = (pos - lo)[:, None]
    step = max(1, int(np.floor(np.min(np.arange(n) - pos))) - 1)
    tape = np.zeros((n + 1, 2))  # the last row: silence, read before the start
    tap = np.zeros((n, 2))
    held = np.zeros(2)
    for i in range(0, n, step):
        j = min(i + step, n)
        at = lo[i:j]
        r0 = tape[np.where(at >= 0, at, n)]
        r1 = tape[np.where(at + 1 >= 0, at + 1, n)]
        r = r0 + (r1 - r0) * frac[i:j]
        y = _lfilter([1.0 - a], [1.0, -a], r, zi=(a * held)[None, :])
        held = y[-1]
        tap[i:j] = np.tanh(y * g) / g
        tape[i:j] = st[i:j] + fb[i:j] * tap[i:j]
    return tap


def tape_echo(time=0.3, feedback=0.45, wow=0.3, tone=0.5, drive=0.3, mix=0.35):
    return partial(
        _tape_echo,
        time=time,
        feedback=feedback,
        wow=wow,
        tone=tone,
        drive=drive,
        mix=mix,
    )


def bandwidth_q(octaves: float) -> float:
    """An RBJ band-pass's Q for a band `octaves` wide between its -3 dB points."""
    k = 2.0 ** float(octaves)
    return float(np.sqrt(k) / (k - 1.0))


def _telephone(x, centre=1200.0, width=2.5, drive=2.0, mix=1.0, sr=SR):
    """A voice down a phone line: `bandpass` twice at `centre` (each a band
    `width` octaves wide, so the skirts fall twice as steeply), then
    driven (`core.soft_clip`; 0 clean) as a cheap capsule breaks up."""
    st = to_stereo(x)
    q = bandwidth_q(width)
    band = _filt(_filt(st, "bandpass", centre, q, sr), "bandpass", centre, q, sr)
    wet = soft_clip(band, float(drive))
    res = st * (1 - mix) + wet * mix
    return res if is_stereo(x) else res.mean(axis=1)


def telephone(centre=1200.0, width=2.5, drive=2.0, mix=1.0):
    return partial(_telephone, centre=centre, width=width, drive=drive, mix=mix)


PITCH_GRAIN = 0.04  # s: the pitch unit's grains (an octave up lags the voice ~40 ms)


def _pitch(x, semis=12.0, mix=0.5, sr=SR):
    """A pitch shifter (`_pitch_shift_causal`, PITCH_GRAIN grains, each
    spliced in phase with the last): the voice `semis` semitones away
    under or over itself — an octave double, a harmony a fifth up. A held
    tone comes out on its shifted harmonics (it was lines 50 Hz apart
    round them before the splice); a chord or noise has no period to
    match, so it stays a little rough. A trick, not a tuner: no formant
    correction, so a fifth up is a smaller singer."""
    st = to_stereo(x)
    ratio = 2.0 ** (float(semis) / 12.0)
    wet = _pitch_shift_causal(st, ratio, sr, PITCH_GRAIN, spliced=True)
    res = st * (1 - mix) + wet * mix
    return res if is_stereo(x) else res.mean(axis=1)


def pitch(semis=12.0, mix=0.5):
    return partial(_pitch, semis=semis, mix=mix)


# A plate: a sheet of steel, so no walls — no early reflections, dense
# from its first milliseconds (the onset), ringing longer below PLATE_SPLIT
# than above it, and thin in the lows (PLATE_LOW_CUT)
PLATE_SPLIT = 4000.0
PLATE_HIGH_DECAY = 0.55  # the highs' decay, as a share of the decay
PLATE_LOW_CUT = 180.0
PLATE_ONSET = 0.003
PLATE_TONE = (3000.0, 13000.0)  # its top: Hz at tone 0, Hz more at tone 1


def plate_ir(decay=2.2, tone=0.65, predelay=0.012, sr=SR, seed=11) -> np.ndarray:
    """The plate's impulse response, wet gain included (as `reverb_ir`'s:
    unit energy less 1.5 dB, never the buffer's). `decay` is the time to
    -60 dB below PLATE_SPLIT; `tone` its brightness; `predelay` s before it
    speaks. Seeded noise, so the same arguments are the same plate."""
    rng = np.random.default_rng(seed)
    n = n_samples(decay * 1.2, sr)
    t = time_axis(n, sr)
    noise = rng.standard_normal((n, 2))
    low = _per_channel(biquad, noise, "lowpass", PLATE_SPLIT, 0.707, sr)
    k = np.log(1000.0)  # down 60 dB at the decay
    ir = low * np.exp(-t * k / decay)[:, None]
    ir += (noise - low) * np.exp(-t * k / (decay * PLATE_HIGH_DECAY))[:, None]
    m = min(n, n_samples(PLATE_ONSET, sr))
    ir[:m] *= (np.arange(m) / m)[:, None]
    top = PLATE_TONE[0] + PLATE_TONE[1] * float(tone)
    ir = _per_channel(biquad, ir, "lowpass", top, 0.707, sr)
    ir = _per_channel(biquad, ir, "highpass", PLATE_LOW_CUT, 0.707, sr)
    pre = int(round(float(predelay) * sr))
    if pre > 0:
        ir = np.vstack([np.zeros((pre, 2)), ir])
    energy = float(np.sqrt(np.mean(np.sum(ir**2, axis=0)))) or 1.0
    return ir * (_REVERB_WET / energy)


@lru_cache(maxsize=8)
def _plate_room(decay, tone, predelay, sr) -> np.ndarray:
    """`plate_ir`, cached for the render path (read-only: renders share it)."""
    ir = plate_ir(decay, tone, predelay, sr)
    ir.flags.writeable = False
    return ir


def _plate(x, decay=2.2, tone=0.65, predelay=0.012, mix=0.3, sr=SR):
    """A plate reverb: the input convolved with `plate_ir` (both channels
    in one transform), blended as the reverb is. The live keys load the
    same IR into a ConvolverNode (GET /fx/ir.wav?kind=plate)."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    args = (float(decay), float(tone), float(predelay), int(sr))
    ir = _plate_room(*args)
    nfft = sp_fft.next_fast_len(n + len(ir) - 1, True)
    spec = sp_fft.rfft(st, nfft, axis=0) * _ir_spectrum("plate", args, ir, nfft)
    wet = sp_fft.irfft(spec, nfft, axis=0)[:n]
    res = st * (1 - mix) + wet * mix
    return res if is_stereo(x) else res.mean(axis=1)


def plate(decay=2.2, tone=0.65, predelay=0.012, mix=0.3):
    return partial(_plate, decay=decay, tone=tone, predelay=predelay, mix=mix)


# ----------------------------------------------------------------------------
# the live vocal chain: auto-tune, a harmonizer, a vocal
# compressor, a de-esser, a noise gate. A mic runs through them live (the
# live vocal chain; static/vox-worklet.js holds the twins), so each is
# causal like the studio tricks above: every decision is made from the past
# alone — the pitch at a grain's start from the window before it, a gain
# from a follower of what has been heard — and nothing is matched to the
# buffer. The pitched two move the voice through the pitch unit's shifter
# (`_pitch_shift_causal`, PITCH_GRAIN grains spliced in phase, a ratio a
# grain); the dynamics three are memoryless gain curves over
# `_envelope_follower`, the compressor's own detector.
# ----------------------------------------------------------------------------

# The pitch tracker: YIN (de Cheveigne & Kawahara 2002) on the TRACK_WINDOW
# s before each grain's start, read every other sample (as the splice's
# window), for a period between 1/TRACK_HI and 1/TRACK_LO s — a bass voice
# to a high soprano. The first lag whose cumulative-mean-normalised
# difference dips under TRACK_THRESHOLD is the period (its local minimum,
# refined on a parabola); none, or a window quieter than TRACK_FLOOR
# (RMS), is unvoiced: a consonant, a breath, silence.
TRACK_LO, TRACK_HI = 70.0, 1000.0
TRACK_WINDOW = 0.024
TRACK_THRESHOLD = 0.2
TRACK_FLOOR = 10 ** (-55 / 20)


def track_lags(sr: int = SR) -> tuple[int, int, int]:
    """The tracker's (window samples read, shortest period, longest), in
    samples at `sr`."""
    m = max(1, int(round(TRACK_WINDOW * sr)) // 2)
    return m, max(2, int(sr // TRACK_HI)), int(np.ceil(sr / TRACK_LO))


def pitch_track(mono: np.ndarray, n_grains: int, hop: int, sr: int = SR):
    """The f0 (Hz; 0 unvoiced) at each grain's start ((i - 1) * hop, as
    `_pitch_shift_causal` lays them), from the window ending there — the
    same arithmetic as vox-worklet.js's `track`, which runs it live."""
    m, t_lo, t_hi = track_lags(sr)
    span = 2 * m - 1 + t_hi  # the past one window and its longest lag read
    pad = span + hop + 4
    y = np.concatenate([np.zeros(pad), np.asarray(mono, dtype=np.float64)])
    taus = np.arange(1, t_hi + 1, dtype=np.float64)
    floor = m * TRACK_FLOOR**2
    f0 = np.zeros(n_grains)
    for i in range(n_grains):
        at = pad + (i - 1) * hop
        past = y[at - span + 1 : at + 1][::-1]  # past[k] = x[start - k]
        rows = sliding_window_view(past, 2 * m - 1)[:, ::2]  # row k: k back
        a = rows[0]
        if float(a @ a) < floor:
            continue
        diff = rows[1:] - a
        d = np.einsum("ij,ij->i", diff, diff)  # the difference at each lag 1..t_hi
        cum = np.cumsum(d)
        cm = d * taus / np.maximum(cum, 1e-300)  # cumulative-mean-normalised
        under = np.flatnonzero(cm[t_lo - 1 :] < TRACK_THRESHOLD)
        if not len(under):
            continue
        k = t_lo - 1 + int(under[0])
        while k + 1 < t_hi and cm[k + 1] < cm[k]:
            k += 1
        tau = float(k + 1)
        if t_lo - 1 < k < t_hi - 1:
            lo, mid, hi = cm[k - 1], cm[k], cm[k + 1]
            den = lo - 2.0 * mid + hi
            if den > 0:
                tau += min(0.5, max(-0.5, 0.5 * (lo - hi) / den))
        f0[i] = sr / tau
    return f0


def nearest_note(m: float, key: int, scale) -> int:
    """The note (MIDI) of `key` + `scale` (pitch classes over the key)
    nearest `m`; a tie goes down."""
    best = None
    for c in range(int(np.floor(m)) - 12, int(np.ceil(m)) + 13):
        if (c - key) % 12 in scale and (best is None or abs(m - c) < abs(m - best)):
            best = c
    return int(best)


def _midi_of(f0: float) -> float:
    return 69.0 + 12.0 * float(np.log2(f0 / 440.0))


# Auto-tune: at `speed` 1 the correction closes on its note with this time
# constant (a slow, natural glide); at 0 it is there at once (the robot).
# A sung pitch must be AUTOTUNE_HOLD semitones nearer another note before
# the target moves to it, so a voice between two never flips each grain.
AUTOTUNE_GLIDE = 0.4
AUTOTUNE_HOLD = 0.15
CHROMATIC = tuple(range(12))


def _held_note(m: float, key: int, scale, held):
    """The note a sung `m` is pulled to: the nearest of the scale, unless
    the one it held is within AUTOTUNE_HOLD semitones of as near."""
    t = nearest_note(m, key, scale)
    if held is not None and abs(m - held) - abs(m - t) < AUTOTUNE_HOLD:
        return held
    return t


def autotune_ratios(
    f0, hop: int, key=0, scale=CHROMATIC, speed=0.0, humanize=0.0, sr=SR
):
    """Each grain's shift as a ratio (1 where unvoiced) and whether it is
    unvoiced (`free`: not spliced, so a run of them is the input). The
    correction is (target - sung) x (1 - humanize), glided at `speed`; it
    starts again from 0 after every unvoiced grain, so a note's onset at a
    slow speed slides in, as a singer's does."""
    speed, humanize, key = float(speed), float(humanize), int(key)
    k = 1.0 if speed <= 0 else 1.0 - float(np.exp(-hop / (speed * AUTOTUNE_GLIDE * sr)))
    scale = frozenset(int(s) for s in scale)
    ratios, free = np.ones(len(f0)), np.zeros(len(f0), dtype=bool)
    c, held = 0.0, None
    for i, f in enumerate(f0):
        if f <= 0:
            c, held, free[i] = 0.0, None, True
            continue
        m = _midi_of(f)
        held = _held_note(m, key, scale, held)
        c += k * ((held - m) * (1.0 - humanize) - c)
        ratios[i] = 2.0 ** (c / 12.0)
    return ratios, free


def _autotune(x, key=0, scale=CHROMATIC, speed=0.0, humanize=0.0, mix=1.0, sr=SR):
    """Auto-tune: the voice's pitch (`pitch_track`) pulled to the nearest
    note of `key` + `scale` (`autotune_ratios`) through the pitch unit's
    shifter, a ratio a grain. `speed` 0 snaps (the hard, stepped robot
    voice), up to 1 glides in over AUTOTUNE_GLIDE s (natural); `humanize`
    leaves that share of the singer's own wander in; unvoiced grains pass
    through untouched. Formants move with the pitch (a correction of a
    semitone or less barely shows; no formant keep)."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    hop = max(8, int(PITCH_GRAIN * sr) // 2)
    mono = (st[:, 0] + st[:, -1]) * 0.5
    f0 = pitch_track(mono, grain_count(n, hop), hop, sr)
    ratios, free = autotune_ratios(f0, hop, key, scale, speed, humanize, sr)
    wet = _pitch_shift_causal(st, ratios, sr, PITCH_GRAIN, spliced=True, free=free)
    res = st * (1 - mix) + wet * mix
    return res if is_stereo(x) else res.mean(axis=1)


def autotune(key=0, scale=CHROMATIC, speed=0.0, humanize=0.0, mix=1.0):
    return partial(
        _autotune,
        key=key,
        scale=tuple(scale),
        speed=speed,
        humanize=humanize,
        mix=mix,
    )


# The harmonizer's intervals, in steps of a seven-note scale (a third is
# two, a fifth four, an octave seven; a scale of another size scales them:
# `harmony_steps`), and their names for the rack's menus
HARMONY_STEPS = (2, 4, 7, -2, -4, -7)
HARMONY_NAMES = ("3rd up", "5th up", "octave up", "3rd down", "5th down", "octave down")
HARMONY_OFF = len(HARMONY_STEPS)  # the second voice's "off"


def harmony_steps(interval: int, size: int) -> int:
    """`HARMONY_STEPS[interval]` in a scale of `size` notes (never a half:
    7 is prime and no scale has more than 12 notes)."""
    return int(round(HARMONY_STEPS[interval] * size / 7))


def _scale_note(key: int, scale: tuple, index: int) -> int:
    """The scale's `index`th note up from `key` (negative: down)."""
    size = len(scale)
    return key + 12 * (index // size) + scale[index % size]


def harmony_semis(f0, key=0, scale=CHROMATIC, interval=0) -> np.ndarray:
    """A harmony voice's shift each grain, in semitones: from the scale
    note nearest the sung pitch (held as the auto-tune holds it) to the
    note `interval` steps of the scale from it — so a third is major or
    minor as the key has it. Unvoiced, the last shift is kept (a consonant
    goes up with its word); before any note, the interval from the key's
    root."""
    key = int(key)
    scale = tuple(sorted({int(s) % 12 for s in scale})) or CHROMATIC
    steps = harmony_steps(int(interval), len(scale))
    semis = float(_scale_note(0, scale, steps) - scale[0])
    out, held, pcs = np.zeros(len(f0)), None, frozenset(scale)
    for i, f in enumerate(f0):
        if f > 0:
            held = _held_note(_midi_of(f), key, pcs, held)
            deg = scale.index((held - key) % 12)
            semis = float(_scale_note(held - scale[deg], scale, deg + steps) - held)
        out[i] = semis
    return out


def _harmony(
    x,
    key=0,
    scale=CHROMATIC,
    first=0,
    first_level=0.5,
    first_pan=-0.4,
    second=HARMONY_OFF,
    second_level=0.4,
    second_pan=0.4,
    sr=SR,
):
    """A harmonizer: the voice, and one or two copies of it (its mono sum)
    `first` / `second` scale steps away (`HARMONY_NAMES`; the second may be
    off), each at its level and pan (`pan`'s law). The voices follow the
    singer — vibrato and all — moved by whole steps of the key, through
    the pitch unit's shifter. No formant keep: a fifth up is a smaller
    singer, as the pitch unit's is."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    hop = max(8, int(PITCH_GRAIN * sr) // 2)
    mono = ((st[:, 0] + st[:, -1]) * 0.5)[:, None]
    f0 = pitch_track(mono[:, 0], grain_count(n, hop), hop, sr)
    out = st.copy()
    voices = ((first, first_level, first_pan), (second, second_level, second_pan))
    for which, level, pos in voices:
        if int(which) >= HARMONY_OFF or level <= 0:
            continue
        semis = harmony_semis(f0, key, scale, int(which))
        ratios = 2.0 ** (semis / 12.0)
        voice = _pitch_shift_causal(mono, ratios, sr, PITCH_GRAIN, spliced=True)
        gl, gr = _pan_gains(pos)
        out += float(level) * voice * np.array([gl, gr])
    return out if is_stereo(x) else out.mean(axis=1)


def harmony(
    key=0,
    scale=CHROMATIC,
    first=0,
    first_level=0.5,
    first_pan=-0.4,
    second=HARMONY_OFF,
    second_level=0.4,
    second_pan=0.4,
):
    return partial(
        _harmony,
        key=key,
        scale=tuple(scale),
        first=first,
        first_level=first_level,
        first_pan=first_pan,
        second=second,
        second_level=second_level,
        second_pan=second_pan,
    )


def _db(env: np.ndarray) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(env, 1e-9))


VOX_KNEE = 6.0  # dB: the vocal compressor's soft knee, centred on the threshold


def _vox_comp(
    x,
    threshold=-18.0,
    ratio=3.0,
    attack=0.005,
    release=0.12,
    makeup=3.0,
    mix=1.0,
    sr=SR,
):
    """The vocal compressor: `compress`'s detector (`_envelope_follower`
    on the louder channel: up at `attack`, down at `release`) into a soft
    knee VOX_KNEE dB wide, `makeup` dB after, blended at `mix` (parallel
    compression below 1). `compress` stays the loop's (offline: its
    automatic makeup, its place on a whole stem's sum); this one is causal
    and live."""
    st = to_stereo(x)
    env = _envelope_follower(np.max(np.abs(st), axis=1), attack, release, sr)
    over = _db(env) - threshold
    slope = 1.0 / ratio - 1.0
    half = VOX_KNEE / 2
    knee = slope * (over + half) ** 2 / (2 * VOX_KNEE)
    gain_db = np.where(over <= -half, 0.0, np.where(over < half, knee, slope * over))
    gain = 10.0 ** ((gain_db + makeup) / 20.0)
    res = st * (1 - mix) + st * gain[:, None] * mix
    return res if is_stereo(x) else res.mean(axis=1)


def vox_comp(
    threshold=-18.0, ratio=3.0, attack=0.005, release=0.12, makeup=3.0, mix=1.0
):
    return partial(
        _vox_comp,
        threshold=threshold,
        ratio=ratio,
        attack=attack,
        release=release,
        makeup=makeup,
        mix=mix,
    )


# The de-esser: the band over `freq` (an RBJ high-pass; the rest is its
# complement, so at no reduction the two sum to the input) followed fast,
# turned down DEESS_RATIO to one over the threshold, at most `depth` dB
DEESS_RATIO = 4.0
DEESS_ATTACK = 0.001
DEESS_RELEASE = 0.06


def _deess(x, freq=6500.0, threshold=-30.0, depth=10.0, sr=SR):
    """A split-band de-esser: only the sibilant band (over `freq`) is
    compressed, on its own level — an "s" turned down, the vowel under it
    untouched."""
    st = to_stereo(x)
    high = _per_channel(biquad, st, "highpass", float(freq), 0.707, sr)
    low = st - high
    env = _envelope_follower(
        np.max(np.abs(high), axis=1), DEESS_ATTACK, DEESS_RELEASE, sr
    )
    over = np.maximum(0.0, _db(env) - threshold)
    cut = np.minimum(depth, over * (1.0 - 1.0 / DEESS_RATIO))
    res = low + high * (10.0 ** (-cut / 20.0))[:, None]
    return res if is_stereo(x) else res.mean(axis=1)


def deess(freq=6500.0, threshold=-30.0, depth=10.0):
    return partial(_deess, freq=freq, threshold=threshold, depth=depth)


VGATE_ATTACK = 0.001


def _vgate(x, threshold=-45.0, ratio=4.0, depth=40.0, release=0.15, sr=SR):
    """A noise gate as a downward expander: under `threshold` the level
    falls `ratio` dB for every dB the input does, at most `depth` dB down
    (a high ratio is a gate; a low one breathes). The follower opens in
    VGATE_ATTACK s and lets go over `release`: the room and the headphone
    spill between phrases go, the words do not."""
    st = to_stereo(x)
    env = _envelope_follower(np.max(np.abs(st), axis=1), VGATE_ATTACK, release, sr)
    under = np.minimum(0.0, _db(env) - threshold)
    gain = 10.0 ** (np.maximum(-depth, under * (ratio - 1.0)) / 20.0)
    res = st * gain[:, None]
    return res if is_stereo(x) else res.mean(axis=1)


def vgate(threshold=-45.0, ratio=4.0, depth=40.0, release=0.15):
    return partial(
        _vgate, threshold=threshold, ratio=ratio, depth=depth, release=release
    )


# ----------------------------------------------------------------------------
# movement, tape and spectral units: an auto-filter, a tremolo / auto-pan, tape
# saturation, a ping-pong delay, a spring, a formant shifter, a frequency
# shifter, an exciter and a vocoder on its own carrier. Every one is causal
# like the studio tricks: each modulation a function of time alone (an LFO's
# phase is its rate times the time since the buffer began — the loop's bar —
# and the tape's hiss a hash of the sample's index, never a random stream),
# every follower and filter run forward, nothing matched to the buffer. Each
# ends in `dry * (1 - mix) + wet * mix` with a mix-free wet (the exciter's
# `amount` and the tremolo's `depth` are their blend), so a song's mix lane
# is the unit with its slider moving. Their live twins are
# static/fx2-worklet.js, the spring's a ConvolverNode on `spring_ir`.
# ----------------------------------------------------------------------------

# An LFO's shapes (the auto-filter's and the tremolo's `shape`, by index),
# each 0..1 and at 0 on the bar: a sine's trough, a triangle's foot, a saw
# up's start (a saw down starts at its top), a square's low half first
LFO_SHAPES = ("sine", "triangle", "saw up", "saw down", "square", "smooth square")
LFO_SMOOTH = 4.0  # the smooth square's tanh: a square with its edges rounded


def lfo_wave(phase: np.ndarray, shape: str = "sine") -> np.ndarray:
    """An LFO's value 0..1 at each `phase` (0..1, the cycles' fraction)."""
    if shape == "triangle":
        return 1.0 - np.abs(1.0 - 2.0 * phase)
    if shape == "saw up":
        return np.array(phase, dtype=np.float64, copy=True)
    if shape == "saw down":
        return 1.0 - phase
    if shape == "square":
        return (phase >= 0.5).astype(np.float64)
    c = np.cos(2 * np.pi * phase)
    if shape == "smooth square":
        return 0.5 - 0.5 * np.tanh(LFO_SMOOTH * c) / np.tanh(LFO_SMOOTH)
    return 0.5 - 0.5 * c


def lfo_phase(rate: float, n: int, sr: int = SR, offset: float = 0.0) -> np.ndarray:
    """The fraction of a cycle at each of `n` samples from the buffer's
    start: `rate` Hz times the time, plus `offset` cycles. A function of
    time alone, so the live twin reads the same phase at the same sample."""
    p = float(rate) * time_axis(n, sr) + float(offset)
    return p - np.floor(p)


AUTOFILTER_MODES = ("lowpass", "bandpass", "highpass")


def autofilter_cutoff(
    level: np.ndarray,
    cutoff=400.0,
    depth=3.0,
    rate=0.5,
    shape="sine",
    env=0.0,
    sens=6.0,
    attack=0.01,
    release=0.25,
    sr=SR,
) -> np.ndarray:
    """The auto-filter's cutoff per sample: `cutoff` moved up `depth`
    octaves by the LFO (its share 1 - |env|) and by the envelope follower
    on `level` (|the input|, the louder channel), `sens` dB into it and
    clipped at 1, its share `env` (negative: the filter closes as the input
    swells — the direction). `cutoff` and `depth` may be per-sample arrays."""
    n = len(level)
    wave = lfo_wave(lfo_phase(rate, n, sr), shape)
    e = float(env)
    move = (1.0 - abs(e)) * wave
    if e != 0.0:
        follow = _envelope_follower(level, attack, release, sr)
        move = move + e * np.minimum(1.0, follow * 10.0 ** (float(sens) / 20.0))
    return cutoff * 2.0 ** (depth * move)


def _autofilter(
    x,
    mode="lowpass",
    cutoff=400.0,
    q=3.0,
    depth=3.0,
    rate=0.5,
    shape="sine",
    env=0.0,
    sens=6.0,
    attack=0.01,
    release=0.25,
    mix=1.0,
    sr=SR,
):
    """An auto-filter (the filter-house sweep): a resonant low-, band- or
    high-pass whose cutoff an LFO and / or an envelope follower move
    (`autofilter_cutoff`), run as `core.biquad`'s swept state-variable
    filter — a coefficient set every 64 samples, the state kept across a
    jump. `rate` is in Hz (a synced division resolved by the rack)."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    fc = autofilter_cutoff(
        np.max(np.abs(st), axis=1),
        cutoff,
        depth,
        rate,
        shape,
        env,
        sens,
        attack,
        release,
        sr,
    )
    wet = biquad(st, mode, fc, q, sr)
    res = st * (1 - mix) + wet * mix
    return res if is_stereo(x) else res.mean(axis=1)


def autofilter(
    mode="lowpass",
    cutoff=400.0,
    q=3.0,
    depth=3.0,
    rate=0.5,
    shape="sine",
    env=0.0,
    sens=6.0,
    attack=0.01,
    release=0.25,
    mix=1.0,
):
    return partial(
        _autofilter,
        mode=mode,
        cutoff=cutoff,
        q=q,
        depth=depth,
        rate=rate,
        shape=shape,
        env=env,
        sens=sens,
        attack=attack,
        release=release,
        mix=mix,
    )


def _tremolo(x, rate=4.0, depth=0.5, shape="sine", phase=0.0, sr=SR):
    """A tremolo, and with the right channel's LFO `phase` degrees on (180:
    the two in turn) an auto-pan: each channel's gain 1 - depth x the LFO,
    full on the bar. `depth` may be a per-sample array (a lane)."""
    st = to_stereo(x)
    n = len(st)
    out = np.empty_like(st)
    for c, off in enumerate((0.0, float(phase) / 360.0)):
        gain = 1.0 - depth * lfo_wave(lfo_phase(rate, n, sr, off), shape)
        out[:, c] = st[:, c] * gain
    return out if is_stereo(x) else out.mean(axis=1)


def tremolo(rate=4.0, depth=0.5, shape="sine", phase=0.0):
    return partial(_tremolo, rate=rate, depth=depth, shape=shape, phase=phase)


# The tape machine: the head bump (Hz, Q) a `bump` dB peak; the top's
# low-pass at TAPE_TOP[0] Hz at tone 0 and TAPE_TOP[1] octaves above it at
# tone 1; the playback's low cut under the bump (Hz: a tape passes no DC,
# so a saturated lopsided wave leaves no offset); the hiss's level at hiss
# 1, and its follower (attack s, release s, and the level x that opens it
# fully at 1): the hiss rides under the signal, so a tape at rest in
# silence is silence
TAPE_BUMP = (90.0, 1.0)
TAPE_TOP = (6000.0, 1.6)
TAPE_LOW = 25.0
TAPE_HISS = 10 ** (-48 / 20)
TAPE_HISS_FOLLOW = (0.01, 0.3, 20.0)


def tick_noise(index, channel: int = 0) -> np.ndarray:
    """White noise -1..1 as a function of the sample's index alone (a
    32-bit hash, murmur3's finaliser, of 2 x index + channel): the same
    noise at the same sample however a buffer is cut, and the live twin's
    to the bit (`Math.imul`)."""
    h = (np.asarray(index, dtype=np.uint64) * 2 + channel).astype(np.uint32)
    h = h * np.uint32(0x9E3779B1)
    h ^= h >> np.uint32(16)
    h *= np.uint32(0x85EBCA6B)
    h ^= h >> np.uint32(13)
    h *= np.uint32(0xC2B2AE35)
    h ^= h >> np.uint32(16)
    return h.astype(np.float64) / 2147483648.0 - 1.0


def tape_filters(bump: float, tone: float, sr: int = SR):
    """The tape's fixed filters' (b, a): the head bump (None at 0 dB), the
    top's low-pass and the low cut."""
    peak = peaking_coeffs(TAPE_BUMP[0], bump, TAPE_BUMP[1], sr) if bump else None
    top = TAPE_TOP[0] * 2.0 ** (TAPE_TOP[1] * float(tone))
    low = biquad_coeffs("highpass", TAPE_LOW, 0.707, sr)
    return peak, biquad_coeffs("lowpass", top, 0.707, sr), low


def _tape(
    x, drive=6.0, bump=2.0, tone=0.6, wow=0.2, flutter=0.2, hiss=0.0, mix=1.0, sr=SR
):
    """Tape saturation: the head wavers (`wow`: the tape echo's wander,
    `flutter`: its fast sine — a moving delay, so a wavering pitch; at
    neither, no delay at all), `drive` dB into tanh and back out of it (a
    quiet signal at unity, the peaks rounded), the hiss under it, a head
    bump in the lows, the top rolled off (`tone`) and the lows under the
    bump (`TAPE_LOW`). `drive` may be a per-sample array (a lane)."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    depth, fl, hz = TAPE_WOW
    reach = wow * depth + flutter * fl
    y = st
    if reach > 0:
        flut = _sines(n, sr, [(hz, 0.0, 1.0)])
        move = wow * depth * wander(n, sr) + flutter * fl * flut
        y = _read_back(st, (reach + move) * sr)
    g = 10.0 ** (np.asarray(drive, dtype=np.float64) / 20.0)
    g = g[:, None] if g.ndim else g
    y = np.tanh(y * g) / g
    if hiss > 0:
        att, rel, opens = TAPE_HISS_FOLLOW
        env = _envelope_follower(np.max(np.abs(st), axis=1), att, rel, sr)
        level = hiss * TAPE_HISS * np.minimum(1.0, env * opens)
        idx = np.arange(n)
        y = y + np.column_stack([tick_noise(idx, c) * level for c in (0, 1)])
    peak, top, low = tape_filters(bump, tone, sr)
    if peak is not None:
        y = _lfilter(*peak, y)
    wet = _lfilter(*low, _lfilter(*top, y))
    res = st * (1 - mix) + wet * mix
    return res if is_stereo(x) else res.mean(axis=1)


def tape(drive=6.0, bump=2.0, tone=0.6, wow=0.2, flutter=0.2, hiss=0.0, mix=1.0):
    return partial(
        _tape,
        drive=drive,
        bump=bump,
        tone=tone,
        wow=wow,
        flutter=flutter,
        hiss=hiss,
        mix=mix,
    )


def _pingpong(
    x,
    time=0.375,
    feedback=0.45,
    lowcut=120.0,
    highcut=6000.0,
    width=1.0,
    mix=0.35,
    sr=SR,
):
    """A ping-pong delay: the input (both channels summed) onto the left
    line, which feeds the right, which feeds back to the left — every
    bounce `time` s after the last, `feedback` down and through the loop's
    filters (a high-pass at `lowcut`, a low-pass at `highcut`). The left
    echoes sit at -`width`, the right at +`width` (0: both centred). wet =
    the input + the echoes, as the delay's: `mix` is the echoes' level.
    `feedback` may be a per-sample array (a lane). Computed `time` at a
    time: each block reads only what the one before it wrote."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    d = n_samples(time, sr)
    fb = as_array(feedback, n)
    mono = st.mean(axis=1)
    hb, ha = biquad_coeffs("highpass", lowcut, 0.707, sr)
    lb, la = biquad_coeffs("lowpass", highcut, 0.707, sr)
    z = np.zeros((2, 2, 2))  # [side][high-pass, low-pass] state
    left, right = np.zeros(n), np.zeros(n)
    for i in range(d, n, d):
        j = min(i + d, n)
        ins = (
            mono[i - d : j - d] + fb[i:j] * right[i - d : j - d],
            fb[i:j] * left[i - d : j - d],
        )
        for side, (u, out) in enumerate(zip(ins, (left, right), strict=True)):
            u, z[side, 0] = _linear_filter(hb, ha, u, -1, z[side, 0])
            out[i:j], z[side, 1] = _linear_filter(lb, la, u, -1, z[side, 1])
    near, far = (1.0 + width) / 2.0, (1.0 - width) / 2.0
    echoes = np.column_stack([left * near + right * far, right * near + left * far])
    res = st * (1 - mix) + (st + echoes) * mix
    return res if is_stereo(x) else res.mean(axis=1)


def pingpong(
    time=0.375, feedback=0.45, lowcut=120.0, highcut=6000.0, width=1.0, mix=0.35
):
    return partial(
        _pingpong,
        time=time,
        feedback=feedback,
        lowcut=lowcut,
        highcut=highcut,
        width=width,
        mix=mix,
    )


# The spring tank: two springs (the left's and the right's round trip, s).
# Each pass goes through SPRING_STAGES stretched allpasses (a + z^-K) /
# (1 + a z^-K), K = sr / (2 SPRING_CHIRP): the dispersion. With `a` below
# zero the lows come round later than the highs, so every pass is a
# falling chirp over 0..SPRING_CHIRP Hz — the "drip" — its spread set by
# `a`, SPRING_DRIP[0] at drip 0 to [1] at 1. Then a one-pole low-pass
# (`tone`: SPRING_TONE[0] Hz + [1] Hz at 1), each pass darker; the tank's
# lows cut at SPRING_LOW_CUT, and its top (the chirp's images over
# SPRING_CHIRP) at SPRING_TOP
SPRING_DELAYS = (0.037, 0.0431)
SPRING_STAGES = 40
SPRING_CHIRP = 3600.0
SPRING_DRIP = (-0.35, -0.75)
SPRING_TONE = (2500.0, 4500.0)
SPRING_LOW_CUT = 160.0
SPRING_TOP = 4800.0


def spring_ir(decay=2.0, tone=0.5, drip=0.5, sr=SR) -> np.ndarray:
    """The spring's impulse response, wet gain included (as `plate_ir`'s:
    unit energy less 1.5 dB). Each spring is a loop — the dispersive
    allpass chain, the low-pass, its round trip, a gain that falls 60 dB
    over `decay` s of passes (a pass as long as its trip and the chain's
    delay midway between its lows' and its highs') — computed in closed
    form: its response is G / (1 - G) for the loop's G, on a transform long
    enough that what wraps round is 150 dB down. Deterministic: no noise
    at all."""
    n = n_samples(decay * 1.2, sr)
    nfft = 1 << int(np.ceil(np.log2(max(n, decay * 2.5 * sr))))
    w = 2 * np.pi * np.fft.rfftfreq(nfft)
    k = max(1, int(round(sr / (2.0 * SPRING_CHIRP))))
    a = SPRING_DRIP[0] + (SPRING_DRIP[1] - SPRING_DRIP[0]) * float(drip)
    zk = np.exp(-1j * w * k)
    chain = ((a + zk) / (1.0 + a * zk)) ** SPRING_STAGES
    lag = SPRING_STAGES * k * ((1 - a) / (1 + a) + (1 + a) / (1 - a)) / 2
    p = float(np.exp(-2 * np.pi * (SPRING_TONE[0] + SPRING_TONE[1] * tone) / sr))
    lp = (1.0 - p) / (1.0 - p * np.exp(-1j * w))
    ir = np.empty((n, 2))
    for c, trip in enumerate(SPRING_DELAYS):
        d = n_samples(trip, sr)
        g = 10.0 ** (-3.0 * (d + lag) / (float(decay) * sr))
        loop = g * chain * lp * np.exp(-1j * w * d)
        ir[:, c] = np.fft.irfft(loop / (1.0 - loop), nfft)[:n]
    ir = _per_channel(biquad, ir, "highpass", SPRING_LOW_CUT, 0.707, sr)
    ir = _per_channel(biquad, ir, "lowpass", SPRING_TOP, 0.707, sr)
    energy = float(np.sqrt(np.mean(np.sum(ir**2, axis=0)))) or 1.0
    return ir * (_REVERB_WET / energy)


@lru_cache(maxsize=8)
def _spring_tank(decay, tone, drip, sr) -> np.ndarray:
    """`spring_ir`, cached for the render path (read-only: renders share it)."""
    ir = spring_ir(decay, tone, drip, sr)
    ir.flags.writeable = False
    return ir


def _spring(x, decay=2.0, tone=0.5, drip=0.5, mix=0.3, sr=SR):
    """A spring reverb: the input convolved with `spring_ir`, blended as the
    plate is. The live keys load the same IR into a ConvolverNode (GET
    /fx/ir.wav?kind=spring)."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    args = (float(decay), float(tone), float(drip), int(sr))
    ir = _spring_tank(*args)
    nfft = sp_fft.next_fast_len(n + len(ir) - 1, True)
    spec = sp_fft.rfft(st, nfft, axis=0) * _ir_spectrum("spring", args, ir, nfft)
    wet = sp_fft.irfft(spec, nfft, axis=0)[:n]
    res = st * (1 - mix) + wet * mix
    return res if is_stereo(x) else res.mean(axis=1)


def spring(decay=2.0, tone=0.5, drip=0.5, mix=0.3):
    return partial(_spring, decay=decay, tone=tone, drip=drip, mix=mix)


# The formant shifter's bank: FORMANT_BANDS log-spaced over FORMANT_RANGE
# (about a fifth of an octave apart); their followers' attack and release
# (s); the most a band is cut or lifted (linear: -24 / +18 dB); the
# followers' floor; the bells' coefficient block (samples, as `biquad`'s)
FORMANT_BANDS = 32
FORMANT_RANGE = (120.0, 8000.0)
FORMANT_FOLLOW = (0.005, 0.04)
FORMANT_GAIN = (1.0 / 16.0, 8.0)
FORMANT_FLOOR = 1e-4
FORMANT_BLOCK = 64


def formant_bands() -> tuple[np.ndarray, float]:
    """The formant shifter's band centres (Hz) and their Q (each band as
    wide as the step to the next)."""
    lo, hi = FORMANT_RANGE
    centres = np.geomspace(lo, hi, FORMANT_BANDS)
    return centres, bandwidth_q(np.log2(hi / lo) / (FORMANT_BANDS - 1))


def formant_taps(shift: float) -> list[tuple[int, float] | None]:
    """Where each band reads its new level: the bank's index of its centre
    moved down `shift` semitones (f / r), as (the band below, the fraction
    toward the one above) — None for a band whose f / r is off the bank
    (it is left alone)."""
    lo, hi = FORMANT_RANGE
    step = 12.0 * np.log2(hi / lo) / (FORMANT_BANDS - 1)  # semitones a band
    out: list[tuple[int, float] | None] = []
    for i in range(FORMANT_BANDS):
        p = i - float(shift) / step
        if p < 0.0 or p > FORMANT_BANDS - 1:
            out.append(None)
            continue
        j = min(int(np.floor(p)), FORMANT_BANDS - 2)
        out.append((j, float(p - j)))
    return out


def _formant_shift(x, shift=4.0, mix=1.0, sr=SR):
    """A formant shifter that keeps the pitch: the spectral envelope moved
    `shift` semitones, the harmonics left where they are. The input (both
    channels summed) is followed in each band of `formant_bands` (two
    band-passes cascaded, as the vocoder's); each band then takes the level
    the input has where its centre moves from (f / r, between two bands:
    `formant_taps`) over its own — a graphic EQ in series (Simper's bells,
    `_eq_swept`'s, their gains held over each 64-sample block) whose curve
    copies the envelope across. Series, not summed: a cut beside a lift
    stays a cut. At 0 semitones every ratio is exactly 1: the input passes
    untouched."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    wet = st
    if float(shift) != 0.0:
        mono = st.mean(axis=1)
        centres, q = formant_bands()
        att, rel = FORMANT_FOLLOW
        blk = FORMANT_BLOCK
        level = [
            _envelope_follower(_band(mono, f, q, sr), att, rel, sr)[::blk]
            for f in centres
        ]
        wet = np.array(st, dtype=np.float64, copy=True)
        for f, have, tap in zip(centres, level, formant_taps(shift), strict=True):
            if tap is None:
                continue
            j, fr = tap
            want = level[j] * (1.0 - fr) + level[j + 1] * fr
            g = np.clip((want + FORMANT_FLOOR) / (have + FORMANT_FLOOR), *FORMANT_GAIN)
            a = np.repeat(np.sqrt(g), blk)[:n]  # the bell's A, held a block
            bp = biquad(wet, "bandpass", f, q * a, sr, blk)
            wet = wet + (a * a - 1.0)[:, None] * bp
    res = st * (1 - mix) + wet * mix
    return res if is_stereo(x) else res.mean(axis=1)


def formant_shift(shift=4.0, mix=1.0):
    return partial(_formant_shift, shift=shift, mix=mix)


# The frequency shifter's Hilbert pair (Olli Niemitalo's: two chains of four
# second-order allpasses, y = a^2 (x + y[n-2]) - x[n-2], the first chain a
# sample late; their outputs 90 degrees apart within 0.7 degrees from 20 Hz
# to 0.98 of Nyquist), and the feedback loop's length (s)
HILBERT_PAIR = (
    (0.6923878, 0.9360654322959, 0.9882295226860, 0.9987488452737),
    (0.4021921162426, 0.8561710882420, 0.9722909545651, 0.9952884791278),
)
FREQSHIFT_LOOP = 0.01


def _hilbert_sections() -> list[list[tuple[np.ndarray, np.ndarray]]]:
    return [
        [(np.array([a * a, 0.0, -1.0]), np.array([1.0, 0.0, -a * a])) for a in path]
        for path in HILBERT_PAIR
    ]


def _freqshift(x, shift=30.0, feedback=0.0, mix=0.5, sr=SR):
    """A frequency shifter (single sideband): every component moved `shift`
    Hz (negative: down) — not a pitch shift, so harmonics stop being
    harmonic: a bell, a metallic voice, and at a few Hz a slow barber-pole
    phasing against the dry. The Hilbert pair splits the signal into two
    halves 90 degrees apart (the second leads); re cos + im sin keeps the
    upper sideband of a positive shift.
    `feedback` sends the shifted signal back in FREQSHIFT_LOOP s later, so
    each pass climbs (or falls) another `shift`: the spiral. Computed a
    loop's length at a time when it feeds back."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    theta = 2 * np.pi * float(shift) * time_axis(n, sr)
    cos, sin = np.cos(theta)[:, None], np.sin(theta)[:, None]
    secs = _hilbert_sections()
    zs = [[np.zeros((2, 2)) for _ in path] for path in secs]
    held = np.zeros((1, 2))  # the first chain's last sample: its one-sample delay
    d = n_samples(FREQSHIFT_LOOP, sr)
    fb = float(feedback)
    step = d if fb else n
    y = np.zeros((n, 2))
    for i in range(0, n, step):
        j = min(i + step, n)
        u = st[i:j] if not fb or i < d else st[i:j] + fb * y[i - d : j - d]
        halves = []
        for c, path in enumerate(secs):
            v = np.ascontiguousarray(u, dtype=np.float64)
            for k, (b, a) in enumerate(path):
                v, zs[c][k] = _linear_filter(b, a, v, 0, zs[c][k])
            halves.append(v)
        late = np.vstack([held, halves[0]])
        held = late[-1:]
        y[i:j] = late[:-1] * cos[i:j] + halves[1] * sin[i:j]
    res = st * (1 - mix) + y * mix
    return res if is_stereo(x) else res.mean(axis=1)


def freqshift(shift=30.0, feedback=0.0, mix=0.5):
    return partial(_freqshift, shift=shift, feedback=feedback, mix=mix)


EXCITER_BIAS = 0.3  # the shaper's offset: even harmonics as well as odd


def _exciter(x, freq=3000.0, drive=6.0, amount=0.3, sr=SR):
    """An exciter (the aural exciter's trick): the band over `freq` (a
    high-pass) driven through an offset tanh — new harmonics, odd and even,
    from what is up there already — high-passed again (no low intermod, no
    offset) and `amount` of it added to the input: air and presence that
    are not an EQ's lift of the hiss. Small-signal unity, so `drive` moves
    the harmonics, `amount` the level (the curve's slope at rest is tanh's
    at the bias, divided out). `amount` may be a per-sample array."""
    st = to_stereo(x)
    if len(st) == 0:
        return x
    b, a = biquad_coeffs("highpass", freq, 0.707, sr)
    band = _lfilter(b, a, st)
    g, off = float(drive), np.tanh(EXCITER_BIAS)
    shaped = (np.tanh(band * g + EXCITER_BIAS) - off) / (g * (1.0 - off * off))
    add = _lfilter(b, a, shaped)
    amt = np.asarray(amount, dtype=np.float64)
    res = st + (amt[:, None] if amt.ndim else amt) * add
    return res if is_stereo(x) else res.mean(axis=1)


def exciter(freq=3000.0, drive=6.0, amount=0.3):
    return partial(_exciter, freq=freq, drive=drive, amount=amount)


# The rack's vocoder: its own carrier, a chord of band-limited saws (each
# chord's scale degrees over the key's root, in an octave); its bands span
# the vocoder's range; the followers' attack and release (s); a fixed
# makeup, and each band's tilt (x (f / lo)^VOCODER_FX_TILT: a saw's bands
# fall 3 dB an octave, this stands them level)
VOCODER_FX_CHORDS = {
    "triad": (0, 2, 4),
    "seventh": (0, 2, 4, 6),
    "power": (0, 4, 7),
    "sus": (0, 3, 4),
    "open": (0, 4, 9),
}
VOCODER_FX_FOLLOW = (0.005, 0.05)
VOCODER_FX_GAIN = 12.0
VOCODER_FX_TILT = 0.5


def chord_freqs(key=0, scale=CHROMATIC, chord="triad", octave=3) -> list[float]:
    """The vocoder's carrier chord in Hz: `chord`'s scale degrees over the
    key's root in `octave` (C3 = MIDI 48)."""
    scale = tuple(sorted({int(s) % 12 for s in scale})) or CHROMATIC
    root = 12 * (int(octave) + 1) + int(key)
    degs = VOCODER_FX_CHORDS[chord]
    notes = [root + _scale_note(0, scale, d) - scale[0] for d in degs]
    return [float(440.0 * 2.0 ** ((m - 69) / 12.0)) for m in notes]


def blep_saw(freq: float, n: int, sr: int = SR) -> np.ndarray:
    """A saw at `freq` from phase 0 on the buffer's first sample, its phase
    a function of time (`lfo_phase`), PolyBLEP's two-sample correction at
    the wrap (`core._polyblep`)."""
    t = lfo_phase(freq, n, sr)
    return (2.0 * t - 1.0) - _polyblep(t, np.full(n, float(freq) / sr))


def vocoder_bands(bands: int, sr: int = SR) -> tuple[np.ndarray, float]:
    """The rack vocoder's band centres (Hz) and their Q."""
    centres = np.geomspace(VOCODER_LO, VOCODER_HI, int(bands))
    return centres, bandwidth_q(np.log2(VOCODER_HI / VOCODER_LO) / (int(bands) - 1))


def vocoder_tilts(centres: np.ndarray) -> np.ndarray:
    """Each rack-vocoder band's gain: the makeup, tilted up the bands."""
    return VOCODER_FX_GAIN * (np.asarray(centres) / VOCODER_LO) ** VOCODER_FX_TILT


def _vocoder_fx(x, notes=(130.81,), bands=16, shift=0.0, noise=0.1, mix=1.0, sr=SR):
    """A vocoder in the rack: the input (both channels summed) is the
    modulator, its own chord (`notes`, Hz: `chord_freqs`) plus `noise` of
    white noise (the consonants' carrier) the carrier. Band by band — as
    `vocoder`'s, constant Q, two band-passes cascaded — the modulator's
    follower sets the carrier band's level; `shift` semitones moves the
    carrier's bands off the modulator's (the formants move). Causal and
    level-fixed, where `vocoder` matches the whole take's level."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    mono = st.mean(axis=1)
    carrier = np.zeros(n)
    for f in notes:
        carrier += blep_saw(f, n, sr)
    carrier /= max(1, len(notes))
    if noise > 0:
        carrier += float(noise) * tick_noise(np.arange(n))
    centres, q = vocoder_bands(bands, sr)
    r = 2.0 ** (float(shift) / 12.0)
    att, rel = VOCODER_FX_FOLLOW
    out = np.zeros(n)
    for f, tilt in zip(centres, vocoder_tilts(centres), strict=True):
        env = _envelope_follower(_band(mono, f, q, sr), att, rel, sr)
        out += env * tilt * _band(carrier, f * r, q, sr)
    res = st * (1 - mix) + out[:, None] * mix
    return res if is_stereo(x) else res.mean(axis=1)


def vocoder_fx(notes=(130.81,), bands=16, shift=0.0, noise=0.1, mix=1.0):
    return partial(
        _vocoder_fx,
        notes=tuple(notes),
        bands=int(bands),
        shift=shift,
        noise=noise,
        mix=mix,
    )


# ----------------------------------------------------------------------------
# the mix's dynamics — a three-band upward and
# downward compressor (the "over the top" squash), the rack's limiter and a
# transient shaper. Causal like the units above: every follower and filter
# runs forward from silence, nothing is matched to the buffer, and the
# limiter's lookahead is a delay (its latency, the same in the loop and
# live) rather than a read of the future. Their live twins are in
# static/fx2-worklet.js. The envelope filter is not a unit of its own: the
# auto-filter's `env` (1: it opens as the input hits, -1: it closes) is it.
# ----------------------------------------------------------------------------

OTT_SPLIT = (120.0, 2500.0)  # Hz: the low / mid and mid / high crossovers
OTT_Q = 0.5**0.5  # a Linkwitz-Riley half: this Butterworth biquad, run twice
# each band's follower (low, mid, high): attack and release, seconds at
# `time` 1 — the lows slower, so a bass is squashed and not torn
OTT_TIMES = ((0.02, 0.25), (0.01, 0.15), (0.004, 0.1))
OTT_DOWN = (-24.0, 8.0)  # dB over which a band is turned down, the ratio
OTT_UP = (-30.0, 4.0)  # dB under which a band is lifted, the ratio
OTT_UP_MAX = 24.0  # dB: the most a band is lifted (a tail, a room's floor)


def allpass_coeffs(fc: float, q: float, sr: int = SR):
    """RBJ's second-order all-pass (b, a): flat in level, its phase the
    phase a Linkwitz-Riley pair at `fc` sums to (LP^2 + HP^2 at the
    Butterworth Q is exactly this filter)."""
    w0 = 2 * np.pi * float(np.clip(fc, 20.0, sr * 0.49)) / sr
    al, cw = np.sin(w0) / (2 * max(q, 0.05)), np.cos(w0)
    a0 = 1 + al
    b = np.asarray([1 - al, -2 * cw, 1 + al]) / a0
    a = np.asarray([1.0, -2 * cw / a0, (1 - al) / a0])
    return b, a


def ott_bands(st: np.ndarray, sr: int = SR) -> tuple[np.ndarray, ...]:
    """A stereo buffer in three bands (low, mid, high) that sum to the
    input through two all-passes: Linkwitz-Riley 4th-order crossovers at
    `OTT_SPLIT`, the low band through the upper split's all-pass so the
    three line up in phase."""
    lo_f, hi_f = OTT_SPLIT
    lp1, hp1 = (biquad_coeffs(k, lo_f, OTT_Q, sr) for k in ("lowpass", "highpass"))
    lp2, hp2 = (biquad_coeffs(k, hi_f, OTT_Q, sr) for k in ("lowpass", "highpass"))
    low = _lfilter(*lp1, _lfilter(*lp1, st))
    rest = _lfilter(*hp1, _lfilter(*hp1, st))
    mid = _lfilter(*lp2, _lfilter(*lp2, rest))
    high = _lfilter(*hp2, _lfilter(*hp2, rest))
    return _lfilter(*allpass_coeffs(hi_f, OTT_Q, sr), low), mid, high


def ott_gain(env: np.ndarray, upward=1.0, downward=1.0) -> np.ndarray:
    """A band's gain (linear) for its follower's level: over `OTT_DOWN`'s
    threshold turned down at its ratio (x `downward`), under `OTT_UP`'s
    lifted at its ratio, at most `OTT_UP_MAX` dB (x `upward`)."""
    lev = _db(env)
    down = np.minimum(0.0, (lev - OTT_DOWN[0]) * (1.0 / OTT_DOWN[1] - 1.0))
    up = np.clip((OTT_UP[0] - lev) * (1.0 - 1.0 / OTT_UP[1]), 0.0, OTT_UP_MAX)
    return 10.0 ** ((downward * down + upward * up) / 20.0)


def _ott(
    x,
    depth=0.5,
    time=1.0,
    upward=1.0,
    downward=1.0,
    low=0.0,
    mid=0.0,
    high=0.0,
    output=0.0,
    sr=SR,
):
    """The over-the-top squash: three bands (`ott_bands`), each one's
    level (its louder channel, `_envelope_follower` at `OTT_TIMES` x
    `time`) pulled toward the middle from both sides — the loud parts
    down, the quiet ones (tails, breaths, a room) up — at its own gain
    (`low`, `mid`, `high` dB), blended band by band with the band itself
    at `depth` (the famous knob: parallel compression; at 0 the bands
    summed, the input through two all-passes), `output` dB after. `depth`
    may be a per-sample array (a lane)."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    d = np.asarray(depth, dtype=np.float64)
    d = d[:, None] if d.ndim else d
    res = np.zeros_like(st)
    for band, (att, rel), gain in zip(
        ott_bands(st, sr), OTT_TIMES, (low, mid, high), strict=True
    ):
        level = np.max(np.abs(band), axis=1)
        env = _envelope_follower(level, att * time, rel * time, sr)
        g = ott_gain(env, upward, downward) * 10.0 ** (float(gain) / 20.0)
        res += band * ((1 - d) + d * g[:, None])
    res = res * 10.0 ** (float(output) / 20.0)
    return res if is_stereo(x) else res.mean(axis=1)


def ott(
    depth=0.5,
    time=1.0,
    upward=1.0,
    downward=1.0,
    low=0.0,
    mid=0.0,
    high=0.0,
    output=0.0,
):
    return partial(
        _ott,
        depth=depth,
        time=time,
        upward=upward,
        downward=downward,
        low=low,
        mid=mid,
        high=high,
        output=output,
    )


def limiter_gain(
    peak: np.ndarray, ceiling=-1.0, release=0.08, w: int = 0, sr: int = SR
) -> np.ndarray:
    """The limiter's gain for each output sample, from the input's peaks
    (`peak`: the louder channel, its `gain` applied): the gain each sample
    needs to sit at `ceiling` dB, held over the `w` + 1 samples it takes
    to reach the output (a sliding minimum: the window behind now is the
    window ahead of the delayed signal), let go over `release` (a one-pole
    the held gain is never above), then averaged over the same `w` + 1
    samples — a ramp down that lands as the peak does, never above the
    gain that peak needs. Before the buffer is silence (gain 1), as it is
    for the live twin."""
    c = 10.0 ** (float(ceiling) / 20.0)
    want = np.minimum(1.0, c / np.maximum(peak, 1e-9))
    hold = want
    if w:  # the lowest of the last w + 1 (a trailing window), ones before
        hold = minimum_filter1d(want, w + 1, mode="constant", cval=1.0, origin=w // 2)
    r = float(np.exp(-1.0 / max(float(release) * sr, 1.0)))
    smooth = _lfilter([1.0 - r], [1.0, -r], hold, zi=[r])
    env = np.minimum(hold, smooth)
    if not w:
        return env
    s = np.cumsum(np.concatenate([np.ones(w + 1), env]))
    return (s[w + 1 :] - s[: len(env)]) / (w + 1)


def _limiter(x, gain=0.0, ceiling=-1.0, release=0.08, lookahead=0.002, sr=SR):
    """The rack's limiter: `gain` dB in, the peaks held at `ceiling` dB
    (`limiter_gain`), the signal delayed by `lookahead` seconds so the gain
    is down before a peak arrives — a lookahead limiter's latency, which
    the loop and the live keys share (no read of the future: `limit`, the
    song master's, looks ahead in its buffer instead and is no rack unit).
    At 0 lookahead nothing is delayed and a peak is caught on its own
    sample (a step). `gain` may be a per-sample array (a lane)."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    g = 10.0 ** (np.asarray(gain, dtype=np.float64) / 20.0)
    y = st * (g[:, None] if g.ndim else g)
    w = int(round(max(0.0, float(lookahead)) * sr))
    env = limiter_gain(np.max(np.abs(y), axis=1), ceiling, release, w, sr)
    if w:
        y = np.concatenate([np.zeros((min(w, n), 2)), y[: max(0, n - w)]])
    res = y * env[:, None]
    return res if is_stereo(x) else res.mean(axis=1)


def limiter(gain=0.0, ceiling=-1.0, release=0.08, lookahead=0.002):
    return partial(
        _limiter, gain=gain, ceiling=ceiling, release=release, lookahead=lookahead
    )


# The transient shaper's detectors (seconds). Each reads a peak follower
# (`peak_follow`: up at once, down at its release); the attack's is that
# follower against itself smoothed over TRANSIENT_SLOW (how far a hit's
# front runs ahead), the sustain's a long release against a short one (how
# far the note hangs on as it dies), smoothed over TRANSIENT_SMOOTH. The
# floors are a steady low tone's own ripple (30 Hz: 2.9 and 1.2 dB) and are
# never read; each dB past one is TRANSIENT_SCALE dB of gain, each term at
# most TRANSIENT_MAX dB
TRANSIENT_PEAK = 0.05
TRANSIENT_SLOW = 0.03
TRANSIENT_SHORT = 0.1
TRANSIENT_LONG = 0.6
TRANSIENT_SMOOTH = 0.003
TRANSIENT_FLOOR = (4.0, 1.5)
TRANSIENT_SCALE = 0.5
TRANSIENT_MAX = 12.0


def peak_follow(level: np.ndarray, release: float, sr: int = SR) -> np.ndarray:
    """A peak follower: e[n] = max(level[n], e[n - 1] r), r = exp(-1 /
    (release sr)), silence before the buffer — as a running maximum in the
    log domain (max over k of level[k] r^(n - k)), so no loop."""
    t = float(release) * sr
    k = np.arange(len(level)) / t
    lg = np.log(np.maximum(level, 1e-12)) + k
    return np.exp(np.maximum.accumulate(lg) - k) if len(level) else level


def transient_db(level: np.ndarray, sr: int = SR) -> tuple[np.ndarray, np.ndarray]:
    """(the attack's dB, the sustain's dB) a sample of `level` (|the
    input|, the louder channel), each past its floor, 0 on a steady tone
    and at most TRANSIENT_MAX / TRANSIENT_SCALE: how far the peak runs
    ahead of itself smoothed as a hit lands (from silence, the whole range
    at once, falling over ~60 ms), how far a long release hangs over a
    short one as a note dies (none while a hit lands)."""
    top = TRANSIENT_MAX / TRANSIENT_SCALE
    pk = peak_follow(level, TRANSIENT_PEAK, sr)
    lag = one_pole(pk, float(np.exp(-1.0 / (TRANSIENT_SLOW * sr))))
    hang = _db(peak_follow(level, TRANSIENT_LONG, sr))
    held = hang - _db(peak_follow(level, TRANSIENT_SHORT, sr))
    held = one_pole(held, float(np.exp(-1.0 / (TRANSIENT_SMOOTH * sr))))
    att = np.clip(_db(pk) - _db(lag) - TRANSIENT_FLOOR[0], 0.0, top)
    # a hit landing is no tail: the last note's sustain lets go at once
    sus = np.clip(held - TRANSIENT_FLOOR[1], 0.0, top)
    return att, np.where(att > 0.0, 0.0, sus)


def _transient(x, attack=0.0, sustain=0.0, output=0.0, sr=SR):
    """A transient shaper: `attack` (-1..1) lifts or softens each hit's
    front, `sustain` (-1..1) its body and tail (`transient_db`: up to
    TRANSIENT_MAX dB each way), `output` dB after. Linked on the louder
    channel. `attack` and `sustain` may be per-sample arrays (lanes)."""
    st = to_stereo(x)
    n = len(st)
    if n == 0:
        return x
    a_db, s_db = transient_db(np.max(np.abs(st), axis=1), sr)
    g_db = attack * TRANSIENT_SCALE * a_db + sustain * TRANSIENT_SCALE * s_db
    res = st * (10.0 ** ((g_db + float(output)) / 20.0))[:, None]
    return res if is_stereo(x) else res.mean(axis=1)


def transient(attack=0.0, sustain=0.0, output=0.0):
    return partial(_transient, attack=attack, sustain=sustain, output=output)
