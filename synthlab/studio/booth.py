"""The vocal booth: the Voice page's fourth step, "Treat" — the take (or the
vocoded result) through the studio tricks records were made with.

A treatment (`TREATMENTS`) is data: a named FX chain built from ordinary
`fxrack` units (`effects.py`'s ADT double, rotary, tape echo, telephone,
pitch shift and plate among them) and a few **offline moves** that are not
units, because they read the whole take: played backwards, a backwards
reverb swell (the take reversed, into a reverb, the wet reversed again and
laid in front of it, so it rises into each phrase) and varispeed (the tape
run faster or slower: `cliphub.tune`'s rate, the hub's own resample, as is
its reverse). Picking one loads its chain and moves; both stay editable —
the chain unit by unit (`fxrack.edit_chain`, the Song audio track's
rack's edit), the moves by their controls — and a render auditions it.

What the booth holds is `voice_lab.json`'s `booth` (and `booth_last`, the
last audition's key) beside the take, like the vocoder's settings: never
the session, never a render key. The last four auditions are held with
their clip meta (`RESULTS`), so Keep / → Song save the samples that were
heard (`docs/sampler-and-clips.md`, "The vocal booth").
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from .. import effects as fx
from ..core import SR
from . import cliphub, limits
from .clips import clip_dir, list_clips
from .fxrack import FX_UNITS, MAX_UNITS, build_chain, clean_chain, rack_rows
from .lru import LRU
from .state.model import song_clip_names

SOURCES = ("take", "result")
SOURCE_LABELS = {"take": "the take", "result": "the vocoded result"}
# whose tempo a synced unit reads (`follows`): "auto" is the song's for a
# take the song plays (or while a song track's sound is edited), else the loop's
TEMPOS = ("auto", "loop", "song")
DIRECTIONS = ("forwards", "backwards")
RATE_RANGE = cliphub.TUNE_RANGES["rate"]  # the hub's tape rate: 0.5..2
MOVES: dict = {"reverse": False, "swell": 0.0, "rate": 1.0}
SWELL_DECAY = 3.0  # s: the room the reversed voice is played into
SWELL_SIZE = 0.9
SWELL_LEAD = 1.5  # s: how long before the voice's first sample the swell starts
TAIL = 4.0  # s of silence the chain may ring into past the take
# A result's cap: a whole clip's worth of take (the moves' `cliphub.tune`
# cuts there), the swell's lead before it and the ring after it, so neither
# costs a 30 s take its last words (limits.MAX_TREATED_SECONDS)
MAX_SECONDS = limits.MAX_TREATED_SECONDS
assert MAX_SECONDS >= limits.MAX_CLIP_SECONDS + SWELL_LEAD + TAIL
TAIL_FLOOR = 1e-4  # -80 dBFS: quieter than this at the end is cut
TAIL_FADE = 0.01  # s: the fade on a cut tail
PEAK = 0.89  # the audition's peak, as the vocoder's

_ECHO = {"wow": 0.3, "tone": 0.5, "drive": 0.3, "sync": 0.0}

# A treatment: label, a one-line title naming the technique (never a band),
# the chain (stored clean: `clean_chain` of it is itself) and the moves.
TREATMENTS: dict[str, dict] = {
    "leslie": {
        "label": "Leslie vocal",
        "title": "Late-'60s psychedelia: the voice sung through a rotating organ "
        "speaker — slow chorale swirl, a little amp grit, a short plate",
        "chain": [
            {"kind": "rotary", "params": {"speed": 0.0, "drive": 1.2, "mix": 0.85}},
            {
                "kind": "plate",
                "params": {"decay": 1.8, "tone": 0.6, "predelay": 0.012, "mix": 0.18},
            },
        ],
        "moves": {},
    },
    "adt": {
        "label": "ADT double",
        "title": "Artificial double tracking ('66): a second take faked from the "
        "first — a few ms late, wavering, panned apart",
        "chain": [
            {
                "kind": "doubler",
                "params": {"time": 0.028, "wobble": 0.6, "spread": 0.75, "mix": 1.0},
            }
        ],
        "moves": {},
    },
    "slapback": {
        "label": "Tape slapback",
        "title": "'50s rock'n'roll: one quick, dark echo off the tape machine, "
        "about 110 ms behind the voice",
        "chain": [
            {
                "kind": "tape_echo",
                "params": {
                    **_ECHO,
                    "time": 0.11,
                    "feedback": 0.12,
                    "wow": 0.15,
                    "tone": 0.4,
                    "drive": 0.45,
                    "mix": 0.5,
                },
            }
        ],
        "moves": {},
    },
    "psych_wash": {
        "label": "Phaser-and-echo wash",
        "title": "Modern psych: the voice squeezed, slowly phased, into a long "
        "wavering tape echo on the dotted eighth and a bright plate",
        "chain": [
            {
                "kind": "compress",
                "params": {
                    "threshold": -22.0,
                    "ratio": 4.0,
                    "attack": 0.005,
                    "release": 0.12,
                    "makeup": -1.0,
                },
            },
            {"kind": "phaser", "params": {"rate": 0.15, "depth": 0.8, "mix": 0.5}},
            {
                "kind": "tape_echo",
                "params": {
                    **_ECHO,
                    "time": 0.3,
                    "feedback": 0.55,
                    "wow": 0.4,
                    "tone": 0.35,
                    "mix": 0.4,
                    "sync": 3.0,
                },
            },
            {
                "kind": "plate",
                "params": {"decay": 3.2, "tone": 0.7, "predelay": 0.02, "mix": 0.3},
            },
        ],
        "moves": {},
    },
    "octave": {
        "label": "Octave double",
        "title": "The voice with itself an octave below, underneath: a "
        "pitch-shifted unison that thickens a line",
        "chain": [{"kind": "pitch", "params": {"semis": -12.0, "mix": 0.45}}],
        "moves": {},
    },
    "fifth": {
        "label": "Fifth harmony",
        "title": "A harmony a fifth above, shifted from the voice itself and "
        "doubled wide",
        "chain": [
            {"kind": "pitch", "params": {"semis": 7.0, "mix": 0.35}},
            {
                "kind": "doubler",
                "params": {"time": 0.022, "wobble": 0.4, "spread": 0.5, "mix": 0.6},
            },
        ],
        "moves": {},
    },
    "telephone": {
        "label": "Telephone",
        "title": "The voice down a phone line: the lows and the air gone, the "
        "band overdriven — a verse sung from far away",
        "chain": [
            {
                "kind": "telephone",
                "params": {"centre": 1300.0, "width": 2.3, "drive": 3.0, "mix": 1.0},
            }
        ],
        "moves": {},
    },
    "swell": {
        "label": "Backwards swell",
        "title": "Reverse reverb (a '60s tape trick): the voice's own reverb "
        "played backwards, rising into each phrase",
        "chain": [
            {
                "kind": "plate",
                "params": {"decay": 1.4, "tone": 0.6, "predelay": 0.01, "mix": 0.12},
            }
        ],
        "moves": {"swell": 0.8},
    },
    "backwards": {
        "label": "Backwards vocal",
        "title": "The take played backwards, as if the tape were turned over, "
        "into a tape echo so the repeats trail after",
        "chain": [
            {
                "kind": "tape_echo",
                "params": {**_ECHO, "time": 0.3, "feedback": 0.35, "mix": 0.3},
            }
        ],
        "moves": {"reverse": True},
    },
    "varispeed": {
        "label": "Varispeed tape",
        "title": "The tape run fast under the voice: higher and quicker, a "
        "little thinner (below 1, slower and deeper)",
        "chain": [
            {
                "kind": "plate",
                "params": {"decay": 1.2, "tone": 0.6, "predelay": 0.012, "mix": 0.12},
            }
        ],
        "moves": {"rate": 1.12},
    },
    "dreamy": {
        "label": "Dreamy plate",
        "title": "A soft, bright plate with a long tail and a touch of chorus — "
        "the voice floating",
        "chain": [
            {"kind": "chorus", "params": {"rate": 0.3, "depth": 3.0, "mix": 0.3}},
            {
                "kind": "plate",
                "params": {"decay": 3.8, "tone": 0.75, "predelay": 0.03, "mix": 0.4},
            },
        ],
        "moves": {},
    },
    "dry": {
        "label": "Dry",
        "title": "No treatment: the take as it is — build your own from + add…",
        "chain": [],
        "moves": {},
    },
}


# --- what the booth holds ----------------------------------------------------------


def _number(v, lo: float, hi: float, default: float) -> float:
    if isinstance(v, bool):
        return default
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return default
    return min(hi, max(lo, f)) if np.isfinite(f) else default


def clean_moves(raw, base: dict | None = None) -> dict:
    """The offline moves, each held to its type and range; a field `raw`
    does not carry keeps `base`'s value. `reverse` is a bool, or the
    direction a form posts ("backwards")."""
    raw = raw if hasattr(raw, "get") else {}
    was = {**MOVES, **(base or {})}
    rev = raw.get("reverse", was["reverse"])
    if isinstance(rev, str):
        rev = rev in ("backwards", "on", "1", "true")
    return {
        "reverse": rev is True,
        "swell": round(_number(raw.get("swell", was["swell"]), 0.0, 1.0, 0.0), 3),
        "rate": round(_number(raw.get("rate", was["rate"]), *RATE_RANGE, 1.0), 3),
    }


def clean_booth(raw) -> dict:
    """`voice_lab.json`'s `booth`: the treatment last picked (a name, or ""),
    what it treats, the chain and the moves — a file anyone can edit."""
    raw = raw if isinstance(raw, dict) else {}
    name = raw.get("treatment")
    source = raw.get("source")
    tempo = raw.get("tempo")
    return {
        "treatment": name if isinstance(name, str) and name in TREATMENTS else "",
        "source": source if source in SOURCES else "take",
        "chain": clean_chain(raw.get("chain")),
        "moves": clean_moves(raw.get("moves")),
        "tempo": tempo if tempo in TEMPOS else "auto",
    }


def treatment(name) -> dict | None:
    """The booth a treatment sets: its name, a clean copy of its chain and
    its moves over the defaults (what it treats is the caller's)."""
    t = TREATMENTS.get(name) if isinstance(name, str) else None
    if t is None:
        return None
    return {
        "treatment": name,
        "chain": clean_chain(copy.deepcopy(t["chain"])),
        "moves": clean_moves(t["moves"]),
    }


def edited(booth: dict) -> bool:
    """Whether the chain or the moves have moved off the treatment's."""
    t = treatment(booth["treatment"])
    return t is not None and (
        t["chain"] != booth["chain"] or t["moves"] != booth["moves"]
    )


# --- the audio --------------------------------------------------------------------


def stereo(audio: np.ndarray) -> np.ndarray:
    x = np.asarray(audio, dtype=np.float64)
    if x.ndim == 1:
        return np.column_stack([x, x])
    return x[:, :2] if x.shape[1] >= 2 else np.column_stack([x[:, 0], x[:, 0]])


def swell(st: np.ndarray, amount: float, sr: int = SR) -> np.ndarray:
    """A backwards reverb swell: the take reversed, played into a room
    (wet only) with room to ring, that wet reversed again — so each
    phrase's reverb rises *into* it — laid under the take at `amount`.
    The swell begins SWELL_LEAD s before the voice, so the result is that
    much longer and the voice starts that much later."""
    lead = int(round(SWELL_LEAD * sr))
    backwards = np.vstack([st[::-1], np.zeros((lead, 2))])
    room = fx.reverb(decay=SWELL_DECAY, mix=1.0, size=SWELL_SIZE)(backwards)
    return np.vstack([np.zeros((lead, 2)), st]) + float(amount) * room[::-1]


def apply_moves(st: np.ndarray, moves: dict, sr: int = SR) -> np.ndarray:
    """The offline moves in their one order: the tape's speed and its
    direction (`cliphub.tune`, the hub's reverse and rate), then the swell."""
    tune = cliphub.clean_tune(
        {"rate": moves["rate"], "reverse": "on" if moves["reverse"] else ""}
    )
    if not cliphub.is_identity(tune):
        st = cliphub.tune(st, sr, tune)
    if moves["swell"] > 0 and len(st):
        st = swell(st, moves["swell"], sr)
    return st


def chain_arr(arr):
    """The arrangement a booth chain reads its tempo and grid from: the
    session's, without its drums — a take has no kick to duck under."""
    return replace(arr, drums=[])


def follows(booth: dict, origin: dict, song: dict, song_edit: bool = False) -> str:
    """Whose tempo the chain's synced units read (a tape echo on the beat,
    a gate, a pump): "loop" (the session's) or "song". The booth's own
    choice, or on "auto" the song's when the take is a clip the song
    plays (a song take, brought in with "Use as take") or while a song
    track's sound is being edited (the keys' rule, `fxlive.keys_bpm`) —
    what it makes is headed for the Song — else the loop's."""
    if booth["tempo"] != "auto":
        return booth["tempo"]
    takes, _shots = song_clip_names(song)
    if song_edit or (origin.get("kind") == "clip" and origin.get("name") in takes):
        return "song"
    return "loop"


def tempo_arr(arr, song: dict, which: str):
    """The session's arrangement at the tempo `which` names."""
    return replace(arr, bpm=float(song["bpm"])) if which == "song" else arr


def treat(audio: np.ndarray, units, moves: dict, arr, step=None) -> np.ndarray:
    """`audio` through the moves, then the chain over it and TAIL s more
    (what an echo or a plate rings into), cut where the ring falls under
    TAIL_FLOOR (never shorter than the moved take), at most MAX_SECONDS —
    room for a whole 30 s take, the swell's lead and the ring, so none of
    them costs the take its tail — peak PEAK. `step()` is called after the
    moves and the chain."""
    step = step or (lambda: None)
    st = apply_moves(stereo(audio), moves)
    n = len(st)
    step()
    padded = np.vstack([st, np.zeros((int(TAIL * SR), 2))])
    arr = chain_arr(arr)
    out = fx.chain(padded, build_chain(units, arr, span=len(padded) / SR))
    out = fx.to_stereo(np.asarray(out, dtype=np.float64))
    step()
    loud = np.flatnonzero(np.max(np.abs(out[n:]), axis=1) > TAIL_FLOOR)
    end = n + (int(loud[-1]) + 1 if len(loud) else 0)
    out = out[: min(end, int(MAX_SECONDS * SR))].copy()
    if end > n:  # a ring cut where it falls quiet: faded, never a click
        f = min(len(out), int(TAIL_FADE * SR))
        out[len(out) - f :] *= np.linspace(1.0, 0.0, f)[:, None]
    peak = float(np.max(np.abs(out), initial=0.0))
    return out / peak * PEAK if peak > 1e-9 else out


def audition_key(audio: np.ndarray, booth: dict, arr) -> str:
    """Everything an audition depends on: the source's samples, the chain,
    the moves, and the tempo and grid a synced unit reads."""
    blob = json.dumps(
        {
            "chain": booth["chain"],
            "moves": booth["moves"],
            "arr": [arr.bpm, arr.bars, arr.drum_bars, arr.drum_steps],
        },
        sort_keys=True,
    ).encode()
    digest = hashlib.sha1(np.ascontiguousarray(audio).tobytes() + b"\0" + blob)
    return "booth:" + digest.hexdigest()[:24]


def result_meta(origin: dict, booth: dict, n: int) -> dict:
    """A kept audition's clip meta: what it treated and how."""
    t = TREATMENTS.get(booth["treatment"])
    return {
        "source": "booth",
        "booth": {
            "take": dict(origin),
            "from": booth["source"],
            "treatment": t["label"] if t else "",
            "edited": edited(booth),
            "chain": copy.deepcopy(booth["chain"]),
            "moves": dict(booth["moves"]),
            "seconds": round(n / SR, 3),
        },
    }


# key -> (the audition's WAV bytes, its clip meta): the last four, as the
# vocoder's RESULTS
RESULTS: LRU[tuple[bytes, dict]] = LRU(4)


# --- the panel ----------------------------------------------------------------------


def talk_takes(data_dir: Path, limit: int = 3) -> list[dict]:
    """The newest takes the live talkbox recorded (clip meta source
    "talkbox"), newest first: one press makes one the booth's take."""
    root = clip_dir(data_dir)
    found = []
    for c in list_clips(data_dir):
        if c.get("source") != "talkbox":
            continue
        try:
            at = (root / f"{c['name']}.wav").stat().st_mtime
        except OSError:
            continue
        found.append((at, {"name": c["name"], "seconds": c.get("seconds", 0.0)}))
    return [c for _at, c in sorted(found, key=lambda f: -f[0])[:limit]]


def label(booth: dict) -> str:
    """The booth's treatment in words ("Leslie vocal (edited)", "Your
    chain"): the panel's legend, and what Now playing calls its take."""
    t = TREATMENTS.get(booth["treatment"])
    name = t["label"] if t else "Your chain"
    return f"{name} (edited)" if t and edited(booth) else name


def tempo_context(booth: dict, origin: dict, song: dict, arr, song_edit: bool) -> dict:
    """The booth's tempo select: its choice, whose tempo that is now, and
    each option labelled with the bpm it means."""
    loop, at_song = f"{arr.bpm:g} bpm", f"{float(song['bpm']):g} bpm"
    which = follows({**booth, "tempo": "auto"}, origin, song, song_edit)
    auto = f"the song's, {at_song}" if which == "song" else f"the loop's, {loop}"
    return {
        "tempo": booth["tempo"],
        "follows": follows(booth, origin, song, song_edit),
        "tempos": [
            ("auto", f"auto ({auto})"),
            ("loop", f"the loop's ({loop})"),
            ("song", f"the song's ({at_song})"),
        ],
    }


def panel_context(data_dir: Path, lab: dict, result_there: bool) -> dict:
    """What the booth draws (`voice_panel.html`, `bo`): the treatments, the
    one picked and whether it was edited, the rack's rows and its adder,
    the moves, what it treats, the last audition while held, and the
    talkbox's newest takes."""
    b = lab["booth"]
    held = RESULTS.get(lab["booth_last"]) if lab["booth_last"] else None
    return {
        "treatments": [(k, t["label"], t["title"]) for k, t in TREATMENTS.items()],
        "treatment": b["treatment"],
        "label": label(b),
        "rack": rack_rows(b["chain"]),
        "units": [(k, spec["label"]) for k, spec in FX_UNITS.items()],
        "rack_full": len(b["chain"]) >= MAX_UNITS,
        "moves": b["moves"],
        "rate_range": RATE_RANGE,
        "swell_lead": SWELL_LEAD,
        "source": b["source"] if result_there else "take",
        "sources": [(k, SOURCE_LABELS[k]) for k in SOURCES],
        "result_there": result_there,
        "audition": {
            "key": lab["booth_last"],
            "seconds": held[1]["booth"]["seconds"],
            "label": held[1]["booth"]["treatment"] or "Your chain",
        }
        if held
        else None,
        "talk": talk_takes(data_dir),
    }
