// synthlab/studio/static/zonemap.js
// The sampler panel's zone map (fragments/sampler_zones.html):
// a multi-sample instrument's zones as boxes on a keys (x, 0..127) by
// velocity (y, 0..127) plane.
// - A drag on a box's edge moves that edge (its low / high key, its low /
//   high velocity), on the box itself moves the whole box; it follows the
//   pointer here and the gesture's end posts the zone's four values once
//   (/fragment/sampler/zone, action=set; the server clamps them). The
//   arrows on a focused box move it a key (Shift: its high edge, Alt: its
//   low edge), up / down by 8 velocities; a burst of them is one post.
//   The panel comes back out of band by its region watch: focus is put
//   back on the same zone's new box.
// - A box and its row light each other (hover, focus); a click on a box
//   brings its row into view.
// - What a played key sounded — the live voice's own pick, livefx.js's
//   `synthlab:zone` (the zones, the key, the velocity step, the strike) —
//   lights those boxes and rows (`.hit`, as a struck key) and is written
//   on the map (`data-last`, `data-last-key`, `data-last-rr`, `data-hits`).
// Budget: <= 112 code lines.

(() => {
  "use strict";
  if (typeof document === "undefined") return;
  const S = () => window.synthlab;
  const EDGES = ["lo", "hi", "vlo", "vhi"];
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const range = (z) => Object.fromEntries(EDGES.map((k) => [k, Number(z.dataset[k])]));
  let drag = null, held = null, keyTimer = 0, refocus = null;

  // a box at its keys and velocities (128 steps across, 128 up)
  function place(z, r) {
    for (const k of EDGES) z.dataset[k] = String(r[k]);
    Object.assign(z.style, { left: `${r.lo / 1.28}%`, width: `${(r.hi - r.lo + 1) / 1.28}%`,
      bottom: `${r.vlo / 1.28}%`, height: `${(r.vhi - r.vlo + 1) / 1.28}%` });
    z.setAttribute("aria-valuenow", String(r.lo));
  }
  function post(z, r, from) {
    if (EDGES.every((k) => r[k] === from[k])) return;
    refocus = document.activeElement === z ? { i: z.dataset.i, until: Date.now() + 5000 } : null;
    S().post("/fragment/sampler/zone", { index: z.dataset.i, action: "set", ...r });
  }
  // a pointer on the plane as a key and a velocity
  const at = (box, e) => ({ k: clamp(Math.floor((e.clientX - box.left) / box.width * 128), 0, 127),
    v: clamp(Math.floor((box.bottom - e.clientY) / box.height * 128), 0, 127) });

  document.addEventListener("pointerdown", (e) => {
    const z = e.target.closest?.("#zone-map .zone");
    if (!z || e.button !== 0) return;
    e.preventDefault();
    z.focus();
    const box = z.parentElement.getBoundingClientRect();
    drag = { z, box, edge: e.target.dataset?.edge || "", from: range(z), p0: at(box, e) };
    z.setPointerCapture(e.pointerId);
    z.classList.add("dragging");
  });
  document.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const { z, box, edge, from, p0 } = drag, p = at(box, e), r = { ...from };
    if (edge === "lo") r.lo = Math.min(p.k, from.hi);
    else if (edge === "hi") r.hi = Math.max(p.k, from.lo);
    else if (edge === "vlo") r.vlo = Math.min(p.v, from.vhi);
    else if (edge === "vhi") r.vhi = Math.max(p.v, from.vlo);
    else {
      const dk = clamp(p.k - p0.k, -from.lo, 127 - from.hi), dv = clamp(p.v - p0.v, -from.vlo, 127 - from.vhi);
      Object.assign(r, { lo: from.lo + dk, hi: from.hi + dk, vlo: from.vlo + dv, vhi: from.vhi + dv });
    }
    place(z, r);
  });
  const end = (e) => {
    if (!drag) return;
    const { z, from } = drag;
    drag = null;
    z.classList.remove("dragging");
    if (e.type === "pointercancel") place(z, from);
    else post(z, range(z), from);
  };
  document.addEventListener("pointerup", end);
  document.addEventListener("pointercancel", end);

  document.addEventListener("keydown", (e) => {
    const z = e.target.closest?.("#zone-map .zone");
    const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowDown: [0, -8], ArrowUp: [0, 8] }[e.key];
    if (!z || !d) return;
    e.preventDefault();
    const r = range(z), [dk, dv] = d;
    if (held?.z !== z) held = { z, from: { ...r } };
    if (e.shiftKey) Object.assign(r, { hi: clamp(r.hi + dk, r.lo, 127), vhi: clamp(r.vhi + dv, r.vlo, 127) });
    else if (e.altKey) Object.assign(r, { lo: clamp(r.lo + dk, 0, r.hi), vlo: clamp(r.vlo + dv, 0, r.vhi) });
    else {
      const k = clamp(dk, -r.lo, 127 - r.hi), v = clamp(dv, -r.vlo, 127 - r.vhi);
      Object.assign(r, { lo: r.lo + k, hi: r.hi + k, vlo: r.vlo + v, vhi: r.vhi + v });
    }
    place(z, r);
    clearTimeout(keyTimer); // a held or repeated arrow is one post
    keyTimer = setTimeout(() => { const h = held; held = null; if (h?.z.isConnected) post(h.z, range(h.z), h.from); }, 300);
  });

  // a box and its row, by zone
  const pair = (i) => document.querySelectorAll(`#zone-map .zone[data-i="${i}"], .zone-row[data-i="${i}"]`);
  for (const type of ["pointerover", "focusin"]) {
    document.addEventListener(type, (e) => {
      const el = e.target.closest?.("#zone-map .zone, .zone-row");
      if (!el && type === "pointerover") return;
      document.querySelectorAll("#sampler-panel .on").forEach((x) => x.classList.remove("on"));
      if (el) pair(el.dataset.i).forEach((x) => x.classList.add("on"));
    });
  }
  document.addEventListener("click", (e) => {
    const z = e.target.closest?.("#zone-map .zone");
    if (z) document.querySelector(`.zone-row[data-i="${z.dataset.i}"]`)?.scrollIntoView({ block: "nearest" });
  });

  addEventListener("synthlab:zone", (e) => {
    const map = document.getElementById("zone-map"), d = e.detail || {};
    if (!map || !Array.isArray(d.zones)) return;
    Object.assign(map.dataset, { last: d.zones.join(","), lastKey: String(d.key), lastVel: String(d.vel),
      lastRr: String(d.rr), hits: String((Number(map.dataset.hits) || 0) + 1) });
    for (const i of d.zones) {
      for (const x of pair(i)) {
        x.classList.remove("hit");
        void x.offsetWidth; // restart the fade on a quick repeat
        x.classList.add("hit");
        clearTimeout(x.hitT);
        x.hitT = setTimeout(() => x.classList.remove("hit"), 140);
      }
    }
  });

  // focus back on the zone's box once the panel came back
  document.addEventListener("htmx:after:settle", () => {
    const a = document.activeElement;
    if (!refocus) return;
    if (a && a !== document.body && a.isConnected) { // not swapped yet, or the player moved on
      if (!a.closest?.("#zone-map .zone")) refocus = null;
      return;
    }
    if (Date.now() < refocus.until) document.querySelector(`#zone-map .zone[data-i="${refocus.i}"]`)?.focus();
    refocus = null;
  });
})();
