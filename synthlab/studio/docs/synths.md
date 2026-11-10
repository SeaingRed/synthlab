# Synths: the bench, chords, Bass check

The Synths page (`/view/editor`) and the other bench views: how the
page is laid out, the reference phrase bench views render, A/B
compare, vs beat, macros, the chord bar / chord mode and Bass check.
Browse / audition / Use is in `sounds-and-breeding.md`; the layer strip
in `lanes-and-layers.md`. Index: `README.md`.

## Synths is a bench, Perform is the sequencer

**Synths is a bench, Perform is the sequencer.** The note grid, the
velocity/slide lanes live in Perform (`fragments/transport.html`); the
song is arranged on the Song page. Synths keeps only what
shapes a *sound*, laid out by job (grid areas
`head / dock strip chain panels fx perform bass keys`, both breakpoints):
the sound strip (`fragments/sound_bar.html`, `id="sound-bar"`, a
`display: contents` form — play, `sound_id.html` name/"edited"/layer,
undo, compare A/B + morph, bounce, the one Save; then the rail —
phrase, beat, key — with `#analysis` at its end; the layer strip below
or beside — see "The sound strip's layout" below and "Layer strip" in `lanes-and-layers.md`),
the signal-chain bar (`chain.html`,
`studio/chain.py`: one stage per panel in engine order + the active
modulators), the panels in signal order (`paramui.PARAM_GROUPS`: Source,
Filter, Vowel, Amp envelope, Character, Motion — full-word labels unique
across groups, `hint` tooltips, `SUBHEADS` row headings, mod slots drawn
as one `.route` line each; the FM4 operator panel, wavetable
picker and sync block are drawn rows of the same fields:
`sounds-and-breeding.md`, "Oscillator panels"), FX, a folded "Perform controls" (macros + XY),
Bass check, and last the sticky `.keys-dock` (piano, Monitor — `space`
and `volume`, which are **not** part of the patch and say so — and the
chord bar with its Play tools drawer). `#sound-id`, `#signal-chain` and
the three panel drawings are regions (`views-and-regions.md`): an edit that moves them
brings them back OOB, rendered from lean contexts (`sound_context()`,
`chain_context()`), never a `page_context`.
The Sounds dock is a `popover`: a sticky column on desktop, a bottom
sheet under 900px. **Its head stays put** (`.sounds-head`, sticky in the
scrolling dock: the title, ☰, ‹ and the filter `#dock-filter`, which
sits outside the refreshed `#preset-list`, so a refresh keeps what is
typed). **‹ folds it** (`.cockpit.dock-collapsed`, per browser:
`synthlab.dock`): the column goes to 0 at every width — the wide
layouts' `#view` columns read `--dock-w`, which the folded cockpit pins
to 0 over the splitter's inline value — and the way back is **Sounds ›**
(`.view-head > .dock-expand`) at the top, left of the page's title,
where it is found at once, and the folded column keeps no width at any
size. **It moves** like any
module of the page (`synths.sounds`, taken by its title row —
`frontend.md`, "Workspace: modules you can move"): under the FX racks,
beside the panels, tabbed with Bass check; Reset puts it back at the
left. Moved, ‹ folds it where it stands (a column of its own goes; under
or tabbed with others it keeps its head, ‹ turned ›) and Sounds › opens
it in place; its head and filter stay put over its own scroll wherever
it is, and under 900 px it is the bottom sheet. A row loads its sound on any click, its tags
included — a tag never fills the filter, which would hide the list on a
click meant to load; a pointer's click gives the focus back to the page (audio.js),
and a button the mouse focused never blocks the typing keys (piano.js
`keysBlocked` blocks only a `:focus-visible` one), so the new sound plays
from the keys at once. **Load says what it replaces**: while
the layer strip holds more than one sound (`#layer-strip[data-stack]`)
or the card brings layers of its own (`data-layered`), a row's name asks
first, in the sound menu's popover under the row (`#sound-pick`,
soundmenu.js, no list) — replace this layer / replace the whole sound /
＋ add as a layer — and posts its own load route with `as`
(`StudioState.load_sound_as`; `lanes-and-layers.md`, "Layer strip").
The default is focused: Enter loads as a load always did (the whole
sound for a card with layers, this layer for any other). One sound and a
plain card ask nothing. **☰** (`#sounds-compact`, per browser,
`html[data-sounds-compact]`) is one line a sound, the tags hidden, in
either density; until it is pressed it starts on under a finger (a
two-line row is 80 px there). **The filter says when nothing matches**: each shelf
(`.dock-shelf`) counts what it shows ("2 of 18") and goes with its
heading when it shows none, and `#dock-none` names the query, Esc, and
the Library (audio.js `applyDockFilter`). Save sound… saves the whole sound by default —
"include layers, FX & curves" starts ticked: every layer, its FX and
curves, the Recipes' format 2 (unticked: the edited layer's patch) — and
opens under its button (`#save-dialog.dialog`, `position-area`), its
name field 24ch wide and holding the sound's name, selected (audio.js,
on open: Enter keeps it, typing replaces it), Save the dialog's primary;
a save toasts where it went and lights the new row in
Yours (`.just-saved`, scrolled to once). Automation curves open in the row's own `.curve-slot`
(`hx-target="next .curve-slot"`; Done hits `GET /fragment/curve-close`). Its ▶ renders a fixed reference phrase
(`studio/audition.py:PHRASES`, chosen by the `phrase` radio, persisted as
`state.audition`) instead of the arrangement, so swapping patches never
swaps what you hear them play — `StudioService.render_inputs(view)`
(`service/loop.py`) makes that substitution and nothing else in the render
path changes.

