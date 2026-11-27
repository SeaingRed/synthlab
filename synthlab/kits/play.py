"""Render one acoustic hit: velocity layers, round robins and a mic mix.

`render_hit` reads a `KitMap` (`sfz.read_kit`) and the files under the kit's
root. The velocity picks a layer, or two: each layer sits at the centre of
its velocity range and between two centres they are mixed equal power by
position (a layer alone beyond the outermost). The amplitude is `velocity **
0.7`, since the layers carry most of the change, times the articulation's
trim. The round robin is a count (`rr % takes`), never a random draw.

A take holds one file per mic. The mixer has twelve channels (`MICS`); the
hat's and the ride's second direct mic (`hats_di`, `ride_di`) plays under its
fader (`CHANNEL_OF`). Each file also carries the kit's own balance
(`Articulation.mic_trim_db`). A mic the articulation lacks adds nothing; a file
that is missing is a `KitError`, never silence.
"""

from __future__ import annotations

import math
import os
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

from synthlab.core import SR
from synthlab.kits.fetch import KitError
from synthlab.kits.sfz import KitMap

MICS = (
    "kick_in",
    "kick_sub",
    "snare_top",
    "snare_bottom",
    "toms",
    "hats",
    "ride",
    "cymbals",
    "overheads",
    "close_room",
    "far_room",
    "mid_side",
)

# a map mic that plays under another mic's fader
CHANNEL_OF = {"hats_di": "hats", "ride_di": "ride"}

_CLOSE = {m: 0.0 for m in MICS[:8]}
MIC_PRESETS: dict[str, dict[str, float | None]] = {
    "studio": {
        **_CLOSE,
        "overheads": 0.0,
        "close_room": -6.0,
        "far_room": -15.0,
        "mid_side": None,
    },
    "close": {
        **_CLOSE,
        "overheads": -6.0,
        "close_room": None,
        "far_room": None,
        "mid_side": None,
    },
    "room": {
        **{m: -3.0 for m in MICS[:8]},
        "overheads": 0.0,
        "close_room": 0.0,
        "far_room": -3.0,
        "mid_side": -6.0,
    },
}


def levels(mics: dict | None) -> dict[str, float | None]:
    """A mic mix as `Arrangement.drum_mics` holds it, resolved: its
    `"preset"` (default and unknown: `studio`), then its own dB values over
    that preset (None = off), then `"muted"` (mics muted over their level)."""
    mics = mics or {}
    preset = mics.get("preset")
    if not isinstance(preset, str) or preset not in MIC_PRESETS:
        preset = "studio"
    out = dict(MIC_PRESETS[preset])
    for k, v in mics.items():
        if k not in ("preset", "muted"):
            out[k] = None if v is None else float(v)
    for k in mics.get("muted") or ():  # muted over their level
        if k in out:
            out[k] = None
    return out


def _cache_bytes() -> int:
    try:
        return int(float(os.environ.get("SYNTHLAB_KIT_CACHE_MB", "512")) * 1_048_576)
    except ValueError:
        return 512 * 1_048_576


class SampleCache:
    """What a hit sums: each file as stereo float32 at the rate asked, read-only,
    byte-bounded and least-recently-used, keyed by (path, rate). Each is made
    once however many threads ask for it at the same time."""

    def __init__(self, max_bytes: int | None = None):
        self.max_bytes = _cache_bytes() if max_bytes is None else max_bytes
        self.bytes = 0
        self._items: OrderedDict[tuple[str, int], np.ndarray] = OrderedDict()
        self._lock = threading.Lock()
        self._path_locks: dict[tuple[str, int], threading.Lock] = {}

    def get(self, path: Path, sr: int = SR) -> np.ndarray:
        """The file as read-only float32 `(n, 2)` at `sr`. A missing or
        unreadable file is a KitError."""
        key = (str(path), sr)
        with self._lock:
            hit = self._items.get(key)
            if hit is not None:
                self._items.move_to_end(key)
            plock = self._path_locks.setdefault(key, threading.Lock())
        if hit is None:
            with plock:
                with self._lock:
                    hit = self._items.get(key)
                if hit is None:
                    audio, file_sr = self._decode(path)
                    hit = _stereo(audio, file_sr, sr)
                    hit.flags.writeable = False
                    self._put(key, hit)
        return hit

    @staticmethod
    def _decode(path: Path):
        import soundfile as sf

        try:
            audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
        except (OSError, RuntimeError) as err:
            raise KitError(f"kit sample missing or unreadable: {path.name}") from err
        return audio, int(sr)

    def _put(self, key, item: np.ndarray) -> None:
        with self._lock:
            if key in self._items or item.nbytes > self.max_bytes:
                return
            self._items[key] = item
            self.bytes += item.nbytes
            while self.bytes > self.max_bytes:
                _, old = self._items.popitem(last=False)
                self.bytes -= old.nbytes


