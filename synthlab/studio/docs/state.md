# Session state, schema, reset and project files

The `state/` package (`StudioState` and its mixins: which module owns
what), `schema.py` (the one definition of every persisted value),
state-file compatibility and migration, reset / revert scopes, and
project export / import. The audio bundle (`/project.zip`) is in
`sampler-and-clips.md`; preset format 2 in `sounds-and-breeding.md`.
Index: `README.md`.

## `state/`: the session package

`state/` — the session, a package. `StudioState` is the one object and
`synthlab.studio.state` the one import for every name; the class
is a constructor (`state/__init__.py`, which declares every field) plus
one mixin per concern:
`model.py` (no `StudioState`: `Arrangement`, `RESET_SCOPES`, the layer /
lane helpers `render_layer` / `materialise` / `Lane`, the validation
entry points onto `schema`, the clip-reference traversal),
`persist.py` (`Persistence`: load / `_migrate` / `_apply` /
`_apply_or_restore`, `_payload`, project export and import,
`rename_clip_refs`, `save` / `save_soon` / `flush` and their retries),
`layers.py` (`Layers`: `select_layer` and the per-slot store, the main
lane, followers, `lanes` / `loop_mix`, the strip's actions),
`history.py` (`History`: the patch undo ring — whose steps are the patch
or, `push_fx_undo`, one session FX rack's chain before an edit
(`synths.md`, "Undo of a rack edit") — `stash_for_load`'s step,
the pattern undo ring, the song's undo / redo ring), `song.py` (`SongBridge`: `loop_to_section`,
`section_to_loop`, `apply_pool_beat`, the track-sound edit),
`drums.py` (`DrumBank`: the bank and the pattern library files),
`presets.py` (`Presets`: preset and panel-preset files, `load_sound`,
"before last load" `stash_prev` / `swap_prev`), `scopes.py` (`Scopes`:
baselines, `revert`, `reset_defaults`, `is_dirty`), `slots.py` (`Slots`:
Perform's launcher — its grid, launching a slot or a scene).

### Mixins, not composed objects, on purpose

**Mixins, not composed objects, on purpose**: `_apply_or_restore` rolls
a failed load back by restoring `self.__dict__`, so every field has to
live on the one instance (and be replaced whole, never mutated in
place, by `_apply`); and a slot's belongings *are* the live fields while
it is edited. What keeps the units apart instead is checked by
`tests/studio/test_state_split.py`: `OWNERS` (`state/__init__.py`) lists,
per runtime field, the modules allowed to touch it — `_save_lock` /
`_save_timer` / `_unsaved` are `persist`'s alone, `_dirty_memo`
`scopes`'s; the edited slot's ring and stash (`_history`, `slot_prev`,
`stack_prev`, `_layer_undo`) name each module that hands them over;
the song's ring (`_song_undo`, `_song_redo`, `_song_undo_at`,
`_song_undo_head`, `_song_undo_sound`) is `history`'s alone, on
purpose: instead of every unit that replaces the song emptying it (as
`persist` and `scopes` each empty `take_history`), the ring remembers
which song object its steps lead to and empties itself when
`state.song` is another one (`song.md`, "Undo and redo") —
and each mixin declares, as bare annotations at the top of its class,
exactly the session fields its code reaches. **A new field goes in the
constructor, in `OWNERS` if it is runtime state, and in the annotations
of each unit that uses it; a new method goes in a unit, never in
`__init__.py`.** `monotonic` and `preset_meta` are read through the
package at call time (`history.py`, `presets.py`), because tests patch
them as `synthlab.studio.state.<name>`; the logger is
`synthlab.studio.state`. The same test pins the saved bytes of a rich
session and the importable surface (`surface.json`, a frozen snapshot;
the bytes are the conformed form: "What is written is the conformed
form", below).
`StudioState` persists to `state.json` (atomic writes,
corrupt-file-tolerant). Live-tweak saves are debounced (`save_soon`); the
app's lifespan hook flushes on shutdown, so persistence tests must use
`with TestClient(app) as client:` to trigger it.

### Layers: everything a slot owns travels with it

**Layers**: the active
layer's patch/cells live in `s.patch`/`s.arrangement.cells`; inactive layers
are stashed dicts, swapped by `select_layer`. **Everything a slot owns
travels with it**: its velocities/slides/arp
(`LAYER_SEQ_KEYS`, stashed as the layer's `seq`) and its `curves` swap
with the cells, and — runtime only, `_layer_undo` — its undo ring, its
"before last load" stash (`slot_prev`/`stack_prev`: state.json keeps the
edited slot's only) and its
`LAYER_SCOPES` (sound/fx/sequence) baselines, so undo/swap-back/revert never reach
another slot and a switch alone never lights "changed". A slot never
stashed (`seq`/`curves` None) takes over the live values (velocities and
slides only when it has notes). A layer dict's `seq`/`curves`/`silent`
are loop state, never render input: `other_layers()` hands the renderer
`render_layer(lay, arr)`, which turns `seq` into render keys (`lanes-and-layers.md`), so
*which* slot is active is inaudible
(`test_switching_the_active_layer_never_changes_the_loop`). Curves stay
the edited lane's only — deliberately: a layer ignores them, in a loop
as in a song. `silent` = muted by the
song's mix (a solo elsewhere), not by the slot's own mute: `own_mute`
(song_bridge) is what a lane carries back into the song, and the layer
strip's mute (`set_layer_muted`) clears it. `_loop_lanes` includes muted
layers that have notes, so a round trip keeps their tracks. Loads
(`stash_for_load`: A/B switches, panel presets, `section_to_loop`) stash
`slot_prev` *and* push one undo step; panel reset pushes one too; a
project import or a section open clears the rings they replace.

### A track edit owns the track's whole sound

**A saved loop open on the grid** (`loop-and-perform.md`,
"Loops"): `loop_edit` (runtime, `OWNERS`: history, song, persist) is
`{name, layer, mark, base}` while Perform's note grid edits a loop of
`studio_data/loops/`. `History.open_loop_edit` sets it once the loop is
on the grid and its pattern undo step pushed (`mark`: that step);
`mirror_loop_edit` (routing, after every request) writes
`LOOP_FIELDS` back to the loop when they moved off `base`;
`pop_pattern_undo` popping the mark, `section_to_loop`, a loaded session
and Done end it. A loop file is conformed by `schema.nodes().LOOP`
(`take`, `played`, `quantise`, `edited` optional: an older file without
them reads with their defaults).

**One a lane**: `loop_edit` is
`{lane: {name, layer, mark, base, row, bars}}` — a lane a layer slot or
the drums (`DRUM_LANE`), `row` the launcher's row it plays from (None:
Edit), `bars` its own length. `lane_state` reads a lane wherever it is
(the edited slot's from the arrangement, another's from its stash; the
drums' `DRUM_LOOP_FIELDS`); `mirror_loop_edit` writes each lane that moved
and no other (an Edit's tempo, key and meter too; a beat's `beat`);
`loop_edit_of`, `loop_open`, `end_loop_edit(lane)`, `end_loop_edits(name)`,
`rename_loop_edits` are its API — no unit but history, song and persist
touches the field.

**Perform's launcher** (`state/slots.py`, the `Slots` mixin, the eighth
unit; `loop-and-perform.md`, "Slots and scenes"): `perform_slots` is a
plain persisted key — `schema.PAYLOAD`'s first **absent when unset**
(`schema.PAYLOAD_OPTIONAL`; the payload's `_payload_post` drops it, and
the rows past the last that holds anything, so no session that never
used it gains a key and the pinned bytes stand; `_apply` reads an absent
one as its default) — and in the "Sequence" reset scope, left out of a
snapshot while unset (`scopes._OPTIONAL`). A launch is a whole-loop
pattern step: `_whole_loop` holds the launcher's rows and `perform_parts`
too. No migration: a file from before reads as an empty launcher.

**A track edit owns the track's whole sound**: `edit_track` sets aside
the loop's patch, ring, "before last load" stash (`slot_prev` *and*
`stack_prev`: one stash, given back together), curves, active-layer FX
and sound/fx baselines, puts the track's patch/curves/FX live (baselined
as the track),
`mirror_patch_to_track` copies all three back, and `_payload` persists the
loop's own set mid-edit.

## `schema.py`: the one definition of every persisted value

`schema.py` — **the one definition of every persisted value.** state.json,
an imported project, a stored baseline (revert/reset via `_scope_set`), a
scene and a preset file all go through `conform(value, node)`. It checks
the container type first, then recurses (only as deep as the schema), then
rejects non-finite numbers and bools used as numbers, clamps ranges, looks
up enums hashable-safely, and dedupes and caps lists (`limits.py`).
`_apply` conforms the whole payload *before* assigning anything. `_load`
and `/project/import` catch `Exception`, so neither startup nor an import
depends on predicting an exception type. **A new persisted key or
`Arrangement` field needs a schema node**: `test_schema.py` fails if one
is missing, then fuzzes every node automatically. **An Arrangement field
added after the pinned states is OPTIONAL: absent when unset wherever an
arrangement is written down** — `schema.ARR_OPTIONAL` (`drum_hits`,
`drum_row_swing`, `strum`, `note_nudge` …: "unset" is the field's own falsy default, an
empty table or 0; a scalar one gives its `Num` an `example`, the value
the schema tests' fixed point writes down): the Arrangement node's post
drops an empty one (and
its default carries none), `schema.arr_dict(arr)` is `asdict` without
them (the session payload and `RenderInputs.key()` use it, never
`asdict`), and a reset scope's snapshot leaves an unset one out (revert
puts it back to unset). So no saved state, baseline, key or pinned
fixture gains a key (`tests/studio/test_drum_hits.py`). The beat's
variations (`beat_vars`, `beat_var`) and the last generator
(`beat_gen`) are session-only runtime fields of `state/drums.py`, like
`beat_style`: in no payload. Keep domain rules as
leaves (`params.clamp`, drumlab specs, `clean_chain`, `CURVABLE`) rather
than re-deriving them there.

**An optional field whose unset value is not falsy**: the
meter, unset at "4/4" — `schema.ARR_NEUTRAL` names it, `arr_unset` is the
one test (`arr_dict`, `arr_post`, `song_bridge.section_loop`,
`scopes.snapshot`). The record's post holds `steps_per_bar` /
`drum_steps` to the meter's step choices (`_fit_steps`; a count no 4/4
grid has is the field's default). A song's `meter`, a
section's `meter` / `bpm` / `ramp` and a pattern's `meter` are absent
when unset or equal to the song's.

### What is written is the conformed form

**`_payload()` is `conform(_live_payload())`** (`state/persist.py`):
state.json and a project export hold the session exactly as
a load of them would, so `save -> load -> save` writes the same bytes
**from the first save**. Otherwise the first save of many sessions would
not be in that form and the file would change on a restart with no edit: a layer
a Sound brought (`load_sound`, a v2 preset, a recipe, a layered breed
card — `LAYERS_OTHER` is a render lane, not a slot) has no `silent` /
`seq` / `curves`; `follow`, `solo`, `role` and `sound_fx` are written
into a slot's dict after its other keys (`add_layer`, the strip's
toggles); "before last load" (`_stack_snapshot`, the agent's end of
turn) keeps copies of both.

