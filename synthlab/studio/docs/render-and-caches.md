# Render pipeline and caches

`StudioService.render` (the one entry point), `render.py`'s stems and
their cache keys, how a loop is made to meet itself, the two WAV
caches and the note memo. Section and song rendering are in
`song-render-export.md`; what each lane plays in `lanes-and-layers.md`.
Index: `README.md`.

## One render entry point

**One render entry point** (`StudioService.render(view, *, patch=None,
chain=None, mix=None, post=None) -> RenderResult`): `render_inputs`
builds the inputs, `_ensure` caches under `RenderInputs.key()` and times
into `perf.record_render`. `do_render`, `preview_trigger`, the browse
auditions, Bass Lab's panels (`result.audio()`, decoded from the cached
WAV, so a reader hears exactly the bytes ▶ plays), its phone test
(`post="phone"`: `POSTS`, cached under the loop key + the post name)
and the clip bounce all go through it — none picks between
`render_loop`, `render_audio` and a cached lookup or builds inputs by
hand. The panels, the phone test and the bounce render the *requesting*
view's ▶ (`view_of`). `tests/studio/test_sound_sources.py::
test_every_listener_hears_the_views_play_render` holds all three to ▶
in every view. Don't call `render_loop`/`render_audio` from a route.

A render of something not in the session goes through it too, by
override, never by putting the session in its place and back: a browse
audition's `patch` / `chain` / `mix`, and a loop's bounce
(`/loop/bounce`) — `arrangement=` (the edited lane's notes, clock, tempo
and key, heard as the main lane; a loop view only) and `parts=` (the
parts heard instead of `view_parts`), with the loop's sound as `patch` /
`chain`. Neither override touches the session (`test_key_takes.py` holds
a bounce's session byte for byte).

## Render pipeline (`render.py`): stems and their keys

Render pipeline (`render.py`): the loop renders as **stems** (main lane,
each layer, drums), each cached in `StemCache` keyed by content hash — a
live tweak re-renders only the section that changed. Per-layer FX chains
bake into their stem; master FX apply to the sum; one normalization at the
end. **A stem key carries everything its render reads**
(`tests/studio/test_render_cache_keys.py`): a layer's includes the
main velocity lane (`_row_events` reads it when the layer has none of its
own) and the layer dict itself, which carries its own velocities, slides
and arp (see "Every synth lane plays its own sequencing", `lanes-and-layers.md`), and a stem whose FX hold a
Sidechain adds the kick hits and the drum clock (`_chain_clock`), since
`build_chain` reads them off the arrangement. Every FX chain is built
from `chain_arr(inp)`: the arrangement with `drums=[]` when drums aren't
among the parts, the same thing `RenderInputs.key()` drops — so a
sidechain does nothing without drums, in the render as in the key. **A
kick that does not sound does not duck** (`fxrack.kick_steps`, the one
list the render, the live keys' `fxlive.live_chain` and the stem key
read): hits hidden past a shrunk drum grid, a muted kick row and one
silenced by another row's solo (`state.drum_row_sounds`, which
`arrange.drum_hits` asks too) are left out — else a muted kick would
pump the synth. Mute and solo reach the stem key as the hits they
leave, so a session with neither keeps its keys. Probability is *not*
followed, except 0: the roll is seeded from the arrangement `drum_hits`
is handed, and a song section rolls its drums per chunk while its chains
run once over the whole cycle, so there is no one roll to share — a
sounding kick row ducks on every programmed step, on the grid (no swing,
no humanise). A duck with no audible kick is the `pump` unit. **Per-hit
drum data follows the same rule hit by hit** (`drums.md`, "The
sidechain"): a kick whose own chance is 0 never ducks and any other
chance is not followed; a nudged kick ducks where it lands and a
ratcheted one on every retrigger — so `kick_steps`, and the `kicks` of
a stem's `_chain_clock`, may hold step numbers with a fraction, and
are whole numbers for a pattern without per-hit
data. **The drums stem's key gains `drum_hits` and `row_swing` only
when the arrangement has them** (`Arrangement.drum_hits` /
`.drum_row_swing`, absent when unset: `schema.arr_dict`): every stem
key and loop key from before them holds. **The main stem's key gains
`strum` only when it plays** (`arrange.strum_beats`: set, and no arp),
and a layer's dict carries its own the same way
(`song_bridge.lane_render`); the loop key holds it through `arr_dict`.
**So does the note grid's timing inside a step** (`note_nudge`, free
record's loops: `arrange.nudge_of` — set, and no arp; a
layer's only on its note steps): no key of a loop without one moved, and
`RENDER_REV` did not (`tests/studio/test_render_cache_keys.py`,
"timing inside a step").
**A loop with chance is several passes** (`RenderInputs.passes`:
`song_model.loop_passes` at the end of `svc.render_inputs`
tiles a loop whose lanes hold a chance lane to `LOOP_CHANCE_PASSES`
passes, each rolled afresh — `loop-and-perform.md`, "The chance lane"):
the key gains `"passes"` only then, so no loop without chance moved and
the one-pass key such a loop had is never served again — a version
folded into the keys that hold chance only, no `RENDER_REV` bump.
`RenderResult.passes` rides the result, and `render.preview_url` puts it
on the URL (`?passes=N`) for audio.js's playheads.
**A
section's FX-parameter lanes** (`song.md`, "FX-parameter lanes") are part
of the chain's run, so a cycle stem's key gains `"fx_auto"` — the lanes
that find their unit in its chain — and `section_key` gains the section's
`fx_auto`, both **only when set**; such a stem is cached as a
`render._LaneStem` (the stem and its second ring). A unit a lane targets
carries an `id` in the chain, and so in the keys that hash the chain;
every other chain, and every key made from one, is unchanged. The
meter's analysis is `analysis.meter` (one FFT: sub share and centroid),
not the full `extract`; section results carry none.

### A loop has to meet itself

**A loop has to meet itself** (`tests/studio/test_loop_assembly.py`).
Each section's FX run on the *un-tiled* render and `_wrap_tile` folds the
wet tail onto the start; running them on the already-tiled stem cut every
reverb and delay dead at the loop point. Master FX process **two repeats
and keep the second**, so the chain is in the steady state a looping
player hears (`_kick_times` therefore emits several loops of triggers, on
the drum machine's own clock rather than the synth grid's).
`normalize_master` scales by a high percentile of the mix and then limits,
because peak normalization handed the volume of everything to its single
loudest sample; the limiter runs over two repeats too, or it ducks the
loop's opening attack and leaves a step in level at every wrap. Finished loops also cache as `RenderResult` in `RenderCache` keyed by
`RenderInputs.key()` (which must include *every* input that affects
audio — there is no free `render_key(...)` function, so an edit
reproducing the render pipeline by hand must call `RenderInputs.key()`).

### The loop key: `RENDER_REV`, and the drum lane only when it is read

**`render.RENDER_REV` is the one engine salt.** `/preview/{key}.wav` is
served `immutable` for a year, so a key must never come to mean different
audio. `RenderInputs.key()` carries `RENDER_REV`; a section key holds its
`RenderInputs` key and a post-processed loop's key (`POSTS`) is made from
the loop's, so all three move with it. **Bump it in the same change as
any edit — engine, `arrange.py`, an FX unit, `render.py` — that moves
what the same inputs render to**, and update the pinned literals
(`tests/studio/test_render.py` x3, `test_song_routes.py` x1). It replaces
a new salt per fix; the per-fix salts that exist (`layer_gain_v`,
`_cycle_fixes`) stay. Rev 1 is the release rule: a gated
note's release lasts the patch's release time. Stem keys, note keys and
the voice-take key do not carry it: they address in-memory caches and
`no-store` responses (a voice take rides `/preview` under a key that
holds the take's own content).

**A render without the drums part hashes none of the drum lane**
(`lane_fields("drum")`: grid, kit, clock, swing, humanise, velocities,
mixer, one-shot slots, the per-hit table and the rows' own swing) — and
with the drums part the last two are in the key only when set
(`schema.arr_dict`: what `RenderInputs.key()` hashes instead of
`asdict(arr)`), so a drum velocity edit does not re-key and
re-render the Synths bench and every card audition for identical audio.
What a chain still reads off the drum clock stays in, by the stem keys'
own rule: `RenderInputs._chain_clock()` is `_chain_clock` over
`chain_arr(inp)` for the master chain and, with the synth, the main
lane's and each layer's — a beat repeat's or a tape stop's period (the
same two entries: gcd(bars, drum_bars),
whether or not the drums play) and a sidechain's kick list (empty
without drums) and grid. Absent from the key when no chain reads it
(`tests/studio/test_render_cache_keys.py`).

## `arrange.py`

`arrange.py` — piano-roll grid → `Song`. Plain per-row scheduling normally;
arps and 303 slides switch a lane (main or layer) to a time-ordered
timeline scheduler (`_lane_timeline`).

### Meter and ramp in the keys

`Arrangement.meter` and `.ramp` are ABSENT from `arr_dict` at 4/4 and no
ramp (`schema.ARR_NEUTRAL`, `arr_unset`), so no loop key, section key or
stem key moved; a stem key gains `meter` / `ramp` only when set
(`render._bar_clock`, in `_timing`, `_drum_timing`, the clip stems and
`_chain_clock`). `RENDER_REV` did not move: no key's audio did.

## Two WAV caches

**Two WAV caches** (`render.py`, `tests/studio/test_note_cache.py`):
`svc.cache` (`RenderCache`) holds **only** what `/preview/{key}.wav`
serves — loops, bounces, tile previews, voice takes, song sections — and
`svc.notes` (`NoteCache`) holds the one-shots that their own routes serve:
`/note/{pitch}.wav`, `/breed/note/{tid}.wav`, drum hits. Two caches,
because in one shared cache a burst of keys would evict the loop the
browser was about to fetch and `/preview` would 404. Both are `lru.LRU`s bounded in
**bytes as well as entries** (`max_bytes`/`sizeof`; RenderCache 256 /
192 MB, NoteCache 256 / 64 MB): WAVs range from a drum hit to a 72 s (the loop cap; a few seconds as a rule)
section, so a count bounds neither memory nor how many fit. A new route
that serves its own WAV puts it in `svc.notes`; anything announced via
`preview-ready` goes in `svc.cache`. `/debug/stats` reports both.

## Note memo

**Note memo** (`arrange.NoteMemo`, `render.NOTE_MEMO`,
`tests/studio/test_note_memo.py`): the loop/section stems' notes by
(patch content, `_engine_sig()`, arguments), read-only arrays, 96 MB. A
one-cell edit re-renders its stem but only the new note (a pluck loop's
fresh render 120-150 ms -> 12-16 ms). Opt-in via `build_song(memo=)`;
the engine sig holds the ids of `Synth.note`, `core.noise`,
`sequencer.render_each` and the native switches, so a test's spy or
swap misses rather than being served the real engine's notes.
`/debug/stats` reports `note_memo`.
