// synthlab/studio/static/songcell.js
// The Song page's pattern-making island: what makes arranging
// cells and editing their patterns feel like one surface. song.js (the
// player and the arranger's roving cursor) had no room; this loads after it
// and talks to the page only through the DOM and htmx posts. Owns:
//   - shared patterns: the cells (and the pool entry) playing the pattern
//     under the pointer, the arranger's cursor or the drawer get `.same`,
//     so "which sections change if I edit this" is visible before opening it;
//   - keys on the focused arranger grid: N puts a new empty pattern in the
//     cursor cell (POST /song/cell/new), Ctrl+C / Ctrl+V copy a cell's
//     pattern to another cell by reference (POST /song/cell). Touch and
//     mouse have the drawer's pick list and "+ New" for both;
//   - the drawer across swaps: a newly opened cell is scrolled into view
//     (it sits below the arranger, off-screen on a short window), and a
//     region swap keeps keyboard focus on the control that was pressed
//     (a tool, a lane slider) and the horizontal scroll of the arranger and
//     of the drawer's grid, instead of dropping both;
//   - the marks scripts put on the arranger (song.js's cursor, open cell
//     and selected section, `.same`) after htmx's settle, which resets an
//     id'd element's class to the response's: a second Delete used to clear
//     the top-left cell, because the cursor's mark was gone.
//   - a section's ⋯ menu across its own posts: a control marked
//     `data-stay` (the repeat ±, a section's own chord tools) keeps the menu
//     open and the keyboard on it — the arranger's redraw replaces the
//     popover, so it is shown again. (It used to close after every ±.)
// Nothing here plays audio or holds song state: every edit is a route.
// 100 -> 110: the menu that stays open, and one line that lets a
// range's keys (songsel.js) go first.
// Budget: <= 110 code lines.

