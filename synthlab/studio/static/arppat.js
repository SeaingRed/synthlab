// synthlab/studio/static/arppat.js
// The arp pattern for the keys arp (arp.js's "pattern" mode, ▦): the walk
// arppattern.py renders for the grid arp, line for line, so a chord held on
// the keys plays what the grid arp renders over the same notes —
// tests/studio/test_arp_pattern.py runs both over the same cases.
// - The pattern: the edited lane's (`Arrangement.arp_pattern`, the default
//   while it has none), read off the editor the server draws
//   (fragments/arp_pattern.html, region arp-pattern: `data-pattern`, the
//   key a step's transpose counts in, `data-key`, and the grid's step in
//   beats, `data-grid-beats`). Nothing here posts: the editor's fields do.
// - A step (`step`): on, the next note of the run (arp.js's own `pick`: up,
//   down, up-down, as played, random — a seeded shuffle a cycle); a rest
//   takes none. Its chance rolls on a hash of (the step, the pattern's
//   pass) — `roll`, core.hash_u's bits, never Math.random; its transpose
//   counts tones of the loop's key (`shift`); repeats split the step.
// - When it starts again (`hits`): retrig "note" at a note struck (arp.js
//   says when: `struck`) — not one joining within 30 ms of where the walk
//   started, a chord's notes being one strike —, "bar" at every bar line
//   (on the loop's while it plays: a run started mid-bar is walked to its
//   place, and its pass count is the loop's bar's, as the grid counts it),
//   "free" never: a new chord goes on where the last one left the walk.
// Budget: <= 80 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const RATES = { grid: null, "1/4": 1, "1/8": 1 / 2, "1/8T": 1 / 3, "1/16": 1 / 4, "1/16T": 1 / 6, "1/32": 1 / 8 };
  const SEED = 2757835283; // arppattern.ROLL_SEED: crc32("arp-pattern")
  // core.hash_u (lowbias32): the bits Python computes
  const hashU = (seed, key) => {
    let x = (seed ^ Math.imul(key, 0x9e3779b9)) >>> 0;
    x ^= x >>> 16; x = Math.imul(x, 0x7feb352d) >>> 0; x ^= x >>> 15; x = Math.imul(x, 0x846ca68b) >>> 0; x ^= x >>> 16;
    return (x >>> 0) / 4294967296;
  };
  const roll = (i, c) => hashU((SEED + i) >>> 0, c);
  // `m` moved `tr` tones of the key [root pitch class, intervals]; off the scale it keeps its distance above the tone under it
  function shift(m, tr, key) {
    if (!tr) return m;
    const [root, iv] = key, rel = m - root, oct = Math.floor(rel / 12), pc = rel - 12 * oct;
    let d = 0;
    for (let i = 0; i < iv.length; i++) if (iv[i] <= pc) d = i;
    const idx = oct * iv.length + d + tr, o2 = Math.floor(idx / iv.length);
    return root + 12 * o2 + iv[idx - o2 * iv.length] + (pc - iv[d]);
  }
  const fresh = () => ({ k: 0, ni: 0, c: -1 });
  const restart = (r) => { r.k = 0; r.ni = 0; };
  // the next step over `held` (midi numbers): its hits, [offset, midi, velocity scale, gate] (offset and gate: shares of the step)
  function step(r, held, pat, key) {
    const steps = pat.steps, i = r.k % steps.length;
    if (i === 0) r.c++;
    r.k++;
    const st = steps[i];
    if (!st.on || !held.length) return [];
    const m0 = S.arpSchedule.pick(held.map((midi) => ({ midi })), pat.mode, pat.oct, r.ni)[0];
    r.ni++;
    if (m0 == null || roll(i, r.c) >= st.chance) return [];
    const m = shift(m0, st.tr, key);
    if (m < 0 || m > 127) return [];
    return Array.from({ length: st.rep }, (_, h) => [h / st.rep, m, st.vel, st.gate / st.rep]);
  }
  const perBar = (barBeats, beats) => Math.max(1, Math.ceil(barBeats / beats - 1e-9));
  S.arpPattern = { step, restart, fresh, shift, roll, perBar, RATES };
  if (typeof document === "undefined") return;

  // ---- the keys arp's side: the pattern the server drew -----------------------------
  const DEF = { steps: [{ on: true, vel: 1, tr: 0, gate: 0.9, chance: 1, rep: 1 }], rate: "grid", mode: "up", oct: 1, retrig: "note" };
  function current() {
    const d = document.getElementById("arp-pat")?.dataset || {};
    try { return { pat: JSON.parse(d.pattern), key: JSON.parse(d.key), grid: Number(d.gridBeats) || 0.25 }; } catch { return { pat: DEF, key: [9, [0, 2, 3, 5, 7, 8, 10]], grid: 0.25 }; }
  }
  // a step of the pattern in beats ("grid": the note grid's step)
  const beats = () => { const c = current(); return RATES[c.pat.rate] ?? c.grid; };
  // arp.js's run (`run.n` its steps, `run.k` the step's index from the loop's bar line while it plays) -> this step's hits
  let kept = null; // the last run's walk: "free" goes on from it
  function hits(run, notes, gridded) {
    const { pat, key } = current(), held = notes.map((x) => x.midi), bar = perBar(S.barBeats?.() || 4, beats());
    const first = !run.pat;
    const r = (run.pat ??= pat.retrig === "free" && kept ? kept : { ...fresh(), from: run.t });
    kept = r;
    const struck = run.struck != null && run.struck > r.from + 0.03; // a chord's notes: one strike
    run.struck = null;
    const atBar = gridded ? run.k === 0 : run.n % bar === 0;
    if ((pat.retrig === "note" && struck) || (pat.retrig === "bar" && atBar)) {
      restart(r); r.from = run.t;
      // the loop's bar: the pass count the grid has there
      if (pat.retrig === "bar" && gridded) r.c = (S.loopBar?.(run.t) || 0) * Math.ceil(bar / pat.steps.length) - 1;
    } else if (first && pat.retrig === "bar" && gridded) {
      r.c = (S.loopBar?.(run.t - run.k * beats() * 60 / (S.tempo?.() || 120)) || 0) * Math.ceil(bar / pat.steps.length) - 1;
      for (let i = 0; i < run.k; i++) step(r, held, pat, key); // a run started mid-bar: walked to its place
    }
    return step(r, held, pat, key);
  }
  S.arpPattern.current = current;
  S.arpPattern.beats = beats;
  S.arpPattern.hits = hits;
})();
