"""The playlist's structural edits: cutting the timeline at a
bar, unrolling repeats, inserting and deleting bars, duplicating a section
with patterns of its own, a range of cells (clear, copy, move, make
unique, variation, a generator), the energy lane and what reads it.

Pure functions on the song dict, as `song_model` is: no StudioState, no
I/O. Each mutates the song it is handed — `routes/song_arr.py` runs it on
the copy `_commit` makes, so one call is one undo step — and refuses with
a `SongError` whose message is fit for a toast.

**The timeline** is every bar of every repeat, 0-based (`sm.song_bars`).
A structural edit never touches `song["long"]`: a whole stem sits at an
absolute bar and the arrangement moves under it (`long_under` names the
stems an insert or a delete moves audio under, for the confirm).

**A cut renders what the uncut section rendered** — pinned at 1e-9 by
`tests/studio/test_song_ops.py` across the cycle, chunk and repeat paths —
with these stated exceptions, each named in what `cut` returns:

- a note held across the cut attacks again there (a section boundary ends
  every run), an arp counts from the cut, and a sound's own drawn curve
  restarts with the pattern on the right;
- a lane is divided at the cut by linear interpolation, so its values are
  the same curve — but what RINGS across the cut (a release, a tail) is
  held at the lane's value at the cut instead of following it on;
- an effect that is not linear and time-invariant (`LINEAR_FX` lists the
  ones that are) restarts or re-reads at the cut, as at any boundary, and
  a delay's echoes stop where a stem's ring does;
- a beat with chance or humanise draws its rolls again (the roll is seeded
  from the pattern).

A cell's pattern is kept by reference wherever it still fits; where the cut
falls inside a pattern the right half plays a `window` of it (a new pool
pattern, "… ↻": the same notes from the cut's phase), and a half shorter
than its pattern plays a window that long. A pattern is 1, 2, 3, 4 or 8
bars: a cut that would leave 5, 6 or 7 bars of an 8-bar pattern is made
with a second cut four bars on (or back), so every part has a length a
pattern can have — and says so.
"""

from __future__ import annotations

import copy

from . import limits
from . import song_model as sm
from .song_model import SongError

# the FX kinds a cut is exact through: linear and time-invariant, so the
# chain over the left half and its ring, folded into the right half, is the
# chain over the whole (measured: 1e-13 or better). A delay is linear too,
# but a stem's ring is rendered for four beats and a second past its last
# note (`render._cycle_stems`), and a delay's echoes outlast that: at a
# boundary they stop there (measured -48 dB of the mix's peak)
LINEAR_FX = frozenset({"reverb", "lowpass", "highpass", "eq", "comb"})
WINDOW_MARK = "↻"
EPS = 1e-6


# --- the timeline -----------------------------------------------------------------


def starts(song: dict) -> list[int]:
    """Each section's first timeline bar (0-based), and the song's end."""
    out, at = [], 0
    for sec in song["sections"]:
        out.append(at)
        at += sec["bars"] * sec["repeats"]
    return [*out, at]


def locate(song: dict, bar: int) -> tuple[int, int, int]:
    """Timeline bar -> (section index, pass, bars into the pass). The
    song's own end is (number of sections, 0, 0)."""
    bar = int(bar)
    at = starts(song)
    if not 0 <= bar <= at[-1]:
        raise SongError(f"Bar {bar + 1} is past the song's end.")
    for i, sec in enumerate(song["sections"]):
        if bar < at[i + 1]:
            into = bar - at[i]
            return i, into // sec["bars"], into % sec["bars"]
    return len(song["sections"]), 0, 0


def long_under(song: dict, bar: int) -> list[str]:
    """The whole stems an edit that moves the timeline from `bar` on would
    move audio under: those that play at or after it. They keep their bar."""
    bar_s = 4 * 60.0 / song["bpm"]
    out = []
    for x in sm.long_items(song):
        whole = x["length"] or max(0.0, (x["seconds"] or 0.0) - x["start"])
        end = x["at_bar"] + (whole / bar_s if whole else limits.MAX_LONG_BAR)
        if end > bar + EPS:
            out.append(x["name"])
    return out


def _room(song: dict, more: int) -> None:
    if len(song["sections"]) + more > limits.MAX_SECTIONS:
        raise SongError(f"A song holds at most {limits.MAX_SECTIONS} sections.")


def _named(name: str, tag: str) -> str:
    return f"{name[: limits.MAX_TITLE_CHARS - len(tag) - 1]} {tag}"


def _clone(song: dict, sec: dict, name: str | None = None) -> dict:
    """A copy of a section under a new id: the same patterns by reference,
    its lanes, spans, takes, chords and energy copied."""
    dup = copy.deepcopy(sec)
    dup["id"] = sm._fresh_id(song)
    if name is not None:
        dup["name"] = name
    return dup


# --- a window of a pattern ----------------------------------------------------------


def _per_bar(p: dict) -> int:
    return int(p["steps"] if p["kind"] == "drums" else p["steps_per_bar"])


