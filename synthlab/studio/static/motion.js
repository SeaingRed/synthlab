// synthlab/studio/static/motion.js
// The motion island: everything that moves on screen without making sound.
//
// 1. Visuals that always say something. The scope and spectrum used to draw
//    only while the loop played: blank grey bars on load, and a frozen
//    picture while you played keys with the loop stopped. Now:
//      - live: whenever the output carries signal (loop, keys, auditions,
//        pads), whatever started it;
//      - at rest: the waveform overview and a log-band spectrum of the
//        current preview, decoded here on an OfflineAudioContext (no user
//        gesture needed), so the strip describes the sound you are editing;
//      - before any preview exists: a baseline, never an empty box.
//    Canvases are sized by a ResizeObserver (reading clientWidth every frame
//    forced layout 60x a second), typed arrays are reused, colours are cached
//    per theme, and nothing draws while the tab is hidden.
//    WHAT the two slots draw is visuals.js's (the registry
//    of variations, chosen on the Visuals page): this file measures the
//    output once a frame into one frame object and hands it over.
// 2. The peak meter + clip latch and the --live light.
// 3. UI motion on GSAP (vendored gsap.min.js; CSP is script-src 'self'):
//    stage/readout flashes when an edit changes them, the
//    readouts counting to their new values, drawers and curves opening, and
//    the chain bar's jump to a panel. Every tween is skipped under
//    calm motion (colours.js's Motion setting), and nothing here runs per frame except the
//    canvas draw.
// 240 -> 270: canvases re-fit after a body morph,
// settle work once per response, the view/curve/badge/readout motions keyed
// on real state changes, open curves kept across OOB row swaps, and --live
// written on its consumers only (throttled) instead of :root.
// 270 -> 282: --kick beside --live — the low end's
// onsets, a fast envelope over a slow average, written on the same consumers
// (the play buttons and the Song playhead too) so the light pumps on the beat.
// The same envelope is `synthlab.kick()`: the deck's keys and strip pump on it
// (deckart.js), so the kick is detected once.
// 282 -> 290: the spectrum drawn as the one
// signal graphic (a smooth area, a top stroke, a fade), and #view lit too
// (the view's stage light, style.css 32).
// 290 -> 240 (the Visuals page): the scope's and the spectrum's
// drawing is visuals.js's, its "Classic" entries; the frame is built
// here once and drawn there.
// 240 -> 255 (frontend.md, "The energy"): the
// energy published once — `--energy` / `--kick` on <body>, `html[data-alive]`
// while the output carries signal, `synthlab.energy()` — settling to exactly
// 0 at rest and never written under calm motion; the keys dock and the
// drum bank lit too; a value that moved leaves an afterglow.
// 255 -> 260: the entrance on Web Animations and the canvases sized from the observer, so neither forces a layout.
// 260 -> 265: calm is the Motion setting (colours.js), not the
// OS's reduced motion, and switching to it takes the light down at once.
// (The view entrance is pagemotion.js's, the page transition.)
// Budget: <= 265 code lines.

