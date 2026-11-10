"""Cleaning a chop before it is kept as a clip: gate, trim, fit to a
tempo, fades, level. Pure — audio and settings in, audio and what was done
out — and non-destructive: the source is never written, the settings live
on the chop (`labchops.py`) and run again each time it is played or kept.

    clean(audio, settings, plan) -> (audio, notes)

`audio` is (n, 2) float at `SR`. The steps run in this order, each skipped
at its "off" value:

| step  | settings                              | off     |
|-------|---------------------------------------|---------|
| gate  | `gate_db` -60..-20 (hold 80 ms, release 60 ms) | None |
| trim  | `trim_db` -60..-20                    | None    |
| fit   | `fit` off / session / song (`plan`)   | "off"   |
| fades | `fade_in`, `fade_out` 0..500 ms       | 5 ms each (the default) |
| level | `level` off / normalise / gain, `gain_db` | "off" |

The defaults are the fades alone, so a kept chop never clicks and is
otherwise the recording. The gate's and the trim's thresholds are measured
from the chop's own peak, so a quiet stem is not gated away whole.

Whole-buffer processing is fine here: this is a clip bake, not anything
`Synth.note` reaches (the engine's causal contract does not apply).
"""

from __future__ import annotations

import numpy as np

from .. import sampler
from ..core import SR
from . import limits

FITS = ("off", "session", "song")
LEVELS = ("off", "normalise", "gain")
DEFAULTS = {
    "gate_db": None,
    "trim_db": None,
    "fit": "off",
    "fade_in": 5.0,
    "fade_out": 5.0,
    "level": "off",
    "gain_db": 0.0,
}
GATE_HOLD, GATE_RELEASE, GATE_ATTACK = 0.08, 0.06, 0.005  # seconds
TRIM_KEEP = 0.005  # seconds kept either side of what the trim found
NORMALISE_DB = -1.0
BARS_WITHIN = 0.05  # a fitted chop this close to whole bars is held to them
SILENT = 1e-4  # a peak under this is silence (the stores' own floor)


def _number(v, lo: float, hi: float, default):
    """A setting as a float within lo..hi; `default` for anything that is
    not a finite number. Form values arrive as text."""
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            return default
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    if not -1e12 < v < 1e12:  # nan, inf, a 400-digit int
        return default
    return round(float(min(max(v, lo), hi)), 2) + 0.0


def clean_settings(raw) -> dict:
    """Settings held to type and range, every key present: a chop's `clean`
    in a meta anyone can edit, or a posted form (where "off" and "" are a
    threshold switched off)."""
    raw = raw if isinstance(raw, dict) else {}
    db, fade, gain = (
        limits.LAB_CLEAN_DB_RANGE,
        limits.LAB_FADE_MS_RANGE,
        limits.LAB_GAIN_DB_RANGE,
    )
    return {
        "gate_db": _number(raw.get("gate_db"), *db, None),
        "trim_db": _number(raw.get("trim_db"), *db, None),
        "fit": raw.get("fit") if raw.get("fit") in FITS else "off",
        "fade_in": _number(raw.get("fade_in"), *fade, DEFAULTS["fade_in"]),
        "fade_out": _number(raw.get("fade_out"), *fade, DEFAULTS["fade_out"]),
        "level": raw.get("level") if raw.get("level") in LEVELS else "off",
        "gain_db": _number(raw.get("gain_db"), *gain, 0.0),
    }


def is_default(settings: dict) -> bool:
    return clean_settings(settings) == DEFAULTS


def _peak(x: np.ndarray) -> float:
    return float(np.max(np.abs(x))) if x.size else 0.0


def _run_mean(mask: np.ndarray, n: int, ahead: bool) -> np.ndarray:
    """The mean of `mask` over the `n` samples behind each one (or ahead of
    it): a one-sided ramp of that length at every edge."""
    n = max(1, n)
    c = np.concatenate([[0.0], np.cumsum(mask, dtype=np.float64)])
    i = np.arange(len(mask))
    if ahead:
        hi = np.minimum(i + n, len(mask))
        return (c[hi] - c[i]) / n
    lo = np.maximum(i + 1 - n, 0)
    return (c[i + 1] - c[lo]) / n


def gate_gain(audio: np.ndarray, db: float, sr: int = SR) -> np.ndarray:
    """The gate's gain per sample, 0..1: open wherever the mono sum has
    reached `db` under the audio's own peak within the last `GATE_HOLD`
    seconds, opening over the `GATE_ATTACK` before that and closing over
    the `GATE_RELEASE` after."""
    mono = np.abs(np.asarray(audio, dtype=np.float64).mean(axis=1))
    peak = float(mono.max()) if len(mono) else 0.0
    if peak < SILENT:
        return np.ones(len(mono))
    loud = mono >= peak * 10.0 ** (db / 20.0)
    hold = max(1, int(GATE_HOLD * sr))
    held = _run_mean(loud, hold, ahead=False) > 0  # loud within the hold
    closing = _run_mean(held, int(GATE_RELEASE * sr), ahead=False)
    opening = _run_mean(held, int(GATE_ATTACK * sr), ahead=True)
    return np.minimum(1.0, np.maximum(closing, opening))


