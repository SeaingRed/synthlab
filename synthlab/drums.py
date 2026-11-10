"""
synthlab.drums — a synthesised drum machine.

No samples needed: every drum is generated from oscillators and noise, the
way a TR-808 or TR-909 does it. That means every parameter is tweakable —
pitch the kick, lengthen the snare tail, tune the hats.
"""

from __future__ import annotations

import numpy as np

from . import effects as fx
from .core import (
    SR,
    biquad,
    n_samples,
    noise,
    perc_env,
    sine,
    soft_clip,
    time_axis,
    triangle,
)


def _norm(x: np.ndarray, target: float) -> np.ndarray:
    """Scale a hit to a predictable peak so the kit is balanced out of the box."""
    peak = float(np.max(np.abs(x))) or 1.0
    return x / peak * target


def kick(tune=52.0, decay=0.42, punch=0.9, click=0.35, drive=2.2, sr=SR) -> np.ndarray:
    """Pitch-swept sine — the 808/909 recipe. `punch` sets the pitch drop depth."""
    n = n_samples(decay + 0.1, sr)
    t = time_axis(n, sr)
    pitch_env = np.exp(-t * 55) * punch
    freq = tune * (1 + 6.0 * pitch_env)
    body = sine(freq, n, sr) * perc_env(n, decay, 3.5, sr)
    tick = noise(n, "white", seed=1) * np.exp(-t * 900) * click
    tick = biquad(tick, "highpass", 1200, 0.7, sr)
    out = _norm(soft_clip(body + tick, drive), 0.95)
    return fx.pan(out, 0.0)


def snare(tune=190.0, decay=0.22, tone=0.45, snap=0.75, sr=SR) -> np.ndarray:
    n = n_samples(decay + 0.15, sr)
    body = triangle(tune, n, sr) + triangle(tune * 1.47, n, sr) * 0.7
    body *= perc_env(n, decay * 0.55, 4.0, sr) * tone
    rattle = noise(n, "white", seed=2)
    rattle = biquad(rattle, "highpass", 380, 0.8, sr)
    rattle = biquad(rattle, "lowpass", 7800, 0.7, sr)
    rattle *= perc_env(n, decay, 3.0, sr) * snap
    out = _norm(soft_clip(body + rattle, 1.6), 0.8)
    return fx.pan(out, 0.0)


def hat(decay=0.055, tone=9000.0, open_hat=False, sr=SR) -> np.ndarray:
    d = decay * (5.5 if open_hat else 1.0)
    n = n_samples(d + 0.05, sr)
    x = noise(n, "white", seed=3)
    # metallic character: sum of inharmonic square-ish partials through a bandpass
    t = time_axis(n, sr)
    metal = np.zeros(n)
    for r in (1.0, 1.34, 1.79, 2.41, 3.11, 4.09):
        metal += np.sign(np.sin(2 * np.pi * tune_ratio(tone, r) * t))
    x = x * 0.6 + metal / 6 * 0.4
    x = biquad(x, "highpass", tone * 0.75, 0.9, sr)
    x = biquad(x, "bandpass", tone, 0.8, sr)
    x *= perc_env(n, d, 5.0, sr)
    return fx.pan(_norm(x, 0.55), 0.15)


def tune_ratio(base, r):
    return base * 0.09 * r


def clap(decay=0.28, spread=0.012, sr=SR) -> np.ndarray:
    n = n_samples(decay + 0.1, sr)
    t = time_axis(n, sr)
    x = np.zeros(n)
    for k, g in enumerate([1.0, 0.85, 0.7, 0.6]):  # the classic 4-burst clap
        d = n_samples(spread * k, sr)
        burst = noise(n, "white", seed=10 + k) * np.exp(-t * 220) * g
        x[d:] += burst[: n - d]
    tail = noise(n, "white", seed=20) * perc_env(n, decay, 3.0, sr) * 0.45
    x = biquad(x + tail, "bandpass", 1400, 0.7, sr)
    x = biquad(x, "highpass", 600, 0.7, sr)
    return fx.pan(_norm(x, 0.8), -0.1)


def tom(tune=140.0, decay=0.4, sr=SR) -> np.ndarray:
    n = n_samples(decay + 0.1, sr)
    t = time_axis(n, sr)
    freq = tune * (1 + 0.9 * np.exp(-t * 28))
    x = sine(freq, n, sr) * perc_env(n, decay, 3.2, sr)
    x += noise(n, "white", seed=4) * np.exp(-t * 120) * 0.15
    return fx.pan(_norm(soft_clip(x, 1.4), 0.8), 0.0)


def rim(tune=420.0, decay=0.05, sr=SR) -> np.ndarray:
    n = n_samples(decay + 0.03, sr)
    t = time_axis(n, sr)
    x = (np.sin(2 * np.pi * tune * t) + np.sin(2 * np.pi * tune * 1.6 * t)) * 0.5
    x *= np.exp(-t * 90)
    x = biquad(x, "highpass", 300, 0.8, sr)
    return fx.pan(_norm(x, 0.5), 0.25)


def crash(decay=1.8, sr=SR) -> np.ndarray:
    n = n_samples(decay + 0.2, sr)
    x = noise(n, "white", seed=5)
    x = biquad(x, "highpass", 4500, 0.7, sr)
    x *= perc_env(n, decay, 2.2, sr)
    return fx.pan(_norm(x, 0.6), 0.0)


KIT = {
    "kick": kick,
    "snare": snare,
    "hat": hat,
    "ohat": lambda **kw: hat(open_hat=True, **kw),
    "clap": clap,
    "tom": tom,
    "rim": rim,
    "crash": crash,
}


def hit(name: str, **kw) -> np.ndarray:
    """drums.hit('kick', tune=45, decay=0.6)"""
    return KIT[name](**kw)
