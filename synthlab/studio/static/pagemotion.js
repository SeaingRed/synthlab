// synthlab/studio/static/pagemotion.js
// The page transition.
// Moving to another page — the rail, a view_link, Back / Forward — is a
// motion moment, not a cut. The top dock and the keyboard dock, the
// instrument's frame, stand still; the page between them changes:
//   1. the outgoing page LEAVES on the compositor: htmx 4's view transition
//      (the swap's `transition`, set here for a swap into #view that brings
//      another page, so no link has to ask) snapshots it, and style.css 48
//      slides it away against the direction of travel as it fades (its
//      name, `page-down` or `page-up`: further down the rail is "down");
//   2. the incoming page ARRIVES under it, at once — htmx settles a
//      transition's swap inside the transition, so the islands (the
//      workspace's layout, folds, tabs) have laid it out before the first
//      frame: GSAP brings its modules in, a cascade from the direction of
//      travel in the order they stand, each igniting as it lands (a
//      brightness flare, a panel's glow layer lit: `--ignite`), a band of
//      the theme's glow sweeping the page the same way, and the knobs,
//      faders, meters and velocity bars in sight sweeping up to their
//      values.
// ~420 ms in all. Input is never blocked: the transition lets the pointer
// through (style.css 48) and nothing waits on the motion; a press in the
// page lands it at once; a second move mid-sequence lands this one and
// skips its transition, so the next page comes in clean. Calm motion
// (colours.js) and a hidden tab keep a plain swap.
// GSAP tweens plain proxies and onUpdate WRITES the styles: no computed
// style is read per module (gsap.from read each one's transform — a forced
// layout a module); the boxes are read once, before any write.
// Budget: <= 120 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const root = document.documentElement;
  const MAX = 40, SWEEP = 96; // modules in the cascade; values swept up (the rest stand)
  // a value a control shows by an inline custom property: [what, property]
  const VALUES = [['input[type="range"]', "--fill"], [".meter", "--level"], [".vel-bar", "--v"]];
  let next = null, ctx = null, tl = null, moved = [], swept = [], band = null;

  // a swap into #view that brings another page: a transition, and which way
  document.addEventListener("htmx:before:swap", (e) => {
    const view = document.getElementById("view");
    const t = e.detail?.tasks?.find((k) => k.type === "main" && k.target === view);
    const to = t?.fragment?.querySelector?.("#view")?.dataset.view;
    if (!to || to === view.dataset.view) return;
    land();
    if (S.calm?.() || document.hidden || !window.gsap || !document.startViewTransition) return;
    const rail = [...document.querySelectorAll("nav.views a.view")].map((a) => a.getAttribute("href"));
    // every part of the answer swaps inside the transition, its OOB regions too:
    // swapped before it, their settles ran each island's work on the OLD page
    // (Synths' dock filter, the chord rows: 37 ms of style before the capture)
    for (const k of e.detail.tasks) k.swapSpec = { ...k.swapSpec, transition: true };
    next = { down: rail.indexOf(`/view/${to}`) >= rail.indexOf(`/view/${view.dataset.view}`) };
    ctx = e.detail.ctx;
    if (ctx?.title) document.title = ctx.title; // (htmx sets it after the swap's end: the transition's)
  });
  // the transition's names, on the three elements alone and only while it
  // runs (style.css 48): the page's says which way it leaves; the docks'
  // hold the frame still. (An attribute on <html> restyled the whole page
  // each way, 12 ms on Kit; a name on #view at rest is a stacking context.)
  let named = [];
  function name(n) {
    const page = `page-${n ? (n.down ? "down" : "up") : ""}`;
    for (const el of named) el.style.removeProperty("view-transition-name");
    named = !n ? [] : [["view", page], ["topdock", "topdock"], ["keys-dock", "keysdock"]]
      .map(([id, v]) => { const el = id === "topdock" ? document.querySelector(".topdock") : document.getElementById(id); el?.style.setProperty("view-transition-name", v); return el; }).filter(Boolean);
  }
  // htmx awaits a transition's `finished` before the swap's after:swap and
  // the next swap: the page's after:swap work (piano.js's keys, the Synths
  // dock) would wait out the old page's leave, typing on the new page
  // played nothing for ~240 ms. Its swap done is enough: the leave runs on
  // by itself, and a next swap's transition skips it
  const start = document.startViewTransition?.bind(document);
  if (start) document.startViewTransition = (cb) => { const vt = start(cb); return { ready: vt.ready, updateCallbackDone: vt.updateCallbackDone, finished: vt.updateCallbackDone, skipTransition: () => vt.skipTransition() }; };
  // the transition begins (the old page is snapshotted next frame): the
  // names, then the swap and, inside it, the arrival
  document.addEventListener("htmx:before:viewTransition", (e) => {
    const n = next;
    if (!n || e.detail?.ctx !== ctx) return;
    next = ctx = null;
    name(n);
    const swap = e.detail.task;
    e.detail.task = async () => {
      const vt = document.activeViewTransition;
      vt?.ready.catch(() => {}); // (skipped by the next move: no uncaught rejection)
      vt?.finished.finally(() => { if ([null, undefined, vt].includes(document.activeViewTransition)) name(null); }); // (however it ends; not the next one's)
      await swap();
      name(n); // the incoming #view is a new element
      arrive(n, vt);
    };
  });

  // the page's outermost modules (a view with none: its children)
  function modules(v) {
    const m = [...v.querySelectorAll("[data-module]")].filter((el) => !el.parentElement.closest("[data-module]"));
    return m.length ? m : [...v.children].filter((el) => !el.matches("style, script, template"));
  }
  const inSight = (r) => r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
  // a module at p (0 -> 1, eased): risen from the direction of travel, lit as it lands
  function put(el, p, dy, lit) {
    const q = 1 - p, flare = q * Math.min(1, p * 5);
    el.style.opacity = String(Math.min(1, p * 1.6));
    el.style.translate = `${(-12 * q).toFixed(1)}px ${(dy * q).toFixed(1)}px`;
    el.style.scale = String(1 - 0.03 * q);
    el.style.filter = `brightness(${(1 + lit * flare).toFixed(3)})`;
    el.style.setProperty("--ignite", flare.toFixed(3));
  }
  const PROPS = ["opacity", "translate", "scale", "filter", "will-change", "--ignite"];

  function arrive(n, vt) {
    const g = window.gsap, v = document.getElementById("view");
    if (!g || !v) return;
    // read every box first (one layout: the one the transition's capture
    // needs anyway), then write
    const boxes = modules(v).map((el) => ({ el, r: el.getBoundingClientRect() })).filter((b) => inSight(b.r));
    boxes.sort((a, b) => (n.down ? a.r.top - b.r.top : b.r.bottom - a.r.bottom) || a.r.left - b.r.left);
    const cast = boxes.slice(0, MAX), vals = [];
    for (const [sel, prop] of VALUES) {
      for (const el of v.querySelectorAll(sel)) {
        const was = el.style.getPropertyValue(prop), num = parseFloat(was);
        if (vals.length >= SWEEP || !(num > 0) || !inSight(el.getBoundingClientRect())) continue;
        vals.push({ el, prop, was, num, unit: was.trim().replace(/^[-\d.]+/, "") });
      }
    }
    // the flare: a filament by night; by day (the aluminium) a glint, or it washes to white
    const day = root.dataset.theme === "day" || (root.dataset.theme === "system" && matchMedia("(prefers-color-scheme: light)").matches);
    const dy = n.down ? 30 : -30, spread = 0.13, lit = day ? 0.3 : 0.85;
    root.dataset.arriving = "";
    // held at its start until the transition's first frame: the swap's own
    // long task would otherwise eat the first ~150 ms of the cascade
    const t = (tl = g.timeline({ paused: true, onComplete: release }));
    (vt?.ready ?? Promise.resolve()).catch(() => {}).then(() => requestAnimationFrame(() => { if (tl === t) t.play(); }));
    moved = cast.map((b) => b.el);
    cast.forEach(({ el }, i) => {
      const o = { p: 0 }, at = cast.length > 1 ? (spread * i) / (cast.length - 1) : 0;
      el.style.willChange = "opacity, translate, scale, filter";
      put(el, 0, dy, lit);
      tl.to(o, { p: 1, duration: 0.3, ease: "power3.out", onUpdate: () => put(el, o.p, dy, lit) }, at);
    });
    // the values in sight sweep up from nothing, a beat behind their module
    swept = vals;
    for (const s of vals) {
      const o = { x: 0 }, i = Math.max(0, moved.findIndex((m) => m.contains(s.el))), at = 0.05 + (spread * i) / Math.max(1, moved.length - 1);
      s.el.style.setProperty(s.prop, `0${s.unit}`);
      tl.to(o, { x: s.num, duration: 0.3, ease: "power2.out", onUpdate: () => s.el.style.setProperty(s.prop, `${o.x.toFixed(3)}${s.unit}`) }, at);
    }
    // a band of the glow sweeping the page the way you travelled
    if (!band?.isConnected) { band = Object.assign(document.createElement("i"), { className: "pm-sweep" }); band.setAttribute("aria-hidden", "true"); document.body.append(band); }
    const b = { y: n.down ? -45 : 100, o: 0 };
    tl.to(b, { y: n.down ? 100 : -45, duration: 0.42, ease: "power2.inOut", onUpdate: () => { band.style.translate = `0 ${b.y.toFixed(1)}vh`; } }, 0);
    tl.to(b, { o: 1, duration: 0.12, yoyo: true, repeat: 1, repeatDelay: 0.18, ease: "sine.inOut", onUpdate: () => { band.style.opacity = b.o.toFixed(3); } }, 0);
  }

  // the motion over (or cut short): every value back to what the page holds
  function release() {
    for (const el of moved) for (const p of PROPS) el.style.removeProperty(p);
    for (const s of swept) s.el.style.setProperty(s.prop, s.was);
    if (band) band.style.opacity = "0";
    moved = []; swept = []; tl = null;
    delete root.dataset.arriving;
  }
  // land it now: the cascade at its end, the transition skipped
  function land() {
    if (tl) { const t = tl; t.progress(1); t.kill(); release(); }
    document.activeViewTransition?.skipTransition();
  }
  // a press in the page, or a value moved there, is the hand's: the motion lands
  document.addEventListener("pointerdown", (e) => { if (tl && e.target.closest?.("#view")) land(); }, true);
  document.addEventListener("input", (e) => { if (tl && e.target.closest?.("#view")) land(); }, true);
  document.addEventListener("synthlab-motion", () => { if (S.calm?.()) land(); });
  S.pageMotion = { land, busy: () => !!tl || !!document.activeViewTransition }; // (the browser tests' handle)
})();
