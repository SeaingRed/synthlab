// synthlab/studio/static/samplerec.js
// ● Record sample, in the Synths page's sampler panel: a mic
// take timed to the session, straight into the sampler.
// - Settings beside the button: 1, 2 or 4 bars ([data-smp-bars], 1 by
//   default) and a click (#smp-click, on by default), remembered per
//   browser. They are buttons, not inputs (the #patch form posts on every
//   input inside it), set again after every redraw of the panel.
// - A press opens the mic (voice.js's capture, the browser's voice
//   processing off, as songrec.js's mic takes), then counts one bar in at
//   the sound bar's bpm (#t-bpm), 4/4: WebAudio blips to the context's
//   destination, and "ready, 3, 2, 1" big over the page (#smp-countdown,
//   outside every region, as songtake.js draws #rec-countdown). It then
//   records exactly the bars chosen, the click on every beat if it is on,
//   and stops by itself. A second press while it counts in or records
//   cancels: no sample.
// - The take is cut to start on the downbeat after the count-in, shifted
//   earlier by the browser's input + output latency — or by this machine's
//   calibration (/song/calibration.json, the Song page's #song-latency)
//   when it reports none — padded or trimmed to the exact length, and
//   posted as a 16-bit WAV (S.voice.toWav) to /fragment/sampler/record,
//   from #smp-countdown (multipart, no field under it): the clip store
//   keeps it and the sampler loads it, the page answered whole.
// Budget: <= 110 code lines.

