"""synthlab.studio.recipes — genre recipe sounds.

Whole sounds built the way a genre builds them, from real engine
parameters and real FX units only (nothing here is a new capability).
The first two:

- **Melodic techno** (~125 BPM): a rolling, pumped 1/16 sub; a "top bass"
  of two saws an octave and two below; a supersaw lead gated by a fast
  square LFO on its level while a bar-long LFO bends its character; metallic
  FM shots; a trance-gated choir-like pad; a cinematic long pad; a rising
  air sweep for builds.
- **UK garage / house, lo-fi** (~132 BPM): a mono sine sub; warm keys under
  heavy sidechain ducking; keys through a band-limited, squashed "phone
  mic"; an offbeat stab; a washy, chopped texture pad over a noise bed.

The style packs follow, each appended so the recipes before it keep their
places:

- **Filter house** (~122 BPM, tag `house`): a stab "loop" behind a
  resonant low-pass to sweep, a slow phaser and a hard pump; a gliding
  funk bass; a dry carrier for the vocoder; a lead whose vowel is drawn
  across the phrase; phased strings.
- **Emotive garage** (the garage pack topped up): felt keys (2-op FM — not
  a piano), a vowel pluck that stands in for vocal chops, a sliding ducked
  sub, a dark pad that leaves the chops' band empty.
- **Melodic techno** (topped up): a pluck for the arp, into a dotted
  echo and a hall; a wide unison stab.
- **Trance** (~138 BPM): the rolling bass as a pair — a mid bass and a
  sub, both with a release short enough to keep three sixteenths apart
  and both ducked on the beat; a supersaw lead with an octave over it; a
  stack pad; a gated pad; a pluck with a dotted ping-pong echo.

Then French electro and industrial, and:

- **Psychedelic pop / neo-psych** (~112 BPM, tag `psych`): a bass warmed
  like overdriven tape, chorused and phased; a fuzzed, flanged lead into
  a wavering tape echo; a hazy pad phased, chorused and drowned; FM keys
  with a tremolo through a saturated tape echo; a phased arp; a swell.
- **60s studio** (~116 BPM, tag `sixties`): an organ through the rotary
  speaker; strings and a flute as a tape-replay keyboard played them; a
  round, short bass; a double-tracked lead with a slapback; a backwards
  cymbal.

Then the gaps five artists' styles showed — generic names:

- **Melodic techno** (the pack the label "Anymore" nods at): a Reese bass
  (two saws phasing slowly, a moving low-pass), a dark FM arp pluck into a
  dotted echo, a sung choir-like lead whose vowel moves across the phrase.
- **Trance** ("Arm In Arm"): the bright pluck-lead layered under a big
  supersaw, a pad on the Big Room Hall chain.
- **Filter house / disco** ("Daft Rock"): a string machine (saw + pulse
  through an ensemble of two choruses), a funk bass through the envelope
  filter (the auto-filter's follower).
- **Emotive garage** ("Fred Before"): felt piano keys (a damped plucked
  string — the nearest the synth gets with no recording, and its `how`
  says what it is not), a vowel pad through the OTT.
- **Drop FX** (tag `drops`, every style's): an impact, a sub drop, a noise
  downlifter (Rising Air's mirror), a reverse crash that swells across the
  phrase into a drop (the Backwards Cymbal's longer, wider variant).

Then five genres the packs above do not cover:

- **Hip-hop / trap** (~140, half time, tag `hiphop`): the long 808 (a
  tuned sine falling onto its note, a soft drive, a long decay, a glide
  between notes), a distorted 808, dark FM bell keys, a vowel choir pad,
  an airy flute-ish lead.
- **Drum & bass** (~174, tag `dnb`): a neuro Reese (the techno Reese
  driven, combed and squashed), a rolling sub, an atmospheric pad, a
  liquid stab.
- **Dubstep / bass music** (~140, half time, tag `dubstep`): a wobble bass
  (a filter LFO synced to the eighth: the starter takes it to the eighth
  triplet), a growl (2-op FM through the vowel filter and a squash), a
  sub, a screech lead.
- **Synthwave / 80s** (~100, tag `synthwave`): a chorused poly pad, a poly
  brass stab, an arpeggio bass, a bright saw lead, lush strings (the
  string machine's, slower and wider).
- **Future bass** (~150, half time, tag `future`): supersaw chord stabs
  on the Future Pump chain (squashed, pumped hard), a vowel-chop lead, a
  pitch-bent pluck, a pumped vowel pad.

Then five more genres, on hard sync, the 4-operator FM shape, the
wavetables and drift:

- **Lo-fi hip-hop** (~80, swung, tag `lofi`): a dusty e-piano (fm4's tine
  on the Dusty Tape chain), a drifting jazz-chord pad, a soft sub, a muted
  pluck.
- **Ambient / cinematic** (~70, tag `ambient`): a scanning pad and a vowel
  pad (wavetables whose position a curve walks across the phrase), a
  shimmer bed, a glass bell texture (fm4), a drifting drone, a braam.
- **Chiptune** (~140, tag `chiptune`): a square lead, the pulse table's
  30 % and 15 % frames (a lead and the arp's sound), a triangle bass
  crushed to four bits, the crush table's lead.
- **Hardstyle** (~150, tag `hardstyle`): a hard-synced screech, a
  euphoric supersaw, a reverse bass that swells into each kick, a pumped
  pad (the kick is the kit's).
- **Phonk** (~140, tag `phonk`): an fm4 cowbell for the melody, a
  distorted 808, a dark vowel pad, a hard synced lead.

Among the five before them, the *Growl Bass* is a 4-op stack and the
*Screech Lead* hard synced, the *Neuro Reese* drifts; the *Poly Brass
Stab* was tried with drift and is kept without it (it softened the swell).

What each measures (level, where it sits, whether it holds, whether the
live keys play it) is the table in `tests/studio/test_style_packs.py`.

A recipe is a **format-2 preset** (`patch` + following `layers` + `main_fx`
+ `curves`), because the FX and layers are half of each one and the
factory format (`factory.py`) holds a bare patch. It is browsed as the
"recipes" source (`sounds.SOURCES`) on the Library's Recipes shelf, and Use
is the one load a v2 preset takes (`StudioState.load_sound`). A recipe
with no layers of its own leaves the session's other lanes alone (its
chain's `layers` is None, like a layered card's "Use sound").

Each entry's measured card numbers (`spectrum`, `centroid`, `brightness`)
live in `presets/recipes_meta.json`, written by
`uv run python tools/refresh_sound_meta.py` and drift-checked by
`tests/studio/test_recipes.py`, the same way the factory's literals are.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np

from ..core import SR, n_samples
from ..effects import chain as fx_chain
from ..effects import to_stereo
from ..sequencer import midi, name_of
from ..synth import Patch, Synth, at_bpm
from .curves import CURVABLE, curve_array
from .fxrack import build_chain, chain_preset, clean_chain
from .soundmeta import note_duration
from .state import Arrangement

META_PATH = Path(__file__).with_name("presets") / "recipes_meta.json"

# the tempo each genre is measured at (a session renders at its own)
TECHNO_BPM = 125.0
GARAGE_BPM = 132.0
HOUSE_BPM = 122.0
TRANCE_BPM = 138.0
ELECTRO_BPM = 120.0  # Just Ice
INDUSTRIAL_BPM = 105.0  # Nine Inch Wails
PSYCH_BPM = 112.0  # Psych Haze
SIXTIES_BPM = 116.0  # Sixties Studio
# the drop FX are every style's: measured between techno's and
# trance's tempos (their curves run across the note, whatever the tempo)
DROPS_BPM = 128.0
HIPHOP_BPM = 140.0  # trap, half time
DNB_BPM = 174.0
DUBSTEP_BPM = 140.0
SYNTHWAVE_BPM = 100.0
FUTURE_BPM = 150.0
LOFI_BPM = 80.0
AMBIENT_BPM = 70.0
CHIP_BPM = 140.0
HARDSTYLE_BPM = 150.0
PHONK_BPM = 140.0
# the core library: named for what they are, no style's
CORE_BPM = 120.0
GENRES = (
    "techno",
    "garage",
    "house",
    "trance",
    "electro",
    "industrial",
    "psych",
    "sixties",
    "drops",
    "hiphop",
    "dnb",
    "dubstep",
    "synthwave",
    "future",
    "lofi",
    "ambient",
    "chiptune",
    "hardstyle",
    "phonk",
    # not a genre: the core library's own tag, one a recipe as a pack's is
    "core",
)


def _patch(name: str, **fields) -> dict:
    return Patch(name=name, **fields).to_json()


def _lane(role: str, octave: int, gain: float, fx: list, **fields) -> dict:
    """A following layer, as a format-2 preset carries one."""
    return {
        "patch": _patch(role, **fields),
        "cells": [],
        "muted": False,
        "gain": gain,
        "fx": clean_chain(fx),
        "follow": {"octave": octave},
        "role": role,
    }


def _fx(kind: str, **params) -> dict:
    return {"kind": kind, "params": params}


def _gate(pattern: str, depth: float = 1.0) -> dict:
    return {"kind": "gate", "params": {"depth": depth}, "pattern": pattern}


def _recipe(
    name: str,
    tags: list[str],
    pitch: str,
    bpm: float,
    patch: dict,
    main_fx: list,
    layers: list | None = None,
    curves: dict | None = None,
    how: str = "",
) -> dict:
    return {
        "name": name,
        "tags": tags,
        "pitch": pitch,  # the card's measuring note (a bass is measured low)
        "bpm": bpm,
        "how": how,
        "patch": patch,
        "layers": layers,
        "main_fx": clean_chain(main_fx),
        "curves": curves or {},
    }


RECIPES: list[dict] = [
    # ------------------------------------------------------ melodic techno
    _recipe(
        "Cinematic Gate Lead",
        ["lead", "techno"],
        "C4",
        TECHNO_BPM,
        _patch(
            "Cinematic Gate Lead",
            osc1="supersaw",
            osc1_level=0.55,
            unison=3,
            unison_detune=16.0,
            unison_spread=0.9,
            filter_type="lowpass",
            cutoff=2400.0,
            resonance=0.25,
            filter_env=1200.0,
            filter_decay=0.4,
            filter_sustain=0.5,
            key_track=0.3,
            attack=0.005,
            decay=0.3,
            sustain=0.85,
            release=0.45,
            # the gate: a square LFO at 1/16 swings the level 0.1 <-> 1.0
            lfo_shape="square",
            lfo_sync="1/16",
            mod1_src="lfo",
            mod1_dst="osc1_level",
            mod1_amt=0.5,
            # the slow bend: the filter breathes once a bar, the pitch
            # drifts a tenth of a semitone at about the same rate
            filter_lfo_shape="triangle",
            filter_lfo_sync="1 bar",
            filter_lfo_depth=1400.0,
            vibrato_rate=0.52,
            vibrato_depth=0.1,
            vibrato_delay=0.0,
            drive=1.4,
        ),
        [
            _fx("delay", feedback=0.4, mix=0.22, ping_pong=1.0, sync=3.0),
            _fx("reverb", decay=3.2, mix=0.22, size=0.85),
        ],
        layers=[
            _lane(
                "pad",
                -1,
                0.6,
                [_fx("highpass", cutoff=200.0, q=0.7), _fx("chorus")],
                osc1="saw",
                unison=2,
                unison_detune=10.0,
                filter_type="lowpass",
                cutoff=900.0,
                resonance=0.1,
                filter_env=0.0,
                attack=0.4,
                decay=0.5,
                sustain=0.9,
                release=1.2,
            )
        ],
        how="supersaw gated by a 1/16 square LFO on its level; a 1-bar filter "
        "LFO and slow vibrato bend it across the bar; a pad an octave under",
    ),
    _recipe(
        "Rolling Sidechained Sub",
        ["bass", "techno"],
        "A1",
        TECHNO_BPM,
        _patch(
            "Rolling Sidechained Sub",
            osc1="sine",
            osc1_level=0.9,
            filter_type="lowpass",
            cutoff=260.0,
            resonance=0.1,
            filter_env=300.0,
            filter_decay=0.08,
            filter_sustain=0.0,
            key_track=0.5,
            attack=0.002,
            decay=0.12,
            sustain=0.7,
            release=0.06,
            drive=1.6,
        ),
        [
            # rolling: three 16ths of every beat, the kick's own 16th left out
            _gate(".xxx.xxx.xxx.xxx"),
            _fx("pump", depth=0.8, release=0.2, beats=1.0),
        ],
        how="driven sine sub chopped to the three off-kick 16ths of every "
        "beat, then pumped on the quarter",
    ),
    _recipe(
        "Two-Saw Top Bass",
        ["bass", "techno"],
        "A2",
        TECHNO_BPM,
        _patch(
            "Two-Saw Top Bass",
            osc1="saw",
            osc1_octave=-1,
            osc1_level=0.7,
            osc2="saw",
            osc2_octave=-2,
            osc2_level=0.6,
            osc2_detune=5.0,
            filter_type="ladder",
            cutoff=700.0,
            resonance=0.4,
            filter_env=1800.0,
            filter_decay=0.16,
            filter_sustain=0.1,
            key_track=0.4,
            attack=0.003,
            decay=0.22,
            sustain=0.55,
            release=0.12,
            drive=1.8,
        ),
        [
            _fx("pump", depth=0.55, release=0.25, beats=1.0),
            _fx("compress", threshold=-16.0, ratio=3.0),
        ],
        how="two saws an octave and two below, a plucky ladder, pumped",
    ),
    _recipe(
        "Metal FM Shot",
        ["fx", "techno"],
        "C4",
        TECHNO_BPM,
        _patch(
            "Metal FM Shot",
            osc1="fm",
            fm_ratio=1.41,
            fm_index=7.0,
            fm_decay=0.18,
            osc2="square",
            osc2_octave=1,
            osc2_level=0.3,
            ring_mod=0.35,
            filter_type="highpass",
            cutoff=400.0,
            resonance=0.3,
            filter_env=0.0,
            attack=0.001,
            decay=0.28,
            sustain=0.0,
            release=0.25,
            pitch_env=7.0,
            pitch_env_decay=0.03,
        ),
        [
            _fx("delay", feedback=0.45, mix=0.28, ping_pong=1.0, sync=2.0),
            _fx("reverb", decay=2.4, mix=0.25, size=0.7),
        ],
        how="inharmonic 2-op FM (ratio 1.41) with a ring-modded square, "
        "one short hit into an eighth-note echo",
    ),
    _recipe(
        "Gated Choir Pad",
        ["pad", "techno"],
        "C3",
        TECHNO_BPM,
        _patch(
            "Gated Choir Pad",
            osc1="saw",
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.5,
            osc2_detune=9.0,
            unison=3,
            unison_detune=14.0,
            unison_spread=0.9,
            formant_amount=0.75,
            formant_vowel=0.72,
            formant_size=1.0,
            filter_type="lowpass",
            cutoff=3000.0,
            resonance=0.1,
            filter_env=0.0,
            attack=0.35,
            decay=0.5,
            sustain=0.9,
            release=1.3,
        ),
        [
            _gate("x.xxx.x.x.xxx.x.", depth=0.85),
            _fx("chorus", rate=0.4, depth=4.0, mix=0.4),
            _fx("reverb", decay=3.5, mix=0.35, size=0.9),
        ],
        how="detuned saws through the vowel filter at 'o', trance-gated",
    ),
    _recipe(
        "Long Cinema Pad",
        ["pad", "techno"],
        "C3",
        TECHNO_BPM,
        _patch(
            "Long Cinema Pad",
            osc1="supersaw",
            osc1_level=0.6,
            unison=4,
            unison_detune=14.0,
            unison_spread=1.0,
            filter_type="lowpass",
            cutoff=1400.0,
            resonance=0.15,
            filter_env=900.0,
            filter_attack=1.2,
            filter_decay=2.0,
            filter_sustain=0.6,
            filter_lfo_rate=0.08,
            filter_lfo_depth=400.0,
            attack=1.6,
            decay=1.0,
            sustain=0.9,
            release=3.2,
        ),
        [
            _fx("delay", feedback=0.55, mix=0.22, ping_pong=1.0, sync=3.0),
            _fx("reverb", decay=6.0, mix=0.45, size=1.0),
        ],
        layers=[
            _lane(
                "air",
                1,
                0.18,
                [_fx("width", amount=1.8)],
                osc1="noise",
                osc1_level=0.6,
                noise_color="white",
                key_track=0.0,
                filter_type="highpass",
                cutoff=4500.0,
                resonance=0.1,
                filter_env=0.0,
                attack=2.0,
                decay=1.0,
                sustain=0.9,
                release=3.0,
            )
        ],
        how="slow supersaw swell into a 6 s room and a dotted-eighth echo, "
        "with high air an octave up",
    ),
    _recipe(
        "Rising Air",
        ["fx", "techno"],
        "C4",
        TECHNO_BPM,
        _patch(
            "Rising Air",
            osc1="noise",
            osc1_level=0.8,
            filter_type="bandpass",
            cutoff=1500.0,
            resonance=0.5,
            key_track=0.0,
            filter_env=0.0,
            attack=1.5,
            decay=1.0,
            sustain=1.0,
            release=1.8,
        ),
        [
            _fx("highpass", cutoff=300.0, q=0.7),
            _fx("width", amount=1.8),
            _fx("reverb", decay=4.0, mix=0.35, size=0.9),
        ],
        # the riser: the band-pass climbs across the phrase
        curves={"cutoff": [[0.0, 0.05], [1.0, 0.9]]},
        how="band-passed noise whose cutoff curve climbs the whole phrase",
    ),
    # --------------------------------------------------------- UK garage
    _recipe(
        "Sine Sub",
        ["bass", "garage"],
        "A1",
        GARAGE_BPM,
        _patch(
            "Sine Sub",
            osc1="sine",
            osc1_level=0.9,
            filter_type="lowpass",
            cutoff=350.0,
            resonance=0.05,
            filter_env=0.0,
            attack=0.004,
            decay=0.2,
            sustain=0.85,
            release=0.1,
            drive=1.15,
        ),
        chain_preset("mono_maker"),
        how="a clean, barely warmed sine, forced to mono",
    ),
    _recipe(
        "Phone-Mic Keys",
        ["keys", "garage"],
        "C4",
        GARAGE_BPM,
        _patch(
            "Phone-Mic Keys",
            osc1="fm",
            fm_ratio=1.0,
            fm_index=2.2,
            fm_decay=0.6,
            osc2="sine",
            osc2_octave=1,
            osc2_level=0.25,
            filter_type="lowpass",
            cutoff=5000.0,
            resonance=0.05,
            filter_env=0.0,
            attack=0.003,
            decay=1.0,
            sustain=0.3,
            release=0.5,
            tremolo_rate=4.5,
            tremolo_depth=0.12,
        ),
        chain_preset("phone_mic"),
        how="soft FM keys recorded on a phone: a small room, 300 Hz-3.5 kHz, "
        "squashed, a little grit, mono",
    ),
    _recipe(
        "Warm Garage Keys",
        ["keys", "garage"],
        "C4",
        GARAGE_BPM,
        _patch(
            "Warm Garage Keys",
            osc1="triangle",
            osc1_level=0.75,
            osc2="sine",
            osc2_octave=1,
            osc2_level=0.35,
            osc2_detune=3.0,
            unison=2,
            unison_detune=6.0,
            unison_spread=0.6,
            filter_type="lowpass",
            cutoff=1800.0,
            resonance=0.1,
            filter_env=1600.0,
            filter_decay=0.5,
            filter_sustain=0.2,
            attack=0.004,
            decay=1.2,
            sustain=0.35,
            release=0.7,
            drive=1.2,
        ),
        [
            _fx("chorus", rate=0.5, depth=3.0, mix=0.35),
            _fx("pump", depth=0.65, release=0.3, beats=1.0),
            _fx("reverb", decay=1.6, mix=0.18, size=0.6),
        ],
        how="triangle + sine keys, chorused, ducked hard on every beat",
    ),
    _recipe(
        "Offbeat Stab",
        ["keys", "garage"],
        "C4",
        GARAGE_BPM,
        _patch(
            "Offbeat Stab",
            osc1="square",
            osc1_level=0.6,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.5,
            osc2_detune=12.0,
            unison=2,
            unison_detune=10.0,
            filter_type="lowpass",
            cutoff=1400.0,
            resonance=0.35,
            filter_env=3000.0,
            filter_decay=0.12,
            filter_sustain=0.0,
            attack=0.002,
            decay=0.2,
            sustain=0.0,
            release=0.18,
        ),
        [
            _fx("delay", feedback=0.3, mix=0.2, ping_pong=1.0, sync=2.0),
            _fx("reverb", decay=1.2, mix=0.2, size=0.5),
        ],
        how="a short square/saw chord stab with a snappy filter, for the offbeats",
    ),
    _recipe(
        "Washy Texture Pad",
        ["pad", "garage"],
        "C3",
        GARAGE_BPM,
        _patch(
            "Washy Texture Pad",
            osc1="triangle",
            osc2="saw",
            osc2_octave=1,
            osc2_level=0.3,
            osc2_detune=12.0,
            noise_level=0.12,
            noise_color="pink",
            unison=2,
            filter_type="lowpass",
            cutoff=2200.0,
            resonance=0.3,
            filter_env=0.0,
            filter_lfo_rate=0.15,
            filter_lfo_depth=500.0,
            attack=0.8,
            decay=1.0,
            sustain=0.85,
            release=2.2,
        ),
        [
            _fx("granular", grain=0.12, scatter=0.6, density=3.0, mix=0.5),
            _fx("vinyl", crackle=0.25, hiss=0.2, wow=0.3, warmth=0.6),
            *chain_preset("washy"),
        ],
        how="a band-passed pad over a pink-noise bed, chopped into grains, "
        "with crackle, then the Washy chain",
    ),
    # ------------------------------------------------------- filter house
    _recipe(
        "Chopped Stab Loop",
        ["keys", "house"],
        "C4",
        HOUSE_BPM,
        _patch(
            "Chopped Stab Loop",
            osc1="square",
            osc1_level=0.8,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.7,
            osc2_detune=9.0,
            unison=2,
            unison_detune=8.0,
            unison_spread=0.6,
            filter_type="ladder",
            cutoff=900.0,
            resonance=0.45,
            filter_env=3200.0,
            filter_decay=0.14,
            filter_sustain=0.05,
            key_track=0.4,
            attack=0.002,
            decay=0.22,
            sustain=0.0,
            release=0.12,
            drive=1.5,
        ),
        # the low-pass is there to be swept (a song lane on its cutoff):
        # open here, so the loop sounds as written until something shuts it
        [
            _fx("lowpass", cutoff=9000.0, q=2.5),
            _fx("phaser", rate=0.2, depth=0.7, mix=0.45),
            _fx("pump", depth=0.8, release=0.3, beats=1.0),
        ],
        how="a square/saw chord stab through a snappy ladder, standing in "
        "for a chopped loop: a resonant low-pass to sweep, a slow phaser, "
        "a hard pump",
    ),
    _recipe(
        "Slide Funk Bass",
        ["bass", "house"],
        "A1",
        HOUSE_BPM,
        _patch(
            "Slide Funk Bass",
            osc1="saw",
            osc1_level=0.3,
            sub_level=0.21,
            filter_type="ladder",
            cutoff=420.0,
            resonance=0.4,
            filter_env=1500.0,
            filter_decay=0.1,
            filter_sustain=0.1,
            key_track=0.5,
            attack=0.003,
            decay=0.16,
            sustain=0.5,
            release=0.08,
            glide=0.05,
            drive=1.7,
        ),
        [
            # a driven saw over a sub is lopsided: this takes its offset out
            _fx("highpass", cutoff=25.0, q=0.7),
            _fx("pump", depth=0.85, release=0.3, beats=1.0),
        ],
        how="a saw over its sub through a short ladder envelope, gliding "
        "50 ms between slid notes, ducked hard on the beat",
    ),
    _recipe(
        "Talkbox Carrier",
        ["lead", "house"],
        "C3",
        HOUSE_BPM,
        _patch(
            "Talkbox Carrier",
            osc1="saw",
            osc1_level=0.42,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.28,
            # in tune with the saw: any detune is a slow beat, and a
            # carrier that moves by itself fights the words
            osc2_detune=0.0,
            filter_type="lowpass",
            cutoff=9000.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.3,
            attack=0.005,
            decay=0.1,
            sustain=1.0,
            release=0.08,
            glide=0.06,
            drive=1.8,
        ),
        # dry on purpose; a driven saw + square is lopsided, and this takes
        # its offset out
        [_fx("highpass", cutoff=60.0, q=0.7)],
        how="one bright, driven saw + square voice with glide and nothing "
        "moving: every harmonic there for the vocoder's bands to shape "
        "(Voice: carrier 'this sound')",
    ),
    _recipe(
        "Vowel Lead",
        ["lead", "house"],
        "C4",
        HOUSE_BPM,
        _patch(
            "Vowel Lead",
            osc1="saw",
            osc1_level=0.5,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.27,
            osc2_detune=5.0,
            formant_amount=0.85,
            formant_vowel=0.0,
            formant_size=1.0,
            filter_type="lowpass",
            cutoff=5000.0,
            resonance=0.15,
            filter_env=0.0,
            attack=0.01,
            decay=0.2,
            sustain=0.85,
            release=0.15,
            glide=0.05,
            vibrato_rate=5.5,
            vibrato_depth=0.12,
            vibrato_delay=0.2,
            drive=1.6,
        ),
        [
            _fx("delay", feedback=0.3, mix=0.18, ping_pong=1.0, sync=2.0),
            _fx("reverb", decay=1.4, mix=0.15, size=0.6),
        ],
        # the talking: the vowel walks a -> e -> i -> o across the phrase
        curves={"formant_vowel": [[0.0, 0.05], [0.4, 0.5], [0.7, 0.3], [1.0, 0.8]]},
        how="a gliding saw + square through the vowel filter, the vowel "
        "drawn across the phrase: a talkbox line with no take",
    ),
    _recipe(
        "Phaser String Pad",
        ["pad", "house"],
        "C3",
        HOUSE_BPM,
        _patch(
            "Phaser String Pad",
            osc1="saw",
            osc1_level=0.65,
            osc2="saw",
            osc2_octave=1,
            osc2_level=0.35,
            osc2_detune=8.0,
            unison=3,
            unison_detune=12.0,
            unison_spread=0.8,
            filter_type="lowpass",
            cutoff=2600.0,
            resonance=0.1,
            filter_env=0.0,
            attack=0.25,
            decay=0.6,
            sustain=0.85,
            release=0.9,
        ),
        [
            _fx("phaser", rate=0.2, depth=0.8, mix=0.5),
            _fx("chorus", rate=0.5, depth=3.0, mix=0.35),
            _fx("reverb", decay=2.2, mix=0.25, size=0.8),
        ],
        how="saw strings an octave apart under a slow phaser and chorus",
    ),
    # ---------------------------------------------- emotive garage (top-up)
    _recipe(
        "Felt Keys",
        ["keys", "garage"],
        "C4",
        GARAGE_BPM,
        _patch(
            "Felt Keys",
            osc1="fm",
            fm_ratio=1.0,
            fm_index=1.1,
            fm_decay=0.25,
            osc1_level=0.8,
            filter_type="lowpass",
            cutoff=1800.0,
            resonance=0.05,
            filter_env=900.0,
            filter_decay=0.3,
            filter_sustain=0.1,
            key_track=0.5,
            attack=0.012,
            decay=1.1,
            sustain=0.2,
            release=0.5,
        ),
        chain_preset("tape_lofi"),
        layers=[
            _lane(
                "sub",
                -1,
                0.3,
                [],
                osc1="sine",
                osc1_level=0.8,
                filter_type="lowpass",
                cutoff=600.0,
                resonance=0.05,
                filter_env=0.0,
                attack=0.012,
                decay=0.9,
                sustain=0.2,
                release=0.4,
            )
        ],
        how="2-op FM at ratio 1 with a low, fast-decaying index, a soft "
        "attack and a 1.8 kHz low-pass, a sine an octave under, on worn "
        "tape. Not a piano: no hammer, no strings, one timbre per key",
    ),
    _recipe(
        "Vox Pluck",
        ["lead", "garage"],
        "C4",
        GARAGE_BPM,
        _patch(
            "Vox Pluck",
            osc1="saw",
            osc1_level=0.75,
            formant_amount=0.9,
            formant_vowel=0.15,
            formant_size=1.1,
            filter_type="lowpass",
            cutoff=4200.0,
            resonance=0.15,
            filter_env=1500.0,
            filter_decay=0.12,
            filter_sustain=0.0,
            attack=0.003,
            decay=0.16,
            sustain=0.0,
            release=0.14,
        ),
        [
            _fx("delay", feedback=0.35, mix=0.22, ping_pong=1.0, sync=3.0),
            _fx("reverb", decay=1.8, mix=0.2, size=0.7),
        ],
        # each chop lands on another vowel as the phrase goes by
        curves={"formant_vowel": [[0.0, 0.15], [0.5, 0.75], [1.0, 0.35]]},
        how="a short saw through the vowel filter, the vowel drawn across "
        "the phrase so each note says something else: vocal chops with "
        "no take",
    ),
    _recipe(
        "Ducked Slide Sub",
        ["bass", "garage"],
        "A1",
        GARAGE_BPM,
        _patch(
            "Ducked Slide Sub",
            osc1="sine",
            osc1_level=0.9,
            filter_type="lowpass",
            cutoff=300.0,
            resonance=0.05,
            filter_env=0.0,
            attack=0.005,
            decay=0.3,
            sustain=0.9,
            release=0.14,
            glide=0.09,
            drive=1.2,
        ),
        [
            _fx("pump", depth=0.7, release=0.28, beats=1.0),
            *chain_preset("mono_maker"),
        ],
        how="a long sine sub that slides between slid notes, ducked on the "
        "beat, in mono",
    ),
    _recipe(
        "Chop Bed Pad",
        ["pad", "garage"],
        "C3",
        GARAGE_BPM,
        _patch(
            "Chop Bed Pad",
            osc1="triangle",
            osc1_level=0.75,
            osc2="sine",
            osc2_octave=0,
            osc2_level=0.4,
            osc2_detune=6.0,
            unison=2,
            unison_detune=8.0,
            filter_type="lowpass",
            cutoff=900.0,
            resonance=0.1,
            filter_env=0.0,
            filter_lfo_rate=0.1,
            filter_lfo_depth=200.0,
            attack=0.6,
            decay=1.0,
            sustain=0.85,
            release=1.8,
        ),
        [
            _fx("vinyl", crackle=0.2, hiss=0.15, wow=0.4, warmth=0.7),
            _fx("pump", depth=0.5, release=0.3, beats=1.0),
            _fx("reverb", decay=3.5, mix=0.35, size=0.9),
        ],
        how="a dark triangle pad shut at 900 Hz, so the band a vocal chop "
        "lives in stays empty; crackle, a soft pump, a long room",
    ),
    # --------------------------------------------- melodic techno (top-up)
    _recipe(
        "Evolving Pluck",
        ["lead", "techno"],
        "C4",
        TECHNO_BPM,
        _patch(
            "Evolving Pluck",
            osc1="pluck",
            osc1_level=0.8,
            pluck_damping=0.45,
            pluck_bright=0.8,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.35,
            osc2_detune=6.0,
            filter_type="lowpass",
            cutoff=1600.0,
            resonance=0.3,
            filter_env=2600.0,
            filter_decay=0.18,
            filter_sustain=0.05,
            key_track=0.4,
            attack=0.002,
            decay=0.25,
            sustain=0.0,
            release=0.2,
        ),
        [
            # a short pluck leaves a thump under its note: out, before the echo
            _fx("highpass", cutoff=150.0, q=0.7),
            *chain_preset("dotted_ping_pong"),
            _fx("reverb", decay=4.0, mix=0.3, size=0.95),
        ],
        how="a plucked string over a saw, short, into a dotted-eighth "
        "ping-pong echo and a hall: the arp whose cutoff is opened over "
        "the build",
    ),
    _recipe(
        "Wide Unison Stab",
        ["keys", "techno"],
        "C4",
        TECHNO_BPM,
        _patch(
            "Wide Unison Stab",
            osc1="supersaw",
            osc1_level=0.6,
            unison=5,
            unison_detune=20.0,
            unison_spread=1.0,
            filter_type="lowpass",
            cutoff=3200.0,
            resonance=0.2,
            filter_env=2500.0,
            filter_decay=0.2,
            filter_sustain=0.15,
            key_track=0.3,
            attack=0.003,
            decay=0.3,
            sustain=0.1,
            release=0.25,
        ),
        [
            _fx("width", amount=1.8),
            _fx("reverb", decay=2.5, mix=0.25, size=0.85),
        ],
        how="five supersaws spread hard left and right, short, widened: "
        "the stab kept back for the second drop",
    ),
    # --------------------------------------------------------------- trance
    _recipe(
        "Rolling Mid Bass",
        ["bass", "trance"],
        "A2",
        TRANCE_BPM,
        _patch(
            "Rolling Mid Bass",
            osc1="saw",
            osc1_level=0.34,
            filter_type="lowpass",
            cutoff=700.0,
            resonance=0.2,
            filter_env=900.0,
            filter_decay=0.07,
            filter_sustain=0.2,
            key_track=0.4,
            attack=0.002,
            decay=0.1,
            sustain=0.75,
            # short: what keeps three sixteenths apart (a gated note rings
            # for the patch's release)
            release=0.07,
            drive=1.6,
        ),
        [
            # the sub has the bottom octave: the mid bass stays out of it
            _fx("highpass", cutoff=80.0, q=0.7),
            _fx("pump", depth=0.85, release=0.18, beats=1.0),
        ],
        how="a saw shut at 700 Hz with a 70 ms release, so three "
        "sixteenths stay three notes, ducked on every beat: the upper "
        "half of the rolling bass",
    ),
    _recipe(
        "Rolling Sub",
        ["bass", "trance"],
        "A1",
        TRANCE_BPM,
        _patch(
            "Rolling Sub",
            osc1="sine",
            osc1_level=0.9,
            filter_type="lowpass",
            cutoff=220.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.5,
            attack=0.003,
            decay=0.08,
            sustain=0.85,
            release=0.09,
            drive=1.3,
        ),
        [
            _fx("pump", depth=0.85, release=0.18, beats=1.0),
            *chain_preset("mono_maker"),
        ],
        how="a sine with a 90 ms release, ducked on every beat, in mono: "
        "the same figure an octave under the mid bass",
    ),
    _recipe(
        "Anthem Supersaw",
        ["lead", "trance"],
        "C4",
        TRANCE_BPM,
        _patch(
            "Anthem Supersaw",
            osc1="supersaw",
            osc1_level=0.55,
            unison=7,
            unison_detune=24.0,
            unison_spread=1.0,
            filter_type="lowpass",
            cutoff=5200.0,
            resonance=0.15,
            filter_env=1800.0,
            filter_decay=0.35,
            filter_sustain=0.5,
            key_track=0.4,
            attack=0.004,
            decay=0.3,
            sustain=0.8,
            release=0.35,
            drive=1.2,
        ),
        [
            *chain_preset("dotted_ping_pong"),
            _fx("reverb", decay=3.5, mix=0.28, size=0.9),
            _fx("width", amount=1.5),
        ],
        layers=[
            _lane(
                "air",
                1,
                0.3,
                [_fx("highpass", cutoff=400.0, q=0.7)],
                osc1="supersaw",
                osc1_level=0.55,
                unison=5,
                unison_detune=20.0,
                unison_spread=1.0,
                filter_type="lowpass",
                cutoff=7000.0,
                resonance=0.1,
                filter_env=0.0,
                attack=0.004,
                decay=0.3,
                sustain=0.8,
                release=0.35,
            )
        ],
        how="seven supersaws 24 cents wide, spread fully, with five more an "
        "octave up, into a dotted echo and a wide hall",
    ),
    _recipe(
        "Stack Pad",
        ["pad", "trance"],
        "C3",
        TRANCE_BPM,
        _patch(
            "Stack Pad",
            osc1="supersaw",
            osc1_level=0.55,
            unison=5,
            unison_detune=16.0,
            unison_spread=1.0,
            osc2="saw",
            osc2_octave=1,
            osc2_level=0.3,
            osc2_detune=7.0,
            filter_type="lowpass",
            cutoff=2400.0,
            resonance=0.1,
            filter_env=600.0,
            filter_attack=0.8,
            filter_decay=1.5,
            filter_sustain=0.7,
            attack=0.5,
            decay=0.8,
            sustain=0.9,
            release=1.6,
        ),
        [
            _fx("chorus", rate=0.3, depth=4.0, mix=0.35),
            _fx("reverb", decay=4.5, mix=0.4, size=1.0),
            _fx("width", amount=1.5),
        ],
        layers=[
            _lane(
                "sub",
                -1,
                0.15,
                [],
                osc1="triangle",
                osc1_level=0.8,
                filter_type="lowpass",
                cutoff=500.0,
                resonance=0.05,
                filter_env=0.0,
                attack=0.5,
                decay=0.8,
                sustain=0.9,
                release=1.4,
            )
        ],
        how="supersaws with a saw an octave up and a triangle an octave "
        "down — the stack — swelling into a 4.5 s hall",
    ),
    _recipe(
        "Gated Pad",
        ["pad", "trance"],
        "C3",
        TRANCE_BPM,
        _patch(
            "Gated Pad",
            osc1="saw",
            osc1_level=0.65,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.5,
            osc2_detune=10.0,
            unison=3,
            unison_detune=14.0,
            unison_spread=0.9,
            filter_type="lowpass",
            cutoff=3800.0,
            resonance=0.1,
            filter_env=0.0,
            attack=0.08,
            decay=0.5,
            sustain=0.95,
            release=0.6,
        ),
        [
            # the gate's depth takes a song lane: 0 -> 1 through a build
            _gate("x.xxx.x.x.xxx.x.", depth=1.0),
            *chain_preset("dotted_ping_pong"),
            _fx("reverb", decay=3.2, mix=0.3, size=0.9),
        ],
        how="a held saw pad chopped by the trance gate, the gaps filled by "
        "a dotted echo and a room",
    ),
    _recipe(
        "Dotted Pluck",
        ["lead", "trance"],
        "C4",
        TRANCE_BPM,
        _patch(
            "Dotted Pluck",
            osc1="saw",
            osc1_level=0.7,
            osc2="square",
            osc2_octave=1,
            osc2_level=0.3,
            osc2_detune=6.0,
            unison=2,
            unison_detune=10.0,
            unison_spread=0.7,
            filter_type="lowpass",
            cutoff=1800.0,
            resonance=0.3,
            filter_env=3500.0,
            filter_decay=0.1,
            filter_sustain=0.0,
            key_track=0.5,
            attack=0.002,
            decay=0.14,
            sustain=0.0,
            release=0.12,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            *chain_preset("dotted_ping_pong"),
            _fx("reverb", decay=2.5, mix=0.22, size=0.8),
        ],
        how="a short saw pluck with a square an octave up, into a "
        "dotted-eighth ping-pong echo",
    ),
    # ------------------------------------------- Just Ice (French electro)
    _recipe(
        "Crushed Saw Bass",
        ["bass", "electro"],
        "A1",
        ELECTRO_BPM,
        _patch(
            "Crushed Saw Bass",
            osc1="saw",
            osc1_level=0.45,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=7.0,
            sub_level=0.3,
            filter_type="ladder",
            cutoff=480.0,
            resonance=0.55,
            filter_env=1600.0,
            filter_decay=0.12,
            filter_sustain=0.15,
            key_track=0.5,
            attack=0.002,
            decay=0.2,
            sustain=0.6,
            release=0.07,
            drive=3.0,
        ),
        [
            _fx("drive", amount=4.0, mix=0.7, mode=1.0),  # hard
            _fx("bitcrush", bits=10.0, downsample=1.0),
            # the clipper squashes it and holds the peak (a compressor is
            # whole-buffer: the live keys would fall back to the dry WAV)
            _fx("clip", drive=4.0, ceiling=-7.0, soft=0.3, mix=1.0),
            # after it: a clipped, lopsided saw + square keeps an offset —
            # and the high-pass overshoots a clipped wave: a soft trim last
            _fx("highpass", cutoff=30.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
            _fx("pump", depth=0.75, release=0.25, beats=1.0),
        ],
        how="a saw and a square over a sub, a resonant ladder snapping "
        "shut, driven into the red and crushed a little, then squashed",
    ),
    _recipe(
        "Gated Disco Stab",
        ["keys", "electro"],
        "C4",
        ELECTRO_BPM,
        _patch(
            "Gated Disco Stab",
            osc1="square",
            osc1_level=0.7,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.6,
            osc2_detune=8.0,
            unison=2,
            unison_detune=8.0,
            unison_spread=0.5,
            filter_type="ladder",
            cutoff=1300.0,
            resonance=0.3,
            filter_env=2600.0,
            filter_decay=0.12,
            filter_sustain=0.05,
            key_track=0.4,
            attack=0.002,
            decay=0.18,
            sustain=0.0,
            release=0.1,
            drive=1.8,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("drive", amount=2.5, mix=0.6),
            _fx("clip", drive=3.0, ceiling=-1.0, soft=0.3, mix=1.0),
            _fx("pump", depth=0.7, release=0.25, beats=1.0),
        ],
        how="a square/saw disco chord stab, short and bright, driven and "
        "pumped on the beat",
    ),
    _recipe(
        "Cathedral Choir Pad",
        ["pad", "electro"],
        "C3",
        ELECTRO_BPM,
        _patch(
            "Cathedral Choir Pad",
            osc1="saw",
            osc1_level=0.5,
            osc2="square",
            osc2_octave=1,
            osc2_level=0.25,
            unison=3,
            unison_detune=12.0,
            unison_spread=0.8,
            filter_type="lowpass",
            cutoff=2400.0,
            resonance=0.1,
            filter_env=0.0,
            formant_amount=0.8,
            formant_vowel=0.7,
            attack=0.5,
            decay=0.6,
            sustain=0.9,
            release=1.2,
        ),
        [
            _fx("highpass", cutoff=140.0, q=0.7),
            _fx("chorus", rate=0.3, depth=4.0, mix=0.4),
            _fx("reverb", decay=4.0, mix=0.35, size=1.0),
        ],
        how="a stacked saw and square sung through the vowel filter on an "
        "open 'o', into a big room: a choir-like pad, not a choir",
    ),
    _recipe(
        "Blown Lead",
        ["lead", "electro"],
        "C4",
        ELECTRO_BPM,
        _patch(
            "Blown Lead",
            osc1="supersaw",
            osc1_level=0.4,
            unison=5,
            unison_detune=14.0,
            unison_spread=0.7,
            filter_type="lowpass",
            cutoff=3600.0,
            resonance=0.2,
            filter_env=1800.0,
            filter_decay=0.2,
            filter_sustain=0.4,
            attack=0.005,
            decay=0.3,
            sustain=0.7,
            release=0.2,
            glide=0.04,
            drive=3.0,
        ),
        [
            _fx("drive", amount=5.0, mix=0.8, mode=3.0),  # tube
            _fx("bitcrush", bits=12.0, downsample=1.0),
            _fx("delay", time=0.375, feedback=0.3, mix=0.2),
            _fx("clip", drive=3.0, ceiling=-7.0, soft=0.3, mix=1.0),
            # after it: tube drive is lopsided — and the trim takes the
            # high-pass's overshoot of a clipped wave
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="a wide supersaw driven until it breaks up, a little crushed, "
        "an echo behind it",
    ),
    _recipe(
        "Slap Pluck Bass",
        ["bass", "electro"],
        "A1",
        ELECTRO_BPM,
        _patch(
            "Slap Pluck Bass",
            osc1="pluck",
            osc1_level=0.6,
            pluck_damping=0.55,
            pluck_bright=0.7,
            sub_level=0.3,
            filter_type="ladder",
            cutoff=1400.0,
            resonance=0.25,
            filter_env=900.0,
            filter_decay=0.1,
            filter_sustain=0.3,
            key_track=0.5,
            attack=0.001,
            decay=0.3,
            sustain=0.5,
            release=0.08,
            drive=1.6,
        ),
        [
            _fx("highpass", cutoff=30.0, q=0.7),
            _fx("clip", drive=6.0, ceiling=-1.0, soft=0.5, mix=1.0),
            _fx("pump", depth=0.6, release=0.2, beats=1.0),
        ],
        how="a plucked string over a sub, bright on the attack and "
        "squashed: a disco bass line's snap, not a bass guitar",
    ),
    _recipe(
        "Siren Riser",
        ["fx", "electro"],
        "C4",
        ELECTRO_BPM,
        _patch(
            "Siren Riser",
            osc1="saw",
            osc1_level=0.8,
            osc2="saw",
            osc2_detune=12.0,
            osc2_level=0.7,
            filter_type="bandpass",
            cutoff=900.0,
            resonance=0.5,
            filter_env=0.0,
            attack=0.05,
            decay=0.5,
            sustain=0.9,
            release=0.4,
            bend_range=12.0,
            drive=2.0,
        ),
        [
            _fx("highpass", cutoff=250.0, q=0.7),
            _fx("phaser", rate=0.6, depth=0.8, mix=0.5),
            _fx("reverb", decay=2.5, mix=0.3, size=0.9),
        ],
        curves={
            "pitch_bend": [[0.0, 0.5], [1.0, 1.0]],
            "cutoff": [[0.0, 0.2], [1.0, 0.85]],
        },
        how="two detuned saws through a band-pass, bent up an octave and "
        "opened across the phrase, phased: a siren for the build",
    ),
    # --------------------------------------- Nine Inch Wails (industrial)
    _recipe(
        "Grinding Bass",
        ["bass", "industrial"],
        "E1",
        INDUSTRIAL_BPM,
        _patch(
            "Grinding Bass",
            osc1="square",
            osc1_level=0.45,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.35,
            osc2_detune=-9.0,
            sub_level=0.3,
            filter_type="ladder",
            cutoff=320.0,
            resonance=0.6,
            filter_env=900.0,
            filter_decay=0.16,
            filter_sustain=0.25,
            key_track=0.5,
            attack=0.003,
            decay=0.25,
            sustain=0.7,
            release=0.1,
            drive=3.5,
        ),
        [
            _fx("fold", amount=1.6, mix=0.3),
            _fx("bitcrush", bits=9.0, downsample=2.0),
            _fx("drive", amount=3.0, mix=0.5, mode=1.0),  # hard
            _fx("clip", drive=6.0, ceiling=-7.0, soft=0.2, mix=1.0),
            _fx("highpass", cutoff=30.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="a square and a detuned saw over a sub, a resonant ladder, "
        "folded and crushed into a grind, then squashed flat",
    ),
    _recipe(
        "Struck Metal",
        ["fx", "industrial"],
        "C4",
        INDUSTRIAL_BPM,
        _patch(
            "Struck Metal",
            osc1="fm",
            osc1_level=0.6,
            fm_ratio=3.41,
            fm_index=6.0,
            fm_decay=0.3,
            filter_type="none",
            attack=0.001,
            decay=0.5,
            sustain=0.0,
            release=0.4,
        ),
        [
            _fx("highpass", cutoff=300.0, q=0.7),
            _fx("comb", tune=310.0, feedback=0.6, mix=0.35),
            _fx("reverb", decay=2.5, mix=0.3, size=0.8),
        ],
        how="an inharmonic FM strike (ratio 3.41) that rings through a "
        "comb into a hard room: struck metal, a sheet or a pipe",
    ),
    _recipe(
        "Static Drone",
        ["pad", "industrial"],
        "C3",
        INDUSTRIAL_BPM,
        _patch(
            "Static Drone",
            osc1="saw",
            osc1_level=0.45,
            osc2="square",
            osc2_octave=-1,
            osc2_level=0.2,
            noise_level=0.25,
            noise_color="brown",
            unison=3,
            unison_detune=18.0,
            unison_spread=0.6,
            filter_type="lowpass",
            cutoff=1100.0,
            resonance=0.25,
            filter_env=0.0,
            filter_lfo_rate=0.08,
            filter_lfo_depth=500.0,
            attack=1.2,
            decay=0.8,
            sustain=0.95,
            release=2.0,
        ),
        [
            _fx("highpass", cutoff=140.0, q=0.7),
            _fx("bitcrush", bits=11.0, downsample=1.0),
            _fx("reverb", decay=5.0, mix=0.4, size=1.0),
        ],
        how="detuned saws over brown noise, a low-pass breathing very "
        "slowly, a little crushed, into a long dark room",
    ),
    _recipe(
        "Ghost Keys",
        ["keys", "industrial"],
        "C4",
        INDUSTRIAL_BPM,
        _patch(
            "Ghost Keys",
            osc1="fm",
            osc1_level=0.6,
            fm_ratio=1.0,
            fm_index=1.4,
            fm_decay=0.25,
            filter_type="lowpass",
            cutoff=2200.0,
            resonance=0.1,
            filter_env=0.0,
            attack=0.008,
            decay=1.4,
            sustain=0.0,
            release=0.9,
        ),
        [
            *chain_preset("tape_wear"),
            _fx("reverb", decay=5.0, mix=0.45, size=1.0),
        ],
        how="soft 2-op FM keys (not a piano), shut at 2.2 kHz, worn like "
        "old tape and left ringing in a long room: the quiet score",
    ),
    _recipe(
        "Fuzz Lead",
        ["lead", "industrial"],
        "C4",
        INDUSTRIAL_BPM,
        _patch(
            "Fuzz Lead",
            osc1="saw",
            osc1_level=0.4,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.28,
            osc2_detune=11.0,
            filter_type="lowpass",
            cutoff=2600.0,
            resonance=0.3,
            filter_env=1200.0,
            filter_decay=0.25,
            filter_sustain=0.5,
            attack=0.005,
            decay=0.3,
            sustain=0.75,
            release=0.15,
            drive=4.0,
        ),
        [
            _fx("drive", amount=5.0, mix=0.9, mode=2.0),  # fuzz
            _fx("delay", time=0.286, feedback=0.25, mix=0.18),
            _fx("clip", drive=3.0, ceiling=-7.0, soft=0.3, mix=1.0),
            # after it: fuzz is near half-rectified, an offset to take out
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="a saw and a detuned square driven all the way, a short echo: "
        "a lead that snarls",
    ),
    _recipe(
        "Machine Pulse",
        ["keys", "industrial"],
        "C4",
        INDUSTRIAL_BPM,
        _patch(
            "Machine Pulse",
            osc1="square",
            osc1_level=1.0,
            filter_type="bandpass",
            cutoff=1500.0,
            resonance=0.45,
            filter_env=1200.0,
            filter_decay=0.06,
            filter_sustain=0.0,
            key_track=0.6,
            attack=0.001,
            decay=0.08,
            sustain=0.0,
            release=0.06,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("bitcrush", bits=7.0, downsample=3.0),
            _fx("delay", time=0.286, feedback=0.45, mix=0.3, ping_pong=1.0),
        ],
        how="a short band-passed square blip, crushed to seven bits and "
        "echoed side to side: a machine ticking under the song",
    ),
    # ---------------------------------- Psych Haze (psychedelic pop / neo-psych)
    _recipe(
        "Chorus Fuzz Bass",
        ["bass", "psych"],
        "A1",
        PSYCH_BPM,
        _patch(
            "Chorus Fuzz Bass",
            osc1="saw",
            osc1_level=0.5,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.25,
            osc2_detune=0.0,
            sub_level=0.35,
            filter_type="ladder",
            cutoff=560.0,
            resonance=0.3,
            filter_env=900.0,
            filter_decay=0.22,
            filter_sustain=0.35,
            key_track=0.5,
            attack=0.003,
            decay=0.3,
            sustain=0.75,
            release=0.1,
            drive=1.6,
        ),
        [
            _fx("drive", amount=2.5, mix=0.6, mode=3.0),  # tube: tape-warm
            # tube drive is lopsided: the offset out before the modulation
            _fx("highpass", cutoff=30.0, q=0.7),
            _fx("chorus", rate=0.5, depth=3.0, mix=0.35),
            _fx("phaser", rate=0.25, depth=0.6, mix=0.35),
        ],
        how="a saw and a square over a sub, warmed like an overdriven tape, "
        "then chorused and slowly phased: a bass that swims",
    ),
    _recipe(
        "Phased Fuzz Lead",
        ["lead", "psych"],
        "C4",
        PSYCH_BPM,
        _patch(
            "Phased Fuzz Lead",
            osc1="square",
            osc1_level=0.5,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=0.0,
            filter_type="lowpass",
            cutoff=2400.0,
            resonance=0.25,
            filter_env=1500.0,
            filter_decay=0.25,
            filter_sustain=0.5,
            attack=0.01,
            decay=0.3,
            sustain=0.8,
            release=0.25,
            glide=0.06,
            vibrato_rate=5.5,
            vibrato_depth=0.12,
            drive=2.0,
        ),
        [
            _fx("drive", amount=3.0, mix=0.6, mode=2.0),  # fuzz
            _fx("clip", drive=3.0, ceiling=-7.0, soft=0.3, mix=1.0),
            # fuzz is near half-rectified: the offset out, then the sweep
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("flanger", rate=0.15, depth=2.5, feedback=0.5, mix=0.4),
            _fx("tape_echo", time=0.3, feedback=0.4, wow=0.4, mix=0.3, sync=3.0),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="a square and a saw gliding into each other, fuzzed, flanged "
        "slowly and thrown into a wavering tape echo on the dotted eighth",
    ),
    _recipe(
        "Haze Pad",
        ["pad", "psych"],
        "C3",
        PSYCH_BPM,
        _patch(
            "Haze Pad",
            osc1="saw",
            osc1_level=0.7,
            osc2="triangle",
            osc2_octave=1,
            osc2_level=0.3,
            osc2_detune=0.0,
            unison=3,
            unison_detune=10.0,
            unison_spread=0.8,
            filter_type="lowpass",
            cutoff=1800.0,
            resonance=0.15,
            filter_env=0.0,
            filter_lfo_rate=0.15,
            filter_lfo_depth=600.0,
            attack=0.8,
            decay=0.6,
            sustain=0.9,
            release=1.6,
        ),
        [
            _fx("highpass", cutoff=140.0, q=0.7),
            _fx("phaser", rate=0.12, depth=0.8, mix=0.5),
            _fx("chorus", rate=0.25, depth=4.0, mix=0.4),
            _fx("tape_echo", time=0.3, feedback=0.5, wow=0.5, mix=0.25, sync=3.0),
            _fx("reverb", decay=5.0, mix=0.45, size=1.0),
            # the room turning slowly from side to side
            _fx("tremolo", sync=1.0, depth=0.35, shape=0.0, phase=180.0),
        ],
        how="three detuned saws breathing under a slow low-pass, phased, "
        "chorused, echoed off a wavering tape, drowned in a long room and "
        "panned slowly across it",
    ),
    _recipe(
        "Wobble Keys",
        ["keys", "psych"],
        "C4",
        PSYCH_BPM,
        _patch(
            "Wobble Keys",
            osc1="fm",
            osc1_level=0.6,
            fm_ratio=1.0,
            fm_index=2.0,
            fm_decay=0.4,
            filter_type="lowpass",
            cutoff=3000.0,
            resonance=0.1,
            filter_env=0.0,
            attack=0.005,
            decay=1.0,
            sustain=0.25,
            release=0.5,
            tremolo_rate=4.0,
            tremolo_depth=0.25,
        ),
        [
            _fx("chorus", rate=0.8, depth=3.0, mix=0.35),
            _fx(
                "tape_echo",
                time=0.2,
                feedback=0.3,
                wow=0.6,
                drive=0.5,
                mix=0.25,
                sync=2.0,
            ),
            _fx("drive", amount=1.5, mix=0.4, mode=3.0),
            _fx("highpass", cutoff=120.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="soft 2-op FM keys (not an electric piano) with a tremolo, "
        "chorused, through a saturated tape echo on the eighth",
    ),
    _recipe(
        "Phase Arp",
        ["keys", "psych"],
        "C4",
        PSYCH_BPM,
        _patch(
            "Phase Arp",
            osc1="square",
            osc1_level=0.8,
            filter_type="ladder",
            cutoff=900.0,
            resonance=0.45,
            filter_env=2400.0,
            filter_decay=0.12,
            filter_sustain=0.0,
            key_track=0.5,
            attack=0.001,
            decay=0.18,
            sustain=0.0,
            release=0.12,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("phaser", rate=0.3, depth=0.7, mix=0.5),
            _fx("delay", time=0.375, feedback=0.45, mix=0.3, ping_pong=1.0, sync=3.0),
            _fx("reverb", decay=3.0, mix=0.3, size=0.9),
        ],
        how="a snappy square through a resonant ladder, phased, its echo "
        "bouncing side to side on the dotted eighth",
    ),
    _recipe(
        "Tape Swell",
        ["fx", "psych"],
        "C4",
        PSYCH_BPM,
        _patch(
            "Tape Swell",
            osc1="saw",
            osc1_level=0.5,
            osc2="saw",
            osc2_octave=1,
            osc2_level=0.3,
            osc2_detune=12.0,
            noise_level=0.15,
            filter_type="lowpass",
            cutoff=900.0,
            resonance=0.3,
            filter_env=0.0,
            attack=1.2,
            decay=0.5,
            sustain=0.9,
            release=0.3,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            # the tape it swells on: wavering, a little hiss
            _fx("tape", drive=4.0, bump=0.0, tone=0.4, wow=0.6, flutter=0.3, hiss=0.2),
            _fx("flanger", rate=0.3, depth=3.0, feedback=0.6, mix=0.5),
            _fx("reverb", decay=4.0, mix=0.4, size=1.0),
        ],
        curves={"cutoff": [[0.0, 0.15], [1.0, 0.85]]},
        how="saws and a breath of noise swelling up under a low-pass that "
        "opens across the phrase, on a wavering tape, flanged: a wash into the "
        "next section",
    ),
    # --------------------------------------------- Sixties Studio (60s studio)
    _recipe(
        "Rotary Organ",
        ["keys", "sixties"],
        "C4",
        SIXTIES_BPM,
        _patch(
            "Rotary Organ",
            osc1="sine",
            osc1_level=0.5,
            osc2="sine",
            osc2_octave=1,
            osc2_level=0.35,
            osc2_detune=0.0,
            sub_level=0.25,
            filter_type="lowpass",
            cutoff=5000.0,
            resonance=0.05,
            filter_env=0.0,
            attack=0.005,
            decay=0.1,
            sustain=1.0,
            release=0.08,
            drive=1.3,
        ),
        [
            _fx("rotary", speed=0.0, drive=1.2, mix=1.0),
            _fx("plate", decay=1.6, tone=0.6, predelay=0.012, mix=0.2),
        ],
        how="drawbar-like sines, the note, its octave and the one under, "
        "through a rotating speaker turning slowly, into a short plate",
    ),
    _recipe(
        "Tape Strings",
        ["pad", "sixties"],
        "C3",
        SIXTIES_BPM,
        _patch(
            "Tape Strings",
            osc1="saw",
            osc1_level=0.45,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=6.0,
            filter_type="lowpass",
            cutoff=2200.0,
            resonance=0.1,
            filter_env=0.0,
            attack=0.12,
            decay=0.4,
            sustain=0.9,
            release=0.4,
            vibrato_rate=0.7,
            vibrato_depth=0.06,
            vibrato_delay=0.0,
        ),
        [
            # the replay tape itself (rather than a low-pass rolling the
            # top off): its head, its wow and flutter, a little hiss
            _fx("tape", drive=4.0, bump=1.0, tone=0.0, wow=0.3, flutter=0.4, hiss=0.15),
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("chorus", rate=0.4, depth=2.0, mix=0.25),
            _fx("plate", decay=2.2, tone=0.5, predelay=0.02, mix=0.3),
        ],
        how="strings as a tape-replay keyboard played them: two saws, the "
        "top rolled off, the pitch wavering with the tape, into a plate",
    ),
    _recipe(
        "Tape Flute",
        ["lead", "sixties"],
        "C4",
        SIXTIES_BPM,
        _patch(
            "Tape Flute",
            osc1="sine",
            osc1_level=0.6,
            osc2="triangle",
            osc2_octave=1,
            osc2_level=0.15,
            osc2_detune=0.0,
            noise_level=0.06,
            filter_type="lowpass",
            cutoff=3000.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.5,
            attack=0.08,
            decay=0.2,
            sustain=0.85,
            release=0.25,
            vibrato_rate=5.0,
            vibrato_depth=0.1,
            vibrato_delay=0.2,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("tape", drive=3.0, bump=1.0, tone=0.2, wow=0.3, flutter=0.4, hiss=0.15),
            _fx("tape_echo", time=0.11, feedback=0.12, wow=0.15, tone=0.4, mix=0.25),
            _fx("plate", decay=2.0, tone=0.6, predelay=0.015, mix=0.3),
        ],
        how="a breathy flute as a tape-replay keyboard played it: a sine "
        "and its octave, a little air, a slow vibrato, slapback and plate",
    ),
    _recipe(
        "Round Bass",
        ["bass", "sixties"],
        "A1",
        SIXTIES_BPM,
        _patch(
            "Round Bass",
            osc1="saw",
            osc1_level=0.55,
            osc2="triangle",
            osc2_octave=0,
            osc2_level=0.35,
            osc2_detune=0.0,
            filter_type="ladder",
            cutoff=800.0,
            resonance=0.1,
            filter_env=400.0,
            filter_decay=0.2,
            filter_sustain=0.2,
            key_track=0.5,
            attack=0.004,
            decay=0.5,
            sustain=0.4,
            release=0.1,
        ),
        [
            _fx("drive", amount=1.4, mix=0.4, mode=3.0),  # a warm valve amp
            _fx("highpass", cutoff=30.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="a round, short bass: a triangle and a saw under a dark ladder "
        "that closes as the note dies, a little valve warmth, nothing else",
    ),
    _recipe(
        "Double Track Lead",
        ["lead", "sixties"],
        "C4",
        SIXTIES_BPM,
        _patch(
            "Double Track Lead",
            osc1="square",
            osc1_level=0.45,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=0.0,
            filter_type="lowpass",
            cutoff=3000.0,
            resonance=0.2,
            filter_env=1000.0,
            filter_decay=0.2,
            filter_sustain=0.6,
            attack=0.005,
            decay=0.25,
            sustain=0.8,
            release=0.2,
            glide=0.03,
            drive=1.6,
        ),
        [
            _fx("drive", amount=2.0, mix=0.5, mode=3.0),
            _fx("doubler", time=0.028, wobble=0.6, spread=0.75, mix=1.0),
            _fx("tape_echo", time=0.11, feedback=0.12, wow=0.15, tone=0.4, mix=0.3),
            _fx("plate", decay=1.8, tone=0.6, predelay=0.012, mix=0.2),
            # last: a valve and a tape's saturation leave an offset on a
            # lopsided square + saw
            _fx("highpass", cutoff=150.0, q=0.7),
        ],
        how="a square and a saw through a warm valve, double-tracked by a "
        "wavering copy a few ms late, a slapback and a short plate",
    ),
    _recipe(
        "Backwards Cymbal",
        ["fx", "sixties"],
        "C4",
        SIXTIES_BPM,
        _patch(
            "Backwards Cymbal",
            osc1="noise",
            osc1_level=0.8,
            filter_type="highpass",
            cutoff=4000.0,
            resonance=0.2,
            key_track=0.0,
            filter_env=0.0,
            attack=1.2,
            decay=0.1,
            sustain=1.0,
            release=0.03,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=2000.0, q=0.7),
            _fx("plate", decay=1.5, tone=0.7, predelay=0.0, mix=0.2),
        ],
        curves={"cutoff": [[0.0, 0.3], [1.0, 0.75]]},
        how="a cymbal played backwards, as tape turned over gives it: "
        "high noise swelling up and stopping dead on the beat",
    ),
    # ======================================================================
    # The gaps the five artists' styles showed —
    # appended; styles, never songs, every name generic
    # ----------------------------------------------- melodic techno (Anymore)
    _recipe(
        "Reese Bass",
        ["bass", "techno"],
        "A1",
        TECHNO_BPM,
        _patch(
            "Reese Bass",
            osc1="saw",
            osc1_level=0.18,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.18,
            # two saws a fifth of a semitone apart: they phase in and out
            # of each other, slowly, which IS the sound
            osc2_detune=18.0,
            sub_level=0.1,
            filter_type="ladder",
            cutoff=520.0,
            resonance=0.25,
            filter_env=400.0,
            filter_decay=0.3,
            filter_sustain=0.4,
            # the low-pass that moves: a slow sine on the cutoff
            filter_lfo_rate=0.15,
            filter_lfo_depth=380.0,
            key_track=0.3,
            attack=0.005,
            decay=0.3,
            sustain=0.9,
            release=0.15,
            drive=1.4,
        ),
        [
            # driven detuned saws over a sub are lopsided: the offset out
            _fx("highpass", cutoff=30.0, q=0.7),
            _fx("pump", depth=0.6, release=0.25, beats=1.0),
        ],
        how="two saws a few cents apart phasing slowly against each other "
        "over a sub, under a driven low-pass that a slow sine moves, "
        "ducked on the beat",
    ),
    _recipe(
        "Dark Arp Pluck",
        ["lead", "techno"],
        "C4",
        TECHNO_BPM,
        _patch(
            "Dark Arp Pluck",
            osc1="fm",
            fm_ratio=2.0,
            fm_index=3.0,
            fm_decay=0.08,
            osc1_level=0.7,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=5.0,
            filter_type="lowpass",
            cutoff=900.0,
            resonance=0.35,
            filter_env=2200.0,
            filter_decay=0.12,
            filter_sustain=0.0,
            key_track=0.5,
            attack=0.001,
            decay=0.18,
            sustain=0.0,
            release=0.15,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            # the dotted eighth that turns a plain arp into a hypnotic one
            _fx("delay", feedback=0.5, mix=0.32, ping_pong=1.0, sync=3.0),
            _fx("reverb", decay=3.5, mix=0.3, size=0.9),
        ],
        how="a short, dark FM ping (ratio 2, the index gone in 80 ms) over a "
        "saw, shut under a low-pass, into a dotted-eighth echo side to side "
        "and a room: an arp that hypnotises by repeating",
    ),
    _recipe(
        "Cinematic Choir Lead",
        ["lead", "techno"],
        "C4",
        TECHNO_BPM,
        _patch(
            "Cinematic Choir Lead",
            osc1="saw",
            osc1_level=0.5,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.4,
            osc2_detune=8.0,
            unison=3,
            unison_detune=10.0,
            unison_spread=0.8,
            formant_amount=0.8,
            formant_vowel=0.3,
            formant_size=1.1,
            filter_type="lowpass",
            cutoff=4200.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.3,
            attack=0.08,
            decay=0.4,
            sustain=0.9,
            release=0.7,
            vibrato_rate=5.2,
            vibrato_depth=0.15,
            vibrato_delay=0.35,
            glide=0.08,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("chorus", rate=0.3, depth=3.0, mix=0.3),
            _fx("delay", feedback=0.4, mix=0.22, ping_pong=1.0, sync=3.0),
            _fx("reverb", decay=5.5, mix=0.42, size=1.0),
        ],
        # the vowel opens across the phrase, "a" toward "o": the line sings
        # where the gated choir pad only chops
        curves={"formant_vowel": [[0.0, 0.2], [1.0, 0.65]]},
        how="saws through the vowel filter, a vowel that moves across the "
        "phrase, a late vibrato and a glide, into a long hall: a sung line "
        "for the breakdown — choir-like, not a choir",
    ),
    # ----------------------------------------------------- trance (Arm In Arm)
    _recipe(
        "Trance Pluck Lead",
        ["lead", "trance"],
        "C4",
        TRANCE_BPM,
        _patch(
            "Trance Pluck Lead",
            osc1="supersaw",
            osc1_level=0.55,
            unison=5,
            unison_detune=18.0,
            unison_spread=0.9,
            osc2="square",
            osc2_octave=1,
            osc2_level=0.22,
            osc2_detune=4.0,
            filter_type="lowpass",
            cutoff=2400.0,
            resonance=0.2,
            filter_env=5500.0,
            filter_decay=0.18,
            filter_sustain=0.12,
            key_track=0.5,
            attack=0.002,
            decay=0.35,
            sustain=0.25,
            release=0.25,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            *chain_preset("dotted_ping_pong"),
            _fx("reverb", decay=3.0, mix=0.28, size=0.9),
            _fx("width", amount=1.4),
        ],
        how="supersaws and a square an octave up behind a low-pass that "
        "snaps open on every note and falls back to a quarter: the bright "
        "pluck-lead layered under a big supersaw, into a dotted echo",
    ),
    _recipe(
        "Big Room Pad",
        ["pad", "trance"],
        "C3",
        TRANCE_BPM,
        _patch(
            "Big Room Pad",
            osc1="supersaw",
            osc1_level=0.5,
            unison=7,
            unison_detune=20.0,
            unison_spread=1.0,
            osc2="saw",
            osc2_octave=1,
            osc2_level=0.25,
            osc2_detune=9.0,
            filter_type="lowpass",
            cutoff=3000.0,
            resonance=0.1,
            filter_env=900.0,
            filter_attack=1.0,
            filter_decay=2.0,
            filter_sustain=0.7,
            attack=0.6,
            decay=1.0,
            sustain=0.9,
            release=2.0,
        ),
        [*chain_preset("big_room_hall")],
        how="seven supersaws wide open with a saw an octave over them, "
        "swelling into the Big Room Hall chain: a six-second room with the "
        "lows kept out of it, the top lifted, wider than wide",
    ),
    # --------------------------------------- filter house / disco (Daft Rock)
    _recipe(
        "String Machine",
        ["pad", "house"],
        "C3",
        HOUSE_BPM,
        _patch(
            "String Machine",
            osc1="saw",
            osc1_level=0.8,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.5,
            osc2_detune=4.0,
            pwm_rate=0.5,
            pwm_depth=0.3,
            filter_type="lowpass",
            cutoff=3600.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.3,
            attack=0.25,
            decay=0.5,
            sustain=0.9,
            release=0.9,
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            # the ensemble: a slow, deep chorus and a fast, shallow one,
            # three voices each — the string machine's own motion
            _fx("chorus", rate=0.6, depth=4.0, mix=0.5),
            _fx("chorus", rate=2.0, depth=1.2, mix=0.35),
            _fx("reverb", decay=2.5, mix=0.25, size=0.8),
        ],
        how="a saw and a slowly pulsing square through a gentle low-pass "
        "and the ensemble — a slow deep chorus over a fast shallow one: "
        "a string machine's strings, a disco record's",
    ),
    _recipe(
        "Envelope Funk Bass",
        ["bass", "house"],
        "A1",
        HOUSE_BPM,
        _patch(
            "Envelope Funk Bass",
            osc1="saw",
            osc1_level=0.4,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.25,
            osc2_detune=3.0,
            sub_level=0.2,
            filter_type="none",
            key_track=0.0,
            filter_env=0.0,
            attack=0.003,
            decay=0.2,
            sustain=0.6,
            release=0.08,
            glide=0.04,
            drive=1.4,
        ),
        [
            _fx("highpass", cutoff=25.0, q=0.7),
            # the envelope filter: the auto-filter on its follower alone,
            # opening as each note hits and closing as it dies
            _fx(
                "autofilter",
                mode=0.0,
                cutoff=180.0,
                q=4.0,
                depth=4.0,
                env=1.0,
                sens=12.0,
                attack=0.005,
                release=0.15,
                mix=1.0,
            ),
            _fx("pump", depth=0.85, release=0.3, beats=1.0),
        ],
        how="a saw and a square over a sub, gliding, through an envelope "
        "filter: a resonant low-pass that opens with how hard each note "
        "hits and shuts as it dies — the disco funk bass's quack — ducked "
        "hard on the beat",
    ),
    # ------------------------------------------- emotive garage (Fred Before)
    _recipe(
        "Felt Piano Keys",
        ["keys", "garage"],
        "C4",
        GARAGE_BPM,
        _patch(
            "Felt Piano Keys",
            osc1="pluck",
            osc1_level=0.75,
            pluck_damping=0.6,
            pluck_bright=0.35,
            osc2="sine",
            osc2_octave=0,
            osc2_level=0.25,
            osc2_detune=0.0,
            # two strings a few cents apart, as a piano's unisons beat
            unison=2,
            unison_detune=3.0,
            unison_spread=0.3,
            filter_type="lowpass",
            cutoff=2200.0,
            resonance=0.05,
            filter_env=900.0,
            filter_decay=0.45,
            filter_sustain=0.0,
            key_track=0.6,
            attack=0.004,
            decay=2.2,
            sustain=0.0,
            release=0.35,
            env_curve=3.0,
        ),
        [
            # a plucked string leaves a thump under its note
            _fx("highpass", cutoff=60.0, q=0.7),
            _fx("tape", drive=3.0, bump=1.5, tone=0.45, wow=0.1, flutter=0.1, hiss=0.1),
            _fx("reverb", decay=1.6, mix=0.25, size=0.5),
        ],
        how="the nearest this synth gets to a felt piano with no recording: "
        "a softly struck, damped string (two copies a few cents apart) over "
        "a sine for the body, dying away under a closing low-pass, on warm "
        "tape in a small room. Not a sampled piano — no hammer noise, no "
        "sympathetic strings, no pedal; one string model a key",
    ),
    _recipe(
        "Squashed Vocal Pad",
        ["pad", "garage"],
        "C3",
        GARAGE_BPM,
        _patch(
            "Squashed Vocal Pad",
            osc1="saw",
            osc1_level=0.55,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.45,
            osc2_detune=7.0,
            unison=3,
            unison_detune=12.0,
            unison_spread=0.8,
            formant_amount=0.85,
            formant_vowel=0.1,
            formant_size=0.9,
            filter_type="lowpass",
            cutoff=3200.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.3,
            attack=0.3,
            decay=0.6,
            sustain=0.9,
            release=1.0,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            # the OTT Squash chain's unit, its output turned up to meet the
            # level it takes off
            _fx("ott", depth=0.6, output=4.0),
            _fx("chorus", rate=0.4, depth=3.0, mix=0.35),
            _fx("reverb", decay=3.5, mix=0.35, size=0.9),
        ],
        # the vowel drifts "a" toward "e" across the phrase
        curves={"formant_vowel": [[0.0, 0.05], [1.0, 0.35]]},
        how="saws through the vowel filter — a voice-like 'ah' drifting "
        "toward 'eh' — squashed over the top (its breaths and tails lifted "
        "to meet it), chorused into a room: the pad a chopped vocal "
        "becomes",
    ),
    # ------------------------------------------- drop FX (every style's)
    _recipe(
        "Impact",
        ["fx", "drops"],
        "C2",
        DROPS_BPM,
        _patch(
            "Impact",
            osc1="sine",
            osc1_level=0.85,
            # the boom: two octaves above the note, down to it in ~0.1 s
            pitch_env=24.0,
            pitch_env_decay=0.1,
            noise_level=0.25,
            noise_color="pink",
            filter_type="lowpass",
            cutoff=6000.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.0,
            attack=0.001,
            decay=1.4,
            sustain=0.0,
            release=0.8,
            env_curve=3.0,
            drive=1.4,
        ),
        [
            _fx("highpass", cutoff=28.0, q=0.7),
            _fx("reverb", decay=4.5, mix=0.35, size=1.0),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        layers=[
            # the crash over the boom: bright noise, a long ring
            _lane(
                "air",
                0,
                0.35,
                [_fx("width", amount=1.6)],
                osc1="noise",
                osc1_level=0.7,
                noise_color="white",
                key_track=0.0,
                filter_type="highpass",
                cutoff=3000.0,
                resonance=0.1,
                filter_env=0.0,
                attack=0.001,
                decay=1.6,
                sustain=0.0,
                release=1.0,
                env_curve=3.0,
            )
        ],
        how="a sine dropping two octaves onto its note in a tenth of a "
        "second with a breath of noise, a bright crash over it, into a big "
        "room: the hit on a drop's first beat",
    ),
    _recipe(
        "Sub Drop",
        ["fx", "drops"],
        "C2",
        DROPS_BPM,
        _patch(
            "Sub Drop",
            osc1="sine",
            osc1_level=0.9,
            # an octave above the note, down onto it over a second
            pitch_env=12.0,
            pitch_env_decay=1.0,
            bend_range=12.0,
            filter_type="lowpass",
            cutoff=400.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.0,
            attack=0.005,
            decay=3.0,
            sustain=0.0,
            release=1.0,
            env_curve=2.0,
        ),
        [_fx("highpass", cutoff=22.0, q=0.7)],
        # and on down across the phrase, an octave under it by the end
        curves={"pitch_bend": [[0.0, 0.5], [1.0, 0.0]]},
        how="a sine falling an octave onto its note and on down another "
        "across the phrase, mono, nothing over 400 Hz: the boom under a "
        "drop",
    ),
    _recipe(
        "Noise Downlifter",
        ["fx", "drops"],
        "C4",
        DROPS_BPM,
        _patch(
            "Noise Downlifter",
            osc1="noise",
            osc1_level=0.8,
            filter_type="bandpass",
            cutoff=6000.0,
            resonance=0.5,
            key_track=0.0,
            filter_env=0.0,
            attack=0.01,
            decay=2.5,
            sustain=0.35,
            release=1.5,
        ),
        [
            _fx("highpass", cutoff=300.0, q=0.7),
            _fx("width", amount=1.8),
            _fx("reverb", decay=4.0, mix=0.35, size=0.9),
            # band-passed noise is quiet: brought up into the limiter
            _fx("limiter", gain=10.0, ceiling=-3.0, release=0.2),
        ],
        # the mirror of Rising Air: the band-pass falls across the phrase
        curves={"cutoff": [[0.0, 0.9], [1.0, 0.05]]},
        how="band-passed noise whose cutoff curve falls the whole phrase, "
        "dying away into a wide room: the air let out after a drop",
    ),
    _recipe(
        "Reverse Crash",
        ["fx", "drops"],
        "C4",
        DROPS_BPM,
        _patch(
            "Reverse Crash",
            osc1="noise",
            osc1_level=0.8,
            filter_type="highpass",
            cutoff=1500.0,
            resonance=0.4,
            key_track=0.0,
            filter_env=0.0,
            attack=2.5,
            decay=0.1,
            sustain=1.0,
            release=0.02,
            env_curve=4.0,
        ),
        [
            _fx("highpass", cutoff=800.0, q=0.7),
            _fx("plate", decay=1.2, tone=0.8, predelay=0.0, mix=0.2),
            _fx("width", amount=1.6),
        ],
        # the ring rising with it across the phrase
        curves={"cutoff": [[0.0, 0.25], [1.0, 0.95]]},
        how="a crash swelling up the whole phrase — slower, wider and "
        "brighter at the top than the Backwards Cymbal, its ringing "
        "high-pass climbing with it — and stopping dead on the drop",
    ),
    # ======================================================================
    # Five genres the packs above do not cover —
    # appended; styles, never songs, every name generic
    # ------------------------------------------------- hip-hop / trap (~140)
    _recipe(
        "808 Long",
        ["bass", "hiphop"],
        "A1",
        HIPHOP_BPM,
        _patch(
            "808 Long",
            osc1="sine",
            osc1_level=0.9,
            # the 808's thud: a few semitones above the note, onto it fast
            pitch_env=7.0,
            pitch_env_decay=0.06,
            filter_type="lowpass",
            cutoff=600.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.0,
            attack=0.002,
            decay=1.8,
            sustain=0.45,
            release=0.5,
            env_curve=2.5,
            # notes slide into each other: the 808's glide
            glide=0.09,
            drive=1.5,
        ),
        [
            _fx("highpass", cutoff=22.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="a tuned sine falling a fifth onto its note, a soft drive, a long "
        "decay that settles under the key, a glide from note to note: the "
        "long 808 a trap beat is built on",
    ),
    _recipe(
        "808 Distorted",
        ["bass", "hiphop"],
        "A1",
        HIPHOP_BPM,
        _patch(
            "808 Distorted",
            osc1="sine",
            osc1_level=0.85,
            # a square under it for the grit's harmonics
            osc2="square",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=0.0,
            pitch_env=9.0,
            pitch_env_decay=0.05,
            filter_type="lowpass",
            cutoff=1800.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.0,
            attack=0.002,
            decay=1.5,
            sustain=0.5,
            release=0.4,
            env_curve=2.5,
            glide=0.08,
            drive=3.5,
        ),
        [*chain_preset("saturated_808")],
        how="the 808 pushed through a valve into the clipper's ceiling: the "
        "same falling sine and glide, its harmonics brought up so it speaks "
        "on small speakers",
    ),
    _recipe(
        "Dark Bell Keys",
        ["keys", "hiphop"],
        "C4",
        HIPHOP_BPM,
        _patch(
            "Dark Bell Keys",
            osc1="fm",
            osc1_level=0.65,
            # an inharmonic ratio: a bell, not a piano
            fm_ratio=3.5,
            fm_index=2.5,
            fm_decay=0.6,
            osc2="sine",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=0.0,
            filter_type="lowpass",
            cutoff=3500.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.5,
            attack=0.002,
            decay=1.4,
            sustain=0.0,
            release=0.6,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("delay", feedback=0.3, mix=0.2, ping_pong=1.0, sync=2.0),
            _fx("reverb", decay=2.5, mix=0.3, size=0.8),
        ],
        how="2-op FM at an inharmonic ratio over a sine for the body, its "
        "brightness gone in half a second, shut under a low-pass, an eighth "
        "echo and a room: the dark bell a minor trap melody is played on",
    ),
    _recipe(
        "Dark Choir Pad",
        ["pad", "hiphop"],
        "C3",
        HIPHOP_BPM,
        _patch(
            "Dark Choir Pad",
            osc1="saw",
            osc1_level=0.55,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.45,
            osc2_detune=7.0,
            unison=3,
            unison_detune=10.0,
            unison_spread=0.8,
            # a dark "o"
            formant_amount=0.8,
            formant_vowel=0.7,
            formant_size=1.2,
            filter_type="lowpass",
            cutoff=2400.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.3,
            attack=0.5,
            decay=0.8,
            sustain=0.9,
            release=1.5,
            vibrato_rate=4.5,
            vibrato_depth=0.08,
            vibrato_delay=0.4,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("chorus", rate=0.4, depth=3.0, mix=0.35),
            _fx("reverb", decay=4.5, mix=0.4, size=0.95),
        ],
        how="saws through the vowel filter on a dark 'o', a slow swell and a "
        "late vibrato, chorused into a long room: a choir-like pad under a "
        "trap beat — choir-like, not a choir",
    ),
    _recipe(
        "Airy Flute Lead",
        ["lead", "hiphop"],
        "C5",
        HIPHOP_BPM,
        _patch(
            "Airy Flute Lead",
            osc1="triangle",
            osc1_level=0.7,
            osc2="sine",
            osc2_octave=1,
            osc2_level=0.15,
            osc2_detune=0.0,
            # the breath
            noise_level=0.12,
            noise_color="pink",
            filter_type="lowpass",
            cutoff=2800.0,
            resonance=0.05,
            filter_env=600.0,
            filter_decay=0.15,
            filter_sustain=0.4,
            key_track=0.5,
            attack=0.06,
            decay=0.3,
            sustain=0.85,
            release=0.25,
            vibrato_rate=5.0,
            vibrato_depth=0.15,
            vibrato_delay=0.3,
            glide=0.04,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("delay", feedback=0.25, mix=0.18, ping_pong=1.0, sync=2.0),
            _fx("reverb", decay=3.0, mix=0.3, size=0.85),
        ],
        how="a triangle and a soft octave over a breath of pink noise, a "
        "short chiff on the filter, a late vibrato and a glide, an eighth "
        "echo and a room: flute-ish, as near as a synth voice gets — not a "
        "flute",
    ),
    # ------------------------------------------------------ drum & bass (~174)
    _recipe(
        "Neuro Reese",
        ["bass", "dnb"],
        "A1",
        DNB_BPM,
        _patch(
            "Neuro Reese",
            osc1="saw",
            osc1_level=0.2,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.2,
            # the Reese's two saws, further apart: they phase faster
            osc2_detune=22.0,
            sub_level=0.3,
            filter_type="lowpass",
            cutoff=900.0,
            resonance=0.35,
            filter_env=0.0,
            key_track=0.3,
            # a quarter-note sweep through it
            filter_lfo_shape="triangle",
            filter_lfo_sync="1/4",
            filter_lfo_depth=600.0,
            attack=0.005,
            decay=0.3,
            sustain=0.9,
            release=0.12,
            drive=1.6,
            # each note's saws start where they will and wander a little
            drift=0.3,
        ),
        [*chain_preset("neuro_grind")],
        how="the techno Reese taken further: two saws further apart over a "
        "sub, a low-pass swept on the quarter, through the Neuro Grind chain "
        "— a hard drive, a comb's metal, squashed over the top, the "
        "clipper's ceiling",
    ),
    _recipe(
        "Rolling DnB Sub",
        ["bass", "dnb"],
        "A1",
        DNB_BPM,
        _patch(
            "Rolling DnB Sub",
            osc1="sine",
            osc1_level=0.9,
            filter_type="lowpass",
            cutoff=250.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.0,
            attack=0.003,
            decay=0.2,
            sustain=0.85,
            release=0.08,
            glide=0.05,
            drive=1.2,
        ),
        [_fx("highpass", cutoff=22.0, q=0.7)],
        how="a clean sine sub, short at both ends and gliding between notes, "
        "mono, nothing over 250 Hz: the roller under a drum & bass two-step",
    ),
    _recipe(
        "Atmos Pad",
        ["pad", "dnb"],
        "C3",
        DNB_BPM,
        _patch(
            "Atmos Pad",
            osc1="supersaw",
            osc1_level=0.45,
            # seven, not five: at five a held C4 met itself 1 dB over the
            # hold-seam bar (the beat between the voices)
            unison=7,
            unison_detune=14.0,
            unison_spread=1.0,
            osc2="triangle",
            osc2_octave=1,
            osc2_level=0.2,
            osc2_detune=0.0,
            filter_type="lowpass",
            cutoff=2600.0,
            resonance=0.1,
            filter_env=0.0,
            filter_lfo_rate=0.1,
            filter_lfo_depth=500.0,
            key_track=0.3,
            attack=1.2,
            decay=1.0,
            sustain=0.85,
            release=2.5,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("chorus", rate=0.3, depth=3.0, mix=0.35),
            _fx("reverb", decay=5.5, mix=0.5, size=1.0),
            _fx("width", amount=1.5),
        ],
        how="supersaws and a triangle an octave over them swelling slowly "
        "under a low-pass that drifts, into a long wide room: the atmosphere "
        "a liquid roller sits in",
    ),
    _recipe(
        "Liquid Stab",
        ["keys", "dnb"],
        "C4",
        DNB_BPM,
        _patch(
            "Liquid Stab",
            osc1="saw",
            osc1_level=0.5,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=5.0,
            unison=2,
            unison_detune=8.0,
            unison_spread=0.6,
            filter_type="lowpass",
            cutoff=1400.0,
            resonance=0.25,
            filter_env=3000.0,
            filter_decay=0.15,
            filter_sustain=0.1,
            key_track=0.5,
            attack=0.002,
            decay=0.35,
            sustain=0.15,
            release=0.3,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("delay", feedback=0.35, mix=0.22, ping_pong=1.0, sync=3.0),
            _fx("reverb", decay=3.0, mix=0.3, size=0.85),
        ],
        how="a saw and a square snapping open and falling back, a dotted "
        "echo side to side and a room: the chord stab of a liquid tune",
    ),
    # --------------------------------------------- dubstep / bass music (~140)
    _recipe(
        "Wobble Bass",
        ["bass", "dubstep"],
        "A1",
        DUBSTEP_BPM,
        _patch(
            "Wobble Bass",
            osc1="saw",
            osc1_level=0.4,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.25,
            osc2_detune=6.0,
            sub_level=0.25,
            filter_type="ladder",
            # the sweep runs 200 Hz to 2.8 kHz: a resonant ladder swept
            # down to its floor rings under 20 Hz (a fifth of the energy)
            cutoff=1500.0,
            resonance=0.55,
            filter_env=0.0,
            key_track=0.2,
            # the wobble: the filter LFO on the clock, an eighth (the
            # starter takes it to the eighth triplet; 1/4 is the slow one)
            filter_lfo_shape="sine",
            filter_lfo_sync="1/8",
            filter_lfo_depth=1300.0,
            attack=0.005,
            decay=0.3,
            sustain=1.0,
            release=0.1,
            drive=2.0,
        ),
        [
            _fx("drive", amount=2.0, mix=0.5, mode=0.0),
            # a high-pass on each side of the clipper: a swept resonant
            # ladder puts a tenth of its energy under 20 Hz (the sweep's own
            # throb), and the clipped lopsided wave keeps an offset
            _fx("highpass", cutoff=35.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-7.0, soft=0.3, mix=1.0),
            _fx("highpass", cutoff=35.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="a saw and a square over a sub through a resonant ladder whose "
        "LFO is synced to the clock — the eighth here; the sync menu has "
        "the quarter and the eighth triplet — driven: the wobble",
    ),
    _recipe(
        "Growl Bass",
        ["bass", "dubstep"],
        "A1",
        DUBSTEP_BPM,
        _patch(
            "Growl Bass",
            osc1="fm4",
            osc1_level=0.6,
            # a 4-operator stack (4->3->2->1): an octave-down modulator on
            # the carrier for the weight, the snarl on that, and operator 4
            # feeding back on itself for the grit
            fm4_algo="stack",
            fm4_ratio1=1.0,
            fm4_ratio2=0.5,
            fm4_ratio3=1.0,
            fm4_ratio4=1.0,
            fm4_level1=1.0,
            fm4_level2=0.4,
            fm4_level3=0.25,
            fm4_level4=0.25,
            fm4_feedback=0.6,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=8.0,
            # (the stack is brighter than the two operators were: a fuller
            # sub keeps a third of it under 120 Hz)
            sub_level=0.7,
            # the vowel the growl talks through
            formant_amount=0.6,
            formant_vowel=0.55,
            formant_size=1.0,
            filter_type="lowpass",
            cutoff=800.0,
            resonance=0.6,
            filter_env=0.0,
            key_track=0.2,
            filter_lfo_shape="triangle",
            filter_lfo_sync="1/16",
            filter_lfo_depth=1500.0,
            # and every quarter the saw steps in and out
            lfo_shape="square",
            lfo_sync="1/4",
            mod1_src="lfo",
            mod1_dst="osc2_level",
            mod1_amt=0.4,
            attack=0.005,
            decay=0.3,
            sustain=1.0,
            release=0.1,
            drive=2.5,
        ),
        [*chain_preset("growl_squash")],
        how="a 4-operator FM stack (an octave-down modulator, the snarl on "
        "it, operator 4 feeding back), a saw that steps in and out on the "
        "quarter, the vowel filter and a resonant low-pass swept on the "
        "sixteenth, through the Growl Squash chain: the growl",
    ),
    _recipe(
        "Half-Time Sub",
        ["bass", "dubstep"],
        "A1",
        DUBSTEP_BPM,
        _patch(
            "Half-Time Sub",
            osc1="sine",
            osc1_level=0.9,
            filter_type="lowpass",
            cutoff=220.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.0,
            attack=0.004,
            decay=0.3,
            sustain=0.9,
            release=0.15,
            glide=0.06,
            drive=1.1,
        ),
        [_fx("highpass", cutoff=22.0, q=0.7)],
        how="a plain sine sub, held, gliding, mono, nothing over 220 Hz: the "
        "weight under a half-time drop while the wobble and the growl take "
        "the mids",
    ),
    _recipe(
        "Screech Lead",
        ["lead", "dubstep"],
        "C4",
        DUBSTEP_BPM,
        _patch(
            "Screech Lead",
            osc1="saw",
            osc1_level=0.35,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.55,
            osc2_detune=0.0,
            # osc2 hard synced to osc1 an octave over it, its sync pitch
            # swept on the sixteenth: the scream
            osc2_sync="on",
            sync_pitch=12.0,
            lfo_shape="triangle",
            lfo_sync="1/16",
            mod1_src="lfo",
            mod1_dst="sync_pitch",
            mod1_amt=0.35,
            filter_type="bandpass",
            cutoff=2500.0,
            resonance=0.7,
            filter_env=0.0,
            key_track=0.4,
            filter_lfo_shape="triangle",
            filter_lfo_sync="1/16",
            filter_lfo_depth=1500.0,
            attack=0.005,
            decay=0.2,
            sustain=0.8,
            release=0.15,
            vibrato_rate=7.0,
            vibrato_depth=0.25,
            vibrato_delay=0.1,
            drive=2.5,
        ),
        [
            _fx("drive", amount=3.0, mix=0.5, mode=2.0),
            _fx("ott", depth=0.6, output=0.0),
            _fx("highpass", cutoff=300.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="a square hard synced to a saw an octave over it, the sync's "
        "pitch and a resonant band-pass both swept on the sixteenth, a fast "
        "vibrato, fuzzed and squashed: the screech over a drop",
    ),
    # --------------------------------------------------- synthwave / 80s (~100)
    _recipe(
        "Poly Chorus Pad",
        ["pad", "synthwave"],
        "C3",
        SYNTHWAVE_BPM,
        _patch(
            "Poly Chorus Pad",
            osc1="saw",
            osc1_level=0.6,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.45,
            osc2_detune=5.0,
            pwm_rate=0.4,
            pwm_depth=0.4,
            filter_type="lowpass",
            cutoff=2200.0,
            resonance=0.15,
            filter_env=800.0,
            filter_attack=0.4,
            filter_decay=1.2,
            filter_sustain=0.6,
            key_track=0.3,
            attack=0.35,
            decay=0.8,
            sustain=0.85,
            release=1.4,
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            # the 80s polysynth's own chorus: one slow, one a little faster
            _fx("chorus", rate=0.5, depth=3.5, mix=0.5),
            _fx("chorus", rate=0.85, depth=2.0, mix=0.3),
            _fx("reverb", decay=3.5, mix=0.3, size=0.9),
        ],
        how="a saw and a slowly pulsing square through a low-pass that opens "
        "as the chord swells, through two choruses as an 80s polysynth's "
        "own, into a room",
    ),
    _recipe(
        "Poly Brass Stab",
        ["keys", "synthwave"],
        "C4",
        SYNTHWAVE_BPM,
        _patch(
            "Poly Brass Stab",
            osc1="saw",
            osc1_level=0.28,
            unison=3,
            unison_detune=10.0,
            unison_spread=0.7,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.2,
            osc2_detune=6.0,
            filter_type="lowpass",
            cutoff=900.0,
            resonance=0.2,
            # the brass: the filter swells open a moment after the note
            filter_env=3500.0,
            filter_attack=0.04,
            filter_decay=0.3,
            filter_sustain=0.35,
            key_track=0.5,
            attack=0.02,
            decay=0.4,
            sustain=0.55,
            release=0.25,
            drive=1.3,
            # no drift (measured): its start phases soften the
            # front the swell is heard against — 60 ms in held 1.7x the
            # front's top at C4, where in phase it holds 2.7x
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            _fx("plate", decay=1.8, tone=0.6, predelay=0.012, mix=0.25),
        ],
        how="detuned saws behind a low-pass that swells open just after the "
        "note and settles, a plate: the poly brass stab of an 80s record",
    ),
    _recipe(
        "Eighties Arp Bass",
        ["bass", "synthwave"],
        "A1",
        SYNTHWAVE_BPM,
        _patch(
            "Eighties Arp Bass",
            osc1="saw",
            osc1_level=0.6,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=4.0,
            sub_level=0.3,
            filter_type="ladder",
            cutoff=500.0,
            resonance=0.45,
            filter_env=2200.0,
            filter_decay=0.12,
            filter_sustain=0.15,
            key_track=0.4,
            attack=0.002,
            decay=0.18,
            sustain=0.4,
            release=0.08,
        ),
        [
            _fx("highpass", cutoff=30.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="a saw and a square over a sub, a resonant ladder snapping on "
        "every note and short at both ends: the driving eighth or sixteenth "
        "bass an 80s sequencer arpeggiates",
    ),
    _recipe(
        "Bright Saw Lead",
        ["lead", "synthwave"],
        "C4",
        SYNTHWAVE_BPM,
        _patch(
            "Bright Saw Lead",
            osc1="saw",
            osc1_level=0.55,
            unison=3,
            unison_detune=12.0,
            unison_spread=0.8,
            osc2="saw",
            osc2_octave=1,
            osc2_level=0.2,
            osc2_detune=4.0,
            filter_type="lowpass",
            cutoff=5000.0,
            resonance=0.15,
            filter_env=1500.0,
            filter_decay=0.3,
            filter_sustain=0.6,
            key_track=0.4,
            attack=0.01,
            decay=0.3,
            sustain=0.85,
            release=0.35,
            vibrato_rate=5.5,
            vibrato_depth=0.12,
            vibrato_delay=0.35,
            glide=0.06,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            *chain_preset("eighties_space"),
            _fx("width", amount=1.3),
        ],
        how="detuned saws and an octave over them, open and bright, a late "
        "vibrato and a glide, through the Eighties Space chain (a chorus, a "
        "dotted echo side to side, a room)",
    ),
    _recipe(
        "Lush Strings",
        ["pad", "synthwave"],
        "C3",
        SYNTHWAVE_BPM,
        _patch(
            "Lush Strings",
            osc1="saw",
            osc1_level=0.8,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.45,
            osc2_detune=5.0,
            pwm_rate=0.4,
            pwm_depth=0.3,
            filter_type="lowpass",
            cutoff=3200.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.3,
            attack=0.5,
            decay=0.6,
            sustain=0.9,
            release=1.6,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            # the String Machine's ensemble, then a longer, wider room
            _fx("chorus", rate=0.6, depth=4.0, mix=0.5),
            _fx("chorus", rate=2.0, depth=1.2, mix=0.35),
            _fx("reverb", decay=4.5, mix=0.4, size=0.95),
            _fx("width", amount=1.4),
        ],
        how="the String Machine slowed and widened: a saw and a pulsing "
        "square through the ensemble (a slow deep chorus over a fast "
        "shallow one), a slower swell, a longer, wider room",
    ),
    # ----------------------------------------------------- future bass (~150)
    _recipe(
        "Supersaw Chord Stab",
        ["keys", "future"],
        "C4",
        FUTURE_BPM,
        _patch(
            "Supersaw Chord Stab",
            osc1="supersaw",
            osc1_level=0.5,
            unison=7,
            # 20 and 9 cents (22 and 6 met a held C4 1.5 dB over the
            # hold-seam bar)
            unison_detune=20.0,
            unison_spread=1.0,
            osc2="saw",
            osc2_octave=1,
            osc2_level=0.25,
            osc2_detune=9.0,
            filter_type="lowpass",
            cutoff=6000.0,
            resonance=0.1,
            filter_env=2000.0,
            filter_decay=0.25,
            filter_sustain=0.6,
            key_track=0.4,
            attack=0.003,
            decay=0.4,
            sustain=0.8,
            release=0.3,
        ),
        [_fx("highpass", cutoff=200.0, q=0.7), *chain_preset("future_pump")],
        how="seven supersaws and a saw an octave up, bright, through the "
        "Future Pump chain — squashed over the top, ducked hard on every "
        "beat, a room, wider than wide: the future bass chord",
    ),
    _recipe(
        "Vocal Chop Lead",
        ["lead", "future"],
        "C4",
        FUTURE_BPM,
        _patch(
            "Vocal Chop Lead",
            osc1="saw",
            osc1_level=1.0,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.5,
            osc2_detune=0.0,
            # a small, bright tract
            formant_amount=0.9,
            formant_vowel=0.15,
            formant_size=1.3,
            # a yip: a little above the note, onto it at once
            pitch_env=2.0,
            pitch_env_decay=0.04,
            filter_type="lowpass",
            cutoff=5000.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.3,
            attack=0.003,
            decay=0.25,
            sustain=0.3,
            release=0.12,
        ),
        [
            _fx("highpass", cutoff=250.0, q=0.7),
            _fx("delay", feedback=0.3, mix=0.2, ping_pong=1.0, sync=2.0),
            _fx("reverb", decay=2.5, mix=0.3, size=0.8),
        ],
        # the vowel moves across the phrase, so the chops say different ones
        curves={"formant_vowel": [[0.0, 0.1], [1.0, 0.7]]},
        how="saws through a small, bright vowel filter, each note scooped "
        "onto its pitch, short; the vowel moving across the phrase so the "
        "hits say different ones: a chopped-vocal lead with no voice in it",
    ),
    _recipe(
        "Bent Pluck",
        ["lead", "future"],
        "C4",
        FUTURE_BPM,
        _patch(
            "Bent Pluck",
            osc1="saw",
            osc1_level=0.6,
            osc2="square",
            osc2_octave=1,
            osc2_level=0.25,
            osc2_detune=0.0,
            # bent up into the note, then wobbling on the sixteenth
            pitch_env=-5.0,
            pitch_env_decay=0.08,
            lfo_shape="sine",
            lfo_sync="1/16",
            mod1_src="lfo",
            mod1_dst="pitch",
            mod1_amt=0.03,
            filter_type="lowpass",
            cutoff=1800.0,
            resonance=0.3,
            filter_env=4500.0,
            filter_decay=0.12,
            filter_sustain=0.0,
            key_track=0.5,
            attack=0.002,
            decay=0.25,
            sustain=0.0,
            release=0.15,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("delay", feedback=0.4, mix=0.25, ping_pong=1.0, sync=3.0),
            _fx("reverb", decay=2.5, mix=0.3, size=0.8),
        ],
        how="a saw and a square an octave up, bent up into each note from "
        "under it and wobbling a third of a semitone on the sixteenth, "
        "plucked by the filter, a dotted echo: the bent pluck of a future "
        "bass hook",
    ),
    _recipe(
        "Future Pad",
        ["pad", "future"],
        "C3",
        FUTURE_BPM,
        _patch(
            "Future Pad",
            osc1="saw",
            osc1_level=0.55,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.45,
            osc2_detune=8.0,
            unison=4,
            unison_detune=15.0,
            unison_spread=0.9,
            formant_amount=0.4,
            formant_vowel=0.2,
            formant_size=1.1,
            filter_type="lowpass",
            cutoff=3000.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.3,
            attack=0.25,
            decay=0.6,
            sustain=0.85,
            release=1.2,
        ),
        [
            _fx("highpass", cutoff=180.0, q=0.7),
            _fx("ott", depth=0.4, output=2.0),
            _fx("pump", depth=0.6, release=0.3, beats=1.0),
            _fx("chorus", rate=0.4, depth=3.0, mix=0.3),
            _fx("reverb", decay=4.0, mix=0.4, size=0.9),
        ],
        how="detuned saws with a touch of the vowel filter, squashed a little, "
        "ducked on the beat, chorused into a room: the pad that breathes "
        "under a future bass drop",
    ),
    # --------------------------------------------- lo-fi hip-hop (~80, swung)
    _recipe(
        "Dusty Keys",
        ["keys", "lofi"],
        "C4",
        LOFI_BPM,
        _patch(
            "Dusty Keys",
            osc1="fm4",
            osc1_level=0.9,
            # an electric piano's two halves: 2->1 the body, a 14:1
            # operator 4 on 3 the tine, gone in a tenth of a second
            fm4_algo="pairs",
            fm4_ratio1=1.0,
            fm4_ratio2=1.0,
            fm4_ratio3=1.0,
            fm4_ratio4=14.0,
            fm4_level1=0.9,
            fm4_level2=0.15,
            fm4_level3=0.45,
            fm4_level4=0.22,
            fm4_decay1=2.0,
            fm4_decay2=1.0,
            fm4_decay3=0.8,
            fm4_decay4=0.1,
            filter_type="lowpass",
            cutoff=3200.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.3,
            attack=0.003,
            decay=1.6,
            sustain=0.3,
            release=0.4,
            tremolo_rate=3.5,
            tremolo_depth=0.08,
            drift=0.25,
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            *chain_preset("dusty_tape"),
            _fx("reverb", decay=1.8, mix=0.2, size=0.6),
        ],
        how="4-operator FM as an electric piano builds its sound (a body pair "
        "and a 14:1 tine gone in a tenth of a second), a slow tremolo, a "
        "little drift, through the Dusty Tape chain (wow, flutter, hiss, the "
        "top rolled off, a few bits gone): e-piano-ish, not an electric piano",
    ),
    _recipe(
        "Jazz Chord Pad",
        ["pad", "lofi"],
        "C3",
        LOFI_BPM,
        _patch(
            "Jazz Chord Pad",
            osc1="triangle",
            osc1_level=0.7,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=6.0,
            filter_type="lowpass",
            cutoff=1500.0,
            resonance=0.05,
            filter_env=400.0,
            filter_attack=0.5,
            filter_decay=1.5,
            filter_sustain=0.5,
            key_track=0.3,
            attack=0.35,
            decay=1.0,
            sustain=0.85,
            release=1.2,
            # the old polysynth's wander, note to note
            drift=0.45,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("chorus", rate=0.35, depth=3.0, mix=0.35),
            *chain_preset("dusty_tape"),
            _fx("reverb", decay=2.5, mix=0.3, size=0.8),
        ],
        how="a triangle and a quiet saw under a dark low-pass that opens as "
        "the chord swells, drifting note to note, chorused, through the "
        "Dusty Tape chain into a room: the mellow pad a lo-fi beat's seventh "
        "and ninth chords are held on",
    ),
    _recipe(
        "Soft Sub",
        ["bass", "lofi"],
        "A1",
        LOFI_BPM,
        _patch(
            "Soft Sub",
            osc1="sine",
            osc1_level=0.9,
            filter_type="lowpass",
            cutoff=200.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.0,
            # a rounded front: no click on the kick
            attack=0.015,
            decay=0.6,
            sustain=0.75,
            release=0.2,
            glide=0.03,
            drive=1.05,
        ),
        [_fx("highpass", cutoff=22.0, q=0.7)],
        how="a sine with a rounded front, settling a little under the key, "
        "mono, nothing over 200 Hz: the soft sub a lo-fi beat leans on",
    ),
    _recipe(
        "Muted Pluck",
        ["lead", "lofi"],
        "C4",
        LOFI_BPM,
        _patch(
            "Muted Pluck",
            osc1="pluck",
            osc1_level=0.9,
            # a palm on the strings: damped, the brightness gone at once
            pluck_damping=0.7,
            pluck_bright=0.25,
            filter_type="lowpass",
            cutoff=2200.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.4,
            attack=0.002,
            decay=0.3,
            sustain=0.0,
            release=0.15,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            *chain_preset("dusty_tape"),
            _fx("reverb", decay=1.5, mix=0.18, size=0.6),
        ],
        how="a plucked string damped hard and darkened, short, through the "
        "Dusty Tape chain: a muted, guitar-ish pluck for a lo-fi riff — "
        "guitar-ish, not a guitar",
    ),
    # ------------------------------------------ ambient / cinematic (~70)
    _recipe(
        "Scanning Pad",
        ["pad", "ambient"],
        "C3",
        AMBIENT_BPM,
        _patch(
            "Scanning Pad",
            osc1="wavetable",
            osc1_level=0.8,
            wt_table="scan",
            wt_pos=0.3,
            unison=3,
            unison_detune=9.0,
            unison_spread=0.9,
            filter_type="lowpass",
            cutoff=3200.0,
            resonance=0.1,
            filter_env=0.0,
            filter_lfo_rate=0.08,
            filter_lfo_depth=500.0,
            key_track=0.3,
            attack=1.5,
            decay=1.0,
            sustain=0.9,
            release=3.0,
            drift=0.35,
        ),
        [
            _fx("chorus", rate=0.2, depth=3.0, mix=0.3),
            *chain_preset("huge_hall"),
        ],
        # the band of harmonics climbs across the phrase
        curves={"wt_pos": [[0.0, 0.1], [1.0, 0.8]]},
        how="the scan wavetable — one band of harmonics — swept up across the "
        "phrase by a curve on its position, three drifting copies, a slow "
        "swell, into the Huge Hall: a pad that evolves while it is held",
    ),
    _recipe(
        "Vowel Drift Pad",
        ["pad", "ambient"],
        "C3",
        AMBIENT_BPM,
        _patch(
            "Vowel Drift Pad",
            osc1="wavetable",
            osc1_level=0.85,
            wt_table="formant",
            wt_pos=0.0,
            unison=2,
            unison_detune=7.0,
            unison_spread=0.8,
            filter_type="lowpass",
            cutoff=4000.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.3,
            attack=2.0,
            decay=1.0,
            sustain=0.9,
            release=3.5,
            drift=0.5,
        ),
        [*chain_preset("huge_hall")],
        # a, e, i, u: the vowel moves across the phrase
        curves={"wt_pos": [[0.0, 0.0], [1.0, 1.0]]},
        how="the formant wavetable (a voice's harmonics under the a, e, i "
        "and u resonances) walked from one vowel to the next across the "
        "phrase, drifting, swelling over two seconds into the Huge Hall: "
        "voice-like, no voice in it",
    ),
    _recipe(
        "Shimmer Bed",
        ["pad", "ambient"],
        "C3",
        AMBIENT_BPM,
        _patch(
            "Shimmer Bed",
            osc1="triangle",
            osc1_level=0.7,
            osc2="sine",
            osc2_octave=1,
            osc2_level=0.35,
            osc2_detune=3.0,
            filter_type="lowpass",
            cutoff=2500.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.3,
            attack=1.2,
            decay=1.0,
            sustain=0.9,
            release=3.0,
            drift=0.3,
        ),
        [*chain_preset("shimmer_wash")],
        how="a triangle and a sine an octave up, soft and slow, into the "
        "Shimmer Wash — a room whose tail climbs an octave each time round: "
        "the bed a cinematic cue opens on",
    ),
    _recipe(
        "Glass Bell Texture",
        ["keys", "ambient"],
        "C4",
        AMBIENT_BPM,
        _patch(
            "Glass Bell Texture",
            osc1="fm4",
            osc1_level=0.85,
            # two struck pairs at inharmonic ratios: a bell, not a key
            fm4_algo="pairs",
            fm4_ratio1=1.0,
            fm4_ratio2=3.5,
            fm4_ratio3=2.76,
            fm4_ratio4=7.1,
            fm4_level1=0.9,
            fm4_level2=0.35,
            fm4_level3=0.5,
            fm4_level4=0.2,
            fm4_decay1=4.0,
            fm4_decay2=1.5,
            fm4_decay3=2.5,
            fm4_decay4=0.6,
            filter_type="lowpass",
            cutoff=7000.0,
            resonance=0.0,
            filter_env=0.0,
            key_track=0.3,
            attack=0.002,
            decay=3.0,
            sustain=0.0,
            release=2.0,
            env_curve=3.0,
        ),
        [
            _fx("delay", feedback=0.4, mix=0.25, ping_pong=1.0, sync=3.0),
            *chain_preset("huge_hall"),
        ],
        how="4-operator FM as two struck pairs at inharmonic ratios (3.5:1 on "
        "the fundamental, 7.1:1 on a 2.76 partial), their brightness dying "
        "over seconds, a dotted echo into the Huge Hall: a glass bell "
        "texture",
    ),
    _recipe(
        "Deep Drone",
        ["bass", "ambient"],
        "A1",
        AMBIENT_BPM,
        _patch(
            "Deep Drone",
            osc1="saw",
            osc1_level=0.5,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.4,
            osc2_detune=7.0,
            sub_level=0.5,
            filter_type="lowpass",
            cutoff=320.0,
            resonance=0.2,
            filter_env=0.0,
            key_track=0.2,
            filter_lfo_rate=0.07,
            filter_lfo_depth=180.0,
            attack=2.0,
            decay=1.0,
            sustain=1.0,
            release=3.0,
            # an old oscillator left on: its pitch and its filter wander
            drift=0.7,
        ),
        [
            _fx("highpass", cutoff=25.0, q=0.7),
            _fx("chorus", rate=0.15, depth=2.0, mix=0.25),
            _fx("reverb", decay=4.0, mix=0.25, size=0.9),
        ],
        how="two saws a few cents apart over a sub behind a low-pass that "
        "breathes once every fifteen seconds, drifting hard, swelling in over "
        "two seconds and held: the drone under a cinematic cue",
    ),
    _recipe(
        "Cinematic Braam",
        ["fx", "ambient"],
        "C2",
        AMBIENT_BPM,
        _patch(
            "Cinematic Braam",
            osc1="saw",
            osc1_level=0.5,
            unison=5,
            unison_detune=12.0,
            unison_spread=0.8,
            osc2="square",
            osc2_octave=-1,
            osc2_level=0.35,
            osc2_detune=0.0,
            sub_level=0.3,
            filter_type="lowpass",
            cutoff=200.0,
            resonance=0.25,
            # the brass: the filter blares open a moment after the hit
            filter_env=1800.0,
            filter_attack=0.12,
            filter_decay=1.5,
            filter_sustain=0.2,
            key_track=0.0,
            attack=0.02,
            decay=2.5,
            sustain=0.0,
            release=1.5,
            env_curve=2.0,
            drive=2.5,
        ),
        [
            _fx("drive", amount=2.0, mix=0.5, mode=3.0),
            _fx("highpass", cutoff=30.0, q=0.7),
            _fx("reverb", decay=6.0, mix=0.4, size=1.0),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="detuned saws and a square an octave under them, driven, behind a "
        "low-pass that blares open a moment after the hit and closes over "
        "two seconds, into a long hall: the brass-ish braam a trailer hits on",
    ),
    # ----------------------------------------------- chiptune / 8-bit (~140)
    _recipe(
        "Square Lead",
        ["lead", "chiptune"],
        "C5",
        CHIP_BPM,
        _patch(
            "Square Lead",
            osc1="square",
            osc1_level=0.45,
            filter_type="none",
            key_track=0.0,
            attack=0.002,
            decay=0.12,
            sustain=0.8,
            release=0.04,
            # a console's vibrato: late and quick
            vibrato_rate=6.5,
            vibrato_depth=0.2,
            vibrato_delay=0.25,
        ),
        [
            _fx("highpass", cutoff=100.0, q=0.7),
            _fx("delay", feedback=0.25, mix=0.15, ping_pong=0.0, sync=3.0),
        ],
        how="a 50 % square with no filter at all, a late quick vibrato, a "
        "short release, one dotted echo: the 8-bit console's lead channel",
    ),
    _recipe(
        "Pulse Lead",
        ["lead", "chiptune"],
        "C5",
        CHIP_BPM,
        _patch(
            "Pulse Lead",
            osc1="wavetable",
            osc1_level=0.55,
            # the pulse table's second frame: 30 % duty, the nearest it holds
            # to the console's 25 %
            wt_table="pulse",
            wt_pos=1 / 3,
            filter_type="none",
            key_track=0.0,
            attack=0.002,
            decay=0.1,
            sustain=0.75,
            release=0.04,
            vibrato_rate=6.5,
            vibrato_depth=0.15,
            vibrato_delay=0.3,
        ),
        [_fx("highpass", cutoff=100.0, q=0.7)],
        how="the pulse wavetable at its 30 % frame (the nearest it holds to "
        "the 25 % duty), unfiltered, a late vibrato: the console's thinner, "
        "nasal lead",
    ),
    _recipe(
        "Thin Pulse Arp",
        ["lead", "chiptune"],
        "C5",
        CHIP_BPM,
        _patch(
            "Thin Pulse Arp",
            osc1="wavetable",
            osc1_level=0.55,
            # the third frame: 15 % duty (the console's 12.5 %, near enough)
            wt_table="pulse",
            wt_pos=2 / 3,
            filter_type="none",
            key_track=0.0,
            attack=0.001,
            decay=0.09,
            sustain=0.0,
            release=0.03,
        ),
        [_fx("highpass", cutoff=100.0, q=0.7)],
        how="the pulse wavetable at its 15 % frame (the 12.5 % duty, near "
        "enough), each note gone in a tenth of a second: what the arpeggio "
        "plays — a chord as one channel's notes in turn",
    ),
    _recipe(
        "Triangle Bass",
        ["bass", "chiptune"],
        "A1",
        CHIP_BPM,
        _patch(
            "Triangle Bass",
            osc1="triangle",
            osc1_level=0.9,
            filter_type="none",
            key_track=0.0,
            attack=0.002,
            decay=0.2,
            sustain=0.9,
            release=0.03,
        ),
        [
            # the console's triangle is sixteen steps: four bits
            _fx("bitcrush", bits=4.0, downsample=1.0),
            _fx("highpass", cutoff=30.0, q=0.7),
        ],
        how="a triangle crushed to four bits — the console's triangle "
        "channel is sixteen steps — held, with no filter: the 8-bit bass",
    ),
    _recipe(
        "Crush Lead",
        ["lead", "chiptune"],
        "C5",
        CHIP_BPM,
        _patch(
            "Crush Lead",
            osc1="wavetable",
            osc1_level=0.6,
            # a saw at five levels, sample-held sixteen at a time
            wt_table="crush",
            wt_pos=1 / 3,
            filter_type="none",
            key_track=0.0,
            attack=0.002,
            decay=0.15,
            sustain=0.7,
            release=0.05,
            vibrato_rate=6.0,
            vibrato_depth=0.12,
            vibrato_delay=0.3,
            glide=0.03,
        ),
        [*chain_preset("chip_crush")],
        how="the crush wavetable (a saw quantised to five levels and held) "
        "through the 8-Bit Crush chain: a gritty, sample-chip lead",
    ),
    # ---------------------------------------------------- hardstyle (~150)
    _recipe(
        "Sync Screech",
        ["lead", "hardstyle"],
        "C4",
        HARDSTYLE_BPM,
        _patch(
            "Sync Screech",
            osc1="saw",
            osc1_level=0.3,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.7,
            osc2_detune=0.0,
            # osc2 hard synced to osc1, swept by a sixteenth LFO and the
            # filter envelope: the screech
            osc2_sync="on",
            sync_pitch=12.0,
            lfo_shape="triangle",
            lfo_sync="1/16",
            mod1_src="lfo",
            mod1_dst="sync_pitch",
            mod1_amt=0.4,
            mod2_src="fenv",
            mod2_dst="sync_pitch",
            mod2_amt=0.4,
            filter_type="lowpass",
            cutoff=6000.0,
            resonance=0.2,
            filter_env=2000.0,
            filter_decay=0.3,
            filter_sustain=0.3,
            key_track=0.3,
            attack=0.003,
            decay=0.2,
            sustain=0.85,
            release=0.15,
            drive=2.0,
        ),
        [
            _fx("drive", amount=3.0, mix=0.5, mode=1.0),
            _fx("ott", depth=0.5, output=0.0),
            _fx("clip", drive=0.0, ceiling=-7.0, soft=0.3, mix=1.0),
            _fx("highpass", cutoff=250.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="a saw hard synced to a saw, its pitch swept on the sixteenth and "
        "by the filter envelope, driven, squashed: the hardstyle screech",
    ),
    _recipe(
        "Euphoric Supersaw",
        ["lead", "hardstyle"],
        "C4",
        HARDSTYLE_BPM,
        _patch(
            "Euphoric Supersaw",
            osc1="supersaw",
            osc1_level=0.5,
            unison=7,
            unison_detune=18.0,
            unison_spread=1.0,
            osc2="saw",
            osc2_octave=1,
            osc2_level=0.2,
            osc2_detune=7.0,
            filter_type="lowpass",
            cutoff=7000.0,
            resonance=0.1,
            filter_env=1500.0,
            filter_decay=0.4,
            filter_sustain=0.6,
            key_track=0.4,
            attack=0.008,
            decay=0.4,
            sustain=0.85,
            release=0.45,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("ott", depth=0.4, output=0.0),
            _fx("delay", feedback=0.3, mix=0.2, ping_pong=1.0, sync=3.0),
            _fx("reverb", decay=3.5, mix=0.35, size=0.95),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="seven supersaws and a saw an octave over them, bright, squashed "
        "a little, a dotted echo and a long room: the euphoric lead of a "
        "hardstyle break",
    ),
    _recipe(
        "Reverse Bass",
        ["bass", "hardstyle"],
        "A1",
        HARDSTYLE_BPM,
        _patch(
            "Reverse Bass",
            osc1="saw",
            osc1_level=0.55,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.35,
            osc2_detune=8.0,
            sub_level=0.4,
            filter_type="lowpass",
            cutoff=300.0,
            resonance=0.3,
            # it swells INTO the next kick: the level and the filter rise
            # across the offbeat and are cut, never decay
            filter_env=1800.0,
            filter_attack=0.15,
            filter_decay=0.1,
            filter_sustain=1.0,
            key_track=0.2,
            attack=0.14,
            decay=0.1,
            sustain=1.0,
            release=0.03,
            env_curve=3.0,
            drive=2.0,
        ),
        [
            # a high-pass on each side of the clipper: the lopsided swell
            # keeps an offset
            _fx("highpass", cutoff=35.0, q=0.7),
            _fx("clip", drive=2.0, ceiling=-7.0, soft=0.3, mix=1.0),
            _fx("highpass", cutoff=35.0, q=0.7),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="saws over a sub whose level and filter swell across the offbeat "
        "and are cut dead at its end: the reverse-bass offbeat between "
        "hardstyle kicks (reverse-bass-ish: a swell, not a reversed kick)",
    ),
    _recipe(
        "Euphoric Pad",
        ["pad", "hardstyle"],
        "C3",
        HARDSTYLE_BPM,
        _patch(
            "Euphoric Pad",
            osc1="supersaw",
            osc1_level=0.45,
            unison=5,
            # five at 16 cents: seven at 14 met a held C4 1 dB over the
            # hold-seam bar, and at 16 its hold sank under the pads' -9 dB
            unison_detune=16.0,
            unison_spread=1.0,
            filter_type="lowpass",
            cutoff=3000.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.3,
            attack=0.4,
            decay=0.8,
            sustain=0.9,
            release=1.5,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("pump", depth=0.7, release=0.25, beats=1.0),
            _fx("reverb", decay=4.0, mix=0.4, size=0.95),
            _fx("width", amount=1.4),
        ],
        how="supersaws swelling under a low-pass, ducked on every beat, into "
        "a long wide room: the pad a hardstyle break lifts on",
    ),
    # -------------------------------------------------------- phonk (~140)
    _recipe(
        "Phonk Cowbell",
        ["lead", "phonk"],
        "C5",
        PHONK_BPM,
        _patch(
            "Phonk Cowbell",
            osc1="fm4",
            osc1_level=1.0,
            # the drum machine's cowbell is two tones a fifth-ish apart
            # (1 : 1.48), within 4 dB of each other here; operator 2 roughens
            # the low one a little, 4 is its echo
            fm4_algo="pair_plus",
            fm4_ratio1=1.0,
            fm4_ratio2=3.0,
            fm4_ratio3=1.48,
            fm4_ratio4=4.44,
            fm4_level1=0.9,
            fm4_level2=0.12,
            fm4_level3=0.75,
            fm4_level4=0.15,
            fm4_decay1=0.0,
            fm4_decay2=0.08,
            fm4_decay3=0.0,
            fm4_decay4=0.1,
            filter_type="bandpass",
            cutoff=1100.0,
            resonance=0.2,
            filter_env=0.0,
            key_track=0.8,
            attack=0.001,
            decay=0.35,
            sustain=0.0,
            release=0.12,
            env_curve=3.0,
            drive=4.0,
        ),
        [
            _fx("highpass", cutoff=300.0, q=0.7),
            _fx("drive", amount=2.0, mix=0.4, mode=1.0),
            _fx("reverb", decay=1.2, mix=0.15, size=0.5),
            _fx("clip", drive=0.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="4-operator FM as a drum machine's cowbell is built — two tones "
        "1 : 1.48 apart, one roughened — through a band-pass, short and "
        "driven, played as a melody: the defining sound of phonk",
    ),
    _recipe(
        "Phonk 808",
        ["bass", "phonk"],
        "A1",
        PHONK_BPM,
        _patch(
            "Phonk 808",
            osc1="sine",
            osc1_level=0.85,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=0.0,
            pitch_env=10.0,
            pitch_env_decay=0.05,
            filter_type="lowpass",
            cutoff=2500.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.0,
            attack=0.002,
            decay=1.2,
            sustain=0.55,
            release=0.35,
            env_curve=2.5,
            glide=0.07,
            drive=4.0,
        ),
        [*chain_preset("phonk_crush")],
        how="the 808's falling sine and glide with a square under it, "
        "driven to the drive's end and through the Phonk Crush chain (fuzz, "
        "lost bits, the ceiling): the distorted 808 under a drift phonk beat",
    ),
    _recipe(
        "Dark Vowel Pad",
        ["pad", "phonk"],
        "C3",
        PHONK_BPM,
        _patch(
            "Dark Vowel Pad",
            osc1="wavetable",
            osc1_level=0.8,
            # the formant table on its "u": a dark, sung-ish vowel
            wt_table="formant",
            wt_pos=1.0,
            unison=3,
            unison_detune=10.0,
            unison_spread=0.8,
            filter_type="lowpass",
            cutoff=2200.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.3,
            attack=0.3,
            decay=0.8,
            sustain=0.85,
            release=1.0,
            drift=0.4,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("bitcrush", bits=10.0, downsample=2.0),
            _fx("lowpass", cutoff=5000.0, q=0.7),
            _fx("reverb", decay=3.0, mix=0.35, size=0.85),
        ],
        how="the formant wavetable on a dark 'u', three drifting copies, a "
        "few bits and the top gone, a room: the lo-fi, vocal-ish pad behind "
        "a memphis-style beat — vocal-ish, no voice in it",
    ),
    _recipe(
        "Hard Phonk Lead",
        ["lead", "phonk"],
        "C4",
        PHONK_BPM,
        _patch(
            "Hard Phonk Lead",
            osc1="saw",
            osc1_level=0.45,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.5,
            osc2_detune=0.0,
            # osc2 synced a fifth up: hard, nasal
            osc2_sync="on",
            sync_pitch=7.0,
            filter_type="lowpass",
            cutoff=3500.0,
            resonance=0.25,
            filter_env=1500.0,
            filter_decay=0.15,
            filter_sustain=0.3,
            key_track=0.4,
            attack=0.002,
            decay=0.2,
            sustain=0.7,
            release=0.1,
            glide=0.05,
            drive=2.5,
        ),
        [*chain_preset("phonk_crush")],
        how="a saw and a square hard synced a fifth over it, snapping open, "
        "gliding, through the Phonk Crush chain: the hard lead over a phonk "
        "beat",
    ),
    # --- the core library --------------------------------------------------------
    # Named for what they are, every category a synth library has; where a
    # genre recipe or a factory sound already IS the thing, `CORE_SHELF`
    # lists it instead of a second copy. Basses first.
    _recipe(
        "Ladder Lead Bass",
        ["bass", "core"],
        "A1",
        CORE_BPM,
        _patch(
            "Ladder Lead Bass",
            osc1="saw",
            osc1_level=0.4,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=5.0,
            sub_level=0.2,
            filter_type="ladder",
            cutoff=420.0,
            resonance=0.4,
            filter_env=1800.0,
            filter_attack=0.002,
            filter_decay=0.28,
            filter_sustain=0.2,
            key_track=0.4,
            attack=0.003,
            decay=0.4,
            sustain=0.8,
            release=0.15,
            glide=0.06,
            drive=1.8,
        ),
        [_fx("highpass", cutoff=28.0, q=0.7)],
        how="a saw and a square a few cents apart over a sub, into a resonant "
        "ladder low-pass that snaps open and settles, driven, gliding between "
        "notes: the fat monophonic lead bass of the 70s ladder-filter synths",
    ),
    _recipe(
        "Synth-Pop Bass",
        ["bass", "core"],
        "A1",
        CORE_BPM,
        _patch(
            "Synth-Pop Bass",
            osc1="square",
            osc1_level=0.5,
            pwm_depth=0.35,
            pwm_rate=0.5,
            sub_level=0.35,
            filter_type="lowpass",
            cutoff=650.0,
            resonance=0.2,
            filter_env=1500.0,
            filter_decay=0.18,
            filter_sustain=0.15,
            key_track=0.4,
            attack=0.002,
            decay=0.25,
            sustain=0.6,
            release=0.1,
        ),
        [
            _fx("highpass", cutoff=30.0, q=0.7),
            _fx("chorus", rate=0.5, depth=2.5, mix=0.3),
        ],
        how="a slowly modulated pulse over a square sub, a short filter blip "
        "on each note, a polysynth's chorus: the bouncy bass of 80s synth-pop",
    ),
    _recipe(
        "FM Slap Bass",
        ["bass", "core"],
        "A1",
        CORE_BPM,
        _patch(
            "FM Slap Bass",
            osc1="fm4",
            osc1_level=0.9,
            # 2->1 the body, bright for an instant; 4->3 the octave's pop,
            # operator 4 a click gone in 30 ms: the thumb on the string
            fm4_algo="pairs",
            fm4_ratio1=1.0,
            fm4_ratio2=1.0,
            fm4_ratio3=3.0,
            fm4_ratio4=5.0,
            fm4_level1=0.9,
            fm4_level2=0.4,
            fm4_level3=0.5,
            fm4_level4=0.35,
            fm4_decay1=0.0,
            fm4_decay2=0.0,
            fm4_decay3=0.0,
            fm4_decay4=0.03,
            filter_type="lowpass",
            cutoff=2500.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.3,
            attack=0.001,
            decay=0.35,
            sustain=0.45,
            release=0.08,
        ),
        [
            _fx("highpass", cutoff=30.0, q=0.7),
            _fx("clip", drive=3.0, ceiling=-1.0, soft=0.6, mix=1.0),
        ],
        how="4-operator FM as two pairs: the body bright for an instant, the "
        "octave popping over it, a 5:1 click gone in 30 ms, squashed a little: "
        "the snap of a slapped bass, not a bass guitar",
    ),
    _recipe(
        "Rubber Bass",
        ["bass", "core"],
        "A1",
        CORE_BPM,
        _patch(
            "Rubber Bass",
            osc1="fm",
            osc1_level=0.8,
            fm_ratio=1.0,
            fm_index=5.0,
            fm_decay=0.4,
            sub_level=0.2,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.2,
            osc2_detune=0.0,
            filter_type="lowpass",
            cutoff=1200.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.3,
            attack=0.002,
            decay=0.3,
            sustain=0.6,
            release=0.1,
        ),
        [_fx("highpass", cutoff=28.0, q=0.7)],
        how="2-operator FM at 1:1 whose index falls away in a fifth of a "
        "second over a sine sub: a round, bouncing bass that 'bwows' on "
        "every note",
    ),
    _recipe(
        "Upright Bass",
        ["bass", "core"],
        "A1",
        CORE_BPM,
        _patch(
            "Upright Bass",
            osc1="pluck",
            osc1_level=1.0,
            pluck_damping=0.75,
            pluck_bright=0.4,
            sub_level=0.45,
            filter_type="lowpass",
            cutoff=900.0,
            resonance=0.05,
            filter_env=400.0,
            filter_decay=0.15,
            filter_sustain=0.0,
            key_track=0.5,
            attack=0.002,
            decay=1.2,
            sustain=0.0,
            release=0.25,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=30.0, q=0.7),
            _fx("reverb", decay=1.0, mix=0.12, size=0.4),
        ],
        how="a dark, damped plucked string over a sine for the body, dying "
        "away, in a small room: upright-ish, not a double bass (no bow, no "
        "finger noise)",
    ),
    # leads
    _recipe(
        "Brass Lead",
        ["lead", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Brass Lead",
            osc1="saw",
            osc1_level=0.38,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.3,
            osc2_detune=8.0,
            filter_type="lowpass",
            cutoff=1200.0,
            resonance=0.15,
            filter_env=2500.0,
            filter_attack=0.06,
            filter_decay=0.4,
            filter_sustain=0.5,
            key_track=0.5,
            attack=0.03,
            decay=0.3,
            sustain=0.85,
            release=0.2,
            vibrato_rate=5.5,
            vibrato_depth=0.12,
            vibrato_delay=0.35,
            glide=0.03,
            drive=1.3,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("reverb", decay=1.5, mix=0.18, size=0.6),
        ],
        how="two saws a few cents apart whose low-pass opens 60 ms after the "
        "note (a brass player's swell), a late vibrato, a little glide: a "
        "brassy solo lead, not a trumpet",
    ),
    _recipe(
        "Portamento Whistle",
        ["lead", "core"],
        "C5",
        CORE_BPM,
        _patch(
            "Portamento Whistle",
            osc1="sine",
            osc1_level=0.85,
            filter_type="none",
            attack=0.04,
            decay=0.2,
            sustain=0.9,
            release=0.15,
            vibrato_rate=5.5,
            vibrato_depth=0.2,
            vibrato_delay=0.2,
            glide=0.12,
        ),
        [
            _fx("delay", feedback=0.3, mix=0.15, ping_pong=1.0, sync=3.0),
            _fx("reverb", decay=2.0, mix=0.2, size=0.7),
        ],
        how="a pure sine sliding a long way between notes, a vibrato coming "
        "in late, a dotted echo: the whistled portamento lead",
    ),
    _recipe(
        "Soft Sine Lead",
        ["lead", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Soft Sine Lead",
            osc1="sine",
            osc1_level=0.8,
            osc2="triangle",
            osc2_octave=1,
            osc2_level=0.2,
            osc2_detune=0.0,
            filter_type="lowpass",
            cutoff=3000.0,
            resonance=0.05,
            filter_env=0.0,
            attack=0.015,
            decay=0.3,
            sustain=0.85,
            release=0.25,
            vibrato_rate=5.0,
            vibrato_depth=0.1,
            vibrato_delay=0.4,
        ),
        [
            *chain_preset("dotted_ping_pong"),
            _fx("reverb", decay=1.8, mix=0.2, size=0.7),
        ],
        how="a sine and a quiet triangle an octave up, a soft front and a "
        "late vibrato, a dotted ping-pong echo: a gentle lead that sits over "
        "anything",
    ),
    _recipe(
        "Theremin Lead",
        ["lead", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Theremin Lead",
            osc1="sine",
            osc1_level=0.85,
            osc2="triangle",
            osc2_octave=0,
            osc2_level=0.15,
            osc2_detune=0.0,
            filter_type="none",
            attack=0.08,
            decay=0.3,
            sustain=0.9,
            release=0.3,
            vibrato_rate=6.5,
            vibrato_depth=0.35,
            vibrato_delay=0.05,
            glide=0.25,
        ),
        [_fx("reverb", decay=2.5, mix=0.3, size=0.8)],
        how="a sine with a breath of triangle, sliding slowly from note to "
        "note, a wide vibrato at once, in a big room: theremin-ish, an "
        "eerie, wavering voice",
    ),
    _recipe(
        "PWM Lead",
        ["lead", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "PWM Lead",
            osc1="square",
            osc1_level=0.65,
            pwm_depth=0.6,
            pwm_rate=0.8,
            filter_type="lowpass",
            cutoff=2800.0,
            resonance=0.15,
            filter_env=1500.0,
            filter_decay=0.25,
            filter_sustain=0.4,
            key_track=0.5,
            attack=0.005,
            decay=0.3,
            sustain=0.8,
            release=0.2,
            vibrato_rate=5.0,
            vibrato_depth=0.08,
            vibrato_delay=0.4,
            glide=0.04,
        ),
        [
            _fx("chorus", rate=0.4, depth=2.0, mix=0.25),
            _fx("delay", feedback=0.3, mix=0.15, ping_pong=1.0, sync=3.0),
        ],
        how="a square whose pulse width breathes, a low-pass that opens on "
        "each note, chorused, a dotted echo: the hollow, moving PWM lead",
    ),
    # keys
    _recipe(
        "Tine EP",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Tine EP",
            osc1="fm4",
            osc1_level=0.9,
            # the body pair and the tine pair: a 14:1 operator 4 on 3, gone
            # in 80 ms, the bell of a struck tine over a round body
            fm4_algo="pairs",
            fm4_ratio1=1.0,
            fm4_ratio2=1.0,
            fm4_ratio3=1.0,
            fm4_ratio4=14.0,
            fm4_level1=0.9,
            fm4_level2=0.2,
            fm4_level3=0.4,
            fm4_level4=0.25,
            fm4_decay1=2.5,
            fm4_decay2=1.2,
            fm4_decay3=1.0,
            fm4_decay4=0.08,
            filter_type="lowpass",
            cutoff=5000.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.3,
            attack=0.002,
            decay=2.0,
            sustain=0.25,
            release=0.5,
            tremolo_rate=4.5,
            tremolo_depth=0.12,
        ),
        [
            _fx("highpass", cutoff=100.0, q=0.7),
            _fx("chorus", rate=0.4, depth=2.0, mix=0.25),
            _fx("reverb", decay=1.6, mix=0.18, size=0.6),
        ],
        how="4-operator FM as a tine piano builds its sound: a round body "
        "pair and a 14:1 tine gone in 80 ms, a stereo tremolo, chorused, a "
        "small room: tine-EP-ish (the Rhodes-style electric piano), clean",
    ),
    _recipe(
        "Reed EP",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Reed EP",
            osc1="fm4",
            osc1_level=0.9,
            # three modulators on one carrier: 1:1 the bark, 3:1 the reed's
            # odd edge, 7:1 the strike
            fm4_algo="three",
            fm4_ratio1=1.0,
            fm4_ratio2=1.0,
            fm4_ratio3=3.0,
            fm4_ratio4=7.0,
            fm4_level1=0.9,
            fm4_level2=0.45,
            fm4_level3=0.2,
            fm4_level4=0.15,
            fm4_decay1=2.0,
            fm4_decay2=0.6,
            fm4_decay3=0.3,
            fm4_decay4=0.05,
            filter_type="lowpass",
            cutoff=4000.0,
            resonance=0.05,
            filter_env=0.0,
            key_track=0.3,
            attack=0.002,
            decay=1.6,
            sustain=0.3,
            release=0.3,
            tremolo_rate=5.5,
            tremolo_depth=0.2,
            drive=1.4,
        ),
        [
            _fx("highpass", cutoff=100.0, q=0.7),
            _fx("reverb", decay=1.2, mix=0.15, size=0.5),
        ],
        how="4-operator FM with three modulators on one carrier (the bark, "
        "a reed's odd edge, the strike), driven a little, a fast tremolo: "
        "reed-EP-ish (the Wurli-style electric piano)",
    ),
    _recipe(
        "FM Bell EP",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "FM Bell EP",
            osc1="fm4",
            osc1_level=0.9,
            # the tine pair louder and longer than the Tine EP's: the 80s
            # digital piano's glassy bell
            fm4_algo="pairs",
            fm4_ratio1=1.0,
            fm4_ratio2=1.0,
            fm4_ratio3=1.0,
            fm4_ratio4=14.0,
            fm4_level1=0.8,
            fm4_level2=0.3,
            fm4_level3=0.55,
            fm4_level4=0.45,
            fm4_decay1=3.0,
            fm4_decay2=1.5,
            fm4_decay3=1.5,
            fm4_decay4=0.4,
            filter_type="lowpass",
            cutoff=8000.0,
            resonance=0.0,
            filter_env=0.0,
            key_track=0.3,
            attack=0.002,
            decay=2.5,
            sustain=0.2,
            release=0.6,
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            _fx("chorus", rate=0.5, depth=3.0, mix=0.35),
            _fx("reverb", decay=2.0, mix=0.22, size=0.7),
        ],
        how="4-operator FM with the 14:1 tine loud and ringing for 0.4 s, "
        "wide chorus, a room: the glassy FM bell piano of the 80s ballad",
    ),
    _recipe(
        "Funk Clav",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Funk Clav",
            osc1="wavetable",
            osc1_level=0.8,
            wt_table="pulse",
            wt_pos=2 / 3,
            filter_type="lowpass",
            cutoff=2500.0,
            resonance=0.3,
            filter_env=4000.0,
            filter_decay=0.12,
            filter_sustain=0.3,
            key_track=0.5,
            attack=0.001,
            decay=0.5,
            sustain=0.35,
            release=0.05,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("drive", amount=2.0, mix=0.5, mode=0.0),
        ],
        how="a thin pulse (the table's 15 % frame) under a resonant low-pass "
        "snapping open, short and a little driven: a funky clav-like stab, "
        "not a clavinet",
    ),
    _recipe(
        "House Organ",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "House Organ",
            osc1="fm4",
            osc1_level=0.95,
            # four sines added: the note and its octave held, the twelfth a
            # percussive flick, a click on top
            fm4_algo="additive",
            fm4_ratio1=1.0,
            fm4_ratio2=2.0,
            fm4_ratio3=3.0,
            fm4_ratio4=6.0,
            fm4_level1=0.9,
            fm4_level2=0.6,
            fm4_level3=0.6,
            fm4_level4=0.35,
            fm4_decay1=0.0,
            fm4_decay2=0.0,
            fm4_decay3=0.25,
            fm4_decay4=0.06,
            filter_type="lowpass",
            cutoff=6000.0,
            resonance=0.0,
            filter_env=0.0,
            attack=0.002,
            decay=0.3,
            sustain=0.75,
            release=0.08,
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            _fx("chorus", rate=0.3, depth=1.5, mix=0.2),
            _fx("reverb", decay=1.4, mix=0.15, size=0.6),
        ],
        how="four sines added (the note, its octave held; the twelfth a "
        "percussive flick and a click over them): the bright, punchy organ "
        "of 90s house piano-and-organ riffs",
    ),
    _recipe(
        "Drawbar Organ",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Drawbar Organ",
            osc1="wavetable",
            osc1_level=0.75,
            wt_table="organ",
            wt_pos=0.4,
            filter_type="lowpass",
            cutoff=7000.0,
            resonance=0.0,
            filter_env=0.0,
            attack=0.005,
            decay=0.1,
            sustain=1.0,
            release=0.06,
        ),
        [
            _fx("highpass", cutoff=60.0, q=0.7),
            _fx("plate", decay=1.2, tone=0.6, predelay=0.01, mix=0.15),
        ],
        how="the organ table (drawbar registrations as frames) held flat, on "
        "and off like a key contact, a short plate: a tonewheel-style "
        "drawbar organ, dry (the Rotary Organ is the one through the "
        "spinning speaker)",
    ),
    _recipe(
        "Harpsichord",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Harpsichord",
            osc1="pluck",
            osc1_level=0.7,
            pluck_damping=0.2,
            pluck_bright=1.0,
            osc2="pluck",
            osc2_octave=1,
            osc2_level=0.4,
            osc2_detune=2.0,
            filter_type="lowpass",
            cutoff=9000.0,
            resonance=0.0,
            filter_env=0.0,
            attack=0.001,
            decay=1.5,
            sustain=0.0,
            release=0.4,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("reverb", decay=1.2, mix=0.15, size=0.5),
        ],
        how="two bright plucked strings an octave apart (two choirs of "
        "strings, as the instrument's 8' and 4'), a hall's touch: "
        "harpsichord-ish, plucked, not hammered",
    ),
    _recipe(
        "Music Box",
        ["keys", "core"],
        "C5",
        CORE_BPM,
        _patch(
            "Music Box",
            osc1="fm4",
            osc1_level=0.9,
            # a struck steel tooth: four sines at its inharmonic partials,
            # the upper ones gone first
            fm4_algo="additive",
            fm4_ratio1=1.0,
            fm4_ratio2=3.0,
            fm4_ratio3=5.4,
            fm4_ratio4=8.9,
            fm4_level1=1.0,
            fm4_level2=0.3,
            fm4_level3=0.2,
            fm4_level4=0.15,
            fm4_decay1=1.8,
            fm4_decay2=0.6,
            fm4_decay3=0.25,
            fm4_decay4=0.08,
            filter_type="none",
            attack=0.001,
            decay=2.0,
            sustain=0.0,
            release=1.0,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("limiter", gain=8.0, ceiling=-3.0, release=0.08, lookahead=0.002),
            _fx("reverb", decay=2.0, mix=0.3, size=0.6),
        ],
        how="four sines at a struck steel tooth's partials (1, 3, 5.4, 8.9), "
        "the upper ones gone first, in a room: a music box",
    ),
    _recipe(
        "Celesta",
        ["keys", "core"],
        "C5",
        CORE_BPM,
        _patch(
            "Celesta",
            osc1="fm",
            osc1_level=0.8,
            fm_ratio=4.0,
            fm_index=1.5,
            fm_decay=0.3,
            osc2="sine",
            osc2_octave=1,
            osc2_level=0.2,
            osc2_detune=0.0,
            filter_type="none",
            attack=0.001,
            decay=1.8,
            sustain=0.0,
            release=0.8,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("plate", decay=2.0, tone=0.7, predelay=0.015, mix=0.25),
        ],
        how="a sine struck by a 4:1 modulator that fades in 0.3 s, its "
        "octave quietly over it, a bright plate: celesta-ish, a soft hammer "
        "on a steel bar",
    ),
    # mallets and bells
    _recipe(
        "Marimba",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Marimba",
            osc1="fm4",
            osc1_level=0.95,
            # a tuned wooden bar: the note, its 4th partial (tuned so), a
            # mallet's knock
            fm4_algo="additive",
            fm4_ratio1=1.0,
            fm4_ratio2=4.0,
            fm4_ratio3=10.0,
            fm4_ratio4=1.0,
            fm4_level1=1.0,
            fm4_level2=0.4,
            fm4_level3=0.1,
            fm4_level4=0.0,
            fm4_decay1=0.8,
            fm4_decay2=0.12,
            fm4_decay3=0.03,
            fm4_decay4=0.0,
            filter_type="lowpass",
            cutoff=4000.0,
            resonance=0.0,
            filter_env=0.0,
            attack=0.001,
            decay=0.9,
            sustain=0.0,
            release=0.3,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=100.0, q=0.7),
            _fx("limiter", gain=8.0, ceiling=-3.0, release=0.08, lookahead=0.002),
            _fx("reverb", decay=1.0, mix=0.15, size=0.5),
        ],
        how="a tuned wooden bar's partials as sines — the note, the fourth "
        "partial a marimba is tuned to, a soft mallet's knock — dying fast: "
        "marimba-ish",
    ),
    _recipe(
        "Vibraphone",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Vibraphone",
            osc1="fm4",
            osc1_level=0.95,
            fm4_algo="additive",
            fm4_ratio1=1.0,
            fm4_ratio2=4.0,
            fm4_ratio3=10.0,
            fm4_ratio4=1.0,
            fm4_level1=1.0,
            fm4_level2=0.3,
            fm4_level3=0.08,
            fm4_level4=0.0,
            fm4_decay1=3.0,
            fm4_decay2=0.6,
            fm4_decay3=0.05,
            fm4_decay4=0.0,
            filter_type="none",
            attack=0.001,
            decay=3.0,
            sustain=0.0,
            release=1.2,
            env_curve=2.5,
            # the motor's turning fans
            tremolo_rate=5.0,
            tremolo_depth=0.3,
        ),
        [
            _fx("highpass", cutoff=100.0, q=0.7),
            _fx("limiter", gain=10.0, ceiling=-3.0, release=0.08, lookahead=0.002),
            _fx("reverb", decay=1.8, mix=0.2, size=0.6),
        ],
        how="a metal bar's note and fourth partial, ringing for seconds, a "
        "5 Hz tremolo as the resonators' fans turn: vibraphone-ish",
    ),
    _recipe(
        "Glockenspiel",
        ["keys", "core"],
        "C5",
        CORE_BPM,
        _patch(
            "Glockenspiel",
            osc1="fm4",
            osc1_level=0.9,
            # a free steel bar's partials: 1, 2.76, 5.40, 8.93
            fm4_algo="additive",
            fm4_ratio1=1.0,
            fm4_ratio2=2.76,
            fm4_ratio3=5.4,
            fm4_ratio4=8.93,
            fm4_level1=1.0,
            fm4_level2=0.5,
            fm4_level3=0.3,
            fm4_level4=0.15,
            fm4_decay1=2.0,
            fm4_decay2=1.0,
            fm4_decay3=0.5,
            fm4_decay4=0.2,
            filter_type="none",
            attack=0.001,
            decay=2.2,
            sustain=0.0,
            release=1.0,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("plate", decay=1.5, tone=0.7, predelay=0.01, mix=0.2),
        ],
        how="four sines at a free steel bar's partials (1, 2.76, 5.40, 8.93), "
        "the upper ones dying first, a plate: glockenspiel-ish",
    ),
    _recipe(
        "Kalimba",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Kalimba",
            osc1="fm4",
            osc1_level=0.95,
            # a plucked tine on a box: the note, a woody octave, the tine's
            # high partial, the thumb's click
            fm4_algo="additive",
            fm4_ratio1=1.0,
            fm4_ratio2=5.9,
            fm4_ratio3=2.0,
            fm4_ratio4=12.0,
            fm4_level1=1.0,
            fm4_level2=0.35,
            fm4_level3=0.15,
            fm4_level4=0.1,
            fm4_decay1=0.9,
            fm4_decay2=0.08,
            fm4_decay3=0.3,
            fm4_decay4=0.015,
            filter_type="lowpass",
            cutoff=6000.0,
            resonance=0.0,
            filter_env=0.0,
            attack=0.001,
            decay=1.0,
            sustain=0.0,
            release=0.4,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            _fx("limiter", gain=8.0, ceiling=-3.0, release=0.08, lookahead=0.002),
            _fx("reverb", decay=1.0, mix=0.15, size=0.4),
        ],
        how="a plucked metal tine on a box as sines (the note, a woody "
        "octave, the tine's high partial gone in 80 ms, a thumb's click): "
        "kalimba-ish",
    ),
    _recipe(
        "Steel Drum",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Steel Drum",
            osc1="fm4",
            osc1_level=0.9,
            # two pairs: the note made metallic by its octave, and the
            # octave by its twelfth — the pan's tuned overtones
            fm4_algo="pairs",
            fm4_ratio1=1.0,
            fm4_ratio2=2.0,
            fm4_ratio3=2.0,
            fm4_ratio4=3.0,
            fm4_level1=0.9,
            fm4_level2=0.35,
            fm4_level3=0.45,
            fm4_level4=0.2,
            fm4_decay1=1.2,
            fm4_decay2=0.4,
            fm4_decay3=0.6,
            fm4_decay4=0.15,
            filter_type="lowpass",
            cutoff=5000.0,
            resonance=0.0,
            filter_env=0.0,
            attack=0.002,
            decay=1.2,
            sustain=0.0,
            release=0.5,
            env_curve=2.5,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("plate", decay=1.4, tone=0.6, predelay=0.01, mix=0.2),
        ],
        how="4-operator FM as two pairs: the note made metallic by its "
        "octave, the octave by its twelfth, a plate: steel-pan-ish",
    ),
    _recipe(
        "Tubular Bells",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Tubular Bells",
            osc1="fm4",
            osc1_level=0.7,
            fm4_algo="pairs",
            fm4_ratio1=1.0,
            fm4_ratio2=3.5,
            fm4_ratio3=2.0,
            fm4_ratio4=5.6,
            fm4_level1=0.9,
            fm4_level2=0.3,
            fm4_level3=0.45,
            fm4_level4=0.2,
            fm4_decay1=4.0,
            fm4_decay2=2.0,
            fm4_decay3=2.5,
            fm4_decay4=0.6,
            # the bell table under it, its own inharmonic frame
            osc2="wavetable",
            osc2_octave=0,
            osc2_level=0.35,
            osc2_detune=0.0,
            wt_table="bell",
            wt_pos=0.5,
            filter_type="lowpass",
            cutoff=6000.0,
            resonance=0.0,
            filter_env=0.0,
            attack=0.001,
            decay=3.0,
            sustain=0.0,
            release=2.5,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("reverb", decay=3.5, mix=0.3, size=0.9),
        ],
        how="4-operator FM at inharmonic ratios (3.5:1, 5.6:2) with the bell "
        "table under it, ringing for seconds in a big room: tubular-bells-"
        "ish, the orchestra's chimes",
    ),
    _recipe(
        "Xylophone",
        ["keys", "core"],
        "C5",
        CORE_BPM,
        _patch(
            "Xylophone",
            osc1="fm4",
            osc1_level=0.95,
            # a hard wooden bar tuned to its twelfth
            fm4_algo="additive",
            fm4_ratio1=1.0,
            fm4_ratio2=3.0,
            fm4_ratio3=6.0,
            fm4_ratio4=1.0,
            fm4_level1=1.0,
            fm4_level2=0.35,
            fm4_level3=0.1,
            fm4_level4=0.0,
            fm4_decay1=0.35,
            fm4_decay2=0.12,
            fm4_decay3=0.03,
            fm4_decay4=0.0,
            filter_type="none",
            attack=0.001,
            decay=0.4,
            sustain=0.0,
            release=0.2,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("limiter", gain=8.0, ceiling=-3.0, release=0.08, lookahead=0.002),
            _fx("reverb", decay=1.0, mix=0.15, size=0.5),
        ],
        how="a hard wooden bar as sines (the note, the twelfth it is tuned "
        "to, a hard mallet's knock), short and dry: xylophone-ish",
    ),
    # brass and orchestral
    _recipe(
        "Synth Brass",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Synth Brass",
            osc1="saw",
            osc1_level=0.45,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.4,
            osc2_detune=10.0,
            filter_type="lowpass",
            cutoff=800.0,
            resonance=0.15,
            filter_env=3000.0,
            filter_attack=0.08,
            filter_decay=0.5,
            filter_sustain=0.4,
            key_track=0.5,
            attack=0.05,
            decay=0.4,
            sustain=0.85,
            release=0.3,
            # the old polysynth's voices never quite agree
            drift=0.3,
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            _fx("chorus", rate=0.4, depth=2.0, mix=0.2),
            _fx("reverb", decay=1.8, mix=0.2, size=0.7),
        ],
        how="two saws apart, the low-pass swelling open 80 ms into each "
        "chord, a little drift between voices, chorused: the big "
        "polysynth brass of 80s rock and pop (the OB-style brass)",
    ),
    _recipe(
        "Brass Stab",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Brass Stab",
            osc1="supersaw",
            osc1_level=0.5,
            unison=3,
            unison_detune=12.0,
            unison_spread=0.6,
            filter_type="lowpass",
            cutoff=900.0,
            resonance=0.2,
            filter_env=4500.0,
            filter_attack=0.005,
            filter_decay=0.2,
            filter_sustain=0.2,
            key_track=0.5,
            attack=0.004,
            decay=0.35,
            sustain=0.0,
            release=0.15,
            drive=1.6,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("reverb", decay=1.2, mix=0.2, size=0.6),
        ],
        how="a driven supersaw, the filter snapping wide open and shut, "
        "gone in a third of a second: the short brass hit a house or funk "
        "track punctuates with",
    ),
    _recipe(
        "Pizzicato",
        ["lead", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Pizzicato",
            osc1="pluck",
            osc1_level=1.0,
            pluck_damping=0.55,
            pluck_bright=0.6,
            # a section, not one player
            unison=3,
            unison_detune=6.0,
            unison_spread=0.7,
            filter_type="lowpass",
            cutoff=3500.0,
            resonance=0.0,
            filter_env=0.0,
            key_track=0.4,
            attack=0.002,
            decay=0.3,
            sustain=0.0,
            release=0.15,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("reverb", decay=2.2, mix=0.25, size=0.8),
        ],
        how="three plucked strings a few cents apart (a section), damped and "
        "short, in a hall: pizzicato-ish strings",
    ),
    _recipe(
        "Orchestra Hit",
        ["keys", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Orchestra Hit",
            osc1="supersaw",
            osc1_level=0.55,
            unison=5,
            unison_detune=16.0,
            unison_spread=0.8,
            osc2="square",
            osc2_octave=1,
            osc2_level=0.3,
            osc2_detune=0.0,
            filter_type="lowpass",
            cutoff=1500.0,
            resonance=0.1,
            filter_env=5000.0,
            filter_attack=0.002,
            filter_decay=0.15,
            filter_sustain=0.2,
            key_track=0.5,
            attack=0.002,
            decay=0.5,
            sustain=0.0,
            release=0.4,
            pitch_env=-1.0,
            pitch_env_decay=0.05,
            drive=1.5,
        ),
        [
            _fx("highpass", cutoff=180.0, q=0.7),
            _fx("reverb", decay=2.5, mix=0.3, size=0.9),
        ],
        how="a wide supersaw with a square an octave over, the filter "
        "snapping open, a drop into the note, in a hall: "
        "the 80s orchestra hit, synthesised (no sample of an orchestra)",
    ),
    _recipe(
        "Synth Strings",
        ["pad", "core"],
        "C3",
        CORE_BPM,
        _patch(
            "Synth Strings",
            osc1="saw",
            osc1_level=0.45,
            unison=3,
            unison_detune=14.0,
            unison_spread=0.8,
            osc2="saw",
            osc2_octave=1,
            osc2_level=0.2,
            osc2_detune=5.0,
            filter_type="lowpass",
            cutoff=2600.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.4,
            attack=0.35,
            decay=0.5,
            sustain=0.9,
            release=0.9,
            vibrato_rate=5.0,
            vibrato_depth=0.06,
            vibrato_delay=0.5,
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            _fx("chorus", rate=0.35, depth=3.0, mix=0.35),
            _fx("reverb", decay=2.5, mix=0.28, size=0.9),
        ],
        how="three saws apart and a quiet saw an octave over, bowed in over a "
        "third of a second, a late vibrato, chorused, in a hall: a string "
        "section as a synth plays one",
    ),
    _recipe(
        "Synth Choir",
        ["pad", "core"],
        "C3",
        CORE_BPM,
        _patch(
            "Synth Choir",
            osc1="wavetable",
            osc1_level=0.6,
            wt_table="formant",
            wt_pos=0.0,
            unison=3,
            unison_detune=10.0,
            unison_spread=0.8,
            filter_type="lowpass",
            cutoff=3500.0,
            resonance=0.05,
            filter_env=0.0,
            formant_amount=0.5,
            formant_vowel=0.0,
            attack=0.4,
            decay=0.6,
            sustain=0.9,
            release=1.2,
            vibrato_rate=5.0,
            vibrato_depth=0.08,
            vibrato_delay=0.3,
        ),
        [
            _fx("highpass", cutoff=140.0, q=0.7),
            _fx("chorus", rate=0.3, depth=3.0, mix=0.35),
            _fx("reverb", decay=3.5, mix=0.35, size=1.0),
        ],
        how="the formant table on its 'a', three voices apart, sung again "
        "through the vowel filter, a slow vibrato, a big room: an 'aah' "
        "choir as a synth sings one, not a choir",
    ),
    _recipe(
        "Soft Horns",
        ["pad", "core"],
        "C3",
        CORE_BPM,
        _patch(
            "Soft Horns",
            osc1="triangle",
            osc1_level=0.7,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.25,
            osc2_detune=6.0,
            filter_type="lowpass",
            cutoff=700.0,
            resonance=0.1,
            filter_env=900.0,
            filter_attack=0.25,
            filter_decay=0.6,
            filter_sustain=0.6,
            key_track=0.5,
            attack=0.15,
            decay=0.5,
            sustain=0.85,
            release=0.5,
            vibrato_rate=4.5,
            vibrato_depth=0.05,
            vibrato_delay=0.5,
        ),
        [
            _fx("highpass", cutoff=100.0, q=0.7),
            _fx("reverb", decay=2.5, mix=0.3, size=0.9),
        ],
        how="a triangle and a quiet saw under a dark low-pass that swells "
        "open with each chord, a slow vibrato, a hall: a soft horn section "
        "(French-horn-ish), not brass you could mistake for a recording",
    ),
    # plucks
    _recipe(
        "Harp",
        ["lead", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Harp",
            osc1="pluck",
            osc1_level=0.8,
            pluck_damping=0.35,
            pluck_bright=0.55,
            osc2="sine",
            osc2_octave=0,
            osc2_level=0.2,
            osc2_detune=0.0,
            filter_type="lowpass",
            cutoff=5000.0,
            resonance=0.0,
            filter_env=0.0,
            attack=0.002,
            decay=2.5,
            sustain=0.0,
            release=1.5,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            _fx("reverb", decay=3.0, mix=0.3, size=0.9),
        ],
        how="a softly plucked string with a sine for its body, ringing on, "
        "in a hall: harp-ish",
    ),
    _recipe(
        "Koto",
        ["lead", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Koto",
            osc1="pluck",
            osc1_level=1.0,
            pluck_damping=0.4,
            pluck_bright=0.9,
            # the string pressed behind the bridge: a little sharp, settling
            pitch_env=0.6,
            pitch_env_decay=0.04,
            filter_type="lowpass",
            cutoff=6000.0,
            resonance=0.0,
            filter_env=0.0,
            attack=0.001,
            decay=1.5,
            sustain=0.0,
            release=0.6,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("plate", decay=1.5, tone=0.6, predelay=0.01, mix=0.2),
        ],
        how="a bright plucked string that starts a little sharp and settles, "
        "a plate: koto-ish",
    ),
    _recipe(
        "Sitar Pluck",
        ["lead", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Sitar Pluck",
            osc1="pluck",
            osc1_level=0.85,
            pluck_damping=0.25,
            pluck_bright=1.0,
            # the curved bridge's buzz: a resonant band sweeping down
            filter_type="bandpass",
            cutoff=1800.0,
            resonance=0.6,
            filter_env=-1200.0,
            filter_attack=0.002,
            filter_decay=0.4,
            filter_sustain=0.0,
            key_track=0.6,
            attack=0.001,
            decay=2.0,
            sustain=0.0,
            release=0.8,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("limiter", gain=14.0, ceiling=-3.0, release=0.08, lookahead=0.002),
            _fx("reverb", decay=2.0, mix=0.2, size=0.7),
        ],
        how="a bright plucked string through a resonant band-pass sweeping "
        "down as it rings (the buzz of a curved bridge): sitar-ish, no "
        "sympathetic strings",
    ),
    _recipe(
        "Bell Pluck",
        ["lead", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Bell Pluck",
            osc1="fm",
            osc1_level=0.8,
            fm_ratio=3.5,
            fm_index=3.0,
            fm_decay=0.25,
            osc2="sine",
            osc2_octave=1,
            osc2_level=0.2,
            osc2_detune=0.0,
            filter_type="lowpass",
            cutoff=7000.0,
            resonance=0.0,
            filter_env=0.0,
            attack=0.001,
            decay=0.5,
            sustain=0.0,
            release=0.3,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            *chain_preset("dotted_ping_pong"),
            _fx("reverb", decay=1.8, mix=0.2, size=0.7),
        ],
        how="2-operator FM at 3.5:1 (a bell's inharmonic ring) gone in half "
        "a second, its octave over it, a dotted ping-pong echo: the bell "
        "pluck of an arpeggio",
    ),
    _recipe(
        "Synth Pluck",
        ["lead", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Synth Pluck",
            osc1="saw",
            osc1_level=0.38,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.28,
            osc2_detune=9.0,
            filter_type="lowpass",
            cutoff=400.0,
            resonance=0.25,
            filter_env=5000.0,
            filter_attack=0.001,
            filter_decay=0.15,
            filter_sustain=0.0,
            key_track=0.5,
            attack=0.001,
            decay=0.35,
            sustain=0.0,
            release=0.2,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("delay", feedback=0.35, mix=0.2, ping_pong=1.0, sync=3.0),
            _fx("reverb", decay=1.5, mix=0.2, size=0.6),
        ],
        how="two saws apart under a low-pass that snaps open and shut in a "
        "sixth of a second, a dotted echo: the plain synth pluck every "
        "dance track arpeggiates",
    ),
    _recipe(
        "Steel String Pluck",
        ["lead", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Steel String Pluck",
            osc1="pluck",
            osc1_level=0.8,
            pluck_damping=0.15,
            pluck_bright=0.85,
            unison=2,
            unison_detune=3.0,
            unison_spread=0.4,
            filter_type="lowpass",
            cutoff=7000.0,
            resonance=0.0,
            filter_env=0.0,
            attack=0.001,
            decay=2.0,
            sustain=0.0,
            release=0.8,
            env_curve=3.0,
        ),
        [
            _fx("highpass", cutoff=100.0, q=0.7),
            _fx("chorus", rate=0.3, depth=1.5, mix=0.15),
            _fx("plate", decay=1.4, tone=0.6, predelay=0.01, mix=0.18),
        ],
        how="a bright, barely damped plucked string doubled a few cents "
        "apart, ringing, a touch of chorus and plate: steel-string-guitar-"
        "ish, not a guitar (no body, no fret noise)",
    ),
    # pads
    _recipe(
        "Warm Analog Pad",
        ["pad", "core"],
        "C3",
        CORE_BPM,
        _patch(
            "Warm Analog Pad",
            osc1="saw",
            osc1_level=0.45,
            osc2="saw",
            osc2_octave=0,
            osc2_level=0.4,
            osc2_detune=9.0,
            filter_type="lowpass",
            cutoff=1400.0,
            resonance=0.15,
            filter_env=600.0,
            filter_attack=0.6,
            filter_decay=1.0,
            filter_sustain=0.6,
            key_track=0.4,
            attack=0.6,
            decay=1.0,
            sustain=0.85,
            release=1.5,
            # every note its own wander: an old polysynth's voices
            drift=0.6,
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            _fx("chorus", rate=0.3, depth=3.0, mix=0.35),
            _fx("reverb", decay=3.0, mix=0.3, size=0.9),
        ],
        how="two saws apart under a warm low-pass that opens with the swell, "
        "drifting note to note as an old polysynth's voices do, chorused, a "
        "hall: the warm analog pad",
    ),
    _recipe(
        "Glass Pad",
        ["pad", "core"],
        "C3",
        CORE_BPM,
        _patch(
            "Glass Pad",
            osc1="wavetable",
            osc1_level=0.55,
            wt_table="bell",
            wt_pos=0.3,
            osc2="sine",
            osc2_octave=1,
            osc2_level=0.3,
            osc2_detune=4.0,
            filter_type="lowpass",
            cutoff=6000.0,
            resonance=0.05,
            filter_env=0.0,
            attack=0.5,
            decay=1.0,
            sustain=0.85,
            release=2.0,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("chorus", rate=0.25, depth=3.0, mix=0.4),
            _fx("delay", feedback=0.35, mix=0.2, ping_pong=1.0, sync=3.0),
            _fx("reverb", decay=4.0, mix=0.35, size=1.0),
        ],
        how="the bell table's inharmonic frame and a sine an octave over it, "
        "slow in and out, chorused, echoed side to side into a long room: a "
        "glassy, shimmering pad",
    ),
    _recipe(
        "PWM Pad",
        ["pad", "core"],
        "C3",
        CORE_BPM,
        _patch(
            "PWM Pad",
            osc1="square",
            osc1_level=0.5,
            pwm_depth=0.6,
            pwm_rate=0.4,
            osc2="square",
            osc2_octave=0,
            osc2_level=0.4,
            osc2_detune=8.0,
            filter_type="lowpass",
            cutoff=1800.0,
            resonance=0.1,
            filter_env=0.0,
            key_track=0.4,
            attack=0.4,
            decay=1.0,
            sustain=0.9,
            release=1.4,
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            _fx("chorus", rate=0.3, depth=3.0, mix=0.35),
            _fx("reverb", decay=3.0, mix=0.3, size=0.9),
        ],
        how="two squares apart, the pulse width breathing slowly, chorused, "
        "a hall: the hollow, moving PWM string pad",
    ),
    _recipe(
        "Sweep Pad",
        ["pad", "core"],
        "C3",
        CORE_BPM,
        _patch(
            "Sweep Pad",
            osc1="saw",
            osc1_level=0.45,
            unison=3,
            unison_detune=12.0,
            unison_spread=0.8,
            filter_type="ladder",
            cutoff=700.0,
            resonance=0.4,
            filter_env=0.0,
            # a resonant sweep once every ten seconds
            filter_lfo_depth=1200.0,
            filter_lfo_rate=0.1,
            filter_lfo_shape="triangle",
            key_track=0.4,
            attack=0.5,
            decay=1.0,
            sustain=0.9,
            release=1.5,
        ),
        [
            _fx("highpass", cutoff=120.0, q=0.7),
            _fx("phaser", rate=0.15, depth=0.6, mix=0.3),
            _fx("reverb", decay=3.0, mix=0.3, size=0.9),
        ],
        how="three saws apart through a resonant ladder low-pass that sweeps "
        "up and back once every ten seconds, a slow phaser, a hall: the "
        "sweeping pad",
    ),
    _recipe(
        "Soft Pad",
        ["pad", "core"],
        "C3",
        CORE_BPM,
        _patch(
            "Soft Pad",
            osc1="triangle",
            osc1_level=0.7,
            osc2="sine",
            osc2_octave=1,
            osc2_level=0.3,
            osc2_detune=5.0,
            filter_type="lowpass",
            cutoff=1200.0,
            resonance=0.05,
            filter_env=0.0,
            attack=0.8,
            decay=1.0,
            sustain=0.9,
            release=2.0,
        ),
        [
            _fx("highpass", cutoff=100.0, q=0.7),
            _fx("chorus", rate=0.25, depth=3.0, mix=0.35),
            _fx("reverb", decay=3.5, mix=0.35, size=1.0),
        ],
        how="a triangle and a sine an octave over, slow in and slow out, "
        "chorused, a long room: the soft, round pad under a ballad",
    ),
    # fx
    _recipe(
        "Glitch",
        ["fx", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Glitch",
            osc1="square",
            osc1_level=0.4,
            filter_type="lowpass",
            cutoff=4000.0,
            resonance=0.3,
            filter_env=0.0,
            attack=0.002,
            decay=0.2,
            sustain=0.8,
            release=0.1,
            # random steps of pitch and tone on the sixteenth triplet
            lfo_shape="noise",
            lfo_sync="1/8T",
            mod1_src="lfo",
            mod1_dst="pitch",
            mod1_amt=0.5,
            mod2_src="lfo",
            mod2_dst="cutoff",
            mod2_amt=0.6,
        ),
        [
            _gate("xx.x..x.xxx..x.x"),
            _fx("bitcrush", bits=6.0, downsample=6.0),
            _fx("highpass", cutoff=150.0, q=0.7),
        ],
        how="a square stepping to random pitches and tones on the eighth "
        "triplet, stuttered by an uneven sixteenth gate, crushed to six "
        "bits: a glitch texture",
    ),
    _recipe(
        "Sci-fi Blips",
        ["fx", "core"],
        "C5",
        CORE_BPM,
        _patch(
            "Sci-fi Blips",
            osc1="sine",
            osc1_level=0.8,
            filter_type="none",
            attack=0.002,
            decay=0.2,
            sustain=0.8,
            release=0.1,
            # a computer's random tones, one a sixteenth
            lfo_shape="noise",
            lfo_sync="1/16",
            mod1_src="lfo",
            mod1_dst="pitch",
            mod1_amt=0.7,
        ),
        [
            _gate("x.x.x.x.x.x.x.x."),
            *chain_preset("dotted_ping_pong"),
        ],
        how="a sine jumping to a random pitch every sixteenth, each one a "
        "blip (a gate on the sixteenth), echoed side to side: the 70s "
        "film computer",
    ),
    _recipe(
        "Alarm",
        ["fx", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Alarm",
            osc1="square",
            osc1_level=0.6,
            filter_type="bandpass",
            cutoff=1500.0,
            resonance=0.3,
            filter_env=0.0,
            key_track=0.6,
            attack=0.002,
            decay=0.2,
            sustain=1.0,
            release=0.1,
            # two tones a fourth apart, twice a second
            lfo_shape="square",
            mod_lfo_rate=2.0,
            mod1_src="lfo",
            mod1_dst="pitch",
            mod1_amt=0.21,
        ),
        [
            _fx("drive", amount=2.0, mix=0.5, mode=1.0),
            _fx("highpass", cutoff=200.0, q=0.7),
            _fx("reverb", decay=1.2, mix=0.15, size=0.6),
        ],
        how="a square switching between two tones a fourth apart twice a "
        "second through a band-pass, driven: a two-tone alarm",
    ),
    _recipe(
        "Ambience Bed",
        ["fx", "core"],
        "C3",
        CORE_BPM,
        _patch(
            "Ambience Bed",
            osc1="triangle",
            osc1_level=0.5,
            noise_level=1.0,
            noise_color="pink",
            filter_type="bandpass",
            cutoff=900.0,
            resonance=0.3,
            filter_env=0.0,
            filter_lfo_depth=500.0,
            filter_lfo_rate=0.08,
            key_track=0.0,
            attack=1.0,
            decay=1.0,
            sustain=0.9,
            release=2.0,
        ),
        [
            _fx("highpass", cutoff=150.0, q=0.7),
            _fx("limiter", gain=18.0, ceiling=-3.0, release=0.3, lookahead=0.002),
            _fx("reverb", decay=6.0, mix=0.5, size=1.0),
            _fx("width", amount=1.5),
        ],
        how="pink noise and a quiet triangle through a band-pass drifting "
        "over ten seconds, drowned in a six-second room, wide: a bed of "
        "air and room tone under a scene",
    ),
    _recipe(
        "Wind",
        ["fx", "core"],
        "C4",
        CORE_BPM,
        _patch(
            "Wind",
            osc1="noise",
            osc1_level=0.9,
            noise_color="white",
            filter_type="bandpass",
            cutoff=1200.0,
            resonance=0.25,
            filter_env=0.0,
            filter_lfo_depth=600.0,
            filter_lfo_rate=0.15,
            filter_lfo_shape="triangle",
            key_track=0.0,
            attack=0.8,
            decay=1.0,
            sustain=0.9,
            release=1.5,
        ),
        [
            _fx("limiter", gain=12.0, ceiling=-3.0, release=0.3, lookahead=0.002),
            _fx("reverb", decay=2.5, mix=0.3, size=0.9),
            _fx("width", amount=1.4),
        ],
        how="noise through a band-pass that gusts up and down every "
        "seven seconds, a room, wide: wind",
    ),
]

_BY_NAME = {e["name"]: e for e in RECIPES}
assert len(_BY_NAME) == len(RECIPES)


def _load_meta() -> dict:
    if not META_PATH.exists():
        return {}
    return json.loads(META_PATH.read_text(encoding="utf-8"))


_META = _load_meta()


def get_recipe(name: str) -> dict | None:
    return _BY_NAME.get(name)


def recipe_patch(entry: dict) -> Patch:
    return Patch.from_dict(entry["patch"])


def recipe_chain(entry: dict) -> dict:
    """The format-2 extras a Use loads, conformed exactly as a v2 preset
    file's are (`state.preset_from_json`). `layers` None when the recipe
    brings none: its FX and curves land, the session's lanes stay."""
    from . import schema

    raw = {
        "patch": entry["patch"],
        "layers": entry["layers"] or [],
        "main_fx": entry["main_fx"],
        "curves": entry["curves"],
    }
    chain = schema.conform(copy.deepcopy(raw), schema.nodes().PRESET_V2)
    chain.pop("patch", None)
    if not entry["layers"]:
        chain["layers"] = None
    return chain