def window(p: dict, phase: int, bars: int) -> dict:
    """Bars [phase, phase + bars) of a pattern played round and round, as
    the lanes of a pattern of its own (`bars` <= the pattern's): a
    rotation when `bars` is the pattern's length, a crop when it is less.
    Where the pattern's own first step lands inside the window a held note
    is cut there, because a repeat of a pattern attacks again."""
    per = _per_bar(p)
    period = sm.pattern_bars(p) * per
    lo, n = (int(phase) * per) % period, int(bars) * per

    def to(step) -> int | None:
        s = int(step)
        if not 0 <= s < period:
            return None  # hidden past a shrunk grid: it never played
        x = (s - lo) % period
        return x if x < n else None

    def pairs(items) -> list[list]:
        return [[a, x] for a, s in items if (x := to(s)) is not None]

    def keyed(values: dict) -> dict:
        return {str(x): v for k, v in values.items() if (x := to(k)) is not None}

    if p["kind"] == "drums":
        out = {
            "bars": int(bars),
            "steps": per,
            "drums": pairs(p["drums"]),
            "drum_vel": keyed(p.get("drum_vel") or {}),
        }
        hits = {}
        for key, v in (p.get("hits") or {}).items():
            voice, _dot, step = key.rpartition(".")
            if step.isdigit() and (x := to(step)) is not None:
                hits[f"{voice}.{x}"] = copy.deepcopy(v)
        if hits:
            out["hits"] = hits
        if p.get("swing"):
            out["swing"] = dict(p["swing"])
        return out
    cuts = pairs(p.get("cuts") or [])
    wrap = to(0)
    if wrap:  # the pattern's own start, inside the window: a repeat attacks
        last = {c[0] for c in p["cells"] if int(c[1]) == period - 1}
        first = {c[0] for c in p["cells"] if int(c[1]) == 0}
        cuts += [[pitch, wrap] for pitch in sorted(last & first)]
    out = {
        "bars": int(bars),
        "steps_per_bar": per,
        "cells": pairs(p["cells"]),
        "cuts": [list(c) for c in dict.fromkeys(map(tuple, cuts))],
        "velocities": keyed(p.get("velocities") or {}),
        "slides": sorted(x for s in p.get("slides") or [] if (x := to(s)) is not None),
    }
    for k in ("arp_mode", "arp_octaves", "strum"):
        if k in p:
            out[k] = p[k]
    # timing inside a step and the chance lane follow their steps (absent
    # when none: a window of a pattern without them gains no key)
    for k in ("note_nudge", "note_chance"):
        if moved := keyed(p.get(k) or {}):
            out[k] = moved
    return out


def held_across(p: dict, phase: int) -> bool:
    """Whether a notes pattern holds a note over the bar line `phase` bars
    in: one run on both sides with no cut between. (Bar 0 is the pattern's
    own start, where a repeat attacks anyway.)"""
    if p["kind"] != "notes":
        return False
    per = _per_bar(p)
    period = sm.pattern_bars(p) * per
    lo = (int(phase) * per) % period
    if lo == 0:
        return False
    before = {c[0] for c in p["cells"] if int(c[1]) == lo - 1}
    here = {c[0] for c in p["cells"] if int(c[1]) == lo}
    cut = {c[0] for c in p.get("cuts") or [] if int(c[1]) == lo}
    return bool((before & here) - cut)


# --- lanes and chords, divided ------------------------------------------------------


def cut_lane(points: list, lo: float, hi: float) -> list:
    """The part of a lane's breakpoints over positions [lo, hi] (0 <= lo <
    hi <= 1), stretched to 0..1: the same curve there. The values at the
    two ends are interpolated (exact: a lane is linear between its
    points)."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    span = hi - lo
    out = [[(x - lo) / span, y] for x, y in points if lo + EPS < x < hi - EPS]
    # the edge values are held, so an end point may be left out when the
    # lane is already full (it could only repeat the value beside it)
    if len(out) < limits.MAX_LANE_POINTS:
        out.insert(0, [0.0, sm._interp(lo, xs, ys)])
    if len(out) < limits.MAX_LANE_POINTS:
        out.append([1.0, sm._interp(hi, xs, ys)])
    return out


def lane_halves(points: list, n_left: int, n: int) -> tuple[list, list]:
    """A lane over a cycle of `n` samples, divided at sample `n_left`.
    `curves.lane_array` reads sample i at position i / (n - 1), so the
    left half's last sample sits at (n_left - 1) / (n - 1) and the right
    half's first at n_left / (n - 1): each half reads, sample for sample,
    the value the whole read there."""
    top = max(1, n - 1)
    return (
        cut_lane(points, 0.0, max(1, n_left - 1) / top),
        cut_lane(points, min(n_left, top - 1) / top, 1.0),
    )


def rotate_chords(track: dict, bars: int) -> dict:
    """A chord track as it reads from `bars` bars in: the same progression
    with its phase carried (a slot that crossed the new start is two)."""
    period = int(track["bars"]) * 4.0
    shift = (int(bars) * 4.0) % period
    if not shift:
        return copy.deepcopy(track)
    slots = sorted(track["slots"], key=lambda s: s["at"])
    out = []
    for i, s in enumerate(slots):
        lo = 0.0 if i == 0 else float(s["at"])
        hi = float(slots[i + 1]["at"]) if i + 1 < len(slots) else period
        for a, b in ((lo, min(hi, shift)), (max(lo, shift), hi)):
            if b - a > EPS:
                at = (a - shift) % period
                out.append({**s, "at": at, "len": max(0.25, b - a)})
    out.sort(key=lambda s: s["at"])
    return {"bars": track["bars"], "slots": out[: limits.MAX_CHORD_SLOTS]}


def chord_edge(track: dict, slot: int, at: float) -> dict:
    """Move where chord `slot` (>= 1, in time order) starts to beat `at`
    of its progression: the chord before it is that much longer or
    shorter. Held a quarter of a beat clear of the chords on either side
    and of the progression's end, on a quarter-beat grid. The track, with
    every slot's `len` the distance to the next."""
    slots = sorted(copy.deepcopy(track["slots"]), key=lambda s: s["at"])
    period = int(track["bars"]) * 4.0
    if not 1 <= int(slot) < len(slots):
        raise SongError("That chord starts the progression: drag the next one.")
    i = int(slot)
    lo = (0.0 if i == 1 else float(slots[i - 1]["at"])) + 0.25
    hi = (float(slots[i + 1]["at"]) if i + 1 < len(slots) else period) - 0.25
    slots[i]["at"] = min(max(round(float(at) * 4) / 4, lo), max(lo, hi))
    for k, s in enumerate(slots):
        end = float(slots[k + 1]["at"]) if k + 1 < len(slots) else period
        start = 0.0 if k == 0 else float(s["at"])
        s["len"] = max(0.25, end - start)
    return {"bars": track["bars"], "slots": slots}


def chord_split(track: dict, slot: int) -> dict:
    """Chord `slot` (in time order) as two of the same chord, each half
    as long: the second is then another chord's to become. Refused under
    half a beat, and past `MAX_CHORD_SLOTS`."""
    slots = sorted(copy.deepcopy(track["slots"]), key=lambda s: s["at"])
    period = int(track["bars"]) * 4.0
    if not 0 <= int(slot) < len(slots):
        raise SongError("No such chord.")
    if len(slots) >= limits.MAX_CHORD_SLOTS:
        raise SongError(f"A progression holds at most {limits.MAX_CHORD_SLOTS} chords.")
    i = int(slot)
    start = 0.0 if i == 0 else float(slots[i]["at"])
    end = float(slots[i + 1]["at"]) if i + 1 < len(slots) else period
    half = round((end - start) * 2) / 4  # half of it, on the quarter-beat grid
    if half < 0.25 or end - start - half < 0.25:
        raise SongError("That chord is too short to split.")
    first = {**slots[i], "at": start, "len": half}
    second = {**slots[i], "at": start + half, "len": end - start - half}
    return {
        "bars": track["bars"],
        "slots": [*slots[:i], first, second, *slots[i + 1 :]],
    }


