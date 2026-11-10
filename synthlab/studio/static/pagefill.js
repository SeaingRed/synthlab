// synthlab/studio/static/pagefill.js
// The filled pages' live parts (no page leaves a region empty at
// 2560 x 1400;
// style.css 47, dense.css D47). Three, each only while its page shows:
// - Song: the open pattern editor's ROWS share the room the editor may
//   take (`--roll-row`, 14-26 px: what is left of the pane's cap — 55 % of
//   the view under the playlist, the pane itself beside it or dragged —
//   after its tools and lanes, a row each), and right of the pattern's
//   last step (a step is at most 48 px: dense.css) the pattern is drawn
//   again, as fading GHOSTS — a 1-bar riff shows how it repeats instead of
//   a void. One canvas in the grid (`.roll-ghost`), redrawn on a settle, a
//   step's input and a resize; it slides in when a pattern opens.
// - Mix: the master's read-outs (views/mixer.html #mm-read), off the
//   master bus — a K-weighted tap (BS.1770's shelf and high-pass as Web
//   Audio biquads — a high-pass's Q is in dB there: −6.02 is BS.1770's 0.5 —
//   near, not certified; the export's own number is the view's "measure";
//   a 997 Hz sine at −20 dBFS on both sides reads −20.2) for the momentary (400 ms), short-term (3 s),
//   integrated (gated, from the last ▶ or a click) loudness and the range;
//   the plain two sides for the sample peak and the correlation. Numbers
//   are counted to each new value with GSAP four times a second; the bars
//   move every frame; the spectrum and the stereo field are the Visuals
//   page's 1/3-octave and goniometer, drawn from motion.js's one frame
//   (`visuals.each`); the history is the short-term over 30 s with the
//   target's line. Nothing is read while another page shows.
// - Breed: a new litter rises in card by card, its spectra grow from the
//   floor and the "what changed" bars run out to their values (GSAP).
// Motion through G(): none under calm motion (the Motion setting, never
// the OS's).
// Budget: <= 200 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const $ = (id) => document.getElementById(id);
  const calm = () => window.synthlab.calm?.() ?? false;
  const G = () => (!calm() && window.gsap) || null;
  let queued = 0;
  const queue = () => { queued ||= requestAnimationFrame(() => { queued = 0; fitRoll(); ghosts(); litter(); }); };

  // ---- Song: the editor's rows ------------------------------------------------
  function fitRoll() {
    const dr = $("song-drawer"), v = $("view"), g = dr && !dr.hidden ? dr.querySelector(".grid.roll") : null;
    if (!g || !v) return;
    const rows = g.querySelectorAll(":scope > .gridrow:not(.sliderow)");
    if (!rows.length) return;
    const wide = innerWidth >= 2200, beside = wide && v.dataset.tab === "beside";
    const docked = !!v.querySelector(":scope > .song-dock:not([hidden])");
    // the pane's cap: its share of the view (55 %, 26 % with the sound dock), and never past what
    // the playlist keeps (min(396 px, 45 %) under the head and the transport: dense.css)
    const free = !beside && !v.style.getPropertyValue("--drawer-h"), H = v.clientHeight;
    const above = (v.querySelector(":scope > .song-arranger")?.getBoundingClientRect().top ?? 0) - v.getBoundingClientRect().top;
    const cap = free ? Math.min(H * (docked ? 0.26 : 0.55), H - above - Math.min(396, H * 0.45) - 2) : dr.clientHeight;
    const gap = parseFloat(getComputedStyle(g).rowGap) || 2, rowH = rows[0].getBoundingClientRect().height;
    const other = dr.scrollHeight - rows.length * (rowH + gap);
    const h = Math.max(14, Math.min(26, Math.floor((cap - other) / rows.length - gap)));
    if (Math.abs(h - rowH) >= 1) dr.style.setProperty("--roll-row", `${h}px`);
  }

  // ---- Song: the ghost repeats ---------------------------------------------------
  let ghostKey = "";
  function ghosts() {
    const dr = $("song-drawer"), g = dr && !dr.hidden ? dr.querySelector(".grid") : null;
    const rows = g ? [...g.querySelectorAll(":scope > .gridrow:not(.sliderow)")] : [];
    const steps = rows[0] ? [...rows[0].querySelectorAll(":scope > .cell")] : [];
    let cv = g?.querySelector(":scope > .roll-ghost");
    if (steps.length < 2 || !S.visuals?.ink) { cv?.remove(); return; }
    const gr = g.getBoundingClientRect(), X = (el) => el.getBoundingClientRect().left - gr.left + g.scrollLeft;
    const Y = (el) => el.getBoundingClientRect().top - gr.top + g.scrollTop;
    const last = steps.at(-1), cw = last.getBoundingClientRect().width, gap = X(last) - X(steps.at(-2)) - cw;
    const x0 = X(last) + cw + gap, period = x0 - X(steps[0]), w = Math.floor(g.clientWidth - x0 - 2);
    if (w < Math.min(period * 0.25, 120)) { cv?.remove(); return; }
    const fresh = !cv, top = Y(rows[0]), endRow = rows.at(-1), h = Math.ceil(Y(endRow) + endRow.getBoundingClientRect().height - top);
    cv ??= g.appendChild(Object.assign(document.createElement("canvas"), { className: "roll-ghost", ariaHidden: "true" }));
    const dpr = devicePixelRatio || 1;
    Object.assign(cv.style, { left: `${x0}px`, top: `${top}px`, width: `${w}px`, height: `${h}px` });
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    const c = cv.getContext("2d"), ink = S.visuals.ink(), rgba = S.visuals.rgba, dx = steps.map((s) => X(s) - X(steps[0]));
    c.scale(dpr, dpr);
    for (let k = 0; k * period < w; k++) {
      const a = 0.62 * 0.74 ** k, at = k * period;
      c.fillStyle = rgba(ink.line, 0.9); c.fillRect(at - gap / 2 - 0.5, 0, 1, h); // the bar line where a repeat starts
      for (const row of rows) {
        const y = Y(row) - top, rh = row.getBoundingClientRect().height, cells = row.querySelectorAll(":scope > .cell");
        cells.forEach((cell, s) => {
          const x = at + (dx[s] ?? 0);
          if (x >= w) return;
          c.fillStyle = cell.checked ? rgba(ink.accent, a) : rgba(ink.inset, 0.55);
          c.fillRect(x, y, Math.min(cw, w - x), rh);
        });
      }
    }
    const key = `${dr.dataset.patternId}|${steps.length}|${rows.length}`;
    if (fresh && key !== ghostKey) G()?.fromTo(cv, { autoAlpha: 0, x: -18 }, { autoAlpha: 1, x: 0, duration: 0.55, ease: "power3.out" });
    ghostKey = key;
  }
  document.addEventListener("input", (e) => { if (e.target.closest?.("#song-drawer .grid")) queue(); }, true);
  document.addEventListener("click", (e) => { if (e.target.closest?.('[data-tab-of="view"]')) queue(); });
  // a settle, a resize of the window or of the pane (a splitter's drag, the sound dock)
  const pane = new ResizeObserver(() => queue());
  let watched = null;
  function settled() {
    const dr = $("song-drawer");
    if (dr !== watched) { if (watched) pane.unobserve(watched); if (dr) pane.observe(dr); watched = dr; }
    queue();
  }
  document.addEventListener("htmx:after:settle", settled);
  for (const ev of ["resize", "synthlab-geometry"]) addEventListener(ev, queue); // (a splitter let go: synthlab-geometry)

  // ---- Breed: a new litter --------------------------------------------------------
  const seen = new WeakSet();
  function litter() {
    const b = $("breed-board");
    if (!b || seen.has(b) || !b.querySelector(".tile")) return;
    seen.add(b);
    const g = G();
    if (!g) return;
    g.from(b.querySelectorAll(".tile"), { y: 22, autoAlpha: 0, duration: 0.5, stagger: 0.055, ease: "power3.out", clearProps: "all" });
    g.from(b.querySelectorAll(".t-spec"), { scaleY: 0, transformOrigin: "50% 100%", duration: 0.7, stagger: 0.055, ease: "power2.out", clearProps: "transform" });
    g.from(b.querySelectorAll(".t-d-bars > i"), { scaleX: 0, duration: 0.6, stagger: 0.012, delay: 0.25, ease: "power2.out", clearProps: "transform" });
  }

  // ---- Mix: the master's read-outs ---------------------------------------------------
  const N = 2048, lufs = (z) => (z > 1e-10 ? -0.691 + 10 * Math.log10(z) : -Infinity), mean = (a) => a.reduce((x, y) => x + y, 0) / (a.length || 1);
  let tap = null, ms = [], blocks = [], hist = [], peak = { v: 0, at: 0, max: 0 }, corr = 0, playing = false, tick = 0, box = null;
  function taps(c, m) {
    if (tap?.c === c) return tap;
    const up = new GainNode(c, { channelCount: 2, channelCountMode: "explicit", channelInterpretation: "speakers" }); // mono reaches both sides
    const raw = c.createChannelSplitter(2), kw = c.createChannelSplitter(2), an = () => new AnalyserNode(c, { fftSize: N });
    const a = [an(), an()], k = [an(), an()];
    m.connect(up); up.connect(raw);
    up.connect(new BiquadFilterNode(c, { type: "highshelf", frequency: 1681, gain: 4 })).connect(new BiquadFilterNode(c, { type: "highpass", frequency: 38, Q: -6.02 })).connect(kw);
    a.forEach((x, i) => raw.connect(x, i)); k.forEach((x, i) => kw.connect(x, i));
    return (tap = { c, a, k, L: new Float32Array(N), R: new Float32Array(N) });
  }
  const reset = () => { blocks = []; peak.max = 0; };
  function integrated() { // BS.1770's gates: under -70 LUFS out, then 10 LU under the mean of the rest
    const abs = blocks.filter((z) => lufs(z) > -70);
    const rel = lufs(mean(abs)) - 10;
    return abs.length ? lufs(mean(abs.filter((z) => lufs(z) > rel))) : -Infinity;
  }
  function range() { // the 10th to the 95th percentile of the short-term (gated at -70)
    const v = hist.filter((x) => x > -70).sort((p, q) => p - q);
    return v.length > 10 ? v[Math.floor(v.length * 0.95)] - v[Math.floor(v.length * 0.1)] : NaN;
  }
  const counting = new WeakMap();
  function put(id, v, digits = 1) {
    const o = $(id);
    if (!o) return;
    counting.get(o)?.kill();
    if (!Number.isFinite(v)) { if (o.textContent !== "—") o.textContent = "—"; delete o.dataset.v; return; }
    const from = Number(o.dataset.v), g = G(), say = (x) => { o.textContent = x.toFixed(digits).replace("-", "−"); };
    o.dataset.v = String(v);
    if (!g || !Number.isFinite(from) || Math.abs(from - v) < 10 ** -digits) { say(v); return; }
    const t = { x: from };
    counting.set(o, g.to(t, { x: v, duration: 0.24, ease: "power1.out", onUpdate: () => say(t.x) }));
  }
  function master(now) {
    requestAnimationFrame(master);
    const read = $("mm-read"), c = S.getCtx?.(), m = S.getMaster?.();
    if (read !== box) { box = read; if (read) G()?.from(read.parentElement.querySelectorAll(".mm-cell, .mm-scope"), { y: 10, autoAlpha: 0, duration: 0.45, stagger: 0.04, ease: "power2.out", clearProps: "all" }); }
    if (!read || !c || !m) return;
    const t = taps(c, m), { L, R } = t;
    t.a[0].getFloatTimeDomainData(L); t.a[1].getFloatTimeDomainData(R);
    let lr = 0, ll = 0, rr = 0, pk = 0, z = 0;
    for (let i = 0; i < N; i++) { lr += L[i] * R[i]; ll += L[i] * L[i]; rr += R[i] * R[i]; pk = Math.max(pk, Math.abs(L[i]), Math.abs(R[i])); }
    t.k[0].getFloatTimeDomainData(L); t.k[1].getFloatTimeDomainData(R);
    for (let i = 0; i < N; i++) z += L[i] * L[i] + R[i] * R[i];
    const gv = Math.max(1e-4, m.gain.value); // the bus's gain is the listening level (#volume): read the mix, not how loud it is heard
    pk /= gv;
    ms.push([now, z / N / (gv * gv)]);
    while (ms.length && now - ms[0][0] > 3000) ms.shift();
    const live = pk > 1e-5, on = !!(S.songClock?.().playing || S.isPlaying?.());
    if (on && !playing) reset();
    playing = on;
    if (ll * rr > 1e-12) corr += (lr / Math.sqrt(ll * rr) - corr) * 0.12;
    if (pk >= peak.v || now - peak.at > 1500) { peak.v = pk; peak.at = now; }
    peak.max = Math.max(peak.max, pk);
    const mom = mean(ms.filter((x) => now - x[0] <= 400).map((x) => x[1])), st = lufs(mean(ms.map((x) => x[1])));
    $("mm-st-bar")?.style.setProperty("--v", Number.isFinite(st) ? Math.max(0, Math.min(1, 1 + st / 48)).toFixed(3) : 0);
    $("mm-corr-at")?.style.setProperty("--v", corr.toFixed(3));
    read.querySelector(".mm-corr")?.toggleAttribute("data-neg", live && corr < 0);
    if (now - tick < 100) return;
    const quarter = Math.floor(now / 250) !== Math.floor(tick / 250);
    tick = now;
    if (live) { blocks.push(mom); hist.push(st); } else restScopes();
    if (hist.length > 300) hist.shift();
    drawHist(live);
    if (!quarter) return;
    put("mm-st", live ? st : NaN); put("mm-mom", live ? lufs(mom) : NaN); put("mm-int", integrated());
    put("mm-pk", peak.v > 1e-5 ? 20 * Math.log10(peak.v) : NaN); put("mm-pk-max", peak.max > 1e-5 ? 20 * Math.log10(peak.max) : NaN);
    put("mm-corr", live ? corr : NaN, 2); put("mm-lra", range());
  }
  function drawHist(live) { // the short-term over 30 s, -48..0 LUFS, the target's line on it
    const cv = $("mm-hist"), target = Number($("song-loudness-target")?.selectedOptions[0]?.dataset.lufs);
    if (!cv || !S.visuals?.ink) return;
    const dpr = devicePixelRatio || 1, w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientHeight * dpr);
    if (!live && cv.width === w && cv.height === h && cv.dataset.target === String(target)) return; // at rest: drawn once
    Object.assign(cv, { width: w, height: h }); cv.dataset.target = String(target);
    const c = cv.getContext("2d"), ink = S.visuals.ink(), rgba = S.visuals.rgba, y = (l) => h * Math.min(1, Math.max(0, -l / 48));
    c.clearRect(0, 0, w, h);
    if (Number.isFinite(target)) { c.fillStyle = rgba(ink.accent, 0.8); c.fillRect(0, y(target), w, Math.max(1, dpr)); }
    if (hist.length < 2) return;
    c.beginPath();
    hist.forEach((l, i) => { const X = w - (hist.length - 1 - i) * (w / 299), Y = Number.isFinite(l) ? y(l) : h; i ? c.lineTo(X, Y) : c.moveTo(X, Y); });
    c.lineWidth = 1.4 * dpr; c.strokeStyle = rgba(ink.signal, 0.95); c.stroke();
    c.lineTo(w, h); c.lineTo(w - (hist.length - 1) * (w / 299), h); c.closePath(); c.fillStyle = rgba(ink.signal, 0.16); c.fill();
  }
  // the spectrum and the stereo field: the Visuals page's variations, sized to their wells;
  // at rest (a variation draws nothing then) each shows its empty face — the 1/3-octave's
  // 28 wells, the goniometer's diamond and its correlation line, where the variation puts them
  const SCOPES = [["mm-spec", "octave"], ["mm-gonio", "goniometer"]];
  function sized(cv) {
    const dpr = devicePixelRatio || 1, w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientHeight * dpr);
    if (cv.width !== w || cv.height !== h) { Object.assign(cv, { width: w, height: h }); delete cv.dataset.rest; }
    return w && h;
  }
  function rest(cv, vis) {
    const g = cv.getContext("2d"), { width: w, height: h } = cv, d = devicePixelRatio || 1;
    g.clearRect(0, 0, w, h); g.fillStyle = g.strokeStyle = S.visuals.rgba(S.visuals.ink().line, 0.6); g.lineWidth = d;
    cv.dataset.rest = vis;
    if (vis === "octave") { const foot = h >= 40 * d ? 11 * d : 0, bw = w / 28; for (let b = 0; b < 28; b++) g.fillRect(b * bw + bw * 0.12, 0, bw * 0.76, h - foot - 2 * d); return; }
    const ry = h * 0.46, rx = Math.min(w * 0.2, h * 1.3), bar = Math.min(w - 2 * rx - 24 * d, 180 * d), cx = Math.max(rx + 6 * d, (w - 2 * rx - 12 * d - bar) / 2 + rx), cy = h / 2;
    g.beginPath(); g.moveTo(cx, cy - ry); g.lineTo(cx + rx, cy); g.lineTo(cx, cy + ry); g.lineTo(cx - rx, cy); g.closePath();
    g.moveTo(cx, cy - ry); g.lineTo(cx, cy + ry); g.moveTo(cx - rx, cy); g.lineTo(cx + rx, cy); g.stroke();
    if (bar >= 20) { const x0 = cx + rx + 12 * d; g.fillRect(x0, cy, bar, d); g.fillRect(x0 + bar / 2, cy - 3 * d, d, 6 * d); }
  }
  function scopes(f, draw) {
    for (const [id, vis] of SCOPES) {
      const cv = $(id);
      if (!cv || !sized(cv)) continue;
      draw(cv, vis, f);
      if (f.isLive) delete cv.dataset.rest; else rest(cv, vis);
    }
  }
  function restScopes() { for (const [id, vis] of SCOPES) { const cv = $(id); if (cv && sized(cv) && cv.dataset.rest !== vis && S.visuals?.ink) rest(cv, vis); } }
  document.addEventListener("click", (e) => { if (e.target.closest?.("#mm-int-reset")) { reset(); put("mm-int", NaN); } });
  document.addEventListener("DOMContentLoaded", () => { S.visuals?.each(scopes); requestAnimationFrame(master); settled(); });
})();
