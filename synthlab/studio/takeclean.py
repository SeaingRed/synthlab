"""Clean up a recorded take: the deterministic half of the
Song page's "✦ Clean up". Pure: a take's raw events in, a pattern and a
list of what changed out. `takecoach.py` is the other half (Gemini's
feedback and a few musical edits, applied here by `apply_edits`).

A take is recorded through `record.py`, which snaps each event to the
grid as it arrives — one rounding, no judgement. The raw events are kept
beside it (`state.take_history`, `routes/song_record.py`), and this works
on those, from the pattern as it was before the take (`base`):

- **Lean.** A player (or an audio path) that sits early or late by the
  same amount drags every hit near a step boundary onto the wrong step. The
  circular mean of the hits' offsets from the grid finds that lean; when
  it is consistent (`LEAN_R`) and big enough to matter (`LEAN_MIN` of a
  grid step) every hit is moved back by it before snapping.
- **A whole step late.** A lean of a whole grid step looks like no lean
  at all. On drums the kick tells: when it lands one grid step off the
  beat nearly every time and hardly ever on it, the take is moved by that
  step (Bluetooth headphones report less latency than they have).
- **Grid.** The grid asked for (1/8, 1/16, 1/32); "off" takes the coarsest
  grid that fits what was played (`FIT`).
- **Doubles.** Two strikes of one voice (or pitch) closer than
  `DOUBLE` of a grid step in one pass are one hit: the louder is kept.
  The same step struck on several passes of an overdub is one hit, at the
  passes' mean velocity.
- **Velocity.** Each voice's velocities are drawn towards their median
  (`SQUEEZE`), so accents stay accents and a stray slap or ghost is tamed.
  Untouched when everything came in at one velocity (the computer keys).
- **Key.** A note outside the song's scale moves to the nearest note in
  it (down on a tie).
"""

from __future__ import annotations

import cmath
import copy
import math
import statistics
from dataclasses import dataclass, field

from ..sequencer import SCALES, midi, name_of
from . import record
from .drumlab import DRUM_VOICES

LEAN_R = 0.5  # how alike the offsets must be (mean resultant length)
LEAN_MIN = 0.1  # of a grid step: a smaller lean changes no step
DOUBLE = 0.6  # of a grid step: two strikes this close are one
FIT = 0.18  # "off": a grid fits when the mean miss is under this share
SQUEEZE = 0.6  # velocity spread kept around each voice's median
VEL_FLOOR = 0.25
MAX_EDITS = 8  # Gemini's edits applied, at most
VOICE_NAMES = {
    "kick": "kick",
    "snare": "snare",
    "hat": "closed hat",
    "ohat": "open hat",
    "clap": "clap",
    "tom": "tom",
    "rim": "rim",
    "crash": "crash",
}


@dataclass
class Take:
    """One take as recorded: `kind` "drums" | "notes"; `raw` the events as
    posted ({voice | midi, t, vel}, seconds from the section's start);
    the pattern's shape (`steps` a bar: a drums pattern's `steps`, a notes
    pattern's `steps_per_bar`); the section's `cycle_bars` and a trimmed
    block's (at, len); `base` the pattern before the take's first post and
    `clear` the steps a replace take cleared; the song's key."""

    kind: str
    raw: list
    bpm: float
    bars: int
    steps: int
    grid: str
    base: dict
    cycle_bars: int | None = None
    block: tuple | None = None
    clear: list = field(default_factory=list)
    root: str = "C2"
    scale_name: str = "minor"


@dataclass
class Cleaned:
    pattern: dict
    fixes: list  # sentences: what was changed and why
    stats: dict  # for the coach: per voice / pitch, timing and level
    played: list  # [(voice | pitch, step, velocity)]: the cleaned take
    grid: int  # the grid used, in pattern steps


def grid_label(steps: int, g: int) -> str:
    return f"1/{steps // g}" if steps % g == 0 else f"{g} steps"


def key_name(root: str, scale_name: str) -> str:
    """ "G minor" for ("G1", "minor"): the key without its octave."""
    return f"{root.rstrip('0123456789')} {scale_name.replace('_', ' ')}"


def _ms(steps: float, unit: float) -> int:
    return round(abs(steps) * unit * 1000)


def _in_key(root: str, scale_name: str) -> set[int]:
    tonic = midi(root) % 12
    return {(tonic + i) % 12 for i in SCALES.get(scale_name, SCALES["minor"])}