# the edits a chord track takes, the song's (`POST /song/chords`) and the
# working loop's (`POST /fragment/chords/track`, Perform's chord lane) alike
CHORD_EDITS = ("preset", "roll", "set", "edge", "split", "add", "remove", "bars")


def edit_chords(song: dict, track: dict, op: str, a: dict) -> dict:
    """`track` (the chord track in force; `song` gives its key and its
    style's progressions: a song, or the loop as one) after edit `op`, one
    of `CHORD_EDITS`, read from `a` (`routes/song_start.chord_args`):

    - `preset`: progression `prog` of the menu (`build.progressions`);
      `roll`: one of them, drawn by `seed`;
    - `set`: slot `slot`'s `deg` (1-7), `q` (a quality), `inv` (0-2);
      `edge`: the beat `at` slot `slot` starts on; `split`: slot `slot` as
      two of the same chord;
    - `add` / `remove`: a slot at the end / slot `slot`; `bars`: the length
      that repeats, the slots spread again.

    A new track (the old one is never changed); a SongError when it cannot."""
    import random

    from .gen import build, chordtrack

    track = copy.deepcopy(track)
    slots = sorted(track["slots"], key=lambda x: x["at"])
    slot = int(a.get("slot") or 0)
    if op in ("preset", "roll"):
        menu = build.progressions(song)
        i = (
            random.Random(a["seed"]).randrange(len(menu))
            if op == "roll"
            else int(a.get("prog") or 0)
        )
        if not 0 <= i < len(menu):
            raise SongError("No such progression.")
        return copy.deepcopy(menu[i]["track"])
    if op == "set":
        if not 0 <= slot < len(slots):
            raise SongError("No such chord.")
        if a.get("deg") is not None:
            slots[slot]["deg"] = int(min(7, max(1, a["deg"])))
        if a.get("q"):
            slots[slot]["q"] = a["q"]
        if a.get("inv") is not None:
            slots[slot]["inv"] = int(min(2, max(0, a["inv"])))
        return {**track, "slots": slots}
    if op == "edge":  # a tile's left edge dragged: where it starts, in beats
        return chord_edge(track, slot, a["at"] if a.get("at") is not None else 0.0)
    if op == "split":
        return chord_split(track, slot)
    if op not in ("add", "remove", "bars"):
        raise SongError("No such chord edit.")
    degrees = [(x["deg"], x["q"], x["inv"]) for x in slots]
    bars = track["bars"]
    if op == "add":
        if len(degrees) >= min(limits.MAX_CHORD_SLOTS, bars * 4):
            raise SongError("No room for another chord: lengthen it first.")
        degrees.append(degrees[-1])
    elif op == "remove":
        if len(degrees) <= 1 or not 0 <= slot < len(degrees):
            raise SongError("A progression keeps one chord.")
        del degrees[slot]
    else:
        if a.get("bars") not in limits.CHORD_BAR_CHOICES:
            raise SongError("A progression is 1, 2, 4, 8 or 16 bars.")
        bars = int(a["bars"])
    return chordtrack.from_degrees(degrees, bars)


# --- cutting the timeline -----------------------------------------------------------


def _fx_notes(track: dict) -> list[str]:
    odd = [u["kind"] for u in track.get("fx") or () if u["kind"] not in LINEAR_FX]
    if not odd:
        return []
    kinds = ", ".join(dict.fromkeys(k.replace("_", " ") for k in odd))
    return [f"{track['name']}'s {kinds} restarts at the cut"]


class PatternLength(SongError):
    """A cut would leave a pattern a length no pattern can have."""

    def __init__(self, name: str, full: int, want: int):
        self.why = f"{name} is {full} bars and cannot be {want}"
        super().__init__(
            f"{name} is {full} bars: a cut that leaves {want} of them can't be "
            "a pattern (1, 2, 3, 4 or 8 bars). Cut on another bar."
        )


