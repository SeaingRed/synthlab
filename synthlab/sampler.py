"""synthlab.sampler — the "sampler" oscillator shape.

Recorded audio played as an oscillator: `core.osc("sampler", ...)` reads a
sample by its id and plays it at the note's pitch, so the amp envelope,
filter, vowel tract, drive and FX of `Synth.note` all apply to it exactly as
they do to a saw.

**Samples are content-addressed.** A sample id is the first 16 hex digits
of the sha1 of its 16-bit PCM (plus its shape). The Patch holds the id, so
every render key that hashes the patch (`studio/render.py` `patch_key`,
`RenderInputs.key()`, stem, lane and note keys) hashes the audio's content
for free: new audio is a new id, never the same key. Samples are stored
stereo, float32, at `core.SR`, at most `MAX_SAMPLE_SECONDS`, as
`<dir>/<id>.wav` in any directory `add_dir` registered (the studio's
`studio_data/samples/`), and kept decoded in a small byte-bounded memory
cache. A missing sample renders silence.

Pitch (array-safe: glide, vibrato, the bend and pitch routes all move it):
  * "resample" (the default) — tape: read faster to go up. Formants move
    with the pitch, the length shrinks as it rises.
  * "formant" — a PSOLA-like grain train: grains two source periods long
    (the source period is the root note's, so set the root to the sample's
    own pitch), read at the source's own speed and snapped to whole source
    periods, re-spaced at the played pitch's period. The grain content —
    the spectral envelope, i.e. the timbre — stays put however far the
    pitch moves.
  * `stretch` != 1 (a length factor, array-safe) — a granular time-stretch
    in "resample" mode: effects.granular's Hann grain cloud with no
    scatter, read at the pitch ratio from a source position advancing at
    1/stretch, each grain slid (WSOLA) to where it continues the last one
    in phase — without that the grains comb and lose ~5 dB. "formant" mode
    always advances at 1/stretch.

Loops: `loop` "on" wraps [loop_start, loop_end) with a crossfade of
`xfade` seconds baked into the loop's tail (the tail blends into the audio
just before loop_start, which flows on into loop_start); its law follows
the two sides' correlation — linear for alike audio, equal power for
unrelated audio — so the seam holds its level either way.

Chops (`slices`, a compact string, see `parse_slices`): when set, the key
picks the slice — slice i on MIDI root + i, wrapping every len(slices)
keys — and the slice plays at its own pitch (+ its semitone offset; glide,
vibrato and bends still move it), optionally reversed, declicked at both
ends. Loop settings don't apply to a slice.

Zones (`zones`, a compact string, see `parse_zones`): a multi-sample
instrument. When set, it plays instead of `sample` and `slices`: each zone
is a recording on its own keys and velocities, at its own root, one of a
key's round-robin takes, at its own gain and fine tune, with its own span
and loop or the patch's. `pick_zones` chooses by the note's key, velocity
and strike (`Settings.velocity`, `.rr`: which strike of its key the note
is in its phrase, counted by `Strikes` — deterministic, never random) and
crossfades two velocity layers at equal power; each chosen zone renders
as a sample of its own and they are summed (`_render_zones`).

Nothing here is causal: a sample is known in full when the note starts.
The live voice (the AudioWorklet) plays what it can of that: `live_payload`
hands it the prepared source(s) — the same `_whole_source`/`_slice_source`
buffers `render` reads, declicked, crossfaded, chopped — one channel for a
sample whose two are the same, both for a true-stereo one. In "resample"
mode without stretch the worklet only steps the read position by the live
pitch (static/sampler-worklet.js). Neither grain engine is "prepare a
buffer, then read it at the pitch" — `_grains` lays 46 ms grains on the
*output* clock, each read at the note's pitch ratio from a position
advancing at 1/stretch, and `_psola` spaces its grains by the *played*
period: both interleave the per-sample pitch with the grain schedule, so
no buffer baked per sample plays every key (a stretched buffer read at
ratio r would last 1/r as long; the engine's note keeps its length). So
"formant" mode is `_psola` itself run in the voice (static/
grain-worklet.js), which needs one thing a live voice does not have:
**`_psola` reads the pitch one root period into the future** — a grain
centred on sample ci starts sounding at ci - g // 2, and its gain and the
next grain's place come from `speed[ci]`. The voice foresees its own
motion that far (glide, vibrato, the pitch envelope, a held bend) and so
plays the engine's samples; a gesture mid-note (the wheel) reaches the
schedule up to a root period late, and what cannot be foreseen at all (a
route onto the pitch, cross-FM) keeps the WAV path — `live_ok` and
studio/paramui.py say which. `_grains` (a stretch in "resample" mode)
stays offline: its similarity search is 961 lags x 2208 products per grain
at 48 kHz, a grain every 11.5 ms per unison copy — 1.5 ms of JS per grain
done directly (most of a 2.67 ms render quantum for one voice), 0.18 ms by
FFT, which sixteen voices struck together still spend in one quantum.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import struct
import threading
import uuid
import wave
from collections import OrderedDict
from dataclasses import dataclass, replace
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlencode

import numpy as np

from .core import SR, as_array

MAX_SAMPLE_SECONDS = 30.0
MAX_SLICES = 32
SLICE_PITCH = 24  # semitones either way
_ID = re.compile(r"[0-9a-f]{16}\Z")
_SCALE = 32767.0  # one PCM scale for writing, reading and hashing
_CACHE_BYTES = 192 << 20

MODES = ("resample", "formant")
ON_OFF = ("off", "on")


# --- the store ---------------------------------------------------------------


class _Cache:
    """id -> decoded stereo float32, least recently used first out."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self._d: OrderedDict[str, np.ndarray] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> np.ndarray | None:
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def put(self, key: str, value: np.ndarray) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            total = sum(a.nbytes for a in self._d.values())
            while total > self.max_bytes and len(self._d) > 1:
                _, old = self._d.popitem(last=False)
                total -= old.nbytes


_MEM = _Cache(_CACHE_BYTES)
_DIRS: list[Path] = []
_DIRS_LOCK = threading.Lock()


