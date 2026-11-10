//! Delay-line kernels of `effects.py`: the feedback delay (`_delay_line`)
//! and the swept read of the chorus/flanger (`_mod_delay`).

use crate::math::sin_turns;
use std::f64::consts::TAU;

/// `_delay_line`: `y[i] = x[i] + feedback * y[i - d]`. NumPy runs it a
/// delay-length block at a time, which reads only already-final samples,
/// so the per-sample recursion here gives identical results.
pub fn feedback_delay(x: &[f64], d: usize, feedback: f64, out: &mut [f64]) {
    assert_eq!(x.len(), out.len());
    assert!(d >= 1);
    out.copy_from_slice(x);
    for i in d..out.len() {
        out[i] += feedback * out[i - d];
    }
}

/// `_mod_delay`'s read: `src` sampled at `i - max(d(i), 0)` (clipped to the
/// buffer, linearly interpolated as `np.interp` does over `arange(n)`),
/// where `d(i) = (base_ms + depth_ms * sin(2π·rate·i/sr + phase)) * 1e-3 * sr`
/// in samples, the sine taken in turns (`math::sin_turns`).
pub fn swept_read(
    src: &[f64],
    base_ms: f64,
    depth_ms: f64,
    rate: f64,
    phase: f64,
    sr: f64,
    out: &mut [f64],
) {
    let n = src.len();
    assert_eq!(n, out.len());
    if n == 0 {
        return;
    }
    assert!(n <= i32::MAX as usize);
    let last = (n - 1) as f64;
    // pass 1, vectorizable: every read position (the sine is the cost; the
    // sample number goes through i32, which AVX2 converts packed)
    let p0 = phase / TAU;
    for (i, y) in out.iter_mut().enumerate() {
        let fi = (i as i32) as f64;
        let d = (base_ms + depth_ms * sin_turns(rate * (fi / sr) + p0)) * 1e-3 * sr;
        *y = (fi - d.max(0.0)).clamp(0.0, last);
    }
    // pass 2: the interpolated reads (a gather: scalar)
    for y in out.iter_mut() {
        let x = *y;
        let j = x as usize; // x >= 0: truncation is floor
        *y = if j + 1 >= n {
            src[n - 1]
        } else {
            (src[j + 1] - src[j]) * (x - j as f64) + src[j]
        };
    }
}

/// `effects._read_back`: `x` (`n` frames of `ch` interleaved channels) read
/// `d[i]` samples behind frame `i` (fractional, never ahead: a negative `d`
/// reads the present), linearly interpolated as `a + (b - a) * frac`, with
/// silence before the first frame and after the last — the twin's padded
/// gather, sample for sample.
pub fn read_back(x: &[f64], ch: usize, d: &[f64], out: &mut [f64]) {
    assert!(ch >= 1 && x.len() == d.len() * ch && x.len() == out.len());
    let n = d.len() as i64;
    let at = |k: i64, c: usize| {
        if (0..n).contains(&k) {
            x[k as usize * ch + c]
        } else {
            0.0
        }
    };
    for (i, (&di, y)) in d.iter().zip(out.chunks_exact_mut(ch)).enumerate() {
        let pos = i as f64 - di.max(0.0);
        let lo = pos.floor();
        let frac = pos - lo;
        let k = lo as i64;
        for (c, yc) in y.iter_mut().enumerate() {
            let (a, b) = (at(k, c), at(k + 1, c));
            *yc = a + (b - a) * frac;
        }
    }
}

/// `effects._rotor`: one rotor of the rotary speaker heard by two mics
/// `mic` radians apart. Its phase is the running sum of `rate` (Hz a
/// sample, from 0); per mic `s = sin(phase + off)` swings a read of the
/// mono `band` over `doppler * (1 + s) + min_delay` s behind now and a
/// level `1 - dip (1 + s) / 2`. Out: `(n, 2)` interleaved. The sum runs
/// in order as `np.cumsum` does; the sine is taken in turns
/// (`math::sin_turns`), so the result is the twin's to rounding.
#[allow(clippy::too_many_arguments)]
pub fn rotor(
    band: &[f64],
    rate: &[f64],
    doppler: f64,
    dip: f64,
    mic: f64,
    min_delay: f64,
    sr: f64,
    out: &mut [f64],
) {
    let n = band.len();
    assert!(rate.len() == n && out.len() == 2 * n && n <= i32::MAX as usize);
    // pass 1, serial: the phase in turns, ahead of each sample
    let mut turns = vec![0.0f64; n];
    let mut cum = 0.0f64;
    for i in 1..n {
        cum += rate[i - 1];
        turns[i] = cum / sr;
    }
    // pass 2, vectorizable: each mic's read position and level
    let offs = [0.0, mic / TAU];
    let mut level = vec![0.0f64; 2 * n];
    for (i, &t) in turns.iter().enumerate() {
        let fi = (i as i32) as f64;
        for (c, off) in offs.iter().enumerate() {
            let s = sin_turns(t + off);
            let d = (doppler * (1.0 + s) + min_delay) * sr;
            out[i * 2 + c] = fi - d.max(0.0);
            level[i * 2 + c] = 1.0 - dip * 0.5 * (1.0 + s);
        }
    }
    // pass 3: the interpolated reads (a gather), silence before the start
    let at = |k: i64| {
        if (0..n as i64).contains(&k) {
            band[k as usize]
        } else {
            0.0
        }
    };
    for (y, g) in out.iter_mut().zip(&level) {
        let p = *y;
        let lo = p.floor();
        let k = lo as i64;
        let (a, b) = (at(k), at(k + 1));
        *y = (a + (b - a) * (p - lo)) * g;
    }
}

