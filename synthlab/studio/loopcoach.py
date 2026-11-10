"""The loop coach on Perform: ✦ Coach reads the working loop
and says what it finds, each with a one-line why and a **Fix**; it offers
other versions of the edited lane and the beat; it talks (`loopchat.py`,
Gemini). Nothing it proposes reaches the loop until it is accepted: every
change it makes can be accepted or reversed.

**The loop as the coach reads it** (`read` / `write`): a *doc* — every lane
that plays notes of its own, by slot (the edited one from the arrangement,
the others from their slots: `cells`, and the `seq`'s cuts, velocities and
slides when the slot has one), and the beat (`drums`, `drum_vel`,
`drum_hits`). A following lane plays the main lane's notes and is no lane
of its own here. The clocks, the key, the sounds are read, never written.

**Rules** (`RULES`, pure: a doc and the loop's facts in, at most one
`Finding` a lane out, its `after` the doc with the fix made). Assess runs
every rule on the doc as it is; "Clean up all" runs them in order, each on
what the ones before left (`clean_all`). The key and the grid pick reuse
`takeclean` (`to_key`, `_in_key`, `_residual`); the chords are
`chordtrack.resolve`'s.

**Options** (`options`): other versions of the edited lane — tighter,
humanised, the last bar varied, call and response, on the chords, busier
and sparser (`gen/loop.vary`) — and of the beat.

**The session** (`StudioState.coach`, runtime): the proposal waiting (its
`base`, the doc it was made from, and its `after`), and the thread —
messages, replies and every accepted change with its ↶ Reverse. Accept is
ONE pattern undo step holding the whole loop (`_whole_loop`, as a lane
head's sound pick is); Reverse undoes that step while it is the newest,
else puts the loop back as it was before it as a new step.
"""

from __future__ import annotations

import copy
import json
import random
import statistics
import uuid
import zlib
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field, replace

from ..sequencer import midi, name_of
from . import takeclean
from .arrange import bar_beats
from .gen import chordtrack
from .gen import loop as gl
from .state.model import grid_rows

NOTE_VEL = 0.9  # a note step's velocity when its lane holds none (patternops)
DRUM_VEL = 1.0  # a hit's, when neither its hit nor its step holds one
FLAT_MIN = 4  # notes before "every note at one velocity" is said
ERRATIC = 0.22  # a lane's velocity spread (pstdev) that reads as erratic
LINE = 0.75  # a lane is a single line when this share of its starts are alone
LEAP = 9  # semitones: the seam's leap worth a word
CROWDED = 0.75  # of a bar's steps holding a note start
THREAD_CAP = 40  # entries the thread keeps
MAX_LINES = 6  # a proposal's change lines shown


def lane_name(slot: int) -> str:
    return f"lane {slot + 1}"


def _where(step: int, spb: int, beat: int) -> str:
    bar, at = divmod(int(step), spb)
    b, s = divmod(at, max(1, beat))
    return f"bar {bar + 1}, beat {b + 1}" + (f" +{s}" if s else "")


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


# --- the loop as a doc --------------------------------------------------------------


def _lane_doc(cells, cuts, velocities, slides) -> dict:
    return {
        "cells": sorted(
            ([str(p), int(s)] for p, s in cells), key=lambda c: (c[1], c[0])
        ),
        "cuts": sorted(
            ([str(p), int(s)] for p, s in cuts or []), key=lambda c: (c[1], c[0])
        ),
        "velocities": None
        if velocities is None
        else {str(k): float(v) for k, v in velocities.items()},
        "slides": sorted(int(s) for s in slides or []),
    }


def own_lanes(state) -> list[int]:
    """The slots that play notes of their own: the edited one (unless it
    follows) and every other slot holding a sound that does not follow."""
    out = []
    for i, lay in enumerate(state.layers):
        if state.follows(i):
            continue
        if i == state.active_layer or lay.get("patch"):
            out.append(i)
    return out


def read(state) -> dict:
    """The working loop as the coach's doc (see the module docstring)."""
    a = state.arrangement
    lanes = {}
    for i in own_lanes(state):
        if i == state.active_layer:
            lanes[str(i)] = _lane_doc(a.cells, a.cuts, a.velocities, a.slides)
            continue
        lay = state.layers[i]
        seq = lay.get("seq") if isinstance(lay.get("seq"), dict) else None
        lanes[str(i)] = _lane_doc(
            lay.get("cells") or [],
            (seq or {}).get("cuts") or [],
            None if seq is None else (seq.get("velocities") or {}),
            (seq or {}).get("slides") or [],
        )
    drums = {
        "drums": sorted(
            ([str(v), int(s)] for v, s in a.drums), key=lambda c: (c[1], c[0])
        ),
        "drum_vel": {str(k): float(v) for k, v in a.drum_vel.items()},
        "drum_hits": copy.deepcopy(dict(a.drum_hits)),
    }
    return {"lanes": lanes, "drums": drums}


def write(state, doc: dict) -> None:
    """Put a doc into the working loop: the edited lane into the
    arrangement, the other lanes into their slots, the beat."""
    a = state.arrangement
    changes = {}
    for key, lane in doc["lanes"].items():
        i = int(key)
        if i == state.active_layer:
            changes |= {
                "cells": [list(c) for c in lane["cells"]],
                "cuts": [list(c) for c in lane["cuts"]],
                "velocities": dict(lane["velocities"] or {}),
                "slides": list(lane["slides"]),
            }
            continue
        if not 0 <= i < len(state.layers):
            continue
        lay = state.layers[i]
        lay["cells"] = [list(c) for c in lane["cells"]]
        seq = lay.get("seq")
        if isinstance(seq, dict):
            seq["cuts"] = [list(c) for c in lane["cuts"]]
            seq["slides"] = list(lane["slides"])
            if lane["velocities"] is not None:
                seq["velocities"] = dict(lane["velocities"])
    d = doc["drums"]
    changes |= {
        "drums": [list(c) for c in d["drums"]],
        "drum_vel": dict(d["drum_vel"]),
        "drum_hits": copy.deepcopy(d["drum_hits"]),
    }
    state.arrangement = replace(a, **changes)


def fingerprint(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True)


# --- notes as runs --------------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    m: int  # MIDI
    start: int
    n: int  # steps

    @property
    def end(self) -> int:
        return self.start + self.n

    @property
    def pitch(self) -> str:
        return name_of(self.m)


