"""What the breeder measures, and what wildness means.

Every threshold here was measured against the earlier breeder, which
decided everything on a bare A1 0.6 s note nobody hears, its
distance was ~75 % crest factor, its novelty gate rejected nothing, "tame"
changed ~19 genes a child and "cleaner" had no signal.
"""

from dataclasses import asdict, replace

import numpy as np

from synthlab import PRESETS
from synthlab.analysis import texture
from synthlab.explore.breed import (
    CROSS_BLOCKS,
    crossover,
    generation,
    litter_profile,
    mutate,
)
from synthlab.explore.probe import (
    PROBE_GATE,
    PROBE_MAX_TAIL,
    distance,
    log_flatness,
    probe,
    probe_audio,
)
from synthlab.params import GENES, MOD_GENE_SLOTS, MOD_SLOTS, PARAMS
from synthlab.synth import Patch, Synth

SR = 44100


# ------------------------------------------------------------------ probe


def test_probe_hears_the_release_and_the_listening_register():
    """A3, a phrase-length gate, and the patch's release after it (capped):
    a 0.6 s buffer squashed every pad into its first half-second."""
    short = probe_audio(Patch(release=0.1))
    pad = probe_audio(Patch(attack=0.3, release=2.5))
    assert len(short) / SR < PROBE_GATE + 0.3
    assert abs(len(pad) / SR - (PROBE_GATE + PROBE_MAX_TAIL)) < 0.05
    assert texture(pad).env_length > texture(short).env_length + 0.4
    # the note sits at A3, not A1: the fundamental is 220 Hz
    x = Synth(Patch(osc1="sine", filter_type="none", sub_level=0.0)).note("A3", 0.5)
    spec = np.abs(np.fft.rfft(x.mean(axis=1)))
    assert abs(np.argmax(spec) * SR / len(x) - 220) < 5
    m = probe(Patch(osc1="sine", filter_type="none", sub_level=0.0))
    assert 180 < m.features.centroid < 300


def test_probe_resolves_synced_lfos_at_the_session_tempo():
    """Without at_bpm every lfo_sync gene was inaudible to the breeder."""
    p = Patch(filter_lfo_depth=3000.0, filter_lfo_sync="1/16", cutoff=600.0)
    slow, fast = probe_audio(p, bpm=60.0), probe_audio(p, bpm=180.0)
    assert not np.allclose(slow, fast)


def test_texture_measures_mean_what_they_say():
    clean = texture(probe_audio(Patch(osc1="sine", filter_type="none")))
    noisy = texture(probe_audio(Patch(noise_level=0.3)))
    assert log_flatness(noisy) > log_flatness(clean) + 2
    swept = texture(
        probe_audio(Patch(cutoff=400.0, filter_lfo_depth=3000.0, filter_lfo_rate=6.0))
    )
    static = texture(probe_audio(Patch(cutoff=400.0)))
    assert swept.movement > 2 * static.movement
    driven = texture(probe_audio(Patch(drive=4.0)))
    assert driven.frame_crest < texture(probe_audio(Patch())).frame_crest - 1.0
    silent = texture(np.zeros((SR, 2)))
    assert silent.loudness_db < -100 and silent.env_length == 0.0


def test_standardised_distance_is_not_one_feature():
    """Raw, crest factor was ~75 % of every distance. Standardised, no
    single measure may carry most of it across a litter."""
    kids = generation([Patch()], n=16, amount=0.75, rng=np.random.default_rng(0))
    vecs = np.array([probe(k).vector for k, _ in kids])
    diff2 = ((vecs[:, None, :] - vecs[None, :, :]) ** 2).sum(axis=(0, 1))
    assert diff2.max() / diff2.sum() < 0.35, diff2 / diff2.sum()


# --------------------------------------------------------------- wildness


def test_tame_is_tame():
    """No random card, no donor cross, rare jumps, few genes a child."""
    donors = [PRESETS[k] for k in sorted(PRESETS)][:6]
    changed, badges = [], []
    for parent in (Patch(), PRESETS["bass_reese"], PRESETS["pad_warm"]):
        for seed in range(4):
            litter = generation(
                [parent],
                n=20,
                amount=0.35,
                rng=np.random.default_rng(seed),
                donors=donors,
            )
            pd = asdict(parent)
            for kid, bs in litter:
                kd = asdict(kid)
                changed.append(sum(kd[g] != pd[g] for g in GENES))
                badges += bs
    assert float(np.median(changed)) <= 6, np.median(changed)
    assert "wild" not in badges
    assert not any(b.startswith("cross:") for b in badges)
    assert badges.count("jump") < 0.2 * len(changed)


