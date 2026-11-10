"""A jam written back as sections (studio `docs/song.md`, "The jam").

The launcher (`static/jam.js`) plays the cell loops the drawer's solo
already renders (`GET /song/cell/{section}/{track}.wav`), a track at a
time, each launched on a bar line, and keeps a log of what it started:

    {"bar": 4, "track": "t1", "target": "s2"}   t1 plays its cell of s2 from bar 4
    {"bar": 8, "track": "t1"}                   t1 stops at bar 8
    {"bar": 8, "scene": "s3"}                   every track plays its cell of s3
                                                (a track with none there stops)
    {"bar": 12}                                 everything stops

`commit` turns that log into sections appended to the song: one where the
set of sounding cells changed, as long as the bars to the next change.
Pure functions on the song dict, as `songops` is: no StudioState, no I/O;
`routes/song_jam.py` runs `commit` inside one `_commit`, so a capture is
ONE undo step. A refusal is a `SongError` fit for a toast and leaves the
song as it was.

**What a captured section plays** is what the launcher looped, bar for
bar. A launched cell loops its section's cycle (`sm.cycle_bars`) from its
first bar, so a track launched `n` bars before a change is `n` bars into
its loop there, and the new section must start it from that bar:

- a pattern that starts on its own first bar and fits is kept **by
  reference** (the pool gains nothing);
- so is one that starts on its first bar and is cut short by the part
  (the section plays its first bars and ends: a crop needs no copy);
- any other plays a `songops.window` of it (a new pool pattern, "… ↻":
  the same notes from that bar) — a rotation;
- a part of 5, 6 or 7 bars cannot hold a window of an 8-bar pattern (a
  pattern is 1, 2, 3, 4 or 8 bars): it is made as two parts, 4 bars and
  the rest;
- where every sounding loop starts on its first bar and the part is a
  whole number of them, the section is one loop long and **repeats**;
- a part longer than a section may be (`MAX_SECTION_BARS`,
  `MAX_SECTION_SECONDS`) is several sections.

**Lanes and trimmed blocks** (`auto`, `spans`) are a section pass's, so
they are carried only where the new section IS a pass of the source: the
same length, from its first bar. Elsewhere the block plays untrimmed and
without the lane — the launcher played both, so that part of the capture
is not what was heard (the route's toast says so). The same holds where
a pattern's length does not divide its section's (a 3-bar pattern in a
4-bar section restarts at the pass; a window of it does not).

**Scenes from elsewhere** (`scenes`, Perform's "Scene → Song"): a log
may also launch sections that are
not in the song — each scene of Perform's launcher made into a section
the song has not been given (`song_bridge.add_scene_section`, its
patterns already in the pool, its tracks already in the song). They are
read exactly as the song's own; a captured part plays them by reference
(and takes the source's own tempo and meter: the launcher plays every
scene on one clock, so a scene's section keeps it), and they never join
the song themselves.

**What a capture never is**: the launcher's cuts. A switch cuts the old
loop's tail at the bar; a section boundary lets it ring. A jam ignores
the mix's mute and solo (the cell render does); the captured sections
obey them. Takes and whole stems are not launched and not captured.
"""

from __future__ import annotations

import math

from . import limits
from . import song_model as sm
from . import songops as so
from .song_model import SongError

NAME = "Jam"


def _ids(song: dict, scenes: dict | None = None) -> tuple[set, dict]:
    tracks = {t["id"] for t in song["tracks"] if t["kind"] in sm.CELL_KIND}
    sections = {s["id"]: s for s in song["sections"]} | dict(scenes or {})
    return tracks, sections


def _filled(song: dict, sec: dict, tid: str) -> bool:
    pid = sec["cells"].get(tid)
    return pid is not None and pid in song["patterns"]


