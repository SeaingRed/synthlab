// synthlab/studio/static/shell.js
// The shell's small parts: tooltips, the clip library's pin, and the status
// strip's progress line. None of them owns session state.
//
// 1. Tooltips. A control's `title` is its tooltip — written once, in the
//    template, as it always was. This turns it into a real one: after
//    DELAY ms of hover (never for a finger), at once on keyboard focus,
//    gone on a press, any key, when its control is swapped or scrolled away,
//    or when the pointer or the focus leaves. It is drawn in #tip, a `popover="manual"` element, so it
//    is in the top layer and no `overflow` clips it, and it is placed by
//    CSS anchor positioning (nothing measured here): shown with
//    `showPopover({ source })`, its control is its anchor, and style.css's
//    `position-area` puts it under the control, above when there is no room,
//    to the right of the side rail's icons (`data-at="rail"`), never off the
//    window (`position-try-fallbacks`), hidden while the control is scrolled
//    out of sight (`position-visibility`). While a hover's shows,
//    the control's `title` is lent to it (moved to `data-tip`, so the
//    browser's own tooltip does not come up over ours) and handed back
//    the moment it hides — at rest the markup is what the server sent. One
//    the keyboard's focus opened leaves the `title` where it is (a
//    glyph-only button would lose its name while it had the focus).
//    Without popover support this does nothing and the native title stays.
//    A press dismisses it until the pointer has left: a click is never
//    followed by a tooltip.
// 2. The clip library's pin (#clip-pin): `html[data-clips="pinned"]` and a
//    `clips` cookie the server reads, so the first paint is right; the
//    layout is style.css's (a column beside the page from 1600 px).
// 3. The status strip's task line (#st-task): events.js already writes a
//    long render's progress as one `.toast.progress` line in #toasts; this
//    mirrors its text beside the meters, so it is still readable after the
//    toast's corner is covered. No second EventSource.
// 4. Skip links: a `.skip-link[data-focus]` focuses the control it names.
// 5. The theme toggle under "system": named by, and posting, the theme the
//    OS shows (the server cannot see it).
// 6. The views' jump keys, Alt+Shift+1…0 in the rail's order.
// 7. Full screen (#fullscreen-btn, the rail): the Fullscreen API on the
//    whole document; pressed while it holds, however it was left (Esc).
// Budget: <= 118 code lines (95 -> 118: the skip links, the
// theme toggle and the jump keys — the shell's, and too small for an
// island of their own).

