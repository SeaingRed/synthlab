// synthlab/studio/static/visuals.js
// The visualiser registry: every way the top dock's two
// canvases — the scope slot and the spectrum slot — can draw the output,
// and which one each slot draws. motion.js measures the master analyser
// once a frame (one analyser, read once) and calls `render(F, scope,
// spectrum)`; this file draws the chosen variation into each slot, and
// hands the same frame to the Visuals page's gallery (vispage.js).
//   A variation is {id, name, kind: "scope" | "spectrum" | "both",
//   draw(g, f)} — `g` a 2D context, `f` the frame:
//     wave (Uint8 time domain, 128 = silence), freq (Uint8 bins, live only),
//     bands (72 log bands 40 Hz..Nyquist, 0..1, falling slowly), peaks (the
//     preview's overview, min/max pairs, or null), isLive, live (the smoothed
//     level, 0..1), kick (the low end's onsets, 0..1), onset (a kick began
//     this frame), rms, peak, beat (0..1 through the beat, or -1), dt (s),
//     rate, w / h (device px), dpr, c (the theme's role colours as rgb, via
//     `ink`), s (this canvas's own state: trails, caps, particles — new when
//     the variation or the size changes, `fresh` that frame).
//   The rules, as motion.js's: nothing per frame at rest, typed arrays and
//   state reused, colours resolved once per theme, no layout reads.
//   AT REST (no dead display): every canvas holds a picture. The
//   last live frame stays and fades (GSAP) into the current sound's own —
//   one note of it (`/note/C3.wav`) run offline through an analyser like
//   the master's, read once 0.3 s in, and drawn by the variation itself as
//   that note would draw live (`ghost`: a few frames, so springs and trails
//   settle), dimmed. Before that note is read, the variation's own at-rest
//   branch over motion.js's rest picture. Sound starting lights the canvas
//   up (a GSAP flare) and it runs live again. Each
//   draw is timed (`costs()`; the Visuals page shows it): under ~1 ms at the
//   dock's size. Adding one: `add({...})` below, and a row in
//   studio/visuals.py (the page's card) — tests/studio/test_visuals.py
//   holds the two lists to each other. visuals2.js registers the second
//   batch through `visuals.add`, with the shared strokes as
//   `visuals.kit`; a choice it names is taken as its entry arrives.
// The choice is per browser (localStorage "synthlab.visuals", read on every
// page); `.viz-name` in each dock cell names it, ticked in on a change.
// 340: the infrastructure is ~90 lines, the thirteen variations ~18 each.
// 340 -> 345: a silent frame blanks every canvas once.
// 345 -> 350: the kit for visuals2.js, and a choice kept for
// an entry registered after this file.
// 350 -> 420: the rest picture (the sound's own note, the
// held frame's fade, the flare when sound starts).
// Budget: <= 420 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const calm = () => window.synthlab.calm(); // the Motion setting (colours.js), never the OS's
  const G = () => (!calm() && window.gsap) || null;
  const KEY = "synthlab.visuals", BARS = 72, TAU = Math.PI * 2;
  const DEF = { scope: "classic-scope", spectrum: "classic-spectrum" };
  const list = [], byId = new Map(), hooks = new Set();
  let dirty = true, want = {}, choice = { ...DEF }; // want: what is stored, choice: what of it is registered

  // ---- the theme's role colours, through one helper --------------------------
  // A token may be a literal, a var() of a hue variable or a color-mix(): a
  // hidden probe resolves it as the page does, a 1 px canvas turns that into
  // rgb. Again only after a theme change.
  const ROLES = { signal: "--signal", accent: "--accent", text: "--text", dim: "--text-dim", inset: "--bg-inset",
    bg: "--bg", line: "--line", danger: "--danger" };
  let inks = null, probe = null, px = null;
  function rgbOf(token) {
    // on <html>, not <body>: a body morph would take it away
    if (!probe?.isConnected) probe = document.documentElement.appendChild(Object.assign(document.createElement("i"), { hidden: true }));
    px ??= Object.assign(document.createElement("canvas"), { width: 1, height: 1 }).getContext("2d", { willReadFrequently: true });
    probe.style.color = `var(${token})`;
    px.clearRect(0, 0, 1, 1); px.fillStyle = "transparent"; px.fillStyle = getComputedStyle(probe).color; px.fillRect(0, 0, 1, 1);
    const d = px.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2]];
  }
  function ink() {
    if (inks) return inks;
    inks = Object.fromEntries(Object.entries(ROLES).map(([k, t]) => [k, rgbOf(t)]));
    inks.font = getComputedStyle(document.body).fontFamily || "system-ui, sans-serif";
    return inks;
  }
  const rgba = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a <= 0 ? 0 : a >= 1 ? 1 : a.toFixed(3)})`;
  let inkGen = 0; // which theme the rest pictures were drawn in
  const restyle = () => { inks = null; inkGen++; dirty = true; };
  new MutationObserver(restyle).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-look"] }); // (a hue: synthlab-colours, below)
  document.addEventListener("synthlab-colours", restyle); // a hue moved (colours.js)
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", restyle);

  // ---- drawing one canvas ----------------------------------------------------
  const states = new WeakMap(); // canvas -> {id, w, h, s, cost}
  function draw(c, id, f) {
    const v = byId.get(id) || byId.get(DEF.scope);
    let st = states.get(c);
    if (!f.isLive) return rest(c, v, st, f);
    if (st?.rest) wake(c, st);
    f.fresh = !st || st.rest || st.id !== v.id || st.w !== c.width || st.h !== c.height;
    if (f.fresh) states.set(c, (st = { id: v.id, w: c.width, h: c.height, s: {}, cost: st?.id === v.id ? st.cost : 0 }));
    const g = c.getContext("2d"), t0 = performance.now();
    f.w = c.width; f.h = c.height; f.s = st.s;
    g.save(); v.draw(g, f); g.restore();
    const ms = performance.now() - t0;
    // a state's first frame builds its layers (and the goniometer its tap): once, not a frame's cost
    if (!f.fresh) st.cost = st.cost ? st.cost * 0.92 + ms * 0.08 : ms;
    return st;
  }

  // ---- at rest: the sound's own picture, never a dead well -------------------------
  const REST_A = 0.66; // a rest picture's strength: plainly a still, not the live trace
  let restF = null, restGen = 0, restAsk = 0, restTimer = 0;
  function edgesOf(bins, rate) { // motion.js's 72 log bands, 40 Hz .. Nyquist: rest reads as live
    const e = [], lo = Math.log(40), hi = Math.log(rate / 2);
    for (let b = 0; b <= BARS; b++) e.push(Math.min(bins - 1, Math.max(1, Math.round((Math.exp(lo + ((hi - lo) * b) / BARS) / (rate / 2)) * bins))));
    return e;
  }
  // one note of the sound on Synths, offline, through an analyser as the master's
  // (2048, no smoothing): its time domain and its bins 0.3 s in, a frame as live
  async function restSound() {
    const ask = ++restAsk, R = 44100;
    try {
      const bytes = await (await fetch("/note/C3.wav")).arrayBuffer();
      const oc = new OfflineAudioContext(1, R, R), buf = await oc.decodeAudioData(bytes);
      const src = new AudioBufferSourceNode(oc, { buffer: buf }), an = new AnalyserNode(oc, { fftSize: 2048, smoothingTimeConstant: 0 });
      src.connect(an).connect(oc.destination); src.start();
      const wave = new Uint8Array(2048), freq = new Uint8Array(1024), at = Math.min(0.3, buf.duration * 0.3);
      oc.suspend(Math.max(1, Math.round((at * R) / 128)) * (128 / R)).then(() => { an.getByteTimeDomainData(wave); an.getByteFrequencyData(freq); oc.resume(); });
      await oc.startRendering();
      if (ask !== restAsk) return;
      const e = edgesOf(freq.length, R), bands = new Float32Array(BARS);
      for (let b = 0; b < BARS; b++) { let t = 0; for (let i = e[b]; i <= e[b + 1]; i++) t = Math.max(t, freq[i]); bands[b] = t / 255; }
      let sum = 0, peak = 0;
      for (const x of wave) { const v = (x - 128) / 128; sum += v * v; peak = Math.max(peak, Math.abs(v)); }
      const rms = Math.sqrt(sum / wave.length);
      restF = { wave, freq, bands, rms, peak, live: Math.min(1, rms * 2.5), rate: R };
      restGen++; dirty = true;
    } catch { /* no sound to measure: the variation's own rest picture stays */ }
  }
  const restSoon = (ms) => { clearTimeout(restTimer); restTimer = setTimeout(restSound, ms); };
  document.addEventListener("DOMContentLoaded", () => restSoon(900));
  document.addEventListener("preview-ready", () => restSoon(700)); // an edit, a sound loaded
  // a still of variation `v` at canvas `c`'s size: the rest note drawn as live
  // a few frames (springs and trails settle), else its own at-rest branch
  const ids = new WeakMap();
  let idN = 0;
  const idOf = (o) => (o ? (ids.get(o) ?? (ids.set(o, ++idN), idN)) : 0);
  function still(c, v, st, f) {
    const key = restF ? `n${restGen}` : `r${idOf(f.bands)}|${idOf(f.peaks)}`;
    const full = `${v.id}|${c.width}x${c.height}|${inkGen}|${key}`;
    if (st.pic?.key === full) return st.pic;
    const cv = Object.assign(document.createElement("canvas"), { width: c.width, height: c.height }), g = cv.getContext("2d");
    const R = restF ? { ...f, ...restF, isLive: true, kick: 0, onset: false, beat: -1, dirty: true } : { ...f };
    R.w = c.width; R.h = c.height; R.s = {}; R.c = f.c;
    for (let i = 0, n = restF ? 10 : 1; i < n; i++) {
      R.now = 1e7 + i * 50; R.dt = 0.05; R.fresh = i === 0;
      const t0 = performance.now();
      g.save(); try { v.draw(g, R); } catch { /* a variation that needs the live tap: what it drew */ } g.restore();
      if (i === n - 1 && restF && !st.cost) st.cost = performance.now() - t0; // a card's cost before any sound
    }
    return (st.pic = { key: full, cv });
  }
  // at rest: what is on the canvas (the last live frame, or the last still) fades
  // into the still — once per still, a GSAP tween (instant under calm motion)
  function rest(c, v, st, f) {
    const live = st && !st.rest && st.id === v.id && st.w === c.width && st.h === c.height;
    if (!st?.rest || st.id !== v.id || st.w !== c.width || st.h !== c.height) {
      st?.tw?.kill?.();
      states.set(c, (st = { id: v.id, w: c.width, h: c.height, s: {}, cost: st?.id === v.id ? st.cost : 0, rest: true, pic: st?.pic }));
    }
    const pic = still(c, v, st, f);
    if (st.shown === pic.key) return st;
    st.shown = pic.key;
    let from = null;
    if (live || st.drawn) { from = Object.assign(document.createElement("canvas"), { width: c.width, height: c.height }); from.getContext("2d").drawImage(c, 0, 0); }
    const g = c.getContext("2d"), o = { t: 0 };
    const paint = () => {
      g.clearRect(0, 0, c.width, c.height);
      if (from && o.t < 1) { g.globalAlpha = 1 - o.t; g.drawImage(from, 0, 0); }
      g.globalAlpha = REST_A * (from ? o.t : Math.min(1, o.t * 1.4)); g.drawImage(pic.cv, 0, 0); g.globalAlpha = 1;
    };
    st.drawn = true; st.tw?.kill?.();
    const gs = G();
    if (!gs) { o.t = 1; paint(); return st; }
    st.tw = gs.to(o, { t: 1, duration: live ? 1.6 : 0.7, ease: live ? "power2.inOut" : "power3.out", onUpdate: paint, onStart: paint });
    return st;
  }
  // sound again: the still gives way to the live trace with a flare
  function wake(c, st) {
    st.tw?.kill?.();
    G()?.fromTo(c, { filter: "brightness(2.4) saturate(1.5)" }, { filter: "brightness(1) saturate(1)", duration: 0.9, ease: "expo.out", clearProps: "filter" });
  }

  // the beat's phase: the demo's clock, else the playing loop's at the page's tempo
  let bpm = 0, bpmAt = -1e9, kickWas = 0, onsetAt = 0;
  function beat(now) {
    if (!S.getCtx?.()) return -1;
    if (now - bpmAt > 500) { bpmAt = now; bpm = S.tempo?.() || 0; }
    const d = S.visuals.demoClock?.(), start = d ? d.start : S.isPlaying?.() ? S.loopStart?.() : null, b = d ? d.bpm : bpm;
    if (start == null || !(b >= 20)) return -1;
    const p = ((S.heardTime?.() ?? S.getCtx().currentTime) - start) / (60 / b);
    return p < 0 ? -1 : p - Math.floor(p);
  }
  function render(f, scope, spectrum) {
    f.c = ink(); f.dpr = devicePixelRatio; f.beat = beat(f.now);
    f.onset = f.kick > 0.45 && kickWas <= 0.45 && f.now - onsetAt > 140;
    if (f.onset) onsetAt = f.now;
    kickWas = f.kick;
    if (!f.isLive && !f.dirty && !dirty) return;
    f.dirty = f.dirty || dirty; dirty = false;
    if (scope) draw(scope, choice.scope, f);
    if (spectrum) draw(spectrum, choice.spectrum, f);
    for (const h of hooks) h(f, draw);
  }

  // ---- shared strokes ----------------------------------------------------------
  function trace(g, f, x0, w, y0, h, gain = 1) { // the wave across a box
    const n = f.wave.length;
    g.beginPath();
    for (let i = 0; i < n; i += 2) {
      const x = x0 + (i / (n - 1)) * w, y = y0 + h / 2 + ((f.wave[i] - 128) / 128) * (h / 2) * gain;
      i ? g.lineTo(x, y) : g.moveTo(x, y);
    }
  }
  function curve(g, f, lv, top) { // the smooth log-band line, a path left open at the right
    const { w, h } = f, bw = w / BARS;
    let px = 0, py = h - Math.max(1, lv[0] * h * top);
    g.beginPath(); g.moveTo(0, py);
    for (let b = 1; b < BARS; b++) {
      const x = (b + 0.5) * bw, y = h - Math.max(1, lv[b] * h * top);
      g.quadraticCurveTo(px, py, (px + x) / 2, (py + y) / 2); px = x; py = y;
    }
    g.lineTo(w, py);
  }
  function fade(g, f, a) { // the one signal graphic's fill: the role colour to nothing
    const k = g.createLinearGradient(0, 0, 0, f.h);
    k.addColorStop(0, rgba(f.c.signal, a)); k.addColorStop(1, rgba(f.c.signal, 0));
    return k;
  }
  // a trail that fades by itself: an offscreen layer the size of the canvas
  function trail(f, tau) {
    const s = f.s;
    if (!s.t || s.t.width !== f.w || s.t.height !== f.h) s.t = Object.assign(document.createElement("canvas"), { width: f.w, height: f.h });
    const t = s.t.getContext("2d");
    if (f.fresh || !f.isLive) t.clearRect(0, 0, f.w, f.h);
    else { t.globalCompositeOperation = "destination-out"; t.globalAlpha = 1 - Math.exp(-f.dt / tau); t.fillRect(0, 0, f.w, f.h); }
    t.globalCompositeOperation = "source-over"; t.globalAlpha = 1;
    return t;
  }
  function glowLine(t, f, wide, thin) { // a halo, then the core
    t.lineJoin = "round"; t.strokeStyle = rgba(f.c.signal, 0.16); t.lineWidth = wide * f.dpr; t.stroke();
    t.strokeStyle = rgba(f.c.signal, 0.95); t.lineWidth = thin * f.dpr; t.stroke();
  }
  const pair = (lv, i) => Math.max(lv[2 * i], lv[2 * i + 1]); // 72 bands -> 36
  function add(v) { list.push(v); byId.set(v.id, v); dirty = true; if (want.scope === v.id || want.spectrum === v.id) choice = load(); }

  // ---- the variations ------------------------------------------------------------
  add({ id: "classic-scope", name: "Classic scope", kind: "scope", draw(g, f) {
    const { w, h } = f;
    g.clearRect(0, 0, w, h); g.fillStyle = g.strokeStyle = rgba(f.c.signal);
    if (f.isLive) { g.lineWidth = 2 * f.dpr; trace(g, f, 0, w, 0, h); g.stroke(); return; }
    if (!f.peaks) { g.globalAlpha = 0.5; g.fillRect(0, h / 2, w, Math.max(1, f.dpr)); return; } // a quiet baseline
    const W = f.peaks.length / 2; // at rest: the preview's overview
    g.globalAlpha = 0.75;
    for (let x = 0; x < w; x++) {
      const i = Math.min(W - 1, Math.floor((x / w) * W)), lo = f.peaks[2 * i], hi = f.peaks[2 * i + 1];
      g.fillRect(x, ((1 - hi) * h) / 2, 1, Math.max(1, ((hi - lo) * h) / 2));
    }
  } });
  add({ id: "classic-spectrum", name: "Classic spectrum", kind: "spectrum", draw(g, f) {
    const a = f.isLive ? 1 : 0.55;
    g.clearRect(0, 0, f.w, f.h); curve(g, f, f.bands, 0.9);
    g.globalAlpha = a; g.lineWidth = 1.5 * f.dpr; g.strokeStyle = rgba(f.c.signal); g.stroke();
    g.lineTo(f.w, f.h); g.lineTo(0, f.h); g.closePath(); g.globalAlpha = a * 0.5; g.fillStyle = fade(g, f, 1); g.fill();
  } });
  function graticule(g, f) {
    const { w, h } = f, step = Math.max(8, Math.round(w / 12));
    g.fillStyle = rgba(f.c.line, 0.9);
    for (let x = step; x < w; x += step) g.fillRect(Math.round(x), 0, 1, h);
    for (const y of [0.25, 0.5, 0.75]) g.fillRect(0, Math.round(h * y), w, 1);
  }
  add({ id: "phosphor", name: "Phosphor", kind: "scope", draw(g, f) {
    const t = trail(f, 0.11);
    g.clearRect(0, 0, f.w, f.h); graticule(g, f);
    if (f.isLive) { trace(t, f, 0, f.w, 0, f.h, 1.1); glowLine(t, f, 5, 1.3); g.drawImage(t.canvas, 0, 0); return; }
    g.fillStyle = rgba(f.c.signal, 0.45);
    if (!f.peaks) return g.fillRect(0, f.h / 2, f.w, Math.max(1, f.dpr));
    const W = f.peaks.length / 2;
    for (let x = 0; x < f.w; x += 2) {
      const i = Math.min(W - 1, Math.floor((x / f.w) * W));
      g.fillRect(x, ((1 - f.peaks[2 * i + 1]) * f.h) / 2, 1, Math.max(1, ((f.peaks[2 * i + 1] - f.peaks[2 * i]) * f.h) / 2));
    }
  } });
  add({ id: "lissajous", name: "Lissajous", kind: "scope", draw(g, f) {
    const { w, h } = f, t = trail(f, 0.16), n = f.wave.length, D = Math.round(f.rate / 700);
    g.clearRect(0, 0, w, h);
    g.fillStyle = rgba(f.c.line, 0.9); g.fillRect(0, Math.round(h / 2), w, 1); g.fillRect(Math.round(w / 2), 0, 1, h);
    if (!f.isLive) { g.fillStyle = rgba(f.c.signal, 0.8); g.beginPath(); g.arc(w / 2, h / 2, 2 * f.dpr, 0, TAU); return g.fill(); }
    const k = 0.45 / Math.max(0.12, f.peak); // a gentle auto-gain: a quiet mix still draws a figure
    t.beginPath();
    for (let i = 0; i + D < n; i += 2) {
      const x = w / 2 + ((f.wave[i] - 128) / 128) * w * k, y = h / 2 - ((f.wave[i + D] - 128) / 128) * h * k;
      i ? t.lineTo(x, y) : t.moveTo(x, y);
    }
    glowLine(t, f, 4, 1); g.drawImage(t.canvas, 0, 0);
  } });
  add({ id: "mirror", name: "Mirror bars", kind: "spectrum", draw(g, f) {
    const { w, h, s } = f, N = BARS / 2, bw = w / N, mid = h / 2, dt = f.isLive ? f.dt : 0;
    s.cap ??= new Float32Array(N); s.vel ??= new Float32Array(N);
    g.clearRect(0, 0, w, h);
    const k = g.createLinearGradient(0, 0, 0, h);
    k.addColorStop(0, rgba(f.c.signal, 0.15)); k.addColorStop(0.5, rgba(f.c.signal, f.isLive ? 0.95 : 0.5)); k.addColorStop(1, rgba(f.c.signal, 0.15));
    g.fillStyle = k;
    for (let i = 0; i < N; i++) {
      const v = pair(f.bands, i), half = Math.max(f.dpr / 2, v * mid * 0.92);
      g.fillRect(i * bw + bw * 0.18, mid - half, bw * 0.64, half * 2);
      if (v >= s.cap[i] || !dt) { s.cap[i] = v; s.vel[i] = 0; } // gravity: the caps fall, faster and faster
      else { s.vel[i] += 2.4 * dt; s.cap[i] = Math.max(v, s.cap[i] - s.vel[i] * dt); }
    }
    g.fillStyle = rgba(f.c.text, f.isLive ? 0.85 : 0.4);
    for (let i = 0; i < N; i++) {
      const y = s.cap[i] * mid * 0.92 + 2 * f.dpr;
      g.fillRect(i * bw + bw * 0.18, mid - y - f.dpr, bw * 0.64, f.dpr * 1.5); g.fillRect(i * bw + bw * 0.18, mid + y, bw * 0.64, f.dpr * 1.5);
    }
  } });
  add({ id: "breath", name: "Breath", kind: "spectrum", draw(g, f) {
    const k = f.isLive ? f.kick : 0, a = f.isLive ? 1 : 0.55;
    g.clearRect(0, 0, f.w, f.h); curve(g, f, f.bands, 0.78 + 0.14 * k);
    g.lineJoin = "round"; g.strokeStyle = rgba(f.c.signal, (0.1 + 0.3 * k) * a); g.lineWidth = (5 + 9 * k) * f.dpr; g.stroke();
    g.strokeStyle = rgba(f.c.signal, a); g.lineWidth = 1.5 * f.dpr; g.stroke();
    g.lineTo(f.w, f.h); g.lineTo(0, f.h); g.closePath(); g.fillStyle = fade(g, f, (0.35 + 0.45 * k) * a); g.fill();
  } });
  add({ id: "radial", name: "Radial", kind: "spectrum", draw(g, f) {
    const { w, h } = f, cx = w / 2, cy = h / 2, k = f.isLive ? f.kick : 0, rx = Math.min(w * 0.22, h * 1.6) * (1 + 0.12 * k), ry = h * 0.24 * (1 + 0.12 * k);
    g.clearRect(0, 0, w, h);
    const ring = f.beat >= 0 ? 0.25 + 0.6 * (1 - f.beat) ** 3 : 0.35;
    g.strokeStyle = rgba(f.c.accent, ring * (f.isLive ? 1 : 0.5)); g.lineWidth = f.dpr;
    g.beginPath(); g.ellipse(cx, cy, rx * 0.92, ry * 0.92, 0, 0, TAU); g.stroke();
    g.beginPath();
    for (let j = 0; j <= 2 * BARS; j++) { // each band twice, mirrored left / right
      const b = j <= BARS ? j : 2 * BARS - j, v = f.bands[Math.min(BARS - 1, b)], a = (j / (2 * BARS)) * TAU - Math.PI / 2;
      const x = cx + Math.cos(a) * (rx + v * rx * 0.9), y = cy + Math.sin(a) * (ry + v * h * 0.26);
      j ? g.lineTo(x, y) : g.moveTo(x, y);
    }
    g.closePath(); g.fillStyle = rgba(f.c.signal, f.isLive ? 0.22 : 0.12); g.fill();
    g.strokeStyle = rgba(f.c.signal, f.isLive ? 1 : 0.55); g.lineWidth = 1.3 * f.dpr; g.stroke();
  } });
  add({ id: "waterfall", name: "Waterfall", kind: "spectrum", draw(g, f) {
    const { w, h, s } = f, rows = BARS / 2, rh = h / rows;
    if (!s.ink || s.ink[0] !== f.c.signal) s.ink = [f.c.signal, ...Array.from({ length: 21 }, (_, i) => rgba(i > 17 ? f.c.accent : f.c.signal, (i / 20) ** 1.3))];
    const col = (x, cw, lv, a) => {
      g.clearRect(x, 0, cw, h);
      for (let r = 0; r < rows; r++) { const v = pair(lv, r) * a; if (v > 0.03) { g.fillStyle = s.ink[1 + Math.round(v * 20)]; g.fillRect(x, h - (r + 1) * rh, cw, Math.ceil(rh)); } }
    };
    if (f.fresh || (!f.isLive && !s.live)) { col(0, w, f.bands, 0.45); s.live = false; return; } // at rest: the preview's bands
    if (!f.isLive) return; // a pause keeps the picture
    s.live = true; s.acc = (s.acc || 0) + f.dt * 40 * f.dpr;
    const k = Math.floor(s.acc);
    if (!k) return;
    s.acc -= k;
    g.globalCompositeOperation = "copy"; g.drawImage(g.canvas, -k, 0); g.globalCompositeOperation = "source-over";
    col(w - k, k, f.bands, 1);
  } });
  add({ id: "ridgeline", name: "Ridgeline", kind: "spectrum", draw(g, f) {
    const { w, h, s } = f, R = 14, N = BARS / 2;
    s.rows ??= []; s.at ??= 0;
    if (f.isLive && f.now - s.at > 110) { s.at = f.now; s.rows.push(Float32Array.from({ length: N }, (_, i) => pair(f.bands, i))); if (s.rows.length > R) s.rows.shift(); }
    const rows = s.rows.length ? s.rows : [Float32Array.from({ length: N }, (_, i) => pair(f.bands, i) * 0.6)];
    g.clearRect(0, 0, w, h);
    const fill = rgba(f.c.inset), step = (h * 0.62) / R, amp = h * 0.42;
    rows.forEach((lv, j) => {
      const base = h * 0.98 - (rows.length - 1 - j) * step, a = 0.2 + 0.8 * ((j + 1) / rows.length);
      g.beginPath(); g.moveTo(0, base);
      for (let i = 0; i < N; i++) {
        const x = ((i + 0.5) / N) * w, bell = Math.sin((Math.PI * (i + 0.5)) / N) ** 0.7; // calm edges, a range in the middle
        g.lineTo(x, base - lv[i] * amp * bell);
      }
      g.lineTo(w, base); g.fillStyle = fill; g.fill();
      g.strokeStyle = rgba(f.c.signal, a * (f.isLive ? 1 : 0.6)); g.lineWidth = f.dpr; g.stroke();
    });
  } });
  add({ id: "ladder", name: "LED ladder", kind: "spectrum", draw(g, f) {
    const { w, h, s } = f, cols = Math.max(12, Math.min(48, Math.floor(w / (9 * f.dpr)))), rows = Math.max(4, Math.min(16, Math.floor(h / (4 * f.dpr))));
    const cw = w / cols, rh = h / rows, gap = Math.max(1, f.dpr);
    if (!s.hold || s.hold.length !== cols) s.hold = new Float32Array(cols);
    g.clearRect(0, 0, w, h);
    for (let c = 0; c < cols; c++) {
      const b0 = Math.floor((c / cols) * BARS), b1 = Math.max(b0 + 1, Math.floor(((c + 1) / cols) * BARS));
      let v = 0;
      for (let b = b0; b < b1; b++) v = Math.max(v, f.bands[b]);
      s.hold[c] = f.isLive ? Math.max(v, s.hold[c] - f.dt * 0.5) : v;
      const lit = Math.round(v * rows), held = Math.min(rows - 1, Math.round(s.hold[c] * rows) - 1);
      for (let r = 0; r < rows; r++) {
        const on = r < lit || r === held, hue = r >= rows * 0.9 ? f.c.danger : r >= rows * 0.7 ? f.c.accent : f.c.signal;
        g.fillStyle = on ? rgba(hue, f.isLive ? 1 : 0.5) : rgba(f.c.line, 0.7);
        g.fillRect(c * cw + gap / 2, h - (r + 1) * rh + gap / 2, cw - gap, rh - gap);
      }
    }
  } });
  add({ id: "needles", name: "VU needles", kind: "both", draw(g, f) {
    const { w, h, s } = f, dt = Math.min(0.05, f.dt);
    s.a ??= [0, 0]; s.v ??= [0, 0];
    const db = (x) => Math.max(0, Math.min(1.08, 1 + (20 * Math.log10(x + 1e-6)) / 36)); // -36..0 dB, a little past
    const want = f.isLive ? [db(f.rms * 1.6), db(f.peak)] : [0, 0];
    const r = Math.min(w * 0.22, h * 0.92), cy = h * 0.96, A = 0.85, top = -Math.PI / 2;
    if (s.inks !== f.c) { // the faces, painted once a theme: arcs, the red zone, ticks, names
      s.inks = f.c; s.face ??= document.createElement("canvas"); Object.assign(s.face, { width: w, height: h });
      const k = s.face.getContext("2d");
      k.font = `${Math.round(Math.max(8, h * 0.16))}px ${f.c.font}`; k.textAlign = "right"; k.fillStyle = rgba(f.c.dim);
      for (let m = 0; m < 2; m++) {
        const cx = (w / 4) * (1 + 2 * m);
        k.lineWidth = Math.max(1, f.dpr); k.strokeStyle = rgba(f.c.dim, 0.7);
        k.beginPath(); k.arc(cx, cy, r, top - A, top + A * 0.5);
        for (let t = 0; t <= 8; t++) { const a = top - A + (t / 8) * 2 * A; k.moveTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r); k.lineTo(cx + Math.cos(a) * r * 0.86, cy + Math.sin(a) * r * 0.86); }
        k.stroke();
        k.strokeStyle = rgba(f.c.danger, 0.9); k.lineWidth = 2 * f.dpr; k.beginPath(); k.arc(cx, cy, r, top + A * 0.5, top + A); k.stroke();
        k.fillText(m ? "peak" : "avg", cx - r * 1.08, cy - 2 * f.dpr);
      }
    }
    g.clearRect(0, 0, w, h); g.drawImage(s.face, 0, 0);
    g.strokeStyle = rgba(f.c.text, f.isLive ? 0.95 : 0.55); g.lineWidth = 1.4 * f.dpr; g.lineCap = "round"; g.beginPath();
    for (let m = 0; m < 2; m++) {
      if (f.isLive) { s.v[m] += (170 * (want[m] - s.a[m]) - 16 * s.v[m]) * dt; s.a[m] += s.v[m] * dt; } // a spring: mass, a little overshoot
      else { s.a[m] = 0; s.v[m] = 0; }
      const cx = (w / 4) * (1 + 2 * m), a = top - A + Math.max(-0.02, Math.min(1.08, s.a[m])) * 2 * A;
      g.moveTo(cx, cy); g.lineTo(cx + Math.cos(a) * r * 1.02, cy + Math.sin(a) * r * 1.02);
    }
    g.stroke();
  } });
  add({ id: "particles", name: "Particles", kind: "both", draw(g, f) {
    const { w, h, s } = f;
    s.p ??= Array.from({ length: 72 }, (_, i) => ({ x: (i * 0.618) % 1, y: (i * 0.377) % 1, vx: 0.02 + (i % 7) * 0.006, vy: ((i % 5) - 2) * 0.004, a: 0 }));
    const gs = G();
    if (f.isLive && f.onset && gs) for (let i = 0; i < 16; i++) { // the burst: a ring of sparks out of the middle
      const p = s.p[(s.n = ((s.n || 0) + 1) % s.p.length)], ang = (i / 16) * TAU + Math.random() * 0.3, d = 0.25 + Math.random() * 0.3;
      gs.killTweensOf(p); Object.assign(p, { x: 0.5, y: 0.5, a: 1 });
      gs.to(p, { x: 0.5 + Math.cos(ang) * d * 0.6, y: 0.5 + Math.sin(ang) * d, a: 0, duration: 0.7 + Math.random() * 0.5, ease: "expo.out" });
    }
    g.clearRect(0, 0, w, h);
    const sp = f.isLive ? 0.4 + f.live * 3 : 0, sz = 1.4 * f.dpr;
    g.fillStyle = rgba(f.c.signal, f.isLive ? 0.35 : 0.25);
    for (const p of s.p) {
      p.x = (p.x + p.vx * sp * f.dt + 1) % 1; p.y = (p.y + p.vy * sp * f.dt + 1) % 1;
      if (p.a < 0.05) g.fillRect(p.x * w, p.y * h, sz, sz);
    }
    for (const p of s.p) if (p.a >= 0.05) { g.fillStyle = rgba(f.c.signal, p.a); g.fillRect(p.x * w - sz, p.y * h - sz, sz * 2, sz * 2); }
  } });
  add({ id: "tunnel", name: "Tunnel", kind: "both", draw(g, f) {
    const { w, h, s } = f, cx = w / 2, cy = h / 2;
    s.st ??= Array.from({ length: 110 }, (_, i) => ({ x: Math.cos(i * 2.4) * (0.2 + ((i * 0.37) % 1)), y: Math.sin(i * 2.4) * (0.2 + ((i * 0.37) % 1)), z: ((i * 0.618) % 1) * 0.95 + 0.05 }));
    const v = f.isLive ? 0.12 + f.live * 1.1 + f.kick * 2.2 : 0;
    g.clearRect(0, 0, w, h); g.lineCap = "round";
    for (const p of s.st) {
      const z0 = p.z; p.z -= v * f.dt;
      if (p.z <= 0.04) { p.z = 1; continue; }
      const k = 1 - p.z, x = cx + (p.x / p.z) * w * 0.12, y = cy + (p.y / p.z) * h * 0.3;
      if (x < 0 || x > w || y < 0 || y > h) { p.z = 1; continue; }
      g.strokeStyle = rgba(k > 0.8 ? f.c.text : f.c.signal, (f.isLive ? 0.25 : 0.15) + 0.75 * k * k); g.lineWidth = (0.6 + 1.6 * k) * f.dpr;
      g.beginPath(); g.moveTo(cx + (p.x / z0) * w * 0.12, cy + (p.y / z0) * h * 0.3); g.lineTo(x + (v ? 0 : 0.5), y); g.stroke();
    }
  } });

  // ---- the choice: per browser, on every page --------------------------------------
  function load() {
    let o = {};
    try { o = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch { /* private mode, a bad value: the defaults */ }
    want = o;
    return { scope: byId.has(o.scope) ? o.scope : DEF.scope, spectrum: byId.has(o.spectrum) ? o.spectrum : DEF.spectrum };
  }
  choice = load();
  function label(animate) { // the dock's name ticker
    for (const n of document.querySelectorAll(".viz-name[data-viz-slot]")) {
      const name = byId.get(choice[n.dataset.vizSlot])?.name || "";
      n.toggleAttribute("data-default", choice[n.dataset.vizSlot] === DEF[n.dataset.vizSlot]); // the classic: no name
      if (n.textContent === name) continue;
      n.textContent = name;
      if (animate) G()?.fromTo(n, { yPercent: 120, autoAlpha: 0 }, { yPercent: 0, autoAlpha: 1, duration: 0.5, ease: "power3.out", clearProps: "transform" });
    }
  }
  function changed(animate) { dirty = true; label(animate); document.dispatchEvent(new CustomEvent("visuals-change", { detail: { ...choice } })); }
  function choose(slot, id) {
    if (!(slot in DEF) || !byId.has(id) || choice[slot] === id) return;
    choice = { ...choice, [slot]: id };
    try { localStorage.setItem(KEY, JSON.stringify(choice)); } catch { /* not kept: this page still shows it */ }
    changed(true);
  }
  // another tab chose: follow it
  addEventListener("storage", (e) => { if (e.key === KEY) { choice = load(); changed(true); } });
  document.addEventListener("DOMContentLoaded", () => label(false));
  document.addEventListener("htmx:after:settle", () => label(false)); // a body morph empties the ticker

  Object.assign((S.visuals ??= {}), {
    list, add, get: (id) => byId.get(id), choice: (slot) => choice[slot], choose, render, draw,
    each: (fn) => hooks.add(fn), touch: () => { dirty = true; }, ink, rgba,
    cost: (c) => states.get(c)?.cost ?? null, drawnBy: (c) => states.get(c)?.id ?? null,
    kit: { BARS, trace, curve, fade, trail, glowLine, graticule, pair },
  });
})();
