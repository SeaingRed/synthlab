// synthlab/studio/static/cliphub.js
// The clip hub's island (/view/clips: templates/views/clips.html). The
// page is server-drawn htmx; this keeps only what a page cannot say in
// markup, and owns no state the server needs:
// 1. ⟲ on the bench's player: the <audio>'s `loop`, kept across the
//    player's redraw (a tune re-asks for it with a new src), and a player
//    that was playing when a tune changed plays on from where it was.
// 2. The row just opened is the selected one (aria-selected) before its
//    bench arrives; the server draws it so on a full page.
// 3. A preview that arrives plays (▶ Preview was pressed to hear it); one
//    hub player starting pauses the others.
// 4. Ctrl+Z on the hub (not in a text field) is ↶ Undo (#hub-undo).
// The players themselves are bench.js's shared `.player`; ▶ in the list is
// audio.js's `.audition[data-note]` one-shot, as in the clip drawer — the
// two helpers a transport read-out can hook.
// 5. The row last heard or opened is remembered (the `hub_item` cookie):
//    the bench opens it on arrival, so it is never empty.
// 60 -> 66: 5.
// Budget: <= 66 code lines.

(() => {
  "use strict";
  const hub = () => document.querySelector("#view.hub-view");
  let looping = false;
  let resume = null; // {time} of a player a tune redraw interrupted

  function player() { return document.querySelector("#hub-player audio"); }
  function applyLoop() {
    const a = player(), btn = document.getElementById("hub-loop");
    if (a) a.loop = looping;
    btn?.setAttribute("aria-pressed", String(looping));
  }

  // 5. the row last heard or opened, kept per browser: a cookie the server
  //    reads (service/hubpage.py `hub_default`) to open it when the URL names none
  function remember(ref) {
    if (ref) document.cookie = `hub_item=${encodeURIComponent(ref)}; max-age=31536000; path=/; samesite=lax`;
  }

  document.addEventListener("click", (e) => {
    if (!hub()) return;
    if (e.target.closest?.("#hub-loop")) { looping = !looping; applyLoop(); return; }
    const row = e.target.closest?.(".hub-row");
    if (row && e.target.closest(".hub-open, .audition")) remember(row.dataset.ref);
    if (e.target.closest?.("#hub-player .player-play")) remember(document.getElementById("hub-bench")?.dataset.ref);
    const open = e.target.closest?.(".hub-open");
    if (open) {
      document.querySelectorAll(".hub-row[aria-selected]").forEach((r) => r.removeAttribute("aria-selected"));
      open.closest(".hub-row")?.setAttribute("aria-selected", "true");
      looping = false;
    }
  });

  document.addEventListener("htmx:before:request", (e) => {
    const src = e.target;
    if (!src?.closest?.("#hub-tune")) return;
    const a = player();
    resume = a && !a.paused ? { time: a.currentTime } : null;
  });

  document.addEventListener("htmx:after:settle", () => {
    if (!hub()) return;
    applyLoop();
    const a = player();
    if (a && resume) {
      const at = resume.time;
      resume = null;
      a.addEventListener("loadedmetadata", () => {
        a.currentTime = Math.min(at, Number.isFinite(a.duration) ? a.duration : at);
        a.play().catch(() => {});
      }, { once: true });
    }
    const fresh = document.querySelector("audio[data-hub-preview]:not([data-heard])");
    if (fresh) { fresh.dataset.heard = "1"; fresh.play().catch(() => {}); }
  });

  // one hub player at a time
  document.addEventListener("play", (e) => {
    if (!hub() || !(e.target instanceof HTMLAudioElement)) return;
    hub().querySelectorAll("audio").forEach((a) => { if (a !== e.target && !a.paused) a.pause(); });
  }, true);

  // in capture, before piano.js's Ctrl+Z (the reset menu's patch undo):
  // on the hub, the hub's own step is the newest thing done here
  window.addEventListener("keydown", (e) => {
    if (!hub() || !(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z" || e.shiftKey) return;
    if (e.target.closest?.("input, textarea, select, [contenteditable]")) return;
    const undo = document.getElementById("hub-undo");
    if (!undo) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    undo.click();
  }, true);
})();
