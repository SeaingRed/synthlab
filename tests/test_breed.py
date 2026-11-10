from dataclasses import asdict, replace

import numpy as np

from synthlab import PRESETS, Patch, Synth
from synthlab.analysis import extract
from synthlab.explore.breed import crossover, generation, mutate
from synthlab.params import GENES, MOD_SLOTS, PARAMS
from synthlab.params import live as _active_genes

REESE = PRESETS["bass_reese"]


def test_mutate_is_deterministic_with_seed():
    a, a_badges = mutate(REESE, rng=np.random.default_rng(42))
    b, b_badges = mutate(REESE, rng=np.random.default_rng(42))
    assert a == b
    assert a_badges == b_badges


def test_mutate_changes_something_and_respects_bounds():
    rng = np.random.default_rng(1)
    child, badges = mutate(REESE, amount=0.8, rng=rng)
    assert child != REESE
    assert isinstance(badges, list)
    _assert_in_genome(asdict(child))


def _assert_in_genome(d: dict) -> None:
    for name in GENES:
        gene = PARAMS[name].gene
        if PARAMS[name].kind == "cat":
            assert d[name] in gene, name
        else:
            lo, hi = gene
            assert lo <= d[name] <= hi, f"{name}={d[name]} outside [{lo}, {hi}]"


def test_mutate_never_touches_voice_fx_or_name_semantics():
    child, _ = mutate(REESE, rng=np.random.default_rng(2))
    assert child.voice_fx == REESE.voice_fx


def test_crossover_fields_come_from_a_parent():
    a, b = PRESETS["bass_reese"], PRESETS["pad_warm"]
    child, badges = crossover(a, b, rng=np.random.default_rng(3))
    assert badges == ["mix"]
    da, db, dc = asdict(a), asdict(b), asdict(child)
    for k in dc:
        if k in ("name", "voice_fx"):
            continue
        assert dc[k] == da[k] or dc[k] == db[k]


def test_generation_returns_n_renderable_children():
    kids = generation([REESE, PRESETS["bass_acid"]], n=8, rng=np.random.default_rng(4))
    assert len(kids) == 8
    for kid, badges in kids:
        assert isinstance(kid, Patch)
        assert isinstance(badges, list)
        audio = Synth(kid).note("A1", 0.5)
        assert np.all(np.isfinite(audio))


def test_lineage_names_stay_bounded():
    p = PRESETS["bass_reese"]
    for _ in range(6):
        p, _ = mutate(p, rng=np.random.default_rng(3))
        p, _ = crossover(p, PRESETS["bass_acid"], rng=np.random.default_rng(4))
    assert len(p.name) <= 24


# --- audible mutation: dead genes are skipped, changes are guaranteed ------


def test_active_genes_excludes_dead_modules():
    # default Patch: osc2 off, no noise, unison 1, no vibrato/tremolo,
    # saw osc (PWM inaudible)
    live = _active_genes(Patch())
    for dead in (
        "osc2_level",
        "osc2_detune",
        "osc2_octave",
        "noise_color",
        "unison_detune",
        "unison_spread",
        "vibrato_rate",
        "vibrato_delay",
        "tremolo_rate",
        "pwm_rate",
        "pwm_depth",
    ):
        assert dead not in live, dead
    for on in ("cutoff", "resonance", "attack", "osc1", "vibrato_depth"):
        assert on in live, on


def test_active_genes_follows_module_state():
    p = replace(Patch(), osc2="square", unison=3, vibrato_depth=0.2)
    live = _active_genes(p)
    for name in (
        "osc2_level",
        "osc2_detune",
        "osc2_octave",
        "unison_detune",
        "unison_spread",
        "vibrato_rate",
        "vibrato_delay",
        "pwm_depth",
        "pwm_rate",
    ):
        assert name in live, name


def test_mutate_only_changes_live_genes():
    parent = Patch()
    live = _active_genes(parent)
    pd = asdict(parent)
    for seed in range(30):
        child, _ = mutate(parent, rng=np.random.default_rng(seed))
        cd = asdict(child)
        changed = {k for k in cd if k not in ("name", "voice_fx") and cd[k] != pd[k]}
        # a mod slot's amount (the live on-ramp) switches the whole route on
        # as one move: source and target come with it, and are heard
        routed = {
            f"{s}_{part}"
            for s in MOD_SLOTS
            if f"{s}_amt" in changed and cd[f"{s}_src"] != "none"
            for part in ("src", "dst")
        }
        # a depth that becomes real wakes the rate it needs (a 0 Hz tremolo
        # is a fixed gain, so drifting its depth alone was inaudible)
        woken = {
            rate
            for depth, rate in (
                ("tremolo_depth", "tremolo_rate"),
                ("filter_lfo_depth", "filter_lfo_rate"),
            )
            if depth in changed
        }
        allowed = live | routed | woken
        dead = changed - allowed
        assert not dead, f"seed {seed}: dead genes mutated: {dead}"


