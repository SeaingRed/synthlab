// synthlab/studio/static/grain-worklet.js
// The live voice's sampler in "formant" mode: sampler._psola, grain for
// grain, over the buffers sampler.live_payload prepares (the same sources
// the tape read in sampler-worklet.js steps through). Grains two root
// periods long, Hann-windowed, read at the source's own rate from positions
// snapped to whole root periods of a source clock that advances at
// 1 / stretch, laid one *played* period apart — so the pitch moves and the
// grain content (the timbre) and the note's length do not.
// Loaded into the AudioWorklet scope beside voice-worklet.js and
// sampler-worklet.js (livefx.js adds all three): sampler-worklet.js's
// `read` hands a copy over when `synthlabGrains.on(T)`.
// **The engine reads the future, by one root period**: a grain centred on
// output sample ci starts sounding at ci - half (half = one root period)
// and `_psola` takes the pitch at ci, which sets that grain's gain and
// where the next one goes. A grain is scheduled here the moment its first
// sample is due, on the pitch at its centre — this block's own value when
// the centre falls in it, else `ahead`: the voice's control pass run
// forward in closed form (glide, vibrato, the pitch envelope, the bend
// where it stands). So a steady pitch and every motion the voice makes by
// itself are the engine's samples (the parity corpus: 0.00 dB), and what a
// player does mid-note (the wheel, a legato bend, a slider) reaches the
// schedule up to one root period late — after which the grain train runs
// at the right spacing from a different phase than a render of the
// recorded gesture, as the engine's own does when the gesture lands one
// render quantum later. What `ahead` cannot foresee (routes onto the pitch,
// cross-FM) never gets here: paramui.py's gate.
// A context at another rate than the engine's (`ratio` = 44100 / its rate)
// plays the engine's grains in time, not in samples: g / ratio samples
// long, read `ratio` source samples apart.
// tests/studio/test_live_sampler.py holds this file to `_psola` sample for
// sample; the parity corpus runs it inside the voice on both engines.
// The granular oscillator (osc1 or osc2 "granular") is here too (`cloud`):
// granular.render grain for grain over the sample as stored (the payload's
// `raw` source) — onsets floor(k sr / density), each grain's three draws
// hash_u(seed, 3k + i) from crc32("grain:<MIDI>:<copy>") (voice-worklet.js
// hands its crc32 and drift_u over as `synthlabHash`), its place, rate and
// direction fixed at its first sample, the window and the cloud's gain from
// the settings alone. Streamed: a copy keeps its grains still sounding.
// tests/studio/test_live_granular.py holds it to the engine sample for sample.
// Budget: <= 121 code lines (60 -> 121: the granular cloud — its region,
// window, seeds and draws, the grains it keeps sounding across blocks).