def _split_pass(
    song: dict, index: int, cuts: list[int], unique: bool, take_bars: dict | None
) -> list[str]:
    """Cut section `index` (it plays once) at each of `cuts` (bars into it,
    ascending): that many more sections in its place. What will not be
    exactly what it was, as notes for a toast."""
    sec = song["sections"][index]
    bars = int(sec["bars"])
    tracks = {t["id"]: t for t in song["tracks"]}
    notes: list[str] = []
    for k in cuts:
        for a in sec["audio"]:
            own = float((take_bars or {}).get(a["clip"], 0.0))
            if a["at_bar"] < k < a["at_bar"] + own - EPS:
                raise SongError(
                    f"The take {a['clip']} plays across bar {k + 1} of "
                    f"{sec['name']}: move it, or cut at its start."
                )
    made: dict[tuple, str] = {}
    forked: dict[str, str] = {}
    clock = sm.section_clock(song, sec)  # the section's own bars
    top = max(1, clock.samples(bars) - 1)

    def piece(pid: str, phase: int, n: int, room: int, right: bool) -> str:
        p = song["patterns"][pid]
        full = sm.pattern_bars(p)
        if phase == 0 and full <= room:
            if not (right and unique):
                return pid
            if pid not in forked:
                forked[pid] = sm.copy_pattern(song, pid)
            return forked[pid]
        want = min(full, n)
        if want not in sm.bar_choices(_per_bar(p)):
            raise PatternLength(p["name"], full, want)
        if (pid, phase, want) not in made:
            data = window(p, phase, want) | {"color": p["color"]}
            made[(pid, phase, want)] = sm.add_pattern(
                song, p["kind"], _named(p["name"], WINDOW_MARK), data
            )
        return made[(pid, phase, want)]

    from .gen import chordtrack

    held = chordtrack.track_of(song, sec)
    edges = [0, *cuts, bars]
    parts = []
    for nth, (a, end) in enumerate(zip(edges, edges[1:], strict=False)):
        length, right = end - a, nth > 0
        part = {
            key: copy.deepcopy(v)
            for key, v in sec.items()
            if key not in ("cells", "audio", "auto", "spans")
        }
        # where this part's first and last samples sit on the uncut lane:
        # `curves.lane_array` reads sample i at i / (n - 1)
        lo = min(clock.samples(a), top - 1) / top if a else 0.0
        hi = max(1, clock.samples(end) - 1) / top if end < bars else 1.0
        if sec.get("ramp"):  # each part ramps over its own share of the way
            beats = clock.bar_beats
            part["bpm"] = round(clock.tempo(a * beats), 3)
            part["ramp"] = round(clock.tempo(end * beats), 3)
        cells, spans, auto = {}, {}, {}
        for tid, pid in sec["cells"].items():
            cells[tid] = None
            p = song["patterns"].get(pid) if pid else None
            if p is None:
                continue
            at, n = sm.cell_span(sec, tid)
            s0, s1 = max(at, a), min(at + n, a + length)
            if s1 <= s0:
                continue  # the block lies in another part
            phase = (s0 - at) % sm.pattern_bars(p)
            cells[tid] = piece(pid, phase, s1 - s0, length, right)
            if (s0 - a, s1 - s0) != (0, length):
                spans[tid] = [s0 - a, s1 - s0]
            if right:
                track = tracks.get(tid, {})
                if held_across(p, phase):
                    notes.append(f"{track.get('name', 'a track')} holds a note across")
                q = song["patterns"][cells[tid]]
                first = min((int(c[1]) for c in q.get("cells") or ()), default=None)
                if first is not None and first in (q.get("slides") or ()):
                    # the note it glided from is on the other side now
                    notes.append(f"{track.get('name', 'a track')}'s slide starts again")
                if p["kind"] == "notes" and p.get("arp_mode", "off") != "off":
                    notes.append(f"{track.get('name', 'a track')}'s arp counts again")
                if track.get("curves") and phase:
                    notes.append(f"{track.get('name', 'a track')}'s curve restarts")
                notes += _fx_notes(track)
            lanes = [
                {"target": ln["target"], "points": cut_lane(ln["points"], lo, hi)}
                for ln in sm.cell_lanes(sec, tid)
            ]
            if lanes:
                auto[tid] = lanes
        part["cells"] = cells
        part["audio"] = [
            {**t, "at_bar": t["at_bar"] - a}
            for t in sec["audio"]
            if a <= t["at_bar"] < a + length
        ]
        # an audio track's lanes sit on its row: cut with it, kept where a
        # take of it lands (the schema drops them on a part without one)
        for tid in {t["track_id"] for t in part["audio"]}:
            if lanes := [
                {"target": ln["target"], "points": cut_lane(ln["points"], lo, hi)}
                for ln in sm.cell_lanes(sec, tid)
            ]:
                auto[tid] = lanes
        part["bars"] = length
        part["repeats"] = 1
        if auto:
            part["auto"] = auto
            if right:
                notes.append("a lane holds its value at the cut for what rings on")
        if spans:
            part["spans"] = spans
        if right:
            part["id"] = sm._fresh_id(song)
            part["name"] = _named(sec["name"], sm.PRIME * nth)
            # the harmony reads on from where it was: a later part's chord
            # track is the one in force, from its own phase
            if a % int(held["bars"]):
                part["chords"] = rotate_chords(held, a)
        parts.append(part)
    if song.get("drum_humanize"):
        notes.append("the beat's humanise is drawn again")
    song["sections"][index : index + 1] = parts
    return list(dict.fromkeys(notes))


def _cut(
    song: dict,
    bar: int,
    more: tuple[int, ...] = (),
    *,
    unique: bool = False,
    take_bars: dict | None = None,
) -> tuple[int, list[str]] | None:
    """`cut`, in place. `more`: further cuts of the same pass, as bars from
    `bar` (+4, -4) — None when one of them falls outside the pass."""
    i, j, k = locate(song, bar)
    if i == len(song["sections"]) or (j == 0 and k == 0):
        return i, []
    sec = song["sections"][i]
    reps = int(sec["repeats"])
    if k == 0:  # between two passes: the repeats, divided
        _room(song, 1)
        rest = _clone(song, sec, sm._primed(sec["name"]))
        sec["repeats"], rest["repeats"] = j, reps - j
        song["sections"].insert(i + 1, rest)
        return i + 1, []
    cuts = sorted({k, *(k + d for d in more)})
    if not all(0 < c < sec["bars"] for c in cuts):
        return None
    _room(song, len(cuts) + (j > 0) + (j < reps - 1))
    at, mid = i, sec
    if j > 0:  # the passes before it stay the section; the cut pass is a copy
        mid = _clone(song, sec)
        song["sections"].insert(i + 1, mid)
        at = i + 1
    if j < reps - 1:
        after = _clone(song, sec, _named(sec["name"], "…"))
        after["repeats"] = reps - j - 1
        song["sections"].insert(at + 1, after)
    if j > 0:
        sec["repeats"] = j
    mid["repeats"] = 1
    notes = _split_pass(song, at, cuts, unique, take_bars)
    also = [f"also cut at bar {bar - k + c + 1}" for c in cuts if c != k]
    return at + 1 + cuts.index(k), [*also, *notes]


def cut(
    song: dict, bar: int, *, unique: bool = False, take_bars: dict | None = None
) -> tuple[int, list[str]]:
    """Make timeline bar `bar` a section boundary. (The index of the
    section that now starts there, notes on what will not sound exactly as
    it did.) A boundary that exists is no edit. A bar inside a section
    that repeats divides its repeats first: the passes before the cut's
    stay one section, the cut pass becomes two, the passes after it one —
    the same bars in the same order, so nothing on the timeline moves.
    `unique`: the later parts play copies of the patterns they would share.
    `take_bars`: {clip: its length in bars}, to refuse a take in two.

    A pattern is 1, 2, 3, 4 or 8 bars, so a cut that would leave 5, 6 or 7
    bars of an 8-bar pattern cannot stand alone: the pass is then ALSO cut
    four bars after the bar, or before it, or both — whichever leaves
    every part a length a pattern can have — and the first note says so
    ("also cut at bar N (…)"). Refused only when none does.
    A refusal leaves the song exactly as it was."""
    first: PatternLength | None = None
    for more in ((), (4,), (-4,), (-4, 4)):
        work = copy.deepcopy(song)
        try:
            out = _cut(work, bar, more, unique=unique, take_bars=take_bars)
        except PatternLength as err:
            first = first or err
            continue
        if out is None:
            continue
        index, notes = out
        if more and first is not None:
            notes[0] = f"{notes[0]} ({first.why})"
        song.clear()
        song.update(work)
        return index, notes
    assert first is not None  # the plain cut is tried first and never None
    raise first


