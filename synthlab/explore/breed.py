"""Evolutionary patch breeding. A Patch IS the genome: a flat, JSON-able
dataclass. Numeric genes drift within musical bounds, categorical genes
flip rarely, `voice_fx` and `name` are not part of the genome.

All randomness comes through an injected numpy Generator so seeded runs
reproduce exactly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace

import numpy as np

from ..params import (
    CAT_GENES,
    GENES,
    MOD_GENE_DESTINATIONS,
    MOD_GENE_SLOTS,
    MOD_GENE_SOURCES,
    NUMERIC_GENES,
    PARAMS,
    live,
)
from ..synth import Patch
from .probe import PROBE_BPM, Probe, distance, log_flatness, probe_many
from .scale import BREED_VECTOR

log = logging.getLogger(__name__)

# The genome — every gene's kind and (lo, hi) range or choices — is
# synthlab.params.PARAMS: LOG genes mutate multiplicatively (musical for
# Hz), LIN genes additively, INT genes step, CAT genes flip. Which genes are
# worth mutating on a given patch is params.live(). GENES is walked in the
# registry's order, which seeded runs depend on.

_MUTATE_PROB = 0.45  # mutate()'s default chance each live numeric gene drifts
_FLIP_PROB = 0.12  # chance each categorical gene flips (scaled by amount)
_MIN_CHANGES = 2  # every child must differ audibly in at least this many genes


@dataclass(frozen=True)
class Litter:
    """What a wildness level lets a litter do. `amount` alone only scaled
    the drift *size*: a tame litter still jumped half the time, crossed with
    library donors, re-rolled a random card and drifted ~19 genes per child
    — "tame" wasn't. Each level now also sets how often children leave the
    parent's architecture and how many genes one child may touch."""

    level: str  # the studio's name for it: tame / normal / wild / feral
    random_rate: float  # share of the litter re-rolled from scratch ("wild")
    donor_prob: float  # chance a child is first crossed with a library donor
    jump_prob: float  # chance a child makes an architectural jump (_jump)
    double_jump: float  # chance a jump makes a second move at once
    drift_prob: float  # chance each live numeric gene drifts
    max_genes: int | None  # most genes one child may change (None: no cap)


# amount -> Litter, one per studio WILDNESS level (tame .35, normal .75,
# wild 1.3, feral 2.0); an amount takes the highest level it reaches the
# midpoint to, so any amount maps onto one of the four
_LEVELS: tuple[tuple[float, Litter], ...] = (
    (0.0, Litter("tame", 0.0, 0.0, 0.10, 0.0, 0.15, 5)),  # tame: nudges only
    (0.55, Litter("normal", 0.05, 0.25, 0.35, 0.0, 0.30, 10)),  # normal
    (1.0, Litter("wild", 0.10, 0.35, 0.55, 0.5, 0.45, None)),  # wild
    (1.65, Litter("feral", 0.20, 0.45, 0.70, 0.6, 0.55, None)),  # feral
)


def litter_profile(amount: float) -> Litter:
    profile = _LEVELS[0][1]
    for threshold, level in _LEVELS:
        if amount >= threshold:
            profile = level
    return profile


# genes with an outsized perceptual effect: boosted drift probability, and
# the pool forced changes are drawn from when a mutation lands too gently
_IMPACT_GENES = (
    "cutoff",
    "resonance",
    "filter_env",
    "attack",
    "decay",
    "release",
    "drive",
    "sub_level",
)
_IMPACT_BOOST = 1.5


def _rng(rng: np.random.Generator | None) -> np.random.Generator:
    return rng if rng is not None else np.random.default_rng()


def _lineage_name(base: str, suffix: str) -> str:
    if len(base) + len(suffix) > 24:
        return base[: 24 - len(suffix)] + suffix
    return base + suffix


_CATEGORICAL_SCORE = 1.0  # a categorical flip always ranks as a big change


def _badges_for(changes: dict, before: dict, limit: int = 2) -> list[str]:
    """Rank changed fields by |delta| / range (categorical flips score
    max) and format the top `limit` as short badge strings."""
    scored: list[tuple[float, str]] = []
    for field, new in changes.items():
        old = before[field]
        if new == old:
            continue
        if PARAMS[field].kind == "cat":
            scored.append((_CATEGORICAL_SCORE, f"{field}→{new}"))
        else:
            lo, hi = PARAMS[field].gene
            span = (hi - lo) or 1.0
            score = abs(float(new) - float(old)) / span
            arrow = "↑" if new > old else "↓"
            scored.append((score, f"{field}{arrow}"))
    scored.sort(key=lambda t: -t[0])
    return [badge for _, badge in scored[:limit]]


