"""Genre steering directions — one a Start-a-song style: "melodic_techno",
"garage", "filter_house", "trance", "electro", "industrial", "psych" and
"sixties": a target feature profile
scored on the breeder's probe, gene leanings for the single-depth breeder
(`breed.GENE_BIAS`), role/FX biases for layered and deep (`genome.STEERS`).
The five tonal directions are left exactly as they were. All seeded, so the
comparisons are fixed numbers, not luck.

Every profile but the first two is measured from its pack's own recipe
sounds, and re-measured here."""

from types import SimpleNamespace

import numpy as np
import pytest

from synthlab import PRESETS
from synthlab.explore import SpaceStore
from synthlab.explore.breed import (
    _EXPLORE_EVERY,
    DIRECTIONS,
    GENE_BIAS,
    GENRE_TARGETS,
    generation,
    genre_distance,
    lean,
    steer,
)
from synthlab.explore.probe import probe
from synthlab.params import PARAMS
from synthlab.studio import genome
from synthlab.studio.breedboard import WILDNESS, new_generation
from synthlab.synth import Patch

GENRES = (
    "melodic_techno",
    "garage",
    "filter_house",
    "trance",
    "electro",
    "industrial",
    "psych",
    "sixties",
)
# the directions whose target profile is measured from a pack's recipes:
# direction -> the recipes' genre tag (`recipes.GENRES`)
MEASURED = {
    "filter_house": "house",
    "trance": "trance",
    "electro": "electro",
    "industrial": "industrial",
    "psych": "psych",
    "sixties": "sixties",
}
TONAL = ("more_sub", "darker", "brighter", "grittier", "cleaner")
PARENTS = (Patch(), PRESETS["pad_warm"], PRESETS["bass_reese"])


def test_genre_directions_are_registered_next_to_the_tonal_ones():
    assert set(DIRECTIONS) == set(TONAL) | set(GENRES)
    assert set(GENRE_TARGETS) == set(GENE_BIAS) == set(GENRES)
    assert set(genome.STEERS) >= set(GENRES)
    for g in GENRES:
        for gene, spec in GENE_BIAS[g].items():
            param = PARAMS[gene]
            if param.kind == "cat":
                assert set(spec) <= set(param.gene), (g, gene)
            else:
                lo, hi = param.gene
                assert lo <= spec[0] <= spec[1] <= hi, (g, gene)


@pytest.mark.parametrize("g", list(MEASURED))
def test_a_measured_profile_is_its_packs_recipes(g):
    """Each target is the mean of that measure over the pack's recipe main
    patches that are not basses, on the breeder's probe, to a tenth (the
    table holds one decimal; 0.06 leaves room for the rounding)."""
    from synthlab.explore.breed import BREED_VECTOR
    from synthlab.studio.recipes import RECIPES, recipe_patch

    sounds = [
        e for e in RECIPES if MEASURED[g] in e["tags"] and "bass" not in e["tags"]
    ]
    assert len(sounds) >= 4
    mean = np.mean([probe(recipe_patch(e)).vector for e in sounds], axis=0)
    for measure, (target, weight) in GENRE_TARGETS[g].items():
        got = float(mean[BREED_VECTOR.index(measure)])
        assert abs(got - target) <= 0.06, (g, measure, round(got, 2), target)
        assert 0.0 < weight <= 1.0
    # and the pack's own sounds sit nearer their profile than the other
    # packs' sounds do
    own = np.mean([genre_distance(g, probe(recipe_patch(e))) for e in sounds])
    rest = [
        e for e in RECIPES if MEASURED[g] not in e["tags"] and "bass" not in e["tags"]
    ]
    other = np.mean([genre_distance(g, probe(recipe_patch(e))) for e in rest])
    assert own < other, (g, own, other)


def test_a_probe_on_the_profile_scores_zero():
    m = probe(PRESETS["pad_warm"])
    for g in GENRES:
        assert genre_distance(g, m) >= 0.0
        assert DIRECTIONS[g](m) == -genre_distance(g, m)


@pytest.mark.parametrize("bias", [None, *TONAL])
def test_tonal_directions_never_lean(bias):
    """Only a genre leans a litter: every other bias draws nothing, so a
    seeded litter is the one it always was."""
    base = generation(list(PARENTS), n=10, rng=np.random.default_rng(3))
    got = generation(list(PARENTS), n=10, rng=np.random.default_rng(3), bias=bias)
    assert got == base


@pytest.mark.parametrize("g", GENRES)
def test_lean_moves_genes_into_the_genre(g):
    r = np.random.default_rng(0)
    moved = 0
    for _ in range(40):
        child = lean(Patch(), g, r)
        diff = {k for k in GENE_BIAS[g] if getattr(child, k) != getattr(Patch(), k)}
        moved += bool(diff)
        for k in diff:
            spec, v = GENE_BIAS[g][k], getattr(child, k)
            if PARAMS[k].kind == "cat":
                assert v in spec
            else:
                assert spec[0] <= v <= spec[1]
    assert 15 <= moved <= 35  # _LEAN_PROB 0.6 of 40, give or take
    a = [lean(Patch(), g, np.random.default_rng(9)) for _ in range(3)]
    assert a[0] == a[1] == a[2]  # seeded


