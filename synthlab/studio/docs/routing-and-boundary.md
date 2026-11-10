# Routing, concurrency and the boundary

How the app is put together (`app.py`, `routes/*.py`, the `service/`
package, `routing.py`), the one concurrency model every handler runs under (the
state lock, `@lock_free`, `@render_outside_lock`), and the security
boundary (`security.py`: Host / Origin checks, share token, CSP, body
caps). Part of the studio notes: index in `README.md`.

## Router layout

**Router layout** (no single `create_app()` closure holding every
route): `app.py` wires
the app together (security boundary, gzip/timing middleware, static
files — `RevvedStaticFiles` marks a `?v=<the file's own hash>` URL
`immutable` (`asset()`, `static_hashes`: frontend.md, "Static files: a
hash each"), anything else revalidates — the Jinja environment, the `StudioService` singleton, the
`RenderError` -> toast exception handler) and mounts one router per area
from `routes/*.py` (`patch.py`, `drums.py`, `perform.py`, `presets.py`,
`breed.py`, `library.py`, `voice.py`, `clips.py`, `audio.py`, `fx.py`,
`macros.py`, `views.py`, `misc.py`, `sampler.py`, `song.py`,
`export.py`, `events.py`, `lab.py` and more — `routes/__init__.py`
is the list). `service/`'s `StudioService` (below)
(`app.state.svc`) holds the session state, the render/stem caches, perf
counters and the shared helpers every route calls — `toast()`,
`render()` (the one render entry point, `render-and-caches.md`), `page_context()`,
`audition()` / `adopt()` (browse, `sounds-and-breeding.md`). `views.py`'s `VIEWS` table is the one registry of
view names, the parts each view renders, which views are benches, and
the reset scopes each view owns (one table, not four registries kept in
sync by hand). `routing.py` holds the concurrency
plumbing below. `server.py` itself is a thin compatibility shim: it
re-exports `create_app`, the CLI (`main`/`_parse_args`) and the form/
upload types so `synthlab.studio.server:create_app` and the
`synthlab-studio` entry point keep working unchanged. Fragment endpoints
return partial HTML; audio changes are signaled via an `HX-Trigger:
preview-ready` header carrying the WAV URL, which `static/audio.js` picks up
and crossfades.

## `service/`: the service package

`service/` — `StudioService`, a package. It is the one object on
`app.state.svc` and `synthlab.studio.service` the one import for every
name; the
class is a constructor (`service/__init__.py`, which declares every
field) plus one mixin per concern:
`respond.py` (`Respond`: `toast`, `view_of`, `asked`, `view_parts`,
`fx_scopes_for`), `loop.py` (`LoopRender`: `render_inputs`, `render`,
`_ensure`, `POSTS`, `do_render`, `preview_trigger`, `reset_caches`),
`song.py` (`SongRender`: `song_snapshot`, the idents, `_master_audio`,
`section_result`, `song_gain`, `song_plan`, `_song_raw`, `song_mixdown`,
`song_stems`), `browse.py` (`Browse`: `audition`, `adopt`),
`contexts.py` (`Contexts`: the per-area template contexts, `space_store`,
and the helpers routes import — `group_specs`, `dxy_spec`, `xy_norm`,
`xy_value`), `songpage.py` (`SongPage`: `song_context`, the drawer, lane
rows, `_thumb`, `calibration`), `mixpage.py` (`MixPage`:
`mixer_context`, the Mix view's strips and returns — no level: the page
lights its meters from the plan), `page.py` (`Page`: the lean-context sets,
`page_context`, `full_view_page`, `full_page_with_preview`, `land_on`
(a whole page on another view, its URL and a toast: a keys take or a loop
→ Perform, a sample → Synths), `bank_response`, `drum_panels_fragment`, `clip_library_context`,
`clip_library_html`, `clips_fragment`, `theme_of`),
`refresh.py` (`Refresh`: `oob_regions`, `watch_page` — each `is_dirty`
and the clip store's listing read once a pass, `dirty_memo` and
`cliplib.sig_memo` — `moved`,
`announce`, `suppress`, `refresh`) — and `regions.py`, which is no
mixin: the `REGIONS` table, its `_w_*` watches and `_ctx_*` lean
contexts, each handed the service as `svc`.

**Mixins, not composed objects, on purpose**: the units call each other
through `self` all the way down (`refresh` -> `oob_regions` ->
`page_context` -> `song_context`; `section_result` -> `_ensure`), and
tests replace methods and fields on the instance (`svc._song_raw`,
`svc._song_idents`, `svc.song_mixdown`; `svc.cache`, `svc.stems`,
`svc.song_memo`) — a composed unit would need a forwarder per name, and
a replaced forwarder, or a cache swapped on the service, would not be
the one its neighbours use. What keeps the units apart instead is
checked by `tests/studio/test_service_split.py`: `OWNERS`
(`service/__init__.py`) lists, per field, the modules allowed to touch
it — `cache`, `perf` and `notes` are `loop`'s alone (every render, a
song section's included, is filed and timed by `_ensure`),
`_suppressed` `refresh`'s, `_space` / `lab` / `data_dir` `contexts`'s,
`agent` `page`'s; `stems` is `loop`'s and `song`'s, `song_levels` /
`song_memo` `song`'s (and `reset_caches`'s), `events` `song`'s and
`refresh`'s; `templates` and `state` are read by nearly every unit —
and each mixin declares, as bare annotations at the top of its class,
exactly the fields its code reaches. **A new field goes in the
constructor, in `OWNERS`, and in the annotations of each unit that uses
it; a new method goes in a unit, never in `__init__.py`.**
`render_loop`, `section_key`, `section_master_audio` and
`library_by_family` are read through the package at call time
(`loop.py`, `song.py`, `page.py`), because tests patch them as
`synthlab.studio.service.<name>`; the logger is still
`synthlab.studio`. The same test pins the importable surface and the
region table against the one-file module. The lock discipline did not
move: `loop.py` reads `self.state` and runs under the studio lock;
`song.py` takes the song as an argument (`song_snapshot` is its one
read of the live song) and is what the `@render_outside_lock` routes
run with the lock released (below).