(() => {
  "use strict";
  window.synthlab ??= {};
  const S = window.synthlab;
  if (typeof document === "undefined") return;
  const $ = (id) => document.getElementById(id);
  const toast = (m, kind) => S.toast?.(m, kind);
  const PREFS = { bars: "synthlab.smpRecBars", click: "synthlab.smpRecClick" };
  const pref = (k, d) => { try { return localStorage.getItem(PREFS[k]) ?? d; } catch { return d; } };
  const keep = (k, v) => { try { localStorage.setItem(PREFS[k], v); } catch { /* private mode */ } };
  const bars = () => ({ 1: 1, 2: 2, 4: 4 })[pref("bars", "1")] || 1;
  const clicks = () => pref("click", "1") !== "0";
  const bpm = () => Math.min(200, Math.max(40, S.tempo() || 120)); // limits.BPM_RANGE
  const heard = () => S.heardTime();
  let rec = null; // the running take: { bars, click, bar, at0, mic, cal, nodes, timers }

  // ---- what the panel shows: set again after every redraw -------------------
  function paint() {
    const btn = $("smp-rec");
    if (btn) {
      btn.setAttribute("aria-pressed", String(!!rec));
      const label = rec ? "■ Cancel" : "● Record sample";
      if (btn.textContent !== label) btn.textContent = label;
    }
    document.querySelectorAll("[data-smp-bars]").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.smpBars) === bars())));
    $("smp-click")?.setAttribute("aria-pressed", String(clicks()));
    const out = $("smp-rec-status");
    if (!out) return;
    let txt = "";
    if (rec && rec.at0 == null) txt = "opening the mic…";
    else if (rec) {
      const t = heard() - rec.at0;
      txt = t < 0 ? "count-in…" : `REC bar ${Math.min(rec.bars, Math.floor(t / rec.bar) + 1)}/${rec.bars}`;
    }
    if (out.textContent !== txt) out.textContent = txt;
  }
  let ticker = 0;
  const ticking = () => { if (!ticker) ticker = setInterval(() => { if (!rec) { clearInterval(ticker); ticker = 0; } paint(); }, 100); };

  // ---- the count-in over the page, and the click -----------------------------
  function say(txt, big) {
    const el = $("smp-countdown");
    if (!el) return;
    el.textContent = txt; el.hidden = !txt; el.dataset.big = big ? "1" : "";
    el.classList.remove("pulse"); void el.offsetWidth; el.classList.add("pulse");
  }
  function blip(r, t, accent) {
    const c = S.getCtx(), o = c.createOscillator(), g = c.createGain();
    o.frequency.value = accent ? 1760 : 1100;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(accent ? 0.35 : 0.22, t + 0.003);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.045);
    o.connect(g).connect(c.destination);
    o.start(t); o.stop(t + 0.05);
    r.nodes.push(g);
  }
  function hush(r) { r.timers.forEach(clearTimeout); r.nodes.forEach((g) => g.disconnect()); say(""); }

  // ---- a take ------------------------------------------------------------------
  async function begin() {
    S.ensureCtx();
    const c = S.getCtx();
    c.resume?.();
    const r = (rec = { bars: bars(), click: clicks(), at0: null, cal: 0, nodes: [], timers: [] });
    paint(); ticking();
    fetch("/song/calibration.json").then((x) => x.json()).then((j) => { r.cal = (Number(j.latency_ms) || 0) / 1000; }).catch(() => {});
    try { r.mic = await S.voice.capture(c); } catch {
      if (rec === r) { rec = null; paint(); }
      return toast("Microphone permission denied.", "error");
    }
    if (rec !== r) return r.mic.stop().catch(() => {}); // cancelled while the mic opened
    const bar = 240 / bpm(), at = c.currentTime + 0.1, lag = S.outLatency();
    Object.assign(r, { bar, at0: at + bar });
    for (let k = 0; k < 4 + (r.click ? 4 * r.bars : 0); k++) blip(r, at + (k * bar) / 4, k % 4 === 0);
    const ms = (t) => Math.max(0, (t + lag - c.currentTime) * 1000);
    ["ready", "3", "2", "1"].forEach((txt, k) => r.timers.push(setTimeout(() => say(txt, k > 0), ms(at + (k * bar) / 4))));
    r.timers.push(setTimeout(() => say("● rec", false), ms(r.at0)));
    r.timers.push(setTimeout(() => say(""), ms(r.at0 + bar / 4)));
    // past the last bar by the input latency (or the calibration) and a margin
    r.timers.push(setTimeout(() => finish(r), ms(r.at0 + r.bars * bar + (r.mic.input || 0) + 0.4) + r.cal * 1000));
  }

  function cancel() {
    const r = rec;
    rec = null;
    if (!r) return;
    hush(r); r.mic?.stop().catch(() => {}); paint();
  }

  async function finish(r) {
    if (rec !== r) return;
    rec = null; hush(r); paint();
    const c = S.getCtx();
    try {
      const buf = await r.mic.stop(), out = S.outLatency();
      const lat = out || r.mic.input ? out + r.mic.input : r.cal;
      const sr = buf.sampleRate, from = Math.max(0, Math.round((r.at0 - r.mic.began + lat) * sr));
      const n = Math.round(r.bars * r.bar * sr), cut = c.createBuffer(1, n, sr); // silence pads a short take
      cut.copyToChannel(buf.getChannelData(0).subarray(from, from + n), 0);
      await htmx.ajax("POST", "/fragment/sampler/record", { source: $("smp-countdown"), target: "body", swap: "innerMorph",
        values: { bars: String(r.bars), file: S.voice.toWav(cut, 1) } });
    } catch (err) { toast(String(err?.message || err), "error"); }
  }

  document.addEventListener("click", (e) => {
    const t = e.target;
    if (!(t instanceof Element)) return;
    if (t.closest("#smp-rec")) return rec ? cancel() : begin();
    const b = t.closest("[data-smp-bars]");
    if (b) { keep("bars", b.dataset.smpBars); return paint(); }
    if (t.closest("#smp-click")) { keep("click", clicks() ? "0" : "1"); paint(); }
  });
  // the panel was redrawn: set it again; gone (another page, no sampler): cancel
  document.addEventListener("htmx:after:settle", () => { if (rec && !$("smp-rec")) cancel(); paint(); });
  document.addEventListener("DOMContentLoaded", paint);
})();
