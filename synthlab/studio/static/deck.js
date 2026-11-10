// synthlab/studio/static/deck.js
// The control surface: four dials, a touch strip and eight keys, as ONE
// model that every transport drives — the Stream Deck + over WebHID
// (deckhid.js), the virtual deck in the page (deckui.js) and learned MIDI
// CCs (midilearn.js). It holds no audio and no session state.
//   - A dial's slot resolves, when read, to a control already on the page
//     (a range input, a select or a radio group). EVERY WRITE IS tweak.js's:
//     `synthlab.tweak(el, value)` sets the control and dispatches the
//     `input` a hand would, `tweak.later(el)` its `change` 250 ms after the
//     last tick, `tweak.reset(el)` its default — so a dial, a CC, a pen and
//     a mouse take one path (read-out, a knob's arc, the live voice, the
//     debounced post, undo) and this file sets no value and posts nothing.
//     Ticks are summed and applied once per 16 ms.
//   - macro mode: the four macro knobs (#macro-0..3, on Synths); an
//     unassigned or absent one's dial takes the touched panel's next
//     control, else the page's own (`pageList`) — a dial is never dead.
//     focus mode: the panel of the last control touched — its closest
//     module / named section / fieldset / .fx-unit / section / form —
//     paged in fours.
//     A PINNED slot ("Put on dial N" in a control's menu: `pin(i, to)`,
//     `to` = "id:<id>" | "name:<field>") is that control in either mode,
//     wherever it is on the page; elsewhere the slot is the mode's.
//     A section's dials ("Put these on the deck" in its menu: `hold(to)`)
//     are pinned four at a time; Page ▶ turns their fours while they hold.
//   - one tick = 1% of the range (never under a step), x2 / x4 / x8 for a
//     fast turn; press + turn = one step; a press let go unturned = the
//     control's default.
//   - the strip: tap a column = zoom it (a tap then sets the value, the
//     left 60 px leave), swipe = page, long press = reset; or the meters.
//   - the keys: `KEYS` binds each to COMMANDS by name — the `data-cmd`
//     declarations palette.js harvests (`synthlab.commands()`), the first
//     one on this page winning — and runs one as the palette does: a click
//     on the control that declares it. A key whose command is not on the
//     page is dim; it is lit from that control's own pressed state.
// A Deck is anything with show(face): attach() it and call handle(event).
// A face goes to every Deck on the next animation frame after a change, one
// a frame however many things moved (`refresh`; a value alone reuses the
// keys and skips the command harvest) — it used to wait 90 ms on two timers.
// Preferences (mode, view, pins) are per browser: localStorage `synthlab.deck`.
// Budget: <= 260 code lines (230 -> 240: a dial is never dead, `pageList`;
// 240 -> 255: a section's dials held on the deck, `hold`;
// 255 -> 260: a page of keys for the keyboard dock's Play —
// note, triad, 7th, power, hold, latch, typing).