(() => {
  "use strict";
  const S = () => window.synthlab || {};
  const calm = () => window.synthlab.calm(); // the Motion setting (colours.js), never the OS's
  const G = () => (!calm() && window.gsap) || null;
  const BARS = 72;
  const PRIMED = new Set(["editor", "library", "breed", "map", "bass", "drums", "perform", "visuals"]);

  // ---- canvas sizing ------------------------------------------------------
  let dirty = true;
  // A body innerMorph syncs the canvases' attributes to the server's (none),
  // so width/height fall back to 300x150 while the CSS box is unchanged and
  // the ResizeObserver never fires: fit() runs on every settle as well.
  // The settle's fit reads the size the observer last reported, never the
  // box: reading clientWidth there would force the swapped page's whole
  // layout early, ~100 ms a view switch flagged as a forced reflow
  const boxes = new WeakMap(); // canvas -> [w, h] (CSS px), the observer's
  function fit(c) {
    const [cw, ch] = boxes.get(c) ?? [c.clientWidth, c.clientHeight];
    const w = Math.max(1, Math.round(cw * devicePixelRatio)), h = Math.max(1, Math.round(ch * devicePixelRatio));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; dirty = true; }
  }
  const sizer = new ResizeObserver((entries) => {
    for (const e of entries) { const b = e.contentBoxSize[0]; boxes.set(e.target, [b.inlineSize, b.blockSize]); fit(e.target); }
    dirty = true;
  });
  let scope = null, spec = null;
  function bindCanvases() {
    const s = document.getElementById("scope"), p = document.getElementById("spectrum");
    if (s === scope && p === spec) return;
    [scope, spec].forEach((c) => c && sizer.unobserve(c));
    scope = s; spec = p;
    [scope, spec].forEach((c) => c && sizer.observe(c));
    dirty = true;
  }

  // ---- the rest picture: overview + log-band spectrum of the preview -----
  let rest = null, restUrl = null; // {peaks: Float32Array pairs, bands: Float32Array}
  function fft(re, im) { // in-place radix-2, n a power of two
    const n = re.length;
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const a = (-2 * Math.PI) / len, wr = Math.cos(a), wi = Math.sin(a);
      for (let i = 0; i < n; i += len) {
        let cr = 1, ci = 0;
        for (let k = 0; k < len / 2; k++) {
          const ur = re[i + k], ui = im[i + k];
          const vr = re[i + k + len / 2] * cr - im[i + k + len / 2] * ci;
          const vi = re[i + k + len / 2] * ci + im[i + k + len / 2] * cr;
          re[i + k] = ur + vr; im[i + k] = ui + vi;
          re[i + k + len / 2] = ur - vr; im[i + k + len / 2] = ui - vi;
          [cr, ci] = [cr * wr - ci * wi, cr * wi + ci * wr];
        }
      }
    }
  }
  // BARS (72) bars of equal width in log frequency, 40 Hz .. Nyquist — the live
  // spectrum uses the same mapping, so rest and live read alike
  function bandEdges(bins, rate) {
    const edges = [], lo = Math.log(40), hi = Math.log(rate / 2);
    for (let b = 0; b <= BARS; b++) edges.push(Math.min(bins - 1, Math.max(1, Math.round((Math.exp(lo + (hi - lo) * b / BARS) / (rate / 2)) * bins))));
    return edges;
  }
  function analyse(buf) {
    const d = buf.getChannelData(0), W = 360, peaks = new Float32Array(W * 2), step = d.length / W;
    for (let x = 0; x < W; x++) {
      let lo = 1, hi = -1;
      for (let i = (x * step) | 0, end = ((x + 1) * step) | 0; i < end; i++) { if (d[i] < lo) lo = d[i]; if (d[i] > hi) hi = d[i]; }
      peaks[2 * x] = lo; peaks[2 * x + 1] = hi;
    }
    const N = 2048, mag = new Float32Array(N / 2), re = new Float32Array(N), im = new Float32Array(N);
    const hops = Math.max(1, Math.min(12, Math.floor(d.length / N)));
    for (let h = 0; h < hops; h++) {
      const at = Math.floor((d.length - N) * (h + 0.5) / hops);
      for (let i = 0; i < N; i++) { re[i] = (d[at + i] || 0) * (0.5 - 0.5 * Math.cos((2 * Math.PI * i) / N)); im[i] = 0; }
      fft(re, im);
      for (let i = 0; i < N / 2; i++) mag[i] += Math.hypot(re[i], im[i]) / hops;
    }
    const edges = bandEdges(N / 2, buf.sampleRate), bands = new Float32Array(BARS);
    for (let b = 0; b < BARS; b++) {
      let pk = 0;
      for (let i = edges[b]; i <= edges[b + 1]; i++) pk = Math.max(pk, mag[i]);
      bands[b] = Math.max(0, Math.min(1, (20 * Math.log10(pk + 1e-9) + 10) / 60)); // ~-50..+10 dB
    }
    return { peaks, bands };
  }
  // While the loop plays the strip is live, so the rest picture waits until
  // it stops; the loop's own decoded buffer is reused when it is this URL.
  let restWant = null;
  async function loadRest(url) {
    if (!url || url === restUrl) return;
    if (S().isPlaying?.()) { restWant = url; return; }
    restUrl = url; restWant = null;
    const b = S().lastBufferUrl?.() === url && S().lastBuffer?.();
    if (b) { rest = analyse(b); dirty = true; return; }
    try {
      const bytes = await (await fetch(url)).arrayBuffer();
      const buf = await new OfflineAudioContext(1, 1, 44100).decodeAudioData(bytes);
      if (restUrl === url) { rest = analyse(buf); dirty = true; }
    } catch { /* a stale key 404s after eviction: keep the last picture */ }
  }
  document.addEventListener("preview-ready", (e) => loadRest(e.detail?.url));
  // a fresh page has no preview yet: ask for this view's own mix once, idle
  let primed = null;
  function primeRest() {
    const view = document.getElementById("view")?.dataset.view;
    // only views whose preview is a plain loop render (Song plays a plan)
    if (rest || primed === view || !PRIMED.has(view) || !document.getElementById("scope") || !window.htmx) return;
    primed = view; // once per view: a failed decode must not re-ask forever
    setTimeout(() => {
      if (!rest) S().post("/fragment/preview", { view });
    }, 800);
  }

  // ---- the frame ----------------------------------------------------------
  // What the dock draws is visuals.js's: this measures the output once a
  // frame into F and hands it over (the scope and spectrum slots, and the
  // Visuals page's gallery, all read the same frame — one analyser, read
  // once). At rest F carries the rest picture: the preview's overview
  // (`peaks`) and its log bands, a flat wave; `dirty` asks for one redraw.
  let wave = null, freq = null, edges = null, live = new Float32Array(BARS);
  const still = new Float32Array(BARS), flat = new Uint8Array(2048).fill(128);
  const F = { now: 0, dt: 0, isLive: false, dirty: true, live: 0, kick: 0, rms: 0, peak: 0, wave: flat, freq: null, bands: still, peaks: null, rate: 44100 };
  let liveUntil = 0, smooth = 0, written = -1, writtenAt = 0, meterPeak = 0, wasLive = false, last = 0;
  let kick = 0, lowAvg = 0, kickWritten = -1; // the low end's onsets: the beat's pump (--kick)
  (window.synthlab ??= {}).kick = () => kick; // deckart.js's keys and strip pump on the same kick
  window.synthlab.energy = () => smooth; // the level, 0..1: the same number as --energy / --live
  // --live is registered non-inherited (style.css @property) and written only
  // on its few consumers, ~15x a second: on :root, 60x a second, every write
  // restyled the whole document. THE ENERGY is the same two numbers published
  // once on <body> (`--energy`, `--kick`: non-inherited too, so <body> alone
  // restyles) for a script, a test or a deck to read — not on <html>, whose
  // style deckart.js and visuals.js watch for the hues (they re-read the
  // palette 20x a second when it was there); at rest both settle to exactly 0
  // and nothing more is written; under calm motion nothing is
  const LIT = "#audio-led, .panel, .viz, .transport, #scope, #spectrum, #xyghost, .play, .cable.live, .btn-play, .song-playhead, #view, .keys-dock, .drum-bank";
  const root = document.documentElement;
  let lit = null;
  function light(now, isLive) {
    if (!isLive && smooth < 0.02) smooth = 0; // settle to rest: the last write is a true 0
    if (!isLive && kick < 0.04) kick = 0;
    const moved = Math.abs(smooth - written) > 0.02 || Math.abs(kick - kickWritten) > 0.04 || (!smooth && written) || (!kick && kickWritten);
    if (now - writtenAt < 50 || !moved || calm()) return;
    written = smooth; kickWritten = kick; writtenAt = now;
    const l = smooth.toFixed(2), k = kick.toFixed(2);
    for (const el of (lit ??= [...document.querySelectorAll(LIT), document.body])) {
      el.style.setProperty(el === document.body ? "--energy" : "--live", l);
      el.style.setProperty("--kick", k);
    }
  }
  // the Motion setting moved (colours.js): calm takes the light down at once
  // (what was written stays put otherwise), full picks it up on the next frame
  document.addEventListener("synthlab-motion", () => {
    wasLive = null; written = kickWritten = -1; // data-alive and the light, again
    if (calm()) for (const el of [...document.querySelectorAll(LIT), document.body]) for (const p of ["--live", "--energy", "--kick"]) el.style.removeProperty(p);
  });
  function frame(now) {
    requestAnimationFrame(frame);
    if (document.hidden) return;
    const an = S().getAnalyser?.();
    let rms = 0, pk = 0;
    if (an) {
      if (!wave || wave.length !== an.fftSize) { wave = new Uint8Array(an.fftSize); freq = new Uint8Array(an.frequencyBinCount); edges = bandEdges(an.frequencyBinCount, an.context.sampleRate); }
      an.getByteTimeDomainData(wave);
      let sum = 0;
      for (let i = 0; i < wave.length; i++) { const v = (wave[i] - 128) / 128; sum += v * v; if (Math.abs(v) > pk) pk = Math.abs(v); }
      rms = Math.sqrt(sum / wave.length);
      meter(pk);
      if (rms > 0.002 || S().isPlaying?.()) liveUntil = now + 350; // hold briefly over gaps
      // the kick: the energy under 150 Hz rising over its own slow average
      an.getByteFrequencyData(freq);
      const top = Math.max(1, Math.round((150 / (an.context.sampleRate / 2)) * freq.length));
      let low = 0;
      for (let i = 0; i < top; i++) low += freq[i];
      low /= top * 255;
      kick = Math.max(Math.min(1, Math.max(0, low - lowAvg * 1.15) * 6), kick * 0.82);
      lowAvg += (low - lowAvg) * 0.05;
    } else kick *= 0.82;
    smooth += (Math.min(1, rms * 2.5) - smooth) * 0.2;
    const isLive = !!an && now < liveUntil;
    if (isLive !== wasLive) root.toggleAttribute("data-alive", isLive && !calm());
    light(now, isLive);
    bindCanvases();
    if (isLive) for (let b = 0; b < BARS; b++) { // freq: read above, for the kick
      let top = 0;
      for (let i = edges[b]; i <= edges[b + 1]; i++) if (freq[i] > top) top = freq[i];
      live[b] = Math.max(top / 255, live[b] * 0.86); // fall slowly: readable, not jittery
    } else if (restWant && !S().isPlaying?.()) loadRest(restWant);
    Object.assign(F, { now, dt: Math.min(0.1, (now - last) / 1000), isLive, dirty: dirty || wasLive !== isLive, live: smooth, kick, rms, peak: pk,
      wave: isLive ? wave : flat, freq: isLive ? freq : null, bands: isLive ? live : rest?.bands || still, peaks: rest?.peaks || null, rate: an?.context.sampleRate || 44100 });
    last = now; dirty = false; wasLive = isLive;
    S().visuals?.render(F, scope, spec);
  }
  let held = 0, heldAt = 0;
  function meter(pk) {
    const m = document.getElementById("meter");
    if (!m) return;
    meterPeak = Math.max(pk, meterPeak * 0.9);
    m.style.setProperty("--level", `${Math.min(1, meterPeak) * 100}%`);
    // the held tick (style.css .meter::after): the loudest of the last 1.2 s
    const at = performance.now();
    if (meterPeak >= held || at - heldAt > 1200) { held = meterPeak; heldAt = at; m.style.setProperty("--peak", `${Math.min(1, held) * 100}%`); }
    if (pk >= 0.99) m.classList.add("clipped");
  }
  document.addEventListener("click", (e) => e.target.closest?.("#meter")?.classList.remove("clipped"));

  // ---- GSAP UI motion -----------------------------------------------------
  // Settle work runs once per response (htmx settles every OOB task), on the
  // next frame, against the page as it now stands.
  let settleQ = 0;
  document.addEventListener("htmx:after:settle", () => { settleQ ||= requestAnimationFrame(() => { settleQ = 0; settle(); }); });
  // (a page's entrance is pagemotion.js's: the page
  // transition — the old page leaving, the new one's modules arriving on GSAP)
  let wasEdited = false;
  // flash what an edit changed, count readouts to value
  let lastStages = [], lastNums = [];
  const counting = new WeakMap(); // readout <b> -> {o, to}: a tween and the value it lands on
  function settle() {
    const g = G();
    lit = null; written = -1;
    const stages = [...document.querySelectorAll(".signal-chain .stage span")];
    const texts = stages.map((s) => s.textContent);
    if (g && lastStages.length === texts.length) stages.forEach((s, i) => {
      if (texts[i] !== lastStages[i]) g.fromTo(s.parentElement, { "--flash": 1 }, { "--flash": 0, duration: 0.9, ease: "power2.out" });
    });
    lastStages = texts;
    // a readout mid-count reads as its target, never as the in-between text —
    // unless the server rewrote it in place (a morph keeps the element)
    const nums = [...document.querySelectorAll("#analysis b")];
    const vals = nums.map((b) => { const c = counting.get(b); return c && c.shown === b.textContent ? c.to : parseFloat(b.textContent); });
    if (g && lastNums.length === vals.length) nums.forEach((b, i) => {
      const to = vals[i], from = lastNums[i], dp = (b.textContent.split(".")[1] || "").length, prev = counting.get(b);
      if (prev && (prev.shown !== b.textContent || to !== prev.to)) { g.killTweensOf(prev.o); counting.delete(b); }
      if (!Number.isFinite(to) || !Number.isFinite(from) || to === from || counting.has(b)) return;
      const o = { v: from }, c = { o, to, shown: b.textContent };
      counting.set(b, c);
      g.to(o, { v: to, duration: 0.45, ease: "power1.out", onUpdate: () => { if (b.textContent !== c.shown) return g.killTweensOf(o); b.textContent = c.shown = o.v.toFixed(dp); }, onComplete: () => counting.delete(b) });
    });
    lastNums = vals;
    // "edited" pops once, when the sound first moves: the OOB swap replaces
    // the badge every edit, so the state lives here, not on the element
    const edited = document.querySelector(".sid-edited");
    if (edited && !wasEdited) g?.from(edited, { scale: 0.4, autoAlpha: 0, duration: 0.3, ease: "back.out(2)" });
    wasEdited = !!edited;
    bindCanvases();
    for (const c of [scope, spec]) if (c) fit(c);
    primeRest();
  }
  // Inline automation curves. Unfold when a slot OPENS (not on every point
  // added); keep the row's ⤳ honest from the curve the response carries; and
  // survive a macro move, whose OOB row swap replaces the row — slot and all.
  let curves = new Map(); // row id -> {row, box} for each open curve
  document.addEventListener("htmx:after:swap", () => {
    for (const [id, { row, box }] of curves) {
      const now = document.getElementById(id), slot = now?.querySelector(".curve-slot");
      // a clone: htmx cleaned the old nodes up on removal and will not re-init them
      if (now && now !== row && slot && !slot.firstElementChild) { slot.append(box.cloneNode(true)); window.htmx?.process(slot); }
    }
    const open = new Map();
    for (const box of document.querySelectorAll(".curve-slot > .curve-box")) {
      const slot = box.parentElement, row = slot.closest(".param"), btn = slot.previousElementSibling;
      if (!row) continue;
      if (!curves.has(row.id)) G()?.from(box, { height: 0, autoAlpha: 0, duration: 0.3, ease: "power2.out", clearProps: "height" });
      open.set(row.id, { row, box });
      const on = box.querySelectorAll("svg.curve circle").length >= 2; // forms: has_curve = 2+ points
      if (btn?.classList.contains("curve-toggle")) { btn.classList.toggle("has-curve", on); btn.setAttribute("aria-pressed", String(on)); }
    }
    curves = open;
  });
  // drawers (play tools, perform controls, bass check, presets) open smoothly
  document.addEventListener("toggle", (e) => {
    const g = G(), d = e.target;
    if (!g || !(d instanceof HTMLDetailsElement) || !d.open) return;
    const body = [...d.children].filter((c) => c.tagName !== "SUMMARY");
    if (body.length) g.from(body, { y: -6, autoAlpha: 0, duration: 0.28, ease: "power2.out", stagger: 0.03, clearProps: "all" });
  }, true);
  // the chain bar: glide to the panel, then light it so the eye lands there
  document.addEventListener("click", (e) => {
    const a = e.target.closest?.(".signal-chain a[href^='#']");
    if (!a) return;
    const panel = document.querySelector(a.getAttribute("href"));
    if (!panel) return;
    e.preventDefault();
    panel.scrollIntoView({ behavior: calm() ? "auto" : "smooth", block: "start" });
    G()?.fromTo(panel, { "--flash": 1 }, { "--flash": 0, duration: 1.4, delay: 0.25, ease: "power2.out" });
  });
  // a card acknowledges the click that auditions or loads it — the dock's,
  // the Library's and the Breed board's alike
  document.addEventListener("click", (e) => {
    const hit = e.target.closest?.(".sound-card .load, .sound-card .audition, .lib-card button, .tile button");
    const card = hit?.closest(".sound-card, .lib-card, .tile");
    if (card) G()?.fromTo(card, { "--flash": 1 }, { "--flash": 0, duration: 0.8, ease: "power2.out" });
  });
  // a value that moved leaves an afterglow on what changed — a knob, a fader,
  // a slider, a step (style.css 39): --after 1 -> 0 on the control itself, the
  // class only while it decays; each input of a drag restarts it (a hand, a
  // dial, a CC: tweak.js sends the same event). None under calm motion
  document.addEventListener("input", (e) => {
    const el = e.target, g = G();
    if (!g || !el.matches?.('input[type="range"], .grid .cell') || el.closest("#colours-pop")) return; // (its sliders' sync is no edit)
    el.classList.add("afterglow");
    g.killTweensOf(el, "--after");
    g.fromTo(el, { "--after": 1 }, { "--after": 0, duration: 0.7, ease: "power2.out",
      onComplete: () => { el.classList.remove("afterglow"); el.style.removeProperty("--after"); } });
  }, true);

  // the frame loop starts first: nothing in settle() may keep the visuals off
  document.addEventListener("DOMContentLoaded", () => { requestAnimationFrame(frame); wasEdited = !!document.querySelector(".sid-edited"); settle(); });
})();
