// synthlab/studio/wasm/assembly/vmath.ts
// sin and tanh on both f64x2 lanes at once, branch-free, for voice.ts.
// The scalar library calls (NativeMath.sin / tanh -> expm1) were a third of
// a voice's time and ran one lane at a time. These agree with them (and
// with V8's Math.sin / Math.tanh, which the JS voice calls) to a few ulp —
// an absolute error under 1e-15 on audio in +-1, far below the f32 the
// voice outputs (tests/studio/test_voice_wasm.py measures it).

// Cody-Waite pi/2 in three parts (fdlibm's pio2_1/2/3: the first two carry
// 33 bits, so k * part is exact for |k| < 2^20)
const PIO2_1 = 1.57079632673412561417e+00;
const PIO2_2 = 6.07710050630396597660e-11;
const PIO2_3 = 2.02226624871116645580e-21;
const INV_PIO2 = 6.36619772367581382433e-01;
// fdlibm's __kernel_sin / __kernel_cos minimax coefficients on [-pi/4, pi/4]
const S1 = -1.66666666666666324348e-01, S2 = 8.33333333332248946124e-03,
  S3 = -1.98412698298579493134e-04, S4 = 2.75573137070700676789e-06,
  S5 = -2.50507602534068634195e-08, S6 = 1.58969099521155010221e-10;
const C1 = 4.16666666666666019037e-02, C2 = -1.38888888888741095749e-03,
  C3 = 2.48015872894767294178e-05, C4 = -2.75573143513906633035e-07,
  C5 = 2.08757232129817482790e-09, C6 = -1.13596475577881948265e-11;

@inline function sp(x: f64): v128 { return f64x2.splat(x); }
@inline function madd(a: v128, b: v128, c: v128): v128 { return f64x2.add(f64x2.mul(a, b), c); }

// sin on both lanes (|x| < 2^20 * pi / 2; the voice's arguments are phases
// times 2 pi plus an fm index of a few radians)
@inline export function vsin(x: v128): v128 {
  const k = f64x2.nearest(f64x2.mul(x, sp(INV_PIO2)));
  const r = f64x2.sub(f64x2.sub(f64x2.sub(x, f64x2.mul(k, sp(PIO2_1))), f64x2.mul(k, sp(PIO2_2))),
    f64x2.mul(k, sp(PIO2_3)));
  const z = f64x2.mul(r, r), w = f64x2.mul(z, z);
  // __kernel_sin(r, 0): r + r^3 (S1 + z (S2 .. S6))
  const ps = madd(z, madd(z, madd(z, madd(z, madd(z, sp(S6), sp(S5)), sp(S4)), sp(S3)), sp(S2)), sp(S1));
  const s = f64x2.add(r, f64x2.mul(f64x2.mul(z, r), ps));
  // __kernel_cos(r, 0): 1 - z/2 with its rounding carried, + z^2 (C1 .. C6)
  const pc = f64x2.add(f64x2.mul(z, madd(z, madd(z, sp(C3), sp(C2)), sp(C1))),
    f64x2.mul(f64x2.mul(w, w), madd(z, madd(z, sp(C6), sp(C5)), sp(C4))));
  const hz = f64x2.mul(sp(0.5), z), one = sp(1), cw = f64x2.sub(one, hz);
  const c = f64x2.add(cw, f64x2.add(f64x2.sub(f64x2.sub(one, cw), hz), f64x2.mul(z, pc)));
  // quadrant k mod 4: 0 sin, 1 cos, 2 -sin, 3 -cos
  const q = f64x2.sub(k, f64x2.mul(sp(4), f64x2.floor(f64x2.mul(k, sp(0.25)))));
  const odd = f64x2.eq(f64x2.sub(q, f64x2.mul(sp(2), f64x2.floor(f64x2.mul(q, sp(0.5))))), one);
  const y = v128.bitselect(c, s, odd);
  return v128.bitselect(f64x2.neg(y), y, f64x2.ge(q, sp(2)));
}

