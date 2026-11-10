// synthlab/studio/static/zoom.js
// Zoom and pan for any `[data-zoom="<name>"]` scroller. The zoom is
// the scroller's step width: one of WIDTHS px, kept per browser and per name
// as `--zoom-<name>` on <html> (the scroller's own style reads it:
// `--step-w: var(--zoom-<name>)`), so a swap that redraws the grid changes
// nothing; "fit" removes it and the steps share the pane again. It owns
//   - Ctrl+wheel: a step wider or narrower about the pointer (a precision
//     touchpad's pinch arrives as Ctrl+wheel in small deltas: summed);
//     a plain wheel and Shift+wheel are the browser's own scroll (down,
//     sideways) and change no value;
//   - two fingers: pinch = zoom about their midpoint, moved together = pan;
//   - a middle-button drag pans;
//   - the − / fit / + buttons (`data-zoom-by`, `data-zoom-for`), and
//     Ctrl+= / Ctrl+− / Ctrl+0 while the focus is inside the scroller.
// The playhead and the step LEDs are the grid's own and need nothing from
// here: both are laid out by the same columns the zoom resizes.
// The playlist keeps its own SCALE (px a bar, "fit" = the whole song:
// songline.js) and takes its gestures from here: #arr-scroll is
// `[data-zoom="song"][data-zoom-own]`, and a step for such a scroller is
// sent to it as a `zoom-step` event instead of being applied.
// Budget: <= 70 code lines (tests/studio/test_island.py).
(() => {
  "use strict";
  const WIDTHS = [12, 16, 20, 28, 40, 56], root = document.documentElement, KEY = "synthlab.zoom.";
  const scroller = (name) => document.querySelector(`[data-zoom="${name}"]`);
  const saved = (name) => { try { return Number(localStorage.getItem(KEY + name)) || 0; } catch { return 0; } };
  // a scroller with a zoom scale of its own (`data-zoom-own`: the playlist's px a bar, songline.js)
  // takes the gesture as an event — `zoom-step` {dir: ±1, or 0 for fit; x} — and this sets nothing
  const own = (name, dir, x) => scroller(name)?.hasAttribute("data-zoom-own") && scroller(name).dispatchEvent(new CustomEvent("zoom-step", { bubbles: true, detail: { dir, x } }));
  // set the width (0 = fit), keeping what is under `x` (default: the middle) where it is
  function set(name, px, x) {
    if (!px && own(name, 0, x)) return;
    const sc = scroller(name), pad = sc?.querySelector(".rowlabel")?.offsetWidth || 0, r = sc?.getBoundingClientRect();
    const off = r ? (x ?? r.left + r.width / 2) - r.left - pad : 0, at = sc ? (sc.scrollLeft + off) / Math.max(1, sc.scrollWidth - pad) : 0;
    if (px) root.style.setProperty(`--zoom-${name}`, `${px}px`); else root.style.removeProperty(`--zoom-${name}`);
    try { if (px) localStorage.setItem(KEY + name, px); else localStorage.removeItem(KEY + name); } catch {}
    if (sc) sc.scrollLeft = at * (sc.scrollWidth - pad) - off;
  }
  // one notch: from the saved width, else from the width a step has now
  function step(name, dir, x) {
    if (own(name, dir, x)) return;
    const now = saved(name) || scroller(name)?.querySelector(".cell")?.getBoundingClientRect().width || WIDTHS[2];
    const i = WIDTHS.reduce((best, w, k) => (Math.abs(w - now) < Math.abs(WIDTHS[best] - now) ? k : best), 0);
    const next = WIDTHS[Math.max(0, Math.min(WIDTHS.length - 1, i + dir))];
    if (next !== saved(name)) set(name, next, x);
  }
  try { for (const k of Object.keys(localStorage)) if (k.startsWith(KEY)) set(k.slice(KEY.length), saved(k.slice(KEY.length))); } catch {}

  let acc = 0;
  document.addEventListener("wheel", (e) => {
    const sc = e.ctrlKey && e.target.closest?.("[data-zoom]");
    if (!sc) return;
    e.preventDefault(); // (not the browser's page zoom)
    acc += e.deltaY;
    if (Math.abs(acc) < 40) return;
    step(sc.dataset.zoom, acc < 0 ? 1 : -1, e.clientX);
    acc = 0;
  }, { passive: false });
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.("[data-zoom-by]");
    if (b) Number(b.dataset.zoomBy) ? step(b.dataset.zoomFor, Number(b.dataset.zoomBy)) : set(b.dataset.zoomFor, 0);
  });
  document.addEventListener("keydown", (e) => {
    const sc = e.ctrlKey && !e.altKey && e.target.closest?.("[data-zoom]"), dir = { "=": 1, "+": 1, "-": -1, 0: 0 }[e.key];
    if (!sc || dir === undefined) return;
    e.preventDefault();
    if (dir) step(sc.dataset.zoom, dir); else set(sc.dataset.zoom, 0);
  });

  // ---- two fingers (pinch, pan) and the middle button (pan) -------------------
  const down = new Map(); // pointerId -> {x, y}
  let pan = null, gap = 0;
  const two = () => { const [a, b] = [...down.values()]; return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, d: Math.hypot(a.x - b.x, a.y - b.y) }; };
  document.addEventListener("pointerdown", (e) => {
    const sc = e.target.closest?.("[data-zoom]");
    if (!sc || !(e.pointerType === "touch" || e.button === 1)) return;
    if (e.button === 1) e.preventDefault(); // (no autoscroll)
    down.set(e.pointerId, { x: e.clientX, y: e.clientY });
    pan = { sc, mid: e.pointerType !== "touch" };
    if (down.size !== 2) return;
    gap = two().d;
    e.stopPropagation(); // two fingers are a pan or a pinch: the second starts no stroke ...
    sc.dispatchEvent(new PointerEvent("pointercancel", { bubbles: true, pointerId: -1 })); // ... and the first one's ends here
  }, true);
  document.addEventListener("pointermove", (e) => {
    const was = down.get(e.pointerId);
    if (!was || !pan || !(pan.mid || down.size === 2)) return;
    const before = pan.mid ? was : two();
    down.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const now = pan.mid ? down.get(e.pointerId) : two();
    pan.sc.scrollLeft -= now.x - before.x;
    pan.sc.scrollTop -= now.y - before.y;
    if (pan.mid || Math.abs(Math.log(now.d / gap)) < 0.22) return; // a pinch: a notch every 25 % the fingers part
    step(pan.sc.dataset.zoom, now.d > gap ? 1 : -1, now.x);
    gap = now.d;
  }, true);
  for (const type of ["pointerup", "pointercancel"]) document.addEventListener(type, (e) => { down.delete(e.pointerId); if (!down.size) pan = null; }, true);
})();
