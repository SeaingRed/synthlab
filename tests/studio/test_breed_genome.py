"""The layered breeding genome (studio/genome.py): a bred Sound is a main
patch, up to two following layers made to a role recipe, main FX and
curves — the format-2 preset shape — and a litter respects its depth."""

import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from synthlab import PRESETS
from synthlab.explore import SpaceStore
from synthlab.explore.breed import generation
from synthlab.explore.probe import probe
from synthlab.params import slot_active
from synthlab.studio import genome
from synthlab.studio.breedboard import WILDNESS, new_generation
from synthlab.studio.genome import (
    DEPTH,
    ROLES,
    SPATIAL,
    Layer,
    Stack,
    chain_of,
    layer_audio,
    main_audio,
    measure_stack,
    stack_of_row,
    struct_distance,
)
from synthlab.synth import Patch

PARENTS = (Patch(), PRESETS["pad_warm"], PRESETS["bass_reese"])


def _state(tmp_path: Path, patch: Patch, starred=()) -> SimpleNamespace:
    return SimpleNamespace(
        breed_starred=list(starred), patch=patch, data_dir=tmp_path, bpm=124.0
    )


def _run(tmp_path, patch, depth, direction=None, seed=0, wild="normal", starred=()):
    store = SpaceStore(tmp_path / "space.jsonl")
    tiles = new_generation(
        _state(tmp_path, patch, starred),
        direction,
        rng=np.random.default_rng(seed),
        amount=WILDNESS[wild],
        store=store,
        depth=depth,
    )
    return [stack_of_row(store.get(t.id)) for t in tiles], tiles, store


# ------------------------------------------------------------------ depth


def test_single_depth_is_the_patch_breeder(tmp_path):
    """ "single" is today's breeder: plain patches, no `sound` in the log,
    and the same litter as before (the origin bookkeeping draws nothing
    from the rng)."""
    stacks, tiles, store = _run(tmp_path, PRESETS["pad_warm"], "single", seed=4)
    assert len(tiles) == 8
    assert all(st.plain for st in stacks)
    assert all("sound" not in store.get(t.id) for t in tiles)
    assert all(not t.chips and not t.layered for t in tiles)
    a = generation([Patch()], n=12, rng=np.random.default_rng(7))
    origins: list[int] = []
    b = generation([Patch()], n=12, rng=np.random.default_rng(7), origins=origins)
    assert [p.to_json() for p, _ in a] == [p.to_json() for p, _ in b]
    assert origins == [0] * 12


def _routes(p: Patch) -> int:
    return sum(slot_active(p, s) for s in ("mod1", "mod2", "mod3", "mod4"))


@pytest.mark.parametrize("depth", ["layered", "deep"])
def test_generation_respects_depth_caps(tmp_path, depth):
    d = DEPTH[depth]
    seen = 0
    for i, parent in enumerate(PARENTS):
        for wild in ("normal", "feral"):
            stacks, tiles, _ = _run(tmp_path, parent, depth, seed=i, wild=wild)
            assert len(tiles) == 8
            for st in stacks:
                seen += 1
                assert d.min_layers <= len(st.layers) <= d.max_layers
                assert len(set(st.roles())) == len(st.roles())
                sub_hp = 1 if "sub" in st.roles() else 0
                assert len(st.main_fx) <= d.max_main_fx + sub_hp
                for lay in st.layers:
                    assert d.layer_fx[0] <= len(lay.fx) <= d.layer_fx[1] + sub_hp
                assert _routes(st.patch) <= d.max_routes
                assert len(st.curves) <= d.max_curves
                kinds = genome.fx_kinds(st)
                assert not kinds & {"sidechain", "gate", "pump"}
                if not d.spatial:
                    assert not kinds & SPATIAL
    assert seen == 8 * len(PARENTS) * 2


def test_depths_make_structure(tmp_path):
    """Layered cards carry layers (a board is picked for difference, so most
    do), deep cards always do, with FX on every layer."""
    layered, deep = [], []
    for i, parent in enumerate(PARENTS):
        layered += _run(tmp_path, parent, "layered", seed=i)[0]
        deep += _run(tmp_path, parent, "deep", seed=i)[0]
    assert np.mean([len(s.layers) > 0 for s in layered]) >= 0.4
    assert all(s.layers for s in deep)
    assert all(lay.fx for s in deep for lay in s.layers)
    roles = {r for s in deep for r in s.roles()}
    assert len(roles) >= 3, roles


