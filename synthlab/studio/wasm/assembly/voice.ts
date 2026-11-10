// synthlab/studio/wasm/assembly/voice.ts -> static/voice-dsp.wasm
// The live voice's inner loops (static/voice-worklet.js), in AssemblyScript
// with 128-bit SIMD: f64x2, two lanes of the JS worklet's own float64 math
// in the same operation order, so the two engines' f32 output agrees to
// the last bit almost everywhere (sin/tanh/exp/2^x are vmath.ts's kernels,
// within a few ulp of V8's). Not an island: the island budgets cover
// static/*.js, and the glue that drives this module is counted there.
//
// Per voice per 64-sample block — voice allocation, messages, smoothing,
// the mod matrix and the filter coefficients stay in JS:
//   control(): the control pass of a voice with no mod route (envelopes,
//              LFOs, vibrato/pitch env/glide, amp) into a scratch set;
//   stack():   the unison stack (osc2, cross-FM into osc1, osc1, the noise
//              layer, ring, levels, sub, equal-power pan) into A/B;
//   post():    dc_block, filter (ladder = SVF -> soft_clip -> SVF, or one
//              SVF), vowel tract, amp and post drive, added into the f32
//              output.
// What the two lanes carry (the "mode"): a voice's unison copies two at a
// time (stack) and its L/R channels (post, stereo); or, for unison 1, two
// voices at once (MODE_PAIR: each lane a whole mono voice, its own slot and
// scratch set) — most sounds are unison 1, where the second lane would
// otherwise idle. Oscillator shapes here: saw/square/sine/triangle/supersaw/
// fm/noise/fm4, and osc2 hard-synced (syncLane); a warped
// saw/square/sine/triangle (warpLane); wavetable and pluck run
// in JS and fill A/B for post().
// No allocation: init() lays out a fixed arena (two scratch sets, pair
// buffers, constants, voice-state slots) past __heap_base; JS keeps typed
// views on it.
// xpost(): post() for the general filter chain
// (core.filt: the new types and filter 2), one lane at a time — a channel's
// filters are branches, the comb a ring of RING samples a channel and
// filter — over its own state (xslot(): 32 f64, then the rings).

import { vexp, vexp2, vsin, vtanh } from "./vmath";

const MAXU = 8;
const SLOT: usize = 10912; // bytes of one voice's state (see slot layout)
const TAU = 2 * Math.PI;
const NOISE_LEVEL = 0.3;

// scratch arrays, in voice-worklet.js's SCRATCH order, 64 f64 each; set 1
// (a paired voice's) follows set 0
const F = 0, W = 1, G = 2, L1 = 3, L2 = 4, LS = 5, LN = 6, M1 = 7, M2 = 8, DR = 9, IX = 10,
  FA = 11, RG = 12, WP = 13, A = 19, B = 20, E1 = 21, X1 = 25, X2 = 26;
const NSCR = 27; // E1..E4: the fm4 operators' level x decay; X1, X2: the warps' amounts

// slot layout (bytes): ph 128 f64 | qs 8 | dc 4 (x, y as L/R pairs) | s 8
// (filter: stage-1 s0, s1, stage-2 s0, s1 as L/R pairs) | fs 12 (tract:
// per band s0, s1 as L/R pairs) | cs 168 (noise filters) | rs 24 u32 |
// wr 1024 (a warped oscillator's history, WRING per slot x copy)
const QS: usize = 1024, DC: usize = 1088, SV: usize = 1120, FS: usize = 1184, CS: usize = 1280,
  RS: usize = 2624, WR: usize = 2720;
// constants (f64 index into konst); K_CO: the filter coefficients a1 a2 a3
// b1 b2 b3 kk, each as a lane pair
const K_SSDET = 0, K_DET = 8, K_PL = 16, K_PR = 24, K_FMT = 32, K_MAKEUP = 47,
  K_PINK = 48, K_POLES = 51, K_TAPS = 57, K_BROWN = 63, K_CO = 66, K_FM4 = 80, K_WARP = 86, K_NTAP = 88;
// core.warped: WARP_OS, WARP_RING, WARP_BEND (the FIR's taps sit at wtaps)
const WOS = 4, WRING = 64, WBEND = 15.0;
// xpost's numbers (voice-worklet.js xk), after
// K_NTAP: K_X the route (0 off, 1 serial, 2 parallel), then each filter's
// 13 — type, the first SVF (kk a1 a2 a3), the second's, g, s, i, fr — from
// K_X + 1 and K_X + 14 (to K_X + 26 = 116); K_TB tanh(the diode's bias), as
// JS computes it
const K_X = 90, K_TB = 120, DIODE_B = 0.25;
// an x-state slot: the SVF states (ch x filter x 6) and the combs' write
// positions in 32 f64, then four rings of RING f64 (its own arena, after
// the voice slots, the dummy and the warp's FIR taps)
const RING = 2048, XSLOT: usize = 256 + 4 * RING * 8;
// core.fm4's FM4_INDEX (a modulator's radians per unit of output)
const FM4_IX = 6.0;
// post() modes
const MODE_STEREO = 0, MODE_MONO = 1, MODE_PAIR = 2;

// control pass inputs (par, per render) and one voice's control state (ctl,
// in and out per block): f64 indices, mirrored in voice-worklet.js
const P_ATT = 0, P_DEC = 1, P_SUS = 2, P_REL = 3, P_CURVE = 4, P_DV = 5, P_DP = 6, P_DTR = 7,
  P_DFL = 8, P_DM = 9, P_VDL = 10, P_PE = 11, P_PK = 12, P_FMI = 13, P_IXD = 14, P_FMIX = 15,
  P_OCT1 = 16, P_OCT2 = 17, P_M1 = 18, P_M2 = 19, P_SR = 20, P_RELN = 21, P_FREL = 22, P_SYNC = 23,
  P_SM = 24, P_DRC = 54, P_KN = 55, P_FM4 = 56, P_FL = 57, P_FD = 61;
// smoothed [start, delta] pairs from P_SM: l1 l2 ls ln vd pw td dr d1 d2 fa rg wp,
// then the bend in semitones (pitch_bend x bend_range + the wheel), then
// sync_pitch. P_DRC: drift x DRIFT_CENTS, P_KN its knot length; P_FL / P_FD
// the four fm4 levels and decay rates (-1 / (decay x sr), 0 holds)
const C_N = 0, C_REL = 1, C_RELN = 2, C_RELLV = 3, C_FRELLV = 4, C_A0 = 5, C_FE = 6, C_PV = 7,
  C_PP = 8, C_PT = 9, C_PF = 10, C_PM = 11, C_FREQ = 12, C_GLN = 13, C_GLR = 14, C_TGT = 15,
  C_FADE = 16, C_FADEN = 17, C_VEL = 18, C_DW0 = 19, C_DW1 = 20, C_KST = 21;

let scr: usize = 0, pfq: usize = 0, po1: usize = 0, po2: usize = 0, pnz: usize = 0;
let outL: usize = 0, outR: usize = 0, konst: usize = 0, par: usize = 0, ctl: usize = 0;
let slots: usize = 0, dummy: usize = 0, wtaps: usize = 0, xslots: usize = 0;
let pdOut: i32 = 0, pk1Out: f64 = 0, bpv: f64 = 0;

export function init(nslots: i32): usize {
  const base = (__heap_base + 15) & ~(<usize>15);
  scr = base; pfq = scr + 2 * NSCR * 512; po1 = pfq + 1024; po2 = po1 + 1024; pnz = po2 + 1024;
  outL = pnz + 1024; outR = outL + 512; konst = outR + 512; par = konst + 1024; ctl = par + 1024;
  wtaps = ctl + 256; slots = wtaps + 512;
  dummy = slots + <usize>nslots * SLOT; // one more slot: a mono voice's unused R lane state
  xslots = dummy + SLOT;
  const end = xslots + <usize>nslots * XSLOT;
  const need = <i32>((end + 0xffff) >> 16) - memory.size();
  if (need > 0) memory.grow(need);
  return base;
}
// scratch array i (0..24 set 0, 25..49 set 1)
export function scratch(i: i32): usize { return scr + <usize>i * 512; }
export function out(ch: i32): usize { return ch ? outR : outL; }
export function konstAt(): usize { return konst; }
export function parAt(): usize { return par; }
export function ctlAt(): usize { return ctl; }
export function slot(i: i32): usize { return slots + <usize>i * SLOT; }
export function xslot(i: i32): usize { return xslots + <usize>i * XSLOT; }
export function lastPd(): i32 { return pdOut; }
export function lastPk1(): f64 { return pk1Out; }
export function warpAt(): usize { return wtaps; }

