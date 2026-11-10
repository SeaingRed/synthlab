// synthlab/studio/static/voice-worklet.js
// Live performance voice: Synth.note()'s signal chain, sample for sample,
// for every patch studio/worklet.py admits — that file is the whitelist and
// tests/studio/test_live_parity_corpus.py runs this file under node against
// the engine for each admitted sound and for one case per admitted field.
// Chain per voice (synth.py / core.py): the 4-slot mod matrix (routes and
// their spans come from /patch.json: params.MOD_RANGES / slot_active), a
// unison stack of osc1 [+ osc2 with cross-FM and ring] [+ sub] [+ noise]
// (PolyBLEP saw/square with PWM, sine, triangle, fm, supersaw, wavetable,
// Karplus-Strong pluck, white/pink/brown noise, the sampler), equal-power pan,
// / sqrt(unison), dc_block, filter (ladder = TPT SVF(q = 0.707 + 6 res) ->
// soft_clip(drive) -> SVF(0.707); lowpass/highpass/bandpass/notch = one SVF;
// none), the vowel tract (three bandpass SVFs, makeup per vowel/size), amp
// env x velocity, tremolo, post drive (non-ladder).
// The engine's fixed tables (wavetable mip stacks, the seeded uniform
// streams pluck and the stepped LFO read, the formant makeup, the pink and
// brown filters) arrive once as a "tables" message (GET /wavetables.bin);
// only the white noise is statistical — numpy's generator isn't realtime.
// Output is stereo at the engine's own level: no scaling here — the key
// bus (keys.js) is the one gain stage for worklet and WAV keys alike.
// Live control: every voice reads one shared param block (`this.T`, the
// active layer's patch, Patch field names) every block, so any control
// reaches held notes. Continuous params are smoothed per 128-sample block
// (one-pole, tau 5 ms; cutoff in the log domain) and ramp linearly across
// it, so a slider drag never zippers.
// Each voice renders in 64-sample blocks (core.biquad's coefficient block):
// a control pass (envelopes, LFOs, the mod matrix) into per-sample arrays,
// then the oscillators copy by copy — so a wavetable's mip can be picked
// from the block's highest pitch, as core.wavetable does — then the filter.
// 200 -> 300: the full engine chain (was a 4-pole ladder-ish filter that
// matched nothing the engine renders; see the parity corpus test).
// 300 -> 570: noise layer + colours, pluck, wavetable,
// formant, the mod matrix, every LFO shape; the block-structured render.
// 570 -> 585: a voice cap per layer node with stealing.
// 585 -> 600: silence culling (a released voice whose output has sat under
// QUIET for CULL samples is freed; an idle node skips its voice setup) and
// the CPU meter's per-quantum timer (`load` messages, livefx.js sums them).
// 600 -> 740: the WebAssembly engine's glue (~2.1x less CPU; see
// tools/bench_live_voice.py). livefx.js compiles static/voice-dsp.wasm
// (synthlab/studio/wasm/, AssemblyScript + SIMD) on the main thread and
// hands the Module over in processorOptions.wasm (or a "wasm" message: a
// Module or its bytes); this scope can't fetch. Once instantiated, the
// scratch arrays and each new voice's oscillator / filter state live in
// its linear memory, and per 64-sample block the route-free control pass
// (voice.ts control()), the unison stack (stack()) and dc/filter/tract/amp
// (post()) run there — f64x2, the same float64 math in the same order (its
// sin/tanh/exp/2^x within a few ulp of V8's), so the f32 output matches
// the JS voice and JS stays the reference and the fallback. The two lanes
// carry unison copies / L+R, or, at unison 1, two voices at once (pair():
// voices side by side in this.voices, block-aligned, drive not routed; the
// second's control pass in scratch set 1). The JS loops below still run
// for: no WebAssembly or SIMD, a failed instantiation, a voice started
// before the module arrived or past the NSLOT slots, a voice with mod
// routes (control pass), wavetable and pluck (their oscillator loop fills
// A/B, post() still runs in the module). `load` reports carry the engine.
// 730 -> 738: pitch bend — the patch's pitch_bend x bend_range plus the
// keyboard's wheel ("wheel" message, semitones, smoothed) and routes to
// "pitch" (amt x half its span x the source, unclipped) bend F per sample.
// 740 -> 770: the live sampler — osc1 reads the buffers sampler.live_payload
// prepares (declicked, crossfaded, chopped; a "sampler" message livefx.js
// fetches from the patch's sampler_live URL) at the live pitch; the engine's
// stereo pan law (x sqrt 2) while it sounds.
// 770 -> 750: true-stereo samples. The read (and the buffers' split) moved
// to sampler-worklet.js, a module in this scope (`synthlabSampler`, as the
// FX rack's EQ is to fx-worklet.js); what stays here is the stereo sum: a
// two-channel sample's right side joins the voice's mix as the engine's
// stereo sampler oscillator does (synth._osc_stack: level and ring per
// channel, osc2 / sub / noise onto both), each side through fx.pan's
// stereo law, and the voice then runs dc/filter/tract per channel even at
// unison 1.
// Formant mode (no lines here): the read is handed `this`, and
// grain-worklet.js — a third module in this scope — runs sampler._psola's
// grain train from it, foreseeing the pitch a root period ahead from the
// control pass's own state (its `ahead` mirrors ctl()'s pitch terms).
// 750 -> 890: hard sync, FM4 and drift, on both engines —
// hard sync (core.sync: osc2 restarted by a phase at osc1's pitch, every
// step polyBLEP'd, one sample held back as the engine's is: syncBlock),
// the 4-operator FM shape (core.fm4: fm4 in oscBlock, each operator's
// level x decay from the control pass in E1..E4) and analog drift (the
// note's seed, synth.drift_seed: its start phases, wander and cutoff
// offset — driftInit / knots / the drift terms in ctl() and coeffs()).
// 890 -> 975: the phase warp (core.warped:
// WOS sub-samples a sample, the warped phase read, the wrap polyBLEP'd, the
// minimum-phase FIR the tables carry down to the rate: warpBlock, its
// amount per sample in X1 / X2), your own wavetable (a "wtable" message:
// GET /wavetable/live/<id>.bin, the patch's wt_live) and one voice (mono:
// a note takes the voice, the last one fading as the engine's line is cut;
// legato: a key over a held one re-aims the voice, last-note priority:
// sequencer.mono_lines' rules, so the keys play what the loop renders).
// 975 -> 1170: on both engines, the new
// filter types and filter 2 (core.filt: xpost() / xf(), the comb's ring,
// coefficients in xcoef(); voice.ts xpost), and the modulation: twelve
// route slots, the new sources (LFO 2 / 3 with the general shapes —
// drawn, chaos, smooth random: lfoX() —, envelopes 3 / 4, the mod wheel
// and aftertouch with their live "modwheel" / "aftertouch" messages, the
// key, a random value per note) and targets (filter 2, wt_pos, the FM
// amounts, the fm4 levels, the vowel). A voice with routes runs the JS
// control pass: the sources live there, the module reads what
// it fills (E1..E4, IX, FA, WP).
// 1170 -> 1176: a multi-sample instrument — each new voice is
// a strike of its key (`strike`: a count per key since the sound's buffers
// arrived, the round robin sampler.Strikes counts in a phrase; a noteOn's
// own `rr` wins), and a zone map's re-hit is a new voice (its next take),
// never a revived one. The pick itself is sampler-worklet.js's
// 1176 -> 1180: a note due later — the keys arp's (arp.js)
// noteOn / noteOff with `at`, the context's time — waits in `due` for the
// block nearest it (half a quantum either side), so an arpeggio lands on
// the loop's grid whatever the main thread's timers do; allOff drops it.
// Budget: <= 1180 code lines.

const D = { // Patch defaults for anything a message left out
  osc1: "saw", osc1_level: 0.8, osc1_octave: 0, osc1_detune: 0, osc2: null, osc2_level: 0.5,
  osc2_octave: -1, osc2_detune: 7, sub_level: 0, noise_level: 0, noise_color: "white",
  filter_type: "ladder", cutoff: 2200, resonance: 0.25, filter_env: 2500, filter_attack: 0.005,
  filter_decay: 0.25, filter_sustain: 0.3, filter_release: 0.2, key_track: 0.35, attack: 0.01,
  decay: 0.18, sustain: 0.7, release: 0.25, env_curve: 2, vibrato_rate: 5, vibrato_depth: 0,
  vibrato_delay: 0.25, pwm_rate: 0.3, pwm_depth: 0, tremolo_rate: 0, tremolo_depth: 0,
  filter_lfo_rate: 0.5, filter_lfo_depth: 0, filter_lfo_shape: "sine", pitch_env: 0,
  pitch_env_decay: 0.15, fm_amount: 0, ring_mod: 0, fm_ratio: 2, fm_index: 2, fm_decay: 0,
  pluck_damping: 0.5, pluck_bright: 0.9, wt_table: "basic", wt_pos: 0, formant_amount: 0,
  formant_vowel: 0, formant_size: 1, mod_lfo_rate: 1, lfo_shape: "sine", drive: 1, unison: 1,
  unison_detune: 12, unison_spread: 0.7, mod_slots: [], mod_sources: [], mod_ranges: {},
  pitch_bend: 0, bend_range: 2, wheel_st: 0, osc2_sync: "off", sync_pitch: 0, fm4_algo: "stack",
  fm4_ratio1: 1, fm4_ratio2: 2, fm4_ratio3: 1, fm4_ratio4: 1, fm4_level1: 1, fm4_level2: 0.3,
  fm4_level3: 0, fm4_level4: 0, fm4_decay1: 0, fm4_decay2: 0, fm4_decay3: 0, fm4_decay4: 0,
  fm4_feedback: 0, drift: 0, osc1_warp: "off", osc1_warp_amt: 0, osc2_warp: "off", osc2_warp_amt: 0,
  wt_custom: "", voice_mode: "poly", glide_mode: "always", glide: 0, filter2_route: "off",
  filter2_type: "lowpass", filter2_cutoff: 1200,
  filter2_resonance: 0.2, filter2_key_track: 0, filter2_env: 0, env3_attack: 0.01, env3_decay: 0.3,
  env3_sustain: 0, env3_release: 0.3, env3_curve: 2, env4_attack: 0.5, env4_decay: 1, env4_sustain: 1,
  env4_release: 0.5, env4_curve: 2, lfo2_rate: 2, lfo2_shape: "sine", lfo2_phase: 0, lfo2_mode: "retrig",
  lfo3_rate: 0.5, lfo3_shape: "sine", lfo3_phase: 0, lfo3_mode: "retrig", lfo_draw: "", lfo2_draw: "",
  lfo3_draw: "", mod_wheel: 0, aftertouch: 0, wheel_cc: 0, at_cc: 0,
};
// smoothed params (1 = log domain); a route's amount joins when it is wired
const SMOOTH = { osc1_level: 0, osc2_level: 0, sub_level: 0, noise_level: 0, cutoff: 1,
  resonance: 0, filter_env: 0, key_track: 0, filter_lfo_depth: 0, drive: 0, ring_mod: 0,
  fm_amount: 0, tremolo_depth: 0, vibrato_depth: 0, pwm_depth: 0, osc1_detune: 0,
  osc2_detune: 0, wt_pos: 0, formant_amount: 0, formant_vowel: 0, formant_size: 1,
  pitch_bend: 0, bend_range: 0, wheel_st: 0, sync_pitch: 0, osc1_warp_amt: 0, osc2_warp_amt: 0,
  filter2_cutoff: 1, filter2_resonance: 0, filter2_key_track: 0, filter2_env: 0, mod_wheel: 0,
  aftertouch: 0, wheel_cc: 0, at_cc: 0 };
// route targets the filter reads once per block (core.biquad samples its
// cutoff/q every 64); every other target is applied per sample
const BLOCK_RATE = { cutoff: 1, resonance: 1, filter_env: 1, filter_lfo_depth: 1, filter2_cutoff: 1,
  filter2_resonance: 1, formant_vowel: 1 };
const SOURCES = { lfo: 1, fenv: 2, aenv: 3, velocity: 4, lfo2: 5, lfo3: 6, env3: 7, env4: 8, wheel: 9,
  aftertouch: 10, key: 11, random: 12 };
// the filters (core.filt): every type's code in xpost (voice.ts the
// same), its SVF q's, the comb's ring and limits, the diode's bias, the bell
const XT = { ladder: 0, lowpass: 1, highpass: 2, bandpass: 3, notch: 4, none: 5, lowpass24: 6,
  highpass24: 7, comb_plus: 8, comb_minus: 9, phaser: 10, diode: 11, peak: 12 };
