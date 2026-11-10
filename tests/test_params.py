"""The parameter registry: one completeness test, and properties at its
interface. These replace the cross-table consistency tests that used to
check each table against the others ("every genome field has a control",
"every field is classified for the worklet", the CURVABLE pin, ...): with
one registry there is nothing left to drift, only the registry to fill."""

from __future__ import annotations

from dataclasses import fields, replace

import numpy as np
import pytest

from synthlab.explore.breed import random_patch
from synthlab.params import (
    GENES,
    MOD_DESTINATIONS,
    MOD_GENE_DESTINATIONS,
    MOD_RANGES,
    PARAMS,
    PITCH_ROUTE,
    clamp,
    denorm,
    lerp,
    live,
    norm,
)
from synthlab.studio import worklet
from synthlab.studio.factory import FACTORY, get_factory
from synthlab.studio.library import LIBRARY, get_library
from synthlab.studio.paramui import GROUPS, PARAM_GROUPS, UI, worklet_blockers
from synthlab.synth import PRESETS, Patch

FIELDS = [f.name for f in fields(Patch)]


def _corpus() -> list[Patch]:
    rows = [get_factory(e["name"]) for e in FACTORY]
    rows += [get_library(e["name"]) for e in LIBRARY]
    return rows + list(PRESETS.values()) + [Patch()]


def _random(n: int, seed: int = 0) -> list[Patch]:
    r = np.random.default_rng(seed)
    return [random_patch(Patch(), rng=r) for _ in range(n)]


# --- completeness -------------------------------------------------------------


def test_every_patch_field_has_exactly_one_param_and_one_ui():
    """THE registration test. A new Patch field fails here until it has a
    Param (engine facts) and a Ui (a control, or the reason it has none)."""
    assert sorted(PARAMS) == sorted(FIELDS)
    assert sorted(UI) == sorted(FIELDS)
    for name in FIELDS:
        ui = UI[name]
        assert (ui.group is None) == bool(ui.why), f"{name}: a control or a reason"
        if ui.group is not None:
            assert ui.group in GROUPS, name
            assert ui.label, f"{name} needs a label"
        param = PARAMS[name]
        if param.kind == "cat":
            assert ui.group is None or ui.widget in ("pills", "select"), name
        # every control has a range to draw: the gene's (the slider IS the
        # gene), or a performance control's cap, which says why it is none
        if ui.group is not None:
            assert param.bounds is not None, name
            assert (param.gene is None) == bool(param.nogene), name
        assert ui.worklet in ("exact", "ignored", "default") or callable(ui.worklet)


def test_genome_order_is_pinned():
    """Seeded breeding draws random numbers walking GENES in this order: a
    reorder changes every seeded litter, so it has to be deliberate."""
    assert " ".join(GENES) == (
        "cutoff osc1_level osc1_detune osc2_level osc2_detune sub_level noise_level "
        "resonance filter_env filter_attack filter_decay filter_sustain "
        "filter_release key_track attack decay sustain release vibrato_rate "
        "vibrato_depth vibrato_delay pwm_rate pwm_depth tremolo_rate tremolo_depth "
        "drive glide unison_detune unison_spread filter_lfo_rate filter_lfo_depth "
        "pitch_env pitch_env_decay fm_amount ring_mod fm_ratio fm_index fm_decay "
        "pluck_damping pluck_bright wt_pos formant_amount formant_vowel "
        "formant_size mod_lfo_rate env_curve mod1_amt mod2_amt mod3_amt mod4_amt "
        # the oscillator extras, each at the end of its kind's run
        "sync_pitch fm4_ratio1 fm4_ratio2 fm4_ratio3 fm4_ratio4 fm4_level1 "
        "fm4_level2 fm4_level3 fm4_level4 fm4_decay1 fm4_decay2 fm4_decay3 "
        "fm4_decay4 fm4_feedback drift "
        "osc1_octave osc2_octave unison osc1 osc2 wt_table filter_type noise_color "
        "lfo_shape filter_lfo_shape lfo_sync filter_lfo_sync mod1_src mod1_dst "
        "mod2_src mod2_dst mod3_src mod3_dst mod4_src mod4_dst osc2_sync fm4_algo"
    )


def test_panels_keep_their_order_and_labels_are_unique():
    assert [g for g, _ in PARAM_GROUPS] == list(GROUPS)
    labels = [s.label for _, specs in PARAM_GROUPS for s in specs]
    assert len(labels) == len(set(labels))


# --- ranges ------------------------------------------------------------------


