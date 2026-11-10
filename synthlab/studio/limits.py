"""Every hard bound the studio enforces on untrusted input, in one place.

Task 9 extends this module later — keep it flat, named constants only, no
logic. Each constant names the one thing it bounds and, where it isn't
obvious, the route or module that enforces it.
"""

from __future__ import annotations

MAX_NOTATION_NODES = 4096  # visited nodes, rests and groups included
MAX_NOTATION_DEPTH = 4
MAX_LANE_CLIPS = 16
MAX_SCENES = 16  # a v2 file's scenes, as the v2 -> v3 migration reads them
MAX_STARRED = 64
# open GET /events streams (studio/events.py): one per visible page — a few
# tabs and a phone. Past it the route answers 503 and that page goes without
MAX_EVENT_STREAMS = 12

MAX_UPLOAD_BYTES = 24 * 1024 * 1024  # /clip/upload (jam capture)
# /voice/take (a recorded/dropped take), and the file of a sampler panel's
# ● Record sample (/fragment/sampler/record: its body is held to
# MAX_UPLOAD_BYTES, so a file over this one is a toast, not a bare 413)
MAX_TAKE_UPLOAD_BYTES = 12_000_000
MAX_PROJECT_UPLOAD_BYTES = 8_000_000  # /project/import: project.json (either form)
# /project/import of a bundle (GET /project.zip: project.json + samples +
# clips, studio/projectzip.py). One sample or clip is at most ~5.3 MB (30 s
# of stereo 16-bit at 44.1 kHz), so this is ~30 of them. It is also the
# path's BodySizeLimit cap (the route can't know which form it gets until
# it has read it), and the route holds a JSON body to the tighter cap above.
MAX_PROJECT_ZIP_BYTES = 160_000_000
MAX_PROJECT_ZIP_MEMBERS = 512  # entries in a bundle, project.json included
MAX_PROJECT_ZIP_UNCOMPRESSED = 200_000_000  # all entries, unpacked
MAX_CLIP_META_BYTES = 64_000  # one clips/<name>.meta.json in a bundle
# /fragment/pattern/import: a .mid file. A dense four-minute, sixteen-track
# file is ~300 kB; the track and event caps bound what the reader walks.
MAX_MIDI_UPLOAD_BYTES = 1_000_000
MAX_MIDI_TRACKS = 64
MAX_MIDI_EVENTS = 200_000

# --- the Sample Lab (studio/sources.py, routes/lab.py) -----------------------
# /lab/upload: a whole song as WAV / FLAC / MP3 / OGG. A ten-minute stereo
# 16-bit WAV at 44.1 kHz is 106 MB and the same as FLAC about 60; 80 MB takes
# any MP3 or OGG of that length, a FLAC of about twelve minutes and a WAV of
# seven and a half (the toast says to convert a longer WAV). Also the
# path's BodySizeLimit cap.
MAX_SOURCE_UPLOAD_BYTES = 80_000_000
MAX_SOURCE_SECONDS = 600.0  # a source's length once decoded (never the header's word)
MAX_SOURCE_CHANNELS = 8  # the first two are kept
MAX_SOURCES = 12  # sources in a data dir; past it, delete one first
# every source's mix + stems on disk: a 4-minute song is ~25 MB of FLAC, and
# its four stems as much again each
MAX_SOURCES_BYTES = 2_000_000_000
MAX_SOURCE_NAME_CHARS = 80
# a region sent anywhere is a clip or a sample, so their 30 s cap is its
# cap too (MAX_CLIP_SECONDS below, sampler.MAX_SAMPLE_SECONDS); an audition
# is cut to the same length rather than refused
MAX_LAB_REGION_BARS = 64
LAB_STRETCH_RANGE = (0.5, 2.0)  # a clip stretched to the session tempo
LAB_BPM_RANGE = (30.0, 300.0)  # a typed tempo (the detector answers 60..200)
LAB_LEVEL_DB_RANGE = (-24.0, 6.0)  # a stem's level in the region's mix, dB
# chops (studio/labchops.py): named spans of a source, kept in its meta
MAX_LAB_CHOPS = 64  # a source; Auto past it keeps the first ones
MIN_CHOP_SECONDS = 0.05  # a chop's length, and how close two edges may come
MAX_CHOP_NAME_CHARS = 60
# Auto by transients / syllables listens to this much of the region at most
MAX_LAB_AUTO_SECONDS = 120.0
# cleaning a chop (studio/labclean.py): the gate's and the trim's threshold
# under the chop's own peak, each fade, the gain
LAB_CLEAN_DB_RANGE = (-60.0, -20.0)
LAB_FADE_MS_RANGE = (0.0, 500.0)
LAB_GAIN_DB_RANGE = (-24.0, 24.0)

