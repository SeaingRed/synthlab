"""Automation moves: four ready-made lanes a user puts on one track in one
section with one click (studio docs/song.md, "Automation moves").

A move writes nothing the lane machinery does not already take: a level
lane (`curves.GAIN`) or an FX-parameter lane on a unit of the track's rack
(`curves.fx_target`, stored by `song_model.set_lane`). A move that needs a
unit the rack does not hold adds it AT REST — the parameter where the lane
is at its resting end, so the track sounds as it did there — and a full
rack (`fxrack.MAX_UNITS`) refuses the move in words with nothing changed.
Applying a move again replaces its own lane and reuses its unit.

Positions are 0..1 across one pass of the section, laid out in BARS (a
lane stays in bars under a tempo ramp: `meter.warp`), so the last beat is
`1 / (bars * the meter's beats a bar)` of the lane whatever the meter.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass

from . import limits
from . import song_model as sm
from .curves import GAIN, GAIN_UNITY, fx_target
from .fxrack import FX_UNITS, MAX_UNITS, clean_chain, unit_id
from .meter import bar_beats


class MoveError(sm.SongError):
    """A move refused, in words a toast can say (a `SongError`, so a route
    answers it as every other refused song edit)."""


@dataclass(frozen=True)
class Move:
    kind: str | None  # the FX unit the lane rides; None: the track's level
    param: str
    at_rest: dict[str, float]  # a unit this move adds starts like this
    points: Callable[[float, float], list[list[float]]]  # (rest 0..1, beats)
    says: str  # "<says> <track> across <section>"


def _open(rest: float, beats: float) -> list[list[float]]:
    # slow at first: 0.04 of the cutoff range is ~580 Hz, the first half
    # reaches only 0.15 (~1.9 kHz), the rest of the climb is the second half
    return [[0.0, 0.04], [0.5, 0.15], [1.0, 1.0]]


def _wash(rest: float, beats: float) -> list[list[float]]:
    return [[0.0, rest], [0.5, rest], [1.0, max(rest, 0.8)]]


def _pump(rest: float, beats: float) -> list[list[float]]:
    return [[0.0, rest], [1.0, max(rest, 0.9)]]


def _cut(rest: float, beats: float) -> list[list[float]]:
    """Unity, a quick fall to 0 on the last beat's downbeat, held to the
    section's end (every repeat replays the lane, so the level is back at
    the next pass). A section of under a beat and a half has no room for
    the unity run: its whole pass is the cut."""
    last = 1.0 / beats
    fall = min(last / 8, 0.01)
    if 1.0 - last - fall <= 0.0:
        return [[0.0, 0.0], [1.0, 0.0]]
    return [
        [0.0, GAIN_UNITY],
        [1.0 - last - fall, GAIN_UNITY],
        [1.0 - last, 0.0],
        [1.0, 0.0],
    ]


MOVES: dict[str, Move] = {
    "open_filter": Move(
        "lowpass",
        "cutoff",
        {"cutoff": FX_UNITS["lowpass"]["params"]["cutoff"][1]},  # its top
        _open,
        "Opened the filter of",
    ),
    "wash_out": Move("reverb", "mix", {"mix": 0.0}, _wash, "Washed out"),
    "pump_harder": Move("pump", "depth", {"depth": 0.0}, _pump, "Pumped harder on"),
    "drop_cut": Move(None, GAIN, {}, _cut, "Cut the last beat of"),
}


def _by_id(rows: list[dict], what: str, rid) -> dict:
    for r in rows:
        if r["id"] == rid:
            return r
    raise MoveError(f"No such {what}.")


def _unit_index(chain: list[dict], kind: str) -> int | None:
    """The unit a move rides: the first of its kind that sounds, else the
    first (a bypassed one is not stripped, so it is not reused while
    another is free — the move adds its own)."""
    live = [i for i, u in enumerate(chain) if u["kind"] == kind and not u.get("off")]
    return live[0] if live else None


def apply(song: dict, section_id: str, track_id: str, move: str) -> str:
    """Put `move` on `track_id` in `section_id` and say what was done.
    Changes `song` only when it succeeds; raises `MoveError` otherwise."""
    spec = MOVES.get(move)
    if spec is None:
        raise MoveError("No such move.")
    work = copy.deepcopy(song)
    sec = _by_id(work["sections"], "section", section_id)
    track = _by_id(work["tracks"], "track", track_id)
    if not sm.lanes_live(sec, track):
        what = "take" if track["kind"] == "audio" else "pattern"
        raise MoveError(f"{track['name']} has no {what} in {sec['name']}.")

    added = ""
    rest = 0.0
    if spec.kind is None:
        target = GAIN
    else:
        chain = clean_chain(track.get("fx") or [])
        i = _unit_index(chain, spec.kind)
        if i is None:
            if len(chain) >= MAX_UNITS:
                raise MoveError(
                    f"{track['name']}'s rack is full ({MAX_UNITS} units): "
                    f"remove one to make room for a {FX_UNITS[spec.kind]['label']}."
                )
            chain.append({"kind": spec.kind, "params": dict(spec.at_rest)})
            chain = clean_chain(chain)
            i = len(chain) - 1
            added = f", adding a {FX_UNITS[spec.kind]['label']} at rest"
        target = fx_target(unit_id(chain, i), spec.kind, spec.param)
        track["fx"] = chain
        rest = sm.lane_rest(track, target)

    beats = sec["bars"] * bar_beats(sm.section_meter(work, sec))
    sm.set_lane(work, section_id, track_id, target, spec.points(rest, beats))
    seconds = sm.section_clock(work, sec).seconds(sm.cycle_bars(work, sec))
    if seconds > limits.MAX_SECTION_SECONDS:
        raise MoveError(
            f"Automation plays {sec['name']} as one {seconds:.0f}s pass — the "
            f"cap is {limits.MAX_SECTION_SECONDS:.0f}s. Shorten the section "
            "or raise the bpm."
        )
    song.clear()
    song.update(work)
    return f"{spec.says} {track['name']} across {sec['name']}{added}."
