// synthlab/studio/static/voice.js
// Voice-lab island (7th; the six-island rule predates a view that needs
// microphone + codec work, and none of this belongs in audio.js). Owns:
// opt-in mic capture, decoding whatever the user supplies, and encoding a
// 16-bit WAV for upload — its own takes, audio.js's jam (wavEncode) and
// songrec.js's Song takes (window.synthlab.voice).
//
// The browser decodes, never the server: MediaRecorder emits webm/opus and
// a dragged file can be anything, but decodeAudioData handles all of it and
// the Python side then only ever sees PCM WAV.
//
// Mic is opt-in like midi.js: nothing calls getUserMedia until Record is
// pressed, and every track is stopped the instant recording ends so the
// browser's recording indicator goes out.
// Budget: <= 120 code lines.

(() => {
  "use strict";
  const MAX_SECONDS = 30;
  let rec = null, stream = null, chunks = [];

  const panel = () => document.getElementById("voice-panel");
  const btn = () => document.getElementById("mic-btn");
  const status = (msg) => {
    const el = document.getElementById("mic-status");
    if (el) el.textContent = msg || "";
  };

  // AudioBuffer -> 16-bit PCM WAV, cut at maxSec: a mono mixdown at
  // MAX_SECONDS for a Voice-lab take; audio.js's jam keeps up to stereo and
  // songrec.js's Song take is mono, both cut at the server's clip cap
  // (limits.MAX_CLIP_SECONDS, read off the button's data-max-clip-seconds).
  // Written by hand because this is the one format the server is willing to
  // parse; the one encoder all three uploads share.
  function toWav(buf, chans = 1, maxSec = MAX_SECONDS) {
    const n = Math.min(buf.length, Math.floor(buf.sampleRate * maxSec));
    const src = buf.numberOfChannels, ch = Math.min(chans, src), sr = buf.sampleRate;
    const mix = Array.from({ length: ch }, () => new Float32Array(n));
    for (let c = 0; c < src; c++) {
      const d = buf.getChannelData(c), m = mix[ch === 1 ? 0 : c], g = ch === 1 ? 1 / src : 1;
      if (m) for (let i = 0; i < n; i++) m[i] += d[i] * g;
    }
    const bytes = new ArrayBuffer(44 + n * ch * 2), view = new DataView(bytes);
    const str = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
    str(0, "RIFF"); view.setUint32(4, 36 + n * ch * 2, true); str(8, "WAVEfmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, ch, true); view.setUint32(24, sr, true);
    view.setUint32(28, sr * ch * 2, true); view.setUint16(32, ch * 2, true);
    view.setUint16(34, 16, true); str(36, "data"); view.setUint32(40, n * ch * 2, true);
    for (let i = 0; i < n; i++) {
      for (let c = 0; c < ch; c++) view.setInt16(44 + (i * ch + c) * 2, Math.max(-1, Math.min(1, mix[c][i])) * 32767, true);
    }
    return new Blob([bytes], { type: "audio/wav" });
  }
  window.synthlab ??= {};
  window.synthlab.wavEncode = toWav;

  // Mic capture for the Song page's takes (songrec.js): the same opt-in
  // MediaRecorder path, with the browser's voice processing off (a take is
  // music). start() resolves once recording runs, with the ctx time it
  // began; stop() releases the mic and resolves the decoded AudioBuffer.
  async function capture(ctx) {
    const st = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
    const r = new MediaRecorder(st), parts = [], input = st.getAudioTracks()[0]?.getSettings?.().latency || 0;
    r.ondataavailable = (e) => e.data.size && parts.push(e.data);
    const began = await new Promise((ok) => { r.onstart = () => ok(ctx.currentTime); r.start(); });
    const stop = () => new Promise((ok, no) => {
      r.onstop = async () => { st.getTracks().forEach((t) => t.stop()); try { ok(await ctx.decodeAudioData(await new Blob(parts).arrayBuffer())); } catch (err) { no(err); } };
      if (r.state === "recording") r.stop(); else r.onstop();
    });
    return { began, input, stop };
  }
  window.synthlab.voice = { capture, toWav };

  async function upload(blob) {
    status("uploading…");
    // the vocoder's form rides along: the panel that comes back keeps it
    const body = new FormData(document.getElementById("voice-render") || undefined);
    body.append("file", blob, "take.wav");
    // swapped through htmx (synthlab.send), never a bare outerHTML: that left the
    // new panel unprocessed, so "Vocode it" submitted natively and "keep take" was dead
    const p = panel();
    await window.synthlab.send("/voice/take", body, { refused: "Take rejected.", target: p, swap: p ? "outerHTML" : "none" });
    status("");
  }

  async function decodeAndUpload(arrayBuffer) {
    try {
      const ctx = window.synthlab.getCtx() || new AudioContext();
      await upload(toWav(await ctx.decodeAudioData(arrayBuffer)));
    } catch {
      window.synthlab.toast("Could not read that audio.", "error");
      status("");
    }
  }

  async function startRec() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      window.synthlab.toast("Microphone permission denied.", "error");
      return;
    }
    chunks = [];
    rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      // release the mic immediately: the indicator must not outlive the take
      stream.getTracks().forEach((t) => t.stop());
      stream = null; rec = null;
      btn()?.setAttribute("aria-pressed", "false");
      if (btn()) btn().textContent = "● Record";
      if (chunks.length) await decodeAndUpload(await new Blob(chunks).arrayBuffer());
    };
    rec.start();
    btn()?.setAttribute("aria-pressed", "true");
    if (btn()) btn().textContent = "■ Stop";
    status("recording…");
    // this take's own stop: an earlier take's timer used to cut the next one short
    const r = rec;
    setTimeout(() => { if (rec === r && r.state === "recording") r.stop(); }, MAX_SECONDS * 1000);
  }

  document.addEventListener("click", (e) => {
    if (!e.target.closest("#mic-btn")) return;
    if (rec && rec.state === "recording") rec.stop();
    else startRec();
  });
  document.addEventListener("change", async (e) => {
    const input = e.target.closest("#voice-file");
    const file = input?.files?.[0];
    if (!file) return;
    status("reading…");
    await decodeAndUpload(await file.arrayBuffer());
    input.value = "";
  });
  // leaving the view mid-record must not leave the microphone live
  document.addEventListener("htmx:after:swap", () => {
    if (rec && rec.state === "recording") rec.stop();
  });
})();