def notes(lane: dict) -> list[Note]:
    """A lane's notes: runs of one row's adjacent cells split at its cuts."""
    by: dict[str, list[int]] = {}
    for p, s in lane["cells"]:
        by.setdefault(p, []).append(int(s))
    cuts = {(p, int(s)) for p, s in lane["cuts"]}
    out = []
    for p, steps in by.items():
        steps = sorted(set(steps))
        start = prev = steps[0]
        for s in steps[1:]:
            if s != prev + 1 or (p, s) in cuts:
                out.append(Note(midi(p), start, prev - start + 1))
                start = s
            prev = s
        out.append(Note(midi(p), start, prev - start + 1))
    return sorted(out, key=lambda n: (n.start, n.m))


def with_notes(lane: dict, ns, *, keep_vel: dict | None = None) -> dict:
    """`lane` holding exactly the notes `ns`: cells, and a cut wherever a
    note starts right after another of its pitch. Velocities and slides
    stay on their steps (the grid's rule: a step's, shared by every note
    starting there); `keep_vel` maps a moved note's new start to the
    velocity it had."""
    cells: set[tuple[str, int]] = set()
    cuts: set[tuple[str, int]] = set()
    for n in sorted(ns, key=lambda n: (n.start, n.m)):
        if not 0 <= n.m <= 127:
            continue
        p = n.pitch
        if (p, n.start - 1) in cells:  # or it would join the note before it
            cuts.add((p, n.start))
        for s in range(n.start, n.end):
            cells.add((p, s))
    out = dict(lane)
    out["cells"] = sorted(([p, s] for p, s in cells), key=lambda c: (c[1], c[0]))
    starts = {(n.pitch, n.start) for n in ns}
    out["cuts"] = sorted(
        ([p, s] for p, s in cuts if (p, s) in starts), key=lambda c: (c[1], c[0])
    )
    if keep_vel and lane["velocities"] is not None:
        out["velocities"] = {
            **lane["velocities"],
            **{str(k): v for k, v in keep_vel.items()},
        }
    return out


def vel_of(lane: dict, step: int, main: dict | None = None) -> float:
    v = lane["velocities"] if lane["velocities"] is not None else (main or {})
    return float(v.get(str(step), NOTE_VEL))


def hit_vel(drums: dict, voice: str, step: int) -> float:
    h = (drums["drum_hits"].get(f"{voice}.{step}") or {}).get("v")
    if h is not None:
        return float(h)
    return float(drums["drum_vel"].get(str(step), DRUM_VEL))


def _set_hit_vel(drums: dict, voice: str, step: int, v: float) -> None:
    key = f"{voice}.{step}"
    drums["drum_hits"][key] = (drums["drum_hits"].get(key) or {}) | {"v": round(v, 3)}


# --- the loop's facts -----------------------------------------------------------------


@dataclass
class Facts:
    """What the rules read besides the doc: the clocks, the key, the
    chords, the roll's window, which lane is edited, the sounds' names."""

    total: int
    spb: int
    bars: int
    beat: int  # steps a beat
    drum_total: int
    drum_spb: int
    keys: frozenset
    key: str
    root: str
    scale_name: str
    chords: list | None  # chordtrack.resolve's, on the note grid's steps
    rows: list[str]  # the roll's window: the edited lane's rows
    active: int
    names: dict = field(default_factory=dict)  # slot -> sound name
    main_vel: dict = field(default_factory=dict)  # the edited lane's
    meter: str = "4/4"
    bpm: float = 120.0


def facts(state) -> Facts:
    a = state.arrangement
    spb, bars = int(a.steps_per_bar), max(1, int(a.bars))
    beat = max(1, round(spb / max(1.0, bar_beats(a))))
    song = gl.song_of(a)
    chords = chordtrack.resolve(song, None, bars, spb) if "chords" in song else None
    names = {}
    for i, lay in enumerate(state.layers):
        if i == state.active_layer:
            names[i] = state.patch.name or "Untitled"
        elif lay.get("patch"):
            p = lay["patch"]
            names[i] = (
                p.get("name") if isinstance(p, dict) else getattr(p, "name", "")
            ) or "Untitled"
    return Facts(
        total=bars * spb,
        spb=spb,
        bars=bars,
        beat=beat,
        drum_total=max(1, int(a.drum_bars)) * max(1, int(a.drum_steps)),
        drum_spb=max(1, int(a.drum_steps)),
        keys=frozenset(takeclean._in_key(a.root, a.scale_name)),
        key=takeclean.key_name(a.root, a.scale_name),
        root=a.root,
        scale_name=a.scale_name,
        chords=chords,
        rows=grid_rows(a, state.grid_span, state.grid_oct),
        active=state.active_layer,
        names=names,
        main_vel=dict(a.velocities),
        meter=a.meter,
        bpm=float(a.bpm),
    )


def chord_pcs(f: Facts, step: int) -> set[int] | None:
    if not f.chords:
        return None
    c = chordtrack.chord_at(f.chords, step % f.total)
    key = midi(f.root) % 12
    return {(key + t) % 12 for t in c["tones"]}


def _nearest(m: int, ok) -> int:
    """The nearest MIDI note to `m` whose pitch class `ok` takes (down on
    a tie); `m` when none within an octave does."""
    for d in range(13):
        for c in (m - d, m + d):
            if 0 <= c <= 127 and ok(c % 12):
                return c
    return m


# --- findings -------------------------------------------------------------------------


@dataclass
class Finding:
    """One thing the coach found: `id` (rule:lane, stable while the loop
    keeps it), the lane ("0".."3" or "drums"), a title, why it matters,
    and the doc with the fix made (None: said, not fixable here)."""

    id: str
    rule: str
    lane: str
    title: str
    why: str
    after: dict | None
    show: bool = False  # notes outside the roll's rows: the window's to fix


def _lane_after(doc: dict, key: str, lane: dict) -> dict:
    out = copy.deepcopy(doc)
    out["lanes"][key] = lane
    return out


def _drums_after(doc: dict, drums: dict) -> dict:
    out = copy.deepcopy(doc)
    out["drums"] = drums
    return out


def _title(f: Facts, key: str, what: str) -> str:
    """A finding's title: what, and on which lane."""
    return f"{what} — {_who(f, key)}"


def _who(f: Facts, key: str) -> str:
    i = int(key)
    name = f.names.get(i)
    return (
        lane_name(i)
        + (f" ({name})" if name else "")
        + (" — edited" if i == f.active else "")
    )


def r_off_key(doc, f: Facts, key: str) -> Finding | None:
    lane = doc["lanes"][key]
    ns = notes(lane)
    bad = [n for n in ns if n.m % 12 not in f.keys]
    if not bad:
        return None
    names = sorted({n.pitch.rstrip("-0123456789") for n in bad})
    moved = [
        replace(n, m=takeclean.to_key(n.m, set(f.keys))) if n in bad else n for n in ns
    ]
    return Finding(
        f"off_key:{key}",
        "off_key",
        key,
        _title(
            f,
            key,
            f"{_plural(len(bad), 'note')} off the key of {f.key} ({', '.join(names)})",
        ),
        f"They are not in {f.key}, so they rub against everything else; "
        "each moves to the nearest note of the key.",
        _lane_after(doc, key, with_notes(lane, moved)),
    )


