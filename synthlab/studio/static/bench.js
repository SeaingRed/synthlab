// synthlab/studio/static/bench.js
// The bench island (Synths, Bass, Voice): the parts
// of the lab look that need a script, and nothing that owns session state.
// Every one of them is a VIEW of controls the page already has, and moves
// them through their own events (`synthlab.tweak`, a select's `input` /
// `change`, a button's `click`), so the form posts, the undo ring, the OOB
// refresh and a hardware controller see exactly what a hand would do.
//
// 1. The mod matrix as a patch bay (#patchbay, in the Mod matrix module).
//    The four `.route` rows are the matrix: `modN_src`, `modN_dst`,
//    `modN_amt`. This draws one lead per active route from its source jack
//    to its target jack — coloured by source, dashed when the amount is
//    negative, the amount written on it — into `.pb-cables`, and redraws
//    when a route field changes, after a swap, and when the bay is resized
//    (a ResizeObserver): never per frame. A pointer or pen drag from jack
//    to jack patches: source -> target takes the first unused slot (and
//    gives it +50 % if its amount is 0); from a plugged target to another
//    re-routes that lead; from a plugged target to nowhere unplugs it. The
//    row's ± and ✕ buttons invert and unplug; a bypassed route (its switch,
//    `modN_on`, off) keeps its lead, faint.
// 2. `.player` (a shared component, style.css section 15): play / pause, a
//    seek bar, the time and (`.player-loop`, when it has one) loop on / off
//    for a control-less <audio> — the Voice take, the
//    Sample Lab's audition — instead of the browser's own slab. The
//    listeners are the document's, so it works on any view.
// 3. Small: the Bass spectrum stretches to its box; the sound agent's
//    drawer docks (#agent-pin, per browser), and — a manual popover, so
//    the browser does neither — opening it puts the focus in its field,
//    and Esc inside it closes it and gives the focus back to its button.
// (A typed value is tweaktype.js's, the context menu menu.js's, the
// `change` after a double-click reset tweak.js's: they were here while the
// bench was the only view with knobs.)
// 135 -> 138: the agent drawer's Esc and its focus on opening (a manual
// popover gets neither from the browser).
// 138 -> 146: the twelve mod routes — "+ route" opens the next of
// slots 5-12, and a lead's jacks are all marked before any is measured (a
// target jack shows while plugged).
// 4. A section at rest folds to its heading. A panel's run of rows under a
//    heading (an h4, or the legend down to the first h4) rests when the
//    engine hears none of it: every row is idle (paramui.idle's mark) or a
//    dial at 0 — Tremolo at depth 0, the Vowel at amount 0, an LFO nothing
//    routes. It is drawn as its heading alone; a click (Enter, Space) opens
//    it, and one opened or touched stays open for the page's life, so
//    nothing folds under the hand. No state: the patch says what rests.
// 146 -> 188: the sections at rest.
// 188 -> 190: a route's switch — a bypassed route's lead is drawn faint,
// and a lead dropped into a bypassed free slot switches it on.
// 5. The patch's switches on #view (`data-p-osc1` …): what Synths' shape
//    rows and jacks read, instead of a :has() on #view (frontend.md, "Page
//    weight"). 190 -> 200.
// Budget: <= 200 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const root = document.documentElement;
  const fire = (el, ...types) => types.forEach((t) => el.dispatchEvent(new Event(t, { bubbles: true })));

  // ---- 1. the patch bay ----------------------------------------------------
  const bay = () => document.getElementById("patchbay");
  const field = (slot, k) => document.querySelector(`:is(#patch, .patch-panel) [name="${slot}_${k}"]`);
  const routes = () => [...document.querySelectorAll("#mod-matrix .route")].map((r) => {
    const slot = r.dataset.slot;
    return { slot, src: field(slot, "src")?.value || "none", dst: field(slot, "dst")?.value, amt: +field(slot, "amt")?.value || 0,
      off: !!document.querySelector(`:is(#patch, .patch-panel) [name="${slot}_on"][value="off"]:checked`) };
  });
  const switchOn = (slot) => { const on = document.querySelector(`:is(#patch, .patch-panel) [name="${slot}_on"][value="on"]`); if (on && !on.checked) { on.checked = true; fire(on, "input", "change"); } };
  const set = (el, v) => { if (el && el.value !== v) { el.value = v; fire(el, "input", "change"); } };
  function lead(a, z, { src, amt = 0, slot = "", off = false }, cls = "") {
    const sag = Math.min(24, 6 + Math.abs(z[0] - a[0]) * 0.12), dx = z[0] - a[0];
    const d = `M${a[0]} ${a[1]}C${a[0] + dx * 0.3} ${a[1] + sag} ${a[0] + dx * 0.7} ${z[1] + sag} ${z[0]} ${z[1]}`;
    const text = slot ? `<text x="${a[0] + dx / 2}" y="${(a[1] + z[1]) / 2 + sag * 0.75 - 3}">${amt > 0 ? "+" : ""}${Math.round(amt * 100)}%</text>` : "";
    return `<g class="cable${amt < 0 ? " neg" : ""}${off ? " off" : ""}${cls}" data-signal="mod" data-src="${src}" data-slot="${slot}"><path d="${d}"/><circle cx="${a[0]}" cy="${a[1]}" r="2.2"/><circle cx="${z[0]}" cy="${z[1]}" r="2.2"/>${text}</g>`;
  }
  function draw(drag) {
    const b = bay(), box = b?.getBoundingClientRect();
    if (!box?.width) return;
    const at = (li) => { const r = li.querySelector(".jack").getBoundingClientRect(); return [+(r.left + r.width / 2 - box.left).toFixed(1), +(r.top + r.height / 2 - box.top).toFixed(1)]; };
    b.querySelectorAll(".pb-jack").forEach((li) => { delete li.dataset.on; li.classList.remove("aim"); });
    let svg = "";
    // every plugged jack first: a target jack shows while plugged (editor.html), so measure after
    const plugged = routes().map((r) => [r, b.querySelector(`[data-src="${r.src}"]`), b.querySelector(`[data-dst="${r.dst}"]`)])
      .filter(([, a, z]) => a && z); // (an unused slot has neither)
    for (const [r, a, z] of plugged) a.dataset.on = z.dataset.on = r.slot;
    for (const [r, a, z] of plugged) svg += lead(at(a), at(z), r);
    if (drag) {
      drag.over?.classList.add("aim");
      svg += lead(at(drag.li), [drag.x - box.left, drag.y - box.top], { src: drag.li.dataset.src || "none" }, " drag");
    }
    b.querySelector(".pb-cables").innerHTML = svg;
  }
  let queued = 0;
  const redraw = () => { queued ||= requestAnimationFrame(() => { queued = 0; draw(); }); };
  const watch = new ResizeObserver(redraw);
  function patch(a, to) {
    const rs = routes(), mine = a.dataset.dst ? rs.find((r) => r.slot === a.dataset.on) : null;
    if (mine) { // a lead pulled by its target end: out of the bay unplugs it, onto a jack moves it
      if (!to) return set(field(mine.slot, "src"), "none");
      return set(field(mine.slot, to.dataset.dst ? "dst" : "src"), to.dataset.dst || to.dataset.src);
    }
    const src = a.dataset.src || to?.dataset.src, dst = a.dataset.dst || to?.dataset.dst;
    if (!to || !src || !dst || rs.some((r) => r.src === src && r.dst === dst)) return;
    const slot = rs.find((r) => r.src === "none")?.slot;
    if (!slot) return void S.toast?.("All twelve routes are in use — unplug one (✕) first", "error");
    set(field(slot, "dst"), dst); set(field(slot, "src"), src); switchOn(slot); // a new lead plays
    if (!+field(slot, "amt").value) S.tweak?.(field(slot, "amt"), 0.5, true);
  }
  let pull = null;
  document.addEventListener("pointerdown", (e) => {
    const li = e.target.closest?.("#patchbay .pb-jack");
    if (!li || e.button !== 0) return;
    e.preventDefault();
    pull = { li, x: e.clientX, y: e.clientY };
  });
  document.addEventListener("pointermove", (e) => {
    if (!pull) return;
    const over = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("#patchbay .pb-jack");
    Object.assign(pull, { x: e.clientX, y: e.clientY, over: over !== pull.li ? over : null });
    draw(pull);
  });
  const drop = (e) => {
    if (!pull) return;
    const { li, over } = pull;
    pull = null;
    if (e.type === "pointerup" && (over || !bay()?.contains(document.elementFromPoint(e.clientX, e.clientY)))) patch(li, over);
    draw();
  };
  document.addEventListener("pointerup", drop);
  document.addEventListener("pointercancel", drop);
  document.addEventListener("click", (e) => { // "+ route": the next of slots 5-12 not shown
    if (!e.target.closest?.("[data-route-add]")) return;
    const next = [...document.querySelectorAll("#mod-matrix .route[data-extra]")].find((r) => !r.offsetParent);
    if (!next) return void S.toast?.("All twelve routes are showing — unplug one (✕) to reuse it", "error");
    next.dataset.open = ""; next.querySelector("select")?.focus();
  });
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.("[data-route]"), slot = b?.closest(".route")?.dataset.slot;
    if (!slot) return;
    if (b.dataset.route === "off") set(field(slot, "src"), "none");
    else S.tweak?.(field(slot, "amt"), -field(slot, "amt").value, true);
  });
  for (const type of ["input", "change"]) {
    document.addEventListener(type, (e) => { if (e.target.closest?.("#mod-matrix")) redraw(); });
  }

  // ---- 2. the player ---------------------------------------------------------
  const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  function tick(e) {
    const a = e.target, p = a.closest?.(".player");
    if (!p) return;
    const d = Number.isFinite(a.duration) ? a.duration : +p.querySelector(".player-bar").getAttribute("aria-valuemax") || 0;
    p.querySelector(".player-bar").style.setProperty("--at", d ? `${Math.min(100, (a.currentTime / d) * 100)}%` : "0%");
    p.querySelector(".player-bar").setAttribute("aria-valuenow", a.currentTime.toFixed(1));
    p.querySelector(".player-time").textContent = `${clock(a.currentTime)} / ${clock(d)}`;
    const btn = p.querySelector(".player-play");
    btn.setAttribute("aria-pressed", String(!a.paused));
    btn.textContent = a.paused ? "▶" : "⏸";
  }
  for (const type of ["timeupdate", "play", "pause", "ended", "loadedmetadata"]) document.addEventListener(type, tick, true);
  const seek = (bar, to) => { const a = bar.closest(".player").querySelector("audio"); if (Number.isFinite(a.duration)) a.currentTime = Math.max(0, Math.min(a.duration, to(a))); };
  document.addEventListener("click", (e) => {
    const p = e.target.closest?.(".player"), a = p?.querySelector("audio"), bar = e.target.closest(".player-bar");
    if (!a) return;
    const loop = e.target.closest(".player-loop");
    if (e.target.closest(".player-play")) a.paused ? a.play().catch(() => {}) : a.pause();
    else if (loop) loop.setAttribute("aria-pressed", String((a.loop = !a.loop)));
    else if (bar) seek(bar, (au) => ((e.clientX - bar.getBoundingClientRect().left) / bar.offsetWidth) * au.duration);
  });
  document.addEventListener("keydown", (e) => {
    const bar = e.target.closest?.(".player-bar"), by = { ArrowLeft: -1, ArrowRight: 1 }[e.key];
    if (bar && by) { e.preventDefault(); seek(bar, (a) => a.currentTime + by); }
  });

  // ---- 3. small things -----------------------------------------------------
  const store = { get: () => { try { return localStorage.getItem("synthlab.agent") === "1"; } catch { return false; } }, set: (on) => { try { localStorage.setItem("synthlab.agent", on ? "1" : "0"); } catch {} } };
  function dock(on) {
    if (on) root.dataset.agent = "pinned"; else delete root.dataset.agent;
    document.getElementById("agent-pin")?.setAttribute("aria-pressed", String(on));
  }
  document.addEventListener("click", (e) => {
    if (!e.target.closest?.("#agent-pin")) return;
    const on = !("agent" in root.dataset);
    store.set(on); dock(on);
    window.dispatchEvent(new Event("synthlab-geometry"));
  });
  function settle() {
    dock(store.get());
    for (const svg of document.querySelectorAll("svg.sub-spectrum:not([preserveAspectRatio])")) svg.setAttribute("preserveAspectRatio", "none");
    watch.disconnect();
    if (bay()) { watch.observe(bay()); redraw(); }
  }
  document.addEventListener("toggle", (e) => {
    if (e.target.id === "agent-drawer" && e.newState === "open") e.target.querySelector("#agent-message:not(:disabled), .ad-close")?.focus();
  }, true);
  document.addEventListener("keydown", (e) => {
    const d = document.getElementById("agent-drawer");
    if (e.key !== "Escape" || e.defaultPrevented || !d?.matches(":popover-open") || !d.contains(document.activeElement)) return;
    d.hidePopover();
    document.querySelector('[popovertarget="agent-drawer"]:not(.ad-close)')?.focus();
  });
  // ---- 4. a section at rest folds to its heading -----------------------------
  const HEADS = ":is(#patch, .patch-panel) h4.subhead, fieldset.patch-panel:not(#panel-mod-matrix) > legend";
  const opened = new Set();
  const keyOf = (h) => h.id || h.parentElement.id;
  const runOf = (h) => {
    const run = [];
    for (let n = h.nextElementSibling; n && !n.matches("h4.subhead"); n = n.nextElementSibling) run.push(n);
    return run;
  };
  const still = (c) => c.type === "range" && +c.value === 0;
  // every control on show in the run, in a row or not (an FM4 operator's dial, a wavetable's frame)
  const atRest = (run) => {
    const ctls = run.flatMap((n) => [...n.querySelectorAll("input, select")]).filter((c) => c.offsetParent && !c.closest("[popover]"));
    return ctls.length > 0 && ctls.every((c) => c.closest(".param[data-idle]") || still(c));
  };
  function rest() {
    const heads = [...document.querySelectorAll(HEADS)].map((h) => [h, runOf(h)]);
    for (const [, run] of heads) run.forEach((n) => n.removeAttribute("data-rested")); // measured as laid out unfolded
    const rests = heads.map(([h, run]) => !!h.offsetParent && atRest(run)); // every read before any write
    // a section folds as its page arrives (a head not yet `data-seen`: a load, a morph), and stays as it
    // was after: an edit's own swaps find the idle marks a beat behind, and never fold what is on show
    heads.forEach(([h, run], i) => {
      const may = !h.hasAttribute("data-seen") || h.dataset.rest === "folded";
      h.setAttribute("data-seen", "");
      const own = h.matches("h4") ? ["title"] : []; // (a legend's title is its panel's note)
      if (!rests[i]) { for (const a of ["data-rest", "tabindex", "role", "aria-expanded", ...own]) h.removeAttribute(a); return; }
      const fold = may && !opened.has(keyOf(h));
      h.dataset.rest = fold ? "folded" : "open";
      h.tabIndex = 0; h.setAttribute("role", "button"); h.setAttribute("aria-expanded", String(!fold));
      if (h.matches("h4")) h.title = "at rest: nothing here is heard now. Click to " + (fold ? "open it" : "fold it");
      if (fold) run.forEach((n) => n.setAttribute("data-rested", ""));
    });
  }
  const headAt = (e) => (e.target.closest?.("button, select, input, [popover]") ? null : e.target.closest?.("[data-rest]"));
  function flip(e) {
    const h = headAt(e);
    if (!h || (e.type === "keydown" && e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault();
    if (h.dataset.rest === "folded") opened.add(keyOf(h)); else { opened.delete(keyOf(h)); h.removeAttribute("data-seen"); }
    rest();
  }
  document.addEventListener("click", flip);
  document.addEventListener("keydown", flip);
  // a control moved: its section stays open, whatever its value comes to
  document.addEventListener("input", (e) => {
    const row = e.target.closest?.(":is(#patch, .patch-panel) .param");
    for (const h of row ? document.querySelectorAll(HEADS) : []) if (runOf(h).some((n) => n.contains(row))) opened.add(keyOf(h));
  });
  document.addEventListener("patch-idle", () => queueMicrotask(rest)); // (after audio.js has moved the idle marks)
  document.addEventListener("htmx:after:settle", settle);
  document.addEventListener("DOMContentLoaded", settle);
  for (const t of ["htmx:after:settle", "DOMContentLoaded"]) document.addEventListener(t, rest);

  // ---- 5. the switches on #view --------------------------------------------
  // `data-p-<field>` for each field #view names in `data-switches` (the
  // server draws them; editor.html's shape rows and jacks read them): kept
  // as the controls move, in the capture phase so every other island's
  // listener already reads the rows as they now show
  function switches() {
    const v = document.querySelector("#view[data-switches]");
    for (const n of v ? v.dataset.switches.split(" ") : []) {
      const c = v.querySelector(`select[name="${n}"], [name="${n}"]:checked`);
      if (c && v.getAttribute(`data-p-${n}`) !== c.value) v.setAttribute(`data-p-${n}`, c.value);
    }
  }
  for (const t of ["input", "change"]) document.addEventListener(t, switches, true);
  for (const t of ["htmx:after:settle", "DOMContentLoaded"]) document.addEventListener(t, switches);
})();
