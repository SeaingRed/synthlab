"""Where a held WAV-fallback key loops, decided (and baked) server-side.

Hold mode sustains a key the AudioWorklet can't play by looping the sustain
of its `/note/{pitch}.wav?hold=1` render. The loop points and the seam
crossfade used to be computed in the browser from the patch fields, which
left the one test of seam quality mirroring JS math in Python. Now the
route bakes the crossfade into the WAV and sends the points in an `X-Loop`
header; `keys.js` only sets `loopStart`/`loopEnd` on the source.

Pure NumPy in/out, no FastAPI types.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from ..core import SR
from ..synth import Patch

# 250 ms, linear: the two sides are a whole number of periods apart, so they
# are correlated — an equal-power fade measured ~2.5 dB loud across the seam.
LOOP_XFADE = 0.25


class HoldLoop(NamedTuple):
    start: int  # loop start, samples
    end: int  # loop end, samples (exclusive): end - start = whole periods
    gate: int  # where the render's own release begins, samples

    def header(self, sr: int = SR) -> str:
        """`X-Loop`, in seconds: the browser decodes at its own rate."""
        return ",".join(f"{v / sr:.6f}" for v in self)


def loop_points(
    patch: Patch, n: int, freq: float, sr: int = SR, settle: float = 0.0
) -> HoldLoop:
    """The sustain span to loop in an `n`-sample render of `patch` at
    `freq`: after attack + decay (+ `settle` s, for a render whose FX are
    still building up — a pre-rendered lane's reverb or delay), ending just
    before the gate, a whole number of fundamental periods long."""
    dur, per = n / sr, 1.0 / freq
    release = float(np.max(patch.release))
    a_d = float(np.max(patch.attack)) + float(np.max(patch.decay))
    s = min(a_d + 0.02 + settle, dur / 2)
    e = max(s + 2 * per, dur - release - 0.02)
    e = s + np.floor((e - s) / per) * per
    gate = max(0, min(n, round((dur - release) * sr)))
    start = round(s * sr)
    # inside the render, always: a fundamental whose two periods outlast it
    # (`/note/C-9.wav?hold=1`, ~0.06 Hz) asked for a loop past the buffer's
    # end and bake_loop indexed out of range. Such a loop is no longer
    # whole periods — there are none to be had — just the rest of the note.
    end = min(n, round(e * sr))
    if end <= start:
        end = n
    return HoldLoop(start, end, gate)


def bake_loop(audio: np.ndarray, loop: HoldLoop, xfade: float = LOOP_XFADE,
              sr: int = SR) -> np.ndarray:  # fmt: skip
    """Crossfade the material just before the loop start into the loop's
    tail, so the sample after `end - 1` (which is `start`) continues exactly
    what `end - 1` was heading into. Returns a copy."""
    a, b = loop.start, loop.end
    n = min(round(xfade * sr), a, b - a)
    out = np.array(audio, dtype=np.float64, copy=True)
    if n < 8:
        return out
    w = np.arange(n) / n
    if out.ndim == 2:
        w = w[:, None]
    out[b - n : b] = audio[b - n : b] * (1 - w) + audio[a - n : a] * w
    return out
