"""The song beside the working loop: loop -> section, section -> loop, and
a track's sound edited on Synths.

`edit_track` sets the loop's own sound aside (`_edit_saved`: patch, undo
ring, slot_prev + stack_prev, curves, layer FX, sound/fx baselines) and
`end_track_edit`
gives it back; `Persistence._payload` reads `_edit_saved` so the file keeps
the loop's sound mid-edit. The pure halves live in `song_bridge`.

A mixin: both bridges read and replace the loop's live fields.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, replace

from ...synth import Patch
from .. import schema, song_bridge
from .. import song_model as sm
from .model import DRUM_LANE, Arrangement, _empty_layer, materialise


class SongBridge:
    """loop_to_section / section_to_loop, pool beats, the track-sound edit."""

    # the session's fields this unit reads or writes (all set in
    # StudioState.__init__; test_state_split.py holds this list to the code)
    patch: Patch
    arrangement: Arrangement
    slot_prev: Patch | None
    stack_prev: dict | None
    _history: list[dict]
    _history_at: float
    _layer_undo: dict[int, dict]
    song_edit_track: str | None
    _edit_saved: dict | None
    loop_edit: dict[int, dict]
    perform_slots: list[dict] | None
    perform_parts: list[str]
    curves: dict
    fx_drums: list
    baselines: dict
    layers: list[dict]
    active_layer: int
    song: dict
    active_slot: int | None
    beat_vars: dict[str, dict]
    beat_var: str
    beat_gen: tuple[str, dict] | None
    main_layer: int | None

    # -- the song ---------------------------------------------------------------
    # The working loop (patch, arrangement, layers) is what Synths, Drums
    # and Perform edit; the song sits beside it. Three bridges connect them:
    # loop -> section, section -> loop, and a track's sound
    # edited on Synths. The pure halves live in song_bridge.

    def loop_arr_dict(self) -> dict:
        """The working loop's arrangement as a song holds one: conformed
        (`schema.ARRANGEMENT`), so its kit, its tempo and key compare with
        a track's or a song's as they are written."""
        return schema.conform(asdict(self.arrangement), schema.nodes().ARRANGEMENT)

    def _loop_lanes(self) -> list[dict]:
        """The working loop's lanes: the main lane, then every other layer
        with a sound and notes, in slot order — a muted one included, so a
        section -> loop -> section round trip keeps its (muted) track; a
        lane's `mute` is its own, never one the song's mix put there."""
        self.stash_active_layer()
        src = self.main_arrangement()
        return [
            song_bridge.main_lane(
                self.patch.to_json(),
                asdict(self._lane_over(src, self.arrangement)),
                self.layers[self.active_layer],
                self.curves,
            ),
            # a following layer goes into the song as the notes it plays:
            # a pattern holds notes, not "the main lane's, an octave down"
            *(
                song_bridge.layer_lane(lay)
                for i, lay in enumerate(
                    materialise({**x, "cells": [], "seq": None}, src)
                    if self.follows(j)
                    else x
                    for j, x in enumerate(self.layers)
                )
                if i != self.active_layer and lay.get("patch") and lay.get("cells")
            ),
        ]

    def loop_beats(self) -> list[tuple[str, dict]]:
        """The beat's other variations that hold hits (`DrumBank.beat_vars`,
        A-D beside the one on the grid), in letter order: (letter, the
        loop's conformed arrangement dict with that variation's drum lane).
        What "Add loop to song" carries into the song's pool."""
        base = asdict(self.arrangement)
        return [
            (
                letter,
                schema.conform(
                    {**base, **copy.deepcopy(lane)}, schema.nodes().ARRANGEMENT
                ),
            )
            for letter, lane in sorted(self.beat_vars.items())
            if letter != self.beat_var and lane.get("drums")
        ]

    def loop_to_section(self, name: str = "") -> str:
        """`loop_into_song`'s section id."""
        return self.loop_into_song(name)[0]

    def loop_into_song(self, name: str = "") -> tuple[str, list[tuple[str, str]]]:
        """Add the loop to the song: the working loop becomes a new last
        section. Its drum lane, main grid and sounding layers become pool
        patterns (identical ones shared), each note lane lands on a synth
        track with its sound (reused when one matches, added when none
        does), and its clip lane becomes takes on the audio track. The
        beat's other variations that hold hits go into the pool too, beside
        the one the section plays (a section holds one pattern a track).
        Returns the section's id and those variations' (letter, pattern
        id), less any that is the section's own beat over again. The
        first loop into an empty song also sets the song's tempo, key and
        groove. All or nothing: a song cap raises SongError and leaves the
        song as it was. Ends any track edit first: the loop's OWN sound and
        kit go into the song, and an edit has a track's standing in them."""
        self.end_track_edit()
        song = copy.deepcopy(self.song)
        arr = self.loop_arr_dict()
        if not song["sections"]:
            song |= {k: arr[k] for k in song_bridge.SONG_WIDE}
        index = song_bridge.pattern_index(song)
        name = name or f"Loop {len(song['sections']) + 1}"
        pooled: list[tuple[str, str]] = []
        sid = song_bridge.add_loop_section(
            song,
            index,
            name,
            arr,
            self._loop_lanes(),
            self.fx_drums,
            self.curves,
            beats=self.loop_beats(),
            letter=self.beat_var,
            pooled=pooled,
        )
        self.song = schema.conform(song, schema.nodes().SONG)
        return sid, pooled

    def loop_as_song(self, name: str = "Loop") -> tuple[dict, str]:
        """The working loop as a song of its own — one section, as "Add
        loop to song" would lay it into an empty song (its tempo, key and
        groove, a track a lane, its patterns) — and that section's id. The
        session's song is not touched: "Make this loop a song" arranges
        this one and commits it (`routes/song_start.py`)."""
        self.end_track_edit()  # the loop's own sound and kit, as above
        arr = self.loop_arr_dict()
        song = sm.new_song(arr["bpm"], arr["root"], arr["scale_name"])
        song |= {k: arr[k] for k in song_bridge.SONG_WIDE}
        sid = song_bridge.add_loop_section(
            song, {}, name, arr, self._loop_lanes(), self.fx_drums, self.curves
        )
        return schema.conform(song, schema.nodes().SONG), sid

    # the scopes a section opened in the loop marks (`section_to_loop`)
    _SECTION_SCOPES = ("sound", "sequence", "kit", "pattern", "fx", "fx_drums")

    def _whole_loop(self) -> dict:
        """Everything `section_to_loop` replaces, as it is now: what one
        pattern undo step puts back (`restore_whole_loop`). Deep copies of
        what is edited in place; the objects it swaps whole (the slots'
        undo rings, the variations) kept as they are."""
        return {
            "arrangement": copy.deepcopy(self.arrangement),
            "patch": copy.deepcopy(self.patch),
            "curves": copy.deepcopy(self.curves),
            "layers": copy.deepcopy(self.layers),
            "main_layer": self.main_layer,
            "fx_drums": copy.deepcopy(self.fx_drums),
            "layer_undo": self._layer_undo,
            "beat_vars": self.beat_vars,
            "beat_var": self.beat_var,
            "beat_gen": self.beat_gen,
            "active_slot": self.active_slot,
            "baselines": {k: self.baselines.get(k) for k in self._SECTION_SCOPES},
            # (each one's "mark" stays the very step in the ring that opened it)
            "loop_edit": {k: dict(e) for k, e in self.loop_edit.items()},
            # Perform's launcher: its rows, and which parts ▶ plays (a scene
            # stops the drums by taking them out)
            "perform_slots": self.perform_slots,
            "perform_parts": list(self.perform_parts),
            "sound_step": None,
        }

    def whole_loop_step(self) -> dict:
        """A pattern undo step holding the whole loop as it is now — for an
        edit made from outside the grids that may move any lane and the
        beat at once (the loop coach's Accept and Reverse): make the edit,
        then `push_pattern_undo` it. Undone, it puts the loop back as a
        section launch's step does (`restore_whole_loop`)."""
        return self.pattern_snapshot() | {"whole": self._whole_loop()}

    def restore_whole_loop(self, whole: dict) -> None:
        """Ctrl+Z on a section opened in the loop (`pop_pattern_undo`): the
        loop as it was — notes, beat, every lane and its sound, the tempo,
        key and meter, the variations, a saved loop open on the grid. The
        outgoing sound's step on the patch ring goes too (it is the sound
        put back here). A track edit the launch ended stays ended."""
        self.arrangement = whole["arrangement"]
        self.patch = whole["patch"]
        self.curves = whole["curves"]
        self.layers = whole["layers"]
        self.main_layer = whole["main_layer"]
        self.fx_drums = whole["fx_drums"]
        self._layer_undo = whole["layer_undo"]
        self.beat_vars, self.beat_var = whole["beat_vars"], whole["beat_var"]
        self.beat_gen = whole["beat_gen"]
        self.active_slot = whole["active_slot"]
        for scope, base in whole["baselines"].items():
            if base is None:
                self.baselines.pop(scope, None)
            else:
                self.baselines[scope] = base
        # the loops open then are open again; one still open since holds in
        # its file what its lane last wrote (an edit after the step — a
        # coach's Accept — saved to it): that is its base, so the mirror
        # writes the lane put back into it
        for lane, e in whole["loop_edit"].items():
            now = self.loop_edit.get(lane)
            if now is not None and now["name"] == e["name"]:
                e["base"] = now["base"]
        self.loop_edit = whole["loop_edit"]
        self.perform_slots = whole["perform_slots"]
        self.perform_parts = whole["perform_parts"]
        if self._history and self._history[-1] is whole["sound_step"]:
            self._history.pop()
        for i, lay in enumerate(self.layers):
            if i != self.active_layer and not lay.get("patch"):
                # a lane the step took away again: nothing of a slot's
                # ring or baselines is left to bring back
                self._layer_undo.pop(i, None)
        self.show_notes()  # the roll's window back on the loop's notes

    def section_to_loop(self, section_id) -> bool:
        """Open a section in the working loop: its patterns and its tracks'
        sounds — the main lane into the active slot, the other lanes into
        the other slots in order (a lane past the third has no slot and is
        left out). The master FX stay the session's. Ends any track edit.
        ONE pattern undo step (Ctrl+Z on Perform) puts the loop back as it
        was (`_whole_loop`). False for
        an unknown section."""
        i = sm.section_index(self.song, section_id)
        if i is None:
            return False
        sec = self.song["sections"][i]
        # its patterns in its own bar and the song at its own tempo
        # (`sm.in_meter`): a 6/8 section opens as a 6/8 loop
        heard = sm.in_meter(self.song, sec)
        p = song_bridge.section_loop(heard, sec)
        if sm.section_meter(self.song, sec) != "4/4":
            p["arr"]["meter"] = sm.section_meter(self.song, sec)
        # the pieces as loop state: each layer's own sequencing, curves and
        # mix flag, every lane on the main lane's grid over the whole cycle
        song_bridge.to_loop(heard, p)
        n = schema.nodes()
        self.end_track_edit()
        # (after the track edit ends: the step is the loop's own sound)
        step = self.pattern_snapshot() | {"whole": self._whole_loop()}
        self.loop_edit = {}  # the grid holds the section now, not a saved loop
        self.arrangement = Arrangement(**schema.conform(p["arr"], n.ARRANGEMENT))
        active = self.layers[self.active_layer]
        if p["patch"] is not None:
            # a load like any other: the outgoing sound is one undo step
            # away and in "before last load"
            self.stash_for_load()
            step["whole"]["sound_step"] = self._history[-1] if self._history else None
            self.patch = Patch.from_dict(schema.conform(p["patch"], n.PATCH))
            self.curves = schema.conform(p["curves"], n.CURVES)
            active["fx"] = schema.conform(p["main"]["fx"], n.FX)
            active["muted"] = bool(p["main"]["muted"])
            active["silent"] = bool(p["main"]["silent"])
            active["gain"] = p["main"]["gain"]
        others = [schema.conform(lay, n.LAYER) for lay in p["layers"]]
        for j in range(len(self.layers)):
            if j != self.active_layer:
                self.layers[j] = others.pop(0) if others else _empty_layer()
        self._make_active_main()
        # the other slots now hold the section's lanes: nothing an old
        # slot's undo ring or baselines held applies to them. The pattern
        # ring keeps its steps under the one that puts the old loop back:
        # they are reached only once it has (`pop_pattern_undo`)
        self._layer_undo = {}
        self.drop_variations()  # the old loop's (state/drums.py)
        if p["fx_drums"] is not None:
            self.fx_drums = schema.conform(p["fx_drums"], n.FX)
        self.mark_baselines(self._SECTION_SCOPES, sec["name"])
        # the section's beat is its own, not whatever bank slot was active:
        # otherwise Perform would mark that slot "playing"
        self.active_slot = None
        self.push_pattern_undo(step)
        self.show_notes()  # the roll's window on the section's notes
        return True

    def lane_sound(
        self,
        index: int,
        patch: Patch,
        chain: dict | None = None,
        *,
        how: str = "",
        label: str = "",
    ) -> bool:
        """A Perform lane head's sound ▾: a
        picked sound into lane `index`. A lane holding a sound becomes the
        edited one — a load lands in the edited lane, as Use's does — and
        takes it as `load_sound_as` says (`how`; "add" leaves the edited
        lane where it is). An empty lane becomes a lane of its own: the
        sound's patch and FX, not following, its own empty grid, and the
        edited one (as the lane switch's ＋ makes one). ONE pattern undo
        step (Ctrl+Z on Perform) puts the loop back as it was, as a section
        opened in it is (`_whole_loop`); the sound is one sound undo step
        on that lane's patch ring too. False when "add" finds every slot
        holding a sound: nothing changed."""
        empty = index != self.active_layer and not self.layers[index].get("patch")
        if not empty:
            self.select_layer(index)
        step = self.pattern_snapshot() | {"whole": self._whole_loop()}
        if empty:
            fx = (chain or {}).get("main_fx") or []
            self.add_layer(patch, fx, select=False, slot=index)
            self.set_follow(index, None)
        else:
            before = self.patch_snapshot()
            top = self._history[-1] if self._history else None
            if not self.load_sound_as(patch, chain, how=how, label=label, select=False):
                return False
            self.push_undo_step(before)
            if self._history and self._history[-1] is not top:
                step["whole"]["sound_step"] = self._history[-1]
        self.push_pattern_undo(step)
        if empty:
            # (after the step: undone, it goes back to the lane it was on)
            self.select_layer(index)
        return True

    def apply_pool_beat(self, pattern_id) -> bool:
        """Perform's Beats launcher: put a pool drums pattern on the working
        loop's drum lane (hits, velocities, clock). The bank is the pool
        now. False for an unknown or non-drums pattern."""
        pool = self.song["patterns"]
        p = pool.get(pattern_id) if isinstance(pattern_id, str) else None
        if not p or p.get("kind") != "drums":
            return False
        self.arrangement = replace(self.arrangement, **song_bridge.beat_lane(p))
        self.arrangement = Arrangement(**self.loop_arr_dict())
        self.active_slot = None
        self.mark_baseline("pattern", p["name"])
        self.end_loop_edit(DRUM_LANE)  # the pool's beat now, not a beat loop's
        return True

    def clear_migration_note(self) -> bool:
        """The Song page has shown the v2 -> v3 migration note: clear it,
        in the song and in the song's baseline too, so the reset menu's
        "changed" dot doesn't light for it and a revert can't bring it back.
        False when there was no note."""
        if not self.song.get("migration_note"):
            return False
        self.song = {**self.song, "migration_note": ""}
        base = self._baseline_data("song")
        old = base.get("state.song") if base else None
        if isinstance(old, dict) and "migration_note" in old:
            base["state.song"] = {**old, "migration_note": ""}
        return True

    def edit_track(self, track_id) -> bool:
        """Start editing a synth track's sound on Synths. The loop's patch,
        undo ring and "before last load" stash (`slot_prev` and `stack_prev`,
        one stash) are set aside and the track's patch becomes the working
        one, with an empty undo ring and no stash — so undo / restore-prev
        can only ever reach sounds of this edit, never the loop's. Every
        patch change on Synths from here on is mirrored into the track;
        `end_track_edit` gives the loop its own patch, ring and stash back."""
        i = sm.track_index(self.song, track_id)
        kind = None if i is None else self.song["tracks"][i]["kind"]
        if kind == "drums":
            return self._edit_kit(track_id, self.song["tracks"][i])
        if kind != "synth":
            return False
        self.end_track_edit()
        track = self.song["tracks"][i]
        active = self.layers[self.active_layer]
        self._edit_saved = {
            "patch": self.patch,
            "history": self._history,
            "slot_prev": self.slot_prev,
            # one stash with slot_prev: the lanes, FX and curves a whole
            # Sound replaced. Left live, a patch-only load on the track
            # dropped it (`stash_prev`) and a chain load on the track left
            # its own snapshot paired with the loop's slot_prev
            "stack_prev": self.stack_prev,
            # the track's sound is its patch *and* its curves and FX: while
            # it is on Synths, the curve editor and the layer rack edit the
            # track's, never the loop's
            "curves": self.curves,
            "fx": active.get("fx") or [],
            "baselines": {k: self.baselines.get(k) for k in ("sound", "fx")},
        }
        self._history, self._history_at = [], -1e9
        self.slot_prev, self.stack_prev = None, None
        self.patch = Patch.from_dict(track["patch"])
        self.curves = copy.deepcopy(track.get("curves") or {})
        active["fx"] = copy.deepcopy(track.get("fx") or [])
        self.song_edit_track = track_id
        # revert / "changed" measure against the track as it was opened
        self.mark_baselines(("sound", "fx"), str(track.get("name") or "track"))
        return True

    # -- the drums track's kit, edited in the Drum Lab -----------------------------
    # The same edit for the song's drums: the Drum Lab works on the loop's
    # kit (`arrangement.drum_params` / `drum_mix` / `drum_samples`) and its
    # bus FX (`fx_drums`), so those are set aside and the TRACK's stand in
    # their place — `_edit_saved` with `kit`, and every change made from the
    # Drums view is the track's (`mirror_patch_to_track`). The loop's beat
    # (its hits, its bank) is not part of it: a song's beats are patterns.

    def _edit_kit(self, track_id, track: dict) -> bool:
        self.end_track_edit()
        kit = song_bridge.KIT
        self._edit_saved = {
            "kit": {k: getattr(self.arrangement, k) for k in kit},
            "fx_drums": self.fx_drums,
            "baselines": {k: self.baselines.get(k) for k in ("kit", "fx_drums")},
        }
        self.arrangement = replace(
            self.arrangement, **{k: copy.deepcopy(track[k]) for k in kit if k in track}
        )
        self.fx_drums = copy.deepcopy(track.get("fx") or [])
        self.song_edit_track = track_id
        self.mark_baselines(("kit", "fx_drums"), str(track.get("name") or "drums"))
        return True

    def track_edit_view(self) -> str | None:
        """The view a running track edit is made from — "editor" for a
        synth track's sound, "drums" for the drums track's kit — or None.
        Routing ends the edit on a request from any view but this one and
        the Song page, and mirrors after a request from this one."""
        if self.song_edit_track is None:
            return None
        return "drums" if self._edit_saved and "kit" in self._edit_saved else "editor"

    def _kit_sound(self) -> dict:
        """The working kit as a drums track holds it."""
        arr = self.loop_arr_dict()
        return {k: arr[k] for k in song_bridge.KIT} | {
            "fx": schema.conform(self.fx_drums, schema.nodes().FX)
        }

    def _loop_baselines(self, saved: dict) -> dict:
        """The session's baselines with the loop's own sound/fx ones in
        place of the track edit's (what `_payload` persists mid-edit)."""
        out = dict(self.baselines)
        for scope, base in saved["baselines"].items():
            if base is None:
                out.pop(scope, None)
            else:
                out[scope] = base
        return out

    def end_track_edit(self) -> bool:
        """Stop editing a track's sound: the loop's patch, undo ring,
        slot_prev, curves, layer FX and sound/fx baselines come back as
        they were. False when no edit was running. Called on any request
        from a view other than Synths (routing), by select_layer and
        section_to_loop, and when the track is gone."""
        saved, self._edit_saved = self._edit_saved, None
        was = self.song_edit_track is not None
        self.song_edit_track = None
        if saved is not None and "kit" in saved:  # the drums track's kit
            self.arrangement = replace(self.arrangement, **saved["kit"])
            self.fx_drums = saved["fx_drums"]
            for scope, base in saved["baselines"].items():
                if base is None:
                    self.baselines.pop(scope, None)
                else:
                    self.baselines[scope] = base
        elif saved is not None:
            self.patch = saved["patch"]
            self._history, self._history_at = saved["history"], -1e9
            self.slot_prev = saved["slot_prev"]
            self.stack_prev = saved["stack_prev"]
            self.curves = saved["curves"]
            self.layers[self.active_layer]["fx"] = saved["fx"]
            for scope, base in saved["baselines"].items():
                if base is None:
                    self.baselines.pop(scope, None)
                else:
                    self.baselines[scope] = base
        return was

    def mirror_patch_to_track(self) -> bool:
        """Copy the working sound — patch, curves and layer FX — into the
        track being edited, if any and if it differs. True when the track
        changed. A track that has gone (or is not a synth track) ends the
        edit. Every route runs this after a successful handler on a request
        from Synths (routing._under_state_lock), which is the one place
        every patch mutation passes through."""
        tid = self.song_edit_track
        if tid is None:
            return False
        i = sm.track_index(self.song, tid)
        kind = "drums" if self.track_edit_view() == "drums" else "synth"
        if i is None or self.song["tracks"][i]["kind"] != kind:
            self.end_track_edit()
            return False
        track = self.song["tracks"][i]
        if kind == "drums":
            sound = self._kit_sound()
            if all(track.get(k) == v for k, v in sound.items()):
                return False
            track.update(sound)
            return True
        sound = self.working_track_sound()
        if all(track.get(k) == v for k, v in sound.items()):
            return False
        track.update(sound)
        return True

    def working_track_sound(self) -> dict:
        """The working sound as a synth track holds it: patch, curves and
        layer FX (what `mirror_patch_to_track` writes; what ▶ renders the
        edited track with, on a copy of the song, without writing it)."""
        n = schema.nodes()
        return {
            "patch": schema.conform(self.patch.to_json(), n.PATCH),
            "curves": schema.conform(self.curves, n.CURVES),
            "fx": schema.conform(self.layers[self.active_layer].get("fx"), n.FX),
        }

    def follow_track_edit(self) -> bool:
        """The song moved under a track edit (the Song page's undo, a new
        song, the track deleted): the working sound is the track's again,
        as `edit_track` opened it — or the edit ends when the track has
        gone. True when anything changed. The other half of
        `mirror_patch_to_track`: routing runs it after a request from the
        Song page, which keeps the edit open while it docks Synths."""
        tid = self.song_edit_track
        if tid is None:
            return False
        i = sm.track_index(self.song, tid)
        kind = "drums" if self.track_edit_view() == "drums" else "synth"
        if i is None or self.song["tracks"][i]["kind"] != kind:
            return self.end_track_edit()
        track = self.song["tracks"][i]
        if kind == "drums":
            now = self._kit_sound()
            if all(track.get(k) == v for k, v in now.items()):
                return False
            kit = {k: copy.deepcopy(track[k]) for k in song_bridge.KIT if k in track}
            self.arrangement = replace(self.arrangement, **kit)
            self.fx_drums = copy.deepcopy(track.get("fx") or [])
            self.mark_baselines(("kit", "fx_drums"), str(track.get("name") or "drums"))
            return True
        n = schema.nodes()
        active = self.layers[self.active_layer]
        same = (
            track.get("patch") == schema.conform(self.patch.to_json(), n.PATCH)
            and (track.get("curves") or {}) == schema.conform(self.curves, n.CURVES)
            and (track.get("fx") or []) == schema.conform(active.get("fx"), n.FX)
        )
        if same:
            return False
        self._history, self._history_at = [], -1e9
        self.slot_prev, self.stack_prev = None, None
        self.patch = Patch.from_dict(track["patch"])
        self.curves = copy.deepcopy(track.get("curves") or {})
        active["fx"] = copy.deepcopy(track.get("fx") or [])
        self.mark_baselines(("sound", "fx"), str(track.get("name") or "track"))
        return True
