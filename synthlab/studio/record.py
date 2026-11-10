"""Note recording's pure half: quantize a take's note-ons onto a
notes pattern's grid, and merge them into the pattern.

The browser only timestamps: each event is a MIDI pitch, the seconds since
the recorded section's start (heard time, output latency already taken
off) and a velocity. Everything grid-shaped happens here, once, so there
is one quantizer — `tests/fixtures/record_vectors.json` pins it.

- **Grid.** `1/8`, `1/16` (the default), `1/32` or `off`. "off" snaps to
  the finest grid a pattern supports, 32 steps per bar. A note snaps to
  the quantize grid first, then onto the pattern's own steps, so a grid
  finer than the pattern lands on the pattern's nearest step. No swing:
  groove is song-wide and applied at render time.
- **Wrap.** Times fold onto the pattern (`step % bars * steps_per_bar`): a
  note on any pass of a section, or one that snaps onto the loop end,
  lands where it sounds in the loop. Note-offs are not needed: a notes
  cell has no length (the patch's envelope gives it one), so an off that
  arrives before its on (across the wrap) changes nothing.
- **A trimmed block** (`block`, the cell's span in bars): the pattern
  starts at the block's first bar, so a note's time is taken from there
  before it folds onto the pattern, and a note played outside the block is
  dropped — the cell is silent there, so it would never be heard where it
  was played.
- **Overdub** adds notes; an identical (pitch, step) is replaced (its
  velocity rewritten). **Replace** first clears every cell whose step the
  take window covered — called for a take's first post only.
- **Drum hits** (the drums track is armed and played from the
  keys, MIDI pads or the on-screen pads) go through the same `step_of`
  onto a drums pattern's grid (`steps` a bar) as (voice, step, velocity):
  `quantize_hits` / `merge_hits`. A hit's velocity is the hit's own
  (`hits["<voice>.<step>"]["v"]`, the per-hit model), never the step's
  `drum_vel`, which every voice on that step shares.
"""

from __future__ import annotations

import math

from ..sequencer import name_of

GRIDS = {"1/8": 8, "1/16": 16, "1/32": 32, "off": 32}
MODES = ("overdub", "replace")


def _round(x: float) -> int:
    return math.floor(x + 0.5)  # half up, never banker's rounding


def bar_seconds(bpm: float) -> float:
    return 240.0 / bpm  # 4/4: four beats a bar, as the renderer counts


