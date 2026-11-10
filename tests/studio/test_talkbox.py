"""The live talkbox's vocoder (`static/talk-worklet.js`, `TalkDSP`), run
under node: the browser's own file, with no AudioWorklet in scope it only
publishes the DSP. The mic decides WHERE the keys are heard (each band's
gain is the mic's envelope there); the keys decide what is heard."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

TALK = Path("synthlab/studio/static/talk-worklet.js")
SR = 44100

HARNESS = """
const fs = require("fs");
eval(fs.readFileSync(process.argv[2], "utf8"));
const job = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const dsp = new globalThis.TalkDSP(job.sr, job.opts);
const f32 = (a) => (a ? Float32Array.from(a) : null);
const n = job.carL.length, L = new Float32Array(n), R = new Float32Array(n);
dsp.run(f32(job.carL), f32(job.carR), f32(job.mic), L, R);
process.stdout.write(JSON.stringify({ L: Array.from(L), R: Array.from(R) }));
"""

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node runs talk-worklet.js's own code"
)


def run(tmp_path, carL, mic, carR=None, **opts):
    (tmp_path / "h.js").write_text(HARNESS, encoding="utf-8")
    job = {
        "sr": SR,
        "opts": opts,
        "carL": list(map(float, carL)),
        "carR": list(map(float, carR)) if carR is not None else None,
        "mic": list(map(float, mic)) if mic is not None else None,
    }
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    out = subprocess.run(
        ["node", str(tmp_path / "h.js"), str(TALK), str(tmp_path / "job.json")],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    d = json.loads(out.stdout)
    return np.array(d["L"]), np.array(d["R"])


T = np.arange(int(0.5 * SR)) / SR
SAW = 2 * ((110 * T) % 1.0) - 1  # a 110 Hz saw: harmonics everywhere


def _band_rms(x, lo, hi):
    tail = x[len(x) // 2 :]
    p = np.abs(np.fft.rfft(tail)) ** 2
    f = np.fft.rfftfreq(len(tail), 1 / SR)
    return float(np.sqrt(p[(f >= lo) & (f < hi)].sum()))


def test_no_voice_no_sound(tmp_path):
    left, _ = run(tmp_path, SAW, np.zeros_like(SAW))
    assert np.max(np.abs(left)) < 1e-6


def test_the_keys_are_heard_where_the_voice_has_energy(tmp_path):
    """A mic tone at 1 kHz opens the 1 kHz bands: the saw's harmonics
    there come through, the ones at 300 Hz and 4 kHz stay shut."""
    mic = 0.5 * np.sin(2 * np.pi * 1000 * T)
    left, _ = run(tmp_path, SAW, mic, air=0.0)
    near = _band_rms(left, 800, 1250)
    assert near > 10 * _band_rms(left, 250, 400)  # 20 dB (25 measured)
    assert near > 10 * _band_rms(left, 3500, 5000)


def test_air_lets_the_consonants_through_and_mix_brings_the_keys_back(tmp_path):
    rng = np.random.default_rng(1)
    hiss = 0.3 * rng.standard_normal(len(T))  # an "s"
    dry, _ = run(tmp_path, np.zeros_like(SAW), hiss, air=0.0)
    wet, _ = run(tmp_path, np.zeros_like(SAW), hiss, air=1.0)
    assert np.max(np.abs(dry)) < 1e-6 and _band_rms(wet, 6000, 12000) > 1.0
    blend, _ = run(tmp_path, SAW, np.zeros_like(SAW), mix=1.0)
    np.testing.assert_allclose(blend, SAW, atol=1e-6)  # float32 of the saw


def test_the_carriers_two_channels_stay_two(tmp_path):
    mic = 0.5 * np.sin(2 * np.pi * 600 * T)
    right = 2 * ((165 * T) % 1.0) - 1
    left, r = run(tmp_path, SAW, mic, carR=right, air=0.0)
    assert not np.allclose(left, r) and np.max(np.abs(r)) > 0.01


def test_the_robot_talks_with_no_keys_and_the_whisper_too(tmp_path):
    """The robot (its own saw at `note`) and the whisper (noise) carry the
    voice with no key held; the keys alone carry nothing then."""
    mic = 0.5 * np.sin(2 * np.pi * 1000 * T)
    silent = np.zeros_like(SAW)
    keys, _ = run(tmp_path, silent, mic, air=0.0)
    robot, _ = run(tmp_path, silent, mic, air=0.0, carrier="robot", note=48)
    whisper, _ = run(tmp_path, silent, mic, air=0.0, carrier="noise")
    assert np.max(np.abs(keys)) < 1e-6
    assert _band_rms(robot, 800, 1250) > 1.0 and _band_rms(whisper, 800, 1250) > 1.0
    # the robot's saw at C3 (130.8 Hz): its 1 kHz energy sits on harmonics
    tail = robot[len(robot) // 2 :]
    p = np.abs(np.fft.rfft(tail)) ** 2
    f = np.fft.rfftfreq(len(tail), 1 / SR)
    band = (f > 800) & (f < 1250)
    peak = f[band][np.argmax(p[band])]
    assert abs(peak / 130.81 - round(peak / 130.81)) < 0.02


def test_level_scales_it_and_the_bands_and_formants_are_laid_again(tmp_path):
    mic = 0.5 * np.sin(2 * np.pi * 1000 * T)
    one, _ = run(tmp_path, SAW, mic, air=0.0)
    two, _ = run(tmp_path, SAW, mic, air=0.0, level=2.0)
    np.testing.assert_allclose(two, 2 * one, rtol=1e-5, atol=1e-6)
    few, _ = run(tmp_path, SAW, mic, air=0.0, bands=8)
    up, _ = run(tmp_path, SAW, mic, air=0.0, shift=12)
    assert not np.allclose(few, one, atol=1e-3)
    # the carrier's bands an octave up: the 1 kHz voice now opens the
    # keys' 2 kHz, not their 1 kHz
    assert _band_rms(up, 1700, 2500) > 2 * _band_rms(up, 800, 1250)


# --- the mic's pick-up (a whisper must still be picked up): gain, auto
# level, gate ---------------------------------------------------------------


def _rms(x):
    tail = x[len(x) // 2 :]
    return float(np.sqrt(np.mean(tail**2)))


VOICE = np.sin(2 * np.pi * 700 * T)  # a vowel-ish tone the bands hear


def test_the_pick_up_at_its_defaults_is_the_old_vocoder_sample_for_sample(tmp_path):
    old = run(tmp_path, SAW, 0.05 * VOICE)
    new = run(tmp_path, SAW, 0.05 * VOICE, gain=0, auto=0, gate=-90)
    assert np.array_equal(old[0], new[0]) and np.array_equal(old[1], new[1])


def test_mic_gain_lifts_a_whisper(tmp_path):
    quiet = _rms(run(tmp_path, SAW, 0.003 * VOICE)[0])
    lifted = _rms(run(tmp_path, SAW, 0.003 * VOICE, gain=24)[0])
    assert quiet > 0 and 14 < lifted / quiet < 18  # +24 dB is x15.8


def test_auto_level_brings_a_whisper_near_a_voice(tmp_path):
    def gap(**o):
        loud = _rms(run(tmp_path, SAW, 0.2 * VOICE, **o)[0])
        soft = _rms(run(tmp_path, SAW, 0.004 * VOICE, **o)[0])
        return loud / soft

    assert gap() > 40  # 34 dB apart, as they went in
    assert gap(auto=1) < 4  # the whisper lifted up to +24 dB; the voice never cut


def test_the_gate_keeps_a_boosted_rooms_hiss_silent_and_lets_the_voice_through(
    tmp_path,
):
    rng = np.random.default_rng(3)
    hiss = 0.0005 * rng.standard_normal(len(T))
    open_ = _rms(run(tmp_path, SAW, hiss, gain=24)[0])
    gated = _rms(run(tmp_path, SAW, hiss, gain=24, gate=-34)[0])
    assert open_ > 0 and gated < 0.01 * open_
    voice = _rms(run(tmp_path, SAW, 0.01 * VOICE, gain=24, gate=-34)[0])
    free = _rms(run(tmp_path, SAW, 0.01 * VOICE, gain=24)[0])
    assert voice > 0.9 * free