### The sound strip's layout

(The aim: no ragged rows — no Resample / → Song or Agent / Save wrapping
onto lines of their own, no Phrase / Beat / Key in three rows of
different lengths, no read-outs floating where they land.) The strip
(`views/editor.html` `.sound-strip` › `.ss-grid` › `.ss-sound` + the
layer strip; style.css 7a2) is three parts, each wrapping **whole**:

- **Identity**: ▶ · the name (`--fs-l`) over one line of facts — "Layer 1
  of 4 ▶ the phrase · hold · 1 bar" (`.sb-title`: `#sound-id` drawn as
  contents, so its `.sid-meta` and ▶'s `.play-cue` share the line) · the
  actions in four `.sb-group`s — edit (Undo) · compare (A/B, ⇄, Morph) ·
  take it further (Bounce, Resample loop, → Song) · the outcome (✦ Agent,
  Save sound…) — in two `.sb-half`s: one line, right-aligned, or, when the
  line is short, the halves one over the other beside the name, never
  three and one. The empty morph slot is drawn unlit ("Morph" over an
  empty groove) — the place reads as the control to come, not a hole.
- **The rail**: Phrase · Beat · Key, each a `.tcluster` cell (its label one
  width, `4.5em`, so the controls of a line's first cell stand in line),
  the read-outs a **meter window** at its end (`#analysis`: a well, each a
  value over its unit — no markup change, `analysis.html` is Bass's too).
  The form and `.sb-listen` are `display: contents`, so the cells and the
  read-outs (outside the OOB-swapped form) are one flow: the read-outs
  close the rail's last line (`margin-left: auto`). Beat's cue is cut at
  18ch (the select names the beat; ▶ beat's title is the whole cue).
- **The layers**: under the sound, or **beside it from a 1500 px strip**
  (a container query on `.sound-strip`, `strip`: the sound list open or
  folded decides it as much as the window — a window media query at
  1600 would squeeze the sound to ~700 px with the list open). The
  sound keeps ~950 px there (the rail in two lines), the layers 560+.

A hairline stands between two groups (two cells) on a line, never at a
line's start or end: each rule sits half a gap outside its group and the
row clips (`overflow-x: clip`, a 4 px clip margin for the focus ring).
Laid out by the workspace, the form is a flex box of the same flow
(style.css 38). Measured (comfy, list open / folded, strip height px,
ragged rows → this layout): 1280 253 / 223 → 244 / 231; 1600 201 / 171 → 231 / 139;
1920 171 / 141 → 139 / 126; 2560 141 / 111 → 126 / 110; dense 1280 232 /
198 → 217 / 199, 1600 195 / 161 → 199 / 129, 1920 161 / 131 → 112 / 111,
2560 97 / 97 → 93 / 93. The taller cases are a stacked strip whose rail
fits on one line but for the read-outs, which take a line of their own at
the rail's end (comfy 1360 px +30, 1280 folded +8; dense 1310 +4).

### The bench tiles the window

On a desktop window (> 900 px) Synths, Bass and Voice are one raised
surface cut by 1 px seams: no outer margin, no cards
(`style.css` section 17; selectors are `#view[data-view=…]` so the layout
holds in both densities, `dense.css` D17 only tightens sizes).
`tests/browser/test_bench.py` measures it: under 5 % bare `--bg` at 1440,
1920 and 2560, both themes. A wider window shows more modules, never
wider margins:

| width | Synths |
|---|---|
| 901+ | to 1200 px, the panels flow down balanced columns (`#patch`, CSS columns; the `.pcol` wrappers are `display: contents`) |
| 1200+ | the panels stand in **three `.pcol` columns** (to 1600, a grid: Source with Motion and Modulators under it \| Filter, Vowel \| Amp envelope, Character, Mod matrix), so Source, Filter and Amp head the first screen of a laptop window — the balanced columns put Filter under Source, below the fold, and headed the second with Vowel; Perform (macros, XY) and Bass check side by side; both are `<details class="rack">` whose content is always shown here (`::details-content`), so nothing a user tweaks is behind a disclosure — closed and foldable below 1200 |
| 1600+ | **four `.pcol` columns**, set in `views/editor.html`'s `layout`: Source \| Filter, Vowel \| Amp envelope, Character, Mod matrix \| Motion, Modulators (a group `layout` does not name joins the last) |
| 2200+ | six across: the FX pedalboard and the Perform / Bass modules stand beside the panels — the whole instrument on one screen |

- **A row is a slider or a knob cell** (`fragments/param_row.html`,
  `dials`): the parameters that come in runs (an oscillator's level /
  octave / detune, the mixer, Character, each modulator's rate / depth /
  delay, a route's amount) are `.param-knob` cells, five to a line; the
  filter, the envelopes and the vowel stay sliders. Same
  `input[type=range]` either way. A group's heading (`SUBHEADS`) stands
  before its first row as `h4#sub-<name>` (the generated shape rule hides
  it with its rows); an OOB row never carries one.
- **Idle controls** (`paramui.idle`): a control the engine
  cannot hear under the patch's other settings — the breeder's own
  `params.live`, never a second copy — is drawn faint (`data-idle`) with
  why after its hint (`IDLE_WHY`: "needs 2 or more unison voices",
  "vibrato depth is 0"…); glide on a phrase with no slides (only `riff`
  has them: the loop glides into a slide, the keys note to note). The
  page draws it; after an edit `do_render` names the idle rows in its
  `patch-idle` trigger and audio.js moves the marks. Still usable.
  (Measured: unison detune and spread change nothing with one voice and
  glide nothing on hold / arp / stab.)
- **The mod matrix is its own module** (`#panel-mod-matrix`: Motion's
  `modN_*` fields — 36 for its twelve slots, `paramui.ROUTE_FIELDS`,
  the Jinja global `route_fields` — same form). Its `#patchbay` is a *view* of the
  `.route` rows — source jacks, target jacks, one lead a route drawn
  by `static/bench.js` into `.pb-cables` (redrawn on a field's `input` /
  `change`, after a swap and on resize; never per frame). A drag from
  jack to jack sets the same fields (the first free slot of twelve); each
  row has ± (invert) and ✕ (source off). `aria-hidden`: the rows are the
  controls. **Twelve slots, dense**: routes 1-4 always stand
  (the ones the breeder and the mixes write); 5-12 (`data-extra`) stand
  only while they route, or once **+ route** opens the next (bench.js:
  `data-open`, kept until the next swap). Twelve source jacks; a target
  jack whose module can't be heard is hidden unless a lead is in it
  (editor.html's generated rules: filter 2's while it is off, cross FM
  with osc 2 off, sync pitch with sync off, the wavetable position / fm
  index / fm4 levels without their shape), so a plain saw's bay stays
  short; bench.js marks every plugged jack before it measures
  one. The other sources' leads: LFOs 2 / 3 amber as LFO 1, envelopes 3 / 4
  pale amber, the wheels the signal colour, key and random grey.
- **Filter 2** (`editor.html`, a `.f2-block` well under the
  filter envelope): its route as pills (off / serial / parallel — an id'd
  radio group, so the deck, MIDI learn and the menu step it), its type a
  select of the thirteen, cutoff / resonance / key track / env amount as
  knobs; the rows hide while it is off. Filter 1's type is a select
  too (thirteen types read as "Ladder 24", "Low 12", "Comb +"…,
  `paramui.FILTER_LABELS`). The filter drawing
  (`soundmeta.filter_response_svg`, region `filter-viz`, whose watch
  reads filter 2 while it plays) draws every type and filter 2 —
  serial the product, parallel the mean — at 160 points (a comb's
  teeth); the chain bar says "Low 12 · 500 Hz → High 12 · 900 Hz" (‖ for
  parallel).
- **The Modulators panel** (`paramui` group "Modulators", the
  last column under Motion): envelopes 3 and 4 (five knobs each: attack,
  decay, sustain, release — log knobs, 1 ms to 8 s — curve) and LFOs 2
  and 3 (rate and phase knobs, shape and sync selects, retrig / free
  pills). **A drawn LFO** (LFO 1 in Motion, 2 and 3 here):
  `fragments/lfo_draw.html` under the shape row, shown while the shape is
  "drawn" — a well with the cycle drawn (`oscfaces.draw_face`: the
  polyline the engine reads and a handle a point) over a hidden
  `<lfo>_draw` field the form posts (`forms.DRAWN`, cleaned by
  `core.clean_draw`). panelfaces.js edits it: press adds a point (or takes
  the one under the pointer) and drags it, double-click removes one, six
  starting shapes (triangle, ramps, steps, gate, swell) start over; each
  write is one `input`, so one post and one undo step. The mod wheel and
  aftertouch are two knobs under Motion's "Wheels" heading.
  **Mod mixes** (`studio/modmixes.py`, `POST /fragment/mod/mix`): the
  matrix in one move — the four routes and the assignable LFO (shape,
  sync, rate) — as one undo step whose toast says what it routed. The
  legend's **⚄ mix** draws a random one (`random_mix`: two to four routes
  from `POOL`, pairs that move a sound musically, no target twice, an LFO
  to match); its ⋯ holds the named `MIXES` (Wobble, Pluck sweep,
  Breathing, Velocity bite, PWM shimmer, Siren, Gate stutter, Analog
  drift, Vibrato swell, Growl — each `title` says what you will hear),
  ⚄ Random mix and ✕ Unplug all. A mix names every slot (one it does not
  use is unplugged), so mixes never stack. A ⋯ row closes its menu
  (audio.js; the body morph would keep it open).
- **Cables**: layer -> FX / master down the layer strip's right edge (one
  server-drawn `<svg preserveAspectRatio="none">` over the rows: a row is
  10 units whatever its height; `.off` = ▶ does not hear it); unit to
  unit across each FX scope (`fx_rack.html`: a pedalboard of `.fx-unit`
  modules whose parameters are `.knob`s with `data-default` — a choice,
  `FX_UNITS[kind]["choices"]` (the drive's mode), is a `<select>` in a
  `.fx-param.fx-choice` posting its index; each module's **★** opens its
  presets (`fxrack.FX_PRESETS`: a few named settings a kind — Reverb's
  Room / Hall / Cathedral / Ambient wash, Delay's Slapback / Eighth /
  Dotted eighth / Dub… — `unit_preset` is just the preset's parameters,
  `{}` for ↺ Defaults, and the recall's `clean_chain` fills every other
  one with its default) and ↺ Defaults, one set post (`name=recall`,
  `fxrack.edit_chain`); each row is a
  `data-menu`, so the module's right click lists them too; the leads between modules are
  drawn by `static/fxcables.js` from where the modules are laid out — a
  sleeve, a core and a sheen, a plug each end, a socket each side, beside
  or round and along the gap to the row below; the inline `lead()` keeps
  the room and is hidden once wired, `tests/browser/test_fx_cables.py`). Only the
  chain bar's leads glow with the level; these are static.
- **One rack everywhere**: every FX rack's units are drawn
  by one macro, `templates/fragments/fx_units.html` `units(rows, r)` —
  the jacks and leads, each unit's header (label, ★ presets, ↺ Defaults,
  ‹ › ✕), its knobs, choice menus, pattern field and step editor (16 /
  32, a gate's rhythms) — from `fxrack.rack_rows`, and every rack's edit
  is `fxrack.edit_chain` (`_set_unit`: a parameter, the pattern, a step,
  the length, a rhythm, a recall). Three racks use it: this page's (and
  Drums', Mix's, Voice's: `fx_rack.html`, a route an op in
  `routes/fx.py`, the scope in `hx-vals`), a Song audio track's (the
  drawer, `POST /song/track/{id}/fx`) and the vocal booth's (`POST
  /voice/booth/fx`). The macro's `r` names a rack's url (one taking `op`,
  or one an op), its `hx-vals`, the swap of a value's post and of a move
  / remove, the popovers' id part, the commands' title and group, the
  out jack's label, and whether ‹ › are disabled at the ends (not here:
  the button pressed at an end keeps its `autofocus`). So fxcables.js
  wires all three. No rack has a per-unit bypass (it would be a new
  persisted field of a unit, in every render key and the live FX).
- **Undo of a rack edit**: every add / chain / move /
  remove / set on a session rack (`routes/fx.py`: the layer, master and
  drum-bus chains) is a step of the patch ring (`state.push_fx_undo`: the
  scope's chain before the edit, in place of a patch), so "Undo the last
  tweak" (Ctrl+Z here, the reset menu's ↩ on every page that has one)
  takes it back as it takes back a knob of the sound. A knob's drag is
  one step (posts with one key inside `UNDO_COALESCE_S`); a press (a
  preset, a step, the length, a rhythm: `fxrack.RACK_PRESSES`) is a step
  of its own, and a patch tweak never joins an FX step. The ring is the
  edited slot's, so a master or drum-bus step pushed on layer 1 is undone
  from layer 1; a step is skipped outside the track edit it was taken in
  (`song_edit_track`: a drum track's kit edit swaps the drum bus without
  swapping the ring). **Not covered**: Ctrl+Z on the Mix view is the
  song's undo, which never reaches the master chain (the session's, not
  the song's; Synths' ↶ Undo does); Ctrl+Z on Drums is the pattern undo
  (the reset menu's ↩ takes a drum-bus edit back); the vocal booth's rack
  has no undo (`voice_lab.json`, outside every ring). A Song audio
  track's rack is undoable too, one song edit each (`song.md`).
- **No per-layer meter**: the loop is one mixed buffer and the live
  voice's `load` reports are CPU, not level — a meter per lane needs an
  analyser per lane. Not built.
- `bench.js` also: the `.player`, the docked agent drawer (`#agent-pin`).
  (A read-out you can type into is every view's — tweaktype.js — and
  the context menus are menu.js's: a layer, an FX unit, a route, a sound
  and a slice declare `data-ctx` and their buttons `data-menu`.)

## Bench views all render the phrase

**Bench views all render the phrase** (`views.BENCH_VIEWS` =
editor/library/breed/map/bass): they judge a *sound*, so they hear it
against a constant. Perform and Drums ARE the arrangement and keep it.
Rendering `s.arrangement` synth-only on a browsing view would be silence
whenever the Perform grid holds no synth notes — ▶ on a Library sound
would play it and Use would give nothing. Bass Lab's `bass`
view moves the phrase to its quick key's root (`state.bass_root`) inside
`render_inputs`, so its ▶, the quick key's preview and its panels agree.

**The sound bar's identity row never reflows** (A/B's Morph, the → Song
note and the beat picker must not push ✦ Agent and Save sound… onto a
second row): the morph keeps its slot while there is
nothing to morph toward (`.morph-slot`), → Song answers in a popover
under its button (`to_song_note.html` in `.to-song-note.pop`, opened by
audio.js when the answer lands; Perform's → Song the same), and Sounds,
✦ Agent and Save sound… are one `.sb-end` group, last in the row's
second half ("The sound strip's layout" above).
**⏺ Bounce says where it went**: a done toast, "Bounced “x” (1 bar,
1.9 s) to Clips." (`kept_as`'s `said`), named in the name popover
(`frontend.md`, "The stack"), the sound's name its default. **⤷ Resample
loop says what it replaced**: it stays a replace of the edited layer's
sound (a sampler of the Perform loop layered under the sound it was
taken from plays the loop at every note of the phrase), and its done
toast names the layer, what it took and its length, Source's chop and
the way back ("Ctrl+Z gives “Acid Alley” back"). **"edited" follows
undo across a load**: a patch undo step keeps the "sound" baseline of
its moment (`state/history.py` `_SOUND_BASE`), and Ctrl+Z that lands on
exactly that baseline's patch restores it — save, Resample, Ctrl+Z is
the saved sound, not "edited" (a later save stays the baseline for any
other step). **A drag is one step, another control is another**:
a `/fragment/patch` post's step remembers the fields it
moved (`_MOVED`), and only a post moving the same ones joins it inside
`UNDO_COALESCE_S` — otherwise an unplug 0.4 s after a patch-bay drag
would fold into the drag's step (whose patch is already the unplugged
one), and Ctrl+Z would move nothing; a post that moves no field of the patch (the sound
bar's tempo) is no step at all. The other pushes (macros, the XY pad,
the panels) coalesce by time alone. **The mod matrix's amounts read in percent**, as the patch
bay's cables print them (`param_row.html`: `data-pct`), the source
column as wide as "Filter envelope"; ⚄ mix's toast says the routes in
the matrix's words ("LFO 1 → Noise level +29%", `modmixes.random_mix`).

