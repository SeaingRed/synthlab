//! Envelopes: `core.adsr`.
//!
//! The sample counts come in already rounded: `core.n_samples` rounds with
//! Python's `round` (half to even), and a count that disagreed by one
//! sample would shift every segment after it.

/// NumPy's `linspace(0, 1, num)[i]`: `i * (1 / (num - 1))`, with the last
/// point exactly 1 (and `[0.0]` for num == 1).
#[inline(always)]
fn ramp(i: usize, num: usize) -> f64 {
    if num <= 1 {
        0.0
    } else if i == num - 1 {
        1.0
    } else {
        i as f64 * (1.0 / (num - 1) as f64)
    }
}

/// `core.adsr` into `env` (length n). `gate_n` is the held length in
/// samples (already >= 1), `a_full`/`d_full` the attack and decay at full
/// length (the gate cuts them), `k` the landing ponytail's length when the
/// gate runs to the end of the buffer, `rel_n` the release: it runs that
/// long from the gate's end (fitted into the buffer when that ends first)
/// and the envelope is zero after it.
#[allow(clippy::too_many_arguments)]
pub fn adsr(
    env: &mut [f64],
    gate_n: usize,
    a_full: usize,
    d_full: usize,
    sustain: f64,
    curve: f64,
    k: usize,
    rel_n: usize,
) {
    let n = env.len();
    env.fill(0.0);
    let gate_n = gate_n.max(1);
    let a_n = a_full.min(gate_n);
    let d_n = d_full.min(gate_n - a_n);
    let s_n = gate_n.saturating_sub(a_n + d_n);
    let inv = 1.0 / curve;
    // x^(1/curve) on every decay and release sample: pow is the envelope's
    // whole cost, and nearly every patch has curve 2 (sqrt) or 1 (linear)
    let shape = |x: f64| -> f64 {
        if inv == 0.5 {
            x.sqrt()
        } else if inv == 1.0 {
            x
        } else {
            x.powf(inv)
        }
    };
    let mut i = 0;
    for (j, e) in env.iter_mut().enumerate().take(a_n) {
        *e = ramp(j, a_full);
    }
    i += a_n;
    for j in 0..d_n {
        if i + j < n {
            env[i + j] = 1.0 + (sustain - 1.0) * shape(ramp(j, d_full));
        }
    }
    i += d_n;
    env[i.min(n)..(i + s_n).min(n)].fill(sustain);
    i += s_n;
    if i < n {
        let start = if i > 0 { env[i - 1] } else { sustain };
        let tail = rel_n.min(n - i);
        for j in 0..tail {
            env[i + j] = start * (1.0 - shape(ramp(j, tail)));
        }
    } else if k > 1 {
        let k = k.min(n);
        for j in 0..k {
            let c = (std::f64::consts::PI * ramp(j, k)).cos();
            env[n - k + j] *= 0.5 * (1.0 + c);
        }
    } else if n > 0 {
        env[n - 1] = 0.0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn segments_land_where_they_should() {
        let mut e = vec![9.0; 100];
        adsr(&mut e, 60, 10, 10, 0.5, 1.0, 2, 40);
        assert_eq!(e[0], 0.0);
        assert_eq!(e[9], 1.0); // attack peak
        assert!((e[19] - 0.5).abs() < 1e-12); // decay lands on sustain
        assert_eq!(e[40], 0.5);
        assert_eq!(e[99], 0.0); // release lands on silence
    }

    #[test]
    fn the_release_lasts_its_own_length_then_silence() {
        let mut e = vec![9.0; 100];
        adsr(&mut e, 60, 10, 10, 0.5, 1.0, 2, 11);
        assert_eq!(e[60], 0.5); // linspace(0, 1, 11)[0]
        assert!((e[65] - 0.25).abs() < 1e-12); // halfway down, linear
        assert_eq!(e[70], 0.0); // its last point
        assert!(e[71..].iter().all(|&x| x == 0.0));
        // a buffer that ends first: the release is fitted into what is left
        let (mut short, mut whole) = (vec![9.0; 100], vec![9.0; 100]);
        adsr(&mut short, 60, 10, 10, 0.5, 1.0, 2, 400);
        adsr(&mut whole, 60, 10, 10, 0.5, 1.0, 2, 40);
        assert_eq!(short, whole);
    }
}
