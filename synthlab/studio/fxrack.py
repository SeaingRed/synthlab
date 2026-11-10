"""User-editable effects chains for layers and the master bus.

A chain is plain JSON: [{"kind": "reverb", "params": {"mix": 0.3}}, ...]
(plus an `id` on a unit a song's automation lane targets: `unit_id`).
FX_UNITS declares what the UI may add and each param's (lo, hi, default);
build_chain turns a stored chain into callables for effects.chain().
Unknown kinds/params are dropped, values clamped — corrupt state can
never crash a render.
"""

from __future__ import annotations

import copy
import math
import re
import secrets

from .. import effects as fx
from ..sequencer import NOTE_NAMES, SCALES, midi
from . import meter as _meter
from .curves import UNIT_ID_LEN, unit_id_ok
from .state import Arrangement, drum_row_sounds

# the pitched vocal units' key and scale (`autotune`, `harmony`): index 0 is
# the session's (`tune_key`), the rest named — a scale's pitch classes over
# its key, "chromatic" every semitone
TUNE_KEYS = ("song key", *NOTE_NAMES)
TUNE_SCALES = ("song scale", "chromatic", *SCALES)
TUNE_SCALE_LABELS = tuple(s.replace("_", " ") for s in TUNE_SCALES)
# the live vocal chain's units (vocal.py): on every rack, but made for a mic
# (the sound agent does not offer them: agent/tools.py `AGENT_FX`)
VOCAL_UNITS = frozenset({"autotune", "harmony", "vox_comp", "deess", "vgate"})
# the FX2 units (fx2-worklet.js and the spring). The
# sound agent does not offer them either: its tool schema has a size budget
# (tests/studio/test_agent.py) and nine more catalogue lines break it
FX2_UNITS = frozenset(
    {
        "autofilter",
        "tremolo",
        "tape",
        "pingpong",
        "spring",
        "formant_shift",
        "freqshift",
        "exciter",
        "vocoder_fx",
    }
)
# the dynamics units (the mix's dynamics, fx2-worklet.js too).
# Not offered to the sound agent, for the FX2 units' reason
DYNAMICS_UNITS = frozenset({"ott", "limiter", "transient"})
# an LFO unit's `sync` (the auto-filter's, the tremolo's): 0 its own `rate`
# in Hz, else one cycle a note length (beats: `LFO_BEATS`, resolve_unit)
LFO_SYNC = (
    "off (Hz)",
    "4 bars",
    "2 bars",
    "1 bar",
    "1/2",
    "1/4",
    "1/8",
    "1/16",
    "dotted 1/8",
    "1/4 triplet",
    "1/8 triplet",
)
LFO_BEATS = (0.0, 16.0, 8.0, 4.0, 2.0, 1.0, 0.5, 0.25, 0.75, 2.0 / 3.0, 1.0 / 3.0)
assert len(LFO_BEATS) == len(LFO_SYNC)

