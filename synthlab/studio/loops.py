"""Loops: what ⏺ Rec makes, and a keys take or Perform's lane kept as what
plays it (one Rec = a take that is both).

The flow: play over the loop, and get both the audio clip AND the actual
loop — every key, its position from the selected bar and rate, saved
exactly as played — to adjust and then move into the Song page. A loop is

    arr     the note lane (bars, steps_per_bar, cells, cuts, velocities,
            slides), the tempo, the key and the meter: an Arrangement's own
            fields (schema.ARRANGEMENT; the fields a loop does not carry
            default)
    sound   the whole sound that plays it: the edited lane's patch, the
            other lanes, the main lane's FX and the curves, as a format-2
            preset holds them (`StudioState.sound_payload`)
    take    the clip of its audio, when it has one: the keys take ⏺ Rec
            recorded it from (keytakes.py), or the clip the clip hub made
            its notes from — audio and loop are one object
    played  the notes exactly as played, [start, length, midi, velocity],
            beats from the take's first downbeat: never rewritten. The grid
            is their placement (`place`) at a rate and a quantise strength
            (`quantise`, 0-100: 0 = each note in the step it began in, 100 =
            the nearest step); placing again starts from them
    edited  the grid was changed by hand since it was placed
    name, at
    kind    "beat" for a beat (the Loops panel): its drum lane is
            `beat`, a PATTERN as the Drums bank keeps one (steps, bars,
            steps a bar, swing, hits, row swings) — never in `arr`, whose
            note lane stays empty and which keeps the tempo, key and meter;
            "idea" for an idea (Perform's launcher): a scene kept
            — `idea`, the loop in each lane's slot (the four layer slots,
            then the drums), `arr` its tempo, key and meter
    star    ★, a favourite
    tags    the loop's tags (lower case, no "#")

(the last five absent when unset: a loop from before reads as it did).
A loop recorded or kept is NAMED FROM WHAT IT IS — its sound and what it
plays: "Acid bass riff" (`auto_name`; its key, tempo and bars are the
lists' fact cells, not its name); a loop an older version named by its
time ("keys-20261107-150616") keeps its file and SHOWS that
name (`title`), until it is renamed; a name an older version baked the
facts into ("Acid bass riff · Am · 124 · 2b") keeps its file and shows
without them while they are its own (`plain`).

in studio_data/loops/<name>.json, written in its conformed form
(`schema.nodes().LOOP`) and read back through it — a file anyone can edit.
Pure functions on the store: the routes (`routes/takes.py`) put a loop into
the session, and the one Loops list is `looplib.py`'s.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

from .. import midifile as mf
from ..sequencer import NOTE_NAMES, SCALES, midi, name_of
from . import limits, midi_bridge, schema
from . import meter as _meter
from .files import dir_sig, rename_file, safe_name
from .files import free_name as free_name_in
from .lru import LRU
from .state.model import (
    DRUM_ROWS,
    LOOP_FIELDS,
    NOTE_LANE,
    Arrangement,
    preset_from_json,
)

DIR = "loops"
BAR_CHOICES = (1, 2, 4)  # what Save as loop offers
KEY = ("bpm", "root", "scale_name")
SAVED = LOOP_FIELDS  # an Arrangement's fields a loop keeps
GRID_BARS = (1, 2, 3, 4)  # a placed take's grid: the Arrangement's own
# a step's length in sixteenths, as a note value
_RATE_WORDS = {
    Fraction(4): "1/4",
    Fraction(2): "1/8",
    Fraction(1): "1/16",
    Fraction(1, 2): "1/32",
}


def loop_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / DIR
    d.mkdir(exist_ok=True)
    return d


# a loop's name may also hold the "·" and "#" its generated name has
NAME_EXTRA = "·#"
# a name given by the clock, not by anyone: ⏺ Rec's "keys-20261107-150616"
# (keyrec.js) — shown as what the loop is (`title`)
AUTO = re.compile(r"^(?:keys|beat)-\d{8}-\d{6}(?: \d+)?$")


def loop_safe(name: str) -> str:
    """The loop store's sanitizer: `safe_name` with "·" and "#" (a
    generated name's), "loop" for a degenerate name (`clips.clip_safe`'s
    twin)."""
    return safe_name(name, "loop", extra=NAME_EXTRA)


def is_auto(name: str) -> bool:
    """Was this name given by the clock (`AUTO`)? Such a loop shows what it
    is (`title`) in its place."""
    return bool(AUTO.match(name))


# --- names: what a loop is --------------------------------------------------------

_SCALE_WORDS = {
    "major": "",
    "minor": "m",
    "harmonic_minor": "m harm",
    "pentatonic_minor": "m pent",
    "pentatonic_major": " pent",
    "blues": " blues",
    "whole_tone": " whole",
}


def key_short(root: str, scale: str) -> str:
    """A key as a name says it: "Am", "C", "F#m pent", "D dor"."""
    note = root.rstrip("-0123456789") or "C"
    word = _SCALE_WORDS.get(scale)
    return note + (word if word is not None else f" {scale[:3]}")


def key_rank(root: str, scale: str) -> int:
    """A key's place in a sort by key: its tonic up from C, then its scale
    (`SCALES`' order); -1 when it is not a key."""
    note = root.rstrip("-0123456789")
    try:
        pc = NOTE_NAMES.index(note[0].upper())
    except (IndexError, ValueError):
        return -1
    pc = (pc + note.count("#") - note[1:].count("b")) % 12
    order = list(SCALES)
    return pc * 100 + (order.index(scale) if scale in order else 99)


def plays(lane: dict) -> str:
    """What a note lane plays, as its name says it: "chords" when three
    notes or more start together anywhere, else "riff" ("loop": no
    notes)."""
    held = {(p, int(st)) for p, st in lane["cells"]}
    if not held:
        return "loop"
    cuts = {(p, int(st)) for p, st in lane["cuts"]}
    starts: dict[int, int] = {}
    for p, st in held:  # a note starts where its row was silent, or at a cut
        if (p, st - 1) not in held or (p, st) in cuts:
            starts[st] = starts.get(st, 0) + 1
    return "chords" if max(starts.values()) >= 3 else "riff"


def _what(sound: str, word: str) -> str:
    """ "Acid bass riff": the sound and what it plays, the word once ("Pad
    chords", never "Pad chords chords")."""
    sound = sound.strip() or "Loop"
    return sound if sound.casefold().endswith(word) else f"{sound} {word}"


def title(sound: str, lane: dict, key: dict, bars: int, *, beat: bool = False) -> str:
    """A loop as its name says it: the sound and what it plays — "Acid bass
    riff", "Techno beat". Plain: its key, tempo and bars are facts every
    list shows once, in cells beside the name (`looplib.facts`), never
    baked in ("Acid bass riff · Am · 124 · 2b") for the row to say
    again. (`key` and `bars` stay in the signature: the callers
    name a loop from what it is.)"""
    return _what(sound, "beat" if beat else plays(lane))


def baked(key: dict, bars: int, *, beat: bool = False) -> str:
    """The suffix an older version's auto-name carried (" · Am · 124 ·
    2b"; a beat's without the key) — what `plain` takes off a stored name
    that still has it."""
    bits = [] if beat else [key_short(key["root"], key["scale_name"])]
    return " · " + " · ".join([*bits, f"{round(key['bpm'])}", f"{bars}b"])


_DUP = re.compile(r"^(.*?)( \d+)?$")


def plain(name: str, key: dict, bars: int, *, beat: bool = False) -> str:
    """A stored name as the lists show it: an older version's auto-name
    loses its baked suffix when it says exactly the loop's own key, tempo
    and bars ("Acid bass riff · Am · 124 · 1b" -> "Acid bass riff", its
    "… 2" kept). The file keeps its name; any other name shows as it is."""
    head, dup = _DUP.match(name).groups()
    tail = baked(key, bars, beat=beat)
    if len(head) > len(tail) and head.endswith(tail):
        return head[: -len(tail)] + (dup or "")
    return name


def auto_name(
    data_dir: Path, sound: str, lane: dict, key: dict, bars: int, **kw
) -> str:
    """A free name for a new loop from what it is (`title`): "… 2" when
    another loop has it."""
    return free_name(data_dir, title(sound, lane, key, bars, **kw))


def _path(data_dir: Path, name: str) -> Path:
    return loop_dir(data_dir) / f"{loop_safe(name)}.json"


def names(data_dir: Path) -> list[str]:
    return sorted(p.stem for p in loop_dir(data_dir).glob("*.json"))


def free_name(data_dir: Path, name: str) -> str:
    """`name` as a loop's file stem, or "name 2", "name 3" ... when taken
    (case-folded, as Windows sees it: `files.free_name`, the clip store's
    rule)."""
    return free_name_in(loop_dir(data_dir), name, loop_safe, (".json",))


# --- the rate and the placement ---------------------------------------------------


def rate_word(meter: str, steps: int) -> str:
    """A step as a note value: "1/16" (a 4/4 bar of 16), "1/8"; a step no
    note value names reads as the steps a bar ("12 a bar")."""
    return _RATE_WORDS.get(_meter.step_length(meter, steps), f"{steps} a bar")


def rates(meter: str) -> list[tuple[int, str]]:
    """The rates a loop in `meter` may be placed at, coarsest first: the
    meter's own step choices (`meter.step_choices`) — 1/4 to 1/32 in 4/4,
    no quarter-note step in an x/8 meter, no triplets (the note grid has no
    triplet steps in a duple meter)."""
    return [(n, rate_word(meter, n)) for n in _meter.step_choices(meter)]


def default_steps(meter: str) -> int:
    """The rate a take is placed at first: the meter's sixteenths."""
    return _meter.sixteenths_choice(meter)


def grid_bars(take_bars: float, steps: int) -> int:
    """The bars a take of `take_bars` is placed on: the take rounded up to
    whole bars, at most the grid's longest that holds the rate
    (`GRID_BARS`, `limits.MAX_GRID_STEPS`); a longer take keeps the rest in
    its notes as played."""
    want = max(1, math.ceil(take_bars - 1e-6)) if take_bars else 1
    fits = [b for b in GRID_BARS if b * steps <= limits.MAX_GRID_STEPS]
    return min(want, max(fits)) if fits else 1


def _step_of(t: float, strength: int) -> int:
    """The step a note that began `t` steps in is placed on: the nearest
    line when it is within `strength`% of half a step of it, else the step
    it began in — 100 snaps every note, 0 leaves each where it was played."""
    near = round(t)
    return near if abs(t - near) <= strength / 200.0 + 1e-9 else math.floor(t)


def place(notes, meter: str, bars: int, steps: int, strength: int = 100) -> dict:
    """The notes as played on a note grid of `bars` x `steps` in `meter`:
    the note lane's fields (`NOTE_LANE`). Each note starts on its step
    (`_step_of`) and is held for its length in whole steps (at least one);
    one that starts where its row still sounds is a cut; a step's velocity
    is the loudest note starting there (the MIDI import's write,
    `midi_bridge.to_grid`, on every pitch the notes play). Notes past the
    grid's last bar stay in the notes, off the grid."""
    step = _meter.bar_beats(meter) / steps
    lane = {
        "bars": bars,
        "steps_per_bar": steps,
        "cells": [],
        "cuts": [],
        "velocities": {},
        "slides": [],
        "meter": meter,
    }
    track = mf.Track(
        name="",
        notes=[
            mf.Note(
                _step_of(s / step, strength) * step,
                max(1, round(ln / step)) * step,
                int(m),
                max(1, min(127, round(v * 127))),
            )
            for s, ln, m, v in notes
        ],
    )
    rows = sorted({name_of(int(n[2])) for n in notes})
    try:
        out, _said = midi_bridge.to_grid(track, lane, rows)
    except midi_bridge.ImportRefused:
        out = {k: lane[k] for k in ("cells", "cuts", "velocities", "slides")}
    # (on the steps: no timing inside one — a place again clears a free one)
    return {"bars": bars, "steps_per_bar": steps, **out, "note_nudge": {}}


MAX_SWING = 0.5  # a free loop's swing: a share of a step on the odd ones


def shaped_start(t: float, strength: int, swing: float = 0.0) -> float:
    """Where a note that starts `t` steps in is heard in a free loop (free
    record's shaper, `place_free`): pulled `strength`% of the way to its
    nearest line — every step's, the odd ones `swing` of a step late — so
    0 is exactly as shaped and 100 on the line. The shaper's preview
    (static/shaper.js `shapedStart`) is this, line for line."""
    lo = math.floor(t)
    lines = [k + (swing if k % 2 else 0.0) for k in (lo - 1, lo, lo + 1, lo + 2)]
    line = min(lines, key=lambda x: (abs(x - t), x))
    return t + (line - t) * max(0, min(100, strength)) / 100.0


def place_free(
    notes,
    meter: str,
    bars: int,
    steps: int,
    strength: int = 0,
    swing: float = 0.0,
) -> dict:
    """A free loop's notes (beats from its start, as shaped: free record's
    shaper) on a note grid of `bars` x `steps`, KEEPING THEIR TIMING: each
    starts where `shaped_start` puts it, on its nearest step with the rest
    as that step's `note_nudge` (a share of a step, -0.5..0.5) — the mean
    of the notes starting there, which share it as they share a velocity.
    A note that lands past the grid's end starts its first step early (the
    loop is a circle: the render wraps it to the end). Held to the step it
    ends nearest (at least one); the cells, cuts and velocities as `place`
    writes them. The note lane's fields."""
    step = _meter.bar_beats(meter) / steps
    total = bars * steps
    swing = max(0.0, min(MAX_SWING, float(swing)))
    placed, offs = [], {}
    for s, ln, m, v in notes:
        at = shaped_start(float(s) / step, strength, swing)
        k = round(at)
        if not 0 <= k < total + 1:
            continue  # past the grid (a crop keeps none): never placed
        run = max(1, round(at + float(ln) / step) - k)
        k %= total
        placed.append((k, min(run, total), int(m), v))
        offs.setdefault(k, []).append(at - round(at))
    track = mf.Track(
        name="",
        notes=[
            mf.Note(k * step, run * step, m, max(1, min(127, round(v * 127))))
            for k, run, m, v in placed
        ],
    )
    lane = {
        "bars": bars,
        "steps_per_bar": steps,
        "cells": [],
        "cuts": [],
        "velocities": {},
        "slides": [],
        "meter": meter,
    }
    rows = sorted({name_of(int(n[2])) for n in placed})
    try:
        out, _said = midi_bridge.to_grid(track, lane, rows)
    except midi_bridge.ImportRefused:
        out = {k: lane[k] for k in ("cells", "cuts", "velocities", "slides")}
    nudge = {
        str(k): round(max(-0.5, min(0.5, sum(o) / len(o))), 4) for k, o in offs.items()
    }
    out["note_nudge"] = schema.clean_nudge(nudge, out["cells"])
    return {"bars": bars, "steps_per_bar": steps, **out}


# --- the store -------------------------------------------------------------------


def _write(data_dir: Path, name: str, raw: dict) -> None:
    clean = schema.conform(raw, schema.nodes().LOOP)
    _path(data_dir, name).write_text(
        json.dumps(clean, separators=(",", ":")), encoding="utf-8"
    )


def _raw(data_dir: Path, name: str) -> dict | None:
    """A loop's file, conformed (`schema.nodes().LOOP`), or None."""
    path = _path(data_dir, name)
    try:
        if path.stat().st_size > limits.MAX_LOOP_BYTES:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return schema.conform(raw, schema.nodes().LOOP)
    except (OSError, ValueError, RecursionError):
        return None


def save(
    data_dir: Path,
    name: str,
    arr: Arrangement,
    sound: dict | None,
    *,
    take: str | None = None,
    played=(),
    quantise: int = 100,
    beat: dict | None = None,
    free: dict | None = None,
) -> str:
    """Keep a loop: `arr`'s note lane, tempo, key and meter, and `sound` (a
    format-2 preset payload, or None) — with the clip of its audio (`take`)
    and the notes as played (`played`) when it was recorded; or a beat
    (`beat`: a drum PATTERN, `arr` its tempo, key and meter and no notes);
    or a free loop (`free`: `{swing, take}`, its `played` the take as
    shaped, `place_free`). The name it was kept under (never over another
    loop). Refused past `limits.MAX_LOOPS`."""
    if len(names(data_dir)) >= limits.MAX_LOOPS:
        raise OSError(f"There are {limits.MAX_LOOPS} loops: delete one first.")
    kept = free_name(data_dir, name)
    full = asdict(arr)
    raw = {
        "v": 1,
        "name": kept,
        "at": round(time.time(), 3),
        # the chance lane only when a step has one (`schema.ARR_OPTIONAL`)
        "arr": {k: full[k] for k in SAVED}
        | ({"note_chance": full["note_chance"]} if full["note_chance"] else {}),
        "sound": {k: v for k, v in (sound or {}).items() if k != "format"} or None,
        "take": take,
        "played": [list(n) for n in played],
        "quantise": quantise,
        "edited": False,
        "beat": beat,
        "free": free,
    }
    _write(data_dir, kept, raw)
    return kept


def save_beat(data_dir: Path, name: str, pattern: dict, arr: Arrangement) -> str:
    """Keep a beat as a loop (kind "beat"): `pattern`, the drum lane as the
    bank keeps it (`StudioState.pattern_from_arrangement`), with `arr`'s
    tempo, key and meter. A beat loop of that name already there is
    written over (Drums' Save keeping the same beat again); otherwise a
    new loop. Its name."""
    beat = schema.conform(pattern, schema.nodes().PATTERN)
    have = load(data_dir, name) if exists(data_dir, name) else None
    tempo = {k: getattr(arr, k) for k in (*KEY, "meter")}
    if have is not None and have["kind"] == "beat":
        update(data_dir, have["name"], beat=beat, arr=tempo | {"bars": beat["bars"]})
        return have["name"]
    empty = Arrangement(**tempo, bars=beat["bars"], cells=[])
    return save(data_dir, name, empty, None, beat=beat)


def save_idea(data_dir: Path, name: str, slots: list, arr: Arrangement) -> str:
    """Keep a scene of Perform's launcher as an idea (kind "idea"): the
    loop in each lane's slot (the layer slots, then the drums; None for an
    empty one) with `arr`'s tempo, key and meter. A new loop: the name it
    was kept under."""
    tempo = {k: getattr(arr, k) for k in (*KEY, "meter")}
    empty = Arrangement(**tempo, cells=[])
    kept = save(data_dir, name, empty, None)
    update(data_dir, kept, idea=list(slots))
    return kept


def clean_tags(text: str) -> list[str]:
    """Tags as typed ("dark, #Bass  wide") as a loop keeps them: lower
    case, no "#", each once (`schema.nodes().LOOP`'s own rule)."""
    raw = [t for t in re.split(r"[\s,;]+", text or "") if t]
    return schema.conform({"tags": raw}, schema.nodes().LOOP).get("tags", [])


def update(data_dir: Path, name: str, **changes) -> bool:
    """Rewrite one loop: `arr` (fields of its arrangement: the note lane,
    the tempo, key, meter), `quantise`, `edited`, `take`, `name`, `star`,
    `tags`, `beat`. False when it is not there (or not a loop)."""
    raw = _raw(data_dir, name)
    if raw is None:
        return False
    if "arr" in changes:
        raw["arr"] = {**raw["arr"], **changes.pop("arr")}
    raw |= changes
    _write(data_dir, _path(data_dir, name).stem, raw)
    return True


def load(data_dir: Path, name: str) -> dict | None:
    """A loop as the session takes it: `{name, at, lane (the note lane's
    fields), key (bpm, root, scale_name), meter, sound ((patch, extras) or
    None), take, played, quantise, edited, kind ("notes" | "beat"), star,
    tags, beat (a PATTERN, or None)}`; None when there is no such loop or
    its file is not one."""
    clean = _raw(data_dir, name)
    if clean is None:
        return None
    arr = clean["arr"]
    sound = clean.get("sound")
    return {
        "name": _path(data_dir, name).stem,
        "at": clean["at"],
        # (and its chance lane, {} when it has none: absent from the file)
        "lane": {k: arr.get(k, {}) for k in NOTE_LANE}  # (note_nudge: absent unset)
        | {"note_chance": dict(arr.get("note_chance") or {})},
        "key": {k: arr[k] for k in KEY},
        "meter": arr.get("meter", _meter.DEFAULT),
        "sound": preset_from_json({"format": 2, **sound}) if sound else None,
        "take": clean["take"],
        "played": clean["played"],
        "quantise": clean["quantise"],
        "edited": clean["edited"],
        "kind": clean.get("kind") or "notes",
        "star": bool(clean.get("star")),
        "tags": list(clean.get("tags") or []),
        "beat": clean.get("beat"),
        "idea": clean.get("idea"),
        "free": clean.get("free"),
    }


# --- the listing: each file read once, by its (name, mtime, size) -----------------

_ROWS: LRU[tuple] = LRU(1024)


def _row(path: Path, stat: tuple) -> dict | None:
    key = f"{path}|{stat[0]}|{stat[1]}"
    hit = _ROWS.get(key)
    if hit is None:
        loop = load(path.parent.parent, path.stem)
        hit = (None if loop is None else _summary(loop),)
        _ROWS.put(key, hit)
    return dict(hit[0]) if hit[0] is not None else None


def _summary(loop: dict) -> dict:
    lane, key, beat, idea = loop["lane"], loop["key"], loop["beat"], loop["idea"]
    patch = loop["sound"][0] if loop["sound"] else None
    sound = patch.name if patch is not None else ""
    bars = beat["bars"] if beat else lane["bars"]
    # a beat is called what the bank or its style called it
    what = title(beat["name"], lane, key, bars, beat=True) if beat else None
    if idea:
        roll, notes = idea_roll(idea), sum(1 for x in idea if x)
    else:
        roll = beat_roll(beat) if beat else roll_of(lane)
        notes = len(beat["steps"]) if beat else len(lane["cells"])
    return {
        "name": loop["name"],
        # what the lists call it: its name — or, a name the clock gave,
        # what it is
        "title": (what or title(sound, lane, key, bars))
        if is_auto(loop["name"])
        else plain(loop["name"], key, bars, beat=bool(beat)),
        "kind": loop["kind"],
        "star": loop["star"],
        "tags": loop["tags"],
        "at": loop["at"],
        "bars": bars,
        "steps": beat["steps_per_bar"] if beat else lane["steps_per_bar"],
        "meter": loop["meter"],
        "bpm": round(key["bpm"]),
        "root": key["root"],
        "scale": key["scale_name"],
        "notes": notes,
        "sound": sound,
        "take": loop["take"],
        "played": len(loop["played"]),
        "quantise": loop["quantise"],
        "edited": loop["edited"],
        "roll": roll,
        "beat_key": beat_key(beat) if beat else None,
        "idea": list(idea) if idea else None,
        # a free loop: the free take it was shaped from (✂ Shape again)
        "free_take": (loop["free"] or {}).get("take") if loop["free"] else None,
        "free": bool(loop["free"]),
    }


def beat_key(pattern: dict) -> str:
    """A beat by what it plays, its name aside: a Drums bank slot holding
    the beat a beat loop holds is that loop (looplib.py lists it once)."""
    p = schema.conform(pattern, schema.nodes().PATTERN)
    return json.dumps({k: v for k, v in p.items() if k != "name"}, sort_keys=True)


def listing(data_dir: Path) -> list[dict]:
    """Every loop, newest first: `_summary` — name, title, kind, star,
    tags, at, bars, steps, meter, bpm, key, notes (cells; a beat's hits),
    the sound's name, its take, how many notes as played, quantise, edited
    and the sparkline (`roll_of`; a beat's `beat_roll`). A file is read
    again only when its own mtime or size moved."""
    d = loop_dir(data_dir)
    rows = [
        r
        for n, mtime, size in sig(data_dir)
        if (r := _row(d / n, (mtime, size))) is not None
    ]
    rows.sort(key=lambda x: (-x["at"], x["name"]))
    return rows


def sig(data_dir: Path) -> tuple:
    """The store's listing (names, mtimes, sizes): a watch, nothing read
    (`files.dir_sig`)."""
    return dir_sig(Path(data_dir) / DIR, ".json")


def exists(data_dir: Path, name: str) -> bool:
    return _path(data_dir, name).is_file()


def rename(data_dir: Path, old: str, new: str) -> str | None:
    """Rename a loop; the name it took, or None (no such loop, or the new
    name is another loop's). A rename that only changes the case moves the
    file through `files.rename_file`, as a clip's does — on Windows the
    case never changed otherwise."""
    src = _path(data_dir, old)
    want = loop_safe(new)
    if not src.is_file():
        return None
    if want == src.stem:
        return want
    dst = _path(data_dir, want)
    if dst.exists() and not dst.samefile(src):
        return None
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (ValueError, RecursionError):
        raw = None  # not a loop `load` reads: it moves as it is
    rename_file(src, dst)
    if isinstance(raw, dict):
        raw["name"] = want
        dst.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
    return want


def delete(data_dir: Path, name: str) -> bool:
    path = _path(data_dir, name)
    if not path.exists():
        return False
    path.unlink()
    return True


def clip_renamed(data_dir: Path, old: str, new: str) -> None:
    """A clip renamed anywhere (`StudioState.rename_clip_everywhere`): a
    loop whose audio it is follows it."""
    for row in listing(data_dir):
        if row["take"] == old:
            update(data_dir, row["name"], take=new)


def of_take(data_dir: Path, take: str) -> str | None:
    """The loop whose audio the clip `take` is, or None."""
    return next((r["name"] for r in listing(data_dir) if r["take"] == take), None)


# --- the sparkline ------------------------------------------------------------------


def roll_of(lane: dict) -> dict:
    """A note lane as a mini piano roll: `{d, w, h}` — one SVG path of
    horizontal runs (`M<step> <row>h<steps>`), a row a pitch from the
    highest down, over `w` steps and `h` rows. A run is a held note: its
    adjacent cells, split at a cut."""
    total = max(1, int(lane["bars"]) * int(lane["steps_per_bar"]))
    by_pitch: dict[int, list[int]] = {}
    for pitch, step in lane["cells"]:
        if 0 <= int(step) < total:
            by_pitch.setdefault(midi(pitch), []).append(int(step))
    if not by_pitch:
        return {"d": "", "w": total, "h": 1}
    cuts = {(midi(p), int(s)) for p, s in lane["cuts"]}
    return _path_of(by_pitch, cuts, total)


def idea_roll(slots) -> dict:
    """An idea as its lanes: a bar a lane that holds a loop (the drums
    last), drawn as `roll_of` draws a run."""
    bits = [f"M0 {i + 0.5:g}h4" for i, name in enumerate(slots) if name]
    return {"d": "".join(bits), "w": 4, "h": len(slots)}


def beat_roll(pattern: dict) -> dict:
    """A beat as a mini step grid, drawn as `roll_of` draws notes: a row a
    voice in the drum grid's order (`DRUM_ROWS`), a dash a hit."""
    total = max(1, int(pattern["bars"]) * int(pattern["steps_per_bar"]))
    bits = sorted(
        {
            f"M{int(st)} {DRUM_ROWS.index(v) + 0.5:g}h0.7"
            for v, st in pattern["steps"]
            if v in DRUM_ROWS and 0 <= int(st) < total
        }
    )
    return {"d": "".join(bits), "w": total, "h": len(DRUM_ROWS)}


def roll_of_notes(notes, bars: float) -> dict:
    """Notes in beats ([start, length, midi, velocity]) as `roll_of` draws
    a lane, on sixteenths: a take that has no loop yet."""
    total = max(1, round(max(bars, 0.25) * 16))
    by_pitch: dict[int, list[int]] = {}
    cuts = set()
    for s, ln, m, _v in notes:
        a = round(s * 4)
        if a >= total:
            continue
        cuts.add((int(m), a))
        steps = range(a, min(total, a + max(1, round(ln * 4))))
        by_pitch.setdefault(int(m), []).extend(steps)
    if not by_pitch:
        return {"d": "", "w": total, "h": 1}
    return _path_of(by_pitch, cuts, total)


def _path_of(by_pitch: dict[int, list[int]], cuts: set, total: int) -> dict:
    hi, lo = max(by_pitch), min(by_pitch)
    h = max(6, hi - lo + 1)
    top = hi + (h - (hi - lo + 1)) // 2  # a narrow range sits mid-height
    bits = []
    for m, steps in sorted(by_pitch.items()):
        run: list[int] = []
        for s in sorted(set(steps)) + [None]:
            if run and (s is None or s != run[-1] + 1 or (m, s) in cuts):
                bits.append(f"M{run[0]} {top - m + 0.5:g}h{len(run)}")
                run = []
            if s is not None:
                run.append(s)
    return {"d": "".join(bits), "w": total, "h": h}
