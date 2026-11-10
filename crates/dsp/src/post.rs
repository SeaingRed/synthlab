//! `Synth.note`'s post chain in one call: what a voice goes through after
//! the oscillator stack — `core.dc_block`, the filter (`core.ladder`'s two
//! lowpass stages around its saturation, or one `core.biquad`), the amp
//! envelope and the drive.
//!
//! Every stage is the kernel the engine already runs for it (`filter::
//! dc_block`, `svf::swept`, `filter::lfilter`, `shape::soft_clip`), in the
//! same order on the same numbers, so the chain is bit-identical to calling
//! them one by one from Python. What it saves is what sat between them: an
//! array handed back and forth per stage, and NumPy's `mix *= env[:, None]`
//! — a broadcast over a length-2 axis that cost as much as a filter stage.
//!
//! The curves (the cutoff per block, the envelope times velocity and
//! tremolo, the drive) are resolved in Python and come in as slices.

use crate::filter;
use crate::osc::Param;
use crate::shape;
use crate::svf::{self, Kind};

pub enum Stage<'a> {
    /// `core.dc_block` with pole `r`
    Dc(f64),
    /// `core._swept_biquad`: one cutoff per `block` frames, one `q` or one
    /// per block
    Swept {
        kind: Kind,
        fc: &'a [f64],
        q: &'a [f64],
        block: usize,
    },
    /// `core.biquad`'s static path: scipy's `lfilter` with fixed `b`/`a`
    Lfilter { b: &'a [f64], a: &'a [f64] },
    /// `core.soft_clip`: one drive (> 0) or one per frame
    Clip(Param<'a>),
    /// `x *= gain[:, None]`: one gain per frame
    Gain(&'a [f64]),
}

/// Why `stages` can't run on `n` frames, if they can't (the binding turns
/// this into a ValueError: a panic would take the interpreter with it).
pub fn check(stages: &[Stage], n: usize) -> Result<(), &'static str> {
    for st in stages {
        match st {
            Stage::Dc(_) => {}
            Stage::Swept { fc, q, block, .. } => {
                if *block == 0 || fc.len() * block < n {
                    return Err("need one cutoff per block");
                }
                if q.len() != 1 && q.len() != fc.len() {
                    return Err("q is a scalar or one per block");
                }
            }
            Stage::Lfilter { b, a } => {
                if b.is_empty() || a.is_empty() || b.len() > 3 || a.len() > 3 || a[0] == 0.0 {
                    return Err("lfilter: 1..3 taps, a[0] != 0");
                }
            }
            // (a constant drive that isn't > 0 is the caller's to skip, as
            // `core.soft_clip` does)
            Stage::Clip(Param::Const(_)) => {}
            Stage::Clip(Param::Series(d)) => {
                if d.len() != n {
                    return Err("drive must be 1 or n long");
                }
            }
            Stage::Gain(g) => {
                if g.len() != n {
                    return Err("gain must be n long");
                }
            }
        }
    }
    Ok(())
}

/// `x` (`n` frames of `ch` interleaved channels) through `stages`, in
/// order. The stages must have passed `check`.
pub fn chain(x: &[f64], ch: usize, sr: f64, stages: &[Stage]) -> Vec<f64> {
    assert!(ch >= 1 && x.len().is_multiple_of(ch));
    let mut cur = x.to_vec();
    let mut next = vec![0.0f64; x.len()];
    let mut i = 0;
    while i < stages.len() {
        // a ladder: two swept stages on one block grid around a saturation
        if ch == 2
            && let [
                Stage::Swept {
                    kind: k1,
                    fc: fc1,
                    q: q1,
                    block: b1,
                },
                Stage::Clip(drive),
                Stage::Swept {
                    kind: k2,
                    fc: fc2,
                    q: q2,
                    block: b2,
                },
                ..,
            ] = &stages[i..]
            && b1 == b2
        {
            let one = svf::Sweep {
                kind: *k1,
                fc: fc1,
                q: q1,
            };
            let two = svf::Sweep {
                kind: *k2,
                fc: fc2,
                q: q2,
            };
            svf::ladder_stereo(&mut cur, &mut next, &one, *drive, &two, sr, *b1);
            std::mem::swap(&mut cur, &mut next);
            i += 3;
            continue;
        }
        match &stages[i] {
            Stage::Dc(r) => filter::dc_block(&cur, &mut next, ch, *r),
            Stage::Swept { kind, fc, q, block } => {
                svf::swept(&cur, &mut next, ch, *kind, fc, q, sr, *block)
            }
            Stage::Lfilter { b, a } => filter::lfilter(b, a, &cur, &mut next, ch, &[]),
            Stage::Clip(drive) => shape::soft_clip(&cur, &mut next, ch, *drive),
            Stage::Gain(g) => {
                for (frame, &gi) in cur.chunks_exact_mut(ch).zip(g.iter()) {
                    for v in frame {
                        *v *= gi;
                    }
                }
                i += 1;
                continue; // in place: nothing to swap
            }
        }
        std::mem::swap(&mut cur, &mut next);
        i += 1;
    }
    cur
}

