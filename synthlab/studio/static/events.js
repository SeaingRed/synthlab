// synthlab/studio/static/events.js
// The server-push island: one EventSource on GET /events (studio/events.py).
// Owns three things and nothing else:
//  - the page's id, sent as X-Studio-Page on every htmx request (and by the
//    islands' own fetch() posts through window.synthlab.pageHeaders, with
//    the page's HX-Current-URL as htmx sends it), so this page can ignore
//    the notice of its own edits;
//  - progress of the long renders (a song export, the stems, a cold
//    section) as one determinate line in #toasts — "Exporting song… 3/8" —
//    shown only once a task has run for SHOW_AFTER ms, so a warm render
//    never flashes one;
//  - `changed`: another page (a second tab, a phone on --share) committed
//    an edit. This page re-fetches its own view (GET /view/<name>, the
//    route the nav uses, morphed in so scroll and focus stay), debounced,
//    never mid-gesture: not while a pointer is down, a request is in
//    flight, a text field has focus or something is recording — it catches
//    up afterwards. Last write still wins; nothing is merged.
// The stream is closed while the tab is hidden (browsers allow six
// connections per origin, and a hidden tab needs none) and reopened when
// it shows again; `hello` carries the server's change count, so a page
// that missed something knows to catch up. Without EventSource, at the
// connection cap (503) or with the server gone, the page behaves exactly
// as it did before there was a stream: nothing is toasted, the browser's
// own reconnect (the stream's `retry:`) and a slow backoff here do the rest.
// Budget: <= 110 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const HEADER = "X-Studio-Page";
  const id = Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
  // + the page's URL, as htmx sends it on its own requests: the server reads
  // the asking view off it (`view_of`), and re-sends that page's regions —
  // without it a fetch() post reads as Synths' (Perform's Loops bin would
  // stay stale after a ⏺ Rec)
  S.pageHeaders = () => ({ [HEADER]: id, "HX-Current-URL": location.href });
  document.addEventListener("htmx:config:request", (e) => { e.detail.ctx.request.headers[HEADER] = id; });

  // ---- progress ------------------------------------------------------------
  const SHOW_AFTER = 400, LINGER = 2500, EXPIRE = 60000;
  const tasks = new Map(); // task id -> { since, el, wait, expire }
  function line(t, text, failed) {
    const box = document.getElementById("toasts");
    if (!box) return;
    if (!t.el) { t.el = document.createElement("p"); t.el.setAttribute("role", "status"); }
    t.el.className = failed ? "toast error" : "toast progress";
    t.el.textContent = text;
    if (!t.el.isConnected) box.append(t.el); // an error toast replaced the box's children
  }
  function drop(tid) {
    const t = tasks.get(tid);
    if (!t) return;
    clearTimeout(t.wait); clearTimeout(t.expire);
    t.el?.remove();
    tasks.delete(tid);
  }
  function progress(d) {
    let t = tasks.get(d.id);
    if (!t) tasks.set(d.id, (t = { since: Date.now() }));
    t.text = d.total > 1 ? `${d.label}… ${d.done}/${d.total}` : `${d.label}…`;
    clearTimeout(t.expire);
    t.expire = setTimeout(() => drop(d.id), EXPIRE); // its `done` was lost
    if (t.el || Date.now() - t.since >= SHOW_AFTER) line(t, t.text);
    else t.wait ??= setTimeout(() => line(t, t.text), SHOW_AFTER);
  }
  function finish(d, failed) {
    const t = tasks.get(d.id);
    if (!t) return;
    if (!t.el) return drop(d.id); // over before it was worth a line
    clearTimeout(t.wait); clearTimeout(t.expire);
    line(t, `${d.label} — ${failed ? "failed" : "done"}`, failed);
    setTimeout(() => drop(d.id), LINGER);
  }

  // ---- changed: catch up with an edit made somewhere else ---------------------
  const RECORDING = ["mic-btn", "song-rec", "jam-rec", "bend-rec", "xy-rec"].map((b) => `#${b}[aria-pressed="true"]`).join(", ");
  const TYPING = "textarea, [contenteditable], input:not([type=range], [type=checkbox], [type=radio], [type=button], [type=submit], [type=color], [type=file], [type=hidden])";
  let es = null, rev = null, tries = 0, timer = 0, stale = false, down = false, busy = 0;
  const view = () => document.getElementById("view")?.dataset.view;
  const held = () => down || busy > 0 || !!document.querySelector(RECORDING) || !!document.activeElement?.matches?.(TYPING);
  function need(delay) {
    stale = true;
    clearTimeout(timer);
    timer = setTimeout(sync, delay);
  }
  function sync() {
    if (!stale || document.hidden) return; // a hidden tab catches up when it shows
    if (held()) { timer = setTimeout(sync, 400); return; }
    const v = view();
    stale = false;
    if (!v || typeof htmx === "undefined") return;
    Promise.resolve(htmx.ajax("GET", `/view/${v}`, { target: "#view", swap: "outerMorph" }))
      .then(() => { if (v === "song") document.dispatchEvent(new CustomEvent("song-changed")); }) // the player re-reads its plan
      .catch(() => {});
  }
  function changed(d) {
    rev = d.rev ?? rev;
    if (d.origin === id) return; // this page's own edit: it already shows it
    // soon when it is this page's view, the whole page, or a region drawn
    // here too; later when the edit was made on another view
    const near = d.whole || d.view === view() || (d.regions || []).some((r) => document.getElementById(r));
    need(near ? 400 : 2500);
  }
  for (const type of ["pointerdown", "pointerup", "pointercancel"]) {
    window.addEventListener(type, () => { down = type === "pointerdown"; }, true);
  }
  document.addEventListener("htmx:before:request", () => { busy++; });
  document.addEventListener("htmx:finally:request", () => { busy = Math.max(0, busy - 1); });

  // ---- the stream -----------------------------------------------------------
  const on = (name, fn) => es.addEventListener(name, (e) => { try { fn(JSON.parse(e.data)); } catch {} });
  function open() {
    if (es || document.hidden || typeof EventSource === "undefined") return;
    es = new EventSource("/events");
    on("hello", (d) => { tries = 0; if (rev !== null && d.rev !== rev) need(400); rev = d.rev; });
    on("progress", progress);
    on("done", (d) => finish(d, false));
    on("failed", (d) => finish(d, true));
    on("changed", changed);
    on("resync", (d) => { rev = d.rev; for (const tid of [...tasks.keys()]) drop(tid); need(400); });
    es.onerror = () => {
      // CONNECTING: the browser is retrying by itself. CLOSED (a 503 at
      // the cap, a refused connection): try again later, quietly
      if (!es || es.readyState !== 2) return;
      close();
      setTimeout(open, Math.min(60000, 2000 * 2 ** tries++));
    };
  }
  function close() {
    es?.close();
    es = null;
    for (const tid of [...tasks.keys()]) drop(tid);
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) close(); else { open(); sync(); }
  });
  open();
  S.pageId = id;
})();