/// `effects._tape_echo`'s loop: a stereo tape (`x`, `n` interleaved
/// frames) read `head[i]` samples behind now (linearly interpolated,
/// silence before the start), through the loop's one-pole low-pass
/// (scipy's `lfilter([1 - a], [1, -a])` arithmetic, its state carried from
/// sample to sample) and `tanh(y g) / g`; that is the tap, and the tape is
/// written `x + fb * tap`. Returns the taps. As the twin, a block shorter
/// than the shortest delay at a time — a block reads only tape already
/// written — so the reads and the recursion run first and the saturation
/// is a pass over the block that vectorizes; only tanh (`math::tanh`)
/// differs from the twin, to rounding.
pub fn tape_echo(x: &[f64], head: &[f64], fb: &[f64], b0: f64, a: f64, g: f64, taps: &mut [f64]) {
    let n = head.len();
    assert!(x.len() == 2 * n && taps.len() == 2 * n && fb.len() == n);
    if n == 0 {
        return;
    }
    // the read positions, and the block: `max(1, floor(min(i - pos)) - 1)`
    let pos: Vec<f64> = (0..n).map(|i| i as f64 - head[i].max(0.0)).collect();
    let near = (0..n)
        .map(|i| i as f64 - pos[i])
        .fold(f64::INFINITY, f64::min);
    let step = ((near.floor() as i64) - 1).max(1) as usize;
    let mut tape = vec![0.0f64; 2 * n];
    let mut z = [0.0f64; 2];
    let neg_a = -a;
    for i0 in (0..n).step_by(step) {
        let i1 = (i0 + step).min(n);
        for (i, &p) in pos.iter().enumerate().take(i1).skip(i0) {
            let lo = p.floor();
            let frac = p - lo;
            let k = lo as i64;
            for c in 0..2 {
                let read = |j: i64| {
                    if j >= 0 && (j as usize) < n {
                        tape[j as usize * 2 + c]
                    } else {
                        0.0
                    }
                };
                let (r0, r1) = (read(k), read(k + 1));
                let r = r0 + (r1 - r0) * frac;
                // lfilter's transposed form: y = z + b0 x; z = x b1 - y a1,
                // with b1 = 0 and a1 = -a
                let y = z[c] + b0 * r;
                z[c] = r * 0.0 - y * neg_a;
                taps[i * 2 + c] = y;
            }
        }
        let block = &mut taps[i0 * 2..i1 * 2];
        for t in block.iter_mut() {
            *t = crate::math::tanh(*t * g) / g;
        }
        for (i, f) in fb.iter().enumerate().take(i1).skip(i0) {
            tape[i * 2] = x[i * 2] + f * taps[i * 2];
            tape[i * 2 + 1] = x[i * 2 + 1] + f * taps[i * 2 + 1];
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn read_back_interpolates_and_stays_in_the_past() {
        let x: Vec<f64> = (1..=6).flat_map(|i| [i as f64, -(i as f64)]).collect();
        let d = [0.0, 0.5, 1.0, 2.5, -3.0, 10.0];
        let mut y = vec![0.0; 12];
        read_back(&x, 2, &d, &mut y);
        assert_eq!(
            y,
            vec![
                1.0, -1.0, 1.5, -1.5, 2.0, -2.0, 1.5, -1.5, 5.0, -5.0, 0.0, 0.0
            ]
        );
    }

    #[test]
    fn tape_echo_repeats_at_the_head_and_decays() {
        let n = 40;
        let mut x = vec![0.0; 2 * n];
        x[0] = 0.5;
        x[1] = -0.5;
        let head = vec![10.0; n];
        let fb = vec![0.5; n];
        let mut taps = vec![0.0; 2 * n];
        // no low-pass (a = 0), a clean loop (g -> 1 keeps tanh near linear)
        tape_echo(&x, &head, &fb, 1.0, 0.0, 1.0, &mut taps);
        assert!((taps[20] - 0.5f64.tanh()).abs() < 1e-15);
        assert_eq!(taps[21], -taps[20]);
        assert!(taps[40] > 0.0 && taps[40] < taps[20] * 0.5);
        assert_eq!(taps[18], 0.0);
    }

    #[test]
    fn feedback_echoes_decay_by_feedback() {
        let mut x = vec![0.0; 10];
        x[0] = 1.0;
        let mut y = vec![0.0; 10];
        feedback_delay(&x, 3, 0.5, &mut y);
        assert_eq!(y, vec![1.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.25, 0.0, 0.0, 0.125]);
    }

    #[test]
    fn zero_depth_is_a_fixed_delay() {
        let src: Vec<f64> = (0..1000).map(|i| i as f64).collect();
        let mut y = vec![0.0; 1000];
        // 1 ms at 10 kHz = 10 samples
        swept_read(&src, 1.0, 0.0, 1.0, 0.0, 10_000.0, &mut y);
        assert!((y[500] - 490.0).abs() < 1e-9);
        assert_eq!(y[3], 0.0); // clipped at the start
    }
}
