"""The studio's side of the parameter registry: how each Patch field is
shown, and whether the AudioWorklet live voice can play it.

`UI` has exactly one `Ui` per Patch field, in panel order (a field with no
control says why). Everything numeric about a control — its range, log
taper, default, whether it can be drawn — comes from `synthlab.params`,
so a slider can never disagree with the breeder or the clamp.

Built from these: `PARAM_GROUPS` (the editor's panels), `spec_of`,
`CONTINUOUS_*` (XY pad / macro targets), `SUBHEADS`, the mod menus' labels,
`SHAPE_DEPS` (dimmed shape rows), and `worklet_blockers()`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from typing import Any

import numpy as np

from .. import sampler
from ..params import (
    FILTER_TYPES_ALL,
    GENES,
    MOD_DESTINATIONS,
    MOD_SLOTS,
    PARAMS,
    PITCH_ROUTE,
    slot_active,
    uses_shape,
)
from ..synth import Patch

# Panels in signal-flow order: what makes the sound, what filters it, the
# vowel tract, the amp, the character stage, then everything that moves it
# over time — the fixed modulators, then the general ones the
# routes read: envelopes 3 and 4, LFOs 2 and 3. FX follow on the page
# (fx_rack.html) as the last stage.
GROUPS = (
    "Source",
    "Filter",
    "Vowel",
    "Amp envelope",
    "Character",
    "Motion",
    "Modulators",
)

# --- the live voice's capabilities (see worklet.py for the contract) ---------

# Oscillator shapes the worklet implements: PolyBLEP saw and square (with
# PWM), sine, triangle, 2-op fm, and supersaw — whose seven saws start at
# phases core.supersaw draws from numpy's RNG, so the worklet carries that
# table (pinned against numpy by the parity test); wavetable and pluck read
# the engine's own mip stacks and seeded burst streams (/wavetables.bin),
# so they match sample for sample too. Noise is the one statistical shape:
# the parity test holds it to 1/3-octave bands, not samples.
SHAPES = frozenset(
    {
        "sine",
        "saw",
        "square",
        "triangle",
        "fm",
        "supersaw",
        "wavetable",
        "pluck",
        "noise",
        "fm4",
    }
)
# Every filter_type core has: the ladder is SVF -> soft_clip(drive) -> SVF,
# lowpass/highpass/bandpass/notch one TPT SVF output each, "none"
# bypasses; the rest (core.filt) are SVFs too — two for the 24 dB
# slopes and the diode ladder (its asymmetric clip between), three
# all-passes for the phaser, a bell from the bandpass — and the comb, a
# ring of 2048 samples a channel (filter 2 has the same)
FILTERS = frozenset(FILTER_TYPES_ALL)
# unison copies the worklet allocates per voice
MAX_UNISON = 8

# A Ui's `worklet` is one of:
#   "exact"    voice-worklet.js reproduces any value the Patch can hold
#   "ignored"  no audible effect on a live note — nothing reads it, or it only
#              matters once a sibling the gate already refuses is active
#   "default"  not reproduced at all: only the Patch default plays live
#   callable   `(Patch) -> bool`, reproduced only for the values it admits
Worklet = str | Callable[[Any], bool]


@dataclass(frozen=True)
class Ui:
    group: str | None  # the panel; None = no control (see `why`)
    label: str = ""  # unique across panels: XY/macro selects list it bare
    step: float = 0.01
    hint: str = ""  # one line: the row's tooltip
    unit: str = ""  # printed after the readout: "1200 Hz", not "1200"
    subhead: str = ""  # a heading drawn at the top of the row
    widget: str = ""  # "pills" | "select" for categorical fields
    option_labels: Mapping | None = None  # value -> menu text (selects)
    mod_label: str = ""  # its name in the route target menu, when it differs
    # pills beyond the gene's choices: a hand-made shape the breeder never
    # draws but a player picks (osc1's "sampler")
    extra: tuple = ()
    worklet: Worklet = "default"
    why: str = ""  # why a field has no control


# The fm4 algorithms as their wiring (core.FM4_ALGOS): a -> b modulates b,
# + sums carriers.
FM4_ALGO_LABELS = {
    "stack": "4→3→2→1",
    "pairs": "2→1 + 4→3",
    "three": "2+3+4 → 1",
    "pair_plus": "2→1 + 3 + 4",
    "additive": "1 + 2 + 3 + 4",
}

# core.WARP_MODES as the warp select reads them
WARP_LABELS = {
    "off": "off",
    "bend_pos": "bend +",
    "bend_neg": "bend −",
    "bend_both": "bend ±",
    "pd": "PD",
    "mirror": "mirror",
    "asym": "asym",
    "quantize": "quantize",
    "pwm": "pwm",
}

# Readable names for the mod matrix's source values: the selects used to
# show raw values ("fenv").
MOD_SOURCE_LABELS = {
    "none": "off",
    "lfo": "LFO 1",
    "fenv": "Filter envelope",
    "aenv": "Amp envelope",
    "velocity": "Velocity",
    # the general modulators and the wheels
    "lfo2": "LFO 2",
    "lfo3": "LFO 3",
    "env3": "Envelope 3",
    "env4": "Envelope 4",
    "wheel": "Mod wheel",
    "aftertouch": "Aftertouch",
    "key": "Key",
    "random": "Random",
}
# the filter types as the menus say them (filter 1's select, filter 2's)
FILTER_LABELS = {
    "ladder": "Ladder 24",
    "lowpass": "Low 12",
    "bandpass": "Band",
    "highpass": "High 12",
    "notch": "Notch",
    "none": "off",
    "lowpass24": "Low 24",
    "highpass24": "High 24",
    "comb_plus": "Comb +",
    "comb_minus": "Comb −",
    "phaser": "Phaser",
    "diode": "Diode 24",
    "peak": "Peak",
}
# the general LFOs' shapes as their menus say them
LFO_SHAPE_LABELS = {
    "sine": "sine",
    "triangle": "triangle",
    "saw": "saw",
    "square": "square",
    "noise": "stepped",
    "drawn": "drawn",
    "chaos": "chaos",
    "smooth": "smooth random",
}
# a route row's three fields, every slot (the mod matrix draws them)
ROUTE_FIELDS = frozenset(f"{s}_{k}" for s in MOD_SLOTS for k in ("src", "dst", "amt"))


EXACT, IGNORED, DEFAULT = "exact", "ignored", "default"


# A formant-mode sampler's grain train reads the pitch one root period
# ahead of the sample it is playing (sampler._psola takes it at each grain's
# centre, and a grain starts half its length before that). The live voice
# foresees what its control pass computes in closed form — glide, vibrato,
# the pitch envelope, the bend where it stands (static/grain-worklet.js
# `ahead`) — and nothing else: a route onto the pitch, and osc2
# frequency-modulating osc1 (`_cross_fm_ok`), keep such a sound's keys on
# the WAV path.
# (and a route to cross FM: osc2 bending osc1, as `_cross_fm_ok`)
_FORMANT_UNSEEN = frozenset({PITCH_ROUTE, "osc1_detune", "vibrato_depth", "fm_amount"})


def _formant_sampler(p) -> bool:
    return p.osc1 == "sampler" and p.sampler_mode == "formant"


def _cross_fm_ok(p) -> bool:
    cross = p.osc2 is not None and bool(np.any(np.asarray(p.fm_amount) > 0))
    return not (cross and _formant_sampler(p))


# sources whose value later in a note the first sample cannot know: an LFO
# (any of the three), and the wheels a player moves
_LATE_SOURCES = frozenset({"lfo", "lfo2", "lfo3", "wheel", "aftertouch"})


def _slot_gate(slot: str) -> Callable[[Any], bool]:
    """The worklet runs every route `params.slot_active` admits, with the
    engine's spans (/patch.json sends MOD_SLOTS, MOD_SOURCES, MOD_RANGES).
    Two are refused. An LFO or a wheel (`_LATE_SOURCES`) -> drive on a
    non-ladder filter whose drive sits at or under 1: the engine's
    post-filter clip switches on for the WHOLE note if any sample's drive
    exceeds 1, which an LFO that starts on the wrong side of zero only does
    later — the live voice can't know at the first sample whether it ever
    will. And a route that moves the pitch of a formant-mode sampler
    (`_FORMANT_UNSEEN`)."""

    def ok(p) -> bool:
        if not slot_active(p, slot):
            return True
        src, dst = getattr(p, f"{slot}_src"), getattr(p, f"{slot}_dst")
        late_clip = (
            src in _LATE_SOURCES
            and dst == "drive"
            and p.filter_type not in ("ladder", "diode")
            and p.drive <= 1.0
        )
        return not late_clip and not (dst in _FORMANT_UNSEEN and _formant_sampler(p))

    return ok


def _osc1_ok(p) -> bool:
    # the sampler plays live when sampler.live_ok says so: "resample" with no
    # stretch or "formant" (any scalar stretch), mono or stereo, within the
    # payload cap (the fields below name which one refuses it)
    return p.osc1 in SHAPES or (p.osc1 == "sampler" and sampler.live_ok(p))


def _sampler_gate(ok: Callable[[Any], bool]) -> Callable[[Any], bool]:
    """A sampler field's live rule: anything goes while osc1 plays no
    sampler (the engine never reads it); else `ok`."""
    return lambda p: p.osc1 != "sampler" or ok(p)


def _osc2_ok(p) -> bool:
    return p.osc2 is None or p.osc2 in SHAPES


def _filter_ok(p) -> bool:
    return p.filter_type in FILTERS


def _unison_ok(p) -> bool:
    return 1 <= p.unison <= MAX_UNISON


def _ui(group: str, label: str, worklet: Worklet, step: float = 0.01, **kw) -> Ui:
    """One control. Its live-voice class is required: none is assumed."""
    return Ui(group, label, step, worklet=worklet, **kw)


_S, _F, _V, _A, _C, _M, _O = GROUPS
# fmt: off
_ENTRIES: list[tuple[str, Ui]] = [
    # "sampler" plays live only as _osc1_ok admits it; otherwise a sampler
    # sound's keys take the server-rendered WAV path
    ("osc1", _ui(_S, "Oscillator 1", _osc1_ok, widget="pills",
        extra=("fm4", "sampler"),
        hint="The main waveform. FM, FM4, pluck, wavetable and sampler show their "
             "own settings.")),
    ("osc1_level", _ui(_S, "Osc 1 level", EXACT,
        hint="How loud oscillator 1 is in the mix.")),
    ("osc1_octave", _ui(_S, "Osc 1 octave", EXACT,
        hint="Shift oscillator 1 by whole octaves.")),
    ("osc1_detune", _ui(_S, "Osc 1 detune", EXACT, 0.5, unit="ct",
        hint="Nudge oscillator 1 in cents (1/100 of a semitone).")),
    # core.warped, oversampled 4x on both sides (voice-worklet.js
    # warpBlock, voice.ts warpLane); drawn as one block in the oscillator's
    # area (editor.html: the mode's select and its amount's knob)
    ("osc1_warp", _ui(_S, "Osc 1 warp", EXACT, widget="select",
        option_labels=WARP_LABELS,
        hint="Bend oscillator 1's phase before its shape reads it (sine, saw, square, "
             "triangle, wavetable): new harmonics from a plain wave. PD is phase "
             "distortion: a sine swept toward a saw.")),
    ("osc1_warp_amt", _ui(_S, "Osc 1 warp amount", EXACT, mod_label="Osc 1 warp",
        hint="How far oscillator 1's warp bends. Draw it or route to it to sweep the "
             "timbre.")),
    ("osc2", _ui(_S, "Oscillator 2", _osc2_ok, widget="pills", subhead="Oscillator 2",
        extra=("fm4",), hint="A second waveform layered on the first.")),
    ("osc2_level", _ui(_S, "Osc 2 level", EXACT,
        hint="How loud oscillator 2 is in the mix.")),
    ("osc2_octave", _ui(_S, "Osc 2 octave", EXACT,
        hint="Shift oscillator 2 by whole octaves.")),
    ("osc2_detune", _ui(_S, "Osc 2 detune", EXACT, 0.5, unit="ct",
        hint="Detune oscillator 2 against 1 for a thicker, beating sound.")),
    ("osc2_warp", _ui(_S, "Osc 2 warp", EXACT, widget="select",
        option_labels=WARP_LABELS,
        hint="Bend oscillator 2's phase before its shape reads it (not while it is "
             "hard-synced).")),
    ("osc2_warp_amt", _ui(_S, "Osc 2 warp amount", EXACT, mod_label="Osc 2 warp",
        hint="How far oscillator 2's warp bends. Draw it or route to it to sweep "
             "the timbre.")),
    # core.sync: band-limited resets, osc2 one sample late (voice-worklet.js
    # holds the same sample)
    ("osc2_sync", _ui(_S, "Hard sync", EXACT, widget="pills",
        hint="Oscillator 2 restarts with every cycle of oscillator 1 (saw, square, "
             "triangle or sine).")),
    ("sync_pitch", _ui(_S, "Sync pitch", EXACT, 0.1, unit="st", mod_label="Sync pitch",
        hint="Hard sync: semitones oscillator 2 sits above. Sweep it (a route from "
             "the filter envelope, a curve) for the sync lead.")),
    ("fm_ratio", _ui(_S, "FM ratio", EXACT, 0.5, subhead="FM shape",
        hint="FM shape: modulator pitch as a multiple of the note.")),
    ("fm_index", _ui(_S, "FM index", EXACT, 0.05,
        hint="FM shape: how hard the modulator bends the carrier (brightness).")),
    ("fm_decay", _ui(_S, "FM index decay", EXACT,
        hint="FM shape: how fast that brightness fades over each note.")),
    # core.fm4: four sine operators; a modulator's level is its depth, a
    # carrier's its volume; each level fades over its own decay
    ("fm4_algo", _ui(_S, "FM4 algorithm", EXACT, widget="select",
        subhead="FM4 shape", option_labels=FM4_ALGO_LABELS,
        hint="FM4 shape: which operators modulate which (arrows), and which are "
             "heard (the last in each chain).")),
    *[
        (f"fm4_ratio{k}", _ui(_S, f"FM4 ratio {k}", EXACT, 0.01,
            hint=f"FM4 shape: operator {k}'s pitch as a multiple of the note. "
                 "Whole numbers are harmonic; the rest bell-like."))
        for k in (1, 2, 3, 4)
    ],
    *[
        (f"fm4_level{k}", _ui(_S, f"FM4 level {k}", EXACT,
            hint=f"FM4 shape: operator {k}'s output — a modulator's depth, a "
                 "carrier's volume."))
        for k in (1, 2, 3, 4)
    ],
    *[
        (f"fm4_decay{k}", _ui(_S, f"FM4 decay {k}", EXACT, unit="s",
            hint=f"FM4 shape: how fast operator {k}'s level fades (0 holds it): "
                 "a fading modulator is a tine, a bell, a mallet."))
        for k in (1, 2, 3, 4)
    ],
    ("fm4_feedback", _ui(_S, "FM4 feedback", EXACT,
        hint="FM4 shape: operator 4 modulates itself — toward a saw, then grit.")),
    ("pluck_damping", _ui(_S, "Pluck damping", EXACT, subhead="Pluck shape",
        hint="Pluck shape: how quickly the string loses its highs.")),
    ("pluck_bright", _ui(_S, "Pluck brightness", EXACT,
        hint="Pluck shape: how bright the pick attack is.")),
    # a radio group of drawn tables (param_row.html, oscfaces.WT_FACES)
    ("wt_table", _ui(_S, "Wavetable", EXACT,
        widget="pills", subhead="Wavetable shape",
        hint="Wavetable shape: which set of frames to morph through. Each is "
             "drawn; the lit frame is where the position stands.")),
    ("wt_pos", _ui(_S, "Wavetable position", EXACT,
        hint="Wavetable shape: position across the table. Automate it to sweep.")),
    # the sampler (synthlab/sampler.py). The live voice reads the prepared
    # buffers (sampler.live_payload) at the live pitch: what shapes the
    # buffers and the read rate is exact, a stereo sample's two channels
    # included. Formant mode is the engine's grain train run in the voice
    # (static/grain-worklet.js: `_psola` with the pitch read one root
    # period late — its header and the parity corpus say how close that
    # is). What is refused is the WSOLA stretch ("resample" mode with a
    # stretch off 1: a similarity search per grain) and a drawn stretch
    ("sampler_root", _ui(_S, "Sample root", EXACT, subhead="Sampler shape",
        hint="Sampler: the note that plays the sample at its own pitch (a chop's "
             "first slice).")),
    ("sampler_mode", _ui(_S, "Sample pitch mode", EXACT, widget="pills",
        hint="Resample is tape (formants move with the pitch); formant keeps the "
             "timbre when shifting far. Set the root to the sample's pitch.")),
    ("sampler_keytrack", _ui(_S, "Sample key track", EXACT, widget="pills",
        hint="Off: every key plays the sample at its own pitch.")),
    ("sampler_start", _ui(_S, "Sample start", EXACT, 0.001,
        hint="Where in the sample each note starts.")),
    ("sampler_end", _ui(_S, "Sample end", EXACT, 0.001,
        hint="Where in the sample each note stops.")),
    ("sampler_stretch", _ui(_S, "Sample stretch",
        _sampler_gate(sampler.live_stretch_ok), 0.01, unit="x",
        hint="Length without pitch (granular): 2 plays twice as long. Draw it "
             "to warp time.")),
    ("sampler_loop", _ui(_S, "Sample loop", EXACT, widget="pills",
        hint="Loop between the loop points while the key is held.")),
    ("sampler_loop_start", _ui(_S, "Loop start", EXACT, 0.001,
        hint="Sampler: where the loop begins.")),
    ("sampler_loop_end", _ui(_S, "Loop end", EXACT, 0.001,
        hint="Sampler: where the loop jumps back.")),
    ("sampler_xfade", _ui(_S, "Loop crossfade", EXACT, 0.001, unit="s",
        hint="Blend across the loop point so it doesn't click.")),
    ("sub_level", _ui(_S, "Sub oscillator", EXACT,
        subhead="Mixer", mod_label="Sub level",
        hint="A sine one octave below oscillator 1, for weight.")),
    # statistical, not sample for sample: numpy's generator isn't realtime,
    # so the parity test holds noise to 1/3-octave bands
    ("noise_level", _ui(_S, "Noise", EXACT, mod_label="Noise level",
        hint="Noise mixed in with the oscillators.")),
    ("noise_color", _ui(_S, "Noise colour", EXACT, widget="pills",
        hint="White is hissy, pink softer, brown rumbly.")),
    ("fm_amount", _ui(_S, "Cross FM", _cross_fm_ok,
        hint="Oscillator 2 frequency-modulates oscillator 1: growls, bells.")),
    ("ring_mod", _ui(_S, "Ring mod", EXACT,
        hint="Multiply oscillator 1 by oscillator 2: metallic, bell-like.")),

    # a select (thirteen types); an id'd control, so the deck,
    # MIDI learn and the menu step it
    ("filter_type", _ui(_F, "Filter type", _filter_ok, widget="select",
        option_labels=FILTER_LABELS, extra=FILTER_TYPES_ALL[6:],
        hint="Ladder is the warm classic; 12 / 24 are the slopes; comb rings at "
             "the cutoff's pitch (resonance is its feedback); phaser notches; "
             "diode is a grittier ladder; peak boosts at the cutoff; off "
             "bypasses.")),
    ("cutoff", _ui(_F, "Cutoff", EXACT, 1.0, unit="Hz", mod_label="Filter cutoff",
        hint="Where the filter starts cutting. Lower is darker.")),
    ("resonance", _ui(_F, "Resonance", EXACT,
        hint="Emphasis at the cutoff: squelch, then whistle.")),
    ("key_track", _ui(_F, "Key tracking", EXACT,
        hint="How much the cutoff follows the note: higher notes, brighter.")),
    ("filter_env", _ui(_F, "Filter env amount", EXACT, 10.0,
        unit="Hz", subhead="Filter envelope",
        hint="How far the filter envelope opens the cutoff.")),
    ("filter_attack", _ui(_F, "Filter attack", EXACT, 0.001, unit="s",
        hint="Filter envelope: time to open.")),
    ("filter_decay", _ui(_F, "Filter decay", EXACT, unit="s",
        hint="Filter envelope: time to fall to the sustain level.")),
    ("filter_sustain", _ui(_F, "Filter sustain", EXACT,
        hint="Filter envelope: level held while the key is down.")),
    # read on both sides: the filter envelope closes over this from the
    # key-up, inside the amp release the voice lives for. (Both used to
    # release over the note's tail — the amp release live — and neither
    # read it for a played key: "exact" was two sides ignoring it alike.)
    ("filter_release", _ui(_F, "Filter release", EXACT, unit="s",
        hint="Filter envelope: time to close after the key is released.")),
    # filter 2, one compact block (editor.html); its rows hide
    # while it is off
    ("filter2_route", _ui(_F, "Filter 2", EXACT, widget="pills", subhead="Filter 2",
        hint="Filter 2: off, after filter 1 (serial), or beside it (parallel: the "
             "two outputs averaged).")),
    ("filter2_type", _ui(_F, "Filter 2 type", EXACT, widget="select",
        option_labels=FILTER_LABELS,
        hint="Filter 2's type: any of filter 1's. Its ladders read the drive.")),
    ("filter2_cutoff", _ui(_F, "Filter 2 cutoff", EXACT, 1.0, unit="Hz",
        hint="Filter 2's cutoff (a comb's pitch).")),
    ("filter2_resonance", _ui(_F, "Filter 2 resonance", EXACT,
        hint="Filter 2's emphasis (a comb's feedback, a peak's boost).")),
    ("filter2_key_track", _ui(_F, "Filter 2 key track", EXACT,
        hint="How much filter 2's cutoff follows the note (1: a comb plays in "
             "tune).")),
    ("filter2_env", _ui(_F, "Filter 2 env amount", EXACT, 10.0, unit="Hz",
        hint="How far the filter envelope moves filter 2's cutoff (negative "
             "closes it).")),

    ("formant_amount", _ui(_V, "Vowel amount", EXACT,
        hint="Blend in the vowel filter. 0 is off.")),
    ("formant_vowel", _ui(_V, "Vowel a-e-i-o-u", EXACT,
        hint="Sweep a, e, i, o, u. Automate it for talking sounds.")),
    ("formant_size", _ui(_V, "Tract size", EXACT,
        hint="Vocal-tract size: lower is a bigger, darker throat.")),

    ("attack", _ui(_A, "Attack", EXACT, 0.001, unit="s",
        hint="Time to reach full level after a note starts.")),
    ("decay", _ui(_A, "Decay", EXACT, unit="s",
        hint="Time to fall to the sustain level.")),
    ("sustain", _ui(_A, "Sustain", EXACT, hint="Level held while the key is down.")),
    ("release", _ui(_A, "Release", EXACT, unit="s",
        hint="Time to fade out after the key is released.")),
    ("env_curve", _ui(_A, "Envelope curve", EXACT, 0.1,
        hint="Shapes both envelopes: higher is snappier.")),

    # warmth: the two an analog voice is known by, side by side (drift
    # last, a slider under the unison knobs, is where nobody looks for
    # warmth); Character's six knobs stand on one line (style.css 17.1)
    ("drive", _ui(_C, "Drive", EXACT, 0.05,
        hint="Saturation. 1 is clean: the warmth of a pushed analog stage.")),
    # seeded from the note (synth.drift_seed): voice-worklet.js draws the
    # same wander, start phases and cutoff offset
    ("drift", _ui(_C, "Analog drift", EXACT,
        hint="Each note wanders a little in pitch, starts at its own phase and "
             "sits its filter a touch off — an analog voice's looseness.")),
    # rides noteOn's fromFreq/glideTime
    ("glide", _ui(_C, "Glide", EXACT, 0.005, unit="s",
        hint="Time to slide from one note to the next.")),
    ("unison", _ui(_C, "Unison voices", _unison_ok,
        hint="Stacked copies of the voice. More is wider and thicker.")),
    ("unison_detune", _ui(_C, "Unison detune", EXACT, 0.5, unit="ct",
        hint="How far the unison copies drift apart in pitch.")),
    ("unison_spread", _ui(_C, "Stereo spread", EXACT,
        hint="How wide the unison copies spread in stereo.")),
    # one voice (sequencer.mono_lines in the loop; the worklet's
    # noteOn / noteOff in the keys) — two pill rows under the knobs' line,
    # so glide and the six knobs keep their one line
    ("voice_mode", _ui(_C, "Voices", EXACT, widget="pills",
        hint="Poly plays every note; mono one at a time, each retriggered; legato "
             "glides into a note played over a held one without retriggering.")),
    ("glide_mode", _ui(_C, "Glide when", EXACT, widget="pills",
        hint="Always glides from the last note; legato only into a note played "
             "over a held one.")),

    # every LFO shape (the stepped "noise" reads core.sample_hold's seeded
    # stream from /wavetables.bin); a sync division reaches the worklet as
    # the rate /patch.json resolves against the bpm (synth.at_bpm)
    ("mod_lfo_rate", _ui(_M, "LFO 1 rate", EXACT, 0.05, unit="Hz", subhead="LFO 1",
        hint="Speed of LFO 1, the LFO the routes use.")),
    ("lfo_shape", _ui(_M, "LFO 1 shape", EXACT, widget="select",
        option_labels=LFO_SHAPE_LABELS, extra=("drawn", "chaos", "smooth"),
        hint="LFO 1's waveform. Stepped is a random step a cycle; drawn is the "
             "curve drawn for it; chaos a chaotic line; smooth random glides.")),
    ("lfo_sync", _ui(_M, "LFO 1 sync", EXACT, widget="select",
        hint="Lock LFO 1 to the tempo. Overrides the rate.")),
    ("filter_lfo_rate", _ui(_M, "Filter LFO rate", EXACT, 0.05,
        unit="Hz", subhead="Filter LFO",
        hint="Speed of the LFO wired to the cutoff.")),
    ("filter_lfo_depth", _ui(_M, "Filter LFO depth", EXACT, 10.0, unit="Hz",
        hint="How far the filter LFO moves the cutoff.")),
    ("filter_lfo_shape", _ui(_M, "Filter LFO shape", EXACT, widget="select",
        hint="The filter LFO's waveform.")),
    ("filter_lfo_sync", _ui(_M, "Filter LFO sync", EXACT, widget="select",
        hint="Lock the filter LFO to the tempo. Overrides the rate.")),
    ("vibrato_rate", _ui(_M, "Vibrato rate", EXACT, 0.1, unit="Hz", subhead="Vibrato",
        hint="Speed of the pitch wobble.")),
    ("vibrato_depth", _ui(_M, "Vibrato depth", EXACT,
        hint="How far the pitch wobbles.")),
    ("vibrato_delay", _ui(_M, "Vibrato delay", EXACT, unit="s",
        hint="Time before vibrato fades in.")),
    ("tremolo_rate", _ui(_M, "Tremolo rate", EXACT, 0.1, unit="Hz", subhead="Tremolo",
        hint="Speed of the volume wobble.")),
    ("tremolo_depth", _ui(_M, "Tremolo depth", EXACT,
        hint="How far the volume wobbles.")),
    ("pwm_rate", _ui(_M, "Pulse width rate", EXACT, 0.05,
        unit="Hz", subhead="Pulse width",
        hint="Speed of the pulse-width sweep (square waves).")),
    ("pwm_depth", _ui(_M, "Pulse width depth", EXACT,
        hint="How far the pulse width sweeps.")),
    ("pitch_env", _ui(_M, "Pitch env amount", EXACT, 0.5,
        unit="st", subhead="Pitch envelope",
        hint="Semitones the pitch starts away from the note.")),
    ("pitch_env_decay", _ui(_M, "Pitch env decay", EXACT, unit="s",
        hint="Time for the pitch to settle on the note.")),
    # the keys' bend strip, MIDI's wheel and the XY "Pitch bend" axis bend
    # live notes by this range (voice-worklet.js: smoothed, every lane)
    ("pitch_bend", _ui(_M, "Pitch bend", EXACT, 0.01, subhead="Pitch bend",
        hint="Bend position (x the bend range). Draw a curve to bend the loop.")),
    ("bend_range", _ui(_M, "Bend range", EXACT, 1.0, unit="st",
        hint="Semitones a full bend reaches, for the curve and the live keys.")),
    # the mod wheel (CC 1) and aftertouch (channel pressure) as
    # route sources — the live keys add the hardware's position to these
    ("mod_wheel", _ui(_M, "Mod wheel", EXACT, subhead="Wheels",
        hint="The mod wheel's position, a route source. MIDI CC 1 adds to it live; "
             "draw a curve to move the loop.")),
    ("aftertouch", _ui(_M, "Aftertouch", EXACT,
        hint="Key pressure, a route source. MIDI channel pressure adds to it "
             "live; draw a curve to move the loop.")),
    # one route per slot, generated from MOD_SLOTS so adding a fifth never
    # means remembering to add it here too; the editor draws each slot's
    # three rows as one line (source -> target, amount); the src entry
    # carries the one route the live voice refuses (_slot_gate).
    *[
        entry
        for i, s in enumerate(MOD_SLOTS, start=1)
        for entry in (
            (f"{s}_src", _ui(_M, f"Route {i} source", _slot_gate(s),
                widget="select", option_labels=MOD_SOURCE_LABELS)),
            (f"{s}_dst", _ui(_M, f"Route {i} target", EXACT, widget="select")),
            (f"{s}_amt", _ui(_M, f"Route {i} amount", EXACT)),
        )
    ],

    # the general modulators the routes read — envelopes 3 and
    # 4 (gated as the filter envelope is), LFOs 2 and 3 (the general shapes,
    # each its own drawing; a sync division reaches the worklet resolved)
    *[
        entry
        for k in (3, 4)
        for entry in (
            (f"env{k}_attack", _ui(_O, f"Env {k} attack", EXACT, 0.001, unit="s",
                subhead=f"Envelope {k}", hint=f"Envelope {k}: time to rise.")),
            (f"env{k}_decay", _ui(_O, f"Env {k} decay", EXACT, unit="s",
                hint=f"Envelope {k}: time to fall to the sustain level.")),
            (f"env{k}_sustain", _ui(_O, f"Env {k} sustain", EXACT,
                hint=f"Envelope {k}: level held while the key is down.")),
            (f"env{k}_release", _ui(_O, f"Env {k} release", EXACT, unit="s",
                hint=f"Envelope {k}: time to fall after the key is released.")),
            (f"env{k}_curve", _ui(_O, f"Env {k} curve", EXACT, 0.1,
                hint=f"Envelope {k}: 1 is straight, higher snappier.")),
        )
    ],
    *[
        entry
        for k in (2, 3)
        for entry in (
            (f"lfo{k}_rate", _ui(_O, f"LFO {k} rate", EXACT, unit="Hz",
                subhead=f"LFO {k}", hint=f"Speed of LFO {k}.")),
            # the two knobs side by side, then the menus
            (f"lfo{k}_phase", _ui(_O, f"LFO {k} phase", EXACT,
                hint=f"Where in its cycle LFO {k} starts each note.")),
            (f"lfo{k}_shape", _ui(_O, f"LFO {k} shape", EXACT, widget="select",
                option_labels=LFO_SHAPE_LABELS,
                hint=f"LFO {k}'s waveform; drawn plays the curve drawn for it.")),
            (f"lfo{k}_sync", _ui(_O, f"LFO {k} sync", EXACT, widget="select",
                hint=f"Lock LFO {k} to the tempo. Overrides the rate.")),
            (f"lfo{k}_mode", _ui(_O, f"LFO {k} mode", EXACT, widget="pills",
                hint=f"Retrig: LFO {k} starts every note at its phase. Free: each "
                     "note picks the cycle up at a place of its own.")),
        )
    ],

    # no control
    ("name", Ui(None, worklet=IGNORED, why="the sound's name: save/rename set it")),
    ("voice_fx", Ui(None, worklet=IGNORED,
        why="holds callables, not JSON; layer FX already covers it")),
    # refused only past the live payload's cap (both channels counted)
    ("sampler_sample", Ui(None, worklet=_sampler_gate(
        lambda p: p.sampler_zones
        or sampler.live_size(p) <= sampler.LIVE_MAX_SAMPLES),
        why="a content hash: Use as sampler / Resample loop set it")),
    ("sampler_slices", Ui(None, worklet=EXACT,
        why="the chops: the sampler panel's Chop buttons and slice rows set it")),
    # every zone's sample rides the live payload: refused past its cap
    ("sampler_zones", Ui(None, worklet=_sampler_gate(
        lambda p: not p.sampler_zones
        or sampler.live_size(p) <= sampler.LIVE_MAX_SAMPLES),
        why="the zone map: the sampler panel's zone editor and auto-map set it")),
    # read live from GET /wavetable/live/<id>.bin (wavetables.live_payload)
    ("wt_custom", Ui(None, worklet=EXACT,
        why="a content hash: the wavetable picker's own tables, + from clip and "
            "draw set it")),
    # the drawn LFO shapes: the drawing on the LFO's own row sets one (a
    # hidden field the form posts, forms.DRAWN)
    *[
        (f"{name}_draw", Ui(None, worklet=EXACT,
            why=f"{label}'s drawn shape: the LFO's drawing sets it"))
        for name, label in (("lfo", "LFO 1"), ("lfo2", "LFO 2"), ("lfo3", "LFO 3"))
    ],
]
# fmt: on

UI: dict[str, Ui] = dict(_ENTRIES)
assert len(UI) == len(_ENTRIES), "a Ui is declared twice"

# Readable names for route targets: a control's label unless it says
# otherwise ("pitch" is no field: it bends every oscillator, +-12 st).
MOD_TARGET_LABELS: dict[str, str] = {
    d: "Pitch (±12 st)" if d == PITCH_ROUTE else UI[d].mod_label or UI[d].label
    for d in MOD_DESTINATIONS
}
# A heading drawn at the top of a row (inside it, so a shape block's heading
# hides with its rows). Keys are row names.
SUBHEADS: dict[str, str] = {n: u.subhead for n, u in UI.items() if u.subhead}


# --- the rendered form: what templates, macros and the XY pad read -----------


@dataclass
class ParamSpec:
    name: str
    label: str
    kind: str  # "range" | "select" | "pills"
    min: float = 0.0
    max: float = 1.0
    step: float = 0.01
    options: tuple = ()
    unit: str = ""  # printed after the readout
    default: float | None = None  # Patch default — double-click restores it
    log: bool = False  # render as a 0..1 position slider (log taper)
    hint: str = ""  # one line, shown on hover/focus (title): what it does


_DEFAULTS = Patch()


def _spec(name: str, ui: Ui) -> ParamSpec:
    param = PARAMS[name]
    if param.kind == "cat":
        if ui.widget == "pills":
            opts = tuple(
                ("" if o is None else o, "off" if o is None else o)
                for o in (*param.bounds, *ui.extra)
            )
        else:
            # a menu of route targets reads as the targets' own names, and
            # offers every target (the gene leaves "pitch" out)
            route = param.cap == MOD_DESTINATIONS
            labels = MOD_TARGET_LABELS if route else ui.option_labels or {}
            # a select offers the gene's choices and the hand-made ones
            # (`extra`: the newer filter types and LFO shapes); a source
            # menu every source there is
            if route:
                values = param.cap
            elif name.endswith("_src") and name.startswith("mod"):
                values = param.cap
            else:
                values = (*param.bounds, *ui.extra)
            opts = tuple((o, labels.get(o, o)) for o in values)
        return ParamSpec(name, ui.label, ui.widget, options=opts, hint=ui.hint)
    lo, hi = param.bounds
    return ParamSpec(
        name,
        ui.label,
        "range",
        float(lo),
        float(hi),
        1.0 if param.kind == "int" else ui.step,
        unit=ui.unit,
        default=getattr(_DEFAULTS, name),
        log=param.kind == "log",
        hint=ui.hint,
    )


def groups() -> list[tuple[str, list[ParamSpec]]]:
    """The editor's panels, in order, each with its controls in order."""
    out: dict[str, list[ParamSpec]] = {g: [] for g in GROUPS}
    for name, ui in UI.items():
        if ui.group is not None:
            out[ui.group].append(_spec(name, ui))
    return list(out.items())


