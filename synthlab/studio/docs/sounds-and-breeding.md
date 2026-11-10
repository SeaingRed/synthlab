# Sounds: browse and Use, library, breeding, genre

Where sounds come from and how they are picked: the browse / audition
/ Use contract (`sounds.py`), the generated library, factory patches,
preset format 2, breeding (single patches and layered Sounds,
`breedboard.py`, `genome.py`) and the genre layer (recipes, chain
presets, kits, beat styles, steering). Index: `README.md`.

## Browse, audition, Use

**Browse, audition, Use** (`studio/sounds.py`): a browse surface only
*finds* a `Sound(patch, chain, label, home_view)` —
`sounds.find(svc, source, ident)` over `SOURCES` (`library`, `factory`,
`recipes` — genre recipes, format-2 Sounds, see "Genre recipes" —
`preset`, `breed` and `map`, the last two both rows of
`svc.space_store()`; `chain` is a format-2 preset's layers/main FX/
curves). Everything after that is two calls, the same for every source,
and each route is a 2-3 line caller:

- `svc.audition(sound, mix=False)` — the card's ▶ as a WAV. On its own
  by default (the reference phrase, no other layers, beat, session FX or
  curves — a preset's own chain stays — so siblings stay
  tell-apart-able); `mix=True` (`?mix=1`, Breed's "in your mix", the
  Library's "mix") is **in your mix**, exactly what Use lands on Synths,
  byte for byte and the same cache entry (Use after ▶ renders nothing).
  Both hear the slot unmuted. Never a bare note: a bare
  `Synth(patch).note("C3")` would promise a sound Use doesn't deliver.
  **A breed card on its own plays in the sound's register**
  (`Sound.register` = `audition.register_of(patch, row features, layer
  octaves)`, set for the audition log's rows): "bass" (over half the A3
  probe under 120 Hz, or osc1 an octave down; never when a layer already
  sits under the main), "pad" (measured attack > 0.15 s), else "lead";
  `audition.listening_root(root, register)` is the root's octave nearest
  the library's family pitch (`REGISTER_PITCH`: A1 / C3 / C4, so root A
  plays A1 / A2 / A3). It is the one audition that is not what Synths
  plays after Use ("in your mix" and Use keep the session's notes); the
  notes are in `RenderInputs.key()`, and the breeder's probe stays at A3
  for every sound (`tests/studio/test_audition_register.py`).
- `svc.adopt(request, sound)` — Use: `StudioState.load_sound` (stash
  "before last load", unmute the slot — a muted slot would make every
  Breed card identical — and open a level at 0 to 1.0 (`landing_gain`,
  which "in your mix" hears too; a level at 0 would silence every sound
  picked after it), set the patch, a preset's chain, and baseline
  "sound" (+ "fx" for a chain) under `sound.label`), save, then
  `full_page_with_preview`. From the source's `home_view` (Library,
  Breed) Use is a decision and lands on Synths, body *and*
  `HX-Push-Url: /view/editor` (without it the editor would be on screen
  under a /view/library URL); from anywhere else the current view
  re-renders (the Map keeps you on the map; the Sounds dock is on Synths).
  `full_page_with_preview(request, view=..., push_url=...)` is the only
  sanctioned way to move somewhere else. htmx 4 keeps the `HX-Push-Url` /
  `HX-Replace-Url` response headers but has no `HX-Trigger`-style
  equivalent for history beyond those two.
Perform's lane heads use `sounds.find` too (`POST
/fragment/perform/lane/sound`: `StudioState.lane_sound`, one pattern
undo step, a queued preview), over `sounds.menu` — every sound as one
menu, base.html's `#lane-sounds`. `adopt(request, sound, how)` and the
load routes take `as` (`StudioState.load_sound_as`: what the load
replaces, `lanes-and-layers.md`, "Layer strip"). `tests/studio/test_sound_sources.py` is
ONE parametrized test over `SOURCES` × {fresh, busy} × {alone, mix}
(mix card == landing == ▶ on Synths; alone == Synths fresh and untouched
by a busy session; baseline clean; the landing rule) — a new source is
one `SOURCES` entry plus its `SETUP` row there
(`test_every_source_is_covered` fails until you add it).
`/breed/note/` (the spectrum's measuring note — a layered card's whole
staged stack) stays for the badges but fronts no card.

### A layered breed card is a Sound with a chain

**A layered breed card is a Sound with a chain** (`sounds._row_chain`:
the row's `sound`, conformed through `PRESET_V2` exactly as a v2
preset's): its ▶ is the whole stack alone, "in your mix" and "Use with
its layers" (`/fragment/breed/adopt`) are the one load a v2 preset takes.
Its "▶ main only" (`?main=1`) and "Use sound" (`main=1`) are the
`breed_main` source: the same chain with `layers` None, which
`render_inputs` and `load_sound` read as "main FX and curves, the
session's lanes kept" (alone: no layers, gain 1). Every load that brings
a chain stashes the stack it replaces in `state.stack_prev` (other
lanes, main FX, curves; schema'd), and `swap_prev` ("Before last load",
`/fragment/restore_prev`) swaps it back with the patch; any patch-only
load clears it — except one that drops the last Sound's own FX: a
chain's main FX are also kept on the lane as `sound_fx`, and a
patch-only load while the rack still equals them empties the rack (and
stashes it), so a card's highpass doesn't ride onto every Library sound
after it; a rack you changed stays. "Use with its layers" carries
`hx-confirm` when `lanes_with_notes()` > 0 ("replaces 2 lanes"). The
depth select (`#depth`, `/fragment/breed/depth`, `state.breed_depth`,
default single) saves on change like `#wildness`; the generation buttons
include both.

## Sound library

**Sound library** (`studio/library.py`, view `/view/library`): 96
generated patches, 6 family *recipes* × 16, each family picked to spread
evenly dark → bright by spectral centroid, frozen in
`studio/presets/library.json` (patch + tags + spectrum + brightness).
Rebuild with `uv run python -m synthlab.studio.library [seed]` (~30 s);
`tests/studio/test_library.py` drift-checks a sample. The library also
feeds the breeder as out-cross donors.

**Names**: each of the 96 is named for
what it is — "{shade} {trait} {oscillator} {family}", *Dark Driven Sine
Bass*, *Bright Wide Saw Pad*, *Mellow Octave String Pluck* —
`library.describe`, which reads the stored entry alone (the shade tag;
the first of `library.traits(patch)` — ring mod, cross-FM, a pitch
sweep, routes, a filter LFO, unison, resonance, drive, glide, vibrato,
tremolo, PWM, noise, detune, octaves, sub, a snappy filter, slow, muted,
an FM bell, a damped string, sustained, percussive, lingering — that no
earlier entry's name took; its osc1; the family's noun), so the names
are unique by construction and a rebuild names its draws the same way.
**The name is the sound's id** (the audition URL, Use's form, a session's
patch, a clip-hub plan, the agent), and older saved state names these
sounds by an earlier `namegen` name ("Obsidian Bed", "Wave Beam 2": a
shade word and a made-up noun), so each entry keeps that name as `was`,
and `get_library` finds a sound by either — it loads under its current
name. The names are written into `library.json` by hand (never by
re-running the build: it re-draws all 96); a
remeasure that moves a shade tag does not rename (`test_library.py`
holds uniqueness, the family's noun, and that every `was` still finds
its sound).

**The page**: each family is a section — a head strip
(name, count, a dark → bright legend: a family's grid runs that way) over
its cards in a grid that fills the width (auto-fill, 196 px at least: 7
across at 1920 px beside the dock, 5 at 1366) — the core library's
categories first ("The core library", below), then the recipes, and the
selected sound's detail docked at the right. **One layout in both
densities** (style.css 9b1; dense.css only seams it): in dense.css alone,
the comfy density, and a touch-first screen that never loads dense.css,
would get a wall of 170 px cards with every third key cut off and no dock.
From 1100 px the view is the window (head, the filter over the browser,
the dock); the browser scrolls inside itself, the filter stays in sight
and each family's head sticks while its cards scroll under it. Under 1100
px the page scrolls and the filter sticks under the top dock.

**A card** is the audition (the whole card: `.lib-play` fills it) — a pilot
lamp (the transport keys' jewel, in the signal's teal: dark glass, lit
while the card sounds — library.js's `.playing`), the name (an ellipsis
and the name in the title when long), the spectral centre as a read-out
(`hz`: "233 Hz", "12.7 kHz", never "12682 Hz" running into the name), the
24-band spectrum in a well 34 px tall with its low / mid / high thirds
ruled, and the tags as chips: a role in outline, **the genre an engraved
plate** (`.g`), **the tone a lens** dark / mid / lit (`.tone-*`), the fx
count teal (its `title` the chain), "+layer" dashed; a chip that does not
fit drops whole. Its three keys sit at the foot's right, short — "▶ mix",
"+", "Use" — so none is ever cut off — **on every card, the selected one
too** (in the card's detail alone, which the dock takes, the card just
clicked would have no Use); a card's Use is a plain
key, and the sound on Synths shows a badge, ✓, where its Use would be.
**The dock's keys** (`.lib-d-acts`, once a page, at the dock's foot over
the detail: "▶ in your mix", "+ Layer", "Use on Synths" — the page's one
primary — or "Reload on Synths", plain, when the sound on Synths is
edited, or "✓ On Synths") press the selected card's own (library.js,
`data-press`); what they read follows that card by `:has()` (style.css
9b1), so nothing is drawn twice per card.
audio.js marks the card clicked `.active`, and that is the selection: the
card's own `.lib-detail` — its name and chips, the spectrum large, meters
for brightness and the spectrum's thirds, facts from the stored patch, how,
**the FX chain as a signal flow** (a chip a unit, a teal lead between),
room left at its foot for the dock's keys — is shown in the dock by CSS alone:
an absolutely positioned descendant of the view's grid, placed with
`grid-area`. So **nothing between a card and the view may become a
containing block** (no `position`, `transform`, `filter`, `contain` or
`container-type` on `.lib-browser`, `.shelf`, `.lib-grid` or `.lib-card`),
or the detail lands inside its card. Nothing is rendered per sound on open
beyond the card and its detail. A card's spark is one `<path id="sp-N">`
(the one signal graphic: `sigdraw.sig_path`, frontend.md); the detail
re-uses it (`<use>`: a clone takes no stroke, the dock's is the lit area
alone). **Rows** (the ☰ toggle beside the filter, per browser:
`html[data-lib-view="rows"]`, library.js) is the same markup as one line a
sound — lamp, name, centre, a short spark, the chips, the keys — two or
three columns of them, to scan all 162.

**Finding a card** (library.js): ← / →
the previous / next card shown, ↑ / ↓ the card above / below, nearest in x
(Enter hears). The filter's tags run in three captioned groups — family ·
tone · genre — each chip with how many it would show with the words and
the other chips (`data-n`), the count of every card ("6 of 162") beside
them, and each family's head "6 of 53"; a family the filter empties is one
"no match" line, not a gap; a filter that hides every card offers **Clear
filter**, not a song. Re-applied after every swap (a morph brings the cards
back with the server's `hidden`).

**The Map is never an empty well**: every shipped sound —
the factory's 31, the recipes' 160 (by the main patch), the library's 96
(287, `mapview.shelf_sounds`) — is a faint point behind the logged ones,
measured on the breeder's probe like them and frozen in
`presets/map_points.json` (`tools/refresh_sound_meta.py` rewrites it
after an engine change, `measure_shelf`; `tests/studio/test_map_view.py`
holds it to the shelves and re-measures a sample). They are fitted
**with** the logged rows (`SpaceStore.project_2d(background=)`): one
space, steady as the log grows — a bred point lands among the sounds it
is near. A shipped point's click is its shelf's own Use
(`/fragment/{library,recipe,factory}/load`), its name on hover. The
`NEIGHBOURS` (8) points nearest the sound on Synths are drawn bright and
listed in the rack as **Near you** (`mapview._layout`, `map_facts`'s
`near`; the sound itself left out when it is a shipped one), each a
click to load, beside **Newest**. The plot is 100 x 54: the
shape of the well it fills on a desktop window. With nothing logged the
map is the shipped field and you; fewer than three points in all still
says how to fill it.

**The Map's "you are here"** is the sound on Synths: its logged row when
it was logged (the exact patch), else the sound itself, measured on the
breeder's probe (routes/views.py `_here_row`, memoised by its patch) and
placed by the logged rows' own PCA fit (`SpaceStore.project_2d(extra=)`:
the logged rows land where they would without it) — a point with its name
shown and no load, never the newest row standing in for it.

**Breed's page**: a rack (parents, wildness / depth, "Breed next
generation" — the one primary —, the seven directions, history) beside
the board, 4 x 2. A card reads down — name, spectrum, what was measured,
what changed, then its actions right under them — and is as tall as
that (actions above the name would leave the cards 500 px tall with the
slack inside them). A history dot's `title` names its generation's first
sounds. A card's meters (sub, brightness) are the probe's own
numbers; `bright_pct` (mapview.py) is the Map's hue scale as a length.
The Map's numbers are that probe's too — one A3 note — so its "you are
here" says "at A3" and its legend "measured: on one A3 note": a bass
read 222 Hz and no sub there and 55 Hz / all low on the Library, which
measures it in its own register.
Cards do not record which parent made them, so no parent -> child leads
are drawn.

