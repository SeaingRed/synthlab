// synthlab/studio/static/songedit.js
// The Song page's undo keys and its "solo this cell" audition.
// song.js is full and songcell.js nearly, so this is the Song island's
// fourth file; it loads after them and talks to the page through the DOM,
// htmx posts and window.synthlab (merged onto, never replaced).
//   - Undo / redo: Ctrl+Z, Ctrl+Shift+Z and Ctrl+Y click the topbar's two
//     buttons (POST /song/undo, /song/redo: the song's own ring, every edit
//     one step). Window capture, so the key is neither the patch undo
//     (piano.js) nor the browser's; a text field keeps its own undo, and a
//     key pressed while a pointer is down or a drag is under way does
//     nothing (the gesture's own edit has not landed yet). `songEdit.key`
//     is the pure half, run under node by tests/studio/test_song_undo.py.
//   - The arranger is replaced by an undo's answer: if the grid had the
//     keyboard it gets it back (its cursor is song.js's and stays put), and
//     the topbar — a morph, so the pressed button keeps focus — gets its
//     play / loop marks back from the player.
//   - Solo this cell: the drawer's toggle loops GET /song/cell/<section>/
//     <track>.wav (that track alone, through its sound and FX) on audio.js's
//     master bus. It stops the song first, follows edits (every
//     song-changed re-fetches and swaps at the next loop start — one with
//     `now`, a sound swapped, where the loop stands), draws the
//     drawer grid's playhead, and stops when the drawer closes or shows
//     another cell, when the song or a loop preview starts, or on a second
//     press. It is a claim in the dock's read-out (transport.js): starting
//     it stops whatever played, and the dock's ■ stops it.
// Budget: <= 116 code lines.