PARAM_GROUPS: list[tuple[str, list[ParamSpec]]] = groups()
_SPEC_BY_NAME = {s.name: s for _, specs in PARAM_GROUPS for s in specs}

# Continuous (float) range params: the XY pad's and the macros' targets.
CONTINUOUS_PARAMS: tuple[str, ...] = tuple(
    s.name
    for s in _SPEC_BY_NAME.values()
    if s.kind == "range" and PARAMS[s.name].kind != "int"
)
CONTINUOUS_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (name, _SPEC_BY_NAME[name].label) for name in CONTINUOUS_PARAMS
)
# Controls that are a performance, not a sound edit: nothing writes them
# into the patch from a knob. `pitch_bend` is the keyboard's wheel — a
# static bend would transpose the loop; a drawn one is its curve. (The
# other `Param.nogene` performance control, `bend_range`, is the sound's
# own setting and stays a target.) The XY pad keeps the axis and plays the
# wheel with it (`routes/macros.PERFORMANCE_AXES`); a macro has no live
# path, so it cannot target one at all.
# The mod wheel and aftertouch are the same kind: the XY pad plays
# them as the hardware would ("modwheel" / "aftertouch" messages), a drawn
# curve moves the loop.
PERFORMANCE_PARAMS: frozenset[str] = frozenset(
    {"pitch_bend", "mod_wheel", "aftertouch"}
)
MACRO_PARAMS: tuple[str, ...] = tuple(
    name for name in CONTINUOUS_PARAMS if name not in PERFORMANCE_PARAMS
)
MACRO_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (name, _SPEC_BY_NAME[name].label) for name in MACRO_PARAMS
)


