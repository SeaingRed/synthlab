// synthlab/studio/static/talkbox.js
// The live talkbox: #talk-btn "🎙 talk" in the keyboard dock's
// Monitor, and its ⚙ panel (#talk-pop). On, the mic is the voice and the
// keys are the carrier — sing or talk while you play and the keys speak
// (talk-worklet.js, a channel vocoder). The offline vocoder (the Voice lab)
// stays the one to bake a take; this one is heard live and is never in a
// render, a bounce or an export (it is the Monitor's: how you hear it) —
// except what ⏺ records here, which goes to the clip library as audio.
//   - the key bus reaches the vocoder through audio.js's setLiveOut: the
//     node the keys went to (the master, or space.js's room) is kept and
//     the vocoder's output goes there, so the room still wraps it; off, it
//     is given back. When something else takes the live out while it is on
//     (space.js builds its room late), the vocoder takes it again.
//   - the mic is opened with echo cancelling, noise suppression and gain
//     control off (they smear a voice into a vocoder) — wear headphones,
//     or the keys feed back into the mic; the first switch-on says so. A
//     click while the mic is being asked for cancels; off releases the
//     processor (its process() ends) and stops the mic's tracks.
//   - the panel: a character (Talkbox, Robot — its own saw at a note, so it
//     talks with no keys — Whisper, a noise carrier, Choir: more bands, the
//     formants down, a slow release, a little dry), then bands, formant
//     shift, consonants (air), dry keys, release and level. Settings are
//     this browser's (localStorage) and reach a running vocoder at once.
//   - the meter: the mic's level, on the switch (--talk-in).
//   - ⏺ (#talk-rec): what the vocoder plays, recorded into the clip library
//     (/clip/upload, "talk-<time>", source "talkbox"), capped at MAX_CLIP_SECONDS.
//     ⏺ → booth (#talk-rec-booth) records the same way, then makes the kept
//     clip the Voice page's take (/voice/take/clip, booth=1), ready to treat.
//   - ↺ Defaults (#talk-reset): every setting back to the Talkbox character's.
// Budget: <= 140 code lines (130 -> 140: ↺ Defaults, the take's source).