#[cfg(test)]
mod tests {
    use super::*;

    fn noise(n: usize) -> Vec<f64> {
        // a fixed LCG: anything broadband and repeatable
        let mut s = 0x2545_f491_4f6c_dd1du64;
        (0..n)
            .map(|_| {
                s = s
                    .wrapping_mul(6364136223846793005)
                    .wrapping_add(1442695040888963407);
                (s >> 11) as f64 / (1u64 << 53) as f64 - 0.5
            })
            .collect()
    }

    /// The chain against its stages run one by one, for `ch` channels.
    fn by_hand(x: &[f64], ch: usize, sr: f64, stages: &[Stage]) -> Vec<f64> {
        let mut cur = x.to_vec();
        for st in stages {
            let mut next = vec![0.0; cur.len()];
            match st {
                Stage::Dc(r) => filter::dc_block(&cur, &mut next, ch, *r),
                Stage::Swept { kind, fc, q, block } => {
                    svf::swept(&cur, &mut next, ch, *kind, fc, q, sr, *block)
                }
                Stage::Lfilter { b, a } => filter::lfilter(b, a, &cur, &mut next, ch, &[]),
                Stage::Clip(d) => shape::soft_clip(&cur, &mut next, ch, *d),
                Stage::Gain(g) => {
                    for (i, v) in cur.iter().enumerate() {
                        next[i] = v * g[i / ch];
                    }
                }
            }
            cur = next;
        }
        cur
    }

    #[test]
    fn the_chain_is_its_stages_bit_for_bit() {
        let sr = 44_100.0;
        for &(n, ch) in &[
            (0usize, 2usize),
            (1, 2),
            (63, 2),
            (64, 2),
            (65, 2),
            (5000, 2),
            (777, 1),
        ] {
            let x = noise(n * ch);
            let nb = n.div_ceil(64);
            let fc: Vec<f64> = (0..nb).map(|b| 80.0 + 37.0 * b as f64).collect();
            let q: Vec<f64> = (0..nb).map(|b| 0.7 + 0.05 * b as f64).collect();
            let gain: Vec<f64> = (0..n).map(|i| 1.0 - i as f64 / (n + 1) as f64).collect();
            let drive: Vec<f64> = (0..n).map(|i| 0.5 + 0.001 * i as f64).collect();
            for per_frame_drive in [false, true] {
                let d = if per_frame_drive {
                    Param::Series(&drive)
                } else {
                    Param::Const(1.7)
                };
                let stages = [
                    Stage::Dc(0.998),
                    Stage::Swept {
                        kind: Kind::Lowpass,
                        fc: &fc,
                        q: &q,
                        block: 64,
                    },
                    Stage::Clip(d),
                    Stage::Swept {
                        kind: Kind::Lowpass,
                        fc: &fc,
                        q: &[0.707],
                        block: 64,
                    },
                    Stage::Gain(&gain),
                    Stage::Clip(d),
                ];
                assert!(check(&stages, n).is_ok());
                assert_eq!(
                    chain(&x, ch, sr, &stages),
                    by_hand(&x, ch, sr, &stages),
                    "n {n} ch {ch}"
                );
            }
        }
    }

    #[test]
    fn check_refuses_what_would_panic() {
        let fc = [100.0; 2];
        let short = Stage::Swept {
            kind: Kind::Lowpass,
            fc: &fc,
            q: &[1.0],
            block: 64,
        };
        assert!(check(&[short], 129).is_err());
        assert!(check(&[Stage::Clip(Param::Series(&[1.0; 3]))], 4).is_err());
        assert!(check(&[Stage::Gain(&[1.0; 3])], 4).is_err());
        assert!(check(&[Stage::Dc(0.9), Stage::Gain(&[1.0; 4])], 4).is_ok());
    }
}