## A/B compare

**A/B compare** (`routes/patch.py` `ab`, `fragments/ab_slots.html`):
the live patch is one slot (`state.ab_live`, session-only) or neither.
Only that slot is lit (`aria-pressed`); holding a sound is the ● dot,
never the lit look — lighting every filled slot made A and B both look
on. A filled slot: the edits go into the live one, then it plays. An
empty slot branches (stores the sound as it is; the live slot keeps its
own). ⇄ and the morph go to the other slot; any load clears `ab_live`.

## vs beat

**vs beat** (`state.vs_beat`, sound-bar checkbox): bench views render
the reference phrase over the current drum lanes —
`phrase_arrangement(..., keep_drums=True)` + `_view_parts` appending
`"drums"`; ▶/Use agreement holds because both go through the same path.
The phrase is one bar, so over the beat the mix loops the beat's bars
(`loop_bars`: lcm(1, drum_bars)): ▶'s cue says so — "the phrase · Riff ·
2 bars + the beat" (transport.js `playsOf` reads the checkbox's
`data-bars`, never "1 bar" over a 2-bar loop).

### Beat ▾: pick the beat, play over it

**The beat is the loop's beat.** Synths picks which beat loop to play
so the synths can be played over the top. There is no second beat for Synths: **Beat ▾** (`beatpick.py`, a
`.tcluster` beside the phrase in `sound_bar.html`) sets the working
loop's drum lane — the one Perform's rack and the Drums page show, and
its `title` says so. Its choices, each the move another page already
makes, through the same state methods:

