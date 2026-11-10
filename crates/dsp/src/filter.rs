//! Fixed filters: `core.dc_block`, and scipy's `lfilter` for the short
//! filters the engine runs everywhere (biquads, shelves, one-poles).

/// `core.dc_block`'s one-pole highpass, b = [1, -1], a = [1, -r], run as
/// scipy's `lfilter` runs it (transposed direct form II: y = x + z, then
/// z = -x + r y) with its state started from the first sample (`lfilter_zi`
/// scaled by x[0], i.e. z = -x[0]) — so a voice that starts off centre
/// doesn't thump. Per channel over `ch` interleaved channels.
pub fn dc_block(x: &[f64], out: &mut [f64], ch: usize, r: f64) {
    assert_eq!(x.len(), out.len());
    assert!(ch >= 1 && x.len().is_multiple_of(ch));
    if x.is_empty() {
        return;
    }
    if ch == 2 {
        // a voice is stereo: both channels in one pass. Each channel's
        // recursion waits on its own last sample, so two of them side by
        // side take the time of one (the arithmetic per channel is the
        // loop's below)
        let (mut zl, mut zr) = (-x[0], -x[1]);
        let (frames_in, frames_out) = (x.as_chunks::<2>().0, out.as_chunks_mut::<2>().0);
        for (fin, fout) in frames_in.iter().zip(frames_out) {
            let (yl, yr) = (fin[0] + zl, fin[1] + zr);
            zl = -fin[0] - yl * -r;
            zr = -fin[1] - yr * -r;
            fout[0] = yl;
            fout[1] = yr;
        }
        return;
    }
    for c in 0..ch {
        let mut z = -x[c];
        for (xi, yi) in x[c..]
            .iter()
            .step_by(ch)
            .zip(out[c..].iter_mut().step_by(ch))
        {
            let y = xi + z;
            // lfilter's `z = 0 + x * b1 - y * a1` with b1 = -1, a1 = -r:
            // the same two roundings as below, in the same order
            z = -xi - y * -r;
            *yi = y;
        }
    }
}

/// scipy.signal.lfilter along axis 0 of `x` (`n` frames x `ch`
/// interleaved channels), for `b`/`a` of up to 3 taps, run exactly as
/// scipy's `_linear_filter` runs it (transposed direct form II):
///
///   y = z[0] + b[0] x;  z[k] = z[k+1] + x b[k+1] - y a[k+1];  z[last] = x b[K] - y a[K]
///
/// with `b`/`a` zero-padded to one length and divided by `a[0]`, so the
/// output is bit-identical. `zi` is the initial state per channel
/// (`(K-1) * ch`, channel-major) or empty for zeros.
pub fn lfilter(b: &[f64], a: &[f64], x: &[f64], out: &mut [f64], ch: usize, zi: &[f64]) {
    assert!(!a.is_empty() && !b.is_empty() && a[0] != 0.0);
    assert!(b.len() <= 3 && a.len() <= 3, "orders up to 2");
    assert!(ch >= 1 && x.len().is_multiple_of(ch) && x.len() == out.len());
    let k = b.len().max(a.len());
    let (mut bb, mut aa) = ([0.0f64; 3], [0.0f64; 3]);
    bb[..b.len()].copy_from_slice(b);
    aa[..a.len()].copy_from_slice(a);
    let a0 = aa[0];
    if a0 != 1.0 {
        for v in bb[..k].iter_mut().chain(aa[..k].iter_mut()) {
            *v /= a0;
        }
    }
    let nz = k - 1;
    assert!(
        zi.is_empty() || zi.len() == nz * ch,
        "zi: (order) per channel"
    );
    for c in 0..ch {
        let mut z = [0.0f64; 2];
        if !zi.is_empty() {
            z[..nz].copy_from_slice(&zi[c * nz..(c + 1) * nz]);
        }
        let xs = x[c..].iter().step_by(ch);
        let ys = out[c..].iter_mut().step_by(ch);
        match nz {
            0 => {
                for (&xn, yn) in xs.zip(ys) {
                    *yn = xn * bb[0];
                }
            }
            1 => {
                for (&xn, yn) in xs.zip(ys) {
                    let y = z[0] + bb[0] * xn;
                    z[0] = xn * bb[1] - y * aa[1];
                    *yn = y;
                }
            }
            _ => {
                for (&xn, yn) in xs.zip(ys) {
                    let y = z[0] + bb[0] * xn;
                    z[0] = z[1] + xn * bb[1] - y * aa[1];
                    z[1] = xn * bb[2] - y * aa[2];
                    *yn = y;
                }
            }
        }
    }
}