@inline function setb(set: i32): usize { return scr + <usize>set * (NSCR * 512); }
@inline function Sb(b: usize, i: i32, j: i32): f64 { return load<f64>(b + (<usize>i << 9) + (<usize>j << 3)); }
@inline function K(i: i32): f64 { return load<f64>(konst + (<usize>i << 3)); }
@inline function pair(a: f64, b: f64): v128 { return f64x2.replace_lane(f64x2.splat(a), 1, b); }
// scratch array i at sample j: lane 0 from set base a, lane 1 from set base b
@inline function Sp(a: usize, b: usize, i: i32, j: i32): v128 { return pair(Sb(a, i, j), Sb(b, i, j)); }
@inline function x0(v: v128): f64 { return f64x2.extract_lane(v, 0); }
@inline function x1(v: v128): f64 { return f64x2.extract_lane(v, 1); }
@inline function sp(x: f64): v128 { return f64x2.splat(x); }
// scalar calls of the vector kernels (lane 0): the control pass's sin, exp, 2^x
@inline function fsin(x: f64): f64 { return x0(vsin(sp(x))); }
@inline function fexp(x: f64): f64 { return x0(vexp(sp(x))); }
@inline function fexp2(x: f64): f64 { return x0(vexp2(sp(x))); }
@inline function ftanh(x: f64): f64 { return x0(vtanh(sp(x))); }
@inline function vfrac(x: v128): v128 { return f64x2.sub(x, f64x2.floor(x)); }
// a lane pair from two addresses, and back
@inline function ld2(a: usize, b: usize): v128 { return pair(load<f64>(a), load<f64>(b)); }
@inline function st2(a: usize, b: usize, v: v128): void { store<f64>(a, x0(v)); store<f64>(b, x1(v)); }

@inline function Pv(i: i32): f64 { return load<f64>(par + (<usize>i << 3)); }
@inline function Cv(i: i32): f64 { return load<f64>(ctl + (<usize>i << 3)); }
@inline function setC(i: i32, x: f64): void { store<f64>(ctl + (<usize>i << 3), x); }
@inline function setS(b: usize, i: i32, j: i32, x: f64): void { store<f64>(b + (<usize>i << 9) + (<usize>j << 3), x); }
// a smoothed param a fraction x through the block
@inline function sm(i: i32, x: f64): f64 { return Pv(P_SM + 2 * i) + Pv(P_SM + 2 * i + 1) * x; }

// core.adsr (voice-worklet.js env/fall): linear attack from `from`, decay
// and release bent by `curve`
function env(t: f64, a: f64, d: f64, s: f64, curve: f64, from: f64): f64 {
  if (t < a) return a > 0 ? from + (1 - from) * (t / a) : 1;
  if (t < a + d) return 1 - (1 - s) * Math.pow((t - a) / (d != 0 ? d : 1e-4), 1 / curve);
  return s;
}
function fall(elapsed: f64, span: f64, curve: f64): f64 {
  if (!(span > 0)) return 0;
  return 1 - Math.pow(Math.min(1, elapsed / span), 1 / curve);
}

// The control pass of a voice with no mod route (voice-worklet.js block()'s
// first loop, routes aside): envelopes, LFO phases, vibrato / pitch env /
// glide, pwm width, amp x velocity x tremolo x steal fade, the smoothed
// levels and pitch multipliers, fm index, the pitch bend — into scratch set `set`, c
// samples from `off` of an N-sample quantum. The block-start filter
// coefficients are the caller's (they need fe and the filter LFO there).
// Returns how many samples ran: fewer than c when the release or a steal
// fade ended the voice.
export function control(off: i32, c: i32, N: i32, set: i32): i32 {
  const sb = setb(set);
  const sr = Pv(P_SR), isr = 1 / sr, iN = 1 / <f64>N, curve = Pv(P_CURVE);
  const att = Pv(P_ATT), dec = Pv(P_DEC), sus = Pv(P_SUS), rel = Pv(P_REL), relMax = Pv(P_RELN), frel = Pv(P_FREL);
  const dv = Pv(P_DV), dp = Pv(P_DP), dtr = Pv(P_DTR), dfl = Pv(P_DFL), dm = Pv(P_DM);
  const vdl = Pv(P_VDL), pe = Pv(P_PE), pk = Pv(P_PK), fmIx = Pv(P_FMIX) != 0, fmi = Pv(P_FMI);
  const ixd = Pv(P_IXD), oct1 = Pv(P_OCT1), oct2 = Pv(P_OCT2), m1 = Pv(P_M1), m2 = Pv(P_M2);
  const sync = Pv(P_SYNC) != 0, drc = Pv(P_DRC), kn = Pv(P_KN), fm4on = Pv(P_FM4) != 0;
  const dw0 = Cv(C_DW0), dw1 = Cv(C_DW1), kst = Cv(C_KST);
  let vn = Cv(C_N), released = Cv(C_REL) != 0, relN = Cv(C_RELN), fe = Cv(C_FE);
  const relLv = Cv(C_RELLV), fRelLv = Cv(C_FRELLV), a0 = Cv(C_A0), vel = Cv(C_VEL);
  let pv = Cv(C_PV), pp = Cv(C_PP), pt = Cv(C_PT), pf = Cv(C_PF), pm = Cv(C_PM);
  let freq = Cv(C_FREQ), glN = Cv(C_GLN), fade = Cv(C_FADE);
  const glR = Cv(C_GLR), tgt = Cv(C_TGT), fadeN = Cv(C_FADEN);
  let n = 0;
  for (; n < c; n++) {
    const x = <f64>(off + n) * iN, t = vn * isr;
    if (fadeN != 0 && fade <= 0) break; // stolen: faded out
    let amp: f64;
    if (released) {
      if (!(rel > 0) || relN >= relMax) break;
      const fl = fall(relN / sr, rel, curve);
      amp = relLv * fl; fe = fRelLv * fall(relN / sr, frel, curve); relN++;
    } else amp = env(t, att, dec, sus, curve, a0);
    pv += dv; pp += dp; pt += dtr; pf += dfl; pm += dm;
    const vd = sm(4, x), pw = sm(5, x), td = sm(6, x);
    let f = freq;
    if (vd > 0) f *= fexp2(fsin(TAU * pv) * vd * Math.min(1, Math.max(0, (t - vdl) / 0.3)) / 12);
    if (pe != 0) f *= fexp2(pe * fexp(vn * pk) / 12);
    const bst = sm(13, x);
    if (bst != 0) f *= fexp2(bst / 12);
    if (drc != 0) { // analog drift's wander (the block's knot pair from JS)
      const xk = (vn - kst) / kn, sk = xk * xk * (3 - 2 * xk);
      f *= fexp2(drc * (dw0 + (dw1 - dw0) * sk) / 1200);
    }
    setS(sb, F, n, f);
    setS(sb, W, n, pw > 0 ? 0.5 + fsin(TAU * pp) * pw * 0.45 : 0.5);
    let g = amp * vel * (td > 0 ? 1 - td * (1 - (fsin(TAU * pt) + 1) * 0.5) : 1);
    if (fadeN != 0) { g *= fade / fadeN; fade--; }
    setS(sb, G, n, g);
    setS(sb, L1, n, sm(0, x)); setS(sb, L2, n, sm(1, x)); setS(sb, LS, n, sm(2, x));
    setS(sb, LN, n, sm(3, x)); setS(sb, DR, n, sm(7, x));
    setS(sb, M1, n, m1 != 0 ? m1 : fexp2(oct1 + sm(8, x) / 1200));
    setS(sb, M2, n, m2 != 0 ? m2 : fexp2(oct2 + sm(9, x) / 1200 + (sync ? sm(14, x) / 12 : 0)));
    if (fmIx) setS(sb, IX, n, fmi * (ixd != 0 ? fexp(vn * ixd) : 1));
    if (fm4on) for (let k = 0; k < 4; k++) {
      const lv = Pv(P_FL + k), kd = Pv(P_FD + k);
      setS(sb, E1 + k, n, kd != 0 ? lv * fexp(vn * kd) : lv);
    }
    setS(sb, FA, n, sm(10, x)); setS(sb, RG, n, sm(11, x)); setS(sb, WP, n, sm(12, x));
    vn++;
    if (glN > 0) { freq *= glR; glN--; if (glN == 0) freq = tgt; }
  }
  setC(C_N, vn); setC(C_RELN, relN); setC(C_FE, fe);
  setC(C_PV, pv); setC(C_PP, pp); setC(C_PT, pt); setC(C_PF, pf); setC(C_PM, pm);
  setC(C_FREQ, freq); setC(C_GLN, glN); setC(C_FADE, fade);
  return n;
}

