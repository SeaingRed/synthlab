// synthlab/studio/static/colours.js
// The palette's theme, hues and the ladder's two dials, per browser
// (docs/frontend.md, "Colour: hues and depths" and "Themes"). style.css
// section 1 builds every colour from a hue a role (--hue-accent,
// --hue-signal, ...) plus --hue-shift, and every lightness from the written
// depth through --contrast and --black-level; a THEME is a named set of
// those dials there (`html[data-look="warm-tube"]`). This island puts the
// theme you chose on <html> (`data-look`), sets the dials you moved from it
// as <html>'s own, and keeps both in localStorage ("synthlab.colours": {
// theme: key, role: degrees, contrast: percent, black: percent }).
//   - Loaded WITHOUT defer, in <head>, after the stylesheets: the stored
//     values are on <html> before the first paint, so a page never flashes
//     the default's colours first. Everything else waits on the document.
//   - The Colours popover (fragments/colours.html, the rail's button): the
//     themes' swatches (`.look-sw`: one click applies a theme and lifts
//     every moved dial; each is a "Theme: <name>" command), then a slider
//     a role, then contrast and black level (`DEPTHS`: a slider's percent
//     is the variable's hundredth). The sliders move through the page's own
//     range handling (audio.js fills the track and writes the read-out;
//     tweak.js, the pen, a dial and menu.js reach it as any slider). A
//     slider's default (`data-default`, set each time the popover opens)
//     is the theme's own; a dial moved from it makes the theme "custom";
//     Reset lifts every one and keeps the theme.
//   - A change says so: `synthlab-colours` on document. What caches a
//     colour in a canvas reads it again on it — motion.js (the scope, the
//     spectrum) and deckart.js (both decks: the virtual one and the Stream
//     Deck +'s keys and strip).
//   - The top bar's visualisers at double height (#viz-size, "2×",
//     `synthlab.vizSize`): a look kept per browser like the hues, so it is
//     here, on <html> (`data-viz-size`) before the first paint.
//   - Motion: full | calm (`synthlab.motion`, the popover's last row): the
//     OS's prefers-reduced-motion is deliberately not followed (frontend.md,
//     "Motion: full or calm"); calm is `data-motion` on <html> before the
//     first paint, `synthlab.calm()` for the islands, and a toggle says so
//     (`synthlab-motion` on document).
// 60 -> 70: the 2× toggle. 70 -> 82: the themes —
// applied before the first paint, picked from a swatch, kept with the hues.
//   - The workspace's first paint (workspace.js is deferred): a page with a
//     saved layout ("synthlab.ws": a record keyed by the view, `editor@2`)
//     hides #view (`data-ws-wait`) until workspace.js has laid it out.
// 82 -> 96: Motion, full or calm. 96 -> 101: the
// workspace's wait, so workspace.js need not block the parser.
// Budget: <= 101 code lines.