const RING = 2048, COMB_MAX = 2000, COMB_FB = 0.97, DIODE_B = 0.25, TB = Math.tanh(DIODE_B), PEAK_DB = 18;
// core.lfo_ext: the logistic map, the per-note keys (synth._RANDOM_KEY,
// _LFO_FREE_KEY), an empty drawing's triangle
const CHAOS_R = 3.99, RANDOM_KEY = 0x30000, FREE_KEY = 0x40000, DRAW_DEFAULT = [[0, -1], [0.5, 1]];
const BLOCK = 64; // core.biquad's coefficient block: cutoff sampled at its start
const MAXU = 8;
const MAXV = 16; // voices per layer node; past it one is stolen (steal())
const NOISE_LEVEL = 0.3; // core.noise's scale
// Silence culling: a released voice whose output has stayed under QUIET
// (-120 dBFS) for CULL samples is freed — only when nothing can bring it
// back up (calm: no tremolo, filter LFO, routes, or a filter env that
// rises through the filter), so what it would still have played is under
// -100 dBFS (test_live_culling.py). CULL = two render quanta.
const QUIET = 1e-6, CULL = 256;
// the CPU meter: time each quantum (performance.now where the scope has
// it, else Date.now, whose 1 ms steps average out over a report) and post
// the sum every LOAD_EVERY quanta
const clock = globalThis.performance?.now ? () => globalThis.performance.now() : Date.now;
const LOAD_EVERY = 64;
// core.supersaw's start phases: np.random.default_rng(seed).random(7) for
// osc1's seeds 0..7 (the unison copy) and osc2's 100..107. Pinned against
// numpy by test_live_parity_corpus.py::test_supersaw_phase_table.
const SUPERSAW_PHASE = [
  [0.636962, 0.269787, 0.040974, 0.016528, 0.81327, 0.912756, 0.606636],
  [0.511822, 0.950464, 0.14416, 0.948649, 0.311831, 0.423326, 0.827703],
  [0.261612, 0.298491, 0.814226, 0.091916, 0.600101, 0.728561, 0.187901],
  [0.085649, 0.236811, 0.801274, 0.582162, 0.094129, 0.433127, 0.479051],
  [0.943056, 0.511328, 0.976244, 0.080836, 0.607356, 0.376487, 0.801901],
  [0.805003, 0.807941, 0.515326, 0.285801, 0.053931, 0.383369, 0.408473],
  [0.538164, 0.343271, 0.369067, 0.374497, 0.987445, 0.632756, 0.674324],
  [0.625095, 0.897214, 0.775686, 0.225207, 0.300166, 0.873553, 0.005265],
  [0.834982, 0.596554, 0.288863, 0.042952, 0.973654, 0.596472, 0.790263],
  [0.943533, 0.359421, 0.784805, 0.591278, 0.294329, 0.922726, 0.869332],
  [0.159991, 0.585944, 0.823837, 0.29726, 0.6849, 0.463884, 0.891357],
  [0.312683, 0.224318, 0.18201, 0.860179, 0.857542, 0.103562, 0.28909],
  [0.838565, 0.692148, 0.216089, 0.125412, 0.375069, 0.753889, 0.735188],
  [0.638195, 0.99146, 0.096729, 0.506704, 0.396439, 0.206016, 0.883369],
  [0.982408, 0.0404, 0.122787, 0.464265, 0.964442, 0.138799, 0.325605],
  [0.648536, 0.477689, 0.502737, 0.734784, 0.491173, 0.076533, 0.415603],
];
const SUPERSAW_DET = [0, 1, 2, 3, 4, 5, 6].map((j) => Math.pow(2, (j - 3) / 3 * 18 / 1200));
const TAU = 2 * Math.PI;
const SCRATCH = ["F", "W", "G", "L1", "L2", "LS", "LN", "M1", "M2", "DR", "IX", "FA", "RG", "WP",
  "O1", "O2", "NZ", "F1", "F2", "A", "B", "E1", "E2", "E3", "E4", "X1", "X2"]; // voice.ts indexes these in this order
