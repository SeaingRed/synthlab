// synthlab/studio/static/tweak.js
// The input layer every VALUE control shares, and its one owner: what a
// mouse, the keyboard, a pen, a Stream Deck dial and a MIDI knob do to a
// slider, a knob or a select all ends in this file (the mapping table is
// docs/keys-and-midi.md, "Gestures: one owner each"). pen.js is the pen on
// SURFACES (grids, lanes, keys) and does nothing to a value control.
//
// A TWEAKABLE VALUE IS A NATIVE `input[type=range]`. It is found by that
// selector and nothing else; its parameter is its `name` (a log slider
// posts through a hidden twin and is named by its id, `p-<name>`), its
// travel `min` / `max` / `step`, its default `data-default`.
//
// THE ONE WRITE is `synthlab.tweak(el, value, done)`: a range to `value`
// (clamped, snapped to its step), a select or a radio group to option
// number `value` (`tweak.steps(el)`: its options / radios in order). It
// sets the control and dispatches the bubbling `input` a hand would (only
// when the value moved) and, with `done`, the `change` — so the page's own
// listeners fill the track or the knob's arc, write the read-out, feed the
// live voice and post the form, and undo sees one ordinary edit. Beside it:
//   tweak.later(el)  the `change` of a tweak with no "let go" (the wheel, a
//                    dial, a MIDI CC): 250 ms after the last move
//   tweak.reset(el)  THE default: the `dblclick` audio.js resets a slider
//                    on (`data-default`; a log slider's exact value) — the
//                    eraser, a dial's press and the menu all send it
//   tweak.lim(el)    { lo, hi, step } as the write reads them
//   tweak.busy()     a drag of this file's is under way, or just ended
//
// What this adds to every slider:
//  - Shift + drag, or a drag with the pen's barrel button held: FINE — the
//    whole travel takes ten times the distance, from where the value was.
//  - the wheel, on the FOCUSED slider only (a wheel over a panel still
//    scrolls the page): 2 % of the travel a notch, one step with Shift.
//  - the pen's eraser, or a middle click (not inside a [data-zoom]
//    scroller, where a middle drag is zoom.js's pan): back to the default.
//  - (a right click, the pen's barrel button tapped, the Menu key: the
//    control's rows in the context menu — menu.js, which asks
//    `tweak.busy()` so a barrel DRAG opens nothing. Typing a value into
//    the read-out is tweaktype.js, this file's second half.)
//  - a double click resets (audio.js, `data-default`); a control not of
//    the #patch form (a panel moved out keeps it) posts on `change`, so
//    the reset is followed by one.
//  - a pen hovering: the value, as the control's tooltip, before contact.
// And to a `.knob` (a range drawn as a dial): every drag is this file's —
// up or right is more, 160 px the whole travel, fine as above — with the
// pointer captured, so the drag never scrolls or selects text.
//
// The pen is DETECTED here and nowhere else: the first pointer event with
// pointerType "pen" marks the browser (`html[data-pen]`, a `pen` cookie the
// server reads) and lets the dense stylesheet in whatever the primary
// pointer reports (#dense-css's media); the touch tier stands down
// (`html:not([data-pen])` in style.css). #pen-off forgets it.
// Budget: <= 100 code lines (95 -> 100: a radio group is a
// stepped control as a select is, `steps`: the wavetable picker on the deck).

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const root = document.documentElement;
  const TRAVEL = 160; // px of drag for a knob's whole range

  // ---- the pen -------------------------------------------------------------
  function pen(on) {
    if (on === ("pen" in root.dataset)) return;
    if (on) root.dataset.pen = ""; else delete root.dataset.pen;
    document.cookie = `pen=${on ? 1 : ""}; max-age=${on ? 31536000 : 0}; path=/; samesite=lax`;
    const css = document.getElementById("dense-css");
    if (css) css.media = on ? "all" : "(pointer: fine)";
    const off = document.getElementById("pen-off");
    if (off) off.hidden = !on;
    window.dispatchEvent(new Event("synthlab-geometry")); // canvases and cached geometry re-fit
  }
  document.addEventListener("pointerdown", (e) => { if (e.pointerType === "pen") pen(true); }, true);
  document.addEventListener("click", (e) => { if (e.target.closest?.("#pen-off")) pen(false); });
  const off = document.getElementById("pen-off");
  if (off) off.hidden = !("pen" in root.dataset);

  // ---- values --------------------------------------------------------------
  const range = (t) => (t?.matches?.('input[type="range"]') && !t.disabled ? t : null);
  const lim = (el) => { const lo = +el.min || 0, hi = el.max === "" ? 100 : +el.max; return { lo, hi, step: +el.step || (hi - lo) / 100 }; };
  // a STEPPED control's values, in order: a select's options, or a radio
  // group's radios (`role="radiogroup"`: the pills rows, the wavetable
  // picker) — written by number, its events the chosen radio's, as a click's
  const steps = (el) => (el.tagName === "SELECT" ? [...el.options] : el.matches?.('[role="radiogroup"]') ? [...el.querySelectorAll('input[type="radio"]')] : null);
  const chosen = (list) => list.findIndex((o) => (o.tagName === "OPTION" ? o.selected : o.checked));
  const own = (el) => (el.matches?.('[role="radiogroup"]') && el.querySelector('input[type="radio"]:checked')) || el;
  function tweak(el, value, done) {
    const { lo, hi, step } = lim(el), list = steps(el);
    if (list?.length === 0) return false;
    const v = list ? Math.min(list.length - 1, Math.max(0, Math.round(value)))
      : +Math.min(hi, Math.max(lo, lo + Math.round((value - lo) / step) * step)).toFixed(6);
    const moved = (list ? chosen(list) : +el.value) !== v;
    if (moved && list) list[v][list[v].tagName === "OPTION" ? "selected" : "checked"] = true;
    else if (moved) el.value = String(v);
    if (moved) own(el).dispatchEvent(new Event("input", { bubbles: true }));
    if (done) own(el).dispatchEvent(new Event("change", { bubbles: true }));
    return moved;
  }
  const due = new WeakMap();
  tweak.later = (el) => {
    clearTimeout(due.get(el));
    due.set(el, setTimeout(() => el.isConnected && own(el).dispatchEvent(new Event("change", { bubbles: true })), 250));
  };
  tweak.steps = steps;
  tweak.chosen = chosen;
  tweak.reset = (el) => void el.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
  tweak.lim = lim;
  S.tweak = tweak;
  const erasing = (e) => e.pointerType === "pen" && (e.button === 5 || (e.buttons & 32) !== 0);
  const barrel = (e) => e.pointerType === "pen" && (e.buttons & 2) !== 0;

  let drag = null, dragged = -1e9; // { el, x, y, from, fine, knob, moved }; when a drag that moved last ended
  document.addEventListener("pointerdown", (e) => {
    const el = range(e.target);
    if (!el) return;
    if (erasing(e)) { e.preventDefault(); return tweak.reset(el); }
    // a middle click: the default, as a double-click (and no autoscroll)
    // (inside a [data-zoom] scroller a middle drag is zoom.js's pan: left alone)
    if (e.button === 1 && el.dataset.default !== undefined && !el.closest("[data-zoom]")) { e.preventDefault(); return tweak.reset(el); }
    const knob = el.classList.contains("knob"), fine = e.shiftKey || barrel(e);
    if (!knob && !fine) return; // an ordinary slider drag is the browser's
    if (e.button > 2) return;
    e.preventDefault(); el.focus({ preventScroll: true });
    try { el.setPointerCapture(e.pointerId); } catch { /* a pointer that is already gone, or a synthetic one */ }
    drag = { el, x: e.clientX, y: e.clientY, from: +el.value, fine, knob, moved: false };
    root.classList.toggle("tweaking-fine", fine);
  }, true);
  document.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const { el, knob } = drag, { lo, hi } = lim(el);
    const fine = drag.fine || e.shiftKey || barrel(e);
    const px = knob ? (e.clientX - drag.x) + (drag.y - e.clientY) : e.clientX - drag.x;
    const span = knob ? TRAVEL : el.getBoundingClientRect().width || TRAVEL;
    if (tweak(el, drag.from + (px / span) * (hi - lo) * (fine ? 0.1 : 1))) drag.moved = true;
  });
  const end = (e) => {
    if (!drag) return;
    if (drag.el.hasPointerCapture?.(e.pointerId)) drag.el.releasePointerCapture(e.pointerId);
    if (drag.moved) { dragged = performance.now(); drag.el.dispatchEvent(new Event("change", { bubbles: true })); }
    drag = null; root.classList.remove("tweaking-fine");
  };
  document.addEventListener("pointerup", end);
  document.addEventListener("pointercancel", end);
  // the barrel button is a right click to the browser: menu.js opens the
  // control's menu on one, unless that press was (or still is) a drag
  tweak.busy = () => !!drag || performance.now() - dragged < 500;
  document.addEventListener("pointerover", (e) => {
    const el = e.pointerType === "pen" && e.target.id && range(e.target);
    const out = el && document.querySelector(`output[for="${CSS.escape(el.id)}"]`);
    if (out) el.title = out.textContent.trim();
  }, true);

  // (audio.js's reset, registered first, has set the value by now)
  document.addEventListener("dblclick", (e) => {
    const el = range(e.target);
    if (el && el.dataset.default !== undefined && el.form?.id !== "patch") el.dispatchEvent(new Event("change", { bubbles: true }));
  });
  document.addEventListener("wheel", (e) => {
    const el = range(e.target);
    if (!el || el !== document.activeElement || e.ctrlKey) return;
    e.preventDefault();
    const { lo, hi, step } = lim(el), by = e.shiftKey ? step : Math.max(step, (hi - lo) * 0.02);
    if (tweak(el, +el.value + (e.deltaY < 0 || e.deltaX < 0 ? by : -by))) tweak.later(el);
  }, { passive: false });
})();
