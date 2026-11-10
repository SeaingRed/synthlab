"""The song arranger's model: tracks (rows), a shared pattern pool, and
sections (columns) — pure functions over the plain JSON-able dict that
`schema.nodes().SONG` defines. No StudioState, no I/O.

    song = {bpm, root, scale_name, swing, drum_swing, drum_humanize,
            tracks: [Track], patterns: {id: Pattern}, sections: [Section]}

The sound lives on the track (patch / kit / fx); the notes live in the
pattern, which is shared by reference — every section whose cell names it
plays the same notes. A pattern's lanes use exactly the `Arrangement`'s
field names and value shapes. A notes pattern's `cells`, `velocities`,
`slides`, `bars`, `steps_per_bar`, `arp_mode` and `arp_octaves` map onto
the Arrangement's fields of the same names with no conversion. A drums
pattern's `drums` and `drum_vel` do too, but its clock is `bars` / `steps`,
which become the Arrangement's `drum_bars` / `drum_steps` — the two
renames a section -> Arrangement bridge must make. What changes over a
section is its automation lanes (`set_lane`, "automation lanes" below):
per (section, track), over one pass of the section.

Every function here leaves the song a schema fixed point. A refusal the UI
must explain is either a return value (the remove_* functions return the
names of the sections in the way) or a `SongError`, whose message is fit
for a toast.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import secrets
from bisect import bisect_left
from dataclasses import dataclass, field, replace
from typing import Any

from ..core import SR, n_samples
from ..sequencer import SCALES, midi, name_of
from . import limits, schema
from . import meter as _meter
from .curves import (
    FX_LANES,
    GAIN,
    GAIN_UNITY,
    LANE_TARGETS,
    fx_target,
    lane_range,
    parse_fx,
    unit_id_ok,
)

# pattern / section colours, in the order new ones are handed out
PALETTE: tuple[str, ...] = (
    "#7c8cff",
    "#ff7ca8",
    "#5fd4a4",
    "#ffb35c",
    "#b784ff",
    "#5cc8ff",
    "#e6d45c",
    "#ff8a5c",
)

TRACK_KINDS: dict[str, int] = {
    "drums": limits.MAX_DRUM_TRACKS,
    "synth": limits.MAX_SYNTH_TRACKS,
    "audio": limits.MAX_AUDIO_TRACKS,
}
# What a track is FOR (`track["role"]`, optional): starters and generators
# set it, "Add loop to song" guesses it, and the checklist, auto-arrange
# and (later) Balance read it. A hand-made track has none.
TRACK_ROLES: tuple[str, ...] = (
    "drums",
    "bass",
    "chords",
    "lead",
    "pad",
    "arp",
    "vocal",
    "fx",
)
ROLE_LABELS = {
    "drums": "Drums",
    "bass": "Bass",
    "chords": "Chords",
    "lead": "Lead",
    "pad": "Pad",
    "arp": "Arp",
    "vocal": "Vocal",
    "fx": "FX",
}
# a chord slot's quality (`gen/chordtrack.py` voices them) and a pattern's
# place in its family of variants
CHORD_QUALITIES: tuple[str, ...] = ("triad", "7th", "9th", "sus2", "sus4", "power")
PATTERN_VARIANTS: tuple[str, ...] = ("A", "B", "C", "D", "fill")
PATTERN_KINDS = ("drums", "notes")
CELL_KIND = {"drums": "drums", "synth": "notes"}  # track kind -> pattern kind
PRIME = "′"  # U+2032: "Riff ′" is Riff's first unique copy


class SongError(ValueError):
    """An operation the song refuses; the message is fit for a toast."""


def new_id() -> str:
    return secrets.token_hex(4)


def _fresh_id(song: dict) -> str:
    taken = {t["id"] for t in song["tracks"]} | set(song["patterns"])
    taken |= {s["id"] for s in song["sections"]}
    taken |= {x["id"] for x in long_items(song)}
    while (i := new_id()) in taken:  # pragma: no cover - 1 in 4e9
        pass
    return i


def _primed(name: str) -> str:
    return f"{name[: limits.MAX_TITLE_CHARS - 2]} {PRIME}"


def _index(x) -> int:
    """A list position from an untrusted value: an int, or an integral
    finite float; anything else is a SongError, never a ValueError."""
    if isinstance(x, bool) or not isinstance(x, int | float):
        raise SongError("Not a position.")
    if isinstance(x, float) and not (math.isfinite(x) and x.is_integer()):
        raise SongError("Not a position.")
    return int(x)


def _nodes():
    return schema.nodes()


# --- lookups ----------------------------------------------------------------------


def track_index(song: dict, track_id) -> int | None:
    return next((i for i, t in enumerate(song["tracks"]) if t["id"] == track_id), None)


def section_index(song: dict, section_id) -> int | None:
    return next(
        (i for i, s in enumerate(song["sections"]) if s["id"] == section_id), None
    )


def audible(song: dict, track: dict) -> bool:
    """Whether a track sounds in the mix: not muted, and, while any track
    is soloed, soloed itself. A silent track keeps its cells and pattern
    clocks (`song_bridge.section_loop` drops only the sound)."""
    if track["mute"]:
        return False
    return bool(track["solo"]) or not any(t["solo"] for t in song["tracks"])


def _track(song: dict, track_id) -> dict:
    i = track_index(song, track_id)
    if i is None:
        raise SongError("No such track.")
    return song["tracks"][i]


def _section(song: dict, section_id) -> dict:
    i = section_index(song, section_id)
    if i is None:
        raise SongError("No such section.")
    return song["sections"][i]


def _pattern(song: dict, pattern_id) -> dict:
    p = song["patterns"].get(pattern_id) if isinstance(pattern_id, str) else None
    if p is None:
        raise SongError("No such pattern.")
    return p


def place_take(
    song: dict, section_id, track_id, clip: str, at_bar: int, gain: float = 1.0
) -> int:
    """Put a clip from the clip store into a section as a take on an audio
    track, at `at_bar` (0-based, clamped into the section). The bar it
    landed on. Refused past MAX_LANE_CLIPS, on a track that is not audio,
    and for a section or track that is gone — the one rule a mic take, a
    generated riser and a clip placed by hand all follow. A take plays at
    its own speed from its bar line; the section's end cuts it."""
    sec, track = _section(song, section_id), _track(song, track_id)
    if track["kind"] != "audio":
        raise SongError("Takes go on an audio track.")
    if len(sec["audio"]) >= limits.MAX_LANE_CLIPS:
        raise SongError(f"A section holds at most {limits.MAX_LANE_CLIPS} takes.")
    bar = min(max(0, int(at_bar)), sec["bars"] - 1)
    sec["audio"].append(
        {"track_id": track["id"], "clip": clip, "at_bar": bar, "gain": gain}
    )
    return bar


def _take_at(section: dict, track_id, clip: str, at_bar: int) -> int:
    """Where in `section["audio"]` the take drawn at (track, clip, bar) is:
    a take has no id, so it is named as the page draws it, and two alike
    are the same take twice — the first match."""
    for i, a in enumerate(section["audio"]):
        if (a["track_id"], a["clip"], a["at_bar"]) == (track_id, clip, int(at_bar)):
            return i
    raise SongError("That take is no longer there.")


def remove_take(song: dict, section_id, track_id, clip: str, at_bar: int) -> dict:
    """Take a placed take off its section (the clip stays in the store).
    The take that was removed."""
    sec = _section(song, section_id)
    return sec["audio"].pop(_take_at(sec, track_id, clip, at_bar))


def move_take(
    song: dict,
    section_id,
    track_id,
    clip: str,
    at_bar: int,
    to_section,
    to_track,
    to_bar,
) -> int:
    """Move a placed take to bar `to_bar` (0-based, clamped into the
    section) of `to_section` on audio track `to_track`; its gain goes with
    it. The bar it landed on. `place_take`'s rules hold at the destination:
    an audio track, at most MAX_LANE_CLIPS takes a section. Within one
    section the take keeps its place in the list, so a move onto itself
    changes nothing."""
    src, dst = _section(song, section_id), _section(song, to_section)
    i = _take_at(src, track_id, clip, at_bar)
    track = _track(song, to_track)
    if track["kind"] != "audio":
        raise SongError("Takes go on an audio track.")
    if dst is not src and len(dst["audio"]) >= limits.MAX_LANE_CLIPS:
        raise SongError(f"A section holds at most {limits.MAX_LANE_CLIPS} takes.")
    take = src["audio"].pop(i)
    take["track_id"] = track["id"]
    take["at_bar"] = min(max(0, int(to_bar)), dst["bars"] - 1)
    dst["audio"].insert(i if dst is src else len(dst["audio"]), take)
    return take["at_bar"]


# --- construction -----------------------------------------------------------------


def new_song(bpm: float, root: str, scale_name: str) -> dict:
    """An empty song (no tracks, patterns or sections); hostile arguments
    are clamped or defaulted by the schema."""
    return schema.conform(
        {"bpm": bpm, "root": root, "scale_name": scale_name}, _nodes().SONG
    )


def add_track(song: dict, kind: str, name: str, **init) -> str:
    """Append a track of `kind` ("drums" | "synth" | "audio"); `init` sets
    its other fields (patch, fx, curves, drum_params, gain, ...), validated
    by the schema. Refused past MAX_TRACKS or the kind's own cap."""
    if kind not in TRACK_KINDS:
        raise SongError(f"Unknown track kind {kind!r}.")
    if len(song["tracks"]) >= limits.MAX_TRACKS:
        raise SongError(f"A song holds at most {limits.MAX_TRACKS} tracks.")
    if sum(t["kind"] == kind for t in song["tracks"]) >= TRACK_KINDS[kind]:
        raise SongError(f"A song holds at most {TRACK_KINDS[kind]} {kind} track(s).")
    tid = _fresh_id(song)
    track = schema.conform_strict(
        {**init, "id": tid, "kind": kind, "name": name}, _nodes().TRACK
    )
    song["tracks"].append(track)
    return tid


def remove_track(song: dict, track_id) -> list[str]:
    """Remove a track, unless a section still uses it (a cell naming a
    pattern, or a take): then nothing changes and the names of those
    sections come back. [] means it was removed."""
    _track(song, track_id)
    using = [
        s["name"]
        for s in song["sections"]
        if s["cells"].get(track_id) is not None
        or any(a["track_id"] == track_id for a in s["audio"])
    ]
    # long audio is on the song, not in a section: it is in the way by name
    using += [f"the stem “{x['name']}”" for x in long_items(song, track_id)]
    if using:
        return using
    del song["tracks"][track_index(song, track_id)]
    for s in song["sections"]:
        s["cells"].pop(track_id, None)
        _drop_lanes(s, track_id)
        _drop_span(s, track_id)
    return []


def add_pattern(song: dict, kind: str, name: str, data: dict) -> str:
    """Add a pattern of `kind` ("drums" | "notes") to the pool; `data`
    holds its lanes under the Arrangement's names, validated by the schema.
    Refused past MAX_PATTERNS."""
    if kind not in PATTERN_KINDS:
        raise SongError(f"Unknown pattern kind {kind!r}.")
    if len(song["patterns"]) >= limits.MAX_PATTERNS:
        raise SongError(f"The pattern pool holds at most {limits.MAX_PATTERNS}.")
    pid = _fresh_id(song)
    raw = dict(data) if isinstance(data, dict) else {}
    raw.setdefault("color", PALETTE[len(song["patterns"]) % len(PALETTE)])
    pattern = schema.conform_strict(
        {**raw, "id": pid, "kind": kind, "name": name}, _nodes().SONG_PATTERN
    )
    song["patterns"][pid] = pattern
    return pid


def pattern_usage(song: dict, pattern_id) -> list[str]:
    """The ids of the sections with a cell naming `pattern_id`, in order."""
    return [s["id"] for s in song["sections"] if pattern_id in s["cells"].values()]


def remove_pattern(song: dict, pattern_id) -> list[str]:
    """Remove a pool pattern, unless a section uses it: then nothing
    changes and those sections' names come back. [] means removed."""
    _pattern(song, pattern_id)
    used = set(pattern_usage(song, pattern_id))
    if used:
        return [s["name"] for s in song["sections"] if s["id"] in used]
    del song["patterns"][pattern_id]
    return []