def test_mutate_guarantees_multiple_audible_changes():
    parent = Patch()
    pd = asdict(parent)
    for seed in range(30):
        child, badges = mutate(parent, rng=np.random.default_rng(seed))
        cd = asdict(child)
        changed = {k for k in cd if k not in ("name", "voice_fx") and cd[k] != pd[k]}
        assert len(changed) >= 2, f"seed {seed}: only {changed}"
        assert badges, f"seed {seed}: no badges"


def test_generation_from_default_patch_sounds_different():
    """The complaint this guards against: offspring that are genetically
    different but perceptually identical to the parent."""
    parent = Patch()
    pf = extract(Synth(parent).note("A1", 0.6)).vector()
    kids = generation([parent], n=8, rng=np.random.default_rng(42))
    distances = []
    for kid, _ in kids:
        f = extract(Synth(kid).note("A1", 0.6)).vector()
        distances.append(float(np.linalg.norm(f - pf)))
    audibly_different = sum(d > 0.15 for d in distances)
    assert audibly_different >= 5, f"distances: {[round(d, 3) for d in distances]}"


def test_jump_mutations_change_architecture():
    """A jump is a categorical/module move, not a drift: across seeds it
    must switch osc1 shapes, toggle osc2, swap filters and switch modules
    on — and any module it switches on has live, non-default genes."""
    parent = Patch()
    pd = asdict(parent)
    kinds = set()
    for seed in range(40):
        child, badges = mutate(parent, rng=np.random.default_rng(seed), jump=True)
        assert badges[0] == "jump"
        cd = asdict(child)
        if cd["osc1"] != pd["osc1"]:
            kinds.add("osc1")
        if cd["osc2"] != pd["osc2"]:
            kinds.add("osc2")
            assert cd["osc2_level"] >= 0.35
        if cd["filter_type"] != pd["filter_type"]:
            kinds.add("filter")
        if cd["sub_level"] > 0.4 or cd["unison"] >= 2 or cd["drive"] > 2:
            kinds.add("module")
    assert {"osc1", "osc2", "filter", "module"} <= kinds, kinds


def test_random_patch_stays_in_range_and_renders():
    from synthlab.explore.breed import random_patch

    for seed in range(10):
        wild = random_patch(Patch(), rng=np.random.default_rng(seed))
        _assert_in_genome(asdict(wild))
        audio = Synth(wild).note("A2", 0.4)
        # "renders": not silence. A 0.6 s attack on this 0.4 s note is quiet:
        # seed 2 (with the fm4 / sync genes in the draws: its 1.5 s release
        # leaves the gate-less note 50 ms of hold, under a 259 Hz lowpass)
        # peaks at 0.0026
        assert np.all(np.isfinite(audio)) and np.abs(audio).max() > 0.001


def test_generation_has_a_wildcard_and_wider_spread():
    """Sibling-to-sibling distances must be wide — variety within a
    litter, not just distance from the parent."""
    parent = Patch()
    kids = generation([parent], n=8, rng=np.random.default_rng(7))
    assert any("wild" in badges for _, badges in kids)
    assert any("jump" in badges for _, badges in kids)
    vecs = [extract(Synth(k).note("A1", 0.6)).vector() for k, _ in kids]
    pair = [
        float(np.linalg.norm(vecs[i] - vecs[j]))
        for i in range(len(vecs))
        for j in range(i + 1, len(vecs))
    ]
    assert float(np.mean(pair)) > 0.25, f"mean sibling distance {np.mean(pair):.3f}"
    assert sum(d > 0.15 for d in pair) >= len(pair) * 0.6


def test_generation_outcrosses_with_donors_and_wildness_scales():
    """Library donors bring in whole other characters (badge "cross:…"),
    and a wild litter makes double jumps where a tame one drifts."""
    parent = Patch()
    donors = [PRESETS[k] for k in sorted(PRESETS)][:6]
    badges = [
        b
        for seed in range(6)
        for _, bs in generation(
            [parent], n=8, amount=1.3, rng=np.random.default_rng(seed), donors=donors
        )
        for b in bs
    ]
    assert any(b.startswith("cross:") for b in badges), badges
    assert "wild" in badges and "jump" in badges

    # Wildness is a *statistical* property: a wild litter drifts further from
    # the parent on average, not on every individual seed (measured: wild
    # beats tame on roughly half of single seeds, but is ~25% wider on the
    # mean). Asserting one seed passed only by luck and broke the moment new
    # genes shifted the RNG stream, so average several instead.
    pf = extract(Synth(parent).note("A1", 0.6)).vector()

    def spread(amount, seed):
        kids = generation([parent], n=8, amount=amount, rng=np.random.default_rng(seed))
        vecs = [extract(Synth(k).note("A1", 0.6)).vector() for k, _ in kids[:-1]]
        return float(np.mean([np.linalg.norm(v - pf) for v in vecs]))

    seeds = range(6)
    tame = float(np.mean([spread(0.35, s) for s in seeds]))
    wild = float(np.mean([spread(2.0, s) for s in seeds]))
    assert wild > tame, f"wild {wild:.2f} did not out-drift tame {tame:.2f}"