const LN2_HI = 6.93147180369123816490e-01, LN2_LO = 1.90821492927058770002e-10;
const INV_LN2 = 1.44269504088896338700e+00, LN2 = 6.93147180559945286227e-01;

// expm1 on both lanes for 0 <= y <= 44: y = k ln2 + r, |r| <= ln2 / 2,
// expm1(r) by its Taylor series to r^14 (under 1e-18 relative there), then
// 2^k (1 + expm1(r)) - 1 (k = 0: expm1(r) itself, full precision near 0)
@inline function vexpm1(y: v128): v128 {
  const k = f64x2.nearest(f64x2.mul(y, sp(INV_LN2)));
  const r = f64x2.sub(f64x2.sub(y, f64x2.mul(k, sp(LN2_HI))), f64x2.mul(k, sp(LN2_LO)));
  const e = kexpm1(r);
  const big = f64x2.sub(f64x2.mul(pow2(k), f64x2.add(e, sp(1))), sp(1));
  return v128.bitselect(e, big, f64x2.eq(k, sp(0)));
}

// 2^k for integral k in [-1022, 1023]: k + 1023 lands in the low mantissa
// bits of k + 2^52 + 1023, shifted into the exponent field
@inline function pow2(k: v128): v128 {
  return i64x2.shl(f64x2.add(k, sp(4503599627371519.0)), 52);
}

// e^y on both lanes (y clamped to [-708, 709]: the normal range)
@inline export function vexp(y: v128): v128 {
  const x = f64x2.min(f64x2.max(y, sp(-708)), sp(709));
  const k = f64x2.nearest(f64x2.mul(x, sp(INV_LN2)));
  const r = f64x2.sub(f64x2.sub(x, f64x2.mul(k, sp(LN2_HI))), f64x2.mul(k, sp(LN2_LO)));
  return f64x2.mul(pow2(k), f64x2.add(kexpm1(r), sp(1)));
}

// 2^y on both lanes (y clamped to [-1022, 1023]): 2^k e^((y - k) ln 2)
@inline export function vexp2(y: v128): v128 {
  const x = f64x2.min(f64x2.max(y, sp(-1022)), sp(1023));
  const k = f64x2.nearest(x);
  const r = f64x2.mul(f64x2.sub(x, k), sp(LN2));
  return f64x2.mul(pow2(k), f64x2.add(kexpm1(r), sp(1)));
}

// expm1(r) for |r| <= ln2 / 2: its Taylor series to r^14
@inline function kexpm1(r: v128): v128 {
  let p = sp(1.0 / 87178291200.0); // 1/14!
  p = madd(p, r, sp(1.0 / 6227020800.0));
  p = madd(p, r, sp(1.0 / 479001600.0));
  p = madd(p, r, sp(1.0 / 39916800.0));
  p = madd(p, r, sp(1.0 / 3628800.0));
  p = madd(p, r, sp(1.0 / 362880.0));
  p = madd(p, r, sp(1.0 / 40320.0));
  p = madd(p, r, sp(1.0 / 5040.0));
  p = madd(p, r, sp(1.0 / 720.0));
  p = madd(p, r, sp(1.0 / 120.0));
  p = madd(p, r, sp(1.0 / 24.0));
  p = madd(p, r, sp(1.0 / 6.0));
  p = madd(p, r, sp(0.5));
  return f64x2.add(r, f64x2.mul(f64x2.mul(r, r), p)); // r + r^2 (1/2 + r/6 + ..)
}

// tanh on both lanes: t = expm1(2|x|), t / (t + 2), sign restored; |x|
// clamped at 22, where tanh is 1 to f64 precision
@inline export function vtanh(x: v128): v128 {
  const a = f64x2.min(f64x2.abs(x), sp(22));
  const t = vexpm1(f64x2.add(a, a));
  const y = f64x2.div(t, f64x2.add(t, sp(2)));
  return v128.or(y, v128.and(x, sp(-0.0))); // |tanh| with x's sign bit
}
