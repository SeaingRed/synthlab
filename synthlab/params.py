"""synthlab.params — the one registry of what every Patch field *is*.

Every `Patch` field has exactly one `Param` here, and everything the engine
side knows about a field is on it:

    kind    "lin" | "log" | "int" | "cat" | "meta" (not a sound parameter)
    gene    breeding domain: (lo, hi) for numbers, the choices for "cat",
            None for fields that are not part of the genome. It is also the
            studio slider's range: the two were always the same numbers.
    cap     what `clamp()` accepts from untrusted input. Defaults to `gene`;
            wider where the musical gene range is deliberately narrower than
            what hand-made sounds legitimately use.
    curve   range a drawn automation curve maps 0..1 onto (None: not drawable)
    mod     span a mod-matrix route's amount is a fraction of (None: not a
            route target)
    live    `live(p) -> bool`: is this gene audible enough on `p` to be worth
            a mutation? Depth/level genes stay live so mutation can still
            switch a module on.
    nogene  why a field with a cap and a control is NOT a gene: the
            performance controls (pitch_bend, bend_range). Their `bounds`
            (the cap) stand in for the gene range as the slider's range and
            `norm`'s; random_patch and mutation never touch them.

The studio's side (labels, hints, panels, the live-voice gate) is
`studio/paramui.py`, keyed by the same names.

ORDER MATTERS. `PARAMS` is in the breeder's historical table order (log,
lin, int, then categorical genes): seeded breeding draws its random numbers
in this order, so reordering it changes every seeded litter.
`tests/test_params.py` pins it.

Two different "is it on?" questions live here, on purpose:
  * `slot_active` is the ENGINE's rule (does `_apply_mod_matrix` apply this
    route at all) — the live-voice gate and the chain bar ask this;
  * `live` is the BREEDER's rule (is it audible enough to spend a mutation
    on), with perceptual thresholds (|amt| >= 0.05, depth >= 5 Hz, ...).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from typing import Any

import numpy as np

from . import sampler, wavetables
from .core import (
    FM4_ALGO_NAMES,
    SYNC_SHAPES,
    WARP_MODES,
    WAVETABLE_NAMES,
    clean_draw,
)

# --- the choice domains -----------------------------------------------------

# The route sources. Eight follow the gene's five: LFO 2 and 3, the
# two general envelopes, the mod wheel and aftertouch (performance
# controls, as pitch_bend is), the key's place on the keyboard and a random
# value per note (seeded from the note's pitch, as drift is)
MOD_SOURCES = (
    "none",
    "lfo",
    "fenv",
    "aenv",
    "velocity",
    "lfo2",
    "lfo3",
    "env3",
    "env4",
    "wheel",
    "aftertouch",
    "key",
    "random",
)
# what a bred route draws from: the five it always did (a longer source
# gene re-draws every seeded litter); a hand-made route to the rest is kept
# by `clamp` (the src Param's cap)
MOD_GENE_SOURCES = MOD_SOURCES[:5]
# Two routings is the ceiling a patch hits as soon as it becomes a sound
# rather than a preset. The slots are uniform, so every table below is built
# from this list; a fifth slot is one entry here (plus its Patch fields).
# Twelve of them: slots 5-12 are no genes (MOD_GENE_SLOTS are
# the four the breeder walks, so seeded litters stand)
MOD_SLOTS = tuple(f"mod{k}" for k in range(1, 13))
MOD_GENE_SLOTS = MOD_SLOTS[:4]
# The route target menu, in menu order. Its order is also the categorical
# gene's choice order (seeded breeding picks by index), so it is written out
# rather than derived; each target's span is its Param's `mod`.
MOD_DESTINATIONS = (
    "cutoff",
    "resonance",
    "drive",
    "filter_env",
    "osc1_level",
    "osc2_level",
    "sub_level",
    "noise_level",
    "osc1_detune",
    "osc2_detune",
    "pwm_depth",
    "vibrato_depth",
    "tremolo_depth",
    "filter_lfo_depth",
    # osc2's synced pitch (the sync sweep) — not a gene choice
    "sync_pitch",
    # each oscillator's warp amount — not gene choices either
    "osc1_warp_amt",
    "osc2_warp_amt",
    # the second filter, the wavetable
    # position, the FM amounts, the fm4 operators' levels, the vowel — no
    # gene choices
    "filter2_cutoff",
    "filter2_resonance",
    "wt_pos",
    "fm_amount",
    "fm_index",
    "fm4_level1",
    "fm4_level2",
    "fm4_level3",
    "fm4_level4",
    "formant_vowel",
    "pitch",
)
ROUND6_DESTINATIONS = MOD_DESTINATIONS[
    MOD_DESTINATIONS.index("filter2_cutoff") : MOD_DESTINATIONS.index("pitch")
]
# "pitch" is the one target that is not a Patch field: a route to it bends
# every oscillator by amount x PITCH_ROUTE_ST semitones x the source (an LFO
# at amount 1 swings +-12 st; several routes add). It is last in the menu
# and is left out of the genome's choices (MOD_GENE_DESTINATIONS): a random
# LFO bending a bred sound by up to an octave is rarely a sound, and leaving
# the gene's choices as they were keeps every seeded litter unchanged. A
# hand-made route to it is kept by `clamp` (the dst Param's cap).
PITCH_ROUTE = "pitch"
PITCH_ROUTE_SPAN = (-12.0, 12.0)
PITCH_ROUTE_ST = (PITCH_ROUTE_SPAN[1] - PITCH_ROUTE_SPAN[0]) / 2
# "sync_pitch" is left out too: appended to the menu, it would
# lengthen the gene's choices and re-draw every seeded litter's routes, and
# a random route to it does nothing on the many sounds with no sync
# (and the targets after it, for the same reason)
MOD_GENE_DESTINATIONS = tuple(
    d
    for d in MOD_DESTINATIONS
    if d
    not in (
        PITCH_ROUTE,
        "sync_pitch",
        "osc1_warp_amt",
        "osc2_warp_amt",
        *ROUND6_DESTINATIONS,
    )
)
# core.lfo() dispatches these: WAVEFORMS plus its own "noise" special case
LFO_SHAPES = ("sine", "triangle", "saw", "square", "noise")
# the general LFOs' shapes (LFO 1, 2, 3): those, then a drawn
# curve (`lfo_draw`...), a chaotic map and a smooth random line, all seeded
# and loop-safe (core.lfo_ext). LFO 1's gene keeps the five: the rest are
# its cap, a hand-made choice
GENERAL_LFO_SHAPES = (*LFO_SHAPES, "drawn", "chaos", "smooth")
# "retrig": a general LFO starts each note at its phase; "free": each note
# picks the cycle up where its own seed puts it (core.lfo_free_phase)
LFO_MODES = ("retrig", "free")
# Every LFO rate in the Patch is raw Hz, which is useless for anything that has
# to lock to the groove. A sync division names beats-per-cycle instead;
# synth.at_bpm() resolves it to Hz just before rendering.
SYNC_DIVISIONS: dict[str, float] = {
    "off": 0.0,
    "4 bars": 16.0,
    "2 bars": 8.0,
    "1 bar": 4.0,
    "1/2": 2.0,
    "1/4": 1.0,
    "1/8": 0.5,
    "1/16": 0.25,
    "1/4.": 1.5,  # dotted
    "1/8.": 0.75,
    "1/4T": 2.0 / 3.0,  # triplet
    "1/8T": 1.0 / 3.0,
}
SYNC_CHOICES = tuple(SYNC_DIVISIONS)

OSC1_SHAPES = (
    "sine",
    "saw",
    "square",
    "triangle",
    "supersaw",
    "fm",
    "pluck",
    "wavetable",
)
OSC2_SHAPES = (None, "sine", "saw", "square", "triangle", "fm", "pluck", "wavetable")
# hand-made shapes (in an oscillator's cap, never its gene): "noise",
# "sampler" and "fm4" — a random 4-operator patch is
# rarely a sound, and a longer shape gene would re-draw every seeded litter
HAND_SHAPES = ("noise", "sampler", "fm4")
FILTER_TYPES = ("ladder", "lowpass", "bandpass", "highpass", "notch", "none")
# how many voices a patch plays at once (sequencer.mono_lines),
# and when it glides
VOICE_MODES = ("poly", "mono", "legato")
GLIDE_MODES = ("always", "legato")
# appended so saved indexes stand: the gene keeps
# the six (FILTER_TYPES), these are the type's cap and filter 2's choices.
# "lowpass" is the 12 dB SVF it always was, "ladder" the driven 24 dB
FILTER_TYPES_ALL = (
    *FILTER_TYPES,
    "lowpass24",
    "highpass24",
    "comb_plus",
    "comb_minus",
    "phaser",
    "diode",
    "peak",
)
# where filter 2 sits: nowhere (every older patch), after filter 1, or
# beside it (the two outputs averaged)
FILTER2_ROUTES = ("off", "serial", "parallel")

# An envelope time's hard ceiling: a 4 s pad release is sound design, not
# corruption, however narrow the musical gene range is.
ENV_TIME_CAP = 20.0


# --- liveness rules (the breeder's "is it audible" question) ----------------

Rule = Callable[[Any], bool]


def _always(p) -> bool:
    return True


def _never(p) -> bool:
    return False


@dataclass(frozen=True)
class uses_shape:
    """Live only while osc1 or osc2 plays `shape`. A class rather than a
    lambda so the studio can read *which* shape (paramui.SHAPE_DEPS dims
    the rows of an unused shape from these)."""

    shape: str

    def __call__(self, p) -> bool:
        return self.shape in (p.osc1, p.osc2)


def slot_active(p, slot: str) -> bool:
    """The engine's rule: does `_apply_mod_matrix` apply this route? A real
    source, a real target, and an amount it doesn't skip as zero."""
    src = getattr(p, f"{slot}_src")
    return (
        src in MOD_SOURCES
        and src != "none"
        and getattr(p, f"{slot}_dst") in MOD_RANGES
        and abs(getattr(p, f"{slot}_amt")) >= 1e-3
    )