def test_cap_contains_gene_and_curve_and_mod_stay_inside_cap():
    for name, p in PARAMS.items():
        if p.gene is None and not p.nogene:
            assert p.cap is None and p.curve is None and p.mod is None, name
            continue
        if p.gene is None and p.kind == "cat":  # a hand-made choice (warp, voices)
            assert p.cap and p.curve is None and p.mod is None, name
            continue
        if p.gene is None:  # a performance control: not bred, still capped
            if p.kind == "cat":  # a hand-made choice (a menu)
                assert p.cap and p.curve is None and p.mod is None, name
                continue
            lo, hi = p.cap
            for extra in (p.curve, p.mod):
                if extra is not None:
                    assert lo <= extra[0] < extra[1] <= hi, name
            continue
        if p.kind == "cat":
            assert set(p.gene) <= set(p.cap), name
            continue
        lo, hi = p.cap
        assert lo <= p.gene[0] <= p.gene[1] <= hi, name
        for extra in (p.curve, p.mod):
            if extra is not None:
                assert lo <= extra[0] < extra[1] <= hi, name


def test_route_targets_all_have_a_span():
    assert tuple(MOD_RANGES) == MOD_DESTINATIONS
    # breeding never routes to "pitch" (and draws the same targets it always
    # did); a hand-made route to it survives clamp
    assert PARAMS["mod1_dst"].gene == MOD_GENE_DESTINATIONS
    assert PARAMS["mod1_dst"].cap == MOD_DESTINATIONS
    assert MOD_DESTINATIONS[-1] == PITCH_ROUTE
    assert PITCH_ROUTE not in MOD_GENE_DESTINATIONS
    routed = replace(Patch(), mod1_src="lfo", mod1_dst="pitch", mod1_amt=0.3)
    assert clamp(routed) == routed


def test_performance_controls_are_not_genes_and_say_why():
    """pitch_bend and bend_range are the player's, not the sound's: not in
    the genome (random_patch re-rolls every gene), capped, and drawn."""
    for name in ("pitch_bend", "bend_range"):
        assert name not in GENES
        assert PARAMS[name].nogene
        assert PARAMS[name].cap is not None
    assert PARAMS["pitch_bend"].curve == (-1.0, 1.0)
    assert PARAMS["bend_range"].cap == (0.0, 24.0)
    for p in _random(50):
        assert p.pitch_bend == 0.0 and p.bend_range == 2.0


# --- clamp -------------------------------------------------------------------


def test_clamp_is_identity_on_every_shipped_sound():
    for p in _corpus():
        assert clamp(p) == p, p.name


@pytest.mark.parametrize("value", [-1e9, 1e9, float("nan"), "junk", None, [3.0]])
def test_clamp_is_idempotent_and_lands_inside_every_cap(value):
    for name in FIELDS:
        if PARAMS[name].cap is None:
            continue
        once = clamp(replace(Patch(), **{name: value}))
        assert clamp(once) == once, name
        v = getattr(once, name)
        cap = PARAMS[name].cap
        if PARAMS[name].kind == "cat":
            assert v in cap, name
        else:
            assert cap[0] <= v <= cap[1], name


# --- norm / denorm / lerp ----------------------------------------------------


def test_denorm_inverts_norm_across_every_continuous_gene():
    for name, p in PARAMS.items():
        if p.kind not in ("lin", "log"):
            continue
        lo, hi = p.bounds
        for x in (0.0, 0.1, 0.5, 0.9, 1.0):
            v = denorm(name, x)
            assert lo - 1e-9 <= v <= hi + 1e-9, name
            assert norm(name, v) == pytest.approx(x, abs=1e-9), name
    # the log taper puts the musical low end where the travel is
    assert denorm("cutoff", 0.5) == pytest.approx((60.0 * 16000.0) ** 0.5)


def test_lerp_hits_both_ends():
    a, b = get_factory(FACTORY[0]["name"]), get_library(LIBRARY[0]["name"])
    assert lerp(a, b, 0.0) == a
    end = lerp(a, b, 1.0)
    for name in GENES:
        want, got = getattr(b, name), getattr(end, name)
        assert got == pytest.approx(want) if PARAMS[name].numeric else got == want


# --- liveness -----------------------------------------------------------------


def test_live_genes_are_real_genes():
    genes = set(GENES)
    for p in _corpus() + _random(200):
        assert live(p) <= genes


def test_depth_and_level_genes_stay_live_so_mutation_can_switch_modules_on():
    heard = live(Patch())
    for on_ramp in ("osc2", "vibrato_depth", "tremolo_depth", "formant_amount"):
        assert on_ramp in heard, on_ramp
    assert {"mod1_amt", "mod4_amt", "noise_level", "unison"} <= heard


# --- the live voice's gate -----------------------------------------------------


def test_worklet_blockers_is_the_gate_the_parity_corpus_runs():
    """test_live_parity_corpus.py measures, under node, every sound that
    `worklet.can_play` admits; that verdict must be `worklet_blockers`'."""
    for p in _corpus() + _random(300, seed=1):
        assert worklet.blockers(p) == worklet_blockers(p)
        assert worklet.can_play(p) == (worklet_blockers(p) == [])
        assert set(worklet_blockers(p)) <= set(FIELDS)


def test_a_field_nobody_classified_would_fall_back_to_wav():
    """ "default" is a Ui's default: forgetting to state a live-voice class
    can only cost a live key, never play one wrong."""
    from synthlab.studio.paramui import Ui

    assert Ui("Source").worklet == "default"
