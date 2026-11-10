// synthlab/studio/static/library.js
// The Library page's island (/view/library: templates/views/library.html;
// apart from audio.js, which is full).
// The page is server-drawn; the selection is a card's own `.lib-detail`,
// placed in the dock by CSS (style.css 9b1). This keeps what markup cannot:
// 1. The filter: the words and the tag chips, matched on the client so it
//    stays instant. A family the filter empties stays as one "no match"
//    line; each family's count reads "6 of 53"; each chip shows how many
//    sounds it would show with the words and the other chips (`data-n`);
//    "N of 149" in the filter row. Re-applied after every swap: a morph
//    brings the cards back with the server's `hidden`, which knows nothing
//    of the filter (Use on a filtered Library once left "bass" in the box
//    over all 96).
// 2. The arrow keys across the cards: ← / → the previous / next shown, ↑ / ↓
//    the card above / below, nearest in x — a grid now, not columns.
// 3. Cards or rows (the ▦ / ☰ toggle): per browser, on <html> so a swap
//    keeps it (`data-lib-view`).
// 4. The pilot lamp: a card is `.playing` while its audition sounds (the
//    `audition-start` event audio.js sends with keys.js's buffer source).
// 5. The dock's keys (`.lib-d-acts`, once a page): each presses the
//    selected card's own (`data-press` names its class) — ▶ in your mix,
//    + Layer, Use — so a card keeps its keys while it is the selection and
//    nothing is drawn twice per card (the card just clicked would lose
//    its Use if its keys moved into the dock).
// Budget: <= 75 code lines.

(() => {
  "use strict";
  const root = document.documentElement, KEY = "synthlab.libView";
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
  try { const v = localStorage.getItem(KEY); if (v) root.dataset.libView = v; } catch { /* blocked: cards */ }

  document.addEventListener("click", (e) => {
    const k = e.target.closest?.(".lib-d-acts [data-press]");
    if (k) document.querySelector(`.library-view .lib-card.active .${k.dataset.press}`)?.click();
  });

  function apply() {
    const box = document.getElementById("lib-search");
    if (!box) return;
    const q = box.value.trim().toLowerCase(), cards = $$(".library-view .lib-card");
    const chips = $$(".tagfilter[data-tag]").filter((b) => b.dataset.tag);
    const on = chips.filter((b) => b.getAttribute("aria-pressed") === "true").map((b) => b.dataset.tag);
    document.querySelector('.tagfilter[data-tag=""]')?.setAttribute("aria-pressed", String(!on.length)); // "all"
    const rows = cards.map((c) => ({ c, tags: (c.dataset.tags || "").split(" "), words: !q || `${c.dataset.name} ${c.dataset.tags}`.toLowerCase().includes(q) }));
    let shown = 0;
    for (const r of rows) { r.c.hidden = !(r.words && on.every((t) => r.tags.includes(t))); shown += !r.c.hidden; }
    for (const b of chips) {
      const want = on.includes(b.dataset.tag) ? on : [...on, b.dataset.tag];
      b.dataset.n = rows.filter((r) => r.words && want.every((t) => r.tags.includes(t))).length;
    }
    const filtering = !!(q || on.length);
    for (const shelf of $$(".library-view .shelf")) {
      const all = shelf.querySelectorAll(".lib-card").length, n = shelf.querySelectorAll(".lib-card:not([hidden])").length;
      shelf.classList.toggle("lib-nomatch", !n);
      const small = shelf.querySelector(".shelf-title small");
      if (small) small.textContent = filtering ? `${n} of ${all}` : String(all);
    }
    const count = document.getElementById("lib-count");
    if (count) count.textContent = filtering ? `${shown} of ${cards.length}` : ""; // the recipes too
    for (const b of $$("[data-lib-view]")) b.setAttribute("aria-pressed", String((root.dataset.libView || "") === b.dataset.libView));
  }
  document.addEventListener("input", (e) => { if (e.target.id === "lib-search") apply(); });
  document.addEventListener("htmx:after:settle", apply);
  document.addEventListener("DOMContentLoaded", apply);
  document.addEventListener("click", (e) => {
    const view = e.target.closest?.("[data-lib-view]");
    if (view) {
      root.dataset.libView = view.dataset.libView;
      try { localStorage.setItem(KEY, view.dataset.libView); } catch { /* this page keeps it */ }
      return apply();
    }
    const chip = e.target.closest?.(".tagfilter, [data-lib-clear]");
    if (!chip) return;
    if (chip.dataset.libClear != null) document.getElementById("lib-search").value = ""; // the words and the tags
    if (!chip.dataset.tag) for (const c of $$(".tagfilter")) c.setAttribute("aria-pressed", "false");
    else chip.setAttribute("aria-pressed", chip.getAttribute("aria-pressed") === "true" ? "false" : "true");
    apply();
  });

  // the cards by arrow: in reading order, or up / down to the nearest in x
  document.addEventListener("keydown", (e) => {
    const card = e.target.closest?.(".lib-card"), key = e.key;
    if (!card || !e.target.classList.contains("lib-play") || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(key)) return;
    const shown = $$(".library-view .lib-card").filter((c) => !c.hidden), i = shown.indexOf(card);
    let to = shown[i + (key === "ArrowRight" ? 1 : key === "ArrowLeft" ? -1 : 0)];
    if (key === "ArrowUp" || key === "ArrowDown") {
      const at = card.getBoundingClientRect(), down = key === "ArrowDown", mid = (r) => r.left + r.width / 2;
      const line = shown.map((c) => [c, c.getBoundingClientRect()]).filter(([, r]) => (down ? r.top >= at.bottom - 1 : r.bottom <= at.top + 1));
      const y = line.length ? (down ? Math.min : Math.max)(...line.map(([, r]) => r.top)) : null;
      to = line.filter(([, r]) => r.top === y).sort(([, a], [, b]) => Math.abs(mid(a) - mid(at)) - Math.abs(mid(b) - mid(at)))[0]?.[0];
    }
    e.preventDefault();
    if (to) { to.querySelector(".lib-play").focus(); to.scrollIntoView({ block: "nearest" }); }
  });

  // the pilot lamp: lit while the card's audition sounds
  document.addEventListener("audition-start", (e) => {
    const card = e.target.closest?.(".lib-card"), src = e.detail;
    if (!card || !src) return;
    card.classList.add("playing");
    card.libSrc = src;
    src.addEventListener("ended", () => { if (card.libSrc === src) card.classList.remove("playing"); });
  });
})();