def spec_of(name: str) -> ParamSpec | None:
    """Look up a ParamSpec by field name, or None if unknown."""
    return _SPEC_BY_NAME.get(name)


# Which Source-panel rows are audible only when an osc pill selects a shape:
# read from the shape rules in params (the breeder's own liveness), and
# rendered as :has() dimming selectors in editor.html.
SHAPE_DEPS: dict[str, tuple[str, ...]] = {}
for _name, _u in UI.items():
    _rule = PARAMS[_name].live
    if _u.group == "Source" and isinstance(_rule, uses_shape):
        SHAPE_DEPS.setdefault(_rule.shape, ())
        SHAPE_DEPS[_rule.shape] += (_name,)


# --- the live voice's gate ---------------------------------------------------


def _unheard(name: str, p) -> bool:
    """A "default" field of a shape no oscillator plays (the sampler's):
    the engine never reads it, so any value is reproduced."""
    rule = PARAMS[name].live
    return (
        UI[name].worklet == DEFAULT
        and isinstance(rule, uses_shape)
        and rule.shape not in (p.osc1, p.osc2)
    )


def worklet_blockers(p) -> list[str]:
    """Field names forcing the WAV fallback, in Patch field order; `[]`
    means the worklet can play `p` exactly."""
    out: list[str] = []
    for f in fields(p):
        name = f.name
        rule = UI[name].worklet
        if rule in ("exact", "ignored") or _unheard(name, p):
            continue
        if callable(rule):
            if not rule(p):
                out.append(name)
        elif getattr(p, name) != getattr(_DEFAULTS, name):
            out.append(name)
    return out


