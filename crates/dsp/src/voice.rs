//! `Synth.note`'s unison oscillator stack in one call: for each unison
//! voice, `Synth._osc_stack` (osc2, cross-FM into osc1, osc1, ring mod,
//! levels, the sub, the noise layer) and its equal-power place in the
//! stereo field, summed and scaled by 1/sqrt(voices). The one piece of a
//! note that NumPy walked through dozens of temporary arrays per voice.
//!
//! What stays in Python, because it has to match NumPy's generators: the
//! seeded noise (buffers in), supersaw start phases (in), and the per-voice
//! pan gains (cos/sin of the voice's angle, in).

use crate::osc::{self, Param, Wavetable};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Shape {
    Basic(osc::Shape),
    Supersaw,
    Fm,
    /// reads `Stack::wavetable`
    Wavetable,
    /// the "noise" oscillator shape: a buffer per unison voice, from Python
    Noise,
}

impl Shape {
    pub fn parse(s: &str) -> Option<Shape> {
        match s {
            "supersaw" => Some(Shape::Supersaw),
            "fm" => Some(Shape::Fm),
            "wavetable" => Some(Shape::Wavetable),
            "noise" => Some(Shape::Noise),
            other => osc::Shape::parse(other).map(Shape::Basic),
        }
    }
}

pub struct Osc<'a> {
    pub shape: Shape,
    pub octave: f64,
    /// cents
    pub detune: Param<'a>,
    pub level: Param<'a>,
    /// Noise: `voices * n` samples, voice-major
    pub noise: &'a [f64],
    /// Supersaw: 7 start phases per voice, voice-major
    pub ss_phases: &'a [f64],
}

pub struct Stack<'a> {
    pub sr: f64,
    pub freq: Param<'a>,
    /// per unison voice: its detune position in [-1, 1], and its pan gains
    pub offsets: &'a [f64],
    pub gain_l: &'a [f64],
    pub gain_r: &'a [f64],
    pub unison_detune: Param<'a>,
    /// the pulse width (0.5, or the PWM curve)
    pub width: Param<'a>,
    pub osc1: Osc<'a>,
    pub osc2: Option<Osc<'a>>,
    /// cross-FM osc2 -> osc1 and ring mod: None when their gate is off
    pub fm_amount: Option<Param<'a>>,
    pub ring: Option<Param<'a>>,
    pub sub_level: Option<Param<'a>>,
    /// the noise layer: its level, and `voices * n` coloured noise samples
    pub noise_level: Option<Param<'a>>,
    pub noise_layer: &'a [f64],
    /// the supersaw's per-voice detune ratios (its 7 saws)
    pub ss_ratios: &'a [f64],
    pub fm_ratio: Param<'a>,
    pub fm_index: Param<'a>,
    pub fm_decay: f64,
    /// the patch's table and position, when an oscillator is a wavetable
    pub wavetable: Option<Wavetable<'a>>,
}

fn render_osc(s: &Stack, o: &Osc, u: usize, f: &[f64], out: &mut [f64]) {
    let n = out.len();
    match o.shape {
        Shape::Basic(shape) => osc::render(shape, Param::Series(f), s.width, s.sr, 0.0, out),
        Shape::Supersaw => {
            let k = s.ss_ratios.len();
            let phases = &o.ss_phases[u * k..(u + 1) * k];
            osc::supersaw(Param::Series(f), s.ss_ratios, phases, s.sr, out)
        }
        Shape::Fm => osc::fm(
            Param::Series(f),
            s.fm_ratio,
            s.fm_index,
            s.fm_decay,
            s.sr,
            0.0,
            out,
        ),
        Shape::Wavetable => {
            let wt = s
                .wavetable
                .as_ref()
                .expect("a wavetable oscillator's table");
            osc::wavetable(Param::Series(f), wt, s.sr, 0.0, out)
        }
        Shape::Noise => out.copy_from_slice(&o.noise[u * n..(u + 1) * n]),
    }
}