def split(
    song: dict,
    section_id,
    bar: int,
    *,
    unique: bool = False,
    take_bars: dict | None = None,
) -> tuple[str, list[str]]:
    """Cut a section at timeline bar `bar`, which must lie inside it (not
    on its first bar: that is a boundary already). (The id of the section
    that now starts at the bar, notes.)"""
    i = sm.section_index(song, section_id)
    if i is None:
        raise SongError("No such section.")
    at = starts(song)
    sec = song["sections"][i]
    if not at[i] < int(bar) < at[i + 1]:
        raise SongError(
            f"Pick a bar inside {sec['name']} (bars {at[i] + 2}–{at[i + 1]}) to cut at."
        )
    index, notes = cut(song, bar, unique=unique, take_bars=take_bars)
    return song["sections"][index]["id"], notes


def unroll(song: dict, section_id) -> list[str]:
    """A section that plays N times becomes N sections that play once:
    the same patterns by reference, its lanes, spans, takes, chords and
    energy on each. The ids, in order (the first is the section's own).

    N sections rather than one of N x the bars: a section with a lane, a
    take or a trimmed block is one pass of its whole length — one long
    section would stretch the lane over N passes, need N copies of each
    take and of each block (the model holds one block a cell), and would
    have to fit `MAX_SECTION_BARS` and `MAX_SECTION_SECONDS`. N sections
    meet only `MAX_SECTIONS`, and render from the cycle already cached."""
    i = sm.section_index(song, section_id)
    if i is None:
        raise SongError("No such section.")
    sec = song["sections"][i]
    reps = int(sec["repeats"])
    if reps < 2:
        raise SongError(f"{sec['name']} plays once: there is nothing to unroll.")
    _room(song, reps - 1)
    sec["repeats"] = 1
    ids = [sec["id"]]
    for n in range(2, reps + 1):
        dup = _clone(song, sec, _named(sec["name"], str(n)))
        song["sections"].insert(i + n - 1, dup)  # in the song: its id is taken
        ids.append(dup["id"])
    return ids


def insert_bars(
    song: dict, bar: int, bars: int, *, take_bars: dict | None = None
) -> tuple[str, list[str]]:
    """`bars` empty bars at timeline bar `bar`: a new section with no
    block and no take — silence on every track, into which the section
    before it rings out. A bar inside a section cuts it first (`cut`).
    Everything from `bar` on plays `bars` later; whole stems stay at their
    bar. (The new section's id, the cut's notes.)"""
    n = min(limits.MAX_SECTION_BARS, max(1, int(bars)))
    locate(song, bar)  # a bar past the song's end is refused
    _room(song, 1)
    index, notes = cut(song, bar, take_bars=take_bars)
    sid = sm.add_section(song, f"{n} bar{'' if n == 1 else 's'}", n, index)
    return sid, notes


def delete_bars(
    song: dict, lo: int, hi: int, *, take_bars: dict | None = None
) -> tuple[int, list[str]]:
    """Take timeline bars [lo, hi) out of the song: the sections between
    the two bars go, a section either bar falls inside is cut first, and
    what followed plays that much earlier (whole stems stay at their bar).
    Patterns stay in the pool, but a window made only to be deleted does
    not. (How many bars went, the cuts' notes.)"""
    lo, hi = int(lo), int(hi)
    if not 0 <= lo < hi <= starts(song)[-1]:
        raise SongError("Pick the bars to delete.")
    pool = set(song["patterns"])
    notes: list[str] = []
    b, more = cut(song, hi, take_bars=take_bars)
    notes += more
    a, more = cut(song, lo, take_bars=take_bars)
    notes += more
    b = locate(song, hi)[0]
    gone = sum(s["bars"] * s["repeats"] for s in song["sections"][a:b])
    del song["sections"][a:b]
    used = {p for s in song["sections"] for p in s["cells"].values() if p}
    for pid in set(song["patterns"]) - pool - used:
        del song["patterns"][pid]
    return gone, list(dict.fromkeys(notes))


# --- sections ---------------------------------------------------------------------


def duplicate(song: dict, section_id, *, unique: bool = False) -> str:
    """A copy of a section right after it. `unique` ("duplicate as a
    variation"): the copy plays copies of the patterns — one a pattern, so
    two tracks that shared one still do — and an edit there changes
    nothing else. Without it this is `sm.duplicate_section`: every pattern
    by reference."""
    new = sm.duplicate_section(song, section_id)
    if unique:
        sec = sm._section(song, new)
        fork: dict[str, str] = {}
        for tid, pid in sec["cells"].items():
            if pid:
                if pid not in fork:
                    fork[pid] = sm.copy_pattern(song, pid)
                sec["cells"][tid] = fork[pid]
    return new


def twins(song: dict) -> dict[str, list[str]]:
    """{section id: the names of the other sections playing exactly its
    patterns} for every section that shares ALL its blocks with another —
    what "duplicate" leaves behind, and what the head says so it is seen."""
    sig: dict[tuple, list[dict]] = {}
    for sec in song["sections"]:
        cells = tuple(sorted((t, p) for t, p in sec["cells"].items() if p))
        if cells:
            sig.setdefault(cells, []).append(sec)
    return {
        s["id"]: [o["name"] for o in group if o is not s]
        for group in sig.values()
        if len(group) > 1
        for s in group
    }


def set_energy(song: dict, section_id, value) -> float | None:
    """A section's energy (0..1), or None to take it away."""
    sec = sm._section(song, section_id)
    if value is None:
        sec.pop("energy", None)
        return None
    sec["energy"] = round(min(1.0, max(0.0, float(value))), 2)
    return sec["energy"]