(() => {
  "use strict";
  const S = window.synthlab ??= {};
  if (typeof document === "undefined") return;
  const $ = (id) => document.getElementById(id);
  const toast = (m, kind) => S.toast?.(m, kind);
  // every character names every setting (the robot's note too), so picking one sets the whole panel
  // the mic's pick-up — gain dB, auto level, gate dB (talk-worklet.js) — is
  // each character's too: the whisper is boosted, levelled and gated
  const MIC = { gain: 0, auto: 0, gate: -90 };
  const CHARACTERS = {
    talkbox: { bands: 16, shift: 0, air: 0.3, dry: 0, release: 0.04, level: 1, carrier: "keys", note: 48, ...MIC },
    robot: { bands: 20, shift: 0, air: 0.15, dry: 0, release: 0.03, level: 1, carrier: "robot", note: 48, ...MIC },
    whisper: { bands: 16, shift: 0, air: 0.6, dry: 0, release: 0.05, level: 1.2, carrier: "noise", note: 48, gain: 12, auto: 0.7, gate: -66 },
    choir: { bands: 24, shift: -3, air: 0.2, dry: 0.15, release: 0.12, level: 1, carrier: "keys", note: 48, ...MIC },
  };
  const DEFAULTS = { character: "talkbox", ...CHARACTERS.talkbox }; // ↺ Defaults puts these back
  let opts = { ...DEFAULTS };
  try { opts = { ...opts, ...JSON.parse(localStorage.getItem("synthlab.talk") || "{}") }; } catch {}
  let on = null; // {node, input, source, stream, prev}: the running talkbox
  let opening = null; // the start in flight: {cancelled}; a click meanwhile cancels it
  let loaded = null; // the worklet module, once a context
  let rec = null; // {L: [], R: [], n}: the recording in flight
  const paint = () => {
    $("talk-btn")?.setAttribute("aria-pressed", String(!!on));
    $("talk-rec")?.setAttribute("aria-pressed", String(!!rec && !rec.booth));
    $("talk-rec-booth")?.setAttribute("aria-pressed", String(!!rec?.booth));
    document.querySelectorAll("[data-talk]").forEach((el) => {
      if (opts[el.dataset.talk] === undefined) return;
      el.value = String(opts[el.dataset.talk]);
      if (el.type === "range") el.style.setProperty("--fill", `${((el.value - el.min) / (el.max - el.min || 1)) * 100}%`);
    });
    document.querySelectorAll("[data-talk-character]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.talkCharacter === opts.character)));
  };
  function save() {
    try { localStorage.setItem("synthlab.talk", JSON.stringify(opts)); } catch {}
    on?.node.port.postMessage(opts);
  }

  async function start() {
    S.ensureCtx?.();
    const c = S.getCtx?.();
    if (!c) return;
    const me = (opening = { cancelled: false });
    $("talk-btn")?.setAttribute("aria-busy", "true");
    let stream;
    try {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      } catch { return toast("The talkbox needs the microphone: allow it for this page.", "error"); }
      try {
        loaded ??= c.audioWorklet.addModule("/static/talk-worklet.js");
        await loaded;
      } catch (err) { stream.getTracks().forEach((t) => t.stop()); return toast(`The talkbox did not load: ${err?.message || err}`, "error"); }
      if (me.cancelled) return stream.getTracks().forEach((t) => t.stop()); // clicked off while asking
    } finally { if (opening === me) opening = null; $("talk-btn")?.removeAttribute("aria-busy"); }
    const node = new AudioWorkletNode(c, "talkbox", { numberOfInputs: 2, numberOfOutputs: 1, outputChannelCount: [2], processorOptions: opts });
    const input = c.createGain(), prev = S.liveOut(), source = c.createMediaStreamSource(stream);
    input.connect(node, 0, 0);
    source.connect(node, 0, 1);
    node.connect(prev);
    node.port.onmessage = (e) => heard(e.data);
    on = { node, input, source, stream, prev };
    take();
    try { if (!localStorage.getItem("synthlab.talkHint")) { localStorage.setItem("synthlab.talkHint", "1"); toast("Talkbox on: play the keys and talk or sing into the mic — wear headphones."); } } catch {}
    paint();
  }
  // the key bus into the vocoder (and again when something else took it)
  function take() {
    if (!on || S.liveOut() === on.input) return;
    const now = S.liveOut();
    if (now !== on.prev) { on.node.disconnect(); on.node.connect(now); on.prev = now; }
    S.setLiveOut(on.input);
    document.dispatchEvent(new Event("live-out-changed"));
  }
  function stop() {
    if (rec) finish();
    const t = on;
    on = null;
    if (!t) return paint();
    S.setLiveOut(t.prev);
    document.dispatchEvent(new Event("live-out-changed"));
    t.stream.getTracks().forEach((x) => x.stop());
    t.node.port.postMessage({ stop: true }); // its process() returns false: released
    try { t.source.disconnect(); t.node.disconnect(); t.input.disconnect(); } catch {}
    $("talk-btn")?.style.removeProperty("--talk-in");
    paint();
  }

  // ---- the processor's reports: the mic's level, the recorded blocks ----------
  const cap = () => Number($("talk-rec")?.dataset.maxClipSeconds) || 30;
  function heard(d) {
    if (d?.level !== undefined) $("talk-btn")?.style.setProperty("--talk-in", Math.min(1, d.level * 4).toFixed(2));
    if (!rec || !d?.L) return;
    rec.L.push(d.L); rec.R.push(d.R); rec.n += d.L.length;
    if (rec.n >= cap() * S.getCtx().sampleRate) { toast(`The talk take reached the ${cap()} s cap — stopped and kept.`); finish(); }
  }
  async function finish() {
    const r = rec;
    rec = null;
    on?.node.port.postMessage({ rec: false });
    paint();
    if (!r || !r.n) return;
    const c = S.getCtx(), buf = c.createBuffer(2, r.n, c.sampleRate);
    let at = 0;
    r.L.forEach((block, i) => { buf.copyToChannel(block, 0, at); buf.copyToChannel(r.R[i], 1, at); at += block.length; });
    const form = new FormData(), stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "");
    form.append("name", `talk-${stamp}`);
    form.append("source", "talkbox");
    form.append("file", S.wavEncode(buf, 2, cap()), "talk.wav");
    try {
      const res = await S.send("/clip/upload", form, { refused: "The talk take was refused." }), name = res?.headers.get("X-Clip-Name");
      if (!res || !r.booth || !name) return res && toast("Kept the talk take in Clips.", "done");
      // "⏺ → booth": kept, then the Voice take (the booth's "Use as take", pointed at the take)
      const panel = $("voice-panel");
      htmx.ajax("POST", "/voice/take/clip", { values: { name, booth: "1" }, target: panel || document.body, swap: panel ? "outerHTML" : "none" });
    } catch (err) { toast(String(err?.message || err), "error"); }
  }

  // ---- the controls -------------------------------------------------------------
  document.addEventListener("input", (e) => {
    const el = e.target.closest?.("[data-talk]");
    if (!el) return;
    opts[el.dataset.talk] = el.dataset.talk === "carrier" ? el.value : Number(el.value);
    opts.character = "custom";
    save(); paint();
  });
  document.addEventListener("click", (e) => {
    const ch = e.target.closest?.("[data-talk-character]");
    if (ch) { opts = { ...opts, ...CHARACTERS[ch.dataset.talkCharacter], character: ch.dataset.talkCharacter }; save(); return paint(); }
    if (e.target.closest?.("#talk-reset")) { opts = { ...DEFAULTS }; save(); return paint(); }
    if (e.target.closest?.("#talk-rec, #talk-rec-booth")) {
      if (rec) return finish();
      if (!on) return toast("Turn the talkbox on (🎙 talk) to record what it says.", "error");
      rec = { L: [], R: [], n: 0, booth: !!e.target.closest("#talk-rec-booth") };
      on.node.port.postMessage({ rec: true });
      return paint();
    }
    if (!e.target.closest?.("#talk-btn")) return;
    if (opening) { opening.cancelled = true; return; } // a second click while the mic is asked for: cancel
    on ? stop() : start();
  });
  document.addEventListener("htmx:after:settle", paint);
  document.addEventListener("DOMContentLoaded", paint);
  // out(): the vocoder's output while it speaks for the keys (keyrec.js records it)
  S.talkbox = { on: () => !!on, stop, recording: () => !!rec, options: () => ({ ...opts }), out: () => on?.node || null };
})();