def _routed(p, source: str) -> bool:
    """Some slot reads `source` with an amount worth mutating around."""
    return any(
        getattr(p, f"{s}_src") == source and abs(getattr(p, f"{s}_amt")) >= 0.05
        for s in MOD_SLOTS
    )


def _filter2_on(p) -> bool:
    return p.filter2_route != "off" and p.filter2_type != "none"


def _osc2_on(p) -> bool:
    return p.osc2 is not None


def _syncable(p) -> bool:  # core.sync's shapes: the others ignore the switch
    return p.osc2 in SYNC_SHAPES


def _synced(p) -> bool:
    return _syncable(p) and p.osc2_sync == "on"


def _filter_on(p) -> bool:  # a bypassed filter has no audible params
    return p.filter_type != "none"


def _fenv_heard(p) -> bool:
    """The filter ADSR shapes the cutoff sweep (scaled by filter_env, only
    through a live filter) and any mod slot whose source is "fenv"."""
    return (
        _routed(p, "fenv")
        or (_filter_on(p) and abs(p.filter_env) >= 20.0)
        or (_filter2_on(p) and bool(np.any(np.abs(np.asarray(p.filter2_env)) >= 20)))
    )


def _flfo_heard(p) -> bool:  # Hz-scale depth: ~0 is inaudible
    return _filter_on(p) and p.filter_lfo_depth >= 5.0


