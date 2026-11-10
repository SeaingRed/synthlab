"""A note's release has to survive the sequencer.

`Song.notes` rendered every note into a buffer 1.15x its own length while
holding the gate for 0.9 of it, so the release — whatever the patch asked
for — was squeezed into the 0.25 that was left. A pad with a 2.2 s release
got 0.125 s of it on a half-second note, notes never rang into one another,
and `release` was effectively a dead control everywhere except a one-shot.
"""

from __future__ import annotations

import os

import numpy as np

from synthlab.core import SR
from synthlab.sequencer import Song
from synthlab.synth import PRESETS, Synth


def energy_at(audio: np.ndarray, seconds: float, window: float = 0.05) -> float:
    i = int(seconds * SR)
    j = min(len(audio), i + int(window * SR))
    if i >= len(audio):
        return 0.0
    seg = audio[i:j]
    return float(np.sqrt((seg**2).mean()))


def test_a_long_release_still_rings_well_after_the_gate_closes():
    """pad_warm holds a 2.2 s release. One beat at 120 bpm is 0.5 s, so a
    second after the note starts it should still be clearly sounding."""
    synth = Synth(PRESETS["pad_warm"])
    song = Song(bpm=120)
    song.notes("pad", synth, [("C4", 1.0)])
    audio = song.render(normalize=0)
    peak = energy_at(audio, 0.9)
    assert energy_at(audio, 1.4) > peak * 0.25, "the release was cut off"


def test_a_short_release_does_not_stretch_the_note():
    """The tail is the patch's release, not a fixed multiplier: a pluck must
    stay a pluck."""
    synth = Synth(PRESETS["pluck_glass"])  # release 0.4
    song = Song(bpm=120)
    song.notes("pluck", synth, [("C4", 1.0)])
    audio = song.render(normalize=0)
    assert energy_at(audio, 1.4) < energy_at(audio, 0.3) * 0.1


def test_notes_still_land_on_the_beat():
    """Giving the release room must not move where a note starts."""
    synth = Synth(PRESETS["pluck_glass"])
    song = Song(bpm=120)
    song.notes("t", synth, [(None, 1.0), ("C4", 1.0)])
    audio = np.abs(song.render(normalize=0)).mean(axis=1)
    onset = int(np.argmax(audio > audio.max() * 0.2))
    assert abs(onset - int(0.5 * SR)) < int(0.02 * SR), f"onset at {onset / SR:.3f}s"


def test_a_song_at_another_rate_renders():
    """`Track.render` sized its buffer with the global SR while `Song.render`
    used the song's, so `Song(sr=22050)` with one event raised a broadcast
    error (44100 more samples than the mix had room for)."""
    song = Song(bpm=120, sr=22050)
    song.play("t", np.ones((100, 2)), beat=1)
    audio = song.render(normalize=0, tail_beats=0)
    assert len(audio) == 11025 + 100 + 22050
    assert np.all(audio[11025:11125] == 1.0) and not np.any(audio[11125:])


def test_an_event_before_beat_zero_loses_its_head_not_the_render():
    """A negative start sample (`play` at beat -0.001) sliced the buffer
    with it and crashed; the part before beat 0 is simply never heard."""
    ramp = np.arange(1.0, 101.0)
    song = Song(bpm=120)
    song.play("t", ramp, beat=-40 / (0.5 * SR))  # 40 samples early
    audio = song.render(normalize=0, tail_beats=0)
    assert audio[0, 0] == ramp[40] and audio[59, 1] == ramp[99]
    assert not np.any(audio[60:])
    song.play("t", ramp, beat=-1)  # wholly before the start: nothing at all
    np.testing.assert_array_equal(song.render(normalize=0, tail_beats=0), audio)


def test_humanize_is_the_same_in_every_process():
    """The jitter was seeded from `hash((name, pattern))`, which Python
    salts per process: a humanized pattern rendered differently every run."""
    import subprocess
    import sys

    code = (
        "from synthlab.sequencer import Song;"
        "s = Song().drum('d', 'hat', 'x.x.x.x.', humanize=0.05);"
        "print([e[0] for e in s.tracks['d'].events])"
    )
    runs = {
        subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("1", "2")
    }
    assert len(runs) == 1 and "[" in runs.pop()
