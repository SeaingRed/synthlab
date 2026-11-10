// synthlab/studio/static/vocal.js
// The live vocal chain: sing over the music through a live
// rack, hear it as you sing, record it in time. Its panel (#vox-pop,
// base.html: on every page, outside #view, so a page change keeps the mic
// singing) is drawn from GET /fragment/vocal when it opens; its openers
// (.vox-open: the rail, the keyboard dock, the Voice page) light while the
// mic is on.
// - The graph: the mic (echo cancelling, noise suppression and gain control
//   off: they smear a voice) -> input gain -> a high-pass (BiquadFilter, Q
//   0.707) -> the chain -> the monitor gain -> the master (so the scope, the
//   meter and a "+ beat" take hear it). The chain is GET /voice/live.json
//   (the server's rack resolved, fxlive.live_chain) built by livefx.js's own
//   stages (S.live.chain: fx-worklet.js runs + vox-worklet.js, ConvolverNode
//   reverbs, Biquad filters), in this context — "interactive" latency, every
//   stage an AudioWorklet. A redrawn panel whose rack moved re-reads it (a
//   chain of the same shape is updated in place: a drag never cuts a tail).
// - Denied or no mic: said in the status and a toast; 🎙 mic stays a button.
// - The status: the latency (the context's base + output, and the mic's own
//   where the browser says), the input's level in .vox-meter.
// - ⏺ (#vox-rec): as keyrec.js takes the keys — from the loop's next bar
//   line as heard (a bar on with count-in, #vox-count; four blips to the
//   speakers when nothing plays), until pressed again, rounded up to the bar
//   (free when nothing plays), at most MAX_CLIP_SECONDS. Two recorders, the
//   processed voice and the dry one (after the gain and the high-pass), each
//   cut from its own start; POST /take/vocal keeps both and answers the
//   panel, its takes listed.
// Settings (gain, hp, monitor, count-in) are this browser's: synthlab.vocal.
// Budget: <= 190 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  if (typeof document === "undefined") return;
  const $ = (id) => document.getElementById(id);
  const toast = (m, kind) => S.toast?.(m, kind);
  const INPUT = { gain: 1, hp: 80 }; // the mic's defaults, which ↺ reset puts back
  let opts = { ...INPUT, monitor: true, count: true };
  try { opts = { ...opts, ...JSON.parse(localStorage.getItem("synthlab.vocal") || "{}") }; } catch { /* blocked */ }
  const save = () => { try { localStorage.setItem("synthlab.vocal", JSON.stringify(opts)); } catch { /* private */ } };
  let on = null; // {stream, src, gain, hp, chainIn, out, mon, an, chain, sig}
  let opening = null, rec = null, said = "off";
  const ms = (t) => Math.max(0, (t - S.getCtx().currentTime) * 1000);
  const pad2 = (n) => String(n).padStart(2, "0");
  const clock = (s) => `${Math.floor(s / 60)}:${pad2(Math.floor(s % 60))}`;
  const cap = () => Number($("vox-rec")?.dataset.maxClipSeconds) || 30;

  function paint() {
    document.querySelectorAll(".vox-open").forEach((b) => b.setAttribute("aria-pressed", String(!!on)));
    $("vox-btn")?.setAttribute("aria-pressed", String(!!on));
    $("vox-monitor")?.setAttribute("aria-pressed", String(!!opts.monitor));
    $("vox-count")?.setAttribute("aria-pressed", String(!!opts.count));
    const r = $("vox-rec");
    if (r) {
      r.setAttribute("aria-pressed", String(!!rec));
      const t = rec ? S.getCtx().currentTime - rec.lag - rec.at : 0;
      r.textContent = !rec ? "⏺ Rec" : t < 0 ? `count-in ${Math.ceil(-t / rec.beat - 1e-6)}` : `■ ${clock(t)}`;
    }
    if ($("vox-status") && $("vox-status").textContent !== said) $("vox-status").textContent = said;
    document.querySelectorAll("[data-vox]").forEach((el) => {
      el.value = String(opts[el.dataset.vox]);
      el.style.setProperty("--fill", `${((el.value - el.min) / (el.max - el.min || 1)) * 100}%`);
    });
  }

  // the rack's chain from the server, into the graph (a redrawn panel: only when it moved)
  const rackSig = () => [...($("vox-panel")?.querySelectorAll(".fx-unit, .fx-unit input, .fx-unit select") || [])]
    .map((el) => el.dataset?.unit || el.value).join("|");
  async function rebuild() {
    if (!on) return;
    on.sig = rackSig();
    try {
      const j = await (await fetch("/voice/live.json", { headers: S.pageHeaders?.() })).json();
      if (on) on.chain = S.live.chain(on.chain, j.fx || [], on.chainIn, on.out);
    } catch (err) { toast(`The live vocal chain did not build: ${err?.message || err}`, "error"); }
  }

  async function start() {
    S.ensureCtx?.();
    const c = S.getCtx?.();
    if (!c) return;
    c.resume?.();
    const me = (opening = { cancelled: false });
    $("vox-btn")?.setAttribute("aria-busy", "true");
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      await S.live.start(); // the FX modules in this context (fx / eq / vox-worklet.js)
    } catch (err) {
      stream?.getTracks().forEach((t) => t.stop());
      const name = err?.name || "";
      said = name === "NotAllowedError" || name === "SecurityError" ? "the mic is blocked: allow it for this page, then 🎙 mic again"
        : name === "NotFoundError" || name === "OverconstrainedError" ? "no microphone found: plug one in, then 🎙 mic again"
        : `the mic did not open: ${err?.message || name || err}`;
      toast(`Live vocal: ${said}.`, "error");
      return paint();
    } finally { if (opening === me) opening = null; $("vox-btn")?.removeAttribute("aria-busy"); }
    if (me.cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
    const src = c.createMediaStreamSource(stream), gain = c.createGain(), hp = new BiquadFilterNode(c, { type: "highpass", Q: 0.707 });
    const chainIn = c.createGain(), out = c.createGain(), mon = c.createGain(), an = c.createAnalyser();
    src.connect(gain).connect(hp).connect(chainIn);
    hp.connect(an);
    out.connect(mon).connect(S.getMaster());
    on = { stream, src, gain, hp, chainIn, out, mon, an, chain: null, buf: new Float32Array(an.fftSize) };
    set();
    const inLat = stream.getAudioTracks()[0]?.getSettings?.().latency || 0;
    said = `on · ≈ ${Math.round((inLat + (c.baseLatency || 0) + (c.outputLatency || 0)) * 1000)} ms`;
    await rebuild();
    try { if (!localStorage.getItem("synthlab.vocalHint")) { localStorage.setItem("synthlab.vocalHint", "1"); toast("Live vocal on: wear headphones — speakers feed back into the mic."); } } catch { /* blocked */ }
    meter(); paint();
  }
  function stop() {
    if (rec) press();
    const t = on;
    on = null; said = "off";
    if (t) {
      t.stream.getTracks().forEach((x) => x.stop());
      try { t.src.disconnect(); t.out.disconnect(); t.mon.disconnect(); t.chainIn.disconnect(); } catch { /* gone */ }
      t.chain?.stages.forEach((s) => { s.fx?.port.postMessage({ type: "dispose" }); try { s.out.disconnect(); } catch { /* gone */ } });
    }
    paint();
  }
  // this browser's settings onto the running graph
  function set() {
    if (!on) return;
    const t = S.getCtx().currentTime;
    on.gain.gain.setTargetAtTime(opts.gain, t, 0.02);
    on.hp.frequency.setTargetAtTime(opts.hp, t, 0.02);
    on.mon.gain.setTargetAtTime(opts.monitor ? 1 : 0, t, 0.02);
  }
  function meter() {
    if (!on) return;
    on.an.getFloatTimeDomainData(on.buf);
    const pk = on.buf.reduce((a, v) => Math.max(a, Math.abs(v)), 0), bar = $("vox-panel")?.querySelector(".vox-meter i");
    if (bar) bar.style.width = `${Math.min(100, Math.max(0, (20 * Math.log10(pk + 1e-9) + 60) / 60 * 100)).toFixed(0)}%`;
    if (rec) paint();
    setTimeout(meter, 100);
  }

  // ---- ⏺: the processed voice and the dry one, in time ---------------------------
  function blip(t, accent) {
    const c = S.getCtx(), o = c.createOscillator(), g = c.createGain();
    o.frequency.value = accent ? 1760 : 1100;
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(accent ? 0.3 : 0.2, t + 0.003);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.045);
    o.connect(g).connect(c.destination); // the speakers: never in the take
    o.start(t); o.stop(t + 0.05);
  }
  function recorder(c, node) {
    const tap = c.createMediaStreamDestination(), media = new MediaRecorder(tap.stream), r = { node, tap, media, chunks: [], began: c.currentTime };
    node.connect(tap);
    media.ondataavailable = (e) => r.chunks.push(e.data);
    media.onstart = () => { r.began = c.currentTime; };
    media.start();
    return r;
  }
  function begin() {
    if (!on) return toast("Turn the mic on (🎙 mic) to record a vocal.", "error");
    const c = S.getCtx(), beat = 60 / Math.min(200, Math.max(40, S.tempo() || 120)), bar = 4 * beat, lag = S.outLatency();
    const line = S.isPlaying() ? S.nextBar(S.heardTime() + (opts.count ? bar : 0), 0.05) : null;
    let at = line ?? S.heardTime() + 0.05;
    if (line == null && opts.count) { at = c.currentTime + 0.1 + bar; for (let k = 0; k < 4; k++) blip(at - bar + k * beat, k === 0); }
    rec = { at, beat, bar, lag, grid: line != null, wet: recorder(c, on.out), dry: recorder(c, on.hp), timer: 0 };
    rec.timer = setTimeout(() => press(), ms(at + lag + cap() - 0.3));
    paint();
  }
  // a press while it records: it ends on the bar line ahead (free: now)
  function press() {
    if (!rec) return begin();
    const r = rec, t = S.getCtx().currentTime - r.lag - r.at;
    rec = null;
    clearTimeout(r.timer); paint();
    const lim = cap() - 0.3, len = t <= 0 ? 0 : Math.min(lim, r.grid ? Math.max(1, Math.ceil(t / r.bar - 1e-3)) * r.bar : t);
    setTimeout(() => finish(r, len), len ? ms(r.at + r.lag + len + 0.25) : 0);
  }
  async function cut(c, r, at, len) {
    await new Promise((ok) => { r.media.onstop = ok; r.media.stop(); });
    try { r.node.disconnect(r.tap); } catch { /* gone */ }
    const buf = await c.decodeAudioData(await new Blob(r.chunks).arrayBuffer()), sr = buf.sampleRate;
    const n = Math.round(len * sr), off = Math.round((at - r.began) * sr), out = c.createBuffer(2, n, sr);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(Math.min(ch, buf.numberOfChannels - 1));
      if (off >= 0) out.copyToChannel(d.subarray(off, off + n), ch); else out.copyToChannel(d.subarray(0, Math.max(0, n + off)), ch, -off);
    }
    return out;
  }
  async function finish(r, len) {
    const c = S.getCtx(), at = r.at + r.lag;
    if (!len) { // stopped before its first bar line: nothing taken
      for (const x of [r.wet, r.dry]) { x.media.stop(); try { x.node.disconnect(x.tap); } catch { /* gone */ } }
      return;
    }
    try {
      const [wet, dry] = await Promise.all([cut(c, r.wet, at, len), cut(c, r.dry, at, len)]);
      const d = new Date(), form = new FormData();
      form.append("name", `vocal-${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}-${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`);
      form.append("bpm", String(Math.round(60 / r.beat * 1000) / 1000));
      form.append("bars", String(Math.round(len / r.bar * 1000) / 1000));
      form.append("file", S.wavEncode(wet, 2, cap()), "vocal.wav");
      form.append("dry", S.wavEncode(dry, 2, cap()), "dry.wav");
      await S.send("/take/vocal", form, { refused: "The vocal take was refused.", target: $("vox-panel"), swap: $("vox-panel") ? "outerMorph" : "none" });
    } catch (err) { toast(String(err?.message || err), "error"); }
  }

  // ---- the controls ------------------------------------------------------------------
  document.addEventListener("input", (e) => {
    const el = e.target.closest?.("[data-vox]");
    if (!el) return;
    opts[el.dataset.vox] = Number(el.value);
    save(); set(); paint();
  });
  document.addEventListener("click", (e) => {
    const t = e.target instanceof Element ? e.target : null;
    if (t?.closest("#vox-rec")) return press();
    if (t?.closest("#vox-monitor")) { opts.monitor = !opts.monitor; save(); set(); return paint(); }
    if (t?.closest("#vox-count")) { opts.count = !opts.count; save(); return paint(); }
    if (t?.closest("#vox-reset")) { Object.assign(opts, INPUT); save(); set(); return paint(); } // (its post resets the chain)
    if (!t?.closest("#vox-btn")) return;
    if (opening) { opening.cancelled = true; return; } // a second click while the mic is asked for
    on ? stop() : start();
  });
  // the panel is drawn when its popover opens; a redrawn rack is re-read into the graph
  document.addEventListener("toggle", (e) => {
    if (e.target?.id === "vox-pop" && e.newState === "open") htmx.ajax("GET", "/fragment/vocal", { target: "#vox-panel", swap: "outerMorph" });
  }, true);
  document.addEventListener("htmx:after:settle", () => { if (on && $("vox-panel") && rackSig() !== on.sig) rebuild(); paint(); });
  document.addEventListener("DOMContentLoaded", paint);
  S.vocal = { on: () => !!on, stop, out: () => on?.out || null };
})();
