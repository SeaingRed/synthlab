// synthlab/studio/static/click.js
// The metronome: a tick on every beat, so the ear has the tempo
// to hold a beat against. A sparse pattern — one read from a song in the
// Sample Lab, say — does not sound like the tempo it plays at.
//   - #click-btn (the rail) turns it on and off; the choice is kept in
//     localStorage, per browser. ON IS HEARD, recording or not:
//     with nothing playing it runs on its own at the page's tempo, bar 1
//     where it was switched on, not only under the loop.
//   - Whose beat it ticks, the first that holds: a Song take running
//     (songrec.js `songTake`: its at0 and bar) — unless the take's own
//     metronome (#rec-click) is on, which already ticks it; the Song
//     playing (song.js `songClock`: the pass sounding now, whose buffer
//     starts on a bar line, and the plan's bpm); the loop (audio.js
//     `loopStart()` and the page's input[name=bpm]: a loop is whole bars,
//     so the beat runs on across every wrap); else its own. It clicks the
//     METER (meter.py `beats`): the loop's from the meter
//     picker's data-clicks / data-bar-beats, a song section's from the
//     plan (its own tempo, and a ramp's bar lines `bar_starts`) — the bar
//     line highest, a group's head (6/8's fourth eighth) lighter, the rest
//     the plain tick; 4/4 is the bar line and three ticks, as it was. A
//     source that changes starts a new grid.
//   - Ticks go straight to the context's destination, not through the master
//     bus: a jam (audio.js records the master) never has the click on it,
//     and the scope does not show it.
//   - A look-ahead scheduler: every 25 ms it schedules the ticks of the next
//     100 ms, reading the grid and the bpm again on every pass.
// It does not follow Perform's tape-stop / half / double (they bend the
// loop's playback rate, which this does not read).
// Budget: <= 82 code lines (45 -> 70: heard with nothing playing, the
// Song and a take's beat; 70 -> 82: the meter's clicks, a ramp's bars).

(() => {
  "use strict";
  const KEY = "synthlab-click", AHEAD = 0.1, S = () => window.synthlab;
  let on = false, last = -1, own = null; // last: the context time of the last tick; own: the free grid {t0, bpm}
  let was = ""; // the grid's source the last pass ("take" | "song" | "loop" | "own")
  try { on = localStorage.getItem(KEY) === "1"; } catch { /* private mode */ }
  const paint = () => {
    const b = document.getElementById("click-btn");
    if (b) { b.dataset.state = on ? "on" : "off"; b.setAttribute("aria-pressed", String(on)); }
  };
  const tick = (ctx, at, lvl) => { // lvl 2: the bar line, 1: a group's head, 0: a beat
    const osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.frequency.value = [1175, 1480, 1760][lvl];
    gain.gain.setValueAtTime([0.2, 0.25, 0.3][lvl], at);
    gain.gain.exponentialRampToValueAtTime(0.001, at + 0.04);
    osc.connect(gain).connect(ctx.destination);
    osc.start(at); osc.stop(at + 0.05);
  };
  // the page's tempo: its transport's field, else the status strip's "109 bpm"
  const pageBpm = () => Number(document.querySelector('input[name="bpm"]')?.value) || parseFloat(document.querySelector(".st-tempo")?.textContent || "");
  // the loop's meter (fragments/meter_pick.html): [clicks [[beats into the bar, level]], a bar in beats]
  const FOUR = [[[0, 2], [1, 0], [2, 0], [3, 0]], 4];
  function loopMeter() {
    const el = document.querySelector("select[data-clicks]");
    if (!el) return FOUR;
    return [el.dataset.clicks.split(" ").map((p) => p.split(":").map(Number)), Number(el.dataset.barBeats) || 4];
  }
  // [source, the time of a bar line, the beat in seconds, clicks, a bar in beats, bar starts (s) or null], or null: the take's own click ticks it
  function grid(ctx) {
    const s = S(), take = s.songTake?.(), song = s.songClock?.(), now = ctx.currentTime;
    if (take) return document.getElementById("rec-click")?.checked ? null : ["take", take.at0, take.bar / 4, ...FOUR, null];
    if (song?.playing && song.plan?.bpm) {
      const e = song.live.find((x) => x.at <= now + AHEAD && now < x.at + x.dur - x.offset) || song.live[0];
      const sec = e && song.plan.sections[e.i], sr = song.plan.sr;
      if (e) return ["song", e.at - e.offset, 60 / (sec?.bpm || song.plan.bpm), ...(sec?.clicks ? [sec.clicks, sec.bar_beats] : FOUR), sec?.bar_starts?.map((x) => x / sr) || null];
    }
    const start = s.loopStart?.(), bpm = pageBpm();
    if (!(bpm >= 20)) return null;
    if (s.isPlaying?.() && start != null) return ["loop", start, 60 / bpm, ...loopMeter(), null];
    // its own: bar 1 where it was switched on (or where the last player stopped); a new tempo starts on the next beat
    if (!own || was !== "own" || own.bpm !== bpm) own = { t0: was === "own" && last > 0 ? last + 60 / (own?.bpm || bpm) : now + 0.05, bpm };
    return ["own", own.t0, 60 / bpm, ...loopMeter(), null];
  }
  // bar k's [start, length] from t0: even bars, or a ramp's own bar lines (one pass, then round again)
  const barOf = (k, beat, bb, st) => {
    if (!st) return [k * bb * beat, bb * beat];
    const n = st.length - 1, pass = Math.floor(k / n), i = k - pass * n;
    return [pass * st[n] + st[i], st[i + 1] - st[i]];
  };
  const pass = () => {
    const ctx = on && S()?.getCtx?.();
    const g = ctx && ctx.state === "running" ? grid(ctx) : null;
    if (!g) { last = -1; was = ""; return; }
    const [from, t0, beat, clicks, bb, st] = g, now = ctx.currentTime;
    if (from !== was && from !== "own") last = -1;
    was = from;
    // from the bar before now's: each bar's clicks, a share of the bar each
    for (let k = Math.max(0, Math.floor((now - t0) / (bb * beat)) - 1); ; k++) {
      const [b0, len] = barOf(k, beat, bb, st);
      if (t0 + b0 > now + AHEAD) break;
      for (const [q, lvl] of clicks) {
        const at = t0 + b0 + (q / bb) * len;
        if (at > now + AHEAD) break;
        if (at >= now - 1e-3 && at > last + beat / 4) { tick(ctx, Math.max(at, now), lvl); last = at; }
      }
    }
  };
  document.addEventListener("click", (ev) => {
    if (!ev.target.closest?.("#click-btn")) return;
    on = !on;
    if (on) { S()?.ensureCtx?.(); own = null; was = ""; } // a gesture: the context may start; bar 1 is now
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* private mode */ }
    paint();
  });
  document.addEventListener("loop-clock", () => { last = -1; });
  document.addEventListener("htmx:after:settle", paint);
  paint();
  setInterval(pass, 25);
})();
