//! `synthlab_dsp`: the Python face of the `dsp` crate. Each function takes
//! C-contiguous float64 arrays (synthlab/_native.py makes sure of that),
//! releases the GIL for the kernel — so notes, stems and breed children can
//! render on several cores at once — and hands back a new array.

use numpy::{
    PyArray1, PyArrayDyn, PyArrayMethods, PyReadonlyArray1, PyReadonlyArrayDyn,
    PyUntypedArrayMethods,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

/// `core._swept_biquad` without the closed-form detour: `x` is `(n,)` or
/// `(n, ch)`, `fc` one cutoff per block, `q` one value or one per block.
#[pyfunction]
fn swept_svf<'py>(
    py: Python<'py>,
    x: PyReadonlyArrayDyn<'py, f64>,
    kind: &str,
    fc: PyReadonlyArray1<'py, f64>,
    q: PyReadonlyArray1<'py, f64>,
    sr: f64,
    block: usize,
) -> PyResult<Bound<'py, PyArrayDyn<f64>>> {
    let kind = dsp::svf::Kind::parse(kind)
        .ok_or_else(|| PyValueError::new_err(format!("unknown filter kind: {kind}")))?;
    let shape = x.shape().to_vec();
    let ch = match shape.as_slice() {
        [_] => 1,
        [_, c] if *c >= 1 => *c,
        _ => return Err(PyValueError::new_err("x must be (n,) or (n, ch)")),
    };
    let n = shape[0];
    if block == 0 || fc.len() * block < n {
        return Err(PyValueError::new_err("need one cutoff per block"));
    }
    let q_len = q.len();
    if q_len != 1 && q_len != fc.len() {
        return Err(PyValueError::new_err("q is a scalar or one per block"));
    }
    let (xs, fcs, qs) = (x.as_slice()?, fc.as_slice()?, q.as_slice()?);
    let mut out = vec![0.0f64; xs.len()];
    py.detach(|| dsp::svf::swept(xs, &mut out, ch, kind, fcs, qs, sr, block));
    PyArray1::from_vec(py, out).reshape(shape)
}

/// `core.sine/saw/square/triangle` in one pass: `freq` and `width` are one
/// value or `n` of them; returns `(n,)`.
#[pyfunction]
fn osc<'py>(
    py: Python<'py>,
    shape: &str,
    freq: PyReadonlyArray1<'py, f64>,
    width: PyReadonlyArray1<'py, f64>,
    n: usize,
    sr: f64,
    phase0: f64,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let shape = dsp::osc::Shape::parse(shape)
        .ok_or_else(|| PyValueError::new_err(format!("unknown shape: {shape}")))?;
    let (fs, ws) = (freq.as_slice()?, width.as_slice()?);
    for (name, s) in [("freq", fs), ("width", ws)] {
        if s.len() != 1 && s.len() != n {
            return Err(PyValueError::new_err(format!("{name} must be 1 or n long")));
        }
    }
    let mut out = vec![0.0f64; n];
    py.detach(|| {
        let (f, w) = (
            dsp::osc::Param::from_slice(fs),
            dsp::osc::Param::from_slice(ws),
        );
        dsp::osc::render(shape, f, w, sr, phase0, &mut out)
    });
    Ok(PyArray1::from_vec(py, out))
}

/// `effects._delay_line`: `y[i] = x[i] + feedback * y[i - d]`.
#[pyfunction]
fn feedback_delay<'py>(
    py: Python<'py>,
    x: PyReadonlyArray1<'py, f64>,
    d: usize,
    feedback: f64,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    if d == 0 {
        return Err(PyValueError::new_err("delay must be at least one sample"));
    }
    let xs = x.as_slice()?;
    let mut out = vec![0.0f64; xs.len()];
    py.detach(|| dsp::delay::feedback_delay(xs, d, feedback, &mut out));
    Ok(PyArray1::from_vec(py, out))
}

/// `effects._mod_delay`'s swept, interpolated read of `src`.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn swept_read<'py>(
    py: Python<'py>,
    src: PyReadonlyArray1<'py, f64>,
    base_ms: f64,
    depth_ms: f64,
    rate: f64,
    phase: f64,
    sr: f64,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let s = src.as_slice()?;
    let mut out = vec![0.0f64; s.len()];
    py.detach(|| dsp::delay::swept_read(s, base_ms, depth_ms, rate, phase, sr, &mut out));
    Ok(PyArray1::from_vec(py, out))
}

