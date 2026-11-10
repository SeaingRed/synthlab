# Clips, takes, the sampler and the project bundle

Recorded audio in the studio: the clip store and what fills it (bounce,
jam, voice-lab takes, Lab chops), the clip library drawer that lists it
on every page, the sampler instrument (panel, chops, live
keys, pitch detection, slice markers) and the project bundle that
carries samples and clips (`/project.zip`). The engine's sampler is in
the root `docs/engine.md`; whole-song import is `sample-lab.md`. Index:
`README.md`.

## Clip store

**Clip store** (`studio/clips.py`, `studio_data/clips/`): named 16-bit
WAVs + meta (bpm/bars/source). Filled by ⏺ bounce (sound bar — the
current view's ▶ render, `svc.render(view).audio()`; named in the name
popover, the sound's name its default, and it always says where it went:
"Bounced “x” (1 bar, 1.9 s) to Clips.", `kept_as`'s `said`), "keep take" and
"Keep result" on Voice (the take, and the take vocoded: "Voice lab" below),
● Record sample in the sampler panel ("Record a sample" below),
and ⏺ resample (the Loops page: client-side master-bus capture incl. performance FX,
uploaded as PCM WAV to `/clip/upload` — the server still has no codec
surface). Consumed by: drum one-shot slots (`Arrangement.drum_samples`,
voice → clip, played by `_schedule_drums` through the untouched groove
machinery) and the **clip lane** (`Arrangement.clips` =
`[{clip, at_bar, gain}]`, a fourth stem kind in `render_stems`; Perform
always renders the `"clips"` part, bench views never do). Every render
input, clips included, is carried on one frozen `RenderInputs`
dataclass (`render.py` — one object rather than positional arguments,
so no caller can pass them in another order or drop one, as a
`clip_bounce` without `clip_root` would not match what ▶ played):
`RenderInputs.key()` is the one render-cache key, and its `clip_root`
field makes it hash each referenced clip's *content*, not its name, so
a re-recorded clip invalidates every cache entry that placed it. Both
exports and sections carry samples and takes. `_merge_arrangement`
preserves `drum_samples` and `clips`.

## A kept chop: the `lab` clip meta, and the one overwrite

**A chop kept from the Sample Lab** (`POST /lab/chop/keep`,
`labchops.clip_info`; `sample-lab.md`, "Keep and Update clip") is a clip
like any other, whose meta says where it came from:

```
{"name": "verse 1", "sr": 44100,
 "source": "lab: <song>",            # what the library groups by
 "bpm": 124.0, "bars": 2,            # only when it is on a grid (below)
 "lab": {"source": "<source id>", "chop": "<chop id>",
         "start": 8.512, "seconds": 3.868,     # the span in the song
         "stems": ["vocals"], "model": "<stem set id>", "levels": {"vocals": -3.0},
         "source_bpm": 93.0, "fitted": true, "key": "D minor",
         "clean": {"gate_db": null, "trim_db": -40.0, "fit": "session",
                   "fade_in": 5.0, "fade_out": 5.0, "level": "off", "gain_db": 0.0}}}
```

Every key of `lab` is always there (`stems` `[]`, `model` `""` and
`levels` `{}` for the recording itself; `source_bpm` 0 with no tempo).
`"lab"` is in `clips.META_SETTINGS`, so `clean_meta` and a bundle import
keep it as a dict. `bpm` is the tempo the clip plays at (the one it was
fitted to, else the source's) and is absent with no grid; `bars` is
there when the audio is whole bars of that tempo (a fitted chop within
5% is cut or padded to them; an unfitted one must already be). A region
sent by `POST /lab/send target=clip` writes the older, smaller `lab`
(no `chop`, `model`, `clean`). `GET /view/lab?source=<lab.source>&chop=
<lab.chop>` is "Open in Lab" (`sample-lab.md`); the source deleted later
leaves the clip as it is — it is a copy.

**"Update clip" is the one deliberate overwrite of a clip.** Bounce,
keep-take, jam, a sent region and a first Keep all go through
`free_clip_name` and never write over a clip that is there. Update
(`update=1`) writes over the chop's own clip only — the one its `clip`
names while that clip's meta still says `lab.source` / `lab.chop` are
this chop (`labchops.own_clip`); anything else becomes a new clip. Since
the content hash moves (`clip_hash`, in every render key that places
it), it changes wherever it is used: it sits behind an `hx-confirm`
naming where (`StudioState.clip_usage`, the words delete uses, the
song's undo steps included), and runs `StudioService.reset_caches()`
after the write. A Song undo step holds a clip by name, so undoing past
an Update restores the reference, not the old audio.

## The clip library: a drawer on every page

(The drawer is the quick picker. The **clip hub**, `/view/clips`, is the
page where a clip is looked at: waveforms, a player, tune, every send,
and a beat / note part / sound made from it — `clip-hub.md`.)

**The clip library** (`studio/cliplib.py`, region `clip-library` =
`fragments/clip_library.html` + `clip_list.html`, `GET /fragment/clips`,
`tests/studio/test_clip_library.py`, `tests/browser/test_clips.py`)
is on every page, not only Synths and Perform. It is
included from `base.html`, **outside `#view`**: a rail button ("Clips"
with the count; an icon over the count in the compact side bar) and the
drawer it opens.

- **A declarative popover, no script.** `#clip-drawer` is `popover`
  (auto), its button `popovertarget`; Esc and a click outside close it,
  which is the browser's. So a pointer click on the nav closes it too
  (light dismiss); a view change from the keyboard, or `events.js`
  re-fetching `#view` for another page's edit, does not. The Song
  page's own Esc already stands down while any popover is open
  (`song.js`: `:popover-open`), so one Esc never closes both. No island
  of its own.
- **Every redraw of it morphs** (`outerMorph`, targeted or out of band):
  the popover element survives, so it stays open and keeps its scroll
  and the keyboard's place. Anything that swaps it must do the same
  (`test_every_redraw_of_the_drawer_morphs`).
- **It differs by page**, so it rides every htmx view swap out of band
  (`routes/views._view_response` appends it to `views/<name>.html`):
  an open drawer follows the page it lands on. The rail button's click
  also asks for it again (`hx-get /fragment/clips?whole=1` beside
  `popovertarget`): what changed with no request of this page's — a
  separation that finished in the background — is there when it opens.
- **Loops first**: the drawer is "Loops & clips" (the rail's "Loops ·
  clips"), and its top group is **Loops** — every loop and every keys
  take, one row each (`looplib.py`, `fragments/loop_rows.html`;
  `loop-and-perform.md`, "Loops"): ▶ in time, its notes as a mini piano
  roll (a beat's hits as a mini step grid), its name — from what it is,
  "Acid bass riff", its facts in cells — ☆ / ★, its tags, Edit (a beat: Open),
  → Song, Q, #, ✎, ✕; the beats kept (✚ Keep on Perform's rack, Drums'
  Save) are Loops too, `beat` (`loop-and-perform.md`, "The
  Loops panel"). The search narrows them by every word of a name, sound,
  key ("am", "a minor"), tempo, kind or `#tag` (`looplib.matches`: the
  same words as Perform's Loops panel, not the name alone);
  the Drums bank's 8 slots are the panel's alone, not the drawer's (it is
  every page's: what is kept, not 8 rows more). A keys take
  is in no clip group (`cliplib.LOOP_GROUPS`: its audio and its
  loop are one row), and the drawer's watch leaves a take's files and its
  usage to the Loops' watch (`looplib.watch`). **The rail's number is
  the loops'** (`looplib.context`'s `total`, `.loop-count`; not the
  clip store's, which counts a loop's audio and the risers in it — "Loops
  6" with 2 loops); the comfy top bar adds "· clips N" (`.clip-count`,
  `cliplib.library`'s `clips`: the clips that are not a loop's audio),
  which the compact rail leaves out. On Song a row with audio drags onto an audio lane as a
  clip row does.
- **Groups**, from each clip's meta (`cliplib.kind_of`), in this order:
  one per Lab song *and the stems the clip plays* ("WOMAN IN LOVE ·
  vocals"; a region with no stems picked is "· mix"; a kit hit, which
  names only its song, is under the song's own heading), then **Voice
  takes** (`source` "voice take", and "take": a mic take recorded on
  Song), **Vocoder** (a `vocoder` dict or `source` "vocoder": a kept
  Voice-lab result — read before the bounce rule, since one made over a
  tempo-locked carrier states bpm and bars), **Jams** ("jam"), **Bounces** (any `source` beside both `bpm`
  and `bars`: the sound's name, which may be any text, so it is read
  before the words), **Risers / impacts** (`transition`), **Other**.
  Both shapes of a Lab clip's `lab` meta are read: a kept chop's
  (`source` + `chop`) and a region sent as a clip (`source` alone).
  **A meta is a file anyone can edit**: `clips.clean_meta` holds the
  top-level keys to their types, `cliplib` checks everything inside
  `lab` again, and a wrong shape is Other — never a failed page
  (`test_a_meta_of_any_shape_draws_and_never_fails_a_page`).
- **Search** (`#clip-q`: `hx-get /fragment/clips`, `hx-trigger="input
  delay:200ms"`, `hx-target="#clip-list"`): the answer is the LIST only,
  so the box being typed in is never redrawn. `q` is cut to
  `cliplib.MAX_QUERY_CHARS` (60) and matched on the name, whatever the
  case; no match says so. The box is a text input with
  `role="searchbox"`, not `type=search`, on purpose: audio.js's
  `syncControls` leaves `[type=search]` alone (`CLIENT_OWNED`), and a
  redraw that drops the filter must clear the box with it.
- **Per clip**: ▶ (the `.audition[data-note]` idiom), name · seconds ·
  bpm · bars, ✎ rename (inline, `POST /fragment/clip/rename`), ✕ delete,
  "⤷ sampler" (`POST /fragment/sampler/clip`; it also closes the drawer
  — `popovertargetaction="hide"` — since it lands on Synths), and by
  page:
  - **Voice — "Use as take"** (`POST /voice/take/clip`, `name`): the
    clip's file through `voice.read_wav`, exactly as an uploaded take is
    read (summed to mono, the same size cap, sample-rate range and 30 s
    cut), then `voice.save_take`; the answer is the Voice panel. What
    an upload would refuse this refuses with the same words. The clip
    is untouched.
    The take remembers which clip it was (`voice_lab.json`), and the
    page says so. **"as carrier"** beside it (`POST /voice/carrier/clip`)
    makes the clip what plays under the voice. Both include the
    vocoder's form (`hx-include="#voice-render"`), so the panel that
    comes back still holds what was typed ("Voice lab" below).
  - **Song — "Place"**: the Song page's clip picker. A "Place into" bar
    at the top of the drawer (section, bar, audio track) and a button
    on every clip posting the existing route, `POST
    /song/section/{id}/clip` (`clip`, `at_bar`, `track_id`). The section
    is the route's *path*, which no form field can change without a
    script: choosing one asks for the drawer again
    (`/fragment/clips?whole=1&place_section=`) so each button's
    `hx-post` names it. One undo step, like every `_commit` — no
    confirm.
  - **"Open in Lab"** on a clip whose `lab.source` is still in the
    source store: `/view/lab?source=<id>` (`&chop=<id>` for a kept
    chop). Disabled (`aria-disabled`, no `href`) when the song is gone —
    the clip is a copy and stays.
- **The drawer's own redraws keep what its controls hold.** Rename,
  delete, Place and the section menu carry the search box and the Place
  bar (`hx-include="#clip-q, #clip-place"`), and `StudioService.asked`
  reads them back for the render — out of band included: `routing`'s
  form dependencies leave the parsed form on `request.state.form`,
  because a region's context is handed the request, not the handler's
  form. A redraw caused from anywhere else (a bounce, a view swap,
  another tab) draws the whole list on the first section.
- **Stems group**, last: each *separated* song in the Sample Lab's store
  with every stem set its download routes serve (a source keeps both
  the 4- and the 6-stem set; `cliplib.stem_sets` is a view of
  `sources.stem_sets`, the one listing, the meta's current set first)
  — download links (`/lab/<id>/stem/
  <name>.flac?model=`, `/lab/<id>/stems.zip?model=`) and "Open in Lab".
  No audition: a stem is minutes long, the Lab plays it. A stem is never
  copied into the clip store. **On the Song page** each stem also has
  **→ Song**: the whole stem, as separated and as recorded, laid on an
  audio track from the "Place into" section and bar (`POST /song/long`
  `op=add`; the group's "the song takes the stem's tempo" box rides
  along, ticked by default) — long audio, `song.md`. It is a reference
  to the source, not a clip.
- **Delete says where a clip is used** before it goes: the "sure?"
  button of a clip the session holds carries an `hx-confirm` built on
  the server (`cliplib._used` / `where_used` from
  `StudioState.clip_usage`; the Lab's "Update clip" says the same places
  through `update_note`) — takes in
  the song, places on the clip lane, drum rows, saved revert points
  (baselines), **and the steps of the song's undo / redo ring**
  (`History.song_undo_clips`, read only), so Ctrl+Z is never how a
  reference to a deleted file comes back unannounced. Deleting is still
  allowed; whatever then points at the clip plays silence for it, as a
  missing sample does (`test_a_step_that_points_at_a_deleted_clip_
  renders_silence_for_it`). A clip nothing uses deletes as before
  (✕, "sure?").
- **Light on purpose**: a row is text and buttons — no waveform, no
  `<audio>`, nothing rendered per clip (~2 KB of HTML a row).
- **The watch** (`service/regions._w_clip_library`) runs around every
  state-changing request on every page and opens no file: the clip
  store's listing (`cliplib.clips_sig`: name, mtime, size), the page,
  `clip_usage()`, the source store (`cliplib.sources_sig`: one listing
  of `sources/`, a source's folder read again only when its own mtime
  moved — folder names only, so it stays a signature of
  `sources.stem_sets`, never a second reading of it; which Lab songs the clips name is memoised by the clip
  listing) and, on Song, the sections and audio tracks. It is precise
  both ways: an imported song that is neither separated nor named by a
  clip re-sends nothing. Measured on an i9-14900K with 30 clips and 12
  separated sources: 0.75 ms, of which 0.6 ms is listing the 60 clip
  files — the listing Synths, Perform and Drums already paid for.

## Keys takes in the library

**A keys take** (⏺ Rec in the keyboard dock: `keys-and-midi.md`, "⏺ Rec";
`keytakes.py`) is a clip whose meta says `source: "keys"` and carries
what it is: `kept` (true: one Rec keeps it with its loop; false on an
older take until ★ Keep), bpm, bars, `at`,
the key, the meter when not 4/4, and `sound` — the id of the whole sound that played it in
`studio_data/takesounds/` (by content: a format-2 preset; not in a
project bundle, so a bundled take keeps its notes and audio, not its
sound). **Its notes are a sidecar**, `studio_data/takenotes/<clip>.json`
(`{notes, raw?, fixes?}`, `[start, length, midi, velocity]` each): a
file the clip store keeps beside the clip by its name
(`clips.SIDECARS`; `delete_clip` and `rename_clip` carry it), outside
`clips/`, so writing it — ✦ Clean up, ↶ — never moves the store's
listing the drawer and the hub watch, and needs no suppress. A take
from before the sidecar kept them in its meta: `keytakes.read_notes`
reads either (the sidecar wins), every write goes to the sidecar, and ★
Keep (the meta's one rewrite) moves them out (`keytakes.migrate`).
`cliplib.kind_of` still names a kept take **Takes** and one not kept
**Takes, not kept** (read before the bounce rule: a take has bpm and
bars too), but the drawer draws neither group: every take is in the
**Loops** at its top, as its loop or — no loop yet — as itself
(`looplib.py`). A `keys` clip with no `kept` — the dock's recording from
before takes, posted to `/clip/upload` as a jam is, audio and no notes —
is filed with the **Jams**, and is no take (`keytakes.is_take`). The
loop a take made names it (`take`): a clip rename carries it
(`loops.clip_renamed`). `projectzip` carries the sidecar as
`takenotes/<name>.json` (at most `limits.MAX_TAKE_NOTES_BYTES`), read
through `keytakes.clean_doc` — the one reader of a take's notes — and
moves an older bundle's meta `notes` into one. `_w_clips` (Perform's
drum rows' clip menus) watches clip names only: they draw nothing else.

## Jams are kept, then named

**Jams are kept, then named**: stopping ⏺ resample uploads the take
immediately as `jam-YYYYmmdd-HHMMSS` (local time); no `window.prompt`,
whose Cancel would discard it. While recording, `#jam-rec[aria-pressed=
true]` shares `#mic-btn`'s red pulse (dropped under calm motion) and
reads `⏺ REC m:ss · bar n/N`. The WAV encoder is `window.synthlab.
wavEncode(buf, channels, maxSeconds)` in voice.js, the codec island, and
is shared by the voice take (mono, 30 s) and the jam (stereo, also 30 s —
`limits.MAX_CLIP_SECONDS`, read off `#jam-rec`'s own
`data-max-clip-seconds` attribute). The client trims to the cap itself
and the toast says so: `BodySizeLimit` rejects a `/clip/upload` body over
~131 s (48kHz stereo 16-bit), and the client keeps no copy, so an
untrimmed longer take would just be gone. audio.js hands the upload response to
`htmx.swap(...)` so its OOB library and lane land. Clips rename inline
(✎ in the clip library, `POST /fragment/clip/rename`): both names go through
`clip_safe`, a taken name is a 422 toast and never an overwrite, and lane
placements, drum one-shot slots, the song's takes, the steps of its
undo ring (`song_undo_remap`) and the Lab chop the clip was kept from
(`labchops.clip_renamed`) follow the rename. "keep take" on Voice
(`/voice/keep`, the take itself) and "Keep
result" (`/voice/result/keep`, below) answer with an empty body: their
buttons swap nothing, so the library comes back out of band by its
watch, as it does for a jam.

## Voice lab

**Voice lab** (`studio/voice.py`, `routes/voice.py`, view `/view/voice`,
island `static/voice.js`, `fragments/voice_panel.html`): a take is the vocoder's *modulator*
— its articulation is imposed on a carrier (`effects.vocoder`, N
constant-Q bands; each band is two cascaded biquads because a single
2-pole skirt leaks a 200 Hz modulator into the 7 kHz band and opens
every band at once). The carrier supplies pitch and timbre; the take
supplies the words. Page-only by design — a take is **not** a loop stem,
so `render.py` and the stem cache are untouched, takes do not appear in
an export, and **nothing here is session state**.

**The page is three columns in signal order** (`style.css`
section 17): 1 the take — where it comes from (`.vx-source`: Record, a
`.filepick`, "From the clips…") over the take itself (`.voice-take`: its
`.player`, "Keep take…"); 2 the vocoder's form (`#voice-render.vx-form`:
Carrier, Bands, the page's one primary button, "Vocode it"); 3 the
result over the FX racks it runs through; and under the take and the
vocoder, 4 the booth (`.voice-booth`, "4 · Treat": below). `#voice-panel`
— still the one element every render swaps — is `display: contents`
above 900 px, so its four children are cells of the page's grid; below
that it is a column of cards. **It fills the window** (style.css 45): the take, the vocoder's bands and the result are wells
that take the height left — 1 · Take | 2 · Vocoder | 3 · Result across,
4 · Treat under the first two and the racks (one line each when empty)
under the result, which runs down to them. The take's well draws **the
take** (`voice.take_wave`: the clip hub's waveform path, 240 columns),
the result's **the result** (`_result_wave`, from the held WAV), both
in the signal colour over a scope's graticule — an empty take or result
is the same well with its hint, never a bare panel. The vocoder's form
draws **its bands as a meter** (`voice.band_meter`, `.vc-meter`): each
band's filter a bell on the log axis the vocoder splits (the edges
`effects.vocoder` uses), a bar as loud as the take is in it (its share
of the loudest band on a 48 dB scale; none without a take) and an amber
tick where its carrier band lands after the formant shift. A picture of
the settings and the take: nothing reads it, no key moves. Every action carries `data-cmd` (group "Voice"). The take
plays in a **`.player`** (a button, a seek bar and the time over a
control-less `<audio>`, driven by `static/bench.js`), not the browser's
own slab; `.filepick` is a `.btn` whose real `<input type="file">`
covers it invisibly, so a click, a drop and the keyboard all reach the
input.

### The take

One file, `voice_take.wav`: mono, 30 s at most (`MAX_TAKE_SECONDS`).
Three ways in: the mic, a dropped file, and the clip library — "Use as
take" on any clip (`POST /voice/take/clip`), reached from the page by
"from the clip library…" (the drawer's own `popovertarget` + `hx-get`;
no script). **The browser owns decoding**: MediaRecorder emits webm/opus
and a dragged file can be anything, so `voice.js` runs `decodeAudioData`
and uploads mono 16-bit PCM — the server has no codec surface and file
upload accepts any format the browser reads. Mic is opt-in like midi.js
(nothing calls `getUserMedia` until Record is pressed) and every track
is stopped on stop *and* on view swap so the recording indicator can't
outlive the take. A stereo clip is summed: a vocoder reads the
modulator's level per band, and a second channel would give it nothing
(the *result* is stereo whenever the carrier is). No trim or region
control: cutting a phrase out of something long is what Lab chops are
for, and a kept chop is in the drawer.

### What the page remembers: `voice_lab.json`

Beside the take, never in the session (the Lab keeps its chops in the
source's meta the same way), so there is no state field, no schema node,
no `OWNERS` entry and no render key, and `/project.zip` does not carry
it (it does not carry the take either):

```
{"take": {"kind": "clip", "name": "verse 1", "hash": "<clip_hash>"},
 "settings": {"bands": 16, "carrier": "sound", "notes": "Am F C G",
              "step": "1", "snap": "off", "octave": 0, "clip": "",
              "shift": 0.0, "smooth": 40.0, "sibilance": 0.0},
 "last": "<the last audition's key>",
 "booth": {"treatment": "adt", "source": "take",
           "chain": [{"kind": "doubler", "params": {...}}],
           "moves": {"reverse": false, "swell": 0.0, "rate": 1.0}},
 "booth_last": "<the booth's last audition's key>"}
```

**A file anyone can edit**: `voice.clean_settings` holds every field to
its type, choices and range (`limits.MAX_VOICE_NOTES_CHARS`,
`VOICE_SHIFT_RANGE`, `VOICE_SMOOTH_MS_RANGE`, `VOICE_OCTAVE_RANGE`),
`clean_origin` the take's origin, and an unreadable file is the
defaults (`test_a_voice_lab_file_of_any_shape_draws_the_page`). The same
function cleans a posted form, *over* the saved settings: a post changes
only the fields it carries. It is written by a render, by either drawer
button and by a new take — voice.js's upload sends the vocoder's form
with the file (`new FormData(form)`), and "Use as take" / "as carrier"
`hx-include="#voice-render"` — so the panel that comes back still holds
what was typed, never the defaults' 16 bands and A2. Another tab on Voice is not told: nothing here moves a region.

### Four carriers (`voice.CARRIERS`)

| Kind | What plays under the voice | Made by |
|---|---|---|
| `sound` | the current sound, held, on the typed **notes or chords** — "A2", "A2 E3", "Am7 F C G", "i VI III VII" in the session's key, "~" a rest (`chords.parse_code`, the chord bar's reader; a `CodeError` is a toast) | `voice.chord_carrier` |
| `follow` | the current sound **following the take's own pitch**: as sung, or snapped to the session's scale; -2..+2 octaves | `voice.follow_carrier` on `analysis.voiced.melody` |
| `loop` | **the loop Synths plays** (`svc.render("editor")`: its layers' FX and the master included), repeated | `voice.tiled` |
| `clip` | **any library clip**, repeated or cut to the take's length, stereo kept — picked with "as carrier" in the drawer (`POST /voice/carrier/clip`) | `voice.load_carrier_clip`, `tiled` |

- **The result is always the take's length.** No carrier is stretched
  and neither is the take (`effects.vocoder` stretches a modulator of
  another length to the carrier's, so the carrier is made exactly as
  long).
- **`sound`** is forced to sustain (`_HELD`): a plucked envelope would
  silence the back half of any phrase. One chord lasts the take. Several
  last `step` bars of the session tempo each (½ / 1 / 2) and the row
  repeats until the take ends; `even` shares the take out once. A
  chord's release rings into the next; at most
  `limits.MAX_VOICE_CHORD_NOTES` notes of a chord sound; a note outside
  MIDI 12..108 is moved by octaves, never clamped to another note.
- **`follow`** renders one held note at the sung line's median and bends
  it along the line (`pitch_bend` x `bend_range` 24 — the Patch's own
  CURVABLE bend, so two octaves either way). Unvoiced stretches hold the
  last sung note. A take with no pitch in it is refused in words ("No
  clear pitch in this take — type notes instead"). A `pluck` sound keeps
  one pitch per note (the engine's rule) and so does not follow: the
  panel says so.
- **`clip`**: a clip whose meta has bpm and bars is a loop and tiles as
  it is; any other gets 5 ms ramps at its ends so a repeat's seam does
  not click. Read as a take is read: `clip_safe` + the containment
  check, a size cap, the sample-rate range; resampled to `SR`.
- **FX**: the Master chain is applied to the result;
  a layer's FX reach the vocoder only inside the `loop` carrier — which
  has then been through the master once already. The page says which
  rack applies.

### Controls

bands (8 / 16 / 24 / 32), **formant** (`shift`, -12..+12 st: the carrier
bands moved off the modulator bands that drive them), **smooth** (the
band followers' release, 10..250 ms; attack stays 5 ms) and
**sibilance** (`noise`, 0..1: the consonant path — seeded noise through
the same bands where the take is unvoiced). The carrier's own rows
(notes + step, snap + octave, the clip's name) are shown by a CSS
`:has()` on the option the kind's `<select>` holds, as the editor's
shape rows are; no script.
The result is peak-normalised to 0.89; there is no level control.

### The render (`POST /voice/render`, `@render_outside_lock`)

Under the studio lock only to snapshot: the settings (saved as they are
read), the patch at the tempo (`at_bpm`), the master chain, the key, the
take, and the carrier's source — the Synths loop is rendered *here*
(`svc.render`, which reads the session and is cached), a clip read from
the store. The vocoding runs with the lock released under an
`events.Task` ("Vocoding", three steps): measured 0.8 s for a 20 s take
at 16 bands and 1.95 s at 32 with sibilance (14900K). The key is
`voice:<sha1>` of the patch key, the take's bytes, the cleaned settings,
the master chain, bpm, root and scale, and the carrier's content (the
loop's own key, a clip's samples) — never part of a session render key.
A key already held is answered without rendering. The answer is the
panel plus `preview-ready` (the audition **loops**, as every preview
does; the top dock's ■ ends it). Its `plays` names it in Now playing
("Vocoder · the Synths loop"; the booth's "Booth · Leslie vocal"): a
preview that names itself is what audio.js calls the loop, where these
read "Loop · Voice · this page's mix".

### Keep result (`POST /voice/result/keep`)

The last four auditions are held with their clip meta (`voice.RESULTS`,
a module-level LRU keyed by the audition's key), so Keep saves **the
samples that were heard** — not a second render of whatever the sound
has since become. The panel's result row (▶ again, what it is, "Keep
result" with an `hx-prompt` for the name) is drawn while the last
audition is still held; a result that is gone (a restart, four newer
ones) is a toast, "Vocode it again". **Never an overwrite**
(`free_clip_name`; default "<take's clip> vocoded"): there is no "Update"
as a Lab chop has, since a result has no identity to update. The meta:

```
{"name": "verse 1 vocoded", "sr": 44100, "source": "vocoder",
 "bpm": 124.0, "bars": 2,          # only when the carrier has a tempo
 "vocoder": {"take": {"kind": "clip", "name": "verse 1", "hash": "…"},
             "carrier": {"kind": "sound", "sound": "<patch name>",
                         "notes": "Am F", "step": "1"},
             "settings": {"bands": 16, "shift": 0.0, "smooth": 40.0,
                          "sibilance": 0.0},
             "seconds": 3.871}}
```

`carrier` is `{kind, sound, notes, step}` (step only with more than one
chord), `{kind, sound, snap, octave, voiced}` (`voiced`: the share of
the take that had a pitch), `{kind: "loop"}` or `{kind, clip}`. `bpm` is
stated when the carrier is tempo-locked — the loop, a clip whose meta
states one, a row of chords on a bar step — and `bars` when the length
is then whole bars of it within 10 ms. `"vocoder"` is in
`clips.META_SETTINGS`. The clip lands in the drawer's **Vocoder** group
(`cliplib.kind_of`, read before the bounce rule since a tempo-locked
result has bpm and bars), where "Place" (Song) and "⤷ sampler" already
work on it.

## The vocal booth

**The vocal booth** (`studio/booth.py`, `routes/booth.py`, the
`.voice-booth` part of `fragments/voice_panel.html`;
`tests/studio/test_booth.py`, `tests/browser/test_vocal_booth.py`): the
Voice page's "4 · Treat" — the take, or the vocoder's last result, through
the studio tricks records were made with. Nothing here is session state
or a render key: what it holds is `voice_lab.json`'s `booth` and
`booth_last` (above), cleaned by `booth.clean_booth` like the rest of the
file.

- **Treatments are data** (`booth.TREATMENTS`): a label, a one-line
  `title` naming the technique — never a band — a chain of ordinary FX
  units, stored clean, and the offline moves. Leslie vocal, ADT double,
  Tape slapback, Phaser-and-echo wash, Octave double, Fifth harmony,
  Telephone, Backwards swell, Backwards vocal, Varispeed tape, Dreamy
  plate, Dry. **One press from a treatment to hearing it**: its button
  posts `/voice/booth/render` with `treatment`, which loads its chain and
  moves and renders. The page marks the one picked (`aria-pressed`) and
  the rack's legend says "(edited)" once anything moved off it.
- **The chain** is any rack: the six studio-trick units (effects.py's
  `doubler`, `rotary`, `tape_echo`, `telephone`, `pitch`, `plate` — on
  every rack, live on the keys too: `live-voice.md`) or any other.
  `POST /voice/booth/fx` (`op` add | remove | move | set) is
  `fxrack.edit_chain`, every rack's edit, and the rack is THE rack
  (`fragments/fx_units.html`, from `fxrack.rack_rows`: `synths.md`, "One
  rack everywhere") — ★ presets and ↺ Defaults (`name=recall`), a
  middle click's default on a knob, choice menus, the step editor and a
  gate's rhythms, jacks and the drawn leads; its posts answer with this
  panel, morphed. A rack edit renders nothing; ▶ Hear it does, and it
  has no undo (the booth is `voice_lab.json`, outside every ring). The
  chain reads the session's grid but not its drums (`booth.chain_arr`):
  a take has no kick to duck under.
- **Whose tempo a synced unit follows** (a tape echo on the beat, a
  gate, a pump): the "synced to" select in the moves' form,
  kept as the booth's `tempo` — `auto` (the default), `loop` or `song`.
  **The rule** (`booth.follows`): on auto, the song's tempo when what the
  booth treats is headed for the Song — the take is a clip the song
  plays (a song take brought in with "Use as take", `song_clip_names`),
  or a song track's sound is being edited (`song_edit_track`, the keys'
  own rule, `fxlive.keys_bpm`) — else the loop's (the session's), as
  before. The select names the bpm each means ("auto (the song's, 97
  bpm)"), so what auto picked is on the page. The tempo is in the
  audition key, so a change re-renders.
- **The offline moves** read the whole take, so they are not units, and
  run before the chain, in one order (`booth.apply_moves`): the tape's
  speed and direction — `cliphub.tune`'s rate (pitch and length together)
  and reverse, the clip hub's own — then the **backwards swell**: the
  take reversed, played into a room (wet only, 3 s), that reversed again
  and laid under the take at `swell`, so each phrase's reverb rises into
  it; the swell starts `SWELL_LEAD` (1.5 s) before the voice, so the
  result is that much longer. `POST /voice/booth/moves` keeps them
  (`reverse` forwards | backwards, `swell` 0..1, `rate` 0.5..2) and
  `source` (take | result: the vocoder's audition while it is held).
- **The render** (`POST /voice/booth/render`, `@render_outside_lock`):
  under the lock only to snapshot; the moves, then the chain over the take
  and `TAIL` (4 s) more, cut where the ring falls under -80 dBFS (faded,
  never shorter than the moved take), at most `booth.MAX_SECONDS`
  (`limits.MAX_TREATED_SECONDS`, 35.5 s: a whole 30 s take, the swell's
  1.5 s lead and the 4 s ring — at a clip's 30 s a full take with a
  swell would lose its last 1.5 s), peak 0.89. Keep saves at that cap
  (`save_clip(cap=)`; the hub reads a clip that long whole). A tape run
  slower than 1 still stretches past it: the moves' `cliphub.tune` cuts
  what it makes at 30 s, as the hub does.
  The key is `booth:<sha1>` of the source's samples, the chain, the moves
  and the tempo and grid; the last four are held with their clip meta
  (`booth.RESULTS`), so a press of a treatment already heard is answered
  without rendering, and Keep saves the samples that were heard.
- **Out**: "Keep as clip…" (`POST /voice/booth/keep`, a name prompt,
  never an overwrite) and "→ Song" (`POST /voice/booth/song`: kept, then
  placed by `routes.song_record.place_clip` — the Lab's "→ Song" and the
  hub's Place — at bar 1 of the section the select names, on the song's
  first audio track, one Song undo step; a clip the song refuses is
  deleted again). The meta: `{"source": "booth", "booth": {take, from,
  treatment, edited, chain, moves, seconds}}` (`"booth"` is in
  `clips.META_SETTINGS`); the drawer files it under **Voice takes**, with
  the live talkbox's recordings (source `talkbox`).
- **Talkbox takes**: the newest three clips the live talkbox's ⏺
  recorded are buttons under the booth; one press is "Use as take"
  (`/voice/take/clip`), so a talk take is treated or vocoded like any.
  **⏺ → booth** (`#talk-rec-booth` in the talkbox's ⚙)
  records straight into it: the take is kept in Clips as ⏺ keeps it,
  then posted to `/voice/take/clip` with `booth=1` — it is the Voice
  take, and the booth treats the take (its `source` back to `take`, not
  a vocoded result of the last one). On the Voice page the panel swaps
  in; elsewhere the toast says where it went.
- **The pitch unit's splice** (`effects._pitch_splices`,
  the live twin `vox-worklet.js`'s `splice`): without it a held note is
  grainy — a 40 ms grain on its own clock leaves the two in a
  crossfade out of phase by whatever the 20 ms hop left, and a
  steady tone comes out as lines 1/hop = 50 Hz apart round the target.
  So each grain starts up to 16 ms later than its place, where it reads
  the source in phase with the grain it fades out of: picked at its
  start, from the past alone, as the least difference function over a
  12 ms window, a bias to the earliest, refined between samples.
  Measured (the share of a 1 s held tone's energy within 6 Hz of the
  target's harmonics; the loudest line between them against the loudest
  on one):

  | held tone, shift | grains on their own clock | the splice |
  |---|---|---|
  | 220 Hz harmonics, -12 / +7 / +12 | 4.2 / 4.6 / 5.0 %, +13.1 / +13.2 / +10.2 dB | 100 %, -65 / -58 / -70 dB |
  | 147 Hz harmonics, -12 / +7 / +12 | 30 / 27 / 81 %, +0.8 / +1.0 / -10 dB | 100 %, -57 / -61 / -86 dB |
  | a sung-like "a" at 165 Hz, -12 / +7 / +12 | 18 / 16 / 24 %, +3.7 / +3.3 / +6.6 dB | 99.9 %, -38 / -39 / -33 dB |
  | 220 Hz with 0.5 % vibrato, -12 / +7 / +12 | 6.9 / 4.6 / 6.8 %, +16 / +16 / +13 dB | 99.8 / 95 / 93 %, -35 / -24 / -22 dB |

  A chord or noise has no one period to match, so it stays a little
  rough; the splice costs up to 16 ms more delay. The live twin is the
  engine sample for sample (`tests/studio/test_live_fx.py`, four pitch
  cases at 1e-6); `RENDER_REV` 3.

## The live vocal chain

**The live vocal chain** (`studio/vocal.py`,
`routes/vocal.py`, `fragments/vocal_panel.html`, `static/vocal.js`;
`tests/studio/test_vocal.py`, `tests/browser/test_vocal.py`): sing over
the music through doubling, auto-tune, compression and the studio tricks,
hear it as you sing, record it in time. The booth treats a take after
the fact; this is the voice live.

- **Where**: its panel is `#vox-pop` in `base.html` — on every page,
  outside `#view`, so a voice sung over Synths, Perform or Song keeps
  singing through a page change. Three openers (`.vox-open`, lit while
  the mic is on): the rail's "vocal" (`#vox-open`, every page), the
  keyboard dock's "🎙 vocal" beside the talkbox (Synths:
  `keys-and-midi.md`) and "🎙 Live vocal…" in the Voice page's take
  source. The panel is drawn (`GET /fragment/vocal`) when it opens.
- **↺ reset** (`#vox-reset`, in the rack's head; back to defaults): the style's chain as it
  ships (`vx.reset_to`: the loaded style, or the default style's for
  your own chain — the same `POST /voice/live/style` a style's button
  makes) and the mic's in gain 1 and high-pass 80 Hz (vocal.js `INPUT`,
  this browser's). The monitor and count-in are left as they are.
- **The graph** (vocal.js, in the page's one AudioContext — "interactive"
  latency): the mic (echo cancelling, noise suppression, auto gain off)
  -> input gain -> a high-pass (BiquadFilter, 20-300 Hz, 80 by default)
  -> the chain -> the monitor gain -> the master (the scope, the meter and
  a "+ beat" keys take hear it). The chain is built by livefx.js's own
  stages (`S.live.chain`: runs of fx-worklet.js / vox-worklet.js /
  fx2-worklet.js units in AudioWorklet nodes, reverb, plate and spring
  ConvolverNodes, Biquad filters),
  from `GET /voice/live.json` (`vocal.live_json`: `fxlive.live_chain` at
  the keys' tempo, on the session's key, no drums); a redrawn panel whose
  rack moved re-reads it, a chain of the same shape updated in place. No
  ScriptProcessor. **Monitor** (`#vox-monitor`) on or off (off: only
  recorded); the status says the latency (the context's base + output
  latency, and the mic track's where the browser reports it) — wear
  headphones, which the first switch-on says. **Denied or no mic**: the
  status and a toast say which (blocked: allow it for the page; none
  found: plug one in) and 🎙 mic stays a button to press again.
- **The chain** is `voice_lab.json`'s `live` (`vocal.clean_live`:
  `{style, chain}`, the default style's when missing) — beside the take
  and the booth, never the session or a render key. The rack is THE rack
  (`fragments/fx_units.html`; `POST /voice/live/fx`, `fxrack.edit_chain`);
  its adder offers the live units only (`FX_LIVE` "js" or "native"). The
  units for a voice are `autotune`, `harmony`, `vox_comp`, `deess`,
  `vgate` (`live-voice.md`, "The live vocal chain's units") with the
  studio tricks and the rest; each has ★ presets (`fxrack.FX_PRESETS`).
- **Vocal styles** (`vocal.STYLES`, data like the booth's treatments;
  `POST /voice/live/style` loads one's chain whole, every unit editable
  after, the legend saying "(edited)"): **Psych Haze vocal** (heavy comp,
  ADT double, slow phaser, a dotted-eighth tape echo, a long plate),
  **Sixties Studio vocal** (comp, ADT, a little rotary, a tape slap with
  the tape's wow, a plate — the varispeed is the booth's offline move, not
  live), **Robot / filter house vocal** (auto-tune at speed 0, a harmony
  an octave under, comp, a resonant low-pass, a phaser), **Pop polish**
  (gate, gentle auto-tune, comp, de-esser, a short plate), **Telephone
  verse**, **Choir** (auto-tune, harmony a third and a fifth up spread
  wide, a double, a room), **Clean** (gate, comp, de-esser: the default).
  Each title names techniques, never a record; every unit in one is live
  (an assert).
- **⏺** (`#vox-rec`, and its count-in `#vox-count`): keyrec.js's timing —
  from the loop's next bar line as heard (`S.nextBar` on `S.heardTime`),
  a bar on with count-in; nothing playing, four blips to the speakers
  then from there (or from now without count-in). Pressed again it ends
  on the bar line ahead (free length when nothing plays); at most
  `MAX_CLIP_SECONDS`. Two MediaRecorders — the processed voice and the
  dry one (after the gain and the high-pass) — each cut from its own
  start. `POST /take/vocal` (`file`, `dry`: two 16-bit WAVs; `bpm`,
  `bars`) keeps both: `vocal-<time>` (meta `source: "vocal"`, `bpm`,
  `bars`, the key, `dry`, and `vocal: {style, label, chain}` — what it
  was sung through) and `vocal-<time> dry` (`source: "vocal dry"`, `of`);
  the clip library files both under **Voice takes**. The answer is the
  panel, its newest three takes listed: ▶ (on the loop's next bar line,
  keyrec.js's `.kt-play`), **→ booth** (the clip library's "Use as take",
  `/voice/take/clip` with `booth=1`, on the dry twin — the booth treats
  it next) and **→ Song** (`POST /take/vocal/song`: `place_clip` at bar 1
  of the section the select names, on the song's first audio track, one
  Song undo step).
- **Not done**: the mic's chain is heard live only — the processed take
  is what the browser recorded, never a server render of the dry take
  through the chain (the booth can do that: → booth, then load a chain).
  Nobody has sung into it on a real interface: the latency, the
  monitor's feel and the auto-tune on a real voice are unheard.

## Sampler instrument

**Sampler instrument** (`samplerview.py`, `routes/sampler.py`, region
`sampler-panel` = `fragments/sampler_panel.html`,
`tests/studio/test_sampler_studio.py`; engine: the root `docs/engine.md` "Sampler").
"⤷ sampler" on a clip in the library / "Use as sampler" in a Song take chip
(`POST /fragment/sampler/clip`, `name`) and "⤷ Resample loop" on the
sound bar / the panel's Resample loop|phrase
(`/fragment/sampler/resample`, `source=loop` = the Perform render,
`phrase` = Synths ▶) snapshot the audio into `StudioState.sample_dir`
(`studio_data/samples/<hash>.wav`, registered with the engine at state
init) and load a fresh sampler patch (`sampler_patch`: osc1 alone, filter
off, level 1) through `load_sound` + one undo step, landing on Synths
(`HX-Push-Url` from elsewhere). The sound keeps its snapshot: re-recording,
renaming or deleting the clip doesn't change it; using the new take again
is a new hash and so a new render key. Silence is a toast. **Chops map
onto keys, not drum pads**: slices live in the Patch (`sampler_slices`),
so a chopped kit is a sound — saved in state.json/project.json, presets,
layers and song tracks, keyed by every render path, played from QWERTY,
MIDI, the Perform grid — and each slice runs through the amp env, filter
and FX; the drum machine's 8 fixed voices (choke, per-voice params, one
whole clip each) fit 16 slices badly. `/fragment/sampler/chop`
(`mode` 8 | 16 | transients | clear; grids span start..end) and
`/fragment/sampler/slice` (`index`, `action` pitch_up/down, reverse,
nudge_left/right = 5 ms, move = a dragged marker's `at`; the touching
neighbour follows) are patch edits
answered by `do_render`; the panel comes back OOB by its watch
(`samplerview.watch`). The panel is buttons only (the `#patch` form posts
on every input inside it), hidden unless an osc is "sampler" (the
`hidden` attribute + an editor.html `:has()` rule on the osc1 pills, like
the shape rows).

### Record a sample

**● Record sample** (`static/samplerec.js`, `POST
/fragment/sampler/record`, `tests/studio/test_sampler_record.py`,
`tests/browser/test_sampler_record.py`): the sampler panel's mic take,
timed to the session, beside Resample and in the empty state too. Its
settings are buttons, not inputs (the `#patch` form posts on every
`input` inside it): a `.seg` of 1 / 2 / 4 bars (`data-smp-bars`, 1 by
default) and `#smp-click` (on by default), remembered per browser
(`localStorage`) and set again after every redraw. A press opens the mic
through voice.js's `capture` (the browser's voice processing off, as a
Song mic take), then counts one bar in at the sound bar's bpm (`#t-bpm`,
4/4): blips to the context's destination and "ready, 3, 2, 1" big over
the page (`#smp-countdown`, a `.rec-countdown` outside the form and every
region, as songtake.js draws the Song's). It records exactly the bars
chosen — the click on every beat if it is on — and stops by itself; a
second press while it counts in or records cancels, and nothing is
posted (the panel leaving the page cancels too). The take is cut to
start on the downbeat after the count-in, shifted earlier by the
browser's input + output latency, or by this machine's calibration
(`/song/calibration.json`, the Song page's `#song-latency`) when it
reports none — songrec.js's rule — padded or trimmed to the exact
length, and posted as a mono 16-bit WAV from `#smp-countdown` (multipart,
no field under it: `frontend.md`, "`htmx.ajax` posts the whole page").
The route reads it with `read_pcm16` (the file capped at
`MAX_TAKE_UPLOAD_BYTES`, a toast; the body at the general
`MAX_UPLOAD_BYTES` by BodySizeLimit), refuses an unreadable file, a
sample rate outside `limits.MIN_SR..MAX_SR`, silence and a length other
than 1, 2 or 4 bars with a toast before anything is written, keeps it in
the clip store as `rec-YYYYmmdd-HHMMSS` (meta: source `sampler-rec`,
the session's bpm, bars), then loads it exactly as "Use as sampler" loads
a clip (`routes/sampler.py` `use_clip` → `adopt_audio`: the root from its
pitch, one undo step, the page answered whole with a toast). The keys
play it from there like any sample.

### Live keys

**Live keys**: osc1 "sampler" plays in the worklet when
`sampler.live_ok` admits it ("resample" at stretch 1 or "formant" at any
scalar stretch, mono or true stereo — the gate names the refusing field:
`sampler_stretch`, or `sampler_sample` — `sampler_zones` for a zone map — for a payload past the cap);
`/patch.json` then
carries `sampler_live`, the URL of the prepared buffers (`GET
/sampler/live/<id>.bin`, lock-free, immutable, capped at
`sampler.LIVE_MAX_SAMPLES` — both channels counted — or 413; a sample the
store doesn't hold is a 404, never an empty payload cached for good). The header
says `ch`: 1 for a sample whose channels are the same, 2 otherwise (each
source's left, then its right). livefx.js fetches it once per URL and
posts it to the lane's node; until it lands `live.ready()` is false and
piano.js keeps the keys on their (warmed) WAVs. **The oscillator is
`static/sampler-worklet.js`** (`globalThis.synthlabSampler`: `set` splits
the payload, `read` is `_Source.read` at the live pitch), a module added
to the worklet scope beside voice-worklet.js, which had no room — as
eq-worklet.js is to fx-worklet.js; every node harness that evals
voice-worklet.js evals it too. A two-channel sample is a stereo pair in
the voice, summed as `synth._osc_stack` sums it (level and ring per
channel; osc2, sub and noise onto both; fx.pan's stereo law per unison
copy), so the voice runs dc/filter/tract per channel even at unison 1.
It never reaches the WebAssembly stack (not one of its shapes): the JS
loop fills the mix, the module's post() runs both lanes. Sampler fields
left on a synth patch block nothing (`_sampler_gate`).

### Formant mode on the live keys

**Formant mode is the engine's grain train, run in the voice**
(`static/grain-worklet.js`, `globalThis.synthlabGrains`, a third module in
the voice's scope with its own budget; sampler-worklet.js's `read` sets a
copy up — source, start, root, Hz-to-speed factor — and hands it over
when `sampler_mode` is "formant"). It is `sampler._psola` grain for grain
over the same payload (nothing new on the wire): grains two root periods
long, centres snapped to whole root periods of a source clock running at
1 / stretch (numpy's cumsum, summed the same way; Python's half-to-even
`round`), the fractional offset of each grain's centre, the
overlap-root gain of a downshift, laid one played period apart. So the
vocal instrument the Sample Lab makes, and any formant sound, plays from
the worklet: the wheel and a legato bend move the pitch and leave the
vowel and the note's length alone (an octave up still lasts as long:
`test_formant_keeps_the_length_whatever_the_key`). `tests/studio/
test_live_sampler.py` runs the two modules alone under node against
`_psola`: the same samples at a held pitch (worst 7e-8 of the peak) for
the root, an octave and two up, a fifth and an octave down, a start
point, a loop, a start inside the loop, stretch 2 and 0.5, chops, key
track off, a low root — mono and stereo. The corpus then runs it inside
the voice (`sampler_formant_*`, both engines).

**`_psola` reads the pitch from the future, by one root period.** A grain
centred on output sample ci starts sounding at ci - half (half = one root
period: 3.8 ms for a C4 root, 15 ms for C2), and the engine takes
`speed[ci]` for that grain's gain and the next grain's place. The voice
schedules a grain the moment its first sample is due, on the pitch at its
centre: the block's own value if the centre is in it, else `ahead(P, v,
d)` — its control pass's pitch terms in closed form from where the block
left them (the glide's geometric step, the vibrato's phase and delay
ramp, the pitch envelope, the bend x range + wheel at this quantum's
end), times the copy's own octave / detune / unison factor. Reading the
pitch a root period late instead (the first attempt) failed the corpus on
a glide or vibrato alone (voice source, glide: envelope p95 1.3 dB;
vibrato: harmonics 1.1 dB RMS / 5.5 max; a C2 root with a glide: 13.7 dB
of envelope) — with `ahead` those are the engine's samples (0.000 /
0.000 / 0.000 / 0.002 dB). **A change to `ctl()`'s pitch math must be
mirrored in `ahead`**; the `sampler_formant_voice_glide` and
`_low_root` cases fail if it is not.

What `ahead` does not hold keeps the WAV path, named by the gate
(`paramui.py`): a mod route to `pitch`, `osc1_detune` or `vibrato_depth`
(`modN_src` — the same routes play live on the tape read) and cross-FM
(`fm_amount` with an osc2: osc2's audio moves osc1's pitch per sample).
A drawn stretch and the WSOLA stretch are `sampler_stretch`.

**What a player does mid-note is the one thing not the engine's.** The
wheel (or a legato bend, or a slider) cannot be foreseen: it reaches the
grain schedule up to one root period late, and smoothed over ~5 ms like
every bend. After it the grains run at the right spacing — the pitch is
right — from a different phase than `_psola` rendering the same gesture
as a recorded curve. On a tone that is periodic at the root, bent up,
that is only a phase and the corpus numbers hold (`sampler_formant_bend`:
level 0.02 dB, harmonics 0.52 RMS / 1.15 max, envelope p95 0.40). Bent
down (the gaps between grains ripple the 10 ms envelope at the played
period), or on a voice whose own period wanders (each grain is cut from
another place), they do not: bent 7 st down, the tone is 2.2 dB off in
envelope p95 and the voice source 2.4 dB RMS / 6.8 max in harmonics and
2.5 in envelope; bent 7 up, the voice is 3.0 / 9.0 in harmonics; level
stays within 0.06 dB throughout. That is the engine's own sensitivity to
when the gesture lands, not a fault of the port: `Synth.note` against
itself with the wheel's step moved by one render quantum (2.9 ms) is off
by 2.3 / 7.4 / 2.4 on the same voice bent down and 2.2 dB of envelope on
the tone (and by exactly 0 for a move smaller than the gap between two
grain centres). No causal voice can do better; those cases are therefore
not in the corpus, and the gate cannot name them (a gesture is not a
patch field).

Smaller print: the engine stops laying grains at its buffer's end, so the
last grain length of a `Synth.note` buffer has nothing overlapping it —
the voice has no buffer end and keeps going (the end of the release:
under every limit). A context at 48 kHz plays the engine's grains in
time (`ratio` = 44100 / 48000: g / ratio samples long, read `ratio`
source samples apart) — level within 0.5 dB per 10 ms, same length, same
pitch, under node (`test_a_48k_context_plays_the_same_grains_in_time`);
`_psola(sr=48000)` itself would lay 2-period grains 1.84 periods long,
which is not what a 44.1 kHz render sounds like. Cost
(`tools/bench_live_voice.py`, case 4, 14900K, 16 keys from the root to
two octaves up — 2 to 8 grains sounding per voice): 7.8% of the realtime
budget against the tape read's 4.1% (the median synth sound: 7.1% JS,
4.6% WebAssembly), so the grain loop is JS only, like the read. In
headless Chromium (`tests/browser/test_sampler.py`): the formant pill
leaves the sound on the worklet, and a key an octave under the root
sounds for the clip's own length, where the tape read takes twice that.

**The stretch in "resample" mode (`_grains`, WSOLA) is still not live.**
It has the same shape (grains on the output clock, the pitch read at each
grain's centre — 23 ms ahead there), and its search is deterministic and
local (the best of 2 x 10 ms + 1 lags of a cross-correlation over one
46 ms grain, read at the pitch ratio, so nothing about it can be
precomputed per sample: the ratio is the key). What stops it is the cost
of matching it exactly: at 48 kHz 961 lags x 2208 products per grain, a
grain every 11.5 ms per unison copy. Measured in node on an i9-14900K:
1.53 ms per grain done directly (13% of realtime per voice; one search
is 57% of a render quantum), 0.18 ms by FFT (4096-point; 1.6% per voice,
25% for sixteen — and sixteen keys struck together search in the same
quantum: 107% of it). It would need the FFT in the WebAssembly module and
the searches spread over the hop before each grain; not built.

### Pitch detection and slice markers

**Pitch detection** (`sampler.detect_pitch`: YIN over a dozen frames of
the sample's loud part — within 6 dB of its loudest frame — median note,
confidence = periodicity x the share of frames agreeing within half a
semitone; `sample_pitch` memoises it by id): a new sampler sound
(`sampler_patch`, so both `/fragment/sampler/clip` and `/resample`) takes
the rounded pitch as `sampler_root` at `PITCH_SURE` (0.8), else 60. The
panel shows "detected A3 · 97%" from `PITCH_SHOWN` (0.5), else "no clear
pitch"; its "use" (`POST /fragment/sampler/root`) sets the root as one
undo step and sends the root's slider row back OOB (`param_rows_oob`: the
#patch form would post the old root back otherwise). **Slice markers**:
each chop's start is a `.slice-mark` (role=slider, focusable) over the
waveform, dragged or arrow-nudged (5 ms, shift 50 ms) by layout.js,
which posts `/fragment/sampler/slice` `action=move`, `at` = 0..1 of the
sample, once per gesture (arrows debounced); the server clamps it
(`sampler.start_bounds`: after the previous slice's start, before its
own end, each by `MIN_SLICE_SECONDS` = 10 ms — the nudges use the same
minimum) and the touching neighbour's end follows, as for a nudge.
layout.js puts focus back on the new marker after the OOB swap.

### Zones: a multi-sample instrument

**Zones** (the engine is the root `docs/engine.md`,
"Sampler", the zone paragraph; `samplerview.py`'s zone section,
`routes/sampler.py`, `fragments/sampler_zones.html`, `static/zonemap.js`,
`tests/studio/test_sampler_zones.py`, `tests/browser/test_sampler_zones.py`).
A sampler sound may hold several recordings, each a **zone**: the keys and
velocities it answers, its root, its round-robin take, its gain and fine
tune (and, when it has one, its own span and loop; else the sound's start,
end and loop sliders apply to every zone's sample). The zones live in the
Patch (`sampler_zones`), so a zoned sound is a sound like any: state.json,
project.json, presets (format 2: each sample by its content id),
layers, song tracks, every render key.

Building one:

- **＋ zone** on a clip — the drawer's row on Synths (`.cd-zone`) and the
  clip hub's Send to ("＋ as a zone") — posts `POST
  /fragment/sampler/zone/add` (`name`): the clip into the sample store,
  then `samplerview.add_zone` (`sampler.place_zone`) puts it at its
  detected pitch (`PITCH_SHOWN`; no clear pitch: the key above the
  highest root) **without moving a zone a player set**: in its layer
  (the same velocities and take), a zone already rooted there gains it
  as its next round-robin take; one whose keys hold its root gives up the
  keys on its side of the midpoint (with that zone's takes and layers on
  the same keys); else it spans the free keys around its root. A sound
  that samples nothing becomes a sampler of that one zone (a load); a
  single-sample sampler keeps its sample as zone 1. The answer is always
  Synths' whole page (morphed in place there, the drawer left open for
  the next clip), one undo step.
- **Auto-map**: a drawer group's **⤷ zones** / **⤷ keys** (on Synths, a
  group of two or more; a search narrows the group) posts `POST
  /fragment/sampler/zones/map` (`names`, one per line; `mode`; `label`):
  the clips as one new sampler sound (a load, landing on Synths, one
  undo step). "pitch" places them lowest pitch first with `place_zone`
  (so the keyboard splits half way between their roots, and two clips on
  one pitch are its takes); "keys" puts them a key each from C4 in the
  group's order (a kit).
- **The Sample Lab's chops** land as a chopped sampler sound; the panel's
  **Chops → zones** (`POST /fragment/sampler/zones`, `action=chops`,
  `sampler.zones_from_slices`) turns them into a zone map, each slice
  alone on its key at its own pitch (a reversed slice plays forwards
  there). The Lab's vocal instrument is a one-sample formant sampler:
  ＋ zone adds to it.

The editor (`fragments/sampler_zones.html`, in the sampler block while
the sound has zones, where the waveform, pitch and chops would be: they
do not play): a **plane of keys (x, 0..127, over a keyboard strip with
each zone's root ticked) by velocity (y, 0..127 up)**, each zone a box; a
later take is dashed, its tag lower. **zonemap.js** drags a box's edge
(its low / high key, low / high velocity) or the whole box and posts its
four values once per gesture (`/fragment/sampler/zone`, `action=set`;
the server clamps, an edge stops at the other); the arrows move a
focused box a key (Shift: its high edge, Alt: its low edge), up / down 8
velocities, a burst one post; focus comes back to the zone's new box
after the OOB swap. The box is `role=slider` (piano.js's arrow bend
leaves it alone). Each zone's row steps its root (and ⌖: its sample's
detected pitch), take, gain (1 dB) and tune (5 cents) with
`action=step` (`field`, `by`: a step from where it stands, so two quick
presses are two steps even before the panel comes back), and ✕ removes
it. The head: ＋ zone (opens the clip library), **Spread** (each layer
over the keyboard by its roots: `sampler.spread`) and **Clear** (the
first sample alone again). A box and its row light each other on hover
and focus. **What a played key sounded is lit**: the live voice reports
its pick (sampler-worklet.js -> the node's port, a "zone" message ->
livefx.js -> `synthlab:zone`), and zonemap.js flashes those boxes and
rows (`.hit`) and writes it on `#zone-map` (`data-last`, `data-last-key`,
`data-last-rr`, `data-hits` — what the browser journey reads).

The live keys play a zoned sound from the worklet (live-voice.md, "A
strike a voice"): every zone's source in one payload, the pick and the
round robin as the engine makes them. The `/note` WAV keys (a payload
past the cap, an offline field) render at velocity 1 and strike 0.

### Undo order

**Undo order**: `push_undo_step(before)` goes AFTER the edit — it keeps a
step only once the patch has moved off `before` (called first, it
would keep nothing, and Ctrl+Z would skip past the edit).

## The audio travels in the bundle, not in project.json

**The audio travels in the bundle, not in project.json**: `GET
/project.zip` (`projectzip.py`, "Export project + audio" in the Synths
dock) is project.json exactly as `/project.json` serves it, plus
`samples/<id>.wav` for every `sampler_sample` anywhere in the payload
and every zone's sample under a `sampler_zones` (`sample_refs`)
(preset files are not part of a project) and `clips/<name>.wav` +
`.meta.json` for every clip it references (`state.clip_refs`: the lane,
drum one-shots, song takes and drums track, baselines — the traversal
`rename_clip_refs`/`remap_clip_refs` use). The bundle is deterministic:
sorted entries written through `files.zip_write` (a fixed date,
`ZIP_EPOCH` = 1980-01-01, a fixed creating system and mode — no wall
clock), so one session is the same bytes whenever it is exported; the
stems zip is written the same way. `POST /project/import` takes
either form (a body starting `PK\x03\x04` is a bundle). A bundle is read
strictly and refused whole before anything is written: only those names
(so no traversal, absolute names, directories), no case-folded
duplicates, `limits.MAX_PROJECT_ZIP_MEMBERS` / `_UNCOMPRESSED`, each
entry read no further than it declares, clips plain 16-bit PCM WAVs,
metas JSON objects. The project loads first; then samples whose content
doesn't hash to their name are **skipped** (logged), and a clip **never
overwrites**: same name + same bytes is reused, a name taken by different
audio installs the incoming clip as "name 2" (clear of the bundle's own
names) and the loaded session's references are remapped all at once and
saved. The path's body cap is `MAX_PROJECT_ZIP_BYTES`; a bare JSON body
is still held to `MAX_PROJECT_UPLOAD_BYTES` by the route (413).
