// synthlab/studio/static/deckui.js
// The control surface's two Decks, and the one panel that shows them.
//   - The virtual deck (#deck-panel; the rail's "deck" button): eight keys,
//     the strip, four knobs. It is the feature without hardware, where the
//     mapping is seen, and what the browser journeys drive.
//       a knob is a real `input[type=range].knob` that MIRRORS its slot's
//       control (min / max / step / value / the arc), so every gesture on
//       it is tweak.js's, as on any knob in the app — drag, Shift or the
//       barrel = fine, the wheel when focused, arrows = one step, the
//       eraser — and its `input` is handed to `deck.put`. Enter / Space or
//       a double-click (the eraser sends one): a press = the default.
//       The strip: tap, swipe, hold, as on the device.
//     DOCKED, never over the page: `html[data-deck]` — a strip along the
//     bottom ("bottom", the default: the device's own landscape shape, and
//     it works at every width) or a column at the right ("side", from
//     1200 px); the page makes room (style.css section 23) and the keys
//     dock stands on it. Open / closed and which dock are per browser.
//   - The hardware deck: a Stream Deck + opened by deckhid.js. ONE drawing,
//     deckart.js's ("Afterhours": glass keys, a luminous strip), paints the
//     virtual deck's canvases and the device's images, which go out as JPEG
//     — only the regions whose content changed, on the frame that changed
//     them, ahead of any motion (`hardware`: urgent in deckhid.js's queue);
//     a region whose MOTION alone moved (a glow, sparks, the pulse, the
//     spectrum, the beat) at most 20 a second (a key 15), and only while the
//     link is free. Both are drawn in THE PAGE'S OWN TOKENS (deckart.js
//     `inks`): the device follows the theme's colours. The virtual deck
//     redraws only what changed too (nothing at rest), on the next frame
//     after a change and a frame at a time while something moves; it slides
//     a new page of keys or a new strip in (GSAP, 0.14 s; not under calm
//     motion), and its glass leans toward the pointer with a light on it.
// Built in script (base.html belongs to the shell). The panel hangs off
// <html>, BESIDE <body>: a full-body morph (Undo, a panel's Defaults)
// removes whatever the response does not hold, and a panel re-made under
// the hand lost the knob's focus mid-gesture. Only the rail button lives
// in the body's top bar; it is put back after such a morph.
// No named field anywhere in it — `htmx.ajax` posts every named input on
// the page. Its own actions are `data-cmd` buttons, so the palette lists
// them and the deck's keys bind to them like any command.
// Budget: <= 300 code lines (255 -> 275: the virtual deck draws only what
// changed, animates while something moves, slides pages in; the drawing
// itself is deckart.js's. 275 -> 300: the device's pump
// puts content first and encodes in parallel, the glass leans to the pointer).

