// synthlab/studio/static/slots.js
// Perform's launcher (loop-and-perform.md, "Slots and scenes"): lanes x scenes of slots, each
// a loop. The server compiles a launch into the working loop and answers
// with a queued preview (routes/slots.py); this island owns the hand:
// - The quantise (bar | 4 bars | loop, per browser: localStorage
//   `synthlab.slots`): audio.js asks `launchAt()` where a queued loop of
//   ours starts — the next point of the clip's grid after it arrived, so a
//   render that missed one lands on the next (never late, as the jam).
// - Queued: a pressed slot, scene or ■ marks its cells `data-q` (on top of
//   the server's `data-state="playing"`, which already names the new one)
//   until audio.js's `loop-switched`; re-applied after every swap (the
//   region comes back with the answer), dropped when the answer cued no
//   preview (a refusal). A launch from silence starts ▶.
// - One selection (loop-and-perform.md, "One selection"): a cell's click
//   (or Space, or Enter) selects it — and the selected cell IS what the
//   editor shows: a filled one posts its launch with `select` (its loop
//   opened on its lane — edits save to it — playing there from the
//   quantise point; the one already playing is only selected), an empty
//   one /fragment/perform/slot/select (its lane edited, a sketch). Notes |
//   Drums turns to its kind (grid.js `S.rack`; from Both too). It never
//   starts the transport: the deck's ▶ does (loopdeck.js: Loop plays the
//   selected cell's loop alone). A scene's ▸, Alt+1…8 and ■ never move
//   the editor.
// - The selected cell: `aria-selected`, kept per tab (sessionStorage
//   `synthlab.slotSel`) and drawn again after every swap. keyrec.js asks
//   `S.slotSel()` where ⏺ Rec, ⟲ Keep and the arp's print land (`free`:
//   the lane's next empty row, where "a new loop" goes from a filled one),
//   and `S.loopBar(t)` which bar of the playing loop a take began on;
//   `slot-selected` tells it (and the deck) the choice moved. A click on
//   the selected empty cell lets it go. An empty cell's ＋ (`.sl-plus`)
//   keeps its lane; the editor's "Save as loop" ([data-save-sketch]) does
//   the same into the selected empty cell of its lane, else /loop/save.
// - Drags: a Loops panel row onto a slot of its kind (place); a slot onto
//   another (move; Ctrl or Alt copies).
// - Keys: the grid is one tab stop with a roving focus — arrows move, Space
//   and Enter select (the button's own click), Shift+Enter the row's scene,
//   Delete / Backspace stop the lane; Alt+1…8 launch a scene and Alt+0
//   stops every lane from anywhere on Perform but a field (in the drum
//   pane Alt+1…4 stay steps.js's variations).
// - Scene → Song: every scene launch, logged when it sounds
//   (`loop-switched`) with the loop names its slots held, and every stop
//   (■ all, the transport stopping) on a bar clock that stands still while
//   nothing plays (the jam's rule); the button posts the log, and the
//   server's `scenes-captured` empties it. The page's own: it ends with it.
// Budget: <= 205 code lines (150 + the capture's log — 175;
// + the selected slot, S.slotSel / S.loopBar for ⏺ Rec into a slot — 200;
// one selection — a filled cell's click its launch, ▶ / double-click gone —
// with the sketch's "Save as loop" and `free` for Rec's new loop — 205).
(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  if (typeof document === "undefined") return;
  const root = document.documentElement, KEY = "synthlab.slots";
  const grid = () => document.getElementById("slots");
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch { prefs = {}; }
  const q = () => (["1", "4", "loop"].includes(prefs.q) ? prefs.q : "1");

  // ---- the quantise, and where a queued launch starts ------------------------------
  function paintQ() {
    for (const b of document.querySelectorAll("[data-sl-q]")) b.setAttribute("aria-pressed", String(b.dataset.slQ === q()));
  }
  let pending = false; // a launch of ours is waiting for its preview
  S.launchAt = () => {
    const c = S.getCtx?.(), t0 = S.loopStart?.(), dur = S.loopDuration?.();
    if (!pending || q() === "1" || !c || t0 == null || !dur) return undefined;
    pending = false;
    const bar = (S.barBeats() * 60) / (S.tempo() || 120), step = q() === "loop" ? dur : bar * 4;
    return t0 + Math.ceil((c.currentTime + 0.03 - t0) / step - 1e-6) * step;
  };

  // ---- the selected slot: where ⏺ Rec, ⟲ Capture and the arp's print land -------------
  const SEL = "synthlab.slotSel";
  let sel = null; // [lane, row] as strings
  try { sel = JSON.parse(sessionStorage.getItem(SEL) || "null"); } catch { sel = null; }
  function select(c) {
    const same = sel && c && sel[0] === c.dataset.lane && sel[1] === c.dataset.row;
    sel = c && !(same && c.querySelector(".sl-empty")) ? [c.dataset.lane, c.dataset.row] : null;
    try { sessionStorage.setItem(SEL, JSON.stringify(sel)); } catch { /* this page still has it */ }
    paintSel();
    document.dispatchEvent(new CustomEvent("slot-selected", { detail: S.slotSel() }));
  }
  function paintSel() {
    for (const c of document.querySelectorAll("#slots .sl-cell")) {
      const on = !!sel && c.dataset.lane === sel[0] && c.dataset.row === sel[1];
      if (c.getAttribute("aria-selected") !== String(on)) c.setAttribute("aria-selected", String(on));
    }
  }
  S.slotSel = () => {
    const c = sel && cell(...sel);
    if (!c) return null;
    const name = c.querySelector(".sl-slot:not(.sl-empty)")?.textContent.trim() || "", lane = Number(sel[0]), row = Number(sel[1]);
    const after = (r) => (r - row + 99) % 99; // the lane's next empty row after this one, round to the first
    const free = [...(grid()?.querySelectorAll(`.sl-cell[data-lane="${lane}"]`) || [])].filter((x) => x.querySelector(".sl-empty")).map((x) => +x.dataset.row).sort((a, b) => after(a) - after(b))[0];
    return { lane, row, kind: c.dataset.kind, filled: !!name, name, free: free ?? null, playing: c.dataset.state === "playing" };
  };
  // the bar of the playing loop at context time `t` (0: nothing plays)
  S.loopBar = (t) => {
    const t0 = S.loopStart?.(), dur = S.loopDuration?.(), bar = (S.barBeats() * 60) / (S.tempo() || 120);
    if (!S.isPlaying?.() || t0 == null || !dur) return 0;
    return Math.round((((t - t0) % dur) + dur) % dur / bar) % Math.max(1, Math.round(dur / bar));
  };

  // ---- queued: the cells a launch waits on --------------------------------------------
  let marks = [], waiting = false;
  const cell = (lane, row) => grid()?.querySelector(`.sl-cell[data-lane="${lane}"][data-row="${row}"]`);
  function paint() {
    for (const c of document.querySelectorAll(".sl-cell[data-q]")) delete c.dataset.q;
    for (const [lane, row, how] of marks) { const c = cell(lane, row); if (c) c.dataset.q = how; }
  }
  function playingIn(lane) { return grid()?.querySelector(`.sl-cell[data-lane="${lane}"][data-state="playing"]`); }
  function mark(btn) {
    const c = btn.closest(".sl-cell"), row = btn.closest(".sl-row")?.dataset.row;
    const lanes = [...(grid()?.querySelectorAll(".sl-heads .sl-lane") || [])].map((x) => x.dataset.lane);
    marks = [];
    if (btn.matches(".sl-slot")) marks.push([c.dataset.lane, c.dataset.row, "go"]);
    else if (btn.matches(".sl-scene")) {
      for (const lane of lanes) {
        const here = cell(lane, row), on = playingIn(lane);
        if (here?.querySelector(".sl-slot:not(.sl-empty)")) marks.push([lane, row, "go"]);
        else if (on) marks.push([on.dataset.lane, on.dataset.row, "stop"]);
      }
    } else if (btn.matches(".sl-stop, .sl-stop-all")) {
      const which = btn.matches(".sl-stop-all") ? lanes : [JSON.parse(btn.getAttribute("hx-vals")).lane];
      for (const lane of which) { const on = playingIn(lane); if (on) marks.push([on.dataset.lane, on.dataset.row, "stop"]); }
    }
    paint();
  }
  document.addEventListener("click", (e) => {
    const t = e.target instanceof Element ? e.target : null;
    const q2 = t?.closest("[data-sl-q]");
    if (q2) { prefs.q = q2.dataset.slQ; try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* this page still has it */ } return paintQ(); }
    if (t?.closest("[data-save-sketch]")) return void saveSketch();
    const c = t?.closest("#perform-slots .sl-cell"), filled = t?.closest("#perform-slots .sl-slot:not(.sl-empty)");
    // selected: the editor shows its kind — Notes or Drums, from Both too
    if (c && t.closest(".sl-slot")) S.rack?.(c.dataset.kind === "beat" ? "drums" : "notes");
    if (c && t.closest(".sl-slot, .sl-plus")) {
      if (!(filled && c.getAttribute("aria-selected") === "true")) select(c); // the one selection: what the editor shows
      if (filled) S.post("/fragment/perform/slot/launch", JSON.parse(filled.dataset.launch)); // its loop opened on its lane
      else if (sel && t.closest(".sl-slot")) S.post("/fragment/perform/slot/select", { lane: c.dataset.lane, row: c.dataset.row });
    }
    const btn = t?.closest("#perform-slots .sl-scene, #perform-slots .sl-stop, #perform-slots .sl-stop-all, #loops-dock .lp-scene") || filled;
    if (!btn || btn.disabled) return;
    // a cell that plays already is only selected (the server launches nothing); from silence nothing waits
    if (btn === filled && (c.dataset.state === "playing" || !S.isPlaying?.())) return;
    mark(btn);
    heard = btn.matches(".sl-scene") ? sceneOf(btn) : btn.matches(".sl-stop-all") ? {} : null;
    silent = !S.isPlaying?.(); // from silence ▶ starts it: no switch to wait for
    waiting = true; pending = !btn.matches(".sl-stop, .sl-stop-all") || S.isPlaying?.();
    if (btn.matches(".sl-scene, .lp-scene") && !S.isPlaying?.()) S.post("/fragment/preview", { view: "perform", play: "1" }); // a scene from silence starts the loop
  });
  // "Save as loop" (the editor's head, a sketch): into the selected empty cell of its lane, else a loop in the Loops panel
  function saveSketch() {
    const s = S.slotSel(), head = document.querySelector("#transport .ed-head");
    if (s && !s.filled && String(s.lane) === head?.dataset.lane) return void S.post("/fragment/perform/slot/keep", { lane: String(s.lane), row: String(s.row) });
    S.post("/loop/save", { lane: "1", bars: head?.dataset.bars || "1" });
  }
  document.addEventListener("preview-ready", (e) => {
    if (!e.detail?.queue) return;
    waiting = false;
    if (heard && silent) { logged(heard); heard = null; }
  });
  // refused (a 422's toast: another meter, an empty scene, a render that failed): nothing waits
  document.addEventListener("htmx:error", () => { if (waiting) { waiting = false; marks = []; pending = false; heard = null; paint(); } });
  document.addEventListener("loop-switched", () => { marks = []; pending = false; paint(); if (heard) logged(heard); heard = null; });

  // ---- ⟲ Capture: the scene launches as played → the song's sections -----------------
  let log = [], heard = null, silent = false, played = 0, since = null; // since: the context time the bar clock last started
  const now = () => S.getCtx?.()?.currentTime ?? 0;
  const bars = () => played + (since == null ? 0 : Math.max(0, (now() - since) * (S.tempo() || 120) / 60 / S.barBeats()));
  function sceneOf(btn) {
    const row = btn.closest(".sl-row"), slots = [...row.querySelectorAll(".sl-cell")].map((c) => c.dataset.loop || null);
    return { slots, name: btn.querySelector("small")?.textContent.trim() || `Scene ${+row.dataset.row + 1}` };
  }
  function paintCap() {
    const b = document.querySelector("#perform-slots .sl-capture"), n = log.filter((e) => e.slots).length;
    if (!b) return;
    b.disabled = !n;
    if (n) b.dataset.n = n; else delete b.dataset.n;
  }
  function logged(ev) {
    if (ev.slots && since == null) since = now(); // a launch from silence starts the clock
    log.push({ bar: Math.round(bars()), ...ev });
    if (!ev.slots && since != null) { played = bars(); since = null; } // a stop: the clock stands still
    if (log.length > 512) log.shift();
    paintCap();
  }
  document.addEventListener("transport-change", () => {
    const last = log.at(-1), on = !!S.isPlaying?.();
    if (!on && since != null) logged({});
    else if (on && since == null && !heard && last && !last.slots) logged({ ...log.findLast((e) => e.slots) });
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest?.("#perform-slots .sl-capture:not(:disabled)")) return;
    const end = log.at(-1)?.slots ? Math.ceil(bars() - 0.05) : log.at(-1).bar; // the bar in progress counts whole
    S.post("/fragment/perform/scene/capture", { events: JSON.stringify(log.map(({ bar, slots, name }) => (slots ? { bar, slots, name } : { bar }))), bars: String(Math.max(1, end)) });
  });
  document.addEventListener("scenes-captured", () => { log = []; played = 0; since = S.isPlaying?.() ? now() : null; paintCap(); });

  // ---- drags: a Loops panel row onto a slot, a slot onto a slot -------------------------
  let from = null;
  const fits = (c, kind) => c && (kind === "beat") === (c.dataset.kind === "beat");
  document.addEventListener("dragstart", (e) => {
    const b = e.target.closest?.("#slots .sl-slot[draggable]");
    if (!b) return;
    const c = b.closest(".sl-cell");
    from = { lane: c.dataset.lane, row: c.dataset.row, kind: c.dataset.kind };
    e.dataTransfer.effectAllowed = "copyMove";
    e.dataTransfer.setData("text/x-synthlab-slot", `${from.lane},${from.row}`);
  });
  document.addEventListener("dragend", () => { from = null; for (const c of document.querySelectorAll(".sl-cell[data-drop]")) delete c.dataset.drop; });
  const dropOn = (e) => {
    const c = e.target.closest?.("#slots .sl-cell");
    return fits(c, from ? from.kind : root.dataset.lpDrag) ? c : null;
  };
  document.addEventListener("dragover", (e) => {
    const c = dropOn(e);
    if (!c) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = from && !(e.ctrlKey || e.altKey) ? "move" : "copy";
    for (const x of document.querySelectorAll(".sl-cell[data-drop]")) if (x !== c) delete x.dataset.drop;
    c.dataset.drop = "";
  });
  document.addEventListener("drop", (e) => {
    const c = dropOn(e);
    if (!c) return;
    e.preventDefault();
    const at = { lane: c.dataset.lane, row: c.dataset.row };
    if (from) return void S.post("/fragment/perform/slot/move", { lane: from.lane, row: from.row, to_lane: at.lane, to_row: at.row, copy: e.ctrlKey || e.altKey ? "1" : "" });
    const ref = JSON.parse(e.dataTransfer.getData("text/x-synthlab-loop") || "{}");
    if (ref.name) S.post("/fragment/perform/slot/place", { ...at, name: ref.name });
  });

  // ---- keys: the roving focus, the grid's own keys, Alt+1…8 / Alt+0 --------------------
  let at = null; // the slot the focus is on: [lane, row]
  function rove() {
    const btns = [...(grid()?.querySelectorAll(".sl-slot") || [])];
    const here = (at && cell(...at)?.querySelector(".sl-slot")) || btns[0];
    for (const b of btns) b.tabIndex = b === here ? 0 : -1;
  }
  document.addEventListener("focusin", (e) => {
    const c = e.target.closest?.("#slots .sl-cell");
    if (c) { at = [c.dataset.lane, c.dataset.row]; rove(); }
  });
  const MOVES = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  window.addEventListener("keydown", (e) => {
    if (!grid() || e.ctrlKey || e.metaKey || e.repeat) return;
    const take = () => { e.preventDefault(); e.stopPropagation(); };
    if (e.altKey && !e.shiftKey) { // from anywhere on Perform but a field
      const d = /^Digit(\d)$/.exec(e.code)?.[1];
      if (!d || S.isTypingTarget?.(e.target)) return;
      take();
      if (d === "0") document.querySelector("#perform-slots .sl-stop-all")?.click();
      else grid().querySelectorAll(".sl-scene")[d - 1]?.click();
      return;
    }
    const c = e.target.closest?.("#slots .sl-cell");
    if (!c) return;
    const [lane, row] = [Number(c.dataset.lane), Number(c.dataset.row)];
    if (MOVES[e.key]) { take(); cell(lane + MOVES[e.key][0], row + MOVES[e.key][1])?.querySelector(".sl-slot")?.focus(); }
    else if (e.key === "Enter" && e.shiftKey) { take(); c.closest(".sl-row")?.querySelector(".sl-scene")?.click(); }
    else if (e.key === "Backspace" || e.key === "Delete") { take(); grid().querySelectorAll(".sl-stop")[lane]?.click(); }
  }, true); // capture: before piano.js hears the arrows or Alt+digit

  document.addEventListener("htmx:after:settle", () => { paint(); rove(); paintQ(); paintCap(); paintSel(); }); // (a swap always settles after)
  document.addEventListener("DOMContentLoaded", () => { paintQ(); rove(); paintSel(); });
})();