// polyBLEP (core._polyblep), both lanes, branch-free: t < dt, else t > 1 - dt
@inline function blep(t: v128, dt: v128): v128 {
  const one = sp(1.0);
  const lo = f64x2.lt(t, dt), hi = f64x2.gt(t, f64x2.sub(one, dt));
  if (!v128.any_true(v128.or(lo, hi))) return sp(0); // most samples: no edge near
  const a = f64x2.div(t, dt);
  const ya = f64x2.sub(f64x2.sub(f64x2.add(a, a), f64x2.mul(a, a)), one);
  const b = f64x2.div(f64x2.sub(t, one), dt);
  const yb = f64x2.add(f64x2.add(f64x2.add(f64x2.mul(b, b), b), b), one);
  return v128.bitselect(ya, v128.bitselect(yb, sp(0), hi), lo);
}

// core.noise, n samples of stream j of slot sl into one lane of a pair
// buffer (`out` already offset to the lane): xorshift uniforms summed to a
// unit-variance white; pink and brown run core.colour_noise's filters on it
function noise(sl: usize, j: i32, out: usize, n: i32, kind: i32): void {
  const rs = sl + RS + (<usize>j << 2), cs = sl + CS + <usize>j * 56;
  const Kq = Math.sqrt(3) / 65536;
  // the filter state and coefficients in locals for the loop
  let c0 = load<f64>(cs), c1 = load<f64>(cs, 8), c2 = load<f64>(cs, 16), c3 = load<f64>(cs, 24);
  let c4 = load<f64>(cs, 32), c5 = load<f64>(cs, 40), c6 = load<f64>(cs, 48);
  const p0 = K(K_POLES), p1 = K(K_POLES + 1), p2 = K(K_POLES + 2), p3 = K(K_POLES + 3);
  const p4 = K(K_POLES + 4), p5 = K(K_POLES + 5), q0 = K(K_TAPS), q1 = K(K_TAPS + 1);
  const q2 = K(K_TAPS + 2), q3 = K(K_TAPS + 3), q4 = K(K_TAPS + 4), q5 = K(K_TAPS + 5);
  const pd = K(K_PINK), plast = K(K_PINK + 1), pg = K(K_PINK + 2), br = K(K_BROWN), bg = K(K_BROWN + 1);
  let x = load<u32>(rs);
  for (let i = 0; i < n; i++) {
    x ^= x << 13; x ^= x >> 17; x ^= x << 5; const a = x;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5; const b = x;
    const w = <f64>(<i32>((a & 0xffff) + (a >> 16) + (b & 0xffff) + (b >> 16)) - 131070) * Kq;
    let y: f64;
    if (kind == 0) y = w * NOISE_LEVEL;
    else if (kind == 2) { c0 = br * c0 + bg * w; y = c0 * NOISE_LEVEL; }
    else {
      let s = pd * w + plast * c6;
      c0 = p0 * c0 + q0 * w; s += c0;
      c1 = p1 * c1 + q1 * w; s += c1;
      c2 = p2 * c2 + q2 * w; s += c2;
      c3 = p3 * c3 + q3 * w; s += c3;
      c4 = p4 * c4 + q4 * w; s += c4;
      c5 = p5 * c5 + q5 * w; s += c5;
      c6 = w;
      y = s * pg * NOISE_LEVEL;
    }
    store<f64>(out + (<usize>i << 4), y);
  }
  store<u32>(rs, x);
  store<f64>(cs, c0); store<f64>(cs, c1, 8); store<f64>(cs, c2, 16); store<f64>(cs, c3, 24);
  store<f64>(cs, c4, 32); store<f64>(cs, c5, 40); store<f64>(cs, c6, 48);
}

// One block of oscillator slot s (0 osc1, 1 osc2) at the frequencies in
// pfq, into the pair buffer `out`: lane 0 is copy ua of slot la (scratch
// set sa: W, IX), lane 1 copy ub of slot lb (set sb) when `both`.
// shape: 0 none, 1 saw, 2 square, 3 sine, 4 triangle, 5 supersaw, 6 fm,
// 7 noise, 8 fm4.
function osc(shape: i32, s: i32, la: usize, ua: i32, lb: usize, ub: i32, both: bool, sa: usize,
  sb: usize, out: usize, c: i32, isr: f64, fmr: f64, nkind: i32): void {
  const o0 = la + (<usize>((s * MAXU + ua) * 8) << 3), o1 = lb + (<usize>((s * MAXU + ub) * 8) << 3);
  const visr = sp(isr), vtau = sp(TAU);
  if (shape == 7) {
    noise(la, s * MAXU + ua, out, c, nkind);
    if (both) noise(lb, s * MAXU + ub, out + 8, c, nkind);
    return;
  }
  if (shape == 5) { // core.supersaw: 7 saws, +-18 cents, / sqrt(7)
    for (let j = 0; j < c; j++) v128.store(out + (<usize>j << 4), sp(0));
    for (let k = 0; k < 7; k++) {
      const kd = sp(K(K_SSDET + k) * isr), ko = <usize>k << 3;
      let q = pair(load<f64>(o0 + ko), both ? load<f64>(o1 + ko) : 0);
      for (let j = 0; j < c; j++) {
        const p = out + (<usize>j << 4);
        const dj = f64x2.mul(v128.load(pfq + (<usize>j << 4)), kd);
        q = vfrac(f64x2.add(q, dj));
        const y = f64x2.sub(f64x2.sub(f64x2.add(q, q), sp(1)), blep(q, dj));
        v128.store(p, f64x2.add(v128.load(p), y));
      }
      store<f64>(o0 + ko, x0(q));
      if (both) store<f64>(o1 + ko, x1(q));
    }
    const r7 = sp(Math.sqrt(7));
    for (let j = 0; j < c; j++) {
      const p = out + (<usize>j << 4);
      v128.store(p, f64x2.div(v128.load(p), r7));
    }
    return;
  }
  if (shape == 8) { fm4(o0, o1, both, sa, sb, out, c, isr); return; }
  let p = pair(load<f64>(o0), both ? load<f64>(o1) : 0);
  if (shape == 1) {
    for (let j = 0; j < c; j++) {
      const dt = f64x2.mul(v128.load(pfq + (<usize>j << 4)), visr);
      p = vfrac(f64x2.add(p, dt));
      v128.store(out + (<usize>j << 4), f64x2.sub(f64x2.sub(f64x2.add(p, p), sp(1)), blep(p, dt)));
    }
  } else if (shape == 2) {
    for (let j = 0; j < c; j++) {
      const dt = f64x2.mul(v128.load(pfq + (<usize>j << 4)), visr);
      p = vfrac(f64x2.add(p, dt));
      const w = Sp(sa, sb, W, j);
      const y = vfrac(f64x2.add(p, f64x2.sub(sp(1), w)));
      const sq = v128.bitselect(sp(1), sp(-1), f64x2.lt(p, w));
      v128.store(out + (<usize>j << 4), f64x2.sub(f64x2.add(sq, blep(p, dt)), blep(y, dt)));
    }
  } else if (shape == 3) {
    for (let j = 0; j < c; j++) {
      const dt = f64x2.mul(v128.load(pfq + (<usize>j << 4)), visr);
      p = vfrac(f64x2.add(p, dt));
      v128.store(out + (<usize>j << 4), vsin(f64x2.mul(vtau, p)));
    }
  } else if (shape == 4) {
    for (let j = 0; j < c; j++) {
      const dt = f64x2.mul(v128.load(pfq + (<usize>j << 4)), visr);
      p = vfrac(f64x2.add(p, dt));
      v128.store(out + (<usize>j << 4), f64x2.sub(f64x2.mul(sp(4), f64x2.abs(f64x2.sub(p, sp(0.5)))), sp(1)));
    }
  } else if (shape == 6) { // core.fm: sin(carrier + index * sin(modulator))
    let m = pair(load<f64>(o0, 8), both ? load<f64>(o1, 8) : 0);
    const vr = sp(fmr);
    for (let j = 0; j < c; j++) {
      const dt = f64x2.mul(v128.load(pfq + (<usize>j << 4)), visr);
      p = vfrac(f64x2.add(p, dt));
      m = vfrac(f64x2.add(m, f64x2.mul(dt, vr)));
      const ix = Sp(sa, sb, IX, j);
      v128.store(out + (<usize>j << 4), vsin(f64x2.add(f64x2.mul(vtau, p), f64x2.mul(ix, vsin(f64x2.mul(vtau, m))))));
    }
    store<f64>(o0, x0(m), 8);
    if (both) store<f64>(o1, x1(m), 8);
  } else {
    for (let j = 0; j < c; j++) v128.store(out + (<usize>j << 4), sp(0));
    return;
  }
  store<f64>(o0, x0(p));
  if (both) store<f64>(o1, x1(p));
}