# --- transitions from the energy lane ------------------------------------------------

RISE = 0.15  # an energy step this large is a build (or a drop away)
# what a boundary's menu offers, in its order: (kind, direction, label)
BOUNDARY_KINDS = (
    ("riser", "up", "riser ↗ into it"),
    ("sweep", "up", "sweep ↗ into it"),
    ("swell", "up", "swell ↗ into it"),
    ("impact", "down", "impact ↘ on its first bar"),
    ("subdrop", "down", "sub drop ↘ on its first bar"),
    ("sweep", "down", "downlifter ↘ off its first bar"),
    ("riser", "down", "falling riser ↘ off its first bar"),
    ("bed", "down", "bed under it"),
)


def trend(before: dict, after: dict) -> str:
    """ "rising" / "falling" / "level": how the energy lane steps from one
    section to the next (level when either is undrawn)."""
    e0, e1 = before.get("energy"), after.get("energy")
    step = (e1 - e0) if e0 is not None and e1 is not None else 0.0
    return "rising" if step >= RISE else "falling" if step <= -RISE else "level"


def boundary_moves(song: dict, index: int) -> dict:
    """What a transition on the boundary before section `index` (>= 1)
    defaults to, from the two sections' energy: rising — a riser into it
    and an impact on its first bar; falling — a downlifter off its first
    bar; level, or no energy drawn — an impact. `{"trend", "default":
    [...], "all": [...]}`, each move `{"kind", "direction", "section_id",
    "bars", "label"}` ready for `POST /song/section/{id}/transition`: one
    that leads in is placed at the END of the section before, one that
    comes off the bar line at the START of this one."""
    from .. import transitions

    secs = song["sections"]
    if not 1 <= index < len(secs):
        raise SongError("No such boundary.")
    before, after = secs[index - 1], secs[index]
    way = trend(before, after)

    def move(kind: str, direction: str, label: str) -> dict:
        lead = transitions.leads_in(kind, direction)
        sec = before if lead else after
        return {
            "kind": kind,
            "direction": direction,
            "section_id": sec["id"],
            "bars": max(b for b in (1, 2, 4, 8) if b <= max(1, min(4, sec["bars"]))),
            "label": label,
            "leads": lead,
        }

    every = [move(*row) for row in BOUNDARY_KINDS]
    by = {(m["kind"], m["direction"]): m for m in every}
    default = {
        "rising": [by["riser", "up"], by["impact", "down"]],
        "falling": [by["sweep", "down"]],
        "level": [by["impact", "down"]],
    }[way]
    return {"trend": way, "default": default, "all": every}


# --- arrange by energy ---------------------------------------------------------------

# How full a section plays for its drawn energy: (up to, the beat's
# variant — `gen.drums.thin`'s names, "all" the whole beat, None no drums
# —, the roles that play). `build.arrange` reads a row exactly as it reads
# a row of `styles.GENERIC_SHAPE`: at 0.9 and above everything plays.
ENERGY_ROWS = (
    (0.2, None, ("chords", "pad")),
    (0.4, "hats", ("chords", "pad", "arp")),
    (0.6, "nokick", ("bass", "chords", "pad", "arp")),
    (0.9, "all", ("bass", "chords", "pad", "arp", "vocal")),
    (2.0, "all", ("bass", "chords", "pad", "arp", "lead", "vocal", "fx")),
)


def energy_row(sec: dict, before: float | None, after: float | None) -> dict:
    """A section as a row of an arrangement shape (`gen/styles.py`): what
    plays for its energy, and a filter move where the energy lane steps —
    opening when the next section is fuller, shut down when this one came
    down from a fuller one."""
    e = float(sec["energy"])
    _top, drums, roles = next(r for r in ENERGY_ROWS if e < r[0])
    moves: list[tuple] = []
    if after is not None and after - e >= RISE and e < 0.9:
        moves.append(("sweep", 0.3, 0.9))
    elif before is not None and before - e >= RISE:
        moves.append(("sweep", 0.7, 0.25))
    return {
        "name": sec["name"],
        "bars": sec["bars"],
        "repeats": sec["repeats"],
        "energy": e,
        "drums": drums,
        "roles": roles,
        "moves": tuple(moves),
    }


def arrange_by_energy(song: dict, seed: int = 0, only=None) -> list[str]:
    """Make every section that has an energy (or the one `only` names) play
    as full as it is drawn — by muting, thinning and filtering the FULLEST
    section's parts, never by writing music: `build.arrange` with one row
    a section (`energy_row`), the loop -> song machinery. The fullest
    section is the source and is left as it is; a section with no energy
    is not touched. Each arranged section keeps its id, name, colour,
    length, repeats, takes and own chords, and LOSES its own blocks, lanes
    and spans (they are the source's now). The names of the sections
    arranged."""
    from .gen import build

    secs = song["sections"]
    drawn = [s for s in secs if s.get("energy") is not None]
    if not drawn:
        raise SongError("Draw the energy lane first: no section has an energy yet.")
    src = max(
        drawn, key=lambda s: (s["energy"], sum(bool(p) for p in s["cells"].values()))
    )
    if not any(src["cells"].values()):
        raise SongError(f"{src['name']} is the fullest section and it is empty.")
    targets = [
        s
        for s in drawn
        if s is not src and (only is None or s["id"] == only) and s["energy"] < 0.999
    ]
    if only is not None and not targets:
        raise SongError("That section is the fullest: the others are made from it.")
    if not targets:
        raise SongError("Every section is as full as the fullest.")
    order = [s["id"] for s in secs]
    energy = {s["id"]: s.get("energy") for s in secs}

    def near(sid: str, d: int) -> float | None:
        i = order.index(sid) + d
        return energy[order[i]] if 0 <= i < len(order) else None

    shape = tuple(energy_row(s, near(s["id"], -1), near(s["id"], 1)) for s in targets)
    pool = set(song["patterns"])
    old = {s["id"]: s for s in secs}
    at = secs.index(src)
    build.arrange(song, src["id"], seed, shape)
    made = song["sections"][at : at + len(shape)]
    src_beat = next(
        (
            p
            for t, p in src["cells"].items()
            if p and song["patterns"][p]["kind"] == "drums"
        ),
        None,
    )
    for new, was in zip(made, targets, strict=True):
        for tid, pid in list(new["cells"].items()):
            if pid and sm.pattern_bars(song["patterns"][pid]) > was["bars"]:
                # the timeline does not grow: the beat with a fill in bar 8
                # gives way to the beat itself, a longer part sits out
                p = song["patterns"][pid]
                fits = (
                    src_beat
                    and sm.pattern_bars(song["patterns"][src_beat]) <= was["bars"]
                )
                new["cells"][tid] = src_beat if p["kind"] == "drums" and fits else None
        live = {t for t, p in new["cells"].items() if p}
        new["auto"] = {t: v for t, v in (new.get("auto") or {}).items() if t in live}
        new["spans"] = {}
        new.update(
            {
                "id": was["id"],
                "color": was["color"],
                "bars": was["bars"],
                "audio": was["audio"],
            }
        )
        if was.get("chords"):
            new["chords"] = was["chords"]
        old[was["id"]] = new
    song["sections"] = [old[sid] for sid in order]
    _dedupe(song, pool)
    return [s["name"] for s in targets]