def add_dir(path: Path | str) -> Path:
    """Search `path` for sample files (created if missing). Newest first;
    a dir added again moves to the front."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    with _DIRS_LOCK:
        if p in _DIRS:
            _DIRS.remove(p)
        _DIRS.insert(0, p)
        del _DIRS[64:]
    return p


def valid_id(sid) -> bool:
    return isinstance(sid, str) and bool(_ID.match(sid))


def _pcm(audio: np.ndarray) -> np.ndarray:
    return np.round(np.clip(audio, -1.0, 1.0) * _SCALE).astype("<i2")


def prepare(audio: np.ndarray, sr: int = SR) -> np.ndarray:
    """Any mono/stereo float audio at `sr` -> the store's form: stereo,
    `SR`, at most MAX_SAMPLE_SECONDS, on the 16-bit grid (so the audio a
    render reads is exactly what a reload of the file gives)."""
    x = np.asarray(audio, dtype=np.float64)
    if x.ndim == 1:
        x = np.column_stack([x, x])
    elif x.ndim == 2 and x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    elif x.ndim == 2 and x.shape[1] > 2:
        x = x[:, :2]
    x = np.nan_to_num(x)
    if sr != SR and len(x):
        from scipy.signal import resample_poly

        g = math.gcd(int(SR), int(sr))
        x = resample_poly(x, SR // g, int(sr) // g, axis=0)
    x = x[: int(MAX_SAMPLE_SECONDS * SR)]
    return (_pcm(x).astype(np.float32) / _SCALE).reshape(-1, 2)


def sample_id(prepared: np.ndarray) -> str:
    h = hashlib.sha1(f"{prepared.shape}".encode())
    h.update(_pcm(prepared).tobytes())
    return h.hexdigest()[:16]


def register(audio: np.ndarray, sr: int = SR) -> str:
    """Put `audio` in the memory store (not on disk); its id."""
    x = prepare(audio, sr)
    sid = sample_id(x)
    _MEM.put(sid, x)
    return sid


def save(directory: Path | str, audio: np.ndarray, sr: int = SR) -> str:
    """Store `audio` as `<directory>/<id>.wav` (once: same content, same
    file) and in memory; its id."""
    x = prepare(audio, sr)
    sid = sample_id(x)
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{sid}.wav"
    if not path.exists():
        # a temp file per writer: two saves of the same content shared
        # `<id>.tmp`, so one could publish the other's half-written file
        tmp = path.with_name(f"{sid}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_bytes(_wav(x))
            tmp.replace(path)
        except OSError:
            # losing the race to an identical file is not a failure
            if not path.exists():
                raise
        finally:
            tmp.unlink(missing_ok=True)
    _MEM.put(sid, x)
    return sid


def _read_file(path: Path) -> np.ndarray | None:
    """A store file's audio, or None — "a missing sample is silence" — for
    anything that is not one whole: another rate or width, over the length
    cap, or cut short (a file truncated mid-frame used to raise out of
    `get` into `Synth.note`)."""
    try:
        return from_wav_bytes(path.read_bytes())
    except OSError:
        return None


def get(sid) -> np.ndarray | None:
    """The sample's audio (stereo float32 at SR), or None."""
    if not valid_id(sid):
        return None
    x = _MEM.get(sid)
    if x is not None:
        return x
    with _DIRS_LOCK:
        dirs = list(_DIRS)
    for d in dirs:
        path = d / f"{sid}.wav"
        if path.is_file():
            x = _read_file(path)
            if x is not None:
                _MEM.put(sid, x)
                return x
    return None