# kind -> {"label", "params": {name: (lo, hi, default)}} (+ "choices":
# {name: labels} for a parameter that is a choice, stored as its index)
FX_UNITS: dict[str, dict] = {
    # `mode` is a choice (effects.DRIVE_MODES, resolved to its name in
    # resolve_unit); 0 soft is the unit as it always was, so a stored drive
    # without one sounds as it did
    "drive": {
        "label": "Drive",
        "params": {
            "amount": (1.0, 12.0, 2.0),
            "mix": (0.0, 1.0, 1.0),
            "mode": (0.0, 3.0, 0.0),
        },
        "choices": {"mode": fx.DRIVE_MODES},
    },
    # a clipper for a bus: `drive` dB in, nothing past `ceiling` dB out, the
    # last `soft` share of the ceiling a rounded knee (0 = a hard clip)
    "clip": {
        "label": "Clipper",
        "params": {
            "drive": (0.0, 24.0, 6.0),
            "ceiling": (-12.0, 0.0, -1.0),
            "soft": (0.0, 1.0, 0.3),
            "mix": (0.0, 1.0, 1.0),
        },
    },
    "phaser": {
        "label": "Phaser",
        "params": {
            "rate": (0.05, 2.0, 0.4),
            "depth": (0.0, 1.0, 0.7),
            "mix": (0.0, 1.0, 0.5),
        },
    },
    "chorus": {
        "label": "Chorus",
        "params": {
            "rate": (0.05, 2.0, 0.6),
            "depth": (0.0, 8.0, 3.0),
            "mix": (0.0, 1.0, 0.45),
        },
    },
    "flanger": {
        "label": "Flanger",
        "params": {
            "rate": (0.05, 2.0, 0.25),
            "depth": (0.0, 6.0, 2.5),
            "feedback": (0.0, 0.9, 0.5),
            "mix": (0.0, 1.0, 0.5),
        },
    },
    "delay": {
        "label": "Delay",
        "params": {
            "time": (0.05, 1.0, 0.375),
            "feedback": (0.0, 0.9, 0.35),
            "mix": (0.0, 1.0, 0.3),
            "ping_pong": (0.0, 1.0, 1.0),
            # 0 off · 1 1/4 · 2 1/8 · 3 dotted 1/8 · 4 1/16 · 5 triplet —
            # resolved from arr.bpm in build_chain, like pump's beats
            "sync": (0.0, 5.0, 0.0),
        },
    },
    "reverb": {
        "label": "Reverb",
        "params": {
            "decay": (0.2, 6.0, 2.0),
            "mix": (0.0, 1.0, 0.25),
            "size": (0.2, 1.0, 0.8),
        },
    },
    "gate": {
        "label": "Trance gate",
        "params": {"depth": (0.0, 1.0, 1.0)},
        "pattern": "x.x.x.x.",
    },
    "vinyl": {
        "label": "Vinyl",
        "params": {
            "crackle": (0.0, 1.0, 0.4),
            "hiss": (0.0, 1.0, 0.15),
            "wow": (0.0, 1.0, 0.3),
            "warmth": (0.0, 1.0, 0.5),
        },
    },
    "bitcrush": {
        "label": "Bitcrush",
        "params": {"bits": (4.0, 16.0, 8.0), "downsample": (1.0, 16.0, 4.0)},
    },
    # Tone-shaping trio. `fold` had been written in effects.py since the
    # start but was never registered here, so the UI could not reach it.
    "fold": {
        "label": "Wavefold",
        "params": {"amount": (1.0, 4.0, 1.5), "mix": (0.0, 1.0, 1.0)},
    },
    "ringmod": {
        "label": "Ring Mod",
        "params": {"freq": (20.0, 4000.0, 220.0), "mix": (0.0, 1.0, 1.0)},
    },
    # feedback hi matches effects.COMB_MAX_FEEDBACK — the resonator is only
    # unconditionally stable below 1.0
    "comb": {
        "label": "Comb",
        "params": {
            "tune": (40.0, 2000.0, 220.0),
            "feedback": (0.0, 0.95, 0.8),
            "mix": (0.0, 1.0, 0.5),
        },
    },
    # attack / release in seconds; `makeup` in dB, and below 0 (its default,
    # -1) the automatic makeup the unit always had
    "compress": {
        "label": "Compress",
        "params": {
            "threshold": (-40.0, 0.0, -14.0),
            "ratio": (1.0, 12.0, 4.0),
            "attack": (0.0005, 0.1, 0.005),
            "release": (0.02, 1.0, 0.12),
            "makeup": (-1.0, 24.0, -1.0),
        },
    },
    "lowpass": {
        "label": "Lowpass",
        "params": {"cutoff": (100.0, 12000.0, 1000.0), "q": (0.5, 8.0, 0.7)},
    },
    "highpass": {
        "label": "Highpass",
        "params": {"cutoff": (20.0, 2000.0, 200.0), "q": (0.5, 8.0, 0.7)},
    },
    # Low shelf / swept mid peak / high shelf (gains in dB; the shelves'
    # corners are effects.EQ_LOW_HZ / EQ_HIGH_HZ). Every gain defaults to 0,
    # where the unit is an exact bypass: adding it changes nothing until a
    # slider moves.
    "eq": {
        "label": "EQ",
        "params": {
            "low": (-12.0, 12.0, 0.0),
            "mid_freq": (100.0, 8000.0, 1000.0),
            "mid_gain": (-12.0, 12.0, 0.0),
            "mid_q": (0.3, 8.0, 1.0),
            "high": (-12.0, 12.0, 0.0),
        },
    },
    "width": {"label": "Width", "params": {"amount": (0.0, 2.0, 1.5)}},
    "sidechain": {
        "label": "Sidechain",
        "params": {"depth": (0.0, 1.0, 0.75), "release": (0.05, 1.0, 0.28)},
    },
    # Sidechain ducks under the *kick*, so it does nothing on Synths, where
    # the preview renders parts=("synth",) and there are no drums at all —
    # nor under a kick row that is muted or soloed out (`kick_steps`).
    # Pump ducks on the clock instead: same effect, no kit required, and
    # the unit for a duck with no audible kick.
    "pump": {
        "label": "Pump",
        "params": {
            "depth": (0.0, 1.0, 0.7),
            "release": (0.05, 1.0, 0.35),
            "beats": (0.25, 4.0, 1.0),  # 1 = every beat, 0.5 = eighths
        },
    },
    "granular": {
        "label": "Granular",
        "params": {
            "grain": (0.01, 0.4, 0.08),
            "scatter": (0.0, 1.0, 0.4),
            "pitch": (-12.0, 12.0, 0.0),
            "density": (0.5, 6.0, 2.0),
            "mix": (0.0, 1.0, 0.6),
        },
    },
    "shimmer": {
        "label": "Shimmer",
        "params": {
            "decay": (0.5, 8.0, 3.0),
            "mix": (0.0, 1.0, 0.3),
            "shift": (-12.0, 24.0, 12.0),
            "size": (0.2, 1.0, 0.9),
        },
    },
    # Stutter: on the pattern's 16ths (like the gate's), the next `grid`
    # slice is repeated. Synced to the bpm, seeded by the grid step, and
    # periodic over the loop (`repeat_period`), so a loop re-renders
    # identically and its wrap-tiled tail meets its head.
    "beat_repeat": {
        "label": "Beat repeat",
        "params": {
            # 0 1/4 · 1 1/8 · 2 1/16 · 3 1/32 (effects.REPEAT_GRID_BEATS)
            "grid": (0.0, 3.0, 2.0),
            "repeats": (1.0, 16.0, 3.0),
            "chance": (0.0, 1.0, 1.0),
            "pitch": (-12.0, 0.0, 0.0),  # semitones lower per repeat
            "decay": (0.0, 0.9, 0.0),  # level lost per repeat
            "gate": (0.1, 1.0, 1.0),  # share of each repeat that sounds
            "mix": (0.0, 1.0, 1.0),
            "seed": (0.0, 99.0, 0.0),  # re-rolls which steps `chance` keeps
        },
        # the 4th beat of every bar: the classic end-of-bar stutter
        "pattern": "............x...",
    },
    # Tape stop: the last `beats` of every period (`repeat_period`, the
    # beat repeat's own) slow to a halt over `fall` of that window, then
    # silence to the bar line. Periodic, so loop-safe like the beat repeat;
    # in a song it is placed by a lane on its `mix` (0 everywhere but the
    # bar that stops).
    "stop": {
        "label": "Tape stop",
        "params": {
            "beats": (0.25, 4.0, 1.0),  # how long before the period's end it starts
            "fall": (0.25, 1.0, 1.0),  # share of that the slowdown takes
            "mix": (0.0, 1.0, 1.0),
        },
    },
    # --- studio tricks (the vocal booth): every one causal and a
    # live-key twin in fx-worklet.js (the plate a ConvolverNode), and every
    # one ends in dry * (1 - mix) + wet * mix (curves.FX_LANES) ---
    # ADT: a second copy `time` s late, its delay wandering (`wobble`), the
    # pair panned apart (`spread`)
    "doubler": {
        "label": "ADT double",
        "params": {
            "time": (0.015, 0.06, 0.03),
            "wobble": (0.0, 1.0, 0.5),
            "spread": (0.0, 1.0, 0.6),
            "mix": (0.0, 1.0, 1.0),
        },
    },
    # a rotating speaker: horn and drum, `speed` 0 chorale .. 1 tremolo (a
    # lane on it is the switch, the rotors ramping between), its amp's drive
    "rotary": {
        "label": "Rotary",
        "params": {
            "speed": (0.0, 1.0, 0.0),
            "drive": (0.0, 5.0, 0.8),
            "mix": (0.0, 1.0, 1.0),
        },
    },
    # an echo off a loop of tape: the head wavers (`wow`), each pass darker
    # (`tone`) and saturated (`drive`); `sync` as the delay's
    "tape_echo": {
        "label": "Tape echo",
        "params": {
            "time": (0.04, 1.0, 0.3),
            "feedback": (0.0, 0.95, 0.45),
            "wow": (0.0, 1.0, 0.3),
            "tone": (0.0, 1.0, 0.5),
            "drive": (0.0, 1.0, 0.3),
            "mix": (0.0, 1.0, 0.35),
            "sync": (0.0, 5.0, 0.0),
        },
    },
    # effects.bandpass twice at `centre`, `width` octaves wide, then driven
    "telephone": {
        "label": "Telephone",
        "params": {
            "centre": (300.0, 3000.0, 1200.0),
            "width": (0.5, 4.0, 2.5),
            "drive": (0.0, 8.0, 2.0),
            "mix": (0.0, 1.0, 1.0),
        },
    },
    # a pitch shifter: whole semitones (resolve_unit rounds), an octave or a
    # fifth under or over the dry
    "pitch": {
        "label": "Pitch shift",
        "params": {"semis": (-24.0, 24.0, 12.0), "mix": (0.0, 1.0, 0.5)},
    },
    # a plate: bright, dense from its first ms, no room's reflections
    "plate": {
        "label": "Plate",
        "params": {
            "decay": (0.4, 6.0, 2.2),
            "tone": (0.0, 1.0, 0.65),
            "predelay": (0.0, 0.08, 0.012),
            "mix": (0.0, 1.0, 0.3),
        },
    },
    # --- the live vocal chain: causal, every one a twin in
    # vox-worklet.js — a mic runs through them as it is sung into ---
    # auto-tune: the sung pitch pulled to the nearest note of a key and a
    # scale (`key` / `scale` 0: the session's — resolve_unit), `speed` 0 a
    # hard snap .. 1 a slow glide, `humanize` the singer's wander left in
    "autotune": {
        "label": "Auto-tune",
        "params": {
            "key": (0.0, 12.0, 0.0),
            "scale": (0.0, float(len(TUNE_SCALES) - 1), 0.0),
            "speed": (0.0, 1.0, 0.1),
            "humanize": (0.0, 1.0, 0.0),
            "mix": (0.0, 1.0, 1.0),
        },
        "choices": {"key": TUNE_KEYS, "scale": TUNE_SCALE_LABELS},
    },
    # a harmonizer: one or two voices whole steps of the key from the
    # sung one, each its level and pan (the second may be off)
    "harmony": {
        "label": "Harmonizer",
        "params": {
            "key": (0.0, 12.0, 0.0),
            "scale": (0.0, float(len(TUNE_SCALES) - 1), 0.0),
            "first": (0.0, float(fx.HARMONY_OFF - 1), 0.0),
            "first_level": (0.0, 1.0, 0.5),
            "first_pan": (-1.0, 1.0, -0.4),
            "second": (0.0, float(fx.HARMONY_OFF), float(fx.HARMONY_OFF)),
            "second_level": (0.0, 1.0, 0.4),
            "second_pan": (-1.0, 1.0, 0.4),
        },
        "choices": {
            "key": TUNE_KEYS,
            "scale": TUNE_SCALE_LABELS,
            "first": fx.HARMONY_NAMES,
            "second": (*fx.HARMONY_NAMES, "off"),
        },
    },
    # a vocal compressor (live: `compress` stays the loop's, offline)
    "vox_comp": {
        "label": "Vocal comp",
        "params": {
            "threshold": (-40.0, 0.0, -18.0),
            "ratio": (1.0, 12.0, 3.0),
            "attack": (0.0005, 0.05, 0.005),
            "release": (0.02, 0.6, 0.12),
            "makeup": (0.0, 18.0, 3.0),
            "mix": (0.0, 1.0, 1.0),
        },
    },
    # a de-esser: the band over `freq` turned down on its own level
    "deess": {
        "label": "De-esser",
        "params": {
            "freq": (4000.0, 10000.0, 6500.0),
            "threshold": (-50.0, 0.0, -30.0),
            "depth": (0.0, 20.0, 10.0),
        },
    },
    # a noise gate / downward expander for the mic
    "vgate": {
        "label": "Noise gate",
        "params": {
            "threshold": (-80.0, -10.0, -50.0),
            "ratio": (1.0, 20.0, 4.0),
            "depth": (0.0, 80.0, 40.0),
            "release": (0.02, 1.0, 0.15),
        },
    },
    # --- the FX2 units: causal, every one a twin in
    # fx2-worklet.js (the spring a ConvolverNode on its own IR) ---
    # an auto-filter, the filter-house sweep: a resonant LP / BP / HP whose
    # cutoff an LFO moves up `depth` octaves (`rate` Hz, or `sync` a note
    # length: resolve_unit) and an envelope follower on the input moves by
    # `env` (negative: it closes as the input swells), `sens` dB into it
    "autofilter": {
        "label": "Auto-filter",
        "params": {
            "mode": (0.0, 2.0, 0.0),
            "cutoff": (40.0, 8000.0, 400.0),
            "q": (0.5, 10.0, 3.0),
            "depth": (0.0, 6.0, 3.0),
            "rate": (0.05, 10.0, 0.5),
            "sync": (0.0, float(len(LFO_SYNC) - 1), 3.0),
            "shape": (0.0, float(len(fx.LFO_SHAPES) - 1), 0.0),
            "env": (-1.0, 1.0, 0.0),
            "sens": (-12.0, 24.0, 6.0),
            "attack": (0.001, 0.2, 0.01),
            "release": (0.02, 1.0, 0.25),
            "mix": (0.0, 1.0, 1.0),
        },
        "choices": {
            "mode": fx.AUTOFILTER_MODES,
            "sync": LFO_SYNC,
            "shape": fx.LFO_SHAPES,
        },
    },
    # a tremolo, and with the right channel's LFO `phase` degrees on an
    # auto-pan (180: the two in turn)
    "tremolo": {
        "label": "Tremolo / pan",
        "params": {
            "rate": (0.1, 20.0, 4.0),
            "sync": (0.0, float(len(LFO_SYNC) - 1), 6.0),
            "depth": (0.0, 1.0, 0.5),
            "shape": (0.0, float(len(fx.LFO_SHAPES) - 1), 0.0),
            "phase": (0.0, 180.0, 0.0),
        },
        "choices": {"sync": LFO_SYNC, "shape": fx.LFO_SHAPES},
    },
    # tape saturation: `drive` dB into a tape-style curve, the head bump
    # (dB at 90 Hz), the top's roll-off (`tone`), wow and flutter (functions
    # of time: never a random stream), hiss under the signal (off at 0)
    "tape": {
        "label": "Tape",
        "params": {
            "drive": (0.0, 24.0, 6.0),
            "bump": (0.0, 6.0, 2.0),
            "tone": (0.0, 1.0, 0.6),
            "wow": (0.0, 1.0, 0.2),
            "flutter": (0.0, 1.0, 0.2),
            "hiss": (0.0, 1.0, 0.0),
            "mix": (0.0, 1.0, 1.0),
        },
    },
    # a ping-pong delay: left, right, left..., each bounce through the
    # loop's high-pass and low-pass; `sync` as the delay's
    "pingpong": {
        "label": "Ping-pong",
        "params": {
            "time": (0.05, 1.0, 0.375),
            "sync": (0.0, 5.0, 3.0),
            "feedback": (0.0, 0.9, 0.45),
            "lowcut": (20.0, 1000.0, 120.0),
            "highcut": (1000.0, 16000.0, 6000.0),
            "width": (0.0, 1.0, 1.0),
            "mix": (0.0, 1.0, 0.35),
        },
    },
    # a spring tank: dispersive (every echo a chirp — `drip`), dark (`tone`)
    "spring": {
        "label": "Spring",
        "params": {
            "decay": (0.5, 5.0, 2.0),
            "tone": (0.0, 1.0, 0.5),
            "drip": (0.0, 1.0, 0.5),
            "mix": (0.0, 1.0, 0.3),
        },
    },
    # the spectral envelope moved `shift` semitones, the pitch kept
    "formant_shift": {
        "label": "Formant shift",
        "params": {"shift": (-12.0, 12.0, 4.0), "mix": (0.0, 1.0, 1.0)},
    },
    # every component moved `shift` Hz (single sideband): bells, a metallic
    # voice, a barber-pole at a few Hz; `feedback` the spiral
    "freqshift": {
        "label": "Freq shift",
        "params": {
            "shift": (-500.0, 500.0, 30.0),
            "feedback": (0.0, 0.9, 0.0),
            "mix": (0.0, 1.0, 0.5),
        },
    },
    # an exciter: harmonics made from the band over `freq`, `amount` added
    "exciter": {
        "label": "Exciter",
        "params": {
            "freq": (1000.0, 10000.0, 3000.0),
            "drive": (1.0, 20.0, 6.0),
            "amount": (0.0, 1.0, 0.3),
        },
    },
    # a vocoder on its own carrier: a chord of saws in the key (`key` /
    # `scale` 0: the session's, as the auto-tune's), the input the modulator
    "vocoder_fx": {
        "label": "Vocoder",
        "params": {
            "key": (0.0, 12.0, 0.0),
            "scale": (0.0, float(len(TUNE_SCALES) - 1), 0.0),
            "chord": (0.0, float(len(fx.VOCODER_FX_CHORDS) - 1), 0.0),
            "octave": (1.0, 5.0, 3.0),
            "bands": (8.0, 32.0, 16.0),
            "shift": (-12.0, 12.0, 0.0),
            "noise": (0.0, 1.0, 0.1),
            "mix": (0.0, 1.0, 1.0),
        },
        "choices": {
            "key": TUNE_KEYS,
            "scale": TUNE_SCALE_LABELS,
            "chord": tuple(fx.VOCODER_FX_CHORDS),
        },
    },
    # --- the mix's dynamics, causal, each a twin in
    # fx2-worklet.js. The envelope filter is the auto-filter's `env` ---
    # three bands (120 Hz / 2.5 kHz), each pulled toward the middle from
    # both sides: `depth` the blend (parallel), `time` x the followers',
    # `upward` / `downward` how much of each, a gain a band (dB), `output`
    "ott": {
        "label": "OTT",
        "params": {
            "depth": (0.0, 1.0, 0.5),
            "time": (0.25, 4.0, 1.0),
            "upward": (0.0, 1.0, 1.0),
            "downward": (0.0, 1.0, 1.0),
            "low": (-12.0, 12.0, 0.0),
            "mid": (-12.0, 12.0, 0.0),
            "high": (-12.0, 12.0, 0.0),
            "output": (-24.0, 12.0, 0.0),
        },
    },
    # `gain` dB in, the peaks held at `ceiling` dB; `lookahead` (seconds) is
    # also the unit's latency (a delay, live and in the loop alike)
    "limiter": {
        "label": "Limiter",
        "params": {
            "gain": (0.0, 24.0, 0.0),
            "ceiling": (-12.0, 0.0, -1.0),
            "release": (0.01, 1.0, 0.08),
            "lookahead": (0.0, 0.01, 0.002),
        },
    },
    # a hit's front (`attack`) and its body and tail (`sustain`), -1..1
    "transient": {
        "label": "Transient",
        "params": {
            "attack": (-1.0, 1.0, 0.0),
            "sustain": (-1.0, 1.0, 0.0),
            "output": (-12.0, 12.0, 0.0),
        },
    },
}


