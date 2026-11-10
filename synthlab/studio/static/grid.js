// synthlab/studio/static/grid.js
// The loop editor's island: what makes the step grids feel like a channel
// rack / piano roll rather than a form of checkboxes. Owns:
//   - the grids' playheads and step LEDs: each grid maps the loop's phase
//     onto its OWN length (a 1-bar note grid inside a 2-bar mixed loop used
//     to sweep at half speed), and the column under the playhead lights;
//   - row labels you can play: press a pitch to hear it on the live voice
//     (window.synthlab.piano), a drum name to hit that voice;
//   - a lone right-click erases a step (a right-DRAG erases in audio.js's
//     paint stroke) and never opens the browser's menu over a grid;
//     (anywhere else a right click is menu.js's: the context menu);
//   - Ctrl+Z on a page with pattern tools = the pattern undo, not the
//     patch's (routes/pattern.py);
//   - the GSAP motion of the pattern tools: a shift slides the notes, a
//     transpose lifts them, ×2 fans the copy in, clear sweeps them away, and
//     a toggled step pops. Skipped under calm motion (colours.js);
//   - cuts (Shift/Alt-click: a new note inside a held run), keys on a
//     focused DRUM step (Shift+arrows shift; on the piano roll the arrows
//     are roll.js's: Shift selects, Alt moves), and holding a tool
//     click until a pending grid stroke has posted;
//   - a finger held on a step of the piano roll is handed to roll.js
//     (`roll-press`) instead of erasing: this stays the grids' one
//     `contextmenu` listener;
//   - the rack's focus on Perform: Notes | Drums | Both (Alt+G).
// 170 -> 200: cuts, the step keys and the stroke/tool ordering.
// Budget: <= 200 code lines.

