"""The layered breeding genome: a bred *Sound*, not just a patch.

A Sound is the format-2 preset shape — the main `patch`, up to two layers,
main-lane FX and up to two curves — so a card is exactly what "Use with its
layers" loads (`sounds.py`, the same `svc.adopt` path a v2 preset takes).
Every layer *follows* the main lane (`{"follow": {"octave": n}}`,
`arrange.follow_layer`): it plays the main lane's notes transposed, so the
card, the landing and any later edit of the main grid play the same stack.

How far a litter may go is `DEPTH[state.breed_depth]` (separate from the
wildness, which still decides how far the *main patch* drifts):

- `single` — today's breeder, untouched: patches only.
- `layered` — +1 layer at p .5, a 2nd at p .15; 0-2 main FX; <= 2 mod
  routes; <= 1 curve.
- `deep` — 1-2 layers (a 2nd at p .4), 1-3 FX a layer, <= 3 main FX, <= 3
  routes, <= 2 curves; the only depth that may add reverb/shimmer/granular
  (they are the costly probes).

Layers are made to a **role recipe** (`ROLES`: sub / pad / pluck / air),
applied as a repair after every operator, so a mutated sub stays a sub (a
sine an octave or two down, low-passed at 80-200 Hz) and a pad stays slow.
The role a new layer takes fills a gap in the main's probe (no low end ->
sub, a short main -> pad, a slow main -> pluck, a dark main -> air). Each
layer's gain is staged from probe RMS to its role's level under the main,
so the main stays loudest.

Measurement is one probe of the whole stack at the listening register
(`explore.probe`: A3, gate + release), each layer rendered once per genome
(`_MEMO`, so a child sharing its parent's layers reuses their renders),
summed at its staged gain. Distances add a structure term to the
standardised feature distance (`struct_distance`: role-set difference, half
the layer-count difference, FX-kind difference; `LAMBDA` = 0.5), and the
pick keeps two "explorer" slots filled from the whole audible pool.
"""

from __future__ import annotations

import copy
import json
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass, field, replace

import numpy as np

from ..analysis.texture import texture
from ..core import SR, n_samples
from ..effects import chain as fx_chain
from ..effects import to_stereo
from ..explore import breed
from ..explore.probe import (
    _BLOCK,
    PROBE_GATE,
    PROBE_MAX_TAIL,
    PROBE_PITCH,
    Probe,
    distance,
    measure,
    pmap,
)
from ..params import MOD_GENE_SLOTS, MOD_SLOTS, PARAMS, live, slot_active
from ..sequencer import midi, name_of, note_span
from ..synth import Patch, Synth, at_bpm
from .arrange import FOLLOW_OCTAVES, LAYER_ROLES
from .curves import CURVABLE, curve_array
from .fxrack import FX_UNITS, build_chain, clean_chain
from .state import Arrangement

# --- depth -------------------------------------------------------------------


@dataclass(frozen=True)
class Depth:
    name: str
    max_layers: int
    p_first: float  # chance a child with no layer gains one
    p_second: float  # chance a child with one layer gains a second
    min_layers: int
    max_main_fx: int
    layer_fx: tuple[int, int]  # (fewest, most) FX on a layer
    max_routes: int
    max_curves: int
    spatial: bool  # reverb / shimmer / granular allowed (costly probes)
    extra_ops: int  # structure moves a child makes beyond its wildness's


DEPTH: dict[str, Depth] = {
    "single": Depth("single", 0, 0.0, 0.0, 0, 0, (0, 0), 4, 0, False, 0),
    "layered": Depth("layered", 2, 0.5, 0.15, 0, 2, (0, 2), 2, 1, False, 0),
    "deep": Depth("deep", 2, 1.0, 0.4, 1, 3, (1, 3), 3, 2, True, 1),
}
DEPTHS = tuple(DEPTH)

LAMBDA = 0.5  # weight of the structure distance against the feature distance
EXPLORERS = 2  # board slots filled from the whole audible pool
SUB_ONLY = 0.85  # a stack whose energy is this much under 120 Hz is a rumble
# A card's main lane must hold its own (loudest 50 ms, dBFS): it is what "Use
# sound" plays, what the mix sums raw against the drums, and what `stage`
# sets every layer's gain from. A main filtered to almost nothing (a
# high-pass at 6-16 kHz) passed the stack gate on its layers, floored them
# at 0.02 and played next to silence in the mix. The quietest preset peaks
# at -17 dB; one card in seven sat under -30 (a session's 80 cards).
MAIN_QUIET_DB = -30.0

# --- roles -------------------------------------------------------------------


@dataclass(frozen=True)
class Role:
    octaves: tuple[int, ...]  # follow octaves it may sit at
    db: tuple[float, float]  # level under the main (dB, probe RMS)
    rules: dict  # field -> (lo, hi) numeric range, or a tuple of choices
    fx: tuple[str, ...]  # FX kinds it may carry
    families: tuple[str, ...]  # library families a donor graft draws from
    # field -> the one value it takes, set without a draw: a field added
    # after the rules were (a rule draws even a fixed range when fresh, so
    # a new one would move every seeded layered litter after it)
    pins: tuple[tuple[str, object], ...] = ()


_OFF = (0.0, 0.0)
# the four slots the breeder walks (a rule draws when fresh: slots 5-12
# are unplugged in `recipe` without a draw, so seeded litters stand)
_ROUTES_OFF = {f"{s}_src": ("none",) for s in MOD_GENE_SLOTS}