MIN_SR, MAX_SR = 8_000, 192_000

MAX_CLIP_SECONDS = 30.0  # moved here from clips.py; re-exported there
# A treated clip (the vocal booth's result): a whole clip's worth of take,
# plus the backwards swell's 1.5 s lead before it and the 4 s its chain may
# ring into after it (`booth.MAX_SECONDS`), so a 30 s take keeps its tail.
# The one clip that may be longer than MAX_CLIP_SECONDS; what reads clips
# back (the hub, `cliphub.read`) allows it.
MAX_TREATED_SECONDS = 35.5
# A free take (the keys' ⏺ free: free record, `routes/freerec.py`): played
# from its first note to ■, so a whole idea fits — 60 s (deliberately past
# the clip cap, 30). Its audio is a clip that long.
MAX_FREE_TAKE_SECONDS = 60.0
# the longest clip the store keeps (`clips.save_clip`) and reads back (the
# hub, a project bundle): a free take's
MAX_STORED_CLIP_SECONDS = max(MAX_TREATED_SECONDS, MAX_FREE_TAKE_SECONDS)
# A whole song. A club or trance arrangement is five to seven minutes. 480
# was measured, not assumed (tools/song_measure.py): the
# loudness-mastered export of a 480 s, 8-synth song had to
# fit 2.0 GB of peak RSS with `song_play.master` working in float32.
MAX_SONG_SECONDS = 480.0
# One section's cycle: what a section renders in one piece (float64 stereo,
# plus up to one cycle of tail per stem), so this bounds a render's memory.
# A cycle is usually the lcm of a few short patterns; it only gets long when
# it is the whole section (a take, or patterns that don't divide it).
MAX_SECTION_SECONDS = 120.0

# --- persisted-state caps (studio/schema.py) ---------------------------------
# The largest grid the UI can produce: 4 bars x 32 steps, for both the synth
# grid and the drum machine's own clock. A step past it can only be garbage;
# a step inside it is kept even when the *current* grid is smaller (a shrunk
# grid hides notes, it doesn't delete them).
MAX_GRID_STEPS = 4 * 32


def step_key(raw: str) -> str | None:
    """A velocity lane's key for the step a form field names (`vel.<raw>`,
    `dvel.<raw>`), or None when it is no step of any grid: the plain
    number under `MAX_GRID_STEPS`, as the schema keys the lane
    (`schema._step_key`). The lanes are read by `str(step)`, so a value
    kept under "03" or " 3 " is inert until a load renames it.

    "²".isdigit() is True but int("²") raises, so the key needs its own
    ASCII-digit check. And it needs a bound: any run of digits used to be
    stored, so every post could add keys for good, and one of 5000 digits
    — past what int() converts — made every later drum tool and bar
    change a 500 until the studio restarted."""
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 6):
        return None
    step = int(raw)
    return str(step) if step < MAX_GRID_STEPS else None


# Distinct pitches a lane can hold: the grid shows 6 rows (15 at its
# two-octave range), and notes written under an earlier root/scale stay
# hidden in the lane, so allow a few scales' worth. Measured: ~0.8 s to
# render 768 cells (6 rows), ~1.7 s for 2048.
MAX_GRID_PITCHES = 24
MAX_GRID_CELLS = MAX_GRID_STEPS * MAX_GRID_PITCHES
MAX_DRUM_CELLS = MAX_GRID_STEPS * 8  # 8 drum voices (state.DRUM_ROWS)
MAX_SONG_ENTRIES = 64  # a v2 file's scene running order (migration only)
MAX_CURVE_POINTS = 512  # per automated param
MAX_XY_GESTURE_POINTS = 300  # /fragment/xy/gesture enforces the same
MAX_BREED_TILES = 64  # the board shows 8
MAX_BREED_HISTORY = 5  # generations kept for "back"
MAX_TILE_SPECTRUM = 256  # a tile's spectrum sparkline (24 bands today)
MAX_NAME_CHARS = 200  # any free-text name persisted in state

