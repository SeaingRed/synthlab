// synthlab/studio/static/perform.js
// Perform view island. Owns: QWER/ASDF -> beat pads (while the keyboard
// dock's typing → pads says so: piano.js `padsTyping`), the drum XY pad
// (posts hat decay / kick punch), and launcher button states
// (queued -> playing on audio.js's `loop-switched`). Budget: <= 150 code lines.

(() => {
  "use strict";
  const KEYS = { q: 0, w: 1, e: 2, r: 3, a: 4, s: 5, d: 6, f: 7 };

  // ---- pads: piano.js already plays any .hit-pad click; add the keymap ----
  // (only with typing → pads: by default typing plays the dock's keys, here as on every page)
  function flash(pad) {
    pad.classList.add("hit");
    setTimeout(() => pad.classList.remove("hit"), 120);
  }
  document.addEventListener("keydown", (e) => {
    if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
    if (window.synthlab.keysBlocked(e.target) || !window.synthlab.keysOn() || !window.synthlab.padsTyping()) return; // shared gates (piano.js)
    const pads = document.querySelectorAll("#perform-pads .hit-pad");
    const i = KEYS[e.key.toLowerCase()];
    if (i === undefined || !pads[i]) return;
    pads[i].click();
    flash(pads[i]);
  });
  document.addEventListener("pointerdown", (e) => {
    const pad = e.target.closest("#perform-pads .hit-pad");
    if (pad) flash(pad);
  });

  // ---- drum XY pad -------------------------------------------------------
  let down = false, last = 0;
  function place(pad, nx, ny) {
    pad.querySelector(".dxy-cross").style.cssText = `left:${nx * 100}%;top:${(1 - ny) * 100}%`;
    const out = document.getElementById("dxy-readout");
    if (out) out.textContent = `X ${pad.dataset.labelX} = ${Math.round(nx * 100)}% · Y ${pad.dataset.labelY} = ${Math.round(ny * 100)}%`;
  }
  function move(pad, e) {
    const r = pad.getBoundingClientRect();
    const nx = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    const ny = 1 - Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    place(pad, nx, ny);
    const now = Date.now();
    if (now - last > 80 || !down) {
      last = now;
      window.synthlab.post("/fragment/perform/xy", { x: nx, y: ny });
    }
  }
  document.addEventListener("pointerdown", (e) => {
    const pad = e.target.closest("#drumxy");
    if (!pad) return;
    e.preventDefault();
    try { pad.setPointerCapture(e.pointerId); } catch {}
    down = true;
    move(pad, e);
  });
  document.addEventListener("pointermove", (e) => {
    const pad = down && document.getElementById("drumxy");
    if (pad) move(pad, e);
  });
  const release = (e) => {
    if (!down) return;
    down = false;
    const pad = document.getElementById("drumxy");
    if (pad && e.clientX !== undefined) move(pad, e); // final send bypasses the throttle
  };
  document.addEventListener("pointerup", release);
  document.addEventListener("pointercancel", release);
  // keyboard: arrows nudge 2% (shift 10%) from the crosshair's current spot
  // (ARROWS is piano.js's table — it loads first; see window.synthlab.keys)
  document.addEventListener("keydown", (e) => {
    const pad = e.target.id === "drumxy" && e.target, d = window.synthlab.keys.ARROWS[e.key];
    if (!pad || !d) return;
    e.preventDefault();
    const cross = pad.querySelector(".dxy-cross"), step = e.shiftKey ? 0.1 : 0.02;
    const nx = Math.min(1, Math.max(0, parseFloat(cross.style.left) / 100 + d[0] * step));
    const ny = Math.min(1, Math.max(0, 1 - parseFloat(cross.style.top) / 100 + d[1] * step));
    place(pad, nx, ny);
    window.synthlab.post("/fragment/perform/xy", { x: nx, y: ny });
  });

  // ---- launcher: click = queued until the loop boundary lands ------------
  function setState(btn, state) {
    if (state) btn.dataset.state = state; else delete btn.dataset.state;
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".launch");
    if (!btn) return;
    document.querySelectorAll('.launch[data-state="queued"]').forEach((b) => setState(b, ""));
    setState(btn, "queued");
    if (!window.synthlab.isPlaying()) document.getElementById("play")?.click();
  });
  // part switches post with hx-swap="none": mirror the server's toggle
  // locally (it never lets the last part switch off either)
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".part-btn");
    if (!b) return;
    const on = b.getAttribute("aria-pressed") === "true";
    const others = [...document.querySelectorAll(".part-btn")].filter((x) => x !== b && x.getAttribute("aria-pressed") === "true");
    if (on && !others.length) return;
    b.setAttribute("aria-pressed", String(!on));
  });
  // (the bank slots and their Save-to-slot confirm are the Kit page's —
  // beats are the Loops panel's)
  // server-side "the action worked but the preview didn't" notices
  document.addEventListener("studio-toast", (ev) => {
    if (ev.detail?.message) window.synthlab.toast(ev.detail.message, ev.detail.kind);
  });
  document.addEventListener("loop-switched", () => {
    const queued = document.querySelector('.launch[data-state="queued"]');
    if (!queued) return;
    document.querySelectorAll('.launch[data-state="playing"]').forEach((b) => setState(b, ""));
    setState(queued, "playing");
  });

  // ---- performance FX -----------------------------------------------------
  // Momentary: hold a pad (or its key) to bend the playing loop, release to
  // snap back. audio.js owns the buffer and exposes loopFx; this owns the
  // gestures. Nothing posts to the server — that is the point, and it is
  // also why these never appear in an export.
  const TAPE_SEC = 0.7; // exponential slowdown, roughly a 7" spinning down
  const held = new Set();
  const bpmNow = () => Number(document.querySelector('#perform-transport [name="bpm"]')?.value) || 120;
  function fxOn(btn) {
    const fx = window.synthlab.loopFx;
    if (!btn || held.has(btn) || !fx?.active()) return;
    held.add(btn);
    btn.setAttribute("aria-pressed", "true");
    const kind = btn.dataset.fx;
    if (kind === "tape") fx.rate(0.02, TAPE_SEC);
    else if (kind === "half") fx.rate(0.5);
    else if (kind === "double") fx.rate(2);
    else if (kind === "reverse") fx.reverse(true);
    else if (kind === "stutter") fx.window(Number(btn.dataset.beats) || 1, bpmNow());
  }
  function fxOff(btn) {
    if (!btn || !held.has(btn)) return;
    held.delete(btn);
    btn.setAttribute("aria-pressed", "false");
    const fx = window.synthlab.loopFx;
    if (btn.dataset.fx === "reverse") fx.reverse(false);
    else fx.reset();
  }
  const allFxOff = () => [...held].forEach(fxOff);
  document.addEventListener("pointerdown", (e) => {
    const b = e.target.closest(".pfx");
    if (b) { e.preventDefault(); fxOn(b); }
  });
  document.addEventListener("pointerup", allFxOff);
  document.addEventListener("pointercancel", allFxOff);
  const fxByKey = (k) => document.querySelector(`.pfx[data-key="${k}"]`);
  // Ctrl/Cmd/Alt chords (Ctrl+C / V / P) are the browser's, never a pad
  document.addEventListener("keydown", (e) => {
    if (e.repeat || e.ctrlKey || e.metaKey || e.altKey || window.synthlab.keysBlocked(e.target) || !window.synthlab.keysOn() || !window.synthlab.padsTyping()) return;
    fxOn(fxByKey(e.key.toLowerCase()));
  });
  document.addEventListener("keyup", (e) => fxOff(fxByKey(e.key.toLowerCase())));
  // a held pad must not survive the view being swapped out from under it —
  // but after:swap also fires for every swap:"none" request (the drum XY
  // drag, a beat launch), so release only when the held pad itself is gone
  document.addEventListener("htmx:after:swap", () => { if ([...held].some((b) => !b.isConnected)) allFxOff(); });
  window.addEventListener("blur", allFxOff);
})();
