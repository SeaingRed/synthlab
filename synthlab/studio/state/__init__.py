"""Studio session state: current patch, arrangement, A/B slots, presets.

Plain JSON files under a data directory. Writes are atomic (temp file +
os.replace). Corrupt files fall back to defaults — never a crash. The
theme is NOT here: it lives in a browser cookie.

One object, `StudioState`, in several modules — each a mixin over the
session's fields, holding one concern (studio `docs/state.md`, "state/"):

- `model`    the Arrangement, scopes, layer / lane helpers, validation
- `persist`  state.json: load, migrate, apply, payload, save / flush
- `layers`   slots, the main lane, followers, the mix
- `history`  the patch undo ring, the pattern undo ring, the song's ring
- `song`     loop <-> section, the track-sound edit
- `drums`    the drum bank and pattern library
- `presets`  preset files, sound loads, "before last load"
- `scopes`   baselines, revert, defaults, "changed"
- `slots`    Perform's launcher: its slots, launching a slot or a scene

Everything that was importable from the one-file `state.py` is importable
from here (tests/studio/test_state_split.py pins the list).
"""

# ruff: noqa: F401
# Unused imports are kept on purpose: every name the one-file module had in
# its namespace is still importable (and patchable) from here.

from __future__ import annotations

import copy
import json
import logging
import math
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep

from ... import sampler, wavetables
from ...sequencer import SCALES, midi, name_of, scale
from ...synth import Patch
from .. import schema, song_bridge
from .. import song_model as sm
from ..drumfactory import DRUM_FACTORY
from ..files import safe_name
from ..paramui import PARAM_GROUPS
from ..soundmeta import preset_meta
from ..views import BENCH_VIEWS
from ..views import RESET_VIEWS as _VIEW_RESETS
from .drums import DrumBank
from .history import History
from .layers import Layers
from .model import (
    _LAYER_STATE_KEYS,
    _OBJECT_KEYS,
    BANK_SLOTS,
    DRUM_ROWS,
    LAYER_SCOPES,
    LAYER_SEQ_KEYS,
    LEGACY_PANELS,
    RESET_SCOPES,
    RESET_VIEWS,
    ROOTS,
    SCALE_CHOICES,
    STATE_VERSION,
    Arrangement,
    Lane,
    _clean_arrangement,
    _clean_pattern,
    _clean_song,
    _empty_layer,
    _lane,
    _map_baselines,
    _map_lane,
    _map_slots,
    _map_song,
    _meta_ok,
    _patch_from_json,
    _patch_to_json,
    _plain_keys,
    _read_json,
    audible_layers,
    clip_refs,
    drum_grid,
    drum_row_sounds,
    is_follower,
    key_ladder,
    lane_fields,
    log,
    map_clip_refs,
    materialise,
    patch_from_json,
    pitch_rows,
    preset_from_json,
    render_layer,
    sounds_in_loop,
    unwrap_preset,
)
from .persist import Persistence
from .presets import Presets
from .scopes import Scopes
from .slots import Slots
from .song import SongBridge