(() => {
  "use strict";
  window.synthlab ??= {};
  const S = window.synthlab;

  // ---- which button a key means (pure) --------------------------------------
  // typing: the event's target is a text field; busy: a pointer is down or a
  // drag is in progress. "" = not ours (leave the event alone), "wait" = ours
  // but not now (swallow it), else the button's id.
  function key(e, typing, busy) {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || typing) return "";
    const k = String(e.key || "").toLowerCase();
    const id = k === "z" ? (e.shiftKey ? "song-redo" : "song-undo") : k === "y" && !e.shiftKey ? "song-redo" : "";
    return id && (busy || e.repeat) ? "wait" : id;
  }
  Object.assign(S, { songEdit: { key } });
  if (typeof document === "undefined") return;

  const $ = (id) => document.getElementById(id);
  const onSong = () => !!$("song-topbar");
  const grid = () => document.querySelector(".arranger-grid");

  // ---- undo / redo ----------------------------------------------------------
  let down = false, dnd = false, regrid = false;
  const off = (b) => b?.getAttribute("aria-disabled") === "true";
  window.addEventListener("pointerdown", () => { down = true; regrid = false; }, true);
  for (const t of ["pointerup", "pointercancel", "blur"]) window.addEventListener(t, () => { down = false; }, true);
  document.addEventListener("dragstart", () => { dnd = true; });
  document.addEventListener("dragend", () => { dnd = false; });
  // a select is no text field: it has no undo of its own to keep
  const typing = (t) => !!S.isTypingTarget?.(t) && !/^select$/i.test(t.tagName || "");
  window.addEventListener("keydown", (e) => {
    const id = onSong() ? key(e, typing(e.target), down || dnd) : "";
    if (!id) return;
    e.preventDefault();
    e.stopImmediatePropagation(); // not the patch undo, not the browser's
    const b = $(id); // "wait": ours, but mid-gesture or a held key
    if (!b) return;
    if (off(b)) return void S.toast?.(b.title);
    regrid = document.activeElement === grid();
    b.click();
  }, true);
  // nothing to move: the buttons stay focusable (aria-disabled), so their
  // click is stopped here, before htmx posts it
  document.addEventListener("click", (e) => {
    if (off(e.target.closest?.(".song-undo button"))) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  document.addEventListener("htmx:after:settle", () => {
    const k = onSong() && S.songClock?.();
    if (!k) return;
    // the topbar morphs: its transport marks are the player's, not the server's
    $("song-play")?.setAttribute("aria-pressed", String(k.playing && !k.loop));
    $("song-loop")?.setAttribute("aria-pressed", String(k.loop));
  });
  // the arranger arrives out of band (watched in the DOM, as song.js does);
  // refocus after song.js's queued sync has made the new grid a tab stop
  new MutationObserver((recs) => {
    if (!regrid || !recs.some((r) => [...r.addedNodes].some((n) => n.id === "song-arranger"))) return;
    queueMicrotask(() => { if (document.activeElement === document.body) grid()?.focus({ preventScroll: true }); });
  }).observe(document.documentElement, { childList: true, subtree: true });

  // ---- solo this cell -------------------------------------------------------
  let solo = null; // {url, bus, src, t0, dur, gen}: what loops, and since when
  const btn = () => document.querySelector("#song-drawer:not([hidden]) [data-cell-solo]");
  function stopSolo() {
    const s = solo;
    if (!s) return;
    solo = null;
    S.transport?.release("solo");
    s.bus.gain.setTargetAtTime(0, S.getCtx().currentTime, 0.01);
    setTimeout(() => { try { s.src?.stop(); } catch {} s.bus.disconnect(); }, 80);
    document.querySelectorAll('[data-cell-solo][aria-pressed="true"]').forEach((b) => b.setAttribute("aria-pressed", "false"));
    document.querySelector("#song-drawer .grid .playhead")?.style.removeProperty("--phase");
  }
  // fetch the cell's loop (again after an edit) and start it: now, or where
  // the loop it replaces next wraps, so an edit never restarts the bar. A
  // swap that keeps the timing (`now`: a sound picked, the dock's edits)
  // takes over where the loop stands, over a 40 ms crossfade, as song.js's
  async function cue(s, now) {
    const my = ++s.gen, c = S.getCtx();
    try {
      const buf = await S.fetchBuffer(`${s.url}${s.url.includes("?") ? "&" : "?"}v=${my}`);
      if (solo !== s || my !== s.gen) return;
      const t = c.currentTime + 0.05, mid = Boolean(now && s.src && t > s.t0);
      const at = s.src && !mid ? s.t0 + Math.ceil((t - s.t0) / s.dur) * s.dur : t, into = mid ? (at - s.t0) % s.dur : 0;
      const src = c.createBufferSource(), g = c.createGain();
      src.buffer = buf; src.loop = true; src.connect(g).connect(s.bus); src.start(at, into % buf.duration);
      if (mid) for (const [x, a, b] of [[g, 0, 1], [s.g, 1, 0]]) { x.gain.setValueAtTime(a, at); x.gain.linearRampToValueAtTime(b, at + 0.04); }
      try { s.src?.stop(mid ? at + 0.05 : at); } catch {}
      Object.assign(s, { src, g, t0: at - into, dur: buf.duration });
    } catch (err) {
      if (solo === s && !s.src) { stopSolo(); S.toast?.("Couldn't render that cell on its own."); }
    }
  }
  function tick() {
    const s = solo, b = btn();
    if (!s) return;
    // a pool pattern's lane changed ("through"): the same pattern, heard through the new one
    if (b && b.dataset.cellSolo !== s.url && b.dataset.soloPattern && b.dataset.soloPattern === s.pid) { s.url = b.dataset.cellSolo; cue(s); }
    // the drawer closed or moved on; the song or a loop preview started
    if (!b || b.dataset.cellSolo !== s.url || S.songClock?.().playing || S.isPlaying?.()) return stopSolo();
    if (b.getAttribute("aria-pressed") !== "true") b.setAttribute("aria-pressed", "true"); // a morph reset it
    const ph = document.querySelector("#song-drawer .grid .playhead"), t = S.getCtx().currentTime - s.t0;
    if (ph && s.src && t >= 0) {
      const bars = Number(document.querySelector('#drawer-grid [name="bars"]')?.value) || 1;
      const d = $("song-drawer"), pos = ((t % s.dur) / s.dur) * (Number(b.dataset.cycleBars) || bars) - (Number(d.dataset.at) || 0);
      ph.hidden = pos < 0 || pos >= (Number(d.dataset.len) || Infinity); // a trimmed block: silent outside it
      ph.style.setProperty("--phase", ((Math.max(0, pos) % bars) / bars).toFixed(3));
    }
    requestAnimationFrame(tick);
  }
  async function startSolo(url, pid, label) {
    S.ensureCtx();
    const c = S.getCtx(), bus = c.createGain(), s = { url, pid, bus, src: null, t0: 0, dur: 1, gen: 0 };
    // one thing plays: the song, the loop, the jam stop (transport.js), and the dock names this
    S.transport?.claim("solo", { kind: "Cell", label, stop: stopSolo, pos: () => s.src && { text: "alone, looping", frac: ((c.currentTime - s.t0) % s.dur) / s.dur } });
    solo = s;
    bus.connect(S.getMaster());
    await c.resume();
    if (solo !== s) return;
    cue(s);
    requestAnimationFrame(tick);
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.("[data-cell-solo]");
    if (!b) return;
    const again = solo?.url === b.dataset.cellSolo;
    stopSolo();
    if (!again) startSolo(b.dataset.cellSolo, b.dataset.soloPattern, b.dataset.soloLabel || "this cell");
  });
  document.addEventListener("song-changed", (e) => { if (solo) cue(solo, e.detail?.now === true); });
})();
