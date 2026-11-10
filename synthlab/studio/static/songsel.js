// synthlab/studio/static/songsel.js
// The playlist's range: a box of cells — sections
// x tracks, drawn `.sel`, at most `data-max-range` of them — and what is
// done with it. The Song island's seventh file; it loads after songline.js.
//
// MAKING A RANGE (one owner a gesture: docs/keys-and-midi.md)
//   Shift+click a cell        the box from the cursor's cell to it
//   a drag from an EMPTY cell the box under the pointer (a filled cell's
//                             plain drag is songdrag.js's move; with Shift
//                             it is a range from there too)
//   Shift+arrows on the grid  the box grows from the cursor's cell
//   "▭ Select" (#arr-select)  the touch and pen path: while it is on, a tap
//                             is a one-cell range and a drag a box — the
//                             cells take `touch-action: none`, so one finger
//                             selects and two still pan and pinch (zoom.js)
//   a plain arrow, Esc, a click elsewhere on the lanes: no range.
// WHAT IS DONE WITH IT (each ONE post, POST /song/cells, one undo step)
//   Delete clears · Ctrl+C then Ctrl+V at the cursor copies it by reference
//   · Ctrl+D duplicates it to the right · Alt+arrows move it a cell · the
//   range's menu (a right click, the barrel, a long press, the Menu key:
//   #range-menu through menu.js) has those and Make unique, Variation,
//   ⚄ Again and "write…" a generator. A selected cell names that menu;
//   with no range a cell keeps its own.
// song.js and songcell.js ask `synthlab.songKeys(e, grid)` before their own
// keys on the grid, so a range takes Delete and Ctrl+C / V first; with no
// range it answers false and they do what they always did.
//
// THE RANGE LOOP's pure half (`synthlab.songRange`, run under node by
// tests/studio/test_song_arr.py) is here too: song.js's schedule asks it
// where a loop over sections [first, last] goes when a section ends —
// back to the first, whose first pass after the wrap is its `?after=<last>`
// WAV (the last section's ring folded into its head: the fold every first
// pass has, from another neighbour) — and `pick()` is the range L loops:
// the sections the box spans, else the heads Shift+click marked
// (songline.js).
// Nothing here holds song state: every edit is a route.
// Budget: <= 120 code lines (tests/studio/test_island.py).
(() => {
  "use strict";
  window.synthlab ??= {};
  const S = window.synthlab;

  // ---- the range loop (pure) -------------------------------------------------
  const at = (plan, sid) => plan.sections.findIndex((s) => s.section_id === sid);
  const key = (u) => (/[?&]k=([^&]*)/.exec(u) || [])[1] || "";
  // [first, last] indices of a range loop in this plan; null: not a range
  function span(plan, loop) {
    if (!Array.isArray(loop)) return null;
    const a = at(plan, loop[0]), b = at(plan, loop[1]);
    return a < 0 || b < 0 ? null : [Math.min(a, b), Math.max(a, b)];
  }
  // the first pass of `s` as it sounds straight after section `last`
  const url = (s, last) => `${s.url.split("?")[0]}?after=${last.section_id}&k=${key(s.url)}-${key(last.url)}`;
  // section `cur.i` ended: the cursor a range loop goes to, or undefined
  // (inside the range, and one before it, the song just plays on)
  function wrap(plan, cur, loop, t) {
    const r = span(plan, loop);
    if (!r || (cur.i >= r[0] - 1 && cur.i < r[1])) return undefined;
    return { sid: plan.sections[r[0]].section_id, i: r[0], pass: 0, at: t, offset: 0, after: cur.sid };
  }
  const urls = (plan, loop) => { const r = span(plan, loop); return r ? [url(plan.sections[r[0]], plan.sections[r[1]])] : []; };
  S.songRange = { span, url, wrap, urls };
  if (typeof document === "undefined") return;

  // ---- the range --------------------------------------------------------------
  const $ = (id) => document.getElementById(id), grid = () => document.querySelector(".arranger-grid");
  const rows = () => [...document.querySelectorAll(".arranger-grid tbody tr")].map((tr) => [...tr.querySelectorAll(".arr-cell")]);
  const keyOf = (td) => `${td.dataset.sectionId}:${td.dataset.trackId}`, cellOf = (k) => $(`cell-${k.replace(":", "-")}`);
  const where = (td, m = rows()) => { const r = m.findIndex((row) => row.includes(td)); return [r, r < 0 ? -1 : m[r].indexOf(td)]; };
  const cursor = () => document.querySelector(".arr-cell.cursor");
  let sel = [], anchor = "", corner = "", clip = "", drag = null; // cell keys ("section:track")
  const menu = (td, id) => { if (td.dataset.kind) td.dataset.ctxPop = id; }; // (an audio cell has no menu to swap)
  function paint() {
    sel = sel.filter(cellOf);
    document.querySelectorAll(".arr-cell.sel").forEach((td) => { td.classList.remove("sel"); menu(td, "cell-menu"); });
    for (const td of sel.map(cellOf)) { td.classList.add("sel"); menu(td, "range-menu"); }
    $("arr-select")?.setAttribute("aria-pressed", String(!!mode()));
    const says = $("range-says"), secs = new Set(sel.map((k) => k.split(":")[0])).size;
    if (says) says.textContent = sel.length ? `${secs} × ${sel.length / secs} cells` : "";
  }
  const clear = () => { if (sel.length) { sel = []; anchor = corner = ""; paint(); } };
  // the box from the anchor to `td`
  function box(td) {
    const m = rows(), [r0, c0] = where(cellOf(anchor) || td, m), [r1, c1] = where(td, m), out = [];
    for (let r = Math.min(r0, r1); r <= Math.max(r0, r1); r++) for (let c = Math.min(c0, c1); c <= Math.max(c0, c1); c++) out.push(keyOf(m[r][c]));
    const max = Number($("song-arranger")?.dataset.maxRange) || 96;
    if (out.length > max) return void S.toast?.(`A range holds at most ${max} cells`);
    if (!anchor) anchor = keyOf(td);
    corner = keyOf(td); sel = out; paint();
  }
  const send = (op, more = {}) => S.songSend("/song/cells", { op, cells: sel.join(","), ...more });
  // the cell `d` = [rows, columns] away from `td`, or undefined
  const beside = (td, d, m = rows()) => { const [r, c] = where(td, m); return m[r + d[0]]?.[c + d[1]]; };
  const ARROWS = { arrowleft: [0, -1], arrowright: [0, 1], arrowup: [-1, 0], arrowdown: [1, 0] };
  const KEYS = { s: "arr-split", i: "arr-insert", t: "arr-trans" }; // bare keys on the grid -> the toolbar's buttons

  // keys on the focused grid; true: it was the range's (song.js, songcell.js)
  S.songKeys = (e, g) => {
    const td = g.querySelector(".arr-cell.cursor"), k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey, d = ARROWS[k];
    if (!td || e.altKey) return false;
    if (d && e.shiftKey && !mod) {
      if (!anchor) { sel = []; anchor = keyOf(td); } // (a range the server put down has no anchor: a new one, from the cursor)
      const to = beside(cellOf(corner) || td, d);
      if (to) { box(to); to.scrollIntoView({ block: "nearest", inline: "nearest" }); }
    } else if (d && mod && d[1]) S.songJump?.(d[1]);
    else if (d) { clear(); return false; }
    else if ((k === "delete" || k === "backspace") && sel.length > 1) send("clear");
    else if (mod && k === "c") { clip = sel.length > 1 ? sel.join(",") : ""; if (!clip) return false; }
    else if (mod && k === "v" && clip) S.songSend("/song/cells", { op: "copy", cells: clip, to_section: td.dataset.sectionId, to_track: td.dataset.trackId });
    else if (mod && k === "d") { if (!sel.length) box(td); send("duplicate"); }
    else if (!mod && !e.shiftKey && KEYS[k]) $(KEYS[k])?.click();
    else return false;
    e.preventDefault();
    e.stopImmediatePropagation();
    return true;
  };
  // Alt+arrows: move the range a cell (songline.js slides a block that is not in a range)
  window.addEventListener("keydown", (e) => {
    const d = ARROWS[e.key.toLowerCase()], m = rows();
    if (e.key === "Escape") clear(); // (song.js's Esc, the drawer's, still runs)
    if (!d || !e.altKey || e.ctrlKey || e.target !== grid() || sel.length < 2) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const to = sel.map((k) => beside(cellOf(k), d, m)), first = to[0];
    if (to.some((td) => !td)) return void S.toast?.("No room to move the range there");
    send("move", { to_section: first.dataset.sectionId, to_track: first.dataset.trackId });
  }, true);

  // ---- pointers: Shift+click, a drag from an empty cell, the select mode -------
  const view = () => document.querySelector(".song-view"), mode = () => view()?.classList.contains("selecting");
  document.addEventListener("pointerdown", (e) => {
    const td = e.target.closest?.(".arr-cell");
    if (!td || e.button || e.target.closest(".blk-edge, .take-chip, .long-part")) return void (td || e.button || !e.target.closest?.("#arr-scroll") || clear());
    if (e.shiftKey) { if (!sel.length) anchor = keyOf(cursor() || td); box(td); }
    else if (!mode() && td.classList.contains("filled")) return void (td.classList.contains("sel") || clear());
    else if (!td.classList.contains("sel")) clear();
    drag = { id: e.pointerId, from: td, on: e.shiftKey };
  }, true);
  document.addEventListener("pointermove", (e) => {
    const td = drag?.id === e.pointerId && document.elementFromPoint(e.clientX, e.clientY)?.closest?.(".arr-cell");
    if (!td || (!drag.on && td === drag.from)) return;
    if (!drag.on) { drag.on = true; anchor = keyOf(drag.from); }
    box(td);
  });
  for (const type of ["pointerup", "pointercancel"]) document.addEventListener(type, (e) => {
    if (!drag || (drag.id !== e.pointerId && e.pointerId !== -1)) return; // (-1: zoom.js ends a stroke for two fingers)
    if (mode() && !drag.on && type === "pointerup") { anchor = ""; box(drag.from); drag.on = true; }
    // after the click this press makes; a press that made a range leaves the keyboard on the grid
    setTimeout(() => { if (drag?.on) grid()?.focus({ preventScroll: true }); drag = null; });
  }, true);
  // a press that made a range opens no drawer
  document.addEventListener("click", (e) => {
    const op = e.target.closest?.("[data-sel-op]"), td = e.target.closest?.(".arr-cell");
    if (td && (drag?.on || e.shiftKey)) { e.preventDefault(); e.stopPropagation(); }
    if (e.target.closest?.("#arr-select")) { if (!view().classList.toggle("selecting")) clear(); paint(); }
    if (!op) return;
    if (!sel.length && cursor()) box(cursor());
    send(op.dataset.selOp);
  }, true);
  document.addEventListener("change", (e) => {
    const pick = e.target.closest?.("[data-sel-fill]");
    if (!pick?.value) return;
    if (!sel.length && cursor()) box(cursor());
    send("fill", { kind: pick.value }); // (the redraw puts the menu back on "a part…")
  });
  // the range L loops: the sections the box spans, else the marked heads
  // (`go`: it is about to loop — it starts from its first section)
  S.songRange.pick = (go) => {
    const ids = [...document.querySelectorAll(".arr-section")].map((th) => th.dataset.sectionId).filter((sid) => sel.some((k) => k.startsWith(`${sid}:`)));
    const r = ids.length > 1 ? [ids[0], ids.at(-1)] : S.songHeads?.() || null;
    if (go && r) S.songClock?.().select(r[0]);
    return r;
  };
  // a copy, a move or a duplicate says where the range is now (`song-range`): the selection goes with it
  document.addEventListener("song-range", (e) => { sel = String(e.detail?.cells || "").split(",").filter(Boolean); anchor = corner = ""; });
  // htmx's settle resets an id'd cell's class: the marks go back on
  document.addEventListener("htmx:after:settle", paint);
})();
