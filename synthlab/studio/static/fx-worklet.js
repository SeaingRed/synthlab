// synthlab/studio/static/fx-worklet.js
// Live FX for the keys: a run of FX-rack units, each
// effects.py's own algorithm sample by sample, on one layer's (or the
// master's) stereo signal. livefx.js builds the chains (studio/fxlive.py
// says which unit plays how) and puts reverb (a ConvolverNode loaded with
// effects.reverb_ir) and lowpass/highpass (BiquadFilterNode) between runs
// of these. The EQ's bands are static/eq-worklet.js's (same scope; no room
// here). tests/studio/test_live_fx.py runs this file under node against
// effects.py, unit by unit.
// Units arrive resolved by fxrack.resolve_unit (the render's own numbers:
// a synced delay's time, a gate's bpm). LFO-driven units keep their own
// time from the processor's first sample, as the engine's from a buffer's;
// gate / pump / sidechain run on the transport clock: `clock` = the frame
// the loop started on, so they duck on the beat the loop plays.
// Silence culling: while the input is silent (under QUIET, -120 dBFS) and
// every unit's tail (`tail`, a bound in samples on how long it can still
// ring: feedback echoes down to QUIET, a history window, the allpasses)
// has run out, the node skips its units and outputs silence; the first
// non-silent sample resumes it in that same quantum. The LFOs' clock keeps
// counting, so a resumed chorus is where it would have been.
// 200 -> 215: the culling and the CPU meter's timer (as voice-worklet.js).
// 215 -> 232: the drive's modes (hard / fuzz / tube) and the clipper.
// 232 -> 310: the studio tricks (the vocal booth): ADT double, rotary, tape
// echo, telephone (a band-pass biquad), pitch shift; a history of any size.
// 310 -> 340: the pitch unit's splice (effects._pitch_splices: each grain
// started in phase with the one it fades out of, picked at its start).
// 340 -> 310: the pitch shifter is vox-worklet.js's, a
// module in this scope, with the live vocal chain's units that share it
// (auto-tune, harmonizer, vocal comp, de-esser, gate): `synthlabVox`, and
// this file's Hist / rbj / biq / panGains handed to it as `synthlabFx`.
// The units of fx2-worklet.js (auto-filter, tremolo, tape,
// ping-pong, formant and frequency shifters, exciter, vocoder) are
// fx2-worklet.js, a fourth module in this scope: `synthlabFx2`'s hooks
// here (three lines, within the budget), `wander` handed out with the rest.
// Budget: <= 310 code lines.

const TAU = 2 * Math.PI;
const LOOK = 1 << 15; // history per channel: 0.68 s at 48 kHz (chorus/flanger read ~30 ms back)
const nSamples = (s, sr) => Math.max(1, Math.round(s * sr)); // core.n_samples
const QUIET = 1e-6, HEAD = 4; // silence; the loudest input a tail is sized for
// samples until a feedback line of length d, fed back at fb, is under QUIET
const ring = (d, fb) => d * (fb > 0 ? Math.ceil(Math.log(QUIET / HEAD) / Math.log(Math.min(fb, 0.999))) + 1 : 1);
const clock = globalThis.performance?.now ? () => globalThis.performance.now() : Date.now;

// A feedback delay line: y[n] = x[n] + fb * y[n - d] (effects._delay_line)
const line = (d) => ({ d, buf: new Float64Array(d), i: 0 });
const feed = (l, x, fb) => {
  const y = x + fb * l.buf[l.i];
  l.buf[l.i] = y; l.i = l.i + 1 === l.d ? 0 : l.i + 1;
  return y;
};
// A delay of exactly d samples (the ping-pong's right-channel offset)
const plain = (l, x) => { const y = l.buf[l.i]; l.buf[l.i] = x; l.i = l.i + 1 === l.d ? 0 : l.i + 1; return y; };
// RBJ biquad, transposed direct form II (core.biquad's static path = lfilter)
const rbj = (kind, fc, q, sr) => {
  const w0 = TAU * Math.min(Math.max(fc, 20), sr * 0.49) / sr, al = Math.sin(w0) / (2 * Math.max(q, 0.05)), cw = Math.cos(w0);
  const b = kind === "lowpass" ? [(1 - cw) / 2, 1 - cw, (1 - cw) / 2] : kind === "bandpass" ? [al, 0, -al] : [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2];
  const a0 = 1 + al;
  return { b0: b[0] / a0, b1: b[1] / a0, b2: b[2] / a0, a1: -2 * cw / a0, a2: (1 - al) / a0, z1: 0, z2: 0 };
};
const biq = (f, x) => { const y = f.b0 * x + f.z1; f.z1 = f.b1 * x - f.a1 * y + f.z2; f.z2 = f.b2 * x - f.a2 * y; return y; };

