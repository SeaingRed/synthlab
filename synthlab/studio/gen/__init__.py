"""Song creation: the chord track, the generators, the styles and the
starters built from them, and the checklist that reads a song
(studio docs/songcraft.md).

- `chordtrack` — a song's harmony as data and its one reader, `resolve`.
- `notes` — bass, chords, pad, arp, lead and chops as note patterns.
- `drums` — beats, fills, builds and thinned variants as drum patterns.
- `styles` — the four styles as data: tempo, key habits, progressions,
  sounds by role, the kit, and the arrangement shape with its moves.
- `build` — a style (or the working loop) laid down as a whole song, and
  one part of it rolled again.
- `checklist` — what a song is missing, each finding with its fix, and
  each style's signature as machine checks.

Everything here is a pure function of plain dicts: no session, no I/O, no
rendering. The routes that commit what it makes are `routes/song_start.py`.
A generator is seeded per part with crc32 (never `hash()`, which differs
between processes), so a seed writes the same song everywhere.
"""
