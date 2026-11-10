// synthlab/studio/static/layout.js
// The workbench island: how much room the screen gives to what. Owns:
//   - density: compact (the default — small type, hairline outlines, tight
//     gaps, the nav as a side activity bar) or comfy (the roomier layout it
//     had). A `density` cookie, so base.html paints the right one first;
//   - splitters: a `.splitter` handle drags the width of the pane it sits
//     in — or names with `data-pane`; its height with `data-axis="y"` —
//     (`data-var` on the nearest `data-scope` ancestor, `data-min` /
//     `data-max` px), arrows nudge it 16px (shift 64), double-click resets.
//     Widths persist per browser (localStorage `synthlab.split.<var>`);
//   - folds: a `[data-fold]` toggle hides the `.fold-body` parts of the
//     section it names, VS Code pane style, remembered per section;
//   - tabs: a `[data-tab-of]` button shows one part of a box at a time
//     (the Loops page's Play module: pads, XY, FX, beats), remembered per
//     box — a fold's sibling;
//   - slice markers: the sampler panel's chop starts, dragged over its
//     waveform (or nudged from the keyboard) — a resize-by-drag gesture
//     like the splitters, so it lives here rather than in a new island.
// Each is re-applied after every swap: a view swap brings new panes.
// Budget: <= 172 code lines (110 -> 150 for the slice markers: pointer drag,
// keyboard nudge with one debounced post, focus kept across the OOB swap;
// 150 -> 156 for a splitter that drags a height: the Song page's editor;
// 156 -> 158 for a fold that starts folded by a media query: the keys dock;
// 158 -> 172 for the tabs: the Loops page's Play module, a
// fold's sibling, re-applied after a swap as a fold is).

