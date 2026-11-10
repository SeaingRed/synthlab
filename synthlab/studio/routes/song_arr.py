"""The playlist's tools: a range of cells, a section
cut / unrolled / duplicated with patterns of its own, bars inserted and
deleted at a bar of the timeline, the energy lane and "arrange by energy",
and a transition on a section boundary.

The edits themselves are `studio/songops.py` (pure, on the song dict). A
route here is what every Song route is (`routes/song.py`): one `_commit` —
so ONE undo step, named — the regions by their watches, a refusal a 422
toast and nothing changed, the length caps checked on what the edit made.
What a route adds is what it says afterwards:

- **what was not exact**: a cut names what will not sound as it did (a
  note held across it, an effect that restarts: `songops.cut`'s notes);
- **what did not move**: an insert or a delete moves the arrangement under
  a whole stem, which stays at its bar — the toast names the stems;
- **what it made plays**: a transition is auditioned from two bars before
  its boundary (`song-audition`, songline.js), a generator on a range
  loops the first section it wrote (`song-autoplay`, song.js);
- **where the range is now**: a copy, a move or a duplicate answers with
  `song-range`, the cells it landed on, and the page's selection follows
  (songsel.js) — so Ctrl+D pressed again carries on to the right.

Hostile input: an unknown `op`, kind or direction, a bar that is not a
whole number or not on the song, a range past `MAX_RANGE_CELLS` (refused
whole, never truncated) or naming a cell the song does not have — each a
422 toast. A seed is taken modulo `SEED_MODULO`.
"""

from __future__ import annotations

import math
import re

from fastapi import Request, Response

from .. import limits
from .. import song_model as sm
from .. import songops as so
from ..clips import delete_clip
from ..gen import notes as nt
from ..routing import StrForm, Svc, studio_router
from ..service import songpage
from .song import _commit, _section
from .song_record import _transition_clip, _transition_spec
from .song_start import FOREVER, _gen_params, _seed

router = studio_router()

CELL_OPS = (
    "clear",
    "copy",
    "move",
    "duplicate",
    "unique",
    "variation",
    "again",
    "fill",
)
INSERT_BARS = 4  # what "Insert bars" adds when no number is posted


def _bar(form: dict, key: str, what: str, default: int | None = None) -> int:
    """A 1-based bar (or a count of bars) from a form: a whole number of
    at most six digits, else a SongError — never a ValueError, never a
    400-digit int."""
    raw = str(form.get(key, "")).strip()
    if not raw and default is not None:
        return default
    if not (raw.isascii() and raw.lstrip("-").isdigit() and len(raw) <= 7):
        raise sm.SongError(f"{what} is a whole number.")
    return int(raw)


def _flag(form: dict, key: str) -> bool:
    return str(form.get(key, "")).strip().lower() in ("1", "on", "true", "yes")


def _takes(svc, song: dict) -> dict[str, float]:
    return songpage.take_bars(svc.state.data_dir, song)


_FX_RESTART = re.compile(r"^(.+)'s .+ restarts at the cut$")


def _not_exact(notes: list[str]) -> str:
    """What a cut's toast adds: the further cut an 8-bar pattern forced
    (`songops.cut`: "also cut at bar N (...)"), then what will not sound
    exactly as it did — the effects that start again folded into one note
    naming the tracks (a track a note made the toast four lines of engine
    notes and buried the extra cut)."""
    also = [n for n in notes if n.startswith("also cut")]
    rest = [n for n in notes if not n.startswith("also cut")]
    said = "".join(f" {n[0].upper()}{n[1:]}." for n in also)
    fx = [m.group(1) for n in rest if (m := _FX_RESTART.match(n))]
    if len(fx) > 1:
        rest = [n for n in rest if not _FX_RESTART.match(n)]
        names = ", ".join(fx[:-1]) + f" and {fx[-1]}"
        rest.insert(0, f"the effects on {names} start again at the cut")
    return said + (f" Not exactly as before: {'; '.join(rest)}." if rest else "")


def _stems_stay(song: dict, bar: int, bars: int) -> str:
    """What an edit that moved the timeline from `bar` on says about the
    whole stems it moved under."""
    names = so.long_under(song, bar)
    if not names:
        return ""
    stems = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
    return (
        f" {stems} stay{'s' if len(names) == 1 else ''} at "
        f"{'its' if len(names) == 1 else 'their'} bar: {bars} bar"
        f"{'' if bars == 1 else 's'} of the arrangement moved under."
    )


