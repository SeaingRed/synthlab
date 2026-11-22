"""A loop at the keys' level: the playback gain that puts a render where
the key bus put the same notes while they were played.

A loop render is normalised (`render._normalize`: the body of the mix to
0.89, `RenderResult.norm_gain_db` the gain it applied); the keys are not —
keys.js's key bus plays every voice at the engine's own level times
`KEY_HEADROOM` over the voices held to the `POLY` (a held chord of three:
0.45 / 3 ** 0.25), into a -4 dB limiter whose makeup gain lifts it all
2.28 dB (`KEY_MAKEUP`). So a take's loop played ~9 dB
louder than its take. A render's notes are the engine's level times the
normalisation's gain; undoing that and applying the key bus's gives the
keys' level, and no render key moves (`RENDER_REV` did not).

The key bus's gain moves with the keys held (`keys.js` `setPoly`: a
press counts until its key is up, a release tail does not, so a chord's
tail rings at one voice's gain); one gain for a whole loop is its power
average — sqrt(sum(n * g(k)^2) / sum(n)), each instant weighted by the n
voices sounding, at the gain g of the k keys held then — which is 0.45
(with the makeup, 0.585) for a line of single notes, that over 3 ** 0.25
for a held three-note chord, and between them for the rest.
A following layer doubles its main lane's presses (one key, one count),
so only the main lane and the layers with notes of their own count. The
bus's limiter is not in it: it catches peaks (a chord's attack), not the
body (`tests/browser/test_take_level.py` measures what reaches the master).

Loudness-placed renders keep their own levels: a song section, the song's
export (its loudness targets), the drum machine, Synths."""

from __future__ import annotations

import math

from . import arrange
from .render import RenderInputs, RenderResult

KEY_HEADROOM = 0.45  # keys.js: the key bus's gain for one voice
POLY = 0.25  # keys.js: the bus scales by voices ** -POLY
# keys.js's limiter (a DynamicsCompressorNode: threshold -4 dB, ratio 20,
# knee 0) adds a makeup gain to all it passes: Blink, WebKit and Gecko
# share the code, which raises the output by the full-range gain's inverse
# to the 0.6 — 0.6 x 4 x (1 - 1 / 20) = 2.28 dB (measured: a take of single
# notes 2.4 dB over 0.45 x the engine)
LIMIT_DB, LIMIT_RATIO, MAKEUP_EXP = -4.0, 20.0, 0.6
KEY_MAKEUP = 10 ** (-MAKEUP_EXP * LIMIT_DB * (1 - 1 / LIMIT_RATIO) / 20)


def held_spans(inp: RenderInputs) -> list[tuple[float, float, float]]:
    """The notes `inp`'s synth lanes play, as (start, key up, sound's end)
    in beats — the main lane and each layer with notes of its own (a
    follower is its main lane's presses again); none when the synth is not
    among its parts. The sound rings past the key up by its release, taken
    as a third of the patch's `release` at the note's level (a linear fall's
    energy: the ear's tail without the envelope's curve)."""
    if "synth" not in inp.parts:
        return []
    layers = [lay for lay in inp.layers if arrange.follow_octave(lay) is None]
    lanes = arrange.note_schedule(inp.arr, layers, inp.main_muted)
    gate, tail = arrange.NOTE_GATE, inp.patch.release / 3 * inp.arr.bpm / 60
    return [
        (n[0], n[0] + n[1] * gate, n[0] + n[1] * gate + tail)
        for lane in lanes
        for n in lane
        if n[1] > 0
    ]


def keys_gain(spans: list[tuple[float, float, float]]) -> float | None:
    """The key bus's gain for notes (start, key up, end) — power-averaged
    over the voices sounding, each at the gain the keys held then give
    (module docstring) — or None for no notes."""
    steps = sorted(
        [(s, 1, 1) for s, u, e in spans if e > s]
        + [(u, -1, 0) for s, u, e in spans if e > s]
        + [(e, 0, -1) for s, u, e in spans if e > s]
    )
    num = den = 0.0
    held = sounding = 0
    at = 0.0
    for (
        t,
        dh,
        ds,
    ) in steps:  # a sweep: from `at` to `t`, `held` keys down, `sounding` voices
        if sounding:
            g = KEY_HEADROOM * KEY_MAKEUP / max(1, held) ** POLY
            num += (t - at) * sounding * g * g
            den += (t - at) * sounding
        held, sounding, at = held + dh, sounding + ds, t
    return math.sqrt(num / den) if den > 0 else None


def playback_db(inp: RenderInputs, result: RenderResult) -> float | None:
    """The gain, in dB, that plays `result` (`inp`'s render) at the keys'
    level: the key bus's gain for its notes less what the render's
    normalisation applied. None: no notes, or a render that reports no
    normalisation (a post-processed or section render)."""
    gain = keys_gain(held_spans(inp))
    norm = getattr(result, "norm_gain_db", None)
    if gain is None or norm is None:
        return None
    return round(20 * math.log10(gain) - norm, 2)