def _dedupe(song: dict, before: set) -> None:
    """Patterns an edit added that are, note for note, ones the pool held
    already are those ones (so arranging again does not fill the pool), and
    an added pattern nothing plays is dropped."""
    seen = {
        (song["patterns"][p]["name"], sm.content_hash(song["patterns"][p])): p
        for p in song["patterns"]
        if p in before
    }
    for pid in [p for p in song["patterns"] if p not in before]:
        p = song["patterns"][pid]
        same = seen.get((p["name"], sm.content_hash(p)))
        if same is None:
            seen[(p["name"], sm.content_hash(p))] = pid
            continue
        for sec in song["sections"]:
            for tid, got in sec["cells"].items():
                if got == pid:
                    sec["cells"][tid] = same
        del song["patterns"][pid]
    used = {p for s in song["sections"] for p in s["cells"].values() if p}
    for pid in [p for p in song["patterns"] if p not in before and p not in used]:
        del song["patterns"][pid]


# --- a range of cells ---------------------------------------------------------------


def parse_cells(song: dict, raw) -> list[tuple[str, str]]:
    """`"sid:tid,sid:tid"` -> [(section id, track id)] in the order
    given, each once. More than `MAX_RANGE_CELLS` is refused WHOLE (half a
    range edit is worse than none), and so is a cell the song does not
    have."""
    text = str(raw or "")
    if len(text) > limits.MAX_RANGE_CELLS * 80:
        raise SongError(f"A range holds at most {limits.MAX_RANGE_CELLS} cells.")
    items = [x for x in text.split(",") if x.strip()]
    if not items:
        raise SongError("Select some cells first.")
    if len(items) > limits.MAX_RANGE_CELLS:
        raise SongError(f"A range holds at most {limits.MAX_RANGE_CELLS} cells.")
    sids = {s["id"] for s in song["sections"]}
    tids = {t["id"] for t in song["tracks"]}
    out: dict[tuple[str, str], None] = {}
    for item in items:
        sid, _colon, tid = item.strip().partition(":")
        if sid not in sids or tid not in tids:
            raise SongError("That range is no longer on the song.")
        out[(sid, tid)] = None
    return list(out)


def _state(song: dict, sid: str, tid: str) -> dict:
    """What a cell holds, to put down elsewhere."""
    sec = sm._section(song, sid)
    kind = sm._track(song, tid)["kind"]
    if kind == "audio":
        takes = [copy.deepcopy(a) for a in sec["audio"] if a["track_id"] == tid]
        return {"kind": kind, "takes": takes}
    return {
        "kind": kind,
        "pid": sec["cells"].get(tid),
        "span": list((sec.get("spans") or {}).get(tid) or ()) or None,
        "lanes": copy.deepcopy(sm.cell_lanes(sec, tid)),
    }


def _wipe(song: dict, sid: str, tid: str) -> bool:
    sec = sm._section(song, sid)
    if sm._track(song, tid)["kind"] == "audio":
        n = len(sec["audio"])
        sec["audio"] = [a for a in sec["audio"] if a["track_id"] != tid]
        return len(sec["audio"]) != n
    had = sec["cells"].get(tid) is not None
    sm.set_cell(song, sid, tid, None)
    return had


def clear_cells(song: dict, cells: list[tuple[str, str]]) -> int:
    """Empty every cell of the range (a block, its lanes and span; an
    audio cell's takes). How many held something."""
    return sum(_wipe(song, sid, tid) for sid, tid in cells)


def paste_cells(
    song: dict,
    cells: list[tuple[str, str]],
    to_section,
    to_track,
    *,
    move: bool = False,
    landed: list | None = None,
) -> tuple[int, int]:
    """Put the range down again with its top-left cell on (`to_section`,
    `to_track`): each cell lands the same number of sections and tracks
    away. By reference — the destination plays the same pool patterns — and
    exactly: an empty cell of the range empties the cell it lands on. A
    block's span goes with it, held to where it lands. Lanes follow
    `sm.move_cell`'s rule: a MOVE along a track carries them, a copy never
    does. A track of another kind refuses the whole edit; a destination
    past the last section adds sections (the source's length and name) as
    far as `MAX_SECTIONS` allows. `move`: the source cells are emptied
    first. (Cells written, sections added.) `landed`, when given, is
    filled with the (section id, track id) of each cell written: where
    the range is now."""
    order = [s["id"] for s in song["sections"]]
    rows = [t["id"] for t in song["tracks"]]
    kinds = {t["id"]: t["kind"] for t in song["tracks"]}
    if to_section not in order or to_track not in rows:
        raise SongError("Pick where the range goes.")
    ds = order.index(to_section) - min(order.index(s) for s, _t in cells)
    dt = rows.index(to_track) - min(rows.index(t) for _s, t in cells)
    if not ds and not dt:
        return 0, 0
    plan = []
    for sid, tid in cells:
        si, ti = order.index(sid) + ds, rows.index(tid) + dt
        if si < 0 or not 0 <= ti < len(rows):
            raise SongError("The range does not fit there.")
        if kinds[rows[ti]] != kinds[tid]:
            src, dst = sm._track(song, tid), sm._track(song, rows[ti])
            raise SongError(
                f"{src['name']}'s cells can't land on {dst['name']} "
                f"({kinds[rows[ti]]}, not {kinds[tid]}): nothing was moved."
            )
        plan.append((sid, tid, si, rows[ti]))
    added = max(0, max(p[2] for p in plan) + 1 - len(order))
    _room(song, added)
    held = {(sid, tid): _state(song, sid, tid) for sid, tid, _si, _t in plan}
    for sid, _tid, si, _t in sorted(plan, key=lambda p: p[2]):
        while si >= len(song["sections"]):
            src = sm._section(song, sid)
            sm.add_section(song, sm._primed(src["name"]), src["bars"])
    order = [s["id"] for s in song["sections"]]
    if move:
        for sid, tid, _si, _t in plan:
            _wipe(song, sid, tid)
    for sid, tid, si, to_tid in plan:
        got, dst = held[(sid, tid)], order[si]
        if landed is not None:
            landed.append((dst, to_tid))
        _wipe(song, dst, to_tid)
        sec = sm._section(song, dst)
        if got["kind"] == "audio":
            for a in got["takes"]:
                sm.place_take(song, dst, to_tid, a["clip"], a["at_bar"], a["gain"])
            continue
        if got["pid"] is None:
            continue
        sm.set_cell(song, dst, to_tid, got["pid"])
        if got["span"]:
            sm.set_span(song, dst, to_tid, *got["span"])
        if move and to_tid == tid and got["lanes"]:
            sec.setdefault("auto", {})[to_tid] = got["lanes"]
    return len(plan), added


