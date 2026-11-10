// synthlab/studio/static/songline.js
// The Song island's sixth file: the playlist's time axis. The
// arranger is drawn on a true time axis — every x is bars x --bar-w, a
// custom property on .song-view (fragments/song_arranger.html, style.css) —
// and this owns what turns pixels into bars and back:
//   - zoom: --bar-w steps through STEPS (px a bar) from the − / + buttons
//     in the ruler's corner (fit: exactly the lanes' width, between the steps) and Ctrl+wheel over the scroller (the
//     bar under the pointer stays put); per browser (localStorage), and a
//     first visit opens at "fit", never under 16 (the song fills the lanes);
//   - the start marker: a click (or tap) on the ruler, or ← / → / Home on
//     it, puts `.ruler-mark` on a bar and selects the section under it;
//     S.songFrom() is how many bars into the SELECTED section Play starts
//     (song.js turns them into the plan's samples); Enter on the ruler, or
//     a click while the song plays, plays from it. Selecting another
//     section moves the marker to its first bar;
//   - follow: when the playhead leaves the scroller it pages on — held off
//     for 4 s after the user SCROLLS it (a click on a block is not a
//     scroll), then it looks again. An IntersectionObserver: no per-frame
//     work;
//   - edges: a section's right edge dragged by whole passes sets its
//     repeats, with Alt its bars. Only CSS moves during the drag (--reps /
//     --bars on that section's column, head and cells, and a tag on the
//     handle); the release posts once, through the routes the section menu
//     uses, and a refusal (a toast) gives the edge back. Esc cancels. The
//     keyboard and touch path is the section menu's bars field and repeat ±.
//     A whole stem's strip has two: its head trims it, its tail sets how
//     many bars play (POST /song/long; the rows under the arranger are the
//     form for both). A block has two (POST /song/cell/span): the left one
//     moves where it starts — the pattern restarts there — the right one
//     trims or extends it, and dragged past the section's last pass it
//     carries the pattern into the following sections (a spill; the route
//     decides how far). Double-click an edge: fill the section. On the
//     focused grid Alt+← / → slide the cursor's block a bar and
//     Alt+Shift+← / → move its end (window capture: Alt+← is the
//     browser's Back); the drawer's from / to fields are the form.
// Nothing here holds song state: the marker is a place on the ruler, and
// every edit is a route. Its pure half (`songLine`) runs under node
// (tests/studio/test_song_playlist.py). It loads after song.js.
//   - sections: `synthlab.songJump(±1)` — Ctrl+← / → on the
//     grid (songsel.js), the tool bar's ⏮ ⏭ — moves the selection, the
//     marker and the cursor to the next section's first bar; while the song
//     plays the player follows at the next pass start. Shift+click two
//     section heads (or Shift+← / → on the ruler) marks a RANGE of
//     sections, drawn as a brace on the ruler with a locator at each end;
//     L loops it (songsel.js `songRange.pick`, song.js). The brace is solid
//     while the range loops;
//   - the chord track's tiles (`.ctile`): a click (Enter) opens the tile's
//     menu and a boundary's + its own (both `data-ctx-pop`: menu.js opens
//     them, asked by a `contextmenu` this sends); ← → / Home / End move
//     between tiles (one tab stop), 1-7 set the degree, Delete removes;
//     a tile's left edge drags where its chord starts, by beats
//     (POST /song/chords op=edge);
//   - `song-audition` (the answer of a transition placed on a boundary):
//     the marker goes two bars before it and the song plays from there.
// The marker's bar rides every "at the marker" post as the hidden
// #mark-bar. Zoom's gestures are zoom.js's (#arr-scroll is a
// `[data-zoom-own]` scroller: Ctrl+wheel about the pointer, a pinch, two
// fingers, Ctrl+= / − / 0 arrive here as `zoom-step`), so the playlist and
// the piano roll zoom by the same hands.
// 150 -> 230: the first 150 was an estimate made before any of it was
// written; zoom, the marker and follow took 124, and the edges are a small
// table (`EDGES`) over one pointer handler rather than lines joined to fit.
// 230 -> 300: the section jump, the range brace and its heads, the
// chord tiles' keys and edge, the two click-to-open menus and the audition
// — all places on the time axis, which is this file; songsel.js (120) is
// the range of CELLS and has no room for them — and a marker that keeps its
// bar when a split or an insert moves the sections under it.
// 300 -> 306: the follow tells a scroll from a click and looks again after
// one (or it would lose the playhead for good after any click in the playlist).
// 306 -> 312: stopped, the ledger's "bar n / N · Section" is the marker's
// (not bar 1 while the marker and the cue say bar 41); fit fills the
// lanes exactly, not to the step under it (a fifth of a wide window empty).
// Budget: <= 326 code lines (312 -> 326: the time-true axis).

