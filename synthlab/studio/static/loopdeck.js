// synthlab/studio/static/loopdeck.js
// The Loops page's deck (loop-and-perform.md, "The deck"): the page's one
// transport, over the launcher. It owns no player — audio.js plays the
// loop, song.js the song, keyrec.js the take, transport.js holds what
// sounds (`S.transport`, the top bar's read-out mirrors it) — only the
// choices and the lamps:
// - The source (Loop | Scene | Song, per browser: localStorage
//   `synthlab.deck`): what ▶ starts (Space is transport.js's, which
//   presses this ▶). Loop: the selected cell's loop alone (POST
//   /fragment/perform/slot/hear: its render at the page's tempo, played as
//   the loop under its name), else the working loop; Scene: the selected
//   cell's row (its ▸, unless all of it plays already), else the working
//   loop; Song: the song (song.js) — an empty one is made from the loop
//   first ("make it a song", the old ▶ Whole song). ▶ while the deck
//   plays is its stop; ■ stops everything (the top bar's ■). A start stops
//   a ▷ listen. A scene's ▸ pressed makes Scene the source.
// - While it plays a cell alone, every preview the server answers (an
//   edit, a launch) is held (`S.deckHold`, audio.js) and the cell asked
//   for again: an edit saves to its loop, so the next render is what the
//   grid holds; another cell selected takes over at the quantise point;
//   the source switched, or no filled cell selected, hands back the mix.
// - The meter: a lamp a bar of what plays (16 at most: a song's 16-bar
//   page), those before lit, the current one pulsing with its beat; while
//   ⏺ Rec counts in, the beats left (keyrec.js's claim). The playing
//   cells' rings (`--ring`, their loop's position) on the same frame.
//   Nothing runs at rest.
// - The key read-out follows the note grid's root and scale; the Rec ▾
//   says where a take lands (keyrec.js presses its buttons).
// Budget: <= 145 code lines.
(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  if (typeof document === "undefined") return;
  const KEY = "synthlab.deck", SRCS = ["loop", "scene", "song"];
  let src = "loop";
  try { src = JSON.parse(localStorage.getItem(KEY) || "{}").src || "loop"; } catch { /* blocked: Loop */ }
  if (!SRCS.includes(src)) src = "loop";
  const deck = () => document.getElementById("perform-transport");
  const T = () => S.transport, sel = () => S.slotSel?.() || null;
  const sounding = () => ["loop", "song"].some((id) => ["playing", "queued"].includes(T()?.state(id)));
  let alone = null, raf = 0; // the cell the deck plays alone; the meter's frame

  // ---- what ▶ plays, and the read-outs --------------------------------------
  const lane = (s) => (s.kind === "beat" ? "the drums" : `lane ${s.lane + 1}`);
  function says() {
    const s = sel();
    if (src === "loop" && s?.filled) return `“${s.name}” alone · ${lane(s)}`;
    return src === "scene" && s ? `scene ${s.row + 1}` : "";
  }
  function lands(d) {
    const out = d.querySelector(".deck-lands"), s = sel(), how = d.querySelector('[data-key-how][aria-pressed="true"]')?.dataset.keyHow;
    if (!out) return;
    let text = s ? "" : "no cell selected: a new loop in the Loops panel";
    if (s?.kind === "beat") text = "a drum cell takes no take: a new loop in the Loops panel";
    else if (s && s.filled && how !== "new") text = `${how} “${s.name}” · ${lane(s)}, scene ${s.row + 1}`;
    else if (s) { const row = s.filled ? s.free : s.row; text = row == null ? `${lane(s)} has no empty cell: the Loops panel` : `a new loop · ${lane(s)}, scene ${row + 1}`; }
    if (out.textContent !== text) out.textContent = text;
  }
  function paint() {
    const d = deck();
    if (!d) return;
    if (d.dataset.src !== src) d.dataset.src = src;
    for (const b of d.querySelectorAll("[data-deck-src]")) b.setAttribute("aria-pressed", String(b.dataset.deckSrc === src));
    const p = document.getElementById("play"), want = src === "song" ? "song" : "loop";
    if (p && p.dataset.player !== want) p.dataset.player = want;
    const say = d.querySelector("[data-plays-now]"), text = says();
    if (say && say.dataset.playsNow !== text) { say.dataset.playsNow = text; say.hidden = !text; }
    const root = document.querySelector('#transport [name="root"]')?.value, sc = document.querySelector('#transport [name="scale_name"]')?.value || "";
    const key = d.querySelector(".deck-key"), k = root ? `${root.replace(/-?\d+$/, "")} ${sc.replace("pentatonic", "pent.").replace(/_/g, " ")}` : "";
    if (key && k && key.textContent !== k) key.textContent = k;
    lands(d);
    raf ||= requestAnimationFrame(frame); // (at rest: the bars ▶ would play)
  }

  // ---- ▶: start what the source says ---------------------------------------------
  async function hear(s, queue) {
    const f = new FormData();
    for (const [k, v] of Object.entries({ lane: s.lane, row: s.row, bpm: S.tempo?.() || "" })) f.append(k, String(v));
    const res = await S.send("/fragment/perform/slot/hear", f, { refused: "That cell did not play." });
    const url = res?.headers.get("X-Preview-Url");
    if (!url) return;
    alone = s;
    const on = S.isPlaying?.(); // (audio.js: started from silence, swapped, or at the quantise point)
    document.dispatchEvent(new CustomEvent("preview-ready", { detail: { url, deck: true, plays: says() || s.name, autoplay: !on, queue: !!(queue && on) } }));
  }
  function start() {
    T()?.stop("audition"); // a ▷ listen gives way
    const s = sel(), was = alone;
    if (src === "song") {
      alone = null;
      return void (deck()?.dataset.songFilled ? S.songClock?.().play() : document.getElementById("deck-arrange")?.click());
    }
    if (src === "loop" && s?.filled) return void hear(s, false);
    alone = null;
    const row = src === "scene" && s && document.querySelector(`#slots .sl-row[data-row="${s.row}"]`);
    if (row && !row.matches(".is-on")) return void row.querySelector(".sl-scene")?.click(); // its ▸ (slots.js starts the loop)
    // the working loop: asked for (the player's last URL may be a cell's alone), started from silence
    if (!S.isPlaying?.() || was) S.post("/fragment/preview", { view: "perform", ...(S.isPlaying?.() ? {} : { play: "1" }) });
  }
  function setSrc(v, quiet) {
    if (!SRCS.includes(v) || v === src) return;
    src = v;
    try { localStorage.setItem(KEY, JSON.stringify({ src })); } catch { /* this page still has it */ }
    paint();
    if (!quiet && sounding()) start(); // playing: the new source takes over
  }
  // a preview while a cell plays alone: that cell again (an edit saved to it), or another; else the mix
  S.deckHold = (d) => {
    const s = sel();
    if (!alone || !S.isPlaying?.() || src !== "loop" || !s?.filled) { alone = null; return false; }
    hear(s, !!d.queue || s.lane !== alone.lane || s.row !== alone.row);
    return true;
  };
  document.addEventListener("click", (e) => {
    const t = e.target instanceof Element ? e.target : null, d = deck();
    if (!t || !d) return;
    const b = t.closest("[data-deck-src]");
    if (b && d.contains(b)) return void setSrc(b.dataset.deckSrc);
    if (t.closest("#deck-stop")) { alone = null; return void T()?.stopAll(); }
    if (t.closest("#perform-slots .sl-scene:not(:disabled)")) setSrc("scene", true); // a scene launched: the deck plays scenes
    if (t.closest("#deck-rec-menu")) queueMicrotask(paint); // (after keyrec.js pressed its choice)
    if (!t.closest("#perform-transport #play")) return;
    e.preventDefault(); // audio.js's own ▶ stands down: the deck chooses
    if (!sounding()) return void start();
    alone = null; T()?.stop("loop"); T()?.stop("song"); // ▶ is its own stop (Space too)
  }, true);
  document.addEventListener("slot-selected", () => {
    paint();
    const s = sel();
    if (!alone || !S.isPlaying?.()) return;
    if (!(src === "loop" && s?.filled)) { alone = null; S.post("/fragment/preview", { view: "perform" }); } // the mix again
    else if (s.playing) hear(s, true); // (one not playing yet: its launch's preview brings it — deckHold)
  });

  // ---- the meter, the rings: one frame while something plays -----------------------
  const bars = (q) => Math.max(1, +document.querySelector(q)?.value || 1), gcd = (a, b) => (b ? gcd(b, a % b) : a);
  function resting() { // at rest, the bars ▶ would play: the selected cell's (Loop), else the working loop's
    const s = sel(), c = src === "loop" && s?.filled && document.querySelector(`#slots .sl-cell[data-lane="${s.lane}"][data-row="${s.row}"]`);
    if (c) return +c.dataset.bars || 1;
    const a = bars('#transport [name="bars"]'), b = bars('#perform-drum-grid [name="bars"]');
    return (a * b) / gcd(a, b);
  }
  function rings(now) {
    const t0 = S.loopStart?.(), bar = ((S.barBeats?.() || 4) * 60) / (S.tempo?.() || 120), on = S.isPlaying?.() && t0 != null;
    for (const c of document.querySelectorAll('#slots .sl-cell[data-state="playing"]')) {
      const len = bar * (+c.dataset.bars || 1), mine = !alone || (+c.dataset.lane === alone.lane && +c.dataset.row === alone.row);
      c.style.setProperty("--ring", (on && mine ? ((((now - t0) % len) + len) % len) / len : 0).toFixed(3));
    }
  }
  function frame() {
    raf = 0;
    const d = deck();
    if (!d) return;
    const now = S.getCtx?.()?.currentTime ?? 0, list = T()?.list() || [];
    const rec = list.find((x) => x.id === "keyrec"), main = list.find((x) => !x.layer && x.state === "playing");
    let n = Math.min(16, resting()), at = -1, beat = 0, count = 0, text = "—";
    if (rec?.at != null && rec.at - now > 0.02) { count = Math.min(16, Math.ceil((rec.at - now) / (rec.beat || 0.5) - 1e-6)); text = `count-in ${count}`; }
    else if (main) {
      const p = typeof main.pos === "function" ? main.pos() : main.pos, m = /bar (\d+) \/ (\d+)/.exec(p?.text || "");
      if (m) { const page = Math.floor((m[1] - 1) / 16) * 16; n = Math.min(16, m[2] - page); at = m[1] - 1 - page; beat = p.beat ?? 0; text = `${m[1]} / ${m[2]}`; }
    }
    d.querySelector(".deck-meter")?.style.setProperty("--pulse", S.calm?.() ? "1" : Math.max(0.25, 1 - beat * 2.5).toFixed(2));
    d.querySelectorAll(".deck-meter > i").forEach((l, i) => {
      const v = count ? (i < count ? "count" : "off") : i >= n ? "off" : i < at ? "past" : i === at ? "now" : "";
      if ((l.dataset.s || "") !== v) { if (v) l.dataset.s = v; else delete l.dataset.s; }
    });
    const pos = d.querySelector(".deck-pos");
    if (pos && pos.textContent !== text) pos.textContent = text;
    rings(now);
    if (rec || main) raf = requestAnimationFrame(frame);
  }
  document.addEventListener("transport-change", () => {
    const p = document.getElementById("play");
    if (p?.closest(".deck")) p.setAttribute("aria-pressed", String(["loop", "song"].some((id) => T()?.state(id) === "playing")));
    raf ||= requestAnimationFrame(frame);
  });
  document.addEventListener("htmx:after:settle", () => { paint(); raf ||= requestAnimationFrame(frame); });
  document.addEventListener("DOMContentLoaded", paint);
})();
