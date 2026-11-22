"""The arp pattern: a step pattern both arps play (studio
docs/loop-and-perform.md, "The arp pattern").

The loop's grid arp (`Arrangement.arp_mode` "pattern", rendered by
`arrange._lane_timeline`) and the keyboard dock's keys arp (static/arp.js
in its "pattern" mode, through static/arppat.js) walk the same pattern
with the same rules, so what the keys play over a held chord is what the
grid renders over the same notes. Pure: no state, no I/O.

A pattern (`Arrangement.arp_pattern`, ABSENT while unset —
`schema.ARR_OPTIONAL`: no saved state, loop file or render key gains it):

    {"steps": [{"on", "vel", "tr", "gate", "chance", "rep"}, ...],   # 1-16
     "rate": "grid" | "1/4" | ... | "1/32",
     "mode": "up" | "down" | "updown" | "played" | "random",
     "oct": 1-4,
     "retrig": "note" | "bar" | "free"}

- `on`: off is a rest (it takes no note of the run).
- `vel`: a scale on the note's own velocity (the grid's velocity lane at
  the step the note falls on; the keys' struck velocity).
- `tr`: SCALE STEPS of the loop's key (`scale_shift`): +2 in A minor
  takes A to C. A note off the scale keeps its distance above the tone
  under it.
- `gate`: how long the note sounds, of its step (of a repeat's share).
- `chance`: the odds it plays, rolled on a hash of (the step, the
  pattern's pass) — `roll`, never a random stream: a re-render, a section's
  chunk and the MIDI file roll the same.
- `rep`: repeats (a ratchet): the step's time split into that many hits.

The walk (`Run`, `step`): each step plays the next note of the run when it
is on (`on_steps`: a rest takes none); the run is the held notes over
`oct` octaves in `mode`'s order (`cycle`; "random" a shuffle a cycle,
seeded, as the keys arp's always was — `pick`). `retrig` says when the
walk starts again: "note" when the held chord is struck (on the grid: at
the start of every pass of the loop's pattern — a grid arp's chord is
every row with a note, held the whole loop), "bar" at every bar line,
"free" never. The pass count (`Run.c`) never restarts: it counts every
time the pattern starts, so the chance of a step rolls afresh each time.
"""

from __future__ import annotations

import json
import math
import zlib

from ..core import hash_u
from ..sequencer import SCALES, midi
from . import meter as _meter

STEPS_MAX = 16
SLOTS = 12
# beats a step ("grid": the note grid's own step)
RATES: dict[str, float | None] = {
    "grid": None,
    "1/4": 1.0,
    "1/8": 0.5,
    "1/8T": 1.0 / 3.0,
    "1/16": 0.25,
    "1/16T": 1.0 / 6.0,
    "1/32": 0.125,
}
MODES = ("up", "down", "updown", "played", "random")
RETRIGS = ("note", "bar", "free")
# a take recorded as "the keys I held" (keyrec.js): the pattern plays over
# the keys as they were held (`arrange._held_timeline`) — "pattern" the
# keys arp's ▦ walk, "plain" its up / down / … modes — absent otherwise;
# `swing` (0..SWING_MAX) the keys arp's, with it
HELD = ("pattern", "plain")
SWING_MAX = 0.6
OCT_RANGE = (1, 4)
TR_RANGE = (-12, 12)
GATE_RANGE = (0.05, 1.0)
REP_RANGE = (1, 4)
# a new step: on, at the note's own velocity, one hit 0.9 of its step —
# the grid arp's gate (`arrange.NOTE_GATE`), so the default pattern on
# the grid is the "up" arp, note for note
STEP_DEFAULT = {"on": True, "vel": 1.0, "tr": 0, "gate": 0.9, "chance": 1.0, "rep": 1}
NEW_STEPS = 8
ROLL_SEED = 2757835283  # zlib.crc32(b"arp-pattern"): the rolls' own stream
GLYPHS = {"up": "↑", "down": "↓", "updown": "↕", "played": "♪", "random": "⚄"}

assert zlib.crc32(b"arp-pattern") == ROLL_SEED


def default() -> dict:
    """The pattern a lane plays in "pattern" mode before one is written."""
    return {
        "steps": [dict(STEP_DEFAULT) for _ in range(NEW_STEPS)],
        "rate": "grid",
        "mode": "up",
        "oct": 1,
        "retrig": "note",
    }


def of(arr) -> dict:
    """The lane's pattern, or the default while it has none."""
    return getattr(arr, "arp_pattern", None) or default()


# --- the walk: both arps' one rule --------------------------------------------


