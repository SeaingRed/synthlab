// synthlab/studio/static/songtake.js
// A take on the Song page, around songrec.js. songrec.js owns
// the take itself (arming, the count-in's blips, stamping, the posts); this
// file is what a player sees and plays while it runs, and what happens
// after:
// - The count-in counts down: "ready", 3, 2, 1 on the four beats of the
//   bar before the take (`song-countin` from songrec.js), then "● rec",
//   big over the page (#rec-countdown, outside every region).
// - The metronome: a tick on every beat while a take runs (#rec-click, on
//   by default; the bar line ticks higher). Ticks go to the context's
//   destination, not the master, as click.js's do.
// - Finger drums: with the drums lane armed, the keys A S D F G H J K and
//   Z X C V B N M , play kick snare hat ohat clap tom rim crash (two keys
//   a voice, for two hands), MIDI notes by the General MIDI drum map
//   (midi.js asks S.drumNote first) and the floating pads (#finger-drums)
//   play them by click, touch or pen. The sound is the drums TRACK's own
//   kit (/song/track/{id}/hit/{voice}.wav), fetched once a voice and again
//   after a song change; every hit goes to songrec.js (S.onHit), which
//   records it when a take runs.
// - "✦ Clean up" (data-song-action="clean"), or by itself when a take
//   ends with #rec-clean on: POST /song/take/clean, then #take-report
//   lists what was fixed, Gemini's feedback and edits, and offers Undo
//   (the song's own ↶). The take's last post is awaited first.
// The two toggles are remembered per browser; the topbar is morphed after
// every song edit, so they are set again after each swap.
// Budget: <= 170 code lines.