def timeline(
    song: dict, events: list[dict], bars: int, scenes: dict | None = None
) -> list[tuple[int, int, dict]]:
    """The log as (start bar, end bar, {track id: (section id, launch
    bar)}) parts, in order, from the first launch to `bars`: a part ends
    where the set of sounding cells changes. Silence before the first
    launch and after the last stop is not a part; silence between two is.

    Events are read in bar order (ties in the order given); one at or past
    `bars` has not happened yet. A track or section the song does not have
    refuses the whole log — an id a jam started with can be undone under
    it. A launch of an empty cell is a stop: it would have been silence.
    `scenes`: sections outside the song the log may launch too (Perform's
    scenes, the module's "Scenes from elsewhere")."""
    bars = int(bars)
    tracks, sections = _ids(song, scenes)
    order = [t["id"] for t in song["tracks"] if t["id"] in tracks]
    if len(events) > limits.MAX_JAM_EVENTS:
        raise SongError(
            f"A jam holds at most {limits.MAX_JAM_EVENTS} launches: capture sooner."
        )
    state: dict[str, tuple[str, int]] = {}
    marks: list[tuple[int, dict]] = []

    def put(tid: str, sid, bar: int) -> None:
        if sid is not None and _filled(song, sections[sid], tid):
            state[tid] = (sid, bar)
        else:
            state.pop(tid, None)

    for ev in sorted(events, key=lambda e: int(e["bar"])):
        bar = int(ev["bar"])
        if bar >= bars:
            break
        tid, scene, target = ev.get("track"), ev.get("scene"), ev.get("target")
        to = target if tid is not None else scene
        if to is not None and to not in sections:
            raise SongError("The jam launched a section the song no longer has.")
        if tid is None:
            for each in order:
                put(each, to, bar)
        elif tid not in tracks:
            raise SongError("The jam launched a track the song no longer has.")
        else:
            put(tid, to, bar)
        if marks and marks[-1][0] == bar:
            marks[-1] = (bar, dict(state))
        else:
            marks.append((bar, dict(state)))
    parts: list[tuple[int, int, dict]] = []
    ends = [bar for bar, _state in marks[1:]] + [bars]
    for (a, now), b in zip(marks, ends[: len(marks)], strict=True):
        if parts and parts[-1][2] == now:
            parts[-1] = (parts[-1][0], b, now)  # an event that changed nothing
        else:
            parts.append((a, b, now))
    while parts and not parts[0][2]:
        parts.pop(0)
    while parts and not parts[-1][2]:
        parts.pop()
    return parts


def _cap(song: dict) -> int:
    """The longest part one section holds: `MAX_SECTION_BARS` and
    `MAX_SECTION_SECONDS` at the song's tempo, in whole fours."""
    bar = 4.0 * 60.0 / float(song["bpm"])
    most = min(limits.MAX_SECTION_BARS, int(limits.MAX_SECTION_SECONDS / bar))
    return max(4, most - most % 4)