def _flfo_rate_heard(p) -> bool:  # sync overrides the Hz rate entirely
    return _flfo_heard(p) and p.filter_lfo_sync == "off"


def _lfo_heard(p) -> bool:
    return _routed(p, "lfo")


def _lfo_rate_heard(p) -> bool:
    return _lfo_heard(p) and p.lfo_sync == "off"


def _slot_src_live(slot: str) -> Rule:
    # amt is the on-ramp (always live); src becomes flippable once amt is real
    return lambda p: abs(getattr(p, f"{slot}_amt")) >= 0.05


def _slot_dst_live(slot: str) -> Rule:
    # dst only matters once the slot actually modulates
    return lambda p: (
        abs(getattr(p, f"{slot}_amt")) >= 0.05 and getattr(p, f"{slot}_src") != "none"
    )


# --- the registry ------------------------------------------------------------


@dataclass(frozen=True)
class Param:
    name: str
    kind: str
    gene: tuple | None = None
    cap: tuple | None = None
    curve: tuple | None = None
    mod: tuple | None = None
    live: Rule = _always
    # why a field with a cap is not a gene (a performance control); "" for
    # genes and for "meta" fields, which have no cap either
    nogene: str = ""

    @property
    def numeric(self) -> bool:
        return self.kind in ("lin", "log", "int")

    @property
    def bounds(self) -> tuple | None:
        """The range a control draws and `norm` maps: the gene range, or
        the cap of a field that is not a gene."""
        return self.gene if self.gene is not None else self.cap


