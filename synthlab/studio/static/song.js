// synthlab/studio/static/song.js
// The Song page's island: the player, its playhead, and
// the playlist's keyboard (drags onto a lane are songdrag.js's, the time
// axis songline.js's). The ninth island, loaded after
// audio.js, keys.js and piano.js; it talks to them only through
// window.synthlab (merged onto, never replaced).
// - Player: plays /song/plan.json — each section's `url` once, then its
//   `loop_url` for every later pass (a section under a whole stem has
//   `pass_urls`, a WAV a pass: each plays the stem's next slice; a loop
//   still plays `loop_url`), back to back on audio.js's context and
//   through its master bus (getMaster: master gain -> limiter -> analyser),
//   so the song meets the same limiter and meter as a preview. The song
//   gain is already in every WAV; nothing here scales it again.
// - It schedules one pass at a time, a short lookahead ahead, so an edit (a
//   changed URL in the re-fetched plan) is heard from the next pass start —
//   never mid-cycle; until the new render arrives the pass replays the
//   slot's last buffer. Buffers are cached by URL: the URLs are content keys.
//   One exception, `swapNow`: a track's sound edited in the sound dock
//   (songdock.js) swaps the playing pass where it stands.
// - songSchedule is the pure half (start / advance / entry / schedule /
//   remap / locate), run under node by tests/studio/test_song_schedule.py.
//   With no `document` nothing else is wired.
// - `loop` is false (the song), true (the selected section) or [first id,
//   last id] — a RANGE of sections (songsel.js's `songRange`, which says
//   where a range goes when a section ends and names the wrap's WAV: the
//   range's first section with its last one's ring folded in). A jump
//   (`songClock().jump`, songline.js's "next section") takes over at the
//   next pass start, as a loop toggled does.
// - Keys (Song view only, through piano.js's keysBlocked gate): L loop
//   (Space is transport.js's: the page's ▶, its ⏸ while it plays); on
//   the focused arranger grid (one tab stop, roving aria-activedescendant)
//   arrows / Home / End move, Enter opens the cell (or drops the pool pattern open in the drawer into it), Delete clears
//   it, D duplicates its section. D is piano.js's E key: it is claimed in
//   window capture only while the grid holds focus. Esc closes the drawer.
// - Recording lives in songrec.js (part of this island, its own budget):
//   it reads S.songClock() and delays the first pass by S.songCountIn.
// 300 -> 310: the playhead caches its section column geometry (resize/settle)
// instead of two getBoundingClientRect reads every frame.
// 310 -> 328: swapNow (the sound dock's mid-pass swap and its crossfade).
// 329 -> 340 (the transport): it reports to transport.js — a
// claim with its section and bar, a wait while the first pass loads, a
// held ⏸ — and ■ takes the marker back to where ▶ started (`back`).
// 340 -> 352: a ramped section's bars are its own bar lines (the
// plan's `bar_starts`): the playhead, the position and a start mid-section.
// Budget: <= 352 code lines.

