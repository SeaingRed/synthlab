// synthlab/studio/static/talk-worklet.js
// The live talkbox: a channel vocoder in the AudioWorklet, so
// a voice into the mic speaks through the keys as they are played — the
// robot / talkbox voice of filter house, live. The offline vocoder
// (effects.vocoder, the Voice lab) bakes a take; this one plays.
//   input 0: the carrier — the live keys (talkbox.js routes the key bus
//            here through audio.js's setLiveOut)
//   input 1: the modulator — the mic
//   output : the vocoded voice, stereo (the carrier's channels kept)
// `TalkDSP` is the whole sound: `bands` log-spaced band-passes from 120 Hz
// to 7 kHz, each run on both inputs (two RBJ constant-0-dB band-passes in
// series — 4 poles, as effects.vocoder's bands: one pair leaked a 1 kHz
// voice into the 300 Hz keys, 25 dB apart now), the mic's band rectified
// into an envelope (3 ms up, `release` down) that is the gain of the
// carrier's band. `shift` (semitones) moves the carrier's bands off the
// mic's — the formants move, the pitch stays the carrier's. `air` is the
// mic's own top (high-passed at 4.5 kHz) laid over it, so the consonants —
// which a carrier has no noise for — come through; `dry` blends the keys
// back; `level` is the output gain. `carrier` picks what speaks: "keys"
// (input 0), "robot" (its own saw at `note`, MIDI — talk without playing)
// or "noise" (a whisper), the latter two added to whatever keys sound.
// The processor also reports the mic's level (~15 a second, for the
// switch's meter) and, while asked to record, posts every output block.
// tests/studio/test_talkbox.py runs TalkDSP under node.
// Budget: <= 125 code lines.

