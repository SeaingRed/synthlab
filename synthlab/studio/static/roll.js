// synthlab/studio/static/roll.js
// The piano roll: what turns the note grid
// (grids.html `note_grid`, on Perform and in the Song drawer alike) into a
// roll. The grid stays a form of checkboxes and the engine a step grid:
// this island adds no note data. It owns
//   - the TOOL (draw / select / stamp / erase; per browser, on <html
//     data-roll>): draw and erase are audio.js's stroke (erase = its
//     `paintVal` false); select and stamp are this file's;
//   - a note's END dragged in the draw tool (the last 6 px of a note's last
//     step; 10 px under a pen, half the step under a finger), the MARQUEE
//     and the selection, a selection DRAGGED (Ctrl = copy) — each ends in
//     one post to the pattern tool route (`patternops.roll_tool`: the
//     server moves cells, cuts, velocities and slides together, one undo
//     step), never in a checkbox it ticks itself;
//   - the selection across swaps: the tool routes answer with where the
//     notes went (`roll-sel`), and every tool post carries `sel` and the
//     window (`span`, `oct`) the grid was drawn at;
//   - the KEYBOARD: one tab stop a grid (roving), arrows move the cursor,
//     Shift+arrows select, Alt+arrows move the selection (or everything:
//     the pattern's own shift / transpose), Ctrl+← → length, Ctrl+↑ ↓ the
//     step's velocity, Ctrl+C / V / D / A, Delete, Home / End, PageUp /
//     PageDown, Enter = a click, Shift+Enter = a cut;
//   - a finger's long press (the cell's menu: menu.js draws it), ghosts
//     on / off, the hover hint.
// The gesture table is docs/keys-and-midi.md, "Gestures: one owner each".
// It loads after grid.js and before pen.js.
// 260 -> 290: what the browser journeys found a first estimate of 260 had
// not counted — posts go one at a time (htmx keeps one waiting request an
// element and the answer redraws the button, so a held key lost presses),
// a key pressed while a redraw is between swap and settle still reaches
// the roll, a drag names its grid by its box (a region redrawn under the
// pointer is a new node), and three commands act at the cursor (paste,
// move, stamp) so that nothing needs a pointer.
// Budget: <= 290 code lines (tests/studio/test_island.py).
(() => {
  "use strict";
  const root = document.documentElement;
  const ROLL = ".grid.roll", CELL = `${ROLL} .gridrow:not(.sliderow) .cell`, BOX = ".transport, #song-drawer";
  const TOOLS = ["draw", "select", "stamp", "erase"];
  const tool = () => root.dataset.roll || "draw";
  const keep = (k, v) => { try { return v === undefined ? localStorage.getItem(`synthlab.roll.${k}`) : localStorage.setItem(`synthlab.roll.${k}`, v); } catch { return null; } };
  const stepOf = (c) => Number(c.name.slice(c.name.lastIndexOf(".") + 1));
  const pitchOf = (c) => c.name.slice(5, c.name.lastIndexOf("."));
  const cellAt = (g, pitch, step) => g.querySelector(`input[name="cell.${pitch}.${step}"]`);
  const pitches = (g) => [...g.querySelectorAll(".rowlabel[data-pitch]")].map((l) => l.dataset.pitch);
  const under = (e) => document.elementFromPoint(e.clientX, e.clientY)?.closest?.(CELL);
  const boxOf = (el) => el.closest(BOX);
  // the held note a lit step belongs to: [first, last] step (a cut starts a new one)
  function runOf(g, pitch, step) {
    const on = (s) => cellAt(g, pitch, s)?.checked, cut = (s) => cellAt(g, pitch, s)?.getAttribute("value") === "cut";
    let a = step, b = step;
    while (!cut(a) && on(a - 1)) a--;
    while (on(b + 1) && !cut(b + 1)) b++;
    return [a, b];
  }

  // ---- the selection: a rectangle of steps x rows, drawn on the notes in it ----
  let sel = null, mark = null, anchor = null, cur = null; // {box, s0, s1, top, low}; the copied one; Shift's fixed corner; the cursor {box, name}
  const text = (r) => (r ? `${r.s0}:${r.s1}:${r.top}:${r.low}` : "");
  const gridOf = (r) => document.getElementById(r?.box)?.querySelector(ROLL) || null;
  function paint() {
    for (const c of document.querySelectorAll(`${ROLL} .sel`)) c.classList.remove("sel");
    const g = gridOf(sel), rows = g ? pitches(g) : [];
    if (!g || !rows.includes(sel.top) || !rows.includes(sel.low)) return;
    for (const pitch of rows.slice(rows.indexOf(sel.top), rows.indexOf(sel.low) + 1)) {
      for (let s = sel.s0; s <= sel.s1; s++) {
        if (!cellAt(g, pitch, s)?.checked) continue;
        const [a, b] = runOf(g, pitch, s);
        for (let k = a; k <= b; k++) cellAt(g, pitch, k).classList.add("sel");
        s = b;
      }
    }
  }
  // select what a rectangle of cells touches (pulled in to the notes it holds; nothing = no selection)
  function pick(g, s0, s1, r0, r1) {
    const rows = pitches(g), box = boxOf(g).id;
    sel = { box, s0: Math.min(s0, s1), s1: Math.max(s0, s1), top: rows[Math.min(r0, r1)], low: rows[Math.max(r0, r1)] };
    paint();
    const lit = [...g.querySelectorAll(".cell.sel")];
    if (!lit.length) return void (sel = null);
    const steps = lit.map(stepOf), at = lit.map((c) => rows.indexOf(pitchOf(c)));
    sel = { box, s0: Math.min(...steps), s1: Math.max(...steps), top: rows[Math.min(...at)], low: rows[Math.max(...at)] };
  }
  document.addEventListener("roll-sel", (e) => { // the server: where the notes went
    if (!sel) return;
    const [s0, s1, top, low] = (e.detail?.sel || "").split(":");
    sel = top ? { box: sel.box, s0: Number(s0), s1: Number(s1), top, low } : null;
    paint();
  });

  // ---- posts: every edit is a click on a tool button, with its values --------
  // One at a time: htmx keeps one waiting request an element and drops the
  // rest, and the answer redraws the button that sent it — so keys pressed
  // faster than the server answers wait here, and each goes when the last
  // has landed (the selection it acts on is the one that answer brought).
  let busy = -1e9; // when the post in flight left (one that never answers stops counting after 8 s)
  const later = [], isTool = (el) => /\/(notes|tool)\/\w+$/.test(el?.getAttribute?.("hx-post") || "");
  function send(g, op, args) {
    if (performance.now() - busy < 8000) return void (later.length < 16 && later.push([boxOf(g).id, op, args]));
    const b = boxOf(g).querySelector(`[data-roll-op="${op}"], [hx-post$="/${op}"]`);
    if (!b || b.disabled) return;
    busy = performance.now();
    b.dataset.args = JSON.stringify(args || {});
    b.click();
  }
  document.addEventListener("htmx:finally:request", (e) => {
    if (!isTool(e.detail?.ctx?.sourceElement)) return;
    busy = -1e9;
    const [box, op, args] = later.shift() || [];
    if (gridOf({ box })) send(gridOf({ box }), op, args);
  });
  document.addEventListener("htmx:config:request", (e) => {
    const el = e.detail.ctx.sourceElement, g = el?.closest?.(BOX)?.querySelector(ROLL);
    if (!g || !isTool(el)) return;
    busy = performance.now(); // (a toolbar click too: a key pressed behind it waits its turn)
    const body = e.detail.ctx.request.body, args = JSON.parse(el.dataset.args || "{}");
    delete el.dataset.args;
    if (sel && sel.box === boxOf(g).id) body.set("sel", text(sel));
    if (el.dataset.rollOp === "dup" && sel) body.set("dx", sel.s1 - sel.s0 + 1);
    body.set("span", g.dataset.span);
    body.set("oct", g.dataset.oct);
    for (const k in args) body.set(k, args[k]);
  });
  // the three commands that act at the cursor: paste what was copied there, move the selection there, stamp a chord there
  const chord = (c) => ({ row: pitchOf(c), step: stepOf(c), q: boxOf(c).querySelector("[data-roll-opt=q]")?.value, len: boxOf(c).querySelector("[data-roll-opt=len]")?.value });
  function atCursor(g, op) {
    const c = g.querySelector(".gridrow:not(.sliderow) .cell[tabindex='0']"), rows = pitches(g), from = op === "paste" ? mark : sel;
    if (!c || op === "stamp") return c && chord(c);
    return from && rows.includes(from.top) ? { sel: text(from), dx: stepOf(c) - from.s0, dy: rows.indexOf(from.top) - rows.indexOf(pitchOf(c)) } : null;
  }

  // ---- tools, ghosts, the stamp's options: per browser ------------------------
  const opts = { q: null, len: null };
  function dress() {
    for (const b of document.querySelectorAll("[data-roll-tool]")) b.setAttribute("aria-pressed", String(b.dataset.rollTool === tool()));
    for (const b of document.querySelectorAll("[data-roll-ghosts]")) b.setAttribute("aria-pressed", String(root.dataset.ghosts !== "off"));
    for (const o of document.querySelectorAll("[data-roll-opt]")) {
      const v = opts[o.dataset.rollOpt];
      if (!v || ![...o.options].some((x) => x.value === v)) continue;
      for (const x of o.options) x.toggleAttribute("selected", x.value === v); // (audio.js's syncControls reads the attribute)
      o.value = v;
    }
    for (const g of document.querySelectorAll(`${ROLL}:not([data-rove])`)) { // one tab stop for the cells, one for the velocity lanes
      g.dataset.rove = "1";
      for (const c of g.querySelectorAll(".cell, .vel-lane")) c.tabIndex = -1;
      const home = (cur && boxOf(g).id === cur.box && g.querySelector(`[name="${cur.name}"]`)) || g.querySelector(".cell");
      if (home) home.tabIndex = 0;
      if (g.querySelector(".vel-lane")) g.querySelector(".vel-lane").tabIndex = 0;
      if (home && cur?.back && boxOf(g).id === cur.box && !g.contains(document.activeElement)) home.focus({ preventScroll: true });
    }
    for (const g of document.querySelectorAll(ROLL)) { g.style.removeProperty("--mx"); g.style.removeProperty("--my"); g.classList.remove("copying", "carrying"); }
    paint();
  }
  if (TOOLS.includes(keep("tool"))) root.dataset.roll = keep("tool");
  if (keep("ghosts") === "off") root.dataset.ghosts = "off";
  document.addEventListener("DOMContentLoaded", dress);
  document.addEventListener("htmx:after:settle", dress);
  document.addEventListener("click", (e) => {
    const t = e.target.closest?.("[data-roll-tool], [data-roll-ghosts], [data-roll-op]");
    if (!t) return;
    const g = boxOf(t)?.querySelector(ROLL), op = t.dataset.rollOp;
    if (t.dataset.rollTool) keep("tool", (root.dataset.roll = t.dataset.rollTool));
    else if (!op) keep("ghosts", (root.dataset.ghosts = root.dataset.ghosts === "off" ? "on" : "off"));
    else if (op === "mark") mark = sel && { ...sel };
    else if (g && !t.dataset.args && ["paste", "move", "stamp"].includes(op)) { // (a click made by `send` brings its own)
      const args = atCursor(g, op);
      if (!args) return void (e.preventDefault(), e.stopImmediatePropagation());
      if (op === "paste") sel ??= { ...mark, box: boxOf(g).id }; // the copy becomes the selection (`roll-sel`)
      t.dataset.args = JSON.stringify(args);
    }
    dress();
  }, true);
  document.addEventListener("input", (e) => { // the stamp's two selects sit in a form that posts on input: they are not its fields
    const o = e.target.closest?.("[data-roll-opt]");
    if (!o) return;
    opts[o.dataset.rollOpt] = o.value;
    e.stopPropagation();
  }, true);
  // hover (a mouse, a pen above the glass): the step says its pitch and number. Not a finger: it has no hover, and a tip under it eats its next tap
  document.addEventListener("pointerover", (e) => { const c = e.pointerType !== "touch" && e.target.closest?.(CELL); if (c && !c.title) c.title = c.getAttribute("aria-label"); });

  // ---- the pointer: a note's end, the marquee, a dragged selection -----------
  // (a drag names its grid by its box: a region redrawn under the pointer is a new node, and the drag goes on)
  let drag = null, eat = false;
  const live = (d) => gridOf(d);
  const grip = (e) => (e.pointerType === "touch" ? 0.5 : (e.pointerType === "pen" ? 10 : 6) / Math.max(1, e.target.offsetWidth));
  document.addEventListener("pointerdown", (e) => {
    const c = e.target.closest?.(CELL), mode = tool();
    if (!c || e.button !== 0 || !e.isPrimary) return;
    const g = c.closest(ROLL), s = stepOf(c), pitch = pitchOf(c), r = pitches(g).indexOf(pitch);
    if (mode === "draw") {
      const [a, b] = c.checked ? runOf(g, pitch, s) : [0, -1];
      if (s !== b || e.offsetX < c.offsetWidth * (1 - grip(e))) return; // not on a note's end: audio.js's stroke
      drag = { kind: "len", box: boxOf(g).id, pitch, a, b, to: b };
    } else if (mode === "select") {
      if (e.shiftKey) return void e.stopPropagation(); // adds to the selection: the click below
      const held = c.classList.contains("sel");
      const a = g.querySelector(".cell"), b = a.nextElementSibling?.matches(".cell") ? a.nextElementSibling : a, row = a.parentElement.nextElementSibling;
      drag = { kind: held ? "move" : "box", box: boxOf(g).id, s, r, to: s, tr: r, copy: e.ctrlKey, w: b.getBoundingClientRect().left - a.getBoundingClientRect().left, h: (row?.getBoundingClientRect().top ?? 0) - a.parentElement.getBoundingClientRect().top };
    } else if (mode !== "stamp") return;
    e.stopPropagation(); // not audio.js's stroke
    if (drag) try { g.setPointerCapture(e.pointerId); } catch {}
  }, true);
  document.addEventListener("pointermove", (e) => {
    const d = drag, c = d && under(e), g = d && live(d);
    if (!c || !g || c.closest(ROLL) !== g) return;
    const s = stepOf(c), r = pitches(g).indexOf(pitchOf(c));
    if (s === d.to && (d.kind === "len" || r === d.tr)) return;
    d.moved = true;
    d.to = s;
    d.tr = r;
    for (const x of g.querySelectorAll(".pre, .gone, .msel")) x.classList.remove("pre", "gone", "msel");
    if (d.kind === "len") {
      const end = Math.max(d.a, s);
      for (let k = d.a; k <= Math.max(end, d.b); k++) cellAt(g, d.pitch, k)?.classList.add(k > end ? "gone" : "pre");
    } else if (d.kind === "move") {
      g.style.setProperty("--mx", `${(s - d.s) * d.w}px`);
      g.style.setProperty("--my", `${(r - d.r) * d.h}px`);
      g.classList.toggle("copying", (d.copy = e.ctrlKey));
      // a class, never `.roll[style*="--mx"]` in the CSS: an ancestor's [style]
      // in a selector restyled every .cell on ANY inline-style write (style.css 25)
      g.classList.add("carrying");
    } else {
      const rows = pitches(g);
      for (let i = Math.min(d.r, r); i <= Math.max(d.r, r); i++) for (let k = Math.min(d.s, s); k <= Math.max(d.s, s); k++) cellAt(g, rows[i], k)?.classList.add("msel");
    }
  }, true);
  function drop(e) {
    const d = drag, g = d && live(d);
    if (!d) return;
    drag = null;
    eat = !!d.moved || d.kind !== "len"; // the click that follows is not a toggle
    setTimeout(() => { eat = false; }, 0);
    if (!g) return;
    for (const x of g.querySelectorAll(".pre, .gone, .msel")) x.classList.remove("pre", "gone", "msel");
    if (e.type === "pointercancel" || (d.kind !== "box" && !d.moved)) return void (d.kind === "move" && pick(g, d.s, d.s, d.r, d.r), dress());
    if (d.kind === "len") Math.max(d.a, d.to) !== d.b && send(g, "len", { sel: `${d.a}:${d.a}:${d.pitch}:${d.pitch}`, by: Math.max(d.a, d.to) - d.b });
    else if (d.kind === "move") send(g, d.copy ? "paste" : "move", { dx: d.to - d.s, dy: d.r - d.tr });
    else pick(g, d.s, d.to, d.r, d.tr);
    if (d.kind === "move") setTimeout(dress, 900); // (a refused move settles nothing)
  }
  document.addEventListener("pointerup", drop, true);
  document.addEventListener("pointercancel", drop, true);
  document.addEventListener("click", (e) => {
    const c = e.target.closest?.(CELL), mode = tool();
    if (!c) return;
    if (mode === "erase" && c.checked) return void e.preventDefault(); // (checked = it was off: erase never draws)
    if (!eat && mode === "draw") return;
    if (mode !== "erase") e.preventDefault();
    if (eat) return void e.stopPropagation();
    const g = c.closest(ROLL);
    e.stopPropagation(); // (grid.js's Shift-click is a cut: not in these tools)
    if (mode === "stamp") send(g, "stamp", chord(c));
    else if (mode === "select" && (e.shiftKey || e.detail === 0)) { // Shift (a pen's barrel): add this step; Space / Enter: the note here
      const rows = pitches(g), s = stepOf(c), r = rows.indexOf(pitchOf(c)), more = e.shiftKey && sel;
      // (after the dispatch: a cancelled click has the box toggled until then)
      setTimeout(() => pick(g, more ? Math.min(more.s0, s) : s, more ? Math.max(more.s1, s) : s, more ? Math.min(rows.indexOf(more.top), r) : r, more ? Math.max(rows.indexOf(more.low), r) : r), 0);
    }
  }, true);
  // a finger held on a step (grid.js hears the press): select the note under it and ask menu.js for the roll's menu
  document.addEventListener("roll-press", (e) => {
    const c = e.target.closest?.(CELL), g = c?.closest(ROLL);
    if (!g) return;
    drag = null;
    eat = true; // the finger's lift is not a toggle
    setTimeout(() => { eat = false; }, 600);
    const r = pitches(g).indexOf(pitchOf(c));
    if (c.checked) pick(g, stepOf(c), stepOf(c), r, r);
    g.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: e.detail.x, clientY: e.detail.y, button: 2 }));
  });

  // ---- the lane under the rows: velocity or chance (grids.html `chance`) -----
  // one row in the velocity lane's place, as the drum grid's lane switch; on
  // <html> so a morph of the roll keeps it (style.css 43)
  const flipLane = (to) => (to ? document.documentElement.dataset.rollLane = to : delete document.documentElement.dataset.rollLane);
  document.addEventListener("click", (e) => { const b = e.target.closest?.(".lane-flip[data-roll-lane]"); if (b) flipLane(b.dataset.rollLane); });

  // ---- the keyboard ------------------------------------------------------------
  document.addEventListener("focusin", (e) => {
    const c = e.target.closest?.(`${ROLL} .cell, ${ROLL} .vel-lane`);
    if (!c) return void (cur && (cur.back = false));
    for (const x of c.parentElement.parentElement.querySelectorAll(c.matches(".cell") ? ".cell[tabindex='0']" : ".vel-lane[tabindex='0']")) x.tabIndex = -1;
    c.tabIndex = 0;
    if (c.name) cur = { box: boxOf(c).id, name: c.name, back: true };
  });
  const ARROW = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  const NUDGE = { ArrowLeft: "shift_left", ArrowRight: "shift_right", ArrowUp: "up", ArrowDown: "down" };
  document.addEventListener("keydown", (e) => {
    let c = e.target.closest?.(`${ROLL} .cell, ${ROLL} .vel-lane`);
    // a redraw replaced the focused step and has not settled yet (a held key repeats faster than that): the key is still the roll's
    if (!c && e.target === document.body && cur?.back) (c = document.getElementById(cur.box)?.querySelector(`${ROLL} [name="${cur.name}"]`))?.focus({ preventScroll: true });
    if (!c || e.metaKey) return;
    const g = c.closest(ROLL), row = c.parentElement, lane = c.matches(".vel-lane"), line = [...row.querySelectorAll(".cell, .vel-lane")], i = line.indexOf(c);
    const ctrl = e.ctrlKey, k = e.key, go = ARROW[k], spb = Number(g.dataset.spb) || line.length, note = !lane && row.querySelector("[data-pitch]");
    const done = () => { e.preventDefault(); e.stopPropagation(); };
    const here = () => { const [a] = runOf(g, pitchOf(c), i); return { sel: `${a}:${a}:${pitchOf(c)}:${pitchOf(c)}` }; };
    const mine = sel && sel.box === boxOf(g).id;
    if (lane && (k === "PageUp" || k === "PageDown") && !ctrl) { // velocity ↔ chance: the other lane in its place, the same step
      const to = g.querySelector(k === "PageDown" ? ".chancerow" : ".velrow:not(.chancerow)");
      if (!to || to === row) return;
      done(), flipLane(k === "PageDown" ? "chance" : "");
      return void to.querySelectorAll(".vel-lane")[i]?.focus();
    }
    if (lane) return void (go && go[0] && !ctrl && !e.altKey && (done(), line[i + go[0]]?.focus())); // ← → walk the lanes; ↑ ↓ are the value (audio.js)
    if (go && e.altKey && !ctrl) return void (done(), send(g, e.shiftKey && go[1] ? `oct_${NUDGE[k]}` : NUDGE[k], {})); // the selection, else the pattern
    if (go && ctrl && note) {
      done();
      if (go[1]) return void g.querySelector(`.vel-lane[data-step="${i}"]`)?.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));
      return void (mine ? send(g, "len", { by: go[0] }) : c.checked && send(g, "len", { ...here(), by: go[0] }));
    }
    let to = null;
    if (go && !ctrl) {
      const rows = [...g.querySelectorAll(".gridrow")], r = rows.indexOf(row) + go[1];
      to = rows[r]?.querySelectorAll(".cell")[i + go[0]];
    } else if (k === "Home" || k === "End") to = line[k === "Home" ? (ctrl ? 0 : i - (i % spb)) : ctrl ? line.length - 1 : i - (i % spb) + spb - 1];
    else if ((k === "PageUp" || k === "PageDown") && note) {
      const up = k === "PageUp" ? 1 : -1, want = pitchOf(c).replace(/-?\d+$/, (o) => Number(o) + up);
      to = cellAt(g, want, i) || cellAt(g, pitches(g).at(up > 0 ? 0 : -1), i);
      if (to === c) { cur = { box: boxOf(g).id, name: `cell.${want}.${i}`, back: true }; boxOf(g).querySelector(`[data-roll-oct="${up}"]`)?.click(); }
    } else if (k === "Enter") return void (done(), e.shiftKey ? c.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, shiftKey: true })) : c.click());
    else if ((k === "Delete" || k === "Backspace") && note) return void (done(), mine ? send(g, "remove", {}) : c.checked && send(g, "remove", here()));
    else if (k === "Escape" && sel) return void (done(), (sel = anchor = null), paint());
    else if (ctrl && note && "cvda".includes(k.toLowerCase()) && !e.shiftKey && !e.altKey) {
      done();
      if (k === "c" || k === "C") mark = sel && { ...sel };
      else if (k === "a" || k === "A") pick(g, 0, line.length - 1, 0, pitches(g).length - 1);
      else if (k.toLowerCase() === "d") send(g, "dup", {});
      else if (atCursor(g, "paste")) { sel ??= { ...mark, box: boxOf(g).id }; send(g, "paste", atCursor(g, "paste")); }
      return;
    }
    if (!to) return void (go && !ctrl && done()); // an edge: the page does not scroll under the cursor
    done();
    if (e.shiftKey && go && note && to.name?.startsWith("cell.")) {
      const rows = pitches(g);
      anchor ??= { s: i, r: rows.indexOf(pitchOf(c)) };
      pick(g, anchor.s, stepOf(to), anchor.r, rows.indexOf(pitchOf(to)));
    } else anchor = null;
    to.focus();
    to.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, true);
})();
