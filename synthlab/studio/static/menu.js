// synthlab/studio/static/menu.js
// THE context menu: the one `contextmenu` opener in the studio (the step
// grids' right click is an ERASE and stays grid.js's; a pen's `contextmenu`
// on a pen surface is cancelled by pen.js before this hears it).
// What opens it: a right click; the pen's barrel button tapped (it arrives
// as a right click); a long press of a finger (the browser's own
// `contextmenu`); the keyboard's Menu key or Shift+F10 on the focused
// thing. Arrows / Home / End move, Enter activates (a row is a button),
// Esc closes and hands the focus back to where it was.
//
// There is no table of menus here, as there is no table of commands in
// palette.js. THE THING DECLARES ITS MENU:
//   data-ctx="Fathom Depth"     on the row / card / module: its name, the
//                               menu's caption
//   data-menu="Use on Synths"   on each control under it that the menu
//                               lists, in document order. Valueless, the
//                               row is named by the control's aria-label.
//                               A pressed toggle (`aria-pressed`, a ticked
//                               box) is drawn ticked.
// A row runs by activating its control (`click()`), so it goes through the
// same htmx attributes, route, confirm and OOB refresh as a hand, is
// disabled while the control is, and cannot outlive it.
//   data-ctx-pop="secmenu-s1"   the thing already HAS a menu on the page (a
//                               popover or a <details> its own ⋯ button
//                               opens; the Song grid's one shared
//                               #cell-menu): that is opened instead,
//                               anchored to the thing, its
//                               `input[data-from="trackId"]` fields filled
//                               from the thing's data-* and
//                               `data-ctx-state` copied onto it — its
//                               `[data-is]` buttons pressed where the state
//                               holds their token, its `.menu-now` the
//                               thing's aria-label (the chord a tile is).
// A VALUE CONTROL (a slider, a knob, a select, a radio group with an id:
// tweak.js `steps`) needs no declaration: its
// rows are generated — Learn / Forget MIDI (midilearn.js), Reset to default
// (tweak.js, `data-default`), Put on / Take off dial 1-4 (deck.js) — and
// the rows of the thing it sits in follow under a seam. A control is named
// as MIDI learn names it (deck.js `toOf`); one with neither id nor name
// gets its thing's rows only. A press that was a drag (tweak.js's fine
// drag is a barrel drag) opens nothing: `synthlab.tweak.busy()`.
// A SECTION declares nothing either, yet has its own options, not just its
// dials': a right click anywhere in a panel — its background, a button, a dial —
// lists, after the control's and the thing's rows, the section's own:
//   - its dials (the visible value controls in it a binding can name):
//     "Put these on the deck" (deck.js `hold`: four pinned, paged in
//     fours), "Add to ★ Yours" (decksets.js), "MIDI learn these"
//     (midilearn.js `learn`: one CC after another);
//   - its commands: each control's own `data-cmd` (or `data-menu`), the
//     ones no nested thing or section owns — declared once, on the control,
//     so the menu is generated, never a second list; past twelve, "More";
//   - a folding rack's Fold / Unfold;
//   - a module's place on the page (`data-module`): collapse, pop out,
//     focus, move, reset — rows workspace.js hands over (`synthlab.workspace`);
//     not when the press was on a thing or a value control in it.
// A section is `SEC`: a module (`data-module`, the stable id a movable
// module carries), `data-menu-section="Name"`, a fieldset, a <section>, an
// FX unit or a folding rack, named by data-menu-section, data-ctx,
// aria-label(ledby), else its legend / summary / heading — one without a
// name is a wrapper, not a section. Nested: the innermost's rows in place,
// each one round it a "Name ▸" row that shows its rows in the menu (→
// opens, ← or "◂" comes back).
// Text fields, selected text and a link keep the browser's menu.
// One element, #ctx-menu (`.pop.menu`, an auto popover), moved inside the
// open popover or dialog its target sits in, so it nests: opening it does
// not close the clip drawer, and Esc closes the menu alone.
// A menu popover opened by its own button (`popovertarget`: the panel
// presets, the pattern tools' menus) takes the same arrows, ↓ / ↑ from its
// button too; Esc on one gives the focus back to that button.
// Budget: <= 145 code lines (90 -> 140: a section's own menu,
// its dials onto the deck, the submenus of the sections round it; 140 ->
// 143: a module's workspace rows; -> 145: a menu
// popover's button walks into it).

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const el = (tag, props = {}, ...kids) => { const n = Object.assign(document.createElement(tag), props); n.append(...kids); return n; };
  const menu = el("div", { id: "ctx-menu", className: "pop menu ctx-menu", popover: "auto", role: "menu" });
  const live = (c) => !c.disabled && c.getAttribute("aria-disabled") !== "true" && !c.closest("[hidden]");
  const cap = (text) => el("div", { className: "caps ctx-title", role: "presentation", textContent: text });
  const CTL = 'input[type="range"], select, [role="radiogroup"][id]', SEC = "[data-module], [data-menu-section], fieldset, section, .fx-unit, details.rack";
  let back = null, at = null; // what the menu was opened on (Esc returns the focus there); its first rows and where

  // `stay`: a submenu's row, which redraws the menu rather than closing it
  function row(text, run, off, cls = "", stay = false) {
    const b = el("button", { type: "button", className: `menu-item${cls}`, textContent: text, disabled: !!off, role: "menuitem" });
    if (stay && !cls) { b.setAttribute("aria-haspopup", "menu"); b.append(el("span", { className: "menu-hint", textContent: "▸" })); }
    b.addEventListener("click", () => { if (!stay) menu.hidePopover(); run(); });
    return b;
  }
  // a control's row: activating it, as a hand does
  function rowOf(c, text) {
    const b = row(text, () => { if (c.isConnected) { c.focus({ preventScroll: true }); c.click(); } }, !live(c), c.matches(".btn-danger") ? " danger" : "");
    if (c.getAttribute("aria-pressed") === "true" || c.checked) b.setAttribute("aria-checked", "true");
    return b;
  }
  // a thing's rows: its own declared controls
  function ofThing(thing) {
    const items = [...thing.querySelectorAll("[data-menu]")].filter((c) => c.closest("[data-ctx]") === thing);
    if (!items.length) return [];
    return [cap(thing.dataset.ctx), ...items.map((c) => rowOf(c, c.dataset.menu || c.getAttribute("aria-label") || c.textContent.trim()))];
  }

  // ---- a section's rows ---------------------------------------------------
  // (in this order: a legend holds its panel's name and its tools)
  const HEADS = [":scope > legend .panel-name", ":scope > legend", ":scope > summary .caps", ":scope > header :is(h2, h3, strong)", ":scope > :is(h2, h3)"];
  function secName(s) {
    const by = document.getElementById(s.getAttribute("aria-labelledby")?.split(" ")[0] || "");
    const head = HEADS.reduce((h, q) => h || s.querySelector(q), null);
    return (s.dataset.menuSection || s.dataset.ctx || s.getAttribute("aria-label") || by?.textContent || head?.textContent || "").replace(/\s+/g, " ").trim().slice(0, 40);
  }
  // the named sections round t, the innermost first
  function sections(t) {
    const out = [];
    for (let s = t.closest?.(SEC); s; s = s.parentElement?.closest(SEC)) if (secName(s)) out.push(s);
    return out;
  }
  const backTo = (name) => row(`◂ ${name}`, () => show(at.top), false, " menu-back", true);
  // its dials (those deck.js `toOf` names and finds again), its own commands, its fold;
  // one with none of those lists the sections in it (Perform's Launchers: each launcher)
  function ofSection(s, skip, deep = true, place = false) {
    const name = secName(s), D = S.deck, L = S.midiLearn, out = [];
    const dials = D ? [...new Set([...s.querySelectorAll(CTL)].filter((c) => !c.disabled && c.offsetParent && !c.closest("#deck-panel, #colours-pop")
      && D.find(D.toOf(c)) === c).map((c) => D.toOf(c)))] : [];
    if (dials.length) {
      out.push(row(dials.length > 4 ? `Put these on the deck (${dials.length}, in fours)` : "Put these on the deck", () => D.hold(dials, name)),
        row("Add to ★ Yours", () => S.deckSets.add(dials, name), !S.deckSets),
        L?.armed() ? row("Stop learning MIDI", () => L.stop()) : row(dials.length > 1 ? "MIDI learn these" : "Learn MIDI", () => L.learn(dials.map((to) => [to, D.nameOf(D.find(to))])), !L));
    }
    // a control a thing inside the section owns is that thing's (a layer's mute: the layer's menu)
    const mine = (c) => { const th = c.closest("[data-ctx], [data-ctx-pop]"); return !(th && th !== s && s.contains(th)) && sections(c)[0] === s; };
    const said = new Set(), rows = [...s.querySelectorAll("[data-cmd], [data-menu]")].filter((c) => !c.matches(CTL) && !skip?.has(c) && live(c) && mine(c))
      .map((c) => [c, c.dataset.menu || c.dataset.cmd || c.getAttribute("aria-label") || c.textContent.trim()])
      .filter(([, text]) => text && !said.has(text) && said.add(text))
      // "Mod matrix: a random mix" under the Mod matrix is "A random mix"
      .map(([c, text]) => rowOf(c, text.toLowerCase().startsWith(`${name.toLowerCase()}: `) ? text[name.length + 2].toUpperCase() + text.slice(name.length + 3) : text));
    if (dials.length && rows.length) out.push(el("hr", { className: "menu-sep" }));
    out.push(...rows.slice(0, rows.length > 12 ? 11 : 12));
    if (rows.length > 12) out.push(row("More", () => show([backTo(name), cap(`${name}: more`), ...rows.slice(11)]), false, "", true));
    if (s.matches("details")) out.push(row(s.open ? "Fold" : "Unfold", () => { s.open = !s.open; }));
    // its fold button (layout.js `data-fold`), when it is no command already
    const fold = [...s.querySelectorAll("[data-fold]:not([data-cmd])")].find((b) => sections(b)[0] === s);
    if (fold) out.push(rowOf(fold, fold.getAttribute("aria-expanded") === "false" ? "Unfold" : "Fold"));
    if (!out.length && deep) {
      for (const k of s.querySelectorAll(SEC)) {
        const r = sections(k.parentElement)[0] === s && secName(k) ? ofSection(k, null, false) : [];
        if (r.length) out.push(row(secName(k), () => show([backTo(name), cap(secName(k)), ...r]), false, "", true));
      }
    }
    // a module's place on the page (workspace.js): collapse, pop out, focus, move, reset —
    // for a press on the module itself, not on a thing or a value control in it
    const ws = place && s.matches("[data-module]") ? S.workspace?.rows(s) || [] : [];
    if (ws.length && out.length) out.push(el("hr", { className: "menu-sep" }));
    out.push(...ws.map(([text, run, off]) => row(text, run, off)));
    return out;
  }
  // the menu's rows, the focus on the first that runs (a submenu's redraw keeps the menu where it opened)
  function show(rows) {
    menu.replaceChildren(...rows);
    place(menu, ...at.where);
    menu.querySelector(".menu-item:not(:disabled, .menu-back)")?.focus({ preventScroll: true });
  }
  // a value control's rows: what no control on the page performs
  function ofControl(c) {
    const L = S.midiLearn, D = S.deck, to = D?.toOf(c);
    if (!to || !L || c.disabled || c.closest("#deck-panel")) return [];
    const on = (run) => () => { const now = D.find(to); if (now) { now.focus({ preventScroll: true }); run(now); } }; // a swap may have replaced it
    // a name two controls share (an FX knob's `value` once) binds neither: it resets, nothing more
    if (D.find(to) !== c) return [cap(D.nameOf(c)), row("Reset to default", () => c.isConnected && S.tweak.reset(c), c.dataset.default === undefined)];
    return [cap(D.nameOf(c)),
      row(L.armed()?.to === to ? "Stop learning MIDI" : "Learn MIDI", on((now) => L.arm(to, D.nameOf(now)))),
      ...(L.list().some((b) => b.to === to) ? [row("Forget MIDI", on(() => L.forget(to)))] : []),
      row("Reset to default", on((now) => S.tweak.reset(now)), c.dataset.default === undefined),
      el("hr", { className: "menu-sep" }),
      ...D.state().pins.map((p, i) => row(`${p === to ? "Take off" : "Put on"} dial ${i + 1}`, on(() => D.pin(i, to))))];
  }
  function place(m, t, x, y) {
    const r = t.getBoundingClientRect(); // a keyboard-opened menu has no pointer: under the thing itself
    if (!x && !y) { x = r.left + 8; y = r.bottom; }
    m.style.left = `${Math.max(4, Math.min(x, innerWidth - m.offsetWidth - 4))}px`;
    m.style.top = `${Math.max(4, Math.min(y, innerHeight - m.offsetHeight - 4))}px`;
  }
  // -> true when a menu opened (or the press was a value control's: the browser's menu never shows on one)
  function open(t, x, y) {
    const c = t.closest?.(CTL), sel = getSelection();
    if (!c && t.closest?.("input:not([type=checkbox]), textarea, [contenteditable], #ctx-menu")) return false;
    if (c && S.tweak?.busy()) return true;
    const thing = t.closest?.("[data-ctx], [data-ctx-pop]"), own = !c && document.getElementById(thing?.dataset.ctxPop || "");
    back = t.closest("button, a[href], input, select, summary, [tabindex]") || t;
    if (own) { // the thing's own panel
      if (own.contains(t)) return false;
      if (own.popover == null) return (own.open = !own.open), true; // a <details>
      for (const i of own.querySelectorAll("input[data-from]")) i.value = thing.dataset[i.dataset.from] ?? "";
      own.dataset.state = thing.dataset.ctxState || "";
      // what it is now: its [data-is] choices pressed, its .menu-now the thing's name
      for (const b of own.querySelectorAll("[data-is]")) b.setAttribute("aria-pressed", String(own.dataset.state.split(" ").includes(b.dataset.is)));
      own.querySelector(".menu-now")?.replaceChildren(thing.getAttribute("aria-label") || "");
      own.showPopover({ source: thing });
      own.querySelector("button:not([hidden]), input:not([type=hidden])")?.focus({ preventScroll: true });
      return true;
    }
    // a link and selected text keep the browser's menu (a new tab, copy), unless a thing claims them
    if (!c && !thing && (t.closest?.("a[href]") || (!sel.isCollapsed && sel.containsNode(t, true)))) return false;
    const mine = c ? ofControl(c) : [], its = thing?.dataset.ctx ? ofThing(thing) : [];
    // the sections round it, each with rows: the innermost's here (a thing that is one: under the thing's caption), each outer one a submenu
    const skip = new Set(thing?.querySelectorAll("[data-menu]"));
    const secs = sections(thing || t).map((s) => [s, ofSection(s, skip, true, !c && !thing)]).filter(([, r]) => r.length), [inner, rows] = secs[0] || [];
    const group = !inner ? [] : inner === thing && its.length ? rows : [cap(secName(inner)), ...rows];
    const up = secs.slice(1).map(([p, r]) => row(secName(p), () => show([backTo(secName(inner)), cap(secName(p)), ...r]), false, "", true));
    const parts = [mine, its, group, up].filter((p) => p.length);
    if (!parts.length) return !!c;
    at = { top: parts.flatMap((p, i) => (i ? [el("hr", { className: "menu-sep" }), ...p] : p)), where: [t, x, y] };
    if (menu.matches(":popover-open")) menu.hidePopover();
    (t.closest(":popover-open, dialog[open]") || document.body).append(menu);
    menu.showPopover();
    show(at.top);
    return true;
  }
  // (the Menu key and Shift+F10 arrive here too: the browser sends them as a
  // `contextmenu` on the focused element)
  document.addEventListener("contextmenu", (e) => {
    if (e.defaultPrevented || e.target.closest?.(".grid .cell")) return; // an erase
    const keyed = e.button !== 2 && e.target === document.activeElement && e.pointerType !== "touch" && e.pointerType !== "pen";
    if (open(e.target, keyed ? 0 : e.clientX, keyed ? 0 : e.clientY)) e.preventDefault();
  });
  document.addEventListener("keydown", (e) => {
    // ↓ / ↑ on the button that opened a menu popover walk into it (a pattern tool's vel, ⚄ write)
    const of = (e.key === "ArrowDown" || e.key === "ArrowUp") && e.target.getAttribute?.("popovertarget");
    const m = e.target.closest?.(".menu:popover-open") || (of && document.querySelector(`#${CSS.escape(of)}.menu:popover-open`));
    if (!m) return;
    // (Esc is the popover's own, but an island that takes Esc for itself cancels that; the
    // context menu hands the focus back to what was pressed, a popover to its opener)
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); m.hidePopover(); return void (m === menu && back?.isConnected && back.focus({ preventScroll: true })); }
    // → opens a section's submenu, ← comes back from one
    const sub = e.key === "ArrowRight" ? e.target.closest?.("[aria-haspopup]") : e.key === "ArrowLeft" ? m.querySelector(".menu-back") : null;
    if (sub && m === menu) { e.preventDefault(); return void sub.click(); }
    const rows = [...m.querySelectorAll(".menu-item:not(:disabled, [hidden])")].filter((b) => b.offsetParent), i = rows.indexOf(e.target), n = rows.length;
    const to = i < 0 && e.key === "ArrowUp" ? n - 1 : { ArrowDown: (i + 1) % n, ArrowUp: (i - 1 + n) % n, Home: 0, End: n - 1 }[e.key];
    if (to === undefined || !n || e.target.matches("input, select, textarea")) return;
    e.preventDefault();
    rows[to].focus();
  }, true);
  // a swap that took the pressed thing away closes its menu (a morph that
  // kept it does not: focusing a slider refreshes a region under the menu)
  document.addEventListener("htmx:after:settle", () => { if (menu.matches(":popover-open") && !back?.isConnected) menu.hidePopover(); });
})();