(() => {
  "use strict";
  const S = () => window.synthlab || {};
  const calm = () => window.synthlab.calm(); // the Motion setting (colours.js), never the OS's
  const G = () => (!calm() && window.gsap) || null;
  const NAMES = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
  const midiOf = (p) => {
    const m = /^([A-G])([#b]?)(-?\d+)$/.exec(p);
    return m ? 12 * (Number(m[3]) + 1) + NAMES[m[1]] + (m[2] === "#" ? 1 : m[2] === "b" ? -1 : 0) : null;
  };

  // ---- playheads + step LEDs ------------------------------------------------
  // Grids are cached per settle: each with its playhead, its bar count and
  // its cells by column. The song drawer's grid runs on the song's clock
  // (song.js), so it is left alone.
  let grids = null, raf = 0;
  const bpm = () => Number(document.querySelector("#perform-transport [name=bpm], #drum-transport [name=bpm]")?.value) || 0;
  function scan() {
    grids = [...document.querySelectorAll(".grid")].filter((g) => !g.closest("#song-drawer")).map((g) => {
      const cols = [];
      for (const row of g.querySelectorAll(".gridrow:not(.sliderow)")) {
        row.querySelectorAll(".cell").forEach((c, i) => (cols[i] ??= []).push(c));
      }
      return { g, head: g.querySelector(".playhead"), bars: Number(g.dataset.bars) || 0, cols, lit: -1 };
    });
  }
  function light(x, step) {
    if (step === x.lit) return;
    for (const c of x.cols[x.lit] || []) c.classList.remove("now");
    for (const c of x.cols[step] || []) c.classList.add("now");
    x.lit = step;
  }
  function tick() {
    raf = 0;
    const s = S();
    if (!s.isPlaying?.() || s.loopStart?.() == null) return rest();
    raf = requestAnimationFrame(tick);
    if (document.hidden) return;
    if (!grids) scan();
    const dur = s.loopDuration(), t = s.getCtx().currentTime - s.loopStart();
    const loopBars = bpm() ? Math.max(1, Math.round((dur * bpm()) / 240)) : 0;
    for (const x of grids) {
      // the loop's position in bars, folded onto this grid's own length
      const own = x.bars && loopBars ? ((((t / dur) % 1) * loopBars) % x.bars) / x.bars : (t / dur) % 1;
      if (!(own >= 0)) continue;
      x.head?.style.setProperty("--phase", own.toFixed(4));
      // set once while it runs, never per frame: its module's lit edge (style.css 39)
      if (!x.running) x.running = !!x.g.toggleAttribute("data-running", true);
      if (!calm()) light(x, Math.floor(own * x.cols.length));
    }
  }
  function rest() {
    for (const x of grids || []) { x.head?.style.removeProperty("--phase"); light(x, -1); x.running = x.g.toggleAttribute("data-running", false); }
  }
  const wake = () => { if (!raf) raf = requestAnimationFrame(tick); };
  document.addEventListener("htmx:after:settle", () => { rest(); grids = null; wake(); });
  document.addEventListener("loop-switched", wake);
  document.addEventListener("click", (e) => { if (e.target.closest?.("#play, .play, .launch")) setTimeout(wake, 60); });
  setInterval(() => { if (S().isPlaying?.()) wake(); }, 500); // a start from anywhere (Space, MIDI)

  // ---- playable row labels ----------------------------------------------------
  let held = null;
  document.addEventListener("pointerdown", (e) => {
    const lab = e.target.closest?.(".grid .rowlabel[data-pitch], .grid .rowlabel[data-voice]");
    if (!lab || e.button !== 0) return;
    e.preventDefault();
    if (lab.dataset.voice) {
      const pad = document.querySelector(`.hit-pad[data-voice="${lab.dataset.voice}"]`);
      S().playUrl?.(pad?.dataset.hitUrl || `/drum/${lab.dataset.voice}/hit.wav`)?.catch?.(() => {});
      flash(lab);
      return;
    }
    const m = midiOf(lab.dataset.pitch);
    if (m == null || !S().piano) return;
    held = { lab, id: `grid:${lab.dataset.pitch}` };
    lab.classList.add("sounding");
    S().piano.noteOn(held.id, m, 0.9);
  });
  const letGo = () => {
    if (!held) return;
    S().piano?.noteOff(held.id);
    held.lab.classList.remove("sounding");
    held = null;
  };
  document.addEventListener("pointerup", letGo);
  document.addEventListener("pointercancel", letGo);
  function flash(el) {
    el.classList.add("sounding");
    setTimeout(() => el.classList.remove("sounding"), 140);
  }

  // ---- right-click erases (elsewhere it is menu.js's context menu) ----------
  document.addEventListener("contextmenu", (e) => {
    const cell = e.target.closest?.(".grid .cell");
    if (!cell) return;
    e.preventDefault();
    // a finger held on the piano roll is not an erase: the roll selects the note and asks menu.js for its menu (roll.js)
    if (e.pointerType === "touch" && cell.closest(".roll")) return void cell.dispatchEvent(new CustomEvent("roll-press", { bubbles: true, detail: { x: e.clientX, y: e.clientY } }));
    if (!cell.checked) return;
    cell.checked = false;
    cell.dispatchEvent(new Event("input", { bubbles: true }));
    G()?.fromTo(cell, { scale: 1.25 }, { scale: 1, duration: 0.18, ease: "power2.out", clearProps: "scale" });
  });

  // ---- a tool never overtakes a stroke ------------------------------------
  // The grids post 150 ms after the last input; a tool clicked inside that
  // window used to reach the server first, and the stroke's late post then
  // wrote the pre-tool grid back over the tool's result. Hold the click
  // until the stroke has gone (the server applies requests in order).
  let lastEdit = -1e9;
  document.addEventListener("input", (e) => { if (e.target.closest?.(".grid")) lastEdit = performance.now(); }, true);
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.(".pattern-tools [hx-post], .row-menu-panel [hx-post]");
    if (!b) return;
    if (b.dataset.flushed) return void delete b.dataset.flushed;
    const wait = 260 - (performance.now() - lastEdit);
    if (wait <= 0) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    setTimeout(() => { b.dataset.flushed = "1"; b.click(); }, wait);
  }, true);

  // ---- Shift/Alt-click: a new note starts here ------------------------------
  // A run of steps is one held note; a "cut" cell (its value posts "cut")
  // attacks again, so two notes can sit side by side. The click's own
  // toggle is cancelled: the step turns on as a cut, or a cut goes back to
  // plain (still on).
  document.addEventListener("click", (e) => {
    const cell = e.target.closest?.(".grid:not(.drum-grid-8) .gridrow:not(.sliderow) .cell");
    if (!cell || !(e.shiftKey || e.altKey) || cell.closest("[data-roll=select], [data-roll=stamp], [data-roll=erase]")) return; // those tools' clicks are roll.js's
    e.preventDefault(); // a cancelled click restores the box AFTER dispatch: act then
    const cut = !cell.checked && cell.getAttribute("value") === "cut"; // .checked is pre-toggled here
    setTimeout(() => {
      cell.checked = true;
      if (cut) cell.removeAttribute("value"); else cell.setAttribute("value", "cut");
      cell.dispatchEvent(new Event("input", { bubbles: true }));
      G()?.fromTo(cell, { scaleX: 0.6 }, { scaleX: 1, duration: 0.2, ease: "back.out(3)", clearProps: "scale" });
    }, 0);
  }, true);

  // ---- keys on a focused step: Shift+←/→ shift, Shift+↑/↓ transpose ------
  // (the drum grid's. On the piano roll Shift+arrows select and Alt+arrows
  // move the selection, or the whole pattern when there is none: roll.js)
  const KEY_TOOL = { ArrowLeft: "shift_left", ArrowRight: "shift_right", ArrowUp: "up", ArrowDown: "down" };
  document.addEventListener("keydown", (e) => {
    const cell = e.target.closest?.(".grid:not(.roll) .cell"), op = KEY_TOOL[e.key];
    if (!cell || !op || !e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
    const btn = cell.closest(BOX)?.querySelector(`.pattern-tools [hx-post$="/${op}"]`);
    if (!btn) return;
    e.preventDefault();
    btn.click();
  });

  // ---- Ctrl+Z: the pattern's undo where the pattern is edited ---------------
  window.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey || e.key.toLowerCase() !== "z") return;
    if (S().keysBlocked?.(e.target) && !e.target.closest?.(".grid")) return; // typing in a field
    const view = document.getElementById("view")?.dataset.view;
    const btn = (view === "perform" || view === "drums") && document.querySelector('[hx-post="/fragment/pattern/undo"]');
    if (!btn) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    btn.click();
  }, true);

  // ---- motion: the pattern tools, and a step's pop ----------------------------
  // A tool's answer replaces the grid (its region comes back OOB), so the
  // motion is armed on click and played on the next settle, against the
  // grid as it now stands.
  let armed = null;
  const BOX = ".transport, .perform-drums, #drum-bank, #song-drawer";
  const gridOf = (btn) => btn.closest(BOX)?.querySelector(".grid");
  document.addEventListener("click", (e) => {
    const btn = e.target.closest?.("[data-anim]");
    if (!btn || btn.disabled) return;
    const g = gridOf(btn), kind = btn.dataset.anim;
    if (!g) return;
    armed = { box: g.closest(BOX)?.id || null, kind };
    const lit = g.querySelectorAll(".cell:checked");
    if (kind === "clear" && G() && lit.length) {
      // clearProps: the Song drawer is morphed, not replaced — its cells are these same nodes, now empty
      G().to(lit, { scale: 0.3, autoAlpha: 0, duration: 0.16, stagger: { each: 0.004, from: "random" }, ease: "power1.in", clearProps: "transform,opacity,visibility" });
    }
  });
  document.addEventListener("htmx:after:settle", () => {
    const a = armed, g = G();
    if (!a) return;
    armed = null;
    const grid = (a.box && document.getElementById(a.box)?.querySelector(".grid")) || null;
    if (!g || !grid) return;
    const lit = [...grid.querySelectorAll(".cell:checked")];
    if (!lit.length) return;
    const w = lit[0].getBoundingClientRect().width + 3, h = lit[0].getBoundingClientRect().height + 3;
    const base = { duration: 0.22, ease: "power2.out", clearProps: "transform,opacity,visibility" };
    if (a.kind === "left") g.from(lit, { ...base, x: w });
    else if (a.kind === "right") g.from(lit, { ...base, x: -w });
    else if (a.kind === "up") g.from(lit, { ...base, y: h });
    else if (a.kind === "down") g.from(lit, { ...base, y: -h });
    else if (a.kind === "flip") g.from(lit, { ...base, scaleX: -1, duration: 0.3 });
    else if (a.kind === "grow" || a.kind === "roll") {
      g.from(lit, { ...base, scale: 0.2, autoAlpha: 0, duration: 0.3, stagger: { each: 0.01, from: a.kind === "grow" ? "start" : "random" } });
    }
  });
  // a step the hand turns on pops in; one turned off just goes
  document.addEventListener("change", (e) => {
    const cell = e.target;
    if (!cell.matches?.(".grid .cell") || !cell.checked) return;
    G()?.fromTo(cell, { scale: 0.7 }, { scale: 1, duration: 0.22, ease: "back.out(3)", clearProps: "scale" });
  });

  // ---- the rack's focus (Perform): Notes | Drums | Both ----------------------
  // One grid large and the other folded to its bar, or both: <html
  // data-rack> (the CSS reads it), per browser; Alt+G steps notes -> drums
  // -> both. Nothing posts: the server draws every button unpressed.
  const RACK = ["notes", "drums", "both"];
  function rack(to) {
    const v = RACK.includes(to) ? to : "both";
    document.documentElement.dataset.rack = v;
    try { localStorage.setItem("synthlab.rack", v); } catch { /* private mode */ }
    for (const b of document.querySelectorAll("[data-rack-show]")) b.setAttribute("aria-pressed", String(b.dataset.rackShow === v));
  }
  window.synthlab.rack = rack; // (slots.js: a selected slot turns it to its kind)
  try { rack(localStorage.getItem("synthlab.rack")); } catch { rack("both"); }
  document.addEventListener("htmx:after:settle", () => rack(document.documentElement.dataset.rack));
  document.addEventListener("click", (e) => { const b = e.target.closest?.("[data-rack-show]"); if (b) rack(b.dataset.rackShow); });
  window.addEventListener("keydown", (e) => {
    if (e.code !== "KeyG" || !e.altKey || e.ctrlKey || e.metaKey || e.repeat || !document.querySelector("[data-rack-show]") || S().isTypingTarget?.(e.target)) return;
    e.preventDefault();
    rack(RACK[(RACK.indexOf(document.documentElement.dataset.rack) + 1) % RACK.length]);
  });
})();
