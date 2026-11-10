// synthlab/studio/static/palette.js
// The command palette (Ctrl/Cmd+K, #palette) and the generated half of the
// shortcut sheet (#shortcuts, opened by ? — audio.js owns that opener).
//
// There is no table of commands here. THE ELEMENT THAT PERFORMS A COMMAND
// DECLARES IT:
//   data-cmd="Add layer"        its name in the palette (on a button, a
//                               link, a <summary>, or a <select>: one
//                               command per option, "Add effect: Reverb")
//   data-cmd-group="Reset"      the heading it is listed under (optional;
//                               else the nearest ancestor's, else "This page")
//   data-cmd-hint="…"           a dim line after the name (optional)
//   data-keys="Ctrl+Z"          the shortcut an island already handles for
//                               it: shown in the palette and listed in the
//                               sheet (data-key-label names it there when
//                               the element has no data-cmd)
//   data-cmd-fill="#clip-q"     a field that takes the command's name
//                               before it runs (a search result opening
//                               the clip drawer on itself)
// `harvest()` reads them off the live page every time the palette opens,
// so a command exists exactly while its control does, is skipped while
// that control is disabled or [hidden], and runs by activating it —
// click(), or for a <select> setting its value and dispatching `change` —
// through the same htmx attributes, route, confirm prompt and toast as a
// hand on the control. Sounds and clips are found by the server: the box
// asks GET /fragment/palette (its own hx-get) and the rows that come back
// into #pal-found are `[data-cmd]` buttons like any other.
//
// Keys: Ctrl/Cmd+K toggles it — not from a text field (piano.js's
// isTypingTarget), except its own box. Up / Down / Home / End move,
// Enter runs, Esc and a click on the backdrop close (a modal <dialog>, closedby="any":
// the focus trap is the browser's).
// Budget: <= 155 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const $ = (id) => document.getElementById(id);
  const live = (el) => !el.disabled && el.getAttribute("aria-disabled") !== "true" && !el.closest("[hidden]");

  // `all` (a control surface binding its keys, deck.js): every declaration,
  // the ones that cannot run now too, marked `off` — and no folding
  function harvest(all) {
    const out = [], seen = new Set(); // the same command on two controls is one command
    for (const el of document.querySelectorAll("[data-cmd]")) {
      const d = el.dataset, off = !live(el);
      if (all !== true && (off || seen.has(d.cmd))) continue;
      seen.add(d.cmd);
      const group = d.cmdGroup || el.closest("[data-cmd-group]")?.dataset.cmdGroup || "This page";
      if (el.tagName !== "SELECT") { out.push({ el, label: d.cmd, group, keys: d.keys, hint: d.cmdHint, off }); continue; }
      for (const o of el.options) {
        if (o.value && !o.disabled) out.push({ el, label: `${d.cmd}: ${o.textContent.trim()}`, group, value: o.value });
      }
    }
    return out;
  }
  S.commands = harvest;

  // every word of the query somewhere in "group name"; a name that starts
  // with the query first, then a word that does, then the rest
  function rank(c, words) {
    const name = c.label.toLowerCase(), hay = `${c.group} ${c.label} ${c.hint || ""}`.toLowerCase();
    if (!words.every((w) => hay.includes(w))) return -1;
    if (!words.length) return 2;
    return name.startsWith(words[0]) ? 0 : name.split(/\W+/).some((p) => p.startsWith(words[0])) ? 1 : 2;
  }

  let shown = [], at = 0;
  function draw() {
    const list = $("pal-list"), box = $("pal-q");
    if (!list || !box) return;
    const words = box.value.toLowerCase().split(/\s+/).filter(Boolean);
    // a <select>'s options (dozens of effects) are listed once something is typed
    const hits = harvest().filter((c) => words.length || c.value === undefined)
      .map((c, i) => ({ c, r: rank(c, words), i })).filter((x) => x.r >= 0);
    // a group stays together, groups in the order of their best hit. With
    // nothing typed the order is fixed: the navigation, then this view's
    // commands (the group of its primary action leading), then the shell's.
    // The list is never cut: it scrolls (Synths declares about 80 commands,
    // and a cap at 80 rows dropped "Go to ..." off the end)
    const tier = (c) => (c.group === "Go to" ? 0 : c.el.matches(".btn-primary") ? 1 : c.el.closest("#view") ? 2 : 3);
    const best = new Map();
    for (const x of hits) {
      if (!words.length) x.r = tier(x.c);
      const b = best.get(x.c.group);
      if (!b || x.r < b.r) best.set(x.c.group, x);
    }
    const order = (g) => best.get(g).r * 1e5 + best.get(g).i;
    shown = hits.sort((a, b) => order(a.c.group) - order(b.c.group) || a.i - b.i).map((x) => x.c);
    at = Math.min(at, Math.max(0, shown.length - 1));
    list.textContent = "";
    let group = null;
    shown.forEach((c, i) => {
      if (c.group !== group) {
        group = c.group;
        const h = list.appendChild(document.createElement("li"));
        h.className = "pal-group caps"; h.setAttribute("role", "presentation"); h.textContent = group;
      }
      const li = list.appendChild(document.createElement("li"));
      li.className = "menu-item pal-item"; li.id = `pal-${i}`; li.dataset.i = i;
      li.setAttribute("role", "option"); li.setAttribute("aria-selected", String(i === at));
      li.append(c.label);
      if (c.hint) { const s = li.appendChild(document.createElement("small")); s.textContent = c.hint; }
      if (c.keys) { const k = li.appendChild(document.createElement("kbd")); k.textContent = c.keys; }
    });
    if (!shown.length) {
      const li = list.appendChild(document.createElement("li"));
      li.className = "pal-empty"; li.setAttribute("role", "presentation");
      li.textContent = words.length ? "Nothing here by that name." : "Nothing to run on this page.";
    }
    box.setAttribute("aria-activedescendant", shown.length ? `pal-${at}` : "");
    $(`pal-${at}`)?.scrollIntoView({ block: "nearest" });
  }
  function move(to) {
    if (!shown.length) return;
    $(`pal-${at}`)?.setAttribute("aria-selected", "false");
    at = (to + shown.length) % shown.length;
    const li = $(`pal-${at}`);
    li?.setAttribute("aria-selected", "true"); li?.scrollIntoView({ block: "nearest" });
    $("pal-q").setAttribute("aria-activedescendant", `pal-${at}`);
  }
  function run(c) {
    if (!c) return;
    $("palette").close();
    const fill = c.el.dataset.cmdFill && document.querySelector(c.el.dataset.cmdFill);
    if (fill) fill.value = c.label;
    if (c.value === undefined) return void c.el.click();
    c.el.value = c.value;
    c.el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function open() {
    const dlg = $("palette");
    if (!dlg || dlg.open) return;
    $("pal-q").value = ""; $("pal-found").textContent = ""; at = 0;
    dlg.showModal(); draw();
  }

  document.addEventListener("keydown", (e) => {
    const dlg = $("palette");
    if (!dlg) return;
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
      if (!dlg.open && S.isTypingTarget?.(e.target)) return; // never from a field
      e.preventDefault();
      return void (dlg.open ? dlg.close() : open());
    }
    if (!dlg.open) return;
    const step = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: shown.length - 1 }[e.key];
    if (step !== undefined && !(e.target.value && (e.key === "Home" || e.key === "End"))) { e.preventDefault(); move(step); }
    else if (e.key === "Enter") { e.preventDefault(); run(shown[at]); }
  }, true);
  document.addEventListener("input", (e) => { if (e.target.id === "pal-q") { at = 0; draw(); } });
  document.addEventListener("click", (e) => {
    const dlg = $("palette");
    if (e.target.closest?.("#palette-btn")) return void open();
    if (!dlg?.open) return;
    const li = e.target.closest?.(".pal-item");
    if (li) run(shown[+li.dataset.i]); // (a click on the backdrop closes it: closedby="any")
  });
  // the server's rows landed in #pal-found: list them
  document.addEventListener("htmx:after:settle", () => { if ($("palette")?.open) draw(); });

  // ---- the shortcut sheet --------------------------------------------------
  // "Commands on this page": every declared shortcut, from the same
  // attributes; and the search box over the whole sheet.
  function sheet() {
    const sec = $("sc-commands"), dl = sec?.querySelector("dl");
    if (!dl) return;
    dl.textContent = "";
    const seen = new Set();
    for (const el of document.querySelectorAll("[data-keys]")) {
      const name = el.dataset.cmd || el.dataset.keyLabel || el.getAttribute("aria-label") || el.textContent.trim();
      if (el.closest("[hidden]") || seen.has(name)) continue; // a disabled control's key is still its key
      seen.add(name);
      const dt = dl.appendChild(document.createElement("dt"));
      el.dataset.keys.split("+").forEach((k, i) => { if (i) dt.append(" + "); dt.appendChild(document.createElement("kbd")).textContent = k; });
      dl.appendChild(document.createElement("dd")).textContent = name;
    }
    sec.hidden = !seen.size;
    const q = $("sc-q");
    if (q) { q.value = ""; narrow(""); }
  }
  function narrow(text) {
    const words = text.toLowerCase().split(/\s+/).filter(Boolean);
    let any = false;
    for (const sec of document.querySelectorAll("#shortcuts .sc-cols section")) {
      if (sec.id === "sc-commands" && !sec.querySelector("dt")) continue;
      let hit = false;
      for (const dt of sec.querySelectorAll("dt")) {
        const dd = dt.nextElementSibling, on = words.every((w) => `${dt.textContent} ${dd?.textContent}`.toLowerCase().includes(w));
        dt.hidden = !on; if (dd) dd.hidden = !on;
        hit ||= on;
      }
      if (!sec.querySelector("dt")) hit = words.every((w) => sec.textContent.toLowerCase().includes(w));
      sec.hidden = !hit; any ||= hit;
    }
    const none = $("sc-none");
    if (none) none.hidden = any;
  }
  document.addEventListener("input", (e) => { if (e.target.id === "sc-q") narrow(e.target.value); });
  const sc = $("shortcuts");
  if (sc) new MutationObserver(() => { if (sc.open) sheet(); }).observe(sc, { attributes: true, attributeFilter: ["open"] });
})();