// core.fm4 on both lanes (voice-worklet.js fm4()): operator phases at
// o+0..3, operator 4's last two outputs at o+4, o+5; levels x decay from
// E1..E4; ratios, feedback (radians) and the algorithm from konst K_FM4.
function fm4(o0: usize, o1: usize, both: bool, sa: usize, sb: usize, out: usize, c: i32, isr: f64): void {
  const vtau = sp(TAU), ix = sp(FM4_IX), half = sp(0.5), visr = sp(isr);
  const r1 = sp(K(K_FM4)), r2 = sp(K(K_FM4 + 1)), r3 = sp(K(K_FM4 + 2)), r4 = sp(K(K_FM4 + 3));
  const beta = sp(K(K_FM4 + 4)), al = <i32>K(K_FM4 + 5);
  let p1 = ld2(o0, both ? o1 : o0), p2 = ld2(o0 + 8, both ? o1 + 8 : o0 + 8);
  let p3 = ld2(o0 + 16, both ? o1 + 16 : o0 + 16), p4 = ld2(o0 + 24, both ? o1 + 24 : o0 + 24);
  let y1 = ld2(o0 + 32, both ? o1 + 32 : o0 + 32), y2 = ld2(o0 + 40, both ? o1 + 40 : o0 + 40);
  for (let j = 0; j < c; j++) {
    const dt = f64x2.mul(v128.load(pfq + (<usize>j << 4)), visr);
    p1 = vfrac(f64x2.add(p1, f64x2.mul(dt, r1))); p2 = vfrac(f64x2.add(p2, f64x2.mul(dt, r2)));
    p3 = vfrac(f64x2.add(p3, f64x2.mul(dt, r3))); p4 = vfrac(f64x2.add(p4, f64x2.mul(dt, r4)));
    const s4 = vsin(f64x2.add(f64x2.mul(vtau, p4), f64x2.mul(f64x2.mul(beta, f64x2.add(y1, y2)), half)));
    y2 = y1; y1 = s4;
    const e1 = Sp(sa, sb, E1, j), e2 = Sp(sa, sb, E1 + 1, j), e3 = Sp(sa, sb, E1 + 2, j);
    const o4 = f64x2.mul(Sp(sa, sb, E1 + 3, j), s4);
    const a1 = f64x2.mul(vtau, p1), a2 = f64x2.mul(vtau, p2), a3 = f64x2.mul(vtau, p3);
    let y: v128;
    if (al == 0) { // 4 -> 3 -> 2 -> 1
      const o3 = f64x2.mul(e3, vsin(f64x2.add(a3, f64x2.mul(ix, o4))));
      const o2 = f64x2.mul(e2, vsin(f64x2.add(a2, f64x2.mul(ix, o3))));
      y = f64x2.mul(e1, vsin(f64x2.add(a1, f64x2.mul(ix, o2))));
    } else if (al == 1) { // (2 -> 1) + (4 -> 3)
      const o3 = f64x2.mul(e3, vsin(f64x2.add(a3, f64x2.mul(ix, o4))));
      const o2 = f64x2.mul(e2, vsin(a2));
      y = f64x2.div(f64x2.add(f64x2.mul(e1, vsin(f64x2.add(a1, f64x2.mul(ix, o2)))), o3), sp(2));
    } else if (al == 2) { // 2 + 3 + 4 -> 1
      const o3 = f64x2.mul(e3, vsin(a3)), o2 = f64x2.mul(e2, vsin(a2));
      const arg = f64x2.add(f64x2.add(f64x2.add(a1, f64x2.mul(ix, o2)), f64x2.mul(ix, o3)), f64x2.mul(ix, o4));
      y = f64x2.mul(e1, vsin(arg));
    } else if (al == 3) { // 2 -> 1, 3, 4
      const o3 = f64x2.mul(e3, vsin(a3)), o2 = f64x2.mul(e2, vsin(a2));
      const o1 = f64x2.mul(e1, vsin(f64x2.add(a1, f64x2.mul(ix, o2))));
      y = f64x2.div(f64x2.add(f64x2.add(o1, o3), o4), sp(3));
    } else { // 1 + 2 + 3 + 4
      const o3 = f64x2.mul(e3, vsin(a3)), o2 = f64x2.mul(e2, vsin(a2));
      const o1 = f64x2.mul(e1, vsin(a1));
      y = f64x2.div(f64x2.add(f64x2.add(f64x2.add(o1, o2), o3), o4), sp(4));
    }
    v128.store(out + (<usize>j << 4), y);
  }
  store<f64>(o0, x0(p1)); store<f64>(o0 + 8, x0(p2)); store<f64>(o0 + 16, x0(p3));
  store<f64>(o0 + 24, x0(p4)); store<f64>(o0 + 32, x0(y1)); store<f64>(o0 + 40, x0(y2));
  if (both) {
    store<f64>(o1, x1(p1)); store<f64>(o1 + 8, x1(p2)); store<f64>(o1 + 16, x1(p3));
    store<f64>(o1 + 24, x1(p4)); store<f64>(o1 + 32, x1(y1)); store<f64>(o1 + 40, x1(y2));
  }
}

// the four syncable shapes' plain value at phase t (core._naive)
@inline function naive(shape: i32, t: f64, w: f64): f64 {
  if (shape == 1) return 2 * t - 1;
  if (shape == 2) return t < w ? 1 : -1;
  if (shape == 4) return 4 * Math.abs(t - 0.5) - 1;
  return fsin(TAU * t);
}
@inline function cl01(x: f64): f64 { return x < 0 ? 0 : x > 1 ? 1 : x; }

