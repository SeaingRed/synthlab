# Live voice: worklet, parity, live FX, WebAssembly

The AudioWorklet voice (`static/voice-worklet.js`) and everything that
holds it to the engine: the parity corpus, `/wavetables.bin`, the
worklet whitelist (`studio/worklet.py`), live layers and FX
(`fxlive.py`, `livefx.js`, `fx-worklet.js`, `eq-worklet.js`), live
CPU (pre-rendered lanes, culling, the meter) and the WebAssembly
engine (`wasm/`). The sampler's live path (`sampler-worklet.js`, and
`grain-worklet.js` for formant mode; a zone map's pick and round robin,
"A strike a voice" below) is in `sampler-and-clips.md`;
WAV-fallback keys in `keys-and-midi.md`.
Index: `README.md`.

## The live voice and the render must agree

**The live voice and the render must agree** (`test_live_voice_parity.py`,
which runs the worklet's own code under node rather than grepping it):
`voice-worklet.js` shapes decay and release by the patch's `env_curve`,
exactly as `core.adsr` does — straight lines would play a different
envelope from the loop playing the same sound. **Release**: on key-up
the amp envelope falls over `release` and the voice ends there; the
filter envelope falls over `filter_release` inside it (`fall()`, the wasm
twin's `P_FREL`). That is `core.adsr`'s rule: the engine does not
stretch a gated release over the note's buffer (the `filter_release`
corpus case fails by 2.4 dB of envelope p95 on a voice that drops the
filter over the amp release instead). The corpus renders the engine at
`gate + release`; on a loop's notes (`note_span`) the engine matches a
key on a 1-bar note within 0.05 dB of tail energy. The
worklet runs `Synth.note`'s whole chain (unison stack of osc1/osc2 with
cross-FM, ring, sub, PWM, supersaw from a numpy-pinned phase table, fm;
dc_block; ladder = SVF -> soft_clip(drive) -> SVF, the other SVF types;
velocity on the filter env; exponential key track; engine level, stereo)
and **`test_live_parity_corpus.py` measures it**: every factory + library
sound `worklet.py` admits at C2/C4/C6, plus one case per admitted field
(`FIELD_CASES` — a field can't join the whitelist without one), must hold
level +-0.5 dB, harmonics (the f0/4 grid, lines within 60 dB of the
loudest and 80 dB of the spectrum peak — below that both sides are float
floor) 1 dB RMS / 3 dB max, envelope p95 1 dB (the corpus also sweeps
every genre recipe's main and layer patches). **114 of 114 pass**,
including the noise layer + colours and the noise oscillator, pluck (the engine's block-vectorised Karplus-Strong,
streamed; its burst is the seeded uniform stream's prefix), wavetable
(mip per 64-sample block from the block's highest pitch — each voice
renders in 64-sample blocks: a control pass into per-sample arrays, then
the oscillators copy by copy, then the filter), the vowel tract, the
4-slot mod matrix per sample (filter targets per block, as core.biquad
samples them), every LFO shape incl. the stepped "noise", and synced
rates. **Random sources are statistical**: a patch with noise is checked
twice — noise stripped, to the numbers above; whole, over a 3 s note,
level +-0.5 dB, every 1/3-octave band within 40 dB of the loudest within
1.5 dB, 50 ms envelope p95 1 dB, each side averaged over four seeds (the
engine's via a shifted `core.noise` seed). White noise is statistical
because numpy's PCG64 + ziggurat isn't realtime; everything else seeded
(pluck bursts, `sample_hold`) reads its exact stream prefix from **`GET
/wavetables.bin`** (`voicetables.py`: mip stacks, uniform streams for
seeds 0..7/100..107, formant makeup, pink/brown filters, vowel table; a
u32 header length, JSON header, float32 arrays). `/patch.json` names its
content-hashed URL (`tables`, cached immutable), plus `mod_slots`,
`mod_sources` and `mod_ranges` — the worklet's route rule is
`params.slot_active` over those, pinned by `test_voice_tables.py` — and
is **`at_bpm`-resolved** (synced LFO rates arrive as Hz; a rate slider's
`mod` is ignored while its sync is on). piano.js fetches the tables and
splits the header (a worklet scope has no TextDecoder) before
`voiceReady`. **Never a top-level `function` or `var` in
voice-worklet.js**: the node harnesses `eval` it, and a hoisted
declaration made every `Math.*` a runtime lookup — 30x slower, a false
CPU alarm. CPU: `uv run python tools/bench_live_voice.py` (the shipped
files under node at 48 kHz, 16 held voices per sound, both engines): JS
median 10%, worst ~24% of the realtime budget (Slate Ray: supersaw x
unison 4 + noise), three heavy layers ~61% all live, ~24% pre-rendered
(see "Live CPU" below); the WebAssembly engine ("WebAssembly voice"
below) halves each — 5.2%, 11.5%, 28%, 11.7%; its docstring records the
latest run.
Voices read one
shared param block every block (piano.js sends the whole patch, and
every p-* range live), smoothed per block (one-pole 5 ms, cutoff in log;
gains ramp across the block).

### A re-hit revives, never stacks

**A re-hit revives, never stacks** (`test_worklet_retrigger.py`): a
noteOn on a released voice with the same id and pitch continues that
voice (phase, filter state) and attacks from its current level. A second
same-pitch voice at phase 0 added or cancelled by a random phase offset
per hit — repeated low keys collapsed to near silence or jumped ~1.5x,
heard as clipping. A different pitch on the same id still gets a new
voice and the old tail rings out. **Not for a zone map** (a multi-sample
instrument): its re-hit is its next strike — the next round-robin take,
the layer of its own velocity — so it is a new voice and the old one's
tail rings out beside it, as a re-struck sample does.

### A strike a voice

**A strike a voice** (a multi-sample instrument): every new
voice is a strike of its key (MIDI, rounded half up), counted per key in
the node from 0 since the sound's sampler buffers arrived
(`strike`; a "sampler" message resets it) — the count `sampler.Strikes`
makes in a phrase for `Synth.note(rr=)`. A noteOn's own `rr` wins (the
parity corpus passes it). A legato note re-aims the held voice and is no
strike, as `_mono_timeline` makes a legato line one render. The zone pick
itself is sampler-worklet.js's (`pick`, `sampler.pick_zones` line for
line), once per voice, on its key, `v.vel` and `v.rr`; each picked zone
is its own read (the tape read or the grain train), summed at its
weight x gain into the copy's buffers, and the pick goes back over the
node's port as `{type: "zone", key, vel, rr, zones}` (livefx.js:
`synthlab:zone`). Held to the engine by `tests/studio/test_live_sampler.py`
(the two modules under node against `sampler.render`, both modes, a
true-stereo take among mono zones, the crossfade, a zone's own span and
loop) and the corpus (`sampler_zones_*`: the low zone, a take, the
crossfade, formant mode, both engines; 0.00 dB level and harmonics,
envelope p95 0.002 dB).

### A note due later

**A note due later** (the keys arp — `keys-and-midi.md`, "The
keys arp"): a `noteOn` or `noteOff` with `at` (the context's time) later
than `currentTime` waits in the node's `due` list; at the head of each
`process` every one due before the quantum's middle is handed to
`onMessage` as if it had just arrived — so it lands within half a render
quantum of its time, whatever the main thread's timers did (Chrome moves
`currentTime` in 10 ms callbacks: a note posted "now" lands anywhere in
one). `allOff` drops what waits. Nothing else changes: a message with no
`at` is handled as it always was, and the DSP is untouched (the parity
corpus never sends one). livefx.js passes `at` through to every lane;
a pre-rendered lane's buffer (keys.js `laneOn`) starts when the message
is posted, up to the arp's 60 ms lookahead early. Held by
`tests/studio/test_keys_arp.py` (under node: a timed note is, sample for
sample, the plain note sent at the quantum nearest its time, rounding
both ways; an allOff drops it).

## The worklet whitelist (`studio/worklet.py`)

`studio/worklet.py` is
the whitelist (one Python source of truth rather than a hand-written JS
mirror of the worklet's capabilities that could silently drift from
`voice-worklet.js`):
every `Patch` field's `Ui.worklet` (`paramui.UI`, one entry per field,
required by `_ui()`) says it is fully worklet-reproduced (`EXACT`),
reproduced only for certain values (a predicate — the mod-slot gates are
generated from `params.MOD_SLOTS`; they refuse only LFO -> drive on a
non-ladder filter at drive <= 1, whose post-clip the engine switches on
for the whole note once any sample passes 1), inaudible on a single live note (`IGNORED`), or
not reproduced (`DEFAULT`: only the Patch default plays live).
`worklet.WORKLET_FIELDS`/`GATED`/`IGNORED_FIELDS` are views of that
table; `tests/test_params.py`'s completeness test fails until a new field
has a `Ui`, so a new field is WAV-fallback until someone teaches the
worklet. `GET
/patch.json` carries the verdict as `worklet_ok`/`worklet_blockers`;
`piano.js` just reads `p.worklet_ok` rather than recomputing it. What still falls back: unison over 8;
the sampler's WSOLA stretch and a drawn stretch (`sampler_stretch`); and,
on a formant-mode sampler only, a route to `pitch` / `osc1_detune` /
`vibrato_depth` (`modN_src`) or cross-FM (`fm_amount`) — its grain train
reads the pitch one root period ahead, which the voice can foresee for
its own motion (`grain-worklet.js` `ahead`: the pitch terms of `ctl()`
in closed form — **change one, change the other**) and not for those
(`sampler-and-clips.md`, "Formant mode on the live keys"). `loopDuration()` (used to decide the
fallback loop length for the shortcut sheet and elsewhere) lives on
`static/audio.js`, not piano.js — piano.js reads it off
`window.synthlab.loopDuration()`. Any new Patch field that the
AudioWorklet can't reproduce must say so in its `Ui.worklet` class
(`paramui.py`, which `worklet.py` reads) so live play falls back to
server WAV.