def _toast(said: dict):
    def then():
        out: dict = {"studio-toast": {"message": said.get("say", "")}}
        if said.get("kind"):
            out["studio-toast"]["kind"] = said["kind"]
        return out | said.get("more", {})

    return then


# --- a range of cells ---------------------------------------------------------------


@router.post("/song/cells")
def song_cells(request: Request, form: StrForm, svc: Svc) -> Response:
    """One edit of a range of cells: `cells` = "section:track,…" (at most
    `MAX_RANGE_CELLS`; more is refused whole) and `op` —

    - `clear`: every cell emptied (a block with its lanes and span, an
      audio cell's takes);
    - `copy` / `move`: the range put down with its top-left cell on
      (`to_section`, `to_track`) — by reference, a block's span with it; a
      move along a track carries lanes, a copy never does; sections are
      added past the end as needed;
    - `duplicate`: the range again, straight after itself;
    - `unique`: patterns of its own (one copy a pattern, so the range
      still shares within itself); `variation`: the same, each varied
      (`seed`);
    - `again`: its generated patterns rolled again (`seed`); `fill`: a
      generator (`kind`, its settings) written into it.

    One undo step; rolling `again` (or the same `fill`) is the same step,
    and what a generator wrote plays."""
    op = str(form.get("op", ""))
    said: dict = {}

    def change(song):
        if op not in CELL_OPS:
            raise sm.SongError("No such range edit.")
        cells = so.parse_cells(song, form.get("cells", ""))
        n = len(cells)
        if op == "clear":
            said["say"] = f"Cleared {so.clear_cells(song, cells)} of {n} cells."
        elif op in ("copy", "move"):
            landed: list = []
            wrote, added = so.paste_cells(
                song,
                cells,
                str(form.get("to_section", "")),
                str(form.get("to_track", "")),
                move=op == "move",
                landed=landed,
            )
            verb = "Moved" if op == "move" else "Copied"
            said["say"] = f"{verb} {wrote} cells" + _added(added)
            said["more"] = _range(landed)
        elif op == "duplicate":
            landed = []
            wrote, added = so.duplicate_cells(song, cells, landed)
            said["say"] = f"Duplicated {wrote} cells to the right" + _added(added)
            said["more"] = _range(landed)
        elif op in ("unique", "variation"):
            seed = _seed(form) if op == "variation" else None
            made = so.unique_cells(song, cells, seed)
            what = "varied" if op == "variation" else "made unique"
            said["say"] = f"{made} pattern{'' if made == 1 else 's'} {what}."
        elif op == "again":
            seed = _seed(form)
            made = so.again_cells(song, cells, seed)
            said["say"] = f"Rolled {made} pattern{'' if made == 1 else 's'} again."
            said["more"] = {"song-autoplay": {"section": cells[0][0]}}
        else:
            kind = str(form.get("kind", ""))
            if kind not in nt.GENERATORS and kind not in ("fill", "build"):
                raise sm.SongError("No such generator.")
            so.fill_cells(song, cells, kind, _seed(form), _gen_params(form, kind))
            said["say"] = f"Wrote {kind.replace('_', ' ')} into {n} cells."
            said["more"] = {"song-autoplay": {"section": cells[0][0]}}

    rolls = op in ("again", "fill")
    return _commit(
        svc,
        change,
        label=f"range: {op}"[:40],
        key=f"cells:{op}:{form.get('kind', '')}:{form.get('cells', '')[:400]}"
        if rolls
        else None,
        window=FOREVER if rolls else None,
        then=_toast(said),
    )


def _range(landed: list) -> dict:
    """`song-range`: where a pasted, moved or duplicated range is now —
    the page's selection goes with it (songsel.js), so Ctrl+D again
    carries on to the right."""
    return {"song-range": {"cells": ",".join(f"{s}:{t}" for s, t in landed)}}


def _added(n: int) -> str:
    return f" ({n} section{'' if n == 1 else 's'} added)." if n else "."


# --- one section --------------------------------------------------------------------


