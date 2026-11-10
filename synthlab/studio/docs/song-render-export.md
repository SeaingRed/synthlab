# Song rendering, playback, export and MIDI

How a section becomes audio (`song_model.section_arrangement`,
`render.section_audio`), how the plan the player plays is the export
(`song_play.py`), loudness targets, stems, and MIDI / FLAC / MP3
export and MIDI import. The song's model and page are in `song.md`.
Index: `README.md`.

## Section rendering

**Section rendering** (`tests/studio/test_song_render.py`):
`song_model.section_arrangement` is the one bridge into the engine. It
builds on `section_loop` and gives `SectionParts` for ONE cycle.
`cycle_bars` is the lcm of the patterns when that divides the section's
bars; otherwise it is the whole section, and so is any section holding a
take, an automation lane or a trimmed block ("Block spans" below). Every
pass is therefore a whole cycle.
- Legacy-shaped sections stay `flat` False: the scene's own arrangement,
  key for key. Anything else is tiled onto one cycle-long grid.
- A flat cycle carries `breaks`, so each tiled repeat's first note
  attacks again (`arrange._merge_runs`), and `arp` (the whole pattern's
  pitches with a cycle-counted phase). A loop never sets either.
- `main_gain` is the main-lane track's gain, applied per note event;
  a layer's gain is too (`build_song` passes it to `song.notes`), and
  `RenderInputs.key()` salts `layer_gain_v` only when a layer's gain is
  not 1.0, so every gain-1 key stays as it was without the salt.
- `render.section_audio` returns the un-folded cycle plus its **tail**:
  what rings past the cycle's end, at most one cycle long. Takes are cut
  at the section's end. They are one shared stem (`clips`), except an
  audio track's with a rack or a lane in the section: those are a stem of
  their own, `clips:<track id>`, through the track's chain, which rings
  on like any stem's (`render._take_stems`; `song.md`, "An audio track's
  rack and lanes"). A section with one takes `_cycle_stems`.
- A flat cycle, or one past `MAX_LOOP_SECONDS`, renders its notes DRY
  in whole-bar chunks, with each note owned by the chunk it starts in.
  `_cycle_stems` stitches them and runs each stem's FX **once** over the
  whole cycle, because reverb, drive and compress aren't linear.
- `render_section(prev_tail=None)` folds its own tail (a loop); passing
  the previous section's tail gives the song's first pass.
- A section with an FX-parameter lane has **two** tails (`song.md`,
  "FX-parameter lanes"): `SectionAudio.tail`, the ring into its own next
  pass (the lane replays), and `.out`, the ring into another section
  (the unit at rest). `.handoff` is the one the next section's head
  folds (`out` when there is one, else `tail`); `svc._section_mix` reads
  it. Without such a lane `out` is None and nothing differs.
- `svc.section_result` caches in `svc.cache` under `section:` keys that
  carry the previous section's `section_key`.
- `StemCache` is byte-bounded (`STEM_CACHE_BYTES`: an eighth of the
  machine's RAM, 256 MB..4 GB, so a whole song's stems stay held and an
  edit renders only what it moved; `SYNTHLAB_STEM_CACHE_MB` sets it).