(() => {
  "use strict";
  const S = window.synthlab, D = () => S.deck, W = 800, H = 100;
  const root = document.documentElement, four = [0, 1, 2, 3];
  const kept = (k, v) => { try { return v === undefined ? localStorage.getItem(k) : localStorage.setItem(k, v); } catch { return null; } };
  let open = kept("synthlab.deck.open") === "1", side = kept("synthlab.deck.dock") === "side";
  let hid = null, panel = null;
  const dock = () => { if (open) root.dataset.deck = side ? "side" : "bottom"; else delete root.dataset.deck; };
  dock(); // before the first paint where it can be: the page is laid out around it

  // ---- drawing: deckart.js paints both decks; here, only what changed -----------
  const A = S.deckArt, KEY = A.size.KEY, DPR = Math.min(2, devicePixelRatio || 1), calm = () => S.calm();
  const G = () => (!calm() && window.gsap) || null;
  // ---- the hardware Deck ---------------------------------------------------------
  // CONTENT FIRST AND AT ONCE: an image whose content changed (a dial turned, a
  // key lit) is drawn and encoded on the frame that brought it and handed to
  // deckhid.js's queue as urgent, ahead of any motion waiting for the link.
  // The encode is toDataURL's, synchronous: ~0.1 ms for a region, where
  // toBlob / convertToBlob answered ~12 ms later, measured, idle or busy; the
  // sheets are CPU canvases (willReadFrequently: no GPU read-back).
  // MOTION AT WHAT THE LINK CARRIES: an image whose motion alone moved goes at
  // most every MOTION ms (a strip region 20 a second, a key 15), at a lower
  // JPEG quality, and only while nothing waits for the link — so the rate
  // falls to whatever the USB link takes and the queue never grows. A motion
  // frame held back is sent later, so the last frame of a motion always is.
  const MOTION = { s: 50, k: 66 };
  const sheets = new Map(); // one canvas a region and a key, reused
  function sheet(k, w, h) {
    let c = sheets.get(k);
    if (c?.width !== w) sheets.set(k, (c = Object.assign(document.createElement("canvas"), { width: w, height: h })));
    return c.getContext("2d", { willReadFrequently: true });
  }
  function jpeg(c, q) {
    const url = c.toDataURL("image/jpeg", q), s = atob(url.slice(url.indexOf(",") + 1)), b = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) b[i] = s.charCodeAt(i);
    return b;
  }
  function hardware(raw) {
    const sent = new Map();
    let face = null, layout = "", ink = "", timer = 0;
    // 2 = its content changed: now; 1 = its motion moved and is due; 0 = nothing; -1 = motion, later
    function due(k, sig, amb, now) {
      const was = sent.get(k);
      if (was?.sig === sig && was.amb === amb) return 0;
      if (was?.sig === sig && (now - was.at < MOTION[k[0]] || raw.backlog?.() > 0)) return -1;
      sent.set(k, { sig, amb, at: now });
      return was?.sig === sig ? 1 : 2;
    }
    function push() {
      if (!face) return;
      const f = face, lay = `${f.view}${f.zoom >= 0}`, now = performance.now(), theme = A.inks(), jobs = [];
      let later = false;
      if (theme !== ink) { ink = theme; sent.clear(); } // the theme changed: every image again
      if (lay !== layout) { layout = lay; for (const k of [...sent.keys()]) if (k[0] === "s") sent.delete(k); }
      for (const r of A.regions(f, now)) {
        const d = due(`s${r.x}`, r.sig, r.amb, now);
        later ||= d < 0;
        if (d <= 0) continue;
        const g = sheet(`s${r.x}`, r.w, H);
        g.setTransform(1, 0, 0, 1, -r.x, 0); r.draw(g, true);
        jobs.push({ d, c: g.canvas, go: (b) => raw.setStrip(r.x, 0, r.w, H, b, d > 1) });
      }
      for (const [i, k] of A.keys(f, now).entries()) {
        const d = due(`k${i}`, k.sig, k.amb, now);
        later ||= d < 0;
        if (d <= 0) continue;
        const g = sheet(`k${i}`, KEY, KEY);
        k.draw(g, KEY, KEY, 0, true);
        jobs.push({ d, c: g.canvas, go: (b) => raw.setKey(i, b, d > 1) });
      }
      jobs.sort((a, b) => b.d - a.d); // content before motion
      try { for (const j of jobs) j.go(jpeg(j.c, j.d > 1 ? 0.88 : 0.72)); } catch { /* unplugged mid-frame */ }
      if (later && !timer) timer = setTimeout(() => { timer = 0; push(); }, MOTION.s);
    }
    return { name: raw.name, show(f) { face = f; push(); kick(); }, motion: push, close: () => { clearTimeout(timer); face = null; return raw.close(); } };
  }
  const ELGATO = "quit the Elgato Stream Deck app first (tray icon → Quit): it holds the device";
  async function connect(device) {
    const H0 = S.deckHid;
    try {
      device ??= await H0.request();
      if (!device) return;
      hid = hardware(await H0.open(device, (ev) => { if (ev?.type === "keydown") lightKey(ev.index); D().handle(ev); }));
      D().attach(hid);
      kept("synthlab.deck.hid", "1");
    } catch (err) {
      hid = null;
      if (err?.name !== "NotFoundError") S.toast?.(`Stream Deck: ${err?.message || err} — ${ELGATO}`, "error");
    }
    status();
  }
  async function disconnect(forget) {
    if (!hid) return;
    const d = hid; hid = null;
    D().detach(d);
    if (forget) kept("synthlab.deck.hid", "0");
    await d.close();
    status();
  }

  // ---- the panel ---------------------------------------------------------------
  const el = (tag, props = {}, ...kids) => {
    const n = Object.assign(document.createElement(tag), props);
    for (const [k, v] of Object.entries(props.attrs || {})) n.setAttribute(k, v);
    n.append(...kids);
    return n;
  };
  // one of the deck's own actions: a canonical button that declares its command (the palette's, and a key's)
  const btn = (id, text, cmd, title, cls = "", group = "Stream Deck") => el("button", { id, type: "button", className: `btn btn-s ${cls}`, textContent: text, title, attrs: cmd ? { "data-cmd": cmd, "data-cmd-group": group } : {} });
  function rail() {
    if (document.getElementById("deck-btn")) return;
    // (group "View", with the rail's other toggles: the panel's own commands list last)
    const b = btn("deck-btn", "deck", "Stream Deck: show / hide the virtual deck", "control surface: a virtual Stream Deck + (dials, strip, keys), docked under or beside the page", "", "View");
    b.setAttribute("aria-pressed", String(open));
    document.getElementById("midi-btn")?.before(b);
  }
  function build() {
    rail();
    panel = el("section", { id: "deck-panel", attrs: { "aria-label": "Control surface" } },
      el("header", { className: "panel-head" },
        el("h2", { className: "caps", textContent: "Deck" }), el("i", { id: "deck-led", className: "led" }), el("small", { id: "deck-status" }),
        el("span", { id: "deck-meter", className: "meter", hidden: true, attrs: { role: "img", "aria-label": "master level" } }, el("i"), el("b")),
        btn("deck-mode", "", "Deck dials: macros / focus", "the dials: the four macros, or the panel of the control you touched last"),
        btn("deck-meters", "meters", "Deck strip: values / meters", "the strip: the dials' values, or the master level and the live voice's CPU"),
        btn("deck-prev", "◀", "Deck: previous four controls", "the previous four controls of the panel (focus)", "btn-icon"),
        btn("deck-next", "▶", "Deck: next four controls", "the next four controls of the panel (focus)", "btn-icon"),
        btn("deck-connect", "", "Stream Deck: connect / disconnect", "a Stream Deck + over WebHID (Chrome or Edge, on this machine)"),
        btn("deck-side", "", "Deck: dock under / beside the page", "dock the deck under the page, or beside it (from 1200 px)", "btn-ghost")),
      // a key: its face is a canvas (the device's own drawing), its name the button's text
      el("div", { id: "deck-keys" }, ...[...Array(8).keys()].map((i) => el("button", { type: "button", className: "btn", attrs: { "data-key": i } },
        el("canvas", { attrs: { "aria-hidden": "true" } }), el("span", { className: "visually-hidden" })))),
      el("canvas", { id: "deck-strip", width: W * DPR, height: H * DPR, attrs: { role: "img", "aria-label": "touch strip: tap a column to zoom, swipe to page, hold to reset" } }),
      el("div", { id: "deck-dials" }, ...four.map((i) => el("div", { className: "deck-dial" },
        el("input", { type: "range", className: "knob knob-l", title: "a dial: drag, wheel or arrows (Shift: fine); Enter or double-click: the default", attrs: { "data-dial": i } }),
        el("span", { className: "caps" }),
        el("button", { type: "button", className: "btn btn-ghost btn-s", textContent: "learn", attrs: { "data-learn": `slot:${i}`, "aria-label": `learn a MIDI knob for dial ${i + 1}` } })))),
      el("div", { id: "deck-bind" }, el("p", { id: "deck-note" }), btn("deck-learn-last", "", "", "bind the next MIDI knob you turn to the control you touched last"),
        el("ul", { id: "deck-map", attrs: { "aria-label": "learned MIDI knobs" } })));
    root.append(panel);
    panel.addEventListener("pointermove", lean, { passive: true });
    panel.addEventListener("pointerleave", lean);
    show(open);
    status();
  }
  // the glass leans toward the pointer and a light follows it: four numbers a
  // frame, drawn by section 23's rules (never under calm motion, nor for a finger)
  let leaning = 0, at = null;
  function lean(e) {
    if (calm() || e.pointerType === "touch") return;
    at = e.type === "pointerleave" ? null : e;
    leaning ||= requestAnimationFrame(() => {
      leaning = 0;
      const b = panel.getBoundingClientRect(), x = at ? (at.clientX - b.left) / b.width : 0.5, y = at ? (at.clientY - b.top) / b.height : 0.5;
      panel.style.setProperty("--lean-x", (2 * x - 1).toFixed(3));
      panel.style.setProperty("--lean-y", (2 * y - 1).toFixed(3));
      panel.style.setProperty("--glint-x", `${(x * 100).toFixed(1)}%`);
      panel.style.setProperty("--glint-y", `${(y * 100).toFixed(1)}%`);
      panel.toggleAttribute("data-lit", !!at);
    });
  }
  function show(on, toSide = side) {
    open = on; side = toSide;
    kept("synthlab.deck.open", on ? "1" : "0"); kept("synthlab.deck.dock", side ? "side" : "bottom");
    dock();
    document.getElementById("deck-btn")?.setAttribute("aria-pressed", String(on));
    panel.querySelector("#deck-side").textContent = side ? "dock under" : "dock beside";
    if (on) { D().attach(virtual); virtual.show(D().face()); } else D().detach(virtual);
    window.dispatchEvent(new Event("synthlab-geometry")); // the page beside it re-fits its canvases
  }
  // the virtual deck's pixels: a region or a key is drawn again only when its
  // content or its motion moved (nothing at rest) — on the frame that moved it
  let face = null, drawn = new Map(), ticking = false, inkSeen = "", layKeys = "", layStrip = "";
  function paint(now) {
    if (!face || !open || !panel?.isConnected) return;
    const theme = A.inks(), strip = panel.querySelector("#deck-strip"), g = strip.getContext("2d"), lay = `${face.view}${face.zoom >= 0}`;
    if (theme !== inkSeen) { inkSeen = theme; drawn = new Map(); }
    if (drawn.get("lay") !== lay) { drawn = new Map([...drawn].filter(([k]) => k[0] === "k")); drawn.set("lay", lay); }
    for (const r of A.regions(face, now)) {
      const v = r.sig + r.amb;
      if (drawn.get(`s${r.x}`) === v) continue;
      drawn.set(`s${r.x}`, v);
      g.setTransform(DPR, 0, 0, DPR, 0, 0); r.draw(g);
    }
    const faces = A.keys(face, now);
    panel.querySelectorAll("#deck-keys canvas").forEach((c, i) => {
      const w = Math.round(c.clientWidth * DPR), h = Math.round(c.clientHeight * DPR), v = `${faces[i].sig}${faces[i].amb}${w}x${h}`;
      if (!w || !h || drawn.get(`k${i}`) === v) return;
      if (c.width !== w || c.height !== h) Object.assign(c, { width: w, height: h });
      drawn.set(`k${i}`, v);
      faces[i].draw(c.getContext("2d"), w, h, 11 * DPR);
    });
  }
  // while anything moves (a playing transport, an afterglow, sparks, a pressed key): a
  // frame at a time, for the panel and the device alike (a hidden tab: a timer, so the device keeps up)
  const soon = (f) => (document.hidden ? setTimeout(() => f(performance.now()), MOTION.s) : requestAnimationFrame(f));
  function tick(now) {
    ticking = false;
    paint(now);
    hid?.motion(); // the device's motion too (its pump throttles it)
    if ((open || hid) && A.busy(now)) { ticking = true; soon(tick); }
  }
  function kick() { if (!ticking && (open || hid)) { ticking = true; soon(tick); } }
  A.restyled = kick; // a hue or the theme moved: the next frame draws both decks again (a new ink)
  function lightKey(i) { A.press(i); kick(); } // a press flashes and rings, on the device too
  // a page of keys or a mode turned: a quick slide and fade on the virtual deck (the device just redraws)
  function turned(f) {
    const keys = f.keys.map((k) => k.label).join(), strip = `${f.mode}${f.view}${f.zoom}`, was = layStrip, fx = G();
    if (fx && layKeys && keys !== layKeys) fx.fromTo(panel.querySelectorAll("#deck-keys canvas"), { opacity: 0, scale: 0.92 }, { opacity: 1, scale: 1, duration: 0.14, stagger: 0.012, ease: "power2.out", clearProps: "opacity,transform" });
    if (fx && was && `${strip}${f.page}` !== was) fx.fromTo("#deck-strip", { opacity: 0.3, x: was.startsWith(strip) ? 12 : 0, y: was.startsWith(strip) ? 0 : 6 }, { opacity: 1, x: 0, y: 0, duration: 0.16, ease: "power3.out", clearProps: "opacity,transform" });
    layKeys = keys; layStrip = `${strip}${f.page}`;
  }
  for (const ev of ["resize", "synthlab-geometry"]) addEventListener(ev, () => { drawn = new Map(); paint(performance.now()); });
  const virtual = {
    show(f) {
      if (!panel?.isConnected) return;
      turned(f);
      face = f;
      paint(performance.now());
      kick();
      panel.querySelectorAll("[data-key]").forEach((b, i) => {
        const k = f.keys[i];
        b.lastElementChild.textContent = k.label;
        b.setAttribute("aria-pressed", String(k.on));
        b.setAttribute("aria-disabled", String(k.off));
      });
      panel.querySelectorAll("[data-dial]").forEach((k, i) => {
        const c = f.cols[i];
        Object.assign(k, { min: c.lo ?? 0, max: c.hi ?? 1, step: c.step ?? 0.01, disabled: !!c.off });
        k.value = c.raw ?? 0;
        k.style.setProperty("--fill", `${c.frac * 100}%`);
        k.setAttribute("aria-label", `dial ${i + 1}: ${c.name}`);
        k.setAttribute("aria-valuetext", c.value);
        Object.assign(k.nextElementSibling, { textContent: c.name, title: c.pinned ? "pinned to this dial (the control's menu takes it off)" : "" });
        k.parentElement.toggleAttribute("data-pinned", !!c.pinned);
      });
      const mode = panel.querySelector("#deck-mode"), meter = panel.querySelector("#deck-meter"), lastEl = D().lastControl();
      mode.textContent = f.mode === "macro" ? "Macros" : "Focus";
      mode.setAttribute("aria-pressed", String(f.mode === "focus"));
      panel.querySelector("#deck-meters").setAttribute("aria-pressed", String(!!f.meter));
      meter.hidden = !f.meter;
      meter.style.setProperty("--level", `${(f.meter?.level || 0) * 100}%`);
      panel.querySelector("#deck-learn-last").textContent = lastEl ? `learn: ${D().nameOf(lastEl)}` : "learn: touch a control";
    },
  };
  function status() {
    if (!panel) return;
    const why = S.deckHid.why(), armed = S.midiLearn.armed();
    panel.querySelector("#deck-status").textContent = hid ? `${hid.name} connected` : "virtual";
    panel.querySelector("#deck-led").toggleAttribute("data-on", !!hid);
    const c = panel.querySelector("#deck-connect");
    c.textContent = hid ? "Disconnect" : "Connect Stream Deck +";
    c.setAttribute("aria-disabled", String(!!why && !hid));
    // how to connect is the button's tooltip (as a line under it, it was cut off at 1920)
    if (!("tip" in c.dataset)) c.title = hid ? "Let go of the Stream Deck +" : `To connect: ${ELGATO}.`;
    panel.querySelector("#deck-note").textContent = armed ? `Learning ${armed.name}: turn a MIDI knob (MIDI must be on).`
      : hid ? "The deck's keys, strip and dials mirror this panel." : why || "";
    panel.querySelectorAll("[data-learn]").forEach((b) => b.setAttribute("aria-pressed", String(armed?.to === b.dataset.learn)));
    panel.querySelector("#deck-map").replaceChildren(...S.midiLearn.list().map((b, i) => el("li", {},
      `CC ${b.cc} · ch ${b.ch + 1} → ${b.name} `,
      el("button", { type: "button", className: "btn btn-ghost btn-s", textContent: b.mode, title: "absolute, or a relative encoding: two's complement, binary offset, sign-magnitude", attrs: { "data-cycle": i } }),
      el("button", { type: "button", className: "btn btn-ghost btn-s", textContent: "✕", attrs: { "data-unlearn": i, "aria-label": `forget CC ${b.cc}` } }))));
  }
  document.addEventListener("midimap-changed", status);

  // ---- gestures on the panel -------------------------------------------------------
  const send = (ev) => D().handle(ev);
  const DO = {
    "deck-btn": () => show(!open),
    "deck-side": () => show(true, !side),
    "deck-mode": () => D().set({ mode: D().state().mode === "macro" ? "focus" : "macro", zoom: -1 }),
    "deck-meters": () => D().set({ view: D().state().view === "meter" ? "params" : "meter", zoom: -1 }),
    "deck-prev": () => D().page(-1),
    "deck-next": () => D().page(1),
    "deck-connect": () => { if (hid) disconnect(true); else if (S.deckHid.why()) S.toast?.(S.deckHid.why(), "error"); else connect(); },
    "deck-learn-last": () => { const c = D().lastControl(); if (c && D().toOf(c)) S.midiLearn.arm(D().toOf(c), D().nameOf(c)); },
  };
  document.addEventListener("click", (e) => {
    const t = e.target.closest?.("#deck-btn, #deck-panel button");
    if (!t) return;
    const d = t.dataset;
    if (d.key) { lightKey(Number(d.key)); send({ type: "keydown", index: Number(d.key) }); }
    else if (d.learn) S.midiLearn.arm(d.learn, `dial ${Number(d.learn.slice(5)) + 1}`);
    else if (d.cycle) S.midiLearn.cycle(Number(d.cycle));
    else if (d.unlearn) S.midiLearn.remove(Number(d.unlearn));
    else DO[t.id]?.();
  });
  const dialOf = (e) => { const k = e.target.closest?.("[data-dial]"); return k ? Number(k.dataset.dial) : -1; };
  const reset = (i) => { send({ type: "dialdown", index: i }); send({ type: "dialup", index: i }); };
  // a knob's value is its slot's control's: what tweak.js or the keyboard set on it goes straight through
  document.addEventListener("input", (e) => { const i = dialOf(e); if (i >= 0) D().put(D().slots()[i], Number(e.target.value)); });
  document.addEventListener("dblclick", (e) => { if (dialOf(e) >= 0) reset(dialOf(e)); });
  // a gesture starts on a knob that mirrors its slot NOW (faces are drawn a beat after the page changes)
  for (const type of ["focusin", "pointerdown"]) document.addEventListener(type, (e) => { if (dialOf(e) >= 0) virtual.show(D().face()); }, true);
  document.addEventListener("keydown", (e) => {
    const i = dialOf(e);
    if (i >= 0 && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); reset(i); }
  });
  let drag = null; // a touch on the strip
  document.addEventListener("pointerdown", (e) => {
    const strip = e.target.closest?.("#deck-strip");
    if (!strip) return;
    drag = { strip, x: e.clientX, at: performance.now() };
    try { strip.setPointerCapture(e.pointerId); } catch { /* a synthetic pointer */ }
  });
  const letGo = (e) => {
    const d = drag;
    drag = null;
    if (!d || e.type === "pointercancel") return;
    const b = d.strip.getBoundingClientRect(), at = (x) => Math.round(((x - b.left) / b.width) * W);
    if (Math.abs(e.clientX - d.x) > 24) send({ type: "swipe", x: at(d.x), x2: at(e.clientX) });
    else send({ type: performance.now() - d.at > 500 ? "long" : "tap", x: at(d.x) });
  };
  document.addEventListener("pointerup", letGo);
  document.addEventListener("pointercancel", letGo);

  // the rail button is not in the server's page: a full-body morph takes it away
  document.addEventListener("htmx:after:settle", rail);
  document.addEventListener("DOMContentLoaded", async () => {
    build();
    globalThis.navigator?.hid?.addEventListener("disconnect", () => disconnect(false));
    const device = kept("synthlab.deck.hid") === "1" && await S.deckHid.granted(); // granted before: no prompt
    if (device) connect(device);
  });
})();
