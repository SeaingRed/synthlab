//! The swept filter: `core._swept_biquad`'s TPT (Zavalishin/Simper)
//! state-variable filter, coefficients held per block, state carried across
//! blocks. NumPy can't run a per-sample recursion fast, so the Python
//! version builds each block's response in closed form (FFT convolution
//! plus a prefix scan over 2x2 state maps). Here it is the recursion
//! itself: about ten flops a sample.

use crate::osc::Param;
use crate::shape;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Lowpass,
    Highpass,
    Bandpass,
    Notch,
}

impl Kind {
    pub fn parse(s: &str) -> Option<Kind> {
        match s {
            "lowpass" => Some(Kind::Lowpass),
            "highpass" => Some(Kind::Highpass),
            "bandpass" => Some(Kind::Bandpass),
            "notch" => Some(Kind::Notch),
            _ => None,
        }
    }
}

/// Filter `x` (`n` frames of `ch` interleaved channels, i.e. a C-ordered
/// `(n, ch)` array) into `out`. `fc` holds one cutoff per `block` frames
/// (`ceil(n / block)` of them); `q` holds one resonance, or one per block.
///
/// Outputs, as `core._svf_blocks` defines them: low = v2, band = v1,
/// high = x - k v1 - v2; bandpass is RBJ's 0 dB-peak `k * band` and notch is
/// low + high.
#[allow(clippy::too_many_arguments)]
pub fn swept(
    x: &[f64],
    out: &mut [f64],
    ch: usize,
    kind: Kind,
    fc: &[f64],
    q: &[f64],
    sr: f64,
    block: usize,
) {
    assert!(ch >= 1 && block >= 1);
    assert_eq!(x.len(), out.len());
    assert_eq!(x.len() % ch, 0);
    let n = x.len() / ch;
    assert!(fc.len() * block >= n, "one cutoff per block");
    assert!(
        q.len() == 1 || q.len() == fc.len(),
        "q is a scalar or per block"
    );

    // state per channel: the two integrators (ic1eq, ic2eq)
    let mut s1 = vec![0.0f64; ch];
    let mut s2 = vec![0.0f64; ch];
    for (b, &f) in fc.iter().enumerate() {
        let lo = b * block;
        if lo >= n {
            break;
        }
        let hi = (lo + block).min(n);
        let f = f.clamp(20.0, sr * 0.49);
        let g = (std::f64::consts::PI * f / sr).tan();
        let qb = if q.len() == 1 { q[0] } else { q[b] };
        let k = 1.0 / qb.max(0.05);
        let a1 = 1.0 / (1.0 + g * (g + k));
        let a2 = g * a1;
        let a3 = g * a2;
        let frames_in = &x[lo * ch..hi * ch];
        let frames_out = &mut out[lo * ch..hi * ch];
        for (fin, fout) in frames_in
            .chunks_exact(ch)
            .zip(frames_out.chunks_exact_mut(ch))
        {
            for c in 0..ch {
                let v0 = fin[c];
                let v3 = v0 - s2[c];
                let v1 = a1 * s1[c] + a2 * v3;
                let v2 = s2[c] + a2 * s1[c] + a3 * v3;
                s1[c] = 2.0 * v1 - s1[c];
                s2[c] = 2.0 * v2 - s2[c];
                fout[c] = match kind {
                    Kind::Lowpass => v2,
                    Kind::Highpass => v0 - k * v1 - v2,
                    Kind::Bandpass => k * v1,
                    Kind::Notch => v0 - k * v1,
                };
            }
        }
    }
}

/// One block's coefficients, as `swept` computes them.
#[derive(Clone, Copy)]
struct Coef {
    k: f64,
    a1: f64,
    a2: f64,
    a3: f64,
}

impl Coef {
    #[inline(always)]
    fn of(f: f64, q: f64, sr: f64) -> Coef {
        let f = f.clamp(20.0, sr * 0.49);
        let g = (std::f64::consts::PI * f / sr).tan();
        let k = 1.0 / q.max(0.05);
        let a1 = 1.0 / (1.0 + g * (g + k));
        let a2 = g * a1;
        let a3 = g * a2;
        Coef { k, a1, a2, a3 }
    }