ROLES: dict[str, Role] = {
    # a sine/triangle an octave or two down, low-passed at 80-200 Hz, dry
    "sub": Role(
        (-1, -2),
        (-12.0, -6.0),
        {
            "osc1": ("sine", "triangle"),
            "osc2": (None,),
            "osc1_octave": (0, 0),
            "unison": (1, 1),
            "noise_level": _OFF,
            "sub_level": _OFF,
            "filter_type": ("lowpass",),
            "cutoff": (80.0, 200.0),
            "resonance": (0.0, 0.3),
            "filter_env": (0.0, 150.0),
            "key_track": (0.0, 0.3),
            "filter_lfo_depth": _OFF,
            "drive": (1.0, 1.5),
            "fm_amount": _OFF,
            "ring_mod": _OFF,
            "formant_amount": _OFF,
            "vibrato_depth": _OFF,
            "tremolo_depth": _OFF,
            "pwm_depth": _OFF,
            "pitch_env": _OFF,
            "glide": _OFF,
            "attack": (0.001, 0.03),
            "sustain": (0.6, 1.0),
            "release": (0.05, 0.6),
            **_ROUTES_OFF,
        },
        ("lowpass", "compress"),
        ("bass",),
        # a sub holds its pitch (drift's wander and its cutoff
        # offset are for the parts over it)
        (("drift", 0.0),),
    ),
    # slow, wide and soft under the main
    "pad": Role(
        (0, 1),
        (-14.0, -8.0),
        {
            "osc1": ("saw", "supersaw", "triangle", "square", "wavetable"),
            "osc1_octave": (0, 0),
            "attack": (0.2, 1.2),
            "decay": (0.3, 2.0),
            "sustain": (0.6, 1.0),
            "release": (0.8, 2.5),
            "unison": (3, 5),
            "unison_detune": (8.0, 25.0),
            "unison_spread": (0.5, 1.0),
            "filter_type": ("lowpass", "ladder"),
            "cutoff": (800.0, 3000.0),
            "resonance": (0.0, 0.5),
            "filter_env": (0.0, 1500.0),
            "drive": (1.0, 1.5),
            "noise_level": (0.0, 0.05),
            "pitch_env": _OFF,
            "glide": _OFF,
        },
        ("highpass", "chorus", "phaser", "width", "reverb", "shimmer"),
        ("pad",),
    ),
    # a short attack and decay over the main, echoing on the beat
    "pluck": Role(
        (0, 1),
        (-10.0, -4.0),
        {
            "osc1": ("saw", "square", "triangle", "sine", "pluck", "fm"),
            "osc1_octave": (0, 0),
            "attack": (0.001, 0.01),
            "decay": (0.1, 0.35),
            "sustain": (0.0, 0.2),
            "release": (0.05, 0.4),
            "unison": (1, 3),
            "filter_type": ("lowpass", "ladder"),
            "cutoff": (600.0, 5000.0),
            "glide": _OFF,
            "pitch_env": (-2.0, 2.0),
        },
        ("delay", "highpass", "chorus", "reverb"),
        ("pluck", "keys"),
    ),
    # a thin band of top an octave or two up (the +2 is osc1_octave +1)
    "air": Role(
        (1,),
        (-20.0, -14.0),
        {
            "osc1_octave": (0, 1),
            "filter_type": ("bandpass", "highpass"),
            "cutoff": (4000.0, 10000.0),
            "resonance": (0.0, 0.6),
            "attack": (0.05, 0.8),
            "sustain": (0.5, 1.0),
            "release": (0.4, 2.0),
            "noise_level": (0.05, 0.3),
            "drive": (1.0, 1.3),
            "glide": _OFF,
            "pitch_env": _OFF,
        },
        ("width", "chorus", "phaser", "highpass", "shimmer"),
        ("pad", "fx", "lead"),
    ),
}
assert tuple(ROLES) == LAYER_ROLES

# FX a main lane may be given. Never a sidechain (it keys off drums a
# bench render doesn't play), a gate or a pump (they chop the phrase).
MAIN_FX = (
    "drive",
    "fold",
    "bitcrush",
    "ringmod",
    "chorus",
    "phaser",
    "flanger",
    "delay",
    "lowpass",
    "highpass",
    "width",
    "compress",
    "comb",
    "reverb",
    "shimmer",
    "granular",
)
SPATIAL = frozenset({"reverb", "shimmer", "granular"})  # deep only
GRIT_FX = frozenset({"drive", "fold", "bitcrush", "ringmod"})
DIRTY_FX = GRIT_FX | {"vinyl", "comb"}

# musical ranges for a unit the breeder adds (FX_UNITS' full ranges are for
# hands: a 0.9-feedback comb at full mix is rarely a sound)
_FX_RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "drive": {"amount": (1.3, 3.0), "mix": (0.5, 1.0)},
    "fold": {"amount": (1.2, 2.5), "mix": (0.4, 1.0)},
    "bitcrush": {"bits": (6.0, 12.0), "downsample": (1.0, 4.0)},
    "ringmod": {"freq": (50.0, 1500.0), "mix": (0.2, 0.6)},
    "chorus": {"rate": (0.2, 1.2), "depth": (2.0, 6.0), "mix": (0.3, 0.6)},
    "phaser": {"rate": (0.1, 1.0), "depth": (0.4, 0.9), "mix": (0.3, 0.6)},
    "flanger": {"rate": (0.1, 0.8), "depth": (1.0, 4.0), "mix": (0.3, 0.6)},
    "delay": {
        "feedback": (0.2, 0.5),
        "mix": (0.15, 0.35),
        "ping_pong": (1.0, 1.0),
        "sync": (2.0, 3.0),
    },
    "lowpass": {"cutoff": (800.0, 6000.0), "q": (0.6, 1.5)},
    "highpass": {"cutoff": (80.0, 600.0), "q": (0.6, 1.0)},
    "width": {"amount": (1.2, 1.8)},
    "compress": {"threshold": (-24.0, -8.0), "ratio": (2.0, 6.0)},
    "comb": {"tune": (80.0, 800.0), "feedback": (0.3, 0.8), "mix": (0.2, 0.5)},
    "reverb": {"decay": (0.8, 3.0), "mix": (0.15, 0.35), "size": (0.5, 0.9)},
    "shimmer": {"decay": (1.5, 4.0), "mix": (0.15, 0.3), "size": (0.6, 0.95)},
    "granular": {"mix": (0.2, 0.5), "scatter": (0.2, 0.6)},
}

# curvable genes worth a drawn sweep, in order of preference
_CURVE_GENES = (
    "cutoff",
    "wt_pos",
    "formant_vowel",
    "filter_lfo_depth",
    "osc2_level",
    "fm_index",
    "drive",
    "resonance",
    "pwm_depth",
    "vibrato_depth",
    "noise_level",
)
CURVE_SHAPES = ("rise", "fall", "dip", "swell")
_SHAPE_MARK = {"rise": "↗", "fall": "↘", "dip": "⌄", "swell": "⌃"}


# --- the genome --------------------------------------------------------------


@dataclass
class Layer:
    role: str
    patch: Patch
    octave: int  # the follow octave (-2..+1)
    db: float  # its level under the main, what `stage` sets `gain` from
    fx: list = field(default_factory=list)
    gain: float = 1.0
    donor: str = ""  # the library sound it was grafted from, if any


@dataclass
class Stack:
    patch: Patch
    layers: list[Layer] = field(default_factory=list)
    main_fx: list = field(default_factory=list)
    curves: dict = field(default_factory=dict)

    @property
    def plain(self) -> bool:
        """Just a patch: exactly what the single-depth breeder makes."""
        return not (self.layers or self.main_fx or self.curves)

    def copy(self) -> Stack:
        return Stack(
            self.patch,
            [replace(lay, fx=copy.deepcopy(lay.fx)) for lay in self.layers],
            copy.deepcopy(self.main_fx),
            copy.deepcopy(self.curves),
        )

    def roles(self) -> list[str]:
        return [lay.role for lay in self.layers]


def _genome(p: Patch) -> dict:
    d = p.to_json()
    d.pop("name", None)
    return d


def layer_dict(lay: Layer) -> dict:
    """A layer as a format-2 preset carries it (`schema` layers_other)."""
    return {
        "patch": lay.patch.to_json(),
        "cells": [],
        "muted": False,
        "gain": round(float(lay.gain), 4),
        "fx": clean_chain(copy.deepcopy(lay.fx)),
        "follow": {"octave": int(lay.octave)},
        "role": lay.role,
    }


def chain_of(stack: Stack) -> dict | None:
    """The Sound's format-2 extras (`layers`/`main_fx`/`curves`), conformed
    exactly as a v2 preset's are (`state.preset_from_json`), so a card and
    a preset go through one load. None for a plain patch."""
    if stack.plain:
        return None
    from . import schema

    raw = {
        "patch": stack.patch.to_json(),
        "layers": [layer_dict(lay) for lay in stack.layers],
        "main_fx": clean_chain(copy.deepcopy(stack.main_fx)),
        "curves": copy.deepcopy(stack.curves),
    }
    return schema.conform(raw, schema.nodes().PRESET_V2)


