// synthlab/studio/static/soundmenu.js
// The one sound menu (fragments/sound_menu.html: base.html's #lane-sounds,
// every sound there is, once a page) and what reads it:
// - a Song lane's <select> (.track-sound-pick) takes its copy as it is
//   reached for — the pointer coming down on it, focus, a key — which is
//   before the browser opens it (moved here from songdock.js);
// - #sound-pick (fragments/sound_pick.html), the searchable menu a Perform
//   lane head's ▾ opens (Alt+S: the edited lane's): every word typed is
//   matched against the name and its group, ★ keeps your favourites, ↑ ↓
//   move, Enter or a click picks — POST /fragment/perform/lane/sound. The
//   list is rebuilt from the page's copy as it opens, then from
//   GET /fragment/sound-menu (a sound saved since the page was loaded: a
//   view swap never redraws base.html).
// A load asks what it replaces when there is more than one sound to
// replace (`[data-stack]`: the lane heads, Synths' layer strip) or the
// sound brings layers of its own (`data-layered`): this layer / the whole
// sound / ＋ add as a layer, the default — what a load always did — focused.
// Synths' Load (a card's name in #preset-list) asks the same, in the same
// popover with no list, and posts its own route with `as`.
// - the Song drawer's sound (`[data-track-sound]`, fragments/song_drawer.html:
//   the track the pattern plays on) opens the same menu for that TRACK —
//   POST /song/track/<id>/sound, nothing asked (a track holds one sound).
//   It browses: ↑ ↓ in the menu, or ↑ ↓ / the wheel on the closed button,
//   swap the sound as they go (debounced), so a playing ▶ Play or song is
//   heard through each (`song-changed` {now}: where it stands). One open,
//   or one run of steps on the button, is one `browse` token: ONE undo step.
// Budget: <= 210 code lines.