// core.warp_phase / warp_end by mode code (voice-worklet.js warpPhase / warpEnd)
@inline function bend(t: f64, k: f64): f64 { return t / (k - (k - 1) * t); }
function warpPhase(m: i32, t: f64, a: f64): f64 {
  const k = 1 + WBEND * a;
  if (m == 1) return bend(t, k);
  if (m == 2) return k * t / (1 + (k - 1) * t);
  if (m == 3) { if (t < 0.5) return 0.5 * bend(2 * t, k); const h = 2 * t - 1; return 0.5 + 0.5 * (k * h / (1 + (k - 1) * h)); }
  if (m == 4) { const d = 0.5 - 0.49 * a; return t < d ? 0.5 * t / d : 0.5 + 0.5 * (t - d) / (1 - d); }
  if (m == 5) { const c = 1 - 0.5 * a; return t < c ? t / c : (1 - t) / Math.max(1 - c, 1e-9); }
  if (m == 6) return t < 0.5 ? 0.5 * bend(2 * t, k) : t;
  if (m == 7) { const q = 1 - a, n = 2 + Math.floor(254 * q * q * q); return Math.floor(t * n) / n; }
  return m == 8 ? Math.min(t / (1 - 0.95 * a), 1) : t;
}
function warpEnd(m: i32, a: f64): f64 {
  if (m == 5) return a > 0 ? 0 : 1;
  if (m == 7) { const q = 1 - a, n = 2 + Math.floor(254 * q * q * q); return (n - 1) / n; }
  return 1;
}
@inline function blep1(t: f64, dt: f64): f64 {
  if (t < dt) { const x = t / dt; return x + x - x * x - 1; }
  if (t > 1 - dt) { const x = (t - 1) / dt; return x * x + x + x + 1; }
  return 0;
}

// core.warped on one lane (voice-worklet.js warpBlock): copy u of oscillator
// s (shape 1..4, warp mode wm) in slot `sl`, scratch set base `sbase` (its
// W and X1 / X2), frequencies on lane `lane` of pfq, out on that lane of
// `out`. WOS sub-samples a sample into the copy's ring (its write index at
// ph o+7), out the FIR over it. `isr`: 1 / (sr x WOS).
function warpLane(shape: i32, wm: i32, sl: usize, s: i32, u: i32, sbase: usize, lane: usize, out: usize,
  c: i32, isr: f64): void {
  const o = sl + (<usize>((s * MAXU + u) * 8) << 3), rb = sl + WR + (<usize>((s * MAXU + u) * WRING) << 3);
  const xs = s ? X2 : X1, nh = <i32>K(K_NTAP);
  let p = load<f64>(o), at = <i32>load<f64>(o, 56);
  for (let j = 0; j < c; j++) {
    const jo = (<usize>j << 4) + lane;
    const d = load<f64>(pfq + jo) * isr, a = Math.min(1, Math.max(0, Sb(sbase, xs, j))), w = Sb(sbase, W, j);
    for (let q = 0; q < WOS; q++) {
      const x = p + d;
      p = x - Math.floor(x);
      let y = naive(shape, warpPhase(wm, p, a), w);
      if (p < d || p > 1 - d) y += (naive(shape, 0, w) - naive(shape, warpEnd(wm, a), w)) / 2 * blep1(p, d);
      at = (at + 1) & (WRING - 1);
      store<f64>(rb + (<usize>at << 3), y);
    }
    let acc: f64 = 0;
    for (let k = 0; k < nh; k++) acc += load<f64>(wtaps + (<usize>k << 3)) * load<f64>(rb + (<usize>((at - k) & (WRING - 1)) << 3));
    store<f64>(out + jo, acc);
  }
  store<f64>(o, p); store<f64>(o, <f64>at, 56);
}

// core.sync on one lane (voice-worklet.js syncBlock, scalar: the events
// are branches): copy u of osc2 in slot `sl`, scratch set base `sbase`
// (its W), master frequencies on lane `lane` of po1, slave frequencies on
// that lane of pfq, out on that lane of `out`. State: the slave phase
// (o+0), the master's (o+8) and the sample held back (o+16).
function syncLane(shape: i32, sl: usize, u: i32, sbase: usize, lane: usize, out: usize, c: i32, isr: f64): void {
  const o = sl + (<usize>((MAXU + u) * 8) << 3);
  const saw = shape == 1, sq = shape == 2, edgy = saw || sq;
  let t = load<f64>(o), tm = load<f64>(o, 8), hold = load<f64>(o, 16);
  for (let j = 0; j < c; j++) {
    const jo = (<usize>j << 4) + lane;
    const dm = load<f64>(po1 + jo) * isr, ds = Math.max(load<f64>(pfq + jo) * isr, 1e-12);
    const w = sq ? Sb(sbase, W, j) : 0.5;
    let now: f64 = 0, pre: f64 = 0, xe: f64 = 0, J: f64 = 0;
    tm += dm;
    if (tm >= 1) {
      tm -= 1;
      const x = cl01(tm / Math.max(dm, 1e-12)), tp = t, tb = tp + ds * (1 - x), tbw = tb >= 1 ? tb - 1 : tb;
      const after = ds * x;
      if (edgy && tb >= 1) {
        xe = cl01(x + (tb - 1) / ds); J = saw ? -2 : 2; now -= J / 2 * (1 - xe) * (1 - xe); pre += J / 2 * xe * xe;
      }
      if (saw) J = -2 * tbw;
      else if (sq) {
        const b0 = tp + 1 - w, b1 = tb + 1 - w;
        if (Math.floor(b0) < Math.floor(b1)) { xe = cl01(x + (b1 - Math.floor(b1)) / ds); now += (1 - xe) * (1 - xe); pre -= xe * xe; }
        J = 2 * ((1 - w) % 1 - (tbw + 1 - w) % 1) + 2 * tbw;
        if (after >= w) { xe = cl01((after - w) / ds); now += (1 - xe) * (1 - xe); pre -= xe * xe; }
      } else J = naive(shape, 0, w) - naive(shape, tbw, w);
      now -= J / 2 * (1 - x) * (1 - x); pre += J / 2 * x * x;
      t = after;
    } else {
      const tp = t, un = t + ds;
      t = un >= 1 ? un - 1 : un;
      if (edgy && un >= 1) { xe = cl01(t / ds); J = saw ? -2 : 2; now -= J / 2 * (1 - xe) * (1 - xe); pre += J / 2 * xe * xe; }
      if (sq) {
        const b0 = tp + 1 - w, b1 = un + 1 - w;
        if (Math.floor(b0) < Math.floor(b1)) { xe = cl01((b1 - Math.floor(b1)) / ds); now += (1 - xe) * (1 - xe); pre -= xe * xe; }
      }
    }
    store<f64>(out + jo, hold + pre);
    hold = naive(shape, t, w) + now;
  }
  store<f64>(o, t); store<f64>(o, tm, 8); store<f64>(o, hold, 16);
}