def row_sound(stack: Stack) -> dict | None:
    """What a space.jsonl row carries beside its `patch`: the chain, plus
    each layer's staged level and donor (breeding reads them back; a load
    ignores them)."""
    chain = chain_of(stack)
    if chain is None:
        return None
    return {
        "layers": chain["layers"],
        "main_fx": chain["main_fx"],
        "curves": chain["curves"],
        "levels": [round(lay.db, 2) for lay in stack.layers],
        "donors": [lay.donor for lay in stack.layers],
    }


def stack_of_row(row: dict) -> Stack:
    """A space.jsonl row as a Stack (a row without `sound` is a plain
    patch: every row written before layered breeding)."""
    patch = Patch.from_dict(row["patch"])
    sound = row.get("sound")
    if not isinstance(sound, dict):
        return Stack(patch)
    from . import schema

    chain = schema.conform({**sound, "patch": row["patch"]}, schema.nodes().PRESET_V2)
    levels = sound.get("levels") if isinstance(sound.get("levels"), list) else []
    donors = sound.get("donors") if isinstance(sound.get("donors"), list) else []
    layers = []
    for i, lay in enumerate(chain["layers"][: DEPTH["deep"].max_layers]):
        if lay.get("patch") is None:
            continue
        role = lay.get("role") or "pad"
        gain = float(lay.get("gain", 1.0))
        db = (
            levels[i]
            if i < len(levels) and isinstance(levels[i], int | float)
            else None
        )
        if db is None:
            db = sum(ROLES[role].db) / 2
        layers.append(
            Layer(
                role=role,
                patch=Patch.from_dict(lay["patch"]),
                octave=int((lay.get("follow") or {}).get("octave", 0)),
                db=float(db),
                fx=lay.get("fx") or [],
                gain=gain,
                donor=str(donors[i]) if i < len(donors) and donors[i] else "",
            )
        )
    return Stack(patch, layers, chain["main_fx"], chain["curves"])


def key_of(stack: Stack) -> str:
    return json.dumps(
        {
            "patch": _genome(stack.patch),
            "layers": [
                [lay.role, _genome(lay.patch), lay.octave, round(lay.gain, 4), lay.fx]
                for lay in stack.layers
            ],
            "fx": stack.main_fx,
            "curves": stack.curves,
        },
        sort_keys=True,
    )


# --- structure distance ------------------------------------------------------


def fx_kinds(stack: Stack) -> frozenset[str]:
    kinds = {u.get("kind") for u in stack.main_fx}
    for lay in stack.layers:
        kinds |= {u.get("kind") for u in lay.fx}
    return frozenset(k for k in kinds if isinstance(k, str))


def struct_distance(a: Stack, b: Stack) -> float:
    """Role-set difference + half the layer-count difference + FX-kind
    difference: "+sub" or "+reverb" is one unit, like one library spread
    of a measure."""
    ra, rb = set(a.roles()), set(b.roles())
    return (
        len(ra ^ rb)
        + 0.5 * abs(len(a.layers) - len(b.layers))
        + len(fx_kinds(a) ^ fx_kinds(b))
    )


# --- the probe of a stack ----------------------------------------------------

_MEMO: OrderedDict[str, np.ndarray] = OrderedDict()
_MEMO_MAX = 96
_MEMO_LOCK = threading.Lock()


def _memo(key: str, make) -> np.ndarray:
    with _MEMO_LOCK:
        hit = _MEMO.get(key)
        if hit is not None:
            _MEMO.move_to_end(key)
            return hit
    audio = make()
    audio.setflags(write=False)
    with _MEMO_LOCK:
        _MEMO[key] = audio
        while len(_MEMO) > _MEMO_MAX:
            _MEMO.popitem(last=False)
    return audio


def _pitch(octave: int) -> str:
    return name_of(midi(PROBE_PITCH) + 12 * int(octave))


def _note(patch: Patch, pitch: str, bpm: float, curves: dict | None) -> np.ndarray:
    """The probe note (`probe.probe_audio`'s: A3-register gate + release,
    whole blocks), with the stack's curves drawn across it."""
    p = at_bpm(patch, bpm)
    dur = min(note_span(PROBE_GATE, p), PROBE_GATE + PROBE_MAX_TAIL)
    n = math.ceil(dur * SR / _BLOCK) * _BLOCK
    if curves:
        m = n_samples(n / SR, SR)
        tweaks = {
            k: arr
            for k, pts in curves.items()
            if k in CURVABLE and (arr := curve_array(pts, k, m)) is not None
        }
        if tweaks:
            p = p.tweak(**tweaks)
    return Synth(p).note(pitch, n / SR, gate=PROBE_GATE)


def _with_fx(audio: np.ndarray, fx: list, bpm: float) -> np.ndarray:
    x = to_stereo(np.asarray(audio, dtype=np.float64))
    if not fx:
        return x
    return np.asarray(fx_chain(x, build_chain(clean_chain(fx), Arrangement(bpm=bpm))))


def main_audio(stack: Stack, bpm: float) -> np.ndarray:
    key = json.dumps(
        ["main", _genome(stack.patch), stack.main_fx, stack.curves, bpm], sort_keys=True
    )
    return _memo(
        key,
        lambda: _with_fx(
            _note(stack.patch, PROBE_PITCH, bpm, stack.curves), stack.main_fx, bpm
        ),
    )


def layer_audio(lay: Layer, bpm: float) -> np.ndarray:
    """A layer's probe at unit gain (the gain is applied when summing, so
    restaging never re-renders)."""
    key = json.dumps(
        ["layer", _genome(lay.patch), lay.octave, lay.fx, bpm], sort_keys=True
    )
    return _memo(
        key,
        lambda: _with_fx(_note(lay.patch, _pitch(lay.octave), bpm, None), lay.fx, bpm),
    )


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


def stage(stack: Stack, bpm: float) -> None:
    """Set each layer's gain from probe RMS so it sits `db` under the main
    (in place). The main stays the loudest part of every card."""
    main = _rms(main_audio(stack, bpm))
    for lay in stack.layers:
        own = _rms(layer_audio(lay, bpm))
        if own < 1e-9 or main < 1e-9:
            lay.gain = 1.0
            continue
        g = main * 10 ** (lay.db / 20.0) / own
        lay.gain = float(min(1.5, max(0.02, g)))


def stack_audio(stack: Stack, bpm: float) -> np.ndarray:
    main = main_audio(stack, bpm)
    parts = [(1.0, main)] + [(lay.gain, layer_audio(lay, bpm)) for lay in stack.layers]
    n = max(len(a) for _g, a in parts)
    out = np.zeros((n, 2))
    for g, a in parts:
        out[: len(a)] += g * a
    return out


def measure_stack(stack: Stack, bpm: float) -> Probe | None:
    """Stage the gains, then one probe of the whole stack. A plain patch is
    measured as the single-depth breeder measures it (the same render)."""
    try:
        if stack.plain:
            from ..explore.probe import probe

            return probe(stack.patch, bpm)
        stage(stack, bpm)
        audio = stack_audio(stack, bpm)
    except Exception:
        return None
    if not np.all(np.isfinite(audio)):
        return None
    return measure(audio)


