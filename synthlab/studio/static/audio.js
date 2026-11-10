// synthlab/studio/static/audio.js
// The studio's core JS island. Owns: WebAudio loop playback with
// crossfade on `preview-ready`, the analyser (drawn by motion.js),
// play/stop, volume, htmx error toasts, drag-paint grids and focus
// restoration after full-body swaps, loop playhead and the Library filter.
// (Scope, spectrum, peak meter and the --live light moved to motion.js.)
// (The key voice — live key filter,
// polyphony limiter, held-note loops — moved to keys.js at 745/745.)
// 480 -> 575: peak meter + clip latch, client-side Library filtering, the
// shortcut sheet opener, and the at-rest loop waveform.
// 575 -> 580: transport truth after a view swap — the server renders every
// view's #play stopped, so switching views mid-loop showed "▶" over running
// audio; plus the dock's global stop for the views that ship no transport
// at all (Breed / Library / Map), where a loop could not be stopped.
// 580 -> 625: loopFx, the performance-FX surface over the sounding buffer
// (playbackRate ramps for tape stop / half / double, loopStart-loopEnd for
// stutter and freeze, a reversed buffer swap for reverse). It lives here
// because this island owns the AudioBufferSourceNode; perform.js owns the
// buttons and holds no audio state of its own.
// 625 -> 700: network-error surfacing + busy LED,
// same-URL swap guard, phase-continuous crossfade, log sliders, dock
// filter. All belong to the audio/htmx glue this
// island owns.
// 700 -> 745: jam capture — the master-bus tap and the
// /clip/upload push live here because this island owns `master`. (Its
// WAV encoder lives in voice.js, the codec island, to pay for the
// REC m:ss · bar n/N label and the prompt-free auto-save.)
// 724 -> 733: curve-click's x/y/w/h, moved out of
// curve.html's `hx-vals="js:{...}"` (CSP's script-src 'self' blocks htmx4
// running js: through Function()) into an htmx:config:request listener.
// 742 -> 744: the jam encoder is capped — with maxSeconds=Infinity a take
// over ~131s (BodySizeLimit's /clip/upload cap at 48kHz stereo) would be
// rejected with no client-side copy kept. Capped at limits.MAX_CLIP_SECONDS (read off #jam-rec's own
// data-max-clip-seconds attribute; 30 if that's ever missing).
// 745 -> 628 in use (budget kept at 745): the key voice (key-buffer cache,
// key bus, held-note loops, release, one-shot pads) moved to keys.js as a
// pure move; audio.js now exposes fetchBuffer for it and calls
// flushKeys / playUrl through window.synthlab (the keys level no longer
// follows the loop).
// 552 -> 594: syncControls — after every
// settle, swapped inputs take their live value/checked/selection back from
// their attributes (morph copies attributes only, so Undo / Defaults /
// Morph left thumbs stale and the next edit posted them back); --fill for
// every range in capture; a log slider's double-click posts the exact default.
// 600 -> 610: the piano roll's stroke fills between
// pointer samples (`paintTo`), and its erase tool is one more `paintVal`.
// 646 -> 677: Synths' ▶ beat — the loop's beat alone, a
// second looped player beside the loop, here because this island owns the
// context and `master`.
// 745 -> 755: `synthlab.post` (a post of just
// its values), the sounds list's roving cursor and the filter's shelf counts.
// 755 -> 762: the Library's rows by arrow, its "Clear
// filter", and a count of every row (the recipes too) — the Library's
// filter lives here, and has no island of its own; and a preview that
// names itself (`preview-ready`'s `plays`: the booth, the vocoder) is what
// Now playing calls the loop.
// (The Library's filter, chips and arrow keys are
// library.js's, its own island; a card's audition hands its buffer source on
// as `audition-start`, so the card can light while it sounds.)
// Budget: <= 762 code lines.

