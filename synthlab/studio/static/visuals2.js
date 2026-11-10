// synthlab/studio/static/visuals2.js
// The visualiser registry's second batch: sixteen more variations, registered into
// visuals.js through `synthlab.visuals.add` with the same contract — `{id,
// name, kind, draw(g, f)}`, blank at rest (visuals.js's `draw` does that
// centrally), colours only through `ink()`'s roles (`f.c`, `rgba`), no
// layout reads, state on `f.s`, under ~1 ms a frame at the dock's size —
// and a row each in studio/visuals.py (the card, its family).
// What several of them read is worked out once a frame, whichever canvases
// draw it (`once`): the dominant pitch (an autocorrelation on the time data,
// decimated to 11 kHz), the chroma (the bins folded onto 12 pitch classes)
// and the master's two sides — the one tap here: an up-mix to two channels,
// a splitter and two small analysers off the master analyser, made the
// first time a stereo variation draws and read once a frame after that.
// No `ctx.font` on a live canvas: setting it resolves the page's style (a
// recalc, ~1.5 ms, whenever anything moved since) — text is stamped from
// sprites drawn once off the document (`text`).
// Calm motion (colours.js): nothing here tweens; the surface stops turning, the
// plasma drifts at a fifth of its pace and the ripples fade where they are.
// Budget: <= 450 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const V = S.visuals;
  if (!V) return;
  const calm = () => window.synthlab.calm(); // the Motion setting (colours.js), never the OS's
  const { rgba, add } = V, K = V.kit, TAU = Math.PI * 2;
  const NAMES = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"];
  const clamp = (x, a, b) => (x < a ? a : x > b ? b : x);
  const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

  // ---- what several variations read, worked out once a frame ---------------------
  const M = { at: -1 };
  function once(f) {
    if (M.at !== f.now) { M.at = f.now; M.pitch = M.chroma = M.lr = undefined; }
    return M;
  }
  // the dominant pitch: the wave decimated 4x, its autocorrelation over 55 Hz..1 kHz,
  // the first peak near the highest after the first zero (not an octave down); a chord has
  // no clear period, and gets its loudest partial instead
  const PX = new Float32Array(512), PR = new Float32Array(260);
  let held = null; // the last pitch heard, held over a short gap (a kick, a chord's beating)
  function pitch(f) {
    const m = once(f);
    if (m.pitch === undefined) {
      const p = detect(f);
      if (p) held = { ...p, at: f.now };
      m.pitch = p || (held && f.rms >= 0.01 && f.now - held.at < 250 ? held : null);
    }
    return m.pitch;
  }
  function detect(f) {
    const w = f.wave, n = Math.min(512, w.length >> 2), sr = f.rate / 4;
    if (f.rms < 0.01 || f.kick > 0.35 || n < 256) return null; // a kick's thump is not a note
    for (let i = 0; i < n; i++) { const o = i * 4; PX[i] = (w[o] + w[o + 1] + w[o + 2] + w[o + 3]) / 4 - 128; }
    const lo = Math.max(2, Math.floor(sr / 1000)), hi = Math.min(PR.length - 2, Math.ceil(sr / 55), n >> 1), W = n - hi;
    let r0 = 0;
    for (let i = 0; i < W; i++) r0 += PX[i] * PX[i];
    if (!r0) return null;
    let start = 0, top = 0;
    for (let t = lo; t <= hi; t++) {
      let a = 0;
      for (let i = 0; i < W; i++) a += PX[i] * PX[i + t];
      PR[t] = a / r0;
      if (!start && PR[t] < 0) start = t;
      if (start && PR[t] > top) top = PR[t];
    }
    if (!start || top < 0.5) return loudest(f); // no clear period: a chord, a pad — name its loudest partial
    for (let t = start + 1; t < hi; t++) if (PR[t] >= top * 0.88 && PR[t] >= PR[t - 1] && PR[t] >= PR[t + 1]) {
      const d = PR[t - 1] - 2 * PR[t] + PR[t + 1], hz = sr / (t + (d ? (0.5 * (PR[t - 1] - PR[t + 1])) / d : 0));
      return { hz, midi: 69 + 12 * Math.log2(hz / 440), conf: PR[t] };
    }
    return null;
  }
  function loudest(f) { // the strongest bin 60 Hz..1.5 kHz, placed between its neighbours
    const fr = f.freq, bin = f.rate / (2 * fr.length), a = Math.ceil(60 / bin), b = Math.min(fr.length - 2, Math.floor(1500 / bin));
    let k = a;
    for (let i = a + 1; i <= b; i++) if (fr[i] > fr[k]) k = i;
    if (fr[k] < 120) return null;
    const d = fr[k - 1] - 2 * fr[k] + fr[k + 1], hz = (k + (d ? (0.5 * (fr[k - 1] - fr[k + 1])) / d : 0)) * bin;
    return { hz, midi: 69 + 12 * Math.log2(hz / 440), conf: fr[k] / 255 };
  }
  // the chroma: each bin 200 Hz..4 kHz onto its pitch class, peaks weighted up, eased
  let cmap = null;
  const RAW = new Float32Array(12), CH = new Float32Array(12);
  function chroma(f) {
    const m = once(f);
    if (m.chroma) return m.chroma;
    const fr = f.freq, n = fr.length;
    if (cmap?.n !== n || cmap.rate !== f.rate) {
      cmap = { n, rate: f.rate, pc: new Int8Array(n).fill(-1) };
      for (let k = 1; k < n; k++) { const hz = (k * f.rate) / (2 * n); if (hz >= 200 && hz <= 4000) cmap.pc[k] = (Math.round(12 * Math.log2(hz / 440) + 69) % 12 + 12) % 12; }
    }
    RAW.fill(0);
    for (let k = 1; k < n; k++) { const p = cmap.pc[k]; if (p >= 0) { const v = fr[k] / 255; RAW[p] += v * v * v; } }
    let top = 1e-6;
    for (const v of RAW) top = Math.max(top, v);
    const a = 1 - Math.exp(-f.dt / 0.12);
    for (let p = 0; p < 12; p++) CH[p] += (RAW[p] / top - CH[p]) * a;
    return (m.chroma = CH);
  }
  // the master's two sides (the one tap of their own: true L / R for the goniometer)
  let lr = null;
  function sides(f) {
    const m = once(f), an = S.getAnalyser?.();
    if (m.lr !== undefined) return m.lr;
    if (!an) return (m.lr = null);
    if (lr?.an !== an) { // a mono source is up-mixed to both sides, as the speakers would
      const c = an.context, up = new GainNode(c, { channelCount: 2, channelCountMode: "explicit", channelInterpretation: "speakers" });
      const sp = c.createChannelSplitter(2), pair = [0, 1].map(() => new AnalyserNode(c, { fftSize: 1024 }));
      an.connect(up).connect(sp); pair.forEach((a, i) => sp.connect(a, i));
      lr = { an, pair, l: new Float32Array(1024), r: new Float32Array(1024) };
    }
    lr.pair[0].getFloatTimeDomainData(lr.l); lr.pair[1].getFloatTimeDomainData(lr.r);
    return (m.lr = lr);
  }
  // a rising zero cross after a dip (a little hysteresis): the same cycle every frame
  function trig(x, span) {
    let armed = false;
    for (let i = 0; i < x.length - span; i++) { if (x[i] < 125) armed = true; else if (armed && x[i] >= 128) return i; }
    return 0;
  }
  const span = (f, cycles, dflt) => { const p = pitch(f); return Math.max(128, Math.min(f.wave.length >> 1, p ? Math.round((cycles * f.rate) / p.hz) : dflt)); };
  // Text without a live canvas's `font` setter: that resolves the page's style first (a
  // recalc whenever anything moved since: ~1.5 ms, paid by whichever draw set a font first).
  // Each string is drawn once onto a sprite, off the document, and stamped from there.
  const sprites = new Map();
  function text(g, f, str, x, y, px, ink, align = "left", base = "top") {
    const key = `${str}|${Math.round(px)}|${ink}`;
    let sp = sprites.get(key);
    if (!sp) {
      if (sprites.size > 200) sprites.clear();
      const c = new OffscreenCanvas(1, 1), k = c.getContext("2d"), fnt = `${Math.round(px)}px ${f.c.font}`;
      k.font = fnt;
      Object.assign(c, { width: Math.ceil(k.measureText(str).width) + 2, height: Math.ceil(px * 1.35) });
      k.font = fnt; k.textBaseline = "top"; k.fillStyle = ink; k.fillText(str, 1, Math.round(px * 0.12));
      sprites.set(key, (sp = c));
    }
    g.drawImage(sp, Math.round(align === "center" ? x - sp.width / 2 : align === "right" ? x - sp.width : x), Math.round(base === "bottom" ? y - sp.height : y));
  }
  const every = (f, ms) => { const s = f.s; if (f.fresh || f.now - (s.at || 0) >= ms) { s.at = f.now; return true; } return false; };

  // ---- scopes -----------------------------------------------------------------------
  add({ id: "triggered", name: "Triggered scope", kind: "scope", draw(g, f) {
    const { w, h } = f, x = f.wave, n = span(f, 4, 1024), at = trig(x, n), step = Math.max(1, Math.floor(n / w));
    g.clearRect(0, 0, w, h); K.graticule(g, f);
    g.beginPath();
    for (let i = 0; i <= n; i += step) { const X = (i / n) * w, Y = h / 2 - ((x[at + i] - 128) / 128) * h * 0.48; i ? g.lineTo(X, Y) : g.moveTo(X, Y); }
    K.glowLine(g, f, 4, 1.4);
    const t = 3 * f.dpr; // the trigger's mark, at its level
    g.fillStyle = rgba(f.c.accent); g.beginPath(); g.moveTo(0, h / 2 - t); g.lineTo(t * 1.6, h / 2); g.lineTo(0, h / 2 + t); g.fill();
  } });
  add({ id: "goniometer", name: "Goniometer", kind: "scope", draw(g, f) {
    const { w, h, s } = f, side = sides(f), ry = h * 0.46, rx = Math.min(w * 0.2, h * 1.3);
    const bar = Math.min(w - 2 * rx - 24 * f.dpr, 180 * f.dpr), cx = Math.max(rx + 6 * f.dpr, (w - 2 * rx - 12 * f.dpr - bar) / 2 + rx), cy = h / 2; // the scope and its meter, centred
    const k = Math.min(4, 0.85 / Math.max(0.05, f.peak)), l = side?.l, r = side?.r;
    let lr2 = 0, ll = 0, rr = 0;
    // the dots as pixels in a phosphor of their own, fading by themselves, put straight in
    const W = Math.max(2, Math.round(2 * rx)), H = Math.max(2, Math.round(2 * ry)), n = l ? l.length : f.wave.length, dot = W > 240 ? 2 : 1;
    if (s.img?.width !== W || s.img.height !== H) s.img = new ImageData(W, H);
    const d = s.img.data, keep = Math.exp(-f.dt / 0.12), [cr, cg, cb] = f.c.signal;
    for (let o = 3; o < d.length; o += 4) if (d[o]) d[o] = d[o] * keep - 1;
    for (let i = 0; i < n; i++) {
      const L = l ? l[i] : (f.wave[i] - 128) / 128, R = r ? r[i] : L;
      lr2 += L * R; ll += L * L; rr += R * R;
      const x = (W / 2 + (L - R) * 0.707 * rx * k) | 0, y = (H / 2 - (L + R) * 0.707 * ry * k) | 0;
      if (x < 0 || y < 0 || x > W - dot || y > H - dot) continue;
      for (let yy = y; yy < y + dot; yy++) for (let xx = x; xx < x + dot; xx++) { const o = (yy * W + xx) * 4; d[o] = cr; d[o + 1] = cg; d[o + 2] = cb; d[o + 3] = Math.min(255, d[o + 3] + 120); }
    }
    g.clearRect(0, 0, w, h); g.putImageData(s.img, Math.round(cx - W / 2), Math.round(cy - H / 2));
    g.strokeStyle = rgba(f.c.line, 0.9); g.lineWidth = f.dpr; g.beginPath(); // the diamond over them: L and R at full scale, M up, S across
    g.moveTo(cx, cy - ry); g.lineTo(cx + rx, cy); g.lineTo(cx, cy + ry); g.lineTo(cx - rx, cy); g.closePath();
    g.moveTo(cx, cy - ry); g.lineTo(cx, cy + ry); g.moveTo(cx - rx, cy); g.lineTo(cx + rx, cy); g.stroke();
    const corr = ll * rr > 1e-9 ? lr2 / Math.sqrt(ll * rr) : 1; // the correlation: +1 mono, 0 wide, -1 out of phase
    s.corr = f.fresh ? corr : s.corr + (corr - s.corr) * (1 - Math.exp(-f.dt / 0.25));
    const x0 = cx + rx + 12 * f.dpr, x1 = x0 + bar, mid = (x0 + x1) / 2, at = x0 + ((s.corr + 1) / 2) * (x1 - x0);
    if (bar < 20) return;
    g.fillStyle = rgba(f.c.line, 0.9); g.fillRect(x0, cy, x1 - x0, Math.max(1, f.dpr)); g.fillRect(mid, cy - 3 * f.dpr, f.dpr, 6 * f.dpr);
    g.fillStyle = rgba(s.corr < 0 ? f.c.danger : f.c.signal); g.fillRect(Math.min(mid, at), cy - f.dpr, Math.abs(at - mid), 2 * f.dpr);
    g.fillRect(at - f.dpr, cy - ry * 0.55, 2 * f.dpr, ry * 0.55); // the needle above the line, the names below
    if (h < 40 * f.dpr) return;
    const px = 9 * f.dpr, dim = rgba(f.c.dim), ty = cy + 6 * f.dpr;
    text(g, f, "−1", x0, ty, px, dim); text(g, f, "+1", x1, ty, px, dim, "right"); text(g, f, `corr ${s.corr.toFixed(2)}`, mid, ty, px, dim, "center");
  } });
  add({ id: "polar", name: "Polar scope", kind: "scope", draw(g, f) {
    const { w, h, s } = f, x = f.wave, n = span(f, 6, 1024), at = trig(x, n), cx = w / 2, cy = h / 2, rx = Math.min(w * 0.3, h * 1.6), ry = h * 0.3;
    const k = Math.min(3, 0.7 / Math.max(0.08, f.peak));
    s.rot = (s.rot || 0) + (calm() ? 0 : f.dt * (0.25 + f.kick * 1.5));
    g.clearRect(0, 0, w, h);
    g.strokeStyle = rgba(f.c.line, 0.9); g.lineWidth = f.dpr; g.beginPath(); g.ellipse(cx, cy, rx, ry, 0, 0, TAU); g.stroke();
    g.beginPath(); // the wave wound once round the ring: the radius is the signal, a petal a cycle
    for (let j = 0, N = Math.min(n, 720); j <= N; j++) {
      const i = at + Math.floor((j / N) * n), a = (j / N) * TAU - Math.PI / 2 + s.rot, v = 1 + ((x[Math.min(x.length - 1, i)] - 128) / 128) * k * 0.6;
      const X = cx + Math.cos(a) * rx * v, Y = cy + Math.sin(a) * ry * v;
      j ? g.lineTo(X, Y) : g.moveTo(X, Y);
    }
    g.closePath(); g.fillStyle = rgba(f.c.signal, 0.12); g.fill(); K.glowLine(g, f, 4, 1.2);
  } });
  add({ id: "afterimage", name: "Afterimage", kind: "scope", draw(g, f) {
    const { w, h, s } = f, x = f.wave, P = 220, D = Math.round(f.rate / 700), k = 0.45 / Math.max(0.12, f.peak);
    s.figs ??= []; // the last few figures, each older one cooler and fainter
    if (every(f, 40)) {
      const fig = s.figs.length >= 7 ? s.figs.shift() : new Float32Array(2 * P), st = Math.max(1, Math.floor((x.length - D) / P));
      for (let j = 0; j < P; j++) { const i = j * st; fig[2 * j] = ((x[i] - 128) / 128) * k; fig[2 * j + 1] = ((x[i + D] - 128) / 128) * k; }
      s.figs.push(fig);
    }
    g.clearRect(0, 0, w, h); g.lineJoin = "round";
    g.fillStyle = rgba(f.c.line, 0.9); g.fillRect(0, Math.round(h / 2), w, 1); g.fillRect(Math.round(w / 2), 0, 1, h);
    s.figs.forEach((fig, j) => {
      const age = 1 - (j + 1) / s.figs.length; // 0: the newest
      g.beginPath();
      for (let p = 0; p < P; p++) { const X = w / 2 + fig[2 * p] * w, Y = h / 2 - fig[2 * p + 1] * h; p ? g.lineTo(X, Y) : g.moveTo(X, Y); }
      g.strokeStyle = rgba(mix(f.c.signal, f.c.accent, age), 0.95 * (1 - age) ** 1.5 + 0.08); g.lineWidth = (age ? 1 : 1.5) * f.dpr; g.stroke();
    });
  } });
  add({ id: "horizon", name: "Horizon", kind: "scope", draw(g, f) {
    const { w, h, s } = f, R = 16, P = 64, x = f.wave, hz = h * 0.16, ms = 70;
    s.rows ??= [];
    if (every(f, ms)) { // a slice of the wave from its trigger, the newest at the horizon
      const row = s.rows.length >= R ? s.rows.shift() : new Float32Array(P), n = span(f, 3, 768), at = trig(x, n);
      for (let p = 0; p < P; p++) row[p] = (x[at + Math.floor((p / (P - 1)) * n)] - 128) / 128;
      s.rows.push(row);
    }
    const frac = Math.min(1, (f.now - s.at) / ms), fill = rgba(f.c.inset), k = Math.min(3, 0.6 / Math.max(0.08, f.peak));
    g.clearRect(0, 0, w, h);
    g.fillStyle = rgba(f.c.accent, 0.5); g.fillRect(0, Math.round(hz), w, Math.max(1, f.dpr));
    for (let j = s.rows.length - 1; j >= 0; j--) { // back to front: each slice hides the ones behind it
      const d = s.rows.length - 1 - j + frac, z = 1 + (R - d) * 0.45, base = hz + (h * 1.02 - hz) / z, sx = (w * 1.25) / z, amp = (h * 0.5 * k) / z;
      if (d > R) continue;
      g.beginPath(); g.moveTo(w / 2 - sx / 2, base);
      for (let p = 0; p < P; p++) g.lineTo(w / 2 + (p / (P - 1) - 0.5) * sx, base - s.rows[j][p] * amp * Math.sin((Math.PI * (p + 0.5)) / P));
      g.lineTo(w / 2 + sx / 2, base); g.fillStyle = fill; g.fill();
      g.strokeStyle = rgba(f.c.signal, clamp(1.6 / z, 0.12, 1)); g.lineWidth = f.dpr; g.stroke();
    }
  } });

  // ---- spectrums ----------------------------------------------------------------------
  add({ id: "surface", name: "Spectrum surface", kind: "spectrum", draw(g, f) {
    const { w, h, s } = f, R = 16, N = K.BARS / 2;
    s.rows ??= [];
    if (every(f, 70)) { const row = s.rows.length >= R ? s.rows.shift() : new Float32Array(N); for (let i = 0; i < N; i++) row[i] = K.pair(f.bands, i); s.rows.push(row); }
    s.t = (s.t || 0) + (calm() ? 0 : f.dt);
    const yaw = calm() ? 0.2 : 0.3 * Math.sin(s.t * 0.35), cs = Math.cos(yaw), sn = Math.sin(yaw), fill = rgba(f.c.inset);
    const to = (X, Z, Y, first) => { // a turn about the upright, then a perspective that falls away up the box
      const x = X * cs - (Z - 0.5) * sn, z = clamp(X * sn + (Z - 0.5) * cs + 0.5, -0.4, 1.4), p = 1 - z * 0.42;
      (first ? g.moveTo : g.lineTo).call(g, w / 2 + x * w * 0.78 * p, h * 0.97 - z * h * 0.42 - Y * h * 0.5 * p);
    };
    g.clearRect(0, 0, w, h); g.lineJoin = "round";
    for (let j = 0; j < s.rows.length; j++) { // the oldest at the back, the newest at the front
      const row = s.rows[j], newest = j === s.rows.length - 1, Z = (s.rows.length - 1 - j) / (R - 1);
      g.beginPath(); to(-0.5, Z, 0, true);
      for (let i = 0; i < N; i++) to(i / (N - 1) - 0.5, Z, row[i]);
      to(0.5, Z, 0); g.closePath(); g.fillStyle = fill; g.fill();
      g.strokeStyle = rgba(newest ? f.c.accent : f.c.signal, newest ? 1 : 0.25 + 0.75 * (1 - Z)); g.lineWidth = (newest ? 1.4 : 1) * f.dpr; g.stroke();
    }
  } });
  add({ id: "reflect", name: "Reflection bars", kind: "spectrum", draw(g, f) {
    const { w, h, s } = f, N = K.BARS / 2, bw = w / N, floor = h * 0.7, top = floor * 0.94;
    s.cap ??= new Float32Array(N);
    g.clearRect(0, 0, w, h);
    const up = g.createLinearGradient(0, 0, 0, floor), down = g.createLinearGradient(0, floor, 0, h);
    up.addColorStop(0, rgba(f.c.signal)); up.addColorStop(1, rgba(f.c.signal, 0.55));
    down.addColorStop(0, rgba(f.c.signal, 0.32)); down.addColorStop(1, rgba(f.c.signal, 0));
    for (let i = 0; i < N; i++) {
      const v = K.pair(f.bands, i), bh = Math.max(f.dpr, v * top), x = i * bw + bw * 0.15, bwi = bw * 0.7;
      g.fillStyle = up; g.fillRect(x, floor - bh, bwi, bh);
      g.fillStyle = down; g.fillRect(x, floor + f.dpr, bwi, Math.min(h - floor, bh * 0.5)); // the floor's reflection, half as tall
      s.cap[i] = Math.max(v, s.cap[i] - f.dt * 0.35);
    }
    g.fillStyle = rgba(f.c.text, 0.85);
    for (let i = 0; i < N; i++) g.fillRect(i * bw + bw * 0.15, floor - s.cap[i] * top - 2 * f.dpr, bw * 0.7, Math.max(1, f.dpr));
    g.fillStyle = rgba(f.c.line); g.fillRect(0, floor, w, Math.max(1, f.dpr * 0.5));
  } });
  add({ id: "bloom", name: "Bloom", kind: "spectrum", draw(g, f) {
    const { w, h } = f, N = K.BARS / 2, bw = w / N, glow = 0.7 + f.kick * 0.6;
    g.clearRect(0, 0, w, h); g.globalCompositeOperation = "lighter"; g.lineCap = "round";
    g.beginPath();
    for (let i = 0; i < N; i++) { const x = (i + 0.5) * bw, v = K.pair(f.bands, i); g.moveTo(x, h - 2 * f.dpr); g.lineTo(x, h - 2 * f.dpr - v * h * 0.86); }
    for (const [wd, a] of [[Math.min(bw * 2.4, 16 * f.dpr), 0.05], [Math.min(bw * 1.2, 8 * f.dpr), 0.11], [Math.min(bw * 0.6, 4 * f.dpr), 0.2]]) { // the halo, wide to narrow: overlaps add up
      g.strokeStyle = rgba(f.c.signal, a * glow); g.lineWidth = wd; g.stroke();
    }
    g.strokeStyle = rgba(f.c.signal, 0.95); g.lineWidth = Math.max(1, f.dpr * 1.2); g.stroke();
    g.beginPath(); // the hottest tips run white
    for (let i = 0; i < N; i++) { const v = K.pair(f.bands, i); if (v > 0.6) { const x = (i + 0.5) * bw, y = h - 2 * f.dpr - v * h * 0.86; g.moveTo(x, y); g.lineTo(x, y + 3 * f.dpr); } }
    g.strokeStyle = rgba(f.c.text, 0.6 * glow); g.lineWidth = 2 * f.dpr; g.stroke();
  } });
  const ISO = [31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000];
  const ISO_TAG = new Set([63, 250, 1000, 4000, 16000]);
  add({ id: "octave", name: "1/3-octave", kind: "spectrum", draw(g, f) {
    const { w, h, s } = f, N = ISO.length, fr = f.freq, n = fr.length, bin = f.rate / (2 * n), bw = w / N;
    if (s.n !== n) { // each ISO centre's bins, its edges a sixth of an octave either side
      s.n = n; s.lo = new Uint16Array(N); s.hi = new Uint16Array(N); s.pk = new Float32Array(N); s.at = new Float32Array(N);
      ISO.forEach((c, b) => { s.lo[b] = clamp(Math.floor(c / 1.1225 / bin), 1, n - 1); s.hi[b] = clamp(Math.max(s.lo[b], Math.round((c * 1.1225) / bin)), 1, n - 1); });
    }
    const tags = h >= 40 * f.dpr, foot = tags ? 11 * f.dpr : 0, H = h - foot - 2 * f.dpr;
    g.clearRect(0, 0, w, h);
    const dim = rgba(f.c.dim);
    for (let b = 0; b < N; b++) {
      let v = 0;
      for (let k = s.lo[b]; k <= s.hi[b]; k++) v = Math.max(v, fr[k] / 255);
      if (v >= s.pk[b]) { s.pk[b] = v; s.at[b] = f.now; } else if (f.now - s.at[b] > 900) s.pk[b] = Math.max(v, s.pk[b] - f.dt * 0.4); // held, then let go
      const x = b * bw + bw * 0.12, ww = bw * 0.76;
      g.fillStyle = rgba(f.c.line, 0.6); g.fillRect(x, 0, ww, H);
      g.fillStyle = rgba(v > 0.86 ? f.c.accent : f.c.signal, 0.9); g.fillRect(x, H - v * H, ww, v * H);
      g.fillStyle = rgba(f.c.text, 0.9); g.fillRect(x, H - s.pk[b] * H - f.dpr, ww, Math.max(1, f.dpr * 1.5));
      if (tags && ISO_TAG.has(ISO[b])) text(g, f, ISO[b] >= 1000 ? `${ISO[b] / 1000}k` : String(ISO[b]), x + ww / 2, h, 8.5 * f.dpr, dim, "center", "bottom");
    }
  } });
  add({ id: "centroid", name: "Brightness ribbon", kind: "spectrum", draw(g, f) {
    const { w, h, s } = f, L = 160, fr = f.freq, bin = f.rate / (2 * fr.length);
    s.y ??= new Float32Array(L).fill(-1); s.l ??= new Float32Array(L); s.i ??= 0;
    let sum = 0, wsum = 0;
    for (let k = 2; k < fr.length; k++) { const p = (fr[k] / 255) ** 2; sum += p * k * bin; wsum += p; }
    const c = wsum > 1e-6 ? sum / wsum : 0, y = clamp(Math.log2(Math.max(100, c) / 100) / Math.log2(80), 0, 1); // 100 Hz..8 kHz, log
    s.cy = f.fresh || s.y[(s.i + L - 1) % L] < 0 ? y : s.cy + (y - s.cy) * (1 - Math.exp(-f.dt / 0.08));
    if (every(f, 33)) { s.y[s.i] = s.cy; s.l[s.i] = Math.min(1, f.rms * 3); s.i = (s.i + 1) % L; }
    g.clearRect(0, 0, w, h);
    g.fillStyle = rgba(f.c.line, 0.8);
    for (const hz of [250, 1000, 4000]) g.fillRect(0, Math.round(h - (Math.log2(hz / 100) / Math.log2(80)) * h), w, 1);
    const X = (j) => w - (j / (L - 1)) * w, Y = (v, d) => h - v * h * 0.9 - h * 0.05 + d;
    g.beginPath(); // the ribbon: as wide as it is loud, newest at the right
    for (let j = 0; j < L; j++) { const q = (s.i - 1 - j + 2 * L) % L; if (s.y[q] < 0) break; g.lineTo(X(j), Y(s.y[q], -s.l[q] * h * 0.22 - f.dpr)); }
    for (let j = L - 1; j >= 0; j--) { const q = (s.i - 1 - j + 2 * L) % L; if (s.y[q] >= 0) g.lineTo(X(j), Y(s.y[q], s.l[q] * h * 0.22 + f.dpr)); }
    const k = g.createLinearGradient(0, 0, w, 0);
    k.addColorStop(0, rgba(f.c.signal, 0)); k.addColorStop(1, rgba(f.c.signal, 0.8));
    g.fillStyle = k; g.fill();
    g.fillStyle = rgba(f.c.accent); g.beginPath(); g.arc(w - 3 * f.dpr, Y(s.cy, 0), 2.5 * f.dpr, 0, TAU); g.fill();
    if (h >= 40 * f.dpr) text(g, f, `${Math.round(c / 10) * 10} Hz`, w - 10 * f.dpr, 2 * f.dpr, 9 * f.dpr, rgba(f.c.dim), "right");
  } });
  const FIFTHS = [0, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10, 5]; // C G D A E B F♯ D♭ A♭ E♭ B♭ F
  add({ id: "chroma", name: "Chroma wheel", kind: "spectrum", draw(g, f) {
    const { w, h } = f, ch = chroma(f), R = Math.min(h * 0.47, w * 0.22), cx = R + 4 * f.dpr, cy = h / 2, r0 = R * 0.42;
    let best = 0;
    for (let p = 1; p < 12; p++) if (ch[p] > ch[best]) best = p;
    g.clearRect(0, 0, w, h);
    FIFTHS.forEach((p, j) => { // the circle of fifths: neighbours share most of their notes
      const a0 = (j / 12) * TAU - Math.PI / 2 - TAU / 24 + 0.04, a1 = a0 + TAU / 12 - 0.08, v = ch[p];
      g.beginPath(); g.arc(cx, cy, r0 + (R - r0) * (0.35 + 0.65 * v), a0, a1); g.arc(cx, cy, r0, a1, a0, true); g.closePath();
      g.fillStyle = rgba(p === best ? f.c.accent : f.c.signal, 0.12 + 0.88 * v); g.fill();
    });
    const x0 = cx + R + 10 * f.dpr, bw = (w - x0 - 4 * f.dpr) / 12;
    if (bw < 4) return;
    const tags = bw >= 12 * f.dpr && h >= 22 * f.dpr, foot = tags ? Math.min(12 * f.dpr, h * 0.4) : 0, H = h - foot - 2 * f.dpr;
    const px = Math.min(foot * 0.85, bw * 0.55);
    FIFTHS.forEach((p, j) => {
      const x = x0 + j * bw, v = ch[p];
      g.fillStyle = rgba(f.c.line, 0.6); g.fillRect(x + bw * 0.15, 0, bw * 0.7, H);
      g.fillStyle = rgba(p === best ? f.c.accent : f.c.signal, 0.3 + 0.7 * v); g.fillRect(x + bw * 0.15, H - v * H, bw * 0.7, v * H);
      if (tags) text(g, f, NAMES[p], x + bw / 2, h, px, rgba(p === best ? f.c.text : f.c.dim), "center", "bottom");
    });
  } });

  // ---- meters -----------------------------------------------------------------------
  // seven segments as bits (a top, b c down the right, d the foot, e f up the left, g the middle)
  const SEG = { 0: 63, 1: 6, 2: 91, 3: 79, 4: 102, 5: 109, 6: 125, 7: 7, 8: 127, 9: 111, A: 119, b: 124, C: 57, d: 94, E: 121, F: 113, G: 61, "-": 64, " ": 0 };
  function digit(g, x, y, dw, dh, bits, t) {
    const half = dh / 2, v = half - 1.5 * t;
    if (bits & 1) g.fillRect(x + t, y, dw - 2 * t, t);
    if (bits & 2) g.fillRect(x + dw - t, y + t, t, v);
    if (bits & 4) g.fillRect(x + dw - t, y + half + t / 2, t, v);
    if (bits & 8) g.fillRect(x + t, y + dh - t, dw - 2 * t, t);
    if (bits & 16) g.fillRect(x, y + half + t / 2, t, v);
    if (bits & 32) g.fillRect(x, y + t, t, v);
    if (bits & 64) g.fillRect(x + t, y + half - t / 2, dw - 2 * t, t);
  }
  const FLAT = ["C", "db", "d", "Eb", "E", "F", "Gb", "G", "Ab", "A", "bb", "b"]; // seven segments' spellings: D and B lower case, a flat a small b
  add({ id: "lcd", name: "LCD panel", kind: "both", draw(g, f) {
    const { w, h, s } = f, dh = h * 0.68, dw = dh * 0.56, t = Math.max(1.2 * f.dpr, dh * 0.11), y = (h - dh) / 2, gap = dw * 0.32, p = pitch(f);
    const name = p ? FLAT[((Math.round(p.midi) % 12) + 12) % 12] : "--", oct = p ? String(clamp(Math.floor(Math.round(p.midi) / 12) - 1, 0, 9)) : "-";
    const db = 20 * Math.log10(f.peak + 1e-9), dbs = db <= -60 ? "---" : `${db < -0.5 ? "-" : " "}${String(clamp(Math.round(-db), 0, 99)).padStart(2, " ")}`;
    const cells = [[name[0], dw, dh, 0], [name[1] === "b" ? "b" : " ", dw * 0.62, dh * 0.6, 0], [oct, dw, dh, gap * 1.6], ...[...dbs].map((ch, i) => [ch, dw, dh, i ? 0 : gap * 3])]; // the note, then the peak in dB
    g.clearRect(0, 0, w, h);
    g.fillStyle = rgba(f.c.signal, 0.05); g.fillRect(0, 0, w, h); // the glass
    for (const lit of [false, true]) { // the unlit segments first, as a real LCD shows them
      g.fillStyle = lit ? rgba(f.c.signal) : rgba(f.c.signal, 0.08);
      let x = gap;
      for (const [ch, cw, chh, pad] of cells) { x += pad; digit(g, x, y, cw, chh, lit ? SEG[ch] ?? 0 : 127, t * (chh / dh)); x += cw + gap; }
      s.x = x;
    }
    const x0 = s.x + gap * 2, segs = clamp(Math.floor((w - x0 - gap) / (5 * f.dpr)), 6, 40), sw = (w - x0 - gap) / segs, rh = (dh - t) / 2;
    if (sw < 2) return;
    s.hold ??= [0, 0];
    [Math.min(1, f.rms * 2.8), f.peak].forEach((v, r) => { // two bars of blocks: the average, the peak (a held block on top)
      const lit = Math.round(clamp(1 + Math.log10(v + 1e-6) / 2.4, 0, 1) * segs), yy = y + r * (rh + t);
      s.hold[r] = Math.max(lit, s.hold[r] - f.dt * 12);
      for (let b = 0; b < segs; b++) {
        const on = b < lit || b === Math.round(s.hold[r]) - 1, hot = b >= segs - 2 ? f.c.danger : b >= segs * 0.8 ? f.c.accent : f.c.signal;
        g.fillStyle = on ? rgba(hot) : rgba(f.c.signal, 0.08); g.fillRect(x0 + b * sw, yy, sw - Math.max(1, f.dpr), rh);
      }
    });
  } });
  add({ id: "beatgrid", name: "Beat grid", kind: "both", draw(g, f) {
    const { w, h, s } = f, cols = 16, cw = w / cols, rh = h / 2, gap = Math.max(1, f.dpr);
    s.k ??= new Float32Array(cols); s.hh ??= new Float32Array(cols); s.n ??= 0; s.hiAvg ??= 0;
    if (f.beat >= 0 && f.beat < (s.was ?? 0) - 0.5) s.n++; // the beat wrapped: the next of the bar's four
    s.was = f.beat;
    const step = f.beat >= 0 ? (s.n % 4) * 4 + Math.floor(f.beat * 4) : Math.floor(f.now / 125) % cols; // no clock: 120 bpm's sixteenths
    let hi = 0;
    for (let b = 56; b < 72; b++) hi += f.bands[b] / 16;
    const hit = hi - s.hiAvg * 1.15 > 0.05 && f.now - (s.hiAt || 0) > 90;
    s.hiAvg += (hi - s.hiAvg) * 0.08;
    if (hit) { s.hiAt = f.now; s.hh[step] = 1; }
    if (f.onset) s.k[step] = 1;
    const fade = Math.exp(-f.dt / 2.2); // a step not hit again fades over a few bars
    g.clearRect(0, 0, w, h);
    g.fillStyle = rgba(f.c.text, 0.1); g.fillRect(step * cw, 0, cw, h);
    for (let c = 0; c < cols; c++) {
      s.k[c] *= fade; s.hh[c] *= fade;
      for (const [row, v, ink] of [[0, s.hh[c], f.c.text], [1, s.k[c], f.c.accent]]) {
        g.fillStyle = v > 0.04 ? rgba(ink, 0.2 + 0.8 * v) : rgba(f.c.line, c % 4 ? 0.55 : 1);
        g.fillRect(c * cw + gap, row * rh + gap, cw - 2 * gap, rh - 2 * gap);
      }
    }
    g.fillStyle = rgba(f.c.signal); g.fillRect(step * cw, h - Math.max(1.5, 1.5 * f.dpr), cw, Math.max(1.5, 1.5 * f.dpr));
  } });
  add({ id: "pitch", name: "Pitch trace", kind: "scope", draw(g, f) {
    const { w, h, s } = f, L = 180, p = pitch(f);
    s.m ??= new Float32Array(L).fill(NaN); s.i ??= 0;
    const range = h >= 60 * f.dpr ? 24 : 14;
    if (p && p.midi > 20 && p.midi < 110) { // the view follows the music, and jumps to a note off it
      s.mid = s.mid == null || Math.abs(p.midi - s.mid) > range / 2 - 1 ? p.midi : s.mid + (p.midi - s.mid) * (1 - Math.exp(-f.dt / 0.8));
    }
    if (every(f, 33)) { s.m[s.i] = p ? p.midi : NaN; s.i = (s.i + 1) % L; }
    const lo = Math.round(s.mid ?? 60) - range / 2, ps = h / range, Y = (m) => h - (m - lo) * ps;
    g.clearRect(0, 0, w, h);
    for (let m = lo; m <= lo + range; m++) { // the note lanes, a C brighter, the black keys darker
      const pc = ((m % 12) + 12) % 12;
      if (ps < 3 * f.dpr && pc) continue;
      g.fillStyle = rgba(f.c.line, pc === 0 ? 1 : [1, 3, 6, 8, 10].includes(pc) ? 0.35 : 0.7); g.fillRect(0, Math.round(Y(m)), w, 1);
    }
    g.beginPath();
    let pen = false;
    for (let j = 0; j < L; j++) {
      const v = s.m[(s.i - 1 - j + 2 * L) % L];
      if (Number.isNaN(v)) { pen = false; continue; }
      if (Math.abs(v - (s.m[(s.i - j + 2 * L) % L] ?? v)) > 4) pen = false; // a leap is a new note, not a slide
      const X = w - 4 * f.dpr - (j / (L - 1)) * (w - 4 * f.dpr), yy = Y(v);
      pen ? g.lineTo(X, yy) : g.moveTo(X, yy); pen = true;
    }
    K.glowLine(g, f, 4, 1.5);
    if (!p) return;
    g.fillStyle = rgba(f.c.accent); g.beginPath(); g.arc(w - 4 * f.dpr, Y(p.midi), 2.5 * f.dpr, 0, TAU); g.fill();
    if (h < 20 * f.dpr) return;
    const n = Math.round(p.midi), cents = Math.round((p.midi - n) * 100);
    text(g, f, `${NAMES[((n % 12) + 12) % 12]}${Math.floor(n / 12) - 1} ${cents >= 0 ? "+" : "−"}${Math.abs(cents)}¢`, w - 12 * f.dpr, 2 * f.dpr,
      clamp(h * 0.3, 9 * f.dpr, 15 * f.dpr), rgba(f.c.text, 0.9), "right");
  } });

  // ---- generative -----------------------------------------------------------------------
  const third = (lv, a, b) => { let v = 0; for (let i = a; i < b; i++) v += lv[i]; return v / (b - a); };
  add({ id: "plasma", name: "Plasma", kind: "both", draw(g, f) {
    const { w, h, s } = f, cols = 64, rows = 16;
    if (!s.px) { s.px = Object.assign(document.createElement("canvas"), { width: cols, height: rows }); s.img = s.px.getContext("2d").createImageData(cols, rows); }
    const lo = third(f.bands, 0, 18), mi = third(f.bands, 18, 45), hi = third(f.bands, 45, 72), d = s.img.data;
    s.t = (s.t || 0) + f.dt * (0.3 + mi * 2.4) * (calm() ? 0.2 : 1);
    const t = s.t, ring = 3 + lo * 10 + f.kick * 4, fine = 4 + hi * 14, a = 0.35 + Math.min(0.65, f.live * 1.4), A = f.c.signal, B = f.c.accent;
    for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) {
      const u = x / cols, v = y / rows, dx = (u - 0.5) * 4, dy = (v - 0.5);
      const q = (Math.sin(u * fine + t) + Math.sin(v * 5 + t * 1.3) + Math.sin((u + v) * 6 - t * 0.7) + Math.sin(Math.sqrt(dx * dx + dy * dy) * ring - t * 2)) / 8 + 0.5;
      const o = (y * cols + x) * 4;
      d[o] = A[0] + (B[0] - A[0]) * q; d[o + 1] = A[1] + (B[1] - A[1]) * q; d[o + 2] = A[2] + (B[2] - A[2]) * q; d[o + 3] = 255 * a * q * q;
    }
    s.px.getContext("2d").putImageData(s.img, 0, 0);
    g.clearRect(0, 0, w, h); g.imageSmoothingEnabled = true; g.imageSmoothingQuality = "low"; g.drawImage(s.px, 0, 0, w, h);
  } });
  add({ id: "ripples", name: "Ripples", kind: "both", draw(g, f) {
    const { w, h, s } = f, life = 1.6;
    s.r ??= [];
    if (f.onset || (every(f, 480) && f.rms > 0.02)) { // a kick drops a big one, the music now and then a small one
      const big = f.onset;
      s.r.push({ x: (0.15 + Math.random() * 0.7) * w, y: (0.25 + Math.random() * 0.5) * h, age: 0, big, ink: big ? f.c.accent : f.c.signal });
      if (s.r.length > 14) s.r.shift();
    }
    g.clearRect(0, 0, w, h); g.lineWidth = f.dpr * 1.2;
    const grow = calm() ? 0 : 1, R = Math.max(w, h) * 0.35;
    s.r = s.r.filter((r) => (r.age += f.dt) < life);
    for (const r of s.r) {
      const k = r.age / life, rad = (calm() ? 0.3 : 0.05 + k * grow) * R * (r.big ? 1 : 0.5);
      for (let e = 0; e < (r.big ? 3 : 1); e++) { // a big drop rings three times
        const rr = rad * (1 - e * 0.22);
        if (rr <= 0) continue;
        g.strokeStyle = rgba(r.ink, (1 - k) ** 2 * (1 - e * 0.3)); g.beginPath(); g.ellipse(r.x, r.y, rr, rr * Math.min(1, (h / w) * 2.2), 0, 0, TAU); g.stroke();
      }
    }
    g.fillStyle = rgba(f.c.signal, 0.3 + f.live); g.fillRect(0, h - Math.max(1, f.dpr), w * Math.min(1, f.live * 1.5), Math.max(1, f.dpr));
  } });
})();