def add_section(song: dict, name: str, bars: int, at: int | None = None) -> str:
    """Insert an empty section (appended when `at` is None); bars clamped
    to 1..MAX_SECTION_BARS. Refused past MAX_SECTIONS."""
    if len(song["sections"]) >= limits.MAX_SECTIONS:
        raise SongError(f"A song holds at most {limits.MAX_SECTIONS} sections.")
    sid = _fresh_id(song)
    section = schema.conform_strict(
        {
            "id": sid,
            "name": name,
            "color": PALETTE[len(song["sections"]) % len(PALETTE)],
            "bars": bars,
        },
        _nodes().SECTION,
    )
    n = len(song["sections"])
    idx = n if at is None else max(0, min(_index(at), n))
    song["sections"].insert(idx, section)
    return sid


def duplicate_section(song: dict, section_id) -> str:
    """Copy a section right after itself: same patterns (by reference),
    same takes, a new id and a primed name."""
    src = _section(song, section_id)
    if len(song["sections"]) >= limits.MAX_SECTIONS:
        raise SongError(f"A song holds at most {limits.MAX_SECTIONS} sections.")
    dup = copy.deepcopy(src)
    dup["id"] = _fresh_id(song)
    dup["name"] = _primed(src["name"])
    song["sections"].insert(section_index(song, section_id) + 1, dup)
    return dup["id"]


def remove_section(song: dict, section_id) -> None:
    """Take a section out of the song. Its patterns stay in the pool."""
    del song["sections"][section_index(song, _section(song, section_id)["id"])]


def move_section(song: dict, section_id, to_index: int) -> None:
    """Move a section to `to_index` (clamped) in the running order."""
    _section(song, section_id)  # raises SongError for an unknown id
    to = _index(to_index)
    sec = song["sections"].pop(section_index(song, section_id))
    idx = max(0, min(to, len(song["sections"])))
    song["sections"].insert(idx, sec)


# --- cells ------------------------------------------------------------------------


def set_cell(song: dict, section_id, track_id, pattern_id: str | None) -> None:
    """Point one cell at a pool pattern (or clear it with None). The
    pattern's kind must match the track's (drums <- drums, synth <- notes;
    audio tracks have takes, not cells). A pattern longer than the section
    grows the section to fit. A trimmed block keeps its span when its
    cell is pointed at another pattern: new content, same place."""
    sec = _section(song, section_id)
    track = _track(song, track_id)
    want = CELL_KIND.get(track["kind"])
    if want is None:
        raise SongError("Audio tracks hold takes, not patterns.")
    if pattern_id is None:
        sec["cells"][track_id] = None
        _drop_lanes(sec, track_id)  # a lane rides a cell that plays
        _drop_span(sec, track_id)  # ... and so does a block's span
        return
    p = _pattern(song, pattern_id)
    if p["kind"] != want:
        raise SongError(f"A {p['kind']} pattern can't go on a {track['kind']} track.")
    sec["cells"][track_id] = pattern_id
    sec["bars"] = min(limits.MAX_SECTION_BARS, max(sec["bars"], pattern_bars(p)))


def move_cell(song: dict, section_id, track_id, to_section, to_track) -> bool:
    """Move a cell's pattern to another cell: the destination plays it (by
    reference, as `set_cell` puts it there — kinds must match, a longer
    pattern grows the section) and the source is cleared. False, and
    nothing changed, when the two are the same cell.

    The lanes rule: a lane belongs to its (section, track), and its targets
    are that track's — its sound's fields, its FX units. So a move ALONG a
    track (to another section) carries the cell's lanes with it, in place of
    whatever lanes the destination had; a move to another track leaves them
    behind, and they go with the cleared source. Copying a pattern
    (`set_cell`) never carries lanes.

    The block's span goes with it, to any track — it is where the block
    sits, not what its track does — held to the destination's bars
    (`set_span`); a block without one fills the destination, whatever span
    the cell there had."""
    src, dst = _section(song, section_id), _section(song, to_section)
    _track(song, track_id)
    pid = src["cells"].get(track_id)
    if pid is None:
        raise SongError("That cell is empty.")
    if (src["id"], track_id) == (dst["id"], to_track):
        return False
    span = (src.get("spans") or {}).get(track_id)
    set_cell(song, to_section, to_track, pid)
    lanes = copy.deepcopy(cell_lanes(src, track_id)) if track_id == to_track else []
    set_cell(song, section_id, track_id, None)
    if track_id == to_track:
        _drop_lanes(dst, to_track)
        if lanes:
            dst.setdefault("auto", {})[to_track] = lanes
    _drop_span(dst, to_track)
    if span:
        set_span(song, to_section, to_track, *span)
    return True


# --- block spans --------------------------------------------------------------------
# A cell plays its pattern through its whole section unless it has a span:
# `section["spans"] = {track id: [at, len]}`, bars, the block playing bars
# [at, at + len) of each pass — the pattern restarting at `at`, tiled, and
# silent outside. Absent means the whole section, which is every cell of a
# song that never trimmed one: no key of such a song moves.


def cell_span(section: dict, track_id) -> tuple[int, int]:
    """(at, len) of a cell's block, in bars: its span, or the whole
    section."""
    got = (section.get("spans") or {}).get(track_id)
    return (int(got[0]), int(got[1])) if got else (0, int(section["bars"]))


def _drop_span(section: dict, track_id) -> None:
    spans = section.get("spans")
    if spans and track_id in spans:
        del spans[track_id]
    if not spans:
        section.pop("spans", None)  # absent means absent, as `auto`


def set_span(song: dict, section_id, track_id, at: int, length: int) -> tuple[int, int]:
    """Put a cell's block at bars [at, at + length) of its section: `at`
    held to 0..bars-1, `length` to 1..bars-at. A span that covers the
    section is no span (it is cleared). What it is now. Refused for a cell
    that holds no pattern."""
    sec = _section(song, section_id)
    _track(song, track_id)
    if sec["cells"].get(track_id) is None:
        raise SongError("That cell is empty.")
    bars = int(sec["bars"])
    at = min(max(0, int(at)), bars - 1)
    length = min(max(1, int(length)), bars - at)
    _drop_span(sec, track_id)
    if (at, length) != (0, bars):
        sec.setdefault("spans", {})[track_id] = [at, length]
    return at, length


def clear_span(song: dict, section_id, track_id) -> None:
    """The block fills its section again."""
    _track(song, track_id)
    _drop_span(_section(song, section_id), track_id)


def spill_span(song: dict, section_id, track_id, to_section, to_len: int) -> list[str]:
    """Carry a block on past its section's end: its right edge was dragged
    into `to_section`, `to_len` bars in. The block runs to the end of its
    own section, and in each following section up to `to_section`, in
    order, on the same track:

    - an empty cell takes the pattern — whole, or bars [0, to_len) in the
      last one;
    - a cell already playing this pattern is extended to cover that much
      (never shortened);
    - a cell playing ANOTHER pattern stops the spill there, and so does a
      section shorter than the pattern (it would have to grow). Nothing is
      ever overwritten.

    The names of the sections it reached, in order (empty: it stopped at
    the first). A section that repeats takes the block in each pass, as
    every cell does."""
    secs = song["sections"]
    i, j = section_index(song, section_id), section_index(song, to_section)
    if i is None or j is None:
        raise SongError("No such section.")
    src = secs[i]
    pid = src["cells"].get(track_id)
    if pid is None:
        raise SongError("That cell is empty.")
    p = _pattern(song, pid)
    at, _n = cell_span(src, track_id)
    set_span(song, section_id, track_id, at, src["bars"] - at)
    reached = []
    for k in range(i + 1, j + 1):
        sec = secs[k]
        there = sec["cells"].get(track_id)
        if (there is not None and there != pid) or sec["bars"] < pattern_bars(p):
            break
        want = sec["bars"] if k < j else min(max(1, int(to_len)), sec["bars"])
        if there is None:
            set_cell(song, sec["id"], track_id, pid)
            set_span(song, sec["id"], track_id, 0, want)
        else:
            a, n = cell_span(sec, track_id)
            set_span(song, sec["id"], track_id, 0, max(a + n, want))
        reached.append(sec["name"])
    return reached


def make_unique(song: dict, section_id, track_id) -> str:
    """Copy the pattern in this one cell as "Name ′", repoint the cell at
    the copy and return its id; every other section keeps the original."""
    sec = _section(song, section_id)
    _track(song, track_id)
    pid = sec["cells"].get(track_id)
    if pid is None:
        raise SongError("That cell is empty.")
    new = copy_pattern(song, pid)
    sec["cells"][track_id] = new
    return new


def copy_pattern(song: dict, pattern_id) -> str:
    """A copy of a pool pattern as "Name ′" (its colour too), in the pool
    and in no cell; returns its id. Refused past MAX_PATTERNS."""
    src = _pattern(song, pattern_id)
    data = {k: copy.deepcopy(v) for k, v in src.items() if k not in ("id", "name")}
    return add_pattern(song, src["kind"], _primed(src["name"]), data)


def family(song: dict, pattern: dict) -> list[dict]:
    """A pattern's family by letter: `[{var, id, on}]` for A-D and "fill"
    — the pool pattern that is that variant (None: not made yet) and
    whether it is `pattern` itself. A pattern in no family is its own A."""
    fam = pattern.get("fam")
    by_var = {
        p["var"]: p["id"]
        for p in song["patterns"].values()
        if fam and p.get("fam") == fam and p.get("var") and p["kind"] == pattern["kind"]
    }
    by_var[pattern.get("var") or "A"] = pattern["id"]
    return [
        {"var": v, "id": by_var.get(v), "on": by_var.get(v) == pattern["id"]}
        for v in PATTERN_VARIANTS
    ]


# what the Song page's "+ pattern" buttons make: a note pattern opens on a
# 16th grid, as a drum pattern does (the schema's own default, 4 steps a
# bar, is the working loop's and stays what an older file loads as)
NEW_NOTE_STEPS = 16


def new_pattern(song: dict, kind: str, name: str = "") -> str:
    """An empty pool pattern, named "Beat N" / "Riff N" unless `name` is
    given; returns its id."""
    n = sum(p["kind"] == kind for p in song["patterns"].values()) + 1
    default = f"{'Beat' if kind == 'drums' else 'Riff'} {n}"
    data = {"steps_per_bar": NEW_NOTE_STEPS} if kind == "notes" else {}
    return add_pattern(song, kind, name or default, data)


def new_cell_pattern(song: dict, section_id, track_id, name: str = "") -> str:
    """A fresh empty pattern of the track's kind, straight into this cell
    (whatever the cell held stays in the pool); returns its id."""
    _section(song, section_id)
    kind = CELL_KIND.get(_track(song, track_id)["kind"])
    if kind is None:
        raise SongError("Audio tracks hold takes, not patterns.")
    pid = new_pattern(song, kind, name)
    m = section_meter(song, _section(song, section_id))
    if m != _meter.DEFAULT:  # written in its section's bar
        set_pattern_meter(song["patterns"][pid], m)
    set_cell(song, section_id, track_id, pid)
    return pid


def variation(song: dict, section_id, track_id, seed: int) -> str:
    """`make_unique`, then mutate the copy — `beatgen.mutate` for drums, a
    seeded note shift + step toggle for notes. Deterministic for a seed,
    and always different from its source."""
    new = make_unique(song, section_id, track_id)
    p = song["patterns"][new]
    if p["kind"] == "drums":
        from .beatgen import mutate  # lazy: beatgen imports state

        p["drums"] = mutate(p["drums"], seed, p["bars"], p["steps"])
    else:
        p["cells"] = _vary_notes(song, p, seed)
    return new


def _ladder(song: dict) -> list[int]:
    """Every MIDI note (0..127) in the song's key."""
    mode = song.get("scale_name")
    steps = SCALES.get(mode, SCALES["minor"])
    try:
        root = midi(song["root"]) % 12
    except Exception:  # noqa: BLE001 - a schema-clean root always parses
        root = 9
    pcs = {(root + s) % 12 for s in steps}
    return [m for m in range(128) if m % 12 in pcs]


