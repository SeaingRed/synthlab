"""The breed distance's fixed scaler (explore/scale.py) must match what
the library measures now. When the engine moves what patches render to,
this fails: regenerate with `uv run python tools/refresh_breed_scale.py`."""

import importlib.util
from pathlib import Path

from synthlab.explore.scale import BREED_SCALE, BREED_VECTOR

TOOL = Path(__file__).resolve().parents[1] / "tools" / "refresh_breed_scale.py"


def _tool():
    spec = importlib.util.spec_from_file_location("refresh_breed_scale", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scale_table_covers_the_vector():
    assert tuple(BREED_SCALE) == BREED_VECTOR
    assert all(std > 0 for _, std in BREED_SCALE.values())


def test_scale_table_matches_the_library():
    measured = _tool().measure_library()
    for name, (mean, std) in BREED_SCALE.items():
        m, s = measured[name]
        assert abs(m - mean) <= 0.1 * std, (name, m, mean)
        assert 0.9 <= s / std <= 1.1, (name, s, std)
