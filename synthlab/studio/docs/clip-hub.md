# The clip hub (`/view/clips`)

Every clip and sample in one place: listen, tune, send it anywhere, and
make a beat, a note part or a sound from it — by the pickers, or by a
description Gemini reads while it listens. `studio/cliphub.py` (the stores,
the waveform, the measured tempo / key, the tune, `HubSession`),
`studio/clipmake.py` (the plan and the makers), `studio/clipmorph.py`
(Gemini's plan: `agent.md`, "The clip hub's morph"), `service/hubpage.py`
(`HubPage`: the context), `routes/cliphub.py`, `templates/views/clips.html`
+ `fragments/hub_*.html`, `static/cliphub.js`, style.css section 31 /
dense.css D31; `tests/studio/test_clip_hub.py`,
`tests/browser/test_clip_hub.py`. The clip library drawer
(`sampler-and-clips.md`, "The clip library") stays the quick picker on
every page. Index: `README.md`.

## A view, not a bigger drawer

The hub is a page on the rail ("Clips" — the name of the drawer's button too —, last, after Lab: `views.VIEWS`
`"clips"`, its parts `("synth",)` like the Lab's, no preview cued on a
view swap). A drawer is the right shape for *picking* a clip while you
work somewhere else — it already does that on every page — but the hub
holds a waveform per row, a player, a tune form, eight sends and a make
form with a preview: a drawer that size is a page that hides the one you
are on, and its every redraw would have to morph a popover holding all of
it. So the drawer stayed light (no waveform, no audio: its rule) and the
hub took everything that needs room.

## The list (region `hub-list`)

`cliphub.items` lists, by **item reference** (`cliphub.parse_ref`: the
one parser; anything else is None and a 404 / toast):

- `clip:<name>` — the clip store, grouped by `cliplib.kind_of` (Lab chops,
  voice takes, vocoder, jams, bounces, risers, other);
- `sample:<16 hex>` — the sampler's content-addressed store
  (`studio_data/samples/`), "used by" the edited sound, the other layers
  and the song's tracks whose sampler plays it (`cliphub.sample_usage`);
- `stem:<source>:<model>:<stem>` — a separated Lab song's stem: listen
  (streamed from the Lab's own WAV route), download, open in the Lab.
  Nothing cuts or makes from a stem here: it is minutes long; the Lab cuts.

A row (a table — the kind a dim column of its own, the
source, then the length, tempo and key as read-out cells, the Loops
lists' `.lp-facts`; a short list's rows grow to 64 px, their waveforms
with them, so the list fills its column): ▶ (audio.js's `.audition[data-note]` one-shot, as in the drawer),
its waveform (inline SVG from `cliphub.peaks`, 64 columns, memoised by the
file's path + mtime + size), name, kind, source, length, bpm and key —
the meta's when it states them (a bounce's clock, a Lab chop's key), else
**`~` measured** (`cliphub.measure`: `analysis.tempo` and `detect_key`,
once per version of the file, when the item is opened on the bench —
before the list is drawn, so its row says the same in that answer:
`hub_context`) — and
where the session uses it (`cliplib.where_used`, the drawer's words). The
list's head holds the hub's **↶ Undo**.

Search (`find`), kind (`show`) and sort (`order`: name, newest, longest,
shortest, kind) are a form whose GET answers the LIST only, so the box
being typed in is never redrawn.

**The hub keeps its choices in its URL, never in the session**:
`/view/clips?item=clip:<name>&find=&show=&order=`. The hub's GETs answer
`HX-Replace-Url`; every request the page makes carries the URL
(`HX-Current-URL`), so a region redrawn after any post — a send, a delete
in the drawer — draws the same filter and selection (`HubPage.hub_params`:
the URL, then the GET's own query, then a whole-page answer's
`request.state.hub`; never a posted form's, whose `item` is the thing it
acts on). The names are not `q` / `kind` / `sort` on purpose: the drawer's
search is `q`, and its posts ride every page.

**The watch** (`regions._w_hub`) opens no file: the clip store's listing,
the sample store's, `clip_usage()`, `sample_usage`, the Lab's
`sources_sig`, which clips have been measured and to what
(`cliphub.facts_sig`), and `hub_undo_label()`.

## The bench

`GET /fragment/hub/bench?item=` (a row's click, `hx-get` on its link).
**Never empty**: a page or a region with no `item` in its
URL opens the item this browser last heard or opened — the `hub_item`
cookie cliphub.js writes on a row's ▶ or link and the bench's ▶ (a
per-browser convenience like the theme: no session state) — while it is
there, else the first row the list shows that plays (`hub_default`; a
stem streams minutes). An `item` that names something gone stays so:
the bench's route toasts it. From 1200 px the bench reaches the foot:
the waveform (the signal colour over a scope's graticule) takes the height the rest leaves, never
under 120 px, and Send to and Make from it stand side by side.

- **facts**: kind, source, length, tempo and key (with how sure the
  measured tempo is), where it is used ("in use: 2 takes in the song" —
  `cliplib.where_used_short`, as the list row says it, in the dim text
  colour: what plays it, never the undo ring's count, which is how a
  delete is weighed; held only by undo steps: "in the song's undo
  history". It read "used: 2 takes in the song, 9 undo steps…", amber);
- **the player**: the shared `.player` (bench.js: ▶ / ⏸, the bar as a
  seek control — click, ← →), its bar drawn as the clip's waveform, the
  played share washed (`--at`); **⟲** loops it (cliphub.js, kept across a
  redraw). Its audio is `GET /hub/audio.wav?item=…&<tune>` — lock-free,
  the stores only, memoised by (version, settings);
- **Tune** — semitones (±24), cents (±100), tape rate (0.5-2, pitch and
  speed together), gain (-24..+12 dB), trim start / end (an end left
  empty is the clip's end, its length the placeholder: "0.0" read as no
  length), reverse.
  `cliphub.clean_tune` parses, clamps and makes finite every field;
  `cliphub.tune` trims, reverses, pitches with the length kept (resampled,
  then stretched back by the sampler's WSOLA `time_stretch`), applies the
  rate and the gain, and cuts at `MAX_CLIP_SECONDS`. A change re-asks for
  the player (`GET /fragment/hub/player`, its audio URL carries the tune)
  and a player that was playing plays on from where it was. **Nothing is
  written until**:
  - **Keep as new clip** (`POST /hub/tune/keep`): "<name> tuned", never
    over a clip that is there (`free_clip_name`); its meta says where it
    came from and how (`tune`: the settings and `from`), keeps `source` /
    `lab`, and a bounce's `bpm` moved by the rate (`bars` only while the
    length is untouched). `"tune"` is in `clips.META_SETTINGS`: a dict,
    dropped by `clean_meta` (and a bundle's import) if it is not one;
  - **Update this clip…** (`POST /hub/tune/update`): the hub's one
    overwrite, behind an `hx-confirm` naming where the clip is used (the
    Lab's "Update clip" words: `cliplib.update_note`). Its old WAV and
    meta are kept in memory (`HubSession`, an `Overwrite` step) for the
    hub's Undo; `reset_caches()` after the write, as the Lab does (a clip
    is in render keys by content). Both answer the whole hub page, the
    clip selected, `HX-Push-Url`.

### Send to: each destination's own route

Nothing in the hub writes to a destination; the bench posts each one's
route, as the drawer does:

| to | route | answer |
|---|---|---|
| Synths, as a sampler sound | `POST /fragment/sampler/clip` | lands on Synths (one undo step there) |
| Synths, as one more zone of the sampler (a multi-sample instrument) | `POST /fragment/sampler/zone/add` | lands on Synths (one undo step there); a sound that is no sampler yet becomes one of this zone |
| a drum row's sample (eight buttons) | `POST /fragment/drums/sample` (`voice`, `clip`) | `hx-swap="none"`; the bench says "in use: 1 drum row" (out of band) |
| the song, at a section / bar / audio track | `POST /song/section/{id}/clip` | `hx-swap="none"`, its toast; Ctrl+Z on Song undoes. The section is the route's path: choosing one asks for the panel again (`GET /fragment/hub/send?place_section=`) |
| the Voice lab, as the take / the carrier | `POST /voice/take/clip`, `/voice/carrier/clip` | `hx-swap="none"`, their toasts |
| the Sample Lab | the clip's own `lab` link (its song, its chop the region) — else `POST /hub/lab`: the clip imported as a song (`sources.add` with `lab.analyse`, the Lab's own import, with the lock released), answered `HX-Location` to it | |
| Perform | no route of its own: Perform's scenes are the song's sections, so a clip placed in a section plays when its scene is launched (the Place button's title says so) | |

A **sampler sample** is kept as a clip first (`POST /hub/keep`, "sample
<id>", `source` "sampler sample"), and then has every send a clip has. A
**stem** offers Open in Lab and download.

## Make from it

A form (`fragments/hub_make.html`): the target — **→ Beat**, **→ Notes**,
**→ Sound** (only that target's pickers show: CSS `:has()`, no script) —
and its pickers, each with an **auto** that means "the default, or what
the description asks for". **▶ Preview** posts it (`POST /hub/make`,
`@render_outside_lock`); nothing is written. The result
(`fragments/hub_result.html`, `#hub-result`) says what was heard and done,
shows a beat's rows as notation, plays the preview (the render, served by
`/preview/<key>.wav` in the shared player; cliphub.js starts it), and
offers **Apply**, **↻ another take** (the plan shown, the next seed) and,
after ✦, **✦ ask again**.

**The plan** (`clipmake.clean_plan`, the one place a plan is checked —
the pickers', Gemini's, and the one Apply posts back):

| field | values |
|---|---|
| `target` | beat, notes, sound |
| `kit` | own (the clip's own hits), keep (the kit on Drums), a `drumfactory.DRUM_KITS` name |
| `style` | clip (the beat as heard), a `beatgen.STYLES` name (blended: below) |
| `feel` | as is, half-time, double-time |
| `fill` | none, half, bar |
| `density` / `amount` | same, busier, sparser / 0.05-1 |
| `swing` | none (the clip's own) or 0-0.6 |
| `loop_tempo` | the loop takes the clip's tempo |
| `notes_from` | melody, rhythm, or a loop generator (`gen/loop.KINDS`: bass_root, bass_rolling, chords, pad, arp, lead) |
| `sound` | keep, clip (the clip itself as a sampler), or {source: library / recipes / factory, name} — the name must be in `clipmake.sound_catalogue()` |
| `sound_mode` | whole, chops, voice (the clip as a sampler: `labmake`) |
| `snap` | loop (the loop's key), clip (the clip's measured key: the loop moves to it), none (as sung) |
| `transpose` | -24..24 semitones |
| `fx` | keep, none, a `fxrack.CHAIN_PRESETS` name (the drum bus for a beat, the lane for notes) |
| `summary`, `why` | Gemini's words, cut to 200 characters, at most four lines |

A name the studio does not have falls back to its default **and a line
says so** in the result ("kit “Nine Inch Wails” is not one the studio
has"); every number is parsed, finite and clamped; a posted plan is JSON
of at most `MAX_PLAN_CHARS` and an object, or there is no plan.
`clipmake.locked` is what the player set (not auto, a transpose off 0, a
swing, the tempo box): it wins over Gemini's plan (`clipmorph.merge`).

**The clip's grid** (`clipmake.clip_grid`): its meta's tempo (a bounce, a
fitted chop: bar 1 at its start), else the Lab song's, else the measured
one when it is at least `MIN_TEMPO_CONF` sure (bar 1 at its first downbeat
— a guess), else the loop's tempo from the clip's start. Bars: the most of
`limits.BAR_CHOICES` (1-4) the clip holds.

What each make really does — and its limits, which the result says:

- **→ Beat** (`make_beat`): `drumhits.transcribe` over the clip's bars —
  kick, snare, closed and open hat (four lanes; claps, toms, rims and
  crashes are not told apart), sixteenths, its swing, a velocity lane. A
  dense mix reads rough; a lane heard only under another drum has no hits
  of its own. Then: a style **blends** (the clip's kick and snare under
  the style's other rows and feel, `gen.drums.beat`), half-time spreads it
  over twice the bars (capped at 4), double-time plays it twice in its
  length (even steps only), busier / sparser (`gen.drums`), a fill
  (`gen.drums.fill`), the swing. The kit: **own** cuts one clean hit a
  lane (`labmake.kit_hits`: only a hit that is that drum *alone*; on a busy
  loop often only the hats qualify) onto those rows' sample slots;
  a **named kit** loads its voices' shaping as a kit swap that keeps the
  beat does (`drumops.swap_kit`) and sets the four transcribed rows back to
  synth. A beat with no hits and no style to blend is refused.
- **→ Notes** (`make_notes`): **melody** — YIN on one line
  (`voiced.pitch_track`, 70-1100 Hz), a sixteenth step the median of its
  voiced frames (half the step at least), snapped to the key, held while
  it stays, re-struck when the level jumps 1.8x. A monophonic voice, lead
  or bass line reads well; **chords, a full mix or a sub under 70 Hz do
  not** — the notes found are sparse or wrong (two or fewer: it falls back
  to **rhythm**, the clip's onsets on the key's root, each to the next
  onset, a beat at most). A **generator** writes a new part in the key
  (`gen.loop.write`). The lane is moved by whole octaves onto the roll's
  window and onto the working loop's grid (`gen.loop._onto`: notes the
  window has no row for are left out, and said), at the clip's bars and
  16 steps. Played by: keep, the clip as a sampler (registered in memory
  for the preview, `sampler.register`; saved to the store on Apply), or a
  Library / recipe / factory sound (`sounds.find`).
- **→ Sound** (`make_sound`): the clip as a sampler sound —
  whole (from its detected root), chops (`labmake.vocal_chops`, else the
  transients) or a voice (`labmake.vocal_instrument`: a held note looped).

**The preview** renders the candidate through `StudioService.
preview_inputs` (`render_inputs(view)` with the candidate's arrangement
fields merged in and `RenderInputs` fields replaced) and `render_preview`
(the one cache and timer, `_ensure`) — the inputs taken under the lock,
the render outside it. A beat is heard on the drum part (with the drum
bus FX it would get); an own-hit kit from a clip root of its own (a temp
folder under the system temp dir holding the cut hits and the other rows'
clips — nothing enters the clip store before Apply); notes on their own
(the sound alone, its FX; "preview with the beat" adds the drum part); a
sound on the reference phrase. The preview is not the whole loop as it
will play with the other layers.

**Apply** (`POST /hub/apply`, `@render_outside_lock`): the plan posted
back is checked again, the candidate made again from it and the seed (the
listening is cached by the clip's version — `clipmake._HEARD`, 32 — and
the generators are seeded, so it is the candidate previewed) and written
under the lock as a hand edit writes it:

- a beat: the cut hits saved as clips (`free_clip_name`, `source` "hub:
  <clip>") on their rows, `drum_vel`, the kit's params, the tempo, then
  `StudioState.apply_pattern`; the drum bus FX; the beat style;
- notes: `loop_refusal` first, then the note lane, bars and 16 steps
  (`steps_per_bar`), the key if it moved; the sound through `load_sound`
  (+ `push_undo_step`, as `adopt_audio` does) and the lane's FX; **and a
  loop** ("turn a sample into a loop": `routes/cliphub.py`
  `_keep_as_loop`): "<clip> notes" in the Loops list — the lane as
  applied, the sound now playing it, tempo, key, meter — whose audio
  (`take`) is the clip it was made from, so audio → loop is the Loops
  list's one object (`loop-and-perform.md`, "Loops"), open on the grid
  (`open_loop_edit`: an edit on Perform saves to it). The hub's ↶ Undo
  deletes it again (`Made.loop`). A beat is not kept as a loop (a loop
  is a note lane: a loop that carries a beat is not built), nor a sound;
- a sound: `routes.sampler.adopt_audio` itself (it lands on Synths).

**Undo is one step, two ways**: the grid's change is ONE pattern undo
step (Ctrl+Z on Drums or Perform takes the grid back; a sound is one
patch undo step on Synths), and the hub's **↶ Undo** (`POST /hub/undo`;
Ctrl+Z on the hub page — cliphub.js, in capture, before piano.js's patch
undo) puts back *everything* the Apply touched — the patch, the layers,
curves, drum bus FX, the arrangement, the beat style, the grid's span —
from a snapshot taken before it, and deletes the hits it cut that nothing
uses any more. It is refused once anything it compares has moved since
the Apply (`_snapshot` now != the one taken after): the page's own Ctrl+Z
is then the way back. An Update is undone by writing the old bytes back,
refused if the file changed since. `HubSession` holds the newest four
steps and is runtime: after a restart they are history, as an agent turn
is.

## Hostile input

Every value from a form, a query, a file or the model is untrusted:
`parse_ref` (a clip name through `clip_safe` and a containment check, a
sample id the store's shape, a source id the source store's), `clean_tune`
(finite, clamped), `clean_plan` (names from the studio's lists, numbers
clamped, text cut), `clipmorph.clean_describe` (300 characters), a posted
plan's size and shape (`_posted_plan`). The clip store's own rules hold
(`clips.clean_meta`; `MAX_CLIP_SECONDS`); the body caps are the
boundary's (`security.BodySizeLimit`), the forms being small.

## Not checked

Nobody has listened to a tune, a preview or an applied result: the tests
prove state, routes and that signal reaches the master analyser, on test
tones and synthetic drums. The transcription and the melody tracker are
the Lab's, with the Lab's limits. The live Gemini path is not exercised
by the tests.
