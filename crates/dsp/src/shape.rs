//! Waveshaping: `core.soft_clip`.

use crate::math::tanh;
use crate::osc::Param;

/// `tanh(x * d) / tanh(d)` for `x` of `n` frames x `ch` interleaved
/// channels; `drive` is one value or one per frame (shared by the frame's
/// channels, as NumPy broadcasts `d[:, None]`). A per-frame drive is
/// floored at 1e-6 as `soft_clip` floors it; a constant drive must be > 0
/// (the caller returns `x` untouched otherwise).
pub fn soft_clip(x: &[f64], out: &mut [f64], ch: usize, drive: Param) {
    assert_eq!(x.len(), out.len());
    assert!(ch >= 1 && x.len().is_multiple_of(ch));
    match drive {
        Param::Const(d) => {
            let norm = tanh(d);
            for (y, &v) in out.iter_mut().zip(x) {
                *y = tanh(v * d) / norm;
            }
        }
        Param::Series(ds) => {
            assert_eq!(ds.len() * ch, x.len(), "one drive per frame");
            for ((fin, fout), &d) in x.chunks_exact(ch).zip(out.chunks_exact_mut(ch)).zip(ds) {
                let d = d.max(1e-6);
                let norm = tanh(d);
                for (y, &v) in fout.iter_mut().zip(fin) {
                    *y = tanh(v * d) / norm;
                }
            }
        }
    }
}

/// `soft_clip` over a buffer that is both input and output: the same
/// arithmetic per sample.
pub fn soft_clip_in_place(x: &mut [f64], ch: usize, drive: Param) {
    assert!(ch >= 1 && x.len().is_multiple_of(ch));
    match drive {
        Param::Const(d) => {
            let norm = tanh(d);
            for v in x.iter_mut() {
                *v = tanh(*v * d) / norm;
            }
        }
        Param::Series(ds) => {
            assert_eq!(ds.len() * ch, x.len(), "one drive per frame");
            for (frame, &d) in x.chunks_exact_mut(ch).zip(ds) {
                let d = d.max(1e-6);
                let norm = tanh(d);
                for v in frame {
                    *v = tanh(*v * d) / norm;
                }
            }
        }
    }
}