/// `effects._read_back`: `x` `(n,)` or `(n, ch)` read `d[i]` samples
/// behind each frame, interpolated; returns `x`'s shape.
#[pyfunction]
fn read_back<'py>(
    py: Python<'py>,
    x: PyReadonlyArrayDyn<'py, f64>,
    d: PyReadonlyArray1<'py, f64>,
) -> PyResult<Bound<'py, PyArrayDyn<f64>>> {
    let shape = x.shape().to_vec();
    let ch = channels(&shape)?;
    let (xs, ds) = (x.as_slice()?, d.as_slice()?);
    if ds.len() != shape[0] {
        return Err(PyValueError::new_err("read_back: one delay a frame"));
    }
    let mut out = vec![0.0f64; xs.len()];
    py.detach(|| dsp::delay::read_back(xs, ch, ds, &mut out));
    PyArray1::from_vec(py, out).reshape(shape)
}

/// `effects._tape_echo`'s loop: `x` `(n, 2)`, the head's delay and the
/// feedback one a frame; returns the taps `(n, 2)`.
#[pyfunction]
fn tape_echo<'py>(
    py: Python<'py>,
    x: PyReadonlyArrayDyn<'py, f64>,
    head: PyReadonlyArray1<'py, f64>,
    fb: PyReadonlyArray1<'py, f64>,
    a: f64,
    g: f64,
) -> PyResult<Bound<'py, PyArrayDyn<f64>>> {
    let shape = x.shape().to_vec();
    if shape.len() != 2 || shape[1] != 2 {
        return Err(PyValueError::new_err("tape_echo: x must be (n, 2)"));
    }
    let (xs, hs, fs) = (x.as_slice()?, head.as_slice()?, fb.as_slice()?);
    if hs.len() != shape[0] || fs.len() != shape[0] {
        return Err(PyValueError::new_err("tape_echo: head and fb one a frame"));
    }
    let mut out = vec![0.0f64; xs.len()];
    py.detach(|| dsp::delay::tape_echo(xs, hs, fs, 1.0 - a, a, g, &mut out));
    PyArray1::from_vec(py, out).reshape(shape)
}

/// `effects._rotor`: one rotor of the rotary speaker over the mono `band`,
/// its rate (Hz) a sample; returns `(n, 2)`.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn rotor<'py>(
    py: Python<'py>,
    band: PyReadonlyArray1<'py, f64>,
    rate: PyReadonlyArray1<'py, f64>,
    doppler: f64,
    dip: f64,
    mic: f64,
    min_delay: f64,
    sr: f64,
) -> PyResult<Bound<'py, PyArrayDyn<f64>>> {
    let (bs, rs) = (band.as_slice()?, rate.as_slice()?);
    if rs.len() != bs.len() {
        return Err(PyValueError::new_err("rotor: one rate a sample"));
    }
    let mut out = vec![0.0f64; 2 * bs.len()];
    py.detach(|| dsp::delay::rotor(bs, rs, doppler, dip, mic, min_delay, sr, &mut out));
    PyArray1::from_vec(py, out).reshape(vec![bs.len(), 2])
}

/// `effects._sines`: `Σ amp sin(2π hz t + ph)` over `n` samples.
#[pyfunction]
fn sines<'py>(
    py: Python<'py>,
    hz: PyReadonlyArray1<'py, f64>,
    ph: PyReadonlyArray1<'py, f64>,
    amp: PyReadonlyArray1<'py, f64>,
    n: usize,
    sr: f64,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let (h, p, a) = (hz.as_slice()?, ph.as_slice()?, amp.as_slice()?);
    if h.len() != p.len() || h.len() != a.len() || n > i32::MAX as usize {
        return Err(PyValueError::new_err("sines: hz, ph, amp one a sine"));
    }
    let mut out = vec![0.0f64; n];
    py.detach(|| dsp::osc::sines(h, p, a, sr, &mut out));
    Ok(PyArray1::from_vec(py, out))
}

