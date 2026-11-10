// synthlab/studio/static/keyrec.js
// ⏺ Rec in the keyboard dock (Synths, Alt+R): record what you play over the
// loop as a take — the keys alone, in time, with the notes.
// - What it hears: the KEY BUS (keys.js's keyTap: every key, chord, pad
//   of the keyboard, MIDI note and live FX lane — or the talkbox's voice
//   while it speaks for the keys, S.talkbox.out), never the beat. "+ beat"
//   (#key-beat, per browser: synthlab.keyRecBeat, off) takes the master
//   bus instead.
// - When: the loop playing (S.isPlaying), a press arms it and the take
//   starts on the loop's next bar line (S.nextBar, at the page's tempo
//   S.tempo) — a whole bar off when count-in (#key-count,
//   synthlab.keyRecCount, on) is lit: the loop is the count-in. Nothing
//   playing: count-in blips one bar in to the speakers, never the take.
// - How long (#key-bars, synthlab.keyRecBars): 1, 2, 4 or 8 bars, then it
//   stops by itself; or "until stopped" (the default): until pressed again, rounded
//   up to the bar line (free length when nothing plays). Never past
//   limits.MAX_CLIP_SECONDS (#key-rec's data-max-clip-seconds): the bars
//   that fit; a free take limits.MAX_FREE_TAKE_SECONDS (data-max-free-seconds:
//   60 s).
// - The cut: from its first downbeat as heard — the context's time plus
//   the output latency, as samplerec.js cuts a mic take — for exactly its
//   length (silence pads a short one).
// - Its notes: piano.js calls S.keyNote(id, midi, velocity) on every note
//   and S.keyNote(id) where it ends, each stamped in heard time on the
//   take's beats: [start, length, midi, velocity], from its first downbeat.
// - Kept, as a take AND a loop: POST /take/keys (the WAV, the
//   notes, bpm, bars, keys-<time>, beat) keeps the clip and makes its loop
//   (loops.py); the Loops strip at the head of the dock (region key-takes)
//   lists it, by its watch. A bar is the loop's meter's (S.barBeats). REC as
//   the dock had it: the button red, "■ m:ss"; the top
//   dock's "● Recording" (a transport layer: ■ / Shift+Space stops it).
//   Stopped before its first bar line: nothing was taken. A page change
//   ends it as ■ does — kept, on the bar line ahead, holding only the
//   notes played before the change; armed and not yet started, it is
//   cancelled: a recording does not continue across pages, though the
//   dock is every page's.
// - (▶ on a loop or a take, in time, is loopspanel.js's: every Loops
//   list's .lp-play.)
// - Into the selected slot: with a slot of a lane of notes selected on the Loops page's
//   launcher (slots.js `S.slotSel`), the take lands there — an empty slot
//   a NEW loop, a filled one by #key-how (synthlab.keyRecHow): OVERDUB
//   (merged into its loop from the bar the take began on: `S.loopBar`) or
//   REPLACE — and plays from it (`slotrec.py`; the post carries lane, row,
//   how, bar). The take is still its audio and its own loop. #key-how
//   shows only while a slot is selected (Perform); a beat's slot or none:
//   a free take. The answer's HX-Trigger (fetch: not htmx's) is
//   dispatched here — the queued launch, its toast.
// - ⟲ Capture (#key-capture, Alt+T): every note the keys sound is kept in
//   a buffer here, Rec or not (the dock is outside #view: it survives a
//   page change), in loop time — what the ear was at, the arp's own notes
//   when they are due (piano.js flags them: `own`). Capture posts the last
//   #key-bars bars (1/2/4/8: up to the bar line after the newest note) or,
//   "to ■", the latest phrase (back to a bar of silence) from the bar line
//   it began on, rounded up to 1/2/4/8 bars (`captureWindow`, pure) —
//   POST /take/keys/capture: a loop with no audio at the page's tempo, into
//   the selected slot as a take is. Bar lines are the loop's while it
//   plays, else the phrase's first note is the downbeat. The keys arp's
//   "print" (#arp-print) is Capture of the arp's own notes.
// The clock is audio.js's (S.tempo, S.heardTime, S.outLatency, S.nextBar),
// a refusal's words S.toastOf.
// 180 -> 190: a page change ends a take as ■ does, said in a toast; the
// prefs read once and kept (not 4 localStorage reads a paint tick).
// - FREE record (#key-bars "free", or the Loops panel's
//   #lp-free-rec, which records free whatever the length says): no
//   count-in, no bar line, no length — the take starts on its FIRST NOTE
//   and ends where ■ is pressed (never past the clip cap), its audio cut
//   to that. POST /take/keys/free (the arp's notes flagged: a fifth item
//   1); the answer's `free-shape` opens the shaper (shaper.js).
// Budget: <= 260 code lines (110 -> 180: the key bus, takes
// on the loop's bars, their notes, ▶ in time — kept in this island;
// 190 -> 250: Rec into the selected slot, ⟲ Capture, the arp's
// print; 250 -> 260: free record — a take is this island's).

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  // ⟲ Capture's window (pure; tests/studio/test_key_takes.py runs it under
  // node): `notes` [[start, end]] in seconds, `bar` seconds, `floor(t)` the
  // bar line at or before t (null: free — the phrase's first note is the
  // downbeat), `want` 1/2/4/8 bars or 0 (the latest phrase, from its bar
  // line, rounded up to 1/2/4/8). {start, bars}. A note up to half a
  // 16th early sits on its bar line.
  function captureWindow(notes, bar, floor, want) {
    const all = [...notes].sort((a, b) => a[0] - b[0]), tol = bar / 32;
    const reach = []; // the latest end up to each note
    all.forEach((n, i) => reach.push(Math.max(n[1], i ? reach[i - 1] : -Infinity)));
    let lo = all[all.length - 1][0]; // back to a bar of silence before the phrase
    for (let i = all.length - 2; i >= 0 && reach[i] > lo - bar; i--) lo = all[i][0];
    const line = floor || ((t) => lo + Math.floor((t - lo) / bar + 1e-9) * bar);
    const end = line(all[all.length - 1][0] + tol) + bar;
    if (want) return { start: end - want * bar, bars: want };
    const n = Math.max(1, Math.round((end - line(lo + tol)) / bar)), bars = [1, 2, 4, 8].find((b) => b >= n) || 8;
    return { start: n > 8 ? end - 8 * bar : line(lo + tol), bars };
  }
  S.keyCapture = { captureWindow };
  if (typeof document === "undefined") return;
  const $ = (id) => document.getElementById(id);
  const PREFS = { count: "synthlab.keyRecCount", bars: "synthlab.keyRecBars", beat: "synthlab.keyRecBeat", how: "synthlab.keyRecHow" };
  // read once, then kept here: paint() runs every 200 ms while a take records
  const prefs = { count: "1", bars: "0", beat: "0", how: "overdub" };
  for (const k in PREFS) { try { prefs[k] = localStorage.getItem(PREFS[k]) ?? prefs[k]; } catch { /* blocked */ } }
  const keep = (k, v) => { prefs[k] = v; try { localStorage.setItem(PREFS[k], v); } catch { /* private mode */ } };
  const counts = () => prefs.count !== "0";
  const withBeat = () => prefs.beat === "1";
  const wanted = () => ({ 1: 1, 2: 2, 4: 4, 8: 8 })[prefs.bars] || 0; // 0: until stopped
  const free = () => prefs.bars === "free"; // from the first note to ■: no bars
  const bpm = () => Math.min(200, Math.max(40, S.tempo() || 120)); // limits.BPM_RANGE
  const cap = (r = rec) => (r?.free ? Number($("key-rec")?.dataset.maxFreeSeconds) || 60 : Number($("key-rec")?.dataset.maxClipSeconds) || 30);
  const ms = (t) => Math.max(0, (t - S.getCtx().currentTime) * 1000);
  const r3 = (x) => Math.round(x * 1000) / 1000;
  const pad2 = (n) => String(n).padStart(2, "0");
  const clock = (s) => `${Math.floor(s / 60)}:${pad2(Math.floor(s % 60))}`;
  const stamp = (d = new Date()) => `keys-${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}-${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`;
  // where a take lands: the launcher's selected slot of a lane of notes (slots.js), else nowhere (a free take)
  function into() {
    const sel = S.slotSel?.();
    if (!sel || sel.kind === "beat") return null;
    return { ...sel, how: sel.filled ? (prefs.how === "replace" ? "replace" : "overdub") : "new" };
  }
  const slotFields = (to, bar) => (to ? { lane: String(to.lane), row: String(to.row), how: to.how, bar: String(bar || 0) } : {});
  let rec = null; // the running take: { at (its first downbeat, heard time), beat, bar, lag, bars, len, most, ... }
  const barAt = (t, lead) => S.nextBar(t, lead); // the loop's next bar line (audio.js: the one clock)
  const heardIn = (r) => S.getCtx().currentTime - r.lag - r.at; // seconds into the take, as heard

  // ---- what the dock and the read-out show --------------------------------------
  function label(r) {
    if (r.at == null) return "⏺ free: play"; // free, before its first note
    const t = heardIn(r);
    if (t < 0) return `count-in ${Math.ceil(-t / r.beat - 1e-6)}`;
    const bar = r.grid ? ` · bar ${Math.floor(t / r.bar) + 1}${r.bars ? `/${r.bars}` : ""}` : "";
    return `■ ${clock(t)}${bar}`;
  }
  function paint() {
    const b = $("key-rec");
    if (b) {
      b.setAttribute("aria-pressed", String(!!rec));
      const text = rec ? label(rec) : "⏺ Rec";
      if (b.textContent !== text) b.textContent = text;
    }
    const f = $("lp-free-rec"); // the Loops panel's ⏺ free
    if (f) {
      f.setAttribute("aria-pressed", String(!!rec?.free));
      const text = rec?.free ? label(rec) : "⏺ free";
      if (f.textContent !== text) f.textContent = text;
    }
    const to = rec ? rec.to : into(), how = $("key-how");
    if (how) {
      how.hidden = !to;
      for (const x of how.querySelectorAll("[data-key-how]")) { // an empty slot: "new" alone; a filled one: overdub | replace
        const v = x.dataset.keyHow;
        x.setAttribute("aria-pressed", String(!!to && v === to.how));
        x.hidden = !!to && (v === "new") === to.filled;
      }
    }
    $("key-count")?.setAttribute("aria-pressed", String(counts()));
    $("key-beat")?.setAttribute("aria-pressed", String(withBeat()));
    const sel = $("key-bars"), len = free() ? "free" : String(wanted());
    if (sel && sel.value !== len) sel.value = len;
    $("now-playing")?.toggleAttribute("data-rec", !!rec);
  }
  let ticker = 0;
  const ticking = () => { ticker ||= setInterval(() => { if (!rec) { clearInterval(ticker); ticker = 0; } paint(); }, 200); };

  function blip(r, t, accent) {
    const c = S.getCtx(), o = c.createOscillator(), g = c.createGain();
    o.frequency.value = accent ? 1760 : 1100;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(accent ? 0.3 : 0.2, t + 0.003);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.045);
    o.connect(g).connect(c.destination); // the speakers, not the master: never in the take
    o.start(t); o.stop(t + 0.05);
    r.nodes.push(g);
  }

  // ---- a take -----------------------------------------------------------------------
  function begin(asFree) {
    S.ensureCtx();
    const c = S.getCtx(), loose = asFree || free(); // free: from the first note, no bars
    c.resume?.(); // a click is a gesture: a suspended context records silence
    const beat = 60 / bpm(), bar = (S.barBeats?.() || 4) * beat, lag = S.outLatency(); // a bar of the loop's meter
    // the loop's next bar line, as heard (a bar later with count-in); nothing playing: now, or a bar of blips
    const line = S.isPlaying() && !loose ? barAt(S.heardTime() + (counts() ? bar : 0), 0.05) : null;
    let at = loose ? null : line ?? S.heardTime() + 0.05, lead = 0;
    if (line == null && counts() && !loose) { lead = bar; at = c.currentTime + 0.1 + lead; }
    const most = Math.max(1, Math.floor((cap() - 0.05) / bar)), bars = wanted() && !loose ? Math.min(wanted(), most) : 0;
    const src = withBeat() ? S.getMaster() : S.talkbox?.out?.() || S.keyTap();
    const tap = c.createMediaStreamDestination(), media = new MediaRecorder(tap.stream);
    src.connect(tap);
    const r = (rec = { at, beat, bar, lag, bars, most, len: bars * bar, grid: line != null || !!bars, beatToo: withBeat(),
      src, tap, media, began: c.currentTime, chunks: [], notes: [], held: new Map(), timers: [], nodes: [],
      view: $("view")?.dataset.view, to: loose ? null : into(), free: loose }); // (a free take's slot is Make loop's)
    r.from = r.to && line != null ? S.loopBar?.(at) : 0; // the loop's bar the take begins on (an overdub folds from it)
    media.ondataavailable = (e) => r.chunks.push(e.data);
    media.onstart = () => { r.began = c.currentTime; };
    media.start();
    for (let k = 0; lead && k < 4; k++) blip(r, at - lead + k * beat, k === 0);
    const into_ = () => (r.at == null ? 0 : Math.max(0, heardIn(r))); // (free: 0 until its first note)
    r.spec = { kind: "● Recording", layer: true, at: (at ?? c.currentTime) + lag, beat, stop: () => press(r),
      label: () => clock(into_()), pos: () => ({ text: "", frac: into_() / (r.len || cap(r)) }) };
    S.transport?.claim("keyrec", r.spec);
    // a length chosen: it ends there by itself; "until stopped": at the cap (free: the cap from the press)
    if (bars) r.timers.push(setTimeout(() => finish(r), ms(at + lag + r.len + 0.25)));
    else if (loose) r.timers.push(setTimeout(() => stopAt(r), (cap(r) - 0.1) * 1000));
    else r.timers.push(setTimeout(() => stopAt(r), ms(at + lag + (r.grid ? most * bar : cap(r) - 0.05))));
    paint(); ticking();
  }

  // a press while it records: it ends on the bar line ahead (free: now)
  function stopAt(r) {
    if (rec !== r) return;
    if (r.at == null) { S.toast?.("Nothing played: a free take starts on its first note."); return finish(r, true); }
    const t = heardIn(r), lim = r.grid ? r.most * r.bar : cap(r) - 0.05;
    if (t <= 0) return finish(r, true); // before its first bar line: nothing taken
    const len = r.grid ? Math.max(1, Math.ceil(t / r.bar - 1e-3)) * r.bar : t;
    r.len = Math.min(r.len || lim, len, lim);
    r.ending = true;
    r.timers.forEach(clearTimeout);
    r.timers = [setTimeout(() => finish(r), ms(r.at + r.lag + r.len + 0.25))];
    paint();
  }
  const press = (r = rec) => (r ? stopAt(r) : begin());

  // piano.js: every note played (id, midi, velocity) and where it ends (id);
  // `at`: the context time a note is due (the keys arp posts ahead, arp.js)
  S.keyNote = (id, midi, vel, at, own) => {
    hear(id, midi, vel, at, own);
    const r = rec;
    if (!r || (r.at == null && midi === undefined)) return;
    if (r.at == null) r.at = (at || S.getCtx().currentTime) - r.lag; // free: its first note is its start
    const t = (at ? at - r.lag - r.at : heardIn(r)) / r.beat;
    if (midi === undefined) {
      const n = r.held.get(id);
      if (n) { n.end = t; r.held.delete(id); }
      return;
    }
    if (t < -0.5 || (r.len && t >= r.len / r.beat)) return; // a hair early is on the downbeat
    if (r.cut != null && t >= r.cut) return; // played after the page changed: not the take's
    const n = { start: Math.max(0, t), midi, vel: typeof vel === "number" ? Math.min(1, Math.max(0, vel)) : 1, arp: !!own };
    r.notes.push(n); r.held.set(id, n);
  };
  // ---- ⟲ Capture: every note the keys sounded lately, Rec or not --------------------
  const heard = [], sounding = new Map(); // {t, end, midi, vel, arp}, in loop time; id -> its note
  function hear(id, midi, vel, at, own) {
    const t = (at || S.getCtx().currentTime) - (own ? 0 : S.outLatency()); // the arp's own are due on the grid
    if (midi === undefined) { const n = sounding.get(id); if (n) { n.end = t; sounding.delete(id); } return; }
    const n = { t, midi, vel: typeof vel === "number" ? Math.min(1, Math.max(0, vel)) : 1, arp: !!own };
    heard.push(n); sounding.set(id, n);
    while (heard.length > 1000 || t - heard[0].t > 120) { const gone = heard.shift(); for (const [k, x] of sounding) if (x === gone) sounding.delete(k); }
  }
  async function capture(arp) {
    const now = S.heardTime(), beat = 60 / bpm(), bar = (S.barBeats?.() || 4) * beat;
    const pool = heard.filter((n) => !arp || n.arp);
    if (!pool.length) return S.toast?.(arp ? "The keys arp has played nothing to print: hold a chord with arp on (Alt+A)." : "Nothing played on the keys to capture yet.");
    const floor = S.isPlaying() ? (t) => barAt(t - bar + 1e-4, 0) : null; // the loop's bar lines
    const w = captureWindow(pool.map((n) => [n.t, n.end ?? now]), bar, floor, wanted()), end = w.start + w.bars * bar;
    const notes = pool.filter((n) => n.t >= w.start - bar / 32 && n.t < end - bar / 32).slice(-800) // limits.MAX_TAKE_NOTES
      .map((n) => [r3(Math.max(0, n.t - w.start) / beat), r3(Math.max(0.01, Math.min(n.end ?? now, end) - Math.max(n.t, w.start)) / beat), n.midi, r3(n.vel)]);
    const fields = { name: stamp(), bpm: String(r3(60 / beat)), bars: String(w.bars), notes: JSON.stringify(notes), arp: arp ? "1" : "" };
    const form = new FormData();
    for (const [k, v] of Object.entries({ ...fields, ...slotFields(into(), floor ? S.loopBar?.(w.start) : 0) })) form.append(k, v);
    await send("/take/keys/capture", form, (loop) => `${arp ? "Printed the keys arp" : "Captured"} “${loop}”: ${w.bars} bar${w.bars === 1 ? "" : "s"}, ${notes.length} note${notes.length === 1 ? "" : "s"} — in the Loops panel.`);
  }
  // a post of ours: synthlab.send (audio.js) — its OOB regions, its HX-Trigger (a slot's queued
  // launch, its toast) — and, when the answer says nothing, a toast named after what it made
  const made = (h) => { const loop = h.get("X-Loop-Name"); return loop ? decodeURIComponent(loop) : h.get("X-Clip-Name") || ""; };
  const send = (path, form, said) => S.send(path, form, { refused: "The take was refused.", done: (h) => said(made(h)) });
  function notesOf(r) {
    const beats = r.len / r.beat;
    return r.notes.filter((n) => n.start < beats).slice(0, 800) // limits.MAX_TAKE_NOTES
      .map((n) => [r3(n.start), r3(Math.max(0.01, Math.min(n.end ?? beats, beats) - n.start)), n.midi, r3(n.vel), ...(r.free && n.arp ? [1] : [])]); // (free: the arp's flagged)
  }

  async function finish(r, cancel) {
    if (rec !== r) return;
    rec = null;
    r.timers.forEach(clearTimeout); r.nodes.forEach((g) => g.disconnect());
    S.transport?.release("keyrec", r.spec);
    paint();
    const stopped = new Promise((ok) => { r.media.onstop = ok; });
    r.media.stop();
    await stopped;
    try { r.src.disconnect(r.tap); } catch { /* already gone */ }
    if (cancel || !r.len) return; // stopped before its first bar line: nothing was taken
    try {
      const c = S.getCtx(), buf = await c.decodeAudioData(await new Blob(r.chunks).arrayBuffer());
      const sr = buf.sampleRate, n = Math.round(r.len * sr), off = Math.round((r.at + r.lag - r.began) * sr);
      const cut = c.createBuffer(Math.min(2, buf.numberOfChannels), n, sr); // silence pads a short take
      for (let ch = 0; ch < cut.numberOfChannels; ch++) {
        const d = buf.getChannelData(ch);
        if (off >= 0) cut.copyToChannel(d.subarray(off, off + n), ch);
        else cut.copyToChannel(d.subarray(0, Math.max(0, n + off)), ch, -off);
      }
      await post(r, cut);
    } catch (err) { S.toast?.(String(err?.message || err), "error"); }
  }

  async function post(r, cut) {
    const form = new FormData(), asked = stamp(), bars = r3(r.len / r.bar), notes = notesOf(r);
    const fields = { name: asked, bpm: String(r3(60 / r.beat)), bars: String(bars), notes: JSON.stringify(notes), beat: r.beatToo ? "1" : "" };
    // (a page change ended it: the slot is the Loops page's, so it lands free)
    for (const [k, v] of Object.entries({ ...fields, ...(r.left ? {} : slotFields(r.to, r.from)) })) form.append(k, v);
    form.append("file", S.wavEncode(cut, 2, cap(r)), "take.wav");
    const tenths = Math.round(bars * 10) / 10; // "0:03 · 1.3 bars", as the strip lists it
    await send(r.free ? "/take/keys/free" : "/take/keys", form, (name) => `${r.left ? "The page changed, so ⏺ Rec stopped: took" : "Took"} “${name || asked}”: ${clock(Math.round(r.len))} · ${tenths} bar${tenths === 1 ? "" : "s"}, ${notes.length} note${notes.length === 1 ? "" : "s"} — its audio and a loop, in the Loops panel: Edit it on the Loops page, or → Song.`);
  }

  document.addEventListener("click", (e) => {
    const t = e.target;
    if (!(t instanceof Element)) return;
    if (t.closest("#key-rec")) return press();
    if (t.closest("#lp-free-rec")) return rec ? press() : begin(true); // the Loops panel's: free, whatever the length says
    if (t.closest("#key-count")) { keep("count", counts() ? "0" : "1"); return paint(); }
    if (t.closest("#key-beat")) { keep("beat", withBeat() ? "0" : "1"); return paint(); }
    const how = t.closest("#key-how [data-key-how]");
    if (how && how.dataset.keyHow !== "new" && !rec) { keep("how", how.dataset.keyHow); return paint(); }
    if (t.closest("#key-capture")) return void capture(false);
    if (t.closest("#arp-print")) return void capture(true);
  });
  document.addEventListener("slot-selected", paint);
  document.addEventListener("change", (e) => { if (e.target?.id === "key-bars") { keep("bars", e.target.value); paint(); } });
  // Alt+R: the dock's ⏺ Rec from anywhere on the page but a field
  // Alt+T: ⟲ Capture (Alt+C is the jam's capture on Song; Alt+Shift switches Windows' input)
  window.addEventListener("keydown", (e) => {
    if (!["KeyR", "KeyT"].includes(e.code) || !e.altKey || e.ctrlKey || e.metaKey || e.repeat || S.isTypingTarget?.(e.target)) return;
    if (e.code === "KeyR" ? !$("key-rec") && !rec : !$("key-capture")) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (e.code === "KeyR") press(); else capture(false);
  }, true);
  // a page change ends the take as ■ would: kept, on the bar line ahead, with
  // the notes played before the change only
  function left() {
    const r = rec, view = $("view")?.dataset.view;
    if (!r || r.ending || !view || view === r.view) return;
    if (r.at == null) { S.toast?.("The page changed before a note: the free take took nothing."); return void finish(r, true); }
    r.left = true; // said again with the take's own toast
    r.cut = Math.max(0, heardIn(r)) / r.beat;
    S.toast?.(heardIn(r) > 0 ? "The page changed: ⏺ Rec stops on the bar line, and the take is kept."
      : "The page changed before ⏺ Rec's first bar: nothing taken.");
    stopAt(r);
  }
  document.addEventListener("htmx:after:settle", () => { left(); paint(); });
  document.addEventListener("DOMContentLoaded", paint);
})();
