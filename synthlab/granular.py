"""The granular oscillator (shape "granular"): the sampler's sample played
as a cloud of short grains laid on the output clock.

What it reads is the sampler's: `sampler_sample` (the audio by content,
`sampler.get`), its span (`sampler_start` / `sampler_end`) — or, with
`sampler_loop` "on", the loop inside it — and its pitch (`sampler_root`,
`sampler_keytrack`: a grain reads the source at the played key's ratio to
the root, as the sampler's tape read does). Chops, zones, the stretch, the
pitch mode and the crossfade are the sampler shape's alone.

The cloud (Patch `grain_*`):

- a grain starts every `1 / density` seconds of output, from the note's
  first sample (`onsets`); `density` is held to `MAX_SOUNDING / size`, so
  no more than 256 grains ever sound at once;
- each lasts `size` seconds, under a raised-cosine window whose fades
  cover the whole grain at `shape` 0 (a Hann window) and 5 % a side at 1
  (`window`);
- where its audio begins in the region is `pos` (read at the grain's
  first sample, so a curve or a route moves the cloud), plus `scan` x the
  seconds since the note began over the region's own length (1 = the
  position walks the region at the recording's speed, negative
  backwards, 0 holds), plus `spray` x a draw in -0.5..0.5 — wrapped
  around the region, which the cloud treats as circular for where grains
  begin (a grain's read runs on past the region's end into whatever the
  recording holds there: silence past its last sample);
- it reads at the key's rate (at its first sample: glide and bends move
  the grains that start after them), times `pitch_spray` semitones x a
  draw in -1..1; a reverse grain plays the same stretch of audio a forward
  one would, backwards ("both": each grain a draw's way).

**Every draw is a hash, never a random stream**: `core.hash_u(seed, 3k +
i)` for grain k, the seed crc32 of "grain:<MIDI number>:<copy>" — the
note's pitch alone (as analog drift's), and the oscillator copy (the
unison voice, osc2's + 100), so the note memo's renders and every cache
key hold, and a JS twin could draw the same numbers with `Math.imul`.

The cloud is summed incoherently: its level is held to unit power by
1 / sqrt(overlap x the window's mean square) once grains overlap
(`cloud_gain`), a function of the settings alone — nothing here reads the
note's own future, so the causal contract holds. Stereo out (n, 2): the
sample's two channels, as the sampler's. Zeros (n,) when there is nothing
to play (no sample, a region too short), as the sampler gives.

Live: the studio's voice lays this cloud grain for grain
(static/grain-worklet.js `cloud`, over the sample as stored — the live
payload's `raw` source), held to `render` by
tests/studio/test_live_granular.py and the parity corpus.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass

import numpy as np

from . import sampler
from .core import SR, as_array, hash_u

DIRECTIONS = ("forward", "reverse", "both")
SIZE = (0.005, 1.0)  # seconds a grain lasts
DENSITY = (1.0, 500.0)  # grains a second
PITCH_SPRAY = 12.0  # semitones either way at most
SCAN = 2.0  # region lengths a second either way at most (1 = real time)
MAX_SOUNDING = 256  # grains overlapping at once, at most
TAPER_MIN = 0.05  # the window's fade a side at shape 1, of the grain
MIN_LOOP = 64  # a loop shorter than this is no region (the sampler's rule)


@dataclass(frozen=True)
class Settings:
    """The Patch fields the granular shape reads, as `Synth._osc_stack`
    hands them over. `pos` may be a per-sample array (a curve, a route)."""

    sample: str = ""
    root: int = 60
    start: float = 0.0
    end: float = 1.0
    loop: str = "off"
    loop_start: float = 0.0
    loop_end: float = 1.0
    keytrack: str = "on"
    pos: object = 0.0
    size: float = 0.08
    density: float = 20.0
    spray: float = 0.0
    pitch_spray: float = 0.0
    shape: float = 0.0
    direction: str = "forward"
    scan: float = 0.0

    @classmethod
    def of(cls, p) -> Settings:
        return cls(
            p.sampler_sample,
            p.sampler_root,
            p.sampler_start,
            p.sampler_end,
            p.sampler_loop,
            p.sampler_loop_start,
            p.sampler_loop_end,
            p.sampler_keytrack,
            p.grain_pos,
            p.grain_size,
            p.grain_density,
            p.grain_spray,
            p.grain_pitch_spray,
            p.grain_shape,
            p.grain_dir,
            p.grain_scan,
        )


def _scalar(v, default: float, lo: float, hi: float) -> float:
    return min(max(sampler._first(v, default), lo), hi)


def grain_seed(key_hz: float, copy: int = 0) -> int:
    """The cloud's seed: crc32 of "grain:<MIDI number>:<copy>" — the note's
    pitch (rounded half up, as `synth.drift_seed`) and the oscillator copy."""
    midi = math.floor(69 + 12 * math.log2(max(float(key_hz), 1e-6) / 440.0) + 0.5)
    return zlib.crc32(f"grain:{midi}:{int(copy)}".encode())


def region(L: int, st: Settings) -> tuple[int, int]:
    """The source samples the cloud lives in, [r0, r1): the span, or the
    loop inside it when the loop is on and at least MIN_LOOP long."""
    a = _scalar(st.start, 0.0, 0.0, 1.0)
    b = _scalar(st.end, 1.0, 0.0, 1.0)
    r0, r1 = int(a * L), int(b * L)
    if st.loop == "on":
        lo = int(_scalar(st.loop_start, 0.0, 0.0, 1.0) * L)
        hi = int(_scalar(st.loop_end, 1.0, 0.0, 1.0) * L)
        lo, hi = max(lo, r0), min(hi, r1)
        if hi - lo >= MIN_LOOP:
            return lo, hi
    return r0, r1


def window(g: int, shape: float) -> np.ndarray:
    """A grain's window over `g` samples: raised-cosine fades of
    0.5 - (0.5 - TAPER_MIN) x shape of the grain a side (a Hann window at
    0), flat between. Sampled at half-sample offsets, so it is symmetric
    and never exactly zero inside the grain."""
    taper = 0.5 - (0.5 - TAPER_MIN) * min(max(float(shape), 0.0), 1.0)
    m = max(1.0, taper * g)
    i = np.arange(g, dtype=np.float64) + 0.5
    rise = 0.5 - 0.5 * np.cos(np.pi * np.minimum(i, m) / m)
    return np.minimum(rise, rise[::-1])


def grain_length(size: float, sr: int = SR) -> int:
    return max(4, int(round(_scalar(size, 0.08, *SIZE) * sr)))


def effective_density(density: float, g: int, sr: int = SR) -> float:
    """Grains a second, held to MAX_SOUNDING overlapping."""
    return min(_scalar(density, 20.0, *DENSITY), MAX_SOUNDING * sr / g)


def onsets(n: int, density: float, sr: int = SR) -> np.ndarray:
    """Where each grain starts, in output samples: floor(k x sr / density)
    for every k that lands inside the note."""
    hop = sr / density
    count = int(math.ceil(n / hop)) + 1
    t = np.floor(np.arange(count) * hop).astype(np.int64)
    return t[t < n]


def cloud_gain(density: float, g: int, win: np.ndarray, sr: int = SR) -> float:
    """1 / sqrt(overlap x the window's mean square), never above 1: the
    level of grains of unrelated audio summed, held to unit power."""
    power = density * g / sr * float(np.mean(win * win))
    return 1.0 / math.sqrt(max(1.0, power))


def grain_draws(seed: int, count: int) -> np.ndarray:
    """(count, 3): grain k's three draws, `hash_u(seed, 3k + i)` — its
    place in the spray, its pitch in the spray, its direction ("both")."""
    return np.array(
        [hash_u(seed, 3 * k + i) for k in range(count) for i in range(3)],
        dtype=np.float64,
    ).reshape(count, 3)


def _read(x: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """`x` (L, 2) at fractional positions, linearly interpolated; zeros
    outside the recording."""
    L = len(x)
    valid = (pos >= 0) & (pos <= L - 1)
    i = np.clip(np.floor(pos), 0, L - 2).astype(np.int64)
    fr = np.clip(pos - i, 0.0, 1.0)[:, None]
    y = x[i] * (1.0 - fr) + x[i + 1] * fr
    y[~valid] = 0.0
    return y


def render(
    freq,
    n: int,
    sr: int = SR,
    settings: Settings | None = None,
    key_hz=None,
    copy: int = 0,
):
    """The granular oscillator: stereo (n, 2), or zeros (n,) when there is
    nothing to play. `copy` is the oscillator's seed in the stack (the
    unison voice; osc2's is 100 on): each copy its own cloud."""
    st = settings or Settings()
    x = sampler.get(st.sample)
    if x is None or len(x) < 4 or n <= 0:
        return np.zeros(max(n, 0))
    L = len(x)
    r0, r1 = region(L, st)
    if r1 - r0 < 4:
        return np.zeros(n)
    f = np.asarray(as_array(freq, n), dtype=np.float64)
    key = max(float(key_hz) if key_hz else float(f[0]), 1e-3)
    root_hz = sampler.midi_hz(sampler._first(st.root, 60.0))
    rel = f / root_hz if st.keytrack != "off" else f / key
    speed = rel * (SR / sr)  # source samples per output sample

    g = grain_length(st.size, sr)
    density = effective_density(st.density, g, sr)
    win = window(g, _scalar(st.shape, 0.0, 0.0, 1.0))
    gain = cloud_gain(density, g, win, sr)
    spray = _scalar(st.spray, 0.0, 0.0, 1.0)
    pspray = _scalar(st.pitch_spray, 0.0, 0.0, PITCH_SPRAY)
    scan = _scalar(st.scan, 0.0, -SCAN, SCAN)
    span = r1 - r0
    top = math.nextafter(1.0, 0.0)

    t = onsets(n, density, sr)
    seed = grain_seed(key, copy)
    u = grain_draws(seed, len(t))
    pos = np.asarray(st.pos, dtype=np.float64)
    if pos.ndim:
        at = np.clip(as_array(pos, n)[t], 0.0, top)
    else:
        at = np.full(
            len(t), min(max(float(pos) if np.isfinite(pos) else 0.0, 0.0), top)
        )
    c = at + scan * (t / sr) / (span / SR) + spray * (u[:, 0] - 0.5)
    first = r0 + np.mod(c, 1.0) * span  # where each grain's audio begins
    rate = speed[t]
    if pspray > 0:
        rate = rate * 2.0 ** (pspray * (2.0 * u[:, 1] - 1.0) / 12.0)
    if st.direction == "reverse":
        back = np.ones(len(t), dtype=bool)
    elif st.direction == "both":
        back = u[:, 2] < 0.5
    else:
        back = np.zeros(len(t), dtype=bool)

    xs = np.asarray(x, dtype=np.float64)
    fwd = np.arange(g, dtype=np.float64)
    rev = fwd[::-1].copy()
    wcol = win[:, None]
    out = np.zeros((n + g, 2))
    for j in range(len(t)):
        a = int(t[j])
        y = _read(xs, first[j] + rate[j] * (rev if back[j] else fwd))
        out[a : a + g] += y * wcol
    out = out[:n]
    if gain != 1.0:
        out *= gain
    return out
