// synthlab/studio/static/pen.js
// The pen as an instrument (Pointer Events, pointerType "pen"). It adds no
// surface of its own: it translates what a pen can say into what the
// islands already understand, and does nothing for a mouse or a finger.
//   - Step grids (notes, drums, the Song drawer's): PRESSURE is the step's
//     velocity. The stroke's peak pressure over a step it turns on is
//     written to the velocity lane under the grid (0.25 + pressure, the
//     piano's own law, on the lane's 0.05 grid) just before the grid's own
//     post leaves — the same bar, hidden field and aria value audio.js
//     writes. The ERASER end erases: a tap here, a drag in audio.js's
//     stroke (button 5). The BARREL button is Shift: a barrel tap is a
//     Shift-click (a cut, where the grid has cuts), and a barrel drag
//     paints as a plain one (audio.js: only a mouse's right-drag erases).
//   - VALUE CONTROLS (sliders, knobs, selects) are not this file's: the
//     fine drag, eraser = default, the barrel's menu and the hover value
//     are tweak.js's, as is detecting the pen (`html[data-pen]`); a
//     slider's `touch-action: none` is style.css's. One owner per gesture:
//     the table is docs/keys-and-midi.md, "Gestures: one owner each".
//   - Every pen surface: no page scroll under a pen (touch-action is set
//     to none while a pen hovers — hover comes before contact — and handed
//     back when a finger or mouse arrives), and no press-and-hold or
//     barrel menu (a pen's contextmenu is cancelled before grid.js's
//     "right-click erases" reads it as an erase — expected of a held pen, not seen on one).
//     Hovering a velocity lane shows its value as a tooltip.
//   - Piano keys and pads already read pressure at the strike (piano.js
//     velOf); nothing continuous follows: the engine has no aftertouch.
// Driven by synthetic PointerEvents in tests/browser/test_pen.py; a real
// pen's pressure curve, hover height, palm rejection and Windows Ink
// gestures are NOT covered by that.
// Budget: <= 110 code lines.

(() => {
  "use strict";
  const SURFACES = ".grid, .velrow, #xypad, #drumxy, #piano, #bend-strip, .hit-pad, .note-pad, .chord-pad, .slice-mark, .blk-edge, .sec-resize, .long-edge, .lab-lanes, #deck-panel";
  const CELL = ".grid .gridrow:not(.sliderow) .cell";
  const pen = (e) => e.pointerType === "pen";
  const barrel = (e) => e.button === 2 || !!(e.buttons & 2);
  const eraser = (e) => e.button === 5 || !!(e.buttons & 32);
  const fire = (el, type = "input") => el.dispatchEvent(new Event(type, { bubbles: true }));

  // ---- no scroll, no menu ------------------------------------------------------
  document.addEventListener("pointerover", (e) => {
    const s = e.target.closest?.(SURFACES);
    if (!s) return;
    if (pen(e)) { s.style.touchAction = "none"; s.dataset.pen = "1"; hint(e.target); }
    else if (s.dataset.pen) { s.style.touchAction = ""; delete s.dataset.pen; }
  }, true);
  let penAt = -1e9; // some builds send a pen's contextmenu as a plain mouse event
  for (const type of ["pointerdown", "pointerup"]) document.addEventListener(type, (e) => { if (pen(e)) penAt = performance.now(); }, true);
  document.addEventListener("contextmenu", (e) => {
    if (!(pen(e) || performance.now() - penAt < 700) || !e.target.closest?.(SURFACES)) return;
    if (e.target.matches?.("input[type=range], select")) return; // a value control's menu is tweak.js's
    e.preventDefault();
    e.stopImmediatePropagation();
  }, true);
  // the value, before contact
  function hint(t) {
    const lane = t.closest?.(".vel-lane");
    if (lane) lane.title = `velocity ${lane.getAttribute("aria-valuenow")}`;
  }

  // ---- grids: pressure, eraser, barrel ----------------------------------------
  const velOf = (p) => Math.round(Math.min(1, 0.25 + p) * 20) / 20;
  function laneOf(cell) {
    const step = [...cell.parentElement.querySelectorAll(".cell")].indexOf(cell);
    return cell.closest(".grid")?.querySelector(`.velrow .vel-lane[data-step="${step}"]`) || null;
  }
  function setLane(lane, v) {
    const bar = lane.querySelector(".vel-bar"), input = lane.querySelector("input");
    if (bar) bar.style.setProperty("--v", v); // (a scale: style.css 8, the velocity bars)
    if (input) input.value = v;
    lane.setAttribute("aria-valuenow", String(v));
  }
  let stroke = null;
  const feel = (cell, e) => {
    const lane = cell && stroke.draws && e.pressure > 0 ? laneOf(cell) : null;
    if (lane) stroke.peaks.set(lane, Math.max(stroke.peaks.get(lane) || 0, e.pressure));
  };
  document.addEventListener("pointerdown", (e) => {
    const cell = pen(e) && e.target.closest?.(CELL);
    if (!cell) return;
    stroke = { cell, barrel: barrel(e), eraser: eraser(e), draws: !cell.checked && !eraser(e), moved: false, peaks: new Map() };
    feel(cell, e);
  }, true);
  document.addEventListener("pointermove", (e) => {
    if (!stroke || !pen(e)) return;
    const cell = document.elementFromPoint(e.clientX, e.clientY)?.closest?.(CELL);
    if (cell !== stroke.cell) stroke.moved = true;
    feel(stroke.moved ? cell : stroke.cell, e);
  }, true);
  // capture: before audio.js ends its stroke and the grid's post is queued
  document.addEventListener("pointerup", (e) => {
    const s = stroke;
    if (!s || !pen(e)) return;
    stroke = null;
    for (const [lane, p] of s.peaks) setLane(lane, velOf(p));
    if (s.moved) return;
    if (s.eraser) { if (s.cell.checked) { s.cell.checked = false; fire(s.cell); } }
    else if (s.barrel) s.cell.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, shiftKey: true }));
  }, true);
  document.addEventListener("pointercancel", () => { stroke = null; }, true);
})();
