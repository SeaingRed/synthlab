//! Transcendentals built from plain arithmetic.
//!
//! Two reasons not to call the platform's `exp`/`tanh`: speed — those are
//! opaque library calls, so a loop around them can't vectorize, and `tanh`
//! was the whole cost of `soft_clip` — and sameness: every platform's libm
//! rounds its last bit its own way, while `+ - * /`, `floor` and bit
//! casts round identically on x86, ARM and wasm32. So the same input gives
//! the same output on all three targets.
//! Both are within a few ulp of the correctly rounded result
//! (`tests::*_is_accurate`).

// fdlibm's split of ln 2: the high part has 32 trailing zero bits, so
// k * LN2_HI is exact for every k the clamp allows
const LN2_HI: f64 = f64::from_bits(0x3fe6_2e42_fee0_0000);
const LN2_LO: f64 = f64::from_bits(0x3dea_39ef_3579_3c76);
const LOG2_E: f64 = std::f64::consts::LOG2_E;

/// 1/k! for k = 0..=13: exp(r) on |r| <= ln2/2 to ~4e-18.
const EXP_TAYLOR: [f64; 14] = [
    1.0,
    1.0,
    0.5,
    1.0 / 6.0,
    1.0 / 24.0,
    1.0 / 120.0,
    1.0 / 720.0,
    1.0 / 5040.0,
    1.0 / 40320.0,
    1.0 / 362880.0,
    1.0 / 3628800.0,
    1.0 / 39916800.0,
    1.0 / 479001600.0,
    1.0 / 6227020800.0,
];

/// exp(x) for x in [-708, 709] (clamped to it): x = k ln2 + r with
/// Cody-Waite's two-part ln2, then 2^k times the Taylor series of e^r.
#[inline(always)]
pub fn exp(x: f64) -> f64 {
    let x = x.clamp(-708.0, 709.0);
    let k = (x * LOG2_E + 0.5).floor();
    let r = (x - k * LN2_HI) - k * LN2_LO;
    let mut p = EXP_TAYLOR[13];
    for &c in EXP_TAYLOR[..13].iter().rev() {
        p = p * r + c;
    }
    // 2^k by building the exponent field; k + 1023 is in [2, 2046] after the
    // clamp. Not `k as i64`: AVX2 has no packed f64 -> i64 conversion, and
    // that one cast kept the whole loop scalar. Adding 2^52 puts the integer
    // k + 1023 in the low mantissa bits instead, and a shift moves it up.
    let biased = (k + (1023.0 + 4_503_599_627_370_496.0)).to_bits();
    p * f64::from_bits(biased << 52)
}

/// tanh's Taylor coefficients, 2^2n (2^2n - 1) B_2n / (2n)! for n = 1..=19
/// (exact Bernoulli numbers, rounded once): odd series in |y| below
/// `TANH_SERIES_MAX`, where 19 terms leave ~4e-18 relative error.
const TANH_TAYLOR: [f64; 19] = [
    1.0,
    -0.333_333_333_333_333_3,
    0.133_333_333_333_333_33,
    -0.053_968_253_968_253_97,
    0.021_869_488_536_155_203,
    -0.008_863_235_529_902_197,
    0.003_592_128_036_572_481,
    -0.001_455_834_387_051_318_3,
    0.000_590_027_440_945_586,
    -0.000_239_129_114_243_552_48,
    9.691_537_956_929_451e-5,
    -3.927_832_388_331_683e-5,
    1.591_890_506_932_896_4e-5,
    -6.451_689_215_655_431e-6,
    2.614_771_151_290_754_6e-6,
    -1.059_726_832_010_465_4e-6,
    4.294_911_078_273_806e-7,
    -1.740_661_896_357_164_8e-7,
    7.054_636_946_400_968e-8,
];
const TANH_SERIES_MAX: f64 = 0.55;

/// tanh(y). Small |y|: the odd Taylor series (no cancellation near 0).
/// Otherwise 1 - 2/(e^2|y| + 1), where the result is >= 0.5 so the
/// subtraction costs nothing; e^44 already rounds it to exactly 1. Both
/// branches are computed and one is selected, so a loop over it vectorizes.
#[inline(always)]
pub fn tanh(y: f64) -> f64 {
    let a = y.abs();
    let s = a * a;
    let mut p = TANH_TAYLOR[18];
    for &c in TANH_TAYLOR[..18].iter().rev() {
        p = p * s + c;
    }
    let small = a * p;
    let large = 1.0 - 2.0 / (exp(2.0 * a.min(22.0)) + 1.0);
    let r = if a < TANH_SERIES_MAX { small } else { large };
    r.copysign(y)
}