(() => {
  "use strict";
  window.synthlab ??= {};
  const S = window.synthlab;

  // ---- the schedule (pure) -------------------------------------------------
  // A cursor is the next pass to start: {sid, i, pass, at, offset} — the
  // section's id and index, its pass (round its cycles x repeats), the ctx
  // time it starts, and seconds into the buffer (only a mid-pass start).
  const indexOf = (plan, sid) => plan.sections.findIndex((s) => s.section_id === sid);
  const passLen = (plan, s) => s.cycle_samples / plan.sr;
  // `from` is samples into section `sid` (the first section if unknown); a
  // song start past its end runs on into the next section, a loop wraps.
  function start(plan, { sid, from = 0, loop = false, at = 0 } = {}) {
    let i = Math.max(0, indexOf(plan, sid)), s = plan.sections[i];
    if (s && loop) from %= s.cycle_samples * s.passes;
    for (; s && from >= s.cycle_samples * s.passes; s = plan.sections[++i]) from -= s.cycle_samples * s.passes;
    if (!s) return null;
    const pass = Math.floor(from / s.cycle_samples);
    return { sid: s.section_id, i, pass, at, offset: (from - pass * s.cycle_samples) / plan.sr };
  }
  const fresh = (plan, i, at) => plan.sections[i] ? { sid: plan.sections[i].section_id, i, pass: 0, at, offset: 0 } : null;
  function advance(plan, cur, loop) {
    const s = plan.sections[cur.i], at = cur.at + passLen(plan, s) - cur.offset;
    return loop === true || cur.pass + 1 < s.passes ? { ...cur, pass: (cur.pass + 1) % s.passes, at, offset: 0 } : S.songRange?.wrap(plan, cur, loop, at) || fresh(plan, cur.i + 1, at);
  }
  // a loop always plays the loop render: it folds its own tail
  function entry(plan, cur, loop) {
    const s = plan.sections[cur.i], one = loop === true, head = !one && cur.pass === 0, own = !one && s.pass_urls?.[cur.pass]; // own: a WAV of this pass alone
    const came = head && cur.after && plan.sections[indexOf(plan, cur.after)]; // a range loop's wrap: after that section
    return { ...cur, head, own: !!own, url: came ? S.songRange.url(s, came) : own || (head ? s.url : s.loop_url), dur: passLen(plan, s) };
  }
  function schedule(plan, opts = {}) {
    const out = [], end = (opts.at || 0) + (opts.horizon ?? Infinity);
    for (let c = start(plan, opts); c && c.at < end && out.length < 4096; c = advance(plan, c, opts.loop)) out.push(entry(plan, c, opts.loop));
    return out;
  }
  // the same cursor in a re-fetched plan: found by section id; a pass that
  // no longer exists moves on (a loop wraps); a deleted section hands over
  // to whatever now sits at its index
  function remap(plan, cur, loop) {
    const found = indexOf(plan, cur.sid), i = found < 0 ? Math.min(cur.i, plan.sections.length - 1) : found, s = plan.sections[i];
    if (!s || found < 0) return fresh(plan, i, cur.at);
    return cur.pass < s.passes || loop === true ? { ...cur, i, pass: cur.pass % s.passes } : fresh(plan, i + 1, cur.at);
  }
  // where time `t` falls inside scheduled pass `e`: 1-based song bar, the
  // section, `at` bars into it across ALL its passes (the playlist's x: one
  // sweep through the echoes) and `pos` / `frac` into one repeat of its bars
  function locate(plan, e, t) {
    const i = indexOf(plan, e.sid), s = plan.sections[i];
    if (!s) return null;
    const cyc = passLen(plan, s), barSec = cyc / s.cycle_bars, inSec = e.pass * cyc + Math.max(0, t - e.at + e.offset);
    const b = s.bar_starts ? rampBars(plan, s, inSec) : inSec / barSec; // a ramp's bars are its own bar lines
    const pos = s.bar_starts ? b % (s.cycle_bars * s.cycles) : (inSec % (cyc * s.cycles)) / barSec; // bars into the repeat
    return { sid: s.section_id, i, name: s.name, pos, frac: pos / s.bars, at: b % (s.bars * s.repeats),
      bar: s.start_bar + Math.floor(b + 1e-9) + 1,
      total: plan.sections.reduce((n, x) => n + x.bars * x.repeats, 0) };
  }
  // seconds into a ramped section's passes -> bars, by the plan's `bar_starts` (one pass's, in samples)
  function rampBars(plan, s, sec) {
    const st = s.bar_starts, n = st.length - 1, k = Math.floor(sec / passLen(plan, s)), at = sec * plan.sr - k * st[n];
    let i = 0;
    while (i < n - 1 && st[i + 1] <= at) i++;
    return k * s.cycle_bars + i + (at - st[i]) / (st[i + 1] - st[i]);
  }
  // bars into a section -> samples into it (a ramp's by its own bar lines)
  const barSamples = (s, bars) => (s.bar_starts ? Math.floor(bars / s.cycle_bars) * s.cycle_samples + s.bar_starts[bars % s.cycle_bars] : Math.round(bars * s.cycle_samples / s.cycle_bars));
  Object.assign(S, { songSchedule: { start, advance, entry, schedule, remap, locate, barSamples } });
  if (typeof document === "undefined") return;

  // ---- the player ----------------------------------------------------------
  // live: the scheduled passes (entry + src); bufs: url -> Promise<AudioBuffer>;
  // ready: url -> the decoded buffer; lastGood: "sid:h|l" -> the buffer that
  // slot last played (an edit not rendered yet replays it)
  const LOOK = 1.5, TICK = 50; // seconds queued ahead; pump period (ms)
  let plan = null, loop = false, playing = false, sel = null; // sel: the selected section's id
  let cursor = null, bus = null, pumpId = 0, gen = 0, planGen = 0, lastSid = null, here = null, home = 0, paused = false; // here: where it is heard (frame)
  const live = [], bufs = new Map(), ready = new Map(), lastGood = new Map();
  const ctx = () => S.getCtx(), $ = (id) => document.getElementById(id), fail = (err) => S.toast?.(String(err?.message || err));

  const load = (url) => bufs.get(url) || bufs.set(url, S.fetchBuffer(url).then((b) => { ready.set(url, b); return b; },
    (err) => { bufs.delete(url); throw err; })).get(url);
  async function fetchPlan(q = "") {
    const res = await fetch("/song/plan.json" + q);
    if (res.ok) return res.json();
    // a 422 (an over-long song) carries the studio's toast fragment
    throw new Error(S.toastOf(await res.text(), `song plan ${res.status}`));
  }
  // drop decoded audio the plan no longer names (every edit mints new URLs)
  function keep(p) {
    const urls = new Set(p.sections.flatMap((s) => [s.url, s.loop_url, ...(s.pass_urls || [])]).concat(S.songRange?.urls(p, loop) || []));
    for (const m of [bufs, ready]) for (const u of [...m.keys()]) if (!urls.has(u)) m.delete(u);
  }
  // fetch ahead. A failure is said only while it is still asked for: a song
  // replaced or a plan re-read mid-fetch leaves requests for audio that is
  // no longer there (a 404), and that is nobody's news
  const want = (u) => { const g = gen, p = planGen; load(u).catch((err) => { if (g === gen && p === planGen) fail(err); }); };
  // fetch what is coming, in the order it plays
  function prefetch() {
    if (!plan || !cursor) return;
    for (const u of S.songRange?.urls(plan, loop) || []) want(u); // a range loop's wrap pass
    for (const s of loop === true ? [plan.sections[cursor.i]] : plan.sections.slice(cursor.i)) {
      if (loop !== true) (s.pass_urls || [s.url]).forEach(want);
      if (loop === true || (s.passes > 1 && !s.pass_urls)) want(s.loop_url);
    }
  }

  const slotOf = (e) => `${e.sid}:${e.url.includes("after=") ? "w" : e.own ? e.pass : e.head ? "h" : "l"}`;
  function pump() {
    const c = ctx(), now = c.currentTime;
    while (live[0] && live[0].at + live[0].dur - live[0].offset < now - 1) live.shift();
    while (playing && cursor && cursor.at < now + LOOK) {
      const e = entry(plan, cursor, loop), slot = slotOf(e), old = lastGood.get(slot);
      const buf = ready.get(e.url) || (old && Math.abs(old.duration - e.dur) < 0.005 ? old : null);
      if (!buf) { // not decoded in time: keep the clock, join mid-pass once it is
        want(e.url);
        const skip = now + 0.05 - cursor.at;
        if (skip > 0) cursor = cursor.offset + skip < e.dur - 0.05 ? { ...cursor, at: cursor.at + skip, offset: cursor.offset + skip } : advance(plan, cursor, loop);
        break;
      }
      lastGood.set(slot, buf);
      const src = c.createBufferSource(), lag = Math.max(0, now - e.at);
      src.buffer = buf; src.connect(bus); src.start(e.at + lag, e.offset + lag);
      live.push({ ...e, src });
      cursor = advance(plan, cursor, loop);
    }
  }

  function silence() { // fade the bus out and stop what it carries
    const b = bus, old = live.splice(0);
    bus = null; if (!b) return;
    b.gain.setTargetAtTime(0, ctx().currentTime, 0.01);
    setTimeout(() => { old.forEach((x) => { try { x.src.stop(); } catch {} }); b.disconnect(); }, 80);
  }
  function halt() { gen++; playing = false; here = null; cursor = null; clearInterval(pumpId); silence(); }
  function stop() { halt(); paused = false; render(); S.transport?.release("song"); }
  // ■: stop, and the marker goes back to the bar ▶ started from (after a ⏸
  // too: `home` is kept across a resume)
  function back() { const h = (playing || paused) && home; stop(); if (h !== false && h != null) S.songMark?.(h); }
  // ⏸: stop where the song is heard — the marker moves to that bar
  // (songline.js), so ▶ (or Space) carries on from there; the dock's
  // read-out (transport.js) holds it as paused
  function pause() {
    const t = S.heardTime();
    const e = playing && plan && live.find((x) => x.at <= t && t < x.at + x.dur - x.offset);
    const loc = e && locate(plan, e, t), was = loop;
    stop();
    if (!loc) return;
    S.songMark?.(loc.bar - 1); paused = true;
    S.transport?.hold("song", { kind: "Song", label: `bar ${loc.bar} / ${loc.total} · ${loc.name}`, stop: back, play: () => { loop = was; play(); } });
  }
  // from the start marker: songline.js's bars into the selected section
  // `head`: start on the head plan (the first section alone, ready in a
  // second or two) and let the whole plan take over at a pass start
  async function play(head) {
    S.ensureCtx(); halt();
    const c = ctx(), my = gen, bars = S.songFrom?.() || 0;
    if (!paused) home = sel ? (+$(`sec-${sel}`)?.dataset.startBar || 1) - 1 + bars : 0; // where ■ goes back to
    paused = false;
    await c.resume();
    S.transport?.take("song", "Song"); // the loop, the jam, a solo, a player: one thing plays
    S.transport?.queue("song", { label: `the song from bar ${home + 1}`, at: null });
    try {
      keep(plan = await fetchPlan(head === true ? "?head=1" : ""));
      const s0 = plan.sections[Math.max(0, indexOf(plan, sel))];
      const first = start(plan, { sid: sel, loop, from: s0 ? barSamples(s0, bars) : 0 });
      if (!first) return;
      await load(entry(plan, first, loop).url);
      if (my !== gen) return; // stopped (or restarted) while loading
      (bus = c.createGain()).connect(S.getMaster());
      const lead = S.songCountIn?.(c.currentTime + 0.08, plan, first) || 0; // songrec.js: a count-in bar
      cursor = { ...first, at: c.currentTime + 0.08 + lead };
      const at0 = cursor.at; // pump() moves the cursor on: the claim's start is this one
      playing = true; prefetch(); pump();
      S.transport?.claim("song", { kind: "Song", stop: back, pause, at: at0, lead, beat: 60 / plan.bpm,
        label: () => (here ? `${Array.isArray(loop) ? "range · " : loop ? "loop · " : ""}${here.name}` : "…"),
        pos: () => here && { text: `bar ${here.bar} / ${here.total}`, frac: (here.bar - 1) / here.total, beat: (here.pos * 4) % 1 } });
      pumpId = setInterval(pump, TICK);
      if (plan.head) refresh(); // the rest renders while the head plays
    } catch (err) { fail(err); } finally { render(); if (my === gen && !playing) S.transport?.release("song"); }
  }
  // "song-autoplay" (HX-Trigger of a starter, an auto-arrange, a generator):
  // what was made plays by itself. {head}: a new song, from bar 1 — from
  // another page, through the rail's own Song link first; {section}: one
  // part was rolled — loop the first section it sounds in, unless the song
  // is playing already (the edited pass swaps in at its next start). The
  // button that started a song is gone with the sheet: the keyboard goes
  // to the playlist (sync), not to <body>.
  document.addEventListener("song-autoplay", (e) => {
    const d = e.detail || {};
    if (d.head) { refocus = true; setTimeout(sync, 120); if (!$("song-topbar")) document.querySelector('a[href="/view/song"]')?.click(); sel = null; loop = false; play(true); }
    else if (d.section && !playing) { sel = d.section; loop = true; play(); }
  });
  // loop toggled or another section selected: take over at the next pass
  // start; passes already queued past it are cancelled
  function retime(to) {
    if (!playing || !plan) return;
    const now = ctx().currentTime, cur = live.find((x) => x.at <= now && now < x.at + x.dur - x.offset);
    for (const x of live.filter((y) => y.at > now + 0.02)) { try { x.src.stop(); } catch {} live.splice(live.indexOf(x), 1); }
    const at = cur ? cur.at + cur.dur - cur.offset : now + 0.05, here = cur && remap(plan, cur, false);
    cursor = to ? start(plan, { sid: to, at }) : loop === true ? start(plan, { sid: sel ?? cur?.sid, loop: true, at }) : here ? advance(plan, here, loop) : start(plan, { sid: sel, at });
    prefetch(); pump();
  }
  // every song-changed, even an empty one: the listed ids are a hint, the
  // plan's URLs (which carry the song gain) are the truth
  let hot = false; // a swap is owed: kept until one lands, whichever refresh carries it
  async function refresh() {
    if (!playing) { plan = null; hot = false; return; } // fetched afresh on the next play
    const my = ++planGen;
    try {
      const p = await fetchPlan();
      if (my !== planGen || !playing) return;
      keep(plan = p);
      cursor = cursor && remap(p, cursor, loop);
      if (sel && indexOf(p, sel) < 0) sel = null;
      prefetch();
      if (hot) await swapNow(my);
    } catch (err) { if (my === planGen) fail(err); } // (else a later plan is on its way)
  }
  // {now} (songdock.js: a track's sound edited in the dock): the pass that
  // is PLAYING swaps too, where it stands, once its new render is decoded —
  // the same position in the new buffer, over a 40 ms crossfade
  async function swapNow(my, again) {
    const c = ctx(), cur = live.find((x) => x.at <= c.currentTime && c.currentTime < x.at + x.dur - x.offset);
    const here = cur && remap(plan, cur, loop), e = here && here.sid === cur.sid && entry(plan, here, loop);
    if (!e) { hot = false; return; }
    const buf = await load(e.url);
    if (my !== planGen || !playing) return; // a later plan is on its way: it swaps
    // the pass ended while its render decoded: the one playing now, once
    if (!live.includes(cur)) { if (again) hot = false; else await swapNow(my, true); return; }
    hot = false;
    const t = c.currentTime + 0.03, into = cur.offset + t - cur.at;
    // what is queued behind it was scheduled from the old plan (pump looks
    // 1.5 s ahead: a short loop would play one more old pass): again, from this one
    for (const x of live.filter((y) => y.at > t)) { try { x.src.stop(); } catch {} live.splice(live.indexOf(x), 1); }
    cursor = advance(plan, here, loop);
    if (cur.src.buffer !== buf && into < cur.dur - 0.15) { // (else: ending, or already this render)
      const fade = (src, from, to) => {
        const g = c.createGain();
        g.gain.value = from; g.gain.setValueAtTime(from, t); g.gain.linearRampToValueAtTime(to, t + 0.04);
        src.disconnect(); src.connect(g).connect(bus);
      };
      const src = c.createBufferSource();
      src.buffer = buf; fade(src, 0, 1); src.start(t, into);
      fade(cur.src, 1, 0); cur.src.stop(t + 0.06);
      live[live.indexOf(cur)] = { ...cur, url: e.url, src };
      lastGood.set(slotOf(e), buf);
    }
    pump();
  }
  document.addEventListener("song-changed", (e) => { hot ||= e.detail?.now === true; refresh(); });

  function setLoop(on) { if (loop !== on) { loop = on; render(); retime(); } }
  function select(sid) { if (sid && sel !== sid) { sel = sid; render(); if (loop === true) retime(); } }
  const loopKey = () => { const on = loop ? false : S.songRange?.pick?.(true) || true; setLoop(on); if (on && !playing) play(); };
  // songrec.js (recording) reads the clock and drives the transport through this
  S.songClock = () => ({ plan, live, loop, playing, sel, cur, play, stop, setLoop, select, jump: (sid) => { select(sid); retime(sid); } });

  // ---- playhead ------------------------------------------------------------
  // The playing section's column geometry (its left edge and the px a bar)
  // is measured once (and again after a resize, a zoom or a swap), not with
  // two getBoundingClientRect calls every frame.
  let geo = null;
  const regeo = () => { geo = null; };
  for (const ev of ["resize", "synthlab-geometry"]) addEventListener(ev, regeo);
  document.addEventListener("htmx:after:settle", regeo);
  function frame() {
    requestAnimationFrame(frame);
    if (!playing || !plan) return;
    const c = ctx(); // stop if interrupted (a call, a suspended tab)
    if (c.state !== "running") return stop();
    // what is heard now left the graph an output latency ago
    const t = S.heardTime();
    const e = live.find((x) => x.at <= t && t < x.at + x.dur - x.offset);
    if (!e) { if (!cursor && !live.some((x) => x.at > t)) stop(); return; } // the song ended
    const loc = (here = locate(plan, e, t)), out = $("song-position");
    if (!loc) return;
    const txt = `bar ${loc.bar} / ${loc.total} · ${loc.name}${loop ? " ⟳" : ""}`;
    // the live region speaks when the section changes, not on every bar
    if (out && out.textContent !== txt) { out.setAttribute("aria-live", loc.sid !== lastSid ? "polite" : "off"); out.textContent = txt; }
    lastSid = loc.sid;
    const sc = $("arr-scroll"), th = $(`sec-${loc.sid}`);
    if (sc && th) {
      const ph = sc.querySelector(".song-playhead") || Object.assign(sc.appendChild(document.createElement("i")), { className: "song-playhead" });
      if (geo?.th !== th) {
        const r = th.getBoundingClientRect(), box = sc.getBoundingClientRect();
        geo = { th, x: r.left - box.left + sc.scrollLeft, per: r.width / ((+th.dataset.bars || 1) * (+th.dataset.repeats || 1)) };
        document.querySelectorAll(".arr-section.playing").forEach((x) => x.classList.toggle("playing", x === th));
        th.classList.add("playing");
      }
      ph.hidden = false; // empty, so silent to assistive tech; moved by translate only
      ph.style.translate = `${(geo.x + loc.at * geo.per).toFixed(1)}px 0`;
    }
    // the open cell's pattern grid follows along (audio.js's --phase line,
    // written on the .playhead itself, never the inherited-by-every-cell .grid)
    const d = $("song-drawer"), g = d?.querySelector(".grid .playhead");
    if (g && !d.hidden && d.dataset.sectionId === loc.sid) {
      // a trimmed block (data-at / data-len, bars): its pattern starts at
      // the block's first bar, and nothing plays outside it
      const bars = Number(d.querySelector('#drawer-grid [name="bars"]')?.value) || 1, into = loc.pos - (Number(d.dataset.at) || 0);
      g.hidden = into < 0 || into >= (Number(d.dataset.len) || Infinity);
      g.style.setProperty("--phase", ((Math.max(0, into) % bars) / bars).toFixed(3));
    }
  }

  function render() {
    for (const id of ["song-play", "mix-play"]) $(id)?.setAttribute("aria-pressed", String(playing && !loop));
    for (const id of ["song-loop", "mix-loop"]) $(id)?.setAttribute("aria-pressed", String(!!loop));
    document.querySelectorAll(".arr-section").forEach((th) => th.classList.toggle("selected", th.dataset.sectionId === sel));
    if (playing) return;
    document.querySelectorAll(".song-playhead").forEach((ph) => { ph.hidden = true; });
    document.querySelectorAll(".arr-section.playing").forEach((x) => x.classList.remove("playing"));
    document.querySelector("#song-drawer .grid .playhead")?.style.removeProperty("--phase");
    lastSid = null; geo = null;
  }

  // ---- arranger grid: one tab stop, a roving cursor ------------------------
  let cur = null, refocus = false; // cur: {sid, tid} of the cursor cell
  const grid = () => document.querySelector(".arranger-grid");
  const rows = () => [...document.querySelectorAll(".arranger-grid tbody tr")].map((tr) => [...tr.querySelectorAll(".arr-cell")]);
  function mark(g, cell) {
    g.querySelector(".arr-cell.cursor")?.classList.remove("cursor");
    cell.classList.add("cursor");
    g.setAttribute("aria-activedescendant", cell.id);
    cur = { sid: cell.dataset.sectionId, tid: cell.dataset.trackId };
  }
  function setCursor(cell, scroll) {
    const g = grid(); if (!g || !cell) return;
    mark(g, cell);
    if (scroll) cell.scrollIntoView({ block: "nearest", inline: "nearest" });
    select(cur.sid);
  }
  function initGrid() {
    const g = grid(); if (!g || g.hasAttribute("tabindex")) return;
    g.tabIndex = 0; g.setAttribute("role", "grid");
    g.setAttribute("aria-label", "Arranger: arrows move, Enter opens a cell, Delete clears it, D duplicates its section");
    g.querySelectorAll(".cell-btn").forEach((b) => { b.tabIndex = -1; });
    const cell = (cur && $(`cell-${cur.sid}-${cur.tid}`)) || rows().flat()[0];
    if (cell) mark(g, cell);
  }
  // every arranger action is an htmx post, so its OOB regions land.
  // htmx.ajax posts from <body>: every named input on the page rides along
  // and only the names in `values` are replaced — so a post that means "no
  // ids" must say so (NONE), or the open drawer's own hidden section_id /
  // track_id answer for it: Esc reopened the drawer it was closing.
  const NONE = { section_id: "", track_id: "", pattern_id: "" };
  function post(path, values = {}) {
    refocus = document.activeElement === grid();
    htmx.ajax("POST", path, { values, swap: "none" });
  }
  const MOVES = { "ArrowLeft": [0, -1], "ArrowRight": [0, 1], "ArrowUp": [-1, 0], "ArrowDown": [1, 0], "Home": [0, -1e3], "End": [0, 1e3] };
  window.addEventListener("keydown", (e) => {
    if (!($("song-topbar") || $("mixer-head")) || e.ctrlKey || e.metaKey || e.altKey) return; // the Mix view plays the song too
    const g = grid(), k = e.key, drawer = $("song-drawer");
    if (k === "Escape") { // not while typing, nor while a menu or dialog owns Esc
      if (drawer && !drawer.hidden && !S.isTypingTarget?.(e.target) && !document.querySelector(":popover-open, dialog[open]")) post("/song/drawer", NONE);
      return;
    }
    if (S.keysBlocked(e.target)) return;
    if (k.toLowerCase() === "l" && !e.repeat) { loopKey(); return; }
    if (!g || e.target !== g || S.songKeys?.(e, g)) return; // songsel.js: a range's keys first
    const m = rows(), at = g.querySelector(".arr-cell.cursor");
    let r = Math.max(0, m.findIndex((row) => row.includes(at))), c = Math.max(0, m[r]?.indexOf(at) ?? 0);
    if (k in MOVES) {
      e.preventDefault(); if (!m.length) return;
      r = Math.min(m.length - 1, Math.max(0, r + MOVES[k][0]));
      setCursor(m[r][Math.min(m[r].length - 1, Math.max(0, c + MOVES[k][1]))], true);
      return;
    }
    const cell = m[r]?.[c], here = cell && { section_id: cell.dataset.sectionId, track_id: cell.dataset.trackId };
    if (!cell || e.repeat) return;
    if (k === "Enter") {
      e.preventDefault(); // Enter drops the pool pattern open in the drawer, else opens the cell
      const pid = drawer && !drawer.hidden && !drawer.dataset.sectionId && drawer.dataset.patternId;
      if (pid && drawer.dataset.kind === cell.dataset.kind) post("/song/cell", { ...here, pattern_id: pid });
      else if (cell.dataset.kind) post("/song/drawer", here);
    } else if (k === "Delete" || k === "Backspace") {
      e.preventDefault(); if (cell.dataset.patternId) post("/song/cell", { ...here, pattern_id: "" });
    } else if (k.toLowerCase() === "d") {
      e.stopImmediatePropagation(); // not piano.js's E
      post(`/song/section/${encodeURIComponent(here.section_id)}/duplicate`);
    }
  }, true);

  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-song-action]")?.dataset.songAction;
    if (a === "play") { if (playing && !loop) back(); else { loop = false; play(); } }
    else if (a === "loop") loopKey();
    else if (a === "stop") back();
    else if (a === "pause") pause();
    if (a) return;
    const cell = e.target.closest(".arr-cell"), th = e.target.closest(".arr-section");
    if (cell) { setCursor(cell, false); grid()?.focus({ preventScroll: true }); }
    else if (th && !e.target.closest("button, .menu-pop")) select(th.dataset.sectionId);
  });
  document.addEventListener("pointerdown", () => { refocus = false; });

  // ---- after any region swap -------------------------------------------------
  // .open marks what the drawer shows; opening a cell selects its section
  // and loops it (the section loops while the drawer is open)
  let drawerKey = "";
  function syncOpen() {
    const d = $("song-drawer"), open = d && !d.hidden, sid = open && d.dataset.sectionId;
    document.querySelectorAll(".arr-cell.open, .pool-pattern.open").forEach((x) => x.classList.remove("open"));
    if (sid) $(`cell-${sid}-${d.dataset.trackId}`)?.classList.add("open");
    else if (open && d.dataset.patternId) $(`pool-${d.dataset.patternId}`)?.classList.add("open");
    const key = sid ? `cell:${sid}` : open ? "pool" : "";
    if (key === drawerKey) return;
    if (sid) { select(sid); setLoop(true); } else if (drawerKey.startsWith("cell:")) setLoop(false);
    drawerKey = key;
  }
  function sync() {
    if (!($("song-topbar") || $("mixer-head"))) { if (playing || paused) stop(); drawerKey = ""; return; }
    initGrid(); syncOpen(); render();
    if (refocus && document.activeElement === document.body) grid()?.focus({ preventScroll: true });
  }
  // htmx fires after:swap on the request's source, which an OOB swap may
  // have detached (a mute button inside the arranger), so watch the DOM for
  // the regions themselves instead
  const REGIONS = new Set(["view", "song-topbar", "song-arranger", "song-drawer", "song-pool", "mixer-strips"]);
  let queued = false;
  new MutationObserver((recs) => {
    if (queued || !recs.some((r) => [...r.addedNodes].some((n) => REGIONS.has(n.id)))) return;
    queued = true; queueMicrotask(() => { queued = false; sync(); });
  }).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", () => { sync(); requestAnimationFrame(frame); });
})();
