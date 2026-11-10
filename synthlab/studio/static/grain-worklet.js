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
// Budget: <= 60 code lines.

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

  globalThis.synthlabGrains = { on, read };
})();