(() => {
  "use strict";
  window.synthlab ??= {};
  const S = window.synthlab;

  // ---- pixels and bars (pure) ----------------------------------------------
  const STEPS = [8, 12, 16, 24, 32, 48, 64, 96]; // px a bar
  const steps = (coarse) => (coarse ? STEPS.filter((s) => s >= 16) : STEPS);
  const nearest = (w, list) => list.reduce((a, b) => (Math.abs(b - w) < Math.abs(a - w) ? b : a));
  // the 0-based bar under x pixels from the ruler's left edge
  const barAt = (x, barW) => Math.max(0, Math.floor(x / barW));
  // the next zoom step up (dir > 0) or down from barW; the ends hold
  function zoomStep(barW, dir, coarse = false) {
    const list = steps(coarse), i = list.indexOf(nearest(barW, list));
    return list[Math.min(list.length - 1, Math.max(0, i + Math.sign(dir)))];
  }
  // the px a bar that shows all `bars` in `width` px of lanes, to a
  // quarter px, inside the steps' range (the smallest when none does: a
  // long song scrolls). Not a step: the largest step under it left up to a
  // fifth of a wide window empty (8 px a bar where 9.9 fit)
  const fit = (width, bars, coarse = false) => {
    const list = steps(coarse);
    return Math.min(list.at(-1), Math.max(list[0], Math.floor((width / Math.max(1, bars)) * 4) / 4));
  };
  // the step a width draws as (the ruler's number spacing): the largest at or under it
  const stepOf = (w) => STEPS.filter((s) => s <= w).pop() ?? STEPS[0];
  // how many whole passes (each `bars` long) a drag of dx px covers
  const passes = (dx, bars, barW) => Math.round(dx / (bars * barW));
  Object.assign(S, { songLine: { STEPS, barAt, zoomStep, fit, passes } });
  if (typeof document === "undefined") return;

  const $ = (id) => document.getElementById(id);
  const view = () => document.querySelector(".song-view");
  const coarse = () => matchMedia("(pointer: coarse)").matches;
  const barW = () => parseFloat(getComputedStyle(view()).getPropertyValue("--bar-w")) || 24;
  const KEY = "synthlab.song.zoom";

  // ---- zoom -----------------------------------------------------------------
  function lanes() { // the scroller's width less the sticky track heads
    const sc = $("arr-scroll");
    return sc ? sc.clientWidth - (sc.querySelector(".arr-corner")?.offsetWidth || 0) : 0;
  }
  // `keep`: the client x whose bar must not move (the pointer's)
  function setZoom(w, keep) {
    const v = view(), sc = $("arr-scroll"), ruler = $("song-ruler"), old = barW();
    const bar = keep == null || !ruler ? null : (keep - ruler.getBoundingClientRect().left) / old;
    v.style.setProperty("--bar-w", `${w}px`);
    v.dataset.barW = String(stepOf(w));
    if (sc) sc.scrollLeft = bar == null ? sc.scrollLeft * w / old : sc.scrollLeft + bar * (w - old);
    dispatchEvent(new Event("synthlab-geometry")); // song.js measures the playhead's column again
  }
  function initZoom() {
    const v = view();
    if (!v || v.dataset.barW) return;
    let w = 0;
    try { w = Number(localStorage.getItem(KEY)); } catch {}
    const bars = Number($("song-arranger")?.dataset.songBars) || 0;
    setZoom(STEPS.includes(w) ? w : Math.max(16, fit(lanes(), bars || 16, coarse())));
  }
  function zoom(how, keep) {
    const bars = Number($("song-arranger")?.dataset.songBars) || 16;
    const w = how === "fit" ? fit(lanes(), bars, coarse()) : zoomStep(barW(), Number(how), coarse());
    setZoom(w, keep);
    try { localStorage.setItem(KEY, String(w)); } catch {}
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.("[data-zoom-to]");
    if (b) zoom(b.dataset.zoomTo);
  });
  // zoom.js owns the gestures (Ctrl+wheel, a pinch, Ctrl+= / − / 0): a step, or 0 = fit
  document.addEventListener("zoom-step", (e) => zoom(e.detail.dir || "fit", e.detail.x));

  // ---- the start marker -----------------------------------------------------
  let mark = null, abs = null; // {sid, bar}: bars into section `sid`; abs: its bar of the timeline
  const heads = () => [...document.querySelectorAll(".arr-section")];
  const startOf = (th) => Number(th.dataset.startBar) - 1;
  const spanOf = (th) => Number(th.dataset.bars) * Number(th.dataset.repeats);
  // the time-true axis (a section in its own meter or tempo, songpage.time_true):
  // a head's data-x0 / data-bs place its bars in base bars; without them x is the bar
  const bsOf = (th) => Number(th?.dataset.bs) || 1;
  const headAt = (bar) => heads().find((h) => bar < startOf(h) + spanOf(h)) || heads().at(-1);
  function xOf(bar) {
    const th = headAt(bar);
    return th?.dataset.x0 ? Number(th.dataset.x0) + (bar - startOf(th)) * bsOf(th) : bar;
  }
  function barOfX(x) {
    const hs = heads();
    if (!hs[0]?.dataset.x0) return Math.max(0, Math.floor(x));
    const th = hs.find((h) => x < Number(h.dataset.x0) + spanOf(h) * bsOf(h)) || hs.at(-1);
    return startOf(th) + Math.max(0, Math.min(spanOf(th) - 1, Math.floor((x - Number(th.dataset.x0)) / bsOf(th))));
  }
  // px a bar where `el` is (a section's own, on a time-true axis)
  const barPx = (el) => barW() * bsOf($(`sec-${el?.closest?.("[data-section-id]")?.dataset.sectionId}`));
  function draw() {
    const ruler = $("song-ruler"), th = mark && $(`sec-${mark.sid}`);
    if (!ruler) return;
    const m = ruler.querySelector(".ruler-mark") || Object.assign(ruler.appendChild(document.createElement("i")), { className: "ruler-mark" });
    m.hidden = !th;
    if (!th) return;
    const at = startOf(th) + Math.min(mark.bar, spanOf(th) - 1), field = $("mark-bar");
    if (field) field.value = String(at + 1); // what "at the marker" posts
    abs = at;
    m.style.setProperty("--b", String(xOf(at)));
    ruler.setAttribute("aria-valuenow", String(at + 1));
    const name = (th.querySelector(".section-name")?.firstChild?.textContent || "").trim(), out = $("song-position");
    ruler.setAttribute("aria-valuetext", `bar ${at + 1}, ${name}`);
    // stopped, the ledger's place is the marker's (playing, song.js's playhead writes it)
    if (out && !S.songClock?.().playing) out.textContent = `bar ${at + 1} / ${ruler.getAttribute("aria-valuemax")} · ${name}`;
  }
  // put the marker on timeline bar `at` (0-based) and select its section
  function setMark(at) {
    const th = heads().find((h) => at < startOf(h) + spanOf(h)) || heads().at(-1);
    if (!th) return;
    mark = { sid: th.dataset.sectionId, bar: Math.max(0, at - startOf(th)) };
    S.songClock?.().select(mark.sid);
    draw();
  }
  // the marker follows the selection: another section, its first bar
  function follow() {
    const sel = S.songClock?.().sel;
    if (sel && mark?.sid !== sel) mark = { sid: sel, bar: 0 };
    draw();
    brace();
    // the energy lane is one tab stop: the selected section's bar
    document.querySelectorAll(".energy").forEach((el, i) => { el.tabIndex = (sel ? el.parentElement.dataset.sectionId === sel : !i) ? 0 : -1; });
  }
  S.songFrom = () => (mark && mark.sid === S.songClock?.().sel ? mark.bar : 0);
  S.songMark = setMark;
  S.songSend = send;
  // ---- sections: the jump, and the range L loops ------------------------------
  let range = null; // [first id, last id]: the heads Shift+click marked
  S.songHeads = () => { // in song order, and only when it is two sections
    const ids = heads().map((h) => h.dataset.sectionId).filter((sid) => range?.includes(sid));
    return ids.length > 1 ? ids : null;
  };
  S.songJump = (d) => {
    const hs = heads(), c = S.songClock?.(), i = Math.max(0, hs.findIndex((h) => h.dataset.sectionId === c?.sel));
    const th = hs[Math.max(0, Math.min(hs.length - 1, i + d))], tid = document.querySelector(".arr-cell.cursor")?.dataset.trackId;
    if (!th || !c) return;
    if (c.playing) c.jump(th.dataset.sectionId);
    $(`cell-${th.dataset.sectionId}-${tid}`)?.click(); // the cursor goes with it (song.js; a cell, not its button)
    c.select(th.dataset.sectionId);
    th.scrollIntoView({ block: "nearest", inline: "nearest" });
    follow();
  };
  // the brace: the range that loops, else the one L would loop
  function brace() {
    const ruler = $("song-ruler"), c = S.songClock?.(), on = Array.isArray(c?.loop), ids = on ? c.loop : S.songRange?.pick?.();
    const ends = (ids || []).map((sid) => $(`sec-${sid}`)).filter(Boolean).sort((a, b) => startOf(a) - startOf(b));
    const el = ruler?.querySelector(".ruler-loop") || (ruler && Object.assign(ruler.appendChild(document.createElement("i")), { className: "ruler-loop" }));
    if (!el) return;
    el.hidden = ends.length < 2;
    heads().forEach((h) => h.classList.toggle("in-loop", !el.hidden && startOf(h) >= startOf(ends[0]) && startOf(h) <= startOf(ends[1])));
    if (el.hidden) return;
    const x0 = xOf(startOf(ends[0])), x1 = xOf(startOf(ends[1])) + spanOf(ends[1]) * bsOf(ends[1]);
    el.style.setProperty("--b", x0);
    el.style.setProperty("--n", x1 - x0);
    el.classList.toggle("on", on);
  }
  document.addEventListener("click", (e) => {
    const ruler = e.target.closest?.("#song-ruler"), th = e.target.closest?.(".arr-section"), jump = e.target.closest?.("[data-song-jump]");
    if (jump) S.songJump(Number(jump.dataset.songJump));
    if (th && !e.target.closest("button, .menu-pop")) range = e.shiftKey ? [mark?.sid || heads()[0].dataset.sectionId, th.dataset.sectionId] : null;
    // a thing whose menu is its `data-ctx-pop` (a chord tile, a boundary's +) opens it on a click: menu.js
    const way = e.target.closest?.("#arr-trans") && ($(`sec-${S.songClock?.().sel}`)?.querySelector(".sec-plus") || document.querySelector(".sec-plus"));
    const thing = e.target.closest?.(".ctile, .sec-plus") || way;
    if (thing) { thing.focus({ preventScroll: true }); thing.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true })); }
    if (!ruler) return void queueMicrotask(follow);
    setMark(barOfX((e.clientX - ruler.getBoundingClientRect().left) / barW()));
    if (S.songClock?.().playing) S.songClock().play();
  });
  window.addEventListener("keydown", (e) => {
    if (e.target.id !== "song-ruler" || e.ctrlKey || e.metaKey || e.altKey) return void queueMicrotask(follow);
    const th = mark && $(`sec-${mark.sid}`), now = th ? startOf(th) + mark.bar : 0;
    const to = { ArrowLeft: now - 1, ArrowRight: now + 1, Home: 0 }[e.key], hs = heads(), k = e.key.toLowerCase();
    if (e.key === "Enter") S.songClock?.().play();
    else if (to != null && e.shiftKey && mark) { // the range's far end, a section at a time
      const far = hs.findIndex((h) => h.dataset.sectionId === (range?.[1] || mark.sid)) + (e.key === "ArrowLeft" ? -1 : 1);
      if (hs[far]) range = [range?.[0] || mark.sid, hs[far].dataset.sectionId];
      brace();
    } else if (to != null) setMark(Math.max(0, to));
    else if (k === "s" || k === "i") $(k === "s" ? "arr-split" : "arr-insert")?.click(); // at the marker
    else return;
    e.preventDefault();
    e.stopImmediatePropagation(); // (not a piano key)
  }, true);
  // the chord tiles: one tab stop, ← → between them, 1-7 the degree, Delete removes
  window.addEventListener("keydown", (e) => {
    const ctile = e.target.closest?.(".ctile"), all = ctile && [...document.querySelectorAll(".ctile")], i = ctile ? all.indexOf(ctile) : -1;
    if (!ctile || e.ctrlKey || e.metaKey || e.altKey) return;
    const here = { scope: ctile.dataset.scope, slot: ctile.dataset.slot }, back = `.ctile[data-scope="${here.scope}"][data-slot="${here.slot}"]`;
    const to = { ArrowLeft: all[i - 1], ArrowRight: all[i + 1], Home: all[0], End: all.at(-1) }[e.key], url = ctile.dataset.post || "/song/chords"; // (Perform's lane: the loop's route)
    if (to) { ctile.tabIndex = -1; to.tabIndex = 0; to.focus(); }
    else if (/^[1-7]$/.test(e.key)) send(url, { ...here, op: "set", deg: e.key }, back);
    else if (e.key === "Delete" || e.key === "Backspace") send(url, { ...here, op: "remove" }, ".ctile");
    else if (e.key.length !== 1) return; // (Enter and Space are the tile's own click; Tab moves on)
    if (e.key !== "Enter" && e.key !== " ") e.preventDefault();
    e.stopImmediatePropagation(); // no bare key on a tile is a piano key
  }, true);
  // a transition was placed on a boundary: hear it, from two bars before
  document.addEventListener("song-audition", (e) => { setMark(Math.max(0, (e.detail?.bar || 1) - 1)); S.songClock?.().play(); });

  // ---- follow the playhead ---------------------------------------------------
  // A SCROLL holds the follow off (the wheel, a finger, the scroller's own
  // bar) — never a click on a block: every edit is one, and a playhead that
  // left within a second of it was never followed again (the observer only
  // speaks when the intersection CHANGES, and an out-of-view line stays
  // out). And a held-off follow looks again once the hand has rested.
  const HOLD = 4000; // ms after the last scroll before the view pages on again
  let io = null, touched = -HOLD, again = 0;
  for (const t of ["wheel", "pointerdown", "touchstart"]) {
    document.addEventListener(t, (e) => {
      if (t === "pointerdown" ? e.target.id === "arr-scroll" : e.target.closest?.("#arr-scroll")) touched = performance.now();
    }, { passive: true, capture: true });
  }
  function watch() {
    const sc = $("arr-scroll");
    io?.disconnect();
    if (!sc) return;
    const ph = sc.querySelector(".song-playhead") || Object.assign(sc.appendChild(document.createElement("i")), { className: "song-playhead", hidden: true });
    const head = sc.querySelector(".arr-corner")?.offsetWidth || 0;
    const look = () => { io.unobserve(ph); io.observe(ph); };
    io = new IntersectionObserver(([e]) => {
      clearTimeout(again);
      if (e.isIntersecting || ph.hidden) return;
      const rest = HOLD - (performance.now() - touched);
      if (rest > 0) { again = setTimeout(look, rest + 50); return; }
      const right = e.boundingClientRect.left >= e.rootBounds.right;
      // off the right edge: page on by 80%; behind (a loop wrapped): go to it
      sc.scrollLeft = right ? sc.scrollLeft + 0.8 * (sc.clientWidth - head) : parseFloat(ph.style.translate) - head - 24;
    }, { root: sc, rootMargin: `0px 0px 0px -${head}px` });
    io.observe(ph);
    // shown while out of view is no change of intersection: look again
    new MutationObserver(look).observe(ph, { attributes: true, attributeFilter: ["hidden"] });
  }

  // ---- edges ----------------------------------------------------------------
  // A drag on an edge handle moves only CSS until it is released, then posts
  // once. EDGES: handle selector -> (handle) => { move(n, e) -> the tag by
  // the pointer, post() -> [path, values] or null, clear() }; `n` is how many
  // whole bars the pointer has moved.
  const clamp = (v, lo, hi = Infinity) => Math.min(hi, Math.max(lo, v));
  const EDGES = {
    // a section's right edge: its repeats, by whole passes; Alt: its bars
    ".sec-resize"(h) {
      const th = h.closest(".arr-section"), sid = th.dataset.sectionId, bars = +th.dataset.bars, reps = +th.dataset.repeats;
      const table = th.closest("table"), total = +$("song-arranger").dataset.songBars, els = [$(`col-${sid}`), ...table.querySelectorAll(`[data-section-id="${CSS.escape(sid)}"]`)];
      let to = ["repeats", reps];
      function paint(b, r) {
        els.forEach((el) => { el.style.setProperty("--bars", b); el.style.setProperty("--reps", r); });
        table.style.setProperty("--song-bars", total + b * r - bars * reps);
      }
      return {
        move(n, e) {
          to = e.altKey ? ["bars", clamp(bars + Math.round(n / reps), 1)] : ["repeats", clamp(reps + Math.round(n / bars), 1)];
          paint(e.altKey ? to[1] : bars, e.altKey ? reps : to[1]);
          return e.altKey ? `${to[1]} bars` : `×${to[1]}`;
        },
        post: () => (to[1] === (to[0] === "bars" ? bars : reps) ? null : [`/song/section/${encodeURIComponent(sid)}/${to[0]}`, { [to[0]]: to[1] }]),
        clear: () => paint(bars, reps),
      };
    },
    // a whole stem's strip: its head trims it (what still plays stays where
    // it is), its tail sets how many bars of it play. Only the tag moves.
    ".long-edge"(h) {
      const part = h.closest(".long-part"), head = h.classList.contains("long-edge-l"), id = part.dataset.long, bars = +part.dataset.bars;
      let n = 0;
      return {
        move(d) {
          n = head ? Math.min(d, bars - 1) : Math.max(d, 1 - bars);
          return head ? `${n > 0 ? "−" : "+"}${Math.abs(n)} bars` : `${bars + n} bars`;
        },
        post: () => (n ? ["/song/long", head ? { op: "set", id, trim: n } : { op: "set", id, bars: bars + n }] : null),
        clear() {},
      };
    },
    // a chord tile's left edge: the beat its chord starts on (the one before gives way)
    ".ctile-edge"(h) {
      const beat = +h.dataset.beat, per = () => barW() / 4;
      let to = beat;
      return {
        move(_n, _e, dx) {
          to = Math.max(0.25, beat + Math.round(dx / per()));
          h.style.translate = `${(to - beat) * per()}px 0`;
          return `bar ${Math.floor(to / 4) + 1}, beat ${(to % 4) + 1}`;
        },
        post: () => (to === beat ? null : ["/song/chords", { scope: h.dataset.scope, op: "edge", slot: h.dataset.slot, at: to }]),
        clear() { h.style.translate = ""; },
      };
    },
    // a block's edges: the left one moves its start, the right one its end
    // — past the section's last pass, into the sections that follow
    ".blk-edge"(h) {
      const td = h.closest("td"), left = h.classList.contains("blk-edge-l"), at = +td.dataset.at, len = +td.dataset.len;
      const th = $(`sec-${td.dataset.sectionId}`), bars = +th.dataset.bars, here = cellOf(td);
      let to = null; // what a release posts
      function paint(a, n) {
        td.style.setProperty("--at", a);
        td.style.setProperty("--len", n);
      }
      return {
        move(n) {
          const a = left ? clamp(at + n, 0, at + len - 1) : at, end = left ? at + len : Math.max(a + 1, at + len + n);
          let past = end - spanOf(th), next = th.nextElementSibling; // bars past the section's own end
          while (past > 0 && next?.matches(".arr-section") && past > spanOf(next)) { past -= spanOf(next); next = next.nextElementSibling; }
          const spill = past > 0 && next?.matches(".arr-section");
          paint(a, Math.min(end, bars) - a);
          to = spill ? { ...here, to_section: next.dataset.sectionId, to_len: Math.min(past, +next.dataset.bars) } : { ...here, at: a, len: Math.min(end, bars) - a };
          return spill ? `→ ${next.querySelector(".section-name").textContent} bar ${to.to_len}` : `bars ${a + 1}–${Math.min(end, bars)}`;
        },
        post: () => (to && (to.to_section || to.at !== at || to.len !== len) ? ["/song/cell/span", to] : null),
        clear: () => paint(at, len),
      };
    },
  };
  const cellOf = (td) => ({ section_id: td.dataset.sectionId, track_id: td.dataset.trackId });
  // Every post goes out from an element with no field under it: htmx.ajax
  // sends every named input beneath its source, and /song/long would take
  // one for the stem's new name. The redraw replaces the grid: when it had
  // the keyboard it gets it back, once song.js has made the new one a tab stop.
  let off = () => {};
  function send(path, values, back) { // back: a selector for what gets the keyboard after the redraw (default: the grid, if it had it)
    const grid = () => document.querySelector(back || ".arranger-grid"), had = back || document.activeElement === grid();
    const regrid = () => { if (document.activeElement === document.body) grid()?.focus({ preventScroll: true }); };
    // (every settle for two seconds, not the next one: another swap's may land first;
    // a newer post's place is the one that counts, so the one before it lets go)
    const later = () => setTimeout(regrid), mine = () => document.removeEventListener("htmx:after:settle", later);
    off();
    off = mine;
    if (had) document.addEventListener("htmx:after:settle", later);
    if (had) setTimeout(mine, 2000);
    return Promise.resolve(S.post(path, values));
  }
  let edge = null; // {h, x, id, on}: the handle in hand, where it was taken
  document.addEventListener("pointerdown", (e) => {
    const sel = Object.keys(EDGES).find((s) => e.target.closest?.(s));
    if (!sel || e.button) return;
    e.preventDefault(); // no text selection, no native drag of what is under it
    edge = { h: e.target.closest(sel), x: e.clientX, id: e.pointerId, on: null, make: EDGES[sel] };
  });
  document.addEventListener("pointermove", (e) => {
    if (!edge || e.pointerId !== edge.id) return;
    if (!edge.on) { edge.on = edge.make(edge.h); edge.h.setPointerCapture(edge.id); view().classList.add("edging"); }
    edge.h.dataset.tag = edge.on.move(Math.round((e.clientX - edge.x) / barPx(edge.h)), e, e.clientX - edge.x);
  });
  function drop(e, cancel) {
    const d = edge, sent = d?.on && !cancel && d.on.post();
    if (!d || (e.pointerId != null && e.pointerId !== d.id)) return;
    edge = null;
    view()?.classList.remove("edging");
    delete d.h.dataset.tag;
    // the answer redraws the arranger; a refusal (its toast) gives the edge back
    if (sent) send(...sent).finally(d.on.clear);
    else d.on?.clear();
  }
  document.addEventListener("pointerup", (e) => drop(e, false));
  document.addEventListener("pointercancel", (e) => drop(e, true));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && edge) drop({}, true); }, true);
  document.addEventListener("dblclick", (e) => {
    const td = e.target.closest?.(".blk-edge")?.closest("td");
    if (td) send("/song/cell/span", { ...cellOf(td), at: 0, len: +$(`sec-${td.dataset.sectionId}`).dataset.bars });
  });
  // the same edit from the keyboard, on the cursor's block
  window.addEventListener("keydown", (e) => {
    const g = document.querySelector(".arranger-grid"), d = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
    const td = e.altKey && d && !e.ctrlKey && !e.metaKey && e.target === g && g.querySelector(".arr-cell.cursor.filled:not(.sel)"); // (a range's Alt+arrows are songsel.js's)
    if (!td) return;
    e.preventDefault(); // Alt+← is the browser's Back
    e.stopImmediatePropagation();
    const at = +td.dataset.at, len = +td.dataset.len, bars = +$(`sec-${td.dataset.sectionId}`).dataset.bars;
    // a slide that would run off either end of the section stays put
    if (e.shiftKey ? len + d < 1 || at + len + d > bars : at + d < 0 || at + d + len > bars) return;
    send("/song/cell/span", { ...cellOf(td), at: e.shiftKey ? at : at + d, len: e.shiftKey ? len + d : len });
  }, true);

  function sync() {
    if (!view()) return;
    initZoom();
    // a redraw that moved the sections under it (a split, an insert): the marker keeps its bar
    const th = mark && $(`sec-${mark.sid}`);
    if (abs != null && (!th || startOf(th) + Math.min(mark.bar, spanOf(th) - 1) !== abs)) setMark(abs);
    follow(); watch();
  }
  document.addEventListener("htmx:after:settle", sync);
  document.addEventListener("DOMContentLoaded", sync);
})();
