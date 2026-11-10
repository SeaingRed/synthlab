// synthlab/studio/static/columns.js
// The keyboard dock's columns layout: the home row A through ' plays the
// naturals, the row above their majors and the row below their minors, in
// the same order (A is C); the number row is free. The third typing layout: letters.js owns the switch (rows |
// letters | columns, Alt+L, html[data-keylayout]); nothing here runs in
// the other two. By `KeyboardEvent.code` — the physical keys, whatever
// the OS layout — each keyboard column is a root, column by column
// (Q sits over A, Z under it, the stagger ignored):
//   - the home row A S D F G H J K L ; ' plays the naturals C D E F G A B
//     C D E F from the dock's C (piano.js's octave: A is C4 at C4), single
//     notes whatever Play says;
//   - the row above, Q … ], the major triad on that column's note, root
//     position in the note's own octave (] is G: the column past ');
//   - the row below, Z … /, the minor triad;
//   - the number row, 1 … 0, what its choice says (`#kbd-colnum`, per
//     browser: synthlab.colnum; Alt+N steps it while columns is on): 7
//     (the default), maj7, m7, sus4, ♯ (the column's note a semitone up:
//     the black keys, E♯ is F) — or oct: 1-7 set the octave, 8 9 0 nothing.
// - / = step the octave (chords.js's octave pair, as in every layout);
// Space is the transport's, on its keydown as in rows; Left Shift is the
// sustain pedal and Right Shift the latch (piano.js: this file never
// touches Shift, and a key struck with Shift held plays, so Shift+/ is
// the pedal and E minor, not the shortcut sheet); Ctrl / Alt / Meta with
// any key are the browser's and the islands'. A key's chord ends on its
// keyup whatever happened meanwhile (a layout switched, the octave moved);
// keys held together sound together; a lost focus ends them all (chords.js's
// blur: they are played through `synthlab.plays.chordOn` — the strum, the
// chord's name beside the keys, its keys lit). The labels are the
// template's (`data-col`, `data-colsharp`: style.css). Window capture,
// loaded after letters.js and before chords.js (base.html), so neither
// chords.js's rows nor piano.js hears a key this layout plays. Typing off,
// a focused field or keyboard-focused button, a surface with keys of its
// own, a page's own key or Perform's typing → pads (piano.js `typing`):
// nothing. The dock is every page's.
// Budget: <= 65 code lines.

(() => {
  "use strict";
  const S = window.synthlab, KEY = "synthlab.colnum";
  // a column's note above the dock's C: the naturals, C up to the G past '
  const STEP = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19];
  const ROWS = {
    home: "KeyA KeyS KeyD KeyF KeyG KeyH KeyJ KeyK KeyL Semicolon Quote",
    top: "KeyQ KeyW KeyE KeyR KeyT KeyY KeyU KeyI KeyO KeyP BracketLeft BracketRight",
    low: "KeyZ KeyX KeyC KeyV KeyB KeyN KeyM Comma Period Slash",
    num: "Digit1 Digit2 Digit3 Digit4 Digit5 Digit6 Digit7 Digit8 Digit9 Digit0",
  };
  const COL = {}; // a key's code -> [its row, its column]
  for (const [r, codes] of Object.entries(ROWS)) codes.split(" ").forEach((c, i) => { COL[c] = [r, i]; });
  // a row, or the number row's choice -> the chord's name (null: a single note) and its intervals
  const PLAY = {
    home: [null, [0]], top: ["", [0, 4, 7]], low: ["m", [0, 3, 7]],
    7: ["7", [0, 4, 7, 10]], maj7: ["maj7", [0, 4, 7, 11]], m7: ["m7", [0, 3, 7, 10]],
    sus4: ["sus4", [0, 5, 7]], sharp: [null, [1]],
  };
  const MODES = ["7", "maj7", "m7", "sus4", "sharp", "octave"];
  let num = MODES.includes(localStorage.getItem(KEY)) ? localStorage.getItem(KEY) : "7";
  const held = new Map(); // a key's code -> its chord's id
  const on = () => S.letters?.get() === "columns";
  const active = (e) => on() && !!document.getElementById("piano") && !!S.typing?.(e);

  function strike(code) {
    const [r, i] = COL[code];
    if (r === "num" && num === "octave") return void (i < 7 && S.piano.setOctave(i + 1));
    const [name, iv] = PLAY[r === "num" ? num : r], id = `col:${code}`;
    const m = 12 * (S.piano.octave() + 1) + STEP[i];
    held.set(code, id);
    S.plays.chordOn(id, iv.map((x) => m + x), 1, name === null ? undefined : S.keys.NOTE_NAMES[m % 12] + name);
  }
  window.addEventListener("keydown", (e) => {
    if (e.code === "KeyN" && e.altKey && !e.ctrlKey && !e.metaKey && !e.repeat && on() && document.getElementById("kbd-colnum") && !S.isTypingTarget?.(e.target)) {
      e.preventDefault(); e.stopImmediatePropagation();
      setNum(MODES[(MODES.indexOf(num) + 1) % MODES.length]);
      return S.toast?.(document.querySelector(`#kbd-colnum [data-colnum="${num}"]`)?.title); // what it plays now
    }
    if (e.ctrlKey || e.altKey || e.metaKey || !(e.code in COL) || !active(e)) return;
    e.stopImmediatePropagation(); // chords.js's rows and piano.js's letters never hear it
    if (!e.repeat && !held.has(e.code)) strike(e.code);
  }, true);
  // a key's keyup ends its chord whatever the layout is by then
  window.addEventListener("keyup", (e) => {
    if (!held.has(e.code)) return;
    e.stopImmediatePropagation();
    S.plays.chordOff(held.get(e.code)); held.delete(e.code);
  }, true);
  window.addEventListener("blur", () => held.clear());

  // ---- the number row's choice ------------------------------------------------
  function sync() {
    document.documentElement.dataset.colnum = num; // style.css: a black key's digit under ♯
    document.querySelectorAll("#kbd-colnum [data-colnum]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.colnum === num)));
    const face = document.getElementById("kbd-colnum-face"), b = document.querySelector(`#kbd-colnum [data-colnum="${num}"]`);
    if (face && b) face.textContent = b.textContent;
  }
  function setNum(v) {
    num = v;
    localStorage.setItem(KEY, num);
    sync();
  }
  document.addEventListener("click", (e) => { const b = e.target.closest?.("#kbd-colnum [data-colnum]"); if (b) setNum(b.dataset.colnum); });
  sync();
  document.addEventListener("DOMContentLoaded", sync);
  document.addEventListener("htmx:after:settle", sync); // a body morph redraws the segment
})();
