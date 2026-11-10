// synthlab/studio/static/jam.js
// The jam (song.md, "The jam"): while
// "Jam" is on (#jam-toggle, Alt+J) the launcher of fragments/jam_bar.html
// stands in the playlist's place. A scene is a section, a pad a cell.
// - Playback: one looping AudioBufferSourceNode a sounding track, its buffer
//   the cell loop the drawer's solo already renders
//   (GET /song/cell/{section}/{track}.wav: that track alone, at its level in
//   the mix, no master chain), through the track's own gain and analyser
//   (a real meter on the row) into audio.js's master bus. No second render
//   path and no plan: a bar is 240 / bpm seconds, a loop its buffer.
// - Launching: a pad queues its cell, a scene head every track's cell of
//   that section (a track with none there stops), a row's stop its track —
//   each for the next quantise point: the next bar, the next fourth bar, or
//   where the sounding loop comes round. One GROUP a press: its members
//   start on the same sample, and a group whose buffers are not decoded by
//   its point moves to the following one (never late, never off the grid).
//   From silence there is no grid yet: the group starts when it is decoded.
// - The log: every group that fired, as {bar, track, target} / {bar, scene}
//   / {bar} (stop all), bars counted from the first launch; the clock stands
//   still while nothing sounds. "Capture" posts it to /song/jam/commit
//   (studio/jam.py writes the sections); `jam-captured` in the answer drops
//   it and leaves Jam, a refusal keeps it.
// - jamSchedule is the pure half (point / defer / barAt / beats), run under
//   node by tests/studio/test_jam_schedule.py. With no `document` nothing
//   else is wired.
// - Keys (the grid is one tab stop with a roving focus): arrows move, Enter
//   or Space launch (a button's own click), Shift+Enter the pad's scene,
//   Backspace / Delete stop its track, Escape stops all; Alt+1..9 launch a
//   scene, Alt+0 stops all, Alt+C captures — from anywhere on the page.
// Preferences (on / off, the quantise) are localStorage; no server state.
//
// It loads after songsel.js, the Song island's eighth file.
//
// - The transport: a launch takes over (`transport.take`), a
//   sounding jam is a claim and a waiting launch a wait in the dock's
//   read-out — the queued pads count down there and on themselves.
//
// Budget: <= 250 code lines (tests/studio/test_island.py).
(() => {
  "use strict";
  window.synthlab ??= {};
  const S = window.synthlab;

  // ---- the schedule (pure) -------------------------------------------------
  // c: {t0, bar, q, ref} — the ctx time of the jam's bar 0 (null while
  // nothing sounds), seconds a bar, the quantise (1 | 4 | "end") and, for
  // "end", the loop it waits for ({at, dur}; none: the bar)
  const EPS = 1e-6;
  const grid = (c) => (c.q === "end" ? (c.ref ? [c.ref.at, c.ref.dur] : [c.t0, c.bar]) : [c.t0, c.bar * c.q]);
  // the first quantise point at or after `now`; from silence, now
  function point(now, c) {
    if (c.t0 == null) return now;
    const [from, step] = grid(c);
    return from + Math.max(0, Math.ceil((now - from) / step - EPS)) * step;
  }
  // a launch whose buffer missed its point: the following one
  const defer = (at, c) => (c.t0 == null ? at : at + grid(c)[1]);
  const barAt = (at, c) => Math.round((at - c.t0) / c.bar);
  const beats = (now, at, c) => Math.max(0, Math.ceil(((at - now) / c.bar) * 4 - EPS));
  S.jamSchedule = { point, defer, barAt, beats };
  if (typeof document === "undefined") return;

  // ---- the launcher ----------------------------------------------------------
  // tracks: tid -> {sid, src, g, at, dur} (sounding, or starting within LOOK);
  // queue: tid -> {sid | null, grp}; grp: {at, scene, size} — `scene` is the
  // section a whole-column press named (null: stop all, undefined: pads)
  const LEAD = 0.06, LOOK = 0.12, TICK = 25, FADE = 0.006, STORE = "synthlab.jam", KEEP = 64, SOLO = '[data-cell-solo][aria-pressed="true"]';
  let on = false, t0 = null, held = 0, timer = 0, raf = 0, log = [], bpm = 0, cur = "", inGrid = false, chain = Promise.resolve();
  const tracks = new Map(), queue = new Map(), strips = new Map(), bufs = new Map(), ready = new Map();
  const $ = (id) => document.getElementById(id), el = () => $("jam"), cx = () => S.getCtx();
  const fail = (err) => S.toast?.(String(err?.message || err), "error");
  const bar = () => 240 / bpm, url = (sid, tid) => `/song/cell/${encodeURIComponent(sid)}/${encodeURIComponent(tid)}.wav`;
  const conf = (tid) => ({ t0, bar: bar(), q: $("jam-q")?.value === "end" ? "end" : +$("jam-q")?.value || 1,
    ref: tracks.get(tid) || [...tracks.values()].sort((a, b) => b.dur - a.dur)[0] });
  const pref = () => { try { return JSON.parse(localStorage.getItem(STORE)) || {}; } catch { return {}; } };
  const save = () => { try { localStorage.setItem(STORE, JSON.stringify({ on, q: $("jam-q")?.value || pref().q })); } catch { /* blocked */ } };

  function load(u) {
    if (bufs.size >= KEEP && !bufs.has(u)) { const old = bufs.keys().next().value; bufs.delete(old); ready.delete(old); }
    return bufs.get(u) || bufs.set(u, S.fetchBuffer(u).then((b) => { ready.set(u, b); return b; }, (err) => { bufs.delete(u); throw err; })).get(u);
  }
  // decode ahead, one at a time: the columns from `i` on (a launch jumps the line)
  function prefetch(i, n = 2) {
    const heads = [...document.querySelectorAll("#jam .jam-scene")].slice(Math.max(0, i), Math.max(0, i) + n);
    for (const h of heads) for (const p of document.querySelectorAll(`#jam .jam-pad[data-section-id="${CSS.escape(h.dataset.sectionId)}"]`)) {
      const u = url(p.dataset.sectionId, p.dataset.trackId);
      chain = chain.then(() => on && load(u)).catch(() => {});
    }
  }
  function strip(tid) {
    if (strips.has(tid)) return strips.get(tid);
    const inp = cx().createGain(), an = cx().createAnalyser();
    an.fftSize = 512; inp.connect(an); inp.connect(S.getMaster());
    return strips.set(tid, { inp, an, buf: new Float32Array(512) }).get(tid);
  }
  function drop(tid, at, fade = FADE) { // the track's loop ends at `at`
    const t = tracks.get(tid); if (!t) return;
    t.g.gain.setValueAtTime(1, Math.max(cx().currentTime, at - fade));
    t.g.gain.linearRampToValueAtTime(0, Math.max(cx().currentTime + fade, at));
    try { t.src.stop(Math.max(cx().currentTime + fade, at) + 0.01); } catch { /* never started */ }
    tracks.delete(tid);
  }
  function fire(tid, sid, at) {
    drop(tid, at);
    if (!sid) return;
    const src = cx().createBufferSource(), g = cx().createGain();
    src.buffer = ready.get(url(sid, tid)); src.loop = true;
    src.connect(g).connect(strip(tid).inp); src.start(at);
    tracks.set(tid, { sid, src, g, at, dur: src.buffer.duration });
  }
  function note(ev) { // one line of the log; past the cap it says so once
    const max = +el()?.dataset.maxEvents || 512;
    if (log.length < max) log.push(ev);
    else if (log.length === max) { log.push(null); S.toast?.(`The jam's log is full (${max} launches): capture it, or forget it.`); }
  }
  const events = () => log.filter(Boolean);
  // the log's length in bars: the bar in progress counts whole
  const bars = () => (t0 == null ? held : Math.max(0, Math.ceil((cx().currentTime - t0) / bar() - EPS)));

  function pump() {
    const now = cx().currentTime;
    if (S.songClock?.().playing || document.querySelector(SOLO)) return stopAll(); // the song, or the drawer's solo, was started: it wins
    for (const grp of new Set([...queue.values()].map((x) => x.grp))) {
      const mine = [...queue].filter(([, x]) => x.grp === grp);
      const okay = mine.every(([tid, x]) => !x.sid || ready.has(url(x.sid, tid)));
      if (t0 == null) grp.at = now + LEAD; // from silence: as soon as it is decoded
      if (grp.at - now > LOOK) continue;
      if (!okay) { if (t0 != null) grp.at = defer(grp.at, conf(mine[0][0])); continue; }
      if (t0 == null) t0 = grp.at - held * bar();
      const at = barAt(grp.at, { t0, bar: bar() });
      for (const [tid, x] of mine) { queue.delete(tid); fire(tid, x.sid, grp.at); }
      if (grp.scene !== undefined && mine.length === grp.size) note(grp.scene ? { bar: at, scene: grp.scene } : { bar: at });
      else for (const [tid, x] of mine) note(x.sid ? { bar: at, track: tid, target: x.sid } : { bar: at, track: tid });
      if (!tracks.size) { held = at; t0 = null; } // the last track stopped: the clock stands
    }
    if (tracks.size) S.transport?.claim("jam", JAM); else S.transport?.release("jam"); // the dock's read-out
    if (!queue.size && !tracks.size) { clearInterval(timer); timer = 0; }
    paint();
  }
  // fetch a queued cell; one that cannot be had (an emptied cell) leaves the queue
  function want(tid) {
    const x = queue.get(tid);
    if (x?.sid) load(url(x.sid, tid)).catch((err) => { if (queue.get(tid) === x) queue.delete(tid); fail(err); });
  }
  // queue [[track id, section id | null], ...] as one group
  function launch(list, scene) {
    if (!el() || !list.length || (t0 == null && !queue.size && !list.some(([, sid]) => sid))) return; // a stop in silence
    S.ensureCtx(); cx().resume();
    S.transport?.take("jam", "Jam"); // the song, the loop preview, the drawer's solo: one thing plays
    const waiting = t0 == null && [...queue.values()][0]?.grp; // from silence: one group, so one grid
    const grp = waiting || { at: point(cx().currentTime + LEAD, conf(list[0][0])), scene, size: list.length };
    if (waiting) grp.scene = undefined;
    for (const [tid, sid] of list) { queue.set(tid, { sid, grp }); want(tid); }
    timer ||= setInterval(pump, TICK);
    raf ||= requestAnimationFrame(frame);
    pump();
  }
  const rowIds = () => [...document.querySelectorAll("#jam .jam-row")].map((r) => r.dataset.trackId);
  function scene(sid) {
    launch(rowIds().map((tid) => [tid, $(`jam-pad-${sid}-${tid}`) ? sid : null]), sid);
    prefetch([...document.querySelectorAll("#jam .jam-scene")].findIndex((h) => h.dataset.sectionId === sid) + 1, 1);
  }
  // now, not on the grid: `say` logs it (the bar in progress is kept whole)
  function stopAll(say) {
    if (t0 != null) { held = bars(); if (say) note({ bar: held }); }
    for (const tid of [...tracks.keys()]) drop(tid, cx().currentTime + 0.03, 0.02);
    queue.clear(); t0 = null;
    clearInterval(timer); timer = 0;
    S.transport?.release("jam");
    paint();
  }
  // what the dock's read-out (transport.js) says of the jam: a claim while a
  // track sounds, a wait (with its pads) while a launch waits for its point
  const scn = (sid) => $(`jam-scene-${sid}`)?.querySelector(".jam-scene-name")?.textContent.replace("▶ ", "").trim() || "";
  const JAM = { kind: "Jam", stop: () => stopAll(true), label: () => [...new Set([...tracks.values()].map((t) => scn(t.sid)))].join(" + "),
    pos: () => { const b = t0 == null ? -1 : Math.max(0, (cx().currentTime - t0) / bar()); // bars into the jam: the line is the bar
      return b >= 0 && { text: `bar ${Math.floor(b + EPS) + 1}`, frac: b % 1, beat: (b * 4) % 1 }; } };
  const forget = () => { stopAll(); log = []; held = 0; paint(); };
  function capture() {
    if (!events().length) return S.toast?.("Nothing to capture yet: press a pad first.", "error");
    S.post("/song/jam/commit", { events: JSON.stringify(events()), bars: String(bars()) });
  }
  document.addEventListener("jam-captured", () => { forget(); setOn(false); });
  // what a test, the deck or another island may read (never write)
  S.jam = { on: () => on, events, bars, t0: () => t0, bar, sounding: () => [...tracks].map(([tid, t]) => ({ tid, sid: t.sid, at: t.at, dur: t.dur })) };

  // ---- what the page shows ---------------------------------------------------
  function paint() {
    const root = el(); if (!root) return;
    for (const p of root.querySelectorAll(".jam-pad")) {
      const t = tracks.get(p.dataset.trackId), q = queue.get(p.dataset.trackId), live = t?.sid === p.dataset.sectionId;
      p.setAttribute("aria-pressed", String(live));
      p.classList.toggle("queued", q?.sid === p.dataset.sectionId);
      p.classList.toggle("leaving", live && !!q);
      if (!live) p.style.removeProperty("--p");
    }
    for (const h of root.querySelectorAll(".jam-scene")) {
      const sid = h.dataset.sectionId, pads = [...root.querySelectorAll(`.jam-pad[data-section-id="${CSS.escape(sid)}"]`)];
      h.setAttribute("aria-pressed", String(pads.length > 0 && tracks.size === pads.length && pads.every((p) => tracks.get(p.dataset.trackId)?.sid === sid)));
      h.classList.toggle("queued", [...queue.values()].some((x) => x.grp.scene === sid));
    }
    for (const b of root.querySelectorAll(".jam-off")) b.classList.toggle("queued", queue.get(b.dataset.trackId)?.sid === null);
    const n = events().length;
    for (const id of ["jam-capture", "jam-forget"]) $(id)?.setAttribute("aria-disabled", String(!n));
    if ($("jam-log")) $("jam-log").textContent = n ? `${bars()} bar${bars() === 1 ? "" : "s"} · ${n} launch${n === 1 ? "" : "es"}` : "";
  }
  function frame() {
    raf = 0;
    const root = el(), c = cx();
    if (!root || !c || !(on || tracks.size)) return;
    raf = requestAnimationFrame(frame);
    const now = S.heardTime(), next = [...queue.values()].sort((a, b) => a.grp.at - b.grp.at)[0];
    for (const [tid, s] of strips) { // the rows' meters: each track's own peak
      s.an.getFloatTimeDomainData(s.buf);
      let pk = 0; for (const v of s.buf) pk = Math.max(pk, Math.abs(v));
      $(`jam-meter-${tid}`)?.style.setProperty("--level", `${Math.min(100, pk * 100).toFixed(0)}%`);
    }
    for (const [tid, t] of tracks) $(`jam-pad-${t.sid}-${tid}`)?.style.setProperty("--p", (now < t.at ? 0 : ((now - t.at) % t.dur) / t.dur).toFixed(3));
    const beat = t0 == null || now < t0 ? -1 : Math.floor((((now - t0) / bar()) % 1) * 4);
    root.querySelectorAll(".jam-clock .led").forEach((led, i) => { if (i === beat) led.dataset.on = next ? "armed" : ""; else delete led.dataset.on; });
    const name = next && (next.grp.scene ? $(`jam-scene-${next.grp.scene}`)?.querySelector(".jam-scene-name")?.textContent.replace("▶ ", "")
      : next.sid ? $(`jam-pad-${next.sid}-${[...queue].find(([, x]) => x === next)[0]}`)?.textContent.trim() : "stop");
    const n = next && t0 != null ? beats(c.currentTime, next.grp.at, { bar: bar() }) : 0;
    S.transport?.queue("jam", next && { label: name || "stop", at: t0 == null ? null : next.grp.at, beat: bar() / 4, sel: "#jam .queued", cancel: () => stopAll() });
    const says = next ? (t0 == null ? `loading ${name}…` : `next: ${name} in ${n} beat${n === 1 ? "" : "s"}`)
      : tracks.size && t0 != null ? `bar ${Math.max(1, Math.floor((now - t0) / bar() + EPS) + 1)}` : events().length ? "stopped · capture it, or play on" : "Press a pad: it plays.";
    if ($("jam-says") && $("jam-says").textContent !== says) $("jam-says").textContent = says;
  }

  function setOn(v) {
    on = !!v && !!$("jam-toggle") && !$("jam-toggle").disabled; save();
    if (!on) { stopAll(); if (inGrid) $("jam-toggle")?.focus({ preventScroll: true }); } // the grid is put away: the keyboard goes to its switch
    sync();
    if (on && el()) { if (S.songClock?.().playing) S.songClock().stop(); S.ensureCtx(); prefetch(0); $(cur)?.focus({ preventScroll: true }); }
  }
  // after any swap: the mode, the cursor and what sounds, laid over new markup
  function sync() {
    const btn = $("jam-toggle"), root = el();
    if (!btn) { if (tracks.size || queue.size) stopAll(); return; } // another page
    document.getElementById("view")?.classList.toggle("jam-on", on);
    btn.setAttribute("aria-pressed", String(on));
    if (!root) return;
    if (bpm && +root.dataset.bpm !== bpm) forget(); // another tempo: the log's bars are not these bars
    bpm = +root.dataset.bpm || 120;
    if ($("jam-q") && pref().q) $("jam-q").value = pref().q;
    const at = (cur && $(cur)) || root.querySelector(".jam-pad, .jam-scene");
    root.querySelectorAll(".jam-grid button").forEach((b) => { b.tabIndex = b === at ? 0 : -1; });
    cur = at?.id || "";
    if (on && inGrid && document.activeElement === document.body) at?.focus({ preventScroll: true });
    paint();
    if (on) raf ||= requestAnimationFrame(frame);
  }
  // an edit: the same URLs, other audio (what sounds plays on until it is launched again)
  document.addEventListener("song-changed", () => { bufs.clear(); ready.clear(); for (const tid of queue.keys()) want(tid); });

  // ---- the hand --------------------------------------------------------------
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-jam]"); if (!b || b.getAttribute("aria-disabled") === "true") return;
    const a = b.dataset.jam, sid = b.dataset.sectionId, tid = b.dataset.trackId;
    if (a === "toggle") setOn(!on);
    else if (a === "pad") launch([[tid, sid]]);
    else if (a === "scene") scene(sid);
    else if (a === "off") launch([[tid, null]]);
    else if (a === "stop") stopAll(true);
    else if (a === "capture") capture();
    else if (a === "forget") forget();
  });
  document.addEventListener("change", (e) => { if (e.target.id === "jam-q") save(); });
  document.addEventListener("focusin", (e) => {
    const b = e.target.closest?.(".jam-grid button");
    inGrid = !!b; if (!b) return;
    if (cur && $(cur)) $(cur).tabIndex = -1;
    b.tabIndex = 0; cur = b.id;
  });
  const MOVES = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0], Home: [0, -99], End: [0, 99] };
  function move(b, dr, dc) { // to the next button that way: an empty cell is stepped over
    const m = [...b.closest("table").rows].map((tr) => [...tr.cells].map((td) => td.querySelector("button")));
    let r = m.findIndex((row) => row.includes(b)), c = m[r].indexOf(b);
    if (Math.abs(dc) > 1) return void (dc < 0 ? m[r].find(Boolean) : m[r].findLast(Boolean))?.focus();
    for (r += dr, c += dc; m[r] && c >= 0 && c < m[r].length; r += dr, c += dc) if (m[r][c]) return void m[r][c].focus();
  }
  window.addEventListener("keydown", (e) => {
    if (!$("jam-toggle") || e.ctrlKey || e.metaKey || e.repeat) return;
    const take = () => { e.preventDefault(); e.stopPropagation(); };
    if (e.altKey) { // from anywhere on the page but a field
      if (S.isTypingTarget?.(e.target)) return;
      const d = !e.shiftKey && !e.target.closest?.(".song-drawer") && /^Digit(\d)$/.exec(e.code)?.[1]; // in the drawer's sequencer Alt+1..4 are steps.js's
      if (e.code === "KeyJ") { take(); $("jam-toggle").click(); }
      else if (!on || !el()) return;
      else if (d === "0") { take(); stopAll(true); }
      else if (d) { take(); document.querySelectorAll("#jam .jam-scene")[d - 1]?.click(); }
      else if (e.code === "KeyC") { take(); capture(); }
      return;
    }
    const b = on && e.target.closest?.(".jam-grid button"), sid = b?.dataset?.sectionId, tid = b?.dataset?.trackId;
    if (!b) return;
    if (MOVES[e.key]) { take(); move(b, ...MOVES[e.key]); }
    else if (e.key === "Enter" && e.shiftKey && sid) { take(); scene(sid); }
    else if ((e.key === "Backspace" || e.key === "Delete") && tid) { take(); launch([[tid, null]]); }
    else if (e.key === "Escape" && $("song-drawer")?.hidden !== false) { take(); stopAll(true); }
  }, true); // capture: before piano.js hears Alt+J as its B

  const REGIONS = new Set(["view", "song-topbar", "song-arranger"]);
  let queued = false;
  new MutationObserver((recs) => {
    if (queued || !recs.some((r) => [...r.addedNodes].some((n) => REGIONS.has(n.id)))) return;
    queued = true; queueMicrotask(() => { queued = false; sync(); });
  }).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("htmx:after:settle", sync); // a morph (the topbar) adds no node: the switch's state again
  document.addEventListener("DOMContentLoaded", () => { on = !!pref().on; sync(); });
})();
