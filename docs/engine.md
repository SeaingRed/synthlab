# Engine notes

The engine's contracts (`synthlab/`, the native kernels in `crates/`, the
render-side studio files) and the new-Patch-parameter checklist at the end.
Setup and commands are in `CONTRIBUTING.md`; the studio's notes start at
`synthlab/studio/docs/README.md`.

## Engine (`synthlab/`)

- `core.py` — oscillators (PolyBLEP), envelopes, LFO, and the **swept biquad**:
  time-varying filters recompute coefficients per 64-sample block with state
  carried across blocks (coefficients come from one vectorized pass,
  `_biquad_blocks`). Accepts mono `(n,)` or stereo `(n, 2)` input.
- `synth.py` — `Patch` is a flat dataclass that is simultaneously the sound,
  the JSON serialization, and the **genome** for breeding. `Synth.note()`
  renders one note. A 12-slot mod matrix (`_apply_mod_matrix`) resolves
  source×target×amount into per-sample arrays before rendering.
- `params.py` — **the parameter registry**: one `Param` per Patch field
  (`kind` lin/log/int/cat/meta, `gene` range or choices — also the slider's
  range, `cap` for `clamp()`, `curve`, `mod` span, `live` rule), plus
  `live(p)`, `clamp(p)`, `norm`/`denorm` (the one log taper), `lerp` (A/B
  morph) and `slot_active` (the engine's "does this route play" rule). Its
  UI half is `studio/paramui.py`: one `Ui` per field (label, hint, unit,
  panel, step, subhead, live-voice class) from which `PARAM_GROUPS`,
  `spec_of`, `SHAPE_DEPS`, the mod menus' labels and `worklet_blockers()`
  are built. **`PARAMS` order is the breeder's RNG draw order**
  (`tests/test_params.py` pins it): append, never reorder.
- **Array-safe params contract**: most numeric Patch fields accept scalars OR
  per-sample arrays (drawn automation). Gates use `np.any(np.asarray(x) > 0)`,
  never bare truth tests. A `Param` with a `curve` range is what the UI may
  automate (`studio/curves.py:CURVABLE` is the view);
  `tests/test_synth.py::test_array_automation_on_every_curvable_param`
  enforces the contract.
- Oscillator shapes live in `core.WAVEFORMS` plus special cases in
  `core.osc()`: `wavetable` (below), `noise`, `fm` (2-op, reads `fm_ratio/fm_index/fm_decay`),
  `fm4` (4-op: below) and `pluck` (block-vectorised Karplus-Strong, reads
  `pluck_damping/pluck_bright`; **pitch is fixed per note** — glide, pitch
  env and vibrato don't bend it). `Synth._osc_stack` passes those Patch
  fields to every shape as `shape_kw`; unknown kwargs are ignored. Every shape
  is in `studio/worklet.py`'s whitelist (the one classification of every
  `Patch` field the AudioWorklet can play — see
  `synthlab/studio/docs/live-voice.md`), measured against the engine: pluck, wavetable and the
  stepped LFO sample for sample (their seeded streams and tables ride
  `/wavetables.bin`), noise statistically (1/3-octave bands).
- **Native kernels** (`crates/`, imported through `synthlab/_native.py`):
  the per-sample loops NumPy can only fake, in Rust — the whole unison
  oscillator stack (`Synth._native_stack`: every shape but pluck and
  sampler), the post chain after it (`Synth._native_post`: DC block,
  filter, amp envelope, drive in one call — a list of stages decided in
  Python by `core._biquad_plan`/`_ladder_stages`/`_clip_stages`, the same
  kernels in the same order, so bit-identical to calling them one by
  one), the swept SVF, oscillators (wavetable included), envelopes,
  `dc_block`, `soft_clip`, scipy's short `lfilter`s, the delays and
  chorus read, the booth's loops (`read_back`, `tape_echo`, `rotor`,
  `sines`: the doubler, rotary, tape echo and fx2 tape) and `core.scan`
  — one pass over a finished buffer for finite-ness, peak, sum of
  squares and `np.percentile`'s 99.5th of the frames' peaks, bit for bit;
  the render's NaN checks and every level the song reads go through it
  (measured: four cold starter exports 14.8 -> 9.9 s, of which scan
  ~3 s, the booth ~1 s). Each public function asks `kernel(name)` and
  falls back to its `*_numpy` twin (the stack to its per-voice loop),
  which stays the reference. A new kernel needs its twin and a parity case
  in `tests/test_native.py` / `test_native_voice.py`; those also fail when
  the extension isn't loaded or is stale. Random draws stay in Python and
  go in as arrays (seeded noise via `core.noise` looked up at call time —
  the parity corpus swaps it — and supersaw phases), so native renders
  match NumPy's. `SYNTHLAB_NATIVE=0` / `SYNTHLAB_NATIVE_OFF=stack,adsr`
  switch kernels off for A/B or bisecting.
- `effects.py` — chainable effects as `partial`s: `fx.chain(x, [fx.reverb(...)])`.
  All take mono or stereo and return the same shape. `fx.pan` keeps a stereo
  input's image (it used to mono-sum first, so panning a track threw away
  unison spread, chorus and reverb width); `_limit` is a real lookahead
  limiter — its block maxima look *forward* and only the recovery is
  smoothed, and its smoother starts from the first gain value or it swallows
  the first 50 ms of whatever it is handed.
- **Vocoder** (`effects.vocoder`, `tests/test_vocoder.py`; the studio's
  Voice lab is its one caller): two signals, so not an FX unit and in no
  render key. N log-spaced constant-Q bands (4-pole: two cascaded
  bandpasses), each modulator band's follower the gain of a carrier
  band. `shift` (semitones) moves the carrier bands off the modulator
  bands that drive them — the formants move, the pitch is the
  carrier's. `noise` is the consonant path: where the modulator is
  unvoiced (`effects.unvoiced`: its level above 4 kHz over its whole
  level, mapped 0.25..0.6 -> 0..1) seeded white noise at the carrier's
  RMS goes through the same bands under the same envelopes. **With
  `shift=0, noise=0` it is the old call sample for sample** (pinned
  literals): add arguments the same way. It level-matches on the whole
  buffer, which is fine *here* — an offline bake, nothing `Synth.note`
  or a live FX unit reaches (the causal contract below does not bind
  it). Measured: a vowel's per-band levels are imposed on a saw within
  4.8 dB over the bands within 15 dB of its loudest; a valley between
  two formants is filled by its neighbours' skirts (up to 9.5 dB at
  -19 dB).
- **Studio tricks** (`effects.py`'s last section,
  `tests/test_effects_booth.py`; the Voice page's booth is their first
  caller, every rack has them): `doubler` (ADT), `rotary` (horn + drum,
  `rotor_rate` slews a `speed` lane), `tape_echo`, `telephone`
  (`bandpass` twice), `pitch` (`_pitch_shift_causal` at 40 ms grains,
  each **spliced in phase with the last**, `_pitch_splices`: a search
  over the past alone, so causal, and the live twin picks the same start
  — before it a steady tone came out as lines 1/hop = 50 Hz apart round
  the target; measured in `docs/sampler-and-clips.md`, "The vocal booth";
  a trick, not a tuner), `plate` (`plate_ir`: no early reflections, a
  two-band decay). All causal, every modulation a function of time alone
  (`wander`: three sines, never a random stream) so the live twins in
  fx-worklet.js are sample for sample, and each ends in `dry * (1 - mix)
  + wet * mix` with a mix-free wet (a mix lane is exact). The plate is its
  own unit, not a `kind` on `reverb`: a new param on reverb would enter
  every stored reverb's clean form and move every key holding one, and
  the two share no parameter but decay and mix.
