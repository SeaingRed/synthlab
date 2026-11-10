// synthlab/studio/static/keysbar.js
// The keyboard dock's toolbar, as a face: notes vs chords plain to see,
// and changed in few clicks. Nothing
// here plays a note or keeps a setting: chords.js owns "each key plays"
// (`synthlab.plays`, its Play segments) and the typing rows, rowsounds.js
// the rows' layers, piano.js the keys. This file:
//   - ` toggles a note / the last chord the keys played (localStorage
//     synthlab.playsChord), Alt+` steps the chord (triad, 7th, 9th, power,
//     octave) — never from a field; a toast says so while the dock is
//     folded (the segments unseen);
//   - shows what the keys play: in chord mode each key a chord in key is
//     built on carries its degree (`data-deg`, "vi": style.css draws it);
//     the pointer on a key lights the keys of its chord faintly (`.tone`;
//     a note: that key alone) and the read-out beside the keys (#kbd-now)
//     names it — "Am7", "vi" — bright while a chord sounds (chords.js's
//     chord-on / chord-off: a key, a typing row, MIDI, a pad);
//   - says what each typing row plays at a glance: the QWERTY group's strip
//     (#kr-strip, "1 chords · Q piano · A piano · Z —"; rows side by side
//     that play the same share a chip, "1 Q A Z chords", and a long name is
//     its short form, "harm", so the toolbar stays one row at 1920 px) and,
//     with rows → sounds on, each row's layer (the keys and MIDI play the
//     edited one);
//     the rows' panel reads Play's choice back on its Keys & MIDI row;
//   - a chord of the letters layout (letters.js) by the name it carries;
//   - a single note by its name and octave, "D4" (piano.js's key-note: a
//     typed key, a key, MIDI, a column's home row — a note above the keys
//     drawn named too), while it sounds — not just "note";
//   - the dock as the page frame's (base.html, every page): its
//     height is --keys-h on <html>, which a window-high view leaves it, and
//     on Synths it stands where its grid area was (#keys-slot: the strip's
//     column to the cockpit's edge, --keys-l / --keys-r); folded, #kbd-sum
//     says what the keys play (the sounding chord or note, else Play's
//     choice · the layout · the octave).
// Budget: <= 116 code lines (90 -> 110: a note's name, the
// folded dock's summary, the frame's measure; -> 116: a
// response's settles answered once a frame, the strip and the measure
// written only when they changed).