def preset_json(entry: dict) -> dict:
    """The recipe as a format-2 preset file would hold it."""
    return {
        "format": 2,
        "patch": entry["patch"],
        "layers": entry["layers"] or [],
        "main_fx": entry["main_fx"],
        "curves": entry["curves"],
    }


def cards() -> list[dict]:
    """The Recipes shelf, dark -> bright: each recipe with its measured
    numbers (a recipe missing from the meta file draws with none)."""
    out = []
    for e in RECIPES:
        m = _META.get(e["name"], {})
        out.append(
            {
                "name": e["name"],
                "tags": e["tags"],
                "how": e["how"],
                "layered": bool(e["layers"]),
                "fx": [u["kind"] for u in e["main_fx"]],
                "spectrum": m.get("spectrum", [0.0] * 24),
                "centroid": m.get("centroid", 0),
                "brightness": m.get("brightness", 0.0),
            }
        )
    return sorted(out, key=lambda c: c["brightness"])


def tags() -> list[str]:
    return sorted({t for e in RECIPES for t in e["tags"]})


# --- the core library's shelf --------------------------------------------------

# The Library's Core shelf, a category at a time: the
# core recipes (tag `core`) and, where a genre recipe or a factory sound
# already IS the thing, that sound — listed, never copied ("factory:" names
# a factory sound). Mallets and orchestral are categories the generated
# families have no word for.
CORE_SHELF: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "bass",
        (
            "Ladder Lead Bass",
            "Synth-Pop Bass",
            "FM Slap Bass",
            "Rubber Bass",
            "Upright Bass",
            "808 Long",  # the long 808 slide (hip-hop)
            "factory:Acid Alley",  # the acid bass
            "Wobble Bass",  # dubstep
            "Reese Bass",  # melodic techno
            "Slap Pluck Bass",  # the pluck bass (electro)
            "Sine Sub",  # the sub sine (garage)
            "Triangle Bass",  # chiptune
        ),
    ),
    (
        "lead",
        (
            "Brass Lead",
            "Portamento Whistle",
            "Soft Sine Lead",
            "Theremin Lead",
            "PWM Lead",
            "Sync Screech",  # the hard-sync scream (hardstyle)
            "Screech Lead",  # dubstep
            "Square Lead",  # the chip square (chiptune)
            "Pulse Lead",  # chiptune
            "Bright Saw Lead",  # the saw lead (synthwave)
            "Anthem Supersaw",  # trance
            "Airy Flute Lead",  # hip-hop
        ),
    ),
    (
        "keys",
        (
            "Tine EP",
            "Reed EP",
            "FM Bell EP",
            "Funk Clav",
            "House Organ",
            "Drawbar Organ",
            "Harpsichord",
            "Music Box",
            "Celesta",
            "Dusty Keys",  # the tape-worn e-piano (lo-fi)
            "Rotary Organ",  # sixties
            "Felt Piano Keys",  # garage
        ),
    ),
    (
        "mallets",
        (
            "Marimba",
            "Vibraphone",
            "Glockenspiel",
            "Kalimba",
            "Steel Drum",
            "Tubular Bells",
            "Xylophone",
            "Glass Bell Texture",  # ambient
            "Dark Bell Keys",  # hip-hop
        ),
    ),
    (
        "orchestral",
        (
            "Synth Brass",
            "Brass Stab",
            "Pizzicato",
            "Orchestra Hit",
            "Synth Strings",
            "Synth Choir",
            "Soft Horns",
            "Poly Brass Stab",  # synthwave
            "Lush Strings",  # synthwave
            "String Machine",  # house
            "Cathedral Choir Pad",  # electro
        ),
    ),
    (
        "pluck",
        (
            "Harp",
            "Koto",
            "Sitar Pluck",
            "Bell Pluck",
            "Synth Pluck",
            "Steel String Pluck",
            "factory:Nylon Pluck",  # the nylon string
            "Muted Pluck",  # lo-fi
        ),
    ),
    (
        "pad",
        (
            "Warm Analog Pad",
            "Glass Pad",
            "PWM Pad",
            "Sweep Pad",
            "Soft Pad",
            "Scanning Pad",  # the evolving scan (ambient)
            "Vowel Drift Pad",  # the vowel pad (ambient)
            "Deep Drone",  # the dark drone (ambient)
            "Poly Chorus Pad",  # synthwave
        ),
    ),
    (
        "fx",
        (
            "Glitch",
            "Sci-fi Blips",
            "Alarm",
            "Ambience Bed",
            "Wind",
            "factory:Laser Zap",  # the laser
            "Rising Air",  # the riser (melodic techno)
            "Noise Downlifter",  # drops
            "Impact",  # drops
            "Reverse Crash",  # drops
        ),
    ),
)
# where a factory sound on the shelf is measured (a recipe has its own pitch)
CORE_PITCH = {"bass": "A1", "pad": "C3"}
FACTORY_REF = "factory:"