# Named settings a unit can take in one move: the unit's ★ menu and its
# right-click rows. A preset names only what it
# moves; every other parameter goes back to its default (a recall sets the
# unit's params to `unit_preset`'s and `clean_chain` fills the rest), so the
# same preset always sounds the same. Values sit inside FX_UNITS' own
# ranges (tests/studio/test_fx_presets.py holds every one to them).
FX_PRESETS: dict[str, dict[str, dict[str, float]]] = {
    "drive": {
        "Warm tube": {"amount": 1.6, "mode": 3.0},
        "Crunch": {"amount": 4.0, "mode": 0.0},
        "Hard clip": {"amount": 5.0, "mode": 1.0},
        "Fuzz": {"amount": 7.0, "mode": 2.0, "mix": 0.8},
    },
    "clip": {
        "Gentle": {"drive": 3.0, "soft": 0.6},
        "Loud": {"drive": 12.0, "ceiling": -0.5},
        "Brickwall": {"drive": 18.0, "ceiling": -0.3, "soft": 0.0},
    },
    "phaser": {
        "Subtle": {"rate": 0.3, "depth": 0.4, "mix": 0.3},
        "Slow swirl": {"rate": 0.15, "depth": 0.85},
        "Fast": {"rate": 1.2, "depth": 0.6, "mix": 0.45},
    },
    "chorus": {
        "Subtle": {"rate": 0.4, "depth": 2.0, "mix": 0.3},
        "Wide": {"depth": 4.5, "mix": 0.5},
        "Lush": {"rate": 0.25, "depth": 7.0, "mix": 0.6},
    },
    "flanger": {
        "Subtle": {"rate": 0.3, "depth": 1.5, "feedback": 0.3, "mix": 0.35},
        "Jet": {"rate": 0.1, "depth": 5.0, "feedback": 0.8, "mix": 0.6},
        "Metallic": {"rate": 1.0, "depth": 3.0, "feedback": 0.85},
    },
    "delay": {
        "Slapback": {"time": 0.09, "feedback": 0.1, "ping_pong": 0.0},
        "Eighth": {"sync": 2.0},
        "Dotted eighth": {"sync": 3.0, "feedback": 0.4},
        "Dub": {"sync": 1.0, "feedback": 0.75, "mix": 0.4},
    },
    "reverb": {
        "Room": {"decay": 0.8, "mix": 0.2, "size": 0.4},
        "Hall": {"decay": 2.8, "mix": 0.3, "size": 0.85},
        "Cathedral": {"decay": 5.5, "mix": 0.35, "size": 1.0},
        "Ambient wash": {"decay": 4.5, "mix": 0.55, "size": 0.95},
    },
    "vinyl": {
        "Clean warmth": {"crackle": 0.0, "hiss": 0.05, "wow": 0.1, "warmth": 0.8},
        "Dusty": {"crackle": 0.5, "hiss": 0.2, "wow": 0.2, "warmth": 0.6},
        "Worn tape": {"crackle": 0.15, "hiss": 0.3, "wow": 0.6, "warmth": 0.7},
    },
    "bitcrush": {
        "Lo-fi": {"bits": 10.0, "downsample": 3.0},
        "8-bit": {"bits": 6.0, "downsample": 6.0},
        "Crushed": {"bits": 4.0, "downsample": 12.0},
    },
    "fold": {"Soft": {"amount": 1.5, "mix": 0.6}, "Hard": {"amount": 3.5}},
    "ringmod": {
        "Bell": {"freq": 440.0, "mix": 0.4},
        "Robot": {"freq": 90.0, "mix": 0.8},
    },
    "comb": {
        "Resonant": {"tune": 220.0, "feedback": 0.7, "mix": 0.4},
        "Metallic": {"tune": 600.0, "feedback": 0.85},
    },
    "compress": {
        "Glue": {"threshold": -18.0, "ratio": 2.0, "attack": 0.01, "release": 0.2},
        "Punch": {"threshold": -14.0, "ratio": 4.0, "attack": 0.02, "release": 0.1},
        "Squash": {"threshold": -28.0, "ratio": 10.0, "attack": 0.001, "release": 0.08},
    },
    "lowpass": {
        "Warm": {"cutoff": 3500.0},
        "Muffled": {"cutoff": 900.0},
        "Resonant": {"cutoff": 1500.0, "q": 4.0},
    },
    "highpass": {
        "Rumble cut": {"cutoff": 40.0},
        "Thin": {"cutoff": 400.0},
        "Telephone-ish": {"cutoff": 800.0, "q": 1.2},
    },
    "eq": {
        "Bass boost": {"low": 5.0},
        "Bright": {"high": 4.0},
        "Scooped": {
            "low": 2.0,
            "mid_freq": 900.0,
            "mid_gain": -5.0,
            "mid_q": 0.8,
            "high": 2.0,
        },
        "Presence": {"mid_freq": 3000.0, "mid_gain": 3.0, "mid_q": 1.2, "high": 1.0},
    },
    "width": {
        "Mono": {"amount": 0.0},
        "Natural": {"amount": 1.0},
        "Extra wide": {"amount": 2.0},
    },
    "pump": {
        "Gentle": {"depth": 0.4, "release": 0.3},
        "Heavy": {"depth": 0.9, "release": 0.25},
        "Half time": {"depth": 0.7, "release": 0.4, "beats": 2.0},
    },
    "granular": {
        "Cloud": {"grain": 0.15, "scatter": 0.7, "density": 4.0},
        "Stutter": {"grain": 0.04, "scatter": 0.2, "mix": 0.7},
        "Octave shimmer": {
            "grain": 0.12,
            "scatter": 0.5,
            "pitch": 12.0,
            "density": 3.0,
            "mix": 0.5,
        },
    },
    "shimmer": {
        "Heavenly": {"decay": 5.0, "mix": 0.35, "size": 0.95},
        "Fifth": {"shift": 7.0, "mix": 0.3, "size": 0.8},
    },
    "stop": {"Quick stop": {"beats": 0.5, "fall": 0.5}, "Long stop": {"beats": 2.0}},
    "doubler": {
        "Tight": {"time": 0.02, "wobble": 0.3, "spread": 0.5},
        "Wide": {"time": 0.04, "wobble": 0.6, "spread": 1.0},
    },
    "rotary": {
        "Chorale (slow)": {"speed": 0.0},
        "Tremolo (fast)": {"speed": 1.0},
        "Driven": {"speed": 0.5, "drive": 3.0},
    },
    "tape_echo": {
        "Slapback": {"time": 0.11, "feedback": 0.15, "wow": 0.2, "tone": 0.6},
        "Space echo": {
            "sync": 3.0,
            "feedback": 0.55,
            "wow": 0.4,
            "tone": 0.45,
            "drive": 0.4,
            "mix": 0.4,
        },
        "Dub": {"sync": 1.0, "feedback": 0.75, "tone": 0.35, "drive": 0.5, "mix": 0.45},
    },
    "telephone": {
        "Phone": {"centre": 1200.0, "width": 1.5},
        "Radio": {"centre": 1800.0, "width": 2.5, "drive": 1.0},
        "Megaphone": {"centre": 1500.0, "width": 1.0, "drive": 6.0},
    },
    "pitch": {
        "Octave up": {"semis": 12.0},
        "Octave down": {"semis": -12.0},
        "Fifth up": {"semis": 7.0, "mix": 0.4},
        "Two octaves down": {"semis": -24.0, "mix": 0.4},
    },
    "plate": {
        "Short": {"decay": 1.2, "tone": 0.7, "predelay": 0.005, "mix": 0.25},
        "Vocal": {"decay": 2.2, "predelay": 0.02},
        "Long": {"decay": 5.0, "tone": 0.6, "predelay": 0.03, "mix": 0.35},
    },
    "autotune": {
        "Natural": {"speed": 0.5, "humanize": 0.3},
        "Pop polish": {"speed": 0.2, "humanize": 0.1},
        "Hard (robot)": {"speed": 0.0},
        "Chromatic snap": {"scale": 1.0, "speed": 0.0},
    },
    "harmony": {
        "3rd up": {"first": 0.0},
        "3rd + 5th": {"first": 0.0, "second": 1.0},
        "Octave under": {"first": 5.0, "first_level": 0.4, "first_pan": 0.0},
        "Wide fifths": {
            "first": 1.0,
            "first_pan": -0.7,
            "second": 4.0,
            "second_pan": 0.7,
        },
    },
    "vox_comp": {
        "Gentle": {"threshold": -16.0, "ratio": 2.0, "makeup": 2.0},
        "Vocal": {"threshold": -22.0, "ratio": 4.0, "makeup": 5.0},
        "Squashed": {
            "threshold": -30.0,
            "ratio": 10.0,
            "attack": 0.001,
            "release": 0.08,
            "makeup": 9.0,
        },
        "Parallel": {"threshold": -32.0, "ratio": 8.0, "makeup": 10.0, "mix": 0.5},
    },
    "deess": {
        "Gentle": {"threshold": -26.0, "depth": 6.0},
        "Strong": {"threshold": -36.0, "depth": 14.0},
        "Bright voice": {"freq": 8000.0},
    },
    "vgate": {
        "Soft": {"ratio": 2.0, "depth": 20.0},
        "Tight": {"threshold": -40.0, "ratio": 12.0, "depth": 60.0, "release": 0.08},
        "Noisy room": {"threshold": -35.0, "ratio": 6.0, "release": 0.2},
    },
    # sync indexes LFO_SYNC (3 1 bar, 5 1/4, 6 1/8, 7 1/16, 8 dotted 1/8),
    # shape effects.LFO_SHAPES, mode AUTOFILTER_MODES
    "autofilter": {
        "Filter house": {"cutoff": 300.0, "q": 4.0, "depth": 4.0, "sync": 2.0},
        "Wah quarters": {"mode": 1.0, "cutoff": 400.0, "q": 6.0, "sync": 5.0},
        "Envelope funk": {
            "mode": 1.0,
            "cutoff": 250.0,
            "q": 5.0,
            "depth": 4.0,
            "env": 1.0,
            "sens": 12.0,
            "attack": 0.005,
            "release": 0.15,
        },
        "Envelope down": {
            "cutoff": 3000.0,
            "q": 3.0,
            "depth": 3.0,
            "env": -1.0,
            "sens": 12.0,
            "attack": 0.005,
            "release": 0.2,
        },
        "Sixteenth chop": {"cutoff": 600.0, "q": 2.0, "sync": 7.0, "shape": 5.0},
        "Rising highpass": {
            "mode": 2.0,
            "cutoff": 60.0,
            "q": 1.5,
            "depth": 5.0,
            "sync": 1.0,
            "shape": 2.0,
        },
    },
    "tremolo": {
        "Vintage amp": {"sync": 0.0, "rate": 5.5, "depth": 0.4},
        "Auto-pan": {"sync": 5.0, "depth": 0.8, "phase": 180.0},
        "Choppy": {"sync": 7.0, "depth": 1.0, "shape": 4.0},
        "Slow swell": {"sync": 3.0, "depth": 0.6, "shape": 1.0},
        "Helicopter": {"sync": 0.0, "rate": 14.0, "depth": 0.9, "shape": 5.0},
    },
    "tape": {
        "Warm": {"drive": 4.0, "bump": 2.0, "tone": 0.7, "wow": 0.1, "flutter": 0.1},
        "Hot cassette": {
            "drive": 12.0,
            "bump": 3.0,
            "tone": 0.3,
            "wow": 0.4,
            "flutter": 0.5,
            "hiss": 0.4,
        },
        "Worn reel": {"drive": 8.0, "tone": 0.4, "wow": 0.8, "flutter": 0.3},
        "Clean mastering": {
            "drive": 2.0,
            "bump": 1.0,
            "tone": 1.0,
            "wow": 0.0,
            "flutter": 0.0,
        },
    },
    "pingpong": {
        "Dotted eighth": {"sync": 3.0},
        "Eighth bounce": {"sync": 2.0, "feedback": 0.35, "mix": 0.3},
        "Quarter dub": {"sync": 1.0, "feedback": 0.65, "highcut": 2500.0, "mix": 0.4},
        "Narrow": {"sync": 2.0, "width": 0.4, "mix": 0.25},
    },
    "spring": {
        "Surf": {"decay": 2.5, "tone": 0.6, "drip": 0.9, "mix": 0.4},
        "Amp spring": {"decay": 1.5, "tone": 0.4, "drip": 0.5, "mix": 0.25},
        "Dub tank": {"decay": 4.0, "tone": 0.3, "drip": 0.7, "mix": 0.45},
        "Short": {"decay": 0.8, "tone": 0.6, "drip": 0.3, "mix": 0.2},
    },
    "formant_shift": {
        "Bigger": {"shift": -4.0},
        "Smaller": {"shift": 4.0},
        "Giant": {"shift": -10.0},
        "Chipmunk-ish": {"shift": 9.0},
    },
    "freqshift": {
        "Barber pole": {"shift": 3.0, "feedback": 0.6, "mix": 0.5},
        "Bell": {"shift": 120.0, "mix": 0.6},
        "Metallic": {"shift": -230.0, "feedback": 0.3, "mix": 0.7},
        "Detune": {"shift": 1.5, "mix": 0.5},
    },
    "exciter": {
        "Air": {"freq": 6000.0, "drive": 4.0, "amount": 0.3},
        "Presence": {"freq": 2500.0, "drive": 6.0, "amount": 0.25},
        "Crispy": {"freq": 3500.0, "drive": 14.0, "amount": 0.5},
    },
    "vocoder_fx": {
        "Robot": {"chord": 0.0, "bands": 20.0, "noise": 0.0},
        "Power": {"chord": 2.0, "octave": 2.0},
        "Seventh pad": {"chord": 1.0, "bands": 24.0, "noise": 0.05},
        "Whisper": {"noise": 0.8, "bands": 24.0},
    },
    "ott": {
        "Light": {"depth": 0.25},
        "Over the top": {"depth": 1.0, "output": -3.0},
        "Upward only": {"depth": 0.6, "downward": 0.0},
        "Slow glue": {"depth": 0.4, "time": 3.0},
        "Bright squash": {"depth": 0.7, "low": -3.0, "high": 3.0},
    },
    "limiter": {
        "Safety": {"ceiling": -0.3},
        "Loud": {"gain": 6.0, "ceiling": -0.5, "release": 0.05},
        "Smash": {"gain": 12.0, "ceiling": -1.0, "release": 0.03},
        "Instant": {"lookahead": 0.0},
    },
    "transient": {
        "Punch": {"attack": 0.6},
        "Snap": {"attack": 1.0, "sustain": -0.3},
        "Tight": {"sustain": -0.7},
        "Roomy": {"sustain": 0.7},
        "Soften": {"attack": -0.6},
    },
}