/// (-1)^k / (2k+1)! for k = 0..=11: sin on [-π/2, π/2] to ~5e-21.
const SIN_TAYLOR: [f64; 12] = [
    1.0,
    -0.166_666_666_666_666_66,
    0.008_333_333_333_333_333,
    -0.000_198_412_698_412_698_4,
    2.755_731_922_398_589_3e-6,
    -2.505_210_838_544_172e-8,
    1.605_904_383_682_161_3e-10,
    -7.647_163_731_819_816e-13,
    2.811_457_254_345_520_6e-15,
    -8.220_635_246_624_33e-18,
    1.957_294_106_339_126_3e-20,
    -3.868_170_170_630_684e-23,
];

/// sin(2π t), the phase `t` in turns — what every oscillator and LFO here
/// wants. The reduction is exact: t minus its nearest integer lands in
/// [-1/2, 1/2), and folding the outer quarters with sin(π - x) = sin(x)
/// (0.5 - r is exact for r in [1/4, 1/2]) leaves |2π r| <= π/2 for the odd
/// series. Only the one multiply by 2π rounds before the series.
#[inline(always)]
pub fn sin_turns(t: f64) -> f64 {
    let r = t - (t + 0.5).floor();
    let r = if r > 0.25 {
        0.5 - r
    } else if r < -0.25 {
        -0.5 - r
    } else {
        r
    };
    let x = std::f64::consts::TAU * r;
    let s = x * x;
    let mut p = SIN_TAYLOR[11];
    for &c in SIN_TAYLOR[..11].iter().rev() {
        p = p * s + c;
    }
    x * p
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sin_turns_is_accurate() {
        // the series against libm on the same exactly-reduced argument
        let worst = sweep(-3.0, 3.0, 2_000_000)
            .map(|t| {
                let r = t - (t + 0.5).floor();
                (sin_turns(t) - (std::f64::consts::TAU * r).sin()).abs()
            })
            .fold(0.0, f64::max);
        assert!(worst < 5e-16, "worst {worst:e}");
        // and against the plain sin(2π t), which rounds 2π t first (at
        // |t| ~ 3 that alone is ~2e-15 of phase): what NumPy computes
        let worst = sweep(-3.0, 3.0, 2_000_000)
            .map(|t| (sin_turns(t) - (std::f64::consts::TAU * t).sin()).abs())
            .fold(0.0, f64::max);
        assert!(worst < 4e-15, "worst {worst:e}");
        assert_eq!(sin_turns(0.0), 0.0);
        assert_eq!(sin_turns(0.25), 1.0);
        assert_eq!(sin_turns(0.5), 0.0);
        assert_eq!(sin_turns(0.75), -1.0);
    }

    fn ulps(a: f64, b: f64) -> u64 {
        if a == b {
            return 0;
        }
        (a.to_bits() as i64 - b.to_bits() as i64).unsigned_abs()
    }

    fn sweep(lo: f64, hi: f64, n: usize) -> impl Iterator<Item = f64> {
        (0..=n).map(move |i| lo + (hi - lo) * i as f64 / n as f64)
    }

    #[test]
    fn exp_is_accurate() {
        let worst = sweep(-700.0, 700.0, 2_000_000)
            .map(|x| ulps(exp(x), x.exp()))
            .max()
            .unwrap();
        assert!(worst <= 3, "worst {worst} ulp");
        assert_eq!(exp(0.0), 1.0);
    }

    #[test]
    fn tanh_is_accurate() {
        let worst = sweep(-30.0, 30.0, 2_000_000)
            .chain(sweep(-1e-3, 1e-3, 100_000))
            .map(|x| ulps(tanh(x), x.tanh()))
            .max()
            .unwrap();
        assert!(worst <= 4, "worst {worst} ulp");
        assert_eq!(tanh(0.0), 0.0);
        assert_eq!(tanh(40.0), 1.0);
        assert_eq!(tanh(-40.0), -1.0);
        assert!(tanh(-0.0).is_sign_negative());
    }
}
