// synthlab/studio/static/deckhid.js
// The Elgato Stream Deck + over WebHID: the one file that knows its bytes.
// Written from the protocol as the open-source hosts document it
// (python-elgato-streamdeck's StreamDeckPlus.py, node-elgato-stream-deck's
// gen2 input service — both MIT; nothing of theirs is copied or vendored):
//   in  (report 1, offsets after the id): [0]=0 keys, 8 bytes from [3];
//       [0]=2 strip, [3]=1 tap / 2 long / 3 swipe, x LE16 @5, y @7, end @9, @11;
//       [0]=3 dials, [3]=0 press (a byte each from [4]) / 1 turn (int8 each);
//   out (report 2, 1023 bytes after the id): a key's JPEG in pages
//       [07, key, last, len LE16, page LE16] + 1016; a strip region's in
//       [0c, x, y, w, h (LE16 each), last, page LE16, len LE16, 00] + 1008;
//   feature (report 3): [08, percent] brightness, [02] back to the logo.
// A report is DEVICE DATA: every read is length-checked, an unknown type is
// ignored, and nothing a handler throws leaves onReport.
// It owns no surface logic: it turns reports into the events deck.js
// handles ({type: keydown|keyup|dialdown|dialup|turn|tap|long|swipe}) and
// JPEG bytes into reports. `parse`, `keyPackets`, `lcdPackets` and `open`
// touch no DOM: tests/studio/test_deck_hid.py runs them under node against
// a fake HIDDevice. Nothing here runs until deckui.js's Connect is pressed.
// The writer (`open`'s `send`) is the device's queue: one image at a time,
// a waiting image replaced by a newer one of the same place, content before
// motion — so the link never backs up behind frames nobody will see.
// Budget: <= 125 code lines (110 -> 125: the queue that coalesces and puts
// a changed value first).