def trim_span(audio: np.ndarray, db: float, sr: int = SR) -> tuple[int, int]:
    """(first, last + 1) samples to keep: what is at or over `db` under the
    audio's own peak, with `TRIM_KEEP` either side. The whole of it when
    nothing is (silence is not trimmed to nothing here: the caller refuses
    a silent chop by name)."""
    level = np.max(np.abs(audio), axis=1) if len(audio) else np.zeros(0)
    peak = float(level.max()) if len(level) else 0.0
    if peak < SILENT:
        return 0, len(audio)
    over = np.flatnonzero(level >= peak * 10.0 ** (db / 20.0))
    keep = int(TRIM_KEEP * sr)
    return max(0, int(over[0]) - keep), min(len(audio), int(over[-1]) + 1 + keep)


def whole_bars(samples: int, bar: float, sr: int = SR) -> int:
    """The whole bars `samples` is, when it is within `BARS_WITHIN` of
    them (`bar` in seconds); 0 when it is not, or there is no bar."""
    if bar <= 0 or samples <= 0:
        return 0
    bars = samples / sr / bar
    n = round(bars)
    return n if n >= 1 and abs(bars - n) <= BARS_WITHIN * n else 0


def fade(audio: np.ndarray, fade_in: float, fade_out: float, sr: int = SR):
    """Raised-cosine ramps of `fade_in` / `fade_out` ms from and to zero
    (each at most half the audio)."""
    out = np.array(audio, dtype=np.float64)
    n = len(out)
    for ms, tail in ((fade_in, False), (fade_out, True)):
        k = min(int(round(ms / 1000.0 * sr)), n // 2)
        if k < 1:
            continue
        ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(k) / k)
        if tail:
            out[n - k :] *= ramp[::-1, None]
        else:
            out[:k] *= ramp[:, None]
    return out


def clean(audio: np.ndarray, settings: dict, plan: dict | None = None):
    """(the cleaned audio as float64 (n, 2), notes): `settings` as
    `clean_settings` holds them; `plan` is the fit — `{"factor", "source_bpm",
    "bpm", "bar"}` (the length factor, the two tempos and the target's bar
    in seconds) or `{"reason"}` when the chop cannot be fitted, None when
    nobody asked. `notes` says what each step did, in numbers."""
    s = clean_settings(settings)
    out = np.array(audio, dtype=np.float64)
    if out.ndim == 1:
        out = np.column_stack([out, out])
    notes: list[str] = []
    if s["gate_db"] is not None and len(out):
        gain = gate_gain(out, s["gate_db"])
        out *= gain[:, None]
        notes.append(f"gate closed {100.0 * float(np.mean(1.0 - gain)):.0f}% of it")
    if s["trim_db"] is not None and len(out):
        a, b = trim_span(out, s["trim_db"])
        cut = (len(out) - (b - a)) / SR
        out = out[a:b]
        notes.append(f"trimmed {cut:.2f} s")
    if s["fit"] != "off":
        if not plan or "factor" not in plan:
            why = (plan or {}).get("reason", "no tempo set")
            notes.append(f"not fitted ({why})")
        else:
            if abs(plan["factor"] - 1.0) > 1e-3 and len(out) >= 4:
                out = sampler.time_stretch(out, plan["factor"])
                notes.append(f"stretched {plan['source_bpm']:g} → {plan['bpm']:g} bpm")
            bars = whole_bars(len(out), plan["bar"])
            if bars:
                n = int(round(bars * plan["bar"] * SR))
                if n > len(out):
                    out = np.concatenate([out, np.zeros((n - len(out), 2))])
                out = out[:n]
                notes.append(f"held to {bars} bar{'s' if bars > 1 else ''}")
    out = fade(out, s["fade_in"], s["fade_out"])
    peak = _peak(out)
    if s["level"] == "normalise" and peak >= SILENT:
        out *= 10.0 ** (NORMALISE_DB / 20.0) / peak
        notes.append(f"normalised {20.0 * np.log10(1.0 / peak) + NORMALISE_DB:+.1f} dB")
    elif s["level"] == "gain" and s["gain_db"]:
        out *= 10.0 ** (s["gain_db"] / 20.0)
        said = f"gain {s['gain_db']:+g} dB"
        if _peak(out) > 1.0:
            out /= _peak(out)
            said += ", held under full scale"
        notes.append(said)
    return out, notes