@router.post("/song/section/{section_id}/op")
def section_op(request: Request, section_id: str, form: StrForm, svc: Svc):
    """A structural edit of one section, `op` —

    - `split`: cut it at `bar` (the 1-based bar of the TIMELINE that
      becomes the new section's first; it must lie inside the section);
      `unique=1`: the right half plays copies of the patterns it would
      share. A take across the bar, or a cut that leaves 5-7 bars of an
      8-bar pattern, refuses it by name; the toast says what will not be
      exactly as it was;
    - `unroll`: a section that plays N times becomes N sections;
    - `insert`: `bars` (default 4) empty bars `where` = "before" or
      "after" (the default) it;
    - `duplicate`: a copy after it, `unique=1` with patterns of its own
      ("duplicate as a variation"; without it, `/duplicate`'s own)."""
    op = str(form.get("op", ""))
    said: dict = {}

    def change(song):
        sec = _section(song, section_id)
        name = sec["name"]
        if op == "split":
            bar = _bar(form, "bar", "The bar")
            _new, notes = so.split(
                song,
                section_id,
                bar - 1,
                unique=_flag(form, "unique"),
                take_bars=_takes(svc, song),
            )
            said["say"] = f"Split {name} at bar {bar}." + _not_exact(notes)
        elif op == "unroll":
            ids = so.unroll(song, section_id)
            said["say"] = f"{name} is {len(ids)} sections now."
        elif op == "insert":
            n = _bar(form, "bars", "The number of bars", INSERT_BARS)
            where = str(form.get("where", "") or "after")
            if where not in ("before", "after"):
                raise sm.SongError("Before or after the section.")
            i = sm.section_index(song, section_id)
            at = so.starts(song)[i + (where == "after")]
            stays = _stems_stay(song, at, min(limits.MAX_SECTION_BARS, max(1, n)))
            so.insert_bars(song, at, n, take_bars=_takes(svc, song))
            said["say"] = f"{max(1, n)} empty bars {where} {name}." + stays
        elif op == "duplicate":
            so.duplicate(song, section_id, unique=_flag(form, "unique"))
            said["say"] = (
                f"{name} again, with patterns of its own."
                if _flag(form, "unique")
                else f"{name} again: every block is shared with it."
            )
        else:
            raise sm.SongError("No such section edit.")

    return _commit(svc, change, label=f"{op} section"[:40], then=_toast(said))


# --- the timeline, by its bars ------------------------------------------------------


@router.post("/song/bars")
def song_bars(request: Request, form: StrForm, svc: Svc) -> Response:
    """An edit at a bar of the timeline (the ruler's marker; every bar of
    every repeat, 1-based), `op` —

    - `cut`: bar `bar` becomes a section's first (`unique=1`: the right
      half's patterns are copies). A bar that already is one: nothing;
    - `insert`: `bars` (default 4) empty bars before bar `bar` — a new
      empty section, the section the bar falls inside cut first;
    - `delete`: bars `bar` to `to` (inclusive) taken out, the sections at
      either end cut first.

    Whole stems keep their bar: the toast names the ones the arrangement
    moved under."""
    op = str(form.get("op", ""))
    said: dict = {}

    def change(song):
        bar = _bar(form, "bar", "The bar")
        takes = _takes(svc, song)
        if op == "cut":
            before = len(song["sections"])
            _i, notes = so.cut(
                song, bar - 1, unique=_flag(form, "unique"), take_bars=takes
            )
            if len(song["sections"]) == before:
                raise sm.SongError(f"Bar {bar} starts a section already.")
            said["say"] = f"Cut at bar {bar}." + _not_exact(notes)
            said["kind"] = "done"
        elif op == "insert":
            n = _bar(form, "bars", "The number of bars", INSERT_BARS)
            n = min(limits.MAX_SECTION_BARS, max(1, n))
            stays = _stems_stay(song, bar - 1, n)
            _sid, notes = so.insert_bars(song, bar - 1, n, take_bars=takes)
            said["say"] = (
                f"{n} empty bar{'' if n == 1 else 's'} at bar {bar}."
                + stays
                + _not_exact(notes)
            )
        elif op == "delete":
            to = _bar(form, "to", "The last bar")
            stays = _stems_stay(song, bar - 1, max(0, to - bar + 1))
            gone, notes = so.delete_bars(song, bar - 1, to, take_bars=takes)
            said["say"] = (
                f"Deleted bars {bar}–{to} ({gone} bars)." + stays + _not_exact(notes)
            )
        else:
            raise sm.SongError("No such edit of the timeline.")

    return _commit(svc, change, label=f"{op} bars"[:40], then=_toast(said))


# --- the energy lane ----------------------------------------------------------------