def audible(m: Probe) -> bool:
    """The quiet gate, and a stack that is all rumble (a sub layer over a
    main it thinned out) is no card either."""
    f = m.features
    return m.audible and f.sub_20_60 + f.sub_60_120 < SUB_ONLY


def main_loudness_db(stack: Stack, bpm: float) -> float:
    """The main lane's own loudest 50 ms (its FX and curves on), as the
    quiet gate reads a probe."""
    return float(texture(main_audio(stack, bpm)).loudness_db)


def card_ok(c: Candidate) -> bool:
    """A card worth showing: the stack passes the quiet/rumble gate AND its
    main lane is loud enough to stand alone (`MAIN_QUIET_DB`)."""
    return audible(c.m) and c.main_db >= MAIN_QUIET_DB


# --- operators ---------------------------------------------------------------


def _uniform(r: np.random.Generator, lo, hi):
    if isinstance(lo, int) and isinstance(hi, int):
        return int(r.integers(lo, hi + 1))
    return float(r.uniform(lo, hi))


def recipe(role: str, patch: Patch, r: np.random.Generator, fresh: bool) -> Patch:
    """`patch` made to `role`'s recipe: every rule's field clamped into its
    range (or, `fresh`, drawn anywhere in it), a categorical outside its
    choices re-picked. A repair, run after every move on a layer."""
    rules = ROLES[role].rules
    changes: dict = {}
    for name, rule in rules.items():
        cur = getattr(patch, name)
        if isinstance(rule[0], str) or rule[0] is None:
            if fresh or cur not in rule:
                changes[name] = rule[int(r.integers(len(rule)))]
            continue
        lo, hi = rule
        if fresh:
            changes[name] = _uniform(r, lo, hi)
        elif not isinstance(cur, int | float) or not lo <= cur <= hi:
            changes[name] = (
                type(lo)(min(hi, max(lo, cur))) if isinstance(cur, int | float) else lo
            )
    if any(k.endswith("_src") for k in rules):
        for s in MOD_SLOTS:
            changes[f"{s}_amt"] = 0.0
        for s in MOD_SLOTS[len(MOD_GENE_SLOTS) :]:
            changes[f"{s}_src"] = "none"
    for name, value in ROLES[role].pins:
        if getattr(patch, name) != value:
            changes[name] = value
    return replace(patch, **changes) if changes else patch


def _fx_unit(kind: str, r: np.random.Generator) -> dict:
    spec = FX_UNITS[kind]["params"]
    ranges = _FX_RANGES.get(kind, {})
    params = {}
    for name, (_lo, _hi, default) in spec.items():
        a, b = ranges.get(name, (default, default))
        params[name] = float(r.uniform(a, b)) if a != b else float(a)
    if kind == "delay":
        params["sync"] = float(r.choice([2.0, 3.0]))  # 1/8 or dotted 1/8
    return {"kind": kind, "params": params}


def _gap_weights(profile, have: set[str]) -> dict[str, float]:
    """Which role fills a gap in the main's probe: no low end -> sub, a
    short sound -> pad, a slow one -> pluck, a dark one -> air."""
    if profile is None:
        w = {"sub": 1.0, "pad": 1.0, "pluck": 1.0, "air": 0.6}
    else:
        f, t = profile.features, profile.texture
        low = f.sub_20_60 + f.sub_60_120
        w = {
            "sub": 0.2 + 4.0 * max(0.0, 0.25 - low),
            "pad": 1.2 if t.env_length < 0.5 else 0.3,
            "pluck": 1.2 if f.attack_time > 0.08 else 0.35,
            "air": 0.8 if f.centroid < 1500 else 0.25,
        }
    return {k: v for k, v in w.items() if k not in have}


def _pick(weights: dict[str, float], r: np.random.Generator):
    keys = sorted(weights)
    w = np.array([max(0.0, weights[k]) for k in keys])
    if not keys or w.sum() <= 0:
        return None
    return keys[int(r.choice(len(keys), p=w / w.sum()))]


@dataclass(frozen=True)
class Steer:
    """How a steering direction biases the structure moves."""

    roles: dict = field(default_factory=dict)  # role -> weight multiplier
    drop_roles: tuple[str, ...] = ()  # roles a remove move prefers
    fx_add: dict = field(default_factory=dict)  # kind -> weight multiplier
    fx_drop: frozenset[str] = frozenset()  # kinds a remove move prefers
    ops: dict = field(default_factory=dict)  # move -> weight multiplier
    curve: tuple[str, str] | None = None  # (gene, shape) a curve move prefers


