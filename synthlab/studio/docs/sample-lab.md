# Sample Lab (`routes/lab.py`, `sources.py`, `lab.py`, `labchops.py`, `labclean.py`, `labmake.py`, `labjobs.py`)

The Lab view: whole-song import, tempo / bars / key, stem separation,
chops (cut, cleaned, kept as clips), whole stems out, and what a region
or a stem's region becomes. Index: `README.md`.

View `/view/lab` ("Lab" in the nav): import a whole song, read its tempo,
bars and key, separate it into stems, cut regions out as clips and sampler
sounds — and, from a stem, a playable voice, vocal chops, a drum pattern,
a kit, or a take on the song. Engine halves: `synthlab/analysis/rhythm.py`
+ `key.py` + `voiced.py` + `drumhits.py` (the root `docs/engine.md`) and
`synthlab/stems.py`. Tests: `tests/studio/test_lab.py`,
`test_lab_stems.py`, `test_lab_make.py`, `test_lab_chops.py`, `test_lab_clean.py`, `tests/test_stems.py`,
`tests/test_song_analysis.py`, `tests/test_voiced.py`,
`tests/test_drumhits.py`, the journey `tests/browser/test_lab.py`; the
audio they use is rendered by the engine (`tests/songs.py`). Opt-in, with
the real model and a real song: `tests/test_lab_real.py`.

## A third audio store, on purpose

**A third audio store, on purpose** (`sources.py`,
`<data_dir>/sources/<id>/`): `mix.flac` (stereo 16-bit at SR),
`meta.json`, `mix.peaks.npy` and `mix.rms.npy` (the waveform's outline and body), and `stems-<model id>/<stem>.flac` once
separated. Clips and samples are capped at 30 s and hashed into render
keys; a source is minutes long and is **no render input of its own**: it
is in no `RenderInputs`, no `state.json`, no schema node, no cache key
(`test_an_import_touches_no_session_state_and_no_render_key`) — until
the SONG places a whole stem of it ("A whole stem onto the song", below:
then the song names the source by id, and the passes that play it carry
it in their keys). The Lab
keeps no session state at all — a source's tempo override, bar-line
nudge, region, picked stems, levels and chops live in its own meta — so the Lab's page
has no entry in `REGIONS` beyond the reset menu every page draws, and
every POST answers with `#lab-body`. Ids are content hashes (SHA-1 of
the decoded 16-bit PCM, 16 hex: the sampler's rule), so the same song
twice is one source and its stems are found again.

## The upload is hostile

**The upload is hostile** (`sources.decode`): libsndfile via `soundfile`
(WAV, FLAC, MP3, OGG — the `studio` extra, imported lazily; without it
the import box is disabled and says why), read from memory in 64k-frame
blocks and stopped at `MAX_SOURCE_SECONDS` whatever the header claims
(an MP3's frame count is an estimate, a WAV's can lie). More than two
channels keep the first two; other rates go through `resample_poly`;
a peak over 1 (MP3 decodes do that) is scaled down, not clipped.
**Every** failure is a `SourceError` -> 422 toast, including
libsndfile's own exceptions (`except Exception`, deliberately). The
browser's file name goes through `files.safe_name` (`Upload.name` is new
on the upload dependency). `meta.json` is a file anyone can edit:
`sources.clean_meta` holds every key to its type and range before the
page draws it. `/lab/upload` is `@render_outside_lock` (seconds of
decode + analysis that touch only the store, which locks itself).

## Limits

**Limits** (`limits.py`): `MAX_SOURCE_UPLOAD_BYTES` 80 MB (also the
path's `BodySizeLimit` cap, `security._PATH_UPLOAD_LIMITS`),
`MAX_SOURCE_SECONDS` 600, `MAX_SOURCE_CHANNELS` 8, `MAX_SOURCES` 12,
`MAX_SOURCES_BYTES` 2 GB (mix + stems of every source), `LAB_BPM_RANGE`
30..300 for a typed tempo, `MAX_LAB_REGION_BARS` 64, `LAB_STRETCH_RANGE`
0.5..2. A region sent anywhere is held to `MAX_CLIP_SECONDS` (30 s: the
clip store's and the sample store's cap) with a toast naming its length;
an audition is cut to 30 s instead. Chops and cleaning: `MAX_LAB_CHOPS`
64 a source, `MIN_CHOP_SECONDS` 0.05, `MAX_CHOP_NAME_CHARS` 60,
`MAX_LAB_AUTO_SECONDS` 120 (what Auto by transients / syllables listens
to), `LAB_CLEAN_DB_RANGE` -60..-20 (gate and trim), `LAB_FADE_MS_RANGE`
0..500, `LAB_GAIN_DB_RANGE` -24..+24. A whole stem is only ever
downloaded, so it is bounded by `MAX_SOURCE_SECONDS` alone.

## A meter guess

`analysis.rhythm.tempo` also says whether a song sounds in three or four
(`Grid.meter_guess`, `meter_sure`: the beat-by-beat harmony change and
low-band attack correlated with itself one and two bars on, for a bar of
three and of four). The panel says "sounds in 3" beside the tempo; the
grid still counts four a bar. A cheap read: no per-bar tracker.

## Grid and region

**Grid and region** (`lab.py`): `grid(meta)` is the one place that
decides the tempo and first bar line in force (the user's, else the
analysis'). A first bar line a few ms *before* the audio starts is kept
negative (`EARLY`), not wrapped to "one bar in". A region is stored as
set — `bars` (start bar, length in bars) or `seconds` — and `span(meta)`
turns it into seconds, so a bars region follows a tempo correction.
`/lab/region`'s fields are named by unit (`bar`/`bars` vs
`start`/`length`): a `mode` posted without its own numbers is the snap's
bar | beat radio and converts the region. **Every time the page posts is
seconds of the song** (`drag_a`/`drag_b`, an edge's `at`, a chop's `at`
— never seconds from the start of the span drawn, which the page zooms
on its own).

**A drag snaps** (`lab.drag_region`): in bars mode to the bars it
touched; in seconds mode (the snap reads "beat") each end to the
nearest **beat** line of the grid in force — a drag inside one beat is
that beat, never nothing — and with `free=1` (Alt held: lab.js sends it)
or no grid, where it was let go. **A drag picks the lane it was made on**
(`lane`, `lab.pick_lane`): the mix lane is the recording (no stem
ticked), a stem's lane that stem alone, and with `add=1` (Shift held)
that stem beside the ones ticked; a drag on the ruler sends no lane and
leaves what plays alone. **An edge** of the region (`edge` start | end,
`at`: `lab.move_region_edge`) lands on the nearest bar line in bars, the
nearest beat line in beats, where it was let go with `free` (which takes
a bars region into seconds); it never crosses the other edge (a region
keeps a bar, a beat or 50 ms). **The keys** (`step` move | grow, `by`:
`lab.step_region`): move by beats — four (Shift+←/→: a bar) keeps a bars
region in bars, one takes it to beats, since it is no longer on the bar
lines — and grow / shrink by steps of the snap (a bar in bars, a beat in
beats), never under one step, always inside the song. The **one-press
lengths** (`lab.REGION_LENGTHS`: 1 2 4 8 16 bars, in bars mode) post
the region from its own first bar.

**The region says where the sample comes from** (`lab.region_label`,
`lab.pick_name`): the selection bar writes "vocals · bars 3–5 · 3 bars"
(or its clock times and length in beats when it is not on the bar
lines), the ruler draws it as a band with a handle each edge, and
**what is made from it is named for what it plays and where** —
`pick_name`: "vocals bars 3-5", "drums-bass bar 2", "mix bar 5.3 (6
beats)" off the bar lines, "vocals 12.5s (3.0s)" with no grid, in a clip
name's characters (`files.safe_name`: no "+", no en dash). It is the
default name of a clip (`/lab/send`), a take (`/lab/place`) and a chop
cut from the selection (`/lab/chop op=add`); a typed name (the make
module's field) still wins.

**Three tempos meet here, so each is named for whose it is**:
the loop's ("fit to the loop's 124 bpm"; the drum pattern's "the loop
takes this song's 120 bpm (the loop's is 124)"), the Song page's ("Stretched
to the Song page's 116 bpm"; "the Song page takes its 119.97 bpm") and
this recording's — never 124, 120 and 116 bare. The tempo bar's
button back to the detected tempo is "↺ reset" (the readout beside it
already says "detected 119.97 bpm", so the button does not).

## Zoom: the page's at once, the server's drawn

**The page zooms at once; the server draws the lanes sharp over it**
(Ctrl + wheel into the stems). The span the lanes show is the
source's own `view` in its meta (`lab.view` / `set_view`, x1 .. x512 and
never under a second), and the server draws every lane over it, its
peaks and RMS read from the audio itself (`sources.window_columns`,
cached by file and span — the stored 1200 columns are 75 at x16).
`#lab-wave[data-view-start|data-view-seconds]` say which span that was.

