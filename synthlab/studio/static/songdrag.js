// synthlab/studio/static/songdrag.js
// The Song island's fifth file: anything dropped on a lane of the
// playlist. All native drag and drop — a click stays a click (the browser's
// own movement threshold), Esc cancels (dragend) — and it acts only on a
// drag it saw start (`from`), never on dataTransfer text from outside.
//
//   dragged                         dropped on      posts
//   a pool pattern                  a cell          /song/cell (fills the section)
//   a block (a filled cell)         another cell    /song/cell/move; Ctrl/Alt: /song/cell
//   a clip row of the library       an audio lane   /song/section/{id}/clip
//   a placed take                   an audio lane   /song/take/edit op=move; Ctrl/Alt: the clip route
//   a whole stem's strip            an audio lane   /song/long op=set (its bar, its track)
//   a stem of the library           an audio lane   /song/long op=add
//   a loop row of the drawer        a lane of its   /loop/song (section_id, track_id, at_bar:
//     (notes or a beat)             kind            a pattern from that bar, the track's sound)
//                                   a section head  /loop/song at_section (a new section there)
//                                   an audio lane   its audio, as a clip row (when it has some)
//
// A loop from the drawer always copies (the loop stays in the store). And
// `song-ask` (tosong.py: the track cap's choice, "already in the song") is
// drawn here, for every page: a popover by the pointer, its choices posts.
//
// A DRAG MOVES; CTRL OR ALT COPIES (`copies`, the one predicate). It was the
// other way round for a week (plain = copy): on a time axis a block is an
// object at a place, and a drag that leaves a copy behind reads as a bug.
// Ctrl is the OS's own copy modifier. Touch has no modifier: a drop that
// began under a finger or a pen asks "Move here / Copy here".
//   - a pattern lands on the whole section; audio lands on the bar under
//     the pointer — a drop on a repeat's echo is that bar of the pass, a
//     stem's bar is the timeline's own — and a take or a strip keeps the
//     offset it was grabbed by;
//   - while it hovers, the lane shows `.drop-at`: an outline at the snapped
//     bar, as long as what would land; the cell gets `.drop-ok` (and
//     `.drop-copy`), a cell of the other kind `.drop-no`;
//   - the clip library is a top-layer popover over the lanes: while one of
//     its rows is dragged it turns see-through to the pointer
//     (`.dragging-out`), and is never closed (hiding a drag's source can
//     cancel the drag);
//   - Delete on a focused take removes it.
// Every post goes out from an element with no field under it: htmx.ajax
// sends every named input beneath its source, and the long-audio route
// would take the page's first `name` field for the stem's new name.
// Nothing here holds song state: every drop is a route.
// 60 -> 140: every drop on a lane, where there was one (cell onto cell).
// 140 -> 175: a loop dropped on a lane or the timeline, and the song's questions.
// Budget: <= 175 code lines.

