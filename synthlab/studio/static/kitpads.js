// synthlab/studio/static/kitpads.js
// Kit's pads show their voice (drums.md "The Kit page, laid out"). Where a strip's pad is
// big (dense, from 2200 px: the pad takes what its knobs leave) its one-shot
// is drawn across it — the hit as `/drum/<voice>/hit.wav` serves it (the
// same file a click auditions: a row's clip when it has one), decoded here
// and binned into an SVG the CSS colours from the tokens (both themes) —
// the hit's loudness envelope faintly behind the waveform, and in a corner
// its decay and tune (the strip's own read-outs) and how long it rings.
// Redrawn when the pad is new or morphed (a preset, Load kit: the server's
// pad comes without `data-drawn`) and after the kit's post for a knob of
// that voice has landed — never per frame. A click still auditions it
// (piano.js); this adds the hit flash (`.hit`, as Perform's pads).
// Budget: <= 70 code lines.
(() => {
  "use strict";
  const N = 120, H = 40; // the drawing's columns and height (viewBox; it stretches to the pad)
  let ac = null;
  const decoder = () => (ac ??= new OfflineAudioContext(1, 1, 48000));
  const out = (voice, p) => document.getElementById(`d-${voice}-${p}`)?.parentElement.querySelector("output")?.textContent;
  const pts = (ys, y) => ys.map((v, i) => `${((i + 0.5) * N / ys.length).toFixed(1)},${y(v).toFixed(1)}`);
  async function draw(pad) {
    const svg = pad.querySelector(".pad-wave");
    if (!svg || pad.dataset.drawn || !pad.dataset.hitUrl || !svg.checkVisibility()) return;
    const ticket = (pad.dataset.drawn = String(performance.now()));
    let d;
    try {
      const res = await fetch(pad.dataset.hitUrl, { cache: "no-store" });
      d = (await decoder().decodeAudioData(await res.arrayBuffer())).getChannelData(0);
    } catch { delete pad.dataset.drawn; return; }
    if (pad.dataset.drawn !== ticket) return; // redrawn meanwhile
    let peak = 1e-9, end = 0;
    for (let i = 0; i < d.length; i++) peak = Math.max(peak, Math.abs(d[i]));
    for (let i = 0; i < d.length; i++) if (Math.abs(d[i]) > peak * 0.01) end = i;
    const per = Math.max(1, (end + 1) / N), hi = [], env = [];
    for (let c = 0; c < N; c++) {
      let mx = 0, ss = 0, n = 0;
      for (let j = Math.floor(c * per); j < Math.min(end + 1, Math.floor((c + 1) * per)); j++, n++) {
        mx = Math.max(mx, Math.abs(d[j])); ss += d[j] * d[j];
      }
      hi.push(mx / peak); env.push(n ? Math.sqrt(ss / n) / peak : 0);
    }
    const top = Math.max(...env, 1e-9), mid = H / 2;
    svg.setAttribute("viewBox", `0 0 ${N} ${H}`);
    // (numbers only: the drawing is the hit's own peaks, nothing a user typed)
    const up = pts(hi, (v) => mid - v * mid * 0.9), down = pts(hi, (v) => mid + v * mid * 0.9).reverse();
    svg.innerHTML = `<path class="pad-env" d="M0,${H} L${pts(env, (v) => H - (v / top) * H * 0.92).join(" L")} L${N},${H} Z"/>`
      + `<path class="pad-wav" d="M0,${mid} L${up.join(" L")} L${N},${mid} L${down.join(" L")} Z"/>`;
    const ms = Math.round(((end + 1) / 48000) * 1000), voice = pad.dataset.voice;
    const read = [["dec", out(voice, "decay")], ["tune", out(voice, "tune")]].filter(([, v]) => v != null).map(([k, v]) => `${k} ${v}`);
    pad.querySelector(".pad-read").textContent = [...read, `${ms} ms`].join(" · ");
  }
  // once a frame, in the frame (a response settles once a region; a pad's visibility forces a layout)
  let queued = 0;
  const all = () => { queued ||= requestAnimationFrame(() => { queued = 0; document.querySelectorAll("#drumkit .hit-pad").forEach(draw); }); };
  document.addEventListener("DOMContentLoaded", all);
  document.addEventListener("htmx:after:settle", all);
  for (const ev of ["resize", "synthlab-geometry"]) addEventListener(ev, all); // (shown from 2200 px: a window made wider draws them)
  // a knob of a voice: its pad again once the kit's post has landed
  const dirty = new Set();
  document.addEventListener("input", (e) => {
    const m = /^(\w+)\.\w+$/.exec(e.target.closest?.("#drumkit") ? e.target.name : "");
    if (m) dirty.add(m[1]);
  });
  document.addEventListener("htmx:after:request", (e) => {
    if (!e.detail?.ctx?.sourceElement?.closest?.("#drumkit")) return;
    for (const v of dirty) {
      const pad = document.getElementById(`hitpad-${v}`);
      if (pad) { delete pad.dataset.drawn; draw(pad); }
    }
    dirty.clear();
  });
  // the hit flash when a pad is played (piano.js plays it)
  document.addEventListener("click", (e) => {
    const pad = e.target.closest?.("#drumkit .hit-pad");
    if (!pad) return;
    pad.classList.add("hit");
    setTimeout(() => pad.classList.remove("hit"), 120);
  });
})();
