"""Programme loudness (ITU-R BS.1770-4): how loud a finished mix *is*, in
LUFS, rather than how high its loudest sample reaches.

A peak says nothing about level — a mix with one stray transient and a
brick of a mix both peak at 0 dBFS — which is why the studio's loop
normaliser reads a percentile instead (`render.normalize_master`). An
export wants the number the outside world uses: streaming services turn
everything to about -14 LUFS, so that is the level worth mastering to.

`integrated_loudness` is the standard's measure, in its order:

1. **K-weighting**: a high shelf (+4 dB above ~1.7 kHz, the head's
   acoustic effect) then the RLB high-pass (~38 Hz), per channel. The
   standard tabulates both biquads at 48 kHz only; `k_weighting` designs
   them from the analogue prototypes those numbers came from, so it gives
   the tabulated coefficients at 48 kHz and the same curve at any rate
   (the engine's `SR` is 44.1 kHz).
2. **Blocks**: the mean square of 400 ms blocks, 75 % overlapped (a hop of
   100 ms), summed over the channels (left and right both weigh 1).
3. **Gating**: blocks under -70 LUFS are dropped (absolute gate: silence
   between sections must not drag the reading down), then blocks more
   than 10 LU under the mean of what is left (relative gate: neither must
   a quiet intro). The loudness is the mean of the survivors.

Pure functions of an array: nothing here knows about the studio.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.signal import lfilter, resample_poly

from ..core import SR, _lfilter

BLOCK_SECONDS = 0.4
HOP_SECONDS = 0.1  # 75 % overlap
ABSOLUTE_GATE = -70.0  # LUFS
RELATIVE_GATE = -10.0  # LU under the absolute-gated loudness
# -0.691 makes a 997 Hz full-scale sine in one channel read -3.01 LKFS
# (K-weighting's gain at 997 Hz is +0.691 dB)
_OFFSET = -0.691

# The analogue prototypes of the standard's two stages (the values its
# 48 kHz table bilinear-transforms from)
_SHELF_HZ, _SHELF_DB, _SHELF_Q = (
    1681.974450955533,
    3.999843853973347,
    0.7071752369554196,
)
_HIGHPASS_HZ, _HIGHPASS_Q = 38.13547087602444, 0.5003270373238773

# `true_peak_db` oversamples this many frames at a time, with this many of
# the neighbouring block's on each side (scipy's `resample_poly` filter is
# 10 input frames a side at any ratio)
_LOUDNESS_HOPS = 256  # hops K-weighted at a time (`block_loudness`: 25.6 s)
_TP_BLOCK = 1 << 16
_TP_PAD = 64


def k_weighting(sr: int = SR) -> list[tuple[np.ndarray, np.ndarray]]:
    """The K-weighting filter as two biquads, (b, a) each: the high shelf,
    then the RLB high-pass. At 48 kHz these are BS.1770's own table."""
    k = math.tan(math.pi * _SHELF_HZ / sr)
    vh = 10 ** (_SHELF_DB / 20)
    vb = vh**0.4996667741545416
    a0 = 1 + k / _SHELF_Q + k * k
    shelf = (
        np.array(
            [
                (vh + vb * k / _SHELF_Q + k * k) / a0,
                2 * (k * k - vh) / a0,
                (vh - vb * k / _SHELF_Q + k * k) / a0,
            ]
        ),
        np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / _SHELF_Q + k * k) / a0]),
    )
    k = math.tan(math.pi * _HIGHPASS_HZ / sr)
    a0 = 1 + k / _HIGHPASS_Q + k * k
    highpass = (
        np.array([1.0, -2.0, 1.0]),
        np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / _HIGHPASS_Q + k * k) / a0]),
    )
    return [shelf, highpass]


def _channels(x: np.ndarray) -> np.ndarray:
    """(n, channels) float64: a mono array is one channel."""
    x = np.asarray(x, dtype=np.float64)
    return x[:, None] if x.ndim == 1 else x


