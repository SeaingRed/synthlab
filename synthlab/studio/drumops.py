"""Drum pattern tools: `patternops`' edits on a drums lane (`bars`,
`steps`, `drums`, `drum_vel`, and the per-hit `hits` and row `swing` when
it has them) — shift, reverse, duplicate, clear and the velocity tools,
over every voice and the lane's steps. Kept apart from `patternops.py`
so the beat workflow and the piano roll each own a file; the shared
primitives stay there.

The beat workflow's own half: what a grid form
says about hits (`hits_from_form`, `row_swing_from_form`), Euclid a row,
the loop's lane both ways (`loop_lane`, `loop_fields`), and a kit swapped
under a beat (`swap_kit`). All pure: a lane in, a lane out.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping

from ..sequencer import euclid as _euclid
from . import limits
from .arrange import clean_hits
from .patternops import (
    ToolRefused,
    _steps_fn,
    _vel_tool,
    can_duplicate,
    clear,
    duplicate,
    duplicate_lane,
    remap,
    remap_lane,
)
from .state import ALL_DRUM_ROWS

DRUM_TOOLS = frozenset(
    {
        "shift_left",
        "shift_right",
        "reverse",
        "duplicate",
        "clear",
        "vel_humanise",
        "vel_ramp",
        "vel_accent",
        "vel_reset",
    }
)
# a hit's fields, and what a lane form may call them
HIT_FIELDS = ("v", "p", "r", "n", "t")
MAX_HITS_JSON = 256 * 1024  # the grid's hidden `hits` field, characters


def _hit_step(key: str) -> tuple[str, int]:
    voice, _dot, raw = key.partition(".")
    return voice, int(raw)


def remap_hits(hits: Mapping, total: int, fn) -> dict:
    """A per-hit table through the step remap its cells take (`remap`):
    in-window hits move with their cells, the rest are kept. Reversed, an
    early hit is a late one: the nudge changes sign with the bar."""
    out: dict = {}
    for key, h in (hits or {}).items():
        voice, s = _hit_step(key)
        out[f"{voice}.{fn(s)}" if 0 <= s < total else key] = h
    return out


def duplicate_hits(hits: Mapping, total: int) -> dict:
    out = {
        k: v
        for k, v in (hits or {}).items()
        if not total <= _hit_step(k)[1] < 2 * total
    }
    for key, h in (hits or {}).items():
        voice, s = _hit_step(key)
        if 0 <= s < total:
            out[f"{voice}.{s + total}"] = dict(h)
    return out


def _with(lane: dict, **changes) -> dict:
    """`lane` with `changes`, its per-hit table held to its cells and the
    optional keys absent when empty (a lane without them reads as before)."""
    out = {**lane, **changes}
    out["hits"] = clean_hits(out.get("hits") or {}, out["drums"])
    for k in ("hits", "swing"):
        if not out.get(k):
            out.pop(k, None)
    return out


def drums_tool(op: str, lane: dict, *, choices: Collection[int], seed: int = 0) -> dict:
    """`op` on a drums lane (every voice, the lane's steps). Raises
    `ToolRefused` / KeyError as `notes_tool` does. A hit's data follows its
    cell through every one of them."""
    if op not in DRUM_TOOLS:
        raise KeyError(op)
    out = dict(lane)
    bars, steps = int(lane["bars"]), int(lane["steps"])
    total = bars * steps
    hits = lane.get("hits") or {}
    if op in ("shift_left", "shift_right", "reverse"):
        fn = _steps_fn(op, total)
        moved = remap_hits(hits, total, fn)
        if op == "reverse":
            moved = {
                k: ({**h, "n": -h["n"]} if "n" in h else h) for k, h in moved.items()
            }
        out |= {
            "drums": remap(lane["drums"], total, None, fn),
            "drum_vel": remap_lane(lane["drum_vel"], total, fn),
            "hits": moved,
        }
    elif op == "duplicate":
        if not can_duplicate(bars, choices):
            raise ToolRefused(
                f"{bars} bars can't double — the grid's longest is {max(choices)}."
            )
        out |= {
            "bars": bars * 2,
            "drums": duplicate(lane["drums"], bars, steps),
            "drum_vel": duplicate_lane(lane["drum_vel"], total),
            "hits": duplicate_hits(hits, total),
        }
    elif op == "clear":
        out["drums"] = clear(lane["drums"], total)  # the velocity lane stays
    else:
        on = {int(c[1]) for c in lane["drums"] if 0 <= int(c[1]) < total}
        out["drum_vel"] = _vel_tool(
            op, lane["drum_vel"], on, total, steps // 4, 1.0, seed
        )
    return _with(out)


# --- the loop's beat as a lane, and back ------------------------------------------


def loop_lane(arr) -> dict:
    """The working loop's drum lane (an `Arrangement`) as a drums lane."""
    return _with(
        {
            "bars": arr.drum_bars,
            "steps": arr.drum_steps,
            "drums": [list(c) for c in arr.drums],
            "drum_vel": dict(arr.drum_vel),
            "hits": {k: dict(v) for k, v in arr.drum_hits.items()},
            "swing": dict(arr.drum_row_swing),
        }
    )


def loop_fields(lane: dict) -> dict:
    """A drums lane as the `Arrangement` fields it is (`replace(arr, **…)`):
    always all six, so a lane without per-hit data clears the loop's."""
    return {
        "drum_bars": int(lane["bars"]),
        "drum_steps": int(lane["steps"]),
        "drums": [list(c) for c in lane["drums"]],
        "drum_vel": dict(lane.get("drum_vel") or {}),
        "drum_hits": clean_hits(lane.get("hits") or {}, lane["drums"]),
        "drum_row_swing": dict(lane.get("swing") or {}),
    }


# --- a grid form's hits ------------------------------------------------------------


def _number(raw) -> float | None:
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def hits_from_form(form: Mapping[str, str], drums, prev: Mapping | None = None) -> dict:
    """The per-hit table a grid post leaves. The grid posts its whole table
    as one hidden field, `hits` (JSON, `{"<voice>.<step>": {v, p, r, n,
    t}}`: steps.js keeps it); a post without it keeps the table it had.
    Single values may ride beside it as `hit.<voice>.<step>.<field>` (a
    number, or "" to clear that field) — what a script, a deck or a test
    posts. Either way a hit without a cell is dropped (the cuts rule), a
    value is clamped (`schema.DRUM_HITS`) and one that says nothing is not
    stored. Hostile input changes nothing it cannot read."""
    table: dict = {k: dict(v) for k, v in (prev or {}).items()}
    raw = form.get("hits")
    if isinstance(raw, str) and len(raw) <= MAX_HITS_JSON:
        try:
            parsed = json.loads(raw)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, dict):
            table = {
                k: dict(v)
                for k, v in parsed.items()
                if isinstance(k, str) and isinstance(v, dict)
            }
    for key, value in form.items():
        if not key.startswith("hit."):
            continue
        parts = key.split(".")
        if (
            len(parts) != 4
            or parts[1] not in ALL_DRUM_ROWS
            or parts[3] not in HIT_FIELDS
        ):
            continue
        step = limits.step_key(parts[2])
        if step is None:
            continue
        hit = table.setdefault(f"{parts[1]}.{step}", {})
        x = _number(value)
        if x is None:
            hit.pop(parts[3], None)
        else:
            hit[parts[3]] = x
    return clean_hits(table, drums)