def duplicate_cells(
    song: dict, cells: list[tuple[str, str]], landed: list | None = None
) -> tuple[int, int]:
    """The range again, straight after itself on the same tracks (adding
    sections as needed): `paste_cells` one range-width to the right."""
    order = [s["id"] for s in song["sections"]]
    rows = [t["id"] for t in song["tracks"]]
    idx = [order.index(s) for s, _t in cells]
    to = max(idx) + 1
    _room(song, max(0, to + (max(idx) - min(idx)) + 1 - len(order)))
    while to >= len(song["sections"]):
        src = sm._section(song, order[min(idx)])
        sm.add_section(song, sm._primed(src["name"]), src["bars"])
    top = min(rows.index(t) for _s, t in cells)
    return paste_cells(
        song, cells, song["sections"][to]["id"], rows[top], landed=landed
    )


def unique_cells(
    song: dict, cells: list[tuple[str, str]], seed: int | None = None
) -> int:
    """Give the range patterns of its own: every pattern it plays that is
    also played OUTSIDE it is copied once, and the range's cells play the
    copy — so the range still shares within itself and no longer with the
    rest of the song. `seed`: each copy is also varied (`sm.variation`'s
    mutation), and a pattern only the range plays is varied in place. How
    many patterns were copied (or varied)."""
    inside = set(cells)
    by_pid: dict[str, list[tuple[str, str]]] = {}
    for sid, tid in cells:
        pid = sm._section(song, sid)["cells"].get(tid)
        if pid:
            by_pid.setdefault(pid, []).append((sid, tid))
    done = 0
    for n, (pid, where) in enumerate(by_pid.items()):
        outside = any(
            s["cells"].get(t["id"]) == pid and (s["id"], t["id"]) not in inside
            for s in song["sections"]
            for t in song["tracks"]
        )
        if not outside and seed is None:
            continue
        sid, tid = where[0]
        if seed is None:
            new = sm.copy_pattern(song, pid)
        else:
            new = sm.variation(song, sid, tid, int(seed) + n)
            if not outside:  # nothing else played it: the copy replaces it
                keep = {k: song["patterns"][pid][k] for k in ("id", "name")}
                song["patterns"][pid] = {**song["patterns"].pop(new), **keep}
                new = pid
        for s, t in where:
            sm._section(song, s)["cells"][t] = new
        done += 1
    if not done:
        raise SongError("Nothing in the range is shared with the rest of the song.")
    return done


def fill_cells(
    song: dict, cells: list[tuple[str, str]], kind: str, seed: int, params=None
) -> int:
    """A generator onto the range: `build.write` for every (track,
    section) of it, each track one pattern shared by its cells of the
    range. Refused whole for a track the generator does not fit. How many
    cells were written."""
    from .gen import build

    by_track: dict[str, list[str]] = {}
    for sid, tid in cells:
        by_track.setdefault(tid, []).append(sid)
    wrote = 0
    for n, (tid, sids) in enumerate(by_track.items()):
        first = build.write(song, tid, kind, int(seed) + n, params, sids[0])
        for sid in sids[1:]:
            if first and sm._track(song, tid)["kind"] == "synth":
                sm.set_cell(song, sid, tid, first[0])
            else:
                build.write(song, tid, kind, int(seed) + n, params, sid)
        wrote += len(sids)
    return wrote


def again_cells(song: dict, cells: list[tuple[str, str]], seed: int) -> int:
    """Roll the range's generated patterns again (`build`'s own rewrite,
    from each pattern's `gen` with a new seed). One shared with the rest of
    the song is copied first, so only the range changes. How many rolled;
    refused when nothing in the range was generated."""
    from .gen import build

    inside = set(cells)
    done = 0
    seen: dict[str, str] = {}
    for sid, tid in cells:
        sec = sm._section(song, sid)
        pid = sec["cells"].get(tid)
        if not pid or not song["patterns"][pid].get("gen"):
            continue
        if pid not in seen:
            outside = any(
                s["cells"].get(t["id"]) == pid and (s["id"], t["id"]) not in inside
                for s in song["sections"]
                for t in song["tracks"]
            )
            new = sm.copy_pattern(song, pid) if outside else pid
            part = (song["patterns"][new]["gen"].get("params") or {}).get("part")
            salt = part or song["patterns"][new]["gen"]["kind"]
            if not build._rewrite(
                song, song["patterns"][new], build.part_seed(seed, salt)
            ):
                raise SongError(
                    f"{song['patterns'][pid]['name']} can't be rolled again."
                )
            seen[pid] = new
            done += 1
        sec["cells"][tid] = seen[pid]
    if not done:
        raise SongError("Nothing in the range was generated: there is nothing to roll.")
    return done
