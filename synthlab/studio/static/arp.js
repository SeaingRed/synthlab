// synthlab/studio/static/arp.js
// The keys arp: the keyboard dock's
// Arp group — not the loop's arp (Perform's `arp_mode`, under FEEL, which
// plays the note grid's pitches a step each in the render). Hold a chord —
// typed in any layout, clicked, MIDI, a row's or a pad's chord, the latch
// included — and it plays as an arpeggio, through the same voice a typed
// note sounds in (piano.js): the keys light, the read-out names the chord
// it plays, ⏺ Rec takes the notes as they sound.
// - What it holds: piano.js keeps a note the arp takes as a voice that does
//   not sound (`arp`: its holds are the key, the pedal, the latch — so
//   Shift, Right Shift, ■ / Shift+Space, a lost focus and another sound let
//   go of it as of any note) and asks `takes` / tells `changed`. While the
//   arp runs on latched notes alone a note played is a note over it: the
//   latch again lets the arpeggio go.
// - When: on the session's tempo (S.tempo), each step `rate` beats; with
//   the loop playing (S.isPlaying) on its bar grid — the steps counted from
//   its last bar line (S.nextBar), so a meter or a tempo map moves them with
//   the loop — the first one the next step (one struck up to 30 ms late
//   sounds at once, on that step); nothing playing but a ⏺ Rec take on its
//   own bars, on the take's (keyrec.js S.keyRecGrid: a take from silence
//   lands on its grid); else from the press. Swing delays the odd steps,
//   as the loop's swing does (arrange.py).
// - In time: each note is posted ahead (LOOK) with the context time it is
//   due (`at`): the worklet holds it to its block (voice-worklet.js), a WAV
//   key waits its timer; its end likewise, `gate` of a step on.
// - Modes: up, down, up-down, random (a seeded shuffle a cycle: the same
//   every time it starts), as played, chord (the held notes as stabs);
//   octaves 1-4 (chord: a stab an octave higher each step).
// - ▦ pattern: the arp pattern (arppat.js — the grid arp's too, the edited
//   lane's): its rate, mode, octaves and each step's on / velocity /
//   transpose / gate / chance / repeats; when a note is struck is `struck`,
//   for its retrig.
// Per browser (localStorage synthlab.arp); the deck, the palette and Alt+A
// reach the switch. arpSchedule is the pure half (cycle / pick / stepAt),
// run under node by tests/studio/test_keys_arp.py.
// Budget: <= 150 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const RATES = { "1/4": 1, "1/8": 1 / 2, "1/8T": 1 / 3, "1/16": 1 / 4, "1/16T": 1 / 6, "1/32": 1 / 8 }; // beats a step
  const GLYPH = { up: "↑", down: "↓", updown: "↕", random: "⚄", played: "♪", chord: "≡", pattern: "▦" };
  // one cycle of steps, each the notes it plays: the held notes low to high
  // (as played for "played") over `oct` octaves
  function cycle(held, mode, oct) {
    const ms = held.map((h) => h.midi), base = mode === "played" ? ms : [...new Set(ms)].sort((a, b) => a - b);
    if (mode === "chord") return Array.from({ length: oct }, (_, o) => base.map((m) => m + 12 * o));
    const run = [];
    for (let o = 0; o < oct; o++) for (const m of base) run.push(m + 12 * o);
    const seq = mode === "down" ? run.reverse() : mode === "updown" && run.length > 2 ? [...run, ...run.slice(1, -1).reverse()] : run;
    return seq.map((m) => [m]);
  }
  // a seeded shuffle (mulberry32): random is the same each time it starts
  function shuffle(list, seed) {
    let s = seed >>> 0;
    const rnd = () => { s = (s + 0x6d2b79f5) | 0; let t = Math.imul(s ^ (s >>> 15), 1 | s); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
    const out = [...list];
    for (let i = out.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [out[i], out[j]] = [out[j], out[i]]; }
    return out;
  }
  // what step `n` of a run plays (a random cycle each note once)
  function pick(held, mode, oct, n) {
    const c = cycle(held, mode === "random" ? "up" : mode, oct), i = n % c.length;
    return mode === "random" ? shuffle(c, 1 + Math.floor(n / c.length))[i] : c[i];
  }
  // the first step at or after `after`: on the grid from `ref` (a bar line,
  // or the press), `step` seconds apart; swing moves an odd one later
  function stepAt(ref, after, step, swing) {
    const k = Math.max(0, Math.ceil((after - ref) / step - 1e-9));
    return { k, base: ref + k * step, t: ref + k * step + (k % 2 ? swing * step : 0) };
  }
  S.arpSchedule = { cycle, pick, stepAt, RATES };
  if (typeof document === "undefined") return;

  const $ = (id) => document.getElementById(id), KEY = "synthlab.arp";
  const DEF = { on: false, mode: "up", rate: "1/8", oct: 1, gate: 0.5, swing: 0 };
  let set = { ...DEF };
  try { set = { ...DEF, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch { /* blocked */ }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(set)); } catch { /* private mode */ } };
  const LOOK = 0.06; // posted this far ahead of its time (the worklet holds it to its block)
  const held = () => S.piano?.arpHeld?.() || [];
  const ctx = () => S.getCtx();
  const pattern = () => set.mode === "pattern" && !!S.arpPattern?.hits;
  const stepSec = () => (pattern() ? S.arpPattern.beats() : RATES[set.rate]) * 60 / (S.tempo?.() || 120);
  const take = () => S.keyRecGrid?.(); // a take recording on its own bars, nothing playing
  const grid = () => (!!S.isPlaying?.() && S.loopStart?.() != null) || !!take();
  // the loop's last bar line at or before `t` (a bar is S.barBeats at S.tempo: the meter's; nextBar keeps it on the loop) — or the take's
  const barLine = (t, g = take()) => (g ? g.ref + Math.floor((t - g.ref) / g.bar + 1e-6) * g.bar : S.nextBar(t - (S.barBeats?.() || 4) * 60 / (S.tempo?.() || 120) + 1e-4, 0));
  let run = null, seq = 0;
  // while it runs it is a layer of the transport (the top dock says so, and
  // ■ / Shift+Space stop it: transport.js stopAll -> piano.js keysOff)
  const spec = { kind: "Keys arp", layer: true, label: () => $("kbd-now")?.value || "arp", stop: () => S.keysOff?.() };

  function start() {
    S.ensureCtx();
    const now = ctx().currentTime, step = stepSec();
    run = { n: 0, ref: grid() ? barLine(now) : now, timer: setInterval(pump, 20) };
    Object.assign(run, stepAt(run.ref, grid() ? now - 0.03 : now, step, set.swing));
    S.transport?.claim("arp", spec);
    queueMicrotask(pump); // a chord's notes all land first: its strike is one call
  }
  function stop() {
    if (!run) return;
    clearInterval(run.timer);
    run = null;
    S.transport?.release("arp", spec);
  }
  // every step due within LOOK: its notes on, and off `gate` of a step on
  function pump() {
    while (run && run.t - ctx().currentTime < LOOK) {
      const notes = held(), step = stepSec();
      if (!notes.length) return stop();
      // steps the timers slept through (a hidden tab) are skipped, not played in a heap
      if (run.t < ctx().currentTime - 0.05) Object.assign(run, stepAt(run.ref, ctx().currentTime, step, set.swing));
      const at = Math.max(run.t, ctx().currentTime);
      const vel = notes[notes.length - 1].vel ?? 1, gate = Math.max(0.1, Math.min(1, set.gate));
      // [offset, midi, velocity scale, gate]: a pattern's step may rest, repeat, transpose
      const hits = pattern() ? S.arpPattern.hits(run, notes, grid()) : pick(notes, set.mode, set.oct, run.n).map((m) => [0, m, 1, gate]);
      run.n++;
      for (const [off, m, scale, g] of hits) {
        const id = `arp:${++seq}`, t0 = at + off * step, end = t0 + g * step;
        S.piano.noteOn(id, m, vel * scale, t0);
        setTimeout(() => S.piano.noteOff(id, end), Math.max(0, (end - ctx().currentTime - LOOK) * 1000));
      }
      // the next step: from the loop's latest bar line while it plays (a
      // loop started under a free arp takes it over), else on from the last.
      // The bar line at or before the next step's own line — a step on a bar
      // line counts from it (k 0: "bar" retrig, the swing's odd steps), as
      // the grid arp counts each bar (arrange._pattern_timeline)
      const after = run.base + step * 0.5;
      if (grid()) run.ref = barLine(run.base + step + 1e-6);
      Object.assign(run, stepAt(run.ref, after, step, set.swing));
    }
  }

  // ---- what piano.js asks and tells -----------------------------------------------
  // a note joins the arp while it is on — but over an arpeggio the latch alone holds, it sounds (`latching`: the latch's own strike joins)
  function takes(latching) {
    if (!set.on) return false;
    const h = held();
    return latching || !h.length || h.some((x) => x.key);
  }
  // the held notes moved: start, stop, and the read-out's chord (keysbar.js: chord-on / chord-off)
  let count = 0;
  function changed() {
    const h = held();
    if (run && h.length > count) run.struck = ctx().currentTime; // a note struck (a pattern's retrig "note": arppat.js)
    count = h.length;
    if (h.length && !run) start();
    if (!h.length) stop();
    document.dispatchEvent(h.length ? new CustomEvent("chord-on", { detail: { id: "arp", notes: h.map((x) => x.midi), arp: GLYPH[set.mode] } })
      : new CustomEvent("chord-off", { detail: { id: "arp" } }));
  }
  S.arp = { takes, changed, get: () => ({ ...set }), running: () => !!run };

  // ---- the group: the switch, its face, the ⚙ panel ---------------------------------
  function paint() {
    $("arp-btn")?.setAttribute("aria-pressed", String(set.on));
    const face = $("arp-face");
    // (the octaves are the panel's: "×2" did not fit the toolbar's room at 1920 px)
    if (face) { face.textContent = `${GLYPH[set.mode]} ${set.rate}`; face.title = `${set.mode} · ${set.rate} · ${set.oct} octave${set.oct > 1 ? "s" : ""}`; }
    for (const [k, v] of Object.entries(set)) {
      document.querySelectorAll(`#arp-pop [data-arp-${k}]`).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset[`arp${k[0].toUpperCase()}${k.slice(1)}`] === String(v))));
      const el = $(`arp-${k}`);
      if (el && el !== document.activeElement && el.type === "range") { el.value = String(v); el.style.setProperty("--fill", `${(v / el.max) * 100}%`); }
      const out = $(`arp-${k}-out`);
      if (out) out.textContent = `${Math.round(v * 100)}%`;
    }
  }
  function toggle() {
    set.on = !set.on; save(); paint();
    if (!set.on) { S.piano?.arpOff?.(); changed(); } // what it held sounds as it is held: a latched chord a drone again
  }
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (!(t instanceof Element)) return;
    if (t.closest("#arp-btn")) return toggle();
    const b = t.closest("#arp-pop [data-arp-mode], #arp-pop [data-arp-rate], #arp-pop [data-arp-oct]");
    if (!b) return;
    const [k, v] = Object.entries(b.dataset).find(([key]) => key.startsWith("arp")), name = k.slice(3).toLowerCase();
    set[name] = name === "oct" ? Number(v) : v;
    save(); paint();
  });
  document.addEventListener("input", (e) => {
    const k = /^arp-(gate|swing)$/.exec(e.target?.id || "")?.[1];
    if (!k) return;
    set[k] = Number(e.target.value) || 0;
    save(); paint();
  });
  // Alt+A: the switch from anywhere but a field (a toast when the dock is folded)
  window.addEventListener("keydown", (e) => {
    if (e.code !== "KeyA" || !e.altKey || e.ctrlKey || e.metaKey || e.repeat || S.isTypingTarget?.(e.target) || !$("arp-btn")) return;
    e.preventDefault(); e.stopImmediatePropagation();
    toggle();
    if (!$("arp-btn").checkVisibility()) S.toast?.(`Keys arp ${set.on ? "on" : "off"} (Alt+A)`);
  }, true);
  document.addEventListener("htmx:after:settle", paint);
  document.addEventListener("DOMContentLoaded", paint);
})();