def _drift(name: str, value, amount: float, r: np.random.Generator, forced=False):
    """One gene's mutated value. Forced drifts use a floored magnitude so
    a guaranteed change is a *hearable* change, not a rounding error."""
    param = PARAMS[name]
    lo, hi = param.gene
    if param.kind == "log":
        if forced:
            exponent = float(r.choice([-1.0, 1.0])) * (0.35 + 0.45 * r.random())
        else:
            exponent = float(r.normal(0.0, 0.6 * amount))
        return float(np.clip(value * float(np.exp(exponent)), lo, hi))
    if param.kind == "lin":
        if forced:
            span = hi - lo
            delta = float(r.choice([-1.0, 1.0])) * (0.12 + 0.15 * r.random()) * span
        else:
            delta = float(r.normal(0.0, 0.2 * amount * (hi - lo)))
        return float(np.clip(value + delta, lo, hi))
    step = int(r.choice([-1, 1])) if forced else int(r.integers(-1, 2))
    return int(np.clip(value + step, lo, hi))


def _random_value(name: str, r: np.random.Generator):
    """A fresh value anywhere in a gene's range (log-uniform for LOG genes)."""
    param = PARAMS[name]
    if param.kind == "cat":
        return param.gene[int(r.integers(len(param.gene)))]
    lo, hi = param.gene
    if param.kind == "log":
        return float(np.exp(r.uniform(np.log(lo), np.log(hi))))
    if param.kind == "lin":
        return float(r.uniform(lo, hi))
    return int(r.integers(lo, hi + 1))


# genes that come alive with a module: when a jump switches one on, these
# are re-rolled so the new module is actually heard, not left at a silent
# default
_MODULE_GENES: dict[str, tuple[str, ...]] = {
    "osc2": ("osc2_level", "osc2_detune", "osc2_octave"),
    "sub_level": (),
    "noise_level": ("noise_color",),
    "fm_amount": ("fm_ratio", "fm_index", "fm_decay"),
    "vibrato_depth": ("vibrato_rate",),
    "pwm_depth": ("pwm_rate",),
    "tremolo_depth": ("tremolo_rate",),
    "filter_lfo_depth": ("filter_lfo_rate",),
    **{f"{s}_amt": (f"{s}_src", f"{s}_dst") for s in MOD_GENE_SLOTS},
    "drive": (),
    "unison": ("unison_detune", "unison_spread"),
}
_SHAPE_GENES = {
    "fm": ("fm_ratio", "fm_index"),
    "pluck": ("pluck_damping", "pluck_bright"),
}
_SLOT_OF_AMT = {f"{s}_amt": s for s in MOD_GENE_SLOTS}
_SLOT_OF_SRC = {f"{s}_src": s for s in MOD_GENE_SLOTS}


def _route_on(slot: str, r: np.random.Generator) -> dict:
    """Switch a mod slot on as ONE move: a real source, a destination and
    an amount you can hear. Drifting `amt` alone while the source is "none"
    changed the genome and not the sound (a sweep over the genes: every
    such drift measured inaudible)."""
    sources = [s for s in MOD_GENE_SOURCES if s != "none"]
    sign = float(r.choice([-1.0, 1.0]))
    return {
        f"{slot}_src": sources[int(r.integers(len(sources)))],
        f"{slot}_dst": MOD_GENE_DESTINATIONS[
            int(r.integers(len(MOD_GENE_DESTINATIONS)))
        ],
        f"{slot}_amt": sign * float(r.uniform(0.3, 1.0)),
    }


def _wake(changes: dict, d: dict, r: np.random.Generator) -> None:
    """Rate genes a depth needs to be heard. A tremolo whose rate sits at
    0 Hz (its range floor, and the default) is a fixed gain, so drifting its
    depth was inaudible; a filter LFO under 0.5 Hz barely moves in a note.
    When a change makes the depth real, re-roll a rate that is heard."""
    merged = {**d, **changes}
    if (
        "tremolo_depth" in changes
        and merged["tremolo_depth"] >= 0.01
        and merged["tremolo_rate"] < 0.5
    ):
        changes["tremolo_rate"] = float(r.uniform(1.0, 9.0))
    if (
        "filter_lfo_depth" in changes
        and merged["filter_lfo_depth"] >= 5.0
        and merged["filter_lfo_sync"] == "off"
        and merged["filter_lfo_rate"] < 0.5
    ):
        changes["filter_lfo_rate"] = float(r.uniform(0.5, 8.0))