def row_swing_from_form(
    form: Mapping[str, str], prev: Mapping | None, pattern_swing: float
) -> dict:
    """The rows' own swing after a grid post: `rswing` is the grid's hidden
    JSON `{voice: 0..0.6}` (the rows whose knob is off the pattern's
    swing); `rswing.<voice>` a single row (a number, or "" to follow the
    pattern again). A row set to the pattern's own swing has no swing of
    its own. A post with neither keeps what there was."""
    out = dict(prev or {})
    raw = form.get("rswing")
    if isinstance(raw, str) and len(raw) <= 4096:
        try:
            parsed = json.loads(raw)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, dict):
            out = parsed
    for key, value in form.items():
        if key.startswith("rswing.") and key[7:] in ALL_DRUM_ROWS:
            x = _number(value)
            if x is None:
                out.pop(key[7:], None)
            else:
                out[key[7:]] = x
    clean: dict = {}
    for voice in ALL_DRUM_ROWS:
        x = _number(out.get(voice)) if voice in out else None
        if x is None:
            continue
        x = round(min(0.6, max(0.0, x)), 4)
        if abs(x - float(pattern_swing)) > 0.0005:
            clean[voice] = x
    return clean


def levels_from_form(form: Mapping[str, str]) -> dict[str, float]:
    """`rlevel` (the grid's hidden JSON `{voice: 0..1.5}`: the rows whose
    level knob moved) and `level.<voice>`: a row's level in the kit's
    mixer, set from its head."""
    out: dict = {}
    raw = form.get("rlevel")
    if isinstance(raw, str) and len(raw) <= 4096:
        try:
            parsed = json.loads(raw)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, dict):
            out = parsed
    for key, value in form.items():
        if key.startswith("level.") and key[6:] in ALL_DRUM_ROWS:
            out[key[6:]] = value
    clean: dict[str, float] = {}
    for voice in ALL_DRUM_ROWS:
        x = _number(out.get(voice)) if voice in out else None
        if x is not None:
            clean[voice] = min(1.5, max(0.0, x))
    return clean


