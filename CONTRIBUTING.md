# Contributing to synthlab

synthlab is an early **alpha**. Issues and pull requests are welcome; for a
large change, open an issue first so the design can be agreed before the
work. By contributing you agree your work is released under the project's
MIT licence (`LICENSE`).

The rules that hold the codebase together are written down — the studio's
in `synthlab/studio/docs/` (start at its `README.md`), the engine's in
`docs/engine.md` — and most of them are pinned by a test. Read the one for
the area you touch before changing it.

A pull request should pass `uv run ruff check synthlab tests tools`,
`uv run ruff format --check synthlab tests tools` and the quick tier
(`uv run pytest -q -m "not slow"`); CI runs the same plus the NumPy
fallback, the WebAssembly check and the Rust kernels.

## Commands

```bash
uv sync --all-extras              # install (studio + playback extras, dev group;
                                   # also the `stems` extra: onnxruntime, for the
                                   # Sample Lab's Separate — its 316 MB model (258 MB for the 6-stem one) is NOT
                                   # installed here: it downloads on the first Separate
                                   # into %LOCALAPPDATA%\synthlab\models, or
                                   # SYNTHLAB_MODEL_DIR; never into the repo)
uv run pytest -q                  # full test suite
uv run pytest -q -m "not slow"    # quick tier: everything but the live parity corpus
uv run pytest tests/test_breed.py::test_mutate_is_deterministic_with_seed  # one test
uv run ruff check synthlab tests tools  # lint (scope to these dirs, not ".")
uv run ruff format synthlab tests tools
uvx pip-audit                     # dependency vulnerability check — run after any
                                   # `uv lock --upgrade`; CI runs it too
                                   # (.github/workflows/ci.yml: lint, the suite native
                                   # and NumPy, wasm --check, cargo, an ARM64 job)
uv run synthlab-studio            # launch the studio at http://127.0.0.1:8420
uv run python -m synthlab.studio.vendor   # fetch GSAP from npm (the first launch
                                   # does it; it is never committed)
uv run synthlab-studio --host 0.0.0.0 --port 9000 --share  # bind flags; --share
                                   # also prints a token URL, good until the studio
                                   # stops — as does any non-loopback --host, with
                                   # or without --share (studio.ps1
                                   # -Share does this with a firewall rule scoped
                                   # to Private + LocalSubnet — see
                                   # synthlab/studio/docs/routing-and-boundary.md,
                                   # "Boundary")
.\studio.ps1                      # clean restart: stops this checkout's studios (any port,
                                   # whole uv->python process tree) + whatever holds 8420, then launches
.\studio.ps1 status               # every running studio, any checkout: port, URL, /health, pids
.\studio.ps1 stop [-All]          # -All also stops other checkouts' studios — off by
                                   # default: another checkout may be in use
                                   # (a raw second launch while one is alive fails with WinError 10048)
# perf: request/render timing + cache hit-rate collection and the per-response
# Server-Timing header are always on; GET /debug/stats 404s unless
# SYNTHLAB_DEBUG=1, which also turns on per-request log lines in the terminal
uv run python demo_track.py       # render the 16-bar demo to demo_track.wav
uv run python tools/lab_measure.py [--audio <dir>] [--model 6] [--drums]  # Sample Lab: tempo/key, separation
                                   # time + peak RSS, what each stem holds (downloads
                                   # the model if missing)
uv run python tools/bench_live_voice.py [--voices 16|32]  # live voice CPU, JS vs WebAssembly
# the live voice's WebAssembly module (synthlab/studio/wasm/ -> static/voice-dsp.wasm,
# committed: running the studio never needs node). After editing wasm/assembly/*.ts:
uv run python tools/build_wasm.py          # npm ci (pinned, project-local) + npm run build
uv run python tools/build_wasm.py --check  # committed module == a fresh build?
cargo test; cargo clippy; cargo fmt        # from crates/: the Rust kernels alone
SYNTHLAB_NATIVE=0 uv run pytest -q         # the suite on the NumPy fallback
# browser smoke tests (tests/browser/, marker `browser`): Playwright + headless
# Chromium against a scratch studio. Opt-in — the `browser` dependency group is
# not in `uv sync --all-extras`, and without it (or its Chromium) they skip
uv run --group browser playwright install chromium   # once per machine
uv run --group browser pytest tests/browser -q       # 241 journeys
```

