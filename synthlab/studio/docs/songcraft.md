# Song creation: starters, the chord track, generators, the checklist

How a whole song is laid down in one click and what it is made of:
`studio/gen/` (the chord track, the generators, the four styles, the
builder, the checklist), `routes/song_start.py`, the starter sheet and
the chord strip on the Song page, the head plan a new song starts on.
The song model these write is `song.md`; rendering is
`song-render-export.md`. Index: `README.md`.

**Nobody has listened to any of it.** Everything below is held by counts,
clocks and model checks: a starter has the grammar of its style, never a
certificate that it is music.

## The rules

- **Generating is committing, and committing plays.** There is no preview
  render: a starter, an auto-arrange and a generator each go through
  `routes/song.py`'s `_commit` (one undo step, the regions by their
  watches) and answer with `song-autoplay` in `HX-Trigger`, which song.js
  plays — a new song from bar 1, one part by looping the first section it
  sounds in.
- **Rolling again is the same undo step.** `key="start"`, `"arrange"`,
  `"gen:<track>"`, with `window=math.inf` (the take rule): ten rolls are
  one Ctrl+Z, and the step under them is the song before the first. An
  edit in between, or an undo, closes the step (`state/history.py`).
  Pinned by `tests/studio/test_gen_undo.py`.
- **Everything made is ordinary song model.** Tracks, pool patterns,
  sections, repeats, spans, lanes, takes: all editable where a hand-made
  one is. A generated pattern also carries `gen` (which generator, its
  seed, its settings), so it can be rolled again; a hand edit leaves
  `gen` alone, and rolling writes over the edit.
- **Deterministic for a seed, across processes.** Every draw is a
  `random.Random(zlib.crc32(...))` — never `hash()`, which is salted per
  process — and a starter's ids come from the seed too (`build._Ids`), so
  `build.start(style, seed)` is the same dict everywhere
  (`test_song_start.py`, `test_gen_notes.py` run it in a child process
  under another `PYTHONHASHSEED`). Seeds are taken modulo
  `limits.SEED_MODULO`; a post without one draws one.
- **Pure.** Nothing in `studio/gen/` touches the session, the disk or the
  renderer. The one side effect of a starter — its transition clips — is
  the route's: `build.start` names them (`Built.clips`), `_clips` renders
  them into the clip store, and a refused commit deletes what it wrote.
- **Generic names only.** A style is tempo, drum grammar, harmony, sound
  design, arrangement shape and mix moves. No artist, track or hook is
  named anywhere in code, presets, UI text, docs or test names.

## The chord track (`gen/chordtrack.py`)

`song["chords"] = {"bars", "slots": [{"at", "deg", "q", "inv", "len"}]}`:
a progression in DEGREES of the song's key (so a key change carries it),
`at` / `len` in beats, `bars` the length that repeats (1, 2, 4, 8, 16).
`section["chords"]` is the same and overrides the song's for that
section. Absent unless set; `schema.CHORDS` clamps and sorts the slots.

- **It renders nothing** and is in no key: guidance for the generators,
  the strip and the checklist. Notes a generator wrote do not follow a
  later chord edit until they are rolled again.
- **`resolve(song, section, bars, steps_per_bar)` is the one reader**: the
  chord under every step, the progression tiled from bar 1.
- **Every key works.** A chord stacks the scale's own tones (`TONES`:
  triad, 7th, 9th, sus2, sus4, power) — "VI" in a pentatonic is a chord,
  not a refusal. With `ROOTS` every semitone from A0 to E3, a song can be
  in any key.