(() => {
  "use strict";
  const S = () => window.synthlab || {};
  const $ = (id) => document.getElementById(id);
  const grid = () => document.querySelector(".arranger-grid");
  const cursor = () => document.querySelector(".arr-cell.cursor");
  const onSong = () => !!$("song-topbar");

  // ---- shared patterns light up together ------------------------------------
  let hover = "";
  function light() {
    const d = $("song-drawer"), pid = hover || (d && !d.hidden && d.dataset.patternId) || "";
    document.querySelectorAll(".arr-cell.same, .pool-pattern.same").forEach((x) => x.classList.remove("same"));
    if (!pid) return;
    const esc = CSS.escape(pid);
    document.querySelectorAll(`.arr-cell[data-pattern-id="${esc}"], .pool-pattern[data-pattern-id="${esc}"]`)
      .forEach((x) => x.classList.add("same"));
  }
  const point = (pid) => { if (pid !== hover) { hover = pid; light(); } };
  const pidOf = (el) => el?.closest?.(".arr-cell[data-pattern-id], .pool-pattern")?.dataset.patternId || "";
  document.addEventListener("pointerover", (e) => { if (onSong() && e.pointerType !== "touch") point(pidOf(e.target)); });
  document.addEventListener("focusin", (e) => { if (onSong() && e.target !== grid()) point(pidOf(e.target)); });

  // ---- keys on the arranger grid: N new, Ctrl+C / Ctrl+V copy a pattern -----
  let clip = null, regrid = false; // clip: {pid, kind} of the copied cell's pattern
  function post(path, values) {
    regrid = true;
    htmx.ajax("POST", path, { values, swap: "none" });
  }
  window.addEventListener("keydown", (e) => {
    const g = grid();
    if (!onSong() || !g || e.target !== g || e.altKey || e.repeat) return;
    const cell = cursor(), k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey;
    if (mod && S().songKeys?.(e, g)) return; // songsel.js: a range's Ctrl+C / V / D first
    if (!cell?.dataset.kind) return; // an audio track's cell holds takes
    const here = { section_id: cell.dataset.sectionId, track_id: cell.dataset.trackId };
    if (!mod && k === "n") post("/song/cell/new", here);
    else if (mod && k === "c" && cell.dataset.patternId) {
      clip = { pid: cell.dataset.patternId, kind: cell.dataset.kind };
      document.querySelector(".arr-cell.copied")?.classList.remove("copied");
      cell.classList.add("copied");
    } else if (mod && k === "v" && clip) {
      if (clip.kind !== cell.dataset.kind) return void S().toast?.(`A ${clip.kind} pattern can't go on this track`);
      post("/song/cell", { ...here, pattern_id: clip.pid });
    } else return;
    e.preventDefault();
    e.stopImmediatePropagation(); // not a piano key, not the browser's copy
  }, true);
  document.addEventListener("keydown", (e) => { if (e.target === grid()) point(pidOf(cursor())); });

  // ---- the drawer and the arranger across region swaps ----------------------
  // what had focus inside the drawer, as something a redrawn drawer can find
  const sig = (el) => [el.tagName, el.getAttribute("aria-label"), el.getAttribute("name"), el.getAttribute("hx-post"),
    el.getAttribute("hx-vals"), el.matches("button, summary") ? el.textContent.trim() : ""].join("|");
  let focused = "", drawerKey = "";
  const scrolls = new Map(); // "arr" | "grid" -> scrollLeft
  document.addEventListener("focusin", (e) => { focused = e.target.closest?.("#song-drawer") ? sig(e.target) : ""; });
  document.addEventListener("pointerdown", (e) => { if (!e.target.closest?.("#song-drawer")) focused = ""; regrid = false; });
  document.addEventListener("scroll", (e) => {
    const t = e.target;
    if (t.id === "arr-scroll") scrolls.set("arr", t.scrollLeft);
    else if (t.matches?.("#song-drawer .grid")) scrolls.set("grid", t.scrollLeft);
  }, true);
  function settle(ids) {
    const d = $("song-drawer"), open = d && !d.hidden;
    const key = open ? `${d.dataset.sectionId || ""}:${d.dataset.trackId || ""}:${d.dataset.patternId || ""}` : "";
    if (ids.has("song-arranger") || ids.has("view")) {
      const sc = $("arr-scroll");
      if (sc && scrolls.get("arr")) sc.scrollLeft = scrolls.get("arr");
      // after song.js's queued sync has made the new grid a tab stop
      if (regrid) queueMicrotask(() => { if (document.activeElement === document.body) grid()?.focus({ preventScroll: true }); });
    }
    if (ids.has("song-drawer") || ids.has("view")) {
      if (key !== drawerKey) { // another cell or pattern: its own scroll, and into view
        scrolls.delete("grid");
        // a drawer button made it (a pick, "+ New", Clear cell): stay in the drawer
        if (open && focused && document.activeElement === document.body) d.querySelector(".pick, .drawer-assign select, .drawer-rename input")?.focus({ preventScroll: true });
        if (open && ids.has("song-drawer")) d.scrollIntoView({ block: "nearest", behavior: window.synthlab.calm() ? "auto" : "smooth" });
      } else if (open) {
        const g = d.querySelector(".grid");
        if (g && scrolls.get("grid")) g.scrollLeft = scrolls.get("grid");
        const lost = focused && document.activeElement === document.body;
        const again = lost && [...d.querySelectorAll("button, input, select, summary, [tabindex]")].find((el) => !el.disabled && sig(el) === focused);
        if (again) again.focus({ preventScroll: true });
      }
    }
    drawerKey = key;
    light();
  }
  // htmx's settle puts every id'd element's class back to what the response
  // said, which drops the marks scripts add: the cursor (the next Delete then
  // cleared the top-left cell), the open cell, the selected section, .same
  document.addEventListener("htmx:after:settle", () => {
    if (!onSong()) return;
    const d = $("song-drawer"), open = d && !d.hidden;
    $(grid()?.getAttribute("aria-activedescendant"))?.classList.add("cursor");
    if (open) $(d.dataset.sectionId ? `cell-${d.dataset.sectionId}-${d.dataset.trackId}` : `pool-${d.dataset.patternId}`)?.classList.add("open");
    $(`sec-${S().songClock?.().sel}`)?.classList.add("selected");
    light();
  });
  // a section's menu stays open across its own ± posts (`data-stay`): the
  // redraw replaces the popover, so it is shown again from its ⋯ button,
  // with the control that was pressed (or its neighbour) under the keyboard
  let stay = null;
  document.addEventListener("htmx:before:request", (e) => {
    const b = e.target.closest?.("[data-stay]");
    stay = b ? { id: b.closest("[popover]")?.id, sig: sig(b) } : null;
  });
  document.addEventListener("htmx:after:settle", () => {
    const pop = stay && $(stay.id), rows = pop ? [...pop.querySelectorAll("[data-stay]:not(:disabled)")] : [];
    const opener = pop && document.querySelector(`[popovertarget="${stay.id}"]`);
    if (pop && !pop.matches(":popover-open")) pop.showPopover({ source: opener });
    if (pop && !pop.contains(document.activeElement)) (rows.find((b) => sig(b) === stay.sig) || rows[0])?.focus({ preventScroll: true });
  });
  // the regions arrive out of band, so watch the DOM for them (as song.js
  // does); this runs before song.js's own queued sync, which then finds
  // focus already placed and leaves it
  const REGIONS = new Set(["view", "song-arranger", "song-drawer", "song-pool"]);
  new MutationObserver((recs) => {
    const ids = new Set(recs.flatMap((r) => [...r.addedNodes]).map((n) => n.id).filter((id) => REGIONS.has(id)));
    if (ids.size && onSong()) settle(ids);
  }).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", () => { if (onSong()) settle(new Set(["view"])); });
})();