## `mount()` appends routes flat

**`mount()` appends routes flat** (`routing.py`): not
`app.include_router`, which (since FastAPI 0.14x) appends one opaque
`_IncludedRouter` entry and stops `app.routes` from listing the
individual `APIRoute`s that the lock-policy pin and
`test_every_route_runs_off_the_event_loop` introspect. One consequence:
`app.dependency_overrides` — which FastAPI resolves by walking
`app.routes`' own dependency graphs — works the same as with
`include_router` here, but a router mounted through `include_router`
elsewhere in the app would not compose with this one the way nested
routers normally do. Tests override a dependency by monkeypatching the
`StudioService`/module attribute the handler reads, not
`dependency_overrides`.

## One concurrency model

**One concurrency model** (`tests/studio/test_concurrency.py`): every
route handler is a plain `def` (threadpool, never the event loop — an
`async def` render stalled every request, `/health` included), and
`StudioRoute` runs each under `StudioState.lock` (an RLock) so a
request's mutation and its render are one step. Forms and uploads are
parsed by async dependencies *before* the handler (`StrForm`,
`RawForm`, `TakeUpload`/`ProjectUpload`/`ClipUpload`), so upload bytes
are read before the lock is taken. `StrForm` / `RawForm` also leave the
parsed form on `request.state.form`: a region's context is handed the
request, not the handler's form, and the clip library redraws with what
its own controls posted (`StudioService.asked`). `StudioRoute` refuses an `async def`
handler unless it is `@lock_free`; only `/health`, `/preview`,
`/debug/stats` and `/theme` are. `save()` serializes
(`json.dumps(self._payload())`) AND writes the tempfile + `os.replace`
all under `self.lock`, not just the snapshot: the debounce timer used
to release the lock before writing, so a request-path save with newer
data could finish first and the timer's stale `os.replace` would land
last and clobber it (also a Windows `PermissionError` risk from two
concurrent `os.replace` calls onto the same target). Holding the lock
for the whole write makes the two saves mutually exclusive end to end,
with no deadlock risk because `_save_lock` (which only guards
cancelling the debounce timer) is never held while waiting on
`self.lock`. The temp file is fsynced before the rename, and a write
that fails stays pending (`_unsaved`): the debounce timer's own save
retries it (`_timed_save`: nothing above that thread hears an
exception) and `flush()` writes it at shutdown
(`tests/studio/test_state_save.py`).
`RenderCache`/`StemCache` are `lru.LRU`s with their own
lock. A new route needs nothing: take `form: StrForm` and write it as a
plain `def`.

