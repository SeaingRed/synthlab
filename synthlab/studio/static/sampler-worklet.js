// synthlab/studio/static/sampler-worklet.js
// The live voice's sampler oscillator: sampler.render in "resample" mode
// with no stretch, over the buffers sampler.live_payload prepares (the
// engine's own sources: declicked, loop crossfade baked, chops reversed).
// Loaded into the AudioWorklet scope beside voice-worklet.js (livefx.js
// adds both modules), which had no room for it: its SynthlabVoice calls
// `synthlabSampler.set` for a "sampler" message and `.read` per block.
// A sample whose channels differ arrives as two (`meta.ch` 2: each source's
// left, then its right) and reads as a stereo pair — the engine's sampler
// oscillator is stereo (n, 2), and the voice sums it as synth._osc_stack
// does. Formant mode is grain-worklet.js (`synthlabGrains`, a third module
// in this scope: `read` hands the copy over); a stretch in "resample" mode
// never gets here (sampler.live_ok).
// A zone map (a multi-sample instrument, `meta.zones`: one source per
// zone) picks its zones once per voice as sampler.pick_zones does — by the
// key, the velocity step and the voice's round-robin strike (`v.rr`, which
// voice-worklet.js counts per key) — reads each as a sample of its own at
// its root and fine tune, and sums them at their weights and gains, as
// sampler._render_zones does; the pick goes back to the page as a "zone"
// message (livefx.js: the zone map lights what sounded).
// tests/studio/test_live_parity_corpus.py runs this file under node
// against the engine, with voice-worklet.js, on both of its engines.
// Budget: <= 64 code lines (35 -> 64: the zone map — its pick, a read per
// zone summed, the report; the voice had no room for it).

(() => {
  // GET /sampler/live/<id>.bin, split by its header: each source's samples
  // per channel, loop points, start and chop pitch; a zone map's zones
  function set(url, meta, data, sr) {
    const ch = meta.ch || 1;
    let o = 0;
    const sources = meta.sources.map((m) => {
      const s = { ...m, buf: data.subarray(o, o + m.n), bufR: ch > 1 ? data.subarray(o + m.n, o + 2 * m.n) : null };
      o += ch * m.n;
      return s;
    });
    return { url, sources, slices: meta.slices, zones: meta.zones || null, stereo: ch > 1, ratio: meta.sr / sr };
  }

  // sampler.pick_zones, line for line: [[zone, weight], ...] for a key, a
  // velocity step (0..127) and a strike
  function pick(zs, key, vel, rr) {
    const c = [];
    zs.forEach((z, i) => { if (z.lo <= key && key <= z.hi && z.vlo <= vel && vel <= z.vhi) c.push(i); });
    if (!c.length) return [];
    const takes = [...new Set(c.map((i) => zs[i].rr))].sort((a, b) => a - b), take = takes[rr % takes.length];
    const sel = c.filter((i) => zs[i].rr === take).sort((a, b) => zs[a].vlo - zs[b].vlo || a - b);
    if (sel.length === 1) return [[sel[0], 1]];
    const [s, h] = sel.slice(-2), lo = zs[h].vlo, hi = zs[s].vhi;
    const t = hi > lo ? Math.min(Math.max((vel - lo) / (hi - lo), 0), 1) : 1;
    return t <= 0 ? [[s, 1]] : t >= 1 ? [[h, 1]] : [[s, Math.cos(t * Math.PI / 2)], [h, Math.sin(t * Math.PI / 2)]];
  }

  // c samples of voice v's copy j into out (left) and outR (right; the
  // left again for a one-channel source): the copy reads its source from
  // the start (the key picks a chop, or the zones), a step of pitch / root
  // (or / key) source samples per sample — read, then step, as the
  // engine's cumsum — wrapping a loop (_Source.read: linear, silent
  // outside the span)
  function read(m, T, v, j, Fq, out, outR, c, P) {
    if (!m) { out.fill(0, 0, c); outR.fill(0, 0, c); return; }
    let r = v.sp[j];
    if (!r) {
      const num = (x, d) => { const y = Number(Array.isArray(x) ? x[0] : x); return Number.isFinite(y) ? y : d; };
      const key = Math.max(v.target, 1e-3), hz = (k) => 440 * Math.pow(2, (k - 69) / 12), root = hz(num(T.sampler_root, 60)), n = m.sources.length;
      if (m.zones) { // a zone map: what sounds is the same for every copy, reported once
        const step = Math.floor(69 + 12 * Math.log2(key / 440) + 0.5), vel = Math.min(127, Math.max(0, Math.floor(v.vel * 127 + 0.5)));
        if (!v.zp) { v.zp = pick(m.zones, step, vel, v.rr || 0); P.port?.postMessage({ type: "zone", key: step, vel, rr: v.rr || 0, zones: v.zp.map((q) => q[0]) }); }
        r = v.sp[j] = { parts: v.zp.filter((q) => m.zones[q[0]].src >= 0).map(([i, w]) => {
          const z = m.zones[i], src = m.sources[z.src], zr = hz(z.root);
          return { src, pos: src.s0, root: zr, k: (T.sampler_keytrack !== "off" ? 1 / zr : 1 / key) * Math.pow(2, z.tune / 1200) * m.ratio, w: w * Math.pow(10, z.gain / 20) };
        }) };
      } else {
        const src = m.slices ? m.sources[((Math.round(12 * Math.log2(key / root)) % n) + n) % n] : m.sources[0];
        const rel = m.slices ? Math.pow(2, src.pitch / 12) / key : T.sampler_keytrack !== "off" ? 1 / root : 1 / key;
        r = v.sp[j] = { src, pos: src.s0, k: rel * m.ratio, root };
      }
    }
    if (!r.parts) return one(r, T, v, P, Fq, out, outR, c, m.ratio);
    out.fill(0, 0, c); outR.fill(0, 0, c);
    if (tmp[0].length < c) tmp = [new Float64Array(c), new Float64Array(c)];
    for (const q of r.parts) { // each zone a sample of its own, at its weight and gain
      one(q, T, v, P, Fq, tmp[0], tmp[1], c, m.ratio);
      for (let i = 0; i < c; i++) { out[i] += q.w * tmp[0][i]; outR[i] += q.w * tmp[1][i]; }
    }
  }
  let tmp = [new Float64Array(128), new Float64Array(128)];

  // one source read for c samples: the tape read here, or the grain train
  function one(r, T, v, P, Fq, out, outR, c, ratio) {
    // formant mode: the grain train (grain-worklet.js), not this read
    if (globalThis.synthlabGrains?.on(T)) return globalThis.synthlabGrains.read(r, T, v, P, Fq, out, outR, c, ratio);
    const { buf, bufR, lo, hi, end, looping } = r.src, last = buf.length - 2;
    for (let i = 0, pos = r.pos; i < c; i++, pos += Fq[i - 1] * r.k, r.pos = pos) {
      const x = looping && pos >= hi ? lo + ((pos - lo) % (hi - lo)) : pos;
      if (x < 0 || (!looping && x > end - 1)) { out[i] = outR[i] = 0; continue; }
      const k = Math.min(Math.max(Math.floor(x), 0), Math.max(0, last)), fr = Math.min(Math.max(x - k, 0), 1);
      const k1 = Math.min(k + 1, last + 1);
      out[i] = buf[k] * (1 - fr) + buf[k1] * fr;
      outR[i] = bufR ? bufR[k] * (1 - fr) + bufR[k1] * fr : out[i];
    }
  }

  globalThis.synthlabSampler = { set, read, pick };
})();