def _jump(d: dict, r: np.random.Generator) -> dict:
    """One architectural move: a new osc1 shape, osc2 toggled, the filter
    swapped, or a whole module switched on at a random strength. These are
    the changes gentle drift never makes — the "it all sounds like the
    parent" complaint."""
    kind = ["osc1", "osc2", "filter", "module"][int(r.integers(4))]
    changes: dict = {}
    if kind == "osc1":
        shapes = [s for s in PARAMS["osc1"].gene if s != d["osc1"]]
        shape = shapes[int(r.integers(len(shapes)))]
        changes["osc1"] = shape
        for g in _SHAPE_GENES.get(shape, ()):
            changes[g] = _random_value(g, r)
    elif kind == "osc2":
        if d["osc2"] is None:
            shapes = [s for s in PARAMS["osc2"].gene if s is not None]
            changes["osc2"] = shapes[int(r.integers(len(shapes)))]
            changes["osc2_level"] = float(r.uniform(0.35, 0.9))
            changes["osc2_detune"] = float(r.uniform(-12.0, 12.0))
            changes["osc2_octave"] = _random_value("osc2_octave", r)
        else:
            changes["osc2"] = None
    elif kind == "filter":
        types = [t for t in PARAMS["filter_type"].gene if t != d["filter_type"]]
        changes["filter_type"] = types[int(r.integers(len(types)))]
        changes["cutoff"] = _random_value("cutoff", r)
        changes["resonance"] = float(r.uniform(0.1, 0.8))
    else:
        names = [n for n in _MODULE_GENES if n != "osc2"]
        gate = names[int(r.integers(len(names)))]
        if gate in _SLOT_OF_AMT:  # a mod route: source, target and depth together
            changes.update(_route_on(_SLOT_OF_AMT[gate], r))
        else:
            lo, hi = PARAMS[gate].gene
            # push the gate well into its range so the module is unmistakable
            changes[gate] = (
                int(r.integers(2, hi + 1))
                if PARAMS[gate].kind == "int"
                else float(lo + (0.4 + 0.6 * r.random()) * (hi - lo))
            )
            for g in _MODULE_GENES[gate]:
                changes[g] = _random_value(g, r)
    _wake(changes, d, r)
    return {k: v for k, v in changes.items() if v != d[k]}


def random_patch(base: Patch, rng: np.random.Generator | None = None) -> Patch:
    """A wildcard: every gene re-rolled inside its range, but with the
    mixer kept sane (osc1 audible, sub/noise as seasoning) so it is a
    *sound*, not a noise floor."""
    r = _rng(rng)
    d = asdict(base)
    changes = {name: _random_value(name, r) for name in GENES}
    changes["osc1_level"] = float(r.uniform(0.6, 1.0))
    changes["sub_level"] = float(r.uniform(0.0, 0.6))
    changes["noise_level"] = float(r.uniform(0.0, 0.15))
    changes["drive"] = float(r.uniform(1.0, 2.5))
    changes["attack"] = min(changes["attack"], 0.6)
    changes["release"] = min(changes["release"], 1.5)
    changes = {k: v for k, v in changes.items() if v != d[k]}
    changes["name"] = _lineage_name(base.name, "?")
    return replace(base, **changes)


def _move(name: str, d: dict, amount: float, r: np.random.Generator) -> dict:
    """One gene's move. A mod slot's amount or source leaving "off" turns
    the whole route on (`_route_on`) rather than drifting one inaudible
    number."""
    if name in _SLOT_OF_AMT and d[f"{_SLOT_OF_AMT[name]}_src"] == "none":
        return _route_on(_SLOT_OF_AMT[name], r)
    if PARAMS[name].kind == "cat":
        choices = PARAMS[name].gene
        new = choices[int(r.integers(len(choices)))]
        slot = _SLOT_OF_SRC.get(name)
        if slot and d[name] == "none" and new != "none":
            return _route_on(slot, r) | {name: new}
        return {name: new}
    return {name: _drift(name, d[name], amount, r)}