def _mean_distance(g, patches) -> float:
    return float(np.mean([genre_distance(g, probe(p)) for p in patches]))


@pytest.mark.parametrize("g", GENRES)
def test_steer_moves_toward_the_genre_profile(g):
    """`breed.steer` (lean + select) lands nearer the genre's profile than
    the unsteered litter of the same seeds, from three different parents.
    Judged on the steered picks: the last of the four is the explorer (the
    candidate farthest from the rest, whatever it scores — `steer`), as the
    board's test below leaves out the board's. With it, a profile near the
    library's middle (psych's) read as no steer at all: one explorer a
    parent outweighed the three picks."""
    steered, plain = [], []
    for i, parent in enumerate(PARENTS):
        picks = steer([parent], g, n=4, pool=16, rng=np.random.default_rng(i))
        steered += picks[: -(4 // _EXPLORE_EVERY)]
        plain += [c for c, _ in generation([parent], n=4, rng=np.random.default_rng(i))]
    assert _mean_distance(g, steered) < 0.8 * _mean_distance(g, plain)


def _state(tmp_path, patch):
    return SimpleNamespace(breed_starred=[], patch=patch, data_dir=tmp_path, bpm=124.0)


def _board(tmp_path, patch, depth, direction, seed):
    store = SpaceStore(tmp_path / "space.jsonl")
    tiles = new_generation(
        _state(tmp_path, patch),
        direction,
        rng=np.random.default_rng(seed),
        amount=WILDNESS["normal"],
        store=store,
        depth=depth,
    )
    return [genome.stack_of_row(store.get(t.id)) for t in tiles]


@pytest.mark.parametrize("g", GENRES)
def test_the_board_moves_toward_the_genre_at_single_depth(tmp_path, g):
    """Judged on the steered cards: the board's last `genome.EXPLORERS`
    slots are the litter's most different sounds, whatever they score."""
    steered, plain = [], []
    for i, parent in enumerate(PARENTS):
        board = _board(tmp_path, parent, "single", g, i)[: -genome.EXPLORERS]
        steered += [st.patch for st in board]
        plain += [st.patch for st in _board(tmp_path, parent, "single", None, i)]
    assert _mean_distance(g, steered) < 0.8 * _mean_distance(g, plain)


def _share(stacks, pred) -> float:
    return float(np.mean([pred(s) for s in stacks]))


def test_deep_steers_of_the_two_new_styles_bias_roles_and_fx(tmp_path):
    house, trance, plain = [], [], []
    for i, parent in enumerate(PARENTS):
        house += _board(tmp_path, parent, "deep", "filter_house", i)
        trance += _board(tmp_path, parent, "deep", "trance", i)
        plain += _board(tmp_path, parent, "deep", None, i)

    def has_role(role):
        return lambda s: role in s.roles()

    def main_fx(*kinds):
        return lambda s: sum(u["kind"] in kinds for u in s.main_fx)

    # filter house: phased, squashed and gritty more often than the
    # unsteered board, and more often than smeared (a steer weights the
    # moves, it forbids none: a shimmer or a grain cloud still turns up)
    squash = main_fx("phaser", "compress", "drive", "bitcrush")
    assert _share(house, squash) > _share(plain, squash)
    assert _share(house, main_fx("shimmer", "granular")) < _share(house, squash)
    # trance: echoes, halls and width; plucks and pads around the main
    wet = main_fx("delay", "reverb", "width", "chorus")
    assert _share(trance, wet) > _share(plain, wet)
    around = lambda s: "pluck" in s.roles() or "pad" in s.roles()  # noqa: E731
    assert _share(trance, around) >= _share(plain, around)
    # worn units: no more often than unsteered, give or take one card of
    # the 24 (a steer forbids no move; with the fm4 / sync genes in the
    # seeded draws one bitcrushed trance card comes up)
    worn = main_fx("bitcrush", "fold")
    assert _share(trance, worn) <= _share(plain, worn) + 1 / len(trance)
    assert has_role("sub")  # (the helper the techno / garage test uses)


def test_deep_genre_steers_bias_roles_and_fx(tmp_path):
    techno, garage, plain = [], [], []
    for i, parent in enumerate(PARENTS):
        techno += _board(tmp_path, parent, "deep", "melodic_techno", i)
        garage += _board(tmp_path, parent, "deep", "garage", i)
        plain += _board(tmp_path, parent, "deep", None, i)

    def has_role(role):
        return lambda s: role in s.roles()

    def main_fx(*kinds):
        # the main lane's FX (a layer's role FX are its recipe's, not a steer's)
        return lambda s: sum(u["kind"] in kinds for u in s.main_fx)

    # techno: pads and air, echoes and rooms, width
    assert _share(techno, has_role("pad")) + _share(techno, has_role("air")) > (
        _share(plain, has_role("pad")) + _share(plain, has_role("air"))
    )
    wet = main_fx("delay", "reverb", "width", "chorus")
    assert _share(techno, wet) > _share(plain, wet)
    # garage: a sub under it, compressed/darkened, never widened
    assert _share(garage, has_role("sub")) > _share(plain, has_role("sub"))
    tight = main_fx("compress", "lowpass")
    assert _share(garage, tight) > _share(plain, tight)
    assert _share(garage, main_fx("width")) <= _share(plain, main_fx("width"))