def unit_preset(kind: str, name: str) -> dict[str, float] | None:
    """What preset `name` of a unit sets: just the preset's parameters
    (`{}` for "defaults"). A recall replaces the unit's params with this and
    `clean_chain` fills every other one with its default, so a preset never
    keeps what the last one set. None for a kind or a name there is no
    preset for."""
    if kind not in FX_UNITS:
        return None
    if name == "defaults":
        return {}
    preset = FX_PRESETS.get(kind, {}).get(name)
    return None if preset is None else dict(preset)


MAX_UNITS = 6  # per chain — enough to stack, hard to melt the CPU with
_PATTERN_RE = re.compile(r"^[x.]{1,32}$")

# Rhythms for the trance gate, by name: data a menu can offer
# (`POST /fragment/fx/set` takes `name=preset`). One character a 16th, as
# the unit reads it, so a preset
# is nothing the pattern field could not hold — choosing one writes its
# string into the unit and the name is not kept. A gate cannot open and
# shut inside one 16th: "eighths" is the fastest even chop there is.
GATE_PATTERNS: dict[str, dict] = {
    "eighths": {"label": "Eighths", "pattern": "x.x.x.x.x.x.x.x."},
    "onbeat": {"label": "On the beat", "pattern": "xx..xx..xx..xx.."},
    "offbeat": {"label": "Offbeat eighths", "pattern": "..xx..xx..xx..xx"},
    "trance": {"label": "Trance", "pattern": "x.xxx.x.x.xxx.x."},
    "gallop": {"label": "Gallop", "pattern": "x.xxx.xxx.xxx.xx"},
    # the three 16ths after every kick: the rolling bass, as a gate
    "rolling": {"label": "Rolling", "pattern": ".xxx.xxx.xxx.xxx"},
    # every third 16th: it crosses the beat and turns over at the bar
    "dotted": {"label": "Dotted eighths", "pattern": "x..x..x..x..x..x"},
    # the trance figure, then an answer that runs into the next bar
    "two_bar": {
        "label": "Two-bar",
        "pattern": "x.xxx.x.x.xxx.x.x.x.xxx.x.xxxxxx",
    },
}
assert all(_PATTERN_RE.fullmatch(g["pattern"]) for g in GATE_PATTERNS.values())


def gate_pattern(name) -> str | None:
    """A gate preset's pattern string, or None for a name that is not one."""
    preset = GATE_PATTERNS.get(name) if isinstance(name, str) else None
    return preset["pattern"] if preset else None


# Whole chains the rack adds in one go ("+ add chain…", `/fragment/fx/chain`),
# built only from the units above so nothing here is a new effect: a preset
# is a starting point that lands as ordinary, editable units. Each is stored
# already clean (`clean_chain` of it is itself — pinned by test_fx_chains),
# so applying one to an empty scope stores exactly these numbers.
# a compressor's attack, release and makeup at the unit's defaults (makeup
# -1: automatic), as every preset's compressor keeps them
_COMP = {"attack": 0.005, "release": 0.12, "makeup": -1.0}