def _p(
    name, kind, gene=None, *, cap=None, curve=False, mod=False, live=_always, nogene=""
):
    """`curve=True` / `mod=True` mean "over the gene range" (a non-gene's
    cap) — every drawable and routable field but one (cutoff's route span)
    uses exactly that."""
    if gene is not None and kind != "cat":
        gene = (gene[0], gene[1])
    whole = gene if gene is not None else cap
    return Param(
        name,
        kind,
        gene,
        cap if cap is not None else gene,
        whole if curve is True else (curve or None),
        whole if mod is True else (mod or None),
        # a non-gene keeps a *shape* rule (uses_shape): never a breeding
        # question for it (`live()` walks GENES only), but paramui.SHAPE_DEPS
        # hides its row, and the live gate skips it, while no osc plays it
        live if gene is not None or isinstance(live, uses_shape) else _never,
        nogene,
    )


def _lin(name, lo, hi, **kw):
    return _p(name, "lin", (float(lo), float(hi)), **kw)


def _env(name, lo, hi, **kw):
    """An envelope time: musical gene range, hard cap at ENV_TIME_CAP."""
    return _lin(name, lo, hi, cap=(float(lo), max(float(hi), ENV_TIME_CAP)), **kw)


_SAMPLING = uses_shape("sampler")
_WARP_WHY = "a hand-made phase warp: breeding never draws one"
_VOICE_WHY = "how the keys and the loop play the sound, not the sound itself"
_SAMPLE_WHY = "a sampler setting: it shapes a recording, not a bred sound"

_SLOT_AMTS = [_lin(f"{s}_amt", -1.0, 1.0) for s in MOD_GENE_SLOTS]
_SLOT_CATS = [
    param
    for s in MOD_GENE_SLOTS
    for param in (
        _p(
            f"{s}_src",
            "cat",
            MOD_GENE_SOURCES,
            cap=MOD_SOURCES,
            live=_slot_src_live(s),
        ),
        _p(
            f"{s}_dst",
            "cat",
            MOD_GENE_DESTINATIONS,
            cap=MOD_DESTINATIONS,
            live=_slot_dst_live(s),
        ),
    )
]

_R6_WHY = (
    "the general modulators and the second filter: hand-made, never bred (a "
    "longer genome would re-draw every seeded litter)"
)
_PERF_WHY = (
    "a performance control (a wheel, a key's pressure): a bred value would only "
    "hold a route open, a bred curve only wobble it"
)


def _r6(name, kind, cap, **kw):
    return _p(name, kind, cap=cap, nogene=kw.pop("nogene", _R6_WHY), **kw)


_ENV_T = (0.001, 8.0)  # a general envelope's times (log sliders)
# The second filter, two general envelopes, two
# general LFOs, the mod wheel and aftertouch, route slots 5-12. None is a
# gene — a longer genome re-draws every seeded litter — so each is a
# hand-made setting a sound keeps (its cap is its control's range).
_ROUND6: list[Param] = [
    _r6("filter2_route", "cat", FILTER2_ROUTES),
    _r6("filter2_type", "cat", FILTER_TYPES_ALL),
    _r6("filter2_cutoff", "log", (60.0, 16000.0), curve=True, mod=(60.0, 8000.0)),
    _r6("filter2_resonance", "lin", (0.0, 0.95), curve=True, mod=True),
    _r6("filter2_key_track", "lin", (0.0, 1.0)),
    _r6("filter2_env", "lin", (-6000.0, 6000.0), curve=True),
    *[
        _r6(f"env{k}_{part}", kind, cap)
        for k in (3, 4)
        for part, kind, cap in (
            ("attack", "log", _ENV_T),
            ("decay", "log", _ENV_T),
            ("sustain", "lin", (0.0, 1.0)),
            ("release", "log", _ENV_T),
            ("curve", "lin", (1.0, 6.0)),
        )
    ],
    *[
        _r6(f"lfo{k}_{part}", kind, cap)
        for k in (2, 3)
        for part, kind, cap in (
            ("rate", "log", (0.02, 30.0)),
            ("shape", "cat", GENERAL_LFO_SHAPES),
            ("sync", "cat", SYNC_CHOICES),
            ("phase", "lin", (0.0, 1.0)),
            ("mode", "cat", LFO_MODES),
        )
    ],
    _r6("mod_wheel", "lin", (0.0, 1.0), curve=True, nogene=_PERF_WHY),
    _r6("aftertouch", "lin", (0.0, 1.0), curve=True, nogene=_PERF_WHY),
    *[
        param
        for s in MOD_SLOTS[len(MOD_GENE_SLOTS) :]
        for param in (
            _r6(f"{s}_src", "cat", MOD_SOURCES),
            _r6(f"{s}_dst", "cat", MOD_DESTINATIONS),
            _r6(f"{s}_amt", "lin", (-1.0, 1.0)),
        )
    ],
]

