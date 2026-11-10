# The studio's notes — index

How `synthlab/studio/` works, for anyone changing it. Setup, commands and
the venv / architecture rules are in `CONTRIBUTING.md` at the repo root; the
engine notes and the new-Patch-parameter checklist in `docs/engine.md`.

This file is an **index**. The contracts themselves are in the files beside
it, one per area: find your task in the table and read that file (its `##`
headings are the map). The wording there is
load-bearing; tests and other docs quote it.

## Rules every studio edit needs

- **Restart to see a change.** The server does not hot-reload templates,
  static files or Python. To test beside a studio you are using, start a
  scratch one on another port with a temp data dir (the browser tests do:
  `tests/browser/conftest.py`).
- **A route handler is a plain `def` taking `form: StrForm`.** `StudioRoute`
  runs it under `StudioState.lock`; an `async def` is refused unless
  `@lock_free`; a long render returns `OutsideLock(run)` under
  `@render_outside_lock`. Both sets are pinned in
  `tests/studio/test_routes.py` (`LOCK_FREE`, `RENDER_OUTSIDE_LOCK`) beside
  `EXPECTED_ROUTES`. → `docs/routing-and-boundary.md`, "One concurrency
  model".
- **No stale regions: a region declares what it shows; routes never name
  regions.** `service.REGIONS` holds each region's `watch`; the route
  wrapper re-sends, OOB, whatever moved. A new POST route needs a `CASES`
  row in `tests/studio/test_oob_refresh.py`; suppress the form that posted
  when redrawing it would drop the gesture. → `docs/views-and-regions.md`.
- **A control that posts on `change` / `input` carries `hx-sync`**:
  `this:queue last` for a value, `this:queue all` for an action. htmx 4's
  default drops every post after the first one waiting, so a stepped knob
  leaves the server on a stale value (`tests/studio/test_change_posts.py`
  scans the templates). → `docs/frontend.md`, "The stack".
- **One render entry point**: `svc.render(view, ...)`; never
  `render_loop`/`render_audio` from a route. `RenderInputs.key()` must
  carry every input that affects audio, and a stem key everything its
  render reads. → `docs/render-and-caches.md`.
- **CSP is `script-src 'self' 'wasm-unsafe-eval'`**: no inline script, no
  `eval` / `new Function` / `js:` attributes, scripts vendored into
  `static/`. A new route is covered by the boundary with nothing extra.
  → `docs/routing-and-boundary.md`, "Boundary".
- **JS islands have line budgets** (`Budget: <= N code lines` in each
  file's header, held by `tests/studio/test_island.py`, which also pins
  the number of islands). Raise a budget deliberately or move code out;
  never join lines to fit. → `docs/frontend.md`, "JS islands".
- **htmx 4 traps**: never the `changed` trigger modifier; `htmx.ajax`
  posts every named input on the page; a swap that replaces the focused
  control loses the keyboard's place — morph instead; read `HX-Prompt`
  only through `routing.prompt_value(request)`. → `docs/frontend.md`.
- **Forms post everything, so the server keeps what a form does not
  carry** (`_merge_arrangement`), and a patch post applies only what
  moved. → `docs/views-and-regions.md`, "Form posts".
- **A new persisted key or `Arrangement` field needs a schema node** (and
  an Arrangement field is written down through `schema.arr_dict`, absent
  when unset: `ARR_OPTIONAL`, so no saved state or key gains it); a
  new session field goes in the constructor, in `OWNERS` if it is runtime
  state, and in each mixin's annotations. → `docs/state.md`. The same
  holds for a new `StudioService` field (`service/__init__.py`'s
  `OWNERS`); a new service method goes in a unit of `service/`, never in
  its `__init__.py`. → `docs/routing-and-boundary.md`, "`service/`".
- **A control is built from the design system, and an action declares
  itself.** `.btn` / `.field` / the other canonical classes and the
  tokens — never a new button family, a raw colour or `color:
  var(--accent)` (`--accent-text`); dense rules go in `static/dense.css`,
  touch sizes inside a `(pointer: coarse)` block's `html:not([data-pen])`;
  an action gets `data-cmd` on its control (that is its whole entry in the
  command palette) and `data-keys` if an island binds a shortcut to it; a
  hint is a `title`, never a paragraph. → `docs/frontend.md`, "Design
  system"; `tests/studio/test_design_system.py`.