CHAIN_PRESETS: dict[str, dict] = {
    # a voice through a handset: the room it was recorded in first, then
    # the band a phone mic passes (~300 Hz-3.5 kHz), squashed hard, a little
    # grit, and one channel
    "phone_mic": {
        "label": "Phone Mic",
        "units": [
            {"kind": "reverb", "params": {"decay": 0.4, "mix": 0.18, "size": 0.25}},
            {"kind": "highpass", "params": {"cutoff": 300.0, "q": 0.8}},
            {"kind": "lowpass", "params": {"cutoff": 3500.0, "q": 0.8}},
            {"kind": "compress", "params": {"threshold": -30.0, "ratio": 8.0, **_COMP}},
            {"kind": "drive", "params": {"amount": 2.2, "mix": 0.7, "mode": 0.0}},
            {"kind": "width", "params": {"amount": 0.0}},
        ],
    },
    # stereo forced to mono (effects.width at 0 is the mid channel only)
    "mono_maker": {
        "label": "Mono Maker",
        "units": [{"kind": "width", "params": {"amount": 0.0}}],
    },
    # a dotted-eighth echo darkened by the low-pass, into a long wide room
    "washy": {
        "label": "Washy",
        "units": [
            {
                "kind": "delay",
                "params": {
                    "time": 0.375,
                    "feedback": 0.55,
                    "mix": 0.3,
                    "ping_pong": 1.0,
                    "sync": 3.0,
                },
            },
            {"kind": "lowpass", "params": {"cutoff": 3200.0, "q": 0.6}},
            {"kind": "reverb", "params": {"decay": 5.5, "mix": 0.45, "size": 0.95}},
        ],
    },
    # ducks on every beat of the clock, kit or no kit
    "pump_quarter": {
        "label": "Sidechain Pump 1/4",
        "units": [
            {"kind": "pump", "params": {"depth": 0.8, "release": 0.25, "beats": 1.0}}
        ],
    },
    # crackle and wow, a few bits and half the sample rate gone, warm clip
    "tape_lofi": {
        "label": "Tape Lo-fi",
        "units": [
            {
                "kind": "vinyl",
                "params": {"crackle": 0.25, "hiss": 0.12, "wow": 0.35, "warmth": 0.7},
            },
            {"kind": "bitcrush", "params": {"bits": 12.0, "downsample": 2.0}},
            {"kind": "drive", "params": {"amount": 1.6, "mix": 0.6, "mode": 0.0}},
        ],
    },
    # --- the four style packs ------------------------------------------------
    # filter house: a resonant low-pass to sweep (its cutoff and q take a
    # song lane), a slow phaser, and the pump
    "filter_disco": {
        "label": "Filter Disco",
        "units": [
            {"kind": "lowpass", "params": {"cutoff": 2400.0, "q": 2.5}},
            {"kind": "phaser", "params": {"rate": 0.2, "depth": 0.7, "mix": 0.45}},
            {"kind": "pump", "params": {"depth": 0.8, "release": 0.3, "beats": 1.0}},
        ],
    },
    # under the kick itself (a sidechain keys off the kit: nothing on
    # Synths, where no drums render): all but gone on the hit, back in 0.3 s
    "hard_duck": {
        "label": "Hard Duck",
        "units": [{"kind": "sidechain", "params": {"depth": 0.9, "release": 0.3}}],
    },
    # bit reduction and heavy compression as character
    "crush_bus": {
        "label": "Crush Bus",
        "units": [
            {"kind": "bitcrush", "params": {"bits": 12.0, "downsample": 2.0}},
            {"kind": "compress", "params": {"threshold": -24.0, "ratio": 8.0, **_COMP}},
            {"kind": "drive", "params": {"amount": 1.6, "mix": 1.0, "mode": 0.0}},
        ],
    },
    # the pluck's echo: dotted eighths, left then right
    "dotted_ping_pong": {
        "label": "Dotted Ping-Pong",
        "units": [
            {
                "kind": "delay",
                "params": {
                    "time": 0.375,
                    "feedback": 0.45,
                    "mix": 0.3,
                    "ping_pong": 1.0,
                    "sync": 3.0,
                },
            }
        ],
    },
    # the classic chop (`GATE_PATTERNS["trance"]`) into a room, so the
    # gaps fill with the tail; its depth takes a song lane
    "trance_gate": {
        "label": "Trance Gate",
        "units": [
            {
                "kind": "gate",
                "params": {"depth": 1.0},
                "pattern": "x.xxx.x.x.xxx.x.",
            },
            {"kind": "reverb", "params": {"decay": 3.0, "mix": 0.3, "size": 0.9}},
        ],
    },
    # gentler than Tape Lo-fi: more wow than crackle, the top rolled off,
    # held together by a slow compressor — no bits lost
    "tape_wear": {
        "label": "Tape Wear",
        "units": [
            {
                "kind": "vinyl",
                "params": {"crackle": 0.12, "hiss": 0.1, "wow": 0.6, "warmth": 0.8},
            },
            {"kind": "lowpass", "params": {"cutoff": 7000.0, "q": 0.6}},
            {"kind": "compress", "params": {"threshold": -18.0, "ratio": 3.0, **_COMP}},
        ],
    },
    # a breakdown's space: the lows kept out of a six-second hall, widened
    "wide_hall": {
        "label": "Wide Hall",
        "units": [
            {"kind": "highpass", "params": {"cutoff": 250.0, "q": 0.7}},
            {"kind": "reverb", "params": {"decay": 6.0, "mix": 0.5, "size": 1.0}},
            {"kind": "width", "params": {"amount": 1.6}},
        ],
    },
    # the last beat of every period (the loop's bar) slows to a halt; in a
    # song, rest its mix at 0 and lift it with a lane on the bar that stops
    "tape_stop": {
        "label": "Tape Stop",
        "units": [{"kind": "stop", "params": {"beats": 1.0, "fall": 1.0, "mix": 1.0}}],
    },
    # --- Just Ice and Nine Inch Wails ----------------------------------------
    # French electro's blown-out bus: a hard drive into the clipper, the
    # offset a clipped lopsided wave keeps taken out, the high-pass's
    # overshoot trimmed at -1 dB (the recipes' own ending)
    "blown_out": {
        "label": "Blown Out",
        "units": [
            {"kind": "drive", "params": {"amount": 4.0, "mix": 0.8, "mode": 1.0}},
            {
                "kind": "clip",
                "params": {"drive": 6.0, "ceiling": -7.0, "soft": 0.3, "mix": 1.0},
            },
            {"kind": "highpass", "params": {"cutoff": 40.0, "q": 0.7}},
            {
                "kind": "clip",
                "params": {"drive": 0.0, "ceiling": -1.0, "soft": 0.6, "mix": 1.0},
            },
        ],
    },
    # industrial rust: crushed, folded and fuzzed, then clipped the same way
    "machine_rust": {
        "label": "Machine Rust",
        "units": [
            {"kind": "bitcrush", "params": {"bits": 9.0, "downsample": 2.0}},
            {"kind": "fold", "params": {"amount": 1.6, "mix": 0.35}},
            {"kind": "drive", "params": {"amount": 5.0, "mix": 0.6, "mode": 2.0}},
            {
                "kind": "clip",
                "params": {"drive": 4.0, "ceiling": -7.0, "soft": 0.2, "mix": 1.0},
            },
            {"kind": "highpass", "params": {"cutoff": 60.0, "q": 0.7}},
            {
                "kind": "clip",
                "params": {"drive": 0.0, "ceiling": -1.0, "soft": 0.6, "mix": 1.0},
            },
        ],
    },
    # --- Psych Haze and Sixties Studio ---------------------------------------
    # neo-psych's wash: slowly phased, into a wavering tape echo on the
    # dotted eighth, drowned in a long room
    "phase_wash": {
        "label": "Phase Wash",
        "units": [
            {"kind": "phaser", "params": {"rate": 0.12, "depth": 0.8, "mix": 0.5}},
            {
                "kind": "tape_echo",
                "params": {
                    "time": 0.3,
                    "feedback": 0.5,
                    "wow": 0.5,
                    "tone": 0.5,
                    "drive": 0.3,
                    "mix": 0.3,
                    "sync": 3.0,
                },
            },
            {"kind": "reverb", "params": {"decay": 5.0, "mix": 0.4, "size": 1.0}},
        ],
    },
    # chorused, flanged and warmed like a tape pushed hard (the offset a
    # valve curve leaves taken out)
    "tape_haze": {
        "label": "Tape Haze",
        "units": [
            {"kind": "chorus", "params": {"rate": 0.3, "depth": 4.0, "mix": 0.4}},
            {
                "kind": "flanger",
                "params": {"rate": 0.15, "depth": 2.5, "feedback": 0.5, "mix": 0.35},
            },
            {"kind": "drive", "params": {"amount": 1.6, "mix": 0.5, "mode": 3.0}},
            {"kind": "highpass", "params": {"cutoff": 40.0, "q": 0.7}},
        ],
    },
    # artificial double tracking: a wavering copy a few ms late, the pair
    # panned apart, a short plate
    "double_track": {
        "label": "Double Track",
        "units": [
            {
                "kind": "doubler",
                "params": {"time": 0.028, "wobble": 0.6, "spread": 0.75, "mix": 1.0},
            },
            {
                "kind": "plate",
                "params": {"decay": 1.6, "tone": 0.6, "predelay": 0.012, "mix": 0.2},
            },
        ],
    },
    # a rotating speaker turning slowly, into a short plate
    "rotary_room": {
        "label": "Rotary Room",
        "units": [
            {"kind": "rotary", "params": {"speed": 0.0, "drive": 0.8, "mix": 1.0}},
            {
                "kind": "plate",
                "params": {"decay": 1.8, "tone": 0.6, "predelay": 0.012, "mix": 0.2},
            },
        ],
    },
    # one quick dark echo off the tape machine, then the studio's plate
    "slap_plate": {
        "label": "Slap Plate",
        "units": [
            {
                "kind": "tape_echo",
                "params": {
                    "time": 0.11,
                    "feedback": 0.12,
                    "wow": 0.15,
                    "tone": 0.4,
                    "drive": 0.45,
                    "mix": 0.4,
                    "sync": 0.0,
                },
            },
            {
                "kind": "plate",
                "params": {"decay": 2.0, "tone": 0.6, "predelay": 0.012, "mix": 0.25},
            },
        ],
    },
    # --- the FX2 units, into the packs ---------------------------------------
    # filter house's sweep on its own: a resonant low-pass opening and
    # closing over two bars, and the pump
    "filter_sweep": {
        "label": "Filter Sweep",
        "units": [
            {
                "kind": "autofilter",
                "params": {
                    "mode": 0.0,
                    "cutoff": 300.0,
                    "q": 4.0,
                    "depth": 4.0,
                    "rate": 0.5,
                    "sync": 2.0,
                    "shape": 0.0,
                    "env": 0.0,
                    "sens": 6.0,
                    "attack": 0.01,
                    "release": 0.25,
                    "mix": 1.0,
                },
            },
            {"kind": "pump", "params": {"depth": 0.7, "release": 0.3, "beats": 1.0}},
        ],
    },
    # neo-psych: pushed into a wavering tape, then panned on the quarter
    "psych_tape": {
        "label": "Psych Tape",
        "units": [
            {
                "kind": "tape",
                "params": {
                    "drive": 9.0,
                    "bump": 2.0,
                    "tone": 0.4,
                    "wow": 0.5,
                    "flutter": 0.3,
                    "hiss": 0.2,
                    "mix": 1.0,
                },
            },
            {
                "kind": "tremolo",
                "params": {
                    "rate": 4.0,
                    "sync": 5.0,
                    "depth": 0.5,
                    "shape": 0.0,
                    "phase": 180.0,
                },
            },
        ],
    },
    # the 60s: warmed on tape, into a dripping spring tank
    "sixties_spring": {
        "label": "Sixties Spring",
        "units": [
            {
                "kind": "tape",
                "params": {
                    "drive": 4.0,
                    "bump": 1.5,
                    "tone": 0.5,
                    "wow": 0.2,
                    "flutter": 0.2,
                    "hiss": 0.0,
                    "mix": 1.0,
                },
            },
            {
                "kind": "spring",
                "params": {"decay": 2.5, "tone": 0.5, "drip": 0.7, "mix": 0.3},
            },
        ],
    },
    # a slow spiral: shifted 3 Hz and fed back, bounced side to side
    "barber_pole": {
        "label": "Barber Pole",
        "units": [
            {
                "kind": "freqshift",
                "params": {"shift": 3.0, "feedback": 0.6, "mix": 0.5},
            },
            {
                "kind": "pingpong",
                "params": {
                    "time": 0.375,
                    "sync": 3.0,
                    "feedback": 0.4,
                    "lowcut": 120.0,
                    "highcut": 6000.0,
                    "width": 1.0,
                    "mix": 0.25,
                },
            },
        ],
    },
    # the input speaks through a triad in the song's key, its top lifted
    "robot_voice": {
        "label": "Robot Voice",
        "units": [
            {
                "kind": "vocoder_fx",
                "params": {
                    "key": 0.0,
                    "scale": 0.0,
                    "chord": 0.0,
                    "octave": 3.0,
                    "bands": 20.0,
                    "shift": 0.0,
                    "noise": 0.05,
                    "mix": 1.0,
                },
            },
            {
                "kind": "exciter",
                "params": {"freq": 4000.0, "drive": 6.0, "amount": 0.3},
            },
        ],
    },
    # --- the mix's dynamics into the packs -----------------------------------
    # the over-the-top squash at a little over half: tails and breaths up,
    # the peaks down, a band at a time
    "ott_squash": {
        "label": "OTT Squash",
        "units": [
            {
                "kind": "ott",
                "params": {
                    "depth": 0.6,
                    "time": 1.0,
                    "upward": 1.0,
                    "downward": 1.0,
                    "low": 0.0,
                    "mid": 0.0,
                    "high": 0.0,
                    "output": -2.0,
                },
            }
        ],
    },
    # trance's breakdown space: the lows kept out of a six-second room (its
    # own 20 ms pre-delay), the top lifted, wider than Wide Hall
    "big_room_hall": {
        "label": "Big Room Hall",
        "units": [
            {"kind": "highpass", "params": {"cutoff": 220.0, "q": 0.7}},
            {"kind": "reverb", "params": {"decay": 6.0, "mix": 0.5, "size": 1.0}},
            {
                "kind": "eq",
                "params": {
                    "low": -3.0,
                    "mid_freq": 1000.0,
                    "mid_gain": 0.0,
                    "mid_q": 1.0,
                    "high": 4.0,
                },
            },
            {"kind": "width", "params": {"amount": 1.8}},
        ],
    },
    # a room ducked on the beat: it swells back between the hits (the clock,
    # so it works with no kit)
    "ducked_verb": {
        "label": "Ducked Verb",
        "units": [
            {"kind": "highpass", "params": {"cutoff": 150.0, "q": 0.7}},
            {"kind": "reverb", "params": {"decay": 4.5, "mix": 0.6, "size": 0.95}},
            {"kind": "pump", "params": {"depth": 0.85, "release": 0.4, "beats": 1.0}},
        ],
    },
    # drums with their fronts lifted and their rooms tightened, squashed, the
    # peaks clipped round
    "drum_punch": {
        "label": "Drum Punch",
        "units": [
            {
                "kind": "transient",
                "params": {"attack": 0.6, "sustain": -0.2, "output": 0.0},
            },
            {"kind": "compress", "params": {"threshold": -18.0, "ratio": 4.0, **_COMP}},
            {
                "kind": "clip",
                "params": {"drive": 3.0, "ceiling": -1.0, "soft": 0.5, "mix": 1.0},
            },
        ],
    },
    # the master's last three: a gentle tilt up, the squash at a quarter, the
    # limiter a little into it
    "master_glue": {
        "label": "Master Glue",
        "units": [
            {
                "kind": "eq",
                "params": {
                    "low": 1.0,
                    "mid_freq": 400.0,
                    "mid_gain": -1.0,
                    "mid_q": 0.7,
                    "high": 1.5,
                },
            },
            {
                "kind": "ott",
                "params": {
                    "depth": 0.2,
                    "time": 1.5,
                    "upward": 1.0,
                    "downward": 1.0,
                    "low": 0.0,
                    "mid": 0.0,
                    "high": 0.0,
                    "output": 0.0,
                },
            },
            {
                "kind": "limiter",
                "params": {
                    "gain": 3.0,
                    "ceiling": -1.0,
                    "release": 0.08,
                    "lookahead": 0.002,
                },
            },
        ],
    },
    # --- hip-hop / trap, drum & bass, dubstep,
    # synthwave, future bass — still units only
    # an 808 pushed into a warm valve and the clipper's ceiling, the dirty
    # recipes' ending (drive -> clip -7 -> high-pass -> soft clip -1)
    "saturated_808": {
        "label": "808 Saturate",
        "units": [
            {"kind": "drive", "params": {"amount": 2.5, "mix": 0.6, "mode": 3.0}},
            {
                "kind": "clip",
                "params": {"drive": 4.0, "ceiling": -7.0, "soft": 0.3, "mix": 1.0},
            },
            {"kind": "highpass", "params": {"cutoff": 25.0, "q": 0.7}},
            {
                "kind": "clip",
                "params": {"drive": 0.0, "ceiling": -1.0, "soft": 0.6, "mix": 1.0},
            },
        ],
    },
    # a drum bus as a sampler of old crunched it: fewer bits, the top
    # rolled off, the fronts lifted, the clipper's ceiling
    "boom_bap_crunch": {
        "label": "Boom Bap Crunch",
        "units": [
            {"kind": "bitcrush", "params": {"bits": 10.0, "downsample": 2.0}},
            {"kind": "lowpass", "params": {"cutoff": 9000.0, "q": 0.7}},
            {
                "kind": "transient",
                "params": {"attack": 0.4, "sustain": -0.1, "output": 0.0},
            },
            {
                "kind": "clip",
                "params": {"drive": 2.0, "ceiling": -1.0, "soft": 0.5, "mix": 1.0},
            },
        ],
    },
    # drum & bass's neuro bass: a hard drive, a comb's metal, squashed over
    # the top, then the clipper's ceiling
    "neuro_grind": {
        "label": "Neuro Grind",
        "units": [
            {"kind": "drive", "params": {"amount": 4.0, "mix": 0.7, "mode": 1.0}},
            {"kind": "comb", "params": {"tune": 180.0, "feedback": 0.5, "mix": 0.25}},
            {
                "kind": "ott",
                "params": {
                    "depth": 0.7,
                    "time": 1.0,
                    "upward": 1.0,
                    "downward": 1.0,
                    "low": 0.0,
                    "mid": 0.0,
                    "high": 0.0,
                    "output": 0.0,
                },
            },
            {
                "kind": "clip",
                "params": {"drive": 3.0, "ceiling": -7.0, "soft": 0.3, "mix": 1.0},
            },
            {"kind": "highpass", "params": {"cutoff": 35.0, "q": 0.7}},
            {
                "kind": "clip",
                "params": {"drive": 0.0, "ceiling": -1.0, "soft": 0.6, "mix": 1.0},
            },
        ],
    },
    # dubstep's growl: folded, fuzzed and squashed hard, the same ceiling
    "growl_squash": {
        "label": "Growl Squash",
        "units": [
            {"kind": "fold", "params": {"amount": 1.8, "mix": 0.4}},
            {"kind": "drive", "params": {"amount": 3.0, "mix": 0.6, "mode": 2.0}},
            {
                "kind": "ott",
                "params": {
                    "depth": 0.8,
                    "time": 1.0,
                    "upward": 1.0,
                    "downward": 1.0,
                    "low": 0.0,
                    "mid": 0.0,
                    "high": 0.0,
                    "output": 0.0,
                },
            },
            {
                "kind": "clip",
                "params": {"drive": 4.0, "ceiling": -7.0, "soft": 0.3, "mix": 1.0},
            },
            {"kind": "highpass", "params": {"cutoff": 40.0, "q": 0.7}},
            {
                "kind": "clip",
                "params": {"drive": 0.0, "ceiling": -1.0, "soft": 0.6, "mix": 1.0},
            },
        ],
    },
    # the 80s gated room: a big bright room, then a gate that shuts it the
    # moment the hit's level falls — the tail cut dead instead of dying
    "gated_verb": {
        "label": "Gated Verb",
        "units": [
            {"kind": "reverb", "params": {"decay": 2.5, "mix": 0.5, "size": 0.9}},
            {
                "kind": "vgate",
                "params": {
                    "threshold": -24.0,
                    "ratio": 20.0,
                    "depth": 70.0,
                    "release": 0.03,
                },
            },
        ],
    },
    # synthwave's space: a chorus, a dotted echo side to side, a room
    "eighties_space": {
        "label": "Eighties Space",
        "units": [
            {"kind": "chorus", "params": {"rate": 0.5, "depth": 3.5, "mix": 0.5}},
            {
                "kind": "delay",
                "params": {
                    "time": 0.375,
                    "feedback": 0.35,
                    "mix": 0.25,
                    "ping_pong": 1.0,
                    "sync": 3.0,
                },
            },
            {"kind": "reverb", "params": {"decay": 3.5, "mix": 0.3, "size": 0.9}},
        ],
    },
    # future bass's signature: squashed over the top, a room, then pumped
    # hard on the beat (the room with it: after the duck, the room filled
    # it back in to -5.8 dB), wider than wide
    "future_pump": {
        "label": "Future Pump",
        "units": [
            {
                "kind": "ott",
                "params": {
                    "depth": 0.7,
                    "time": 1.0,
                    "upward": 1.0,
                    "downward": 1.0,
                    "low": 0.0,
                    "mid": 0.0,
                    "high": 0.0,
                    "output": 2.0,
                },
            },
            {"kind": "reverb", "params": {"decay": 2.5, "mix": 0.25, "size": 0.8}},
            {"kind": "pump", "params": {"depth": 0.9, "release": 0.3, "beats": 1.0}},
            {"kind": "width", "params": {"amount": 1.6}},
        ],
    },
    # --- lo-fi hip-hop, ambient /
    # cinematic, chiptune, hardstyle, phonk — units only
    # lo-fi's tape: wow and flutter, hiss, the head bump, the top rolled
    # off, a few bits gone
    "dusty_tape": {
        "label": "Dusty Tape",
        "units": [
            {
                "kind": "tape",
                "params": {
                    "drive": 4.0,
                    "bump": 2.0,
                    "tone": 0.45,
                    "wow": 0.5,
                    "flutter": 0.35,
                    "hiss": 0.25,
                    "mix": 1.0,
                },
            },
            {"kind": "lowpass", "params": {"cutoff": 5500.0, "q": 0.7}},
            {"kind": "bitcrush", "params": {"bits": 12.0, "downsample": 1.0}},
        ],
    },
    # the cinematic room: lows kept out, six seconds of hall, wider than
    # the speakers
    "huge_hall": {
        "label": "Huge Hall",
        "units": [
            {"kind": "highpass", "params": {"cutoff": 150.0, "q": 0.7}},
            {"kind": "reverb", "params": {"decay": 6.0, "mix": 0.5, "size": 1.0}},
            {"kind": "width", "params": {"amount": 1.5}},
        ],
    },
    # ambient's wash: a room whose tail climbs an octave each time round
    # (the shimmer unit: whole-buffer, so a key under it plays the dry WAV)
    "shimmer_wash": {
        "label": "Shimmer Wash",
        "units": [
            {"kind": "highpass", "params": {"cutoff": 200.0, "q": 0.7}},
            {
                "kind": "shimmer",
                "params": {"decay": 6.0, "mix": 0.45, "shift": 12.0, "size": 1.0},
            },
            {"kind": "width", "params": {"amount": 1.4}},
        ],
    },
    # the 8-bit console's: six bits at a third of the rate, the clipper's
    # ceiling
    "chip_crush": {
        "label": "8-Bit Crush",
        "units": [
            {"kind": "bitcrush", "params": {"bits": 6.0, "downsample": 3.0}},
            {"kind": "highpass", "params": {"cutoff": 40.0, "q": 0.7}},
            {
                "kind": "clip",
                "params": {"drive": 0.0, "ceiling": -1.0, "soft": 0.3, "mix": 1.0},
            },
        ],
    },
    # hardstyle's kick: driven hard into a clipper, the fronts lifted, the
    # tail kept loud enough to carry its pitch, the ceiling
    "hard_kick_drive": {
        "label": "Hard Kick Drive",
        "units": [
            {"kind": "drive", "params": {"amount": 6.0, "mix": 1.0, "mode": 1.0}},
            {
                "kind": "clip",
                "params": {"drive": 4.0, "ceiling": -7.0, "soft": 0.2, "mix": 1.0},
            },
            {"kind": "highpass", "params": {"cutoff": 30.0, "q": 0.7}},
            {
                "kind": "transient",
                "params": {"attack": 0.5, "sustain": 0.2, "output": 0.0},
            },
            {
                "kind": "clip",
                "params": {"drive": 0.0, "ceiling": -1.0, "soft": 0.6, "mix": 1.0},
            },
        ],
    },
    # phonk's dirt: fuzz, a sampler's lost bits, the clipper (-7), the
    # fuzz's offset taken out, the ceiling — five units, so a bass on it
    # still has room for a duck
    "phonk_crush": {
        "label": "Phonk Crush",
        "units": [
            {"kind": "drive", "params": {"amount": 3.0, "mix": 0.6, "mode": 2.0}},
            {"kind": "bitcrush", "params": {"bits": 10.0, "downsample": 2.0}},
            {
                "kind": "clip",
                "params": {"drive": 2.0, "ceiling": -7.0, "soft": 0.3, "mix": 1.0},
            },
            {"kind": "highpass", "params": {"cutoff": 30.0, "q": 0.7}},
            {
                "kind": "clip",
                "params": {"drive": 0.0, "ceiling": -1.0, "soft": 0.6, "mix": 1.0},
            },
        ],
    },
}


