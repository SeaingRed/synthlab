// synthlab/studio/static/wtdraw.js
// The wavetable editor (docs/sounds-and-breeding.md, "Phase warp, custom
// wavetables, voice and glide modes"): the "draw…" popover (fragments/wt_make.html)
// holds four frames of 64 points (-1..1, each a single cycle). A pointer
// or pen dragged across the canvas sets the frame shown (the points
// between two moves joined, so a fast stroke leaves no gaps); the shape
// buttons fill it; the frame tabs switch which one is drawn. After every
// change the hidden `points` input holds the first `count` frames as the
// route reads them (wavetables.parse_drawing: "," between points, ";"
// between frames), so the form posts them as it is. The stroke's colour is
// the canvas's own `color` (style.css: a token), never a value here.
// Budget: <= 80 code lines.

(() => {
  "use strict";
  const N = 64;
  const SHAPES = {
    sine: (t) => Math.sin(2 * Math.PI * t),
    triangle: (t) => 1 - 4 * Math.abs(t - 0.5),
    saw: (t) => 2 * t - 1,
    square: (t) => (t < 0.5 ? 1 : -1),
    flat: () => 0,
  };
  const fill = (fn) => Array.from({ length: N }, (_, i) => fn(i / N));
  const frames = [fill(SHAPES.sine), fill(SHAPES.saw), fill(SHAPES.square), fill(SHAPES.triangle)];
  let drawn = 0, last = null;

  const formOf = () => document.querySelector(".wt-draw-form");
  function save(form) {
    const count = Number(form.elements.count?.value) || 1;
    form.elements.points.value = frames.slice(0, count).map((f) => f.map((v) => v.toFixed(3)).join(",")).join(";");
  }
  function paint(form) {
    const cv = form.querySelector(".wt-canvas"), g = cv?.getContext("2d");
    if (!g) return;
    const { width: w, height: h } = cv, css = getComputedStyle(cv);
    g.clearRect(0, 0, w, h);
    g.strokeStyle = css.borderColor; g.lineWidth = 1;
    g.beginPath(); g.moveTo(0, h / 2); g.lineTo(w, h / 2); g.stroke();
    g.strokeStyle = css.color; g.lineWidth = 2;
    g.beginPath();
    frames[drawn].forEach((v, i) => g[i ? "lineTo" : "moveTo"]((i / (N - 1)) * w, h / 2 - v * (h / 2 - 3)));
    g.stroke();
    save(form);
  }
  // the canvas position under the pointer as (point index, value)
  function at(cv, e) {
    const r = cv.getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left) / r.width) * (N - 1));
    const v = 1 - (2 * (e.clientY - r.top)) / r.height;
    return [Math.min(N - 1, Math.max(0, i)), Math.min(1, Math.max(-1, v))];
  }
  function stroke(cv, e) {
    const [i, v] = at(cv, e), f = frames[drawn];
    const [i0, v0] = last ?? [i, v];
    const span = Math.abs(i - i0) || 1;
    for (let k = 0; k <= span; k++) {
      const x = i0 + Math.sign(i - i0) * k;
      f[x] = v0 + ((v - v0) * k) / span;
    }
    last = [i, v];
    paint(cv.closest("form"));
  }

  document.addEventListener("pointerdown", (e) => {
    const cv = e.target.closest?.(".wt-canvas");
    if (!cv) return;
    e.preventDefault(); last = null;
    cv.setPointerCapture(e.pointerId);
    stroke(cv, e);
  });
  document.addEventListener("pointermove", (e) => {
    const cv = e.target.closest?.(".wt-canvas");
    if (cv && cv.hasPointerCapture(e.pointerId)) stroke(cv, e);
  });
  document.addEventListener("pointerup", () => { last = null; });
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.("[data-wt-shape]");
    if (!b || !b.form) return;
    frames[drawn] = fill(SHAPES[b.dataset.wtShape] || SHAPES.flat);
    paint(b.form);
  });
  document.addEventListener("change", (e) => {
    const form = e.target.closest?.(".wt-draw-form");
    if (!form) return;
    if (e.target.name === "wt_frame") drawn = Number(e.target.value) || 0;
    paint(form);
  });
  // drawn when the popover opens (a canvas in a closed popover has no box)
  document.addEventListener("toggle", (e) => {
    if (e.target.id === "wt-draw" && e.newState === "open") paint(formOf());
  }, true);
})();
