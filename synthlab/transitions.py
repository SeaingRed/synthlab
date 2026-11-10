"""
synthlab.transitions — risers, impacts, sweeps, swells, sub drops and beds:
the ear-candy between sections.

Six generators, each a pure function of its arguments (seeded, so the same
settings always render the same samples) that returns a stereo `(n, 2)`
buffer exactly `seconds` long, peak-normalised (-1 dBFS; the bed -18):

    riser(seconds, direction="up", brightness=0.6, tail=0.3)
        noise through a resonant lowpass plus a detuned supersaw, both
        swept (filter and pitch) across the whole length. "up" builds to
        its loudest, brightest moment at the very end (place it so it ends
        on the downbeat); "down" is the downlifter: it starts there and
        falls away.
    impact(seconds, direction="down", brightness=0.6, tail=0.5)
        a sub drop (a sine gliding down from ~130 Hz), a filtered noise
        burst and a click, into a reverb tail. "down" hits at the start;
        "up" is the same hit reversed — a reverse swell that lands on the
        next downbeat.
    sweep(seconds, direction="up", brightness=0.6, tail=0.3)
        filtered noise alone: a resonant band (and the noise under it)
        climbing from 200 Hz to the top `brightness` sets — the uplifter;
        "down" is the same band falling, the downlifter. No pitch in it,
        so it sits over any key.
    swell(seconds, direction="up", brightness=0.6, tail=0.5)
        a cymbal-like noise burst into a long room, played backwards: it
        grows out of nothing and ends on its loudest moment (place it so
        it ends on the bar line). "down" is the burst the right way
        round, a wash that dies away. No sub and no click (an impact run
        "up" has both).
    subdrop(seconds, direction="down", brightness=0.6, tail=0.5)
        the sine fall alone, in mono: from 90-150 Hz (`brightness`) down
        to 32 Hz, ringing as long as `tail` says. "up" is the fall
        reversed, a sub that climbs into the bar line.
    bed(seconds, direction="down", brightness=0.6, tail=0.3)
        room tone and crackle for a breakdown: low-passed pink noise, a
        low rumble and seeded dust, quiet (-18 dBFS peak) and
        **loopable** — its end runs into its own start, so the same clip
        placed twice in a row has no seam. It has no direction (both
        render the same samples) and no fades.

`brightness` (0..1) sets how far the filters open, `tail` (0..1) how much
room there is (the reverb's decay and mix; the sub drop's ring). The
studio renders these into the clip store
(`studio/routes/song_record.py`, `POST /song/section/{id}/transition`), so
they are placed as takes on the Song page: what `leads_in` (a riser, a
sweep or a swell going up, a reversed impact or sub drop) ends on the
section's last bar line, everything else starts on its first.

These are clip bakes, known whole before they play, so the causal contract
does not bind them (a swell is a reversed render on purpose).
"""

from __future__ import annotations

import numpy as np

from . import effects as fx
from .core import SR, biquad, colour_noise, n_samples, supersaw

DIRECTIONS = ("up", "down")
_PEAK = 0.89  # -1 dBFS, like Song.render's default normalisation
_BED_PEAK = 10 ** (-18 / 20)  # a bed sits under the music: -18 dBFS
_SEED = {"riser": 1301, "impact": 1302, "sweep": 1303, "swell": 1304, "bed": 1306}


def _fit(x: np.ndarray, n: int) -> np.ndarray:
    x = fx.to_stereo(np.asarray(x, dtype=np.float64))
    if len(x) < n:
        x = np.vstack([x, np.zeros((n - len(x), 2))])
    return x[:n]


def _fade(x: np.ndarray, head: int, tail: int) -> np.ndarray:
    """Short raised-cosine edges so a clip never starts or ends on a step."""
    out = x.copy()

    def ramp(m: int) -> np.ndarray:
        return (0.5 - 0.5 * np.cos(np.pi * (np.arange(m) + 0.5) / m))[:, None]

    head, tail = min(head, len(x) // 2), min(tail, len(x) // 2)
    if head > 0:
        out[:head] *= ramp(head)
    if tail > 0:
        out[len(x) - tail :] *= ramp(tail)[::-1]
    return out


def _normalise(x: np.ndarray, to: float = _PEAK) -> np.ndarray:
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    return x * (to / peak) if peak > 1e-9 else x


def _clamp01(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, f)) if np.isfinite(f) else 0.0