(() => {
  const num = (x, d) => { const y = Number(Array.isArray(x) ? x[0] : x); return Number.isFinite(y) ? y : d; };
  // Python's round(): halves go to the even neighbour
  const rint = (x) => { const f = Math.floor(x), d = x - f; return d > 0.5 || (d === 0.5 && f % 2) ? f + 1 : f; };

  const on = (T) => T.sampler_mode === "formant";

  // The pitch (Hz) d samples past the block the voice P just ran for v: the
  // terms of its control pass (voice-worklet.js ctl()) from where that left
  // them — v.freq and v.pv are the next sample's glide and the last one's
  // vibrato phase. A wheel or slider on the move counts where this quantum
  // leaves it.
  function ahead(P, v, d) {
    const T = P.T, C = P.cur, sr = sampleRate, n = v.n + d, vd = C.vibrato_depth;
    const bst = C.pitch_bend * C.bend_range + C.wheel_st;
    let f = v.glideN > 0 ? (d >= v.glideN ? v.target : v.freq * Math.pow(v.glideR, d)) : v.freq;
    if (vd > 0) f *= Math.pow(2, Math.sin(2 * Math.PI * (v.pv + (d + 1) * T.vibrato_rate / sr)) * vd * Math.min(1, Math.max(0, (n / sr - T.vibrato_delay) / 0.3)) / 12);
    if (T.pitch_env) f *= Math.pow(2, T.pitch_env * Math.exp(n * (-5 / (Math.max(T.pitch_env_decay, 1e-3) * sr))) / 12);
    return bst ? f * Math.pow(2, bst / 12) : f;
  }

  // a copy's grain state: the engine's grain length g (source samples, two
  // root periods), its length here, the window, and the two clocks — `at`,
  // where the next grain is centred, and the source clock `sum` (numpy's
  // cumsum of the per-sample step, summed as it sums it) at sample `si`
  function init(r, ratio) {
    const pSrc = ratio * sampleRate / r.root, g = Math.floor(Math.min(Math.max(2 * pSrc, 32), 8192));
    const n = Math.max(2, Math.round(g / ratio)), win = new Float64Array(n);
    for (let i = 0; i < n; i++) win[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / n);
    return { pSrc, n, half: n >> 1, win, at: 0, t: 0, sum: 0, si: 0, done: false, live: [] };
  }

  // c samples of one copy of voice v (r: its source, start and Hz -> speed
  // factor, set up by sampler-worklet.js) at the pitches Fq into out / outR
  function read(r, T, v, P, Fq, out, outR, c, ratio) {
    const G = (r.g ??= init(r, ratio)), { buf, bufR, lo, hi, end, looping, s0 } = r.src;
    const { n, half, win, pSrc, live } = G, last = buf.length - 2, span = hi - lo, t0 = G.t;
    const step = ratio / Math.min(Math.max(num(T.sampler_stretch, 1), 0.05), 16);
    const mult = Fq[c - 1] / P.F[c - 1]; // this copy's octave and detune over the voice's pitch
    if (!c) return; // a voice ending on a block edge
    out.fill(0, 0, c); outR.fill(0, 0, c);
    // every grain whose first sample is due in this block
    while (!G.done && Math.floor(G.at) - half < t0 + c) {
      const ci = Math.floor(G.at), fr = G.at - ci, i = ci - t0, hz = i < c ? Fq[i] : mult * ahead(P, v, i - c);
      for (; G.si < ci; G.si++) G.sum += step;
      const centre = s0 + rint(((s0 + G.sum) - s0) / pSrc) * pSrc;
      if (!looping && centre >= end - 1) { G.done = true; break; } // _Source.done: the train ends
      const pOut = sampleRate / Math.max(hz * r.k * r.root / ratio, 1), ov = n / (2 * pOut);
      // grains a period apart add incoherently: a downshift's gaps are
      // made up by the square root of the overlap (sampler._psola)
      live.push({ a: ci - half, centre, fr, gain: ov < 1 ? 1 / Math.sqrt(ov) : 1 });
      G.at += Math.max(pOut, 1);
    }
    let keep = 0;
    for (const q of live) {
      const { centre, fr, gain } = q, o = q.a - t0;
      for (let i = Math.max(0, o), e = Math.min(c, o + n); i < e; i++) {
        const j = i - o;
        let x = centre + (j - half - fr) * ratio;
        if (looping && x >= hi) x = lo + ((x - lo) % span);
        if (x < 0 || (!looping && x > end - 1)) continue;
        const k = Math.min(Math.max(Math.floor(x), 0), Math.max(0, last)), f = Math.min(Math.max(x - k, 0), 1);
        const k1 = Math.min(k + 1, last + 1), w = win[j] * gain;
        out[i] += (buf[k] * (1 - f) + buf[k1] * f) * w;
        if (bufR) outR[i] += (bufR[k] * (1 - f) + bufR[k1] * f) * w;
      }
      if (o + n > c) live[keep++] = q; // still sounding past this block
    }
    live.length = keep;
    if (!bufR) outR.set(out.subarray(0, c));
    G.t = t0 + c;
  }

  // granular.region: the span, or the loop inside it (at least 64 long)
  const frac = (x, d) => Math.min(Math.max(num(x, d), 0), 1);
  function region(L, T) {
    let r0 = Math.floor(frac(T.sampler_start, 0) * L), r1 = Math.floor(frac(T.sampler_end, 1) * L);
    if (T.sampler_loop === "on") {
      const lo = Math.max(Math.floor(frac(T.sampler_loop_start, 0) * L), r0), hi = Math.min(Math.floor(frac(T.sampler_loop_end, 1) * L), r1);
      if (hi - lo >= 64) { r0 = lo; r1 = hi; }
    }
    return r1 - r0 >= 4 ? [r0, r1] : null;
  }
  // does a granular oscillator over the buffers m sound (granular.render's stereo)?
  const sounds = (m, T) => !!m && m.sources.length > 0 && !!region(m.sources[0].n, T);

  // granular.window over g samples, its mean square: one per (g, shape), shared
  let wc = { key: "" };
  function windowOf(g, shape) {
    const key = g + ":" + shape;
    if (wc.key === key) return wc;
    const taper = 0.5 - (0.5 - 0.05) * shape, m = Math.max(1, taper * g), w = new Float64Array(g);
    const rise = (i) => 0.5 - 0.5 * Math.cos(Math.PI * Math.min(i, m) / m);
    let ms = 0;
    for (let j = 0; j < g; j++) { w[j] = Math.min(rise(j + 0.5), rise(g - j - 0.5)); ms += w[j] * w[j]; }
    return (wc = { key, w, ms: ms / g });
  }

  // c samples of one granular copy (slot j, seed copy `copy`: the unison
  // voice, osc2's + 100) of voice v at the pitches Fq into out / outR
  function cloud(m, T, v, j, copy, Fq, out, outR, c, P) {
    out.fill(0, 0, c); outR.fill(0, 0, c);
    const src = m?.sources[0], r = src && region(src.n, T);
    if (!r) return;
    let G = v.sp[j];
    if (!G?.cloud) { // the note's seed: its own key (synth.Synth.note's key_hz), the copy
      const key = Math.max(v.target, 1e-3), midi = Math.floor(69 + 12 * Math.log2(Math.max(key, 1e-6) / 440) + 0.5);
      G = v.sp[j] = { cloud: true, seed: globalThis.synthlabHash.crc32(`grain:${midi}:${copy}`), key, k: 0, t: 0, hop: 0, live: [] };
    }
    const sr = sampleRate, H = globalThis.synthlabHash.driftU, { buf, bufR } = src, L = src.n, [r0, r1] = r, span = r1 - r0;
    const g = Math.max(4, rint(Math.min(Math.max(num(T.grain_size, 0.08), 0.005), 1) * sr));
    const dens = Math.min(Math.min(Math.max(num(T.grain_density, 20), 1), 500), 256 * sr / g), hop = sr / dens;
    const win = windowOf(g, Math.min(Math.max(num(T.grain_shape, 0), 0), 1)), w = win.w;
    const gain = 1 / Math.sqrt(Math.max(1, dens * g / sr * win.ms));
    const spray = Math.min(Math.max(num(T.grain_spray, 0), 0), 1), ps = Math.min(Math.max(num(T.grain_pitch_spray, 0), 0), 12);
    const scan = Math.min(Math.max(num(T.grain_scan, 0), -2), 2), dir = T.grain_dir, top = 1 - Number.EPSILON / 2;
    const rel = T.sampler_keytrack !== "off" ? 440 * Math.pow(2, (num(T.sampler_root, 60) - 69) / 12) : G.key;
    const routed = !!P.idx && "grain_pos" in P.idx, t0 = G.t, live = G.live;
    // a density moved mid-note: the grains go on as if the note had always had it
    if (hop !== G.hop) { if (G.hop) G.k = Math.ceil(t0 / hop); G.hop = hop; }
    for (let a = Math.floor(G.k * hop); a < t0 + c; a = Math.floor(++G.k * hop)) {
      const i = Math.max(0, a - t0), k3 = 3 * G.k, x = routed ? P.GP[i] : num(T.grain_pos, 0);
      const at = Number.isFinite(x) ? Math.min(Math.max(x, 0), top) : 0;
      let q = (at + scan * (a / sr) / (span / m.sr) + spray * (H(G.seed, k3) - 0.5)) % 1;
      if (q < 0) q += 1; // np.mod
      let rate = Fq[i] / rel * m.ratio;
      if (ps > 0) rate = rate * Math.pow(2, ps * (2 * H(G.seed, k3 + 1) - 1) / 12);
      const back = dir === "reverse" || (dir === "both" && H(G.seed, k3 + 2) < 0.5);
      live.push({ a, first: r0 + q * span, rate, back, g, w });
    }
    let keep = 0;
    for (const q of live) { // granular._read: linear, silent outside the recording
      const o = q.a - t0, gq = q.g, wq = q.w;
      for (let i = Math.max(0, o), e = Math.min(c, o + gq); i < e; i++) {
        const jj = i - o, pos = q.first + q.rate * (q.back ? gq - 1 - jj : jj);
        if (!(pos >= 0 && pos <= L - 1)) continue;
        const k = Math.min(Math.max(Math.floor(pos), 0), L - 2), f = Math.min(Math.max(pos - k, 0), 1), ww = wq[jj];
        out[i] += (buf[k] * (1 - f) + buf[k + 1] * f) * ww;
        if (bufR) outR[i] += (bufR[k] * (1 - f) + bufR[k + 1] * f) * ww;
      }
      if (o + gq > c) live[keep++] = q; // still sounding past this block
    }
    live.length = keep;
    if (gain !== 1) for (let i = 0; i < c; i++) { out[i] *= gain; outR[i] *= gain; }
    if (!bufR) outR.set(out.subarray(0, c));
    G.t = t0 + c;
  }

  globalThis.synthlabGrains = { on, read, cloud, sounds, region };
})();
