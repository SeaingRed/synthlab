"""Mod mixes: the mod matrix set in one move — a random assortment of mod
mixes to apply to a synth on the fly.

A mix is the four routes and the assignable LFO that drives them — every
slot it does not name is unplugged, so a mix never stacks on what was
there. `MIXES` are named moves (each a `title` saying what you will hear);
`random_mix` draws two to four routes from `POOL`, pairs that make a sound
move musically, with an LFO to match. Both answer a dict of Patch fields,
which `routes/patch.py` applies as one undo step. Values are inside every
field's own range (`params.clamp` is applied after, as for any edit).
"""

from __future__ import annotations

import random

from synthlab.params import MOD_GENE_SLOTS, MOD_SLOTS

from .paramui import MOD_SOURCE_LABELS, MOD_TARGET_LABELS

# (label, what you will hear, LFO {shape, sync, rate}, routes (src, dst, amt))
MIXES: dict[str, tuple[str, str, dict, tuple[tuple[str, str, float], ...]]] = {
    "wobble": (
        "Wobble",
        "the LFO sweeps the cutoff on the eighth notes, a little resonance with it",
        {"lfo_shape": "sine", "lfo_sync": "1/8"},
        (("lfo", "cutoff", 0.6), ("lfo", "resonance", 0.2)),
    ),
    "pluck": (
        "Pluck sweep",
        "the filter envelope snaps the cutoff open, harder the harder you play",
        {"lfo_shape": "sine", "lfo_sync": "off", "mod_lfo_rate": 1.0},
        (("fenv", "cutoff", 0.5), ("velocity", "cutoff", 0.3)),
    ),
    "breathe": (
        "Breathing",
        "a slow triangle opens the filter and trades the oscillators; air on attack",
        {"lfo_shape": "triangle", "lfo_sync": "2 bars"},
        (
            ("lfo", "cutoff", 0.3),
            ("lfo", "osc2_level", -0.3),
            ("aenv", "noise_level", 0.2),
        ),
    ),
    "bite": (
        "Velocity bite",
        "how hard you play opens the filter, adds drive and envelope",
        {"lfo_shape": "sine", "lfo_sync": "off", "mod_lfo_rate": 1.0},
        (
            ("velocity", "cutoff", 0.5),
            ("velocity", "drive", 0.4),
            ("velocity", "filter_env", 0.3),
        ),
    ),
    "shimmer": (
        "PWM shimmer",
        "the pulse width and the second oscillator's detune drift together",
        {"lfo_shape": "triangle", "lfo_sync": "off", "mod_lfo_rate": 0.4},
        (("lfo", "pwm_depth", 0.6), ("lfo", "osc2_detune", 0.3)),
    ),
    "siren": (
        "Siren",
        "a saw LFO bends the pitch up and drops it, once a bar",
        {"lfo_shape": "saw", "lfo_sync": "1 bar"},
        (("lfo", "pitch", 0.5),),
    ),
    "stutter": (
        "Gate stutter",
        "a square LFO chops the level on the sixteenths",
        {"lfo_shape": "square", "lfo_sync": "1/16"},
        (("lfo", "osc1_level", -0.8), ("lfo", "osc2_level", -0.8)),
    ),
    "drift": (
        "Analog drift",
        "a slow random LFO pulls the two oscillators apart and nudges the filter",
        {"lfo_shape": "noise", "lfo_sync": "off", "mod_lfo_rate": 0.3},
        (
            ("lfo", "osc1_detune", 0.3),
            ("lfo", "osc2_detune", -0.3),
            ("lfo", "cutoff", 0.1),
        ),
    ),
    "swell": (
        "Vibrato swell",
        "the vibrato grows as the note sustains",
        {"lfo_shape": "sine", "lfo_sync": "off", "mod_lfo_rate": 1.0},
        (("aenv", "vibrato_depth", 0.6),),
    ),
    "growl": (
        "Growl",
        "a saw LFO on the quarter notes rakes the cutoff and the drive",
        {"lfo_shape": "saw", "lfo_sync": "1/4"},
        (("lfo", "cutoff", 0.7), ("lfo", "drive", 0.5)),
    ),
}

# routes that move a sound musically:
# (src, dst, lowest |amt|, highest |amt|, may it be inverted)
POOL = (
    ("lfo", "cutoff", 0.2, 0.7, True),
    ("lfo", "resonance", 0.1, 0.3, False),
    ("lfo", "pwm_depth", 0.3, 0.7, False),
    ("lfo", "osc2_detune", 0.1, 0.4, True),
    ("lfo", "osc2_level", 0.2, 0.5, True),
    ("lfo", "drive", 0.2, 0.5, False),
    ("lfo", "tremolo_depth", 0.2, 0.6, False),
    ("lfo", "noise_level", 0.1, 0.3, False),
    ("lfo", "pitch", 0.02, 0.08, True),
    ("fenv", "cutoff", 0.3, 0.7, True),
    ("fenv", "resonance", 0.1, 0.3, False),
    ("aenv", "vibrato_depth", 0.3, 0.7, False),
    ("aenv", "noise_level", 0.1, 0.3, False),
    ("aenv", "filter_lfo_depth", 0.2, 0.5, False),
    ("velocity", "cutoff", 0.2, 0.5, False),
    ("velocity", "drive", 0.2, 0.5, False),
    ("velocity", "filter_env", 0.2, 0.4, False),
)
RANDOM_SHAPES = ("sine", "triangle", "saw", "square", "noise")
RANDOM_SYNCS = ("off", "2 bars", "1 bar", "1/2", "1/4", "1/8", "1/16")


def _fields(lfo: dict, routes) -> dict:
    out = dict(lfo)
    for i, slot in enumerate(MOD_SLOTS):
        if i < len(routes):
            src, dst, amt = routes[i]
            out |= {
                f"{slot}_src": src,
                f"{slot}_dst": dst,
                f"{slot}_amt": round(amt, 2),
            }
        else:
            out[f"{slot}_src"] = "none"  # unplugged: a mix never stacks on the last one
    return out


def mix_fields(name: str) -> dict | None:
    """A named mix's Patch fields, or None for a name that is not one."""
    mix = MIXES.get(name)
    return _fields(mix[2], mix[3]) if mix else None


def random_mix(rng: random.Random | None = None) -> tuple[str, dict]:
    """Two to four routes from `POOL` (no target twice) and an LFO to drive
    them: (what it is, in words; its Patch fields)."""
    rng = rng or random.Random()
    # two to four (slots 5-12 are left unplugged by a mix)
    want, picks, seen = rng.randint(2, len(MOD_GENE_SLOTS)), [], set()
    for src, dst, lo, hi, flip in rng.sample(POOL, len(POOL)):
        if dst in seen:
            continue
        seen.add(dst)
        amt = rng.uniform(lo, hi) * (rng.choice((1, -1)) if flip else 1)
        picks.append((src, dst, amt))
        if len(picks) == want:
            break
    sync = rng.choice(RANDOM_SYNCS)
    lfo = {"lfo_shape": rng.choice(RANDOM_SHAPES), "lfo_sync": sync}
    if sync == "off":
        lfo["mod_lfo_rate"] = round(rng.uniform(0.2, 6.0), 2)
    # in the matrix's own words, the amount as the cables print it
    # ("LFO 1 → Noise level +29%"), never the field values ("lfo → noise level")
    words = ", ".join(
        f"{MOD_SOURCE_LABELS.get(src, src)} → "
        f"{MOD_TARGET_LABELS.get(dst, dst.replace('_', ' '))} {round(amt, 2):+.0%}"
        for src, dst, amt in picks
    )
    return words, _fields(lfo, picks)
