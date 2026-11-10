"""
02 — sound design: build a patch from scratch and hear each layer.

This is the file to live in while you're learning. Change one number,
re-run, listen. That loop is the whole craft.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from synthlab import Patch, Synth, audio
from synthlab import effects as fx

# ---------------------------------------------------------------------------
# A patch is just data. Every field is documented in synthlab/synth.py.
# ---------------------------------------------------------------------------
my_bass = Patch(
    name="growler",
    osc1="saw",
    osc1_level=0.8,
    osc2="square",
    osc2_level=0.45,
    osc2_octave=0,
    osc2_detune=11,
    sub_level=0.6,  # sine an octave down = weight
    noise_level=0.02,
    filter_type="ladder",  # try "lowpass", "bandpass", "none"
    cutoff=170,  # where the filter sits at rest
    resonance=0.55,  # 0..1 — past 0.7 it starts to whistle
    filter_env=3400,  # how far the envelope opens it
    filter_decay=0.30,  # the "wow" of the sweep
    filter_sustain=0.15,
    attack=0.004,
    decay=0.25,
    sustain=0.6,
    release=0.15,
    drive=2.6,  # saturation, adds harmonics
    unison=2,
    unison_detune=10,
)

synth = Synth(my_bass)

# --- play a riff -----------------------------------------------------------
riff = ["A1", "A1", "C2", "A1", "D2", "A1", "G1", "E1"]
out = np.zeros((0, 2))
for i, n in enumerate(riff):
    out = np.vstack([out, synth.note(n, 0.4, velocity=0.9 if i % 2 == 0 else 0.7)])

out = fx.chain(
    out, [fx.drive(1.6, mix=0.5), fx.reverb(1.2, mix=0.12), fx.limit(loop=False)]
)
audio.save("my_bass.wav", out)
print("wrote my_bass.wav")

# --- A/B a parameter -------------------------------------------------------
# Render the same riff with four different resonance settings, back to back.
compare = np.zeros((0, 2))
for res in (0.1, 0.4, 0.7, 0.9):
    s = Synth(my_bass.tweak(resonance=res))
    for n in riff[:4]:
        compare = np.vstack([compare, s.note(n, 0.35)])
audio.save("resonance_sweep.wav", compare, normalize_to=0.9)
print("wrote resonance_sweep.wav — four resonance settings in a row")

# --- roll the dice ---------------------------------------------------------
# Patch.random() invents new sounds. Keep the ones you like: p.save('x.json')
lucky = np.zeros((0, 2))
for seed in range(6):
    p = Patch.random(seed=seed)
    lucky = np.vstack([lucky, Synth(p).note("C3", 1.0), np.zeros((4410, 2))])
    print(
        f"  seed {seed}: {p.osc1}/{p.osc2} cutoff={p.cutoff:.0f} res={p.resonance:.2f}"
    )
audio.save("random_patches.wav", lucky, normalize_to=0.9)
print("wrote random_patches.wav")