_ALL: list[Param] = [
    # log genes: mutate multiplicatively (musical for Hz). Cutoff runs to
    # 16 kHz (8k couldn't open up); its route span is a separate scale and
    # stays at 8 kHz.
    _p(
        "cutoff",
        "log",
        (60.0, 16000.0),
        curve=True,
        mod=(60.0, 8000.0),
        live=_filter_on,
    ),
    # lin genes: bounds are "musically sane", not engine limits
    _lin("osc1_level", 0.1, 1.0, curve=True, mod=True),
    _lin("osc1_detune", -25.0, 25.0, curve=True, mod=True),
    _lin("osc2_level", 0.0, 1.0, curve=True, mod=True, live=_osc2_on),
    _lin("osc2_detune", -30.0, 30.0, curve=True, mod=True, live=_osc2_on),
    _lin("sub_level", 0.0, 1.0, curve=True, mod=True),
    # a noise *layer* louder than 0.4 rarely breeds into anything musical,
    # but hand-made sounds (Rising Whoosh) go to full level
    _lin("noise_level", 0.0, 0.4, cap=(0.0, 1.0), curve=True, mod=True),
    _lin("resonance", 0.0, 0.95, curve=True, mod=True, live=_filter_on),
    # Laser Zap's negative filter_env sweeps the cutoff down instead of up
    _lin(
        "filter_env",
        0.0,
        6000.0,
        cap=(-6000.0, 6000.0),
        curve=True,
        mod=True,
        live=_filter_on,
    ),
    _env("filter_attack", 0.001, 1.5, live=_fenv_heard),
    _env("filter_decay", 0.02, 2.0, live=_fenv_heard),
    _lin("filter_sustain", 0.0, 1.0, live=_fenv_heard),
    _env("filter_release", 0.02, 2.0, live=_fenv_heard),
    _lin("key_track", 0.0, 1.0, live=_filter_on),
    _env("attack", 0.001, 1.5),
    _env("decay", 0.02, 2.0),
    _lin("sustain", 0.0, 1.0),
    _env("release", 0.02, 3.0),
    _lin("vibrato_rate", 0.1, 9.0, live=lambda p: p.vibrato_depth >= 0.01),
    _lin("vibrato_depth", 0.0, 0.5, curve=True, mod=True),
    _lin("vibrato_delay", 0.0, 1.0, live=lambda p: p.vibrato_depth >= 0.01),
    _lin("pwm_rate", 0.05, 5.0, live=uses_shape("square")),  # PWM only shapes squares
    _lin("pwm_depth", 0.0, 0.9, curve=True, mod=True, live=uses_shape("square")),
    _lin("tremolo_rate", 0.0, 9.0, live=lambda p: p.tremolo_depth >= 0.01),
    _lin("tremolo_depth", 0.0, 0.6, curve=True, mod=True),
    _lin("drive", 1.0, 4.0, curve=True, mod=True),
    # a slow hand-made slide is legitimate, just not a breeding move
    _lin("glide", 0.0, 0.3, cap=(0.0, 2.0)),
    _lin("unison_detune", 2.0, 30.0, live=lambda p: p.unison > 1),
    _lin("unison_spread", 0.0, 1.0, live=lambda p: p.unison > 1),
    _lin("filter_lfo_rate", 0.05, 8.0, live=_flfo_rate_heard),
    _lin("filter_lfo_depth", 0.0, 4000.0, curve=True, mod=True, live=_filter_on),
    _lin("pitch_env", -24.0, 24.0),
    _env("pitch_env_decay", 0.02, 1.0, live=lambda p: abs(p.pitch_env) >= 0.5),
    _lin("fm_amount", 0.0, 1.0, mod=True, live=_osc2_on),
    _lin("ring_mod", 0.0, 1.0, live=_osc2_on),
    _lin("fm_ratio", 0.5, 12.0, curve=True, live=uses_shape("fm")),
    _lin("fm_index", 0.0, 12.0, curve=True, mod=True, live=uses_shape("fm")),
    _env("fm_decay", 0.0, 2.0, live=uses_shape("fm")),
    _lin("pluck_damping", 0.0, 1.0, live=uses_shape("pluck")),
    _lin("pluck_bright", 0.0, 1.0, live=uses_shape("pluck")),
    _lin("wt_pos", 0.0, 1.0, curve=True, mod=True, live=uses_shape("wavetable")),
    # vowel filter: amount is the on-ramp, so it stays breedable always;
    # which vowel and what size are inaudible until amount opens it
    _lin("formant_amount", 0.0, 1.0, curve=True),
    _lin(
        "formant_vowel",
        0.0,
        1.0,
        curve=True,
        mod=True,
        live=lambda p: p.formant_amount >= 0.05,
    ),
    _lin("formant_size", 0.6, 1.8, live=lambda p: p.formant_amount >= 0.05),
    _lin("mod_lfo_rate", 0.05, 12.0, live=_lfo_rate_heard),
    _lin("env_curve", 1.0, 6.0),
    *_SLOT_AMTS,
    # appended to the lin run: hard sync's sweep
    # (an octave and a half over osc2; a hand-made one goes to four)
    _lin("sync_pitch", 0.0, 19.0, cap=(0.0, 48.0), curve=True, mod=True, live=_synced),
    # the 4-operator FM shape: ratios, levels, per-operator decay, feedback
    *[
        _lin(f"fm4_ratio{k}", 0.5, 12.0, cap=(0.25, 32.0), live=uses_shape("fm4"))
        for k in (1, 2, 3, 4)
    ],
    *[
        _lin(f"fm4_level{k}", 0.0, 1.0, mod=True, live=uses_shape("fm4"))
        for k in (1, 2, 3, 4)
    ],
    *[_env(f"fm4_decay{k}", 0.0, 3.0, live=uses_shape("fm4")) for k in (1, 2, 3, 4)],
    _lin("fm4_feedback", 0.0, 1.0, live=uses_shape("fm4")),
    # analog drift. A wildcard may roll some; mutation only moves an amount
    # a sound already has (it is what changes from note to note, which the
    # breeder's one probe note hears little of: no on-ramp for it)
    _lin("drift", 0.0, 0.5, cap=(0.0, 1.0), live=lambda p: p.drift >= 0.01),
    # int genes
    _p("osc1_octave", "int", (-1, 1)),
    _p("osc2_octave", "int", (-2, 2), live=_osc2_on),
    # musically 1-5 voices; the engine takes up to 16
    _p("unison", "int", (1, 5), cap=(1, 16)),
    # categorical genes. A pure noise oscillator is a real shape core.osc()
    # dispatches (Rising Whoosh, Vapor Floor), just excluded from breeding
    # "sampler" plays recorded audio: a hand-made shape (it needs a sample),
    # never drawn by breeding, like "noise"
    _p("osc1", "cat", OSC1_SHAPES, cap=(*OSC1_SHAPES, *HAND_SHAPES)),
    _p("osc2", "cat", OSC2_SHAPES, cap=(*OSC2_SHAPES, *HAND_SHAPES)),
    _p("wt_table", "cat", WAVETABLE_NAMES, live=uses_shape("wavetable")),
    _p("filter_type", "cat", FILTER_TYPES, cap=FILTER_TYPES_ALL),
    _p(
        "noise_color",
        "cat",
        ("white", "pink", "brown"),
        live=lambda p: p.noise_level >= 0.01,
    ),
    _p("lfo_shape", "cat", LFO_SHAPES, cap=GENERAL_LFO_SHAPES, live=_lfo_heard),
    _p("filter_lfo_shape", "cat", LFO_SHAPES, live=_flfo_heard),
    _p("lfo_sync", "cat", SYNC_CHOICES, live=_lfo_heard),
    _p("filter_lfo_sync", "cat", SYNC_CHOICES, live=_flfo_heard),
    *_SLOT_CATS,
    # categorical genes, appended: hard sync, the fm4 algorithm
    _p("osc2_sync", "cat", ("off", "on"), live=_syncable),
    _p("fm4_algo", "cat", FM4_ALGO_NAMES, live=uses_shape("fm4")),
    # performance controls: a cap and a control, but not genes
    _p(
        "pitch_bend",
        "lin",
        cap=(-1.0, 1.0),
        curve=True,
        nogene="a performance control (the bend wheel's position): a bred "
        "static bend would only transpose the sound, a bred curve only detune it",
    ),
    _p(
        "bend_range",
        "lin",
        cap=(0.0, 24.0),
        nogene="how far the wheel bends is the player's setting, inaudible "
        "until something bends",
    ),
    # the "sampler" shape (synthlab/sampler.py): which part of a recording
    # plays and how. Never genes: a bred start point or chop only garbles
    # somebody's recording, and a sample isn't a number to mutate
    *[
        _p(name, kind, cap=cap, curve=curve, live=_SAMPLING, nogene=_SAMPLE_WHY)
        for name, kind, cap, curve in (
            ("sampler_root", "int", (0, 127), False),
            ("sampler_start", "lin", (0.0, 1.0), False),
            ("sampler_end", "lin", (0.0, 1.0), False),
            ("sampler_loop", "cat", sampler.ON_OFF, False),
            ("sampler_loop_start", "lin", (0.0, 1.0), False),
            ("sampler_loop_end", "lin", (0.0, 1.0), False),
            ("sampler_xfade", "lin", (0.0, 0.5), False),
            ("sampler_keytrack", "cat", ("on", "off"), False),
            ("sampler_mode", "cat", sampler.MODES, False),
            # drawable: a stretch curve warps time across the note
            ("sampler_stretch", "lin", (0.25, 4.0), True),
        )
    ],
    # not the sound: never bred, clamped, drawn or routed
    _p("name", "meta"),
    # callables, not JSON; no UI on purpose (see docs/engine.md, voice_fx)
    _p("voice_fx", "meta"),
    # the sampler's content hash and chops: strings the sampler routes set,
    # cleaned by `clamp` (META_CLEAN), never numbers
    _p("sampler_sample", "meta", live=_SAMPLING),
    _p("sampler_slices", "meta", live=_SAMPLING),
    # appended as one run: hand-made, never
    # genes — a random warp on every bred oscillator is rarely a sound, a
    # bred voice mode would change how a sound's notes play, and new genes
    # would re-draw every seeded litter
    *[
        param
        for k in (1, 2)
        for param in (
            _p(f"osc{k}_warp", "cat", cap=WARP_MODES, nogene=_WARP_WHY),
            _p(
                f"osc{k}_warp_amt",
                "lin",
                cap=(0.0, 1.0),
                curve=True,
                mod=True,
                nogene=_WARP_WHY,
            ),
        )
    ],
    # your own wavetable's content hash (synthlab.wavetables), cleaned by
    # `clamp` (META_CLEAN)
    _p("wt_custom", "meta", live=uses_shape("wavetable")),
    _p("voice_mode", "cat", cap=VOICE_MODES, nogene=_VOICE_WHY),
    _p("glide_mode", "cat", cap=GLIDE_MODES, nogene=_VOICE_WHY),
    # after that run: filter 2, the
    # general envelopes and LFOs, the wheels, route slots 5-12 (_ROUND6),
    # then the general LFOs' drawn shapes, "x:y,..." (core.clean_draw)
    *_ROUND6,
    _p("lfo_draw", "meta"),
    _p("lfo2_draw", "meta"),
    _p("lfo3_draw", "meta"),
    # the sampler's zone map (a multi-sample instrument): a
    # string the zone routes set, cleaned by `clamp` (META_CLEAN)
    _p("sampler_zones", "meta", live=_SAMPLING),
]

