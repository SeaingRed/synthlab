// synthlab/studio/static/chords.js
// Keyboard chord mode + chord pads + the code line (Synths view). Owns:
// "each key plays" (a note, or a voicing: chord mode), intercepting
// key/pointer/MIDI note-ons in chord mode and fanning them out to
// window.synthlab.piano (same voices as
// single notes), the I–VII chord pads (keys 1–7), and the typed sequence
// player. Chord tables come from the server (#chord-bar[data-chords]).
// Also: note/harmonic pads, the chord library, and the per-row key map
// (number / Q / A / Z rows -> piano | notes | chords | major | minor |
// diminished | harmonics | off), and the tiling that makes rows sharing a
// surface read as one instrument rather than repeating each other.
// 270 -> 292: row-offset ranking + collapsible pad blocks.
// 292 -> 320: rows tile through the pad list (startOf/padAt/cycleOf) with a
// wrap past the last pad, so no key is dead and no pad is unreachable;
// plus the - / = octave pair, which no play surface can claim (Z/X could
// not do that job — Z is the low row's first key).
// 320 -> 330: a row set to a library category loads the chord poster
// (ensureLib) — fetched only on the fold's first open, so every key of
// such a row played nothing until then.
// 330 -> 340: one "each key plays" choice (the older Note|Chord +
// voicing setting migrated into it), and one typed letter per on-screen
// key (the lowest row's; the others in data-typed and its title).
// 340 -> 345: strum is every chord's — the chord pads strum
// whatever the keys play — and kept per browser (synthlab.strum), so a
// view swap or a deck dial's turn is not lost.
// 345 -> 355: Play tools as a popover kept open across pages, and the
// chords-in-key block open by default.
// 355 -> 360 (the dock's Play group): "each key plays" is one
// row of segments, not two selects; `synthlab.plays` (chordOf: a chord's
// name and degree) and the chord-on / chord-off events for keysbar.js.
// 360 -> 370: "all rows" in the rows' panel (every typing row
// at once), a chord row's letter on its root key, a chord's first note
// sounding in the press itself (no 0 ms timer).
// (chordOn / chordOff on synthlab.plays for letters.js, a
// chord's own name on chord-on — one line, inside the budget.)
// 370 -> 390: "chords in key" follow the loop's chords (its
// chord track, Perform's chord lane: the chord under the playhead, its pad
// lit — `under`, `paintFollow`), and the bridge passes the keys arp's `at`.
// Budget: <= 390 code lines.