def window_rows(song: dict, span: int = 1, octave: int = 0) -> list[str]:
    """The Song drawer's note-grid rows, highest first: the song key's
    rows (`state.pitch_rows`: six tones, or two octaves at `span` 2)
    from the root moved `octave` whole octaves (-2..+4) — the window a
    pattern is shown and edited at. Octave 0 is every drawer grid from
    before there was a window."""
    from .state import Arrangement, pitch_rows

    try:
        root = midi(song["root"])
    except Exception:  # noqa: BLE001 - a schema-clean root always parses
        root = 33
    k = min(4, max(-2, int(octave)))
    while k > -1 and root + 12 * k + 24 > 127:
        k -= 1
    while root + 12 * k < 0:
        k += 1
    key = Arrangement(root=name_of(root + 12 * k), scale_name=song["scale_name"])
    return pitch_rows(key, span)


def window_octave(song: dict, pattern: dict, span: int = 1) -> int:
    """Which octave window shows the most of a notes pattern: 0 (from
    the song's root) unless another holds more of its notes — a lead
    written two octaves up opens on its own notes, not on an empty grid
    that says "24 notes outside the key". Ties go to the window nearest
    the root, the lower first, so a pattern's window never depends on
    the order of its cells."""
    cells = pattern.get("cells") or []
    best, held = 0, -1
    for k in (0, 1, -1, 2, -2, 3, 4) if cells else ():
        rows = set(window_rows(song, span, k))
        n = sum(c[0] in rows for c in cells)
        if n > held:
            best, held = k, n
    return best


def _note_order(c) -> tuple:
    """Step, then pitch, then spelling: C#2 and Db2 tie on MIDI number, and
    without the spelling the tie would fall back to set order, which
    PYTHONHASHSEED changes between processes."""
    return (c[1], midi(c[0]), c[0])


def _vary_notes(song: dict, p: dict, seed: int) -> list[list]:
    """Shift about a quarter of the visible notes (at least one) by one or
    two scale degrees, then toggle one step: clear a sounding step, or add
    a note on a silent one. Hidden notes (past the grid) are left alone."""
    rng = random.Random(seed)
    ladder = _ladder(song)
    total = p["bars"] * p["steps_per_bar"]
    src = {(c[0], c[1]) for c in p["cells"]}
    visible = sorted((c for c in src if c[1] < total), key=_note_order)
    cells = set(src)

    def degree(pitch: str, delta: int) -> str:
        i = min(bisect_left(ladder, midi(pitch)), len(ladder) - 1)
        return name_of(ladder[max(0, min(i + delta, len(ladder) - 1))])

    if visible:
        for pitch, step in rng.sample(visible, max(1, round(len(visible) / 4))):
            cells.discard((pitch, step))
            cells.add((degree(pitch, rng.choice((-2, -1, 1, 2))), step))

    step = rng.randrange(total)
    here = sorted(c for c in cells if c[1] == step)
    if here:
        cells.discard(rng.choice(here))
    else:
        base = rng.choice(visible)[0] if visible else song.get("root", "A1")
        cells.add((degree(base, rng.choice((0, 2, 4))), step))

    if cells == src:  # every change cancelled out: add one note that isn't there
        base = visible[0][0] if visible else song.get("root", "A1")
        for s in range(total):
            if (base, s) not in cells:
                cells.add((base, s))
                break
        else:
            cells.add((degree(base, 1), 0))
    return [list(c) for c in sorted(cells, key=_note_order)]


# --- automation lanes -------------------------------------------------------------
#
# A (section, track) cell can carry a few lanes, `section["auto"][track_id]`
# = [{target, points}], one per target: breakpoints [position, value], both
# 0..1, linear between them, the first and last values held out to the
# edges. The position runs across ONE PASS of the section (every repeat
# replays the lane); the value maps onto the target's range
# (`curves.lane_range`). Targets: `gain` (the track's level over the
# section, a fader after its FX; 0.5 is unity) on a drums or synth track,
# and any CURVABLE field of a synth track's sound.
#
# In the section, a field lane WINS over a curve the track's sound carries
# for that field (the curve still plays in every section without the lane),
# and it is the only automation a layer track ever plays — a layer ignores
# its sound's own curves, in a song as in a loop.
#
# A lane may also move a parameter of a unit in the track's FX chain
# ("fx:<unit id>:<kind>:<param>", `curves.FX_LANES`, rendered by
# `fxauto`), on a drums, a synth or an audio track. The lane names its unit
# by id, so it follows the unit through a reorder of the rack; when the unit
# is gone the lane is MISSING — kept in the song (the drawer shows it, with
# a remove button), read by nothing (`section_lanes`), so the section
# renders and keys as if it were not there.
#
# An audio track has takes, not cells: its lanes (the level and its FX
# units' parameters, never a field) sit on its ROW in a section, under the
# same key, `section["auto"][track_id]`, and are read while the section
# holds a take on that track (`lanes_live`) — as a cell's are while it
# holds a pattern. Such a track's takes then render as a stem of their own
# (`SectionParts.takes`), and so do they under a rack.
#
# `auto` is absent unless a cell is automated, so every older song, and
# every key derived from one, is unchanged.


def _fx_lane_targets(track: dict) -> tuple[str, ...]:
    """The FX-parameter targets a track's lanes can name now: the
    whitelisted parameters of the units in its chain that have an id."""
    return tuple(
        fx_target(u["id"], u["kind"], param)
        for u in track.get("fx") or ()
        if unit_id_ok(u.get("id"))
        for param in FX_LANES.get(u["kind"], ())
    )


def lane_targets(track: dict) -> tuple[str, ...]:
    """What a track's cells (an audio track's row in a section) can
    automate: its level, on a synth track every curvable field of its
    sound, and the parameters of its FX units a lane can move (the units
    with an id: `fx_lane_menu` lists the rest, `claim_fx_target` gives one
    its id)."""
    if track["kind"] == "synth":
        return (*LANE_TARGETS, *_fx_lane_targets(track))
    return (GAIN, *_fx_lane_targets(track))


def has_takes(section: dict, track_id) -> bool:
    """Whether a section holds a take on `track_id`."""
    return any(a["track_id"] == track_id for a in section.get("audio") or ())


def lanes_live(section: dict, track: dict) -> bool:
    """Whether a track's lanes in a section are read: on a cell that holds
    a pattern or — an audio track — while the section holds one of its
    takes."""
    if track["kind"] == "audio":
        return has_takes(section, track["id"])
    return section["cells"].get(track["id"]) is not None


FX_SLOT = "@"  # "fx:@<index>:<kind>:<param>": a unit named by its place


def fx_lane_menu(track: dict) -> list[tuple[str, list[tuple[str, str]]]]:
    """The drawer's FX choices, a group per unit of the track's chain with
    a parameter a lane can move: (the unit's label — numbered when the
    chain holds several of its kind —, [(target, param name)]). A unit
    without an id yet is named by its place ("fx:@2:reverb:mix"), which
    only ever travels from this menu to `claim_fx_target`: no lane is
    stored under it."""
    from .fxrack import FX_UNITS  # lazy: fxrack imports state

    chain = track.get("fx") or []
    count: dict[str, int] = {}
    out = []
    for i, u in enumerate(chain):
        kind = u["kind"]
        count[kind] = count.get(kind, 0) + 1
        if kind not in FX_LANES:
            continue
        label = FX_UNITS[kind]["label"]
        if sum(x["kind"] == kind for x in chain) > 1:
            label = f"{label} {count[kind]}"
        uid = u["id"] if unit_id_ok(u.get("id")) else f"{FX_SLOT}{i}"
        out.append((label, [(fx_target(uid, kind, p), p) for p in FX_LANES[kind]]))
    return out


def claim_fx_target(track: dict, target) -> str:
    """A lane target as it is stored. One naming an FX unit by its place
    (`fx_lane_menu`) becomes the target naming it by id — the unit is
    given its id here if it had none, which is the moment a chain first
    holds one. Anything else comes back as it is. Refused (SongError) when
    the place holds no unit of that kind any more."""
    from .fxrack import unit_id  # lazy: fxrack imports state

    if not isinstance(target, str) or not target.startswith(f"fx:{FX_SLOT}"):
        return target
    bits = target.split(":")
    chain = track.get("fx") or []
    try:
        index = int(bits[1][len(FX_SLOT) :])
        kind, param = bits[2], bits[3]
        ok = len(bits) == 4 and 0 <= index < len(chain)
    except (ValueError, IndexError):
        ok = False
    if not ok or chain[index]["kind"] != kind or param not in FX_LANES.get(kind, ()):
        raise SongError(f"{track['name']} has no such effect now.")
    return fx_target(unit_id(chain, index), kind, param)


def cell_lanes(section: dict, track_id) -> list[dict]:
    """The lanes on one cell, in the order they were added."""
    return section.get("auto", {}).get(track_id, [])


def lane_rest(track: dict, target: str) -> float:
    """Where a new lane starts out, 0..1 of its range: unity for the level,
    the sound's own value for a field — so adding a lane moves nothing
    until it is shaped."""
    if target == GAIN:
        return GAIN_UNITY
    lo, hi = lane_range(target)
    if fx := parse_fx(target):  # the unit's own setting
        unit = next((u for u in track.get("fx") or () if u.get("id") == fx[0]), {})
        v = unit.get("params", {}).get(fx[2])
    else:
        v = track.get("patch", {}).get(target)
    if isinstance(v, bool) or not isinstance(v, int | float):
        return 0.5
    return min(1.0, max(0.0, (float(v) - lo) / (hi - lo)))


def _drop_lanes(section: dict, track_id) -> None:
    auto = section.get("auto")
    if auto is not None:
        auto.pop(track_id, None)
        if not auto:
            del section["auto"]


def set_lane(song: dict, section_id, track_id, target, points) -> None:
    """Add a lane to a cell, or replace the one it has on `target`.
    `points` are validated by the schema (clamped, sorted by position);
    refused without a start and an end, on an empty cell, for a target the
    track can't automate, and past MAX_CELL_LANES."""
    sec = _section(song, section_id)
    track = _track(song, track_id)
    if not isinstance(target, str) or target not in lane_targets(track):
        raise SongError(f"{track['name']} can't automate that.")
    if not lanes_live(sec, track):
        if track["kind"] == "audio":
            raise SongError(
                f"{track['name']} has no take in {sec['name']}: "
                "its lanes move its takes."
            )
        raise SongError("That cell is empty.")
    try:
        lane = schema.conform_strict(
            {"target": target, "points": points}, _nodes().LANE
        )
    except schema.Invalid:
        raise SongError("A lane needs a start and an end value.") from None
    lanes = sec.setdefault("auto", {}).setdefault(track_id, [])
    for i, old in enumerate(lanes):
        if old["target"] == target:
            lanes[i] = lane
            return
    if len(lanes) >= limits.MAX_CELL_LANES:
        raise SongError(f"A cell holds at most {limits.MAX_CELL_LANES} lanes.")
    lanes.append(lane)


def remove_lane(song: dict, section_id, track_id, target) -> None:
    """Take one lane off a cell (a target it doesn't automate: no change)."""
    sec = _section(song, section_id)
    _track(song, track_id)
    left = [ln for ln in cell_lanes(sec, track_id) if ln["target"] != target]
    _drop_lanes(sec, track_id)
    if left:
        sec.setdefault("auto", {})[track_id] = left


def section_lanes(song: dict, section: dict) -> dict[str, list[dict]]:
    """The lanes a section's render reads, {track id: lanes}: those on a
    cell that holds a pattern (an audio track's row in a section that holds
    one of its takes), for a target its track can automate. A gain
    lane resting at unity from end to end is no lane, so a flat lane at the
    neutral value renders, and keys, exactly as the section without it.

    Never a track's mute or solo: a silenced track keeps its lanes as it
    keeps its pattern clocks, so a stem export's sections keep the mix's
    cycle (`song_play.stem_songs`)."""
    tracks = {t["id"]: t for t in song["tracks"]}
    out: dict[str, list[dict]] = {}
    for tid, lanes in (section.get("auto") or {}).items():
        t = tracks.get(tid)
        if t is None or not lanes_live(section, t):
            continue
        ok = lane_targets(t)
        keep = [
            ln
            for ln in lanes
            if ln["target"] in ok
            and len(ln["points"]) >= 2
            and not (
                ln["target"] == GAIN and all(p[1] == GAIN_UNITY for p in ln["points"])
            )
        ]
        if keep:
            out[tid] = keep
    return out


