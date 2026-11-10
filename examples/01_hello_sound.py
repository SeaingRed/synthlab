"""
01 — the smallest possible thing that makes sound.

Run:  python examples/01_hello_sound.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synthlab import Synth, audio, preset
from synthlab import effects as fx

# 1. pick a sound
synth = Synth(preset("pluck_glass"))

# 2. play a note
note = synth.note("C4", duration=2.0)

# 3. put it through some effects
note = fx.chain(
    note,
    [
        fx.delay(time=0.28, feedback=0.45, mix=0.35, ping_pong=True),
        fx.reverb(decay=2.5, mix=0.3),
    ],
)

# 4. hear it
audio.save("hello.wav", note)
print("wrote hello.wav")
# audio.play(note)          # uncomment if you have speakers + sounddevice