def core_factory() -> list[tuple[str, str]]:
    """[(factory name, category)]: the factory sounds the shelf lists."""
    return [
        (ref[len(FACTORY_REF) :], cat)
        for cat, refs in CORE_SHELF
        for ref in refs
        if ref.startswith(FACTORY_REF)
    ]


def core_cards() -> list[tuple[str, list[dict]]]:
    """The Core shelf: [(category, cards dark -> bright)], each card as
    `cards()` draws a recipe plus `kind` (the `sounds.SOURCES` it is found
    in) and "core" among its tags, for the filter (the card adds the
    category, as a family's card adds its family)."""
    from .factory import FACTORY

    by_recipe = {c["name"]: c for c in cards()}
    by_factory = {e["name"]: e for e in FACTORY}
    out = []
    for cat, refs in CORE_SHELF:
        shelf = []
        for ref in refs:
            if ref.startswith(FACTORY_REF):
                e, m = by_factory[ref[len(FACTORY_REF) :]], _META.get(ref, {})
                card = {
                    "name": e["name"],
                    "tags": e.get("tags", []),
                    "how": "",
                    "layered": False,
                    "fx": [],
                    "spectrum": m.get("spectrum", e.get("spectrum", [0.0] * 24)),
                    "centroid": m.get("centroid", 0),
                    "brightness": m.get("brightness", 0.0),
                    "kind": "factory",
                }
            else:
                card = {**by_recipe[ref], "kind": "recipes"}
            # the card adds its shelf's category itself (the template's family)
            card["tags"] = list(dict.fromkeys(["core", *card["tags"]]))
            shelf.append(card)
        out.append((cat, sorted(shelf, key=lambda c: c["centroid"])))
    return out