def block_loudness(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """Each 400 ms block's mean square, K-weighted and summed over the
    channels (linear power, not dB: gating averages these). Empty when the
    signal is shorter than one block."""
    x = np.asarray(x)
    x = x[:, None] if x.ndim == 1 else x
    size, hop = int(round(BLOCK_SECONDS * sr)), int(round(HOP_SECONDS * sr))
    if len(x) < size:
        return np.zeros(0)
    if size % hop:  # a rate whose block is no whole number of hops
        y = np.ascontiguousarray(_channels(x))
        for b, a in k_weighting(sr):
            y = _lfilter(b, a, y)
        # running sum of squares: a block's energy is one subtraction
        total = np.concatenate([[0.0], np.cumsum((y * y).sum(axis=1))])
        starts = np.arange(0, len(x) - size + 1, hop)
        return (total[starts + size] - total[starts]) / size
    # The song export measures up to eight minutes of stereo, so K-weight it
    # a piece at a time (the filters' state carried across) and keep only
    # each hop's energy: a block is `size / hop` hops. Whole, this held
    # three float64 copies of the song (1 GB at 480 s); the reading is the
    # same to rounding.
    filters = k_weighting(sr)
    state = [np.zeros((len(a) - 1, x.shape[1])) for _b, a in filters]
    hops = len(x) // hop
    energy = np.empty(hops)
    per = _LOUDNESS_HOPS * hop
    for lo in range(0, hops * hop, per):
        y = np.asarray(x[lo : min(lo + per, hops * hop)], dtype=np.float64)
        for i, (b, a) in enumerate(filters):
            y, state[i] = lfilter(b, a, y, axis=0, zi=state[i])
        sq = (y * y).sum(axis=1).reshape(-1, hop).sum(axis=1)
        energy[lo // hop : lo // hop + len(sq)] = sq
    k = size // hop
    total = np.concatenate([[0.0], np.cumsum(energy)])
    return (total[k:] - total[:-k]) / size


def _lufs(power: float) -> float:
    return _OFFSET + 10 * math.log10(power) if power > 0 else -math.inf


def integrated_loudness(x: np.ndarray, sr: int = SR) -> float:
    """Gated programme loudness in LUFS. Mono `(n,)` is one channel;
    `(n, 2)` is left and right. `-inf` when there is nothing to measure:
    silence, a signal shorter than one 400 ms block, or one that never
    rises over the -70 LUFS absolute gate."""
    z = block_loudness(x, sr)
    z = z[z > 10 ** ((ABSOLUTE_GATE - _OFFSET) / 10)]
    if not len(z):
        return -math.inf
    relative = _lufs(float(z.mean())) + RELATIVE_GATE
    z = z[z > 10 ** ((relative - _OFFSET) / 10)]
    return _lufs(float(z.mean())) if len(z) else -math.inf


def peak_db(x: np.ndarray) -> float:
    """The loudest sample, in dBFS (`-inf` for silence)."""
    x = np.asarray(x)
    # max(|x|) without the |x| copy of a whole song
    peak = max(float(x.max()), -float(x.min())) if x.size else 0.0
    return 20 * math.log10(peak) if peak > 0 else -math.inf


def true_peak_db(x: np.ndarray, oversample: int = 4) -> float:
    """The peak of the waveform the samples describe, in dBTP: the loudest
    value after 4x oversampling (BS.1770 Annex 2). A converter reconstructs
    what lies *between* samples, and that can pass full scale while every
    sample sits under it."""
    x = np.asarray(x)
    x = x[:, None] if x.ndim == 1 else x
    if not x.size:
        return -math.inf
    # In blocks: the oversampled copy of a whole song at the length cap is
    # 4 x 170 MB, and this is read up to five times an export (1.5 GB at
    # the peak, measured). Each block is resampled with `_TP_PAD` frames of
    # its neighbours either side — the interpolation filter reaches ten —
    # and only its own samples are read, so the blocks' values are the
    # whole array's, bit for bit.
    n, peak = len(x), 0.0
    for a in range(0, n, _TP_BLOCK):
        b = min(n, a + _TP_BLOCK)
        lo, hi = max(0, a - _TP_PAD), min(n, b + _TP_PAD)
        # float64 a block at a time: never a second copy of a float32 song
        piece = np.asarray(x[lo:hi], dtype=np.float64)
        y = resample_poly(piece, oversample, 1, axis=0)
        own = y[(a - lo) * oversample : (b - lo) * oversample]
        peak = max(peak, float(np.max(np.abs(own))))
    return 20 * math.log10(peak) if peak > 0 else -math.inf