// The unison stack into A/B (c samples). Two lanes at a time: voice slot la
// (scratch set seta) copies u, u + 1 of U; or, with `paired`, unison 1 of
// voice la (set seta) and voice lb (set setb), each into its own set's A/B.
// flags: 1 osc2, 2 ring, 4 cross-FM, 8 sub, 16 noise layer, 32 osc2
// hard-synced to osc1 (its master frequencies through po1). Per-copy
// detune and pan come from konst (K_DET/K_PL/K_PR), written once per
// render.
export function stack(la: usize, lb: usize, seta: i32, setb_: i32, paired: bool, U: i32, sh1: i32,
  sh2: i32, flags: i32, c: i32, sr: f64, fmr: f64, nkind: i32): void {
  const isr = 1 / sr, has2 = (flags & 1) != 0, ring = (flags & 2) != 0, cross = (flags & 4) != 0;
  const sub = (flags & 8) != 0, nz = (flags & 16) != 0, sync = (flags & 32) != 0, vtau = sp(TAU);
  // each oscillator's warp (0: none), on the shapes warpLane reads
  const wm1 = sh1 >= 1 && sh1 <= 4 ? <i32>K(K_WARP) : 0, wm2 = sh2 >= 1 && sh2 <= 4 ? <i32>K(K_WARP + 1) : 0;
  const isr4 = isr / WOS;
  const sa = setb(seta), sb = paired ? setb(setb_) : sa, lB = paired ? lb : la;
  const aA = sa + (<usize>A << 9), aB = sa + (<usize>B << 9);
  const bA = sb + (<usize>A << 9), bB = sb + (<usize>B << 9);
  memory.fill(aA, 0, <usize>c << 3); memory.fill(aB, 0, <usize>c << 3);
  if (paired) { memory.fill(bA, 0, <usize>c << 3); memory.fill(bB, 0, <usize>c << 3); }
  const n = paired ? 1 : U;
  for (let u = 0; u < n; u += 2) {
    const ub = paired ? 0 : u + 1, both = paired || u + 1 < U;
    const du = pair(K(K_DET + u), both ? K(K_DET + ub) : K(K_DET + u));
    if (has2) {
      for (let j = 0; j < c; j++) {
        v128.store(pfq + (<usize>j << 4), f64x2.mul(f64x2.mul(Sp(sa, sb, F, j), du), Sp(sa, sb, M2, j)));
      }
      if (sync) { // the master at osc1's pitch (before cross-FM), in po1 until osc1 fills it
        for (let j = 0; j < c; j++) {
          v128.store(po1 + (<usize>j << 4), f64x2.mul(f64x2.mul(Sp(sa, sb, F, j), du), Sp(sa, sb, M1, j)));
        }
        syncLane(sh2, la, u, sa, 0, po2, c, isr);
        if (both) syncLane(sh2, lB, ub, sb, 8, po2, c, isr);
      } else if (wm2 != 0) {
        warpLane(sh2, wm2, la, 1, u, sa, 0, po2, c, isr4);
        if (both) warpLane(sh2, wm2, lB, 1, ub, sb, 8, po2, c, isr4);
      } else osc(sh2, 1, la, u, lB, ub, both, sa, sb, po2, c, isr, fmr, nkind);
    }
    for (let j = 0; j < c; j++) {
      let f = f64x2.mul(f64x2.mul(Sp(sa, sb, F, j), du), Sp(sa, sb, M1, j));
      if (cross) { // x (1 + 0.9 fm_amount osc2) where fm_amount > 0
        const fa = Sp(sa, sb, FA, j), o2 = v128.load(po2 + (<usize>j << 4));
        const m = f64x2.add(sp(1), f64x2.mul(f64x2.mul(sp(0.9), fa), o2));
        f = v128.bitselect(f64x2.mul(f, m), f, f64x2.gt(fa, sp(0)));
      }
      v128.store(pfq + (<usize>j << 4), f);
    }
    if (wm1 != 0) {
      warpLane(sh1, wm1, la, 0, u, sa, 0, po1, c, isr4);
      if (both) warpLane(sh1, wm1, lB, 0, ub, sb, 8, po1, c, isr4);
    } else osc(sh1, 0, la, u, lB, ub, both, sa, sb, po1, c, isr, fmr, nkind);
    if (nz) {
      noise(la, 2 * MAXU + u, pnz, c, nkind);
      if (both) noise(lB, 2 * MAXU + ub, pnz + 8, c, nkind);
    }
    const qa = la + QS + (<usize>u << 3), qb = lB + QS + (<usize>ub << 3);
    let qs = pair(load<f64>(qa), both ? load<f64>(qb) : 0);
    const hs = f64x2.div(f64x2.mul(du, sp(0.5)), sp(sr));
    const pl0 = K(K_PL + u), pr0 = K(K_PR + u);
    const pl1 = both ? K(K_PL + ub) : 0, pr1 = both ? K(K_PR + ub) : 0;
    for (let j = 0; j < c; j++) {
      const jo = <usize>j << 4;
      let o1 = v128.load(po1 + jo), y: v128;
      if (has2) {
        const o2 = v128.load(po2 + jo);
        if (ring) {
          const rg = Sp(sa, sb, RG, j);
          const r = f64x2.add(f64x2.mul(o1, f64x2.sub(sp(1), rg)), f64x2.mul(f64x2.mul(o1, o2), rg));
          o1 = v128.bitselect(r, o1, f64x2.gt(rg, sp(0)));
        }
        y = f64x2.add(f64x2.mul(Sp(sa, sb, L1, j), o1), f64x2.mul(Sp(sa, sb, L2, j), o2));
      } else y = f64x2.mul(Sp(sa, sb, L1, j), o1);
      if (sub) {
        qs = vfrac(f64x2.add(qs, f64x2.mul(Sp(sa, sb, F, j), hs)));
        y = f64x2.add(y, f64x2.mul(Sp(sa, sb, LS, j), vsin(f64x2.mul(vtau, qs))));
      }
      if (nz) y = f64x2.add(y, f64x2.mul(Sp(sa, sb, LN, j), v128.load(pnz + jo)));
      // lane 0 then lane 1, as the JS sums copy by copy (or voice by voice)
      const j8 = <usize>j << 3;
      store<f64>(aA + j8, load<f64>(aA + j8) + x0(y) * pl0);
      store<f64>(aB + j8, load<f64>(aB + j8) + x0(y) * pr0);
      if (both) {
        store<f64>(bA + j8, load<f64>(bA + j8) + x1(y) * pl1);
        store<f64>(bB + j8, load<f64>(bB + j8) + x1(y) * pr1);
      }
    }
    store<f64>(qa, x0(qs));
    if (both) store<f64>(qb, x1(qs));
  }
}