### Long renders run outside the lock

**Long renders run outside the lock** (`@render_outside_lock`,
`routing.py`): `/export/song.wav`, `/export/stems.zip`,
`/song/plan.json`, `/song/section/{id}.wav`,
`/fragment/breed/generation` (under the lock a 1.6 ms request would wait
3.85 s behind a stems export) and `/note/{pitch}.wav` (a live key: snapshots a copy of
the patch at the bpm, renders at most `routes/audio.NOTE_RENDERS` = 4 at
once, and coalesces two requests for the same key into one render; its
`X-Patch-Key` names the patch it is, which `/patch.json`'s `note_key`
says is current), and `/voice/render` (the vocoder: seconds for a long
take at 32 bands; it renders the Synths loop under the lock when that is
the carrier, then vocodes with it released). The handler still runs under the lock, but only to
snapshot (`svc.song_snapshot()`: a deep copy of the song and the master
chain; breed: the starred ids and a copy of the patch) and returns
`OutsideLock(run)`; the wrapper releases the lock, then calls `run()`.
`run()` reads state only through its snapshot, writes only the
self-locking caches (keys derived from the same snapshot, so an edit
landing mid-render can't file audio under the wrong key), and re-takes
`s.lock` itself to write session state (breed's board). The set is
pinned by `test_routes.py::RENDER_OUTSIDE_LOCK`; adding a route to it is
a deliberate act. Known residue: a clip re-recorded under the *same*
name mid-export is read from disk by the render.

## Boundary (`security.py`)

**Boundary** (`security.py`): the Host allowlist (`LOCAL_HOSTS`,
including `::1`; loopback names only — the suite adds TestClient's
`testserver` itself in `tests/conftest.py`), the Origin/`Sec-Fetch-Site`
check on every non-GET
(an `Origin: null` — sandboxed iframes, `file://`, some privacy modes —
is treated as a mismatch, never exempted), the same check on reads
(`_foreign_read`: a GET/HEAD with `Sec-Fetch-Site` cross-site or
same-site is a 403 unless it is a top-level navigation, and that
navigation is redirected to `/` from any other path — some GETs render
or change state, `/view/breed` on an empty board among them; no header
and `none` pass), the `-Share` token cookie
(`samesite=lax`, not `strict`, or a cross-site nav into a shared link
drops it; any non-loopback `--host` gets a token too, `--share` or not:
`server._share_token`; named per port, `synthlab_token_<port>` —
`security.token_cookie` — because a browser keeps cookies by
host alone and two shared studios on one machine would take each other's), the
CSP (`script-src 'self' 'wasm-unsafe-eval'` — the one
addition lets livefx.js compile the voice's WebAssembly module and
nothing else; never `'unsafe-eval'`, so still no eval/`new Function`/
`js:` attributes anywhere; and `base-uri 'none'`,
`form-action 'self'`, `object-src 'none'`: no `<base>`, no form posting
off the studio, no plugin — `tests/studio/test_security.py` pins the
whole policy), `X-Frame-Options` (`SAMEORIGIN`, with `frame-ancestors
'self'`: no other origin frames the studio, its own Song page frames
Synths — `song.md`, "The sound dock"), and
the pure-ASGI `BodySizeLimit` (per-path caps from `limits.py`, checked
against a declared `Content-Length` before `receive()` is ever called,
and against the running byte count otherwise) all live here, installed
once as middleware in `app.py`. A new route needs nothing extra to be
covered by any of this. A new *cross-origin* integration — a browser
extension, a companion app on another port — needs its own deliberate
addition to the Origin/Host allowlists; nothing here infers one.

**A name in `hx-vals` goes through `tojson`**: `hx-vals='{{
{"name": p.name} | tojson }}'`, never `'{"name": "{{ p.name }}"}'` — a
preset, a clip or a panel preset named with a `"` or a `\` makes the
second form invalid JSON (htmx then sends nothing, or the wrong name).
Ids the studio makes and fixed words may stay interpolated.
`tests/studio/test_frontend_contracts.py` renders one with quotes in it.
