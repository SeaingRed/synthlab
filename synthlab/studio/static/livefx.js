// synthlab/studio/static/livefx.js
// The live key engine's graph: one voice-worklet node per
// lane a key sounds on (the edited slot, the main lane, the followers —
// the ▶ set, /patch.json's `heard` + `layers`), each through its own FX chain at its
// own gain, the sum through the master chain, into keys.js's key bus. A
// key sounds like the loop: every layer, every chain, as render.py mixes
// the stems (studio/fxlive.py classifies each unit; a chain holding an
// offline one makes /patch.json's worklet_ok false and keys play the dry
// WAV, as before).
// Chains: runs of JS units -> one fx-worklet.js node (the EQ's bands are
// eq-worklet.js's, a module in the same scope); reverb -> a
// ConvolverNode on effects.reverb_ir (GET /fx/ir.wav, at this context's
// rate) with dry/wet gains, plate -> one on effects.plate_ir and spring -> one
// on effects.spring_ir; lowpass/highpass -> a BiquadFilterNode (RBJ;
// Web Audio takes those Q in dB). A slider drag that keeps a chain's shape
// updates it in place; a new shape rebuilds it.
// piano.js talks to this through the voice facade `start()` returns: its
// noteOn/noteOff/bend/allOff reach every lane (noteOn/bend at the lane's
// follow octave), "mod" the edited slot.
// Pre-rendered lanes (live CPU): a lane /patch.json marks `mode: "buffer"`
// (every one but the edited slot, unless its chain is clocked or the dev
// flag asked for all-live) plays keys.js's server renders of its notes,
// FX baked in, into the master chain here — its voice node only stands in
// while no render of the key is at hand (`live_ok`) — so one lane runs in
// the worklet, not three.
// The CPU meter: every worklet node posts its own processing time
// ("load"); the sum, as a share of the render quanta's realtime budget,
// and the live voices show in #cpu-meter (Monitor, Perform's strip), with
// the voice engine the nodes run: "wasm" or "js".
// The WebAssembly engine: static/voice-dsp.wasm (synthlab/studio/wasm/) is
// compiled here, once — an AudioWorklet scope can't fetch — and the Module
// handed to every voice node in its processorOptions (a Module posted over
// the port never arrives in Chrome), which instantiates it. No WebAssembly, no
// SIMD (compile fails), CSP without 'wasm-unsafe-eval', or `?nowasm` on the
// page (the A/B flag): nothing is posted and the nodes run the JS voice.
// 130 -> 150: the pre-rendered lanes' routing and the CPU meter.
// 150 -> 160: the WebAssembly module's loading and the meter's engine.
// 160 -> 170: offline units pass through and a lane's chain failure stays its own
// (a "compress" taken for a biquad threw in setStack and silenced the keys).
// 170 -> 190: the live sampler's buffers (a lane's sampler_live URL, fetched
// and posted to its node; `ready` holds the keys on WAVs until they land).
// The node's sampler oscillator is sampler-worklet.js, a module added to
// the worklet scope beside voice-worklet.js (as eq-worklet.js is to the FX),
// and grain-worklet.js with it: the sampler's formant mode.
// 190 -> 192: the live vocal chain's units (vox-worklet.js, a
// third module in the FX scope) and `chain` handed out: vocal.js builds the
// mic's chain with it, the keys' own stages.
// 192 -> 193: the units of fx2-worklet.js (a
// fourth module in the FX scope) and the spring's ConvolverNode.
// The dynamics units (OTT, limiter, transient shaper) join
// the `JS` set, fx2-worklet.js's too — no new line.
// 193 -> 194: a lane's own wavetable rides the
// sampler's fetch (`sampler(l, url, "wtable")`) and holds `ready` the same way.
// Budget: <= 194 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const JS = new Set(["drive", "clip", "fold", "bitcrush", "ringmod", "comb", "width", "delay", "chorus",
    "flanger", "phaser", "gate", "pump", "sidechain", "eq", "doubler", "rotary", "tape_echo", "telephone", "pitch",
    "autotune", "harmony", "vox_comp", "deess", "vgate", // these five and pitch: vox-worklet.js, in the same scope
    "autofilter", "tremolo", "tape", "pingpong", "formant_shift", "freqshift", "exciter", "vocoder_fx", "ott", "limiter", "transient"]); // fx2-worklet.js
  let ctx = null, tables = null, masterIn = null, master = null, stack = [], wasm = null;
  const layers = []; // {node, gain, chain}
  const irs = new Map(); // IR url -> Promise<AudioBuffer>
  const smps = new Map(); // sampler buffers url -> Promise<{meta, data}>
  const bufd = new Map(); // note id -> {lanes, vel, hold} of its pre-rendered lanes
  const loads = new Map(); // worklet node -> {share, voices, at}
  let shown = 0, smooth = 0;

  // A worklet node's "load" report -> the meter: the nodes share one audio
  // thread, so their shares add; smoothed, redrawn at most 4x a second.
  function meter(node, d) {
    const now = performance.now(), el = () => document.getElementById("cpu-meter");
    loads.set(node, { share: d.ms / (d.quanta * 128 / ctx.sampleRate * 1000), voices: d.voices || 0, at: now,
      engine: d.engine });
    if (now - shown < 250) return;
    let share = 0, voices = 0;
    const engines = new Set();
    for (const [n, l] of loads) {
      if (now - l.at > 1000) loads.delete(n); else { share += l.share; voices += l.voices; if (l.engine) engines.add(l.engine); }
    }
    smooth += (share - smooth) * 0.5; shown = now;
    const m = el();
    if (!m) return;
    m.hidden = false;
    m.textContent = `CPU ${Math.round(smooth * 100)}% · ${voices} voice${voices === 1 ? "" : "s"}` +
      (engines.size ? ` · ${[...engines].sort().join("+")}` : "");
    m.classList.toggle("hot", smooth > 0.8);
  }
  // ...and a zone map's pick ("zone": the zones a key sounded) -> the page, as
  // a `synthlab:zone` event (zonemap.js lights them)
  const watch = (node) => { node.port.onmessage = (e) => (e.data?.type === "load" ? meter(node, e.data) : e.data?.type === "zone" && dispatchEvent(new CustomEvent("synthlab:zone", { detail: e.data }))); return node; };

  // The engine's fixed tables (/wavetables.bin; /patch.json names the
  // content-hashed URL). The header is split here: an AudioWorklet scope
  // has no TextDecoder. Each voice node gets its own copy.
  async function loadTables(url) {
    const buf = await (await fetch(url || "/wavetables.bin")).arrayBuffer();
    const n = new DataView(buf).getUint32(0, true);
    const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, n)));
    tables = { type: "tables", meta, data: new Float32Array(buf, 4 + n) };
  }

  // The voice DSP module, compiled on this thread (null: the JS voice)
  async function loadWasm() {
    if (!globalThis.WebAssembly || /[?&]nowasm\b/.test(globalThis.location?.search ?? "")) return;
    try { wasm = await WebAssembly.compile(await (await fetch("/static/voice-dsp.wasm")).arrayBuffer()); } catch { wasm = null; }
  }

  // A live sampler's buffers (GET /sampler/live/<id>.bin, sampler.live_payload;
  // header split as the tables' is), fetched once per URL (a failure is
  // retried next time), a copy posted to the lane's node once they land.
  // Until every lane's are in, `ready` is false: piano.js keeps WAV keys.
  // A lane's own wavetable (GET /wavetable/live/<id>.bin,
  // wavetables.live_payload) the same way, as a "wtable" message.
  function sampler(l, url, type = "sampler", k = "smp") {
    l[k] = url || null;
    if (!url || l[k + "At"] === url) return;
    if (!smps.has(url)) {
      smps.set(url, fetch(url).then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(r.status))).then((b) => {
        const n = new DataView(b).getUint32(0, true);
        return { meta: JSON.parse(new TextDecoder().decode(new Uint8Array(b, 4, n))), data: new Float32Array(b, 4 + n) };
      }).catch((e) => { smps.delete(url); throw e; }));
    }
    smps.get(url).then((d) => {
      if (l[k] !== url) return; // the lane moved on to another sound
      l.node.port.postMessage({ type, url, meta: d.meta, data: d.data.slice() });
      l[k + "At"] = url;
    }).catch(() => {});
  }
  const ready = () => layers.every((l) => (!l.smp || l.smpAt === l.smp) && (!l.wtb || l.wtbAt === l.wtb));

  function ir(url) {
    const full = `${url}&sr=${ctx.sampleRate}`;
    if (!irs.has(full)) irs.set(full, S.fetchBuffer(full).catch((e) => { irs.delete(full); throw e; })); // retried next time
    return irs.get(full);
  }

  // A chain's shape: which kinds, in order, grouped the way the graph is
  const shape = (units) => units.map((u) => (JS.has(u.kind) ? "js" : u.kind)).join(">");
  function runs(units) { // consecutive JS units share one worklet node
    const out = [];
    for (const u of units) {
      const last = out[out.length - 1];
      if (JS.has(u.kind) && last?.js) last.units.push(u);
      else out.push(JS.has(u.kind) ? { js: true, units: [u] } : { js: false, units: [u] });
    }
    return out;
  }
  const qdb = (q) => 20 * Math.log10(Math.max(q, 1e-4)); // RBJ Q -> Web Audio's dB

  // One stage of a chain: {inp, out, set(units)}
  function stage(run) {
    const u = run.units[0];
    if (run.js) {
      const node = watch(new AudioWorkletNode(ctx, "synthlab-fx", { outputChannelCount: [2], processorOptions: { units: run.units } }));
      node.port.postMessage({ type: "clock", t0: S.loopStart?.() ?? 0 });
      return { inp: node, out: node, fx: node, set: (r) => node.port.postMessage({ type: "units", units: r.units }) };
    }
    if (u.kind === "reverb" || u.kind === "plate" || u.kind === "spring") { // effects._reverb / _plate / _spring: st * (1 - mix) + conv(st, its ir) * mix
      const inp = ctx.createGain(), out = ctx.createGain(), dry = ctx.createGain(), wet = ctx.createGain();
      const conv = new ConvolverNode(ctx, { disableNormalization: true });
      inp.connect(dry).connect(out); inp.connect(conv).connect(wet).connect(out);
      let url = null;
      const set = (r) => {
        const v = r.units[0];
        dry.gain.setTargetAtTime(1 - v.mix, ctx.currentTime, 0.02);
        wet.gain.setTargetAtTime(v.mix, ctx.currentTime, 0.02);
        if (v.ir !== url) { url = v.ir; ir(url).then((b) => { if (url === v.ir) conv.buffer = b; }).catch(() => { if (url === v.ir) url = null; }); } // failed: asked again on the next set
      };
      set(run);
      return { inp, out, set };
    }
    if (u.kind !== "lowpass" && u.kind !== "highpass") { // an offline unit (compress, shimmer...):
      const g = ctx.createGain(); // never a biquad of kind "compress" — that threw and silenced the keys
      return { inp: g, out: g, set: () => {} };
    }
    const f = new BiquadFilterNode(ctx, { type: u.kind, frequency: u.cutoff, Q: qdb(u.q) });
    const set = (r) => {
      f.frequency.setTargetAtTime(r.units[0].cutoff, ctx.currentTime, 0.02);
      f.Q.setTargetAtTime(qdb(r.units[0].q), ctx.currentTime, 0.02);
    };
    return { inp: f, out: f, set };
  }

  // A replaced worklet node: its processor is told to stop (process() returns
  // false), or it ran on with its port open for as long as the page lived
  const drop = (n) => { if (n) { n.port.postMessage({ type: "dispose" }); try { n.disconnect(); } catch {} } };

  // A chain from `inp` to `out`, rebuilt only when its shape changes
  function chain(old, units, inp, out) {
    if (old && old.shape === shape(units)) {
      runs(units).forEach((r, i) => old.stages[i].set(r));
      return old;
    }
    if (old) { try { inp.disconnect(); } catch {} old.stages.forEach((s) => { try { s.out.disconnect(); } catch {} drop(s.fx); }); }
    const stages = runs(units).map(stage);
    let cur = inp;
    for (const s of stages) { cur.connect(s.inp); cur = s.out; }
    cur.connect(out);
    return { shape: shape(units), stages };
  }

  // Post the loop's start (a frame on this context's clock) to every clocked unit
  function clock() {
    const t0 = S.loopStart?.() ?? 0;
    for (const c of [master, ...layers.map((l) => l.chain)]) {
      for (const s of c?.stages || []) s.fx?.port.postMessage({ type: "clock", t0 });
    }
  }
  document.addEventListener("loop-clock", clock);

  // /patch.json -> the graph: a voice node per lane (params, gain, chain)
  // and the master chain. The lanes are the view's ▶ set for keys
  // (state.audible_layers): the edited slot first unless it is muted or
  // soloed out (`heard`), then the main lane and the followers — never a
  // plain slot playing its own notes. "mod" reaches the edited one.
  function setStack(p) {
    if (!ctx || !p) return;
    stack = [...(p.heard === false ? [] : [{ ...p, layers: undefined, edited: true }]), ...(p.layers || [])];
    while (layers.length > stack.length) {
      const l = layers.pop();
      drop(l.node); l.chain?.stages.forEach((s) => drop(s.fx));
      try { l.gain.disconnect(); } catch {}
    }
    stack.forEach((lp, i) => {
      let l = layers[i];
      if (!l) {
        // the Module rides processorOptions: Chrome drops one posted over the port
        // (messageerror); a browser that can't clone it at all gets a JS node
        const make = (o) => new AudioWorkletNode(ctx, "synthlab-voice", { outputChannelCount: [2], processorOptions: o });
        let node;
        try { node = make(wasm ? { wasm } : {}); } catch { node = make({}); }
        watch(node);
        node.port.postMessage({ ...tables, data: tables.data.slice() });
        const gain = ctx.createGain();
        node.connect(gain);
        l = layers[i] = { node, gain, chain: null };
      }
      l.node.port.postMessage({ type: "params", p: lp });
      sampler(l, lp.sampler_live);
      sampler(l, lp.wt_live, "wtable", "wtb");
      l.gain.gain.setTargetAtTime(lp.gain ?? 1, ctx.currentTime, 0.02);
      // a pre-rendered lane that can't stand in live never sounds through
      // this chain (its renders carry their FX), so don't build one
      const fx = lp.mode === "buffer" && !lp.live_ok ? [] : lp.fx || [];
      try { l.chain = chain(l.chain, fx, l.gain, masterIn); } catch (e) {
        console.warn("live FX chain failed; lane plays dry", e); // one lane, never the keyboard
        try { l.gain.disconnect(); } catch {}
        l.gain.connect(masterIn); l.chain = null;
      }
    });
    try { master = chain(master, p.master || [], masterIn, S.keyBus()); } catch (e) {
      console.warn("live master chain failed; keys play dry", e);
      try { masterIn.disconnect(); } catch {}
      masterIn.connect(S.keyBus()); master = null;
    }
  }

  // piano.js's voice: note messages reach every lane, each at its follow
  // octave (a key is the main lane's note: a follower at -1 plays it an
  // octave down, as the loop does) with its own patch (a noteOn's `p` is
  // the edited slot's); "mod" reaches the edited slot only. A "buffer"
  // lane plays the key's render instead (keys.js laneOn, at the key's own
  // pitch: the server applies the octave) when one is at hand or its voice
  // can't stand in; a bend re-strikes it at the new pitch.
  function post(m) {
    const midi = m.freq ? Math.round(69 + 12 * Math.log2(m.freq / 440)) : 0, hold = m.hold !== false;
    const only = m.freq ? S.rowLane?.(m.id) : null; // typing rows by sound (rowsounds.js): this note is one lane's
    const b = m.type === "noteOn" ? { vel: m.vel, hold, lanes: stack.filter((lp) => (!only || lp.slot === only) && lp.mode === "buffer" && !lp.edited &&
      (!lp.live_ok || S.hasLane?.(lp.slot, midi, S.holdOf(lp, hold)))) } : bufd.get(m.id);
    if (m.freq && b?.lanes.length) { bufd.set(m.id, b); S.laneOn(m.id, b.lanes, midi, b.vel, b.hold, masterIn); }
    if (m.type === "noteOff") { bufd.delete(m.id); S.laneOff?.(m.id); }
    if (m.type === "allOff") { bufd.clear(); S.laneIds?.().forEach(S.laneOff); }
    layers.forEach((l, i) => {
      const lp = stack[i], r = 2 ** (lp.octave || 0);
      if ((m.type === "mod" && !lp.edited) || (only && lp.slot !== only) || (m.freq && b?.lanes.includes(lp))) return;
      if (!m.freq) { l.node.port.postMessage(m); return; } // noteOff, allOff, mod
      const out = { ...m, freq: m.freq * r };
      if (m.fromFreq) out.fromFreq = m.fromFreq * r;
      if (m.type === "noteOn" && !lp.edited) out.p = lp;
      l.node.port.postMessage(out);
    });
  }

  // One start, however many keys land while the modules and tables load:
  // each call used to make its own masterIn, and chains already wired to
  // the first one never reached the key bus again. A failed start is not
  // kept, so the next key tries again.
  let starting = null;
  const start = (p) => (starting ??= (async () => {
    ctx = S.getCtx();
    await Promise.all([ctx.audioWorklet.addModule("/static/voice-worklet.js"), ctx.audioWorklet.addModule("/static/sampler-worklet.js"), ctx.audioWorklet.addModule("/static/grain-worklet.js"),
      ctx.audioWorklet.addModule("/static/fx-worklet.js"), ctx.audioWorklet.addModule("/static/eq-worklet.js"),
      ctx.audioWorklet.addModule("/static/vox-worklet.js"), ctx.audioWorklet.addModule("/static/fx2-worklet.js"), loadTables(p?.tables), loadWasm()]);
    masterIn ??= ctx.createGain();
    setStack(p);
    return { port: { postMessage: post } };
  })().catch((e) => { starting = null; throw e; }));

  Object.assign(S, { live: { start, setStack, shape, runs, qdb, ready, chain } });
})();
