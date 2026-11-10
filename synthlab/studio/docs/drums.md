# Drums

The drum machine's clock and voices, **the per-hit model and its one
schedule**, the step sequencer (the grid on Drums, in Perform's rack and
in the Song drawer), variations, fills and the other beat generators,
the pattern bank and its save rules, the Kit page (the drum page: kits
here, beats in the Loops panel), mini-notation, the
generator's bar rule and the row heads. Genre kits and beat styles are in
`sounds-and-breeding.md`; the pattern tools in `loop-and-perform.md`; a
song's generators in `songcraft.md`; which island owns which gesture in
`keys-and-midi.md`. Index: `README.md`.

## Drum machine

**Drum machine** (`arrange._schedule_drums`): 8 voices (`DRUM_ROWS`:
kick snare hat ohat clap tom rim crash; notation `bd sn hh oh cp tm rm
cr`). It has its **own clock** — `Arrangement.drum_bars/drum_steps/
drum_swing/drum_humanize` — separate from the synth grid's
`bars/steps_per_bar/swing`; only `bpm` is shared. A mixed render loops
over `loop_bars(arr, parts)` = lcm of both, each section rendered at
its own length and wrap-tiled (`render._wrap_tile`). Per step `drum_vel`
(the drum velocity lane), per voice `drum_mix[voice] = {level, mute,
solo, prob}`; the open hat is choked by the next closed hat; humanise
and probability are seeded from the grid so a loop re-renders identically.
`fx_drums` is the drum bus FX scope (`fx_scopes_for(view)` decides which
racks a view shows). `_merge_arrangement` must keep every `drum_*` field
when the synth form posts. Per-row tools: `/fragment/drums/row/{voice}/
{randomise|mutate|clear|fill1..8|shift_left|shift_right|mute|solo|euclid|
hitclear}` (`beatgen.generate_row`, `drumops.euclid_row`).

## The per-hit model

A hit may carry data of its own beside its cell: `Arrangement.drum_hits`
(a pool pattern's `hits`, a bank slot's `hits`) is
`{"<voice>.<step>": {v, p, r, n, t}}`, sparse, and a row may have its own
swing: `drum_row_swing` (a pattern's `swing`, a slot's `row_swing`),
`{voice: 0..0.6}`.