// the WebAssembly engine (wasm/assembly/voice.ts): its shape and filter
// codes, voice-state slots, and each slot's byte layout
const SHAPES = { saw: 1, square: 2, sine: 3, triangle: 4, supersaw: 5, fm: 6, noise: 7, fm4: 8 };
// core.fm4: the algorithms (voice.ts reads the index), FM4_INDEX, FM4_FEEDBACK
const FM4 = { stack: 0, pairs: 1, three: 2, pair_plus: 3, additive: 4 }, FM4_IX = 6, FM4_FB = 1.5;
// core.SYNC_SHAPES: what osc2's hard sync applies to
const SYNCS = { saw: 1, square: 1, triangle: 1, sine: 1 };
// analog drift (synth.py): DRIFT_CENTS, DRIFT_CUT_OCT, the hash keys, and
// the shapes it starts at a seeded phase (_PHASED)
const DRIFT_CENTS = 10, DRIFT_CUT = 0.25, CUT_KEY = 0x10000, PHASE_KEY = 0x20000;
const PHASED = { sine: 1, saw: 1, square: 1, triangle: 1, wavetable: 1, fm: 1, fm4: 1 };
// synth.drift_seed's crc32 and drift_u's integer hash (lowbias32): the same bits
const crc32 = (s) => {
  let c = ~0;
  for (let i = 0; i < s.length; i++) {
    c ^= s.charCodeAt(i);
    for (let k = 0; k < 8; k++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
  }
  return ~c >>> 0;
};
const driftU = (seed, key) => {
  let x = (seed ^ Math.imul(key, 0x9e3779b9)) >>> 0;
  x ^= x >>> 16; x = Math.imul(x, 0x7feb352d) >>> 0; x ^= x >>> 15; x = Math.imul(x, 0x846ca68b) >>> 0; x ^= x >>> 16;
  return (x >>> 0) / 4294967296;
};
// a sync event's polyBLEP halves: x is the fraction of a sample since the step
const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const naive = (shape, t, w) => shape === "saw" ? 2 * t - 1 : shape === "square" ? (t < w ? 1 : -1)
  : shape === "triangle" ? 4 * Math.abs(t - 0.5) - 1 : Math.sin(TAU * t);
const FTYPES = { ladder: 0, lowpass: 1, highpass: 2, bandpass: 3, notch: 4, none: 5 };
const NSLOT = 64;
const LAYOUT = { ph: [0, 128], qs: [1024, 8], dc: [1088, 4], s: [1120, 8], fs: [1184, 12], cs: [1280, 168],
  wr: [2720, 2 * MAXU * 64] };
// core.WARP_MODES (voice.ts reads the code), the shapes a warp
// bends, WARP_OS and the history a warped oscillator keeps (WARP_RING)
const WARP = { bend_pos: 1, bend_neg: 2, bend_both: 3, pd: 4, mirror: 5, asym: 6, quantize: 7, pwm: 8 };
const WARPS = { sine: 1, saw: 1, square: 1, triangle: 1, wavetable: 1 };
const WOS = 4, WRING = 64, WBEND = 15, MONO_FADE = 0.003;
// core.warp_phase / warp_end / warp_slope, by mode code
const bend = (t, k) => t / (k - (k - 1) * t);
const warpPhase = (m, t, a) => {
  const k = 1 + WBEND * a;
  if (m === 1) return bend(t, k);
  if (m === 2) return k * t / (1 + (k - 1) * t);
  if (m === 3) { if (t < 0.5) return 0.5 * bend(2 * t, k); const h = 2 * t - 1; return 0.5 + 0.5 * (k * h / (1 + (k - 1) * h)); }
  if (m === 4) { const d = 0.5 - 0.49 * a; return t < d ? 0.5 * t / d : 0.5 + 0.5 * (t - d) / (1 - d); }
  if (m === 5) { const c = 1 - 0.5 * a; return t < c ? t / c : (1 - t) / Math.max(1 - c, 1e-9); }
  if (m === 6) return t < 0.5 ? 0.5 * bend(2 * t, k) : t;
  if (m === 7) { const q = 1 - a, n = 2 + Math.floor(254 * q * q * q); return Math.floor(t * n) / n; }
  return m === 8 ? Math.min(t / (1 - 0.95 * a), 1) : t;
};
const warpEnd = (m, a) => {
  if (m === 5) return a > 0 ? 0 : 1;
  if (m === 7) { const q = 1 - a, n = 2 + Math.floor(254 * q * q * q); return (n - 1) / n; }
  return 1;
};
const warpSlope = (m, a) => {
  const s = m === 1 || m === 2 || m === 3 || m === 6 ? 1 + WBEND * a : m === 4 ? 0.5 / (0.5 - 0.49 * a)
    : m === 5 ? 1 / Math.max(0.5 * a, 1e-9) : m === 8 ? 1 / (1 - 0.95 * a) : 1;
  return Math.min(Math.max(s, 1), 1 + WBEND);
};
// polyBLEP (core._polyblep) for the block loops; the method below is the
// same. A const, never a top-level `function` or `var`: the node harnesses
// eval this file, and a declaration hoisted into the caller's scope turns
// every global lookup in it (Math.*) into a runtime call — 30x slower.
const blep = (t, dt) => {
  if (t < dt) { t /= dt; return t + t - t * t - 1; }
  if (t > 1 - dt) { t = (t - 1) / dt; return t * t + t + t + 1; }
  return 0;
};

class SynthlabVoice extends AudioWorkletProcessor {
  constructor(opts) {
    super();
    this.voices = new Map();
    this.T = { ...D }; // target params: the shared block every voice reads
    this.prev = {}; this.cur = {};
    this.steal = 0;
    this.seed = 0x9e3779b9; // white noise streams: one per note, never repeating
    this.cull = true; this.ms = 0; this.q = 0; // culling on; the meter's accumulators
    this.tab = null; // the engine's tables, once they arrive
    this.wasm = null; // the WebAssembly engine, once a module arrives and instantiates
    this.dirty = true; this.routes = []; this.idx = {}; this.routed = new Set();
    this.keys = []; this.lastFreq = 0; // one voice: the keys held, newest last; the last note
    this.acc = new Float64Array(1); this.names = Object.keys(SMOOTH); this.SV = new Float64Array(13);
    for (const k of SCRATCH) this[k] = new Float64Array(BLOCK);
    this.OR = new Float64Array(BLOCK); // a stereo sampler's right channel (never the module's)
    this.port.onmessage = (e) => this.onMessage(e.data);
    if (opts?.processorOptions?.wasm) this.useWasm(opts.processorOptions.wasm);
  }

  onMessage(m) {
    // a note due later (the keys arp's `at`): held for its block (process)
    if (m.at && m.at > currentTime && (m.type === "noteOn" || m.type === "noteOff")) { (this.due ??= []).push(m); return; }
    if (m.type === "tables") { this.setTables(m.meta, m.data); return; }
    // GET /sampler/live/<id>.bin (sampler.live_payload), split by sampler-worklet.js
    if (m.type === "sampler") { this.smp = globalThis.synthlabSampler.set(m.url, m.meta, m.data, sampleRate); this.rrc = null; return; }
    // your own wavetable (wavetables.live_payload): its mip stack, read as the built-ins are
    if (m.type === "wtable") { this.wtc = { url: m.url, nm: m.meta.mips, nf: m.meta.frames, len: m.meta.len, wt: m.data }; return; }
    if (m.type === "wasm") { this.useWasm(m.module); return; }
    if (m.type === "params") { Object.assign(this.T, m.p); this.dirty = true; return; }
    if (m.type === "mod") {
      // a synced LFO's rate arrives resolved in /patch.json: its Hz slider is dead
      const sync = { mod_lfo_rate: "lfo_sync", filter_lfo_rate: "filter_lfo_sync", lfo2_rate: "lfo2_sync",
        lfo3_rate: "lfo3_sync" }[m.param];
      if (sync && (this.T[sync] ?? "off") !== "off") return;
      this.T[m.param === "level" ? "osc1_level" : m.param] = m.value; this.dirty = true;
      return;
    }
    if (m.type === "allOff") { this.voices.clear(); this.keys = []; this.due = []; return; }
    // its lane is gone (livefx.js): process() returns false and the node can be collected
    if (m.type === "dispose") { this.voices.clear(); this.dead = true; return; }
    // the bend wheel (strip, MIDI, XY), in semitones: every voice, held or
    // releasing, smoothed like any param; never part of a patch message
    if (m.type === "wheel") { this.T.wheel_st = Number(m.st) || 0; return; }
    // the mod wheel (CC 1) and aftertouch, 0..1: added to the patch's own
    // mod_wheel / aftertouch where a route reads them
    if (m.type === "modwheel") { this.T.wheel_cc = Number(m.v) || 0; return; }
    if (m.type === "aftertouch") { this.T.at_cc = Number(m.v) || 0; return; }
    if (m.type === "bend") {
      const v = this.voices.get(m.id);
      if (v && !v.rel) this.setGlide(v, m.freq, m.time);
      return;
    }
    if (m.type === "noteOff") {
      const v = this.voices.get(m.id), ks = this.keys, i = ks.findIndex((k) => k.id === m.id);
      if (i >= 0) ks.splice(i, 1);
      // legato: the newest key up while older ones are held — the voice goes back to the newest of those
      if (v && !v.rel && this.T.voice_mode === "legato" && i === ks.length && ks.length) {
        const k = ks[ks.length - 1];
        this.retarget(v, m.id, k.id, k.freq);
        return;
      }
      if (v && !v.rel) {
        const T = this.T, t = v.n / sampleRate;
        v.rel = true; v.relN = 0;
        v.relLevel = this.env(t, T.attack, T.decay, T.sustain, T.env_curve, v.a0);
        v.fRelLevel = this.env(t, T.filter_attack, T.filter_decay, T.filter_sustain, T.env_curve, v.f0);
        v.e3R = this.envK(3, t, v.e30); v.e4R = this.envK(4, t, v.e40);
      }
      return;
    }
    if (m.type !== "noteOn") return;
    if (m.p) { Object.assign(this.T, m.p); this.dirty = true; }
    // one voice (sequencer.mono_lines): legato re-aims a held voice; mono and
    // a legato line's first note take the voice, the rest fading as a stolen one
    const one = this.T.voice_mode === "mono" || this.T.voice_mode === "legato";
    const held = [...this.voices.values()].find((x) => !x.rel && !x.fadeN), last = this.lastFreq;
    this.lastFreq = m.freq;
    if (one && held && this.T.voice_mode === "legato" && m.vel > 0) {
      this.keys.push({ id: m.id, freq: m.freq });
      this.retarget(held, [...this.voices].find(([, x]) => x === held)[0], m.id, m.freq);
      return;
    }
    if (one) {
      for (const x of this.voices.values()) if (!x.fadeN) x.fade = x.fadeN = Math.max(1, Math.round(MONO_FADE * sampleRate));
      this.keys = [{ id: m.id, freq: m.freq }];
    }
    // Re-hitting a key whose tail still rings, on the same pitch: revive
    // that voice (same phase, same filter state) and restart its envelope
    // from where it is. A second voice at phase 0 beside it added or
    // cancelled by a random phase offset per hit — the level snapped up or
    // collapsed on each repeat (worst on low keys), heard as clipping.
    // A zone map's re-hit is its own strike (the next take, the layer of its
    // velocity): a new voice, as a sample re-struck rings beside its tail.
    const old = this.voices.get(m.id), T = this.T, zoned = T.osc1 === "sampler" && !!this.smpNow()?.zones;
    if (!one && !zoned && old?.rel && !old.fadeN && Math.abs(old.target - m.freq) < 1e-6 * m.freq && m.vel > 0) {
      const u = old.relN / sampleRate;
      const now = old.relLevel * this.fall(u, T.release, T.env_curve) * old.vel;
      const fNow = old.fRelLevel * this.fall(u, T.filter_release, T.env_curve);
      const e30 = old.e3R * this.fall(u, T.env3_release, T.env3_curve), e40 = old.e4R * this.fall(u, T.env4_release, T.env4_curve);
      Object.assign(old, { vel: m.vel, n: 0, rel: false, relN: 0, a0: Math.min(1, now / m.vel), f0: fNow, q: 0, e30, e40 });
      this.driftInit(old, false); // a new note to the engine: its wander starts again
      return;
    }
    // the glide in: the keys' fromFreq (one voice: the last note, at the patch's
    // glide); glide_mode "legato" only while a key is down
    let from = m.fromFreq && m.glideTime > 0 ? m.fromFreq : m.freq, gt = m.glideTime;
    if (one) { from = T.glide > 0 && last ? last : m.freq; gt = T.glide; }
    if (T.glide_mode === "legato" && !held) from = m.freq;
    const v = {
      freq: from, target: m.freq, glideN: 0, glideR: 1, vel: m.vel, n: 0, a0: 0, f0: 0,
      rel: false, relN: 0, relLevel: 0, fRelLevel: 0, fe: 0, pd: false, fade: 0, fadeN: 0, lvl: 1, q: 0,
      pv: 0, pp: 0, pt: 0, pf: 0, pm: 0, // LFO phases: vibrato, pwm, tremolo, filter, mod
      p2: 0, p3: 0, e30: 0, e40: 0, e3R: 0, e4R: 0, lx: [null, {}, {}, {}], // LFO 2/3, env 3/4, lfoX's steps
      dcInit: false, c: null, fc: null, // filter, vowel tract coefficients
      ks: [], mip: new Int32Array(2 * MAXU), sp: [], // pluck strings, wavetable mips, sampler reads
      rr: m.rr ?? this.strike(m.freq), zp: null, acc: {}, // a zone map's strike of this key, its pick
      // oscillator phases, sub phases, dc/filter/tract state, noise filters and
      // streams: in a WebAssembly slot when there is one, else plain arrays
      ...this.state(),
    };
    this.phases(v.ph);
    this.driftInit(v, true);
    let seed = (m.seed ?? this.seed++) >>> 0;
    for (let j = 0; j < v.rs.length; j++) { seed = Math.imul(seed ^ (seed >>> 15), 0x2c1b3c6d) + 0x6d2b79f5 | 0; v.rs[j] = seed || 1; }
    if (from !== m.freq) this.setGlide(v, m.freq, gt);
    // Hitting the same key again while it is still releasing used to
    // overwrite the map entry, cutting the tail dead mid-cycle. Re-key the
    // old voice instead so it finishes its release alongside the new one.
    // Only a *released* voice may be re-keyed: one still held would never
    // receive its noteOff under the new id and would hang forever.
    if (old?.rel || old?.fadeN) this.voices.set(m.id + "#" + this.steal++, old);
    this.voices.set(m.id, v);
    this.cap();
  }

  // Which strike of its key (MIDI, rounded half up) a new voice is, counted
  // from 0 since the sound's sampler buffers arrived (sampler.Strikes)
  strike(f) {
    const k = Math.floor(69 + 12 * Math.log2(Math.max(f, 1e-3) / 440) + 0.5), c = (this.rrc ??= new Map()), n = c.get(k) || 0;
    c.set(k, n + 1);
    return n;
  }

  // A layer node plays at most MAXV voices: past that the oldest released
  // one goes, else the quietest held, over a 3 ms fade (no click, and a
  // pile of long tails can't eat the audio thread).
  cap() {
    const on = [...this.voices.values()].filter((v) => !v.fadeN);
    if (on.length <= MAXV) return;
    const rel = on.filter((v) => v.rel);
    const x = rel.length ? rel.reduce((a, b) => (b.relN > a.relN ? b : a))
      : on.slice(0, -1).reduce((a, b) => (b.lvl < a.lvl ? b : a));
    x.fade = x.fadeN = Math.max(1, Math.round(0.003 * sampleRate));
  }

  // GET /wavetables.bin, split by its header (voicetables.py): the mip
  // stacks, the seeded uniform streams, the formant makeup, and the
  // pink/brown filters and vowel table as numbers.
  setTables(meta, data) {
    const arr = {};
    let o = 0;
    for (const [name, shape] of meta.arrays) {
      const n = shape.reduce((a, b) => a * b, 1);
      arr[name] = data.subarray(o, o + n); o += n;
    }
    const shape = (name) => meta.arrays.find((a) => a[0] === name)[1];
    const [nt, nm, nf, len] = shape("wt"), [, un] = shape("uniform"), [fv, fsn] = shape("formant");
    this.tab = { ...meta, ...arr, nt, nm, nf, len, un, fv, fsn,
      brownR: Math.exp(-TAU * meta.brown.corner / sampleRate), warp: Float64Array.from(meta.warp_taps || [1]) };
    this.konst();
  }

  // the sampler buffers the patch names (none yet, or another sound's: silence)
  smpNow() { const m = this.smp; return m && m.url === this.T.sampler_live && m.sources.length ? m : null; }

  // The WebAssembly engine: instantiate the Module livefx.js compiled (sync:
  // this is the audio thread, not the main one), lay out its arena and put
  // the scratch arrays in it. Any failure leaves the JS engine running.
  useWasm(mod) {
    try {
      if (!(mod instanceof WebAssembly.Module)) mod = new WebAssembly.Module(mod); // the bytes
      const ex = new WebAssembly.Instance(mod, {}).exports;
      ex.init(NSLOT);
      const buf = ex.memory.buffer; // after init(): it grows the memory once
      SCRATCH.forEach((k, i) => { this[k] = new Float64Array(buf, ex.scratch(i), BLOCK); });
      this.wasm = { ex, buf, K: new Float64Array(buf, ex.konstAt(), 128), stale: true,
        P: new Float64Array(buf, ex.parAt(), 128), C: new Float64Array(buf, ex.ctlAt(), 32),
        L: new Float32Array(buf, ex.out(0), 128), R: new Float32Array(buf, ex.out(1), 128) };
      this.wasm.K.set(SUPERSAW_DET, 0); this.wasm.K[120] = TB; // voice.ts K_TB
      this.set1 = {}; // scratch set 1: a paired voice's control pass
      SCRATCH.forEach((k, i) => { this.set1[k] = new Float64Array(buf, ex.scratch(SCRATCH.length + i), BLOCK); });
      this.konst();
    } catch (e) { this.wasm = null; }
    this.port.postMessage({ type: "engine", engine: this.wasm ? "wasm" : "js" });
  }

  konst() { // the noise filters' numbers, into the module's constants
    const K = this.wasm?.K, tb = this.tab;
    if (!K || !tb) return;
    K.set([tb.pink.direct, tb.pink.last, tb.pink.gain, ...tb.pink.poles, ...tb.pink.taps,
      tb.brownR, tb.brown.gain], 48);
    K[88] = tb.warp.length; new Float64Array(this.wasm.buf, this.wasm.ex.warpAt(), 64).set(tb.warp); // the warp's FIR
  }

  // A new voice's state: a free WebAssembly slot's views (zeroed), else arrays
  state() {
    const w = this.wasm;
    let sl = -1;
    if (w) {
      const used = new Set([...this.voices.values()].map((v) => v.w));
      for (sl = 0; sl < NSLOT && used.has(sl); sl++);
    }
    if (sl < 0 || sl >= NSLOT) {
      const st = { w: -1, rs: new Uint32Array(3 * MAXU) };
      for (const [k, [, n]] of Object.entries(LAYOUT)) st[k] = new Float64Array(n);
      return st;
    }
    const at = w.ex.slot(sl), st = { w: sl, at, rs: new Uint32Array(w.buf, at + 2624, 3 * MAXU) };
    for (const [k, [o, n]] of Object.entries(LAYOUT)) st[k] = new Float64Array(w.buf, at + o, n).fill(0);
    return st;
  }

  // Analog drift: the note's seed (synth.drift_seed, from the key's own
  // pitch), its cutoff offset, the first two knots of its wander and, for
  // a new voice, its oscillators' start phases (synth.drift_phase; a synced
  // osc2's master phase is osc1's draw).
  driftInit(v, fresh) {
    const T = this.T;
    v.dseed = crc32("drift:" + Math.floor(69 + 12 * Math.log2(Math.max(v.target, 1e-6) / 440) + 0.5));
    v.uc = 2 * driftU(v.dseed, CUT_KEY) - 1;
    v.dk = 0; v.dw0 = 2 * driftU(v.dseed, 0) - 1; v.dw1 = 0.8 * v.dw0 + 0.6 * (2 * driftU(v.dseed, 1) - 1);
    // the per-note sources from the same seed: the key's place
    // (synth.KEY_SOURCE_SPAN), a random value, where LFO 2 / 3 start
    v.key = Math.min(1, Math.max(-1, (69 + 12 * Math.log2(Math.max(v.target, 1e-6) / 440) - 60) / 60));
    v.rnd = 2 * driftU(v.dseed, RANDOM_KEY) - 1;
    if (fresh) for (const k of [2, 3]) v[`p${k}`] = (+T[`lfo${k}_phase`] || 0) + (T[`lfo${k}_mode`] === "free" ? driftU(v.dseed, FREE_KEY + k) : 0);
    if (!fresh || !(T.drift > 0)) return;
    for (let s = 0; s < 2; s++) {
      const shape = s ? T.osc2 : T.osc1, sync = s && T.osc2_sync === "on" && SYNCS[shape];
      for (let u = 0; u < MAXU && (PHASED[shape] || sync); u++) {
        const o = (s * MAXU + u) * 8;
        v.ph[o] = driftU(v.dseed, PHASE_KEY + 16 * s + u);
        if (sync) v.ph[o + 1] = driftU(v.dseed, PHASE_KEY + u);
      }
    }
  }

  // the wander's knot pair for sample v.n (synth.drift_walk, advanced as
  // the note runs; a block never straddles two: the knot is 64-aligned)
  knots(v, kn) {
    while (v.n >= (v.dk + 1) * kn) {
      v.dk++; v.dw0 = v.dw1; v.dw1 = 0.8 * v.dw1 + 0.6 * (2 * driftU(v.dseed, v.dk + 1) - 1);
    }
  }

  // legato: the voice keyed `from` re-aimed at a key — keyed by it now, gliding
  // there over the patch's glide (none: the next sample), envelopes running on
  retarget(v, from, id, freq) {
    this.voices.delete(from); this.voices.set(id, v);
    this.setGlide(v, freq, this.T.glide > 0 ? this.T.glide : 0);
  }

  // np.geomspace(start, target, g): the target lands on sample g - 1
  setGlide(v, target, time) {
    v.target = Math.max(1e-3, target);
    v.glideN = Math.max(1, Math.round(time * sampleRate) - 1);
    v.glideR = Math.pow(v.target / Math.max(1e-3, v.freq), 1 / v.glideN);
  }

  // core.adsr: linear attack, decay (and release, fall()) bent by `curve`
  // (1 = linear, higher = snappier). `from` is where the attack starts: 0
  // for a new note, the current level for a revived one, so a re-hit never
  // steps the envelope.
  env(t, a, d, s, curve, from = 0) {
    if (t < a) return a > 0 ? from + (1 - from) * (t / a) : 1;
    if (t < a + d) return 1 - (1 - s) * Math.pow((t - a) / (d || 1e-4), 1 / (curve || 1));
    return s;
  }

  fall(elapsed, span, curve) {
    if (!(span > 0)) return 0;
    const u = Math.min(1, elapsed / span);
    return 1 - Math.pow(u, 1 / (curve || 1));
  }

  // polyBLEP: the band-limited residual of a unit step (core._polyblep)
  blep(t, dt) {
    if (t < dt) { t /= dt; return t + t - t * t - 1; }
    if (t > 1 - dt) { t = (t - 1) / dt; return t * t + t + t + 1; }
    return 0;
  }

  osc(wave, t, dt, width) {
    if (wave === "sine") return Math.sin(TAU * t);
    if (wave === "triangle") return 4 * Math.abs(t - 0.5) - 1;
    if (wave === "square") {
      const u = t + (1 - width);
      return (t < width ? 1 : -1) + this.blep(t, dt) - this.blep(u - Math.floor(u), dt);
    }
    return 2 * t - 1 - this.blep(t, dt); // saw
  }

  // core.lfo: the oscillator shapes at `rate`, or "noise" = core.sample_hold,
  // seed 0's uniform stream stepped at floor(n * rate / sr)
  lfo(shape, ph, rate, n) {
    if (shape !== "noise") return this.osc(shape, ph - Math.floor(ph), rate / sampleRate, 0.5);
    const tab = this.tab;
    return tab ? tab.uniform[Math.floor(n * rate / sampleRate) % tab.un] : 0;
  }

  // core.lfo_ext: general LFO k's new shapes (and LFO 2 / 3's stepped one)
  // at its unwrapped phase `acc`. Drawn: the drawing (wire() parses it)
  // as numpy's interp reads it; chaos: the logistic map a cycle, joined by
  // lines; smooth: a hashed value a cycle, smoothstepped; noise: table
  // stream k - 1, a step a cycle. The steps' state is the voice's (v.lx[k]).
  lfoX(v, k, shape, acc) {
    const K = Math.floor(acc), fr = acc - K;
    if (shape === "drawn") {
      const [xs, ys] = this.draws[k];
      let j = 0;
      while (j < xs.length - 2 && fr >= xs[j + 1]) j++;
      return (ys[j + 1] - ys[j]) / (xs[j + 1] - xs[j]) * (fr - xs[j]) + ys[j];
    }
    if (shape === "noise") {
      const tb = this.tab, row = tb ? tb.seeds.indexOf(k - 1) : -1;
      return row < 0 ? 0 : tb.uniform[row * tb.un + K % tb.un];
    }
    const st = v.lx[k], seed = this.seeds[k];
    if (shape === "chaos") {
      if (st.K === undefined) { st.K = 0; st.a = 0.1 + 0.8 * driftU(seed, 0); st.b = CHAOS_R * st.a * (1 - st.a); }
      while (st.K < K) { st.a = st.b; st.b = CHAOS_R * st.b * (1 - st.b); st.K++; }
      return 2 * (st.a + (st.b - st.a) * fr) - 1;
    }
    if (st.S !== K) { st.S = K; st.u = 2 * driftU(seed, K) - 1; st.w = 2 * driftU(seed, K + 1) - 1; }
    return st.u + (st.w - st.u) * (fr * fr * (3 - 2 * fr)); // smooth
  }

  // envelope 3 or 4 (core.adsr, as the filter envelope) at time t, held
  envK(k, t, from) {
    const T = this.T;
    return k === 3 ? this.env(t, T.env3_attack, T.env3_decay, T.env3_sustain, T.env3_curve, from)
      : this.env(t, T.env4_attack, T.env4_decay, T.env4_sustain, T.env4_curve, from);
  }

  // LFO 2 or 3 at its unwrapped phase acc: an oscillator shape (core.osc
  // from its phase), else lfoX
  lfoG(v, k, acc) {
    const T = this.T, shape = k === 2 ? T.lfo2_shape : T.lfo3_shape;
    if (shape === "sine" || shape === "triangle" || shape === "saw" || shape === "square") {
      return this.osc(shape, acc - Math.floor(acc), (k === 2 ? T.lfo2_rate : T.lfo3_rate) / sampleRate, 0.5);
    }
    return this.lfoX(v, k, shape, acc);
  }

  // Phase state per oscillator slot (osc1, osc2) x unison copy, 8 each: [0]
  // the phase (fm: the carrier's, [1] its modulator's), or supersaw's seven
  // saws, which start where core.supersaw's seeded RNG puts them.
  phases(ph) {
    for (let s = 0; s < 2; s++) {
      if ((s ? this.T.osc2 : this.T.osc1) !== "supersaw") continue;
      for (let u = 0; u < MAXU; u++) ph.set(SUPERSAW_PHASE[s * MAXU + u], (s * MAXU + u) * 8);
    }
  }

  // One block of oscillator slot s (0 osc1, 1 osc2), unison copy u, at the
  // frequencies Fq (Hz) into out, advancing its state (increment, then
  // read: core._phase is a cumsum). A tight loop per shape: dispatching per
  // sample was most of a voice's cost.
  oscBlock(shape, v, s, u, Fq, out, c) {
    const ph = v.ph, o = (s * MAXU + u) * 8, isr = 1 / sampleRate, W = this.W;
    let p = ph[o];
    if (shape === "supersaw") { // core.supersaw: 7 saws, +-18 cents, / sqrt(7)
      out.fill(0, 0, c);
      for (let k = 0; k < 7; k++) {
        const kd = SUPERSAW_DET[k] * isr;
        let q = ph[o + k];
        for (let j = 0; j < c; j++) {
          const dj = Fq[j] * kd, x = q + dj;
          q = x - Math.floor(x);
          out[j] += 2 * q - 1 - blep(q, dj);
        }
        ph[o + k] = q;
      }
      for (let j = 0; j < c; j++) out[j] /= Math.sqrt(7);
      return;
    }
    if (shape === "saw" || shape === "square" || shape === "sine" || shape === "triangle") {
      for (let j = 0; j < c; j++) {
        const dt = Fq[j] * isr, x = p + dt;
        p = x - Math.floor(x);
        if (shape === "saw") out[j] = 2 * p - 1 - blep(p, dt);
        else if (shape === "sine") out[j] = Math.sin(TAU * p);
        else if (shape === "triangle") out[j] = 4 * Math.abs(p - 0.5) - 1;
        else { const w = W[j], y = p + (1 - w); out[j] = (p < w ? 1 : -1) + blep(p, dt) - blep(y - Math.floor(y), dt); }
      }
    } else if (shape === "fm") { // core.fm: sin(carrier + index * sin(modulator))
      const r = this.T.fm_ratio, IX = this.IX;
      let m = ph[o + 1];
      for (let j = 0; j < c; j++) {
        const dt = Fq[j] * isr, x = p + dt, y = m + dt * r;
        p = x - Math.floor(x); m = y - Math.floor(y);
        out[j] = Math.sin(TAU * p + IX[j] * Math.sin(TAU * m));
      }
      ph[o + 1] = m;
    } else if (shape === "fm4") { this.fm4(v, o, Fq, out, c); p = ph[o]; }
    else if (shape === "wavetable") {
      const mip = v.mip[s * MAXU + u], WP = this.WP;
      for (let j = 0; j < c; j++) {
        const x = p + Fq[j] * isr;
        p = x - Math.floor(x);
        out[j] = this.wavetable(mip, p, WP[j]);
      }
    } else if (shape === "pluck") {
      for (let j = 0; j < c; j++) out[j] = this.pluck(v, s, u, Fq[j]);
    } else if (shape === "noise") this.noise(v, s * MAXU + u, out, c);
    else if (shape === "sampler") globalThis.synthlabSampler.read(this.smpNow(), this.T, v, s * MAXU + u, Fq, out, this.OR, c, this);
    else out.fill(0, 0, c);
    ph[o] = p;
  }

  // core.fm4: four operators (phases ph[o..o+3], operator 4's last two
  // outputs ph[o+4], ph[o+5]); each one's level x decay is E1..E4 from the
  // control pass; modulators add FM4_IX x their output to their target's
  // phase, in core.FM4_ALGOS' order, and the carriers are averaged
  fm4(v, o, Fq, out, c) {
    const T = this.T, ph = v.ph, isr = 1 / sampleRate, { E1, E2, E3, E4 } = this;
    const r1 = +T.fm4_ratio1, r2 = +T.fm4_ratio2, r3 = +T.fm4_ratio3, r4 = +T.fm4_ratio4;
    const al = FM4[T.fm4_algo] ?? 0, beta = (+T.fm4_feedback || 0) * FM4_FB;
    let p1 = ph[o], p2 = ph[o + 1], p3 = ph[o + 2], p4 = ph[o + 3], y1 = ph[o + 4], y2 = ph[o + 5];
    for (let j = 0; j < c; j++) {
      const dt = Fq[j] * isr;
      let x = p1 + dt * r1; p1 = x - Math.floor(x); x = p2 + dt * r2; p2 = x - Math.floor(x);
      x = p3 + dt * r3; p3 = x - Math.floor(x); x = p4 + dt * r4; p4 = x - Math.floor(x);
      const s4 = Math.sin(TAU * p4 + beta * (y1 + y2) * 0.5), o4 = E4[j] * s4;
      y2 = y1; y1 = s4;
      let o3, o2;
      if (al === 0) { // 4 -> 3 -> 2 -> 1
        o3 = E3[j] * Math.sin(TAU * p3 + FM4_IX * o4); o2 = E2[j] * Math.sin(TAU * p2 + FM4_IX * o3);
        out[j] = E1[j] * Math.sin(TAU * p1 + FM4_IX * o2);
      } else if (al === 1) { // (2 -> 1) + (4 -> 3)
        o3 = E3[j] * Math.sin(TAU * p3 + FM4_IX * o4); o2 = E2[j] * Math.sin(TAU * p2);
        out[j] = (E1[j] * Math.sin(TAU * p1 + FM4_IX * o2) + o3) / 2;
      } else if (al === 2) { // 2 + 3 + 4 -> 1
        o3 = E3[j] * Math.sin(TAU * p3); o2 = E2[j] * Math.sin(TAU * p2);
        out[j] = E1[j] * Math.sin(TAU * p1 + FM4_IX * o2 + FM4_IX * o3 + FM4_IX * o4);
      } else if (al === 3) { // 2 -> 1, 3, 4
        o3 = E3[j] * Math.sin(TAU * p3); o2 = E2[j] * Math.sin(TAU * p2);
        out[j] = (E1[j] * Math.sin(TAU * p1 + FM4_IX * o2) + o3 + o4) / 3;
      } else { // 1 + 2 + 3 + 4
        o3 = E3[j] * Math.sin(TAU * p3); o2 = E2[j] * Math.sin(TAU * p2);
        out[j] = (E1[j] * Math.sin(TAU * p1) + o2 + o3 + o4) / 4;
      }
    }
    ph[o] = p1; ph[o + 1] = p2; ph[o + 2] = p3; ph[o + 3] = p4; ph[o + 4] = y1; ph[o + 5] = y2;
  }

  // core.sync, osc2 copy u: the slave phase ph[o], the master's (at osc1's
  // pitch, Fm) ph[o+1], and the sample held back ph[o+2] — each step's
  // polyBLEP reaches into the sample before it, so the output runs one
  // sample late, as the engine's does. Events (x: the fraction of a sample
  // since the step, J: its height): the saw's wrap, the square's two edges
  // (it is a saw less a saw `width` behind), and each reset.
  syncBlock(shape, v, u, Fm, Fs, out, c) {
    const ph = v.ph, o = (MAXU + u) * 8, isr = 1 / sampleRate, W = this.W;
    const saw = shape === "saw", sq = shape === "square", edgy = saw || sq;
    let t = ph[o], tm = ph[o + 1], hold = ph[o + 2];
    for (let j = 0; j < c; j++) {
      const dm = Fm[j] * isr, ds = Math.max(Fs[j] * isr, 1e-12), w = sq ? W[j] : 0.5;
      let now = 0, pre = 0, xe, J;
      tm += dm;
      if (tm >= 1) { // a reset, x of a sample ago
        tm -= 1;
        const x = clamp01(tm / Math.max(dm, 1e-12)), tp = t, tb = tp + ds * (1 - x), tbw = tb >= 1 ? tb - 1 : tb;
        const after = ds * x;
        if (edgy && tb >= 1) { // the slave wrapped first
          xe = clamp01(x + (tb - 1) / ds); J = saw ? -2 : 2; now -= J / 2 * (1 - xe) ** 2; pre += J / 2 * xe * xe;
        }
        if (saw) J = -2 * tbw;
        else if (sq) {
          const b0 = tp + 1 - w, b1 = tb + 1 - w;
          if (Math.floor(b0) < Math.floor(b1)) { xe = clamp01(x + (b1 - Math.floor(b1)) / ds); now += (1 - xe) ** 2; pre -= xe * xe; }
          J = 2 * ((1 - w) % 1 - (tbw + 1 - w) % 1) + 2 * tbw;
          if (after >= w) { xe = clamp01((after - w) / ds); now += (1 - xe) ** 2; pre -= xe * xe; }
        } else J = naive(shape, 0, w) - naive(shape, tbw, w);
        now -= J / 2 * (1 - x) ** 2; pre += J / 2 * x * x;
        t = after;
      } else {
        const tp = t, un = t + ds;
        t = un >= 1 ? un - 1 : un;
        if (edgy && un >= 1) { xe = clamp01(t / ds); J = saw ? -2 : 2; now -= J / 2 * (1 - xe) ** 2; pre += J / 2 * xe * xe; }
        if (sq) {
          const b0 = tp + 1 - w, b1 = un + 1 - w;
          if (Math.floor(b0) < Math.floor(b1)) { xe = clamp01((b1 - Math.floor(b1)) / ds); now += (1 - xe) ** 2; pre -= xe * xe; }
        }
      }
      out[j] = hold + pre;
      hold = naive(shape, t, w) + now;
    }
    ph[o] = t; ph[o + 1] = tm; ph[o + 2] = hold;
  }

  // core.warped, copy u of slot s: WOS sub-samples a sample, each phase
  // warped (amount X1 / X2, clipped 0..1) and read by the shape, the wrap
  // polyBLEP'd with the step the warp leaves there; history in v.wr (WRING
  // a copy, its write index in ph[o+7]) and out the tables' FIR (warp_taps)
  warpBlock(shape, v, s, u, Fq, out, c, wm) {
    const ph = v.ph, o = (s * MAXU + u) * 8, isr = 1 / (sampleRate * WOS), X = s ? this.X2 : this.X1;
    const { W, WP } = this, H = this.tab.warp, nh = H.length, R = v.wr, rb = (s * MAXU + u) * WRING;
    const mip = v.mip[s * MAXU + u], read = (x, w, pos) => shape === "sine" ? Math.sin(TAU * x)
      : shape === "saw" ? 2 * x - 1 : shape === "square" ? (x < w ? 1 : -1)
        : shape === "triangle" ? 4 * Math.abs(x - 0.5) - 1 : this.wavetable(mip, x, pos);
    let p = ph[o], at = ph[o + 7] | 0;
    for (let j = 0; j < c; j++) {
      const d = Fq[j] * isr, a = Math.min(1, Math.max(0, X[j])), w = W[j], pos = WP[j];
      for (let q = 0; q < WOS; q++) {
        const x = p + d;
        p = x - Math.floor(x);
        let y = read(warpPhase(wm, p, a), w, pos);
        if (p < d || p > 1 - d) y += (read(0, w, pos) - read(warpEnd(wm, a), w, pos)) / 2 * blep(p, d);
        at = (at + 1) & (WRING - 1); R[rb + at] = y;
      }
      let acc = 0;
      for (let k = 0; k < nh; k++) acc += H[k] * R[rb + ((at - k) & (WRING - 1))];
      out[j] = acc;
    }
    ph[o] = p; ph[o + 7] = at;
  }

  // core.wavetable: bilinear in phase and frame position on the mip `m`, of
  // the patch's own table once it is here (wt_custom; one that isn't: "basic")
  wavetable(m, t, pos) {
    const c = this.wtc, own = c && this.T.wt_custom && c.url === this.T.wt_live, tb = own ? c : this.tab;
    if (!tb) return 0;
    const ti = own ? 0 : Math.max(0, tb.tables.indexOf(this.T.wt_custom ? "basic" : this.T.wt_table));
    const p = Math.min(1, Math.max(0, pos)) * (tb.nf - 1), f0 = Math.floor(p), f1 = Math.min(f0 + 1, tb.nf - 1);
    const x = t * tb.len, i0 = Math.floor(x) % tb.len, i1 = (i0 + 1) % tb.len, fr = x - Math.floor(x);
    const base = (ti * tb.nm + m) * tb.nf, w = tb.wt, L = tb.len;
    const a = w[(base + f0) * L + i0] * (1 - fr) + w[(base + f0) * L + i1] * fr;
    const b = w[(base + f1) * L + i0] * (1 - fr) + w[(base + f1) * L + i1] * fr;
    return a + (b - a) * (p - f0);
  }

  // core._wt_mip_index: the brightest harmonic cap under Nyquist at the
  // block's highest pitch (the first c of Fq); warped (mode wm, amounts X):
  // at WOS x the rate, that many times darker as the warp's steepest slope
  mipOf(Fq, c, wm = 0, X = null) {
    let f = 0, a = 0;
    for (let j = 0; j < c; j++) { f = Math.max(f, Math.abs(Fq[j])); if (wm) a = Math.max(a, X[j]); }
    const allowed = sampleRate * (wm ? WOS : 1) * 0.5 / Math.max(wm ? f * warpSlope(wm, Math.min(1, Math.max(0, a))) : f, 1e-6);
    let i = 0;
    for (const cap of this.tab?.mips || []) if (cap > allowed) i++;
    return Math.min(i, (this.tab?.nm || 1) - 1);
  }

  // core.pluck, streamed: the burst is the seeded stream's prefix, and the
  // block-vectorised string runs one period per pass, so each sample reads
  // the previous pass's buffer (the wrap sample reads its first value).
  pluck(v, s, u, f) {
    const j = s * MAXU + u;
    let k = v.ks[j];
    if (!k) k = v.ks[j] = this.pluckInit(s, u, f);
    const buf = k.buf, i = k.pos, frac = k.frac;
    if (i === 0 && !k.first) k.wrap = buf[0];
    const y = buf[i] * (1 - frac) + (i < buf.length - 1 ? buf[i + 1] : k.wrap) * frac;
    buf[i] = k.loss * ((1 - k.d) * y + k.d * k.yprev);
    k.yprev = y;
    if (i + 1 === buf.length) { k.pos = 0; k.first = false; } else k.pos = i + 1;
    return y * k.scale;
  }

  pluckInit(s, u, f) {
    const T = this.T, sr = sampleRate, tb = this.tab;
    const period = sr / Math.max(20, Math.min(f, sr / 4)), L = Math.floor(period), frac = period - L;
    const seed = tb ? tb.seeds.indexOf(s ? 100 + u : u) : -1;
    const b = new Float64Array(L + 1);
    for (let i = 0; i <= L; i++) b[i] = seed >= 0 && i < tb.un ? tb.uniform[seed * tb.un + i] : Math.random() * 2 - 1;
    const a = 1 - 0.97 * (1 - T.pluck_bright);
    for (let i = 1; i <= L; i++) b[i] = a * b[i] + (1 - a) * b[i - 1];
    let mean = 0;
    for (let i = 0; i <= L; i++) mean += b[i];
    mean /= L + 1;
    let peak = 0;
    for (let i = 0; i <= L; i++) b[i] -= mean;
    for (let i = 0; i < L; i++) peak = Math.max(peak, Math.abs(b[i] * (1 - frac) + b[i + 1] * frac));
    return { buf: b.slice(0, L), pos: 0, first: true, wrap: b[L], yprev: b[L - 1], frac,
      d: 0.5 * T.pluck_damping, loss: 0.996 - 0.03 * T.pluck_damping, scale: 0.8 / (peak || 1) };
  }

  // core.noise, n samples of stream j into out: white is statistical (a
  // sum of four uniforms at unit variance), pink/brown run
  // core.colour_noise's filters on it
  noise(v, j, out, n) {
    const col = this.T.noise_color, tb = this.tab, cs = v.cs, o = j * 7, K = Math.sqrt(3) / 65536;
    const kind = !tb ? 0 : col === "pink" ? 1 : col === "brown" ? 2 : 0;
    let x = v.rs[j];
    for (let i = 0; i < n; i++) {
      x ^= x << 13; x ^= x >>> 17; x ^= x << 5; const a = x >>> 0;
      x ^= x << 13; x ^= x >>> 17; x ^= x << 5; const b = x >>> 0;
      const w = ((a & 0xffff) + (a >>> 16) + (b & 0xffff) + (b >>> 16) - 131070) * K;
      if (kind === 0) { out[i] = w * NOISE_LEVEL; continue; }
      if (kind === 2) { cs[o] = tb.brownR * cs[o] + tb.brown.gain * w; out[i] = cs[o] * NOISE_LEVEL; continue; }
      const pk = tb.pink;
      let y = pk.direct * w + pk.last * cs[o + 6];
      for (let k = 0; k < 6; k++) { cs[o + k] = pk.poles[k] * cs[o + k] + pk.taps[k] * w; y += cs[o + k]; }
      cs[o + 6] = w;
      out[i] = y * pk.gain * NOISE_LEVEL;
    }
    v.rs[j] = x;
  }

  // one block of smoothing: prev = where the block starts, cur = where it ends
  smoothStep(n) {
    const k = 1 - Math.exp(-n / (0.005 * sampleRate));
    for (const name of this.names) {
      const tgt = Number(this.T[name] ?? D[name]) || 0, c = this.cur[name];
      this.prev[name] = c ?? tgt;
      if (c == null) this.cur[name] = tgt;
      else if (SMOOTH[name] && c > 0 && tgt > 0) this.cur[name] = Math.exp(Math.log(c) + (Math.log(tgt) - Math.log(c)) * k);
      else this.cur[name] = c + (tgt - c) * k;
    }
  }

  at(name, x) { // the smoothed value a fraction x through this block
    const a = this.prev[name], b = this.cur[name];
    return SMOOTH[name] && a > 0 ? a * Math.pow(b / a, x) : a + (b - a) * x;
  }

  // The mod matrix: params.slot_active's rule over the slots, sources and
  // spans /patch.json sends (MOD_SLOTS, MOD_SOURCES, MOD_RANGES).
  wire() {
    const T = this.T, R = [], idx = {};
    for (const s of T.mod_slots || []) {
      const src = T[`${s}_src`], dst = T[`${s}_dst`], amt = Number(T[`${s}_amt`]), span = T.mod_ranges?.[dst];
      if (src === "none" || !(T.mod_sources || []).includes(src) || !span || !(Math.abs(amt) >= 1e-3)) continue;
      // "pitch" is no param: its routes sum amt x half its span x the source
      const pitch = dst === "pitch";
      SMOOTH[`${s}_amt`] ??= 0; if (!pitch) SMOOTH[dst] ??= 0;
      const first = !(dst in idx);
      if (first) idx[dst] = Object.keys(idx).length;
      R.push({ src: SOURCES[src], dst, amt: `${s}_amt`, lo: span[0], hi: span[1], pitch,
        span: (span[1] - span[0]) / (pitch ? 2 : 1), block: !!BLOCK_RATE[dst], i: idx[dst], first,
        log: !!SMOOTH[dst], sd: null, sa: null });
    }
    this.routes = R; this.idx = idx; this.routed = new Set(Object.keys(idx));
    this.acc = new Float64Array(Math.max(1, R.length)); this.names = Object.keys(SMOOTH);
    this.needLfo = R.some((r) => r.src === 1); this.needFe = R.some((r) => r.src === 2);
    this.need = new Set(R.map((r) => r.src)); // which sources to compute
    // the drawn shapes (core.draw_points: the drawing as one periodic line)
    this.draws = ["", T.lfo_draw, T.lfo2_draw, T.lfo3_draw].map((s) => {
      const pts = new Map();
      for (const part of String(s || "").split(",")) {
        const [x, y] = part.split(":").map(Number);
        if (Number.isFinite(x) && Number.isFinite(y) && x >= 0 && x < 1) pts.set(x, Math.min(1, Math.max(-1, y)));
      }
      const p = pts.size ? [...pts].sort((a, b) => a[0] - b[0]).slice(0, 32) : DRAW_DEFAULT, m = p.length;
      return [[p[m - 1][0] - 1, ...p.map((q) => q[0]), p[0][0] + 1], [p[m - 1][1], ...p.map((q) => q[1]), p[0][1]]];
    });
    this.seeds ??= [0, 1, 2, 3].map((k) => crc32("lfo" + k));
    this.dirty = false;
  }

  // Route every wired slot at this sample into this.acc (one value per
  // target, this.idx): each adds amt * span * source to the value so far,
  // clipped to the span widened to the patch's own value
  // (synth._mod_routes). Filter targets only at a block start. "pitch"
  // starts from 0 semitones and is never clipped. `sv`: each source's
  // value at this sample, by its SOURCES number.
  route(x, sv, bs) {
    const acc = this.acc;
    for (const r of this.routes) {
      if (r.block && !bs) continue;
      const src = sv[r.src];
      const b0 = r.pitch ? 0 : r.log ? this.at(r.dst, x) : r.sd[0] + r.sd[1] * x;
      const y = (r.first ? b0 : acc[r.i]) + (r.sa[0] + r.sa[1] * x) * r.span * src;
      acc[r.i] = r.pitch ? y : Math.min(Math.max(r.hi, b0), Math.max(Math.min(r.lo, b0), y));
    }
  }

  process(_inputs, outputs) {
    const t0 = clock(), L = outputs[0][0], R = outputs[0][1] || null, N = L.length;
    L.fill(0); R?.fill(0);
    if (this.due?.length) { // what is due by this block's middle starts at its head
      const mid = currentTime + N / (2 * sampleRate), due = this.due.filter((m) => m.at < mid);
      this.due = this.due.filter((m) => m.at >= mid);
      for (const m of due) this.onMessage({ ...m, at: 0 });
    }
    if (this.dirty) this.wire();
    this.smoothStep(N); // even idle: the next note starts from settled params
    const w = this.wasm;
    if (this.voices.size || !this.cull) {
      // the WebAssembly engine sums into its own f32 output (as the JS
      // voices do into L/R), copied out once per quantum
      if (w) { w.L.fill(0, 0, N); w.R.fill(0, 0, N); }
      this.render(w ? w.L : L, w ? w.R : R, N);
      if (w) { L.set(w.L.subarray(0, N)); R?.set(w.R.subarray(0, N)); }
    }
    this.ms += clock() - t0;
    if (++this.q >= LOAD_EVERY) {
      this.port.postMessage({ type: "load", ms: this.ms, quanta: this.q, voices: this.voices.size,
        engine: w ? "wasm" : "js" });
      this.ms = 0; this.q = 0;
    }
    return !this.dead;
  }

  render(L, R, N) {
    // every smoothed param as [start, delta] across this block, shared by all voices
    const S = (this.S ??= {}), P = this.prev, C = this.cur, rt = this.routed;
    for (const name of this.names) { const s = (S[name] ??= [0, 0]); s[0] = P[name]; s[1] = C[name] - P[name]; }
    for (const r of this.routes) { r.sd = S[r.dst]; r.sa = S[r.amt]; }
    const on = (name) => C[name] > 0 || P[name] > 0 || rt.has(name);
    // the bend in semitones across this block (patch bend x range + wheel)
    const b0 = P.pitch_bend * P.bend_range + P.wheel_st;
    this.bendS = [b0, C.pitch_bend * C.bend_range + C.wheel_st - b0];
    this.flags = { sub: on("sub_level"), nz: on("noise_level"),
      fmt: !!this.tab && (C.formant_amount > 0 || P.formant_amount > 0) };
    const T = this.T, U = Math.max(1, Math.min(MAXU, Math.round(T.unison) || 1));
    // per-block constants: unison detune factors and equal-power pan (fx.pan)
    const det = [], pl = [], pr = [];
    // a sounding sampler makes the engine's raw stereo: fx.pan's stereo law, x sqrt 2
    const smp = T.osc1 === "sampler" ? this.smpNow() : null, g = smp ? Math.SQRT2 : 1;
    for (let u = 0; u < U; u++) {
      const off = U === 1 ? 0 : (u - (U - 1) / 2) / ((U - 1) / 2);
      det.push(Math.pow(2, off * T.unison_detune / 1200));
      const ang = (Math.min(1, Math.max(-1, off * T.unison_spread)) + 1) * Math.PI / 4;
      pl.push(Math.cos(ang) / Math.sqrt(U) * g); pr.push(Math.sin(ang) / Math.sqrt(U) * g);
    }
    // smp2: a two-channel sample, whose sides differ even at unison 1
    const smp2 = !!smp?.stereo, ft = T.filter_type || "ladder";
    const k = { U, det, pl, pr, smp2, stereo: U > 1 || smp2, sync: !!T.osc2 && T.osc2_sync === "on" && !!SYNCS[T.osc2] };
    // each oscillator's warp code (synth.warp_of: a shape it bends; osc2 not synced)
    k.w1 = this.tab && WARPS[T.osc1] ? WARP[T.osc1_warp] || 0 : 0;
    k.w2 = this.tab && T.osc2 && WARPS[T.osc2] && !k.sync ? WARP[T.osc2_warp] || 0 : 0;
    this.flags.warp = !!(k.w1 || k.w2);
    const w = this.wasm, has2 = !!T.osc2, sh1 = SHAPES[T.osc1], sh2 = has2 ? SHAPES[T.osc2] : 0;
    if (w) { // the module's per-render inputs: unison detune/pan, the stack's shapes and switches
      w.K.set(det, 8); w.K.set(pl, 16); w.K.set(pr, 24); w.stale = true;
      const tb = this.tab, col = T.noise_color, sr = S.ring_mod, sf = S.fm_amount;
      w.shapes = !!sh1 && sh2 !== undefined; w.sh1 = sh1; w.sh2 = sh2;
      w.nk = !tb ? 0 : col === "pink" ? 1 : col === "brown" ? 2 : 0;
      w.sw = has2 | (has2 && (sr[0] > 0 || sr[1] !== 0)) << 1 | (has2 && (sf[0] > 0 || sf[1] !== 0 || this.routed.has("fm_amount"))) << 2 |
        this.flags.sub << 3 | this.flags.nz << 4 | k.sync << 5;
      // fm4's ratios, feedback (radians) and algorithm (voice.ts K_FM4)
      w.K.set([+T.fm4_ratio1, +T.fm4_ratio2, +T.fm4_ratio3, +T.fm4_ratio4,
        (+T.fm4_feedback || 0) * FM4_FB, FM4[T.fm4_algo] ?? 0, k.w1, k.w2], 80); // + the warps (voice.ts K_WARP)
    }
    // the general chain (a newer type, filter 2) runs xpost, never paired
    const f2 = (T.filter2_route === "serial" || T.filter2_route === "parallel") && (T.filter2_type || "lowpass") !== "none";
    k.x = f2 || !(ft in FTYPES); k.f2 = f2;
    // unison 1, drive not routed, shapes and filter in the module: two voices share the f64x2 lanes (pair())
    const pairs = w && U === 1 && w.shapes && !k.x && !this.routed.has("drive");
    // nothing can raise a released voice's level again (see QUIET): a filter
    // envelope can only close a lowpass as it falls
    const low = (t, e) => !e || (e > 0 && ["ladder", "lowpass", "none", "lowpass24", "diode"].includes(t));
    const calm = this.cull && !(T.tremolo_depth > 0) && !(T.filter_lfo_depth > 0) && !this.routes.length &&
      low(ft, T.filter_env) && (!f2 || low(T.filter2_type, T.filter2_env));
    const list = [...this.voices];
    for (let i = 0; i < list.length; i++) {
      const [id, v] = list[i], nx = list[i + 1];
      if (pairs && v.w >= 0 && nx?.[1].w >= 0 && nx[1].n % BLOCK === v.n % BLOCK) {
        this.pair(id, v, nx[0], nx[1], k, L, R, N);
        if (calm && nx[1].q >= CULL) this.voices.delete(nx[0]);
        i++;
      } else this.solo(id, v, k, L, R, 0, N);
      if (calm && v.q >= CULL) this.voices.delete(id);
    }
  }

  // one voice from sample i of the quantum, block by block
  solo(id, v, k, L, R, i, N) {
    while (i < N) {
      const c = Math.min(N - i, BLOCK - (v.n % BLOCK)), n = this.ctl(v, i, c, N, 0);
      this.dsp(v, k, L, R, i, n, N, 0);
      if (n < c) { this.voices.delete(id); return; }
      i += c;
    }
  }

  // two unison-1 voices on the module's two lanes (b's control pass in
  // scratch set 1); a block where either ends early runs them one by one,
  // and the survivor finishes the quantum alone
  pair(ia, a, ib, b, k, L, R, N) {
    const w = this.wasm, { ex, K } = w, r = Math.exp(-TAU * 12 / sampleRate);
    for (let i = 0; i < N;) {
      const c = Math.min(N - i, BLOCK - (a.n % BLOCK)), na = this.ctl(a, i, c, N, 0), nb = this.ctl(b, i, c, N, 1);
      if (na < c || nb < c) {
        this.dsp(a, k, L, R, i, na, N, 0); this.dsp(b, k, L, R, i, nb, N, 1);
        if (na < c) this.voices.delete(ia);
        if (nb < c) this.voices.delete(ib);
        if (na === c) this.solo(ia, a, k, L, R, i + c, N);
        if (nb === c) this.solo(ib, b, k, L, R, i + c, N);
        return;
      }
      const fc = this.flags.fmt ? a.fc : null, [faa, fad] = this.S.formant_amount;
      ex.stack(a.at, b.at, 0, 1, true, 1, w.sh1, w.sh2, w.sw, c, sampleRate, this.T.fm_ratio, w.nk);
      this.coef(K, 0, a.c); this.coef(K, 1, b.c);
      if (fc) { fc.bands.forEach((x, j) => K.set(x, 32 + 5 * j)); K[47] = fc.makeup; }
      const pk = ex.post(a.at, b.at, 0, 1, 2, i, c, N, FTYPES[a.c.type], this.routed.has("drive") | a.dcInit << 1 |
        b.dcInit << 2 | a.pd << 3 | b.pd << 4 | !!fc << 5, a.c.d, a.c.td, faa, fad, r);
      const pd = ex.lastPd(), pk1 = ex.lastPk1();
      a.dcInit = b.dcInit = true; a.pd = !!(pd & 1); b.pd = !!(pd & 2);
      if (a.rel) a.q = pk < QUIET ? a.q + c : 0;
      if (b.rel) b.q = pk1 < QUIET ? b.q + c : 0;
      i += c;
    }
  }

  coef(K, l, cf) { // lane l's filter coefficients (voice.ts K_CO)
    const [kk, a1, a2, a3] = cf.s1, [, b1, b2, b3] = cf.s2;
    K[66 + l] = a1; K[68 + l] = a2; K[70 + l] = a3; K[72 + l] = b1; K[74 + l] = b2; K[76 + l] = b3; K[78 + l] = kk;
  }

  // filter coefficients at the start of each 64-sample block, as core.biquad
  coeffs(v, fe, x, lfoF) {
    const T = this.T, sr = sampleRate, acc = this.acc, I = this.idx;
    const val = (name) => (name in I ? acc[I[name]] : this.at(name, x)); // routed, or as set
    const cut = val("cutoff"), fenv = val("filter_env"), depth = val("filter_lfo_depth"), res = val("resonance");
    let fc = cut * Math.pow(v.target / 261.63, this.at("key_track", x)) + fenv * fe * v.vel;
    if (depth > 0) fc += lfoF * depth;
    if (T.drift > 0) fc *= Math.pow(2, T.drift * DRIFT_CUT * v.uc); // this note's filter, a little off
    fc = Math.min(sr * 0.49, Math.max(20, fc));
    const g = Math.tan(Math.PI * fc / sr), d = this.at("drive", x);
    const st = (q) => {
      const kk = 1 / Math.max(q, 0.05), a1 = 1 / (1 + g * (g + kk));
      return [kk, a1, g * a1, g * g * a1];
    };
    const cf = { s1: st(0.707 + res * 6), s2: st(0.707), d, td: d > 0 ? Math.tanh(d) : 0,
      type: T.filter_type || "ladder" };
    // a newer type or filter 2 — the general chain (xpost), filter
    // 2's cutoff following the key and the envelope as filter 1's does
    const r2 = T.filter2_route, f2 = (r2 === "serial" || r2 === "parallel") && (T.filter2_type || "lowpass") !== "none";
    if (f2 || !(cf.type in FTYPES)) {
      cf.x = true; cf.f1 = cf.type === "none" ? null : this.xcoef(cf.type, fc, res); cf.f2 = null;
      if (f2) {
        let c2 = val("filter2_cutoff") * Math.pow(v.target / 261.63, this.at("filter2_key_track", x)) + this.at("filter2_env", x) * fe * v.vel;
        if (T.drift > 0) c2 *= Math.pow(2, T.drift * DRIFT_CUT * v.uc);
        cf.f2 = this.xcoef(T.filter2_type, Math.min(sr * 0.49, Math.max(20, c2)), val("filter2_resonance"));
        cf.route = r2 === "serial" ? 1 : 2;
      }
    }
    return cf;
  }

  // One filter of the general chain at cutoff fc (core.filt): its type's
  // code, the SVF coefficients of its stages (a, b), the bell's gain or the
  // comb's feedback g, the comb's output gain s and its delay i + fr
  xcoef(type, fc, res) {
    const sr = sampleRate, g = Math.tan(Math.PI * fc / sr), t = XT[type] ?? 1;
    const st = (q) => {
      const kk = 1 / Math.max(q, 0.05), a1 = 1 / (1 + g * (g + kk));
      return [kk, a1, g * a1, g * g * a1];
    };
    const c = { t, a: null, b: null, g: 0, s: 1, i: 0, fr: 0 };
    if (t <= 4) { c.a = st(0.707 + res * 6); c.b = st(0.707); } // the ladder and the one-SVF types
    else if (t === 6 || t === 7) { c.a = st(0.5412); c.b = st(1.3066 + res * 5); } // LP24_Q1, Q2 + res x LP24_RES
    else if (t === 11) { c.a = st(0.5 + res * 5.5); c.b = st(0.5); } // the diode: DIODE_Q, DIODE_RES, DIODE_Q2
    else if (t === 10) c.a = st(0.5 + res * 3); // the phaser's all-passes
    else if (t === 12) { c.a = st(1); c.g = Math.pow(10, PEAK_DB * res / 20) - 1; }
    else if (t === 8 || t === 9) {
      const f = Math.min(sr * 0.49, Math.max(sr / COMB_MAX, fc)), dl = sr / f;
      c.i = Math.floor(dl); c.fr = dl - c.i; c.g = (t === 8 ? 1 : -1) * Math.min(COMB_FB, Math.max(0, res));
      c.s = Math.sqrt((1 - Math.abs(c.g)) * Math.sqrt(1 - c.g * c.g));
    }
    return c;
  }

  // core.formant's three bands at this block's vowel/size: SVF coefficients,
  // each band's gain, and the tract's makeup (core.formant_makeup_table)
  tract(x) {
    const tb = this.tab, sr = sampleRate;
    const vw = "formant_vowel" in this.idx ? this.acc[this.idx.formant_vowel] : this.at("formant_vowel", x); // routed
    const pos = Math.min(1, Math.max(0, vw)), size = Math.min(4, Math.max(0.1, this.at("formant_size", x)));
    const vf = pos * (tb.vowel_f.length - 1), lo = Math.floor(vf), hi = Math.min(lo + 1, tb.vowel_f.length - 1), fr = vf - lo;
    const kk = 1 / tb.formant_q, out = [];
    for (let b = 0; b < 3; b++) {
      const fc = Math.min(sr * 0.49, Math.max(20, (tb.vowel_f[lo][b] * (1 - fr) + tb.vowel_f[hi][b] * fr) * size));
      const g = Math.tan(Math.PI * fc / sr), a1 = 1 / (1 + g * (g + kk));
      out.push([kk, a1, g * a1, g * g * a1, (tb.vowel_a[lo][b] * (1 - fr) + tb.vowel_a[hi][b] * fr) * kk]);
    }
    const [slo, shi] = tb.formant_sizes, nv = tb.fv, cnt = tb.fsn;
    const vi = pos * (nv - 1), si = Math.log(size / slo) / Math.log(shi / slo) * (cnt - 1);
    const v0 = Math.min(Math.floor(vi), nv - 2), s0 = Math.min(Math.floor(si), cnt - 2), fv = vi - v0, fs = si - s0;
    const m = (a, b) => tb.formant[a * cnt + b];
    const top = m(v0, s0) * (1 - fs) + m(v0, s0 + 1) * fs, bot = m(v0 + 1, s0) * (1 - fs) + m(v0 + 1, s0 + 1) * fs;
    return { bands: out, makeup: top * (1 - fv) + bot * fv };
  }

  // TPT SVF step on state s[o], s[o+1]: returns low, leaves band in this.bp
  // (no array per sample: that allocation was most of a voice's cost)
  svf(s, o, x, c) {
    const v3 = x - s[o + 1], v1 = c[1] * s[o] + c[2] * v3, v2 = s[o + 1] + c[2] * s[o] + c[3] * v3;
    s[o] = 2 * v1 - s[o]; s[o + 1] = 2 * v2 - s[o + 1];
    this.bp = v1;
    return v2;
  }

  filter(s, o, x, c, d, td) {
    const t = c.type;
    if (t === "none") return x;
    const lo = this.svf(s, o, x, c.s1), bp = this.bp, kk = c.s1[0];
    if (t === "lowpass") return lo;
    if (t === "highpass") return x - kk * bp - lo;
    if (t === "bandpass") return kk * bp;
    if (t === "notch") return x - kk * bp;
    const y = d > 0 ? Math.tanh(lo * d) / td : lo; // ladder: soft_clip(drive)
    return this.svf(s, o + 2, y, c.s2);
  }

  formant(s, o, x, fc, a) { // x through the tract, blended by amount a
    let wet = 0;
    for (let b = 0; b < 3; b++) { this.svf(s, o + 2 * b, x, fc.bands[b]); wet += this.bp * fc.bands[b][4]; }
    return x * (1 - a) + wet * fc.makeup * a;
  }

  // One sample of channel ch through filter fi (0, 1) of the general chain
  // (core.filt): its SVFs' state at v.xs[(ch * 2 + fi) * 6 ..], a comb's
  // ring at v.xr[(ch * 2 + fi) * RING ..], written at v.xs[24 + fi]
  xf(v, ch, fi, x, c, d, td) {
    const s = v.xs, o = (ch * 2 + fi) * 6, t = c.t;
    if (t === 5) return x;
    if (t === 8 || t === 9) { // the comb: y = x + g (y[n - i] (1 - fr) + y[n - i - 1] fr), out s y
      const R = v.xr, b = (ch * 2 + fi) * RING, p = s[24 + fi], M = RING - 1;
      const y = x + c.g * ((1 - c.fr) * R[b + ((p - c.i) & M)] + c.fr * R[b + ((p - c.i - 1) & M)]);
      R[b + p] = y;
      return c.s * y;
    }
    const lo = this.svf(s, o, x, c.a), bp = this.bp, kk = c.a[0];
    if (t === 1) return lo;
    if (t === 2) return x - kk * bp - lo;
    if (t === 3) return kk * bp;
    if (t === 4) return x - kk * bp;
    if (t === 12) return x + c.g * (kk * bp); // the bell: x + g bp
    if (t === 6) return this.svf(s, o + 2, lo, c.b);
    if (t === 7) { const h = x - kk * bp - lo, l2 = this.svf(s, o + 2, h, c.b); return h - c.b[0] * this.bp - l2; }
    if (t === 10) { // three all-passes (x - 2 bp), averaged with the input
      let y = x - 2 * (kk * bp);
      this.svf(s, o + 2, y, c.a); y = y - 2 * (kk * this.bp);
      this.svf(s, o + 4, y, c.a); y = y - 2 * (kk * this.bp);
      return 0.5 * (x + y);
    }
    const z = t === 11 ? (d > 0 ? (Math.tanh(d * lo + DIODE_B) - TB) / (d * (1 - TB * TB)) : lo) // the diode's bias
      : d > 0 ? Math.tanh(lo * d) / td : lo; // the ladder's soft_clip(drive)
    return this.svf(s, o + 2, z, c.b);
  }

  // filter 1, then filter 2 after it (serial) or beside it (parallel: the
  // two averaged), as Synth._two_filters
  xchain(v, ch, x, cf, d, td) {
    const y = cf.f1 ? this.xf(v, ch, 0, x, cf.f1, d, td) : x;
    if (!cf.f2) return y;
    return cf.route === 1 ? this.xf(v, ch, 1, y, cf.f2, d, td) : 0.5 * (y + this.xf(v, ch, 1, x, cf.f2, d, td));
  }

  // post() for the general chain: dc block, the chain, tract, amp and post
  // drive (none after a ladder or a diode filter 1); voice.ts xpost() for a
  // voice in the module
  xpost(v, k, L, R, off, c, N, set) {
    const w = this.wasm, cf = v.c, fc = this.flags.fmt ? v.fc : null, perD = this.routed.has("drive");
    const [faa, fad] = this.S.formant_amount, r = Math.exp(-TAU * 12 / sampleRate), tr = v.rel;
    const driven = cf.type === "ladder" || cf.type === "diode";
    if (v.w >= 0) {
      const { ex, K, buf } = w;
      if (!v.xs) { v.xat = ex.xslot(v.w); v.xs = new Float64Array(buf, v.xat, 32).fill(0); new Float64Array(buf, v.xat + 256, 4 * RING).fill(0); }
      if (fc) { fc.bands.forEach((b, i) => K.set(b, 32 + 5 * i)); K[47] = fc.makeup; }
      K[90] = cf.f2 ? cf.route : 0; this.xk(K, 91, cf.f1); this.xk(K, 104, cf.f2); // voice.ts K_X (after part A's K_NTAP)
      const pk = ex.xpost(v.at, v.xat, set, k.stereo ? 0 : 1, off, c, N, perD | v.dcInit << 1 | v.pd << 3 | !!fc << 5 | driven << 6,
        cf.d, cf.td, faa, fad, r);
      if (c) v.dcInit = true;
      v.pd = !!(ex.lastPd() & 1);
      if (tr) v.q = pk < QUIET ? v.q + c : 0;
      return;
    }
    v.xs ??= new Float64Array(32); v.xr ??= new Float64Array(4 * RING);
    const { A, B, G, DR } = this, dc = v.dc, st = k.stereo, xs = v.xs;
    let pk = 0;
    for (let j = 0; j < c; j++) {
      const a = A[j], b = B[j];
      if (!v.dcInit) { dc[0] = a; dc[2] = b; v.dcInit = true; }
      const ya = a - dc[0] + r * dc[1], yb = b - dc[2] + r * dc[3];
      dc[0] = a; dc[1] = ya; dc[2] = b; dc[3] = yb;
      const d = perD ? DR[j] : cf.d, td = perD ? (d > 0 ? Math.tanh(d) : 0) : cf.td;
      let oa = this.xchain(v, 0, ya, cf, d, td), ob = st ? this.xchain(v, 1, yb, cf, d, td) : oa;
      xs[24] = (xs[24] + 1) & (RING - 1); xs[25] = (xs[25] + 1) & (RING - 1);
      if (fc) {
        const fa = Math.min(1, Math.max(0, faa + fad * (off + j) / N));
        oa = this.formant(v.fs, 0, oa, fc, fa);
        ob = st ? this.formant(v.fs, 6, ob, fc, fa) : oa;
      }
      oa *= G[j]; ob *= G[j];
      if (!driven && (perD ? (v.pd ||= d > 1) : d > 1)) { oa = Math.tanh(oa * d) / td; ob = Math.tanh(ob * d) / td; }
      L[off + j] += oa;
      if (R) R[off + j] += ob;
      if (tr) pk = Math.max(pk, Math.abs(oa), Math.abs(ob));
    }
    if (tr) v.q = pk < QUIET ? v.q + c : 0;
  }

  xk(K, o, f) { // a filter's numbers for voice.ts xpost (K_X): type, the SVFs, g, s, i, fr
    K[o] = f ? f.t : 5;
    if (!f) return;
    if (f.a) K.set(f.a, o + 1);
    if (f.b) K.set(f.b, o + 5);
    K[o + 9] = f.g; K[o + 10] = f.s; K[o + 11] = f.i; K[o + 12] = f.fr;
  }

  // The control pass for c samples of one voice from `off` (never across a
  // 64-sample block edge) into the scratch arrays (the module's set `set`);
  // returns how many ran: fewer than c when its release has finished.
  ctl(v, off, c, N, set) {
    const T = this.T, sr = sampleRate, curve = T.env_curve || 1, relN = Math.round(T.release * sr);
    const rt = this.routed, nr = this.routes.length, S = this.S;
    const { fmt } = this.flags, needLfo = this.needLfo, needFe = this.needFe;
    const { F, W, G, L1, L2, LS, LN, M1, M2, DR, IX, FA, RG, WP, E1, E2, E3, E4, X1, X2 } = set ? this.set1 : this;
    const sl1 = S.osc1_level, sl2 = S.osc2_level, sls = S.sub_level, sln = S.noise_level;
    const svd = S.vibrato_depth, spw = S.pwm_depth, std = S.tremolo_depth, sfm = S.fm_amount;
    const srg = S.ring_mod, sd1 = S.osc1_detune, sd2 = S.osc2_detune, sdr = S.drive, swp = S.wt_pos;
    const sx1 = S.osc1_warp_amt, sx2 = S.osc2_warp_amt, warp = this.flags.warp; // the warps' amounts
    // where each per-sample route target lands in this.acc (-1: not routed)
    const I = this.idx, acc = this.acc, ix = (name) => (name in I ? I[name] : -1);
    const iL1 = ix("osc1_level"), iL2 = ix("osc2_level"), iLS = ix("sub_level"), iLN = ix("noise_level");
    const iVD = ix("vibrato_depth"), iPW = ix("pwm_depth"), iTD = ix("tremolo_depth"), iDR = ix("drive");
    const iD1 = ix("osc1_detune"), iD2 = ix("osc2_detune"), iPT = ix("pitch"), sbn = this.bendS;
    // the routes' per-sample targets, and their sources (this.need: the ones a route reads)
    const iWP = ix("wt_pos"), iFA = ix("fm_amount"), iIX = ix("fm_index"), iE4 = [1, 2, 3, 4].map((k) => ix(`fm4_level${k}`));
    const need = this.need, SV = this.SV, dl2 = T.lfo2_rate / sr, dl3 = T.lfo3_rate / sr, sh1 = T.lfo_shape;
    const old1 = sh1 === "sine" || sh1 === "triangle" || sh1 === "saw" || sh1 === "square" || sh1 === "noise";
    let p2 = v.p2, p3 = v.p3;
    const iX1 = ix("osc1_warp_amt"), iX2 = ix("osc2_warp_amt");
    // hard sync: osc2's pitch also carries sync_pitch semitones (synth._osc_stack)
    const sync = T.osc2_sync === "on" && !!SYNCS[T.osc2], iSP = sync ? ix("sync_pitch") : -1, ssp = S.sync_pitch;
    // the oscillators' pitch multipliers: fixed unless a route or a drag moves them
    const m1 = iD1 < 0 && sd1[1] === 0 ? Math.pow(2, T.osc1_octave + sd1[0] / 1200) : 0;
    const m2 = iD2 < 0 && sd2[1] === 0 && (!sync || (iSP < 0 && ssp[1] === 0))
      ? Math.pow(2, T.osc2_octave + sd2[0] / 1200 + (sync ? ssp[0] / 12 : 0)) : 0;
    // the fm4 operators' levels and decays (core.fm4: level x exp(-t / decay))
    const fm4 = T.osc1 === "fm4" || T.osc2 === "fm4", E = [E1, E2, E3, E4];
    const lv4 = [1, 2, 3, 4].map((k) => +T[`fm4_level${k}`] || 0);
    const kd4 = [1, 2, 3, 4].map((k) => (T[`fm4_decay${k}`] > 0 ? -1 / (T[`fm4_decay${k}`] * sr) : 0));
    // analog drift: cents at drift 1 x the amount, the knot length (synth.drift_knot)
    const dc = T.drift > 0 ? T.drift * DRIFT_CENTS : 0, kn = BLOCK * Math.max(1, Math.floor(0.5 * sr / BLOCK + 0.5));
    const fmIx = T.osc1 === "fm" || T.osc2 === "fm", ixd = T.fm_decay > 0 ? -1 / (T.fm_decay * sr) : 0;
    const { attack, decay, sustain, filter_attack: fa, filter_decay: fd, filter_sustain: fs } = T;
    const pe = T.pitch_env, pk = -5 / (Math.max(T.pitch_env_decay, 1e-3) * sr), vdl = T.vibrato_delay;
    const dv = T.vibrato_rate / sr, dp = T.pwm_rate / sr, dtr = T.tremolo_rate / sr, dfl = T.filter_lfo_rate / sr;
    const dm = T.mod_lfo_rate / sr, vel = v.vel, flfo = rt.has("filter_lfo_depth");
    let pv = v.pv, pp = v.pp, pt = v.pt, pf = v.pf, pm = v.pm, fe = v.fe;
    let n = 0, lfoF = 0;
    const iN = 1 / N, isr = 1 / sr, wc = v.w >= 0 && !nr;
    if (wc) { // the WebAssembly control pass (a voice with routes runs the JS loop)
      const w = this.wasm, P = w.P, C = w.C;
      if (v.n % BLOCK === 0 || !v.c) { // this block's filter, as the loop sets it at n = 0
        const t = v.n * isr, x = off * iN;
        fe = v.rel ? v.fRelLevel * this.fall(v.relN / sr, T.filter_release, curve) : this.env(t, fa, fd, fs, curve, v.f0);
        lfoF = this.at("filter_lfo_depth", x) > 0 || flfo ? this.lfo(T.filter_lfo_shape, pf + dfl, T.filter_lfo_rate, v.n) : 0;
        v.c = this.coeffs(v, fe, x, lfoF);
        if (fmt) v.fc = this.tract(x);
      }
      if (w.stale) { // the render's constants, once (voice.ts P_*)
        P.set([attack, decay, sustain, T.release, curve, dv, dp, dtr, dfl, dm, vdl, pe, pk, T.fm_index, ixd,
          +fmIx, T.osc1_octave, T.osc2_octave, m1, m2, sr, relN, T.filter_release, +sync]);
        [sl1, sl2, sls, sln, svd, spw, std, sdr, sd1, sd2, sfm, srg, swp, sbn, ssp].forEach((s, i) => P.set(s, 24 + 2 * i));
        P.set([dc, kn, +fm4, ...lv4, ...kd4], 54);
        w.stale = false;
      }
      C[0] = v.n; C[1] = +v.rel; C[2] = v.relN; C[3] = v.relLevel; C[4] = v.fRelLevel; C[5] = v.a0;
      C[6] = fe; C[7] = pv; C[8] = pp; C[9] = pt; C[10] = pf; C[11] = pm; C[12] = v.freq; C[13] = v.glideN;
      C[14] = v.glideR; C[15] = v.target; C[16] = v.fade; C[17] = v.fadeN; C[18] = vel;
      if (dc) { this.knots(v, kn); C[19] = v.dw0; C[20] = v.dw1; C[21] = v.dk * kn; }
      n = w.ex.control(off, c, N, set);
      v.n = C[0]; v.relN = C[2]; fe = C[6]; pv = C[7]; pp = C[8]; pt = C[9]; pf = C[10]; pm = C[11];
      if (warp) for (let j = 0; j < n; j++) { const x = (off + j) * iN; X1[j] = sx1[0] + sx1[1] * x; X2[j] = sx2[0] + sx2[1] * x; }
      v.freq = C[12]; v.glideN = C[13]; v.fade = C[16];
    }
    // -- control pass: envelopes, LFOs, routes -> per-sample arrays
    for (; !wc && n < c; n++) {
      const x = (off + n) * iN, t = v.n * isr, bs = n === 0 && (v.n % BLOCK === 0 || !v.c);
      if (v.fadeN && v.fade <= 0) break; // stolen: faded out
      let amp, ru = -1;
      if (v.rel) {
        if (!(T.release > 0) || v.relN >= relN) break;
        // core.adsr: each envelope releases from the gate over its own
        // release time (the voice lives for the amp's), then sits at zero
        const u = v.relN / sr;
        ru = u;
        amp = v.relLevel * this.fall(u, T.release, curve);
        fe = v.fRelLevel * this.fall(u, T.filter_release, curve);
        v.relN++;
      } else {
        amp = this.env(t, attack, decay, sustain, curve, v.a0);
        if (bs || needFe) fe = this.env(t, fa, fd, fs, curve, v.f0);
      }
      // LFO phases advance before they are read: core._phase is a cumsum
      pv += dv; pp += dp; pt += dtr; pf += dfl; pm += dm; p2 += dl2; p3 += dl3;
      let l1 = sl1[0] + sl1[1] * x, l2 = sl2[0] + sl2[1] * x, ls = sls[0] + sls[1] * x;
      let ln = sln[0] + sln[1] * x, vd = svd[0] + svd[1] * x, pw = spw[0] + spw[1] * x;
      let td = std[0] + std[1] * x, dr = sdr[0] + sdr[1] * x, d1 = sd1[0] + sd1[1] * x, d2 = sd2[0] + sd2[1] * x;
      let bst = sbn[0] + sbn[1] * x;
      if (nr) {
        SV[1] = needLfo ? (old1 ? this.lfo(sh1, pm, T.mod_lfo_rate, v.n) : this.lfoX(v, 1, sh1, pm)) : 0;
        SV[2] = fe; SV[3] = amp; SV[4] = vel; SV[11] = v.key; SV[12] = v.rnd;
        if (need.has(5)) SV[5] = this.lfoG(v, 2, p2);
        if (need.has(6)) SV[6] = this.lfoG(v, 3, p3);
        if (need.has(7)) SV[7] = ru >= 0 ? v.e3R * this.fall(ru, T.env3_release, T.env3_curve) : this.envK(3, t, v.e30);
        if (need.has(8)) SV[8] = ru >= 0 ? v.e4R * this.fall(ru, T.env4_release, T.env4_curve) : this.envK(4, t, v.e40);
        if (need.has(9)) SV[9] = clamp01(this.at("mod_wheel", x) + this.at("wheel_cc", x));
        if (need.has(10)) SV[10] = clamp01(this.at("aftertouch", x) + this.at("at_cc", x));
        this.route(x, SV, bs);
        if (iL1 >= 0) l1 = acc[iL1];
        if (iL2 >= 0) l2 = acc[iL2];
        if (iLS >= 0) ls = acc[iLS];
        if (iLN >= 0) ln = acc[iLN];
        if (iVD >= 0) vd = acc[iVD];
        if (iPW >= 0) pw = acc[iPW];
        if (iTD >= 0) td = acc[iTD];
        if (iDR >= 0) dr = acc[iDR];
        if (iD1 >= 0) d1 = acc[iD1];
        if (iD2 >= 0) d2 = acc[iD2];
        if (iPT >= 0) bst += acc[iPT];
      }
      if (bs) {
        lfoF = this.at("filter_lfo_depth", x) > 0 || flfo ? this.lfo(T.filter_lfo_shape, pf, T.filter_lfo_rate, v.n) : 0;
        v.c = this.coeffs(v, fe, x, lfoF);
        if (fmt) v.fc = this.tract(x);
      }
      // the pitch. grain-worklet.js's `ahead` runs these same terms forward
      // (a formant sampler reads the pitch a root period ahead): keep them
      // in step — and voice.ts control(), which computes them too
      let f = v.freq;
      if (vd > 0) f *= Math.pow(2, Math.sin(TAU * pv) * vd * Math.min(1, Math.max(0, (t - vdl) / 0.3)) / 12);
      if (pe) f *= Math.pow(2, pe * Math.exp(v.n * pk) / 12);
      if (bst) f *= Math.pow(2, bst / 12); // bend + routes to "pitch" (synth.note)
      if (dc) { // the drift's wander: knots smoothstepped (synth.drift_cents)
        this.knots(v, kn);
        const xk = (v.n - v.dk * kn) / kn, sk = xk * xk * (3 - 2 * xk);
        f *= Math.pow(2, dc * (v.dw0 + (v.dw1 - v.dw0) * sk) / 1200);
      }
      F[n] = f;
      W[n] = pw > 0 ? 0.5 + Math.sin(TAU * pp) * pw * 0.45 : 0.5;
      G[n] = amp * vel * (td > 0 ? 1 - td * (1 - (Math.sin(TAU * pt) + 1) * 0.5) : 1);
      if (v.fadeN) G[n] *= v.fade-- / v.fadeN;
      L1[n] = l1; L2[n] = l2; LS[n] = ls; LN[n] = ln; DR[n] = dr;
      M1[n] = m1 || Math.pow(2, T.osc1_octave + d1 / 1200);
      M2[n] = m2 || Math.pow(2, T.osc2_octave + d2 / 1200 + (sync ? (iSP >= 0 ? acc[iSP] : ssp[0] + ssp[1] * x) / 12 : 0));
      if (fmIx) IX[n] = (iIX >= 0 ? acc[iIX] : T.fm_index) * (ixd ? Math.exp(v.n * ixd) : 1);
      if (fm4) {
        for (let k = 0; k < 4; k++) {
          const lv = iE4[k] >= 0 ? acc[iE4[k]] : lv4[k];
          E[k][n] = kd4[k] ? lv * Math.exp(v.n * kd4[k]) : lv;
        }
      }
      FA[n] = iFA >= 0 ? acc[iFA] : sfm[0] + sfm[1] * x; RG[n] = srg[0] + srg[1] * x;
      WP[n] = iWP >= 0 ? acc[iWP] : swp[0] + swp[1] * x;
      if (warp) { X1[n] = iX1 >= 0 ? acc[iX1] : sx1[0] + sx1[1] * x; X2[n] = iX2 >= 0 ? acc[iX2] : sx2[0] + sx2[1] * x; }
      v.n++;
      if (v.glideN > 0) { v.freq *= v.glideR; if (--v.glideN === 0) v.freq = v.target; }
    }
    v.pv = pv; v.pp = pp; v.pt = pt; v.pf = pf; v.pm = pm; v.fe = fe; v.p2 = p2; v.p3 = p3;
    if (n) v.lvl = G[n - 1];
    return n;
  }

  // The oscillators, one unison copy at a time, then post(): c samples of
  // one voice from `off`, its control pass in scratch set `set`
  dsp(v, k, L, R, off, c, N, set) {
    const T = this.T, sr = sampleRate, has2 = !!T.osc2, S = this.S, w = this.wasm;
    const { sub, nz } = this.flags, srg = S.ring_mod, sfm = S.fm_amount;
    if (v.w >= 0 && w.shapes) { // the WebAssembly stack: two copies per f64x2
      w.ex.stack(v.at, v.at, set, set, false, k.U, w.sh1, w.sh2, w.sw, c, sr, T.fm_ratio, w.nk);
      this.post(v, k, L, R, off, c, N, set);
      return;
    }
    const { F, M1, M2, FA, RG, L1, L2, LS, LN, O1, O2, NZ, F1, F2, A, B, OR, X1, X2 } = this, { w1, w2 } = k;
    const ring = has2 && (srg[0] > 0 || srg[1] !== 0), cross = has2 && (sfm[0] > 0 || sfm[1] !== 0 || this.routed.has("fm_amount"));
    A.fill(0, 0, c); B.fill(0, 0, c);
    for (let u = 0; u < k.U; u++) {
      const du = k.det[u], pl = k.pl[u], pr = k.pr[u];
      if (has2) {
        for (let j = 0; j < c; j++) F2[j] = F[j] * du * M2[j];
        if (k.sync) { // the master runs at osc1's pitch, before cross-FM
          for (let j = 0; j < c; j++) F1[j] = F[j] * du * M1[j];
          this.syncBlock(T.osc2, v, u, F1, F2, O2, c);
        } else {
          if (T.osc2 === "wavetable") v.mip[MAXU + u] = this.mipOf(F2, c, w2, X2);
          if (w2) this.warpBlock(T.osc2, v, 1, u, F2, O2, c, w2); else this.oscBlock(T.osc2, v, 1, u, F2, O2, c);
        }
      }
      for (let j = 0; j < c; j++) F1[j] = F[j] * du * M1[j] * (cross && FA[j] > 0 ? 1 + 0.9 * FA[j] * O2[j] : 1);
      if (T.osc1 === "wavetable") v.mip[u] = this.mipOf(F1, c, w1, X1);
      if (w1) this.warpBlock(T.osc1, v, 0, u, F1, O1, c, w1); else this.oscBlock(T.osc1, v, 0, u, F1, O1, c);
      // a stereo sample: what its right side adds to the mix below (the
      // level and the ring are linear in osc1; the rest is the same on both)
      if (k.smp2) for (let j = 0; j < c; j++) OR[j] = L1[j] * (OR[j] - O1[j]) * (ring && RG[j] > 0 ? 1 - RG[j] + O2[j] * RG[j] : 1);
      if (nz) this.noise(v, 2 * MAXU + u, NZ, c);
      let qs = v.qs[u];
      const hs = du * 0.5 / sr; // the sub: an octave under the copy
      for (let j = 0; j < c; j++) {
        let o1 = O1[j], y;
        if (has2) {
          const o2 = O2[j];
          if (ring && RG[j] > 0) o1 = o1 * (1 - RG[j]) + o1 * o2 * RG[j];
          y = L1[j] * o1 + L2[j] * o2;
        } else y = L1[j] * o1;
        if (sub) {
          const q = qs + F[j] * hs;
          qs = q - Math.floor(q);
          y += LS[j] * Math.sin(TAU * qs);
        }
        if (nz) y += LN[j] * NZ[j];
        A[j] += y * pl; B[j] += (k.smp2 ? y + OR[j] : y) * pr;
      }
      v.qs[u] = qs;
    }
    this.post(v, k, L, R, off, c, N, 0);
  }

  // dc block, filter, tract, amp and post drive for c samples of v's mix
  // (A/B), added into L/R from `off`
  post(v, k, L, R, off, c, N, set) {
    if (v.c.x) { this.xpost(v, k, L, R, off, c, N, set); return; }
    const { A, B, G, DR } = this, cf = v.c, dc = v.dc, st = k.stereo, sv = v.s;
    const fc = this.flags.fmt ? v.fc : null, ladder = cf.type === "ladder", perD = this.routed.has("drive");
    const [faa, fad] = this.S.formant_amount, r = Math.exp(-TAU * 12 / sampleRate); // core.dc_block at 12 Hz
    const [, a1, a2, a3] = cf.s1, [, b1, b2, b3] = cf.s2, tr = v.rel;
    if (v.w >= 0 && cf.type in FTYPES) { // the WebAssembly post: L/R as the two f64x2 lanes
      const ex = this.wasm.ex, K = this.wasm.K;
      if (fc) { fc.bands.forEach((b, i) => K.set(b, 32 + 5 * i)); K[47] = fc.makeup; }
      this.coef(K, 0, cf); this.coef(K, 1, cf);
      const pk = ex.post(v.at, v.at, set, set, st ? 0 : 1, off, c, N, FTYPES[cf.type],
        perD | v.dcInit << 1 | v.dcInit << 2 | v.pd << 3 | v.pd << 4 | !!fc << 5, cf.d, cf.td, faa, fad, r);
      if (c) v.dcInit = true;
      v.pd = !!(ex.lastPd() & 1);
      if (tr) v.q = pk < QUIET ? v.q + c : 0;
      return;
    }
    let pk = 0; // a released voice's peak here: silence culling (QUIET)
    for (let j = 0; j < c; j++) {
      const a = A[j], b = B[j];
      if (!v.dcInit) { dc[0] = a; dc[2] = b; v.dcInit = true; } // state from the first sample
      const ya = a - dc[0] + r * dc[1], yb = b - dc[2] + r * dc[3];
      dc[0] = a; dc[1] = ya; dc[2] = b; dc[3] = yb;
      const d = perD ? DR[j] : cf.d, td = perD ? (d > 0 ? Math.tanh(d) : 0) : cf.td;
      let oa, ob;
      if (ladder) { // SVF -> soft_clip(drive) -> SVF, inlined: most sounds are ladders
        let s0 = sv[0], s1 = sv[1], v3 = ya - s1, v1 = a1 * s0 + a2 * v3, v2 = s1 + a2 * s0 + a3 * v3;
        sv[0] = 2 * v1 - s0; sv[1] = 2 * v2 - s1;
        let y = d > 0 ? Math.tanh(v2 * d) / td : v2;
        s0 = sv[2]; s1 = sv[3]; v3 = y - s1; v1 = b1 * s0 + b2 * v3; v2 = s1 + b2 * s0 + b3 * v3;
        sv[2] = 2 * v1 - s0; sv[3] = 2 * v2 - s1; oa = v2;
        if (st) {
          s0 = sv[4]; s1 = sv[5]; v3 = yb - s1; v1 = a1 * s0 + a2 * v3; v2 = s1 + a2 * s0 + a3 * v3;
          sv[4] = 2 * v1 - s0; sv[5] = 2 * v2 - s1;
          y = d > 0 ? Math.tanh(v2 * d) / td : v2;
          s0 = sv[6]; s1 = sv[7]; v3 = y - s1; v1 = b1 * s0 + b2 * v3; v2 = s1 + b2 * s0 + b3 * v3;
          sv[6] = 2 * v1 - s0; sv[7] = 2 * v2 - s1; ob = v2;
        } else ob = oa;
      } else {
        oa = this.filter(sv, 0, ya, cf, d, td);
        ob = st ? this.filter(sv, 4, yb, cf, d, td) : oa;
      }
      if (fc) {
        const fa = Math.min(1, Math.max(0, faa + fad * (off + j) / N));
        oa = this.formant(v.fs, 0, oa, fc, fa);
        ob = st ? this.formant(v.fs, 6, ob, fc, fa) : oa;
      }
      oa *= G[j]; ob *= G[j];
      // non-ladder drive over 1; a routed drive follows core's gate ("any
      // sample > 1" clips the whole note), so once on it stays on
      if (!ladder && (perD ? (v.pd ||= d > 1) : d > 1)) { oa = Math.tanh(oa * d) / td; ob = Math.tanh(ob * d) / td; }
      L[off + j] += oa;
      if (R) R[off + j] += ob;
      if (tr) pk = Math.max(pk, Math.abs(oa), Math.abs(ob));
    }
    if (tr) v.q = pk < QUIET ? v.q + c : 0;
  }
}
registerProcessor("synthlab-voice", SynthlabVoice);
