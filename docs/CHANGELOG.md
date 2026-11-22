# Changelog

## Unreleased

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