# --- long audio -------------------------------------------------------------------
#
# A long audio item is a whole stem (or a mix of stems) of a Sample Lab
# source on the SONG's timeline (`song["long"]`, absent when empty). It
# belongs to an audio
# track — its row, gain, mute and solo — but to no section: it sits at a bar
# of the timeline (`at_bar`, every bar of every repeat counted) and plays
# linearly, as recorded, through whatever sections lie under it. So a
# section that repeats plays the NEXT bars of it on each pass, and sections
# moved, added or removed leave it where it is.


def long_items(song: dict, track_id=None) -> list[dict]:
    """The song's long audio items (all, or one audio track's)."""
    items = song.get("long") or []
    if track_id is None:
        return list(items)
    return [x for x in items if x["track_id"] == track_id]


def _long(song: dict, item_id) -> dict:
    for x in long_items(song):
        if x["id"] == item_id:
            return x
    raise SongError("That stem is no longer on the song.")


def section_bar(song: dict, section_id, bar: int = 0) -> int:
    """The timeline bar of `bar` (0-based, clamped) of a section's first
    pass: where "start at this section" puts a long item."""
    at = 0
    for sec in song["sections"]:
        if sec["id"] == section_id:
            return at + min(max(0, int(bar)), sec["bars"] - 1)
        at += sec["bars"] * sec["repeats"]
    raise SongError("No such section.")


def song_bars(song: dict) -> int:
    return sum(s["bars"] * s["repeats"] for s in song["sections"])


def add_long(song: dict, track_id, source: str, **fields) -> str:
    """Put a long audio item on an audio track; `fields` are its other
    values (name, model, stems, levels, at_bar, start, length, gain, bpm),
    held to type and range by the schema. Refused past MAX_LONG_AUDIO and
    on a track that is not audio."""
    track = _track(song, track_id)
    if track["kind"] != "audio":
        raise SongError("A stem goes on an audio track.")
    items = long_items(song)
    if len(items) >= limits.MAX_LONG_AUDIO:
        raise SongError(f"A song holds at most {limits.MAX_LONG_AUDIO} whole stems.")
    raw = {**fields, "id": _fresh_id(song), "track_id": track["id"], "source": source}
    try:
        item = schema.conform_strict(raw, _nodes().LONG)
    except schema.Invalid:
        raise SongError("That is not a song in the Lab.") from None
    song["long"] = [*items, item]
    return item["id"]


LONG_FIELDS = ("name", "at_bar", "start", "length", "gain", "mute", "track_id")


def set_long(song: dict, item_id, **fields) -> dict:
    """Change a long item's own values (`LONG_FIELDS`); anything else is
    ignored, and the schema clamps when the song is committed."""
    item = _long(song, item_id)
    if "track_id" in fields and _track(song, fields["track_id"])["kind"] != "audio":
        raise SongError("A stem goes on an audio track.")
    item.update({k: v for k, v in fields.items() if k in LONG_FIELDS})
    return item


