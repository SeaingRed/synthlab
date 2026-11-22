// synthlab/studio/static/band-worklet.js
// A unit's band for the live keys: effects.band_apply sample by sample —
// any rack unit on the lows, mids or highs (the OTT's Linkwitz-Riley
// crossover, fx2-worklet.js's `crossover` / `split`) or on the mid or side
// channel, the rest passed and summed back in phase with it.
// Loaded into the AudioWorklet scope beside fx-worklet.js (livefx.js adds
// it; fx-worklet.js and fx2-worklet.js are full): a unit fx-worklet.js runs
// gets its band here (`synthlabBand.init` / `.run` / `.quiet`: the band and
// the rest in their own buffers, the unit run on the band, the two summed);
// a native unit (a ConvolverNode, a biquad) sits between two `synthlab-band`
// nodes livefx.js builds round it — the first splits (output 0 the band,
// output 1 the rest), the second joins (input 0 the unit's output, input 1
// the rest). Units arrive resolved by fxlive.live_chain with the band's
// crossover (`band_split`, `band_q`: effects.OTT_SPLIT / OTT_Q).
// The utility unit's twin is here too (`kinds`, `make`, `unit`: its bass
// mono is the same crossover's all-pass and high-pass).
// tests/studio/test_live_fx.py runs this under node against effects.py.
// Budget: <= 76 code lines.

(() => {
  const F = () => globalThis.synthlabFx, X = () => globalThis.synthlabFx2; // fx-worklet.js's biq, fx2-worklet.js's crossover
  const FREQ = ["low", "mid", "high"];

  // a unit's band: the crossover for a frequency band (its state kept from `o`), none for mid / side
  function init(u, o, sr) {
    const freq = FREQ.includes(u.band);
    return { band: u.band, x: freq ? X().crossover({ split: u.band_split, q: u.band_q }, o || {}, sr) : null, tail: freq ? 16384 : 0 };
  }

  // one sample in two: [band L, band R, rest L, rest R] (effects.band_apply's)
  function part(b, l, r) {
    if (b.x) {
      const bl = X().split(b.x[0], l, F().biq), br = X().split(b.x[1], r, F().biq), i = FREQ.indexOf(b.band);
      const [a, c] = [0, 1, 2].filter((j) => j !== i);
      return [bl[i], br[i], bl[a] + bl[c], br[a] + br[c]];
    }
    const m = (l + r) * 0.5, s = (l - r) * 0.5;
    return b.band === "ms_mid" ? [m, m, s, -s] : [s, s, m, m];
  }

  // the band's output and the rest summed back: L / R (mid / side: the output's two channels averaged)
  function join(b, pl, pr, ql, qr) {
    if (b.x) return [pl + ql, pr + qr];
    const y = (pl + pr) * 0.5;
    return [y + ql, (b.band === "ms_side" ? -y : y) + qr];
  }

  // fx-worklet.js's processor: one unit (`unit(L, R)`, in place) over the block, on its band
  function run(b, L, R, N, unit) {
    if (!b.p || b.p[0].length !== N) { b.p = [new Float64Array(N), new Float64Array(N)]; b.q = [new Float64Array(N), new Float64Array(N)]; }
    const [pl, pr] = b.p, [ql, qr] = b.q;
    for (let i = 0; i < N; i++) [pl[i], pr[i], ql[i], qr[i]] = part(b, L[i], R[i]);
    unit(pl, pr);
    for (let i = 0; i < N; i++) [L[i], R[i]] = join(b, pl[i], pr[i], ql[i], qr[i]);
  }
  // culled (silent in, every tail rung out): the crossover at rest
  const quiet = (b) => [...(b.x || []).flat(), ...(b.m || [])].forEach((f) => { f.z1 = f.z2 = 0; }); // (the utility's filters too)

  // The utility (effects._utility): polarity, the side x width, bass mono (the side
  // high-passed twice, the mid all-passed), pan's law, gain. `make`: its state
  const kinds = new Set(["utility"]);
  function make(u, old, sr) {
    const keep = (f, o) => Object.assign(f, o ? { z1: o.z1, z2: o.z2 } : {}), f = u.bass_mono, q = u.xover_q;
    const m = f > 0 ? [X().allpass(f, q, sr), F().rbj("highpass", f, q, sr), F().rbj("highpass", f, q, sr)].map((g, j) => keep(g, old.m?.[j])) : null;
    const by = !u.gain && u.width === 1 && !u.pan && !(f > 0) && !u.flip_l && !u.flip_r; // at its defaults: the input itself
    return { m, by, tail: m ? 16384 : 0 };
  }
  function unit(s, L, R, N) {
    const u = s.u, [gl, gr] = F().panGains(u.pan), g = 10 ** (u.gain / 20), fl = u.flip_l >= 0.5 ? -1 : 1, fr = u.flip_r >= 0.5 ? -1 : 1, biq = F().biq;
    if (s.by) return;
    for (let i = 0; i < N; i++) {
      const l = L[i] * fl, r = R[i] * fr;
      let m = (l + r) * 0.5, d = (l - r) * 0.5 * u.width;
      if (s.m) { m = biq(s.m[0], m); d = biq(s.m[2], biq(s.m[1], d)); }
      L[i] = (m + d) * gl * g; R[i] = (m - d) * gr * g;
    }
  }

  // the split before a banded native unit and the join after it
  class Band extends AudioWorkletProcessor {
    constructor(o) {
      super();
      const p = o?.processorOptions || {};
      this.joins = !!p.join; this.b = init(p.unit || {}, null, sampleRate);
      this.port.onmessage = (e) => { if (e.data.type === "dispose") this.dead = true; };
    }
    process(inputs, outputs) {
      const at = (k, c, i) => (inputs[k]?.[c] ?? inputs[k]?.[0])?.[i] ?? 0, [o, r] = outputs, N = o[0].length;
      for (let i = 0; i < N; i++) {
        if (this.joins) [o[0][i], o[1][i]] = join(this.b, at(0, 0, i), at(0, 1, i), at(1, 0, i), at(1, 1, i));
        else [o[0][i], o[1][i], r[0][i], r[1][i]] = part(this.b, at(0, 0, i), at(0, 1, i));
      }
      return !this.dead;
    }
  }
  registerProcessor("synthlab-band", Band);

  globalThis.synthlabBand = { init, run, quiet, part, join, kinds, make, unit };
})();