def test_parents_pass_their_structure_on(tmp_path):
    stacks, tiles, store = _run(tmp_path, PRESETS["lead_supersaw"], "deep", seed=2)
    kids, _, _ = _run(
        tmp_path, Patch(), "deep", seed=3, starred=[tiles[0].id], wild="tame"
    )
    parent = stacks[0]
    kept = [k for k in kids if set(parent.roles()) & set(k.roles())]
    assert len(kept) >= 4  # tame keeps most of the parent's layers


# ---------------------------------------------------------------- recipes


def _ctx(depth="deep", seed=0, direction=None, profile=None):
    from synthlab.explore.breed import litter_profile

    return genome._Ctx(
        depth=DEPTH[depth],
        r=np.random.default_rng(seed),
        amount=0.75,
        prof=litter_profile(0.75),
        steer=genome.STEERS.get(direction or "", genome.Steer()),
        donors=[],
        others=[],
        profile=profile,
    )


def test_recipes_repair_layers_into_their_role():
    ctx = _ctx()
    main = PRESETS["lead_supersaw"]
    wrecked = Patch(osc1="saw", unison=5, cutoff=9000.0, attack=0.001, release=3.0)
    for role in ROLES:
        fixed = genome.recipe(role, wrecked, ctx.r, fresh=False)
        for name, rule in ROLES[role].rules.items():
            v = getattr(fixed, name)
            if isinstance(rule[0], str) or rule[0] is None:
                assert v in rule, (role, name, v)
            else:
                assert rule[0] <= v <= rule[1], (role, name, v)
    sub = genome._new_layer("sub", main, ctx)
    assert sub.octave < 0 and sub.patch.osc1 in ("sine", "triangle")
    assert sub.patch.cutoff <= 200 and sub.patch.unison == 1
    pad = genome._new_layer("pad", main, ctx)
    assert pad.patch.attack >= 0.2 > main.attack  # a pad is slower
    assert any(u["kind"] == "highpass" for u in pad.fx)
    pluck = genome._new_layer("pluck", main, ctx)
    assert pluck.patch.sustain <= 0.2 and pluck.patch.decay <= 0.35
    assert any(u["kind"] == "delay" for u in pluck.fx)


def test_a_sub_layer_high_passes_the_main():
    ctx = _ctx()
    st = Stack(PRESETS["bass_reese"], [genome._new_layer("sub", Patch(), ctx)])
    genome.repair(st, ctx)
    hp = [u for u in st.main_fx if u["kind"] == "highpass"]
    assert hp and 120 <= hp[0]["params"]["cutoff"] <= 180
    st.layers = []
    genome.repair(st, _ctx("layered"))  # the sub gone, its high-pass goes too
    assert not [u for u in st.main_fx if u["kind"] == "highpass"]


def test_gains_are_staged_under_the_main():
    ctx = _ctx(seed=3)
    main = PRESETS["lead_supersaw"]
    st = Stack(main, [genome._new_layer(r, main, ctx) for r in ("sub", "pad")])
    genome.repair(st, ctx)
    genome.stage(st, 124.0)
    rms = genome._rms
    m = rms(main_audio(st, 124.0))
    for lay in st.layers:
        heard = lay.gain * rms(layer_audio(lay, 124.0))
        assert heard < m  # the main stays loudest
        if 0.02 < lay.gain < 1.5:  # not clipped: on its role's level
            assert 20 * np.log10(heard / m) == pytest.approx(lay.db, abs=0.1)
        lo, hi = ROLES[lay.role].db
        assert lo <= lay.db <= hi


def test_a_card_whose_main_is_silent_is_no_card():
    # a main filtered to almost nothing (a saw behind a 16 kHz high-pass)
    # passed the stack gate at -35 dB: staging floored its layers against
    # it, and "Use sound" / the mix played next to nothing
    ctx = _ctx()
    thin = Patch(osc1="saw", filter_type="highpass", cutoff=16000.0)
    bad = Stack(thin, [genome._new_layer(r, thin, ctx) for r in ("sub", "pad")])
    main = PRESETS["lead_supersaw"]
    good = Stack(main, [genome._new_layer(r, main, ctx) for r in ("sub", "pad")])
    for st in (bad, good):
        genome.repair(st, ctx)
    quiet, ok = genome.measure_all([(bad, bad, []), (good, good, [])], 124.0)
    assert genome.audible(quiet.m)  # the whole stack passes: that was the hole
    assert quiet.main_db < genome.MAIN_QUIET_DB <= ok.main_db
    assert not genome.card_ok(quiet) and genome.card_ok(ok)
    picked = genome.pick([quiet, ok], [], None, 1, 0.0, 1.0)
    assert picked == [ok]