# Which modules of this package may touch which runtime field (`self.<name>`;
# the constructor below declares them all). One owner where the field is one
# concern's own; where several are listed the field is the edited slot's and
# changes hands as a whole — a new use anywhere else fails
# tests/studio/test_state_split.py, which is the place to write down why.
OWNERS: dict[str, tuple[str, ...]] = {
    # the debounced save: its timer, and whether state.json is behind
    "_save_lock": ("persist",),
    "_save_timer": ("persist",),
    "_unsaved": ("persist",),
    # is_dirty's answers inside a `dirty_memo` block
    "_dirty_memo": ("scopes",),
    # the edited slot's patch undo ring: pushed and popped by history; swapped
    # whole by a layer switch and a track edit; emptied by a loaded session
    "_history": ("history", "layers", "song", "persist"),
    "_history_at": ("history", "layers", "song", "persist"),
    # the inactive slots' rings, stashes and baselines, by slot index: kept by
    # a layer switch; a slot's entry dropped by whatever puts a new lane in it
    "_layer_undo": ("layers", "presets", "song", "persist"),
    # the step grids' ring: session-wide; emptied when a session is loaded
    # (a section opened in the loop is one step on it: song pushes it
    # through history)
    "_pattern_undo": ("history", "persist"),
    # the loop's own sound, set aside while a track's is on Synths; the
    # payload reads it so the file keeps the loop's
    "_edit_saved": ("song", "persist"),
    # (history reads it: an FX undo step is the edit's it was taken in)
    "song_edit_track": ("song", "persist", "history"),
    # the saved loops the grids are editing, one a lane (loops.py): history
    # opens, mirrors and ends them (the pattern ring's step that opened one,
    # undone, ends it); a section opened and a loaded session end them too;
    # Perform's launcher (slots) opens one on each lane it launches, through
    # history's methods; a sound load replacing a lane (presets) ends the
    # edit open there, so its saved loop is never written over by the new lane
    "loop_edit": ("history", "song", "persist", "presets"),
    # the loop coach's proposal and thread (loopcoach.py, outside this
    # package): a loaded session drops them — an accepted change's ↶
    # Reverse must never write the old session's loop into the new one
    "coach": ("persist",),
    # "before last load": the edited slot's, like its ring
    "slot_prev": ("presets", "layers", "song", "persist"),
    "stack_prev": ("presets", "layers", "song"),
    "sound_brought": ("presets",),
    # one entry per note take (the song routes push and pop it): emptied
    # whenever the song it points into is replaced
    "take_history": ("scopes", "persist"),
    # counts the times the breed board was replaced whole (a loaded session,
    # a board revert / reset); a litter bred outside the lock reads it
    "board_epoch": ("scopes", "persist"),
    # the song's undo and redo stacks, the clock that joins a drag's posts
    # into one step, the song object the steps lead to (another one: the
    # song was replaced, and the ring empties itself — so no unit that
    # replaces the song has to know the ring exists), and the track sound
    # Synths is editing in place. history's alone.
    "_song_undo": ("history",),
    "_song_redo": ("history",),
    "_song_undo_at": ("history",),
    "_song_undo_head": ("history",),
    "_song_undo_sound": ("history",),
}