# --- a row, by Euclid ---------------------------------------------------------------


def euclid_row(lane: dict, voice: str, hits: int, of: int, rotate: int = 0) -> dict:
    """One row set to `hits` pulses spread as evenly as they go over `of`
    steps, turned by `rotate`, and repeated to the lane's end (3 in 8 is
    the tresillo, 5 in 16 the bossa). The figure STARTS ON A HIT: the
    engine's `sequencer.euclid` ends on one (its 3 in 8 is `..x..x.x`), so
    it is asked for one step on — `x..x..x.`, the longest gaps first — and
    `rotate` turns it from there. The row's hits past the grid stay
    hidden; the other rows do not move."""
    if voice not in ALL_DRUM_ROWS:
        raise KeyError(voice)
    total = int(lane["bars"]) * int(lane["steps"])
    of = max(1, min(int(of), total, limits.MAX_GRID_STEPS))
    hits = max(0, min(int(hits), of))
    figure = _euclid(hits, of, rotate=(1 + int(rotate)) % of) if hits else "." * of
    mine = [[voice, s] for s in range(total) if figure[s % of] == "x"]
    rest = [
        list(c) for c in lane["drums"] if c[0] != voice or not 0 <= int(c[1]) < total
    ]
    return _with(lane, drums=sorted(rest + mine, key=lambda c: (c[0], c[1])))


def with_hidden(lane: dict, was: dict) -> dict:
    """`lane` (a generator's: it holds only what its grid shows) with the
    hits `was` kept, hidden, past the grid's end — a generator rewrites
    what it can see, and a grid grown back still finds its later bars."""
    total = int(lane["bars"]) * int(lane["steps"])
    past = [list(c) for c in was["drums"] if int(c[1]) >= total]
    if not past:
        return _with(lane)
    names = {f"{v}.{s}" for v, s in past}
    hits = {k: v for k, v in (was.get("hits") or {}).items() if k in names}
    return _with(
        lane,
        drums=sorted([*lane["drums"], *past], key=lambda c: (c[0], c[1])),
        hits={**(lane.get("hits") or {}), **hits},
    )


# --- a kit under a beat -------------------------------------------------------------


def swap_kit(lane: dict, kit: dict, samples: Mapping | None = None) -> dict:
    """What a kit swap that KEEPS THE BEAT loads from `kit`
    (`drumfactory.DRUM_KITS[...]`): the voices' shaping — nothing of the
    pattern. The lane (its cells, velocities, per-hit data, row swings)
    and the rows' sample assignments are returned as they were, row for
    row: both kits are the same eight voices, so "by voice role" is the
    row itself, and a row the kit does not shape keeps the shaping it had
    (`unshaped` names those that carry hits)."""
    played = {c[0] for c in lane["drums"]}
    return {
        "lane": _with(lane),
        "params": {v: dict(p) for v, p in kit["params"].items()},
        "samples": dict(samples or {}),
        "unshaped": sorted(played - set(kit["params"]), key=ALL_DRUM_ROWS.index),
    }


# --- the rows a grid draws ----------------------------------------------------------


