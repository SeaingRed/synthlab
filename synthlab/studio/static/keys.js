// synthlab/studio/static/keys.js
// The key-voice island: WAV-fallback keys (the sounds the AudioWorklet
// can't play) and one-shot pads/auditions. Owns the key-buffer store and
// its warm-up, the key bus (1/sqrt(voices) polyphony gain + a limiter — the
// one gain stage the worklet voice shares), hold-mode looping (at the
// server's X-Loop points), key release, and the swaps below.
// Its own island, apart from audio.js (which sits at its 745-line
// budget): audio.js keeps the AudioContext, master chain and transport and
// exposes them on window.synthlab; this file reads them live.
// A key never waits on a render it can stand in for: every
// patch change re-warms all visible keys (piano.js passes them, QWERTY
// octave first) while the old buffers keep playing; a press on a key with
// no current render plays its last render, or the nearest warm key within
// +-2 semitones repitched by playbackRate, and crossfades (XF) to the exact
// render when it lands. A held note does the same on every patch change,
// so every parameter reaches a held WAV key. The choice (pickKey) and the
// swap position (swapOffset) are pure and run under node by
// tests/studio/test_key_swap.py.
// 160 -> 260: the buffer store, warm-up, stand-ins and swaps above.
// Pre-rendered lanes (live CPU): the same store, warm-up, stand-ins and
// swaps serve every lane of a key's stack but the edited slot — /note
// ?layer=N renders one lane with its FX (studio/fxlive.py); livefx.js
// plays them (laneOn/laneOff) into its live master chain. Entries are
// keyed per lane ("<slot>:midi|hold"), each lane with its own current key
// (/patch.json's layers[].note_key), so an edit to one lane re-warms and
// crossfades only that lane.
// 260 -> 268: the pitch bend (bendKeys): the wheel repitches every sounding
// WAV key and pre-rendered lane by playbackRate — the worklet voice bends
// itself (a "wheel" message); a buffer can only be sped up, so a bent WAV
// key moves its formants and filter with it, and its tail runs short.
// 268 -> 275: bendStep, the arrow-key bend's glide (piano.js drives it),
// pure so node can run it.
// 275 -> 281: a warm-up never outlives its patch or its keyboard (a stale
// queue asked a view for lanes it does not pre-render, and ran on into Song).
// 281 -> 282 (the transport): an audition over 1.5 s (a clip, a sound) is
// reported to transport.js, so the dock names it and its ■ stops it.
// 282 -> 285 (keys takes): keyTap, the key bus after its limiter and
// before the live out (the room, the talkbox): what ⏺ Rec records.
// Budget: <= 290 code lines (+5: an audition is one at a time, `solo`).

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const ctx = () => S.getCtx(); // audio.js owns the context (ensureCtx)
  const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const midiOf = (pitch) => {
    const m = /^([A-G]#?)(-?\d+)$/.exec(pitch);
    return m ? 12 * (Number(m[2]) + 1) + NAMES.indexOf(m[1]) : null;
  };
  const nameOf = (midi) => `${NAMES[midi % 12]}${Math.floor(midi / 12) - 1}`;
  const XF = 0.04; // seconds: a stand-in or a held loop swapping to a new render
  const NEAR = 2; // semitones a neighbour may be repitched to stand in

  // ---- buffer store: the newest render per key, whatever patch it was ----
  // "midi|hold" -> {buffer, pk (X-Patch-Key), gen (patch change it was asked
  // for)}. Old entries are never flushed: they stand in until replaced.
  const store = new Map(), pending = new Map();
  let currentPk = null, gen = 0, warmToken = 0, warmTimer = null, noteView = "";
  const lanePk = new Map(); // pre-rendered lane slot -> its current note_key
  const kOf = (midi, hold, lane) => `${lane ? `${lane}:` : ""}${midi}|${hold ? 1 : 0}`;
  const pkOf = (lane) => (lane ? lanePk.get(lane) : currentPk);
  const live = new Set(); // sounding key handles, for swaps
  let bend = 1; // the wheel, as a playback rate (bendKeys)

  // Pure: the buffer to play for `midi` now. Exact key first (current or
  // not), else the nearest neighbour within NEAR, current renders before
  // stale ones. {entry, rate, exact} or null.
  function pickKey(entries, midi, hold, pk, lane = "") {
    const own = entries.get(kOf(midi, hold, lane));
    if (own) return { entry: own, rate: 1, exact: own.pk === pk };
    let best = null;
    for (let d = 1; d <= NEAR; d++) {
      for (const m of [midi - d, midi + d]) {
        const e = entries.get(kOf(m, hold, lane));
        if (e && (!best || (e.pk === pk && best.entry.pk !== pk))) best = { entry: e, rate: Math.pow(2, (midi - m) / 12), exact: false };
      }
    }
    return best;
  }
  // Pure: where in a new render a sounding note continues, `t` seconds
  // after it started. Inside the hold loop once past its start; -1 when a
  // one-shot has already run out.
  function swapOffset(t, loop, duration) {
    if (loop && t > loop[0]) {
      const len = loop[1] - loop[0];
      return len > 0 ? loop[0] + ((t - loop[0]) % len) : loop[0];
    }
    return t < duration - 0.005 ? t : -1;
  }

  // X-Loop (hold renders only) = "start,end,gate" in seconds. The server
  // picks the loop (whole periods of the sustain) and bakes its crossfade
  // into the WAV (holdloop.py), so nothing here copies or edits the buffer.
  async function fetchKey(midi, hold, lane) {
    // `view`: the key sounds on that view's ▶ set of layers (/patch.json's);
    // `layer`: just that pre-rendered lane of it, FX baked in
    const q = new URLSearchParams({ ...(hold && { hold: 1 }), ...(noteView && { view: noteView }), ...(lane && { layer: lane }) });
    const g = gen, res = await fetch(`/note/${encodeURIComponent(nameOf(midi))}.wav${q.size ? `?${q}` : ""}`);
    if (!res.ok) throw new Error(`key fetch ${res.status}`);
    const buffer = await ctx().decodeAudioData(await res.arrayBuffer());
    const loop = res.headers.get("X-Loop");
    // loop points land on the decoded buffer's own sample grid: the browser
    // resamples the 44.1 kHz WAV, and a fractional loop point is a seam
    const snap = (t) => Math.round(t * buffer.sampleRate) / buffer.sampleRate;
    if (loop) buffer.holdLoop = loop.split(",").map(Number).map(snap);
    return { buffer, pk: res.headers.get("X-Patch-Key"), gen: g, lane };
  }
  function land(key, e) {
    const old = store.get(key);
    if (old && old.gen > e.gen) return old; // a newer render already landed
    store.set(key, e);
    for (const h of live) if (h.key === key && !h.released && (h.rate !== 1 || e.pk !== h.entry.pk)) swap(h, e);
    if (!e.lane && e.pk === currentPk) resetBridge(e.gen);
    return e;
  }
  // The current render of a key: a promise, shared by every caller.
  function ensure(midi, hold, lane = "") {
    const key = kOf(midi, hold, lane), have = store.get(key);
    if (have && have.pk === pkOf(lane)) return Promise.resolve(have);
    const p = pending.get(key);
    if (p && p.gen === gen) return p.promise;
    const promise = fetchKey(midi, hold, lane).then((e) => land(key, e))
      .finally(() => { if (pending.get(key)?.promise === promise) pending.delete(key); });
    pending.set(key, { gen, promise });
    return promise;
  }

  // ---- the key bus: WAV keys and the worklet voice (piano.js) ----------
  let keyFilter = null, keyGain = null, keyLimit = null, keyTapNode = null, tapOut = null, oneShotLimit = null;
  // Every voice is full-scale on its own, so chords sum past 1.0; the master
  // limiter let 3 voices through at 1.124. Limiting per bus keeps 6 at 0.993.
  // -4 not -9: measured through the full chain (bus -> master 0.8 -> master
  // limiter), -9 cost a single note 23% of its level for no extra headroom.
  const limiter = () => new DynamicsCompressorNode(ctx(),
    { threshold: -4, knee: 0, ratio: 20, attack: 0.003, release: 0.25 });
  // keyPoly scales the bus by voices^-POLY under a fixed headroom, so the
  // limiter only catches peaks instead of doing the mixing (raw sums pumped
  // audibly). ONE level, loop or not, so a sound never drops in volume
  // because others play: two levels (0.7 alone, 0.32 under the loop) would
  // drop the keys 6.8 dB the moment the loop starts, a held note too; the
  // master's limiter takes the rare
  // peak of keys over a loud loop. POLY 0.25 (was 0.5, 1/sqrt): a note
  // joining dips the held ones 1.5 dB at two, 3 at four (was 3 and 6).
  const KEY_HEADROOM = 0.45, POLY = 0.25;
  let keyPoly = null, keyVoices = 0;
  function setPoly() {
    if (!keyPoly) return;
    const g = KEY_HEADROOM / Math.max(1, keyVoices) ** POLY;
    keyPoly.gain.setTargetAtTime(g, ctx().currentTime, 0.03); // ramped: no click
  }
  function keyPath() {
    if (!keyFilter) {
      keyFilter = new BiquadFilterNode(ctx(), { type: "lowpass", frequency: 20000, Q: 0.5 });
      keyGain = ctx().createGain();
      keyPoly = ctx().createGain();
      keyPoly.gain.value = KEY_HEADROOM;
      keyLimit = limiter();
      keyTapNode = ctx().createGain(); // the keys alone: keyrec.js records from here
      keyFilter.connect(keyGain).connect(keyPoly).connect(keyLimit).connect(keyTapNode);
      keyTapNode.connect((tapOut = S.liveOut()));
    }
    return keyFilter;
  }
  // only the live out moves: a take recording from the tap keeps it
  document.addEventListener("live-out-changed", () => {
    if (!keyTapNode) return;
    try { keyTapNode.disconnect(tapOut); } catch { /* not connected */ }
    keyTapNode.connect((tapOut = S.liveOut()));
  });
  // The worklet voice joins the bus after the WAV-only live filter: it
  // plays at the engine's level, as the WAVs do, so one gain stage serves
  // both. countVoice: its notes count toward the polyphony gain too.
  function keyBus() { keyPath(); return keyPoly; }
  function keyTap() { keyPath(); return keyTapNode; }
  function countVoice(d) { keyVoices = Math.max(0, keyVoices + d); setPoly(); }

  // A drag reaches sounding WAV keys at once through this filter/gain, as a
  // correction relative to the render they play (`bridge`: the cutoff and
  // level it was made with); the re-render then lands and the bridge resets.
  let bridge = null, bridgeGen = -1;
  function liveMod(param, value) {
    if (!keyFilter || !bridge) return;
    const t = ctx().currentTime;
    if (param === "cutoff") keyFilter.frequency.setTargetAtTime(value < bridge.cutoff ? Math.max(20, value) : 20000, t, 0.02);
    else if (param === "osc1_level") keyGain.gain.setTargetAtTime(Math.max(0, value / (bridge.osc1_level || 1)), t, 0.02);
  }
  const gens = new Map(); // gen -> {cutoff, osc1_level} of that patch
  function resetBridge(g) {
    if (g <= bridgeGen) return;
    bridgeGen = g; bridge = gens.get(g) || bridge;
    if (!keyFilter) return;
    const t = ctx().currentTime;
    keyFilter.frequency.setTargetAtTime(20000, t, 0.01);
    keyGain.gain.setTargetAtTime(1, t, 0.01);
  }

  function source(buffer, rate, hold) {
    const src = ctx().createBufferSource(), x = ctx().createGain();
    src.buffer = buffer;
    src.playbackRate.value = rate * bend;
    const loop = hold && buffer.holdLoop;
    if (loop) { src.loop = true; src.loopStart = loop[0]; src.loopEnd = loop[1]; }
    src.connect(x);
    return { src, x };
  }
  function started(h, src) {
    src.onended = () => { if (h.src === src && !h.done) { h.done = true; live.delete(h); if (h.count) { keyVoices--; setPoly(); } } };
  }
  // Crossfade a sounding key to `e` at the same point of the note.
  function swap(h, e) {
    const c = ctx(), now = c.currentTime;
    const off = swapOffset(now - h.t0, h.hold && e.buffer.holdLoop, e.buffer.duration);
    if (off < 0) return;
    const { src, x } = source(e.buffer, 1, h.hold);
    x.gain.setValueAtTime(0, now);
    x.gain.linearRampToValueAtTime(1, now + XF);
    x.connect(h.g);
    h.x.gain.cancelScheduledValues(now);
    h.x.gain.setValueAtTime(h.x.gain.value, now);
    h.x.gain.linearRampToValueAtTime(0, now + XF);
    h.src.stop(now + XF + 0.01);
    Object.assign(h, { src, x, entry: e, rate: 1 });
    started(h, src);
    src.start(now, off);
  }

  // hold = {curve}: loop the rendered note's sustain at the server's X-Loop
  // points until releaseKey() lets it go. Without it: a one-shot.
  // o = {lane, out, count}: a pre-rendered lane's key (livefx.js), into
  // `out` (its master chain), not counted by the polyphony gain (the key's
  // worklet note already is).
  async function playKey(pitch, velocity = 1, hold = null, o = {}) {
    S.ensureCtx();
    if (ctx().state !== "running") await ctx().resume();
    const midi = midiOf(pitch), lane = o.lane || "";
    const pick = pickKey(store, midi, !!hold, pkOf(lane), lane) || { entry: await ensure(midi, !!hold, lane), rate: 1, exact: true };
    const { src, x } = source(pick.entry.buffer, pick.rate, !!hold);
    const g = ctx().createGain();
    g.gain.value = velocity;
    g.curve = hold?.curve ?? 2; // env_curve, for releaseKey's fade
    x.connect(g).connect(o.out || keyPath());
    const h = { src, x, g, midi, lane, key: kOf(midi, hold, lane), hold: !!hold, entry: pick.entry, rate: pick.rate,
      t0: ctx().currentTime, count: o.count !== false };
    // count before start so a fast run of keys ducks the bus as it builds;
    // onended covers both releaseKey's stop() and a one-shot ringing out
    if (h.count) { keyVoices++; setPoly(); }
    live.add(h); started(h, src);
    src.start();
    if (!pick.exact) ensure(midi, !!hold, lane).catch(() => {}); // lands -> swap
    return h;
  }
  // core.adsr's release, 1 -> 0 over n points: 1 - u^(1/curve). The loop
  // render releases along this curve, so a held key must too (a straight
  // line was a different tail from the same note in the loop).
  function releaseCurve(n, curve = 2) {
    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) out[i] = 1 - Math.pow(i / (n - 1), 1 / (curve || 1));
    return out;
  }
  // Fades the still-looping sustain rather than jumping into the render's own
  // release tail: that target sits at an arbitrary phase of any unison
  // beating, and measured rougher than this fade (tests/studio/
  // test_key_release.py). With the loop wrapping ~0.3x/s, few seams land here.
  function releaseKey(h, release = 0.1) {
    // one-shots ring out as rendered; a second release would overlap the
    // first curve, which setValueCurveAtTime throws on
    if (!h?.src.loop || h.released) return;
    h.released = true;
    const t = ctx().currentTime, r = Math.max(0.02, release);
    const level = h.g.gain.value, curve = releaseCurve(256, h.g.curve);
    for (let i = 0; i < curve.length; i++) curve[i] *= level;
    h.g.gain.cancelScheduledValues(t);
    h.g.gain.setValueCurveAtTime(curve, t, r);
    h.src.stop(t + r + 0.02);
  }

  // The pitch bend in semitones (piano.js's wheel): every sounding key and
  // lane glides to it (5 ms, as the worklet smooths it); new ones start there
  function bendKeys(st) {
    bend = Math.pow(2, st / 12);
    const t = ctx()?.currentTime ?? 0;
    for (const h of live) h.src.playbackRate.setTargetAtTime(h.rate * bend, t, 0.005);
  }

  // Pure: the wheel `w` (-1..1) one frame of an arrow-key bend later.
  // `dir` is where it heads (+1/-1 held, 0 back to centre); `t` seconds
  // since that began, `dt` the frame; `secs` a whole 0 -> +-1 trip (rise or
  // release). "linear" moves at a steady 1/secs; "ease" speeds up the
  // longer it goes (position t^2/secs^2 from rest: the same trip time).
  function bendStep(w, dir, t, dt, secs, curve) {
    if (!(secs > 0)) return dir;
    const move = curve === "ease" ? (t * t - Math.max(0, t - dt) ** 2) / (secs * secs) : dt / secs;
    return dir > w ? Math.min(dir, w + move) : Math.max(dir, w - move);
  }

  // A patch change (piano.js loadPatch): note which render is current —
  // the key's, and each pre-rendered lane's — re-fetch the keys sounding on
  // a changed one right now, then (debounced) warm every visible key of
  // the key WAV (unless the worklet plays it) and of every lane —
  // `pitches` in order, QWERTY octave first, lanes interleaved.
  const holdOf = (p, holdMode) => !!(holdMode && p && p.sustain > 0.05);
  function setKeyPatch(patch, pitches, holdMode, needWav) {
    noteView = patch.view || "";
    // a warm-up still queued is the LAST patch's: its lanes may not be this
    // view's (rows by sound switched off: every /note of it was a 404), and
    // this patch may queue none of its own to supersede it
    warmToken++;
    const lanes = patch.worklet_ok ? (patch.layers || []).filter((l) => l.mode === "buffer") : [];
    for (const slot of [...lanePk.keys()]) if (!lanes.some((l) => l.slot === slot)) lanePk.delete(slot);
    const moved = new Set();
    if (patch.note_key !== currentPk) {
      currentPk = patch.note_key; moved.add("");
      gens.set(gen + 1, { cutoff: patch.cutoff, osc1_level: patch.osc1_level });
      gens.delete(gen - 31);
    }
    for (const l of lanes) if (lanePk.get(l.slot) !== l.note_key) { lanePk.set(l.slot, l.note_key); moved.add(l.slot); }
    if (moved.size) {
      gen++;
      const held = [...live].filter((h) => !h.released);
      if (moved.has("") && !held.some((h) => !h.lane)) resetBridge(gen); // nothing sounds on an old render
      for (const h of held) if (moved.has(h.lane)) ensure(h.midi, h.hold, h.lane).catch(() => {});
    }
    clearTimeout(warmTimer);
    const jobs = pitches.map(midiOf).filter((m) => m != null).flatMap((m) => [
      ...(needWav ? [[m, holdOf(patch, holdMode), ""]] : []), ...lanes.map((l) => [m, holdOf(l, holdMode), l.slot])]);
    if (jobs.length) warmTimer = setTimeout(() => warm(jobs), 300);
  }
  // A view without a keyboard (Song, Mix): the warm-up queued on the last
  // one stops. It ran on — two pre-rendered lanes x every key, each a render
  // with its FX — beside the song's own renders, for keys nobody can press.
  document.addEventListener("htmx:after:settle", () => {
    if (!document.querySelector(".pkey")) { clearTimeout(warmTimer); warmToken++; }
  });
  async function warm(queue) {
    S.ensureCtx();
    const token = ++warmToken;
    const worker = async () => {
      while (queue.length && token === warmToken) {
        try { await ensure(...queue.shift()); } catch { /* a lane gone mid-warm: 404 */ }
      }
    };
    await Promise.all([worker(), worker(), worker(), worker()]); // the server renders 4 at once
  }

  // one-shot pads and auditions: immutable URLs, cached by URL. An audition
  // (`solo`: a card's or a sound's ▶) is one at a time — the next one fades
  // the last out in 30 ms, so they never stack, every click a phrase more
  // over the others. Pads and drum hits overlap.
  const urlBuffers = new Map();
  let soloOut = null; // {src, g}: the audition sounding
  async function playUrl(url, { solo = false } = {}) {
    S.ensureCtx();
    await ctx().resume();
    let buffer = urlBuffers.get(url);
    if (!buffer) { buffer = await S.fetchBuffer(url); urlBuffers.set(url, buffer); }
    const src = ctx().createBufferSource();
    src.buffer = buffer;
    // one-shots (drum hits, auditions) stay dry — SPACE is the keys' effect —
    // but still need the limiter: hammering a pad overlaps hits and two
    // full-scale kicks sum past 1.0.
    if (!oneShotLimit) { oneShotLimit = limiter(); oneShotLimit.connect(S.getMaster()); }
    if (solo) {
      const t = ctx().currentTime, g = ctx().createGain();
      if (soloOut) { soloOut.g.gain.setTargetAtTime(0, t, 0.01); soloOut.src.stop(t + 0.03); }
      src.connect(g).connect(oneShotLimit);
      const me = soloOut = { src, g };
      src.addEventListener("ended", () => { if (soloOut === me) soloOut = null; });
    } else src.connect(oneShotLimit);
    src.start();
    if (buffer.duration > 1.5) S.transport?.oneShot(url, src, buffer.duration); // a clip, a sound: the dock names it, ■ stops it
    return src; // a card that auditions lights while it sounds (library.js)
  }
  // A new loop landed (audio.js). Key renders are never dropped here — they
  // stand in until setKeyPatch's re-warm replaces them — but patch-derived
  // one-shot URLs are stale; factory and bred-tile one-shots are immutable.
  function flushKeys() {
    for (const k of urlBuffers.keys()) if (!k.startsWith("/factory/") && !k.startsWith("/breed/note/")) urlBuffers.delete(k);
  }

  // ---- pre-rendered lanes (livefx.js): a key's notes on those lanes ----
  const laneKeys = new Map(); // note id -> Promise of [{h, release}]
  // A render of the key on that lane at hand (stale or a neighbour will do)?
  // A key with no current one fetches it now, so the next press has it even
  // where nothing warms the keyboard (Perform's pads, MIDI past the keys).
  function hasLane(slot, midi, hold) {
    const got = pickKey(store, midi, hold, lanePk.get(slot), slot);
    if (!got?.exact && lanePk.has(slot)) ensure(midi, hold, slot).catch(() => {});
    return !!got;
  }
  function laneOn(id, lanes, midi, vel, holdMode, out) {
    laneOff(id);
    laneKeys.set(id, Promise.all(lanes.map((l) => {
      const hold = holdOf(l, holdMode) ? { curve: l.env_curve } : null;
      return playKey(nameOf(midi), vel, hold, { lane: l.slot, out, count: false })
        .then((h) => ({ h, release: l.release }), () => null);
    })));
  }
  function laneOff(id) { // a lane still fetching is released the moment it lands
    const p = laneKeys.get(id); laneKeys.delete(id);
    p?.then((hs) => hs.forEach((x) => x && releaseKey(x.h, x.release)));
  }

  Object.assign(S, {
    playKey,
    laneOn,
    laneOff,
    hasLane,
    holdOf,
    laneIds: () => [...laneKeys.keys()],
    releaseKey,
    limiter,
    keyBus,
    keyTap,
    countVoice,
    playUrl,
    liveMod,
    flushKeys,
    setKeyPatch,
    bendKeys,
    bendStep, // pure; tests/studio/test_pitch_bend.py runs it under node
    releaseCurve, // pure; tests/studio/test_key_release.py runs it under node
    pickKey, // pure; tests/studio/test_key_swap.py
    swapOffset, // pure; tests/studio/test_key_swap.py
    warmKey: (pitch, hold) => { S.ensureCtx(); return ensure(midiOf(pitch), hold).then(() => {}); },
  });
})();
