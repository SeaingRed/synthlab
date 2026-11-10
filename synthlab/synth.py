"""
synthlab.synth — the instrument.

A `Patch` is a plain dataclass describing a sound: two oscillators, a filter
with its own envelope, an amp envelope, and modulation. `Synth.note()` turns
a patch plus a pitch into audio. Because a patch is just data, you can save
it to JSON, mutate it, randomise it, or breed it genetically.
"""

from __future__ import annotations

import json
import logging
import math
import zlib
from dataclasses import asdict, dataclass, field, fields, replace

import numpy as np

from . import core
from . import effects as fx
from ._native import kernel
from .core import (
    SR,
    WAVETABLE_NAMES,
    _series,
    adsr,
    as_array,
    biquad,
    dc_block,
    formant,
    ladder,
    lfo,
    n_samples,
    note_to_hz,
    osc,
    soft_clip,
)
from .params import (
    FILTER_TYPES,
    LFO_SHAPES,
    MOD_DESTINATIONS,
    MOD_RANGES,
    MOD_SLOTS,
    MOD_SOURCES,
    PITCH_ROUTE,
    PITCH_ROUTE_ST,
    SYNC_CHOICES,
    SYNC_DIVISIONS,
    slot_active,
)
from .sampler import Settings as SamplerSettings

log = logging.getLogger(__name__)

# The oscillator shapes the native unison stack renders (`Synth._native_stack`);
# pluck and sampler take the Python loop.
_STACK_SHAPES = frozenset(
    {"sine", "saw", "square", "triangle", "supersaw", "fm", "noise", "wavetable"}
)
# The native post chain (`Synth._native_post`) and the kernels it is made of
_POST_KERNELS = ("post", "dc_block", "swept_svf", "lfilter", "soft_clip")
# `core.osc`'s supersaw: its defaults (7 saws, 0.18 detune, spread 1), never
# a patch field
_SS_VOICES, _SS_DETUNE, _SS_SPREAD = 7, 0.18, 1.0
_SS_RATIOS = np.array(
    [
        2
        ** (
            ((i - (_SS_VOICES - 1) / 2) / max(1, (_SS_VOICES - 1) / 2))
            * _SS_DETUNE
            * 100
            * _SS_SPREAD
            / 1200
        )
        for i in range(_SS_VOICES)
    ]
)


def _ss_phases(seed: int) -> np.ndarray:
    """`core.supersaw`'s seeded start phases, one per saw."""
    rng = np.random.default_rng(seed)
    return np.array([rng.random() for _ in range(_SS_VOICES)])


def _unison_offset(u: int, voices: int) -> float:
    """Voice u's place across the unison spread, -1..1 (0 for one voice)."""
    return 0.0 if voices == 1 else (u - (voices - 1) / 2) / ((voices - 1) / 2)


# --- analog drift --------------------------------------------------------------
# Everything drift does is drawn from one seed per note: crc32 of the note's
# MIDI number, so a note renders the same every time (its cache keys hold)
# and the live voice — which knows the key it plays — draws the same
# numbers (voice-worklet.js `drift*`, the same integer hash). The pitch
# alone, not where the note sits: `Song` renders each (pitch, length) once
# through the note memo, and a live key is that lone note.
DRIFT_CENTS = 10.0  # the wander's scale at drift 1 (its RMS is ~0.58 of it)
DRIFT_KNOT = 0.5  # seconds between the wander's random knots
DRIFT_CUT_OCT = 0.25  # the cutoff's offset at drift 1, either way, octaves
_DRIFT_CUT_KEY = 0x10000  # the hash keys: knots 0.., the cutoff, the phases
_DRIFT_PHASE_KEY = 0x20000
# more per-note draws from the same seed: the "random" route source,
# and where a free-running general LFO picks up its cycle (+ the LFO's number)
_RANDOM_KEY = 0x30000
_LFO_FREE_KEY = 0x40000
# the "key" route source: (MIDI - 60) / 60, so C-1 is -1 and C9 +1
KEY_SOURCE_SPAN = 60.0


def drift_seed(f0: float) -> int:
    """The note's drift seed: crc32 of "drift:<MIDI number>"."""
    midi = math.floor(69 + 12 * math.log2(max(float(f0), 1e-6) / 440.0) + 0.5)
    return zlib.crc32(f"drift:{midi}".encode())


def drift_u(seed: int, key: int) -> float:
    """A uniform 0..1 from (seed, key): a 32-bit integer hash (lowbias32),
    written so JS's Math.imul computes the same bits (`core.hash_u`, which
    the general LFOs share)."""
    return core.hash_u(seed, key)


def drift_knot(sr: int) -> int:
    """Samples between knots: a whole number of 64-sample blocks, so a live
    block never straddles two (the voice hands the module one pair)."""
    return 64 * max(1, math.floor(DRIFT_KNOT * sr / 64 + 0.5))


def drift_walk(seed: int, count: int) -> list[float]:
    """The wander's knots: a leaky random walk (0.8 of the last plus 0.6 of
    a fresh -1..1 draw: as spread as one draw — RMS 0.58 — and never past 3)."""
    w = [2 * drift_u(seed, 0) - 1]
    for k in range(1, count):
        w.append(0.8 * w[-1] + 0.6 * (2 * drift_u(seed, k) - 1))
    return w