- A song's uncached sections render side by side on
  `render.section_pool` (`SongRender._warm`, before the gain's loop):
  sample for sample the serial song (`test_parallel_render.py`).
- No master FX or normalization run per section: both are song-wide.

## Chance: every pass afresh

**A pattern's chance lane and its timing inside a step render as the
loop's** (`song.md`, "Patterns carry timing and chance"). A
section whose notes patterns hold any chance (`song_model.has_chance`)
is **one cycle** — `cycle_bars` is its bars, as for a take, a lane or a
span — and **always tiled** (`section_arrangement` skips the native
path): `_flat` / `_tile_layer` tile the chance as `[chance, its step in
the pattern, where its pass starts]` (`arrange.chance_tile`), so each
pass of a pattern rolls afresh (`arrange.note_roll(..., at)`; the first
pass rolls what the loop's grid rolls) and the timing as keys
(`_tile_keys`). `window()` / `_crop_layer` crop both; a chance value keeps
its pass, so a chunk rolls what the whole cycle rolls. A note nudged
early on a chunk's first step plays on that step (`_crop_nudge`: a chunk
of a longer cycle is not a circle; the whole cycle as one window is, as
the loop is).

**Each of its `repeats` is a pass of its own** (`song_model.repass(parts,
j)`: every chance `j` cycles further on). `svc._song_idents` makes such a
section an `each` row, as long audio does — one WAV a pass (`pass_urls`
in the plan), `_song_raw` (the mixdown, the stems) stitching them; a pass
0 is the section's own key, so a song without chance keeps every key.
The song's MIDI file (`midi_bridge.song_file`) writes pass `i` through
`repass(parts, i)`. Seeded: the same song re-renders identically (the
same keys, the same bytes: `test_song_chance.py`). `RENDER_REV` needs
no bump: a song without chance keeps every key, and one with chance
keys it.

## Block spans: a lane tiled from its block's first bar

**Block spans** (`song.md`, "Block spans"; `tests/studio/
test_song_spans.py`): `section["spans"] = {track: [at, len]}` — the
cell's pattern plays bars `[at, at + len)` of each pass, restarting at
`at`. It lives in `song_model.py`; `render.py` has no part in it.

- **One cycle, the whole section.** `cycle_bars` returns the section's
  bars for any section with a span (a muted track's too: its block keeps
  its clock, so a stem export keeps the mix's cycle), and
  `section_arrangement` always takes the flat path for it, even where
  the patterns' own lengths would have made the native loop. It must
  therefore fit `MAX_SECTION_SECONDS`; `check_length`'s refusal says "a
  trimmed block plays it as one pass".
- **`_flat(..., where=)`**: `where` = {"main", "drums": (lo, hi) bars or
  None, "layers": [...]}, the block of each lane (None: the whole cycle).
  `_tile` / `_tile_keys` / `_tile_layer` take `lo` / `hi` in steps: the
  pattern is tiled from `lo` (its first step lands there), nothing lands
  at or past `hi` — so a held note is cut at the block's end — and
  `breaks` fall on every tile start after the first, so a repeat attacks
  again. `_tile_curve(..., lo)` restarts the main lane's sound curves at
  the block's first bar. Without `where` every one of these is the
  plain tiling, key for key: `section_key` moves only for a
  section that has a span.
- **The arp is gated in `arrange._lane_timeline`.** An arp plays the next
  pitch of its cycle on EVERY step of the grid, whether a step holds a
  note or not — so a trimmed arp cannot be silenced by leaving cells
  out. The arp tuple a section hands the engine, `(pitches, start_step,
  period)`, gains an optional `(lo, hi)`: with five values the arp plays
  only the cycle's steps `[lo, hi)` and counts its phase from `lo`.
  `SectionParts.window` and `_crop_layer` carry the gate into a chunk
  (they add the chunk's first step to `start_step` and leave the rest).
  The three-value form plays — and keys — as an ungated arp, so
  `RENDER_REV` needs no bump.
- **Downstream follows**: chunks (`window`), the stem cache, tails,
  `_song_idents`, the plan, the export, the stems, `midi_bridge.
  song_file` (no note outside the block, a trimmed arp included) and
  solo cell all read `section_arrangement`'s parts. Long audio is
  unaffected: `pass_cuts` walks sections and passes, and a span changes
  neither.
- **Exactness is pinned, not assumed**: a trimmed block against the same
  notes written by hand into a section-long pattern (`by_hand`: a loop of
  its own, with a cut where a repeat attacks again), at the 1e-9 the
  chunked-vs-one-piece tests hold — one piece, chunked, track FX, repeats
  and tails, a level and a field lane, a whole stem under it, solo cell,
  stems, MIDI. The measured differences are in `song.md`.

## The mixer: pan, sends, returns, levels

**The mixer's render** (`render._cycle_stems`, `song_model._with_mix`,
`studio/mixer.py`; `tests/studio/test_mixer_render.py`): a track's `pan`
and `sends` and the song's `returns` are applied to the FINISHED cycle
stems — past the stem cache, like a level lane — so moving one renders no
note and no chain.

- **Absent means absent.** `SectionParts.mix` is `{stem name: {"pan",
  "sends": {return id: level}, "gain"}}` and holds only what is set: a
  pan off 0, a send above 0 to a return the song has (and whose fader is
  up), the drums' level off 1 — and only for a track that sounds.
  `SectionParts.returns` holds only the returns such a track feeds.
  `section_key` adds `"mix"` / `"returns"` only when they are not empty,
  so a song that sets none (an older saved song included) keys and
  renders byte for byte as without them (the pinned key literals hold;
  `RENDER_REV` is 2).
- **A section with either takes the cycle-stems path** (`render.
  _own_stems`): it need not be flat — the renderer flattens it, the model
  does not — and its dry tracks are what the per-stem path gives (1e-9,
  pinned).
- **Order on a stem**: cache → level lane → the drums' fader → pan →
  sends. A synth track's `gain` is in its stem, before its chain. **A drums
  track's `gain` is a fader past the drum bus** (`mix["drums"]["gain"]`);
  an older saved song may hold one that never sounded, and such a song
  sounds different — under a new key, never the old one.
- **Pan** is `effects.pan`: equal power, and a stereo stem keeps its
  image (left x cos, right x sin, x sqrt 2: unity at centre).
- **A return** is one more stem, `return:<id>`: its input is each
  sender's WHOLE stem (the cycle and its tail) times its send level,
  summed; its chain runs once, from silence, over two cycles
  (`render._return_stems`), fully wet (`mixer.wet_chain`: every unit that
  has a `mix` at 1), times the return's `gain`. Its tail is cut at one
  cycle, like any stem's. For a linear return the fold onto the next pass
  is exact (a looped section == the middle of the same material rendered
  three times over, 1e-9), and two senders superpose (1e-9). A sender
  under an FX lane has two rings (`fxauto`), and so has its return.
- **The delay's direct term is taken out** (`render._wet`): the engine's
  delay line is y = x + feedback x y[-d], so its wet path at `mix` 1
  still carries its input undelayed (on the left only, ping-pong). A send
  to a delay return would have raised the dry track and pulled it left;
  the return subtracts the same unit with no feedback fed what it passes
  straight through. Exact, because the unit is linear.
- **Cache**: a return's stem is in the `StemCache` under `return:` — its
  senders' stem keys, their send levels and what is applied past the cache
  (lane, fader, pan), its chain, its gain, the cycle's length and the
  chain's clock. A send or a pan re-renders the return and no track.
- **Levels**: every section render records each stem's (peak, RMS) over
  the cycle (`SectionAudio.levels`, by stem name, before the master chain,
  at gain 1). `SongRender._keep_levels` files them by the section's key
  under the track ids (`SectionParts.tracks`; the shared takes are ONE
  stem, so every audio track in it with a take in the section shows the
  same level — one with a rack or a lane there has its own) and
  the return ids — `svc.stem_levels`. `/song/plan.json` gives each section
  its `levels: {id: [peak, rms]}` at the song's gain, once it has
  rendered; `svc.song_stem_levels(song)` is the same table and renders
  nothing. The Mix view's meters and Balance read it.
- **Stems export**: a stem is the song with every other track muted, and
  a muted track sends nothing — so each stem carries its own share of the
  returns, and the stems still sum to the mixdown (1e-9 with linear
  returns, pinned). There is no stem for a return on its own.
- Not built: a `send:<id>` lane (a throw), sends in the working loop
  ("Open in Perform" leaves them behind and does not say so yet).

## Song playback and export

**Song playback and export** (`song_play.py`, `routes/song.py`,
`tests/studio/test_song_export.py`): `/song/plan.json` lists each
section's `start_sample`, `start_bar`, `passes`, and two WAVs, `url`
(the first pass) and `loop_url` (every later pass). `/export/song.wav`
is `svc._song_raw`, which stitches exactly those arrays in
`section_timeline` order, so export == playback sample for sample, master
FX included. Keep it that way: never add a second tiling.
- **Master FX are part of each section's render**
  (`render.section_master_audio`): one run of the chain from silence over
  the cycle plus its tail, split at the cycle's end. The master tails fold
  like the stems' tails. The caveats are listed in its docstring: LFOs
  restart per section, and master tails over one cycle are cut. (The
  reverb's wet level is the room's own, `effects.reverb_ir`, not set per
  section.)
- **Gain** is one number per song (`song_play.gain_of`), from the
  loudest pass. It is a plain scale with no limiter, capped so no pass
  peaks over 0.97 (so `wav_bytes` never rescales a WAV on its own), and
  it is already applied to every WAV: the player must not apply it again.
- **Loudness targets are the export's alone** (`song_play.
  LOUDNESS_TARGETS`, `tests/studio/test_export_loudness.py`):
  `/export/song.wav?loudness=peak|streaming|club` (a whitelist; anything
  else is a 422). "peak", the default, is the mixdown above and nothing
  more. -14 / -9 LUFS run
  `song_play.master` on the finished mixdown: one gain from its measured
  integrated loudness (`analysis.loudness`, BS.1770), `fx.limit(loop=
  False)` at `MASTER_CEILING` (-1 dB), a trim if the *true* peak is still
  over it, and up to `MASTER_PASSES` gain corrections, each from the
  original audio. The response names what came out (`X-Loudness-LUFS`,
  `X-Peak-dBFS`, `X-True-Peak-dBTP`, and the file name);
  `GET /export/song/loudness` is the topbar's "measure" (the same render,
  as the `#song-loudness` readout; in `RENDER_OUTSIDE_LOCK`). The plan,
  its WAVs, the stems and every loop render never see a target: export
  == playback holds for "peak" only, by design. The topbar's Export
  group is a plain GET form (no script).
- **A new song starts on a head plan** (`/song/plan.json?head=1`,
  `song_head_gain`: the first section alone, at the gain of the first
  and the fullest section, while the rest renders): `songcraft.md`,
  "Sound in seconds". The plan proper, and export == playback, are
  untouched by it.
- **The loudness master works in float32 with one scratch buffer**
  (`song_play.master`, `_limit_into`: `effects._limit` as a one-shot,
  a piece at a time), the loudness reading K-weights a piece at a time
  keeping each hop's energy (`analysis.loudness.block_loudness`), the
  true peak converts a block at a time and `audio._pcm16` fills its
  16-bit samples a block at a time.
  Measured (`tools/song_measure.py --worst 480 --synths 8 --loudness
  streaming`, i9-14900K): 1.26 GB of peak RSS for the -14 LUFS export
  of a 480 s, 8-synth song. That is what lets `MAX_SONG_SECONDS` be
  480. Against a float64 master: 1e-7 of full scale, the same LUFS and true peak, at most one
  LSB in the 16-bit file. The "peak" export never goes through it.
- **Memos**: `svc.song_memo` holds a song's section idents and gain by
  content (`_song_content_key`), `svc.song_levels` holds each pass's
  levels (`song_play.levels`, one `core.scan` — measured by the
  `_section_mix` that renders the pass, which is also its NaN check;
  `_pass_levels` reads them back rather than measure again),
  `svc.stems` holds each section's master-processed cycle
  (`_master_audio`, keyed by `section_key` + the cleaned master chain;
  read-only arrays, one master run per section across the plan, its WAVs
  and the export), and `clips.clip_hash` is memoized by file stat. Fetching a
  plan's WAVs re-keys nothing and reads no take twice.
- **Caps** (`song_play.check_length`: `MAX_SONG_SECONDS`, and
  `MAX_SECTION_SECONDS` on a section's cycle) are checked from the
  song's numbers before anything renders: in the plan, the section WAV,
  both exports and `section_result`.
- **Stems** (`song_play.stem_songs`): one per sounding track, named
  `drums.wav`, `synth-N.wav` and `audio-N.wav`, where N is the track's
  place among its kind, never user text. A stem is the whole song with
  the other tracks muted (a muted track keeps its cells and pattern
  clocks; `section_loop` drops a muted drums track's hits too), so each
  section keeps its cycle and the stems line up with the mix. They play at the song
  gain, without master FX.

## Long audio: the pass asks for its slice

**Long audio** (`song.md`, "Long audio"; `tests/studio/test_song_long.py`)
is the one thing in a song that is not a section's: a stem runs linearly
across passes, so **under it no two passes of a section are one WAV**.

- `song_model.pass_cuts(song)` (pure; the plan's own arithmetic,
  `cycle_samples` / `bar_sample`) gives, for each section some audible
  item crosses, one tuple of **cuts** per pass: `{source, model, stems,
  levels, frm, a, b, gain}` — `frm` the source sample at the pass's first
  sample, `[a, b)` the item's own extent relative to it, `gain` the
  item's times its track's (capped at 1.5, as a take's). A muted item, or
  one on a track the mix silences, gives none.
- `SectionParts.under` carries one pass's cuts. `render.section_audio`
  adds them onto the cycle after its stems (`longaudio.under`): each
  stem's range decoded from its FLAC (**never a whole stem in memory**),
  weighted by its level and summed in float — not `sources.read_sum`,
  whose rescale of a boosted sum is per read and would step the level
  between passes. Dry, **no tail**: the next pass reads the next sample,
  so a seam is sample-continuous in the export. The item's own first and
  last 5 ms are faded (`LONG_FADE_SECONDS`), at its start, its end, and
  the song's end when that cuts it. The master chain runs over it, per
  pass, with the caveats it has per section. A section keeps its
  `cycle_bars`.
- **Keys.** `section_key` adds `"under"` = `longaudio.keyed(...)` only
  for a pass that plays long audio: the cut and each stem file's size on
  disk (None when missing). A pass without long audio — every pass of a
  song without an item — keeps its key byte for byte, so `RENDER_REV`
  needs no bump. `_song_content_key` adds what is on disk
  (`longaudio.sig`) only for a song that holds an item, so a source
  deleted or separated again is another memo entry.
- **Idents.** `svc._song_idents` gives `passes[i] = (head, loop)`
  for every section; `each[i]` — only for a section under long audio —
  is one (ident, parts, previous parts) per pass: pass 0 is `head`, pass
  j folds pass j-1's own tail (`"same"` in its ident: `SectionAudio.tail`,
  not `.handoff`), two neighbouring passes that came out alike fold as a
  plain loop, `loop` is **pass 0's slice folded onto itself** (the
  player's ⟳ Loop must not walk through the stem), and the next section
  folds the LAST pass. `song_gain`, `_song_raw` (so the export, the
  loudness masters and the stems) and the plan read `each`.
- **The plan** gives such a section `pass_urls` (one URL a pass,
  `pass_urls[0]` is `url`), absent otherwise;
  `/song/section/{id}.wav?p=N` serves pass N (`section_result(at=)`; past
  the last pass: a 422). song.js plays `pass_urls[pass]` when not
  looping, prefetches them all, and keys its "replay the slot's last
  buffer" fallback per pass (`entry().own`): the wrong slice of a vocal
  is worse than a late one. `song-changed`'s section list reads every
  pass's ident.
- **Stems export**: `audio-N.wav` holds the track's long audio (the song
  with the other tracks muted; `song_play._sounds` counts an
  unmuted item). **Solo this cell** mutes the audio track with the rest:
  no long audio under it. **MIDI**: nothing.
- **Cost**: an empty section is a 1-bar cycle, so a song that is only a
  stem is one WAV a bar.

## A range loop: the wrap is one more head

**A range loop** (`static/song.js`,
`songsel.js` `songRange`, `svc.section_result(after=)`;
`tests/studio/test_song_arr.py`). Looping one section plays its
`loop_url` every pass, the section folded onto itself. Looping sections
[first .. last] plays the plan's own passes in order — each section's
`url` (its head: the section before it rings in), then its `loop_url` /
`pass_urls` — and after the last section's last pass goes back to the
first. **Only that one pass is new**: the first section's first pass *as
it sounds straight after the last section*, with the last section's ring
folded into its head instead of its own neighbour's.

- **`GET /song/section/{id}.wav?after=<section id>`**
  (`section_result(after=index)`): the head ident with `fold` = the last
  pass of `after` (under long audio, its own last slice), its
  `SectionAudio.handoff` folded in by the one `fold_tail` every head
  uses. **Nothing renders that the plan did not**: both sections' cycles
  are in the stem cache; the fold is an add. It is cached under its own
  `section:` key and is in no plan, so a song that loops no range keeps
  its plan, its URLs and its keys byte for byte.
- **The seam, measured**: the loop's last pass and the wrap pass, end to
  end, against the same stretch of the song that plays the two sections
  twice — one signal to a step of the WAVs' 16 bits across the wrap as
  everywhere else, and the fold itself against that song at gain 1 to
  1e-9 (`test_the_seam_of_a_range_loop_is_the_songs_own_boundary`,
  `test_the_wrap_pass_is_the_section_after_its_neighbour`). So the wrap
  has no step the song would not have with those two sections side by
  side — which is not a promise that the join is musical. **Nobody has
  listened to one.**
- **Gain**: the song's one gain is found without the wrap pass. One that
  would peak over `song_play.CEILING` is scaled down to it (a wrap never
  clips; it may sit a fraction of a dB low) — not met in any test song,
  where the wrap peaks with the loudest pass.
- **The schedule** (pure, under node): `loop` is false, true (one
  section) or `[first id, last id]`. `advance` asks
  `songRange.wrap` where a range goes when a section ends — inside the
  range, and one section before it, the song just plays on; from its
  last section, or from anywhere else, to its first with `after` set —
  and `entry` names that pass's WAV (`songRange.url`: the two sections'
  render keys joined, so an edit of either is a new URL). A range whose
  end is gone is no range. The wrap WAV is prefetched with the range's
  passes and kept across plan refreshes (`songRange.urls`).
- **A jump** (`songClock().jump(sid)`): the player's `retime(to)` — as a
  loop toggled mid-play does — takes over at the next pass start
  from section `to`; nothing queued past it survives. While stopped it
  only moves the selection and the marker.
- **Not done**: a loop of bars rather than sections (cut first); the wrap
  under an FX-parameter lane has the "ring into another section" caveat
  every head has (`song.md`, "FX-parameter lanes").

## Meter and tempo in the render

A section's `SectionParts.arr` carries its `meter`, its tempo (`bpm`) and
its `ramp` ([to, over beats, at beats]; `song_model.section_arrangement`).
`parts.clock` (`meter.arr_clock`) is what `section_audio`'s length,
`_cycle_stems`' chunk offsets, `_with_lanes` and the warm-up's size read;
`bar_seconds` is a bar's longest under a ramp, so a chunk never passes
the cap. Under a ramp every note and hit is placed through the clock
(`arrange.TempoSong`), a note's length is the clock's from where it
starts, every lane takes the time-ordered path, and a lane drawn in bars
is read in bars (`meter.warp`). The FX still run at the section's opening
tempo (synced LFOs, delays, a pump); the sidechain's kicks follow the
ramp. A stem key carries the meter and the ramp only when set
(`render._bar_clock`).

## MIDI out and in, export formats

**MIDI out and in, export formats** (`studio/midi_bridge.py` over the
engine's `synthlab/midifile.py`; `tests/studio/test_midi_export.py`,
`test_midi_import.py`, `test_export_formats.py`):
- **Export is the schedule, never a second reading of the grid.**
  `arrange.note_schedule` is what `build_song` schedules for the synth
  lanes (playing steps only — nothing hidden past a shrunk grid — held
  runs, cuts, breaks, swing, velocities, the arp, a chord's notes
  apart by the lane's `strum`), held for `arrange.NOTE_GATE`; `arrange.drum_hits` is the beat `_schedule_drums`
  places (one seeded probability / humanise roll for audio and file).
  A scheduling change belongs in those two, and the file follows.
- `GET /export/loop.mid`: Perform's `render_inputs` — a track per synth
  lane that plays (edited lane, then layers; followers resolved), the
  beat on channel 10 at General MIDI keys (`GM_DRUMS`), each clock tiled
  over `loop_bars`, only the parts the loop carries. `GET
  /export/song.mid`: one track per sounding drums/synth track
  (`song_play.stem_songs`, so the stems' tracks), sections end to end,
  one cycle per `section_timeline` pass, read through
  `section_arrangement` exactly as a stem renders (a chunked cycle's
  beat is rolled per `section_windows` chunk, as the audio is); the
  song's length caps apply. No notes: 404. Not exported, on purpose:
  slides (a plain note — a bend would move every note on the channel),
  the open hat's choke, audio tracks, FX. A drawn `pitch_bend` curve on
  the main lane is written as the wheel with `bend_range` as RPN 0.
- **Meters and tempos**: the loop's file writes its time
  signature; the song's writes the first section's tempo and meter and
  every change after (`midi_bridge._song_clock` -> `MidiFile.tempos` /
  `.meters`): a section in another meter, at another tempo, and a ramp as
  a tempo a beat (each at the tempo half-way through it, so the file's
  beats last what the render's do). A 4/4 song at one tempo writes
  neither. The reader keeps every change; an
  import takes the file's time signature as the loop's meter when the
  studio has it (`file_meter`, `set_meter` first: the toast says so), and
  never its tempo (the grid is in beats).
- `POST /fragment/pattern/import` (`@pattern_edit`: one Ctrl+Z):
  the first non-percussion track with notes (form `track` = n-th with
  notes; no UI for it) onto the note grid — quantised to its steps,
  moved by whole octaves to where most notes meet the grid's rows
  (`_best_octave`), clamped to rows and bars with the toast counting
  what was dropped; replaces what the grid shows, keeps what it hides.
  The file is untrusted: `MAX_MIDI_UPLOAD_BYTES` (the path's
  `BodySizeLimit` cap and the part's), `MAX_MIDI_TRACKS` /
  `MAX_MIDI_EVENTS` in the reader, any exception a 422 toast with the
  grid untouched. The form sits in Perform's header, outside
  `#transport` — inside it, the file input's `input` event would post
  the old grid over the import.
- **`?format=wav|flac|mp3`** on `/export/song.wav` and
  `/export/stems.zip` (`audio.EXPORT_FORMATS` is the whitelist, checked
  before anything renders; `audio.encode`). `wav` is the bytes of no
  format at all.
  FLAC and MP3 encode the WAV's own 16-bit samples through `soundfile`
  (the `studio` extra, imported lazily: without it WAV still works and
  the others answer a 422 toast). MP3 is fed floats: libsndfile 1.2.2
  turns int16 input into noise. The Song transport's export group is
  the only UI for any of these (plain links). The stems zip is
  deterministic: its entries go through `files.zip_write` (fixed date
  `ZIP_EPOCH`, no wall clock — `writestr(name, ...)` stamped "now" at 2 s
  resolution), so the same song exports the same bytes every time;
  extracted stems are dated 1980-01-01.

## One cell alone

`GET /song/cell/{section}/{track}.wav` (the drawer's "solo this cell",
`song.md`) is the stems idea for one section: `song_play.solo_song`
mutes every other track and the same `section_result(..., loop=True)`
renders it, at the mix's `song_gain` and without the master chain. It
is `@render_outside_lock` like the section WAVs, and its key is the
solo song's own, so the plan's URLs and the section's cached passes are
untouched by it.
