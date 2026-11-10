// synthlab/studio/static/deckart.js
// The control surface's look: the pixels of the Stream Deck +'s eight
// keys and its touch strip, drawn ONCE here for both decks — deckui.js
// paints them onto the virtual deck's canvases and JPEG-encodes the same
// drawing for the device. "Afterhours": each key a recessed glass button
// (a vignette, a bevel lit from above, the icon or label glowing in its
// role's colour; lit = a rim light and a bloom that pumps on the kick, a
// playing Play pulses with the master level too; a press flashes and sends
// a ring out across the glass), the strip a dark glass bar whose values are
// luminous traces — an afterglow behind a move, sparks thrown off a fast
// one, the read-out rolling to its new value with a flare — and, while the
// transport runs, a mirrored spectrum with peak caps behind them, a beat
// line sweeping each bar and the floor of the column on the beat lit.
//   - The colours are THE PAGE'S TOKENS, read from computed style (`inks`):
//     the role hues (--accent, --signal, --ai, --go, --pause, --danger) and
//     the glass, which is always dark (the device is a black slab; in the
//     day theme it is the warm graphite of --text, not --bg-inset's grey).
//     A token that will not parse falls back to a fixed palette. They are
//     read again only after a theme or a hue moved (data-theme, data-look —
//     a theme, Warm Tube's —, <html>'s style — where the Colours popover
//     sets the hues —, the OS scheme, a `synthlab-colours`), and both decks
//     draw again (`A.restyled`: deckui.js).
//   - The transport keys (Play, Stop all, Jam / Capture) carry the page's
//     pilot lamp (style.css 15): a round jewel in their corner, dark glass in
//     the role's hue while off, lit and blooming while on.
//   - The motion is measured, not guessed: the deck's OWN tap off the master
//     analyser (1024-point, no smoothing of its own: ~12 ms of window) feeds
//     envelopes that rise at once and fall over 150 ms, so the light lands
//     on the beat; the kick is motion.js's (`synthlab.kick`), the bar's
//     phase the transport's (the loop's start, or the song's pass, at the
//     page's bpm). The master analyser motion.js draws from is untouched.
//   - Drawn in layers: what a key or a column SHOWS (its glass, its face, its
//     name, value and trace — the shadow-blurred, costly part) is drawn once
//     per content into a cached canvas, and a frame of motion composes those
//     with the light over them, so a playing deck costs little.
//   - `regions(face)` / `keys(face)`: what to draw, each with a `sig` (its
//     content) and an `amb` (its motion: glow, sparks, ring, pulse,
//     spectrum, beat), so a caller draws only what changed, and the device's
//     motion frames can be throttled apart from its content.
//   - `busy()`: anything still moving. Nothing here runs on a timer: at rest
//     no pixel is drawn (`draws` counts them; the journeys read it). Under
//     calm motion (colours.js's Motion setting) the deck is still: no spectrum, beat, sparks,
//     ring, roll or pump — a press is a plain flash, a move a short glow.
// Budget: <= 420 code lines (260 -> 420: the tap and its
// envelopes, the beat clock, cached layers, sparks, the ring, the roll, the
// mirrored spectrum with caps — so the motion is not 30 fps and 150 ms late).

