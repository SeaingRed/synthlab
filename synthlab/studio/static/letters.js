// synthlab/studio/static/letters.js
// The keyboard dock's letters layout: the letter keys play the notes they
// name (the A key an A), and holding Space, Shift or Ctrl makes it minor,
// major, a 7th…. The typing rows' panel's layout: rows |
// letters | columns (`#kbd-layout`, per browser: synthlab.layout; Alt+L
// from anywhere cycles them in that order, a toast saying which); rows is
// the typing rows alone, columns is columns.js's, and nothing here runs in
// either but the switch. In letters:
//   - A B C D E F G play that note in the dock's octave (C..B: A and B sit
//     above C); the typing rows' other keys play nothing (they are not
//     swallowed: Ctrl / Alt with them reach the browser and the islands);
//   - what is held as the letter goes down makes the chord ON that root —
//     the letter names it, never the key: Left Shift major, Space minor,
//     Ctrl 7, Shift+Ctrl maj7, Space+Ctrl m7, Shift+Space sus4 (all three
//     7sus4); none: what Play says (synthlab.plays: a note, or its chord in
//     key on that note); Tab held: sharp (E♯ is F, B♯ the C above);
//   - 1-7 set the octave (C1..C7, clamped as − / + are); - / = still step;
//   - Space is the transport's still: transport.js asks `takesSpace`, and a
//     tap with no letter in it plays / stops (Shift+Space: stop all) on its
//     keyup. Left Shift is "major", so not the sustain pedal here (piano.js
//     never hears it); Right Shift still latches. Ctrl+A..G never reach the
//     browser (select all, bookmark, copy, find …), nor Tab (focus);
//   - a chord ends on its letter's keyup whatever the modifiers did
//     meanwhile; a lost focus ends them all (chords.js's blur, which holds
//     these chords: they are played through `synthlab.plays.chordOn` — the
//     strum, the chord's name beside the keys, its keys lit).
// window capture, loaded after piano.js and before chords.js (base.html),
// so neither hears a letter. Typing off, a focused field, a keyboard-
// focused button, a surface with keys of its own, a page's own key or
// Perform's typing → pads (piano.js `typing`): nothing. The dock is every
// page's, so the layout plays on every page.
// (The switch's third layout, columns — columns.js — fits inside the
// budget.)
// Budget: <= 91 code lines (90 -> 91: a layout-changed event,
// for the folded dock's summary — keysbar.js).