- **A new top-level section needs its own named grid area in both
  breakpoints**, and layout changes are checked in a real browser — the
  suite does not catch layout breakage. → `docs/frontend.md`, "Layout
  gotcha".
- **The live voice must match the engine.** JS (`voice-worklet.js`) is the
  reference, the WebAssembly module mirrors it; a Patch field the worklet
  cannot reproduce stays `DEFAULT` in `paramui.py` so keys fall back to
  the server WAV; never a top-level `function` or `var` in
  voice-worklet.js. → `docs/live-voice.md`.

## Working on X → read Y

| Working on | Read (`synthlab/studio/docs/`) |
|---|---|
| A new route, `app.py` / `routes/*.py` wiring, locks, uploads, long renders | `routing-and-boundary.md` |
| Host / Origin / share token / CSP / body-size caps | `routing-and-boundary.md` ("Boundary"); `sample-lab.md` ("Limits") for the Lab's caps |
| `StudioService`, the `service/` package: which module holds what, `OWNERS`, the names tests patch on it | `routing-and-boundary.md` ("`service/`: the service package") |
| What a view renders; regions, OOB refresh, `REGIONS`, watches, suppression | `views-and-regions.md` |
| The `#patch` / arrangement forms, `_merge_arrangement`, `syncControls` | `views-and-regions.md` ("Form posts") |
| htmx behaviour, JS islands and budgets, density / splitters / folds, CSS grid areas, page weight | `frontend.md` |
| **The workspace**: a module moved, tabbed, folded, popped out, focused; a page's docks (`data-ws-dock`), layouts, presets and added modules (workspace.js, `studio/workspace.py`, `fragments/ws_menu.html`, `GET /fragment/module/{key}`) | `frontend.md` ("Workspace: modules you can move") |
| The top dock's scope / spectrum, a new visualiser variation, the Visuals page (`/view/visuals`: visuals.js, vispage.js, `studio/visuals.py`) | `frontend.md` ("The visualiser registry") |
| **Any new control, colour, size or shortcut**: the design system — tokens, the palette (its tiers, one meaning per hue), the module frame (`.module`, `data-module`), the canonical components (`.btn`, `.field`, `.meter`, `.cable`, `.knob` …), `dense.css`, the pen, tweakable controls, `data-cmd` / `data-keys` (the command palette and the shortcut sheet), the status strip, tooltips | `frontend.md` ("Design system", "The palette", "The module", "The light model", "The energy" — the glow, a played module's edge, `--energy` —, "Motion: full or calm" — the OS's reduced motion is not followed: `synthlab.calm()`, `html[data-motion="calm"]`) |
| The Synths page: sound strip, chain bar, panels, phrase, A/B, vs beat, macros, chord bar / chord mode, Bass check | `synths.md` |
| `svc.render`, stems and stem keys, loop assembly, `RenderCache` / `NoteCache` / `StemCache`, the note memo | `render-and-caches.md` |
| Layers: what each lane plays, following layers, the main lane, mute / solo / focus, the layer strip | `lanes-and-layers.md` |
| `StudioState`, the `state/` package, `schema.py`, migration, reset / revert scopes, project export / import | `state.md` |
| The drum machine, **the per-hit model** (a hit's velocity / chance / ratchet / nudge / tune, a row's swing: `arrange.drum_hits`, the one schedule), **the step sequencer** (`drumgrid.html`, steps.js), variations A-D, fill / build / busier / sparser, Euclid, kit swap, bank, notation, row heads, generator | `drums.md` (kits and beat styles: `sounds-and-breeding.md`) |
| Perform: parts, launchers, performance FX, the note / drum grids, pattern tools, pattern undo, cuts, grid.js; the loop's chord track (Perform's chord lane, `POST /fragment/chords/track`) | `loop-and-perform.md` ("The chord lane") |
| The Song page, song model, bridges to the loop, recording, risers, automation lanes (level, fields, FX-unit parameters: `fxauto.py`), the playlist (a bar ruler, time-true lanes, zoom, seek, the edges: `songline.js`), drags onto a lane (`songdrag.js`: a drag moves, Ctrl or Alt copies), block spans, a whole stem on the timeline (long audio: `longaudio.py`, `routes/song_long.py`), the playlist tools (`songops.py`, `routes/song_arr.py`, `songsel.js`: a cut at a bar, unroll, insert / delete bars, a range of cells, the energy lane and "arrange by energy", a transition on a boundary, the chord tiles), the shared grid macros | `song.md` |
| **The mixer** (the Mix view: a strip a track — level, pan, sends, duck, EQ — the returns, the master strip, Balance; `studio/mixer.py`, `routes/mixer.py`, `service/mixpage.py`, mixer.js) | `song.md` ("The mixer"); the render — pan, sends, returns, stem levels — `song-render-export.md` ("The mixer: pan, sends, returns, levels") |
| **The jam** (the Song page's launcher: scenes = sections, pads = cells, quantised launches of the cell loops `/song/cell/..wav`, the log, capture to sections; `static/jam.js`, `fragments/jam_bar.html`, `studio/jam.py`, `routes/song_jam.py`) — not the Loops page's "⏺ resample" (a clip) | `song.md` ("The jam") |
| Section rendering, song playback == export, a range loop's wrap pass, loudness targets, stems, MIDI / FLAC / MP3 | `song-render-export.md` |
| Song creation: starters (`POST /song/start`), loop → song, the chord track, the generators (`studio/gen/`), the four styles, the checklist, track roles, the head plan a new song starts on, `song-autoplay` | `songcraft.md` |
| QWERTY / touch / typing rows, the keyboard dock (its toolbar: Play — each key plays, hold, latch, the keys arp (arp.js), octave — · QWERTY — typing, the rows — · Rec — keyrec.js — · Talk · Tools; keysbar.js: the shortcuts, the keys lit with what they play), the pitch wheel, WAV keys and held-key loops (keys.js), midi.js | `keys-and-midi.md` ("The keys arp") |
| Hardware control: the control surface (deck.js — dial slots, macro / focus mode, the strip, the key table), the Stream Deck + over WebHID (deckhid.js), the virtual deck (deckui.js), both decks' look (deckart.js), MIDI learn (midilearn.js); the pen (pen.js — pressure, eraser, barrel) ; the one context menu, a value control's rows included (menu.js); a typed value (tweaktype.js); **which island owns which gesture on which control** | `keys-and-midi.md` ("Control surfaces", "Gestures: one owner each", "Pen") |
| The AudioWorklet voice, parity corpus, `/wavetables.bin`, `worklet.py`, live layers and FX (`fxlive.py`, livefx.js, fx-worklet.js), live CPU, the WebAssembly voice | `live-voice.md` |
| Browse / audition / Use (`sounds.py`), the library, factory patches, preset format 2, breeding (`breedboard.py`, `genome.py`), genre recipes / chain presets / kits / steering | `sounds-and-breeding.md` |
| Clips, the clip library drawer (every page), bounce, jam, the Voice lab (the vocoder: its take, four carriers, `voice_lab.json`, Keep result), **the vocal booth** (4 · Treat: treatments, the offline moves, `booth.py`, `routes/booth.py`), the sampler panel and its live keys, `/project.zip` | `sampler-and-clips.md` |
| The Sample Lab: import, tempo / key, regions, stems and what they become; chops (cut, clean, keep / Update clip), stem download, "Open in Lab" | `sample-lab.md` |
| **The clip hub** (`/view/clips`: every clip and sample — the list, waveforms, the bench's player, tune / keep / update, send to each destination's own route, make a beat / notes / a sound from a clip with a preview and one-step Apply + Undo, ✦ the Gemini morph; `cliphub.py`, `clipmake.py`, `clipmorph.py`, `service/hubpage.py`, `routes/cliphub.py`, cliphub.js) | `clip-hub.md` (the Gemini call: `agent.md`, "The clip hub's morph") |
| `GET /events`: progress and change notices | `events.md` |
| The Gemini sound agent | `agent.md` |

A topic lives in exactly one file; the others name it. When you move or
rename a section, update the files that point at it
(`git grep -n "<file>.md"` from the repo root: `CONTRIBUTING.md`, a few
comments and tests name sections by title).

**Read `tests/studio/` with the matching area file open**: most contracts
above name the test that pins them.
