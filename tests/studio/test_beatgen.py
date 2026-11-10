import pytest

from synthlab.studio import beatgen
from synthlab.studio.state import DRUM_ROWS


def _hits(steps, voice):
    return sorted(s for v, s in steps if v == voice)


def test_generate_is_deterministic_per_seed():
    a = beatgen.generate("breaks", seed=7)
    b = beatgen.generate("breaks", seed=7)
    c = beatgen.generate("breaks", seed=8)
    assert a == b
    assert a != c


def test_a_row_outside_the_style_is_the_same_in_every_process():
    """A voice the style table doesn't hold (toms, the open hat) gets a
    seeded figure. Its rng was seeded from `hash((seed, voice))`, and a
    str's hash is salted per process: the same seed drew another figure
    every time the studio started. Pinned, so it can't depend on the run."""
    assert beatgen.generate_row("tom", "techno", 5, 1, 16) == [
        ["tom", 1],
        ["tom", 5],
        ["tom", 9],
        ["tom", 13],
    ]
    assert beatgen.generate_row("ohat", "sparse", 5, 2, 16) == [
        ["ohat", 3],
        ["ohat", 11],
        ["ohat", 18],
        ["ohat", 23],
        ["ohat", 28],
    ]


def test_generate_rejects_unknown_style():
    with pytest.raises(ValueError):
        beatgen.generate("polka", seed=1)


def test_four_floor_kicks_every_beat():
    steps = beatgen.generate("four-floor", seed=3, steps_per_bar=16)
    assert _hits(steps, "kick") == [0, 4, 8, 12]


@pytest.mark.parametrize("style", beatgen.STYLES)
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_every_style_has_a_kick_and_valid_cells(style, seed):
    steps = beatgen.generate(style, seed=seed, bars=2, steps_per_bar=16)
    assert any(v == "kick" for v, _ in steps)
    assert steps == sorted(steps)
    assert len(steps) == len({tuple(s) for s in steps})
    for v, s in steps:
        assert v in DRUM_ROWS
        assert 0 <= s < 32


def test_sparse_is_sparse():
    for seed in range(5):
        assert len(beatgen.generate("sparse", seed=seed)) <= 12


def test_generate_scales_to_coarse_grid():
    steps = beatgen.generate("four-floor", seed=1, steps_per_bar=4)
    assert _hits(steps, "kick") == [0, 1, 2, 3]


def test_mutate_flips_a_few_cells():
    base = beatgen.generate("techno", seed=1)
    out = beatgen.mutate(base, seed=5, bars=1, steps_per_bar=16)
    assert out != base
    assert out == sorted(out)
    assert len(out) == len({tuple(s) for s in out})
    changed = len(set(map(tuple, out)) ^ set(map(tuple, base)))
    assert 1 <= changed <= 0.25 * 16 * len(DRUM_ROWS)


# -- drum tools cover every bar ---------------------------------
@pytest.mark.parametrize("style", beatgen.STYLES)
def test_two_bar_generate_fills_both_bars_and_keeps_bar_one(style):
    """Randomise on a 2-bar grid fills bar 2 as well — a light variation
    (the groove repeats, the sprinkles re-roll) — and bar 1 is exactly the
    1-bar pattern for the same seed, so 1-bar output never moved."""
    for seed in range(5):
        two = beatgen.generate(style, seed, bars=2, steps_per_bar=16)
        one = beatgen.generate(style, seed, bars=1, steps_per_bar=16)
        assert [c for c in two if c[1] < 16] == one
        bar2 = [[v, s - 16] for v, s in two if s >= 16]
        assert any(v == "kick" for v, _ in bar2)
        # the forced groove is in both bars
        forced = {(v, s) for v, s in one if v == "kick"} & {tuple(c) for c in bar2}
        assert forced


def test_two_bar_mutate_reaches_bar_two():
    base = beatgen.generate("techno", seed=1, bars=2)
    touched = set()
    for seed in range(20):
        out = beatgen.mutate(base, seed, bars=2, steps_per_bar=16)
        touched |= {s for _, s in set(map(tuple, out)) ^ set(map(tuple, base))}
    assert any(s >= 16 for s in touched)


def test_growing_one_bar_to_two_duplicates_bar_one():
    bar1 = beatgen.generate("breaks", seed=4, bars=1)
    grown = beatgen.extend_bars(bar1, 1, 2, 16)
    assert [c for c in grown if c[1] < 16] == bar1
    assert [[v, s - 16] for v, s in grown if s >= 16] == bar1


def test_growing_repeats_the_whole_phrase_and_shrinking_trims():
    two = [["kick", 0], ["snare", 20]]
    assert beatgen.extend_bars(two, 2, 4, 16) == [
        ["kick", 0],
        ["kick", 32],
        ["snare", 20],
        ["snare", 52],
    ]
    assert beatgen.extend_bars(two, 2, 1, 16) == [["kick", 0]]
    assert beatgen.extend_bars(two, 2, 2, 16) == two


def test_growing_reveals_hidden_hits_instead_of_overwriting_them():
    """A grid shrunk 2 -> 1 keeps bar 2's hits, hidden. Growing back used
    to drop them and copy bar 1 over the bar; the note grid shows them
    again (`patternops.repeat_into`). A repeat only goes into a block that
    holds no hits; hits still past the new end stay hidden."""
    kept = [["kick", 0], ["snare", 20], ["hat", 70]]  # bar 1, bar 2, bar 5
    assert beatgen.repeat_offsets(kept, 16, 32) == []
    assert beatgen.extend_bars(kept, 1, 2, 16) == sorted(kept)
    # 1 -> 4: bar 2 comes back as it was, bars 3 and 4 repeat bar 1
    assert beatgen.repeat_offsets(kept, 16, 64) == [32, 48]
    assert beatgen.extend_bars(kept, 1, 4, 16) == sorted(
        [*kept, ["kick", 32], ["kick", 48]]
    )
    # nothing hidden: every block repeats, as before
    assert beatgen.repeat_offsets([["kick", 0]], 16, 64) == [16, 32, 48]