def to_key(m: int, keys: set[int]) -> int:
    """The nearest MIDI note to `m` whose pitch class is in `keys` (down on
    a tie); `m` itself when it already is."""
    for d in range(7):
        for c in (m - d, m + d):
            if 0 <= c <= 127 and c % 12 in keys:
                return c
    return m


def _events(take: Take, unit: float):
    """(what, x, vel, pass) per event: `x` in pattern steps from the
    pattern's start (fractional, unsnapped), `pass` the cycle it was
    played in. A block's outside is dropped, as record.step_of drops it."""
    bar = record.bar_seconds(take.bpm)
    out = []
    for e in take.raw:
        t = float(e["t"])
        n = 0
        if take.cycle_bars:
            n, t = divmod(t, take.cycle_bars * bar)
        x = t / unit
        if take.block:
            lo = take.block[0] * take.steps
            hi = (take.block[0] + take.block[1]) * take.steps
            if not lo - 0.5 <= x < hi - 0.5:
                continue
            x -= lo
        what = e["voice"] if take.kind == "drums" else int(e["midi"])
        out.append([what, x, float(e.get("vel", 1.0)), int(n)])
    return out


def _residual(x: float, g: int) -> float:
    return x - g * round(x / g)


def _pick_grid(take: Take, xs: list[float]) -> int:
    q = record.GRIDS.get(take.grid, 16)
    if take.grid != "off":
        return max(1, take.steps // q) if q < take.steps else 1
    for g in (8, 4, 2):  # coarsest first
        if g < take.steps and take.steps % g == 0 and xs:
            miss = statistics.fmean(abs(_residual(x, g)) for x in xs)
            if miss <= FIT * g:
                return g
    return 1


def _lean(xs: list[float], g: int) -> tuple[float, float]:
    """(the take's lean in pattern steps, how alike the offsets are 0..1)."""
    if not xs:
        return 0.0, 0.0
    v = sum(cmath.exp(2j * math.pi * _residual(x, g) / g) for x in xs) / len(xs)
    return g * cmath.phase(v) / (2 * math.pi), abs(v)


def _kick_shift(evs, g: int, steps: int) -> int:
    """A whole grid step of latency, read off the kick: the step to move
    the take by (0: none)."""
    beat = steps // 4
    if steps % 4 or g >= beat:
        return 0
    kicks = [round(x / g) * g for w, x, *_ in evs if w == "kick"]
    if len(kicks) < 4:
        return 0
    phase = [((k % beat) + beat // 2) % beat - beat // 2 for k in kicks]
    on = sum(1 for p in phase if p == 0)
    for off in (g, -g):
        if sum(1 for p in phase if p == off) >= 0.75 * len(kicks) and on <= 0.1 * len(
            kicks
        ):
            return off
    return 0


def clean(take: Take) -> Cleaned:
    unit = record.bar_seconds(take.bpm) / take.steps
    total = take.bars * take.steps
    evs = _events(take, unit)
    fixes: list[str] = []
    xs = [e[1] for e in evs]
    g = _pick_grid(take, xs)
    if take.grid == "off" and evs:
        fixes.append(f"Fitted the take to a {grid_label(take.steps, g)} grid.")
    lean, alike = _lean(xs, g)
    raw_off = {}  # per voice / pitch: signed offsets in steps, before any fix
    for w, x, *_ in evs:
        raw_off.setdefault(w, []).append(_residual(x, g))
    if not (len(evs) >= 4 and alike >= LEAN_R and abs(lean) >= LEAN_MIN * g):
        lean = 0.0
    for e in evs:
        e[1] -= lean
    shift = _kick_shift(evs, g, take.steps) if take.kind == "drums" else 0
    for e in evs:
        e[1] -= shift
    if moved_by := lean + shift:  # one sentence for both: they add up
        late = moved_by > 0
        said = (
            f"The take sat about {_ms(moved_by, unit)} ms {'late' if late else 'early'}"
        )
        if shift:
            said += (
                f" — the kick landed a {grid_label(take.steps, g)} "
                f"{'after' if shift > 0 else 'before'} the beat almost every time, "
                "which is usually latency (wireless headphones?)"
            )
        fixes.append(
            f"{said}. Moved it {_ms(moved_by, unit)} ms "
            f"{'earlier' if late else 'later'} before snapping."
        )
    # snap, and drop doubles inside one pass
    by_pass: dict = {}
    for w, x, v, n in evs:
        by_pass.setdefault((w, n), []).append((x, v))
    doubles = 0
    strikes: dict = {}  # (what, step) -> velocities, one a pass
    moved = 0
    for (w, _n), hits in by_pass.items():
        hits.sort()
        kept: list = []
        for x, v in hits:
            if kept and x - kept[-1][0] < DOUBLE * g:
                doubles += 1
                if v > kept[-1][1]:
                    kept[-1] = (kept[-1][0], v)
                continue
            kept.append((x, v))
        for x, v in kept:
            if abs(_residual(x, g)) > 0.25 * g:
                moved += 1
            step = (round(x / g) * g) % total
            strikes.setdefault((w, step), []).append(v)
    if doubles:
        fixes.append(
            f"Merged {doubles} double hit{'s' if doubles != 1 else ''} "
            f"(two strikes a flam apart) into one."
        )
    played = [(w, s, statistics.fmean(vs)) for (w, s), vs in strikes.items()]
    snapped = len(fixes)  # the sentence below; every other one is a fix
    if evs:
        fixes.append(
            f"Snapped {len(played)} hit{'s' if len(played) != 1 else ''} to the "
            f"{grid_label(take.steps, g)} grid"
            + (
                f"; {moved} {'was' if moved == 1 else 'were'} well off it."
                if moved
                else "."
            )
        )
    # velocity: towards each voice's median, keeping accents
    by_what: dict = {}
    for w, _s, v in played:
        by_what.setdefault(w, []).append(v)
    if len({round(v, 3) for _, _, v in played}) > 1:
        evened = 0
        out = []
        for w, s, v in played:
            med = statistics.median(by_what[w])
            nv = max(VEL_FLOOR, min(1.0, med + (v - med) * SQUEEZE))
            evened += abs(nv - v) > 0.05
            out.append((w, s, nv))
        played = out
        if evened:
            fixes.append(
                f"Evened out {evened} velocit{'ies' if evened != 1 else 'y'}: "
                "accents kept, the stray slaps and ghosts tamed."
            )
    # key
    if take.kind == "notes":
        keys = _in_key(take.root, take.scale_name)
        out, shifted = [], []
        for m, s, v in played:
            k = to_key(m, keys)
            if k != m:
                shifted.append(f"{name_of(m)}→{name_of(k)}")
            out.append((k, s, v))
        played = out
        if shifted:
            more = f" and {len(shifted) - 4} more" if len(shifted) > 4 else ""
            fixes.append(
                f"Moved {len(shifted)} note{'s' if len(shifted) != 1 else ''} into "
                f"{key_name(take.root, take.scale_name)}: "
                f"{', '.join(shifted[:4])}{more}."
            )
    # nothing but the grid fitted and the snap said: the take was already tight
    if (
        evs
        and not moved
        and len(fixes) == snapped + 1
        and snapped == (take.grid == "off")
    ):
        on = grid_label(take.steps, g)
        fixes = [f"Already tight: {len(played)} hits, all on the {on} grid."]
    pattern = copy.deepcopy(take.base)
    if take.kind == "drums":
        record.merge_hits(pattern, played, clear=set(take.clear))
    else:
        notes = {}
        for m, s, v in played:  # one pitch a step: the louder strike
            key = (name_of(m), s)
            notes[key] = max(v, notes.get(key, 0.0))
        record.merge(
            pattern, [(p, s, v) for (p, s), v in notes.items()], clear=set(take.clear)
        )
    stats = {
        (VOICE_NAMES.get(w, w) if take.kind == "drums" else name_of(w)): {
            "hits": len(offs),
            "lean_ms": round(statistics.fmean(offs) * unit * 1000),
            "spread_ms": round(statistics.pstdev(offs) * unit * 1000),
        }
        for w, offs in raw_off.items()
    }
    return Cleaned(pattern=pattern, fixes=fixes, stats=stats, played=played, grid=g)


# -- Gemini's edits ---------------------------------------------------------------


def apply_edits(
    pattern: dict,
    kind: str,
    edits,
    *,
    steps: int,
    root: str,
    scale_name: str,
) -> list[str]:
    """Apply the coach's edits to a cleaned pattern in place, after
    checking each one: a known voice (drums) or a note in the song's key
    (notes), a step inside the pattern, a `remove` / `velocity` only on a
    hit that is there. Anything else is skipped. At most `MAX_EDITS`.
    Returns a sentence per edit applied."""
    total = int(pattern.get("bars", 1)) * steps
    keys = _in_key(root, scale_name)
    done: list[str] = []
    for e in list(edits or [])[: MAX_EDITS * 2]:
        if len(done) >= MAX_EDITS or not isinstance(e, dict):
            break
        op, why = e.get("op"), str(e.get("why") or "").strip()[:120]
        try:
            step = int(e.get("step"))
            vel = float(e.get("velocity", 0.8))
        except (TypeError, ValueError):
            continue
        if not 0 <= step < total or not math.isfinite(vel):
            continue
        vel = min(1.0, max(VEL_FLOOR, vel))
        where = f"bar {step // steps + 1}, step {step % steps + 1}"
        if kind == "drums":
            voice = e.get("voice")
            if voice not in DRUM_VOICES:
                continue
            cells = pattern.setdefault("drums", [])
            hits = pattern.setdefault("hits", {})
            on = any(c[0] == voice and int(c[1]) == step for c in cells)
            key, name = f"{voice}.{step}", VOICE_NAMES[voice]
            if op == "add" and not on:
                cells.append([voice, step])
                hits[key] = {"v": round(vel, 3)}
                done.append(f"Added a {name} at {where}" + (f" — {why}" if why else ""))
            elif op == "remove" and on:
                pattern["drums"] = [
                    c for c in cells if not (c[0] == voice and int(c[1]) == step)
                ]
                hits.pop(key, None)
                done.append(
                    f"Removed the {name} at {where}" + (f" — {why}" if why else "")
                )
            elif op == "velocity" and on:
                hits[key] = hits.get(key, {}) | {"v": round(vel, 3)}
                done.append(
                    f"Set the {name} at {where} to {round(vel * 100)}%"
                    + (f" — {why}" if why else "")
                )
            continue
        try:
            m = midi(str(e.get("pitch")))
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if m % 12 not in keys:
            continue
        pitch = name_of(m)
        cells = pattern.setdefault("cells", [])
        on = any(c[0] == pitch and int(c[1]) == step for c in cells)
        if op == "add" and not on:
            cells.append([pitch, step])
            pattern.setdefault("velocities", {})[str(step)] = round(vel, 3)
            done.append(f"Added {pitch} at {where}" + (f" — {why}" if why else ""))
        elif op == "remove" and on:
            pattern["cells"] = [
                c for c in cells if not (c[0] == pitch and int(c[1]) == step)
            ]
            pattern["cuts"] = [
                c
                for c in pattern.get("cuts") or []
                if not (c[0] == pitch and int(c[1]) == step)
            ]
            done.append(f"Removed {pitch} at {where}" + (f" — {why}" if why else ""))
        elif op == "velocity" and on:
            pattern.setdefault("velocities", {})[str(step)] = round(vel, 3)
            done.append(
                f"Set {where} to {round(vel * 100)}%" + (f" — {why}" if why else "")
            )
    return done


def grid_text(pattern: dict, kind: str, steps: int) -> str:
    """The pattern as rows of text for the coach: a row per voice (drums,
    in kit order) or per pitch (notes, high to low), a cell per step —
    `.` empty, a digit 1-9 the velocity — bars split by `|`."""
    total = int(pattern.get("bars", 1)) * steps
    rows: dict = {}
    if kind == "drums":
        hits = pattern.get("hits") or {}
        vel = pattern.get("drum_vel") or {}
        for voice, s in pattern.get("drums") or []:
            v = (hits.get(f"{voice}.{int(s)}") or {}).get("v", vel.get(str(s), 0.8))
            rows.setdefault(voice, {})[int(s)] = v
        order = [v for v in DRUM_VOICES if v in rows]
    else:
        vel = pattern.get("velocities") or {}
        for pitch, s in pattern.get("cells") or []:
            rows.setdefault(pitch, {})[int(s)] = vel.get(str(s), 0.8)
        order = sorted(rows, key=midi, reverse=True)
    lines = []
    for name in order:
        cells = "".join(
            ("|" if s and s % steps == 0 else "")
            + (
                str(max(1, min(9, round(rows[name][s] * 9))))
                if s in rows[name]
                else "."
            )
            for s in range(total)
        )
        lines.append(f"{name:<6} {cells}")
    return "\n".join(lines) or "(empty)"