def chain_preset(name: str) -> list[dict] | None:
    """A fresh, clean copy of a chain preset's units, or None."""
    preset = CHAIN_PRESETS.get(name)
    return clean_chain(copy.deepcopy(preset["units"])) if preset else None


# --- the chain browser ("+ add chain…": fragments/fx_rack.html) -------------
# Every chain is listed by what it is for (`group`) or by the genre pack it
# came with (`pack`), each with a one-line `blurb`. Keys, in the order the
# browser lists them; a chain whose key is missing or unknown is listed
# under "Other" (tests/studio/test_fx_chains.py fails on it, so it gets one).
CHAIN_GROUPS: dict[str, str] = {
    "space": "Space",
    "drive": "Drive & colour",
    "dynamics": "Dynamics",
    "motion": "Motion",
    "vocal": "Vocal",
    "master": "Master",
}
CHAIN_PACKS: dict[str, str] = {
    "melodic_techno": "Melodic techno",
    "garage": "Garage",
    "house": "Filter house",
    "trance": "Trance",
    "electro": "French electro",
    "industrial": "Industrial",
    "psych": "Psych",
    "sixties": "60s studio",
    "round_four": "Mix and glue",
    "hiphop": "Hip-hop / trap",
    "dnb": "Drum & bass",
    "dubstep": "Dubstep",
    "synthwave": "Synthwave",
    "future_bass": "Future bass",
    "lofi": "Lo-fi",
    "ambient": "Ambient",
    "chiptune": "Chiptune",
    "hardstyle": "Hardstyle",
    "phonk": "Phonk",
}
# The chains above: (group, pack, blurb), merged onto each preset below. A
# chain added later may carry its three keys in its own dict instead.
# fmt: off
_CHAIN_INFO: dict[str, tuple[str, str, str]] = {
    "phone_mic": ("vocal", "garage",
        "A voice through a handset: a small room, the phone's band, squashed, mono."),
    "mono_maker": ("master", "garage",
        "Stereo folded to mono: a sub or a bass that must sit dead centre."),
    "washy": ("space", "garage",
        "A dark dotted-eighth echo side to side, into a long wide room."),
    "pump_quarter": ("dynamics", "melodic_techno",
        "Ducks on every beat of the clock, kit or no kit: the pump."),
    "tape_lofi": ("drive", "garage",
        "Crackle, hiss and wow, a few bits and half the rate gone, a warm clip."),
    "filter_disco": ("motion", "house",
        "A resonant low-pass to sweep, a slow phaser and the pump."),
    "hard_duck": ("dynamics", "house",
        "Ducked under the kit's own kick: gone on the hit, back in 0.3 s."),
    "crush_bus": ("drive", "house",
        "Bits reduced, compressed hard, driven: crunch as character."),
    "dotted_ping_pong": ("space", "trance",
        "The pluck's echo: dotted eighths, left then right."),
    "trance_gate": ("motion", "trance",
        "The trance chop into a room, so the gaps fill with the tail."),
    "tape_wear": ("drive", "garage",
        "Gentler than Tape Lo-fi: wow, the top rolled off, a slow compressor."),
    "wide_hall": ("space", "melodic_techno",
        "A breakdown's space: the lows kept out of a six-second hall, widened."),
    "tape_stop": ("motion", "garage",
        "The last beat of every bar slows to a halt (lift its mix with a lane)."),
    "blown_out": ("drive", "electro",
        "French electro's blown-out bus: a hard drive into the clipper."),
    "machine_rust": ("drive", "industrial",
        "Crushed, folded and fuzzed, then clipped: industrial rust."),
    "phase_wash": ("space", "psych",
        "Slowly phased, a wavering dotted tape echo, drowned in a long room."),
    "tape_haze": ("motion", "psych",
        "Chorused, flanged and warmed like a tape pushed hard."),
    "double_track": ("vocal", "sixties",
        "Double tracking: a wavering copy a few ms late, panned apart, a plate."),
    "rotary_room": ("motion", "sixties",
        "A rotating speaker turning slowly, into a short plate."),
    "slap_plate": ("space", "sixties",
        "One quick dark slap off the tape machine, then the studio's plate."),
    "filter_sweep": ("motion", "house",
        "A resonant filter opening and closing over two bars, and the pump."),
    "psych_tape": ("motion", "psych",
        "Pushed into a wavering tape, then panned on the quarter note."),
    "sixties_spring": ("space", "sixties",
        "Warmed on tape, into a dripping spring tank."),
    "barber_pole": ("motion", "psych",
        "A slow spiral: shifted 3 Hz and fed back, bounced side to side."),
    "robot_voice": ("vocal", "electro",
        "The input speaks through a triad in the song's key, its top lifted."),
    "ott_squash": ("dynamics", "round_four",
        "Over-the-top squash at a little over half: tails up, peaks down."),
    "big_room_hall": ("space", "trance",
        "Trance's breakdown room: no lows, six seconds, the top lifted, very wide."),
    "ducked_verb": ("space", "round_four",
        "A room ducked on the beat that swells back between the hits."),
    "drum_punch": ("dynamics", "round_four",
        "Drums with their fronts lifted, squashed, the peaks clipped round."),
    "master_glue": ("master", "round_four",
        "The master's last three: a gentle tilt, a little OTT, the limiter."),
    "saturated_808": ("drive", "hiphop",
        "An 808 pushed into a warm valve and the clipper's ceiling."),
    "boom_bap_crunch": ("drive", "hiphop",
        "A drum bus as an old sampler crunched it: fewer bits, a dull top, punch."),
    "neuro_grind": ("drive", "dnb",
        "Neuro bass: a hard drive, a comb's metal, squashed, clipped."),
    "growl_squash": ("drive", "dubstep",
        "Dubstep's growl: folded, fuzzed and squashed hard."),
    "gated_verb": ("space", "synthwave",
        "The 80s gated room: a big bright room cut dead as the hit falls."),
    "eighties_space": ("space", "synthwave",
        "Synthwave's space: a chorus, a dotted echo side to side, a room."),
    "future_pump": ("dynamics", "future_bass",
        "Squashed over the top, a room, pumped hard on the beat, wide."),
    "dusty_tape": ("drive", "lofi",
        "Lo-fi tape: wow and flutter, hiss, the top rolled off, a few bits gone."),
    "huge_hall": ("space", "ambient",
        "A cinematic six-second hall, lows kept out, wider than the speakers."),
    "shimmer_wash": ("space", "ambient",
        "An ambient wash: a room whose tail climbs an octave each time round."),
    "chip_crush": ("drive", "chiptune",
        "The 8-bit console: six bits at a third of the rate, clipped."),
    "hard_kick_drive": ("drive", "hardstyle",
        "Hardstyle's kick: driven into a clipper, the front lifted, the tail loud."),
    "phonk_crush": ("drive", "phonk",
        "Phonk's dirt: fuzz, a sampler's lost bits, clipped hard."),
}
# fmt: on
for _key, (_group, _pack, _blurb) in _CHAIN_INFO.items():
    CHAIN_PRESETS[_key].update(group=_group, pack=_pack, blurb=_blurb)