@router.post("/song/energy")
def song_energy(request: Request, form: StrForm, svc: Svc) -> Response:
    """The energy lane: `section_id` + `energy` (0..1; empty takes it
    away) sets how full one section is meant to be — it renders nothing by
    itself; it is what a boundary's transition defaults from, what the
    checklist reads, and what `op=arrange` plays: every section with an
    energy (or `section_id` alone) made as full as it is drawn, by
    muting, thinning and filtering the fullest section's parts
    (`songops.arrange_by_energy`: the loop -> song machinery). A dragged
    bar is one undo step; arranging again is the same step."""
    sid = str(form.get("section_id", ""))
    said: dict = {}
    if str(form.get("op", "")) == "arrange":

        def arrange(song):
            names = so.arrange_by_energy(song, _seed(form), sid or None)
            said["say"] = (
                f"Arranged {', '.join(names[:4])}{'…' if len(names) > 4 else ''} "
                "by energy: thinned from the fullest section."
            )

        return _commit(
            svc,
            arrange,
            label="arrange by energy",
            key=f"energy:arrange:{sid}",
            window=FOREVER,
            then=_toast(said),
        )

    def change(song):
        raw = str(form.get("energy", "")).strip()
        if not raw:
            so.set_energy(song, sid, None)
            return
        try:
            v = float(raw)
        except ValueError:
            v = math.nan
        if not math.isfinite(v):
            raise sm.SongError("The energy is a number from 0 to 1.")
        so.set_energy(song, sid, v)

    return _commit(svc, change, label="section energy", key=f"energy:{sid}")


# --- a transition on a boundary -----------------------------------------------------


@router.post("/song/boundary")
def song_boundary(request: Request, form: StrForm, svc: Svc) -> Response:
    """A transition on the boundary BEFORE section `section_id` (the `+`
    on it): `kind` (+ `direction`) places that one; none posted places
    what the two sections' energy suggests (`songops.boundary_moves`:
    rising — a riser into it and an impact on its first bar; falling — a
    downlifter off it; level — an impact). What leads in ends on the last
    bar line of the section before, what comes off the bar line starts
    this one — the rule and the clips of `/song/section/{id}/transition`
    (`_transition_spec`, `_transition_clip`), as ONE undo step however
    many clips, and auditioned: the page plays from two bars before the
    boundary."""
    s = svc.state
    index = sm.section_index(s.song, form.get("section_id", ""))
    if index is None:
        return svc.toast("No such section.")
    try:
        moves = so.boundary_moves(s.song, index)
        kind = str(form.get("kind", ""))
        if kind:
            direction = str(form.get("direction", ""))
            picked = [
                m
                for m in moves["all"]
                if m["kind"] == kind and direction in ("", m["direction"])
            ][:1]
            if not picked:
                raise sm.SongError("No such transition.")
        else:
            picked = moves["default"]
        bpm = float(s.song["bpm"])
        specs = []
        for m in picked:
            sec = _section(s.song, m["section_id"])
            form_ = {"kind": m["kind"], "direction": m["direction"]}
            form_["bars"] = str(form.get("bars") or m["bars"])
            specs.append((m, _transition_spec(form_, sec, bpm)))
    except sm.SongError as err:
        return svc.toast(str(err))
    audio = [t["id"] for t in s.song["tracks"] if t["kind"] == "audio"]
    if not audio and len(s.song["tracks"]) >= limits.MAX_TRACKS:
        return svc.toast(f"A song holds at most {limits.MAX_TRACKS} tracks.")
    wrote, names = [], []
    for _m, spec in specs:
        name, new = _transition_clip(s.data_dir, spec)
        names.append(name)
        if new:
            wrote.append(name)

    def change(song):
        track = audio[0] if audio else sm.add_track(song, "audio", "FX")
        for (m, spec), name in zip(specs, names, strict=True):
            sec = _section(song, m["section_id"])
            if len(sec["audio"]) >= limits.MAX_LANE_CLIPS:
                raise sm.SongError(
                    f"A section holds at most {limits.MAX_LANE_CLIPS} takes."
                )
            bar = max(0, sec["bars"] - spec["bars"]) if m["leads"] else 0
            sm.place_take(song, sec["id"], track, name, bar)

    def then():
        at = so.starts(svc.state.song)[index]
        said = " + ".join(m["label"].split(" ")[0] for m, _s in specs)
        return {
            "studio-toast": {"message": f"Added {said} ({moves['trend']} energy)."},
            "song-audition": {"bar": max(1, at - 1)},
        }

    resp = _commit(svc, change, label="add transition", then=then)
    if resp.status_code >= 400:
        for name in wrote:
            delete_clip(s.data_dir, name)  # nothing refers to them
    return resp