def mutate(
    patch: Patch,
    amount: float = 0.5,
    rng: np.random.Generator | None = None,
    jump: bool = False,
    *,
    drift_prob: float | None = None,
    max_genes: int | None = None,
    double_jump: float | None = None,
) -> tuple[Patch, list[str]]:
    """`drift_prob`, `max_genes` and `double_jump` come from the litter's
    wildness (`litter_profile`); left out, mutate() keeps its standalone
    behaviour (the panel dice uses that)."""
    r = _rng(rng)
    changes: dict = {}
    d = asdict(patch)
    heard = live(patch)
    if jump:
        changes.update(_jump(d, r))
        two = (
            amount > 0.8 and r.random() < 0.5
            if double_jump is None
            else double_jump > 0 and r.random() < double_jump
        )
        if two:  # wild litters: two moves at once
            changes.update(_jump({**d, **changes}, r))
        # the jump may have switched modules on: their genes are live now
        heard = live(replace(patch, **changes))

    p_drift = _MUTATE_PROB if drift_prob is None else drift_prob
    p_flip = _FLIP_PROB * amount * 2 * (p_drift / _MUTATE_PROB)
    moves = []
    for name in NUMERIC_GENES:
        if name not in heard or name in changes:
            continue
        p = p_drift * (_IMPACT_BOOST if name in _IMPACT_GENES else 1.0)
        if r.random() < p:
            moves.append(name)
    for name in CAT_GENES:
        if name in heard and name not in changes and r.random() < p_flip:
            moves.append(name)
    if max_genes is not None:
        budget = max(0, max_genes - len(changes))
        if len(moves) > budget:
            keep = set(r.choice(len(moves), size=budget, replace=False).tolist())
            moves = [m for i, m in enumerate(moves) if i in keep]
    base = {**d, **changes}
    for name in moves:
        if name not in changes:  # a route move may already have set it
            changes.update(_move(name, base, amount, r))

    # a mutation that lands only no-op values (clip at a bound, same
    # categorical redrawn, int step clipped away) leaves the child
    # indistinguishable from the parent: force impactful drifts until at
    # least _MIN_CHANGES genes genuinely differ
    effective = {k for k, v in changes.items() if v != d[k]}
    pool = [g for g in _IMPACT_GENES if g in heard and g not in effective]
    while len(effective) < _MIN_CHANGES and pool:
        name = pool.pop(int(r.integers(len(pool))))
        v = _drift(name, d[name], amount, r, forced=True)
        if v != d[name]:
            changes[name] = v
            effective.add(name)

    _wake(changes, d, r)
    changes = {k: v for k, v in changes.items() if v != d[k]}
    badges = _badges_for(changes, d)
    if jump:
        badges = ["jump"] + badges[:1]
    changes["name"] = _lineage_name(patch.name, "*")
    return replace(patch, **changes), badges


# Crossover swaps whole modules, never single genes: a child that took
# osc2's shape from one parent and its level/octave from the other, or a
# filter type from A with B's cutoff and envelope, is neither parent's
# module — just an incoherent one. Every gene lives in exactly one block;
# genes no module claims (sub, drive, glide) travel alone.
_MODULE_BLOCKS: tuple[tuple[str, ...], ...] = (
    ("osc1", "osc1_level", "osc1_detune", "osc1_octave"),
    # hard sync travels with osc2 (in a block already drawn, so no seeded
    # crossover moves)
    (
        "osc2",
        "osc2_level",
        "osc2_detune",
        "osc2_octave",
        "fm_amount",
        "ring_mod",
        "osc2_sync",
        "sync_pitch",
    ),
    ("fm_ratio", "fm_index", "fm_decay"),
    ("pluck_damping", "pluck_bright"),
    ("wt_table", "wt_pos"),
    (
        "filter_type",
        "cutoff",
        "resonance",
        "filter_env",
        "key_track",
        "filter_attack",
        "filter_decay",
        "filter_sustain",
        "filter_release",
    ),
    ("filter_lfo_rate", "filter_lfo_depth", "filter_lfo_shape", "filter_lfo_sync"),
    ("attack", "decay", "sustain", "release", "env_curve"),
    ("noise_level", "noise_color"),
    ("vibrato_rate", "vibrato_depth", "vibrato_delay"),
    ("pwm_rate", "pwm_depth"),
    ("tremolo_rate", "tremolo_depth"),
    ("unison", "unison_detune", "unison_spread"),
    ("pitch_env", "pitch_env_decay"),
    ("formant_amount", "formant_vowel", "formant_size"),
    ("mod_lfo_rate", "lfo_shape", "lfo_sync"),
    *((f"{s}_src", f"{s}_dst", f"{s}_amt") for s in MOD_GENE_SLOTS),
)
# the fm4 shape: one block, drawn after every other one so the
# draws before it stay where seeded crossovers had them
_LATE_BLOCKS: tuple[tuple[str, ...], ...] = (
    (
        "fm4_algo",
        "fm4_feedback",
        *(f"fm4_{k}{i}" for k in ("ratio", "level", "decay") for i in range(1, 5)),
    ),
)
_BLOCKED = {g for block in _MODULE_BLOCKS + _LATE_BLOCKS for g in block}
CROSS_BLOCKS: tuple[tuple[str, ...], ...] = (
    _MODULE_BLOCKS + tuple((g,) for g in GENES if g not in _BLOCKED) + _LATE_BLOCKS
)


