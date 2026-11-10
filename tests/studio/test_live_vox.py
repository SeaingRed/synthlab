"""The live vocal chain's pitched units under node (vox-worklet.js, in
fx-worklet.js's scope) against effects.py on what they are for: a held note
sung off its note, a glide through a key, a voice with vibrato and
syllables. `test_live_fx.py` runs every unit on a synth note; here the
tracker's every decision is exercised — voiced and unvoiced grains, the
hold between two notes, the glide — and the live twin must make the same
ones, sample for sample (float32 rounding: measured <= 3.4e-7 of peak)."""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest

from synthlab import effects as fx
from synthlab.core import SR
from synthlab.studio import fxlive, voicetables
from synthlab.studio.fxrack import build_chain
from synthlab.studio.state import Arrangement
from tests.studio.test_live_fx import EQW, FXW, HARNESS, VOICE, node
from tests.test_effects_vocal import saw

ARR = Arrangement(bpm=120.0, root="A1", scale_name="minor")


def _inputs() -> dict[str, np.ndarray]:
    n = int(1.5 * SR)
    t = np.arange(n) / SR
    voice = saw(196 * 2 ** (0.4 * np.sin(2 * np.pi * 5.5 * t) / 12), n)
    voice *= (t * 2.5) % 1.0 < 0.75  # syllables: silence between
    voice += 0.003 * np.random.default_rng(7).standard_normal(n)  # breath
    return {
        "detuned": fx.to_stereo(saw(440 * 2 ** (38 / 1200), n)),
        "glide": fx.to_stereo(saw(np.geomspace(330, 520, n), n)),
        "voice": np.column_stack([voice, 0.9 * voice]),
    }


CASES = {
    "autotune_hard": [{"kind": "autotune", "params": {"speed": 0.0}}],
    "autotune_slow": [
        {"kind": "autotune", "params": {"speed": 0.7, "humanize": 0.2, "mix": 0.9}}
    ],
    "harmony_two": [
        {
            "kind": "harmony",
            "params": {"first": 0.0, "second": 4.0, "second_level": 0.6},
        }
    ],
    "vocal_chain": [
        {"kind": "vgate", "params": {"threshold": -40.0}},
        {"kind": "vox_comp", "params": {"threshold": -24.0, "ratio": 4.0}},
        {"kind": "autotune", "params": {"speed": 0.1}},
        {"kind": "deess", "params": {}},
    ],
}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("livevox")
    jobs, want = [], {}
    for src, x in _inputs().items():
        x32 = x.astype(np.float32).astype(np.float64)
        path = tmp / f"{src}.f32"
        path.write_bytes(np.concatenate([x32[:, 0], x32[:, 1]]).astype("<f4").tobytes())
        for name, units in CASES.items():
            out = tmp / f"{src}-{name}.f32"
            jobs.append(
                {
                    "chains": [fxlive.live_chain(units, ARR, drums=False)],
                    "input": str(path),
                    "total": len(x),
                    "out": str(out),
                }
            )
            want[(src, name)] = (fx.chain(x32, build_chain(units, ARR)), out)
    (tmp / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    (tmp / "h.cjs").write_text(HARNESS, encoding="utf-8")
    (tmp / "t.bin").write_bytes(voicetables.payload())
    cmd = ["node", str(tmp / "h.cjs"), str(VOICE), str(FXW), str(tmp / "jobs.json")]
    subprocess.run([*cmd, str(tmp / "t.bin"), str(EQW)], check=True, timeout=600)
    got = {}
    for key, (eng, path) in want.items():
        raw = np.fromfile(path, dtype="<f4").astype(np.float64)
        got[key] = (eng, np.column_stack([raw[: len(raw) // 2], raw[len(raw) // 2 :]]))
    return got


@node
@pytest.mark.parametrize("src", ["detuned", "glide", "voice"])
@pytest.mark.parametrize("name", list(CASES))
def test_the_live_vocal_units_are_the_engines(runs, src, name):
    eng, live = runs[(src, name)]
    err = np.max(np.abs(live - eng)) / (np.max(np.abs(eng)) + 1e-12)
    assert err < 1e-5, (src, name, err)