(() => {
  "use strict";
  const root = document.documentElement, KEY = "synthlab.colours";
  const ROLES = ["shift", "neutral", "ink", "accent", "signal", "danger", "ai", "go", "pause", "glow"];
  const DEPTHS = { contrast: "--contrast", black: "--black-level" }; // percent on the slider
  const prop = (k) => DEPTHS[k] || `--hue-${k}`, scale = (k) => (DEPTHS[k] ? 100 : 1);
  const read = () => {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
  };
  const keep = (v) => {
    try {
      if (Object.keys(v).length) localStorage.setItem(KEY, JSON.stringify(v)); else localStorage.removeItem(KEY);
    } catch { /* storage refused (private mode): this page only */ }
  };
  const put = (k, n) => (n === undefined ? root.style.removeProperty(prop(k)) : root.style.setProperty(prop(k), String(n / scale(k))));
  // the theme: its key is style.css's `html[data-look="…"]` (Studio Teal, the default, has none)
  const look = (t) => { if (t && t !== "studio") root.dataset.look = t; else delete root.dataset.look; };
  const stored = read();
  if (typeof stored.theme === "string" && /^[a-z][a-z-]*$/.test(stored.theme)) look(stored.theme);
  for (const [k, n] of Object.entries(stored)) if ((ROLES.includes(k) || k in DEPTHS) && Number.isFinite(n)) put(k, n);
  // the workspace's wait: a saved layout for this page (any rev of it: a stale one is dropped and shown at once)
  try {
    const ws = JSON.parse(localStorage.getItem("synthlab.ws") || "{}") || {}, view = /^\/view\/([\w-]+)/.exec(location.pathname)?.[1] || "editor";
    if (Object.keys(ws).some((k) => k.split("@")[0] === view)) root.dataset.wsWait = "";
  } catch { /* storage refused: the server's order */ }
  const VIZ = "synthlab.vizSize", big = () => root.dataset.vizSize === "2";
  try { if (localStorage.getItem(VIZ) === "2") root.dataset.vizSize = "2"; } catch { /* storage refused */ }
  const showViz = () => document.getElementById("viz-size")?.setAttribute("aria-pressed", String(big()));
  document.addEventListener("DOMContentLoaded", showViz);
  document.addEventListener("click", (e) => {
    if (!e.target.closest?.("#viz-size")) return;
    if (big()) delete root.dataset.vizSize; else root.dataset.vizSize = "2";
    try { if (big()) localStorage.setItem(VIZ, "2"); else localStorage.removeItem(VIZ); } catch { /* this page only */ }
    showViz();
  });
  // Motion: full (the default, deliberately, whatever the OS asks) or
  // calm — `html[data-motion="calm"]`, style.css's calm rules,
  // and `synthlab.calm()`, which every island that moves asks
  const MOTION = "synthlab.motion", calm = () => root.dataset.motion === "calm";
  (window.synthlab ??= {}).calm = calm;
  try { if (localStorage.getItem(MOTION) === "calm") root.dataset.motion = "calm"; } catch { /* storage refused */ }
  const showMotion = () => { for (const b of document.querySelectorAll("[data-set-motion]")) b.setAttribute("aria-pressed", String((b.dataset.setMotion === "calm") === calm())); };
  document.addEventListener("DOMContentLoaded", showMotion);
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.("[data-set-motion]");
    if (!b || (b.dataset.setMotion === "calm") === calm()) return;
    if (calm()) delete root.dataset.motion; else root.dataset.motion = "calm";
    try { if (calm()) localStorage.setItem(MOTION, "calm"); else localStorage.removeItem(MOTION); } catch { /* this page only */ }
    showMotion();
    document.dispatchEvent(new CustomEvent("synthlab-motion", { detail: { calm: calm() } })); // what moves on its own hears it
  });
  const changed = () => document.dispatchEvent(new CustomEvent("synthlab-colours"));
  let quiet = false; // (sync's own input events: the page's range handling, not a change)
  // the theme's own value of one: read with the stored one lifted for a moment
  function own(k) {
    const was = root.style.getPropertyValue(prop(k));
    put(k);
    const n = Math.round((parseFloat(getComputedStyle(root).getPropertyValue(prop(k))) || 0) * scale(k));
    if (was) root.style.setProperty(prop(k), was);
    return n;
  }
  const SLIDERS = "input[data-hue], input[data-depth]", keyOf = (el) => el.dataset.hue || el.dataset.depth;
  // which theme is in force, and whether a dial was moved from it
  function badge(pop, v = read()) {
    const t = root.dataset.look || "studio", name = pop.querySelector(`.look-sw[data-look="${t}"] .look-name`);
    for (const sw of pop.querySelectorAll(".look-sw")) sw.setAttribute("aria-pressed", String(sw.dataset.look === t));
    const out = pop.querySelector("#look-now");
    if (out) out.textContent = (name?.textContent || t) + (Object.keys(v).some((k) => k !== "theme") ? " · custom" : "");
  }
  // the popover's sliders follow what is in force each time it opens
  function sync(pop) {
    const v = read();
    badge(pop, v);
    showMotion(); // (a body morph puts the template's back)
    for (const el of pop.querySelectorAll(SLIDERS)) {
      const k = keyOf(el);
      el.dataset.default = String(own(k));
      el.value = String(k in v ? v[k] : el.dataset.default);
      quiet = true;
      el.dispatchEvent(new Event("input", { bubbles: true })); // the track's fill, the read-out
      quiet = false;
    }
  }
  document.addEventListener("toggle", (e) => { if (e.target.id === "colours-pop" && e.newState === "open") sync(e.target); }, true);
  document.addEventListener("input", (e) => {
    const el = e.target.closest?.(`#colours-pop :is(${SLIDERS})`);
    if (!el || quiet) return;
    const v = read(), k = keyOf(el), n = Number(el.value);
    if (n === Number(el.dataset.default)) delete v[k]; else v[k] = n;
    put(k, v[k]);
    keep(v);
    badge(el.closest("#colours-pop"), v);
    changed();
  });
  // a theme's swatch (or its command) applies it whole; Reset goes back to its own
  document.addEventListener("click", (e) => {
    const sw = e.target.closest?.(".look-sw[data-look]");
    if (!sw && !e.target.closest?.("#colours-reset")) return;
    for (const k of [...ROLES, ...Object.keys(DEPTHS)]) put(k);
    if (sw) look(sw.dataset.look);
    keep(root.dataset.look ? { theme: root.dataset.look } : {});
    changed();
    const pop = document.getElementById("colours-pop");
    if (pop?.matches(":popover-open")) sync(pop);
  });
})();