(() => {
  "use strict";
  const S = window.synthlab, $ = (id) => document.getElementById(id);
  const P = () => S.plays, LAST = "synthlab.playsChord";
  const pc = (m) => ((m % 12) + 12) % 12;
  const noteName = (m) => `${S.keys.NOTE_NAMES[pc(m)]}${Math.floor(m / 12) - 1}`;
  const chordMode = () => !!P() && P().get() !== "note";

  // ---- the shortcuts: ` note / chord, Alt+` the next chord ---------------------
  document.addEventListener("keydown", (e) => {
    if (e.code !== "Backquote" || e.ctrlKey || e.metaKey || e.repeat || !P() || !$("kbd-plays") || S.keysBlocked?.(e.target)) return;
    e.preventDefault();
    const now = P().get(), chords = P().list().filter((v) => v !== "note");
    const last = chords.includes(localStorage.getItem(LAST)) ? localStorage.getItem(LAST) : chords[0];
    P().set(now === "note" ? last : e.altKey ? chords[(chords.indexOf(now) + 1) % chords.length] : "note");
    const on = document.querySelector('#kbd-plays [aria-pressed="true"]');
    if (on && !on.checkVisibility()) S.toast?.(`${on.dataset.cmd} (\` note / chord, Alt+\` the next chord)`);
  });

  // ---- the keys show what they play ------------------------------------------
  // each key a chord in key is built on: its degree (pitch classes: no octave moves them)
  function degrees() {
    const by = new Map((chordMode() ? P().chords() : []).map((c) => [pc(c.notes[0]), c]));
    for (const k of document.querySelectorAll(".pkey")) {
      const c = by.get(Number(k.dataset.note));
      if (c) k.dataset.deg = c.roman;
      else delete k.dataset.deg;
    }
  }
  // the key under the pointer: the keys of what it plays, faintly lit
  let hov = null, hovC = null;
  const held = new Map(); // a sounding chord's id -> { name, roman }
  function light(el) {
    document.querySelectorAll(".pkey.tone").forEach((k) => k.classList.remove("tone"));
    if (!el || !S.piano) return null;
    const m = S.piano.midiOf(el), c = chordMode() ? P().chordOf(m) : { notes: [m], name: noteName(m) };
    const keys = new Map([...document.querySelectorAll(".pkey")].map((k) => [S.piano.midiOf(k), k]));
    c.notes.forEach((n) => keys.get(n)?.classList.add("tone"));
    return c;
  }
  const notes = new Map(); // a sounding voice's token -> its midi (piano.js key-note)
  function show() {
    const out = $("kbd-now");
    if (!out) return;
    const n = [...notes.values()].pop(), h = [...held.values()].pop() || (n !== undefined ? { name: noteName(n) } : null);
    const c = h || hovC, on = document.querySelector('#kbd-plays [aria-pressed="true"]'), rest = on?.textContent.trim() || "note";
    out.dataset.state = h ? "held" : hovC ? "hover" : "rest";
    out.value = c?.name || rest;
    if (on && $("kr-keys")) $("kr-keys").textContent = `${on.dataset.cmd.replace(/^.*: /, "")} (Play)`; // the rows' panel: the keys & MIDI row
    $("kbd-now-deg").textContent = c?.roman || "";
    const sum = $("kbd-sum"); // the folded dock: what sounds, else Play · the layout · the octave
    if (sum) sum.value = `${h?.name || rest} · ${document.documentElement.dataset.keylayout || "rows"} · C${S.piano?.octave() ?? 4}`;
  }
  document.addEventListener("key-note", (e) => { const { tok, midi } = e.detail; if (midi === undefined ? notes.delete(tok) : notes.set(tok, midi)) show(); });
  for (const ev of ["octave-changed", "layout-changed"]) document.addEventListener(ev, show);
  document.addEventListener("pointerover", (e) => {
    const k = e.target.closest?.(".pkey") || null;
    if (k === hov) return;
    hov = k; hovC = light(k); show();
  });
  // a chord sounding: its name in key, else its notes (a pad of the chord chart)
  function named(notes) {
    const key = (ns) => [...new Set(ns.map(pc))].sort().join(), c = notes.map((n) => P()?.chordOf(n)).find((x) => x?.name && key(x.notes) === key(notes)); // (any note its root: an inversion)
    return c || { name: notes.map((n) => S.keys.NOTE_NAMES[pc(n)]).join(" ") };
  }
  // (a letters-layout chord names itself, "Dm7": its degree too when the key has it)
  function heldName({ notes, name }) { const c = named(notes); return !name || c.name === name ? c : { name }; }
  // (the keys arp's chord, arp.js: what it plays and its mode, "Am ↑" — a single note too)
  function arpName(d) { const c = d.notes.length > 1 ? heldName(d) : { name: noteName(d.notes[0]) }; return { ...c, name: `${c.name} ${d.arp}` }; }
  document.addEventListener("chord-on", (e) => { if (e.detail.notes.length > 1 || e.detail.arp) { held.delete(e.detail.id); held.set(e.detail.id, e.detail.arp ? arpName(e.detail) : heldName(e.detail)); show(); } });
  document.addEventListener("chord-off", (e) => { if (held.delete(e.detail.id)) show(); });
  document.addEventListener("plays-changed", (e) => {
    if (e.detail.plays !== "note") localStorage.setItem(LAST, e.detail.plays);
    degrees(); hovC = light(hov); show();
  });

  // ---- the typing rows at a glance ---------------------------------------------
  const ROWS = { num: "1", top: "Q", home: "A", low: "Z" };
  const SHORT = { harmonics: "harm", extended: "ext", altered: "alt", seventh: "7th", major: "maj", minor: "min" }; // (the panel says them whole)
  function strip() {
    const box = $("kr-strip");
    if (!box) return;
    const by = S.rowsBy?.(), row = (r, kind) => document.querySelector(`#kbd-rows select[name="${kind}-${r}"]`)?.value;
    // rows side by side that play the same (and, rows → sounds, the same
    // layer) share one chip — "1 Q A Z chords", not "chords" four times: the
    // toolbar stays one row at 1920 px
    const chips = [];
    for (const [r, cap] of Object.entries(ROWS)) {
      const what = row(r, "row") || "off", lane = by && what !== "off" ? row(r, "lane") : "", last = chips.at(-1);
      if (last?.what === what && last.lane === lane) { last.caps.push(cap); continue; }
      chips.push({ what, lane, caps: [cap] });
    }
    const sig = JSON.stringify(chips);
    if (box.dataset.sig === sig) return; // the same rows: nothing to redraw
    box.dataset.sig = sig;
    box.replaceChildren(...chips.map(({ what, lane, caps }) => {
      const chip = document.createElement("span");
      for (const cap of caps) chip.appendChild(document.createElement("kbd")).textContent = cap;
      chip.append(what === "off" ? "—" : SHORT[what] || what);
      if (lane) Object.assign(chip.appendChild(document.createElement("small")), { className: "kr-layer", textContent: `L${lane}` });
      return chip;
    }));
  }
  document.addEventListener("change", (e) => { if (e.target.closest?.("#kbd-rows")) strip(); });
  document.addEventListener("rows-by", strip);
  const all = () => { degrees(); strip(); show(); frame(); };
  // every settle of a response (one per OOB region) once, in the next frame:
  // the measure in frame() after strip()'s redraw forced a layout each time
  let queued = 0;
  const soon = () => { queued ||= requestAnimationFrame(() => { queued = 0; all(); }); };
  document.addEventListener("DOMContentLoaded", all);
  document.addEventListener("htmx:after:settle", soon); // a view swap, a key change (the chord bar's region)

  // ---- the page frame's dock: its height, and on Synths its place -------------
  // (measured, never guessed: the dock wraps, folds, grows a Loops strip; the
  // slot follows the sounds rack's splitter and the pinned clip drawer. A
  // resize is answered on the next frame, so a measure never loops)
  const root = document.documentElement, sized = new ResizeObserver(() => requestAnimationFrame(frame));
  let watched = [];
  const put = (el, k, v) => { if (el.style.getPropertyValue(k) !== v) el.style.setProperty(k, v); }; // only what moved
  function frame() {
    const dock = $("keys-dock"), slot = $("keys-slot");
    if (!dock) return;
    put(root, "--keys-h", `${dock.offsetHeight}px`);
    const b = slot?.offsetParent ? slot.getBoundingClientRect() : null, cs = getComputedStyle(document.body);
    const body = document.body.getBoundingClientRect(), l = body.left + parseFloat(cs.paddingLeft), r = body.right - parseFloat(cs.paddingRight);
    put(dock, "--keys-l", b ? `${Math.max(0, Math.round(b.left - l))}px` : "0px");
    put(dock, "--keys-r", b ? `${Math.max(0, Math.round(r - b.right))}px` : "0px");
    const now = [dock, slot, document.body].filter(Boolean);
    if (now.some((el, i) => el !== watched[i]) || now.length !== watched.length) { sized.disconnect(); now.forEach((el) => sized.observe(el)); watched = now; }
  }
})();
