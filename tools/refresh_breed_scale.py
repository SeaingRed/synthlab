"""Regenerate synthlab/explore/scale.py: the breed distance's fixed scaler.

    uv run python tools/refresh_breed_scale.py

Probes every library sound at the breeder's listening note
(synthlab/explore/probe.py) and writes each breed measure's mean and
standard deviation. Run it after an engine change that moves what patches
render to (tests/test_breed_scale.py says when), or after changing the
probe or the measures themselves.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from synthlab.explore.probe import probe, raw_vector  # noqa: E402
from synthlab.explore.scale import BREED_VECTOR  # noqa: E402
from synthlab.studio.library import LIBRARY  # noqa: E402
from synthlab.synth import Patch  # noqa: E402

OUT = ROOT / "synthlab" / "explore" / "scale.py"

# a measure the library barely varies on would be blown up by 1/std; floor
# each spread so one freak value can't dominate the distance
_MIN_STD = {"sub_20_60": 0.01, "movement": 0.01}


def measure_library() -> dict[str, tuple[float, float]]:
    rows = []
    for entry in LIBRARY:
        pr = probe(Patch.from_dict(entry["patch"]))
        if pr is not None:
            rows.append(raw_vector(pr.features, pr.texture))
    v = np.array(rows)
    mean, std = v.mean(axis=0), v.std(axis=0)
    return {
        name: (float(mean[i]), max(float(std[i]), _MIN_STD.get(name, 1e-3)))
        for i, name in enumerate(BREED_VECTOR)
    }


def render_module(scale: dict[str, tuple[float, float]]) -> str:
    head, _, _ = OUT.read_text(encoding="utf-8").partition("BREED_SCALE")
    rows = "".join(f'    "{k}": ({m:.6g}, {s:.6g}),\n' for k, (m, s) in scale.items())
    return head + "BREED_SCALE: dict[str, tuple[float, float]] = {\n" + rows + "}\n"


if __name__ == "__main__":
    scale = measure_library()
    OUT.write_text(render_module(scale), encoding="utf-8")
    for k, (m, s) in scale.items():
        print(f"{k:14s} mean {m:10.4f}  std {s:8.4f}")