class _Writer:
    """One capture: the sections it appends, the windows it has made."""

    def __init__(self, song: dict, scenes: dict | None = None):
        self.song = song
        self.sections = _ids(song, scenes)[1]
        self.made: dict[tuple, str] = {}
        self.out: list[str] = []
        self.loose = False  # a lane or a trimmed block was left behind
        self.cap = _cap(song)

    def _phase(self, sid: str, launched: int, a: int) -> int:
        """Bars into the source's loop at bar `a`."""
        return (a - launched) % sm.cycle_bars(self.song, self.sections[sid])

    def _want(self, tid: str, sid: str, phase: int, length: int):
        """(pattern id, its phase, the bars to take) for one track of a part."""
        sec = self.sections[sid]
        pid = sec["cells"][tid]
        p = self.song["patterns"][pid]
        at, _n = sm.cell_span(sec, tid)
        full = sm.pattern_bars(p)
        return pid, (phase - at) % full, min(full, length)

    def _piece(self, pid: str, phase: int, want: int) -> str:
        """The pattern a part plays: `pid` itself from its first bar — a
        crop included, since `want` is less than the pattern only where it
        is the whole section, which then plays its first `want` bars and
        ends (`sm.cycle_bars`: a section its patterns do not divide is one
        cycle) — else a window of it (a rotation: a new "… ↻" pattern).
        A crop is never a window: that would leave a short jam's pool a
        "↻" pattern a part, each used once."""
        p = self.song["patterns"][pid]
        if phase == 0:
            return pid
        if (pid, phase, want) not in self.made:
            data = so.window(p, phase, want) | {"color": p["color"]}
            self.made[(pid, phase, want)] = sm.add_pattern(
                self.song, p["kind"], so._named(p["name"], so.WINDOW_MARK), data
            )
        return self.made[(pid, phase, want)]

    def part(self, a: int, b: int, now: dict) -> None:
        song, length = self.song, b - a
        if length <= 0:
            return
        if not now:  # a rest between two launches
            n = min(length, self.cap)
            self._add(f"{NAME} rest", n, 1, {}, None)
            return self.part(a + n, b, now)
        phases = {t: self._phase(s, at, a) for t, (s, at) in now.items()}
        # every loop on its first bar, and a whole number of them: repeats
        if not any(phases.values()):
            loop = math.lcm(
                *(sm.cycle_bars(song, self.sections[s]) for s, _b in now.values())
            )
            if loop <= self.cap and length >= loop and length % loop == 0:
                reps = min(length // loop, limits.MAX_SECTION_REPEATS)
                self._add(None, loop, reps, now, phases)
                return self.part(a + loop * reps, b, now)
        if length > self.cap:
            self.part(a, a + self.cap, now)
            return self.part(a + self.cap, b, now)
        wants = {t: self._want(t, s, phases[t], length) for t, (s, _b) in now.items()}
        for pid, _phase, want in wants.values():
            p = song["patterns"][pid]
            if want not in sm.bar_choices(so._per_bar(p)):  # 5-7 bars of 8
                self.part(a, a + 4, now)
                return self.part(a + 4, b, now)
        self._add(None, length, 1, now, phases)

    def _add(self, name, bars: int, reps: int, now: dict, phases: dict | None) -> None:
        song = self.song
        sources = {s for s, _b in now.values()}
        one = self.sections[next(iter(sources))] if len(sources) == 1 else None
        n = len(self.out) + 1
        sid = sm.add_section(
            song, name or (one["name"] if one else f"{NAME} {n}"), bars
        )
        sec = song["sections"][-1]
        sec["repeats"] = reps
        if one is not None:
            sec["color"] = one["color"]
            # a scene's own clock (the song's sections keep none in a jam)
            sec |= {k: one[k] for k in ("meter", "bpm") if one.get(k)}
        cells: dict = {
            t["id"]: None for t in song["tracks"] if t["kind"] in sm.CELL_KIND
        }
        auto, spans = {}, {}
        for tid, (src_id, _b) in now.items():
            src = self.sections[src_id]
            whole = phases[tid] == 0 and int(src["bars"]) == bars
            if whole:  # a pass of the source: its block and its lanes with it
                cells[tid] = src["cells"][tid]
                if lanes := sm.cell_lanes(src, tid):
                    auto[tid] = [dict(ln, points=list(ln["points"])) for ln in lanes]
                if tid in (src.get("spans") or {}):
                    spans[tid] = list(src["spans"][tid])
                continue
            cells[tid] = self._piece(*self._want(tid, src_id, phases[tid], bars))
            if sm.cell_lanes(src, tid) or tid in (src.get("spans") or {}):
                self.loose = True
        sec["cells"] = cells
        if auto:
            sec["auto"] = auto
        if spans:
            sec["spans"] = spans
        self.out.append(sid)


def commit(
    song: dict, events: list[dict], bars: int, scenes: dict | None = None
) -> tuple[list[str], bool]:
    """Append the jam `events` describe, up to bar `bars`, to the song
    (`scenes`: sections outside it the log launches as well, `timeline`).
    (The new sections' ids in order, whether a lane or a trimmed block was
    left behind.) Refused whole — nothing launched yet, an id the song
    does not have, more parts than `MAX_SECTIONS` has room for, a pool
    that cannot hold the windows — and then the song is as it was only
    when the caller works on a copy, as `_commit` does."""
    parts = timeline(song, events, bars, scenes)
    if not parts:
        raise SongError("Nothing has been launched yet: press a pad, then capture.")
    w = _Writer(song, scenes)
    room = limits.MAX_SECTIONS - len(song["sections"])
    try:
        for a, b, now in parts:
            w.part(a, b, now)
    except SongError as err:
        if len(song["sections"]) < limits.MAX_SECTIONS:
            raise
        raise SongError(
            f"The jam is more than the {room} section"
            f"{'' if room == 1 else 's'} the song has room for "
            f"({limits.MAX_SECTIONS} at most): remove sections, or capture sooner."
        ) from err
    return w.out, w.loose
