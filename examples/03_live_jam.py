"""
03 — real-time: a playable instrument driven by your computer keyboard.

Needs speakers and:  pip install sounddevice
(on Linux you may also need:  sudo apt install libportaudio2)

This shows the pattern for live audio: a callback that fills a small block
of samples every few milliseconds. Everything in synthlab.core works inside
a callback because it is all just NumPy on arrays.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from synthlab import audio
from synthlab.core import SR, ladder, note_to_hz

# --- a tiny polyphonic engine that runs in real time -----------------------
voices: dict[str, dict] = {}  # note name -> {"freq":..., "phase":..., "amp":...}
cutoff_lfo_phase = 0.0


def render_block(n_frames: int, t: float) -> np.ndarray:
    global cutoff_lfo_phase
    out = np.zeros(n_frames)
    dead = []
    for key, v in voices.items():
        # continue the oscillator from where it left off (phase continuity!)
        ph = (np.arange(n_frames) * v["freq"] / SR + v["phase"]) % 1.0
        v["phase"] = float(ph[-1])
        saw = 2 * ph - 1
        sub = np.sin(2 * np.pi * (ph * 0.5))
        env = np.linspace(v["amp"], v["amp"] * v["decay"], n_frames)
        v["amp"] = float(env[-1])
        out += (saw * 0.6 + sub * 0.4) * env
        if v["amp"] < 1e-4:
            dead.append(key)
    for k in dead:
        voices.pop(k, None)

    # a slow filter sweep so it never sounds static
    sweep = 600 + 1400 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.15 * t))
    out = ladder(out, sweep, resonance=0.4, drive=1.5)
    return np.column_stack([out, out]) * 0.35


def note_on(name: str, amp: float = 0.5, decay: float = 0.9997):
    voices[name] = {"freq": note_to_hz(name), "phase": 0.0, "amp": amp, "decay": decay}


if __name__ == "__main__":
    KEYS = "zsxdcvgbhnjm,l.;/"  # a piano-style keyboard row
    NOTES = [
        "C3",
        "C#3",
        "D3",
        "D#3",
        "E3",
        "F3",
        "F#3",
        "G3",
        "G#3",
        "A3",
        "A#3",
        "B3",
        "C4",
        "C#4",
        "D4",
        "D#4",
        "E4",
    ]

    print("keys:", " ".join(f"{k}={n}" for k, n in zip(KEYS, NOTES, strict=False)))
    print("type letters then press enter to play them; 'q' to quit\n")

    with audio.stream(render_block, blocksize=256):
        while True:
            line = input("> ").strip()
            if line == "q":
                break
            for ch in line:
                if ch in KEYS:
                    note_on(NOTES[KEYS.index(ch)])