    /// One sample through the filter, as `swept`'s loop body runs it: the
    /// integrators `s1`/`s2` move on, the `kind` output comes back.
    #[inline(always)]
    fn step(&self, kind: Kind, s1: &mut f64, s2: &mut f64, v0: f64) -> f64 {
        let v3 = v0 - *s2;
        let v1 = self.a1 * *s1 + self.a2 * v3;
        let v2 = *s2 + self.a2 * *s1 + self.a3 * v3;
        *s1 = 2.0 * v1 - *s1;
        *s2 = 2.0 * v2 - *s2;
        match kind {
            Kind::Lowpass => v2,
            Kind::Highpass => v0 - self.k * v1 - v2,
            Kind::Bandpass => self.k * v1,
            Kind::Notch => v0 - self.k * v1,
        }
    }
}

/// One swept filter's curves: a cutoff per block, one `q` or one per block.
pub struct Sweep<'a> {
    pub kind: Kind,
    pub fc: &'a [f64],
    pub q: &'a [f64],
}

impl Sweep<'_> {
    #[inline(always)]
    fn coef(&self, b: usize, sr: f64) -> Coef {
        let q = if self.q.len() == 1 {
            self.q[0]
        } else {
            self.q[b]
        };
        Coef::of(self.fc[b], q, sr)
    }
}

