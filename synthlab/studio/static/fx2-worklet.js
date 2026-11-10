// synthlab/studio/static/fx2-worklet.js
// More FX units, each effects.py's own
// algorithm sample by sample: the auto-filter (`autofilter_cutoff` into
// core.biquad's swept state-variable filter, a coefficient set every 64
// samples), the tremolo / auto-pan, tape saturation, the ping-pong delay,
// the formant shifter (32 followed bands, a Simper bell each in series,
// its gain set every 64 samples), the frequency shifter (the Hilbert
// pair), the exciter and the rack's vocoder (its own saw chord the
// carrier). The
// spring is a ConvolverNode on effects.spring_ir (livefx.js), not here.
// The mix's dynamics here too — the OTT (three
// Linkwitz-Riley bands built at this context's rate, each pulled toward
// the middle), the limiter (a sliding minimum, a release, a ramp, its
// lookahead a delay) and the transient shaper (peak followers): 150 -> 205.
// Loaded into the AudioWorklet scope beside fx-worklet.js (livefx.js adds
// it; fx-worklet.js and vox-worklet.js are full): its SynthlabFx calls
// `synthlabFx2.init` / `.run` / `.quiet` for a unit of one of `kinds`, and
// this reads its biquads, history ring and wander (`synthlabFx`). Units
// arrive resolved by fxlive.live_chain, the engine's constants (and the
// fixed filters' coefficients) with them. The two LFO units run on the
// transport clock (the processor's `pos`: the loop's bar), every other
// modulation on the processor's own sample count, as the engine's on a
// buffer's. tests/studio/test_live_fx.py runs this under node against
// effects.py.
// Budget: <= 205 code lines.

