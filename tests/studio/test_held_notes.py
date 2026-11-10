"""A held WAV-fallback key must not click.

Hold mode sustains a key the worklet can't play by looping a slice of its
rendered `/note/` WAV (`keys.js` `playKey`; loop points: `holdloop.py`).
The one-shot render leaves ~0.3 s of sustain, so the loop wrapped ~3 times
a second, and a sound whose sustain keeps moving never meets itself there:
"Velvet Line"
(3 unison voices at 17.8 cents, whose beating swings the level ~10 dB)
jumped 4.5 dB at every wrap against 1.3 dB of natural change — heard as a
rhythmic click while holding and all through the release fade.

`?hold=1` renders a separate buffer with `LIVE_HOLD_WINDOW` seconds of
sustain, so the loop wraps ~10x less often; the one-shot URL is untouched.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab.audio import read_pcm16
from synthlab.core import SR
from synthlab.studio.server import create_app
from synthlab.studio.soundmeta import LIVE_HOLD_WINDOW, note_duration
from synthlab.synth import Patch, Synth

ISLAND = Path("synthlab/studio/static/keys.js")
PIANO = Path("synthlab/studio/static/piano.js")

# the sound that exposed it in a live session
VELVET = replace(
    Patch(),
    osc1="sine", osc1_level=0.8, osc2="saw", osc2_level=0.5, osc2_detune=2.08,
    noise_level=0.003, filter_type="ladder", cutoff=1075.0, resonance=0.007,
    filter_env=472.0, filter_attack=0.815, filter_decay=0.25,
    filter_sustain=0.3, filter_release=0.2, key_track=0.35,
    attack=0.906, decay=1.381, sustain=0.969, release=1.862,
    unison=3, unison_detune=17.78, unison_spread=0.72,
)  # fmt: skip


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _loop(response) -> np.ndarray:
    """The span keys.js loops: the WAV between the route's own X-Loop
    points (its seam crossfade is already baked in server-side)."""
    buf, sr = read_pcm16(response.content)
    start, end, _gate = (float(v) for v in response.headers["x-loop"].split(","))
    return buf[round(start * sr) : round(end * sr)]


def _rms(x):
    return float(np.sqrt(np.mean(x**2)))


def test_one_shot_note_is_unchanged(tmp_path):
    client = make_client(tmp_path)
    client.app.state.studio.patch = VELVET
    got, sr = read_pcm16(client.get("/note/C4.wav").content)
    assert len(got) == len(Synth(VELVET).note("C4", note_duration(VELVET)))


def test_hold_render_carries_a_long_sustain_window(tmp_path):
    client = make_client(tmp_path)
    client.app.state.studio.patch = VELVET
    one, sr = read_pcm16(client.get("/note/C4.wav").content)
    held, _ = read_pcm16(client.get("/note/C4.wav?hold=1").content)
    assert (len(held) - len(one)) / sr >= LIVE_HOLD_WINDOW - 0.01
    assert client.get("/note/C4.wav?hold=2").status_code == 422


def test_held_unison_pad_wraps_rarely_and_without_a_level_jump(tmp_path):
    client = make_client(tmp_path)
    client.app.state.studio.patch = VELVET
    seg, sr = _loop(client.get("/note/C4.wav?hold=1")), SR
    assert len(seg) / sr >= 2.5  # was 0.32 s: ~3 wraps a second
    y = np.concatenate([seg] * 3)[:, 0]
    w, at = int(0.03 * sr), len(seg)
    step = abs(20 * np.log10(_rms(y[at : at + w]) / _rms(y[at - w : at])))
    assert step < 2.5  # was 4.5 dB; the sound's own 30 ms movement is ~1.3


def test_hold_voices_fetch_the_hold_render():
    audio = ISLAND.read_text(encoding="utf-8")
    assert "hold: 1" in audio  # the /note query: ?hold=1
    piano = PIANO.read_text(encoding="utf-8")
    # the warm-up fetches the render a press will ask for (keys.js ensure):
    # piano.js hands it the hold mode, keys.js applies holdFor's rule per
    # lane (the key's patch, and each pre-rendered lane's own sustain)
    assert "keys.map(pitchOfEl), holdMode" in piano
    assert "p.sustain > 0.05" in piano and "p.sustain > 0.05" in audio
    assert "holdOf(patch, holdMode)" in audio