class StudioState(
    Persistence, Layers, History, SongBridge, DrumBank, Presets, Scopes, Slots
):
    """The session: every caller's one object. The fields are declared here
    (and only here); the methods come from the mixins, one concern each."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.patch = Patch()
        self.arrangement = Arrangement()
        self.slot_a: Patch | None = None
        self.slot_b: Patch | None = None
        # which A/B slot the live patch is ("a"/"b"): edits are made to it,
        # and pressing the other slot saves them into it first. None: the
        # live sound is in neither (fresh, or loaded since). Session-only,
        # like the undo ring — after a restart nothing is lit until a slot
        # is pressed.
        self.ab_live: str | None = None
        # automatic stash of whatever a load overwrote — the guaranteed way
        # back after a stray card click (loads also re-mark the baseline, so
        # revert can never reach the pre-load sound without this)
        self.slot_prev: Patch | None = None
        # what the last whole Sound brought besides its patch and FX (its
        # other lanes and curves, as placed): a next load clears what is
        # still as it came (`load_sound`); runtime, like the stash
        self.sound_brought: dict | None = None
        # session-only undo ring — deliberately not persisted: undo is for
        # "that drag was a mistake", not cross-session history
        self._history: list[dict] = []
        self._history_at: float = -1e9
        # the inactive layer slots' own undo rings and LAYER_SCOPES
        # baselines, by slot index (runtime, like the ring): select_layer
        # swaps them, so undo/revert only ever reach the slot on screen
        self._layer_undo: dict[int, dict] = {}
        # the step grids' own undo ring (session-only, like the patch's):
        # one entry per pattern edit — a grid stroke, a pattern tool, a
        # drum row tool — so Clear or a stray Randomise is one Ctrl+Z away
        self._pattern_undo: list[dict] = []
        # the song's undo ring (session-only too; state/history.py, studio
        # docs/song.md "Undo and redo"): whole songs, newest last
        self._song_undo: list[dict] = []
        self._song_redo: list[dict] = []
        self._song_undo_at: float = -math.inf
        self._song_undo_head: dict | None = None
        self._song_undo_sound: dict | None = None
        # Every other persisted attribute — curves, the breed board, the
        # audition phrase, macros (a *control* layer, not part of the sound:
        # each one maps a single 0..1 knob onto several existing params, the
        # XY pad generalised from 2 axes to N; keeping them off the Patch
        # means the genome, breeding and every render path stay exactly as
        # they were), the FX racks, layers (3 synth lanes — the ACTIVE
        # layer's patch/cells live in self.patch / self.arrangement.cells;
        # inactive layers are stashed here), the song (song_model: tracks,
        # the pattern pool, sections — beside the working loop, never
        # rendered through it), and the drum pattern bank — takes its
        # default straight from schema.PAYLOAD, so this list can't drift
        # from what actually gets persisted.
        for name in _plain_keys():
            setattr(self, name, schema.nodes().PAYLOAD.fields[name].default())
        # runtime only, never persisted: the synth track whose sound Synths
        # is editing. While set, every patch change is mirrored into that
        # track (mirror_patch_to_track, run after every route).
        self.song_edit_track: str | None = None
        self._edit_saved: dict | None = None  # the loop's patch/undo/slot_prev
        # runtime only: the saved loops (studio_data/loops/) the grids are
        # editing, ONE A LANE (Perform's launcher) — {lane: {name, layer,
        # mark, base, row, bars}}: every
        # request after which a lane differs from its `base` writes that
        # loop back (`mirror_loop_edit`). Empty: the grids are the working
        # loop's alone (state/history.py). A slot of the launcher that plays
        # is the one open on its lane (`row`)
        self.loop_edit: dict[int, dict] = {}
        # runtime only: Perform's loop coach (loopcoach.py) — the proposal
        # waiting and the thread (messages, replies, accepted changes with
        # their undo steps). None until the coach is first used
        self.coach: dict | None = None
        # runtime only, a listening setting: what Synths' ▶ plays while it
        # edits a synth track — "alone", the block's pattern on its own
        # ("Solo this cell", the default: the pattern clicked, no other
        # lane), or "mix", the section as the song plays it; both at the
        # song's own level (`StudioService.track_heard`)
        self.song_edit_hear: str = "alone"
        # runtime only: what the Song page's drawer has open — a cell
        # ({section_id, track_id}: it follows whatever pattern the cell
        # holds) or a pool pattern ({pattern_id}); None when closed. Ids
        # that have gone just close it (StudioService.song_context).
        self.song_drawer: dict | None = None
        # runtime only, a view setting: the piano roll's octave window on
        # the working loop, whole octaves from the root (`pitch_rows`).
        # Not persisted: a saved session has no key for it, so it opens at
        # the root (the drawer keeps its own in `song_drawer["oct"]`).
        self.grid_oct: int = 0
        # note recording's undo (runtime, like the drawer): one entry per
        # take, newest last — {take, pattern_id, before, cell} (routes/song.py)
        self.take_history: list[dict] = []
        # last Randomise settings (session-only, shown in the bank form)
        self.beat_style: str = "four-floor"
        self.beat_seed: int = 1
        # the working beat's variations A-D on Drums and Perform (session-
        # only, like the Randomise settings; "Save to slot" keeps one): the
        # drum lanes set aside by letter, and the letter the grid holds
        # (`DrumBank.beat_variation`). Emptied when the beat is replaced.
        self.beat_vars: dict[str, dict] = {}
        self.beat_var: str = "A"
        # the last generator that wrote the beat, and the grid it left
        # (`DrumBank.gen_step`): rolling the same one again on that grid is
        # the same pattern undo step
        self.beat_gen: tuple[str, dict] | None = None
        # The one studio lock: every request holds it across its
        # mutation *and* its render (routing.StudioRoute), and save()
        # holds it while it snapshots. Re-entrant because handlers call
        # save() while already holding it. `_save_lock` only guards the
        # debounce timer, and is never held while waiting for this one.
        self.lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._save_timer: threading.Timer | None = None
        # a save was asked for and state.json does not hold it yet: set by
        # save()/save_soon(), cleared only by a write that landed, so a
        # failed one stays pending for the retry and for flush()
        self._unsaved = False
        # how many saves were asked for (save / save_soon): a request that
        # moved it committed something, which is what the route wrapper
        # announces to the pages open elsewhere (`StudioService.announce`)
        self.edits = 0
        # how many times the breed board was replaced under whoever was
        # working from it: a loaded session (`_apply`) or the board scope
        # reverted / reset (`_scope_set`). A generation renders outside
        # the lock and commits when done (routes/breed.py): it reads this
        # before and after, and drops a litter bred for a board that is
        # gone. Runtime only; an ordinary edit, star or save never moves it.
        self.board_epoch = 0
        # is_dirty's answers while nothing can change (`dirty_memo`)
        self._dirty_memo: dict[str, bool] | None = None
        # the sampler's content-addressed store (synthlab/sampler.py): the
        # engine finds a sampler sound's audio here by the Patch's hash
        self.sample_dir = sampler.add_dir(self.data_dir / "samples")
        # your own wavetables' store (synthlab/wavetables.py), the same way
        self.wavetable_dir = wavetables.add_dir(self.data_dir / "wavetables")
        self._load()
