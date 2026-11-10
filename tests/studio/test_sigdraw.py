"""The one signal graphic (studio/sigdraw.py): a spark, a spectrum or an
energy shape is ONE path — a smooth area whose outline leaves its box below
and beside it, so a stroke on it is only seen along the top."""

from __future__ import annotations

import re

from synthlab.studio.sigdraw import sig_box, sig_path, smooth_path


def _numbers(d: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d*\.?\d+", d)]


def test_a_spectrum_is_one_closed_path_in_whole_units():
    d = sig_path([0.1, 0.9, 0.5, 0.2])
    assert sig_box([0.1, 0.9, 0.5, 0.2]) == "0 0 8 100"
    assert d.startswith("M-1 101V") and d.endswith("V101Z")  # out below and beside
    assert d.count("M") == 1 and "." not in d  # one path, whole numbers
    # inside the box but for the closing run (a full-scale band sits at 8 of
    # 100, below: its stroke is never clipped)
    assert min(_numbers(d)) >= -1
    assert d == "M-1 101V91L2 54Q3 17 4 36 5 54 6 68L9 82V101Z"


def test_nothing_measured_draws_a_floor_never_fails():
    for values in ([], None, [float("nan")], ["x"], [2.0, -1.0]):
        d = sig_path(values)
        assert d.startswith("M-1 101V") and d.endswith("Z"), values
    assert sig_path([1.0]) == "M-1 101V8L3 8V101Z"


def test_an_energy_shape_takes_its_own_x():
    d = smooth_path([(1.6, 0.2), (6.4, 0.2), (9.6, 0.8), (14.4, 0.8)], 16)
    assert d.startswith("M-1 11V8.2") and "L17 2.6V11Z" in d
