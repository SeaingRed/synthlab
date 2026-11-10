"""Your own wavetables: a table made from
audio — a clip or a sample sliced into single-cycle frames — or drawn by
hand, stored content-addressed like the sampler's samples.

A table is its frames' harmonic spectra (`spectra`: (frames, 256)
complex, sine part real and cosine part imaginary, as `core._wt_spectra`
defines the built-in tables), so `core.wavetable_stack` builds the same
mip stack the built-ins have and the shape never aliases. Its id is the
first 16 hex of the sha1 of those spectra (complex64), and a Patch names
it in `wt_custom` — every render key that hashes the patch carries the
table by content. Stored as `<dir>/<id>.npy` (the spectra) beside
`<id>.json` (a name and where it came from) in any `add_dir` dir (the
studio's `studio_data/wavetables/`); a table this machine doesn't hold
plays the "basic" table (`synth._wt_table`), never an error.

The live voice gets a table as `GET /wavetable/live/<id>.bin`
(`live_payload`: the mip stack as float32, header-split like
/wavetables.bin) — never by growing /wavetables.bin.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import uuid
from collections import OrderedDict
from pathlib import Path

import numpy as np

from . import core
from .core import SR

MAX_FRAMES = 64  # from audio: 4..64 frames across the clip
MIN_FRAMES = 1
DRAW_FRAMES = 4  # the editor draws up to four
DRAW_POINTS = 128  # points a drawn cycle may carry (the editor sends 64)
HARMONICS = core._WT_MIPS[0]  # 256: what a frame keeps
MODES = ("slice", "pitch")  # equal slices of the clip, or one period each
NAME_MAX = 40
_ID = re.compile(r"[0-9a-f]{16}\Z")
_CACHE_BYTES = 128 << 20

_DIRS: list[Path] = []
_LOCK = threading.Lock()


class _Cache:
    """id -> mip stack, least recently used first out."""

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


def add_dir(path: Path | str) -> Path:
    """Search `path` for tables (created if missing), newest first."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if p in _DIRS:
            _DIRS.remove(p)
        _DIRS.insert(0, p)
        del _DIRS[64:]
    return p


def valid_id(tid) -> bool:
    return isinstance(tid, str) and bool(_ID.match(tid))


def clean_id(tid) -> str:
    """`wt_custom` as `params.clamp` keeps it: an id, or ""."""
    return tid if valid_id(tid) else ""


# --- frames -> spectra ---------------------------------------------------------


def spectra(cycles) -> np.ndarray:
    """Single cycles ((frames, k) samples, any k >= 8) -> each frame's first
    HARMONICS harmonics, put to a peak of 1 and rounded to complex64 (the
    stored, hashed form). A silent frame stays silent."""
    x = np.asarray(cycles, dtype=np.float64)
    x = np.nan_to_num(x.reshape(-1, x.shape[-1]))
    x = x - x.mean(axis=1, keepdims=True)
    k = x.shape[1]
    c = np.fft.rfft(x, axis=1)[:, 1 : HARMONICS + 1] * (2.0 / k)
    out = np.zeros((len(x), HARMONICS), dtype=np.complex128)
    out[:, : c.shape[1]] = -c.imag + 1j * c.real
    # the stack puts each frame to its own peak; the stored amplitudes stay
    # on one scale (the loudest cycle's), so equal tables hash equal
    return (out / max(float(np.abs(x).max()), 1e-12)).astype(np.complex64)


def table_id(spec: np.ndarray) -> str:
    h = hashlib.sha1(f"{spec.shape}".encode())
    h.update(np.ascontiguousarray(spec, dtype=np.complex64).tobytes())
    return h.hexdigest()[:16]


# --- the store -----------------------------------------------------------------


def save(directory: Path | str, spec: np.ndarray, name: str, source: str = "") -> str:
    """Store a table (once: same spectra, same file) with its name; its id."""
    spec = np.ascontiguousarray(spec, dtype=np.complex64)
    tid = table_id(spec)
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{tid}.npy"
    if not path.exists():
        tmp = path.with_name(f"{tid}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "wb") as f:
                np.save(f, spec, allow_pickle=False)
            tmp.replace(path)
        except OSError:
            if not path.exists():
                raise
        finally:
            tmp.unlink(missing_ok=True)
    meta = {"name": clean_name(name), "source": str(source)[:120], "frames": len(spec)}
    (d / f"{tid}.json").write_text(json.dumps(meta), encoding="utf-8")
    _MEM.put(tid, core.wavetable_stack(spec))
    return tid


def clean_name(name) -> str:
    text = " ".join(str(name or "").split())[:NAME_MAX]
    return text or "My table"


def _read(path: Path) -> np.ndarray | None:
    """A stored table's spectra, or None for anything that is not one."""
    try:
        spec = np.load(path, allow_pickle=False)
    except (OSError, ValueError, EOFError):
        return None
    ok = (
        spec.dtype == np.complex64
        and spec.ndim == 2
        and MIN_FRAMES <= spec.shape[0] <= MAX_FRAMES
        and spec.shape[1] == HARMONICS
        and np.all(np.isfinite(spec.view(np.float32)))
    )
    return spec if ok else None


def _path(tid: str) -> Path | None:
    with _LOCK:
        dirs = list(_DIRS)
    for d in dirs:
        p = d / f"{tid}.npy"
        if p.is_file():
            return p
    return None


def stack(tid) -> np.ndarray | None:
    """The table's mip stack (mips, frames, 2048), or None."""
    if not valid_id(tid):
        return None
    st = _MEM.get(tid)
    if st is not None:
        return st
    path = _path(tid)
    spec = _read(path) if path else None
    if spec is None:
        return None
    st = core.wavetable_stack(spec)
    _MEM.put(tid, st)
    return st


def info(tid) -> dict | None:
    """{"id", "name", "source", "frames"} of a stored table, or None."""
    path = _path(tid) if valid_id(tid) else None
    if path is None:
        return None
    try:
        meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "id": tid,
        "name": clean_name(meta.get("name")),
        "source": str(meta.get("source", ""))[:120],
        "frames": int(meta.get("frames", 0) or 0),
    }