(() => {
  "use strict";
  const S = (window.synthlab ??= {}), root = document.documentElement;
  const W = 800, H = 100, BACK = 60, GLOW = 800, FLASH = 280, RING = 560, ROLL = 240, FLARE = 600, BANDS = 48;
  const WHITE = [255, 255, 255], BLACK = [0, 0, 0];
  // fallback inks (the night theme's): used only where a token will not parse
  const FALL = { bgInset: [22, 24, 30], text: [233, 230, 220], dim: [148, 154, 168], signal: [96, 214, 210],
    accent: [244, 166, 66], ai: [176, 120, 236], go: [86, 206, 128], pause: [244, 160, 70], danger: [234, 84, 76] };
  const P = {}, A = { draws: 0 };
  const calm = () => window.synthlab.calm(); // the Motion setting (colours.js), never the OS's
  const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
  const rgba = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
  let probe = null, seen = "", stale = true, gen = 0;
  function rgbOf(css, fb) {
    probe ??= Object.assign(document.createElement("canvas"), { width: 1, height: 1 }).getContext("2d", { willReadFrequently: true });
    probe.fillStyle = rgba(fb);
    probe.fillStyle = css || "transparent";
    probe.fillRect(0, 0, 1, 1);
    const d = probe.getImageData(0, 0, 1, 1).data;
    return d[3] ? [d[0], d[1], d[2]] : fb;
  }
  const restyle = () => { stale = true; A.restyled?.(); };
  new MutationObserver(restyle).observe(root, { attributes: true, attributeFilter: ["data-theme", "data-look"] }); // (a hue: synthlab-colours; "style" fired on every --keys-h)
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", restyle);
  document.addEventListener("synthlab-colours", restyle);
  // the page's tokens -> rgb, again only after a theme change (a frame never forces a style recalc)
  A.inks = function inks() {
    if (!stale) return seen;
    stale = false;
    const cs = getComputedStyle(root), t = (name) => cs.getPropertyValue(name).trim();
    const raw = { bgInset: t("--bg-inset"), text: t("--text"), dim: t("--text-dim"), signal: t("--signal"),
      accent: t("--accent"), ai: t("--ai"), go: t("--go"), pause: t("--pause"), danger: t("--danger") };
    const sig = Object.values(raw).join() + t("--font");
    if (sig === seen) return seen;
    seen = sig; gen++;
    const c = Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, rgbOf(v, FALL[k])]));
    // the glass: halfway between the theme's two extremes, then most of the way to black
    P.glass = mix(mix(c.bgInset, c.text, 0.5), BLACK, 0.74);
    P.bezel = mix(P.glass, BLACK, 0.6);
    P.ink = mix(WHITE, c.signal, 0.1);
    P.dim = mix(P.ink, P.glass, 0.45);
    // a role's hue, lifted a little so the day theme's deeper ones still glow on the glass
    for (const k of ["signal", "accent", "ai", "go", "pause", "danger"]) P[k] = mix(c[k], WHITE, 0.15);
    P.font = t("--font") || "system-ui, sans-serif";
    return seen;
  };
  const font = (px, w = 650) => `${w} ${Math.round(px)}px ${P.font}`;

  // ---- layers: what a key or a column shows, drawn once per content ---------------
  // one canvas a slot (a key or a column, on one deck), painted again only when its key moves
  const layers = new Map();
  function layer(slot, key, w, h, s, paint) {
    let L = layers.get(slot);
    const pw = Math.max(1, Math.ceil(w * s)), ph = Math.max(1, Math.ceil(h * s)), k = `${gen}|${key}`;
    if (!L) layers.set(slot, (L = { c: document.createElement("canvas"), k: "" }));
    if (L.k !== k || L.c.width !== pw || L.c.height !== ph) {
      if (L.c.width !== pw || L.c.height !== ph) Object.assign(L.c, { width: pw, height: ph });
      const g = L.c.getContext("2d", { willReadFrequently: slot.endsWith("d") }); // the device's: CPU canvases, as its sheets
      g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, pw, ph);
      g.save(); g.setTransform(s, 0, 0, s, 0, 0); paint(g); g.restore();
      L.k = k;
    }
    return L.c;
  }
  // a spark: a soft dot of light, one sprite a colour
  const sprite = (c) => layer(`sp${c}`, "", 24, 24, 1, (g) => {
    const r = g.createRadialGradient(12, 12, 0, 12, 12, 12);
    r.addColorStop(0, rgba(mix(c, WHITE, 0.7))); r.addColorStop(0.35, rgba(c, 0.7)); r.addColorStop(1, rgba(c, 0));
    g.fillStyle = r; g.fillRect(0, 0, 24, 24);
  });

  // ---- what moves: the deck's tap off the master, the beat, a move, a press ------
  const spec = new Float32Array(BANDS), caps = new Float32Array(BANDS), capAt = new Float64Array(BANDS);
  const ghosts = [], pressed = [], sparks = [];
  let tap = null, from = null, freq = null, wave = null, edges = null;
  let level = 0, loud = false, on = false, kick = 0, phase = -1, sampledAt = -1e9, bpmAt = -1e9, bpm = 0;
  const playing = () => !!S.isPlaying?.() || !!S.songClock?.().playing;
  // a second analyser on the master's output: short, unsmoothed; motion.js's stays as it is
  function tapOf(an) {
    if (from === an) return tap;
    try { tap?.disconnect(); } catch { /* its context is gone */ }
    from = an;
    tap = an.context.createAnalyser();
    Object.assign(tap, { fftSize: 1024, smoothingTimeConstant: 0, minDecibels: -88, maxDecibels: -22 });
    an.connect(tap);
    freq = new Uint8Array(tap.frequencyBinCount); wave = new Uint8Array(tap.fftSize);
    const hz = tap.frequencyBinCount / (an.context.sampleRate / 2); // log bands, 40 Hz to 16 kHz, in fractional bins
    edges = Float32Array.from({ length: BANDS + 1 }, (_, j) => 40 * 400 ** (j / BANDS) * hz);
    return tap;
  }
  function band(j) {
    const lo = edges[j], hi = edges[j + 1];
    if (hi - lo < 1) { const c = (lo + hi) / 2, b = Math.floor(c), f = c - b; return freq[b] * (1 - f) + (freq[b + 1] ?? 0) * f; }
    let pk = 0;
    for (let b = Math.floor(lo); b < hi && b < freq.length; b++) pk = Math.max(pk, freq[b]);
    return pk;
  }
  // where the transport is in its bar (0..1), or -1: the loop's start or the song's pass, at the page's bpm
  function barPhase(now) {
    if (!S.getCtx?.()) return -1;
    if (now - bpmAt > 500) { bpmAt = now; bpm = S.tempo?.() || 0; }
    const t = S.heardTime();
    let start = S.isPlaying?.() ? S.loopStart?.() : null;
    const song = start == null ? S.songClock?.() : null;
    if (song?.playing) { const e = song.live?.findLast?.((x) => x.at <= t); if (e) start = e.at - (e.offset || 0); }
    if (start == null || !(bpm >= 20)) return -1;
    const p = (t - start) / (240 / bpm);
    return p < 0 ? -1 : p - Math.floor(p);
  }
  function sample(now) {
    const dt = Math.min(120, now - sampledAt);
    if (dt < 6) return; // once a frame, whoever asks
    sampledAt = now;
    const an = S.getAnalyser?.(), down = Math.exp(-dt / 150);
    on = playing() && !calm();
    if (an && on) {
      const t = tapOf(an);
      t.getByteFrequencyData(freq); t.getByteTimeDomainData(wave);
      let pk = 0;
      for (const v of wave) pk = Math.max(pk, Math.abs(v - 128) / 128);
      level = Math.max(pk, level * Math.exp(-dt / 220)); // up at once, down over a fifth of a second
      for (let j = 0; j < BANDS; j++) {
        spec[j] = Math.max(band(j) / 255, spec[j] * down);
        if (spec[j] >= caps[j]) { caps[j] = spec[j]; capAt[j] = now; }
        else if (now - capAt[j] > 260) caps[j] = Math.max(spec[j], caps[j] - dt * 0.0012); // held, then falling
      }
      kick = Math.min(1, S.kick?.() || 0);
      phase = barPhase(now);
    } else {
      const r = Math.exp(-dt / 90);
      level *= r; kick *= r; phase = -1;
      for (let j = 0; j < BANDS; j++) { spec[j] *= r; caps[j] *= r; }
    }
    loud = on || level > 0.01 || caps.some((v) => v > 0.01);
  }
  // a column's motion: the afterglow since the move began, sparks off a fast one, the read-out's roll
  function trail(i, c, now, head) {
    const g = ghosts[i];
    if (!g || g.name !== c.name || c.off) { ghosts[i] = { name: c.name, frac: c.frac, from: c.frac, at: -1e9, text: c.value, was: "", rolled: -1e9 }; return ghosts[i]; }
    if (g.frac !== c.frac) {
      const dt = Math.min(200, Math.max(16, now - g.at)), speed = (Math.abs(c.frac - g.frac) / dt) * 1000;
      if (now - g.at > GLOW) g.from = g.frac;
      if (speed > 0.8 && !calm() && !c.meter) spray(i, head(c.frac), Math.sign(c.frac - g.frac), Math.min(7, Math.round(speed * 1.6)), now);
      g.frac = c.frac; g.at = now;
    }
    if (g.text !== c.value) { g.was = g.text; g.text = c.value; g.rolled = calm() || c.meter ? -1e9 : now; }
    return g;
  }
  function spray(i, x, dir, n, now) {
    for (let k = 0; k < n && sparks.length < 72; k++) {
      sparks.push({ i, x, y: 84, vx: -dir * (30 + Math.random() * 110) + (Math.random() - 0.5) * 50,
        vy: -(40 + Math.random() * 120), at: now, life: 420 + Math.random() * 380 });
    }
  }
  const alive = (now) => { for (let k = sparks.length - 1; k >= 0; k--) if (now - sparks[k].at > sparks[k].life) sparks.splice(k, 1); return sparks.length > 0; };
  A.press = (i, now = performance.now()) => { pressed[i] = now; };
  A.busy = (now = performance.now()) => loud || alive(now) || pressed.some((t) => now - t < RING)
    || ghosts.some((g) => g && (now - g.at < GLOW || now - g.rolled < FLARE));
  A.spectrum = spec;

  // ---- the keys -----------------------------------------------------------------
  const ROLE = [[/^(Play|Launcher|Scene \d)$/, "go"], [/^(Jam|Capture|Stop all)$/, "danger"],
    [/^(Fill|Sparser|Busier|Balance)$/, "ai"], [/^Mute$/, "pause"],
    [/^(Macros|Focus|Meters|Mix|Loop|[A-D])$|^Page/, "signal"]];
  const roleOf = (label) => ROLE.find(([re]) => re.test(label))?.[1] || "accent";
  // icons on a -1..1 box: [fill or stroke, path]
  const ICON = {
    play: ["f", (g) => { g.moveTo(-0.62, -0.86); g.lineTo(0.92, 0); g.lineTo(-0.62, 0.86); g.closePath(); }],
    stop: ["f", (g) => g.roundRect(-0.72, -0.72, 1.44, 1.44, 0.18)],
    rec: ["f", (g) => g.arc(0, 0, 0.78, 0, 7)],
    left: ["s", (g) => { g.moveTo(0.38, -0.8); g.lineTo(-0.42, 0); g.lineTo(0.38, 0.8); }],
    right: ["s", (g) => { g.moveTo(-0.38, -0.8); g.lineTo(0.42, 0); g.lineTo(-0.38, 0.8); }],
    undo: ["s", (g) => { g.arc(0.12, 0.12, 0.72, Math.PI, Math.PI * 2.35); g.moveTo(-0.95, -0.2); g.lineTo(-0.6, 0.2); g.lineTo(-0.2, -0.12); }],
    loop: ["s", (g) => { g.arc(0, 0, 0.72, 0.3, Math.PI * 1.85); g.moveTo(0.3, -0.95); g.lineTo(0.62, -0.45); g.lineTo(0.08, -0.32); }],
    more: ["f", (g) => { for (const x of [-0.75, 0, 0.75]) { g.moveTo(x + 0.2, 0); g.arc(x, 0, 0.2, 0, 7); } }],
    bars: ["f", (g) => { for (const [x, h] of [[-0.9, 0.7], [-0.2, 1.3], [0.5, 1.8]]) g.roundRect(x, 0.9 - h, 0.42, h, 0.08); }],
    dial: ["s", (g) => { g.arc(0, 0, 0.8, Math.PI * 0.75, Math.PI * 2.25); g.moveTo(0, 0); g.lineTo(0.38, -0.5); }],
    tick: ["s", (g) => { g.moveTo(-0.62, 0.86); g.lineTo(-0.26, -0.86); g.lineTo(0.26, -0.86); g.lineTo(0.62, 0.86); g.closePath(); g.moveTo(0, 0.5); g.lineTo(0.58, -0.62); }],
  };
  const ICONS = { Play: "play", "Stop all": "stop", Jam: "rec", Capture: "rec", Undo: "undo", Loop: "loop", More: "more",
    Meters: "bars", Macros: "dial", Focus: "dial", Click: "tick", "Page ◀": "left", "◀ Prev": "left", "Page ▶": "right", "Next ▶": "right", "Dup ▶": "right" };
  function icon(g, name, x, y, s) {
    const [how, path] = ICON[name];
    g.save();
    g.translate(x, y); g.scale(s, s);
    g.beginPath(); path(g);
    if (how === "f") g.fill();
    else { g.lineWidth = 0.24; g.lineCap = g.lineJoin = "round"; g.stroke(); }
    g.restore();
  }
  // a label at the size that fits, never squeezed: the device's keys are 120 px
  function label(g, text, x, y, px, room) {
    g.font = font(px);
    const w = g.measureText(text).width;
    if (w > room) g.font = font(Math.max(px * 0.62, (px * room) / w));
    g.fillText(text, x, y);
  }
  const keyBox = (g, w, h, d = 0) => { const u = Math.min(w, h), m = u * 0.035; g.beginPath(); g.roundRect(m + d, m + d, w - 2 * (m + d), h - 2 * (m + d), u * 0.17); };
  // what a key shows: the glass, the bevel, the lamp, the face (the layer under the light)
  function face(g, k, w, h, c, o) {
    const u = Math.min(w, h), m = u * 0.035, lw = Math.max(1, u * 0.022);
    if (o.opaque) { g.fillStyle = rgba(P.bezel); g.fillRect(0, 0, w, h); }
    g.save();
    // the glass: lifted at the centre (by the key's own colour when lit), near-black at the rim
    const v = g.createRadialGradient(w / 2, h * 0.38, 0, w / 2, h * 0.45, Math.max(w, h) * 0.72);
    v.addColorStop(0, rgba(mix(P.glass, k.on ? c : P.ink, k.off ? 0.03 : k.on ? 0.16 : 0.085)));
    v.addColorStop(1, rgba(mix(P.glass, BLACK, 0.55)));
    keyBox(g, w, h); g.fillStyle = v; g.fill(); g.clip();
    const s = g.createLinearGradient(0, m, 0, h * 0.5); // a sheen over the top half
    s.addColorStop(0, rgba(P.ink, 0.08)); s.addColorStop(1, rgba(P.ink, 0));
    g.fillStyle = s; g.fillRect(0, 0, w, h * 0.5);
    const e = g.createLinearGradient(0, m, 0, h - m); // the bevel: light on the top edge, shadow on the bottom
    e.addColorStop(0, rgba(P.ink, 0.32)); e.addColorStop(0.3, rgba(P.ink, 0.04));
    e.addColorStop(0.7, rgba(BLACK, 0)); e.addColorStop(1, rgba(BLACK, 0.75));
    g.lineWidth = lw * 2; g.strokeStyle = e; keyBox(g, w, h); g.stroke();
    g.restore();
    // the role's lamp, top centre — a transport key's is the page's pilot lamp:
    // a round jewel in its corner, dark glass in its hue while off, lit while on
    g.save();
    const jewel = ["play", "stop", "rec"].includes(ICONS[k.label]), r = Math.max(2, u * 0.05), x = m + u * 0.13;
    g.fillStyle = jewel && !k.on ? rgba(mix(c, BLACK, 0.68), k.off ? 0.5 : 1) : rgba(c, k.off ? 0.12 : k.on ? 1 : 0.42);
    if (k.on) { g.shadowColor = rgba(c); g.shadowBlur = u * (jewel ? 0.12 : 0.08); }
    g.beginPath();
    if (jewel) g.arc(x, x, r, 0, 7); else g.roundRect(w / 2 - u * 0.1, m + u * 0.075, u * 0.2, Math.max(2, u * 0.024), u);
    g.fill();
    if (jewel) { g.shadowBlur = 0; g.fillStyle = rgba(WHITE, k.on ? 0.75 : 0.22); g.beginPath(); g.arc(x - r * 0.3, x - r * 0.35, r * 0.3, 0, 7); g.fill(); }
    g.restore();
    // the face: an icon over its label, or the label alone; glowing in the role's colour
    g.save();
    g.fillStyle = g.strokeStyle = k.off ? rgba(P.dim, 0.5) : k.on ? rgba(mix(c, WHITE, 0.35)) : rgba(P.ink, 0.93);
    if (!k.off) { g.shadowColor = rgba(c, k.on ? 0.95 : 0.55); g.shadowBlur = u * (k.on ? 0.11 : 0.05); }
    g.textAlign = "center"; g.textBaseline = "middle"; g.letterSpacing = `${Math.round(u * 0.012)}px`;
    const ic = ICONS[k.label], text = k.label.replace(/[◀▶]/g, "").trim().toUpperCase();
    if (k.label === "More") { // and which of the key pages this is
      icon(g, ic, w / 2, h * 0.44, u * 0.15);
      const [at, of] = o.book || [0, 1], gap = u * 0.075;
      for (let n = 0; n < of; n++) {
        g.fillStyle = n === at ? rgba(P.accent) : rgba(P.dim, 0.6);
        g.beginPath(); g.arc(w / 2 + (n - (of - 1) / 2) * gap, h * 0.74, Math.max(1.5, u * 0.018), 0, 7); g.fill();
      }
    } else if (ic && o.min > u * 0.25) { // a short key on screen: the icon beside its label
      const px = o.min, s = px * 0.42;
      g.font = font(px);
      const tw = Math.min(g.measureText(text).width, w - 2 * m - 4 * s), x0 = (w - tw - 3 * s) / 2;
      icon(g, ic, x0 + s, h * 0.54, s);
      g.textAlign = "left";
      label(g, text, x0 + 3 * s, h * 0.56, px, tw + 1);
    } else if (ic) {
      icon(g, ic, w / 2, h * 0.44, u * 0.14);
      label(g, text, w / 2, h * 0.75, Math.max(o.min, u * 0.13), w - 2 * m - u * 0.1);
    } else label(g, text, w / 2, h * 0.54, Math.max(o.min, u * 0.2), w - 2 * m - u * 0.12);
    g.restore();
  }
  // the light a key gives off, at full strength: a bloom from behind the glass and a
  // rim light glowing inward (the key's corners stay clear)
  function light(g, w, h, c) {
    const u = Math.min(w, h), lw = Math.max(1, u * 0.022);
    keyBox(g, w, h); g.clip();
    const b = g.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, u * 0.62);
    b.addColorStop(0, rgba(c, 0.42)); b.addColorStop(1, rgba(c, 0));
    g.fillStyle = b; g.fillRect(0, 0, w, h);
    g.shadowColor = rgba(c, 0.95); g.shadowBlur = u * 0.14;
    g.lineWidth = lw * 1.5; g.strokeStyle = rgba(mix(c, WHITE, 0.3));
    keyBox(g, w, h, lw / 2); g.stroke();
  }
  function key(g, i, k, w, h, o) {
    A.draws++;
    const role = roleOf(k.label), c = P[role], { flash, pulse, ring } = o.a, dev = o.opaque ? "d" : "v";
    const lit = k.off ? 0 : k.on ? 0.55 + 0.2 * pulse + 0.35 * o.a.kick : 0, hot = Math.min(1, lit + flash);
    g.clearRect(0, 0, w, h);
    g.drawImage(layer(`kf${i}${dev}`, JSON.stringify([k, o.book, o.min, o.opaque]), w, h, 1, (l) => face(l, k, w, h, c, o)), 0, 0, w, h);
    if (hot > 0) {
      g.save();
      g.globalAlpha = hot; g.globalCompositeOperation = "lighter";
      g.drawImage(layer(`kl${i}${dev}`, role, w, h, 1, (l) => light(l, w, h, c)), 0, 0, w, h);
      g.restore();
    }
    if (ring > 0 && ring < 1) { // a press: a ring of light out across the glass
      const u = Math.min(w, h), e = 1 - (1 - ring) ** 3;
      g.save();
      keyBox(g, w, h); g.clip();
      g.lineWidth = Math.max(1, u * 0.06 * (1 - ring));
      g.strokeStyle = rgba(mix(c, WHITE, 0.45), 0.9 * (1 - ring) ** 1.5);
      g.beginPath(); g.arc(w / 2, h / 2, u * (0.12 + 0.62 * e), 0, 7); g.stroke();
      g.restore();
    }
  }
  A.keys = function keys(f, now = performance.now()) {
    sample(now);
    return f.keys.map((k, i) => {
      const t = now - (pressed[i] ?? -1e9);
      const a = { flash: Math.max(0, 1 - t / FLASH) * (calm() ? 0.8 : 1), ring: calm() ? 0 : t / RING,
        pulse: k.on && k.label === "Play" ? Math.min(1, level * 1.4) : 0, kick: k.on ? kick : 0 };
      const book = k.label === "More" ? f.book : null;
      return { sig: JSON.stringify([k, book]),
        amb: `${Math.ceil(a.flash * 8)}|${a.ring < 1 ? Math.ceil(a.ring * 12) : ""}|${Math.round(a.pulse * 8)}|${Math.round(a.kick * 8)}`,
        draw: (g, w, h, min = 0, opaque = false) => key(g, i, k, w, h, { a, book, min, opaque }) };
    });
  };

  // ---- the strip ------------------------------------------------------------------
  // the glass under a column (a light along its top, a line of light between columns, the way back)
  function under(g, w, back, sep) {
    const bg = g.createLinearGradient(0, 0, 0, H); // dark glass, lit from above
    bg.addColorStop(0, rgba(mix(P.glass, P.ink, 0.07))); bg.addColorStop(0.45, rgba(P.glass)); bg.addColorStop(1, rgba(mix(P.glass, BLACK, 0.5)));
    g.fillStyle = bg; g.fillRect(0, 0, w, H);
    g.fillStyle = rgba(P.ink, 0.16); g.fillRect(0, 0, w, 1);
    const sep1 = (sx) => {
      const s = g.createLinearGradient(0, 10, 0, H - 10);
      s.addColorStop(0, rgba(P.ink, 0)); s.addColorStop(0.5, rgba(P.ink, 0.22)); s.addColorStop(1, rgba(P.ink, 0));
      g.fillStyle = s; g.fillRect(sx, 10, 1, H - 20);
    };
    if (sep) sep1(w - 1);
    if (back) { g.strokeStyle = rgba(P.dim); icon(g, "left", 28, 50, 11); sep1(BACK); }
  }
  // the mirrored spectrum across the strip (this column's share of it): a luminous
  // ribbon through the bands' tops and its reflection under a centre line, coloured
  // low to high, its crest lit, each band's held peak a short dash of light over it
  function spectrum(g, x, w) {
    const bw = W / BANDS, mid = 60, up = 30, cx = (j) => (j + 0.5) * bw, hy = (j) => Math.min(1, spec[j] * 1.15) * up;
    const crest = (p, sgn, k) => { // a smooth curve through the tops (sgn -1) or their reflection (+1, k of the height)
      let px = sgn < 0 ? 0 : W, py = mid;
      for (let n = 0; n < BANDS; n++) {
        const j = sgn < 0 ? n : BANDS - 1 - n, X = cx(j), Y = mid + sgn * hy(j) * k;
        p.quadraticCurveTo(px, py, (px + X) / 2, (py + Y) / 2);
        px = X; py = Y;
      }
      p.lineTo(sgn < 0 ? W : 0, mid);
    };
    const top = new Path2D(), body = new Path2D();
    top.moveTo(0, mid); crest(top, -1, 1);
    body.moveTo(0, mid); crest(body, -1, 1); crest(body, 1, 0.55); body.closePath();
    const hue = g.createLinearGradient(0, 0, W, 0);
    hue.addColorStop(0, rgba(P.signal, 0.22)); hue.addColorStop(0.55, rgba(P.ai, 0.2)); hue.addColorStop(1, rgba(P.accent, 0.16));
    g.fillStyle = hue; g.fill(body);
    g.lineWidth = 1.5; g.strokeStyle = rgba(mix(P.signal, WHITE, 0.3), 0.35); g.stroke(top);
    g.fillStyle = rgba(mix(P.signal, WHITE, 0.55), 0.42);
    for (let j = Math.max(0, Math.floor(x / bw)); j < Math.min(BANDS, Math.ceil((x + w) / bw)); j++) {
      const ch = Math.min(1, caps[j] * 1.15) * up;
      if (ch > 1.5) g.fillRect(cx(j) - bw * 0.3, mid - ch - 3, bw * 0.6, 1.5);
    }
  }
  // the bar's beat: a line sweeping the strip once a bar, and the floor of the beat's column lit
  function beat(g, x, w) {
    if (phase < 0) return;
    const at = phase * W, n = Math.floor(phase * 4), f = phase * 4 - n, bx = n * (W / 4);
    if (bx < x + w && bx + W / 4 > x) {
      const fl = g.createLinearGradient(0, H - 26, 0, H);
      fl.addColorStop(0, rgba(P.signal, 0)); fl.addColorStop(1, rgba(P.signal, 0.34 * (1 - f) ** 2 + 0.06));
      g.fillStyle = fl; g.fillRect(bx, H - 26, W / 4, 26);
    }
    if (at > x - 4 && at - 70 < x + w) {
      const tr = g.createLinearGradient(at - 70, 0, at, 0);
      tr.addColorStop(0, rgba(P.signal, 0)); tr.addColorStop(1, rgba(P.signal, 0.12));
      g.fillStyle = tr; g.fillRect(at - 70, 0, 70, H);
      g.fillStyle = rgba(mix(P.signal, WHITE, 0.5), 0.55); g.fillRect(at - 1, 0, 2, H);
    }
  }
  const line = (g, x0, x1, y) => { g.beginPath(); g.moveTo(x0, y); g.lineTo(x1, y); g.stroke(); };
  const colOf = (c) => (c.meter ? P[c.frac > 0.95 ? "danger" : c.frac > 0.7 ? "pause" : "go"] : c.pinned ? P.accent : P.signal);
  function readout(g, c, left, room, big, col, dy = 0, alpha = 1, flare = 0) {
    g.save();
    g.globalAlpha = alpha;
    g.fillStyle = c.off ? rgba(P.dim) : rgba(mix(P.ink, WHITE, flare * 0.6));
    if (!c.off) { g.shadowColor = rgba(col, 0.5 + 0.5 * flare); g.shadowBlur = 10 + 16 * flare; }
    label(g, String(c.value), left, 63 + dy, big ? 32 : 28, room);
    g.restore();
  }
  // what a column shows over the glass: its name, its value, its rail and its trace
  function over(g, c, w, back, value) {
    const left = back + 14, room = w - back - 28, ty = 84, col = colOf(c);
    g.textBaseline = "alphabetic";
    g.fillStyle = c.pinned ? rgba(P.accent) : rgba(P.dim); g.letterSpacing = "1.5px";
    label(g, String(c.name).toUpperCase(), left, 25, 14, room);
    g.letterSpacing = "0px";
    if (value) readout(g, c, left, room, back || c.meter, col);
    g.lineCap = "round"; g.lineWidth = 2; g.strokeStyle = rgba(P.ink, 0.1);
    line(g, left, left + room, ty);
    if (c.off) return;
    const at = left + room * c.frac;
    g.shadowColor = rgba(col, 0.9);
    if (at > left + 1) {
      const lg = g.createLinearGradient(left, 0, at, 0);
      lg.addColorStop(0, rgba(col, 0.2)); lg.addColorStop(1, rgba(col));
      g.shadowBlur = 12; g.lineWidth = 3; g.strokeStyle = lg;
      line(g, left, at, ty);
    }
    g.shadowBlur = 14; g.fillStyle = rgba(mix(col, WHITE, 0.55));
    g.beginPath(); g.arc(at, ty, 4, 0, 7); g.fill();
  }
  function column(g, i, c, x, w, o, now) {
    A.draws++;
    const back = o.back ? BACK : 0, left = back + 14, room = w - back - 28, col = colOf(c), s = g.getTransform().a || 1, gh = ghosts[i];
    const roll = Math.min(1, (now - gh.rolled) / ROLL), flare = Math.max(0, 1 - (now - gh.rolled) / FLARE) ** 2;
    g.save();
    g.beginPath(); g.rect(x, 0, w, H); g.clip();
    g.drawImage(layer(`u${x}${o.dev}`, `${w}|${back}`, w, H, s, (l) => under(l, w, back, x + w < W)), x, 0, w, H);
    if (loud) spectrum(g, x, w);
    beat(g, x, w);
    if (kick > 0.02) { // the kick: light welling up from the strip's bottom edge
      const k = g.createLinearGradient(0, H - 18, 0, H);
      k.addColorStop(0, rgba(P.signal, 0)); k.addColorStop(1, rgba(P.signal, 0.32 * kick));
      g.fillStyle = k; g.fillRect(x, H - 18, w, 18);
    }
    g.translate(x, 0);
    if (o.ghost > 0 && !c.off) { // the afterglow of a move
      g.save();
      g.lineCap = "round"; g.shadowColor = rgba(col, 0.9); g.shadowBlur = 16; g.lineWidth = 7; g.strokeStyle = rgba(col, 0.32 * o.ghost);
      line(g, left + room * o.from, left + room * c.frac, 84);
      g.restore();
    }
    const live = flare > 0 || roll < 1;
    g.drawImage(layer(`o${x}${o.dev}`, `${o.sig}|${live}`, w, H, s, (l) => over(l, c, w, back, !live)), 0, 0, w, H);
    if (live) { // the read-out rolls to its new value, and flares
      const e = 1 - (1 - roll) ** 3, big = back || c.meter;
      g.save(); g.beginPath(); g.rect(0, 34, w, 38); g.clip();
      if (roll < 1) readout(g, { ...c, value: gh.was }, left, room, big, col, -16 * e, 1 - e);
      readout(g, c, left, room, big, col, 16 * (1 - e), e, flare);
      g.restore();
    }
    g.globalCompositeOperation = "lighter";
    for (const p of sparks) {
      const t = (now - p.at) / 1000, life = 1 - (now - p.at) / p.life;
      if (p.i !== i || life <= 0) continue;
      const px = p.x - x + p.vx * t, py = p.y + p.vy * t + 160 * t * t, r = 3 + 6 * life;
      g.globalAlpha = life;
      g.drawImage(sprite(col), px - r, py - r, 2 * r, 2 * r);
    }
    g.restore();
  }
  // the strip as regions: each knows where it is, what it shows, and how to draw it
  A.regions = function regions(f, now = performance.now()) {
    sample(now);
    alive(now);
    const one = (c, i, x, w, back) => {
      const left = x + (back ? BACK : 0) + 14, room = w - (back ? BACK : 0) - 28;
      const g = trail(i, c, now, (fr) => left + room * fr), glow = Math.max(0, 1 - (now - g.at) / GLOW) * (calm() ? 0.6 : 1);
      const j0 = Math.floor((x / W) * BANDS), j1 = Math.ceil(((x + w) / W) * BANDS);
      let bands = "";
      if (loud) for (let j = j0; j < j1; j++) bands += String.fromCharCode(48 + Math.round(spec[j] * 24), 48 + Math.round(caps[j] * 24));
      const at = phase * W, beatAmb = phase < 0 ? "" : `${Math.floor(phase * 4)}:${Math.round((phase * 4 % 1) * 8)}:${at > x - 4 && at - 70 < x + w ? Math.round(at / 2) : ""}`;
      const moving = sparks.some((p) => p.i === i) || now - g.rolled < FLARE ? Math.floor(now / 16) : "";
      const sig = JSON.stringify([c, back]);
      return { x, w, sig, amb: `${bands}|${beatAmb}|${Math.round(kick * 10)}|${glow > 0 ? `${Math.ceil(glow * 10)}@${g.from}` : ""}|${moving}`,
        draw: (gc, dev = false) => column(gc, i, c, x, w, { back, ghost: glow, from: g.from, sig, dev: dev ? "d" : "v" }, now) };
    };
    if (f.meter) return [one({ name: "master level", value: f.meter.cpu, frac: Math.round(f.meter.level * 50) / 50, meter: true }, 8, 0, W, false)];
    if (f.zoom >= 0) return [one(f.cols[f.zoom], f.zoom, 0, W, true)];
    return f.cols.map((c, i) => one(c, i, i * 200, 200, false));
  };
  A.size = { W, H, KEY: 120 };
  S.deckArt = A;
})();