**Why there and not where state is mutated**: what real paths leave
unconformed is *absent keys that equal their default* and *key order*.
Neither is visible to anything that reads the live session — render keys
take a slot through `render_layer` (which drops the loop-state keys) and
hash with `sort_keys`; `is_dirty` compares scope values — so it shows
only in the file, and the file's form is a property of the one
function that writes it rather than of every route that touches a slot
(routes mutate slots in place: `lay["follow"] = ...`,
`chain[:] = clean_chain(chain)`). **Saving never touches the live
session**: no object is replaced, so no render key of a running session
moves because it was saved, and no session is "changed" differently.
A save never fails for it: if the schema raised (it does not for any
session the suite can build), the session is written as it stands.

**One thing a render key *can* see** is an FX unit without all its
parameters: chains are hashed as they stand. Every production path
already cleans a chain where it enters (`clean_chain`: the rack's
routes, the agent's `set_fx_chain` / `add_layer`, presets and breed
cards through the schema); `add_layer` conforms the chain it is
handed too, beside the gain. Code that writes a
partial unit straight into a slot (`s.layers[i]["fx"] = [{"kind":
"delay", "params": {"mix": 0.2}}]`, as the split's fixture builder does)
still keys the loop differently before a restart than after; its file
is conformed all the same. **Clean a chain where you build it.**

Pinned by `tests/studio/test_state_conformed.py`: eleven ways of
building a session (Sounds with layers, a saved stack, the layer strip,
a hand-built FX unit through `add_layer`, the FX rack, breed cards, the
agent's tools and its end-of-turn stash, a Sample Lab region, the song
bridges, resets, a hand-written project import) — the first save is its
own conformed form, a second save and two restarts write the same
bytes, and every view's loop key, the song plan's URLs and every
scope's "changed" flag are the same before and after the restart.
**An older file not in the conformed form** loads as it stands and is
written conformed by the first save after, once
(`tests/fixtures/state_split/legacy_first_save*.json`).

## State compatibility

**State compatibility**: `Patch.from_dict` (`synth.py`) drops unknown
keys with a warning instead of raising, so a field renamed or removed
since a preset/state file was written loads as defaults for that field
rather than refusing the whole file. `STATE_VERSION` (`state/model.py`)
and `_migrate` (`state/persist.py`) are where a field rename or restructure gets its migration
logic. v3 is the first real one: v1 (no `"v"` key) and v2 held
`scenes` + a scene running order (`song` as a list); `_migrate` runs
`song_bridge.migrate_v2` on load and on project import, which builds the
song model and drops both, and `_load` keeps the old file as
`state.json.v2.bak` (once). The migration is pinned against real v2
payloads frozen in `tests/fixtures/legacy_states/`
(`test_song_migration.py`), including each scene's `RenderInputs.key()`.
A `state.json` that fails to load is copied aside
(`state.json.corrupt-<timestamp>`) before the session falls back to
defaults, so a corrupt file is never silently overwritten by the next
save. `_apply_or_restore` snapshots `self.__dict__` first and restores
it if `_apply` raises partway through, so a rejected import or a
mid-load failure never leaves the session half-applied (old patch,
new arrangement). The rollback leaves the save's own fields
(`Persistence._SAVE_FIELDS`: `_save_lock`, `_save_timer`, `_unsaved` —
the ones `OWNERS` gives to `persist` alone) as they are at that moment:
the debounce timer can fire during a failing import, and a restored,
already-fired timer read as a save still pending.

## Reset / revert

**Reset / revert** (`state/scopes.py`; `RESET_SCOPES` / `RESET_VIEWS`,
`fragments/reset_menu.html`): every view header carries a `.head-tools`
reset menu. A *scope* is a list of keys (`patch`, `curves`, `layer_fx`,
`arr.<field>`, `state.<attr>`) that exactly one scope may own — the
disjointness is pinned by a test. `mark_baseline(scope, label)` fires
from inside `StudioState` on every load/save (`load_preset`,
`save_preset` — both mark `fx` too for a format-2 file, which carries the
main-lane FX — `apply_pattern`, `bank_save`, `section_to_loop`,
`edit_track`, a first visit to a layer slot) plus every Use
(`load_sound`, via `svc.adopt`); the `kit` scope owns `arr.drum_samples` with the
params and mixer; `revert` restores that snapshot, `reset_defaults`
writes factory values *and* rebaselines. Baselines persist in
`state.json`; a session without them baselines itself as "as opened".
`POST /fragment/reset` (scope + mode, `all` = every scope the view owns)
answers with `full_view_page` — never `full_page`, which would bounce you
onto the editor. The menu is a region on every page, re-sent **OOB**
whenever an edit moves a dot or a label, so the "changed"
dots stay honest even though edits post with `hx-swap="none"`.

## Project export/import

Project export/import: `GET /project.json` is `StudioState.project_payload()`
(the `save()` dict tagged `format: synthlab-project`); `POST /project/import`
runs it through the same `_apply` parser as `_load`, then resets every
render cache (`svc.reset_caches()`: loops, notes, stems, the note memo and
the song's levels and gain — all keyed by content, where a sample is only
its id, so audio that arrives after its project must not meet renders made
without it).
From htmx it answers 200 + `HX-Refresh: true` (a 303 + `HX-Redirect`
would be followed by fetch itself, so htmx would never see the header and
the page would keep the old state); a plain form post still gets the 303.
