// synthlab/studio/static/labchop.js
// The Sample Lab's chops: the gestures on the waveform that are not the
// region (lab.js owns that one — a drag across; it ignores a press on a
// marker, and a click without a drag, so the two never start together).
//  - a double-click on a lane cuts a chop there (POST /lab/chop op=split);
//  - a `.chop-mark` (a chop's edge: role=slider, focusable) is dragged, or
//    nudged with the arrows — 5 ms, Shift 50 ms — and its new place posted
//    (op=move) when the gesture ends; a held arrow is one post.
// The island sends positions only, in seconds of the song (labview.js's
// `synthlab.lab.at`, whatever span the lanes show). Where an edge
// lands is the server's: it clamps to the neighbours and snaps to a beat
// line nearby unless `free` is sent — Alt held, or an arrow nudge (a 5 ms
// step must not be pulled back to the line it just left).
// The answers MORPH #lab-body, so the marker under the keyboard is the
// same element afterwards and keeps the focus; a cancelled drag goes back.
// Also here, because every Lab swap would otherwise shut them: which of
// the Lab's <details> are open (the chops' "clean" rows — what a region
// becomes is cards now, not a fold) is kept out of the morph and
// remembered across the swaps that replace.
// A fold nobody has touched starts open on a window 1900 px wide or more
// — but a chop's "clean" only on the marked chop (`aria-current`): all of
// them open made five chops a column of forms.
// Budget: <= 90 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const STEP = 0.005; // seconds an arrow moves an edge; x10 with Shift
  const DETAILS = "#lab-body details[id]";
  let drag = null, timer = 0;
  const waveOf = (el) => el.closest("#lab-wave");
  function post(wave, values) {
    const source = wave.closest("form")?.elements.source?.value;
    if (!source) return;
    htmx.ajax("POST", "/lab/chop", {
      source: wave.closest("form"), // the Lab form's fields, not the page's every field
      values: { source, drag_a: "", drag_b: "", ...values },
      target: "#lab-body", swap: "outerMorph",
    });
  }
  function place(mark, at) {
    const v = Math.min(S.lab.data().total, Math.max(0, at));
    mark.dataset.at = v.toFixed(4);
    mark.style.left = `${S.lab.pct(v)}%`;
    mark.setAttribute("aria-valuenow", v.toFixed(4));
  }
  const move = (mark, free) => post(waveOf(mark), {
    op: "move", chop: mark.dataset.chop, edge: mark.dataset.edge, at: mark.dataset.at, free: free ? "1" : "",
  });
  document.addEventListener("pointerdown", (ev) => {
    const mark = ev.target.closest?.(".chop-mark");
    if (!mark || ev.button !== 0 || !S.lab) return;
    ev.preventDefault();
    mark.focus();
    mark.setPointerCapture(ev.pointerId);
    mark.classList.add("dragging");
    drag = { mark, from: mark.dataset.at };
  });
  document.addEventListener("pointermove", (ev) => {
    if (drag) place(drag.mark, S.lab.at(ev));
  });
  const end = (ev) => {
    if (!drag) return;
    const { mark, from } = drag;
    drag = null;
    mark.classList.remove("dragging");
    if (ev.type === "pointercancel") place(mark, Number(from));
    else if (mark.dataset.at !== from) move(mark, ev.altKey);
  };
  document.addEventListener("pointerup", end);
  document.addEventListener("pointercancel", end);
  document.addEventListener("keydown", (ev) => {
    const mark = ev.target.closest?.(".chop-mark");
    const d = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1 }[ev.key];
    if (!mark || !d) return;
    ev.preventDefault();
    place(mark, Number(mark.dataset.at) + d * STEP * (ev.shiftKey ? 10 : 1));
    clearTimeout(timer); // a held or repeated arrow is one post
    timer = setTimeout(() => mark.isConnected && move(mark, true), 300);
  });
  document.addEventListener("dblclick", (ev) => {
    const wave = ev.target.closest?.("#lab-wave");
    if (!wave || !S.lab || !ev.target.closest(".lab-track, .lab-over")) return;
    post(wave, { op: "split", at: S.lab.at(ev).toFixed(4), free: ev.altKey ? "1" : "" });
  });

  // ---- which <details> are open -------------------------------------------
  const key = (d) => `synthlab.lab.${d.id}`;
  document.addEventListener("toggle", (ev) => {
    const d = ev.target.closest?.(DETAILS);
    if (d) sessionStorage.setItem(key(d), d.open ? "1" : "0");
  }, true);
  // from 1900 px the folds have a column of their own (style.css section
  // 19): one nobody has touched starts open there; a chop's, only the marked one's
  const wide = matchMedia("(min-width: 1900px)");
  const fresh = (d) => (d.matches(".chop-clean") ? !!d.closest('[aria-current="true"]') : wide.matches);
  const reopen = () => {
    for (const d of document.querySelectorAll(DETAILS)) d.open = (sessionStorage.getItem(key(d)) ?? (fresh(d) ? "1" : "0")) === "1";
  };
  document.addEventListener("htmx:before:morph:attr", (ev) => {
    if (ev.detail.attrName === "open" && ev.target.matches?.(DETAILS)) ev.preventDefault();
  });
  document.addEventListener("htmx:after:settle", reopen);
  document.addEventListener("DOMContentLoaded", reopen);
})();