def r_off_chord(doc, f: Facts, key: str) -> Finding | None:
    if not f.chords:
        return None
    lane = doc["lanes"][key]
    ns = notes(lane)
    bad = []
    for n in ns:
        pcs = chord_pcs(f, n.start)
        if (
            pcs
            and n.m % 12 in f.keys
            and n.start % f.beat == 0
            and n.n >= max(1, f.beat // 2)
            and n.m % 12 not in pcs
        ):
            bad.append(n)
    if not bad:
        return None
    moved = []
    for n in ns:
        if n in bad:
            pcs = chord_pcs(f, n.start) or set()
            n = replace(n, m=_nearest(n.m, lambda pc, pcs=pcs: pc in pcs))
        moved.append(n)
    first = bad[0]
    return Finding(
        f"off_chord:{key}",
        "off_chord",
        key,
        _title(
            f,
            key,
            f"{_plural(len(bad), 'held note')} off the chord under "
            f"{'it' if len(bad) == 1 else 'them'} "
            f"(first: {first.pitch} at {_where(first.start, f.spb, f.beat)})",
        ),
        "On a beat and held, a note outside the chord the chord lane plays there "
        "sounds wrong rather than passing; each moves to the nearest chord tone.",
        _lane_after(doc, key, with_notes(lane, moved)),
    )


def r_hidden(doc, f: Facts, key: str) -> Finding | None:
    """Notes the roll does not show: outside its rows (they play: the fix
    is the window's — Show them, `StudioState.show_notes` — never a moved
    note) and past the loop's end (they never play: the grid's tidy)."""
    if int(key) != f.active:
        return None
    lane = doc["lanes"][key]
    ns = notes(lane)
    rows = {midi(r) for r in f.rows}
    past = [n for n in ns if n.start >= f.total]
    out = [n for n in ns if n.start < f.total and n.m not in rows]
    if not past and not out:
        return None
    bits, why = [], []
    if out:
        names = sorted({n.pitch for n in out}, key=midi)
        bits.append(
            f"{_plural(len(out), 'note')} outside the roll's rows "
            f"({', '.join(names[:4])}{' …' if len(names) > 4 else ''})"
        )
        why.append(
            "a note outside the rows plays but cannot be seen: "
            "Show them moves the rows to the notes"
        )
    if past:
        bits.append(f"{_plural(len(past), 'note')} past the loop's end")
        why.append(
            "a note past the end never plays: the fix removes it (the grid's tidy)"
        )
    return Finding(
        f"hidden:{key}",
        "hidden",
        key,
        f"Hidden: {' and '.join(bits)}",
        "; ".join(why).capitalize() + ".",
        _lane_after(doc, key, with_notes(lane, [n for n in ns if n not in past]))
        if past
        else None,
        show=bool(out),
    )


def _is_line(ns) -> bool:
    if len(ns) < 3:
        return False
    starts = Counter(n.start for n in ns)
    alone = sum(1 for c in starts.values() if c == 1)
    return alone >= LINE * len(starts)


def r_overlap(doc, f: Facts, key: str) -> Finding | None:
    """A single line whose held notes run under the next one."""
    lane = doc["lanes"][key]
    ns = notes(lane)
    if not _is_line(ns):
        return None
    starts = sorted({n.start for n in ns})
    out, hit = [], 0
    for n in ns:
        nxt = next((s for s in starts if n.start < s < n.end), None)
        if nxt is not None:
            n = replace(n, n=nxt - n.start)
            hit += 1
        out.append(n)
    if not hit:
        return None
    return Finding(
        f"overlap:{key}",
        "overlap",
        key,
        f"{_plural(hit, 'note')} still held when the next one starts — {_who(f, key)}",
        "In a single line a note held under the next smears it (and a mono "
        "sound cuts it anyway); each now stops where the next begins.",
        _lane_after(doc, key, with_notes(lane, out)),
    )


def r_rub(doc, f: Facts, key: str) -> Finding | None:
    """Two notes of one lane a semitone apart, sounding together."""
    lane = doc["lanes"][key]
    ns = notes(lane)
    gone: set[Note] = set()
    first = None
    for a in ns:
        for b in ns:
            if a is b or a in gone or b in gone or b.start < a.start:
                continue
            if (
                b.start < a.end
                and abs(a.m - b.m) in (1, 13)
                and (a.start, a.m) < (b.start, b.m)
            ):
                # the one that is out of the key goes; else the shorter, else the later
                loser = (
                    a
                    if a.m % 12 not in f.keys and b.m % 12 in f.keys
                    else b
                    if b.m % 12 not in f.keys and a.m % 12 in f.keys
                    else min((a, b), key=lambda n: (n.n, -n.start))
                )
                gone.add(loser)
                first = first or (a, b)
    if not gone:
        return None
    a, b = first
    return Finding(
        f"rub:{key}",
        "rub",
        key,
        _title(
            f,
            key,
            f"{_plural(len(gone), 'semitone rub')} inside the lane ({a.pitch} "
            f"against {b.pitch} at {_where(b.start, f.spb, f.beat)})",
        ),
        "Two notes a semitone apart at once sound like a mistake, not a chord; "
        "the odd one out goes.",
        _lane_after(doc, key, with_notes(lane, [n for n in ns if n not in gone])),
    )


def _grid_of(starts: list[int], beat: int) -> int | None:
    """The coarsest grid (a beat, an eighth) most starts sit on, when a few
    do not: those are the off-grid ones."""
    for g in (beat, beat // 2):
        if g < 2:
            continue
        on = sum(1 for s in starts if s % g == 0)
        if on < len(starts) and on >= 0.8 * len(starts):
            return g
        if on == len(starts):
            return None
    return None


def r_off_grid(doc, f: Facts, key: str) -> Finding | None:
    lane = doc["lanes"][key]
    ns = notes(lane)
    if len(ns) < 6:
        return None
    g = _grid_of([n.start for n in ns], f.beat)
    if g is None:
        return None
    bad = [n for n in ns if n.start % g]
    out, vel = [], {}
    taken = {(n.m, n.start) for n in ns if n not in bad}
    for n in ns:
        if n in bad:
            s = int(round(n.start / g) * g) % f.total
            if (n.m, s) in taken:
                continue  # it lands on a note of its own pitch: one note
            vel[s] = vel_of(lane, n.start, f.main_vel)
            n = replace(n, start=s, n=max(1, min(n.n, f.total - s)))
            taken.add((n.m, s))
        out.append(n)
    label = takeclean.grid_label(f.spb, g)
    return Finding(
        f"off_grid:{key}",
        "off_grid",
        key,
        _title(
            f, key, f"{_plural(len(bad), 'note')} off the {label} grid the rest sit on"
        ),
        f"Everything else starts on a {label}: a stray sixteenth reads as a slip; "
        f"each snaps to the nearest {label}.",
        _lane_after(doc, key, with_notes(lane, out, keep_vel=vel)),
    )


def _accented(step: int, f: Facts, seed: int) -> float:
    """A velocity with a groove: the bar's one loudest, the beats, the
    off-beat eighths, the sixteenths between quietest — a little jitter."""
    at = step % f.spb
    base = (
        1.0
        if at == 0
        else 0.9
        if at % f.beat == 0
        else 0.74
        if at % max(1, f.beat // 2) == 0
        else 0.62
    )
    r = random.Random(seed * 1009 + step).uniform(-0.04, 0.04)
    return round(min(1.0, max(0.3, base + r)), 3)


def r_velocity(doc, f: Facts, key: str) -> Finding | None:
    lane = doc["lanes"][key]
    ns = notes(lane)
    if len(ns) < FLAT_MIN or (lane["velocities"] is None and int(key) != f.active):
        return None
    starts = sorted({n.start for n in ns})
    vels = [vel_of(lane, s, f.main_vel) for s in starts]
    seed = zlib.crc32(key.encode())
    new = dict(lane["velocities"] or {})
    if len({round(v, 2) for v in vels}) == 1:
        for s in starts:
            new[str(s)] = _accented(s, f, seed)
        out = {**lane, "velocities": new}
        return Finding(
            f"velocity:{key}",
            "velocity",
            key,
            f"Every note at {round(vels[0] * 100)}% — flat — {_who(f, key)}",
            "One velocity for every note sounds programmed; an accent on the beats "
            "(the bar's one strongest, the sixteenths between softer) gives it a "
            "pulse.",
            _lane_after(doc, key, out),
        )
    if len(vels) >= FLAT_MIN and statistics.pstdev(vels) > ERRATIC:
        med = statistics.median(vels)
        for s, v in zip(starts, vels, strict=True):
            new[str(s)] = round(
                max(takeclean.VEL_FLOOR, min(1.0, med + (v - med) * takeclean.SQUEEZE)),
                3,
            )
        return Finding(
            f"velocity:{key}",
            "velocity",
            key,
            _title(
                f,
                key,
                "Velocities all over the place "
                f"(spread {round(statistics.pstdev(vels) * 100)}%)",
            ),
            "Big jumps in level from note to note read as mistakes; they are drawn "
            "towards the lane's middle, accents kept.",
            _lane_after(doc, key, {**lane, "velocities": new}),
        )
    return None


def _bar_sig(ns, bar: int, spb: int) -> frozenset:
    lo = bar * spb
    return frozenset((n.m, n.start - lo, n.n) for n in ns if lo <= n.start < lo + spb)


def _bar_copy(ns, src: int, dst: int, spb: int, total: int) -> list[Note]:
    lo, hi = dst * spb, (dst + 1) * spb
    kept = [n for n in ns if not lo <= n.start < hi]
    for n in ns:
        if src * spb <= n.start < (src + 1) * spb:
            s = n.start - src * spb + lo
            kept.append(replace(n, start=s, n=min(n.n, total - s)))
    return kept


def r_repeat(doc, f: Facts, key: str) -> Finding | None:
    """One bar that breaks a pattern the others keep."""
    if f.bars < 3:
        return None
    lane = doc["lanes"][key]
    ns = notes(lane)
    sigs = [_bar_sig(ns, b, f.spb) for b in range(f.bars)]
    common, count = Counter(sigs).most_common(1)[0]
    if count != f.bars - 1 or not common:
        return None
    odd = next(b for b, s in enumerate(sigs) if s != common)
    src = next(b for b, s in enumerate(sigs) if s == common)
    same = len(sigs[odd] & common) / max(1, len(sigs[odd] | common))
    if odd == f.bars - 1 and same < 0.5:
        return None  # the last bar a different thing: a turnaround, on purpose
    out = _bar_copy(ns, src, odd, f.spb, f.total)
    return Finding(
        f"repeat:{key}",
        "repeat",
        key,
        _title(
            f,
            key,
            f"Bar {odd + 1} breaks the pattern the other {f.bars - 1} bars repeat",
        ),
        ("It is almost the same — a slip? " if same >= 0.5 else "Only it differs. ")
        + f"The fix makes it bar {src + 1} again (keep it if it was on purpose).",
        _lane_after(doc, key, with_notes(lane, out)),
    )


def r_seam(doc, f: Facts, key: str) -> Finding | None:
    """The loop's last note into its first: a big leap at the wrap."""
    lane = doc["lanes"][key]
    ns = notes(lane)
    if not _is_line(ns):
        return None
    first = min(ns, key=lambda n: (n.start, -n.m))
    last = max(ns, key=lambda n: (n.start, n.m))
    leap = last.m - first.m
    if abs(leap) <= LEAP or first is last:
        return None
    to = last.m - 12 if leap > 0 else last.m + 12
    if abs(to - first.m) >= abs(leap) or not 0 <= to <= 127:
        return None
    out = [replace(n, m=to) if n is last else n for n in ns]
    return Finding(
        f"seam:{key}",
        "seam",
        key,
        _title(
            f,
            key,
            f"The seam leaps {abs(leap)} semitones: {last.pitch} at the end back "
            f"to {first.pitch}",
        ),
        "The last note into the first is heard every time the loop goes round; "
        "a leap there sounds like a restart. The last note moves an octave closer.",
        _lane_after(doc, key, with_notes(lane, out)),
    )


def r_bars(doc, f: Facts, key: str) -> Finding | None:
    """An empty bar among full ones; a bar so busy it is a wall of notes."""
    if f.bars < 2:
        return None
    lane = doc["lanes"][key]
    ns = notes(lane)
    if len(ns) < 4:
        return None
    sounding = [
        any(n.start < (b + 1) * f.spb and n.end > b * f.spb for n in ns)
        for b in range(f.bars)
    ]
    if sum(sounding) >= 2 and not all(sounding):
        empty = sounding.index(False)
        src = max((b for b in range(empty) if sounding[b]), default=None)
        if src is None:
            src = next(b for b in range(f.bars) if sounding[b])
        out = _bar_copy(ns, src, empty, f.spb, f.total)
        return Finding(
            f"bars:{key}",
            "bars",
            key,
            _title(f, key, f"Bar {empty + 1} is empty"),
            f"The other bars play; a silent one sounds like the part dropped out. "
            f"The fix fills it with bar {src + 1} (keep it if the gap is the idea).",
            _lane_after(doc, key, with_notes(lane, out)),
        )
    for b in range(f.bars):
        starts = {n.start for n in ns if b * f.spb <= n.start < (b + 1) * f.spb}
        if len(starts) > CROWDED * f.spb:
            gone = [
                n
                for n in ns
                if b * f.spb <= n.start < (b + 1) * f.spb
                and n.start % 2
                and (
                    chord_pcs(f, n.start) is None
                    or n.m % 12 not in chord_pcs(f, n.start)
                )
            ]
            if not gone:
                return None
            return Finding(
                f"bars:{key}",
                "bars",
                key,
                _title(
                    f,
                    key,
                    f"Bar {b + 1} is crowded: a note on {len(starts)} of its "
                    f"{f.spb} steps",
                ),
                "A note on nearly every step is a wall; the off-beat sixteenths "
                "that are not chord tones go, so the strong notes speak.",
                _lane_after(
                    doc, key, with_notes(lane, [n for n in ns if n not in gone])
                ),
            )
    return None


def _centre(ns) -> float:
    return statistics.median(n.m for n in ns) if ns else 0.0


def r_register(doc, f: Facts, key: str) -> Finding | None:
    """Another lane in the same register, playing at the same time: the
    edited lane (or the later one) moves an octave out of its way."""
    me = notes(doc["lanes"][key])
    if len(me) < 3:
        return None
    for other, lane in doc["lanes"].items():
        if other == key:
            continue
        them = notes(lane)
        if len(them) < 3:
            continue
        mine = {s for n in me for s in range(n.start, n.end)}
        theirs = {s for n in them for s in range(n.start, n.end)}
        both = mine & theirs
        if len(both) < 0.3 * max(1, min(len(mine), len(theirs))):
            continue
        a, b = _centre(me), _centre(them)
        if abs(a - b) > 4:
            continue
        # one moves: the edited lane, else the later slot of the two
        mover = (
            key
            if int(key) == f.active
            or (int(other) != f.active and int(key) > int(other))
            else other
        )
        if mover != key:
            continue  # said once, on the lane that moves
        up = a >= b
        out = [replace(n, m=n.m + (12 if up else -12)) for n in me]
        if any(not 12 <= n.m <= 108 for n in out):
            continue
        return Finding(
            f"register:{key}",
            "register",
            key,
            f"{lane_name(int(key))} and {lane_name(int(other))} sit in the same "
            f"register (around {name_of(round(a))})",
            "Two parts in one octave at once mask each other; this one moves "
            f"an octave {'up' if up else 'down'} out of the way.",
            _lane_after(doc, key, with_notes(doc["lanes"][key], out)),
        )
    return None


def _kicks(doc, f: Facts) -> set[int]:
    """The kick's steps on the note grid (where the two clocks line up)."""
    out = set()
    for v, s in doc["drums"]["drums"]:
        if v != "kick":
            continue
        x = int(s) * f.spb
        if x % f.drum_spb == 0:
            st = x // f.drum_spb
            for k in range(0, f.total, max(1, f.drum_total * f.spb // f.drum_spb)):
                if 0 <= st + k < f.total:
                    out.add(st + k)
    return out


def r_bass_kick(doc, f: Facts, key: str) -> Finding | None:
    """The bass (the lowest lane, under C3) starting a sixteenth off the
    kick: a flam that blurs the low end."""
    if f.spb < 8:
        return None
    centres = {k: _centre(notes(ln)) for k, ln in doc["lanes"].items() if notes(ln)}
    if (
        not centres
        or min(centres, key=centres.get) != key
        or centres[key] >= midi("C3")
    ):
        return None
    kicks = _kicks(doc, f)
    if not kicks:
        return None
    lane = doc["lanes"][key]
    ns = notes(lane)
    starts = {n.start for n in ns}
    near = []
    for n in ns:
        if n.start in kicks:
            continue
        for d in (-1, 1):
            k = n.start + d
            if k in kicks and k not in starts:
                near.append((n, k))
                break
    if not near:
        return None
    moved, vel = {}, {}
    for n, k in near:
        moved[n] = replace(n, start=k, n=max(1, min(n.n, f.total - k)))
        vel[k] = vel_of(lane, n.start, f.main_vel)
    out = [moved.get(n, n) for n in ns]
    return Finding(
        f"bass_kick:{key}",
        "bass_kick",
        key,
        _title(
            f,
            key,
            f"The bass starts a sixteenth off the kick {_plural(len(near), 'time')}",
        ),
        "A bass note a sixteenth before or after the kick flams with it and blurs "
        "the low end; each moves onto the kick.",
        _lane_after(doc, key, with_notes(lane, out, keep_vel=vel)),
    )


RULES = (
    ("off_key", r_off_key),
    ("off_chord", r_off_chord),
    ("hidden", r_hidden),
    ("rub", r_rub),
    ("overlap", r_overlap),
    ("off_grid", r_off_grid),
    ("bass_kick", r_bass_kick),
    ("repeat", r_repeat),
    ("bars", r_bars),
    ("seam", r_seam),
    ("register", r_register),
    ("velocity", r_velocity),
)


# --- the beat -------------------------------------------------------------------------

_HATS = ("hat", "ohat")


def d_velocity(doc, f: Facts) -> Finding | None:
    """Hats all at one level: a machine, not a drummer."""
    d = doc["drums"]
    hats = [(v, int(s)) for v, s in d["drums"] if v in _HATS]
    levels = {round(hit_vel(d, v, s), 2) for v, s in hats}
    if len(hats) < FLAT_MIN or len(levels) != 1:
        return None
    out = copy.deepcopy(d)
    beat = max(1, f.drum_spb // 4)
    for v, s in hats:
        at = s % f.drum_spb
        lvl = 0.92 if at % beat == 0 else 0.72 if at % max(1, beat // 2) == 0 else 0.55
        _set_hit_vel(out, v, s, lvl + random.Random(s).uniform(-0.03, 0.03))
    return Finding(
        "velocity:drums",
        "velocity",
        "drums",
        f"The hats are all at {round(hit_vel(d, *hats[0]) * 100)}% — flat",
        "Hats at one level sound like a machine gun; on the beat strong, the "
        "off-beats lighter, the sixteenths ghosted.",
        _drums_after(doc, out),
    )


def d_bars(doc, f: Facts) -> Finding | None:
    d = doc["drums"]
    bars = f.drum_total // f.drum_spb
    if bars < 2 or len(d["drums"]) < 4:
        return None
    full = [
        any(b * f.drum_spb <= int(s) < (b + 1) * f.drum_spb for _v, s in d["drums"])
        for b in range(bars)
    ]
    if all(full) or sum(full) < 2:
        return None
    empty = full.index(False)
    src = max((b for b in range(empty) if full[b]), default=full.index(True))
    out = copy.deepcopy(d)
    lo = src * f.drum_spb
    for v, s in d["drums"]:
        s = int(s)
        if lo <= s < lo + f.drum_spb:
            t = s - lo + empty * f.drum_spb
            out["drums"].append([v, t])
            h = d["drum_hits"].get(f"{v}.{s}")
            if h:
                out["drum_hits"][f"{v}.{t}"] = dict(h)
    out["drums"].sort(key=lambda c: (c[1], c[0]))
    return Finding(
        "bars:drums",
        "bars",
        "drums",
        f"Beat bar {empty + 1} is empty",
        f"The beat drops out for a bar; the fix plays bar {src + 1} there "
        "(keep it if the break is the idea).",
        _drums_after(doc, out),
    )


DRUM_RULES = (("velocity", d_velocity), ("bars", d_bars))


def assess(doc: dict, f: Facts) -> list[Finding]:
    out = []
    for _name, rule in RULES:
        for key in doc["lanes"]:
            got = rule(doc, f, key)
            if got:
                out.append(got)
    for _name, rule in DRUM_RULES:
        got = rule(doc, f)
        if got:
            out.append(got)
    return out


def apply_ids(doc: dict, f: Facts, ids) -> tuple[dict, list[str]]:
    """The fixes named (`Finding.id`), in rule order, each on what the ones
    before left: (the doc, the titles of the fixes made)."""
    want = set(ids)
    said = []
    for name, rule in RULES:
        for key in list(doc["lanes"]):
            if f"{name}:{key}" not in want:
                continue
            got = rule(doc, f, key)
            if got and got.after:
                doc = got.after
                said.append(got.title)
    for name, rule in DRUM_RULES:
        if f"{name}:drums" in want and (got := rule(doc, f)) and got.after:
            doc = got.after
            said.append(got.title)
    return doc, said


def clean_all(doc: dict, f: Facts) -> tuple[dict, list[str]]:
    """Every fix, in rule order, each on what the ones before left."""
    return apply_ids(doc, f, [x.id for x in assess(doc, f) if x.after])


# --- options --------------------------------------------------------------------------


@dataclass
class Option:
    id: str
    label: str
    why: str
    after: dict | None  # None: not on offer for this loop (`why` says so)


def _ladder(f: Facts) -> list[int]:
    return [m for m in range(12, 109) if m % 12 in f.keys]


def _step_in_key(m: int, by: int, f: Facts) -> int:
    lad = _ladder(f)
    m = takeclean.to_key(m, set(f.keys))
    i = lad.index(m) if m in lad else 0
    return lad[max(0, min(len(lad) - 1, i + by))]


def options(state, doc: dict, f: Facts) -> list[Option]:
    """Other versions of the edited lane, and of the beat."""
    out: list[Option] = []
    key = str(f.active)
    lane = doc["lanes"].get(key)
    if lane is not None and notes(lane):
        ns = notes(lane)
        # tighter: the key, the semitone rubs, the smears, the grid
        tight = doc
        for name in ("off_key", "rub", "overlap", "off_grid"):
            tight, _said = apply_ids(tight, f, [f"{name}:{key}"])
        out.append(
            Option(
                "tighter",
                "Tighter",
                "in the key, no rubs or smears, on the grid the part keeps",
                tight if tight != doc else None,
            )
        )
        seed = zlib.crc32(fingerprint(doc).encode()) % 997
        hum = {**(lane["velocities"] or {})}
        for s in sorted({n.start for n in ns}):
            hum[str(s)] = round(
                min(
                    1.0,
                    max(
                        0.3,
                        _accented(s, f, seed)
                        + random.Random(seed + s).uniform(-0.08, 0.08),
                    ),
                ),
                3,
            )
        out.append(
            Option(
                "humanised",
                "Humanised",
                "accents on the beats, every velocity a little different",
                _lane_after(doc, key, {**lane, "velocities": hum}),
            )
        )
        if f.bars >= 2:
            # the last bar's second half a walk up the key into the loop's first note
            first = min(ns, key=lambda n: (n.start, -n.m))
            mid = (f.bars - 1) * f.spb + f.spb // 2
            step = max(1, f.beat // 2)
            kept = [n for n in ns if not mid <= n.start < f.total]
            count = (f.total - mid) // step
            walk = []
            for k in range(count):
                m = _step_in_key(first.m, k - count, f)
                walk.append(Note(m, mid + k * step, step))
            out.append(
                Option(
                    "turnaround",
                    "Last bar varied",
                    "the last half bar walks up the key into the loop's first note",
                    _lane_after(doc, key, with_notes(lane, kept + walk)),
                )
            )
            if f.bars % 2 == 0:
                # bars 2, 4… answer bars 1, 3…: the rhythm kept, the line turned over
                call = []
                for b in range(0, f.bars, 2):
                    ask = [n for n in ns if b * f.spb <= n.start < (b + 1) * f.spb]
                    call += ask
                    if not ask:
                        continue
                    pivot = ask[0].m
                    for n in ask:
                        steps = _ladder(f)
                        a = takeclean.to_key(n.m, set(f.keys))
                        p = takeclean.to_key(pivot, set(f.keys))
                        d = (
                            (steps.index(a) - steps.index(p))
                            if a in steps and p in steps
                            else 0
                        )
                        m = _step_in_key(pivot, -d, f)
                        call.append(
                            Note(
                                m, n.start + f.spb, min(n.n, f.total - n.start - f.spb)
                            )
                        )
                out.append(
                    Option(
                        "answer",
                        "Call and response",
                        "every other bar answers the one before: its rhythm, "
                        "the line turned upside down",
                        _lane_after(doc, key, with_notes(lane, call)),
                    )
                )
        if f.chords:
            onto = []
            for n in ns:
                pcs = chord_pcs(f, n.start) or set()
                onto.append(replace(n, m=_nearest(n.m, lambda pc, pcs=pcs: pc in pcs)))
            out.append(
                Option(
                    "chords",
                    "On the chords",
                    "every note moved to the nearest tone of the chord under it",
                    _lane_after(doc, key, with_notes(lane, onto)),
                )
            )
        else:
            out.append(
                Option(
                    "chords",
                    "On the chords",
                    "the loop has no chord lane yet: write one above the roll",
                    None,
                )
            )
        a = state.arrangement
        rows = f.rows
        for kind in gl.VARY:
            try:
                fields, _note = gl.vary(a, rows, kind, 0.3, seed, state.song)
            except gl.Refused as err:
                out.append(Option(kind, kind.capitalize(), str(err), None))
                continue
            new = _lane_doc(
                fields["cells"], fields["cuts"], fields["velocities"], fields["slides"]
            )
            out.append(
                Option(
                    kind,
                    kind.capitalize(),
                    "notes taken out where they matter least"
                    if kind == "sparser"
                    else "chord tones added on the silent off-beats",
                    _lane_after(doc, key, new),
                )
            )
    # the beat
    d = doc["drums"]
    if d["drums"]:
        hum = copy.deepcopy(d)
        for v, s in d["drums"]:
            _set_hit_vel(
                hum,
                v,
                int(s),
                min(
                    1.0,
                    max(
                        0.3,
                        hit_vel(d, v, int(s))
                        + random.Random(int(s) * 31 + len(v)).uniform(-0.12, 0.06),
                    ),
                ),
            )
        out.append(
            Option(
                "beat_human",
                "Beat: humanised",
                "every hit a little louder or softer",
                _drums_after(doc, hum),
            )
        )
        fill = copy.deepcopy(d)
        last = f.drum_total - max(1, f.drum_spb // 4)
        fill["drums"] = [
            c
            for c in fill["drums"]
            if not (int(c[1]) >= last and c[0] in _HATS + ("snare",))
        ]
        n = f.drum_total - last
        for k in range(n):
            fill["drums"].append(["snare", last + k])
            _set_hit_vel(fill, "snare", last + k, 0.5 + 0.5 * (k + 1) / n)
        fill["drums"].sort(key=lambda c: (c[1], c[0]))
        fill["drum_hits"] = {
            k: v
            for k, v in fill["drum_hits"].items()
            if [k.split(".")[0], int(k.split(".")[1])] in fill["drums"]
        }
        out.append(
            Option(
                "beat_fill",
                "Beat: fill into the top",
                "a snare roll in the last beat, rising",
                _drums_after(doc, fill),
            )
        )
        hats = [c for c in d["drums"] if c[0] == "hat"]
        if len(hats) >= 6:
            thin = copy.deepcopy(d)
            step2 = max(1, f.drum_spb // 8)
            thin["drums"] = [
                c
                for c in thin["drums"]
                if not (c[0] == "hat" and int(c[1]) % (2 * step2))
            ]
            out.append(
                Option(
                    "beat_sparse",
                    "Beat: lighter hats",
                    "the hats on the eighths only",
                    _drums_after(doc, thin) if thin["drums"] != d["drums"] else None,
                )
            )
    return out


# --- what a proposal changes ----------------------------------------------------------


def diff(before: dict, after: dict, f: Facts) -> dict:
    """Per lane: the cells added and removed, the velocities moved; the
    beat's hits added and removed. `grid` is what Perform's two grids can
    draw (the edited lane's cells, the beat's): the overlay's data."""
    lanes = {}
    for key in sorted(set(before["lanes"]) | set(after["lanes"])):
        a = before["lanes"].get(key) or _lane_doc([], [], {}, [])
        b = after["lanes"].get(key) or _lane_doc([], [], {}, [])
        ca = {(p, s) for p, s in a["cells"]}
        cb = {(p, s) for p, s in b["cells"]}
        na, nb = set(notes(a)), set(notes(b))
        va, vb = a["velocities"] or {}, b["velocities"] or {}
        vel = sum(
            1
            for s in {x.start for x in nb}
            if round(va.get(str(s), NOTE_VEL), 3) != round(vb.get(str(s), NOTE_VEL), 3)
        )
        if ca != cb or na != nb or vel:
            lanes[key] = {
                "add": sorted(cb - ca, key=lambda c: (c[1], c[0])),
                "del": sorted(ca - cb, key=lambda c: (c[1], c[0])),
                "notes_add": len(nb - na),
                "notes_del": len(na - nb),
                "vel": vel,
            }
    da = {(v, int(s)) for v, s in before["drums"]["drums"]}
    db = {(v, int(s)) for v, s in after["drums"]["drums"]}
    dvel = sum(
        1
        for v, s in db & da
        if round(hit_vel(before["drums"], v, s), 3)
        != round(hit_vel(after["drums"], v, s), 3)
    )
    drums = None
    if da != db or dvel:
        drums = {
            "add": sorted(db - da, key=lambda c: (c[1], c[0])),
            "del": sorted(da - db, key=lambda c: (c[1], c[0])),
            "vel": dvel,
        }
    key = str(f.active)
    grid = {
        "notes": {
            "add": [f"cell.{p}.{s}" for p, s in (lanes.get(key) or {}).get("add", [])],
            "del": [f"cell.{p}.{s}" for p, s in (lanes.get(key) or {}).get("del", [])],
        },
        "drums": {
            "add": [f"drum.{v}.{s}" for v, s in (drums or {}).get("add", [])],
            "del": [f"drum.{v}.{s}" for v, s in (drums or {}).get("del", [])],
        },
    }
    lines = []
    for k, d in lanes.items():
        bits = []
        if d["notes_add"]:
            bits.append(f"+{d['notes_add']}")
        if d["notes_del"]:
            bits.append(f"−{d['notes_del']}")
        what = " ".join(bits) + (" notes" if bits else "")
        if d["vel"]:
            what += (", " if what else "") + _plural(d["vel"], "velocity", "velocities")
        hidden = (
            ""
            if int(k) == f.active
            else " (not on the grid: select the lane to see it)"
        )
        lines.append(f"{lane_name(int(k))}: {what}{hidden}")
    if drums:
        bits = [
            x
            for x in (
                f"+{len(drums['add'])}" if drums["add"] else "",
                f"−{len(drums['del'])}" if drums["del"] else "",
            )
            if x
        ]
        what = " ".join(bits) + (" hits" if bits else "")
        if drums["vel"]:
            what += (", " if what else "") + _plural(drums["vel"], "level")
        lines.append(f"beat: {what}")
    return {"lanes": lanes, "drums": drums, "grid": grid, "lines": lines}


# --- the session ----------------------------------------------------------------------


def session(state) -> dict:
    """The coach's runtime on the session (`StudioState.coach`), made on
    first use."""
    if state.coach is None:
        # `sid`: this session's own, so the panel's memo never hands one
        # session's thread to another at the same revision
        state.coach = {
            "sid": uuid.uuid4().hex,
            "rev": 0,
            "next": 1,
            "proposal": None,
            "thread": [],
        }
    return state.coach


def _bump(c: dict) -> int:
    c["rev"] += 1
    n = c["next"]
    c["next"] += 1
    return n


def add_entry(state, entry: dict) -> dict:
    c = session(state)
    entry = {"id": _bump(c), **entry}
    c["thread"].append(entry)
    del c["thread"][:-THREAD_CAP]
    return entry


def propose(
    state, label: str, source: str, base: dict, after: dict, said=()
) -> dict | None:
    """`after` as the proposal waiting (replacing any other); None when it
    changes nothing."""
    if fingerprint(after) == fingerprint(base):
        return None
    c = session(state)
    c["proposal"] = {
        "id": _bump(c),
        "label": label,
        "source": source,
        "base": base,
        "after": after,
        "said": list(said)[:MAX_LINES],
    }
    return c["proposal"]


def discard(state) -> dict | None:
    c = session(state)
    p, c["proposal"] = c["proposal"], None
    if p:
        _bump(c)
    return p


def accept(state) -> tuple[dict | None, str]:
    """The proposal into the loop: ONE pattern undo step holding the whole
    loop (`_whole_loop`), remembered in the thread for ↶ Reverse. Refused
    (None, why) when nothing waits or the loop moved since it was made."""
    c = session(state)
    p = c["proposal"]
    if p is None:
        return None, "Nothing to accept: ask for a fix, an option or a change first."
    now = read(state)
    if fingerprint(now) != fingerprint(p["base"]):
        return None, "The loop changed since this was proposed — ask again."
    step = state.whole_loop_step()
    write(state, p["after"])
    state.push_pattern_undo(step)
    c["proposal"] = None
    entry = add_entry(
        state,
        {
            "kind": "accepted",
            "label": p["label"],
            "lines": diff(p["base"], p["after"], facts(state))["lines"],
            "before": p["base"],
            "after": p["after"],
            "step": step,
            "reversed": "",
        },
    )
    return entry, ""


def reverse(state, entry_id: int) -> tuple[str, str]:
    """↶ an accepted change: ("undone" | "restored", "") or ("", why).
    Undone: its pattern undo step was the newest and the loop is as it
    left it, so the step is popped (Ctrl+Z's own way). Restored: the loop
    as it was before the change, put back as a new undo step of its own
    (what came after it is gone with it — the thread says so)."""
    c = session(state)
    e = next(
        (x for x in c["thread"] if x["id"] == entry_id and x["kind"] == "accepted"),
        None,
    )
    if e is None:
        return "", "That change is no longer in the coach's thread."
    if e["reversed"]:
        return "", "That change was already reversed."
    now = read(state)
    if state.newest_pattern_step() is e["step"] and fingerprint(now) == fingerprint(
        e["after"]
    ):
        state.pop_pattern_undo()
        how = "undone"
    else:
        step = state.whole_loop_step()
        write(state, e["before"])
        state.push_pattern_undo(step)
        how = "restored"
    e["reversed"] = how
    c["proposal"] = None
    _bump(c)
    return how, ""


# --- the panel ------------------------------------------------------------------------

_MEMO: dict[str, dict] = {}


def context(state) -> dict:
    """What the panel draws (`fragments/coach_panel.html`, region
    `coach-panel`): the findings, the options, the proposal with its diff,
    the thread. Memoised on what it reads, so the region's watch (this,
    `repr`'d) costs one assessment per change of the loop."""
    doc = read(state)
    f = facts(state)
    c = state.coach or {"sid": "", "rev": 0, "proposal": None, "thread": []}
    top = state.newest_pattern_step()
    # the accepted change ↶ Reverse would undo (rather than restore over)
    undo = next(
        (
            e["id"]
            for e in c["thread"]
            if e["kind"] == "accepted"
            and not e["reversed"]
            and e["step"] is top
            and fingerprint(e["after"]) == fingerprint(doc)
        ),
        None,
    )
    chords = f.chords and [(x["lo"], x["hi"], x["tones"], x["root"]) for x in f.chords]
    memo_key = json.dumps(
        [
            doc,
            f.total,
            f.spb,
            f.beat,
            f.drum_total,
            f.drum_spb,
            f.key,
            sorted(f.keys),
            chords,
            f.rows,
            f.active,
            f.names,
            c["sid"],
            c["rev"],
            undo,
            f.meter,
            state.follows(state.active_layer),
        ],
        sort_keys=True,
        default=str,
    )
    got = _MEMO.get(memo_key)
    if got is not None:
        return got
    finds = assess(doc, f)
    fixable = [x for x in finds if x.after]
    p = c["proposal"]
    prop = None
    if p:
        stale = fingerprint(p["base"]) != fingerprint(doc)
        d = diff(p["base"], p["after"], f)
        prop = {
            "id": p["id"],
            "label": p["label"],
            "said": p["said"],
            "lines": d["lines"],
            "stale": stale,
            "grid": None if stale else d["grid"],
        }
    out = {
        "follows": state.follows(state.active_layer),
        "lane": lane_name(state.active_layer),
        "key": f.key,
        "findings": [
            {
                "id": x.id,
                "title": x.title,
                "why": x.why,
                "fix": x.after is not None,
                "show": x.show,
                "lane": x.lane,
            }
            for x in finds
        ],
        "fixable": len(fixable),
        "options": [
            {"id": o.id, "label": o.label, "why": o.why, "on": o.after is not None}
            for o in options(state, doc, f)
        ],
        "proposal": prop,
        "thread": [
            {k: v for k, v in e.items() if k not in ("before", "after", "step")}
            for e in c["thread"]
        ],
        "undo": undo,
    }
    if len(_MEMO) > 8:
        _MEMO.clear()
    _MEMO[memo_key] = out
    return out


def watch(state) -> str:
    return repr(context(state))


def option_after(state, oid: str) -> tuple[dict, dict, str] | None:
    """(base, after, label) of the option `oid` on the loop as it is."""
    doc, f = read(state), facts(state)
    for o in options(state, doc, f):
        if o.id == oid and o.after is not None:
            return doc, o.after, o.label
    return None


def finding_after(state, fid: str) -> tuple[dict, dict, str] | None:
    doc, f = read(state), facts(state)
    if fid == "all":
        after, said = clean_all(doc, f)
        return (doc, after, "Clean up all") if said else None
    for x in assess(doc, f):
        if x.id == fid and x.after is not None:
            return doc, x.after, f"Fix: {x.title}"
    return None


@contextmanager
def with_doc(state, doc: dict):
    """The working loop as `doc` for the length of the block, then exactly
    as it was: what ▶ proposed renders (`svc.render_inputs`), under the
    lock, without the session ever holding it."""
    arr = state.arrangement
    held = [(lay.get("cells"), copy.deepcopy(lay.get("seq"))) for lay in state.layers]
    try:
        write(state, doc)
        yield
    finally:
        state.arrangement = arr
        for lay, (cells, seq) in zip(state.layers, held, strict=True):
            lay["cells"] = cells
            lay["seq"] = seq