def _wav(x: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(_pcm(x).tobytes())
    return buf.getvalue()


def wav_bytes(sid) -> bytes | None:
    """The sample as the store's own file (stereo 16-bit at SR) — what a
    project bundle carries — or None when this studio doesn't have it."""
    x = get(sid)
    return None if x is None else _wav(x)


def from_wav_bytes(data: bytes) -> np.ndarray | None:
    """A store-format file (stereo, 16-bit, SR, <= MAX_SAMPLE_SECONDS) as
    the store's audio, or None for anything else. Strict on purpose: it
    reads files from outside (a project bundle), and an id is only
    checkable against audio in exactly the form it was hashed from."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (2, 2, SR):
                return None
            n = w.getnframes()
            if n > int(MAX_SAMPLE_SECONDS * SR):
                return None
            raw = w.readframes(n)
    except (OSError, wave.Error, EOFError, ValueError):
        return None
    if len(raw) != n * 4:
        return None
    return (np.frombuffer(raw, dtype="<i2").astype(np.float32) / _SCALE).reshape(-1, 2)


def seconds(sid) -> float:
    x = get(sid)
    return 0.0 if x is None else len(x) / SR


def peaks(sid, columns: int = 240) -> np.ndarray | None:
    """Per-column peak of |x| over both channels, 0..1: a waveform
    overview for drawing."""
    x = get(sid)
    if x is None or not len(x):
        return None
    m = np.max(np.abs(x), axis=1)
    edges = np.linspace(0, len(m), columns + 1).astype(int)
    return np.array([float(m[a:b].max()) if b > a else 0.0 for a, b in pairwise(edges)])


# --- chops -------------------------------------------------------------------


@dataclass(frozen=True)
class Slice:
    a: float  # start, 0..1 of the sample
    b: float  # end
    pitch: int = 0  # semitones
    rev: bool = False


def parse_slices(text) -> list[Slice]:
    """ "a,b,pitch,rev;..." -> slices. Anything malformed is dropped, not
    raised: this reads untrusted state."""
    if not isinstance(text, str) or not text:
        return []
    out: list[Slice] = []
    for part in text.split(";")[:MAX_SLICES]:
        bits = part.split(",")
        if len(bits) != 4:
            continue
        try:
            a, b = float(bits[0]), float(bits[1])
            pitch, rev = int(bits[2]), int(bits[3])
        except ValueError:
            continue
        if not (math.isfinite(a) and math.isfinite(b)):
            continue
        a, b = min(1.0, max(0.0, a)), min(1.0, max(0.0, b))
        if b - a < 1e-4:
            continue
        pitch = max(-SLICE_PITCH, min(SLICE_PITCH, pitch))
        out.append(Slice(a, b, pitch, bool(rev)))
    return out


def format_slices(slices: list[Slice]) -> str:
    return ";".join(
        f"{s.a:.5f},{s.b:.5f},{int(s.pitch)},{int(s.rev)}" for s in slices[:MAX_SLICES]
    )


def clean_slices(text) -> str:
    return format_slices(parse_slices(text))


def clean_id(sid) -> str:
    return sid if valid_id(sid) else ""


# --- zones: a multi-sample instrument ------------------------------------------

MAX_ZONES = 64
MAX_TAKES = 16  # round-robin takes: rr 0..15
ZONE_GAIN_DB = (-36.0, 12.0)
ZONE_TUNE = 100.0  # cents either way (the root moves whole semitones)


@dataclass(frozen=True)
class Zone:
    """One recording of a multi-sample instrument: the keys and velocities
    it answers, the key that plays it as recorded, which round-robin take
    it is, its level and fine tune, and — when it needs its own — its span
    and loop (None: the patch's start / end / loop settings, read on this
    zone's sample)."""

    sample: str
    lo: int = 0  # lowest key, MIDI
    hi: int = 127
    root: int = 60
    vlo: int = 0  # velocity range, 0..127 (`velocity_step`)
    vhi: int = 127
    rr: int = 0  # the round-robin take it belongs to
    gain: float = 0.0  # dB
    tune: float = 0.0  # cents
    start: float | None = None  # 0..1 of its sample
    end: float | None = None
    loop_start: float | None = None  # both set: it loops there
    loop_end: float | None = None

    @property
    def own_loop(self) -> bool:
        return self.loop_start is not None and self.loop_end is not None


_ZONE_INTS = (("lo", 0, 127), ("hi", 0, 127), ("root", 0, 127), ("vlo", 0, 127))
_ZONE_INTS += (("vhi", 0, 127), ("rr", 0, MAX_TAKES - 1))
_ZONE_FRACS = ("start", "end", "loop_start", "loop_end")


def _zone_num(text: str) -> float:
    v = float(text)
    if not math.isfinite(v):
        raise ValueError(text)
    return v


def parse_zones(text) -> list[Zone]:
    """ "sid,lo,hi,root,vlo,vhi,rr,gain,tune,start,end,loop_start,loop_end;..."
    -> zones. Fields after the id may be left off from the end (their
    defaults) and a span or loop field left empty (the patch's). Anything
    malformed is dropped or clamped, never raised: this reads untrusted
    state. A range given backwards is turned round; a span or loop shorter
    than 1e-4 of the sample is the patch's."""
    if not isinstance(text, str) or not text:
        return []
    out: list[Zone] = []
    for part in text.split(";")[:MAX_ZONES]:
        bits = part.split(",")
        if not valid_id(bits[0]) or len(bits) > 13:
            continue
        bits += [""] * (13 - len(bits))
        kw: dict = {}
        try:
            for (name, lo, hi), raw in zip(_ZONE_INTS, bits[1:7], strict=True):
                if raw:
                    kw[name] = min(hi, max(lo, int(_zone_num(raw))))
            if bits[7]:
                kw["gain"] = min(
                    ZONE_GAIN_DB[1], max(ZONE_GAIN_DB[0], _zone_num(bits[7]))
                )
            if bits[8]:
                kw["tune"] = min(ZONE_TUNE, max(-ZONE_TUNE, _zone_num(bits[8])))
            fr = [
                min(1.0, max(0.0, _zone_num(raw))) if raw else None
                for raw in bits[9:13]
            ]
        except (ValueError, OverflowError):
            continue
        for a in (0, 2):  # a span / a loop: both ends, forwards, or neither
            if fr[a] is None or fr[a + 1] is None or fr[a + 1] - fr[a] < 1e-4:
                fr[a] = fr[a + 1] = None
        kw.update(zip(_ZONE_FRACS, fr, strict=True))
        z = Zone(bits[0], **kw)
        if z.lo > z.hi:
            z = replace(z, lo=z.hi, hi=z.lo)
        if z.vlo > z.vhi:
            z = replace(z, vlo=z.vhi, vhi=z.vlo)
        out.append(z)
    return out


def _fmt(v: float) -> str:
    return f"{v:.5f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def format_zones(zones: list[Zone]) -> str:
    """The inverse of `parse_zones`: trailing defaults left off."""
    parts = []
    for z in zones[:MAX_ZONES]:
        bits = [z.sample, *(str(getattr(z, name)) for name, _, _ in _ZONE_INTS)]
        bits += [_fmt(round(z.gain, 2)), _fmt(round(z.tune, 1))]
        bits += [
            "" if getattr(z, k) is None else f"{getattr(z, k):.5f}" for k in _ZONE_FRACS
        ]
        defaults = ["", "0", "127", "60", "0", "127", "0", "0", "0", "", "", "", ""]
        while len(bits) > 1 and bits[-1] == defaults[len(bits) - 1]:
            bits.pop()
        parts.append(",".join(bits))
    return ";".join(parts)


def clean_zones(text) -> str:
    return format_zones(parse_zones(text))


def zone_samples(text) -> list[str]:
    """Every sample a zone map plays, once each, in map order."""
    return list(dict.fromkeys(z.sample for z in parse_zones(text)))


def velocity_step(velocity) -> int:
    """A note's velocity (0..1) as the 0..127 step a zone's range reads —
    rounded half up, as the live voice's Math.round rounds it."""
    try:
        v = float(np.asarray(velocity, dtype=np.float64).ravel()[0])
    except (TypeError, ValueError, IndexError):
        v = 1.0
    v = v if math.isfinite(v) else 1.0
    return min(127, max(0, math.floor(v * 127 + 0.5)))


def key_step(hz: float) -> int:
    """The MIDI key a pitch sits on (half up, as Math.round)."""
    return math.floor(69 + 12 * math.log2(max(float(hz), 1e-3) / 440.0) + 0.5)


def pick_zones(
    zones: list[Zone], key: int, vel: int, rr: int
) -> list[tuple[int, float]]:
    """Which zones a note plays, and at what weight: those whose keys and
    velocities hold it; of their round-robin takes (the distinct `rr`
    values among them, in order) take number `rr` modulo how many there
    are; and of that take's zones, the two whose velocity ranges start
    highest (the earlier zone first on a tie), crossfaded over the
    velocities both hold — equal power, the softer one fading out from
    where the harder one's range starts to where its own ends. One zone:
    weight 1. None: silence. static/sampler-worklet.js `pick` is this,
    line for line."""
    cands = [
        i for i, z in enumerate(zones) if z.lo <= key <= z.hi and z.vlo <= vel <= z.vhi
    ]
    if not cands:
        return []
    takes = sorted({zones[i].rr for i in cands})
    take = takes[int(rr) % len(takes)]
    sel = sorted(
        (i for i in cands if zones[i].rr == take), key=lambda i: (zones[i].vlo, i)
    )
    if len(sel) == 1:
        return [(sel[0], 1.0)]
    s, h = sel[-2:]
    lo, hi = zones[h].vlo, zones[s].vhi
    t = min(max((vel - lo) / (hi - lo), 0.0), 1.0) if hi > lo else 1.0
    if t <= 0.0:
        return [(s, 1.0)]
    if t >= 1.0:
        return [(h, 1.0)]
    return [(s, math.cos(t * math.pi / 2)), (h, math.sin(t * math.pi / 2))]


def round_robin(p) -> bool:
    """Does a note of this patch depend on which strike of its key it is?
    (A zone map with more than one take.) The schedulers count strikes
    only for such a patch: everything else renders exactly as before."""
    if "sampler" not in (getattr(p, "osc1", None), getattr(p, "osc2", None)):
        return False
    return len({z.rr for z in parse_zones(getattr(p, "sampler_zones", ""))}) > 1


class Strikes:
    """The round-robin index of each note of a phrase: how many times its
    key was struck before it in the same phrase (0, 1, 2, ...), counted
    only for a `round_robin` patch (always 0 otherwise). The live voice
    counts the same way, per key, from the moment its sound arrived."""

    def __init__(self, patch):
        self.on = round_robin(patch)
        self.seen: dict = {}

    def __call__(self, pitch) -> int:
        if not self.on:
            return 0
        k = key_step(_hz(pitch))
        n = self.seen.get(k, 0)
        self.seen[k] = n + 1
        return n


def _hz(pitch) -> float:
    from .core import note_to_hz

    return note_to_hz(pitch)


def zones_from_slices(sid: str, slices: list[Slice], root: int) -> list[Zone]:
    """Chops as a zone map: slice i alone on key root + i (as the chops
    play it, without the wrap), played at its own pitch there (+ its
    semitones: the zone's root moves the other way). A reversed slice
    keeps its span; zones play forwards."""
    out = []
    for i, s in enumerate(slices[:MAX_ZONES]):
        key = int(root) + i
        if not 0 <= key <= 127:
            break
        out.append(
            Zone(sid, key, key, min(127, max(0, key - s.pitch)), start=s.a, end=s.b)
        )
    return out


def place_zone(zones: list[Zone], new: Zone) -> list[Zone]:
    """`new` added to the map without moving any zone a player set: among
    the zones of its layer (the same velocity range and take), one already
    rooted on its key gains it as its next round-robin take (same keys,
    same velocities); one whose keys hold its root gives up the keys on
    `new`'s side of the midpoint between their roots; otherwise `new`
    spans the free keys around its root. The first zone spans the whole
    keyboard."""
    if len(zones) >= MAX_ZONES:
        return zones
    layer = [z for z in zones if (z.vlo, z.vhi, z.rr) == (new.vlo, new.vhi, new.rr)]
    same = [
        z for z in zones if z.root == new.root and (z.vlo, z.vhi) == (new.vlo, new.vhi)
    ]
    if same:
        rr = max(z.rr for z in same) + 1
        if rr < MAX_TAKES:
            return [*zones, replace(new, lo=same[0].lo, hi=same[0].hi, rr=rr)]
    holder = next((z for z in layer if z.lo <= new.root <= z.hi), None)
    if holder is not None:
        # the holder, and its takes and layers on the same keys and root,
        # give up the keys on new's side of the midpoint
        mid = (holder.root + new.root) // 2
        up = new.root > holder.root
        twin = (holder.lo, holder.hi, holder.root)
        out = [
            (replace(z, hi=mid) if up else replace(z, lo=mid + 1))
            if (z.lo, z.hi, z.root) == twin
            else z
            for z in zones
        ]
        lo, hi = (mid + 1, holder.hi) if up else (holder.lo, mid)
        return [*out, replace(new, lo=lo, hi=hi)]
    below = [z.hi for z in layer if z.hi < new.root]
    above = [z.lo for z in layer if z.lo > new.root]
    lo = max(below) + 1 if below else 0
    hi = min(above) - 1 if above else 127
    return [*zones, replace(new, lo=lo, hi=hi)]


def spread(zones: list[Zone]) -> list[Zone]:
    """Each layer's zones (same velocities, same take) re-spread over the
    keyboard by their roots: each from the midpoint with the root below it
    to the midpoint with the root above, the lowest from key 0, the
    highest to 127. Zones sharing a root share its keys."""
    out = list(zones)
    layers: dict = {}
    for i, z in enumerate(zones):
        layers.setdefault((z.vlo, z.vhi, z.rr), []).append(i)
    for idx in layers.values():
        roots = sorted({zones[i].root for i in idx})
        for i in idx:
            r = zones[i].root
            k = roots.index(r)
            lo = 0 if k == 0 else (roots[k - 1] + r) // 2 + 1
            hi = 127 if k == len(roots) - 1 else (r + roots[k + 1]) // 2
            out[i] = replace(zones[i], lo=lo, hi=hi)
    return out


def grid_slices(count: int, start: float = 0.0, end: float = 1.0) -> list[Slice]:
    """`count` equal slices of [start, end)."""
    count = max(1, min(MAX_SLICES, int(count)))
    if end - start < 1e-3:
        start, end = 0.0, 1.0
    edges = np.linspace(start, end, count + 1)
    return [Slice(float(a), float(b)) for a, b in pairwise(edges)]


def onsets(audio: np.ndarray, max_slices: int = 16, min_gap: float = 0.05) -> list[int]:
    """Transient positions (samples) by spectral flux: 1024-sample frames,
    hop 256, half-wave-rectified log-magnitude rise, peaks above a moving
    median + a fraction of the spread, at least `min_gap` apart; the
    strongest `max_slices - 1` kept, plus 0."""
    x = np.asarray(audio, dtype=np.float64)
    mono = x.mean(axis=1) if x.ndim == 2 else x
    n_fft, hop = 1024, 256
    if len(mono) < n_fft * 2:
        return [0]
    frames = np.lib.stride_tricks.sliding_window_view(mono, n_fft)[::hop]
    spec = np.log1p(np.abs(np.fft.rfft(frames * np.hanning(n_fft), axis=1)) * 10)
    flux = np.maximum(np.diff(spec, axis=0), 0).sum(axis=1)
    # plus the frame level's rise in dB: a kick's energy sits in a handful
    # of low bins, so spectral flux alone hears a snare and misses it
    level = 10 * np.log10(np.mean(frames**2, axis=1) + 1e-10)
    rise = np.maximum(np.diff(level), 0)
    flux = flux / (flux.max() or 1.0) + rise / (rise.max() or 1.0)
    flux = np.concatenate([[0.0], flux])
    k = 8
    padded = np.pad(flux, k, mode="edge")
    med = np.median(np.lib.stride_tricks.sliding_window_view(padded, 2 * k + 1), axis=1)
    thresh = med + 0.1 * (flux.max() - np.median(flux))
    cand = [
        i
        for i in range(1, len(flux) - 1)
        if flux[i] > thresh[i] and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1]
    ]
    gap = max(1, int(min_gap * SR / hop))
    picked: list[int] = []
    for i in sorted(cand, key=lambda i: -flux[i]):
        if all(abs(i - j) >= gap for j in picked) and i >= gap // 2:
            picked.append(i)
        if len(picked) >= max_slices - 1:
            break
    # frame i rises when the onset enters its last hop; the slice starts
    # 3 ms ahead of it, so the attack is kept whole
    pre = int(0.003 * SR)
    return [0] + sorted(max(0, i * hop + n_fft - hop - pre) for i in picked)


def transient_slices(sid, max_slices: int = 16) -> list[Slice]:
    x = get(sid)
    if x is None or not len(x):
        return []
    n = len(x)
    pts = onsets(x, max_slices) + [n]
    return [Slice(a / n, b / n) for a, b in pairwise(pts) if b - a > 32]


def start_bounds(
    slices: list[Slice], index: int, min_len: float = 1e-3
) -> tuple[float, float]:
    """Where slice `index`'s start may go (fractions of the sample): after
    the previous slice's start and before its own end, each by at least
    `min_len`, so no slice — its own or the neighbour that follows it —
    shrinks below that. Clamp as min(max(x, lo), hi): in a run of slices
    already shorter than that, the slice's own end wins."""
    s = slices[index]
    lo = slices[index - 1].a + min_len if index > 0 else 0.0
    return lo, s.b - min_len


def nudge(
    slices: list[Slice], index: int, delta: float, min_len: float = 1e-3
) -> list[Slice]:
    """Move slice `index`'s start by `delta` (fraction of the sample),
    carrying the previous slice's end with it when they touch; kept
    inside both neighbours (`start_bounds`)."""
    if not 0 <= index < len(slices):
        return slices
    out = list(slices)
    s = out[index]
    lo, hi = start_bounds(out, index, min_len)
    new_a = min(max(s.a + delta, lo), hi)
    out[index] = Slice(new_a, s.b, s.pitch, s.rev)
    if index > 0 and abs(out[index - 1].b - s.a) < 1e-6:
        p = out[index - 1]
        out[index - 1] = Slice(p.a, new_a, p.pitch, p.rev)
    return out


def move_start(
    slices: list[Slice], index: int, at: float, min_len: float = 1e-3
) -> list[Slice]:
    """Put slice `index`'s start at `at` (a dragged marker's absolute
    position), with `nudge`'s rules: clamped by `start_bounds`, the
    touching neighbour's end following."""
    if not 0 <= index < len(slices) or not math.isfinite(at):
        return slices
    return nudge(slices, index, at - slices[index].a, min_len)


# --- pitch -------------------------------------------------------------------
# YIN (de Cheveigné & Kawahara 2002): the cumulative-mean-normalised
# difference function of a frame, its first dip under a threshold taken as
# the period, refined by a parabola through its neighbours. Run on a dozen
# frames of the sample's loud part (within 6 dB of its loudest frame: a
# recording's note, not its tail or the silence around it); the answer is
# their median, and the confidence is how periodic they were (1 - the
# median dip) times the share of frames that agree on it within half a
# semitone — so noise, or a loop of drums and chords, comes out low.

PITCH_HZ = (40.0, 2000.0)  # what the detector looks for
_YIN_WINDOW = 2048
_YIN_DIP = 0.15
_YIN_FRAMES = 12


def _yin(frame: np.ndarray, w: int, tau_min: int, tau_max: int) -> tuple[float, float]:
    """(period in samples, the normalised difference at it) for one frame
    of `w + tau_max` samples."""
    frame = frame - frame.mean()
    size = 1 << int(math.ceil(math.log2(len(frame) + w)))
    # sum_j x[j] x[j + tau] over the window, all lags at once
    corr = np.fft.irfft(
        np.fft.rfft(frame, size) * np.conj(np.fft.rfft(frame[:w], size)), size
    )[: tau_max + 1]
    energy = np.concatenate([[0.0], np.cumsum(frame * frame)])
    taus = np.arange(tau_max + 1)
    d = np.maximum(energy[w] + energy[taus + w] - energy[taus] - 2.0 * corr, 0.0)
    d[0] = 0.0
    cmnd = np.ones(tau_max + 1)
    cmnd[1:] = d[1:] * taus[1:] / np.maximum(np.cumsum(d[1:]), 1e-12)
    seg = cmnd[tau_min : tau_max + 1]
    under = np.flatnonzero(seg < _YIN_DIP)
    if len(under):
        t = int(under[0])
        while t + 1 < len(seg) and seg[t + 1] < seg[t]:
            t += 1
    else:
        t = int(np.argmin(seg))
    tau = t + tau_min
    shift = 0.0
    if tau_min < tau < tau_max:
        a, b, c = cmnd[tau - 1], cmnd[tau], cmnd[tau + 1]
        den = a - 2.0 * b + c
        if den > 1e-12:
            shift = min(0.5, max(-0.5, 0.5 * (a - c) / den))
    return tau + shift, float(cmnd[tau])


def detect_pitch(audio: np.ndarray, sr: int = SR) -> tuple[float, float]:
    """(MIDI note as a float, confidence 0..1) of mono or stereo audio;
    (nan, 0.0) when there is nothing to measure (silence, too short)."""
    x = np.asarray(audio, dtype=np.float64)
    mono = x.mean(axis=1) if x.ndim == 2 else x.ravel()
    tau_min = int(sr / PITCH_HZ[1])
    tau_max = int(math.ceil(sr / PITCH_HZ[0]))
    w = _YIN_WINDOW
    span = w + tau_max
    if len(mono) < span or not np.all(np.isfinite(mono)):
        return math.nan, 0.0
    starts = np.arange(0, len(mono) - span + 1, w // 2)
    energy = np.concatenate([[0.0], np.cumsum(mono * mono)])
    rms = np.sqrt(np.maximum(energy[starts + span] - energy[starts], 0.0) / span)
    if rms.max() < 1e-3:
        return math.nan, 0.0
    loud = starts[rms >= rms.max() * 0.5]
    pick = loud[
        np.unique(np.linspace(0, len(loud) - 1, _YIN_FRAMES).round().astype(int))
    ]
    found = [_yin(mono[s : s + span], w, tau_min, tau_max) for s in pick]
    periods = np.array([p for p, _ in found])
    dips = np.array([c for _, c in found])
    notes = 69.0 + 12.0 * np.log2(sr / periods / 440.0)
    agree = np.abs(notes - np.median(notes)) < 0.5
    if not agree.any():  # an even split with no middle: no pitch
        return float(np.median(notes)), 0.0
    confidence = max(0.0, 1.0 - float(np.median(dips))) * float(agree.mean())
    return float(np.median(notes[agree])), min(1.0, confidence)


@lru_cache(maxsize=128)
def _sample_pitch(sid: str) -> tuple[float, float]:
    x = get(sid)
    return (math.nan, 0.0) if x is None else detect_pitch(x)


def sample_pitch(sid) -> tuple[float, float]:
    """`detect_pitch` of a stored sample, memoised by id (an id is its
    content, so it can never go stale); (nan, 0.0) for a missing one —
    not memoised, so a sample that arrives later is measured then."""
    if get(sid) is None:
        return math.nan, 0.0
    return _sample_pitch(sid)


# --- rendering ---------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """The Patch's sampler fields, as `Synth._osc_stack` hands them over."""

    sample: str = ""
    root: int = 60
    start: float = 0.0
    end: float = 1.0
    loop: str = "off"
    loop_start: float = 0.0
    loop_end: float = 1.0
    xfade: float = 0.01
    keytrack: str = "on"
    mode: str = "resample"
    stretch: object = 1.0  # a length factor; may be a per-sample array
    slices: str = ""
    # a zone map (`parse_zones`): when set, it plays instead of `sample`
    # and `slices`, a zone picked by the note's key, `velocity` (0..1) and
    # `rr` (which strike of its key it is: `Strikes`)
    zones: str = ""
    velocity: float = 1.0
    rr: int = 0

    @classmethod
    def of(cls, p, velocity: float = 1.0, rr: int = 0) -> Settings:
        return cls(
            p.sampler_sample,
            p.sampler_root,
            p.sampler_start,
            p.sampler_end,
            p.sampler_loop,
            p.sampler_loop_start,
            p.sampler_loop_end,
            p.sampler_xfade,
            p.sampler_keytrack,
            p.sampler_mode,
            p.sampler_stretch,
            p.sampler_slices,
            p.sampler_zones,
            velocity,
            rr,
        )


def _first(v, default: float) -> float:
    """A per-note value: the first sample of an automation array."""
    try:
        f = float(np.asarray(v, dtype=np.float64).ravel()[0])
    except (TypeError, ValueError, IndexError):
        return default
    return f if math.isfinite(f) else default


def midi_hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69.0) / 12.0)