- **Ctrl + wheel** over the lanes (a trackpad's pinch is the same event
  in Chromium) zooms round the pointer — the moment under it stays under
  it — and **Shift + wheel** or a sideways wheel scrolls along the song;
  a plain wheel scrolls along it too while zoomed, unless the lanes
  themselves have height to scroll. A wheel anywhere but on the lanes is
  the page's own. labview.js does it **at once**: each lane's SVG, drawn
  over the server's span, is stretched to the page's span with a CSS
  transform (`translateX` + `scaleX`, origin left) and every marker is
  placed again; 220 ms after the wheel rests the span is posted
  (`POST /lab/view action=set start= seconds=`, `all` when it is the
  whole song), and the answer morphs in lanes drawn sharp over it. A swap
  that is not the page's own zoom (a button, a region post) hands the
  page the server's span again.
- **The overview strip** over the ruler is the whole song's mix
  (`lab.strip_svg`: the stored overview, the region and the chops on it)
  with a box on what the lanes show: drag the box to scroll; a press
  outside it looks there first.
- **The corner's buttons** (◀ − + ▶ ⇔ all: `POST /lab/view` in | out |
  left | right | region | all) are the keyboard's and the pen's way; in /
  out keeps the region in sight.
- Zooming changes no snap rule but the chops' (`labchops.snap`: within
  1% of the span shown).

## Key

**Key** (`lab.key_block`, `better_key`, `reread_key`, `POST /lab/key`):
after a separation the key is read again from every stem but the drums,
with the bass stem asked which of the key and its relative it is
(`analysis.key`), on the grid in force. **A re-read never replaces a
surer key** (`better_key`: confidence, or — between relatives, which
share their scale — the larger `key_margin`): the phase-2 re-read turned
the one real song's right D minor into F major. The panel shows the
confidence, what decided ("the bass line picked D minor over F major")
and a 24-key menu to name the key by hand (`meta.user.key`; "read again"
re-reads a song separated before this). The key only labels (a clip's
`lab.key`) and hints: a sampler sound made from a region with no sure
pitch of its own takes the tonic as its root (`lab.tonic_midi`).

## The Lab's islands: lab.js, labview.js, labchop.js

**lab.js** (island 20, <= 130 code lines) is picking a sample: a drag on a lane (`drag_a`/`drag_b` in
seconds of the song, `lane`, `add` with Shift, `free` with Alt; its band
follows the pointer snapped as the server will snap it, on the lane it
selects), a drag of one of the region's edges (`.lab-sel-edge` on the
ruler: `edge`, `at`), a click without a drag (that lane from there into
`#lab-scrub` — the region's WAV route with the lane's stem and level
from that point, 30 s; on the ruler, what the region plays; Esc stops it
and lets the stream go), the keys on the focused lanes (← → a beat,
Shift a bar, `[` `]` a step shorter / longer — a run of presses is one
post — Enter clicks `#lab-keep`) and a file dropped on the import box.
Its pointer is captured only once a press has moved, so a click and a
double-click keep their own target. A press on a label, a button, a
link, a field, a chop's marker, the separation call-out (`.lab-cta`),
the overview or a grip is not a selection.

**labview.js** (island 65, <= 200 code lines) is the lanes as a window
on the song ("Zoom", above): Ctrl + wheel, Shift + wheel, the overview's
box, the stretch of the lanes until the sharp ones land, every
`.lab-at[data-at]` marker placed from its time (`data-to` for a band),
the ruler and the lines ("UI", below), the playhead of whichever Lab
`<audio>` plays (`data-from` + its time), the pointer's line with its
bar.beat and clock on the ruler, and the lanes' height. It hands lab.js
and labchop.js `synthlab.lab`: `at(ev)` (the song's time under the
pointer), `pct(t)`, `view()`, `data()`, `place()`.