- **Live vocal units** (`effects.py`, `tests/test_effects_vocal.py`,
  `tests/studio/test_live_vox.py`; the live vocal chain on the mic is
  their first caller, every rack has them): `autotune` (a pitch read per
  20 ms grain, the nearest note of a key + scale, `speed` / `humanize` /
  mix; unvoiced input passes sample for sample), `harmony` (one or two
  voices a scale interval away, in key), `vox_comp` (a causal compressor:
  `compress` stays offline, its automatic makeup is the loop's), `deess`
  (split band) and `vgate` (gate / downward expander). The pitch ones run
  on the booth's spliced causal shifter, now with a ratio per grain — at
  one ratio bit-identical to before, so `RENDER_REV` did not move. Their
  live twins are `static/vox-worklet.js`, a module beside `fx-worklet.js`,
  held to the engine by `test_live_vox.py`. No formant keep yet: the
  shifter resamples, so a fifth up is a smaller singer.
- **Movement, tape and spectral units** (`effects.py`'s last section,
  `tests/test_effects_fx2.py`): `autofilter` (an LFO and / or envelope
  follower on the swept SVF's cutoff, `autofilter_cutoff`), `tremolo`
  (amplitude, an auto-pan at 180°), `tape` (wow / flutter as a moving
  delay, tanh, hiss = `tick_noise`, a hash of the sample's index — never a
  random stream, silent in silence — head bump, top, a 25 Hz low cut),
  `pingpong`, `spring` (`spring_ir` in closed form, a ConvolverNode live,
  as the plate), `formant_shift` (a graphic EQ in series whose gains copy
  the envelope across: Simper bells held per 64-sample block, as
  `_eq_swept`'s), `freqshift` (Niemitalo's Hilbert pair, SSB),
  `exciter`, `vocoder_fx` (its own band-limited saw chord in key, the
  input the modulator — where `vocoder` reads the whole take). Every LFO's
  phase is a function of time from the buffer's start; a synced `rate` /
  `time` is resolved by `fxrack.resolve_unit` (`LFO_SYNC`). Live twins:
  `static/fx2-worklet.js`, a fourth module in the FX scope, sample for
  sample (<= 6e-8 of peak, `test_live_fx.py`). No existing unit moved:
  `RENDER_REV` did not either. The formant shifter is the expensive one
  (0.06 s of CPU per second of stereo; the vocoder 0.03): NumPy, no
  native kernel.
- **Dynamics: OTT, limiter, transient shaper** (`effects.py`'s last section,
  `tests/test_effects_dyn.py`): `ott` (three Linkwitz-Riley bands —
  `ott_bands`, 120 Hz / 2.5 kHz, summing to the input through two
  all-passes — each pulled toward the middle from both sides by its own
  `_envelope_follower`, `depth` the parallel blend band by band),
  `limiter` (the rack's: its lookahead is a **delay**, a sliding minimum
  over the window behind now — `scipy.ndimage.minimum_filter1d`, live a
  deque — a release one-pole, a ramp over the same window; the latency
  is the lookahead, the same live and in the loop. `limit`, the song
  master's, reads the future of its buffer and is no rack unit) and
  `transient` (attack / sustain from `peak_follow`, a peak follower
  written as a running maximum in the log domain — no Python loop — with
  floors over a low tone's own ripple). Silent in silence, causal, live
  twins in `fx2-worklet.js` (<= 2.0e-7 of peak, a stacked chain
  included); lanes on the OTT's depth, the limiter's gain, the shaper's
  attack and sustain. **The envelope filter is the auto-filter's `env`**
  (a preset each way), not a unit. No existing unit moved: no `RENDER_REV`.
- `sequencer.py` — `Song`: beat-based multitrack timeline. `note_span()` is
  the one definition of how long a note's buffer is: its own length plus the
  patch's release (capped at `MAX_NOTE_TAIL`). It was a flat `dur * 1.15`
  against a 0.9 gate, which squeezed every release into the 25% left over —
  a pad asking for 2.2 s got 0.125 s of it, and notes never rang into each
  other. Anything reproducing the schedule by hand must call it too. The
  buffer only makes room: how long the note rings is the patch's release
  (`core.adsr`, the artifact contract below), and whatever the buffer holds
  past it — the `0.15 * dur` floor on a short release — is silence.
  **`Song.render()` peak-normalizes every call** (default 0.89); anything that
  sums multiple renders (the studio's stem cache) must pass `normalize=0` and
  normalize once at the end.
- `explore/breed.py` — genetic patch breeding over `params.GENES`: each
  gene's kind and range come from its `Param`, and mutation only spends
  budget on `params.live(p)` (never on osc2 params while osc2 is off). What
  stays here is breeding *strategy*: litters, jumps, `_MODULE_GENES`, the
  crossover blocks (`CROSS_BLOCKS`, whose order is also RNG order). Tests
  pin a minimum perceptual spread per generation. `steer()` gives one pick
  in four to the audible candidate farthest from the rest rather than the
  next best (the best-scoring children are near-clones of each other);
  `tools/breed_spread.py` measures spread over steered generations. Breeding whole Sounds
  (following layers made to role recipes, FX, curves — `state.breed_depth`
  layered/deep) is the studio's `studio/genome.py`, on top of this: a new
  Patch field a role should pin belongs in its `ROLES` rules too (see
  `synthlab/studio/docs/sounds-and-breeding.md`, "Breeding").
- `analysis/` — audio → `Features` vector; used by breeding (novelty gate,
  steering), the map view (PCA), and tile badges. `analysis/loudness.py`
  is ITU-R BS.1770 programme loudness (`integrated_loudness`, LUFS:
  K-weighting designed from the standard's analogue prototypes so 44.1 kHz
  reads like its 48 kHz table, 400 ms blocks, -70 / -10 LU gates; `-inf`
  for silence) and `true_peak_db` (4x oversampled). Pure functions, pinned
  to EBU Tech 3341's cases within 0.1 LU (`tests/test_loudness.py`); the
  studio uses them for the song export's loudness targets only.
- **Whole-song analysis** (the studio's Sample Lab; `tests/test_song_analysis.py`
  against loops rendered by `tests/songs.py`): `analysis.tempo(audio) ->
  Grid(bpm, confidence, beat_offset, downbeat_offset)` (`analysis/rhythm.py`:
  a band-flux onset envelope at 100 fps, autocorrelation with a tempo prior
  around 120 bpm — so a 174 bpm tune reads 87, and the Lab has halve /
  double — then the envelope *folded* at the beat period, coarse to fine,
  for a tempo good to ~0.1 % and a beat phase within ~20 ms; the downbeat
  is a guess from where the harmony changes) and `analysis.detect_key(audio)
  -> Key(tonic, mode, confidence, scores)` (`analysis/key.py`: a
  peak-picked pitchgram at 5.5 kHz, drums taken out by a median over time,
  harmonics subtracted, log-compressed, against the Krumhansl-Schmuckler
  profiles; a near-tie between relative keys goes to the stronger tonic).
  Given the separated **bass** (`detect_key(audio, bass=, bar=, offset=)`)
  the relative pair is settled by where the bass line rests instead
  (`tonic_weights`: its pitch classes, counted harder on bar and phrase
  downbeats and over its last bars) — asked of that pair only, never of
  all 24 keys. `Key.confidence` is about the scale, `Key.margin` its lead
  over the relative, `Key.tonic_from` "bass" when the bass decided.
  Constant-tempo grids only. Its docstring lists the measured limits (a
  long driven kick, a sub two octaves under the range, relative keys).
- **A stem read for the sampler and the drum grid** (the Sample Lab):
  `analysis/voiced.py` — a framewise YIN `pitch_track` (the sampler's own
  frame), `steady_runs` / `best_run` (the held note to loop: seconds x
  periodicity x level), `syllables` (spans to chop) and `melody` / `snap`
  (the sung line as a per-sample pitch curve a synth follows: the Voice
  lab's "follow" carrier — a voiced frame counts only in a run of three,
  one further than a fifth from the half second around it is a tracking
  error and is put back on the line, a held leap keeps its place);
  `analysis/drumhits.py` — `find_hits` (onsets from four bands' rise),
  `classify` (hits clustered by spectral shape, so "the same drum" is
  learned from the recording; a cluster named kick / snare / hat by its
  centre; second lanes; open hats by decay), `to_pattern` (16th steps,
  swing, a velocity lane) and `exemplars` (one clean hit a lane). Both are
  about one part on its own — a stem. `tests/test_voiced.py`,
  `tests/test_drumhits.py` (precision / recall per lane against patterns
  the drum machine rendered: kick 0.99 / 1.00, snare 0.94 / 1.00, any hat
  1.00 / 0.80 — a hat under a snare is not looked for).
- **Stem separation** (`synthlab/stems.py`, the `stems` extra): HT-Demucs
  as an ONNX graph on onnxruntime — not PyTorch, whose Windows-on-ARM
  wheels stop at cp312. `separate(mix, run)` is the chunked overlap-add
  around any `run(chunk)`; `download` / `verify` fetch the model from a
  URL pinned to a commit and hold it to a SHA-256 pinned in code. Two
  models, `stems.MODELS`: `HTDEMUCS` (4 stems) and `HTDEMUCS_6S` (guitar
  and piano as well; same graph interface, each carries its own
  `sources`). The studio half is in
  `synthlab/studio/docs/sample-lab.md`.
- **Artifact contract** (`tests/test_artifacts.py`, thresholds measured
  against the broken code): `core.lfo`'s "noise" shape is a seeded
  sample-and-hold at the given rate, not audio noise — it used to call
  `noise(n)`, which ignores the rate, so a 2 Hz LFO sprayed 22,000 zero
  crossings a second into whatever it modulated *and* re-rolled itself every
  render, quietly invalidating every content-addressed cache. `core.adsr`
  always lands on silence: with `gate >= duration` there is no room for a
  release and it used to stop dead at the sustain level. **A gated release
  lasts the release time**, with the envelope's curve, and the envelope is
  exactly zero after it: the ramp used to span whatever was left of the
  buffer, so `note_span` — not the patch — set how long a note rang (a
  0.02 s release on a 2 s note was still at 68 / 45 / 23 % of its gate level
  50 / 150 / 300 ms after key-up; a 1-bar note at 124 bpm rang 190-410 ms
  past its release). A buffer that ends before the release does (a release
  over `MAX_NOTE_TAIL`) has it fitted into what is left, as every release
  used to be, so it still lands on zero; `gate=None` is unchanged (its
  hold is what the release leaves). Both envelopes follow it, so
  `filter_release` is read with a gate too — it was a dead control in the
  loop, and the live voice let the filter fall over the amp release to
  match; both now use the filter's own. The filter envelope is the one
  that is *not* fitted (`synth._filter_env`): the amp envelope lands the
  note, so a filter release the buffer cannot hold is cut where the note
  ends, as a live key's is — fitted, a 0.2 s filter release on a 0.09 s
  amp release closed twice as fast in the loop as under a key (the parity
  corpus caught it: three sounds at C6, envelope p95 1.03 dB).
  `core.dc_block`
  runs on every voice after the unison sum — a square of duty d carries a DC
  term of 2d-1, so PWM walked `stab_hoover` to 32.7% of its energy below
  20 Hz (now 3.6%); its state is initialised from the first sample so the
  filter's own settling is not the thump it removes. `formant`'s wet-path
  makeup gain is capped (`_FORMANT_MAKEUP_MAX`): real vowel excitation needs
  6-8x, a 45 Hz sine asks for 50x and comes back louder than it went in.
- **Causal contract** (`tests/test_live_causal.py`): the
  studio's live voice renders sample by sample and must match the engine,
  so nothing a note renders may depend on the note's own future. Four
  places used to: pink/brown noise are now white noise through fixed IIR
  filters (`core.colour_noise`: Kellet's pink, a 20 Hz leaky integrator;
  gains = the old level of a 1.3 s note — the old FFT shaping + whole-buffer
  peak made brown ~10 dB louder on a 0.3 s note than a 4 s one); `pluck`
  divides by its first period's peak (identical to the old whole-note peak
  in every case measured); `formant`'s makeup is a fixed table per
  vowel/size (`core.formant_makeup_table`, a saw's 1/f^2 envelope from C3
  through the tract, still capped by `_FORMANT_MAKEUP_MAX`) instead of the
  note's own RMS ratio — so a formant sound's level now follows the tract
  per pitch (measured -12..+11 dB vs dry across C2-C5 on the default saw,
  where the old code renormalised every note); and a second mod route on
  the same target clips to the span widened to the *patch's* value, not
  the first route's whole-note min/max. Don't reintroduce whole-buffer
  normalisation into anything `Synth.note` reaches. The same holds for the
  FX units the live keys run (`studio/fxlive.py`): the reverb's
  wet gain is the room's own (`effects.reverb_ir`: 1/||ir|| less 1.5 dB,
  the median of the old per-buffer peak match — so a loud hit no longer
  turns the room down on every other note), and `_mod_delay` never reads
  ahead of now (a flanger swept deeper than its base delay used to read up
  to 3 ms of the future). Granular, shimmer, compress and vinyl stay
  whole-buffer: `fxlive.FX_LIVE` marks them offline. The engine tables the
  live voice reads (wavetable mips, the seeded uniform streams pluck and
  the stepped LFO draw, the makeup table, the noise filters) are served
  from `studio/voicetables.py` (`GET /wavetables.bin`) — change them in
  core and the live voice follows.
- Regenerate factory/library metadata with `uv run python
  tools/refresh_sound_meta.py` after any engine change that moves what a
  patch renders to. NOT `python -m synthlab.studio.library`, which re-draws
  all 96 candidates against the new measurements and changes every sound's
  identity and name; the refresher remeasures the sounds that exist and
  re-ranks each family, because brightness and the shade tag are derived
  from the spectral centroid. It also remeasures the genre recipes
  (`studio/recipes.py` -> `presets/recipes_meta.json`). The same engine
  change **bumps `studio/render.py`'s `RENDER_REV`** (and the four pinned
  key literals it moves): `/preview/{key}.wav` is served immutable, so a
  key whose inputs now render differently must become a new key (studio
  `docs/render-and-caches.md`, "The loop key").
- **Genre layer** (details in
  `synthlab/studio/docs/sounds-and-breeding.md`, "Genre recipes, kits and
  chains"): 12 melodic-techno / UK-garage recipe Sounds
  (format-2: patch + layers + FX + curves, the `recipes` source and the
  Library's Recipes shelf), two drum kits + beat styles, five FX chain
  presets ("+ add chain…"), and two breed directions (`melodic_techno`,
  `garage`: a target profile plus gene/role/FX leanings). All built from
  existing engine params and FX units; generic names only, never an
  artist's. **One exception, deliberately: a style's label (and the kit
  made for it) may be a nod** — a play on a name that is never the name
  itself: *Daft Rock* (filter house), *Fred Before* (emotive garage),
  *Just Ice* (French electro), *Nine Inch Wails* (industrial), *Psych
  Haze* (neo-psych), *Sixties Studio* (60s studio), *Anymore* (melodic
  techno) and *Arm In Arm* (trance). Ids, recipes, chains and every other
  name stay generic, and nothing recreates a track, a hook or a sample.
  Five genres under plain labels — hip-hop / trap, drum & bass, dubstep,
  synthwave, future bass — are 22 recipes, five kits, eleven beat styles
  (two feel keys: `rolls`, `ratchet`), seven chains and five starters
  (`sounds-and-breeding.md`, "Style packs"), on existing units and Patch
  fields only. Five more use the newer oscillator features (hard sync,
  fm4, the extra wavetables, drift) — lo-fi hip-hop, ambient / cinematic,
  chiptune, hardstyle, phonk: 23 recipes, five kits, ten beat styles, six
  chains, five starters; the Growl Bass is a 4-op stack, the Screech Lead
  hard-synced and the Neuro Reese drifts. No unit or Patch field exists
  for a style alone. **The core library** is no style: 49 recipes named
  for what they are (tag `core`, 160 recipes in all) on a Core shelf of
  83 by category that lists the genre recipes and factory sounds already
  being the thing rather than copying them (`recipes.CORE_SHELF`; "The
  core library"), and the 96 generated sounds are named for what they
  measure as (`library.describe`; a sound's earlier name is kept as
  `was`, which still finds it).
  **Four style packs** (the same doc, "Style packs") are more
  entries in the same tables and nothing else — filter house (~122 BPM,
  recipe tag `house`), emotive garage (the garage pack topped up),
  melodic techno (topped up) and trance (~138): 17 more recipe Sounds
  (29 in all; appended — the first twelve keep their places), kits
  Filter House / Two-Step / Trance, eight beat styles, eight chain
  presets, the directions `filter_house` and `trance`, eight gate
  rhythms (`fxrack.GATE_PATTERNS`) and a Voice preset
  (`voice.PRESETS`). Styles, not songs: tempo, drum grammar, sound
  design and mix moves — no track, hook or sample is recreated, and no
  audio file ships. Three things to keep when adding to a pack: a
  ducked recipe uses `pump`, never `sidechain` (it must work on Synths,
  where no drums render); the two new directions' target profiles are
  **measured from their own recipes** and
  `tests/test_genre_steer.py` re-measures them, so editing a recipe
  can fail it until the target follows; and a pack's measured table
  (level, band, hold, live keys, the rolling bass against the kick) is
  `tests/studio/test_style_packs.py` — it says where a sound sits, never
  whether it sounds like the style.
- **Wavetable oscillator** (`core.WAVETABLES` / `core.wavetable`, shape
  `"wavetable"`, Patch fields `wt_table` / `wt_pos`): twelve morphable
  tables of four single-cycle frames (eight — `scan`, `organ`, `bell`,
  `stack`, `pulse`, `formant`, `crush`, `analog` — appended after the
  first four, so saved indexes and the first four's samples stand). Frames are
  defined **spectrally** (a harmonic
  amplitude vector per frame) and rendered into a mip stack of harmonic caps
  — `_wt_mip` picks the brightest cap under Nyquist for the pitch, so the
  shape never aliases, matching the PolyBLEP bar the other oscillators set.
  Defining the tables as sampled waveforms instead would alias badly. `wt_pos`
  is CURVABLE: sweeping the table across a note is the point of the shape.
  Live-playable: the worklet reads the same mip stack from
  `/wavetables.bin` and picks the mip per 64-sample block from the block's
  highest pitch, as `core.wavetable` does. A frame's amplitude may be
  complex (sine part real, cosine part imaginary: a pulse and a
  quantised wave are not odd functions); `crush` is a quantised,
  sample-held wave taken through the FFT to its harmonics, so the crush
  is in the spectrum and the mips still band-limit it. The payload the
  page fetches is 3.81 MB (1.45 with the first four tables alone;
  cached immutable by its hash).
- **Hard sync** (`core.sync`, Patch `osc2_sync`
  / `sync_pitch`, `tests/test_osc_sync_fm4_drift.py`): osc2 (saw, square,
  triangle or sine; other shapes ignore it) restarts whenever a master
  phase at osc1's pitch — before cross-FM, a ghost of osc1, so any osc1
  shape is a master — wraps; `sync_pitch` semitones lift osc2 over where
  it sat (CURVABLE, a mod target the gene's routes never draw). Every
  step is an event at a fractional time with a jump — the slave's own
  wraps (a square is a saw less a saw `width` behind: its two edges are
  two saws' wraps) and each reset, of whatever height it cuts — and the
  polyBLEP residual is spread over the samples either side. The sample
  before an event is only known with it, so **the synced osc2 runs one
  sample late** (the live voice holds one back the same way).
  Vectorised between resets, a loop over the resets. Measured against a
  naive reset: 15-17 dB less alias (saw at 220 / 880 / 1760 Hz: -34 /
  -30 / -26 dB against -19 / -13 / -11), harmonics within 0.1 dB of a
  32x-oversampled reference at 220 Hz. The native stack hands a synced
  patch to the Python loop (a few ms a note).
- **4-operator FM** (`core.fm4`, shape `"fm4"`, Patch
  `fm4_algo`, `fm4_ratio1-4`, `fm4_level1-4`, `fm4_decay1-4`,
  `fm4_feedback`): four sine operators in one of `core.FM4_ALGOS` —
  `stack` 4→3→2→1, `pairs` (2→1)+(4→3), `three` 2+3+4→1, `pair_plus`
  2→1 with 3 and 4 as carriers, `additive`. A modulator's output (level x
  its sine, its level decaying over its own `fm4_decay`) bends its
  target's phase by `FM4_INDEX` (6) radians a unit; carriers are
  averaged; operator 4 feeds back on itself (the mean of its last two
  outputs, `FM4_FEEDBACK` 1.5 radians at 1) — that one operator is a
  Python loop when feedback is on (+4 ms a second of audio), the rest
  vectorised. Scalars, not curves (the voice reads them per block). A
  hand-made shape like noise and sampler: in the osc caps, not the
  genes; its fields breed once a sound has it.
- **Analog drift** (Patch `drift`, `synth.drift_*`): per
  note, a slow pitch wander (a leaky random walk's knots every
  `DRIFT_KNOT` 0.5 s — a whole number of 64-sample blocks —
  smoothstepped, `DRIFT_CENTS` 10 x the amount, RMS ~0.58 of that,
  under 1 Hz), a start phase per oscillator and unison copy, and the
  cutoff off by up to `DRIFT_CUT_OCT` 0.25 octave. **All of it from one
  seed: crc32 of the note's MIDI number** and a 32-bit integer hash
  (`drift_u`) the live voice computes bit for bit (`Math.imul`) — the
  pitch alone, so the note memo's renders and every cache key hold, and
  a live key is the lone note `/note` renders. A drifting patch takes
  the Python loop (the start phases). At 0 nothing runs (gated on the
  amount, as hard sync, fm4 and drift all are on their switches:
  `tests/test_osc_sync_fm4_drift.py` fails a render of any patch that leaves them
  off yet reaches their code).
- **Phase warp** (`core.warp_phase` / `warped`,
  Patch `osc1_warp` / `osc1_warp_amt`, `osc2_warp` / `osc2_warp_amt`,
  `tests/test_osc_warp_wavetables.py`): the phase bent before the shape reads it,
  on `core.WARP_SHAPES` (sine, saw, square, triangle, wavetable; the rest
  and a synced osc2 ignore it — `synth.warp_of`). Modes (`WARP_MODES`):
  bend + / − / ± (a rational curve, `t / (k - (k - 1) t)`, k = 1 + 15 x
  amount), PD (two straight segments, the half-cycle at 0.5 - 0.49 x
  amount), mirror, asym (the first half bent), quantize (2 + 254 (1 - a)^3
  steps), pwm (the cycle in 1 - 0.95 a of it, then held) — + - x / and
  floor only, so the live voice computes the same numbers. A bent phase
  breaks PolyBLEP: the warped oscillator runs **4x oversampled**
  (`WARP_OS`), its own wrap polyBLEP'd with the step the warp leaves there
  (`warp_end`), down through **`WARP_TAPS`** (a 95-tap Kaiser lowpass at
  20.5 kHz made minimum-phase: 48 taps, ~1 sample of delay, so a warped
  oscillator beside a plain one doesn't comb); output sample j is the FIR
  over sub-samples to 4j + 3 (the plain phase's), zeros before the note.
  A warped wavetable takes the mip `warp_slope` times darker. Alias at 880
  Hz, warped / naive (the warp at the rate, no band-limiting): saw bend
  -45.5 / -20.7 dB, saw PD -42.0 / -17.1, sine bend -53.1 / -34.1; the
  square's mid edge is not polyBLEP'd (-35.6, the plain square's floor).
  The amount is CURVABLE and a route target (not a gene choice). **"off"
  is the bypass**; any other mode takes the oversampled path even at
  amount 0 (the live voice can't switch paths mid-note). The native stack
  hands a warped patch to the Python loop (~6 ms a second of a saw, ~20 a
  wavetable).
- **Your own wavetables** (`synthlab/wavetables.py`, Patch
  `wt_custom`): a clip or a sample sliced into 4-64 single cycles (equal
  slices, or one period of its YIN pitch at each point) or up to four
  drawn cycles, as harmonic spectra (256 complex64 a frame, sine part
  real); **content-addressed like samples**: the id is the sha1 of the
  spectra, stored as `<dir>/<id>.npy` + `.json` (a name) in any
  `wavetables.add_dir` dir (the studio's `studio_data/wavetables/`), built
  into the built-ins' mip stack by `core.wavetable_stack` (the inverse
  FFT; a built-in's spectra give its stack within 1e-12). `wt_custom`
  names it (a "meta" field, `META_CLEAN` keeps it an id); `wt_table` plays
  while it is ""; **a table this machine doesn't hold plays "basic"**,
  never an error. `core.wavetable` / the native kernel take a stack as
  readily as a name. Live: `GET /wavetable/live/<id>.bin`
  (`live_payload`), never `/wavetables.bin`.
- **One voice** (Patch `voice_mode` poly / mono / legato,
  `glide_mode` always / legato; `sequencer.mono_lines`, `Line`,
  `cut_line`, `Synth.note(path=)`): a mono or legato patch's notes render
  as **lines** — one `Synth.note` each, its buffer faded over
  `MONO_FADE` (3 ms, linear, the live steal fade's samples) where the next
  line starts and silent after. Mono: every note a line. Legato: a note
  starting while a key of the line is down (or a slide, which holds the
  gate into it) joins it — `path` re-aims the one voice there (a
  geometric glide over `glide`, or the next sample), the envelopes and
  drift running on, the filter's key tracking following the path; the
  newest key up while older ones are held goes back to the newest of
  those. A new line glides in from the last note when `glide` > 0 and
  `glide_mode` is "always" (or a key is still down), and on a slide.
  `arrange._timeline_notes` hands a one-voice patch to `_mono_timeline`
  (`_uses_timeline` takes the patch: a one-voice lane is time-ordered even
  with no slide, arp or strum); `Song.notes` to `_mono_notes`. The poly
  path is untouched. The fields are in every render key that hashes the
  patch.
- **Sampler** (`synthlab/sampler.py`, shape `"sampler"`, `sampler_*` Patch
  fields, `tests/test_sampler.py`): recorded audio as an oscillator, through
  the same amp env, filter, tract and FX. **Samples are content-addressed**:
  `sampler_sample` is the first 16 hex of the sha1 of the 16-bit PCM, stored
  stereo at `SR` (<= 30 s) as `<dir>/<id>.wav` in any `sampler.add_dir` dir
  (the studio's `studio_data/samples/`) and in a byte-bounded memory cache —
  so every key that hashes the patch carries the audio by content, and a
  missing sample is silence. Pitch is array-safe (glide, vibrato, bends,
  pitch routes): `resample` (tape) or `formant` (a grain train two root
  periods long, snapped to whole source periods, re-spaced at the played
  period — set the root to the sample's pitch; at the root it IS the
  recording within 0.7 %). `sampler_stretch` != 1 (CURVABLE) is a WSOLA
  time-stretch. Loops bake a crossfade whose law follows the two sides'
  correlation. Chops (`sampler_slices`, "a,b,pitch,rev;...") put slice i on
  root + i, wrapping; each has its own semitones and reverse. A sampler
  voice is **stereo** (n, 2): `_osc_stack` promotes the sum (`_times`/
  `_plus`); every other shape stays mono and bit-identical. All fields are
  `nogene` (breeding never draws them; "sampler" is in osc1/osc2's `cap`,
  not their gene) and keep their `uses_shape("sampler")` rule, which
  `SHAPE_DEPS` hides rows by and `worklet_blockers` skips them by while no
  osc samples. `sampler_sample`/`sampler_slices` are "meta" strings cleaned
  by `params.META_CLEAN` inside `clamp`. Not causal-contract bound: a sample
  is known whole at note-on. **The live voice plays what is causal**: the
  server prepares the sources (`_whole_source`/`_slice_source` — the same
  ones `render` reads: declicked, loop crossfade baked, chops reversed) as
  `GET /sampler/live/<id>.bin` (`live_payload`; `/patch.json` names it as
  `sampler_live`), and the worklet steps a read position by the live pitch.
  `sampler.live_ok` admits "resample" with a scalar stretch of 1 and
  "formant" with any scalar stretch, mono or true stereo: a sample whose
  channels are the same sends one (`ch` 1, the engine's stereo pan law,
  x sqrt 2, applied to it), any other sends both and plays as a stereo
  pair, summed as `_osc_stack` sums it (the read is
  `static/sampler-worklet.js`, a module in the voice worklet's scope).
  `LIVE_MAX_SAMPLES` counts both channels. Neither grain engine can be
  baked server-side: `_grains` puts its grains on the output clock and
  reads each at the note's pitch ratio, `_psola` spaces them by the played
  period — the pitch is inside the grain schedule, so a baked buffer read
  at ratio r is a different sound (1/r as long). Don't fake them with a
  pitched read of a stretched buffer. **Formant mode is `_psola` itself in
  the voice** (`static/grain-worklet.js`, a third module in that scope,
  over the same payload; held to `_psola` sample for sample by
  `tests/studio/test_live_sampler.py` and by the corpus). **`_psola` reads
  the pitch one root period ahead** (at each grain's centre; the grain
  starts half its length earlier), so the voice foresees its own motion
  that far (`ahead`: glide, vibrato, pitch env, the bend where it stands
  — the engine's samples, 0.00 dB in the corpus) and the gate refuses
  what cannot be foreseen: a route to `pitch` / `osc1_detune` /
  `vibrato_depth` (named by its `modN_src`) and cross-FM (`fm_amount`)
  on a formant sampler. A wheel gesture mid-note reaches the schedule up
  to a root period late: right spacing after it, another grain phase than
  a render of the recorded gesture — as far off as the engine is from
  itself when the gesture lands one render quantum later (see
  `sampler-and-clips.md`). A change to `ctl()`'s pitch terms in
  voice-worklet.js must be made in `ahead` too. **The WSOLA stretch
  ("resample" with stretch off 1) and a drawn stretch keep the `/note`
  WAV path**, named `sampler_stretch`: a similarity search per grain,
  measured at 0.18 ms (FFT) to 1.5 ms (direct) of JS per grain, 87 grains
  a second per unison copy.
  **Zones: a multi-sample instrument** (`sampler_zones`, a
  "meta" string `META_CLEAN` cleans, `parse_zones` / `format_zones`:
  "sid,lo,hi,root,vlo,vhi,rr,gain,tune,start,end,loop_start,loop_end;..."
  — trailing defaults left off, a span or loop left empty is the
  patch's; at most `MAX_ZONES` 64, takes 0..15, gain -36..+12 dB, tune
  ±100 cents). Set, it plays instead of `sampler_sample` / `_slices`
  (`render` -> `_render_zones`): **`pick_zones`** takes the zones whose
  keys (the note's key, `key_step`: MIDI rounded half up) and velocities
  (`velocity_step`: `floor(v * 127 + 0.5)`) hold it; of their distinct
  `rr` values, in order, take number `rr % count`; of that take's zones
  the two whose velocity ranges start highest, **crossfaded** (equal
  power) over the velocities both hold. Each picked zone is a sample of
  its own (`zone_settings`: its root, its span / loop, else the patch's;
  no chops) rendered by `render` at its fine tune, times weight x gain,
  summed. One zone at weight 1 and 0 dB is that sample's render bit for
  bit (`tests/test_sampler.py`). **The round robin is deterministic: a
  strike count, never a random stream.** `Synth.note(rr=)` (into
  `_osc_stack(velocity=, rr=)` -> `Settings.of(p, velocity, rr)`) is the
  k-th strike of its key in its phrase, counted by `sampler.Strikes` —
  only for a `round_robin` patch (a map with more than one take; every
  other patch gets 0 and nothing passed, so nothing it renders moved) —
  in exactly these schedule places: `Song.notes` / `Song._mono_notes`
  (sequencer.py) and `arrange.MemoSynth.note`, `_curved_notes`,
  `_timeline_notes`, `_mono_timeline` (a one-voice line is one strike of
  its first key). The count restarts with each phrase: a loop cycle, a
  section chunk (`song_model.SectionParts.window`), a layer's row. The
  live voice counts the same per key from when the sound's buffers
  arrived (voice-worklet.js `strike`; a noteOn's own `rr` wins) and picks
  with sampler-worklet.js `pick`, `pick_zones` line for line. The live
  payload carries every zone's source (`_zone_sources`, the header's
  `zones`: keys, velocities, take, root, gain, tune, `src`); its URL is
  named by the first zone's sample and the query carries the map (no
  other URL moved); `live_size` / `live_zone_samples` count every zone
  against `LIVE_MAX_SAMPLES`. The `/note` WAV keys render at velocity 1,
  strike 0 (the top layer, take 1): the live voice is what plays layers
  and takes. Empty (the default) = one sample, bit-identical (every
  shipped patch hashed before / after, native and `SYNTHLAB_NATIVE=0`):
  no `RENDER_REV` bump, and `sampler_zones` is a `KEY_ADDED_FIELDS`
  entry.
- **Vowel filter** (`core.VOWELS` / `core.formant`, Patch fields
  `formant_amount` / `formant_vowel` / `formant_size`): three parallel
  bandpass resonators whose centres interpolate along a→e→i→o→u. It is a
  *Patch* field, not an FX unit, on purpose — `CURVABLE` and macros both
  target Patch fields only, and FX params are baked into a `partial` once
  per render, so an FX-unit version could never be swept. `amount` 0 is
  both the default (old presets unchanged) and the array-safe gate. Sits
  after the filter, before the amp env: filter is the source, formants are
  the tract. Live-playable: three bandpass SVFs in the worklet with the
  makeup read off the same table (see the causal contract below).
- **Sound-design layer** (added on top of the bench):
  - `lfo_shape` / `filter_lfo_shape` — `core.lfo` always took a shape and
    every call site hardcoded `"sine"`. `env_curve` — `core.adsr` always took
    `curve` and nothing passed it, so every envelope sat at 2.0. Both were
    engine capabilities the UI simply could not reach.
  - **Tempo sync** (`synth.py` `SYNC_DIVISIONS` / `at_bpm`): LFO rates are raw
    Hz in the Patch; a sync division names beats-per-cycle and `at_bpm()`
    resolves it to Hz *just before rendering* — in `build_song()` and in the
    `/note/` route. Nothing downstream learns about tempo and the Patch stays
    a flat serializable genome. Both call sites must stay in step or a live
    key plays at a different rate than the loop.
- **Mod matrix**: **twelve slots**, listed once as `params.MOD_SLOTS`. The registry, the engine, the UI, the
  chain bar and the worklet gate all read that list rather than repeating
  themselves per slot. Only the first four are genes
  (`MOD_GENE_SLOTS`: their Params sit where they always did in the
  draw order; slots 5-12 are `nogene` Params appended at the end, and
  breed.py / genome.py / modmixes walk the four, so seeded litters and
  mixes stand); a later slot defaults to off. **Sources**
  (`MOD_SOURCES`, appended): lfo (LFO 1), fenv, aenv, velocity, then
  lfo2, lfo3, env3, env4, wheel (`mod_wheel`), aftertouch, key ((MIDI -
  60) / 60, clipped to -1..1) and random (2 u - 1, u = `drift_u` of the
  note's seed — the pitch's crc32, as drift's: caches hold); the gene
  draws the first five (`MOD_GENE_SOURCES`), the cap takes all.
  `synth._mod_source` makes each one (`_mod_routes` takes the note's
  `f0`). Route targets are `params.MOD_DESTINATIONS` (menu order); each
  target's span is its `Param.mod`; the later targets (filter 2's cutoff
  and resonance, `wt_pos`, `fm_amount` — its gates are array-safe —
  `fm_index`, the four `fm4_level`s — `core.fm4` takes a level per sample —
  and `formant_vowel`, `ROUND6_DESTINATIONS`) sit before "pitch" and out
  of the gene's choices. Not targets: unison detune (the live stack holds
  it per render), pan (no per-voice pan field), LFO / envelope rates (a
  source on its own rate needs a per-sample loop). **"pitch"** is the one
  target that is not a field (`params.PITCH_ROUTE`, last in the menu): its
  routes add amount x `PITCH_ROUTE_ST` (12) semitones x the source, summed,
  never clipped, to every oscillator (`synth._mod_routes` returns them
  beside the modulated patch). Breeding never routes to it
  (`MOD_GENE_DESTINATIONS` is the dst gene's choices, unchanged, so seeded
  litters are too); the dst `cap` keeps a hand-made route through `clamp`.
- **General envelopes and LFOs** (`tests/test_modulators_filters.py`): two
  general envelopes (`env3_*`, `env4_*`: ADSR + curve, gated as the filter
  envelope is — `synth._gated_env`, which `_filter_env` also calls) and two
  general LFOs (`lfo2_*`, `lfo3_*`: rate, shape, sync — `at_bpm` resolves
  them — start phase, and mode "retrig" or "free": a free LFO starts each
  note at `lfo_free_phase`, a draw from the note's seed; a render cannot
  know where a note sits on the song clock without breaking the note memo
  and every cache key, so "free" means each note picks the cycle up at its
  own place). **The general shapes** (`params.GENERAL_LFO_SHAPES`, for LFO
  1 too — its gene keeps the five): "drawn" (`lfo_draw` / `lfo2_draw` /
  `lfo3_draw`, "x:y,..." meta strings, `core.clean_draw` in
  `params.META_CLEAN`: up to 32 points over one cycle, read by
  `np.interp` along the phase), "chaos" (the logistic map at r 3.99, a step
  a cycle, joined by lines, from a seeded start — float64 arithmetic the
  live voice repeats bit for bit) and "smooth" (`core.hash_u` values a
  cycle, smoothstepped); LFO 2 / 3's "noise" is `sample_hold`'s stream 1 /
  2 (the live table holds them) stepped on the phase. All a function of
  the phase from the note's start: loop-safe and live-reproducible.
  `mod_wheel` and `aftertouch` are **performance controls** like
  `pitch_bend` (`nogene`, CURVABLE, `paramui.PERFORMANCE_PARAMS`: macros
  refuse them, the XY pad plays them live); live, the hardware's value is
  added to the patch's (`keys-and-midi.md`, "The mod wheel and
  aftertouch"). Every field is opt-in: `tests/test_modulators_filters.py` fails a
  render of a patch that leaves them off yet reaches their code.
- **More filter types and filter 2** (`core.filt`, `params.FILTER_TYPES_ALL`
  appended after the six, so saved indexes stand; the gene keeps the six):
  `lowpass24` / `highpass24` (two SVFs, Butterworth at 0, the second's q
  raised by the resonance; "lowpass" is still the 12 dB SVF), `comb_plus`
  / `comb_minus` (a feedback comb, delay sr / cutoff with linear
  interpolation, feedback ±resonance up to 0.97, out x sqrt((1 - |g|)
  sqrt(1 - g^2)): ±6.4 dB peaks / noise at 0.9; tunes down to sr / 2000 —
  the live ring is 2048; pitch-tracked within 0.01 cent to C6, 0.3 at A7),
  `phaser` (three SVF all-passes x - 2 bp, averaged with the input: three
  notches), `diode` (SVF, an asymmetric tanh — the drive, a bias of 0.25 —
  SVF at q 0.5; no post clip after it, as the ladder) and `peak` (x + g bp,
  +18 dB x resonance). Each holds its cutoff per 64-sample block as the
  SVF does; all stable at full resonance under a 40 Hz <-> 15 kHz square
  sweep. **Filter 2** (`filter2_route` off / serial / parallel — the two
  averaged —, `filter2_type` any of the thirteen, its cutoff following
  the key and the filter envelope, CURVABLE cutoff and resonance, both
  route targets): `Synth._two_filters`. A new type or filter 2 takes the
  NumPy stages (`core.filt`; its SVFs still the native swept kernel), not
  `_native_post`. `core.filter_response` draws them on the Filter panel.
  The 293 shipped patches render bit for bit as they would without them,
  native and NumPy: no `RENDER_REV` bump.
- **Pitch bend** (`pitch_bend` -1..1 x `bend_range` 0..24 st, default 2):
  applied after glide, vibrato and the pitch env, with the pitch routes, on
  every oscillator (pluck takes its first value, as it does any pitch).
  Both are **performance controls, not genes** (`Param.nogene` says why;
  their cap is their `bounds`). `pitch_bend` is CURVABLE: a drawn bend, or
  one loop recorded from the keyboard's wheel. Live, the wheel (bend strip,
  MIDI 0xe0, the XY pad's "Pitch bend" axis — never written to the patch,
  `routes/macros.PERFORMANCE_AXES`) is one path; see
  `synthlab/studio/docs/keys-and-midi.md`, "Pitch bend".
- New FX units: `pump` (sidechain on the clock — plain `sidechain` keys off
  kick hits and so does nothing on Synths, which renders `parts=("synth",)`),
  `granular`, `shimmer`, `beat_repeat` and `stop` (the two time remaps:
  their own entries below). `voice_fx` renders (`synth.py`) but has **no UI on
  purpose**: it holds callables rather than JSON, is excluded from all three
  cache keys, and sits after the unison sum where layer FX already covers it.
- **Parametric EQ** (`effects.eq`, FX unit `eq`, `tests/test_eq.py`): a
  low shelf and a high shelf at fixed corners (`EQ_LOW_HZ` 120,
  `EQ_HIGH_HZ` 8000, `EQ_SLOPE` 0.7) around one swept peaking band
  (`mid_freq`/`mid_gain`/`mid_q`); gains in dB, RBJ biquads
  (`core.shelf_coeffs`, `core.peaking_coeffs`) in series through
  `_lfilter`. `eq_bands` leaves out every band at 0 dB rather than running
  a unity filter, which is what makes the unit at its defaults a
  bit-identical bypass — keep that when adding a band. Causal and LTI: it
  is live (`fxlive.FX_LIVE` "js") and commutes with `_wrap_tile`'s fold.
- **Drive modes, clipper, compressor times**
  (`tests/test_effects_dist.py`): the `drive` unit has a `mode` —
  `effects.DRIVE_MODES` soft / hard / fuzz / tube, stored as its index
  0..3 (`FX_UNITS["drive"]["choices"]`: the rack draws a menu,
  `clean_chain` rounds it, `resolve_unit` passes the name). Soft is
  `core.soft_clip`, the same call as before; hard is clip(a x) at +-1;
  fuzz is tanh(2a x + 0.5) less tanh(0.5), over the negative side's reach
  (half-rectified: -1 to +0.37); tube is soft's s + 0.2 s^2 over 1.2 (even
  harmonics). `amount` is 1-12 (its song lane spans 1-4,
  `curves.FX_LANE_RANGES`). The `clip` unit (`effects._clip`): `drive`
  dB in, a hard clip at `ceiling` dB, or with `soft` > 0 linear to
  c (1 - soft) and a tanh knee to c. `compress` exposes `attack`,
  `release` (s) and `makeup` (dB; below 0 — the default, -1 — is the
  automatic makeup, the factory's None). All memoryless or as before,
  causal, silence to silence, live where drive was ("js", same math in
  fx-worklet.js); at their defaults every stored unit renders bit for bit
  what it did, so `RENDER_REV` did not move.
- **Beat repeat** (`effects.beat_repeat`, FX unit `beat_repeat`): a
  time-remapping effect, so it is only loop-safe because it is *periodic*:
  its pattern and seeded chance rolls (`(seed, step)`, never an rng stream)
  restart every `fxrack.repeat_period(arr)` beats = gcd(bars, drum_bars)
  bars, which divides every stem's own loop, a mixed loop and a section
  cycle, and no repeat crosses a period end. That makes it commute with
  `_wrap_tile`'s tail fold exactly (`tests/studio/test_beat_repeat.py`,
  < 1e-9). Its period reads the drum bars, which a synth stem's key
  doesn't carry, so `render._chain_clock` adds it to the stem key. Don't
  seed it from anything that isn't periodic in that period.
- **Tape stop** (`effects.stop`, FX unit `stop`,
  `tests/studio/test_tape_stop.py`): the last `beats` of every period
  slow to a halt — speed and pitch fall in a straight line to zero over
  `fall` of that window, then silence to the bar line. The second time
  remap, and loop-safe for the beat repeat's reason and by the same
  means: its windows restart every `fxrack.repeat_period(arr)`, each
  reads and writes inside its own period (and only behind the
  playhead), so it commutes with `_wrap_tile`'s fold (measured 2e-12);
  `_chain_clock` puts the same two entries in the stem key. Offline and
  transport-bound for the live keys (`fxlive.FX_LIVE`, `TRANSPORT`): a
  lane holding one stays live and blocks, so a held key takes the dry
  WAV and is never stopped by where the loop is. A window reads from
  its own start (`pos` is relative), which is what makes every period
  the same samples — keep that. In a song it is *placed* by a lane on
  its `mix`: the rack rests at 0 and one section lifts it.
- **FX lanes on the clocked units** (`curves.FX_LANES`,
  `studio/fxauto.py`, `tests/studio/test_song_fx_lanes_clock.py`): the
  gate's, the sidechain's and the pump's `depth`, and the `mix` of the
  beat repeat and the tape stop. `effects._gate`, `_sidechain`,
  `_beat_repeat` and `_stop` take that parameter as a per-sample array;
  **the scalar path is the arithmetic it always was** (existing renders
  do not move) and an array that stands still gives it back bit for
  bit — the sidechain by computing its duck's *shape* once and applying
  `1 - depth * shape` (1 - d * s never rises with s, so the lowest gain
  is the deepest shape's). Never a time parameter: a lane must not move
  which steps repeat or where the tape stops. `fxrack.unit_args` is the
  one place a unit's call is built (`build_chain` and `fxauto` share
  it). A duck's first trigger, at time 0, lands on sample 1
  (`core.n_samples` never answers 0): the head of a pass is ducked a
  sample later than the same duck mid-buffer — the unit's own, not a
  lane's, and not to be "fixed" without bumping `RENDER_REV`.
- **Transitions** (`synthlab/transitions.py`): six generators —
  `riser`, `impact`, `sweep` (filtered noise alone, a
  resonant band rising or falling), `swell` (a reversed, reverberated
  cymbal-like burst ending on its loudest moment), `subdrop` (the sine
  fall alone, mono) and `bed` (room tone and dust, **loopable**, no
  fades) — deterministic, exact length, -1 dBFS peak (the bed -18:
  `transitions.PEAK`). The Song page
  renders them into the clip store as takes
  (`POST /song/section/{id}/transition`, `routes/song_record.py`); the
  clip's meta holds the settings, so the same settings re-place the same
  clip instead of rendering it again. `leads_in(kind, direction)` says
  which end sits on the bar line and `DEFAULT_DIRECTION` what a kind
  does when none is posted; a bed has no direction (both render the
  same samples and name one clip). Clip bakes, known whole before they
  play: the causal contract does not bind them.
- **MIDI files and export codecs**: `synthlab/midifile.py` writes and
  reads Standard MIDI Files with no dependency (`struct` only) — format 1
  out (tempo + time signature track, then note on/off with velocity, and
  pitch-bend with RPN 0 when a track has bends), formats 0/1/2 in, time in
  beats on both sides. `read` is written for hostile bytes: every length
  is checked, tracks and events are bounded, and anything wrong is a
  `MidiError` (`tests/test_midifile.py` cuts a real file at every byte).
  Slides are not expressed (its docstring says why). What goes in a file
  is the studio's business (`studio/midi_bridge.py`: see
  `synthlab/studio/docs/song-render-export.md`, "MIDI out and in, export
  formats"). `audio.encode(audio,
  fmt)` is the one encoder behind `audio.EXPORT_FORMATS` (wav, flac,
  mp3): WAV is `wav_bytes` byte for byte; FLAC and MP3 need `soundfile`
  (libsndfile; in the `studio` extra, wheels for win_amd64 *and*
  win_arm64, imported inside the function) and raise `CodecUnavailable`
  without it. Both start from the WAV's own 16-bit samples, so FLAC
  decodes to exactly the WAV.

## Cross-cutting checklist for a new Patch parameter

1. Field + default on `Patch` (defaults must keep old preset JSON loading).
2. Wire into `Synth.note()`/`_osc_stack()` with an array-safe gate.
3. One `Param` in `synthlab/params.py`, **appended at the end of its kind's
   run** (inserting mid-list reorders seeded breeding): `gene` range or
   choices (breeding *and* the slider), `cap=` if hand-made sounds need
   more room than the gene, `curve=True` if drawable, `mod=True` if a route
   target (then also add it to `MOD_DESTINATIONS`), and a `live=` rule if
   it is only audible when another field is on.
4. One `Ui` in `studio/paramui.py` at its place in the panel: label, hint,
   unit, step, and its live-voice class — `DEFAULT` (only the Patch default
   plays live; the honest choice until `voice-worklet.js` implements it),
   `EXACT` (needs a case in `test_live_parity_corpus.py::FIELD_CASES`),
   `IGNORED`, or a predicate.

`tests/test_params.py::test_every_patch_field_has_exactly_one_param_and_one_ui`
fails until steps 3 and 4 are done. Clamp range, UI range, curve range,
morph and XY behaviour all follow from them; there is nothing else to
register.