def _declick_len(n: int, ms: float = 2.0) -> int:
    """How many samples `_declick` fades at each end of `n`."""
    return min(n // 4, int(ms * SR / 1000))


def _declick(seg: np.ndarray, head: bool, tail: bool, ms: float = 2.0) -> np.ndarray:
    k = _declick_len(len(seg), ms)
    if k < 2:
        return seg
    seg = seg.copy()
    ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)[:, None]
    if head:
        seg[:k] *= ramp
    if tail:
        seg[-k:] *= ramp[::-1]
    return seg


class _Source:
    """What a note reads: `buf` with positions in [0, end), optionally
    wrapping [lo, hi) — the crossfade already baked into buf's loop tail."""

    def __init__(self, buf: np.ndarray, end: int, lo: int = 0, hi: int = 0):
        self.looping = hi - lo > 1
        if self.looping:
            # index hi reads lo, so interpolation across the seam is right
            buf = np.concatenate([buf[:hi], buf[lo : lo + 1]])
            end = hi
        self.buf, self.end, self.lo, self.hi = buf, end, lo, hi

    def read(self, pos: np.ndarray) -> np.ndarray:
        pos = np.asarray(pos, dtype=np.float64)
        if self.looping:
            span = self.hi - self.lo
            over = pos >= self.hi
            pos = np.where(over, self.lo + np.mod(pos - self.lo, span), pos)
            valid = pos >= 0
        else:
            valid = (pos >= 0) & (pos <= self.end - 1)
        last = len(self.buf) - 2
        i = np.clip(np.floor(pos), 0, max(0, last)).astype(np.int64)
        fr = np.clip(pos - i, 0.0, 1.0)[:, None]
        y = self.buf[i] * (1.0 - fr) + self.buf[np.minimum(i + 1, last + 1)] * fr
        y[~valid] = 0.0
        return y

    def done(self, pos: float) -> bool:
        return not self.looping and pos >= self.end - 1


