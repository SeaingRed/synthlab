// synthlab/studio/static/workspace.js
// The workspace: every square section its own little space, moved around
// to fit, a nimble technical place like VS Code. Every module with an id (`data-module`,
// style.css 15) can be collapsed to its head, popped out to a floating
// panel, focused (maximised: Esc returns) and, inside a dock, moved:
//   - a DOCK is an element a page marks `data-ws-dock="<key>"` (the view,
//     Synths' #patch form); its MEMBERS are the modules in it with nothing
//     between that a swap names (an id, a form) or that is a module — so a
//     member can move anywhere in its dock and every region still finds it
//     by id. A wrapper marked `data-ws-pass` is only a place in the
//     server's grid (Synths' FX racks, Perform tools, Bass check): its
//     modules are the dock's. Moving is a DOM move (`moveBefore`), never a
//     clone. A nested dock's member (a panel) may stand in any dock round
//     its own (anywhere on the page): its tree is that dock's,
//     and out of its form its controls still name it (`form=`, `ws-input`).
//   - a dock's layout is a TREE, VS Code's grid: a split {d: "row" | "col",
//     k: [nodes], s: [sizes]} (a row's sizes are weights, a column's a
//     fixed height in px or null) or a group {t: [module ids], a: the
//     tab shown}. Until a module moves, the page is the server's own grid;
//     the first move carves the tree from where the modules stand (guillotine
//     cuts of their rects), so nothing jumps. Drawn as <ws-dock> /
//     <ws-split> / <ws-group> (+ <ws-tabs>, the sashes: `.splitter.ws-sash`);
//     a comment marks each member's place in the server's markup for Reset.
//   - a drag starts on a member's head (its legend / summary / header /
//     h2, a [data-ws-head]) or its bare frame, or on a tab; the rects are
//     measured once, the drop zone (a side of a module, or its middle: tab
//     with it; the empty room under a column's last module: its foot) is
//     previewed by a transform; Esc cancels.
// Per page and per browser (localStorage "synthlab.ws", guarded), applied
// before the first paint: colours.js (blocking, in <head>) hides #view
// (`data-ws-wait`, a CSS fallback shows it anyway) when this page has a
// saved layout, and this file — deferred, so 45 KB does not hold the
// parser — lays it out and shows it; after every swap (a morph puts the server's order back, an OOB
// region lands in place) it is laid out again, and a module's own ws
// attributes ride through its morph (`htmx:before:morph:node`).
// A page's Layout menu (fragments/ws_menu.html: `data-ws` buttons) holds
// its presets, Reset and "+ module" (a visualiser drawn by visuals.js, or
// a server fragment `data-ws-url` that re-fetches itself after the page's
// posts); a module's own actions are in its context menu (menu.js asks
// `synthlab.workspace.rows`). Keys: F6 / Shift+F6 the next / previous
// module, Alt+Shift+arrows move the focused one.
// Budget: <= 645 code lines (the tree and its
// carving ~90, drawing it ~70, folds / floats / focus / added modules ~110,
// the drag and its zones ~80, the sashes ~45, the keyboard ~35, the Layout
// menu and the tabs ~40, the context menu's rows ~20; 530 -> 536 for a
// module's own fold button opening it when collapsed: the keys dock;
// 536 -> 590: a column's foot (the space below its last module) as a
// drop zone and its placement ~35, a sticky member carved where it flows,
// a new column the dragged module's width, the "can't leave" cue ~15;
// 590 -> 630: every component movable — a panel anywhere on the page (its zones in every dock round its own, a tree's
// stranger drawn and sent home, its form kept) ~30, pass-through wrappers,
// an old Synths layout carried over to the split modules ~12, tabs kept
// through a replace; the "can't leave" cue went, -10; 630 -> 645: no
// stretched last module — the room under a
// module of the server's grid, to its place's foot, is a drop zone ~15).

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const root = document.documentElement;
  const KEY = "synthlab.ws", WS = "ws-dock, ws-split, ws-group, ws-float";
  let all = {};
  try { all = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch { all = {}; }
  const keep = () => { try { localStorage.setItem(KEY, JSON.stringify(all)); } catch { /* this page still shows it */ } };
  // a page's record key: bumped when its markup's default moved too far for
  // an old layout to fit (Perform: 2, the Loops page's layout; Synths: 2,
  // its FX racks, Perform tools and Bass check split into modules) — a record under an older key is dropped, or carried
  // over where SPLIT names what an old module became (its place a column
  // of the new ones, a tab of them where it was a tab)
  const REV = { perform: 2, editor: 2 }, keyOf = (v) => (REV[v] ? `${v}@${REV[v]}` : v);
  const SPLIT = { editor: { "fx.rack": ["fx.layer", "fx.master"], "synths.perform": ["synths.macros", "synths.xy"], "synths.bass": ["synths.bass", "synths.sub"] } };
  const split = (n, to) => {
    if (!n?.t) return n && { ...n, k: n.k.map((k) => split(k, to)) };
    const t = n.t.flatMap((id) => to[id] || [id]);
    return n.t.length === 1 && to[n.t[0]] ? { d: "col", k: t.map((id) => ({ t: [id], a: 0 })), s: t.map(() => null) } : { ...n, t, a: Math.max(0, t.indexOf((to[n.t[n.a]] || [n.t[n.a]])[0])) };
  };
  const stale = Object.keys(all).filter((k) => { const [v, n] = k.split("@"); return REV[v] && k !== keyOf(v) && (n === undefined || Number(n) < REV[v]); });
  for (const k of stale) {
    const v = k.split("@")[0], to = SPLIT[v], r = all[k];
    if (to && r && !all[keyOf(v)]) all[keyOf(v)] = { ...r, docks: Object.fromEntries(Object.entries(r.docks || {}).map(([d, t]) => [d, split(t, to)])), fold: (r.fold || []).flatMap((id) => to[id] || [id]), float: Object.fromEntries(Object.entries(r.float || {}).filter(([id]) => !to[id])) };
    delete all[k];
  }
  if (stale.length) keep();
  // (the first paint's wait, `data-ws-wait`, is set by colours.js: this file is deferred)

  const $view = () => document.getElementById("view");
  const page = () => keyOf($view()?.dataset.view || "");
  const rec = (make) => (all[page()] ??= make ? { docks: {}, fold: [], float: {}, add: [] } : undefined);
  function commit() {
    const r = all[page()];
    if (r && !Object.keys(r.docks).length && !r.fold.length && !Object.keys(r.float).length && !r.add.length) delete all[page()];
    keep();
  }
  const idOf = (m) => m.dataset.module;
  const byId = (id) => document.querySelector(`#view [data-module="${CSS.escape(id)}"]`);
  const mk = (tag, props = {}) => Object.assign(document.createElement(tag), props);
  // a DOM move that keeps a focused control, a playing canvas, a form's state
  const put = (into, node, before = null) => {
    try { if (into.moveBefore && node.isConnected && into.isConnected) return void into.moveBefore(node, before); } catch { /* across documents: insert */ }
    into.insertBefore(node, before);
  };

  // ---- docks and their members ------------------------------------------------
  function dockOf(m) {
    for (let p = m.parentElement; p; p = p.parentElement) {
      if (p.matches("[data-ws-dock]")) return p;
      if (!p.matches(WS) && ((p.id && !p.hasAttribute("data-ws-pass")) || p.matches("form, [data-module]"))) return null;
    }
    return null;
  }
  // the docks a module may stand in: the one the server draws it in and
  // every dock round that (a panel anywhere on its page; a page's module
  // never into the panels' form, whose input every patch post hears)
  const homeDock = (m) => { const a = anchors.get(idOf(m)); return a?.isConnected ? a.parentElement.closest("[data-ws-dock]") : dockOf(m); };
  function reach(m) {
    const out = [];
    for (let d = homeDock(m); d; d = d.parentElement?.closest("[data-ws-dock]")) out.push(d);
    return out;
  }
  // back where the server drew it (a module no tree of its dock names)
  const back = (m) => { const a = anchors.get(idOf(m)); if (a?.isConnected) put(a.parentNode, m, a.nextSibling); };
  // a module out of its form (a panel out of #patch) keeps it: its own controls name it
  function owned(m) {
    const f = anchors.get(idOf(m))?.parentElement?.closest("form[id]");
    if (f && !f.contains(m)) for (const c of m.querySelectorAll("input, select, textarea, button")) if (!c.form && !c.hasAttribute("form")) c.setAttribute("form", f.id);
  }
  // … and what is typed there still reaches the form's own trigger (#patch: `ws-input`)
  document.addEventListener("input", (e) => { const f = e.target.form; if (f && !f.contains(e.target) && f.matches("[data-ws-dock]")) f.dispatchEvent(new Event("ws-input")); });
  // a member still where the server drew it (not yet in a laid-out tree or a float)
  const home = (m) => !m.parentElement?.closest(`${WS}, [data-ws-dock]`)?.matches(WS);
  // a module's box (one drawn `display: contents`, Synths' sound bar: its children's)
  function box(m) {
    const r = m.getBoundingClientRect();
    if (r.width || r.height || getComputedStyle(m).display !== "contents") return r;
    const rs = [...m.children].map((c) => c.getBoundingClientRect()).filter((q) => q.width && q.height);
    if (!rs.length) return r;
    const [l, t] = [Math.min(...rs.map((q) => q.left)), Math.min(...rs.map((q) => q.top))];
    return new DOMRect(l, t, Math.max(...rs.map((q) => q.right)) - l, Math.max(...rs.map((q) => q.bottom)) - t);
  }
  // how far down a module's own content reaches (a grid area or a row can
  // stretch its box far past it: the FX racks' column); ROOM is the least foot taken as a drop zone
  const ROOM = 48;
  function filled(m) {
    const r = box(m), cs = getComputedStyle(m);
    if (cs.display === "contents") return r.bottom;
    let b = r.top;
    for (const c of m.children) {
      const q = c.getBoundingClientRect();
      if (q.height && !/fixed|absolute/.test(getComputedStyle(c).position)) b = Math.max(b, q.bottom);
    }
    return Math.min(r.bottom, b + parseFloat(cs.paddingBottom) + parseFloat(cs.borderBottomWidth));
  }
  const members = (dock) => [...dock.querySelectorAll("[data-module]")].filter((m) => idOf(m) && dockOf(m) === dock);
  // (the server's grid: a column's last module keeps its own
  // height) the room under a module nothing of its dock stands under, down
  // to the foot of its place — the wrapper or column holding it (a rack's,
  // a column of panels), else the page's grid (to whatever is drawn below)
  function under(o, dock, m) {
    let p = o;
    while (p.parentElement !== dock) p = p.parentElement;
    if (p === o) p = dock;
    const r = box(o), cs = getComputedStyle(p), across = (q) => q.width && q.left < r.right - 1 && q.right > r.left + 1;
    const col = members(dock).filter((x) => x !== m && p.contains(x) && !x.parentElement.matches("ws-float") && across(box(x)));
    if (col.some((x) => box(x).top >= r.bottom - 1)) return null;
    let b = p.getBoundingClientRect().bottom - parseFloat(cs.paddingBottom) - parseFloat(cs.borderBottomWidth);
    for (const c of p === dock ? dock.children : []) {
      const q = c.getBoundingClientRect();
      if (c !== o && across(q) && q.top >= r.bottom - 1 && !/fixed|absolute/.test(getComputedStyle(c).position)) b = Math.min(b, q.top);
    }
    return b - r.bottom >= ROOM ? { ids: col.map(idOf), r: new DOMRect(r.left, r.bottom, r.width, b - r.bottom) } : null;
  }
  const HEAD = ":scope > :is(.module-head, legend, summary, header, h2, h3), [data-ws-head]";
  function nameOf(m) {
    const by = document.getElementById(m.getAttribute("aria-labelledby")?.split(" ")[0] || "");
    const h = m.querySelector(":scope > legend .panel-name, :scope > summary .caps") || m.querySelector(HEAD);
    const text = h && ([...h.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim())?.textContent || h.textContent);
    return (m.dataset.wsName || m.dataset.menuSection || m.getAttribute("aria-label") || by?.textContent || text || idOf(m).split(".").pop())
      .replace(/\s+/g, " ").trim().slice(0, 32);
  }
  // where each member stood in the server's markup (Reset puts it back there)
  const anchors = new Map();
  function anchor(m) {
    const a = anchors.get(idOf(m));
    if (a?.isConnected || !home(m)) return;
    const c = document.createComment(` ws ${idOf(m)} `);
    m.before(c);
    anchors.set(idOf(m), c);
  }

  // ---- the tree ------------------------------------------------------------------
  const leaf = (id) => ({ t: [id], a: 0 });
  const leaves = (n, out = []) => (n.t ? out.push(n) : n.k.forEach((k) => leaves(k, out)), out);
  function locate(n, id, parent = null, i = 0) {
    if (n.t) return n.t.includes(id) ? { leaf: n, parent, i } : null;
    for (let j = 0; j < n.k.length; j++) { const r = locate(n.k[j], id, n, j); if (r) return r; }
    return null;
  }
  // empty groups out; a split of one is that one; a split inside a split of its direction joins it
  function prune(n) {
    if (!n) return null;
    if (n.t) { n.a = Math.max(0, Math.min(n.a || 0, n.t.length - 1)); return n.t.length ? n : null; }
    const k = [], s = [];
    n.k.forEach((c, i) => {
      const p = prune(c), w = n.s?.[i] ?? null;
      if (!p) return;
      if (p.t || p.d !== n.d) return void (k.push(p), s.push(w));
      const sum = p.s.reduce((a, b) => a + (b || 1), 0);
      p.k.forEach((q, j) => { k.push(q); s.push(n.d === "row" ? ((p.s[j] || 1) * (w || sum)) / sum : p.s[j]); });
    });
    return k.length > 1 ? { d: n.d, k, s } : k[0] || null;
  }
  // `id` to a side of `target` ("left" "right" "top" "bottom"), or into its group ("tab");
  // no target: that side of the whole dock. Beside it, it takes `f` of the target's width
  // (the drag: its own width, up to half; a sound list dropped at the dock's edge stays a list's width)
  function place(tree, id, target, zone, f = 0.5) {
    for (const l of leaves(tree)) l.t = l.t.filter((x) => x !== id);
    let t = prune(tree);
    if (!t) return leaf(id);
    if (zone === "foot") return foot(t, id, target);
    const at = target && target !== id ? locate(t, target) : null;
    if (zone === "tab" && at) { at.leaf.t.push(id); at.leaf.a = at.leaf.t.length - 1; return t; }
    const d = zone === "left" || zone === "right" ? "row" : "col", after = zone === "right" || zone === "bottom";
    const node = at ? (at.parent ? at.parent.k[at.i] : t) : t, add = leaf(id);
    if (at?.parent?.d === d) {
      const p = at.parent, w = p.s[at.i] || 1;
      p.k.splice(at.i + after, 0, add);
      p.s.splice(at.i + after, 0, d === "row" ? w * f : null);
      if (d === "row") p.s[at.i + !after] = w * (1 - f);
      return t;
    }
    const pair = { d, k: after ? [node, add] : [add, node], s: d === "row" ? (after ? [1 - f, f] : [f, 1 - f]) : [null, null] };
    if (at?.parent) { at.parent.k[at.i] = pair; return t; }
    return pair;
  }
  // `id` at the foot of the column holding the modules `ids` (the empty room
  // under them): the smallest node that holds them all — a column takes it
  // last, anything else becomes a column with it underneath
  const pathTo = (n, id, p = []) => (n.t ? (n.t.includes(id) ? p : null) : n.k.reduce((r, k, i) => r || pathTo(k, id, [...p, i]), null));
  function foot(t, id, ids) {
    let pre = null;
    for (const p of ids.map((x) => pathTo(t, x)).filter(Boolean)) {
      if (!pre) { pre = p; continue; }
      let i = 0;
      while (i < pre.length && pre[i] === p[i]) i++;
      pre = pre.slice(0, i);
    }
    const n = pre && nodeAt(t, pre.join(".")), add = leaf(id);
    if (!n) return place(t, id, null, "bottom");
    if (n.d === "col") { n.k.push(add); n.s.push(null); return t; }
    const pair = { d: "col", k: [n, add], s: [null, null] };
    if (!pre.length) return pair;
    nodeAt(t, pre.slice(0, -1).join(".")).k[pre.at(-1)] = pair;
    return t;
  }
  // the tree of a dock as the page draws it now: cut the members' rects
  // across, else down, wherever no rect straddles the cut
  function carve(items) {
    if (items.length === 1) return leaf(items[0].id);
    for (const [d, lo, hi] of [["col", "top", "bottom"], ["row", "left", "right"]]) {
      const parts = [];
      let cur = [], edge = -Infinity;
      for (const it of [...items].sort((a, b) => a.r[lo] - b.r[lo])) {
        if (cur.length && it.r[lo] >= edge - 2) { parts.push(cur); cur = []; }
        cur.push(it); edge = Math.max(edge, it.r[hi]);
      }
      parts.push(cur);
      const wide = (p) => Math.max(...p.map((x) => x.r.right)) - Math.min(...p.map((x) => x.r.left));
      if (parts.length > 1) return { d, k: parts.map(carve), s: parts.map((p) => (d === "row" ? wide(p) : null)) };
    }
    return { d: "col", k: items.map((it) => leaf(it.id)), s: items.map(() => null) };
  }
  function carveDock(dock) {
    const ms = members(dock).filter((m) => !m.parentElement.matches("ws-float"));
    // a sticky member (the sound list, the keys dock) is cut where it flows,
    // not where it is stuck: stuck, it straddled every cut and the page fell
    // to one column
    const stuck = ms.filter((m) => getComputedStyle(m).position === "sticky");
    stuck.forEach((m) => m.style.setProperty("position", "static", "important"));
    const seen = ms.map((m) => ({ id: idOf(m), r: box(m) })).filter((x) => x.r.width && x.r.height);
    stuck.forEach((m) => m.style.removeProperty("position"));
    let t = seen.length ? carve(seen) : null;
    for (const m of ms) if (!t || !locate(t, idOf(m))) t = t ? place(t, idOf(m), null, "bottom") : leaf(idOf(m));
    return t;
  }

  // ---- drawing a dock's tree -------------------------------------------------------
  function sash(d) {
    const s = mk("div", { className: "splitter ws-sash", tabIndex: 0, title: "drag to resize · double-click evens it" });
    s.setAttribute("role", "separator");
    s.setAttribute("aria-orientation", d === "row" ? "vertical" : "horizontal");
    s.dataset.d = d;
    return s;
  }
  function draw(dock, tree) {
    const ms = members(dock), mods = new Map(ms.map((m) => [idOf(m), m])), r = rec(true);
    // a module of a dock inside this one that the tree names (a panel out of
    // its form) is drawn here; one another dock's tree names is that dock's
    for (const l of leaves(tree)) for (const id of l.t) { const m = byId(id); if (m && !mods.has(id) && reach(m).includes(dock)) mods.set(id, m); }
    mods.forEach(anchor);
    const theirs = (id) => Object.entries(r.docks).some(([k, t]) => k !== dock.dataset.wsDock && t && locate(t, id));
    const late = [...mods.values()].filter((m) => !locate(tree, idOf(m)) && homeDock(m) === dock && !theirs(idOf(m)));
    for (const m of late) tree = place(tree, idOf(m), null, "bottom"); // one the tree never saw: at the foot
    r.docks[dock.dataset.wsDock] = tree;
    if (late.length) commit();
    const old = [...dock.children].filter((c) => c.matches("ws-dock")), fresh = mk("ws-dock");
    let at = old[0] || ms.map((m) => anchors.get(idOf(m))).filter((a) => a?.isConnected)
      .sort((a, b) => (a.compareDocumentPosition(b) & 4 ? -1 : 1))[0];
    while (at && at.parentNode !== dock) at = at.parentNode;
    dock.insertBefore(fresh, at || null);
    const build = (n, into, path) => {
      if (n.t) {
        const ids = n.t.filter((id) => mods.has(id) && !r.float[id]);
        if (!ids.length) return null;
        const g = mk("ws-group"), shown = ids.includes(n.t[n.a]) ? n.t[n.a] : ids[0];
        g.dataset.wsIds = ids.join(" "); // (what CSS asks of a group: the sound list's alone, folded, goes)
        into.append(g);
        if (ids.length > 1) {
          const bar = g.appendChild(mk("ws-tabs", { role: "tablist" }));
          for (const id of ids) {
            const b = bar.appendChild(mk("button", { type: "button", className: "ws-tab", textContent: nameOf(mods.get(id)), role: "tab", tabIndex: id === shown ? 0 : -1 }));
            b.dataset.ws = id;
            b.setAttribute("aria-selected", String(id === shown));
          }
        }
        for (const id of ids) {
          const m = mods.get(id);
          put(g, m);
          if (ids.length > 1 && id !== shown) m.dataset.wsTab = "off"; else delete m.dataset.wsTab;
        }
        return g;
      }
      const sp = into.appendChild(mk("ws-split"));
      sp.dataset.d = n.d;
      let made = 0;
      n.k.forEach((c, i) => {
        const el = build(c, sp, [...path, i]);
        if (!el) return;
        if (made++) sp.insertBefore(sash(n.d), el);
        el.dataset.wsAt = [...path, i].join(".");
        if (n.d === "row") el.style.setProperty("--g", String(n.s[i] || 1));
        else if (n.s[i]) el.style.setProperty("--h", `${n.s[i]}px`);
      });
      return made ? sp : (sp.remove(), null);
    };
    build(tree, fresh, []);
    // (one left in the old tree that this one does not draw goes home first: never removed with it)
    for (const m of mods.values()) if (!fresh.contains(m) && !m.parentElement.matches("ws-float") && old.some((o) => o.contains(m))) back(m);
    old.forEach((o) => o.remove());
    dock.dataset.wsOn = "";
    mods.forEach(owned);
  }
  // a dock drawn again from its saved tree (a module left it for another), or as the server drew it
  function redraw(dock) {
    const t = rec()?.docks[dock.dataset.wsDock];
    if (t) return draw(dock, t);
    members(dock).forEach(back);
    dock.querySelector(":scope > ws-dock")?.remove();
    delete dock.dataset.wsOn;
  }
  const unplace = (r, k, id) => { const t = r.docks[k]; if (!t) return; leaves(t).forEach((l) => { l.t = l.t.filter((x) => x !== id); }); r.docks[k] = prune(t) || undefined; };
  const nodeAt = (tree, path) => path.split(".").filter(Boolean).reduce((n, i) => n.k[+i], tree);

  // ---- what a module is now: folded, floating, focused, added --------------------------
  function float(m, g) {
    anchor(m);
    let f = m.parentElement.matches("ws-float") ? m.parentElement : null;
    if (!f) {
      f = mk("ws-float");
      const dock = dockOf(m);
      if (dock) dock.append(f); else m.before(f);
      put(f, m);
    }
    const x = Math.max(0, Math.min(g.x, innerWidth - 120)), y = Math.max(0, Math.min(g.y, innerHeight - 40));
    Object.assign(f.style, { left: `${x}px`, top: `${y}px`, width: `${g.w}px`, height: `${g.h}px` });
    f.dataset.ws = idOf(m);
  }
  function unfloat(m) {
    const f = m.parentElement;
    if (!f?.matches("ws-float")) return;
    const a = anchors.get(idOf(m));
    if (a?.isConnected && !dockOf(m)?.matches("[data-ws-on]")) a.after(m); else f.before(m);
    f.remove();
  }
  // a module added from the Layout menu: a visualiser, or a server fragment
  function made(a) {
    const m = mk("section", { className: "module ws-added" });
    Object.assign(m.dataset, { module: a.id, wsName: a.name });
    m.setAttribute("aria-label", a.name);
    if (a.vis) m.append(mk("h2", { className: "caps module-head", textContent: a.name }), mk("canvas", { className: "module-well ws-vis" }));
    if (a.vis) m.dataset.wsVis = a.vis;
    if (a.url) { m.id = a.el || ""; m.setAttribute("hx-get", a.url); m.setAttribute("hx-trigger", "load"); m.setAttribute("hx-swap", "outerHTML"); }
    return m;
  }
  // put every saved thing back on the page as it is now (cheap when it already is)
  function paint() {
    const v = $view(), r = rec();
    if (!v) return;
    const docks = [...v.querySelectorAll("[data-ws-dock]")];
    if (v.matches("[data-ws-dock]")) docks.unshift(v);
    for (const a of r?.add || []) {
      if (byId(a.id) || !docks[0]) continue;
      const m = made(a);
      docks[0].append(m);
      if (a.url) window.htmx?.process(m);
    }
    for (const m of v.querySelectorAll("[data-module]")) {
      const id = idOf(m);
      if (!id) continue;
      m.toggleAttribute("data-ws-fold", !!r?.fold.includes(id));
      m.toggleAttribute("data-ws-added", !!r?.add.some((a) => a.id === id));
      if (dockOf(m)) m.dataset.wsM = ""; else delete m.dataset.wsM;
      if (r?.float[id]) float(m, r.float[id]); else unfloat(m);
    }
    for (const d of docks) {
      const t = r?.docks[d.dataset.wsDock];
      if (t && [...members(d), ...leaves(t).flatMap((l) => l.t.map(byId))].some((m) => m && home(m))) draw(d, t);
    }
    v.querySelectorAll("[data-module]").forEach(owned);
    // (a member replaced in its group — an outerHTML answer — takes its tab's state again)
    for (const b of v.querySelectorAll("ws-tabs .ws-tab")) { const m = byId(b.dataset.ws); if (m && b.getAttribute("aria-selected") === "true") delete m.dataset.wsTab; else if (m) m.dataset.wsTab = "off"; }
    sizeVis();
    delete root.dataset.wsWait;
  }
  // a module's ws attributes survive its own morph (a region's OOB redraw)
  document.addEventListener("htmx:before:morph:node", (e) => {
    const { oldNode: o, newNode: n } = e.detail || {};
    if (o?.nodeType !== 1 || !o.hasAttribute("data-module") || n?.nodeType !== 1) return;
    for (const a of ["data-ws-fold", "data-ws-tab", "data-ws-m", "data-ws-max", "data-ws-added", "tabindex"]) if (o.hasAttribute(a) && !n.hasAttribute(a)) n.setAttribute(a, o.getAttribute(a));
  });

  // ---- the actions -----------------------------------------------------------------
  function edit(dock, fn) {
    const r = rec(true), k = dock.dataset.wsDock;
    r.docks[k] = prune(fn(r.docks[k] || carveDock(dock)));
    commit();
    draw(dock, r.docks[k]);
  }
  // `into`: the dock it lands in (a panel's own, or one round it): every other tree lets it go
  function moveTo(m, target, zone, f, into = dockOf(m)) {
    const from = dockOf(m), id = idOf(m);
    if (!into || target === id) return;
    const r = rec(true);
    if (from !== into) for (const k of Object.keys(r.docks)) if (k !== into.dataset.wsDock) unplace(r, k, id);
    edit(into, (t) => place(t, id, target, zone, f));
    if (from && from !== into) redraw(from);
  }
  function fold(m, on = !m.hasAttribute("data-ws-fold")) {
    const r = rec(true), id = idOf(m);
    r.fold = r.fold.filter((x) => x !== id).concat(on ? [id] : []);
    m.toggleAttribute("data-ws-fold", on);
    commit();
  }
  // a module's own fold ([data-fold], layout.js: the keys dock's "⌨ keyboard")
  // pressed while the workspace holds it collapsed opens it: one press shows it,
  // never a head its own button can't open (its own fold goes on only if it was shut)
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.("[data-fold]"), m = b?.closest("[data-module][data-ws-fold]");
    if (!m) return;
    fold(m, false);
    if (!document.getElementById(b.dataset.fold)?.classList.contains("folded")) e.stopPropagation();
  }, true);
  function popOut(m, on = !m.parentElement.matches("ws-float")) {
    const r = rec(true), id = idOf(m), b = m.getBoundingClientRect();
    if (on) r.float[id] = { x: b.left + 24, y: Math.max(48, b.top + 24), w: Math.max(240, Math.min(b.width, 720)), h: Math.max(120, Math.min(b.height, innerHeight - 96)) };
    else delete r.float[id];
    commit();
    if (on) float(m, r.float[id]); else { unfloat(m); paint(); }
  }
  let maxed = null, viaFull = false;
  function maximise(m, full = false) {
    if (maxed) { delete maxed.dataset.wsMax; delete root.dataset.wsMax; }
    if (!m || m === maxed) { maxed = null; if (viaFull && document.fullscreenElement) document.exitFullscreen().catch(() => {}); viaFull = false; return; }
    maxed = m; viaFull = full;
    m.dataset.wsMax = ""; root.dataset.wsMax = idOf(m);
    if (full && !document.fullscreenElement) root.requestFullscreen?.().catch(() => {});
  }
  document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && viaFull) maximise(maxed); });
  function reset() {
    maximise(null);
    for (const [id, a] of anchors) { const m = a.isConnected && byId(id); if (m) put(a.parentNode, m, a.nextSibling); }
    for (const m of document.querySelectorAll("#view [data-ws-added]")) m.remove();
    for (const n of document.querySelectorAll("#view ws-dock, #view ws-float")) n.remove();
    for (const d of document.querySelectorAll("#view [data-ws-on], #view[data-ws-on]")) delete d.dataset.wsOn;
    for (const m of document.querySelectorAll("#view [data-module]")) { delete m.dataset.wsTab; m.removeAttribute("data-ws-fold"); }
    delete all[page()];
    keep();
    window.dispatchEvent(new Event("synthlab-geometry"));
  }
  function preset(spec) {
    reset();
    all[page()] = { docks: {}, fold: [], float: {}, add: [], ...structuredClone(spec) };
    keep();
    paint();
    window.dispatchEvent(new Event("synthlab-geometry"));
  }
  function addModule(b) {
    const r = rec(true), a = { id: b.dataset.wsAdd, name: b.dataset.wsName || b.textContent.trim(), vis: b.dataset.wsVis, url: b.dataset.wsUrl, el: b.dataset.wsEl };
    if (byId(a.id)) return byId(a.id).scrollIntoView({ block: "nearest" });
    r.add.push(a);
    // (the page as it stands is carved first: the new module lands at its foot)
    const dock = $view()?.matches("[data-ws-dock]") && $view();
    if (dock) r.docks[dock.dataset.wsDock] = prune(r.docks[dock.dataset.wsDock] || carveDock(dock));
    commit();
    paint();
    byId(a.id)?.scrollIntoView({ block: "nearest" });
  }
  function remove(m) {
    const r = rec(true), id = idOf(m);
    r.add = r.add.filter((a) => a.id !== id);
    delete r.float[id];
    for (const k of Object.keys(r.docks)) unplace(r, k, id);
    commit();
    (m.parentElement.matches("ws-float") ? m.parentElement : m).remove();
    paint();
  }
  // the context menu's rows for a module (menu.js: [label, run, off, ticked])
  S.workspace = {
    rows(m) {
      const id = idOf(m);
      if (!id) return [];
      const dock = dockOf(m), out = [
        [m.hasAttribute("data-ws-fold") ? "Expand" : "Collapse to its head", () => fold(m)],
        [m.parentElement.matches("ws-float") ? "Dock it back" : "Pop out", () => popOut(m)],
        [m.hasAttribute("data-ws-max") ? "Back from focus" : "Focus (Esc returns)", () => maximise(m)],
        ["Focus in full screen", () => maximise(m, true), !root.requestFullscreen],
      ];
      if (dock) out.push(["Move with the keyboard (Alt+Shift+arrows)", () => { m.tabIndex = -1; m.focus(); }]);
      if (m.hasAttribute("data-ws-added")) out.push(["Remove from this page", () => remove(m)]);
      out.push(["Reset this page's layout", reset, !all[page()]]);
      return out;
    },
    state: () => structuredClone(all[page()] || null),
  };

  // ---- the Layout menu ------------------------------------------------------------------
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.("[data-ws]:not(.ws-tab)");
    if (b) {
      const act = b.dataset.ws;
      if (act === "reset") reset();
      else if (act === "preset") preset(JSON.parse(b.dataset.wsPreset || "{}"));
      else if (act === "add") addModule(b);
      else if (act === "pin") document.getElementById("clip-pin")?.click();
      else if (act === "next" || act === "prev") step(act === "next" ? 1 : -1);
      b.closest(":popover-open")?.hidePopover();
      return;
    }
    const tab = e.target.closest?.("ws-tabs .ws-tab");
    if (tab) pick(tab);
  });
  function pick(tab) {
    const g = tab.closest("ws-group"), dock = g && dockOf(g.querySelector("[data-module]") || g), r = rec();
    const l = dock && r?.docks[dock.dataset.wsDock] && locate(r.docks[dock.dataset.wsDock], tab.dataset.ws)?.leaf;
    if (!l) return;
    l.a = l.t.indexOf(tab.dataset.ws);
    commit();
    for (const b of g.querySelectorAll(":scope > ws-tabs .ws-tab")) { const on = b === tab; b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; }
    for (const m of g.querySelectorAll(":scope > [data-module]")) if (idOf(m) === tab.dataset.ws) delete m.dataset.wsTab; else m.dataset.wsTab = "off";
    window.dispatchEvent(new Event("synthlab-geometry"));
  }

  // ---- the keyboard: F6 between modules, Alt+Shift+arrows move one ------------------------
  const visible = () => [...document.querySelectorAll("#view [data-module]")].filter((m) => idOf(m) && m.offsetParent !== null || m.matches("[data-ws-max]"));
  function step(by) {
    const list = visible(), now = document.activeElement?.closest?.("[data-module]"), i = list.indexOf(now);
    const m = list[(i + by + list.length) % list.length] || list[0];
    if (!m) return;
    if (!m.hasAttribute("tabindex")) m.tabIndex = -1;
    m.focus({ preventScroll: true });
    m.scrollIntoView({ block: "nearest" });
  }
  const DIR = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "top", ArrowDown: "bottom" };
  document.addEventListener("keydown", (e) => {
    if (e.key === "F6" && !e.ctrlKey && !e.altKey && !e.metaKey) { e.preventDefault(); return void step(e.shiftKey ? -1 : 1); }
    // a group's tabs: one Tab stop, ← → pick the next
    const tab = e.target.closest?.(".ws-tab"), to = tab && { ArrowLeft: tab.previousElementSibling, ArrowRight: tab.nextElementSibling }[e.key];
    if (to) { e.preventDefault(); pick(to); return void to.focus(); }
    if (e.key === "Escape" && drag?.on) { e.preventDefault(); return void stop(true); }
    if (e.key === "Escape" && maxed && !e.defaultPrevented && !document.querySelector(":popover-open, dialog[open]")) { e.preventDefault(); return void maximise(null); }
    const z = DIR[e.key], m = e.target;
    if (!z || !e.altKey || !e.shiftKey || e.ctrlKey || !m.matches?.("[data-ws-m]")) return;
    e.preventDefault();
    // the nearest module that way, by the centres
    const c = (r) => [r.left + r.width / 2, r.top + r.height / 2], [x, y] = c(m.getBoundingClientRect());
    const ax = z === "left" || z === "right" ? 0 : 1, sg = z === "left" || z === "top" ? -1 : 1;
    let best = null, bd = Infinity;
    for (const o of members(dockOf(m))) {
      const r = o.getBoundingClientRect();
      if (o === m || !r.width) continue;
      const p = c(r), along = (p[ax] - [x, y][ax]) * sg, d = along + Math.abs(p[1 - ax] - [x, y][1 - ax]) * 2;
      if (along > 4 && d < bd) { best = o; bd = d; }
    }
    moveTo(m, best ? idOf(best) : null, z);
    if (document.activeElement !== m) m.focus({ preventScroll: true });
    m.scrollIntoView({ block: "nearest" });
  });

  // ---- the drag --------------------------------------------------------------------------
  let drag = null, shade = null, chip = null;
  const CTRL = "button:not(.ws-tab), a[href], input, select, textarea, label, [contenteditable], canvas, [role=slider], [role=application], .splitter";
  document.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.pointerType === "touch") return;
    const t = e.target, tab = t.closest?.("ws-tabs .ws-tab");
    const m = tab ? byId(tab.dataset.ws) : t.closest?.("[data-module]");
    if (!m || !(m.matches("[data-ws-m]") || m.parentElement.matches("ws-float"))) return;
    // its head (not a control in it) or its bare frame
    if (!tab) {
      const head = t.closest(HEAD.replace(/:scope > /g, "")), c = t.closest(CTRL);
      // (a heading in a nested section or form is that one's, not the module's)
      const own = head && (head.matches("[data-ws-head]") ? head.closest("[data-module]") : head.parentElement.closest("[data-module], section, fieldset, form, details")) === m;
      if ((t !== m && !own) || (c && m.contains(c))) return;
    }
    drag = { m, x0: e.clientX, y0: e.clientY, on: false, f: m.parentElement.matches("ws-float") ? m.parentElement : null };
  });
  function begin() {
    const { m } = drag, docks = drag.f ? [] : reach(m), seen = new Set();
    drag.on = true;
    drag.zones = [];
    // measured once: each other module's box (its group's, in a laid-out dock;
    // a hidden tab has none) in every dock it may stand in — a panel's own
    // first, then the page's round it (never the module it is in)
    for (const dock of docks) {
      const others = (el) => [...el.querySelectorAll("[data-module]")].filter((o) => o !== m && dockOf(o) === dock).map(idOf);
      for (const o of members(dock)) {
        const el = o.parentElement.matches("ws-group") ? o.parentElement : o;
        if (o === m || o.contains(m) || seen.has(el) || o.parentElement.matches("ws-float") || !box(o).width) continue;
        seen.add(el);
        // a module stretched past what it holds (a rack, Bass check, a
        // panel at a column's foot): the room under its content is the foot
        const r = box(el), f = filled(o), ids = [idOf(o), ...others(el)].filter((x) => x !== idOf(m));
        if (r.bottom - f >= ROOM) drag.zones.push({ ids, dock, r: new DOMRect(r.left, f, r.width, r.bottom - f) });
        drag.zones.push({ id: idOf(o), dock, r: r.bottom - f >= ROOM ? new DOMRect(r.left, r.top, r.width, f - r.top) : r, own: el.contains(m) });
        const u = home(o) && under(o, dock, m);
        if (u) drag.zones.push({ ids: u.ids, dock, r: u.r });
      }
      // a laid-out column shorter than its row: the room under its last module
      for (const s of dock.querySelectorAll("ws-split[data-d=col]")) {
        const last = s.lastElementChild, r = s.getBoundingClientRect(), b = last?.getBoundingClientRect().bottom, ids = others(s);
        if (s.closest("[data-ws-dock]") === dock && ids.length && r.bottom - b >= ROOM) drag.zones.unshift({ ids, dock, r: new DOMRect(r.left, b, r.width, r.bottom - b) });
      }
    }
    // (the outermost dock's own edge)
    drag.dock = docks.at(-1);
    drag.box = drag.dock?.getBoundingClientRect();
    if (drag.f) drag.fb = drag.f.getBoundingClientRect();
    shade ??= root.appendChild(mk("div", { className: "ws-drop" }));
    chip ??= root.appendChild(mk("div", { className: "ws-chip" }));
    chip.textContent = nameOf(m);
    drag.mw = box(m).width || 240;
    m.dataset.wsDrag = "";
    root.classList.add("ws-dragging");
    getSelection()?.removeAllRanges();
  }
  function hit(x, y) {
    const z = drag.zones.find((q) => x >= q.r.left && x <= q.r.right && y >= q.r.top && y <= q.r.bottom), r = z?.r || drag.box;
    if (z?.ids) return { ids: z.ids, dock: z.dock, zone: "foot", x: r.left, y: r.top, w: r.width, h: r.height };
    if (!r || x < r.left || x > r.right || y < r.top || y > r.bottom) return null;
    const fx = (x - r.left) / r.width, fy = (y - r.top) / r.height, near = { left: fx, right: 1 - fx, top: fy, bottom: 1 - fy };
    // between modules: only the dock's own edge (24 px of it) takes a drop
    if (!z && Math.min(x - r.left, r.right - x, y - r.top, r.bottom - y) > 24) return null;
    const zone = z && !z.own && fx > 0.3 && fx < 0.7 && fy > 0.3 && fy < 0.7 ? "tab" : Object.keys(near).reduce((a, b) => (near[a] <= near[b] ? a : b));
    const f = Math.max(0.15, Math.min(0.5, drag.mw / r.width));
    const p = { tab: [0, 0, 1, 1], left: [0, 0, f, 1], right: [1 - f, 0, f, 1], top: [0, 0, 1, 0.5], bottom: [0, 0.5, 1, 0.5] }[zone];
    return { id: z?.id ?? null, dock: z?.dock ?? drag.dock, zone, f, x: r.left + p[0] * r.width, y: r.top + p[1] * r.height, w: p[2] * r.width, h: p[3] * r.height };
  }
  document.addEventListener("pointermove", (e) => {
    if (!drag) return;
    if (!drag.on) { if (Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 6) return; begin(); }
    const dx = e.clientX - drag.x0, dy = e.clientY - drag.y0;
    chip.style.transform = `translate(${e.clientX + 12}px, ${e.clientY + 12}px)`;
    if (drag.f) return void (drag.f.style.transform = `translate(${dx}px, ${dy}px)`);
    const a = (drag.at = hit(e.clientX, e.clientY));
    shade.hidden = !a;
    if (a) shade.style.transform = `translate(${a.x}px, ${a.y}px) scale(${a.w / 100}, ${a.h / 100})`;
  });
  function stop(cancel) {
    const d = drag;
    drag = null;
    if (!d?.on) return;
    delete d.m.dataset.wsDrag;
    root.classList.remove("ws-dragging");
    // the click that ends a drag is not a click (a summary would toggle, a tab switch)
    addEventListener("click", swallow, { capture: true, once: true });
    setTimeout(() => removeEventListener("click", swallow, true), 0);
    if (d.f) {
      d.f.style.transform = "";
      if (cancel) return;
      const b = d.f.getBoundingClientRect(), r = rec(true), id = idOf(d.m);
      r.float[id] = { ...r.float[id], x: b.left + (d.dx || 0), y: b.top + (d.dy || 0) };
      return void (commit(), float(d.m, r.float[id]));
    }
    if (!cancel && d.at) moveTo(d.m, d.at.ids || d.at.id, d.at.zone, d.at.f, d.at.dock);
    window.dispatchEvent(new Event("synthlab-geometry"));
  }
  const swallow = (e) => { e.stopPropagation(); e.preventDefault(); };
  document.addEventListener("pointerup", (e) => {
    if (drag?.f && drag.on) Object.assign(drag, { dx: e.clientX - drag.x0, dy: e.clientY - drag.y0 });
    stop(false);
    // a floating panel resized by its corner keeps its size
    const r = rec();
    for (const f of document.querySelectorAll("#view ws-float")) {
      const g = r?.float[f.dataset.ws], w = Math.round(f.offsetWidth), h = Math.round(f.offsetHeight);
      if (g && (g.w !== w || g.h !== h)) { Object.assign(g, { w, h }); commit(); }
    }
  });
  document.addEventListener("pointercancel", () => stop(true));

  // ---- the sashes: a row's two neighbours share their width, a column's upper one takes a height
  let sashing = null;
  document.addEventListener("pointerdown", (e) => {
    const s = e.target.closest?.(".ws-sash");
    if (!s || e.button !== 0) return;
    e.preventDefault();
    const a = s.previousElementSibling, b = s.nextElementSibling, row = s.dataset.d === "row";
    sashing = { s, a, b, row, at: row ? e.clientX : e.clientY, wa: a.getBoundingClientRect()[row ? "width" : "height"], wb: b.getBoundingClientRect().width };
    s.setPointerCapture(e.pointerId);
    root.classList.add("resizing");
    root.classList.toggle("resizing-y", !row);
  });
  function sized(a, b, row, wa, wb) {
    const dock = a.closest("[data-ws-on]"), t = rec()?.docks[dock?.dataset.wsDock];
    if (!t) return;
    const pa = a.dataset.wsAt.split("."), i = +pa.pop(), split = nodeAt(t, pa.join("."));
    if (row) { const j = +b.dataset.wsAt.split(".").pop(); split.s[i] = wa; split.s[j] = wb; a.style.setProperty("--g", String(wa)); b.style.setProperty("--g", String(wb)); }
    else { split.s[i] = wa; if (wa) a.style.setProperty("--h", `${wa}px`); else a.style.removeProperty("--h"); }
  }
  document.addEventListener("pointermove", (e) => {
    if (!sashing) return;
    const { a, b, row, at, wa, wb } = sashing, d = (row ? e.clientX : e.clientY) - at;
    if (row) { const x = Math.max(120 - wa, Math.min(wb - 120, d)); sized(a, b, true, Math.round(wa + x), Math.round(wb - x)); }
    else sized(a, b, false, Math.round(Math.max(40, wa + d)));
  });
  const endSash = () => { if (!sashing) return; sashing = null; root.classList.remove("resizing", "resizing-y"); commit(); window.dispatchEvent(new Event("synthlab-geometry")); };
  document.addEventListener("pointerup", endSash);
  document.addEventListener("pointercancel", endSash);
  document.addEventListener("dblclick", (e) => {
    const s = e.target.closest?.(".ws-sash");
    if (!s) return;
    const a = s.previousElementSibling, b = s.nextElementSibling, row = s.dataset.d === "row";
    sized(a, b, row, row ? 1 : null, 1);
    commit();
  });
  document.addEventListener("keydown", (e) => {
    const s = e.target.closest?.(".ws-sash"), row = s?.dataset.d === "row";
    const by = s && (row ? { ArrowLeft: -1, ArrowRight: 1 } : { ArrowUp: -1, ArrowDown: 1 })[e.key];
    if (!by) return;
    e.preventDefault();
    const a = s.previousElementSibling, b = s.nextElementSibling, n = by * (e.shiftKey ? 64 : 16);
    const wa = a.getBoundingClientRect()[row ? "width" : "height"], wb = b.getBoundingClientRect().width;
    if (row) sized(a, b, true, Math.round(wa + n), Math.round(wb - n)); else sized(a, b, false, Math.round(Math.max(40, wa + n)));
    commit();
  });

  // ---- an added visualiser: the shared frame, drawn big ---------------------------------------
  const fits = new ResizeObserver((es) => { for (const { target: c } of es) Object.assign(c, { width: Math.max(1, Math.round(c.clientWidth * devicePixelRatio)), height: Math.max(1, Math.round(c.clientHeight * devicePixelRatio)) }); S.visuals?.touch(); });
  function sizeVis() { for (const c of document.querySelectorAll("#view .ws-added canvas")) if (!c.dataset.fit) { c.dataset.fit = "1"; fits.observe(c); } }
  let hooked = false;
  const hook = () => {
    if (hooked || !S.visuals) return;
    hooked = true;
    S.visuals.each((f, drawIt) => { for (const m of document.querySelectorAll("#view .ws-added[data-ws-vis]")) drawIt(m.querySelector("canvas"), S.visuals.choice(m.dataset.wsVis), f); });
  };
  // an added server fragment is no region: it is fetched again after the page's own posts
  let again = 0;
  document.addEventListener("htmx:after:request", (e) => {
    const r = rec(), from = e.target?.closest?.("[data-module]");
    if (!r?.add.some((a) => a.url) || (e.detail?.ctx?.request?.method || "GET") === "GET") return;
    clearTimeout(again);
    again = setTimeout(() => {
      for (const a of r.add) {
        const m = a.url && byId(a.id);
        if (m && m !== from && !m.contains(document.activeElement)) window.htmx?.ajax("GET", a.url, { target: m, swap: "outerMorph", source: document.getElementById("post-from") || m });
      }
    }, 400);
  });

  const apply = () => { if (maxed && !maxed.isConnected) maximise(null); hook(); paint(); };
  document.addEventListener("htmx:after:settle", apply); // (a swap always settles after: once is enough)
  document.addEventListener("DOMContentLoaded", apply);
  addEventListener("resize", () => { const r = rec(); for (const [id, g] of Object.entries(r?.float || {})) { const m = byId(id); if (m) float(m, g); } });
})();