(() => {
  const TAU = 2 * Math.PI;
  const kinds = new Set(["autofilter", "tremolo", "tape", "pingpong", "formant_shift", "freqshift", "exciter", "vocoder_fx",
    "ott", "limiter", "transient"]); // the last three: the dynamics
  const F = () => globalThis.synthlabFx; // fx-worklet.js's Hist, rbj, biq, wander
  const pole = (t, sr) => Math.exp(-1 / (t * sr));
  // effects._envelope_follower, one sample: the larger of a fast and a slow one-pole on |x|
  const follower = (att, rel, sr) => ({ a: pole(att, sr), r: pole(rel, sr), up: 0, dn: 0 });
  const follow = (f, x) => { f.up = (1 - f.a) * x + f.a * f.up; f.dn = (1 - f.r) * x + f.r * f.dn; return Math.max(f.up, f.dn); };
  // a biquad from the engine's own (b, a), its state carried from `old` (a slider drag keeps it ringing)
  const coef = ([b0, b1, b2], [, a1, a2], old) => ({ b0, b1, b2, a1, a2, z1: old?.z1 || 0, z2: old?.z2 || 0 });
  const bp = (fc, q, sr, old) => Object.assign(F().rbj("bandpass", fc, q, sr), old ? { z1: old.z1, z2: old.z2 } : {});
  const db = (e) => 20 * Math.log10(Math.max(e, 1e-9)); // effects._db
  // effects.allpass_coeffs: RBJ's all-pass, the phase a Linkwitz-Riley pair sums to
  function allpass(fc, q, sr) {
    const w0 = TAU * Math.min(Math.max(fc, 20), sr * 0.49) / sr, al = Math.sin(w0) / (2 * Math.max(q, 0.05)), cw = Math.cos(w0), a0 = 1 + al;
    return { b0: (1 - al) / a0, b1: -2 * cw / a0, b2: (1 + al) / a0, a1: -2 * cw / a0, a2: (1 - al) / a0, z1: 0, z2: 0 };
  }
  const keep = (f, old) => Object.assign(f, old ? { z1: old.z1, z2: old.z2 } : {});
  // effects.lfo_wave: 0..1, at 0 on the bar
  function wave(ph, shape, sm) {
    if (shape === "triangle") return 1 - Math.abs(1 - 2 * ph);
    if (shape === "saw up") return ph;
    if (shape === "saw down") return 1 - ph;
    if (shape === "square") return ph >= 0.5 ? 1 : 0;
    const c = Math.cos(TAU * ph);
    return shape === "smooth square" ? 0.5 - 0.5 * Math.tanh(sm * c) / Math.tanh(sm) : 0.5 - 0.5 * c;
  }
  const phase = (rate, t, off) => { const p = rate * t + off; return p - Math.floor(p); }; // effects.lfo_phase
  // effects.tick_noise: murmur3's finaliser on 2 x index + channel, -1..1
  function tick(n, c) {
    let h = Math.imul((n * 2 + c) >>> 0, 0x9e3779b1) >>> 0;
    h = Math.imul((h ^ (h >>> 16)) >>> 0, 0x85ebca6b) >>> 0;
    h = Math.imul((h ^ (h >>> 13)) >>> 0, 0xc2b2ae35) >>> 0;
    return ((h ^ (h >>> 16)) >>> 0) / 2147483648 - 1;
  }
  // core._polyblep, then effects.blep_saw at the phase t
  const blep = (t, dt) => (t > 1 - dt ? ((t - 1) / Math.max(dt, 1e-12)) ** 2 + 2 * (t - 1) / Math.max(dt, 1e-12) + 1
    : t < dt ? 2 * (t / Math.max(dt, 1e-12)) - (t / Math.max(dt, 1e-12)) ** 2 - 1 : 0);
  // a TPT SVF step on state z (core._swept_biquad: low v2, band v1); c = [k, a1, a2, a3]
  function svf(z, o, x, c) {
    const v3 = x - z[o + 1], v1 = c[1] * z[o] + c[2] * v3, v2 = z[o + 1] + c[2] * z[o] + c[3] * v3;
    z[o] = 2 * v1 - z[o]; z[o + 1] = 2 * v2 - z[o + 1];
    return [v1, v2];
  }
  function svfCoef(fc, q, sr) { // core._svf_blocks for one block's cutoff
    const g = Math.tan(Math.PI * Math.min(sr * 0.49, Math.max(20, fc)) / sr), k = 1 / Math.max(q, 0.05), a1 = 1 / (1 + g * (g + k));
    return [k, a1, g * a1, g * g * a1];
  }

  function init(u, old, sr) {
    const s = { tail: 4096 }, k = u.kind, o = old.u?.kind === k ? old : {};
    if (k === "autofilter") Object.assign(s, { z: o.z || new Float64Array(4), f: follower(u.attack, u.release, sr), tail: sr });
    else if (k === "tape") {
      Object.assign(s, { hL: o.hL || new (F().Hist)(), hR: o.hR || new (F().Hist)(), f: follower(...u.hiss_follow.slice(0, 2), sr) });
      s.pk = u.peak ? [0, 1].map((c) => coef(...u.peak, o.pk?.[c])) : null;
      s.top = [0, 1].map((c) => coef(...u.top, o.top?.[c])); s.lo = [0, 1].map((c) => coef(...u.low, o.lo?.[c])); s.tail = 8192;
    } else if (k === "pingpong") {
      const d = Math.max(1, Math.round(u.time * sr)), keep = o.d === d, fb = Math.min(Math.max(u.feedback, 1e-9), 0.999);
      Object.assign(s, keep ? { d, b: o.b, i: o.i } : { d, b: [0, 1, 2].map(() => new Float64Array(d)), i: 0 });
      s.f = [0, 1].map((c) => [F().rbj("highpass", u.lowcut, 0.707, sr), F().rbj("lowpass", u.highcut, 0.707, sr)]
        .map((f, j) => Object.assign(f, o.f ? { z1: o.f[c][j].z1, z2: o.f[c][j].z2 } : {})));
      s.tail = d * (Math.ceil(Math.log(1e-6 / 4) / Math.log(fb)) + 3) + 2048;
    } else if (k === "formant_shift") { // the bank (fixed: its followers and the bells' states kept), the bells' gains anew
      Object.assign(s, { b: o.b || u.centres.map((f) => ({ e: [bp(f, u.q, sr), bp(f, u.q, sr)], f: follower(...u.follow, sr) })),
        z: o.z || new Float64Array(4 * u.centres.length), E: o.E || new Float64Array(u.centres.length), c: null, tail: 16384 });
    } else if (k === "freqshift") {
      const sec = (j) => u.hilbert.map((path) => path.map((a, i) => coef([a * a, 0, -1], [1, 0, -a * a], o.sec?.[j][path === u.hilbert[0] ? 0 : 1][i])));
      const d = Math.max(1, Math.round(u.loop * sr)), fb = Math.min(Math.max(u.feedback, 1e-9), 0.999);
      Object.assign(s, { sec: [0, 1].map(sec), held: o.held || [0, 0], d, y: o.y?.[0].length === d ? o.y : [new Float64Array(d), new Float64Array(d)], i: o.i || 0 });
      s.tail = 16384 + d * (Math.ceil(Math.log(1e-6 / 4) / Math.log(fb)) + 1);
    } else if (k === "exciter") {
      s.f = [0, 1, 2, 3].map((j) => Object.assign(F().rbj("highpass", u.freq, 0.707, sr), o.f ? { z1: o.f[j].z1, z2: o.f[j].z2 } : {}));
    } else if (k === "vocoder_fx") {
      const same = o.u?.centres.length === u.centres.length && o.u?.ratio === u.ratio;
      s.b = u.centres.map((f, j) => (same ? o.b[j] : { m: [bp(f, u.q, sr), bp(f, u.q, sr)], c: [bp(f * u.ratio, u.q, sr), bp(f * u.ratio, u.q, sr)], f: follower(...u.follow, sr) }));
      s.tail = sr;
    } else if (k === "ott") { // per channel: the low split's LP, LP, HP, HP, the high split's the same, the low band's all-pass
      const [f1, f2] = u.split, kinds4 = ["lowpass", "lowpass", "highpass", "highpass"];
      s.x = [0, 1].map((c) => [...kinds4.map((kd, j) => keep(F().rbj(kd, f1, u.q, sr), o.x?.[c][j])),
        ...kinds4.map((kd, j) => keep(F().rbj(kd, f2, u.q, sr), o.x?.[c][4 + j])), keep(allpass(f2, u.q, sr), o.x?.[c][8])]);
      s.f = u.times.map(([at, rl], j) => Object.assign(follower(at * u.time, rl * u.time, sr), o.f ? { up: o.f[j].up, dn: o.f[j].dn } : {}));
      s.tail = 16384 + Math.ceil(14 * u.times[0][1] * u.time * sr);
    } else if (k === "limiter") { // the deque of the last w + 1 gains, the release, the ramp's ring, the delay
      const w = Math.round(Math.max(0, u.lookahead) * sr);
      Object.assign(s, o.w === w ? { w, q: o.q, sm: o.sm, box: o.box, sum: o.sum, d: o.d, n: o.n } : { w, sm: 1, sum: w + 1, n: 0,
        q: { v: new Float64Array(w + 1), t: new Float64Array(w + 1), h: 0, len: 0 }, box: new Float64Array(w + 1).fill(1),
        d: [new Float64Array(Math.max(1, w)), new Float64Array(Math.max(1, w))] });
      s.r = Math.exp(-1 / Math.max(u.release * sr, 1)); s.c = 10 ** (u.ceiling / 20);
      s.tail = w + Math.ceil(14 * u.release * sr) + 2;
    } else if (k === "transient") { // the peak followers (the attack's, the long, the short) and the two one-poles
      Object.assign(s, { r: [u.peak, u.long, u.short].map((x) => Math.exp(-1 / (x * sr))), a: [pole(u.slow, sr), pole(u.smooth, sr)],
        pk: o.pk || [0, 0, 0], lag: o.lag || 0, held: o.held || 0, tail: Math.ceil(14 * u.long * sr) });
    }
    return s;
  }

  // One unit over the block, in place (n0: the processor's first sample's
  // index; fx: the processor, whose `pos` is the transport's)
  function run(s, L, R, N, n0, fx) {
    const u = s.u, k = u.kind, sr = sampleRate, biq = F().biq;
    for (let i = 0; i < N; i++) {
      const n = n0 + i, x = L[i], y = R[i];
      let a = x, b = y;
      if (k === "autofilter") { // effects.autofilter_cutoff -> the swept SVF, its coefficients every 64 samples
        const lev = follow(s.f, Math.max(Math.abs(x), Math.abs(y)));
        if (n % 64 === 0 || !s.c) {
          const e = u.env, w = wave(phase(u.rate, fx.pos(i) / sr, 0), u.shape, u.smooth);
          const move = (1 - Math.abs(e)) * w + (e !== 0 ? e * Math.min(1, lev * 10 ** (u.sens / 20)) : 0);
          s.c = svfCoef(u.cutoff * 2 ** (u.depth * move), u.q, sr);
        }
        [a, b] = [x, y].map((v, c) => { const [v1, v2] = svf(s.z, 2 * c, v, s.c);
          return u.mode === "lowpass" ? v2 : u.mode === "highpass" ? v - s.c[0] * v1 - v2 : s.c[0] * v1; });
      } else if (k === "tremolo") { // effects._tremolo: the right channel's LFO `phase` degrees on
        const t = fx.pos(i) / sr;
        a = x * (1 - u.depth * wave(phase(u.rate, t, 0), u.shape, u.smooth));
        b = y * (1 - u.depth * wave(phase(u.rate, t, u.phase / 360), u.shape, u.smooth));
        L[i] = a; R[i] = b; continue;
      } else if (k === "tape") { // effects._tape: the wavering head, tanh, the hiss, the bump, the top
        const t = n / sr, [dw, fw, fh] = u.tape_wow, reach = u.wow * dw + u.flutter * fw;
        s.hL.push(x); s.hR.push(y);
        if (reach > 0) { const d = (reach + (u.wow * dw * F().wander(u, t) + u.flutter * fw * Math.sin(TAU * fh * t))) * sr; a = s.hL.at(d); b = s.hR.at(d); }
        const g = 10 ** (u.drive / 20), [, , opens] = u.hiss_follow;
        const lev = u.hiss > 0 ? u.hiss * u.hiss_level * Math.min(1, follow(s.f, Math.max(Math.abs(x), Math.abs(y))) * opens) : 0;
        a = Math.tanh(a * g) / g + (lev ? tick(n, 0) * lev : 0); b = Math.tanh(b * g) / g + (lev ? tick(n, 1) * lev : 0);
        if (s.pk) { a = biq(s.pk[0], a); b = biq(s.pk[1], b); }
        a = biq(s.lo[0], biq(s.top[0], a)); b = biq(s.lo[1], biq(s.top[1], b));
      } else if (k === "pingpong") { // effects._pingpong: the sum onto the left line, left -> right -> left
        const j = s.i, [xb, lb, rb] = s.b, fb = u.feedback, near = (1 + u.width) / 2, far = (1 - u.width) / 2;
        const l = biq(s.f[0][1], biq(s.f[0][0], xb[j] + fb * rb[j])), r = biq(s.f[1][1], biq(s.f[1][0], fb * lb[j]));
        xb[j] = (x + y) * 0.5; lb[j] = l; rb[j] = r; s.i = j + 1 === s.d ? 0 : j + 1;
        a = x + (l * near + r * far); b = y + (r * near + l * far);
      } else if (k === "formant_shift" && u.shift !== 0) { // effects._formant_shift: bells in series, each at the envelope at f / r over its own
        const m = (x + y) * 0.5, E = s.E;
        s.b.forEach((q, j) => { E[j] = follow(q.f, Math.abs(biq(q.e[1], biq(q.e[0], m)))); });
        if (n % u.block === 0 || !s.c) s.c = u.taps.map((t, j) => { if (!t) return null; // a block's gains: the bell's A and A^2 - 1
          const g = Math.min(u.gain[1], Math.max(u.gain[0], (E[t[0]] * (1 - t[1]) + E[t[0] + 1] * t[1] + u.floor) / (E[j] + u.floor))), A = Math.sqrt(g);
          return [...svfCoef(u.centres[j], u.q * A, sr), A * A - 1]; });
        s.c.forEach((c, j) => { if (c) { a += c[4] * c[0] * svf(s.z, 4 * j, a, c)[0]; b += c[4] * c[0] * svf(s.z, 4 * j + 2, b, c)[0]; } });
      } else if (k === "freqshift") { // effects._freqshift: the Hilbert pair, re cos + im sin; fed back `loop` s later
        const th = TAU * u.shift * (n / sr), cs = Math.cos(th), sn = Math.sin(th), j = s.i;
        [a, b] = [x, y].map((v, c) => {
          let p = v + u.feedback * s.y[c][j], q = p;
          for (const f of s.sec[c][0]) p = biq(f, p);
          for (const f of s.sec[c][1]) q = biq(f, q);
          const re = s.held[c]; s.held[c] = p;
          return (s.y[c][j] = re * cs + q * sn);
        });
        s.i = j + 1 === s.d ? 0 : j + 1;
      } else if (k === "exciter") { // effects._exciter: the band over freq through an offset tanh, high-passed again
        const g = u.drive, off = Math.tanh(u.bias), sl = g * (1 - off * off);
        a = x + u.amount * biq(s.f[1], (Math.tanh(biq(s.f[0], x) * g + u.bias) - off) / sl);
        b = y + u.amount * biq(s.f[3], (Math.tanh(biq(s.f[2], y) * g + u.bias) - off) / sl);
        L[i] = a; R[i] = b; continue;
      } else if (k === "ott") { // effects._ott: three bands, each pulled toward the middle, blended with itself at depth
        const split = (X, v) => { const r = biq(X[3], biq(X[2], v)); return [biq(X[8], biq(X[1], biq(X[0], v))), biq(X[5], biq(X[4], r)), biq(X[7], biq(X[6], r))]; };
        const bl = split(s.x[0], x), br = split(s.x[1], y), gains = [u.low, u.mid, u.high], d = u.depth, out = 10 ** (u.output / 20);
        a = 0; b = 0;
        for (let j = 0; j < 3; j++) { // effects.ott_gain, then the blend
          const lev = db(follow(s.f[j], Math.max(Math.abs(bl[j]), Math.abs(br[j]))));
          const dn = Math.min(0, (lev - u.down[0]) * (1 / u.down[1] - 1)), up = Math.min(u.up_max, Math.max(0, (u.up[0] - lev) * (1 - 1 / u.up[1])));
          const m = (1 - d) + d * (10 ** ((u.downward * dn + u.upward * up) / 20) * 10 ** (gains[j] / 20));
          a += bl[j] * m; b += br[j] * m;
        }
        L[i] = a * out; R[i] = b * out; continue;
      } else if (k === "limiter") { // effects._limiter / limiter_gain: the gain each peak needs, held, let go, ramped; the signal delayed
        const g = 10 ** (u.gain / 20), yl = x * g, yr = y * g, w = s.w, q = s.q, cap = w + 1;
        const want = Math.min(1, s.c / Math.max(Math.max(Math.abs(yl), Math.abs(yr)), 1e-9));
        let hold = want;
        if (w) { // the lowest of the last w + 1: a deque rising from its front (the expired out first: it holds w + 1)
          while (q.len && q.t[q.h] < s.n - w) { q.h = (q.h + 1) % cap; q.len--; }
          while (q.len && q.v[(q.h + q.len - 1) % cap] >= want) q.len--;
          q.v[(q.h + q.len) % cap] = want; q.t[(q.h + q.len) % cap] = s.n; q.len++;
          hold = q.v[q.h];
        }
        s.sm = (1 - s.r) * hold + s.r * s.sm;
        let env = Math.min(hold, s.sm);
        if (w) {
          const j = s.n % cap, dj = s.n % w;
          s.sum += env - s.box[j]; s.box[j] = env; env = s.sum / cap;
          a = s.d[0][dj]; b = s.d[1][dj]; s.d[0][dj] = yl; s.d[1][dj] = yr;
        } else { a = yl; b = yr; }
        s.n++; L[i] = a * env; R[i] = b * env; continue;
      } else if (k === "transient") { // effects._transient / transient_db
        const lev = Math.max(Math.max(Math.abs(x), Math.abs(y)), 1e-12), pk = s.pk, top = u.top;
        for (let j = 0; j < 3; j++) pk[j] = Math.max(lev, pk[j] * s.r[j]);
        s.lag = (1 - s.a[0]) * pk[0] + s.a[0] * s.lag;
        s.held = (1 - s.a[1]) * (db(pk[1]) - db(pk[2])) + s.a[1] * s.held;
        const att = Math.min(top, Math.max(0, db(pk[0]) - db(s.lag) - u.floor[0]));
        const sus = att > 0 ? 0 : Math.min(top, Math.max(0, s.held - u.floor[1]));
        const gain = 10 ** ((u.attack * u.scale * att + u.sustain * u.scale * sus + u.output) / 20);
        L[i] = x * gain; R[i] = y * gain; continue;
      } else if (k === "vocoder_fx") { // effects._vocoder_fx: the modulator's bands set its own chord's
        const t = n / sr, m = (x + y) * 0.5;
        let car = 0, out = 0;
        for (const f of u.notes) { const ph = phase(f, t, 0); car += 2 * ph - 1 - blep(ph, f / sr); }
        car /= Math.max(1, u.notes.length);
        if (u.noise > 0) car += u.noise * tick(n, 0);
        s.b.forEach((q, j) => { out += follow(q.f, Math.abs(biq(q.m[1], biq(q.m[0], m)))) * u.tilts[j] * biq(q.c[1], biq(q.c[0], car)); });
        a = b = out;
      }
      L[i] = x * (1 - u.mix) + a * u.mix; R[i] = y * (1 - u.mix) + b * u.mix;
    }
  }

  // Culled (silent in, every tail rung out): the filters and followers at rest
  function quiet(s) {
    const zero = (v) => { if (!v || typeof v !== "object") return; if ("z1" in v) v.z1 = v.z2 = 0; if ("up" in v) v.up = v.dn = 0;
      if (v instanceof Float64Array) v.fill(0); else if (!(v instanceof Object.getPrototypeOf(Float64Array))) Object.values(v).forEach(zero); };
    for (const key of ["z", "f", "pk", "top", "lo", "b", "sec", "y", "E", "x"]) zero(s[key]);
    if (s.held) s.held = Array.isArray(s.held) ? [0, 0] : 0;
    if (s.u.kind === "transient") { s.pk = [0, 0, 0]; s.lag = 0; }
    if (s.u.kind === "limiter") { s.q.len = 0; s.sm = 1; s.box.fill(1); s.sum = s.w + 1; s.d.forEach((v) => v.fill(0)); }
  }

  globalThis.synthlabFx2 = { kinds, init, run, quiet };
})();
