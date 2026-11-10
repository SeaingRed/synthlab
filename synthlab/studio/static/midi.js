// synthlab/studio/static/midi.js
// Opt-in Web MIDI input. NOTHING here runs until #midi-btn is clicked:
// no requestMIDIAccess, no port listeners, no audio nodes — so the audio
// path and its latency are untouched unless MIDI is in use. Events go
// through the same noteOn/noteOff the QWERTY keys use (window.synthlab.piano).
// CC 1 is the "wheel" route source when a route
// reads it (else the cutoff sweep it always was), channel pressure the
// "aftertouch" source; poly pressure is not read.
// Budget: <= 120 code lines.

(() => {
  "use strict";
  let access = null, on = false, sustain = null; // sustain: the port whose pedal is down
  const held = new Map();      // midi note -> the port that struck it (sounding)
  const pending = new Set();   // note-offs deferred by the sustain pedal
  let modBase = 800, modOut = NaN; // the mod wheel's un-swept cutoff, and its last write

  const btn = () => document.getElementById("midi-btn");
  const piano = () => window.synthlab && window.synthlab.piano;

  function label() {
    const b = btn();
    if (!b) return;
    const n = access ? [...access.inputs.values()].length : 0;
    b.dataset.state = on ? "on" : "off";
    b.setAttribute("aria-pressed", String(on));
    (b.querySelector(".btn-label") || b).textContent = on ? `MIDI ${n}` : "MIDI"; // the label beside its icon
    b.title = on ? [...access.inputs.values()].map((i) => i.name).join(", ") || "no MIDI inputs found" : "enable MIDI input";
  }

  function noteOff(note) {
    if (sustain !== null) { pending.add(note); return; }
    held.delete(note);
    piano()?.noteOff(`midi:${note}`);
  }
  // Let go of what a port holds (no port: everything) — a keyboard that was
  // unplugged, or MIDI switched off, never sends its note-offs or pedal-up.
  function release(port) {
    if (port === undefined || sustain === port) sustain = null;
    for (const [n, from] of [...held]) {
      if (port !== undefined && from !== port) continue;
      held.delete(n); pending.delete(n);
      piano()?.noteOff(`midi:${n}`);
    }
    if (sustain === null) { for (const n of pending) noteOff(n); pending.clear(); }
  }

  function onMessage(ev) {
    const p = piano();
    if (!p || !on) return;
    const [status, d1, d2] = ev.data;
    if (window.synthlab.midiLearn?.feed(status, d1, d2)) return; // a learned CC (midilearn.js): a dial or a control
    const kind = status & 0xf0, port = ev.target?.id ?? "";
    if (kind === 0x90 && d2 > 0) {
      // the Song page's drums lane armed: pads play its kit (songtake.js, GM map)
      if (window.synthlab.drumNote?.(d1, d2 / 127)) return;
      // a re-strike of a note still sounding (its note-off deferred by the
      // pedal): piano.js ignores a note-on for a key that is already down
      if (held.has(d1)) p.noteOff(`midi:${d1}`);
      held.set(d1, port); pending.delete(d1);
      p.noteOn(`midi:${d1}`, d1, Math.max(0.05, d2 / 127));
    } else if (kind === 0x80 || (kind === 0x90 && d2 === 0)) {
      noteOff(d1);
    } else if (kind === 0xe0) {
      // pitch bend: the keyboard's one wheel (piano.js), x the patch's bend_range
      p.wheel(((d2 << 7 | d1) - 8192) / 8192);
    } else if (kind === 0xd0) {
      p.aftertouch?.(d1 / 127); // channel pressure: the "aftertouch" route source
    } else if (kind === 0xb0 && d1 === 1 && p.routes?.("wheel")) {
      p.modWheel(d2 / 127); // the mod wheel, where a route reads it
    } else if (kind === 0xb0 && d1 === 1) {
      // no route reads the wheel: it sweeps the cutoff, as it always did —
      // mod wheel -> cutoff, log-mapped from the patch's own cutoff up to
      // 8 kHz. p.mod writes the swept value into the patch (new notes read
      // it), so the base is only re-read when something else moved the
      // cutoff — a slider, a /patch.json reload; reading it back every
      // message compounded, and a sweep up and back left it at 8 kHz.
      const cur = Number(p.patch()?.cutoff) || 800;
      if (cur !== modOut) modBase = Math.max(20, cur);
      modOut = modBase * Math.pow(8000 / modBase, d2 / 127);
      p.mod("cutoff", modOut);
    } else if (kind === 0xb0 && d1 === 64) {
      sustain = d2 >= 64 ? port : null;
      if (sustain === null) { for (const n of pending) noteOff(n); pending.clear(); }
    } else if (kind === 0xb0 && (d1 === 120 || d1 === 123)) {
      for (const n of held.keys()) piano()?.noteOff(`midi:${n}`);
      held.clear(); pending.clear();
    }
  }

  function attach() {
    for (const input of access.inputs.values()) input.onmidimessage = on ? onMessage : null;
    label();
  }

  async function enable() {
    if (!access) {
      access = await navigator.requestMIDIAccess({ sysex: false });
      access.onstatechange = (e) => {
        if (e.port?.type === "input" && e.port.state === "disconnected") release(e.port.id);
        attach();
      };
    }
    on = true;
    window.synthlab.ensureCtx();
    attach();
    if (!access.inputs.size) window.synthlab.toast("MIDI enabled — no input devices found yet (plug one in)");
  }

  function disable() {
    on = false;
    release();
    if (access) attach();
    label();
  }

  document.addEventListener("click", (e) => {
    if (!e.target.closest("#midi-btn")) return;
    if (on) { disable(); return; }
    const b = btn(); if (b) { b.dataset.state = "pending"; (b.querySelector(".btn-label") || b).textContent = "MIDI…"; } // browser permission prompt may be up
    enable().catch((err) => { on = false; label(); window.synthlab.toast(`MIDI: ${err.message || err}`, "error"); });
  });
  document.addEventListener("htmx:after:swap", label);
  document.addEventListener("DOMContentLoaded", () => {
    if (!navigator.requestMIDIAccess) btn()?.setAttribute("hidden", "");
  });
})();
