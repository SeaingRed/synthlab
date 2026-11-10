// synthlab/studio/static/piano.js
// Note-input island: QWERTY keys, piano strip, AudioWorklet live voice, and
// the XY motion pad's drone/gesture-record/replay layer all land here.
// 380 -> 470: the stated budget had been stale for a while (the file was
// already 462 lines against it, unenforced). Chord/MIDI/gesture work landed
// here because "six islands only" leaves nowhere else to put it. The number
// is now honest AND pinned by tests/studio/test_island.py, so it cannot
// drift silently again. Real shrinkage means moving the ~195-line XY pad +
// gesture layer out to a 7th island — an architecture call, not a cleanup.
// 470 -> 490: Ctrl+Z undo and the panel-slider live-mod bridge — both
// belong to the note/worklet surface this island owns.
// 490 -> 540: the pitch bend — one wheel (setWheel) the bend strip, MIDI's
// pitch bend and the XY pad's "Pitch bend" axis all drive: a "wheel"
// message to every voice node (semitones = position x the patch's
// bend_range, smoothed in the worklet), keys.js's bendKeys for WAV keys and
// pre-rendered lanes, and "record bend", which captures one loop of it as
// the edited lane's pitch_bend curve.
// 540 -> 590: the arrow bend — hold up/down to glide the same wheel over a
// rise time, let go and it glides home over a release time (keys.js
// bendStep), with its settings menu (arrowsBend, glideTo).
// 590 -> 592: the classic split carries on past J (K O L P ; ': C..F an
// octave up), each key found in its own octave.
// 592 -> 615: Shift is the sustain pedal (pedal, sustained, release; a
// struck held note starts over).
// 615 -> 645: Right Shift latches the next note (latch, alias, latchTap).
// 645 -> 660: a voice token per note-on — the pedal and the
// latch are holds on a voice, not aliases of a key's id (`voices`, `drop`,
// a key's light painted from its voices), which makes per-press fetch
// tokens redundant.
// 660 -> 680: the mod wheel and aftertouch (setMod: a message to
// every voice node; MIDI's CC 1 / pressure and the XY pad's axes drive it;
// ⏺ bend records them too when they move in the take).
// 680 -> 690: keysOff for stop-all, a latch let go on another sound.
// 690 -> 715: the keyboard dock on every page — the one typing
// gate (`typing`: a surface with keys of its own, a page's own key,
// Perform's typing → keys | pads and its Alt+P), the key-note event the
// read-out names a note by, the same sound on another page left alone.
// 715 -> 730: the keys arp (arp.js) — a note it takes is a
// voice that does not sound (its holds still the key, pedal and latch), a
// note it plays is due at a context time (`at`), and its chord sounds as
// it is held when the arp goes off (`sound`, `arpHeld`, `arpOff`).
// Budget: <= 730 code lines.

