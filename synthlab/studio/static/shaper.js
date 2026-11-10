// synthlab/studio/static/shaper.js
// Free record's shaper (routes/freerec.py, keys-and-midi.md
// "⏺ Rec", loop-and-perform.md "Free record"): a free take — every key from
// its first note to ■, no bars (keyrec.js) — made into a loop, in a
// time-true roll (seconds, not steps), to shape, cut and size as you
// please.
// - Opens on `free-shape` (the free take's answer, dispatched by keyrec.js)
//   or a Loops row's ✂ Shape ([data-free-take]): GET /fragment/free/shaper
//   into base.html's #free-shaper, a manual popover (slots stay clickable).
// - CUT: the ▕ ▏ handles trim the start and end; a click on empty roll
//   puts the cursor (✂ split there, S), a drag there picks a rectangle;
//   a note's click picks it (Shift adds), its drag moves it (time, pitch),
//   its end's drag resizes it; ⌫ delete, ⊡ keep, ⇤⇥ trim to the notes,
//   ↶ (Ctrl+Z), "as played".
// - SIZE: bars 1-4, FIT (what is between the handles stretched or squashed
//   to fill them) or CROP (the timing at the tempo; past the bars cut);
//   the tempo a guess from the playing (`guessTempo`), or the page's.
// - FEEL: the grid, quantise 0-100 % (0 = as played), swing, velocity.
//   The roll draws each note where the loop will play it (`shapedStart`
//   mirrors loops.shaped_start, so the preview is what Make loop places).
// - ▶ hear: POST /take/keys/free/hear, its render looped from the next
//   bar line of what plays (a transport layer). Make loop: POST
//   /take/keys/free/loop — into the selected slot (slots.js S.slotSel) as
//   a take lands — through synthlab.send's answer handling (audio.js).
// NO named field: the fragment's controls are read by `data-shp`.
// Budget: <= 300 code lines (the pure half ~60 — the shape,
// the quantise, the tempo guess —, the roll's drawing ~70, its gestures
// ~80, the controls, ▶ and Make loop ~90).

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  // ---- the pure half (tests/studio/test_free_rec.py runs it under node) -------------
  // where a note `t` steps in is heard: pulled `q`% to its nearest line, the
  // odd steps' lines `swing` of a step late (loops.shaped_start, line for line)
  function shapedStart(t, q, swing = 0) {
    const lo = Math.floor(t);
    let line = null;
    for (const k of [lo - 1, lo, lo + 1, lo + 2]) {
      const x = k + (k % 2 ? swing : 0);
      if (line == null || Math.abs(x - t) < Math.abs(line - t) - 1e-12) line = x;
    }
    return t + (line - t) * Math.max(0, Math.min(100, q)) / 100;
  }
  // seconds of the take <-> beats of the loop: fit maps [a, b) onto the bars,
  // crop keeps the time at the tempo from a
  function mapping(o) {
    const B = o.bars * o.barBeats, perBeat = o.mode === "crop" ? 60 / o.bpm : (o.b - o.a) / B;
    return { B, end: o.a + B * perBeat, beats: (t) => (t - o.a) / perBeat, secs: (x) => o.a + x * perBeat };
  }
  // the take as shaped: [[start, length, midi, velocity]] in beats from the
  // loop's start — the notes between the handles that start inside the loop
  function shape(notes, o) {
    const m = mapping(o), out = [];
    for (const n of notes) {
      if (n.t < o.a - 1e-9 || n.t >= Math.min(o.b, m.end) - 1e-9 || (o.dropArp && n.arp)) continue;
      const s = Math.max(0, m.beats(n.t)), e = Math.min(m.beats(Math.min(n.t + n.d, o.b)), m.B);
      out.push([r4(s), r4(Math.max(0.01, e - s)), n.m, r4(n.v)]);
    }
    return out.sort((x, y) => x[0] - y[0]).slice(0, 400); // limits.MAX_TAKE_NOTES
  }
  const r4 = (x) => Math.round(x * 10000) / 10000;
  // the tempo the playing suggests: its onsets (seconds from the start) on a
  // grid of eighths, the beats weighing more, its length near whole bars,
  // and a pull towards 115 — tried every 0.25 bpm from `lo` to `hi`
  function guessTempo(onsets, span, barBeats, lo = 70, hi = 160) {
    if (onsets.length < 2) return null;
    let best = null;
    for (let bpm = lo; bpm <= hi + 1e-9; bpm += 0.25) {
      const half = 30 / bpm;
      let s = 0;
      for (const t of onsets) { const x = t / half; s += Math.cos(2 * Math.PI * x) * (1 + 0.5 * Math.cos(Math.PI * x)); }
      s /= onsets.length * 1.5;
      const bars = span / (barBeats * 2 * half), near = Math.max(1, Math.round(bars));
      s += 0.2 * Math.max(0, 1 - 2 * Math.abs(bars - near)) * ([1, 2, 4, 8].includes(near) ? 1 : 0.5);
      s -= 0.12 * Math.abs(Math.log2(bpm / 115));
      if (!best || s > best.s) best = { bpm, s };
    }
    return Math.round(best.bpm * 10) / 10;
  }
  // the bars a phrase of `span` seconds fills at `bpm` (1-4, rounded up past a quarter bar)
  const barsFor = (span, bpm, barBeats) => Math.max(1, Math.min(4, Math.ceil(span / (barBeats * 60 / bpm) - 0.25)));
  S.shaper = { shapedStart, mapping, shape, guessTempo, barsFor };
  if (typeof document === "undefined") return;

  const pop = () => document.getElementById("free-shaper");
  let st = null; // the open shaper: { take, data, notes, a, b, bpm, bars, mode, q, swing, steps, sel, cursor, undo, dropArp }
  const $ = (sel) => pop()?.querySelector(sel);
  const ctl = (k) => $(`[data-shp="${k}"]`);
  const out = (k, text) => { const o = $(`[data-shp-out="${k}"]`); if (o && o.textContent !== text) o.textContent = text; };
  const opts = () => ({ a: st.a, b: st.b, bpm: st.bpm, bars: st.bars, barBeats: st.data.bar_beats, mode: st.mode, dropArp: st.dropArp });

  async function open(take) {
    const p = pop();
    if (!p || !take) return;
    stopHear();
    // through htmx (not a bare fetch into innerHTML): the page's headers, a refusal's toast
    await htmx.ajax("GET", `/fragment/free/shaper?take=${encodeURIComponent(take)}`, { target: p, swap: "innerHTML", source: document.getElementById("post-from") }).catch(() => {});
    const box = p.querySelector("[data-shaper]");
    if (!box) return;
    const data = JSON.parse(box.dataset.shaper);
    const notes = data.notes.map(([t, d, m, v, arp]) => ({ t, d, m, v, arp: !!arp }));
    const last = Math.max(...notes.map((n) => n.t)), bb = data.bar_beats;
    const bpm = guessTempo(notes.map((n) => n.t), data.seconds, bb) || data.page_bpm;
    const bars = barsFor(last + 30 / bpm, bpm, bb);
    st = { take, data, notes, a: 0, bpm, bars, mode: "fit", q: 0, swing: 0, steps: data.steps, sel: new Set(), cursor: null, undo: [], dropArp: false };
    st.b = Math.max(last + 0.05, bars * bb * 60 / bpm);
    st.first = snap();
    if (!p.matches(":popover-open")) p.showPopover();
    sync(); draw();
    $("[data-shp-roll]")?.focus({ preventScroll: true });
  }
  function close() { stopHear(); st = null; const p = pop(); if (p?.matches(":popover-open")) p.hidePopover(); }
  const snap = () => JSON.stringify({ notes: st.notes, a: st.a, b: st.b });
  function edit(fn) { st.undo.push(snap()); if (st.undo.length > 60) st.undo.shift(); fn(); draw(); }
  function restore(json) { const o = JSON.parse(json); st.notes = o.notes; st.a = o.a; st.b = o.b; st.sel.clear(); draw(); }

  // ---- the controls ------------------------------------------------------------------
  function sync() {
    const bpm = ctl("bpm");
    if (bpm && document.activeElement !== bpm) bpm.value = String(st.bpm);
    for (const b of pop().querySelectorAll("[data-shp-bars]")) b.setAttribute("aria-pressed", String(+b.dataset.shpBars === st.bars));
    for (const b of pop().querySelectorAll("[data-shp-mode]")) b.setAttribute("aria-pressed", String(b.dataset.shpMode === st.mode));
    $('[data-shp-do="arp"]')?.setAttribute("aria-pressed", String(!st.dropArp));
    out("q", `${st.q}%`); out("swing", `${Math.round(st.swing * 100)}%`);
    const loopSec = st.bars * st.data.bar_beats * 60 / st.bpm;
    out("stretch", st.mode === "fit" ? `×${(loopSec / Math.max(0.01, st.b - st.a)).toFixed(2)}` : "");
    const sel = S.slotSel?.();
    out("into", sel && sel.kind !== "beat" ? `→ lane ${sel.lane + 1}, scene ${sel.row + 1}${sel.filled ? ` (${how()})` : ""}` : "→ the Loops panel");
  }
  const how = () => { try { return localStorage.getItem("synthlab.keyRecHow") === "replace" ? "replace" : "overdub"; } catch { return "overdub"; } };

  // ---- the roll ----------------------------------------------------------------------
  const NS = "http://www.w3.org/2000/svg", RULER = 14, ROW = 9;
  let geo = null; // what the last draw measured: { x(t), t(x), y(m), m(y), w, lo, hi }
  function draw() {
    const roll = $("[data-shp-roll]");
    if (!roll || !st) return;
    const all = st.notes, len = Math.max(st.data.seconds, st.b, ...all.map((n) => n.t + n.d));
    const t0 = -0.15, t1 = len + 0.35, w = Math.max(320, roll.clientWidth);
    let lo = Math.min(...all.map((n) => n.m), 127) - 2, hi = Math.max(...all.map((n) => n.m), 0) + 2;
    if (hi - lo < 12) { lo -= 6 - ((hi - lo) >> 1); hi = lo + 12; }
    const h = RULER + (hi - lo + 1) * ROW;
    geo = { w, lo, hi, x: (t) => ((t - t0) / (t1 - t0)) * w, t: (x) => t0 + (x / w) * (t1 - t0), y: (m) => RULER + (hi - m) * ROW, m: (y) => hi - Math.floor((y - RULER) / ROW) };
    const m = mapping(opts()), step = st.data.bar_beats / st.steps, parts = [];
    const rect = (x, y, rw, rh, cls, extra = "") => parts.push(`<rect x="${x.toFixed(1)}" y="${y}" width="${Math.max(1, rw).toFixed(1)}" height="${rh}" class="${cls}"${extra}/>`);
    for (let p = lo; p <= hi; p++) if ([1, 3, 6, 8, 10].includes(p % 12)) rect(0, geo.y(p), w, ROW, "shp-k");
    const end = Math.min(m.end, st.mode === "fit" ? st.b : m.end);
    rect(geo.x(st.a), 0, geo.x(end) - geo.x(st.a), h, "shp-loop");
    for (let k = 0; k <= m.B / step + 1e-9; k++) { // the loop's grid, mapped onto the take's time
      const x = geo.x(m.secs(k * step)), bar = k % st.steps === 0, beat = (k * step) % 1 < 1e-9;
      rect(x, bar ? 0 : RULER, bar ? 1.5 : 1, h - (bar ? 0 : RULER), bar ? "shp-bar" : beat ? "shp-beat" : "shp-step");
      if (bar && k * step < m.B) parts.push(`<text x="${(x + 3).toFixed(1)}" y="10" class="shp-num">${k / st.steps + 1}</text>`);
    }
    const kept = new Set(shapeIdx());
    all.forEach((n, i) => {
      const s = m.beats(n.t), at = m.secs(shapedStart(s / step, st.q, st.swing) * step), dx = at - n.t;
      const cls = `shp-note${st.sel.has(i) ? " sel" : ""}${n.arp ? " arp" : ""}${kept.has(i) ? "" : " out"}`;
      if (Math.abs(dx) > 1e-4 && kept.has(i)) rect(geo.x(n.t), geo.y(n.m) + 1, geo.x(n.t + n.d) - geo.x(n.t), ROW - 2, "shp-ghost");
      rect(geo.x(n.t + dx), geo.y(n.m) + 1, geo.x(n.t + n.d) - geo.x(n.t), ROW - 2, cls, ` data-i="${i}" style="fill-opacity:${(0.35 + 0.65 * n.v).toFixed(2)}"`);
    });
    rect(0, 0, geo.x(st.a), h, "shp-off"); rect(geo.x(st.b), 0, w - geo.x(st.b), h, "shp-off");
    for (const [k, t] of [["a", st.a], ["b", st.b]]) rect(geo.x(t) - 3, 0, 6, h, "shp-handle", ` data-h="${k}"`);
    if (st.cursor != null) rect(geo.x(st.cursor), 0, 1, h, "shp-cursor");
    roll.innerHTML = `<svg xmlns="${NS}" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${parts.join("")}</svg>`;
    const played = shape(st.notes, opts());
    out("len", `${(st.b - st.a).toFixed(2)} s of ${st.data.seconds.toFixed(2)} s`);
    out("notes", `${played.length} note${played.length === 1 ? "" : "s"} in ${st.bars} bar${st.bars === 1 ? "" : "s"} at ${st.bpm} bpm`);
    sync();
  }
  // which notes the loop keeps (`shape`'s rule), by index
  function shapeIdx() {
    const m = mapping(opts()), stop = Math.min(st.b, m.end);
    return st.notes.flatMap((n, i) => (n.t >= st.a - 1e-9 && n.t < stop - 1e-9 && !(st.dropArp && n.arp) ? [i] : []));
  }

  // ---- gestures ------------------------------------------------------------------------
  let drag = null;
  function down(e) {
    if (!st || !geo || e.button !== 0) return;
    const svg = e.target.closest("svg"), box = svg?.getBoundingClientRect();
    if (!box) return;
    const x = e.clientX - box.left, y = e.clientY - box.top, t = geo.t(x), hit = e.target.dataset;
    st.undo.push(snap());
    if (hit.h) drag = { kind: hit.h, x };
    else if (hit.i != null) {
      const i = +hit.i, n = st.notes[i], r = e.target.getBoundingClientRect();
      if (e.shiftKey) st.sel.has(i) ? st.sel.delete(i) : st.sel.add(i); else if (!st.sel.has(i)) st.sel = new Set([i]);
      ctl("vel").value = String(n.v);
      drag = { kind: e.clientX > r.right - 5 ? "end" : "move", x, y, t, from: [...st.sel].map((k) => ({ ...st.notes[k], k })) };
    } else drag = { kind: "box", x, y, t, add: e.shiftKey };
    svg.setPointerCapture(e.pointerId);
    e.preventDefault();
    draw();
  }
  function move(e) {
    if (!drag || !geo) return;
    const box = $("[data-shp-roll] svg")?.getBoundingClientRect();
    if (!box) return;
    const x = e.clientX - box.left, y = e.clientY - box.top, t = Math.max(0, geo.t(x)), len = st.data.seconds + 30;
    if (drag.kind === "a") st.a = Math.max(0, Math.min(t, st.b - 0.05));
    else if (drag.kind === "b") st.b = Math.min(len, Math.max(t, st.a + 0.05));
    else if (drag.kind === "move") {
      const dt = t - drag.t, dm = Math.round((drag.y - y) / ROW);
      for (const f of drag.from) Object.assign(st.notes[f.k], { t: Math.max(0, f.t + dt), m: Math.max(0, Math.min(127, f.m + dm)) });
    } else if (drag.kind === "end") for (const f of drag.from) st.notes[f.k].d = Math.max(0.02, f.d + t - drag.t);
    else drag.to = { x, y };
    draw();
  }
  function up() {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (d.kind === "box") {
      st.undo.pop(); // a pick is no edit
      if (!d.to || Math.abs(d.to.x - d.x) < 4) { st.cursor = d.t; if (!d.add) st.sel.clear(); return draw(); }
      const [ta, tb] = [geo.t(Math.min(d.x, d.to.x)), geo.t(Math.max(d.x, d.to.x))], [ma, mb] = [geo.m(Math.max(d.y, d.to.y)), geo.m(Math.min(d.y, d.to.y))];
      if (!d.add) st.sel.clear();
      st.notes.forEach((n, i) => { if (n.t < tb && n.t + n.d > ta && n.m >= ma && n.m <= mb) st.sel.add(i); });
    } else if (st.undo[st.undo.length - 1] === snap()) st.undo.pop(); // a click on a note: nothing moved
    draw();
  }

  // ---- the actions ---------------------------------------------------------------------
  function act(what) {
    if (!st) return;
    const sel = st.sel;
    if (what === "undo") { const s = st.undo.pop(); return s && restore(s); }
    if (what === "reset") return edit(() => { Object.assign(st, JSON.parse(st.first)); sel.clear(); });
    if (what === "guess") {
      const on = st.notes.filter((n) => n.t >= st.a && n.t < st.b).map((n) => n.t - st.a);
      st.bpm = guessTempo(on, st.b - st.a, st.data.bar_beats) || st.bpm;
      return draw();
    }
    if (what === "page") { st.bpm = st.data.page_bpm; return draw(); }
    if (what === "arp") { st.dropArp = !st.dropArp; return draw(); }
    if (what === "split" && st.cursor != null) {
      const c = st.cursor;
      return edit(() => { for (const n of [...st.notes]) if (n.t < c - 0.01 && n.t + n.d > c + 0.01) { st.notes.push({ ...n, t: c, d: n.t + n.d - c }); n.d = c - n.t; } });
    }
    if (what === "trim" && st.notes.length) return edit(() => { st.a = Math.min(...st.notes.map((n) => n.t)); st.b = Math.max(...st.notes.map((n) => n.t + n.d)); });
    if (!sel.size) return S.toast?.("Pick some notes first: click one, or drag a box round them.");
    if (what === "delete") edit(() => { st.notes = st.notes.filter((_n, i) => !sel.has(i)); sel.clear(); });
    if (what === "keep") edit(() => {
      st.notes = st.notes.filter((_n, i) => sel.has(i)); sel.clear();
      st.a = Math.min(...st.notes.map((n) => n.t)); st.b = Math.max(st.a + 0.05, ...st.notes.map((n) => n.t + n.d));
    });
  }
  function fields() {
    const f = { take: st.take, played: JSON.stringify(shape(st.notes, opts())), bpm: String(st.bpm), bars: String(st.bars), steps: String(st.steps), strength: String(st.q), swing: String(st.swing) };
    const sel = S.slotSel?.();
    if (sel && sel.kind !== "beat") Object.assign(f, { lane: String(sel.lane), row: String(sel.row), how: sel.filled ? how() : "new", bar: "0" });
    const form = new FormData();
    for (const [k, v] of Object.entries(f)) form.append(k, v);
    return form;
  }
  let hearing = null; // { src, spec }
  function stopHear() {
    const h = hearing;
    hearing = null;
    if (h) { try { h.src.stop(); } catch { /* ended */ } S.transport?.release("shaper", h.spec); }
    $("[data-shp-hear]")?.setAttribute("aria-pressed", "false");
  }
  async function hear() {
    if (hearing) return stopHear();
    S.ensureCtx();
    const c = S.getCtx(), btn = $("[data-shp-hear]");
    c.resume?.();
    btn?.setAttribute("aria-busy", "true");
    try {
      const res = await S.send("/take/keys/free/hear", fields(), { refused: "That did not play." });
      if (!res) return;
      const buf = await S.fetchBuffer(res.headers.get("X-Preview-Url")), src = c.createBufferSource();
      src.buffer = buf; src.loop = true; src.connect(S.getMaster());
      const at = (S.isPlaying() && S.nextBar(c.currentTime, 0.05)) || c.currentTime + 0.02;
      src.start(at);
      const spec = { kind: "Shaper", layer: true, label: "the take as shaped, looped", stop: stopHear, at, beat: 60 / st.bpm,
        pos: () => ({ text: "", frac: ((c.currentTime - at) % buf.duration) / buf.duration }) };
      hearing = { src, spec };
      S.transport?.claim("shaper", spec);
      btn?.setAttribute("aria-pressed", "true");
    } catch (err) { S.toast?.(`That did not play: ${err?.message || err}`, "error"); } finally { btn?.removeAttribute("aria-busy"); }
  }
  async function make() {
    stopHear();
    const res = await S.send("/take/keys/free/loop", fields(), { refused: "The take was refused.", done: (h) => `Made the loop “${decodeURIComponent(h.get("X-Loop-Name") || "")}”.` });
    if (res) close();
  }

  document.addEventListener("free-shape", (e) => void open(e.detail?.take));
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (!(t instanceof Element)) return;
    const take = t.closest("[data-free-take]");
    if (take) return void open(take.dataset.freeTake);
    if (!st || !t.closest("#free-shaper")) return;
    let b;
    if (t.closest("[data-shp-close]")) return close();
    if ((b = t.closest("[data-shp-do]"))) return act(b.dataset.shpDo);
    if ((b = t.closest("[data-shp-bars]"))) { st.bars = +b.dataset.shpBars; return draw(); }
    if ((b = t.closest("[data-shp-mode]"))) { st.mode = b.dataset.shpMode; return draw(); }
    if (t.closest("[data-shp-hear]")) return void hear();
    if (t.closest("[data-shp-make]")) return void make();
  });
  document.addEventListener("input", (e) => {
    const k = e.target?.dataset?.shp;
    if (!st || !k || !e.target.closest("#free-shaper")) return;
    const v = Number(e.target.value);
    if (k === "bpm" && v >= st.data.bpm_range[0] && v <= st.data.bpm_range[1]) st.bpm = v;
    else if (k === "q") st.q = v;
    else if (k === "swing") st.swing = v;
    else if (k === "steps") st.steps = v;
    else if (k === "vel") { const pick = st.sel.size ? [...st.sel] : st.notes.map((_n, i) => i); for (const i of pick) st.notes[i].v = v; }
    draw();
  });
  document.addEventListener("change", (e) => { if (st && e.target?.dataset?.shp === "steps") { st.steps = Number(e.target.value); draw(); } });
  document.addEventListener("pointerdown", (e) => { if (e.target instanceof Element && e.target.closest("#free-shaper [data-shp-roll] svg")) down(e); });
  document.addEventListener("pointermove", move);
  document.addEventListener("pointerup", up);
  document.addEventListener("keydown", (e) => {
    if (!st || !e.target?.closest?.("#free-shaper") || S.isTypingTarget?.(e.target)) return;
    const k = e.key, mod = e.ctrlKey || e.metaKey;
    const run = { Delete: "delete", Backspace: "delete", s: "split", k: "keep", t: "trim" }[k];
    if (k === "Escape") close();
    else if (mod && k.toLowerCase() === "z") act("undo");
    else if (mod && k.toLowerCase() === "a") { st.notes.forEach((_n, i) => st.sel.add(i)); draw(); }
    else if (run && !mod) act(run);
    else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(k) && st.sel.size) {
      const dt = { ArrowLeft: -0.01, ArrowRight: 0.01 }[k] || 0, dm = { ArrowUp: 1, ArrowDown: -1 }[k] || 0;
      edit(() => { for (const i of st.sel) { const n = st.notes[i]; n.t = Math.max(0, n.t + dt * (e.shiftKey ? 10 : 1)); n.m = Math.max(0, Math.min(127, n.m + dm)); } });
    } else return;
    e.preventDefault(); e.stopPropagation();
  }, true);
  document.addEventListener("slot-selected", () => st && sync());
  for (const ev of ["resize", "synthlab-geometry"]) window.addEventListener(ev, () => st && draw());
})();