def crossover(
    a: Patch, b: Patch, rng: np.random.Generator | None = None
) -> tuple[Patch, list[str]]:
    r = _rng(rng)
    db = asdict(b)
    picked = {}
    for block in CROSS_BLOCKS:
        if r.random() >= 0.5:
            picked.update({g: db[g] for g in block})
    picked["name"] = _lineage_name(a.name, "x" + b.name[:6])
    return replace(a, **picked), ["mix"]


def _genome_diff(child: Patch, origin: Patch) -> dict:
    dc, do = asdict(child), asdict(origin)
    return {g: dc[g] for g in GENES if dc[g] != do[g]}


def generation(
    parents: list[Patch],
    n: int = 8,
    amount: float = 0.75,
    rng: np.random.Generator | None = None,
    donors: list[Patch] | None = None,
    origins: list[int] | None = None,
    bias: str | None = None,
) -> list[tuple[Patch, list[str]]]:
    """A litter shaped by its wildness (`litter_profile(amount)`): each
    child draws its own drift strength (0.6x-2.2x `amount`); some make an
    architectural jump (new oscillator shape, module switched on, filter
    swapped — two at once when wild), some are first crossed with a
    `donor` (a library sound of another character), and the last few are
    wildcards re-rolled from scratch. Tame does none of the three.

    Badges say what changed from the *parent* the child came from, not
    from the crossed intermediate it was mutated from (which the user
    never saw).

    `origins`, when given, receives each child's origin parent index (the
    one its badges compare against) — the layered breeder
    (`studio/genome.py`) builds the child's structure on that parent's. It
    draws nothing from `rng`, so a seeded litter is the same either way.

    `bias`, a direction with a `GENE_BIAS` entry (the genre directions),
    leans each mutated child's genes toward that genre (`lean`) before its
    badges are taken. Any other value — None and the five tonal directions,
    which steer by selection alone — draws nothing and changes nothing."""
    if not parents:
        raise ValueError("generation() needs at least one parent")
    r = _rng(rng)
    prof = litter_profile(amount)
    n_random = 0
    if prof.random_rate > 0:
        n_random = max(1 if n >= 6 else 0, round(n * prof.random_rate))
    kids: list[tuple[Patch, list[str]]] = []
    for k in range(n):
        if k >= n - n_random:
            pi = int(r.integers(len(parents)))
            if origins is not None:
                origins.append(pi)
            kids.append((random_patch(parents[pi], rng=r), ["wild"]))
            continue
        lineage: list[str] = []
        if len(parents) >= 2 and r.random() < 0.7:
            i, j = r.choice(len(parents), size=2, replace=False)
            pi = int(i)
            origin = parents[pi]
            base, _ = crossover(origin, parents[int(j)], rng=r)
            lineage.append("mix")
        else:
            pi = int(r.integers(len(parents)))
            origin = base = parents[pi]
        if origins is not None:
            origins.append(pi)
        if donors and prof.donor_prob > 0 and r.random() < prof.donor_prob:
            donor = donors[int(r.integers(len(donors)))]
            base, _ = crossover(base, donor, rng=r)
            base = replace(base, name=parents[0].name)
            lineage.append("cross:" + donor.name.strip())
        strength = amount * float(r.uniform(0.6, 2.2))
        jump = r.random() < prof.jump_prob
        child, _ = mutate(
            base,
            amount=strength,
            rng=r,
            jump=jump,
            drift_prob=prof.drift_prob,
            max_genes=prof.max_genes,
            double_jump=prof.double_jump,
        )
        if bias in GENE_BIAS:
            child = lean(child, bias, r)
        genes = _badges_for(_genome_diff(child, origin), asdict(origin))
        badges = lineage + (["jump"] + genes[:1] if jump else genes)
        kids.append((child, badges[:3]))
    return kids


# --- steering: score measured candidates by a direction, keep the best ---