(() => {
  "use strict";
  const VID = 0x0fd9, PID = 0x0084, KEYS = 8, DIALS = 4, W = 800, H = 100, REPORT = 1023;
  const each = (n, f) => Array.from({ length: n }, (_, i) => f(i));

  // the bytes after the report id (a DataView) -> what they say, or null
  function parse(v) {
    if (!v || !(v.byteLength >= 4)) return null;
    const type = v.getUint8(0), sub = v.getUint8(3);
    if (type === 0x00) {
      if (v.byteLength < 3 + KEYS) return null;
      return { type: "keys", down: each(KEYS, (i) => v.getUint8(3 + i) !== 0) };
    }
    if (type === 0x03) {
      if (sub > 1 || v.byteLength < 4 + DIALS) return null;
      if (sub === 0) return { type: "press", down: each(DIALS, (i) => v.getUint8(4 + i) !== 0) };
      return { type: "turns", ticks: each(DIALS, (i) => Math.max(-127, v.getInt8(4 + i))) };
    }
    if (type === 0x02) {
      if (sub < 1 || sub > 3 || v.byteLength < (sub === 3 ? 13 : 9)) return null;
      const at = (o, max) => Math.min(max - 1, v.getUint16(o, true));
      const ev = { type: ["tap", "long", "swipe"][sub - 1], x: at(5, W), y: at(7, H) };
      return sub === 3 ? { ...ev, x2: at(9, W), y2: at(11, H) } : ev;
    }
    return null;
  }

  // JPEG bytes -> the reports that carry them, `head` bytes of header each
  function pages(bytes, head, fill) {
    const out = [], room = REPORT - head;
    if (!(bytes instanceof Uint8Array) || !bytes.length) return out;
    for (let at = 0, page = 0; at < bytes.length; page++) {
      const len = Math.min(room, bytes.length - at), buf = new Uint8Array(REPORT);
      fill(new DataView(buf.buffer), at + len >= bytes.length ? 1 : 0, page, len);
      buf.set(bytes.subarray(at, at + len), head);
      out.push(buf);
      at += len;
    }
    return out;
  }
  function keyPackets(key, bytes) {
    if (!Number.isInteger(key) || key < 0 || key >= KEYS) return [];
    return pages(bytes, 7, (v, last, page, len) => {
      v.setUint8(0, 0x07); v.setUint8(1, key); v.setUint8(2, last);
      v.setUint16(3, len, true); v.setUint16(5, page, true);
    });
  }
  function lcdPackets(x, y, w, h, bytes) {
    if (![x, y, w, h].every(Number.isInteger) || x < 0 || y < 0 || w < 1 || h < 1 || x + w > W || y + h > H) return [];
    return pages(bytes, 15, (v, last, page, len) => {
      v.setUint8(0, 0x0c);
      [x, y, w, h].forEach((n, i) => v.setUint16(1 + 2 * i, n, true));
      v.setUint8(9, last); v.setUint16(10, page, true); v.setUint16(12, len, true);
    });
  }
  const feature = (...bytes) => { const b = new Uint8Array(31); b.set(bytes); return b; };

  // Open a device and speak to it. `emit` gets one event per change: a
  // keys report is the state of all eight, so edges are found here.
  async function open(device, emit) {
    if (!device.opened) await device.open();
    let keys = [], dials = [];
    const edges = (now, was, down, up) => now.forEach((d, i) => { if (d !== !!was[i]) emit({ type: d ? down : up, index: i }); });
    const onReport = (e) => {
      try {
        const ev = e.reportId === 1 ? parse(e.data) : null;
        if (!ev) return;
        if (ev.type === "keys") { edges(ev.down, keys, "keydown", "keyup"); keys = ev.down; }
        else if (ev.type === "press") { edges(ev.down, dials, "dialdown", "dialup"); dials = ev.down; }
        else if (ev.type === "turns") ev.ticks.forEach((t, i) => { if (t) emit({ type: "turn", index: i, ticks: t }); });
        else emit(ev);
      } catch { /* a report never throws out of its handler */ }
    };
    device.addEventListener("inputreport", onReport);
    // ONE writer, and a queue that never grows: the reports of one image never
    // interleave with another's; an image still waiting for the link is
    // REPLACED by a newer one of the same key, or of a strip region it
    // overlaps (it would be drawn over: after a layout change the caller sends
    // regions that cover the strip); an URGENT image (its content changed)
    // goes before the waiting motion frames. So the queue holds at most one
    // image a key and a region, and a dial's new value waits for one image
    // at most, never for a backlog of motion.
    const queue = new Map();
    let writing = null, closed = false;
    async function drain() {
      await null; // always later: `writing` is set before the loop can end
      while (queue.size && !closed) {
        let next = queue.keys().next().value;
        for (const [k, e] of queue) if (e.urgent) { next = k; break; }
        const { packets } = queue.get(next);
        queue.delete(next);
        try { for (const p of packets) if (!closed) await device.sendReport(2, p); } catch { queue.clear(); } // unplugged
      }
      writing = null;
    }
    function send(target, packets, urgent, x0 = 0, x1 = 0) {
      if (closed || !packets.length) return writing || Promise.resolve();
      let hot = !!urgent; // what it replaces was urgent: so is it (it carries that content too)
      for (const [k, e] of queue) if (k === target || (x1 && e.x1 && e.x0 < x1 && x0 < e.x1)) { hot ||= e.urgent; queue.delete(k); }
      queue.set(target, { packets, urgent: hot, x0, x1 });
      return (writing ||= drain());
    }
    await device.sendFeatureReport(3, feature(0x08, 70));
    return {
      name: device.productName || "Stream Deck +",
      setKey: (i, bytes, urgent) => send(`k${i}`, keyPackets(i, bytes), urgent),
      setStrip: (x, y, w, h, bytes, urgent) => send(`s${x}`, lcdPackets(x, y, w, h, bytes), urgent, x, x + w),
      backlog: () => queue.size, // images waiting (not the one going out)
      async close() {
        device.removeEventListener("inputreport", onReport);
        closed = true; // a frame still being drawn sends nothing more
        queue.clear();
        await writing;
        try { await device.sendFeatureReport(3, feature(0x02)); await device.close(); } catch { /* unplugged */ }
      },
    };
  }

  const hid = () => globalThis.navigator?.hid;
  const filters = [{ vendorId: VID, productId: PID }];
  window.synthlab ??= {};
  window.synthlab.deckHid = {
    VID, PID, parse, keyPackets, lcdPackets, open,
    // why the page cannot open a device, or "" when it can try
    why: () => (hid() ? "" : globalThis.isSecureContext === false
      ? "this page is not a secure context (plain http on the network): open the studio on the deck's own machine at http://127.0.0.1, or use MIDI learn"
      : "this browser has no WebHID: use Chrome or Edge, or MIDI learn"),
    request: async () => (await hid().requestDevice({ filters }))[0] || null, // needs a click; shows the browser's chooser
    granted: async () => (hid() ? (await hid().getDevices()).find((d) => d.vendorId === VID && d.productId === PID) || null : null),
  };
})();
