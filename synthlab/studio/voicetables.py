"""The engine's fixed tables, served once for the live voice
(`GET /wavetables.bin`, read by piano.js and handed to voice-worklet.js).

Everything here is the engine's own data, not a copy of it, so the live
voice cannot drift from `core.py`:

- `wt`: every wavetable's mip stack exactly as `core.wavetable` reads it —
  (tables, mips, frames, 2048) in `core.WAVETABLE_NAMES` / `_WT_MIPS` order.
  Rendering them in the browser would mean ~37M sines at startup.
- `uniform`: the first values of `np.random.default_rng(seed).uniform(-1,
  1)` for the seeds the engine draws them from — `core.pluck`'s burst
  (seed = unison copy, +100 for osc2) and `core.sample_hold`, the "noise"
  LFO (seed 0). Both read a prefix of that stream, so a prefix is exact:
  a live pluck or stepped LFO is the rendered one, value for value.
- `formant`: `core.formant_makeup_table` (vowel x tract size).
- the header also carries the pink/brown noise filters, the vowel
  formant table and the warp's decimating FIR (`core.WARP_TAPS`), so the
  worklet repeats none of those numbers.

Layout: a little-endian u32 header length, the JSON header (space-padded
to 4 bytes), then float32 arrays in the order the header's `arrays` lists.
"""

from __future__ import annotations

import hashlib
import json
from functools import cache

import numpy as np

from .. import core
from .paramui import MAX_UNISON

# the seeds whose uniform streams the engine reads: pluck's burst per unison
# copy (osc1 u, osc2 100 + u) — seed 0 doubles as sample_hold's stream
SEEDS = (*range(MAX_UNISON), *range(100, 100 + MAX_UNISON))
# a pluck's burst is floor(sr / 20 Hz) + 1 values: 2206 at 44.1 kHz, 2401 at
# 48; a stepped LFO reads one value per step (341 s at 12 Hz before it wraps)
UNIFORM = 4096


@cache
def payload() -> bytes:
    stack = np.stack([core._WT_STACK[name] for name in core.WAVETABLE_NAMES])
    uniform = np.stack(
        [np.random.default_rng(s).uniform(-1.0, 1.0, UNIFORM) for s in SEEDS]
    )
    makeup = core.formant_makeup_table()
    arrays = {"wt": stack, "uniform": uniform, "formant": makeup}
    header = {
        "sr": core.SR,
        "tables": list(core.WAVETABLE_NAMES),
        "mips": list(core._WT_MIPS),
        "mip_block": core._WT_MIP_BLOCK,
        "seeds": list(SEEDS),
        "formant_sizes": list(core.FORMANT_MAKEUP_SIZES),
        "formant_q": core._FORMANT_Q,
        "vowel_f": core._VOWEL_F.tolist(),
        "vowel_a": core._VOWEL_A.tolist(),
        "pink": {
            "poles": list(core.PINK_POLES),
            "taps": list(core.PINK_TAPS),
            "direct": core.PINK_DIRECT,
            "last": core.PINK_LAST,
            "gain": core.PINK_GAIN,
        },
        "brown": {"gain": core.BROWN_GAIN, "corner": core.BROWN_CORNER},
        # the warped oscillator's decimating FIR (core.WARP_TAPS),
        # float64 in JSON so the voice convolves the engine's own numbers
        "warp_taps": core.WARP_TAPS.tolist(),
        "warp_os": core.WARP_OS,
        "arrays": [[name, list(a.shape)] for name, a in arrays.items()],
    }
    head = json.dumps(header, separators=(",", ":")).encode()
    head += b" " * (-len(head) % 4)
    body = b"".join(
        np.ascontiguousarray(a, dtype="<f4").tobytes() for a in arrays.values()
    )
    return len(head).to_bytes(4, "little") + head + body


@cache
def version() -> str:
    """Content hash: `/patch.json` names the URL with it, so the file can be
    cached forever and still change when the engine does."""
    return hashlib.sha1(payload()).hexdigest()[:12]


def url() -> str:
    return f"/wavetables.bin?v={version()}"