# ---- idle controls -------------------------------------------------------------
# Not every area is always working (Character, say): a control the engine
# cannot hear under the patch's other settings (params.live — the breeder's
# own rule, never a second copy) is drawn idle, with why. A route row's own
# fields are left to the mod matrix's :has() dimming.
_IDLE_GROUPS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("unison_detune", "unison_spread"), "needs 2 or more unison voices"),
    (("vibrato_rate", "vibrato_delay"), "vibrato depth is 0"),
    (("tremolo_rate",), "tremolo depth is 0"),
    (("pwm_rate", "pwm_depth"), "acts on a square oscillator"),
    (
        ("osc2_level", "osc2_detune", "osc2_octave", "fm_amount", "ring_mod"),
        "oscillator 2 is off",
    ),
    (("resonance", "key_track", "filter_lfo_depth", "filter_env"), "the filter is off"),
    (
        ("filter_attack", "filter_decay", "filter_sustain", "filter_release"),
        "the filter envelope amount is 0 and nothing routes it",
    ),
    (
        ("filter_lfo_rate", "filter_lfo_shape", "filter_lfo_sync"),
        "the filter LFO depth is 0",
    ),
    (("mod_lfo_rate", "lfo_shape", "lfo_sync"), "no route uses LFO 1"),
    (("pitch_env_decay",), "the pitch envelope amount is 0"),
    (("fm_ratio", "fm_index", "fm_decay"), "acts on an fm oscillator"),
    (("pluck_damping", "pluck_bright"), "acts on a pluck oscillator"),
    (("wt_pos", "wt_table"), "acts on a wavetable oscillator"),
    (("osc2_sync",), "acts on a saw, square, triangle or sine oscillator 2"),
    (("sync_pitch",), "hard sync is off"),
    (
        ("fm4_algo", "fm4_feedback")
        + tuple(
            f"fm4_{k}{i}" for k in ("ratio", "level", "decay") for i in range(1, 5)
        ),
        "acts on an fm4 oscillator",
    ),
    (("formant_vowel", "formant_size"), "vowel amount is 0"),
    (("noise_color",), "noise is 0"),
)
IDLE_WHY: dict[str, str] = {n: why for names, why in _IDLE_GROUPS for n in names}
# the panels' fields once: the genes they show, a route row's own left out
_SHOWN = frozenset(spec.name for _, specs in PARAM_GROUPS for spec in specs)
_IDLE_ASKED = tuple(
    n for n in GENES if n in _SHOWN and not (n.startswith("mod") and n[4:5] == "_")
)
GLIDE_WHY = "heard on slides — the riff phrase has them (the keys glide note to note)"


def idle(p: Patch, phrase_slides: bool = True) -> dict[str, str]:
    """The panel controls that change nothing now, each with why: a gene the
    engine's own `params.live(p)` leaves out (a rate with its depth at 0,
    detune with one unison voice), and glide on a phrase with no slides
    (`phrase_slides` False) — the loop glides only into a slide."""
    from ..params import live

    heard = live(p)
    out = {
        n: IDLE_WHY.get(n, "no effect with the current settings")
        for n in _IDLE_ASKED
        if n not in heard
    }
    if not phrase_slides and p.glide > 0 and "glide" in _SHOWN:
        out["glide"] = GLIDE_WHY
    return out