def cycle(held: list[int], mode: str, octaves: int) -> list[int]:
    """One cycle of the run: the held notes low to high ("played": in the
    order they came) over `octaves` octaves, shaped by `mode` (arp.js
    `cycle`, the keys arp's own)."""
    base = list(held) if mode == "played" else sorted(set(held))
    run = [m + 12 * o for o in range(octaves) for m in base]
    if mode == "down":
        return run[::-1]
    if mode == "updown" and len(run) > 2:
        return run + run[-2:0:-1]
    return run


def _mulberry(seed: int):
    """arp.js's seeded stream (mulberry32), bit for bit: 32-bit unsigned."""
    s = seed & 0xFFFFFFFF

    def rnd() -> float:
        nonlocal s
        s = (s + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((s ^ (s >> 15)) * (1 | s)) & 0xFFFFFFFF
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    return rnd


def shuffle(items: list, seed: int) -> list:
    """arp.js `shuffle`: Fisher-Yates over the seeded stream."""
    rnd = _mulberry(seed)
    out = list(items)
    for i in range(len(out) - 1, 0, -1):
        j = math.floor(rnd() * (i + 1))
        out[i], out[j] = out[j], out[i]
    return out


def pick(held: list[int], mode: str, octaves: int, n: int) -> int | None:
    """Note `n` of the run (arp.js `pick`): "random" each note once a
    cycle, a shuffle seeded by the cycle's number."""
    c = cycle(held, "up" if mode == "random" else mode, octaves)
    if not c:
        return None
    i = n % len(c)
    if mode == "random":
        return shuffle(c, 1 + n // len(c))[i]
    return c[i]


def key_of(root: str, scale_name: str) -> tuple[int, tuple[int, ...]]:
    """(the root's pitch class, the scale's intervals): what `tr` counts in."""
    return midi(root) % 12, tuple(SCALES.get(scale_name) or SCALES["minor"])


def scale_shift(m: int, tr: int, key: tuple[int, tuple[int, ...]]) -> int:
    """`m` moved `tr` tones of the key. A note off the scale keeps its
    distance above the scale tone under it."""
    if not tr:
        return m
    root, iv = key
    rel = m - root
    octave, pc = rel // 12, rel % 12
    d = max(i for i, x in enumerate(iv) if x <= pc)
    off = pc - iv[d]
    o2, d2 = divmod(octave * len(iv) + d + int(tr), len(iv))
    return root + 12 * o2 + iv[d2] + off


def roll(i: int, c: int) -> float:
    """Step `i`'s chance roll on the pattern's `c`-th pass: 0 <= x < 1, a
    32-bit hash (`core.hash_u`, which JS's Math.imul repeats bit for bit)."""
    return hash_u((ROLL_SEED + int(i)) & 0xFFFFFFFF, int(c))


class Run:
    """Where an arp is in its pattern: `k` steps and `ni` notes since it
    last started again, `c` the passes of the pattern so far (-1 before the
    first). arppat.js keeps the same three."""

    __slots__ = ("c", "k", "ni")

    def __init__(self) -> None:
        self.k, self.ni, self.c = 0, 0, -1

    def restart(self) -> None:
        self.k, self.ni = 0, 0


def step(run: Run, held: list[int], pat: dict, key) -> list[tuple]:
    """The next step of `pat` over `held`: its hits as (offset, a share of
    the step; midi; velocity scale; gate, a share of the step) — none for
    a rest, a step its chance rests or a note past MIDI's range."""
    steps = pat["steps"]
    i = run.k % len(steps)
    if i == 0:
        run.c += 1
    run.k += 1
    st = steps[i]
    if not st["on"] or not held:
        return []
    m = pick(held, pat["mode"], int(pat["oct"]), run.ni)
    run.ni += 1
    if m is None or roll(i, run.c) >= float(st["chance"]):
        return []
    m = scale_shift(m, int(st["tr"]), key)
    if not 0 <= m <= 127:
        return []
    r = int(st["rep"])
    return [(h / r, m, float(st["vel"]), float(st["gate"]) / r) for h in range(r)]


def named_rate(beats: float) -> str | None:
    """The rate named for a step of `beats` ("1/16" for 0.25), or None."""
    for name, b in RATES.items():
        if b is not None and abs(b - float(beats)) < 1e-9:
            return name
    return None


def from_keys(arp: dict) -> dict | None:
    """The grid arp's pattern that plays a take's held keys as the keys arp
    played them (static/arp.js, keyrec.js's `arp`: its mode, rate, octaves,
    gate, swing and — in its ▦ mode — the pattern and the note grid's step
    in beats it read "grid" as). ▦: that pattern, its "grid" rate named
    (the loop may be placed on another step), `held` "pattern"; up / down /
    up-down / random / as played: one step at its gate on its rate, in its
    mode and octaves (the walk the keys arp's `pick` makes, a note a step),
    `held` "plain". Its swing with it. None for what the grid arp cannot
    play (chord stabs) or nothing that reads as the keys arp's settings —
    the take then keeps the arp's notes. Unconformed: the schema cleans
    it (`schema` `arp_pattern_field`)."""
    if not isinstance(arp, dict):
        return None
    try:
        swing = min(SWING_MAX, max(0.0, float(arp.get("swing", 0.0))))
    except (TypeError, ValueError):
        swing = 0.0
    swing = swing if math.isfinite(swing) else 0.0
    mode = arp.get("mode")
    if mode == "pattern":
        pat = arp.get("pattern")
        if not isinstance(pat, dict) or not isinstance(pat.get("steps"), list):
            return None
        out = dict(pat)
        if out.get("rate") == "grid":
            try:
                out["rate"] = named_rate(float(arp.get("grid"))) or "grid"
            except (TypeError, ValueError):
                pass
        return out | {"held": "pattern", "swing": swing}
    if mode not in MODES or arp.get("rate") not in RATES or arp["rate"] == "grid":
        return None
    try:
        gate = float(arp.get("gate", 0.5))
        octaves = int(arp.get("oct", 1))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(gate):
        return None
    step = dict(STEP_DEFAULT, gate=min(GATE_RANGE[1], max(GATE_RANGE[0], gate)))
    return {
        "steps": [step],
        "rate": arp["rate"],
        "mode": mode,
        "oct": min(OCT_RANGE[1], max(OCT_RANGE[0], octaves)),
        "retrig": "note",
        "held": "plain",
        "swing": swing,
    }


def rate_beats(pat: dict, step_beats: float) -> float:
    """A step of the pattern in beats ("grid": the note grid's step)."""
    beats = RATES.get(pat["rate"])
    return step_beats if beats is None else beats


def steps_in(bar_beats: float, rate: float) -> int:
    """The pattern's steps a bar holds: every one that starts inside it."""
    return max(1, math.ceil(bar_beats / rate - 1e-9))


def rests_by_chance(mode, pat) -> bool:
    """Does a lane's grid arp play a pattern a step of which may rest by
    chance (so the working loop renders several passes:
    `song_model.loop_passes`)?"""
    return mode == "pattern" and any(
        float(st.get("chance", 1.0)) < 1.0 for st in (pat or {}).get("steps") or ()
    )


def summary(pat: dict) -> str:
    """What the pattern is, in a few characters: "8 ↑ grid"."""
    return f"{len(pat['steps'])} {GLYPHS.get(pat['mode'], '')} {pat['rate']}"


def context(arr, slots) -> dict:
    """The editor's template context (fragments/arp_pattern.html, region
    `arp-pattern`): the edited lane's pattern (the default while it has
    none: `stored` False), what arppat.js reads off it (the pattern, the key
    as [root pitch class, intervals], the grid's step in beats), the 12
    slots, and whether the grid arp plays it."""
    pat = of(arr)
    root, iv = key_of(arr.root, arr.scale_name)
    slots = list(slots or [None] * SLOTS)
    return {
        "pattern": pat,
        "stored": bool(getattr(arr, "arp_pattern", None)),
        "json": json.dumps(pat, separators=(",", ":")),
        "key": json.dumps([root, list(iv)]),
        "grid_beats": _meter.bar_beats(arr.meter) / arr.steps_per_bar,
        "slots": [
            (i, p is not None, summary(p) if p else "") for i, p in enumerate(slots)
        ],
        "summary": summary(pat),
        "grid_on": arr.arp_mode == "pattern",
        "rates": list(RATES),
        "modes": [(m, GLYPHS[m]) for m in MODES],
        "retrigs": RETRIGS,
        "scale": f"{arr.root[:-1]} {arr.scale_name.replace('_', ' ')}",
    }


def watch(arr, slots) -> tuple:
    """What the editor draws (its region's watch)."""
    return (
        json.dumps(of(arr), sort_keys=True),
        bool(getattr(arr, "arp_pattern", None)),
        arr.root,
        arr.scale_name,
        _meter.bar_beats(arr.meter) / arr.steps_per_bar,  # its "grid" rate
        arr.arp_mode == "pattern",
        json.dumps(slots, sort_keys=True),
    )