# --- grid/tempo choices ------------------------------------------------------
# These used to be a literal duplicated in arrange.py's clamps, server.py's
# route-level clamps, schema.py's Choice/Num nodes, and three templates.
BPM_RANGE = (40.0, 200.0)
BAR_CHOICES = (1, 2, 3, 4)  # the synth grid's bars, and the drum bank slot count
# Same four values as STEP_CHOICES today, but a separate name: the synth
# grid's steps_per_bar and the drum machine's own drum_steps are two clocks
# (Randomise/Mutate once read the wrong one), and giving them one
# shared constant would make that mistake easy to reintroduce.
STEP_CHOICES = (4, 8, 16, 32)  # the synth grid's steps_per_bar
DRUM_STEP_CHOICES = (4, 8, 16, 32)  # the drum machine's own drum_steps

# the Synths layer strip's slots: the main lane and up to three more
LAYER_SLOTS = 4

# --- the song arranger (studio/song_model.py, schema.SONG) -------------------
MAX_SECTIONS = 32
# A trance arrangement wants a kick bus, a sub, a
# mid bass, a pad, a pluck, a lead, a gated pad and an FX lane: 8 synths at
# 420 s cost what 5 at 480 s do (measured: 7.3 s cold, 1.23 GB).
MAX_TRACKS = 12  # = the three per-kind caps below
MAX_DRUM_TRACKS = 1
MAX_SYNTH_TRACKS = 8
MAX_AUDIO_TRACKS = 3
MAX_SECTION_BARS = 64
MAX_SECTION_REPEATS = 16
MAX_PATTERNS = 128  # the shared pattern pool
# A pool pattern's bars (schema.SONG_PATTERN): the loop's own choices plus
# 8, when bars x steps a bar still fits MAX_GRID_STEPS (8 bars at 4, 8 or
# 16 steps) — an 8-bar progression, or a fill in bar 8, as ONE pattern. The
# Perform loop keeps BAR_CHOICES: its render is one piece.
PATTERN_BAR_CHOICES = (1, 2, 3, 4, 8)
# the Song page's playlist (fragments/song_arranger.html): pattern
# thumbnails one arranger fragment draws (a legal song is at most 400 bars
# — 480 s at 200 bpm — so 400 a lane and 4800 for twelve; an over-cap
# import past this is drawn as plain colour), and the numbers on its bar
# ruler (every 4th bar, the step doubled)
MAX_PLAYLIST_TILES = 6000
MAX_RULER_NUMBERS = 256
# --- song creation (studio docs/songcraft.md) ----------------------------------
# the chord track (studio/gen/chordtrack.py): slots in one progression
MAX_CHORD_SLOTS = 64
CHORD_BAR_CHOICES = (1, 2, 4, 8, 16)  # the length a progression repeats at
# returns (a shared reverb, a shared delay) a song's tracks send to
MAX_RETURNS = 2
# per-hit drum data beside a drums pattern's cells: at most one a cell
MAX_DRUM_HITS = MAX_DRUM_CELLS
# a jam's launch log (POST /song/jam/commit), and a range edit's cells
# (POST /song/cells): past either the post is refused whole, never cut
MAX_JAM_EVENTS = 512
MAX_RANGE_CELLS = 96
# a generator's seed is taken modulo this (routes/song_start.py)
SEED_MODULO = 1_000_000
STRUM_MS_RANGE = (-60.0, 60.0)  # a chord's notes apart, low to high (or high to low)
# long audio on the song's timeline (song_model.pass_cuts, studio/longaudio.py):
# a whole stem of a Lab source under the arrangement. Each item costs one
# ranged FLAC decode a stem a pass; its bar is far past any song under
# MAX_SONG_SECONDS; its start may sit one bar (at the 40 bpm floor) before
# the audio, for a Lab grid whose first bar line is a hair early.
MAX_LONG_AUDIO = 4
MAX_LONG_BAR = 2047
LONG_START_RANGE = (-6.0, 600.0)  # seconds into the source (MAX_SOURCE_SECONDS)
LONG_FADE_SECONDS = 0.005  # an item's own first and last samples
# note recording (routes/song.py record): note-ons per post (one loop wrap),
# the take undo ring, and the take window a post may name
MAX_RECORD_EVENTS = 512
MAX_TAKE_HISTORY = 16
MAX_TAKE_WINDOW_SECONDS = 3600.0
# a take's raw events kept for "✦ Clean up" (routes/song_record.py,
# takeclean.py): what one take holds across all its posts; the rest still
# records, the clean-up reads the first this many
MAX_TAKE_RAW_EVENTS = 4096
# mic takes: the per-machine latency calibration (studio_data/calibration.json)
MAX_LATENCY_MS = 500.0
MAX_TITLE_CHARS = 40  # a track / pattern / section name
# section automation (song_model.set_lane): lanes on one (section, track)
# cell, and breakpoints in a lane (the drawer writes two or three)
MAX_CELL_LANES = 4
MAX_LANE_POINTS = 16
MAX_MIGRATION_NOTE_CHARS = 600  # song.migration_note (v2 -> v3)

