// synthlab/studio/static/tweaktype.js
// TYPE A VALUE: tweak.js's second file (tweak.js is the drag, the wheel,
// the eraser and the one write; it has no room for a field). On every
// view, a slider's or knob's read-out — its <output>, found by `for` or as
// the one beside it in the same label / cell — is a field in waiting:
//   click the read-out, or press Enter on the focused control; type;
//   Enter applies, Esc cancels, leaving the field applies.
// The number goes through `synthlab.tweak(el, value, true)`: clamped and
// snapped like a drag, with the `input` and `change` a hand would fire, so
// the form posts, undo sees one edit and a hardware surface reads the same
// control. Three things a read-out can say about itself:
//   <output data-pct>        it shows the value x 100 (a 0..1 control in %)
//   <output data-unit="Hz">  the unit audio.js writes after the number
//   <output data-untyped>    it is not the control's value in the control's
//                            units (a Song lane's point: the slider is 0..1,
//                            the read-out the parameter's own Hz): no field
// A log slider (`data-log`: its position is 0..1, its hidden twin before it
// posts the real value) takes the typed value EXACTLY: the slider snaps to
// the nearest position, the twin and the read-out carry what was typed —
// as the double-click reset does.
// The field is `.val-edit` (style.css, section 15); it never bubbles its
// own `input` / `change`, so typing is not an edit of the form around it.
//
// TYPE A NAME: the one popover every "name it" action asks in
// — an `hx-prompt` control (⏺ Bounce, ★ Keep a take, loop the lane, a
// rename, the Voice lab's keeps) — in place of the browser's prompt(),
// which opened in the middle of the window with an empty default. It opens
// under the control (`#name-pop.dialog`), the prompt as its label, the
// field prefilled with `data-name` (or `data-name-from`'s text) and selected; Enter saves, Esc or a
// click away cancels. The official extension asks `window.htmxPrompt(text)`
// and wants the answer at once: the first ask opens the popover and
// answers null (no request); Save clicks the control again, and that ask
// answers with the name — the same request, `HX-Prompt` and all.
//
// A CONFIRM: the one popover every `hx-confirm` asks in
// (`#confirm-pop.dialog`), in place of the browser's confirm(), which
// stops every script on the page while it was open — the song (song.js
// schedules 1.5 s ahead) went silent into it, the jam and the click with
// it. htmx 4's `htmx:confirm` hands over `issueRequest` / `dropRequest`:
// the request waits for OK (Enter) and never leaves on Cancel, Esc or a
// click away. Under the control that asked (in the window's centre when it
// is hidden — a row of a closed menu), and it arrives in the studio's
// motion: a drop and a bloom, the answers after (GSAP; none when calm).
// Budget: <= 132 code lines (60 + the name popover's 35 + the confirm's 37).

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const RANGE = 'input[type="range"]';
  const outOf = (el) => (el.id && document.querySelector(`output[for="${CSS.escape(el.id)}"]`)) || el.parentElement?.querySelector("output");
  const rangeOf = (out) => document.getElementById(out.getAttribute("for") || "") || out.parentElement?.querySelector(RANGE);
  function apply(el, v, pct) {
    if (pct) v /= 100;
    if (!el.dataset.log) return void S.tweak(el, v, true);
    const lo = +el.dataset.min, hi = +el.dataset.max, exact = Math.min(hi, Math.max(lo, v)), twin = el.previousElementSibling;
    const moved = S.tweak(el, Math.log(exact / lo) / Math.log(hi / lo), true);
    if (!twin?.name) return;
    twin.value = String(exact); // the slider's position is snapped; the field posts what was typed
    const out = outOf(el);
    if (out) out.textContent = `${+exact.toFixed(2)}${out.dataset.unit ? ` ${out.dataset.unit}` : ""}`;
    if (!moved) twin.dispatchEvent(new Event("input", { bubbles: true }));
  }
  function type(el) {
    const out = el?.matches?.(RANGE) && !el.disabled && outOf(el);
    if (!out || out.querySelector("input") || out.hasAttribute("data-untyped") || !S.tweak) return;
    const was = out.textContent, pct = out.hasAttribute("data-pct");
    const now = (el.dataset.log ? S.logVal(el) : +el.value) * (pct ? 100 : 1);
    const box = Object.assign(document.createElement("input"), { className: "val-edit", value: String(+now.toFixed(3)), inputMode: "decimal" });
    box.setAttribute("aria-label", `type a value: ${el.getAttribute("aria-label") || el.name || el.id}`);
    let over = false; // removing the box blurs it: that blur must not run this again
    const done = (ok, refocus) => {
      if (over) return;
      over = true;
      const v = parseFloat(box.value);
      out.textContent = was;
      if (ok && Number.isFinite(v) && v !== +now.toFixed(3)) apply(el, v, pct);
      if (refocus) el.focus({ preventScroll: true });
    };
    box.addEventListener("input", (e) => e.stopPropagation()); // typing is not an edit of the form yet
    box.addEventListener("change", (e) => e.stopPropagation());
    box.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); done(e.key === "Enter", true); }
    });
    box.addEventListener("blur", () => done(true, false));
    out.replaceChildren(box);
    box.select();
  }
  document.addEventListener("click", (e) => {
    const out = e.target.closest?.("output");
    if (out && !e.target.closest("input")) type(rangeOf(out));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !e.target.matches?.(RANGE) || e.ctrlKey || e.metaKey || e.altKey) return;
    e.preventDefault();
    type(e.target);
  });

  // ---- a name: the one popover every hx-prompt asks in ----------------------
  let asker = null, named = null, pop = null;
  document.addEventListener("click", (e) => { asker = e.target.closest?.("[hx-prompt]") || null; }, true);
  function namePop() {
    if (pop) { if (!pop.isConnected) document.body.append(pop); return pop; } // a body morph drops it
    pop = Object.assign(document.createElement("div"), { id: "name-pop", className: "dialog pop" });
    pop.popover = "auto";
    pop.innerHTML = '<form><label><span></span><input class="field" maxlength="60" autocomplete="off" spellcheck="false"></label>'
      + '<span class="dialog-acts"><button type="button" class="btn btn-s btn-ghost">Cancel</button>'
      + '<button type="submit" class="btn btn-s btn-primary">Save</button></span></form>';
    const form = pop.querySelector("form"), box = pop.querySelector("input");
    box.addEventListener("keydown", (e) => e.stopPropagation()); // a letter typed is not a note
    pop.querySelector('[type="button"]').addEventListener("click", () => pop.hidePopover());
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const el = pop.asker;
      named = { el, v: box.value.trim() };
      pop.hidePopover();
      el.click(); // asks again: this time the answer is the name
      el.focus({ preventScroll: true });
    });
    pop.addEventListener("toggle", (e) => { if (e.newState === "closed" && !named) pop.asker?.focus({ preventScroll: true }); });
    document.body.append(pop);
    return pop;
  }
  window.htmxPrompt = (text) => {
    const el = asker;
    asker = null;
    if (named && el === named.el) { const v = named.v; named = null; return v; }
    named = null;
    if (!el?.isConnected) return window.prompt(text); // not asked by a click: the browser's own
    const p = namePop(), box = p.querySelector("input");
    p.asker = el;
    p.querySelector("label > span").textContent = text;
    // the default: `data-name`, or the text of what `data-name-from` names (Bounce: the
    // sound's, read off its own region so the bar never redraws for it); blank is an
    // answer too — each route has its own
    box.value = el.dataset.name ?? document.querySelector(el.dataset.nameFrom || null)?.textContent.trim() ?? "";
    p.showPopover({ source: el }); // the control is its anchor (and its menu stays open)
    box.select();
    return null;
  };

  // ---- a confirm: the one popover every hx-confirm asks in -------------------
  // htmx 4 hands `htmx:confirm` a promise's two ends; held, the request waits
  // for OK (Enter) or never leaves (Cancel, Esc, a click away)
  let ask = null;
  function confirmPop() {
    if (ask) { if (!ask.isConnected) document.body.append(ask); return ask; } // a body morph drops it
    ask = Object.assign(document.createElement("div"), { id: "confirm-pop", className: "dialog pop confirm-pop" });
    ask.popover = "auto";
    ask.innerHTML = '<p class="confirm-q"></p><span class="dialog-acts"><button type="button" class="btn btn-s btn-ghost">Cancel</button>'
      + '<button type="button" class="btn btn-s btn-primary">OK</button></span>';
    const [no, ok] = ask.querySelectorAll("button");
    ask.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter" && e.target !== no) { e.preventDefault(); ok.click(); } }); // a letter is not a note
    no.addEventListener("click", () => ask.hidePopover());
    ok.addEventListener("click", () => { const d = ask.held; ask.held = null; ask.hidePopover(); d?.issueRequest(); });
    ask.addEventListener("toggle", (e) => {
      if (e.newState !== "closed" || ask.matches(":popover-open")) return; // (a hide and a show in one task: one toggle)
      ask.held?.dropRequest(); ask.held = null; // closed without OK: no request
      if (ask.from?.isConnected) ask.from.focus({ preventScroll: true });
    });
    document.body.append(ask);
    return ask;
  }
  document.addEventListener("htmx:confirm", (e) => {
    const d = e.detail, el = d?.ctx?.sourceElement;
    if (!d?.issueRequest) return; // (not htmx 4's: let it ask its own way)
    e.preventDefault();
    const p = confirmPop();
    const was = p.held; p.held = null; was?.dropRequest(); // one question at a time: the last one drops
    if (p.matches(":popover-open")) p.hidePopover();
    p.held = d; p.from = el;
    p.querySelector(".confirm-q").textContent = d.ctx.confirm;
    const anchored = el?.isConnected && el.checkVisibility?.();
    p.classList.toggle("confirm-free", !anchored); // a control in a closed menu: the window's centre
    const g = !window.synthlab?.calm?.() && window.gsap; // the entrance: a drop, a bloom, the answers after
    p.classList.toggle("by-gsap", !!g); // (and no CSS transition fighting it)
    p.showPopover(anchored ? { source: el } : undefined);
    p.querySelector(".btn-primary").focus({ preventScroll: true });
    if (g) {
      g.fromTo(p, { opacity: 0, y: -10, scale: 0.92 }, { opacity: 1, y: 0, scale: 1, duration: 0.32, ease: "back.out(1.8)", clearProps: "transform" });
      g.fromTo(p, { filter: "blur(8px)" }, { filter: "blur(0px)", duration: 0.22, ease: "power2.out", clearProps: "filter" }); // (no overshoot: a blur below 0 sticks)
      g.fromTo(p.querySelectorAll(".confirm-q, .dialog-acts .btn"), { opacity: 0, y: 6 }, { opacity: 1, y: 0, duration: 0.24, stagger: 0.05, delay: 0.08, ease: "power2.out", clearProps: "transform" });
    }
  });
})();
