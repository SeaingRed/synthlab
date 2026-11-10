// synthlab/studio/static/mixer.js
// The Mix view's island (views/mixer.html, region
// `mixer-strips`). It holds no mix state: every value is a native range
// whose write is tweak.js's and whose post is its own (hx-post on
// `change`), mute and solo are buttons, and the song plays through
// song.js's player (the bar's data-song-action buttons; Space and L).
// What is here:
// - Meters. A strip's `.meter[data-meter=<track or return id>]` is lit
//   from the plan's `levels` ({id: [peak, rms]} a section, what the
//   song's render measured — GET /song/plan.json, the player's own plan
//   while it plays): the section in #mix-section, which follows the
//   playhead while the song plays. So a meter steps at section
//   boundaries; only the master's two (#mix-out-l / -r) are live — its
//   left and right off the master bus (a splitter, two small analysers,
//   read only while this view shows). The plan is fetched
//   when the view appears. After a `song-changed` the meters are STALE
//   (dimmed: `#view[data-mix-stale]`) until a plan of the edited song is
//   in: while the song plays that is the player's own (song.js re-fetches
//   it; this file never asks twice), otherwise this file's, 2 s after the
//   hand rests — a plan renders every section the edit reached, which on
//   a full-length song is seconds of work a knob still turning would
//   throw away.
// - The focused strip: the strip last touched is `[data-focus]`; the
//   bar's "M strip" / "S strip" (commands, so the palette and a Stream
//   Deck key have them) press its M / S.
// - A finger's double tap on a strip's fader or knob is its default (a
//   mouse's double click is audio.js's; a touch screen sends no dblclick).
// - Keys. Every control is a tab stop and its arrows are its own: ← → pan
//   a pan knob as ↑ ↓ do (a strip is not one tab stop with ← → moving
//   between its controls, which would leave the pan knob's arrows unable to pan).
//   Alt+M opens the view from anywhere; Ctrl+Z / Ctrl+Shift+Z are the
//   song's undo and redo.
// 130 -> 145: the master's stereo meter, read off the bus (not a mirror
// of the mono status meter).
// Budget: <= 145 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const $ = (id) => document.getElementById(id);
  const here = () => !!$("mixer-head");
  const FLOOR = -48; // dB at the bottom of a meter (mixer.METER_FLOOR_DB)
  const pct = (x) => (x > 1e-9 ? Math.max(0, Math.min(1, 1 - (20 * Math.log10(x)) / FLOOR)) * 100 : 0);
  let plan = null, shown = "", chosen = "", focus = "", gen = 0, timer = 0;

  // ---- meters ----------------------------------------------------------------
  const power = (s) => Object.values(s.levels || {}).reduce((a, lv) => a + lv[1] * lv[1], 0);
  const stale = (on) => $("view")?.toggleAttribute("data-mix-stale", on);
  function light() {
    // nothing chosen and nothing playing: the fullest section, where every track shows
    if (plan && !chosen && !S.songClock?.().playing) {
      const full = plan.sections.reduce((a, s) => (power(s) > power(a) ? s : a), plan.sections[0]);
      if (full && power(full) > 0 && full.section_id !== shown) return show(full.section_id);
    }
    const sec = plan?.sections.find((s) => s.section_id === shown);
    for (const m of document.querySelectorAll(".mix-board .meter[data-meter]")) {
      const lv = sec?.levels?.[m.dataset.meter];
      m.style.setProperty("--level", `${lv ? pct(lv[1]).toFixed(1) : 0}%`);
      m.style.setProperty("--peak", lv ? `${pct(lv[0]).toFixed(1)}%` : "-10%");
      m.classList.toggle("clipped", !!lv && lv[0] >= 1);
    }
  }
  async function fetchPlan() {
    const my = ++gen;
    try {
      const res = await fetch("/song/plan.json");
      if (res.ok && my === gen && here()) { plan = await res.json(); stale(false); light(); }
    } catch { /* offline: the meters stay as they are */ }
  }
  function show(sid, byHand) {
    if (byHand) chosen = sid;
    if (!sid || sid === shown) return;
    shown = sid;
    const sel = $("mix-section");
    if (sel && sel.value !== sid) sel.value = sid;
    light();
  }
  document.addEventListener("song-changed", () => {
    if (!here()) return;
    clearTimeout(timer); gen++; stale(true);
    if (!S.songClock?.().playing) timer = setTimeout(fetchPlan, 2000); // (playing: the player's plan, in frame())
  });
  document.addEventListener("change", (e) => {
    if (e.target.id !== "mix-section") return;
    show(e.target.value, true);
    S.songClock?.().select(e.target.value); // Loop loops the section shown
  });

  // ---- the focused strip --------------------------------------------------------
  function touch(strip) {
    const id = strip.dataset.strip || strip.dataset.return;
    if (id === focus) return;
    focus = id;
    for (const s of document.querySelectorAll(".mix-strip")) s.toggleAttribute("data-focus", s === strip);
  }
  for (const type of ["focusin", "pointerdown"]) document.addEventListener(type, (e) => {
    const strip = e.target.closest?.(".mix-strip");
    if (strip) touch(strip);
  }, true);
  // a tap is a touch that came up where it went down (tweak.js has the drags)
  let down = null, tapped = null, tapAt = 0;
  document.addEventListener("pointerdown", (e) => { down = e.pointerType === "touch" ? [e.clientX, e.clientY] : null; }, true);
  document.addEventListener("pointerup", (e) => {
    const el = down && e.target.closest?.(".mix-strip input[type=range]");
    if (!el || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) { tapped = null; return; }
    if (tapped === el && e.timeStamp - tapAt < 400) { tapped = null; S.tweak.reset(el); } else { tapped = el; tapAt = e.timeStamp; }
  }, true);
  document.addEventListener("click", (e) => {
    const what = e.target.closest?.("[data-mix-focus]")?.dataset.mixFocus;
    if (!what) return;
    const strip = document.querySelector(".mix-strip[data-focus][data-strip]") || document.querySelector(".mix-strip[data-strip]");
    strip?.querySelector(`[data-mix="${what}"]`)?.click();
  });
  window.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (e.altKey && !mod && e.key.toLowerCase() === "m") { e.preventDefault(); document.querySelector('a.view[href="/view/mixer"]')?.click(); return; }
    if (!here()) return;
    if (mod && !e.altKey && e.key.toLowerCase() === "z" && !S.isTypingTarget?.(e.target)) { e.preventDefault(); $(e.shiftKey ? "mix-redo" : "mix-undo")?.click(); }
  }, true);

  // ---- after a swap, and every frame ---------------------------------------------
  function sync() {
    if (!here()) { plan = null; shown = ""; clearTimeout(timer); return; }
    for (const strip of document.querySelectorAll(".mix-strip")) strip.toggleAttribute("data-focus", (strip.dataset.strip || strip.dataset.return) === focus);
    const sel = $("mix-section"), ids = sel ? [...sel.options].map((o) => o.value) : [];
    const keep = ids.includes(chosen) ? chosen : ids.includes(shown) ? shown : sel?.value || "";
    shown = ""; show(keep);
    if (!plan) fetchPlan(); else light();
  }
  // the master's two sides: a splitter off the master bus, two small
  // analysers, read only while the view shows (#mix-out-l / -r)
  let lr = null;
  function sides(c, m) {
    if (lr?.c === c) return lr;
    const sp = c.createChannelSplitter(2), an = [0, 1].map(() => Object.assign(c.createAnalyser(), { fftSize: 512 }));
    m.connect(sp); an.forEach((a, i) => sp.connect(a, i));
    return (lr = { c, an, buf: new Float32Array(512), pk: [0, 0], held: [0, 0], at: [0, 0], clip: [-1e9, -1e9] });
  }
  function frame(now) {
    requestAnimationFrame(frame);
    const outs = [$("mix-out-l"), $("mix-out-r")], ac = S.getCtx?.(), m = S.getMaster?.();
    if (!outs[0] || !ac || !m) return;
    const s = sides(ac, m);
    s.an.forEach((a, i) => {
      a.getFloatTimeDomainData(s.buf);
      let pk = 0;
      for (const v of s.buf) pk = Math.max(pk, Math.abs(v));
      s.pk[i] = Math.max(pk, s.pk[i] * 0.9);
      if (s.pk[i] >= s.held[i] || now - s.at[i] > 1200) { s.held[i] = s.pk[i]; s.at[i] = now; } // the held tick
      outs[i].style.setProperty("--level", `${Math.min(1, s.pk[i]) * 100}%`);
      outs[i].style.setProperty("--peak", `${Math.min(1, s.held[i]) * 100}%`);
      if (pk >= 0.99) s.clip[i] = now;
      outs[i].classList.toggle("clipped", now - s.clip[i] < 2000); // a clip lamp lit for 2 s
    });
    const c = S.songClock?.();
    if (!c?.playing || !c.plan) return;
    if (c.plan !== plan && !c.plan.head) { plan = c.plan; gen++; clearTimeout(timer); stale(false); light(); } // the player's newest plan
    const t = S.getCtx().currentTime, e = c.live.find((x) => x.at <= t && t < x.at + x.dur - x.offset);
    if (e && e.sid !== shown) show(e.sid);
  }
  let queued = false;
  new MutationObserver((recs) => {
    if (queued || !recs.some((r) => [...r.addedNodes].some((n) => n.id === "view" || n.id === "mixer-strips"))) return;
    queued = true; queueMicrotask(() => { queued = false; sync(); });
  }).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("htmx:after:settle", () => { if (here()) sync(); });
  document.addEventListener("DOMContentLoaded", () => { sync(); requestAnimationFrame(frame); });
})();
