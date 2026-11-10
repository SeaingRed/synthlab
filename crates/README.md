# crates/ — synthlab's native DSP

The hot per-sample loops of the engine, in Rust. Python imports them as
`synthlab_dsp`; `synthlab/_native.py` hands each one to the engine, which
falls back to its own NumPy version when a kernel is missing.

| crate | what it is |
|---|---|
| `dsp` | the kernels: plain Rust, slices in and out, no Python. Kept free of PyO3 so the same code can build to `wasm32`. |
| `dsp-py` | the PyO3 binding, built by maturin into the `synthlab_dsp` extension. Every call releases the GIL. |

## Kernels

| kernel | NumPy twin (the reference) | speedup |
|---|---|---|
| `swept_svf` | `core._swept_biquad_numpy` | ~30x (the per-sample recursion instead of FFT blocks + a prefix scan) |
| `osc` | `core.sine/saw/square/triangle_numpy` | sine 7x, saw 6x, square 5x, triangle 2.5x |
| `supersaw` | `core.supersaw_numpy` | 2.7x, bit-identical (the seeded phases and detune ratios still come from Python) |
| `fm` | `core.fm_numpy` | 3x |
| `wavetable` | `core.wavetable_numpy` | 7x, bit-identical (the mip stack comes in from Python: `core._wt_native`) |
| `dc_block` | `core.dc_block_numpy` | bit-identical to scipy's `lfilter`; a stereo voice's two channels in one pass |
| `stack` | `Synth.note`'s per-voice loop (`_osc_stack` + pan) | the whole unison stack in one call; runs 106 of the 114 sounds and any wavetable one (pluck/sampler keep the loop) |
| `post` | `Synth.note`'s stages after the stack, called one by one (`dc_block`, `ladder`/`biquad`, the amp envelope, `soft_clip`) | a note 17% faster (2.16 -> 1.78 ms), bit-identical to the loose calls |
| `adsr` | `core.adsr_numpy` | 5x (sqrt for the default curve); takes the release's sample count (`rel_n`): a gated release lasts that long, fitted into the buffer when it ends first |
| `lfilter` | scipy's `lfilter` (`core._lfilter`, order <= 2) | ~5x, bit-identical: biquads, shelves, one-poles, pink/brown noise, the limiter's smoother |
| `allpass_sweep` | `effects._allpass_sweep_numpy` (the phaser's cascade: scipy once a block a stage) | ~5x on a phaser, bit-identical; a 283 s song with two phasers rendered in 10.5 s instead of 16.4 |
| `feedback_delay` | `effects._delay_line_numpy` | exact; also the comb (scipy ran its all-pole filter in O(n x delay)) |
| `swept_read` | `effects._mod_delay_numpy` | 7x: chorus/flanger read |
| `soft_clip` | `core.soft_clip_numpy` | 2.5x (also the ladder's saturation stage) |
| `scan` | `core.scan_numpy` | one pass for finite-ness, peak and sum of squares (16x: 0.9 vs 14.7 ms on a 39 s stereo section), and with `q` the frames' peaks' percentile — `np.percentile` bit for bit (8x: 6.3 vs 52 ms); the render's NaN checks, the meter's levels, `song_play.levels` |
| `read_back` | `effects._read_back_numpy` | bit-identical: the booth's moving reads (ADT double, the fx2 tape) |
| `tape_echo` | `effects._tape_echo_taps_numpy` | the tape echo's loop, block by block as the twin (3x on the unit, 5x with a feedback lane) |
| `rotor` | `effects._rotor_numpy` | a rotary speaker's rotor: phase sum, sines and Doppler reads in one call (the unit 3.6x) |
| `sines` | `effects._sines_numpy` | sums of sines of time: the booth's `wander` and the tape heads' flutter |
| `build_info` | — | arch and SIMD of this build: `synthlab_dsp.build_info()` |

`tests/test_native.py` holds each kernel to its twin: saw, square,
triangle, supersaw, wavetable, `dc_block`, the feedback delay,
`read_back` and `scan`'s peak and percentile are bit-identical; the SVF,
sine, FM, the swept read, `soft_clip`, `sines`, `rotor`, the tape echo
(its tanh) and `scan`'s sum of squares agree to rounding.

`post` is a chain of the kernels above, not new arithmetic: Python
decides each stage exactly where its twin decides it (`core._biquad_plan`
is `biquad`'s static-or-swept choice, `_ladder_stages`, `_clip_stages`)
and hands the kernel a list of `("dc", r)`, `("svf", kind, fc, q, block)`,
`("lfilter", b, a)`, `("clip", drive)`, `("gain", g)`. So it is
bit-identical to the loose calls (`tests/test_native_voice.py`, every
sound) and to rounding against the NumPy twins. What it saves is what
sat between the stages — above all NumPy's `mix *= env[:, None]`, a
broadcast over a length-2 axis that cost 250 us a note, as much as a
filter stage — plus one trick: a ladder's two filters are each bound by
their own recursion (a sample waits on the last one's state), but the
second only waits on the first one's *output*, so `svf::ladder_stereo`
walks the first a block ahead of the second in one loop and the two
recursions overlap in the CPU (481 -> 362 us), with the saturation still
a vectorized pass per block between them. The vowel filter stays in
Python (the chain is called either side of it), and switching off any
kernel the chain is made of (`SYNTHLAB_NATIVE_OFF=swept_svf`) switches
the chain off with it, so that stage really runs on its twin.

`dsp::math` has `exp`, `tanh` and `sin_turns` (sin 2πt, the phase in
turns) built from plain arithmetic, within a few ulp of libm: the
platform's are opaque calls a loop can't vectorize, and each platform
rounds their last bit differently, whereas `+ - * /`,
`floor` and bit casts round identically on x86, ARM and wasm32. Rust never
fuses `a * b + c` into an FMA on its own, so an x86-64-v3 build and a NEON
build compute the same bits. Keep new kernels on `dsp::math` (not
`f64::exp`/`tanh`/`sin`) wherever the result feeds audio. Still on libm,
so not yet bit-identical across machines: the SVF's per-block `tan` — a
`dsp::math` version comes with a wasm32 build, where it starts to
matter.

A loop that carries state from sample to sample (a phase sum, a filter)
never vectorizes, and it keeps everything else in its body scalar too.
Split the serial part into its own pass (`osc::phases`) and put `sin`,
`exp` and the like in a second pass over the buffer: the sine oscillator
went from 1x to 7x on that alone. Inside such a pass, avoid a per-sample
branch on a `Param` and `usize as f64` (AVX2 has no packed u64 -> f64;
go through `i32`).

The x86 builds target x86-64-v3 (AVX2), set in `crates/.cargo/config.toml`;
ARM64's baseline already has NEON.

## Running vs. building

**Running needs no Rust.** `crates/wheels/` holds prebuilt wheels —
`win_amd64` for x86-64, `win_arm64` for Windows on ARM — and
`[tool.uv.sources]` in pyproject.toml installs the one for the machine's
arch, so a plain `uv sync --all-extras` is all any machine needs. Other
platforms fall back to building `crates/dsp-py` from source.

**Changing the Rust** needs the toolchain, on the machine doing it (an
x86-64 one builds both wheels):

```powershell
winget install Rustlang.Rustup
winget install Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Tools.ARM64 --includeRecommended"
rustup default stable-x86_64-pc-windows-msvc
rustup target add aarch64-pc-windows-msvc wasm32-unknown-unknown
```

Not the GNU toolchain (Chocolatey's `rust` package is
`x86_64-pc-windows-gnu`). The ARM64 build tools component is what lets
an x86 machine cross-compile the ARM64 wheel.

While iterating, install just this machine's build (stamped "dev") and
keep uv from syncing the committed wheel back over it:

```powershell
uv run --no-sync python tools/build_wheels.py --dev
uv run --no-sync pytest -q tests/test_native.py tests/test_native_voice.py
```

When done, after editing anything under `crates/`:

```powershell
uv run python tools/build_wheels.py   # both wheels, stamped; re-locks and reinstalls
git add crates uv.lock                # the wheels, SOURCE and the new lock hashes
# (uv won't re-read a rebuilt local wheel by itself: the tool runs
#  `uv lock --refresh-package synthlab-dsp` so the lock gets the new hashes)
```

Each build is stamped with a hash of the Rust sources (`crates/wheels/
SOURCE`, and `synthlab_dsp.build_info()["source"]` in the module).
`tests/test_native.py` fails when the committed wheels, the lock or the
installed module no longer match the sources. The wheels are `abi3-py313`:
one wheel serves a 3.13 venv (ARM64's native interpreter) and a 3.14 one.

Rust-only checks: `cargo test`, `cargo clippy` and `cargo fmt` from
`crates/`.

## Switching it off

`SYNTHLAB_NATIVE=0` runs every kernel on its NumPy twin: A/B listening,
bisecting a difference. `SYNTHLAB_NATIVE_OFF=stack,adsr` switches off just
the named kernels. (The studio warns once if the module is missing
at all, rather than quietly running slow.)
