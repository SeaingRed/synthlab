# Perform and the loop editor

The Perform page — named **"Loops"** in the studio ("The Loops page,
laid out"; the route and the code keep "perform") — (parts, launchers, its CSS grid, the lane inspector), the momentary
performance FX, and the loop editor shared with Drums and the Song
drawer: pattern tools, pattern undo, grid range, hidden notes, cuts,
grid.js — and **the piano roll** (the note grid's tools, selection,
rows, ghosts, zoom and keyboard, and Perform's lane switch). Index:
`README.md`.

## Perform

**Perform** carries `state.perform_parts` (synth/drums toggles decide the
loop's parts), and launches **loops** from its launcher's slots and
scenes ("Slots and scenes" below), **beats** (the song
pool's drums patterns — the bank is the pool:
`/fragment/perform/launch/{pattern_id}` -> `StudioState.apply_pool_beat`;
the playing one is matched by content in `svc.pool_beats`) — queued at the
loop wrap — and a lane's **sound** from its head in the lane inspector
(every sound there is, searched: "The lane heads (Perform)" below).
Perform has no song strip and no clip placer: it links "Arrange ▸" to
the Song page, where sections and takes are arranged.
The Drums view's 8-slot bank is unchanged and still its own. A fresh
studio has an empty pool: while the pool holds no drums pattern the
Beats launcher shows the bank's filled slots instead (heading "from the
bank", `pool_beats()["beat_source"] == "bank"`, ids = slot numbers),
each launched through `/fragment/perform/bank/{slot}` ->
`StudioState.bank_load` (the active slot follows, as on Drums), queued
like a pool beat. The heading is inside the `launcher` region because it
names the source; `_w_launcher` reads the source, the ids/names drawn and
the playing one, so a bank edit moves it only while the bank is shown.

## The Loops page, laid out (Perform, tiled)

**The name.** The page the user reads as **"Loops"** is this one: the rail
and the nav ("Loops", Alt+Shift+3, the palette's "Go to Loops", the
Stream Deck's view key), the page's `<h1>`, its tooltips and the toasts
that send you here ("Edit it on the Loops page"). **Still "perform"**:
the route `/view/perform`, the view key `perform` (`views.py`,
`data-view`, the reset scopes, `synthlab.ws`'s page key but for its
revision), the module ids (`perform.*`), the file and class names
(`perform.html`, `perform.js`, `.perform-view`, `routes/perform.py`) and
these docs' "Perform". The clip drawer's rail button says **"Clips"**
(`fragments/clip_library.html`: "Clips 3" — the count is
the loops' — and "Clips 3 loops · 4 clips" on the comfy top bar; palette
"Clips: the loops and clips drawer"), the name the clip hub's page has.

**The layout** (`views/perform.html`, style.css 9b4
and **42**, dense.css D18 / D41 / **D42**): three columns under the head,
in **both densities from 1200 px** with any but a coarse pointer (dense with a pen
too, dense.css D42; touch flows, its targets 40 px) — the page is the window
(`calc(100dvh - --top-h - --keys-h)`) and every pane scrolls inside
itself:

- **the Loops panel** (`loops`, the left column: pinned from 2200 px, a
  tab sliding over the page below; "The Loops panel" below);
- **the centre**: the live strip (`bar`: ▶, tempo, the meter, the loop's
  position, ⏺ resample, the master knob and **Notes | Drums | Both**), the
  **launcher** (`slots`: its scenes, then it scrolls, at most a third of
  the window) and the **editor** under it — the note grid (`seq`) over the
  drum grid (`rack`), sharing the rest 1.5 : 1 in Both, one of them the
  whole of it in Notes or Drums;
- **the right column** (`side`, `.perform-side`: a flex column of
  modules, no id, so each is a workspace member of its own): **→ Song**,
  **the lane inspector** (it takes the height) and **Play** (its own
  height, at most 62 % of the column). Its width is `--lane-w` (300 px
  dense, 320 comfy; the splitter on its left seam, 240-560 px).

A grid takes the height its pane has: a step is `clamp(17px, its share,
30px)` dense (`clamp(20px, …, 34px)` comfy), from `--rows` on `.grid` and a
size container, and scrolls inside past that, the velocity lanes stuck to
the pane's foot. **Comfy tiles as dense does** (stacked, it would put
the launcher about 1000 px below the grids' foot at 1920): larger type
and gaps, the same tiling — but its rows keep a floor
in Both (300 px the note grid, 250 the drums), so at 1920 x 1080 comfy in
Both the page scrolls by about 80 px (1440 x 900: about 340 — comfy's view
is at least the window, not exactly it, and the Loops panel and the right
column take the rows' height, `contain: size`); Notes or Drums fits. Below 1200 px
it is the flowing page (the same three columns from 901 px, one column
under that: head, strip, launcher, the two grids, the right column, the
Loops panel).

**The lane inspector** (`#perform-lane`, `data-module="perform.lane"`;
its body the region **`lane-inspector`**, `fragments/lane_inspector.html`,
`StudioService.lane_context`, `_w_lane` — exactly what it draws). The
**selected lane** — the layer slot the note grid, the keys and Synths
edit:

- **the lane heads** ("The lane heads" below), here rather than in the
  note grid's controls row (where four heads with long names wrap length,
  pattern and feel onto a second line at 1920 dense): one a line, name ·
  ▾ · M · S, and the next empty slot's "＋ lane N" ▾ last. The ids
  (`lh<N>-…`), routes and `data-stack` are the lane heads' own;
- **what plays it**: the launcher's slot on this lane — "▶ 2 Drop ·
  “Acid line v2” ✎" (its scene and loop, as the Loops lists name it; ✎:
  an edit saves to it) — or "the grid's own notes", or that it follows the
  main lane (at its octave); and the drums' slot, or "stopped";
- **its mix**: level (`/fragment/layer/gain`), Follow and its octave
  (`/fragment/layer/follow`; not on the main lane) — the layer strip's own
  routes;
- **its FX** by name ("Reverb → Drive", or none) and **FX rack ＋**: the
  workspace's added module (`fx_rack_fragment`, the edited layer's and the
  master's racks), placed at the dock's foot like "+ FX rack" in ⊞ Layout;
- **Open on Synths ▸**: a nav link to Synths, on this lane's sound (the
  panels edit the edited lane); the browser's Back (Alt+←) comes back —
  a one-click round trip.

The transport's controls row says which lane the grid edits ("editing
1 Acid bass", `.ed-lane`, in the `transport` region's watch:
`active_layer`, the patch's name).

**✦ Coach is the inspector's other tab** (Lane | ✦ Coach in its head,
`data-coach-toggle="close" | "open"`, `data-coach-tab`; coach.js sets
their `aria-selected`): `#coach-dock` stands in the inspector's body and,
open, hides the lane (`.inspector:has(#coach-dock:not([hidden]))`) — the
two never fight for the column. The head's ✦ Coach and **Alt+K** still
toggle it (an inspector folded to its head opens with it); the coach's
own head hides there (the tabs are its head). It is a module still: it
pops out and focuses, and moves with the inspector.

**Play** (`#perform-play`, `data-module="perform.play"`): the eight pads,
the drum XY, the performance FX and the beats
launcher, **one tab at a time** — pads | XY | FX | beats (layout.js's
tabs: `[data-tab-of][data-tab-pick]` set `data-tab` on the box, per
browser `synthlab.tab.perform-play`, re-applied after a swap) — and its
fold (its head's "Play", `data-fold`). Each part is a module of its own
(it pops out — a popped-out part shows whatever tab is up — and focuses)
inside Play; only Play moves in the dock. The pads' keys (Q W E R / A S D
F) and the FX's (C V B N · I O P) play them whichever tab shows, Play
folded too (perform.js clicks the buttons it finds).

**The workspace**: every block is a module — `perform.loops`,
`.transport`, `.slots`, `.notes`, `.drums`, `.song`, `.lane`, `.play`
(and inside them `.coach`, `.pads`, `.xy`, `.fx`, `.launch`). The
presets (`workspace.PRESETS`, Grids / Launch / Compact) are redrawn on
them, Reset returns to the page above, and **older saved layouts of
Perform are dropped**: `synthlab.ws` keys this page `perform@2`
(workspace.js `REV`), and a record under an older key is deleted on load.

The pattern tools are `.btn`s with a command each (`data-cmd`, group
"Pattern": the palette and a Stream Deck find "Shift notes left"); the
velocity tools are a `.menu`; the bars are a `.seg`; the how-to line is
the tooltip of the (?) at the end of the toolbar. The momentary FX are
`.btn`s named for the shortcut sheet with `data-keys` + `data-key-label`
(they are held, so they are not commands). Launch cards are commands
("Launch beat …", "Launch section …"); a lane head's ▾ is "Pick the
sound of lane N".

### The rack's focus: Notes | Drums | Both

Perform is where a quickly made loop is perfected in the grid, drums or
synths, so the editor switches between them. **Notes | Drums | Both**
(`.rack-focus`, at the live strip's far end, `views/perform.html`)
shows one grid large and folds the other to its bar, or both. Client-only: grid.js writes `<html data-rack>` (per browser,
`localStorage` `synthlab.rack`) and presses the button in force after
every settle (the server draws all three unpressed); CSS reads it —
`style.css` section 35 hides the folded grid (the drums keep their tools
row; the note grid goes whole, its controls row too — lane, length,
pattern and feel read as acting on the drums, and the switch is in the
live strip), style.css 42 gives the focused one the editor's whole height
(`html[data-rack] #view.perform-view`'s rows), and in drums focus the
steps grow past 30 px to fill it (dense.css D35). **The editor follows
the selection**: a slot selected in the launcher turns Notes |
Drums to its kind (slots.js, grid.js's `synthlab.rack`), from Both too
(one editor, as the slot is: Both is a press away, Alt+G). **Alt+G** steps notes → drums → both (`data-keys` on the cluster;
no bare letter is free).

The part buttons (`synth`, `drums`; in the live strip, so what the loop
carries stays in sight) **mute** a part of what ▶ plays (lit = it plays),
and say so:
a "mute" label, their `title`s, "Mute / unmute the … part of the loop".

### → Song: the loop into the song in one move

**→ Song** (`#perform-arrange`, and Synths' sound bar) posts `POST
/song/from-loop` — "Add loop to song", the Song page's own — and the
answer, asked from another page, has a body for the note beside the
button (`fragments/to_song_note.html`): the section the loop became
("Added “Loop 3” to the song: section 3, 2 bars"), the beat's other
variations that went to the pool, **Open Song ▸**, and — when the song
already had a drums track whose kit is not the loop's (the new section's
beat plays through that track) — **Use <kit> on Drums** (`POST
/song/drums/loop-kit`: the loop's `song_bridge.KIT` on the track, one song
undo step). The same words ride a `studio-toast`. What it carries:
`song.md`, "Bridges". The note is a popover anchored by name to its
button (`anchor-name: --to-song`, scoped to its bar) and grows to the
right from the button's left edge (`position-area: bottom span-right`),
never narrower than 36ch: Perform's button sits at the left of its
column, and `span-left` would give it only the 106 px between the
window's edge and the button. **It never covers the keyboard dock**: its
foot is held off the dock (`bottom: var(--keys-h)`), so
when there is no room under its button — the dock open under Perform's
at 1920 — it overflows its area and takes the first fallback that fits,
`@position-try --to-song-above` (above, from the same left edge), then
leftwards.

**▶ Whole song** (`#perform-arrange`, beside → Song; palette "Make this
loop a song") is the Song page's "From my loop": `POST
/song/arrange source=loop` lays the loop down as intro / verse / build /
drop / break / drop / outro, REPLACING the song in one song undo step,
and plays it on Song (`songcraft.md`, "`POST /song/arrange`"). It asks
first (`hx-confirm`) when the song had sections as the page was drawn
(`song_filled`, `service/page.py`). It is this page's one click from
the loop to a song;
`tests/browser/test_songcraft.py::test_a_loop_becomes_a_song` presses
it.

## Perform view: launches queue at the loop wrap

Perform view (`views/perform.html`, `static/perform.js`): pads reuse the
`.hit-pad` click handler in `piano.js`; the launcher posts
`/fragment/perform/launch/{pattern_id}` (or `/fragment/perform/bank/{slot}`
while the pool is empty) whose `preview-ready` carries
`queue: true`, and `audio.js` then starts the new loop exactly at the
current loop's wrap (`startLoop(buffer, at)`) and fires `loop-switched`.

## Perform's grid

**Perform's grid** (the Loops page's) is `head` over `loops |
bar / slots / seq / rack | side` ("The Loops page, laid out" above),
named in **both** breakpoints (`test_perform_names_every_grid_area_at_both_breakpoints`;
under 900 px one column: head, bar, slots, seq, rack, side, loops). The
pads, the XY, the FX and the beats are the Play module's tabs in `side`,
no areas of their own. `.launcher` is `auto-fill minmax(150px,
1fr)` so the 8 pattern slots land in one row, and
`.launcher > :not(.launch)` spans the row — an empty state or the clip
placer is prose, not a card, and would be crammed into one 150px column.
**`.ghost` is `width: 100%`** (right in the dock and the launchers, wrong
in a flex row): in the live strip it stretches a button across the whole
strip and wraps the meter and volume onto two more rows, a 40px strip
146px tall. Scope a `width: auto` wherever a ghost sits inline.

## Performance FX

**Performance FX** (`.perf-fx` strip on Perform, `audio.js:loopFx` +
`perform.js`): momentary tape stop / half / double / reverse / stutter
(1/4, 1/8, 1/16), held-to-apply. They act on the **sounding
AudioBufferSourceNode** — `playbackRate` ramps, `loopStart`/`loopEnd` for
the stutter window, a reversed-buffer swap for reverse — not on a server
re-render, because a tape stop that arrives after a round trip is not a
tape stop. Consequence: they are a performance and are deliberately NOT
baked into `export song` / `export stems`. The *rendered* stutter is
the `beat_repeat` FX unit (pattern + seeded chance, in the render, the
export and the stems like any unit), and the *rendered* tape stop is
the `stop` unit (the last `beats` of every `repeat_period` slow to a
halt; in a song, placed on one section by a lane on its `mix`). audio.js owns the surface
because it owns the buffer; perform.js owns the gestures and holds no
audio state. Keys (C V B N · I O P) must dodge the pads' Q W E R /
A S D F and piano.js's z/x octave pair — pinned by `test_perform_view.py`.
**They are typing → pads' keys**: the keyboard dock is on
Perform too, and its QWERTY group's **typing → keys | pads**
(`#kbd-typeto`, Alt+P; per browser, keys by default) says what typing
plays here — the dock's keys in its layout, as on every page, or the
pads (Q W E R / A S D F) and these FX keys, the dock's typing standing
down whole (piano.js `typing`: `padsTyping`). While typing plays the keys
the pads' and the FX's letters are dimmed. The switch is the dock's
alone (`keys-and-midi.md`, "The keyboard dock"); Perform's head carries
no copy of it.

## Loop editor

**Loop editor** (FL-style; `routes/pattern.py`,
`patternops.py`, `static/grid.js`, `tests/studio/test_patternops.py`):

### Perform is the channel rack

**Perform is the channel rack**: the note grid (`transport`) and the
drum grid (`fragments/perform_drums.html`, region `perform-drums`,
grid area `rack` in both breakpoints) sit together. The drum grid is
the same `drum_grid` macro and routes as Drums with `target=none`: its
tools answer `hx-swap="none"` and the rack comes back by its watch — as
a MORPH (`hx-swap-oob="outerMorph"`), so the step under the keyboard
keeps the focus through a tool; a drum grid post suppresses `drum-bank`
*and* `perform-drums`. It is the step sequencer (`drums.md`, "The step
sequencer"): per-hit lanes, the inspector, the beat's variations A-D
and its generators (`beat_bar`) are all here too.

### Pattern tools

**Pattern tools** (`patternops`, pure): shift ◀ ▶ (wraps), reverse,
×2 (`duplicate`, only when bars*2 is a `BAR_CHOICES`), clear, transpose
by a key tone / an octave along `state.key_ladder`, velocity humanise /
ramp / accent / reset, and "tidy" (drop hidden notes). Drum rows' ⋯ add
fill every 1/2/4/8 and a row shift. **Every tool acts on the window**
(the steps and rows the grid shows) and keeps what it hides, the
`_carry_over` rule. Growing the note grid's bars repeats them
(`routes/patch._grow`, as the drum transport's bars do) unless the new
bars already hold hidden notes (then they reappear instead).

### Pattern undo

**Pattern undo** (`StudioState.push_pattern_undo`/`pop_pattern_undo`,
runtime, 32 deep, tagged with the active layer): any route marked
`@pattern_edit` (`routing.py`: every grid post, pattern tool, drum row
tool, bank/kit/lib load, beat launch, chord write) that moved the grids
is one step. `/fragment/pattern/undo`; grid.js binds Ctrl+Z to it on
Perform and Drums (window capture, before piano.js's patch undo). A step
from another layer is refused (its cells are that slot's). Cleared on
import/new session.

**A section opened in the loop is one step** (so Ctrl+Z after a launch
never answers "Nothing to undo in the pattern"): `section_to_loop` — the
Song page's "Open in Perform" — pushes a step that holds the
*whole* loop (`SongBridge._whole_loop`: the arrangement with its tempo,
key and meter, the patch and curves, every layer slot, the main lane, the
drum bus FX, the variations, the bank slot, the six scopes' baselines it
marks, the other slots' undo rings, the saved loops open on the lanes,
and the launcher's rows and which parts ▶ plays). A launch
of the launcher is the same kind of step ("Slots and scenes"). Undoing it
(`restore_whole_loop`) puts all of that back in one go, drops the
outgoing sound's step it pushed on the patch ring, and moves the octave
window to the loop's notes; the steps under it are the loop's own again.
A track edit the launch ended stays ended. It is tagged with the active
layer like any step.

### Range

**Range** (`state.grid_span`, schema'd): 1 = the key's first six tones,
2 = two octaves root to root (`pitch_rows(arr,
span)`; 15 rows for a 7-tone scale, so `MAX_GRID_PITCHES` is 24), 3 =
chromatic, the 24 semitones from the window's bottom. The transport
posts its drawn `span` — and its octave window, `oct` — so `_carry_over`
knows which rows the form could show (a stale page never makes an
unticked note stick). The window is "The piano roll", "Rows", below.

### Hidden notes

**Hidden notes**: off-key notes play (by design, as the drawer says);
**notes past a shrunk grid never play** — `arrange._playing_steps`: a
run must *start* inside `bars * steps_per_bar` (otherwise it would
render past the loop end and `_wrap_tile` would fold it onto bar 1). A run that starts
inside keeps its tail (section chunks rely on it). The transport shows
"N hidden notes · tidy".

### grid.js

**grid.js** (island 15): per-grid playheads — each grid maps the loop's
phase onto its own `data-bars` (audio.js writes only the loopbar's;
the song drawer's grid stays song.js's) — and step LEDs (`.cell.now`);
playable row labels (a pitch through `window.synthlab.piano`, a drum
through its `.hit-pad`'s URL); a lone right-click erases (a right-drag
erases in audio.js's stroke: `paintVal` false for button 2, the pen's
eraser and the roll's erase tool) — but a FINGER held on a step of the
piano roll is handed to roll.js as `roll-press` (it selects the note
and asks menu.js for the roll's menu), so this is still the grids' one
`contextmenu` listener; the GSAP motion of `[data-anim]` tools, armed
on click and played on the next settle against the grid as it now
stands; a step pop on toggle. Shift+arrows on a focused step are the
DRUM grid's here; the piano roll's keys are roll.js's.

### A run of adjacent cells is one held note

A run of adjacent cells is ONE held note (`_merge_runs`): CSS draws it
as one joined bar with a brighter attack edge; root rows are bold.

### Cuts

**Cuts** (`Arrangement.cuts`, `[pitch, step]`, `tests/studio/
test_note_cuts.py`): a cut cell attacks again although the step before
is on — two notes side by side. The grid posts it as the checkbox's
**value** (`cell.A1.4=cut`; Shift/Alt-click in grid.js, which sets the
value after the cancelled click's restore), so no new inputs exist and
an older page posts plain cells. Engine: `arrange._row_breaks` adds a
row's cuts to its `breaks`. They travel exactly as `slides` do: layer
`seq` (`LAYER_SEQ_KEYS`), `lane_render` (only on the layer's cells),
follow layers (moved by the octave), pool patterns (`_NOTE_FIELDS`,
`pattern_seq`, regrid/tile/crop, `_crop_runs`), the pattern tools
(reverse maps a cut at c to total - c: the boundary between the same
two notes), the undo ring. **Absent means none**: an empty `cuts` is
dropped from `RenderInputs.key()`, from layer dicts and from
`content_hash`, so no key or dedupe hash moves for a lane without cuts. A cut without a cell is
dropped on every post. Note recording (`record.merge`) cuts each played
note-on that follows a step of its own pitch: repeated 16ths record as
notes, not one run.

### The Song drawer has the same tools

**The Song drawer has the same tools** (`/song/pattern/{id}/tool/{op}`,
`patternops.notes_tool` / `drums_tool` — one implementation for both
surfaces; `tests/studio/test_song_pattern_tools.py`): on a pool
pattern, so every section using it follows; ×2 grows a section it
outgrows (the SONG schema's rule, toasted). The drawer's rows follow
`state.grid_span` too (its grid posts its `span`). The piano roll's
tools ride the same route (`patternops.roll_tool`; "The piano roll").

### A tool never overtakes a stroke

**A tool never overtakes a stroke**: the grids post 150 ms after the
last input; grid.js holds a tool click made inside 260 ms of a grid
edit (window capture, re-clicked after), or the stroke's late post
wrote the pre-tool grid back over the tool.

## The piano roll

**The note grid is a piano roll** (`templates/grids.html` `note_grid` + `roll_tools`, `static/roll.js`,
`static/zoom.js`, `patternops.py` "the piano roll: notes as runs",
`rollview.py`; `tests/studio/test_roll_ops.py`, `test_roll_routes.py`,
`tests/browser/test_input_matrix.py`). It is ONE component: Perform's
transport and the Song drawer draw it from the same macro and post to
the two pattern tool routes.

### The rule: every gesture compiles to cells, cuts and the two lanes

The engine is a step grid and stays one. **The
roll adds no note data**: a note is still a run of one row's adjacent
checkboxes split at the row's cuts (`arrange._merge_runs`), its velocity
and its slide still sit on its START step (per step, shared by every
note starting there). So rendering, the MIDI export, the generators,
pattern undo and the pool need nothing of their own for the roll. (One
more lane sits the same way: **timing
inside a step**, `note_nudge`, which only free record's loops write —
"Free record and the shaper" below. No gesture writes it; every tool
carries it with its note's start step, as a velocity.) What each
gesture becomes:

| Gesture | Becomes | Through |
|---|---|---|
| draw (a click, a drag) | cells ticked; a drag fills every step between two pointer samples (`audio.js` `paintTo`, so a fast stroke skips no cells) | the grid form's own post |
| erase (the tool, a right-drag, the pen's eraser) | cells unticked (`paintVal` false) | the form's post |
| Shift / Alt-click, a pen's barrel tap, Shift+Enter | a cut: the cell's value `cut` | the form's post |
| a note's END dragged; Ctrl+← → | `resize`: the run's cells set or cleared from its end; it stops at the next note on its row, which keeps its start and gets a cut; never under one step, never past the grid | `POST …/notes/resize` (`sel`, `by`) |
| a selection dragged; "move"; Alt+arrows | `move`: the notes' cells cleared and set `dx` steps later, `dy` rows up; each note's velocity and slide go from its old start step to its new one (a start step no note is left on gives them up; a note at the default velocity does not pick up a stale one); a cut is written wherever two different notes now touch and dropped where they no longer do; a note dropped on another takes the steps it covers. The move stops at the grid's edges as a whole | `…/notes/move` (`sel`, `dx`, `dy`) |
| Ctrl-drag; copy + paste; Ctrl+D | `copy`: the same, the originals kept | `…/notes/copy` |
| Delete; ⌦ | `remove`: the notes' cells, and the velocity and slide of every start step left empty | `…/notes/remove` |
| legato | each note runs to the next later START among the notes it is given (any row), the last to the end of the grid | `…/notes/legato` |
| ½× / 2× | each note's length halved (at least a step) or doubled (to the next note on its row, or the grid's end); starts never move | `…/notes/half`, `double` |
| a stamp | the in-key chord on the clicked row (`STAMPS`: triad, 7th, 9th, sus2, sus4, power — scale steps up `key_ladder`) as simultaneous notes of the chosen length; in the drawer also "chord track": the tones `chordtrack.resolve` holds under that step, root position from the nearest root at or below the row. A tone the window has no row for is left out, and the toast says how many | `…/notes/stamp` (`row`, `step`, `q`, `len`) |
| ◀ ▶ ▲ ▼ ±8va with a selection | the same `move` (an octave = however many rows of the window an octave is); without one they are the pattern tools (the whole window; shift wraps) | `…/notes/shift_left` … with `sel` |

`…` is `/fragment/pattern` on Perform (`@pattern_edit`: one pattern-undo
step) and `/song/pattern/{id}/tool/` in the drawer (one song-undo step;
every section using the pattern follows). A selection is
`sel=<step0>:<step1>:<pitch>:<pitch>`, inclusive: it holds every note a
cell of which lies in the rectangle (`_picked`). A tool that needs one
and gets none, a rectangle that names no row, a number that is not one:
a 422 toast, nothing changed. Every one keeps what the window hides
(`_split` / `_join`, the `_carry_over` rule).

**The answer carries the selection**: `HX-Trigger: {"roll-sel": {"sel":
…}}` is the rectangle round the notes the edit touched (`""` after a
remove; absent when the tool ran on the window, or a move could not
move). roll.js keeps its selection from that, so it survives the redraw
and a second move selects exactly the notes the first one moved.

**Strum** (`tests/studio/test_strum.py`) is not a
gesture and compiles to nothing: it is one number beside the grid,
labelled **grid strum** — the keyboard dock's own is "keys
strum" (the live keys and pads), and each control's tooltip names the other.
`Arrangement.strum` (ms, −60..60; a notes pattern's `strum` in a song):
notes that START on one step are started that far apart, low to high
(negative: high to low), and each still ENDS where it did — shortened
by what it waited, never under an eighth of a step — so a strummed
chord releases as one. A note alone on its step is not moved; an arp
plays one note a step and is not strummed. The rule is
`arrange._strummed`, applied in `_lane_timeline`: the ONE list
`build_song`, a section's chunks and `note_schedule` (the MIDI file)
read, so the three agree by construction. Four things to keep:

- **A strummed lane takes the timeline path** (`_uses_timeline`), as a
  lane with slides or an arp does. At 0 it stays on the per-row path and
  renders the same samples as a lane without the field.
- **A slide reads the grid's order, not the strum's.** `_strummed`
  returns the notes in the order the unstrummed list sorts to, and
  `_timeline_notes(ordered=True)` takes it as it stands: a strum moves
  when a note starts, never which note came before it. That is what
  keeps a chunked section equal to the whole one (`song_model._lead`
  names a chunk's predecessor from the grid).
- **Absent means absent.** `strum` is in `schema.ARR_OPTIONAL`: no
  session file, loop key or baseline holds a 0. The main stem's key
  carries it only when it plays (`arrange.strum_beats`: set, and no
  arp); a layer dict, a slot's `seq` (`state.model.seq_of` /
  `seq_changes`) and a pattern only when set. A layer strums by its own
  value, never the main lane's; a follower by the main lane's
  (`follow_layer`).
- **It is milliseconds, converted at the arrangement's tempo**: the same
  20 ms is a larger part of a step at 170 bpm than at 90.

The control is a knob in the grid's own form, beside the arp (Perform's
"feel" cluster, where it reads **"grid arp"**, as the strum reads "grid
strum": the dock's "arp" is the keys arp, live, and the two sit side by
side on Perform; the drawer's arp row): it must post WITH the grid, and a
form that does not carry the field leaves the value alone
(`routes/patch._merge_arrangement`). It is a feel setting like the swing
beside it: not a pattern undo step. The stamp still writes simultaneous
notes; the lane's strum spreads them.

### Tools

`<html data-roll="draw|select|stamp|erase">`, per browser
(`localStorage`), set by the toolbar's `.seg` (each a command: "Tool:
select"). **A plain drag keeps its meaning in draw** (paint): moving and the marquee need the select tool, and a drag
never changes meaning by where it starts — with one exception a pen and
a finger need: in draw, a drag that starts on a note's END (the last
6 px of its last step; 10 px under a pen; the right HALF of the step
under a finger, where a step is 40 px) changes its length. A click
there is still a click. In select: a drag on empty steps is a marquee
(the selection is pulled in to the notes it holds), a drag on a selected
note moves the selection (Ctrl held when it lands = copy), a click
selects the note under it, Shift-click (a pen's barrel) adds. In stamp a
click stamps. Erase never draws. A right click erases a step in every
tool (grid.js).

### Rows, the octave window, ghosts, chord tones

- **Rows**: six tones (`span` 1), the key over two octaves (2: every row
  in key — this is scale lock) or chromatic (3: 24 semitones, the rows
  outside the key `.off-key`, dimmed). Root rows are bold.
- **The octave window** moves the rows' bottom whole octaves from the
  root (`state.model.grid_rows(arr, span, octave)`; `pitch_rows` is the
  window at 0). On Perform it is `state.grid_oct` (-1..+3): **runtime
  only** — a saved session has no key for it (a top-level payload key
  must be in every save, which would move the pinned state bytes), so
  it opens at the root. In the drawer it is the open drawer's own
  (`song_drawer["oct"]`, -2..+4), and until ▲ / ▼ is pressed the one
  holding most of the pattern's notes (`sm.window_octave`). Both are
  moved by `POST /fragment/grid/span` (`oct_by` = 1 | -1).
- **A load moves the window to its notes** (`StudioState.show_notes`): a loop (Edit, → Perform), a
  take (→ Perform as notes), a section opened, a generated part, a MIDI
  import, chords → grid, the clip hub's notes, and the undo of a section
  launch. The notes are never moved: the window finds the nearest octave
  that shows them all, widening to two octaves of the key or to the
  chromatic rows when a note is out of the key (`model.notes_window`).
  An empty grid keeps the window where it was. PageUp on
  the top row / PageDown on the bottom row pages it and keeps the
  cursor on the pitch asked for. Notes outside the window are hidden
  notes: kept, played, counted.
- **Ghosts**: the other lanes' notes on the visible rows, as a dim band
  in the lane's tint (`.cell.ghost.g<n>`). On Perform: every other slot
  that holds a sound and is not muted — a follower's are the main
  lane's at its octave, what it plays. In the drawer: the section's
  other synth tracks, regridded to this pattern's steps and tiled over
  its length. Classes on cells the grid already has (`rollview.context`
  -> `marks`): no input, nothing posted, never a render input. "ghosts"
  hides them per browser (`<html data-ghosts="off">`).
- **Chord tones**: under each step, the rows that are tones of the chord
  the chord track holds there carry `.ct` (a dot; the chord's root
  `.cr`, a bigger one). In the drawer the section's (else the song's);
  on Perform the working loop's own, once it has one ("The chord lane"
  below) — and with it the stamp's "chord track" choice.
- **The ruler** (`.ruler`, bar numbers) sticks to the top of the pane and
  the row labels to its left edge while the grid scrolls; on Perform the
  **velocity lane sticks to its foot** (dense.css D26), as the drum
  grid's does: in Both, with two octaves or the chromatic 24, the rows
  floor at 17 px and the lane would otherwise fall below the pane. The
  pane's `scroll-padding-bottom` (32 px; the drum grid's too) keeps a step
  scrolled to — the keys' cursor, a pen stroke — above the lane, not
  under it. **The lane hides no row the pane can show**: the
  rows share the pane's height less the ruler, the chord lane's row, the
  slide row and the lane (dense.css D25: `100cqh - 80px` with a chord
  row; left out of the sum, that row makes the rows overflow by it at
  1920 in Both, and the lane sits over the slide row and the bottom note
  until the pane is scrolled). Rows that cannot fit at the 17 px
  floor (the chromatic 24, a short window) scroll under the lane, the
  last one clear of it at the scroll's end
  (`tests/browser/test_perform.py`, "hides no row").

### Zoom and pan (`zoom.js`)

Any `[data-zoom="<name>"]` scroller: the roll's are `perform` and
`drawer`. The zoom is the step width — 12, 16, 20, 28, 40 or 56 px —
kept per browser and name as `--zoom-<name>` on `<html>`, which the
grid's own style reads (`--step-w: var(--zoom-<name>)`), so a redraw
changes nothing and nothing flashes; unset is "fit" (the steps share
the pane). Ctrl+wheel = a notch about the pointer (a
precision touchpad's pinch arrives as Ctrl+wheel in small deltas:
summed); wheel and Shift+wheel are the browser's own scroll and change
no value; two fingers = pinch (a notch per 25 % the fingers part) and
pan; a middle-button drag pans; the − / fit / + buttons; Ctrl+= / − / 0
with the focus in the grid. **The playhead is exact at every zoom**: the
grid is one column as wide as the pattern
(`grid-template-columns: minmax(max-content, 1fr)`) and the playhead is
placed in it and across every row (its inline `grid-row`), so its
`100cqw` track is the pattern, not the pane; the step LEDs are classes.

### The keyboard

The grid is **two tab stops** (roving `tabindex`, set by roll.js after
every redraw): its cells, and its velocity lanes.

| Keys | On a step |
|---|---|
| arrows | move the cursor (the slide row is the last row) |
| Home / End; Ctrl+Home / End | the bar's first / last step; the grid's |
| PageUp / PageDown | an octave up / down; at the top / bottom row, the window |
| Space, Enter | a click in the current tool (toggle, stamp, select the note) |
| Shift+Enter | a cut |
| Shift+arrows | select: from where Shift was first held to the cursor |
| Alt+arrows; Alt+Shift+↑ ↓ | move the selection a step / a row / an octave — with no selection, the whole pattern (the tools ◀ ▶ ▲ ▼ ±8va) |
| Ctrl+← → | the note under the cursor (or the selection) a step shorter / longer |
| Ctrl+↑ ↓ | the cursor's step's velocity (the lane's own keys) |
| Ctrl+C, Ctrl+V, Ctrl+D, Ctrl+A | copy (remember the selection), paste it at the cursor, duplicate to the right, select all |
| Delete / Backspace | the selection, else the note under the cursor |
| Esc | no selection |
| Ctrl+= / − / 0 | zoom in / out / fit |

On a velocity lane ← → walk the lanes and ↑ ↓ Home End set the value
(audio.js). **On a note step Shift+arrows select**; shifting and
transposing the whole pattern is Alt+arrows (the drum grid keeps
Shift+arrows: grid.js). Tools have no key
of their own (no bare letter is free): they are commands (Ctrl+K,
"Tool: stamp") and Stream Deck keys. Paste copies the notes that are in
the remembered rectangle *now* — it is "copy from there to here", not a
clipboard that outlives an edit of its source.

**Posts go one at a time.** htmx keeps one waiting request an element
and drops the rest, and a tool's answer redraws the button that sent
it; so roll.js queues its own posts (`send`) behind any tool request in
flight, and a key pressed in the ~30 ms between a redraw's swap and its
settle is still the roll's. A held Ctrl+→ therefore lengthens once a
round trip, in order, and loses nothing.

### The chance lane

The Elektron / Bitwig "chance", on melodic loops. **A step's chance**
— the odds that the notes starting on it play — is
`Arrangement.note_chance`, `{step: 0..1}`, the edited lane's own (a
layer slot keeps its own in its `seq`; `state.LAYER_SEQ_OPTIONAL`).

- **On the roll**: a row "chance" in the place of "vel" (grids.html
  `chance`, drawn on Perform only — the Song drawer's patterns have none),
  one of the two at a time as the drum grid's lane switch: the row's label
  is the switch ("vel" → chance, "chance" → vel; "vel%" says some step has
  a chance), and **PageDown / PageUp** in a lane swap them at the same
  step (roll.js `flipLane`; `html[data-roll-lane="chance"]`, so a morph of
  the roll keeps it; style.css 43). One row at a time keeps the rows'
  height: two octaves still fit Perform's Both at 1920
  (`test_perform.py`'s lane test). The same `.vel-lane` bars: drag across
  to paint (audio.js), a focused bar's ↑ ↓ step 5 %, Home / End 0 /
  100 %, ← → walk the steps. A bar under 100 % is hatched (the drum lane's
  chance look, `[data-lane="p"]`), a step at 100 % drawn quiet. Its hidden
  fields are `nchance.<step>` and always post; the grid's post stores only
  a value under 1 (`arrangement_from_form`), a form without them keeps the
  lane (`_merge_arrangement`), and steps past a shrunk grid keep theirs,
  as velocities do.
- **On a hash, never the dice** (drums.md, "Chance: a hash"):
  `arrange.note_roll` is `hit_roll(crc32("notes:" + the pitch classes
  that start on the step), step)`. So a chord plays or rests whole; a
  following layer an octave away rests what its main lane rests; a chance
  raised keeps every note it kept (a note that plays at 0.5 plays at 0.8);
  editing another step leaves this one's roll alone; changing what starts
  on the step rolls it again. **Every pass rolls afresh**: `note_roll(pcs, step,
  at)` also hashes `at`, where the pass of the pattern starts (in steps);
  the first pass (`at` 0) rolls what the grid always rolled. A tiled lane
  carries each step as `[chance, its step in the pattern, at]`
  (`arrange.chance_tile`; `note_plays` reads either form), so a section's
  repeats of a 1-bar riff differ bar to bar and still re-render
  identically (seeded, never the dice). See "The working loop plays four
  passes" below and `song-render-export.md`, "Chance: every pass afresh".
- **One filter in front of every scheduler**: `arrange._chanced` takes a
  lane's playing steps and removes each note (a run of a row, split at
  its cuts) whose START step's roll says rest — before the per-row path
  and the timeline alike, so a note that plays sounds exactly as it does
  without chance (`test_the_render_is_the_grid_without_the_rested_notes`)
  and anything later in the schedule (a strum, a slide, a timing offset)
  composes with it; under an arp `_lane_timeline` rolls each step's
  arpeggiated note. `note_schedule` reads the same, so **the loop's MIDI
  file holds the notes that play**, not the odds.
- **Absent means absent**: the field is in `schema.ARR_OPTIONAL`, in no
  session file, loop key, stem key, slot `seq` or loop file while no step
  has one; a session without it renders and keys exactly as one without
  the field, so `RENDER_REV` need not move. With a chance, the main stem's payload
  and a layer's dict carry it (`song_bridge.lane_render`: on the steps
  the layer reads).
- **Moves with its notes**: the pattern tools (shift, reverse, ×2, clear)
  and the roll's move / copy carry a step's chance as they carry its
  velocity (`patternops`); a longer grid repeats it; it is a pattern undo
  step (`PATTERN_FIELDS`).
- **A saved loop keeps it** (its file's `arr.note_chance`, absent when
  none; `loops.load` hands `lane.note_chance`, {} when none): Edit and a
  launch put the loop's own on the lane, so a loop without one never
  inherits the last loop's; the open loop's mirror writes it back.
- **The working loop plays four passes**: the browser loops
  ONE WAV, so a loop rendered as one pass would rest the same notes every
  time round. A loop whose lanes hold any chance (`song_model.
  loop_chance`: the main lane's, a layer's; a follower rolls its main
  lane's) renders `LOOP_CHANCE_PASSES` (4) passes of its grid as its
  cycle (`song_model.loop_passes`, applied at the end of
  `svc.render_inputs`: every lane, the beat, the curves, the arp and the
  takes tiled as a section's flat cycle is; fewer passes when four would
  pass the 72 s loop cap). `RenderInputs.passes` is in its key (only
  then: no other key moves, and a loop with chance never asks for its
  one-pass key — no `RENDER_REV` bump), on the result
  (`RenderResult.passes`) and on its URL (`render.preview_url`:
  `/preview/<key>.wav?passes=4`). audio.js reads it: `loopDuration()` is
  one pass (the grid's, piano.js's, the launcher's playheads and
  `S.loopBar`), the buffer loops all four. The loop's MIDI file
  (`/export/loop.mid`) writes the four passes the audio plays; a bounce
  (`/loop/bounce`) is four passes long. Nothing changes for a loop with
  no chance.
- **The Song carries it**: a song pattern keeps
  `note_chance` (and `note_nudge`) — → Song, a scene's →§, Scene → Song,
  a drop on a lane, a section opened as the loop and back — and a section
  rolls each pass afresh (`song.md`, "Patterns carry timing and chance").
  The live voice and the loop coach ignore it.

`tests/studio/test_note_chance.py`, `test_song_chance.py`;
`tests/browser/test_song_chance.py`.

### The lane heads (Perform)

**The lane heads** (pick up the synths you made and switch layers
quickly): the lane inspector's first cluster (`lane_inspector.html`,
`.lane-heads`, one a line). A lane IS a layer slot, so the heads are
the sound's layers 1-4: one a slot that holds a sound (and the edited
one), each a small well (`.lane-head.seg`, ids `lh<N>-…`) of

- **N name** — which lane this grid, the keys and Synths edit: the
  Synths strip's own route (`/fragment/layer/select`, a body morph: the
  page stays), pressed while edited; ↳ when it follows; struck through
  while ▶ does not hear it (`StudioState.audible`: muted, or another
  soloed — its `title` says which);
- **▾** — the lane's sound: **the one sound menu** (`#sound-pick`,
  soundmenu.js, over base.html's `#lane-sounds`: yours ★ first, the
  factory set, the recipes, the core library's categories, the
  library's families — `sounds.menu`). It opens under the ▾ with the
  search focused: every word typed is matched against the name and its
  group ("acid", "core bass", "factory alien"), **★** keeps the sounds
  you starred on Synths (the favourites: no shelf of their own), ↑ ↓ move, Enter or a click picks, Esc closes; the
  lane's own sound is marked "now", a sound with layers "layers". The
  list is the page's copy as it opens, then `GET /fragment/sound-menu`'s
  (a sound saved since the page loaded: a view swap never redraws
  base.html). **Alt+S** opens the edited lane's (`data-keys` on its ▾;
  on the Song page, the editor drawer's track sound — `song.md`, "The
  drawer's sound: change it while it plays");
- **M** / **S** — the layer strip's own mute and solo
  (`/fragment/layer/mute`, `/fragment/layer/solo`; the transport comes
  back OOB and the pressed button keeps the keyboard by its id).

**A pick** posts `POST /fragment/perform/lane/sound` (`index`, `sound`
= `<source>:<name>` as `sounds.find` knows it, `as`) →
`StudioState.lane_sound`: the lane becomes the edited one — a load
lands in the edited lane, `svc.adopt`'s rule — and takes the sound as
**a load that says what it replaces** (`load_sound_as`, below). It is
**one pattern undo step** holding the whole loop (`_whole_loop`, as a
section launch: Ctrl+Z on Perform puts the lane's old sound, its other
lanes, FX and curves back, from whichever lane is edited by then — the
step selects the lane it was taken on first, `pop_pattern_undo`), and
the outgoing patch is one sound undo step on that lane's ring too. It
answers as a launch does (`_queued`): the loop changes at the wrap,
the keys at once — the preview's `preview-ready` has piano.js read
`/patch.json` again, which re-warms the live voice exactly as a Synths
load does (`live-voice.md`, "Live keys sound like the loop"). Nothing
renders differently: no render key moves for an unchanged session.

**What a load replaces** (`StudioState.LOAD_AS`): a pick asks nothing
when the stack holds one sound and the picked one brings no layers;
otherwise `#spk-choice` asks — **replace this layer** (the sound's patch
with its own FX and curves; the other lanes stay, a layered sound's
layers stay behind), **replace the whole sound** (the other lanes go:
the sound's own layers come in their place, or none —
`_load_alone`; "before last load" brings the stack back) or **＋ add as
a layer** (a free slot following the main lane at ±0; the edited lane
stays where it is; off when every slot holds a sound) — the default
focused, so Enter does what a plain load does: "the whole sound" for a
sound with layers, "this layer" for any other (`as=""` on the wire is
the same). ← → walk the three. `data-stack` on the heads (and on
Synths' layer strip) is how many sounds a load could replace.

**The last head** is the next empty slot (`lane_next`): **＋**
posts `/fragment/layer/add` with `own=1`: a copy of the edited sound in
a free slot, selected, **not following** — its own empty grid, which
sounds as soon as a note is drawn on it — and the page stays on Perform
(without `own` the route is unchanged: a following layer, landing on
Synths); its **▾** is a new lane with a sound picked from the menu
instead — the same own empty grid, no question asked (nothing is
replaced), undone whole by Ctrl+Z. The main lane is pinned first in
all of them, so what the other lanes play does not move. A lane that does follow (made on Synths, or bred)
shows the `lane-follow-note` with **draw its own** (`/fragment/layer/
follow` `on=0`), one click and no page change
(`test_input_matrix.py`). A sound a lane, by
mouse and keyboard, the layers switched, the keys playing each new sound
and Ctrl+Z: `tests/browser/test_lane_sounds.py`; the routes and the undo:
`tests/studio/test_lane_sounds.py`.

### Loops: one Rec, one loop

Perform is where a quickly made loop is perfected in the grid, and a
recording keeps both: the audio clip AND the loop itself — every key, its
position at the selected bar and rate — saved exactly as played, to
adjust and then move into the song.

**One ⏺ Rec is a loop** (`keys-and-midi.md`, "⏺ Rec"): the take's audio
(a clip, `source: "keys"`, kept) AND a loop made with it, named as the
clip (`studio/loops.py`, `studio_data/loops/<name>.json`,
`schema.nodes().LOOP`):

| key | what |
|---|---|
| `arr` | the note lane (bars, steps a bar, cells, cuts, velocities, slides), the tempo, the key and the meter — `state.model.LOOP_FIELDS` |
| `sound` | the whole sound that played it, with its layers (a format-2 preset's body) |
| `take` | the clip of its audio (absent on an older loop file, or one saved from the lane until it is rendered) |
| `played` | the notes **exactly as played**, `[start, length, midi, velocity]` in beats from the take's first downbeat — never rewritten; the arp's notes as they sounded |
| `quantise` | the strength the grid holds them at, 0-100 |
| `edited` | the grid was changed by hand since it was placed |

An older loop file without these keys reads as one with no audio and
nothing played (they are all optional: absent, a file conforms to the defaults;
`test_a_loop_from_before_these_keys_reads_as_one_with_no_audio`).

**The placement** (`loops.place`): the notes as played on a grid of the
take's bars, rounded up (`loops.grid_bars`: at most the Arrangement's 4,
and `MAX_GRID_STEPS`; a longer take keeps the rest in `played`), at the
**rate** — the meter's own steps (`meter.step_choices`: 1/4, 1/8, 1/16,
1/32 in 4/4; 3, 6, 12, 24 a bar in 3/4; no quarter-note step in an x/8
meter; **no triplets**, which the note grid has no steps for in a duple
meter) — the meter's sixteenths first. A note starts on **its step by the
strength**: within `strength`% of half a step of a grid line it goes onto
that line, else it stays in the step it began in (`loops._step_of`: 100
= every note on its nearest step, the default; 0 = **as played**, each
note in the step it began in). It is held for its length in whole steps
(at least one); one starting where its row still sounds is a cut; a
step's velocity is the loudest note starting there — the MIDI import's
write (`midi_bridge.to_grid`) on every pitch the notes play. **The step
grid holds no timing inside a step**: "as played" timing is kept in the
loop (`played`) and heard in its audio; the grid, the pattern a song
plays and a bounce are on the steps. (A **free loop** —
free record's shaper, "Free record and the shaper" below — does keep its
timing on the grid: `note_nudge`, the drum hits' `nudge` for notes, and
its Q keeps it below 100%. A take's loop is placed as here.)

**The Loops list** (`studio/looplib.py`, `fragments/loop_rows.html`): every
loop and every keys take no loop names yet (an older take, or one
taken while the store was full), newest first — **one list**, drawn in three
places: the clip library's **Loops** group at its top (every page: the
rail's button, whose count is the loops' — `looplib.context`'s `total`,
the search aside — with "· clips N" beside it on the comfy top bar, the
clips that are not a loop's audio), **Perform's Loops panel**
(region `loops-panel`, `fragments/loops_panel.html`, a slide-out module
of the page — "The Loops panel" below), and the keyboard dock's strip (this
session's: `keys-and-midi.md`, "The Loops strip"). Each is a region with
its own watch, re-sent by whatever moves it — so a ⏺ Rec on Perform
lists its loop in the panel at once: keyrec.js's `fetch` carries the page's
`HX-Current-URL` (events.js `pageHeaders`, as every island's fetch post
does; without it the server would read the post as Synths' and never
re-send the Perform-only list). A row: ▶ in time, its
notes as a mini piano roll (`loops.roll_of`: one SVG path of runs,
cached with the file) or a beat's hits as a mini step grid
(`loops.beat_roll`), its name (`title`) · bars · rate · bpm · key · sound,
☆ / ★, its tags, and
**Edit** (a beat: **Open**), **→ Song** ▸ pattern | audio, **Q** ▸
(Quantise), **#** (tags), **✎**, **✕**.
A take's row posts `take=` and its first action makes its loop
(`routes/takes.py` `_loop_named`: from its notes as played and the sound
that played it) — nothing recorded before is lost. The drawer's search
reads the Loops too, as the panel's does (`looplib.matches`); a keys take
is in no clip group (one row, not two). **loop the lane** (1 / 2
/ 4, the panel's foot) saves this lane as a loop, named by the prompt
(blank: from what it is); when its bars are the grid's, the grid then
edits it.

- **Edit** (`POST /loop/edit`; lands on Perform, the whole page): the loop
  on the edited lane **as the loop** — its notes, its sound with its
  layers (`load_sound`: one sound undo step), its tempo and key and meter
  (another meter: the loop's other grids fitted first, `set_meter`; refused
  past the loop cap), the octave window where its notes are; **one pattern
  undo step, which is the edit's mark**. From then on the grid **is the
  loop**: `StudioState.loop_edit` (runtime, **one entry a lane** —
  `{lane: {name, layer, mark, base, row, bars}}`: the launcher's
  playing slots are open loops too, "Slots and scenes" below) names it, a
  banner on the note grid says "✎ editing the loop “…” — every change to
  these notes saves to it" with its **Q** and **Done** (the loop named as
  the Loops lists name it — `looplib.titles`, never the clock's file name
  "keys-20261106-005322"; so do the
  launcher's slots and every toast that names a loop),
  and after every request (`routing._track_edit_after`, the one choke
  point, `StudioState.mirror_loop_edit`) the lane's notes, tempo, key and
  meter are written back to the loop when they moved (`edited` set when
  the notes did). The list draws the open loop by its name alone
  ("✎ editing", `looplib.watch`), so a stroke on the grid redraws no list.
  **It ends** at **Done** (`/loop/edit/done`: the loop keeps everything,
  the notes stay on the grid as the working loop's), when another loop,
  take or section is put on the grid (`/loop/load`, `/take/keys/notes`,
  `section_to_loop` — the Song page's "Open in Perform" —, a loaded
  session; on its own lane only: another loop on that lane), when the
  loop is deleted, and
  at **Ctrl+Z past its opening** (`pop_pattern_undo` popping the mark: the
  grid is the working loop's as it was before Edit, and the loop keeps
  every edit made). **Undo** inside the edit is the pattern ring's as
  ever: each Ctrl+Z on Perform is a request, so the loop follows it back.
  Another lane's edit writes nothing to the loop (the banner says the edit
  is lane n's): each open loop is mirrored from its own lane — the edited
  slot's from the arrangement, another's from its stash (`lane_state`) —
  and only when that lane moved, so a request writes the files of the
  lanes it changed and no others. A Synths sound edit is not the loop's:
  the loop keeps the sound it was recorded with.
- **→ Song** (`POST /loop/song`, one song undo step; the first loop into an
  empty song sets its tempo and key): **pattern** — a new last section of
  the loop's bars, its notes a pool pattern on a synth track that plays
  its sound (its other sounding lanes too; a track already playing that
  sound is reused: `song_bridge.add_lanes_section`, no beat, no drums
  track); **audio** — a new last section with its audio as a take from
  its first bar on an audio track (`add_audio_section`); a loop with no
  audio is rendered first (the bounce below) and that clip is its audio
  from then on. A beat → Song is a section holding it on the drums track. Asked, not refused (`tosong.py`, a
  `song-ask` popover): past the song's 8 synth tracks, which track takes
  a lane — its sound replaced, or kept — and a plain → Song the song
  already holds (`tosong.already`: another section holds all it would),
  "Place it again?". On the Song page a row drags out of the drawer
  (songdrag.js): a loop of notes onto a synth lane, a beat onto
  the drums lane — a pattern there from the bar under the pointer, in the
  track's own sound (`/loop/song` `section_id`, `track_id`, `at_bar`) —
  onto a section's head (a new section before or after it,
  `at_section`), and its audio onto an audio lane as a clip row does.
  `song.md`, "Bridges" and "Drags onto a lane".
- **Q** (`POST /loop/quantise`, a pattern undo step when the loop is open
  on the grid): the notes as played placed again at a rate (the meter's
  steps) and a strength (as played, 25, 50, 75, 100%) — **never
  destructive**: `played` is never touched, and placing again always
  starts from it. A grid edited by hand is replaced; the buttons ask
  first (`edited`). **✦** in the same menu (a row with a take's notes): the
  Song take's clean-up on its take's notes (`keytakes.clean` →
  `takeclean.clean`, in the take's key; with a Gemini key `takecoach.coach`
  + `takeclean.apply_edits`, outside the lock), placed on the loop's grid
  at 100% (`_place_take_loop`); **↶** places the notes as played again.
- **✕** (`/loop/delete`, `audio=1`): the loop and its audio — unless the
  song (or anything else) uses that clip: it then stays in the clip
  library, and the toast says so. A take's ✕ is `/take/keys/discard`.
- **✎** (`/loop/rename`): the loop; the clip of its audio keeps its own
  name. A clip renamed anywhere takes its loop's `take` with it
  (`loops.clip_renamed`, from `rename_clip_everywhere`).

Older routes still answer: **→ notes**
(`/take/keys/notes`: a take's notes and sound onto the grid, quantised
over the take's length — `keytakes.to_grid`), **→ sample**
(`/take/keys/sample`: its audio as the lane's sampler sound), a loop's
**→ Perform** (`/loop/load`: a copy on the grid, no edit), **⤓ audio**
(`/loop/bounce`: the loop's synths rendered — `svc.render("perform",
patch=, chain=, mix=True, arrangement=, parts=("synth",))` — into the
clip store, the session untouched), ★ Keep and "clear takes" (an older
take). The list does not offer them: Edit and → Song are the flow.

`tests/studio/test_loops.py`, `test_key_takes.py`,
`tests/browser/test_key_takes.py` (the journey: record 2 bars ->
Loops -> Edit -> an edit saves -> → Song -> the song plays it).

### The Loops panel

Past loops, easy to find in a panel that slides out, to pick one up,
tweak it and build on it.

- **Where.** `#loops-dock` in `views/perform.html` (`data-module=
  "perform.loops"`), grid area **`loops`**, the page's left column beside
  every row under the head, in both breakpoints (style.css 9b4 and 14c,
  dense.css D18 / D35). Three states on `<html data-loops>` (loopspanel.js,
  per browser in `localStorage` `synthlab.loops`: `{open, pin, kind,
  sort}`): **pinned** — a column of `--loops-w` (340 px comfy, 320 dense);
  **folded** — a 26 px tab down the page's edge ("▸ LOOPS"); **over** —
  open and not pinned: the panel stands at its width over the page from
  its tab column, nothing under it moves, and a press anywhere outside it
  (not a menu, a prompt or a dialog) or Esc inside it folds it. Unset, the
  window decides: from 2200 px open and pinned, below it folded (so
  pinned at 2560, folded at 1920). **◀** folds, the tab opens, the pin button pins
  or lets it slide over; **Alt+O** opens or folds it from anywhere on
  Perform but a field (not Alt+L: that is the keyboard dock's layout;
  `data-keys` on ◀). Under 900 px it is a block in the
  stacked page. In a laid-out workspace (`[data-ws-on]`) it is a module
  like the others: its tab or its body in its group, never over the page.
- **What is the page's, what is the region's.** The head, ◀, the pin, the
  search box and the kind / ★ / sort buttons are the page's — never
  redrawn, no `name` on any of them (htmx.ajax sweeps named inputs) — and
  the list under them (the tags in use, the rows, "loop the lane") is the
  region `loops-panel` (`_w_loops_panel`: `looplib.watch` with the bank).
  The filter is a style rule loopspanel.js writes (`#lp-filter`, outside
  the list) over the rows' `data-find` / `data-what` / `data-star` /
  `data-tags`, so a morph of the list shows the rows already filtered; the
  sort moves the rows after every swap. The search words are never kept (a
  reload shows everything); the kind and the sort are. The head counts the
  rows shown ("2 / 13").
- **Names from what it is.** A loop recorded under a name the clock gave
  (`loops.AUTO`: keyrec.js's `keys-YYYYMMDD-HHMMSS`) is named from its
  sound, what it plays (`loops.plays`: "chords" when three notes start
  together, else "riff"): **"Acid bass riff"** (`loops.title`,
  `auto_name`: no key, tempo or bars in it — the row's facts say them;
  "… 2" when taken; the take's clip keeps its clock name,
  `X-Loop-Name` says the loop's, percent-encoded). A loop's name may hold
  "·" and "#" (`loops.loop_safe`: `safe_name`'s `extra`). An older loop named by
  the clock keeps its file and **shows** that name (`title`,
  `looplib._unique_titles`: two alike are told apart) until it is renamed;
  ✎ offers the title. An older name with the facts baked in ("Acid bass
  riff · Am · 124 · 2b") keeps its file and **shows without them** while they are
  exactly the loop's own key, tempo and bars (`loops.plain`; "… 2" kept,
  any other suffix shown as it is: someone wrote it).
- **A row's facts, once, as read-out cells** (`looplib.facts`,
  `FACTS`; loop_rows.html `facts`; style.css 45): bars, grid (a step as a
  note value), tempo, key (`key_short`), the take's length as a clock,
  then the rest (hits, lanes, a meter not 4/4, the sound when the name
  does not say it, a bank slot) — each a fixed-width Bahnschrift tabular
  cell, so they line up down any list (the Loops panel, the dock's strip,
  the drawer); a cell a row lacks holds its column empty. The words
  spelt out (`row["detail"]`, "2 bars · 1/16 · 124 bpm · A minor") are
  the cells' tooltip. The panel's narrow column drops the "bpm" unit. "loop the lane" with a blank name and ✚ Keep name
  theirs the same way. A beat: its bank or style name, "beat", tempo and
  bars ("Techno beat · 124 · 1b").
- **Search** (`looplib.matches`, the drawer's too): every word must be in
  the row's `find` — its title and name, sound, key both ways ("am",
  "a minor"; a beat has none), "124 bpm", its kind (notes, beat, loop,
  take, bank), meter, `#tags`, ★. **Kind** all | notes | beats | ideas |
  sections (a scene of the launcher kept, and the song's
  sections — both open as a scene, "Slots and scenes"); **★**
  favourites only; a **tag** chip (the region's bar of the tags in use, or
  a row's own) filters by it, again shows all. **Sort**: date (newest
  first, the bank's slots last), name, bpm (none last), key
  (`looplib.rows`' `rank`: the tonic up from C, then the scale; a beat
  last).
- **★ and tags** (`POST /loop/star`, `/loop/tag`): optional keys of the
  loop's file, absent when unset (`schema.nodes().LOOP`'s post hook: an
  older loop file, and one unstarred again, is written as it was). Tags
  are one prompt of words (`#` on a row, prefilled with its tags): lower
  case, no "#", each once, 12 of 24 characters at most
  (`limits.MAX_LOOP_TAGS`, `MAX_TAG_CHARS`); an empty answer clears them.
- **Beats are loops.** `kind: "beat"` with the drum lane in **`beat`** — a
  PATTERN as the bank keeps one (steps, bars, steps a bar, swing, hits,
  row swings; not the kit: the Kit page keeps kits) — never in `arr`, whose note
  lane stays empty and which keeps the tempo, key and meter
  (`loops.save_beat`). They come from **✚ Keep** on Perform's drum rack
  (`POST /loop/beat/keep`: the grid's beat, named from what it is; a beat a
  loop already holds is not kept twice), from the Kit page's **✚ Save beat**
  (the same route with the form's `name`, "… 2" when a loop has it — the
  page's only save, `drums.md` "The Kit page"), and
  from the bank: **the bank's 8 slots show as beats in the panel**
  (`looplib._bank_rows`, `ref {"slot": n}`), and so does each beat of the
  beat library no loop or slot holds (`kind` "lib", `ref {"lib":
  name}`) — a view: nothing is copied
  until ★, tags or a rename keep one as a beat loop (`_slot_kept`), and a
  slot whose beat a loop holds is listed once, as the loop
  (`loops.beat_key`: the pattern's content, its name aside). The drawer and
  the dock's strip list kept beats, not the 8 slots (the drawer is every
  page's; `loops_context(bank=)`) — except the drawer on the Kit page,
  whose Beats ▸ must reach every beat, the page having no bank. **Open** (`POST /loop/beat`, one pattern
  undo step): the beat on the drum grid (`apply_pattern`; a slot as the
  bank loads one, the active slot following) — the notes, sound and tempo
  stay; on Perform it takes over at the wrap (`preview-ready queue`), from
  another page it lands on Perform. Edit on a beat is Open. A beat
  loop opened is **open on the drum lane** (`loop_edit[DRUM_LANE]`):
  its edits on the grid save back to it (its `beat`, under the name the
  bank or its style gave it), until a bank slot or a pool beat is put on
  the grid or Ctrl+Z goes past its opening; ✚ Keep keeps the edited beat
  as another. → Song is notes only
  (as audio a beat bounces; as a pattern the route says to Open it and use
  Perform's → Song).
- **▶ in time** (every Loops list; loopspanel.js): on
  the next bar line of what plays (`S.nextBar`), else at once; one
  audition at a time. The take's audio when the row has one and nothing
  plays or what plays is at its tempo; otherwise `POST /loop/preview`
  (`bpm` = the page's tempo while it plays) answers `X-Preview-Url`: at the
  loop's own tempo its audio — the take, or a render made now and **kept
  as its `take`** (`_bounce`: the clip library holds it, and the row's ▶
  plays it without asking next time); at another tempo a render of its
  notes (or its beat) at that tempo from the render cache
  (`/preview/{key}.wav`, `_render_loop`) — in time and in tune, where the
  take's audio would drift (or, sped up, change key): **as played** feel
  is then the grid's, not the take's. A bank slot renders through the
  session's kit (nothing kept).
- **Drag** a panel row onto the note grid (`#transport`) or the drum grid
  (`#perform-drums`): its Edit (notes) or Open (a beat) is pressed, as a
  click; the grid it opens on is outlined while it drags
  (`html[data-lp-drag]`). Onto the Song is the drawer's drag (the Loops
  panel is the Loops page's; the drawer is on every page): a loop onto a
  lane of its kind or a section's head, its audio onto an audio lane
  ("→ Song" above). An idea's **→ Song** makes it a
  section ("Slots and scenes", "To the song").

`tests/studio/test_loops.py` (names, search, ★ and tags absent until set,
an older file written back as it was, the bank as beats and a kept one
once, ✚ Keep, Drums' Save, Open, ▶'s audio and render),
`tests/browser/test_loops_panel.py` (folded / over / pinned, Alt+O, the
search, kind, ★, a tag chip, the sort across a redraw, ▶ while the loop
plays; a row dragged onto the grid edits it and the edit saves; a bank
slot opened, ✚ Keep, the kept beat listed and opened).

### ✦ Coach

The coach assesses the loop being made, offers options and clean-ups for
consistency, and talks; each change it makes is accepted or reversed.
`studio/loopcoach.py` (the rules, the
options, the session), `studio/loopchat.py` (the talk: `agent.md`, "The
loop coach"), `routes/coach.py`, `fragments/coach_panel.html` (region
`coach-panel`), `static/coach.js`; `tests/studio/test_loop_coach.py`,
`tests/browser/test_loop_coach.py`.

**Where.** **✦ Coach** in the page's head tools (a `data-cmd`, **Alt+K**)
opens `#coach-dock` (`data-module="perform.coach"`): the
**lane inspector's other tab** (Lane | ✦ Coach in its head; "The Loops
page, laid out" above) — open, it stands in the inspector's body in the
lane's place; closed, `[hidden]`, the lane shows (style.css 14d / 42). A
workspace module still — it pops out and focuses, moving with the
inspector. Its head, the
message box and 🎙 are the page's and never redrawn (a message being
typed survives every edit); the body is the region, whose watch is the
panel's own context (`loopcoach.context`, memoised on everything it
reads: the loop, the key, the chord lane, the window, the lane names,
the session's revision). Open / closed is per browser (localStorage
`synthlab.coach`).

**The loop as the coach reads it** (`loopcoach.read` / `write`): every
lane that plays notes of its own (the edited one from the arrangement;
another slot from its `cells` and its `seq`'s cuts, velocities and
slides; a following lane plays the main lane's and is none) and the beat
(`drums`, `drum_vel`, `drum_hits`). Clocks, key, meter, sounds: read,
never written. Notes are runs (`notes`, `with_notes`: cells and the cuts
between two notes of a pitch; a velocity stays on its start step, the
grid's rule).

**Assess** (instant, no network): `RULES`, each at most one finding a
lane with a one-line why and its fix as a whole new doc — notes **off the
key** (`takeclean.to_key`), held notes **off the chord** under them (on a
beat, when the loop has a chord lane), **hidden** notes (off the roll's
rows: "Show them" moves the rows, `StudioState.show_notes` — the notes
play, so no note is moved; past the end: the grid's tidy), a **semitone
rub** inside a lane, a single line's **held note under the next**, a few
notes **off the grid** the rest sit on (a beat or an eighth), the **bass
a sixteenth off the kick**, **one bar that breaks** what the others
repeat (not a different last bar: a turnaround), an **empty or crowded
bar**, a big **leap at the seam** (the last note into the first), **two
lanes in one register**, **flat or erratic velocities**; on the beat,
flat hats and an empty bar. **Clean up all** makes every fix in that
order, each on what the ones before left (`clean_all`: a second pass
finds nothing to fix).

**Options** (`loopcoach.options`): the edited lane tighter (the key, rubs,
smears, the grid), humanised (accents and a seeded jitter), the last bar
varied (its second half walks up the key into the loop's first note),
call and response (every other bar answers the one before: its rhythm,
the line inverted in the key), on the chords (with a chord lane), busier
and sparser (`gen/loop.vary`, a 16-step 4/4 grid); the beat humanised, a
snare fill into the top, lighter hats.

**Accept or reverse.** A fix, Clean up all, an option or a reply's edits
is a **proposal** (`StudioState.coach["proposal"]`: its `base` — the doc
it was made from — and its `after`): nothing reaches the loop. It is
drawn on Perform's two grids (`#coach-panel[data-diff]`: coach.js puts
`.coach-add` — outlined in the AI's violet — and `.coach-del` — dashed in
red, dimmed — on the cells; another lane's changes are listed, "select
the lane to see it"); **▶ proposed** renders the loop as it would be
(`loopcoach.with_doc` around `svc.render_inputs("perform")`, then
`render_preview`: the one cache, so Accept plays the very render ▶
played), **▶ as is** the loop as it is. **Accept** (refused when the loop
moved since the proposal: "ask again") is ONE pattern undo step holding
the whole loop (`StudioState.whole_loop_step`, the lane heads' sound-pick
mechanism: every lane, the beat, the sounds), so Ctrl+Z on Perform puts
it all back; an open loop edit saves to its Loop as any edit does
(`mirror_loop_edit`). **Discard** drops it. Each accepted change stays in
the thread with **↶ Reverse**, which says which it will do: while its
step is the newest and nothing moved since, it **undoes** that step
(`pop_pattern_undo`); otherwise it **restores** the loop as it was before
the change, as a new undo step of its own (what came after goes too, and
Ctrl+Z puts it back). The session is runtime, dropped with a loaded
session (`persist._apply`: its steps were the old ring's). No render key
moves for an unchanged session.

### Slots and scenes (the launcher)

Make many ideas, switch quickly: Ableton's Session view on this studio's
one working loop.

- **Where.** `#perform-slots` (`views/perform.html`, `data-module=
  "perform.slots"`, grid area **`slots`**: the top of the
  centre column at every size, under the live strip, the editor under it
  — style.css 41 / 42, dense.css D41). Its head — the quantise, ■
  all — is the page's and never redrawn; the grid is the region
  **`slots`** (`fragments/slots.html`, `StudioService.slots_context`,
  `_w_slots`: exactly what it draws).
- **The model** (`state/slots.py`, the `Slots` mixin; pure helpers
  `loop_window` / `regrid_lane` in `state/model.py`). **Lanes** are the
  working loop's: the four layer slots, then the drums (`DRUM_LANE` = 4).
  **Rows** are scenes. The grid is `perform_slots` — persisted, a schema
  node (`schema.PAYLOAD.perform_slots`: rows of `{slots: [5 loop names or
  None], name}`), **absent when unset** (`schema.PAYLOAD_OPTIONAL`,
  `_payload_post`: no row holds anything — trailing empty rows go), in the
  **"Sequence"** reset scope (absent from a snapshot while unset, as the
  optional arrangement fields are: `scopes._OPTIONAL`). No migration: an
  older file reads as an empty launcher. A slot is a loop's **name**: a
  loop renamed or deleted follows (`slots_renamed`).
- **Slots compile to the working loop.** A launch (`StudioState.launch`)
  puts each picked loop on its lane — a loop of notes: its note lane into
  that layer slot (the edited slot's through the arrangement, another's
  through its cells and `seq`) and its sound (its patch, the main lane's
  FX and curves: a lane is one layer; the edited slot's as a load, "this
  layer", one sound undo step); a beat: `apply_pattern` on the drum lane —
  unmutes the lane, makes it its own (not following) and opens the loop
  on it (`loop_edit`, `row` = the launcher's row): **the playing slot is
  the open one**, and an edit on its lane saves to its loop. The render
  path does not change: an unchanged session (a launcher filled but not
  launched) renders bit-identically, and no render key moves, so
  `RENDER_REV` stays (`test_an_unlaunched_launcher_moves_no_render_key`).
- **One grid for the lanes.** The note lanes share the arrangement's bars
  and steps a bar: a launch leaves the finest steps of what plays (a whole
  multiple — one meter's choices are — so every lane is regridded with
  nothing lost) and the longest bars (a launched loop's own, a lane that
  keeps playing its slot's, any other heard lane's the grid's). A shorter
  loop repeats across it (`loop_window`); a lane that keeps playing
  repeats when the grid grows (`patternops.repeat_into`); a shorter grid
  hides what lies past it, never deletes. A slot's loop is written back at
  its own length while its lane is still a repeat of it, else at the
  grid's (an edit past its first bars makes the loop that long). Another
  meter is refused (the toast names it); so is a grid past
  `MAX_GRID_STEPS` or a loop past the length cap (`loop_refusal`).
- **A scene** (a row's ▶, Alt+1…8) launches every lane's slot at once —
  **one render, one undo step**; a lane whose slot there is empty stops
  (as an empty slot of a scene does in every launcher; a slot with no
  sound has nothing to stop). **■** a lane (the foot; Delete on a slot;
  "■ all", Alt+0) stops it at the quantise point: a layer slot muted, the
  drums out of `perform_parts` — its notes stay, a slot plays it again.
  Every launch and stop is ONE pattern undo step holding the whole loop
  (`_whole_loop`, as a section opened: the lanes, sounds, tempo, the
  launcher's rows, `perform_parts`, the open loops); placing, moving and
  emptying a slot is a step of its own (`{"slots": …}`) that touches
  nothing that plays.
- **Quantised, never late.** The answer is a queued preview (`preview-
  ready` `queue`). audio.js asks slots.js's `launchAt` where it starts:
  the next bar line (as every Perform launch), the next fourth bar from
  the loop's start, or where the playing loop comes round (`bar | 4 bars |
  loop`, per browser: `localStorage` `synthlab.slots`) — computed when the
  render has arrived, so a render that missed one point lands on the next,
  as the jam's. Measured (an i9-14900K desktop, a 4-bar scene of four
  synth lanes and a beat at 124 bpm, a bar 1935 ms): a cold scene 528 ms,
  a scene of all new lanes 131 ms, one sharing lanes and the beat (stems
  cached) 100 ms, a scene already rendered 39 ms (median 42 ms warm) —
  well inside a bar.
- **Selection, not launch, moves the editor**. A slot's own click (or
  Space on it, or Enter while it is not selected) **selects** it — and
  ONLY selects it: slots.js posts
  `/fragment/perform/slot/select` and its lane becomes the edited one — the
  note grid, the keys and Synths edit it, the lane inspector shows it, as
  its lane head's name would (`select_layer`, before the launch, so the
  launch's undo step is tagged with that lane) — and Notes | Drums turns
  to the slot's kind — from Both too: one editor, as the slot is; a beat's
  slot selects no layer.
  **A filled slot launches by its own ▶** (`.sl-go`, a small button at
  the cell's right, lit on the selected slot; its post carries `select`,
  `_select`), **a double-click**, or **Enter on the selected slot**; ▶ on
  the slot that **already plays** only selects it (nothing renders, no
  undo step, nothing waits; from silence it starts ▶). A refused launch
  (another meter) selects nothing. **A scene's ▶,
  Alt+1…8, the deck's scene keys, ■, an idea or a section opened never
  move the editor** — they launch; what you edit is what you picked. An
  empty slot's ＋ selects its lane once it holds the kept loop.
- **The selected slot**: the selection is also
  drawn — `aria-selected` on the cell, the accent's ring; an empty one
  reads "⏺ rec here" — and kept in the page per tab (slots.js,
  sessionStorage `synthlab.slotSel`, drawn again after every swap). **An
  empty slot's click selects it** (not ＋: `POST
  /fragment/perform/slot/select` makes its lane the edited one when that
  lane has a sound, so the keys play what the slot will hold — a lane with
  none keeps the edited one, and the take carries that sound there;
  nothing renders, no undo step); a click on the selected empty slot lets
  it go. Its ＋ is a small `.sl-plus` in the cell. The selection is where
  ⏺ Rec, ⟲ keys capture and the keys arp's print land ("Rec into a
  slot", below).
- **The hand** (slots.js, island 76): a click on a slot selects it (an
  empty one too), its **▶** / a double-click / Enter on it selected
  launches it, an empty slot's **＋** keeps what its lane plays as a new loop there (open
  at once: it is what plays), **✕** empties a slot, a row's **⤓** keeps
  the scene as an idea; a Loops panel row drags onto a slot of its kind
  (place), a slot onto another (move; Ctrl or Alt copies). A launched
  slot is drawn waiting (`data-q`, the accent's dashed edge, the
  transport's ring) until audio.js's `loop-switched`, over the server's
  `data-state="playing"` (the region comes back with the answer; slots.js
  re-applies the wait after every swap; a 422's refusal drops it). The
  grid is one tab stop with a roving focus: arrows move, Space selects,
  Enter selects or (on the selected slot) launches, Shift+Enter launches
  the row's scene, Delete / Backspace stop
  the lane; **Alt+1…8** a scene and **Alt+0** stop all from anywhere on
  Perform but a field (in the drum pane Alt+1…4 stay the beat's
  variations, steps.js). A launch from silence starts ▶.
- **Ideas** (`loops.save_idea`): a scene kept is a loop of kind `idea`
  (schema `LOOP.idea`: the five slots' names; `arr` its tempo, key and
  meter; absent when unset), listed in the Loops panel's "ideas" (not the
  drawer's or the strip's). **Open** lays it out in the first empty row
  and launches it at its tempo, key and meter (another meter fits every
  lane first, `set_meter`) — one undo step.
- **Song sections** (the Loops panel's "sections"): **Open**
  keeps each of the section's synth lanes (its main lane, then the
  others, as `section_to_loop` orders them; a fifth goes) as a loop named
  "<section> · <sound>" and its beat as "<section> beat" — one that
  already holds the same notes in the same sound is reused, so opening
  it again makes no copies — lays them in the first empty row under the
  section's name and launches them at its tempo, key, meter, kit and
  drum-bus FX. Ctrl+Z puts the loop and the launcher back (the loops
  stay in the panel).
- **Stream Deck**: the jam's key page (Scene 1-4, Stop all) works on
  Perform: the launcher declares the same commands (`Launch scene N`,
  `Stop all`), a scene's key lit while all of it plays (`data-state="on"`).

**To the song** (`tosong.py`,
`song_bridge.add_scene_section`; `song.md`, "Bridges"):

- **→§ on a filled row** (`POST /fragment/perform/scene/song`, `row`) and
  **→ Song on an idea** (the same, `idea`): a new last section named for
  the scene ("Scene N" unnamed) — each slot's loop of notes a pool pattern
  on its own grid, on a synth track that plays its sound (its main layer:
  a lane is one layer, as a launch plays it; a track that already plays
  it is reused), its beat on the drums track (the session's kit makes it
  when the song has none). As long as its longest loop (a shorter one
  goes round in it, as on the launcher), in the scene's meter; a row is
  at the loop's tempo and key, an idea at its own; the first section of
  an empty song gives the song its tempo and key, and a later one at
  another tempo keeps it as the section's own (the toast says so). A
  slot's loop in another meter is left out (the toast names it). One
  song undo step (Ctrl+Z on Song). Past 8 synth tracks it asks which
  track takes each lane that needs one; a scene the song holds already
  asks whether to place it again.
- **Scene → Song** (the launcher's head — not "⟲ Capture", which is the
  keys'; palette "Scene →
  Song: the scenes you launched, as sections"; `POST
  /fragment/perform/scene/capture`): every scene launch, logged when it
  SOUNDS (slots.js, on `loop-switched`) with the loop names its slots held
  then, and every stop (■ all; the transport stopping), on a bar clock
  that stands still while nothing plays — the jam's rule. The button
  counts the launches; it posts the log and the bars so far (the bar in
  progress counted whole), and the server makes each distinct scene a
  section it never adds itself and runs the jam's capture over them
  (`jam.commit(scenes=)`): a scene left to loop is its section repeated
  (Intro four times = "Intro" ×4), one cut short plays its first bars, a
  rest between two is a "Jam rest". One song undo step; `scenes-captured`
  drops the log (a refusal keeps it). **Only scene launches**: a slot
  launched alone or a lane stopped in between is not captured. No server
  state: the log ends with the page.
  Past 8 synth tracks Capture refuses — → Song a scene first to choose.

Routes (`routes/slots.py`): `/fragment/perform/slot/place | keep | clear
| move | launch | stop`, `/fragment/perform/scene/launch | keep | idea |
section | song | capture`. `tests/studio/test_slots.py`,
`tests/studio/test_to_song.py`, `tests/browser/test_slots.py`,
`tests/browser/test_to_song.py`.

### Rec into a slot, ⟲ keys capture, print the arp

Something good just played is never lost, and a take lands where it is
wanted.

- **⏺ Rec records into the selected slot** of a lane of notes: an empty
  slot gets the take's loop (**new**), a filled one by the dock's choice
  beside ⏺ Rec — **overdub | replace** (`#key-how`, shown only while a
  slot is selected; per browser). The take is still its audio and its own
  loop in the Loops panel (`keytakes.py`, `loops.place`); then
  `slotrec.land` (`routes/takes.py` `_landed`): *new / replace* sets the
  slot inside a launch of the take's loop there (`StudioState.launch`'s
  `setup`: ONE undo step, queued for the quantise point, so the take plays
  from the slot); *overdub* launches the slot's loop first when its lane
  is not playing it (a step of its own), then
  `StudioState.overdub_lane` folds the take's notes onto that loop's own
  bars from the bar of the loop the take began on (`bar`, keyrec.js from
  slots.js `S.loopBar`) and merges them by `record.merge`'s rules — a
  shared step's velocity rewritten, a repeat on its own pitch a cut — the
  held steps of a long note kept (never cuts); the lane is laid back over
  the grid as a launch lays it (what lies past the grid stays hidden), ONE
  whole-loop pattern undo step, and the loop open on the lane saves it
  (`mirror_loop_edit`) at its own length. A refusal (another meter) keeps
  the take as a loop and says why in the toast. A beat's slot, no slot,
  or a take a page change ended: a plain take (its loop in the Loops
  panel, in no slot).
- **⟲ keys capture** (`#key-capture`, Alt+T, the dock on every page) and
  **print** (the keys arp's ⚙ panel): keyrec.js's always-on note buffer —
  `keys-and-midi.md`, "⏺ Rec" — cut to the last bars or the latest phrase,
  posted to `POST /take/keys/capture`: a loop with no audio at the page's
  tempo, landing in the selected slot as a new loop when it is empty —
  **a capture never overwrites**: with a
  filled slot selected it lands in the lane's next empty slot (after it,
  round to the first scene; `slotrec.capture_target`, the toast saying
  "The selected slot holds a loop: this went into scene N"), and with
  none empty it is a loop in the Loops panel only ("Lane N has no empty
  slot … nothing was replaced"). The dock's overdub | replace is ⏺ Rec's
  alone; the keys arp's print is a capture and follows the same rule.
  Not the launcher head's "Scene → Song" (the scenes as sections).
- **⏺ resample** is the live strip's master-bus recording into a clip
  (label, palette "Resample: record what you hear to a clip", the deck's
  key, the empty states), named so it reads apart from ⏺ Rec.

Routes: `POST /fragment/perform/slot/select` (`routes/slots.py`), `POST
/take/keys` (gains `lane`, `row`, `how`, `bar`), `POST /take/keys/capture`
(`routes/takes.py`). `tests/studio/test_slots.py`, `test_key_takes.py`;
`tests/browser/test_key_takes.py`, `test_slots.py`, `test_keys_arp.py`.

### Free record and the shaper

One button live-records every key and its timing until it is stopped —
no bars, no 4/4, just playing — and the take is then shaped, cut and
sized into a loop.

- **⏺ free** (`#lp-free-rec`, the Loops panel's head; the dock's length
  select has **free** too — `keys-and-midi.md`, "⏺ Rec"): no count-in, no
  bar line, no length. The take starts on its **first note** (keys, pads,
  MIDI; the keys arp's notes flagged) and ends where ■ is pressed — never
  past `limits.MAX_FREE_TAKE_SECONDS`, 60 s (every other take stops at
  the clip cap, 30 s) —, its audio cut to that. A page change
  ends it as ⏺ Rec's (before a note: nothing taken). `POST
  /take/keys/free` keeps it as a keys take (meta `free: true`, the arp's
  notes' indices in its sidecar's `arp`) and makes **no loop**: its answer's
  `free-shape` opens the shaper. Until a loop is made of it, the Loops list
  shows it as a take with **✂ Shape**.
- **The shaper** (`static/shaper.js`, `fragments/free_shaper.html` drawn
  into base.html's `#free-shaper`, a manual popover on every page: the
  launcher's slots stay clickable under it; `GET /fragment/free/shaper`).
  A **time-true roll** — seconds, not steps — of the take as played, the
  loop's bars, beats and steps drawn over it:
  - **cut**: the two green handles trim the start and end; a click on
    empty roll puts the cursor and ✂ split (S) cuts every note under it;
    a note's click picks it (Shift adds), a drag on empty roll picks a
    box; ⌫ delete, ⊡ keep (only the picked, trimmed to them), ⇤⇥ trim to
    the notes; a note's drag moves it (time and pitch), its end's drag
    resizes it, arrows nudge the picked ones; ↶ / Ctrl+Z, "as played".
  - **size**: 1-4 bars (the grid's), **fit** — what is between the handles
    stretched or squashed to fill them (×1.25 says by how much) — or
    **crop** — the timing kept at the tempo, past the bars cut. The tempo
    starts as a **guess from the playing** (`guessTempo`: the onsets on a
    grid of eighths, the beats weighing more, the length near whole bars,
    a pull towards 115; 70-160 bpm), ✦ guesses again from what is between
    the handles, "= 124" takes the page's.
  - **feel**: the grid (the meter's rates), **quantise 0-100 %** (0 =
    exactly as shaped: each note's start pulled that share of the way to
    its nearest line), swing (the odd lines lean late, 0-50 % of a step),
    the picked notes' velocity; "arp notes" in or out when the take has
    them. Each note is drawn where the loop will play it, a dashed ghost
    where it was played.
  - **▶ hear** (`POST /take/keys/free/hear`): the take as shaped, placed as
    Make loop places it and rendered as a Loops row's ▶ renders a loop
    (`takes._render_loop`), looped from the next bar line of what plays —
    a transport layer, so it plays over the loop.
  - **Make loop** (`POST /take/keys/free/loop`): a **free loop** — the
    notes on the grid KEEPING THEIR TIMING (`loops.place_free`: each start
    where `loops.shaped_start` puts it, on its nearest step, the rest that
    step's `note_nudge`; the mean where several start on one), the shaped
    notes its `played`, the strength its `quantise`, the take's sound,
    the tempo as shaped, the key and meter, named from what it is; LOOP's
    `free: {swing, take}` (`take` the free take: its audio as played, never
    the loop's — the loop has none until a ▶ or → Song audio bounces it).
    Into the selected slot as a take lands (`slotrec.land`: new, or
    overdub | replace by the dock's choice). The free take is then the
    loop's: the list shows the loop, with ✂ Shape (another loop of the
    take; this one stays).
  - **Q** on a free loop places its `played` again keeping the timing
    (`place_free` at the rate and strength, its swing): 100% puts every
    note on its (swung) step and no timing is left.
- **Timing inside a step on the note grid** (`Arrangement.note_nudge`:
  step -> a share of a step, -0.5..0.5; `schema.ARR_OPTIONAL`, absent when
  unset). It sits on a note's START step, shared by every note starting
  there, as a velocity does (the roll's rule) — so the shaper's
  notes that start on one step take the mean of their offsets. It is read
  in `arrange._lane_timeline` (the ONE note schedule: the render, a
  section's chunks — none carry it yet — and the MIDI file):
  the start moves by it after swing, the END stays where it was (as a
  strum's), never under an eighth of a step; a note moved before the
  loop's first beat wraps to its end (the loop is a circle, as a drum hit's
  nudge). A nudged lane takes the timeline path (`_uses_timeline`); an arp
  ignores it (`arrange.nudge_of`). The chance lane (`note_chance`) is
  rolled first (`_chanced`): a note that plays then moves by its nudge. A layer slot holds its own (`seq`, its
  `note_nudge`; `song_bridge.lane_render` carries it on the layer dict only
  on its note steps), a follower the main lane's. The roll draws a nudged
  note's start cell off its step line (`.cell.nudged`, `--nudge`, its
  `title` says how far). Every tool keeps it with its note (a move or a
  shift carries it from the start step, a reverse mirrors it early for
  late, the ×2 copy copies it, a removed note drops it: `schema.clean_nudge`, the
  grid's own post too); pattern undo holds it; the launcher's windows and
  regrids carry it (`loop_window`, `regrid_lane`). No gesture writes one:
  the shaper and Q do.
- **A song's patterns keep no timing inside a step** (not built): → Song
  as a pattern plays a free loop on its steps; → Song as audio keeps the
  feel. The drawer's roll tools leave it out.

Routes (`routes/freerec.py`): `POST /take/keys/free`, `GET
/fragment/free/shaper`, `POST /take/keys/free/hear`, `POST
/take/keys/free/loop`. `tests/studio/test_free_rec.py`,
`test_render_cache_keys.py`; `tests/browser/test_free_rec.py`.

### What is not built

`snap` (starts are on the grid by construction); an arpeggio written
from selected chords (the keys arp plays one live: `keys-and-midi.md`,
"The keys arp"); a chord edge dragged on Perform's chord lane (its
tiles' 1-7, menu and split do the work; the Song page's edge is
songline.js's bar axis); a section opened in Perform bringing its chords
onto the loop's lane (it brings its notes; the generators still follow
the song's chords there while the loop has none of its own); a change of
steps that keeps time
(`regrid`: 4 -> 16 still keeps step numbers); the chopped sampler's
slices as rows; F6 / Shift+F6 between a page's regions (no island has
it); Stream Deck dials on the zoom or the cursor's velocity (a lane is
a `role=slider`, not a value control the deck's focus mode pages).

### The loop's meter

The "bar" picker by Perform's bpm (`fragments/meter_pick.html`, region
`meter-pick`; `consume`s its change, so the live strip's form does not
post the tempo again) posts `/fragment/meter`: every grid the loop holds
fitted bar by bar (`meterops`: the note grid, the beat, the other
layers, the beat's variations), one pattern undo step, refused past the
loop cap. The note grid's steps are the meter's (`meter.step_choices`;
`arrangement_from_form(meter=)` holds a post to them) and its cells are
marked as the drum grid's (`drums.md`, "The bar in other meters"). The
generators write on the meter's beats (`meterops.beat_fit`); busier /
sparser refuse a grid that is not 16 steps of 4/4. The click counts the
meter (`click.js`).

### Generators on the loop (`gen/loop.py`)

`gen/notes.py`'s bass, rolling bass, chords, pad, arp and lead on
Perform's roll: `POST /fragment/gen` with `lane=notes`
(`routes/pattern.gen_notes`; the route is the beat's, `drums.md`), from
the tool bar's "⚄ write" menu, ⚄ (again), busier, sparser. Pure half:
`gen/loop.py`; `tests/studio/test_gen_loop.py`.

- **Where it lands**: in the octaves the roll shows. Every kind fits two
  octaves from a root of the key, so the lane is moved by whole octaves
  until that root is the window's bottom row; from the six-row view the
  route opens the two-octave one (`ROWS_NEEDED`). The window itself is
  not moved — a "bass" written with the window high is not a bass.
- **What it replaces**: what the grid shows; what it hides (other rows,
  steps past the end) is kept, the rule of every pattern tool. On a grid
  that is not 16 steps a bar the lane is moved as a song's layer is
  (`song_model._regrid`).
- **The harmony**: the loop's own chord track ("The chord lane" below)
  when it has one; else the song's when the loop is in the song's key
  (root and scale); else one of `chordtrack.PRESETS` picked by the seed,
  a chord a bar (a loop shorter than the progression plays its opening).
  The toast says which ("over the loop's chords"). In a key that is not
  seven-tone the chords are its diatonic parent's (`songcraft.md`, "The
  chord track"), so a part may hold a tone the key's rows lack (A
  pentatonic minor's VI plays its F): it lands on any semitone between
  the window's rows (`gen/loop._between`), and the roll's window then
  opens the chromatic rows (`show_notes`) — none is left out.
- **Undo**: a roll again on the grid the same generator left joins that
  roll's pattern undo step (`StudioState.gen_again(key, lane)` /
  `gen_done`, shared with the beat's fills: one memo, the last
  generator's); busier and sparser are a step each, and need a 16-step
  grid. A follower lane and a lane with its arp on refuse with a reason.
- **It plays**: the answer's `preview-ready` carries `autoplay`
  (`routing.autoplay`).

### The chord lane (the loop's chord track)

**Perform's chord lane** (`POST /fragment/chords/track`): the working
loop's chord track,
`Arrangement.chords` — `schema.CHORDS`, degrees of the loop's key, `at` /
`len` in beats, `bars` the length that repeats (`songcraft.md`, "The chord
track"). **It renders nothing**: `RenderInputs.key` drops it, and it is
in `schema.ARR_OPTIONAL` (absent when unset: no saved state, loop key,
stem key or baseline gains a key). It is a pattern undo step
(`PATTERN_FIELDS`: Ctrl+Z on Perform), in the "Sequence" reset scope,
kept by every grid post (`_merge_arrangement`, its own lane tag
`chords`).

- **The lane**: a row of the roll under the bar ruler (grids.html
  `chord_lane`, Perform only — the drawer's chords are the Song page's
  tiles), a tile a chord on the steps it holds (`gen/loop.lane`: the
  progression tiled from bar 1 by `chordtrack.resolve`, a pass after the
  first dimmed). A tile reads its numeral and its chord's name ("III
  C"); **outside a seven-tone key a chord is the diatonic parent's**
  (`chordtrack.chord_scale`: major when the key has a major
  third, else natural minor) — its degree counts and its chord stacks
  in the parent, so a progression written for seven tones plays as
  written: (1, 6, 3, 7) in A pentatonic minor is i VI III VII, Am F C G
  (not "I I IV III", the 6th and 7th degrees wrapped round the five
  tones). The pads and the code line number by the parent too
  (`chordtrack.numeral`, a ♭ for a root outside it: `chords.scale_chords`,
  "iii" plays C there), though the static pads stay the key's own
  chords; "follow the loop's chords" plays the track's (F included) and
  lights the pad on its root, none when the key has no pad there
  (`loop_track`). The Song
  page's tiles are the model and the class
  (`.ctile`): a click or Enter opens its menu (`#loop-chord-menu`:
  degree, quality, inversion, split, remove — songline.js sends the
  `contextmenu` menu.js opens on), ← → / Home / End move between them
  (one tab stop), 1-7 set the degree, Delete removes, each posting the
  tile's `data-post`. The row's label ("chords") opens the progression's
  tools (`#loop-chord-tools`: ⚄ another, the key's progressions,
  + / − a chord, over 1-16 bars, clear). With none, one button rolls a
  progression in. Buttons only (the menu's two fields name no form):
  nothing posts with the grid's own form. In the Tab order between the
  roll's tools and its grid: the label, then the tiles (one stop) —
  `test_input_matrix.py`'s keyboard journey Tabs through both.
- **The route**: `POST /fragment/chords/track` (routes/pattern.py,
  `@pattern_edit`): `op` one of the Song page's chord edits
  (`songops.edit_chords`, shared with `POST /song/chords`, its fields
  read by `song_start.chord_args`) on the loop's track — one bar of the
  key's first chord when it has none — or `clear`. A roll, a preset and a
  clear say the progression in a toast.
- **What follows it**: the roll's chord tones (`.ct` / `.cr`) and the
  stamp's "chord track" choice (`chordtrack.voiced`, the drawer's rule),
  at once; the generators on the loop ("⚄ write", `gen/loop.harmony`) from
  their next roll; and, an option off by default, the keys: "follow the
  loop's chords" in Play tools' chords in key (`#chords-follow`,
  chords.js; per browser `synthlab.chordsFollow`) — with the loop
  playing, a key in chord mode plays the chord under the playhead (its
  root nearest the key) and that degree's pad is lit, from the chord
  bar's `data-track` (the region `chord-bar` re-sends on a chord edit).
  The notes already on the grid stay as they are.
- **In another meter** (`meter.py`, the loop's "bar"): a chord track's
  beats are quarters of the loop's bar (`chordtrack.resolve` reads
  `steps_per_bar / 4` a beat), so a chord a bar is a tile a bar of 3/4 or
  7/8 alike — as the generators fit their 4/4 lanes bar to bar
  (`meterops.beat_fit`). The follow counts the loop's bar by its meter
  (`S.barBeats`).

`tests/studio/test_loop_chords.py` (the route, the field's absence, the
render keys, undo, the generators), `tests/browser/test_loop_chords.py`
(a lane edit changes what "⚄ write" writes; the tile's menu and keys;
the keys following). Nobody has listened to a part written over the
loop's chords.

## Step-cell pointer capture

**Step-cell pointer capture**: drag-paint captures the pointer
only once the stroke leaves the origin cell. Capturing on pointerdown
would retarget a plain mouse click's `click` to the `.grid`, so the
checkbox would never toggle (touch taps and Space still would, which
hides the bug).
`test_island.py` pins that the pointerdown handler doesn't capture.

## The clip lane has no placer on Perform

**The clip lane has no placer on Perform**: `Arrangement.clips`
still renders (a section opened in Perform brings its takes), but clips
are placed as takes on the Song page's audio tracks, from the clip
library drawer's "Place" (`sampler-and-clips.md`, "The clip library";
`song.md`, "Recording"). That drawer is on every page.
