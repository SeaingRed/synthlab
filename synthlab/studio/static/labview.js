// synthlab/studio/static/labview.js
// The Sample Lab's lanes as a window on the song — Ctrl + wheel into the
// stems, bars and lines easy to see:
//  - Ctrl + wheel over the lanes (a trackpad's pinch is the same event in
//    Chromium) zooms round the pointer — the moment under it stays under
//    it; Shift + wheel, a sideways wheel, or a plain wheel while zoomed and
//    the lanes have no height to scroll, moves along the song. The page
//    zooms AT ONCE: the lanes' SVGs (drawn by the server over the span it
//    last drew, `#lab-wave[data-view-*]`) are stretched to the page's span
//    with a transform, and 220 ms after the wheel stops the span is posted
//    (`POST /lab/view action=set`), whose answer morphs in the lanes drawn
//    sharp over it. A wheel anywhere else is the page's own.
//  - the overview strip: the whole song, the box on it what the lanes
//    show; drag the box to scroll, press outside it to look there.
//  - the ruler and the lines, drawn here for whatever span is shown: bar
//    numbers every bar while one has 34 px, else every 2nd, 4th, 8th ...;
//    a line a bar (thinned when they crowd), stronger every 4th bar and
//    stronger again every 16th, and the beats once a beat has 7 px.
//  - every marker (`.lab-at[data-at]`, `data-to` for a band: the
//    selection on the ruler, its edges, the chops' edges) placed from its
//    time in seconds of the song; the playhead of whichever Lab <audio>
//    plays (`data-from` + its time); the pointer's line and its bar.beat.
//  - the lanes' height: a head's grip dragged (double-click: fit) or
//    Ctrl + Shift + wheel; kept per browser (localStorage).
// Shared with lab.js and labchop.js as `synthlab.lab`: `at(ev)` (the time
// under the pointer), `pct(t)`, `view()`, `data()`, `place()`.
// Budget: <= 200 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const KEY = "synthlab.lab.lanes";
  const $w = () => document.getElementById("lab-wave");
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const num = (w, k) => Number(w.dataset[k]) || 0;
  let cv = null; // the span the page shows {start, seconds}; null = the server's
  let timer = 0, posting = false, ov = null, grip = null, song = "";

  function data(w = $w()) {
    if (!w) return null;
    return { total: num(w, "total"), beat: num(w, "beat"), offset: num(w, "offset"), bars: num(w, "bars"),
      per: num(w, "perBar") || 4, start: num(w, "viewStart"), seconds: num(w, "viewSeconds") || num(w, "total"),
      min: num(w, "minView") || 1 };
  }
  function view() {
    const d = data();
    return cv || (d && { start: d.start, seconds: d.seconds });
  }
  const box = (w = $w()) => w.querySelector(".lab-over") || w;
  const pct = (t, v = view()) => ((t - v.start) / v.seconds) * 100;
  function at(ev) {
    const w = $w(), r = box(w).getBoundingClientRect(), v = view();
    return clamp(v.start + ((ev.clientX - r.left) / Math.max(1, r.width)) * v.seconds, 0, data(w).total);
  }
  const near = (a, b) => Math.abs(a.start - b.start) < 2e-3 && Math.abs(a.seconds - b.seconds) < 2e-3;
  const clock = (t) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
  function barBeat(d, t) {
    if (!d.beat) return "";
    const b = (t - d.offset) / d.beat;
    return b < 0 ? "before bar 1" : `bar ${Math.floor(b / d.per) + 1}.${Math.floor(b % d.per) + 1}`;
  }

  // ---- drawing: the lanes stretched, the markers, the ruler, the lines --------
  function place() {
    const w = $w();
    if (!w) return;
    const d = data(w), v = view();
    const k = d.seconds / v.seconds, x = ((d.start - v.start) / v.seconds) * 100;
    const tf = near(v, d) ? "" : `translateX(${x.toFixed(4)}%) scaleX(${k.toFixed(6)})`;
    for (const svg of w.querySelectorAll(".lab-track > svg")) svg.style.transform = tf;
    for (const el of w.querySelectorAll(".lab-at")) {
      const a = pct(+el.dataset.at, v);
      el.style.left = `${a.toFixed(3)}%`;
      if (el.dataset.to != null) el.style.width = `${(pct(+el.dataset.to, v) - a).toFixed(3)}%`;
      else el.hidden = a < -0.5 || a > 100.5;
    }
    const win = w.querySelector(".lab-ov-win");
    if (win) Object.assign(win.style, { left: `${(v.start / d.total) * 100}%`, width: `${(v.seconds / d.total) * 100}%` });
    const zoom = d.total / v.seconds, out = w.querySelector(".lab-zoom-readout");
    if (out) out.textContent = zoom > 1.05 ? `×${zoom < 10 ? zoom.toFixed(1) : Math.round(zoom)}` : "";
    grid(w, d, v);
    heights(w);
  }
  function grid(w, d, v) {
    const nums = w.querySelector(".lab-ruler-nums"), svg = w.querySelector(".lab-gridlines");
    if (!nums || !svg) return;
    const px = box(w).clientWidth || 1, bar = d.beat * d.per, spp = v.seconds / px;
    if (!d.beat) { nums.textContent = ""; svg.textContent = ""; return; }
    let every = 1;
    while ((every * bar) / spp < 34 && every < 4096) every *= 2;
    const first = Math.max(0, Math.ceil((v.start - d.offset) / bar - 0.06 / bar));
    const last = Math.min(d.bars, Math.floor((v.start + v.seconds - d.offset) / bar + 1e-6));
    const X = (t) => (((t - v.start) / v.seconds) * 1000).toFixed(1);
    const path = { beat: "", bar: "", four: "", sixteen: "" }, labels = [];
    const gap = { bar: bar / spp, four: (4 * bar) / spp, sixteen: (16 * bar) / spp };
    const beats = d.beat / spp >= 7;
    for (let i = first; i <= last; i++) {
      const t = d.offset + i * bar, cls = i % 16 === 0 ? "sixteen" : i % 4 === 0 ? "four" : "bar";
      // a line only where its kind is not crowded: bars under 4 px fall back to every 4th, 16th
      const show = cls === "bar" ? gap.bar >= 4 : cls === "four" ? gap.four >= 4 : true;
      if (show) path[cls] += `M${X(Math.max(t, v.start))} 0V1`;
      if (beats && i < d.bars) for (let b = 1; b < d.per; b++) path.beat += `M${X(t + b * d.beat)} 0V1`;
      if (i % every === 0) {
        const strong = i % (every * 4) === 0 || every === 1 && i % 4 === 0 ? " lab-bar-n4" : "";
        labels.push(`<span class="lab-bar-n${strong}" style="left: ${pct(Math.max(t, v.start), v).toFixed(3)}%">${i + 1}</span>`);
      }
    }
    nums.innerHTML = labels.join("");
    // a bar's line a light core in a dark sleeve (the page's own colours): it
    // reads over the dark well and over a bright wave alike
    const halo = (c, p) => (c === "beat" ? "" : `<path class="lg-halo lg-${c}-h" d="${p}"/>`);
    svg.innerHTML = Object.entries(path).map(([c, p]) => (p ? `${halo(c, p)}<path class="lg-${c}" d="${p}"/>` : "")).join("");
  }

  // ---- the span: zoomed or scrolled here at once, posted when it rests -----
  function zoomTo(start, seconds) {
    const w = $w(), d = data(w);
    if (!d) return;
    seconds = clamp(seconds, Math.min(d.min, d.total), d.total);
    cv = { start: clamp(start, 0, d.total - seconds), seconds };
    place();
    clearTimeout(timer);
    timer = setTimeout(send, 220);
  }
  function send() {
    timer = 0;
    const w = $w(), d = data(w);
    if (!w || !cv) return;
    if (posting) { timer = setTimeout(send, 120); return; }
    if (near(cv, d)) { cv = null; return; }
    posting = true;
    const whole = cv.seconds >= d.total - 1e-3, source = w.closest("form")?.elements.source?.value;
    const done = () => {
      posting = false;
      const now = data();
      if (cv && now && near(cv, now) && !timer) cv = null;
      place();
    };
    Promise.resolve(S.post?.("/lab/view", { source, action: whole ? "all" : "set",
      start: cv.start.toFixed(4), seconds: cv.seconds.toFixed(4) }, { target: "#lab-body", swap: "outerMorph" })).then(done, done);
  }
  document.addEventListener("wheel", (ev) => {
    const w = ev.target.closest?.("#lab-wave");
    if (!w || !ev.target.closest(".lab-track, .lab-over, .lab-ruler, .lab-strip, .lab-lane")) return;
    const d = data(w), v = view();
    const unit = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? 400 : 1;
    const dy = ev.deltaY * unit, dx = ev.deltaX * unit, delta = dy || dx;
    if (ev.ctrlKey && ev.shiftKey) { ev.preventDefault(); return resize(w, "stem", delta > 0 ? 0.9 : 1.1); }
    if (ev.ctrlKey) {
      ev.preventDefault();
      const t = at(ev), f = Math.exp(clamp(delta, -300, 300) * 0.002), s = clamp(v.seconds * f, Math.min(d.min, d.total), d.total);
      return zoomTo(t - ((t - v.start) / v.seconds) * s, s);
    }
    if (v.seconds >= d.total - 1e-6) return; // not zoomed: nothing to scroll along
    const sideways = ev.shiftKey || Math.abs(dx) > Math.abs(dy);
    if (!sideways && w.scrollHeight > w.clientHeight + 1) return; // the lanes scroll up and down
    ev.preventDefault();
    zoomTo(v.start + (delta / (box(w).clientWidth || 1)) * v.seconds, v.seconds);
  }, { passive: false });

  // ---- the overview strip, the grips, the pointer's line ------------------
  document.addEventListener("pointerdown", (ev) => {
    const strip = ev.target.closest?.("#lab-wave .lab-strip"), g = ev.target.closest?.("#lab-wave .lab-grip");
    if (ev.button !== 0 || !(strip || g)) return;
    ev.preventDefault();
    if (g) {
      const lane = g.closest(".lab-lane");
      grip = { kind: g.dataset.grip, y: ev.clientY, h: lane.querySelector(".lab-track")?.offsetHeight || lane.offsetHeight };
    } else {
      const d = data(), v = view(), r = strip.getBoundingClientRect(), t = ((ev.clientX - r.left) / r.width) * d.total;
      if (t < v.start || t > v.start + v.seconds) zoomTo(t - v.seconds / 2, v.seconds);
      ov = { r, total: d.total, grab: t - view().start };
    }
    (g || strip).setPointerCapture(ev.pointerId);
  });
  document.addEventListener("pointermove", (ev) => {
    if (ov) return zoomTo(((ev.clientX - ov.r.left) / ov.r.width) * ov.total - ov.grab, view().seconds);
    if (grip) return setHeight(grip.kind, clamp(grip.h + ev.clientY - grip.y, 22, 480));
    hover(ev);
  });
  const up = () => { if (grip) save(); ov = grip = null; };
  document.addEventListener("pointerup", up);
  document.addEventListener("pointercancel", up);
  document.addEventListener("dblclick", (ev) => {
    const g = ev.target.closest?.("#lab-wave .lab-grip");
    if (g) { setHeight(g.dataset.grip, 0); save(); }
  });
  function hover(ev) {
    const w = $w(), line = w?.querySelector(".lab-hover"), label = w?.querySelector(".lab-hover-t");
    if (!line || !label) return;
    const on = !!ev.target.closest?.("#lab-wave :is(.lab-track, .lab-ruler, .lab-over)");
    line.hidden = label.hidden = !on;
    if (!on) return;
    const t = at(ev), a = `${pct(t).toFixed(3)}%`;
    line.style.left = label.style.left = a;
    label.textContent = `${barBeat(data(w), t)} · ${clock(t)}`.replace(/^ · /, "");
  }

  // ---- the lanes' height (per browser) ----------------------------------------
  let sizes = {};
  try { sizes = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch { sizes = {}; }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(sizes)); } catch { /* private mode */ } };
  function setHeight(kind, px) { sizes[kind] = px || 0; heights($w()); }
  function resize(w, kind, by) {
    const lane = w.querySelector(kind === "mix" ? ".lab-mix .lab-track" : ".lab-lane:not(.lab-mix) .lab-track");
    if (lane) { setHeight(kind, clamp(Math.round(lane.offsetHeight * by), 22, 480)); save(); }
  }
  function heights(w) {
    if (!w) return;
    w.style.setProperty("--mix-h", sizes.mix ? `${sizes.mix}px` : "");
    w.style.setProperty("--lane-h", sizes.stem ? `${sizes.stem}px` : "");
  }

  // ---- the playhead of whichever Lab <audio> plays ---------------------------
  function head() {
    const w = $w(), ph = w?.querySelector(".lab-playhead");
    const a = [...document.querySelectorAll("#lab-region audio")].find((x) => !x.paused);
    if (!ph) return;
    ph.hidden = !a;
    if (!a) return;
    const t = (Number(a.dataset.from) || 0) + a.currentTime, x = pct(t);
    ph.hidden = x < 0 || x > 100;
    ph.style.left = `${x.toFixed(3)}%`;
    requestAnimationFrame(head);
  }
  document.addEventListener("play", (e) => { if (e.target.closest?.("#lab-region")) requestAnimationFrame(head); }, true);

  // ---- after a swap: the server's span is the page's, unless one is on its way
  function sync() {
    const w = $w(), id = w?.closest("form")?.elements.source?.value || "";
    if (id !== song) { song = id; cv = null; }
    if (!timer && !posting) cv = null;
    place();
  }
  document.addEventListener("htmx:after:settle", sync); // (a swap always settles after)
  document.addEventListener("DOMContentLoaded", sync);
  for (const ev of ["resize", "synthlab-geometry"]) addEventListener(ev, () => place());
  S.lab = { at, pct, view, data, place, zoomTo, clock, barBeat };
})();
