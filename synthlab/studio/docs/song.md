# Song: model, contracts, page

The song model beside the working loop, the Song contracts (model,
bridges, recording, risers, automation lanes — on the level, a sound's
fields and an FX unit's parameters — whole stems, block spans) and the
Song page (the playlist: a bar ruler over time-true lanes, zoom, seek, the
edges; pool, drawer, pattern making, drags onto a lane, the shared
step-grid macros), the song's undo ring and the drawer's solo audition.
Section rendering, playback, export and MIDI are in
`song-render-export.md`.
Index: `README.md`.

## Song model beside the working loop

**Song model beside the working loop** (`state.song`, `song_model.py`,
`song_bridge.py`): the loop (patch/arrangement/layers) is the only
render input for the other views. A section's main lane is its
first synth track (song order) with a pattern; the other lanes are
layers — `place_lanes` keeps a loop's main lane first when it adds one.
`state.song_edit_track` (runtime only) marks a synth track whose sound
Synths is editing; `routing._under_state_lock` calls
`mirror_patch_to_track()` after every route, the one place every patch
mutation passes through.

**The sound dock** (`fragments/song_dock.html`, the region
`song-dock`, `static/songdock.js`): a row's ✎ posts
`/song/track/{id}/edit-sound` with `dock=1` and stays on the Song page —
a bar across the bottom of the view holds **Synths itself, in a frame**
(`/view/editor`), editing that track; the track's name still goes to
the Synths page. The bar names no grid area: it spans every column of a
row after the view's own (`grid-auto-rows`), half the view until its
handle is dragged (`--sdock-h`, layout.js), and what is above gives way
(dense.css D18). **A lane's sound menu** (the `<select>` in a synth
track's head, `POST /song/track/{id}/sound`, `sound=<source>:<name>` as
`sounds.find` knows it; the list is `sounds.menu`: yours,
factory, recipes, the core library's categories, the library's families
— on the page ONCE, as `<template id="lane-sounds">`
(`fragments/sound_menu.html`, base.html's: Perform's
lane heads search the same list), and copied into a menu by
soundmenu.js as it is reached for: ~150 options a lane inside the
arranger's region were redrawn with every edit) swaps the track's patch in
one undo step — a recipe or a saved format-2 sound brings its FX and
curves, a plain patch leaves the lane's FX — and the notes, level and
mix stay. The dock follows a sound picked for the track it holds. The
editor drawer's head picks the same track sound, searchable and
browsing ("The drawer's sound: change it while it plays"). So:
- **the edit belongs to Synths and the Song page**: a request from any
  *other* view ends it (`routing._track_edit_before`), the dock's ✕ does
  (`POST /song/dock/close`), and a Song page loaded while an edit runs
  shows the dock. After a request from the Song page the working sound
  follows the track (`state.follow_track_edit`: the song's undo, a new
  song), or the next mirror would put the undone edit back;
- **a framed page drops its shell**: `Sec-Fetch-Dest: iframe` ->
  `<html data-embed>` (`service/page.py`), which hides the rail, the top
  dock and the banner's "back to Song". Framing is same-origin only
  (`security.CSP` `frame-ancestors 'self'`, `X-Frame-Options:
  SAMEORIGIN`);
- **the playing song follows mid-pass**: songdock.js hears every request
  the framed page finishes and dispatches `song-changed` with `{now}`;
  song.js re-reads the plan and `swapNow` crossfades (40 ms) the PLAYING
  pass into the edited one at the same position. Any other edit still
  waits for the next pass start. Measured in headless Chromium on a
  283 s song: about 5 s from the change to the swap (a track under a
  phaser; the plan's render is most of it). The framed Synths' own ▶ is
  immediate, and so are the keys — the Song page's own keyboard dock
  (every page's: `keys-and-midi.md`, "The keyboard
  dock"), which re-reads the edited sound on the frame's `song-changed`;
  the frame's is there but not drawn, so typing into the frame still
  plays — and play beside the song;
- its watch is the track alone (`_w_track_edit`), so nothing else
  redraws the frame; a whole-view refetch morphs round it (checked).
**The dock is where a lane is edited first**: a lane's name
and its ✎ open it; the whole page is the other choice — "Open on the
Synths page ↗" / "Open on the Drum Lab page ↗" in the track's ⋯ menu,
and "↗" on the bar. While it is open it **follows what is reached for**:
a cell or a pool pattern opened in the drawer moves it to that lane
(`routes/song.py` `_dock_follows`: a pattern's first lane in song
order; an audio lane leaves it where it was). Closed, nothing opens it.

**The drums track's kit** is the same edit made from the Drum Lab
(`state.edit_track` on a drums track -> `_edit_kit`): the loop's kit
(`song_bridge.KIT`: `drum_params` / `drum_mix` / `drum_samples`) and
`fx_drums` are set aside in `_edit_saved` (`kit`), the track's stand in
their place, `track_edit_view()` says "drums", and a request from the
Drums view mirrors them into the track (`_kit_sound`). The saved
session keeps the loop's own (`persist._live_payload`), the song's undo
ring takes the kit as one step (`history._TRACK_SOUND`), "Add loop to
song" and "Make this loop a song" end the edit first (the loop's OWN
sound and kit go in). **The Drum Lab's grid and bank stay the loop's
beat** — a song's beats are its patterns — and the banner there says so.

**Another tab does not close it**: a second tab of the studio refreshes
itself at every change notice (its view, a preview), so ending the edit
on any request from another view would close the dock by itself. The
page that opened the edit is remembered
(`_edit_saved["page"]`, `events.ORIGIN_HEADER`) and a read or the
preview's render from ANY OTHER page leaves the edit alone
(`routing._track_edit_before`, `_looks_on`); a change made from another
view, and the opening page going elsewhere, end it.

**▶ in the dock hears the song** (`service/song.py`
`track_heard`, through `LoopRender.heard` — `do_render` and
`preview_trigger` only, so the agent's measure, a bounce and a resample
keep the sound's own render): one loop of **the block last opened in
the drawer** — a cell of this lane, or the first section where the
drawer's pool pattern plays on it — else the first section that plays
the track (`song_bridge.heard_section`), at the song's level
(`song_head_gain`): **this pattern** alone (`state.song_edit_hear`
"alone", the default — `/song/cell/..wav`'s render, "Solo this cell":
the pattern clicked and no other lane) or **in the mix** ("mix":
`/song/section/{id}.wav?loop=1`'s render, every track, the mixer, the
master chain). The drawer's answer carries `dock-reheard` while the dock
holds a synth lane, and songdock.js has the frame render again (a plain
fetch of `/fragment/preview` from the frame, so it is not taken for an
edit), so the frame's next ▶ is the block just opened. No second render path; cached under the section's key +
":meter" so Synths' readouts are measured. It renders a copy of the
song holding the working sound (`working_track_sound`) and never writes
the track: routing mirrors, after the handler and only for a request from
Synths (mirroring here would let a request from nowhere write the
track — `test_patch_edits_on_synths_mirror_into_the_track`). The track
alone at unity gain would sit ~18 dB over the song (measured on a
trance song's pad: −14.8 dB rms vs −32.9 in the song) and not sound the
same. While a synth track is edited, the sound
bar shows **Hear** (the radios ride the bar's post, `edit_hear` in
`fragment_arrangement`) and the song's tempo and key in place of the
loop's phrase, bpm and key, which ▶ does not play (the loop's root and
scale ride along hidden: an absent key would reset; `#t-bpm` carries the
song's tempo unnamed, for audio.js's bar-line swap); `sound_id` names the
track instead of "Layer n of 4"; the layer strip is one line (the slots
are the loop's, and picking one ends the edit). A track no section plays
keeps the phrase, in the song's key (`render_inputs`). Pinned by
`test_play_on_synths_hears_the_track_at_its_level_in_the_song`.

**Not built**: the Mix view (FX, returns) and the Voice lab in the bar,
and the lane played by the live voice in time with the song (the
real-time path).

## Song contracts, in one place

**Song contracts, in one place**:

### Model

**Model**: `state.song` = song-wide bpm/key/groove, tracks (drums ≤ 1,
synth ≤ 8, audio ≤ 3; the sound lives on the
track, and so may a `role`: `songcraft.md`), a pattern pool
shared by reference (the notes live in the pattern; `make_unique` /
`variation` fork one cell), sections (bars, repeats, `cells`
{track: pattern}, `audio` takes at 0-based `at_bar`, and — only when a
block is trimmed — `spans`, "Block spans" below), and — only when
the song holds one — `long`, whole stems on the timeline ("Long audio"
below). Every node is in `schema.SONG` with caps in `limits.py`.

**Song creation's keys** (`songcraft.md`): the song's `title`,
`chords` (the chord track), `gen` (how it was started) and `returns`; a
track's `role`, `pan`, `sends`; a section's `energy` and its own
`chords`; a pattern's `gen`, `fam`, `var`, and `strum` (notes) or `hits`
/ `swing` (drums). **All optional and absent when unset** (a post hook
takes each out again), so no song saved before them gains a key and
none is a render input: `section_key` and the audio of a song that
sets them are the song's without them
(`tests/studio/test_song_foundations.py`). Each is read where it
matters (`strum`: a pattern's own, `loop-and-perform.md`, "The rule" — a section's key moves
with it and stays put without it).

**A pool pattern may be 8 bars** while bars x steps a bar fits
`MAX_GRID_STEPS` (`limits.PATTERN_BAR_CHOICES`, `sm.bar_choices`: 8
bars at 4, 8 or 16 steps). The loop keeps `BAR_CHOICES`: the
Arrangement's own node would call 8 bars invalid, so
`section_arrangement` carries them round it, and "Open in Perform"
opens a longer pattern as its first four bars (the rest stays, hidden
past the loop's grid). The drawer offers 8 only where it fits, and
finer steps shorten an 8-bar pattern to 4.

### Bridges

**Bridges**: `loop_to_section` ("Add loop to song"), `section_to_loop`
("Open in Perform", Perform's section launcher) and the track-sound
edit (`edit_track` / `mirror_patch_to_track`). The Song page's drawer
edits pool patterns by id and never touches the working loop.
`section_to_loop` runs `section_loop` (the render bridge)
then `song_bridge.to_loop`, which turns the pieces into loop state:
a layer whose pattern has other bars/steps than the main lane's is
regridded (`song_model._regrid`) and every lane tiled onto one grid of
lcm(lane bars) (capped at 4) — the loop has one step grid; each layer
gets its pattern's `seq`, its track's `curves`, and `silent` when only
the mix muted it. `loop_to_section` writes a layer's `seq` back into
its pattern (`layer_note_data`).

**A saved loop → Song** (the Loops list's **→ Song** ▸
pattern | audio, `POST /loop/song`; `loop-and-perform.md`, "Loops"):
not the working loop but a loop from the store, as a new last section of
its bars, one song undo step — **pattern**: its notes a pool pattern on a
synth track that plays its sound and its other sounding lanes
(`song_bridge.add_lanes_section`: `place_lanes` and `intern_pattern` as
"Add loop to song" uses them, no beat and no drums track); **audio**: its
take — the audio ⏺ Rec recorded with it, or its synths rendered first —
from the section's first bar on an audio track
(`song_bridge.add_audio_section`). The first loop into an empty song sets
its tempo and key. A loop in another meter makes a section in its meter.

**Where it lands, and what it asks** (`studio/tosong.py`, `tests/studio/test_to_song.py`):
`/loop/song` also takes `section_id` + `track_id` + `at_bar` (a drop on a
lane, "Drags onto a lane" below: the loop's main lane — or a beat — a
pool pattern in that cell from that bar of the pass to the section's end,
a block span when it is not bar 1; **in the track's own sound**, by
design: a pattern on a track plays that track) and `at_section` (a
new section at that place in the running order). A beat as a pattern is
a section holding it on the drums track. Two answers are
**questions, not refusals** (`tosong.Ask` → a 200 whose HX-Trigger is
`song-ask` {message, choices}; songdrag.js draws a popover by the last
press, each choice the same post with one more value, or a link; the song
is untouched until one is picked):

- **The 8-synth-track cap** (`song_bridge.place_lanes(onto=)`,
  `TrackCap`): a lane with no track to reproduce its sound and no room for
  one names its sound and the synth tracks the section does not use yet;
  each is offered twice — "<track>: becomes <sound>" (`replace`: the
  track's patch, FX, curves, gain and mute become the lane's — every
  section it plays in hears it) and "<track>: keeps <its sound>"
  (`keep`), plus "Free a track on Song ▸". The pick goes back as `onto`
  (JSON, [[track id, how], …], taken in order by each lane that needs a
  track), so a scene of two new sounds asks twice. A track a choice puts
  out of song order makes a layer the main lane by song order (the main
  lane alone plays its track's curves).
- **Already in the song** (`tosong.already`): a plain → Song (no place
  given, not `again`) whose new section would hold nothing another
  section does not already hold — every pattern in its cells (the pool's
  dedupe, `content_hash`, makes them the same ids), every take's clip —
  asks "“X” is already in the song: section N, “Name”. Place it again?";
  **Place it again** posts `again=1` (a new section playing the same
  patterns by reference: the pool keeps one copy). A drop never asks —
  its place was chosen.

**A scene → a section** (`song_bridge.add_scene_section`; Perform's →§
and an idea's → Song, `POST /fragment/perform/scene/song`;
`loop-and-perform.md`, "Slots and scenes"): each slot's loop of notes a
pattern on its own grid (`lane["arr"]`) on a track with its sound, the
beat (`beat_data`: the bank's shape and its per-hit data) on the drums
track, as long as the longest, in the scene's meter, at its tempo
(`section["bpm"]`, when it is not the song's), the first into an empty
song giving it its tempo and key; the cap and "already" ask as above. And
**Scene → Song** makes the
launcher's scene launches, as played, sections in order ("The jam",
"Scenes from elsewhere").

**Patterns carry timing and chance**: a
notes pattern keeps a loop's timing inside a step (`note_nudge`, a share
of a step on a note's start step) and its chance lane (`note_chance`) —
`schema.SONG_PATTERN`'s notes kind, **absent when unset** (`pool_post`:
a step's timing only on a note, no chance of 1), so no pattern from
before them gains a key or moves its content hash
(`song_bridge._NOTE_OPTIONAL`: `note_data`, `layer_note_data`,
`section_loop`'s main lane; `pattern_seq` / `lane_render` for a layer,
regridded with its notes; `songops`' windows; the drawer's tools carry
them as the loop's do, a grid post keeps the timing on the notes still
there; `to_loop` puts them back on the loop). So → Song as a pattern, a
scene's →§, Scene → Song and a drop on a lane play a free-played loop as
it plays — the section's onsets are the loop's (`test_song_chance.py`,
`tests/browser/test_song_chance.py`, ±12 ms from the audio). A section's
render, its chunks, the mixdown, the stems and the MIDI export read them
through `SectionParts` (`song-render-export.md`, "Chance: every pass
afresh"). The drawer's roll draws a nudged pattern on its step lines and
has no chance lane (Perform's has).

**What "Add loop to song" carries** (the Song page's button, Perform's
and Synths' **→ Song**, `loop-and-perform.md`): the main lane and every
sounding layer as note patterns on synth tracks; the drum lane on the
grid as the section's beat; the clip lane as takes. **Every other
variation of the beat that holds hits** (`StudioState.loop_beats`: A–D
in `beat_vars`, session-only on the loop) goes into the pool too — a
section holds one pattern a track, so they wait there, "<section> beat
B", for a cell to take (the section's own is then "<section> beat A",
its letter); one identical to a pattern already in the pool is that
pattern (`intern_pattern`). **The kit**: the first loop into a song makes
its drums track from the loop's kit; after that the drums track keeps
its own (`ensure_drums_track`) and the loop's beat plays through it — →
Song says so and offers `POST /song/drums/loop-kit`, which puts the
loop's `KIT` (the voices' shaping, the mixer, the rows' samples, not the
drum bus FX) on that track: every section's beat changes with it, one
song undo step.

### Meter and tempo

**Meter and tempo** (`studio/meter.py`, `song_model.py`
"meter and tempo", `tests/studio/test_meter.py`,
`tests/browser/test_meter.py`). The song has one tempo and, optionally,
a meter (`song["meter"]`, the topbar's "bar"); a section may keep its own
`meter`, its own `bpm`, and a `ramp` — the tempo it reaches at the end of
each pass, from its own at the start (linear per beat: an accelerando or
a ritardando). All three are absent when unset and when equal to the
song's (`schema` drops them), so a song from before them times, renders
and keys as it did. The section menu holds them (`POST
/song/section/{id}/time`: bar, bpm, a ramp toggle and where it ends; an
empty value is the song's again), the head a chip.
- **A bar keeps its meter's beats at its section's tempo**:
  `section_clock` (a `meter.Clock`) is what `section_timeline`,
  `section_seconds`, `_passes`, the long items' spans, `check_length`, a
  cut at a bar and the plan count in. Bpm always counts quarter notes: a
  6/8 bar at 120 is three of them, 1.5 s.
- **A section plays its patterns in its own bar** (`in_meter`,
  `fit_pattern`): each bar keeps its first beats and where everything sits
  in them, the step length kept — a 4/4 beat in a 3/4 bridge plays its
  first three beats a bar, a quarter grid in 7/8 becomes eighths. The
  pool keeps every pattern as written; a pattern written in another meter
  says so (`pattern["meter"]`: one made in a 6/8 section's cell, from a
  6/8 loop, by a generator in that section). The drawer draws a pattern
  in its own meter.
- **A ramped section is one cycle** (`cycle_bars`), so every pass is the
  ramp; its render is `arrange.TempoSong` (below, "Rendering"), and its
  chunks are the same ramp from their first bar (`Clock.window`).
- **The plan** gives a timed section (`sm.timed`) `bpm`, `meter`,
  `bar_beats`, `clicks` and, under a ramp, `bar_starts` (one pass's bar
  lines, in samples): song.js's playhead, position and a start from the
  marker read them; click.js clicks them. A 4/4 section at the song's
  tempo has none of them.
- **The playlist is time-true** (`songpage.time_true`): a section's bar is
  `--bs` base bars wide — a base bar is a 4/4 bar at the song's tempo, a
  6/8 bar 0.75 of one, a bar at 80 bpm in a 120 song 1.5, a ramp's bars
  their average — through one CSS rule (`.song-view [style*="--bs:"]`
  rescales `--bar-w` off `--bar-w-base`), so every block, echo, take and
  lane inside it follows. The ruler draws a run of ticks a section
  (`.ruler-seg`) and its numbers where their bars are; songline.js maps
  bars and x through the heads' `data-x0` / `data-bs` (the marker, the
  range brace, a ruler click, an edge drag). A song with no timed
  section has none of these attributes.
- **Takes and long audio keep their own speed**, placed in seconds from
  their bar through that section's clock; they are never stretched.
  Recording a pattern take into a timed section is refused (its times
  are folded onto steps at the song's 4/4 tempo); the jam cannot launch a
  timed section (below, "The jam").
- **Bridges**: "Add loop to song" gives a loop in another meter a section
  in it (unless it is the song's) and metered patterns; "Open in
  Perform" opens a timed section as a loop in its meter at its tempo.

### Rendering

**Rendering**: `song_model.section_arrangement` is the only bridge
into the engine; cycles and tails as under "Section
rendering" in `song-render-export.md`. **Plan == export**: the player plays exactly the
arrays `/export/song.wav` stitches (`section_timeline`); never add a
second tiling.

### Recording

**Recording** (`routes/song_record.py`, `record.py`, `songrec.js`):
one armed track (runtime only). Notes: the browser stamps note-ons in
heard time from the section start and posts each loop wrap (and once
on stop) to `/song/pattern/{id}/record`; `record.py` is the one
quantizer (1/8, 1/16, 1/32, off = 32 steps a bar; swing-free; times
fold by the section's `cycle_bars` first, then onto the pattern; one
rounding — straight to the pattern's step when the grid is at least as
fine, else to the grid's own step; into a trimmed block times count from
the block's first bar and a note outside it is dropped, the toast saying
how many: "Block spans"), pinned by
`tests/fixtures/record_vectors.json`.
Overdub replaces an identical (pitch, step); replace clears the first
post's window. A take's first post may fork a shared pattern
(`unique=1`: `#rec-shared` asks first, naming the track ⏺ records — the
armed one, else the cursor's — the section and the pattern, with Make
unique, Record into shared and Cancel; Esc is Cancel) or make one for an
empty cell (`new`); later posts follow
the take id. One undo per take (`state.take_history`, runtime,
`/song/record/undo`; emptied whenever the song is replaced — a project
import or load, and a revert/reset of the "song" scope through
`_scope_set` — because an undo restores its pattern by id; how it sits
beside Ctrl+Z is under "Undo and redo"). Mic: `/song/take` stores a PCM WAV take as
`take-YYYYmmdd-HHMMSS` (capped at MAX_CLIP_SECONDS and kept; deleted
again if the song refuses the placement) at its bar — a section can't
be shortened past a take's bar; the browser shifts it by input +
output latency, else by
`studio_data/calibration.json` (`/song/calibration`,
`schema.CALIBRATION`). Takes rename inline (`/song/take/{clip}/rename`; the Lab chop a take's
clip was kept from follows, `labchops.clip_renamed`).

**Finger drums and ✦ Clean up** (`static/songtake.js`,
`takeclean.py`, `takecoach.py`, `routes/song_record.py` `take_clean`;
`tests/studio/test_take_clean.py`, `tests/browser/test_song_take.py`).
- **The drums lane arms** like a synth lane. While it is armed the keys
  A S D F G H J K and Z X C V B N M , play kick snare hat ohat clap tom
  rim crash (two keys a voice), MIDI pads play them by the General MIDI
  drum map (`midi.js` asks `S.drumNote` before the synth), and
  `#finger-drums` floats the eight pads (click, touch, pen pressure). The
  sound is the drums TRACK's kit (`GET /song/track/{id}/hit/{voice}.wav`,
  not the loop's `/drum/{voice}/hit.wav`). Every hit goes to songrec.js
  (`S.onHit`); a take stamps and posts hits as it does notes, and the
  record route writes them onto the drums pattern (`record.quantize_hits` /
  `merge_hits`: a hit's velocity is its own `hits[..]["v"]`); an empty
  drums cell gets a drums "Take n" pattern.
- **The count-in counts down** ("ready", 3, 2, 1 on its four beats, then
  "● rec": `#rec-countdown`, from songrec.js's `song-countin`) and **a
  metronome ticks every beat while a take runs** (`#rec-click`; to the
  context's destination, as click.js's). The section loops and every pass
  overdubs until ■.
- **The take's undo entry keeps its raw events** (`take_history[..]
  ["raw"]`, capped at `MAX_TAKE_RAW_EVENTS`), the pattern before the take
  (`base`, `clear`) and after it (`after`). **✦ Clean up** (`POST
  /song/take/clean`; by itself when a take ends with `#rec-clean` on)
  rebuilds the take from those: `takeclean.clean` takes out a steady lean
  (the circular mean of the offsets, when they agree) and a whole step of
  latency read off the kick, snaps to the take's grid ("off": the coarsest
  that fits), merges doubles, draws velocities to each voice's median
  and moves wrong notes into the song's key; then, with a Gemini key,
  `takecoach.coach` (one JSON call against `SCHEMA`; the agent's key and
  model, `agent.md`) returns up to four lines of feedback and eight edits,
  each checked by `takeclean.apply_edits`. Gemini's call runs outside the
  lock (`render_outside_lock`); the result is one song undo step, "clean
  up take" — Ctrl+Z (or the report's ↶) gives the take back as recorded.
  Refused once the take's pattern was edited since. `#take-report` lists
  what was done and said, and why Gemini was not asked when it was not.
  Measured on synthetic takes only (the tests): a beat played 75 ms late
  at 120 bpm records every hit a 16th late, and cleans up onto the beat.
  **Nobody has played a take on real keys or pads, or judged Gemini's
  edits by ear.**

**Place: the clip picker.** A clip already in the store goes onto an
audio track from the clip library drawer (`sampler-and-clips.md`, "The
clip library"): on this page the drawer has a "Place into" bar (section,
bar, audio track — "first audio track", or a new one when the song has
none) and a **Place** button on every clip, posting `POST
/song/section/{id}/clip` (`clip`, `at_bar` 0-based, `track_id`;
`song_record.place_clip` -> `sm.place_take`). It is a `_commit`, so one
undo step ("place clip") and no confirm; the take appears on its track
and the drawer stays open on the section and bar that were chosen. The
Sample Lab's "→ Song" calls the same `place_clip`. On the playlist a clip
row also **drags** out of the drawer onto an audio lane, through the same
route ("Drags onto a lane").

**A placed take** (`POST /song/take/edit`, `routes/song_record.py`;
`sm.move_take`, `sm.remove_take`; `tests/studio/test_song_take.py`) is
moved or removed. A take has no id, so it is named as the page draws it —
`section_id`, `track_id`, `clip`, `at_bar` (the first match). `op=move`
takes `to_section` / `to_track` (default: where it is) and `to_bar`
(0-based; 1-based with `bar_base=1`, the take menu's field), clamped into
the section, with `place_take`'s rules at the destination (an audio
track, `MAX_LANE_CLIPS` a section); the gain goes with it. `op=remove`
takes the reference off the song; the clip stays in the store. One undo
step each ("move take" / "remove take").

### Long audio: a whole stem on the timeline

**Long audio** (`song_model.py`
"long audio", `longaudio.py`, `routes/song_long.py`;
`tests/studio/test_song_long.py`, the journey in
`tests/browser/test_lab.py`, opt-in `tests/test_long_real.py`): a whole
stem of a Sample Lab source (or a mix of its stems, or the recording)
under the arrangement. A clip is 30 s and a take lives in one section;
this is neither.

- **On the song, not in a section**: `song["long"]`, at most
  `MAX_LONG_AUDIO` (4), **absent when empty** (`song_post`; the default
  song has none either, so no older song gains a key and
  `tests/fixtures/state_split` is untouched). An item:

  ```
  {"id", "track_id" (an audio track: its row, gain, mute, solo),
   "name", "source" (the Lab source's id — a content hash), "model",
   "stems" ([] = the recording), "levels" ({stem: dB}, sparse),
   "at_bar" (0-based bar of the TIMELINE: every bar of every repeat),
   "start" (the second of the source that lands on that bar line),
   "length" (seconds; 0 = to the source's end), "gain", "mute",
   "bpm" (the source's tempo when placed: a label and the tempo
   button's value), "seconds" (the source's length when placed)}
  ```

  Hostile values are clamped or dropped by `schema.LONG` (a source that
  is not 16 hex, a model that is not `[a-z0-9-]`, a stem no model makes,
  an item on a track that is not audio); whether the audio is on disk is
  the render's question.
- **The timeline rule** (a deliberate call): the item **stays at
  its bar** and the arrangement moves under it. A section that repeats
  does not replay it — each pass plays the next bars. Sections added,
  moved, resized or deleted leave `at_bar` alone. The song is never
  lengthened: the item is cut at the song's end, and one whose bar is
  past the end is silent (`sm.long_spans`).
- **Played as recorded, never stretched.** In time with the song only
  when the song's tempo is the source's: `op=add` with `tempo=on` and
  `op=tempo` set `song["bpm"]` to the item's `bpm` to three decimals
  (the Song page's only tempo control; it retimes every pattern). An
  item whose `bpm` is not the song's says so on its row ("it drifts").
  Whole-stem stretch was measured and not built (WSOLA moves 5 % of
  drum hits by 15 ms or more).
- **By reference.** Nothing is copied: the item names the source store's
  files. A source deleted in the Lab leaves the item on the song,
  **silent and marked "missing"** (the Lab's delete confirm says which
  items play it, the undo ring's steps included:
  `StudioState.long_usage`, `History.song_undo_long`,
  `longaudio.delete_note`). Importing and separating the same file again
  reconnects it — same content, same id. The project bundle never
  carries it (`sample-lab.md`, "Project export").
- **Edits** — `POST /song/long`, `op` add | set | remove | tempo, each a
  `_commit` (one undo step; a dragged level joins by
  `key="long:<id>:gain"`). `set` takes `bar` (1-based), `section_id`
  (start at that section), `start`, `bars` (how many song bars play; 0 /
  empty = all), `gain`, `mute`, `name`, `track_id`, and `trim` (signed
  bars off its head: `sm.trim_long` moves `at_bar` and `start` together,
  so what still plays stays where it was). An audio track that holds an
  item is not removed (named in the refusal, as for takes).
- **Getting there**: the clip library's Stems group on this page (**→
  Song** beside each stem's download link: that stem as separated, at the
  "Place into" section and bar, with the tempo box) and the Sample Lab's
  "→ Song track, whole" (the ticked stems at the lanes' levels —
  `mix=lanes`, read from the source's meta — from the Lab grid's first
  bar line; `longaudio.from_source`).
- **The page**: in the audio track's row every section the item crosses
  draws a `.long-part` (its share of the section's WHOLE span, all
  repeats, with that share's waveform from the Lab's stored peaks; the
  name where it starts; hatched when missing). On the playlist a
  section's column IS its whole span, so the strips of neighbouring
  sections join into one region at its true place and length. Under the
  arranger, `.long-list` — a row an item: name, from bar, start at…,
  ◂ bar / bar ▸ (trim), for N bars, track (when there are two), M,
  level, "Song tempo → N" when it differs, ✕, and what it plays ("bars
  1–64 of its 78: the song ends first"). Each control posts itself (no
  enclosing form, `op` and `id` in `hx-vals`). `song_context` gives
  `song_long` (`longaudio.rows`), and the arranger's watch adds the
  items and what is on disk **only when the song holds one**.
- **On the playlist** (`tests/browser/test_song_playlist.py`): a strip **drags** along the timeline
  (songdrag.js: `op=set` `bar`, and `track_id` on another audio lane),
  keeping the bar it was grabbed by — each strip carries the item's
  first bar, its played bars and how many lie before the strip
  (`data-at`, `data-bars`, `data-skip`); the first strip's left edge
  **trims** its head (`op=set trim`, signed bars: what still plays stays
  where it is) and the last strip's right edge sets **how many bars
  play** (`op=set bars`) — songline.js's `.long-edge`, which moves only
  a tag by the pointer (the strips are drawn in the server's
  percentages); and a stem of the clip library's Stems group drags onto
  an audio lane (`op=add`). **`op=add` also takes `bar`** (1-based, the
  timeline's own bar, as `set` does): `section_id` + `at_bar` can only
  name a section's first pass, and a drop may land on a repeat's.
- **Rendering and keys**: `song-render-export.md`, "Long audio".
- **Not built**: stretch to the song's tempo; long audio in the project
  bundle; FX or lanes on long audio (an audio track's rack and lanes move
  its takes only — "An audio track's rack and lanes" below: a stem runs
  across passes and a chain would have to as well); a pickup before the Lab's first
  bar line; a preview of the region's new place while a section's edge is
  dragged (the item stays at its bar while the sections move under it;
  the drag's CSS preview moves the strip with its cell until the redraw).

### Block spans

**Block spans** (the playlist's one model change; `song_model.py`
"block spans"; `tests/studio/test_song_spans.py`): a
cell's pattern need not fill its section.

- **Model**: `section["spans"] = {track id: [at, len]}`, bars, `at`
  0-based: the block plays bars `[at, at + len)` of **each pass** of its
  section — the pattern **restarting at `at`** (its bar 1 lands on the
  block's first bar), tiled, cut at the block's end — and is silent
  outside. One block a (section, track). **Absent for a track = the whole
  section, which is every cell of every song that never trimmed one**:
  no saved song gains a key, no render key moves, `RENDER_REV` is
  unaffected, `tests/fixtures/state_split` is untouched.
- **Schema** (`SECTION.spans`, held in `song_post` once a section's bars
  are final): `at` clamped to `0..bars-1`, `len` to `1..bars-at`; a span
  on an empty cell, an audio track or an unknown track is dropped, and
  so is any malformed pair; **a span that covers the section is dropped
  and an empty `spans` is not written** (absent means absent, as `auto`).
- **Edits** (`sm.cell_span`, `set_span`, `clear_span`, `spill_span`):
  `set_cell(None)` and `remove_track` drop the span; `set_cell(pid)` on
  a trimmed cell keeps it (new content, same place); `duplicate_section`
  copies it; `move_cell` carries it to any track, held to the
  destination's bars (a block without one fills the cell it lands on);
  `make_unique` / `variation` leave it. Shortening a section cuts its
  spans to fit; a section cannot end at or before a block's `at`
  (refused, naming the block — as for a take).
- **`POST /song/cell/span`** (`routes/song.py`): `section_id`,
  `track_id`, `at`, `len` (a span covering the section clears it: "Fill
  section"); or `base=1` with `from` / `to`, the drawer's 1-based
  inclusive fields; or a spill, `to_section` + `to_len`. One `_commit`
  ("trim block" / "extend block" / "slide block"),
  `key="span:<section>:<track>"` so a held Alt+arrow or a stepped field
  is one step (a spill never joins: it is its own).
- **Spill** (`sm.spill_span`: a right edge dragged past its section's
  last pass). The block runs to the end of its own section; then for
  each following section up to `to_section`, in order, on the same
  track: an empty cell takes the pattern (whole, or `[0, to_len)` in the
  last one); a cell already playing this pattern is extended to cover
  (never shortened); **a cell playing another pattern stops the spill
  there, and so does a section shorter than the pattern** (it would have
  to grow). Nothing is ever overwritten; the toast says how far it got.
  The result is one block a section — the model has no block that
  crosses a boundary.
- **The render rule** is `song-render-export.md`, "Block spans": a
  section with a trimmed block is one flat cycle, its whole length (as
  with a take or a lane), so it must fit `MAX_SECTION_SECONDS` (120 s) —
  the refusal names the block. Automation lanes still run across one
  pass of the SECTION, not the block. Everything downstream — chunks,
  the stem cache, tails, the plan, the export, stems, MIDI, solo cell —
  reads the same parts and follows; a whole stem under the section is
  unaffected (`pass_cuts` never reads a cell).
- **Exact**, not approximate: a trimmed block renders what the same
  notes written by hand into a section-long pattern render. Measured
  (`test_song_spans.py`; largest sample difference): one piece against
  the by-hand section's native loop 0, against it flattened 0; chunked
  2.2e-16; through track FX (reverb, drive) 0; repeats and tails into
  the next section 0; under a level lane, a field lane on the main lane
  and on a layer 0; with a whole stem under it 0; solo cell 0 LSB; each
  stem 0; the MIDI notes equal.
- **Recording** (`record.step_of(block=)`): a note's time is taken from
  the block's first bar before it folds onto the pattern, and a note
  played outside the block is dropped — it would never be heard where it
  was played — the toast counting them ("2 notes fell outside the block
  (bars 3-4) and were not recorded."). Replace mode's window is shifted
  the same way. Five vectors in `tests/fixtures/record_vectors.json`.
- **"Open in Perform"**: the loop has no spans (`song_bridge.to_loop`
  carries none), so a trimmed block opens as its whole pattern and the
  route's toast says so.
- **The page**: the block is drawn at its bars with its echoes
  (`songpage.blocks` reads `sm.cell_span`); its two edges, the keys and
  the drawer's row are under "Song view" below. The drawer's root
  carries `data-at` / `data-len` for a trimmed block, and both
  playheads (song.js's, songedit.js's solo) count the pattern from the
  block's first bar and hide outside it.
- **Not built**: two patterns on one track inside one section (a fill in
  bar 8 still needs its own section, or a second synth track); a block
  crossing a section boundary as one object (a spill makes one block a
  section); per-track boundaries; beat (sub-bar) placement; a left edge
  that holds the notes still (a phase per block — the pattern always
  restarts at the block's left edge). `spans` is shaped so a later list
  of placements can replace the pair.

### Risers / impacts

**Risers / impacts** (a section's ⋯ menu, `.trans-form` ->
`/song/section/{id}/transition`; also the `+`
on a section's boundary, which picks from the energy lane and places
through the same clips: "Playlist tools" below): `synthlab.transitions` renders at
the song's bpm (bars cut to the section and `MAX_CLIP_SECONDS`) into
the clip store (`riser-up-2bar-b60-t40-124bpm`; meta `source:
transition` + the settings, which a repeat post matches to re-place the
clip unrendered) and places it as a take: on `track_id`, else the
first audio track, else a new "FX" audio track. `direction` up (riser,
reversed impact) ends on the section's last bar line, down starts at
bar 0. A refused commit deletes a clip it wrote. The take is audio: a
later bpm change doesn't re-render it.

**Six kinds** (`transitions.GENERATORS`; the form lists all
six, and its direction starts on **auto** — no direction posted, so
the route takes the kind's own): beside `riser` and
`impact`, `sweep` (filtered noise alone, a resonant band rising or
falling: the uplifter / downlifter with no pitch in it), `swell` (a
cymbal-like burst into a long room, reversed, ending on its loudest
moment), `subdrop` (the sine fall alone, in mono) and `bed` (room tone
and dust at -18 dBFS, **loopable**: its end runs into its start, no
fades). All deterministic, exact in length, peak-normalised (-1 dBFS;
the bed -18: `transitions.PEAK`). The route reads two tables instead of
the direction alone: `transitions.leads_in(kind, direction)` says which
end sits on the bar line (anything going "up" ends on it — except a
bed, which has no direction: both render the same samples, the route
stores "down", so both name one clip, and it starts the section), and
`DEFAULT_DIRECTION` is what a kind takes when the form posts none (a
riser up, an impact down — which is what the form's "auto" does; a
form starting on "up" for every kind would place an impact reversed,
before the bar line). Unknown kinds and
directions, and non-finite numbers, are a 422 toast and nothing
written; wild numbers are clamped into the clip's name
(`tests/studio/test_song_transitions.py`).

### Automation

**Automation** (`tests/studio/test_song_automation.py`): what
changes over a section. `section["auto"] = {track_id: [{target,
points}]}` (`sm.set_lane` / `remove_lane`; one lane a target, ≤
`MAX_CELL_LANES` a cell, ≤ `MAX_LANE_POINTS` each). Points are
[position, value], both 0..1, linear between, edge values held: the
position runs across **one pass of the section** (each repeat replays
it), the value maps onto `curves.lane_range(target)`. Targets: `gain`
(every kind of track: the track's level, a fader *after* its FX;
0..1 = x0..x2, 0.5 = unity) and any `CURVABLE` field (synth tracks),
clamped by construction to the registry's curve range. An audio track's
lanes sit on its row in a section, under the same key ("An audio track's
rack and lanes" below).
- **Combine rule**: in that section a field lane *wins* over a curve
  the track's sound carries for the field (`_with_lanes` takes it out
  of `curves`); the curve still plays in every section without the
  lane. A layer track ignores its sound's curves — a lane is
  the one automation it plays (its render dict's `"auto"`).
- **Render**: a section with a lane is one flat cycle the length of
  the section (`cycle_bars`, like a take), so plan, export, stems and
  MIDI all follow from `section_timeline`. Field lanes are resolved
  per note by `build_song(auto=)` through `_curved_notes` /
  `_timeline_notes`: `auto` = {curves, at, n} is the lane over the
  cycle's `n` samples read from sample `at` (`curves.lane_array`),
  and `SectionParts.window` only moves `at` — a chunk reads exactly
  the cycle's values and a note ringing past its chunk keeps following
  the lane, so chunked == one-piece to 0.0 (the loop curves'
  crop-and-relinspace only comes close). Gain lanes
  (`SectionParts.gains`, by stem name) scale the finished stems in
  `_cycle_stems`, after the stem cache: reshaping one re-renders no
  note and no FX chain; the tail leaves at the lane's last value.
- **Keys**: absent means absent. `auto` is not in a section without
  lanes (`section_post`), `RenderInputs.key()` and the main stem's
  payload add `"auto"` only when set, `section_key` adds `"gains"`
  only when set: a lane-free song's keys and audio are byte-identical.
  A gain lane at unity end to end is dropped by `sm.section_lanes`
  (the one predicate the cycle and the render share), so it is
  bit-identical too. A muted track keeps its lanes (as its clocks):
  a stem export keeps the mix's cycle.
- **Schema**: `LANE` / `SECTION.auto`; hostile values are clamped
  (positions, values), sorted, or dropped (unknown or non-curvable
  target, < 2 points, a second lane on a target, a lane on an empty
  cell or on an audio track's row in a section without one of its takes,
  a field lane on drums or audio). Clearing a cell or removing its
  track drops its lanes; duplicating a section copies them.
- **UI**: `POST /song/cell/auto` (`op` add / set / remove) from the
  drawer's `.drawer-lanes` — one htmx form a lane (start, optional mid
  + its position, end; `hx-trigger="change"`), no script; the arranger
  cell shows `.cell-auto`. The drawer comes back as a morph, so the
  slider being nudged keeps keyboard focus. A lane on a section too long to render as
  one pass (`MAX_SECTION_SECONDS`) is refused. Undo is the song's own
  ring ("Undo and redo" below): a dragged lane slider is one step.
- **Not automated** (FX-parameter lanes aside, below): master lanes, lanes on long
  audio, lanes across section boundaries, curve
  shapes other than linear segments, drawing on a canvas (the drawer
  writes 2-3 points; the model holds 16). "Open in Perform" and the MIDI
  export carry no lanes.

### FX-parameter lanes

**FX-parameter lanes** (`studio/fxauto.py`, `curves.FX_LANES`,
`tests/studio/test_song_fx_lanes.py`, `tests/test_effects_swept.py`): a
lane on a parameter of a unit in the TRACK's FX chain, on a drums, a
synth or an audio track (the last: "An audio track's rack
and lanes" below). Everything under "Automation" holds — the lane model, one
flat cycle a section, repeats replay it, 4 lanes a cell, the drawer's
sliders, one undo step a drag — with a new target form,
`fx:<unit id>:<kind>:<param>`, whose 0..1 maps onto the rack slider's own
range (`fxrack.FX_UNITS`; linear, as the slider is) — except where the
slider is wider than the lane, `curves.FX_LANE_RANGES`: the drive's
`amount` slider spans 1-12 and its lane 1-4, the range older saved lanes
were drawn on, so they play the values they did.
- **A whitelist, each entry the unit itself with its slider moving** —
  never a crossfade between two renders, never the chain run in pieces.
  A parameter not listed is not in the menu, and a target naming one is
  dropped by the schema. Time parameters (a delay's time and sync, a
  beat repeat's grid, a tape stop's length) are out on purpose.

  | Parameter | How | Accuracy (measured) |
  |---|---|---|
  | `mix` of drive, clip, phaser, chorus, flanger, delay, reverb, fold, ringmod, comb, granular, shimmer, doubler, rotary, tape_echo, telephone, pitch, plate | each ends in `dry * (1 - mix) + wet * mix`: the unit runs once fully wet, the blend is per sample | exact: the scalar unit's arithmetic; a lane that stands still is the unit bit for bit |
  | drive `amount` | `effects.drive_shape` takes a per-sample drive in every mode (soft is `core.soft_clip`, as the Patch's `drive` curve runs) | exact, memoryless |
  | clip `drive` | the dB of gain into the clipper, per sample | exact, memoryless |
  | delay `feedback` | y[i] = x[i] + feedback[i] * y[i - d] (`effects._delay_line_numpy`) | exact, per sample |
  | tape_echo `feedback` | the loop's gain per sample: tape[i] = x[i] + feedback[i] * tap[i] (`effects._tape_echo` takes the array as it comes) | exact: one code path for a scalar and an array, so a still lane is the unit bit for bit |
  | rotary `speed` | the rotors' target rate per sample through their one-pole slews (`effects.rotor_rate`: the horn's 0.6 s, the drum's 2.2 s), so a step on the lane is the chorale / tremolo switch, the rotors ramping to it | exact: one code path, a still lane is the unit bit for bit |
  | lowpass / highpass `cutoff`, `q` | `core.biquad`'s swept state-variable filter: one coefficient set per 64-sample block (1.45 ms) | a still lane is the unit bit for bit; against a coefficient set per sample, a full-range sweep over 2 s is 46 dB or more (cutoff) and 58 dB or more (q) below the signal |
  | eq `low`, `mid_gain`, `high`, `mid_freq` | `effects._eq_swept`: the same filter per band, the gain mixed from its outputs (Simper's bell and shelves — the transfer functions `eq_bands` builds, with a state that survives a coefficient change) | at rest `eq` to 6e-14; -12..+12 dB over 2 s is 57-78 dB down, the mid frequency 100 Hz-8 kHz over 2 s 39 dB down |
  | gate `depth` | the gate's smoothed step envelope `g` is computed once; gain = 1 - depth[i] * (1 - g[i]) | exact, memoryless: a still lane is the unit bit for bit, a moving one is the formula to rounding (0) |
  | sidechain `depth`, pump `depth` | the duck's shape `s` (0 at rest, 1 on a trigger; overlapping ducks take the deeper) is computed once; gain = 1 - depth[i] * s[i] | exact, memoryless: bit for bit when still (1 - d * s never rises with s, so the lowest gain is the deepest shape's), 6e-17 moving |
  | beat_repeat `mix`, stop `mix` | the unit's own blend inside its regions, `dry * (1 - ramp * mix) + wet * mix`, with the mix read at each output sample (`fxauto.OWN_MIX`); which steps repeat and where the tape stops are the pattern's, the rolls' and the period's — never the mix's | exact: bit for bit when still; each output sample is the scalar unit's at that sample's mix (0 at 24 instants) |
  | ott `depth`, limiter `gain`, transient `attack` / `sustain` (the FX2 units' lanes are in `live-voice.md`, "The FX2 units") | the OTT's blend band by band, `1 - depth[i] + depth[i] * g[i]` against each band's own gain; the limiter's dB in per sample (the held gain follows it, so a build pushed louder is held at the ceiling as the slider would be); the shaper's detector dB times the value per sample | exact, memoryless in the parameter: a still lane is the unit bit for bit (`test_song_fx_lanes.py`) |

  The gate, sidechain / pump and beat_repeat / stop rows
  (`tests/studio/test_song_fx_lanes_clock.py` holds their table) are the
  clocked units. A stutter or a tape stop is *placed* this way: the rack
  rests at mix 0 and one section's lane lifts it. Still no time
  parameter: a gate's pattern, a pump's beats, a repeat's grid and a
  stop's length are not on the list.

- **The tail rule: two rings.** A flat cycle's chain runs once over the
  cycle and what rings past it, and that ring is folded into whatever
  plays next (`song-render-export.md`). With a parameter moving, what
  plays next matters, so an automated stem keeps two
  (`render._LaneStem`, `SectionAudio.out` / `.handoff`):
  - into the section's **own next pass** (a repeat, the looped section,
    the solo audition) the lane has started again, so past the cycle's
    end the parameter *replays the lane*. For a linear unit the fold is
    then the two passes rendered in one piece — measured at rounding
    (-250 dB or better) for the reverb, the delay and the comb, and at
    the swept filters' own block error and nothing more (the filter's
    block grid is shifted so a block starts on the cycle's end: the
    restart lands on that sample, not up to 63 late);
  - into **another section** the unit sits at the rack's own value, so
    there the parameter *rests at it*. As exact when the section the ring
    enters does not automate that parameter. **Approximate when it
    does**, and when a section without the lane rings into one that has
    it: the ring alone plays at the rack's value instead of that
    section's lane (the section's own notes follow its lane exactly).
    Measured on about the worst case — a reverb's mix at 1 where the rack
    says 0.25 — the first pass is off by -11.5 dB of its level, -5.9 dB
    in its first quarter second.
  The fold is otherwise as good as the unit's with its sliders still,
  which was never exact for some and is not made so: a drive is not
  linear, a phaser's, chorus's or flanger's LFO and a ring mod's carrier
  restart with the stem, granular and shimmer read the whole buffer. The
  master chain runs over both rings (`section_master_audio`); a level
  lane holds its last value over both.
  The clocked units (gate, sidechain, pump, beat repeat, stop) are
  linear and periodic on the clock, and a cycle is a whole number of
  their periods, so both rings are exact for them — measured 6e-17 for
  the gate and the beat repeat, 0 for the stop — with two things that
  are the units' own, lane or no lane: a duck's first trigger, at time
  0, lands on sample 1 (`core.n_samples` never answers 0), so the head
  of a pass is ducked a sample later than the same duck met mid-buffer
  (-91 dB of the pass or lower for the sidechain and the pump, exact
  past that first duck; -84 dB between two passes with the depth
  standing still); and the gate's 16th is a whole number of samples, so
  at a tempo whose 16th is not (120 bpm: 5512.5) its pattern slips 8
  samples a bar.
- **Unit identity.** A chain is a list of `{kind, params}`, matched by
  index, and an index does not survive a reorder.
  A unit gets an `id` (8 hex) **when a lane first targets it** —
  `sm.claim_fx_target` -> `fxrack.unit_id`, in the same commit as the
  lane, so one Ctrl+Z takes back both — and `clean_chain` keeps it (a
  malformed or repeated id is dropped). The rack's move and remove carry
  the unit as it is, so the lane follows its unit through a reorder on
  Synths. **A chain no lane ever touched holds no id**: it saves, keys
  and renders as a plain chain (`tests/fixtures/state_split` is
  untouched). The drawer's menu names an id-less unit by its place
  (`fx:@<index>:<kind>:<param>`), a form that only travels from the menu
  to the route and is never stored.
- **Missing units.** A lane whose unit is no longer in the chain (or
  whose id now sits on a unit of another kind) is kept in the song and
  read by nothing: `sm.section_lanes` leaves it out, so the section's
  cycle, key and audio are the section's without it — never a crash,
  never another unit of the same kind. The drawer shows it struck
  through ("missing — its effect is gone") with only its remove button.
- **Keys**: absent means absent. `SectionParts.fx_auto` = {stem name:
  [[target, points]]} joins a cycle stem's key and `section_key` only
  when set (the lanes ride the stem's chain, so unlike a level lane they
  are in the stem cache). `RENDER_REV` is not bumped: no existing
  session's audio moves.
- **UI**: the "+ Lane" menu lists the track's units after the level and
  the fields, an `<optgroup>` per unit ("Reverb", "Reverb 2" when the
  chain holds two) with its automatable parameters; a lane row is named
  "Reverb mix" and reads out in the parameter's unit (%, Hz, dB). The
  undo key is `auto:<section>:<track>:<target>` as for any lane, the target
  being the stored string. Live keys are not affected (lanes are a Song
  render feature); the MIDI export carries none.
- **Left**: the ring into a section that automates the same parameter
  (above; exact needs the next section's lane in the stem's render and
  key); a log taper for a cutoff lane (it is linear in Hz, as the rack's
  slider is — the first 1 % step from 100 Hz is 219 Hz); the other
  parameters (a reverb's decay and size are one convolution; rates,
  depths, a fold's amount, width, the comb's tune and feedback each want
  a per-sample form and its test: none is built); FX lanes on the master
  and on long audio; a lane is not carried when "Open in Perform"
  copies the track's chain (the unit keeps its id there).

### An audio track's rack and lanes

**An audio track's rack and lanes** (`mixer.edit_rack`,
`routes/mixer.py` `track_rack`, `song_model._own_takes`,
`render._take_stems`; `tests/studio/test_audio_fx.py`,
`tests/browser/test_song_audio_fx.py`). A track of takes — a mic take, a
clip placed from the library, a Sample Lab chop — has an FX chain and
automation lanes like the other kinds, so a sampled loop can have its
resonant low-pass swept open over eight bars.

- **The rack is the track's `fx`** (the TRACK node every kind shares,
  `clean_chain`-validated: unknown kinds dropped, parameters clamped, a
  unit's id kept). A synth or a drums track's chain is its SOUND's — edited
  in the dock and mirrored into the track — so only an audio track's is
  edited here: `POST /song/track/{id}/fx`, `op` add (`kind`) | remove
  (`index`) | move (`index`, `dir` ±1) | set (`index`, `name` a parameter,
  `pattern`, `step`, `steps`, a gate's `preset` or `recall` — a ★ preset
  or "defaults" — `value`: `fxrack.edit_chain`, every rack's). Each is a
  `_commit`, one undo step; a knob's posts share
  `key="rack:<track>:<index>:<param>"`, so a drag is one step, and a
  press (`fxrack.RACK_PRESSES`) has no key, so two presets are two
  steps. The drawer draws it with THE rack (`fragments/fx_units.html`:
  `synths.md`, "One rack everywhere") — ★ presets, jacks and the drawn
  leads, choice menus, the step editor — every post to the track's route
  with `hx-swap="none"` (the drawer's region brings it back, morphed).
  Refused (a 422 toast): another kind of track, an unknown kind, a full
  rack (`MAX_UNITS`), a unit that is gone, a parameter it does not have, a
  value that is not a number. A move carries the unit as it is, id
  included, so a lane follows it. The Mix view's EQ reaches it too (the
  strip's lo / mid / hi add the track's first `eq` unit, as on a synth);
  its pan and sends stay off.
- **Where its lanes live**: under the key every lane has,
  `section["auto"][track_id]` — an audio track has no cell, so the key is
  its **row in the section**. They are read while the section holds a take
  on the track (`sm.lanes_live`, the one predicate `set_lane`,
  `section_lanes` and the schema share), as a cell's are while it holds a
  pattern; the schema drops them from a section with none (its last take
  removed or moved away), and a section cut keeps them on a part a take of
  the track lands in. Targets: `gain` and the parameters of its rack's
  units (`curves.FX_LANES`, through `_fx_lane_targets` / `fx_lane_menu` /
  `claim_fx_target` as on any track) — never a field: there is no sound.
- **The render**: an audio track that sounds and has a rack or a lane in
  the section (`sm._own_takes`) takes its takes out of the one shared
  takes stem (`arr.clips`) into a stem of its own, `clips:<track id>`
  (`SectionParts.takes`; a section with one renders through
  `_cycle_stems`). There they are placed as the shared stem places them
  and cut at the section's end, and the chain runs ONCE over the cycle and
  one cycle more — so a reverb or a delay rings on into the next pass like
  any stem's (kept to what rings; a dry stem hands on nothing). FX lanes
  run in the chain (`fxauto.render`: the two rings, as on a synth), the
  level lane scales the finished stem past the cache. The stem is cached
  under `cycle-takes:` (the takes and their clips' content, the chain and
  its clock, the cycle, the lanes the chain plays); `section_key` adds
  `"takes"` and the lanes add `gains` / `fx_auto` under the stem's name.
  Another audio track stays in the shared stem, and the two sum to the mix
  (1e-12). Its meter is its own (`SectionParts.tracks`).
- **Absent means absent**: an audio track with an empty rack and no lane
  is not split, so a song whose audio tracks have neither keys and renders
  byte for byte as without the rack (the section keys of the routes'
  fixture song with two takes a section are pinned); `RENDER_REV` is
  unaffected. Long audio on the track stays dry: a whole
  stem runs across passes, and so would have to its chain.
- **Measured** (white noise as the loop, `test_audio_fx.py`): a low-pass at
  500 Hz takes the spectral centroid from 11 kHz to 950 Hz; a cutoff lane
  from 100 Hz to 12 kHz at q 2 moves it from 2.2 kHz in the section's first
  quarter to 7.8 kHz in its last.
- **The page**: an audio track's ⋯ menu has **Effects and lanes…** (the
  drawer on its row, in the first section that holds one of its takes),
  and so has each take's menu (that take's section). The drawer on an
  audio row is titled "Effects and lanes": the rack (`fieldset.drawer-fx`,
  the pedalboard classes, "+ add…", each unit's ‹ › ✕ and knobs, each
  posting itself with `hx-swap="none"`; the drawer comes back by its
  watch, a morph) and then the lanes, as a cell's — or, in a section
  without a take of it, a line saying so. The arranger marks an automated
  row with `.cell-auto`, as a cell. The FX badge on the track head
  ("fx N") counts its units, as on the others.
- **Not built**: pan and sends on an audio track (the Mix view's are off:
  a return would need its stem split from the shared one as well); FX on
  long audio; the rack edited from the Mix view's strip; live keys never
  play takes, so nothing live changes.

### The mixer

**The Mix view** (`views/mixer.html`, region `mixer-strips`,
`studio/mixer.py`, `routes/mixer.py`, `service/mixpage.py`,
`static/mixer.js`; the render is `song-render-export.md`, "The mixer"). The
song's tracks as channel strips. It edits the SONG and nothing else: every
edit is a `_commit` (one undo step, saved, `song-changed`).

- **A strip is a `fieldset`** whose value controls are, in DOM order,
  level, pan, send A, send B, duck, EQ lo / mid / hi — so the deck's focus
  mode pages them in fours with no deck code — then its role. Each is a
  native range that posts ITSELF on `change` with `hx-swap="none"`; the
  region comes back **morphed**, so the control keeps its node and focus.
  **`hx-sync="this:queue last"` on each**: htmx 4's default keeps the
  FIRST waiting request of an element and drops the rest, so a held arrow
  key would leave the page at 95 % and the song at 98 %.
- **What each control is** (`mixer.set_strip`): level / mute / solo are
  the track's own; **pan** `track["pan"]` and **sends**
  `track["sends"]` (posted by slot, `send.0` / `send.1` = A / B; the
  first one turned above 0 makes the default returns up to its slot, in
  the same undo step); **duck** is the depth of the track's first
  `sidechain` unit and **eq** the three gains of its first `eq` unit —
  each added to the track's chain on the first touch off rest (refused
  when the chain holds `MAX_UNITS`), left in place at 0 afterwards. A new
  duck's release is read off the tempo (`mixer.duck_release`: 0.28 s at
  122 bpm, 0.18 s at 138). A drums track does not duck; an audio track
  has a level and its EQ (its chain runs over its takes: "An audio
  track's rack and lanes" above) but no pan, sends or duck yet.
- **Returns** (`song["returns"]`, at most `limits.MAX_RETURNS` = 2):
  `{id, name, fx, gain}`. The defaults are `mixer.DEFAULT_RETURNS`: "A
  Reverb" (decay 2.4 s) and "B Delay" (a dotted eighth on the tempo,
  ping-pong, feedback 0.4). The return's strip has its fader and its first
  unit's parameters — never its `mix`: a return renders fully wet.
  Removing one drops every send to it: its ✕ asks first, and the toast
  names the return and the sends that went (one undo step).
- **The keyboard**: every control is a Tab stop, in the strip's order,
  and its arrows are its own (← → pan a pan knob). The bar's **Mute strip / Solo
  strip** press the M / S of the strip last touched (its top edge lit).
- **The rack line** under a strip names its units. On a synth or the
  kit they are the SOUND's chain: the line is a button to the sound's
  page (Synths or the Drum Lab, editing that track: `POST
  /song/track/{id}/edit-sound`); an audio track's is plain text (its
  rack is the Song page's drawer).
- **Routes**: `POST /song/mix/strip/{track_id}` (only the fields posted;
  unknown fields ignored, a post naming none refused), `POST
  /song/mix/return/{return_id}` (`gain`, `p.<param>`), `POST /song/mix`
  (`op=return-add | return-remove | balance`). A track's role stays with
  `POST /song/track/{id}/mix`. A control's posts share an undo `key`
  (`pan:<track>`), so a drag, a wheel burst or a held key is one step.
- **Balance** (`mixer.balance`; `render_outside_lock`): in the loudest
  section (the most stem energy), each synth track that has a role is set
  so its RMS sits at the role's offset under the drums' (`ROLE_OFFSETS_DB`:
  bass -2, lead and vocal -3, chords -6, arp -7, pad -9, fx -10 dB); with
  no drums there, under the loudest such track. Gains are held to
  0.05..1.5; the drums and a track without a role do not move. It reads
  `svc.song_stem_levels`, rendering what is cold from a snapshot with the
  lock released, and commits under it — unless the song was edited
  meanwhile (a toast). One undo step; the toast lists what moved. A
  track's gain sits before its chain, so a driven or compressed track
  lands near its target, not on it.
- **Meters** are the render's, not live: `.meter[data-meter=<id>]` is lit
  by mixer.js from the plan's `levels` for the section in `#mix-section`
  (captioned "meters in": "section" alone read as "play this section")
  — the fullest one when the view opens, the playing one while the song
  plays. They step at section boundaries. The plan is fetched when the
  view appears (rendering the song if it is cold, as Play would). After
  an edit the meters are STALE — dimmed, `#view[data-mix-stale]` — until
  a plan of the edited song is in: the player's own while the song plays
  (mixer.js never asks twice), else mixer.js's, 2 s after the hand rests.
  A plan renders every section the edit reached — side by side
  (`SongRender._warm`), from a stem cache sized to hold the whole song
  (an eighth of the machine's RAM: `render._stem_cache_bytes`) — so that
  is seconds: 1.7-3.6 s on an i9-14900K for an edit to a 283 s, 6-track
  song (a cached section costs 0.05 s for a pan, 0.2 s for a send).
  A fixed 256 MB cache would make every edit cost the whole song. Only the master's
  meters are live: the left and right of the master bus (`#mix-out-l` / `-r`, mixer.js:
  a splitter and two small analysers, read only while the view shows). **No level is in the region's HTML**,
  which is what lets its watch (`regions._w_mixer`: the tracks' mix, the
  returns, the sections' names, the undo labels) be exact.
- **The master strip** is outside the region: its stereo meter, the
  listening fader (`#volume`, the same client-only control as the keyboard
  dock's: how loud you hear the song here, never saved or exported — the
  loudness target sets the export), the target and measure, and its rack:
  `#fx-rack` with
  the one scope `master` (`fx_scopes_for("mixer")`) — the session's master
  chain, which the song plays through — and the FX routes answer a request
  from this view with `song-changed`, never a loop preview. It is not in
  the song's undo ring (as on Synths).
- **The master column is a mastering desk** (strips of a fixed width
  would stop at 1100 px of a 2560 px window and leave the master 1380 px
  of nothing).
  The strips share the width (`grid-auto-columns: minmax(116px, 1fr)`;
  more than fit scroll sideways), each printing its fader's travel (a tick
  every tenth); the master is a fifth of the window (`minmax(380px,
  20%)`), filled to its foot: the stereo meter and fader beside six
  read-outs (`#mm-read`), then the spectrum, the stereo field, the
  short-term history, the target and the rack (one line where it fits).
  pagefill.js reads them off the master bus only while the view shows,
  **corrected for the listening level** (the bus's gain is `#volume`, so
  the numbers are the mix's, not how loud it is heard): a K-weighted tap
  (BS.1770's shelf and high-pass as two Web Audio biquads — near, not
  certified) gives the momentary (400 ms), short-term (3 s), integrated
  (gated at −70 LUFS and 10 LU under, from the last ▶; a click on it
  starts it again) and the range (10th to 95th percentile of the
  short-term over 30 s); the plain two sides the sample peak (held 1.5 s,
  and the highest since ▶) and the correlation. The export's own LUFS and
  true peak stay "measure"'s (`/export/song/loudness`). **The two agree**:
  what plays is the export sample for sample (the plan's
  WAVs stitched == `/export/song.wav`, max difference 0), and the live
  integrated read within 0.4 LU of "measure" on the fixture song played
  through and within 0.3 LU of the export's own first minutes on a
  340 s song ("Anymore 7")
  (`tests/browser/test_input_matrix_mixer.py::test_the_master_read_outs_agree_with_the_export_s_measure`). Keep in mind when comparing: the integrated
  counts from the last ▶ (a song started part way, or stopped early, is
  not the whole song "measure" reads), the peak here is a sample peak
  where "measure" says the true peak, and the short-term (3 s, the big
  number beside it) of a drop is several LU over the song's integrated. The numbers are
  counted to each new value with GSAP four times a second; the bars move
  every frame. The spectrum and the stereo field are the Visuals page's
  1/3-octave and goniometer (`visuals.each`: motion.js's one frame, no
  analyser of their own); at rest each well shows its empty face (the 28
  band wells, the diamond and the correlation line), the history its
  6 LU lines and the target's line. Not built: a gain-reduction / duck
  meter beside each fader.
- **The view plays the song**: song.js's player runs on the Mix view too
  (`#mixer-head`; Space — transport.js's: ▶, ⏸ while it plays — L, the
  bar's buttons), so a mute or a send is
  heard from the next pass. Alt+M opens the view from anywhere.
- **An empty song** (no track or no section) draws one `.empty-state`
  with the shared "Start a song" button.
- Not built: a strip's rack edited in place (the FX routes' `scope=
  track:<id>`), `send:<id>` lanes, sends in the starters' shapes.

### Undo and redo

**Undo and redo** (`state/history.py`, `routes/song.py`
`_commit`, `static/songedit.js`; `tests/studio/test_song_undo.py`,
`tests/browser/test_song.py`). Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y on the Song
page, and the topbar's ↶ ↷, whose tooltips name the step ("Undo: clear
cell").
- **A step holds a clip by name, not by audio.** The Sample Lab's
  "Update clip" (`sample-lab.md`, "Keep and Update clip") is the one
  action that writes over a clip: undoing past it restores the
  *reference* to that clip, which now plays the updated audio — the old
  audio is not kept anywhere.
- **A step is the whole song as it was before one edit.** Every song
  edit already goes through `_commit` (copy, conform, caps, commit);
  `_commit` hands the song it replaced to `state.song_undo_push(old,
  label, key, window)`, so *every* route is one step without code of its
  own — cells, the drawer's grid / tools / length, sections, tracks and
  their mix, lanes, the pool, takes and placed clips, the Lab's beat —
  and a new route is covered by calling `_commit`. Its `label=` is what
  the buttons say. "Add loop to song" (`state.loop_to_section`, not a
  `_commit`) pushes its own. A commit that moved nothing, or was
  refused, is no step. The old song object is kept as it is (a commit
  makes a fresh one), so a step costs no copy; undo swaps the session's
  song with the step's and moves the step to the redo stack; **a new
  edit empties redo.**
- **One step a gesture** (`key=`): a control that posts many times a
  gesture names itself, and commits with the same key within
  `UNDO_COALESCE_S` (1 s, the patch ring's rule and clock) of the last
  one join it: a track fader (`gain:<track>`), one lane's sliders
  (`auto:<section>:<track>:<target>`, an FX lane's target included), a
  section's repeat ± clicks
  (`repeats:<section>`), a block's span nudged by Alt+arrows or its
  drawer field stepped (`span:<section>:<track>`), a dragged velocity
  bar in the drawer
  (`vel:<pattern>` — the grid route sees that the hits did not move). A
  grid stroke or a tool has no key: each is its own step. An undo or a
  redo closes the step a drag was joining.
- **Bounds**: `SONG_UNDO_CAP` 48 steps, and `SONG_UNDO_BYTES` 8 MiB (a
  song may run long, on twelve tracks) of compact JSON over both stacks (each step is sized once, when pushed;
  the newest always stays). Measured (`json.dumps`, and the same song as
  Python objects): a typical song (12 patterns, 6 sections) 17 KB /
  87 KB; 48 patterns, 16 sections 52 KB / 388 KB; every cap at once (128
  full patterns, 32 sections) 141 KB / 1.2 MB. So an ordinary session
  never meets the byte bound (48 steps < 1 MB), and a cap-sized song
  keeps 29 steps (~34 MB of objects) instead of 48.
- **Runtime only.** Never in `state.json` (a session that edited, undid
  and redid writes the bytes of one that only edited), never in a
  project file. Render keys are content keys, so an undone song plays
  from the caches it filled before.
- **What empties it**: the song being *replaced* — a project import or
  load, a revert or reset of the "song" scope, the v2 -> v3 migration
  note being cleared. Nothing has to tell the ring: it remembers which
  song object its steps lead to (`_song_undo_head`) and empties itself
  when `state.song` is another one — which is why `persist.py` and
  `scopes.py` do not mention it, and why a failed import (rolled back to
  the same object) keeps it. A **clip rename** is not a step (the file
  itself moved): `song_undo_remap` renames the clip in every step, as
  `rename_clip_refs` does in the live song.
- **Takes.** A note take is ONE song step however many loop wraps it
  posts (`key="take:<id>"`, no time limit), and a mic take, a riser, a
  placed clip are each one step. Undoing a placement drops the
  *reference*; the audio stays in the clip store (redo needs it, and so
  may another section). The "↶ take" button
  (`/song/record/undo`, `state.take_history`) keeps its own, narrower
  job — put back the pattern of the *last take*, even with later edits
  elsewhere in between — and is itself a `_commit`, so Ctrl+Z after it
  brings the take back. A take whose step Ctrl+Z has undone loses its
  entry in `take_history` (`routes/song.py` `_moved`): its pattern is
  already back, and the button must not offer a second undo of it.
  Redo brings the notes back, not the entry — Ctrl+Z covers it.
- **The other undo domains.** *A track's sound on Synths*
  (`/song/track/{id}/edit-sound`): Synths has its own ring (the patch's,
  empty at the start of each visit) and writes the track's sound into
  the song in place, tweak by tweak, outside `_commit`. The route marks
  the sound as it was (`song_undo_sound`), and the next time the Song
  page uses the ring, whatever the visit left is ONE step ("sound of
  Bass") — so entering is a boundary and leaving pushes a step, if
  anything changed. *"Open in Perform"* copies a section into the
  working loop and changes nothing in the song: no step. The loop's own
  edits belong to Perform's pattern ring and the patch ring; they reach
  the song only through "Add loop to song", which adds a section and is
  one step. Nothing on the Song page undoes into the loop, and nothing
  on Perform undoes into the song.
- **The page** (`songedit.js`): the keys click the topbar's two buttons
  (their own `hx-post`: no other field on the page rides along), in
  window capture so they are neither the patch undo (piano.js) nor the
  browser's. A text field keeps its own Ctrl+Z; a key pressed while a
  pointer is down or a drag is under way, or auto-repeating, does
  nothing. `POST /song/undo` / `/song/redo` answer like an edit —
  `song-changed`, a toast naming the step, the regions by their watches
  — so the open drawer shows the restored pattern (a drawer whose cell
  or pattern is gone closes, and stays closed after a redo) and the
  arranger keeps its cursor and, if it had it, the keyboard. The topbar
  comes back as a **morph** so the button pressed keeps focus; for the
  same reason a button with nothing to move is `aria-disabled`, not
  `disabled` (one that disables itself under the keyboard drops focus to
  `<body>`), and songedit.js swallows its click. A morph also resets the
  transport's `aria-pressed`: songedit.js puts play / loop back from
  `songClock()` after each settle.
- **"Clear pattern" asks nothing** (no `hx-confirm`): Ctrl+Z is the
  way back. "Delete pattern" and the section / track
  deletes still confirm.
- **Not undone**: the master FX chain and anything else outside
  `state.song`; the clip store (a take's audio is never deleted or
  restored by undo — a clip deleted by hand stays deleted, and a step
  that refers to it then plays silence there, as any missing clip does);
  the working loop.
- **Steps hold clips by name.** A step is a whole song, and a take in
  it is `{clip: <name>, ...}`, never audio. So: a clip *renamed* is
  renamed in every step (`song_undo_remap`); a clip *deleted* is asked
  about first — the clip library's delete counts the undo and redo
  steps that hold it beside the live song's takes
  (`History.song_undo_clips`, `StudioState.clip_usage`) — and a step
  restored afterwards plays silence where it was; and the Sample Lab's
  "Update clip", the one deliberate overwrite of a clip's audio, changes
  what every step holding that name plays: **undoing past an Update
  restores the reference, not the old audio.**

### Solo this cell

**Solo this cell** (the drawer's "▶ Play", `songedit.js`, `GET
/song/cell/{section}/{track}.wav`; `tests/studio/test_song_solo.py`):
one loop of the section with only that track playing — its pattern
through its own sound, FX and lanes. No second render path: the song
with every other track muted (`song_play.solo_song`, the stems export's
idea — but the track sounds even when the mix mutes it, and a solo
elsewhere does not silence it) goes through the same `section_result` a
section's loop does, at the **mix's song-wide gain** and **without the
master chain**, so the cell is heard at the level it has in the mix and
the solos of a section add up to its loop (pinned, to 16-bit rounding).
Its cache key is the solo song's own `section_key`, so it neither stands
in for the section's normal render nor moves it. The page loops it on
audio.js's master bus: it stops the song first, re-fetches on every
`song-changed` and swaps at the next loop start (an edit never restarts
the bar), draws the drawer grid's playhead (`data-cycle-bars`), and
stops when the drawer closes or shows another cell, when the song or a
loop preview starts, or on a second press. It is a real `<button>` with
`aria-pressed`; Space on it toggles (transport.js's Space ignores
buttons). It is a claim in the top dock's read-out ("Cell · Bass in
Verse", `data-solo-label`): starting it stops whatever plays.
The first audition after an edit waits for the song's gain, which
renders the edited section's mix too — the same wait Play has.
**Its button is "▶ Play", a `.btn-play`**, and **a pool pattern
has one too**: `GET /song/pattern/{id}/play.wav?track=` loops a pattern
no section plays (or not on that lane) through a lane's sound —
`StudioService.pattern_heard`: a section of its own, the pattern's bars,
appended to a copy of the song, then this same solo render of it at the
song's head gain. The lane is the drawer's **through** (`song_drawer
["through"]`, posted to `/song/drawer`; else the synth lane the dock
edits, else `song_bridge.pattern_lane`), the dock follows it, and the
dock's own ▶ plays the pool pattern through its lane too
(`track_heard`). Changing the lane keeps the loop going through the new
one (songedit.js follows `data-solo-pattern`). So a sound is designed in
the dock against any pattern of the pool, heard as each change lands.

**A swap that keeps the timing is heard where the loop stands**:
a `song-changed` whose detail says `now` (a track's sound
picked — `POST /song/track/{id}/sound` always says it —, or the sound
dock's edits, songdock.js) re-cues the solo at once, not at its next
wrap: the new render starts at the same place in its buffer and the two
cross over 40 ms (songedit.js `cue(s, now)`, as song.js's `swapNow` does
for the playing song). Any other edit still waits for the loop's start.

### The drawer's sound: change it while it plays

The drawer's head names **the
sound the pattern plays with** — its track's (`song_drawer.sound_track`:
a cell's synth track, or a pool pattern's "through" lane; none for a
beat, an audio row or a stand-in) — as a picker, `#drawer-sound`, with
✎ beside it (the track's sound dock, `edit-sound` with `dock=1`, as a
track head's ✎). The track heads are scrolled away while the editor is
open; this is the same swap from where the hands are.
- **The menu is THE sound menu**: `#sound-pick` over `#lane-sounds`
  (`fragments/sound_pick.html`, soundmenu.js — Perform's lane heads'),
  searchable, ★, ↑ ↓, Enter or a click; its head reads "<Track> · every
  section", and nothing is asked (a track holds one sound: no "replace
  this layer / the whole sound"). A pick posts the **track's** route,
  `POST /song/track/{id}/sound`: it changes that track's sound in every
  section, as the tooltip says.
- **It browses**: ↑ ↓ in the open menu swap the sound as they move (the
  row marked "now" follows), and on the closed button ↑ ↓ or the wheel
  step through the menu's order — each swap posted once the keys or the
  wheel rest (160 / 220 ms), so the drawer's ▶ Play, or the song, is
  heard through sound after sound (`now`, above: where it stands). The
  button's name changes at once; the drawer's redraw follows. Nothing
  starts playing by itself.
- **Undo: one browse is one step.** Every swap of one browse carries the
  same `browse` token (one open of the menu; one run of steps on the
  button, ended by focus or the pointer leaving it), and the route makes
  them one undo step, "sound of <Track>" (`_commit`'s `key` =
  `sound:<track>:<token>`, `window` = ∞). Ctrl+Z gives back the sound
  from before the browse; another browse is another step. A plain pick
  (a track head's menu) is a step of its own. Esc closes the
  menu and keeps what is heard; picking the sound the track already
  holds posts nothing (a pick reloads the sound, so a sound shaped in
  the dock under the same name would lose its changes).
- **Alt+S** opens it: from inside the drawer, or anywhere on a page with
  no Perform lane head (`data-keys="Alt+S"` on the button).
- **Render**: nothing new — the swap is the track-sound route, and no
  render key moves for an unchanged session.

### The jam

**A section with its own meter or tempo is not in the jam**:
the jam plays every pad on the song's one bar clock (240 / bpm), so the
scene and its pads are `aria-disabled` and their titles say why
(`song_timed` in the context).

**The jam** (`static/jam.js`,
`fragments/jam_bar.html`, `studio/jam.py`, `routes/song_jam.py`;
`tests/studio/test_jam.py`, `test_jam_schedule.py`,
`tests/browser/test_input_matrix_jam.py`): "◉ Jam" in the topbar (Alt+J)
puts a **launcher** in the playlist's place — a column a section (a
*scene*), a row a drums or synth track, a *pad* a cell that holds a
pattern. It is a mode of the Song page, not a view: `jam-on` on `#view`
(jam.js) and style.css section 29 swap the launcher for the arranger's
other children. The launcher is included inside `#song-arranger`, so
every song edit redraws it with the playlist and jam.js lays what sounds
back over the new markup; it is rendered only when a pad would be, so an
empty song keeps the arranger's own empty states. **No server state, no
region, no route but the capture's**: the mode and the quantise are
`localStorage` (`synthlab.jam`); what sounds, what is queued and the log
are the page's and end with it. (Not the Loops page's "⏺ resample", which records
the master bus to a clip: `sampler-and-clips.md`.)

**Playback is "Solo this cell", many at once.** A pad's audio is `GET
/song/cell/{section}/{track}.wav` — the section's cycle with that track
alone, at its level in the mix, no master chain — as one looping
`AudioBufferSourceNode` a sounding track, through that track's gain and
analyser (the row's meter, a real one) into audio.js's master bus.
Nothing here is a render path, a plan or a key. What follows from that,
and is said on the page ("tracks before the master chain"): no master
chain and no returns; a ducked bass ducks to its own section's kick;
takes and whole stems do not play; mute and solo are ignored (the cell
render ignores them); a pad plays at the track's level and has no fader.
A bar is `240 / bpm` seconds and a loop is its decoded buffer — the
launcher never reads `/song/plan.json`.

**Launching is quantised, in groups** (`jamSchedule`, the pure half, run
under node). A press makes one group — a pad (one track), a scene's head
(every row: its pad's section, or a stop where the row has no pad
there), a row's ■ (a stop) — due at the next quantise point: the next
bar, the next fourth bar, or where the sounding loop comes round (that
track's own; for a scene, or a silent track, the longest sounding). A
group's members start on the same sample, and the loops they replace
end there (a 6 ms fade: **a tail is cut at a switch**). A group whose
buffers are not all decoded by its point moves to the following one —
never late, never off the grid. **From silence there is no grid**: the
first group starts as soon as it is decoded and its start is bar 0; a
second press before it sounds joins that group. The scheduler looks
120 ms ahead on a 25 ms timer (so a press in the last 120 ms of a bar
starts on that bar line without ever showing as queued). **Stop all is
immediate**, not quantised. The song stops when a group is queued
(`transport.take`: so do the loop preview, the drawer's solo and an
`<audio>` player); the jam stops when the song or the drawer's solo
starts, and when the page is left. The top dock's read-out names what
sounds ("Jam · Verse", its bar) and what waits ("next: Chorus · 3
beats", loading… before the first decode), and each waiting pad, scene
or ■ carries the count in a filling ring (`data-wait`, `--q`:
transport.js; `frontend.md`, "The transport is the same on every page"). After a song edit the loops that sound play on (old audio) and
every buffer is dropped, so the next launch is the new render; a tempo
change forgets the jam (its bars are not those bars).

**The log** is every group that fired: `{bar, track, target}` (a pad),
`{bar, track}` (a row's stop), `{bar, scene}` (a whole column), `{bar}`
(stop all); bars count from the first launch and **the clock stands
still while nothing sounds**, so a pause is not bars of rest. It is
always on — there is nothing to arm — and holds `MAX_JAM_EVENTS` (512;
past it the page says so once and logs no more). "✕ forget" stops and
empties it.

**Capture** (`POST /song/jam/commit`: `events` as JSON, `bars` — the
log's length, the bar in progress counted whole) appends the jam to the
song through one `_commit`: ONE undo step ("capture the jam"), the
regions by their watches, `jam-captured` in the `HX-Trigger` (the page
drops its log and leaves Jam). A refusal is a 422 toast with no trigger,
so **the log is kept**. `jam.timeline` reads the log as parts — a new
one wherever the set of sounding cells changed, a relaunch included —
and `jam.commit` writes a section a part:

- named and coloured for its section when every sounding track came
  from one ("Drop"), else "Jam N";
- **a pattern by reference** where it starts on its own first bar —
  whole, or cut short by the part: a section its patterns do not divide
  is one cycle, so it plays the pattern's first bars and ends (a window
  there would leave the pool a "↻" pattern a part, each used once); **a `songops.window`** of it ("… ↻",
  a new pool pattern, one a (pattern, phase, length)) where the track was
  joined mid-loop — so the section plays the bars the launcher looped,
  held bar for bar by `test_jam.py`;
- **repeats** where every loop starts on its first bar and the part is a
  whole number of them (a scene left to loop four times is its section
  ×4); past `MAX_SECTION_REPEATS` another section carries on;
- 5-7 bars holding an 8-bar pattern are two sections (4 + the rest), a
  part longer than `MAX_SECTION_BARS` / `MAX_SECTION_SECONDS` several;
- **lanes and trimmed blocks only with a whole pass** (the same length
  as the source section, from its first bar): elsewhere the block plays
  untrimmed and without the lane, and the toast says one stayed behind.
  A pattern whose length does not divide its section's is also only
  exact for a whole pass.

Refused whole, never truncated: a log past the cap, unreadable JSON, an
id the song no longer has, an id the schema would have dropped (as
"none" it would read as *stop everything*), more parts than
`MAX_SECTIONS` has room for (the message says how many fit), a song past
its length caps. What a capture cannot hold is the launcher's cut tails:
at a section boundary a stem rings on.

**Scenes from elsewhere**: `jam.timeline` / `jam.commit` take `scenes`, sections that are
not in the song — Perform's ⟲ Capture makes each distinct scene it
logged into one (`add_scene_section`, then takes it back out of
`song["sections"]`: its patterns and tracks stay, the section itself is
never added) and logs `{bar, scene: <its id>}` / `{bar}`. They are read as
the song's own sections are; a captured part that came from one source
takes its `meter` and `bpm` too (the launcher plays every scene on one
clock; the song's own sections keep none in a jam — a timed one is not
launchable). `POST /fragment/perform/scene/capture` (`routes/slots.py`):
`events` `[{bar, slots: [5 loop names | null], name}, {bar}]`, `bars`;
refused whole on anything else, a loop gone or the 8-track cap;
`scenes-captured` in the answer.

**The grid is one tab stop** with a roving focus over real buttons
(heads, pads, the rows' ■): arrows step over an empty cell, Home / End
go to the row's ends, Enter and Space are the button's own click
(transport.js's Space ignores buttons), Shift+Enter the pad's scene,
Backspace / Delete its track, Escape stop all (while no drawer is open:
Escape closes that first). Alt+J, Alt+1..9, Alt+0 and Alt+C work from
anywhere on the page but a field; jam.js hears keys in window capture so
piano.js never plays Alt+J as a note. Leaving Jam from the grid puts the
focus on the switch. `window.synthlab.jam` (`on`, `events`, `bars`,
`t0`, `bar`, `sounding`) is read-only state for tests and other islands.

Costs (a trance and a filter-house starter): a rendered pad 0.2 s
median and up to 2.3 s; a cached one 9 ms; **the first pad of a song
that has never been played 4-15 s** — a cell is rendered at the song's
gain, which is the whole song's render (a starter has played itself by
then; a reload after a server restart has not). Jam on prefetches the
first two scenes and a launched scene the next, one cell at a time; the
page keeps 64 decoded cells (a 16-bar one is ~11 MB). Each cell render
raises the section render's own "Rendering …" notice.

## Song view

**Song creation on this page** (`songcraft.md`): an empty
playlist is the **starter sheet** (`fragments/song_start.html`: a card a
style, a Start that lays down the whole song and plays it; with a song
it is the topbar's "Start…" popover); the **chord track** — its tools
in the playlist's tool bar (`chord_strip.html`) and its chords as tiles
on the time axis ("Playlist tools" below); the topbar's
song title, tempo and key (`POST /song/meta`), "⚄ Song" and the
checklist's "✓ N" (`song_check.html`); a track's ⋯ menu: its role, ⚄
Again / busier / sparser, "write…" a part with a generator; the
drawer's row of the same for the open cell (the page's commands "Roll
this part again", "Make it busier", "Make it sparser", "Add a fill",
"Add a build"). The arranger's and the topbar's watches carry the key,
the chord track, `gen` and (where the sheet is drawn) the working
loop's size.

**Song view** (`views/song.html`, layout A): transport (`song_topbar`),
pool (`song_pool`), the playlist (`song_arranger`), drawer
(`song_drawer`). Grid areas `head / top / pool / arr / drawer` at both
breakpoints (`test_song_names_every_grid_area_at_both_breakpoints`); at
<= 900px the pool is a declarative-popover bottom sheet. The section /
track / take menus are popovers too (the table's scroll box would clip a
`<details>` panel). The htmx swap to `/view/song` cues no loop preview
(every other view's swap does): the page plays the plan, so that render
was pure cost. Its regions build `song_context` only when a context
names the `"song"` section (`service.SONG_NEEDS`).

**Tiled to the window in the dense tier** (`dense.css` D18, from
901 px). The view is exactly as tall as the window
under the top dock and nothing on it scrolls the page:

- **the pool** is a full-height column (its splitter still drags
  `--pool-w`);
- **the playlist's pane** (`.song-arranger`) holds the lanes
  (`.arr-scroll`, a size container that scrolls both ways), the **master
  strip** beside them, and under them the whole stems' rows and the foot.
  **A lane is its share of the pane's height**: `.arr-cell`'s height is
  `clamp(40px, (100cqh - 66px) / --tracks - gap, 168px)` (`--tracks` is on
  `#song-arranger`), so four tracks are four tall lanes with readable
  thumbnails and sixteen are sixteen short ones, with no script. The
  table is `max(100%, its bars)` wide and its last column takes the rest:
  the time axis runs on to the pane's edge;
- **the editor is docked** (`#song-drawer`): under the playlist. It is a
  pane of the grid, so opening a cell scrolls nothing (songcell.js's
  `scrollIntoView` finds it in view). While no cell is open the pane is
  `.drawer-idle`, one line that says what it is for, and the lanes take
  the height (from 2200 px a narrow column beside the playlist);
- **the open editor fills its pane, never the page** (a 1-bar riff
  would otherwise take ~70 % of the height). Its row is `fit-content(55 %)`
  under the playlist's `minmax(min(396px, 45 %), 1fr)` — at most 55 %, so
  the playlist keeps 45 % at least; a dragged `--drawer-h` is held to
  55 % too. From 2200 px it stands **under** the playlist as at 1920 (beside
  it, its rows would end a third of the way down); the drawer's
  ⬓ / ◨ (`.drawer-place`, layout.js's tabs: `data-tab="beside"` on
  `#view`, per browser) puts it beside again. Its tools wrap into as few
  rows as the width allows (`.song-drawer` is a wrapping flex there; the
  piano roll's bar and the arp share a row: the form is `display:
  contents`). **A step is at most 48 px** (`--step-max` on the drawer's
  grid: the pane's share — `100cqi` of `.drawer-grid-wrap` less the row
  head, over `--steps` — held to 48 px; a fixed length, since a row is
  max-content wide and a 48 px cap against `1fr` drew an 8-bar pattern
  48 px a step and scrolled it. The step grids' columns are
  `minmax(--step-min, var(--step-max, 1fr))` everywhere, so nothing else
  moves), and **the roll's rows share the
  pane**: pagefill.js sets `--roll-row` (14-26 px) from what the pane may
  take — 55 % of the view, or less where the playlist's minimum binds; the
  pane itself when beside or dragged — less its tools and lanes. Right of
  a short pattern's last step the pattern is drawn again as **ghost
  repeats** (`.roll-ghost`, one canvas in the grid: its notes at fading
  alpha, the empty steps as wells, a bar line where each repeat starts),
  redrawn after a settle, a step's input and a resize, sliding in with
  GSAP when a pattern opens. A long pattern's steps simply share the width
  (8 bars of 16 at 2560 are ~17 px a step), so it has no room for ghosts;
- **the master strip** (`#song-master`, inside the arranger's region so
  it follows the tracks): one input a track — an LED (on / solo / off),
  the name, its level as a 2 px bar — a server-drawn SVG of leads into
  the bus (`.master-leads`: a row is 18 units, a muted track's lead is an
  unplugged one, and a track whose FX hold a `pump` / `sidechain` gets a
  red lead from the drums' input), and one `.meter-v`. **The meter is the
  output's, not a track's**: it reads `--live`, which motion.js already
  writes ~15 times a second on every `.panel` (the strip is one), handed
  down as `--out` (an unregistered property inherits; `--live` does not).
  No script, no new analysis. **There is no live level per track to
  meter**: the player schedules whole mixed passes (`/song/plan.json`),
  so a per-track meter would need a render per track; the strip shows
  each track's set level instead and says so in its tooltip;
- **track heads**: mute / solo / arm, the level as a `.knob` (still the
  `input[type=range][name=gain]` that posts `/song/track/<id>/mix`; its
  value beside it), "fx N" — the track's FX count, and the way to them
  (a button: a synth's or the kit's chain is the sound's, so it opens the
  sound dock; an audio track's opens its rack in the drawer) — and a
  short lead (`.track-path`) towards
  the master. Beside a lane's sound menu a long name gives way first
  (down to a few letters) so the menu keeps nine.

**Context menus** (a right click, which is also a pen's barrel button):
grid.js opens the popover a pressed element names in `data-ctx`,
anchored to it (`showPopover({source})`). A section head and a track
head open their own ⋯ menus; a cell opens `#cell-menu` (one for the
whole playlist: the opener fills its hidden `section_id` / `track_id` /
`len` from the cell's `data-*`, and `data-ctx-state="empty"` hides the
rows that need a pattern). Every row posts a route the drawer or the
keyboard already posts. Never on a field, and never on a step of a grid
(a right click erases there).

Comfy and touch keep the flowing layout (style.css 9b5): no master
strip, no idle pane, the drawer under the arranger.

### The playlist

**The playlist** (`fragments/song_arranger.html`, `service/songpage.py` `ruler` / `blocks`
/ `take_bars`, `static/songline.js`; `tests/studio/test_song_playlist.py`,
`tests/browser/test_song_playlist.py`). The arranger grid drawn on a true
time axis — one region (`song-arranger`), one `<table>`, the ids
(`sec-<id>`, `trk-<id>`, `cell-<sid>-<tid>`) and `data-*` the keyboard
grid, the cursor marks, the drawer and recording read. There is no Grid /
Playlist toggle. A column sized `base + 22px × bars` with repeats as a
label would draw the 16-bar ×2 chorus of a 64-bar song as half the song
and a quarter of the picture.

- **One variable.** `--bar-w` (px a bar) on `.song-view` — which no
  region swap replaces — `24px` in CSS so the first paint is right
  without script. Every x on the page is bars × `--bar-w`.
- **Columns.** One `<col>` a section, `bars × repeats × --bar-w` wide
  (its `--bars` / `--reps`); the table is `table-layout: fixed` with no
  horizontal `border-spacing`, so a bar is the same width everywhere and
  a section boundary is a line inside the cell. Measured in the browser
  suite to half a pixel.
- **Ruler.** The first header row: `#song-ruler` across the sections'
  columns. Ticks are a `repeating-linear-gradient` (a line a bar, a
  stronger one every 4; beats when zoomed to 48 px or more), numbers a
  `<span style="--b: N">` every 4 bars from 1 — the step doubled until
  there are at most `limits.MAX_RULER_NUMBERS` (256), and every other one
  hidden at 8 and 12 px a bar. `.ruler-cap` marks the 240 s cap: the bar
  line after bar `bpm` exactly (a bar is 240 / bpm seconds), drawn when
  it falls inside the ruler or within 16 bars of its end.
- **Section heads** under the ruler: the first pass in the section's
  colour (name, `8 bars ×2`, ⋯), each later pass paler with its number
  (`.sec-pass`), `.sec-resize` on the right edge. A container query on
  the first pass drops the length text, then the name, as it narrows (a
  1-bar section at the default zoom is all menu button; its `title` has
  the name and bars).
- **Blocks and echoes.** A filled cell's `.cell-btn.blk` — the button
  that opens the drawer — sits at `--at` for `--len` bars of the first
  pass (the whole section unless the block is trimmed: "Block spans"),
  with the pattern's name, its shared `×N`, `∿`, and its thumbnail tiled
  `len / pattern bars` times (cut, not squeezed). Each later pass draws
  an `.echo` (`aria-hidden`, `pointer-events: none`, 45 % opacity): the
  same tiles again (one `<use>` of the block's `<g>`), no text; a click
  on an echo falls through to the cell. `.blk-edge-l` / `.blk-edge-r`
  are the block's edges. An empty cell offers `+` across the first pass.
- **The drawing bound.** A legal song is at most 200 bars (240 s at
  200 bpm), so a lane is at most 200 thumbnail tiles; an over-cap import
  can be far longer, so `songpage.blocks` counts tiles and stops at
  `limits.MAX_PLAYLIST_TILES` (2048): blocks past it are plain colour.
  Nothing is ever drawn per step.
- **Takes.** `.take-chip` at its bar, `take_bars` wide — its clip's
  seconds × bpm / 240, two decimals, from the WAV's header — cut at the
  section's end with `.take-cut` (the render cuts it there), echoed like
  a block. Its menu is a popover (`.take-menu`: rename, "starts at bar",
  Use as sampler, Remove). The arranger's watch holds the song's tempo
  and the file stats of **the clips the song holds as takes** (an
  "Update clip" can change a length; another clip in the store is not
  this region's business).
- **Narrow tiers.** <= 900px: the same timeline, scrolled sideways,
  beside a 96 px track head (name; M S ● under it; the level moves into
  the track's ⋯ menu). Coarse pointers: an
  edge handle is a 28 px target with `touch-action: none`.

**`static/songline.js`** (the Song island's sixth file, <= 300 code
lines; its pure half `songLine` — `barAt`, `zoomStep`, `fit`, `passes` —
runs under node in `test_song_playlist.py`):

- **Zoom**: 8, 12, 16, 24, 32, 48, 64, 96 px a bar (a coarse pointer:
  from 16), from the `−` / `fit` / `+` buttons in the ruler's corner and
  **zoom.js's gestures**: `#arr-scroll` is a
  `[data-zoom="song"][data-zoom-own]` scroller, so Ctrl+wheel about the
  pointer (a touchpad's pinch, summed), a two-finger pinch, Ctrl+= / − /
  0 arrive here as `zoom-step` and the playlist and the piano roll zoom
  by the same hands; a plain wheel and Shift+wheel scroll and never
  change a value (the bar under the pointer stays put).
  "fit" is the width that shows the whole song in the lanes, to a
  quarter px, inside the steps' range (the smallest when none does), not
  the largest step under it, which would leave up to a fifth of a wide
  window empty. Per browser (`localStorage`); a stored value that is not
  a step (a fit) is fitted again, and a first visit opens at "fit" held
  to 16..48.
  `[data-bar-w]` on `.song-view` is what the ruler's CSS reads — the step
  at or under the width, so a fit draws its ruler as that step does (not
  `data-zoom`, which would make the whole view a `zoom.js` scroller named
  by its width: Ctrl+wheel over the top bar or the pool would write an
  unused `--zoom-24`).
  `#arr-scroll` is `touch-action: pan-x pan-y`: one or two fingers pan
  it natively and a pinch is the playlist's zoom, not the page's.
- **The start marker**: the ruler is `role="slider"`. A click (or tap)
  puts `.ruler-mark` on a bar and selects the section under it; ← / →
  move it a bar, Home to bar 1. Play, Space and Enter on the ruler start
  from it (the cue beside ▶ says "the song from bar N · Section",
  transport.js; ■ brings the marker back to the bar ▶ started from, ⏸
  moves it to where the song paused): `S.songFrom()` is how many **bars** into the SELECTED section
  to start (0 when the marker is elsewhere) and song.js's `play()` turns
  them into the plan's own samples (`cycle_samples / cycle_bars`) for
  `start`'s `from`, which starts mid-pass. A click while the
  song plays jumps there; it never starts playback by itself. Selecting
  another section (a cell click, the cursor) moves the marker to its
  first bar. What loops is a section, or a range of them ("Playlist
  tools" below). Stopped, the topbar's place (`#song-position`, "bar 41
  / 128 · Verse B") is the marker's; playing, the playhead writes it.
- **The playhead** (song.js): `songSchedule.locate` also returns `at`,
  bars into the section across ALL its passes, so it sweeps a repeated
  section once. **Follow**: an
  `IntersectionObserver` on `.song-playhead`; when it leaves the
  scroller on the right the scroller pages on by 80 %, and when it is
  behind (a loop wrapped) the scroller goes to it. **A scroll holds it
  off for 4 s** (`HOLD`: the wheel, a finger, the scroller's own bar),
  then it looks again; **a click on a block does not** (were any
  pointerdown in the scroller to count, for 1 s, a playhead that left
  within a second of a click, which every edit is, would never be
  followed again and run on off-screen: the observer only speaks when the
  intersection changes). No per-frame work.
- **Edges** — one table (`EDGES`) over one pointer handler: the pointer
  is captured on the first move (a click on a handle is still a click),
  only CSS moves during the drag plus a tag on the handle (`×3`,
  `12 bars`, `bars 3–7`, `→ Chorus bar 2`), the release posts once, a
  refusal (a toast) gives the edge back, Esc cancels.
  - **A section's right edge** (`.sec-resize`): by whole passes it sets `repeats`
    (`POST /song/section/{id}/repeats`, absolute); **Alt** held sets
    `bars` (`/bars`). Repeats is the plain drag because it re-renders
    nothing (the cycle is cached). Form path: the section menu's bars
    field and repeat ±.
  - **A block's edges** (`.blk-edge`, `POST /song/cell/span`): the left
    one moves where it starts, the right one trims or extends it; past
    the section's last pass it spills into the following sections
    ("Block spans"). Double-click: fill the section. Form path: the
    drawer's `from` / `to` fields and **Fill section**
    (`.drawer-span`, `.drawer-fill`); keys, on the focused grid:
    **Alt+← / →** slide the cursor's block a bar, **Alt+Shift+← / →**
    move its end (window capture and `preventDefault`: Alt+← is the
    browser's Back). The grid keeps the keyboard through the redraw.
  - **A whole stem's edges** (`.long-edge`): "Long audio" above.

### Playlist tools: structure, ranges, the energy lane, chord tiles

**Playlist tools** (`studio/songops.py`, `routes/song_arr.py`, `static/songsel.js`,
`songline.js`; `tests/studio/test_song_ops.py`, `test_song_arr.py`,
`tests/browser/test_input_matrix.py`). Each edit below is
ONE `_commit` (one undo step), pure in `songops.py`, and refuses with the
usual toast.

**The tool bar is one line on a laptop** (dense.css D27): from
901 to 1599 px the words of its glyph buttons (`.tool-word`: "▭ Select",
"⟳ range", "≋ by energy", "⚄ chords", "progression") are visually hidden —
still their accessible names, the titles say the rest — so the lanes keep
the row a second line would take: with a cell open at 1366 x 768 that
leaves the lanes about two and a half rows. The editor pane's top seam
is a splitter (`--drawer-h`, layout.js) with its grip drawn. A cut's toast names the extra cut first and folds the
effects that restart into one note naming their tracks
(`song_arr._not_exact`).

**The timeline** is every bar of every repeat, 0-based in the model and
1-based on the page (`songops.starts`, `locate`). The ruler's marker is
the bar cursor: its bar rides every "at the marker" post as the hidden
`#mark-bar`, and it keeps its bar when a split or an insert moves the
sections under it.

- **Cut at a bar** (`songops.cut`; `POST /song/bars op=cut`, `POST
  /song/section/{id}/op op=split`; **S** on the grid or the ruler, "✂
  Split", a section's ⋯ menu). A bar that starts a section already is no
  edit. A bar inside a section becomes a boundary:
  - **repeats** are divided first — the passes before the cut's stay one
    section (it keeps the id), the cut pass becomes two, the passes after
    it one: the same bars in the same order, so nothing on the timeline
    moves. A cut between two passes only divides the repeats;
  - **patterns** stay shared by reference wherever they still fit. Where
    the cut falls inside a pattern, the right half plays a **window** of
    it (`songops.window`: a new pool pattern, "Name ↻", the same notes
    from the cut's phase, a cut written where the pattern's own start
    lands so a repeat still attacks); a half shorter than its pattern
    plays a window that long. A pattern is 1, 2, 3, 4 or 8 bars, so a cut
    that would leave 5, 6 or 7 bars of an 8-bar pattern cannot stand
    alone: **the pass is also cut four bars on (or back, or both)** —
    whichever leaves every part a length a pattern can have — and the
    toast says so ("Also cut at bar 7 (Lead A is 8 bars and cannot be
    6)."). So bar 3 of an 8-bar part gives 2 + 4 + 2 bars. Measured on the
    four starters: every bar of every song cuts (without the second cut, a
    third of them would not). Refused, naming the pattern, only when no
    such cut helps. `unique=1` ("… with its own patterns"): the later
    parts' shared patterns are copies;
  - **spans**: a block wholly on one side stays there (the other half's
    cell is empty); one across the cut is two blocks, the right one a
    window from ITS phase;
  - **lanes** are divided at the cut by linear interpolation, at the
    sample `curves.lane_array` reads (`lane_halves`: sample i sits at
    i / (n - 1), so the naive k / bars is a sample off by the end of a
    half) — the same curve, sample for sample; never more than
    `MAX_LANE_POINTS`;
  - **takes** go to the half their bar is in; one that would play across
    the cut **refuses it by name** (a section's end cuts a take);
  - **the section's chords and energy**: both halves keep the energy;
    the right half gets the chord track in force rotated to the cut's
    phase (`rotate_chords`), so the harmony reads on from where it was;
  - **whole stems**: untouched — a cut moves no bar.
- **Exactness** (pinned in `test_song_ops.py` through `svc._song_raw`,
  largest sample difference): every bar of an 8-bar section under 1-bar,
  2-bar and 4-bar patterns, once and twice, **4e-16**; chunked 4e-16;
  with trimmed blocks 4e-16; with takes 4e-16; under a whole stem < 1e-9;
  through a reverb, a filter, an EQ, a comb 1e-13 or better; under a lane
  with nothing ringing over the cut < 1e-9. **Not exact, and the cut says
  so in its toast** ("Not exactly as before: …"):
  - what RINGS over the cut under a lane (a release, a tail) is held at
    the lane's value at the cut instead of following it on: measured
    -50 dB of the mix's peak or lower on a pad that rings 0.7 s under a
    lane crossing its whole range;
  - an effect that is not linear and time-invariant restarts or re-reads
    at the cut (`songops.LINEAR_FX` lists the exact ones): a phaser -13 dB,
    a drive only where two rings overlap; and **a delay's echoes stop
    where a stem's ring does** (four beats and a second past its last
    note, `render._cycle_stems`): -48 dB;
  - a note held across the cut attacks again, a slide into the first
    note after it starts again, an arp counts from the cut, a sound's own
    drawn curve restarts with the pattern on the right, a beat's
    humanise is drawn again (the roll is seeded from the pattern);
  - at a tempo where a bar is not a whole number of samples each
    section's cycle is rounded on its own, so a cut
    can move what follows by one sample.
  The master chain is per section (its LFOs restart at every boundary:
  `song-render-export.md`): a cut is one more boundary for it.
- **Unroll** (`songops.unroll`; a section's menu, "Unroll ×N"): a section
  that plays N times becomes N sections that play once — the same
  patterns by reference, its lanes, spans, takes, chords and energy on
  each. **N sections, not one of N x the bars**: a section with a lane, a
  take or a span is one pass of its whole length, so one long section
  would stretch the lane over N passes, need N copies of every take and
  block (the model holds one block a cell) and have to fit
  `MAX_SECTION_BARS` and `MAX_SECTION_SECONDS`; N sections meet only
  `MAX_SECTIONS` and render from the cycle already cached. Exact: 0.0
  with a level lane, a reverb and a trimmed block (an FX-parameter
  lane's two rings differ between "own next pass" and "another section":
  that one case is not).
- **Insert bars** (`songops.insert_bars`; `POST /song/bars op=insert`,
  a section's "4 bars ← before / after →"; **I**; "＋4 bars"): a new
  EMPTY section — silence on every track, into which the section before
  rings out. A bar inside a section cuts it first. **Delete bars**
  (`delete_bars`, `op=delete` `bar` .. `to`): the sections between go,
  the ones at either end are cut first, and a window made only to be
  deleted is not left in the pool. Both move the timeline and nothing
  else (pinned: the samples before the bar are the same, the samples
  after it the same N bars later). **Whole stems stay at their bar** —
  the arrangement moves under them: the confirm on the insert buttons
  and every such edit's toast name the stems (`songops.long_under`).
- **Duplicate as a variation** (`songops.duplicate(unique=True)`; the
  section menu): "Duplicate" still shares every pattern; this one gives
  the copy patterns of its own — one copy a pattern, so two tracks that
  shared one still do. **The shared state is visible**: a block shared
  with other sections says `×N`, and a section that shares
  EVERY block with another shows ⧉ beside its name, naming them
  (`songops.twins`).
- **A range of cells** (`POST /song/cells`: `cells` = "section:track,…",
  at most `MAX_RANGE_CELLS` (96), refused whole past it; `op`):
  `clear` (blocks with their lanes and spans, an audio cell's takes);
  `copy` / `move` to (`to_section`, `to_track`) — by reference, exactly
  (an empty cell of the range empties where it lands), a span with it, a
  MOVE along a track carries lanes and a copy never does (`move_cell`'s
  rule), a track of another kind refuses the whole edit, sections are
  added past the end as needed; `duplicate` (the range again, straight
  after itself: it pastes OVER what is there, it does not insert time);
  `unique` (patterns of its own: one copy a pattern played outside the
  range); `variation` (the same, varied); `again` (its generated patterns
  rolled, copied first when shared outside); `fill` (`kind`: a generator
  written into it — `build.write`). A copy, a move and a duplicate answer
  with `song-range`, where the range is now, and the page's selection
  follows — so Ctrl+D again carries on to the right. Rolling again is the
  same undo step, and what a generator wrote plays.
- **Making a range** (songsel.js; the gesture table in
  `keys-and-midi.md`): Shift+click a cell (from the cursor), a drag that
  starts on an EMPTY cell, Shift+drag from a block, Shift+arrows on the
  grid, or **▭ Select** — range mode, the touch and pen path, where a tap
  is a one-cell range and a drag a box. A selected cell is `.sel` and its
  context menu is the range's (`#range-menu`). Delete clears; Ctrl+C then
  Ctrl+V at the cursor copies; Ctrl+D duplicates; Alt+arrows move it;
  Esc, a plain arrow or a press elsewhere on the lanes ends it.
- **The energy lane** (`section["energy"]`, 0..1, absent until drawn;
  `POST /song/energy`): a row under the heads, a bar a section — as wide
  as the section, as tall as it is full, hatched while undrawn. Each is a
  native range: drag along it, arrows, the wheel when focused, a typed
  value; one tab stop (the selected section's). **It renders nothing and
  is in no key.** It is what (a) a boundary's `+` defaults from, (b) the
  checklist reads — a stretch where nothing changes for 32 bars under an
  energy lane that steps says so, and its fix is — (c) **"≋ by energy"**
  (`songops.arrange_by_energy`; `op=arrange`): every section with an
  energy is made as full as it is drawn by muting, thinning and filtering
  the FULLEST section's parts — `gen/build.arrange` with one row a
  section (`energy_row`, `ENERGY_ROWS`: no drums and pads alone under
  0.2, hats, no kick, the full beat, everything from 0.9; a low-pass
  opening into a fuller section or closing after one). Nothing is
  written. The fullest section is the source and is left alone; an
  arranged section keeps its id, name, colour, length, repeats, takes and
  own chords and LOSES its own blocks, lanes and spans (the button
  confirms; Ctrl+Z takes it back); the timeline never grows (a part
  longer than a section sits out; the 8-bar beat-with-a-fill gives way to
  the beat). Arranging again is the same undo step and adds nothing to
  the pool.
- **A transition on a boundary** (`POST /song/boundary`, `section_id` =
  the section AFTER it; the `+` at the left end of every section head but
  the first, **T**, "＋ transition"): `#bound-menu` lists the eight
  placements (`songops.BOUNDARY_KINDS`) under one suggestion from the two
  sections' energy (`boundary_moves`: rising by 0.15 or more — a riser
  that ENDS on the last bar line of the section before and an impact on
  this one's first bar; falling — a downlifter off the bar line; level or
  undrawn — an impact). The clips and their placement rule are
  `/song/section/{id}/transition`'s own (`_transition_spec`,
  `_transition_clip`), as ONE undo step however many clips, and
  auditioned: the answer carries `song-audition` and the page plays from
  two bars before the boundary.
- **Chord tiles** (`.arr-chords`, `songpage.playlist_context`): the chord
  track on the time axis, a tile a chord at its true place and width over
  each section's first pass (its repeats echo the line), the section's
  own chords when it has them (teal), else the song's (amber — an edit
  there changes every section that plays it). A click or Enter opens the
  tile's menu (`#chord-menu`: captioned with the tile's chord and bar,
  its degree, quality and inversion pressed — the tile's `data-ctx-state`,
  menu.js's `[data-is]` — then split in two, remove); zoomed out (16 px a
  bar or less) a tile is its numeral between hairlines (the amber edge a
  chord made the lane a hatching); on a focused tile 1-7 set the degree, Delete removes, ← → /
  Home / End move between tiles (one tab stop); a tile's left edge drags
  where its chord starts, by beats (`op=edge`, `songops.chord_edge`: the
  chord before it gives way). The route is `POST /song/chords`
  (`songcraft.md`) with the ops `edge` and `split`; the strip keeps the
  progression's own tools (`fragments/chord_strip.html`, in the tool bar
  `#arr-tools`).
- **Looping a range, jumping a section**: `song-render-export.md`, "A
  range loop"; **L** loops the sections the range spans (or the heads
  Shift+click marked — drawn as a brace on the ruler with a locator at
  each end, solid while it loops); Ctrl+← / → and ⏮ ⏭ jump a section (the
  marker and the cursor go with it; while playing, at the next pass
  start).
- **A section's menu stays open** across its own ± (`data-stay`:
  songcell.js shows the redrawn popover again, the pressed control under
  the keyboard).
- **Commands** (each a `data-cmd`: the palette, a Stream Deck key — the
  deck's fifth page): Next / Previous section, Loop the selected range,
  Split at the marker, Insert 4 bars at the marker, Add a transition
  before the selected section, Arrange by energy, Select cells: range
  mode on / off, and Duplicate / Make unique / Vary / Roll again / Clear
  the selection.
- **Not built**: drawing a lane (the drawer writes 2-3 points); a bar-exact loop without a cut; dragging a
  locator; "delete these bars" from the range's menu (the route is
  there: `op=delete`); a long press THEN a drag as a range (range mode is
  the touch path); a cut of an 8-bar pattern that leaves it whole on one
  side (it takes a second cut).

### Pattern making

**Pattern making** (`tests/studio/test_song_view.py`,
browser-driven):
- **Thumbnails**: every pool pattern's `_thumb` is drawn once as an SVG
  `<symbol id="thumb-<pid>">` in the `song-thumbs` region
  (`fragments/song_thumbs.html`, out of the grid's flow: `.song-thumbs`
  is absolute, it has no grid area). A filled arranger cell is its name
  over `<use href="#thumb-…">`, once per time the pattern plays across
  the section's bars (viewBox width = section bars / pattern bars, so a
  pattern that doesn't divide the section is cut, not squeezed); the
  drawer's pick list uses them too. A step edit therefore re-sends the
  symbols (and the pool), **never the arranger** — its watch holds a used
  pattern's name, colour and bars, not its notes. The pool still draws
  its own rects.
- **Shared patterns**: a cell whose pattern more than one section plays
  carries `.cell-shared` ("×N", and "shared by N sections" in its
  accessible name); the drawer's `.drawer-used.shared` reads "shared ·
  edits change N sections" with the section names in its title
  (`song_drawer.shared_in`, which is why the drawer's watch sees a
  section rename); "Make unique" is `primary` then and **disabled when
  only this section plays the pattern** (forking it would just orphan the
  original in the pool).
- **Filling a cell**: `POST /song/cell/new` (`sm.new_cell_pattern`) makes
  an empty pattern of the track's kind, puts it in the cell and opens the
  drawer — the empty cell's drawer offers it ("+ New beat / note
  pattern") beside a `.pick` button per pool pattern of that kind (name +
  thumbnail, `POST /song/cell`); a filled cell's tools have "+ New" and
  "Clear cell". It takes **no `name`**: `htmx.ajax` with no source sends
  every field on the page along (the drawer's rename box, the section
  menus), so a keyboard post would name the pattern after whatever was
  open. New note patterns open at 16 steps a bar (`sm.NEW_NOTE_STEPS`;
  the schema default stays 4, the loop's and an older file's). `POST
  /song/pattern/{id}/duplicate` (`sm.copy_pattern`, the pool drawer's
  "Duplicate") copies a pattern into the pool and opens the copy.
- **Drawer order**: head, the cell's actions, `.drawer-rack` (length +
  tools, one wrapping row), the draw / erase / cut hint, the grid, then
  the automation lanes (above it, they would push the grid below the
  fold at 1440x900).
- **`static/songedit.js`** (<= 110 code lines): the undo keys and the
  solo audition, above ("Undo and redo", "Solo this cell").
- **`static/songcell.js`** (island 19, <= 110 code lines; song.js is full):
  `.same` on every cell and pool entry playing the pattern under the
  pointer / the arranger cursor / the drawer; on the focused arranger
  grid N = new pattern in the cursor cell, Ctrl+C / Ctrl+V = copy a
  cell's pattern to another by reference (window capture, like D); a
  newly opened drawer is scrolled into view (`.song-drawer`'s
  `scroll-margin-top` clears the sticky dock); after a region swap focus
  goes back to the drawer control that was pressed (matched by tag,
  aria-label, name, hx-post, hx-vals, text) and `#arr-scroll` / the
  drawer grid keep their `scrollLeft`. It loads after song.js on
  purpose: its MutationObserver callback runs before song.js's queued
  `sync`, which only refocuses the arranger when focus is still lost.
- **`static/songdrag.js`** (island 23, <= 140 code lines): every drop
  on a lane — "Drags onto a lane" below.
- **`static/songline.js`** (island 27, <= 300 code lines): the time
  axis — "The playlist" and "Playlist tools" above.
- **`static/songsel.js`** (<= 120 code lines): the range of cells and
  the range loop's pure half — "Playlist tools" above.
- **htmx's settle resets an id'd element's class** to the response's, so
  every mark a script adds to a cell or section head (`.cursor`, `.open`,
  `.selected`, `.same`) vanishes at `htmx:after:settle` — the next
  Delete / D / arrow would act from the top-left cell. songcell.js puts
  them back there, the cursor from the table's `aria-activedescendant`
  (the table has no id, so the settle leaves it alone). A new mark on an
  id'd Song element needs the same treatment. The browser journey
  `test_enter_after_delete_opens_the_cell_under_the_cursor` pins it
  (click the drums cell, ArrowDown, Delete, Enter: the drawer of the
  cell under the cursor, not the first cell's).

### Drags onto a lane

**Drags onto a lane** (`static/songdrag.js`;
`tests/studio/test_song_cell_move.py`, `test_song_take.py`,
`tests/browser/test_song.py`, `test_song_playlist.py`). All native drag
and drop: the browser's own movement threshold keeps a click a click
(opening a block's drawer is untouched), Esc cancels, and the island
acts only on a drag it saw start (its own `from`) — never on
`dataTransfer` text from outside the page. The pool's drag is here too.

| Dragged | Dropped on | Posts |
|---|---|---|
| a pool pattern | a cell of its kind | `POST /song/cell` — it fills the section |
| a block | another cell | `POST /song/cell/move`; **Ctrl or Alt**: `POST /song/cell` |
| a clip row of the library drawer | an audio lane | `POST /song/section/{id}/clip` (`clip`, `at_bar`, `track_id`) |
| a placed take | an audio lane | `POST /song/take/edit` `op=move`; Ctrl or Alt: the clip route |
| a whole stem's strip | an audio lane | `POST /song/long` `op=set` (`bar`, `track_id`) |
| a stem of the library | an audio lane | `POST /song/long` `op=add` (`bar`) |
| a loop row of the drawer (notes or a beat) | a lane of its kind | `POST /loop/song` (`section_id`, `track_id`, `at_bar`) |
| a loop row of the drawer | a section's head, the + column | `POST /loop/song` `at_section` |
| a loop row with audio | an audio lane | its clip, as a clip row |

**A loop out of the drawer**: on the Song page the
drawer's Loops rows of notes and beats are draggable (`data-drag-loop`,
the row's ref; a row with audio also keeps its `data-name`). Onto a lane
of its kind it lands **from the bar of the pass under the pointer to the
section's end** (bar 1 fills the section) in the track's own sound — the
`.drop-at` preview is that stretch; a beat on a synth lane, or notes on
the drums, say so and post nothing. Onto a section's head the head shows
an edge where its new section goes (`.drop-before` / `.drop-after`, the
half under the pointer); the + column is the end. A loop is never moved
— the store keeps it — so a drop always copies and Ctrl / Alt change
nothing. Perform's slots are not on the Song page: a
slot's loop is the drawer's row, a whole scene goes by →§.

- **A drag moves; Ctrl or Alt copies.** One predicate, `copies(e)`. On
  a time axis a block is an object at a place — a drag that leaves a copy
  behind reads as a bug — and Ctrl is the OS's
  own copy modifier (the cursor shows it). **Touch** has no modifier: a
  drop of a block or a take that began under a finger or a pen opens a
  small popover, "Move here / Copy here" (Esc or a tap outside: neither).
- **A move is one undo step**: `POST /song/cell/move` (`from_section`,
  `from_track` -> `section_id`, `track_id`) points the destination at
  the pattern and clears the source in one `_commit` ("move cell"). The
  kinds must match, a longer pattern grows the destination, a move onto
  itself changes nothing, and a drawer open on the source follows the
  pattern. A cell of the other kind takes the drop and answers with a
  toast ("A notes pattern can't go on this track").
- **The lanes rule**: a lane belongs to its (section, track) and its
  targets are that track's (its sound's fields, its FX units). A move
  **along a track** (to another section) carries the cell's lanes, in
  place of the destination's own; a move **to another track** leaves
  them behind (they go with the cleared source) and the destination
  keeps its own; a **copy never carries lanes**. A block's **span** goes
  with a move to any track, held to where it lands.
- **Where it lands.** A pattern fills its section: position comes
  afterwards, from the block's edges (a drop a few pixels off must not
  make an accidental late entry). Audio lands on the bar under the
  pointer: a clip or a take on that bar **of the pass** (a drop on an
  echo is the same bar of the first pass), a stem on the timeline's own
  bar. A take and a strip keep the bar they were grabbed by.
- **The drop preview**: while it hovers the lane shows `.drop-at`, an
  outline at the snapped bar as long as what would land (the section for
  a pattern, its clip's bars for a clip or a take — `data-bars`); the
  cell gets `.drop-ok`, dashed with `.drop-copy`, and a cell of the other
  kind `.drop-no`.
- **Out of the drawer.** The clip library is a top-layer popover over
  the right of the page. While one of its rows is dragged `#clip-library`
  gets `.dragging-out` (`opacity: 0.25; pointer-events: none`, set a
  frame after `dragstart` so the drag image is taken first), so the lanes
  under it take the drop; `dragend` restores it. It is never closed
  mid-drag, and it does not close itself: the browser's light dismiss
  needs a pointer press outside the popover, and a drag begins with the
  press inside it. Driven in Chromium, a lane under the drawer included
  (`test_a_clip_dragged_out_of_the_library_drawer_lands_at_the_bar_
  under_the_pointer`). With no audio track, a `+ audio` strip shows under
  the lanes while a clip or a stem is in hand; a drop on it makes the
  track.
- **Without a drag**: the library's "Place into" bar and Place button;
  a take's menu ("starts at bar", Remove) and **Delete** on a focused
  take; the whole-stem rows; Ctrl+C / Ctrl+V, N, Enter and the pick list
  for patterns.
- **Every post goes out from an element with no field under it**
  (`.song-view h1`): `htmx.ajax` sends every named input beneath its
  source (`frontend.md`), and `/song/long` would have read the page's
  first `name` field as the stem's new name otherwise. songline.js's posts do the
  same.

### The step grids are macros

**The step grids are macros** (`templates/grids.html`: `note_grid`;
`templates/drumgrid.html`: `drum_grid`, `drum_tools` — two files, so
the piano roll and the beat workflow each own one;
the drawer includes them through `fragments/drawer_notes.html` /
`drawer_drums.html`, and the drum half of `patternops.py` is
`drumops.py`) shared by Drums, Perform and the drawer, which posts the
same field names to `/song/pattern/{id}/grid`. **The drawer's note
grid has an octave window** (`sm.window_octave`, a hidden `oct` the
form posts back: `songcraft.md`, "Generators"; it can be chosen — `svc.drawer_octave`: `song_drawer["oct"]` once ▲ / ▼ was
pressed on this drawer, else the derived one). **The note grid is the
piano roll** (`loop-and-perform.md`, "The piano roll"): the drawer's
own bar (`drawer_notes.html`, "Piano roll tools") holds its tools, the
stamp, the length ops, the selection's, the rows (six / key / all — the
drawer's one range control), the octave window, ghosts and zoom; the
section's other synth tracks are drawn as ghost notes and the chord
track's tones are marked under each step (`songpage._drawer_roll`); a
stamp may read the chord track (`q=track`). Every roll edit is one
song-undo step through `/song/pattern/{id}/tool/{op}`. Drums and Perform are
pinned byte for byte against goldens (`test_grid_macros.py`,
`tests/fixtures/grid_partials/`; regenerate with
`uv run python -m tests.studio.test_grid_macros` after an intended
change).