// A modulated read of a channel's history (effects._mod_delay: linear
// interpolation at n - d samples)
class Hist {
  constructor(size = LOOK) { this.m = size - 1; this.buf = new Float64Array(size); this.w = 0; } // size: a power of two
  push(x) { this.buf[this.w] = x; this.w = (this.w + 1) & this.m; }
  // the sample `d` (fractional) before the one just pushed
  at(d) { // d < 0 is clamped, as effects._mod_delay does: never the future
    const p = this.w - 1 - Math.max(d, 0), i = Math.floor(p), fr = p - i;
    const a = this.buf[i & this.m], b = this.buf[(i + 1) & this.m];
    return a + (b - a) * fr;
  }
}
// effects.wander: three slow sines, at most +-1 (ADT's wobble, the tape's wow)
const wander = (u, t) => u.wander.reduce((w, [hz, ph, amp]) => w + amp * Math.sin(TAU * hz * t + ph), 0) / u.wander_norm;
const panGains = (p) => { const a = (Math.min(1, Math.max(-1, p)) + 1) * Math.PI / 4; return [Math.cos(a) * Math.SQRT2, Math.sin(a) * Math.SQRT2]; }; // effects.pan's law
const soft = (x, a) => (a > 0 ? Math.tanh(x * a) / Math.tanh(a) : x); // core.soft_clip
globalThis.synthlabFx = { Hist, rbj, biq, panGains, wander }; // vox- and fx2-worklet.js's: their units run in this scope
const vox = () => globalThis.synthlabVox; // the pitch shifter and the vocal units (vox-worklet.js)
const fx2 = () => globalThis.synthlabFx2; // fx2-worklet.js's units