def chain_browser() -> dict:
    """The chain browser's rows and headings: each chain once (its place
    in the table, key, label, blurb and unit path), its group and
    pack ("other" when it names none we know), and both headings' orders
    with "Other" last. The template lists the rows by group; the island
    regroups them by pack (static/panelfaces.js)."""
    groups = {**CHAIN_GROUPS, "other": "Other"}
    packs = {**CHAIN_PACKS, "other": "Other"}
    rows = []
    for i, (key, preset) in enumerate(CHAIN_PRESETS.items()):
        group = preset.get("group") if preset.get("group") in CHAIN_GROUPS else "other"
        pack = preset.get("pack") if preset.get("pack") in CHAIN_PACKS else "other"
        path = " → ".join(
            FX_UNITS.get(u.get("kind"), {}).get("label", "?") for u in preset["units"]
        )
        blurb = preset.get("blurb", "")
        rows.append(
            {
                "i": i,
                "key": key,
                "label": preset["label"],
                "blurb": blurb,
                "path": path,
                "group": group,
                "pack": pack,
                "pack_label": packs[pack],
            }
        )
    by_group = {g: [r for r in rows if r["group"] == g] for g in groups}
    return {"groups": groups, "packs": packs, "by_group": by_group, "count": len(rows)}


def clean_chain(units) -> list[dict]:
    """Validated copy of a stored chain: known kinds, clamped params."""
    out: list[dict] = []
    ids: set[str] = set()
    for u in units if isinstance(units, list) else []:
        if not isinstance(u, dict):
            continue
        kind = u.get("kind")
        spec = FX_UNITS.get(kind) if isinstance(kind, str) else None
        if spec is None:
            continue
        uid = u.get("id")
        params: dict = {}
        raw = u.get("params")
        raw = raw if isinstance(raw, dict) else {}
        for name, (lo, hi, default) in spec["params"].items():
            try:
                v = float(raw.get(name, default))
            except (TypeError, ValueError, OverflowError):  # a 400-digit JSON int
                v = default
            params[name] = min(hi, max(lo, v))
            if name in spec.get("choices", ()):  # a choice: its index
                params[name] = float(round(params[name]))
        unit = {"kind": kind, "params": params}
        if "pattern" in spec:
            pat = str(u.get("pattern", spec["pattern"]))
            unit["pattern"] = pat if _PATTERN_RE.fullmatch(pat) else spec["pattern"]
        # a unit's identity, kept only where it has one (`unit_id`): the
        # second unit claiming an id loses it, so an id names one unit
        if unit_id_ok(uid) and uid not in ids:
            ids.add(uid)
            unit["id"] = uid
        out.append(unit)
    return out[:MAX_UNITS]


# --- one edit of a rack, and a rack as it is drawn ----------------------------
# Every rack edits its chain with `edit_chain` and draws it with `rack_rows`
# (the shared macro, `templates/fragments/fx_units.html`): the session's
# scopes (`routes/fx.py`: the layer, master and drum-bus chains), a song's
# audio track (`mixer.edit_rack`) and the Voice page's booth (`booth.py`).

RACK_OPS = ("add", "remove", "move", "set")
# a set's names that are a press, not a value dragged: each its own undo
# step (`mixer.edit_rack` gives them no key to join on)
RACK_PRESSES = ("recall", "step", "steps", "preset")


class RackError(ValueError):
    """A rack edit refused, in words a toast can say."""


def _rack_index(chain: list, raw) -> int:
    try:
        i = int(str(raw).strip())
    except ValueError:
        i = -1
    if not 0 <= i < len(chain):
        raise RackError("That effect is no longer in the rack.")
    return i


def _rack_number(raw, what: str) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = math.nan
    if not math.isfinite(v):
        raise RackError(f"{what.capitalize()} must be a number.")
    return v


def _set_unit(unit: dict, name: str, value) -> None:
    """A set's one change to `unit` (in place; `clean_chain` clamps it
    after): a parameter, the pattern string, a 16th of the step editor
    toggled (`step`), its length (`steps` 16 | 32: the pattern repeated or
    cut), a gate's named rhythm (`preset`, `GATE_PATTERNS`) or a preset
    recalled (`recall`: `FX_PRESETS` by name, or "defaults")."""
    spec = FX_UNITS[unit["kind"]]
    if name == "pattern" and "pattern" in spec:
        unit["pattern"] = str(value)
    elif name in ("step", "steps") and "pattern" in spec:
        pat = str(unit.get("pattern") or spec["pattern"])
        n = int(_rack_number(value, name))
        if name == "steps" and n in (16, 32):
            unit["pattern"] = (pat * 32)[:n]
        elif name == "step" and 0 <= n < len(pat):
            unit["pattern"] = pat[:n] + ("." if pat[n] == "x" else "x") + pat[n + 1 :]
        else:
            raise RackError("That pattern has no such step.")
    elif name == "preset" and unit["kind"] == "gate":
        pattern = gate_pattern(value)
        if pattern is None:
            raise RackError("No such rhythm.")
        unit["pattern"] = pattern
    elif name == "recall":
        params = unit_preset(unit["kind"], str(value))
        if params is None:
            raise RackError(f"{spec['label']} has no such preset.")
        unit["params"] = params
    elif name in spec.get("params", {}):
        unit["params"][name] = _rack_number(value, name)
    else:
        raise RackError("That effect has no such setting.")


def edit_chain(units, op: str, values) -> tuple[list[dict], str, int | None]:
    """One edit of a stored chain: `op` add (`kind`), remove (`index`), move
    (`index`, `dir` -1 | 1) or set (`index`, `name`, `value`: a parameter,
    the pattern, a step, the steps, a gate rhythm or a recalled preset —
    `_set_unit`; clamped by `clean_chain`). Returns (the clean chain, the
    edit in words, the index of the unit a set moved — None for the
    others). The chain handed in is not changed. Refused (`RackError`): an
    unknown op or kind, a full rack (`MAX_UNITS`), a unit that is gone, a
    setting it does not have, a value that is not a number. A move carries
    the unit as it is, id included, so a lane follows it."""
    chain = copy.deepcopy(clean_chain(units))
    at = None
    if op == "add":
        kind = values.get("kind", "")
        if kind not in FX_UNITS:
            raise RackError("No such effect.")
        if len(chain) >= MAX_UNITS:
            raise RackError(f"That rack is full ({MAX_UNITS} units).")
        chain.append({"kind": kind, "params": {}})
        said = f"add {FX_UNITS[kind]['label']}"
    elif op == "remove":
        gone = chain.pop(_rack_index(chain, values.get("index", "")))
        said = f"remove {FX_UNITS[gone['kind']]['label']}"
    elif op == "move":
        i = _rack_index(chain, values.get("index", ""))
        to = i + (1 if str(values.get("dir")) == "1" else -1)
        if not 0 <= to < len(chain):
            raise RackError("That effect is at the end of the rack already.")
        chain[i], chain[to] = chain[to], chain[i]
        said = "move effect"
    elif op == "set":
        at = _rack_index(chain, values.get("index", ""))
        name, value = values.get("name", ""), values.get("value", "")
        _set_unit(chain[at], name, value)
        label = FX_UNITS[chain[at]["kind"]]["label"]
        said = f"{label}: {value}" if name == "recall" else f"{label} {name}"
    else:
        raise RackError("No such rack edit.")
    return clean_chain(chain), said, at


def rack_rows(units) -> list[dict]:
    """A chain as a rack draws it (`fragments/fx_units.html`): a row a unit
    — its index, kind, the kind's label (`name`) and the unit's (`label`:
    numbered when the chain holds two of its kind), its parameters (a dict
    each: name, lo, hi, default, value, and `choices`, the labels, for one
    that is a choice), its pattern string when it has one, its ★ presets'
    names, a gate's rhythms `(key, label, pattern)`, and whether it is the
    first or the last."""
    chain = clean_chain(list(units or []))
    count: dict[str, int] = {}
    rows = []
    for i, u in enumerate(chain):
        spec = FX_UNITS[u["kind"]]
        count[u["kind"]] = count.get(u["kind"], 0) + 1
        label = spec["label"]
        if sum(x["kind"] == u["kind"] for x in chain) > 1:
            label = f"{label} {count[u['kind']]}"
        choices = spec.get("choices") or {}
        rows.append(
            {
                "index": i,
                "kind": u["kind"],
                "name": spec["label"],
                "label": label,
                "params": [
                    {
                        "name": name,
                        "lo": lo,
                        "hi": hi,
                        "default": default,
                        "value": u["params"][name],
                        "choices": choices.get(name),
                    }
                    for name, (lo, hi, default) in spec.get("params", {}).items()
                ],
                "pattern": u.get("pattern"),
                "presets": list(FX_PRESETS.get(u["kind"], {})),
                "rhythms": (
                    [(k, g["label"], g["pattern"]) for k, g in GATE_PATTERNS.items()]
                    if u["kind"] == "gate"
                    else []
                ),
                "first": i == 0,
                "last": i == len(chain) - 1,
            }
        )
    return rows


