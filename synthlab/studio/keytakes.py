"""Keys takes: what ⏺ Rec in the keyboard dock keeps (static/keyrec.js,
`routes/takes.py`).

A take is a clip (`clips.py`) of the key bus alone — the beat left out
unless "+ beat" was lit — recorded in time with the loop, with what it is
in its meta:

    source  "keys"
    kept    True when Rec kept it with its loop (loops.py); False on a
            take an older version saved, until ★ Keep. Every take has it: a
            "keys" clip without it is an older dock recording
            (no notes), which is no take (`is_take`; the clip
            library files it with the jams)
    bpm     the tempo it was played at
    bars    its length in bars (whole bars when it was cut on the loop's
            bar lines; a free take — nothing was playing — may be a part)
    at      when it was taken (unix seconds): this session's takes first
    beat    True when the beat was recorded with the keys
    root, scale   the key it was played in
    meter   the loop's meter it was played over, when not 4/4
    sound   the id of the sound that played it (`save_sound`): the whole
            stack, as a format-2 preset holds it, in studio_data/takesounds/
            (by content, so takes of one sound share one file). A take is
            notes + that sound; its audio is the preview
    free    True on a free take (free record): it starts on its
            first note and stops where ■ was pressed — no count-in, no bar
            line, any length; `bars` is that length at `bpm`, the page's
            tempo. Free record's shaper (routes/freerec.py) makes its loop

and its notes in a sidecar, studio_data/takenotes/<clip>.json
(`clips.TAKE_NOTES`: `delete_clip` and `rename_clip` carry it, and writing
it never moves the clip store's listing the clip library and the hub
watch):

    notes   every note played, [[start, length, midi, velocity], ...]:
            start and length in beats from the take's first downbeat,
            velocity 0..1
    raw, fixes    after ✦ Clean up (`clean`): the notes as played, and what
            the clean-up changed; ↶ puts `raw` back
    arp     a free take's: the indices of `notes` the keys arp played

A take from before the sidecar kept these three in its meta: `read_notes`
reads either (the sidecar wins), every write goes to the sidecar, and ★
Keep — the meta's one rewrite — moves them out of the meta (`migrate`).

Pure functions on the clip store and the arrangement: no session. A meta
or a sidecar is a file anyone can edit (a bundle carries both:
`projectzip.py`), so everything read from one is checked again
(`clean_doc`, `notes_of`: the one validator); a post is held tighter
(`parse_notes`, which refuses).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import replace
from pathlib import Path

from .. import midifile as mf
from ..sequencer import midi, name_of
from . import clips, limits, loops, midi_bridge, schema, takeclean
from . import patternops as po
from .cliplib import clips_sig
from .clips import clip_dir, meta_number
from .files import dir_sig
from .lru import LRU
from .state.model import (
    NOTE_LANE,
    Arrangement,
    notes_window,
    preset_from_json,
)

SOURCE = "keys"
SESSION_START = time.time()  # this studio's start: "this session's" takes

MAX_BEATS = 4 * limits.MAX_TAKE_BARS
SOUNDS = "takesounds"  # studio_data/takesounds/<id>.json: a format-2 preset
NOTES = clips.TAKE_NOTES  # studio_data/takenotes/<clip>.json: the notes
NOTE_KEYS = ("notes", "raw", "fixes", "arp")  # what the sidecar holds
MAX_FIXES = 12  # what the clean-up changed, as kept
CLEAN_STEPS = 16  # ✦ Clean up's grid: sixteenths


class TakeRefused(ValueError):
    """A take post (or a write of one) that cannot be done: the toast."""


def is_take(clip: dict) -> bool:
    """A keys take: source "keys" and a `kept` saying whether it is kept
    (the module docstring: one without is the dock's older recording)."""
    return clip.get("source") == SOURCE and isinstance(clip.get("kept"), bool)


def is_kept(clip: dict) -> bool:
    return clip.get("kept") is True


# --- the notes -----------------------------------------------------------------


def _note(item) -> list | None:
    """One [start, length, midi, velocity], or None when it is not one."""
    if not isinstance(item, list | tuple) or len(item) != 4:
        return None
    start, length, midi, vel = item
    if not all(meta_number(v) for v in item):
        return None
    if not (0 <= start < MAX_BEATS and 0 < length <= MAX_BEATS):
        return None
    if midi != int(midi) or not 0 <= midi <= 127 or not 0 <= vel <= 1:
        return None
    return [
        round(float(start), 3),
        round(float(length), 3),
        int(midi),
        round(float(vel), 3),
    ]


def parse_notes(text: str) -> list[list]:
    """The notes a take posts (JSON), checked whole: a malformed one, or
    too many, refuses the take rather than keeping half of what was
    played. An empty list is a take with nothing played."""
    if len(text) > limits.MAX_TAKE_NOTES_CHARS:
        raise TakeRefused("That take holds too many notes.")
    try:
        raw = json.loads(text or "[]")
    except (ValueError, RecursionError) as err:
        raise TakeRefused("The take's notes aren't readable.") from err
    if not isinstance(raw, list) or len(raw) > limits.MAX_TAKE_NOTES:
        raise TakeRefused("That take holds too many notes.")
    out = [_note(n) for n in raw]
    if any(n is None for n in out):
        raise TakeRefused("The take's notes aren't readable.")
    return sorted(out)


def notes_of(doc: dict) -> list[list]:
    """The readable notes under `doc`'s "notes" (a sidecar, or an older
    take's meta)."""
    raw = doc.get("notes")
    if not isinstance(raw, list):
        return []
    return [n for n in map(_note, raw[: limits.MAX_TAKE_NOTES]) if n is not None]


def clean_doc(raw) -> dict:
    """A take's notes as the studio keeps them, from anything — a sidecar,
    an older take's meta, a bundle's entry: `{notes, raw?, fixes?}`, each
    held to its shape (`notes_of`; `fixes` strings, at most `MAX_FIXES`).
    The one reader every path goes through."""
    if not isinstance(raw, dict):
        return {"notes": []}
    out: dict = {"notes": notes_of(raw)}
    if isinstance(raw.get("raw"), list):
        out["raw"] = notes_of({"notes": raw["raw"]})
    if isinstance(raw.get("fixes"), list):
        out["fixes"] = [f[:300] for f in raw["fixes"][:MAX_FIXES] if isinstance(f, str)]
    if isinstance(raw.get("arp"), list):  # a free take's arp notes, by index
        n = len(out["notes"])
        arp = {i for i in raw["arp"][:n] if type(i) is int and 0 <= i < n}
        if arp:
            out["arp"] = sorted(arp)
    return out


def bars_of(clip: dict) -> float:
    bars = clip.get("bars")
    if meta_number(bars) and 0 < bars <= limits.MAX_TAKE_BARS:
        return float(bars)
    return 0.0


# --- the store -------------------------------------------------------------------


def read_meta(data_dir: Path, name: str) -> dict | None:
    """A take's meta (`clips.read_meta`), or None when `name` is not a
    keys take."""
    meta = clips.read_meta(data_dir, name)
    return meta if meta is not None and is_take(meta) else None


def read_notes(data_dir: Path, name: str, meta: dict | None = None) -> dict:
    """A take's notes (`clean_doc`): its sidecar, else — a take from
    before the sidecar — what its meta (`meta`, or read here) holds."""
    path = clips.sidecar(data_dir, NOTES, name)
    try:
        if path.stat().st_size <= limits.MAX_TAKE_NOTES_BYTES:
            return clean_doc(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, RecursionError):
        pass
    if meta is None:
        meta = clips.read_meta(data_dir, name) or {}
    return clean_doc(meta)


def write_notes(data_dir: Path, name: str, doc: dict) -> None:
    """A take's notes into its sidecar, held to `clean_doc`, compact."""
    path = clips.sidecar(data_dir, NOTES, name)
    path.parent.mkdir(exist_ok=True)
    text = json.dumps(clean_doc(doc), separators=(",", ":"))
    path.write_text(text, encoding="utf-8")


def migrate(data_dir: Path, name: str, meta: dict) -> dict:
    """`meta` without the notes a take from before the sidecar kept in it,
    which go to its sidecar first when it has none (the sidecar wins over
    them anyway). For a write of the meta: ★ Keep."""
    if not any(k in meta for k in NOTE_KEYS):
        return meta
    if not clips.sidecar(data_dir, NOTES, name).is_file():
        write_notes(data_dir, name, meta)
    return {k: v for k, v in meta.items() if k not in NOTE_KEYS}


# a meta file read once, by its (path, mtime, size): the take's meta, or
# None for a clip that is no take — so only a new or rewritten meta is
# read, and only a take's WAV header
_METAS: LRU[tuple] = LRU(4096)


def _take_meta(path: Path, stat: tuple) -> dict | None:
    key = f"{path}|{stat[0]}|{stat[1]}"
    hit = _METAS.get(key)
    if hit is None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            raw = None
        meta = clips.clean_meta(raw) if isinstance(raw, dict) else {}
        hit = (meta if is_take(meta) else None,)
        _METAS.put(key, hit)
    return hit[0]


# every keys take in a store, by its take files only — each take's meta
# and WAV, and the notes' sidecars: another clip's bounce, rename or
# delete reads nothing again
_TAKES: LRU[tuple] = LRU(8)


def takes(data_dir: Path) -> list[dict]:
    """Every keys take in the store, newest first: `{name, seconds, bpm,
    bars, notes (how many), kept, at, beat, sound, cleaned, fixes}`."""
    d = clip_dir(data_dir)
    stats = {n: (mtime, size) for n, mtime, size in clips_sig(data_dir)}
    found, metas = [], {}
    for n, stat in stats.items():
        if not n.endswith(".meta.json"):
            continue
        name = n[: -len(".meta.json")]
        wav = stats.get(f"{name}.wav")
        meta = _take_meta(d / n, stat) if wav else None
        if meta is not None:
            found.append((name, stat, wav))
            metas[name] = meta
    listing = (tuple(found), dir_sig(Path(data_dir) / NOTES, ".json"))

    def build() -> tuple:
        rows = [_row(data_dir, name, metas[name]) for name, *_ in found]
        return tuple(sorted(rows, key=lambda t: (-t["at"], t["name"])))

    return [dict(t) for t in _TAKES.by_sig(str(data_dir), listing, build)]


def _row(data_dir: Path, name: str, meta: dict) -> dict:
    """A take as the strip and the bin list it."""
    bpm, at = meta.get("bpm"), meta.get("at")
    doc = read_notes(data_dir, name, meta)
    return {
        "name": name,
        "seconds": clips.seconds(data_dir, name),
        "bpm": round(float(bpm)) if meta_number(bpm) and 0 < bpm < 1000 else 0,
        "bars": bars_of(meta),
        "notes": len(doc["notes"]),
        "kept": is_kept(meta),
        "at": float(at) if meta_number(at) else 0.0,
        "beat": meta.get("beat") is True,
        # free record's take: from its first note, no bar lines (the shaper)
        "free": meta.get("free") is True,
        "sound": isinstance(meta.get("sound"), str),
        # ✦ cleaned up: what it changed (↶ puts the notes back)
        "cleaned": "raw" in doc,
        "fixes": doc.get("fixes", []),
        # what the Loops list shows of a take no loop was made from yet
        # (looplib.py): its key, the sound's name, its notes as a sparkline
        "key": key_of(meta),
        "sound_name": sound_name(data_dir, meta.get("sound")),
        "roll": loops.roll_of_notes(doc.get("raw", doc["notes"]), bars_of(meta)),
    }


# (what the strip and the bin draw of the takes is looplib.py's:
# one Loops list, a take beside the loops — a take's loop in its place)


# --- a take onto the note grid ---------------------------------------------------


def grid_bars(arr: Arrangement, take_bars: float) -> int:
    """The grid's bars once a take of `take_bars` is written: as long as
    the take (whole bars, the loop's choices) unless it is longer already,
    and never past `MAX_GRID_STEPS`."""
    want = max(arr.bars, math.ceil(take_bars - 1e-6))
    fits = [
        b
        for b in limits.BAR_CHOICES
        if b * arr.steps_per_bar <= limits.MAX_GRID_STEPS and b <= want
    ]
    return max(fits) if fits else arr.bars


def to_grid(
    arr: Arrangement,
    take: str,
    notes: list[list],
    take_bars: float,
    span: int,
    octave: int,
) -> tuple[Arrangement, int, int, str]:
    """The take's notes onto the edited lane's note grid: (the arrangement
    after, the grid's span and octave window to show them in, a note
    saying what happened). Quantised to the grid's step
    (`midi_bridge.to_grid`, the MIDI import's write), replacing what the
    grid shows over the take's length and keeping the rest — the steps
    past it, the rows it does not show; the grid grows to the take's bars
    (`grid_bars`). The take's bpm is the caller's to adopt. Nothing to
    place raises `TakeRefused`."""
    if not notes:
        raise TakeRefused("That take has no notes: play the keys while it records.")
    bars = grid_bars(arr, take_bars or max(n[0] + n[1] for n in notes) / 4)
    arr2 = replace(arr, bars=bars)
    span, octave, rows = notes_window(arr2, [n[2] for n in notes], span, octave)
    track = mf.Track(
        name=take,
        notes=[
            mf.Note(s, ln, m, max(1, min(127, round(v * 127)))) for s, ln, m, v in notes
        ],
    )
    lane = {k: getattr(arr2, k) for k in NOTE_LANE}
    try:
        out, said = midi_bridge.to_grid(track, lane, rows)
    except midi_bridge.ImportRefused as err:
        raise TakeRefused(
            "No note of that take fits the grid: try another key, or more bars."
        ) from err
    total = bars * arr2.steps_per_bar
    edge = (
        min(total, math.ceil(round(take_bars * arr2.steps_per_bar, 6)))
        if take_bars
        else total
    )
    if edge < total:  # past the take's length the grid keeps what it showed
        shown = set(rows)

        def past(c) -> bool:
            return c[0] in shown and edge <= int(c[1]) < total

        for k in ("cells", "cuts"):
            out[k] = po._join(
                [], [c for c in out[k] if not past(c)] + [c for c in lane[k] if past(c)]
            )
        out["velocities"] = {
            **{
                k: v
                for k, v in out["velocities"].items()
                if not edge <= po._step(k) < total
            },
            **{
                k: v
                for k, v in lane["velocities"].items()
                if edge <= po._step(k) < total
            },
        }
        out["slides"] = sorted(
            {int(s) for s in out["slides"]}
            | {int(s) for s in lane["slides"] if edge <= int(s) < total}
        )
    said = said.replace("Imported", "Wrote", 1).rstrip(".")
    if bars > arr.bars:
        said += f" · the grid is {bars} bars now"
    return replace(arr2, **out), span, octave, said + "."


# --- the sound that played it ------------------------------------------------------


def _sound_path(data_dir: Path, sid: str) -> Path | None:
    if not (len(sid) == 16 and all(c in "0123456789abcdef" for c in sid)):
        return None
    return Path(data_dir) / SOUNDS / f"{sid}.json"


def save_sound(data_dir: Path, payload: dict) -> str:
    """Keep a sound (`StudioState.sound_payload`: a format-2 preset) by its
    content; its id. A take of the same sound as the last shares its file."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    sid = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    path = _sound_path(data_dir, sid)
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sid


def sound_payload(data_dir: Path, sid) -> dict | None:
    """A kept sound's file as written (a format-2 preset, unchecked: a
    loop's schema conforms it), or None."""
    path = _sound_path(data_dir, sid) if isinstance(sid, str) else None
    if path is None or not path.is_file():
        return None
    if path.stat().st_size > limits.MAX_LOOP_BYTES:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return raw if isinstance(raw, dict) and raw.get("format") == 2 else None


_SOUND_NAMES: LRU[tuple] = LRU(256)  # a kept sound's name, by its id (content)


def sound_name(data_dir: Path, sid) -> str:
    """The name of the patch a kept sound plays ("" when there is none):
    read once per id — a kept sound's file is its content."""
    if not isinstance(sid, str):
        return ""
    key = f"{data_dir}|{sid}"
    hit = _SOUND_NAMES.get(key)
    if hit is None:
        sound = load_sound(data_dir, sid)
        hit = (sound[0].name if sound is not None else "",)
        _SOUND_NAMES.put(key, hit)
    return hit[0]


def load_sound(data_dir: Path, sid) -> tuple | None:
    """(patch, format-2 extras) of a kept sound, through the preset's own
    schema (`preset_from_json`); None when it is not there or not a sound."""
    raw = sound_payload(data_dir, sid)
    return preset_from_json(raw) if raw is not None else None


def key_of(meta: dict) -> tuple[str, str] | None:
    """(root, scale) a take was played in, when its meta says so readably."""
    root, scale = meta.get("root"), meta.get("scale")
    if not (isinstance(root, str) and isinstance(scale, str)):
        return None
    if scale not in takeclean.SCALES or not schema.valid_pitch(root):
        return None
    return root, scale


# --- ✦ Clean up -----------------------------------------------------------------------


def clean(notes: list[list], bpm: float, bars: float, root: str, scale: str):
    """The Song take's clean-up (`takeclean.clean`: the lean, the grid,
    doubles, velocities, the key) on a keys take's notes: (the notes after,
    the `takeclean.Cleaned`). The notes go in as the raw events a Song take
    keeps (seconds from the downbeat); what comes out starts on the 1/16
    grid, each note held for its own length snapped to the grid (at least
    a step). The algorithm is takeclean's, unforked."""
    whole = max(1, math.ceil(bars - 1e-6)) if bars else 1
    beat = 60.0 / bpm
    raw = [{"midi": m, "t": s * beat, "vel": v} for s, _ln, m, v in notes]
    take = takeclean.Take(
        kind="notes",
        raw=raw,
        bpm=bpm,
        bars=whole,
        steps=CLEAN_STEPS,
        grid="1/16",
        base={"bars": whole, "cells": [], "cuts": [], "velocities": {}},
        root=root,
        scale_name=scale,
    )
    cleaned = takeclean.clean(take)
    return _as_notes(cleaned.played, notes, cleaned.grid, root, scale, whole), cleaned


def _as_notes(played, notes, g: int, root: str, scale: str, bars: int) -> list[list]:
    """takeclean's (midi, step, velocity) back to notes: each takes the
    length of the played note it came from (the nearest of its pitch,
    before the key moved it), snapped to the grid."""
    keys = takeclean._in_key(root, scale)
    per_beat = CLEAN_STEPS / 4
    end = bars * 4.0
    out = []
    for m, step, v in sorted(played, key=lambda p: (p[1], p[0])):
        start = step / per_beat
        near = [n for n in notes if takeclean.to_key(n[2], keys) == m or n[2] == m]
        length = min(near, key=lambda n: abs(n[0] - start))[1] if near else 0.0
        steps = max(g, round(length * per_beat / g) * g)
        length = min(steps / per_beat, end - start)
        if length > 0:
            out.append(
                [round(start, 3), round(length, 3), int(m), round(min(1.0, v), 3)]
            )
    return out


def edited_notes(notes: list[list], pattern: dict, g: int) -> list[list]:
    """The cleaned notes after Gemini's edits (`takeclean.apply_edits` on
    the cleaned pattern): a note whose start cell it removed goes, a cell
    it added is a note a grid step long, a velocity it set is the note's."""
    per_beat = CLEAN_STEPS / 4
    cells = {(c[0], int(c[1])) for c in pattern.get("cells") or []}
    vel = pattern.get("velocities") or {}
    starts = {(name_of(n[2]), round(n[0] * per_beat)): n for n in notes}
    out = [
        [n[0], n[1], n[2], round(float(vel.get(str(step), n[3])), 3)]
        for (pitch, step), n in starts.items()
        if (pitch, step) in cells
    ]
    for pitch, step in sorted(cells - set(starts)):
        v = round(float(vel.get(str(step), 0.8)), 3)
        out.append([round(step / per_beat, 3), round(g / per_beat, 3), midi(pitch), v])
    return sorted(out)