def _grit(m: Probe) -> float:
    """Noise, harmonic density *and* saturation. Log flatness alone rewards
    hiss — and, measured on litters, it picked *peakier* waveforms (frame
    crest +1.1 dB): the opposite of drive, which barely moves flatness but
    squares the waveform off (frame crest ~-2 dB at drive 4) and fills the
    band with harmonics (density). With crest weighted 2x the top 30 % of a
    litter is both noisier (+0.9 decades) and more saturated (-1.0 dB
    crest, drive up). Library spreads: log flatness 2.1 decades, density
    0.2, frame crest 2.2 dB."""
    t = m.texture
    return log_flatness(t) + _GRIT_DENSITY * t.density - _GRIT_CREST * t.frame_crest


_GRIT_DENSITY = 10.0
_GRIT_CREST = 2.0

DIRECTIONS: dict[str, Callable[[Probe], float]] = {
    # at the listening register (A3) everything under 120 Hz is sub-octave
    # content — the sub oscillator and octave-down layers — not the note
    "more_sub": lambda m: m.features.sub_20_60 + m.features.sub_60_120,
    "darker": lambda m: -np.log2(max(m.features.centroid, 20.0)),
    "brighter": lambda m: np.log2(max(m.features.centroid, 20.0)),
    "grittier": _grit,
    # per-frame flatness over 200 Hz-8 kHz, in decades: the whole-note
    # flatness this used was ~0 for every pitched sound, so it had no signal
    "cleaner": lambda m: -log_flatness(m.texture),
}

# --- genre directions: a target feature profile + gene leanings ------------
#
# A genre is not one axis, so it steers toward a *profile*: a target on a
# few of the breed's standardised measures (`Probe.vector`, in library
# spreads — 0 is the library's mean) with a weight each, scored as minus
# the weighted squared distance. Melodic techno: wide (unison/supersaw
# spread, stereo FX), dense harmonics, moving (gated/LFO'd), a little
# brighter and longer than the library. Garage: warm and narrow (sine sub,
# keys, mono), clean, with sub-octave weight and shorter, keyed notes.
GENRE_TARGETS: dict[str, dict[str, tuple[float, float]]] = {
    # measure -> (target z, weight)
    "melodic_techno": {
        "stereo_width": (1.5, 1.0),
        "density": (1.0, 1.0),
        "movement": (1.0, 1.0),
        "centroid": (0.3, 0.5),
        "env_length": (0.5, 0.5),
    },
    "garage": {
        "centroid": (-0.6, 1.0),
        "stereo_width": (-0.4, 0.7),
        "log_flatness": (-0.5, 0.7),
        "sub_60_120": (0.8, 1.0),
        "env_length": (-0.3, 0.5),
    },
    # The two below are *measured*, not written down: each target is the
    # mean of that measure over the style's own recipe sounds that are not
    # basses (`studio/recipes.py`; a direction steers a main sound — the
    # low end is the role recipes' job), probed as the breeder probes any
    # patch and rounded to a tenth. `tests/test_genre_steer.py` re-measures
    # them, so a recipe edit that moves the style moves the test.
    # Filter house: shortish (stabs, a talking lead; a string machine
    # holds beside them), a little bright and harmonically
    # busy, nearly mono, barely moving (its movement is the filter sweep,
    # the phaser and the ensemble, which are FX and a song's lanes —
    # `genome.STEERS` — not the patch the probe hears). Measured with the
    # String Machine in the pack.
    "filter_house": {
        "env_length": (-0.3, 1.0),
        "centroid": (0.6, 0.7),
        "log_flatness": (0.7, 0.7),
        "movement": (0.1, 0.7),
        "stereo_width": (0.1, 0.5),
    },
    # Trance: wide (unison and supersaws spread fully), dense, bright and
    # moving, a touch shorter than the library. Measured with the
    # pluck-lead and the big-room pad in the pack.
    "trance": {
        "stereo_width": (1.4, 1.0),
        "density": (1.0, 1.0),
        "centroid": (0.8, 0.7),
        "movement": (1.3, 0.5),
        "env_length": (-0.1, 0.3),
    },
    # The last four packs' (every Start-a-song style has its
    # direction), measured the same way. The sub bands are left out of
    # every profile: a style's non-bass sounds all sit under the library
    # there, so they tell no style from another.
    # Electro (Just Ice): noisy and moving — gated stabs, a blown lead, a
    # siren — a little bright and short.
    "electro": {
        "log_flatness": (0.9, 1.0),
        "movement": (0.6, 1.0),
        "centroid": (0.5, 0.7),
        "env_length": (-0.4, 0.7),
        "density": (0.2, 0.3),
    },
    # Industrial (Nine Inch Wails): narrow, thin, a little bright and
    # steady. Its spikes (struck metal, a pulse: crest) and its lengths
    # vary too much across the pack to steer by — a profile with them sat
    # nearer the other packs' sounds than its own; the grit is the FX's
    # (`genome.STEERS`).
    "industrial": {
        "stereo_width": (-0.3, 1.0),
        "density": (-0.2, 1.0),
        "centroid": (0.5, 0.7),
        "movement": (0.0, 0.5),
    },
    # Psych (Psych Haze): the low mids (a fuzz lead, a hazy pad, wobbling
    # keys), sparse and short, slow to speak; its swirl is the phaser and
    # the tape — FX (`genome.STEERS`), not the patch.
    "psych": {
        "low_120_250": (0.5, 1.0),
        "density": (-0.4, 0.7),
        "env_length": (-0.4, 0.7),
        "crest": (0.2, 0.5),
        "attack_time": (0.2, 0.3),
    },
    # Sixties (Sixties Studio): short, even and mono — an organ, tape
    # strings and flute, a double-tracked lead — and a little bright.
    "sixties": {
        "env_length": (-0.8, 1.0),
        "crest": (-0.6, 0.7),
        "stereo_width": (-0.6, 1.0),
        "log_flatness": (0.5, 0.5),
        "centroid": (0.4, 0.5),
    },
}
_VECTOR_INDEX = {k: i for i, k in enumerate(BREED_VECTOR)}


