// synthlab/studio/static/songdock.js
// The Song island's seventh file: the sound dock (fragments/song_dock.html —
// Synths in a frame beside the playlist while a track's sound is edited).
// The frame is a whole page of the studio and needs nothing from here to
// work; this file is the wire back from it: every request the framed page
// finishes (a slider, a sound loaded, an FX unit) may have changed the
// track, so the player is told — `song-changed` with {now}, which song.js
// answers by re-reading the plan and swapping the PLAYING pass for the
// edited one where it stands (any other edit waits for the next pass
// start). Debounced: a dragged knob is one plan. The frame is same-origin,
// so its document is listened to directly; a page that is itself in a
// frame wires nothing. (A lane's sound menu takes its list from the page's
// #lane-sounds in soundmenu.js, the menu's own island.)
// Budget: <= 46 code lines.

(() => {
  "use strict";
  if (window.top !== window) return;
  let timer = 0;
  const changed = () => {
    clearTimeout(timer);
    timer = setTimeout(() => document.dispatchEvent(new CustomEvent("song-changed", { detail: { now: true } })), 250);
  };
  const listen = (frame) => {
    try { frame.contentDocument?.addEventListener("htmx:after:request", changed); } catch {}
  };
  function wire() {
    const frame = document.querySelector("#song-dock .dock-frame");
    if (!frame || frame.dataset.wired) return;
    frame.dataset.wired = "1";
    frame.addEventListener("load", () => listen(frame)); // every document it loads
    if (frame.contentDocument?.readyState === "complete") listen(frame);
  }
  document.addEventListener("DOMContentLoaded", wire);
  document.addEventListener("htmx:after:settle", wire);
  // a block opened while the dock holds its lane (routes/song.py drawer_open):
  // the frame's ▶ plays that block now, so it renders it — a plain fetch from
  // the frame, not htmx (that would read as an edit, `changed` above)
  document.addEventListener("dock-reheard", async () => {
    const w = document.querySelector("#song-dock .dock-frame")?.contentWindow;
    if (!w?.synthlab) return;
    try {
      const body = new URLSearchParams({ view: "editor" });
      const res = await w.fetch("/fragment/preview", { method: "POST", body, headers: w.synthlab.pageHeaders?.() });
      const url = JSON.parse(res.headers.get("HX-Trigger") || "{}")["preview-ready"]?.url;
      if (url) w.document.dispatchEvent(new w.CustomEvent("preview-ready", { detail: { url } }));
    } catch { /* the frame is loading another lane */ }
  });
})();