def listing(directory: Path | str) -> list[dict]:
    """Every table in `directory`, newest first."""
    d = Path(directory)
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.npy"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [i for p in files if valid_id(p.stem) and (i := info(p.stem))]


def register(spec: np.ndarray) -> str:
    """Put a table in the memory store only (tests, the parity corpus)."""
    spec = np.ascontiguousarray(spec, dtype=np.complex64)
    tid = table_id(spec)
    _MEM.put(tid, core.wavetable_stack(spec))
    return tid


# --- makers ----------------------------------------------------------------------


def _mono(audio) -> np.ndarray:
    x = np.asarray(audio, dtype=np.float64)
    return x.mean(axis=1) if x.ndim == 2 else x


def _cycle(seg: np.ndarray, length: int = core._WT_LEN) -> np.ndarray:
    """`seg` read as one cycle `length` long (linear interpolation)."""
    if len(seg) < 2:
        return np.zeros(length)
    at = np.arange(length) * (len(seg) / length)
    return np.interp(at, np.arange(len(seg)), seg)


def from_audio(
    audio, frames: int = 16, mode: str = "slice", sr: int = SR
) -> np.ndarray:
    """A clip as `frames` single cycles across it (the cycles, not yet
    spectra). "slice": the clip cut into equal slices, each one cycle — a
    texture scanned. "pitch": at each of `frames` points across it, one
    period of the clip's own pitch there (YIN, `analysis.voiced`; where it
    hears none, the whole clip's), so a sung or played note becomes its
    own timbre moving."""
    x = _mono(audio)
    frames = int(min(MAX_FRAMES, max(MIN_FRAMES, frames)))
    mode = mode if mode in MODES else "slice"
    if len(x) < 64:
        return np.zeros((frames, core._WT_LEN))
    if mode == "slice":
        edges = np.linspace(0, len(x), frames + 1).astype(int)
        return np.array(
            [_cycle(x[a:b]) for a, b in zip(edges[:-1], edges[1:], strict=True)]
        )
    from .analysis import voiced
    from .sampler import detect_pitch

    hz, _conf = detect_pitch(np.column_stack([x, x]), sr)
    hz = hz if hz and math.isfinite(hz) and hz > 0 else 110.0
    track = voiced.pitch_track(x, sr)
    out = []
    centres = (np.arange(frames) + 0.5) / frames * len(x)
    for c in centres:
        f = hz
        if len(track.notes):
            i = int(
                min(len(track.notes) - 1, max(0, (c - track.span / 2) // track.hop))
            )
            if track.voiced[i] and math.isfinite(track.notes[i]):
                f = 440.0 * 2 ** ((track.notes[i] - 69) / 12)
        period = max(8, int(round(sr / f)))
        a = int(min(max(0, c - period / 2), max(0, len(x) - period)))
        out.append(_cycle(x[a : a + period]))
    return np.array(out)


def parse_drawing(text) -> np.ndarray | None:
    """The editor's post — frames separated by ";", points by "," (each
    -1..1) — as cycles; None for anything that is not 1..DRAW_FRAMES
    frames of 8..DRAW_POINTS finite points."""
    if not isinstance(text, str) or len(text) > 16384:
        return None
    rows = [r for r in text.split(";") if r.strip()]
    if not 1 <= len(rows) <= DRAW_FRAMES:
        return None
    out = []
    for r in rows:
        try:
            pts = [float(v) for v in r.split(",")]
        except ValueError:
            return None
        if not 8 <= len(pts) <= DRAW_POINTS or not all(math.isfinite(v) for v in pts):
            return None
        pts = np.clip(np.asarray(pts), -1.0, 1.0)
        out.append(_cycle(pts))
    return np.array(out)


# --- the live voice --------------------------------------------------------------


def live_payload(tid) -> bytes | None:
    """The table's mip stack as the live voice reads it: a u32 header
    length, a JSON header (space-padded to 4 bytes: frames, mips, len),
    then (mips, frames, len) float32."""
    st = stack(tid)
    if st is None:
        return None
    head = json.dumps(
        {"id": tid, "mips": len(st), "frames": st.shape[1], "len": st.shape[2]},
        separators=(",", ":"),
    ).encode()
    head += b" " * (-len(head) % 4)
    body = np.ascontiguousarray(st, dtype="<f4").tobytes()
    return len(head).to_bytes(4, "little") + head + body


def live_url(p) -> str | None:
    """Where the live voice gets the patch's own table (None: no wavetable
    oscillator reads one, or this machine doesn't hold it — the voice then
    reads "basic", as the engine does)."""
    if "wavetable" not in (p.osc1, p.osc2) or not valid_id(p.wt_custom):
        return None
    return (
        f"/wavetable/live/{p.wt_custom}.bin" if stack(p.wt_custom) is not None else None
    )