The audition is the shared `.player` (`frontend.md`, "Components": a
play button, a seek bar, the time and — `.player-loop`, bench.js, any
player that has one — loop on / off over a control-less `<audio>`) on
`GET /lab/<id>/region.wav?start=&length=&levels=&stems=` (lock-free,
clamped, `no-store`; the cross-site read rule applies); its ▶ is the
page's **Space** (`data-keys="Space" data-player="media"`: the
transport's, `frontend.md`). **The Lab's audition is looped by default**
(`<audio loop>`, the button pressed): a sample is heard round and round,
its seam with it; a swap that redraws the player loops again. A paused
click-to-hear is let go (`transport.release`), so Space then plays the
selection rather than resuming it.

## Chops

**A chop is a named span of one source with its own mix and its own
cleaning** (`labchops.py`, pure functions on a meta; `tests/studio/
test_lab_chops.py`):

```
{"id": "c1a2", "name": "verse 1", "start": 8.512, "end": 12.38,
 "stems": ["vocals"], "model": "htdemucs-…", "levels": {"vocals": -3.0},
 "clean": {...}, "notes": "", "clip": ""}
```

- **Data in the source's meta**: `meta["chops"]`, in time order, never
  overlapping, and `meta["chop"]`, the one the region was last set from.
  Held to type and range by `sources.clean_meta` (`_clean_chops`) exactly
  as `region` / `view` / `levels` are: a wrong type, a span outside the
  song or under `MIN_CHOP_SECONDS`, a repeated id, an overlap, a stem its
  set does not make, a `clip` that is not a `clip_safe` name — dropped or
  clamped, never a 500; only the first `4 x MAX_LAB_CHOPS` entries of a
  hostile list are looked at and 64 kept. A meta from before chops loads
  with none. Ids are 4 random hex, unique in the source. **Still no
  session state, no schema node, no render key**
  (`test_chops_and_a_kept_chop_touch_no_session_state_and_no_render_key`,
  and the import test in `test_lab.py`).
- **Seconds, not bars**: a chop does not follow a later tempo correction
  (a region in bars does). The grid only snaps.
- **A chop keeps its own mix**: `stems` of the stem set `model` at
  `levels` (dB, sparse) — the lanes' ticks and sliders when it was made,
  or when its row's "use the lanes' mix" was last pressed
  (`labchops.lane_mix`). Its audition and its clip read **that**
  (`labchops.audio` -> `sources.read_sum(sources.with_model(meta, model),
  stems, levels=)`), never the lanes at the moment of pressing, so
  "Update clip" makes the same mix again. `stems` empty is the recording
  itself (`mix.flac`). **A chop of the other stem set still plays** while
  that set is on disk (`sources.models_on_disk`; a song keeps both); when
  it is gone the row says "needs the N-stem separation", its ▶ / Keep are
  disabled, the audition is a 404 and Keep a toast (`labchops.needs`) —
  never played from the wrong set.
- **Selecting** a chop (`select`) makes it the region — in bars when it
  sits on bar lines (the drum reading wants a region in bars), else
  seconds — and sets the lanes from it: `picked`, its stems' levels, and
  its stem set brought up when that is not the one showing. "The selected
  chop" is derived, not stored twice: `lab.selected_chop` = `meta["chop"]`
  while the region still is its span, so dragging a new region unmarks
  it; moving the selected chop's edge re-frames the region
  (`labchops.frame`).
- **`move_edge` is the sampler's slice rule** (`sampler.nudge` /
  `start_bounds`), for both edges: the edge is held `MIN_CHOP_SECONDS`
  inside its own chop's other edge (which wins); a neighbour that
  *touched* it follows and is itself never made shorter than that; a
  neighbour that did not touch is a wall.
  `test_an_edge_rule_matches_the_samplers_on_the_same_seam` runs both on
  one seam.
- **Snap is the server's** (`labchops.snap`): an edge or a cut lands on
  the nearest **beat** line when that line is within 1% of the span the
  waveform shows (`lab.view`), from the grid in force (`lab.grid`) — so
  zooming in makes the snap finer. `free=1` (Alt held, and every arrow
  nudge) bypasses it. The island sends positions in seconds of the song,
  as lab.js's drag does.
- **✂ Chop** on the selection bar (`op=add`) cuts the region itself as a
  chop, named for it (`lab.pick_name`: "vocals bars 9-12"), with the
  lanes' mix, and marks it (the region is that chop); one that runs into
  a chop is cut back at it, one that starts inside a chop is refused with
  a toast.
- **Auto** (`labchops.auto`) fills the region, **replacing only the
  chops wholly inside it** (one that straddles the region's edge stays,
  and a new span is cut back at it): `bars` / `beats` from the grid
  (`labchops.grid_spans`: `lab.chop_slices`' line walk, in seconds; a bar
  is named by its number in the song), `transients` a chop from each hit
  to the next (`drumhits.find_hits`), `syllables` a chop a span
  (`voiced.syllables`) — the last two on what the region plays, its first
  `MAX_LAB_AUTO_SECONDS`. Syllables is offered only when the vocals stem
  is what the region plays (`picked == ["vocals"]`). New chops take the
  lanes' mix. Past `MAX_LAB_CHOPS` the first ones are kept and the page
  says how many did not fit (`#lab-said`). Nothing found: a toast, and
  the chops that were there stay.

**Routes** (all answer `#lab-body`):

| Route | Does |
|---|---|
| `POST /lab/chop` | `op` split \| add (the region) \| auto (`rule`) \| move (`edge`, `at`) \| remove \| rename (`name`) \| select \| lanes \| clean (the settings) — one meta change each. `@render_outside_lock`: Auto by listening and a cleaning's notes read seconds of audio, all of it the source store's |
| `GET /lab/{sid}/chop/{cid}.wav` | the chop's own mix, cleaned, cut at 30 s. `@lock_free`, `no-store`; the page's URL carries `?v=` (a hash of the chop and the tempos) so the key cache never plays a stale one |
| `POST /lab/chop/keep` | bake to the clip store (below) |

