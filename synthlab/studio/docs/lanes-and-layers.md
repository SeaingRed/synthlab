# Lanes and layers

What each synth lane plays in a loop and in a song section: per-lane
velocities / slides / arp, following layers, the main lane,
`loop_mix` (mute, solo, focus), the layer strip, and layers inside
sections. How a slot's belongings are stored is in `state.md`
("Layers"). Index: `README.md`.

## Every synth lane plays its own sequencing

**Every synth lane plays its own sequencing** — main or layer, loop or
song section: its velocities, slides and arp (curves: main lane only).
- A layer's render dict carries them as `velocities` / `slides` /
  `arp_mode` + `arp_octaves` (and, in a tiled section cycle, `arp` =
  [pitches, start_step, period], like `SectionParts.arp`), written by
  one function, `song_bridge.lane_render`, and only when audible:
  velocities at the steps the layer reads (every step under an arp,
  else its note steps) that differ from the main lane's; slides on its
  note steps; the arp when on. So a plain layer's dict — and every
  loop, scene and migrated section key — is byte-identical to before.
- `arrange.build_song` reads them via `layer_arrangement`; a layer with
  an arp or slides goes through the main lane's `_lane_timeline` (arp
  sequence, slides, swing, velocities, breaks, the `arp` tuple), any
  other plays per row exactly as it always did.

### Velocities: absent means the main lane's

**Velocities: absent means the main lane's.** A layer dict without
`velocities`, a slot never stashed (`seq` None, an old save) and a
pool pattern with an empty velocity lane all play the main lane's —
the legacy render every migrated scene's key depends on. A stashed
slot's empty lane is its own (all 0.9), so `layer_note_data` writes
it into its pattern as explicit 0.9s where the main lane's would
differ, and `to_loop` gives a slot from an empty-lane pattern a copy
of the main lane's velocities. A slot never stashed also plays no arp
or slides (it takes over the live ones when first selected).

### Following layers

**Following layers** (`{"follow": {"octave": -2..+1}}`, optional
`role`: sub/pad/pluck/air — a bred Sound's layers, `studio/genome.py`,
or any slot whose strip Follow is on) ignore their own cells (kept,
for Follow off): they play the main lane's notes moved by
the octave, with its velocities, slides (on their note steps) and arp.
`arrange.follow_layer` resolves one into a plain layer dict at render
time — `_stem_jobs` does it before building the stem, so the stem's
cache payload carries the notes it plays and a main-grid edit
re-renders it; `build_song` does it too for direct callers. Both keys
are optional in `schema` (`drop_unset`): no older layer, preset or
render key gains one. `other_layers` counts a follower as sounding
(`sounds_in_loop`). `_loop_lanes` hands the song bridge materialised
copies (`state.materialise`, same render) — patterns hold notes, so a
section → loop round trip brings back plain layers. No state-version
bump: an older build reading a follower drops `follow` (unknown key)
and plays the slot's own cells there.

### The main lane