**Browser smoke tests** (`tests/browser/`, its `conftest.py` is the harness):
each test starts its own studio in-process (uvicorn in a thread, the first
free port from 8430, a pytest temp data dir) and drives it with a real
pointer and keyboard — every view cold and through the nav, the live keys
(worklet + wasm, typing, chords, a stand-in MIDI port, FX units under a held
note), Perform's grids / velocity lane / undo / MIDI out and in, Song's
drawer, automation lanes and export form, Breed, the sampler (mono and true
stereo). A test fails on any console error, uncaught exception, CSP
violation or 5xx. Audio is asserted through the page's own objects (the
master analyser's level, the worklet's `#cpu-meter` reports, a splitter on
the key bus), so they prove signal flows and the right engine holds the
voices — **nobody has listened**: timbre, tuning, clicks and latency are
not checked. Once the group is installed in a venv, a plain `uv run pytest
-q` runs them too (about a minute); `uv sync --all-extras` removes it
again. Not in CI. A `wait_for_function` must be given an arrow function: a
bare expression is `eval`ed in the page, which the CSP refuses.

The native kernels (`crates/`, Rust via PyO3/maturin) install from
prebuilt wheels in `crates/wheels/` (x64 and ARM64), so running needs no
Rust. After editing anything under `crates/`, rebuild them on the machine
with the toolchain — `uv run python tools/build_wheels.py` — and commit
`crates/wheels/` with `uv.lock` (`crates/README.md`).

The studio runs on Windows on both **ARM64** (e.g. a Snapdragon Surface)
and **x86-64**. The venv must match the host arch — a cross-arch
interpreter runs under emulation at roughly half speed, and `.venv` is not
portable between ARM and x86: check before trusting it.

```bash
python -c "import platform; print(platform.machine())"   # ARM64 or AMD64
grep home .venv/pyvenv.cfg                               # interpreter path; ARM's is ...-arm64
uv python list --only-installed                          # which arch each interpreter is
uv venv --python cpython-3.13-windows-aarch64-none && uv sync --all-extras   # recreate on ARM
uv venv --python cpython-3.14-windows-x86_64-none && uv sync --all-extras    # recreate on x86
```

Always pin the arch in `--python`: a bare version picks whatever uv finds
first, which can silently build an emulated venv. `requires-python =
">=3.13,<3.15"` is capped because a bare `uv sync` picks the newest Python
found, and onnxruntime and SciPy have no 3.15 wheels yet.

numpy must be the native wheel on both (SIMD: `X86_V3`/AVX2 on Intel,
ASIMD/NEON on ARM); `np.__config__.show()` reports which was built in.

Hybrid Intel CPUs (P-cores + E-cores) need one more step.
Windows power throttling (EcoQoS) parks unfocused processes on E-cores, which
made the live voice ~40-75% dearer on an i9-14900K and flattened the WASM
engine's lead over JS (x1.06 vs x1.97 on a Snapdragon Surface) — it read as
"the faster PC runs worse". Exempt the browser, node and the studio's pythons:

```powershell
# elevated; `powercfg /powerthrottling list` shows what is exempt
powercfg /powerthrottling disable /path "C:\Program Files\Google\Chrome\Application\chrome.exe"
powercfg /powerthrottling disable /path "C:\Program Files\nodejs\node.exe"
powercfg /powerthrottling disable /path "<repo>\.venv\Scripts\python.exe"   # and synthlab-studio.exe,
                                                     # and the base interpreter pyvenv.cfg names
```

Paths are per machine, and a recreated venv or new base Python needs
re-exempting. `tools/bench_live_voice.py` is the check: with the exemptions
an i9-14900K runs the median sound at 4.6% (WASM), under a Snapdragon
Surface's 5.2%.
Use node 24 LTS (the parity tests and the bench run on it).

The studio server does not hot-reload: after editing templates, static files,
or Python, restart it. VSCode's ruff/ty/black language servers respawn from
`.venv` within ~1s, so replacing the venv requires a kill+retry loop.

## Where things live

Open these first; paths are from the repo root (`studio/` =
`synthlab/studio/`, `docs/` there = `synthlab/studio/docs/`). Grep for the
names — line numbers go stale.

