// synthlab/studio/static/space.js
// SPACE FX chain on the live note path: liveBus -> dry + ping-pong delay +
// convolution reverb -> master. Builds lazily on first user gesture (needs
// an AudioContext, which only exists after one). Budget: <= 150 code lines.

(() => {
  "use strict";
  let built = false;
  let macro = Number(localStorage.getItem("synthlab.space")) || 0;
  let liveBus, dry, delayL, delayR, feedbackL, feedbackR, wetDelay, wetRev;

  function makeIR(ctx, seconds = 3.5) {
    const len = Math.round(seconds * ctx.sampleRate);
    const buf = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 2.8);
    }
    return buf;
  }

  function applyMacro(m) {
    if (!built) return;
    const ctx = window.synthlab.getCtx();
    const now = ctx.currentTime, tau = 0.05;
    dry.gain.setTargetAtTime(1 - 0.3 * m, now, tau);
    wetRev.gain.setTargetAtTime(Math.pow(m, 1.5) * 0.8, now, tau);
    wetDelay.gain.setTargetAtTime(Math.pow(m, 1.3) * 0.5, now, tau);
    feedbackL.gain.setTargetAtTime(0.25 + 0.4 * m, now, tau);
    feedbackR.gain.setTargetAtTime(0.25 + 0.4 * m, now, tau);
  }

  function build() {
    if (built || !window.synthlab.getCtx()) return;
    built = true;
    const ctx = window.synthlab.getCtx();
    const master = window.synthlab.getMaster();

    liveBus = ctx.createGain();
    dry = ctx.createGain();
    // Final summing stage: dry + both wet paths can exceed unity on a
    // 5-voice chord at macro=1 (0.7 dry + 0.8 rev + delay steady-state);
    // a gentle limiter keeps that from hitting the destination hard-clip.
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -12; comp.knee.value = 24; comp.ratio.value = 4;
    comp.attack.value = 0.003; comp.release.value = 0.25;
    comp.connect(master);
    liveBus.connect(dry).connect(comp);

    // Ping-pong delay: cross-feeding delay pair, panned hard L/R.
    delayL = ctx.createDelay(1); delayL.delayTime.value = 0.28;
    delayR = ctx.createDelay(1); delayR.delayTime.value = 0.42;
    feedbackL = ctx.createGain();
    feedbackR = ctx.createGain();
    feedbackL.gain.value = feedbackR.gain.value = 0.25 + 0.4 * macro;
    const pannerL = ctx.createStereoPanner(); pannerL.pan.value = -0.8;
    const pannerR = ctx.createStereoPanner(); pannerR.pan.value = 0.8;
    wetDelay = ctx.createGain();

    liveBus.connect(delayL);
    delayL.connect(feedbackL).connect(delayR);
    delayR.connect(feedbackR).connect(delayL);
    delayL.connect(pannerL).connect(wetDelay);
    delayR.connect(pannerR).connect(wetDelay);
    wetDelay.connect(comp);

    // Convolution reverb: generated stereo IR, decorrelated channels.
    const preDelay = ctx.createDelay(1); preDelay.delayTime.value = 0.02;
    const convolver = ctx.createConvolver();
    convolver.normalize = true;
    convolver.buffer = makeIR(ctx);
    wetRev = ctx.createGain();
    liveBus.connect(preDelay).connect(convolver).connect(wetRev).connect(comp);

    window.synthlab.setLiveOut(liveBus);
    document.dispatchEvent(new Event("live-out-changed"));
    applyMacro(macro);
  }

  function sync() {
    const el = document.getElementById("space");
    if (!el) return;
    el.value = macro;
    el.style.setProperty("--fill", `${macro * 100}%`);
  }

  function tryBuild() { build(); sync(); }
  // The first touch is the gesture that unlocks audio on iOS: create the
  // context right here so the pad/key that follows can play immediately.
  document.addEventListener("pointerdown", () => { window.synthlab.ensureCtx(); tryBuild(); });
  document.addEventListener("keydown", tryBuild);
  document.addEventListener("htmx:after:swap", sync);
  document.addEventListener("DOMContentLoaded", sync);

  document.addEventListener("input", (e) => {
    if (!e.target || e.target.id !== "space") return;
    macro = Number(e.target.value);
    localStorage.setItem("synthlab.space", String(macro));
    applyMacro(macro);
  });

  window.synthlab.space = { build, applyMacro, get macro() { return macro; } };
})();