(() => {
  "use strict";
  const DELAY = 500;
  const tip = () => document.getElementById("tip");
  let host = null, timer = 0, pressed = null, focused = false; // focused: the keyboard opened it
  // the side rail (dense, a column): the tooltip stands to its icons' right
  const railSide = (el) => { const rail = el.closest(".topbar"); return !!rail && getComputedStyle(rail).flexDirection === "column"; };
  function show(el, byFocus = false) {
    const t = tip(), text = el.getAttribute("title");
    if (!t?.showPopover || !text || !el.isConnected) return;
    shut();
    host = el; focused = byFocus;
    // a hover lends the title (the browser's own tooltip would show too); the
    // keyboard's focus keeps it: what the control is called stays on it
    if (!byFocus) { el.dataset.tip = text; el.removeAttribute("title"); }
    t.textContent = text;
    t.toggleAttribute("data-at-rail", railSide(el));
    try { t.showPopover({ source: el }); } catch { return void hide(); } // its anchor: placed by CSS
  }
  function hide() {
    clearTimeout(timer); timer = 0;
    shut();
  }
  // take a showing tooltip down, leaving a hover that is still waiting alone
  function shut() {
    if (!host) return;
    const t = tip();
    if (t?.matches(":popover-open")) t.hidePopover();
    if (host.dataset.tip !== undefined && !host.hasAttribute("title")) host.setAttribute("title", host.dataset.tip);
    delete host.dataset.tip;
    host = null;
  }
  const titled = (n) => n?.closest?.("[title], [data-tip]");

  document.addEventListener("pointerover", (e) => {
    const el = titled(e.target);
    if (e.pointerType === "touch" || el === host || el === pressed) return;
    // the keyboard's tooltip stays while the mouse merely rests somewhere
    // (the browser re-sends pointerover when a dialog closes under it)
    if (host && focused) return;
    pressed = null; hide();
    if (el) timer = setTimeout(() => show(el), DELAY);
  });
  document.addEventListener("pointerout", (e) => {
    const el = titled(e.target);
    if (!el || el.contains(e.relatedTarget)) return;
    if (el === pressed) pressed = null;
    if (!(host && focused)) hide();
  });
  document.addEventListener("pointerdown", (e) => { pressed = titled(e.target); hide(); }, true);
  document.addEventListener("focusin", (e) => {
    const el = titled(e.target);
    if (el && e.target.matches?.(":focus-visible") && !e.target.matches("input:not([type=range]), textarea")) show(el, true);
  });
  document.addEventListener("focusout", shut); // (a hover that is waiting is the pointer's, not the focus's)
  document.addEventListener("keydown", hide, true);
  // (a scroll moves the control: its anchored tooltip follows it, and hides
  // while it is out of sight — CSS's, nothing measured here)
  // a swap took the control away: its tooltip goes with it (one that is
  // only waiting finds its control gone when the timer fires)
  document.addEventListener("htmx:after:settle", () => { if (host && !host.isConnected) shut(); });

  // ---- the clip library, pinned beside the page ---------------------------
  const root = document.documentElement;
  const pinned = () => { const b = document.getElementById("clip-pin"); if (b) b.setAttribute("aria-pressed", String(root.dataset.clips === "pinned")); };
  document.addEventListener("click", (e) => {
    if (!e.target.closest?.("#clip-pin")) return;
    const on = root.dataset.clips !== "pinned";
    if (on) root.dataset.clips = "pinned"; else delete root.dataset.clips;
    document.cookie = `clips=${on ? "pinned" : ""}; max-age=${on ? 31536000 : 0}; path=/; samesite=lax`;
    pinned(); window.dispatchEvent(new Event("synthlab-geometry")); // canvases and cached geometry re-fit
  });
  document.addEventListener("htmx:after:settle", pinned); // the drawer morphs back with the server's markup
  pinned();

  // ---- skip links ----------------------------------------------------------
  // `.skip-link` (base.html's "Skip to the page", a view's own): one with
  // `data-focus` lands on that control (a grid's one Tab stop); without, the
  // browser's own jump to its #id moves where the next Tab starts.
  document.addEventListener("click", (e) => {
    const a = e.target.closest?.(".skip-link[data-focus]"), to = a && document.querySelector(a.dataset.focus);
    if (!to) return;
    e.preventDefault();
    // a grid just swapped in can refuse the focus for a few frames (it is
    // `content-visibility: auto`, not laid out yet): ask again, up to ~0.5 s
    let n = 0;
    const go = () => { to.focus(); if (document.activeElement !== to && ++n < 30) requestAnimationFrame(go); };
    go();
  });

  // ---- the theme toggle under "system" --------------------------------------
  // The server cannot see the OS's scheme: under data-theme="system" the
  // toggle is named by the theme in force and posts it (`from`), so the
  // first press always changes what is seen.
  const dark = matchMedia("(prefers-color-scheme: dark)");
  function themeToggle() {
    const b = document.querySelector(".theme-toggle");
    if (!b || document.documentElement.dataset.theme !== "system") return;
    const now = dark.matches ? "night" : "day", to = now === "day" ? "night" : "day";
    b.setAttribute(b.hasAttribute("data-tip") ? "data-tip" : "title", `switch to the ${to} theme`);
    b.setAttribute("aria-label", `Switch to ${to} theme`);
    b.dataset.cmd = `Theme: switch to ${to}`;
    b.setAttribute("hx-vals", JSON.stringify({ from: now }));
  }
  dark.addEventListener("change", themeToggle);
  document.addEventListener("htmx:after:settle", themeToggle);
  themeToggle();

  // ---- the views' jump keys -------------------------------------------------
  // Alt+Shift+1…0: the rail's views in its order (each link's `data-keys`)
  document.addEventListener("keydown", (e) => {
    const d = /^Digit(\d)$/.exec(e.code)?.[1];
    if (!d || !e.altKey || !e.shiftKey || e.ctrlKey || e.metaKey) return;
    const a = document.querySelectorAll("nav.views a.view")[(+d + 9) % 10];
    if (a) { e.preventDefault(); a.click(); }
  });

  // ---- full screen ----------------------------------------------------------
  const fs = document.getElementById("fullscreen-btn");
  fs?.addEventListener("click", () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.())?.catch?.(() => {}));
  document.addEventListener("fullscreenchange", () => fs?.setAttribute("aria-pressed", String(!!document.fullscreenElement)));

  // ---- the status strip's task line ---------------------------------------
  const box = document.getElementById("toasts");
  if (!box) return;
  new MutationObserver(() => {
    const out = document.getElementById("st-task");
    if (out) out.textContent = [...box.querySelectorAll(".toast.progress")].map((p) => p.textContent).join(" · ");
  }).observe(box, { childList: true, subtree: true, characterData: true });
})();