def drift_cents(seed: int, amount: float, n: int, sr: int = SR) -> np.ndarray:
    """The pitch wander over `n` samples, in cents: the knots smoothstepped
    one into the next — well under 1 Hz, and every sample known from the
    seed (nothing here reads the audio, so the causal contract holds)."""
    kn = drift_knot(sr)
    w = np.asarray(drift_walk(seed, n // kn + 2))
    j = np.arange(n)
    k = j // kn
    x = (j - k * kn) / kn
    s = x * x * (3 - 2 * x)
    return amount * DRIFT_CENTS * (w[k] + (w[k + 1] - w[k]) * s)


def drift_cutoff(seed: int, amount: float) -> float:
    """The note's cutoff factor: +-DRIFT_CUT_OCT octaves x amount."""
    return 2.0 ** (amount * DRIFT_CUT_OCT * (2 * drift_u(seed, _DRIFT_CUT_KEY) - 1))


def drift_phase(seed: int, slot: int, copy: int) -> float:
    """Oscillator `slot`'s (0 osc1, 1 osc2) start phase for unison copy
    `copy`."""
    return drift_u(seed, _DRIFT_PHASE_KEY + 16 * slot + copy)


def _drift(p) -> float:
    """The patch's drift amount (a scalar: it is no curve)."""
    return float(np.max(np.asarray(p.drift, dtype=np.float64)))


# shapes a drifting oscillator starts at a random phase: those with one
# phase to start (fm and fm4: the carrier / operator 1)
_PHASED = frozenset({"sine", "saw", "square", "triangle", "wavetable", "fm", "fm4"})


def _synced(p) -> bool:
    """Does osc2 hard-sync to osc1 (`core.sync`)?"""
    return p.osc2_sync == "on" and p.osc2 in core.SYNC_SHAPES


def _filter2(p) -> bool:
    """Does filter 2 play (a route and a type)?"""
    return p.filter2_route in ("serial", "parallel") and p.filter2_type != "none"


# the filter types that take the drive into themselves (no post clip)
_DRIVEN = ("ladder", "diode")


def warp_of(p, slot: int):
    """Oscillator `slot`'s (1, 2) warp as `core.osc` takes it, or None:
    off, a shape it doesn't bend, or a synced osc2 (sync restarts it)."""
    mode = getattr(p, f"osc{slot}_warp")
    shape = getattr(p, f"osc{slot}")
    if mode == "off" or shape not in core.WARP_SHAPES or (slot == 2 and _synced(p)):
        return None
    return mode, getattr(p, f"osc{slot}_warp_amt")


def _wt_table(p):
    """What the wavetable shape reads: your own table's mip stack
    (`wt_custom`), "basic" when this machine doesn't hold it, else the
    built-in `wt_table`."""
    if not p.wt_custom:
        return p.wt_table
    from . import wavetables

    stack = wavetables.stack(p.wt_custom)
    return "basic" if stack is None else stack


# the parameter domains live in params.py (the registry); the engine reads
# them from there like everything else
__all__ = [
    "LFO_SHAPES",
    "MOD_DESTINATIONS",
    "MOD_SLOTS",
    "MOD_SOURCES",
    "SYNC_CHOICES",
    "WAVETABLE_NAMES",
]


@dataclass
class Patch:
    name: str = "init"

    # --- oscillator 1 ---
    osc1: str = "saw"  # sine saw square triangle supersaw noise
    osc1_level: float = 0.8
    osc1_octave: int = 0
    osc1_detune: float = 0.0  # cents

    # --- oscillator 2 ---
    osc2: str | None = None
    osc2_level: float = 0.5
    osc2_octave: int = -1
    osc2_detune: float = 7.0

    # --- sub oscillator (pure sine one octave down: instant weight) ---
    sub_level: float = 0.0

    # --- noise layer ---
    noise_level: float = 0.0
    noise_color: str = "white"

    # --- filter ---
    filter_type: str = "ladder"  # ladder lowpass highpass bandpass none
    cutoff: float = 2200.0  # Hz
    resonance: float = 0.25  # 0..1
    filter_env: float = 2500.0  # Hz of envelope sweep added to cutoff
    filter_attack: float = 0.005
    filter_decay: float = 0.25
    filter_sustain: float = 0.3
    filter_release: float = 0.2
    key_track: float = 0.35  # cutoff follows pitch

    # --- amp envelope ---
    attack: float = 0.01
    decay: float = 0.18
    sustain: float = 0.7
    release: float = 0.25
    # core.adsr has always taken `curve` and nothing ever passed it, so every
    # envelope in the synth was stuck at the 2.0 default. 1 = linear (pads),
    # higher = snappier decay/release (plucks, percussion).
    env_curve: float = 2.0

    # --- modulation ---
    vibrato_rate: float = 5.0
    vibrato_depth: float = 0.0  # semitones
    vibrato_delay: float = 0.25  # seconds before vibrato fades in
    pwm_rate: float = 0.3
    pwm_depth: float = 0.0
    tremolo_rate: float = 0.0
    tremolo_depth: float = 0.0
    filter_lfo_rate: float = 0.5  # Hz — slow sweeps for pads, fast for wobble
    filter_lfo_depth: float = 0.0  # Hz added to cutoff, bipolar
    # core.lfo has always taken a shape; every call site used to hardcode
    # "sine". square = classic gate/wobble, noise = sample-and-hold texture.
    filter_lfo_shape: str = "sine"  # LFO_SHAPES
    filter_lfo_sync: str = "off"  # SYNC_DIVISIONS — overrides filter_lfo_rate

    # --- pitch envelope (zap basses, 808 booms, laser drops) ---
    pitch_env: float = 0.0  # semitones at note start, decays to 0
    pitch_env_decay: float = 0.15  # seconds

    # --- oscillator interaction (needs osc2) ---
    fm_amount: float = 0.0  # osc2 modulates osc1 frequency: growls, bells
    ring_mod: float = 0.0  # 0 dry osc1 .. 1 full osc1*osc2: metallic

    # --- "fm" shape (2-op FM; used when osc1 or osc2 is "fm") ---
    fm_ratio: float = 2.0  # modulator = carrier * ratio
    fm_index: float = 2.0  # modulation depth (0 = pure sine)
    fm_decay: float = 0.0  # seconds; > 0 fades the index (bright attack)

    # --- "pluck" shape (Karplus-Strong string; fixed pitch per note) ---
    pluck_damping: float = 0.5  # 0 ringing .. 1 muted
    pluck_bright: float = 0.9  # 0 dull thud .. 1 full-band pick

    # --- "wavetable" shape (morphable single-cycle frames; see core.WAVETABLES) ---
    wt_table: str = "basic"  # WAVETABLE_NAMES
    wt_pos: float = 0.0  # 0..1 across the table's frames

    # --- vowel filter (vocal tract; see core.VOWELS) ---
    # amount 0 keeps every pre-existing preset sounding exactly as it did,
    # and is also the array-safe gate in note()
    formant_amount: float = 0.0  # 0 off .. 1 fully vowelled
    formant_vowel: float = 0.0  # 0..1 along a -> e -> i -> o -> u
    formant_size: float = 1.0  # vocal-tract scale: 0.5 large .. 2 small

    # --- mod matrix: 2 slots of source x destination x amount ---
    mod_lfo_rate: float = 1.0  # the assignable LFO's rate (Hz)
    lfo_shape: str = "sine"  # LFO_SHAPES — shape of the assignable LFO
    lfo_sync: str = "off"  # SYNC_DIVISIONS — overrides mod_lfo_rate
    mod1_src: str = "none"  # none | lfo | fenv | aenv | velocity
    mod1_dst: str = "cutoff"  # any params.MOD_DESTINATIONS entry
    mod1_amt: float = 0.0  # -1..1 of the destination's range
    mod2_src: str = "none"
    mod2_dst: str = "resonance"
    mod2_amt: float = 0.0
    # Slots 3 and 4 default to off, so every patch written against the
    # two-slot matrix loads and sounds exactly as it did.
    mod3_src: str = "none"
    mod3_dst: str = "drive"
    mod3_amt: float = 0.0
    mod4_src: str = "none"
    mod4_dst: str = "filter_lfo_depth"
    mod4_amt: float = 0.0

    # --- pitch bend (a performance control, like a wheel) ---
    # the wheel's position, -1..1; it bends every oscillator by
    # pitch_bend x bend_range semitones, after glide, vibrato and the pitch
    # envelope. Drawn as a curve it bends the loop; the live keys' bend
    # strip / MIDI wheel / XY axis move it live. 0 = every older patch.
    pitch_bend: float = 0.0
    bend_range: float = 2.0  # semitones at full bend (0..24)

    # --- character ---
    drive: float = 1.0
    glide: float = 0.0  # portamento seconds
    unison: int = 1  # extra detuned copies of the whole voice
    unison_detune: float = 12.0  # cents
    unison_spread: float = 0.7  # stereo spread of the unison voices

    # --- per-voice effects (applied inside the instrument) ---
    voice_fx: list = field(default_factory=list)

    # --- "sampler" shape (recorded audio; see synthlab/sampler.py) ---
    # Every default is "no sampler": old presets load and render unchanged
    # (studio/render.py KEY_ADDED_FIELDS keeps them out of the cache keys).
    sampler_root: int = 60  # MIDI note the sample plays at its own pitch
    sampler_start: float = 0.0  # 0..1 of the sample
    sampler_end: float = 1.0
    sampler_loop: str = "off"  # "on" wraps [loop_start, loop_end)
    sampler_loop_start: float = 0.0
    sampler_loop_end: float = 1.0
    sampler_xfade: float = 0.01  # seconds of equal-power loop crossfade
    sampler_keytrack: str = "on"  # "off": every key plays the root pitch
    sampler_mode: str = "resample"  # | "formant" (grain train, keeps timbre)
    sampler_stretch: float = 1.0  # length factor, pitch kept (granular)
    # the sample's content hash (sampler.sample_id); "" = none
    sampler_sample: str = ""
    # chops: "a,b,pitch,rev;..." (sampler.parse_slices); "" = whole sample
    sampler_slices: str = ""

    # --- oscillator extras: every default is "off", so a patch without
    # them renders bit for bit as if they did not exist
    # (render.KEY_ADDED_FIELDS keeps them out of its cache keys) ---
    # hard sync: osc2 restarts with osc1's cycle (core.sync), `sync_pitch`
    # semitones above where it would sit: sweep it (a route, a curve) and
    # the harmonics sweep with it — the sync lead
    osc2_sync: str = "off"
    sync_pitch: float = 0.0
    # the "fm4" shape (core.fm4): four sine operators in an algorithm
    fm4_algo: str = "stack"
    fm4_ratio1: float = 1.0
    fm4_ratio2: float = 2.0
    fm4_ratio3: float = 1.0
    fm4_ratio4: float = 1.0
    fm4_level1: float = 1.0
    fm4_level2: float = 0.3
    fm4_level3: float = 0.0
    fm4_level4: float = 0.0
    fm4_decay1: float = 0.0  # seconds; 0 holds the level
    fm4_decay2: float = 0.0
    fm4_decay3: float = 0.0
    fm4_decay4: float = 0.0
    fm4_feedback: float = 0.0  # operator 4 into itself
    # analog drift: a slow pitch wander, a start phase and a cutoff offset
    # per note, all seeded from the note's pitch (`drift_seed`)
    drift: float = 0.0

    # --- warp, own wavetables, one voice: again every default is "off" ---
    # a phase warp per oscillator (core.warped): a mode from WARP_MODES and
    # its amount (a curve, a route target); the shapes it bends are
    # core.WARP_SHAPES
    osc1_warp: str = "off"
    osc1_warp_amt: float = 0.0
    osc2_warp: str = "off"
    osc2_warp_amt: float = 0.0
    # your own wavetable (synthlab.wavetables): its content hash; "" plays
    # `wt_table`, and a hash this machine doesn't hold plays "basic"
    wt_custom: str = ""
    # one voice: "mono" retriggers on every note, "legato" glides into a
    # note played over a held one without retriggering; `glide_mode`
    # "legato" glides only then (sequencer.mono_lines)
    voice_mode: str = "poly"
    glide_mode: str = "always"
    # --- filters and modulation. Every default is "off", so a patch
    # without them renders bit for bit as if they did not exist
    # (render.KEY_ADDED_FIELDS keeps them out of its cache keys) ---
    # filter 2: after filter 1 ("serial"), beside it ("parallel": the two
    # averaged) or nowhere; any type of params.FILTER_TYPES_ALL, its cutoff
    # following the key and the filter envelope as filter 1's does
    filter2_route: str = "off"
    filter2_type: str = "lowpass"
    filter2_cutoff: float = 1200.0
    filter2_resonance: float = 0.2
    filter2_key_track: float = 0.0
    filter2_env: float = 0.0  # Hz of the filter envelope added to its cutoff
    # two general envelopes (route sources "env3", "env4"), gated as the
    # filter envelope is
    env3_attack: float = 0.01
    env3_decay: float = 0.3
    env3_sustain: float = 0.0
    env3_release: float = 0.3
    env3_curve: float = 2.0
    env4_attack: float = 0.5
    env4_decay: float = 1.0
    env4_sustain: float = 1.0
    env4_release: float = 0.5
    env4_curve: float = 2.0
    # LFO 2 and 3 (route sources "lfo2", "lfo3"): rate, shape (the general
    # LFO shapes), tempo sync, start phase, and retrig (each note from its
    # phase) or free (each note picks the cycle up at its own seeded place)
    lfo2_rate: float = 2.0
    lfo2_shape: str = "sine"
    lfo2_sync: str = "off"
    lfo2_phase: float = 0.0
    lfo2_mode: str = "retrig"
    lfo3_rate: float = 0.5
    lfo3_shape: str = "sine"
    lfo3_sync: str = "off"
    lfo3_phase: float = 0.0
    lfo3_mode: str = "retrig"
    # the drawn shape of LFO 1, 2, 3 ("x:y,x:y,...", core.clean_draw)
    lfo_draw: str = ""
    lfo2_draw: str = ""
    lfo3_draw: str = ""
    # performance controls, as pitch_bend: the mod wheel (MIDI CC 1) and
    # aftertouch (channel pressure), 0..1, route sources "wheel" and
    # "aftertouch"; drawn as curves they move the loop
    mod_wheel: float = 0.0
    aftertouch: float = 0.0
    # route slots 5-12 (the matrix is twelve: params.MOD_SLOTS), off
    mod5_src: str = "none"
    mod5_dst: str = "cutoff"
    mod5_amt: float = 0.0
    mod6_src: str = "none"
    mod6_dst: str = "cutoff"
    mod6_amt: float = 0.0
    mod7_src: str = "none"
    mod7_dst: str = "cutoff"
    mod7_amt: float = 0.0
    mod8_src: str = "none"
    mod8_dst: str = "cutoff"
    mod8_amt: float = 0.0
    mod9_src: str = "none"
    mod9_dst: str = "cutoff"
    mod9_amt: float = 0.0
    mod10_src: str = "none"
    mod10_dst: str = "cutoff"
    mod10_amt: float = 0.0
    mod11_src: str = "none"
    mod11_dst: str = "cutoff"
    mod11_amt: float = 0.0
    mod12_src: str = "none"
    mod12_dst: str = "cutoff"
    mod12_amt: float = 0.0
    # a multi-sample instrument: the sampler's zone map,
    # "sid,lo,hi,root,vlo,vhi,rr,gain,tune,...;..." (sampler.parse_zones).
    # Set, it plays instead of sampler_sample / sampler_slices: a zone by
    # the note's key, velocity and round-robin strike. "" = one sample
    sampler_zones: str = ""

    def tweak(self, **kw) -> Patch:
        """Return a copy with parameters changed: pad.tweak(cutoff=800)."""
        return replace(self, **kw)

    def to_json(self) -> dict:
        """The one serialization: every field except the callable voice_fx.
        render.patch_key/RenderInputs.key() hash this, so its shape must
        never change out from under an existing cache key."""
        return {k: v for k, v in asdict(self).items() if k != "voice_fx"}

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_json(), f, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> Patch:
        """Tolerant load: unknown keys (a field renamed or removed since the
        file was written) are dropped with a warning, never fatal."""
        names = {f.name for f in fields(cls)}
        unknown = set(d) - names
        if unknown:
            log.warning("Patch: ignoring unknown fields %s", sorted(unknown))
        return cls(**{k: v for k, v in d.items() if k in names and k != "voice_fx"})

    @classmethod
    def load(cls, path: str) -> Patch:
        with open(path) as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def random(cls, seed: int | None = None, name: str = "random") -> Patch:
        """Roll a new sound. Surprisingly good hit rate."""
        rng = np.random.default_rng(seed)

        def pick(seq):
            return seq[int(rng.integers(len(seq)))]

        return cls(
            name=name,
            osc1=pick(["saw", "square", "triangle", "supersaw", "sine"]),
            osc2=pick([None, "saw", "square", "triangle"]),
            osc2_octave=int(rng.integers(-2, 2)),
            osc2_detune=float(rng.uniform(-15, 15)),
            sub_level=float(rng.uniform(0, 0.6)),
            noise_level=float(rng.uniform(0, 0.15)),
            filter_type=pick(["ladder", "lowpass", "bandpass"]),
            cutoff=float(rng.uniform(200, 4000)),
            resonance=float(rng.uniform(0.05, 0.8)),
            filter_env=float(rng.uniform(0, 5000)),
            filter_decay=float(rng.uniform(0.05, 1.2)),
            attack=float(rng.uniform(0.002, 0.4)),
            decay=float(rng.uniform(0.05, 0.8)),
            sustain=float(rng.uniform(0.0, 0.9)),
            release=float(rng.uniform(0.05, 1.5)),
            vibrato_depth=float(rng.choice([0, 0, 0.1, 0.3])),
            drive=float(rng.uniform(1.0, 4.0)),
            unison=int(rng.integers(1, 5)),
        )


def synced_rate(division: str, bpm: float) -> float | None:
    """Hz for a sync division at `bpm`, or None when the division is off."""
    beats = SYNC_DIVISIONS.get(division, 0.0)
    if beats <= 0:
        return None
    return (bpm / 60.0) / beats  # cycles per second


def at_bpm(p: Patch, bpm: float) -> Patch:
    """Resolve any synced LFO rate against `bpm`. A no-op for an unsynced
    patch, so it is safe to call on every render path."""
    kw: dict[str, float] = {}
    rate = synced_rate(p.lfo_sync, bpm)
    if rate is not None:
        kw["mod_lfo_rate"] = rate
    rate = synced_rate(p.filter_lfo_sync, bpm)
    if rate is not None:
        kw["filter_lfo_rate"] = rate
    for k in (2, 3):  # the general LFOs
        rate = synced_rate(getattr(p, f"lfo{k}_sync"), bpm)
        if rate is not None:
            kw[f"lfo{k}_rate"] = rate
    return replace(p, **kw) if kw else p


def _apply_mod_matrix(p: Patch, n: int, sr: int, velocity: float, gate):
    """Resolve the patch's mod slots into per-sample destination arrays.
    Returns `p` untouched when no slot is active (a route to "pitch" is
    `_mod_routes`' second value, not a field of the patch)."""
    return _mod_routes(p, n, sr, velocity, gate)[0]


def _release_eff(release, duration: float):
    """The release an envelope is asked for on a note of `duration` seconds:
    clamped so a note always sounds even when the release tail is longer
    than (or equal to) the requested duration. Without a gate the hold is
    whatever the release leaves, so a release that long collapses it to
    nothing and the whole envelope is silence. Keeps a 50 ms audible floor.
    With a gate the release runs for its own time from the key-up
    (`core.adsr`), fitted into the buffer when that ends first — so the
    clamp only ever trims a release the buffer could not have held."""
    return min(release, max(0.0, duration - 0.05))


def _filter_env(p: Patch, n: int, sr: int, gate, duration: float) -> np.ndarray:
    """The filter envelope over a note's `n` samples (also the "fenv" mod
    source). Without a gate: `adsr` with the release clamp the amp envelope
    has — a filter release as long as the note left no hold, so the sweep
    was all zeros and the filter sat shut at its base cutoff.

    With a gate it releases over `filter_release` from the key-up, and
    where the buffer ends first it is simply cut there: the amp envelope
    is what lands the note on silence, the filter only has to be wherever
    it had got to. `adsr` alone would fit the release into the buffer —
    the 0.09 s an amp release leaves, for a 0.2 s filter release — which
    is the buffer setting the sound again, and not what a live key plays
    (its filter is still closing when the voice ends). So the envelope is
    drawn on a buffer long enough for its release, then cut to the note."""
    times = (p.filter_attack, p.filter_decay, p.filter_sustain)
    return _gated_env(n, times, p.filter_release, p.env_curve, gate, sr, duration)


def _gated_env(n: int, times, release, curve, gate, sr: int, duration: float):
    """`_filter_env`'s envelope for any (attack, decay, sustain), release and
    curve: the filter envelope's, and the general env3 / env4."""
    if gate is None:
        rel = _release_eff(release, duration)
        return adsr(n, *times, rel, None, sr, curve=curve)
    gate_n = n_samples(gate, sr)
    room = n if gate_n >= n else max(n, gate_n + n_samples(release, sr))
    return adsr(room, *times, release, gate, sr, curve=curve)[:n]


def lfo_free_phase(f0: float, k: int) -> float:
    """Where a free-running general LFO (`lfoK_mode` "free") picks its cycle
    up on a note: a draw from the note's seed (`drift_seed`: its pitch), so
    the loop and every cache key hold and a live key is the same note."""
    return drift_u(drift_seed(f0), _LFO_FREE_KEY + k)


def general_lfo(p: Patch, k: int, n: int, sr: int, f0: float | None) -> np.ndarray:
    """General LFO `k` (1, 2, 3) over a note's `n` samples, -1..1. LFO 1 in
    an old shape is the call it always was (`core.lfo`); the new shapes and
    LFO 2 / 3 are `core.lfo_ext` (LFO 1 always restarts at phase 0)."""
    if k == 1:
        shape, rate, phase, draw = p.lfo_shape, p.mod_lfo_rate, 0.0, p.lfo_draw
        if shape in LFO_SHAPES:
            return lfo(rate, n, shape, sr)
    else:
        shape, rate = getattr(p, f"lfo{k}_shape"), getattr(p, f"lfo{k}_rate")
        phase, draw = float(getattr(p, f"lfo{k}_phase")), getattr(p, f"lfo{k}_draw")
        if getattr(p, f"lfo{k}_mode") == "free" and f0 is not None:
            phase += lfo_free_phase(f0, k)
    seed = zlib.crc32(f"lfo{k}".encode())
    return core.lfo_ext(shape, rate, n, sr, phase, seed, draw, table_seed=k - 1)


def _note_midi(f0: float) -> float:
    return 69 + 12 * math.log2(max(float(f0), 1e-6) / 440.0)


def _mod_source(p: Patch, src: str, n: int, sr: int, velocity, gate, duration, f0):
    """One route source over the note: -1..1 (the LFOs, the key, random) or
    0..1 (the envelopes, velocity, the wheel, aftertouch)."""
    if src == "lfo":
        return general_lfo(p, 1, n, sr, f0)
    if src in ("lfo2", "lfo3"):
        return general_lfo(p, int(src[3]), n, sr, f0)
    if src == "fenv":
        return _filter_env(p, n, sr, gate, duration)
    if src == "aenv":
        rel = _release_eff(p.release, duration)
        return adsr(n, p.attack, p.decay, p.sustain, rel, gate, sr, curve=p.env_curve)
    if src in ("env3", "env4"):
        times = tuple(getattr(p, f"{src}_{t}") for t in ("attack", "decay", "sustain"))
        rel, curve = getattr(p, f"{src}_release"), getattr(p, f"{src}_curve")
        return _gated_env(n, times, rel, curve, gate, sr, duration)
    if src in ("wheel", "aftertouch"):
        v = p.mod_wheel if src == "wheel" else p.aftertouch
        return np.clip(as_array(v, n), 0.0, 1.0)
    hz = 261.63 if f0 is None else f0
    if src == "key":
        key = (_note_midi(hz) - 60) / KEY_SOURCE_SPAN
        return np.full(n, min(1.0, max(-1.0, key)))
    if src == "random":
        return np.full(n, 2 * drift_u(drift_seed(hz), _RANDOM_KEY) - 1)
    return np.full(n, velocity)  # "velocity": slot_active admits only real sources


def _fit_arrays(p: Patch, n: int) -> Patch:
    """`p` with every automation array at the render's length (`as_array`'s
    resampling). Done once, before anything reads the patch: the native
    stack resamples through `_series` and the Python oscillator loop, the
    filter and the routes did not, so a control-rate curve either raised a
    broadcast error or rendered differently per path. An array already `n`
    long is left alone, and so is the patch when all of them are."""
    fit = {
        k: as_array(v, n)
        for k, v in vars(p).items()
        if isinstance(v, np.ndarray) and v.ndim == 1 and len(v) != n
    }
    return p.tweak(**fit) if fit else p


def _mod_routes(
    p: Patch,
    n: int,
    sr: int,
    velocity: float,
    gate,
    duration: float | None = None,
    f0: float | None = None,
):
    """(`p` with its routed fields as per-sample arrays, the routes to
    "pitch" summed into semitones per sample — or None when none is on).
    A pitch route adds amount x PITCH_ROUTE_ST x the source, unclipped:
    there is no base value to keep inside a span. `duration` is the note's
    length in seconds (`n` samples' worth when not given): the envelope
    sources take the release `Synth.note`'s own envelopes do. `f0` is the
    note's pitch: the "key" and "random" sources and a free LFO read it."""
    duration = n / sr if duration is None else duration
    changes: dict = {}
    pitch = None
    sources: dict[str, np.ndarray] = {}
    for slot in MOD_SLOTS:
        if not slot_active(p, slot):
            continue
        src, dst, amt = (getattr(p, f"{slot}_{k}") for k in ("src", "dst", "amt"))
        if src not in sources:
            sources[src] = _mod_source(p, src, n, sr, velocity, gate, duration, f0)
        if dst == PITCH_ROUTE:
            bend = amt * PITCH_ROUTE_ST * sources[src]
            pitch = bend if pitch is None else pitch + bend
            continue
        lo, hi = MOD_RANGES[dst]
        base = changes.get(dst, getattr(p, dst))
        # Depth is a fraction of the route's span, but the clip must not
        # pull the base itself into that span: clipping to (lo, hi) alone
        # flattened a 12 kHz cutoff to 8 kHz the moment any route touched it.
        # The bounds come from the patch's own value, not an earlier route's
        # output: that one's min/max over the whole note is unknowable at
        # the first sample, and the live voice applies routes as it plays.
        b = np.asarray(getattr(p, dst), dtype=np.float64)
        clip_lo = min(lo, float(np.min(b)))
        clip_hi = max(hi, float(np.max(b)))
        changes[dst] = np.clip(base + amt * (hi - lo) * sources[src], clip_lo, clip_hi)
    return (p.tweak(**changes) if changes else p), pitch


def _mono(x) -> np.ndarray:
    x = np.asarray(x)
    return x.mean(axis=1) if x.ndim == 2 else x


def _times(a, b) -> np.ndarray:
    """a * b where either may be a stereo (n, 2) signal and the other a
    scalar or a per-sample (n,) array."""
    a, b = np.asarray(a), np.asarray(b)
    if a.ndim == 1 and b.ndim == 2:
        a = a[:, None]
    elif a.ndim == 2 and b.ndim == 1:
        b = b[:, None]
    return a * b


def _plus(a, b) -> np.ndarray:
    a, b = np.asarray(a), np.asarray(b)
    if a.ndim == 1 and b.ndim == 2:
        a = a[:, None]
    elif a.ndim == 2 and b.ndim == 1:
        b = b[:, None]
    return a + b


class Synth:
    """Renders patches into audio. Stateless — safe to reuse everywhere."""

    def __init__(self, patch: Patch, sr: int = SR):
        self.patch = patch
        self.sr = sr

    # -- one voice ---------------------------------------------------------
    def note(
        self,
        pitch,
        duration: float,
        velocity: float = 1.0,
        gate: float | None = None,
        pan: float = 0.0,
        from_pitch=None,
        path=(),
        rr: int = 0,
    ) -> np.ndarray:
        """
        Render a single note to stereo audio.

        pitch      a note name ('C4') or a MIDI number (60)
        duration   total length including the release tail, in seconds
        gate       how long the key is held (defaults to duration - release)
        from_pitch start pitch for glide/portamento
        path       a legato line's later notes: (seconds, pitch) pairs the
                   one voice glides to (over `glide`, or at once) without
                   retriggering; the filter's key tracking follows them
        rr         which strike of its key this note is in its phrase
                   (sampler.Strikes): a zone map's round-robin take. Read
                   by nothing else
        """
        sr = self.sr
        n = n_samples(duration, sr)
        f0 = note_to_hz(pitch)
        p, routed_st = _mod_routes(
            _fit_arrays(self.patch, n), n, sr, velocity, gate, duration, f0
        )

        # pitch curve (glide + vibrato)
        freq = np.full(n, f0)
        if p.glide > 0 and from_pitch is not None:
            g = min(n, n_samples(p.glide, sr))
            start = note_to_hz(from_pitch)
            freq[:g] = np.geomspace(start, f0, g)
        key = f0
        if path:  # a legato line: the voice re-aimed where each note starts
            key = np.full(n, f0)
            glen = n_samples(p.glide, sr) if p.glide > 0 else 1
            for t, nxt in path:
                at = int(round(float(t) * sr))
                if at >= n:
                    break
                hz = note_to_hz(nxt)
                m = min(n - at, glen)
                freq[at : at + m] = np.geomspace(freq[at], hz, m)
                freq[at + m :] = hz
                key[at:] = hz
        if np.any(np.asarray(p.vibrato_depth) > 0):
            fade = np.clip((np.arange(n) / sr - p.vibrato_delay) / 0.3, 0, 1)
            vib = lfo(p.vibrato_rate, n, "sine", sr) * p.vibrato_depth * fade
            freq = freq * 2 ** (vib / 12)
        if p.pitch_env != 0.0:
            tau = max(p.pitch_env_decay, 1e-3) * sr
            penv = np.exp(-5.0 * np.arange(n) / tau)  # ~-43dB by decay time
            freq = freq * 2 ** (p.pitch_env * penv / 12)
        # pitch bend (the wheel x its range) and routes to "pitch", in
        # semitones, on top of all of the above; array-safe (a drawn bend)
        st = np.asarray(p.pitch_bend, dtype=np.float64) * p.bend_range
        if st.ndim:  # a drawn bend: one value per sample, as any curve
            st = as_array(st, n)
        if routed_st is not None:
            st = st + routed_st
        if np.any(st != 0):
            freq = freq * 2 ** (st / 12)
        # analog drift: the note's own slow wander, on top of everything
        drift = _drift(p)
        dseed = drift_seed(f0) if drift > 0 else None
        if dseed is not None:
            freq = freq * 2 ** (drift_cents(dseed, drift, n, sr) / 1200)

        # oscillator stack, optionally in unison
        voices = max(1, int(p.unison))
        mix = self._native_stack(p, freq, n, voices, pan)
        if mix is None:
            left = np.zeros(n)
            right = np.zeros(n)
            for u in range(voices):
                off = _unison_offset(u, voices)
                f = freq * 2 ** (off * p.unison_detune / 1200)
                # the modulated patch: routes to levels/detunes/PWM live in `p`
                raw = self._osc_stack(
                    f,
                    n,
                    seed=u,
                    p=p,
                    key_hz=f0,
                    drift_seed=dseed,
                    velocity=velocity,
                    rr=rr,
                )
                pos = off * p.unison_spread + pan
                stpair = fx.pan(raw, float(np.clip(pos, -1, 1)))
                left += stpair[:, 0]
                right += stpair[:, 1]
            mix = np.column_stack([left, right]) / np.sqrt(voices)

        # the filter's cutoff curve (mono-summed cutoff modulation applied
        # per channel)
        cutoff = cutoff2 = fenv = None
        if p.filter_type != "none" or _filter2(p):
            fenv = _filter_env(p, n, sr, gate, duration)
        if p.filter_type != "none":
            base = p.cutoff * (key / 261.63) ** p.key_track
            cutoff = base + p.filter_env * fenv * velocity
            if np.any(np.asarray(p.filter_lfo_depth) > 0):
                flfo = lfo(p.filter_lfo_rate, n, p.filter_lfo_shape, sr)
                cutoff = cutoff + flfo * p.filter_lfo_depth
            if dseed is not None:  # this note's filter sits a little off
                cutoff = cutoff * drift_cutoff(dseed, drift)
            cutoff = np.clip(cutoff, 20, sr * 0.49)
        if _filter2(p):  # its own key track and envelope amount
            base = p.filter2_cutoff * (key / 261.63) ** p.filter2_key_track
            cutoff2 = base + p.filter2_env * fenv * velocity
            if dseed is not None:
                cutoff2 = cutoff2 * drift_cutoff(dseed, drift)
            cutoff2 = np.clip(cutoff2, 20, sr * 0.49)

        # amp envelope — clamp release so a note always sounds even when the
        # release tail is longer than (or equal to) the requested duration;
        # without this the ADSR hold segment collapses to zero samples and
        # the whole envelope goes silent. Keep a 50ms audible floor.
        release_eff = _release_eff(p.release, duration)
        aenv = adsr(
            n, p.attack, p.decay, p.sustain, release_eff, gate, sr, curve=p.env_curve
        )
        if np.any(np.asarray(p.tremolo_depth) > 0):
            aenv *= 1 - p.tremolo_depth * (
                1 - lfo(p.tremolo_rate, n, "sine", sr, bipolar=False)
            )
        gain = aenv * velocity
        vowel = bool(np.any(np.asarray(p.formant_amount) > 0))
        # the ladders take the drive into their filter: no post clip after
        clip = bool(np.any(np.asarray(p.drive) > 1.0)) and p.filter_type not in _DRIVEN

        # the post chain: one native call (`_native_post`), or the stages
        # it is made of, one by one — the reference. The other filters
        # (a type past the first six, filter 2) take the stages: `core.filt`
        # is NumPy, its SVFs the native swept kernel
        post = None
        if p.filter_type in FILTER_TYPES and cutoff2 is None:
            post = self._native_post(p, mix, n, cutoff, gain, vowel, clip)
        if post is not None:
            mix = post
        else:
            # PWM and asymmetric shapes leave a DC term that no later stage
            # removes; block it here, before the filter's resonance has a
            # chance to build a resonant peak on top of an offset baseline.
            mix = dc_block(mix)
            if cutoff is not None and cutoff2 is None:
                if p.filter_type == "ladder":
                    mix = ladder(mix, cutoff, p.resonance, p.drive, sr)
                elif p.filter_type in FILTER_TYPES:
                    q = 0.707 + p.resonance * 6
                    mix = biquad(mix, p.filter_type, cutoff, q, sr)
                else:
                    mix = core.filt(
                        mix, p.filter_type, cutoff, p.resonance, p.drive, sr
                    )
            elif cutoff2 is not None:
                mix = self._two_filters(p, mix, cutoff, cutoff2)
            # vowel filter: the vocal tract sits after the source-shaping
            # filter, before the envelope tames its ring
            if vowel:
                mix = self._vowel(p, mix)
            mix *= gain[:, None]
            if clip:
                mix = soft_clip(mix, p.drive)

        if p.voice_fx:
            mix = fx.chain(mix, p.voice_fx)
        return mix

    def _two_filters(self, p: Patch, mix, cutoff, cutoff2) -> np.ndarray:
        """Filter 1 then filter 2 ("serial"), or each on the voice and the
        two averaged ("parallel"); filter 1 "none" passes the voice on."""
        sr = self.sr
        one = mix
        if cutoff is not None:
            one = core.filt(mix, p.filter_type, cutoff, p.resonance, p.drive, sr)
        kind, res = p.filter2_type, p.filter2_resonance
        if p.filter2_route == "serial":
            return core.filt(one, kind, cutoff2, res, p.drive, sr)
        return 0.5 * (one + core.filt(mix, kind, cutoff2, res, p.drive, sr))

    def _vowel(self, p: Patch, mix: np.ndarray) -> np.ndarray:
        return formant(mix, p.formant_vowel, p.formant_amount, p.formant_size, self.sr)

    def _native_post(
        self, p: Patch, mix, n: int, cutoff, gain, vowel: bool, clip: bool
    ):
        """Everything after the oscillator stack — the DC block, the filter
        (`cutoff` is its curve, None for no filter), the vowel filter, the
        amp `gain` and the drive — as one native call (`synthlab_dsp.post`),
        or None when the stages must run one by one: no native module, one
        of the chain's own kernels switched off (`SYNTHLAB_NATIVE_OFF`:
        that stage must reach its NumPy twin), or a drive only NumPy takes.
        The kernel runs the same kernels in the same order (bit-identical
        to the loose calls); `core`'s `_*_stage` functions decide what each
        one is, as its twin does. The vowel filter stays in Python, between
        two calls."""
        native = kernel("post")
        if any(kernel(k) is None for k in _POST_KERNELS):
            return None
        if np.ndim(mix) != 2 or not len(mix):
            return None
        sr = self.sr
        head: list = [("dc", core._dc_pole(sr=sr))]
        if cutoff is not None:
            if p.filter_type == "ladder":
                stages = core._ladder_stages(n, cutoff, p.resonance, p.drive, sr)
                if stages is None:
                    return None
                head += stages
            else:
                q = 0.707 + p.resonance * 6
                head.append(core._biquad_stage(n, p.filter_type, cutoff, q, sr))
        tail: list = [("gain", np.ascontiguousarray(gain, dtype=np.float64))]
        if clip:
            stages = core._clip_stages(p.drive, n)
            if stages is None:
                return None
            tail += stages
        x = np.ascontiguousarray(mix, dtype=np.float64)
        if not vowel:
            return native(x, float(sr), head + tail)
        x = self._vowel(p, native(x, float(sr), head))
        return native(np.ascontiguousarray(x, dtype=np.float64), float(sr), tail)

    def _native_stack(self, p: Patch, freq, n: int, voices: int, pan: float):
        """The whole unison stack — every voice's `_osc_stack` and its pan,
        summed and scaled — as one native call (`synthlab_dsp.stack`), or
        None when the patch needs the Python loop: a pluck or sampler
        oscillator, or no native module. What must match NumPy's
        generators is drawn here and handed in: the seeded noise, the
        supersaw start phases, the voices' pan gains. Noise is looked up as
        `core.noise` at call time, as `core.osc` does, so anything that
        swaps the generator (the parity corpus reseeds it) reaches both
        paths."""
        native = kernel("stack")
        shapes = (p.osc1, p.osc2) if p.osc2 else (p.osc1,)
        if native is None or any(s not in _STACK_SHAPES for s in shapes):
            return None
        # the oscillator extras (hard sync, a drifting start
        # phase) are the Python loop's: measured cheap enough there
        if _synced(p) or _drift(p) > 0:
            return None
        # the warp is core.warped's (oversampled), in the loop too
        if warp_of(p, 1) or (p.osc2 and warp_of(p, 2)):
            return None
        # the stack reads a wavetable with the "wavetable" kernel's code:
        # switched off, that shape goes back to the loop and its NumPy twin
        if "wavetable" in shapes and kernel("wavetable") is None:
            return None
        sr = self.sr
        offs = [_unison_offset(u, voices) for u in range(voices)]
        angles = [
            (float(np.clip(o * p.unison_spread + pan, -1, 1)) + 1) * np.pi / 4
            for o in offs
        ]
        width = 0.5
        if np.any(np.asarray(p.pwm_depth) > 0):
            width = 0.5 + lfo(p.pwm_rate, n, "sine", sr) * p.pwm_depth * 0.45
        spec = {
            "sr": float(sr),
            "freq": _series(freq, n),
            "offsets": np.array(offs),
            "gain_l": np.cos(angles),
            "gain_r": np.sin(angles),
            "unison_detune": _series(p.unison_detune, n),
            "width": _series(width, n),
            "fm_ratio": _series(p.fm_ratio, n),
            "fm_index": _series(p.fm_index, n),
            "fm_decay": float(p.fm_decay),
            "ss_ratios": _SS_RATIOS,
        }
        for i, shape in enumerate(shapes, start=1):
            seed0 = 0 if i == 1 else 100  # _osc_stack's seed / seed + 100
            spec[f"osc{i}"] = shape
            spec[f"osc{i}_octave"] = float(getattr(p, f"osc{i}_octave"))
            spec[f"osc{i}_detune"] = _series(getattr(p, f"osc{i}_detune"), n)
            spec[f"osc{i}_level"] = _series(getattr(p, f"osc{i}_level"), n)
            if shape == "noise":
                spec[f"osc{i}_noise"] = np.concatenate(
                    [core.noise(n, p.noise_color, seed0 + u) for u in range(voices)]
                )
            elif shape == "supersaw":
                spec[f"osc{i}_ss"] = np.concatenate(
                    [_ss_phases(seed0 + u) for u in range(voices)]
                )
        if "wavetable" in shapes:
            spec["wt_table"], spec["wt_caps"], spec["wt_frames"] = core._wt_native(
                _wt_table(p)
            )
            spec["wt_pos"] = _series(p.wt_pos, n)
        if p.osc2 and np.any(np.asarray(p.fm_amount) > 0):  # a route: per sample
            spec["fm_amount"] = _series(p.fm_amount, n)
        if p.osc2 and p.ring_mod > 0:
            spec["ring"] = _series(p.ring_mod, n)
        if np.any(np.asarray(p.sub_level) > 0):
            spec["sub_level"] = _series(p.sub_level, n)
        if np.any(np.asarray(p.noise_level) > 0):
            spec["noise_level"] = _series(p.noise_level, n)
            spec["noise_layer"] = np.concatenate(
                [core.noise(n, p.noise_color, u + 999) for u in range(voices)]
            )
        return native(n, spec)

    def _osc_stack(
        self,
        freq,
        n,
        seed=0,
        p: Patch | None = None,
        key_hz: float | None = None,
        drift_seed: int | None = None,
        velocity: float = 1.0,
        rr: int = 0,
    ) -> np.ndarray:
        """`p` is the patch after the mod matrix; reading `self.patch` here
        made every route to a level, detune or PWM depth silently do nothing.
        Mono (n,), or stereo (n, 2) when a "sampler" oscillator plays stereo
        audio (`fx.pan` keeps a stereo voice's image). `key_hz` is the
        played key's pitch, before octave/detune/glide/bends: the sampler
        picks a chop's slice by it, and a zone by it, `velocity` and `rr`."""
        p = self.patch if p is None else p
        sr = self.sr

        width = 0.5
        if np.any(np.asarray(p.pwm_depth) > 0):
            width = 0.5 + lfo(p.pwm_rate, n, "sine", sr) * p.pwm_depth * 0.45

        # kwargs only the "fm" / "pluck" shapes read; others ignore them
        shape_kw = {
            "ratio": p.fm_ratio,
            "index": p.fm_index,
            "index_decay": p.fm_decay,
            "damping": p.pluck_damping,
            "bright": p.pluck_bright,
            "table": _wt_table(p) if "wavetable" in (p.osc1, p.osc2) else p.wt_table,
            "pos": p.wt_pos,
            # a "noise" oscillator shape is coloured like the noise layer
            "color": p.noise_color,
        }
        if "sampler" in (p.osc1, p.osc2):
            shape_kw["sampler"] = SamplerSettings.of(p, velocity, rr)
            shape_kw["key_hz"] = key_hz
        if "fm4" in (p.osc1, p.osc2):
            shape_kw.update(
                fm4_algo=p.fm4_algo,
                fm4_ratios=[getattr(p, f"fm4_ratio{k}") for k in (1, 2, 3, 4)],
                fm4_levels=[getattr(p, f"fm4_level{k}") for k in (1, 2, 3, 4)],
                fm4_decays=[getattr(p, f"fm4_decay{k}") for k in (1, 2, 3, 4)],
                fm4_feedback=p.fm4_feedback,
            )
        # a drifting note's oscillators start where its seed puts them
        ph1 = ph2 = 0.0
        if drift_seed is not None:
            ph1, ph2 = (drift_phase(drift_seed, s, seed) for s in (0, 1))

        o2 = None
        f1 = freq * 2.0**p.osc1_octave * 2 ** (p.osc1_detune / 1200)
        if p.osc2:
            f2 = freq * 2.0**p.osc2_octave * 2 ** (p.osc2_detune / 1200)
            kw2 = {"phase0": ph2} if p.osc2 in _PHASED else {}
            if _synced(p):  # restarted by a phase at osc1's pitch (pre cross-FM)
                f2 = f2 * 2 ** (p.sync_pitch / 12)
                o2 = core.sync(p.osc2, f1, f2, n, sr, width, ph2, ph1)
            else:
                o2 = osc(
                    p.osc2,
                    f2,
                    n,
                    sr,
                    width=width,
                    seed=seed + 100,
                    warp=warp_of(p, 2),
                    **kw2,
                    **shape_kw,
                )

        if o2 is not None and np.any(np.asarray(p.fm_amount) > 0):
            # osc2 modulates osc1's frequency (through the phase integrator):
            # growls, bells, psy squelch. Modulation index scaled so 1.0 is
            # wild but never negative-frequency.
            f1 = f1 * (1.0 + 0.9 * p.fm_amount * _mono(o2))
        kw1 = {"phase0": ph1} if p.osc1 in _PHASED else {}
        o1 = osc(
            p.osc1,
            f1,
            n,
            sr,
            width=width,
            seed=seed,
            warp=warp_of(p, 1),
            **kw1,
            **shape_kw,
        )
        if o2 is not None and p.ring_mod > 0:
            o1 = o1 * (1.0 - p.ring_mod) + _times(o1, o2) * p.ring_mod
        out = _times(p.osc1_level, o1)

        if o2 is not None:
            out = _plus(out, _times(p.osc2_level, o2))

        if np.any(np.asarray(p.sub_level) > 0):
            out = _plus(out, _times(p.sub_level, osc("sine", freq * 0.5, n, sr)))

        if np.any(np.asarray(p.noise_level) > 0):
            out = _plus(
                out,
                _times(
                    p.noise_level,
                    osc("noise", 0, n, sr, color=p.noise_color, seed=seed + 999),
                ),
            )
        return out

    # -- chords ------------------------------------------------------------
    def chord(
        self,
        pitches,
        duration: float,
        velocity: float = 0.8,
        gate: float | None = None,
        spread: float = 0.0,
        strum: float = 0.0,
    ) -> np.ndarray:
        """Play several pitches at once. `strum` staggers them in seconds."""
        n = n_samples(duration + strum * len(pitches), self.sr)
        out = np.zeros((n, 2))
        for i, pitch in enumerate(pitches):
            pos = (
                0.0 if len(pitches) < 2 else (i / (len(pitches) - 1) - 0.5) * 2 * spread
            )
            v = self.note(pitch, duration, velocity, gate, pan=pos)
            off = n_samples(strum * i, self.sr) if strum else 0
            out[off : off + len(v)] += v[: max(0, n - off)]
        return out / np.sqrt(max(1, len(pitches)))


# ---------------------------------------------------------------------------
# a starter patch library
# ---------------------------------------------------------------------------

PRESETS: dict[str, Patch] = {
    "bass_reese": Patch(
        name="bass_reese",
        osc1="saw",
        osc2="saw",
        osc2_octave=0,
        osc2_detune=18,
        sub_level=0.7,
        cutoff=180,
        resonance=0.3,
        filter_env=900,
        filter_decay=0.35,
        filter_sustain=0.25,
        attack=0.005,
        decay=0.2,
        sustain=0.85,
        release=0.12,
        drive=2.2,
        unison=2,
        unison_detune=9,
        unison_spread=0.25,
    ),
    "bass_acid": Patch(
        name="bass_acid",
        osc1="saw",
        osc2=None,
        sub_level=0.35,
        cutoff=140,
        resonance=0.85,
        filter_env=3800,
        filter_decay=0.22,
        filter_sustain=0.05,
        attack=0.002,
        decay=0.15,
        sustain=0.4,
        release=0.08,
        drive=3.0,
        key_track=0.5,
    ),
    "lead_supersaw": Patch(
        name="lead_supersaw",
        osc1="supersaw",
        osc1_level=0.9,
        osc2="square",
        osc2_level=0.18,
        osc2_octave=-1,
        sub_level=0.2,
        cutoff=1400,
        resonance=0.2,
        filter_env=4200,
        filter_attack=0.02,
        filter_decay=0.8,
        filter_sustain=0.5,
        attack=0.02,
        decay=0.4,
        sustain=0.75,
        release=0.5,
        vibrato_depth=0.12,
        vibrato_rate=5.5,
        unison=3,
        unison_detune=14,
        unison_spread=0.9,
        drive=1.4,
    ),
    "pad_warm": Patch(
        name="pad_warm",
        osc1="saw",
        osc2="triangle",
        osc2_octave=-1,
        osc2_detune=-9,
        sub_level=0.25,
        noise_level=0.03,
        cutoff=600,
        resonance=0.15,
        filter_env=1800,
        filter_attack=1.2,
        filter_decay=2.5,
        filter_sustain=0.6,
        attack=0.9,
        decay=1.5,
        sustain=0.75,
        release=2.2,
        vibrato_depth=0.05,
        vibrato_rate=0.4,
        unison=3,
        unison_detune=11,
        unison_spread=1.0,
        drive=1.1,
    ),
    "pluck_glass": Patch(
        name="pluck_glass",
        osc1="triangle",
        osc2="sine",
        osc2_octave=1,
        osc2_level=0.35,
        cutoff=1800,
        resonance=0.35,
        filter_env=5000,
        filter_attack=0.001,
        filter_decay=0.18,
        filter_sustain=0.0,
        attack=0.001,
        decay=0.35,
        sustain=0.0,
        release=0.4,
        drive=1.2,
    ),
    "keys_ep": Patch(
        name="keys_ep",
        osc1="sine",
        osc2="sine",
        osc2_octave=2,
        osc2_level=0.22,
        filter_type="lowpass",
        cutoff=1600,
        resonance=0.1,
        filter_env=2600,
        filter_decay=0.5,
        filter_sustain=0.15,
        attack=0.003,
        decay=1.1,
        sustain=0.25,
        release=0.7,
        tremolo_rate=4.2,
        tremolo_depth=0.12,
    ),
    "stab_hoover": Patch(
        name="stab_hoover",
        osc1="supersaw",
        osc2="square",
        osc2_level=0.4,
        osc2_octave=-1,
        pwm_depth=0.6,
        pwm_rate=0.8,
        cutoff=700,
        resonance=0.45,
        filter_env=4500,
        filter_decay=0.5,
        filter_sustain=0.25,
        attack=0.01,
        decay=0.4,
        sustain=0.5,
        release=0.35,
        drive=2.0,
        unison=2,
        unison_detune=20,
    ),
    "brass_synth": Patch(
        name="brass_synth",
        osc1="saw",
        osc2="saw",
        osc2_detune=-12,
        cutoff=500,
        resonance=0.2,
        filter_env=3200,
        filter_attack=0.09,
        filter_decay=0.6,
        filter_sustain=0.55,
        attack=0.06,
        decay=0.3,
        sustain=0.8,
        release=0.3,
        drive=1.6,
        unison=2,
    ),
    "bell_fm": Patch(
        name="bell_fm",
        osc1="sine",
        osc2="sine",
        osc2_octave=2,
        osc2_detune=3,
        osc2_level=0.45,
        filter_type="lowpass",
        cutoff=4000,
        filter_env=3000,
        filter_decay=0.6,
        filter_sustain=0.1,
        attack=0.001,
        decay=1.8,
        sustain=0.0,
        release=1.6,
    ),
}


def preset(name: str) -> Patch:
    return PRESETS[name]


def list_presets() -> list[str]:
    return sorted(PRESETS)