(() => {
  "use strict";
  window.synthlab ??= {};
  const S = window.synthlab, STORE = "synthlab.deck", N = 4, BACK = 60, STRIP = 800;
  const CTL = "input[type=range], select, [role=radiogroup][id]", TO = /^(id|name):[\w.-]{1,64}$/;
  const clamp01 = (v) => Math.min(1, Math.max(0, v));
  const st = { mode: "macro", view: "params", page: 0, keys: 0, zoom: -1, pins: ["", "", "", ""] };
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) || "{}");
    if (saved.mode === "focus") st.mode = "focus";
    if (saved.view === "meter") st.view = "meter";
    if (Array.isArray(saved.pins)) st.pins = st.pins.map((_, i) => (TO.test(saved.pins[i]) ? saved.pins[i] : ""));
  } catch { /* a blocked or mangled store: the defaults */ }
  const decks = new Set();
  let last = null, own = false, pages = 1;
  // controls whose pending `change` (tweak.later's, 250 ms on) is a dial's turn
  const turned = new WeakSet();

  // ---- a control, as a dial sees it ---------------------------------------
  // a select, or a radio group with an id (the pills rows, the wavetable picker): its options / radios, in order
  const sel = (el) => S.tweak.steps(el);
  // { lo, hi, step, raw }: a range's own travel, a stepped control's option numbers
  const span = (el) => (sel(el) ? { lo: 0, hi: sel(el).length - 1, step: 1, raw: S.tweak.chosen(sel(el)) } : { ...S.tweak.lim(el), raw: Number(el.value) });
  const outOf = (el) => (el.id && document.querySelector(`output[for="${CSS.escape(el.id)}"]`)) || null;
  // a control <-> the name a binding keeps for it ("id:p-cutoff", else "name:bpm"); find: only if it is on this page
  const toOf = (el) => (el.id ? `id:${el.id}` : el.name ? `name:${el.name}` : "");
  function find(to) {
    const [kind, key] = TO.test(to) ? [to.slice(0, to.indexOf(":")), to.slice(to.indexOf(":") + 1)] : [];
    const el = kind === "id" ? document.getElementById(key) : kind ? document.querySelector(`[name="${CSS.escape(key)}"]`) : null;
    return el?.matches(CTL) ? el : null;
  }
  function nameOf(el) {
    const lab = el.id && (document.querySelector(`label[for="${CSS.escape(el.id)}"]`) || document.getElementById(el.getAttribute("aria-labelledby") || ""));
    return (lab?.textContent || el.getAttribute("aria-label") || el.name || el.id || "").trim().slice(0, 28);
  }
  function textOf(el) {
    if (el.disabled) return "unassigned";
    const o = sel(el)?.[span(el).raw]; // an option's text, a radio's label's
    if (sel(el)) return (o?.text ?? o?.closest("label")?.textContent ?? "").trim();
    const out = outOf(el), { lo, hi } = span(el);
    if (out) return out.textContent.trim();
    return lo === 0 && hi === 1 ? `${Math.round(el.value * 100)}%` : String(Math.round(el.value * 100) / 100);
  }
  // the one way in: tweak.js's write, marked as the deck's own (not a "touch")
  function put(el, value) {
    if (!el || el.disabled || !Number.isFinite(value)) return;
    own = true;
    try { if (S.tweak(el, value)) { turned.add(el); S.tweak.later(el); } } finally { own = false; }
  }
  // `raw` ticks as turned, `pct` those ticks after acceleration, `steps` fine ones
  function nudge(el, raw, pct = raw, steps = 0) {
    if (!el) return;
    const { lo, hi, step, raw: at } = span(el);
    put(el, sel(el) ? at + raw : at + pct * Math.max(step, (hi - lo) / 100) + steps * step);
  }
  function setEl(el, n) {
    if (!el) return;
    const { lo, hi } = span(el);
    put(el, lo + clamp01(n) * (hi - lo));
  }
  function reset(el) {
    if (!el || el.disabled) return;
    own = true;
    try { S.tweak.reset(el); } finally { own = false; }
  }

  // ---- which four ------------------------------------------------------------
  const shown = (el) => el.offsetParent !== null && !el.closest("#deck-panel");
  // (a module or a named section, as menu.js reads them, before the markup's own panels)
  const panelOf = (el) => el.closest("[data-module], [data-menu-section], fieldset, .fx-unit, section, form") || document.body;
  function panelList() {
    if (last && !last.isConnected) last = (last.id && document.getElementById(last.id)) || null;
    return last ? [...panelOf(last).querySelectorAll(CTL)].filter(shown) : [];
  }
  // the page's own, for a dial nothing else names: its knobs first, then its sliders and lists
  function pageList() {
    const all = [...(document.getElementById("view")?.querySelectorAll(CTL) || [])].filter((el) => shown(el) && !el.disabled && !el.closest(".piano-wrap"));
    return [...all.filter((el) => el.matches(".knob")), ...all.filter((el) => !el.matches(".knob"))];
  }
  function slots() {
    let sl;
    if (st.mode === "macro") {
      pages = 1;
      // an unassigned macro, or none on this page: the touched panel's next control turns instead, else the page's
      const mac = [0, 1, 2, 3].map((i) => document.getElementById(`macro-${i}`)).map((el) => (el && !el.disabled ? el : null));
      const taken = [...mac, ...st.pins.map(find)];
      const spare = (last ? panelList() : pageList()).filter((el) => !el.disabled && !taken.includes(el));
      sl = mac.map((el) => el || spare.shift() || null);
    } else {
      const touched = panelList(), all = touched.length ? touched : pageList();
      pages = Math.max(1, Math.ceil(all.length / N));
      st.page = ((st.page % pages) + pages) % pages;
      sl = [0, 1, 2, 3].map((k) => all[st.page * N + k] || null);
    }
    return sl.map((el, i) => find(st.pins[i]) || el);
  }
  function touched(e) {
    const el = e.target?.closest?.(CTL);
    if (own || !el || el === last || !shown(el)) return;
    last = el;
    if (st.mode === "focus") { st.page = Math.floor(panelList().indexOf(el) / N); st.zoom = -1; }
    refresh();
  }
  for (const type of ["focusin", "pointerdown", "input"]) document.addEventListener?.(type, touched, true);

  // ---- dials ---------------------------------------------------------------
  const acc = [0, 1, 2, 3].map(() => ({ raw: 0, pct: 0, steps: 0, at: 0, held: false, turned: false }));
  let flushing = 0;
  function turn(i, ticks, fine) {
    const a = acc[i];
    if (!a || !Number.isFinite(ticks) || !ticks) return;
    const now = performance.now(), dt = now - a.at;
    a.at = now;
    a.turned = true;
    a.raw += ticks;
    if (fine ?? a.held) a.steps += ticks;
    else a.pct += ticks * (dt < 25 ? 8 : dt < 60 ? 4 : dt < 120 ? 2 : 1);
    flushing ||= setTimeout(flush, 16);
  }
  function flush() {
    flushing = 0;
    const sl = slots();
    acc.forEach((a, i) => {
      if (a.raw) nudge(sl[i], a.raw, a.pct, a.steps);
      a.raw = a.pct = a.steps = 0;
    });
    refresh(false);
  }
  function press(i, down) {
    const a = acc[i];
    if (!a) return;
    if (down) { a.held = true; a.turned = false; return; }
    if (a.held && !a.turned) reset(slots()[i]);
    a.held = false;
    refresh();
  }

  // ---- the strip -------------------------------------------------------------
  const set = (next) => {
    Object.assign(st, next);
    try { localStorage.setItem(STORE, JSON.stringify({ mode: st.mode, view: st.view, pins: st.pins })); } catch { /* blocked */ }
    refresh();
  };
  // a SECTION's dials put on the deck from its menu (menu.js "Put these on
  // the deck"): pinned four at a time, the slots past its last left to the
  // mode; more than four page in fours — Page ▶ / a swipe turns its pages
  // while the pins are still one of them (a dial set or a pin ends it)
  let held = null; // { to: [...], at, name }
  const fours = (to, at) => [0, 1, 2, 3].map((k) => to[at * N + k] || "");
  const holding = () => (held && fours(held.to, held.at).every((p, i) => p === st.pins[i]) ? held : null);
  function hold(to, name = "") {
    to = [...new Set(to.filter((x) => TO.test(x)))];
    if (!to.length) return;
    held = { to, at: 0, name };
    set({ pins: fours(to, 0), zoom: -1 });
    say();
  }
  function say() {
    const h = holding(), n = Math.ceil(h.to.length / N), on = fours(h.to, h.at).map(find).filter(Boolean);
    if (on.length) S.toast?.(`${h.name ? `${h.name}: ` : ""}${on.map(nameOf).join(", ")} on dials 1-${on.length}${n > 1 ? ` (page ${h.at + 1} of ${n}: Page ▶ for the next four)` : ""}`);
  }
  const page = (d) => {
    const h = holding();
    if (h) { const n = Math.ceil(h.to.length / N); h.at = (h.at + d + n) % n; set({ pins: fours(h.to, h.at), zoom: -1 }); return say(); }
    slots(); set({ page: (st.page + d + pages) % pages, zoom: -1 }); // slots(): how many pages there are
  };
  // a control onto dial i in every mode; the same control again takes it off
  const pin = (i, to) => { if (st.pins[i] !== undefined && TO.test(to)) set({ pins: st.pins.map((p, k) => (k === i ? (p === to ? "" : to) : p === to ? "" : p)), zoom: -1 }); };
  const colAt = (x) => (st.zoom >= 0 ? st.zoom : Math.min(N - 1, Math.max(0, Math.floor((x / STRIP) * N))));
  function tap(x) {
    if (st.view === "meter") return set({ view: "params" });
    const el = slots()[colAt(x)];
    if (st.zoom < 0) return void (el && set({ zoom: colAt(x) }));
    if (x < BACK) return set({ zoom: -1 });
    setEl(el, (x - BACK) / (STRIP - BACK));
    refresh();
  }

  // ---- the keys ----------------------------------------------------------------
  // [label, ...commands]: a command is a `data-cmd` name (a RegExp for the
  // one each page words its own way). Every key is bound by name: nothing
  // here knows a selector or a route.
  const go = (v) => [v, `Go to ${v}`];
  const MODE = "Deck dials: macros / focus", METERS = "Deck strip: values / meters";
  const KEYS = [[
    go("Synths"), go("Loops"), go("Kit"), go("Song"),
    ["Play", /^Play \/ stop the /, "Play the song", "Stop everything"],
    ["Undo", "Undo the last pattern edit", "Undo the last song edit", "Undo the last tweak"],
    [() => (st.mode === "macro" ? "Macros" : "Focus"), MODE], ["More"],
  ], [
    go("Library"), go("Lab"), ["Resample", "Resample: record what you hear to a clip"], ["Meters", METERS],
    ["Sets ▶", "Deck dials: next set"], ["Page ▶", "Deck: next four controls"], // ◀: a swipe
    ["Click", "Metronome: on / off"], ["More"],
  ], [ // the beat (steps.js; the step sequencer on Drums, Perform and in the Song drawer)
    ["A", "Beat variation A"], ["B", "Beat variation B"], ["C", "Beat variation C"], ["D", "Beat variation D"],
    ["Fill", "Add a fill"], ["Sparser", "Make it sparser"], ["Busier", "Make it busier"], ["More"],
  ], [ // the mixer (the Mix view; in focus mode the dials are the touched strip's level, pan, send A, send B)
    go("Mix"), ["Balance", "Balance the mix"], ["Mute", "Mute the focused strip"], ["Solo", "Solo the focused strip"],
    ["Return", "Add a return"], ["Play", "Play the song"], ["Undo", "Undo the last song edit"], ["More"],
  ], [ // the Song page's playlist tools (dim on every other view)
    ["◀ Prev", "Previous section"], ["Next ▶", "Next section"], ["Loop", "Loop the selected range"], ["Split", "Split at the marker"],
    ["+4 bars", "Insert 4 bars at the marker"], ["+ Trans", "Add a transition before the selected section"], ["Dup ▶", "Duplicate the selection"], ["More"],
  ], [ // the jam (jam.js: the launcher on the Song page; a scene's key is lit while all of it sounds) —
    // and on Perform its launcher (slots.html declares the same names, lit the same way)
    ["Launcher", "Jam: on / off"], ["Scene 1", "Launch scene 1"], ["Scene 2", "Launch scene 2"], ["Scene 3", "Launch scene 3"],
    ["Scene 4", "Launch scene 4"], ["Stop all", "Stop all"], ["Capture", "Capture the jam to the song"], ["More"],
  ], [ // the keyboard dock's Play (Synths): what each key plays — one press each, lit while it is the choice
    ["Note", "Each key plays: a note"], ["Triad", "Each key plays: a triad"], ["7th", "Each key plays: a 7th chord"], ["Power", "Each key plays: a power chord"],
    ["Hold", "Hold: a note sounds while its key is held, on / off"], ["Latch", "Latch: the next note or chord holds, on / off"], ["Typing", "Typing keys play notes: on / off"], ["More"],
  ]];
  // what the deck knows without asking the control: its own state, and a transport started anywhere
  const ON = { Play: () => !!S.isPlaying?.() || !!S.songClock?.().playing, [MODE]: () => st.mode === "focus", [METERS]: () => st.view === "meter" };
  const lit = (el) => el.getAttribute("aria-pressed") === "true" || el.hasAttribute("aria-current") || el.dataset.state === "on";
  // the first name DECLARED on the page is the key's command (a song with
  // nothing to undo dims Undo; it does not fall through to the patch's)
  function keyOf([label, ...names], all) {
    let c = null;
    for (const n of names) {
      const hit = all.filter((x) => x.value === undefined && (n.test ? n.test(x.label) : x.label === n));
      c = hit.find((x) => !x.off) || hit[0];
      if (c) break;
    }
    const text = typeof label === "function" ? label() : label, on = ON[text] || ON[names[0]], can = !!c && !c.off;
    return { label: text, el: can ? c.el : null, more: !names.length, on: on ? on() : !!c && lit(c.el), off: !!names.length && !can };
  }
  const keys = () => { const all = S.commands?.(true) || []; return KEYS[st.keys].map((k) => keyOf(k, all)); };

  // ---- what a Deck shows -------------------------------------------------------
  let peakBuf = null;
  function level() {
    const an = S.getAnalyser?.();
    if (!an) return 0;
    if (peakBuf?.length !== an.fftSize) peakBuf = new Float32Array(an.fftSize);
    an.getFloatTimeDomainData(peakBuf);
    let pk = 0;
    for (const v of peakBuf) pk = Math.max(pk, Math.abs(v));
    return clamp01(pk);
  }
  // `cheap`: only a value moved, so the keys are the last face's (the command harvest is skipped)
  let lastKeys = null;
  function face(cheap = false) {
    const sl = slots(), macro = st.mode === "macro";
    if (st.zoom >= 0 && !sl[st.zoom]) st.zoom = -1;
    const cols = sl.map((el, i) => {
      if (!el) return { name: macro ? `Macro ${i + 1}` : "—", value: macro ? "on Synths" : "touch a control", frac: 0, off: true };
      const { lo, hi, step, raw } = span(el), pinned = find(st.pins[i]) === el;
      return { name: nameOf(el), value: textOf(el), frac: clamp01((raw - lo) / (hi - lo || 1)), off: el.disabled, lo, hi, step, raw, pinned };
    });
    // a name on two dials (two layers' "gain") takes its place's: "L1 gain", "L2 gain"
    const twice = new Set(cols.map((c) => c.name).filter((n, i, all) => all.indexOf(n) !== i));
    cols.forEach((c, i) => { if (sl[i] && twice.has(c.name)) c.name = `${(sl[i].closest("[data-ctx]")?.dataset.ctx || "").split(" · ")[0].replace(/^Layer (\d+)$/, "L$1")} ${c.name}`.trim(); });
    return {
      mode: st.mode, view: st.view, zoom: st.zoom, page: holding()?.at ?? st.page, pages: holding() ? Math.ceil(held.to.length / N) : pages, cols,
      keys: cheap && lastKeys ? lastKeys : (lastKeys = keys().map((k) => ({ label: k.label, on: k.on, off: k.off }))),
      book: [st.keys, KEYS.length], // which page of keys, of how many (the More key shows it)
      meter: st.view === "meter" ? { level: level(), cpu: document.getElementById("cpu-meter")?.textContent.trim() || "live voice idle" } : null,
    };
  }
  // ONE face a frame, however many things asked: drawn on the next animation
  // frame, after every handler of the event that moved it has run (a read-out
  // included) — a hidden tab gets it from a timer, so the device still hears
  let due = false, full = false;
  const soon = (f) => (document.hidden ? setTimeout(f, 16) : requestAnimationFrame(f));
  function refresh(keysToo = true) {
    full ||= keysToo;
    if (!decks.size || due) return;
    due = true;
    soon(() => { due = false; const f = face(!full); full = false; for (const d of decks) d.show(f); });
  }
  // a value: this frame; a click, a change or a swap: this frame and again a beat later (what it started may land late)
  for (const type of ["input", "change", "click", "htmx:after:settle", "loop-switched"]) {
    document.addEventListener?.(type, () => { refresh(type !== "input"); if (type !== "input") setTimeout(refresh, 120); });
  }
  let wasOn = false; // the meters, a transport started elsewhere, and the frame after it stopped
  setInterval(() => { const on = ON.Play(); if (st.view === "meter" || on || on !== wasOn) refresh(); wasOn = on; }, 100);

  // every transport's events land here; a device's never throw past it
  function handle(ev) {
    try {
      const i = ev?.index;
      if (ev.type === "turn") turn(i, Math.max(-127, Math.min(127, Number(ev.ticks))));
      else if (ev.type === "dialdown" || ev.type === "dialup") press(i, ev.type === "dialdown");
      else if (ev.type === "keydown") {
        const k = keys()[i];
        if (k?.more) set({ keys: (st.keys + 1) % KEYS.length }); else k?.el?.click();
        refresh();
      }
      else if (ev.type === "tap") tap(Number(ev.x));
      else if (ev.type === "long") reset(slots()[colAt(Number(ev.x))]);
      else if (ev.type === "swipe" && Math.abs(ev.x2 - ev.x) > 40) page(ev.x2 < ev.x ? 1 : -1);
    } catch { /* device data never breaks the page */ }
  }

  S.deck = {
    handle, turn, press, face, slots, set, page, pin, hold, put, nudge, setEl, refresh, find, toOf,
    // this `change` is the deck's own: a reset's (sync) or a turn's (later)
    ownChange: (el) => own || turned.delete(el),
    state: () => ({ ...st, pins: [...st.pins] }), lastControl: () => last, nameOf,
    attach(d) { decks.add(d); refresh(); },
    detach(d) { decks.delete(d); },
  };
})();
