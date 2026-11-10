"""How much variety a steered breed keeps, generation over generation.

    uv run python tools/breed_spread.py [--gens 6] [--seeds 2] [--wildness normal]

Breeds each start patch toward each direction for `--gens` generations, two
ways — `breed.steer` feeding its own picks back as parents, and the studio's
board (`breedboard.new_generation`, starring its three best-scoring cards,
the listener who follows the direction hardest) — and prints per generation:

- **spread**: mean sibling-to-sibling distance on the breeder's standardised
  probe vector (one unit = one library spread, `explore/scale.py`);
- **alike**: how many of the litter's 28 pairs sit under 1.0 — soundalikes;
- **best**: the mean direction score of the three best cards.

A steer that works holds the first up and the second down while the third
rises. `tests/studio/test_breed_board_rules.py` pins the board's numbers.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from synthlab import PRESETS
from synthlab.explore import SpaceStore
from synthlab.explore.breed import DIRECTIONS, steer
from synthlab.explore.probe import Probe, distance, probe
from synthlab.studio.breedboard import WILDNESS, new_generation
from synthlab.synth import Patch

STARTS = {
    "default": Patch(),
    "pad_warm": PRESETS["pad_warm"],
    "lead_supersaw": PRESETS["lead_supersaw"],
}
DIRS = ("brighter", "grittier", "melodic_techno")
ALIKE = 1.0  # a pair closer than this is one sound twice (a one-knob move)


def pair_distances(probes: list[Probe]) -> list[float]:
    vecs = [m.vector for m in probes]
    return [
        distance(vecs[i], vecs[j])
        for i in range(len(vecs))
        for j in range(i + 1, len(vecs))
    ]


def spread(patches: list[Patch]) -> float:
    """Mean pairwise probe distance between siblings (0 for clones)."""
    pairs = pair_distances([m for m in map(probe, patches) if m is not None])
    return float(np.mean(pairs)) if pairs else 0.0


def _row(patches: list[Patch], direction: str) -> tuple[float, float, float, list]:
    """(spread, soundalike pairs, mean of the three best scores, the
    patches' order best-first)."""
    probes = [probe(p) for p in patches]
    pairs = pair_distances(probes)
    scores = [float(DIRECTIONS[direction](m)) for m in probes]
    order = sorted(range(len(patches)), key=lambda i: -scores[i])
    best = float(np.mean([scores[i] for i in order[:3]]))
    return float(np.mean(pairs)), float(sum(d < ALIKE for d in pairs)), best, order


def steer_run(start: Patch, direction: str, gens: int, seed: int, amount: float):
    """`steer` on its own output: every pick parents the next litter."""
    r = np.random.default_rng(seed)
    parents, out = [start], []
    for _ in range(gens):
        parents = steer(parents, direction, n=8, pool=16, amount=amount, rng=r)
        out.append(_row(parents, direction)[:3])
    return out


def board_run(start: Patch, direction: str, gens: int, seed: int, amount: float):
    """The board, starring its three best-scoring cards each generation."""
    r = np.random.default_rng(seed)
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        store = SpaceStore(Path(tmp) / "space.jsonl")
        state = SimpleNamespace(
            breed_starred=[], patch=start, data_dir=Path(tmp), bpm=124.0
        )
        for _ in range(gens):
            tiles = new_generation(state, direction, rng=r, amount=amount, store=store)
            patches = [Patch.from_dict(store.get(t.id)["patch"]) for t in tiles]
            *row, order = _row(patches, direction)
            out.append(row)
            state.breed_starred = [tiles[i].id for i in order[:3]]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gens", type=int, default=6)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--wildness", choices=sorted(WILDNESS), default="normal")
    args = ap.parse_args()
    amount = WILDNESS[args.wildness]
    for label, run in (("steer()", steer_run), ("board", board_run)):
        print(f"\n{label}, {args.wildness}: spread/alike/best per generation")
        total = np.zeros((args.gens, 3))
        for d in DIRS:
            rows = np.zeros((args.gens, 3))
            for start in STARTS.values():
                for seed in range(args.seeds):
                    rows += np.array(run(start, d, args.gens, seed, amount))
            rows /= len(STARTS) * args.seeds
            total += rows
            cells = "  ".join(f"{s:4.2f}/{a:4.1f}/{b:+6.2f}" for s, a, b in rows)
            print(f"  {d:<15} {cells}", flush=True)
        total /= len(DIRS)
        cells = "  ".join(f"{s:4.2f}/{a:4.1f}       " for s, a, _ in total)
        print(f"  {'mean':<15} {cells}", flush=True)


if __name__ == "__main__":
    main()