STEERS: dict[str, Steer] = {
    "more_sub": Steer(
        roles={"sub": 8.0, "air": 0.2},
        drop_roles=("air",),
        fx_drop=frozenset({"highpass"}),
        ops={"add_layer": 3.0, "graft": 1.5},
    ),
    "darker": Steer(
        roles={"sub": 2.0, "pad": 1.5, "air": 0.1},
        drop_roles=("air",),
        fx_add={"lowpass": 6.0},
        fx_drop=frozenset({"highpass", "bitcrush", "ringmod"}),
        ops={"add_fx": 2.0, "remove_layer": 1.5},
        curve=("cutoff", "fall"),
    ),
    "brighter": Steer(
        roles={"air": 8.0, "sub": 0.3},
        drop_roles=("sub",),
        fx_add={"highpass": 2.0, "width": 2.0, "chorus": 1.5},
        fx_drop=frozenset({"lowpass"}),
        ops={"add_layer": 2.0},
        curve=("cutoff", "rise"),
    ),
    "grittier": Steer(
        fx_add={"drive": 6.0, "fold": 5.0, "bitcrush": 5.0, "ringmod": 3.0},
        ops={"add_fx": 3.0, "route": 1.5},
    ),
    "cleaner": Steer(
        fx_drop=DIRTY_FX,
        ops={"remove_fx": 4.0, "clean": 3.0},
    ),
    # genre directions (breed.GENRE_TARGETS): melodic techno is wide, dense
    # and moving — pads and air over the main, echoes, rooms, width and a
    # little saturation, mod routes and a rising curve
    "melodic_techno": Steer(
        roles={"pad": 3.0, "air": 2.5, "sub": 1.5, "pluck": 0.5},
        drop_roles=("pluck",),
        fx_add={"delay": 4.0, "reverb": 4.0, "width": 3.0, "chorus": 2.0, "drive": 1.5},
        fx_drop=frozenset({"lowpass", "bitcrush"}),
        ops={"add_layer": 2.0, "add_fx": 2.0, "route": 2.0, "add_curve": 1.5},
        curve=("cutoff", "rise"),
    ),
    # garage is warm, clean and narrow: a sub under keys, squashed and
    # darkened, never widened or smeared
    "garage": Steer(
        roles={"sub": 5.0, "pluck": 1.5, "pad": 0.7, "air": 0.2},
        drop_roles=("air",),
        fx_add={"compress": 5.0, "lowpass": 3.0, "chorus": 1.5},
        fx_drop=frozenset({"width", "shimmer", "granular", "ringmod", "fold"}),
        ops={"add_layer": 2.0, "add_fx": 1.5, "remove_fx": 1.5},
        curve=("cutoff", "fall"),
    ),
    # filter house is a filtered, phased, squashed loop over a sub: a
    # phaser, compression and grit on the main, a pluck or a sub under it,
    # the cutoff drawn opening — never a shimmer or a grain cloud
    "filter_house": Steer(
        roles={"sub": 2.0, "pluck": 2.0, "pad": 0.7},
        drop_roles=("air",),
        fx_add={
            "phaser": 4.0,
            "compress": 3.0,
            "drive": 2.0,
            "bitcrush": 2.0,
            "chorus": 1.5,
        },
        fx_drop=frozenset({"shimmer", "granular"}),
        ops={"add_fx": 2.0, "add_curve": 1.5, "route": 1.5},
        curve=("cutoff", "rise"),
    ),
    # trance is wide and wet: plucks, pads and a sub around the main,
    # echoes, halls and width, nothing crushed or worn
    "trance": Steer(
        roles={"pluck": 2.5, "pad": 2.0, "sub": 2.0, "air": 1.0},
        fx_add={"delay": 4.0, "reverb": 4.0, "width": 3.0, "chorus": 2.0},
        fx_drop=frozenset({"bitcrush", "vinyl", "fold"}),
        ops={"add_layer": 2.0, "add_fx": 2.0},
        curve=("cutoff", "rise"),
    ),
    # electro is driven, crushed and phased over a sub, squashed, its
    # filter drawn opening — never a hall or a grain cloud
    "electro": Steer(
        roles={"sub": 2.5, "pluck": 1.5, "pad": 0.7},
        drop_roles=("air",),
        fx_add={
            "drive": 4.0,
            "bitcrush": 2.5,
            "phaser": 2.5,
            "compress": 2.5,
            "delay": 1.5,
        },
        fx_drop=frozenset({"shimmer", "granular", "reverb"}),
        ops={"add_fx": 2.0, "add_curve": 1.5, "route": 1.5},
        curve=("cutoff", "rise"),
    ),
    # industrial is grit on grit: folded, crushed, ring-modulated and
    # combed, dry and narrow, the cutoff falling — never widened or sweet
    "industrial": Steer(
        roles={"sub": 2.0, "pluck": 1.0, "pad": 0.5, "air": 0.3},
        drop_roles=("air", "pad"),
        fx_add={
            "drive": 4.0,
            "fold": 3.0,
            "bitcrush": 3.0,
            "ringmod": 3.0,
            "comb": 2.0,
        },
        fx_drop=frozenset({"chorus", "width", "shimmer", "reverb"}),
        ops={"add_fx": 3.0, "route": 1.5},
        curve=("cutoff", "fall"),
    ),
    # psych swirls: a phaser, a flanger, a tape echo and a room around a
    # pad, a little fuzz — never crushed
    "psych": Steer(
        roles={"pad": 2.5, "air": 1.5, "sub": 1.0},
        fx_add={
            "phaser": 4.0,
            "flanger": 3.0,
            "delay": 3.0,
            "chorus": 2.0,
            "reverb": 2.0,
            "drive": 1.5,
        },
        fx_drop=frozenset({"bitcrush", "ringmod", "width"}),
        ops={"add_fx": 2.5, "route": 2.0, "add_curve": 1.5},
        curve=("cutoff", "rise"),
    ),
    # sixties is a small room on tape: a chorus or a slap echo, a plate,
    # a little drive, narrow and dark at the top — nothing modern
    "sixties": Steer(
        roles={"pad": 1.5, "pluck": 1.5, "sub": 0.5, "air": 0.3},
        drop_roles=("air", "sub"),
        fx_add={
            "chorus": 3.0,
            "delay": 2.5,
            "reverb": 2.5,
            "drive": 2.0,
            "lowpass": 2.0,
        },
        fx_drop=frozenset(
            {"width", "shimmer", "granular", "bitcrush", "ringmod", "fold"}
        ),
        ops={"add_fx": 2.0, "remove_fx": 1.5},
        curve=("cutoff", "fall"),
    ),
}


def _routes(p: Patch) -> list[str]:
    return [s for s in MOD_SLOTS if slot_active(p, s)]


def _route_off(p: Patch, slot: str) -> Patch:
    return replace(p, **{f"{slot}_src": "none", f"{slot}_amt": 0.0})


@dataclass
class _Ctx:
    depth: Depth
    r: np.random.Generator
    amount: float
    prof: breed.Litter
    steer: Steer
    donors: list[dict]  # library entries {name, family, patch}
    others: list[Stack]  # the other parents (layer crossover)
    profile: Probe | None  # the origin parent's main probe (role gaps)


def _fx_choices(where: str, ctx: _Ctx) -> list[str]:
    kinds = MAIN_FX if where == "main" else ROLES[where].fx
    return [k for k in kinds if ctx.depth.spatial or k not in SPATIAL]


def _new_layer(role: str, base: Patch, ctx: _Ctx, donor: dict | None = None) -> Layer:
    r = ctx.r
    if donor is not None:
        patch = recipe(role, Patch.from_dict(donor["patch"]), r, fresh=False)
    else:
        seed_patch = breed.mutate(base, amount=0.6, rng=r, max_genes=6)[0]
        patch = recipe(role, seed_patch, r, fresh=True)
    patch = replace(patch, name=f"{role}")
    spec = ROLES[role]
    lay = Layer(
        role=role,
        patch=patch,
        octave=int(r.choice(spec.octaves)),
        db=float(r.uniform(*spec.db)),
        donor=str(donor["name"]) if donor else "",
    )
    lay.fx = _role_fx(lay, ctx)
    return lay


def _role_fx(lay: Layer, ctx: _Ctx) -> list[dict]:
    """A role's signature FX: a pad's 200 Hz high-pass and chorus (or a
    reverb at deep), a pluck's synced delay, air's width; then up to the
    depth's minimum."""
    r = ctx.r
    out: list[dict] = []
    if lay.role == "pad":
        hp = _fx_unit("highpass", r)
        hp["params"]["cutoff"] = 200.0
        out.append(hp)
        wet = "reverb" if ctx.depth.spatial and r.random() < 0.5 else "chorus"
        out.append(_fx_unit(wet, r))
    elif lay.role == "pluck":
        out.append(_fx_unit("delay", r))
    elif lay.role == "air":
        out.append(_fx_unit("width", r))
    choices = _fx_choices(lay.role, ctx)
    while len(out) < ctx.depth.layer_fx[0] and choices:
        out.append(_fx_unit(choices[int(r.integers(len(choices)))], r))
    return out[: max(ctx.depth.layer_fx[1], 1)]


def _add_layer(s: Stack, ctx: _Ctx, graft: bool = False) -> str | None:
    if len(s.layers) >= ctx.depth.max_layers:
        return None
    w = _gap_weights(ctx.profile, set(s.roles()))
    w = {k: v * ctx.steer.roles.get(k, 1.0) for k, v in w.items()}
    role = _pick(w, ctx.r)
    if role is None:
        return None
    donor = None
    if graft:
        pool = [d for d in ctx.donors if d["family"] in ROLES[role].families]
        if not pool:
            return None
        donor = pool[int(ctx.r.integers(len(pool)))]
    s.layers.append(_new_layer(role, s.patch, ctx, donor))
    return role