def test_wildness_scales_jumps_and_random_cards():
    tame, normal, wild, feral = (litter_profile(a) for a in (0.35, 0.75, 1.3, 2.0))
    assert [p.level for p in (tame, normal, wild, feral)] == [
        "tame",
        "normal",
        "wild",
        "feral",
    ]
    for lo, hi in ((tame, normal), (normal, wild), (wild, feral)):
        assert lo.jump_prob < hi.jump_prob
        assert lo.random_rate < hi.random_rate
        assert lo.drift_prob < hi.drift_prob

    def randoms(amount):
        kids = generation([Patch()], n=20, amount=amount, rng=np.random.default_rng(1))
        return sum(b == ["wild"] for _, b in kids)

    assert randoms(0.35) == 0 < randoms(0.75) < randoms(2.0)


# ------------------------------------------------------- inaudible genes


def test_a_mod_slot_turns_on_as_one_move():
    """Drifting mod*_amt while its source is "none" changed nothing."""
    parent = Patch()
    routed = 0
    for seed in range(60):
        child, _ = mutate(parent, amount=1.0, rng=np.random.default_rng(seed))
        for s in MOD_SLOTS:
            if getattr(child, f"{s}_amt") != getattr(parent, f"{s}_amt"):
                assert getattr(child, f"{s}_src") != "none", (seed, s)
                assert abs(getattr(child, f"{s}_amt")) >= 0.3
                routed += 1
    assert routed > 0


def test_tremolo_depth_brings_a_rate_you_can_hear():
    parent = Patch(tremolo_rate=0.0, tremolo_depth=0.0)
    woke = 0
    for seed in range(80):
        child, _ = mutate(parent, amount=1.0, rng=np.random.default_rng(seed))
        if child.tremolo_depth >= 0.01:
            assert child.tremolo_rate >= 0.5, seed
            woke += 1
    assert woke > 0


# -------------------------------------------------------------- crossover


def test_cross_blocks_partition_the_genome():
    seen = [g for block in CROSS_BLOCKS for g in block]
    assert sorted(seen) == sorted(GENES)
    assert len(seen) == len(set(seen))
    blocks = {b[0]: b for b in CROSS_BLOCKS}
    assert {"osc2_level", "osc2_octave"} <= set(blocks["osc2"])
    assert {"cutoff", "resonance", "filter_env"} <= set(blocks["filter_type"])
    for s in MOD_GENE_SLOTS:  # the bred four (of twelve slots)
        assert (f"{s}_src", f"{s}_dst", f"{s}_amt") in CROSS_BLOCKS


def test_crossover_keeps_modules_whole():
    a = PRESETS["bass_reese"]
    b = replace(
        PRESETS["pad_warm"],
        osc2="square",
        osc2_level=0.33,
        osc2_octave=2,
        mod1_src="lfo",
        mod1_dst="drive",
        mod1_amt=0.9,
    )
    da, db = asdict(a), asdict(b)
    for seed in range(20):
        child, badges = crossover(a, b, rng=np.random.default_rng(seed))
        assert badges == ["mix"]
        dc = asdict(child)
        for block in CROSS_BLOCKS:
            from_a = all(dc[g] == da[g] for g in block)
            from_b = all(dc[g] == db[g] for g in block)
            assert from_a or from_b, (seed, block)


# ----------------------------------------------------------------- badges


def test_badges_describe_the_change_from_the_parent():
    """They were computed against the donor-crossed intermediate, which the
    user never saw; donor names were cut to 10 chars (trailing spaces)."""
    parent = Patch()
    donors = [replace(PRESETS["pad_warm"], name="Glass Pad Keys Long")]
    pd = asdict(parent)
    crossed = 0
    for seed in range(12):
        kids = generation(
            [parent], n=8, amount=1.3, rng=np.random.default_rng(seed), donors=donors
        )
        for kid, badges in kids:
            kd = asdict(kid)
            for b in badges:
                if b.startswith("cross:"):
                    assert b == "cross:Glass Pad Keys Long"
                    crossed += 1
                for sep in ("→", "↑", "↓"):
                    field, hit, _ = b.partition(sep)
                    if hit and field in pd:
                        assert kd[field] != pd[field], (seed, b)
                        if PARAMS[field].kind == "cat":
                            assert b == f"{field}→{kd[field]}"
    assert crossed > 0


def test_near_clone_is_close_and_a_real_move_is_not():
    """Scale sanity for the novelty gate (0.75): an inaudible change sits
    far under it, a module switched on far over it."""
    base = Patch()
    a = probe(base).vector
    assert distance(probe(replace(base, glide=0.2)).vector, a) < 0.1
    assert distance(probe(replace(base, osc1="square", noise_level=0.3)).vector, a) > 1
