# Changelog

## Unreleased

- **A take with the keys arp lands on the grid it was played on**: the
  note grid has triplets — 1/8T and 1/16T (12 and 24 steps a bar in 4/4;
  9 and 18 in 3/4; none in 6/8, 7/8 or 12/8, whose eighths already are
  their beat's triplets) — and a take placed as played picks the rate its
  notes sit on best, said in its toast ("on 1/8T (12 a bar)"): a 1/8T arp
  lands with every note on a line, a riff of sixteenths stays on 16. A
  take recorded with nothing playing now counts the keys arp from the
  take's own bars (it counted from each press, so no grid held it), and
  the live arp counts each bar from its line ("bar" retrigger restarts at
  every bar, as the grid's does). The drum grid keeps its straight steps;
  1/16T holds 8 bars at most. Loops already saved keep their rate.
- **Every row the notes use**: the roll's rows have a fourth range,
  "notes" — the key's rows from the lowest note to the highest (and any
  note's row out of the key), scrolling — and a loop or take whose notes no
  24-row window holds (a take over three octaves and more) opens on it.
- **Rec ▾ → Arp: its notes | the keys I held**: with "the keys I held", a
  take made while the keys arp plays keeps the keys you pressed — when,
  how long, how hard — and its loop plays them through the grid arp set as
  the keys arp was (its pattern, rate, direction, octaves, gate, swing), so
  the loop edits as a few held notes, not every note of the arpeggio; the
  same switch is in the keys arp's ⚙. Its render is the keys arp's notes,
  pitch for pitch and time for time. Nobody has listened.
- **The Loops page has one transport, the deck**: ■ and ▶ (Space) at the
  top of the page play what its source says — **Loop** (the selected
  cell's loop alone), **Scene** (that cell's row) or **Song** (an empty
  song made from the loop first: the old "▶ Whole song") — beside one
  ⏺ Rec, the keyboard dock's own, whose ▾ holds the length (free
  included), count-in, + beat, where the take lands (a new loop, or the
  selected cell overdubbed or replaced — always shown), "keep what I just
  played" and "record the mix as audio". A meter of lamps shows the bar
  playing, pulsing on the beat, and counts in while Rec is armed. Every
  other play button on the page is a hollow ▷ listen that never stops
  what plays. **One selection**: a cell's click opens its loop on its
  lane — the editor shows it ("saved"), it plays there from the next bar
  and every edit saves to it — and an empty cell's lane is a sketch ("not
  saved", Save as loop). Each lane's ■ and a lamp that lights while it
  sounds moved into its head; the playing cell turns a ring of its loop's
  position.
- **A take's loop plays at the level it was played at**: a ⏺ Rec take's
  loop — opened with Edit or playing in a launcher slot on Perform, a
  Loops row's ▶ at another tempo, a loop's bounced audio — plays where the
  keys put the same notes, not ~9 dB over them as a normalised render did.
  The render is untouched (no render key moved); the player applies the
  gain. The keys and every other sound keep their levels.
- **A take not of whole bars asks what to do**: recorded with nothing
  playing (or "to ■" off the grid), its answer offers **Keep N bars** (the
  gap before it repeats), **Trim to M bars** (the notes past the line cut
  at it) or **Fit the tempo** (the tempo at which the take is exactly
  whole bars, every note where it was played). Each is one undo step on
  the grid.
- **A take's audio sits on its notes**: the take was cut ~70 ms late
  against its notes in Chromium (the recorder's start event comes long
  after it begins, and the key bus's limiter delays the keys 6 ms); it is
  now measured, and the two agree within a few milliseconds.

- **A quieter Loops page**: a loop's row (the Loops panel, the clip
  drawer, the keyboard dock's strip) shows ▷ listen, its name, its facts
  and ★ — every other action (Edit, → Song, Quantise, clean-up, tags,
  rename, delete, ✂ Shape) is its right-click menu, with → Song and
  Quantise as submenus. Double-click a row (or Enter) to edit it; on the
  Loops page a loop that sits in a launcher cell selects that cell
  instead. The note grid's and the drum rack's toolbars are one line
  each — the tool, snap and Q, the bars and steps, the rows and octave
  window, ghosts, undo — with the rest behind **Tools ▾**, grouped (move,
  notes, length, velocity, write / generate, clipboard, view), every tool
  still a palette command and its shortcut. The key and the feel fit one
  line above it. The right column is the lane inspector alone: → Song
  moved to the launcher's head, and Play (pads, XY, FX, beats) opens from
  the keyboard dock's Tools ("Pads"), its keys playing it shut too.
  Saved layouts of the page are reset once.
- **Import SFZ**: a zip of an `.sfz` and its WAV / FLAC / OGG samples becomes
  a sampler sound with a zone a region (keys, velocities, root, tune, gain,
  loops, round-robin takes, the first region's amplitude envelope). Every
  opcode it does not read is listed in the toast with its count, never
  dropped silently; the archive is read by name, never extracted, with
  bounds on its size, entries, compression, text, includes and defines.
- **A spectral oscillator**: osc 1's "spectral" pill plays the sampler's
  sample resynthesised from its spectrum, with time and pitch apart — a
  scan position (drawable, a route target) and a scan rate (0.5 twice as
  long at the same pitch, 0 a freeze, negative backwards), a timbre shift
  that moves the formants without the pitch (riding the key, or kept
  where the recording has them), low and high partial cuts, and a
  transient keeper that holds attacks sharp when stretched. The sampler
  panel draws where the read goes. Deterministic, so renders cache; the
  keys play it from the server's render, not the live voice.
- **A take plays back exactly as it was played**: a ⏺ Rec take's loop is
  placed "as played" unasked, every press keeping its own start between
  the lines, its own release and its own velocity (a chord's notes each
  their own) — a per-note feel beside the grid's cells, played by the
  loop, Edit, → Song and the MIDI file alike, carried by every grid tool,
  drawn on the roll (its start off the line, its tail where it was let go)
  and dropped by the roll's new **snap**. Q still puts it on the steps at
  100 %.
- **Gestures and knob turns in a take**: the pitch bend, mod wheel,
  aftertouch, every Patch knob or slider (dragged, typed, a deck dial, a
  MIDI CC), the XY pad and the macros moved during ⏺ Rec are kept with the
  loop as automation from where the take began; what cannot be automated
  (envelope times, shapes, an FX unit's sliders) is named in the take's
  toast. The session's sound stays as you left it.
- **Tempo-synced envelopes**: the filter envelope's and envelopes 3 and
  4's attack, decay and release can each be a beat division (1/16, 1/4,
  a bar...) instead of seconds, following the tempo — in the loop and on
  the live keys alike.
- **LFOs to 1000 Hz**: the general LFOs' rates reach audio rate — FM and
  AM on the pitch, levels and other per-sample targets (a filter target
  reads it once a 64-sample block). Live on the keys for every shape but
  a stepped one past 30 Hz, which plays from the server's render.
- **LFOs 4, 5 and 6**: three more general LFOs in the Modulators panel
  (rate, shape, sync, phase, retrig / free, a drawing) and three more
  sources in the mod matrix. Folded away until a route uses one; they
  play on the live keys.
- **Chaotic LFO shapes**: every general LFO's shape menu gains Lorenz and
  Rössler — a line wandering round a chaotic flow, never repeating, the
  same on every render and on the live keys.
- **A route's switch and curve**: each mod-matrix row gains an on / off
  switch (off keeps the route but bypasses it) and a curve knob that bends
  its source before the amount — up rises early, down late. On and
  straight by default, so no sound changes; both play on the live keys.
- **A convolution unit**: pick any clip of up to 8 s as the impulse — a
  room, a cabinet, a body — with mix, predelay and a low and high cut.
  The clip is kept by its content, travels in a project bundle, and the
  keys play it live through the same impulse. A missing impulse passes
  the sound through untouched.
- **A utility unit**: gain, pan, width (mono to twice as wide), the lows
  made mono under a frequency, and each channel's polarity — on every
  rack, live on the keys, its gain and width automatable in a song.
- **A unit on part of the signal**: every rack unit but the limiter has a
  band select on its head — full, lows, mids, highs (the OTT's
  crossover) or mid / side. The unit works on that part; the rest passes
  and is summed back in phase. Live on the keys wherever the unit is.
  Nothing stored moves: a unit without a band is the unit it was.
- **Two warps at once**: each oscillator's warp block gains a second
  warp, bending the phase the first made (PD then mirror, a bend then
  quantize...), band-limited as one warp is. Off by default, so no sound
  changes; while it bends, the keys play from the server's render.
- **A third oscillator**: Synths' oscillator area gains osc 3 — its
  shape, level, octave, semitones, detune and its own warp — summed with
  the first two in every unison voice. Off at level 0 (its section folds
  away), so no sound changes; while it sounds, the keys play it from the
  server's render, not the live voice.
- **An arp pattern**: up to 16 steps, each on / off, velocity, transpose
  (tones of the loop's key), gate, chance and repeats, with a rate,
  direction (up, down, up-down, as played, random), octaves and a
  retrigger (per note, per bar, free) — one pattern both arps play: the
  grid arp's new "▦ pattern" mode renders it (the loop, a section, the
  MIDI file) and the keys arp's ▦ mode plays it live. An editor opens
  from the grid arp's ▦ on the Loops page and from the keys arp's ⚙,
  with 12 slots kept with the session. Nothing saved before moves.
- **A granular oscillator**: osc 1's "granular" pill plays the sampler's
  sample as a cloud of grains — position (drawable, a route target),
  size, density (up to 256 sounding), position and pitch spray, window,
  direction and scan — through the same envelope, filters and FX. The
  sampler panel draws where the grains read. Deterministic (every grain's
  draw a hash), so renders cache; the keys play it from the server's
  render, not the live voice.
- **The note grid holds 8 and 16 bars**: a take is placed on a grid as
  long as it was played (rounded up to 8 or 16 past 4 bars; 16 at 1/16,
  8 at 1/32), Edit shows and edits all of it, the bar chips and ×2 reach
  16, the roll scrolls rather than squeezing, and → Song makes one
  pattern of it ("Open in Perform" brings it back whole). The drum grid
  keeps 1-4 bars; nothing saved or rendered before changes.
- **Q "as played" keeps the timing**: below 100% each note of a take's
  loop sits on its nearest step with the rest of its timing kept inside
  it, and a held note ends where it was released — the loop plays back
  like the take, on the grid, in the song and in a bounce. 100% stays on
  the steps.
- **A loop sounds the same everywhere**: a test holds a loop's ▶, its
  Edit on the grid and its → Song section to the same notes, timing and
  sound (layers, FX, curves). One known gap: an effect's tail on a lane
  whose notes end early in a long loop is cut in ▶ and Edit but rings
  on in the song.
- **⏺ Rec runs to 60 s**, as a free take does (it stopped at 30 s): the
  length select offers 16 bars, and the take's audio is kept whole.
- **A long take reaches the song whole**: → Song on a loop recorded past
  its 4-bar grid lays out every bar played as sections in a row, each an
  editable pattern in the sound that played it. The take's toast, its row
  ("+n bars played") and Edit say what the grid holds.
- **Edit works from a following layer**: the loop opens on the main lane
  instead of being refused.
- **FX units have a power key**: the lamp before a unit's name bypasses it
  — it keeps its place, its settings and its automation lane — on every
  rack (Synths, Kit, Mix, Voice, a Song audio track, the vocal booth), in
  the render and on the live keys. One undo step.
- **Synths, tidied**: every module's head is one height with one
  lettering and one gap under it; seams no longer push a title down a
  pixel; FX units stand on one left edge with one gap beside and below,
  two a row in the wide layout; an empty rack is one line.
- **Unassigned macros are one line** of "Macro n ＋" keys until one has a
  target.
- **Sections at rest fold to their heading** (Tremolo at depth 0, the
  Vowel at amount 0, an LFO nothing routes…): a click opens one, and one
  you touch stays open.

## 0.1.0a1 — 2026-11-10

The first public release (alpha).

- **Synths**: oscillators (saw, square, triangle, sine, supersaw, noise, FM,
  4-operator FM, pluck, wavetables, a sampler with zones), filters,
  envelopes, LFOs, a mod matrix, macros, four layers and 47 FX units.
- **Kit**: synthesized drum kits and a step sequencer with per-hit
  velocity, chance, ratchet, nudge and tune; variations, fills, Euclidean
  rhythms, a mini-notation and 54 beat styles.
- **Loops**: note and drum grids, a chord lane, recording to an editable
  loop, quantise, a launcher.
- **Song**: a playlist arranger with automation, meter and tempo changes,
  a quantised clip launcher, recorded takes, and one-click song creation in
  18 styles.
- **Mix**: strips, returns and a master with live loudness, true peak,
  correlation, a spectrum and a goniometer.
- **Library, Breed, Map, Voice, Lab, Clips, Visuals**: browsing and
  breeding sounds, a vocoder and vocal booth, stem separation and chops,
  the clip hub, visualisers.
- **Control**: computer keys, pen, Web MIDI with MIDI learn, the Stream
  Deck + over WebHID.
- **Export**: WAV, FLAC, MP3, stems, MIDI and a one-file project.