def _remove_layer(s: Stack, ctx: _Ctx) -> bool:
    if len(s.layers) <= ctx.depth.min_layers or not s.layers:
        return False
    prefer = [i for i, lay in enumerate(s.layers) if lay.role in ctx.steer.drop_roles]
    idx = prefer or list(range(len(s.layers)))
    s.layers.pop(idx[int(ctx.r.integers(len(idx)))])
    return True


def _reroll_layer(s: Stack, ctx: _Ctx) -> bool:
    if not s.layers:
        return False
    i = int(ctx.r.integers(len(s.layers)))
    old = s.layers.pop(i)
    if _add_layer(s, ctx) is None:
        s.layers.insert(i, old)
        return False
    return True


def _mutate_layer(s: Stack, ctx: _Ctx) -> bool:
    if not s.layers:
        return False
    lay = s.layers[int(ctx.r.integers(len(s.layers)))]
    child, _ = breed.mutate(
        lay.patch,
        amount=0.5 * ctx.amount,
        rng=ctx.r,
        drift_prob=ctx.prof.drift_prob,
        max_genes=4,
    )
    lay.patch = replace(recipe(lay.role, child, ctx.r, fresh=False), name=lay.role)
    return True


def _nudge_layer(s: Stack, ctx: _Ctx) -> bool:
    if not s.layers:
        return False
    lay = s.layers[int(ctx.r.integers(len(s.layers)))]
    spec = ROLES[lay.role]
    others = [o for o in spec.octaves if o != lay.octave]
    if others and ctx.r.random() < 0.4:
        lay.octave = int(ctx.r.choice(others))
    else:
        lo, hi = spec.db
        lay.db = float(np.clip(lay.db + ctx.r.choice([-3.0, 3.0]), lo, hi))
    return True


def _chains(s: Stack) -> list[tuple[str, list]]:
    return [("main", s.main_fx)] + [(lay.role, lay.fx) for lay in s.layers]


def _cap(where: str, ctx: _Ctx) -> int:
    return ctx.depth.max_main_fx if where == "main" else ctx.depth.layer_fx[1]


def _add_fx(s: Stack, ctx: _Ctx) -> str | None:
    r = ctx.r
    room = [(w, c) for w, c in _chains(s) if len(c) < _cap(w, ctx)]
    if not room:
        return None
    where, chain = room[int(r.integers(len(room)))]
    have = {u["kind"] for u in chain}
    weights = {
        k: ctx.steer.fx_add.get(k, 1.0)
        for k in _fx_choices(where, ctx)
        if k not in have
    }
    kind = _pick(weights, r)
    if kind is None:
        return None
    chain.append(_fx_unit(kind, r))
    return kind


def _remove_fx(s: Stack, ctx: _Ctx) -> bool:
    units = [(c, i) for w, c in _chains(s) for i in range(len(c))]
    if ctx.depth.name == "deep":  # a deep layer keeps its minimum
        units = [
            (c, i)
            for w, c in _chains(s)
            for i in range(len(c))
            if w == "main" or len(c) > ctx.depth.layer_fx[0]
        ]
    if not units:
        return False
    prefer = [(c, i) for c, i in units if c[i]["kind"] in ctx.steer.fx_drop]
    pool = prefer or units
    c, i = pool[int(ctx.r.integers(len(pool)))]
    c.pop(i)
    return True


def _replace_fx(s: Stack, ctx: _Ctx) -> bool:
    units = [(w, c, i) for w, c in _chains(s) for i in range(len(c))]
    if not units:
        return False
    w, c, i = units[int(ctx.r.integers(len(units)))]
    have = {u["kind"] for u in c}
    choices = [k for k in _fx_choices(w, ctx) if k not in have]
    if not choices:
        return False
    c[i] = _fx_unit(choices[int(ctx.r.integers(len(choices)))], ctx.r)
    return True


def _nudge_fx(s: Stack, ctx: _Ctx) -> bool:
    units = [u for _w, c in _chains(s) for u in c]
    if not units:
        return False
    u = units[int(ctx.r.integers(len(units)))]
    ranges = _FX_RANGES.get(u["kind"], {})
    names = [k for k in ranges if ranges[k][0] != ranges[k][1] and k != "sync"]
    if not names:
        return False
    name = names[int(ctx.r.integers(len(names)))]
    lo, hi = ranges[name]
    u["params"][name] = float(
        np.clip(u["params"][name] + ctx.r.normal(0, 0.25) * (hi - lo), lo, hi)
    )
    return True


def _curve_points(gene: str, value: float, shape: str, r: np.random.Generator):
    lo, hi = CURVABLE[gene]
    v = float(np.clip((value - lo) / ((hi - lo) or 1.0), 0.02, 0.98))
    d = float(r.uniform(0.15, 0.35))
    ends = {
        "rise": [[0.0, v - d / 2], [1.0, v + d / 2]],
        "fall": [[0.0, v + d / 2], [1.0, v - d / 2]],
        "dip": [[0.0, v], [0.5, v - d], [1.0, v]],
        "swell": [[0.0, v], [0.5, v + d], [1.0, v]],
    }[shape]
    return [[round(t, 3), round(float(np.clip(y, 0.0, 1.0)), 3)] for t, y in ends]


def _add_curve(s: Stack, ctx: _Ctx) -> str | None:
    if len(s.curves) >= ctx.depth.max_curves:
        return None
    heard = live(s.patch)
    genes = [g for g in _CURVE_GENES if g in heard and g not in s.curves]
    if not genes:
        return None
    r = ctx.r
    want = ctx.steer.curve
    if want and want[0] in genes and r.random() < 0.7:
        gene, shape = want
    else:
        gene = genes[min(int(r.integers(len(genes))), int(r.integers(len(genes))))]
        shape = CURVE_SHAPES[int(r.integers(len(CURVE_SHAPES)))]
    val = getattr(s.patch, gene)
    if gene == "cutoff":  # a log gene on a linear curve: move near it
        val = float(val)
    s.curves[gene] = _curve_points(gene, float(val), shape, r)
    return gene


def _remove_curve(s: Stack, ctx: _Ctx) -> bool:
    if not s.curves:
        return False
    keys = sorted(s.curves)
    s.curves.pop(keys[int(ctx.r.integers(len(keys)))])
    return True


def _route(s: Stack, ctx: _Ctx) -> bool:
    """One mod route on or off, as one move (`breed._route_on`)."""
    on = _routes(s.patch)
    r = ctx.r
    if on and (len(on) >= ctx.depth.max_routes or r.random() < 0.35):
        s.patch = _route_off(s.patch, on[int(r.integers(len(on)))])
        return True
    free = [x for x in MOD_GENE_SLOTS if x not in on]  # the four the breeder walks
    if not free:
        return False
    slot = free[0]
    s.patch = replace(s.patch, **breed._route_on(slot, r))
    return True


def _cross(s: Stack, ctx: _Ctx) -> bool:
    """Swap in a whole layer from another parent, by role."""
    pool = [lay for o in ctx.others for lay in o.layers]
    if not pool:
        return False
    lay = pool[int(ctx.r.integers(len(pool)))]
    lay = replace(lay, fx=copy.deepcopy(lay.fx))
    same = [i for i, x in enumerate(s.layers) if x.role == lay.role]
    if same:
        s.layers[same[0]] = lay
    elif len(s.layers) < ctx.depth.max_layers:
        s.layers.append(lay)
    else:
        s.layers[int(ctx.r.integers(len(s.layers)))] = lay
    return True