/// `core.scan`: one pass over `x` (`(n,)` or `(n, ch)`) for (finite, peak,
/// sum of squares, body) — body the `q`th percentile of the frames' peaks
/// (NumPy's linear method, exactly), 0 when `q` is None, the buffer is
/// empty or not finite.
#[pyfunction]
#[pyo3(signature = (x, q=None))]
fn scan<'py>(
    py: Python<'py>,
    x: PyReadonlyArrayDyn<'py, f64>,
    q: Option<f64>,
) -> PyResult<(bool, f64, f64, f64)> {
    let shape = x.shape().to_vec();
    let ch = channels(&shape)?;
    let xs = x.as_slice()?;
    Ok(py.detach(|| {
        let s = dsp::scan::scan(xs);
        let body = match q {
            Some(q) if s.finite && !xs.is_empty() => {
                let mut mags = vec![0.0f64; xs.len() / ch];
                dsp::scan::frame_peaks(xs, ch, &mut mags);
                dsp::scan::percentile(&mut mags, q)
            }
            _ => 0.0,
        };
        (s.finite, s.peak, s.sumsq, body)
    }))
}

/// The channel count of an `(n,)` or `(n, ch)` buffer.
fn channels(shape: &[usize]) -> PyResult<usize> {
    match shape {
        [_] => Ok(1),
        [_, c] if *c >= 1 => Ok(*c),
        _ => Err(PyValueError::new_err("x must be (n,) or (n, ch)")),
    }
}

/// `core.soft_clip`: `x` is `(n,)` or `(n, ch)`; `drive` one value (> 0)
/// or one per frame.
#[pyfunction]
fn soft_clip<'py>(
    py: Python<'py>,
    x: PyReadonlyArrayDyn<'py, f64>,
    drive: PyReadonlyArray1<'py, f64>,
) -> PyResult<Bound<'py, PyArrayDyn<f64>>> {
    let shape = x.shape().to_vec();
    let ch = match shape.as_slice() {
        [_] => 1,
        [_, c] if *c >= 1 => *c,
        _ => return Err(PyValueError::new_err("x must be (n,) or (n, ch)")),
    };
    let ds = drive.as_slice()?;
    if ds.len() != 1 && ds.len() != shape[0] {
        return Err(PyValueError::new_err("drive must be 1 or n long"));
    }
    let xs = x.as_slice()?;
    let mut out = vec![0.0f64; xs.len()];
    py.detach(|| dsp::shape::soft_clip(xs, &mut out, ch, dsp::osc::Param::from_slice(ds)));
    PyArray1::from_vec(py, out).reshape(shape)
}

fn series<'a>(name: &str, s: &'a [f64], n: usize) -> PyResult<dsp::osc::Param<'a>> {
    if s.len() != 1 && s.len() != n {
        return Err(PyValueError::new_err(format!("{name} must be 1 or n long")));
    }
    Ok(dsp::osc::Param::from_slice(s))
}

/// `core.supersaw`: `ratios`/`phases` per voice (the caller draws the
/// phases from its seeded rng, as NumPy did).
#[pyfunction]
fn supersaw<'py>(
    py: Python<'py>,
    freq: PyReadonlyArray1<'py, f64>,
    ratios: PyReadonlyArray1<'py, f64>,
    phases: PyReadonlyArray1<'py, f64>,
    n: usize,
    sr: f64,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let f = series("freq", freq.as_slice()?, n)?;
    let (rs, ps) = (ratios.as_slice()?, phases.as_slice()?);
    if rs.len() != ps.len() || rs.is_empty() {
        return Err(PyValueError::new_err("one ratio and one phase per voice"));
    }
    let mut out = vec![0.0f64; n];
    py.detach(|| dsp::osc::supersaw(f, rs, ps, sr, &mut out));
    Ok(PyArray1::from_vec(py, out))
}

/// `core.fm`: `freq`, `ratio`, `index` are one value or n of them.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn fm<'py>(
    py: Python<'py>,
    freq: PyReadonlyArray1<'py, f64>,
    ratio: PyReadonlyArray1<'py, f64>,
    index: PyReadonlyArray1<'py, f64>,
    index_decay: f64,
    n: usize,
    sr: f64,
    phase0: f64,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let f = series("freq", freq.as_slice()?, n)?;
    let r = series("ratio", ratio.as_slice()?, n)?;
    let i = series("index", index.as_slice()?, n)?;
    let mut out = vec![0.0f64; n];
    py.detach(|| dsp::osc::fm(f, r, i, index_decay, sr, phase0, &mut out));
    Ok(PyArray1::from_vec(py, out))
}