| choice | does | the same as |
|---|---|---|
| a variation (A–D, those with hits) | `beat_variation(letter)` | `POST /fragment/drums/var` |
| a bank slot | `bank_load(slot)` | `POST /fragment/drums/bank/load/{slot}` |
| a new beat (every `beatgen.STYLES`, grouped "new: house & techno"… by `beatgen.STYLE_FAMILIES`, each by `style_name`: "Filter house break"; Drums' style select the same) | `beatpick.roll_style` (cells + `dress` + the style's swing) | Drums' Randomise (`POST /fragment/drums/randomise` calls it) |
| none | "over the beat" off; the beat stays | the checkbox |

and a **kit** select beside it: `beatpick.swap_kit_under` — Drums' "Swap
kit, keep beat" (`POST /fragment/drums/kit` `keep=pattern` calls it).
Any pick but none turns "over the beat" on.

- **Each select posts itself** (`POST /fragment/beat/pick`,
  `routes/patch.py`, over `beatpick.apply` / `swap_kit_under`) with
  `hx-trigger="input consume"`: the select's own handler stops its
  `input` before it reaches the sound bar's form, whose post (and its
  `vs_beat`) would otherwise fire too and undo the pick; `hx-vals` blanks
  the other select, so a post carries one pick. Each select's first
  option is `""` — the beat as it is ("Filter House · B · 2 bars"; the
  kit, else "kit"), a post of it moves nothing — and the bar comes back
  by its region's watch (`beatpick.watch`) with `""` chosen again.
  `/fragment/arrangement` reads no pick.