(() => {
  "use strict";
  window.synthlab ??= {};
  const S = window.synthlab;
  if (typeof document === "undefined") return;
  const $ = (id) => document.getElementById(id);
  const VOICES = ["kick", "snare", "hat", "ohat", "clap", "tom", "rim", "crash"];
  const KEYS = { a: 0, z: 0, s: 1, x: 1, d: 2, c: 2, f: 3, v: 3, g: 4, b: 4, h: 5, n: 5, j: 6, m: 6, k: 7, ",": 7 };
  // General MIDI drums: 36 kick, 38/40 snare, 42/44 hat, 46 open hat, 39 clap, toms, 37 rim, cymbals
  const GM = { 35: 0, 36: 0, 38: 1, 40: 1, 42: 2, 44: 2, 46: 3, 51: 3, 53: 3, 59: 3, 39: 4, 41: 5, 43: 5, 45: 5, 47: 5, 48: 5, 50: 5, 37: 6, 49: 7, 52: 7, 55: 7, 57: 7 };
  const PREFS = { "rec-click": "synthlab.recClick", "rec-clean": "synthlab.recClean" };
  const pref = (id) => { try { return localStorage.getItem(PREFS[id]) !== "0"; } catch { return true; } };
  const toast = (m, kind) => S.toast?.(m, kind);

  // ---- the toggles: remembered, set again after every morph -------------------
  function syncPrefs() { for (const id in PREFS) { const el = $(id); if (el) el.checked = pref(id); } }
  document.addEventListener("change", (e) => {
    const id = e.target?.id;
    if (!(id in PREFS)) return;
    try { localStorage.setItem(PREFS[id], e.target.checked ? "1" : "0"); } catch { /* private mode */ }
  });

  // ---- the count-in: ready, 3, 2, 1 ---------------------------------------------
  let shown = [];
  function say(txt, big) {
    const el = $("rec-countdown");
    if (!el) return;
    el.textContent = txt; el.hidden = !txt; el.dataset.big = big ? "1" : "";
    el.classList.remove("pulse"); void el.offsetWidth; el.classList.add("pulse");
  }
  function hush() { shown.forEach(clearTimeout); shown = []; say(""); }
  document.addEventListener("song-countin", (e) => {
    const { at, bar } = e.detail, c = S.getCtx(), lag = S.outLatency();
    const ms = (t) => Math.max(0, (t + lag - c.currentTime) * 1000);
    hush();
    ["ready", "3", "2", "1"].forEach((txt, k) => shown.push(setTimeout(() => say(txt, k > 0), ms(at + (k * bar) / 4))));
    shown.push(setTimeout(() => say("● rec", false), ms(at + bar)));
    shown.push(setTimeout(() => say(""), ms(at + bar * 1.25)));
    ticking();
  });

  // ---- the metronome while a take runs ----------------------------------------
  let tick = 0, last = -1;
  function blip(t, accent) {
    const c = S.getCtx(), o = c.createOscillator(), g = c.createGain();
    o.frequency.value = accent ? 1760 : 1100;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(accent ? 0.35 : 0.22, t + 0.003);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.045);
    o.connect(g).connect(c.destination);
    o.start(t); o.stop(t + 0.05);
  }
  function ticking() { last = -1; if (!tick) tick = setInterval(schedule, 25); }
  function schedule() {
    const take = S.songTake?.();
    if (!take) { if (!S.songClock?.()?.playing) { clearInterval(tick); tick = 0; } return; }
    if (!$("rec-click")?.checked) return;
    const c = S.getCtx(), beat = take.bar / 4, horizon = c.currentTime + 0.1;
    let k = Math.max(0, Math.ceil((Math.max(c.currentTime, last + 0.001) - take.at0) / beat));
    for (let t = take.at0 + k * beat; t < horizon; k++, t = take.at0 + k * beat) {
      if (t > last) { blip(t, k % 4 === 0); last = t; }
    }
  }

  // ---- finger drums ---------------------------------------------------------------
  const bufs = new Map(); // "<track>:<voice>" -> AudioBuffer
  let rev = 0; // bumped by a song change: the kit may have moved
  const drumsArmed = () => { const a = S.songArmed?.(); return a?.kind === "drums" ? a.tid : null; };
  function load(tid) {
    S.ensureCtx?.();
    for (const v of VOICES) {
      S.fetchBuffer(`/song/track/${encodeURIComponent(tid)}/hit/${v}.wav?v=${rev}`)
        .then((b) => bufs.set(`${tid}:${v}`, b)).catch(() => {});
    }
  }
  function hit(i, vel) {
    const tid = drumsArmed();
    if (!tid) return false;
    const voice = VOICES[i], buf = bufs.get(`${tid}:${voice}`), c = S.getCtx?.();
    if (buf && c) {
      const src = c.createBufferSource(), g = c.createGain();
      src.buffer = buf; g.gain.value = 0.25 + 0.75 * vel;
      src.connect(g).connect(S.getMaster());
      src.start();
    }
    S.onHit?.(voice, vel);
    const pad = document.querySelector(`.fd-pad[data-voice="${voice}"]`);
    if (pad) { pad.classList.remove("struck"); void pad.offsetWidth; pad.classList.add("struck"); }
    return true;
  }
  S.drumNote = (note, vel) => GM[note] !== undefined && hit(GM[note], vel);
  const typing = (t) => t?.closest?.("input:not([type=checkbox]):not([type=radio]), textarea, select, [contenteditable]");
  window.addEventListener("keydown", (e) => {
    if (e.repeat || e.ctrlKey || e.metaKey || e.altKey || typing(e.target) || !drumsArmed()) return;
    const i = KEYS[e.key.toLowerCase()];
    if (i === undefined) return;
    e.preventDefault(); e.stopImmediatePropagation(); // before piano.js's keys and the page's shortcuts
    hit(i, 0.85);
  }, true);
  document.addEventListener("pointerdown", (e) => {
    const pad = e.target.closest?.(".fd-pad");
    if (!pad) return;
    e.preventDefault();
    hit(VOICES.indexOf(pad.dataset.voice), e.pointerType === "pen" ? Math.min(1, 0.3 + e.pressure) : 0.85);
  });
  function showPads() {
    const tid = drumsArmed(), pads = $("finger-drums");
    if (pads) pads.hidden = !tid;
    if (tid && !VOICES.every((v) => bufs.has(`${tid}:${v}`))) load(tid);
  }
  document.addEventListener("song-armed", showPads);
  // a song edit may have moved the kit (a take's own posts cannot: no refetch mid-take)
  document.addEventListener("song-changed", () => {
    if (S.songTake?.()) return;
    rev++;
    const tid = drumsArmed();
    if (tid) load(tid); // the old buffers play until the new ones land
  });

  // ---- ✦ Clean up ---------------------------------------------------------------------
  let cleaning = false;
  async function clean() {
    if (cleaning) return;
    cleaning = true;
    const btn = document.querySelector('[data-song-action="clean"]');
    btn?.setAttribute("aria-busy", "true");
    toast("✦ Cleaning up the take…");
    try {
      const body = new URLSearchParams({ ai: "1" });
      const res = await S.send("/song/take/clean", body, { refused: "The clean-up was refused." }); // (song-changed: what the answer triggers)
      if (res?.trigger["take-cleaned"]) report(res.trigger["take-cleaned"]);
    } catch (err) { toast(String(err?.message || err), "error"); } finally { cleaning = false; btn?.removeAttribute("aria-busy"); }
  }
  function list(el, items) {
    if (!el) return;
    el.replaceChildren(...items.map((s) => { const li = document.createElement("li"); li.textContent = s; return li; }));
    el.hidden = !items.length;
  }
  function report(r) {
    const pop = $("take-report");
    if (!pop) return;
    pop.querySelector(".tr-what").textContent = `${r.pattern} on ${r.track}`;
    list(pop.querySelector(".tr-fixes"), r.fixes || []);
    list(pop.querySelector(".tr-feedback"), r.feedback || []);
    list(pop.querySelector(".tr-edits"), r.edits || []);
    const ai = r.ai === "off" ? "Gemini was not asked." : r.ai ? `Gemini: ${r.ai}` : r.feedback?.length || r.edits?.length ? `Gemini${r.tokens ? ` · ${r.tokens} tokens` : ""}` : "Gemini had nothing to add.";
    pop.querySelector(".tr-note").textContent = ai;
    pop.querySelector(".tr-ai").hidden = !(r.feedback?.length || r.edits?.length);
    try { pop.showPopover(); } catch { /* already open */ }
  }
  document.addEventListener("song-take-end", async (e) => {
    hush();
    if (!$("rec-clean")?.checked) return;
    try { await e.detail.done; } catch { return; }
    clean();
  });
  document.addEventListener("click", (e) => {
    if (e.target.closest('[data-song-action="clean"]')) clean();
    if (e.target.closest("[data-take-undo]")) { $("take-report")?.hidePopover(); $("song-undo")?.click(); }
  });
  document.addEventListener("htmx:after:settle", () => { syncPrefs(); showPads(); });
  document.addEventListener("DOMContentLoaded", () => { syncPrefs(); showPads(); });
})();