- **One rule for a key that is not seven-tone** (the pentatonics, blues,
  whole tone): a slot's degree counts and
  its chord stacks in the key's **diatonic parent** — major when the key
  has a major third, else natural minor (`chord_scale` / `parent`) — so
  every progression, `PRESETS` and the styles' alike, plays as written:
  (1, 6, 3, 7) in A pentatonic minor is i VI III VII, Am F C G (taking
  the degree modulo the scale's length would play "I I IV III"). `semis`, `roman`, `from_cells` and through them `resolve`, the
  strip and its menu (`build.progressions`), Perform's lane, the pads
  that follow the loop's chords and the generators all read it. A
  generator's chord tones may so lie outside the key (the F); its
  melodic lines stay on the key's ladder (`notes.py`, "In key"). The
  track renders nothing, so it is in no render key.
- `roman` / `name` spell a slot ("VI", "F#m"); `from_cells` reads a chord
  PATTERN back into a track (`POST /song/chords` `op=from-pattern`).
- **The strip and the tiles** (`fragments/chord_strip.html`, in the
  playlist's tool bar; `song_arranger.html`'s `.arr-chords` row): the
  strip holds the progression's own tools — the progression menu (the
  style's own, then `chordtrack.PRESETS`), + / −, the length, ⚄ — and a
  section's ⋯ menu has "Own chords" and then the same strip for that
  section. **The chords themselves are tiles on the time axis** (the
  playlist tools: `song.md`, "Playlist tools"): a tile a chord at its
  bars, its menu the degree / quality / inversion / split / remove, 1-7
  on a focused tile, its left edge dragged to move where it starts. All
  `POST /song/chords` (`op` also `edge` and `split`).

## Generators (`gen/notes.py`, `gen/drums.py`)

A notes generator is `(ctx, **params) -> lane` — a notes pattern's own
fields. `notes.context(song, section, bars=, kick=, register=, seed=,
role=)` builds the `Ctx`: the key, `resolve`'s chords, the kick's steps,
the part's register and its stream.

| Kind | Writes |
|---|---|
| `bass_rolling` | steps 1, 2, 3 of every beat and never the kick's step, one step long each with a **cut** between (two cells of one pitch side by side are otherwise one held note), on the chord's root. `figure`: `roll`, `octave` (the third an octave up), `gallop` (2, 3), `offbeat` (2). The seed moves which beats' last note pops to the chord's fifth or the octave. `bass_pair` is the two tracks from one seed: the mid with its pops, the sub an octave under on the roots |
| `bass_root`, `bass_offbeat`, `bass_walk`, `bass_2step` | long roots · an offbeat eighth · a syncopated funk line with a slide · a two-step sub with gaps |
| `chords` | the progression voiced (`close`: voice-led; `wide`; `anthem`; `power`), `rhythm` `held` / `stabs` (offbeats) / `pump` / `push` |
| `pad` | held, wide |
| `arp` | the chord's tones as explicit notes: `up`, `down`, `updown`, `random`, `pedal`; `rate` 1 or 2 sixteenths, `gate`, `octaves` |
| `lead` | a two-bar motif answered in bars 3-4, varied in 5-8, home on the root |
| `chops` | short pitched hits, call and answer, a stutter at each phrase end |
| `hit` | one note, the key's root at the bottom of the window, on the pattern's first step, held `bars`: a drop FX's single hit — the sound (an impact, a sub drop) does the rest |
| drums: `beat`, `fill`, `fill_every`, `build`, `thin`, `busier`, `sparser`, `variation`, `tile` | `beatgen`'s beat (with `density`) and its style's FEEL (`beatgen.dress`: ghosts with a velocity of their own, a chance a hit, the rows' own swing) · the last ½ / 1 / 2 bars of any bar replaced by a seeded figure from the style's own table (`STYLE_FILL`), velocity rising, a roll's last eighth doubled · that at the end of every N-th bar · **a roll in quarters, eighths, sixteenths, then 32nds by ratchet — each snare hit with its own velocity (0.4 to 1) and tune (0 to +7) — while the kick thins** · voices taken out · ghosts and offbeats added (never a kick) / the weakest removed, ghosts first (never a kick on a beat or the backbeat) · a few light hits traded |

- **In key, in register, on the grid**: a pitch is a scale tone inside the
  part's two-octave window (`notes.REGISTER`, octaves above the bass
  root, which sits between E1 and D#2 whatever octave the song's root is
  written in), on a sixteenth. Every generated pattern is 8 bars x 16
  steps.
- **The drawer opens a pattern on its own octave.** A generated lead is
  two or three octaves above the root, so the drawer's grid — the key's
  rows from the root — would open empty and say "24 notes outside the
  key". `sm.window_octave(song, pattern, span)` picks the octave window
  (-2..+4) that holds the most of the pattern's notes; the drawer draws
  those rows and posts them back as a hidden `oct`, which the grid route
  trusts (clamped). A starter also sets `grid_span` to 2.
- **Per-hit drum data** (`drums.md`, "The
  per-hit model") is what the drum generators write with: a drum lane
  carries `hits` and `swing` beside its cells (empty when it has none —
  the pool's schema drops an empty one). `hits_per_bar` counts a
  ratcheted step as the hits it plays. Two signature checks read it
  (below).
- **A beat's family** (`build.cell_variant`): a variation by letter (A-D)
  or the fill of the beat a cell plays — made, picked or, for the fill,
  rolled again in place; `drums.md`, "Variations …". The working loop's
  own generators are `POST /fragment/gen` (the beat only).

## Styles (`gen/styles.py`)

Eighteen, as data: `melodic_techno` (the default: the first Start a fresh
session offers), `filter_house`, `garage`, `trance`, then `electro` and
`industrial` (`sounds-and-breeding.md`, "Style packs: electro and
industrial") and `psych` and `sixties` (labels *Psych Haze* and *Sixties
Studio*, "Style packs: psych and sixties"), then, with plain genre labels
("Style packs: hip-hop, drum & bass, dubstep, synthwave, future bass"),
`hiphop` (*Hip-Hop / Trap*, 140 half time:
the 808 on the kick, `bass_2step`'s slides, hat rolls, a bridge with no
drums, the distorted 808 in the second hook), `dnb` (*Drum & Bass*, 174:
the two-step, a neuro Reese on `bass_walk` — its chain is full, so it
leaves the kick's steps rather than being ducked — over a ducked sub, a
breakdown, the jungle-ish beat in the second drop, an Impact track),
`dubstep` (*Dubstep*, 140 half time: a sub under a wobble at the
eighth; the second drop the riddim beat under a growl for the first half
of every pass and the same wobble at the eighth triplet — a track's
`patch` override — for the second; a cut and an Impact into each drop;
seven synth tracks, the eighth free), `synthwave` (*Synthwave*, 100: the
drum bus through the Gated Verb chain's room and gate, a sixteenth arp
bass on `arp` "pedal" in the bass register, tucked; brass pushes, then
stabs in the choruses; a bridge with no drums swelling into the last
chorus) and `future_bass` (*Future Bass*, 150 half time: supersaw chords
on Future Pump, vowel chops, a bent pluck, the `future-build` beat
climbing into each drop, an impact take on the first, a sub drop take
under the second); and, with plain labels too ("Style packs: lo-fi,
ambient, chiptune, hardstyle, phonk"), `lofi` (*Lo-Fi Hip-Hop*, 80, swung 0.24: the
Dusty Keys pushed off the beat and held in the break, a drifting jazz
pad, a soft sub on `bass_2step` tucked, a muted pluck; the drum bus on
the same Dusty Tape as the keys; a quiet fill, a swell into the second
hook), `ambient` (*Ambient / Cinematic*, 70: no drums in the opening,
the drift or the fade — 48 of 112 bars quiet; a drone tucked under the
toms' kick; a lane walking the scanning pad's table across the rise;
swells (takes) into the climaxes, whose toms end in a fill; the braam a
`hit` part on each climax's first beat; its long form doubles only the
second climax — both would pass the length cap at 70 bpm), `chiptune`
(*Chiptune*, 140: the square lead, then the pulse lead in the second
verse, the thin pulse on `arp` "up" a sixteenth a note, the triangle
bass on `arp` "pedal" in eighths an octave over the subs, the crush lead
on the chase beat; dry — no reverb anywhere — the drum bus on 8-Bit
Crush), `hardstyle` (*Hardstyle*, 150: the drum bus on Hard Kick Drive,
the reverse bass on `bass_offbeat` — the offbeat between every kick, so
it needs no duck — the screech in the drops, a break where the euphoric
supersaw enters over the pumped pad, the rawstyle beat under the climax,
a cut and an Impact into each drop) and `phonk` (*Phonk*, 140: the
cowbell on `chops` — short pitched hits, call and answer, a stutter at
each phrase's end — over the Phonk 808 on `bass_2step` tucked, the drum
bus on Phonk Crush, the memphis beat in the hooks, the hard lead in
them). Melodic techno's label is
*Anymore* and trance's *Arm In Arm* (nods, as the others'; ids, kits and
blurbs' genres are the genre's — "Style packs: garage, techno, house,
electro, trance"), and five styles play that pack's sounds: melodic techno a Reese, a dark arp, a sung
choir lead and the drops' **Impact** as a track of its own (a `hit` part,
role `fx`) where the impact takes were; trance the big-room pad, the
pluck-lead and a sub drop (a take) under the climax, its drums through
Drum Punch's transient shaper; filter house the envelope-filter funk bass
and the string machine; garage the felt piano keys and a vowel pad
through the OTT; electro the transient shaper in front of its squashed
drums. **No starter fills the eighth synth track**
(`limits.MAX_SYNTH_TRACKS`): with eight, the checklist's "Add a bass" and
its kin have no room, so the sub drops stay takes. Each: tempo and its
range, keys (the seed draws one), progressions, the kit
(`drumfactory.DRUM_KITS`), drums variants as short pipelines of
`gen/drums.py` calls, a track list (key, role, name, recipe Sound,
register, gain, parts) and the **shape** — the sections in order with
what plays and the moves: `sweep` ("Sweep all": the same low-pass lane on
every sounding synth track), `lane`, `cut` (every sounding track's level
to 0 over the last beat), `take` (a transition clip on a boundary),
`enter` / `span` (a block that comes in late). The file's docstring is
the vocabulary.

Three things the shapes are built around:

- **A section with a lane, a take or a span renders as one pass of its
  whole length**, and every repeat replays it. So the moving sections are
  8 or 16 bars and a 32-bar drop is 8 bars x 4 (with an impact on its
  first bar line, which then lands every 8 bars). A breakdown where a
  part enters at bar 9 is two sections: "Breakdown" (16 bars, the part's
  block from bar 9) and "Breakdown B" (8 x 2).
- **A recipe's following layers are not used**: a track holds one sound,
  so a starter takes the recipe's patch, FX and curves.
- **Gains are by role, not measured** (`_track(... gain)`). Balance is
  the mixer's job (`song.md`, "The mixer").

`GENERIC_SHAPE` is what "From my loop" arranges into.

## Starters and loop -> song (`gen/build.py`, `routes/song_start.py`)

- **`POST /song/start`** (`style`, `seed`, optional `bpm`, `root`,
  `scale_name`, `variant` "long", `again=1`): `build.start` is the whole
  song — key and tempo, the chord track, tracks with their sounds, roles
  and readable names ("Sub", "Mid Bass", "Gated Pad": never "Synth 2"),
  one pattern family a part, sections from the shape, the moves. It
  REPLACES the song as one undo step (the sheet's buttons carry an
  `hx-confirm` only when the song has sections), closes the drawer, sets
  `grid_span` to 2 and plays from bar 1. A tempo at which the shape would
  pass `MAX_SONG_SECONDS` takes repeats off the longest sections and says
  so; one that still cannot fit is refused before anything is rendered.
  The style offered first is the song's own, else the browser's last
  (the `synthlab_style` cookie), else melodic techno. **A card names the
  keys its seed may draw** ("one of 5 keys", the list its `title`): it
  showed the style's first key, and the seed drew another of five
  (`styles.card`'s `key_names`). "From my loop" is a line under the
  cards, not a card stretched to their height; "Add loop to song" is the
  playlist foot's, once.
- **A key is a name** — "E minor", in the topbar's select and ledger and
  the starter sheet's — never a register ("E1"): `songpage.key_choices`
  offers the twelve, C to B, each valued at the root nearest the song's
  (a key change moves the bass the shortest way), the starter sheet's in
  the starters' own register (E1 to D#2). The octave stays in the model
  (`song["root"]`), where the bass reads it.
- **`POST /song/arrange`**: `source=loop` (the default) lays the working
  loop down as intro / verse / build / drop / break / drop / outro
  (`StudioState.loop_as_song`, then `build.arrange`) **by muting,
  thinning and filtering, never by writing new music**: each section
  plays the loop's own patterns by reference on the tracks its row names;
  the beat's variants are derived patterns that only ever have fewer
  hits; the full beat is tiled to 8 bars with a fill in bar 8 — the one
  thing generated. Tracks get a role (`guess_roles`: the lowest lane is
  the bass, a lane of three notes at once the chords) and a name for it
  ("Bass · Sub Bomb"). `source=<section id>` shapes one section in place;
  `add=intro|outro` adds one thinned section at that end.
- **`POST /song/gen`** (`track_id`; `kind` again | busier | sparser | a
  generator's name | fill | build | duck; `add` bass | beat | riff):
  one part. `again` rewrites every generated pattern the track plays from
  its `gen` with a new seed — and a rolling bass's other half with it
  (the two share one stream). Nothing else in the song moves.
- **`POST /song/meta`**: the song's own `title`, `bpm`, `root`,
  `scale_name`, `swing`, `drum_swing`. A key change moves nothing by
  itself (the chord track is in degrees and follows); `move=1` carries
  every note pattern to the new key.
- **`POST /song/chords`**, **`GET /fragment/song/check`**: above, below.
- **Hostile input**: an unknown style, variant, kind, op, role, quality,
  scope, root or scale is a 422 toast and nothing changed; wild numbers
  are clamped; a seed is taken modulo `SEED_MODULO`.

### Sound in seconds: the head plan

The song's one gain is the minimum over every pass, so a plan needs every
section rendered — 3 to 13 s cold for a starter on the x86 desktop. A new
song does not wait for that. `GET /song/plan.json?head=1`
(`SongRender.song_plan(head=True)`) is the plan to START on: the first
section alone, looping (`HEAD_PASSES`), at `song_head_gain` — `gain_of`
over the first section and the fullest one (its `energy`). song.js plays
it, fetches the whole plan (`refresh`) and swaps it in at a pass start.

- The whole-plan gain is never higher than the head's, and where the
  fullest section is the loudest — what `energy` means — it is the head's
  exactly, so nothing steps at the swap. **It is not always**: measured
  over the four styles x five seeds (the x86 desktop) the step at
  the first pass boundary after the whole plan arrives is 0 dB in 12 of
  20 — every trance, four of five melodic techno, three of five garage —
  and a drop of 0.06 to 1.04 dB in the rest (filter house every time,
  0.11-1.04 dB: its groove, not its drop, holds the loudest pass). One
  case is pinned at 0
  (`test_the_head_plan_starts_on_the_first_section_at_the_final_gain`);
  the head gain is never below the song's. Nobody has heard whether a
  1 dB step sixteen bars in is noticed.
- A head WAV says `head=1`, which is how `/song/section/{id}.wav` knows
  to play at that gain without rendering every section first. Once the
  song's gain is known, a head request is simply the plan.
- Both renders run outside the studio lock (`RENDER_OUTSIDE_LOCK`,
  unchanged) with the plan's own progress events.

`tools/song_measure.py` measures it (cold, in a child process): the
build, the first section, the plan, the export, peak RSS.

## The checklist (`gen/checklist.py`, `fragments/song_check.html`)

The topbar's "✓ N": what the song is missing, read off the model alone
(nothing renders), each finding with the button that fixes it through a
route the page already has.

| Finding | When | Fix |
|---|---|---|
| no intro / no ending | the first (last) section has a kick and four sounding synth tracks or more | "Add an intro / outro" (`/song/arrange add=`) |
| nothing changes for N bars | 32 bars or more of the same patterns with no lane, span, take or fill | "Add a fill every 8 bars" (`/song/gen kind=fill`); when the **energy lane** steps inside the stretch (`checklist.energy_steps`: two neighbours 0.15 apart) the finding says "though the energy lane does" and the fix is "Arrange by energy" (`/song/energy op=arrange`) |
| no bass in the fullest section | no `bass` role sounds there | "Add a bass that follows the chords" |
| a bass on the kick's steps, unducked | note-ons on kick steps and no `pump` / `sidechain` | "Duck the bass under the kick" |
| nothing leads into the fullest section | no take ends on its first bar line | "Add a riser" |
| a track muted everywhere / playing nowhere | | "Unmute" / "Remove the track" |
| over a cap / under two minutes (info) | from the numbers | — |

`signature(song, style)` is each style's signature as machine checks, a
list a style, shown under the findings for a song started in one. A
starter passes its own for five seeds with nothing missing
(`tests/studio/test_styles.py`): **every check, 15 / 11 / 9 / 11 for
trance, melodic techno, filter house and garage** (9 / 7 for electro and
industrial, 8 / 10 for psych and sixties; among them trance's "a
sub drop under the climax's first beat" and "the drums punched", melodic
techno's "a sub drop under a drop's first beat", filter house's
envelope filter on the bass, garage's OTT pad, electro's punched drums;
9 / 9 / 9 / 8 / 10 for hip-hop, drum & bass, dubstep,
synthwave and future bass — `_half_time` (a snare or clap on step 8 of
the drop's first bar, none on 4 or 12), `_rolled` (a hat or snare hit
with a ratchet of 2 or more), `_ghosts`, a gliding sine 808 landing
with the kick, a Reese read as two saws 10 cents apart or more, the
wobble as a bass whose filter LFO is synced (two clock rates or more
across the basses), a gated room as a `reverb` followed by a `vgate` on
the drum bus, a sequenced bass as eight note-ons a bar or more in every
chorus bar, a pitch-bent pluck as a pitch envelope or a route to
`pitch`; 10 each for lo-fi, ambient, chiptune, hardstyle and
phonk — the hooks' rows held straight under swung hats (a pattern's
`swing` 0 for kick and snare), a ghost kick at 0.5 or under, fm4 keys on
tape (`tape` wow + flutter) and the drum bus on tape, a third of the song
or more without drums (`quiet_runs`), a wavetable pad whose `wt_pos`
rises (a curve on the track's sound or a section lane), a braam read as
saws behind a filter opening 50 ms or more after the note that dies, six
toms a bar or more in the section before a climax, a pulse lead off the
pulse table's square frame, an arp with a note every sixteenth in every
chorus bar, the kit's snare at tone 0.1 or under (noise), no reverb unit
on any track (chiptune is dry), the kit's kick at 0.6 s / drive 3, a
reverse bass as a bass with a slow attack whose drop notes miss every
kick step, a hard-synced lead, a cowbell read as a struck fm4 lead with a
non-integer ratio, the kit's rim tuned to 500 Hz or more and playing).
A drop's hit is read by the sound's grammar, never its name
(`checklist._drop_hit`): a take of the kind on the drop's first bar line,
or an `fx` track hitting its first beat whose patch falls onto its note
(an impact: a pitch envelope of 12 st or more gone in 0.3 s, no
sustain; a sub drop: a sine under 500 Hz falling onto it, or bending on
down). Two read the per-hit table: trance's "the roll reaches 32nds
with the snare's tune rising" (`_roll_peaks`: a snare step ratcheted to
two hits or more on a sixteenth grid, the tune never falling and ending
five semitones or more up) and melodic techno's "four hits or more with
a chance under 1" (`_chancy`: over the beats the song plays). Garage's
ghosts are counted by a hit's own velocity, else its step's. A check is
a pass or a fail.

Not built: the spectrum half of "nothing below 60 Hz" (the plan measures
levels, not spectra) and the loudness row (the export group's "measure"
is where that number is).

## Generators in another meter

The generators write a 4/4 bar of sixteenths. A part written for a
section in another meter (`gen/build.write`, one section's or the song's
meter) is put on that meter's beats (`meterops.beat_fit`, `meter.
beat_step`: 4/4 beat j onto the meter's beat j, the place inside the beat
scaled; 6/8 takes a 4/4 bar's first two beats onto its two dotted
quarters) and says its `meter`; rolling it again keeps it. A beat
pattern rolled again keeps its own meter (`gd.beat(meter=)`). The chord
track is per bar and holds in any meter. No starter is in another meter.

## Roles and names

`track["role"]` (optional): drums, bass, chords, lead, pad, arp, vocal,
fx (`sm.TRACK_ROLES`). Starters set it; auto-arrange guesses it; a
track's ⋯ menu sets it (`POST /song/track/{id}/mix` `role=`); the
checklist and the shapes read it. Tracks are named for what they are for.

## Empty states

An empty state offers a next step that makes sound: the empty Song page
IS the starter sheet; an empty pool (and a song with sections but no
pattern) writes a beat or a riff and places it (`/song/gen add=`);
the loop becomes a song through the Loops page's **▶ Whole song**, never
empty (`loop-and-perform.md`, "→ Song");
Perform's Sounds, the
Drums library and the Library's empty detail start a song in
the style used last (the Library's filter that hides every row offers
Clear filter instead: the way back is the filter's)
(`fragments/empty_start.html`). Driven in Chromium by
`tests/browser/test_songcraft.py::test_no_empty_state_is_a_dead_end`.

**The rest** have one of three presses, each a `data-cmd`:

- **"▶ Play the loop"** (`fragments/empty_play.html`: `POST
  /fragment/preview` with `play`; the render comes back `autoplay`) —
  Synths' "Yours", the agent's drawer, the clip library, Breed's empty
  board, the Map (only while it plots fewer than three points: the
  shipped sounds are always on it, so its empty state does not show),
  both of Voice's. It changes
  nothing. It is a way to hear something where starting a song is not
  what the page is for; it is not always that page's next step.
- **"＋ Reverb ▶"** in an empty FX rack (`/fragment/fx/add` with `play`;
  on the Mix view, whose rack is the song's master, the song's first
  section plays).
- **"▶ Start a song"** — the Song drawer's idle line, Perform's Beats
  launcher, and a sampler with no sample
  (which can only be silent, so nothing nearer its page exists).
- The Song page's "No tracks yet" has the empty pool's two generator
  buttons (`/song/gen add=` adds the track it writes on).

`tests/studio/test_empty_states.py` holds EVERY `.empty-state` in the
template sources to a sounding action (exemptions: the Library's "No
library yet", an install fault, and the Sample Lab's two — "Bring in a
song" and "No chops yet" — whose one action is the Lab's own next step,
choosing a song or cutting the region into chops: "Start a song" and
"Play the loop" would be off-topic there, `sample-lab.md`, "UI"); a new
empty state without one fails there. The browser presses nine of them
and reads the meter
(`test_the_other_empty_states_make_sound_in_one_press`); Breed's empty
board (the view breeds on arrival) is held by the source scan alone.

## The energy lane

`section["energy"]` (0..1): a starter and an auto-arrange set it; it is
DRAWN and can be set (`song.md`, "Playlist
tools": a bar a section under the heads). It renders nothing. Three
things read it: the head plan's gain (the fullest section, above), a
boundary's suggested transition, and "≋ by energy" — the loop -> song
machinery (`build.arrange`) run with one row a section, made from the
energy drawn (`songops.arrange_by_energy`).

## Elsewhere

Each where its own notes are: the per-hit drum table and
the beat's generators (`drums.md`), the chord tiles and the energy lane
(`song.md`, "Playlist tools"), Balance (`song.md`, "The mixer"),
`Arrangement.strum` and the NOTE generators on the working loop
(`loop-and-perform.md`, "The rule" and "Generators on the loop").

`/fragment/chords/track` — a chord track on the
working loop, `Arrangement.chords` (`schema.CHORDS`; absent when unset
through `schema.arr_dict`, as `drum_hits`, `drum_row_swing` and `strum`
are, and dropped from every render key: it renders nothing) — Perform's
chord lane (`loop-and-perform.md`, "The chord lane"). The loop's
generators read it first (`gen/loop.harmony`), and a chord edit is
`songops.edit_chords` for the song and the loop alike. `grid_oct`, the
roll's octave window, is a RUNTIME field (`loop-and-perform.md`,
"Rows").
