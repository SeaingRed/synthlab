import numpy as np

from synthlab import PRESETS, Synth
from synthlab.explore.breed import DIRECTIONS, steer
from synthlab.explore.probe import measure, probe


def _mean_score(patches, direction):
    # scored on the breeder's own probe (A3, the listening register)
    return float(np.mean([DIRECTIONS[direction](probe(p)) for p in patches]))


def test_steer_returns_n_and_is_deterministic():
    parents = [PRESETS["bass_reese"]]
    a = steer(parents, "more_sub", n=4, pool=8, rng=np.random.default_rng(5))
    b = steer(parents, "more_sub", n=4, pool=8, rng=np.random.default_rng(5))
    assert len(a) == 4
    assert a == b


def test_steer_more_sub_beats_unsteered_selection():
    parents = [PRESETS["lead_supersaw"]]  # start bright on purpose
    rng = np.random.default_rng(6)
    steered = steer(parents, "more_sub", n=4, pool=16, rng=rng)
    parent_score = _mean_score(parents, "more_sub")
    steered_score = _mean_score(steered, "more_sub")
    assert steered_score >= parent_score


def _pairs(patches):
    vecs = [probe(p).vector for p in patches]
    return [
        float(np.linalg.norm(a - b)) for i, a in enumerate(vecs) for b in vecs[i + 1 :]
    ]


def test_steer_keeps_a_quarter_of_its_picks_for_the_most_different():
    """The 8 best-scoring of a tame litter of 16 were mostly one sound:
    over three presets x three seeds toward "brighter", 15 of their 28
    pairs sat under 1.0 on the probe distance (mean sibling distance
    1.33). With one pick in four going to the farthest audible candidate
    it is 8 pairs (1.92) — and the best six are the same six, first."""
    spreads, alike = [], []
    for name in ("bass_reese", "pad_warm", "lead_supersaw"):
        for seed in range(3):
            args = dict(n=8, pool=16, amount=0.35)
            kids = steer(
                [PRESETS[name]], "brighter", rng=np.random.default_rng(seed), **args
            )
            pairs = _pairs(kids)
            spreads.append(np.mean(pairs))
            alike.append(sum(d < 1.0 for d in pairs))
            scores = [DIRECTIONS["brighter"](probe(k)) for k in kids]
            assert scores[:6] == sorted(scores[:6], reverse=True)
            assert min(scores[:6]) >= max(scores[6:])
    assert float(np.mean(spreads)) > 1.6, spreads
    assert float(np.mean(alike)) < 11, alike


def test_steer_unknown_direction_raises():
    import pytest

    with pytest.raises(KeyError):
        steer([PRESETS["bass_reese"]], "sadder", rng=np.random.default_rng(0))


def test_all_directions_are_callable_on_features():
    m = measure(Synth(PRESETS["bass_acid"]).note("A3", 0.5))
    for name, score in DIRECTIONS.items():
        assert np.isfinite(score(m)), name
