// synthlab/studio/static/eq-worklet.js
// The FX rack's parametric EQ for the live keys: effects.eq's three bands
// (low shelf, swept mid peak, high shelf), designed here exactly as
// core.shelf_coeffs / core.peaking_coeffs design them and run in series as
// scipy's lfilter runs them (transposed direct form II), per channel.
// A band at 0 dB is not run, as effects.eq_bands leaves it out: the unit
// at its defaults passes its input through untouched.
// Loaded into the AudioWorklet scope beside fx-worklet.js (livefx.js adds
// both modules), which had no room for it: its SynthlabFx calls
// `synthlabEq.init` / `.run` for a unit of kind "eq". The unit arrives
// resolved by fxlive.live_chain, the shelves' corners and slope included
// (effects.eq's own defaults: never retyped here).
// tests/studio/test_live_fx.py runs this file under node against
// effects.py, with fx-worklet.js.
// Budget: <= 40 code lines.

(() => {
  const TAU = 2 * Math.PI;
  const w0 = (fc, sr) => TAU * Math.min(Math.max(fc, 20), sr * 0.49) / sr;
  const band = (b, a) => ({ b0: b[0] / a[0], b1: b[1] / a[0], b2: b[2] / a[0], a1: a[1] / a[0], a2: a[2] / a[0], z1: 0, z2: 0 });

  // core.peaking_coeffs: exactly `db` at fc, unity at DC and Nyquist
  function peak(fc, db, q, sr) {
    if (!db) return null;
    const A = 10 ** (db / 40), w = w0(fc, sr), al = Math.sin(w) / (2 * Math.max(q, 0.05)), cw = Math.cos(w);
    return band([1 + al * A, -2 * cw, 1 - al * A], [1 + al / A, -2 * cw, 1 - al / A]);
  }

  // core.shelf_coeffs: side -1 the low shelf, +1 the high one (the two RBJ
  // forms differ only in the sign of cos w0)
  function shelf(side, fc, db, slope, sr) {
    if (!db) return null;
    const A = 10 ** (db / 40), w = w0(fc, sr), c = side * Math.cos(w);
    const t = 2 * Math.sqrt(A) * (Math.sin(w) / 2) * Math.sqrt((A + 1 / A) * (1 / slope - 1) + 2);
    return band(
      [A * (A + 1 + (A - 1) * c + t), -2 * side * A * (A - 1 + (A + 1) * c), A * (A + 1 + (A - 1) * c - t)],
      [A + 1 - (A - 1) * c + t, 2 * side * (A - 1 - (A + 1) * c), A + 1 - (A - 1) * c - t]);
  }

  // A unit's state: its bands per channel. A slider drag (`old`: the same
  // unit before it) keeps each band's filter state, so it never clicks.
  function init(u, old, sr) {
    const eq = [0, 1].map((ch) => [shelf(-1, u.low_hz, u.low, u.slope, sr), peak(u.mid_freq, u.mid_gain, u.mid_q, sr),
      shelf(1, u.high_hz, u.high, u.slope, sr)].map((f, j) => {
      const was = old.eq?.[ch][j];
      return f && was ? Object.assign(f, { z1: was.z1, z2: was.z2 }) : f;
    }));
    // silence culling: the narrowest band (100 Hz, Q 8, +12 dB) rings under
    // -120 dBFS within 0.8 s
    return { eq, tail: Math.ceil(sr) };
  }

  // y = b0 x + z1; z1 = b1 x - a1 y + z2; z2 = b2 x - a2 y (lfilter)
  function run(s, L, R, N) {
    for (let j = 0; j < 3; j++) {
      const l = s.eq[0][j], r = s.eq[1][j];
      if (!l) continue;
      for (let i = 0; i < N; i++) {
        let x = L[i], y = l.b0 * x + l.z1;
        l.z1 = l.b1 * x - l.a1 * y + l.z2; l.z2 = l.b2 * x - l.a2 * y; L[i] = y;
        x = R[i]; y = r.b0 * x + r.z1;
        r.z1 = r.b1 * x - r.a1 * y + r.z2; r.z2 = r.b2 * x - r.a2 * y; R[i] = y;
      }
    }
  }

  globalThis.synthlabEq = { init, run };
})();