(() => {
  "use strict";
  // KEYMAP/NOTE_NAMES/ACT_KEY are piano.js's (it loads first — base.html);
  // reading them here instead of a second copy is what keeps this island's
  // key tables from drifting from the QWERTY piano's own.
  const { KEYMAP, NOTE_NAMES, ACT_KEY } = window.synthlab.keys;
  // What each key plays: "note", or a voicing — the chord in key on the key
  // played. One choice (localStorage synthlab.plays), ONE control: the
  // toolbar's Play segments (#kbd-plays [data-plays]), every choice a click.
  // It was a Note|Chord switch beside a voicing select that nothing
  // remembered: "chord" comes back as a triad, so nobody's setting is lost.
  let plays = localStorage.getItem("synthlab.plays") || (localStorage.getItem("kbd-mode") === "chord" ? "triad" : "note");
  const chordy = () => plays !== "note";
  const piano = () => window.synthlab.piano;
  const bar = () => document.getElementById("chord-bar");
  const held = new Map(); // id -> [voice ids]

  function voicing() { return chordy() ? plays : "triad"; }
  function table() {
    try { return JSON.parse(bar()?.dataset.chords || "{}"); } catch { return {}; }
  }
  // chord built on a midi note: the in-scale chord whose root is the
  // nearest scale tone (mirrors chords.chord_for_midi, client-side) — its
  // notes, and its name and degree (keysbar.js shows them: "Am7", "vi")
  function chordOf(m) {
    const f = under(); // following the loop's chords: the one under the playhead, its root nearest m
    if (f) { const k = Math.round((m - f[5][0]) / 12); return { notes: f[5].map((n) => n + 12 * k), name: f[3], roman: f[4] }; }
    const set = table()[voicing()] || [];
    if (!set.length) return { notes: [m] };
    const mod = (a) => ((a % 12) + 12) % 12; // JS % is negative for negatives
    const pcs = set.map((c) => mod(c.notes[0]));
    let best = 0, bestD = 99;
    pcs.forEach((pc, i) => {
      const d = Math.min(mod(m - pc), mod(pc - m));
      if (d < bestD) { bestD = d; best = i; }
    });
    const down = mod(m - pcs[best]), up = mod(pcs[best] - m);
    const base = up < down ? m + up : m - down, c = set[best];
    return { notes: c.notes.map((n) => base + (n - c.notes[0])), name: c.name, roman: c.roman };
  }
  const chordAt = (m) => chordOf(m).notes;
  // ---- "chords in key" follow the loop's chords (off by default,
  // synthlab.chordsFollow): with the loop playing over a chord track of its
  // own (Perform's chord lane, #chord-bar's data-track: [lo, hi, degree,
  // name, roman, notes] over the note grid's steps), a key in chord mode
  // plays the chord under the playhead and that degree's pad is lit (.now)
  let follow = localStorage.getItem("synthlab.chordsFollow") === "1", lit = null;
  function under() {
    const S = window.synthlab, t0 = S.loopStart?.();
    let t = null;
    try { t = follow && S.isPlaying?.() && t0 != null && JSON.parse(bar()?.dataset.track || "null"); } catch { /* none */ }
    if (!t) return null;
    const step = Math.floor((S.heardTime() - t0) / ((S.barBeats?.() || 4) * 60 / (S.tempo() || 120) / t.spb)) % t.total;
    return t.tiles.find(([lo, hi]) => lo <= step && step < hi) || null;
  }
  function paintFollow() {
    document.getElementById("chords-follow")?.setAttribute("aria-pressed", String(follow));
    const f = under(), pad = f && bar()?.querySelector(`.chord-pads .chord-pad[data-degree="${f[2]}"]`);
    if (pad !== lit) { lit?.classList.remove("now"); pad?.classList.add("now"); lit = pad || null; }
  }
  setInterval(() => { if (follow || lit) paintFollow(); }, 100);
  document.addEventListener("click", (e) => {
    if (!e.target.closest?.("#chords-follow")) return;
    follow = !follow; localStorage.setItem("synthlab.chordsFollow", follow ? "1" : "0"); paintFollow();
  });
  // strum (#kbd-strum): ms between a chord's notes — the keys' chords and the pads', whatever a key plays
  function strumMs() { return Number(document.getElementById("kbd-strum")?.value || 0); }
  document.addEventListener("input", (e) => { if (e.target.id === "kbd-strum") localStorage.setItem("synthlab.strum", e.target.value); });
  // (name: what keysbar.js calls it — a letters-layout chord names itself, "Dm7")
  function chordOn(id, notes, vel, name) {
    chordOff(id);
    const timers = [];
    const ms = strumMs();
    const ids = notes.map((n, i) => {
      const vid = `${id}:${i}`;
      // a note due now sounds now: a 0 ms timer waited out the press's frame
      // (~20 ms), late on every chord and squeezing a strum's first gap
      if (i * ms === 0) piano().noteOn(vid, n, vel);
      else timers.push(setTimeout(() => piano().noteOn(vid, n, vel), i * ms));
      return vid;
    });
    held.set(id, { ids, timers });
    document.dispatchEvent(new CustomEvent("chord-on", { detail: { id, notes, name } })); // keysbar.js names it
  }
  function chordOff(id) {
    const h = held.get(id);
    if (!h) return;
    document.dispatchEvent(new CustomEvent("chord-off", { detail: { id } }));
    // a strummed note that hasn't sounded yet must never start after the
    // release — that's how keys used to stay lit (and voices stay on)
    h.timers.forEach(clearTimeout);
    for (const vid of h.ids) piano().noteOff(vid);
    held.delete(id);
  }

  // ---- what a key plays ----------------------------------------------------
  const choices = () => [...document.querySelectorAll("#kbd-plays [data-plays]")];
  function syncMode() {
    const seg = choices();
    if (seg.length && !seg.some((b) => b.dataset.plays === plays)) plays = "note"; // a voicing the server no longer has
    seg.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.plays === plays)));
    const strum = document.getElementById("kbd-strum"), ms = localStorage.getItem("synthlab.strum");
    if (strum && ms !== null && strum !== document.activeElement) { strum.value = ms; strum.style.setProperty("--fill", `${(strum.value / strum.max) * 100}%`); }
  }
  function setPlays(v) {
    plays = v; localStorage.setItem("synthlab.plays", plays); localStorage.removeItem("kbd-mode"); syncMode();
    document.dispatchEvent(new CustomEvent("plays-changed", { detail: { plays } }));
  }
  document.addEventListener("click", (e) => { const b = e.target.closest?.("#kbd-plays [data-plays]"); if (b) setPlays(b.dataset.plays); });
  // keysbar.js reads and sets it (the shortcuts, the read-out, the keys' degrees)
  window.synthlab.plays = { get: () => plays, set: setPlays, list: () => choices().map((b) => b.dataset.plays), chordOf, chords: () => table()[voicing()] || [] };
  Object.assign(window.synthlab.plays, { chordOn, chordOff }); // letters.js plays its chords through them (strum, the name, the keys lit)
  document.addEventListener("htmx:after:settle", syncMode); // settle: fires on the (connected) target
  document.addEventListener("DOMContentLoaded", syncMode);

  // ---- key rows: four physical rows, each driving one surface ------------
  // Rows are physical key codes (layout/shift-proof); labels are the US
  // caps. "piano" on both Q and A rows = the classic split layout piano.js
  // owns. Everything else is claimed here (window capture, registered
  // before the chord-mode listener below) so piano.js never double-fires —
  // including Z/X, which only shift octave while the Z row is "off".
  const ROWS = {
    // Minus/Equal are NOT here: they are the octave pair (see OCTAVE_KEYS).
    // Z/X do the same job in piano.js, but Z is the low row's first key, so
    // assigning that row used to steal octave-down outright.
    num: { codes: "Digit1 Digit2 Digit3 Digit4 Digit5 Digit6 Digit7 Digit8 Digit9 Digit0".split(" "), caps: "1234567890" },
    top: { codes: "KeyQ KeyW KeyE KeyR KeyT KeyY KeyU KeyI KeyO KeyP BracketLeft BracketRight".split(" "), caps: "QWERTYUIOP[]" },
    home: { codes: "KeyA KeyS KeyD KeyF KeyG KeyH KeyJ KeyK KeyL Semicolon Quote".split(" "), caps: "ASDFGHJKL;'" },
    low: { codes: "KeyZ KeyX KeyC KeyV KeyB KeyN KeyM Comma Period Slash".split(" "), caps: "ZXCVBNM,./" },
  };
  // library categories map to whichever quality row is currently shown
  const SURFACES = { notes: ".note-pad:not(.harm)", chords: ".chord-pad:not(.lib)", harmonics: ".note-pad.harm" };
  for (const c of ["major", "minor", "seventh", "extended", "sus", "dim", "aug", "add", "altered"])
    SURFACES[c] = `.lib-row:not([hidden]) .chord-pad[data-cat="${c}"]`;
  // quality pills inside each library category (sticky)
  let libq = {};
  try { libq = JSON.parse(localStorage.getItem("synthlab.libq") || "{}"); } catch {}
  function applyLibQ() {
    document.querySelectorAll("#chord-bar .chord-lib-row").forEach((row) => {
      const q = String(libq[row.dataset.cat] ?? 0);
      row.querySelectorAll(".lib-row").forEach((r) => r.toggleAttribute("hidden", r.dataset.q !== q));
      row.querySelectorAll(".lib-q-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.q === q)));
    });
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("#chord-bar .lib-q-btn");
    if (!b) return;
    libq[b.closest(".chord-lib-row").dataset.cat] = Number(b.dataset.q);
    localStorage.setItem("synthlab.libq", JSON.stringify(libq));
    applyLibQ(); applyRows();
  });
  let rows = { num: "chords", top: "piano", home: "piano", low: "off" };
  try { rows = { ...rows, ...JSON.parse(localStorage.getItem("synthlab.rows") || "{}") }; } catch {}
  // (rows by sound, rowsounds.js: every row is a layer's own keyboard — none is piano.js's, none tiles onto another)
  const classic = () => rows.top === "piano" && rows.home === "piano" && !window.synthlab.rowsBy?.();
  const rowOf = (code) => Object.keys(ROWS).find((r) => ROWS[r].codes.includes(code));
  // Rows set to the same surface are ONE instrument: ranked bottom-to-top
  // (Z, A, Q, 1 — physically low to high) and tiled, so each row carries on
  // from where the row below it stopped rather than replaying the same pads
  // an octave up. `startOf` is that running key count.
  //
  // Running past the last pad wraps to the first, one octave higher (see
  // padAt), so a short surface never leaves keys dead: the 7 chord degrees
  // become I..VII then I..V again, and all 15 note pads stay reachable.
  const ROW_ORDER = ["low", "home", "top", "num"];
  const peers = (r) => window.synthlab.rowsBy?.() ? [r] : ROW_ORDER.filter((x) => rows[x] === rows[r] && !ownedByPiano(x));
  const rank = (r) => peers(r).indexOf(r);
  const startOf = (r) => peers(r).slice(0, Math.max(0, rank(r))).reduce((n, x) => n + ROWS[x].codes.length, 0);
  // How far one wrap lifts, and how many pads make a cycle. Note pads carry
  // data-midi and run A3..A5 — the last pad IS the first two octaves up, so
  // lifting a flat 12 dropped the row an octave mid-way (…80, 81, then 69).
  // Lift by the list's own span and drop that duplicate end pad. Chord
  // degrees and harmonics have no such period: one octave, all pads.
  const cycleOf = (pads) => {
    const m = pads.map((p) => Number(p.dataset.midi));
    const span = m.every(Number.isFinite) ? m[m.length - 1] - m[0] : 0;
    return span > 0 && span % 12 === 0 ? { len: pads.length - 1, lift: span } : { len: pads.length, lift: 12 };
  };
  // absolute index -> which pad, and how far above its printed pitch it sits
  const padAt = (pads, abs) => {
    if (!pads.length) return null;
    const { len, lift } = cycleOf(pads);
    return { pad: pads[((abs % len) + len) % len], lift: Math.floor(abs / len) * lift };
  };
  // classic rows are piano.js's; a lone "piano" row is a chromatic run from C
  const ownedByPiano = (r) => rows[r] === "piano" && (r === "top" || r === "home") && classic();
  function padsFor(rm) {
    const sel = SURFACES[rm];
    return sel && bar() ? [...bar().querySelectorAll(sel)] : [];
  }
  // A row set to a library category plays the chord poster's pads, which
  // are fetched on the fold's first open (chord_bar.html): load it now, or
  // every key of that row played nothing until the fold had been opened once
  function ensureLib() {
    const lib = document.getElementById("chord-lib");
    if (!lib || lib.querySelector(".chord-pad") || lib.dataset.asked) return;
    if (!Object.values(rows).some((rm) => SURFACES[rm]?.startsWith(".lib-row"))) return;
    lib.dataset.asked = "1";
    // fetched as its own first open would (a dispatched "toggle" is not one) — through
    // htmx (not a bare fetch into innerHTML): the page's headers, the error toast
    const again = () => { if (!lib.querySelector(".chord-pad")) delete lib.dataset.asked; applyAll(); };
    htmx.ajax("GET", lib.getAttribute("hx-get"), { target: lib.querySelector(".lib-body"), swap: "innerHTML", source: document.getElementById("post-from") }).then(again, again);
  }
  function applyRows() {
    ensureLib();
    document.querySelectorAll('#kbd-rows select[name^="row-"]').forEach((s) => {
      const r = s.name.slice(4);
      s.value = rows[r];
      const from = rows[r] === "off" ? 0 : startOf(r);
      s.title = from > 0 ? `plays ${rows[r]}, carrying on from pad ${from + 1}` : "";
      s.closest(".kr-row")?.classList.toggle("row-lifted", from > 0);
    });
    document.querySelectorAll("[data-rows-all]").forEach((b) => b.setAttribute("aria-pressed", String(ROW_ORDER.every((r) => rows[r] === b.dataset.rowsAll))));
    bar()?.querySelectorAll(".khint").forEach((h) => { h.textContent = ""; });
    const pkeys = [...document.querySelectorAll(".pkey")];
    pkeys.forEach((k) => { const h = k.querySelector(".qhint"); if (h) h.dataset.classic ??= h.textContent; });
    // bottom-to-top, so the lowest row to reach a pad is the one that
    // labels it — object key order would let the top row claim them first
    // The on-screen keys are labelled by the note a typed key PLAYS — the
    // sum the keydown handler below makes (`rowMidi`) — never by a layout
    // worked out a second time: a row tiled past the first octave, a row of
    // notes in key and a row by sound all light the key that names them.
    // A key several rows play shows ONE letter — the lowest row's — and
    // keeps them all in data-typed and its title ("VK" piles read as noise).
    const byMidi = new Map(pkeys.map((k) => [piano().midiOf(k), k]));
    const typed = new Map(pkeys.map((k) => [k, []]));
    for (const r of ROW_ORDER) {
      const rm = rows[r], caps = ROWS[r].caps;
      if (r === "home" && classic()) pkeys.forEach((k) => { const c = k.querySelector(".qhint")?.dataset.classic; if (c) typed.get(k).push(c); });
      if (rm === "off" || ownedByPiano(r)) continue;
      for (let i = 0; i < caps.length; i++) {
        // every row labels its own slice of the pads; the lowest row to
        // reach a pad wins so a wrapped repeat can't overwrite it
        const hit = (rm !== "piano" && padAt(padsFor(rm), startOf(r) + i)) || null, h = hit?.pad.querySelector(".khint");
        if (h && !h.textContent) h.textContent = caps[i];
        // a chord pad's letter sits on its root's key: with every row on chords the keys would be blank
        const m = rowMidi(r, i) ?? (hit?.pad.classList.contains("chord-pad") ? padNotes(hit.pad, hit.lift)[0] : null);
        typed.get(byMidi.get(m))?.push(caps[i]); // a key may be several rows' (rows by sound: each runs from C)
      }
    }
    for (const [k, cs] of typed) {
      const h = k.querySelector(".qhint");
      if (h) h.textContent = cs[0] || "";
      k.dataset.typed = cs.join(" ");
      k.title = cs.length > 1 ? `typed: ${cs.join(", ")}` : "";
    }
  }
  // the one note typed key `i` of row `r` plays (null: a chord pad, no pad)
  function rowMidi(r, i) {
    if (rows[r] === "piano") return 12 * (piano().octave() + 1) + i + startOf(r); // a chromatic run, continued semitone-wise
    const hit = padAt(padsFor(rows[r]), startOf(r) + i);
    return hit?.pad.classList.contains("note-pad") ? Number(hit.pad.dataset.midi) + shift() + hit.lift : null;
  }
  document.addEventListener("change", (e) => {
    const s = e.target.closest?.('#kbd-rows select[name^="row-"]');
    if (!s) return;
    rows[s.name.slice(4)] = s.value;
    localStorage.setItem("synthlab.rows", JSON.stringify(rows));
    applyRows();
  });
  // "all rows" (#kbd-rows): every row at once; a change event so the toolbar's strip (keysbar.js) redraws
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.("[data-rows-all]");
    if (!b) return;
    for (const r of ROW_ORDER) rows[r] = b.dataset.rowsAll;
    localStorage.setItem("synthlab.rows", JSON.stringify(rows));
    applyRows();
    b.dispatchEvent(new Event("change", { bubbles: true }));
  });
  const applyAll = () => { applyLibQ(); applyRows(); };
  document.addEventListener("DOMContentLoaded", applyAll);
  document.addEventListener("rows-by", applyRows);
  document.addEventListener("htmx:after:settle", applyAll);
  const rowPads = new Map(); // held id -> pad element lit by it
  // The octave pair, next to the on-screen - / + buttons. Every surface
  // already follows the octave (pads add shift(), piano rows read octave()),
  // so this only needs to drive piano.js's setOctave.
  const OCTAVE_KEYS = { Minus: -1, Equal: 1 };
  // #keys-toggle (piano.js owns it) switches EVERY typed surface off: rows,
  // the number row, chord-mode letters and the octave pair, not just piano's
  // — and piano.js's `typing` is the whole gate (a page's own keys, Perform's pads)
  const typing = (e) => window.synthlab.typing(e);
  window.addEventListener("keydown", (e) => {
    if (!bar() || e.ctrlKey || e.metaKey || e.altKey || !typing(e)) return;
    if (e.code in OCTAVE_KEYS) {
      e.stopImmediatePropagation();
      piano().setOctave(piano().octave() + OCTAVE_KEYS[e.code]);
      applyRows();
      return;
    }
    if (e.repeat) return;
    const r = rowOf(e.code);
    // "off" means the row plays nothing — piano.js's own letters on it
    // included (they used to leak through); Z/X octave and ? still pass
    if (r && rows[r] === "off") { if (e.key.toLowerCase() in KEYMAP) e.stopImmediatePropagation(); return; }
    if (!r || ownedByPiano(r)) return;
    const rm = rows[r], i = ROWS[r].codes.indexOf(e.code), id = `row:${e.code}`;
    e.stopImmediatePropagation();
    if (rm === "piano") {
      const m = rowMidi(r, i); // no pads to tile
      chordOn(id, chordy() ? chordAt(m) : [m], 1);
      return;
    }
    const hit = padAt(padsFor(rm), startOf(r) + i);
    if (!hit) return;
    const { pad: p, lift } = hit;
    rowPads.set(id, p);
    if (p.classList.contains("chord-pad")) { p.classList.add("down"); chordOn(id, padNotes(p, lift), 1); }
    else padOn(p, id, lift);
  }, true);
  window.addEventListener("keyup", (e) => {
    const id = `row:${e.code}`;
    if (!held.has(id)) return;
    e.stopImmediatePropagation();
    chordOff(id);
    rowPads.get(id)?.classList.remove("down");
    rowPads.delete(id);
  }, true);

  // ---- intercept single-note triggers in chord mode ----------------------
  // window capture fires before piano.js's document listeners.
  const midiOfKey = (key) => 12 * (piano().octave() + 1) + KEYMAP[key];
  window.addEventListener("keydown", (e) => {
    if (!chordy() || !bar() || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
    const key = e.key.toLowerCase(), focused = e.target.closest?.(".pkey");
    if (focused && ACT_KEY.test(e.key)) { // Enter/Space on a focused key
      e.stopImmediatePropagation(); e.preventDefault();
      chordOn(`focus:${focused.dataset.note}:${focused.dataset.rel}`, chordAt(piano().midiOf(focused)), 1);
      return;
    }
    if (!(key in KEYMAP) || !typing(e)) return;
    e.stopImmediatePropagation();
    chordOn(`kbd:${e.code || key}`, chordAt(midiOfKey(key)), 1); // by code: AltGr mid-hold renames e.key
  }, true);
  window.addEventListener("keyup", (e) => {
    const focused = e.target.closest?.(".pkey");
    const id = focused && ACT_KEY.test(e.key) ? `focus:${focused.dataset.note}:${focused.dataset.rel}` : `kbd:${e.code || e.key.toLowerCase()}`;
    if (!held.has(id)) return;
    e.stopImmediatePropagation();
    chordOff(id);
  }, true);

  // ---- octave buttons move the pads too: pads are authored at the default
  // octave (C4); every pad note is shifted by the keyboard's current offset
  const shift = () => (piano().octave() - 4) * 12;
  const nameOf = (m) => `${NOTE_NAMES[m % 12]}${Math.floor(m / 12) - 1}`;
  document.addEventListener("octave-changed", () => {
    document.querySelectorAll("#chord-bar .note-pad").forEach((b) => {
      const m = Number(b.dataset.midi) + shift();
      b.firstChild.textContent = `${nameOf(m)}`;
      b.setAttribute("aria-label", `play ${nameOf(m)}`);
    });
  });

  // ---- note pads: every tone of the key; chord mode plays the chord on it --
  function padOn(btn, id, lift = 0) {
    btn.classList.add("down");
    const m = Number(btn.dataset.midi) + shift() + lift;
    // harmonics are partials, never re-harmonised into a chord
    chordOn(id, chordy() && !btn.classList.contains("harm") ? chordAt(m) : [m], 1);
  }
  document.addEventListener("pointerdown", (e) => {
    const btn = e.target.closest("#chord-bar .note-pad");
    if (!btn) return;
    e.preventDefault();
    padOn(btn, `npad:${e.pointerId}`);
  });
  const notePadUp = (e) => {
    const mine = `npad:${e.pointerId}`;
    for (const id of [...held.keys()]) if (id === mine || (e.type === "pointercancel" && id.startsWith("npad:") && id !== "npad:focus")) chordOff(id);
    if (![...held.keys()].some((id) => id.startsWith("npad:"))) document.querySelectorAll("#chord-bar .note-pad.down").forEach((b) => b.classList.remove("down"));
  };
  document.addEventListener("pointerup", notePadUp);
  document.addEventListener("pointercancel", notePadUp);
  // Enter/Space on a focused note pad or chord pad
  document.addEventListener("keydown", (e) => {
    const btn = e.target.closest?.("#chord-bar .note-pad, #chord-bar .chord-pad");
    if (!btn || e.repeat || !ACT_KEY.test(e.key)) return;
    e.preventDefault();
    if (btn.classList.contains("note-pad")) padOn(btn, "npad:focus");
    else { btn.classList.add("down"); chordOn("pad:focus", padNotes(btn), 1); }
  });
  document.addEventListener("keyup", (e) => {
    const btn = e.target.closest?.("#chord-bar .note-pad, #chord-bar .chord-pad");
    if (!btn || !ACT_KEY.test(e.key)) return;
    btn.classList.remove("down");
    chordOff(btn.classList.contains("note-pad") ? "npad:focus" : "pad:focus");
  });
  // focus moved on mid-press (Tab, a click elsewhere): the keyup lands on
  // another element, so the pad's or key's focus id is released here
  document.addEventListener("focusout", (e) => {
    const n = held.size;
    for (const id of [...held.keys()]) if (/^(focus:|n?pad:focus$)/.test(id)) chordOff(id);
    if (held.size < n) e.target.classList?.remove("down");
  });
  // window blur: piano.js silences every voice, so every chord goes with its
  // strum timers — one still pending started a note nothing ever released
  window.addEventListener("blur", () => {
    for (const id of [...held.keys()]) chordOff(id);
    rowPads.clear();
    bar()?.querySelectorAll(".down").forEach((b) => b.classList.remove("down"));
  });
  window.addEventListener("pointerdown", (e) => {
    const el = chordy() && bar() && e.target.closest(".pkey");
    if (!el) return;
    e.stopImmediatePropagation(); e.preventDefault();
    chordOn(`ptr:${e.pointerId}`, chordAt(piano().midiOf(el)), 1);
  }, true);
  for (const ev of ["pointerup", "pointercancel"]) window.addEventListener(ev, (e) => {
    if (held.has(`ptr:${e.pointerId}`)) { e.stopImmediatePropagation(); chordOff(`ptr:${e.pointerId}`); }
  }, true);
  // MIDI (midi.js calls the bridge): wrap once piano.js has installed it.
  // Only a key's own id (midi:60) is a chord root: chordOn plays its notes
  // through this same bridge as midi:60:0, midi:60:1..., and matching those
  // too sent every note back into chordOn, forever (the tab froze).
  document.addEventListener("DOMContentLoaded", () => {
    const p = piano(); if (!p) return;
    const on = p.noteOn, off = p.noteOff;
    // (`at`: the keys arp's notes are due at a context time — arp.js)
    p.noteOn = (id, m, vel, at) => (chordy() && bar() && /^midi:\d+$/.test(id)) ? chordOn(id, chordAt(m), vel) : on(id, m, vel, at);
    p.noteOff = (id, at) => held.has(id) ? chordOff(id) : off(id, at);
  });

  // ---- chord pads (I–VII) + keys 1–7 -------------------------------------
  function padNotes(btn, lift = 0) { return JSON.parse(btn.dataset.notes || "[]").map((n) => n + shift() + lift); }
  document.addEventListener("pointerdown", (e) => {
    const btn = e.target.closest("#chord-bar .chord-pad");
    if (!btn) return;
    e.preventDefault();
    btn.classList.add("down");
    chordOn(`ppad:${btn.dataset.degree}`, padNotes(btn), 1);
  });
  // release by held id, not by which button still looks pressed: every
  // pointer-held chord ends on any pointerup/cancel, wherever it lands
  const padUp = () => {
    for (const id of [...held.keys()]) {
      if (!id.startsWith("ppad:")) continue;
      chordOff(id);
      bar()?.querySelector(`.chord-pad[data-degree="${id.slice(5)}"]`)?.classList.remove("down");
    }
  };
  document.addEventListener("pointerup", padUp);
  document.addEventListener("pointercancel", padUp);
  // (keys 1–7 for the key chords are the number row's default "chords" map above)

  // ---- code line: the server parses, we play it in time -------------------
  let playToken = 0;
  async function playCode(chords, beat) {
    const token = ++playToken;
    for (let i = 0; i < chords.length; i++) {
      if (token !== playToken) return;
      if (chords[i].length) chordOn("code", chords[i], 1);
      await new Promise((r) => setTimeout(r, beat * 900));
      chordOff("code");
      await new Promise((r) => setTimeout(r, beat * 100));
    }
  }
  // Every pad block (notes, chords, harmonics, the chord chart) is collapsed
  // by default — the keyboard is what you reach for first, and ~500 pads
  // between it and the panel stack pushed the sound controls off screen.
  // Whatever you last opened is remembered per block, except the ~500-pad
  // chord chart, which starts closed on every visit.
  const REMEMBERED = "#chord-bar details[id]:not(#chord-lib)";
  // A block never opened or shut here takes its default (`data-open`: chords
  // in key). Play tools itself is a popover: open again if it was left open.
  function syncBlocks() {
    document.querySelectorAll(REMEMBERED).forEach((d) => {
      const was = localStorage.getItem("synthlab." + d.id);
      d.open = was === null ? d.hasAttribute("data-open") : was === "1";
    });
    const pt = document.getElementById("play-tools");
    if (pt && !pt.matches(":popover-open") && localStorage.getItem("synthlab.play-tools") === "1" && document.querySelector('[popovertarget="play-tools"]')?.checkVisibility()) pt.showPopover();
  }
  document.addEventListener("toggle", (e) => {
    if (e.target.id === "play-tools") localStorage.setItem("synthlab.play-tools", e.newState === "open" ? "1" : "0");
  }, true);
  document.addEventListener("toggle", (e) => {
    const d = e.target.closest?.("#chord-bar details[id]");
    if (!d) return;
    if (d.matches(REMEMBERED)) localStorage.setItem("synthlab." + d.id, d.open ? "1" : "0");
    if (d.open) applyAll(); // pads that were hidden still need their key hints
  }, true);
  document.addEventListener("DOMContentLoaded", syncBlocks);
  document.addEventListener("htmx:after:settle", syncBlocks); // a view swap brings a fresh bar
  // #chord-bar is a region, morphed on every root/scale change. Its blocks'
  // `open` is client state the server never draws: removing it closed every
  // open block (and the toggle it fired stored them closed). htmx:after:swap
  // was no rescue — it fires on the element that posted, which that same
  // swap can detach — so keep the attribute out of the morph altogether.
  document.addEventListener("htmx:before:morph:attr", (e) => {
    if (e.detail.attrName === "open" && e.target.matches?.("#chord-bar details:not(#chord-lib)")) e.preventDefault();
  });
  // The chord chart arrives from /fragment/chordlib on first open. That is
  // an ordinary hx-get swap, so the `htmx:after:settle` listener above already
  // re-applies the hints; two `htmx:afterSwap` listeners used to sit here,
  // which is htmx 2 naming and never fired under htmx 4 at all.

  document.addEventListener("submit", async (e) => {
    const form = e.target.closest("#chord-bar form.code-line");
    if (!form) return;
    e.preventDefault();
    const res = await window.synthlab.send("/fragment/chords/code", new FormData(form), { refused: "Couldn't read that" });
    if (!res) return;
    const data = JSON.parse(res.text);
    window.synthlab.ensureCtx();
    playCode(data.chords, data.beat_seconds);
  });
})();