def _clean(s: Stack, ctx: _Ctx) -> bool:
    """Cleaner: the main's noise layer and drive come down."""
    p = s.patch
    if p.noise_level <= 0.01 and p.drive <= 1.05:
        return False
    s.patch = replace(p, noise_level=0.0, drive=float(max(1.0, p.drive * 0.6)))
    return True


_OPS = {
    "add_layer": (_add_layer, 1.0),
    "remove_layer": (_remove_layer, 0.5),
    "reroll_layer": (_reroll_layer, 0.6),
    "mutate_layer": (_mutate_layer, 1.2),
    "nudge_layer": (_nudge_layer, 0.8),
    "add_fx": (_add_fx, 1.2),
    "remove_fx": (_remove_fx, 0.6),
    "replace_fx": (_replace_fx, 0.5),
    "nudge_fx": (_nudge_fx, 0.8),
    "add_curve": (_add_curve, 0.7),
    "remove_curve": (_remove_curve, 0.3),
    "route": (_route, 0.6),
    "cross": (_cross, 0.6),
    "graft": (lambda s, ctx: _add_layer(s, ctx, graft=True), 0.5),
    "clean": (_clean, 0.0),
}

# structure moves a child makes, by wildness level: (fewest, most)
_MOVES = {"tame": (0, 1), "normal": (1, 2), "wild": (2, 3), "feral": (3, 4)}


def _op_weights(s: Stack, ctx: _Ctx) -> dict[str, float]:
    d = ctx.depth
    w = {name: base * ctx.steer.ops.get(name, 1.0) for name, (_f, base) in _OPS.items()}
    if len(s.layers) >= d.max_layers:
        w["add_layer"] = w["graft"] = 0.0
    if len(s.layers) <= d.min_layers:
        w["remove_layer"] = 0.0
    if not s.layers:
        w["reroll_layer"] = w["mutate_layer"] = w["nudge_layer"] = 0.0
    if d.max_curves == 0 or len(s.curves) >= d.max_curves:
        w["add_curve"] = 0.0
    if not s.curves:
        w["remove_curve"] = 0.0
    if not ctx.others:
        w["cross"] = 0.0
    if not ctx.donors or ctx.prof.donor_prob <= 0:
        w["graft"] = 0.0
    return w


def repair(s: Stack, ctx: _Ctx) -> None:
    """The depth's caps and the roles' recipes, after every child's moves:
    one layer per role, recipes held, FX trimmed to their caps (a deep
    layer topped up to its minimum), routes and curves capped, a curve on
    a gene gone dead dropped, and a sub layer's 120-180 Hz high-pass on the
    main (and a pluck) so the low end is the sub's."""
    d, r = ctx.depth, ctx.r
    seen: set[str] = set()
    kept = []
    for lay in s.layers:
        if lay.role in seen:
            continue
        seen.add(lay.role)
        lay.patch = replace(recipe(lay.role, lay.patch, r, fresh=False), name=lay.role)
        if lay.octave not in ROLES[lay.role].octaves:
            lay.octave = ROLES[lay.role].octaves[0]
        lay.fx = clean_chain(lay.fx)[: d.layer_fx[1]]
        choices = _fx_choices(lay.role, ctx)
        while len(lay.fx) < d.layer_fx[0] and choices:
            have = {u["kind"] for u in lay.fx}
            left = [k for k in choices if k not in have] or choices
            lay.fx.append(_fx_unit(left[int(r.integers(len(left)))], r))
        kept.append(lay)
    s.layers = kept[: d.max_layers]
    while len(s.layers) < d.min_layers and _add_layer(s, ctx) is not None:
        pass
    has_sub = "sub" in s.roles()
    s.main_fx = [
        u
        for u in clean_chain(s.main_fx)
        if d.spatial or u["kind"] not in SPATIAL
        if has_sub or not _is_sub_hp(u)
    ]
    extra = 0
    if has_sub:
        hp = [u for u in s.main_fx if u["kind"] == "highpass"]
        if not hp:
            unit = _fx_unit("highpass", r)
            unit["params"]["cutoff"] = float(r.uniform(120.0, 180.0))
            s.main_fx.insert(0, unit)
        elif hp[0]["params"]["cutoff"] < 120.0:
            hp[0]["params"]["cutoff"] = float(r.uniform(120.0, 180.0))
        extra = 1
        for lay in s.layers:
            if lay.role == "pluck" and not any(u["kind"] == "highpass" for u in lay.fx):
                unit = _fx_unit("highpass", r)
                unit["params"]["cutoff"] = 150.0
                lay.fx.insert(0, unit)
    s.main_fx = s.main_fx[: d.max_main_fx + extra]
    for lay in s.layers:
        lay.fx = lay.fx[: max(d.layer_fx[1], 1) + (1 if has_sub else 0)]
    on = _routes(s.patch)
    for slot in on[d.max_routes :]:
        s.patch = _route_off(s.patch, slot)
    heard = live(s.patch)
    s.curves = {k: v for k, v in s.curves.items() if k in heard and k in CURVABLE}
    for k in sorted(s.curves)[d.max_curves :]:
        s.curves.pop(k)


def _is_sub_hp(u: dict) -> bool:
    return u["kind"] == "highpass" and 119.0 <= u["params"]["cutoff"] <= 181.0


def grow(child: Stack, origin: Stack, others: list[Stack], ctx: _Ctx) -> Stack:
    """A child's structure moves on its origin parent's structure: first the
    depth's fill (a layer at p_first / p_second), then its wildness's worth
    of moves (more at deep), then `repair`."""
    r, d = ctx.r, ctx.depth
    s = child
    if len(s.layers) == 0 and r.random() < d.p_first:
        _add_layer(s, ctx, graft=bool(ctx.donors) and r.random() < ctx.prof.donor_prob)
    elif len(s.layers) == 1 and r.random() < d.p_second:
        _add_layer(s, ctx)
    lo, hi = _MOVES.get(ctx.prof.level, (1, 2))
    moves = int(r.integers(lo, hi + 1)) + d.extra_ops
    for _ in range(moves):
        w = _op_weights(s, ctx)
        name = _pick(w, r)
        if name is None:
            break
        _OPS[name][0](s, ctx)
    repair(s, ctx)
    return s


def litter(
    parents: list[Stack],
    n: int,
    amount: float,
    depth: str,
    rng: np.random.Generator,
    donors: list[dict],
    direction: str | None = None,
    profiles: list[Probe | None] | None = None,
) -> list[tuple[Stack, Stack, list[str]]]:
    """(child, origin, main-patch badges) for `n` children. The main patches
    come from `breed.generation` exactly as the single-depth breeder makes
    them; the structure grows on each child's origin parent."""
    d = DEPTH[depth]
    origins: list[int] = []
    kids = breed.generation(
        [p.patch for p in parents],
        n=n,
        amount=amount,
        rng=rng,
        donors=[Patch.from_dict(e["patch"]) for e in donors],
        origins=origins,
        bias=direction,
    )
    prof = breed.litter_profile(amount)
    steer = STEERS.get(direction or "", Steer())
    out = []
    for (patch, badges), oi in zip(kids, origins, strict=True):
        origin = parents[oi]
        child = origin.copy()
        child.patch = patch
        ctx = _Ctx(
            depth=d,
            r=rng,
            amount=amount,
            prof=prof,
            steer=steer,
            donors=donors,
            others=[p for i, p in enumerate(parents) if i != oi],
            profile=profiles[oi] if profiles else None,
        )
        grow(child, origin, ctx.others, ctx)
        out.append((child, origin, badges))
    return out