/// A wavetable's pieces, checked: the flattened `(mips, frames, len)`
/// stack, a harmonic cap per mip, the frame count and the position curve.
fn wavetable_of<'a>(
    table: &'a [f64],
    caps: &'a [f64],
    frames: usize,
    pos: &'a [f64],
    n: usize,
) -> PyResult<dsp::osc::Wavetable<'a>> {
    if pos.is_empty() {
        return Err(PyValueError::new_err("wavetable: missing pos"));
    }
    let wt = dsp::osc::Wavetable {
        table,
        caps,
        frames,
        pos: series("pos", pos, n)?,
    };
    if !wt.fits(n) {
        return Err(PyValueError::new_err(
            "wavetable: table must be (mips, frames, len), one cap per mip",
        ));
    }
    Ok(wt)
}

/// `core.wavetable`: `freq` and `pos` are one value or n of them; `table`
/// is the table's mip stack `(mips, frames, len)` flattened, `caps` each
/// mip's harmonic cap (brightest first).
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn wavetable<'py>(
    py: Python<'py>,
    freq: PyReadonlyArray1<'py, f64>,
    pos: PyReadonlyArray1<'py, f64>,
    table: PyReadonlyArray1<'py, f64>,
    caps: PyReadonlyArray1<'py, f64>,
    frames: usize,
    n: usize,
    sr: f64,
    phase0: f64,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let f = series("freq", freq.as_slice()?, n)?;
    let (ts, cs, ps) = (table.as_slice()?, caps.as_slice()?, pos.as_slice()?);
    let wt = wavetable_of(ts, cs, frames, ps, n)?;
    let mut out = vec![0.0f64; n];
    py.detach(|| dsp::osc::wavetable(f, &wt, sr, phase0, &mut out));
    Ok(PyArray1::from_vec(py, out))
}

/// `core.dc_block`: `x` is `(n,)` or `(n, ch)`; `r` the pole.
#[pyfunction]
fn dc_block<'py>(
    py: Python<'py>,
    x: PyReadonlyArrayDyn<'py, f64>,
    r: f64,
) -> PyResult<Bound<'py, PyArrayDyn<f64>>> {
    let shape = x.shape().to_vec();
    let ch = match shape.as_slice() {
        [_] => 1,
        [_, c] if *c >= 1 => *c,
        _ => return Err(PyValueError::new_err("x must be (n,) or (n, ch)")),
    };
    let xs = x.as_slice()?;
    let mut out = vec![0.0f64; xs.len()];
    py.detach(|| dsp::filter::dc_block(xs, &mut out, ch, r));
    PyArray1::from_vec(py, out).reshape(shape)
}

/// scipy.signal.lfilter along axis 0 for filters up to order 2
/// (`core._lfilter` picks it); `zi` empty for a zero initial state.
#[pyfunction]
fn lfilter<'py>(
    py: Python<'py>,
    b: PyReadonlyArray1<'py, f64>,
    a: PyReadonlyArray1<'py, f64>,
    x: PyReadonlyArrayDyn<'py, f64>,
    zi: PyReadonlyArray1<'py, f64>,
) -> PyResult<Bound<'py, PyArrayDyn<f64>>> {
    let shape = x.shape().to_vec();
    let ch = match shape.as_slice() {
        [_] => 1,
        [_, c] if *c >= 1 => *c,
        _ => return Err(PyValueError::new_err("x must be (n,) or (n, ch)")),
    };
    let (bs, as_, zs) = (b.as_slice()?, a.as_slice()?, zi.as_slice()?);
    let k = bs.len().max(as_.len());
    if bs.is_empty() || as_.is_empty() || k > 3 || as_[0] == 0.0 {
        return Err(PyValueError::new_err("lfilter: 1..3 taps, a[0] != 0"));
    }
    if !zs.is_empty() && zs.len() != (k - 1) * ch {
        return Err(PyValueError::new_err("lfilter: zi is (order) per channel"));
    }
    let xs = x.as_slice()?;
    let mut out = vec![0.0f64; xs.len()];
    py.detach(|| dsp::filter::lfilter(bs, as_, xs, &mut out, ch, zs));
    PyArray1::from_vec(py, out).reshape(shape)
}