/// `core.ladder` on a stereo buffer: `one`, then `soft_clip(drive)`, then
/// `two` — the three passes `swept`, `shape::soft_clip`, `swept` make, with
/// the same arithmetic on every sample, so the result is bit-identical.
///
/// A swept filter can't go faster than its own recursion: each sample waits
/// on the last one's state. But the second filter only waits on the first
/// one's *output*, so the two recursions can run side by side: this walks
/// the first filter one block ahead of the second, in one loop. The
/// saturation stays its own pass over each block, between the two, where
/// it vectorizes.
///
/// `x` is used as scratch (it comes back holding the saturated first
/// stage); the result lands in `out`.
pub fn ladder_stereo(
    x: &mut [f64],
    out: &mut [f64],
    one: &Sweep,
    drive: Param,
    two: &Sweep,
    sr: f64,
    block: usize,
) {
    assert!(block >= 1 && x.len() == out.len() && x.len().is_multiple_of(2));
    let n = x.len() / 2;
    let nb = n.div_ceil(block);
    assert!(
        one.fc.len() >= nb && two.fc.len() >= nb,
        "one cutoff per block"
    );
    if nb == 0 {
        return;
    }
    let span = |b: usize| (b * block, ((b + 1) * block).min(n));
    let clip = |buf: &mut [f64], lo: usize, hi: usize| {
        let d = match drive {
            Param::Const(d) => Param::Const(d),
            Param::Series(ds) => Param::Series(&ds[lo..hi]),
        };
        shape::soft_clip_in_place(&mut buf[2 * lo..2 * hi], 2, d);
    };
    // each filter's integrators, per channel
    let (mut p1, mut p2) = ([0.0f64; 2], [0.0f64; 2]);
    let (mut r1, mut r2) = ([0.0f64; 2], [0.0f64; 2]);
    // the first filter's head start: block 0
    let (lo, hi) = span(0);
    let c = one.coef(0, sr);
    for f in x[2 * lo..2 * hi].as_chunks_mut::<2>().0 {
        f[0] = c.step(one.kind, &mut p1[0], &mut p2[0], f[0]);
        f[1] = c.step(one.kind, &mut p1[1], &mut p2[1], f[1]);
    }
    clip(x, lo, hi);
    for b in 0..nb {
        let (lo, hi) = span(b);
        let c2 = two.coef(b, sr);
        let (behind, ahead) = x.split_at_mut(2 * hi);
        let src = &behind[2 * lo..];
        let dst = &mut out[2 * lo..2 * hi];
        // the first filter on block b + 1 beside the second on block b
        let mut done = 0;
        if b + 1 < nb {
            let (alo, ahi) = span(b + 1);
            let c1 = one.coef(b + 1, sr);
            let lead = &mut ahead[..2 * (ahi - alo)];
            for ((a, s), d) in lead
                .as_chunks_mut::<2>()
                .0
                .iter_mut()
                .zip(src.as_chunks::<2>().0)
                .zip(dst.as_chunks_mut::<2>().0.iter_mut())
            {
                a[0] = c1.step(one.kind, &mut p1[0], &mut p2[0], a[0]);
                a[1] = c1.step(one.kind, &mut p1[1], &mut p2[1], a[1]);
                d[0] = c2.step(two.kind, &mut r1[0], &mut r2[0], s[0]);
                d[1] = c2.step(two.kind, &mut r1[1], &mut r2[1], s[1]);
            }
            done = ahi - alo; // a short last block leaves the second some
        }
        for (s, d) in src[2 * done..]
            .as_chunks::<2>()
            .0
            .iter()
            .zip(dst[2 * done..].as_chunks_mut::<2>().0)
        {
            d[0] = c2.step(two.kind, &mut r1[0], &mut r2[0], s[0]);
            d[1] = c2.step(two.kind, &mut r1[1], &mut r2[1], s[1]);
        }
        if b + 1 < nb {
            let (alo, ahi) = span(b + 1);
            clip(x, alo, ahi);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_ladder_is_its_three_passes_bit_for_bit() {
        for n in [1usize, 63, 64, 65, 128, 129, 1000] {
            let x: Vec<f64> = (0..2 * n)
                .map(|i| ((i * 7919) % 1013) as f64 / 506.5 - 1.0)
                .collect();
            let nb = n.div_ceil(64);
            let fc: Vec<f64> = (0..nb).map(|b| 60.0 + 911.0 * b as f64).collect();
            let q: Vec<f64> = (0..nb).map(|b| 0.5 + 0.4 * b as f64).collect();
            let drive: Vec<f64> = (0..n).map(|i| 0.8 + 0.002 * i as f64).collect();
            for d in [Param::Const(1.3), Param::Series(&drive)] {
                let (mut a, mut b, mut want) =
                    (vec![0.0; 2 * n], vec![0.0; 2 * n], vec![0.0; 2 * n]);
                swept(&x, &mut a, 2, Kind::Lowpass, &fc, &q, 44_100.0, 64);
                shape::soft_clip(&a, &mut b, 2, d);
                swept(&b, &mut want, 2, Kind::Lowpass, &fc, &[0.707], 44_100.0, 64);
                let one = Sweep {
                    kind: Kind::Lowpass,
                    fc: &fc,
                    q: &q,
                };
                let two = Sweep {
                    kind: Kind::Lowpass,
                    fc: &fc,
                    q: &[0.707],
                };
                let (mut scratch, mut got) = (x.clone(), vec![0.0; 2 * n]);
                ladder_stereo(&mut scratch, &mut got, &one, d, &two, 44_100.0, 64);
                assert_eq!(got, want, "n {n}");
            }
        }
    }

    #[test]
    fn dc_passes_lowpass_and_is_blocked_by_highpass() {
        let n = 48_000;
        let x = vec![1.0; n];
        let fc = vec![1000.0; n.div_ceil(64)];
        let mut lp = vec![0.0; n];
        let mut hp = vec![0.0; n];
        swept(&x, &mut lp, 1, Kind::Lowpass, &fc, &[0.707], 48_000.0, 64);
        swept(&x, &mut hp, 1, Kind::Highpass, &fc, &[0.707], 48_000.0, 64);
        assert!((lp[n - 1] - 1.0).abs() < 1e-9);
        assert!(hp[n - 1].abs() < 1e-9);
    }

    #[test]
    fn stereo_channels_are_independent() {
        let n = 1000;
        let mut x = vec![0.0; 2 * n];
        x[0] = 1.0; // impulse on the left only
        let fc = vec![500.0; n.div_ceil(64)];
        let mut y = vec![0.0; 2 * n];
        swept(&x, &mut y, 2, Kind::Lowpass, &fc, &[2.0], 48_000.0, 64);
        assert!(y.iter().skip(1).step_by(2).all(|&v| v == 0.0));
        assert!(y.iter().step_by(2).any(|&v| v != 0.0));
    }
}