| Task | Open first | Read |
|---|---|---|
| New Patch parameter | `synthlab/synth.py` (`Patch`, `Synth.note`, `_osc_stack`) → `synthlab/params.py` → `studio/paramui.py` → `studio/render.py` `KEY_ADDED_FIELDS` → `tests/test_params.py` | the checklist at the end of `docs/engine.md` |
| New FX unit | `synthlab/effects.py` → `studio/fxrack.py` `FX_UNITS` (+ `resolve_unit` / `build_chain`) → `studio/fxlive.py` `FX_LIVE` → `studio/static/fx-worklet.js` (`eq-worklet.js` is its EQ module, `vox-worklet.js` its pitch shifter and the vocal units, `fx2-worklet.js` the modulation, colour and vocoder units and the dynamics) → `tests/test_effects_*.py`, `tests/studio/test_fx_rack.py`, `test_live_fx.py`; a parameter a Song lane may move also goes in `studio/curves.py` `FX_LANES` with a swept form in `studio/fxauto.py` (`tests/studio/test_song_fx_lanes.py` holds every entry to the scalar unit) | `docs/engine.md` (causal contract); studio `docs/live-voice.md`, "Live keys sound like the loop"; `docs/song.md`, "FX-parameter lanes" |
| New route or fragment | `studio/routes/<area>.py` (`routes/__init__.py` lists them) → `studio/templates/fragments/` → `tests/studio/test_routes.py` `EXPECTED_ROUTES` (+ `LOCK_FREE` / `RENDER_OUTSIDE_LOCK`) → `tests/studio/test_oob_refresh.py` `CASES` | studio `docs/routing-and-boundary.md` |
| A region / OOB refresh | `studio/service/regions.py` `REGIONS`, its `_w_*` watches and `_ctx_*` contexts; `studio/service/refresh.py` `StudioService.refresh` / `moved` / `suppress`; `studio/routing.py` `_settle` | studio `docs/views-and-regions.md` |
| `StudioService` (what routes call) | `studio/service/`: `__init__.py` (fields, `OWNERS`), `respond.py` (`toast`, `view_of`), `loop.py` (`render_inputs`, `render`, `_ensure`, `reset_caches`), `song.py` (sections, gain, plan, mixdown, stems), `browse.py` (`audition`, `adopt`), `contexts.py` + `songpage.py` (per-area template contexts), `page.py` (`page_context`, whole-page answers), `regions.py`, `refresh.py` | studio `docs/routing-and-boundary.md`, "`service/`" |
| Render or cache keys | `studio/render.py` `RenderInputs.key`, `RENDER_REV` (bump on any change that moves what a key renders to), `KEY_ADDED_FIELDS`, `_stem_jobs`, `section_key`; `tests/studio/test_render_cache_keys.py`; `test_render.py` and `test_song_routes.py` pin key literals | studio `docs/render-and-caches.md` |
| Session state | `studio/state/`: `__init__.py` (fields, `OWNERS`), `model.py` (`Arrangement`, `RESET_SCOPES`), `persist.py` (load / save / migrate / project), `layers.py`, `history.py` (undo rings), `song.py`, `drums.py` (bank), `presets.py` (`load_sound`), `scopes.py` (revert / reset); `studio/schema.py` | studio `docs/state.md` |
| The Song page (a playlist: a bar ruler over time-true lanes) | `studio/song_model.py`, `song_bridge.py`, `song_play.py`, `songops.py` (the playlist tools: cut at a bar, unroll, insert / delete bars, a range of cells, the energy lane) + `routes/song_arr.py`, `routes/song.py` + `routes/song_record.py` + `routes/song_long.py`, `fxauto.py` (FX-parameter lanes; the whitelist is `curves.FX_LANES`), `longaudio.py`, `service/songpage.py` (`ruler`, `blocks`, `take_bars`), `static/song.js` / `songcell.js` / `songrec.js` / `songtake.js` (a take's count-in, metronome, finger drums, ✦ Clean up: `takeclean.py` + `takecoach.py`) / `songedit.js` / `songdrag.js` (drops on a lane) / `songline.js` (zoom, the ruler, the edges), the undo ring in `studio/state/history.py`, `templates/views/song.html` + `fragments/song_*.html` | studio `docs/song.md` ("The playlist", "Drags onto a lane", "Block spans"), `docs/song-render-export.md` |
| The drum machine and the step sequencer (a hit's velocity / chance / ratchet / nudge / tune, a row's swing; variations, fills) | `studio/arrange.py` `drum_hits` (THE schedule: the render, a section's chunks, the MIDI file and the sidechain's `fxrack.kick_steps` read it; `Hit`, `hit_roll`, `clean_hits`), `studio/state/model.py` `Arrangement.drum_hits` / `.drum_row_swing` (absent when unset: `schema.ARR_OPTIONAL`, `arr_dict`), `song_model.py` `_tile_hits` / `_window_drums`, `studio/drumops.py`, `beatgen.py` (`STYLE_FEEL`, `dress`), `gen/drums.py`, `routes/drums.py` (`/fragment/drums/var`, `/fragment/gen`), `state/drums.py` (variations), `templates/drumgrid.html`, `static/steps.js`; `tests/studio/test_drum_hits.py`, `test_beats.py`, `tests/browser/test_input_matrix_drums.py` | studio `docs/drums.md` |
| The mixer (the Mix view: a strip a track, returns, the master strip, Balance) | `studio/mixer.py` (the pure model: `set_strip`, the default returns, `wet_chain`, `balance`, `context`), `routes/mixer.py` (`/song/mix/strip/{id}`, `/song/mix/return/{id}`, `/song/mix`), `render.py` `_cycle_stems` / `_return_stems` / `_wet` (pan, sends, returns on the finished stems), `song_model.py` `_with_mix` (`SectionParts.mix` / `.returns`, absent when unset), `service/song.py` `_keep_levels` / `song_stem_levels` (the plan's `levels`), `service/mixpage.py`, `service/regions.py` `_w_mixer`, `templates/views/mixer.html` + `fragments/mixer_strips.html` / `mixer_strip.html`, `static/mixer.js`; `tests/studio/test_mixer_render.py`, `test_mixer_routes.py`, `tests/browser/test_input_matrix_mixer.py` | studio `docs/song.md` ("The mixer"), `docs/song-render-export.md` ("The mixer: pan, sends, returns, levels") |
| The note grid's strum and the generators on the working loop | `studio/arrange.py` `_strummed` / `strum_beats` / `_lane_timeline` (THE note schedule: the render, a section's chunks and the MIDI file read it), `Arrangement.strum` (absent at 0: `schema.ARR_OPTIONAL`), `state/model.py` `seq_of` / `seq_changes`, `song_bridge.lane_render`; `studio/gen/loop.py`, `routes/pattern.py` `gen_notes` (`POST /fragment/gen`, `lane=notes`); `tests/studio/test_strum.py`, `test_gen_loop.py`, `test_change_posts.py` (every control that posts on `change` carries `hx-sync`), `test_empty_states.py` | studio `docs/loop-and-perform.md`, "The rule" and "Generators on the loop"; `docs/frontend.md`, "The stack" |
| Song creation (a whole song in one click) | `studio/gen/`: `chordtrack.py` (the harmony as data, `resolve`), `notes.py` / `drums.py` (the generators: pure, seeded with crc32), `styles.py` (the four styles as data: tracks, drums variants, the shape and its moves), `build.py` (`start`, `arrange`, `reroll`, `vary`, `write`, the checklist's fixes), `checklist.py` (`check`, `signature`); `routes/song_start.py` (`/song/start`, `/arrange`, `/gen`, `/meta`, `/chords`, `GET /fragment/song/check`); `service/song.py` `song_head_gain` / `song_plan(head=)`; `templates/fragments/song_start.html`, `chord_strip.html`, `song_check.html`, `empty_start.html`; `static/song.js` (`song-autoplay`); `tools/song_measure.py`; `tests/studio/test_gen_notes.py`, `test_song_start.py`, `test_styles.py`, `test_gen_undo.py`, `test_song_foundations.py`, `tests/browser/test_songcraft.py` | studio `docs/songcraft.md` |
| Meters and tempo changes (a loop or a section in 3/4, 6/8, 7/8…; a section's own tempo, a ramp; the time-true playlist) | `studio/meter.py` (meters, step choices, accents, fitting, `Clock`), `meterops.py` (a meter change on the loop, `beat_fit`), `song_model.py` "meter and tempo" (`section_clock`, `in_meter`, `fit_pattern`), `arrange.py` `bar_beats` / `TempoSong`, `routes/pattern.py` `loop_meter`, `routes/song.py` `section_time`, `service/songpage.py` `time_true`, `fragments/meter_pick.html`, `static/click.js`; `tests/studio/test_meter.py`, `tests/browser/test_meter.py` | studio `docs/song.md`, "Meter and tempo"; `docs/song-render-export.md`, "Meter and tempo in the render" |
| The jam (the Song page's launcher: scenes, pads, quantised launches, capture to sections) | `studio/static/jam.js` (its pure half `jamSchedule`; plays `GET /song/cell/{section}/{track}.wav`, no render path of its own), `templates/fragments/jam_bar.html` (included in `song_arranger.html`), `studio/jam.py` (the log → sections: `timeline`, `commit`), `routes/song_jam.py` (`POST /song/jam/commit`), `static/style.css` section 29; `tests/studio/test_jam.py`, `test_jam_schedule.py`, `tests/browser/test_input_matrix_jam.py` | studio `docs/song.md`, "The jam" |
| The live voice | `studio/static/voice-worklet.js` (the reference), `sampler-worklet.js`, `grain-worklet.js`, `livefx.js`, `studio/wasm/assembly/voice.ts`, `studio/worklet.py`, `studio/voicetables.py`; `tests/studio/test_live_parity_corpus.py` | studio `docs/live-voice.md`, `docs/keys-and-midi.md` |
| Hardware control (Stream Deck +, MIDI knobs, the pen) | `studio/static/deck.js` (the control surface: dial slots, strip, keys), `deckhid.js` (the Stream Deck + over WebHID), `deckui.js` (the virtual deck), `deckart.js` (both decks' look: the keys' glass, the strip's traces), `midilearn.js`, `pen.js`, `menu.js` (the one context menu: a thing's own actions, a value control's generated rows, and a section's own — its dials onto the deck, its controls' `data-cmd`; `frontend.md`, "A section's own menu"), `tweaktype.js` (a value typed into a read-out); every write goes through `tweak.js`'s `synthlab.tweak`; `tests/studio/test_deck_hid.py`, `tests/browser/test_deck.py`, `test_pen.py`. No route, no state: preferences are `localStorage` | studio `docs/keys-and-midi.md`, "Control surfaces", "Gestures: one owner each" and "Pen" |
| A native kernel | `crates/dsp/src/` (Rust) → `crates/dsp-py/src/lib.rs` (PyO3) → `synthlab/_native.py` + the `*_numpy` twin → `tests/test_native.py` / `test_native_voice.py` → `tools/build_wheels.py` | `crates/README.md`; `docs/engine.md`, "Native kernels" |
| Loops (one ⏺ Rec = its audio and an editable loop; the Loops list; Edit on Perform saves back; Quantise; → Song) | `studio/loops.py` (the store, `place`, the sparkline), `looplib.py` (the one list), `keytakes.py` (the take), `routes/takes.py` (`/take/keys`, `/loop/edit`, `/loop/quantise`, `/loop/song`), `state/history.py` (`loop_edit`, `mirror_loop_edit`), `templates/fragments/loop_rows.html`, `key_takes.html`, `static/keyrec.js`; `tests/studio/test_loops.py`, `test_key_takes.py`, `tests/browser/test_key_takes.py` | studio `docs/loop-and-perform.md`, "Loops"; `docs/keys-and-midi.md`, "⏺ Rec" |
| Clips and the clip library drawer | `studio/clips.py` (the store), `studio/cliplib.py` (groups, search, the Lab's stem sets, the watch's listings), `routes/clips.py`, `templates/fragments/clip_library.html` + `clip_list.html` (included from `base.html`), `service/regions.py` `_w_clip_library`, `state/persist.py` `clip_usage`; `tests/studio/test_clip_library.py`, `tests/browser/test_clips.py` | studio `docs/sampler-and-clips.md`, "The clip library" |
| The clip hub (`/view/clips`: every clip and sample; listen, tune, send, make a beat / notes / a sound, the ✦ Gemini morph) | `studio/cliphub.py` (stores, waveform, tune, `HubSession`), `clipmake.py` (the plan: `clean_plan`; the makers), `clipmorph.py` (Gemini), `service/hubpage.py`, `routes/cliphub.py`, `templates/views/clips.html` + `fragments/hub_*.html`, `static/cliphub.js`; region `hub-list` (`regions._w_hub`); `tests/studio/test_clip_hub.py`, `tests/browser/test_clip_hub.py` | studio `docs/clip-hub.md`; `docs/agent.md`, "The clip hub's morph" |
| The Sample Lab | `studio/sources.py`, `lab.py`, `labchops.py` (chops), `labclean.py` (cleaning a chop), `labmake.py`, `labjobs.py`, `routes/lab.py`, `templates/fragments/lab_panel.html`, `static/lab.js` (picking: the selection on any lane, its keys) / `labview.js` (Ctrl + wheel, the ruler and lines) / `labchop.js`; `synthlab/stems.py`, `synthlab/analysis/` (`rhythm`, `key`, `voiced`, `drumhits`) | studio `docs/sample-lab.md` |
| The sound agent | `studio/agent/` (`config`, `client`, `context`, `tools`, `service`), `routes/agent.py`, `fragments/agent_drawer.html`, `tests/studio/test_agent.py`, `tools/agent_eval.py` | studio `docs/agent.md` |
| A new control, colour, size, shortcut or command (the design system) | `studio/static/style.css` sections 1 (tokens), 15 (components), 16 (shell); `studio/static/dense.css` (the desktop density); `templates/base.html` (the rail, the top dock's status strip); `static/palette.js` (`data-cmd` / `data-keys`), `shell.js` (tooltips), `tweak.js` (the pen, sliders, knobs); `tests/studio/test_design_system.py`, `tests/browser/test_shell.py` | studio `docs/frontend.md`, "Design system" |
| A new JS island | `studio/static/<name>.js` (a `Budget: <= N code lines` header), its `<script>` in `templates/base.html` (load order matters), `tests/studio/test_island.py` (the island count and every budget) | studio `docs/frontend.md` |
| Limits, hostile input | `studio/limits.py`, `studio/security.py` (`_PATH_UPLOAD_LIMITS`, `BodySizeLimit`), `studio/schema.py` (`conform`), `studio/routing.py` (upload dependencies), `studio/files.py`; `tests/studio/test_limits.py`, `test_schema.py`, `test_security.py` | studio `docs/routing-and-boundary.md`, `docs/state.md` |

**Big files** (`studio/genome.py`, `schema.py`, `song_model.py`, `render.py`,
`synthlab/core.py`, `effects.py`; `studio/service/` is a package, imported
from `synthlab.studio.service`): Grep `^def |^class ` for the outline, then
Read around the name — don't read these whole.

## Architecture

Two halves: a pure-NumPy synthesis library (`synthlab/`), and a
server-rendered web studio (`synthlab/studio/`) built on it.

### Engine (`synthlab/`)

The engine notes (the array-safe, artifact and causal contracts, native
kernels, sampler, FX units, `RENDER_REV` / `tools/refresh_sound_meta.py` after an
engine change) and the new-Patch-parameter checklist are in
`docs/engine.md`: **read it before engine work** (its bold bullet names
are the map).

### Studio (`synthlab/studio/`)

FastAPI + Jinja fragments + vendored htmx 4.0.0. Its contracts (views,
render pipeline, state, drum machine, perform, keyboard/touch, layout
gotchas) are split by area into `synthlab/studio/docs/*.md`.
`synthlab/studio/docs/README.md` is the short index: the rules every
studio edit needs, and a "working on X → read Y" table. Open the area file
for your task **before editing `synthlab/studio/` or `tests/studio/`**.
The live voice's DSP inner loops also exist as WebAssembly + SIMD
(`synthlab/studio/wasm/`, AssemblyScript 0.28.20, pinned and locked,
node_modules git-ignored); the built `static/voice-dsp.wasm` is committed
with its sha256, and the JS DSP in `static/voice-worklet.js` stays the
reference and the fallback (see
`synthlab/studio/docs/live-voice.md`, "WebAssembly voice").