(() => {
  const LO = 120, HI = 7000;
  function biquad(type, f, q, sr) {
    const w = (2 * Math.PI * Math.min(f, sr * 0.45)) / sr, al = Math.sin(w) / (2 * q), cw = Math.cos(w), a0 = 1 + al;
    const b = type === "band" ? [al, 0, -al] : [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2];
    return { b0: b[0] / a0, b1: b[1] / a0, b2: b[2] / a0, a1: (-2 * cw) / a0, a2: (1 - al) / a0, z1: 0, z2: 0 };
  }
  // transposed direct form II, as scipy's lfilter runs one
  function step(s, x) {
    const y = s.b0 * x + s.z1;
    s.z1 = s.b1 * x - s.a1 * y + s.z2;
    s.z2 = s.b2 * x - s.a2 * y;
    return y;
  }
  const pair = (f, q, sr) => [biquad("band", f, q, sr), biquad("band", f, q, sr)];
  const step2 = (s, x) => step(s[1], step(s[0], x));
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  // the mic's pick-up (a whisper must still get through): `gain` dB before anything hears it, `auto` (0-1) lifts a quiet
  // voice toward speaking level (up to +24 dB, never down), `gate` dB shuts
  // what is under it — the room's hiss a boost would make speak. At their
  // defaults (0, 0, -90) the voice reaches the bands untouched, as before
  const DEFAULTS = { bands: 16, shift: 0, air: 0.3, dry: 0, release: 0.04, level: 1, carrier: "keys", note: 48, gain: 0, auto: 0, gate: -90 };
  const TARGET = 0.1, MAX_LIFT = 16; // the auto level: a voice's mean |x| it aims for, its most lift
  class TalkDSP {
    constructor(sr, opts = {}) {
      this.sr = sr;
      Object.assign(this, DEFAULTS);
      this.phase = 0; this.seed = 22222; this.lev = 0; this.open = 0;
      this.top = biquad("high", 4500, 0.707, sr);
      this.up = 1 - Math.exp(-1 / (0.003 * sr));
      const k = (s) => 1 - Math.exp(-1 / (s * sr));
      this.levUp = k(0.01); this.levDown = k(0.25); this.gateOpen = k(0.002); this.gateShut = k(0.08);
      this.set(opts, true);
    }
    // a change of `bands` or `shift` lays the bands again; the rest is live
    set(p, build = false) {
      if (!p) return;
      const n = (k, lo, hi) => { if (Number.isFinite(p[k])) this[k] = clamp(p[k], lo, hi); };
      const was = [this.bands, this.shift];
      n("bands", 4, 32); n("shift", -12, 12); n("air", 0, 1); n("dry", 0, 1); n("release", 0.01, 0.4); n("level", 0, 2); n("note", 24, 84);
      n("gain", 0, 36); n("auto", 0, 1); n("gate", -90, -20);
      this.gin = 10 ** (this.gain / 20); this.gth = this.gate <= -90 ? 0 : 10 ** (this.gate / 20);
      if (Number.isFinite(p.mix)) this.dry = clamp(p.mix, 0, 1); // the first version's name for `dry`
      if (["keys", "robot", "noise"].includes(p.carrier)) this.carrier = p.carrier;
      this.bands = Math.round(this.bands);
      this.down = 1 - Math.exp(-1 / (this.release * this.sr));
      if (build || was[0] !== this.bands || was[1] !== this.shift) this.lay();
    }
    lay() {
      const B = this.bands, ratio = (HI / LO) ** (1 / (B - 1)), q = Math.sqrt(ratio) / (ratio - 1);
      const lift = 2 ** (this.shift / 12); // the carrier's bands moved: the formants move
      this.mod = []; this.car = [[], []]; this.env = new Float64Array(B);
      for (let k = 0; k < B; k++) {
        const f = LO * ratio ** k;
        this.mod.push(pair(f, q, this.sr));
        for (const ch of this.car) ch.push(pair(f * lift, q, this.sr));
      }
      this.makeup = Math.sqrt(B) * 2;
    }
    // the carrier's own voice: a saw at `note`, or seeded white noise
    own() {
      if (this.carrier === "robot") {
        this.phase += (440 * 2 ** ((this.note - 69) / 12)) / this.sr;
        this.phase -= Math.floor(this.phase);
        return (2 * this.phase - 1) * 0.5;
      }
      if (this.carrier === "noise") {
        this.seed = (this.seed * 16807) % 2147483647;
        return (this.seed / 2147483647 - 0.5) * 0.8;
      }
      return 0;
    }
    // carrier: [left, right] Float32Arrays (right may be the left); mic: one channel
    run(carL, carR, mic, outL, outR) {
      const n = outL.length, env = this.env, B = this.bands;
      let power = 0;
      for (let i = 0; i < n; i++) {
        let m = (mic ? mic[i] : 0) * this.gin;
        const a = Math.abs(m); // the voice's level (10 ms up, 250 ms down): the gate and the auto level read it
        this.lev += (a - this.lev) * (a > this.lev ? this.levUp : this.levDown);
        if (this.gth) { const t = this.lev > this.gth ? 1 : 0; this.open += (t - this.open) * (t > this.open ? this.gateOpen : this.gateShut); m *= this.open; }
        if (this.auto) m *= Math.min(MAX_LIFT, Math.max(1, TARGET / Math.max(this.lev, 1e-6))) ** this.auto;
        const o = this.own(), kl = carL ? carL[i] : 0, kr = carR ? carR[i] : kl;
        const cl = kl + o, cr = kr + o;
        power += m * m;
        let l = 0, r = 0;
        for (let k = 0; k < B; k++) {
          const level = Math.abs(step2(this.mod[k], m));
          env[k] += (level - env[k]) * (level > env[k] ? this.up : this.down);
          l += step2(this.car[0][k], cl) * env[k];
          r += step2(this.car[1][k], cr) * env[k];
        }
        const air = step(this.top, m) * this.air;
        outL[i] = (l * this.makeup + air + kl * this.dry) * this.level;
        outR[i] = (r * this.makeup + air + kr * this.dry) * this.level;
      }
      return Math.sqrt(power / Math.max(1, n)); // the mic's level this block
    }
  }
  globalThis.TalkDSP = TalkDSP;
  if (typeof registerProcessor !== "function") return;
  registerProcessor("talkbox", class extends AudioWorkletProcessor {
    constructor(o) {
      super();
      this.dsp = new TalkDSP(sampleRate, o?.processorOptions || {});
      this.live = true; this.rec = false; this.peak = 0; this.blocks = 0;
      // {stop}: released; {rec: true|false}: post the output; the rest: settings
      this.port.onmessage = (e) => {
        const d = e.data || {};
        if (d.stop) this.live = false;
        else if ("rec" in d) this.rec = !!d.rec;
        else this.dsp.set(d);
      };
    }
    process(inputs, outputs) {
      if (!this.live) return false;
      const car = inputs[0] || [], mic = inputs[1] || [], out = outputs[0], R = out[1] || out[0];
      const level = this.dsp.run(car[0], car[1], mic[0], out[0], R);
      this.peak = Math.max(this.peak, level);
      if (++this.blocks % 24 === 0) { this.port.postMessage({ level: this.peak }); this.peak = 0; }
      if (this.rec) this.port.postMessage({ L: out[0].slice(), R: R.slice() });
      return true;
    }
  });
})();