def _genre(name: str) -> Callable[[Probe], float]:
    target = GENRE_TARGETS[name]
    idx = np.array([_VECTOR_INDEX[k] for k in target])
    goal = np.array([t for t, _ in target.values()])
    weight = np.array([w for _, w in target.values()])

    def score(m: Probe) -> float:
        return -float(np.sum(weight * (m.vector[idx] - goal) ** 2))

    score.__name__ = f"genre_{name}"
    return score


def genre_distance(name: str, m: Probe) -> float:
    """How far a probe sits from a genre's profile (0 = on it)."""
    return -DIRECTIONS[name](m)


DIRECTIONS.update({name: _genre(name) for name in GENRE_TARGETS})

# The genre directions also *lean* the litter (selection alone can only
# pick what the mutations happened to make): each mutated child, with
# probability `_LEAN_PROB`, has 1-2 of its genre's genes re-drawn inside
# that genre's range (a numeric gene's (lo, hi), clamped to the gene's own
# range; a categorical gene's choices). The tonal directions have no entry
# and never lean.
GENE_BIAS: dict[str, dict[str, tuple]] = {
    "melodic_techno": {
        "osc1": ("supersaw", "saw"),
        "unison": (3, 5),
        "unison_detune": (14.0, 26.0),
        "unison_spread": (0.8, 1.0),
        "drive": (1.2, 1.8),
        "filter_lfo_depth": (600.0, 1800.0),
    },
    "garage": {
        "osc1": ("sine", "triangle", "fm"),
        "cutoff": (900.0, 2200.0),
        "unison": (1, 1),
        "unison_spread": (0.0, 0.4),
        "sub_level": (0.3, 0.6),
        "resonance": (0.05, 0.25),
    },
    # a resonant filter snapped open by its envelope on a saw, a square or
    # a pluck, a little drive and glide, notes that do not hang on
    "filter_house": {
        "osc1": ("saw", "square", "pluck"),
        "resonance": (0.35, 0.7),
        "filter_env": (4000.0, 6000.0),
        "drive": (1.3, 2.2),
        "glide": (0.02, 0.08),
        "sustain": (0.0, 0.35),
        "release": (0.05, 0.25),
    },
    # supersaws and saws in the widest unison the breeder draws (5: a hand
    # can take a patch to 16), spread fully, the filter open
    "trance": {
        "osc1": ("supersaw", "saw"),
        "unison": (4, 5),
        "unison_detune": (18.0, 30.0),
        "unison_spread": (0.85, 1.0),
        "cutoff": (2500.0, 9000.0),
    },
    # a square or a saw driven hard through a snapping, resonant filter
    "electro": {
        "osc1": ("square", "saw", "supersaw"),
        "drive": (1.6, 3.0),
        "resonance": (0.2, 0.5),
        "filter_env": (1800.0, 3000.0),
        "unison": (1, 3),
    },
    # FM and squares, hard drive, a resonant peak, mono, nothing that hangs
    "industrial": {
        "osc1": ("fm", "square", "saw"),
        "drive": (2.0, 4.0),
        "resonance": (0.25, 0.45),
        "unison": (1, 1),
        "unison_spread": (0.0, 0.3),
        "release": (0.05, 0.4),
    },
    # a square, a saw or a triangle, one voice, the filter low, quick to
    # speak and to let go
    "psych": {
        "osc1": ("square", "saw", "triangle"),
        "cutoff": (500.0, 1400.0),
        "unison": (1, 1),
        "attack": (0.02, 0.15),
        "release": (0.05, 0.3),
    },
    # plain waves, one voice, the filter fairly open, short tails
    "sixties": {
        "osc1": ("sine", "saw", "square", "triangle"),
        "unison": (1, 1),
        "unison_spread": (0.0, 0.2),
        "cutoff": (2200.0, 5000.0),
        "resonance": (0.05, 0.2),
        "release": (0.05, 0.4),
    },
}
_LEAN_PROB = 0.6
_EXPLORE_EVERY = 4  # steer(): one pick in four is the most different, not the best