/// `effects._allpass_sweep`: `stages` first-order allpasses over a mono
/// buffer, coefficient `gs[k]` for block `k` of `block` samples.
#[pyfunction]
fn allpass_sweep<'py>(
    py: Python<'py>,
    x: PyReadonlyArray1<'py, f64>,
    gs: PyReadonlyArray1<'py, f64>,
    stages: usize,
    block: usize,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let (xs, g) = (x.as_slice()?, gs.as_slice()?);
    if block == 0 || g.len() < xs.len().div_ceil(block) {
        return Err(PyValueError::new_err(
            "allpass_sweep: block >= 1 and one coefficient a block",
        ));
    }
    let mut out = vec![0.0f64; xs.len()];
    py.detach(|| dsp::filter::allpass_sweep(xs, g, stages, block, &mut out));
    Ok(PyArray1::from_vec(py, out))
}

/// `core.adsr` with its sample counts already rounded by Python.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn adsr<'py>(
    py: Python<'py>,
    n: usize,
    gate_n: usize,
    a_full: usize,
    d_full: usize,
    sustain: f64,
    curve: f64,
    k: usize,
    rel_n: usize,
) -> Bound<'py, PyArray1<f64>> {
    let mut env = vec![0.0f64; n];
    py.detach(|| dsp::env::adsr(&mut env, gate_n, a_full, d_full, sustain, curve, k, rel_n));
    PyArray1::from_vec(py, env)
}

/// A 1-D float64 array from the spec dict (`None` when the key is absent).
fn arr<'py>(spec: &Bound<'py, PyDict>, key: &str) -> PyResult<Option<PyReadonlyArray1<'py, f64>>> {
    match spec.get_item(key)? {
        None => Ok(None),
        Some(v) if v.is_none() => Ok(None),
        Some(v) => Ok(Some(v.extract()?)),
    }
}

fn num(spec: &Bound<'_, PyDict>, key: &str) -> PyResult<f64> {
    spec.get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("stack: missing {key}")))?
        .extract()
}