# --- measuring (the card numbers, the drift check) ---------------------------


def _lane_audio(
    patch: Patch, pitch: str, dur: float, fx: list, arr: Arrangement, curves
) -> np.ndarray:
    p = at_bpm(patch, arr.bpm)
    if curves:
        m = n_samples(dur, SR)
        tweaks = {
            k: a
            for k, pts in curves.items()
            if k in CURVABLE and (a := curve_array(pts, k, m)) is not None
        }
        if tweaks:
            p = p.tweak(**tweaks)
    audio = to_stereo(np.asarray(Synth(p).note(pitch, dur), dtype=np.float64))
    return fx_chain(audio, build_chain(fx, arr)) if fx else audio


def stack_audio(entry: dict) -> np.ndarray:
    """One note of the whole recipe at its measuring pitch and tempo: the
    main lane with its FX and curves drawn across the note, each layer at
    its follow octave with its own FX, summed at its gain."""
    arr = Arrangement(bpm=entry["bpm"])
    main = recipe_patch(entry)
    dur = note_duration(main)
    for lay in entry["layers"] or []:
        dur = max(dur, note_duration(Patch.from_dict(lay["patch"])))
    parts = [
        _lane_audio(main, entry["pitch"], dur, entry["main_fx"], arr, entry["curves"])
    ]
    for lay in entry["layers"] or []:
        pitch = name_of(midi(entry["pitch"]) + 12 * int(lay["follow"]["octave"]))
        audio = _lane_audio(
            Patch.from_dict(lay["patch"]), pitch, dur, lay["fx"], arr, None
        )
        parts.append(float(lay["gain"]) * audio)
    n = max(len(a) for a in parts)
    out = np.zeros((n, 2))
    for a in parts:
        out[: len(a)] += a
    return out


