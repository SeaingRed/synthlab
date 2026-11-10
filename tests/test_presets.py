import numpy as np
import pytest

from synthlab import PRESETS, Synth
from synthlab.core import SR, n_samples


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_preset_renders_clean(name):
    patch = PRESETS[name]
    duration = 1.0 + max(0.0, patch.release)
    audio = Synth(patch).note("C3", duration)
    assert audio.shape == (n_samples(duration, SR), 2)
    assert np.all(np.isfinite(audio))
    rms = float(np.sqrt(np.mean(audio**2)))
    assert rms > 1e-4, "preset rendered near-silence"
    assert float(np.max(np.abs(audio))) < 20.0, "preset exploded"


def test_random_patch_is_deterministic_and_renders():
    from synthlab import Patch

    a = Patch.random(seed=7)
    b = Patch.random(seed=7)
    assert a == b
    audio = Synth(a).note("A2", 0.5)
    assert np.all(np.isfinite(audio))
