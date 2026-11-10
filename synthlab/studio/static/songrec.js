// synthlab/studio/static/songrec.js
// Recording on the Song page. Part of song.js's island — split
// out so the player keeps its own budget — loaded right after it, and
// talking to it only through window.synthlab: S.songClock() (the plan, the
// scheduled passes, loop/selection, play/stop) and S.songCountIn, which
// song.js asks for the delay before the first pass.
// - Arming: one track at a time (runtime only, never persisted). ● records
//   onto the armed track, or arms the selected cell's track. The section
//   loops after a 1-bar count-in of WebAudio blips at the song tempo.
// - Notes (synth track): piano.js calls S.onNoteOn(midi, velocity) for
//   every key, pad, chord and MIDI note. Each is stamped in heard time
//   (ctx time minus output latency) from the section start; on every loop
//   wrap, and once on stop, the batch is posted to
//   /song/pattern/{id}/record, where studio/record.py quantizes it. A
//   shared pattern asks first (#rec-shared): make unique is the default.
// - Hits (the drums track): songtake.js plays the kit from the
//   keys, MIDI pads or its on-screen pads and calls S.onHit(voice, velocity);
//   they are stamped and posted like notes, onto the drums pattern.
//   songtake.js also draws the count-in (`song-countin`), clicks the beat
//   while a take runs (S.songTake()) and cleans a take up when it ends
//   (`song-take-end`, which carries the last post's promise).
// - Mic (audio track): voice.js's capture (S.voice) runs from before the
//   count-in; on stop the take is cut to start at the section's first bar
//   line — shifted earlier by the browser's input + output latency, or by
//   this machine's calibration (#song-latency) when it reports none — and
//   capped at MAX_CLIP_SECONDS (the take auto-stops there and is kept),
//   then posted to /song/take, which places it at bar 1 of the section.
//   The first time an audio track is armed, a hint suggests headphones.
// Budget: <= 200 code lines.