/// `effects._allpass_sweep`: the phaser's cascade of `stages` first-order
/// allpasses, b = [g, 1], a = [1, g], whose coefficient `gs[k]` holds for
/// block `k` (`block` samples), each stage's state carried across blocks
/// from zero. The arithmetic is scipy's `lfilter` (`y = z + g x`, then
/// `z = x - y g`), so the output is bit-identical to the twin, which calls
/// it once a block a stage — 3.2 million calls for a 283 s song's two
/// phasers. A block goes through every stage while it is in cache; a stage
/// only reads the one before it, so the order of the two loops is free.
pub fn allpass_sweep(x: &[f64], gs: &[f64], stages: usize, block: usize, out: &mut [f64]) {
    assert_eq!(x.len(), out.len());
    assert!(block >= 1 && gs.len() >= x.len().div_ceil(block));
    out.copy_from_slice(x);
    let mut zs = vec![0.0f64; stages];
    for (chunk, &g) in out.chunks_mut(block).zip(gs) {
        for z in zs.iter_mut() {
            let mut zz = *z;
            for v in chunk.iter_mut() {
                let xn = *v;
                let y = zz + g * xn;
                zz = xn - y * g;
                *v = y;
            }
            *z = zz;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_allpass_sweep_is_its_stages_one_after_another() {
        let n = 1000usize;
        let x: Vec<f64> = (0..n)
            .map(|i| ((i * 37) % 101) as f64 / 50.0 - 1.0)
            .collect();
        let gs: Vec<f64> = (0..n.div_ceil(64)).map(|k| -0.9 + 0.1 * k as f64).collect();
        let mut got = vec![0.0; n];
        allpass_sweep(&x, &gs, 3, 64, &mut got);
        let mut want = x.clone();
        for _ in 0..3 {
            let mut z = 0.0;
            for (i, v) in want.iter_mut().enumerate() {
                let g = gs[i / 64];
                let y = z + g * *v;
                z = *v - y * g;
                *v = y;
            }
        }
        assert_eq!(got, want);
        // allpass: the energy that went in comes out (less what is still
        // in the states)
        let (ein, eout): (f64, f64) = (
            x.iter().map(|v| v * v).sum(),
            got.iter().map(|v| v * v).sum(),
        );
        assert!((ein - eout).abs() < 0.05 * ein);
    }

    #[test]
    fn a_constant_input_is_silent_from_the_first_sample() {
        let x = vec![0.7; 1000];
        let mut y = vec![1.0; 1000];
        dc_block(&x, &mut y, 1, 0.998);
        assert!(y.iter().all(|&v| v.abs() < 1e-12));
    }

    #[test]
    fn the_stereo_pass_is_each_channel_alone() {
        let n = 500;
        let x: Vec<f64> = (0..2 * n).map(|i| ((i * 37) % 101) as f64 / 50.0).collect();
        let mut both = vec![0.0; 2 * n];
        dc_block(&x, &mut both, 2, 0.9983);
        for c in 0..2 {
            let mono: Vec<f64> = x[c..].iter().step_by(2).copied().collect();
            let mut y = vec![0.0; n];
            dc_block(&mono, &mut y, 1, 0.9983);
            let got: Vec<f64> = both[c..].iter().step_by(2).copied().collect();
            assert_eq!(got, y);
        }
    }
}
