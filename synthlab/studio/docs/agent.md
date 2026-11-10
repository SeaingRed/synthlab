# Sound agent (`studio/agent/`, `routes/agent.py`)

The optional Gemini assistant on Synths: setup, the secret, its tools,
a turn, the drawer, the log / budget, pricing, the prefix cache and
the eval. Index: `README.md`.

A Gemini-driven assistant that edits the Synths sound through tools.
Prototype; optional.

## Setup

**Setup**: `uv sync --all-extras` (the `agent` extra: `google-genai`;
on Windows-on-ARM it pins `cryptography==46.0.3`, the last release with a
win_arm64 wheel — pip-audit flags it, see pyproject), then put
`GEMINI_API_KEY=...` in the checkout's `.env` (git-ignored; resolved
from the package, `<repo>/.env`, never the cwd — a studio started
elsewhere still finds it, a checkout without one has none). Environment
variables win over the file. Optional: `GEMINI_MODEL` (default
`gemini-flash-latest`), `SYNTHLAB_AGENT_TIMEOUT_S` (30, one call),
`SYNTHLAB_AGENT_TURN_DEADLINE_S` (55, a whole turn — under htmx's
timeout, which the drawer lifts to 120 s), `SYNTHLAB_AGENT_TOKEN_BUDGET`
(250k per budget), `SYNTHLAB_AGENT_TURN_BUDGET` (40), `SYNTHLAB_AGENT_AUDIO`
(on), `SYNTHLAB_AGENT_AUDIO_STEREO` (on: the clip is stereo when the
sound is), `SYNTHLAB_AGENT_CACHE` (**off**: the prefix cache, below),
`SYNTHLAB_AGENT_CACHE_TTL_S` (1800), `SYNTHLAB_AGENT_CACHE_MIN_TOKENS`
(1024, unverified), `SYNTHLAB_AGENT_PRICE_IN` / `_OUT` ($ per million
tokens, both or neither: the money estimate's price for every model,
overriding `config.PRICES`; `_PRICE_CACHED` adds a cached-input rate to
that override). A key added after start is picked up on the next request. Without
the SDK or a key the drawer says why and every turn is a toast.

## Secret