def trim_long(song: dict, item_id, bars: int) -> dict:
    """Cut `bars` bars off a long item's head (negative: give them back):
    its place on the timeline and its place in the source move together,
    so the audio that still plays stays exactly where it was. Bars are the
    SONG's (the timeline is what must not move). Held at the song's first
    bar and at the first bar line the source has."""
    item = _long(song, item_id)
    bar = 4 * 60.0 / song["bpm"]
    clock = clock_at_bar(song, item["at_bar"])
    if clock is not None and clock != _meter.Clock(float(song["bpm"])):
        bar = clock.seconds(1)  # the bar of the section it starts in
    back = int((item["start"] - limits.LONG_START_RANGE[0]) // bar)
    n = max(int(bars), -item["at_bar"], -back)
    n = min(n, limits.MAX_LONG_BAR - item["at_bar"])
    item["at_bar"] += n
    item["start"] = round(item["start"] + n * bar, 6)
    if item["length"]:
        item["length"] = max(bar, round(item["length"] - n * bar, 6))
    return item


def remove_long(song: dict, item_id) -> None:
    _long(song, item_id)
    song["long"] = [x for x in long_items(song) if x["id"] != item_id]


def _passes(song: dict):
    """The timeline as (section index, pass index, first sample, samples,
    first bar, bars) — the plan's own arithmetic (`svc.song_plan`)."""
    start = bar = 0
    for i, sec in enumerate(song["sections"]):
        cycle = cycle_bars(song, sec)
        n = section_clock(song, sec).samples(cycle)
        for j in range(len(section_timeline(song, sec))):
            yield i, j, start, n, bar, cycle
            start += n
            bar += cycle


def long_spans(song: dict) -> dict[str, tuple[int, int]]:
    """Where each long item plays on the timeline, {item id: (first sample,
    end sample)}. The end is its own (`length`, else the source's own end,
    `seconds`, else as far as a source can go), cut at the song's end: the
    song is never lengthened. An item whose bar is past the song's end is
    not in it."""
    passes = list(_passes(song))
    if not passes:
        return {}
    total = passes[-1][2] + passes[-1][3]
    clocks = [section_clock(song, s) for s in song["sections"]]
    out = {}
    for x in long_items(song):
        at = next(
            (
                p0 + clocks[i].bar_sample(x["at_bar"] - b0)
                for i, _j, p0, _n, b0, bars in passes
                if b0 <= x["at_bar"] < b0 + bars
            ),
            None,
        )
        if at is None:
            continue
        whole = x["seconds"] or limits.MAX_SOURCE_SECONDS
        length = max(0.0, whole - x["start"])
        if x["length"]:
            length = min(length, x["length"])
        end = min(at + int(round(length * SR)), total)
        if end > at:
            out[x["id"]] = (at, end)
    return out


def pass_cuts(song: dict) -> dict[int, list[tuple]]:
    """The long audio every pass plays: {section index: [cuts of pass 0,
    of pass 1, ...]}, only for a section some audible long item crosses.
    A cut is what `longaudio.read` reads and a pass's key carries:

        {source, model, stems, levels, frm, a, b, gain}

    `frm` is the sample of the source at the pass's first sample; `[a, b)`
    the item's own extent relative to that sample (it may begin before the
    pass or end after it — its fades sit at `a` and `b`); `gain` the
    item's times its track's. A muted item, or one on a track the mix
    silences (`audible`), plays nothing."""
    tracks = {t["id"]: t for t in song["tracks"]}
    spans = long_spans(song)
    live = [
        x
        for x in long_items(song)
        if x["id"] in spans
        and not x["mute"]
        and x["track_id"] in tracks
        and audible(song, tracks[x["track_id"]])
    ]
    if not live:
        return {}
    out: dict[int, dict[int, tuple]] = {}
    count: dict[int, int] = {}
    for i, j, p0, n, _b0, _bars in _passes(song):
        count[i] = j + 1
        cuts = []
        for x in live:
            at, end = spans[x["id"]]
            if at >= p0 + n or end <= p0:
                continue
            cuts.append(
                {
                    "source": x["source"],
                    "model": x["model"],
                    "stems": list(x["stems"]),
                    "levels": dict(x["levels"]),
                    "frm": int(round(x["start"] * SR)) + (p0 - at),
                    "a": at - p0,
                    "b": end - p0,
                    "gain": min(1.5, x["gain"] * tracks[x["track_id"]]["gain"]),
                }
            )
        if cuts:
            out.setdefault(i, {})[j] = tuple(cuts)
    return {i: [by.get(j, ()) for j in range(count[i])] for i, by in out.items()}


# --- timing -----------------------------------------------------------------------


def pattern_bars(pattern: dict) -> int:
    return max(1, int(pattern.get("bars", 1)))


def bar_choices(steps_per_bar: int) -> tuple[int, ...]:
    """The bars a pool pattern may have at this resolution: the loop's
    own choices, and 8 while bars x steps fits the grid
    (`limits.PATTERN_BAR_CHOICES`, `limits.MAX_GRID_STEPS`)."""
    return tuple(
        b
        for b in limits.PATTERN_BAR_CHOICES
        if b * int(steps_per_bar) <= limits.MAX_GRID_STEPS
    )


def has_chance(song: dict, section: dict) -> bool:
    """Does a notes pattern the section plays rest notes by chance (a
    pattern's `note_chance`)? Such a section is one cycle (see
    `cycle_bars`) and always tiled (`section_arrangement`), so every pass
    of each pattern rolls afresh; no section without one changes."""
    pool = song["patterns"]
    return any(
        (p := pool.get(pid)) is not None
        and p["kind"] == "notes"
        and p.get("note_chance")
        for pid in section["cells"].values()
        if pid is not None
    )


def parts_chance(parts: SectionParts) -> bool:
    """Do these parts roll any chance (the main lane's, a layer's)?"""
    return bool(parts.arr.note_chance) or any(
        lay.get("note_chance") for lay in parts.layers
    )


def repass(parts: SectionParts, j: int) -> SectionParts:
    """Pass `j` of a section's timeline (its `repeats`): the same cycle,
    every chance rolled `j` cycles further on (`arrange.chance_passes`), so
    no two passes of a section with chance rest the same notes. Pass 0 is
    `parts` itself; so is every pass of parts without chance."""
    if not j or not parts_chance(parts):
        return parts
    from .arrange import chance_passes

    at = int(j) * parts.cycle_bars * parts.arr.steps_per_bar
    arr = replace(parts.arr, note_chance=chance_passes(parts.arr.note_chance, at))
    layers = tuple(
        {**lay, "note_chance": chance_passes(lay["note_chance"], at)}
        if lay.get("note_chance")
        else lay
        for lay in parts.layers
    )
    return replace(parts, arr=arr, layers=layers)


def cycle_bars(song: dict, section: dict) -> int:
    """How many bars the section renders as one cycle: the lcm of its used
    patterns' bars (1 when none), when that divides the section's bars.
    Otherwise the whole section is one cycle, so every pass of a section is
    a whole cycle and its end is always a cycle's end (which is what rings
    on into the next section). A section holding a take is one cycle too:
    a take sits at its own bar and plays once per pass of the section. So
    is one with an automation lane (`section_lanes`): a lane runs across
    the whole pass, so no shorter piece of it repeats. And so is one with
    a trimmed block (`spans`): a block sits at its own bars of the pass.
    And so is one whose notes rest by chance (`has_chance`): each pass of
    a pattern rolls afresh, so no shorter piece of the section repeats."""
    bars = max(1, int(section["bars"]))
    if (
        section.get("audio")
        or section.get("spans")
        or section.get("ramp")  # a ramp runs across the whole pass
        or section_lanes(song, section)
        or has_chance(song, section)
    ):
        return bars
    n = 1
    for pid in section["cells"].values():
        p = song["patterns"].get(pid) if pid is not None else None
        if p is not None:
            n = math.lcm(n, pattern_bars(p))
    return n if bars % n == 0 else bars


def cycle_samples(bars: int, bpm: float) -> int:
    """A cycle's length in samples: the one formula every section render,
    playback plan and export uses (the same arithmetic as
    `arrange.loop_seconds`, so a non-flat cycle is exactly its loop)."""
    return n_samples(bars * 4.0 * (60.0 / bpm), SR)


def bar_sample(bar: int, bpm: float) -> int:
    """Where bar `bar` of a cycle starts, in samples: the offset a chunk's
    dry render is added in at (`render._cycle_stems`) and the one its
    automation lanes are read from (`SectionParts.window`)."""
    return int(round(bar * 4.0 * (60.0 / bpm) * SR))


def section_timeline(song: dict, section: dict) -> list[tuple[int, int]]:
    """How a section plays out, as (start_sample, n_samples) passes from
    the section's own start: its cycle `bars / cycle` times, all of that
    `repeats` times. Every pass is one whole cycle (`cycle_bars` divides
    `bars`), and patterns realign at each. The first pass is the one the
    previous section's tail folds into (`svc.section_result(i)`); every
    later one folds the cycle's own tail, as a loop does
    (`svc.section_result(i, loop=True)`)."""
    cycle = cycle_bars(song, section)
    n = section_clock(song, section).samples(cycle)
    count = int(section["repeats"]) * (int(section["bars"]) // cycle)
    return [(i * n, n) for i in range(count)]


def section_seconds(song: dict, section: dict) -> float:
    if timed(song, section):  # its own meter or tempo: its own clock
        clock = section_clock(song, section)
        return clock.seconds(section["bars"]) * section["repeats"]
    return section["bars"] * section["repeats"] * 4 * 60.0 / song["bpm"]


# --- meter and tempo (meter.py) -------------------------------------------
# A song has one tempo and, optionally, a meter (`song["meter"]`, absent at
# 4/4); a section may have its own of either (`section["meter"]`,
# `section["bpm"]`) and a ramp (`section["ramp"]`: the tempo it reaches at
# the end of each pass, from its own at the start). All absent when unset,
# so every song from before them times, renders and keys as it did. A bar
# keeps its meter's beats at its section's tempo: a slower section is a
# longer stretch of the timeline (and wider on the playlist).


def section_meter(song: dict, section: dict | None) -> str:
    m = (section or {}).get("meter") or song.get("meter") or _meter.DEFAULT
    return _meter.clean(m)


def section_bpm(song: dict, section: dict | None) -> float:
    return float((section or {}).get("bpm") or song["bpm"])


def timed(song: dict, section: dict) -> bool:
    """Does the section keep a clock of its own (a meter, a tempo or a
    ramp other than the song's 4/4 at one tempo)?"""
    return (
        section_meter(song, section) != _meter.DEFAULT
        or section_bpm(song, section) != float(song["bpm"])
        or bool(section.get("ramp"))
    )


def section_clock(song: dict, section: dict) -> _meter.Clock:
    """How long a section's bars last: its tempo, its meter's bar, its ramp
    across one pass (`meter.Clock`). `Clock(song bpm)` for every section
    of a 4/4 song at one tempo — `cycle_samples`' own arithmetic."""
    bpm = section_bpm(song, section)
    m = section_meter(song, section)
    ramp = _meter.ramp_of(bpm, section.get("ramp"), section["bars"], m)
    return _meter.Clock(bpm, _meter.bar_beats(m), tuple(ramp))


def clock_at_bar(song: dict, bar: int) -> _meter.Clock | None:
    """The clock of the section the timeline's bar `bar` is in."""
    at = 0
    for sec in song["sections"]:
        at += sec["bars"] * sec["repeats"]
        if bar < at:
            return section_clock(song, sec)
    return None


def pattern_meter(pattern: dict) -> str:
    """The meter a pattern's steps were written in (absent: 4/4)."""
    return _meter.clean(pattern.get("meter"))


def set_pattern_meter(pattern: dict, m: str) -> None:
    if m == _meter.DEFAULT:
        pattern.pop("meter", None)
    else:
        pattern["meter"] = m


def fit_pattern(pattern: dict, to: str) -> dict:
    """A pattern written in one meter as the bars of another
    (`meter.refit`): each bar keeps its first beats and where everything
    sits in them, step length kept — a 4/4 beat in a 3/4 bridge plays its
    first three beats a bar; a step past the shorter bar is not heard; a
    step length the bar cannot hold (a quarter in 7/8) becomes the finer
    steps it covers. The pattern in the pool is never changed: this is a
    copy, for a section in that meter (`in_meter`)."""
    frm = pattern_meter(pattern)
    out = copy.deepcopy(pattern)
    set_pattern_meter(out, to)
    if frm == to:
        return out
    drums = pattern["kind"] == "drums"
    sk = "steps" if drums else "steps_per_bar"
    spb = int(pattern.get(sk, 16))
    to_spb, k = _meter.refit(spb, frm, to)
    out[sk] = to_spb

    def at(s):
        return _meter.refit_step(int(s), spb, frm, to_spb, to)

    def keyed(values: dict) -> dict:
        moved = {}
        for key, v in (values or {}).items():
            got = at(key) if str(key).isdigit() else None
            if got is not None:
                moved[str(got)] = v
        return moved

    if drums:
        cells = [[v, at(s)] for v, s in pattern.get("drums", [])]
        out["drums"] = sorted({(v, s) for v, s in cells if s is not None})
        out["drums"] = [list(c) for c in out["drums"]]
        out["drum_vel"] = keyed(pattern.get("drum_vel"))
        if pattern.get("hits"):
            hits = {}
            for key, v in pattern["hits"].items():
                voice, _, s = key.partition(".")
                got = at(s) if s.isdigit() else None
                if got is not None:
                    hits[f"{voice}.{got}"] = v
            out["hits"] = hits
        return out
    cells = []
    for pitch, s in pattern.get("cells", []):
        got = at(s)
        if got is not None:
            cells += [[pitch, got + i] for i in range(k)]
    out["cells"] = [list(c) for c in dict.fromkeys(map(tuple, cells))]
    out["velocities"] = keyed(pattern.get("velocities"))
    out["slides"] = sorted(
        {x for x in (at(s) for s in pattern.get("slides", [])) if x is not None}
    )
    out["cuts"] = [
        [p, got] for p, s in pattern.get("cuts", []) if (got := at(s)) is not None
    ]
    return out


def in_meter(song: dict, section: dict) -> dict:
    """The song as `section` hears it: its patterns fitted to the
    section's meter (`fit_pattern`) and the song's tempo the section's.
    The song itself when nothing differs — every section of a 4/4 song at
    one tempo — so nothing downstream sees a copy."""
    m = section_meter(song, section)
    bpm = section_bpm(song, section)
    pool = song["patterns"]
    need = [
        pid
        for pid in dict.fromkeys(section["cells"].values())
        if pid is not None and pid in pool and pattern_meter(pool[pid]) != m
    ]
    if not need and bpm == float(song["bpm"]):
        return song
    fitted = dict(pool)
    for pid in need:
        fitted[pid] = fit_pattern(pool[pid], m)
    return {**song, "patterns": fitted, "bpm": bpm}


def song_seconds(song: dict) -> float:
    return float(sum(section_seconds(song, s) for s in song["sections"]))


def content_hash(pattern: dict) -> str:
    """A dedupe key: the sha1 of the pattern's content (its kind and lanes)
    in canonical form — id, name and colour excluded, note lists sorted."""
    data = {k: v for k, v in pattern.items() if k not in ("id", "name", "color")}
    for k in ("drums", "cells"):
        if k in data:
            data[k] = sorted([str(a), int(b)] for a, b in data[k])
    if "slides" in data:
        data["slides"] = sorted(data["slides"])
    # a pattern without cuts hashes as it did before they existed
    if data.get("cuts"):
        data["cuts"] = sorted([str(a), int(b)] for a, b in data["cuts"])
    else:
        data.pop("cuts", None)
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()


# --- section rendering bridge -----------------------------------------------------

SECTION_PARTS: tuple[str, ...] = ("synth", "drums", "clips")


@dataclass(frozen=True)
class SectionParts:
    """One section, ready for the unchanged render engine: an ordinary
    `Arrangement` for ONE cycle plus the lanes `RenderInputs` takes.

    - `arr` holds the song-wide values, the main lane's notes, the drums
      track's beat (a drums pattern's `bars`/`steps` become `drum_bars`/
      `drum_steps`) and kit, and the section's takes as `clips`.
    - `main_patch`/`curves`/`main_fx`/`main_muted`/`main_gain` are the main
      lane's track (the first synth track, in song order, with a pattern
      here). Its gain applies exactly as a layer's does (before its FX), so
      moving a track never changes whether its gain is heard.
    - `layers` are the other sounding synth lanes (stash format, as
      `StudioState.other_layers()` gives them); `drums_fx` the drum bus.
    - `flat` False: `arr` is the section's patterns as they are. The main
      lane, its layers and the beat each loop at their own length inside
      the cycle, which is `lcm(arr.bars, arr.drum_bars)`: exactly what the
      legacy loop/scene render was, key for key. `flat` True: every lane is
      tiled onto one grid `cycle_bars` long (`bars == drum_bars ==
      cycle_bars`). That happens when a layer's grid differs from the main
      lane's, when the section's bars cut the cycle short, or when a take
      makes the whole section one cycle. `window()` cuts a cycle into
      chunks.
    - `auto` / a layer's `"auto"` / `gains` / `fx_auto`: the section's
      automation lanes (`_with_lanes`; always a flat cycle that is the
      whole section). `fx_auto` = {stem name: [[target, points]]} are the
      FX-parameter lanes, which each stem's chain plays (`fxauto`).
      `auto` = {curves: {field: points}, at, n} is one synth lane's field
      lanes over the `n` samples of the cycle, read from sample `at` on —
      0 for the cycle, a chunk's own start in `window()` — so a chunk plays
      its slice of the lane. `gains` = {stem name: points} are the level
      lanes, applied to the finished stems.
    """

    arr: Any
    main_patch: dict
    curves: dict
    main_fx: tuple
    main_muted: bool
    layers: tuple
    drums_fx: tuple
    cycle_bars: int
    flat: bool = False
    # main-lane steps where a held note attacks again (a tiled pattern's
    # repeat starts); a flat layer carries its own as `"breaks"`
    breaks: tuple = ()
    main_gain: float = 1.0
    # a flat cycle's arp: (the pattern's pitches, first step, period) —
    # each chunk plays the whole pattern's arp at its place in the cycle
    arp: tuple = ()
    # a chunk whose first main-lane note slides: the pitch it slides from,
    # the last note before the chunk (`window`); a layer's is its `"lead"`
    lead: str | None = None
    # the section's automation lanes (see the docstring); empty without any
    auto: dict = field(default_factory=dict)
    gains: dict = field(default_factory=dict)
    fx_auto: dict = field(default_factory=dict)
    # the long audio ONE pass of the section plays (`pass_cuts`): added
    # onto the cycle after its stems, dry. Empty for every pass no long
    # item crosses, which is every pass of a song without one
    under: tuple = ()
    # the mixer (`_with_mix`): {stem name: {"pan": -1..1, "sends": {return
    # id: 0..1}, "gain" (the drums' fader)}} for the stems that have any, and
    # the returns they feed
    # as ({"id", "fx" (fully wet), "gain"}, ...). Both empty for a song
    # that uses neither — every song from before the mixer — so no key
    # gains them. A section with either renders through `_cycle_stems`
    mix: dict = field(default_factory=dict)
    returns: tuple = ()
    # {stem name: [track ids]}: whose stem is whose, for the levels a
    # render records (`render.SectionAudio.levels`). In no key
    tracks: dict = field(default_factory=dict)
    # the audio tracks whose takes are a stem of their own (`_own_takes`:
    # a track with a rack or a lane here), each `(stem name "clips:<track
    # id>", its placed takes, its chain)`; their takes are not in
    # `arr.clips`. Rendered over the whole cycle through the chain
    # (`render._take_stems`). Empty for every song whose audio tracks have
    # neither, so no key gains it
    takes: tuple = ()

    @property
    def audio_clips(self) -> list:
        """The section's takes, placed (they ride `arr.clips`)."""
        return self.arr.clips

    @property
    def clock(self) -> _meter.Clock:
        """How long this cycle's bars last (`meter.arr_clock`)."""
        return _meter.arr_clock(self.arr)

    @property
    def bar_seconds(self) -> float:
        """A bar's length — under a ramp its longest, so a chunk of bars
        never passes the render cap."""
        beats = _meter.bar_beats(getattr(self.arr, "meter", _meter.DEFAULT))
        if self.arr.ramp:
            return beats * 60.0 / min(self.arr.bpm, self.arr.ramp[0])
        return beats * 60.0 / self.arr.bpm

    def inputs(self, *, clip_root=None, master_fx=(), parts=SECTION_PARTS):
        """These parts as `RenderInputs` (no master FX unless given)."""
        from ..synth import Patch
        from .render import RenderInputs

        return RenderInputs(
            patch=Patch.from_dict(self.main_patch),
            arr=self.arr,
            curves=self.curves,
            layers=self.layers,
            main_muted=self.main_muted,
            main_fx=self.main_fx,
            master_fx=master_fx,
            drums_fx=self.drums_fx,
            parts=parts,
            clip_root=clip_root,
            breaks=self.breaks,
            main_gain=self.main_gain,
            arp=self.arp,
            lead=self.lead,
            auto=self.auto or None,
        )

    def flatten(self) -> SectionParts:
        """The same cycle with every lane tiled onto one cycle-long grid."""
        if self.flat:
            return self
        return _flat(self, [self.arr.bars] * len(self.layers), self.cycle_bars)

    def window(self, b0: int, b1: int) -> SectionParts:
        """Bars [b0, b1) of the (flattened) cycle as parts of their own,
        `b1 - b0` bars long: notes, beat and automation cropped and shifted
        to start at 0. A held note belongs to the window it starts in and
        keeps its full length there (its steps past the window's end ride
        along), so a chunk boundary never re-attacks it. A slide on the
        window's first note glides from the note before the window
        (`lead`), as it does in the cycle rendered whole. Takes are left
        out: a take can cross windows, so the renderer places the takes
        over the whole cycle instead. An automation lane keeps its whole
        shape and is read from the window's first sample (`auto["at"]`)."""
        f = self.flatten()
        a, s, ds = f.arr, f.arr.steps_per_bar, f.arr.drum_steps
        lo, hi = b0 * s, b1 * s
        clock = f.clock
        at = clock.bar_sample(b0)
        whole = (b0, b1) == (0, f.cycle_bars)
        drums, hits = _window_drums(a, b0 * ds, b1 * ds, f.cycle_bars * ds)
        arr = replace(
            a,
            # a ramp, seen from the window's first bar
            ramp=list(clock.window(b0).ramp),
            bars=b1 - b0,
            drum_bars=b1 - b0,
            cells=_crop_runs(a.cells, lo, hi, f.breaks, a.cuts),
            cuts=[[c[0], int(c[1]) - lo] for c in a.cuts if lo <= int(c[1]) < hi],
            velocities=_crop_keys(a.velocities, b0 * s, b1 * s),
            slides=[x - b0 * s for x in a.slides if b0 * s <= x < b1 * s],
            drums=drums,
            drum_vel=_crop_keys(a.drum_vel, b0 * ds, b1 * ds),
            drum_hits=hits,
            clips=[],
            note_nudge=_crop_nudge(a.note_nudge, lo, hi, whole),
            note_chance=_crop_keys(a.note_chance, lo, hi),
        )
        c = f.cycle_bars
        return replace(
            f,
            arr=arr,
            curves={k: _crop_curve(v, b0 / c, b1 / c) for k, v in f.curves.items()},
            layers=tuple(_crop_layer(lay, lo, hi, at, whole) for lay in f.layers),
            auto=_auto_from(f.auto, at),
            cycle_bars=b1 - b0,
            breaks=tuple(b - lo for b in f.breaks if b > lo),
            arp=(f.arp[0], f.arp[1] + lo, *f.arp[2:]) if f.arp else (),
            lead=(
                _lead(a.cells, lo, hi, f.breaks, a.cuts, a.slides)
                if a.arp_mode == "off"
                else None
            ),
        )


def section_arrangement(song: dict, section: dict) -> SectionParts:
    """A section as `SectionParts`: the one bridge from the song model
    into the render engine. It is built on `song_bridge.section_loop`, so a
    migrated scene renders exactly what it rendered.

    Every lane loops at its own pattern's length and they realign at each
    cycle start (`cycle_bars`). When every synth lane shares the main lane's
    grid and the cycle is the lcm of the main lane and the beat, the
    arrangement is the section's own (`flat` False). Otherwise it is tiled
    onto one cycle-long grid (`flat` True). A layer on another step
    resolution is moved onto the main lane's: a coarser note fills the
    finer steps it covers, and a finer one lands on the step it starts in.

    A section with a trimmed block (`spans`) is always tiled, onto the
    whole section: each lane's pattern from its block's first bar to its
    last (`_flat`'s `where`), nothing outside."""
    from . import song_bridge
    from .state import Arrangement

    m = section_meter(song, section)
    ramp = _meter.ramp_of(
        section_bpm(song, section), section.get("ramp"), section["bars"], m
    )
    # its patterns in its own bar, the song at its own tempo (the song
    # itself for a 4/4 section at the song's tempo)
    song = in_meter(song, section)
    p = song_bridge.section_loop(song, section)
    if m != _meter.DEFAULT or ramp:
        p["arr"] = {**p["arr"], "meter": m, "ramp": ramp}
    # an 8-bar pool pattern (`limits.PATTERN_BAR_CHOICES`) is longer than
    # any loop's grid: the Arrangement's own node would call its bars
    # invalid, so they are carried round it. Nothing differs for a
    # pattern of 1-4 bars — every song from before them.
    raw = p["arr"]
    long_bars = {
        k: int(raw[k])
        for k in ("bars", "drum_bars")
        if raw.get(k) not in limits.BAR_CHOICES
        and raw.get(k) in limits.PATTERN_BAR_CHOICES
    }
    if long_bars:
        raw = {**raw, **dict.fromkeys(long_bars, 1)}
    lanes = section_lanes(song, section)
    takes, rest = _own_takes(song, section, raw["clips"], lanes)
    if takes:
        raw = {**raw, "clips": rest}
    arr = Arrangement(**schema.conform(raw, _nodes().ARRANGEMENT))
    if long_bars:
        arr = replace(arr, **long_bars)
    pool = song["patterns"]
    spans = section.get("spans") or {}
    lane_tids = p["lane_tracks"]
    heard = [
        (lay, pool[pid], tid)
        for lay, pid, tid in zip(
            p["layers"], p["lane_patterns"][1:], lane_tids[1:], strict=True
        )
        if not lay["muted"] and lay["patch"] and lay["cells"]
    ]
    sounding = [(lay, pat) for lay, pat, _tid in heard]
    main = p["main"] or {"fx": [], "muted": False, "gain": 1.0}
    cycle = cycle_bars(song, section)
    own_takes = [name.split(":", 1)[1] for name, _c, _fx in takes]

    def automated(out: SectionParts) -> SectionParts:
        if not lanes:
            return out
        tids = p["lane_tracks"]
        heard = [
            tid
            for lay, tid in zip(p["layers"], tids[1:], strict=True)
            if not lay["muted"] and lay["patch"] and lay["cells"]
        ]
        drums = next((t["id"] for t in song["tracks"] if t["kind"] == "drums"), None)
        return _with_lanes(
            out, lanes, tids[0] if tids else None, heard, drums, own_takes
        )

    def mixed(out: SectionParts) -> SectionParts:
        stems = {"main": lane_tids[0] if lane_tids else None}
        stems |= {f"layer{i + 2}": tid for i, (_l, _p, tid) in enumerate(heard)}
        stems["drums"] = next(
            (t["id"] for t in song["tracks"] if t["kind"] == "drums"), None
        )
        return _with_mix(out, song, section, stems, own_takes)

    parts = SectionParts(
        arr=arr,
        main_patch=p["patch"] if p["patch"] is not None else _default_patch(),
        curves=p["curves"] or {},
        main_fx=tuple(main["fx"]),
        main_muted=bool(main["muted"]),
        main_gain=float(main["gain"]),
        layers=tuple(lay for lay, _pat in sounding),
        drums_fx=tuple(p["fx_drums"] or []),
        cycle_bars=cycle,
        takes=takes,
    )
    s = arr.steps_per_bar
    same_grid = all(
        (pat["bars"], pat["steps_per_bar"]) == (arr.bars, s) for _l, pat in sounding
    )
    native = cycle == math.lcm(max(1, arr.bars), max(1, arr.drum_bars))
    # a lane resting notes by chance is always tiled: each pass of its
    # pattern rolls afresh (`arrange.chance_tile`), where a native cycle
    # repeats a lane's one render
    chance = bool(arr.note_chance) or any(
        pat.get("note_chance") for _l, pat in sounding
    )
    if same_grid and native and not spans and not chance:
        # each layer plays its own pattern's velocities, slides and arp
        # (`lane_render`: only what differs from the legacy render, so a
        # migrated scene keeps its key)
        own = tuple(
            lay
            | song_bridge.lane_render(
                song_bridge.pattern_seq(pat, s),
                lay["cells"],
                arr.velocities,
                max(1, arr.bars) * s,
            )
            for lay, pat in sounding
        )
        return mixed(automated(replace(parts, layers=own)))
    regridded = []
    for lay, pat in sounding:
        cells = _regrid(lay["cells"], pat["bars"], pat["steps_per_bar"], s)
        seq = song_bridge.pattern_seq(pat, s)
        # lanes loop at their own lengths here, so a layer's own velocities
        # are always carried (never compared with the main lane's)
        regridded.append(
            {
                **lay,
                "cells": cells,
                **song_bridge.lane_render(seq, cells, None, pattern_bars(pat) * s),
            }
        )
    periods = [pattern_bars(pat) for _l, pat in sounding]
    where = None
    if spans:

        def block(tid) -> tuple[int, int] | None:
            got = spans.get(tid)
            return (got[0], got[0] + got[1]) if got else None

        drums = next((t["id"] for t in song["tracks"] if t["kind"] == "drums"), None)
        where = {
            "main": block(lane_tids[0]) if lane_tids else None,
            "layers": [block(tid) for _l, _p, tid in heard],
            "drums": block(drums),
        }
    flat = _flat(replace(parts, layers=tuple(regridded)), periods, cycle, where)
    return mixed(automated(flat))


def _own_takes(
    song: dict, section: dict, clips: list, lanes: dict
) -> tuple[tuple, list]:
    """The section's takes split by who renders them: `(own, rest)`. `own`
    is one `(stem name, takes, chain)` an audio track that sounds and has
    a rack or a lane here (`SectionParts.takes`), in song order; `rest`
    the placed takes (`clips`, as `song_bridge.section_loop` lists them)
    that stay in the one shared takes stem. No such track: `((), clips)`,
    so every song from before the audio rack keeps its stems and keys."""
    from .fxrack import clean_chain  # lazy: fxrack imports state

    audio = {t["id"]: t for t in song["tracks"] if t["kind"] == "audio"}
    whose = [
        a["track_id"]
        for a in section["audio"]
        if a["track_id"] in audio and audible(song, audio[a["track_id"]])
    ]
    chains = {
        tid: clean_chain(list(t.get("fx") or []))
        for tid, t in audio.items()
        if tid in whose
    }
    split = [tid for tid in audio if tid in chains and (chains[tid] or lanes.get(tid))]
    if not split:
        return (), clips
    own = tuple(
        (
            f"clips:{tid}",
            tuple(c for c, w in zip(clips, whose, strict=True) if w == tid),
            tuple(chains[tid]),
        )
        for tid in split
    )
    rest = [c for c, w in zip(clips, whose, strict=True) if w not in split]
    return own, rest


def _with_mix(
    parts: SectionParts, song: dict, section: dict, stems: dict, own_takes=()
):
    """`parts` carrying the mixer's pan and sends (`stems`: {stem name:
    track id}, the names `render._stem_jobs` gives the main lane, layer i
    and the drums). Only a track that sounds and has a pan, a send above
    0 or (the drums) a level off 1 gets an entry, and only a return such
    a track feeds is carried — its chain fully wet (`mixer.wet_chain`).
    Nothing is flattened here: the renderer takes the cycle-stems path for
    a section with an entry."""
    from . import mixer

    by_id = {t["id"]: t for t in song["tracks"]}
    buses = {r["id"]: r for r in song.get("returns") or ()}
    mix: dict = {}
    for stem, tid in stems.items():
        t = by_id.get(tid)
        if t is None or not audible(song, t):
            continue
        own: dict = {}
        if t["kind"] == "drums" and t["gain"] != 1.0:
            # a synth track's gain is in its stem (before its chain); the
            # drums' had no reader at all until the mixer: it is a fader
            # past the drum bus
            own["gain"] = float(t["gain"])
        if t.get("pan"):
            own["pan"] = float(t["pan"])
        sends = {
            rid: float(v)
            for rid, v in (t.get("sends") or {}).items()
            if v > 0 and rid in buses and buses[rid].get("gain", 1.0) > 0
        }
        if sends:
            own["sends"] = sends
        if own:
            mix[stem] = own
    fed = {rid for own in mix.values() for rid in own.get("sends", ())}
    returns = tuple(
        {
            "id": r["id"],
            "fx": mixer.wet_chain(r["fx"]),
            "gain": float(r.get("gain", 1.0)),
        }
        for r in song.get("returns") or ()
        if r["id"] in fed
    )
    tracks = {stem: [tid] for stem, tid in stems.items() if tid in by_id}
    takes = sorted({a["track_id"] for a in section.get("audio") or ()} - set(own_takes))
    if takes:
        tracks["clips"] = takes
    # an audio track with a rack or a lane here: a stem (and a meter) of
    # its own
    tracks |= {f"clips:{tid}": [tid] for tid in own_takes}
    return replace(parts, mix=mix, returns=returns, tracks=tracks)


def _with_lanes(
    parts: SectionParts,
    lanes: dict,
    main_tid,
    layer_tids: list,
    drums_tid,
    take_tids=(),
) -> SectionParts:
    """`parts` carrying the section's lanes (`section_lanes`; `layer_tids`
    are the tracks behind `parts.layers`, in order). The cycle is the whole
    section (`cycle_bars`) and is flattened: a lane runs across it once, so
    no lane's pattern may loop inside it on a stem of its own.

    - A field lane on the main lane's track replaces that field's curve
      (the lane wins) and rides `auto`; on a layer's track it rides the
      layer dict as `"auto"` — both resolved per note by `build_song`,
      through the machinery a loop's curves use.
    - A gain lane goes to `gains` under its stem's name, the ones
      `render._stem_jobs` gives the main lane, layer i and the drums.
    - An FX-parameter lane goes to `fx_auto` under the same stem names:
      the stem's chain plays it (`render._cycle_stems`, `fxauto`).
    - An audio track's (`take_tids`: those whose takes are a stem of their
      own, "clips:<id>") are a level and its FX: on its takes' stem."""
    f = parts.flatten()
    n = f.clock.samples(f.cycle_bars)
    gains: dict = {}
    fx_auto: dict = {}

    def split(tid, stem: str) -> dict:
        """A track's level and FX lanes into `gains` / `fx_auto` under its
        stem's name; its field lanes come back, as `build_song` takes them."""
        fields: dict = {}
        for ln in lanes.get(tid, ()):
            points = [list(pt) for pt in ln["points"]]
            if ln["target"] == GAIN:
                gains[stem] = points
            elif parse_fx(ln["target"]):
                fx_auto.setdefault(stem, []).append([ln["target"], points])
            else:
                fields[ln["target"]] = points
        return {"curves": fields, "at": 0, "n": n} if fields else {}

    auto = split(main_tid, "main")
    layers = []
    for i, (lay, tid) in enumerate(zip(f.layers, layer_tids, strict=True)):
        own = split(tid, f"layer{i + 2}")
        layers.append({**lay, "auto": own} if own else lay)
    split(drums_tid, "drums")  # a drums track has no field lanes
    for tid in take_tids:  # nor has an audio track
        split(tid, f"clips:{tid}")
    return replace(
        f,
        curves={k: v for k, v in f.curves.items() if k not in auto.get("curves", ())},
        auto=auto,
        layers=tuple(layers),
        gains=gains,
        fx_auto=fx_auto,
    )


def _auto_from(auto: dict, at: int) -> dict:
    """A lane set read from `at` samples further into its cycle."""
    return {**auto, "at": auto["at"] + at} if auto else auto


def _default_patch() -> dict:
    from ..synth import Patch

    return Patch().to_json()


def _flat(
    parts: SectionParts, layer_bars: list[int], cycle: int, where: dict | None = None
) -> SectionParts:
    """`parts` as the section's own patterns (the main lane loops at
    `arr.bars`, layer i at `layer_bars[i]`, the beat at `arr.drum_bars`)
    tiled onto one grid `cycle` bars long.

    `where` (a section with trimmed blocks): {"main", "drums": (lo, hi) or
    None, "layers": [(lo, hi) or None, ...]} — the bars of the cycle each
    lane's block covers, None for the whole of it. A lane's pattern is then
    tiled from `lo` (its bar 1 on `lo`) up to `hi` and plays nothing
    outside: a note is cut at `hi`, a repeat's first note attacks again
    (`breaks`), the arp counts its phase from `lo` and is silent outside
    (`arrange._lane_timeline`'s gated form), and the main lane's sound
    curves start again at `lo`. Without `where` nothing here differs from
    the tiling it always was — key for key."""
    where = where or {}
    a = parts.arr
    s, ds = a.steps_per_bar, a.drum_steps
    main_p, drum_p = max(1, a.bars) * s, max(1, a.drum_bars) * ds
    lo, hi = _block_steps(where.get("main"), cycle, s)
    d_lo, d_hi = _block_steps(where.get("drums"), cycle, ds)
    blocks = where.get("layers") or [None] * len(parts.layers)
    arr = replace(
        a,
        bars=cycle,
        drum_bars=cycle,
        cells=_tile(a.cells, main_p, cycle * s, lo, hi),
        cuts=_tile(a.cuts, main_p, cycle * s, lo, hi),
        velocities=_tile_keys(a.velocities, main_p, cycle * s, lo, hi),
        slides=[
            t for _x, t in _tile([[0, x] for x in a.slides], main_p, cycle * s, lo, hi)
        ],
        drums=_tile(a.drums, drum_p, cycle * ds, d_lo, d_hi),
        drum_vel=_tile_keys(a.drum_vel, drum_p, cycle * ds, d_lo, d_hi),
        drum_hits=_tile_hits(a.drum_hits, a.drums, drum_p, cycle * ds, d_lo, d_hi),
        clips=[c for c in a.clips if int(c.get("at_bar", 0)) < cycle],
        # timing inside a step follows its notes; a step's chance rolls
        # afresh on every pass (`arrange.chance_tile`). Both stay empty
        # for a lane without them, so no key gains them
        note_nudge=_tile_keys(a.note_nudge, main_p, cycle * s, lo, hi),
        note_chance=chance_tile(a.note_chance, main_p, cycle * s, lo, hi),
    )
    gate = (lo, hi) if where.get("main") else ()
    return replace(
        parts,
        arr=arr,
        curves={
            k: _tile_curve(v, max(1, a.bars), cycle, lo // s)
            for k, v in parts.curves.items()
        },
        layers=tuple(
            _tile_layer(
                lay, max(1, b) * s, cycle * s, blk and _block_steps(blk, cycle, s)
            )
            for lay, b, blk in zip(parts.layers, layer_bars, blocks, strict=True)
        ),
        cycle_bars=cycle,
        flat=True,
        breaks=tuple(range(lo + main_p, hi, main_p)),
        arp=(
            (tuple(pitches), 0, main_p, *gate)
            if a.arp_mode != "off" and (pitches := _arp_pitches(a.cells, main_p))
            else ()
        ),
    )


# how many passes of its grid a loop with chance plays as its cycle (so
# chance varies every pass), fewer when that
# many would pass the loop cap (`render.LOOP_CAP_SECONDS`)
LOOP_CHANCE_PASSES = 4


def loop_chance(inp) -> bool:
    """Does a loop's `RenderInputs` rest any note by chance — its main
    lane's `note_chance` or a layer's (a following layer rolls its main
    lane's) — in a render that plays the synth?"""
    if "synth" not in inp.parts:
        return False
    return bool(inp.arr.note_chance) or any(
        isinstance(lay, dict) and lay.get("note_chance") for lay in inp.layers
    )


def loop_passes(inp, cap_seconds: float):
    """A loop's `RenderInputs` as `LOOP_CHANCE_PASSES` passes of itself when
    its notes rest by chance (`loop_chance`), else `inp` itself: the
    browser loops one WAV, so a loop that rolled one pass would rest the
    same notes every time round. Every lane is tiled as a section's flat
    cycle is (`_flat`: the main lane, each layer, the beat, the curves,
    the arp), each pass of the grid rolling its chance afresh
    (`arrange.chance_tile`) — the first pass is the grid's own roll — and
    the loop's takes (`arr.clips`) start again on each pass. `passes`
    says how many (in the key, and on the URL the player reads:
    `render.preview_url`). Fewer passes when that many would pass
    `cap_seconds`; one is the loop as it is."""
    if not loop_chance(inp):
        return inp
    from .arrange import loop_bars

    a = inp.arr
    one = loop_bars(a, tuple(p for p in inp.parts if p in ("synth", "drums")))
    clock = _meter.arr_clock(a)
    k = LOOP_CHANCE_PASSES
    while k > 1 and clock.seconds(one * k) > cap_seconds:
        k -= 1
    if k < 2:
        return inp
    cycle = one * k
    parts = SectionParts(
        arr=a,
        main_patch={},
        curves=inp.curves or {},
        main_fx=(),
        main_muted=inp.main_muted,
        layers=tuple(inp.layers),
        drums_fx=(),
        cycle_bars=one,
    )
    flat = _flat(parts, [max(1, a.bars)] * len(inp.layers), cycle)
    clips = [
        {**c, "at_bar": int(c.get("at_bar", 0)) + r * one}
        for r in range(k)
        for c in a.clips
        if int(c.get("at_bar", 0)) < one
    ]
    return replace(
        inp,
        arr=replace(flat.arr, clips=clips),
        curves=flat.curves,
        layers=flat.layers,
        breaks=flat.breaks,
        arp=flat.arp,
        passes=k,
    )


def _block_steps(block, cycle: int, steps: int) -> tuple[int, int]:
    """A block's (lo, hi) bars as steps of a `steps`-a-bar grid, held to
    the cycle; None is the whole cycle."""
    if not block:
        return 0, cycle * steps
    lo = min(max(0, int(block[0])), cycle - 1)
    hi = min(max(lo + 1, int(block[1])), cycle)
    return lo * steps, hi * steps


def _arp_pitches(cells, period: int) -> list[str]:
    """The pitches a pattern's arp cycles through: the rows with a note
    inside its own grid — what a loop arpeggiates (`arrange._playing_steps`:
    a run must start inside the grid, so a row plays exactly when it has a
    cell there). A note kept, hidden, past a shrunk grid used to join a
    section's arp and not the loop's."""
    return sorted({c[0] for c in cells if 0 <= int(c[1]) < period})


def _tile_layer(lay: dict, period: int, total: int, block=None) -> dict:
    """A layer's pattern (one `period`) looped out to `total` steps, as the
    main lane is in `_flat`: its cells, its own velocities and slides, and
    -- its arp on -- an `"arp"` of the pattern's pitches restarting every
    `period` steps (the main lane's `SectionParts.arp`). `block` = (lo, hi)
    steps: a trimmed block, tiled from `lo` up to `hi` and silent outside
    (`_flat`)."""
    lo, hi = block or (0, total)
    out = {**lay, "cells": _tile(lay.get("cells") or [], period, total, lo, hi)}
    if lo + period < hi:
        out["breaks"] = list(range(lo + period, hi, period))
    if "velocities" in lay:
        out["velocities"] = _tile_keys(lay["velocities"], period, total, lo, hi)
    if "cuts" in lay:
        out["cuts"] = _tile(lay["cuts"], period, total, lo, hi)
    if "slides" in lay:
        slides = [[0, x] for x in lay["slides"]]
        out["slides"] = [t for _x, t in _tile(slides, period, total, lo, hi)]
    if lay.get("note_nudge"):
        out["note_nudge"] = _tile_keys(lay["note_nudge"], period, total, lo, hi)
    if lay.get("note_chance"):  # every pass rolls afresh (`chance_tile`)
        out["note_chance"] = chance_tile(lay["note_chance"], period, total, lo, hi)
    if lay.get("arp_mode", "off") != "off" and (
        pitches := _arp_pitches(lay.get("cells") or [], period)
    ):
        out["arp"] = [pitches, 0, period, *(block or ())]
    return out


def _crop_layer(lay: dict, lo: int, hi: int, at: int = 0, whole: bool = True) -> dict:
    """Steps [lo, hi) of a tiled layer, shifted to start at 0, as
    `SectionParts.window` crops the main lane (`at`: the window's first
    sample, where its automation lanes are read from; `whole`: the window
    is the whole cycle — `_crop_nudge`)."""
    brk = lay.get("breaks", ())
    out = {**lay, "cells": _crop_runs(lay["cells"], lo, hi, brk, lay.get("cuts"))}
    if lay.get("auto"):
        out["auto"] = _auto_from(lay["auto"], at)
    if "cuts" in lay:
        out["cuts"] = [
            [c[0], int(c[1]) - lo] for c in lay["cuts"] if lo <= int(c[1]) < hi
        ]
    out.pop("breaks", None)
    if moved := [b - lo for b in brk if b > lo]:
        out["breaks"] = moved
    if "velocities" in lay:
        out["velocities"] = _crop_keys(lay["velocities"], lo, hi)
    if "slides" in lay:
        out["slides"] = [x - lo for x in lay["slides"] if lo <= x < hi]
    if lay.get("note_nudge"):
        out["note_nudge"] = _crop_nudge(lay["note_nudge"], lo, hi, whole)
    if lay.get("note_chance"):  # its values keep their pass: the roll holds
        out["note_chance"] = _crop_keys(lay["note_chance"], lo, hi)
    if lay.get("arp"):
        pitches, start, *rest = lay["arp"]  # rest: the period, a block's gate
        out["arp"] = [pitches, start + lo, *rest]
    out.pop("lead", None)
    if lay.get("arp_mode", "off") == "off" and (
        lead := _lead(
            lay["cells"], lo, hi, brk, lay.get("cuts"), lay.get("slides") or ()
        )
    ):
        out["lead"] = lead
    return out


def _runs(cells, breaks, cuts=()) -> list[tuple[int, int, str]]:
    """A lane's notes as (start_step, length, pitch), in the order the
    slide scheduler plays them (`arrange._lane_timeline`, sorted: by start,
    then length, then pitch) — runs split as `arrange._merge_runs` does."""
    brk = set(breaks)
    cut = {(c[0], int(c[1])) for c in cuts or ()}
    by_pitch: dict[str, list[int]] = {}
    for c in cells:
        by_pitch.setdefault(c[0], []).append(int(c[1]))
    runs: list[list] = []
    for pitch, steps in by_pitch.items():
        prev = None
        for st in sorted(set(steps)):
            if prev is None or st != prev + 1 or st in brk or (pitch, st) in cut:
                runs.append([st, 1, pitch])
            else:
                runs[-1][1] += 1
            prev = st
    return sorted((st, n, pitch) for st, n, pitch in runs)


def _lead(cells, lo: int, hi: int, breaks, cuts, slides) -> str | None:
    """The pitch the first note starting in steps [lo, hi) slides from:
    the note played just before it, when that first note is on a slide
    step and something came before the window. None otherwise — a chunk
    with no slide on its first note renders as it always did (and keeps
    its key). Each chunk of a cycle builds its own song, so without this
    the slide across a chunk seam lost its glide."""
    runs = _runs(cells, breaks, cuts)
    first = next((r for r in runs if lo <= r[0] < hi), None)
    if first is None or first[0] not in {int(x) for x in slides}:
        return None
    before = [r for r in runs if r[0] < lo]
    return before[-1][2] if before else None


def _crop_runs(cells, lo: int, hi: int, breaks, cuts=()) -> list[list]:
    """The cells of every note (a run of one pitch on consecutive steps,
    split at `breaks` and the row's `cuts`, as `arrange._merge_runs` joins
    them) that STARTS in [lo, hi), shifted by -lo, including its steps past
    `hi`."""
    brk = set(breaks)
    cut = {(c[0], int(c[1])) for c in cuts or ()}
    by_pitch: dict[str, list[int]] = {}
    for c in cells:
        by_pitch.setdefault(c[0], []).append(int(c[1]))
    keep: set[tuple[str, int]] = set()
    for pitch, steps in by_pitch.items():
        start = prev = None
        for st in sorted(steps):
            if prev is None or st != prev + 1 or st in brk or (pitch, st) in cut:
                start = st
            if lo <= start < hi:
                keep.add((pitch, st))
            prev = st
    return [[c[0], int(c[1]) - lo] for c in cells if (c[0], int(c[1])) in keep]


def _tile(cells, period: int, total: int, lo: int = 0, hi: int | None = None):
    """[x, step] pairs looping every `period` steps, out to `total` steps.
    A step past the pattern's own grid is never heard, so it is dropped.
    `lo` / `hi` (a trimmed block): the loop starts at step `lo` — the
    pattern's first step lands there — and nothing lands at or past `hi`."""
    hi = total if hi is None else min(hi, total)
    own = [c for c in cells if 0 <= int(c[1]) < period]
    return [
        [c[0], lo + r * period + int(c[1])]
        for r in range(-(-(hi - lo) // period))
        for c in own
        if lo + r * period + int(c[1]) < hi
    ]


def _tile_keys(
    values: dict, period: int, total: int, lo: int = 0, hi: int | None = None
) -> dict:
    """A {str(step): v} lane looping every `period` steps (from `lo` up to
    `hi`, as `_tile`)."""
    return {
        str(t): values[k]
        for k in values
        for _x, t in _tile([[0, int(k)]], period, total, lo, hi)
    }


def _tile_hits(
    hits: dict, cells, period: int, total: int, lo: int = 0, hi: int | None = None
) -> dict:
    """A beat's per-hit table following its cells through `_tile`. A hit
    with a chance keeps what it is rolled from as `k` — the seed of its row
    IN THE PATTERN and its step there (`arrange.hit_roll`) — so every
    repeat of the pattern, and every chunk a cycle is rendered in, rolls
    what the pattern rolls looping on its own."""
    if not hits:
        return {}
    from .arrange import chance_seed  # lazy: arrange imports state

    rows: dict[str, list[int]] = {}
    for v, s in cells:
        if 0 <= int(s) < period:
            rows.setdefault(v, []).append(int(s))
    out: dict = {}
    for key, h in hits.items():
        voice, _dot, raw = key.partition(".")
        step = int(raw)
        if step >= period or step not in rows.get(voice, ()):
            continue
        if float(h.get("p", 1.0)) < 1.0 and "k" not in h:
            h = {**h, "k": [chance_seed(voice, rows[voice]), step]}
        for _x, t in _tile([[voice, step]], period, total, lo, hi):
            out[f"{voice}.{t}"] = h
    return out


def _window_drums(a, lo: int, hi: int, cycle: int) -> tuple[list[list], dict]:
    """Steps [lo, hi) of a tiled beat (`cycle` steps long) as a window's
    cells and per-hit table. A hit nudged EARLY on a window's first step
    sounds before that window starts: it belongs to the window before it —
    the cycle's first step to the cycle's last window, where the loop
    wraps it (`arrange.drum_hits`) — and rides there one step past that
    window's own grid, marked `pk`, with the velocity of its own step. The
    whole cycle as one window moves nothing."""
    drums, data = _crop(a.drums, lo, hi), a.drum_hits
    if not data:
        return drums, {}
    hits = {}
    for key, h in data.items():
        voice, _dot, raw = key.partition(".")
        if lo <= int(raw) < hi:
            hits[f"{voice}.{int(raw) - lo}"] = h
    if (lo, hi) == (0, cycle):
        return drums, hits

    def early(step: int) -> list[str]:
        return [
            v
            for v, s in a.drums
            if int(s) == step and float((data.get(f"{v}.{step}") or {}).get("n", 0)) < 0
        ]

    for v in early(lo):
        drums.remove([v, 0])
        hits.pop(f"{v}.0", None)
    nxt = hi % cycle
    for v in early(nxt):
        h = {**data[f"{v}.{nxt}"], "pk": 1}
        h.setdefault("v", float(a.drum_vel.get(str(nxt), 1.0)))
        drums.append([v, hi - lo])
        hits[f"{v}.{hi - lo}"] = h
    return drums, hits


def chance_tile(chance, period: int, total: int, lo: int = 0, hi=None) -> dict:
    """`arrange.chance_tile` (lazy: arrange imports state)."""
    if not chance:
        return {}
    from .arrange import chance_tile as tile

    return tile(chance, period, total, lo, hi)


def _crop_nudge(nudge: dict, lo: int, hi: int, whole: bool) -> dict:
    """Steps [lo, hi) of a tiled lane's timing inside a step (`_crop_keys`).
    The whole cycle as one window is a circle, as a loop is (a note nudged
    early on its first step sounds at its end: `arrange._lane_timeline`);
    a chunk of a longer cycle is not, so a note nudged early on a chunk's
    first step plays on that step there (at most half a step late) rather
    than at the chunk's end."""
    out = _crop_keys(nudge, lo, hi)
    if not whole and float(out.get("0", 0.0)) < 0.0:
        out.pop("0")
    return out


def _crop(cells, lo: int, hi: int) -> list[list]:
    return [[c[0], int(c[1]) - lo] for c in cells if lo <= int(c[1]) < hi]


def _crop_keys(values: dict, lo: int, hi: int) -> dict:
    return {str(int(k) - lo): v for k, v in values.items() if lo <= int(k) < hi}


def _regrid(cells, bars: int, spb: int, to: int) -> list[list]:
    """A layer's cells on its own `spb` grid, moved onto a `to`-step grid:
    a coarser step fills every finer step it covers (so its length is
    kept), a finer one lands on the step it starts in."""
    own = [c for c in cells if 0 <= int(c[1]) < max(1, bars) * spb]
    if spb == to:
        return [list(c) for c in own]
    if spb < to:
        k = max(1, round(to / spb))
        return [[c[0], int(c[1]) * k + i] for c in own for i in range(k)]
    k = max(1, round(spb / to))
    seen = dict.fromkeys((c[0], int(c[1]) // k) for c in own)
    return [list(c) for c in seen]


def _tile_curve(points, period: int, total: int, lo: int = 0) -> list[list]:
    """An automation curve drawn over one `period`-bar loop of the main
    lane, repeated across `total` bars (x in 0..1 of the whole). Each
    repeat holds its first and last values out to its edges, as a single
    loop does. `lo` (a trimmed block's first bar): the repeats start there,
    so the curve restarts with the pattern; before it the curve's first
    value is held (nothing plays there)."""
    if len(points) < 2:
        return [list(p) for p in points]
    out: list[list] = []
    if not lo:
        for r in range(-(-total // period)):
            x0, x1 = r * period / total, (r + 1) * period / total
            out.append([x0, points[0][1]])
            out += [[x0 + t * (x1 - x0), v] for t, v in points]
            out.append([x1, points[-1][1]])
        return out
    reps = -(-(total - lo) // period)
    whole = lo + reps * period  # the repeats may run past the cycle's end
    out.append([0.0, points[0][1]])
    for r in range(reps):
        x0, x1 = (lo + r * period) / whole, (lo + (r + 1) * period) / whole
        out.append([x0, points[0][1]])
        out += [[x0 + t * (x1 - x0), v] for t, v in points]
        out.append([x1, points[-1][1]])
    return out if whole == total else _crop_curve(out, 0.0, total / whole)


def _crop_curve(points, x0: float, x1: float) -> list[list]:
    """The part of a curve between x0 and x1, rescaled to 0..1."""
    if len(points) < 2:
        return [list(p) for p in points]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    span = x1 - x0
    mid = [[(x - x0) / span, y] for x, y in zip(xs, ys, strict=True) if x0 < x < x1]
    return [[0.0, _interp(x0, xs, ys)], *mid, [1.0, _interp(x1, xs, ys)]]


def _interp(x: float, xs: list, ys: list) -> float:
    """np.interp for one point, without importing numpy into the model."""
    if x <= xs[0]:
        return float(ys[0])
    for i in range(1, len(xs)):
        xa, xb, ya, yb = xs[i - 1], xs[i], ys[i - 1], ys[i]
        if xa <= x <= xb:
            return float(ya if xb == xa else ya + (yb - ya) * (x - xa) / (xb - xa))
    return float(ys[-1])
