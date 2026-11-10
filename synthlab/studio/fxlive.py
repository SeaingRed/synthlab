"""Which FX units the live keys play, and how.

The live voice (voice-worklet.js) sends each layer through that layer's
FX chain and the sum through the master chain, as the loop render does —
`FX_LIVE` says how each `fxrack.FX_UNITS` kind gets there. Mirrors the
worklet whitelist (`worklet.py`): a unit is live only when something
measures it against `effects.py` (`tests/studio/test_live_fx.py`).

    "js"      static/fx-worklet.js runs effects.py's own algorithm (the
              EQ's bands through static/eq-worklet.js, the pitch shifter
              and the live vocal units through static/vox-worklet.js, the
              FX2 units — auto-filter, tremolo, tape, ping-pong,
              formant and frequency shifters, exciter, vocoder — and
              the dynamics (OTT, limiter, transient shaper)
              through static/fx2-worklet.js, all in the same scope)
    "clock"   the same, timed on the transport clock (gate / pump /
              sidechain: the loop's own beat, not the key's)
    "native"  a Web Audio node computes it exactly: reverb is a
              ConvolverNode loaded with `effects.reverb_ir` (GET
              /fx/ir.wav), the plate one with `effects.plate_ir` (GET
              /fx/ir.wav?kind=plate), the spring one with
              `effects.spring_ir` (?kind=spring), lowpass/highpass a
              BiquadFilterNode (RBJ; its Q in dB)
    "offline" not reproduced live: on the edited slot or the master, a
              chain holding one makes live keys fall back to the WAV (dry,
              as before). Granular and shimmer read the whole buffer;
              compress's detector is peak-hold on the loop's sum; vinyl is
              seeded noise over the whole buffer. A pre-rendered lane
              (below) bakes them in like any other unit — except beat
              repeat and the tape stop (`TRANSPORT`), which act on the
              loop's grid: a lane holding one stays live, like a clock
              unit's, and blocks (the keys take the dry WAV: a held key
              is never stopped or stuttered by where the loop happens to
              be).

Pre-rendered lanes (live CPU): only the edited slot plays in the worklet.
Every other lane a key sounds on (the main lane, the followers) plays
server renders of that lane's note at its octave with its gain and its FX
baked in (`render.lane_note`, `/note/{pitch}.wav?layer=N`), through the
live master chain — `lane_mode` says which. A lane whose chain holds a
clock unit (gate / pump / sidechain: the transport's beat, which a
one-shot can't know) stays live. `/patch.json?live=all` puts every lane
back in the worklet (the A/B dev flag).
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import replace

from .. import effects as fx
from .. import sampler, wavetables
from ..synth import Patch, at_bpm
from . import worklet
from .fxrack import FX_UNITS, clean_chain, kick_steps, resolve_unit
from .render import patch_key
from .state import Arrangement, Lane, audible_layers

FX_LIVE: dict[str, str] = {
    "drive": "js",
    "clip": "js",
    "fold": "js",
    "bitcrush": "js",
    "ringmod": "js",
    "comb": "js",
    "width": "js",
    "delay": "js",
    "chorus": "js",
    "flanger": "js",
    "phaser": "js",
    "eq": "js",
    "doubler": "js",
    "rotary": "js",
    "tape_echo": "js",
    "telephone": "js",
    "pitch": "js",
    "plate": "native",
    "autotune": "js",
    "harmony": "js",
    "vox_comp": "js",
    "deess": "js",
    "vgate": "js",
    "autofilter": "js",
    "tremolo": "js",
    "tape": "js",
    "pingpong": "js",
    "formant_shift": "js",
    "freqshift": "js",
    "exciter": "js",
    "vocoder_fx": "js",
    "ott": "js",
    "limiter": "js",
    "transient": "js",
    "spring": "native",
    "gate": "clock",
    "pump": "clock",
    "sidechain": "clock",
    "reverb": "native",
    "lowpass": "native",
    "highpass": "native",
    "compress": "offline",
    "granular": "offline",
    "shimmer": "offline",
    "vinyl": "offline",
    "beat_repeat": "offline",
    "stop": "offline",
}
assert set(FX_LIVE) == set(FX_UNITS), "every FX unit needs a live class"


CLOCK = frozenset(k for k, v in FX_LIVE.items() if v == "clock")
# offline units that are timed on the transport, like the clock ones: a
# one-shot key render can't know where the loop's beat is, so a lane
# holding one is never pre-rendered — it stays "live", where the unit
# blocks the worklet and keys take the dry WAV fallback
TRANSPORT = frozenset({"beat_repeat", "stop"})
assert TRANSPORT <= {k for k, v in FX_LIVE.items() if v == "offline"}


def lane_mode(units, all_live: bool = False) -> str:
    """ "live" (a voice node in the worklet) or "buffer" (pre-rendered
    notes) for a lane that isn't the edited slot."""
    if all_live or any(u["kind"] in CLOCK | TRANSPORT for u in clean_chain(units)):
        return "live"
    return "buffer"