(() => {
  "use strict";
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} },
  };

  // ---- density --------------------------------------------------------------
  document.addEventListener("click", (e) => {
    const btn = e.target.closest?.("#density-toggle");
    if (!btn) return;
    const next = document.documentElement.dataset.density === "comfy" ? "compact" : "comfy";
    document.documentElement.dataset.density = next;
    document.cookie = `density=${next}; max-age=31536000; path=/; samesite=lax`;
    btn.setAttribute("aria-pressed", String(next === "compact"));
    window.dispatchEvent(new Event("synthlab-geometry")); // canvases and cached geometry re-fit
  });

  // ---- splitters ----------------------------------------------------------
  const key = (v) => `synthlab.split.${v}`;
  const scopeOf = (sp) => sp.closest(sp.dataset.scope || "body");
  const paneOf = (sp) => (sp.dataset.pane && document.querySelector(sp.dataset.pane)) || sp.parentElement;
  // `data-axis="y"`: the handle drags a height (the Song page's editor pane);
  // `data-edge` left / top: the handle is on the pane's near edge, so the
  // pane grows as the pointer moves away from its far one
  const vert = (sp) => sp.dataset.axis === "y";
  const far = (sp) => sp.dataset.edge === "left" || sp.dataset.edge === "top";
  const clampTo = (sp, px) => Math.round(Math.min(Number(sp.dataset.max) || 900, Math.max(Number(sp.dataset.min) || 120, px)));
  function setWidth(sp, px, save = true) {
    const scope = scopeOf(sp);
    if (!scope) return;
    const w = clampTo(sp, px);
    scope.style.setProperty(sp.dataset.var, `${w}px`);
    sp.setAttribute("aria-valuenow", String(w));
    if (save) store.set(key(sp.dataset.var), String(w));
  }
  function restore() {
    for (const sp of document.querySelectorAll(".splitter[data-var]")) {
      const saved = Number(store.get(key(sp.dataset.var)));
      if (saved) setWidth(sp, saved, false);
    }
  }
  let drag = null;
  document.addEventListener("pointerdown", (e) => {
    const sp = e.target.closest?.(".splitter[data-var]");
    if (!sp || e.button !== 0) return;
    e.preventDefault();
    const pane = paneOf(sp).getBoundingClientRect();
    const y = vert(sp), flip = far(sp);
    drag = { sp, y, flip, from: y ? (flip ? pane.bottom : pane.top) : flip ? pane.right : pane.left };
    sp.setPointerCapture(e.pointerId);
    document.documentElement.classList.add("resizing");
    document.documentElement.classList.toggle("resizing-y", y);
  });
  document.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const at = drag.y ? e.clientY : e.clientX;
    setWidth(drag.sp, drag.flip ? drag.from - at : at - drag.from);
  });
  const endDrag = () => {
    if (!drag) return;
    drag = null;
    document.documentElement.classList.remove("resizing", "resizing-y");
    window.dispatchEvent(new Event("synthlab-geometry"));
  };
  document.addEventListener("pointerup", endDrag);
  document.addEventListener("pointercancel", endDrag);
  document.addEventListener("dblclick", (e) => {
    const sp = e.target.closest?.(".splitter[data-var]");
    if (!sp) return;
    scopeOf(sp)?.style.removeProperty(sp.dataset.var);
    store.set(key(sp.dataset.var), null);
  });
  document.addEventListener("keydown", (e) => {
    const sp = e.target.closest?.(".splitter[data-var]");
    const d = sp && (vert(sp) ? { ArrowUp: -1, ArrowDown: 1 } : { ArrowLeft: -1, ArrowRight: 1 })[e.key];
    if (!sp || !d) return;
    e.preventDefault();
    const now = paneOf(sp).getBoundingClientRect()[vert(sp) ? "height" : "width"];
    setWidth(sp, now + d * (e.shiftKey ? 64 : 16) * (far(sp) ? -1 : 1));
  });

  // ---- folds ----------------------------------------------------------------
  // `data-fold-auto="<media query>"`: a fold nobody has chosen yet starts
  // folded where the query holds (the keys dock on a short window or under
  // a finger); a press is remembered either way ("1" folded, "0" open).
  // `data-fold-page` (the keys dock, on every page): per page, a page never
  // pressed taking the one key it had before it was every page's
  const foldKey = (btn, page) => `synthlab.fold.${btn.dataset.fold}${page && btn.hasAttribute("data-fold-page") ? `@${document.getElementById("view")?.dataset.view}` : ""}`;
  function fold(btn, folded, save) {
    const box = document.getElementById(btn.dataset.fold);
    if (!box) return;
    box.classList.toggle("folded", folded);
    btn.setAttribute("aria-expanded", String(!folded));
    if (save) store.set(foldKey(btn, true), folded ? "1" : btn.dataset.foldAuto ? "0" : null);
  }
  const folded = (btn, v = store.get(foldKey(btn, true)) ?? store.get(foldKey(btn))) => v === "1" || (v == null && !!btn.dataset.foldAuto && matchMedia(btn.dataset.foldAuto).matches);
  document.addEventListener("click", (e) => {
    const btn = e.target.closest?.("[data-fold]");
    if (!btn) return;
    const box = document.getElementById(btn.dataset.fold);
    fold(btn, !box?.classList.contains("folded"), true);
    if (!box?.classList.contains("folded") && !window.synthlab.calm()) window.gsap?.from(box.querySelectorAll(":scope > .fold-body"), { autoAlpha: 0, y: -6, duration: 0.2, clearProps: "all" });
  });
  function refold() {
    for (const btn of document.querySelectorAll("[data-fold]")) fold(btn, folded(btn), false);
  }

  // ---- tabs ----------------------------------------------------------------
  // `[data-tab-of=<id>][data-tab-pick=<name>]`: a tab of the box `#id`,
  // which shows one part at a time (`data-tab` on it, the CSS reads it:
  // the Loops page's Play module); remembered per box, re-applied after a
  // swap as a fold is (a morph puts the server's default back)
  function tab(btn, save) {
    const box = document.getElementById(btn.dataset.tabOf);
    if (!box) return;
    box.dataset.tab = btn.dataset.tabPick;
    for (const b of document.querySelectorAll(`[data-tab-of="${btn.dataset.tabOf}"]`)) b.setAttribute("aria-selected", String(b === btn));
    if (save) store.set(`synthlab.tab.${btn.dataset.tabOf}`, btn.dataset.tabPick);
  }
  document.addEventListener("click", (e) => { const b = e.target.closest?.("[data-tab-of]"); if (b) tab(b, true); });
  const retab = () => {
    for (const b of document.querySelectorAll("[data-tab-of]")) if (store.get(`synthlab.tab.${b.dataset.tabOf}`) === b.dataset.tabPick) tab(b, false);
  };

  // ---- slice markers ------------------------------------------------------
  // The sampler panel's `.slice-mark`s: each chop's start over the waveform
  // (role=slider, so piano.js's arrow bend leaves it alone). A drag, or the
  // arrows (5 ms, shift 50 ms — `data-step` is 5 ms of this sample), move it
  // here at once, inside `data-lo`..`data-hi` (the server's own clamp);
  // the gesture's end posts the absolute position (`action=move`, `at`
  // 0..1). The panel comes back OOB by its region watch, so focus is put
  // back on the new marker of the same slice.
  let mark = null, markTimer = 0, refocus = null;
  function markAt(m, at) {
    const v = Math.min(Math.max(at, Number(m.dataset.lo)), Number(m.dataset.hi));
    const secs = Number(m.parentElement?.dataset.secs) || 0;
    m.dataset.at = String(v);
    m.style.left = `${v * 100}%`;
    m.setAttribute("aria-valuenow", v.toFixed(5));
    m.setAttribute("aria-valuetext", `${(v * secs).toFixed(3)} s`);
  }
  function postMark(m) {
    refocus = document.activeElement === m ? { i: m.dataset.index, until: Date.now() + 5000 } : null;
    window.synthlab.post("/fragment/sampler/slice", { index: m.dataset.index, action: "move", at: m.dataset.at });
  }
  document.addEventListener("pointerdown", (e) => {
    const m = e.target.closest?.(".slice-mark");
    if (!m || e.button !== 0) return;
    e.preventDefault();
    m.focus();
    mark = { m, box: m.parentElement.getBoundingClientRect(), from: m.dataset.at };
    m.setPointerCapture(e.pointerId);
    m.classList.add("dragging");
  });
  document.addEventListener("pointermove", (e) => {
    if (mark) markAt(mark.m, (e.clientX - mark.box.left) / Math.max(1, mark.box.width));
  });
  const endMark = (e) => {
    if (!mark) return;
    const { m, from } = mark;
    mark = null;
    m.classList.remove("dragging");
    if (e.type === "pointercancel") markAt(m, Number(from));
    else if (m.dataset.at !== from) postMark(m);
  };
  document.addEventListener("pointerup", endMark);
  document.addEventListener("pointercancel", endMark);
  document.addEventListener("keydown", (e) => {
    const m = e.target.closest?.(".slice-mark");
    const d = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1 }[e.key];
    if (!m || !d) return;
    e.preventDefault();
    const step = Number(m.parentElement?.dataset.step) || 0.001;
    markAt(m, Number(m.dataset.at) + d * step * (e.shiftKey ? 10 : 1));
    clearTimeout(markTimer); // a held or repeated arrow is one post
    markTimer = setTimeout(() => postMark(m), 300);
  });
  function refocusMark() {
    const a = document.activeElement;
    if (!refocus) return;
    if (a && a !== document.body && a.isConnected) { // not swapped yet, or the player moved on
      if (!a.closest?.(".slice-mark")) refocus = null;
      return;
    }
    if (Date.now() < refocus.until) document.querySelector(`.slice-mark[data-index="${refocus.i}"]`)?.focus();
    refocus = null;
  }

  const apply = () => { restore(); refold(); retab(); refocusMark(); };
  document.addEventListener("htmx:after:settle", apply);
  document.addEventListener("DOMContentLoaded", apply);
})();