PARAMS: dict[str, Param] = {p.name: p for p in _ALL}
assert len(PARAMS) == len(_ALL), "a Param is declared twice"

# the genome, in the order seeded breeding walks it
GENES: tuple[str, ...] = tuple(n for n, p in PARAMS.items() if p.gene is not None)
NUMERIC_GENES: tuple[str, ...] = tuple(n for n in GENES if PARAMS[n].numeric)
CAT_GENES: tuple[str, ...] = tuple(n for n in GENES if PARAMS[n].kind == "cat")

# route target -> span, in menu order. All of these accept per-sample arrays.
MOD_RANGES: dict[str, tuple[float, float]] = {
    d: PITCH_ROUTE_SPAN if d == PITCH_ROUTE else PARAMS[d].mod
    for d in MOD_DESTINATIONS
    if d == PITCH_ROUTE or PARAMS[d].mod is not None
}
assert tuple(MOD_RANGES) == MOD_DESTINATIONS, "every route target needs a mod span"
assert {n for n, p in PARAMS.items() if p.mod} == set(MOD_DESTINATIONS) - {
    PITCH_ROUTE
}, "a Param with a mod span is missing from MOD_DESTINATIONS"


# --- the interface -----------------------------------------------------------


def live(p) -> set[str]:
    """Genes that audibly affect `p` — what mutation spends its budget on.
    Mutating a disabled module's parameters (osc2 detune with osc2 off, PWM
    rate on a saw...) changes the genome but not the sound."""
    return {n for n in GENES if PARAMS[n].live(p)}


