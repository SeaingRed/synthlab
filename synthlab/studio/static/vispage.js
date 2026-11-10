// synthlab/studio/static/vispage.js
// The Visuals page (/view/visuals): its gallery of every
// variation in visuals.js's registry drawing live side by side, the big
// preview (and its full screen), "Use for scope" / "Use for spectrum" with
// the swap flown up into the dock, each card's frame cost counted to its
// value, and the demo signal — a quiet test pattern through the master
// (a kick, an off-beat hat, a chord that breathes), so the page moves with
// nothing loaded. It is a player: the transport hears it (`claim`), so the
// dock's ■ stops it and a ▶ elsewhere takes over from it.
// The cards draw from motion.js's frame through `visuals.each` — one
// analyser read, nothing of their own per frame — and only while on
// screen (an IntersectionObserver). Every tween here is GSAP's and skipped
// under calm motion (colours.js): the card entrances, the hover lift, the
// numbers counting, the swap's flight, and a pop of the lit ▶ buttons on
// each kick (the one thing here that runs on any page).
// The gallery is grouped by family (the template's, from visuals.py); kept
// per browser here: the big preview's pick, and the ♥
// favourites — moved up into their own group, first. Shuffle deals the
// dock a random pair: a scope-ish one and a spectrum-ish one, from the
// favourites once there are two, never what a slot already draws.
// 175 -> 205: the families' entrance, the big pick kept, ♥ and
// Shuffle.
// Budget: <= 205 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const V = S.visuals;
  const calm = () => window.synthlab.calm(); // the Motion setting (colours.js), never the OS's
  const G = () => (!calm() && window.gsap) || null;
  const $ = (id) => document.getElementById(id);
  const TAU = Math.PI * 2;

  // ---- kept per browser: the big preview's pick, the favourites ----------------------
  const BIG = "synthlab.visuals.big", FAV = "synthlab.visuals.fav";
  const kept = (k) => { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch { return null; } };
  const keep = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* not kept: this page still shows it */ } };
  const favs = new Set((Array.isArray(kept(FAV)) ? kept(FAV) : []).filter((id) => V.get(id)));

  // ---- the gallery: each card's canvas drawn from the shared frame -----------------
  let cards = [], big = null, bigId = null, costAt = 0;
  const seen = new WeakSet();
  function fit(c) {
    const w = Math.max(1, Math.round(c.clientWidth * devicePixelRatio)), h = Math.max(1, Math.round(c.clientHeight * devicePixelRatio));
    if (c.width !== w || c.height !== h) Object.assign(c, { width: w, height: h });
  }
  const sizer = new ResizeObserver((es) => { for (const e of es) fit(e.target); V.touch(); });
  const shown = new IntersectionObserver((es) => { for (const e of es) e.isIntersecting ? seen.add(e.target) : seen.delete(e.target); V.touch(); });
  function bind() {
    const now = [...document.querySelectorAll("#view .vis-card")], b = $("vis-big"), had = new Set(cards.map((k) => k.el));
    if (b === big && now.length === had.size && now.every((el) => had.has(el))) return false; // arrange() moves them: the same set
    sizer.disconnect(); shown.disconnect();
    cards = now.map((el) => ({ el, c: el.querySelector("canvas"), id: el.dataset.vis, out: el.querySelector(".vis-cost") }));
    big = b; bigId ??= V.get(kept(BIG)) ? kept(BIG) : V.choice("scope"); costAt = 0;
    for (const c of [...cards.map((k) => k.c), big]) if (c) { sizer.observe(c); shown.observe(c); }
    arrange(); sync();
    return cards.length > 0;
  }
  // a ♥ card goes up into Favourites, the rest to their family, each in the registry's order
  function arrange() {
    const fav = document.querySelector('#view .vis-family[data-family="favourites"]'), at = (k) => V.list.findIndex((v) => v.id === k.id);
    if (!fav) return;
    for (const k of [...cards].sort((a, b) => at(a) - at(b))) {
      const home = favs.has(k.id) ? fav : document.querySelector(`#view .vis-family[data-family="${k.el.dataset.family}"]`);
      home?.querySelector(".vis-grid")?.append(k.el);
    }
    fav.hidden = !cards.some((k) => favs.has(k.id));
  }
  V.each((f, draw) => {
    if (f.isLive && f.onset) pulse();
    if (!cards.length || !big?.isConnected) return;
    for (const k of cards) if (seen.has(k.c)) draw(k.c, k.id, f);
    if (seen.has(big)) draw(big, bigId, f);
    if (!costAt || f.now - costAt > 500) { costAt = f.now; costs(); }
  });
  // what a frame costs, counted to its new value
  function costs() {
    const g = G();
    for (const k of [...cards, { c: big, out: $("vis-big-cost") }]) {
      const ms = V.cost(k.c), out = k.out;
      if (ms == null || !out) continue;
      out.toggleAttribute("data-over", ms > 1);
      const from = parseFloat(out.textContent);
      if (!g || !Number.isFinite(from) || Math.abs(from - ms) < 0.01) { out.textContent = `${ms.toFixed(2)} ms`; continue; }
      const o = { v: from };
      g.to(o, { v: ms, duration: 0.4, ease: "power1.out", overwrite: true, onUpdate: () => { out.textContent = `${o.v.toFixed(2)} ms`; } });
    }
  }
  V.costs = () => Object.fromEntries(cards.map((k) => [k.id, V.cost(k.c)]));
  function sync() {
    for (const k of cards) {
      for (const b of k.el.querySelectorAll("[data-use]")) b.setAttribute("aria-pressed", String(V.choice(b.dataset.use) === k.id));
      k.el.querySelector("[data-big]")?.setAttribute("aria-pressed", String(bigId === k.id));
      k.el.querySelector("[data-fav]")?.setAttribute("aria-pressed", String(favs.has(k.id)));
      k.el.toggleAttribute("data-in-dock", V.choice("scope") === k.id || V.choice("spectrum") === k.id);
    }
    for (const slot of ["scope", "spectrum"]) { const o = $(`vis-in-${slot}`); if (o) o.textContent = V.get(V.choice(slot))?.name || ""; }
    const n = $("vis-big-name"), d = $("vis-demo");
    if (n) n.textContent = V.get(bigId)?.name || "";
    if (d) d.setAttribute("aria-pressed", String(!!demo));
  }
  document.addEventListener("visuals-change", sync);

  // ---- motion: entrances, the lift, the swap's flight, the ▶'s pop ------------------
  function enter() { // a family's head, then its cards, down the page
    const els = document.querySelectorAll("#view .vis-family:not([hidden]) > .vis-fam-head, #view .vis-card");
    G()?.from(els, { y: 18, scale: 0.97, autoAlpha: 0, duration: 0.45, ease: "power3.out", stagger: 0.022, clearProps: "all" });
  }
  function lift(e, y) {
    const card = e.target.closest?.("#view .vis-card");
    if (card && !card.contains(e.relatedTarget)) G()?.to(card, { y, duration: 0.2, ease: "power2.out", overwrite: "auto" });
  }
  document.addEventListener("pointerover", (e) => lift(e, -3));
  document.addEventListener("pointerout", (e) => lift(e, 0));
  // FLIP: a still of the card flies to the slot it now fills, which fades up under it
  function fly(from, slot) {
    const g = G(), to = $(slot === "scope" ? "scope" : "spectrum");
    if (!g || !to || !from?.width) return;
    const a = from.getBoundingClientRect(), b = to.getBoundingClientRect();
    if (!a.width || !b.width) return;
    const ghost = Object.assign(document.createElement("canvas"), { width: from.width, height: from.height, className: "vis-ghost" });
    ghost.getContext("2d").drawImage(from, 0, 0);
    Object.assign(ghost.style, { left: `${a.left}px`, top: `${a.top}px`, width: `${a.width}px`, height: `${a.height}px` });
    document.body.append(ghost);
    g.to(ghost, { x: b.left - a.left, y: b.top - a.top, scaleX: b.width / a.width, scaleY: b.height / a.height, transformOrigin: "0 0",
      duration: 0.6, ease: "power3.inOut", onComplete: () => ghost.remove() });
    g.to(ghost, { autoAlpha: 0, duration: 0.2, delay: 0.45 });
    g.fromTo(to, { autoAlpha: 0.1 }, { autoAlpha: 1, duration: 0.45, delay: 0.42, ease: "power2.out", clearProps: "opacity,visibility" });
  }
  let popAt = 0;
  function pulse() {
    const g = G(), now = performance.now();
    if (!g || now - popAt < 150) return;
    popAt = now;
    const lit = document.querySelectorAll('.btn-play[aria-pressed="true"], #vis-demo[aria-pressed="true"]');
    if (lit.length) g.fromTo(lit, { scale: 1.08 }, { scale: 1, duration: 0.3, ease: "power2.out", overwrite: true, clearProps: "transform" });
  }

  // ---- the demo signal: a 2-bar test pattern, looped, quiet ---------------------------
  let demo = null, cached = null; // {src, gain, start, bpm}; the last buffer made, by bpm
  const spec = { kind: "Demo", label: "demo signal · a test pattern", stop: () => setDemo(false) };
  function pattern(ctx, bpm) {
    if (cached?.bpm === bpm && cached.rate === ctx.sampleRate) return cached.b;
    const sr = ctx.sampleRate, beat = 60 / bpm, n = Math.round(sr * beat * 8), b = ctx.createBuffer(2, n, sr), x = b.getChannelData(0), y = b.getChannelData(1);
    // A minor, F over A: every note above the kick's band (motion.js reads the
    // kick under 150 Hz; a 110 Hz root there held the band full, and the
    // demo's kick never tripped it: `synthlab.kick()` stayed 0)
    const chords = [[220, 261.63, 329.63, 440], [220, 261.63, 349.23, 440]];
    const ph = [0, 0, 0, 0];
    let seed = 7;
    for (let i = 0; i < n; i++) {
      const bt = i / sr / beat, k = (bt % 1) * beat, off = ((bt + 0.5) % 1) * beat, notes = chords[Math.floor(bt / 4) % 2];
      seed = (seed * 16807) % 2147483647;
      // the kick: a sine falling 120 -> 50 Hz in ~60 ms, ~-6 dBFS through the gain, gone well before the next
      const kick = Math.sin(TAU * (50 * k + 70 * 0.03 * (1 - Math.exp(-k / 0.03)))) * Math.exp(-25 * k) * 1.1;
      const hat = ((seed / 2147483647) * 2 - 1) * Math.exp(-45 * off) * 0.16; // the hat, on the off-beat, a little left
      const swell = 0.6 + 0.4 * Math.sin((TAU * (bt % 4)) / 8); // the chord breathes over its bar
      let l = kick + hat, r = kick + hat * 0.6;
      for (let j = 0; j < 4; j++) { // the chord a quarter-cycle apart on the right: wide, for the goniometer
        ph[j] += notes[j] / sr;
        const p = TAU * ph[j], a = (j ? 0.035 : 0.06) * swell;
        l += (Math.sin(p) + 0.4 * Math.sin(2 * p) + 0.2 * Math.sin(3 * p)) * a;
        r += (Math.cos(p) + 0.4 * Math.cos(2 * p) + 0.2 * Math.cos(3 * p)) * a;
      }
      x[i] = l; y[i] = r;
    }
    cached = { bpm, rate: sr, b };
    return b;
  }
  function setDemo(on) {
    if (!on === !demo) return sync();
    if (!on) {
      const d = demo;
      demo = null;
      try { d.src.stop(); } catch { /* already stopped */ }
      d.gain.disconnect(); S.transport?.release("demo", spec); sync();
      return;
    }
    S.ensureCtx?.();
    const ctx = S.getCtx?.(), master = S.getMaster?.();
    if (!ctx || !master) return;
    ctx.resume?.();
    S.transport?.claim("demo", spec); // one thing plays: the loop stops
    const bpm = Math.round(S.tempo?.() || 120), gain = new GainNode(ctx, { gain: 0.45 }); // the kick ~-6 dBFS, the rest well under: plain, never loud
    const src = new AudioBufferSourceNode(ctx, { buffer: pattern(ctx, bpm), loop: true }), start = ctx.currentTime + 0.05;
    src.connect(gain).connect(master); src.start(start);
    demo = { src, gain, start, bpm };
    sync();
  }
  V.demo = setDemo;
  V.demoClock = () => demo && { start: demo.start, bpm: demo.bpm };

  // Shuffle: a random pair, each slot something of its own kind if it can, never what it draws now
  function shuffle() {
    const pool = V.list.filter((v) => favs.size < 2 || favs.has(v.id));
    const pick = (slot, not) => {
      const free = pool.filter((v) => v.id !== not && v.id !== V.choice(slot)), fit = free.filter((v) => v.kind !== (slot === "scope" ? "spectrum" : "scope"));
      const from = fit.length ? fit : free;
      return from[Math.floor(Math.random() * from.length)]?.id;
    };
    const scope = pick("scope"), spectrum = pick("spectrum", scope);
    for (const [slot, id] of [["scope", scope], ["spectrum", spectrum]]) {
      if (!id) continue;
      V.choose(slot, id); fly(document.querySelector(`#view .vis-card[data-vis="${id}"] canvas`), slot);
    }
  }

  // ---- the page's controls -----------------------------------------------------------------
  document.addEventListener("click", (e) => {
    const t = e.target;
    const use = t.closest?.("#view .vis-card [data-use]");
    if (use) {
      const card = use.closest(".vis-card"), slot = use.dataset.use;
      if (V.choice(slot) !== card.dataset.vis) { V.choose(slot, card.dataset.vis); fly(card.querySelector("canvas"), slot); }
      return;
    }
    const pick = t.closest?.("#view .vis-card [data-big], #view .vis-card canvas");
    if (pick) {
      bigId = pick.closest(".vis-card").dataset.vis; keep(BIG, bigId); V.touch(); sync();
      if (big) G()?.fromTo(big, { autoAlpha: 0.2 }, { autoAlpha: 1, duration: 0.35, ease: "power2.out", clearProps: "opacity,visibility" });
      return;
    }
    const heart = t.closest?.("#view .vis-card [data-fav]");
    if (heart) {
      const card = heart.closest(".vis-card"), id = card.dataset.vis;
      favs.has(id) ? favs.delete(id) : favs.add(id);
      keep(FAV, [...favs]); arrange(); sync();
      G()?.from(card, { scale: 0.94, autoAlpha: 0.3, duration: 0.4, ease: "back.out(2)", clearProps: "all" });
      return;
    }
    if (t.closest?.("#vis-shuffle")) shuffle();
    if (t.closest?.("#vis-demo")) setDemo(!demo);
    if (t.closest?.("#vis-full")) {
      const stage = document.querySelector("#view .vis-stage");
      if (document.fullscreenElement) document.exitFullscreen();
      else stage?.requestFullscreen?.().catch(() => S.toast?.("Full screen was refused by the browser.", "error"));
    }
  });
  document.addEventListener("htmx:after:settle", () => { if (bind()) enter(); });
  document.addEventListener("DOMContentLoaded", () => { if (bind()) enter(); });
})();