def step_of(
    t: float,
    *,
    bpm: float,
    bars: int,
    steps_per_bar: int,
    grid: str,
    cycle_bars: int | None = None,
    block: tuple[int, int] | None = None,
) -> int | None:
    """The pattern step a note-on at `t` seconds lands on. One rounding:
    a grid at least as fine as the pattern snaps straight to the pattern's
    nearest step (snapping to the grid first and then again to the pattern
    rounded twice and could move a note a step); a coarser grid snaps to
    itself, which is always a whole number of pattern steps. `t` folds by
    the section's cycle (`song_model.cycle_bars`) before the pattern: the
    pattern restarts at every cycle, so a 3-bar pattern in a 4-bar section
    plays its bar 1 again in bar 4 and again at bar 5 (the next pass).

    `block` = (at, len), bars of the cycle (a trimmed block, whose section
    is one cycle): the pattern starts at bar `at`, so the step is counted
    from there, and a note that lands outside [at, at + len) is None — the
    block is silent there."""
    bar = bar_seconds(bpm)
    q = GRIDS.get(grid, 16)
    if cycle_bars:
        t %= cycle_bars * bar
    if q >= steps_per_bar:
        step = _round(t * steps_per_bar / bar)
    else:
        step = _round(t * q / bar) * (steps_per_bar // q)
    if cycle_bars:
        # a note snapping onto the cycle's end is the next cycle's first;
        # inside a cycle the pattern loops from its start (song_model._tile)
        step %= cycle_bars * steps_per_bar
    if block:
        lo, hi = block[0] * steps_per_bar, (block[0] + block[1]) * steps_per_bar
        if not lo <= step < hi:
            return None
        step -= lo
    return step % (bars * steps_per_bar)


def quantize(
    events,
    *,
    bpm: float,
    bars: int,
    steps_per_bar: int,
    grid: str = "1/16",
    cycle_bars: int | None = None,
    block: tuple[int, int] | None = None,
    missed: list | None = None,
) -> list[tuple[str, int, float]]:
    """[(pitch name, step, velocity)] for a take's note-ons, in play order;
    a later note on the same (pitch, step) wins. `cycle_bars`: the
    section's cycle, which `t` folds by first (None: the pattern's own
    length is the cycle). `block`: a trimmed block's (at, len) — a note
    outside it is left out, and appended to `missed` when that is given."""
    out: dict[tuple[str, int], float] = {}
    for e in events:
        step = step_of(
            e["t"],
            bpm=bpm,
            bars=bars,
            steps_per_bar=steps_per_bar,
            grid=grid,
            cycle_bars=cycle_bars,
            block=block,
        )
        if step is None:
            if missed is not None:
                missed.append(e)
            continue
        key = (name_of(e["midi"]), step)
        out.pop(key, None)
        out[key] = float(e.get("vel", 1.0))
    return [(p, s, v) for (p, s), v in out.items()]


def window_steps(
    t0: float, t1: float, *, bpm: float, bars: int, steps_per_bar: int
) -> set[int]:
    """The pattern steps a take window [t0, t1) covered (all of them once
    it spans a whole loop of the pattern)."""
    total = bars * steps_per_bar
    step = bar_seconds(bpm) / steps_per_bar
    s0, s1 = _round(t0 / step), _round(t1 / step)
    if s1 - s0 >= total:
        return set(range(total))
    return {k % total for k in range(s0, max(s0, s1))}


def merge(pattern: dict, notes, *, clear: set[int] = frozenset()) -> None:
    """Write quantized notes into a notes pattern in place: first drop the
    cells (and step velocities) on `clear`, then overdub. Every played
    note-on is an attack: one landing right after a step of its own pitch
    is a cut (`Arrangement.cuts`), so four repeated 16ths record as four
    notes, not one held run."""
    cells = [list(c) for c in pattern["cells"] if c[1] not in clear]
    cuts = [list(c) for c in pattern.get("cuts") or [] if c[1] not in clear]
    vel = {k: v for k, v in pattern["velocities"].items() if int(k) not in clear}
    have = {(c[0], c[1]) for c in cells}
    played = []
    for pitch, step, v in notes:
        if (pitch, step) not in have:
            cells.append([pitch, step])
            have.add((pitch, step))
        played.append((pitch, step))
        vel[str(step)] = min(1.0, max(0.0, v))
    cut = {(c[0], c[1]) for c in cuts}
    for pitch, step in played:
        if (pitch, step - 1) in have and (pitch, step) not in cut:
            cuts.append([pitch, step])
            cut.add((pitch, step))
    pattern["cells"] = cells
    pattern["cuts"] = cuts
    pattern["velocities"] = vel


def quantize_hits(
    events,
    *,
    bpm: float,
    bars: int,
    steps: int,
    grid: str = "1/16",
    cycle_bars: int | None = None,
    block: tuple[int, int] | None = None,
    missed: list | None = None,
) -> list[tuple[str, int, float]]:
    """[(voice, step, velocity)] for a take's drum hits (`{voice, t, vel}`),
    as `quantize` places notes: a later hit on the same (voice, step)
    wins."""
    out: dict[tuple[str, int], float] = {}
    for e in events:
        step = step_of(
            e["t"],
            bpm=bpm,
            bars=bars,
            steps_per_bar=steps,
            grid=grid,
            cycle_bars=cycle_bars,
            block=block,
        )
        if step is None:
            if missed is not None:
                missed.append(e)
            continue
        key = (e["voice"], step)
        out.pop(key, None)
        out[key] = float(e.get("vel", 1.0))
    return [(v, s, x) for (v, s), x in out.items()]


def merge_hits(pattern: dict, hits, *, clear: set[int] = frozenset()) -> None:
    """Write quantized hits into a drums pattern in place: first drop every
    voice's cell (and its per-hit data) on `clear`, then overdub — a hit
    already on the grid takes the new velocity."""
    cells = [list(c) for c in pattern.get("drums") or [] if int(c[1]) not in clear]
    data = {
        k: dict(v)
        for k, v in (pattern.get("hits") or {}).items()
        if int(k.split(".")[1]) not in clear
    }
    have = {(c[0], int(c[1])) for c in cells}
    for voice, step, vel in hits:
        if (voice, step) not in have:
            cells.append([voice, step])
            have.add((voice, step))
        key = f"{voice}.{step}"
        data[key] = data.get(key, {}) | {"v": round(min(1.0, max(0.05, vel)), 3)}
    pattern["drums"] = cells
    pattern["hits"] = data
