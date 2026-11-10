// synthlab/studio/static/vox-worklet.js
// The live vocal chain's units, and the pitch shifter they
// share with the pitch unit: effects.py's `_pitch_shift_causal` (Hann
// grains, each spliced in phase with the last: `_pitch_splices`) with a
// ratio a grain, the pitch tracker (`pitch_track`, YIN on the window before
// a grain), the auto-tune (`autotune_ratios`) and the harmonizer
// (`harmony_semis`) that set the ratios, and the three gain curves over
// `_envelope_follower` — the vocal compressor, the de-esser, the gate.
// Loaded into the AudioWorklet scope beside fx-worklet.js (livefx.js adds
// both modules; fx-worklet.js was full): its SynthlabFx calls
// `synthlabVox.init` / `.run` / `.quiet` for a unit of one of `kinds`, and
// this reads its history ring, biquads and pan law (`synthlabFx`). Units
// arrive resolved by fxlive.live_chain, the engine's constants with them.
// tests/studio/test_live_fx.py runs this under node against effects.py.
// Budget: <= 170 code lines.

(() => {
  const TAU = 2 * Math.PI;
  const kinds = new Set(["pitch", "autotune", "harmony", "vox_comp", "deess", "vgate"]);
  const F = () => globalThis.synthlabFx; // fx-worklet.js's Hist, rbj, biq, panGains
  const nSamples = (s, sr) => Math.max(1, Math.round(s * sr)); // core.n_samples
  const lagOf = (q, hop) => Math.max(0, (q - 1) * (2 * hop - 1)); // the causal start
  const db = (e) => 20 * Math.log10(Math.max(e, 1e-9)); // effects._db
  const pole = (t, sr) => Math.exp(-1 / (t * sr));
  const mod = (a, b) => ((a % b) + b) % b;
  // effects._envelope_follower, one sample: the larger of a fast and a slow one-pole on |x|
  const follow = (s, x) => { s.up = (1 - s.a) * x + s.a * s.up; s.dn = (1 - s.r) * x + s.r * s.dn; return Math.max(s.up, s.dn); };

  // a grain voice of the shifter: the current grain's ratio, lag and splice, and the last one's
  const voice = (q, hop) => ({ qc: q, qp: q, lc: lagOf(q, hop), lp: lagOf(q, hop), dc: 0, dp: 0 });
  // a grain starts at the sample just pushed, at ratio q: spliced in phase with the last (unless free)
  function start(s, v, q, free) {
    v.dp = v.dc; v.qp = v.qc; v.lp = v.lc; v.qc = q; v.lc = lagOf(q, s.hop);
    v.dc = free ? 0 : splice(s, v);
  }
  // the voice at sample n: the grain begun at n - r read at k = r, the one a hop before at k = hop + r
  const read = (h, s, v, n, r, w1, w2) =>
    h.at(n - (n - r - (v.lc + v.dc) + r * v.qc)) * w1 + h.at(n - (n - r - s.hop - (v.lp + v.dp) + (s.hop + r) * v.qp)) * w2;

  // effects._pitch_splices, for the grain starting at the sample just pushed:
  // its extra delay (0..reach), where it reads in phase with the one before
  function splice(s, v) {
    const h = s.hM, buf = h.buf, mk = h.m, w = h.w - 1, M = s.m, reach = s.reach, x = (k) => buf[(w - k) & mk];
    let e = 0;
    for (let j = 0; j < M; j++) { const a = x(2 * j); e += a * a; }
    if (e === 0) return 0; // silence: nothing to be in phase with
    const c = s.hop * (v.qp - 1) + (v.lc - v.lp) - v.dp, lo = Math.ceil(c), hi = Math.floor(c + reach);
    const cost = (s.cost ||= new Float64Array(reach + 2));
    let bj = 0;
    for (let l = lo; l <= hi; l++) { // e + sum x[t - |l|]^2 - 2 sum x[t] x[t - |l|], plus the bias
      const b0 = Math.abs(l);
      let eb = 0, cr = 0;
      for (let j = 0; j < M; j++) { const b = x(b0 + 2 * j); eb += b * b; cr += x(2 * j) * b; }
      cost[l - lo] = e + eb - 2 * cr + s.u.bias * e * (l - c) / reach;
      if (cost[l - lo] < cost[bj]) bj = l - lo;
    }
    let lag = lo + bj;
    if (bj > 0 && bj < hi - lo) {
      const a = cost[bj - 1], b = cost[bj], z = cost[bj + 1], den = a - 2 * b + z;
      if (den > 0) lag += Math.min(0.5, Math.max(-0.5, 0.5 * (a - z) / den));
    }
    return Math.min(Math.max(lag - c, 0), reach);
  }

  // effects.pitch_track at the sample just pushed: the f0 (0 unvoiced)
  function track(s) {
    const u = s.u, h = s.hM, M = s.tm, T = s.thi, p = (s.past ||= new Float64Array(s.span));
    for (let k = 0; k < s.span; k++) p[k] = h.buf[(h.w - 1 - k) & h.m]; // newest first
    let e = 0;
    for (let j = 0; j < M; j++) e += p[2 * j] * p[2 * j];
    if (e < M * u.track_floor ** 2) return 0;
    const cm = (s.cm ||= new Float64Array(T));
    let cum = 0;
    for (let t = 1; t <= T; t++) {
      let d = 0;
      for (let j = 0; j < M; j++) { const b = p[t + 2 * j] - p[2 * j]; d += b * b; }
      cum += d; cm[t - 1] = d * t / Math.max(cum, 1e-300);
    }
    let k = -1;
    for (let i = s.tlo - 1; i < T; i++) if (cm[i] < u.track_threshold) { k = i; break; }
    if (k < 0) return 0;
    while (k + 1 < T && cm[k + 1] < cm[k]) k++;
    let tau = k + 1;
    if (k > s.tlo - 1 && k < T - 1) {
      const a = cm[k - 1], b = cm[k], z = cm[k + 1], den = a - 2 * b + z;
      if (den > 0) tau += Math.min(0.5, Math.max(-0.5, 0.5 * (a - z) / den));
    }
    return sampleRate / tau;
  }
  // effects.nearest_note / _held_note: the scale's note nearest m, unless the one held is within `hold`
  function held(s, m) {
    const u = s.u;
    let t = null;
    for (let c = Math.floor(m) - 12; c <= Math.ceil(m) + 12; c++) {
      if (s.pcs.has(mod(c - u.key, 12)) && (t === null || Math.abs(m - c) < Math.abs(m - t))) t = c;
    }
    return s.held !== null && Math.abs(m - s.held) - Math.abs(m - t) < u.hold ? s.held : t;
  }
  const note = (sc, key, i) => key + 12 * Math.floor(i / sc.length) + sc[mod(i, sc.length)]; // effects._scale_note
  // effects.harmony_semis: from the held note to the one `steps` of the scale away
  const semis = (s, steps) => (s.held === null ? note(s.sc, 0, steps) - s.sc[0]
    : note(s.sc, s.held - s.sc[s.sc.indexOf(mod(s.held - s.u.key, 12))], s.sc.indexOf(mod(s.held - s.u.key, 12)) + steps) - s.held);

  // A grain starts: the pitch unit's ratio; the auto-tune's correction
  // (effects.autotune_ratios); the harmonizer's intervals from the note held
  function grain(s) {
    const u = s.u;
    if (u.kind === "pitch") return start(s, s.v, u.ratio, false);
    const f = track(s), m = f > 0 ? 69 + 12 * Math.log2(f / 440) : 0;
    if (u.kind === "harmony") {
      if (f > 0) { s.held = held(s, m); for (const v of s.voices) v.semis = semis(s, v.steps); }
      for (const v of s.voices) start(s, v, 2 ** (v.semis / 12), false);
      return;
    }
    if (f <= 0) { s.c = 0; s.held = null; return start(s, s.v, 1, true); }
    s.held = held(s, m);
    s.c += s.k * ((s.held - m) * (1 - u.humanize) - s.c);
    start(s, s.v, 2 ** (s.c / 12), false);
  }

  function init(u, old, sr) {
    const s = {}, H = F().Hist;
    if (u.kind === "vox_comp" || u.kind === "deess" || u.kind === "vgate") {
      Object.assign(s, { a: pole(u.attack, sr), r: pole(u.release, sr), up: old.up || 0, dn: old.dn || 0 });
      if (u.kind === "deess") { // a high-pass a channel, its state kept through a drag at the same corner
        const same = old.u?.freq === u.freq;
        s.f = same ? old.f : [0, 1].map(() => F().rbj("highpass", u.freq, 0.707, sr));
      }
      s.tail = nSamples(u.release * 14, sr) + (u.kind === "deess" ? 16384 : 0); // the followers fall to ~1e-6
      return s;
    }
    const hop = Math.max(8, Math.floor(Math.trunc(u.grain * sr) / 2));
    Object.assign(s, { hop, reach: Math.round(u.search * sr), m: Math.max(1, Math.floor(Math.round(u.window * sr) / 2)),
      hL: old.hL || new H(), hR: old.hR || new H(), hM: old.hM || new H() });
    if (u.kind === "pitch") { // the splice kept while the ratio is
      s.v = old.u?.ratio === u.ratio && old.v ? old.v : voice(u.ratio, hop);
      s.tail = Math.ceil(s.v.lc) + s.reach + 2 * hop + 2 * s.m + 2;
      return s;
    }
    const tm = Math.max(1, Math.floor(Math.round(u.track_window * sr) / 2)), thi = Math.ceil(sr / u.track_lo);
    Object.assign(s, { tm, thi, tlo: Math.max(2, Math.floor(sr / u.track_hi)), span: 2 * tm - 1 + thi,
      pcs: new Set(u.scale), held: old.held ?? null, c: old.c ?? 0,
      k: u.speed <= 0 ? 1 : 1 - Math.exp(-hop / (u.speed * u.glide * sr)) });
    if (u.kind === "autotune") s.v = old.v || voice(1, hop);
    else { // the harmonizer's voices (a second "off", or at no level, is none), each its interval's
      s.sc = [...new Set(u.scale.map((p) => mod(p, 12)))].sort((a, b) => a - b);
      const ask = [[u.first, u.first_level, u.first_pan], [u.second, u.second_level, u.second_pan]];
      s.voices = ask.filter(([w, l]) => w < u.steps.length && l > 0).map(([w, level, pan], i) => {
        const steps = Math.round(u.steps[w] * s.sc.length / 7), sm = semis(s, steps), [gl, gr] = F().panGains(pan);
        return Object.assign(old.voices?.[i] || voice(2 ** (sm / 12), hop), { steps, level, gl, gr, semis: sm });
      });
    }
    s.tail = Math.ceil(lagOf(2, hop)) + s.reach + 2 * hop + 2 * s.m + s.span + 2;
    return s;
  }

  // One unit over the block, in place (n0: the processor's first sample's index)
  function run(s, L, R, N, n0) {
    const u = s.u, k = u.kind;
    if (k === "vox_comp" || k === "deess" || k === "vgate") {
      for (let i = 0; i < N; i++) {
        if (k === "deess") { // effects._deess: the band over freq, turned down on its own level
          const hl = F().biq(s.f[0], L[i]), hr = F().biq(s.f[1], R[i]), e = follow(s, Math.max(Math.abs(hl), Math.abs(hr)));
          const g = 10 ** (-Math.min(u.depth, Math.max(0, db(e) - u.threshold) * (1 - 1 / u.ratio)) / 20);
          L[i] = (L[i] - hl) + hl * g; R[i] = (R[i] - hr) + hr * g;
          continue;
        }
        const e = follow(s, Math.max(Math.abs(L[i]), Math.abs(R[i])));
        if (k === "vgate") { // effects._vgate: a downward expander under the threshold
          const g = 10 ** (Math.max(-u.depth, Math.min(0, db(e) - u.threshold) * (u.ratio - 1)) / 20);
          L[i] *= g; R[i] *= g;
          continue;
        }
        const over = db(e) - u.threshold, half = u.knee / 2, slope = 1 / u.ratio - 1, m = u.mix; // effects._vox_comp
        const gdb = over <= -half ? 0 : over < half ? slope * (over + half) ** 2 / (2 * u.knee) : slope * over;
        const g = 10 ** ((gdb + u.makeup) / 20);
        L[i] = L[i] * (1 - m) + L[i] * g * m; R[i] = R[i] * (1 - m) + R[i] * g * m;
      }
      return;
    }
    const hop = s.hop, g = 2 * hop;
    for (let i = 0; i < N; i++) {
      s.hL.push(L[i]); s.hR.push(R[i]); s.hM.push((L[i] + R[i]) * 0.5);
      const n = n0 + i, r = n % hop, w1 = 0.5 - 0.5 * Math.cos(TAU * r / g), w2 = 0.5 - 0.5 * Math.cos(TAU * (hop + r) / g);
      if (r === 0) grain(s);
      if (k === "harmony") { // effects._harmony: the voice, plus each shifted copy of its mono sum, panned
        let a = L[i], b = R[i];
        for (const v of s.voices) { const y = v.level * read(s.hM, s, v, n, r, w1, w2); a += y * v.gl; b += y * v.gr; }
        L[i] = a; R[i] = b;
      } else { // pitch / autotune: both channels through one voice, blended
        const m = u.mix, a = read(s.hL, s, s.v, n, r, w1, w2), b = read(s.hR, s, s.v, n, r, w1, w2);
        L[i] = L[i] * (1 - m) + a * m; R[i] = R[i] * (1 - m) + b * m;
      }
    }
  }

  // Culled (silent in, every tail rung out): a grain's splice in silence is
  // none, and a follower has fallen to nothing
  function quiet(s) {
    for (const v of [s.v, ...(s.voices || [])]) if (v) v.dc = v.dp = 0;
    if ("up" in s) s.up = s.dn = 0;
    for (const f of s.f || []) f.z1 = f.z2 = 0;
  }

  globalThis.synthlabVox = { kinds, init, run, quiet };
})();