// dc_block, filter, tract, amp and post drive for c samples, added into the
// f32 output from `off`. mode: MODE_STEREO (voice la's L and R), MODE_MONO
// (la's L; its R lane only runs the dc blocker, as the JS voice does),
// MODE_PAIR (la's L on lane 0 from set seta, lb's L on lane 1 from set
// setb, each summed into both outputs, la first). type: 0 ladder,
// 1 lowpass, 2 highpass, 3 bandpass, 4 notch, 5 none. Coefficients per lane
// in konst K_CO. flags: 1 per-sample drive (DR), 2 lane 0's dc state set,
// 4 lane 1's, 8 lane 0's post drive latched (pd), 16 lane 1's, 32 vowel
// tract. Returns lane 0's (the voice's) peak; lastPk1() lane 1's in
// MODE_PAIR, lastPd() the latches (bit 0 lane 0, bit 1 lane 1).
export function post(la: usize, lb: usize, seta: i32, setb_: i32, mode: i32, off: i32, c: i32, N: i32,
  type: i32, flags: i32, d0: f64, td0: f64, faa: f64, fad: f64, r: f64): f64 {
  const perD = (flags & 1) != 0, fmt = (flags & 32) != 0;
  let dc0 = (flags & 2) != 0, dc1 = (flags & 4) != 0, pd0 = (flags & 8) != 0, pd1 = (flags & 16) != 0;
  const paired = mode == MODE_PAIR, stereo = mode == MODE_STEREO, ladder = type == 0;
  const sa = setb(seta), sb = paired ? setb(setb_) : sa;
  // where each lane reads and keeps its state: filter/tract (f), dc (d)
  const f0 = la, f1 = paired ? lb : stereo ? la + 8 : dummy + 8;
  const d1 = paired ? lb : la + 8;
  const in0 = sa + (<usize>A << 9), in1 = paired ? sb + (<usize>A << 9) : sa + (<usize>B << 9);
  const inx0 = sa + (<usize>B << 9), inx1 = sb + (<usize>B << 9); // MODE_PAIR: each voice's R, dc only
  const g0 = sa + (<usize>G << 9), g1 = sb + (<usize>G << 9);
  const vr = sp(r), two = sp(2);
  const ck = konst + (<usize>K_CO << 3);
  const va1 = v128.load(ck), va2 = v128.load(ck, 16), va3 = v128.load(ck, 32);
  const vb1 = v128.load(ck, 48), vb2 = v128.load(ck, 64), vb3 = v128.load(ck, 80), vkk = v128.load(ck, 96);
  let xp = ld2(la + DC, d1 + DC), yp = ld2(la + DC + 16, d1 + DC + 16);
  let xq = ld2(la + DC + 8, lb + DC + 8), yq = ld2(la + DC + 24, lb + DC + 24); // MODE_PAIR only
  let s0 = ld2(f0 + SV, f1 + SV), s1 = ld2(f0 + SV + 16, f1 + SV + 16);
  let t0 = ld2(f0 + SV + 32, f1 + SV + 32), t1 = ld2(f0 + SV + 48, f1 + SV + 48);
  let e0 = ld2(f0 + FS, f1 + FS), e1 = ld2(f0 + FS + 16, f1 + FS + 16);
  let h0 = ld2(f0 + FS + 32, f1 + FS + 32), h1 = ld2(f0 + FS + 48, f1 + FS + 48);
  let k0 = ld2(f0 + FS + 64, f1 + FS + 64), k1 = ld2(f0 + FS + 80, f1 + FS + 80);
  const fk = konst + (<usize>K_FMT << 3), makeup = K(K_MAKEUP);
  let pk0: f64 = 0, pk1: f64 = 0;
  for (let j = 0; j < c; j++) {
    const j8 = <usize>j << 3;
    const ab = pair(load<f64>(in0 + j8), load<f64>(in1 + j8));
    // dc state from a voice's first sample
    if (!dc0) xp = f64x2.replace_lane(xp, 0, x0(ab));
    if (!dc1) xp = f64x2.replace_lane(xp, 1, x1(ab));
    const y = f64x2.add(f64x2.sub(ab, xp), f64x2.mul(vr, yp));
    xp = ab; yp = y;
    if (paired) {
      const xb = pair(load<f64>(inx0 + j8), load<f64>(inx1 + j8));
      if (!dc0) xq = f64x2.replace_lane(xq, 0, x0(xb));
      if (!dc1) xq = f64x2.replace_lane(xq, 1, x1(xb));
      yq = f64x2.add(f64x2.sub(xb, xq), f64x2.mul(vr, yq)); xq = xb;
    }
    dc0 = true; dc1 = true;
    let d = d0, td = td0;
    if (perD) { d = Sb(sa, DR, j); td = d > 0 ? x0(vtanh(sp(d))) : 0; }
    let o: v128;
    if (type == 5) o = y;
    else {
      let v3 = f64x2.sub(y, s1);
      let v1 = f64x2.add(f64x2.mul(va1, s0), f64x2.mul(va2, v3));
      let v2 = f64x2.add(f64x2.add(s1, f64x2.mul(va2, s0)), f64x2.mul(va3, v3));
      s0 = f64x2.sub(f64x2.mul(two, v1), s0); s1 = f64x2.sub(f64x2.mul(two, v2), s1);
      if (ladder) { // soft_clip(drive), then the second SVF
        const z = d > 0 ? f64x2.div(vtanh(f64x2.mul(v2, sp(d))), sp(td)) : v2;
        v3 = f64x2.sub(z, t1);
        v1 = f64x2.add(f64x2.mul(vb1, t0), f64x2.mul(vb2, v3));
        v2 = f64x2.add(f64x2.add(t1, f64x2.mul(vb2, t0)), f64x2.mul(vb3, v3));
        t0 = f64x2.sub(f64x2.mul(two, v1), t0); t1 = f64x2.sub(f64x2.mul(two, v2), t1);
        o = v2;
      } else if (type == 1) o = v2;
      else if (type == 2) o = f64x2.sub(f64x2.sub(y, f64x2.mul(vkk, v1)), v2);
      else if (type == 3) o = f64x2.mul(vkk, v1);
      else o = f64x2.sub(y, f64x2.mul(vkk, v1));
    }
    if (fmt) { // core.formant: three bandpass SVFs, blended by amount
      const fa = Math.min(1, Math.max(0, faa + fad * <f64>(off + j) / <f64>N));
      let v3 = f64x2.sub(o, e1);
      let v1 = f64x2.add(f64x2.mul(sp(load<f64>(fk, 8)), e0), f64x2.mul(sp(load<f64>(fk, 16)), v3));
      let v2 = f64x2.add(f64x2.add(e1, f64x2.mul(sp(load<f64>(fk, 16)), e0)), f64x2.mul(sp(load<f64>(fk, 24)), v3));
      e0 = f64x2.sub(f64x2.mul(two, v1), e0); e1 = f64x2.sub(f64x2.mul(two, v2), e1);
      let wet = f64x2.mul(v1, sp(load<f64>(fk, 32)));
      v3 = f64x2.sub(o, h1); // band 2
      v1 = f64x2.add(f64x2.mul(sp(load<f64>(fk, 48)), h0), f64x2.mul(sp(load<f64>(fk, 56)), v3));
      v2 = f64x2.add(f64x2.add(h1, f64x2.mul(sp(load<f64>(fk, 56)), h0)), f64x2.mul(sp(load<f64>(fk, 64)), v3));
      h0 = f64x2.sub(f64x2.mul(two, v1), h0); h1 = f64x2.sub(f64x2.mul(two, v2), h1);
      wet = f64x2.add(wet, f64x2.mul(v1, sp(load<f64>(fk, 72))));
      v3 = f64x2.sub(o, k1); // band 3
      v1 = f64x2.add(f64x2.mul(sp(load<f64>(fk, 88)), k0), f64x2.mul(sp(load<f64>(fk, 96)), v3));
      v2 = f64x2.add(f64x2.add(k1, f64x2.mul(sp(load<f64>(fk, 96)), k0)), f64x2.mul(sp(load<f64>(fk, 104)), v3));
      k0 = f64x2.sub(f64x2.mul(two, v1), k0); k1 = f64x2.sub(f64x2.mul(two, v2), k1);
      wet = f64x2.add(wet, f64x2.mul(v1, sp(load<f64>(fk, 112))));
      o = f64x2.add(f64x2.mul(o, sp(1 - fa)), f64x2.mul(f64x2.mul(wet, sp(makeup)), sp(fa)));
    }
    o = f64x2.mul(o, pair(load<f64>(g0 + j8), load<f64>(g1 + j8)));
    // non-ladder drive over 1; a routed drive latches, as core's gate does
    if (!ladder) {
      const on0 = perD ? (pd0 = pd0 || d > 1) : d > 1;
      const on1 = paired ? (perD ? (pd1 = pd1 || d > 1) : d > 1) : on0;
      if (on0 || on1) {
        const z = f64x2.div(vtanh(f64x2.mul(o, sp(d))), sp(td));
        o = pair(on0 ? x0(z) : x0(o), on1 ? x1(z) : x1(o));
      }
    }
    const oa = x0(o), ob = stereo ? x1(o) : oa;
    const pl = outL + (<usize>(off + j) << 2), pr = outR + (<usize>(off + j) << 2);
    store<f32>(pl, <f32>(<f64>load<f32>(pl) + oa));
    store<f32>(pr, <f32>(<f64>load<f32>(pr) + ob));
    pk0 = Math.max(pk0, Math.max(Math.abs(oa), Math.abs(ob)));
    if (paired) {
      const oc = x1(o);
      store<f32>(pl, <f32>(<f64>load<f32>(pl) + oc));
      store<f32>(pr, <f32>(<f64>load<f32>(pr) + oc));
      pk1 = Math.max(pk1, Math.abs(oc));
    }
  }
  st2(la + DC, d1 + DC, xp); st2(la + DC + 16, d1 + DC + 16, yp);
  if (paired) { st2(la + DC + 8, lb + DC + 8, xq); st2(la + DC + 24, lb + DC + 24, yq); }
  st2(f0 + SV, f1 + SV, s0); st2(f0 + SV + 16, f1 + SV + 16, s1);
  st2(f0 + SV + 32, f1 + SV + 32, t0); st2(f0 + SV + 48, f1 + SV + 48, t1);
  st2(f0 + FS, f1 + FS, e0); st2(f0 + FS + 16, f1 + FS + 16, e1);
  st2(f0 + FS + 32, f1 + FS + 32, h0); st2(f0 + FS + 48, f1 + FS + 48, h1);
  st2(f0 + FS + 64, f1 + FS + 64, k0); st2(f0 + FS + 80, f1 + FS + 80, k1);
  pdOut = (pd0 ? 1 : 0) | (pd1 ? 2 : 0);
  pk1Out = pk1;
  return pk0;
}