- **A "new:" value carries its seed** (`style:<name>:<seed>`, drawn from
  `beat_seed` when the menu is drawn): the bar's form queues its posts, and
  a pick posted twice writes the same beat; the same state draws the same
  menu (the sound bar's watch holds `beatpick.watch`).
- A pick is one pattern undo step (`@pattern_edit`: Ctrl+Z on Perform /
  Drums); a beat too long for the tempo is refused before it is stored
  (`loop_refusal`) and nothing changes.
- **▶ beat** (`#play-beat`, `data-player="beat"`): the beat alone, looped
  at the loop's tempo, to play the keys over just the drums. `GET
  /beat.wav` renders the Drums view's ▶ (`svc.render("drums")`: the drum
  lane, its kit, the drum bus and master FX, over its own bars) and
  redirects to its `/preview/{key}.wav`. The sound bar draws that key on
  `#play-beat` (`data-beat-key`, `svc.beat_key()`: a key, no render — the
  bar's watch holds it), and audio.js (a second looped player beside the
  loop) asks `/beat.wav` again only when the key or the page's tempo
  moved (a bpm typed into the bar is a post that never redraws it): a
  beat picked while it plays swaps in on its own wrap, and a sound edit
  asks nothing (fetching `/beat.wav` on every `preview-ready` would cost a
  locked key hash each). It claims `"beat"` on
  the transport (kind "Beat", label "the beat · Filter House · 2 bars",
  `beatpick.label`): ▶ stops it and it stops ▶. Its cue is the
  `output[data-cue-for="play-beat"]` beside it.