## Factory patches (`factory.py`)

`factory.py` — factory patches carry precomputed `spectrum` literals that
`tests/studio/test_factory.py` drift-checks by re-rendering; regenerate them
with `soundmeta.preset_meta` whenever a factory patch changes. Factory
patches with `glide > 0` must be worklet-playable (pinned by test).

**Oscillator demos** (four entries; one per engine feature — hard sync,
fm4, the wavetables, drift — so each is reachable from the Library and
held by the parity corpus): *Sync Lead* (osc2 a saw hard
synced a fifth over osc1, the filter envelope routed to `sync_pitch` for
the sweep), *FM4 Tine Keys* (fm4 `pairs`: 2→1 the body, a 14:1 operator 4
gone in 0.12 s the tine, a 4.5 Hz tremolo), *Wavetable Scan* (the `scan`
table at 0.55, three unison copies, a slow filter LFO) and *Drifting Poly*
(saw + square at drift 0.6).

### Hard sync, FM4, wavetables and drift

Nineteen Patch fields, all off at their defaults (`render.KEY_ADDED_FIELDS`:
keys without them unmoved). In the editor (drawn as panels: "Oscillator
panels" below): **Hard sync** and **Sync pitch** one block under
Oscillator 2; an **FM4 shape** panel under the FM shape's (its rows hide
unless an oscillator plays fm4, from `uses_shape("fm4")`); **Analog drift**
a knob beside Drive in Character; fm4 is a pill on both
oscillators (`Ui.extra`, as the sampler on osc1). The agent
reads them from the registry like every field (its `set_params` catalogue
puts a numbered run of one range — fm4_ratio1-4 — on one line).