**The main lane** (`state.main_layer`, schema'd, `main_index()`) is
the slot followers follow. None (every older session) means the
active slot; `select_layer`, `set_follow` and `add_layer` pin it
before anything moves, so **selecting a layer never moves the main
lane or detaches a follower** (materialising every follower would leave
the stack on Synths silent). The main lane never
follows (the follow route refuses it with a toast; `main_index` skips
a following slot). **A following slot being edited keeps following**:
`_lane_over` gives the edited lane the main lane's notes at its octave
(`lane_arrangement()` in the loop; Perform's transport shows a
`lane-follow-note` saying its own grid isn't what plays).
`main_arrangement()` is the main slot's cells + `seq` on the loop's
clock when it isn't the edited one. Loads that replace the other lanes
(a v2 preset / layered card with layers, `section_to_loop`) make the
edited slot the main again (`_make_active_main`). **The main lane's
notes never leave with it**: clearing the main slot (✕) hands them to
a follower — the edited one first, else the first with a sound — which
takes what it was playing as its own cells, stops following and is
pinned as the main lane; other followers' octaves are re-based on it
(`_inherit_main`). A Sound loaded with its layers into an edited
follower keeps them the same way. Neither leaves an empty grid as
the main lane: a silent loop. With no follower, clearing un-pins it.

### `loop_mix(src, bench=)`: the one mix rule; mute and solo

**`loop_mix(src, bench=)`** is the one mix rule, used by
`render_inputs`: `src` is the main lane's notes — `main_arrangement()`
in a loop, **the reference phrase on a bench view**, where the edited
lane plays the phrase (at its octave when it follows) *and so does the
main lane when another slot is edited*. Other plain slots play their
own cells — on a bench view, one with none plays the phrase (as the
main lane does there; `_render_slot`, `lanes`). When the main is the edited lane, followers keep the lazy
render-time path (render dicts and keys byte-identical to before);
otherwise they are resolved against `src` up front (`_render_slot`).
**Mute and solo** (`audible`): while any slot has `solo` only soloed
slots are heard; mute wins over solo. `solo` is a slot key (schema
`Opt(Bool)`, absent unless set, in `_LAYER_STATE_KEYS`: never render
input, never in a preset, scene or the song — monitoring).
`other_layers()` (presets, direct renders) ignores solo. Who is heard
is `StudioState.lanes` (via `audible_layers(state, view)`, which adds
"This layer" on bench views) — the one predicate for ▶ *and* the live
keys (`for_keys`: the key stands in for the main lane's notes, so
plain slots on their own patterns drop out; see "Live keys sound like
the loop" in `live-voice.md`).

### Layer strip

**Layer strip** (`fragments/layer_strip.html`, region `layer-strip`,
`svc.layer_context()`, outside `#sound-bar` because that form posts
on every input; morphed OOB so a clicked toggle keeps focus): one row
per slot — number + name (click = edit it; the edited row is amber
with "✎ editing" and `aria-current`), MAIN / role badge and what it
plays ("follows main −1 oct", "own notes (3)"), a speaker that says
whether ▶ hears it (its label says why not: muted / another layer is
soloed / ▶ is set to This layer; a slot with a sound and no notes of
its own plays the phrase on Synths — "plays the phrase (no notes of its
own)" — silent there, "All layers" would hear two of
three), level (`/fragment/layer/gain`, `change`; suppresses its own
strip), M, S (`/fragment/layer/solo`), Follow + octave
(`/fragment/layer/follow`, `on`/`octave`), ✕ (`/fragment/layer/clear`;
no confirm — the user asked for it gone. On the edited slot the panels
move to the nearest slot with a sound first and the whole page comes
back; only the one sound there is cannot be cleared). ＋ on a sound in
the list (`/fragment/layer/add` with a `source`) is never refused: with
every slot full, or while a song track's sound is edited, it loads over
the sound being edited (`svc.adopt`). Four
slots (`limits.LAYER_SLOTS`; the schema's `layers` pads a shorter
save rather than resetting it). Header: "▶ hears" All layers / This layer
(`/fragment/layer/focus`, `state.layer_focus`, persisted: a bench
render — and a card's "in your mix", so it still equals Use's landing
— drops the other layers and unmutes the edited one; loop views are
untouched. A load that brings its own layers — "Use with its layers",
a v2 preset — turns it off, and that Sound's "in your mix" keeps its
layers: left on, it silently swallowed the lanes just picked) and
"+ Add layer" (`/fragment/layer/add`: a copy of the edited sound, or
`source`+`name` — the dock's ＋ on preset/factory cards, the
Library's "＋ Layer", which lands on Synths — into the first free
slot, following at ±0, and selected; a toast when every slot holds
a sound. **`own=1`** — Perform's lane heads (`loop-and-perform.md`,
"The lane heads"): the new slot does NOT follow — it has its own empty
grid and sounds as soon as a note is drawn — and the page stays where
it is. Perform also picks the edited lane (`/fragment/layer/select`)
and turns a follower's Follow off without leaving the page).

**On Perform the slots are the lane heads** (`loop-and-perform.md`,
"The lane heads"): the same slots, one
head each — its name selects it (`/fragment/layer/select`), M / S are
this strip's routes, and ▾ puts any sound in it (`POST
/fragment/perform/lane/sound`, `StudioState.lane_sound`: the lane
becomes the edited one, one pattern undo step holding the whole loop).
**A load says what it replaces** (`StudioState.load_sound_as`, `LOAD_AS`):
"" the plain load, `layer` the edited slot's sound alone (a
layered Sound's layers stay behind), `whole` the stack (its layers, or
the other slots emptied), `add` a free slot following at ±0. A card's
Load on Synths asks it too, whenever the strip holds more than one
sound (`data-stack` on the strip) or the card brings layers
(`data-layered`; `sounds.yours` reads each saved file's layers once a
version) — otherwise loading "Acid Alley" after adding "Nebula
Sweep" as layer 2 would silently swap Nebula out.

### Sections

Sections: `section_arrangement` adds each sounding layer's
`pattern_seq` (regridded like its cells); same-grid sections compare
velocities with the main lane's, tiled ones always carry them;
`_tile_layer` / `_crop_layer` tile and crop `velocities`, `slides`
and `arp` exactly as `_flat` / `window` do the main lane's.