(() => {
  "use strict";
  const S = () => window.synthlab || {};
  const $ = (id) => document.getElementById(id);
  let from = null, touch = false, shown = "", xy = [16, 16]; // from: what is in hand; shown: the preview drawn; xy: the last press
  const copies = (e) => e.ctrlKey || e.altKey;
  const AUDIO = ["clip", "take", "long", "stem"];
  const takeOf = (chip, td = chip.closest("td")) => ({ section_id: td.dataset.sectionId, track_id: td.dataset.trackId, clip: chip.dataset.clip, at_bar: chip.dataset.atBar });
  // what a dragstart puts in hand: the first selector that matches wins
  const SOURCES = [
    [".pool-pattern", (el) => ({ kind: "pattern", pid: el.dataset.patternId, pkind: el.dataset.kind })],
    [".take-chip", (el) => ({ kind: "take", ...takeOf(el), bars: +el.dataset.bars })],
    [".long-part", (el) => ({ kind: "long", id: el.dataset.long, bars: +el.dataset.bars, skip: +el.dataset.skip })],
    [".arr-cell.filled", (el) => ({ kind: "cell", id: el.id, sid: el.dataset.sectionId, tid: el.dataset.trackId, pid: el.dataset.patternId, pkind: el.dataset.kind })],
    [".lp-row[data-drag-loop]", (el) => ({ kind: "loop", ref: JSON.parse(el.dataset.dragLoop), what: el.dataset.what, clip: el.dataset.name || "", bars: +el.dataset.bars || 1 })],
    [".cd-clip", (el) => ({ kind: "clip", clip: el.dataset.name, bars: +el.dataset.bars })],
    [".cd-stem", (el) => ({ kind: "stem", ...el.dataset })],
  ];
  const post = (path, values) => S().post(path, values);

  // What a drop at `e` would do: {td, ok, copy, ask, at, len, send(copy)} —
  // the lane cell, whether it takes this, the preview (bars from the cell's
  // left edge), and the post. null: not a target.
  function plan(e, f = from) {
    if (f.kind === "loop") { // a section's head: a new section before or after it; an audio lane: its audio
      const head = e.target.closest?.(".arr-section, .arr-add-col");
      if (head) return timeline(e, f, head);
      if (e.target.closest?.(".arr-takes")) return f.clip ? plan(e, { ...f, kind: "clip" }) : null;
    }
    const td = e.target.closest?.(AUDIO.includes(f.kind) ? ".arr-takes" : ".arr-cell[data-kind]");
    const th = td && $(`sec-${td.dataset.sectionId}`);
    if (!th || td.id === f.id) return null;
    const sid = td.dataset.sectionId, here = { section_id: sid, track_id: td.dataset.trackId || "" };
    const bars = +th.dataset.bars, span = bars * +th.dataset.repeats, start = +th.dataset.startBar - 1, r = td.getBoundingClientRect();
    const raw = Math.min(span - 1, Math.max(0, Math.floor((e.clientX - r.left) / (r.width / span)))); // bars into the cell
    const bar = Math.max(0, raw % bars - (f.kind === "take" ? f.grab : 0)), pass = raw - raw % bars; // the bar of its pass
    const abs = Math.max(0, start + raw - (f.kind === "long" ? f.grab : 0)); // the timeline's bar
    const lib = { q: $("clip-q")?.value || "", place_section: sid }; // the drawer keeps its search when it is redrawn
    const place = [`/song/section/${encodeURIComponent(sid)}/clip`, { ...here, ...lib, clip: f.clip, at_bar: bar }];
    const fits = { at: pass + bar, len: Math.min(f.bars || 1, bars - bar) };
    if (f.kind === "pattern") return { td, ok: td.dataset.kind === f.pkind, copy: true, at: 0, len: bars, send: () => ["/song/cell", { ...here, pattern_id: f.pid }] };
    if (f.kind === "cell") {
      return { td, ok: td.dataset.kind === f.pkind, copy: copies(e), ask: true, at: 0, len: bars,
        send: (copy) => (copy ? ["/song/cell", { ...here, pattern_id: f.pid }] : ["/song/cell/move", { ...here, from_section: f.sid, from_track: f.tid }]) };
    }
    if (f.kind === "loop") {
      const ok = (td.dataset.kind === "drums") === (f.what === "beat");
      return { td, ok, no: f.what === "beat" ? "A beat goes on the drums lane" : "Notes go on a synth lane", copy: true, at: pass + bar, len: bars - bar,
        send: () => ["/loop/song", { ...f.ref, ...here, at_bar: bar }] };
    }
    if (f.kind === "clip") return { td, ok: true, copy: true, ...fits, send: () => place };
    if (f.kind === "take") {
      const move = { op: "move", section_id: f.section_id, track_id: f.track_id, clip: f.clip, at_bar: f.at_bar, to_section: sid, to_track: here.track_id, to_bar: bar };
      return { td, ok: true, copy: copies(e), ask: true, ...fits, send: (copy) => (copy ? place : ["/song/take/edit", move]) };
    }
    if (f.kind === "long") return { td, ok: true, at: abs - start, len: f.bars, send: () => ["/song/long", { op: "set", id: f.id, bar: abs + 1, track_id: here.track_id }] };
    const add = { op: "add", source: f.source, model: f.model, stems: f.stem, bar: abs + 1, track_id: here.track_id, tempo: $("long-tempo")?.checked ? "on" : "", ...lib };
    return { td, ok: true, copy: true, at: raw, len: span - raw, send: () => ["/song/long", add] };
  }
  // a loop over a section's head (or the + column): a new section at that place in the running order
  function timeline(e, f, head) {
    const end = head.matches(".arr-add-col"), r = head.getBoundingClientRect(), after = !end && e.clientX > r.left + r.width / 2;
    const at = end ? document.querySelectorAll(".arr-section").length : +head.dataset.index + (after ? 1 : 0);
    return { td: head, ok: true, copy: true, edge: after ? "drop-after" : "drop-before", send: () => ["/loop/song", { ...f.ref, at_section: at }] };
  }
  // the marks of a drop that would land (p), or none
  function show(p) {
    const key = p ? `${p.td.id}|${p.td.dataset.sectionId}|${p.ok}|${p.copy}|${p.at}|${p.len}|${p.edge}` : "";
    if (key === shown) return;
    shown = key;
    document.querySelectorAll(".drop-ok, .drop-copy, .drop-no, .drop-before, .drop-after").forEach((c) => c.classList.remove("drop-ok", "drop-copy", "drop-no", "drop-before", "drop-after"));
    document.querySelector(".drop-at")?.remove();
    if (p?.edge) return void p.td.classList.add(p.edge);
    if (!p) return;
    p.td.classList.add(p.ok ? "drop-ok" : "drop-no");
    p.td.classList.toggle("drop-copy", p.ok && !!p.copy);
    if (!p.ok) return;
    const m = Object.assign(p.td.appendChild(document.createElement("i")), { className: "drop-at" });
    m.style.setProperty("--at", p.at);
    m.style.setProperty("--len", p.len);
  }
  // the touch drop's choice: a popover, so Esc and a tap outside dismiss it
  function ask(p) {
    const box = Object.assign(document.body.appendChild(document.createElement("div")), { className: "cell-drop", popover: "auto" });
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Move or copy it here");
    for (const [label, copy] of [["Move here", false], ["Copy here", true]]) {
      const b = Object.assign(box.appendChild(document.createElement("button")), { type: "button", className: "btn btn-s menu-btn", textContent: label });
      b.addEventListener("click", () => { box.remove(); post(...p.send(copy)); });
    }
    box.addEventListener("toggle", (e) => { if (e.newState === "closed") box.remove(); });
    box.showPopover();
    box.querySelector("button").focus();
  }

  document.addEventListener("pointerdown", (e) => { touch = e.pointerType !== "mouse"; xy = [e.clientX, e.clientY]; }, true);
  // a song question (`song-ask`, tosong.py): its message over its choices, by the last press or drop
  document.addEventListener("song-ask", (e) => {
    const { message = "", choices = [] } = e.detail || {};
    document.querySelector(".song-ask")?.remove();
    const box = Object.assign(document.body.appendChild(document.createElement("div")), { className: "cell-drop song-ask", popover: "auto" });
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", message);
    box.appendChild(document.createElement("p")).textContent = message;
    const list = Object.assign(box.appendChild(document.createElement("div")), { className: "ask-choices" });
    for (const c of choices) {
      const b = Object.assign(list.appendChild(document.createElement(c.href ? "a" : "button")), { className: "btn btn-s menu-btn", textContent: c.label, title: c.title || "" });
      if (c.href) b.href = c.href;
      else { b.type = "button"; b.addEventListener("click", () => { box.hidePopover(); post(c.path, c.vals); }); }
    }
    box.addEventListener("toggle", (t) => { if (t.newState === "closed") box.remove(); });
    box.showPopover();
    const r = box.getBoundingClientRect();
    box.style.left = `${Math.max(16, Math.min(xy[0], innerWidth - r.width - 16))}px`;
    box.style.top = `${Math.max(16, Math.min(xy[1] + 8, innerHeight - r.height - 16))}px`;
    box.querySelector("button, a")?.focus();
  });
  document.addEventListener("dragstart", (e) => {
    const hit = SOURCES.find(([sel]) => e.target.closest?.(sel)), el = hit && e.target.closest(hit[0]);
    if (!el?.draggable || document.querySelector(".song-view.edging")) return; // an edge in hand is songline.js's
    if (e.shiftKey || document.querySelector(".song-view.selecting")) return void e.preventDefault(); // a range is being made: songsel.js's
    from = hit[1](el);
    // a take or a strip keeps the bar it was grabbed by
    const barW = parseFloat(getComputedStyle(el).getPropertyValue("--bar-w")) || 24;
    from.grab = Math.max(0, Math.floor((e.clientX - el.getBoundingClientRect().left) / barW)) + (from.skip || 0);
    e.dataTransfer.setData("text/plain", from.pid || from.clip || from.id || from.stem || "");
    e.dataTransfer.effectAllowed = "all"; // Alt asks the OS for a link: allow it, the drop decides
    el.classList.add("dragging");
    document.querySelector(".song-view")?.classList.toggle("dragging-audio", AUDIO.includes(from.kind));
    // after the drag image is taken: the drawer lets the lanes under it take the drop
    if (el.closest("#clip-library")) requestAnimationFrame(() => { if (from) $("clip-library").classList.add("dragging-out"); });
  });
  document.addEventListener("dragover", (e) => {
    const p = from && plan(e);
    show(p);
    if (!p) return;
    e.preventDefault(); // every lane cell takes the drop: the wrong kind answers with a toast
    e.dataTransfer.dropEffect = p.copy ? "copy" : "move";
  });
  document.addEventListener("drop", (e) => {
    const p = from && plan(e), f = from;
    if (!p) return;
    e.preventDefault();
    xy = [e.clientX, e.clientY];
    if (!p.ok) S().toast?.(p.no || `A ${f.pkind} pattern can't go on this track`);
    else if (touch && p.ask) ask(p);
    else post(...p.send(p.copy));
  });
  document.addEventListener("dragend", () => {
    from = null;
    show(null);
    document.querySelectorAll(".dragging, .dragging-out, .dragging-audio").forEach((x) => x.classList.remove("dragging", "dragging-out", "dragging-audio"));
  });
  // the keyboard's way to remove a take (its menu has Remove and the bar field)
  document.addEventListener("keydown", (e) => {
    const chip = (e.key === "Delete" || e.key === "Backspace") && e.target.closest?.(".take-name")?.parentElement;
    if (!chip || e.repeat) return;
    e.preventDefault();
    post("/song/take/edit", { op: "remove", ...takeOf(chip) });
    // the redraw takes the chip (and the keyboard) with it: back to the
    // grid, once song.js's queued sync has made the new one a tab stop
    const regrid = () => { if (document.activeElement === document.body) document.querySelector(".arranger-grid")?.focus({ preventScroll: true }); };
    const later = () => setTimeout(regrid); // every settle for two seconds: another swap's may land first
    document.addEventListener("htmx:after:settle", later);
    setTimeout(() => document.removeEventListener("htmx:after:settle", later), 2000);
  });
})();