@pytest.mark.parametrize("seed", [0, 1])
def test_every_deep_card_has_a_main_you_can_hear(tmp_path, seed):
    stacks, _tiles, _store = _run(tmp_path, PRESETS["pad_warm"], "deep", seed=seed)
    for st in stacks:
        loud = genome.main_loudness_db(st, 124.0)
        assert loud >= genome.MAIN_QUIET_DB, (st.patch.filter_type, st.patch.cutoff)


def test_the_role_fills_a_gap_in_the_main():
    thin = probe(Patch(osc1="saw", sub_level=0.0, filter_type="highpass"))
    fat = probe(PRESETS["bass_reese"])
    assert (
        genome._gap_weights(thin, set())["sub"] > genome._gap_weights(fat, set())["sub"]
    )
    assert "sub" not in genome._gap_weights(thin, {"sub"})


# ---------------------------------------------------- measure and distance


def test_a_plain_stack_measures_as_the_patch_probe():
    p = PRESETS["pad_warm"]
    a, b = measure_stack(Stack(p), 124.0), probe(p, 124.0)
    np.testing.assert_array_equal(a.vector, b.vector)


def test_layer_renders_are_reused_across_children():
    ctx = _ctx(seed=5)
    lay = genome._new_layer("pad", Patch(), ctx)
    first = layer_audio(lay, 124.0)
    again = layer_audio(Layer(**{**lay.__dict__}), 124.0)
    assert again is first  # the memo, by genome


def test_structure_distance():
    ctx = _ctx(seed=1)
    p = Patch()
    sub = genome._new_layer("sub", p, ctx)
    pad = genome._new_layer("pad", p, ctx)
    plain = Stack(p)
    assert struct_distance(plain, plain) == 0
    one = Stack(p, [sub])
    # a role (1) + half a layer (0.5) + its FX kinds
    assert struct_distance(plain, one) == 1.5 + len(genome.fx_kinds(one))
    two = Stack(p, [sub, pad])
    assert struct_distance(one, two) == pytest.approx(
        1 + 0.5 + len(genome.fx_kinds(two) ^ genome.fx_kinds(one))
    )
    swapped = Stack(p, [pad])
    assert struct_distance(one, swapped) >= 2  # sub vs pad: two roles apart


def test_a_row_round_trips_its_sound(tmp_path):
    stacks, tiles, store = _run(tmp_path, PRESETS["lead_supersaw"], "deep", seed=6)
    for st, t in zip(stacks, tiles, strict=True):
        row = store.get(t.id)
        assert st.layers and row["sound"]["layers"]
        chain = chain_of(st)
        for k in ("layers", "main_fx", "curves"):
            assert chain[k] == row["sound"][k], k
        assert chain["patch"] == row["patch"]
        for lay in row["sound"]["layers"]:
            assert lay["cells"] == [] and "octave" in lay["follow"]
        assert t.layered and t.chips[0]["role"] == "main"
        assert [c["role"] for c in t.chips[1:]] == st.roles()


def test_badges_lead_with_structure(tmp_path):
    _, tiles, _ = _run(tmp_path, Patch(), "deep", seed=1)
    firsts = [t.badges_genes[0] for t in tiles if t.badges_genes]
    assert sum(b.startswith("+") for b in firsts) >= 6, firsts


# ----------------------------------------------------------------- steering


def _mean_roles(stacks, role):
    return float(np.mean([role in s.roles() for s in stacks]))


def test_steering_biases_the_structure(tmp_path):
    subs, airs, plain = [], [], []
    for i, parent in enumerate(PARENTS):
        subs += _run(tmp_path, parent, "deep", "more_sub", seed=i)[0]
        airs += _run(tmp_path, parent, "deep", "brighter", seed=i)[0]
        plain += _run(tmp_path, parent, "deep", None, seed=i)[0]
    assert _mean_roles(subs, "sub") > _mean_roles(plain, "sub")
    assert _mean_roles(airs, "air") > _mean_roles(plain, "air")


# ------------------------------------------------------------------- budget


def _best_of(n, fn):
    best = float("inf")
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def test_deep_generation_time_budget(tmp_path):
    """Measured on the ARM dev machine (4 probe threads): deep ~0.5 s
    plain, ~0.7 s steered (30-candidate pool). Best of 3 with headroom for
    a loaded suite; the budget is 1 s."""
    for parent in (Patch(), PRESETS["pad_warm"]):
        _run(tmp_path, parent, "deep")  # warm: imports, threads, parent cache
        plain = _best_of(3, lambda p=parent: _run(tmp_path, p, "deep", seed=1))
        steered = _best_of(
            3, lambda p=parent: _run(tmp_path, p, "deep", "brighter", seed=1)
        )
        assert plain < 1.0, f"{parent.name}: {plain * 1000:.0f} ms"
        assert steered < 1.4, f"{parent.name}: {steered * 1000:.0f} ms"
