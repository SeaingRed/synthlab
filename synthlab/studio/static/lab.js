// synthlab/studio/static/lab.js
// The Sample Lab's island: picking a sample. The Lab is server-rendered
// forms; this owns the gestures a form cannot express ("listen, mark,
// keep"):
//  - a drag on a lane is the selection ON THAT LANE: its two ends, in
//    seconds of the song (`synthlab.lab.at`, labview.js), posted to
//    /lab/region with `lane` — the mix lane plays the recording, a stem's
//    lane that stem alone, Shift held adds it to the ticked ones. The band
//    that follows the pointer is snapped as the server will snap it (to the
//    bars, or to the beats; Alt held: free). A drag on the ruler moves the
//    selection without changing what it plays.
//  - an edge of the selection (`.lab-sel-edge` on the ruler) dragged:
//    `edge` + `at`, the server snapping it as a drag's.
//  - a click without a drag plays that lane from there (`#lab-scrub`: the
//    region's WAV route from that point, 30 s; on the ruler, what the
//    selection plays); Esc stops it. Space is the transport's: the
//    selection's ▶ (`data-keys="Space"`).
//  - the keyboard on the lanes (#lab-wave focused): ← → move the
//    selection a beat (Shift: a bar), [ ] make it a step shorter / longer
//    (a bar in bars, a beat in beats), Enter keeps it (#lab-keep). A run of
//    presses is one post.
//  - a file dropped on the import box goes into its file input, whose
//    change event is what the upload form already posts on.
// A press on a label, a button, a link, a field, a chop's edge (labchop.js),
// the separation call-out, the overview or a grip (labview.js) is theirs.
// Every post morphs #lab-body, so the lanes keep the keyboard.
// Budget: <= 130 code lines (50 -> 130: the selection on any
// lane, its edges, the live snap, click to hear, the keys).

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const SKIP = "label, button, a, input, select, .chop-mark, .lab-cta, .lab-strip, .lab-grip, .lab-head, .lab-corner";
  let drag = null; // {w, lane, t0, t1, x0, moved, band, edge, el, from}
  const acc = { op: "", by: 0 };
  let kt = 0;

  function post(w, values) {
    const form = w.closest("form"), source = form?.elements.source?.value;
    if (!source) return;
    htmx.ajax("POST", "/lab/region", {
      values: { source, drag_a: "", drag_b: "", ...values },
      source: form, // its snap / stems / levels, not the page's every field
      target: "#lab-body", swap: "outerMorph",
    });
  }
  // where the server will put a drag from a to b (lab.drag_region)
  function snapped(w, a, b, free) {
    const d = S.lab.data(w), bar = d.beat * d.per;
    [a, b] = [Math.min(a, b), Math.max(a, b)];
    if (!d.beat || free) return [a, b];
    if (w.dataset.mode === "bars") {
      const i = Math.floor((a - d.offset) / bar + 1e-3), j = Math.ceil((b - d.offset) / bar - 1e-3);
      return [d.offset + Math.max(0, i) * bar, d.offset + Math.max(i + 1, j) * bar];
    }
    const r = (t) => d.offset + Math.round((t - d.offset) / d.beat) * d.beat;
    return [r(a), Math.max(r(b), r(a) + d.beat)];
  }

  document.addEventListener("pointerdown", (ev) => {
    const w = ev.target.closest?.("#lab-wave");
    if (!w || ev.button !== 0 || !S.lab) return;
    const edge = ev.target.closest(".lab-sel-edge"), lane = ev.target.closest(".lab-lane[data-lane]");
    if (!edge && (ev.target.closest(SKIP) || !(lane || ev.target.closest(".lab-ruler, .lab-over")))) return;
    ev.preventDefault();
    w.focus({ preventScroll: true });
    const t = S.lab.at(ev);
    drag = { w, lane: lane?.dataset.lane || "", t0: t, t1: t, x0: ev.clientX, moved: false, edge: edge?.dataset.edge, el: edge };
    if (edge) drag.from = edge.dataset.at;
    else {
      drag.band = Object.assign(document.createElement("div"), { className: "lab-drag" });
      (lane?.querySelector(".lab-track") || w.querySelector(".lab-over")).append(drag.band);
    }
  });
  document.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    drag.t1 = S.lab.at(ev);
    // captured once it is a drag: a click and a double-click keep their own target
    if (!drag.moved && Math.abs(ev.clientX - drag.x0) > 3) drag.moved = !drag.w.setPointerCapture?.(ev.pointerId);
    if (!drag.moved) return;
    if (drag.edge) {
      drag.el.dataset.at = drag.t1.toFixed(4);
      const band = drag.w.querySelector(".lab-ruler-region");
      if (band) band.dataset[drag.edge === "start" ? "at" : "to"] = drag.t1.toFixed(4);
      return S.lab.place();
    }
    const [a, b] = snapped(drag.w, drag.t0, drag.t1, ev.altKey);
    drag.band.style.left = `${S.lab.pct(a)}%`;
    drag.band.style.width = `${S.lab.pct(b) - S.lab.pct(a)}%`;
  });
  const end = (ev) => {
    if (!drag) return;
    const d = drag;
    drag = null;
    d.band?.remove();
    const free = ev.altKey ? "1" : "";
    if (ev.type !== "pointerup") {
      if (d.edge) { d.el.dataset.at = d.from; S.lab.place(); }
      return;
    }
    if (d.edge) { if (d.moved) post(d.w, { edge: d.edge, at: d.t1.toFixed(4), free }); return; }
    if (!d.moved) return play(d.w, d.lane, d.t0);
    post(d.w, { drag_a: d.t0.toFixed(4), drag_b: d.t1.toFixed(4), free, lane: d.lane, add: ev.shiftKey ? "1" : "" });
  };
  document.addEventListener("pointerup", end);
  document.addEventListener("pointercancel", end);

  // ---- a click: the lane from there --------------------------------------
  function play(w, lane, t) {
    const a = document.getElementById("lab-scrub"), sel = w.closest("form")?.querySelector(".lab-player audio");
    if (!a || !sel) return;
    const u = new URL(sel.getAttribute("src"), location.href);
    if (lane) { // that lane alone, at its level (the mix: the recording)
      u.searchParams.set("stems", lane === "mix" ? "" : lane);
      u.searchParams.set("levels", lane === "mix" ? "" : w.querySelector(`[name="level.${lane}"]`)?.value || "0");
    }
    u.searchParams.set("start", t.toFixed(3));
    u.searchParams.set("length", "30");
    a.dataset.from = t.toFixed(3);
    a.src = u.pathname + u.search;
    a.play().catch(() => {});
  }
  // Esc: the click-to-hear stops and lets its stream go
  function stop() {
    const a = document.getElementById("lab-scrub");
    if (!a?.getAttribute("src")) return;
    a.pause();
    a.removeAttribute("src");
    a.load();
  }
  // a paused click-to-hear is over, not held: Space then plays the selection
  document.addEventListener("pause", (e) => { if (e.target.id === "lab-scrub") S.transport?.release("media"); }, true);

  // ---- the keys on the lanes ---------------------------------------------
  function flush(w) {
    clearTimeout(kt);
    if (acc.by && w.isConnected) post(w, { step: acc.op, by: String(acc.by) });
    acc.op = ""; acc.by = 0;
  }
  document.addEventListener("keydown", (ev) => {
    const w = ev.target;
    if (w?.id !== "lab-wave" || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    if (ev.key === "Enter") { ev.preventDefault(); return document.getElementById("lab-keep")?.click(); }
    if (ev.key === "Escape") return stop();
    const k = { ArrowLeft: ["move", -1], ArrowRight: ["move", 1], "[": ["grow", -1], "]": ["grow", 1] }[ev.key];
    if (!k) return;
    ev.preventDefault();
    if (acc.op && acc.op !== k[0]) flush(w);
    acc.op = k[0];
    acc.by += k[1] * (k[0] === "move" && ev.shiftKey ? 4 : 1);
    clearTimeout(kt);
    kt = setTimeout(() => flush(w), 160);
  });

  // ---- a file dropped on the import box ------------------------------------
  for (const type of ["dragover", "dragleave", "drop"]) {
    document.addEventListener(type, (ev) => {
      const box = ev.target.closest?.("#lab-drop");
      if (!box) return;
      ev.preventDefault();
      box.classList.toggle("over", type === "dragover");
      const input = box.querySelector("input");
      if (type !== "drop" || !ev.dataTransfer?.files.length || input.disabled) return;
      input.files = ev.dataTransfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }
})();