def keep_hidden(
    drums, hits: Mapping, was_drums, was_hits: Mapping, drawn: Collection[str], form
) -> tuple[list, dict]:
    """A grid post's cells and hits with the rows its grid did not draw
    kept as they were. A grid draws the rows of its beat's kit
    (`state.kit_rows`): the electronic kits' eight, the acoustic kit's own
    — so a post replaces `drums` whole only for those rows, and a row
    neither drawn nor posted (`drum.<voice>.<step>`) keeps its cells and
    their hits (steps.js prunes the table to the steps it draws)."""
    posted = {
        k.split(".")[1] for k in form if k.startswith("drum.") and k.count(".") == 2
    }
    mine = set(drawn) | posted
    kept = [list(c) for c in was_drums if c[0] not in mine]
    cells = [list(c) for c in drums if c[0] in mine] + kept
    out = {k: dict(v) for k, v in hits.items() if k.split(".")[0] in mine}
    out |= {
        k: dict(v) for k, v in (was_hits or {}).items() if k.split(".")[0] not in mine
    }
    return sorted(cells, key=lambda c: (c[0], int(c[1]))), clean_hits(out, cells)


def open_hats_to_hat(arr) -> dict:
    """What choosing the acoustic kit does to a beat (the Kit sound select):
    its `ohat` cells become hat hits played "open" (`a`), so the open hats
    are on the one hat row the kit has. A hit keeps its own data; the
    `ohat` row's chance goes onto its hits (times their own), its level
    onto their velocity (over the hat row's, held to 0.05..1) and its mute
    to a chance of 0 — so the beat plays what it played. A step the hat
    already strikes becomes the open hit. The `ohat` row's mix entry goes.
    Arrangement fields to `replace`; {} when there is no open hat."""
    opens = [int(s) for v, s in arr.drums if v == "ohat"]
    if not opens:
        return {}
    mix = dict(arr.drum_mix or {})
    om, hm = mix.pop("ohat", {}) or {}, mix.get("hat", {}) or {}
    ratio = float(om.get("level", 1.0)) / max(1e-6, float(hm.get("level", 1.0)))
    prob = 0.0 if om.get("mute") else float(om.get("prob", 1.0))
    hits = {
        k: dict(v)
        for k, v in (arr.drum_hits or {}).items()
        if not k.startswith("ohat.")
    }
    for s in opens:
        h = dict((arr.drum_hits or {}).get(f"ohat.{s}") or {})
        if abs(ratio - 1.0) > 1e-9:
            v = float(h.get("v", (arr.drum_vel or {}).get(str(s), 1.0)))
            h["v"] = round(min(1.0, max(0.05, v * ratio)), 3)
        if prob < 1.0:
            h["p"] = round(float(h.get("p", 1.0)) * prob, 3)
        h["a"] = "open"
        hits[f"hat.{s}"] = h
    cells = {(v, int(s)) for v, s in arr.drums if v != "ohat"} | {
        ("hat", s) for s in opens
    }
    drums = sorted([list(c) for c in cells], key=lambda c: (c[0], c[1]))
    return {"drums": drums, "drum_hits": clean_hits(hits, drums), "drum_mix": mix}


def hat_to_open_hats(arr) -> dict:
    """The way back (an electronic kit chosen): a hat hit played "half" or
    "open" goes onto the open hat's row (`ohat`, the electronic kits' open
    hat), keeping its own data but the articulation. Arrangement fields to
    `replace`; {} when the hat has no open hit."""
    hits = dict(arr.drum_hits or {})
    opens = sorted(
        int(k.split(".")[1])
        for k, h in hits.items()
        if k.startswith("hat.") and h.get("a") in ("half", "open")
    )
    if not opens:
        return {}
    for s in opens:
        h = {f: x for f, x in hits.pop(f"hat.{s}").items() if f != "a"}
        hits[f"ohat.{s}"] = h
    moved = set(opens)
    cells = {(v, int(s)) for v, s in arr.drums if not (v == "hat" and int(s) in moved)}
    cells |= {("ohat", s) for s in opens}
    drums = sorted([list(c) for c in cells], key=lambda c: (c[0], c[1]))
    return {"drums": drums, "drum_hits": clean_hits(hits, drums)}
