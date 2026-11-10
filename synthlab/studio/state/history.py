"""Undo: the patch ring, the step grids' ring and the song's ring.

The patch ring (`_history`, coalesced by `_history_at`) belongs to the
edited slot, and holds the session FX racks' edits too (`push_fx_undo`: a
step that is one scope's chain, not the patch): `Layers.select_layer` and
`SongBridge.edit_track` swap it out whole and `Persistence._apply` empties
it; only this module pushes to it or pops from it. The pattern ring
(`_pattern_undo`) is session-wide, each step tagged with the slot it was
taken on; a section opened in the loop is one step that holds the whole
loop (`SongBridge.restore_whole_loop`). Takes have their own undo
(`take_history`), which the song routes push and pop.

The song ring (`_song_undo` / `_song_redo`, studio `docs/song.md`, "Undo
and redo") holds whole songs: a step is the song as it was before one
edit. It is this module's alone. Nothing else has to tell it when the song
is replaced (a project import, a revert or reset of the "song" scope, the
v2 migration's note): it remembers which song object its steps lead to
(`_song_undo_head`) and empties itself when the session's song is another.

A mixin: an undo step is a snapshot of `patch` / `arrangement`, and a pop
assigns them.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import fields, replace
from pathlib import Path

from ...sequencer import midi as midi_of
from ...synth import Patch
from .. import song_model as sm
from .model import (
    DRUM_LANE,
    DRUM_LOOP_FIELDS,
    LOOP_FIELDS,
    NOTE_LANE,
    Arrangement,
    _map_song,
    _patch_from_json,
    _patch_to_json,
    loop_window,
    notes_window,
    song_clip_names,
)

# what a track's sound is (`SongBridge.mirror_patch_to_track` writes exactly
# these while it is edited): a synth track's patch, curves and FX; the drums
# track's kit and its bus FX. A track holds its own kind's keys only.
_TRACK_SOUND = ("patch", "curves", "fx", "drum_params", "drum_mix", "drum_samples")


# what marks a step of the patch ring as an FX rack's (`push_fx_undo`): the
# scope whose chain it holds. A patch's JSON has no such key.
_FX_STEP = "fx_scope"

# what a patch step also keeps (`patch_snapshot`): the "sound" baseline as it
# was when the step was taken. A load moves the baseline (`load_sound`), so
# Ctrl+Z back across one — a resample, a picked sound — landed on the saved
# sound with "edited" still lit against the sound it had undone.
_SOUND_BASE = "sound_base"

# what a patch step also keeps when a form post took it (`push_history`'s
# `after`): the fields that post moved. Only a post moving the same fields
# joins it — a slider's drag — so a discrete edit landing inside the window
# (an unplug 0.4 s after a patch-bay drag) is a step of its own, not folded
# into the drag's step and left with nothing to undo.
_MOVED = "moved"


def _sound_of(track: dict) -> dict:
    return {k: track[k] for k in _TRACK_SOUND if k in track}


def monotonic() -> float:
    """The undo ring's clock, read off the package on every call: it has
    always been patchable as `synthlab.studio.state.monotonic`
    (tests/studio/test_reset.py), and stays so."""
    from . import monotonic as clock

    return clock()


class History:
    """The patch undo ring and the pattern undo ring."""

    # the session's fields this unit reads or writes (all set in
    # StudioState.__init__; test_state_split.py holds this list to the code)
    patch: Patch
    arrangement: Arrangement
    _history: list[dict]
    _history_at: float
    _pattern_undo: list[dict]
    _song_undo: list[dict]
    _song_redo: list[dict]
    _song_undo_at: float
    _song_undo_head: dict | None
    _song_undo_sound: dict | None
    active_layer: int
    baselines: dict
    beat_var: str
    song: dict
    song_edit_track: str | None
    loop_edit: dict[int, dict]
    data_dir: Path
    grid_span: int
    grid_oct: int
    layers: list[dict]
    perform_slots: list[dict] | None

    def stash_for_load(self) -> None:
        """Before a load replaces the patch: the outgoing one goes to
        "before last load" *and* is its own undo step (never coalesced into
        a slider drag that happened a moment earlier)."""
        self.stash_prev()
        self._history_at = -1e9
        self.push_history()

    UNDO_CAP = 32
    UNDO_COALESCE_S = 1.0

    def push_history(self, after: Patch | None = None) -> None:
        """Snapshot the patch before a mutation. Pushes landing within
        UNDO_COALESCE_S of the last keep only the first snapshot — a slider
        drag posts every 150ms and must undo as ONE step. (Never into an FX
        step: a patch tweak just after a knob in a rack is a step of its
        own.) `after`, the patch a form post is about to set: only a push
        moving the same fields joins (`_MOVED`), and a post moving none
        is no step at all (Ctrl+Z would have undone nothing)."""
        moved = None
        if after is not None:
            moved = [
                f.name
                for f in fields(Patch)
                if getattr(after, f.name) != getattr(self.patch, f.name)
            ]
            if not moved:
                return
        now = monotonic()
        last = self._history[-1] if self._history else {}
        joins = last and _FX_STEP not in last and last.get(_MOVED) == moved
        if joins and now - self._history_at < self.UNDO_COALESCE_S:
            self._history_at = now
            return
        step = self.patch_snapshot()
        if moved is not None:
            step[_MOVED] = moved
        self._history.append(step)
        del self._history[: -self.UNDO_CAP]
        self._history_at = now

    def top_undo_step(self) -> dict | None:
        """The patch ring's newest step (a whole-loop pattern step keeps the
        one its load pushed: `restore_whole_loop` drops it again)."""
        return self._history[-1] if self._history else None

    def patch_snapshot(self) -> dict:
        """The live patch as the undo ring stores it (see `push_undo_step`),
        with the "sound" baseline of the moment (`_SOUND_BASE`)."""
        return _patch_to_json(self.patch) | {_SOUND_BASE: self.baselines.get("sound")}

    def push_undo_step(self, before: dict) -> None:
        """`before` (a `patch_snapshot`) as ONE undo step of its own — never
        coalesced into a drag a moment earlier — when the patch has moved
        off it since. For the whole-scope actions (the reset menu's revert
        and defaults), which replace the patch without going through
        `push_history`: Ctrl+Z after them used to skip the pre-reset sound."""
        if _patch_to_json(self.patch) == {
            k: v for k, v in before.items() if k != _SOUND_BASE
        }:
            return
        self._history.append(before)
        del self._history[: -self.UNDO_CAP]
        self._history_at = -1e9  # the next edit is a fresh step

    # what a pattern undo step holds: both grids' cells, lanes and shapes
    PATTERN_FIELDS = (
        "cells",
        "velocities",
        "slides",
        "cuts",
        "note_chance",  # the note grid's chance lane
        "bars",
        "steps_per_bar",
        "drums",
        "drum_vel",
        "drum_bars",
        "drum_steps",
        "drum_hits",
        "drum_row_swing",
        # the bar: a meter change is one step (`set_meter`), and undoing it
        # puts every grid back in the meter its steps are counted in
        "meter",
        # the loop's chord track (Perform's chord lane): its edits are steps too
        "chords",
        # the note grid's timing inside a step (free record's loops): it
        # goes back with the notes it sits on
        "note_nudge",
    )

    def pattern_snapshot(self) -> dict:
        """The step grids as one pattern undo step (`push_pattern_undo`),
        tagged with the layer whose cells `arrangement.cells` holds."""
        a = self.arrangement
        snap = {k: copy.deepcopy(getattr(a, k)) for k in self.PATTERN_FIELDS}
        # (and with the beat variation the drum grid held: `variation_undone`)
        return {"layer": self.active_layer, "arr": snap, "var": self.beat_var}

    def push_pattern_undo(self, before: dict | None = None) -> None:
        """`before` (a `pattern_snapshot`, default: now) as one pattern undo
        step, unless the grids already equal the newest step."""
        before = before or self.pattern_snapshot()
        if self._pattern_undo and self._pattern_undo[-1] == before:
            return
        self._pattern_undo.append(before)
        del self._pattern_undo[: -self.UNDO_CAP]

    def pop_pattern_undo(self) -> str | None:
        """Put the grids back as the newest step had them. Returns why it
        could not (nothing to undo; the step belongs to another layer —
        its cells are that slot's, and the slot on screen is not it)."""
        while self._pattern_undo:
            step = self._pattern_undo.pop()
            for lane, e in list(self.loop_edit.items()):
                if step is e["mark"]:
                    # back past the step that opened a saved loop: the lane
                    # is the working loop's again, and the loop keeps what
                    # it had
                    del self.loop_edit[lane]
            if "slots" in step:
                # a slot of Perform's launcher filled, moved or cleared: the
                # launcher as it was (what plays is not touched)
                self.perform_slots = step["slots"]
                return None
            whole = step.get("whole")  # a section opened, a lane's sound
            if whole is None and step["arr"] == self.pattern_snapshot()["arr"]:
                continue  # nothing to see: undo the one before it
            if whole is not None:
                # the loop as it was before a section or a lane's sound
                # replaced it whole — from the lane it was taken on, which
                # it holds (a lane head's new lane moved the edit away)
                if step["layer"] != self.active_layer:
                    self.select_layer(step["layer"])
                self.restore_whole_loop(whole)
                return None
            if step["layer"] != self.active_layer:
                self._pattern_undo.clear()
                return "That edit was on another layer — nothing to undo here."
            self.variation_undone(step.get("var"))
            was = self.arrangement.meter
            back = step["arr"].get("meter", was)
            if back != was:  # the lanes the step does not hold follow
                self.fit_other_lanes(was, back)
            self.arrangement = replace(self.arrangement, **step["arr"])
            return None
        return "Nothing to undo in the pattern."

    def can_undo_pattern(self) -> bool:
        return bool(self._pattern_undo)

    def newest_pattern_step(self) -> dict | None:
        """The step Ctrl+Z would undo next (the loop coach's ↶ Reverse asks
        whether it is still the one its Accept pushed)."""
        return self._pattern_undo[-1] if self._pattern_undo else None

    def show_notes(self) -> None:
        """The piano roll's window (`grid_span`, `grid_oct`) moved to where
        the note grid's notes are — after anything that puts notes on the
        grid from elsewhere (a loop, a take, a section, a generated part),
        so none lands out of sight ("48 hidden notes"). The notes are never
        moved: the window is (`notes_window`). An empty grid keeps it."""
        midis = sorted({midi_of(p) for p, _st in self.arrangement.cells})
        if midis:
            self.grid_span, self.grid_oct, _rows = notes_window(
                self.arrangement, midis, self.grid_span, self.grid_oct
            )

    # -- a saved loop on the grid (loops.py, routes/takes.py) ---------------------
    def loop_fields(self) -> dict:
        """What a saved loop keeps of the working arrangement (`LOOP_FIELDS`):
        the edited lane's notes, the tempo, the key, the meter."""
        a = self.arrangement
        return {k: copy.deepcopy(getattr(a, k)) for k in LOOP_FIELDS}

    # Saved loops open on the grids, ONE A LANE (`loop_edit`):
    # {lane: {name, layer, mark, base, row,
    # bars}} — `layer` the lane (a layer slot 0-3, or `DRUM_LANE`), `mark`
    # the pattern undo step that opened it, `base` what the lane held when
    # the file last did, `row` the launcher's row it was launched from
    # (None: Edit on a Loops row), `bars` the loop's own length (a slot
    # shorter than the grid repeats across it). Each lane is mirrored on its
    # own and only when it moved: a request writes at most the files of the
    # lanes it changed.
    def lane_state(self, lane: int) -> dict:
        """What a saved loop open on `lane` holds of the session now: a
        note lane's `NOTE_LANE` fields — the edited slot's from the
        arrangement, another slot's from its stash (its cells and `seq`) —
        or the drum lane's (`DRUM_LOOP_FIELDS`). A note lane's chance lane
        rides beside them (`note_chance`, {} when none: a loop keeps it in
        its file's `arr`, absent when empty)."""
        a = self.arrangement
        if lane == DRUM_LANE:
            return {k: copy.deepcopy(getattr(a, k)) for k in DRUM_LOOP_FIELDS}
        if lane == self.active_layer:
            out = {k: copy.deepcopy(getattr(a, k)) for k in NOTE_LANE}
            return out | {"note_chance": dict(a.note_chance)}
        lay = self.layers[lane]
        seq = lay.get("seq") or {}
        return {
            "bars": a.bars,
            "steps_per_bar": a.steps_per_bar,
            "cells": [list(c) for c in lay.get("cells") or []],
            "cuts": [list(c) for c in seq.get("cuts") or []],
            "velocities": dict(seq.get("velocities") or {}),
            "slides": list(seq.get("slides") or []),
            "note_nudge": dict(seq.get("note_nudge") or {}),
            "note_chance": dict(seq.get("note_chance") or {}),
        }

    def _edit_state(self, e: dict) -> dict:
        """An open loop's lane as its mirror compares it: a loop opened by
        Edit keeps the tempo, key and meter too (`LOOP_FIELDS`); a slot of
        the launcher plays at the scene's, and keeps its notes alone."""
        now = self.lane_state(e["layer"])
        if e.get("row") is None and e["layer"] != DRUM_LANE:
            a = self.arrangement
            now |= {
                k: copy.deepcopy(getattr(a, k)) for k in LOOP_FIELDS[len(NOTE_LANE) :]
            }
        return now

    def open_loop_edit(
        self,
        name: str,
        lane: int | None = None,
        *,
        row: int | None = None,
        bars: int | None = None,
    ) -> None:
        """`lane` (default: the edited slot) is editing the saved loop
        `name` from now on: call it once the loop is on the lane and its
        pattern undo step pushed — that step (the newest) is the edit's
        mark: undoing it ends the edit (`pop_pattern_undo`). `row`: the
        launcher's row it plays from; `bars`: its own length."""
        lane = self.active_layer if lane is None else lane
        e = {"name": name, "layer": lane, "row": row, "bars": bars}
        e["mark"] = self._pattern_undo[-1] if self._pattern_undo else None
        e["base"] = self._edit_state(e)
        self.loop_edit[lane] = e

    def loop_edit_of(self, lane: int | None = None) -> dict | None:
        """The saved loop open on `lane` (default: the edited slot), or None."""
        return self.loop_edit.get(self.active_layer if lane is None else lane)

    def loop_open(self, name: str) -> bool:
        """Is the saved loop `name` open on any lane?"""
        return any(e["name"] == name for e in self.loop_edit.values())

    def end_loop_edit(self, lane: int | None = None) -> str | None:
        """Stop editing the saved loop open on `lane` (default: the edited
        slot); the name it was editing, or None."""
        was = self.loop_edit.pop(self.active_layer if lane is None else lane, None)
        return was["name"] if was else None

    def end_loop_edits(self, name: str | None = None) -> list[str]:
        """Stop editing every open loop (`name`: that loop, wherever it is
        open); the names that were open."""
        gone = [k for k, e in self.loop_edit.items() if name in (None, e["name"])]
        return [self.loop_edit.pop(k)["name"] for k in gone]

    def rename_loop_edits(self, old: str, new: str) -> None:
        """A loop renamed in the store: wherever it is open, by its new name."""
        for e in self.loop_edit.values():
            if e["name"] == old:
                e["name"] = new

    def loop_edit_synced(self) -> None:
        """The loops' files hold what the lanes hold now (a route wrote
        both, or moved a lane without editing it — a scene launch fitting
        the lanes to one grid): the next mirror has nothing to write."""
        for e in self.loop_edit.values():
            e["base"] = self._edit_state(e)

    def _written_lane(self, e: dict, now: dict) -> dict:
        """A slot's lane as its loop keeps it: its own bars when the grid
        repeats it across a longer loop, untouched, else the grid's whole
        length (an edit past its first bars makes the loop that long)."""
        bars, total = e.get("bars"), now["bars"]
        if e.get("row") is None or not bars or bars >= total:
            return now
        own = loop_window(now, bars)
        if loop_window(own, total) != now:
            e["bars"] = total  # edited past its own length: the loop grows
            return now
        return own

    def mirror_loop_edit(self) -> bool:
        """After a request (routing, every one that may change state): each
        lane's notes (an Edit's tempo, key and meter too; the drum lane's
        beat) written back into the saved loop open on it, when they moved
        since — the edit saves as it goes. Diffed a lane at a time: an
        untouched lane writes nothing. A loop gone from the store ends its
        edit. True when it wrote."""
        from .. import loops  # lazy: loops reads the model this package holds

        wrote = False
        for lane, e in list(self.loop_edit.items()):
            now = self._edit_state(e)
            if now == e["base"]:
                continue
            if lane == DRUM_LANE:
                # (the beat keeps the name the bank or its style gave it)
                was = loops.load(self.data_dir, e["name"]) or {}
                word = (was.get("beat") or {}).get("name") or e["name"]
                beat = self.pattern_from_arrangement(word)
                changes = {"beat": beat, "arr": {"bars": beat["bars"]}, "edited": True}
            else:
                moved = any(
                    now.get(k) != e["base"].get(k) for k in (*NOTE_LANE, "note_chance")
                )
                arr = self._written_lane(e, now)
                changes = {"arr": arr, "edited": True} if moved else {"arr": arr}
            if not loops.update(self.data_dir, e["name"], **changes):
                del self.loop_edit[lane]
                continue
            e["base"] = now
            wrote = True
        return wrote

    def push_fx_undo(self, scope: str, before: list, key: str | None = None) -> None:
        """`before` (a copy of a session FX scope's chain, taken before an
        edit of the rack) as one step of the patch ring, when the chain has
        moved off it since — so "Undo the last tweak" (Ctrl+Z on Synths)
        takes back a rack edit as it takes back a knob of the sound. `key`
        (scope, unit and parameter) joins a knob's drag into one step, as
        `push_history` does for the patch; a press has none. The step
        remembers the track whose sound was being edited (None: the
        loop's), and is skipped by an undo made outside that edit: a drum
        track's kit edit swaps the drum bus without swapping this ring."""
        chain = self.fx_chain(scope)
        if chain is None or chain == before:
            return
        now = monotonic()
        last = self._history[-1] if self._history else {}
        joins = key and _FX_STEP in last and last["key"] == key
        if joins and now - self._history_at < self.UNDO_COALESCE_S:
            self._history_at = now
            return
        self._history.append(
            {
                _FX_STEP: scope,
                "chain": copy.deepcopy(before),
                "key": key,
                "track": self.song_edit_track,
            }
        )
        del self._history[: -self.UNDO_CAP]
        self._history_at = now

    def pop_history(self) -> bool:
        """Undo the newest step of the patch ring: the patch, or one FX
        scope's chain (`push_fx_undo`). False with nothing to undo."""
        while self._history:
            step = self._history.pop()
            self._history_at = -1e9  # the next edit is a fresh step
            if _FX_STEP not in step:
                step.pop(_MOVED, None)
                base = step.pop(_SOUND_BASE, None)
                self.patch = _patch_from_json(step) or self.patch
                # back on the sound that was the baseline then: it is again,
                # so "edited" reads against it (a later save stays the
                # baseline for any other step)
                if (
                    isinstance(base, dict)
                    and (base.get("data") or {}).get("patch") == step
                ):
                    self.baselines["sound"] = base
                return True
            chain = self.fx_chain(step[_FX_STEP])
            if chain is not None and step["track"] == self.song_edit_track:
                chain[:] = copy.deepcopy(step["chain"])
                return True
        return False

    # -- the song's ring ---------------------------------------------------------
    # A step is {label, key, song, size}: the whole song as it was before
    # one edit (the object the session held: an edit commits a fresh copy,
    # so keeping the old one costs no copy), what to call the edit, and the
    # control it came from. Undo swaps the session's song with the newest
    # step's and moves the step to the redo stack; a new edit empties that.

    SONG_UNDO_CAP = 48
    # the steps' songs as compact JSON, both stacks together. Measured: a
    # typical song (12 patterns, 6 sections) is 17 KB (87 KB as Python
    # objects), one at every cap (128 full patterns, 32 sections) 141 KB
    # (1.2 MB) — so 48 typical steps are under 1 MB and never meet this,
    # and a ring of cap-sized songs is cut at 29 steps (~34 MB of objects)
    # instead of 48 (~56 MB). The newest step always stays.
    # 8 MiB: a song may run 480 s and hold twelve tracks; a step is still
    # sized once, when pushed.
    SONG_UNDO_BYTES = 8 * 1024 * 1024

    def _song_ring(self, live: dict) -> None:
        """Before every use of the ring, with the song its steps should
        lead to. Another object means the song was replaced behind it (an
        import, a revert, a reset): the steps describe a song that is gone.
        Then the track-sound edit, if one was marked (`song_undo_sound`):
        Synths writes a track's sound into the song in place, outside any
        commit, so the difference since the mark becomes one step here."""
        if self._song_undo_head is not live:
            self._song_undo, self._song_redo = [], []
            self._song_undo_at = -math.inf
            self._song_undo_sound = None
            self._song_undo_head = live
        mark = self._song_undo_sound
        if mark is None:
            return
        i = sm.track_index(live, mark["track"])
        if i is None:
            self._song_undo_sound = None
            return
        now = _sound_of(live["tracks"][i])
        if now != mark["sound"]:
            before = copy.deepcopy(live)
            before["tracks"][i].update(copy.deepcopy(mark["sound"]))
            for gained in now.keys() - mark["sound"].keys():
                before["tracks"][i].pop(gained, None)
            self._song_step(before, mark["label"], None)
            self._song_undo_at = -math.inf
            mark["sound"] = now

    @staticmethod
    def _song_size(song: dict) -> int:
        return len(json.dumps(song, separators=(",", ":"), default=str))

    def _song_step(self, before: dict, label: str, key: str | None) -> None:
        step = {"label": label, "key": key, "song": before}
        self._song_undo.append({**step, "size": self._song_size(before)})
        self._song_redo = []
        del self._song_undo[: -self.SONG_UNDO_CAP]
        while (
            len(self._song_undo) > 1 and self.song_undo_bytes() > self.SONG_UNDO_BYTES
        ):
            del self._song_undo[0]

    def song_undo_relabel(self, label: str) -> None:
        """Name the newest step for what it now holds: a run of starters is
        one step, and "Undo: start: trance" after a Sixties start misled."""
        if self._song_undo:
            self._song_undo[-1]["label"] = label

    def song_undo_bytes(self) -> int:
        """The ring's size as its byte bound counts it."""
        return sum(step["size"] for step in self._song_undo + self._song_redo)

    def _song_sound_sync(self) -> None:
        """After the ring moved the song: the marked track's sound is
        whatever the song holds now (an undo is not a sound edit)."""
        mark = self._song_undo_sound
        if mark is None:
            return
        i = sm.track_index(self.song, mark["track"])
        if i is None:
            self._song_undo_sound = None
        else:
            track = self.song["tracks"][i]
            mark["sound"] = _sound_of(track)

    def song_undo_push(
        self,
        before: dict,
        label: str,
        key: str | None = None,
        window: float | None = None,
    ) -> None:
        """An edit has just replaced `before` with `self.song`: one undo
        step, unless it continues the newest one — same `key` (the control:
        a track's fader, one automation lane, one take), nothing undone
        since, and within `window` seconds of the last push with that key
        (default UNDO_COALESCE_S, the patch ring's; `math.inf` for a take,
        which posts once a loop wrap for as long as it records). A drag
        posts many times and must undo as ONE step."""
        self._song_ring(before)
        self._song_undo_head = self.song
        if self.song == before:
            return  # a commit that moved nothing is no step
        now = monotonic()
        window = self.UNDO_COALESCE_S if window is None else window
        top = self._song_undo[-1] if self._song_undo else None
        joins = key is not None and top is not None and top["key"] == key
        if not (joins and now - self._song_undo_at < window):
            self._song_step(before, label, key)
        self._song_undo_at = now if key is not None else -math.inf
        self._song_sound_sync()

    def _song_swap(self, source: list[dict], sink: list[dict]) -> str | None:
        self._song_ring(self.song)
        if not source:
            return None
        step = source.pop()
        sink.append({**step, "song": self.song, "size": self._song_size(self.song)})
        self.song = self._song_undo_head = step["song"]
        self._song_undo_at = -math.inf  # the next edit is a fresh step
        self._song_sound_sync()
        return step["label"]

    def song_undo(self) -> str | None:
        """Put the song back as it was before the newest step's edit.
        Returns the step's label, or None when there is nothing to undo."""
        return self._song_swap(self._song_undo, self._song_redo)

    def song_redo(self) -> str | None:
        """Take back the last undo. Returns its label, or None."""
        return self._song_swap(self._song_redo, self._song_undo)

    def song_undo_state(self) -> dict:
        """What the Song page's undo / redo buttons say: the label of the
        step each would move (None: nothing to move), how many steps there
        are, and the keys of the undone steps (a take whose step is undone
        has no undo of its own left: `routes/song.py`)."""
        self._song_ring(self.song)
        return {
            "undo": self._song_undo[-1]["label"] if self._song_undo else None,
            "redo": self._song_redo[-1]["label"] if self._song_redo else None,
            "depth": len(self._song_undo),
            "undone": [s["key"] for s in self._song_redo if s["key"]],
        }

    def song_undo_sound(self, track_id: str, label: str) -> None:
        """Synths is about to edit a track's sound in place: remember the
        sound as it is, so what the edit leaves is one undo step (taken the
        next time the ring is used — `_song_ring`)."""
        self._song_ring(self.song)
        self._song_undo_sound = {"track": track_id, "label": label, "sound": None}
        self._song_sound_sync()

    def song_undo_remap(self, fn) -> None:
        """A clip was renamed: every step's takes and one-shot slots follow
        (`rename_clip_refs` rewrites the live song in place), or an undo
        would bring back a name the clip store no longer has."""
        for step in self._song_undo + self._song_redo:
            _map_song(step["song"], fn)

    def song_undo_clips(self) -> dict[str, int]:
        """How many of the ring's steps (undo and redo) hold each clip, as
        a take or a drums track's one-shot: read only — it neither empties
        a ring whose song was replaced (those steps lead nowhere: nothing)
        nor takes the pending track-sound step. Deleting a clip asks this
        (`clip_usage`): Ctrl+Z would bring back a reference to a file that
        is gone, which then plays silence."""
        if self._song_undo_head is not self.song:
            return {}
        out: dict[str, int] = {}
        for step in self._song_undo + self._song_redo:
            takes, shots = song_clip_names(step["song"])
            for c in set(takes) | set(shots):
                out[c] = out.get(c, 0) + 1
        return out

    def song_undo_long(self) -> dict[str, int]:
        """How many of the ring's steps hold a long audio item playing each
        Lab source (`song["long"]`), read only, as `song_undo_clips` is:
        what deleting a source in the Lab says first."""
        if self._song_undo_head is not self.song:
            return {}
        out: dict[str, int] = {}
        for step in self._song_undo + self._song_redo:
            for sid in {x["source"] for x in step["song"].get("long") or ()}:
                out[sid] = out.get(sid, 0) + 1
        return out