# "meta" fields that still reach the engine: cleaned, not capped
META_CLEAN: dict[str, Callable[[Any], str]] = {
    "sampler_sample": sampler.clean_id,
    "sampler_slices": sampler.clean_slices,
    "wt_custom": wavetables.clean_id,
    "lfo_draw": clean_draw,
    "lfo2_draw": clean_draw,
    "lfo3_draw": clean_draw,
    "sampler_zones": sampler.clean_zones,
}


def clamp(p):
    """Clamp every field into its `cap` before it reaches the engine:
    untrusted input (an imported project, an edited state.json) must never
    hang a render or blow up a filter.

    Non-finite or unparseable numbers fall back to the field default; so
    does a categorical value outside its choices. A per-sample automation
    array (np.ndarray only — a JSON list is never legitimate automation:
    arrays exist only at render time, built from `curves`) is left alone,
    and so is any field with no cap (`name`, `voice_fx`)."""
    defaults = type(p)()
    changes: dict = {}
    for f in fields(p):
        name = f.name
        param = PARAMS[name]
        if name in META_CLEAN:
            clean = META_CLEAN[name](getattr(p, name))
            if clean != getattr(p, name):
                changes[name] = clean
            continue
        if param.cap is None:
            continue
        value = getattr(p, name)
        if param.kind == "cat":
            try:
                valid = value in set(param.cap)
            except TypeError:  # unhashable, impersonating a category value
                valid = False
            if not valid:
                changes[name] = getattr(defaults, name)
            continue
        if isinstance(value, np.ndarray):
            continue  # per-sample automation: array-safe, leave alone
        try:
            fval = float(value)
        except (TypeError, ValueError, OverflowError):  # a 400-digit JSON int
            changes[name] = getattr(defaults, name)
            continue
        if not math.isfinite(fval):
            changes[name] = getattr(defaults, name)
            continue
        lo, hi = float(param.cap[0]), float(param.cap[1])
        clamped = min(hi, max(lo, fval))
        new_value = int(clamped) if param.kind == "int" else clamped
        if new_value != value:
            changes[name] = new_value
    return replace(p, **changes) if changes else p