# --- badges ------------------------------------------------------------------


def _label(gene: str) -> str:
    from .paramui import spec_of

    spec = spec_of(gene)
    return spec.label if spec else gene.replace("_", " ")


def _curve_shape(points) -> str:
    ys = [y for _t, y in points]
    if len(ys) == 3:
        return "dip" if ys[1] < ys[0] else "swell"
    return "rise" if ys[-1] >= ys[0] else "fall"


def _oct(o: int) -> str:
    return "" if o == 0 else f" {'+' if o > 0 else '−'}{abs(o)} oct"


def _heard_octave(lay: Layer) -> int:
    """Where a layer sounds against the main: its follow octave, plus
    air's own osc1 octave (air's +2 lives there — follow stops at +1)."""
    return lay.octave + (int(lay.patch.osc1_octave) if lay.role == "air" else 0)


# what a role brings with it, never worth its own badge next to "+pad"
_SIGNATURE = {
    "sub": {"highpass"},
    "pad": {"highpass"},
    "pluck": {"delay", "highpass"},
    "air": {"width"},
}


def structure_badges(child: Stack, origin: Stack) -> list[str]:
    """What changed in the stack, structure first: "+sub −1 oct",
    "+pad (Sable Wire)", "reverb", "Cutoff ↗", "2 mods"."""
    out: list[str] = []
    before = {lay.role: lay for lay in origin.layers}
    after = {lay.role: lay for lay in child.layers}
    quiet: set[str] = set()
    for role, lay in after.items():
        if role not in before:
            quiet |= _SIGNATURE.get(role, set())
            out.append(
                f"+{role} ({lay.donor})"
                if lay.donor
                else f"+{role}{_oct(_heard_octave(lay))}"
            )
        elif _heard_octave(lay) != _heard_octave(before[role]):
            out.append(f"{role}{_oct(_heard_octave(lay)) or ' same oct'}")
    out += [f"−{role}" for role in before if role not in after]
    old_fx, new_fx = fx_kinds(origin), fx_kinds(child)
    out += sorted((new_fx - old_fx) - quiet)
    out += [f"−{k}" for k in sorted(old_fx - new_fx)]
    for gene, pts in sorted(child.curves.items()):
        if origin.curves.get(gene) != pts:
            out.append(f"{_label(gene)} {_SHAPE_MARK[_curve_shape(pts)]}")
    n_old, n_new = len(_routes(origin.patch)), len(_routes(child.patch))
    if n_new != n_old:
        out.append(f"{n_new} mod{'s' if n_new != 1 else ''}" if n_new else "no mods")
    return out


def chips(stack: Stack) -> list[dict]:
    """The card's layer chips: [MAIN saw][SUB −1][PAD]."""
    main = stack.patch.osc1 or "main"
    out = [{"role": "main", "label": str(main)}]
    for lay in stack.layers:
        o = _heard_octave(lay)
        out.append({"role": lay.role, "label": _oct(o).replace(" oct", "").strip()})
    return out


# --- the pick ----------------------------------------------------------------


@dataclass(eq=False)
class Candidate:
    stack: Stack
    origin: Stack
    badges: list[str]
    m: Probe
    score: float = 0.0
    main_db: float = 0.0  # the main lane alone (`main_loudness_db`)


def combined(a: Candidate, b_vec: np.ndarray, b_stack: Stack) -> float:
    return distance(a.m.vector, b_vec) + LAMBDA * struct_distance(a.stack, b_stack)


def _maximin(
    pool: list[Candidate], anchors: list[tuple[np.ndarray, Stack]], n: int
) -> list[Candidate]:
    chosen: list[Candidate] = []
    rest = list(pool)
    while rest and len(chosen) < n:
        refs = anchors + [(c.m.vector, c.stack) for c in chosen]
        gaps = [min((combined(c, v, s) for v, s in refs), default=1e9) for c in rest]
        chosen.append(rest.pop(int(np.argmax(gaps))))
    return chosen


def pick(
    rendered: list[Candidate],
    parents: list[tuple[np.ndarray, Stack]],
    score,
    n: int,
    eps: float,
    keep: float,
) -> list[Candidate]:
    """The quiet / sub-only / main-alone gate first (`card_ok`); the steered
    best `keep` share; novel against every parent on the combined distance;
    maximin for all but
    `EXPLORERS` slots, which are filled from the whole audible pool (so a
    steered board still shows two ways off the beaten track); then the
    fallbacks keep the board full."""
    ok = [c for c in rendered if card_ok(c)]
    quiet = [c for c in rendered if not card_ok(c)]
    if score is not None:
        for c in ok:
            c.score = float(score(c.m))
        ranked = sorted(ok, key=lambda c: -c.score)
        k = max(n, math.ceil(len(ranked) * keep))
        steered, spare = ranked[:k], ranked[k:]
    else:
        steered, spare = ok, []

    def novel(c: Candidate) -> bool:
        return all(combined(c, v, s) >= eps for v, s in parents)

    main_n = max(0, n - EXPLORERS)
    chosen = _maximin([c for c in steered if novel(c)], parents, main_n)
    for tier in (
        [c for c in ok if novel(c)],  # the explorers: the whole audible pool
        [c for c in steered if not novel(c)],
        spare,
        quiet,
    ):
        if len(chosen) >= n:
            break
        taken = {id(c) for c in chosen}
        rest = [c for c in tier if id(c) not in taken]
        anchors = parents + [(c.m.vector, c.stack) for c in chosen]
        chosen += _maximin(rest, anchors, n - len(chosen))
    return chosen


def measure_all(
    children: list[tuple[Stack, Stack, list[str]]], bpm: float
) -> list[Candidate]:
    probes = pmap(lambda item: measure_stack(item[0], bpm), children)
    out = []
    for (child, origin, badges), m in zip(children, probes, strict=True):
        if m is None:
            continue
        # a plain stack's probe IS its main; a layered one reads the main's
        # render `stage` already memoised
        main = m.texture.loudness_db if child.plain else main_loudness_db(child, bpm)
        out.append(Candidate(child, origin, badges, m, main_db=float(main)))
    return out


def depth_of(name) -> str:
    return name if name in DEPTH else "single"


__all__ = [
    "DEPTH",
    "DEPTHS",
    "FOLLOW_OCTAVES",
    "LAMBDA",
    "PARAMS",
    "ROLES",
    "Candidate",
    "Layer",
    "Stack",
    "chain_of",
    "chips",
    "litter",
    "measure_all",
    "measure_stack",
    "pick",
    "repair",
    "row_sound",
    "stack_of_row",
    "struct_distance",
    "structure_badges",
]
