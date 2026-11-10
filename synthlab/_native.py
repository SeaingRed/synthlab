"""The native DSP kernels (`synthlab_dsp`, built from crates/ by maturin).

Engine code asks `kernel("name")` for a kernel and falls back to its own
NumPy version when it gets None. The NumPy versions stay the reference: the
tests in tests/test_native.py hold each kernel to its twin, and
`SYNTHLAB_NATIVE=0` switches every kernel off (A/B listening, bisecting a
difference, or a machine with no Rust toolchain yet).

A missing module is not silent: a studio that quietly lost its native
kernels would just feel slow, so the first lookup warns once.
"""

from __future__ import annotations

import os
import warnings

try:
    import synthlab_dsp as _dsp
except ImportError as err:  # not built on this machine
    _dsp = None
    _import_error = str(err)
else:
    _import_error = ""

ENABLED = _dsp is not None and os.environ.get("SYNTHLAB_NATIVE", "1") != "0"
# SYNTHLAB_NATIVE_OFF=stack,adsr: just those kernels on their NumPy twins —
# bisecting which one moved a render
OFF = frozenset(
    k.strip() for k in os.environ.get("SYNTHLAB_NATIVE_OFF", "").split(",") if k.strip()
)
_warned = False


def kernel(name: str):
    """The native kernel `name`, or None to use the NumPy path."""
    global _warned
    if not ENABLED:
        if _dsp is None and not _warned and os.environ.get("SYNTHLAB_NATIVE") != "0":
            _warned = True
            warnings.warn(
                "synthlab_dsp is not built, so the engine runs its slower "
                f"NumPy kernels ({_import_error}). Install a Rust toolchain "
                "and run `uv sync --all-extras` — see crates/README.md.",
                RuntimeWarning,
                stacklevel=3,
            )
        return None
    if name in OFF:
        return None
    return getattr(_dsp, name, None)
