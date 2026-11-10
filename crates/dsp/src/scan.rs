//! One pass over a finished buffer: whether every sample is finite, its
//! peak and its sum of squares (`core.scan`). The render's NaN check, a
//! stem's meter levels and a section's levels for the song's gain each used
//! to be its own NumPy reduction over the same samples — and NumPy's max
//! over a stereo frame (`axis=1`, two values) is a slow reduction of its
//! own. Plus `song_play.levels`' 99.5th percentile of the frames' peaks,
//! exactly as `np.percentile` takes it.

/// What one pass over a buffer found. `peak` and `sumsq` mean nothing when
/// `finite` is false.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Scan {
    pub finite: bool,
    pub peak: f64,
    pub sumsq: f64,
}

const LANES: usize = 8;

/// Finite-ness, the largest |x| and the sum of x² over `x`. Eight lanes so
/// the loop vectorizes: a non-finite sample turns its lane's `v * 0` sum
/// into NaN (inf * 0 is NaN too), which no finite buffer can produce. The
/// peak is exact; the sum of squares is to rounding (NumPy sums pairwise).
pub fn scan(x: &[f64]) -> Scan {
    let mut peak = [0.0f64; LANES];
    let mut sq = [0.0f64; LANES];
    let mut nan = [0.0f64; LANES];
    let (chunks, rest) = x.as_chunks::<LANES>();
    for c in chunks {
        for k in 0..LANES {
            let v = c[k];
            let a = v.abs();
            peak[k] = if a > peak[k] { a } else { peak[k] };
            sq[k] += v * v;
            nan[k] += v * 0.0;
        }
    }
    for (k, &v) in rest.iter().enumerate() {
        let a = v.abs();
        peak[k] = if a > peak[k] { a } else { peak[k] };
        sq[k] += v * v;
        nan[k] += v * 0.0;
    }
    let finite = nan.iter().all(|z| *z == 0.0);
    let peak = peak.iter().fold(0.0f64, |m, &a| if a > m { a } else { m });
    let sumsq = ((sq[0] + sq[1]) + (sq[2] + sq[3])) + ((sq[4] + sq[5]) + (sq[6] + sq[7]));
    Scan {
        finite,
        peak,
        sumsq,
    }
}

/// Each frame's peak over its `ch` interleaved channels, `|x|` for mono
/// (`np.max(np.abs(x), axis=1)` of finite samples).
pub fn frame_peaks(x: &[f64], ch: usize, out: &mut [f64]) {
    assert!(ch >= 1 && x.len() == out.len() * ch);
    if ch == 2 {
        let frames = x.as_chunks::<2>().0;
        for (f, y) in frames.iter().zip(out.iter_mut()) {
            let (l, r) = (f[0].abs(), f[1].abs());
            *y = if r > l { r } else { l };
        }
        return;
    }
    for (f, y) in x.chunks_exact(ch).zip(out.iter_mut()) {
        *y = f
            .iter()
            .fold(0.0f64, |m, &v| if v.abs() > m { v.abs() } else { m });
    }
}

/// The `q`th percentile of `v` (finite values; reordered in place) exactly
/// as `np.percentile(v, q)` takes it with its default "linear" method: the
/// virtual index `(n - 1) * (q / 100)`, the two order statistics either
/// side of it, and NumPy's `_lerp` between them (from the far end when the
/// fraction is at least a half). Two selections, not a sort.
pub fn percentile(v: &mut [f64], q: f64) -> f64 {
    let n = v.len();
    assert!(n >= 1);
    let virt = (n - 1) as f64 * (q / 100.0);
    if virt >= (n - 1) as f64 {
        return *v.iter().max_by(|a, b| a.total_cmp(b)).unwrap();
    }
    if virt < 0.0 {
        return *v.iter().min_by(|a, b| a.total_cmp(b)).unwrap();
    }
    let lo = virt.floor() as usize;
    let (_, &mut a, above) = v.select_nth_unstable_by(lo, |x, y| x.total_cmp(y));
    let b = *above.iter().min_by(|x, y| x.total_cmp(y)).unwrap();
    let t = virt - lo as f64;
    let diff = b - a;
    if t >= 0.5 {
        b - diff * (1.0 - t)
    } else {
        a + diff * t
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_peak_and_energy_and_non_finite() {
        let x: Vec<f64> = (0..37).map(|i| (i as f64 - 18.0) * 0.1).collect();
        let s = scan(&x);
        assert!(s.finite);
        assert_eq!(s.peak, 1.8);
        let want: f64 = x.iter().map(|v| v * v).sum();
        assert!((s.sumsq - want).abs() < 1e-12);
        for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let mut y = x.clone();
            y[29] = bad;
            assert!(!scan(&y).finite);
        }
        assert_eq!(
            scan(&[]),
            Scan {
                finite: true,
                peak: 0.0,
                sumsq: 0.0
            }
        );
    }

    #[test]
    fn frame_peaks_take_the_louder_channel() {
        let x = [0.1, -0.5, 0.3, 0.2, -0.0, 0.0];
        let mut out = [0.0; 3];
        frame_peaks(&x, 2, &mut out);
        assert_eq!(out, [0.5, 0.3, 0.0]);
    }

    #[test]
    fn percentile_ends_and_middle() {
        let mut v = vec![3.0, 1.0, 2.0, 4.0];
        assert_eq!(percentile(&mut v, 100.0), 4.0);
        assert_eq!(percentile(&mut v, 0.0), 1.0);
        assert_eq!(percentile(&mut v, 50.0), 2.5);
        assert_eq!(percentile(&mut [7.0], 99.5), 7.0);
    }
}