def factory_audio(name: str, category: str) -> np.ndarray:
    """One note of a factory sound the Core shelf lists, at its category's
    note (a bare patch: no FX, no layers)."""
    from .factory import get_factory

    patch = get_factory(name)
    pitch = CORE_PITCH.get(category, "C4")
    return to_stereo(
        np.asarray(Synth(patch).note(pitch, note_duration(patch)), dtype=np.float64)
    )


def measure_all() -> dict:
    """{name: {spectrum, centroid, brightness}} — brightness ranks the
    recipes by spectral centroid (0 darkest .. 1 brightest), as the library
    ranks a family. The factory sounds on the Core shelf are measured too
    ("factory:Name", at their category's note), ranked with the recipes."""
    from ..analysis import extract
    from .soundmeta import band_magnitudes

    rows = []
    for e in RECIPES:
        audio = stack_audio(e)
        rows.append((e["name"], extract(audio).centroid, band_magnitudes(audio)))
    for name, cat in core_factory():
        audio = factory_audio(name, cat)
        rows.append(
            (FACTORY_REF + name, extract(audio).centroid, band_magnitudes(audio))
        )
    order = sorted(rows, key=lambda r: r[1])
    last = max(1, len(order) - 1)
    return {
        name: {
            "spectrum": [round(v, 3) for v in spec],
            "centroid": round(cent) if math.isfinite(cent) else 0,
            "brightness": round(rank / last, 3),
        }
        for rank, (name, cent, spec) in enumerate(order)
    }
