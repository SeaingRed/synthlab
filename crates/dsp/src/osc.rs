//! The band-limited oscillators of `core.py` — `_phase`, `_polyblep` and
//! the shapes built on them — as one pass per note instead of NumPy's
//! five-plus (cumsum, floor, two masks, the shape) with a temporary array
//! each.
//!
//! The phase is accumulated exactly as `_phase` does it (a running sum of
//! the frequency, then `/ sr + phase0`, then the fractional part), so saw,
//! square and triangle come out bit-identical to NumPy; sine differs only
//! by the last-ulp error of the two `sin` implementations.

use crate::math::{exp, sin_turns};

/// A per-sample parameter that may be one value for the whole note.
#[derive(Clone, Copy)]
pub enum Param<'a> {
    Const(f64),
    Series(&'a [f64]),
}

impl Param<'_> {
    pub fn from_slice(s: &[f64]) -> Param<'_> {
        if s.len() == 1 {
            Param::Const(s[0])
        } else {
            Param::Series(s)
        }
    }

    #[inline(always)]
    fn at(&self, i: usize) -> f64 {
        match self {
            Param::Const(v) => *v,
            Param::Series(s) => s[i],
        }
    }

    fn fits(&self, n: usize) -> bool {
        match self {
            Param::Const(_) => true,
            Param::Series(s) => s.len() == n,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Shape {
    Sine,
    Saw,
    Square,
    Triangle,
}

impl Shape {
    pub fn parse(s: &str) -> Option<Shape> {
        match s {
            "sine" => Some(Shape::Sine),
            "saw" => Some(Shape::Saw),
            "square" => Some(Shape::Square),
            "triangle" => Some(Shape::Triangle),
            _ => None,
        }
    }
}

/// `c - floor(c)`: `_phase`'s fractional part (exact for finite c).
#[inline(always)]
fn frac(c: f64) -> f64 {
    c - c.floor()
}

/// `_polyblep` for one sample. Where both of its masks hold (dt > 0.5) the
/// NumPy version's second assignment wins, and so does it here.
#[inline(always)]
fn polyblep(t: f64, dt: f64) -> f64 {
    if t > 1.0 - dt {
        let x = (t - 1.0) / dt.max(1e-12);
        x * x + x + x + 1.0
    } else if t < dt {
        let x = t / dt.max(1e-12);
        x + x - x * x - 1.0
    } else {
        0.0
    }
}

/// `core.sine/saw/square/triangle(freq, n, sr, phase0[, width])` into `out`
/// (length n). `width` only matters for the square.
pub fn render(shape: Shape, freq: Param, width: Param, sr: f64, phase0: f64, out: &mut [f64]) {
    let n = out.len();
    assert!(freq.fits(n) && width.fits(n), "a series must be n long");
    if shape == Shape::Sine {
        // two passes: the running phase sum is inherently serial, but kept
        // apart from it the sine pass vectorizes
        phases(freq, sr, phase0, out);
        out.iter_mut().for_each(|y| *y = sin_turns(*y));
        return;
    }
    let mut acc = 0.0f64;
    for (i, y) in out.iter_mut().enumerate() {
        let f = freq.at(i);
        acc += f;
        let t = frac(acc / sr + phase0);
        *y = match shape {
            Shape::Sine => unreachable!(),
            Shape::Saw => (2.0 * t - 1.0) - polyblep(t, f / sr),
            Shape::Triangle => 4.0 * (t - 0.5).abs() - 1.0,
            Shape::Square => {
                let w = width.at(i);
                let dt = f / sr;
                let base = if t < w { 1.0 } else { -1.0 };
                // `(t + (1 - width)) % 1.0`, Python's float mod. For s >= 0
                // (any width <= 1) both it and s - floor(s) are exact, so
                // they agree; a width > 1 can go negative, where only the
                // (slow) Euclidean remainder rounds as Python does.
                let s = t + (1.0 - w);
                let t2 = if s >= 0.0 { frac(s) } else { s.rem_euclid(1.0) };
                base + polyblep(t, dt) - polyblep(t2, dt)
            }
        };
    }
}

/// `_phase`: the running sum of `freq`, `/ sr + phase0`, fractional part.
pub fn phases(freq: Param, sr: f64, phase0: f64, out: &mut [f64]) {
    let mut acc = 0.0f64;
    for (i, y) in out.iter_mut().enumerate() {
        acc += freq.at(i);
        *y = frac(acc / sr + phase0);
    }
}

/// `core.wavetable`'s frames: a table's whole mip stack and where to read it.
#[derive(Clone, Copy)]
pub struct Wavetable<'a> {
    /// `(mips, frames, len)` samples, C-ordered: each mip level holds the
    /// table's frames rendered up to that level's harmonic cap
    pub table: &'a [f64],
    /// the harmonic cap of each mip level, brightest first
    pub caps: &'a [f64],
    pub frames: usize,
    /// 0..1 across the frames
    pub pos: Param<'a>,
}

/// Samples per mip decision (`core._WT_MIP_BLOCK`).
pub const WT_MIP_BLOCK: usize = 64;

impl Wavetable<'_> {
    /// Samples per single-cycle frame.
    pub fn len(&self) -> usize {
        self.table.len() / (self.caps.len() * self.frames).max(1)
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Whether the pieces fit together, for a render of `n` samples.
    pub fn fits(&self, n: usize) -> bool {
        !self.caps.is_empty()
            && self.frames >= 1
            && !self.is_empty()
            && self.table.len() == self.caps.len() * self.frames * self.len()
            && self.pos.fits(n)
    }
}

/// `core.wavetable(freq, n, sr, table, pos, phase0)` into `out`: a bilinear
/// read (phase within a frame, `pos` between frames) of the mip level
/// picked per `WT_MIP_BLOCK` samples from the block's highest pitch — the
/// brightest whose harmonics stay under Nyquist. Every step is NumPy's
/// arithmetic in NumPy's order, so the result is bit-identical.
pub fn wavetable(freq: Param, wt: &Wavetable, sr: f64, phase0: f64, out: &mut [f64]) {
    let n = out.len();
    assert!(freq.fits(n) && wt.fits(n));
    let (len, nf) = (wt.len(), wt.frames);
    let top = (nf - 1) as f64;
    phases(freq, sr, phase0, out);
    for (b, block) in out.chunks_mut(WT_MIP_BLOCK).enumerate() {
        let lo = b * WT_MIP_BLOCK;
        let peak = match freq {
            Param::Const(f) => f.abs(),
            Param::Series(s) => s[lo..lo + block.len()]
                .iter()
                .fold(0.0f64, |m, v| m.max(v.abs())),
        };
        // `_wt_mip_index`: the first cap that fits; the caps descend, so
        // count the ones that don't
        let allowed = (sr * 0.5) / peak.max(1e-6);
        let mip = wt.caps.iter().filter(|&&c| c > allowed).count();
        let mip = mip.min(wt.caps.len() - 1);
        let level = &wt.table[mip * nf * len..(mip + 1) * nf * len];
        for (j, y) in block.iter_mut().enumerate() {
            let ph = *y * len as f64;
            let whole = ph.floor();
            // ph is in [0, len); the wraps are for a phase that isn't finite
            let mut i0 = whole as usize;
            if i0 >= len {
                i0 %= len;
            }
            let i1 = if i0 + 1 == len { 0 } else { i0 + 1 };
            let fr = ph - whole;
            let p = wt.pos.at(lo + j).clamp(0.0, 1.0) * top;
            let below = p.floor();
            let f0 = (below as usize).min(nf - 1);
            let f1 = (f0 + 1).min(nf - 1);
            let fm = p - below;
            let (r0, r1) = (&level[f0 * len..], &level[f1 * len..]);
            let a = r0[i0] * (1.0 - fr) + r0[i1] * fr;
            let b = r1[i0] * (1.0 - fr) + r1[i1] * fr;
            *y = a * (1.0 - fm) + b * fm;
        }
    }
}

/// `core.supersaw`: saws at `freq * ratios[v]` from `phases[v]`, summed
/// voice by voice (NumPy's `out += saw(...)` order) and scaled by
/// 1/sqrt(voices). The caller supplies the detune ratios and the seeded
/// start phases, so both stay exactly what NumPy draws.
pub fn supersaw(freq: Param, ratios: &[f64], phases: &[f64], sr: f64, out: &mut [f64]) {
    let n = out.len();
    assert!(freq.fits(n), "a series must be n long");
    assert_eq!(ratios.len(), phases.len());
    out.fill(0.0);
    for (&ratio, &phase0) in ratios.iter().zip(phases) {
        let mut acc = 0.0f64;
        for (i, y) in out.iter_mut().enumerate() {
            let f = freq.at(i) * ratio;
            acc += f;
            let t = frac(acc / sr + phase0);
            *y += (2.0 * t - 1.0) - polyblep(t, f / sr);
        }
    }
    let norm = (ratios.len() as f64).sqrt();
    for y in out.iter_mut() {
        *y /= norm;
    }
}

/// `core.fm`: `sin(2π c + index * sin(2π m))`, carrier phase c at `freq`
/// (from `phase0`), modulator m at `freq * ratio`; the index fades as
/// exp(-i / (index_decay * sr)) when `index_decay` > 0.
pub fn fm(
    freq: Param,
    ratio: Param,
    index: Param,
    index_decay: f64,
    sr: f64,
    phase0: f64,
    out: &mut [f64],
) {
    let n = out.len();
    assert!(freq.fits(n) && ratio.fits(n) && index.fits(n));
    // pass 1, serial: both phase sums (carrier into `out`, modulator aside)
    let mut m = vec![0.0f64; n];
    let (mut acc_c, mut acc_m) = (0.0f64, 0.0f64);
    for (i, (c, mm)) in out.iter_mut().zip(m.iter_mut()).enumerate() {
        let f = freq.at(i);
        acc_c += f;
        acc_m += f * ratio.at(i);
        *c = frac(acc_c / sr + phase0);
        *mm = frac(acc_m / sr);
    }
    // pass 2, vectorizable: the index envelope and the two sines. In turns:
    // 2π c + idx sin(2π m) radians is c + idx sin(2π m) / 2π. The index
    // becomes a plain slice (no per-sample Const/Series branch) and the
    // sample number goes through i32: AVX2 converts packed i32 to f64, but
    // not u64 — either one kept this loop scalar.
    let idx: Vec<f64> = match index {
        Param::Const(v) => vec![v; n],
        Param::Series(s) => s.to_vec(),
    };
    assert!(n <= i32::MAX as usize);
    let tau = std::f64::consts::TAU;
    if index_decay > 0.0 {
        let d = index_decay * sr;
        for (i, ((y, &mm), &k)) in out.iter_mut().zip(&m).zip(&idx).enumerate() {
            let k = k * exp(-((i as i32) as f64) / d);
            *y = sin_turns(*y + k * sin_turns(mm) / tau);
        }
    } else {
        for ((y, &mm), &k) in out.iter_mut().zip(&m).zip(&idx) {
            *y = sin_turns(*y + k * sin_turns(mm) / tau);
        }
    }
}

/// A sum of sines of time from the buffer's start, `Σ amp_k sin(2π hz_k
/// i / sr + ph_k)` (radians), added in the order given — `effects.wander`
/// (the booth's slow wobble: three sines) and the tape head's flutter.
/// Taken in turns (`math::sin_turns`): the twin's `np.sin` to rounding.
pub fn sines(hz: &[f64], ph: &[f64], amp: &[f64], sr: f64, out: &mut [f64]) {
    assert!(hz.len() == ph.len() && hz.len() == amp.len());
    assert!(out.len() <= i32::MAX as usize);
    out.fill(0.0);
    let tau = std::f64::consts::TAU;
    for ((&f, &p), &a) in hz.iter().zip(ph).zip(amp) {
        let p0 = p / tau;
        for (i, y) in out.iter_mut().enumerate() {
            let t = ((i as i32) as f64) / sr;
            *y += a * sin_turns(f * t + p0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sines_are_their_sum() {
        let mut out = vec![0.0; 100];
        sines(&[3.0, 7.5], &[0.4, 1.3], &[1.0, 0.25], 1000.0, &mut out);
        for (i, y) in out.iter().enumerate() {
            let t = i as f64 / 1000.0;
            let want = (std::f64::consts::TAU * 3.0 * t + 0.4).sin()
                + 0.25 * (std::f64::consts::TAU * 7.5 * t + 1.3).sin();
            assert!((y - want).abs() < 1e-13, "{i}: {y} vs {want}");
        }
    }

    #[test]
    fn saw_is_bounded_and_periodic() {
        let mut y = vec![0.0; 4410];
        render(
            Shape::Saw,
            Param::Const(100.0),
            Param::Const(0.5),
            44_100.0,
            0.0,
            &mut y,
        );
        assert!(y.iter().all(|v| v.abs() <= 1.0 + 1e-9));
        // 100 Hz at 44.1 kHz: period 441 samples
        assert!((y[100] - y[100 + 441]).abs() < 1e-9);
    }

    #[test]
    fn wavetable_reads_the_frame_and_drops_a_mip_when_the_pitch_needs_it() {
        // two mips (caps 8 and 1) of two frames, four samples a cycle
        let table = [
            0.0, 1.0, 0.0, -1.0, /* mip 0, frame 0 */
            0.0, 2.0, 0.0, -2.0, /* mip 0, frame 1 */
            0.0, 0.5, 0.0, -0.5, /* mip 1, frame 0 */
            0.0, 0.5, 0.0, -0.5, /* mip 1, frame 1 */
        ];
        let wt = |pos| Wavetable {
            table: &table,
            caps: &[8.0, 1.0],
            frames: 2,
            pos: Param::Const(pos),
        };
        // a quarter cycle a sample: sr/2 / f = 2 harmonics fit, so cap 8
        // doesn't and cap 1 does (mip 1); the phases 1/4, 1/2, 3/4, 0 land
        // on the samples themselves
        let mut y = [0.0; 4];
        wavetable(Param::Const(16.0), &wt(0.0), 64.0, 0.0, &mut y);
        assert_eq!(y, [0.5, 0.0, -0.5, 0.0]);
        // a sixteenth of a cycle a sample: 8 harmonics fit (mip 0), and
        // the read walks up the first quarter of the frame
        let mut y = [0.0; 4];
        wavetable(Param::Const(4.0), &wt(0.0), 64.0, 0.0, &mut y);
        assert_eq!(y, [0.25, 0.5, 0.75, 1.0]);
        let mut y = [0.0; 4];
        wavetable(Param::Const(4.0), &wt(1.0), 64.0, 0.0, &mut y);
        assert_eq!(y, [0.5, 1.0, 1.5, 2.0]);
        // halfway between the frames
        let mut y = [0.0; 4];
        wavetable(Param::Const(4.0), &wt(0.5), 64.0, 0.0, &mut y);
        assert_eq!(y, [0.375, 0.75, 1.125, 1.5]);
    }

    #[test]
    fn square_duty_cycle_follows_width() {
        let mut y = vec![0.0; 44_100];
        render(
            Shape::Square,
            Param::Const(50.0),
            Param::Const(0.25),
            44_100.0,
            0.0,
            &mut y,
        );
        let high = y.iter().filter(|&&v| v > 0.0).count() as f64 / y.len() as f64;
        assert!((high - 0.25).abs() < 0.01, "{high}");
    }
}