(() => {
  "use strict";
  window.synthlab ??= {};
  const S = window.synthlab;
  if (typeof document === "undefined") return;
  const $ = (id) => document.getElementById(id);
  const clock = () => S.songClock?.();
  const heard = () => S.heardTime();
  const toast = (m, kind) => S.toast?.(m, kind);
  let armed = null, rec = null; // rec: the running take
  const KINDS = { synth: "notes", drums: "hits", audio: "mic" }; // a track's kind -> its take's

  // ---- count-in ------------------------------------------------------------
  function blip(t, accent) {
    const c = S.getCtx(), o = c.createOscillator(), g = c.createGain();
    o.frequency.value = accent ? 1760 : 1320;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.4, t + 0.003);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.05);
    o.connect(g).connect(S.getMaster());
    o.start(t); o.stop(t + 0.06);
  }
  S.songCountIn = (at, plan, first) => {
    if (!rec || rec.at0 != null) return 0;
    const s = plan.sections[first.i], bar = s.cycle_samples / plan.sr / s.cycle_bars;
    for (let k = 0; k < 4; k++) blip(at + (k * bar) / 4, k === 0);
    Object.assign(rec, { at0: at + bar, dur: s.cycle_samples / plan.sr, bar, bars: s.bars });
    document.dispatchEvent(new CustomEvent("song-countin", { detail: { at, bar } }));
    return bar;
  };

  // ---- the shared-pattern prompt --------------------------------------------
  function ask(pid, used, tid, sid) {
    const d = $("rec-shared"), name = (el, sel) => (el?.querySelector(sel)?.firstChild?.textContent || "").trim();
    if (!d) return Promise.resolve("unique");
    d.querySelector(".rec-shared-name").textContent = $(`pool-${pid}`)?.querySelector(".pool-name")?.textContent || "this pattern";
    d.querySelector(".rec-shared-track").textContent = name($(`trk-${tid}`), ".track-name") || "this track";
    d.querySelector(".rec-shared-sec").textContent = name($(`sec-${sid}`), ".section-name") || "this section";
    d.querySelector(".rec-shared-count").textContent = String(used);
    d.returnValue = "";
    return new Promise((ok) => { d.addEventListener("close", () => ok(d.returnValue), { once: true }); d.showModal(); });
  }

  // ---- a take ----------------------------------------------------------------
  async function begin() {
    const k = clock();
    if (!k) return;
    const sid = k.sel || k.cur?.sid || document.querySelector(".arr-section")?.dataset.sectionId;
    const tid = armed || k.cur?.tid, kind = tid && $(`trk-${tid}`)?.dataset.kind;
    if (!sid || !KINDS[kind]) return toast("Arm a synth track (notes), the drums (hits) or an audio track (mic) first.", "error");
    armed = tid;
    const pid = $(`cell-${sid}-${tid}`)?.dataset.patternId || "";
    rec = { kind: KINDS[kind], sid, tid, pid, unique: "", take: Math.random().toString(36).slice(2, 12),
      mode: document.querySelector('input[name="rec_mode"]:checked')?.value || "overdub",
      grid: $("song-quantize")?.value || "1/16", at0: null, events: [], t0: 0, sent: false };
    const r = rec;
    if (r.kind !== "mic" && pid) {
      const used = new Set([...document.querySelectorAll(".arr-cell")].filter((c) => c.dataset.patternId === pid).map((c) => c.dataset.sectionId)).size;
      if (used > 1) {
        const ans = await ask(pid, used, tid, sid);
        if (!ans) { rec = null; return paint(); }
        r.unique = ans === "unique" ? "1" : "";
      }
    }
    if (r.kind === "mic" && !(await S.songMic?.open(r))) { rec = null; return paint(); }
    k.stop(); k.select(sid); k.setLoop(true);
    paint(); ticking();
    await k.play();
    if (rec === r && r.at0 == null) { rec = null; S.songMic?.cancel(r); paint(); } // the song would not start
  }

  function flush(r, t1) {
    const events = r.events.splice(0), t0 = r.t0, first = !r.sent;
    r.t0 = t1;
    if (!events.length && !(first && r.mode === "replace")) return;
    r.sent = true;
    return htmx.ajax("POST", `/song/pattern/${encodeURIComponent(r.pid || "new")}/record`, { swap: "none",
      values: { events: JSON.stringify(events), take: r.take, mode: r.mode, grid: r.grid, t0: t0.toFixed(4), t1: t1.toFixed(4),
        section_id: r.sid, track_id: r.tid, unique: r.unique } });
  }

  // Stop mid-take keeps what was captured: the last partial pass posts too
  async function finish() {
    const r = rec;
    rec = null; paint();
    if (!r) return;
    if (r.at0 == null) return S.songMic?.cancel(r);
    const t1 = Math.max(0, heard() - r.at0);
    if (r.kind === "mic") return S.songMic?.close(r, t1);
    const done = flush(r, t1);
    if (r.sent) document.dispatchEvent(new CustomEvent("song-take-end", { detail: { take: r.take, kind: r.kind, done } }));
  }

  S.onHit = (voice, vel) => {
    const r = rec;
    if (r?.kind !== "hits" || r.at0 == null) return;
    const t = heard() - r.at0;
    if (t > -r.bar / 8) r.events.push({ voice, t: Math.max(0, t), vel });
  };
  S.songTake = () => (rec && rec.at0 != null ? { at0: rec.at0, bar: rec.bar, kind: rec.kind } : null);
  S.songArmed = () => (armed ? { tid: armed, kind: $(`trk-${armed}`)?.dataset.kind } : null);

  S.onNoteOn = (midi, vel) => {
    const r = rec;
    if (r?.kind !== "notes" || r.at0 == null) return;
    const t = heard() - r.at0;
    if (t > -r.bar / 8) r.events.push({ midi, t: Math.max(0, t), vel: typeof vel === "number" ? vel : 1 });
  };

  // ---- mic takes -------------------------------------------------------------
  const capSec = () => Number($("song-rec")?.dataset.maxClipSeconds) || 30;
  function hint() {
    try { if (localStorage.getItem("synthlab.micHint")) return; localStorage.setItem("synthlab.micHint", "1"); } catch {}
    toast("Tip: wear headphones, or the mic records the speakers too.");
  }
  S.songMic = {
    get cap() { return capSec(); },
    armed(tid) { if (tid && $(`trk-${tid}`)?.dataset.kind === "audio") hint(); },
    async open(r) {
      S.ensureCtx();
      try { r.mic = await S.voice.capture(S.getCtx()); return true; } catch { toast("Microphone permission denied.", "error"); return false; }
    },
    cancel(r) { r.mic?.stop().catch(() => {}); },
    async close(r, t1) {
      const c = S.getCtx();
      try {
        const buf = await r.mic.stop(), out = S.outLatency();
        const lat = out || r.mic.input ? out + r.mic.input : (Number($("song-latency")?.value) || 0) / 1000;
        const sr = buf.sampleRate, from = Math.max(0, Math.round((r.at0 - r.mic.began + lat) * sr));
        const n = Math.min(buf.length - from, Math.round(Math.min(t1, capSec()) * sr));
        if (n <= 0) return toast("The take was empty.", "error");
        const cut = c.createBuffer(1, n, sr);
        cut.copyToChannel(buf.getChannelData(0).subarray(from, from + n), 0);
        const body = new FormData();
        for (const [k, v] of [["section_id", r.sid], ["track_id", r.tid], ["at_bar", "0"]]) body.append(k, v);
        body.append("file", S.voice.toWav(cut, 1, capSec()), "take.wav");
        await S.send("/song/take", body, { refused: "The take was refused." }); // (its song-changed and toast: what the answer triggers)
      } catch (err) { toast(String(err?.message || err), "error"); }
    },
  };

  // ---- the clock: wraps, auto-stop, the REC label ----------------------------
  function paint() {
    $("song-rec")?.setAttribute("aria-pressed", String(!!rec));
    document.querySelectorAll(".rowbtn.arm").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.arm === armed)));
    const out = $("song-rec-status");
    if (!out) return;
    let txt = "";
    if (rec && rec.at0 == null) txt = "count-in…";
    else if (rec) {
      const t = Math.max(0, heard() - rec.at0), bar = (Math.floor(t / rec.bar) % rec.bars) + 1;
      txt = `REC ${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")} · bar ${bar}/${rec.bars}`;
    }
    if (out.textContent !== txt) out.textContent = txt;
  }
  // The 50 ms clock runs only while a take is live (it used to tick on every
  // view, forever); arm buttons and the status are repainted after swaps.
  let tick = 0;
  const ticking = () => { if (rec && !tick) tick = setInterval(step, 50); };
  function step() {
    const r = rec;
    if (!r) { clearInterval(tick); tick = 0; return paint(); }
    if (r.at0 != null) {
      if (!clock()?.playing) return finish();
      const t = heard() - r.at0;
      if (r.kind !== "mic" && t >= r.t0 + r.dur) flush(r, r.t0 + r.dur);
      if (r.kind === "mic" && t >= capSec()) { toast(`The take reached the ${capSec()} s cap — stopped and kept.`); finish(); }
    }
    paint();
  }
  document.addEventListener("htmx:after:settle", paint);

  document.addEventListener("click", (e) => {
    const arm = e.target.closest(".rowbtn.arm[data-arm]");
    if (arm) {
      armed = armed === arm.dataset.arm ? null : arm.dataset.arm;
      S.songMic?.armed(armed);
      document.dispatchEvent(new CustomEvent("song-armed", { detail: S.songArmed() }));
      return paint();
    }
    if (e.target.closest('[data-song-action="record"]')) rec ? finish() : begin();
  });
})();