def taper(v: float, lo: float, hi: float) -> float:
    """A value's 0..1 position on a log scale from lo to hi (unclamped)."""
    return math.log(v / lo) / math.log(hi / lo)


def norm(name: str, v: float) -> float:
    """`v` as a 0..1 position across the gene range, log-tapered for "log"
    params (unclamped: a value outside the range lands outside 0..1)."""
    lo, hi = PARAMS[name].bounds
    if hi == lo:
        return 0.0
    if PARAMS[name].kind == "log":
        return taper(v, lo, hi)
    return (v - lo) / (hi - lo)


def denorm(name: str, x: float) -> float:
    """The inverse of `norm`."""
    lo, hi = PARAMS[name].bounds
    if PARAMS[name].kind == "log":
        return lo * (hi / lo) ** x
    return lo + x * (hi - lo)


def lerp(a, b, t: float):
    """`a` blended toward `b` through the genome: lin genes lerp, log genes
    lerp geometrically, int genes round, categorical genes snap from the
    nearer end. Drawn-automation arrays keep `a`'s side."""
    changes: dict = {}
    for name in NUMERIC_GENES:
        va, vb = getattr(a, name), getattr(b, name)
        if not isinstance(va, (int, float)) or not isinstance(vb, (int, float)):
            continue
        if PARAMS[name].kind == "log" and va > 0 and vb > 0:
            out = float(va) * (float(vb) / float(va)) ** t
        else:
            out = float(va) + (float(vb) - float(va)) * t
        changes[name] = round(out) if PARAMS[name].kind == "int" else out
    if t >= 0.5:
        changes.update({name: getattr(b, name) for name in CAT_GENES})
    return replace(a, **changes)