def _loop_source(x: np.ndarray, s0: int, e: int, lo: int, hi: int, xfade: float):
    """[0, e) with [lo, hi) looping and the crossfade baked in. A start
    inside the audio (`s0` > 0) fades its first 2 ms in, as a one-shot's
    does: a loop used to start on whatever sample the start point hit."""
    lo = max(0, min(lo, hi - 2))
    k = int(min(xfade * SR, lo, (hi - lo) // 2))
    buf = x[:hi].astype(np.float64, copy=True)
    if k >= 2:
        out_seg, in_seg = x[hi - k : hi], x[lo - k : lo]
        # the fade law follows how alike the two sides are (holdloop.py's
        # lesson): a linear fade sums correlated audio to a constant level
        # but dips uncorrelated audio by 3 dB, equal power the reverse. Both
        # gains are linear ramps scaled so fi^2 + fo^2 + 2*rho*fi*fo = 1.
        num = float(np.sum(out_seg * in_seg))
        den = float(np.sqrt(np.sum(out_seg**2) * np.sum(in_seg**2))) or 1.0
        rho = min(max(num / den, 0.0), 1.0)
        fi = np.linspace(0.0, 1.0, k)[:, None]
        fo = 1.0 - fi
        g = 1.0 / np.sqrt(fi**2 + fo**2 + 2 * rho * fi * fo)
        buf[hi - k : hi] = (out_seg * fo + in_seg * fi) * g
    if s0 <= 0:
        return _Source(buf, hi, lo, hi)
    first = _declick(buf[s0:], True, False)
    if s0 + _declick_len(hi - s0) <= lo:  # the fade is over before the loop
        buf[s0:] = first
        return _Source(buf, hi, lo, hi)
    # The fade lands inside the loop, where every later pass would read it
    # again: the first pass gets its own copy and the loop wraps behind it.
    # buf[hi - 1] still flows into buf[hi] = x[lo], as the seam does.
    buf = np.concatenate([buf[:s0], first, buf[lo:hi]])
    return _Source(buf, 2 * hi - lo, hi, 2 * hi - lo)


def _slice_source(x: np.ndarray, s: Slice) -> _Source:
    """One chop as a note reads it: its span, reversed if asked, declicked
    at both ends."""
    L = len(x)
    seg = x[int(s.a * L) : max(int(s.a * L) + 4, int(s.b * L))]
    if s.rev:
        seg = seg[::-1]
    return _Source(_declick(seg, True, True).astype(np.float64), len(seg))


def _whole_source(x: np.ndarray, st: Settings) -> tuple[_Source, int] | None:
    """The whole sample as a note reads it (start/end, the loop and its
    crossfade, the declick fades) and where reading starts; None when the
    span is too short to play."""
    L = len(x)
    a = min(max(_first(st.start, 0.0), 0.0), 1.0)
    b = min(max(_first(st.end, 1.0), 0.0), 1.0)
    s0, e = int(a * L), int(b * L)
    if e - s0 < 4:
        return None
    if st.loop == "on":
        lo = int(min(max(_first(st.loop_start, 0.0), 0.0), 1.0) * L)
        hi = int(min(max(_first(st.loop_end, 1.0), 0.0), 1.0) * L)
        lo, hi = max(lo, 0), min(hi, e)
        if hi - lo >= 64 and s0 < hi:
            return _loop_source(x, s0, e, lo, hi, _first(st.xfade, 0.01)), s0
        # no loop to speak of: the one-shot below (its head fade used to
        # land on the buffer's first sample here, not on the start point)
    head = x[:e]
    if s0 > 0:  # a start inside the audio: fade its first 2 ms in
        head = head.copy()
        head[s0:] = _declick(head[s0:], True, False)
    return _Source(_declick(head, False, True).astype(np.float64), e), s0


def render(freq, n: int, sr: int = SR, settings: Settings | None = None, key_hz=None):
    """The sampler oscillator: stereo (n, 2), or zeros (n,) when there is
    nothing to play."""
    st = settings or Settings()
    if st.zones:
        zones = parse_zones(st.zones)
        if zones:
            return _render_zones(freq, n, sr, st, zones, key_hz)
    x = get(st.sample)
    if x is None or len(x) < 4:
        return np.zeros(n)
    f = np.asarray(as_array(freq, n), dtype=np.float64)
    if f.ndim == 0:
        f = np.full(n, float(f))
    key = float(key_hz) if key_hz else float(f[0])
    key = max(key, 1e-3)
    root_hz = midi_hz(_first(st.root, 60.0))
    slices = parse_slices(st.slices)
    formant = st.mode == "formant"
    if slices:
        k = int(round(12.0 * math.log2(key / root_hz)))
        s = slices[k % len(slices)]
        src = _slice_source(x, s)
        rel = f / key * 2.0 ** (s.pitch / 12.0)
        s0 = 0
        # a slice keeps its own pitch at its key: formant mode's grain
        # period is the root's, as for the whole sample
    else:
        whole = _whole_source(x, st)
        if whole is None:
            return np.zeros(n)
        src, s0 = whole
        rel = f / root_hz if st.keytrack != "off" else f / key
    speed = rel * (SR / sr)  # source samples per output sample
    stretch = np.asarray(st.stretch, dtype=np.float64)
    if stretch.ndim:
        stretch = np.clip(as_array(stretch, n), 0.05, 16.0)
    else:
        stretch = float(np.clip(stretch if np.isfinite(stretch) else 1.0, 0.05, 16.0))
    if formant:
        out = _psola(src, s0, speed, stretch, n, root_hz, sr)
    elif np.any(np.abs(np.asarray(stretch) - 1.0) > 1e-3):
        out = _grains(src, s0, speed, stretch, n, sr)
    else:
        pos = s0 + np.concatenate([[0.0], np.cumsum(speed[:-1])])
        out = src.read(pos)
    return out


def zone_settings(st: Settings, z: Zone) -> Settings:
    """What one zone plays: its sample at its root, its own span and loop
    where it has them, else the patch's; no chops."""
    kw: dict = {"sample": z.sample, "root": z.root, "slices": "", "zones": ""}
    if z.start is not None:
        kw.update(start=z.start, end=z.end)
    if z.own_loop:
        kw.update(loop="on", loop_start=z.loop_start, loop_end=z.loop_end)
    return replace(st, **kw)


def _render_zones(freq, n: int, sr: int, st: Settings, zones: list[Zone], key_hz):
    """A zone map's note: the zones `pick_zones` chooses for its key,
    velocity and strike, each played as a sample of its own (`render`, at
    its root and fine tune), at its weight and gain, summed. One zone at
    weight 1 and 0 dB is exactly that sample's own render."""
    f = np.asarray(as_array(freq, n), dtype=np.float64)
    if f.ndim == 0:
        f = np.full(n, float(f))
    key = float(key_hz) if key_hz else float(f[0])
    picks = pick_zones(zones, key_step(key), velocity_step(st.velocity), st.rr)
    out = None
    for i, w in picks:
        z = zones[i]
        fz = f * 2.0 ** (z.tune / 1200.0) if z.tune else f
        y = render(fz, n, sr, zone_settings(st, z), key)
        if y.ndim == 1:  # a zone with nothing to play (its sample missing)
            continue
        g = w * 10.0 ** (z.gain / 20.0)
        y = y * g if g != 1.0 else y
        out = y if out is None else out + y
    return np.zeros(n) if out is None else out


_WSOLA_TOL = 0.010  # seconds a stretch grain may slide to stay in phase


def _advance(stretch, n: int, sr: int, s0: float) -> np.ndarray:
    """Source position (samples) per output sample at 1/stretch speed."""
    step = (SR / sr) / (stretch if np.ndim(stretch) else np.full(n, stretch))
    return s0 + np.concatenate([[0.0], np.cumsum(step[:-1])])


def _grains(src: _Source, s0, speed, stretch, n: int, sr: int) -> np.ndarray:
    """Granular time-stretch (WSOLA): Hann grains read at `speed` (the
    pitch ratio) from a source position that advances at 1/stretch. Each
    grain may slide up to `_WSOLA_TOL` from that position to where it best
    continues the previous grain's audio (cross-correlation over the
    grain), so overlapping grains add in phase instead of combing. At
    stretch 1 the best continuation is the nominal position: plain
    reading. Normalised by the window sum actually laid down, so the edges
    hold their level."""
    g = int(0.046 * sr) // 4 * 4  # ~46 ms grains, 4x overlap
    hop = g // 4
    half = g // 2
    tol = int(_WSOLA_TOL * sr)
    win = np.hanning(g + 1)[:-1]
    sp = _advance(stretch, n, sr, s0)
    step0 = sp[1] - sp[0] if n > 1 else 1.0
    out = np.zeros((n + g, 2))
    wsum = np.zeros(n + g)
    j = np.arange(g)
    prev = None  # the previous grain's first source position
    for c in range(0, n + half, hop):
        ci = min(c, n - 1)
        r = float(speed[ci])
        nominal = sp[ci] + (c - ci) * step0 - half * r
        if src.done(nominal):
            break
        first = nominal
        if prev is not None and tol:
            natural = src.read(prev + hop * r + j * r).mean(axis=1) * win
            seg = src.read(nominal - tol * r + np.arange(g + 2 * tol) * r).mean(axis=1)
            corr = np.correlate(seg, natural, "valid")
            if np.any(corr > 0):
                first = nominal - tol * r + int(np.argmax(corr)) * r
        prev = first
        pos = first + j * r
        y = src.read(pos)
        ok = (pos >= s0).astype(np.float64)
        t0 = c - half
        a, b = max(0, t0), min(n + g, t0 + g)
        if b <= a:
            continue
        w = (win * ok)[a - t0 : b - t0]
        out[a:b] += y[a - t0 : b - t0] * w[:, None]
        wsum[a:b] += w
    good = wsum > 1e-3
    out[good] /= wsum[good][:, None]
    out[~good] = 0.0
    return out[:n]


def time_stretch(audio: np.ndarray, factor: float, sr: int = SR) -> np.ndarray:
    """`audio` (mono or stereo at `sr`) made `factor` times as long at the
    same pitch — the stretch a sampler note uses (`_grains`: WSOLA), on a
    whole buffer. The Sample Lab fits a region cut at one tempo to the
    session's with it. Returns stereo (n, 2); a factor within a thousandth
    of 1 is the audio itself."""
    x = np.asarray(audio, dtype=np.float64)
    if x.ndim == 1:
        x = np.column_stack([x, x])
    factor = float(np.clip(factor, 0.05, 16.0))
    if abs(factor - 1.0) <= 1e-3 or len(x) < 4:
        return x
    n = int(round(len(x) * factor))
    return _grains(_Source(x, len(x)), 0.0, np.ones(n), factor, n, sr)


def edge_slices(edges: list[float]) -> list[Slice]:
    """Slices between consecutive `edges` (0..1 of the sample, ascending):
    one per beat or bar of a region cut on a grid. At most MAX_SLICES; an
    edge closer than 1e-3 to the one before is dropped."""
    kept: list[float] = []
    for e in edges:
        e = min(max(float(e), 0.0), 1.0)
        if not kept or e - kept[-1] > 1e-3:
            kept.append(e)
    return [Slice(a, b) for a, b in pairwise(kept)][:MAX_SLICES]


def _psola(src: _Source, s0, speed, stretch, n: int, root_hz: float, sr: int):
    """Formant-keeping pitch: grains of two source periods, read at the
    source's own rate from whole-period-snapped positions, laid one output
    period apart."""
    p_src = SR / root_hz  # source period, source samples
    g = int(min(max(2 * p_src, 32), 8192))
    half = g // 2
    win = np.hanning(g + 1)[:-1]
    ks = np.arange(g) - half
    sp = _advance(stretch, n, sr, s0)
    out = np.zeros((n + g, 2))
    c = 0.0
    while c < n:
        ci = int(c)
        centre = s0 + round((sp[ci] - s0) / p_src) * p_src
        if src.done(centre):
            break
        # the grain lands at an integer sample but its centre is fractional:
        # read the source offset by that fraction, so at the root pitch
        # grains tile the source exactly
        y = src.read(centre + (ks - (c - ci)) * (SR / sr)) * win[:, None]
        p_out = sr / max(float(speed[ci]) * root_hz * (sr / SR), 1.0)  # samples
        # grains a source period apart add incoherently: overlapping ones
        # (an upshift) hold the level as they are; the gaps of a downshift
        # lose it by the overlap, made up by its square root (measured flat
        # within ~2 dB over +-2 octaves on a sung-vowel-like source)
        ov = g / (2.0 * p_out)
        if ov < 1.0:
            y *= ov**-0.5
        t0 = ci - half
        a, b = max(0, t0), t0 + g
        out[a:b] += y[a - t0 :]
        c += max(p_out, 1.0)
    return out[:n]


# --- the live voice ----------------------------------------------------------


@lru_cache(maxsize=256)
def _dual_mono(sid: str) -> bool:
    x = get(sid)
    return x is not None and bool(np.array_equal(x[:, 0], x[:, 1]))


def dual_mono(sid) -> bool:
    """Are the sample's two channels the same? The live voice is then sent
    one of them (half the payload). A missing sample counts: both paths
    play silence. Cached by id — a sample id is its content."""
    if not valid_id(sid) or get(sid) is None:
        return True
    return _dual_mono(sid)


def stretch_is_one(stretch) -> bool:
    """A scalar stretch of 1: plain resampling, no grain engine."""
    s = np.asarray(stretch, dtype=np.float64)
    return s.ndim == 0 and bool(np.isfinite(s)) and abs(float(s) - 1.0) <= 1e-3


def live_stretch_ok(p) -> bool:
    """Does the live voice play this patch's `sampler_stretch`? In
    "formant" mode any scalar (the grain train's source clock runs at
    1 / stretch); in "resample" mode only 1 — a stretch there is `_grains`,
    WSOLA, which stays offline. A drawn (array) stretch is offline in both."""
    s = np.asarray(p.sampler_stretch, dtype=np.float64)
    if p.sampler_mode == "formant":
        return s.ndim == 0 and bool(np.isfinite(s))
    return stretch_is_one(s)


def live_ok(p) -> bool:
    """Can the live voice play this patch's sampler? "resample" mode with
    no stretch (the tape read) or "formant" mode at any scalar stretch
    (`_psola`'s grain train, run in the voice); mono or true stereo,
    within the payload cap. The WSOLA stretch stays offline (the module
    docstring says why). Only osc1 offers the sampler. What else a
    formant sampler needs of the patch — a pitch the voice can foresee —
    is studio/paramui.py's (`_FORMANT_UNSEEN`, `_cross_fm_ok`)."""
    if p.osc1 != "sampler":
        return True
    return live_stretch_ok(p) and live_size(p) <= LIVE_MAX_SAMPLES


def live_size(p) -> int:
    """`live_samples` for whatever the patch plays: its zone map's every
    sample, else its sample (whole or chopped)."""
    if p.sampler_zones:
        return live_zone_samples(p.sampler_zones)
    return live_samples(p.sampler_sample, p.sampler_slices)


def _zones_channels(zones: list[Zone]) -> int:
    """A zone map sends both channels of every zone when any zone's
    sample is true stereo (a dual-mono one then plays as a stereo pair of
    two equal sides, which is the same sound), else one."""
    return 2 if any(live_channels(z.sample) == 2 for z in zones) else 1


def live_zone_samples(text) -> int:
    """How many samples `live_payload` sends for a zone map: each zone's
    whole-sample bound, over every channel it sends."""
    zones = parse_zones(text)
    frames = 0
    for z in zones:
        x = get(z.sample)
        frames += 0 if x is None else 2 * len(x) + 1
    return frames * _zones_channels(zones)


_LIVE_KEYS = ("start", "end", "loop", "loop_start", "loop_end", "xfade", "slices")
_LIVE_KEYS += ("zones",)
# The live buffers' total, in samples (32 MB of float32), both channels of
# a true-stereo sample counted. Real chops are disjoint, but slices may
# overlap: 32 full-length ones of a 30 s sample would be ~170 MB per
# request (twice that in stereo). Past this the keys stay on the WAV path.
# A whole 30 s stereo sample, its loop unrolled, is 5.3M: under it.
LIVE_MAX_SAMPLES = 8 << 20


def live_channels(sid) -> int:
    """How many channels `live_payload` sends: 1 when the sample's two are
    the same (or it is missing), else 2."""
    return 1 if dual_mono(sid) else 2


def live_samples(sid, slices_text) -> int:
    """How many samples `live_payload` would send (the whole sample's
    bound, or every chop's), over every channel it sends."""
    x = get(sid) if valid_id(sid) else None
    if x is None:
        return 0
    L = len(x)
    slices = parse_slices(slices_text)
    if slices:
        frames = sum(max(int(s.a * L) + 4, int(s.b * L)) - int(s.a * L) for s in slices)
    else:
        # a loop whose start point sits inside it plays its first pass
        # from a copy of its own (`_loop_source`): at most the sample twice
        frames = 2 * L + 1
    return frames * live_channels(sid)


def _live_query(p) -> dict:
    """What shapes the prepared buffers (nothing else does: root and key
    track only change how fast the voice reads them). A zone map rides
    along only when there is one, so no other URL moved."""
    q = {
        "start": _first(p.sampler_start, 0.0),
        "end": _first(p.sampler_end, 1.0),
        "loop": p.sampler_loop,
        "loop_start": _first(p.sampler_loop_start, 0.0),
        "loop_end": _first(p.sampler_loop_end, 1.0),
        "xfade": _first(p.sampler_xfade, 0.01),
        "slices": clean_slices(p.sampler_slices),
    }
    zones = clean_zones(p.sampler_zones)
    return {**q, "zones": zones} if zones else q


def live_url(p) -> str | None:
    """Where the live voice gets osc1's sampler buffers: content-addressed
    (the id is the audio, the query the rest), so cacheable for good.
    None when no live sampler plays. A zone map's URL is named by its
    first zone's sample (the query names every one)."""
    if p.osc1 != "sampler" or not live_ok(p):
        return None
    sid = p.sampler_sample
    if p.sampler_zones:
        sid = next(iter(zone_samples(p.sampler_zones)), "")
    if not valid_id(sid):
        return None
    return f"/sampler/live/{sid}.bin?{urlencode(_live_query(p))}"


def live_settings(query: dict) -> Settings:
    """`live_url`'s query back into the Settings the buffers come from."""
    q = {k: query.get(k) for k in _LIVE_KEYS}
    num = lambda k, d: _first(q[k], d) if q[k] is not None else d  # noqa: E731
    return Settings(
        start=num("start", 0.0),
        end=num("end", 1.0),
        loop="on" if q["loop"] == "on" else "off",
        loop_start=num("loop_start", 0.0),
        loop_end=num("loop_end", 1.0),
        xfade=num("xfade", 0.01),
        slices=clean_slices(q["slices"] or ""),
        zones=clean_zones(q["zones"] or ""),
    )


def _zone_sources(st: Settings) -> tuple[list, list[dict], int]:
    """A zone map's buffers: one source per zone that has one (its sample
    as `render` reads it for that zone: `zone_settings`), and per zone what
    the voice picks it by — keys, velocities, take, root, gain, tune and
    `src`, the index of its source (-1: its sample is missing, a silent
    zone that still counts in the pick)."""
    zones = parse_zones(st.zones)
    sources, info = [], []
    for z in zones:
        x = get(z.sample)
        ok = x is not None and len(x) >= 4
        whole = _whole_source(x, zone_settings(st, z)) if ok else None
        if whole is not None:
            src, s0 = whole
            sources.append(({"s0": s0, "pitch": 0.0}, src))
        info.append(
            {
                "lo": z.lo,
                "hi": z.hi,
                "root": z.root,
                "vlo": z.vlo,
                "vhi": z.vhi,
                "rr": z.rr,
                "gain": z.gain,
                "tune": z.tune,
                "src": len(sources) - 1 if whole is not None else -1,
            }
        )
    return sources, info, _zones_channels(zones)


def live_payload(sid: str, st: Settings) -> bytes:
    """The buffers the live voice reads, as `render` would read them for
    these settings: the whole sample (start/end, loop and crossfade,
    declick), or every chop, or — `st.zones` set, `sid` then unread —
    every zone's sample (`_zone_sources`; the header's `zones`). A uint32
    header length, a JSON header — `ch` (`live_channels`: 1 for a sample
    whose channels are the same, else 2) and per source its length `n` in
    frames, `end`, loop `lo`/`hi`/`looping`, start `s0` and semitone
    `pitch` — then each source's samples as little-endian float32, a
    channel at a time (`n` left, then `n` right when `ch` is 2), the
    header space-padded so they start 4-byte aligned. No source: nothing
    to play (silence, as `render` gives). Raises ValueError past
    LIVE_MAX_SAMPLES."""
    zoned = bool(parse_zones(st.zones))
    size = live_zone_samples(st.zones) if zoned else live_samples(sid, st.slices)
    if size > LIVE_MAX_SAMPLES:
        raise ValueError("live sampler buffers too large")
    sources: list[tuple[dict, np.ndarray]] = []
    zones: list[dict] = []
    if zoned:
        sources, zones, ch = _zone_sources(st)
    else:
        x = get(sid) if valid_id(sid) else None
        ch = live_channels(sid)
        if x is not None and len(x) >= 4:
            slices = parse_slices(st.slices)
            if slices:
                for s in slices:
                    src = _slice_source(x, s)
                    sources.append(({"s0": 0, "pitch": s.pitch}, src))
            else:
                whole = _whole_source(x, st)
                if whole is not None:
                    src, s0 = whole
                    sources.append(({"s0": s0, "pitch": 0.0}, src))
    head, data = [], []
    for info, src in sources:
        buf = np.ascontiguousarray(src.buf[:, :ch].T, dtype="<f4")
        head.append(
            {
                **info,
                "n": buf.shape[1],
                "end": int(src.end),
                "lo": int(src.lo),
                "hi": int(src.hi),
                "looping": bool(src.looping),
            }
        )
        data.append(buf.tobytes())
    header = {
        "sr": SR,
        "ch": ch,
        "sources": head,
        "slices": not zoned and bool(parse_slices(st.slices)),
    }
    if zoned:
        header["zones"] = zones
    meta = json.dumps(header, separators=(",", ":")).encode()
    meta += b" " * (-len(meta) % 4)  # the samples start 4-byte aligned (Float32Array)
    return struct.pack("<I", len(meta)) + meta + b"".join(data)
