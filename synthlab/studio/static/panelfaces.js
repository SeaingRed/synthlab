// synthlab/studio/static/panelfaces.js
// The drawn controls on the Synths page and the FX racks' chain
// browser (docs/sounds-and-breeding.md, "Oscillator panels",
// and "Chain presets"). The markup is the server's (param_row.html,
// fragments/fm4_panel.html, fragments/fx_rack.html); this keeps what it
// cannot, and writes every value through tweak.js:
// 1. The FM4 algorithm's drawings pick through the select: a click is
//    `synthlab.tweak` onto `#p-fm4_algo` (the select stays the control the
//    keyboard, the deck and MIDI learn use).
// 2. The ratio presets: four ratios in one move, each through tweak.js (a
//    ratio past a slider's travel widens it first, as the server draws a
//    stored value outside the spec).
// 3. The wavetable drawings' `--wt-pos`, from the position slider as it
//    moves and after a swap (an OOB row, an A/B morph, a curve).
// 4. The chain browser: the search (every word in a row's text or group),
//    purpose / genre (the rows moved under the other headings; per browser,
//    "synthlab.fxChainsBy"), focus to the search on open, ↓ / ↑ / Home /
//    End across the rows shown, Enter in the search adds the first.
// 70 -> 120: 5. the drawn LFO shapes (fragments/lfo_draw.html):
//    the drawing on LFO 1, 2 and 3's rows, edited here and written to its
//    hidden field (the panels' live parts are this file's).
// Budget: <= 120 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ||= {}), KEY = "synthlab.fxChainsBy";
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

  // ---- 1, 2: the FM4 panel ---------------------------------------------------
  document.addEventListener("click", (e) => {
    const algo = e.target.closest?.(".fm4-algo[data-algo]"), sel = document.getElementById("p-fm4_algo");
    if (algo && sel) S.tweak?.(sel, [...sel.options].findIndex((o) => o.value === algo.dataset.algo), true);
    const preset = e.target.closest?.("[data-fm4-ratios]");
    if (!preset) return; // (audio.js closes the menu: it is a panel preset)
    preset.dataset.fm4Ratios.split(" ").map(Number).forEach((v, i) => {
      const el = document.getElementById(`p-fm4_ratio${i + 1}`);
      if (!el || !Number.isFinite(v)) return;
      if (v > +el.max) el.max = String(v);
      if (v < +el.min) el.min = String(v);
      S.tweak?.(el, v, true);
    });
  });

  // ---- 3: the wavetable position on the drawings -----------------------------
  function wtPos() {
    const pos = document.getElementById("p-wt_pos"), pick = document.querySelector(".wt-pick");
    if (!pos || !pick) return;
    const lo = +pos.min || 0, hi = +pos.max || 1;
    pick.style.setProperty("--wt-pos", String(Math.min(1, Math.max(0, (pos.value - lo) / (hi - lo || 1)))));
  }
  document.addEventListener("input", (e) => { if (e.target.id === "p-wt_pos") wtPos(); });
  document.addEventListener("htmx:after:settle", wtPos);

  // ---- 4: the chain browser --------------------------------------------------
  const rowsOf = (pop) => $$(".fxc-row", pop);
  function regroup(pop, by) {
    pop.dataset.by = by;
    for (const b of $$(".fxc-mode", pop)) b.setAttribute("aria-pressed", String(b.dataset.by === by));
    for (const r of rowsOf(pop).sort((a, b) => a.dataset.i - b.dataset.i)) {
      pop.querySelector(`.fxc-sec[data-by="${by}"][data-key="${r.dataset[by]}"]`)?.append(r);
    }
  }
  function filter(pop) {
    const words = (pop.querySelector(".fxc-q")?.value || "").toLowerCase().split(/\s+/).filter(Boolean);
    for (const r of rowsOf(pop)) {
      const hay = `${r.textContent} ${r.dataset.group}`.toLowerCase();
      r.hidden = !words.every((w) => hay.includes(w));
    }
  }
  document.addEventListener("toggle", (e) => { // (toggle does not bubble: caught on its way down)
    const pop = e.target;
    if (!pop.matches?.(".fx-chains") || e.newState !== "open") return;
    let by = "group";
    try { if (localStorage.getItem(KEY) === "pack") by = "pack"; } catch { /* storage refused: by purpose */ }
    regroup(pop, by);
    filter(pop);
    pop.querySelector(".fxc-q")?.focus();
  }, true);
  document.addEventListener("click", (e) => {
    const mode = e.target.closest?.(".fx-chains .fxc-mode");
    if (!mode) return;
    regroup(mode.closest(".fx-chains"), mode.dataset.by);
    try { localStorage.setItem(KEY, mode.dataset.by); } catch { /* this page only */ }
    mode.closest(".fx-chains").querySelector(".fxc-q")?.focus(); // (a clicked button would let typing play notes)
  });
  document.addEventListener("input", (e) => { if (e.target.matches?.(".fxc-q")) filter(e.target.closest(".fx-chains")); });

  // ---- 5: a general LFO's drawing ----------------------------------------------
  // The hidden `<lfo>_draw` field is the control; this redraws it (as
  // oscfaces.draw_face) and writes it: press adds a point (or takes the one
  // under the pointer) and drags it, double-click removes one, a shape button
  // starts over. A write fires `input`, so the form posts it.
  const W = 120, H = 40, ptsOf = (s) => (s || "").split(",").map((q) => q.split(":").map(Number))
    .filter(([x, y]) => x >= 0 && x < 1 && Number.isFinite(y)).sort((a, b) => a[0] - b[0]);
  const yOf = (v) => H / 2 - v * (H / 2 - 2), vOf = (y) => Math.max(-1, Math.min(1, (H / 2 - y) / (H / 2 - 2)));
  function paint(box, pts, held = -1) {
    const p = pts.length ? pts : [[0, -1], [0.5, 1]], m = p.length;
    const xs = [p[m - 1][0] - 1, ...p.map((q) => q[0]), p[0][0] + 1], ys = [p[m - 1][1], ...p.map((q) => q[1]), p[0][1]];
    const at = (t) => { let j = 0; while (j < xs.length - 2 && t >= xs[j + 1]) j++; return ys[j] + (ys[j + 1] - ys[j]) * (t - xs[j]) / (xs[j + 1] - xs[j]); };
    box.querySelector(".lfo-draw-line").setAttribute("points", [0, ...p.map((q) => q[0]), 1].map((t) => `${t * W},${yOf(at(t))}`).join(" "));
    box.querySelectorAll(".lfo-draw-pt").forEach((c) => c.remove());
    p.forEach(([x, y], i) => box.querySelector("svg").insertAdjacentHTML("beforeend",
      `<circle class="lfo-draw-pt${i === held ? " held" : ""}" cx="${x * W}" cy="${yOf(y)}" r="2.4"/>`));
  }
  function write(box, pts) {
    const f = box.querySelector("input[type=hidden]");
    f.value = pts.slice(0, 32).map(([x, y]) => `${+x.toFixed(3)}:${+y.toFixed(3)}`).join(",");
    paint(box, ptsOf(f.value));
    f.dispatchEvent(new Event("input", { bubbles: true }));
  }
  let drawing = null;
  const where = (svg, e) => { const r = svg.getBoundingClientRect(); return [Math.max(0, Math.min(0.999, (e.clientX - r.left) / r.width)), vOf((e.clientY - r.top) / r.height * H)]; };
  document.addEventListener("pointerdown", (e) => {
    const svg = e.target.closest?.(".lfo-draw-svg");
    if (!svg || e.button !== 0) return;
    e.preventDefault();
    const box = svg.closest(".lfo-draw"), pts = ptsOf(box.querySelector("input[type=hidden]").value), [x, y] = where(svg, e);
    let i = pts.findIndex((q) => Math.abs(q[0] - x) < 0.04 && Math.abs(q[1] - y) < 0.3);
    if (i < 0) { pts.push([x, y]); i = pts.length - 1; }
    try { svg.setPointerCapture(e.pointerId); } catch { /* a synthetic event */ }
    drawing = { box, svg, pts, i };
    paint(box, pts, i);
  });
  document.addEventListener("pointermove", (e) => {
    if (!drawing) return;
    drawing.pts[drawing.i] = where(drawing.svg, e);
    paint(drawing.box, [...drawing.pts].sort((a, b) => a[0] - b[0]));
  });
  const lift = () => { if (drawing) { write(drawing.box, [...drawing.pts].sort((a, b) => a[0] - b[0])); drawing = null; } };
  document.addEventListener("pointerup", lift);
  document.addEventListener("pointercancel", lift);
  document.addEventListener("dblclick", (e) => {
    const svg = e.target.closest?.(".lfo-draw-svg"), box = svg?.closest(".lfo-draw");
    if (!box) return;
    const pts = ptsOf(box.querySelector("input[type=hidden]").value), [x, y] = where(svg, e);
    const i = pts.findIndex((q) => Math.abs(q[0] - x) < 0.04 && Math.abs(q[1] - y) < 0.3);
    if (i >= 0 && pts.length > 1) { pts.splice(i, 1); write(box, pts); }
  });
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.(".lfo-draw [data-draw]");
    if (b) write(b.closest(".lfo-draw"), ptsOf(b.dataset.draw));
  });
  document.addEventListener("keydown", (e) => {
    const pop = e.target.closest?.(".fx-chains"), q = e.target.matches?.(".fxc-q");
    if (!pop || !["ArrowDown", "ArrowUp", "Home", "End", "Enter"].includes(e.key)) return;
    const shown = rowsOf(pop).filter((r) => !r.hidden); // (document order is the order drawn)
    if (e.key === "Enter") return void (q && shown[0] && (e.preventDefault(), shown[0].click()));
    if (q && e.key !== "ArrowDown") return; // Home / End / ↑ in the field move its caret
    const at = q ? -1 : shown.indexOf(e.target);
    const to = e.key === "Home" ? 0 : e.key === "End" ? shown.length - 1 : at + (e.key === "ArrowDown" ? 1 : -1);
    e.preventDefault();
    (to < 0 ? pop.querySelector(".fxc-q") : shown[Math.min(to, shown.length - 1)])?.focus();
  });
})();