**Secret**: the key lives only in `AgentConfig` (`repr=False`) and the
SDK client. Everything that came off the network goes through
`config.scrub` before a toast or a log line; no template, response,
state.json, `agent_log.json` or /debug/stats sees it (`test_agent.py`
checks each, with the key planted in an SDK exception and in a model
reply; the log's every string is scrubbed before it is written). `tests/conftest.py` (autouse) strips
the key variables, points `ENV_FILE` at nothing and makes the real client
unbuildable, so no test can reach Gemini. Free-tier requests may be used
by Google; only the sound's settings and a 2 s clip are sent.

## Tools

**Tools** (`tools.py`, generated, nothing per-parameter by hand):
`set_params` (every editor control but the performance controls
`Param.nogene`, as a **catalogue** in the `params` description — one
line per field, `cutoff: 60..16000 Hz`: `Param.cap`, the unit, the
slider range as "typical", a categorical's choices, fields with the same
choices on one line (`tools.param_groups`): 3,080 characters of the
9,891 every call sends, where a JSON-schema property per field would be
8,063 of 14,874. So the model's arguments are not type-checked by the
API, only by `coerce_params` + `params.clamp`),
`add_layer` (role recipe via
`genome.recipe`, following, not selected — the panels stay on the edited
sound; `StudioState.add_layer(select=False)`), `set_layer`,
`set_fx_chain` (`FX_UNITS` catalogue — a choice parameter, the drive's
`mode`, listed with its indices, `mode 0=soft 1=hard 2=fuzz 3=tube`;
`clean_chain`; width capped at 1.5 for the agent: past that the mix goes
out of phase), `add_mod_route`,
`draw_curve` (rise/fall/dip/swell/points/clear), `set_macro`,
`choose_phrase`. Bad arguments come back to the model as errors/notes,
never raise.

## A turn

**A turn** (`service.AgentService`): `/fragment/agent/turn` is
`@render_outside_lock` — `begin` (under the lock: render + measure + the
compact description, `context.describe`) and the model calls run with the
lock released; each round of tool calls re-takes it to apply,
`svc.render("editor")` and `context.measure` (centroid/brightness, sub,
loudness, width, movement, flatness, crest, attack). Loudness is the
loudest 50 ms *before* the loop's normalisation: the ▶ render is brought
to one level (`render.normalize_master`), so it measured -0.8 dB for a
sound made 6 dB quieter; `render_loop` keeps the gain it applied
(`RenderResult.norm_gain_db`, a float, no second copy of the audio) and
`context.measure` subtracts it — exact up to what the master limiter took
off the peaks. The rest read the render as heard. The numbers and the
self-check (`context.expectations`: "darker" -> centroid down, ...) go
back to the model; at most 3 tool rounds, then a no-tools summary call.
The turn is ONE undo step (`push_undo_step`), "Before last load" gets the
patch and the stack it replaced; `/fragment/agent/undo` restores the whole
stack snapshot, `/ab` stores before/after in A/B, `/apply` moves the
result onto a layer (new or 1-3) and restores the edited one. The
changes list is a diff of state (`context.diff`), never the model's own
account. Every answer is the full page (morph) or a toast; an error after
some rounds keeps their changes and toasts via `HX-Trigger`.

## UI

**UI**: `fragments/agent_drawer.html`, a `popover="manual"` (top layer:
no cockpit grid area; the body morph keeps it open), opened by the sound
bar's "✦ Agent". No script: htmx POSTs, `hx-indicator` gives the busy
state — but for what a manual popover does not get from the browser
(bench.js): opening it puts the focus in its field (its ✕ when the field
is off), and Esc with the focus inside closes it and gives the focus
back to "✦ Agent". With no key the field says so in its placeholder. Log per turn: request, reply, changes, before→after numbers, check
chips, tokens and "≈ $"; budget counter (tokens, $, turns) in the header
with "New budget" (`/fragment/agent/budget`: zeroes the budget, keeps the
log).

## Log and budget persist

**Log and budget persist** per data dir in `agent_log.json`
(`schema.AGENT_LOG`, conformed on load — a hostile file is an empty log;
the newest `limits.MAX_AGENT_LOG_TURNS` (50) turns; tempfile +
`os.replace`; display fields only). A turn's stack snapshots stay
runtime, so after a restart the logged turns are history, not undoable.
Because the budget survives restarts, "New budget" is how a spent one
is renewed.

## Money estimate

**Money estimate**: each reply is priced from its token split
(`client.usage_split`: prompt + tool-use prompt in, of which AUDIO at the
model's audio rate; candidates + thoughts out) at `config.PRICES` for the
model that *answered* (`model_version` — `gemini-flash-latest` is an
alias Google hot-swaps, so it is never priced by name). A model not in
the table, or a reply without a split, is unpriced: no "$" rather than a
wrong one; the budget total shows "+" when some turns were unpriced. The
table is from ai.google.dev/gemini-api/docs/pricing (updated 2026-09-24),
including the 3.7/3.8 Flash price rise on 2027-01-01 (`Price.until`).
Cached input (`cached_content_token_count`, already inside the prompt
count: ours or Gemini's implicit cache) is priced at `Price.cached_in`;
no row of `PRICES` has one, so it is charged at the full input rate — an
over-estimate, on purpose. Creating a cache and its hourly storage are in
neither the estimate nor the token budget. A turn's cached tokens show in
the drawer ("(2,900 cached)", `Turn.tokens_cached`).

## Cost

**Cost**: what one call sends, offline (`tools/agent_eval.py --dry-run`; tokens =
characters / 4, an estimate — the SDK's local tokenizer needs
sentencepiece and a download): system 1.8k chars + tools 10.5k
+ ~1k of context = ~3.3k tokens; each later call of
the turn adds ~170 for the last round's results. A turn is 2-4 calls.
The FX units and the Patch fields are catalogue strings, not schemas.

## Prefix cache

**Prefix cache** (`client.PrefixCaches`, `SYNTHLAB_AGENT_CACHE=1`; OFF by
default because it has never met the live API): the system instruction
and the tool declarations are the same on every call, so they go into an
explicit context cache — `client.caches.create(model=, config=
CreateCachedContentConfig(system_instruction, tools, tool_config, ttl,
display_name))` once per (key digest, model, prefix hash), shared by
every client of the process — and a call names it
(`GenerateContentConfig(cached_content=name)`) and carries only the
history (~260 tokens instead of ~3.3k). The API refuses a request that
names a cache *and* carries system/tools/tool_config, so the cache holds
mode AUTO and the rare no-tools summary call (mode NONE, after the last
round) is sent uncached. A cache past its TTL (less 30 s; or the
service's own `expire_time` if sooner) is made again; one the service no
longer has (403/404 naming the cache) is dropped and made again on the
next call. **It fails safe**: any exception from `caches.create` or from
a cached call sends the same request uncached, in what is left of the
call's timeout. **Only an answer about the cache or the request marks
the prefix refused** for `CACHE_RETRY_S` (10 min) — `REFUSAL_CODES`: 400,
403, 404, and a cache that came back without a name — so an unsupported
model or a too-small prefix costs one failed call, not one per request.
Anything else (429, 408, 5xx, a dropped connection, a timeout) is
**transient**: that one call goes uncached and the registry is left as
it was, so the next call uses the cache (or makes it) — one 429 does
not cost ten minutes of full-price calls. One scrubbed warning per kind
(`agent cache refused` / `expired` / `unavailable`). A prefix under
`cache_min_tokens` (estimated) is not offered. Don't put anything that
varies per turn into `context.SYSTEM` or a declaration: it would make a
cache per turn.

## The clip

**The clip** (`context.audio_clip`): ~2 s from the first sound, 16 kHz,
16-bit; **stereo** when the clip has an image (side more than -40 dB
under mid, `MONO_SIDE_DB`), mono when L = R — 128 KB against 64 KB, the
same ~64 tokens (audio is billed by length). 24 kHz was left out (192 KB
for a band the service is said to resample to 16 kHz). The service is
also said to fold channels to one, so the image reaches the model for
certain only as numbers: `context.stereo_text` adds L/R correlation,
side level vs mid and the L/R balance to the measured line
(`side_db`, `balance_db` in `FEATURE_KEYS`, logged with the turn).

## A take's clean-up

**A take's clean-up** (`studio/takecoach.py`, the Song page's ✦ Clean up:
`song.md`, "Finger drums and ✦ Clean up") uses the same key, model and
timeout (`load_config`) and the same scrub, but not the agent: one
`generate_content` call with a JSON schema (`takecoach.SCHEMA`: feedback
lines and at most eight edits), text only — the song's tempo, key, style
and section, the other tracks there, the cleaned pattern as a text grid
and the take's timing figures. It is not logged in `agent_log.json` and
not counted against the agent's budget. `tests/conftest.py` replaces
`takecoach.client_for`, so no test reaches Gemini.

## The clip hub's morph

**The clip hub's morph** (`studio/clipmorph.py`; the hub is
`clip-hub.md`, "Make from it"): "✦ Make it from my words" on a clip's
bench. Same key, model, timeout and scrub as the agent (`load_config`,
`config.scrub`), and the same SDK client as a take's clean-up
(`takecoach.client_for`, read at call time — so the suite's guard in
`tests/conftest.py` covers it, and `takecoach.load_config` is the one
patch point for a test's key). Not the agent: one `generate_content`
call, not logged in `agent_log.json`, not counted against its budget; the
tokens it cost are shown with the result.

**It listens**: the contents are the brief (text) and the clip as an
audio Part (`clipmorph.audio_wav`: at most `MORPH_SECONDS` = 8 s from the
first sound, 16 kHz mono 16-bit, ~256 KB — the agent sends 2 s of its
render; a clip's groove needs bars). **The page says so** beside the
button ("sends the first 8 s of this clip's audio and your words to
Google Gemini"), and without a key the button is disabled and says what
it needs (`clipmorph.available`, `takecoach.available`: the SDK, the
key); the pickers work without it.

**The brief** (`clipmorph.brief`): the player's words (cut to
`MAX_DESCRIBE` = 300 characters), the clip (name, kind, length, the grid
it is read on, the measured key), the loop (key, tempo, beat style), what
the player locked (`clipmake.locked`: kept exactly), and the catalogue —
the kits, the beat styles, the note sources, every Library / recipe /
factory sound by family, the FX chain presets. No session state beyond
that, no key.

**The answer** is JSON against `clipmorph.schema()` (`response_json_
schema`; `response_mime_type` application/json; thinking low; temperature
0.4, 0.9 with a seed for "✦ ask again"):

```
{target: beat|notes|sound,            # enums are the studio's own names
 kit: own|keep|<DRUM_KITS>, style: clip|<beatgen.STYLES>,
 density: same|busier|sparser, amount: number, swing: number,
 feel: as is|half-time|double-time, fill: none|half|bar, loop_tempo: bool,
 notes_from: melody|rhythm|bass_root|bass_rolling|chords|pad|arp|lead,
 sound: {source: keep|clip|library|recipes|factory, name: string},
 sound_mode: whole|chops|voice, transpose: integer,
 snap: loop|clip|none, fx: keep|none|<CHAIN_PRESETS>,
 summary: string, why: [string] (<= 4)}         # required: target, summary
```

**Nothing in it is trusted**: the enums are a request, not a guarantee —
`clipmake.clean_plan` holds every name to the studio's lists (a sound's
name to `sound_catalogue`), clamps every number, cuts the text, and says
which names it refused; the player's locked picks are laid over it
(`clipmorph.merge`). The summary and reasons are scrubbed of the key.
A failed call or an answer that is not a JSON object is an `AgentError`,
scrubbed, and a toast. The plan is then made and previewed like the
pickers' own, and written only on Apply.

## The loop coach

**The loop coach** (Perform's ✦ Coach: `studio/loopcoach.py`
the rules and the session, `studio/loopchat.py` the talk,
`routes/coach.py`; the panel is `loop-and-perform.md`, "✦ Coach"). Its
checks, fixes and options need no key and no network. Its chat is one
`generate_content` call a message, the same key, model, timeout and
scrub as a take's clean-up and the morph (`takecoach.load_config` and
`takecoach.client_for`, read at call time — `tests/conftest.py`'s guard
covers it; a test patches `takecoach.load_config` / `client_for` /
`sdk_available`), thinking low, temperature 0.4, JSON against
`loopchat.schema()`:

```
{reply: [string] (<= 4), heard: string (a spoken message: what it heard),
 fixes: [finding id] (<= 8: the coach's own fixes, made as they stand),
 edits: [{lane: "1".."4" | "drums", op: add|remove|move|velocity,
          pitch, voice, step, length, to_step, to_pitch, velocity, why}]
        (<= 16)}                                 # required: reply, edits
```

**What is sent** (`loopchat.info` + `brief`, text only): the tempo, the
meter, the key, the chord lane's chords with their steps, each lane that
plays notes of its own with its sound's name and its notes as a grid (a
row a pitch: a velocity digit where a note starts, `=` while it is held),
the beat as a grid, what the coach's rules found (id and title), the last
six lines of the thread and the message. No audio, unless the message was
spoken.

**Nothing in it is trusted**: `loopchat.apply` makes each edit on a copy
of the loop only when it fits — a lane that plays its own notes, a pitch
in the key (or a tone of the chord there), a step inside the lane, an add
where its pitch is free, a remove / move / velocity on a note that is
there, a drum voice the kit has — and counts the rest as skipped (the
thread says how many). The reply and every `why` are scrubbed of the key.
What fits becomes a **proposal**, never the loop: the player hears it and
accepts or discards it. A call that fails, or an answer that is not the
JSON asked for, is a scrubbed line in the thread.

**🎙 hold to talk** is offered because the configured model takes audio
cheaply and simply: the agent already sends it a clip, and a spoken
message is billed by its length (Flash: ~32 tokens a second, at the
model's audio rate in `config.PRICES` where it has one) — a 5 s phrase is
a fraction of a cent. coach.js records while the button is held (voice.js's
capture; at most 10 s), resamples to 16 kHz mono and posts a 16-bit WAV
(`POST /fragment/coach/talk`, its body capped by
`limits.MAX_COACH_TALK_BYTES`); the server sends its first 8 s from the
first sound (`clipmorph.audio_wav`) with the same text a typed message
is sent with, and the thread shows what Gemini says it heard. Not offered
without a key, or with `SYNTHLAB_AGENT_AUDIO=0`.

**The budget and the log**: the coach is not the agent. Its calls are not
logged in `agent_log.json` and not counted against the agent's budget —
the coach edits nothing without an Accept and has no tool loop, so a
message is one call of ~1.5-2k tokens; each reply shows its tokens and
"≈ $" in the thread (priced as the agent's are: by the model that
answered, `client.reply_cost`). Without a key the thread says how to add
`GEMINI_API_KEY`; the checks, the fixes and the options above it work
all the same. Measured (one live turn on a typical loop,
`gemini-flash-latest`): 1,665 tokens, ≈ $0.0036.

## Eval

**Eval**: `uv run python tools/agent_eval.py [--only 1,3] [--list]
[--cache]` — 30 requests scored by measured features / state checks,
real API only, never collected by pytest; it prints each case's cached
tokens and, with `--cache`, how many caches were made and refused.
`--dry-run` needs no key and no network: the real route, `GeminiClient`
and SDK build every request against a scripted httpx transport
(`GeminiClient(config, transport=...)`), without and with the cache, and
it prints each call's size (`test_agent_cache.py` runs it).