def riser(
    seconds: float,
    direction: str = "up",
    brightness: float = 0.6,
    tail: float = 0.3,
    sr: int = SR,
) -> np.ndarray:
    """A noise + supersaw riser (or, `direction="down"`, a downlifter)."""
    n = n_samples(max(0.05, float(seconds)), sr)
    bright, room = _clamp01(brightness), _clamp01(tail)
    u = np.linspace(0.0, 1.0, n)
    s = u if direction != "down" else 1.0 - u  # 0 -> 1 = calm -> peak
    rng = np.random.default_rng(_SEED["riser"])
    top = 1500.0 + bright * 14000.0
    cutoff = 250.0 * (top / 250.0) ** (s**1.6)
    # noise: the whoosh
    wash = biquad(rng.standard_normal(n), "lowpass", cutoff, 1.8, sr)
    wash = biquad(wash, "highpass", 120.0 + 400.0 * s, 0.707, sr)
    # supersaw: the pitch that climbs two octaves (A2 -> A4)
    pitch = 110.0 * 2.0 ** (2.0 * s**1.4)
    saw = biquad(supersaw(pitch, n, sr, seed=3), "lowpass", cutoff * 0.8, 1.2, sr)
    left = 0.8 * wash + (0.35 + 0.25 * bright) * saw
    right = 0.8 * biquad(rng.standard_normal(n), "lowpass", cutoff, 1.8, sr)
    right = biquad(right, "highpass", 120.0 + 400.0 * s, 0.707, sr)
    right = right + (0.35 + 0.25 * bright) * saw
    level = 0.04 + 0.96 * s**1.8
    dry = np.column_stack([left * level, right * level])
    if room > 0:
        dry = fx.reverb(decay=0.6 + 3.0 * room, mix=0.12 + 0.45 * room, size=0.9)(dry)
    fade_in = n_samples(0.004 if direction == "down" else 0.02, sr)
    return _normalise(_fade(_fit(dry, n), fade_in, n_samples(0.006, sr)))


def impact(
    seconds: float,
    direction: str = "down",
    brightness: float = 0.6,
    tail: float = 0.5,
    sr: int = SR,
) -> np.ndarray:
    """Sub drop + noise burst + click into a reverb tail; `direction="up"`
    reverses it into a swell that lands on the hit."""
    n = n_samples(max(0.05, float(seconds)), sr)
    bright, room = _clamp01(brightness), _clamp01(tail)
    t = np.arange(n) / sr
    rng = np.random.default_rng(_SEED["impact"])
    # the sub: a sine gliding 130 -> 38 Hz, ringing longer with more room
    f = 38.0 + 92.0 * np.exp(-t / 0.09)
    phase = 2.0 * np.pi * np.cumsum(f) / sr
    sub = np.sin(phase) * np.exp(-t / (0.35 + 0.9 * room))
    # the burst: noise opened by brightness, gone in a tenth of a second
    top = 1200.0 + bright * 13000.0
    env = np.exp(-t / (0.03 + 0.08 * bright))
    burst = [biquad(rng.standard_normal(n), "lowpass", top, 0.9, sr) for _ in "lr"]
    click = np.zeros(n)
    click[: min(n, 64)] = np.hanning(128)[64 : 64 + min(n, 64)]
    hit = np.column_stack(burst) * env[:, None] * (0.35 + 0.3 * bright)
    hit += click[:, None] * 0.6
    if room > 0:
        wet = fx.reverb(decay=0.8 + 5.0 * room, mix=1.0, size=1.0)(hit)
        hit = hit + (0.3 + 0.9 * room) * _fit(wet, n)
    out = hit + 0.9 * sub[:, None]
    # a 0.5 ms head: the hit still lands at once, without a step from zero
    out = _fade(
        _fit(out, n), n_samples(0.0005, sr), n_samples(min(0.25, seconds / 4), sr)
    )
    if direction == "up":
        out = out[::-1].copy()
        out = _fade(out, n_samples(0.02, sr), n_samples(0.002, sr))
    return _normalise(out)