def lean(child: Patch, direction: str, r: np.random.Generator) -> Patch:
    """`child` with 1-2 of `direction`'s genes re-drawn toward the genre
    (`GENE_BIAS`), with probability `_LEAN_PROB`; else `child` itself."""
    table = GENE_BIAS[direction]
    if r.random() >= _LEAN_PROB:
        return child
    genes = sorted(table)
    k = 1 + int(r.random() < 0.5)
    picked = r.choice(len(genes), size=k, replace=False)
    changes: dict = {}
    for i in sorted(int(j) for j in picked):
        gene, spec = genes[i], table[genes[i]]
        param = PARAMS[gene]
        if param.kind == "cat":
            changes[gene] = spec[int(r.integers(len(spec)))]
            continue
        lo, hi = param.gene
        a, b = max(lo, spec[0]), min(hi, spec[1])
        if param.kind == "int":
            changes[gene] = int(r.integers(int(a), int(b) + 1))
        else:
            changes[gene] = float(r.uniform(a, b))
    return replace(child, **changes)


def steer(
    parents: list[Patch],
    direction: str,
    n: int = 8,
    pool: int = 24,
    amount: float = 0.6,
    rng: np.random.Generator | None = None,
    return_badges: bool = False,
    donors: list[Patch] | None = None,
    bpm: float = PROBE_BPM,
) -> list[Patch] | list[tuple[Patch, list[str]]]:
    """The `n` best of a `pool`-child litter by `direction`, each candidate
    measured once on the breeder's probe (probe.py). The studio's board
    does the same inside `new_generation` so it can reuse the renders for
    its diversity pick and tiles.

    Not quite the `n` best: one slot in four (`n // _EXPLORE_EVERY`, best
    first, the explorers last) goes to the audible candidate farthest from
    everything already picked, whatever it scores. The best-scoring
    children of one litter are each other's neighbours — on a tame litter
    of 16, 15 of the 28 pairs among the top 8 were the same sound twice
    (probe distance < 1.0; 8 with the explorers) — and a litter bred from
    such parents has nowhere else to go. The draws are untouched: the
    explorers are chosen from what was already measured."""
    score_fn = DIRECTIONS[direction]  # KeyError on unknown direction: intended
    r = _rng(rng)
    candidates = generation(
        parents, n=pool, amount=amount, rng=r, donors=donors, bias=direction
    )

    scored: list[tuple[float, int, Patch, list[str], Probe]] = []
    probes = probe_many([cand for cand, _ in candidates], bpm)
    for i, ((cand, badges), m) in enumerate(zip(candidates, probes, strict=True)):
        if m is None:
            continue
        scored.append((float(score_fn(m)), i, cand, badges, m))

    scored.sort(key=lambda t: (-t[0], t[1]))  # index tiebreak = deterministic
    explorers = min(n // _EXPLORE_EVERY, max(0, len(scored) - n))
    picked, rest = scored[: n - explorers], scored[n - explorers :]
    for _ in range(explorers):
        # farthest from every pick so far; a silent candidate is far from
        # everything, so only audible ones explore (argmax: first on a tie)
        gaps = [
            min(distance(t[4].vector, p[4].vector) for p in picked)
            if t[4].audible
            else -1.0
            for t in rest
        ]
        if not picked or max(gaps) < 0:
            break
        picked.append(rest.pop(int(np.argmax(gaps))))
    picked += rest[: n - len(picked)]
    if return_badges:
        return [(cand, badges) for _, _, cand, badges, _ in picked]
    return [cand for _, _, cand, _, _ in picked]
