// synthlab/studio/static/steps.js
// The step sequencer (docs: drums.md "The step
// sequencer", keys-and-midi.md "Gestures: one owner each"). The grid stays
// the `drum_grid` macro's form (drumgrid.html), on Drums, in Perform's rack
// and in the Song drawer alike; audio.js still paints its steps and drags
// its lane, grid.js still erases on a right click, pen.js still writes
// pressure into "the lane under the grid". This island owns what a HIT is:
//   - the per-hit table: `hits` (a hidden JSON field of the grid form,
//     {"<voice>.<step>": {v, p, r, n, t}}) is the one copy on the page.
//     Every edit here writes it and then lets the grid's own `input` post
//     carry it — one post, one undo step, whatever made the edit;
//   - the selected row (a press on its name, or ↑ ↓ in the grid) and the
//     lane switch: the lane under the grid then shows THAT row's hits in
//     one field (velocity / chance / ratchet / nudge / tune) instead of
//     the steps' shared velocity. The bars are the same `.vel-lane`s —
//     audio.js's drag and pen.js's pressure write them as they always did,
//     and this island reads what changed back into the table;
//   - the cursor: the grid is one Tab stop (roving tabindex over the
//     steps, the lane and the cursor row's head). Arrows move; Space /
//     Enter toggle; 1-4 the ratchet; + / − (or Alt+↑ ↓) the lane's value;
//     Alt+← → nudge; PageUp / PageDown the lane; Delete the hit's data;
//     Alt+1-4 the beat's variation; the Menu key the hit menu;
//   - the wheel, on the FOCUSED step only: the lane's value, a notch;
//   - the inspector's knobs and the row heads' (unnamed ranges: tweak.js
//     drags, wheels, types and resets them, a deck dial turns them): the
//     selected row's level and swing, the cursor hit's five values;
//   - the hit menu (a `.pop.menu` the macro draws): its rows, and opening
//     it from a long press or the Menu key on a hit (a right click on a
//     step is still grid.js's erase; on a lane bar it is menu.js's);
//   - the step LEDs' marks (`data-r`, `data-p`, `data-n`, `data-t`,
//     `--hv`), kept in step with the table.
// The stub declared 80 lines for "the selected row, the lane switch,
// Alt+arrows". Raised to 270 with the code (257 used): the keyboard grid,
// the wheel, the inspector and row knobs, the hit menu and the marks are
// the input matrix's whole step-sequencer row, and they share one table.
// 270 -> 295: Kit's stacked lanes — the four hit lanes drawn
// under the velocity's at once, each read back into its own field.
// Budget: <= 295 code lines (tests/studio/test_island.py).
(() => {
  "use strict";
  const S = () => window.synthlab || {};
  const GRID = ".drum-grid-8", CELL = `${GRID} .drumrow .cell`, LANES = ["v", "p", "r", "n", "t"];
  // field: neutral (not stored), min, max, a notch, the lane's levels
  const F = { v: [null, 0.05, 1, 0.05, 20], p: [1, 0, 1, 0.05, 20], r: [1, 1, 4, 1, 3], n: [0, -0.5, 0.5, 0.05, 20], t: [0, -12, 12, 1, 24] };
  const NAME = { v: "vel", p: "chance", r: "ratchet", n: "nudge", t: "tune" };
  const ui = new Map(); // form id -> { row, lane, voice, step }: kept across the region's redraws
  const formOf = (el) => el?.closest?.("form.drum-grid") || null;
  const st = (form) => ui.get(form.id) || ui.set(form.id, { row: "", lane: "v", voice: "", step: 0 }).get(form.id);
  const snap = (f, x) => Math.round(Math.min(F[f][2], Math.max(F[f][1], x)) / F[f][3]) * F[f][3];
  const tidy = (x) => Math.round(x * 100) / 100;
  // a lane bar's height for a value, and back (a velocity is its own height)
  const norm = (f, x) => (f === "v" ? x : (x - F[f][1]) / (F[f][2] - F[f][1]));
  const denorm = (f, n) => (f === "v" ? n : F[f][1] + n * (F[f][2] - F[f][1]));
  const fire = (el) => el.dispatchEvent(new Event("input", { bubbles: true }));
  function table(form, name = "hits") {
    try { return JSON.parse(form.elements[name]?.value || "{}") || {}; } catch { return {}; }
  }
  const keep = (form, name, t) => { if (form.elements[name]) form.elements[name].value = JSON.stringify(t); };
  const cellOf = (form, voice, step) => form.elements[`drum.${voice}.${step}`] || null;
  const whereOf = (cell) => { const [, voice, step] = cell.name.split("."); return [voice, Number(step)]; };
  const laneAt = (form, step) => form.querySelector(`.vel-lane[data-step="${step}"]`);
  // Kit's stacked lanes (drumgrid.html `lanes`), drawn where the page has the
  // height: the velocity row is then always the velocity, the lane keys
  // (+ −, PageUp / PageDown) name the lit one; they show the selected row's
  // hits, else the cursor's row's, else the first row with a hit
  const stacked = (form) => !!form.querySelector(".hitrow")?.checkVisibility();
  const laneOf = (form) => (stacked(form) ? "v" : st(form).lane);
  const who = (form) => { const s = st(form); return s.row || s.voice || form.querySelector(`${CELL}:checked`)?.name.split(".")[1] || ""; };
  const stepVel = (form, step) => { const i = laneAt(form, step)?.querySelector("input"); return Number(i?.dataset.vel ?? i?.value ?? 1); };

  // ---- the table ---------------------------------------------------------------
  function mark(cell, h) {
    if (!cell) return;
    const d = cell.dataset;
    for (const [k, v] of [["r", h?.r > 1 && h.r], ["p", h && "p" in h && Math.round(h.p * 100)], ["n", h?.n && (h.n < 0 ? "-" : "+")], ["t", h?.t && (h.t < 0 ? "-" : "+")]]) {
      if (v === false || v == null || v === 0 && k !== "p") delete d[k]; else d[k] = v;
    }
    if (h && "v" in h) cell.style.setProperty("--hv", h.v); else cell.style.removeProperty("--hv");
  }
  const get = (form, voice, step, f) => { const h = table(form)[`${voice}.${step}`]; return h && f in h ? h[f] : f === "v" ? stepVel(form, step) : F[f][0]; };
  // one field of one hit (x null: forget it); a value that says nothing is not kept
  function set(form, voice, step, f, x) {
    const t = table(form), k = `${voice}.${step}`, h = t[k] || {};
    if (f === "*") for (const key of Object.keys(h)) delete h[key];
    else if (x == null || tidy(snap(f, x)) === F[f][0]) delete h[f];
    else h[f] = tidy(snap(f, x));
    if (Object.keys(h).length) t[k] = h; else delete t[k];
    keep(form, "hits", t);
    mark(cellOf(form, voice, step), t[k]);
  }
  const bump = (form, voice, step, f, by) => set(form, voice, step, f, get(form, voice, step, f) + by * F[f][3]);
  // a hit's data goes with its cell (the server drops it too: this is so a
  // step turned on again does not find the old hit's values waiting)
  function prune(form) {
    const t = table(form);
    let cut = false;
    for (const k of Object.keys(t)) {
      const [voice, step] = k.split(".");
      if (!cellOf(form, voice, step)?.checked) { delete t[k]; cut = true; }
    }
    if (cut) keep(form, "hits", t);
    for (const c of form.querySelectorAll(`${CELL}:not(:checked):is([data-r], [data-p], [data-n], [data-t], [style])`)) mark(c);
  }

  // ---- the lane, the inspector, the cursor -------------------------------------
  function show(lane, n) {
    lane.querySelector(".vel-bar").style.setProperty("--v", n); // (a scale: style.css 8)
    lane.setAttribute("aria-valuenow", String(tidy(n)));
  }
  function put(el, v, on = true) {
    if (!el) return;
    el.disabled = !on;
    el.value = v;
    el.style.setProperty("--fill", `${((el.value - el.min) / (el.max - el.min || 1)) * 100}%`);
    const out = el.parentElement.querySelector("output");
    if (out) out.textContent = out.hasAttribute("data-pct") ? `${Math.round(el.value * 100)}%` : String(tidy(Number(el.value)));
  }
  function draw(form) {
    const s = st(form), row = form.querySelector(".velrow"), sw = table(form, "rswing"), f0 = laneOf(form);
    if (!row) return;
    form.dataset.row = s.row;
    row.dataset.lane = s.row ? f0 : "v";
    row.querySelector(".lane-name").textContent = s.row ? NAME[f0] : "vel";
    for (const lane of row.querySelectorAll(".vel-lane")) {
      const step = Number(lane.dataset.step), input = lane.querySelector("input");
      if (s.row) {
        input.dataset.vel ??= input.value; // the step's own velocity waits here
        input.disabled = true;
        show(lane, norm(f0, get(form, s.row, step, f0)));
        input.value = lane.getAttribute("aria-valuenow");
        lane.dataset.q = F[f0][4];
        lane.classList.toggle("idle", !cellOf(form, s.row, step)?.checked);
      } else if (input.dataset.vel !== undefined) {
        input.value = input.dataset.vel;
        delete input.dataset.vel; delete lane.dataset.q;
        input.disabled = false;
        show(lane, Number(input.value));
        lane.classList.toggle("idle", !form.querySelector(`${CELL}[name$=".${step}"]:checked`));
      }
      lane.dataset.was = lane.getAttribute("aria-valuenow");
      lane.setAttribute("aria-label", `${s.row ? `${s.row} ${NAME[f0]}` : "drum velocity"} step ${step + 1}`);
    }
    const hv = who(form);
    for (const hr of form.querySelectorAll(".hitrow")) {
      const f = hr.dataset.lane;
      hr.toggleAttribute("data-on", f === s.lane);
      hr.querySelector(".lane-who").textContent = hv;
      for (const lane of hr.querySelectorAll(".vel-lane")) {
        const on = !!cellOf(form, hv, lane.dataset.step)?.checked;
        show(lane, norm(f, on ? get(form, hv, Number(lane.dataset.step), f) : F[f][0] ?? 1));
        lane.dataset.q = F[f][4];
        lane.dataset.was = lane.getAttribute("aria-valuenow");
        lane.classList.toggle("idle", !on);
      }
    }
    for (const b of form.querySelectorAll(".lane-pick [data-lane]")) b.setAttribute("aria-pressed", String(b.dataset.lane === row.dataset.lane));
    for (const b of form.querySelectorAll(".rowlabel[data-voice]")) b.setAttribute("aria-pressed", String(b.dataset.voice === s.row));
    const of = form.querySelector(".lane-of");
    if (of) of.textContent = s.row || "every row";
    // the knobs: the cursor row's (the inspector) and each row's own; the hit under the cursor
    const voice = s.row || s.voice, cell = cellOf(form, s.voice, s.step), hit = !!cell?.checked;
    const strip = (v) => document.querySelector(`#drumkit [name="${v}.level"]`);
    for (const k of form.querySelectorAll("[data-row]")) {
      const v = k.closest(".drumrow")?.dataset.voice ?? voice, own = v in sw;
      if (k.dataset.row === "swing") { k.classList.toggle("own", own); put(k, own ? sw[v] : k.dataset.default, !!v); }
      else put(k, table(form, "rlevel")[v] ?? strip(v)?.value ?? k.getAttribute("value"), !!v);
    }
    for (const k of form.querySelectorAll("[data-hit]")) put(k, hit ? get(form, s.voice, s.step, k.dataset.hit) : k.dataset.default, hit);
    const at = form.querySelector(".hit-at");
    if (at) at.textContent = s.voice ? `${s.voice} ${s.step + 1}${hit ? "" : " · no hit"}` : "no hit";
  }
  // roving tabindex: the cursor's step, its lane bar and its row's head take the keyboard
  function rove(form) {
    const s = st(form), cur = cellOf(form, s.voice, s.step) || form.querySelector(CELL);
    if (!cur) return;
    for (const el of form.querySelectorAll('.drumrow [tabindex="0"], .vel-lane[tabindex="0"]')) el.tabIndex = -1;
    cur.tabIndex = 0;
    for (const el of cur.closest(".drumrow").querySelectorAll(".rowhead :is(button, summary, input)")) el.tabIndex = 0;
    const lane = laneAt(form, whereOf(cur)[1]);
    if (lane) lane.tabIndex = 0;
  }
  function cursor(form, cell, row = false) {
    const s = st(form), [voice, step] = whereOf(cell);
    Object.assign(s, { voice, step });
    if (row || (s.row && s.row !== voice)) s.row = voice; // a selection follows the hand
    rove(form);
    draw(form);
  }
  function init() {
    for (const form of document.querySelectorAll("form.drum-grid")) { rove(form); draw(form); }
  }
  document.addEventListener("DOMContentLoaded", init);
  document.addEventListener("htmx:after:settle", init);
  for (const ev of ["resize", "synthlab-geometry"]) addEventListener(ev, init); // (the stacked lanes come and go with the window's height)

  // ---- edits: whatever wrote the lane or a knob, the table follows -----------------
  document.addEventListener("input", (e) => {
    const t = e.target, form = formOf(t);
    if (t.closest?.(".euclid")) return void e.stopPropagation(); // Euclid's numbers are its own form's
    // the kit strip's level and the kit's swing reach the rows' knobs
    const kit = /^(\w+)\.level$/.exec(t.closest?.("#drumkit") ? t.name : "");
    if (kit || t.matches?.("#drum-transport [name=swing]")) {
      for (const f of document.querySelectorAll("form.drum-grid")) {
        if (!kit) for (const k of f.querySelectorAll('[data-row="swing"]')) k.dataset.default = t.value;
        draw(f);
      }
      return;
    }
    if (!form) return;
    const s = st(form), voice = t.closest?.(".drumrow")?.dataset.voice ?? (s.row || s.voice);
    if (t.dataset?.hit) set(form, s.voice, s.step, t.dataset.hit, Number(t.value));
    else if (t.dataset?.row === "swing") {
      const sw = table(form, "rswing");
      if (Number(t.value) === Number(t.dataset.default)) delete sw[voice]; else sw[voice] = Number(t.value);
      keep(form, "rswing", sw);
    } else if (t.dataset?.row === "level") {
      const strip = document.querySelector(`#drumkit [name="${voice}.level"]`);
      if (strip) { e.stopPropagation(); S().tweak?.(strip, Number(t.value)); S().tweak?.later?.(strip); } // the kit's own knob posts it
      else keep(form, "rlevel", { ...table(form, "rlevel"), [voice]: Number(t.value) });
    } else if (t.closest?.(".hitrow")) { // a stacked lane: its own field, of the row it shows
      const hr = t.closest(".hitrow"), f = hr.dataset.lane, hv = who(form);
      for (const lane of hr.querySelectorAll(".vel-lane")) {
        const step = Number(lane.dataset.step), now = lane.getAttribute("aria-valuenow");
        if (now !== lane.dataset.was && cellOf(form, hv, step)?.checked) set(form, hv, step, f, denorm(f, Number(now)));
      }
    } else if (s.row && t.closest?.(".velrow, .grid")) {
      // the lane was dragged, keyed or written by a pen: each bar that moved, into its hit
      const f0 = laneOf(form);
      for (const lane of form.querySelectorAll(".velrow:not(.hitrow) .vel-lane")) {
        const step = Number(lane.dataset.step), now = lane.getAttribute("aria-valuenow");
        if (now !== lane.dataset.was && cellOf(form, s.row, step)?.checked) set(form, s.row, step, f0, denorm(f0, Number(now)));
      }
    }
    prune(form);
    draw(form);
  }, true);
  // a step or a row's name under the pointer, or reached by Tab
  for (const type of ["pointerdown", "focusin"]) {
    document.addEventListener(type, (e) => {
      const cell = e.target.closest?.(CELL), form = formOf(e.target);
      // a right press on a lane bar is its menu (menu.js), never a stroke of audio.js's lane paint
      if (e.button === 2 && e.target.closest?.(`${GRID} .vel-lane`)) return void e.stopPropagation();
      if (cell && form) cursor(form, cell);
    }, true);
  }
  document.addEventListener("click", (e) => {
    const form = formOf(e.target);
    if (!form) return;
    const s = st(form), name = e.target.closest(".rowlabel[data-voice]"), pick = e.target.closest(".lane-pick [data-lane]"), item = e.target.closest("[data-hit-set]");
    if (name) { s.row = s.row === name.dataset.voice ? "" : name.dataset.voice; s.voice ||= s.row; }
    else if (e.target.closest("[data-row-all]")) s.row = "";
    else if (pick) { // a lane other than velocity is a row's: the cursor's, else the first with a hit
      s.lane = pick.dataset.lane;
      if (s.lane !== "v") s.row ||= s.voice || form.querySelector(`${CELL}:checked`)?.name.split(".")[1] || "kick";
    }
    else if (item) return void menuRow(form, item);
    else return;
    rove(form);
    draw(form);
  });

  // ---- the hit menu ----------------------------------------------------------------
  const menuOf = (form) => document.getElementById(`hitmenu-${form.id}`);
  function openMenu(form, cell) {
    const m = menuOf(form), [voice, step] = whereOf(cell);
    if (!m) return;
    m.querySelector("[data-from]").value = "";
    m.querySelector(".hit-menu-title").textContent = `${voice} · step ${step + 1}`;
    m.showPopover({ source: cell });
    m.querySelector(".menu-item")?.focus({ preventScroll: true });
  }
  function menuRow(form, item) {
    const s = st(form), m = menuOf(form), from = m.querySelector("[data-from]").value, what = item.dataset.hitSet;
    // opened on a lane bar (menu.js): that step of the lane's row; else the cursor's hit
    const step = from === "" ? s.step : Number(from);
    const voice = (from !== "" && s.row) || s.voice || s.row || form.querySelector(`${CELL}[name$=".${step}"]:checked`)?.name.split(".")[1];
    m.hidePopover();
    const cell = voice && cellOf(form, voice, step);
    if (!cell?.checked) return;
    if (what === "clear") set(form, voice, step, "*");
    else if (what === "n-" || what === "n+") bump(form, voice, step, "n", what === "n-" ? -5 : 5);
    else set(form, voice, step, what[0], Number(what.slice(2)));
    cell.focus({ preventScroll: true });
    fire(cell);
  }
  // a long press (touch) or the Menu key on a hit: its menu. A mouse's right
  // click stays an erase (grid.js); a pen's is cancelled before this (pen.js)
  document.addEventListener("contextmenu", (e) => {
    const cell = e.target.closest?.(CELL), form = formOf(cell);
    const keyed = e.button !== 2 && cell === document.activeElement;
    if (!cell?.checked || !form || !(keyed || e.pointerType === "touch")) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    cursor(form, cell);
    openMenu(form, cell);
  }, true);

  // ---- the keyboard on a step, and on a hit lane ---------------------------------
  window.addEventListener("keydown", (e) => {
    const t = e.target, form = formOf(t), cell = t.closest?.(CELL), lane = t.closest?.(".vel-lane");
    const take = () => { e.preventDefault(); e.stopImmediatePropagation(); };
    if (e.ctrlKey || e.metaKey) return;
    // Alt+1-4: the beat's variation, from anywhere in the sequencer's pane (its tools too)
    if (e.altKey && !e.shiftKey && /^Digit[1-4]$/.test(e.code)) { // (Alt+Shift+digits: the views)
      const b = t.closest?.(".drum-bank, .perform-drums, .song-drawer")?.querySelector(`.var-pick [data-var="${"ABCD"[e.code[5] - 1]}"]`);
      if (b && !b.disabled) { take(); b.click(); }
      return;
    }
    if (!form) return;
    const s = st(form);
    const by = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1 }[e.key];
    const hr = lane?.closest(".hitrow"), lf = hr ? hr.dataset.lane : laneOf(form), lv = hr ? who(form) : s.row;
    if (lane && lv && !e.altKey && !e.shiftKey && (by || e.key === "Home" || e.key === "End")) {
      const step = Number(lane.dataset.step); // a hit lane's keys: one notch of ITS field (audio.js's are the shared velocity's)
      take();
      if (!cellOf(form, lv, step)?.checked) return;
      if (by) bump(form, lv, step, lf, by); else set(form, lv, step, lf, F[lf][e.key === "Home" ? 1 : 2]);
      return void fire(lane);
    }
    if (!cell) return;
    const [voice, step] = whereOf(cell), rows = [...form.querySelectorAll(".drumrow")].map((r) => r.dataset.voice);
    const edit = (f, n) => { if (!cell.checked) return; s.row ||= voice; bump(form, voice, step, f, n); fire(cell); };
    if (e.altKey && by) { take(); return void edit(e.key === "ArrowUp" || e.key === "ArrowDown" ? s.lane : "n", by); }
    if (e.altKey || (by && e.shiftKey)) return; // (Shift+arrows are grid.js's pattern tools)
    if (by) {
      const up = e.key === "ArrowUp" || e.key === "ArrowDown", row = rows.indexOf(voice) - (up ? by : 0);
      const to = cellOf(form, rows[Math.min(rows.length - 1, Math.max(0, row))], step + (up ? 0 : by));
      take();
      if (to) { cursor(form, to, up); to.focus(); }
    } else if (e.key === "Enter") { take(); cell.checked = !cell.checked; fire(cell); }
    else if (e.key === "PageUp" || e.key === "PageDown") {
      take();
      s.row ||= voice;
      s.lane = LANES[(LANES.indexOf(s.lane) + (e.key === "PageDown" ? 1 : 4)) % 5];
      draw(form);
    } else if (/^[1-4]$/.test(e.key)) { take(); if (cell.checked) { set(form, voice, step, "r", Number(e.key)); fire(cell); } }
    else if (e.key === "+" || e.key === "=" || e.key === "-") { take(); edit(s.lane, e.key === "-" ? -1 : 1); }
    else if (e.key === "Delete" || e.key === "Backspace") { take(); if (cell.checked) { set(form, voice, step, "*"); fire(cell); } }
  }, true);

  // ---- the wheel: the focused step's value in the lane, a notch -------------------
  document.addEventListener("wheel", (e) => {
    const cell = e.target.closest?.(CELL), form = formOf(cell);
    if (!form || cell !== document.activeElement || !cell.checked || e.ctrlKey || e.shiftKey || !e.deltaY) return;
    e.preventDefault();
    const s = st(form), [voice, step] = whereOf(cell);
    s.row ||= voice;
    bump(form, voice, step, s.lane, e.deltaY < 0 ? 1 : -1);
    fire(cell);
  }, { passive: false });
})();
