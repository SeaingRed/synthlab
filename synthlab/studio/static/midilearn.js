// synthlab/studio/static/midilearn.js
// MIDI learn: any CC onto a dial slot of the control surface (deck.js) or
// onto one control of the page. The path that needs no exclusive access to
// a device: a Stream Deck + whose Elgato app maps its dials to CCs through
// a virtual port, or any other controller's knobs.
//   - arm(to, name), then the next CC that is not reserved binds;
//   - a binding is {ch, cc, to, name, mode}: `to` is "slot:0".."slot:3",
//     "id:<element id>" or "name:<field name>"; `mode` is absolute
//     (0..127 = the control's range) or one of three relative encodings —
//     two's complement (1 / 127), binary offset (65 / 63), sign-magnitude
//     (1 / 65) — picked by hand: a guess from the first values is wrong
//     as often as right;
//   - CC 1 (mod wheel), 64 (sustain) and 120-127 (channel mode) are
//     midi.js's and are never bound or consumed;
//   - a CC already bound moves to its new target.
// A bound CC drives its target through deck.js (`setEl` / `nudge` / `turn`),
// whose every write is tweak.js's `synthlab.tweak`: a CC, a dial and a hand
// take one path. "Learn MIDI" / "Forget MIDI" on a control's own menu
// (deckui.js) call `arm` and `forget`; "MIDI learn these" on a section's
// menu calls `learn(list)`, a walk: each CC binds the next control, until
// the last, or `stop()` / any `arm`.
// midi.js hands every message to feed() first (one line there); nothing
// here asks for MIDI access. The map is per browser: localStorage
// `synthlab.midimap`, cleaned on load. `ticks` and `feed` run under node
// (tests/studio/test_deck_hid.py).
// Budget: <= 80 code lines.

(() => {
  "use strict";
  window.synthlab ??= {};
  const S = window.synthlab, STORE = "synthlab.midimap", MODES = ["abs", "rel2c", "relbin", "relsign"];
  const reserved = (cc) => cc === 1 || cc === 64 || cc >= 120;
  const byte = (n, max) => Number.isInteger(n) && n >= 0 && n <= max;
  const valid = (b) => !!b && byte(b.ch, 15) && byte(b.cc, 127) && !reserved(b.cc)
    && /^(slot:[0-3]|(id|name):[\w.-]{1,64})$/.test(b.to) && MODES.includes(b.mode);
  const clean = (list) => (Array.isArray(list) ? list.filter(valid).slice(0, 64)
    .map((b) => ({ ch: b.ch, cc: b.cc, to: b.to, mode: b.mode, name: String(b.name ?? b.to).slice(0, 40) })) : []);
  let map = [], armed = null, queue = []; // queue: the rest of a section's walk ("MIDI learn these")
  try { map = clean(JSON.parse(globalThis.localStorage?.getItem(STORE) || "[]")); } catch { /* blocked or mangled */ }
  function changed() {
    try { globalThis.localStorage?.setItem(STORE, JSON.stringify(map)); } catch { /* blocked */ }
    globalThis.document?.dispatchEvent(new CustomEvent("midimap-changed"));
  }

  // a relative encoder's value -> signed ticks
  function ticks(mode, v) {
    if (mode === "rel2c") return v < 64 ? v : v - 128;
    if (mode === "relbin") return v - 64;
    if (mode === "relsign") return v & 0x40 ? -(v & 0x3f) : v & 0x3f;
    return 0;
  }
  function apply(b, v) {
    const [kind, key] = b.to.split(":"), deck = S.deck, t = ticks(b.mode, v);
    if (!deck) return;
    if (kind === "slot") {
      if (b.mode !== "abs") return deck.turn(Number(key), t, false);
      return deck.setEl(deck.slots()[Number(key)], v / 127);
    }
    const el = deck.find(b.to);
    if (!el) return; // not on this page
    if (b.mode === "abs") deck.setEl(el, v / 127); else deck.nudge(el, t);
    deck.refresh();
  }
  // every MIDI message (midi.js): true when it was a bound or learned CC
  function feed(status, cc, v) {
    if ((status & 0xf0) !== 0xb0 || !byte(cc, 127) || !byte(v, 127)) return false;
    const ch = status & 0x0f;
    if (armed) {
      if (reserved(cc)) { S.toast?.(`CC ${cc} is reserved (mod wheel, sustain, channel mode): move another knob`, "error"); return false; }
      map = map.filter((b) => !(b.ch === ch && b.cc === cc));
      map.push({ ch, cc, to: armed.to, name: armed.name, mode: "abs" });
      armed = queue.shift() || null; // a walk: the next control waits for the next knob
      changed();
      return true;
    }
    const b = reserved(cc) ? null : map.find((m) => m.ch === ch && m.cc === cc);
    if (!b) return false;
    try { apply(b, v); } catch { /* a port's bytes never break the page */ }
    return true;
  }

  S.midiLearn = {
    feed, ticks, MODES, reserved,
    list: () => map.map((b) => ({ ...b })),
    armed: () => armed,
    arm(to, name) { queue = []; armed = armed?.to === to ? null : valid({ ch: 0, cc: 0, to, mode: "abs" }) ? { to, name: String(name || to).slice(0, 40) } : null; changed(); },
    // a section's dials, one after another: [[to, name], ...]; each CC binds the next ("2 of 5")
    learn(list) {
      queue = list.filter(([to]) => valid({ ch: 0, cc: 0, to, mode: "abs" })).slice(0, 64).map(([to, name], i, all) => ({ to, name: String(name || to).slice(0, 40), n: `${i + 1} of ${all.length}` }));
      armed = queue.shift() || null;
      changed();
    },
    stop() { queue = []; armed = null; changed(); },
    remove(i) { map.splice(i, 1); changed(); },
    forget(to) { map = map.filter((b) => b.to !== to); changed(); }, // every CC bound to one target
    cycle(i) { if (map[i]) { map[i].mode = MODES[(MODES.indexOf(map[i].mode) + 1) % MODES.length]; changed(); } },
  };

  // learning from a control's menu (menu.js) has no panel to say so in: it
  // is said in the toast corner — a plain `.toast`, not audio.js's error one
  let learning = null;
  function say(text) {
    const p = Object.assign(document.createElement("p"), { className: "toast", role: "status", textContent: text });
    document.getElementById("toasts")?.append(p);
    setTimeout(() => p.remove(), 6000);
  }
  globalThis.document?.addEventListener("midimap-changed", () => { // (no document under node: test_deck_hid.py)
    const a = S.midiLearn.armed(), b = S.midiLearn.list().at(-1);
    if (learning && b?.to === learning.to && a !== learning) say(`CC ${b.cc} · ch ${b.ch + 1} → ${b.name}`);
    if (a && a !== learning) say(`Learning ${a.name}${a.n ? ` (${a.n})` : ""}: turn a MIDI knob (MIDI must be on)`);
    learning = a;
  });
})();