(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const S = () => window.synthlab || {};

  const fill = (e) => {
    const pick = e.target.closest?.(".track-sound-pick"), list = $("lane-sounds");
    if (pick && list && !pick.querySelector("optgroup")) pick.append(list.content.cloneNode(true));
  };
  for (const type of ["pointerdown", "focusin", "keydown"]) document.addEventListener(type, fill, true);

  let at = null; // what the popover is for: { lane, name, opener }, { card, opener } or { track, name, opener }
  let items = [], active = -1, star = false;
  // a browse of a track's sound: its token (one undo step), the debounced post, the sound last sent
  let browse = null, wait = null, sent = "", pos = null; // pos: where the button's steps stand ({ track, value }), kept across the drawer's redraws
  const el = (tag, cls, text) => Object.assign(document.createElement(tag), { className: cls, textContent: text });

  function build() {
    const list = $("spk-list"), tpl = $("lane-sounds");
    items = [];
    list.replaceChildren();
    for (const g of tpl?.content.querySelectorAll("optgroup") || []) {
      const head = el("li", "spk-group caps", g.label);
      head.setAttribute("role", "presentation");
      list.append(head);
      for (const o of g.querySelectorAll("option")) {
        const li = el("li", "menu-item spk-item", o.textContent);
        li.id = `spk-${items.length}`;
        li.setAttribute("role", "option");
        li.dataset.value = o.value;
        li.dataset.hay = `${o.textContent} ${g.label}`.toLowerCase();
        for (const k of ["layered", "star"]) if (o.hasAttribute(`data-${k}`)) li.dataset[k] = "";
        if (o.textContent === at.name) li.setAttribute("aria-current", "true"); // what the lane holds now
        li.head = head;
        items.push(li);
        list.append(li);
      }
    }
    filter();
  }

  function filter() {
    const words = $("spk-q").value.toLowerCase().split(/\s+/).filter(Boolean), heads = new Set();
    for (const li of items) {
      li.hidden = (star && !("star" in li.dataset)) || !words.every((w) => li.dataset.hay.includes(w));
      if (!li.hidden) heads.add(li.head);
    }
    for (const h of $("spk-list").querySelectorAll(".spk-group")) h.hidden = !heads.has(h);
    $("spk-none").hidden = heads.size > 0;
    const now = !words.length && items.find((li) => !li.hidden && li.hasAttribute("aria-current"));
    select(now || items.find((li) => !li.hidden));
  }

  function select(li) {
    items[active]?.removeAttribute("aria-selected");
    active = items.indexOf(li);
    li?.setAttribute("aria-selected", "true");
    li?.scrollIntoView({ block: "nearest" });
    $("spk-q").setAttribute("aria-activedescendant", li?.id || "");
  }

  function move(by) {
    const shown = items.filter((li) => !li.hidden), i = shown.indexOf(items[active]);
    select(shown[Math.max(0, Math.min(shown.length - 1, i + by))]);
    if (at.track && items[active]) { // a track's menu auditions as it moves
      for (const li of items) li.removeAttribute("aria-current");
      items[active].setAttribute("aria-current", "true"); // "now": what the track holds
      swap(at.track, items[active].dataset.value);
    }
  }

  // a track's sound, swapped: the post waits for the keys or the wheel to rest
  function swap(track, value, soon = 160) {
    clearTimeout(wait?.t);
    const token = (browse ??= Math.random().toString(36).slice(2, 12));
    const go = () => {
      wait = null;
      if (value !== sent) S().post?.(`/song/track/${encodeURIComponent(track)}/sound`, { sound: value, browse: token });
      sent = value;
    };
    wait = { go, t: setTimeout(go, soon) };
  }
  const flush = () => { if (wait) { clearTimeout(wait.t); wait.go(); } };
  const endBrowse = () => { flush(); browse = pos = null; sent = ""; };

  // ↑ ↓ / the wheel on the drawer's closed button: the next sound in the menu's order
  const sounds = () => [...($("lane-sounds")?.content.querySelectorAll("option") || [])];
  function step(btn, by) {
    const all = sounds(), cur = pos?.track === btn.dataset.trackSound ? pos.value : null;
    let i = cur ? all.findIndex((o) => o.value === cur) : all.findIndex((o) => o.textContent === btn.dataset.soundName);
    i = Math.max(0, Math.min(all.length - 1, i < 0 ? 0 : i + by));
    if (!all[i] || all[i].value === cur) return;
    pos = { track: btn.dataset.trackSound, value: all[i].value };
    btn.querySelector(".ds-name").textContent = all[i].textContent; // at once: the drawer's redraw follows
    swap(btn.dataset.trackSound, all[i].value, 220);
  }

  // the question: asked when a load could replace more than the one sound
  const stack = () => Number(document.querySelector("[data-stack]")?.dataset.stack || 1);
  function pick(value, name, layered) {
    if (at.track) return send(value, "");
    if (at.lane != null && !at.name) return send(value, ""); // an empty lane: a new one, nothing replaced
    if (stack() < 2 && !layered) return send(value, "");
    for (const id of ["spk-head", "spk-list", "spk-none"]) $(id).hidden = true;
    $("spk-choice").hidden = false;
    $("spk-say").textContent = `“${name}”${at.lane != null ? ` into lane ${at.lane + 1}` : ""}: what does it replace?`;
    $("spk-choice").dataset.value = value;
    $("spk-choice").querySelector('[data-as="add"]').disabled = stack() >= 4; // every slot holds a sound
    $("spk-choice").querySelector(`[data-as="${layered ? "whole" : "layer"}"]`).focus();
  }

  function send(value, how) {
    const go = at, s = S();
    $("sound-pick").hidePopover();
    if (go.track) { // the Song drawer: the track's sound, one undo step with the browse before it
      swap(go.track, value, 0);
      flush();
    } else if (go.card) { // Synths' Load: its own route, as the card posts it, told what it replaces
      const vals = { ...JSON.parse(go.card.getAttribute("hx-vals") || "{}"), as: how };
      s.post?.(go.card.getAttribute("hx-post"), vals, { target: "body", swap: "innerMorph" });
    } else {
      s.post?.("/fragment/perform/lane/sound", { index: String(go.lane), sound: value, as: how });
    }
    go.opener?.focus({ preventScroll: true });
  }

  // the menu again, through htmx (not a bare fetch into innerHTML) — the
  // page's headers, its error toast — swapped in only when it differs
  let fresh = false; // it came back different: an open list is built again
  const refresh = () => $("lane-sounds") && htmx.ajax("GET", "/fragment/sound-menu", { target: "#lane-sounds", swap: "outerHTML", select: "#lane-sounds", source: $("post-from") })
    .then(() => { if (fresh && $("sound-pick").matches(":popover-open") && !$("spk-list").hidden) build(); fresh = false; }, () => {});
  document.addEventListener("htmx:before:swap", (e) => {
    const t = e.detail?.tasks?.find((k) => k.target?.id === "lane-sounds");
    if (t) { fresh = t.fragment.firstElementChild?.innerHTML !== $("lane-sounds").innerHTML; if (!fresh) e.preventDefault(); }
  });

  function open(opener, what) {
    const p = $("sound-pick");
    if (p.matches(":popover-open")) p.hidePopover();
    endBrowse();
    at = { ...what, opener };
    $("spk-choice").hidden = true;
    $("spk-for").textContent = at.card ? "Load" : at.track ? `${at.trackName} · every section` : at.name ? `lane ${at.lane + 1} · ${at.name}` : `a new lane ${at.lane + 1}`;
    p.showPopover({ source: opener });
    if (at.card) return pick("", at.card.closest(".sound-card").dataset.name, at.layered);
    for (const id of ["spk-head", "spk-list"]) $(id).hidden = false;
    $("spk-star").setAttribute("aria-pressed", String(star));
    build();
    if (at.track) sent = items.find((li) => li.hasAttribute("aria-current"))?.dataset.value || ""; // picked again: nothing posts
    $("spk-q").focus({ preventScroll: true });
    $("spk-q").select();
    refresh();
  }

  document.addEventListener("click", (e) => {
    const head = e.target.closest?.("[data-sound-menu]");
    if (head) return open(head, { lane: Number(head.dataset.soundMenu), name: head.dataset.laneName || "" });
    const trk = e.target.closest?.("[data-track-sound]");
    if (trk) return open(trk, { track: trk.dataset.trackSound, trackName: trk.dataset.trackName, name: trk.dataset.soundName || "" });
    const item = e.target.closest?.("#spk-list .spk-item");
    if (item) return pick(item.dataset.value, item.textContent, "layered" in item.dataset);
    const how = e.target.closest?.("#spk-choice [data-as]");
    if (how) return send($("spk-choice").dataset.value, how.dataset.as);
    if (e.target.closest?.("#spk-star")) {
      star = !star;
      e.target.closest("#spk-star").setAttribute("aria-pressed", String(star));
      return filter();
    }
  });
  // Synths' Load: asks first when there are layers (capture: before htmx posts it)
  document.addEventListener("click", (e) => {
    const btn = e.target.closest?.("#preset-list .sound-card .load");
    if (!btn || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    const layered = btn.closest(".sound-card").hasAttribute("data-layered");
    if (stack() < 2 && !layered) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    open(btn, { card: btn, layered });
  }, true);

  document.addEventListener("input", (e) => { if (e.target.id === "spk-q") filter(); });
  // a browse ends with the menu (what is heard stays: Ctrl+Z gives back the one before),
  // or as the button's steps are left
  document.addEventListener("toggle", (e) => {
    if (e.target.id === "sound-pick" && !e.target.matches(":popover-open")) endBrowse(); // (not when open again since)
  }, true);
  for (const type of ["focusout", "pointerleave"]) {
    document.addEventListener(type, (e) => {
      const b = e.target.closest?.("[data-track-sound]");
      if (b && !$("sound-pick").matches(":popover-open")) endBrowse();
    }, true);
  }
  document.addEventListener("wheel", (e) => {
    const b = e.target.closest?.("[data-track-sound]");
    if (!b || !e.deltaY || e.ctrlKey) return;
    e.preventDefault();
    if (performance.now() - (b.wheelAt || 0) > 90) step(b, Math.sign(e.deltaY));
    b.wheelAt = performance.now();
  }, { passive: false });
  document.addEventListener("keydown", (e) => {
    const b = e.target.closest?.("[data-track-sound]");
    if (b && (e.key === "ArrowDown" || e.key === "ArrowUp") && !e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      return step(b, e.key === "ArrowDown" ? 1 : -1);
    }
    if (e.target.id === "spk-q" && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      return move(e.key === "ArrowDown" ? 1 : -1);
    }
    if (e.target.id === "spk-q" && e.key === "Enter" && items[active] && !items[active].hidden) {
      e.preventDefault();
      return items[active].click();
    }
    const seg = e.target.closest?.("#spk-choice [data-as]");
    if (seg && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      const bs = [...seg.parentElement.querySelectorAll("button:not(:disabled)")];
      bs[(bs.indexOf(seg) + (e.key === "ArrowRight" ? 1 : bs.length - 1)) % bs.length]?.focus();
      return e.preventDefault();
    }
    // Alt+S: the sound of what is edited — the Song drawer's track (the drawer
    // holding the keyboard, or no Perform lane head on the page), else Perform's edited lane
    if (e.code !== "KeyS" || !e.altKey || e.ctrlKey || e.metaKey || e.repeat || S().isTypingTarget?.(e.target)) return;
    const drawer = document.querySelector("#song-drawer:not([hidden]) [data-track-sound]");
    const mine = (e.target.closest?.("#song-drawer") && drawer) || document.querySelector(".lane-head.on .lh-sound") || drawer;
    if (!mine) return;
    e.preventDefault();
    mine.click();
  });
})();