def unit_id(chain: list[dict], index: int) -> str:
    """The id of the unit at `index` of a clean chain, given one now if it
    had none. Units are otherwise anonymous (a chain is a list of `{kind,
    params}`); an id is what lets a song's automation lane follow its unit
    through a reorder (`curves.FX_LANES`). Only a unit a lane targets ever
    gets one, so every other chain is stored as it always was."""
    unit = chain[index]
    if not unit_id_ok(unit.get("id")):
        taken = {u.get("id") for u in chain}
        while (uid := secrets.token_hex(UNIT_ID_LEN // 2)) in taken:  # pragma: no cover
            pass
        unit["id"] = uid
    return unit["id"]


def _beat_times(arr: Arrangement, beats: float, span: float = 0.0) -> list[float]:
    """Trigger times on the clock rather than on the kit. Generated well past
    any loop (and past `span` seconds, for a chain run over a whole song or
    a long section) — _sidechain drops the ones that land past the buffer."""
    period = max(0.25, float(beats)) * 60.0 / arr.bpm
    return [i * period for i in range(max(256, int(span / period) + 2))]


def _kick_times(arr: Arrangement, span: float = 0.0) -> list[float]:
    """Kick hits in seconds, on the *drum machine's* clock — the kit has its
    own bars/steps, and reading the synth grid's step count put every
    trigger in the wrong place whenever the two grids disagreed. Repeated
    past the loop (at least 8 times, and past `span` seconds) so a chain
    running over more than one repeat still ducks; _sidechain drops
    whatever lands past the buffer."""
    beats = _meter.bar_beats(getattr(arr, "meter", "4/4"))
    step_seconds = (beats / arr.drum_steps) * 60.0 / arr.bpm
    loop = arr.drum_bars * arr.drum_steps * step_seconds
    hits = [step * step_seconds for step in kick_steps(arr)]
    if getattr(arr, "ramp", None):  # a ramped section: where each beat sounds
        clock = _meter.arr_clock(arr)
        step_beats = beats / arr.drum_steps
        loop = clock.seconds(arr.drum_bars)
        hits = [clock.at(step * step_beats) for step in kick_steps(arr)]
    reps = max(8, int(span / loop) + 2)
    return [t + r * loop for r in range(reps) for t in hits]


def kick_steps(arr: Arrangement) -> list[int]:
    """The kick steps a sidechain ducks on: the ones the drum machine plays,
    inside its own grid (`arrange._schedule_drums` drops the rest). A hit
    kept, hidden, past a shrunk grid used to duck where no kick sounds, and
    so did a kick row that was muted or silenced by another row's solo
    (`drum_row_sounds`, the scheduler's own rule) — a duck under nothing.
    The stem key reads this too (`render._chain_clock`), so mute and solo
    reach it as the hits they leave, and a session with neither keeps its
    key.

    Probability is not followed, except 0 (the row never plays): the duck
    is on every programmed step of a sounding kick row, on the grid (no
    swing, no humanise — as before). `arrange.drum_hits`' roll is seeded
    from the arrangement it is handed, and a song section renders its
    drums in chunks, each rolled from its own window, while its chains
    run once over the whole cycle: there is no one roll to follow. For a
    duck with no audible kick, use the `pump` unit.

    Per-hit data (`arr.drum_hits`) follows the same rule, hit by hit: a
    kick whose own chance is 0 never plays and never ducks, and any other
    chance is not followed. What a hit's data says about WHEN is followed,
    because it is a fact of the pattern and not a roll: a **nudged** kick
    ducks where it lands (a step number with a fraction; one nudged to
    before step 0 lands at the loop's end, as it sounds), and a
    **ratcheted** kick ducks on every retrigger. Swing — the pattern's or
    the row's own — is still not followed. A pattern with no per-hit data
    answers the whole numbers it always did."""
    mix = (getattr(arr, "drum_mix", {}) or {}).get("kick") or {}
    if not drum_row_sounds(arr, "kick") or float(mix.get("prob", 1.0)) <= 0.0:
        return []
    total = arr.drum_bars * arr.drum_steps
    steps = [
        int(step)
        for name, step in arr.drums
        if name == "kick" and 0 <= int(step) < total
    ]
    data = getattr(arr, "drum_hits", None)
    if not data:
        return steps
    out: list = []
    for s in steps:
        h = data.get(f"kick.{s}")
        if not h:
            out.append(s)
            continue
        if float(h.get("p", 1.0)) <= 0.0:
            continue
        at = s + float(h.get("n", 0.0))
        if at < 0:
            at += total
        r = max(1, min(4, int(h.get("r", 1))))
        out += [s if at == s and j == 0 else round(at + j / r, 6) for j in range(r)]
    return out


def build_chain(
    units: list[dict],
    arr: Arrangement,
    *,
    span: float = 0.0,
) -> list:
    """Stored chain -> effects.chain() callables. bpm-aware units (gate,
    sidechain) read timing from the arrangement. `span` is how many
    seconds the chain will run over, so the clocked triggers (sidechain,
    pump) reach its end (a song section's cycle and tail); the default
    leaves a loop's chain exactly as it was."""
    chain: list = []
    for u in clean_chain(units):
        name, kw = unit_args(u, arr, span)
        chain.append(getattr(fx, name)(**kw))
    return chain


def unit_args(u: dict, arr: Arrangement, span: float = 0.0) -> tuple[str, dict]:
    """A clean unit -> (effects factory name, everything it is called
    with): `resolve_unit` plus the trigger times a sidechain or a pump
    ducks on, reaching `span` seconds. `build_chain` calls the factory with
    these; `fxauto` swaps a parameter for a per-sample array first."""
    name, kw = resolve_unit(u, arr)
    if name == "sidechain":  # a clock: the trigger times it ducks on
        beats = kw.pop("beats", None)
        kw["trigger_times"] = (
            _kick_times(arr, span) if beats is None else _beat_times(arr, beats, span)
        )
    return name, kw


def repeat_period(arr: Arrangement) -> float:
    """The beats a beat repeat's pattern and chance rolls — and a tape
    stop's windows — cycle over: the
    gcd of the synth grid's and the drum machine's bars. It divides every
    stem's own loop (either clock), a mixed loop (their lcm) and a song
    section's cycle, so the effect is periodic at every length a render
    wrap-tiles or loops — which is what keeps it loop-safe."""
    bars = math.gcd(max(1, int(arr.bars)), max(1, int(arr.drum_bars)))
    return _meter.bar_beats(getattr(arr, "meter", "4/4")) * bars


# tempo-synced delay: 0 off · 1 1/4 · 2 1/8 · 3 dotted 1/8 · 4 1/16 · 5 triplet
_DELAY_BEATS = {1: 1.0, 2: 0.5, 3: 0.75, 4: 0.25, 5: 1.0 / 3.0}


def resolve_unit(u: dict, arr: Arrangement) -> tuple[str, dict]:
    """A clean unit -> (effects factory name, its keyword arguments), with
    the arrangement's tempo resolved. The one reading of a unit's params:
    `build_chain` renders it, `fxlive.live_chain` hands the same numbers to
    the live keys. A sidechain's triggers are left to the caller (`beats`
    for a pump's clock, none for the kick's)."""
    kind, p = u["kind"], u["params"]
    if kind == "gate":
        return "gate", {"pattern": u["pattern"], "bpm": arr.bpm, "depth": p["depth"]}
    if kind == "sidechain":
        return "sidechain", {"depth": p["depth"], "release": p["release"]}
    if kind == "pump":
        return "sidechain", {
            "depth": p["depth"],
            "release": p["release"],
            "beats": p["beats"],
        }
    if kind in ("delay", "tape_echo"):
        # tempo sync overrides the raw-seconds slider so echoes sit on
        # the grid of a bar-locked loop
        sync = int(p.get("sync", 0))
        time = (
            _DELAY_BEATS[sync] * 60.0 / arr.bpm if sync in _DELAY_BEATS else p["time"]
        )
        if kind == "tape_echo":
            rest = {k: v for k, v in p.items() if k not in ("time", "sync")}
            return "tape_echo", {"time": time, **rest}
        return "delay", {
            "time": time,
            "feedback": p["feedback"],
            "mix": p["mix"],
            "ping_pong": p["ping_pong"] >= 0.5,
        }
    if kind == "pitch":  # whole semitones: a knob between two is no interval
        return "pitch", {"semis": float(round(p["semis"])), "mix": p["mix"]}
    if kind == "beat_repeat":
        return "beat_repeat", {
            "bpm": arr.bpm,
            "grid": int(p["grid"]),
            "repeats": int(p["repeats"]),
            "chance": p["chance"],
            "pattern": u["pattern"],
            "pitch": p["pitch"],
            "decay": p["decay"],
            "gate": p["gate"],
            "mix": p["mix"],
            "period": repeat_period(arr),
            "seed": int(p["seed"]),
        }
    if kind == "stop":
        return "stop", {
            "bpm": arr.bpm,
            "beats": p["beats"],
            "fall": p["fall"],
            "mix": p["mix"],
            "period": repeat_period(arr),
        }
    if kind == "bitcrush":
        return "bitcrush", {"bits": int(p["bits"]), "downsample": int(p["downsample"])}
    if kind == "drive":
        mode = fx.DRIVE_MODES[int(p["mode"])]
        return "drive", {"amount": p["amount"], "mix": p["mix"], "mode": mode}
    if kind == "compress":
        # makeup below 0 dB is the automatic one (the factory's None)
        makeup = 10 ** (p["makeup"] / 20) if p["makeup"] >= 0 else None
        return "compress", {
            "threshold": p["threshold"],
            "ratio": p["ratio"],
            "attack": p["attack"],
            "release": p["release"],
            "makeup": makeup,
        }
    if kind in ("lowpass", "highpass"):
        return kind, {"cutoff": p["cutoff"], "q": p["q"]}
    if kind in ("autotune", "harmony"):  # the key and scale, as pitch classes
        key, scale = tune_key(p, arr)
        rest = {k: v for k, v in p.items() if k not in ("key", "scale")}
        if kind == "harmony":
            rest["first"], rest["second"] = int(p["first"]), int(p["second"])
        return kind, {"key": key, "scale": scale, **rest}
    if kind in ("autofilter", "tremolo"):  # a synced rate, the choices' names
        rest = {k: v for k, v in p.items() if k != "sync"}
        beats = LFO_BEATS[int(p["sync"])]
        rest["rate"] = arr.bpm / (60.0 * beats) if beats else p["rate"]
        rest["shape"] = fx.LFO_SHAPES[int(p["shape"])]
        if kind == "autofilter":
            rest["mode"] = fx.AUTOFILTER_MODES[int(p["mode"])]
        return kind, rest
    if kind == "pingpong":  # `sync` as the delay's
        sync = int(p["sync"])
        rest = {k: v for k, v in p.items() if k not in ("time", "sync")}
        time = (
            _DELAY_BEATS[sync] * 60.0 / arr.bpm if sync in _DELAY_BEATS else p["time"]
        )
        return "pingpong", {"time": time, **rest}
    if kind == "vocoder_fx":  # its carrier: the chord's notes in the key, in Hz
        key, scale = tune_key(p, arr)
        chord = tuple(fx.VOCODER_FX_CHORDS)[int(p["chord"])]
        notes = fx.chord_freqs(key, scale, chord, int(round(p["octave"])))
        return "vocoder_fx", {
            "notes": notes,
            "bands": int(round(p["bands"])),
            "shift": p["shift"],
            "noise": p["noise"],
            "mix": p["mix"],
        }
    return kind, dict(p)  # every remaining unit's params map 1:1 onto its factory


def tune_key(p: dict, arr: Arrangement) -> tuple[int, list[int]]:
    """A pitched vocal unit's key (a pitch class, 0 = C) and scale (pitch
    classes over the key), its choices resolved: 0 is the session's key or
    scale (a scale the engine does not know is chromatic)."""
    k = int(p["key"])
    try:
        key = (midi(arr.root) if k == 0 else k - 1) % 12
    except (ValueError, KeyError, TypeError):
        key = 0
    name = TUNE_SCALES[int(p["scale"])]
    if name == "song scale":
        name = arr.scale_name if arr.scale_name in SCALES else "chromatic"
    scale = list(fx.CHROMATIC) if name == "chromatic" else list(SCALES[name])
    return key, scale