- **beat only** (the beat and no phrase): the Phrase pills' last, `audition.NO_PHRASE` (`"none"`,
  in `audition.CHOICES` and the schema's `audition` choice, not in
  `PHRASES`). Under it `render_inputs("editor")` — no `patch`,
  `arrangement` or `parts`, not a song track's edit — returns
  `render_inputs("drums")`: Synths' ▶ and Space play ▶ beat's very render
  (one cache entry), the cue is `beatpick.label` (transport.js `playsOf`
  reads `#play-beat`'s `data-plays`). Disabled while the loop has no
  beat; a sound's audition and the other bench views fall back to
  `DEFAULT`; Beat ▾'s none (the phrase on its own) puts `DEFAULT` back.
- Not on a song track's edit (the Hear cluster replaces the phrase).

## Macros

**Macros** (`state.macros`, `/fragment/macro/{i}`): four knobs, each
driving up to three params — the XY pad generalised. Deliberately on
`StudioState`, not `Patch`: keeping them out of the genome leaves
breeding, serialization and every render path untouched. A move replies
with **OOB `param_row.html` fragments for the moved sliders only**;
swapping the body would rebuild ~2300 nodes per drag frame. A macro
targets `paramui.MACRO_PARAMS`: the continuous params less the
performance controls (`PERFORMANCE_PARAMS`, i.e. `pitch_bend` — the set
the XY pad refuses to write, `routes/macros.PERFORMANCE_AXES`). The menu
does not offer one, an assignment and the agent's `set_macro` drop it,
`apply_macro` skips it and the schema drops a saved one on load.
`bend_range` is the sound's own setting and stays a target.

## Chord bar

The chord bar (`#chord-bar`) is the keyboard dock's Play tools — the
note, chord and harmonic pads, the chord chart and the code line — and a
region watching root + scale: a key change re-sends it (morphed; its open
pad blocks survive, the chord chart refetches for the new key). What a
key plays, the strum and the typing rows are the dock's toolbar, outside
it; the key itself is in the top status strip (the bar does not say
"chord mode · A1 minor").

## Chord mode and Bass check

Chord mode (`studio/chords.py`, `static/chords.js`): the Synths
keyboard's "each key plays" (`#kbd-plays`, the dock's Play segments: a note, or a voicing —
`keys-and-midi.md`, "The keyboard dock"); anything but a note is chord
mode, which builds the in-scale chord on the nearest
scale tone (`chord_for_midi`, mirrored client-side as `chordAt`) and plays
it through the same `window.synthlab.piano` bridge — so QWERTY, pointer
and MIDI all inherit it. `chords.js` intercepts in **window capture**
(`stopImmediatePropagation`) so piano.js never sees the single note.
Chord pads (I–VII, keys 1–7) and the code line (`C4`, `Am7`, `vi`)
come from `chord_sets` in `page_context`; **note pads** (`note_pads`,
`chords.scale_notes`: every tone of the key over octaves 3–4) sit above
them and follow "each key plays" (harmonics never become chords). Bass Lab is folded into the
Synths page as the "Bass check" `<details>` (route `/view/bass` kept: the
page is "Bass check" too, linked from the details as "Low roots ▸"; it has
the bench's ▶ — Space plays the phrase at the root in force, the quick key
pressed (`aria-pressed`) — because its keys move a render, `"bass"`'s,
that Synths' ▶ and meters never play; the sub spectrum is THE signal
graphic, `svg.sig`, and says when the sub holds nothing to draw).
**Bass Lab's quick keys never touch the grid**: they set `state.bass_root`
(schema'd, persisted), which only `render_inputs("bass")` reads — the
bench render (full chain) with the phrase moved down to that root. The panels, phone audition and quick-key preview all
render it; `bass_panels.html`'s root carries its own `hx-get`/`hx-trigger`
so it keeps refreshing after the placeholder is swapped away. On Synths
the Bass check and the sub spectrum are two modules
(`synths.bass`: the mono and phone cards, Low roots ▸; `synths.sub`,
`#bass-sub`) in the one `<details>`, which is only their place: asked from
Synths, `/fragment/bass` draws the two cards and sends `#bass-sub` out of
band (fragments/bass_sub.html, the plot fragments/sub_plot.html), so the
sub spectrum lands wherever it was moved. Macros and the XY pad are two
modules the same way (`synths.macros`, `synths.xy`), and the two FX racks
(`fx.layer`, `fx.master`) — `frontend.md`, "Workspace: modules you can
move".
