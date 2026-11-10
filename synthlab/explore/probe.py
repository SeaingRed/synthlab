"""The breeder's ears: one note, in the register the card is heard at.

Everything the breeder decides — is a child new, which eight are most
different, which way a steer pushes, the tile's numbers and its name — is
decided on this probe. It used to be a bare A1 note gated at 0.6 s, which
nobody hears: the card plays the reference phrase at octave 3
(`studio/audition.LISTEN_OCTAVE`), and a 0.6 s buffer with no room for the
release squashed every pad (69-75 % of pad children measured near-silent).
Now: A3, a 0.4 s gate (a phrase note), the patch's own release after it
(`sequencer.note_span`, capped so one 3 s pad can't blow the generation's
time budget), and synced LFOs resolved at the session tempo with `at_bpm`
exactly as the loop does — without it every `lfo_sync` gene was inaudible
to the breeder.

The probe stays at A3 for *every* sound, although a card's own ▶ now plays
a bass at A1 and a pad at A2 (`studio/audition.register_of`): distances,
the novelty gate and the steering scores compare candidates with each
other and with the library scale (`scale.py`, measured at A3), so they
need one pitch for all of them, and the register itself is read off this
probe. Measurement pitch and audition pitch differ on purpose.

The distance is taken on a *standardised* vector: each measure divided by
its spread over the sound library (`scale.BREED_SCALE`, a fixed table —
regenerate with `uv run python tools/refresh_breed_scale.py`). Raw, the
crest factor (dB) was ~75 % of every distance and nothing else counted.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from ..analysis.features import Features, extract
from ..analysis.texture import Texture, texture
from ..core import SR
from ..sequencer import note_span
from ..synth import Patch, Synth, at_bpm
from .scale import BREED_SCALE, BREED_VECTOR

log = logging.getLogger(__name__)

PROBE_PITCH = "A3"  # audition.listening_root("A1"): the phrase's register
PROBE_GATE = 0.4  # s the key is held: one phrase note at ~124 bpm
PROBE_MAX_TAIL = 1.0  # s of release heard at most (budget: ~20 renders a litter)
PROBE_BPM = 124.0  # Arrangement's default tempo, when no session is given

# a candidate the user could not hear on the card is never picked while
# enough audible ones exist
QUIET_DB = -40.0  # loudest 50 ms under this (dBFS) is silence on a card
INFRA_MAX = 0.5  # most of the energy under 30 Hz is rumble, not a note

_Z_CLIP = 3.0  # one freak measure must not outvote the other eleven


_BLOCK = 2048  # probe lengths are whole blocks: extract()'s one FFT runs at
# the signal's own length, and an arbitrary length can be a large prime


def probe_audio(patch: Patch, bpm: float = PROBE_BPM) -> np.ndarray:
    p = at_bpm(patch, bpm)
    dur = min(note_span(PROBE_GATE, p), PROBE_GATE + PROBE_MAX_TAIL)
    n = math.ceil(dur * SR / _BLOCK) * _BLOCK
    return Synth(p).note(PROBE_PITCH, n / SR, gate=PROBE_GATE)


def log_flatness(tex: Texture) -> float:
    """Per-frame flatness in decades (-8 pure tone .. 0 white noise). The
    linear value sits under 0.001 for 90 % of the library, so adding a
    little noise or grit only shows on a log scale."""
    return float(np.log10(max(tex.flatness, 1e-8)))


def raw_vector(features: Features, tex: Texture) -> np.ndarray:
    """The breed measures in `BREED_VECTOR` order, unscaled. Hz measures go
    log2 (an octave is an octave anywhere); flatness is the per-frame one
    (`log_flatness`), because whole-note flatness is ~0 for anything
    pitched."""
    f = features
    values = {
        "centroid": np.log2(max(f.centroid, 20.0)),
        "rolloff": np.log2(max(f.rolloff, 20.0)),
        "log_flatness": log_flatness(tex),
        "crest": f.crest,
        "sub_20_60": f.sub_20_60,
        "sub_60_120": f.sub_60_120,
        "low_120_250": f.low_120_250,
        "attack_time": f.attack_time,
        "stereo_width": f.stereo_width,
        "density": tex.density,
        "env_length": tex.env_length,
        "movement": tex.movement,
    }
    return np.array([float(values[k]) for k in BREED_VECTOR])


_MEAN = np.array([BREED_SCALE[k][0] for k in BREED_VECTOR])
_STD = np.array([BREED_SCALE[k][1] for k in BREED_VECTOR])


def standardise(raw: np.ndarray) -> np.ndarray:
    return np.clip((raw - _MEAN) / _STD, -_Z_CLIP, _Z_CLIP)


@dataclass(eq=False)  # compared by identity: it carries arrays
class Probe:
    audio: np.ndarray
    features: Features
    texture: Texture
    vector: np.ndarray  # standardised: distances are in library spreads

    @property
    def audible(self) -> bool:
        return self.texture.loudness_db >= QUIET_DB and self.texture.infra < INFRA_MAX


def measure(audio: np.ndarray) -> Probe:
    f = extract(audio)
    t = texture(audio)
    return Probe(
        audio=audio, features=f, texture=t, vector=standardise(raw_vector(f, t))
    )


def probe(patch: Patch, bpm: float = PROBE_BPM) -> Probe | None:
    """Render and measure; None for a patch that fails or goes non-finite
    (a broken candidate is dropped, never fatal)."""
    try:
        audio = probe_audio(patch, bpm)
    except Exception:
        log.exception("breed: candidate patch %r failed to render", patch.name)
        return None
    if not np.all(np.isfinite(audio)):
        return None
    return measure(audio)


# Probes are independent and the engine is mostly NumPy calls that release
# the GIL: four threads measure a 20-candidate litter ~2x faster on both dev
# machines (8 threads is no better). The results are bit-identical to a
# serial run — every render is deterministic and all randomness was drawn
# before rendering starts.
_WORKERS = max(1, min(4, os.cpu_count() or 1))
_POOL: ThreadPoolExecutor | None = None
_POOL_LOCK = threading.Lock()


def pmap(fn, items: list) -> list:
    """`fn` over `items` on the probe threads, in order (serially for one
    item). The layered breeder (`studio/genome.py`) measures its stacks
    through here too."""
    global _POOL
    if len(items) <= 1 or _WORKERS == 1:
        return [fn(x) for x in items]
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(_WORKERS, thread_name_prefix="breed-probe")
    return list(_POOL.map(fn, items))


def probe_many(patches: list[Patch], bpm: float = PROBE_BPM) -> list[Probe | None]:
    """`probe` over a litter, in order."""
    return pmap(lambda p: probe(p, bpm), patches)


def distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))