Breeding: these genes come last in each kind's run (`tests/test_params.py`
pins the order), so they move a seeded draw sequence only where one of them
is drawn — every wildcard (`random_patch` walks every gene), and a mutation
of a patch that has one of them live. `osc2_sync` (live on a syncable
osc2) and `sync_pitch` (live while synced) cross over with osc2's block,
which is drawn once, so they cost no crossover draw; the fm4 fields are one
block drawn after every other one. **fm4 is a hand-made shape** (the
osc caps' `HAND_SHAPES`, with noise and sampler): never drawn by a jump or
a wildcard — a random 4-operator patch is rarely a sound, and a longer
shape gene would re-draw every seeded litter — but its fields breed on a
sound that has it. **Drift** is a gene (0-0.5, cap 1) live only once a
sound has some: a wildcard may roll it, mutation moves it, nothing switches
it on (its point is note-to-note variation, which the one probe note hears
little of). `sync_pitch` is a route target outside the gene's
destinations (`MOD_GENE_DESTINATIONS`, as "pitch"). The sub role pins
`drift` to 0 (`Role.pins`: set without a draw, unlike a rule, so it
moves no layered litter). Measured over `tools/breed_spread.py`'s
starts, directions and two seeds, the board's spread per generation with
these genes is 5.12 5.20 5.18 5.06 5.08 (4.90 5.02 4.99 4.80 4.84
without).

### Oscillator panels

Hard sync, fm4 and the wavetables are drawn as panels rather than generic
rows (fm4 would be fourteen flat sliders, the twelve wavetables a bare
select). **Every field is still its own parameter row** (`param_row.html`: the same `name`, `id="row-<name>"`
and range input as any), so the form, undo, the pen, the deck, MIDI learn
and `synthlab.tweak` reach them as any row, and a macro's or an A/B morph's
out-of-band rows come back in the same shape (`param_row.html` draws the
special ones itself: `tests/studio/test_osc_panels.py` morphs both). The
drawings are `studio/oscfaces.py`'s — geometry computed once at import from
what the engine holds (`core.FM4_ALGOS`, `core.WAVETABLES`), SVG with no
colour in it (style.css 17.1 strokes it), a few kB on the page and never
the 3.8 MB `/wavetables.bin`. Jinja globals `osc_faces` and `spec_of`
(`app.make_templates`). The live parts are `static/panelfaces.js`.

- **FM4 operator panel** (`fragments/fm4_panel.html`, drawn where
  `fm4_algo`'s row stood; its block hides with the shape — `shape-<shape>`
  joins editor.html's generated hide rule). The algorithm stays a select;
  beside it,
  each algorithm's wiring drawn (`oscfaces.FM4_FACES`: carriers on the
  bottom line into the output bus, each modulator above what it bends, op
  4's feedback loop) — a view, `aria-hidden`, a click picks that algorithm
  through `synthlab.tweak` onto the select. Under it the operators as a
  matrix in a well: a column an operator (its lamp lit while it is heard —
  a carrier in the algorithm the select holds: editor.html's generated
  `:has(option:checked)` rule sets `--on`, no script), a row each for
  ratio, level and decay, op 4's feedback under its column. Each cell is a
  `.knob` whose label is visually hidden ("FM4 ratio 2": the deck's and a
  screen reader's name). **Ratio presets** (`ratios ▾`, `oscfaces.FM4_RATIOS`:
  Unison, Harmonics, E-piano tine 1:1:1:14, Bell 1:3.5:1:7, Brass, Hollow;
  each a `FM4 ratios: <name>` command) set the four ratios through
  `synthlab.tweak`, a knob widened first when a ratio is past its drawn
  travel (14 > the gene's 12) — one form post, one undo step.
- **Wavetable picker**: `wt_table` is a radio group of twelve drawings
  (`Ui.widget` "pills"; `oscfaces.WT_FACES`: each table's frames over one
  another, 48 points a frame). The frame the position stands on is lit
  (`--wt-pos` on `.wt-pick`, from the server and then from the `wt_pos`
  slider as it moves; each path's opacity falls off with its distance
  from it, CSS math only) and a tick under the chosen table marks it. The
  arrows move the choice (a native radio group), each move a post. The
  group carries a select's id, `p-wt_table`: a radio group with an
  id is a stepped value control (tweak.js `steps`; `keys-and-midi.md`, "A
  dial writes through the one hook"), so a deck dial, a learned CC, the
  dial menus and "Put these on the deck" step the tables as they would a
  select.
- **Sync** — the switch and `sync_pitch` (a knob) one block in
  oscillator 2's area (`.sync-block`, a well). **Drift** — a knob beside
  Drive, the warmth pair first in Character, whose six knobs stand on
  one line (a sixth of the line each: style.css 17.1). (Character has no
  sub-headings: two, Warmth and Voice, would push the mod matrix's lower
  jacks under the keyboard dock at 1920 x 1080 —
  `tests/browser/test_bench.py`'s drag.)

### Filter and modulation parameters

Thirteen filter types, a second filter, and a deeper modulation
system — twelve route slots, LFO 2 and 3, envelopes 3 and 4, the general LFO shapes
(drawn, chaos, smooth random), the mod wheel, aftertouch, key and random
sources, and ten more route targets (the engine: the root `docs/engine.md`,
"More filter types and filter 2", "General envelopes and LFOs", "Mod matrix"; the live
voice: `live-voice.md`, "Filters and modulation"; the panels: `synths.md`).
Every field is opt-in, its default "off": the 293 shipped patches render
bit for bit the same with every one of them off (native and NumPy,
hashed), and
`render.KEY_ADDED_FIELDS` keeps them out of the keys of patches that leave
them off.

- **Breeding does not draw them.** None of these 55 fields is a gene
  (`Param.nogene`: a longer genome re-draws every seeded litter), and the
  slot, source, target, filter-type and LFO-shape genes keep a narrower
  set of choices (`MOD_GENE_SLOTS`, `MOD_GENE_SOURCES`, `MOD_GENE_DESTINATIONS`,
  `FILTER_TYPES`, `LFO_SHAPES`; the hand-made rest is each Param's cap).
  `genome.recipe` unplugs slots 5-12 without a draw where a role pins its
  routes off, `genome._route` and `breed._route_on` fill the first four,
  `modmixes.random_mix` draws two to four routes: no seeded litter, layered
  litter or mix depends on them. A bred child keeps a parent's filter-2
  and extra-modulator settings as they are (crossover swaps genes only).
- **The agent** sets them through `set_params` (filter 2, envelopes 3 / 4
  and LFOs 2 / 3 are in its catalogue: `agent/tools.UI_PARAMS`; the
  declarations are 12,789 characters) and `add_mod_route`
  (every source, every target, slots 1-12). Not the wheels: the player's.
- **Five demo factory sounds** (`factory.py`, after the oscillator
  demos): *Comb
  Pluck* (a key-tracked comb rung by a noise burst, filter 2 after it
  opening on the envelope and on the key), *Dual Filter Sweep* (a 24 dB
  low-pass beside a band-pass that LFO 2 walks, envelope 3 on the
  resonance), *Chaos Pad* (LFO 2 chaos on the cutoff, LFO 3 smooth random
  through the wavetable, a phaser after the filter, a random detune per
  note), *Wheel Growl* (a diode-ladder bass the mod wheel opens and
  wobbles, aftertouch on the drive; plain at rest) and *Drawn Gate Pad*
  (LFO 1 drawn as a gate, a beat long). All live (the corpus plays them at
  C2 / C4 / C6); nobody has listened.

## Preset format 2

**Preset format 2**: `save_preset(name, include_layers=True)` writes
`{"format": 2, patch, layers, main_fx, curves}`; v1 flat-Patch JSON
loads unchanged (`unwrap_preset` is the shared reader); a v2 file whose
`patch` isn't one is refused, as a bad v1 file is. A v2 preset's
▶ audition renders the FILE's chain, not the session's, to keep the
audition-consistency contract. Loads of every kind stash the outgoing
patch in `slot_prev` (reset menu: "Before last load"), and patch edits
push a 32-deep session-only undo ring (Ctrl+Z / the reset menu's ↩).

## Breeding

**Breeding** (`explore/breed.py`, `explore/probe.py`,
`studio/breedboard.py`): `WILDNESS` (tame/normal/wild/feral) is the
`amount`, persisted as `state.breed_wildness`, posted via `#wildness`;
`breed.litter_profile(amount)` turns it into what a litter may do —
tame: no random card, no donor cross, jumps 10 %, drift 15 %, <= 5
genes a child; each level up raises jumps, drift, donor crosses and the
share of `wild` re-rolls. Crossover swaps whole module blocks
(`breed.CROSS_BLOCKS`), never single genes; a mod slot turns on as one
move (src+dst+amt), a tremolo depth wakes its rate. Badges compare the
child with its *parent* (not the crossed intermediate).

### Everything is decided on one probe per candidate

**Everything is decided on one probe per candidate** (`probe.py`): A3
(the phrase's register), a 0.4 s gate plus the patch's release (capped
at 1 s), synced LFOs at the session bpm (`at_bpm`) — the same render
feeds the quiet gate (loudest 50 ms < -40 dBFS or mostly < 30 Hz is
never picked while 8 audible exist), the steering score, the novelty
gate, the maximin pick, the tile's spectrum/numbers and its name (a bare
A1 0.6 s note would be one nobody hears, and steering need not render a
kept candidate twice). Distances are on a *standardised* vector (per-measure
library mean/std, the fixed table `explore/scale.py`; regenerate with
`uv run python tools/refresh_breed_scale.py` after an engine change —
`tests/test_breed_scale.py` says when); raw, crest factor would be ~75 %
of every distance. Novelty gate 0.75 (0.3 on tame). Steering measures a
40-candidate pool and keeps the best 30 % before the pick — for six of
the eight cards: the last `genome.EXPLORERS` (2) are the most different
candidates of the whole audible pool, whatever they score (as the
layered pick does). All eight from the best 30 % would breed the board
into one sound when the best three are starred (mean sibling distance by
generation 3.90 -> 2.60 over six; with the explorers flat at ~4.9, same
best scores: `uv run python tools/breed_spread.py`). A test judging "did the steer
move the board" reads the steered cards (`[:-EXPLORERS]`). `breed.steer`
does the same with one pick in four. "cleaner" /
"grittier" read per-frame flatness over 200 Hz-8 kHz in decades
(`analysis/texture.py`; whole-note flatness is ~0 for anything pitched),
grittier adds harmonic density and frame crest (drive). Probes run on 4
threads (bit-identical to serial); budget ~400 ms plain / ~600 ms
steered, pinned in `tests/studio/test_breed_board_rules.py`. `svc.space_store()` is ONE long-lived `SpaceStore` holding the
log's rows in memory (re-read only when the file's mtime/size change;
`add` updates it; own lock) — pass it to `new_generation(store=...)`.

### Layered Sounds

**Layered Sounds** (`studio/genome.py`): `new_generation(depth=...)`
with `genome.DEPTH` = single (the above, untouched — a plain patch per
card) / layered / deep breeds a whole Sound in the format-2 shape
(main patch, <= 2 layers, main FX, <= 2 curves). The main patch still
comes from `breed.generation` (its `origins` out-param says which parent
each child grew from, drawing nothing from the rng); `genome.litter`
grows structure on that parent's (`grow`: the depth's fill — layered +1
layer p .5, +2 p .15; deep 1-2, a 2nd p .4 — then the wildness's
`_MOVES` of `_OPS`: add/remove/re-roll/mutate(half strength)/nudge a
layer, add/remove/replace/nudge FX, add/remove a curve (rise/fall/dip/
swell on a live curvable gene), a mod route on/off as one move, swap a
whole layer from another parent, graft a library donor of the role's
family), then `repair`: caps (layered <= 2 main FX, <= 2 routes, <= 1
curve; deep <= 3 / 1-3 FX a layer / <= 3 / <= 2, and the only depth with
reverb/shimmer/granular — they are the costly probes; never
sidechain/gate/pump) and the **role recipes** (`ROLES`: sub -1/-2 oct
sine/triangle LP 80-200 Hz -6..-12 dB, and a 120-180 Hz HP on the main
and a pluck; pad 0/+1 slow attack/release, unison, LP, -8..-14 dB, HP 200
and chorus/reverb; pluck short decay, synced delay, -4..-10 dB; air +1
(+2 via its osc1_octave) BP/HP 4-10 kHz, width, -14..-20 dB). A new
layer's role fills a gap in the parent main's probe (`_gap_weights`);
steering biases roles, FX and moves (`STEERS`). Every layer *follows*
the main lane (see "Following layers" in `lanes-and-layers.md`). Each layer's gain is staged from
probe RMS to its role's dB under the main (`stage`), so the main stays
loudest. **One probe of the whole stack** (`measure_stack`: main with
its FX and curves drawn across the note, each layer at A3 + its octave
with its FX, summed at its gain); every render memoised by genome
(`_MEMO`), so children sharing a parent's layer reuse it; a plain stack
measures exactly as `probe()`. The pick (`genome.pick`, `card_ok`): quiet,
sub-only (> 85 % under 120 Hz) *and* main-alone gate — the main lane's
own loudest 50 ms >= `MAIN_QUIET_DB` (-30; the quietest preset is -17):
without it a main high-passed at 6-16 kHz would pass on its layers, floor
their staged gains at 0.02, and play near-silence from "Use sound" and in
the mix (the top-up counts `card_ok` too) — steering keep, novelty 0.75 on
feature distance + `LAMBDA` 0.5 × `struct_distance` (role-set diff +
½ layer-count diff + FX-kind diff), maximin for 6, and 2 **explorer**
slots from the whole audible pool. A steered stack litter measures 30
(`_STACK_STEER_POOL`), not 40: budget < 1 s at deep on the ARM machine
(~0.5 s plain, ~0.7 s steered), pinned in `test_breed_genome.py`.
Badges are structure first ("+sub −1 oct", "+pad (Sable Wire)",
"reverb", "Cutoff ↗", "2 mods", a role's own signature FX left out),
then the main patch's. A layered card's space.jsonl row carries `sound`
beside `patch` (`genome.row_sound`: the conformed chain + each layer's
staged dB and donor); a row without one is a plain patch
(`stack_of_row`), so the Map and every older row keep working. Tiles
carry `chips` (`[MAIN saw][SUB −1][PAD]`) and `layered`.

### What a card changed

A card says what it changed from its parent (`breedboard.changes`, the
tile's `changes`, schema `tile.changes`): the genes heard in either sound
that moved — a numeric one by at least 3 % of its range, a choice always
— biggest move first, eight at most. Each row is the gene's name, its two
values as a card prints them ("480 Hz → 1.2 kHz", "saw → fm") and a bar
pair: where the parent sat on the gene's range (faint) over where the
card sits (the signal); a choice draws ⇄. The parent is the nearest by
genes (a crossover's nearer side); a layered card is told against its
origin's main patch. With the list the gene badges keep only how it was
bred (× a donor, big jump, random). An older saved board has no list
and shows its badges alone. In the dense tier the board is 4 x 2 at
every width from 1100 px, each
card a size container: the spectrum 40 % of it, the list the rest (rows
past its height are cut); a new litter rises in card by card, its
spectra growing from the floor and its bars running out (pagefill.js,
GSAP).

### Stars, generations, restore

Starred parents are **consumed** by a generation (stars are pruned to the
new board); any star can be taken off, and a card of a restorable
generation can be starred. Star / generation / restore answer with the
board; `#breed-parent` and `#gen-strip` are regions and come back OOB
when a star, a generation or a restore moved them; the strip is always
in the page (hidden while empty).

**A litter lands only on the board it was bred for.** A generation
renders outside the studio lock and commits when done; if the board was
replaced meanwhile — a project import (`_apply`) or the `board` scope
reverted / reset (`_scope_set`), each of which bumps the runtime counter
`StudioState.board_epoch` — the commit is refused with a toast ("The
session changed while this litter was breeding…") and nothing is
written — never the old session's litter, history and pruned stars over
the new one's. Nothing else moves the counter: an edit, a star, a save or
another scope's reset mid-generation leaves the litter landing
(`tests/studio/test_breed_view.py`).

## Genre recipes, kits and chains

**Genre recipes, kits and chains** (melodic techno ~125 BPM,
UK garage/house ~132 BPM; generic names only — never an artist's, but
a style's label and its kit may be a nod: "Style packs: electro and
industrial"). The **style packs** build on them — see "Style packs" at
the end of this section; each subsection below says what they add to it.

### Recipes

**Recipes** (`recipes.py`, `tests/studio/test_recipes.py`): 160 whole
sounds (the first 12, then the house / garage / techno / trance packs'
17, the electro and industrial packs' 12, the psych and sixties packs'
12, the garage / techno / house / electro / trance top-up's 13, the hip-hop to future-bass packs' 22, the
lo-fi to phonk packs' 23 and the core library's 49, in that order — the
order is pinned; the core ones are on the Core shelf, the 111 genre ones
on the Recipes shelf)
whose FX/layers are half the recipe, so they are **format-2
Sounds**, not factory patches (the factory holds a bare patch): the
`recipes` source in `sounds.SOURCES` (home view `library`), the
Library's Recipes shelf (`/recipe/{name}/audition.wav`,
`/fragment/recipe/load`, "＋ Layer" with `source=recipes`), Use = the
v2-preset load. A recipe with no layers of its own loads with chain
`layers` None (its FX and curves land, the session's lanes stay), one
with layers replaces them. Card numbers (spectrum, centroid,
brightness rank) are in `presets/recipes_meta.json` from
`recipes.measure_all` (the whole stack: main + FX + curves across the
note, layers at their octaves/gains) — `tools/refresh_sound_meta.py`
rewrites it; the drift test fails past 0.15 per band. Every recipe
patch (main and layers) is in the live parity corpus and the hold-seam
sweep; the air layer and Rising Air are noise with `key_track` 0
because a high-passed 65 Hz saw is a click train whose 10 ms frames the
parity metric can't align (190 dB "env" error, inaudible). The tests
measure each technique (1/16 gate at 8.33 Hz / 125 BPM > 10 dB, pump
ducks, mono/band-limited phone keys, the riser's centroid rising).

### Chain presets

**Chain presets** (`fxrack.CHAIN_PRESETS`: Phone Mic, Mono Maker,
Washy, Sidechain Pump 1/4, Tape Lo-fi; the house / garage / techno /
trance packs' eight — Filter Disco, Hard Duck, Crush Bus, Dotted
Ping-Pong, Trance Gate, Tape Wear, Wide Hall, Tape Stop; electro and
industrial's Blown Out and Machine Rust; psych and sixties' Phase Wash,
Tape Haze, Double Track, Rotary Room and Slap Plate; on the FX2 units,
Filter Sweep, Psych Tape, Sixties Spring, Barber Pole and Robot Voice; on
the dynamics units, OTT Squash, Big Room Hall, Ducked Verb, Drum Punch and
Master Glue; hip-hop to future bass's 808 Saturate, Boom Bap Crunch,
Neuro Grind, Growl Squash, Gated Verb, Eighties Space and Future Pump;
lo-fi to phonk's Dusty Tape, Huge Hall, Shimmer Wash, 8-Bit Crush, Hard
Kick Drive and Phonk Crush): built from existing units only
and stored already clean. "+ add chain…" in every rack opens **the
chain browser** (`fragments/fx_rack.html`, a popover a rack):
every chain listed by purpose (`group`: space, drive & colour, dynamics,
motion, vocal, master) or by genre pack (`pack`: `fxrack.CHAIN_PACKS`),
each with its one-line `blurb`
and its units in order ("Reverb → Noise gate"), searchable (every word in
the row's text or group), one click (or Enter on the first found) to add.
A row posts `/fragment/fx/chain` (scope + chain), which **appends** the
whole chain or, if the rack lacks room (`MAX_UNITS` 6), a toast and
nothing. The three keys are data on each preset (`_CHAIN_INFO`, merged
onto the first 43; a chain added later carries them in its own dict);
`chain_browser()` (a Jinja global) draws the rows once, by group, and
`panelfaces.js` moves them under the pack headings for "genre" (kept per
browser: `synthlab.fxChainsBy`) and does the search and the arrow keys. A
chain naming no group or pack the browser knows still shows, under
"Other" — and `test_fx_chains.py` fails until it names them. The opener
is the rack's one command for it ("Add effect chain to <rack>"); the rows
are a list, not commands (the palette's unfiltered list stays short), and
the browser is a thing of its own (`data-ctx`, each row a `data-menu`
named for its chain), so a right click in it lists the chains and the
rack's section menu does not gain 37 rows.
`chain_preset(name)` is a fresh clean copy (recipes reuse them).

### Kits

**Kits** (`drumfactory.DRUM_KITS`: Melodic Techno, Garage Rim, Filter
House, Two-Step, Trance, Just Ice, Nine Inch Wails, Psych Haze, Sixties
Studio, Hip-Hop 808, Drum and Bass, Dubstep, Synthwave, Future Bass,
Lo-Fi, Cinematic, Chiptune, Hardstyle and Phonk — one kit a
style, since the
select marks a kit by its style; the
bank's "genre kit" select, `POST /fragment/drums/kit`, a whole-view
answer because the kit panels move): pattern + swing, the velocity
lane (`vel`: the garage ghost kick is step 7 at 0.35 with nothing else
on that step — `drum_vel` is per step, not per voice), per-voice Drum
Lab params (hat `tone` 4800 = band-passed hats) and the Randomise
style/seed. Baselines "pattern" and "kit"; `active_slot` cleared.
`DRUM_FACTORY` (the 8 bank slots) is separate from the kits.

### Beat styles

**Beat styles** `melodic-techno` / `garage-rim` (`beatgen`; the house /
garage / techno / trance packs add `filter-house`, `filter-house-break`,
`two-step`, `garage-4x4`, `hypnotic-perc`, `trance`, `trance-climax`,
`trance-break`, appended — each style's table keeps its place in the
one rng stream; the other packs' are inserted before `roll`, the eleven
hip-hop to future-bass ones and the ten lo-fi to phonk ones listed in
`drums.md`, "Variations, fills …"), plus `beatgen.STYLE_SWING`: Randomise
in a style listed there also sets `drum_swing` (garage 0.16 of a 16th ≈
a 58 % shuffle; a DAW's "0.55-0.6" is a percentage, not this unit),
styles not listed never touch it. **A style's feel** (`beatgen.STYLE_FEEL`, `dress`, the beat
workflow): what it says per hit and per row beside its cells — ghosts
added with a velocity of their own (`two-step`, `garage-rim`,
`garage-4x4`), a chance drawn for each rim and tom hit
(`hypnotic-perc`, `melodic-techno`'s rim), the rows' own swing (garage
and house: the hats shuffle, the kick and the clap never do), off-16th
hats softened where no kit lane already holds the step down, and the
build style `roll` (last in `STYLES`: a snare roll whose velocity and
tune climb across the pattern, its last half bar ratcheted). `dress`
is applied by Randomise and by `gen/drums.beat`; `generate` and every
style's cells are untouched by it (`drums.md`, "The per-hit model").

### Genre steering

**Two targets follow their packs' sounds**: the String Machine is among
filter house's non-bass sounds and the pluck-lead and the big-room pad
among trance's, and `test_a_measured_profile_is_its_packs_recipes` holds
each target to its pack — filter house's env_length -0.3, centroid 0.6,
movement 0.1, width 0.1; trance's width 1.4, density 1.0, env_length
-0.1. The steered-board ratios quoted below (0.40x, 0.54x) were measured
on earlier targets (filter house's env_length -0.7, centroid 0.7,
movement 0.2, width 0.3; trance's width 1.5, density 1.1, env_length
-0.3) and have not been re-measured.

**Genre steering** (`breed.GENRE_TARGETS`/`GENE_BIAS`/`lean`,
`genome.STEERS`): `melodic_techno` and `garage` — and the packs'
`filter_house` and `trance` — score minus a weighted
distance to a target profile on the standardised probe vector (techno:
wide, dense, moving, a little bright/long; garage: dark, narrow, clean,
sub-weighted, short). They also *lean* the litter — `generation(...,
bias=direction)` re-draws 1-2 genre genes of a mutated child with p 0.6
— which only a direction in `GENE_BIAS` does, so the five tonal
directions (and None) draw nothing and seeded litters are unchanged
(pinned). Layered/deep: role/FX/op biases (techno pad+air, delay/reverb/
width, routes, rising cutoff curve; garage sub, compress/lowpass, no
width/shimmer/granular). `tests/test_genre_steer.py`.

**Every Start-a-song style has its direction**: `electro` (Just Ice: noisy,
moving, a little bright and short), `industrial` (Nine Inch Wails:
narrow, thin, a little bright, steady), `psych` (Psych Haze: the low
mids, sparse, short, slow to speak) and `sixties` (Sixties Studio:
short, even, mono, a little bright) — each profile measured from its
pack's non-bass recipes like `filter_house`'s, with the sub bands left
out (every pack's non-bass sounds sit under the library there, so they
tell no style from another). Industrial's spikes (crest) and lengths
vary too much across its pack to steer by: a profile with them sat
nearer the other packs' sounds than its own. Their deep / layered
steers: electro drive, crush, phaser and compression over a sub;
industrial grit on grit (fold, crush, ring mod, comb), dry and narrow;
psych phaser, flanger, tape echo and a room around a pad; sixties a
chorus, a slap echo, a plate and a little drive, narrow.

### Style packs

**Style packs** (`tests/studio/test_style_packs.py` holds the measured
table). Four styles, generic
names: **filter house** (~122 BPM, recipe tag `house`), **emotive
garage** (~132, on top of the garage recipes), **melodic techno** (~125,
on top of the techno recipes) and **trance** (~138). A pack is nothing but entries in the
tables above — no new mechanism, no audio file, no Patch field — so
everything this section says about recipes, kits, styles, chains and
directions holds for it. Styles, not songs: each is built from tempo,
drum grammar, sound design and mix moves; nothing recreates a track.

- **Recipes** (17). Filter house: *Chopped Stab Loop* (a square / saw
  stab through a snappy ladder, standing in for a chopped loop: a
  resonant `lowpass` unit left open **to be swept by a song lane**, a
  0.2 Hz phaser, a pump at 0.8), *Slide Funk Bass* (saw + sub, 50 ms
  glide, pump 0.85), *Talkbox Carrier* (one dry, driven saw + square
  voice in tune with itself — any detune is a slow beat, and a detuned
  one fails the hold-seam sweep — for the vocoder's "this sound"
  carrier), *Vowel Lead* (the vowel filter with a `formant_vowel` curve
  across the phrase), *Phaser String Pad*. Emotive garage: *Felt Keys*
  (**2-op FM, said so in its `how`: not a piano** — ratio 1, a low
  fast-decaying index, a 12 ms attack, shut at 1.8 kHz, a sine layer an
  octave under, on Tape Lo-fi), *Vox Pluck* (a short saw through the
  vowel filter; the vowel curve runs over the *phrase*, so each note
  lands on another vowel — `formant_vowel` is not a mod target, there
  is no per-note vowel), *Ducked Slide Sub*, *Chop Bed Pad* (shut at
  900 Hz so 1-4 kHz stays empty for chops). Melodic techno: *Evolving
  Pluck* (pluck + saw, dotted ping-pong, a 4 s hall), *Wide Unison Stab*
  (supersaw x5, width 1.8). Trance: *Rolling Mid Bass* and *Rolling Sub*
  (below), *Anthem Supersaw* (x7, 24 cents, spread 1, five more an
  octave up as an `air` layer), *Stack Pad* (a saw an octave over, a
  triangle layer an octave under, a 4.5 s hall), *Gated Pad* (the
  trance gate at depth 1 — the unit whose depth takes a song lane),
  *Dotted Pluck*.
- **Pumped, not sidechained.** Every ducked recipe uses `pump` (the
  clock), never `sidechain` (the kit's kick): a recipe has to work on
  Synths, where no drums render, and through a bar where the kick drops
  out. Against a four-on-the-floor kit the two are the same duck to
  1e-9 (pinned). A song that wants the kick's own duck uses the *Hard
  Duck* chain. **A pumped recipe's card note starts on the beat, at its
  most ducked**: its card level reads up to 10 dB under what it peaks at
  (the stab loop: -11.0 dBFS on the card, -1.5 with the pump out).
- **DC and sub-sonic housekeeping.** A short filtered saw
  leaves a thump under its note (9-14 % of a pluck's energy under
  20 Hz without a high-pass) and a driven saw + square is lopsided (a
  10 % offset): the two
  plucks carry a `highpass` at 150 Hz, the funk bass one at 25 Hz, the
  carrier at 60 Hz, the mid bass at 80 Hz (which also keeps it out of
  the sub's octave). Every pack recipe: |mean| / peak < 0.005, under 1 %
  of its energy below 20 Hz, peak <= 0 dBFS with its pump taken out.
- **The rolling bass** is two sounds played on one figure — steps 1, 2
  and 3 of every beat, never step 0, a cut before the second and the
  third — the mid bass around F#2 and the sub an octave under (**the
  track's register, not the patch's**: both patches sit at octave 0).
  What keeps three sixteenths three notes is the release (70 ms / 90 ms
  against a 109 ms sixteenth at 138 bpm; a gated note rings for the
  patch's release). Measured against the Trance kit: the kick's step is
  22 dB (mid) and 20 dB (sub) under the three after it — 12 dB of that
  is the figure, the rest the duck — and between two notes the mid bass
  falls 16-18 dB, the sub 6-8 (a 46 Hz sine is five cycles a sixteenth).
- **Kits and styles: the cells.** In a kit's cells a step hits or
  it does not; a velocity is the *step's* (so a ghost in the cells is
  alone on its step — Two-Step keeps a rim, a snare and a rim alone on
  steps 3, 7 and 15 at 0.3-0.35); the kit's swing moves the odd 16ths
  only (so a kick and a clap on even steps stay straight). *Trance*: kick on every beat, clap + a short snare stacked
  on 2 and 4, an open hat on every offbeat eighth choked by the closed
  hat a 16th later, the crash cut to 0.5 s as a ride that
  `trance-climax` plays in eighths. *Filter House*: the same floor with
  a shaker-like hat on every 16th (off-16ths at 0.5: the offbeats are
  the accent), swing 0.04. *Two-Step*: kick on 1 and the "and" of 3,
  never on 2 or 4, swing 0.18. A style always writes a kick
  (`beatgen._bar`), so `filter-house-break` keeps one on the downbeat:
  a bar with no kick is an arrangement's doing. Ghost velocities inside
  a style, a chance on each `hypnotic-perc` hit, a swing per row and the
  build's roll are the style's feel, per hit ("Beat styles" above;
  `drums.md`, "The per-hit model").
- **Chains.** *Filter Disco* (lowpass q 2.5 + phaser 0.2 Hz + pump),
  *Hard Duck* (`sidechain` 0.9 / 0.30: the kit's kick, so nothing on
  Synths), *Crush Bus* (bitcrush 12 / 2, compress -24 / 8, drive 1.6),
  *Dotted Ping-Pong*, *Trance Gate* (the gate into a 3 s room), *Tape
  Wear* (vinyl with more wow than crackle, lowpass 7 kHz, a slow
  compressor — no bits lost), *Wide Hall* (highpass 250, a 6 s room,
  width 1.6), *Tape Stop* (the `stop` unit on the last beat).
- **Gate presets** (`fxrack.GATE_PATTERNS`, `gate_pattern(name)`): eight
  named rhythms as data — eighths, onbeat, offbeat, trance, gallop,
  rolling, dotted, two_bar. A preset is a string the pattern field could
  hold (one character a 16th, at most 32); `POST
  /fragment/fx/set` takes `name=preset` on a gate and writes the string
  (the name is not kept; an unknown name is a 422 and nothing changes).
  **The rack's step editor** (`fx_rack.html`) lists
  them, and draws any pattern unit's string (the gate's, the beat
  repeat's) as `.check`s, one a 16th: a tick posts `name=step` (that
  character toggled), "16" / "32" post `name=steps` (the pattern repeated
  or cut to that length). It is the same string the text field holds —
  no DSP, key or saved byte of its own — and the rack morphs, so the
  check under the keyboard keeps its place. A gate cannot open and shut
  inside one 16th, so there is no "straight 16ths" — `eighths` is the
  fastest even chop.
- **Directions.** `filter_house` and `trance` are registered like
  melodic techno and garage (`GENRE_TARGETS`, `GENE_BIAS`, `STEERS`), with one
  difference: **their target profiles are measured, not written down** —
  the mean of each measure over the pack's recipe main patches that are
  not basses, on the breeder's probe, to a tenth — and
  `test_a_measured_profile_is_its_packs_recipes` re-measures them, so a
  recipe edit that moves the style fails the test until the target
  follows. Filter house came out short, a little bright and busy,
  nearly mono (its movement is FX and lanes, which a patch probe does
  not hear); trance wide, dense, bright and moving. `GENE_BIAS` ranges
  sit inside the *gene* ranges, so trance leans unison to 4-5 (a hand
  can take a patch to 16; the breeder draws to 5). Measured, seeded:
  the steered single-depth board lands at 0.40x (filter house) and
  0.54x (trance) of the unsteered board's distance to the profile.
  The Breed page's direction buttons are a hard-coded list in
  `views/breed.html`; both are in it.
- **The Voice preset.** `voice.PRESETS["talkbox"]` (`preset_settings`):
  carrier "this sound", 24 bands, shift +2, followers at 30 ms, and the
  name of the recipe made to be its carrier. `POST /voice/preset`
  (`name`; the "Preset" row at the head of the vocoder's form) lays the
  settings over the form's and loads that recipe on Synths the way Use
  loads a sound — one Ctrl+Z there brings the previous sound back. It needs
  no take.
- **Live keys.** Fifteen of the seventeen play in the AudioWorklet with
  their chains; *Felt Keys* and *Chop Bed Pad* hold a `vinyl` unit
  (offline) and fall back to the dry key WAV.
- **What none of this says**: whether a pack sounds like its style.
  Every number above is a level, a band, a step or a distance.

### Style packs: electro and industrial

**Nod names, two packs, the clipper's packs.** Four style labels
are nods (the root `docs/engine.md`): *Daft Rock* (filter house), *Fred
Before* (emotive garage), and two styles of their own, *Just Ice* (`electro`, French electro, 120 bpm) and *Nine Inch Wails*
(`industrial`, 105 bpm) — ids, recipes and chains stay generic, each
blurb starts with the genre, and nothing recreates a track.

- **Recipes** (12, appended after the house / garage / techno / trance
  packs' 17). Just Ice: *Crushed Saw Bass*
  (hard drive, crushed, clipped, pumped), *Gated Disco Stab*, *Cathedral
  Choir Pad* (the vowel filter on an open "o": choir-like, not a choir),
  *Blown Lead* (tube drive), *Slap Pluck Bass* (a plucked string over a
  sub), *Siren Riser* (bent up an octave across the phrase: the siren
  track into the last drop). Nine Inch Wails: *Grinding Bass* (folded,
  crushed, hard drive), *Struck Metal* (FM at ratio 3.41 into a comb),
  *Static Drone*, *Ghost Keys* (soft FM on Tape Wear: the quiet score —
  the live keys play its dry WAV, as Felt Keys'), *Fuzz Lead* (fuzz
  drive), *Machine Pulse* (a crushed blip echoed side to side).
- **The clipper is the ceiling** (`clip`, live). A driven, lopsided
  wave clipped keeps an offset, and a high-pass after a hard clip
  overshoots by up to 6 dB: each dirty recipe is drive → clipper at
  -7 dB → high-pass → a soft clipper at -1 dB. That holds every rule
  of the table above (peak, |mean| / peak < 0.005, under 1 % below
  20 Hz) — a compressor in its place is whole-buffer, and would keep
  the live keys off three of them.
- **Kits** *Just Ice* (a hard, clicky, fully driven kick, a snapping
  snare and a clap stacked on it, crunchy hats, a pushed kick before the
  bar line at 0.6) and *Nine Inch Wails* (a tight distorted low kick, a
  cracking noisy snare, the tom high and short as struck metal, the rim
  a clank). **Beat styles** `electro`, `electro-stomp` (half time: the
  kick on 1 and the "and" of 2, one snare + clap on 3), `electro-break`,
  `industrial` (kick on 1, the "a" of 1 and 3, metal on a Euclidean 5),
  `industrial-march`, `industrial-pulse` — inserted before `roll`, the
  other styles' tables and stream untouched.
- **Chains** *Blown Out* and *Machine Rust* (the recipes' endings as
  "+ add chain…" entries). **Signatures** (`gen/checklist.py`
  `_electro`, `_industrial`): what makes a song the style — the stomp,
  the vowel-filter pad, a cut before a drop, the squashed drums; quiet
  against loud, a section with no drums at all, struck metal, a driven
  lead, crushed drums.
- **What none of this says**: whether either sounds like its style.
  Nobody has listened.

### Style packs: psych and sixties

**Two packs, the booth's units in them.** *Psych Haze* (`psych`,
psychedelic pop / neo-psych, 112 bpm, modern psych-rock records the
direction) and *Sixties Studio* (`sixties`, the 60s studio, 116 bpm).
The labels are nods; ids, recipes, kits' sounds and chains stay
generic, each blurb starts with the genre, nothing recreates a track.
Built as the electro and industrial packs are — recipes, a kit, beat
styles, chains, a Start-a-song style and its signature — from existing
engine params and FX units only, the booth's six (ADT `doubler`,
`rotary`, `tape_echo`, `plate`) among them. The breed direction is
measured like the others' ("Genre steering" above).

- **Recipes** (12, appended after electro and industrial's). Psych Haze: *Chorus Fuzz Bass*
  (saw + square + sub, a tube drive for the tape's warmth, chorused and
  slowly phased), *Phased Fuzz Lead* (gliding, a little vibrato, fuzz,
  flanged, a wavering tape echo on the dotted eighth), *Haze Pad*
  (phased, chorused, a tape echo and a 5 s room), *Wobble Keys* (2-op FM
  with a tremolo — not an electric piano — chorused into a saturated
  tape echo), *Phase Arp*, *Tape Swell* (its low-pass curve opens across
  the phrase). Sixties Studio: *Rotary Organ* (the note, its octave and
  the one under as sines through the rotary speaker and a plate), *Tape
  Strings* and *Tape Flute* (as a tape-replay keyboard played them: the
  pitch wavering with the tape, a plate; the flute breathy, with a
  slapback — "mellotron-ish", never named), *Round Bass* (short and
  round, a little valve warmth), *Double Track Lead* (ADT then a
  slapback and a plate), *Backwards Cymbal* (high noise swelling up and
  stopping dead). All twelve play in the worklet (no offline unit), and
  hold the table's rules (peak, |mean| / peak < 0.005, under 1 % below
  20 Hz, where the role sits). Four use the FX2 units
  (`docs/live-voice.md`, "The FX2 units") — *Haze Pad* a slow auto-pan (a
  `tremolo` at 180°, two bars), *Tape Swell*, *Tape Strings* and *Tape
  Flute* the `tape` unit (wow, flutter, a little hiss; on the strings in
  place of a low-pass rolling the top off); the tape rounds the peaks
  (the swell's sits 6 dB under its untaped peak, its RMS 1.6). Two
  housekeeping notes: a tape echo's
  saturation leaves an offset on a lopsided wave, so *Double Track Lead*
  ends in its high-pass; and a starter's synth track holds six units, so
  the bass recipes leave room for the sweep's low-pass and the duck.
  Measured (one note of the whole recipe at its card pitch and tempo,
  `recipes.stack_audio`; dBFS; "<120" the share under 120 Hz; "hold" the
  dry patch 0.6-0.9 s into a held note against its loudest; none pumps):

  | recipe | role | peak | rms | <120 | hold | width |
  |---|---|---|---|---|---|---|
  | Chorus Fuzz Bass | bass | -6.3 | -18.1 | 0.75 | -2.4 | 0.06 |
  | Phased Fuzz Lead | lead | -1.9 | -10.3 | 0.00 | -0.9 | 0.27 |
  | Haze Pad | pad | -11.2 | -26.4 | 0.00 | -1.6 | 0.66 |
  | Wobble Keys | keys | -8.6 | -22.1 | 0.00 | -6.6 | 0.05 |
  | Phase Arp | keys | -3.3 | -28.0 | 0.00 | gone | 0.29 |
  | Tape Swell | fx | -9.1 | -22.5 | 0.00 | -2.3 | 0.56 |
  | Rotary Organ | keys | -0.4 | -9.4 | 0.00 | -0.1 | 0.49 |
  | Tape Strings | pad | -9.7 | -23.5 | 0.00 | -4.1 | 0.27 |
  | Tape Flute | lead | -8.9 | -16.1 | 0.00 | -0.8 | 0.04 |
  | Round Bass | bass | -2.3 | -15.5 | 0.84 | -6.1 | 0.00 |
  | Double Track Lead | lead | -0.2 | -12.8 | 0.00 | -1.1 | 1.23 |
  | Backwards Cymbal | fx | -4.3 | -19.7 | 0.00 | -2.2 | 0.03 |
- **Kits** *Psych Haze* (a round, low kick, a loose ringing snare, two
  ghost snares alone on steps 7 and 15 at 0.3, warm eighth hats, a low
  long tom, swing 0.12) and *Sixties Studio* (close-miked and dead: a
  0.22 s kick, a 0.12 s snare, plain eighth hats, a short bright open hat
  on 2 and 4 where the tambourine sits, a floor tom, a cross-stick;
  straight). Their snares snap at 0.7 so `analysis/drumhits` reads them
  at the clean kits' bar (`tests/test_drumhits.py`). **Beat styles**
  `psych` (the kick on 1 and the "and" of 2 and 3, ghost snares, swung
  hats — the kick and the snare never swing), `psych-disco` (four on the
  floor, open hats on the offbeats), `psych-haze` (half time: one snare
  on 3, toms on a Euclidean 5 by chance), `sixties` (kick on 1 and 3,
  snare on 2 and 4, straight eighths), `sixties-toms` (a floor tom's
  rolling figure under them), `sixties-break` (a cross-stick on the
  backbeat) — inserted before `roll`.
- **Chains** *Phase Wash* (phaser, a dotted tape echo, a 5 s room),
  *Tape Haze* (chorus, flanger, a tube drive and the high-pass its offset
  needs), *Double Track* (ADT and a plate), *Rotary Room* (the rotary and
  a plate), *Slap Plate* (a 110 ms tape slap into a plate). And, on
  the FX2 units: *Filter Sweep* (filter
  house: an auto-filter opening over two bars, the pump), *Psych Tape*
  (a pushed, wavering tape, then a quarter-note auto-pan), *Sixties
  Spring* (a gentle tape into a dripping spring), *Barber Pole* (a 3 Hz
  shift fed back, a dotted ping-pong), *Robot Voice* (the rack's vocoder
  on the song's key, an exciter).
- **Start-a-song styles** (`gen/styles.py` `psych`, `sixties`; 128 bars
  each). Psych: a loose verse, a pre-chorus whose swell and a reversed
  cymbal (the `swell` take) lead into a disco-psych chorus, a half-time
  haze where the lead enters late, the drums on a phaser and a squash.
  Sixties: verses on the cross-stick, choruses on the plain beat with tom
  fills, a tom-loop bridge ending in the backwards cymbal, a swell into
  each last chorus; no reverb on the drums. Both tuck the bass under the
  kick with a light `sidechain` (0.35: a bus compressor's hold, not a
  pump), which the checklist asks of a bass on the kick's steps.
  **Signatures** (`gen/checklist.py` `_psych`, `_sixties`; 8 and 10
  checks): swung drums, a phaser or flanger on the bass and a lead, a
  chorused bass, a tape echo or synced delay, a long room, phased drums,
  a half-time haze; dry drums, the kick on 1 and 3 in the verses and
  choruses, a tom loop, a rotary, a double-tracked part, a slapback, a
  plate, a pad wavering with the tape, a backwards cymbal.
- **What none of this says**: whether either sounds like its style.
  Nobody has listened.

### Style packs: garage, techno, house, electro, trance

**The sounds and effects common to five styles' records** — emotive
garage, melodic techno, filter house / disco, French electro, trance:
rack units, recipes, chains, and the five styles' starters playing them.
Styles, never songs: ids, recipe
and chain names stay generic, each blurb starts with the genre, nothing
recreates a track. Two more labels are nods, in the spirit of *Daft Rock*
and *Fred Before*: **Anymore** (melodic techno) and **Arm In Arm**
(trance) — the label is one string in `gen/styles.py`; the kits keep
their names (*Melodic Techno*, *Trance*: a kit's name is its key in
`drumfactory.DRUM_KITS`, which sessions store — renaming one is more than
a string), and so do the Breed page's direction buttons (genres, not
labels).

- **Units** (`docs/live-voice.md`, "The dynamics units"): `ott` (three
  bands, upward and downward, depth the parallel blend), `limiter` (the
  rack's face of a lookahead limiter: its lookahead a delay, the same
  latency live and in the loop) and `transient` (attack / sustain from
  peak followers). The envelope filter is the auto-filter's `env`, with
  a closing preset, *Envelope down*, beside *Envelope funk*, and no
  second unit.
- **Recipes** (13, appended after psych and sixties'). Melodic techno: *Reese Bass*
  (two saws 18 cents apart phasing over a sub, a slow sine on a driven
  ladder, pumped), *Dark Arp Pluck* (a short FM ping over a saw, a dotted
  echo side to side), *Cinematic Choir Lead* (the vowel filter on saws, a
  vowel curve "a" -> "o" across the phrase, a late vibrato, a glide, a
  5.5 s hall — a line that sings, which the gated choir pad does not).
  Trance: *Trance Pluck Lead* (supersaws and a square an octave up behind
  a low-pass that snaps open on every note and falls back to a quarter: the
  pluck layered under the big supersaw), *Big Room Pad* (on the Big Room
  Hall chain). Filter house / disco: *String Machine* (a saw and a slowly
  pulsing square into an ensemble — a slow deep chorus over a fast
  shallow one, three voices each), *Envelope Funk Bass* (through the
  auto-filter on its follower alone: a resonant low-pass each note opens
  as hard as it hits, pumped). Emotive garage: *Felt Piano Keys* (a
  damped plucked string, two copies 3 cents apart as a piano's unisons
  beat, over a sine for the body, a closing low-pass, warm tape, a small
  room — **its `how` says what it is not**: no recording, no hammer noise,
  no sympathetic strings, no pedal; *Felt Keys* is the FM one),
  *Squashed Vocal Pad* (the vowel filter drifting "ah" -> "eh", the OTT
  at 0.6 lifting its breaths and tails). **Drop FX** (tag `drops`, every
  style's, measured at 128 bpm): *Impact* (a sine falling two octaves onto
  its note in 0.1 s, a breath of pink noise, a bright crash layer, a big
  room, the clipper's ceiling), *Sub Drop* (a sine falling an octave onto
  its note and, by its bend curve, another across the phrase; mono, under
  400 Hz), *Noise Downlifter* (Rising Air's mirror: the band-pass falls
  across the phrase, into the limiter, which brings the quiet band-passed
  noise up to a usable level), *Reverse Crash* — **the Backwards Cymbal
  is a reverse crash too**, so this is the swelling-into-a-drop
  variant: a 2.5 s swell (the cymbal's 1.2), wider, its ringing high-pass
  climbing across the phrase, stopping dead. All thirteen play in the
  worklet (no offline unit), and hold the table's rules (peak, |mean| /
  peak < 0.005, under 1 % below 20 Hz, where the role sits; the sub drop
  is held to the subs' rule — mono, over 0.9 under 120 Hz — and the
  impact to its own: a boom with a crash over it, 0.5-0.97 under 120 Hz).
  Measured as psych and sixties' (one note of the whole recipe at its card
  pitch and tempo; dBFS; "free" the peak with the pump out; width
  `extract`'s):

  | recipe | role | peak | rms | free | <120 | hold | width |
  |---|---|---|---|---|---|---|---|
  | Reese Bass | bass | -6.0 | -18.4 | -0.9 | 0.70 | -5.7 | 0.00 |
  | Dark Arp Pluck | lead | -3.6 | -28.1 | | 0.00 | gone | 0.34 |
  | Cinematic Choir Lead | lead | -5.7 | -20.9 | | 0.00 | -9.0 | 0.38 |
  | Trance Pluck Lead | lead | -6.0 | -23.2 | | 0.00 | -10.8 | 0.74 |
  | Big Room Pad | pad | -4.4 | -20.2 | | 0.00 | -2.9 | 1.30 |
  | String Machine | pad | -8.0 | -22.9 | | 0.00 | -1.5 | 0.40 |
  | Envelope Funk Bass | bass | -2.6 | -16.5 | -2.6 | 0.39 | -1.4 | 0.00 |
  | Felt Piano Keys | keys | -9.0 | -29.5 | | 0.00 | -16.5 | 0.10 |
  | Squashed Vocal Pad | pad | -8.0 | -21.4 | | 0.00 | -3.3 | 0.58 |
  | Impact | fx | -3.8 | -19.6 | | 0.91 | -11.4 | 0.08 |
  | Sub Drop | fx | -3.5 | -16.3 | | 0.97 | -5.3 | 0.00 |
  | Noise Downlifter | fx | -7.1 | -24.7 | | 0.00 | -3.1 | 0.12 |
  | Reverse Crash | fx | -3.7 | -18.8 | | 0.00 | -2.2 | 0.07 |
- **Chains** *OTT Squash* (the OTT at 0.6, -2 dB out: a -6 / -46 dBFS
  pair of passages comes out 24.4 dB apart), *Big Room Hall* (high-pass
  220, a 6 s room, the EQ's top +4 and bottom -3, width 1.8), *Ducked
  Verb* (high-pass 150, a 4.5 s room at 0.6, the pump at 0.85: the room
  swells back between the beats), *Drum Punch* (the transient shaper at
  +0.6 / -0.2, a 4:1 compressor, the clipper at -1 dB), *Master Glue* (a
  gentle EQ tilt, the OTT at 0.2 and slow, the limiter 3 dB into -1 dBFS).
- **Starters** (`gen/styles.py`; `songcraft.md`, "Styles"): melodic
  techno's mid bass is the Reese, its arp the dark pluck, its breakdown
  lead the choir lead, and its drops' impact is a track of its own
  (`notes.hit`: one note on the first beat; role `fx`), not a take (the
  sub drop is a take: a starter keeps its eighth synth
  track free, or the checklist's "Add a bass" has no room); trance's pad
  is the big-room pad, its pluck the pluck-lead, a sub drop (a take)
  lands under the climax and its drums run through Drum Punch's shaper
  (the punchier kick: the drum-side gap, cheap); filter
  house's bass is the envelope-filter funk bass and its strings the
  string machine; garage's keys are the felt piano keys and a squashed
  vocal pad joins the breakdown and the last drop; electro's drums take
  the shaper in front of their squash. **Signatures** (`gen/checklist.py`):
  six checks — a sub drop under trance's climax and under a melodic
  techno drop, the punched drums (trance, electro), filter house's
  envelope filter on the bass, garage's OTT pad — and melodic techno's
  impact check reads an impact track as well as a take. A drop FX is
  read by its sound's grammar, never its name.
- **No breed direction or kit of its own.** Two measured direction
  targets follow these sounds (above, "Genre steering").
- **What none of this says**: whether any of it sounds like its style.
  Nobody has listened.

### Phase warp, custom wavetables, voice and glide modes

Seven Patch fields, all off at their defaults (`render.KEY_ADDED_FIELDS`)
and **none a gene**: `osc1_warp` / `osc2_warp` and their amounts, `wt_custom`,
`voice_mode`, `glide_mode` (`Param.nogene` says why: a random warp on
every bred oscillator is rarely a sound, a bred voice mode changes how a
sound plays, and new genes would re-draw every seeded litter). The warp
amounts are CURVABLE and route targets outside the genes' destinations
(`MOD_GENE_DESTINATIONS`, as `sync_pitch`); `wt_custom` is a "meta" string
`META_CLEAN` keeps to an id. The agent reads them from the registry.

In the editor, as the oscillator panels: **each oscillator's warp** one
block in its area (`warp-block`, as the sync block) — the mode a select
(`osc*_warp`: off, bend +, bend −, bend ±, PD, mirror, asym, quantize,
pwm; the deck and MIDI learn step it), the amount a knob. **Your own
wavetables** are radios of the picker's own group after the twelve
built-ins (`oscfaces.own_faces`: up to eight of a table's frames drawn
like a built-in's, its name in the accent's ink; the value `own:<id>`,
which `forms.patch_from_form` turns into `wt_custom` — picking a built-in
lets it go), so a deck dial or a learned CC steps through them too. Under
the picker, **+ from clip…** (a popover: a clip or a sample, 4-64
frames, slices or pitch cycles, a name — `POST /fragment/wavetable/clip`)
and **draw…** (four frames of 64 points drawn with the pointer or the
pen, sine / triangle / saw / square / flat fills, how many make the
table — `static/wtdraw.js`, `POST /fragment/wavetable/draw`); both are
one undo step, the new table picked and a wavetable oscillator switched
on if none was. **Voices** and **Glide when** two pill rows under
Character's knob line.

Four demo factory sounds (`factory.py`, 26): *PD Resonant Sine* (one sine,
PD swept by the filter envelope through a route), *Bent Sine Bass* (bend +
over an octave-down sine), *Mirror Lead* (an LFO walking a saw's mirror,
a pwm-warped square), *Legato Glide Lead* (legato, glide only over a held
key).

### Style packs: hip-hop, drum & bass, dubstep, synthwave, future bass

**Five more genres.** **hip-hop / trap** (`hiphop`, 140 half time), **drum & bass** (`dnb`, 174),
**dubstep / bass music** (`dubstep`, 140 half time), **synthwave / 80s**
(`synthwave`, 100) and **future bass** (`future_bass`, 150 half time;
recipe tag `future`). Plain genre labels; ids, recipes, kits and chains
generic, each blurb starts with the genre, nothing recreates a track, no
audio file. Built as the packs above are, from existing units and Patch
fields only. Lo-fi, ambient / cinematic, chiptune, hardstyle and phonk
are the next section's, which also rebuilds three of these sounds on hard
sync, a 4-op FM stack and drift ("Revisited").

- **Recipes** (22, appended after the garage-to-trance top-up's). Hip-hop / trap: *808 Long* (a
  sine falling a fifth onto its note in 60 ms, a soft drive, a 1.8 s
  decay settling at 0.45, a 90 ms glide — the defining sound; mono, over
  0.9 under 120 Hz, held to the subs' rule), *808 Distorted* (the same
  with a square under it through the *808 Saturate* chain), *Dark Bell
  Keys* (2-op FM at ratio 3.5, sustain 0), *Dark Choir Pad* (the vowel
  filter on a dark "o"), *Airy Flute Lead* (a triangle and a breath of
  pink noise, a late vibrato: "flute-ish, not a flute"). Drum & bass:
  *Neuro Reese* (the techno *Reese Bass* further apart — 22 cents — over a
  sub, swept on the quarter, through *Neuro Grind*), *Rolling DnB Sub*,
  *Atmos Pad*, *Liquid Stab*. Dubstep: *Wobble Bass* (a resonant ladder
  whose filter LFO is synced to the eighth; the same patch at the
  eighth triplet and the quarter wobbles at those rates —
  `test_the_wobble_moves_on_the_clock_at_any_sync_division`),
  *Growl Bass* (2-op FM with the modulator an octave down, a saw a square
  LFO steps in and out on the quarter, the vowel filter, a resonant
  low-pass swept on the sixteenth, *Growl Squash*), *Half-Time Sub*,
  *Screech Lead* (a saw cross-modulated by a square an octave up, a
  resonant band-pass on the sixteenth, a fast vibrato, fuzz, the OTT).
  Synthwave: *Poly Chorus Pad* (two choruses, as an 80s polysynth's own),
  *Poly Brass Stab* (the filter swelling open 40 ms after the note),
  *Eighties Arp Bass*, *Bright Saw Lead* (on *Eighties Space*), *Lush
  Strings* (the String Machine's ensemble, a slower swell, a longer,
  wider room). Future bass: *Supersaw Chord Stab* (seven supersaws on
  *Future Pump*), *Vocal Chop Lead* (a small bright vowel filter, each
  note scooped onto its pitch, the vowel moving across the phrase),
  *Bent Pluck* (bent up into each note, wobbling a third of a semitone on
  the sixteenth by a route to `pitch`), *Future Pad*. All 22 play in the
  worklet (no offline unit) and hold the table's rules.
  **Housekeeping**: a resonant ladder swept to its floor rings under
  20 Hz (a fifth of the wobble's energy, swept that low): its sweep runs
  200 Hz-2.8 kHz and it has a high-pass on each side of a -7 dB clipper;
  the supersaws are voiced to stay under the hold-seam bar on a held C4
  (Atmos Pad seven voices, the chord stab 20 / 9 cents); the brass's
  levels keep its peak under 0 dBFS. Measured as the garage-to-trance
  top-up's (dBFS; "free" the peak with the pump out; the wobble's hold is
  the dry patch's, its LFO moving):

  | recipe | role | note | peak | rms | free | <120 | hold | width |
  |---|---|---|---|---|---|---|---|---|
  | 808 Long | bass (sub) | A1 | -2.7 | -9.0 | | 1.00 | -2.6 | 0.00 |
  | 808 Distorted | bass | A1 | -2.4 | -8.0 | | 0.85 | -0.8 | 0.00 |
  | Dark Bell Keys | keys | C4 | -5.7 | -22.3 | | 0.00 | -10.8 | 0.18 |
  | Dark Choir Pad | pad | C3 | -8.9 | -24.6 | | 0.00 | -4.2 | 0.67 |
  | Airy Flute Lead | lead | C5 | -8.1 | -15.4 | | 0.00 | -1.0 | 0.05 |
  | Neuro Reese | bass | A1 | -2.1 | -12.5 | | 0.64 | -3.1 | 0.00 |
  | Rolling DnB Sub | bass (sub) | A1 | -1.7 | -6.3 | | 1.00 | -0.6 | 0.00 |
  | Atmos Pad | pad | C3 | -10.3 | -25.8 | | 0.00 | -3.8 | 1.09 |
  | Liquid Stab | keys | C4 | -9.2 | -27.2 | | 0.00 | -7.7 | 0.60 |
  | Wobble Bass | bass | A1 | -2.1 | -12.6 | | 0.65 | -1.4 | 0.00 |
  | Growl Bass | bass | A1 | -2.1 | -13.0 | | 0.34 | -1.5 | 0.00 |
  | Half-Time Sub | bass (sub) | A1 | -1.7 | -6.3 | | 1.00 | -0.4 | 0.00 |
  | Screech Lead | lead | C4 | -4.9 | -17.0 | | 0.00 | -2.0 | 0.00 |
  | Poly Chorus Pad | pad | C3 | -7.3 | -23.2 | | 0.00 | -1.1 | 0.29 |
  | Poly Brass Stab | keys | C4 | -1.3 | -20.0 | | 0.00 | -10.3 | 0.56 |
  | Eighties Arp Bass | bass | A1 | -4.2 | -18.1 | | 0.51 | -4.1 | 0.00 |
  | Bright Saw Lead | lead | C4 | -6.4 | -22.7 | | 0.00 | -6.8 | 0.95 |
  | Lush Strings | pad | C3 | -7.8 | -23.4 | | 0.00 | -1.0 | 0.68 |
  | Supersaw Chord Stab | keys | C4 | -4.5 | -20.1 | -0.6 | 0.00 | -7.5 | 0.96 |
  | Vocal Chop Lead | lead | C4 | -11.5 | -23.3 | | 0.00 | -8.5 | 0.16 |
  | Bent Pluck | lead | C4 | -8.3 | -30.8 | | 0.00 | gone | 0.29 |
  | Future Pad | pad | C3 | -10.6 | -25.7 | -10.1 | 0.00 | -5.3 | 0.74 |
- **Kits** *Hip-Hop 808*, *Drum and Bass*, *Dubstep*, *Synthwave*,
  *Future Bass* and **beat styles** `trap-808`, `boom-bap`, `dnb`,
  `dnb-roller`, `jungle`, `dubstep`, `riddim`, `synthwave`, `synthpop`,
  `future-bass`, `future-build` (`drums.md`, "Variations, fills …": the
  grammar of each, and the two feel keys `rolls` and `ratchet` —
  trap's hat rolls are per-hit ratchets). Boom bap (~90, swung 0.2) is a
  bank style only: no starter is at its tempo, and no kit is its own.
- **Chains** *808 Saturate* (tube drive -> clip at -7 dB -> high-pass 25
  -> soft clip at -1), *Boom Bap Crunch* (10 bits, a 9 kHz low-pass, the
  shaper's attack +0.4, the clipper), *Neuro Grind* (hard drive, a comb at
  180 Hz, the OTT at 0.7, the clipper's ceiling), *Growl Squash* (fold,
  fuzz, the OTT at 0.8, the ceiling), **Gated Verb** (a 2.5 s room, then
  `vgate` at -24 dB, 20:1, 70 dB deep, 30 ms: a burst at -2 dBFS passes
  whole for 100 ms, is 15 dB down by 200-300 ms and 56 dB down by 300-500
  — the 80s gated snare from two existing units; the gate reads absolute
  level, so a much quieter bus is gated sooner), *Eighties Space* (a
  chorus, a dotted echo side to side, a room), *Future Pump* (the OTT at
  0.7, a room, the pump at 0.9 *after* the room so the room ducks too —
  a room after the pump fills the duck back in to -5.8 dB; width 1.6).
- **Starters** (`songcraft.md`, "Styles"): every one opens with nothing
  missing and passes its signature for five seeds (9 / 9 / 9 / 8 / 10
  checks). Dubstep is the one with seven synth tracks (the wobble twice:
  the eighth for the first drop, the eighth triplet by a track `patch`
  override answering the growl in the second). `tools/song_measure.py`,
  seed 1, cold, on an i9-14900K desktop (x86; build / first section /
  whole plan / peak export, peak RSS):

  | style | bars | length | build | first | plan | export | RSS |
  |---|---|---|---|---|---|---|---|
  | hiphop | 128 | 219 s | 0.035 s | 1.24 s | 2.66 s | 0.20 s | 1361 MB |
  | dnb | 128 | 177 s | 0.030 s | 0.90 s | 1.85 s | 0.16 s | 1206 MB |
  | dubstep | 128 | 219 s | 0.029 s | 0.88 s | 2.25 s | 0.20 s | 1232 MB |
  | synthwave | 96 | 230 s | 0.031 s | 0.75 s | 2.81 s | 0.22 s | 2055 MB |
  | future_bass | 144 | 230 s | 0.029 s | 1.48 s | 3.83 s | 0.22 s | 1311 MB |

  (Trance and sixties measured beside them: 1616 and 1670 MB.)
  **Synthwave's 2.0-2.1 GB is high for a 230 s song** — not the gated
  room (2034 MB without it) and hardly the sixteenth bass (1944 MB in
  eighths): most likely its passes, 16 bars at 100 bpm (38 s each).
  `limits.MAX_SONG_SECONDS` is sized on a 480 s worst case at 2.0 GB.
- **No breed direction** (a direction is a measured target); these
  packs touch no other pack's recipes, so no target moves.
- **What none of this says**: whether any of it sounds like its style.
  Nobody has listened.

### Style packs: lo-fi, ambient, chiptune, hardstyle, phonk

**Five genres on hard sync, the 4-operator `fm4` shape, the wavetables
and analog drift** ("Hard sync, FM4, wavetables and drift" above):
**lo-fi hip-hop**
(`lofi`, 80, swung hard), **ambient / cinematic** (`ambient`, 70, sparse
drums), **chiptune / 8-bit** (`chiptune`, 140), **hardstyle**
(`hardstyle`, 150) and **phonk** (`phonk`, 140, drift phonk / memphis).
Plain genre labels, blurbs starting with the genre, generic ids and
names, nothing recreates a track, no audio file; existing units and
engine fields only. Measured and tested as the hip-hop to future-bass
packs are (the table's rules, the parity corpus, the hold-seam bar).

- **Recipes** (23, appended after the hip-hop to future-bass packs'). Lo-fi: *Dusty Keys* (fm4
  `pairs` as an electric piano builds its sound — a body pair and a 14:1
  tine gone in 0.1 s — a slow tremolo, drift 0.25, on *Dusty Tape*;
  "e-piano-ish, not an electric piano"), *Jazz Chord Pad* (a triangle and
  a quiet saw under a dark low-pass that opens with the swell, drift
  0.45, chorused, on Dusty Tape), *Soft Sub* (a sine with a 15 ms front),
  *Muted Pluck* (the pluck shape damped 0.7: "guitar-ish, not a
  guitar"). Ambient / cinematic: *Scanning Pad* (the `scan` table, a
  curve walking `wt_pos` 0.1 -> 0.8 across the phrase — measured, its
  centroid halfway through 1017 Hz against 709 as it swells in — three
  drifting copies, *Huge Hall*), *Vowel Drift Pad* (the `formant` table
  walked a -> u across the phrase, drift 0.5), *Shimmer Bed* (a triangle
  and a sine an octave up on *Shimmer Wash*), *Glass Bell Texture* (fm4
  `pairs` at 3.5:1 and 7.1:1 on a 2.76 partial, dying over seconds, a
  dotted echo, Huge Hall), *Deep Drone* (two saws and a sub, a low-pass
  breathing once every 15 s, drift 0.7, a 2 s swell), *Cinematic Braam*
  (detuned saws and a square an octave under, a filter blaring open 120
  ms after the hit, driven, a 6 s hall). Chiptune: *Square Lead* (a 50 %
  square, no filter, a late quick vibrato), *Pulse Lead* and *Thin Pulse
  Arp* (the `pulse` table at its 30 % and 15 % frames — the nearest it
  holds to the console's 25 and 12.5 %: the table's frames are 50 / 30 /
  15 / 6 %, and a position between two frames crossfades two pulses, it
  is not a pulse in between; measured on the wave, 0.50 / 0.30 / 0.15 of
  each cycle high), *Triangle Bass* (a triangle crushed to four bits —
  the console's triangle is sixteen steps), *Crush Lead* (the `crush`
  table's held five-level saw on *8-Bit Crush*). Hardstyle: *Sync
  Screech* (a saw hard synced to a saw, `sync_pitch` swept on the
  sixteenth and by the filter envelope — its brightness moves at exactly
  10 Hz at 150 bpm — driven, squashed), *Euphoric Supersaw* (seven
  supersaws and a saw an octave over, a dotted echo, a long room),
  *Reverse Bass* (saws over a sub whose level and filter swell over 140
  ms and are cut, never decay: 14.5 dB up from its first 20 ms to 120 ms
  in; "reverse-bass-ish: a swell, not a reversed kick"), *Euphoric Pad*
  (pumped supersaws). Phonk: *Phonk Cowbell* (fm4 `pair_plus` as a drum
  machine's cowbell is built — two tones 1 : 1.48, within 4 dB of each
  other, the low one roughened a little — through a band-pass, short,
  driven: the defining sound, played as the melody), *Phonk 808* (the
  808's fall and glide with a square under it, drive 4, on *Phonk
  Crush*), *Dark Vowel Pad* (the `formant` table on its "u", drifting,
  crushed to 10 bits), *Hard Phonk Lead* (a square hard synced a fifth
  over a saw, on Phonk Crush). All play in the worklet but the Shimmer
  Bed (the shimmer is a whole-buffer unit: its keys take the dry WAV,
  as the vinyl ones do). Measured as the hip-hop to future-bass packs'
  (dBFS; "free" the peak with the pump out):

  | recipe | role | note | peak | rms | free | <120 | hold | width |
  |---|---|---|---|---|---|---|---|---|
  | Dusty Keys | keys | C4 | -8.6 | -22.7 | | 0.00 | -8.4 | 0.01 |
  | Jazz Chord Pad | pad | C3 | -10.5 | -22.0 | | 0.00 | -0.3 | 0.08 |
  | Soft Sub | bass (sub) | A1 | -2.0 | -7.6 | | 1.00 | -1.6 | 0.00 |
  | Muted Pluck | lead | C4 | -11.4 | -34.2 | | 0.00 | gone | 0.03 |
  | Scanning Pad | pad | C3 | -6.4 | -22.5 | | 0.00 | -2.0 | 1.29 |
  | Vowel Drift Pad | pad | C3 | -1.1 | -16.4 | | 0.00 | -4.1 | 1.30 |
  | Shimmer Bed | pad | C3 | -8.5 | -21.7 | | 0.00 | -2.2 | 0.73 |
  | Glass Bell Texture | keys | C4 | -10.9 | -28.8 | | 0.00 | -8.8 | 1.03 |
  | Deep Drone | bass | A1 | -1.2 | -18.1 | | 0.77 | -2.1 | 0.06 |
  | Cinematic Braam | fx (boom) | C2 | -1.4 | -15.3 | | 0.90 | -4.2 | 0.34 |
  | Square Lead | lead | C5 | -6.6 | -12.2 | | 0.00 | -1.6 | 0.00 |
  | Pulse Lead | lead | C5 | -5.7 | -15.5 | | 0.00 | -2.7 | 0.00 |
  | Thin Pulse Arp | lead | C5 | -7.4 | -36.0 | | 0.05 | gone | 0.00 |
  | Triangle Bass | bass (sub) | A1 | -5.2 | -10.2 | | 0.98 | -2.0 | 0.00 |
  | Crush Lead | lead | C5 | -5.6 | -16.7 | | 0.00 | -3.8 | 0.00 |
  | Sync Screech | lead | C4 | -2.5 | -10.4 | | 0.00 | -0.8 | 0.00 |
  | Euphoric Supersaw | lead | C4 | -8.1 | -19.8 | | 0.00 | -6.4 | 0.67 |
  | Reverse Bass | bass | A1 | -1.9 | -10.7 | | 0.82 | -1.4 | 0.00 |
  | Euphoric Pad | pad | C3 | -7.5 | -24.4 | -6.9 | 0.00 | -3.8 | 0.96 |
  | Phonk Cowbell | lead | C5 | -5.8 | -28.4 | | 0.00 | gone | 0.06 |
  | Phonk 808 | bass | A1 | -2.1 | -8.3 | | 0.85 | -0.6 | 0.00 |
  | Dark Vowel Pad | pad | C3 | -3.4 | -16.7 | | 0.00 | -2.2 | 0.50 |
  | Hard Phonk Lead | lead | C4 | -3.5 | -8.2 | | 0.00 | -1.3 | 0.00 |

  The triangle bass is a sub by the table's rule (a triangle's harmonics
  are weak: 0.98 under 120 Hz at A1, crushed or not) and is held to it
  (mono, held); the braam is a boom (0.90). **Housekeeping**: a clipper
  with nothing after it leaves an offset on the lopsided synced and
  swelling waves (the screech 1.1 %, the reverse bass 3.8 % of its peak
  and 1.2 % of its energy under 20 Hz): both clip at -7, high-pass, then
  the ceiling, as the hip-hop to future-bass dirt chains do — and so does
  Phonk Crush (with no low-pass of its own: six units would leave a bass
  on it no room for its duck); the cowbell's roughening operator is 0.12
  (at 0.25 the low tone sits 10 dB under the high one) and its level keeps
  it over -14 dBFS; the Euphoric Pad's supersaw is five voices at 16 cents,
  the seam 1.3 / 1.8 dB inside the hold-seam bar at C3 / C4 (seven voices
  at 14 cents meet a held C4 1.0 dB over it, and at 16 cents its hold
  sinks to -9.3, under the pads' -9).
- **Revisited** (three hip-hop to future-bass sounds rebuilt on this
  engine; that pack's table above holds the numbers of their 2-op /
  cross-FM / drift-free form, in parentheses here): *Growl Bass* — a 4-op `stack` (an octave-down
  modulator, the snarl on it, operator 4 feeding back at 0.6) in place of
  2-op FM; the stack is brighter, so its sub is 0.7 (0.35): peak -2.7
  (-2.1), rms -13.2 (-13.0), under 120 Hz 0.36 (0.34), under 20 Hz 0.5 %
  (0.6 %), hold -2.5 (-1.5). *Screech Lead* — a square hard synced to the
  saw an octave over it, `sync_pitch` swept on the sixteenth, in place of
  cross-FM: peak -4.3 (-4.9), rms -15.6 (-17.0), hold -1.0 (-2.0).
  *Neuro Reese* — drift 0.3: the same peak and rms, hold -2.4 (-3.1).
  *Poly Brass Stab* has **no** drift: with any, its per-note start phases
  soften the front the swell is heard against (60 ms in, the top over 2
  kHz holds 1.7x the front's at C4, against 2.7x in phase). A recipe is
  its own render input, so a recipe edit needs no `RENDER_REV` bump
  (`recipes_meta.json` is refreshed).
- **Kits** *Lo-Fi*, *Cinematic*, *Chiptune*, *Hardstyle*, *Phonk* and
  **beat styles** `lofi`, `lofi-chill`, `ambient-pulse`,
  `cinematic-toms`, `chiptune`, `chip-chase`, `hardstyle`, `rawstyle`,
  `drift-phonk`, `memphis` (`drums.md`, "Variations, fills …"; no feel
  key of their own). The phonk kit's cowbell is the rim tuned to 540 Hz (there
  is no cowbell row); the long cowbell is the recipe. Cinematic,
  Chiptune and Hardstyle read as dirty in `tests/test_drumhits.py`.
- **Chains** *Dusty Tape* (`tape` — drive 4, wow 0.5, flutter 0.35,
  hiss 0.25 — a 5.5 kHz low-pass, 12 bits), *Huge Hall* (a 150 Hz
  high-pass, a 6 s room at 0.5, width 1.5: a burst rings at -26 dB 2 s on,
  where a 2 s room is at -54), *Shimmer Wash* (a 200 Hz high-pass, the
  `shimmer` unit — 6 s, mix 0.45, an octave — width 1.4: offline, so a key
  under it plays the dry WAV), *8-Bit Crush* (six bits at a third of the
  rate, a high-pass, the ceiling), *Hard Kick Drive* (hard drive at 6, a
  clipper at -7, a 30 Hz high-pass, the transient shaper's attack and
  sustain lifted, the ceiling), *Phonk Crush* (fuzz at 3, 10 bits at
  half the rate, a clipper at -7, a 30 Hz high-pass, the ceiling; five
  units). The two dirt ones hold the ceiling with no offset
  (`test_the_dirt_chains_hold_the_ceiling_with_no_offset`).
- **Starters** (`songcraft.md`, "Styles"): every one opens with nothing
  missing, standard and long, and passes its signature for five seeds
  (10 checks each). The drum buses carry the styles' chains (Dusty Tape,
  Huge Hall, 8-Bit Crush, Hard Kick Drive, Phonk Crush, written out in
  `styles.py` as synthwave's Gated Verb is). `tools/song_measure.py`,
  seed 1, cold, on an i9-14900K desktop (x86; build / first section /
  whole plan / peak export, peak RSS):

  | style | bars | length | build | first | plan | export | RSS |
  |---|---|---|---|---|---|---|---|
  | lofi | 88 | 264 s | 0.034 s | 1.35 s | 4.82 s | 0.26 s | 1964 MB |
  | ambient | 112 | 384 s | 0.040 s | 2.19 s | 8.06 s | 0.39 s | 2149 MB |
  | chiptune | 96 | 165 s | 0.031 s | 0.18 s | 0.84 s | 0.17 s | 842 MB |
  | hardstyle | 128 | 205 s | 0.031 s | 0.66 s | 2.10 s | 0.19 s | 1297 MB |
  | phonk | 88 | 151 s | 0.030 s | 0.60 s | 1.67 s | 0.15 s | 1144 MB |

  **Ambient's 2.15 GB is the highest of any starter** (synthwave's
  2.06 next), lo-fi's 1.96 after it: both are slow songs of long
  passes (16 bars at 70 bpm is 55 s, at 80 bpm 48 s), as synthwave's
  likely is (not measured further). That is over the 2.0 GB
  `limits.MAX_SONG_SECONDS` is sized at (a 480 s worst case), on a 384
  s song; not investigated further.
- **No breed direction** (a direction is a measured target); no other
  pack's target reads the recipes revisited here.
- **What none of this says**: whether any of it sounds like its style.
  Nobody has listened.

### The core library

**Sounds ready to go, named for what they are**, for every kind of sound:
the genre packs are styles, and the 96 generated sounds are random draws.
The core library fills every category a full synth library has, with hard
sync, fm4, the wavetables and drift where they are the right tool (fm4 for the electric pianos, the house
organ, the bars and bells; the `organ`, `pulse`, `bell` and `formant`
tables; drift for the analog pad and the synth brass; glide for the
whistle and the theremin). Synthesis only, generic names (a brand only as
"Rhodes-style" in a `how`), styles never songs; an acoustic instrument's
`how` says "-ish" and what it is not. Existing units and Patch fields
only.

- **49 recipes** (tag `core`, appended to `recipes.RECIPES`: 160 in all;
  `recipes.CORE_BPM` 120, the tempo their synced parts resolve against;
  `core` is in `recipes.GENRES` — a recipe's one pack tag — though it is
  not a style). Bass: *Ladder Lead Bass*, *Synth-Pop Bass*, *FM Slap
  Bass* (fm4 `pairs`), *Rubber Bass*, *Upright Bass*. Lead: *Brass Lead*,
  *Portamento Whistle*, *Soft Sine Lead*, *Theremin Lead*, *PWM Lead*.
  Keys: *Tine EP*, *Reed EP*, *FM Bell EP* (fm4), *Funk Clav* (the pulse
  table's 15 % frame), *House Organ* (fm4 `additive`: the note and octave
  held, the twelfth a percussive flick), *Drawbar Organ* (the `organ`
  table, dry — the *Rotary Organ* is the spinning one), *Harpsichord*,
  *Music Box*, *Celesta*. Mallets: *Marimba*, *Vibraphone*,
  *Glockenspiel*, *Kalimba*, *Xylophone* (fm4 `additive` at the bars'
  partials — the marimba's 4th, the xylophone's 3rd, the glockenspiel's
  1 / 2.76 / 5.40 / 8.93 — a limiter's gain making up the four averaged
  carriers), *Steel Drum*, *Tubular Bells* (fm4 with the `bell` table on
  osc2). Orchestral: *Synth Brass* (the OB-style swell, drift 0.3),
  *Brass Stab*, *Pizzicato*, *Orchestra Hit*, *Synth Strings*, *Synth
  Choir* (the `formant` table on "a" through the vowel filter), *Soft
  Horns*. Plucks: *Harp*, *Koto*, *Sitar Pluck*, *Bell Pluck*, *Synth
  Pluck*, *Steel String Pluck*. Pads: *Warm Analog Pad* (drift 0.6),
  *Glass Pad*, *PWM Pad*, *Sweep Pad*, *Soft Pad*. FX: *Glitch* and
  *Sci-fi Blips* (the stepped LFO on pitch, gated), *Alarm* (two tones a
  fourth apart), *Ambience Bed*, *Wind*. All 49 play in the worklet with
  their FX (no offline unit); the live parity corpus run on them alone
  passes (294 cases, the JS and WASM voices agreeing). Two avoid noise
  for it: an *Orchestra Hit* noise burst fails the corpus's statistical
  bands (1.9-6.9 dB) and a narrow band-pass on the *Wind* its envelope
  (1.5 dB) — so the hit is saws and a square, the wind a wider band.
- **The Core shelf** (`recipes.CORE_SHELF`, `core_cards()`): a shelf a
  category — bass, lead, keys, **mallets**, **orchestral**, pluck, pad, fx
  — first on the Library, each dark -> bright. **83 cards: the 49, and
  what already IS the thing, listed rather than copied** — 31 genre
  recipes (the *808 Long* for the 808 slide, the *Wobble Bass*, the
  *Slap Pluck Bass* for the pluck bass, the *Sine Sub*, the *Sync
  Screech* for the sync scream, the *Square Lead* for the chip square,
  the *Bright Saw Lead*, the *Scanning Pad* for the evolving scan, the
  *Vowel Drift Pad*, the *Deep Drone* for the dark drone, the *Glass Bell
  Texture*, the *Dusty Keys*, …) and three factory sounds ("factory:" in
  the table: *Acid Alley*, *Nylon Pluck*, *Laser Zap*). A card is Used
  from its own source (its `kind`: the recipe route, or the factory's —
  `sounds._factory`'s `home_view` is "library", so Use from the shelf
  lands on Synths as every shelf card does, and from Synths' own dock
  still stays put). A factory card's numbers are measured into
  `recipes_meta.json` beside the recipes' ("factory:Name", at its
  category's note: `factory_audio`) and drift-checked
  (`tests/studio/test_core_library.py`). The genre recipes the shelf
  lists keep their place on the Recipes shelf, so their cards appear
  twice (both "✓ On Synths" when loaded; a filter counts both). **The
  filter**: mallets and orchestral join the family chips; "core" is a
  chip among the genres (a plate on every core card). The page holds 290
  cards (916 kB, 84.9 kB gzipped: inside `test_browse_views.py`'s per-row
  budget).
- **Measured** as the lo-fi to phonk packs' (dBFS; one note of the whole recipe at its
  card pitch, `stack_audio`; "hold" the dry patch 0.6-0.9 s into a held
  key; "gone" under -60):

  | recipe | role | note | peak | rms | <120 | hold | width |
  |---|---|---|---|---|---|---|---|
  | Ladder Lead Bass | bass | A1 | -0.6 | -11.6 | 0.56 | -0.8 | 0.00 |
  | Synth-Pop Bass | bass | A1 | -1.8 | -14.7 | 0.84 | -3.3 | 0.10 |
  | FM Slap Bass | bass | A1 | -4.2 | -15.0 | 0.45 | -6.0 | 0.00 |
  | Rubber Bass | bass | A1 | -2.2 | -11.5 | 0.83 | -1.1 | 0.00 |
  | Upright Bass | bass | A1 | -11.3 | -29.2 | 0.80 | -14.7 | 0.06 |
  | Brass Lead | lead | C4 | -0.7 | -14.4 | 0.00 | -1.2 | 0.10 |
  | Portamento Whistle | lead | C5 | -4.8 | -10.5 | 0.00 | -0.6 | 0.12 |
  | Soft Sine Lead | lead | C4 | -4.6 | -11.5 | 0.00 | -0.8 | 0.11 |
  | Theremin Lead | lead | C4 | -4.9 | -11.2 | 0.00 | -0.6 | 0.26 |
  | PWM Lead | lead | C4 | -2.1 | -12.5 | 0.01 | -2.0 | 0.15 |
  | Tine EP | keys | C4 | -9.7 | -25.5 | 0.00 | -6.7 | 0.21 |
  | Reed EP | keys | C4 | -0.4 | -15.1 | 0.00 | -9.1 | 0.10 |
  | FM Bell EP | keys | C4 | -12.2 | -27.5 | 0.00 | -4.7 | 0.22 |
  | Funk Clav | keys | C4 | -0.5 | -16.8 | 0.00 | -10.4 | 0.00 |
  | House Organ | keys | C4 | -12.1 | -23.3 | 0.00 | -3.0 | 0.11 |
  | Drawbar Organ | keys | C4 | -4.3 | -12.6 | 0.00 | -0.1 | 0.18 |
  | Harpsichord | keys | C4 | -10.6 | -35.0 | 0.00 | -34.2 | 0.12 |
  | Music Box | keys | C5 | -8.4 | -24.9 | 0.00 | -13.0 | 0.19 |
  | Celesta | keys | C5 | -3.9 | -20.7 | 0.00 | -10.0 | 0.40 |
  | Marimba | keys | C4 | -6.9 | -25.4 | 0.00 | -29.2 | 0.10 |
  | Vibraphone | keys | C4 | -6.3 | -23.5 | 0.00 | -9.0 | 0.08 |
  | Glockenspiel | keys | C5 | -12.4 | -33.1 | 0.00 | -12.4 | 0.36 |
  | Kalimba | keys | C4 | -7.6 | -25.3 | 0.00 | -24.9 | 0.07 |
  | Steel Drum | keys | C4 | -6.4 | -25.1 | 0.00 | -19.4 | 0.19 |
  | Tubular Bells | keys | C4 | -7.3 | -29.4 | 0.00 | -7.9 | 0.33 |
  | Xylophone | keys | C5 | -7.6 | -28.7 | 0.00 | gone | 0.12 |
  | Synth Brass | keys | C4 | -3.5 | -17.8 | 0.00 | -1.8 | 0.22 |
  | Brass Stab | keys | C4 | -3.9 | -25.1 | 0.00 | gone | 0.52 |
  | Pizzicato | lead | C4 | -12.0 | -37.8 | 0.00 | gone | 0.60 |
  | Orchestra Hit | keys | C4 | -2.0 | -20.3 | 0.00 | gone | 0.49 |
  | Synth Strings | pad | C3 | -6.4 | -24.2 | 0.00 | -4.7 | 0.63 |
  | Synth Choir | pad | C3 | -10.2 | -24.0 | 0.00 | -2.9 | 0.75 |
  | Soft Horns | pad | C3 | -11.2 | -19.0 | 0.00 | -2.0 | 0.22 |
  | Harp | lead | C4 | -11.3 | -33.6 | 0.00 | -11.0 | 0.17 |
  | Koto | lead | C4 | -12.5 | -36.6 | 0.00 | -44.6 | 0.20 |
  | Sitar Pluck | lead | C4 | -10.1 | -34.5 | 0.00 | -42.1 | 0.21 |
  | Bell Pluck | lead | C4 | -5.9 | -25.3 | 0.00 | gone | 0.30 |
  | Synth Pluck | lead | C4 | -1.7 | -29.0 | 0.00 | gone | 0.20 |
  | Steel String Pluck | lead | C4 | -11.7 | -38.2 | 0.00 | -30.3 | 0.40 |
  | Warm Analog Pad | pad | C3 | -5.3 | -22.9 | 0.00 | -0.7 | 0.25 |
  | Glass Pad | pad | C3 | -11.5 | -23.4 | 0.00 | -0.5 | 0.43 |
  | PWM Pad | pad | C3 | -3.0 | -19.0 | 0.00 | -1.3 | 0.25 |
  | Sweep Pad | pad | C3 | -4.2 | -22.0 | 0.00 | -4.0 | 0.53 |
  | Soft Pad | pad | C3 | -9.2 | -20.4 | 0.00 | -0.6 | 0.31 |
  | Glitch | fx | C4 | -1.1 | -15.8 | 0.00 | -3.7 | 0.00 |
  | Sci-fi Blips | fx | C5 | -4.5 | -13.9 | 0.00 | -1.3 | 0.24 |
  | Alarm | fx | C4 | -9.4 | -18.9 | 0.00 | -0.8 | 0.14 |
  | Ambience Bed | fx | C3 | -8.4 | -24.0 | 0.00 | -1.6 | 0.94 |
  | Wind | fx | C4 | -5.0 | -21.4 | 0.00 | -0.9 | 0.52 |

  Held to the table's rules in `tests/studio/test_style_packs.py` (its
  `NEW`: peak, DC, under 20 Hz, the role's band; the plucked and struck
  ones gone 0.6 s into a held key, the pads and the two held fx holding),
  every held patch to the hold-seam bar (`test_hold_seams.py`), and a few
  to what they are named for in `test_core_library.py` — the
  glockenspiel's second partial at 2.76 of its note and the marimba's at
  4, read off the wave; the alarm's two tones a fourth apart; the
  vibraphone's 5 Hz turn.
- **The 96 named for what they are** ("Sound library", "Names", above).
  Near-duplicates were measured; none removed.
- **What none of this says**: whether a marimba sounds like a marimba.
  Nobody has listened.