| field | means | range | when absent |
|---|---|---|---|
| `v` | the hit's velocity | 0.05..1 | the step's `drum_vel` (every voice on the step shares that) |
| `p` | its chance | 0..1 | 1: it always plays (times the voice's own `prob`) |
| `r` | ratchet: that many hits inside the step, evenly, each 0.85 of the one before (`RATCHET_FALL`) | 1..4 | 1 |
| `n` | nudge: its start moved by that share of a step, before swing | -0.5..+0.5 | 0 |
| `t` | tune: semitones on that hit | -12..+12 | 0 |
| row swing | that row's swing in place of the kit's (`drum_swing`); 0 is "this row is straight" | 0..0.6 | the kit's |

- **A value that says nothing is not stored** (`schema.HIT_NEUTRAL`:
  `p` 1, `r` 1, `n` 0, `t` 0; a `v` always says something), **a hit
  without a cell is dropped** on every post and every load (the cuts
  rule), and a row whose swing equals the kit's has none of its own.
  `arrange.clean_hits` and the schema node `DRUM_HITS` are the one
  cleaner; hostile values are clamped, hostile keys dropped.
- **Absent means absent.** Both fields are Arrangement fields and are in
  NOTHING that is written down while they are empty: the session file,
  a loop key, a stem key, a baseline, a pool pattern, a bank slot
  (`schema.ARR_OPTIONAL`, `schema.arr_dict` — use it, never
  `asdict(arr)`, wherever an arrangement is serialised). A pattern with
  no per-hit data is scheduled, rendered and keyed exactly as a plain
  cell pattern: `tests/studio/test_drum_hits.py` holds a frozen copy of
  the cells-only scheduler and compares every factory beat style, kit,
  bank slot and saved fixture, float for float and sample for sample.
- **Tune**: a synthesized voice with a `tune` of its own (kick, snare,
  tom, rim: `drumlab.DRUM_PARAMS`) is struck at `tune x 2^(t/12)`; one
  without (the hats, the clap, the crash) is itself; a sampled row
  (`drum_samples`) is resampled, linear, as its sample-rate conversion is.
- **Ratchet**: each retrigger is cut by the next (`Hit.until`, the open
  hat's own 8 ms fade): one voice struck again, not voices piling up.
  The last one rings; an open hat's is still choked by the next closed
  hat, whichever comes first.

### One schedule

`arrange.drum_hits(arr)` is the one schedule: per sounding voice,
its hits in time as `Hit`s. A `Hit` IS the pair `(beat, gain)` — it
unpacks, compares and hashes as one, so a reader that wants only the
pair reads it as one — and carries `tune` and `until` beside it. The
render (`_schedule_drums`), a section's cycle and its chunks, the
solo-cell audition, the stems export and the MIDI file
(`midi_bridge._drum_notes`: a ratchet is that many notes, each ended
where the next begins; a nudge is the note's own time; a tune has no
place on a General MIDI drum key) all read it, and the sidechain reads
the same per-hit facts (below).

### Chance: a hash, not a roll of the dice

A hit plays when `hit_roll(seed, step) < p`. `seed` is
`chance_seed(voice, the row's cells)` — crc32, never `hash()` — and
`step` is the hit's step **in its pattern**; the roll is a sha1 of the
two. So it is the same in every process, and:

- editing another row, a velocity, a nudge or the chance itself leaves a
  row's rolls alone (a hit that plays at 0.5 plays at any higher
  chance); adding or removing one of the row's own hits rolls it again;
- the mixer's `prob` and humanise draw from their own seeded stream,
  and the draws are made whatever the per-hit roll says: a chance on
  one hit never re-humanises the hits after it;
- **the loop, a section and the export agree.** A pattern rolls once a
  pass of ITSELF: a 1-bar pattern drops the same hits every bar, an
  8-bar one differs bar to bar (which is why `hypnotic-perc` is written
  eight bars long in the starters). A section that tiles its beat onto
  one flat cycle hands each tiled hit the pattern's seed and step as
  `k` (`song_model._tile_hits`), so every repeat and every chunk rolls
  what the pattern rolls looping on its own
  (`test_a_pattern_rolls_the_same_looping_in_a_section_and_in_every_chunk`).
  There is no roll per bar of the SONG: a section's cycle is rendered
  once and repeated, and its render is keyed by content.
- The MIDI file holds the ROLLED result (the hits this pattern's seed
  plays), not the odds.
- **The note grid has the same rule**: a step's chance
  on Perform's roll (`Arrangement.note_chance`, `arrange.note_roll`) is
  `hit_roll` of the step and a seed of the pitch classes starting there —
  `loop-and-perform.md`, "The chance lane".

### The seam: a nudge wraps, a window borrows

The loop and a section's cycle are circles.

- A hit nudged to **before the loop's first beat** plays at the loop's
  **end** (`drum_hits` adds the loop's length; a humanised hit with no
  nudge clamps at 0). It rings round the seam: the
  loop's stem is wrap-tiled (`render._wrap_tile`), a section's cycle
  hands the rest on as its tail.
- A hit nudged or ratcheted **past the end** simply rings into the tail
  the renderers already fold back.
- A tiled cycle rendered in **chunks** (`SectionParts.window`): a hit
  nudged early on a window's FIRST step sounds before that window
  starts, so it rides in the window before it, one step past that
  window's own grid, marked `pk` (`song_model._window_drums`) — the
  cycle's first step in the cycle's last window. `k` and `pk` are
  internal to a flattened cycle: never stored, never conformed.
- Chunked == one piece to 1e-9, and parallel == serial, are held at
  125 bpm, where a bar, a step and a third of a step are whole samples;
  at other tempi a chunk's offset is rounded on its own
  (`song_model.bar_sample`) and any hit, plain or not, may land a
  sample apart.

### The sidechain

`fxrack.kick_steps` (the kicks a `sidechain` unit ducks on, and part of
its stem's key) follows the row rule hit by hit: **a kick whose chance
is 0 never ducks; any other chance is not followed** (the duck is on
every programmed kick, as for the row's `prob`). What a hit says about
WHEN is followed, because it is a fact of the pattern and not a roll: a
**nudged** kick ducks where it lands (a step number with a fraction;
one nudged before step 0 at the loop's end) and a **ratcheted** kick on
every retrigger. Swing — the kit's or the row's — is not followed. A
pattern with no per-hit data answers whole step numbers.

## The step sequencer (`drumgrid.html`, `static/steps.js`)

The drum grid is one component — the `drum_grid` macro — on Drums, in
Perform's rack and in the Song drawer.

- **The table is one hidden field.** `hits` (JSON) in the grid form is
  the one copy on the page; `rswing` (the rows whose swing is their
  own) and `rlevel` (a row's level, where no kit strip is on the page)
  ride beside it. Every knob in the form is UNNAMED on purpose: steps.js
  writes what it means into those fields and the grid's own
  `input delay:150ms` post carries them — one post, one undo step,
  whatever made the edit. This is the "any control inside `#drum-grid`
  posts the whole grid" rule, used rather than fought; `drumops.
  hits_from_form` / `row_swing_from_form` / `levels_from_form` read
  them on both grid routes (`hit.<voice>.<step>.<field>`, `rswing.
  <voice>`, `level.<voice>` are accepted too: a script's or a test's).
- **The selected row** (a press on its name — which also hears the
  voice — or ↑ ↓ in the grid) owns **the lane under the grid**: the
  lane switch (`vel | chance | ratchet | nudge | tune`) shows that row's
  hits in one field. The bars are the same `.vel-lane`s: audio.js's
  drag and pen.js's pressure write them, and
  steps.js reads what changed back into the table (a lane's own number
  of levels is its `data-q`). With no row selected the lane is the
  steps' shared velocity ("every row").
- **The chance lane hatches what a step hatches**: a bar under 100 %
  is hatched, a sure hit's (100 %, `aria-valuenow="1"`) solid, so 100 %
  and 40 % differ in more than height.
- **Step LEDs say what their hit carries**: a ratchet cuts the lit step
  into its hits, a chance under 100 % hatches it (0 %: dashed), a nudge
  is a teal edge on the side it leans to, a tune one on top or
  underneath, a velocity of its own dims it (`data-r`, `data-p`,
  `data-n`, `data-t`, `--hv`; the template draws them, steps.js keeps
  them).
- **The inspector** under the grid: the selected row's level and swing
  and the cursor hit's five values as `.knob`s — so the wheel, a typed
  value, MIDI learn and the deck's dials reach every one of them
  through `synthlab.tweak` (`keys-and-midi.md`). From 1900 px each row
  also carries its own level and swing in its head. A row's level is the kit's mixer: where the kit strip is on
  the page the row's knob moves the strip's own (which posts it),
  elsewhere it rides in `rlevel`. The kit routes do not redraw the grid
  for a level or a swing: steps.js moves the rows' knobs.
- **The hit menu** (a `.pop.menu` the macro draws, one a grid): ratchet
  x2 x3 x4, chance 25 / 50 / 75, nudge, clear. It opens from a right
  click on a lane bar (menu.js, `data-ctx-pop`), the Menu key or a long
  press on a hit (steps.js), or its own `⋯`. **A right click on a step
  still erases.**
- **The grid is one Tab stop**: a step, a lane bar and a row head's
  controls take the keyboard only on the row and step the cursor is on
  (`tabindex` -1 elsewhere; steps.js moves it). The keys are in
  `keys-and-midi.md`.
- **Drum tools morph.** Every drum tool's answer morphs `#drum-bank`
  (`hx-swap="outerMorph"`), and the Perform rack and the bank come back
  OOB as morphs: the step under the keyboard keeps the focus through a
  tool, a variation, a fill (a replaced grid would drop it). steps.js
  draws its own state again after either.
- In a short window the eight rows scroll inside the grid and **the
  lane stays in sight** (sticky): a hit's value is edited there.

## The bar in other meters

The loop's meter (`Arrangement.meter`, `meter.py`; the "bar" picker in
the Drums transport and by Perform's bpm, `POST /fragment/meter`) sets
what a bar of the drum grid holds: its `drum_steps` are the meter's
(3/4: 3 6 12 24; 6/8: 6 12 24; 7/8: 7 14 28), and `drum_hits` reads a
step as `bar_beats / drum_steps` beats. The grid draws the bar as it is
(`meter.grid_accents`: a /4 meter's beats, an /8 meter's groups — 6/8 is
3 + 3, 7/8 2 + 2 + 3 or 3 + 2 + 2 — `.cell.group` between a beat line
and a bar line). A meter change fits the beat bar by bar, the variations
too, one pattern undo step (`StudioState.set_meter`). **Beat styles in
other meters** (`beatgen.STYLE_METER`, "Other meters" in every style
menu): Waltz and Jazz waltz (3/4), 6/8 shuffle and Afro 6/8, 7/8. A style
is written in its own bar's sixteenths and generated onto the grid's (a
4/4 style on a 3/4 grid keeps its first three beats); Randomise in a
style of another meter takes the loop there first.

## Variations, fills, busier / sparser, build, Euclid, kit swap

- **Variations A-D** (`POST /fragment/drums/var`, `DrumBank.
  beat_variation`): the grid holds ONE variation (the arrangement's
  drum lane, the only render input); the others wait in
  `state.beat_vars` by letter. A press on a letter that holds nothing
  copies the grid into it; `copy` overwrites one with another. A
  variation is the whole drum lane — cells, velocities, shape, per-hit
  data, row swings; the kit, the tempo and the kit's swing are the
  family's. One pattern undo step, which takes the LETTER back too (a
  step is tagged with it). **Session-only**, like the Randomise
  settings: a restart keeps the one on the grid; "Save to slot" keeps
  any. A bank slot, a kit or a pool beat loads INTO the variation on
  the grid; opening a section in Perform or loading a session drops
  them.
- **In the Song** a beat's variations are its **family**: pool patterns
  sharing `fam`, each with a `var` (A-D, `fill`). The drawer's letters:
  one the family has is `POST /song/cell` (this section picks it); one
  it has not is `POST /song/cell/variation` with `var` (`copy=1`: an
  exact copy; without: a few light hits traded) — `gen/build.
  cell_variant`. `fill=1` makes the family's fill, and rolls it again
  in place when it has one (one undo step however often).
- **The same route writes NOTES** with `lane=notes`
  (`loop-and-perform.md`, "Generators on the loop"): one memo for "the
  last generator's roll" (`StudioState.gen_again(key, lane)`), so a note
  roll after a fill starts a new undo step and the other way round.
- **Generators on the loop** (`POST /fragment/gen`, `gen/drums.py`;
  seeded, pure, and the result plays): `fill` (the last half bar, bar
  or two; the style's own table — `STYLE_FILL`; `every` 1 / 2 / 4
  bars), `build` (the beat becomes a roll over the grid's bars, two at
  least: quarters, eighths, sixteenths, then 32nds by ratchet, each
  snare hit with its own velocity 0.4 -> 1 and tune 0 -> +7),
  `busier` (ghosts beside the backbeat at 0.3, then offbeat hats, then
  the sixteenths between — never a kick), `sparser` (ghosts first, then
  what sits off the eighths — never a kick on a beat, never a clap or a
  snare on one), `variation`. **A fill or a build rolled again joins
  its first roll's undo step** (`DrumBank.gen_again`: the same
  generator on the grid it left); busier and sparser are a step each. A
  generator keeps what the grid hides past its end.
- **Euclid a row** (`row/{voice}/euclid`, `hits`, `of`, `rotate`): the
  figure starts ON a hit — the engine's `sequencer.euclid` ends on one
  (its 3 in 8 is `..x..x.x`), so it is asked for one step on:
  `x..x..x.`.
- **Kit swap that keeps the beat** (`POST /fragment/drums/kit` with
  `keep=pattern`): only the kit's voices load. The pattern, its swing
  and velocity lane, the per-hit data, the rows' own swing and the
  rows' sample assignments stay, row for row — every kit is the same
  eight voices, so "by voice role" is the row itself. Nothing in the
  grid moves, so it is no pattern undo step.
- **A style's feel** (`beatgen.STYLE_FEEL`, `dress`): ghosts with a
  velocity of their own, a chance a hit, the rows' own swing, the build
  style `roll` — added to a beat it generated; `generate` and every
  style's cells are untouched (`sounds-and-breeding.md`). **Rolls**:
  two more feel keys, drawn after every other one so a style without
  them dresses exactly as it would if they did not exist
  (`test_the_rolls_are_drawn_only_by_the_styles_that_have_them`) —
  `rolls` ((voice, sixteenth, ratchet): that hit, where the cells have
  it, struck that many times inside its step in every bar: trap's last
  eighth in triplets, the "and" of 2 in 32nds) and `ratchet` ({voice:
  (p, choices)}: each other hit rolled with chance p, its ratchet drawn
  from the choices — trap's and future bass's hats, jungle's snare
  edits). They are the per-hit `r`, nothing new in the schedule.
- **Hip-hop, drum & bass, bass music and synthwave beat styles and
  kits** (the packs are `sounds-and-breeding.md`, "Style packs: hip-hop,
  drum & bass, dubstep, synthwave, future bass"): `trap-808` (half
  time at ~140: the kick on 1 and the "and" of 3, snare and clap on 3,
  eighth hats with sixteenths and rolls), `boom-bap` (~90, swung 0.2:
  the backbeat on 2 and 4, a ghost kick and snare), `dnb` (the two-step:
  kick on 1 and the "and" of 3, snare on 2 and 4, hats shuffled 0.08),
  `dnb-roller` (sixteenth hats, ghost snares, a rim by chance), `jungle`
  (a third kick, three ghost snares, toms by chance, snare edits),
  `dubstep` (half time, offbeat hats), `riddim` (sparser: a kick, the
  snare on 3, three hats a bar), `synthwave` (kick on 1 and 3, snare and
  clap on 2 and 4, sixteenth hats with the off-16ths held down),
  `synthpop` (four on the floor, open hats on the offbeats),
  `future-bass` (half time with hat rolls) and `future-build` (eighth
  snares whose velocity and tune climb, the last half bar ratcheted) —
  inserted before `roll`, in four menu families of their own (`STYLE_FAMILIES`:
  Hip-hop & trap, Drum & bass, Bass music, Synthwave). Kits (one a
  style): *Hip-Hop 808* (`trap-808`), *Drum and Bass* (`dnb`: a tight
  snare, a ghost on step 7 at 0.3), *Dubstep* (a huge 0.4 s snare),
  *Synthwave* (a round synth kick, the off-16th hats at 0.6; its gated
  room is the style's bus chain, not the kit's) and *Future Bass*.
  `STYLE_FILL`: boom bap's fills quiet, synthwave's, synthpop's and
  jungle's toms.
- **Lo-fi, ambient, chiptune, hardstyle and phonk beat styles and kits**
  (the packs are `sounds-and-breeding.md`, "Style packs: lo-fi, ambient,
  chiptune, hardstyle, phonk"):
  `lofi` (~80, swung 0.24 — a 62 % shuffle, the hardest of any style:
  the kick on 1 and the "and" of 3, the snare on 2 and 4, eighth hats;
  the feel holds the kick and the snare straight and adds a ghost kick on
  step 7 at 0.45), `lofi-chill` (a kick, the rim on 2, the snare on 4,
  quarter hats by chance), `ambient-pulse` (a kick on the bar, a tom and
  a hat on Euclidean figures, each by chance), `cinematic-toms` (low toms
  gathering into a run at the bar's end under a kick on 1 and 3, the
  off-sixteenths held down), `chiptune` (straight: kick on 1 and 3, the
  noise snare on 2 and 4, eighth hats), `chip-chase` (four on the floor,
  sixteenth hats, a pickup snare, now and then doubled), `hardstyle`
  (four on the floor, a clap on 2 and 4, an open hat on every offbeat),
  `rawstyle` (the four kicks, a clap on 4, now and then a kick doubled
  — a `ratchet` on the kick), `drift-phonk` (the kick on 1 and the "and"
  of 3, a hard clap on 2 and 4, sixteenth hats rolling, the cowbell on a
  Euclidean 5) and `memphis` (a third kick, the backbeat doubled by a
  clap, two triplet hat rolls a bar) — inserted before `roll`, in three
  menu families of their own (Lo-fi & phonk, Ambient & cinematic, Chiptune &
  hardstyle). Kits (one a style): *Lo-Fi* (`lofi`: a soft kick, a
  brushed snare — tone 0.25, a soft rattle — a rimshot, dark hats, swing
  0.24, the ghost kick on step 7 at 0.45), *Cinematic* (`cinematic-toms`:
  taiko-ish toms at 80 Hz, a 1 s kick and a 3 s crash for the hit),
  *Chiptune* (`chiptune`: a short kick driven flat, a snare that is all
  noise — tone 0), *Hardstyle* (`hardstyle`: a 0.8 s kick at drive 4,
  the genre; the bus's Hard Kick Drive takes it further) and *Phonk*
  (`drift-phonk`: a boomy 808 kick, a hard clap, and the **cowbell as the
  rim tuned to 540 Hz** — its two partials 540 and 864, the drum
  machine's pair near enough; the rim's decay is fixed, so a short one:
  the long cowbell is the *Phonk Cowbell* recipe). There is no cowbell
  row: `DRUM_ROWS` is eight voices and a ninth would move every pattern.
  `STYLE_FILL`: lo-fi's fills quiet, the cinematic ones toms. Cinematic,
  Chiptune and Hardstyle read as **dirty kits** in
  `tests/test_drumhits.py` (low toms read as kicks, an all-noise snare's
  tail as an open hat, the long driven kick's tail as more hits).

## Percussion one-shots

**Percussion one-shots** (`synthlab/percussion.py`,
`tests/test_percussion.py`): the hand and metal sounds the eight voices
lack — cowbell (the 545 / 815 Hz square pair), shaker, conga (a skin
near 200 Hz with a small pitch drop and a slap), tambourine, clank
(inharmonic FM partials into a short comb). Pure and seeded, stereo,
-1 dBFS, ending in silence. A row's sample menu (`drumgrid.html`,
shown with an empty clip store too) has a **Percussion** group:
`perc:<kind>` posts to `/fragment/drums/sample`, which renders the
one-shot into the clip store the first time (`perc-<kind>`, meta
`source: percussion`) and points the row at it like any clip
(`drum_samples`); later rows reuse the file. Every clip is still listed,
under **Clips**.

## The Kit page: the drum page makes kits

The page at `/view/drums` is named **Kit** for the user: the rail, its
`<h1>`, the palette's "Go to Kit"
(Alt+Shift+2), the deck's view key, the shortcut sheet, the toasts and
titles that named the page. Kept: the route `/view/drums`, the view key
`drums`, the module ids (`drums.bank`, `drums.kit` …), `#drum-bank` and its
region `drum-bank`, the `/fragment/drums/*` routes, file and class names —
and "Drums" where it names the drum TRACK of a song or the rack's
Notes | Drums focus, not the page.

What it keeps: the transport (the drum clock), **the grid** — the loop's
beat, the same one Perform's rack edits, a mirror of the selected drum
slot — with its tools, notation, Randomise / Mutate / Clear, the genre
kit (Load kit, Swap kit, keep beat), **the kit as eight channel strips**
and the drum bus FX.

What it does not show: **the bank's 8 slots and the beat library list.**
Beats are loops: **✚ Save beat** (`POST /loop/beat/keep` with the
form's `name`) keeps the grid's beat as a beat loop in the Loops panel and
the drawer only — the bank and the library are not written; a name another
loop has gets "… 2" (never written over), a blank name is the generated one
("Techno beat · 124 · 1b"), and a beat a loop already holds is not kept
twice (the toast names it). **Beats ▸** opens the drawer searched for
"beat"; on this page only, the drawer's Loops list also holds the bank's
slots and the beat library's beats (`service.loops_context`: `view ==
"drums"`), as the Loops panel does everywhere. **Open** on any of them puts
it on this page's grid (`POST /loop/beat` answers on Kit as on Perform: at
the wrap, one pattern undo step; from another page it still lands on
Perform).

Nothing is migrated: the bank (`drum_bank`, `active_slot`) is session
state, and the beat library's files (`<data_dir>/presets/drums`) stay
where they are. Both show in the Loops panel as **views**
(`looplib._bank_rows`): the slots as `kind` "bank" (`ref {"slot": n}`), a
library beat no loop or slot holds as `kind` "lib" (`ref {"lib": name}`,
`StudioState.drum_lib_patterns`, read again only when the folder moves);
★, tags or a rename keep one as a beat loop (`routes/takes.py`
`_slot_kept`), ▶ renders it through the session's kit, Open loads it (a lib
beat through `load_drum_pattern`). The other readers of the bank are
untouched: Play's beats tab on the Loops page (`beat_launcher.html`, the
bank's filled slots while the song's pool is empty), the song's pool built
from a session (`song_bridge`), the drum lane's loop edit; the reset
menu's "Bank beats — Restore factory slots" still puts the factory beats
back. The routes `/fragment/drums/bank/{load,save}` and
`/fragment/drums/lib/{save,load,delete}` stay (nothing on the page posts
the saves).

### The Kit page, laid out

At 2560 x 1400, without the bank's slots, ~700 px would stand empty under
the beat, so the page keeps the small text and fills all the space — a
power tool, more on screen. Dense, from 2200 px
(`dense.css`, the 2200 block): the clock strip across; **the beat** on the
left, **the kit's eight strips** beside it (four across, two rows sharing
the column's height), **the bus FX** a pedalboard across both at the
bottom — the page ends with it, no foot. The beat's steps **take the
height** (`clamp(17px, their share, 96px)`: ~80 px squares at 2560 x 1400,
the type unchanged), and in each strip **the pad takes what its knobs
leave** (its presets under it; every strip keeps two knob rows, so the
eight pads are one size: ~280 px at 2560 x 1400) — a block of pads to
play, as a drum machine's. **Each pad shows its voice** (`kitpads.js`, the
79th island; its SVG and read-out are drawn only where the pads are this
big): the one-shot `/drum/<voice>/hit.wav` serves (what a click
auditions — a row's clip when it has one) decoded in the page and drawn
across the pad as its peaks, mirrored (`.pad-wav`), with its loudness
envelope faint behind (`.pad-env`, the hit's RMS), both `--signal`, so
night and day follow; its name top-left and bottom-right its decay and
tune (the strip's own read-outs, where the voice has them) and how long it
rings ("dec 0.42 · tune 52 · 520 ms"). Redrawn when the pad is new or
morphed (a preset, Load kit: the server's pad has no `data-drawn`) and
once the kit's post for a knob of that voice has landed — never per frame.
A click still auditions it (piano.js), with the hit flash (`.hit`).

**The five hit lanes stacked** (`drumgrid.html` `lanes`, Kit only): where
the window is 1000 px tall or more (1920 x 1080 included), chance,
ratchet, nudge and tune stand under the velocity's lane at once
(`.velrow.hitrow`, the same `.vel-lane` bars: audio.js paints them, pen
and keys as the velocity's; steps.js reads each into its own field), and
the lane switch goes; under 1000 px the switch is back (a laptop keeps
every row on screen). They show the selected row's hits, else the
cursor's row's, else the first row with a hit — its name beside each
lane's — and the velocity lane is then always the velocity; the lane keys
(+ −, PageUp / PageDown, the wheel) edit the lit lane. Their height comes
out of the steps' share (`--lanes-h`: 144 px, 202 from 2200 px where the
lanes are 36 px). Under 2200 px the layout is the tiled one ("The Drums
page, tiled" below: the beat taking
the height, the strips eight across, the bus FX a 92 px strip that
scrolls): every module on the first screen at 1920 x 1080. The layout key
stays `drums` (the module ids are the same; a saved layout is a tree of
them and still fits). `tests/browser/test_page_layouts.py` ("kit fills"),
`test_workspace.py` ("Kit fills its page").

## Drum pattern bank

Drum pattern bank (`state/drums.py`, `drum_bank`: 8 slots seeded from
`drumfactory.py`; library at `<data_dir>/presets/drums`, not the
package's own `presets/`). Neither is drawn on the Kit
page ("The Kit page" above): both are beats in the Loops panel. `beatgen.py` is the seeded
Randomise/Mutate generator. Genre kits (pattern + voices + feel) load
beside it — see "Genre recipes, kits and chains" in `sounds-and-breeding.md`. Loading a slot copies it into `Arrangement`
(drums + bars/steps/swing) — the arrangement stays the only render input.

## Drum mini-notation

Drum mini-notation (`studio/notation.py`): `bd*4 [~ sn] hh*8`, layers
with `,`. Off-grid slots are a `NotationError` (422 toast), never a
silent quantise. `to_notation` describes bar 1 only (grid is the truth).

## Drum bank saves never overwrite silently

(The Kit page has no Save to slot — its ✚ Save beat never writes over a
loop, above. What follows is the bank's rule as the routes apply it.)
**Drum bank saves never overwrite silently**: "Save to slot"
defaults to `StudioState.default_save_slot()` (first empty slot; the
active one only when none is empty). A slot holding a pattern carries
`data-confirm` on its `<option>`; perform.js mirrors the selected one onto
the form's `hx-confirm`. An unnamed save is named after `beat_style`,
deduplicated (`techno`, `techno 2`), never `slot N`. The Drums reset menu
(`bank_factory` in the `resets` context) offers "Restore factory slots"
(`POST /fragment/drums/bank/factory`, confirmed), which puts
`drumfactory` back into the bank without touching the grid. Opening a
section or launching a pool beat clears `active_slot`: its beat is not
any slot's.

## Drum tools cover every bar

**Drum tools cover every bar**: `beatgen.generate` fills every
bar — the forced/Euclidean groove repeats and only the `prob` sprinkles
re-roll from the same seeded stream, so bar 1 is byte-identical to the
1-bar output (pinned) and later bars are a light variation. Growing
`drum_bars` on the transport repeats the bars you have
(`beatgen.extend_bars`, velocity lane included) instead of adding empty
ones — **but only into a block that holds nothing**
(`beatgen.repeat_offsets`: each phrase-long block of the new range): a
grid shrunk 2 → 1 keeps bar 2's hits, hidden, and growing back shows
them and their velocities again rather than copying bar 1 over them
(the note grid's rule, `routes/patch._grow`, taken per block: 1 → 4
with a hidden bar 2 brings bar 2 back and repeats bar 1 into bars 3-4,
where the note grid repeats nothing once any new bar holds a note).
Hits still past the new end stay hidden. One pattern undo step. Each bar line after the first gets `.cell.bar`, heavier than `.beat`.

## The Drums page, tiled

In the dense tier from 1200 px the page is **at least** the window
(`dense.css` D18):
the clock strip, the beat (`#drum-bank`, without the bank's slots row)
with **the grid taking the height** (a step is
`clamp(17px, its share of the pane, 44px)` — 64 px in a window 1200 px
tall or more — from `--rows` on the grid and a size container), and
**never less than its floor**: every row at the dense 17 px plus its
lane (`--rows` x 19 px + 32). On a laptop window (1366 x 768, 1280 x 800)
the floor pushes the kit strips and the bus FX under the fold and the
page scrolls — without the floor the grid's box would be 53 px and
20 px there and only the kick row would show
(`tests/browser/test_page_layouts.py` holds every row on the first screen
at both sizes). The playhead spans the rows and their lane (its inline
`grid-row`, as the piano roll's), never the slack under them. Then the
**kit as eight channel strips across**, and
the master FX. From 2200 px the page tiles across its width, each tile
as tall as it is ("The Kit page, laid out" above). A strip is one voice: an LED, a lead (`voice → kit → master`), the
reset, its pad and presets, then **a `.knob` a parameter** with the value
under it and the name under that — shape, level and chance of every
voice on the page at once. They are the same `input[type=range]`s in the
same `#drumkit` form (`name="<voice>.<param>"`, `data-default` from the
parameter's spec), so the form's post and the deck treat them as any
range. A bool
(the hat's open) is a `.seg` of two radios.

**A row's level and swing in its head, from 1900 px** (captioned "lvl" / "sw" under
the last row, in the velocity row's head: two twin dials a row would
read as one control twice): any input inside
`#drum-grid` fires that form's `input` trigger and posts the whole grid
— which is how they post ("The step sequencer" above: unnamed knobs,
two hidden fields). Below 1900 px the head has no room and the
inspector under the grid holds the selected row's. A right click on a
row's name opens its ⋯ tools (menu.js, `data-ctx-pop`); on a step it
erases.

## Drum row heads

**Drum row heads** (the page's head is its rail label, "Kit"): the row's
name (a button: a press hears
the voice and selects the row) + M + S + a `⋯` `<details>` menu
holding ⚄/±/✕, fill, shift, Euclid, "plain hits" and the sample select
(in the head the select would squeeze the buttons to 7-9 px). Euclid's three numbers
belong to a form of their own outside the grid's (`form=`), so they
are no field of the grid. Targets are 24 px, 44 px under `(pointer: coarse)`. Heads are
`position: sticky; left: 0` and rows `min-width: max-content` (without it
the sticky head is released halfway along the scroll). The menu opens
sideways on desktop and under/over the sticky head on touch so it never
leaves the visible grid. Each row's select is named `clip.<voice>`: all
eight sit inside `#drum-grid`, so htmx posts them all, and a shared
`clip` name would make the kick row's value win for every row.