def sweep(
    seconds: float,
    direction: str = "up",
    brightness: float = 0.6,
    tail: float = 0.3,
    sr: int = SR,
) -> np.ndarray:
    """Filtered noise alone: a resonant band rising (the uplifter) or,
    `direction="down"`, falling (the downlifter)."""
    n = n_samples(max(0.05, float(seconds)), sr)
    bright, room = _clamp01(brightness), _clamp01(tail)
    u = np.linspace(0.0, 1.0, n)
    s = u if direction != "down" else 1.0 - u  # 0 -> 1 = calm -> peak
    rng = np.random.default_rng(_SEED["sweep"])
    top = 2500.0 + bright * 11500.0
    centre = 200.0 * (top / 200.0) ** (s**1.3)
    level = 0.05 + 0.95 * s**1.6
    sides = []
    for _ in "lr":  # two noises: the width is the sweep's own
        w = rng.standard_normal(n)
        band = biquad(w, "bandpass", centre, 5.0, sr)  # the whistle
        body = biquad(w, "lowpass", centre * 1.5, 0.9, sr)
        body = biquad(body, "highpass", 0.4 * centre, 0.707, sr)
        sides.append((band * 2.2 + 0.45 * body) * level)
    dry = np.column_stack(sides)
    if room > 0:
        dry = fx.reverb(decay=0.6 + 3.0 * room, mix=0.12 + 0.4 * room, size=0.9)(dry)
    fade_in = n_samples(0.004 if direction == "down" else 0.02, sr)
    return _normalise(_fade(_fit(dry, n), fade_in, n_samples(0.006, sr)))


def swell(
    seconds: float,
    direction: str = "up",
    brightness: float = 0.6,
    tail: float = 0.5,
    sr: int = SR,
) -> np.ndarray:
    """A cymbal-like burst into a long room, reversed: it grows out of
    nothing and ends on its loudest moment. `direction="down"` is the
    burst the right way round."""
    n = n_samples(max(0.05, float(seconds)), sr)
    bright, room = _clamp01(brightness), _clamp01(tail)
    t = np.arange(n) / sr
    rng = np.random.default_rng(_SEED["swell"])
    # the burst: band-limited noise and a few inharmonic partials (the
    # cymbal), short — the length of the swell is the room's
    env = np.exp(-t / max(0.02, min(0.09, (n / sr) / 8.0)))
    low, top = 500.0 + 1500.0 * bright, 3500.0 + bright * 11000.0
    ratios = (1.0, 1.483, 1.932, 2.546, 2.63, 3.358)
    ring = sum(np.sin(2 * np.pi * 540.0 * r * t + i) for i, r in enumerate(ratios))
    sides = []
    for _ in "lr":
        w = biquad(rng.standard_normal(n), "lowpass", top, 0.8, sr)
        w = biquad(w, "highpass", low, 0.707, sr)
        sides.append((w + 0.12 * bright * ring) * env)
    hit = np.column_stack(sides)
    # the room is the swell: with none, the burst alone is reversed. Its
    # decay follows the length, so an 8-bar swell grows for 8 bars (a
    # fixed 3 s room left the first half of a long one silent)
    if room > 0:
        decay = (n / sr) * (0.5 + 0.9 * room)
        wet = fx.reverb(decay=decay, mix=1.0, size=1.0)(hit)
        hit = hit + (0.4 + 2.2 * room) * _fit(wet, n)
    out = _fade(
        _fit(hit, n), n_samples(0.0005, sr), n_samples(min(0.3, n / sr / 3), sr)
    )
    if direction != "down":
        out = out[::-1].copy()
        out = _fade(out, n_samples(0.02, sr), n_samples(0.002, sr))
    return _normalise(out)


