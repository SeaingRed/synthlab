"""
synthlab.percussion — the hand and metal percussion the drum machine's
eight synthesized voices do not have: a cowbell, a shaker, a
conga, a tambourine and a metal clank.

Each is a pure, seeded function returning a stereo `(n, 2)` one-shot,
peak-normalised to -1 dBFS, with a 2 ms fade-in and a fade to silence at
its end — the same contract as `transitions`. The studio renders one into
the clip store the first time a drum row asks for it (a row's sample menu,
"Percussion"), and the row plays it like any clip (`drum_samples`).

    cowbell     two square partials (~545 and ~815 Hz, the classic drum
                machine's pair) through a band-pass, a fast attack and a
                short, slightly longer tail on the lower partial
    shaker      a burst of high-passed noise with a soft swell in (the
                shake's grains) and a quick fall
    conga       a sine tuned near 200 Hz with a small downward pitch blip
                and a slap of band-passed noise on the attack
    tambourine  the jingles: a few high, inharmonic partials over a
                band of bright noise, each with its own short decay
    clank       struck sheet metal: inharmonic FM partials, a noisy click
                and a short ringing comb — industrial, not musical
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, lfilter, sosfilt

from .core import SR

KINDS = ("cowbell", "shaker", "conga", "tambourine", "clank")
_PEAK = 10 ** (-1.0 / 20)  # -1 dBFS
_LEN = {"cowbell": 0.45, "shaker": 0.22, "conga": 0.5, "tambourine": 0.4, "clank": 0.7}


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(round(seconds * SR))) / SR


def _band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    sos = butter(2, [lo, hi], btype="bandpass", fs=SR, output="sos")
    return sosfilt(sos, x)


def _high(x: np.ndarray, f: float) -> np.ndarray:
    return sosfilt(butter(2, f, btype="highpass", fs=SR, output="sos"), x)


def _square(f: float, t: np.ndarray) -> np.ndarray:
    return np.sign(np.sin(2 * np.pi * f * t))


def _finish(x: np.ndarray) -> np.ndarray:
    """2 ms in, 10 ms out to silence, DC off, peak to -1 dBFS, stereo."""
    x = x - x.mean()
    n = len(x)
    head, tail = int(0.002 * SR), int(0.01 * SR)
    env = np.ones(n)
    env[:head] = np.linspace(0.0, 1.0, head)
    env[n - tail :] = np.linspace(1.0, 0.0, tail)
    x = x * env
    peak = float(np.max(np.abs(x))) or 1.0
    x = x * (_PEAK / peak)
    return np.stack([x, x], axis=1)


def cowbell(seed: int = 0) -> np.ndarray:
    t = _t(_LEN["cowbell"])
    low = _square(545.0, t) * np.exp(-t / 0.09)
    high = _square(815.0, t) * np.exp(-t / 0.06) * 0.7
    body = _band(low + high, 450.0, 2400.0)
    return _finish(body)


def shaker(seed: int = 0) -> np.ndarray:
    t = _t(_LEN["shaker"])
    rng = np.random.default_rng(seed + 11)
    noise = _high(rng.standard_normal(len(t)), 5000.0)
    swell = np.minimum(1.0, t / 0.03)  # the grains come in, then fall
    return _finish(noise * swell * np.exp(-np.maximum(0.0, t - 0.03) / 0.05))


def conga(seed: int = 0, pitch: float = 200.0) -> np.ndarray:
    t = _t(_LEN["conga"])
    f = pitch * (1.0 + 0.12 * np.exp(-t / 0.012))  # the head's little drop
    phase = 2 * np.pi * np.cumsum(f) / SR
    tone = np.sin(phase) * np.exp(-t / 0.16)
    rng = np.random.default_rng(seed + 23)
    slap = _band(rng.standard_normal(len(t)), 800.0, 4000.0) * np.exp(-t / 0.008)
    return _finish(tone + 0.35 * slap)


def tambourine(seed: int = 0) -> np.ndarray:
    t = _t(_LEN["tambourine"])
    rng = np.random.default_rng(seed + 37)
    jingles = sum(
        np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi)) * np.exp(-t / d)
        for f, d in ((5120.0, 0.12), (6840.0, 0.09), (8210.0, 0.07), (9630.0, 0.05))
    )
    hiss = _band(rng.standard_normal(len(t)), 6000.0, 12000.0) * np.exp(-t / 0.1)
    return _finish(0.6 * jingles + hiss)


def clank(seed: int = 0) -> np.ndarray:
    t = _t(_LEN["clank"])
    rng = np.random.default_rng(seed + 53)
    partials = sum(
        np.sin(2 * np.pi * f * t + 3.0 * np.sin(2 * np.pi * f * 1.41 * t))
        * np.exp(-t / d)
        for f, d in ((311.0, 0.35), (523.0, 0.25), (887.0, 0.18), (1453.0, 0.1))
    )
    click = _high(rng.standard_normal(len(t)), 2000.0) * np.exp(-t / 0.004)
    x = 0.5 * partials + click
    delay = int(SR / 412.0)  # a short ringing comb: the sheet's own ring
    a = np.zeros(delay + 1)
    a[0], a[delay] = 1.0, -0.55
    return _finish(lfilter([1.0], a, x))


GENERATORS = {
    "cowbell": cowbell,
    "shaker": shaker,
    "conga": conga,
    "tambourine": tambourine,
    "clank": clank,
}


def generate(kind: str, seed: int = 0) -> np.ndarray:
    if kind not in GENERATORS:
        raise ValueError(f"unknown percussion {kind!r}")
    return GENERATORS[kind](seed)
