"""state.json: load, migrate, apply, save.

Owns the file (`_state_path`, the backups), the payload both ways (`_apply`
adopts one, `_payload` writes one, `_migrate` upgrades an older one, the
project export / import on top of them), the rename of every persisted clip
reference, and the save machinery: `save` (serialize and write under the
studio lock), the debounced `save_soon`, its retries and `flush`.

The save's own fields — `_save_lock`, `_save_timer`, `_unsaved` — are used
nowhere else in the package (`OWNERS`). `_save_lock` only guards the timer
and is never held while waiting for the studio lock.

A mixin, not an object of its own: `_apply_or_restore` rolls a failed load
back by restoring `self.__dict__`, which only covers fields that live on
the one `StudioState` instance, and `_apply` resets every unit's runtime
fields (the undo rings, the track edit) as part of adopting a session.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from time import sleep

from ...synth import Patch
from .. import labchops, schema, song_bridge
from ..clips import rename_clip
from .model import (
    RESET_SCOPES,
    STATE_VERSION,
    Arrangement,
    _lane_names,
    _map_baselines,
    _map_lane,
    _map_slots,
    _map_song,
    _patch_from_json,
    _patch_to_json,
    _plain_keys,
    _slot_names,
    log,
    song_clip_names,
)


class Persistence:
    """The session's file: load / migrate / apply, payload, save and flush."""

    # the session's fields this unit reads or writes (all set in
    # StudioState.__init__; test_state_split.py holds this list to the code)
    data_dir: Path
    patch: Patch
    arrangement: Arrangement
    slot_a: Patch | None
    slot_b: Patch | None
    ab_live: str | None
    slot_prev: Patch | None
    _history: list[dict]
    _history_at: float
    _layer_undo: dict[int, dict]
    _pattern_undo: list[dict]
    song_edit_track: str | None
    _edit_saved: dict | None
    loop_edit: dict[int, dict]
    coach: dict | None
    take_history: list[dict]
    lock: threading.RLock
    _save_lock: threading.Lock
    _save_timer: threading.Timer | None
    _unsaved: bool
    edits: int
    board_epoch: int
    baselines: dict
    layers: list[dict]
    active_layer: int
    song: dict

    # -- persistence --------------------------------------------------------
    @property
    def _state_path(self) -> Path:
        return self.data_dir / "state.json"

    def _load(self) -> None:
        if self._state_path.exists():
            # Exception, not a list of types: new exception types kept
            # escaping (AttributeError, OverflowError, RecursionError...).
            # Startup must not depend on predicting them.
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                migrated = self._migrate(data)
                self._apply_or_restore(migrated)
                if migrated is not data:
                    self._backup_legacy()
            except Exception:
                log.exception(
                    "state.json failed to load; backing up and using defaults"
                )
                self._backup_corrupt()
        if not self.baselines:  # first run: revert means "back to how I found it"
            self.mark_baselines(RESET_SCOPES, "as opened")

    def _apply_or_restore(self, data: dict) -> None:
        """Run `_apply`, rolling the session back to exactly how it was if
        it raises partway through. `_apply` mutates `self` by reassigning
        whole attributes (`self.patch = ...`, `self.layers = ...`, ...) —
        it never mutates an existing list/dict of self's in place — so a
        shallow snapshot of `__dict__` is a correct, cheap undo. Without
        this, a rejected import (or a corrupt state.json caught below)
        could still leave the session half-applied: e.g. the new patch
        adopted but the old arrangement kept, because `_apply` raised
        between the two assignments.

        The save's own fields (`_SAVE_FIELDS`) are not part of the session
        being applied and are left as they are *now*: the debounce timer
        can fire while a failing import runs, and putting the fired timer
        back made `flush` see a save pending and a later failed timed save
        skip its retry ("a newer timer is waiting")."""
        snapshot = copy.copy(self.__dict__)
        try:
            self._apply(data)
        except Exception:
            now = {k: self.__dict__[k] for k in self._SAVE_FIELDS}
            self.__dict__.clear()
            self.__dict__.update(snapshot | now)
            raise

    # what a rollback never touches: the fields `OWNERS` gives to this
    # module alone (tests/studio/test_state_save.py holds the two together)
    _SAVE_FIELDS = ("_save_lock", "_save_timer", "_unsaved")

    def _backup_legacy(self) -> None:
        """Keep the pre-migration state.json (once): the next save rewrites
        it as v3, and the legacy scenes it held are not written again."""
        dest = self.data_dir / "state.json.v2.bak"
        if dest.exists():
            return
        try:
            shutil.copy2(self._state_path, dest)
        except OSError:
            log.exception("failed to back up legacy %s", self._state_path)

    def _backup_corrupt(self) -> None:
        """Preserve a state.json that failed to load instead of silently
        overwriting it on the next save — the only copy of that session
        lives in this one file."""
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        dest = self.data_dir / f"state.json.corrupt-{stamp}"
        try:
            shutil.copy2(self._state_path, dest)
        except OSError:
            log.exception("failed to back up corrupt %s", self._state_path)

    @staticmethod
    def _migrate(data: dict) -> dict:
        """Upgrade an older state/project dict to the current schema, so no
        caller special-cases old files itself. v1 (no "v" key) and v2 hold
        scenes and a scene running order; v3 holds the song model instead
        (`song_bridge.migrate_v2`). Returns `data` itself when it
        is already current (or not a dict at all: `_apply` rejects it). The
        shape of `song` decides before "v" does: a list is the legacy
        running order and a dict is a song, whatever "v" says, so a damaged
        "v" can never throw a real song away."""
        if not isinstance(data, dict):
            return data
        song, v = data.get("song"), data.get("v")
        if isinstance(song, dict):
            return data
        current = isinstance(v, int) and not isinstance(v, bool) and v >= 3
        if current and not isinstance(song, list):
            return data
        return song_bridge.migrate_v2(data)

    # -- project export / import ----------------------------------------------
    PROJECT_FORMAT = "synthlab-project"

    def project_payload(self) -> dict:
        """Everything save() writes (including its own "v"), tagged so
        import can recognise it."""
        return {"format": self.PROJECT_FORMAT, **self._payload()}

    def load_project(self, data: dict) -> None:
        """Replace the session with an exported project (raises ValueError
        on anything that isn't one) and persist it."""
        if not isinstance(data, dict) or data.get("format") != self.PROJECT_FORMAT:
            raise ValueError("not a synthlab project file")
        try:
            self._apply_or_restore(self._migrate(data))
        except Exception as err:  # any failure is a rejected import, never a 500
            raise ValueError(f"corrupt project: {err}") from err
        self.save()

    def _apply(self, data: dict) -> None:
        """Adopt a persisted payload. The whole thing is conformed first
        (schema.PAYLOAD, a pure function) and only then assigned, so a
        payload the schema rejects — not a dict, or missing its patch,
        arrangement or A/B slots — changes nothing."""
        clean = schema.conform_strict(data, schema.nodes().PAYLOAD)
        self.patch = Patch.from_dict(clean["patch"])
        self.arrangement = Arrangement(**clean["arrangement"])
        self.slot_a = _patch_from_json(clean["slot_a"])
        self.slot_b = _patch_from_json(clean["slot_b"])
        self.ab_live = None
        self.slot_prev = _patch_from_json(clean["slot_prev"])
        top = schema.nodes().PAYLOAD.fields
        for name in _plain_keys():
            # (a key absent when unset — Perform's launcher — is its default)
            setattr(self, name, clean[name] if name in clean else top[name].default())
        # the track ids an edit named may be gone; the loaded patch is the
        # loop's, so nothing set aside is restored over it
        self.song_edit_track, self._edit_saved = None, None
        self.loop_edit = {}  # saved loops on the old grid: not this one's
        self.coach = None  # the coach's thread: its steps were the old ring's
        # a new session's patches: no undo step may reach the old one's
        self._history, self._history_at = [], -1e9
        self._layer_undo = {}
        self._pattern_undo = []
        self.drop_variations()  # the old loop's (state/drums.py)
        self.take_history = []  # the old song's takes: undo must not reach it
        # the old session's board is gone: a litter still breeding for it
        # must not land on this one (routes/breed.py)
        self.board_epoch += 1
        if not self.baselines:
            # a session saved before baselines existed (or an imported
            # project without them): "how it was" is how it opened
            self.mark_baselines(RESET_SCOPES, "as opened")
        elif "song" not in self.baselines:  # a migrated v2 session
            self.mark_baseline("song", "as opened")

    def _payload(self) -> dict:
        """What is written: the session **in the schema's conformed form**
        — exactly what a load of it would hold, so `save -> load -> save`
        writes the same bytes from the first save on.

        The live session is not always in that form, and need not be. A
        layer a Sound brought has no `silent` / `seq` / `curves` yet
        (`schema.LAYERS_OTHER` is a render lane, not a slot); `follow`,
        `solo`, `role` and `sound_fx` are added to a slot's dict after its
        other keys; "before last load" holds copies of both. None of that
        is audible or compared: render keys take a slot through
        `render_layer` (which drops the loop-state keys) and sort their
        keys, and `is_dirty` compares scope values — so it only ever showed
        in this file, which then changed on the next restart with no edit.
        Conforming here, once, rather than at each place a slot is built or
        touched, is what makes the file's form a property of the file and
        not of every route that will ever write a slot
        (`tests/studio/test_state_conformed.py`). The live session is left
        as it is: no render key of a running session moves."""
        return self._conformed(self._live_payload())

    @staticmethod
    def _conformed(payload: dict) -> dict:
        """`payload` as `_apply` would adopt it. A save must never fail
        because of this: a session the schema can make nothing of is
        written as it stands (the load would refuse it, and keep the file
        aside as corrupt, as it always did)."""
        try:
            clean = schema.conform_strict(payload, schema.nodes().PAYLOAD)
        except Exception:
            log.exception("the session is not in a savable form; written as it is")
            return payload
        return {"v": payload["v"], **clean}

    def _live_payload(self) -> dict:
        """The session's fields as they stand in memory."""
        # mid track-edit the working sound is the track's (persisted in the
        # song): the session file keeps the loop's own patch, slot_prev,
        # curves, layer FX and sound/fx baselines
        saved = self._edit_saved
        # ...or the working KIT is the drums track's (`_edit_kit`): the file
        # keeps the loop's own kit, drum-bus FX and their baselines
        kit, saved = (saved, None) if saved and "kit" in saved else (None, saved)
        loop = saved or {"patch": self.patch, "slot_prev": self.slot_prev}
        arr = replace(self.arrangement, **kit["kit"]) if kit else self.arrangement
        payload = {
            "v": STATE_VERSION,
            "patch": _patch_to_json(loop["patch"]),
            "arrangement": schema.arr_dict(arr),
            "slot_a": _patch_to_json(self.slot_a),
            "slot_b": _patch_to_json(self.slot_b),
            "slot_prev": _patch_to_json(loop["slot_prev"]),
        }
        for name in _plain_keys():
            payload[name] = getattr(self, name)
        if saved is not None:
            payload["stack_prev"] = saved["stack_prev"]  # slot_prev's other half
            payload["curves"] = saved["curves"]
            layers = [dict(lay) for lay in self.layers]
            layers[self.active_layer]["fx"] = saved["fx"]
            payload["layers"] = layers
            payload["baselines"] = self._loop_baselines(saved)
        if kit is not None:
            payload["fx_drums"] = kit["fx_drums"]
            payload["baselines"] = self._loop_baselines(kit)
        return payload

    # the debounced save's own retries: a write that fails on the timer
    # thread has no caller to tell
    SAVE_RETRIES = 4
    SAVE_RETRY_S = 0.5

    def save(self) -> None:
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            self._unsaved = True  # until the write below lands
        # Serialize AND write under the state lock. `_payload()` hands back
        # live lists and dicts, so walking them after the lock is released
        # would race a request mutating them ("dictionary changed size
        # during iteration") — and writing the file outside the lock let
        # two save() calls race each other: the debounce timer could
        # snapshot stale data, get pre-empted before its own os.replace,
        # let a request-path save (holding the lock only for its own
        # snapshot) write newer data and finish, and then land its stale
        # os.replace last, silently reverting state.json (and on Windows,
        # two concurrent os.replace calls onto the same target can also
        # raise PermissionError instead). Holding self.lock across the
        # whole write makes the two saves mutually exclusive end to end, so
        # whichever one still has the lock when the other releases it is
        # guaranteed to be writing the more current snapshot. The debounce
        # timer calls this from its own thread, which is why the lock is
        # re-entrant: a handler that already holds it can call save()
        # directly with no deadlock risk, because `_save_lock` above is
        # only ever held long enough to cancel the timer and is never held
        # while waiting on `self.lock`.
        with self.lock:
            self.edits += 1
            text = json.dumps(self._payload(), indent=2)
            fd, tmp = tempfile.mkstemp(dir=self.data_dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                    # on disk before the rename: a power loss between the
                    # two left an empty state.json, which the next start
                    # set aside as corrupt and replaced with defaults
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self._state_path)
                # everything asked for up to the snapshot is written; an
                # edit made since holds its own pending timer
                self._unsaved = False
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    def flush(self) -> None:
        """Persist immediately if a save is still pending — a debounced one
        that has not fired, or one that failed (server shutdown must not
        lose the last couple of tweaks). A write that fails is tried again
        a few times before the error is raised."""
        with self._save_lock:
            pending = self._save_timer is not None or self._unsaved
        if not pending:
            return
        for attempt in range(self.SAVE_RETRIES):
            try:
                return self.save()
            except OSError:
                if attempt == self.SAVE_RETRIES - 1:
                    raise
                log.warning("state.json could not be written; trying again")
                sleep(0.1)

    def save_soon(self, delay: float = 2.0) -> None:
        """Trailing-edge debounced save: rapid live tweaks (slider drags
        re-render every 150ms) collapse into one state.json write `delay`
        seconds after the last one. Any direct save() cancels the timer."""
        self._save_later(delay, 0)

    def _save_later(self, delay: float, attempt: int) -> None:
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            self._unsaved = True
            self.edits += 1
            t = threading.Timer(delay, self._timed_save, args=(attempt,))
            t.daemon = True
            self._save_timer = t
            t.start()

    def _timed_save(self, attempt: int) -> None:
        """The debounce timer's save. Nothing above this thread hears an
        exception: `os.replace` raising here (Windows: PermissionError
        while an indexer or a virus scanner holds state.json) used to die
        with the thread, the timer already cleared — so flush() saw nothing
        pending and the last edits were never written. A failed write is
        tried again a few times, and stays pending (`_unsaved`) for the
        next save or the shutdown flush after that."""
        try:
            self.save()
        except Exception:
            if attempt >= self.SAVE_RETRIES:
                log.exception(
                    "state.json could not be written; the changes stay "
                    "pending until the next save"
                )
                return
            log.warning("state.json could not be written; trying again", exc_info=True)
            with self._save_lock:
                newer = self._save_timer is not None  # a later edit's own timer
            if not newer:
                self._save_later(self.SAVE_RETRY_S, attempt + 1)

    def rename_clip_refs(self, old: str, new: str) -> None:
        """Point every persisted reference to clip `old` at `new` — the one
        traversal a clip rename goes through, so the next place that stores
        a clip name has one function to update. Clip names live in an
        arrangement's lane (`clips[].clip`) and drum one-shot slots
        (`drum_samples`), and they are persisted in three places: the live
        arrangement, the song (takes in sections, the drums track's one-shot
        slots — renaming only the live one silently dropped the clip from
        song export and section launches), and any reset baseline whose
        scope owns those fields. Bank / library / pool patterns carry
        neither (schema.PATTERN, PATTERN_DRUMS)."""
        self.remap_clip_refs(lambda c: new if c == old else c)

    def follow_clip_rename(self, old: str, new: str) -> None:
        """A clip `clips.rename_clip` has moved from `old` to `new`: every
        persisted reference follows it (`rename_clip_refs`), so do the
        songs in the undo ring (not an undo step: the file itself moved)
        and the Lab chop it was kept from (`labchops.clip_renamed`)."""
        self.rename_clip_refs(old, new)
        self.song_undo_remap(lambda c: new if c == old else c)
        labchops.clip_renamed(self.data_dir, old, new)

    def rename_clip_everywhere(self, old: str, new: str) -> bool:
        """THE clip rename: the clip itself (`clips.rename_clip`), then
        everything that names it (`follow_clip_rename`), saved — the clip
        library's ✎, ★ Keep of a keys take (the Song page's take ✎ runs
        `follow_clip_rename` inside its song commit). False, nothing
        touched, when `rename_clip` refuses (no such clip, the same name,
        or another clip's)."""
        if not rename_clip(self.data_dir, old, new):
            return False
        self.follow_clip_rename(old, new)
        from .. import loops  # lazy: loops reads this package's model

        loops.clip_renamed(self.data_dir, old, new)  # a loop's audio follows
        self.save()
        return True

    def remap_clip_refs(self, fn) -> None:
        """Every persisted clip reference `c` becomes `fn(c)`, all at once
        (a project import's renames: "jam" -> "jam 2" and "jam 2" -> "jam 3"
        can't chain). The traversal is `map_clip_refs`'s, on the live
        session."""
        arr = self.arrangement
        self.arrangement = replace(
            arr,
            clips=_map_lane(arr.clips, fn),
            drum_samples=_map_slots(arr.drum_samples, fn),
        )
        _map_song(self.song, fn)
        _map_baselines(self.baselines, fn)

    def clip_usage(self) -> dict[str, dict[str, int]]:
        """Where the session holds each clip, read only: `{name: {"song":
        takes in the song, "lane": places on the clip lane, "drums": drum
        rows (the loop's and the song's drums tracks), "saved": revert
        points, "undo": steps of the song's undo / redo ring}}`, a key only
        where the count is not 0. What deleting a clip says first: the
        traversal is `remap_clip_refs`'s, plus the ring (`song_undo_clips`)
        — an undo must not be the way a reference to a deleted file comes
        back unannounced."""
        out: dict[str, dict[str, int]] = {}

        def note(names, where: str, by: int = 1) -> None:
            for c in names:
                at = out.setdefault(c, {})
                at[where] = at.get(where, 0) + by

        takes, shots = song_clip_names(self.song)
        note(takes, "song")
        note(_lane_names(self.arrangement.clips), "lane")
        note(_slot_names(self.arrangement.drum_samples) + shots, "drums")
        bases = self.baselines if isinstance(self.baselines, dict) else {}
        for base in bases.values():
            data = base.get("data") if isinstance(base, dict) else None
            if not isinstance(data, dict):
                continue
            held = _lane_names(data.get("arr.clips"))
            held += _slot_names(data.get("arr.drum_samples"))
            held += [
                c for part in song_clip_names(data.get("state.song")) for c in part
            ]
            note(set(held), "saved")
        for c, steps in self.song_undo_clips().items():
            note([c], "undo", steps)
        return out

    def long_usage(self) -> dict[str, dict]:
        """Which Lab sources the song's long audio plays, read only:
        `{source id: {"names": the items on the live song, "undo": steps of
        the song's undo / redo ring}}`. A source is otherwise outside the
        session; this is what deleting one in the Lab says first — the
        item stays on the song and plays silence."""
        out: dict[str, dict] = {}
        for x in self.song.get("long") or ():
            out.setdefault(x["source"], {"names": [], "undo": 0})["names"].append(
                x["name"]
            )
        for sid, steps in self.song_undo_long().items():
            out.setdefault(sid, {"names": [], "undo": 0})["undo"] = steps
        return out