fn text(spec: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<String>> {
    match spec.get_item(key)? {
        None => Ok(None),
        Some(v) if v.is_none() => Ok(None),
        Some(v) => Ok(Some(v.extract()?)),
    }
}

/// `Synth.note`'s unison oscillator stack (`dsp::voice::stack`) from a spec
/// dict built by `synthlab.synth._native_stack`; returns `(n, 2)`.
#[pyfunction]
fn stack<'py>(
    py: Python<'py>,
    n: usize,
    spec: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyArrayDyn<f64>>> {
    use dsp::voice::{Osc, Shape, Stack};
    // every array stays borrowed (readonly) for the whole render
    let keys = [
        "freq",
        "offsets",
        "gain_l",
        "gain_r",
        "unison_detune",
        "width",
        "osc1_detune",
        "osc1_level",
        "osc1_noise",
        "osc1_ss",
        "osc2_detune",
        "osc2_level",
        "osc2_noise",
        "osc2_ss",
        "fm_amount",
        "ring",
        "sub_level",
        "noise_level",
        "noise_layer",
        "ss_ratios",
        "fm_ratio",
        "fm_index",
        "wt_table",
        "wt_caps",
        "wt_pos",
    ];
    let mut held = std::collections::HashMap::new();
    for k in keys {
        if let Some(a) = arr(spec, k)? {
            held.insert(k, a);
        }
    }
    let slices: std::collections::HashMap<&str, &[f64]> = held
        .iter()
        .map(|(k, a)| Ok((*k, a.as_slice()?)))
        .collect::<PyResult<_>>()?;
    let get = |k: &str| slices.get(k).copied().unwrap_or(&[]);
    let param = |k: &str| -> PyResult<Option<dsp::osc::Param>> {
        match slices.get(k) {
            None => Ok(None),
            Some(s) => Ok(Some(series(k, s, n)?)),
        }
    };
    let required = |k: &str| -> PyResult<dsp::osc::Param> {
        param(k)?.ok_or_else(|| PyValueError::new_err(format!("stack: missing {k}")))
    };
    let shape = |k: &str| -> PyResult<Option<Shape>> {
        match text(spec, k)? {
            None => Ok(None),
            Some(s) => Shape::parse(&s)
                .map(Some)
                .ok_or_else(|| PyValueError::new_err(format!("stack: unknown shape {s}"))),
        }
    };
    let voices = get("offsets").len();
    let osc_of = |i: u8, sh: Shape| -> PyResult<Osc> {
        let p = format!("osc{i}_");
        let noise = get(&format!("{p}noise"));
        let ss = get(&format!("{p}ss"));
        if sh == Shape::Noise && noise.len() != voices * n {
            return Err(PyValueError::new_err("stack: one noise buffer per voice"));
        }
        if sh == Shape::Supersaw && ss.len() != voices * get("ss_ratios").len() {
            return Err(PyValueError::new_err("stack: supersaw phases per voice"));
        }
        Ok(Osc {
            shape: sh,
            octave: num(spec, &format!("{p}octave"))?,
            detune: required(&format!("{p}detune"))?,
            level: required(&format!("{p}level"))?,
            noise,
            ss_phases: ss,
        })
    };
    let sh1 = shape("osc1")?.ok_or_else(|| PyValueError::new_err("stack: missing osc1"))?;
    let osc2 = match shape("osc2")? {
        Some(sh) => Some(osc_of(2, sh)?),
        None => None,
    };
    let noise_level = param("noise_level")?;
    if noise_level.is_some() && get("noise_layer").len() != voices * n {
        return Err(PyValueError::new_err("stack: one noise layer per voice"));
    }
    if voices == 0 || get("gain_l").len() != voices || get("gain_r").len() != voices {
        return Err(PyValueError::new_err(
            "stack: one offset and pan gain per voice",
        ));
    }
    let uses_wt =
        sh1 == Shape::Wavetable || osc2.as_ref().is_some_and(|o| o.shape == Shape::Wavetable);
    let wavetable = if uses_wt {
        let frames: usize = spec
            .get_item("wt_frames")?
            .ok_or_else(|| PyValueError::new_err("stack: missing wt_frames"))?
            .extract()?;
        Some(wavetable_of(
            get("wt_table"),
            get("wt_caps"),
            frames,
            get("wt_pos"),
            n,
        )?)
    } else {
        None
    };
    let s = Stack {
        sr: num(spec, "sr")?,
        freq: required("freq")?,
        offsets: get("offsets"),
        gain_l: get("gain_l"),
        gain_r: get("gain_r"),
        unison_detune: required("unison_detune")?,
        width: required("width")?,
        osc1: osc_of(1, sh1)?,
        osc2,
        fm_amount: param("fm_amount")?,
        ring: param("ring")?,
        sub_level: param("sub_level")?,
        noise_level,
        noise_layer: get("noise_layer"),
        ss_ratios: get("ss_ratios"),
        fm_ratio: required("fm_ratio")?,
        fm_index: required("fm_index")?,
        fm_decay: num(spec, "fm_decay")?,
        wavetable,
    };
    let mut out = vec![0.0f64; 2 * n];
    py.detach(|| dsp::voice::stack(&s, &mut out));
    PyArray1::from_vec(py, out).reshape(vec![n, 2])
}

/// One stage of `post`, its arrays still borrowed from Python.
enum HeldStage<'py> {
    Dc(f64),
    Swept(
        dsp::svf::Kind,
        PyReadonlyArray1<'py, f64>,
        PyReadonlyArray1<'py, f64>,
        usize,
    ),
    Lfilter(PyReadonlyArray1<'py, f64>, PyReadonlyArray1<'py, f64>),
    Clip(PyReadonlyArray1<'py, f64>),
    Gain(PyReadonlyArray1<'py, f64>),
}

/// `Synth.note`'s post chain (`dsp::post::chain`): `x` is `(n,)` or
/// `(n, ch)`, `stages` a list of tuples built by `synthlab.synth` —
/// `("dc", r)`, `("svf", kind, fc, q, block)`, `("lfilter", b, a)`,
/// `("clip", drive)`, `("gain", gain)` — run in order.
#[pyfunction]
fn post<'py>(
    py: Python<'py>,
    x: PyReadonlyArrayDyn<'py, f64>,
    sr: f64,
    stages: Vec<Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyArrayDyn<f64>>> {
    use dsp::post::Stage;
    let shape = x.shape().to_vec();
    let ch = match shape.as_slice() {
        [_] => 1,
        [_, c] if *c >= 1 => *c,
        _ => return Err(PyValueError::new_err("x must be (n,) or (n, ch)")),
    };
    let n = shape[0];
    let mut held = Vec::with_capacity(stages.len());
    for st in &stages {
        let name: String = st.get_item(0)?.extract()?;
        held.push(match name.as_str() {
            "dc" => HeldStage::Dc(st.get_item(1)?.extract()?),
            "svf" => {
                let kind: String = st.get_item(1)?.extract()?;
                let kind = dsp::svf::Kind::parse(&kind)
                    .ok_or_else(|| PyValueError::new_err(format!("unknown filter kind: {kind}")))?;
                HeldStage::Swept(
                    kind,
                    st.get_item(2)?.extract()?,
                    st.get_item(3)?.extract()?,
                    st.get_item(4)?.extract()?,
                )
            }
            "lfilter" => HeldStage::Lfilter(st.get_item(1)?.extract()?, st.get_item(2)?.extract()?),
            "clip" => HeldStage::Clip(st.get_item(1)?.extract()?),
            "gain" => HeldStage::Gain(st.get_item(1)?.extract()?),
            other => {
                return Err(PyValueError::new_err(format!(
                    "post: unknown stage {other}"
                )));
            }
        });
    }
    let mut chain = Vec::with_capacity(held.len());
    for h in &held {
        chain.push(match h {
            HeldStage::Dc(r) => Stage::Dc(*r),
            HeldStage::Swept(kind, fc, q, block) => Stage::Swept {
                kind: *kind,
                fc: fc.as_slice()?,
                q: q.as_slice()?,
                block: *block,
            },
            HeldStage::Lfilter(b, a) => Stage::Lfilter {
                b: b.as_slice()?,
                a: a.as_slice()?,
            },
            HeldStage::Clip(d) => {
                let d = d.as_slice()?;
                if d.is_empty() {
                    return Err(PyValueError::new_err("drive must be 1 or n long"));
                }
                // one value is a constant drive, even on a one-frame note
                Stage::Clip(dsp::osc::Param::from_slice(d))
            }
            HeldStage::Gain(g) => Stage::Gain(g.as_slice()?),
        });
    }
    dsp::post::check(&chain, n).map_err(|e| PyValueError::new_err(format!("post: {e}")))?;
    let xs = x.as_slice()?;
    let out = py.detach(|| dsp::post::chain(xs, ch, sr, &chain));
    PyArray1::from_vec(py, out).reshape(shape)
}

/// What this build is: target arch and the SIMD it was compiled for (the
/// x86 builds target x86-64-v3 — crates/.cargo/config.toml).
#[pyfunction]
fn build_info() -> std::collections::HashMap<&'static str, String> {
    let simd = if cfg!(target_feature = "avx2") {
        "avx2"
    } else if cfg!(target_feature = "neon") {
        "neon"
    } else if cfg!(target_feature = "sse2") {
        "sse2"
    } else {
        "none"
    };
    std::collections::HashMap::from([
        ("arch", std::env::consts::ARCH.to_string()),
        ("simd", simd.to_string()),
        ("fma", cfg!(target_feature = "fma").to_string()),
        ("debug", cfg!(debug_assertions).to_string()),
        // the crates' source hash tools/build_wheels.py stamps a build with
        // ("dev" for a plain cargo/maturin build); tests/test_native.py
        // compares it with the sources on disk
        (
            "source",
            option_env!("SYNTHLAB_DSP_SOURCE")
                .unwrap_or("dev")
                .to_string(),
        ),
    ])
}

#[pymodule]
fn synthlab_dsp(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(swept_svf, m)?)?;
    m.add_function(wrap_pyfunction!(osc, m)?)?;
    m.add_function(wrap_pyfunction!(feedback_delay, m)?)?;
    m.add_function(wrap_pyfunction!(swept_read, m)?)?;
    m.add_function(wrap_pyfunction!(soft_clip, m)?)?;
    m.add_function(wrap_pyfunction!(build_info, m)?)?;
    m.add_function(wrap_pyfunction!(supersaw, m)?)?;
    m.add_function(wrap_pyfunction!(fm, m)?)?;
    m.add_function(wrap_pyfunction!(dc_block, m)?)?;
    m.add_function(wrap_pyfunction!(adsr, m)?)?;
    m.add_function(wrap_pyfunction!(lfilter, m)?)?;
    m.add_function(wrap_pyfunction!(allpass_sweep, m)?)?;
    m.add_function(wrap_pyfunction!(stack, m)?)?;
    m.add_function(wrap_pyfunction!(post, m)?)?;
    m.add_function(wrap_pyfunction!(wavetable, m)?)?;
    m.add_function(wrap_pyfunction!(read_back, m)?)?;
    m.add_function(wrap_pyfunction!(tape_echo, m)?)?;
    m.add_function(wrap_pyfunction!(scan, m)?)?;
    m.add_function(wrap_pyfunction!(rotor, m)?)?;
    m.add_function(wrap_pyfunction!(sines, m)?)?;
    Ok(())
}