## Live keys sound like the loop

**Live keys sound like the loop** (`tests/studio/
test_live_fx.py`, `test_live_layers.py`): **a key sounds on the lanes
▶ hears** — `state.audible_layers(state, view, for_keys=True)`, the same
predicate `render_inputs` renders by (`StudioState.lanes`), so the two
can't drift. A key is the main lane's note played by hand: it sounds on
the edited slot, the main lane and every follower, each at its follow
octave (a follower at -1 plays the key an octave down; an edited
follower too), its gain and its FX, the sum through the master chain,
then keys.js's bus. **A plain slot with notes of its own is never
triggered by a key** — it plays its own pattern in the loop, not the
keyboard's. Mute, solo (mute wins) and "This layer" (bench views: only
the edited slot, even muted) apply exactly as to ▶; a muted or
soloed-out edited slot is silent from the keys too (`heard` false: no
node, "mod" reaches nothing). `/patch.json?view=<view>` carries the
stack (`fxlive.live_stack`: `heard`/`slot`/`octave` of the edited slot,
`layers` [patch + `slot`/`octave`/`gain`/`fx`], `fx`, `gain`,
`master`, `bpm`, `note_key`, `view`) and folds it into the one verdict:
`worklet_ok` is false if *any heard* lane or chain can't play live —
`worklet_blockers` then names `layer<N>:<field>` or `fx:<kind>` — and
keys fall back to the WAV: `/note/<pitch>.wav?view=` renders the same
lanes (`fxlive.key_voices`) at their octaves and gains, dry, one
buffer (one shared gate, so hold mode loops the whole stack); keys.js
sends `view` back on every fetch. The keyboard dock is on every page,
so the keys ask on every page, for that page's ▶ set: an
answer that differs from the last only in `view` (the last key of the
JSON) is left alone — no push to the voices, no re-warm — so a view swap
moves no sound (piano.js `loadPatch`, `patchText`;
`keys-and-midi.md`, "The keyboard dock"). Its X-Patch-Key (`voices_key`) is
the patch's own `patch_key` when the edited slot sounds alone at unity
and its own pitch, so single-layer sessions keep every key render. `fxlive.FX_LIVE`
classifies every `FX_UNITS` kind (an assert keeps it complete): "js"
(static/fx-worklet.js runs effects.py's algorithm: drive (every mode of
`effects.drive_shape`: soft hard fuzz tube) clip fold bitcrush
ringmod comb width delay chorus flanger phaser; the studio tricks
doubler rotary tape_echo telephone pitch — the vocal booth's units, their
wow a sum of sines (`effects.wander`) so the twin computes the engine's
numbers, the tape echo on a history sized to its time; and eq — the parametric
EQ's bands are `static/eq-worklet.js`, a module in the same worklet
scope with its own budget, called from fx-worklet.js, which had no room;
a band at 0 dB is skipped on both sides, so a flat EQ is an exact
bypass, and a slider drag keeps each band's state), "clock" (gate / pump /
sidechain on the transport: audio.js's `loopStart()` + a `loop-clock`
event; stopped, the clock free-runs from frame 0; sidechain ducks on the
kit only in views whose loop renders drums, the `?view=` parameter),
"native" (reverb = ConvolverNode on `effects.reverb_ir` served by
`GET /fx/ir.wav?decay&size&sr` at the context's rate, float32 — the plate
the same node on `effects.plate_ir`, `?kind=plate&decay&tone&predelay`, the
spring on `effects.spring_ir`, `?kind=spring&decay&tone&drip`; lowpass/
highpass = BiquadFilterNode, RBJ Q converted to Web Audio's dB), and
"offline" (granular, shimmer, compress, vinyl, beat_repeat, stop — the
last two also in `fxlive.TRANSPORT`: they stutter and stop on the loop's
grid, so a lane holding one is never pre-rendered as a one-shot; it
stays "live" like a clock unit's lane and blocks, and keys take the dry
WAV fallback — a held key is never slowed or silenced by where the loop
happens to be). Units reach the browser
resolved by `fxrack.resolve_unit` — the one reading of a unit's params,
which `build_chain` renders from too — plus the effects factory's own
defaults (`inspect.signature`), so no number is retyped in JS (the
drive's shape constants, `effects.FUZZ_BIAS` / `FUZZ_GAIN` / `TUBE_EVEN`,
ride on the unit from `fxlive.live_chain`). Every JS
unit is float32-exact against effects.py under node (the gate's first
edge 0.6%); a whole key (voice + layer chain + master chain) holds
1/3-octave bands within 0.003 dB. **static/livefx.js** (island 13)
builds the graph: a `synthlab-voice` node per layer (tables copied to
each), consecutive JS units share one `synthlab-fx` node (island 14,
`fx-worklet.js`), a same-shape chain updates in place (a slider drag
keeps a delay's tail), a new shape rebuilds. piano.js's `voiceNode` is
its facade: note messages fan out to every lane, noteOn/bend `freq`
(and `fromFreq`) times 2**octave (a noteOn carries each lane's own
patch; the edited slot's is piano.js's live one), "mod" reaches the
edited slot only. **Voice
caps**: a layer node holds 16 voices; past that it steals the oldest
released, else the quietest held (never the one just struck), over a
3 ms fade — and a stolen voice is never revived by a re-hit.

### The live vocal chain's units

**The live vocal chain's units** (`effects.py`'s last
section, `static/vox-worklet.js`; `tests/test_effects_vocal.py`,
`tests/studio/test_live_vox.py`): five FX units made for a mic sung into
live (`sampler-and-clips.md`, "The live vocal chain") and on every rack —
`autotune`, `harmony`, `vox_comp`, `deess`, `vgate` — all "js" in
`FX_LIVE`. Their twins are a third module in the FX worklet's scope,
`vox-worklet.js` (`synthlabVox.init` / `run` / `quiet`, called by
fx-worklet.js for a unit of `synthlabVox.kinds`; fx-worklet.js hands it
its `Hist`, `rbj`, `biq` and `panGains` as `synthlabFx`). **The pitch
shifter lives there too**: the pitch unit, the auto-tune and the
harmonizer are one shifter (`_pitch_shift_causal` + `_pitch_splices`),
which takes a ratio a grain — each grain reads at its own ratio from
its own lag, and the splice reckons the gap from both grains' — and
`free` grains (the auto-tune's unvoiced ones) start unspliced on their
place, so a run of them is the input exactly. At one ratio it is a
fixed-ratio shifter bit for bit (checked on five shifts and the shimmer).

- **The tracker** (`effects.pitch_track`): YIN on the 24 ms before each
  grain's start (`TRACK_WINDOW`, read every other sample), periods 70 Hz -
  1 kHz, the first dip of the cumulative-mean-normalised difference under
  0.2 (to its local minimum, refined on a parabola); a window under -55
  dBFS RMS or with no dip is unvoiced. A held band-limited note reads
  within 1 cent (82-880 Hz); white noise is voiced in under 5% of grains.
- **`autotune`** (key, scale, speed, humanize, mix): the nearest note of
  the key and scale (`fxrack.tune_key`: index 0 is the session's key or
  scale, "chromatic" every semitone; `render._chain_clock` puts the
  resolved key in a stem's key), held until another is 0.15 semitone
  nearer (`AUTOTUNE_HOLD`), the correction (target - sung) x (1 -
  humanize) glided at `speed` (time constant `speed` x 0.4 s; 0 snaps),
  reset to 0 after an unvoiced grain. **Measured** at speed 0: a held
  band-limited note 20-45 cents off its note at 110, 196, 300, 440 and
  700 Hz comes out within 0.33 cents of it (asserted 2); a glide 400 ->
  500 Hz through C major comes out as G4, A4 and B4 only, each 20 ms
  grain a median 4.1 cents off its note (90th percentile 4.4; up to 38 at
  the step from one to the next: the glide keeps moving through a grain);
  humanize 0.5 leaves 20 of 40 cents. **No formant keep**: the shifter
  resamples, so the formants move with the pitch — inaudible for a
  correction under a semitone or two, a smaller singer for a harmony a
  fifth up. A TD-PSOLA shifter would keep them; not built.
- **`harmony`** (key, scale, first / second: 3rd, 5th or octave up or
  down — the second may be off — each with a level and a pan): the scale
  note nearest the sung pitch (held as the auto-tune's), the note
  `HARMONY_STEPS` steps of the scale from it (a third is two steps of a
  seven-note scale, scaled to the scale's size), the voice shifted by the
  difference — so a third is major or minor as the key has it, and the
  voice keeps the singer's vibrato. Unvoiced, the last shift is kept.
  Measured: in C major a sung E4 gets G4, C4 gets E4, A4's fifth E5, each
  within 3 cents.
- **`vox_comp`** (threshold, ratio, attack, release, makeup, mix): the
  compressor's own detector (`_envelope_follower` on the louder channel)
  into a 6 dB soft knee, makeup in dB, blended at `mix`. **`compress`
  stays offline**: its automatic makeup and its place on a stem's whole
  sum are the loop's; a live twin of it would change what every stored
  rack holding one does on the keys. Measured: a phrase 20 dB over the
  last comes out 12.9 dB over at ratio 4 (the follower reads about the
  mean of |x|, some 6 dB under a peak).
- **`deess`** (freq, threshold, depth): the band over `freq` (an RBJ
  high-pass, Q 0.707; the rest its complement, so at no reduction the sum
  is the input) followed fast (1 / 60 ms), turned down 4:1 over the
  threshold, at most `depth` dB. Measured: hiss over 9 kHz 6.8 dB down at
  depth 12 (the complement's phase lets some through); a vowel under 3
  kHz within 2%.
- **`vgate`** (threshold, ratio, depth, release): a downward expander —
  under the threshold the level falls `ratio` dB a dB, at most `depth` dB
  down; opens in 1 ms. -60 dB spill between phrases comes out more than
  30 dB lower at ratio 10; a word over the threshold is untouched.

Every one is causal and the live twin the engine sample for sample
(measured <= 3.4e-7 of peak on a detuned note, a glide and a voice with
vibrato and syllables, `test_live_vox.py`; on the synth note,
`test_live_fx.py`). A culled node resets each splice and follower
(`quiet`); the tails cover the tracker's window. **Live CPU**: the
tracker is 630 lags x 529 samples per 20 ms grain at 44.1 kHz (~17 M
multiply-adds a second) per pitched unit; not measured in a browser.

### The FX2 units

**The FX2 units** (`fxrack.FX2_UNITS`; `effects.py`'s last section,
`static/fx2-worklet.js`; `tests/test_effects_fx2.py`,
`tests/studio/test_live_fx.py`): nine units on every rack — modulation,
tape, echo, spring, spectral and vocoder effects. Eight are "js" in `FX_LIVE`; their twins are a
fourth module in the FX worklet's scope, `fx2-worklet.js`
(`synthlabFx2.init` / `run` / `quiet`, called by fx-worklet.js for a unit
of `synthlabFx2.kinds`, which hands it `Hist`, `rbj`, `biq`, `panGains`
and `wander` as `synthlabFx`). The spring is "native": a ConvolverNode on
`effects.spring_ir` (GET `/fx/ir.wav?kind=spring&decay=&tone=&drip=`),
as the plate. Every one is causal; every live twin is the engine sample
for sample — **measured <= 5.3e-8 of peak** on the synth note, 20 cases
(two or more per unit, off their defaults: every mode, shape, sync, the
envelope both ways, hiss, feedback, a shift). The sound agent offers none
of them (`fxrack.FX2_UNITS`: its schema's size budget).

**Synced rates.** The auto-filter's and the tremolo's `sync`
(`fxrack.LFO_SYNC`: off — the `rate` slider in Hz — 4 bars .. 1/16,
dotted 1/8, triplets) and the ping-pong's (the delay's five) are resolved
to Hz / seconds from the session's bpm by `resolve_unit`. The LFO's phase
is the time since the buffer began (the loop's bar, a section's start)
times the rate: in the engine a function of the sample, live of the
transport's position (`pos`, as the gate's), so a held key's sweep is on
the loop's bar. A sine, a triangle and both squares start at their foot
on the bar (a saw down at its top).

| unit | what it is | measured |
|---|---|---|
| `autofilter` (mode LP / BP / HP, cutoff, q, depth, rate / sync, shape, env, sens, attack, release, mix) | `autofilter_cutoff`: `cutoff` x 2^(depth x move), move = (1 - \|env\|) x the LFO (0..1) + env x the follower (`sens` dB in, capped at 1; negative env closes as the input swells), into `core.biquad`'s swept SVF, a coefficient set every 64 samples | the defaults sweep 400 -> 3200 Hz (3 octaves) once a bar; on noise the centroid runs 255 -> 2082 Hz; at depth 0 it is the static filter to 1e-12 |
| `tremolo` (rate / sync, depth, shape, phase) | each channel x (1 - depth x the LFO); the right channel's LFO `phase` degrees on — 180 is an auto-pan | depth 0.5 dips 6.0 dB, 1 dips 48 dB (a 10 ms block's peak); at 180 the sides' envelopes correlate -0.995; depth 0 passes the input bit for bit |
| `tape` (drive, bump, tone, wow, flutter, hiss, mix) | the head wavers (the tape echo's wander for wow, a 7.3 Hz sine for flutter: a moving delay), `drive` dB into tanh and back (small signals at unity), hiss under it, a head bump (90 Hz, Q 1), the top's low-pass (6 kHz at tone 0, 18 kHz at 1), a 25 Hz low cut | a -6 dBFS 1 kHz sine's third harmonic at -34 / -24 / -16 dB under it at drive 0 / 6 / 12; +2.0 dB at 90 Hz, flat at 1 kHz, -1.4 dB at 10 kHz (defaults); wow 1 wavers a 440 Hz tone +-8 cents, flutter 1 +-6; hiss 1 is -58.5 dBFS under a -10 dBFS tone and exactly 0 in silence |
| `pingpong` (time / sync, feedback, lowcut, highcut, width, mix) | the input's sum onto the left line, left feeds right feeds left, every bounce `time` later through the loop's high-pass and low-pass; left echoes at -width, right at +width | a blip's echoes alternate left / right, each 6.0 dB (feedback 0.5) under the last |
| `spring` (decay, tone, drip, mix) | two springs (37 and 43 ms round trips), each pass through 40 stretched allpasses (a + z^-K) / (1 + a z^-K), K = sr / 7200: a falling chirp over 0-3.6 kHz, `a` -0.35 .. -0.75 with drip; a one-pole low-pass a pass (`tone`); 160 Hz - 4.8 kHz; closed form (G / (1 - G) on one long transform) | the first pass's 300 Hz arrives 9 ms after its 3 kHz at drip 0, 24 ms at 1; a 2 s decay falls 60 dB in 1.8 s |
| `formant_shift` (shift, mix) | the input followed in 32 bands (120 Hz - 8 kHz, two band-passes each); each band a Simper bell in series whose gain is the level at f / r over its own (`formant_taps`, -24 .. +18 dB), held a 64-sample block — a graphic EQ that copies the envelope across, the partials untouched | noise through three formants comes out moved -8.7 / -3.6 / +3.1 / +8.2 semitones for -8 / -4 / +4 / +8; a saw's partials stay put (> 99% of the energy on them); 0 passes the input bit for bit |
| `freqshift` (shift, feedback, mix) | Niemitalo's Hilbert pair (two chains of four second-order allpasses, 90° apart within 0.7° from 20 Hz to 0.98 x Nyquist), re x cos + im x sin: one sideband; `feedback` sends it back 10 ms later, a shift more each pass | sideband rejection 44 dB at 60 Hz, 51-74 dB from 200 Hz to 12 kHz; with feedback 0.6 each further step 4.4 dB down |
| `exciter` (freq, drive, amount) | the band over `freq` (a high-pass) through an offset tanh (odd and even harmonics), its slope at rest divided out, high-passed again and `amount` of it added | a 4 kHz sine (freq 3 kHz) gets its 2nd and 3rd ~28 dB under it; a 500 Hz one gains nothing (-82 dBFS added); amount 0 passes bit for bit |
| `vocoder_fx` (key, scale, chord, octave, bands, shift, noise, mix) | the input (both channels summed) the modulator, the carrier its own chord of PolyBLEP saws (`chord_freqs`: the chord's scale degrees over the key's root — key / scale 0 the session's, so the stem key carries them, as the auto-tune's) plus `noise` (`tick_noise`); band by band (two band-passes, 8-32 bands, 120 Hz - 8 kHz) the follower sets the carrier band, `shift` semitones off the modulator's | in C major the default chord is C3 E3 G3; > 95% of the output's energy on the chord's partials; level -0.9 dB from the input's |

**Not a twin, and why.** None: the eight worklet units are exact, the
spring is the engine's IR. **The vocoder stays a rack unit on its own
carrier**: the session's own sound as the carrier would be a second
input to a chain (a chain runs one signal), which the Voice page's
vocoder (`sampler-and-clips.md`, the Voice lab) and the live talkbox
(talk-worklet.js) already are; the rack's is the robot / choir voice in
key on any track.

**Lanes** (`curves.FX_LANES`): the auto-filter's cutoff, depth and mix,
the tremolo's depth, the tape's drive and mix, the ping-pong's feedback
and mix, the exciter's amount, every other one's mix — each the unit with
its slider moving, standing still bit for bit the scalar unit
(`test_song_fx_lanes.py`). The auto-filter is not in `fxauto.BLOCKED`:
a lead-in would move its LFO's phase, so a lane's restart may land up to
63 samples late instead.

**Cost** (NumPy + the native lfilter / swept SVF, i9 desktop, CPU
seconds per second of stereo audio): auto-filter 0.003, tremolo 0.002,
tape 0.009, ping-pong 0.002, spring 0.003, formant shifter 0.063,
frequency shifter 0.005, exciter 0.002, vocoder 0.027. The formant
shifter (32 bands x a follower and a swept bell) is the dear one: about
11 s over a three-minute track. No native kernel yet: in a song they are
small — the Psych Haze and Sixties starters (274 s and 265 s, the tape
and the auto-pan in four recipes) export in 5.7 s and 4.0 s, and neither
unit reaches the top 40 functions by own time. Live: the formant
shifter is 32 x (two biquads, a follower, two SVF steps) a sample, the
vocoder 16 x (four biquads, a follower) plus three saws; not measured in
a browser.

### The dynamics units

**The dynamics units** (`fxrack.DYNAMICS_UNITS`; `effects.py`'s last section,
`tests/test_effects_dyn.py`, `tests/studio/test_live_fx.py`): the OTT,
the limiter and the transient shaper, on every rack, "js" in `FX_LIVE`.
Their twins are in `fx2-worklet.js` beside the FX2 units' (three more
`kinds` — fx-worklet.js and vox-worklet.js have no room), so
fx-worklet.js and livefx.js only name them. **Measured <= 2.0e-7 of peak** on the synth note: seven cases
off their defaults (the OTT at depth 1 and its fastest time with every
band's gain moved, the limiter pushed 18-24 dB at three lookaheads, the
shaper both ways) and a stacked chain of six (shaper, clipper, EQ, OTT,
limiter, the envelope filter). The sound agent offers none of them
(`fxrack.DYNAMICS_UNITS`, the schema's budget again).

**The envelope filter is the auto-filter.** Its `env` already moved the
cutoff by a follower on the input, at `sens` dB, up (1) or down (-1),
over `depth` octaves, at `q`: with `env` at ±1 the LFO's share is 0 and
the unit IS an envelope filter. Its presets say so — *Envelope funk*
(opens, a band-pass) and *Envelope down* (closes) — and *Envelope Funk
Bass* runs one. No second unit with the same parameters.

| unit | what it is | measured |
|---|---|---|
| `ott` (depth, time, upward, downward, low, mid, high, output) | three bands (`ott_bands`: Linkwitz-Riley 4th-order crossovers at 120 Hz and 2.5 kHz, the low band through the upper split's all-pass, so they sum to the input through two all-passes); each band's follower (its louder channel; attack / release 20 / 250, 10 / 150, 4 / 100 ms x `time`) turned down over -24 dB at 8:1 and lifted under -30 dB at 4:1 (at most +24 dB), times `downward` / `upward`; each band x (1 - depth + depth x its gain x its own gain), `output` dB after | at depth 0 the bands' sum is the two all-passes to 1e-9 (flat to 1e-12); at depth 1 a -50 dBFS tone comes out at -31.9 and a -6 one at -16.3 (44 dB -> 15.6); upward 0 leaves the quiet tone at -50.0; a band's +6 dB is +6.0 on it, 0.0 on the others; silence in, exactly silence out |
| `limiter` (gain, ceiling, release, lookahead) | `gain` dB in; the gain each sample needs to sit at `ceiling` (`limiter_gain`), held over the `lookahead` window (a sliding minimum — live, a deque), let go over `release` (a one-pole it never rises above), averaged over the same window (a ramp that lands with the peak); the signal delayed by the lookahead | pushed 18 dB it never passes its ceiling (to 1e-9); under the ceiling it is the input exactly `lookahead` late (88 samples at the default 2 ms); after a burst the gain is back half way in 0.7 x release and all the way in 6 |
| `transient` (attack, sustain, output) | peak followers on the louder channel (`peak_follow`: up at once, down at 50 / 100 / 600 ms); the attack's dB is the 50 ms one over itself smoothed at 30 ms, the sustain's the 600 ms one over the 100 ms one smoothed at 3 ms (and 0 while a hit lands), each past a floor (4 / 1.5 dB: a 30 Hz tone's own ripple) x 0.5, at most 12 dB; gain = attack x the one + sustain x the other | on 180 Hz bursts every 250 ms (a 60 ms decay): attack 1 lifts each front's first 8 ms by +10.3 dB (peak), -1 softens it -5.2, the tail 100 ms on untouched; sustain 1 lifts the tail 150 ms on by +4.6, -1 cuts it -4.6, the front untouched (a slower 100 ms decay: +-10 dB at 300 ms); a steady 30 Hz - 2 kHz tone passes either knob's end unchanged to 1e-6 |

**The limiter's latency is the lookahead, live and in the loop alike.**
`effects.limit` (the song master's) looks ahead in its buffer — a read of
the future the live keys cannot make. The rack's limiter delays the
signal instead, by the same `lookahead` in both (0 - 10 ms, 2 ms by
default), so the twin is exact; at 0 nothing is delayed and a peak is
caught on its own sample (a step in the gain). In a layer's chain the
layer is that late against the drums (2 ms); on the master or the drum
bus everything is.

**Lanes** (`curves.FX_LANES`): the OTT's `depth`, the limiter's `gain`,
the shaper's `attack` and `sustain` — each memoryless in its parameter
(a per-sample array through the same arithmetic), standing still the
scalar unit bit for bit (`test_song_fx_lanes.py`).

**Cost** (NumPy + the native lfilter, i9 desktop, CPU seconds per second
of stereo noise): OTT 0.013 (nine biquads a channel and three followers),
limiter 0.003 (`scipy.ndimage.minimum_filter1d`, O(n) whatever the
lookahead), transient shaper 0.006 (its peak followers are a running
maximum in the log domain: no Python loop). Live: the OTT is 18 biquads
and three followers a sample, the limiter a deque push and a few
multiplies; not measured in a browser.

## Hard sync, FM4, wavetables and drift

Hard sync, the 4-operator FM shape, eight more wavetables and analog drift
(the engine's side: the root `docs/engine.md`, "Hard sync", "4-operator
FM", "Analog drift") are all `EXACT` in `paramui.py`, on both engines, and
held by the corpus (`sync_*`, `fm4_*`, `wavetable_<table>` for each new
table, `drift*`, and the four demo factory sounds at C2 / C4 / C6 — 0.00 dB
of level, harmonics within 0.02 dB, envelope p95 0.003 dB; the module's
output equals the JS voice's to the last f32 bit on every one of them).

- **Hard sync** — `syncBlock` (JS) / `syncLane` (voice.ts, scalar per lane:
  the events are branches): per osc2 copy the slave phase, a master phase
  at osc1's pitch (`F x du x M1`, before cross-FM — `po1` carries it in the
  module until osc1 overwrites it) and **one sample held back**, as
  `core.sync` runs: a step's polyBLEP reaches into the sample before it,
  which a block already handed to `post()` could not take. `sync_pitch`
  rides `M2` (`2^(oct2 + detune / 1200 + sync_pitch / 12)`; smoothed, and a
  route target per sample, `P_SM` pair 14 in the module).
- **fm4** — four operators per copy in the phase slot (`ph[o..o+3]`,
  operator 4's last two outputs at `o+4`, `o+5`); each operator's level x
  decay is a per-sample scratch array (`E1..E4`, scratch 21-24: the
  control pass writes them from `v.n`, as the 2-op fm's index), the ratios,
  feedback and algorithm per render (konst `K_FM4`). The module runs it on
  both lanes (`fm4()`: vsin, the JS order of operations).
- **The tables** need nothing in the voice: `/wavetables.bin` carries every
  `WAVETABLE_NAMES` stack and the voice finds a table by name — 3.81 MB
  (cached immutable, fetched once per version).
- **Drift** — `driftInit` draws the note's seed from the key's own pitch
  (crc32 of `"drift:<MIDI>"`, the same bits as `synth.drift_seed`), its
  cutoff offset (`coeffs()`), its oscillators' start phases (a fresh voice
  only: a revived one keeps its phase, its wander restarts with its `n`),
  and the wander's first two knots; `knots()` walks them on as `v.n`
  passes each knot (a whole number of 64-sample blocks, so a block never
  straddles two). The JS control pass computes the cents per sample; for
  the module's, JS hands the block's knot pair in the control state
  (`C_DW0`, `C_DW1`, `C_KST`).

The arena: 25 scratch arrays a set, the param block 128 f64. Cost, 16
voices at 48 kHz on an i9-14900K
(`tools/bench_live_voice.py`'s method, each demo against its twin with
the feature off), JS / WebAssembly: Sync Lead 9.4 / 8.2 % (sync off 7.3 /
5.4); FM4 Tine Keys 11.6 / 8.4 % (the same tine as a 2-op fm 7.2 / 5.2;
with feedback 0.5 11.4 / 8.1); Wavetable Scan 8.1 / 7.7 % (the basic table
7.7 / 7.2); Drifting Poly 8.3 / 6.1 % (drift 0: 7.1 / 4.8). The whole
corpus with the four demos (118 sounds): median 7.8 % JS, 5.3 %
WebAssembly — against 7.2 / 5.1 % without them, within a run's noise.

## Phase warp, custom wavetables, one voice

The phase warp, your own wavetables and one voice (the engine's side:
the root `docs/engine.md`, "Phase warp", "Your own wavetables", "One
voice") are `EXACT` in `paramui.py` on both engines, held by the corpus
(`warp_*`, `wt_custom*`, `voice_*`, and the four demo factory sounds at C2
/ C4 / C6: 0.00 dB of level and harmonics, envelope p95 <= 0.003 dB, the
module's f32 output the JS voice's to the bit) and, for a phrase, by
`tests/studio/test_live_voicing.py`.

- **The warp** — `warpBlock` (JS) / `warpLane` (voice.ts, scalar per lane,
  shapes 1-4: saw, square, sine, triangle; a warped wavetable stays in the
  JS loop, as every wavetable does): per oscillator copy, WOS (4)
  sub-samples a sample, each phase stepped by `F / (sr x 4)`, warped
  (`warpPhase`, the mode by code; its amount per sample in scratch `X1` /
  `X2` — the JS control pass writes them, with routes, or JS after the
  module's control pass, from the smoothed pair), read by the shape, the
  wrap polyBLEP'd with the step `warpEnd` says the warp leaves, into a
  64-sample ring (`v.wr` / the slot's `WR`, its write index in `ph[o+7]`);
  out the FIR over it — `core.WARP_TAPS`, 48 taps, carried in
  `/wavetables.bin`'s header (`warp_taps`) and copied into the module at
  `warpAt()`. A warped wavetable's mip: the block's peak pitch x the
  warp's steepest slope over its amounts (`warpSlope`), at 4x the rate.
  The warp codes ride konst `K_WARP` (86, 87), the tap count `K_NTAP`.
- **Your own wavetable** — `/patch.json` (and each layer's lane) names it
  as `wt_live` (`wavetables.live_url`: none when no wavetable oscillator
  reads one, or this machine doesn't hold it). livefx.js fetches it once
  per URL on the sampler's path and posts a `"wtable"` message (`ready`
  waits for it, so the keys stay on WAVs until it lands); `wavetable()`
  reads that stack while `T.wt_live` is its URL, else `"basic"` when the
  patch names a table at all (the engine's rule), else the built-in by
  name.
- **One voice** — `voice_mode` "mono": a noteOn fades every other voice
  over `MONO_FADE` (3 ms — the steal fade, `cap()`'s), and the re-hit
  revive is off; "legato": a noteOn while a voice is held re-aims it
  (`retarget`: re-keyed under the new id, `setGlide` over the patch's glide
  or the next sample) and pushes the key on `this.keys`; the newest key's
  noteOff while older ones are held re-aims it at the newest of those. The
  glide in comes from the worklet's own `lastFreq` (one voice) or the
  keys' `fromFreq` (poly), dropped under `glide_mode` "legato" when no key
  is held. A retarget moves `v.target`, so the filter's key tracking
  follows it — as the engine's line does (`Synth.note(path=)`).

The arena: 27 scratch arrays a set (two of them the warps' amounts), a
slot 10912 bytes (2720 without the warp's rings), 64 f64 of taps. Cost, 16
voices at 48 kHz on an x86-64 desktop (`tools/bench_live_voice.py
--round6`), JS / WebAssembly, off -> on (bend +, 0.6): saw 4.8 / 3.1 % ->
10.5 / 5.5 %; saw x4 unison 6.6 / 4.6 -> 29.0 / 14.1; sine 5.1 / 3.3 ->
11.5 / 8.4; sine x4 8.7 / 5.6 -> 34.3 / 25.5; wavetable 6.0 / 5.7 -> 14.9 /
15.5; wavetable x4 12.1 / 11.9 -> 46.4 / 46.4 (its loop is JS on both).

## Filters and modulation

The filter types, filter 2 and the modulation system (the root
`docs/engine.md`, "More filter types and filter 2" and "General envelopes and LFOs") are
`EXACT` in `paramui.py`, on both engines, held by the corpus's filter and
modulation cases (`f_*`, `f2_*`, `lfo*`, `env34`, `wheels`, `wheel_live`, `aftertouch_live`, `key_random`, `mod5` ..
`mod12`, `fm4_routes`; at C2 / C4 / C6 where a key matters) and the five
demo factory sounds at C2 / C4 / C6: worst level 0.0004 dB, harmonics
0.016 dB max, envelope p95 0.02 dB; the module's output equals the JS
voice's to the last f32 bit on every one.

- **The general filter chain** (`xcoef` / `xf` / `xchain` / `xpost` in
  JS, `xpost` in voice.ts): taken when filter 1 is a new type or filter 2
  plays (`v.c.x`; such a voice is never paired). `coeffs()` builds each
  filter's numbers at the block start, as `core.filt` holds them — the SVF
  stages' q's, the bell's gain, the comb's feedback, output gain and delay
  (`i + fr`) — and filter 2's cutoff from its key track, the filter
  envelope, drift. Per sample and channel: filter 1, then filter 2 after
  it or beside it (averaged), then the tract, amp and the post drive (none
  after a ladder or diode filter 1). State: 32 f64 (the SVFs, the combs'
  write positions) and four rings of 2048 f64 (a channel x a filter) — a
  voice's own arrays in JS, `xslot(i)` in the module (64.25 kB a slot,
  zeroed when the voice first runs the chain); the module is scalar here
  (one lane at a time: the types are branches), reading the numbers from
  konst `K_X` (JS `xk`) and tanh(the diode's bias) from `K_TB`, written by
  JS so both engines use the same double.
- **The modulation** lives in the JS control pass, as every route always
  has (a voice with routes runs it; the module's `control()` is
  route-free): `route(x, sv, bs)` reads each source from `this.SV` by its
  `SOURCES` number — LFO 1 (`lfoX` for its new shapes), LFO 2 / 3
  (`lfoG`: their phases `v.p2` / `v.p3` start at `lfoN_phase` plus, when
  free, `driftU(seed, 0x40000 + k)`), the filter / amp envelopes,
  velocity, envelopes 3 / 4 (`envK`, their release levels taken at
  note-off, revived like the filter envelope's), the wheels (the patch's
  `mod_wheel` / `aftertouch` smoothed, plus the live `wheel_cc` / `at_cc`
  from the "modwheel" / "aftertouch" messages, clipped to 0..1), the key
  and the note's random value (`driftInit`). `lfoX`: the drawing parsed in
  `wire()` and read as `np.interp` does; chaos the logistic map advanced
  as the phase crosses each cycle (`v.lx[k]`); smooth the hashed pair of
  the cycle, smoothstepped; LFO 2 / 3's stepped from the table's streams 1
  / 2. The new targets reach what the module reads: `WP` (wt_pos), `FA`
  (cross FM — a route switches the cross path on), `IX` (fm index), `E1..E4`
  (the fm4 levels); filter 2's cutoff / resonance and the vowel at the
  block start (`BLOCK_RATE`).

Cost, 16 voices at 48 kHz (an x86-64 desktop, the bench's harness over hand-made
variants), JS / WebAssembly: the default ladder 5.2 / 3.8 %, low 12 4.4 /
3.1; low 24 5.2 / 3.8, diode 6.6 / 5.2, phaser 5.3 / 3.9, peak 4.9 / 3.7,
comb 4.9 / 3.9; filter 2 serial (a low 12) 7.9 / 6.1, parallel comb 8.5 /
6.8; four routes from the basic sources 11.1 / 10.1, from the others (chaos,
env 3, drawn, random) 12.1 / 11.3, twelve routes 17.8 / 16.7; the demos:
Comb Pluck 8.3 / 7.2, Dual Filter Sweep 14.1 / 10.5, Chaos Pad 17.9 /
16.3, Wheel Growl 17.9 / 18.1, Drawn Gate Pad 12.7 / 11.1.

## Live CPU

**Live CPU** (`test_live_prerender.py`, `test_live_culling.py`):
three heavy layers held 16 deep took 62% of the audio thread, so **only
the edited slot runs in the worklet**. Every other lane a key sounds on
(same `audible_layers(for_keys=True)` set — nothing about *which* lanes
changed) is `mode: "buffer"` in /patch.json's `layers` and plays
`/note/{pitch}.wav?view=&layer=N` (1-based slot; 404 for the edited slot
or a lane the keys don't pre-render): that lane's note at its octave
and gain **with its own FX baked in** — `render.lane_note`, which is the
loop's layer stem for a lone note (`stem_fx` is the one FX path for
both; pinned `array_equal`) — silent tail cut, hold renders looping at
`loop_points(settle=LANE_SETTLE)` (1 s later with FX, so the room has
filled). Its key is `fxlive.lane_key` (slot, patch, octave, gain,
resolved chain), handed out as the lane's `note_key`: an edit of the
edited slot re-renders no lane. keys.js serves them from the same store
(`"<slot>:midi|hold"`), warm-up (every visible key, lanes interleaved,
4 at once), stand-ins and crossfading swaps as the key WAVs; livefx.js
routes each note (`laneOn`/`laneOff`, into its live master chain; a bend
re-strikes at the new pitch). A lane with no render at hand plays on its
voice node as a stand-in if it can (`live_ok`) — `hasLane` fetches the
missing key then, so Perform/MIDI (nothing warms) go pre-rendered from
the second press. Buffer lanes block nothing: an offline FX unit on one
is simply baked in. A lane whose chain holds a **clock** unit (gate /
pump / sidechain) stays `"live"` and blocks as before. Dev A/B flag:
`localStorage["synthlab.allLive"]="1"` or `?alllive` on the page ->
`/patch.json?live=all`, every lane in the worklet. Browser-checked: the
two modes' key levels agree within 0.1 dB.

### Silence culling

**Silence culling** (no sound change): a *released* voice whose output
sat under -120 dBFS (`QUIET`) for two quanta is freed when it is `calm`
(no tremolo, filter LFO, mod route, or a filter env that rises through
the filter as it falls); a voice node with no voices skips everything
but the param smoothing. fx-worklet.js skips its units while its input
is silent and the silence before the quantum outlasts the sum of the
units' `tail` bounds (feedback echoes to QUIET assuming a 4.0 input,
history windows, 8192 for the phaser), resuming in the first loud
quantum; its LFO clock keeps counting. `proc.cull = false` turns both
off (the tests' and bench's "before"); culled output is bit-identical
until the cull and < -100 dBFS off after it. Big wins: plucks/decaying
sounds with long releases, idle FX chains; sustained pads gain ~nothing
(their envelope lands on zero only at the release's end).

### CPU meter

**CPU meter** (`#cpu-meter`, in the top dock's status strip on every
page, so the meter reports wherever keys are played):
every worklet node times its own `process()` (`performance.now` where
the scope has it, else `Date.now`, whose 1 ms steps average out over a
report) and posts `{type:"load", ms, quanta, voices}` every 64 quanta;
livefx.js sums the nodes' shares (one audio thread), smooths, redraws
<= 4x/s: "CPU 11% · 16 voices · wasm" (the engine the voice nodes
report: `wasm`, `js`, or `js+wasm` mid-switch), `.hot` (red) over 80%.
Display only — nothing degrades under load. It measures the worklets,
not native nodes (convolvers, buffer sources).

## WebAssembly voice

**WebAssembly voice** (`tests/studio/test_voice_wasm.py`, the
parity corpus on both engines): the voice's inner loops also exist as
WebAssembly + 128-bit SIMD, about 2.1x less audio-thread CPU than the
JS DSP (`tools/bench_live_voice.py` prints both; its docstring has the
run). Sources in `synthlab/studio/wasm/` — AssemblyScript 0.28.20
(exact pin, `package-lock.json`, binaryen with it; `node_modules/` is
git-ignored): `assembly/voice.ts` (control pass, unison stack, post
chain), `assembly/vmath.ts` (f64x2 sin/tanh/exp/2^x, a few ulp from
V8's Math), `assembly/mathcheck.ts` (test-only build of those).
**Build**: `uv run python tools/build_wasm.py` (`npm ci` into
`wasm/node_modules/` if missing, then `npm run build` = `node build.mjs`:
asconfig.json, -O3, `--enable simd`, stub runtime, no imports) writes
`static/voice-dsp.wasm` (~43 KB, **committed** — running the studio
never needs node) and its sha256 to `wasm/voice-dsp.wasm.sha256`;
`--check` rebuilds to a temp file and compares. The test always checks
the served file against the recorded hash, and against a fresh build
(deterministic: same bytes) when `node_modules/` exists. Rebuild after
any edit to `wasm/assembly/*.ts`. The .ts sources are not islands (not
static/*.js); the glue in voice-worklet.js/livefx.js counts against
their budgets. **Loading**: livefx.js fetches and `WebAssembly.compile`s
the module on the main thread (an AudioWorklet scope can't fetch) and
hands the Module to each new voice node in its **`processorOptions`**
(`{wasm}`) — browser-checked: a Module posted over the node's port never
arrives in Chrome (messageerror in the worklet), processorOptions
carries it. The node instantiates it synchronously (the audio thread
may) and answers `{type: "engine"}`; a `{type: "wasm", module}` port
message (a Module or the raw bytes, compiled there) does the same and is
what the node harnesses use. **CSP**: `script-src 'self' 'wasm-unsafe-eval'` —
compile needs it; `'unsafe-eval'` stays out (test_security.py pins the
policy). **Fallback** (nothing breaks, voice by voice): no WebAssembly,
no SIMD (compile fails), a CSP without it, an uncloneable Module, a
failed instantiation, `?nowasm` on the page (the A/B flag) -> the JS
DSP; so do voices started before the module arrived, voices past the 64
state slots, and parts the module lacks: the control pass of a voice
with mod routes, the wavetable and pluck oscillators (their JS loops
write straight into the module's scratch arrays, post() still runs
there; the sampler's read and its formant grain train are JS on both
engines for the same reason). **Precision**: f64x2 — the JS voice's own float64 math in the
same operation order, so the f32 output matches the JS voice (worst
5.6e-8 of the peak over the corpus, most jobs bit-identical) and the
corpus numbers are unchanged; f32x4 would have doubled the lanes but
drifted the phase accumulators. **Lanes**: a voice's unison copies two
at a time (supersaw's seven saws per copy), its L/R channels in the
dc/filter/tract chain, and at unison 1 two voices at once (`pair()`:
neighbours in `this.voices`, block-aligned, drive not routed — voice
B's control pass writes scratch set 1), since most sounds are unison 1.
Memory: one fixed arena per node (two scratch sets, pair buffers,
constants, 64 + 1 voice-state slots of 2720 B; a voice's `ph`/`qs`/
`dc`/`s`/`fs`/`cs`/`rs` are typed views into its slot, so the JS loops
and the module share state; the slot layout is `LAYOUT` in the worklet
and the header of voice.ts). JS stays the reference: change the DSP in
voice-worklet.js first, mirror it in voice.ts, rebuild, and let the
corpus + `test_engines_agree_through_everything_a_voice_goes_through`
hold them together.
