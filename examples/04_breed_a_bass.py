# examples/04_breed_a_bass.py
"""Breed a deep bass from two presets, steering toward more sub weight.

Three generations of feature-guided evolution, every audition logged to
a sound-space store, best patch saved as JSON + WAV.

    uv run python examples/04_breed_a_bass.py
"""

import numpy as np

from synthlab import PRESETS, Synth, audio
from synthlab.analysis import extract, phone_test
from synthlab.explore import SpaceStore, steer

rng = np.random.default_rng(2026)
store = SpaceStore("space.jsonl")

parents = [PRESETS["bass_reese"], PRESETS["bass_acid"]]
parent_ids = []
for p in parents:
    parent_ids.append(store.add(p, extract(Synth(p).note("A1", 1.0))))

for gen in range(3):
    children = steer(parents, "more_sub", n=4, pool=16, rng=rng)
    child_ids = []
    for c in children:
        clip = Synth(c).note("A1", 1.0)
        f = extract(clip)
        child_ids.append(store.add(c, f, parents=parent_ids))
        print(
            f"gen {gen}  {c.name:<28} sub={f.sub_20_60:.2f} "
            f"centroid={f.centroid:6.0f} Hz"
        )
    parents, parent_ids = children[:2], child_ids[:2]

best = parents[0]
clip = Synth(best).note("A1", 2.0)
report = phone_test(clip)
print(f"\nwinner: {best.name}")
print(
    f"phone-speaker survival: {report['survives_ratio']:.0%} "
    "(low = pure sub; consider drive/harmonics if you need it to read)"
)

best.save("bred_bass.json")
audio.save("bred_bass.wav", clip, normalize_to=0.89)
print(
    "wrote bred_bass.json and bred_bass.wav "
    f"({len(store.rows())} sounds now in space.jsonl)"
)
