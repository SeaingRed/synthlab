// synthlab/studio/static/rowsounds.js
// Typing rows by sound (Synths): with "rows → sounds" on, each typing row
// is one layer's own keyboard — the number row layer 1, Q 2, A 3, Z 4 by
// default, changed in the typing rows' panel (its layer column) — so four sounds sit under one hand
// and a row is switched mid-performance. Nothing here plays a note:
// - `rowsBy()` is read by piano.js (/patch.json?rows=1: the key stack is
//   then every layer with a sound, fxlive.key_lanes) and chords.js (every
//   row is its own run from C; none is piano.js's split layout);
// - `rowLane(id)` is read by livefx.js for every note: a row's key
//   (chords.js ids, "row:<code>:<n>") sounds on its row's layer alone, any
//   other note (the on-screen keys, MIDI, the XY drone) on the layer being
//   edited.
// The live engine only: a stack the worklet cannot play (/patch.json's
// worklet_ok false) falls back to the whole-stack WAV key, which no row
// can split — the toggle says so when that is the case.
// A preference of this browser (localStorage), like typing / hold.
// Budget: <= 45 code lines.

(() => {
  "use strict";
  const S = window.synthlab;
  const ROWS = { num: /^Digit\d$/, top: /^(Key[QWERTYUIOP]|Bracket(Left|Right))$/, home: /^(Key[ASDFGHJKL]|Semicolon|Quote)$/, low: /^(Key[ZXCVBNM]|Comma|Period|Slash)$/ };
  let on = localStorage.getItem("synthlab.rowsBy") === "1";
  let lanes = { num: 1, top: 2, home: 3, low: 4 };
  try { lanes = { ...lanes, ...JSON.parse(localStorage.getItem("synthlab.rowLanes") || "{}") }; } catch {}
  const toggle = () => document.getElementById("rows-toggle");
  S.rowsBy = () => on && !!toggle(); // only where the toggle is (Synths)
  S.rowLane = (id) => {
    if (!S.rowsBy()) return null;
    const code = /^row:([A-Za-z0-9]+)/.exec(String(id))?.[1];
    const row = code && Object.keys(ROWS).find((r) => ROWS[r].test(code));
    return row ? lanes[row] : S.piano?.patch()?.slot ?? null;
  };
  function sync() {
    toggle()?.setAttribute("aria-pressed", String(on));
    document.documentElement.toggleAttribute("data-rows-by", S.rowsBy());
    document.querySelectorAll('#kbd-rows select[name^="lane-"]').forEach((s) => { s.value = String(lanes[s.name.slice(5)]); s.disabled = !on; }); // a layer means something only while on
  }
  document.addEventListener("click", async (e) => {
    if (!e.target.closest("#rows-toggle")) return;
    on = !on; localStorage.setItem("synthlab.rowsBy", on ? "1" : "0"); sync();
    document.dispatchEvent(new Event("rows-by")); // chords.js relabels its rows
    await S.piano?.reload?.(); // the key stack changes with it
    const pop = document.getElementById("kbd-rows"); // (switched from the palette: show the layer column it opened)
    if (on && pop && !pop.matches(":popover-open")) pop.showPopover();
    if (on && S.piano?.patch() && !S.piano.patch().worklet_ok) S.toast?.("Rows → sounds needs the live engine: this stack plays as rendered notes, so every key plays all of it.");
  });
  document.addEventListener("change", (e) => {
    const s = e.target.closest?.('#kbd-rows select[name^="lane-"]');
    if (!s) return;
    lanes[s.name.slice(5)] = Number(s.value);
    localStorage.setItem("synthlab.rowLanes", JSON.stringify(lanes));
  });
  document.addEventListener("DOMContentLoaded", sync);
  document.addEventListener("htmx:after:settle", sync);
})();