# --- the sound agent's log (studio_data/agent_log.json, schema.AGENT_LOG) ----
MAX_AGENT_LOG_TURNS = 50  # the newest kept; the drawer shows 12
MAX_AGENT_REQUEST_CHARS = 400  # agent/service.MAX_MESSAGE
MAX_AGENT_REPLY_CHARS = 1200
MAX_AGENT_LINE_CHARS = 300  # one change / note / error line
MAX_AGENT_LINES = 60  # changes or notes per turn

# --- the Voice lab's vocoder (studio/voice.py, voice_lab.json) ----------------
MAX_VOICE_NOTES_CHARS = 200  # the carrier's typed notes / chords
MAX_VOICE_CHORD_NOTES = 6  # notes of one typed chord that sound
VOICE_SHIFT_RANGE = (-12.0, 12.0)  # the formant shift, semitones
VOICE_SMOOTH_MS_RANGE = (10.0, 250.0)  # the band followers' release
VOICE_OCTAVE_RANGE = (-2, 2)  # a followed carrier against the sung pitch

# --- a keys take (the keyboard dock's ⏺ Rec, POST /take/keys: keytakes.py) ---
# the notes played during one take: 13 a second for a free take's 60 s (it
# was 400 for 30 s)
MAX_TAKE_NOTES = 800
MAX_TAKE_NOTES_CHARS = 100_000  # the notes' JSON as posted
# one take's notes sidecar (studio_data/takenotes/<clip>.json), read or in a
# bundle: the notes and the notes as played, compact, are ~50 kB at most
MAX_TAKE_NOTES_BYTES = 128_000
MAX_TAKE_BARS = 64  # a free take's 60 s (MAX_FREE_TAKE_SECONDS) at 200 bpm is 50 bars
# --- loops (studio_data/loops/, loops.py: Save as loop) ---------------------------
MAX_LOOPS = 200  # the store; a save past it is refused
MAX_LOOP_TAGS = 12  # a loop's tags (the Loops panel)
MAX_TAG_CHARS = 24  # one tag
MAX_LOOP_BYTES = 2_000_000  # one loop's file, read: its sound is a whole stack
# Perform's launcher (`perform_slots`): its rows, a
# scene each — 4 drawn while fewer hold anything, one more past the last
MAX_SLOT_ROWS = 8
SLOT_ROWS_SHOWN = 4

# --- your own wavetables (synthlab/wavetables.py, routes/wavetable.py) ---
# POST /fragment/wavetable/draw: up to four drawn cycles of up to 128 points
# each as text (its BodySizeLimit cap; the parser refuses past 16 kB itself)
MAX_WT_DRAW_BYTES = 64_000
MAX_WT_TABLES = 200  # tables in a data dir; a make past it is refused
MAX_WT_SOURCE_SECONDS = 20.0  # what of a clip a table is made from
WT_FRAMES_RANGE = (4, 64)  # frames a clip is sliced into

# --- the loop coach (Perform's ✦ Coach: loopcoach.py, loopchat.py) ---
MAX_COACH_SAY = 400  # one message typed to the coach, characters
# a spoken message (🎙, POST /fragment/coach/talk): a 16-bit WAV the browser
# made, refused past this (10 s of 48 kHz mono is ~0.96 MB); what is sent is
# its first 8 s from the first sound at 16 kHz (`clipmorph.audio_wav`)
MAX_COACH_TALK_BYTES = 2 * 1024 * 1024