/// `f * 2**octave * 2**(detune / 1200)`, as `_osc_stack` scales a voice's
/// pitch for each oscillator
fn scaled(f: &[f64], o: &Osc, out: &mut [f64]) {
    let oct = 2f64.powf(o.octave);
    match o.detune {
        // a fixed detune (nearly every patch) is one ratio for the note
        Param::Const(d) => {
            let r = 2f64.powf(d / 1200.0);
            for (y, &v) in out.iter_mut().zip(f) {
                *y = v * oct * r;
            }
        }
        Param::Series(ds) => {
            for ((y, &v), &d) in out.iter_mut().zip(f).zip(ds) {
                *y = v * oct * 2f64.powf(d / 1200.0);
            }
        }
    }
}

#[inline(always)]
fn at(p: Param, i: usize) -> f64 {
    match p {
        Param::Const(v) => v,
        Param::Series(s) => s[i],
    }
}

/// Render the stack into `out` (`n` frames, L/R interleaved).
pub fn stack(s: &Stack, out: &mut [f64]) {
    let n = out.len() / 2;
    let voices = s.offsets.len();
    assert!(voices >= 1 && s.gain_l.len() == voices && s.gain_r.len() == voices);
    out.fill(0.0);
    let (mut fu, mut f1, mut f2) = (vec![0.0; n], vec![0.0; n], vec![0.0; n]);
    let (mut o1, mut o2, mut sub) = (vec![0.0; n], vec![0.0; n], vec![0.0; n]);
    for u in 0..voices {
        // the voice's own pitch: freq * 2**(off * unison_detune / 1200)
        let off = s.offsets[u];
        match s.unison_detune {
            Param::Const(d) => {
                let r = 2f64.powf(off * d / 1200.0);
                for (i, y) in fu.iter_mut().enumerate() {
                    *y = at(s.freq, i) * r;
                }
            }
            Param::Series(ds) => {
                for (i, (y, &d)) in fu.iter_mut().zip(ds).enumerate() {
                    *y = at(s.freq, i) * 2f64.powf(off * d / 1200.0);
                }
            }
        }
        if let Some(osc2) = &s.osc2 {
            scaled(&fu, osc2, &mut f2);
            render_osc(s, osc2, u, &f2, &mut o2);
        }
        scaled(&fu, &s.osc1, &mut f1);
        if let (Some(_), Some(amt)) = (&s.osc2, s.fm_amount) {
            for (i, (f, &m)) in f1.iter_mut().zip(&o2).enumerate() {
                *f *= 1.0 + 0.9 * at(amt, i) * m;
            }
        }
        render_osc(s, &s.osc1, u, &f1, &mut o1);
        if let (Some(_), Some(ring)) = (&s.osc2, s.ring) {
            for (i, (a, &b)) in o1.iter_mut().zip(&o2).enumerate() {
                let r = at(ring, i);
                *a = *a * (1.0 - r) + *a * b * r;
            }
        }
        if s.sub_level.is_some() {
            for (y, &f) in sub.iter_mut().zip(&fu) {
                *y = f * 0.5;
            }
            let half = sub.clone();
            osc::render(
                osc::Shape::Sine,
                Param::Series(&half),
                Param::Const(0.5),
                s.sr,
                0.0,
                &mut sub,
            );
        }
        let (gl, gr) = (s.gain_l[u], s.gain_r[u]);
        let noise = &s.noise_layer[if s.noise_level.is_some() {
            u * n..(u + 1) * n
        } else {
            0..0
        }];
        for i in 0..n {
            let mut raw = at(s.osc1.level, i) * o1[i];
            if let Some(osc2) = &s.osc2 {
                raw += at(osc2.level, i) * o2[i];
            }
            if let Some(level) = s.sub_level {
                raw += at(level, i) * sub[i];
            }
            if let Some(level) = s.noise_level {
                raw += at(level, i) * noise[i];
            }
            out[2 * i] += raw * gl;
            out[2 * i + 1] += raw * gr;
        }
    }
    let norm = (voices as f64).sqrt();
    out.iter_mut().for_each(|y| *y /= norm);
}