(() => {
  "use strict";
  const S = window.synthlab, KEY = "synthlab.layout";
  // Alt+L's order, the segment's; each one's toast
  const SAY = {
    rows: "Typing: rows — each row plays what its panel says (Alt+L: letters)",
    letters: "Typing: letters — A–G play their note; ⇧ maj, Space min, Ctrl 7, Tab ♯, 1–7 the octave (Alt+L: columns)",
    columns: "Typing: columns — A S D F … play C D E F …, the row above the major on each, the row below the minor, 1–0 their choice (Alt+N) (Alt+L: rows)",
  };
  const LAYOUTS = Object.keys(SAY);
  let layout = LAYOUTS.includes(localStorage.getItem(KEY)) ? localStorage.getItem(KEY) : "rows";
  const PC = { KeyC: 0, KeyD: 2, KeyE: 4, KeyF: 5, KeyG: 7, KeyA: 9, KeyB: 11 };
  // [Left Shift, Space, Ctrl] held -> the chord's name and its intervals
  const CHORDS = {
    "100": ["", [0, 4, 7]], "010": ["m", [0, 3, 7]], "001": ["7", [0, 4, 7, 10]],
    "101": ["maj7", [0, 4, 7, 11]], "011": ["m7", [0, 3, 7, 10]],
    "110": ["sus4", [0, 5, 7]], "111": ["7sus4", [0, 5, 7, 10]],
  };
  // the rest of the typing rows: silent here ("?" — Shift+/ — is the shortcut sheet's)
  const ROWKEY = /^(Key[H-Z]|Digit[089]|BracketLeft|BracketRight|Semicolon|Quote|Comma|Period|Slash)$/;
  const mods = { shift: false, tab: false, space: null }; // space: { e: its keydown, used } while held
  const held = new Map(); // a letter's code -> its chord's id
  const active = (e) => layout === "letters" && !!document.getElementById("piano") && !!S.typing?.(e);
  // a focused thing whose own Space it is (a key, a slider, a grid …) keeps it
  const takesSpace = (e) => active(e) && !e.target.closest?.(S.transport?.OWN_SPACE || "input");
  S.letters = { get: () => layout, takesSpace };

  function strike(code, ctrl) {
    const m = 12 * (S.piano.octave() + 1) + PC[code] + (mods.tab ? 1 : 0), id = `letter:${code}`;
    if (mods.space) mods.space.used = true;
    const c = CHORDS[`${+mods.shift}${+!!mods.space}${+ctrl}`];
    held.set(code, id);
    if (c) return S.plays.chordOn(id, c[1].map((i) => m + i), 1, S.keys.NOTE_NAMES[m % 12] + c[0]);
    S.plays.chordOn(id, S.plays.get() === "note" ? [m] : S.plays.chordOf(m).notes, 1); // what Play says
  }
  window.addEventListener("keydown", (e) => {
    if (e.code === "KeyL" && e.altKey && !e.ctrlKey && !e.metaKey && !e.repeat && document.getElementById("kbd-layout") && !S.isTypingTarget?.(e.target)) {
      e.preventDefault(); e.stopImmediatePropagation();
      setLayout(LAYOUTS[(LAYOUTS.indexOf(layout) + 1) % LAYOUTS.length]);
      return S.toast?.(SAY[layout]);
    }
    if (e.altKey || e.metaKey || !active(e)) return;
    const c = e.code;
    if (c === "Space") {
      if (!takesSpace(e)) return;
      e.preventDefault(); e.stopImmediatePropagation(); // not the page's scroll, nor a button's press
      if (!e.repeat) mods.space = { e, used: false };
    } else if (c === "Tab") {
      e.preventDefault(); e.stopImmediatePropagation();
      mods.tab = true;
    } else if (c === "ShiftLeft") {
      e.stopImmediatePropagation(); // major: piano.js's pedal never goes down
      mods.shift = true;
    } else if (c in PC) {
      e.stopImmediatePropagation();
      if (e.ctrlKey) e.preventDefault(); // select all, bookmark, copy, find …: never the browser's here
      if (!e.repeat && !held.has(c)) strike(c, e.ctrlKey);
    } else if (e.ctrlKey) {
      return;
    } else if (/^Digit[1-7]$/.test(c)) {
      e.stopImmediatePropagation();
      S.piano.setOctave(Number(c.slice(5)));
    } else if (ROWKEY.test(c) && !(c === "Slash" && e.shiftKey)) {
      e.stopImmediatePropagation();
    }
  }, true);
  // a letter's keyup ends its chord whatever the modifiers do; a modifier's
  // keyup counts wherever the focus went, so none is left down
  window.addEventListener("keyup", (e) => {
    const c = e.code;
    if (held.has(c)) {
      e.stopImmediatePropagation();
      S.plays.chordOff(held.get(c)); held.delete(c);
    } else if (c === "ShiftLeft") mods.shift = false;
    else if (c === "Tab") mods.tab = false;
    else if (c === "Space" && mods.space) {
      const sp = mods.space;
      mods.space = null;
      e.preventDefault(); e.stopImmediatePropagation();
      if (!sp.used && !sp.e.ctrlKey) S.transport?.space?.(sp.e); // a tap: the transport's Space, on its keyup
    }
  }, true);
  window.addEventListener("blur", () => { held.clear(); Object.assign(mods, { shift: false, tab: false, space: null }); });

  // ---- the switch -------------------------------------------------------------
  function sync() {
    document.documentElement.dataset.keylayout = layout; // style.css: the faces, the keys' letters
    document.querySelectorAll("#kbd-layout [data-layout]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.layout === layout)));
  }
  function setLayout(v) {
    layout = v;
    localStorage.setItem(KEY, layout);
    sync();
    document.dispatchEvent(new CustomEvent("layout-changed")); // keysbar.js: the folded dock's summary
  }
  document.addEventListener("click", (e) => { const b = e.target.closest?.("#kbd-layout [data-layout]"); if (b) setLayout(b.dataset.layout); });
  sync();
  document.addEventListener("DOMContentLoaded", sync);
  document.addEventListener("htmx:after:settle", sync); // a body morph redraws the segment
})();