def lane_arr(arr: Arrangement) -> Arrangement:
    """The arrangement a pre-rendered lane's chain is built from: a lane
    with a sidechain stays live, so no kicks are needed."""
    return replace(arr, drums=[])


def lane_key(voice, units, arr: Arrangement) -> str:
    """THE key of a pre-rendered lane's notes: its slot, patch, octave,
    gain and resolved chain. /patch.json hands it out as the lane's
    `note_key` and `/note?layer=` files its renders under it (X-Patch-Key),
    so keys.js can tell a current render from a stand-in."""
    slot, p, octave, gain = voice
    chain = live_chain(units, arr, False)
    blob = json.dumps([slot, patch_key(p), octave, gain, chain], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def offline_units(units) -> list[str]:
    """The kinds in a chain the live keys can't play."""
    return [u["kind"] for u in clean_chain(units) if FX_LIVE[u["kind"]] == "offline"]


def keys_bpm(s) -> float:
    """The tempo a key's synced rates resolve against: the loop's, or the
    song's while Synths edits a track's sound (its ▶ plays at the song's
    too: `StudioService.render_inputs`)."""
    if getattr(s, "song_edit_track", None) is not None:
        return float(s.song["bpm"])
    return s.arrangement.bpm


# /patch.json hands the keys' view back with this on it when the typing
# rows play one layer each (rowsounds.js), and keys.js sends that view with
# every /note: the two routes then read one set of lanes
ROWS = ":rows"


def key_lanes(s, view: str) -> list[Lane]:
    """What a key sounds on for `view`: its ▶ set (`audible_layers`,
    `for_keys`) — or, for a view ending in `ROWS`, every unmuted slot that
    holds a sound, a plain one playing its own notes included: there a
    typing row is a layer's own keyboard, and the browser sends each note
    to one lane of this set (livefx.js `post`). A follower keeps its
    octave; solo is not read (a row names its layer outright)."""
    if not view.endswith(ROWS):
        return audible_layers(s, view or "editor", for_keys=True)
    return [
        Lane(
            i,
            int(lay["follow"].get("octave", 0)) if s.follows(i) else 0,
            i == s.active_layer,
        )
        for i, lay in enumerate(s.layers)
        if not lay.get("muted") and (i == s.active_layer or lay.get("patch"))
    ]


def key_voices(s, lanes: list[Lane]) -> list[tuple[int, Patch, int, float]]:
    """What a key sounds on: (0-based slot, patch with synced rates
    resolved, octave, gain) per lane of `audible_layers(..., for_keys=True)`
    — the edited slot's live patch, every other slot's stored one. The
    worklet stack (`live_stack`) and the WAV fallback (`/note`) both read
    this, so they sound the same set."""
    bpm = keys_bpm(s)
    out = []
    for ln in lanes:
        lay = s.layers[ln.slot]
        # a copy of the live patch: /note renders it outside the lock
        p = replace(s.patch) if ln.edited else Patch.from_dict(lay["patch"])
        out.append(
            (ln.slot, at_bpm(p, bpm), ln.octave or 0, float(lay.get("gain", 1.0)))
        )
    return out


def voices_key(voices, edited: int) -> str:
    """The X-Patch-Key of a key rendered over `voices`: the patch's own
    `render.patch_key` when the edited slot sounds alone at its pitch and
    unity gain (every single-layer session keeps its key renders), else a
    hash of the whole stack."""
    if len(voices) == 1:
        slot, p, octave, gain = voices[0]
        if slot == edited and octave == 0 and gain == 1.0:
            return patch_key(p)
    blob = json.dumps(
        [[slot, patch_key(p), octave, gain] for slot, p, octave, gain in voices]
    )
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def live_stack(s, lanes: list[Lane], drums: bool, all_live: bool = False) -> dict:
    """/patch.json's picture of what a key sounds through — `lanes`, the
    view's `audible_layers(..., for_keys=True)`, the very predicate its ▶
    renders by: whether the edited slot sounds (`heard`) and at which
    `octave`, its FX and gain; every other lane (the main lane and the
    followers — never a plain slot playing its own notes) as its own patch,
    `slot`/`octave`/`gain`/`fx`; the master chain — plus the one verdict
    piano.js reads, `worklet_ok`, true only when the live voice plays
    every lane and every chain. `worklet_blockers` says what fails it: a
    field name for the edited patch, `layer<N>:<field>` for another
    slot's, `fx:<kind>` for an offline unit anywhere. A muted or
    soloed-out slot blocks nothing: it isn't played. Each other lane's
    `mode` (`lane_mode`): "buffer" lanes play pre-rendered notes filed
    under `note_key` and block nothing — `live_ok` says whether the
    worklet could stand in for one until its renders land — while "live"
    ones (and all of them with `all_live`) block as the edited slot does."""
    arr, act = s.arrangement, s.active_layer
    voices = key_voices(s, lanes)
    heard = next((v for v in voices if v[0] == act), None)
    blockers: list[str] = []
    chains = [s.fx_master] if voices else []
    layers = []
    for voice in voices:
        slot, p, octave, gain = voice
        fx_units = s.layers[slot].get("fx") or []
        if slot == act:
            chains.append(fx_units)
            blockers += worklet.blockers(s.patch)
            continue
        mine = [f"layer{slot + 1}:{b}" for b in worklet.blockers(p)]
        mine += [f"fx:{k}" for k in offline_units(fx_units)]
        mode = lane_mode(fx_units, all_live)
        if mode == "live":
            blockers += mine
        layers.append(
            {
                **p.to_json(),
                "slot": slot + 1,
                "octave": octave,
                "gain": gain,
                "fx": live_chain(fx_units, arr, drums),
                "mode": mode,
                "live_ok": not mine,
                "sampler_live": sampler.live_url(p),
                "wt_live": wavetables.live_url(p),
                "note_key": lane_key(voice, fx_units, lane_arr(arr)),
            }
        )
    blockers += [f"fx:{k}" for c in chains for k in offline_units(c)]
    active = s.layers[act]
    return {
        "worklet_ok": not blockers,
        "worklet_blockers": blockers,
        "heard": heard is not None,
        "slot": act + 1,
        "octave": heard[2] if heard else 0,
        "layers": layers,
        "fx": live_chain(active.get("fx") or [], arr, drums),
        "gain": float(active.get("gain", 1.0)),
        "master": live_chain(s.fx_master, arr, drums),
        "bpm": arr.bpm,
        "note_key": voices_key(voices, act),
    }


def _defaults(name: str) -> dict:
    return {
        k: v.default
        for k, v in inspect.signature(getattr(fx, name)).parameters.items()
        if v.default is not inspect.Parameter.empty
    }


# the pitch shifter's grain and its splice (how far a grain may move, the
# window, the bias): the pitch unit's, the auto-tune's and the harmonizer's
_SHIFTER = {
    "grain": fx.PITCH_GRAIN,
    "search": fx.PITCH_SEARCH,
    "window": fx.PITCH_WINDOW,
    "bias": fx.PITCH_BIAS,
}
# the pitch tracker's window and range, and the auto-tune's glide and hold
_TRACKER = {
    "track_window": fx.TRACK_WINDOW,
    "track_lo": fx.TRACK_LO,
    "track_hi": fx.TRACK_HI,
    "track_threshold": fx.TRACK_THRESHOLD,
    "track_floor": fx.TRACK_FLOOR,
    "glide": fx.AUTOTUNE_GLIDE,
    "hold": fx.AUTOTUNE_HOLD,
}


def _tape(kw: dict) -> dict:
    peak, top, low = fx.tape_filters(kw["bump"], kw["tone"])
    return {
        "tape_wow": fx.TAPE_WOW,
        "wander": fx._WANDER,
        "wander_norm": fx._WANDER_NORM,
        "hiss_level": fx.TAPE_HISS,
        "hiss_follow": fx.TAPE_HISS_FOLLOW,
        "peak": None if peak is None else [list(peak[0]), list(peak[1])],
        "top": [list(top[0]), list(top[1])],
        "low": [list(low[0]), list(low[1])],
    }


def _formant(kw: dict) -> dict:
    centres, q = fx.formant_bands()
    return {
        "centres": list(centres),
        "q": q,
        "taps": fx.formant_taps(kw["shift"]),
        "block": fx.FORMANT_BLOCK,
        "follow": fx.FORMANT_FOLLOW,
        "gain": fx.FORMANT_GAIN,
        "floor": fx.FORMANT_FLOOR,
    }


def _vocoder(kw: dict) -> dict:
    centres, q = fx.vocoder_bands(kw["bands"])
    return {
        "centres": list(centres),
        "q": q,
        "ratio": 2.0 ** (kw["shift"] / 12.0),
        "follow": fx.VOCODER_FX_FOLLOW,
        "tilts": list(fx.vocoder_tilts(centres)),
    }


# the FX2 units and the dynamics (fx2-worklet.js): what each
# needs past its resolved arguments — the engine's constants, its fixed
# filters' (b, a)
_FX2 = {
    "autofilter": lambda kw: {"smooth": fx.LFO_SMOOTH},
    "tremolo": lambda kw: {"smooth": fx.LFO_SMOOTH},
    "tape": _tape,
    "pingpong": lambda kw: {},
    "formant_shift": _formant,
    "freqshift": lambda kw: {"hilbert": fx.HILBERT_PAIR, "loop": fx.FREQSHIFT_LOOP},
    "exciter": lambda kw: {"bias": fx.EXCITER_BIAS},
    "vocoder_fx": _vocoder,
    # the dynamics: the crossovers (built in the browser at its own rate), the
    # followers' times, the thresholds; the transient shaper's detectors
    "ott": lambda kw: {
        "split": fx.OTT_SPLIT,
        "q": fx.OTT_Q,
        "times": fx.OTT_TIMES,
        "down": fx.OTT_DOWN,
        "up": fx.OTT_UP,
        "up_max": fx.OTT_UP_MAX,
    },
    "limiter": lambda kw: {},
    "transient": lambda kw: {
        "peak": fx.TRANSIENT_PEAK,
        "slow": fx.TRANSIENT_SLOW,
        "short": fx.TRANSIENT_SHORT,
        "long": fx.TRANSIENT_LONG,
        "smooth": fx.TRANSIENT_SMOOTH,
        "floor": fx.TRANSIENT_FLOOR,
        "scale": fx.TRANSIENT_SCALE,
        "top": fx.TRANSIENT_MAX / fx.TRANSIENT_SCALE,
    },
}


def live_chain(units, arr: Arrangement, drums: bool) -> list[dict]:
    """A stored chain as the live keys build it: each unit's resolved
    arguments (`fxrack.resolve_unit`, the same numbers the render uses),
    plus what the browser needs to reach it — the reverb's IR URL, the
    clock of a pump (seconds per beat trigger) or of a sidechain (the kick
    hits inside the drum loop, none when the view renders no drums: the
    loop's sidechain ducks on nothing then too, `render.chain_arr`)."""
    out: list[dict] = []
    for u in clean_chain(units):
        name, kw = resolve_unit(u, arr)
        # the factory's own defaults for what the rack doesn't expose
        # (a chorus's base delay, a phaser's stages): never retyped in JS
        unit = {"kind": u["kind"], **_defaults(name), **kw}
        if u["kind"] == "drive":  # the shapes' constants (effects.drive_shape)
            unit |= {
                "fuzz_bias": fx.FUZZ_BIAS,
                "fuzz_gain": fx.FUZZ_GAIN,
                "tube_even": fx.TUBE_EVEN,
            }
        elif u["kind"] == "reverb":
            unit["ir"] = f"/fx/ir.wav?decay={kw['decay']:.6g}&size={kw['size']:.6g}"
        elif u["kind"] == "plate":
            unit["ir"] = (
                f"/fx/ir.wav?kind=plate&decay={kw['decay']:.6g}"
                f"&tone={kw['tone']:.6g}&predelay={kw['predelay']:.6g}"
            )
        elif u["kind"] == "spring":
            unit["ir"] = (
                f"/fx/ir.wav?kind=spring&decay={kw['decay']:.6g}"
                f"&tone={kw['tone']:.6g}&drip={kw['drip']:.6g}"
            )
        elif u["kind"] in ("doubler", "tape_echo"):  # their wander's sines
            unit |= {"wander": fx._WANDER, "wander_norm": fx._WANDER_NORM}
            unit |= (
                {"adt_wobble": fx.ADT_WOBBLE, "adt_level": fx.ADT_LEVEL}
                if u["kind"] == "doubler"
                else {"tape_wow": fx.TAPE_WOW, "tape_tone": fx.TAPE_TONE}
            )
        elif u["kind"] == "rotary":  # the cabinet's constants
            unit |= {
                "crossover": fx.ROTARY_CROSSOVER,
                "horn": fx.ROTARY_HORN,
                "drum": fx.ROTARY_DRUM,
                "mic": fx.ROTARY_MIC,
                "min_delay": fx.ROTARY_MIN_DELAY,
            }
        elif u["kind"] == "telephone":  # the band's Q, as the engine reckons it
            unit["q"] = fx.bandwidth_q(kw["width"])
        elif u["kind"] == "pitch":  # the ratio as the engine's, and its grain
            unit |= {"ratio": 2.0 ** (kw["semis"] / 12.0), **_SHIFTER}
        elif u["kind"] in ("autotune", "harmony"):  # the shifter, the tracker
            unit |= {**_SHIFTER, **_TRACKER}
            if u["kind"] == "harmony":
                unit["steps"] = list(fx.HARMONY_STEPS)
        elif u["kind"] == "vox_comp":
            unit["knee"] = fx.VOX_KNEE
        elif u["kind"] == "deess":
            unit |= {
                "ratio": fx.DEESS_RATIO,
                "attack": fx.DEESS_ATTACK,
                "release": fx.DEESS_RELEASE,
            }
        elif u["kind"] == "vgate":
            unit["attack"] = fx.VGATE_ATTACK
        elif u["kind"] in _FX2:  # the FX2 units' constants (fx2-worklet.js)
            unit |= _FX2[u["kind"]](kw)
        elif u["kind"] == "pump":
            unit["period"] = max(0.25, float(kw.pop("beats"))) * 60.0 / arr.bpm
        elif u["kind"] == "sidechain":
            step = (4.0 / arr.drum_steps) * 60.0 / arr.bpm
            unit["loop"] = arr.drum_bars * arr.drum_steps * step
            # the kicks the render ducks on (`fxrack.kick_steps`): one kept,
            # hidden, past a shrunk drum grid plays in no loop, and the
            # worklet repeats every hit each loop, so it ducked where no
            # kick sounds; a muted or soloed-out kick row leaves none
            unit["hits"] = sorted(s * step for s in kick_steps(arr)) if drums else []
        out.append(unit)
    return out
