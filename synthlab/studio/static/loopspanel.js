// synthlab/studio/static/loopspanel.js
// The Loops panel (Perform; loop-and-perform.md, "The Loops
// panel") and ▶ in time on every Loops list (the panel, the drawer's
// group, the keyboard dock's strip: fragments/loop_rows.html).
// - Open / folded / pinned: <html data-loops> = "folded" | "over" (open,
//   sliding over the page's left) | "pinned" (open, a column beside it).
//   Unset prefs follow the window: from 2200 px the panel is open and
//   pinned, below it folded and unpinned. Per browser (localStorage
//   `synthlab.loops`: {open, pin, kind, sort}). ◀ / the tab / Alt+O (from
//   anywhere on Perform but a field: Alt+L is the dock's layout) toggle it;
//   sliding over, a press outside it or Esc folds it.
// - Search, kind, ★, a tag, sort: over the rows' data-* (looplib.py). The
//   filter is a style rule (`#lp-filter`), outside the list, so a morph of
//   the region shows the rows already filtered; the sort moves the rows
//   after every swap. Search words are never kept (a reload shows all).
// - ▶ (.lp-play): on the next bar line of what plays (S.nextBar), else at
//   once — the take's audio when it is at the tempo playing (or nothing
//   plays), else POST /loop/preview, whose X-Preview-Url is a render at
//   that tempo (or the loop's own audio, made the first time). One
//   audition at a time (transport.js's oneShot).
// - A panel row drags onto Perform's grids: its Edit (notes) or Open
//   (a beat) is pressed, as if clicked.
// Budget: <= 150 code lines (the panel's state ~35, the filter
// and sort ~50, ▶ in time ~30, the drag ~20).

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  if (typeof document === "undefined") return;
  const root = document.documentElement, KEY = "synthlab.loops";
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch { prefs = {}; }
  const keep = (k, v) => { prefs[k] = v; try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* this page still shows it */ } };
  const wide = matchMedia("(min-width: 2200px)");
  const dock = () => document.getElementById("loops-dock");
  const view = { q: "", kind: prefs.kind || "all", star: false, tag: "", sort: prefs.sort || "date" };

  // ---- open, folded, pinned -------------------------------------------------------
  const isOpen = () => prefs.open ?? wide.matches;
  function lay() {
    const state = !isOpen() ? "folded" : (prefs.pin ?? wide.matches) ? "pinned" : "over";
    root.dataset.loops = state;
    const d = dock();
    if (!d) return;
    for (const b of d.querySelectorAll("[data-lp-toggle]")) b.setAttribute("aria-expanded", String(state !== "folded"));
    d.querySelector("[data-lp-pin]")?.setAttribute("aria-pressed", String(state === "pinned"));
  }
  function toggle(open = !isOpen()) {
    keep("open", open);
    lay();
    if (open) dock()?.querySelector("#lp-q")?.focus({ preventScroll: true });
  }
  wide.addEventListener?.("change", lay);

  // ---- search, kind, ★, tag, sort -----------------------------------------------------
  const quote = (s) => `"${s.replace(/["\\]/g, "\\$&")}"`;
  function filter() {
    const need = view.q.toLowerCase().split(/\s+/).filter(Boolean).map((w) => `[data-find*=${quote(w)}]`);
    if (view.kind !== "all") need.push(`[data-what=${quote(view.kind)}]`);
    if (view.star) need.push("[data-star]");
    if (view.tag) need.push(`[data-tags~=${quote(view.tag)}]`);
    let style = document.getElementById("lp-filter");
    if (!style) document.head.append(style = Object.assign(document.createElement("style"), { id: "lp-filter" }));
    style.textContent = need.map((n) => `#loops-panel .lp-row:not(${n})`).join(",") + (need.length ? " { display: none; }" : "");
    const d = dock(), rows = d ? [...d.querySelectorAll("#loops-panel .lp-row")] : [];
    const shown = need.length ? rows.filter((r) => need.every((n) => r.matches(n))).length : rows.length;
    const n = d?.querySelector(".lp-n");
    if (n) n.textContent = shown === rows.length ? String(rows.length) : `${shown} / ${rows.length}`;
    d?.toggleAttribute("data-none", rows.length > 0 && shown === 0);
  }
  // what is missing (no tempo, no key: the bank's slots, a beat) sorts last
  const num = (v, missing) => (Number(v) > missing ? Number(v) : 1e9);
  const BY = {
    date: (a, b) => Number(b.dataset.at) - Number(a.dataset.at), // newest first, the bank last
    name: (a, b) => a.dataset.title.localeCompare(b.dataset.title),
    bpm: (a, b) => num(a.dataset.bpm, 0) - num(b.dataset.bpm, 0),
    key: (a, b) => num(a.dataset.rank, -1) - num(b.dataset.rank, -1),
  };
  function sort() {
    const list = document.querySelector("#loops-panel .lp-panel-list");
    if (!list) return;
    const rows = [...list.children], by = BY[view.sort] || BY.date;
    const sorted = [...rows].sort((a, b) => by(a, b) || a.dataset.title.localeCompare(b.dataset.title));
    if (sorted.some((r, i) => r !== rows[i])) for (const r of sorted) list.append(r);
  }
  function paint() {
    const d = dock();
    if (!d) return;
    for (const b of d.querySelectorAll("[data-lp-kind]")) b.setAttribute("aria-pressed", String(b.dataset.lpKind === view.kind));
    for (const b of d.querySelectorAll("[data-lp-sort]")) b.setAttribute("aria-pressed", String(b.dataset.lpSort === view.sort));
    d.querySelector("[data-lp-star]")?.setAttribute("aria-pressed", String(view.star));
    for (const b of d.querySelectorAll(".lp-tagbar .lp-tag")) b.setAttribute("aria-pressed", String(b.dataset.tag === view.tag));
    filter(); sort();
  }

  // ---- ▶ in time -------------------------------------------------------------------
  async function hear(btn) {
    S.ensureCtx();
    const c = S.getCtx(), playing = S.isPlaying(), now = S.tempo();
    c.resume?.();
    let url = btn.dataset.takeUrl;
    const own = Number(btn.dataset.loopBpm) || 0;
    try {
      if (!url || (playing && own && now && Math.abs(own - now) > 0.01)) {
        const form = new FormData();
        for (const [k, v] of Object.entries(JSON.parse(btn.dataset.preview || "{}"))) form.append(k, v);
        if (playing && now) form.append("bpm", String(now));
        btn.setAttribute("aria-busy", "true");
        const res = await S.send("/loop/preview", form, { refused: "That did not play." });
        if (!res) return;
        url = res.headers.get("X-Preview-Url");
      }
      const buf = await S.fetchBuffer(url), src = c.createBufferSource();
      src.buffer = buf; src.connect(S.getMaster());
      const at = (S.isPlaying() && S.nextBar(c.currentTime, 0.05)) || c.currentTime;
      src.start(at);
      S.transport?.oneShot(url, src, buf.duration + (at - c.currentTime));
    } catch (err) { S.toast?.(`That did not play: ${err?.message || err}`, "error"); } finally { btn.removeAttribute("aria-busy"); }
  }

  // ---- the clicks, the keys, the drag ---------------------------------------------------
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (!(t instanceof Element)) return;
    const play = t.closest(".lp-play[data-preview]");
    if (play) return void hear(play);
    if (!t.closest("#loops-dock")) return;
    let b;
    if ((b = t.closest("[data-lp-toggle]"))) return toggle();
    if ((b = t.closest("[data-lp-pin]"))) { keep("pin", root.dataset.loops !== "pinned"); keep("open", true); return lay(); }
    if ((b = t.closest("[data-lp-kind]"))) { view.kind = b.dataset.lpKind; keep("kind", view.kind); return paint(); }
    if ((b = t.closest("[data-lp-sort]"))) { view.sort = b.dataset.lpSort; keep("sort", view.sort); return paint(); }
    if ((b = t.closest("[data-lp-star]"))) { view.star = !view.star; return paint(); }
    if ((b = t.closest(".lp-tag[data-tag]"))) { view.tag = view.tag === b.dataset.tag ? "" : b.dataset.tag; return paint(); }
    if (t.closest(".lp-reset")) { Object.assign(view, { q: "", kind: "all", star: false, tag: "" }); const q = document.getElementById("lp-q"); if (q) q.value = ""; return paint(); }
  });
  document.addEventListener("input", (e) => { if (e.target?.id === "lp-q") { view.q = e.target.value; filter(); } });
  window.addEventListener("keydown", (e) => {
    if (e.code === "KeyO" && e.altKey && !e.ctrlKey && !e.metaKey && !e.repeat && dock() && !S.isTypingTarget?.(e.target)) {
      e.preventDefault(); e.stopImmediatePropagation();
      return toggle();
    }
    if (e.key === "Escape" && root.dataset.loops === "over" && e.target.closest?.("#loops-dock") && !e.target.closest("[popover], dialog")) toggle(false);
  }, true);
  // sliding over the page, a press anywhere else folds it (not a menu, a prompt, the palette)
  document.addEventListener("pointerdown", (e) => {
    if (root.dataset.loops !== "over" || !dock() || e.target.closest?.("#loops-dock, [popover], dialog, #ctx-menu")) return;
    toggle(false);
  }, true);
  let dragged = null;
  document.addEventListener("dragstart", (e) => {
    dragged = e.target.closest?.("#loops-panel .lp-row[data-drag-loop]") || null;
    if (!dragged) return;
    e.dataTransfer.effectAllowed = "copy";
    e.dataTransfer.setData("text/x-synthlab-loop", dragged.dataset.dragLoop);
    root.dataset.lpDrag = dragged.dataset.what;
  });
  document.addEventListener("dragend", () => { dragged = null; delete root.dataset.lpDrag; });
  const target = (e) => (dragged && e.target.closest?.("#transport, #perform-drums")) || null;
  document.addEventListener("dragover", (e) => { if (target(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } });
  document.addEventListener("drop", (e) => {
    if (!target(e)) return;
    e.preventDefault();
    dragged.querySelector(".lp-edit")?.click(); // Edit (notes) or Open (a beat)
  });
  document.addEventListener("htmx:after:settle", () => { lay(); paint(); }); // (a swap always settles after)
  document.addEventListener("DOMContentLoaded", () => { lay(); paint(); });
})();