One `op` route rather than eight: a new POST costs a row in
`EXPECTED_ROUTES` and `test_oob_refresh.CASES`.

**The page**: chops are shaded spans on every lane and on the overview
strip (`lab.wave_svg`, `strip_svg`, `.lw-chop`; the selected one
stronger) and each edge is a `.chop-mark` over all the lanes
(role=slider, focusable, an id; its `data-at` the song's seconds —
labview.js places it, and hides the ones out of sight). The **chop
list** (`fragments/lab_chops.html`, `#lab-chops`, module `lab.chops`) is
secondary: it sits beside the lanes, over what else a selection becomes
(under them below 1100 px): the Auto buttons, then a row a chop —
▶ (`.audition[data-note]`: the chop's WAV), the name (typed in place),
its start · length · bars (the button that selects it), the stems it
plays with their levels and its stem set when that is not the one
showing, "use the lanes' mix", the "clean" disclosure with what the
cleaning last did, Keep / Update clip, ✕ (a chop's Keep is not the
page's primary: the selection's Keep clip is). **Only the marked chop's
"clean" starts open** (every chop's open would make five chops a
column of forms); one you open or close stays as you left it.

**labchop.js** (island 25, <= 90 code lines): a double-click on a lane
(`op=split`), a marker dragged or nudged with the arrows (5 ms, Shift
50 ms; a held arrow is one post; `op=move` when the gesture ends; its
positions from `synthlab.lab.at`, labview.js), and
which of the Lab's `<details>` are open. lab.js's region drag never
starts on a marker, and a double-click is two clicks without a drag, so
it is not a region either. **Every chop post morphs `#lab-body`**
(`outerMorph`): the marker under the keyboard is the same element
afterwards and keeps the focus, a name being typed and a number in the
clean form survive, and `open` is kept out of the morph
(`htmx:before:morph:attr`, the chord bar's rule) and re-asserted from
`sessionStorage` after the swaps that replace (a region drag, a stem
tick). The ids and the `op` ride in `hx-vals`, never in named inputs:
lab.js's drag posts from `<body>` and would sweep them along
(`frontend.md`, "`htmx.ajax` posts the whole page").

## Clean

**Cleaning is pure and non-destructive** (`labclean.py`:
`clean(audio, settings, plan) -> (audio, notes)`; `tests/studio/
test_lab_clean.py` holds each step alone against a built signal). The
settings live on the chop and run again each time it is played or kept;
the source is never written. Steps, in this order, each skipped at its
"off":

| Step | Settings | Off | What it does |
|---|---|---|---|
| gate | `gate_db` -60..-20 | None | open wherever the mono sum reached the threshold within the last 80 ms, opening over the 5 ms before and closing over the 60 ms after; for stem bleed between phrases |
| trim | `trim_db` -60..-20 | None | cuts the start and the end under the threshold, 5 ms kept either side |
| fit | `fit` off \| session \| song | off | `sampler.time_stretch` by source bpm / target bpm, inside `lab.stretch_plan`'s range; then a chop within 5% of whole bars is cut or padded to them |
| fades | `fade_in`, `fade_out` 0..500 ms | 5 ms each | raised cosines, each at most half the chop |
| level | `level` off \| normalise \| gain, `gain_db` | off | normalise = peak to -1 dBFS; gain = that many dB, scaled back under full scale if it passes it |

- **Defaults change nothing but the first and last 5 ms** — a kept chop
  never clicks and is otherwise the recording, bit for bit.
- **Thresholds are measured from the chop's own peak**, so a quiet stem
  is not gated or trimmed away whole.
- **"session" is the loop's bpm** (`arrangement.bpm`, what `/lab/send`
  fits a clip to) **and "song" the song's** (`song["bpm"]`, what
  `/lab/place` fits a take to): `labchops.fit_plan`. A fit that cannot
  run (no tempo, a factor outside `LAB_STRETCH_RANGE`) changes nothing
  and the notes say why.
- **`notes`** says what each step did in numbers ("gate closed 22% of it
  · trimmed 0.31 s · stretched 93 → 124 bpm · held to 2 bars"). They are
  written onto the chop when its cleaning is set (`op=clean` runs it
  once) and when it is kept, and cleared when its span or mix changes —
  the row never shows numbers of an older cut.
- Whole-buffer processing is fine here: a clip bake, not anything
  `Synth.note` reaches (the causal contract does not apply).

## Keep and Update clip

**Keep** (`POST /lab/chop/keep`: `source`, `chop`, optional `update=1`):
the chop's own mix, cleaned, `save_clip`ped under
`free_clip_name(<chop name>)` — never over a clip — with the meta
`labchops.clip_info` builds (`sampler-and-clips.md`, "A kept chop"); the
chop records the clip's name in `clip`. **A rename takes the chop with
it**: both rename routes (`POST /fragment/clip/rename`, the Song page's
`/song/take/{clip}/rename`) call `labchops.clip_renamed`, which moves the
`clip` field of the one chop the clip's own meta names, and only while
that chop still pointed at the old name — so Update goes on writing over
that clip, and a later Keep does not quietly make a second one. A clip
**deleted** leaves the chop's `clip` as it was; `own_clip` no longer
finds it, and the row offers Keep again. The drawer is on the Lab page
too: its rename and delete answer with the open song's chop list out of
band (`routes.lab.chops_oob`, `#lab-chops`, morphed), so the row never
offers an Update for a clip that just went.

**"Update clip" is the one deliberate overwrite in the studio** (bounce,
keep-take and jam never overwrite). It is offered only while the chop's
`clip` still names a clip in the store whose own meta says it is this
chop's (`labchops.own_clip`: `lab.source` and `lab.chop` match); the
route checks the same thing again, and `update=1` on anything else — a
clip deleted, replaced by another of that name, or named by hand in the
meta — keeps a **new** clip instead
(`test_update_never_writes_over_a_clip_that_is_not_this_chops`). The
clip's content hash changes, so every render that places it re-renders:
the button's `hx-confirm` says so and names where it is used, from
`StudioState.clip_usage` in the words the library's delete uses
(`cliplib.update_note` / `where_used`: takes in the song, the clip lane,
drum rows, revert points, **and the song's undo / redo steps** — or
"nowhere in this session yet"), and after the write the route runs `StudioService.reset_caches()` — what
a project import runs — because memos keyed on a clip's name served
stale audio once before. Song undo steps hold clips by name: undoing
past an Update restores the reference, not the old audio.

**Refused, with a toast naming why and nothing written**: a chop over
`MAX_CLIP_SECONDS` (its row says its length and points at the stem
download), one that is longer than that once fitted, one that is silent
after cleaning, one whose stem set is not on disk.

**Nothing in the session changes**: a kept chop is a clip nothing places
yet, so it is in no render key until it is placed somewhere.

## Whole stems out

**Download, never copy into the clip store** (a stem is minutes long):

| Route | Answers |
|---|---|
| `GET /lab/{sid}/stem/{name}.flac` | the stored file, from disk (`FileResponse`); `name` is `mix` or a stem of the set |
| `GET /lab/{sid}/stem/{name}.wav` | that file decoded to 16-bit PCM and written in 64k-frame blocks (`sources.wav_blocks`): never the whole stem in memory |
| `GET /lab/{sid}/stems.zip` | the set's stems as FLAC, **stored** (FLAC does not deflate), through `files.zip_write` (deterministic) into a spooled temp file, sent in blocks |

All three are `@lock_free`, read the source store only, take an optional
`?model=` (default the set the source shows; a stem that set does not
have, a set not on disk, a bad id: 404 — `sources.stem_path` is the one
way a download reaches a path), and answer `Content-Disposition:
attachment` with a `files.safe_name`d name (`<song> - <stem>.flac`,
`<song> - stems.zip`). They name `Content-Encoding: identity` so the
gzip middleware passes them through (audio does not compress, and the
length stays known). Perf counts each as its route template. On the
page: `⤓ flac` / `wav` at the end of each lane's label (the mix lane
too) and "⤓ Download all stems" in the separation block. A lane's level
is **not** applied to a download: it is the stem as separated.
`sources.stem_sets(data_dir)` is the one listing of every separated
source with the stem names of each set it holds — exactly the sets
`stem_path` serves (a finished set of a model this build knows, plus the
set the meta itself names). The clip library's Stems group is a view of
it (`cliplib.stem_sets`).

