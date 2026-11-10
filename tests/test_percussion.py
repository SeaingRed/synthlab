"""The percussion one-shots (`synthlab/percussion.py`): the
hand and metal sounds the drum machine's eight voices lack. Each is
pinned to what it is named for by where its energy sits."""

from __future__ import annotations

import numpy as np
import pytest

from synthlab import percussion
from synthlab.core import SR


def _centroid(x: np.ndarray) -> float:
    m = x[:, 0]
    p = np.abs(np.fft.rfft(m)) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return float((p * f).sum() / p.sum())


@pytest.mark.parametrize("kind", percussion.KINDS)
def test_a_one_shot_is_stereo_peaks_at_minus_one_and_ends_in_silence(kind):
    a = percussion.generate(kind)
    assert a.ndim == 2 and a.shape[1] == 2 and np.all(np.isfinite(a))
    assert np.max(np.abs(a)) == pytest.approx(10 ** (-1 / 20), rel=1e-6)
    assert a[0, 0] == 0.0 and abs(a[-1, 0]) < 1e-9
    assert abs(float(a.mean())) < 1e-3
    np.testing.assert_array_equal(a, percussion.generate(kind))  # seeded


@pytest.mark.parametrize(
    "kind, lo, hi",
    [
        ("conga", 120.0, 400.0),  # a tuned skin near 200 Hz
        ("cowbell", 500.0, 1500.0),  # the 545 / 815 Hz pair
        ("clank", 1000.0, 3500.0),  # inharmonic metal partials
        ("tambourine", 5000.0, 10000.0),  # jingles
        ("shaker", 8000.0, 20000.0),  # high noise
    ],
)
def test_each_sits_where_it_is_named_for(kind, lo, hi):
    assert lo < _centroid(percussion.generate(kind)) < hi


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        percussion.generate("gong")