def subdrop(
    seconds: float,
    direction: str = "down",
    brightness: float = 0.6,
    tail: float = 0.5,
    sr: int = SR,
) -> np.ndarray:
    """The sine fall alone, in mono: `brightness` sets where it starts
    (90-150 Hz) and how much it is warmed, `tail` how long it rings.
    `direction="up"` reverses it into a sub that climbs to the bar line."""
    n = n_samples(max(0.05, float(seconds)), sr)
    bright, room = _clamp01(brightness), _clamp01(tail)
    t = np.arange(n) / sr
    length = n / sr
    start, floor = 90.0 + 60.0 * bright, 32.0
    # an exponential glide, near the floor two thirds of the way in
    f = floor + (start - floor) * np.exp(-t / (0.22 * length))
    phase = 2.0 * np.pi * np.cumsum(f) / sr
    amp = np.exp(-t / ((0.2 + 0.7 * room) * length))
    sub = np.tanh(np.sin(phase) * (1.0 + 1.5 * bright)) * amp
    out = _fade(
        _fit(sub, n), n_samples(0.003, sr), n_samples(min(0.25, length / 4), sr)
    )
    if direction == "up":
        out = out[::-1].copy()
        out = _fade(out, n_samples(0.02, sr), n_samples(0.004, sr))
    return _normalise(out)


def bed(
    seconds: float,
    direction: str = "down",
    brightness: float = 0.6,
    tail: float = 0.3,
    sr: int = SR,
) -> np.ndarray:
    """Room tone and crackle, -18 dBFS, loopable (its end runs into its
    start). `direction` is accepted and ignored: a bed has none."""
    n = n_samples(max(0.05, float(seconds)), sr)
    bright, room = _clamp01(brightness), _clamp01(tail)
    xf = max(1, min(n // 4, n_samples(0.5, sr)))  # the loop's crossfade
    m = n + xf
    rng = np.random.default_rng(_SEED["bed"])
    top = 700.0 + 5500.0 * bright
    sides = []
    for _ in "lr":
        w = rng.standard_normal(m)
        tone = biquad(colour_noise(w, "pink", sr), "lowpass", top, 0.707, sr)
        low = colour_noise(rng.standard_normal(m), "brown", sr)
        rumble = biquad(low, "lowpass", 180.0, 0.707, sr)
        # dust: sparse seeded ticks, each a filtered click
        ticks = np.zeros(m)
        count = int(m / sr * (2.0 + 10.0 * bright))
        at = rng.integers(0, m, size=count)
        ticks[at] = rng.uniform(0.3, 1.0, size=count) * rng.choice(
            [-1.0, 1.0], size=count
        )
        ticks = biquad(ticks, "bandpass", 2800.0, 1.2, sr)
        sides.append(tone + 0.5 * rumble + 6.0 * ticks)
    x = np.column_stack(sides)
    if room > 0:
        x = _fit(fx.reverb(decay=0.5 + 2.0 * room, mix=0.5 * room, size=0.7)(x), m)
    # the `xf` samples past the end are crossfaded onto the head (equal
    # power: two stretches of noise are uncorrelated), so the sample after
    # the last is the first
    a = np.sin(0.5 * np.pi * (np.arange(xf) + 0.5) / xf)[:, None]
    out = x[:n].copy()
    out[:xf] = out[:xf] * a + x[n:] * a[::-1]
    out -= out.mean(axis=0)  # no DC: a constant keeps the loop a loop
    return _normalise(out, _BED_PEAK)


GENERATORS = {
    "riser": riser,
    "impact": impact,
    "sweep": sweep,
    "swell": swell,
    "subdrop": subdrop,
    "bed": bed,
}
# the direction a kind takes when none is given: the way its name reads
DEFAULT_DIRECTION = {
    "riser": "up",
    "impact": "down",
    "sweep": "up",
    "swell": "up",
    "subdrop": "down",
    "bed": "down",
}
DIRECTIONLESS = frozenset({"bed"})  # both directions render the same samples
PEAK = {kind: (_BED_PEAK if kind == "bed" else _PEAK) for kind in GENERATORS}


def leads_in(kind: str, direction: str) -> bool:
    """Whether a transition leads *into* a downbeat (it ends on a bar line:
    a riser, a sweep or a swell going up, a reversed impact or sub drop)
    rather than coming *off* one. A bed does neither: it starts the section."""
    return direction == "up" and kind not in DIRECTIONLESS


def generate(
    kind: str,
    seconds: float,
    direction: str,
    brightness: float,
    tail: float,
    sr: int = SR,
) -> np.ndarray:
    """One transition by name (a `GENERATORS` key)."""
    if kind not in GENERATORS:
        raise ValueError(f"unknown transition: {kind!r}")
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction: {direction!r}")
    return GENERATORS[kind](seconds, direction, brightness, tail, sr=sr)