## A whole stem onto the song

**"→ Song track, whole"** (the "onto the song" panel, `#lab-whole`; `POST
/song/long` `op=add` `mix=lanes` — `routes/song_long.py`, `song.md`,
"Long audio"): the whole song, not the region — the ticked stems at the
lanes' levels (the mix the region plays; nothing ticked is the
recording), full length and **as recorded**, on the song's first audio
track (one is made when there is none), from the section and bar the
panel's menus name. Its `start` is the Lab grid's first bar line
(`lab.grid`'s `offset`: bar 1 of the source lands on the bar line), its
`bpm` the grid's tempo, and with the box ticked (the default) **the song
takes that tempo** to three decimals — a whole stem is never stretched.
The item is a reference: nothing is copied, the stem set must be on disk
(else a toast), and the mix is fixed when it is placed (moving a lane
afterwards does not move the song). The same item can be placed from the
clip library's Stems group on the Song page (one stem, as separated).

**Deleting a source the song plays** is allowed and says so first: the
delete's confirm names the long items that play it and the song's undo
steps that hold one (`StudioState.long_usage`, `longaudio.delete_note`).
The item stays on the song, silent and marked "missing"; importing and
separating the same file again brings it back (same content, same id).

## Open in Lab

**`GET /view/lab?source=<sid>&chop=<cid>`** (what a kept chop's clip and
a stem in the library link to; `labchops.open_at`, `routes/views.py`):
that source becomes the one the Lab opens on, that chop the region and
the lanes its mix, and the waveform is brought to it when it was zoomed
elsewhere. `chop` is optional. A source or a chop that is gone opens the
Lab as it is with one line saying so (`#lab-notice` on a full page, a
toast on an htmx swap) — never an error.

## Make: one press on the selection, the rest a card each

**The four things a sample mostly becomes are one press on the
selection bar**: **Keep clip** (`#lab-keep`,
Enter on the lanes, the page's primary once the song has stems: `POST
/lab/send target=clip`, with its "fit <loop bpm>" box), **⤷ Sampler**
(`#lab-sampler`, `target=sampler`, with its slices menu: whole, a slice
a hit / beat / bar), **✂ Chop** (`#lab-chop-add`, `POST /lab/chop
op=add`: "Chops") and **→ Beat** (`#lab-beat`, `POST /lab/drums to=grid
loop_tempo=on`, behind its confirm; only in bars). The Clip and Sampler
sound cards are not on the page: one place each.

**What else it becomes is a card each** (`#lab-make`, module
`lab.make`, "More from bars 3–5": not a fold, so it is plain where a
sample came from and what it will become) — **Vocal instrument**, **Vocal chops**, **Drum pattern**
(the grid with its tempo box, or the Song's pool), **Drum kit**, **Song
take** (with "or the whole song, unstretched") — each says in one line
what you get and where it goes (opens on Synths, → Drums, → Song), and
in its head **which audio feeds it** (`.lab-from`): the take "from
<what the lanes feed>", the one-stem cards "from vocals stem" / "from
drums stem" (or "from mix" before a separation, with "only a voice /
drums alone works"). The one-stem cards carry a ▶ of exactly what they
read (`.audition` on `lab.stem_url`: the region as that one stem, at
unity). A card that cannot run says why and offers the way out where
there is one: a drum pattern on a region not in bars has "snap the
selection to bars" (`POST /lab/region mode=bars`), a take with no
sections "Open Song". Their routes are unchanged — they read the region,
and selecting a chop sets it; the module's name field names what they
make (empty: `lab.pick_name`).

**Send a region** (`POST /lab/send`): `target=clip` copies it into the
clip store under a free name (`free_clip_name`: never over a clip), meta
`{source, bpm, bars, lab: {source id, start, seconds, stems, source_bpm,
fitted}}` (`"lab"` is in `clips.META_SETTINGS`); with `fit` it is
stretched by `sampler.time_stretch` (the sampler's WSOLA, `_grains`) by
source bpm / session bpm, so N bars stay N bars — the panel says which
it will do, in numbers. `target=sampler` goes through
`routes.sampler.adopt_audio` (what "⤷ sampler" on a clip calls; it grew
a `chop` argument): detected root at `PITCH_SURE`, `chop` none |
transients | beats | bars (`lab.chop_slices`: the grid lines inside the
region as slice edges, 32 at most), landing on Synths.

## What a stem's region becomes

**What a stem's region becomes** (`labmake.py`; each reads ONE stem —
`stem_region`: that stem whatever is ticked, else what the region plays,
and then the result says "read from the mix"):
- `POST /lab/vocal` `what=instrument`: `voiced.best_run` on the vocals,
  then `vocal_fields` — the sampler's own fields, **no new engine
  param**: start just before the held note, a loop inside it where its
  level is steadiest (`_steadiest`; a whole number of the note's periods,
  crossfaded), root on its pitch with the cents taken out
  (`osc1_detune`), `sampler_mode` formant, a holding envelope — through
  `adopt_audio(fields=, root=, say=)`. Nothing pitched: the plain region,
  tape-style, and the toast says so. `what=chops`: `voiced.syllables` as
  `sampler_slices`. The voice plays from the live worklet like any
  formant sampler sound (the engine's grain train in the voice:
  `sampler-and-clips.md`, "Formant mode on the live keys") — its
  `osc1_detune` is a fixed offset, which the grain schedule carries.
- `POST /lab/drums` (`@pattern_edit`) `to=grid`: `drumhits.transcribe`
  of the region's first `labmake.MAX_PATTERN_BARS` (4) bars, written by
  `StudioState.apply_pattern` + `drum_vel` exactly as a kit is — one
  pattern undo step (Ctrl+Z on Drums; swing is not in that step, as for
  a bank load). With `loop_tempo=on` (the panel's box, ticked by
  default) the loop's tempo becomes the source's (`labmake.loop_tempo`:
  a whole bpm inside `BPM_RANGE`, else left alone) — a beat is steps on
  a grid, and at the session's tempo it was another beat. The tempo is
  not in the undo step; what was written names the one it replaced. `to=pool`: the same as a beat in the song's pool
  (`sm.add_pattern` through the song's `_commit`; a pool pattern has no
  swing). `to=kit`: `drumhits.exemplars` — one hit a lane that sounds
  alone — saved as clips and put on the drum rows' **existing** sample
  slots (`Arrangement.drum_samples`: the row's own picker undoes it).
  The region must be in bars. What was written stays on the page
  (`#lab-said`, with the rows as notation): `body(svc, sid, said=,
  rows=)`. **Honest scope**: kick, snare, closed and open hat; a hat
  under a snare is not looked for; toms / claps / rims land in the lane
  they resemble; on a real stem there is no ground truth and the page
  calls it "a guess to edit".
- `POST /lab/place`: `labmake.song_take` — the region stretched to the
  **song's** tempo (not the loop's) and cut or padded to whole bars —
  saved as a clip and placed by `routes.song_record.place_clip` in the
  chosen section (`sm.place_take`, the one rule mic takes, risers and
  placed clips share; `POST /song/section/{id}/clip` is the same half for
  any clip in the store). A refusal deletes the clip it made.

## Where it went: the said line and its undo

**After a make, the line under the selection bar says what was made,
opens it where it went, and takes it back** (`#lab-said`, over the
lanes: most makes are the selection bar's; `routes/lab.py` `body(go=)`, `_went`, `OPEN_ON`). ⤷ Sampler and the
voice and vocal-chops cards land on Synths themselves (a whole page,
"Before last load" there is their undo). The others stay on the Lab:

| Made | Opens | Undo (`POST /lab/undo`, `what`) |
|---|---|---|
| a clip (Keep clip, a chop's Keep) | "Open in Clips" — the drawer, `popovertarget` | `clip`: deleted, while it is the Lab's own from this song (`lab.source` in its meta) and nothing uses it |
| a beat on the drum grid | "Open on Drums" | `pattern`: the pattern undo step `/lab/drums` pushed (Ctrl+Z on Drums) and the loop's tempo back to `bpm` |
| a beat in the song's pool / a take | "Open on Song" | `song`: the song's newest undo step, only while it is still the Lab's (`label`: `POOL_LABEL` / `PLACE_LABEL`) — else a toast "undo it on the Song page"; a take's clip is deleted only when nothing holds it, and the song's redo step does, so it stays ("Song's redo puts it back") |
| a kit | "Open on Drums" | `kit`: each row back to what it held (`was`), only while it still holds the kit's clip (`now`); a row set by hand since stays |

An Update clip has "Open in Clips" and no undo (it wrote over the clip).
The fields ride in the Undo's `hx-vals` (JSON), never named inputs.
`tests/studio/test_lab_make.py` ("where it went, and its undo"), the
journey `tests/browser/test_lab.py`
`test_a_drum_stems_region_becomes_a_pattern_that_lands_on_drums`.

## Two models

**Two models** (`stems.MODELS`; `POST /lab/separate` takes `model`):
the 4-stem HT-Demucs and `HTDEMUCS_6S` (drums, bass, other, vocals,
guitar, piano — 258 MB, same exporter, same graph interface, pinned to
the publisher's repo at a commit + SHA-256). A stem set's names are its
model's (`sources.stem_names(model)`; `ALL_STEMS` for validation), so
the lanes, the pick boxes, `read_sum` and the key re-read follow. A song
keeps both sets on disk; going back to a model it already has runs
nothing (`LabJobs.start` re-points the meta). Measured on an i9-14900K
(`tools/lab_measure.py --model 6`): a 3:22 song in 70.5 s, peak RSS
2.78 GB — the 4-stem model 69.3 s and 2.70 GB in the same session (59 s when
it was measured on its own, see "Separation": runs differ by ~10 s). On
the one real song its guitar stem came out silent (-66 dB) and its
piano stem 19 dB under the mix: upstream calls the model experimental.

## Separation

**Separation** (`synthlab/stems.py`, optional extra `stems` =
`onnxruntime`; `labjobs.LabJobs`, `svc.lab`): HT-Demucs 4-stem as one
ONNX graph on the CPU provider. `stems.separate` is the chunked
inference (track-level normalise, 7.8 s segments a quarter overlapped,
linear crossfade windows, divide by the weight sum) around a plain
`run(chunk)` function, which is what the tests replace. The DC offset is
not added back to each stem (Demucs does; here the stems sum to the mix
less its DC). **The model** (`stems.HTDEMUCS`: 316 MB) is never in the
repo: downloaded on the first Separate (after an `hx-confirm` naming
its size) to `stems.cache_dir()` — `%LOCALAPPDATA%\synthlab\models`,
`SYNTHLAB_MODEL_DIR` overrides — from one HTTPS URL pinned to a commit,
size-capped, redirects followed to HTTPS only, written to a temp name
and renamed only after its SHA-256 matched the one pinned in code; a
cached file is re-verified (memoised by stat) before every load.
**The job**: one at a time ("busy" toast for a second), a daemon thread
that never touches `StudioState` or the studio lock, cancellable between
segments, `events.Task` progress (a step per MB downloaded, then per
segment) plus `LabJobs.state()` for the Lab's own status line
(`#lab-job` polls `GET /fragment/lab/job` once a second while a job
runs, and that answer brings `#lab-panel` back OOB when the job has
ended — so it works with no event stream). Stems are written into a
temp folder and renamed in one step, then named in the meta
(`sources.save_stems`); `sources.sweep` (run when a studio opens the
store) removes temps, and a stem folder missing a stem is not a stem
set — a half-written one is never trusted. Threads: `stems.threads()`
= half the logical CPUs, 2..8 (`SYNTHLAB_STEMS_THREADS`). **The session
runs with graph optimisation off**: any level peaks at 4.7 GB while it
loads and 5.8 GB over a job; off, a 3:22 song takes 59 s instead of 47 s
on an i9-14900K and peaks at 2.7 GB, same output to 3e-7. Don't turn it
back on without measuring (`tools/lab_measure.py`). The model's stems
are four predictions, not a partition: they sum to the mix closely, not
exactly. A synth "voice" lands in `other`, not `vocals`. After a
separation the key is read again from the stems (above, "Key"). Without
the extra or the model the Lab still
imports, analyses and cuts; the button says what is missing. No test
needs the model or the network (`tests/conftest.py` points the model
dir at an empty temp and makes the downloader's opener refuse); the two
that run the real model are opt-in (`SYNTHLAB_STEMS_MODEL_DIR`,
`SYNTHLAB_TEST_AUDIO`). `tools/lab_measure.py` measures time, memory
and stem content on a machine.

## htmx 4 trap

**htmx 4 trap**: `hx-trigger="change from:find ..."` on the region form
never fired in Chromium (ticking a stem changed nothing). The units
radios and the stem boxes carry their own `hx-post` + `hx-include=
"closest form"` instead (`.lab-auto`).

## UI

**One path, one gesture.** Picking a stem and picking a region are one
drag, on the lane it is made on; a sample is kept in one place, not
three; the zoom is the wheel and is drawn at once; every lane carries
the bar and beat lines; the region's controls are one bar, not three
rows; the waveform has keys, and a stem can be heard from a point.

**The page is one path, top to bottom**: the song's bar (tempo and
bar grid, key, stems ⤓ all and the other model's Separate); **the
selection** (`#lab-region`, module `lab.lanes`, "Pick a sample"); the
chops and what else a selection becomes beside it, secondary.

**The selection bar** says what the selection plays — its lane's colour
swatch, "vocals", where ("bars 9–12 · 4 bars") and its clock — with ▶ /
⟲ (the looped audition, Space), the four one-press makes (Keep clip,
Enter — the primary once separated; ⤷ Sampler and its slices; ✂ Chop;
→ Beat), and a row of fine adjustments under it: the typed numbers, the
one-press lengths 1 2 4 8 16 and the snap (bar | beat). What was made is said under it (`#lab-said`).

**The lanes** (`#lab-wave`, focusable: its keys are lab.js's) are a
grid of a head column and a track column: the corner (the zoom buttons
and the zoom, ×2.5) and, over the tracks, the overview strip and the bar
ruler — both stay in sight when the lanes scroll — then the mix lane and
a lane a stem, and one layer over every track (`.lab-over`: the lines,
the chops' edges, the playhead, the pointer's line). **A lane's head**:
its colour (a 3 px edge and a swatch), its tick (a stem: in the
selection or not — nothing ticked is the recording) and its name, S
(solo), then the meter, the level knob and ⤓ flac / wav; a grip on its
bottom edge (drag: the lanes' height, double-click: fit again; Ctrl +
Shift + wheel too; kept per browser). The mix lane's head says whether
the recording feeds the selection (its LED) and offers "use mix" when
stems do (`#lab-use-mix`: the region form with every `stem.<name>`
posted empty). A lane that feeds the selection has its head tinted in
its colour and its region band bright; while stems feed, the lanes left
out are dimmed (60 %), and before a stem is ticked none is.

**Each stem a colour, of a piece**: `--lane`, a turn of the signal's hue
round the wheel at the signal's own depth and chroma (one tier: the mix
the signal's teal, vocals −50°, guitar −90°, piano +35°, bass +70°,
other +110°, drums +150°) — relative colours of `--signal` (`oklch(from
var(--signal) l c calc(h + N))`), so the Colours popover turns them with
it, and none is the amber of the selection or the red of a stop; data
colours, like the song's sections'.

**The waveform** is the peaks as a translucent outline and the RMS as
its solid body, centred, in the lane's colour (`lab.wave_svg`; the RMS
columns are stored beside the peaks, `<stem>.rms.npy`, worked out once
for a source stored before them: `sources.overview`), drawn up to its
loudest moment in the song (`lab.display_gain`, at most ×8) so a quiet
stem is a shape, not a hairline — drawing only, the meter says the
level.

**The ruler and the lines are drawn for whatever span is shown**
(labview.js, from `#lab-wave[data-beat|data-offset|data-bars|data-per-bar]`,
the grid in force): bar numbers every bar while one has 34 px, else
every 2nd, 4th, 8th ... counted from bar 1 (a bar 1 `EARLY` before the
audio numbered at the left edge), the phrase's bars (every 4th) bold; a
line a bar, stronger every 4th, strongest every 16th, each kind drawn
only while its own lines are 4 px apart; the beats once a beat has 7 px.
A bar's line is a light core in a dark sleeve of the page's own colours
(`.lg-halo`), so it reads over the dark well and over a bright wave
alike (by night the core alone on a lane's body was 1.2–1.5:1); by day
the same rule holds the other way round.

**Right click**: a lane is a thing (`data-ctx`: "the vocals stem") whose
menu is its tick, Solo and the downloads, then the module's own: the
audition, Keep as a clip, a sampler sound, a chop, a beat, the
lengths, the zoom (`menu.js`); a chop row and a song have theirs.

**Before a separation the lanes say stems exist** (`.lab-cta`, a row of
the lanes' grid where the stem lanes will be): "drums · bass · vocals ·
other — separate the song to take one part alone", with the Separate
buttons (the first one the page's primary) and the model download note.
While a job runs the four skeleton lanes stand there ("separating — the
stems land here"); once separated the song's bar carries "stems: ⤓ all"
and the other model's Separate. The job's end brings the panel **and the
song list** (`fragments/lab_sources.html`: its LED and "· stems") back
out of band.

**Empty states**: one a page state, each with one on-topic action — no
songs: the panel's "Bring in a song" (the three steps ahead, "Choose a
song", the page's primary; the song list says only "No songs yet."); no
chops: "✂ a chop a bar" (Auto by bars, or by hits with no grid). Neither
offers "Start a song" or "Play the loop" (`test_empty_states.py`'s
`EXEMPT` says why).

**Layout** (dense, 1100 px and up — `dense.css` D19): the page is a
window-high grid. Songs down the left (the drop box, the job line, the
list); the selected song's bar over the rest; the selection and its
lanes, which take the height (the mix a share and a half, a stem one
each, until a grip sets them in px); beside them the chops over what
else a selection becomes (from 1900 px the two side by side, each a
column). `#lab-body` is `display: contents` there: its children are the
grid's tiles, and the swaps that target `#lab-body` are unchanged. Hints
are `title`s. Every action is a command (`data-cmd`); the marked chop's
actions are the commands of "the selected chop".
**A lane's meter is static** (`lab.region_meter`): the highest of the
stem's stored overview peaks inside the region, plus the lane's level,
in dBFS on a -48..0 scale; `clipped` when the level pushes it over full
scale. Nothing is rendered or analysed for it: it says what the region
will send, not what is sounding. The mix lane's is the recording's own.

## A level per stem: the region's mix

**One definition of the mix**: `lab.region_audio` =
`sources.read_sum(..., meta["picked"], levels=sources.levels(meta))` —
the ticked stems, each times `10 ** (dB / 20)`, summed. The audition
plays it and everything that sends a *sum* sends it: "⤷ sampler" and
"→ Song" (`labmake.song_take` goes through `lab.clip_audio`), and a
**new chop takes it as its own mix** (`labchops.lane_mix`) — after which
the chop keeps that mix whatever the lanes do ("Chops", above). **The one-stem actions ignore the levels** and the
panel says so: "⤷ voice", "⤷ vocal chops", "→ drum grid", "→ Song beat
pattern" and "kit from this stem" read one stem's own file
(`labmake.stem_region`), whatever is ticked and at whatever level. With
no box ticked the region is the recording itself (`mix.flac`, not a sum
of stems) and the levels do nothing; the key re-read
(`lab.reread_key`) sums at unity on purpose.

- **Range**: `limits.LAB_LEVEL_DB_RANGE`, -24 .. +6 dB, 0 = as
  separated, kept to a tenth of a dB (`sources.level_db`; the slider
  steps 0.5). There is no -inf: unticking the box is the mute.
- **Stored with the source**, per stem set: `meta["levels"] = {model id:
  {stem: dB}}`, sparse (0 dB is not written, so a meta from before
  levels existed and one never touched are the same file), cleaned by
  `sources._clean_levels` like every other key (a model this build does
  not know, a stem its model does not make, a non-number: dropped; out
  of range: clamped). The 4-stem and the 6-stem set of one song keep
  their own levels. **Never a render input** and not in `state.json`
  (`test_levels_touch_no_session_state_and_no_render_key`): a clip made
  from a region is audio by content, as before. A clip's meta records
  the non-zero levels beside the stems (`lab.levels`).
- **Unity is bit-identical** to the sum before levels existed (the
  weighted path is skipped). A boost that takes the sum past full scale
  is scaled down to it, not clipped (`read_sum`: only when a level is
  over 0 dB) — the store's own rule for a decode that does.
- **The audition's URL names the whole mix**:
  `region.wav?start=&length=&levels=&stems=`, `levels` in step with
  `stems` (missing or unreadable = 0, clamped; both lists cut to 8
  entries). A level moved is another URL, so the `<audio>` element loads
  the new mix; the route reads the query, never the meta.
- **Posting**: the slider (`level.<stem>`) and the solo button (`solo`)
  post the region form to `POST /lab/region`, like the stem boxes; a
  level is only read beside the form's `stems=1`. The slider's swap is
  `outerMorph` of `#lab-body` (the FX rack's rule, `frontend.md`), so it
  keeps the keyboard; the solo button changes which boxes are ticked, so
  it replaces (`outerHTML`) and its id brings the focus back. Solo =
  that stem alone; pressed again on the stem playing alone, every stem
  (`lab.solo`). No JavaScript of its own: lab.js's drag ignores a
  press on a button as well as on a label (the same line).

## Project export

**Project export**: unchanged, and that is the rule. A clip or sampler
sound made from a region is in the clip / sample store and travels in
`/project.zip` when the session uses it; **sources and stems are never
bundled** (tens to hundreds of MB each). `project.json` names a source
in one case only: a song with a whole stem on it (`song["long"]`) holds
the source's id and name — and still none of its audio
(`test_the_project_bundle_names_the_source_and_never_carries_it`). On
another machine that item is "missing" and silent until the song is
imported and separated there; the id is a content hash, so it then
plays with no edit. Moving machines means importing the song again (same
id, so same stems folder name) and separating again. Carrying the played
slice in the bundle was weighed and not built (20-25 MB an item as FLAC;
it needs a store to import into).

## Rights

**Rights**: one neutral line under the import box says checking the
right to sample or separate a recording is the user's job.

## Not built

**Not built**: a clip picker on the Song page itself (the route is there);
hats under snares, toms and claps in the drum reading; the formant
voice on the live worklet.