CACHE = SampleCache()


def _stereo(audio: np.ndarray, sr: int, want: int) -> np.ndarray:
    """Float32 `(n, 2)` at `want`: resampled linearly first (a mono file once,
    not twice), then a mono file to both sides."""
    if audio.shape[1] > 2:
        audio = audio[:, :2]
    if sr != want and len(audio):
        n = max(1, round(len(audio) * want / sr))
        idx = np.linspace(0, len(audio) - 1, n)
        base = np.arange(len(audio))
        audio = np.stack(
            [np.interp(idx, base, audio[:, c]) for c in range(audio.shape[1])], axis=1
        ).astype(np.float32)
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    return np.ascontiguousarray(audio)


def _inside(root: Path, rel: str) -> Path:
    base = Path(root).resolve()
    path = (base / rel).resolve()
    if not path.is_relative_to(base):
        raise KitError(f"kit sample outside the kit: {rel}")
    return path


def _weights(layers, m: float):
    """[(layer, weight)] for MIDI velocity `m`: the nearest centres, equal power."""
    order, centres = [], []
    for ly in sorted(layers, key=lambda ly: (ly.lo + ly.hi) / 2):
        c = (ly.lo + ly.hi) / 2
        if centres and c == centres[-1]:
            continue  # two layers at one centre: the first stands for both
        order.append(ly)
        centres.append(c)
    if m <= centres[0]:
        return [(order[0], 1.0)]
    if m >= centres[-1]:
        return [(order[-1], 1.0)]
    for i in range(len(order) - 1):
        if centres[i] <= m <= centres[i + 1]:
            t = (m - centres[i]) / (centres[i + 1] - centres[i])
            a = math.pi / 2 * t
            return [(order[i], math.cos(a)), (order[i + 1], math.sin(a))]
    return [(order[-1], 1.0)]


def render_hit(
    kit: KitMap,
    root: Path,
    row: str,
    art: str,
    velocity: float,
    rr: int,
    mics: dict | None,
    sr: int = SR,
) -> np.ndarray:
    """One hit of `row`/`art` as `(n, 2)` float64."""
    a = kit.arts.get((row, art))
    if a is None or not a.layers:
        raise KitError(f"the kit has no {row}/{art}")
    velocity = min(1.0, max(0.0, float(velocity)))
    chan = levels(mics)
    amp = velocity**0.7 * 10 ** (a.trim_db / 20)
    parts: list[tuple[np.ndarray, float]] = []
    for layer, w in _weights(a.layers, velocity * 127):
        if not layer.takes:
            continue
        take = layer.takes[rr % len(layer.takes)]
        for mic, rel in take.items():
            db = chan.get(CHANNEL_OF.get(mic, mic))
            if db is None:
                continue
            gain = amp * w * 10 ** ((db + a.mic_trim_db.get(mic, 0.0)) / 20)
            parts.append((CACHE.get(_inside(root, rel), sr), gain))
    if not parts:
        return np.zeros((0, 2))
    out = np.zeros((max(len(p) for p, _ in parts), 2))
    for p, gain in parts:
        out[: len(p)] += p * np.float32(gain)
    return out
