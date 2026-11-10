// synthlab/studio/static/transport.js
// The transport: one registry every player reports to, the top dock's
// "now playing" read-out, the cue under each page's ▶, and the two keys.
// The studio has many players — the working loop (audio.js),
// the song and its section / range loops (song.js), the jam's launches
// (jam.js), a cell played alone (songedit.js), the shared <audio> player
// (bench.js's `.player`: the Lab's region, the Voice take; heard here from
// the media events, so bench.js has no hook) and a long audition
// (keys.js's playUrl: a clip, a sound) — and nothing said what ▶ / Space
// would start, what was sounding or what waited for the bar.
//   S.transport.claim(id, spec)  it sounds. spec: {kind, label, stop,
//       pause?, pos? () => {text, frac, beat}, at? (the context time its
//       first sample plays: a count-in until then), beat? (seconds),
//       layer?}; `label` and `pos` may be functions (read every frame).
//       A claim that is not a layer stops every other one first: ONE
//       thing plays (frontend.md, "The transport is the same on every
//       page"). The same spec object claimed again costs nothing.
//   .hold(id, spec)     paused: kept, `spec.play` resumes it
//   .release(id, spec?) it stopped by itself (with spec: only that claim)
//   .queue(id, wait)    it starts later — {label, at (null: loading),
//       beat, sel (what waits: each gets --q and data-wait), cancel?};
//       null: it no longer waits
//   .take(id, kind)     about to start: the others stop now
//   .stop(id), .stopAll()   ■ in the dock and Shift+Space
//   .oneShot(url, src, seconds)  keys.js's long audition (a layer)
// Draws #now-playing (base.html's status strip: the client's, outside the
// `status-bar` region); `output[data-cue-for=<id>]` beside each
// `#view [data-player]` ▶ plus that ▶'s aria-label and title; the waits.
// Keys: Space = this page's ▶ (`#view [data-keys="Space"]`) — its ⏸ while
// that player plays, its resume while paused; with no ▶ on the page, the
// player that sounds. Shift+Space = stop everything. Neither from a
// field; Space never from a focused button, a step grid, a key or a pad
// (each keeps its own Space). In the keys' letters layout (letters.js) a
// held Space is a chord's modifier: a tap with no letter in it calls
// `space` on its keyup.
// Budget: <= 200 code lines (190 + Perform's cue naming its parts and length:
// "the loop · notes + drums · 2 bars"; Synths' "beat only"
// phrase naming the beat; Space handed to the letters layout
// while it is a modifier).
(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  const $ = (id) => document.getElementById(id);
  const on = new Map(), waits = new Map();
  let said = null, raf = 0; // said: the last handoff, shown idle for a moment
  const now = () => S.getCtx?.()?.currentTime ?? 0;
  const val = (v) => (typeof v === "function" ? v() : v);
  const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const page = () => document.querySelector('.views a[aria-current="page"] .vl')?.textContent.trim() || "";

  // ---- the registry ----------------------------------------------------------
  function changed() {
    cues(); raf ||= requestAnimationFrame(frame);
    document.dispatchEvent(new CustomEvent("transport-change"));
  }
  function end(id) { const e = on.get(id); on.delete(id); try { e?.stop?.(); } catch { /* already gone */ } }
  function take(id, kind) {
    for (const [k, e] of [...on]) {
      if (k === id || e.layer) continue;
      said = { text: `${kind || "it"} took over from ${e.kind}`, until: performance.now() + 4000 };
      end(k);
    }
    for (const [k, w] of [...waits]) if (k !== id) { waits.delete(k); w.cancel?.(); }
  }
  function claim(id, spec) {
    const e = on.get(id);
    if (e?.spec === spec && e.state === "playing") return;
    if (!spec.layer) take(id, spec.kind);
    on.set(id, { ...spec, spec, id, state: "playing" });
    waits.delete(id);
    changed();
  }
  function hold(id, spec) { on.set(id, { ...spec, spec, id, state: "paused" }); changed(); }
  function release(id, spec) {
    if (spec && on.get(id)?.spec !== spec) return;
    if (+on.delete(id) | +waits.delete(id)) changed();
  }
  function queue(id, w) {
    const old = waits.get(id);
    if (!w) { if (waits.delete(id)) changed(); return; }
    waits.set(id, { ...w, from: old && old.at != null ? old.from : now() });
    if (old?.label !== w.label) changed(); else raf ||= requestAnimationFrame(frame);
  }
  function stop(id) { const w = waits.get(id); waits.delete(id); end(id); w?.cancel?.(); changed(); }
  function stopAll() {
    S.keysOff?.(); // the keys' notes too, a latched drone's included (piano.js)
    for (const id of [...on.keys()]) end(id);
    for (const w of [...waits.values()]) w.cancel?.();
    waits.clear(); said = null; changed();
  }
  // a one-shot long enough to want a stop: a clip, a sound's audition. It
  // layers over what plays (a hand's audition is heard against the music);
  // a second one replaces the first
  function oneShot(url, src, dur) {
    const m = /^\/([a-z]+)\/([^/?]+?)(?:\.wav|\/audition\.wav)/.exec(url) || [], t0 = now();
    const row = document.activeElement?.closest?.("[data-ctx]")?.dataset.ctx, sound = /audition\.wav/.test(url);
    const spec = {
      kind: m[1] === "clip" ? "Clip" : "Audition", layer: true,
      // a sound's ▶ plays the phrase, the sound on its own (browse.audition)
      label: (row || (m[1] === "clip" || sound ? decodeURIComponent(m[2]) : "a sound")) + (sound ? " · the phrase, alone" : ""),
      stop: () => { try { src.stop(); } catch { /* ended */ } },
      pos: () => ({ text: `${clock(now() - t0)} / ${clock(dur)}`, frac: (now() - t0) / dur }),
    };
    end("audition"); claim("audition", spec);
    src.addEventListener("ended", () => release("audition", spec));
  }
  S.transport = { claim, hold, release, queue, take, stop, stopAll, oneShot, playsOf,
    state: (id) => on.get(id)?.state || (waits.has(id) ? "queued" : null), list: () => [...on.values()] };

  // the shared <audio> player (bench.js): its media events, heard in capture
  const media = (e) => e.target instanceof HTMLMediaElement && e.target;
  document.addEventListener("play", (e) => {
    const a = media(e); if (!a) return;
    if (on.get("media")?.el && on.get("media").el !== a) end("media");
    claim("media", { kind: page() || "Audio", label: a.getAttribute("aria-label") || "audio", el: a,
      stop: () => { a.pause(); a.currentTime = 0; }, pause: () => a.pause(), play: () => a.play().catch(() => {}),
      pos: () => ({ text: `${clock(a.currentTime)} / ${clock(a.duration || 0)}`, frac: a.duration ? a.currentTime / a.duration : 0 }) });
  }, true);
  for (const type of ["pause", "ended"]) document.addEventListener(type, (e) => {
    const a = media(e), x = on.get("media");
    if (a && x?.el === a) a.ended || type === "ended" ? release("media") : hold("media", x.spec);
  }, true);

  // ---- the read-out ------------------------------------------------------------
  // the one it names: a player before a layer, the newest layer otherwise
  const main = () => { const all = [...on.values()]; return all.find((e) => !e.layer) || all.at(-1); };
  const put = (box, sel, text) => { const el = box.querySelector(sel); if (el && el.textContent !== text) el.textContent = text; };
  const left = (w, t) => (w.at == null ? "loading…" : plural(Math.max(0, Math.ceil((w.at - t) / (w.beat || 0.5) - 1e-6)), "beat"));
  function frame() {
    raf = 0;
    const box = $("now-playing"), t = now(), e = main();
    const w = [...waits.values()].sort((a, b) => (a.at ?? -1) - (b.at ?? -1))[0];
    const p = e?.state === "playing" ? val(e.pos) : null, ahead = e?.at != null ? e.at - t : 0;
    const state = !e ? (w ? "queued" : "idle") : e.state === "paused" ? "paused" : ahead > 0.25 ? "count-in" : "playing";
    if (said && performance.now() > said.until) said = null;
    if (box) {
      if (box.dataset.state !== state) box.dataset.state = state;
      put(box, ".np-kind", e ? e.kind : w ? "Next" : said ? "Handoff" : "Stopped");
      put(box, ".np-label", e ? val(e.label) : w ? val(w.label) : said ? said.text : idle());
      put(box, ".np-pos", state === "count-in" ? `count-in · ${left({ at: e.at, beat: e.beat }, t)}` : state === "paused" ? "paused" : w && !e ? left(w, t) : p?.text || "");
      put(box, ".np-next", e && w ? `next: ${val(w.label)} · ${left(w, t)}` : "");
      const also = [...on.values()].filter((x) => x !== e).map((x) => `${x.kind} · ${val(x.label)}`).join(", ");
      put(box, ".np-also", also && `+ ${also}`);
      box.style.setProperty("--np-at", Math.min(1, Math.max(0, p?.frac || 0)).toFixed(3)); // a scale: a width laid the page out every frame
      const ring = state === "count-in" ? 1 - ahead / Math.max(ahead, e.lead || ahead) : w ? q(w, t) : 0;
      box.style.setProperty("--q", ring.toFixed(3));
      box.style.setProperty("--np-pulse", p?.beat != null && !calm() ? Math.max(0, 1 - p.beat * 3).toFixed(2) : "0");
      // written only when they move: an attribute set to the same value each
      // frame still restyles its element
      const set = (id, k, v) => { const el = $(id); if (el && el[k] !== v) el[k] = v; };
      set("np-pause", "hidden", !(e?.pause && state === "playing"));
      set("np-resume", "hidden", !(e?.play && state === "paused"));
      set("global-stop", "disabled", !e && !w);
    }
    marks(t);
    if (on.size || waits.size || said) raf = requestAnimationFrame(frame);
  }
  const calm = () => window.synthlab.calm(); // the Motion setting (colours.js), never the OS's
  const q = (w, t) => (w.at == null ? 0 : Math.min(1, Math.max(0, (t - w.from) / Math.max(0.05, w.at - w.from))));
  // the things that wait (a queued pad, a launcher): their own ring and count
  function marks(t) {
    const seen = new Set();
    for (const w of waits.values()) {
      if (!w.sel) continue;
      for (const el of document.querySelectorAll(w.sel)) {
        seen.add(el);
        const n = w.at == null ? "…" : String(Math.max(0, Math.ceil((w.at - t) / (w.beat || 0.5) - 1e-6)));
        if (el.dataset.wait !== n) el.dataset.wait = n;
        el.style.setProperty("--q", q(w, t).toFixed(3));
      }
    }
    for (const el of document.querySelectorAll("[data-wait]")) if (!seen.has(el)) { delete el.dataset.wait; el.style.removeProperty("--q"); }
  }
  // stopped: what Space would play here
  function idle() {
    const b = mainPlay();
    return b ? `Space plays ${cueOf(b)}` : "Nothing playing";
  }

  // ---- the cue under each ▶ --------------------------------------------------
  const mainPlay = () => document.querySelector('#view [data-keys="Space"][data-player]:not([disabled])');
  const secName = (sid) => $(`sec-${sid}`)?.querySelector(".section-name")?.textContent.trim()
    || document.querySelector(`#mix-section option[value="${CSS.escape(sid)}"]`)?.textContent.trim() || "";
  // what the song's ▶ starts: the song from the marker (songline.js)
  function songCue() {
    const c = S.songClock?.(); if (!c) return "the song";
    const s = c.sel && c.plan?.sections.find((x) => x.section_id === c.sel), th = c.sel && $(`sec-${c.sel}`);
    const first = th ? +th.dataset.startBar : s ? s.start_bar + 1 : 1;
    return c.sel ? `the song from bar ${first + (S.songFrom?.() || 0)} · ${secName(c.sel)}` : "the song from bar 1";
  }
  // what a ▶ plays: its data-plays; Synths' phrase ▶ also names the phrase,
  // how long the loop is (the phrase is one bar, audition.phrase_arrangement;
  // over the beat it loops the beat's bars: the checkbox's data-bars), the
  // beat under it and "this layer" — read off the sound bar's own pills,
  // since a pill posts with the bar's redraw suppressed and a server-drawn
  // label would go stale. ▶ beat (data-player="beat") is its data-plays
  // Perform's ▶ names the parts its switches leave on and the loop's length
  // (arrange.loop_bars: the lcm of the parts' bars): "the loop · notes + drums · 2 bars"
  function partsOf(b) {
    const on = [...document.querySelectorAll('#view .part-btn[aria-pressed="true"]')].map((x) => x.textContent.trim());
    const n = (sel) => Math.max(1, +document.querySelector(`#view ${sel} [name="bars"]`)?.value || 1), gcd = (a, c) => (c ? gcd(c, a % c) : a);
    const lens = [on.includes("synth") && n("#transport"), on.includes("drums") && n("#perform-drum-grid")].filter(Boolean);
    const bars = lens.reduce((a, c) => (a * c) / gcd(a, c), 1);
    return `${b.dataset.plays} · ${on.map((p) => (p === "synth" ? "notes" : p)).join(" + ")} · ${plural(bars, "bar")}`;
  }
  function playsOf(b) {
    const f = b?.form, p = b?.dataset.player === "loop" && f?.querySelector('[name="phrase"]:checked');
    if (!p && b?.dataset.player === "loop" && document.querySelector("#view .part-btn")) return partsOf(b);
    if (!p) return b?.dataset.plays || "this page's mix";
    if (p.value === "none") return $("play-beat")?.dataset.plays || "the beat"; // beat only: ▶ is ▶ beat's render
    const vs = f.querySelector('[name="vs_beat"]'), on = !!vs?.checked;
    const one = $("ls-this")?.getAttribute("aria-pressed") === "true" ? " · this layer" : "";
    return `${b.dataset.plays} · ${p.nextElementSibling?.textContent.trim() || p.value} · ${plural(on ? Math.max(1, +vs.dataset.bars || 1) : 1, "bar")}${on ? " + the beat" : ""}${one}`;
  }
  function cueOf(b) {
    const id = b.dataset.player, e = on.get(id);
    const plays = id === "song" ? songCue() : document.querySelector("#view [data-plays-now]:not([hidden])")?.dataset.playsNow || playsOf(b);
    if (e?.state === "paused") return id === "song" ? `${plays}: on from the pause` : `on from ${val(e.label)}`; // (⏸ moved the marker)
    if (e?.state === "playing") return id === "song" && S.songClock?.().loop ? `${plays} (stops the loop)` : `nothing new: it is playing (${e.pause ? "Space pauses" : "▶ stops"})`;
    const other = main();
    return other && other.id !== id && !other.layer ? `${plays} · stops ${other.kind}` : plays;
  }
  function cues() {
    for (const b of document.querySelectorAll("#view [data-player]")) {
      const c = cueOf(b), out = document.querySelector(`[data-cue-for="${b.id}"]`), e = on.get(b.dataset.player);
      const playing = e?.state === "playing" && !(b.dataset.player === "song" && S.songClock?.().loop);
      const say = playing ? `${e.pause ? "Playing" : "Stop"}: ${val(e.label)}` : `Play ${c}`;
      if (out && out.textContent !== (playing ? "playing" : c)) out.textContent = playing ? "playing" : c;
      if (out) out.dataset.state = e?.state || "";
      if (b.getAttribute("aria-label") !== say) b.setAttribute("aria-label", say);
      if (!("tip" in b.dataset) && b.title !== `${say} (Space)`) b.title = `${say} (Space)`;
    }
    raf ||= requestAnimationFrame(frame); // the idle line names the cue too
  }
  document.addEventListener("htmx:after:settle", cues);
  setInterval(cues, 400); // the marker, the selection, a loop toggled: all redraw within a beat

  // ---- the keys -------------------------------------------------------------------
  const OWN_SPACE = "input, .grid, .pkey, #chord-bar, #perform-pads, .jam-grid, .ctile, [role=slider]:not(#song-ruler)";
  window.addEventListener("keydown", (e) => {
    if (e.key !== " " || e.ctrlKey || e.metaKey || e.altKey || e.repeat || S.isTypingTarget?.(e.target)) return;
    if (!S.letters?.takesSpace(e)) space(e); // the letters layout: Space is a modifier, a tap plays on its keyup (letters.js)
  }, true);
  function space(e) {
    if (document.documentElement.hasAttribute("data-embed")) return; // the Song page's sound dock: a frame, not a page
    if (e.shiftKey) { e.preventDefault(); e.stopImmediatePropagation(); stopAll(); return; }
    if (S.keysBlocked?.(e.target) || e.target.closest?.(OWN_SPACE) || document.querySelector("dialog[open]")) return;
    const b = mainPlay(), x = b ? on.get(b.dataset.player) : main();
    if (!b && !x) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (x?.state === "paused") x.play();
    else if (x?.state === "playing" && x.pause) x.pause();
    else if (b) b.click();
    else stop(x.id);
  }
  Object.assign(S.transport, { space, OWN_SPACE }); // letters.js: a Space tap, and what keeps its own Space
  document.addEventListener("click", (e) => {
    if (e.target.closest?.("#global-stop")) stopAll();
    else if (e.target.closest?.("#np-pause")) main()?.pause?.();
    else if (e.target.closest?.("#np-resume")) main()?.play?.();
  });
  document.addEventListener("DOMContentLoaded", changed);
})();