// --- the general filter chain (voice-worklet.js xf / xchain / xpost,
// scalar: one channel at a time) ---------------------------------------------

// one TPT SVF step on the state at st (s0) and st + stride (s1): returns
// low, leaves band in bpv (voice-worklet.js svf)
@inline function svfS(st: usize, stride: usize, x: f64, a1: f64, a2: f64, a3: f64): f64 {
  const s0 = load<f64>(st), s1 = load<f64>(st + stride);
  const v3 = x - s1, v1 = a1 * s0 + a2 * v3, v2 = s1 + a2 * s0 + a3 * v3;
  store<f64>(st, 2 * v1 - s0); store<f64>(st + stride, 2 * v2 - s1);
  bpv = v1;
  return v2;
}

// one sample of channel ch through filter fi of the chain, its numbers at
// konst kb (xk's layout); state at xa (voice-worklet.js xf)
function xf(xa: usize, ch: i32, fi: i32, x: f64, kb: i32, d: f64, td: f64): f64 {
  const t = <i32>K(kb);
  if (t == 5) return x;
  const st = xa + (<usize>((ch * 2 + fi) * 6) << 3);
  if (t == 8 || t == 9) { // the comb
    const ring = xa + 256 + (<usize>((ch * 2 + fi) * RING) << 3), M = RING - 1;
    const p = <i32>load<f64>(xa + 192 + (<usize>fi << 3)), i = <i32>K(kb + 11), fr = K(kb + 12);
    const y = x + K(kb + 9) * ((1 - fr) * load<f64>(ring + (<usize>((p - i) & M) << 3)) +
      fr * load<f64>(ring + (<usize>((p - i - 1) & M) << 3)));
    store<f64>(ring + (<usize>p << 3), y);
    return K(kb + 10) * y;
  }
  const kk = K(kb + 1), a1 = K(kb + 2), a2 = K(kb + 3), a3 = K(kb + 4);
  const lo = svfS(st, 8, x, a1, a2, a3), bp = bpv;
  if (t == 1) return lo;
  if (t == 2) return x - kk * bp - lo;
  if (t == 3) return kk * bp;
  if (t == 4) return x - kk * bp;
  if (t == 12) return x + K(kb + 9) * (kk * bp); // the bell
  const b1 = K(kb + 6), b2 = K(kb + 7), b3 = K(kb + 8);
  if (t == 6) return svfS(st + 16, 8, lo, b1, b2, b3);
  if (t == 7) {
    const h = x - kk * bp - lo, l2 = svfS(st + 16, 8, h, b1, b2, b3);
    return h - K(kb + 5) * bpv - l2;
  }
  if (t == 10) { // the phaser: three all-passes, averaged with the input
    let y = x - 2 * (kk * bp);
    svfS(st + 16, 8, y, a1, a2, a3); y = y - 2 * (kk * bpv);
    svfS(st + 32, 8, y, a1, a2, a3); y = y - 2 * (kk * bpv);
    return 0.5 * (x + y);
  }
  let z: f64;
  if (t == 11) { const tb = K(K_TB); z = d > 0 ? (ftanh(d * lo + DIODE_B) - tb) / (d * (1 - tb * tb)) : lo; }
  else z = d > 0 ? ftanh(lo * d) / td : lo; // the ladder's soft_clip
  return svfS(st + 16, 8, z, b1, b2, b3);
}

@inline function xchain(xa: usize, ch: i32, x: f64, route: i32, d: f64, td: f64): f64 {
  const y = xf(xa, ch, 0, x, K_X + 1, d, td);
  if (route == 0) return y;
  return route == 1 ? xf(xa, ch, 1, y, K_X + 14, d, td) : 0.5 * (y + xf(xa, ch, 1, x, K_X + 14, d, td));
}

// core.formant on one channel (its state pairs at fs: post()'s layout),
// blended by fa
function tract(fs: usize, x: f64, fa: f64): f64 {
  const fk = konst + (<usize>K_FMT << 3);
  let wet: f64 = 0;
  for (let b = 0; b < 3; b++) {
    const o = <usize>(40 * b);
    svfS(fs + <usize>(32 * b), 16, x, load<f64>(fk + o + 8), load<f64>(fk + o + 16), load<f64>(fk + o + 24));
    wet += bpv * load<f64>(fk + o + 32);
  }
  return x * (1 - fa) + wet * K(K_MAKEUP) * fa;
}

// post() for the general chain: dc block, filter 1 and filter 2 (K_X), the
// tract, amp and post drive (flags 64: filter 1 drives itself — a ladder
// or the diode — so no post drive), added into the output from `off`.
// mode: MODE_STEREO (L and R through the chain) or MODE_MONO (L, its R
// only dc-blocked). flags: 1 per-sample drive, 2 dc state set, 8 post
// drive latched, 32 vowel tract. Returns the peak; lastPd() the latch.
export function xpost(la: usize, xa: usize, set: i32, mode: i32, off: i32, c: i32, N: i32, flags: i32,
  d0: f64, td0: f64, faa: f64, fad: f64, r: f64): f64 {
  const perD = (flags & 1) != 0, fmt = (flags & 32) != 0, driven = (flags & 64) != 0, stereo = mode == MODE_STEREO;
  let dcOn = (flags & 2) != 0, pd = (flags & 8) != 0;
  const sb = setb(set), inA = sb + (<usize>A << 9), inB = sb + (<usize>B << 9), g0 = sb + (<usize>G << 9);
  const route = <i32>K(K_X), M = RING - 1;
  let xL = load<f64>(la + DC), xR = load<f64>(la + DC + 8), yL = load<f64>(la + DC + 16), yR = load<f64>(la + DC + 24);
  const fL = la + FS, fR = stereo ? la + FS + 8 : dummy + FS + 8;
  let pk: f64 = 0;
  for (let j = 0; j < c; j++) {
    const j8 = <usize>j << 3;
    const a = load<f64>(inA + j8), b = load<f64>(inB + j8);
    if (!dcOn) { xL = a; xR = b; dcOn = true; }
    const ya = a - xL + r * yL, yb = b - xR + r * yR;
    xL = a; yL = ya; xR = b; yR = yb;
    let d = d0, td = td0;
    if (perD) { d = Sb(sb, DR, j); td = d > 0 ? ftanh(d) : 0; }
    let oa = xchain(xa, 0, ya, route, d, td);
    let ob = stereo ? xchain(xa, 1, yb, route, d, td) : oa;
    store<f64>(xa + 192, <f64>((<i32>load<f64>(xa + 192) + 1) & M));
    store<f64>(xa + 200, <f64>((<i32>load<f64>(xa + 200) + 1) & M));
    if (fmt) {
      const fa = Math.min(1, Math.max(0, faa + fad * <f64>(off + j) / <f64>N));
      oa = tract(fL, oa, fa);
      ob = stereo ? tract(fR, ob, fa) : oa;
    }
    const g = load<f64>(g0 + j8);
    oa *= g; ob *= g;
    if (!driven && (perD ? (pd = pd || d > 1) : d > 1)) { oa = ftanh(oa * d) / td; ob = ftanh(ob * d) / td; }
    const pl = outL + (<usize>(off + j) << 2), pr = outR + (<usize>(off + j) << 2);
    store<f32>(pl, <f32>(<f64>load<f32>(pl) + oa));
    store<f32>(pr, <f32>(<f64>load<f32>(pr) + ob));
    pk = Math.max(pk, Math.max(Math.abs(oa), Math.abs(ob)));
  }
  store<f64>(la + DC, xL); store<f64>(la + DC + 8, xR); store<f64>(la + DC + 16, yL); store<f64>(la + DC + 24, yR);
  pdOut = pd ? 1 : 0;
  return pk;
}