(() => {
  "use strict";
  // the classic split: A..J one octave, then K O L P ; ' carry on into the
  // next (C..F), as a DAW's typing keyboard does — they used to play nothing
  const KEYMAP = { a: 0, w: 1, s: 2, e: 3, d: 4, f: 5, t: 6, g: 7, y: 8, h: 9, u: 10, j: 11, k: 12, o: 13, l: 14, p: 15, ";": 16, "'": 17 };
  const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  let octave = 4;

  // A focused range slider must NOT swallow the keys: only text-like
  // inputs, selects, textareas and editable regions count as "typing".
  function isTypingTarget(t) {
    return !!t && (/^(select|textarea)$/i.test(t.tagName || "") || t.isContentEditable === true ||
      t.getAttribute?.("role") === "textbox" ||
      (/^input$/i.test(t.tagName || "") && !/^(range|checkbox|radio|button|submit|color)$/i.test(t.type || "")));
  }
  // Shared gate for every letter/digit shortcut (piano, chords, perform
  // pads): typing targets never play, and neither does a focused button or
  // link outside a play surface — Tab to "delete preset", press "a", and
  // nothing should sound. Only a button the KEYBOARD reached (:focus-visible):
  // one a mouse clicked (a sound in the list, a tab) keeps the keys playing.
  const PLAY_SURFACE = ".piano-wrap, #chord-bar, #perform-pads";
  function keysBlocked(t) {
    if (isTypingTarget(t)) return true;
    return !!t?.closest && /^(button|a|summary)$/i.test(t.tagName || "") && !t.closest(PLAY_SURFACE) && !!t.matches?.(":focus-visible");
  }
  window.synthlab.isTypingTarget = isTypingTarget;
  window.synthlab.keysBlocked = keysBlocked;
  // the "typing" switch (#keys-toggle: the keys dock's QWERTY group, every
  // page): turns the QWERTY piano off so typing letters elsewhere never
  // plays; sticky across reloads.
  let keysOn = localStorage.getItem("synthlab.keys") !== "0";
  window.synthlab.keysOn = () => keysOn; // chords.js rows and perform.js pads obey it too
  // typing → keys | pads (#kbd-typeto, shown where #perform-pads is; Alt+P):
  // per browser, keys by default — the dock's layout carries there as on
  // every page; pads gives Perform's keys back (perform.js: its pads and FX)
  let typeTo = localStorage.getItem("synthlab.typeto") === "pads" ? "pads" : "keys";
  const padsTyping = () => typeTo === "pads" && !!document.getElementById("perform-pads");
  function syncKeysToggle() {
    document.getElementById("keys-toggle")?.setAttribute("aria-pressed", String(keysOn));
    const seg = document.getElementById("kbd-typeto");
    if (seg) seg.hidden = !document.getElementById("perform-pads");
    document.documentElement.dataset.typeto = typeTo; // style.css dims the pads' and FX keys' letters while typing plays the keys
    seg?.querySelectorAll("[data-typeto]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.typeto === typeTo)));
  }
  function setTypeTo(v) { typeTo = v; localStorage.setItem("synthlab.typeto", v); syncKeysToggle(); }
  document.addEventListener("click", (e) => {
    const to = e.target.closest?.("#kbd-typeto [data-typeto]");
    if (to) return setTypeTo(to.dataset.typeto);
    if (!e.target.closest("#keys-toggle")) return;
    keysOn = !keysOn; localStorage.setItem("synthlab.keys", keysOn ? "1" : "0"); syncKeysToggle();
  });
  window.addEventListener("keydown", (e) => {
    if (e.code !== "KeyP" || !e.altKey || e.ctrlKey || e.metaKey || e.repeat || !document.getElementById("perform-pads") || isTypingTarget(e.target)) return;
    e.preventDefault(); e.stopImmediatePropagation();
    setTypeTo(typeTo === "pads" ? "keys" : "pads");
    window.synthlab.toast?.(typeTo === "pads" ? "Typing plays the pads (Q W E R / A S D F) and the FX keys — Alt+P: the keys" : "Typing plays the keys — Alt+P: the pads");
  }, true);
  document.addEventListener("htmx:after:swap", syncKeysToggle);
  document.addEventListener("DOMContentLoaded", syncKeysToggle);
  // What the dock's typing plays through (the dock is on every
  // page): never a field or a keyboard-reached button (keysBlocked), nor a
  // surface with keys of its own — a step grid, the playlist, the jam's
  // grid, a chord tile, the ruler, the Lab's wave, an edge — nor a page's
  // own plain key (#view[data-own-keys]: L loops on Song and Mix), nor
  // while Perform's typing plays its pads or the Song page's finger drums
  // are armed. (keysBlocked alone still gates the transport's Space.)
  const KEY_OWNERS = ".grid, .arranger-grid, .jam-grid, .ctile, #song-ruler, #lab-wave, .chop-mark, .slice-mark";
  const ownKey = (code) => (document.getElementById("view")?.dataset.ownKeys || "").split(" ").includes(code);
  // a button or link a pointer pressed (a view's nav link, a toolbar's
  // button) is not one the keyboard reached, though Chrome turns its
  // :focus-visible on at the first key typed: it leaves the keys playing
  // (its Space stays its own: keysBlocked, transport.js)
  let pointed = null;
  document.addEventListener("pointerdown", (e) => { pointed = e.target.closest?.("button, a, summary") || null; }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Tab") pointed = null; }, true); // the keyboard moves on
  const typing = (e) => keysOn && (!keysBlocked(e.target) || (e.target === pointed && e.code !== "Space")) &&
    !e.target.closest?.(KEY_OWNERS) && !ownKey(e.code) && !padsTyping() && window.synthlab.songArmed?.()?.kind !== "drums";
  Object.assign(window.synthlab, { typing, padsTyping }); // chords.js, letters.js, columns.js; perform.js

  const clamp01 = (v) => Math.min(1, Math.max(0, v));
  // Pen pressure -> velocity. A mouse reports exactly 0.5, which is "no
  // pressure info", not "half pressure" — those play at full velocity.
  const velOf = (e) => (e.pressure > 0 && e.pressure !== 0.5 ? Math.min(1, 0.25 + e.pressure) : 1);

  function pitchOf(noteIndex, octOffset = 0) {
    return `${NOTE_NAMES[noteIndex]}${octave + octOffset}`;
  }

  // ---- voices: a token per note-on, sounding while anything holds it ------
  // A source (a pointer, "kbd:<code>", "midi:60", a chord's "<id>:<i>")
  // sounds one voice at a time (`active`); every note-on mints a voice
  // token, and that token is what the worklet, a WAV key and a take's notes
  // know it by. A voice's `holds` are what keep it sounding: "key" (its
  // source is down), "pedal" (let go under Left Shift), "latch" (Right
  // Shift caught it) — it ends when the last one lets go (`drop`). So a
  // held note's key struck again is simply a new voice: nothing is renamed.
  const active = new Map(); // source id -> voice token
  const voices = new Map(); // token -> { el, midi, holds: Set }
  let voiceSeq = 0;
  // a key with no element on screen (a MIDI note off the drawn octaves, a typing key on a folded keyboard)
  const ghostKey = (note, rel) => ({ dataset: { note, rel }, classList: { toggle() {} } });

  function keyEl(noteIndex, rel = 0) {
    return document.querySelector(`.pkey[data-note="${noteIndex}"][data-rel="${rel}"]`);
  }

  function pitchOfEl(el) {
    return pitchOf(Number(el.dataset.note), Number(el.dataset.rel));
  }
  const midiOf = (el) => 12 * (octave + Number(el.dataset.rel) + 1) + Number(el.dataset.note);

  // a key's light, from every voice on it: down while one sounds, `held`
  // while one sounds with its key up, `latched` while the latch holds one
  // (`arped`: a note the keys arp holds — arp.js plays it)
  function paint(el) {
    const on = [...voices.values()].filter((v) => v.el === el), up = on.filter((v) => !v.holds.has("key"));
    el.classList.toggle("down", on.some((v) => !v.arp));
    el.classList.toggle("held", up.some((v) => !v.arp));
    el.classList.toggle("latched", up.some((v) => !v.arp && v.holds.has("latch")));
    el.classList.toggle("arped", on.some((v) => v.arp));
  }

  // (at: the context time a note is due — the keys arp's, posted ahead; else now)
  function noteOn(id, el, velocity, at) {
    if (!el) return;
    const was = active.get(id), v = voices.get(was);
    if (v?.el === el) return; // a key's auto-repeat, a pointer still on its key
    // a pointer slid onto the next key: the worklet glides its voice there (a latched drone stays put)
    if (v && !v.arp && !v.holds.has("latch") && voiceKind.get(was) === "worklet" && workletCanPlay(patch) && el.classList) {
      const from = v.el;
      Object.assign(v, { el, midi: midiOf(el) });
      paint(from); paint(el);
      const f = freqOf(el);
      voiceNode.port.postMessage({ type: "bend", id: was, freq: f, time: Math.max(patch?.glide || 0, 0.06) });
      lastFreq = f;
      return;
    }
    if (was) noteOff(id);
    // the keys arp's own note (arp.js): its gate ends it — no pedal, no latch
    const midi = midiOf(el), own = String(id).startsWith("arp:");
    // a note the pedal alone holds, struck again: it starts over, as under a real pedal
    if (!own) for (const [t, w] of [...voices]) if (w.midi === midi && w.holds.size === 1 && w.holds.has("pedal")) release(t);
    // the latch armed: this note, and the rest of its chord (a strum included), hold
    if (latch.armed && !own) { latch.armed = false; latch.until = performance.now() + 250; }
    // the keys arp on: the note joins the chord it plays (it never sounds of itself)
    const latching = !own && performance.now() <= latch.until, arp = !own && !!window.synthlab.arp?.takes(latching);
    const tok = `v${++voiceSeq}`;
    voices.set(tok, { el, midi, vel: velocity, arp, holds: new Set(latching ? ["key", "latch"] : ["key"]) });
    active.set(id, tok);
    paint(el); paintLatch();
    if (arp) return void window.synthlab.arp.changed();
    sound(tok, el, midi, velocity, at, own);
    if (!holdMode && !own) setTimeout(() => { if (active.get(id) === tok) noteOff(id); }, GATE_MS);
  }
  function sound(tok, el, midi, velocity, at, own) {
    startVoice(tok, el, velocity, at);
    document.dispatchEvent(new CustomEvent("key-note", { detail: { tok, midi } })); // keysbar.js names what sounds
    window.synthlab.onNoteOn?.(midi, velocity); // songrec.js captures takes
    window.synthlab.keyNote?.(tok, midi, velocity, at, own); // keyrec.js: a take's notes, ⟲ Capture's buffer (own: the keys arp's)
  }

  // the source lets go: under the pedal its voice keeps sounding, its key lit, until Shift comes up
  function noteOff(id, at) {
    const tok = active.get(id);
    if (!tok) return;
    active.delete(id);
    if (pedal && !String(id).startsWith("arp:")) voices.get(tok)?.holds.add("pedal");
    drop(tok, "key", at);
  }
  // a hold lets go of a voice: the last one ends it
  function drop(tok, hold, at) {
    const v = voices.get(tok);
    if (!v) return;
    v.holds.delete(hold);
    if (v.holds.size) paint(v.el); else release(tok, at);
  }
  const dropAll = (hold) => { for (const [tok, v] of [...voices]) if (v.holds.has(hold)) drop(tok, hold); };
  // ends a voice whatever holds it (one the arp held never sounded: it lets it go)
  function release(tok, at) {
    const v = voices.get(tok);
    if (!v) return;
    voices.delete(tok);
    for (const [id, t] of active) if (t === tok) active.delete(id);
    paint(v.el);
    if (v.arp) return void window.synthlab.arp?.changed();
    stopVoice(tok, at);
    window.synthlab.keyNote?.(tok, undefined, undefined, at); // ... and where each ends
    document.dispatchEvent(new CustomEvent("key-note", { detail: { tok } }));
  }

  // ---- Left Shift: the sustain pedal --------------------------------------
  // Held, a released note — a key let go, a row's chord, an on-screen key, a
  // MIDI note, hold-off's short gate — keeps sounding until Shift comes up
  // (every source ends in noteOff). Not from a field (Shift is a capital
  // there), never while the typing keys are off; a lost focus lets it up.
  // ---- Right Shift: the latch ---------------------------------------------
  // A tap arms it; the next note played — with the rest of its chord, the
  // notes struck within 250 ms — holds after it is let go, a drone to play
  // over; a tap again lets it go (or disarms it, nothing played). Its key
  // struck again is a new voice of its own (never the drone bent or
  // restarted). The latch is not the pedal's: Left Shift up leaves it.
  let pedal = false;
  const latch = { armed: false, until: 0 };
  const latched = () => [...voices.values()].filter((v) => v.holds.has("latch")).length;
  function paintLatch() {
    const state = latch.armed ? "armed" : latched() ? "on" : "";
    if (state) document.documentElement.dataset.latch = state; else delete document.documentElement.dataset.latch;
    document.getElementById("latch-btn")?.setAttribute("aria-pressed", String(!!state)); // the dock's Play: latch
  }
  function latchTap() {
    if (latch.armed || latched()) {
      latch.armed = false; latch.until = 0;
      dropAll("latch");
    } else {
      latch.armed = true;
      window.synthlab.toast?.("Latch: the next note you play holds — latch (or Right Shift) again lets it go.");
    }
    paintLatch();
  }
  function pedalUp() {
    if (!pedal) return;
    pedal = false;
    document.documentElement.toggleAttribute("data-pedal", false);
    dropAll("pedal");
  }
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Shift" || e.repeat || !typing(e)) return;
    if (e.code === "ShiftRight") return latchTap();
    if (pedal) return;
    pedal = true;
    document.documentElement.toggleAttribute("data-pedal", true);
  }, true);
  document.addEventListener("keyup", (e) => { if (e.key === "Shift" && e.code !== "ShiftRight") pedalUp(); }, true);
  document.addEventListener("click", (e) => { if (e.target.closest?.("#latch-btn")) latchTap(); }); // the dock's latch: a tap of Right Shift
  window.synthlab.pedal = () => pedal;
  window.synthlab.latch = () => ({ armed: latch.armed, notes: latched() });

  // Voice indirection: AudioWorklet live voice when the patch is supported;
  // otherwise the WAV one-shot fallback. The gate itself is server-side now
  // (studio/worklet.py: the one classification of every Patch field, reused
  // by test_factory.py) — /patch.json carries the verdict as worklet_ok, so
  // this is a read, never a re-derivation that can drift from what
  // voice-worklet.js actually does.
  // (and a live sampler's buffers have reached the worklet: livefx.js ready)
  function workletCanPlay(p) {
    return !!(p && p.worklet_ok && (window.synthlab.live?.ready?.() ?? true));
  }
  let voiceNode = null, voiceReady = false, patch = null, patchSeq = 0, patchText = "";
  const voiceKind = new Map(); // voice token -> "worklet"|"wav"
  let lastFreq = null;

  // The live voice is livefx.js's graph: a voice node per lane a key
  // sounds on (the view's ▶ set: edited slot, main lane, followers), each
  // through its FX, then the master chain, into keys.js's bus (the one gain
  // stage WAV keys share). `voiceNode` is its facade: note messages reach
  // every lane at its follow octave, "mod" the edited slot.
  async function initVoice() {
    if (voiceNode || !window.synthlab.getCtx()) return;
    try {
      voiceNode = await window.synthlab.live.start(patch?.worklet_ok ? patch : null);
      voiceReady = true; pushParams(); // live.start is one shared start: the patch may have moved on since
    } catch { voiceReady = false; }
  }

  // The worklet's voices read one shared param block every block: the
  // whole stack (every layer's patch, FX and gain) goes over on every load,
  // so any control reaches held notes. Only one it plays — a held worklet
  // note keeps the last one.
  function pushParams() {
    if (voiceNode && patch?.worklet_ok) window.synthlab.live.setStack(patch);
  }

  async function loadPatch() {
    // If the XY pad is mid-drag, its unthrottled local mod stream is ahead
    // of whatever the server had when this fetch was issued; overwriting
    // patch and pushing it would snap the sound backwards mid-gesture. Skip
    // the push here — pointermove keeps posting mod values and pointerup's
    // final send + its own preview-ready converge state once the drag ends.
    const live = xyDown;
    const view = document.getElementById("view")?.dataset.view || "";
    // A/B dev flag: every lane in the worklet, none pre-rendered (fxlive.py)
    const all = localStorage.getItem("synthlab.allLive") === "1" || /[?&]alllive\b/.test(location.search);
    const seq = ++patchSeq;
    let next = null, text = "";
    try { text = await (await fetch(`/patch.json?view=${encodeURIComponent(view)}${all ? "&live=all" : ""}${window.synthlab.rowsBy?.() ? "&rows=1" : ""}`)).text(); next = JSON.parse(text); } catch {}
    if (seq !== patchSeq) return; // overtaken: the latest fetch's answer is the patch, whichever lands first
    // the same sound on another page (a view swap re-cues ▶, it moves no
    // sound): its page name is all that changed — no push, no re-warm
    const same = text.replace(/"view": "[^"]*"}$/, "");
    if (next && patch && same === patchText) { patch.view = next.view; return; }
    const was = patch?.name;
    patch = next; patchText = same;
    if (!patch) return;
    // another sound: what the latch held goes, or it would keep sounding the
    // old one; a tweak keeps a drone
    if (was && patch.name !== was && latched()) { latch.armed = false; dropAll("latch"); paintLatch(); }
    warmKeys();
    if (!live) pushParams();
    syncBend();
    if (wheel) setWheel(wheel); // a new range, or voice nodes that never heard it
  }
  // Every visible key's WAV, QWERTY octave first (keys.js keeps the old
  // renders playing until these land); none when the worklet plays it —
  // but every pre-rendered lane's (keys.js reads them off the patch).
  function warmKeys() {
    if (!patch) return;
    const keys = [...document.querySelectorAll(".pkey")].filter((k) => k.offsetParent).sort((a, b) => a.dataset.rel - b.dataset.rel);
    window.synthlab.setKeyPatch(patch, keys.map(pitchOfEl), holdMode, !workletCanPlay(patch));
  }

  // Panel sliders ride the live-mod bus too: every p-* range reaches the
  // worklet's shared block at once (a held note follows the drag), WAV keys
  // get the cutoff/level bridge (keys.js liveMod) until their re-render
  // lands; the debounced form post trues up the loop render. Bubble phase,
  // no stopPropagation — the form post must still happen.
  document.addEventListener("input", (e) => {
    const id = e.target?.id || "";
    if (!id.startsWith("p-") || !patch || e.target.type !== "range") return;
    const name = id.slice(2);
    if (typeof patch[name] !== "number") return;
    const value = e.target.dataset.log ? window.synthlab.logVal(e.target) : Number(e.target.value);
    if (!Number.isFinite(value)) return;
    patch[name] = value;
    if (patch.worklet_ok) voiceNode?.port.postMessage({ type: "mod", param: name, value });
    window.synthlab.liveMod(name, value);
  });

  function midiToFreq(midi) { return 440 * Math.pow(2, (midi - 69) / 12); }
  function freqOf(el) { return midiToFreq(12 * (octave + Number(el.dataset.rel) + 1) + Number(el.dataset.note)); }
  // Parses "E3"-style note names (NOTE_NAMES + octave) into a frequency —
  // shares the midi math with freqOf but works from a plain string (the
  // arrangement's root select) rather than a .pkey element's dataset.
  function freqOfPitch(name) {
    const m = /^([A-G]#?)(-?\d+)$/.exec(name || "");
    const idx = m && NOTE_NAMES.indexOf(m[1]);
    return m && idx >= 0 ? midiToFreq(12 * (Number(m[2]) + 1) + idx) : null;
  }

  // First-note race: initVoice() loads the worklet module asynchronously, so
  // a note struck before it resolves falls back to the WAV path; subsequent
  // notes use the worklet. Intended behavior, not a bug to fix with awaits.
  // a note due later (the keys arp's `at`): the worklet holds it to its
  // block (voice-worklet.js); a WAV key waits for it on a timer
  const later = (at, fn) => { const now = window.synthlab.getCtx().currentTime; return at > now ? setTimeout(fn, (at - now) * 1000) : fn(); };
  function startVoice(id, el, velocity, at) {
    window.synthlab.ensureCtx();
    window.synthlab.getCtx().resume();
    if (!voiceNode) initVoice().then(() => {});
    if (voiceReady && workletCanPlay(patch)) {
      voiceKind.set(id, "worklet");
      // the whole patch (env_curve and all): the worklet's shared block
      const msg = { type: "noteOn", id, freq: freqOf(el), vel: velocity, p: patch, hold: holdMode, ...(at ? { at } : {}) };
      if (patch.glide > 0 && lastFreq) { msg.fromFreq = lastFreq; msg.glideTime = patch.glide; }
      voiceNode.port.postMessage(msg);
      window.synthlab.countVoice(1);
      lastFreq = msg.freq;
    } else {
      voiceKind.set(id, "wav");
      lastFreq = freqOf(el);
      // hold mode sustains the rendered note by looping its sustain segment
      const hold = holdFor(patch) ? { curve: patch.env_curve } : null;
      // the voice's token is the press: one released (or re-pressed) before
      // its fetch landed lets that loop go, never stores a second handle
      later(at, () => window.synthlab.playKey(pitchOfEl(el), velocity, hold).then((h) => {
        if (voices.has(id)) wavHandles.set(id, h);
        else window.synthlab.releaseKey(h, patch?.release); // released before the fetch landed
      }).catch((err) => window.synthlab.toast(String(err.message || err), "error")));
    }
  }

  const wavHandles = new Map(); // voice token -> {src, g} of a looping WAV voice
  function stopVoice(id, at) {
    if (voiceReady) voiceNode.port.postMessage({ type: "noteOff", id, ...(at ? { at } : {}) });
    if (voiceKind.get(id) === "worklet") window.synthlab.countVoice(-1);
    const h = wavHandles.get(id);
    if (h) { later(at, () => window.synthlab.releaseKey(h, patch?.release ?? 0.2)); wavHandles.delete(id); }
    voiceKind.delete(id);
  }

  document.addEventListener("preview-ready", () => { loadPatch(); }, true);
  // the Song page's sound dock edits the sound in a frame (songdock.js): its
  // posts never cue this page's ▶, so the dock's keys ask again (the same answer costs nothing)
  document.addEventListener("song-changed", (e) => { if (e.detail?.now) loadPatch(); });
  document.addEventListener("DOMContentLoaded", () => { loadPatch(); });
  // A full-body swap (preset load, breed "Use this sound", layer select)
  // replaces #patch wholesale; the preview-ready that rides on it is fired
  // on an element the swap just removed, so it never reaches us. Watch the
  // form node itself: a new one means a new sound for the keys.
  let patchForm = null;
  document.addEventListener("htmx:after:swap", () => {
    const f = document.getElementById("patch");
    if (!f || f === patchForm) return;
    patchForm = f;
    loadPatch(); // a new sound: keys.js re-warms, old renders stand in
  });
  document.addEventListener("DOMContentLoaded", () => { patchForm = document.getElementById("patch"); });
  // hold: a key sounds while it's down (default). Off = trigger mode: every
  // press fires a short gate and the envelope's own release takes over.
  let holdMode = localStorage.getItem("synthlab.hold") !== "0";
  // a held key loops its own longer render (keys.js, ?hold=1), so the
  // warm-up must fetch the same one the key will ask for
  const holdFor = (p) => !!(holdMode && p && p.sustain > 0.05);
  const GATE_MS = 160;
  function syncHold() { document.getElementById("hold-toggle")?.setAttribute("aria-pressed", String(holdMode)); }
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#hold-toggle")) return;
    holdMode = !holdMode; localStorage.setItem("synthlab.hold", holdMode ? "1" : "0"); syncHold(); warmKeys();
  });
  document.addEventListener("htmx:after:swap", () => { syncHold(); paintLatch(); });
  document.addEventListener("DOMContentLoaded", syncHold);

  function setOctave(next) {
    // (the top octave showing stays under MIDI 127: four of them stop at C5, five at C4)
    octave = Math.min(6, 9 - [...document.querySelectorAll(".octave")].filter((o) => o.offsetParent).length, Math.max(1, next));
    const label = document.getElementById("oct-label");
    if (label) label.lastChild.textContent = `C${octave}`; // the "octave" prefix is a static hidden span
    document.querySelectorAll(".pkey").forEach((k) => k.setAttribute("aria-label", pitchOfEl(k)));
    document.dispatchEvent(new CustomEvent("octave-changed", { detail: { octave } })); // chords.js shifts its pads
    warmKeys();
  }
  // Warming is keys.js's (setKeyPatch, via warmKeys): all 24 visible keys on
  // load and on every patch change, debounced 300 ms and cancelled by the
  // next one. It used to be armed only by hovering the keyboard and cover
  // one octave, so a first press — and the first after any edit — was cold.
  // Views with no keyboard (Drums) warm nothing: there are no .pkey keys.

  // A focused key plays on Enter/Space (chords.js intercepts in chord mode).
  const ACT_KEY = /^( |Enter)$/;
  document.addEventListener("keydown", (e) => {
    const el = e.target.closest?.(".pkey");
    if (!el || e.repeat || !ACT_KEY.test(e.key)) return;
    e.preventDefault();
    noteOn(`focus:${el.dataset.note}:${el.dataset.rel}`, el, 1);
  });
  // (focusout: focus moved on mid-press, so the keyup lands somewhere else)
  const focusOff = (e) => {
    const el = e.target.closest?.(".pkey");
    if (el && (e.type === "focusout" || ACT_KEY.test(e.key))) noteOff(`focus:${el.dataset.note}:${el.dataset.rel}`);
  };
  document.addEventListener("keyup", focusOff);
  document.addEventListener("focusout", focusOff);
  // Ctrl+Z = undo last tweak (clicks the reset menu's ↶ Undo); the piano's plain-z
  // octave shortcut below ignores modified keys so the two never collide.
  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.key.toLowerCase() !== "z" || isTypingTarget(e.target)) return;
    e.preventDefault();
    document.getElementById("undo-btn")?.click();
  });
  document.addEventListener("keydown", (e) => {
    // (Perform's typing → pads: the pads' keys; Alt+letter is a page's own —
    // the jam's Alt+J, Alt+L, Alt+M — never a note, as in chords.js's rows)
    if (e.repeat || !typing(e) || e.ctrlKey || e.metaKey || e.altKey) return;
    const key = e.key.toLowerCase();
    if (key === "z") { setOctave(octave - 1); return; }
    if (key === "x") { setOctave(octave + 1); return; }
    if (!(key in KEYMAP)) return;
    const n = KEYMAP[key] % 12, rel = Math.floor(KEYMAP[key] / 12);
    noteOn(`kbd:${e.code || key}`, keyEl(n, rel) || ghostKey(n, rel), 1);
  }, true);
  // released by physical key: AltGr/Option pressed mid-hold changes e.key, and the note hung
  document.addEventListener("keyup", (e) => noteOff(`kbd:${e.code || e.key.toLowerCase()}`), true);

  document.addEventListener("pointerdown", (e) => {
    const el = e.target.closest(".pkey");
    if (!el) return;
    e.preventDefault();
    const piano = document.getElementById("piano");
    try { piano.setPointerCapture(e.pointerId); } catch {}
    noteOn(e.pointerId, el, velOf(e));
  });
  document.addEventListener("pointermove", (e) => {
    if (!active.has(e.pointerId)) return;
    const el = document.elementFromPoint(e.clientX, e.clientY)?.closest(".pkey");
    if (el) noteOn(e.pointerId, el, velOf(e));
  });
  const releasePointer = (e) => { if (active.has(e.pointerId)) noteOff(e.pointerId); };
  document.addEventListener("pointerup", releasePointer);
  document.addEventListener("pointercancel", releasePointer);

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".oct-btn");
    if (btn) setOctave(octave + Number(btn.dataset.dir));
  });

  // every note off, latched ones too: ■ / Shift+Space (transport.js stopAll)
  window.synthlab.keysOff = () => {
    Object.assign(latch, { armed: false, until: 0 });
    for (const tok of [...voices.keys()]) release(tok);
    paintLatch();
    if (voiceNode) voiceNode.port.postMessage({ type: "allOff" });
  };
  window.addEventListener("blur", () => {
    pedalUp();
    Object.assign(latch, { armed: false, until: 0 });
    for (const tok of [...voices.keys()]) release(tok);
    paintLatch();
    if (voiceNode) voiceNode.port.postMessage({ type: "allOff" });
    droneOn = false; xyRecArmed = false; xyRecording = false; recSamples = [];
    syncArms();
  });

  // ---- XY performance pad (throttled 80ms; final position always sent) --
  let xyDown = false, xyLast = 0, xyPlay = false, droneOn = false;
  function startDrone(freq, vel) {
    window.synthlab.ensureCtx(); window.synthlab.getCtx().resume();
    if (!voiceNode) initVoice().then(() => {});
    if (!voiceReady) return;
    voiceNode.port.postMessage({ type: "noteOn", id: "xy-drone", freq, vel, p: patch });
    droneOn = true;
  }
  function stopDrone() { if (droneOn) { voiceNode?.port.postMessage({ type: "noteOff", id: "xy-drone" }); droneOn = false; } }
  const XY_SEL = { x: 'select[name="xy_x"]', y: 'select[name="xy_y"]' };
  // applyXY moves the crosshair/lines/readout and sends the live-mod
  // messages ONLY (no server post) — shared by direct pointer drags and the
  // gesture replayer below.
  function applyXY(nx, ny) {
    const pad = document.getElementById("xypad");
    if (!pad) return;
    const cross = pad.querySelector("#xycross");
    if (cross) { cross.style.left = `${nx * 100}%`; cross.style.top = `${(1 - ny) * 100}%`; }
    const lineV = pad.querySelector("#xyline-v"), lineH = pad.querySelector("#xyline-h");
    if (lineV) lineV.style.left = `${nx * 100}%`;
    if (lineH) lineH.style.top = `${(1 - ny) * 100}%`;
    const readout = document.getElementById("xy-readout");
    const parts = [];
    for (const [axis, n] of [["x", nx], ["y", ny]]) {
      const sel = document.querySelector(XY_SEL[axis]);
      const param = sel && sel.value;
      const min = Number(pad.dataset[`min${axis.toUpperCase()}`]), max = Number(pad.dataset[`max${axis.toUpperCase()}`]);
      /* log-axis math assumes min > 0 for log-flagged fields (only cutoff today) */
      const log = pad.dataset[`log${axis.toUpperCase()}`] === "1";
      const value = log ? min * Math.pow(max / min, n) : min + (max - min) * n;
      if (sel) parts.push(`${axis.toUpperCase()} ${sel.options[sel.selectedIndex]?.text || param} = ${Math.round(n * 100)}% (${value >= 1000 ? (value / 1000).toFixed(2) + "k" : value.toFixed(2)})`);
      // a performance axis: the wheel (every lane and key), not the patch
      if (param === "pitch_bend") { setWheel(value); continue; }
      if (param === "mod_wheel" || param === "aftertouch") { setMod(param, value); continue; } // the player's too
      if (!param || !patch || typeof patch[param] !== "number") continue;
      patch[param] = value;
      voiceNode?.port.postMessage({ type: "mod", param, value });
      window.synthlab.liveMod(param, value);
    }
    if (readout && parts.length) readout.textContent = parts.join(" · ");
  }
  function xyMove(pad, e, post) {
    const r = pad.getBoundingClientRect();
    const nx = clamp01((e.clientX - r.left) / r.width);
    const ny = 1 - clamp01((e.clientY - r.top) / r.height);
    applyXY(nx, ny);
    if (xyRecording) recordSample(nx, ny);
    const now = Date.now();
    if (post && (now - xyLast > 80 || !xyDown)) {
      xyLast = now;
      window.synthlab.post("/fragment/xy", { x: nx, y: ny });
    }
  }
  document.addEventListener("pointerdown", (e) => {
    const pad = e.target.closest("#xypad");
    if (!pad) return;
    e.preventDefault();
    try { pad.setPointerCapture(e.pointerId); } catch {}
    xyDown = true;
    if (xyRecArmed) { xyRecArmed = false; xyRecording = true; recStart = performance.now(); recSamples = []; recLast = 0; }
    xyMove(pad, e, true);
    if (xyPlay && workletCanPlay(patch)) {
      const freq = freqOfPitch(document.querySelector('select[name="root"]')?.value);
      if (freq) startDrone(freq, velOf(e));
    }
  });
  document.addEventListener("pointermove", (e) => {
    const pad = xyDown && document.getElementById("xypad");
    if (pad) xyMove(pad, e, true);
  });
  document.addEventListener("pointerup", (e) => {
    if (!xyDown) return;
    xyDown = false;
    const pad = document.getElementById("xypad");
    if (pad) xyMove(pad, e, true); // xyDown now false: final send bypasses the throttle
    if (Object.values(XY_SEL).some((q) => document.querySelector(q)?.value === "pitch_bend")) setWheel(0); // springs back
    stopDrone();
    if (xyRecording) { (performance.now() - recStart) / 1000 / loopSeconds() >= 0.25 ? finishRecording() : cancelRecording(); }
    if (xyGesture.length) replayAnchor = performance.now(); // restore: resync phase to now
  });
  document.addEventListener("pointercancel", () => { xyDown = false; stopDrone(); if (xyRecording) cancelRecording(); });
  // keyboard: arrows nudge the crosshair 2% (shift: 10%) and post like a drag
  const ARROWS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
  // piano.js loads before chords.js/perform.js (base.html), so it owns these
  // shared shortcut tables; the copies that used to live in those two files
  // read them from here instead of drifting their own.
  window.synthlab.keys = { KEYMAP, NOTE_NAMES, ACT_KEY, ARROWS };
  document.addEventListener("keydown", (e) => {
    const pad = e.target.id === "xypad" && e.target, d = ARROWS[e.key];
    if (!pad || !d) return;
    e.preventDefault();
    const cross = pad.querySelector("#xycross"), step = e.shiftKey ? 0.1 : 0.02;
    const nx = clamp01(parseFloat(cross.style.left) / 100 + d[0] * step);
    const ny = clamp01(1 - parseFloat(cross.style.top) / 100 + d[1] * step);
    applyXY(nx, ny);
    window.synthlab.post("/fragment/xy", { x: nx, y: ny });
  });

  // ---- gesture record + beat-synced replay -------------------------------
  // loopSeconds used to reimplement arrange.py::loop_seconds from the bpm/
  // bars fields on the page — a second definition of the same number that
  // could read stale or wrong DOM state. audio.js already decodes the
  // playing loop's buffer, so its duration is the one truth; fall back to a
  // sane default only when nothing is playing yet (recording/replay are
  // meaningless without a loop anyway).
  function loopSeconds() {
    return window.synthlab.loopDuration() || 4;
  }
  let xyRecArmed = false, xyRecording = false, recStart = 0, recLast = 0, recSamples = [];
  let xyGesture = [], replayAnchor = 0, replayRAF = 0;
  // Loop arm gates replay without deleting the recording; sticky across
  // reloads so a switched-off gesture never resumes moving on its own.
  let xyLoop = localStorage.getItem("synthlab.xyloop") !== "0";
  function setLoop(on) {
    xyLoop = on;
    localStorage.setItem("synthlab.xyloop", on ? "1" : "0");
    on ? replayStart() : replayStop();
    syncArms();
  }
  // Re-applies live JS state onto the arm buttons — needed after an axis
  // swap replaces #xypad-panel's outerHTML (fresh aria-pressed="false"
  // markup) while xyPlay/xyRecording keep their in-memory values. Always
  // re-queries: never caches the (possibly stale, post-swap) nodes.
  function syncArms() {
    document.getElementById("xy-play")?.setAttribute("aria-pressed", String(xyPlay));
    document.getElementById("xy-rec")?.setAttribute("aria-pressed", String(xyRecArmed || xyRecording));
    const loopBtn = document.getElementById("xy-loop");
    loopBtn?.setAttribute("aria-pressed", String(xyLoop));
    loopBtn?.toggleAttribute("disabled", !xyGesture.length); // stays visible: its armed state is sticky
    document.getElementById("xy-clear")?.toggleAttribute("hidden", !xyGesture.length);
  }
  document.addEventListener("htmx:after:swap", syncArms);
  function persistGesture(points) {
    window.synthlab.post("/fragment/xy/gesture", { points: JSON.stringify(points) });
  }
  function recordSample(nx, ny) {
    const now = performance.now(), elapsed = (now - recStart) / 1000;
    if (now - recLast < 30 || recSamples.length >= 240) return;
    recLast = now;
    recSamples.push([elapsed / loopSeconds(), nx, ny]);
    if (elapsed >= loopSeconds()) finishRecording();
  }
  function finishRecording() {
    if (!xyRecording) return;
    xyRecording = false;
    if (recSamples.length) {
      const maxPhase = recSamples[recSamples.length - 1][0];
      if (maxPhase > 0 && maxPhase < 1) for (const s of recSamples) s[0] = Math.min(1, s[0] / maxPhase);
      xyGesture = recSamples; persistGesture(xyGesture); setLoop(true); // a fresh take should be heard
    }
    recSamples = [];
    syncArms();
  }
  function cancelRecording() { xyRecording = false; recSamples = []; syncArms(); }
  document.addEventListener("click", (e) => {
    const play = e.target.closest("#xy-play"), rec = e.target.closest("#xy-rec"), clr = e.target.closest("#xy-clear");
    const loop = e.target.closest("#xy-loop");
    if (loop) { setLoop(!xyLoop); return; }
    if (play) {
      if (!xyPlay && !workletCanPlay(patch)) { window.synthlab.toast(`This sound can't drone live (${(patch?.worklet_blockers || []).join(", ")})`, "error"); return; }
      xyPlay = !xyPlay;
      if (!xyPlay) stopDrone();
      syncArms();
    } else if (rec) {
      if (xyRecArmed || xyRecording) { xyRecArmed = false; cancelRecording(); return; }
      if (!xyPlay) { window.synthlab.toast("Arm play first", "error"); return; }
      xyRecArmed = true; syncArms();
    } else if (clr) {
      xyGesture = []; persistGesture([]); replayStop(); syncArms();
    }
  });
  function sampleGesture(phase) {
    const pts = xyGesture;
    if (!pts.length) return null;
    let lo = 0, hi = pts.length - 1;
    if (phase <= pts[0][0]) return [pts[0][1], pts[0][2]];
    if (phase >= pts[hi][0]) return [pts[hi][1], pts[hi][2]];
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (pts[mid][0] <= phase) lo = mid; else hi = mid; }
    const [p0, x0, y0] = pts[lo], [p1, x1, y1] = pts[hi], t = p1 > p0 ? (phase - p0) / (p1 - p0) : 0;
    return [x0 + (x1 - x0) * t, y0 + (y1 - y0) * t];
  }
  function replayTick(now) {
    replayRAF = requestAnimationFrame(replayTick);
    const ghost = document.getElementById("xyghost");
    if (xyDown || !xyGesture.length) { ghost?.setAttribute("hidden", ""); return; }
    const pos = sampleGesture(((now - replayAnchor) / 1000 / (loopSeconds() || 1)) % 1);
    if (!pos) return;
    applyXY(pos[0], pos[1]);
    if (ghost) { ghost.removeAttribute("hidden"); ghost.style.left = `${pos[0] * 100}%`; ghost.style.top = `${(1 - pos[1]) * 100}%`; }
  }
  function replayStart() {
    if (!xyGesture.length || !xyLoop) return;
    replayAnchor = performance.now();
    if (!replayRAF) replayRAF = requestAnimationFrame(replayTick);
  }
  function replayStop() {
    if (replayRAF) cancelAnimationFrame(replayRAF);
    replayRAF = 0;
    document.getElementById("xyghost")?.setAttribute("hidden", "");
  }
  document.addEventListener("DOMContentLoaded", () => {
    try { xyGesture = JSON.parse(document.getElementById("xypad")?.dataset.gesture || "[]"); } catch { xyGesture = []; }
    xyGesture.sort((a, b) => a[0] - b[0]);
    if (xyGesture.length) replayStart(); // no-op while the loop arm is off
    syncArms();
  });

  // ---- sound-card hold-to-audition (tap loads, hold plays without loading) --
  // holdCard scopes the suppress-click flag to the card that was actually
  // held, so holding card A then dragging onto card B before release
  // doesn't swallow B's unrelated tap.
  let holdTimer = 0, holdFired = false, suppressCardClick = false, holdCard = null, holdAt = [0, 0];
  const holdStop = () => { clearTimeout(holdTimer); holdTimer = 0; };
  document.addEventListener("pointerdown", (e) => {
    const card = e.target.closest(".sound-card .load");
    if (!card) return;
    holdStop();
    holdCard = card.closest(".sound-card");
    const url = holdCard.dataset.auditionUrl;
    holdFired = false; holdAt = [e.clientX, e.clientY];
    holdTimer = setTimeout(() => {
      holdTimer = 0; holdFired = true;
      window.synthlab.playUrl(url, { solo: true }).catch((err) => window.synthlab.toast(String(err.message || err), "error"));
    }, 350);
  });
  // a flick-scroll is not a hold: a move past a few pixels, or the browser
  // taking the touch over to pan (pointercancel, never a pointerup), calls it off
  document.addEventListener("pointermove", (e) => { if (holdTimer && Math.hypot(e.clientX - holdAt[0], e.clientY - holdAt[1]) > 8) holdStop(); });
  document.addEventListener("pointercancel", () => { holdStop(); holdFired = false; }, true);
  document.addEventListener("pointerup", () => {
    holdStop();
    if (holdFired) { holdFired = false; suppressCardClick = true; setTimeout(() => { suppressCardClick = false; }, 0); }
  }, true);
  document.addEventListener("click", (e) => {
    if (!suppressCardClick) return;
    if (e.target.closest(".sound-card") !== holdCard) return;
    suppressCardClick = false;
    holdCard = null;
    e.preventDefault();
    e.stopPropagation();
  }, true);

  // ---- MIDI bridge (midi.js): same noteOn/noteOff path as the QWERTY keys ----
  // Row lifts + pad wraps + a high octave can run past MIDI 127 (G9); the
  // WAV fallback would then ask for /note/C10.wav, which the route rejects.
  // Fold by octaves so the pitch class survives.
  const inMidiRange = (m) => { while (m > 127) m -= 12; while (m < 0) m += 12; return m; };
  const fakeKey = (m) => keyEl(m % 12, Math.floor(m / 12) - 1 - octave) || ghostKey(m % 12, Math.floor(m / 12) - 1 - octave);
  window.synthlab.piano = {
    noteOn: (id, midi, vel, at) => noteOn(id, fakeKey(inMidiRange(midi)), vel, at), noteOff,
    wheel: (w) => setWheel(w), // MIDI pitch bend, -1..1
    modWheel: (v) => setMod("mod_wheel", v), aftertouch: (v) => setMod("aftertouch", v), // CC 1, pressure: 0..1
    routes: (src) => !!patch && (patch.mod_slots || []).some((s) => patch[`${s}_src`] === src && Math.abs(+patch[`${s}_amt`]) >= 1e-3),
    mod: (param, value) => { if (patch) patch[param] = value; voiceNode?.port.postMessage({ type: "mod", param, value }); },
    patch: () => patch, reload: loadPatch, octave: () => octave, setOctave, midiOf,
    // the keys arp (arp.js): the notes it holds, in the order played; off, they sound as they are held
    arpHeld: () => [...voices.values()].filter((v) => v.arp).map((v) => ({ midi: v.midi, vel: v.vel, key: v.holds.has("key") })),
    arpOff: () => { for (const [tok, v] of voices) if (v.arp) { v.arp = false; paint(v.el); sound(tok, v.el, v.midi, v.vel); } },
  };

  // ---- pitch bend: one wheel for the strip, MIDI and the XY axis ----------
  // -1..1; semitones = wheel x the edited patch's bend_range, so every lane
  // bends by the same interval (the worklet adds it to the patch's own bend)
  let wheel = 0, bendRec = null;
  // the mod wheel and aftertouch, 0..1 — a message to every
  // voice node, which adds it to the patch's own where a route reads it
  const mods = { mod_wheel: 0, aftertouch: 0 };
  function setMod(param, v) {
    mods[param] = Math.min(1, Math.max(0, Number(v) || 0));
    voiceNode?.port.postMessage({ type: param === "mod_wheel" ? "modwheel" : "aftertouch", v: mods[param] });
  }
  const range = () => Math.max(0, Number(patch?.bend_range ?? 2));
  function setWheel(w) {
    wheel = Math.min(1, Math.max(-1, Number(w) || 0));
    const st = wheel * range();
    voiceNode?.port.postMessage({ type: "wheel", st });
    window.synthlab.bendKeys?.(st);
    syncBend();
  }
  function syncBend() {
    const el = document.getElementById("bend-strip"), r = range(), st = +(wheel * r).toFixed(2);
    if (el) {
      el.style.setProperty("--bend", String(wheel));
      el.setAttribute("aria-valuemin", String(-r)); el.setAttribute("aria-valuemax", String(r));
      el.setAttribute("aria-valuenow", String(st));
      el.setAttribute("aria-valuetext", `${st > 0 ? "+" : ""}${st} semitones`);
    }
    const sel = document.getElementById("bend-range");
    if (sel && patch && document.activeElement !== sel) sel.value = String(r);
  }
  const stripAt = (el, e) => { const b = el.getBoundingClientRect(); return 1 - 2 * clamp01((e.clientY - b.top) / b.height); };
  document.addEventListener("pointerdown", (e) => {
    const el = e.target.closest?.("#bend-strip");
    if (!el) return;
    e.preventDefault(); el.focus();
    try { el.setPointerCapture(e.pointerId); } catch {}
    el.dataset.drag = String(e.pointerId); setWheel(stripAt(el, e));
  });
  document.addEventListener("pointermove", (e) => {
    const el = document.getElementById("bend-strip");
    if (el?.dataset.drag === String(e.pointerId)) setWheel(stripAt(el, e));
  });
  const springBack = (e) => {
    const el = document.getElementById("bend-strip");
    if (el?.dataset.drag !== String(e.pointerId)) return;
    delete el.dataset.drag; setWheel(0); // a wheel's spring
  };
  document.addEventListener("pointerup", springBack);
  document.addEventListener("pointercancel", springBack);
  // on the strip: left/right step (shift: a quarter of the range), Home
  // centres; up/down are the arrow bend below
  document.addEventListener("keydown", (e) => {
    if (e.target.id !== "bend-strip") return;
    const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (e.key === "Home") setWheel(0);
    else if (d) setWheel(wheel + d * (e.shiftKey ? 0.25 : 0.1));
    else return;
    e.preventDefault();
  });

  // ---- arrow bend: hold up/down and the wheel glides toward the top/bottom
  // over "rise", let go and it glides home over "release" (keys.js
  // bendStep: steady, or "ease" speeding up the longer it runs). "always":
  // any time the keyboard is on the page, except where arrows already mean
  // something (fields, sliders, menus, the XY pad, the song grid); "strip":
  // only while the bend strip has focus. Per browser, like typing/hold.
  const BEND_SET = { arrows: "always", rise: 300, release: 200, curve: "linear" };
  const bendSet = { ...BEND_SET };
  for (const k in BEND_SET) {
    try { const v = localStorage.getItem(`synthlab.bend.${k}`); if (v !== null) bendSet[k] = typeof BEND_SET[k] === "number" ? Number(v) || 0 : v; } catch {}
  }
  // (the dock on every page: and the other pages' arrows — the
  // Library's cards, the playlist, the jam, a chord tile, the Lab's wave and
  // edges, a splitter, the drum XY, the chain browser, a tab)
  const ARROW_OWNERS = "input, select, textarea, [role=slider], [role=grid], [role=listbox], [role=menu], [role=radiogroup], [role=tab], #xypad, .vel-lane, .grid, .sound-card, " +
    ".lib-card, .arranger-grid, .jam-grid, .ctile, #lab-wave, .chop-mark, .slice-mark, .splitter, #drumxy, .fx-chains";
  const arrowsBend = (t) => !!document.getElementById("bend-strip") && (t?.id === "bend-strip" ||
    (bendSet.arrows === "always" && !isTypingTarget(t) && !t?.closest?.(ARROW_OWNERS)));
  const BEND_DIR = { ArrowUp: 1, ArrowDown: -1 };
  let bendHeld = [], glide = null; // held directions, last pressed wins
  function glideTo(dir) {
    const now = performance.now(), running = !!glide;
    glide = { dir, t0: now, last: now };
    if (!running) requestAnimationFrame(glideFrame);
  }
  function glideFrame(now) {
    const g = glide;
    if (!g) return;
    const dt = Math.max(0, now - g.last) / 1000, t = Math.max(0, now - g.t0) / 1000;
    g.last = now;
    const secs = (g.dir ? bendSet.rise : bendSet.release) / 1000;
    const w = window.synthlab.bendStep(wheel, g.dir, t, dt, secs, bendSet.curve);
    if (w !== wheel) setWheel(w);
    if (wheel === g.dir) { glide = null; return; } // there: a release restarts it
    requestAnimationFrame(glideFrame);
  }
  document.addEventListener("keydown", (e) => {
    const dir = BEND_DIR[e.key];
    if (!dir || e.ctrlKey || e.metaKey || e.altKey || !arrowsBend(e.target)) return;
    e.preventDefault(); // not the page scroll
    if (e.repeat) return;
    bendHeld = bendHeld.filter((d) => d !== dir).concat(dir);
    glideTo(dir);
  });
  const bendLetGo = (dir) => {
    if (!bendHeld.includes(dir)) return;
    bendHeld = bendHeld.filter((d) => d !== dir);
    glideTo(bendHeld.at(-1) ?? 0);
  };
  document.addEventListener("keyup", (e) => { if (BEND_DIR[e.key]) bendLetGo(BEND_DIR[e.key]); });
  // every note off, latched ones too: ■ / Shift+Space (transport.js stopAll)
  window.synthlab.keysOff = () => {
    Object.assign(latch, { armed: false, until: 0 });
    for (const tok of [...voices.keys()]) release(tok);
    paintLatch();
    if (voiceNode) voiceNode.port.postMessage({ type: "allOff" });
  };
  window.addEventListener("blur", () => { if (bendHeld.length) { bendHeld = []; glideTo(0); } });
  function syncBendSet() {
    for (const k in BEND_SET) {
      const el = document.getElementById(`bend-${k}`);
      if (el && document.activeElement !== el) el.value = String(bendSet[k]);
      if (el?.type === "range") el.style.setProperty("--fill", `${(bendSet[k] / el.max) * 100}%`);
      const out = document.getElementById(`bend-${k}-out`);
      if (out) out.textContent = `${bendSet[k]} ms`;
    }
  }
  document.addEventListener("input", (e) => {
    const k = /^bend-(arrows|rise|release|curve)$/.exec(e.target.id || "")?.[1];
    if (!k) return;
    bendSet[k] = typeof BEND_SET[k] === "number" ? Number(e.target.value) || 0 : e.target.value;
    try { localStorage.setItem(`synthlab.bend.${k}`, String(bendSet[k])); } catch {}
    syncBendSet();
  });
  document.addEventListener("htmx:after:swap", syncBendSet);
  document.addEventListener("DOMContentLoaded", syncBendSet);
  // record bend: one loop of the wheel, from now, as the pitch_bend curve
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("#bend-rec");
    if (!btn) return;
    if (bendRec) { clearInterval(bendRec.timer); bendRec = null; btn.setAttribute("aria-pressed", "false"); return; }
    const S = window.synthlab, dur = S.loopDuration?.();
    if (!S.isPlaying?.() || !dur) { S.toast("Start the loop (▶) to record a bend across it", "error"); return; }
    // the bend, and the mod wheel and aftertouch when they move in the take
    const ctx = S.getCtx(), t0 = ctx.currentTime, took = { pitch_bend: [], mod_wheel: [], aftertouch: [] };
    const take = () => {
      const t = ctx.currentTime, at = (((t - S.loopStart()) / dur) % 1 + 1) % 1;
      took.pitch_bend.push([at, (wheel + 1) / 2]); took.mod_wheel.push([at, mods.mod_wheel]); took.aftertouch.push([at, mods.aftertouch]);
      if (t - t0 < dur) return;
      clearInterval(bendRec.timer); bendRec = null; btn.setAttribute("aria-pressed", "false");
      for (const [param, pts] of Object.entries(took)) {
        if (param !== "pitch_bend" && pts.every((q) => q[1] === pts[0][1])) continue;
        pts.sort((a, b) => a[0] - b[0]);
        const keep = pts.filter((q, i) => i === 0 || i === pts.length - 1 || q[1] !== pts[i - 1][1] || q[1] !== pts[i + 1][1]);
        keep.unshift([0, keep[0][1]]); keep.push([1, keep[keep.length - 1][1]]);
        window.synthlab.post(`/fragment/curve/${param}/set`, { points: JSON.stringify(keep.slice(0, 512)) });
      }
    };
    bendRec = { timer: setInterval(take, Math.max(30, dur * 1000 / 400)) };
    btn.setAttribute("aria-pressed", "true"); take();
  });
  document.addEventListener("change", (e) => {
    if (e.target.id !== "bend-range") return;
    window.synthlab.post("/fragment/bend-range", { value: e.target.value });
    if (patch) { patch.bend_range = Number(e.target.value); setWheel(wheel); }
  });
  document.addEventListener("htmx:after:swap", syncBend);

  // ---- drum lab hit pads: click plays the current-params one-shot ----
  document.addEventListener("click", (e) => {
    const pad = e.target.closest(".hit-pad");
    if (!pad || !pad.dataset.hitUrl) return;
    window.synthlab.playUrl(pad.dataset.hitUrl).catch((err) => window.synthlab.toast(String(err.message || err), "error"));
  });
})();