(() => {
  "use strict";
  const XFADE = 0.06; // seconds

  let ctx = null, analyser = null, master = null;
  let liveOutNode = null;
  let current = null;            // {src, gain}
  let pendingUrl = null, playing = false, lastUrl = null;
  let restBuffer = null, restBufUrl = null; // the last loop buffer that sounded + its URL (motion.js reuses it)


  function ensureCtx() {
    if (ctx) return;
    // At the engine's rate (core.SR): every render the server sends is
    // 44.1 kHz, and the live voice costs per sample — left to the device,
    // an interface at 192 kHz made every voice 4.35x dearer. The browser
    // resamples once at the output; a browser that refuses the rate gets
    // the device's.
    try { ctx = new AudioContext({ latencyHint: "interactive", sampleRate: 44100 }); }
    catch { ctx = new AudioContext({ latencyHint: "interactive" }); }
    master = ctx.createGain();
    analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    // Brick-wall limiter: loop (0.89 peak) + pad one-shots (0.95) + live
    // keys sum past 1.0 and hard-clip at the output as drum "static".
    const limiter = new DynamicsCompressorNode(ctx, { threshold: -1, knee: 0, ratio: 20, attack: 0.001, release: 0.05 });
    master.connect(limiter).connect(analyser).connect(ctx.destination);
    const vol = document.getElementById("volume");
    master.gain.value = vol ? Number(vol.value) : 0.8;
    setLed(true);
    // iOS hands the output to a call/Siri ("interrupted") or suspends it in
    // the background: the transport must not claim to be playing dead air.
    ctx.onstatechange = () => { if (ctx.state !== "running" && playing) stop(); };
  }
  function setLed(on) {
    const led = document.getElementById("audio-led");
    if (!led) return;
    led.dataset.state = on ? "on" : "off";
    led.setAttribute("aria-label", on ? "audio engine on" : "audio engine off");
  }

  // (`missing`: what a 404 means to the caller, as its message)
  async function fetchBuffer(url, missing) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.status === 404 && missing ? missing : `preview fetch ${res.status}`);
    return ctx.decodeAudioData(await res.arrayBuffer());
  }

  // startLoop(buffer) crossfades in now; startLoop(buffer, at) is the
  // launcher path: the new loop starts exactly where the current one wraps
  // (sample-accurate, no crossfade — both share the bar grid) and
  // `loop-switched` fires so the launcher can flip queued -> playing.
  function switched() { S().transport?.queue("loop", null); document.dispatchEvent(new CustomEvent("loop-switched")); }
  // passes: how many passes of the grid the buffer holds (a loop with chance
  // plays several, each rolled afresh: `?passes=N` on its URL, render.preview_url)
  function startLoop(buffer, at, passes = 1) {
    const src = ctx.createBufferSource(), gain = ctx.createGain();
    src.buffer = buffer; src.loop = true; src.connect(gain).connect(master);
    const now = ctx.currentTime, t = at || now, old = current;
    // A tweak always swaps a same-length re-render of the same phrase, so
    // crossfade in at the OLD loop's current phase — restarting at beat 1
    // meant you never heard the tail while tuning. Structure changes
    // (different duration) and queued launches still start clean.
    let phase = 0;
    if (old && at) {
      old.src.stop(at);
      setTimeout(() => { try { old.src.disconnect(); old.gain.disconnect(); } catch {} switched(); }, (at - now) * 1000 + 5);
    } else if (old) {
      if (Math.abs(old.duration - buffer.duration) < 1e-6) {
        phase = ((now - old.startedAt) % buffer.duration + buffer.duration) % buffer.duration;
      }
      old.gain.gain.setValueAtTime(1, now);
      old.gain.gain.linearRampToValueAtTime(0, now + XFADE);
      setTimeout(() => { try { old.src.stop(); old.src.disconnect(); old.gain.disconnect(); } catch {} }, XFADE * 1000 + 60);
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(1, now + XFADE);
    }
    src.start(t, phase);
    current = { src, gain, startedAt: t - phase, duration: buffer.duration, cycle: buffer.duration / passes };
    document.dispatchEvent(new Event("loop-clock")); // livefx.js: gate / pump / sidechain follow the loop
    restBuffer = buffer;
    reversed = null; // perf-FX reverse must mirror THIS loop, not the first one it ever built
    startPlayhead();
  }

  // ---- ▶ beat (Synths): the loop's beat alone, to play the keys over ------
  // GET /beat.wav redirects to the Drums view's render (/preview/<key>.wav).
  // A player of its own beside the loop: the transport (transport.js) stops
  // one when the other claims. The beat it plays is known by `sig`: the
  // render key the sound bar draws on #play-beat (data-beat-key, by its
  // region's watch) and the tempo (a bpm typed into the bar is a post that
  // never redraws it). Only a new sig refetches — a beat picked while it
  // plays changes on its own wrap, as a launch lands on the bar; any other
  // edit asks nothing (every preview-ready used to fetch /beat.wav).
  let beat = null; // {src, t0, dur, spec, sig}
  const beatSig = () => `${document.getElementById("play-beat")?.dataset.beatKey || ""}@${tempo()}`;
  function beatStop() {
    const b = beat; beat = null;
    try { b?.src.stop(); } catch { /* ended */ }
    document.getElementById("play-beat")?.setAttribute("aria-pressed", "false");
    if (b) S().transport?.release("beat", b.spec);
  }
  const beatPos = () => (beat ? posAt(beat.t0, beat.dur) : null);
  async function beatPlay(start) {
    ensureCtx(); await ctx.resume();
    const sig = beatSig(), buf = await fetchBuffer("/beat.wav", "The loop has no beat yet: pick one.");
    if (!start && !beat) return; // stopped meanwhile
    const src = ctx.createBufferSource(), now = ctx.currentTime, old = beat;
    src.buffer = buf; src.loop = true; src.connect(master);
    const at = old ? old.t0 + Math.ceil((now + 0.03 - old.t0) / old.dur) * old.dur : now;
    old?.src.stop(at); src.start(at);
    const spec = old?.spec || { kind: "Beat", label: () => document.getElementById("play-beat")?.dataset.plays || "the beat", stop: beatStop, pos: beatPos };
    beat = { src, t0: at, dur: buf.duration, spec, sig: old?.sig ?? sig };
    document.getElementById("play-beat")?.setAttribute("aria-pressed", "true");
    if (!old) S().transport?.claim("beat", spec);
  }
  document.addEventListener("click", (e) => {
    if (!e.target.closest?.("#play-beat")) return;
    if (beat) S().transport?.stop("beat");
    else beatPlay(true).catch((err) => toast(String(err.message || err), "error"));
  });
  function beatFollow() {
    const sig = beatSig();
    if (!beat || beat.sig === sig) return;
    beat.sig = sig; // claimed now: a second settle before the fetch lands asks nothing
    beatPlay(false).catch(() => {});
  }
  document.addEventListener("preview-ready", beatFollow);
  document.addEventListener("htmx:after:settle", () => {
    document.getElementById("play-beat")?.setAttribute("aria-pressed", String(!!beat));
    beatFollow();
  });

  // ---- jam capture -------------------------------------------------------
  // Record the master bus — loop, live keys AND performance FX, exactly as
  // performed — into the clip store. Perf FX stay client-side by design;
  // this is how a tape-stop becomes loop material. The take is kept the
  // moment it stops, as jam-YYYYmmdd-HHMMSS (renamed later, inline on the
  // clip library): it used to stop into window.prompt, whose Cancel silently
  // threw it away. While it records, the button reads REC m:ss · bar n/N —
  // it used to look idle. voice.js (the codec island) encodes the WAV; the
  // response's OOB fragments refresh the library and the lane's clip select.
  let jamRec = null, jamTick = 0;
  const pad2 = (n) => String(n).padStart(2, "0");
  function jamLabel(t0) {
    const btn = document.getElementById("jam-rec"); // survives view swaps
    if (!btn) return;
    const sec = Math.floor(ctx.currentTime - t0);
    let txt = `⏺ REC ${Math.floor(sec / 60)}:${pad2(sec % 60)}`;
    const barSec = 240 / (tempo() || 120);
    if (current && playing) {
      const n = Math.max(1, Math.round(current.duration / barSec));
      const pos = (((ctx.currentTime - current.startedAt) % current.duration) + current.duration) % current.duration;
      txt += ` · bar ${Math.min(n, Math.floor(pos / barSec) + 1)}/${n}`;
    }
    btn.setAttribute("aria-pressed", "true");
    btn.textContent = txt;
  }
  function jamName(d = new Date()) {
    return `jam-${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}-${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`;
  }
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#jam-rec")) return;
    ensureCtx();
    ctx.resume(); // a click is a gesture: a suspended context records silence
    if (jamRec) { jamRec.stop(); return; }
    const tap = ctx.createMediaStreamDestination(), chunks = [], t0 = ctx.currentTime;
    master.connect(tap);
    jamRec = new MediaRecorder(tap.stream);
    jamRec.ondataavailable = (ev) => chunks.push(ev.data);
    jamRec.onstop = async () => {
      try { master.disconnect(tap); } catch {}
      jamRec = null;
      clearInterval(jamTick);
      const btn = document.getElementById("jam-rec"), name = jamName();
      // limits.MAX_CLIP_SECONDS, rendered onto the button so this island
      // doesn't hardcode the server's own cap; 30 if it's ever missing.
      const cap = Number(btn?.dataset.maxClipSeconds) || 30;
      if (btn) { btn.setAttribute("aria-pressed", "false"); btn.textContent = "⏺ resample"; }
      try {
        const buf = await ctx.decodeAudioData(await new Blob(chunks).arrayBuffer());
        const trimmed = buf.duration > cap;
        const form = new FormData();
        form.append("name", name);
        form.append("file", window.synthlab.wavEncode(buf, 2, cap), "jam.wav");
        await send("/clip/upload", form, { refused: "The resample was refused.", done: () => `kept "${name}"${trimmed ? ` — first ${cap}s` : ""} — rename it in Clips` });
      } catch (err) { toast(String(err.message || err), "error"); }
    };
    jamRec.start();
    jamLabel(t0);
    jamTick = setInterval(() => jamLabel(t0), 250);
  });

  // ---- performance FX on the playing loop --------------------------------
  // These manipulate the sounding AudioBufferSourceNode directly rather than
  // asking the server to re-render: a tape stop that arrives 60ms after the
  // press is not a tape stop. The trade is that they are a *performance* —
  // they are deliberately not baked into export song / export stems.
  // perform.js owns the buttons; this is the whole audio surface it needs.
  let reversed = null; // lazily-built reversed copy of the current loop
  function loopRate(value, rampSec = 0) {
    if (!current) return;
    const r = current.src.playbackRate, now = ctx.currentTime;
    r.cancelScheduledValues(now);
    r.setValueAtTime(r.value, now);
    // exponential can't reach 0, and a tape stop is an exponential slowdown;
    // ramp to a floor and let the caller stop or restore from there
    if (rampSec > 0) r.exponentialRampToValueAtTime(Math.max(value, 0.0001), now + rampSec);
    else r.setValueAtTime(Math.max(value, 0.0001), now);
  }
  // Freeze/stutter: shrink the source's own loop window to `beats` of the
  // bar, anchored at the play position. loopStart/loopEnd is sample-accurate
  // and costs nothing — no re-trigger, no click.
  function loopWindow(beats, bpm) {
    if (!current) return;
    const src = current.src;
    if (!beats) { src.loopStart = 0; src.loopEnd = current.duration; return; }
    const len = Math.max((60 / (bpm || 120)) * beats, 0.02);
    const played = (ctx.currentTime - current.startedAt) % current.duration;
    const start = Math.max(0, Math.min(played, current.duration - len));
    src.loopStart = start;
    src.loopEnd = start + len;
  }
  function loopReverse(on) {
    if (!current || !restBuffer) return;
    if (on && !reversed) {
      reversed = ctx.createBuffer(restBuffer.numberOfChannels, restBuffer.length, restBuffer.sampleRate);
      for (let c = 0; c < restBuffer.numberOfChannels; c++) {
        reversed.getChannelData(c).set(restBuffer.getChannelData(c).slice().reverse()); // typed, never boxed
      }
    }
    const buf = on ? reversed : restBuffer;
    if (!buf) return;
    // restart at the mirrored position so the bar grid still lines up
    const played = (ctx.currentTime - current.startedAt) % current.duration;
    const at = on ? current.duration - played : played;
    const src = ctx.createBufferSource();
    src.buffer = buf; src.loop = true; src.connect(current.gain);
    try { current.src.stop(); current.src.disconnect(); } catch {}
    src.start(ctx.currentTime, at);
    current = { ...current, src, startedAt: ctx.currentTime - at };
  }
  const loopFx = {
    rate: loopRate,
    window: loopWindow,
    reverse: loopReverse,
    reset() { if (!current) return; loopRate(1); loopWindow(0); },
    active: () => !!current,
  };

  // A slider's <output> chip is server-rendered, so it reads the OLD value
  // for the whole drag (or forever, when the post doesn't re-render that
  // fragment — the drum transport's swing/humanise). Mirror the macro's
  // formatting client-side: data-pct chips show whole percents, the rest
  // use the compact fmt() rules.
  function fmtNum(f) {
    if (f === Math.round(f)) return String(Math.round(f));
    for (const d of [3, 2, 1]) {
      const s = f.toFixed(d).replace(/0+$/, "").replace(/\.$/, "");
      if (s.length <= 5) return s;
    }
    return String(Math.round(f));
  }
  // log-taper sliders (data-log): the visible range is a 0..1 position; the
  // hidden field before it carries the real value the form posts.
  const logVal = (t) => (+t.dataset.min) * Math.pow((+t.dataset.max) / (+t.dataset.min), +t.value);
  window.synthlab ??= {};
  window.synthlab.logVal = logVal;
  function syncOutput(t, v = t.dataset.log ? logVal(t) : Number(t.value)) {
    const out = (t.id && document.querySelector(`output[for="${CSS.escape(t.id)}"]`))
      || t.parentElement?.querySelector?.("output");
    if (!out) return;
    const p = Math.round(v * 100), pan = p ? `${p < 0 ? "L" : "R"}${Math.abs(p)}` : "C"; // data-pan: C, L20, R20
    const s = out.hasAttribute("data-pan") ? pan : out.hasAttribute("data-pct") ? `${p}%` : fmtNum(v);
    out.textContent = out.dataset.unit ? `${s} ${out.dataset.unit}` : s;
  }

  // ---- the clock every take and launch reads: S.tempo, S.heardTime,
  // S.nextBar — one definition of each, not one an island -----------------
  // The page's tempo: the sound bar's (#t-bpm), else the page's bpm field; 0: none.
  const tempo = () => Number((document.getElementById("t-bpm") || document.querySelector("[name='bpm']"))?.value) || 0;
  // The output latency, and the context's time as the speakers are at it:
  // a take is cut, and a playhead drawn, in heard time.
  const outLatency = () => (ctx ? ctx.outputLatency || ctx.baseLatency || 0 : 0);
  const heardTime = () => (ctx ? ctx.currentTime - outLatency() : 0);
  // Launch quantise: a queued loop starts on the next BAR line, never at
  // the end of the current clip — a 4-bar beat must not make you wait 8s
  // to switch away from it. Every loop is a whole number of bars at one
  // bpm, so a bar line of the running clip is a bar line of the new one.
  // Without a tempo on the page, fall back to the clip's own wrap. The
  // first bar line at least `lead` s after `now` (too close to schedule:
  // the next); null with no loop playing. A take's first downbeat and a
  // take's ▶ (keyrec.js) land on it too.
  // The loop's bar in beats (meter.py: 3 for 3/4 and 6/8, 3.5 for 7/8),
  // off the dock's chord bar — every page's (not a fixed 4: a 3/4 loop's
  // bar lines would fall where 4/4's do)
  const barBeats = () => Number(document.getElementById("chord-bar")?.dataset.barBeats) || 4;
  function nextBar(now = ctx?.currentTime ?? 0, lead = 0.03) {
    if (!current) return null;
    const bpm = tempo(), s0 = current.startedAt;
    const grid = bpm > 0 ? barBeats() * (60 / bpm) : current.duration;
    return s0 + Math.ceil((now + lead - s0) / grid) * grid;
  }
  // A 422's own toast fragment (the server's words), as text.
  function toastOf(html, fallback = "Request failed.") {
    try { return new DOMParser().parseFromString(html, "text/html").querySelector(".toast")?.textContent.trim() || fallback; }
    catch { return fallback; }
  }
  // THE post an island makes by hand — a body htmx cannot build (a take, a
  // file, the coach's mic) — answered as htmx answers its own (frontend.md,
  // "synthlab.send"): the busy LED; the answer swapped, its OOB regions
  // (`swap` "none") or into `target`; its HX-Trigger dispatched (a
  // `studio-toast` toasts, perform.js); a refusal in the server's words; a
  // dead server or a timeout said once. `done(headers)`: the toast when the
  // answer brings none. Resolves to { headers, text, trigger }, or null.
  async function send(path, body, { target = null, swap = "none", refused = "That was refused.", done = null, timeout = htmx.config.defaultTimeout || 60000 } = {}) {
    const led = document.getElementById("audio-led"), stop = new AbortController(), t = setTimeout(() => stop.abort(), timeout);
    led?.classList.add("busy");
    try {
      const res = await fetch(path, { method: "POST", body, headers: S().pageHeaders?.(), signal: stop.signal }), text = await res.text();
      led?.classList.remove("err");
      if (!res.ok) { toast(res.status === 422 ? toastOf(text, refused) : `${refused} (${res.status})`, "error"); return null; }
      if (!/json/.test(res.headers.get("content-type") || "")) await htmx.swap({ text, swap, target: target || document.body, sourceElement: document.body });
      const h = res.headers.get("HX-Trigger") || "";
      let trigger;
      try { trigger = JSON.parse(h || "{}"); } catch { trigger = Object.fromEntries(h.split(",").map((n) => [n.trim(), null])); }
      for (const [k, detail] of Object.entries(trigger)) document.body.dispatchEvent(new CustomEvent(k, { detail, bubbles: true }));
      if (done && !trigger["studio-toast"]) toast(done(res.headers), "done");
      return { headers: res.headers, text, trigger };
    } catch (err) {
      if (err?.name === "AbortError") toast(`that took too long (over ${Math.round(timeout / 1000)} s) and was stopped`, "error");
      else if (err instanceof TypeError && /fetch|network|load failed/i.test(err.message)) { led?.classList.add("err"); toast("server unreachable — is the studio running?", "error"); }
      else toast(String(err?.message || err), "error");
      return null;
    } finally { clearTimeout(t); led?.classList.remove("busy"); }
  }

  let swapToken = 0;
  async function swapTo(url, queue) {
    ensureCtx();
    // The URL is a content hash (render_key covers every audible input), so
    // same URL means same audio: keep the running loop's phase instead of
    // restarting it. Queued launches still go through — the launcher waits
    // on loop-switched.
    if (playing && current && url === lastUrl && !queue) return;
    lastUrl = url;
    if (!playing) { pendingUrl = url; if (queue) switched(); return; }
    const token = ++swapToken;
    try {
      const buffer = await fetchBuffer(url);
      if (token !== swapToken || !playing) return; // superseded by a newer swapTo call, or stopped
      // (Perform's launcher picks its own point — 4 bars, the loop's end:
      // slots.js `launchAt`, from the clip's grid as it stands now, so a
      // render that missed one point lands on the next: never late)
      const at = (queue && (S().launchAt?.() ?? nextBar())) || 0;
      const waits = '.launch[data-state="queued"], .sl-cell[data-q="go"] .sl-slot'; // Perform's launchers: the point it waits for
      if (at) S().transport?.queue("loop", { label: document.querySelector(waits)?.textContent.trim() || "the next loop", at, beat: 60 / (tempo() || 120), sel: waits });
      startLoop(buffer, at, Math.max(1, Number(new URL(url, location.href).searchParams.get("passes")) || 1));
      restBufUrl = url;
      if (!at) S().transport?.claim("loop", loopSpec()); // the label follows the view whose mix this is
      if (queue && !at) switched();
    } catch (e) { toast(String(e.message || e), "error"); }
  }
  const queueTo = (url) => swapTo(url, true);

  function stop() {
    if (current) { try { current.src.stop(); } catch {} current = null; }
    document.dispatchEvent(new Event("loop-clock"));
    playing = false;
    swapToken++; // a fetch still in flight must not start the loop after stop
    stopPlayhead();
    setPlayButton(false);
    S().transport?.release("loop");
  }
  // what the registry (transport.js) says of the loop: whose mix, where it is
  const S = () => window.synthlab;
  let plays = ""; // the last preview's own name (preview-ready's `plays`), else the view's mix
  function loopSpec() {
    const b = document.querySelector('#view [data-player="loop"]'), v = document.querySelector('.views a[aria-current="page"] .vl')?.textContent.trim();
    return { kind: "Loop", label: plays || [v, S().transport?.playsOf?.(b) || b?.dataset.plays || "this page's mix"].filter(Boolean).join(" · "), stop, pos: loopPos };
  }
  // where a looped player begun at t0 (on its bar grid) is: the loop's, ▶ beat's
  function posAt(t0, dur) {
    const bar = 240 / (tempo() || 120), n = Math.max(1, Math.round(dur / bar));
    const t = Math.max(0, ctx.currentTime - t0), ph = (t % dur) / dur;
    return { text: `bar ${Math.min(n, Math.floor(ph * n) + 1)} / ${n}`, frac: ph, beat: ((t / bar) * 4) % 1 };
  }
  const loopPos = () => (current ? posAt(current.startedAt, current.cycle) : null);

  async function play() {
    ensureCtx();
    await ctx.resume();
    playing = true;
    setPlayButton(true);
    S().transport?.claim("loop", loopSpec()); // one thing plays: whatever else did stops
    const url = pendingUrl || lastUrl;
    pendingUrl = null;
    if (url) await swapTo(url);
    else htmx.ajax("POST", "/fragment/preview", { values: { view: document.getElementById("view")?.dataset.view || "" }, swap: "none" }); // first render: this view's own mix
  }

  // (Breed / Library / Map ship no #play: the top dock's read-out,
  // transport.js, names the loop and stops it on every page)
  function setPlayButton(on) {
    const btn = document.getElementById("play");
    const glyph = on ? "■" : "▶"; // (only what moved: a new text node restyled #view through a :has() rule)
    if (btn) { btn.setAttribute("aria-pressed", String(on)); if (btn.textContent !== glyph) btn.textContent = glyph; }
  }

  // THE toast. `kind`: "info" (the default — neutral, a polite status),
  // "done" (green: something was kept or made) or "error" (red and an
  // alert: a failure or a refusal). Red means clip, record, mute or delete
  // here, so a "Saved…" must not wear it.
  function toast(message, kind = "info") {
    const box = document.getElementById("toasts");
    if (!box) return;
    const p = document.createElement("p");
    p.className = kind === "info" ? "toast" : `toast ${kind}`;
    p.setAttribute("role", kind === "error" ? "alert" : "status");
    p.textContent = message;
    box.replaceChildren(p);
    setTimeout(() => p.remove(), 8000);
  }
  // A toast never stands between the hand and the page: it lets the
  // pointer through to what it covers (style.css 9: only its own buttons
  // take one) and goes faint while the pointer is over it, so the head's
  // tools under it can be seen and pressed for its whole 8 s.
  document.addEventListener("pointermove", (e) => {
    for (const t of document.getElementById("toasts")?.children || []) {
      const r = t.getBoundingClientRect(), over = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
      t.classList.toggle("see-through", over && !t.contains(e.target));
    }
  }, { passive: true });

  // ---- visualizers -------------------------------------------------------
  // The scope, spectrum, peak meter and the --live light moved to motion.js
  // (the motion island); this island keeps the loop playhead, which needs
  // the sounding buffer's clock.
  // Playhead: --phase is written on the dedicated .playhead elements only
  // (it used to go on the whole .grid, an inherited property that restyled
  // every step cell each frame); the line moves with `translate` in a
  // container-unit track (compositor only). The rAF runs only while a loop
  // plays, and the elements are cached until the next settle.
  let lastPhase = -1, phRAF = 0, heads = null;
  // the step grids' playheads are grid.js's: each grid maps the loop's phase
  // onto its own length (a 1-bar synth grid in a 2-bar mixed loop)
  const playheads = () => (heads ??= [...document.querySelectorAll(".loopbar .playhead")]);
  document.addEventListener("htmx:after:settle", () => { heads = null; lastPhase = -1; });
  function tickPlayhead() {
    phRAF = 0;
    if (!current || !playing) return stopPlayhead();
    phRAF = requestAnimationFrame(tickPlayhead);
    if (document.hidden) return;
    const phase = ((ctx.currentTime - current.startedAt) / current.cycle) % 1; // one pass of the grid
    if (phase >= 0 && Math.abs(phase - lastPhase) > 0.003) {
      lastPhase = phase;
      for (const h of playheads()) h.style.setProperty("--phase", phase.toFixed(3));
    }
  }
  function startPlayhead() { if (!phRAF && playing) phRAF = requestAnimationFrame(tickPlayhead); }
  function stopPlayhead() {
    cancelAnimationFrame(phRAF); phRAF = 0;
    if (lastPhase === -1 && !heads) return;
    lastPhase = -1;
    for (const h of playheads()) h.style.removeProperty("--phase");
  }
  // Shortcut sheet. <dialog> brings its own focus trap and Esc handling, so
  // this is only the opener — and it must not fire while you are typing or
  // playing, hence the shared keysBlocked gate piano.js exposes.
  document.addEventListener("click", (e) => {
    if (e.target.closest("#keys-help")) document.getElementById("shortcuts")?.showModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "?" || e.repeat) return;
    if (window.synthlab.isTypingTarget?.(e.target)) return;
    const dlg = document.getElementById("shortcuts");
    if (!dlg) return;
    e.preventDefault();
    dlg.open ? dlg.close() : dlg.showModal();
  });

  // (the Library's filter, its chips and arrow keys: library.js)
  // Sounds-dock filter: same idea over .sound-card (user + factory), by
  // name and tags. Reapplied after swaps so the query survives a reload
  // of the list.
  function applyDockFilter() {
    const box = document.getElementById("dock-filter");
    if (!box) return;
    const q = box.value.trim().toLowerCase();
    for (const card of document.querySelectorAll(".sound-card")) {
      const hay = `${card.dataset.name} ${card.dataset.tags || ""}`.toLowerCase();
      card.closest("li").hidden = !!q && !hay.includes(q);
    }
    // each shelf counts what it shows ("2 of 18") and goes when it shows none
    let any = !q;
    for (const shelf of document.querySelectorAll("#preset-list .dock-shelf")) {
      const all = shelf.querySelectorAll(".sound-cards > li").length;
      const shown = all - shelf.querySelectorAll(".sound-cards > li[hidden]").length, n = shelf.querySelector(".lib-section small");
      const says = q ? `${shown} of ${all}` : String(all);
      if (n && n.textContent !== says) n.textContent = says; // (only what moved: a new text node restyles #view)
      shelf.hidden = !!q && !shown;
      any ||= shown > 0;
    }
    const none = document.getElementById("dock-none");
    const asked = none?.querySelector("q");
    if (none) { none.hidden = any; if (asked.textContent !== box.value.trim()) asked.textContent = box.value.trim(); }
    rove();
  }
  document.addEventListener("input", (e) => { if (e.target.id === "dock-filter") applyDockFilter(); });
  document.addEventListener("htmx:after:settle", applyDockFilter);
  // The sounds list is ONE Tab stop, as the step grids are (it was 3-6
  // stops a card, 60 cards before the sound bar: 501 Tabs to ▶). Only the
  // cursor's card's load button takes Tab; ↑ / ↓ / Home / End move the
  // cursor, Enter loads, Space hears, the Menu key (Shift+F10) is the
  // card's menu for the rest (menu.js: ＋ layer, ★, rename, delete).
  let roving = null; // the cursor's card: its element, else its name after a swap
  const dockCards = () => [...document.querySelectorAll("#preset-list .sound-card")].filter((c) => !c.closest("[hidden]"));
  function rove(to, focus) {
    const shown = dockCards();
    const at = to || (roving?.isConnected && !roving.closest("[hidden]") ? roving : shown.find((c) => c.dataset.name === roving?.dataset?.name)) || shown[0];
    for (const b of document.querySelectorAll("#preset-list .sound-card :is(button, summary)")) b.tabIndex = b.closest(".sound-card") === at && b.classList.contains("load") ? 0 : -1;
    if (at) roving = at;
    if (at && focus) at.querySelector(".load").focus();
  }
  document.addEventListener("keydown", (e) => {
    const card = e.target.closest?.("#preset-list .sound-card");
    if (!card || !e.target.classList.contains("load") || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === " ") { e.preventDefault(); return card.querySelector(".sc-play")?.click(); }
    const list = dockCards(), i = list.indexOf(card);
    const to = list[{ ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: list.length - 1 }[e.key]];
    if (!to) return;
    e.preventDefault();
    rove(to, true);
  });
  document.addEventListener("focusin", (e) => { const c = e.target.closest?.("#preset-list .sound-card"); if (c && c !== roving) rove(c); });
  rove(); // (deferred: the page is parsed)
  // A sound's tags are part of the row that loads it, not a filter: a click
  // meant to load a sound must not hide the rest of the list. A mouse click on a row's button gives the focus back
  // to the page, so the keys and Space play the new sound at once.
  // ☰ (#sounds-compact): one line a sound, in either density (per browser);
  // unchosen, it starts on under a finger (two-line rows were 80 px there).
  let compact = false;
  try { const v = localStorage.getItem("synthlab.soundsCompact"); compact = v === "1" || (v == null && matchMedia("(pointer: coarse)").matches); } catch { /* blocked */ }
  const paintCompact = () => {
    document.documentElement?.toggleAttribute("data-sounds-compact", compact); // (?.: node's harnesses have no page)
    document.getElementById?.("sounds-compact")?.setAttribute("aria-pressed", String(compact));
  };
  paintCompact();
  document.addEventListener("htmx:after:settle", () => {
    paintCompact();
    const saved = document.querySelector(".sound-card.just-saved:not([data-shown])"); // a sound just saved: brought into view once
    if (saved) { saved.dataset.shown = ""; saved.scrollIntoView({ block: "nearest" }); }
  });
  document.addEventListener("click", (e) => {
    if (e.target.closest?.("#sounds-compact")) {
      compact = !compact;
      try { localStorage.setItem("synthlab.soundsCompact", compact ? "1" : "0"); } catch { /* blocked */ }
      return paintCompact();
    }
    const b = e.target.closest?.(".sound-card button");
    if (b && e.detail > 0) b.blur(); // a pointer's click, not Enter / Space on a focused row
  });
  // a level drawn by a new view (the Mix view's master fader, Perform's vol:
  // [data-volume]) shows the level the output is at, not its markup's 80 %;
  // and every one moves with the keyboard dock's Monitor Volume (#volume)
  const levels = (but) => document.querySelectorAll("#volume, [data-volume]").forEach((v) => {
    if (v === but || !master || Math.abs(+v.value - master.gain.value) < 0.005) return;
    v.value = String(master.gain.value); rangeFill(v); syncOutput(v);
  });
  document.addEventListener("htmx:after:settle", () => levels());

  // ---- wiring ------------------------------------------------------------
  // Nav view-switch links keep a plain href (server-rendered full-page
  // fallback / deep-linking) alongside hx-get so #view swaps in place on
  // a normal click. htmx's own click handling is expected to
  // preventDefault() that native navigation itself, but a capture-phase
  // guard here makes it unconditional: it runs before htmx's own
  // (bubble-phase, per-element) listener regardless of any race between
  // htmx's async trigger pipeline and the browser's native activation
  // step, so the anchor never falls through to a real page load once JS
  // has booted. Only cancels the browser default; htmx's own listener
  // still runs afterward and performs the fetch + #view swap normally.
  document.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const link = e.target.closest(".views a[hx-get]");
    if (link && typeof htmx !== "undefined") e.preventDefault();
  }, true);

  // Delegated so preset-load / A/B-swap full-body swaps (hx-target="body")
  // don't orphan listeners bound to the old #play / #volume nodes.
  document.addEventListener("click", (e) => {
    if (e.target.closest("#play")) playing ? stop() : play();
  });
  // Double-click a slider = back to the Patch default for that one field —
  // the per-param escape hatch the scope-level reset system skips.
  document.addEventListener("dblclick", (e) => {
    const t = e.target;
    if (t?.type !== "range" || t.dataset.default === undefined) return;
    const d = Number(t.dataset.default);
    t.value = t.dataset.log
      ? String(Math.log(Math.max(d, +t.dataset.min) / +t.dataset.min) / Math.log(+t.dataset.max / +t.dataset.min))
      : String(d);
    // a default off the step grid (decay 2.0 on 0.2..6 in 100 steps snaps to
    // 1.998): drop the grid until the next swap redraws it, so it lands exact
    if (!t.dataset.log && Math.abs(+t.value - d) > 1e-9) { t.step = "any"; t.value = String(d); }
    t.dispatchEvent(new Event("input", { bubbles: true }));
    // the input handler wrote the step-snapped position's value (2202 for a
    // 2200 default); the default itself is exact, so post and show that
    if (t.dataset.log && t.previousElementSibling?.name) {
      t.previousElementSibling.value = String(d);
      syncOutput(t, d);
    }
  });

  // Capture phase + stopPropagation: #volume is client-only (no name) but
  // lives inside the sound-bar form — without this every nudge posted a
  // no-op render whose preview-ready restarted the loop from beat 1.
  document.addEventListener("input", (e) => {
    const t = e.target;
    if (!t) return;
    editing = t; editedAt = Date.now(); // syncControls leaves this one alone
    // every range's track fill, here in capture: the handlers below stop
    // propagation for the morph slider and #volume, which then never filled
    if (t.type === "range") rangeFill(t);
    // the morph slider posts itself on change; its drag must not fire the
    // sound-bar form's input trigger
    if (t.closest?.(".morph")) { e.stopPropagation(); return; }
    // an inline curve's time/value fields sit inside #patch but belong to the
    // curve: keep them away from the patch form's input trigger (a no-op
    // render and an empty undo step per keystroke). "value %" is bottom-up,
    // the endpoint wants top-down y.
    if (t.closest?.(".curve-add")) {
      e.stopPropagation();
      const y = t.name === "yv" && t.closest(".curve-add").querySelector("[name=y]");
      if (y) y.value = 100 - Number(t.value);
      return;
    }
    if (t.id === "volume" || t.dataset?.volume !== undefined) {
      e.stopPropagation();
      if (master) master.gain.value = Number(t.value);
      syncOutput(t); levels(t); // the Mix view's master fader has a read-out
    }
  }, true);
  document.addEventListener("input", (e) => {
    const t = e.target;
    if (t && t.type === "range") {
      if (t.dataset.log) { // keep the posting field in step with the position
        const h = t.previousElementSibling;
        if (h?.name) h.value = String(Math.round(logVal(t) * 100) / 100);
      }
      syncOutput(t);
    }
  });

  // ---- live values after a swap ------------------------------------------
  // Morph and OOB swaps copy ATTRIBUTES. A control the user has touched is
  // "dirty": its live value/checked/selection stops following its
  // attribute, so when the server redraws it with the value attribute it
  // was first drawn with (Undo, panel Defaults, Morph, a root change
  // redrawing the bpm) the thumb stayed where the hand left it — and the
  // next edit posted that stale value straight back. After every settle,
  // each swapped control takes its live state from its attributes again,
  // except client-owned controls (no server state behind them) and the one
  // the user is still dragging or typing into. data-springs opts a control
  // (the morph slider) back in: it is meant to snap home after its post.
  const CLIENT_OWNED = "#volume, [data-volume], #space, #bend-range, #bend-pop :is(input, select), [type=search], [type=file], [type=hidden], :is(#chord-bar, .kbd-bar) :is(input, select), [data-talk]";
  let editing = null, editedAt = 0;
  function rangeFill(t) { t.style.setProperty("--fill", `${((t.value - t.min) / (t.max - t.min || 1)) * 100}%`); }
  function syncControl(el) {
    if (el.matches(CLIENT_OWNED) || el.matches(":active")) return false;
    if (el === editing && !el.hasAttribute("data-springs") && Date.now() - editedAt < 1500) return false;
    let moved = false;
    if (el.tagName === "SELECT") {
      const i = Math.max(0, [...el.options].findIndex((o) => o.hasAttribute("selected")));
      if (!el.multiple && el.selectedIndex !== i) { el.selectedIndex = i; moved = true; }
    } else if (el.type === "checkbox" || el.type === "radio") {
      const on = el.hasAttribute("checked");
      if (el.checked !== on) { el.checked = on; moved = true; }
    } else if (el.hasAttribute("value")) {
      const before = el.value;
      el.value = el.getAttribute("value");
      moved = el.value !== before;
    }
    // a log slider's exact value is its hidden field (the position is snapped)
    const exact = el.dataset.log && el.previousElementSibling?.name ? Number(el.previousElementSibling.value) : undefined;
    if (el.type === "range") { rangeFill(el); if (moved) syncOutput(el, exact); }
    return moved;
  }
  function syncControls(nodes) {
    let n = 0;
    for (const node of nodes) {
      if (node.nodeType !== 1) continue;
      if (node.matches("input, select") && syncControl(node)) n++;
      for (const el of node.querySelectorAll("input, select")) if (syncControl(el)) n++;
    }
    return n;
  }
  // reaching for anything else (Undo, Defaults — Safari never focuses a
  // clicked button, hence pointerdown too) ends the edit at once
  for (const type of ["focusin", "pointerdown"]) {
    document.addEventListener(type, (e) => { if (editing && !editing.contains(e.target)) editing = null; }, true);
  }
  window.synthlab.syncControls = syncControls;
  document.addEventListener("htmx:after:settle", (e) => syncControls(e.detail?.newContent || [e.target]));
  document.addEventListener("DOMContentLoaded", () => document.querySelectorAll("input[type=range]").forEach(rangeFill));
  // (a panel preset, a ⋯ row — Defaults, a mod mix — loads with a body morph, which keeps
  // its popover open: each row closes it itself, popovertargetaction="hide")
  // Full-body swaps morph (hx-swap="innerMorph"), so the focused element is
  // patched in place rather than destroyed and focus survives on its own.
  // The lastFocus save/restore this replaced existed only because innerHTML
  // rebuilt every node; it is dead weight now — but the save dialog still
  // needs closing, since that IS a deliberate focus move, not a restore.
  document.addEventListener("htmx:after:swap", (e) => {
    if (e.target.closest?.("#save-dialog")) { // saved: close the popover, focus returns to its opener
      document.getElementById("save-dialog")?.hidePopover?.();
      document.querySelector('[popovertarget="save-dialog"]')?.focus();
    }
  });
  // the Bass page's quick keys: the root in force is the pressed one, and ▶ plays the phrase there
  document.addEventListener("click", (e) => {
    const k = e.target.closest?.(".quickkeys > button"), play = document.getElementById("play");
    if (!k) return;
    for (const b of k.parentElement.children) b.setAttribute("aria-pressed", String(b === k));
    if (play?.closest(".bass-view")) play.dataset.plays = `the phrase at ${k.textContent.trim()}`;
  });
  // Save sound…: the name field opens holding the sound's name, selected (Enter keeps it)
  // (before it shows, so it never opens on the last name; selected once it is up)
  document.addEventListener("beforetoggle", (e) => {
    if (e.target.id !== "save-dialog" || e.newState !== "open") return;
    const box = e.target.querySelector('input[name="name"]'), now = document.querySelector("#sound-id .sid-name")?.textContent.trim();
    if (box) { box.value = now && now !== "Untitled" ? now : ""; queueMicrotask(() => box.select()); }
  }, true);
  document.addEventListener("htmx:after:swap", (e) => {
    // → Song's answer: its note opens under the button (a popover, so the bar never reflows)
    const note = e.target.matches?.(".to-song") ? e.target.nextElementSibling : null;
    if (note?.matches("[popover]:not(:popover-open)") && note.textContent.trim()) note.showPopover({ source: e.target });
  });
  // curve.html's click-to-add-point used to read the click offset via
  // hx-vals="js:{...}"; CSP's script-src 'self' (no 'unsafe-eval') blocks
  // htmx4 from running that through Function(). Filled in here instead,
  // from the click event and element htmx already hands the config-request.
  document.addEventListener("htmx:config:request", (e) => {
    const el = e.detail.ctx.sourceElement;
    if (!el.classList?.contains("curve-click")) return;
    const ev = e.detail.ctx.sourceEvent, body = e.detail.ctx.request.body;
    body.set("x", ev.offsetX);
    body.set("y", ev.offsetY);
    body.set("w", el.clientWidth);
    body.set("h", el.clientHeight);
  });
  function applyDock() {
    const cockpit = document.querySelector(".cockpit");
    let collapsed = false;
    if (cockpit && cockpit.querySelector(".dock")) {
      collapsed = localStorage.getItem("synthlab.dock") === "closed";
      cockpit.classList.toggle("dock-collapsed", collapsed);
    }
    document.querySelectorAll(".dock-toggle").forEach((btn) => btn.setAttribute("aria-expanded", String(!collapsed)));
  }
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".dock-toggle")) return;
    const cockpit = document.querySelector(".cockpit");
    const closed = cockpit.classList.toggle("dock-collapsed");
    localStorage.setItem("synthlab.dock", closed ? "closed" : "open");
    document.querySelectorAll(".dock-toggle").forEach((btn) => btn.setAttribute("aria-expanded", String(!closed)));
  });
  // (on the settle: a page transition's swap settles inside the transition, its
  // after:swap waits for the end — pagemotion.js — and the dock must be laid out first)
  document.addEventListener("htmx:after:settle", applyDock);
  // the rail's page lit as the swap that brings it begins (after the swap, it
  // would lag a page transition by its length): the incoming #view's name
  document.addEventListener("htmx:before:swap", (e) => {
    const t = e.detail?.tasks?.find((k) => k.type === "main" && k.target?.id === "view");
    const view = t?.fragment?.querySelector?.("#view")?.dataset.view;
    if (!view) return;
    document.querySelectorAll(".views .view").forEach((a) => {
      const on = a.getAttribute("href") === `/view/${view}`;
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  });
  document.addEventListener("htmx:after:swap", () => {
    // Full-body swaps (preset load / A-B swap / breed adopt) replace
    // #audio-led itself, resetting it to "off" even though ctx is still
    // alive and audio keeps playing. Resync from JS-owned state.
    if (ctx) setLed(true);
    // Same problem, one control over: every view ships its own #play, and
    // the server always renders it stopped. Switching views while a loop ran
    // left a playing transport showing "▶", so the next press tried to start
    // what was already going. `playing` is the only truth here.
    setPlayButton(playing);
  });

  // ---- drag-paint step cells ---------------------------------------------
  // pointerdown only arms the stroke (never mutates .checked, so a plain
  // click's native activation is untouched). pointermove paints once a
  // genuine drag leaves the origin cell — origin included, each cell once
  // (elementFromPoint covers touch). pointerup fires one bubbling `input`
  // only if painting happened; a same-cell click fires its own native
  // input, already caught by the htmx debounce. A capture-phase click
  // guard swallows the native click a drag-back-to-origin would replay.
  // Shared suppress-click contract for every drag-paint surface below: a
  // drag that actually painted something arms the flag for one tick so the
  // synthetic click a drag-back-to-origin (or a hidden-input drag) would
  // otherwise replay gets swallowed by the single capture-phase guard.
  let suppressClick = false;
  function armSuppressClick() {
    suppressClick = true;
    setTimeout(() => { suppressClick = false; }, 0);
  }
  document.addEventListener("click", (e) => {
    if (!suppressClick) return;
    suppressClick = false;
    e.preventDefault();
    e.stopPropagation();
  }, true);

  let origin = null, paintVal = null, painted = false, leftOrigin = false, last = null;
  function paint(cell) {
    if (!cell || !cell.classList.contains("cell") || cell.dataset.painted) return;
    cell.checked = paintVal;
    cell.dataset.painted = "1";
    painted = true;
  }
  // The piano roll's stroke fills every step between two pointer samples (a
  // fast drag used to paint every second or third cell: 6 samples across
  // 14 steps): rows and steps are walked together from the last cell to
  // this one. Any other grid paints the cell under the pointer, as before.
  function paintTo(cell) {
    const from = last?.parentElement, to = cell?.parentElement, grid = to?.parentElement;
    if (!grid?.classList.contains("roll") || from?.parentElement !== grid) return paint(cell);
    const rows = [...grid.children], r0 = rows.indexOf(from), r1 = rows.indexOf(to);
    const c0 = [...from.querySelectorAll(".cell")].indexOf(last), c1 = [...to.querySelectorAll(".cell")].indexOf(cell);
    const n = Math.max(Math.abs(c1 - c0), Math.abs(r1 - r0), 1);
    for (let i = 1; i <= n; i++) paint(rows[Math.round(r0 + ((r1 - r0) * i) / n)].querySelectorAll(".cell")[Math.round(c0 + ((c1 - c0) * i) / n)]);
  }
  // The pointer is captured only once the stroke LEAVES the origin cell
  // Capturing on pointerdown would retarget pointerup to the .grid, so
  // Chromium would fire a plain click's `click` on the grid, not the
  // checkbox — a mouse click on a step would never toggle it.
  document.addEventListener("pointerdown", (e) => {
    const cell = e.target.closest(".cell");
    if (!cell) return;
    origin = cell;
    // a right-drag erases (FL; grid.js takes a lone right-click), and so does a pen's eraser end (button 5) and the roll's erase tool (roll.js); a pen's barrel is Shift (pen.js)
    paintVal = e.button === 5 || (e.button === 2 && e.pointerType !== "pen") || !!cell.closest("[data-roll=erase] .roll") ? false : !cell.checked;
    painted = false;
    leftOrigin = false;
    last = cell;
  });
  document.addEventListener("pointermove", (e) => {
    if (!origin) return;
    const cell = document.elementFromPoint(e.clientX, e.clientY)?.closest(".cell");
    if (!leftOrigin && cell !== origin) {
      leftOrigin = true;
      try { origin.closest(".grid")?.setPointerCapture(e.pointerId); } catch {}
      paint(origin);
    }
    if (leftOrigin) paintTo(cell);
    last = cell || last;
  });
  // pointerup and a cancelled touch (the browser took over to scroll) both
  // end the stroke — whatever was painted so far is committed either way.
  function endPaint() {
    if (!origin) return;
    const grid = origin.closest(".grid");
    origin = null;
    document.querySelectorAll(".cell[data-painted]").forEach((c) => delete c.dataset.painted);
    if (painted) {
      armSuppressClick();
      grid?.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }
  document.addEventListener("pointercancel", endPaint, true);
  document.addEventListener("pointerup", endPaint);

  // ---- drag-paint velocity lanes -----------------------------------------
  // Mirrors the cell-paint pattern above: pointerdown arms the stroke and
  // captures the pointer on the row (so a drag above/below the 40px lane
  // still keeps painting), pointermove writes each lane's bar + hidden
  // input via elementFromPoint, pointerup fires one bubbling `input` and
  // reuses armSuppressClick for the same drag-back click-swallow guard.
  let velRow = null, velPainted = false;
  function paintVelLane(lane, clientY) {
    if (!lane || !lane.classList.contains("vel-lane")) return;
    const rect = lane.getBoundingClientRect();
    const raw = 1 - (clientY - rect.top) / rect.height;
    const q = Number(lane.dataset.q) || 20, v = Math.round(Math.min(1, Math.max(0, raw)) * q) / q; // (steps.js: a hit lane's own levels)
    setVelLane(lane, v);
    velPainted = true;
  }
  function setVelLane(lane, v) {
    const bar = lane.querySelector(".vel-bar"), input = lane.querySelector("input");
    if (bar) bar.style.setProperty("--v", v); // (a scale: style.css 8, the velocity bars)
    if (input) input.value = v;
    lane.setAttribute("aria-valuenow", String(v));
  }
  // keyboard: a focused lane steps 0.05 per arrow, Home/End to the extremes
  document.addEventListener("keydown", (e) => {
    const lane = e.target.closest?.(".vel-lane");
    const delta = { ArrowUp: 0.05, ArrowRight: 0.05, ArrowDown: -0.05, ArrowLeft: -0.05, Home: -1, End: 1 }[e.key];
    if (!lane || delta === undefined) return;
    e.preventDefault();
    const cur = Number(lane.querySelector("input")?.value ?? 0.9);
    setVelLane(lane, Math.round(Math.min(1, Math.max(0, cur + delta)) * 20) / 20);
    lane.dispatchEvent(new Event("input", { bubbles: true }));
  });
  document.addEventListener("pointerdown", (e) => {
    const lane = e.target.closest(".vel-lane");
    if (!lane) return;
    velRow = lane.closest(".velrow");
    try { velRow.setPointerCapture(e.pointerId); } catch {}
    velPainted = false;
    paintVelLane(lane, e.clientY);
  });
  document.addEventListener("pointermove", (e) => {
    if (!velRow) return;
    const lane = document.elementFromPoint(e.clientX, e.clientY)?.closest(".vel-lane");
    if (lane) paintVelLane(lane, e.clientY);
  });
  function endVelPaint() {
    if (!velRow) return;
    const row = velRow; // the painted row: Perform has two (notes, drums), each in its own form
    velRow = null;
    if (velPainted) {
      armSuppressClick();
      row.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }
  document.addEventListener("pointercancel", endVelPaint, true);
  document.addEventListener("pointerup", endVelPaint);

  document.addEventListener("DOMContentLoaded", applyDock);

  // idle controls (paramui.idle): after an edit the post names the rows that change nothing now
  document.addEventListener("patch-idle", (ev) => {
    const idle = ev.detail || {};
    for (const row of document.querySelectorAll(":is(#patch, .patch-panel) .param[id^='row-']")) {
      const why = idle[row.id.slice(4)], hint = row.dataset.hint || "";
      const title = why ? `${hint}${hint ? " — " : ""}no effect now: ${why}` : hint;
      if (row.title === title) continue; // unchanged: no write
      row.toggleAttribute("data-idle", !!why);
      row.title = title;
    }
  });
  document.addEventListener("preview-ready", (ev) => {
    const detail = ev.detail || {};
    // patch-derived buffers are stale only when the audio actually changed;
    // "/factory/" + "/breed/note/" one-shots survive the flush either way
    if (detail.url && detail.url !== lastUrl) window.synthlab.flushKeys();
    if (!detail.url) return;
    plays = detail.plays || ""; // a render that names itself (the booth, the vocoder): Now playing says so
    // autoplay (breed "in loop" audition): hearing it is the point of the
    // click, so a stopped transport starts instead of stashing the URL
    if (detail.autoplay && !playing) { pendingUrl = detail.url; play(); }
    else if (detail.queue) queueTo(detail.url);
    else swapTo(detail.url);
  }, true);

  // Bred-tile cards carry data-note: play the tile's own short note
  // instantly, client-side, no loop render — and mark the card active.
  // Scoped to .audition — piano keys reuse the data-note attribute name.
  document.addEventListener("click", (e) => {
    const card = e.target.closest(".audition[data-note]");
    if (!card) return;
    // the buffer source goes out on the card (library.js lights its lamp)
    window.synthlab.playUrl(card.dataset.note, { solo: true }) // one audition at a time
      .then((src) => card.dispatchEvent(new CustomEvent("audition-start", { detail: src, bubbles: true })))
      .catch((err) => toast(String(err.message || err), "error"));
    document.querySelectorAll(".tile.active, .lib-card.active").forEach((t) => t.classList.remove("active"));
    card.closest(".tile, .lib-card")?.classList.add("active");
  });

  // Fetch/network failures (server killed for a restart, port gone) fire
  // htmx:error, not htmx:response:error. Toast once per 3s so a slider drag
  // against a dead server doesn't stack ten of them; the LED goes red until
  // the next request succeeds. The busy class pulses the LED while any
  // request is in flight — a slow render and a dead server finally look
  // different from "nothing happened".
  // What went wrong says so: a request htmx stopped at its
  // timeout took too long; one a newer one replaced (hx-sync) is no error;
  // only a fetch that never reached the server turns the LED red.
  let lastNetToast = 0;
  const began = new WeakMap(); // ctx -> when it left
  document.addEventListener("htmx:error", (e) => {
    const err = e.detail?.error, ctx = e.detail?.ctx, now = Date.now();
    let said;
    if (err?.name === "AbortError") {
      const limit = ctx?.request?.timeout != null ? htmx.parseInterval(ctx.request.timeout) : htmx.config.defaultTimeout;
      if (!limit || !began.has(ctx) || performance.now() - began.get(ctx) < limit - 50) return; // replaced, not failed
      said = `that took too long (over ${Math.round(limit / 1000)} s) and was stopped`;
    } else if (err instanceof TypeError && /fetch|network|load failed/i.test(err.message)) {
      document.getElementById("audio-led")?.classList.add("err");
      said = "server unreachable — is the studio running?";
    } else said = `that went wrong in the page: ${err?.message || err}`;
    if (now - lastNetToast > 3000) { lastNetToast = now; toast(said, "error"); }
  });
  document.addEventListener("htmx:before:request", (e) => {
    if (e.detail?.ctx) began.set(e.detail.ctx, performance.now());
    document.getElementById("audio-led")?.classList.add("busy");
  });
  document.addEventListener("htmx:finally:request", () => {
    document.getElementById("audio-led")?.classList.remove("busy");
  });
  document.addEventListener("htmx:before:response", () => {
    document.getElementById("audio-led")?.classList.remove("err");
  });
  document.addEventListener("htmx:response:error", (ev) => {
    // 422 responses carry their own toast fragment; the requesting elements
    // all use hx-swap="none" so htmx 4's default-swap-everything behavior
    // never applies it — parse it out of ctx.text ourselves and surface it.
    // Anything else gets a generic one.
    const { ctx } = ev.detail;
    const status = ctx.response.status;
    toast(status === 422 ? toastOf(ctx.text) : `request failed (${status})`, "error");
  }, true);

  // Object.assign onto the existing object, never a fresh literal: this used
  // to be a flat reassignment to a brand-new object, which silently dropped
  // whatever earlier code in this same file had already hung off it —
  // logVal (above) went missing on the very first slider drag, thrown as
  // "window.synthlab.logVal is not a function" from piano.js, because this
  // reassignment always runs (it is top-level IIFE code, not inside a
  // listener) before any slider is ever touched. Not a script-order race
  // between files — both assignments live in this file, audio.js overwrote
  // itself.
  window.synthlab ??= {};
  Object.assign(window.synthlab, {
    ensureCtx,
    getCtx: () => ctx,
    getMaster: () => master,
    getAnalyser: () => analyser, // motion.js draws the scope/spectrum from it
    lastBuffer: () => restBuffer,
    lastBufferUrl: () => restBufUrl, // motion.js reuses the decoded loop instead of refetching it
    liveOut: () => liveOutNode || master,
    setLiveOut: (n) => { liveOutNode = n; },
    fetchBuffer, // keys.js fetches key buffers through the same decode
    queueTo, isPlaying: () => playing,
    stop, // song.js stops the view's loop preview before the song plays
    loopFx,
    toast,
    // one pass of the playing loop's grid in seconds (the buffer's duration
    // but for a loop with chance, whose buffer holds several), or null when
    // stopped — the one truth piano.js's loopSeconds() reads instead of
    // re-deriving bars * 4 * (60 / bpm) from page state that can be stale.
    loopDuration: () => (current ? current.cycle : null),
    // when the playing loop began, on the context's clock (null when stopped)
    loopStart: () => (current ? current.startedAt : null),
    tempo, barBeats, heardTime, outLatency, nextBar, toastOf, // the shared clock, and a 422's words
    // a post of just `values`: htmx.ajax from <body> sweeps in every named
    // input on the page (frontend.md, "htmx.ajax posts the whole page")
    post: (path, values, opts) => htmx.ajax("POST", path, { values, source: document.getElementById("post-from"), swap: "none", ...opts }),
    send, // a body of our own (a take, a file): fetched, then answered as htmx answers
  });
})();
