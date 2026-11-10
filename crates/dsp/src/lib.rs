//! synthlab's DSP kernels as plain Rust: slices in, slices out, no Python
//! and no allocation in the per-sample loops. Every kernel here has a NumPy
//! twin in `synthlab/` that stays the reference; the tests in
//! `tests/test_native.py` hold the two together.

pub mod delay;
pub mod env;
pub mod filter;
pub mod math;
pub mod osc;
pub mod post;
pub mod scan;
pub mod shape;
pub mod svf;
pub mod voice;