class SynthlabFx extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.n = 0; // samples since the first one: the LFOs' time, as a buffer's in the engine
    this.clock = 0; // the transport's loop start, as a frame (gate / pump / sidechain)
    this.units = [];
    this.cull = true; this.still = 0; this.ms = 0; this.q = 0; // culling; the meter
    this.set(options?.processorOptions?.units || []);
    this.port.onmessage = (e) => {
      if (e.data.type === "units") this.set(e.data.units);
      if (e.data.type === "clock") this.clock = Math.round(e.data.t0 * sampleRate);
      if (e.data.type === "dispose") this.dead = true; // replaced by livefx.js: stop processing
    };
  }

  // New params for the same kinds keep their state (a slider drag must not
  // cut a delay's tail); a different chain starts fresh.
  set(units) {
    const same = units.length === this.units.length && units.every((u, i) => u.kind === this.units[i].u.kind);
    this.units = units.map((u, i) => (same ? Object.assign(this.units[i], this.init(u, this.units[i])) : this.init(u, {})));
    this.tail = this.units.reduce((a, s) => a + s.tail, 0);
  }

  init(u, old) {
    const sr = sampleRate, s = { u, tail: 0 };
    if (u.kind === "delay") {
      const pp = u.ping_pong, dl = nSamples(pp ? u.time * 2 : u.time, sr), dr = nSamples(pp ? u.time * 2 : u.time * 1.02, sr);
      const keep = old.L?.d === dl && old.R?.d === dr && !!old.off === pp;
      Object.assign(s, keep ? { L: old.L, R: old.R, off: old.off, fl: old.fl, fr: old.fr } : {
        L: line(dl), R: line(dr), off: pp ? line(nSamples(u.time, sr)) : null,
        fl: rbj("lowpass", 4000, 0.707, sr), fr: rbj("lowpass", 4000, 0.707, sr) });
      s.tail = ring(Math.max(dl, dr), u.feedback) + (pp ? nSamples(u.time, sr) : 0) + 1024;
    } else if (u.kind === "comb") {
      const d = Math.max(1, Math.round(sr / Math.max(u.tune, 1e-6)));
      Object.assign(s, old.L?.d === d ? { L: old.L, R: old.R } : { L: line(d), R: line(d) });
      s.tail = ring(d, Math.min(Math.max(u.feedback, 0), 0.95));
    } else if (u.kind === "chorus" || u.kind === "flanger") {
      const d = nSamples(u.base * 1e-3, sr);
      Object.assign(s, { hL: old.hL || new Hist(), hR: old.hR || new Hist(),
        cL: old.cL?.d === d ? old.cL : line(d), cR: old.cR?.d === d ? old.cR : line(d) });
      s.tail = (u.kind === "flanger" ? ring(d, u.feedback) : 0) + nSamples((u.base * 1.1 + u.depth) * 1e-3, sr) + 2;
    } else if (u.kind === "phaser") {
      s.ap = old.ap || new Float64Array(24); // 6 stages x 2 channels, one DF2T state each
      s.tail = 8192; // six allpasses whose lowest pole (300 Hz) falls to QUIET in ~400 samples each
    } else if (u.kind === "bitcrush") {
      s.hold = old.hold || [0, 0]; s.tail = Math.max(1, Math.trunc(u.downsample));
    } else if (u.kind === "gate") {
      const w = Math.floor(0.004 * sr), k = [];
      let sum = 0;
      for (let i = 0; i < 2 * w + 1; i++) { const h = 0.5 - 0.5 * Math.cos(TAU * i / (2 * w)); k.push(h); sum += h; } // np.hanning
      s.kernel = k.map((h) => h / sum); s.w = w;
      s.step = Math.max(1, Math.round(60 / u.bpm / 4 * sr));
      s.pat = [...u.pattern].map((c) => (c === "x" ? 1 : 0));
    } else if (u.kind === "pump" || u.kind === "sidechain") {
      s.seg = nSamples(u.release, sr); s.att = nSamples(u.attack, sr);
    } else if (u.kind === "doubler") {
      Object.assign(s, { hL: old.hL || new Hist(), hR: old.hR || new Hist() });
      s.tail = nSamples(u.time + u.adt_wobble * u.wobble, sr) + 2;
    } else if (vox()?.kinds.has(u.kind)) Object.assign(s, vox().init(u, old, sr)); // the pitch unit and the vocal chain's
    else if (fx2()?.kinds.has(u.kind)) Object.assign(s, fx2().init(u, old, sr));
    else if (u.kind === "rotary") { // the rotors keep turning through a slider drag
      Object.assign(s, { hH: old.hH || new Hist(), hD: old.hD || new Hist(), xo: old.xo || rbj("lowpass", u.crossover, 0.707, sr),
        rot: old.rot || [u.horn, u.drum].map((spec) => ({ spec, f: undefined, acc: 0 })) });
      s.tail = 4096;
    } else if (u.kind === "tape_echo") { // the tape keeps what is on it while it is long enough
      const top = (u.time + u.tape_wow[0] + u.tape_wow[1]) * sr + 4, size = 2 ** Math.ceil(Math.log2(top)), ok = old.tL?.m === size - 1;
      Object.assign(s, { tL: ok ? old.tL : new Hist(size), tR: ok ? old.tR : new Hist(size), zL: old.zL || 0, zR: old.zR || 0 });
      s.tail = ring(Math.ceil(top), Math.min(u.feedback, 0.999)) + 1024;
    } else if (u.kind === "telephone") { // two band-passes a channel, their state kept through a drag
      s.f = [0, 1, 2, 3].map((j) => Object.assign(rbj("bandpass", u.centre, u.q, sr), old.f ? { z1: old.f[j].z1, z2: old.f[j].z2 } : {}));
      s.tail = 16384;
    } else if (u.kind === "eq") Object.assign(s, globalThis.synthlabEq.init(u, old, sr)); // eq-worklet.js
    return s;
  }

  process(inputs, outputs) {
    const t0 = clock(), inp = inputs[0], out = outputs[0], N = out[0].length;
    const L = out[0], R = out[1] || out[0];
    const iL = inp?.[0], iR = inp?.[1] || iL;
    let pk = 0;
    for (let i = 0; i < N; i++) { L[i] = iL ? iL[i] : 0; R[i] = iR ? iR[i] : 0; pk = Math.max(pk, Math.abs(L[i]), Math.abs(R[i])); }
    this.still = pk >= QUIET || !this.cull ? 0 : this.still + N; // samples of silent input
    // skip only once the silence before this quantum outlasts every tail
    if (this.still - N <= this.tail) for (const s of this.units) this.run(s, L, R, N);
    else { // culled: silent in, every tail rung out; a pitch grain's splice in silence is none
      L.fill(0); R.fill(0);
      for (const s of this.units) for (const m of [vox(), fx2()]) if (m?.kinds.has(s.u.kind)) m.quiet(s);
    }
    this.n += N;
    this.ms += clock() - t0;
    if (++this.q >= 64) { this.port.postMessage({ type: "load", ms: this.ms, quanta: this.q }); this.ms = 0; this.q = 0; }
    return !this.dead;
  }

  // One unit over the block, in place.
  run(s, L, R, N) {
    const u = s.u, sr = sampleRate, k = u.kind, n0 = this.n;
    if (k === "drive" || k === "fold" || k === "clip") { // effects.drive_shape / sine fold / _clip, blended
      const f = k === "drive" ? this.shape(u) : k === "clip" ? this.clipper(u) : (x) => Math.sin(x * u.amount * Math.PI) * 0.8;
      const m = u.mix;
      for (let i = 0; i < N; i++) { L[i] = L[i] * (1 - m) + f(L[i]) * m; R[i] = R[i] * (1 - m) + f(R[i]) * m; }
    } else if (k === "bitcrush") { // quantise, then hold every d-th sample
      const lv = Math.pow(2, Math.trunc(u.bits)), d = Math.max(1, Math.trunc(u.downsample));
      for (let i = 0; i < N; i++) {
        if ((n0 + i) % d === 0) { s.hold[0] = Math.round(L[i] * lv) / lv; s.hold[1] = Math.round(R[i] * lv) / lv; }
        L[i] = s.hold[0]; R[i] = s.hold[1];
      }
    } else if (k === "ringmod") {
      for (let i = 0; i < N; i++) {
        const c = Math.sin(TAU * u.freq * ((n0 + i) / sr)), m = u.mix;
        L[i] = L[i] * (1 - m) + L[i] * c * m; R[i] = R[i] * (1 - m) + R[i] * c * m;
      }
    } else if (k === "comb") { // effects._comb: wet * sqrt(1 - fb^2)
      const fb = Math.min(Math.max(u.feedback, 0), 0.95), g = Math.sqrt(1 - fb * fb), m = u.mix;
      for (let i = 0; i < N; i++) {
        L[i] = L[i] * (1 - m) + feed(s.L, L[i], fb) * g * m; R[i] = R[i] * (1 - m) + feed(s.R, R[i], fb) * g * m;
      }
    } else if (k === "width") {
      for (let i = 0; i < N; i++) { const mid = (L[i] + R[i]) * 0.5, side = (L[i] - R[i]) * 0.5 * u.amount; L[i] = mid + side; R[i] = mid - side; }
    } else if (k === "delay") { // effects._delay: wet = the fed-back line (dry included), lowpassed
      const fb = u.feedback, m = u.mix;
      for (let i = 0; i < N; i++) {
        const wl = biq(s.fl, feed(s.L, L[i], fb)), wr = biq(s.fr, feed(s.R, s.off ? plain(s.off, R[i]) : R[i], fb));
        L[i] = L[i] * (1 - m) + wl * m; R[i] = R[i] * (1 - m) + wr * m;
      }
    } else if (k === "chorus") this.chorus(s, L, R, N);
    else if (k === "flanger") this.flanger(s, L, R, N);
    else if (k === "phaser") this.phaser(s, L, R, N);
    else if (k === "gate") this.gate(s, L, R, N);
    else if (k === "pump" || k === "sidechain") this.duck(s, L, R, N);
    else if (k === "eq") globalThis.synthlabEq.run(s, L, R, N); // effects._eq
    else if (k === "doubler") this.doubler(s, L, R, N);
    else if (k === "rotary") this.rotary(s, L, R, N);
    else if (k === "tape_echo") this.tape(s, L, R, N);
    else if (vox()?.kinds.has(k)) vox().run(s, L, R, N, n0); // vox-worklet.js
    else if (fx2()?.kinds.has(k)) fx2().run(s, L, R, N, n0, this); // fx2-worklet.js (`this.pos`: the transport)
    else if (k === "telephone") { // effects._telephone: band-pass twice, soft-clipped
      const a = u.drive, m = u.mix, f = s.f;
      for (let i = 0; i < N; i++) {
        const l = soft(biq(f[1], biq(f[0], L[i])), a), r = soft(biq(f[3], biq(f[2], R[i])), a);
        L[i] = L[i] * (1 - m) + l * m; R[i] = R[i] * (1 - m) + r * m;
      }
    }
  }

  // effects._doubler: the copy `time` s late, its delay wandering, the pair panned apart
  doubler(s, L, R, N) {
    const u = s.u, sr = sampleRate, m = u.mix, [dl, dr] = panGains(-u.spread), [wl, wr] = panGains(u.spread);
    for (let i = 0; i < N; i++) {
      s.hL.push(L[i]); s.hR.push(R[i]);
      const d = (u.time + u.wobble * u.adt_wobble * wander(u, (this.n + i) / sr)) * sr;
      const a = (L[i] * dl + s.hL.at(d) * wl) * u.adt_level, b = (R[i] * dr + s.hR.at(d) * wr) * u.adt_level;
      L[i] = L[i] * (1 - m) + a * m; R[i] = R[i] * (1 - m) + b * m;
    }
  }

  // effects._rotary: the mono sum driven, split at the crossover; each rotor's
  // rate slews to its target (effects.rotor_rate), its phase the rates' sum,
  // and per mic a swinging delay and level
  rotary(s, L, R, N) {
    const u = s.u, sr = sampleRate, m = u.mix, sp = Math.min(1, Math.max(0, u.speed));
    for (let i = 0; i < N; i++) {
      const x = soft((L[i] + R[i]) * 0.5, u.drive), lo = biq(s.xo, x);
      s.hH.push(x - lo); s.hD.push(lo);
      let a = 0, b = 0;
      s.rot.forEach((r, j) => {
        const [slow, fast, ramp, dop, dip] = r.spec, tg = slow + (fast - slow) * sp, k = 1 - Math.exp(-1 / (ramp * sr));
        r.f = k * tg + (1 - k) * (r.f ?? tg);
        const ph = TAU * r.acc / sr, h = j ? s.hD : s.hH;
        const sl = Math.sin(ph), sr2 = Math.sin(ph + u.mic);
        a += h.at((dop * (1 + sl) + u.min_delay) * sr) * (1 - dip * 0.5 * (1 + sl));
        b += h.at((dop * (1 + sr2) + u.min_delay) * sr) * (1 - dip * 0.5 * (1 + sr2));
        r.acc += r.f;
      });
      L[i] = L[i] * (1 - m) + a * m; R[i] = R[i] * (1 - m) + b * m;
    }
  }

  // effects._tape_echo: the head reads the tape (time + wow) back, each pass
  // through a one-pole and a tanh, written back with the input
  tape(s, L, R, N) {
    const u = s.u, sr = sampleRate, m = u.mix, g = 1 + 4 * u.drive, [dw, fw, fh] = u.tape_wow;
    const a = Math.exp(-TAU * u.tape_tone[0] * Math.pow(2, u.tape_tone[1] * u.tone) / sr);
    for (let i = 0; i < N; i++) {
      const t = (this.n + i) / sr, d = (u.time + u.wow * (dw * wander(u, t) + fw * Math.sin(TAU * fh * t))) * sr;
      s.zL = (1 - a) * s.tL.at(d - 1) + a * s.zL; s.zR = (1 - a) * s.tR.at(d - 1) + a * s.zR; // the last pushed is n - 1
      const pl = Math.tanh(s.zL * g) / g, pr = Math.tanh(s.zR * g) / g;
      s.tL.push(L[i] + u.feedback * pl); s.tR.push(R[i] + u.feedback * pr);
      L[i] = L[i] * (1 - m) + (L[i] + pl) * m; R[i] = R[i] * (1 - m) + (R[i] + pr) * m;
    }
  }

  // effects.drive_shape, one sample: the mode's curve (its constants
  // arrive with the unit: fxlive.live_chain)
  shape(u) {
    const a = u.amount, ta = Math.tanh(a);
    if (u.mode === "hard") return (x) => Math.min(1, Math.max(-1, x * a));
    if (u.mode === "fuzz") {
      const g = a * u.fuzz_gain, b = u.fuzz_bias, tb = Math.tanh(b), nf = Math.tanh(g - b) + tb;
      return (x) => (Math.tanh(x * g + b) - tb) / nf;
    }
    if (u.mode === "tube") {
      const aa = Math.max(a, 1e-6), t = Math.tanh(aa), e = u.tube_even;
      return (x) => { const s = Math.tanh(x * aa) / t; return (s + e * s * s) / (1 + e); };
    }
    return (x) => (a > 0 ? Math.tanh(x * a) / ta : x); // soft: core.soft_clip
  }

  // effects._clip, one sample: drive dB in, a hard clip at the ceiling, or
  // linear to t = c (1 - soft) and a tanh knee from there to c
  clipper(u) {
    const g = Math.pow(10, u.drive / 20), c = Math.pow(10, u.ceiling / 20), k = Math.min(1, Math.max(0, u.soft));
    const t = c * (1 - k), room = c * k;
    return (x) => {
      const v = x * g, mag = Math.abs(v);
      if (k <= 0) return Math.min(c, Math.max(-c, v));
      return mag > t ? Math.sign(v) * (t + room * Math.tanh((mag - t) / room)) : v;
    };
  }

  // effects._chorus: three voices a third of a cycle apart per channel;
  // the right channel's base is 1.1x and its rate 1.07x, 1.1 rad on
  chorus(s, L, R, N) {
    const u = s.u, sr = sampleRate, ms = sr * 1e-3;
    for (let i = 0; i < N; i++) {
      s.hL.push(L[i]); s.hR.push(R[i]);
      const t = (this.n + i) / sr;
      let a = 0, b = 0;
      for (let v = 0; v < 3; v++) {
        const ph = TAU * v / 3;
        a += s.hL.at((u.base + u.depth * Math.sin(TAU * u.rate * t + ph)) * ms);
        b += s.hR.at((u.base * 1.1 + u.depth * Math.sin(TAU * u.rate * 1.07 * t + ph + 1.1)) * ms);
      }
      L[i] = L[i] * (1 - u.mix) + (a / 3) * u.mix; R[i] = R[i] * (1 - u.mix) + (b / 3) * u.mix;
    }
  }

  // effects._flanger: a comb one base-delay long (when fed back), read at
  // a sweeping delay; the right channel's sweep a quarter cycle on
  flanger(s, L, R, N) {
    const u = s.u, sr = sampleRate, ms = sr * 1e-3, fb = u.feedback;
    for (let i = 0; i < N; i++) {
      s.hL.push(fb === 0 ? L[i] : feed(s.cL, L[i], fb)); s.hR.push(fb === 0 ? R[i] : feed(s.cR, R[i], fb));
      const t = (this.n + i) / sr;
      const a = s.hL.at((u.base + u.depth * Math.sin(TAU * u.rate * t)) * ms);
      const b = s.hR.at((u.base + u.depth * Math.sin(TAU * u.rate * t + Math.PI / 2)) * ms);
      L[i] = L[i] * (1 - u.mix) + a * u.mix; R[i] = R[i] * (1 - u.mix) + b * u.mix;
    }
  }

  // effects._phaser: six first-order allpasses whose centre is sampled
  // every 64 samples (effects._allpass_sweep), right channel in quadrature
  phaser(s, L, R, N) {
    const u = s.u, sr = sampleRate;
    if (u.mix <= 0) return;
    const lo = 300, hi = 300 + 2200 * u.depth, mid = (hi + lo) / 2, span = (hi - lo) / 2, st = s.ap;
    let gl = s.gl ?? 0, gr = s.gr ?? 0;
    for (let i = 0; i < N; i++) {
      const n = this.n + i;
      if (n % 64 === 0 || s.gl === undefined) {
        const t = n / sr, g = (fc) => { const tn = Math.tan(Math.PI * Math.min(Math.max(fc, 40), sr * 0.45) / sr); return (tn - 1) / (tn + 1); };
        gl = s.gl = g(mid + span * Math.sin(TAU * u.rate * t)); gr = s.gr = g(mid + span * Math.sin(TAU * u.rate * t + Math.PI / 2));
      }
      let a = L[i], b = R[i];
      for (let j = 0; j < 6; j++) { // y = g x + z; z = x - g y (lfilter's DF2T)
        let y = gl * a + st[j]; st[j] = a - gl * y; a = y;
        y = gr * b + st[6 + j]; st[6 + j] = b - gr * y; b = y;
      }
      L[i] = L[i] * (1 - u.mix) + 0.5 * (L[i] + a) * u.mix; R[i] = R[i] * (1 - u.mix) + 0.5 * (R[i] + b) * u.mix;
    }
  }

  // Transport position of sample i of this block, in samples since the loop began.
  pos(i) { return (globalThis.currentFrame ?? this.n) + i - this.clock; }

  // effects._gate: the pattern's 16th steps from the loop start, edges
  // smoothed by a normalised Hann window (np.convolve, "same")
  gate(s, L, R, N) {
    const u = s.u, P = s.pat.length * s.step, K = s.kernel, w = s.w;
    const at = (p) => s.pat[Math.floor((((p % P) + P) % P) / s.step)];
    for (let i = 0; i < N; i++) {
      const p = this.pos(i), q = ((p % s.step) + s.step) % s.step;
      let env = at(p);
      if (q < w || q >= s.step - w) { env = 0; for (let j = 0; j < K.length; j++) env += K[j] * at(p + j - w); }
      const g = 1 - u.depth * (1 - env);
      L[i] *= g; R[i] *= g;
    }
  }

  // effects._sidechain on the clock: a duck at every trigger (a pump's
  // every `period` s; a sidechain's kick hits, every drum loop), each
  // at 1 - depth on the trigger and recovering along (k / (seg - 1)) **
  // 0.55 over `release`; the way down is a half cosine over the `attack`
  // before the trigger (a clock is known ahead; a one-sample fall clicks)
  duck(s, L, R, N) {
    const u = s.u, sr = sampleRate, seg = s.seg, att = s.att;
    const period = u.kind === "pump" ? u.period : u.loop, hits = u.kind === "pump" ? [0] : u.hits;
    if (!hits.length || !(period > 0)) return;
    for (let i = 0; i < N; i++) {
      const p = this.pos(i);
      let g = 1;
      for (let r = Math.floor((p - seg) / (period * sr)) - 1; r <= Math.floor((p + att) / (period * sr)) + 1; r++) {
        for (const h of hits) {
          const t = r * period + h, k = p - (t === 0 ? 1 : Math.round(t * sr)); // core.n_samples(0) is 1
          if (k >= 0 && k < seg) g = Math.min(g, 1 - u.depth * (1 - Math.pow(k / Math.max(seg - 1, 1), 0.55)));
          else if (k < 0 && k >= -att) g = Math.min(g, 1 - u.depth * (0.5 - 0.5 * Math.cos(Math.PI * (k + att) / att)));
        }
      }
      L[i] *= g; R[i] *= g;
    }
  }
}
registerProcessor("synthlab-fx", SynthlabFx);
