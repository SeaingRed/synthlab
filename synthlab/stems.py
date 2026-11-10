"""Stem separation: a song into drums, bass, other and vocals.

The model is HT-Demucs (Rouard, Massa, Défossez 2023) as a single ONNX
graph, run by ONNX Runtime on the CPU. Not PyTorch — its Windows-on-ARM
wheels stop at Python 3.12 and this project needs 3.13 — and not the
`demucs-onnx` package, whose `soxr` dependency has no Windows-on-ARM wheel
either. What is left to do around the graph is small and is all here:

- **The graph** takes exactly `SEGMENT` samples (7.8 s) of stereo 44.1 kHz
  float32 as `mix` (1, 2, n) and returns `stems` (1, 4, 2, n) in the order
  `SOURCES`. The STFT, the inverse STFT and the model's own per-segment
  normalisation are inside it.
- **`separate`** does the rest, as Demucs' own `separate.py` does: the whole
  track is normalised by its mono mean and deviation (and the stems scaled
  back — the mean, a DC offset that belongs to no stem, is not added back
  to each as Demucs does, so the stems sum to the mix less its DC), cut
  into segments a quarter overlapped, and the outputs laid back
  down under a window that fades linearly across each overlap — so a chunk
  join is a crossfade between two predictions of the same audio, never a
  seam. It takes the model as a plain function, which is what the tests
  hand it: an identity "model" must come back exact.
- **The model file** is not in the repo and never will be (316 MB). It is
  downloaded on first use into a per-user cache (`cache_dir`,
  `SYNTHLAB_MODEL_DIR` overrides) from one pinned HTTPS URL — a commit
  hash, not a moving `main` — and is only ever used after its SHA-256
  matched the one pinned here (`verify`). A file that fails is deleted.
- **onnxruntime is optional** (the `stems` extra) and imported only inside
  `OnnxSeparator`. `availability()` says, in a sentence a button can show,
  what is missing.

Nothing here knows about the studio: the Lab's job, its progress and its
cache of finished stems live in `synthlab/studio/labjobs.py`.
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .core import SR

SOURCES = ("drums", "bass", "other", "vocals")
SEGMENT = 343_980  # samples the graph takes: 7.8 s at 44.1 kHz
OVERLAP = 0.25  # of a segment, crossfaded with its neighbour
MODEL_ENV = "SYNTHLAB_MODEL_DIR"
THREADS_ENV = "SYNTHLAB_STEMS_THREADS"
MAX_MODEL_BYTES = 400_000_000  # no model this module names is larger
_CHUNK = 1 << 20


class StemsError(RuntimeError):
    """Separation cannot run or did not finish; str(err) is for the user."""


class Cancelled(StemsError):
    """The caller's `cancel()` said stop."""


@dataclass(frozen=True)
class Model:
    """A separation model file: where it comes from and what it must be."""

    id: str  # names the stems made with it (a cache key, a folder name)
    url: str
    sha256: str
    size: int
    filename: str
    sources: tuple[str, ...] = SOURCES
    segment: int = SEGMENT
    label: str = ""  # what a menu calls it


# https://huggingface.co/adowu/htdemucs-onnx — the standard (non fine-tuned)
# 4-stem htdemucs, exported by StemSplit's demucs-onnx pipeline. Model card:
# MIT, "matching the original HT-Demucs" (facebookresearch/demucs, MIT). The
# URL names a commit, so the file behind it cannot change; the hash is the
# repo's own LFS sha256, checked again on every download.
HTDEMUCS = Model(
    id="htdemucs-e6b688a9",
    url=(
        "https://huggingface.co/adowu/htdemucs-onnx/resolve/"
        "e6b688a9a9f977e2efaba79d0f18b9b0cfc606ea/htdemucs.onnx"
    ),
    sha256="68d0bf16428ef66e692cdff8a9ccf28f1ef3f69440d57e58605a4cc55fcc5e74",
    size=316_446_953,
    filename="htdemucs-e6b688a9.onnx",
    label="4 stems: drums, bass, vocals, other",
)
# https://huggingface.co/StemSplitio/htdemucs-6s-onnx — `htdemucs_6s`, the
# six-source HT-Demucs (guitar and piano split out of "other"), from the
# same exporter as the file above and with the same graph interface: `mix`
# (1, 2, 343980) in, `stems` (1, 6, 2, 343980) out, STFT inside. Model
# card: MIT, "matching the original HT-Demucs". Pinned to the publisher's
# own repo at a commit, with the repo's LFS sha256. Upstream calls this
# model experimental and its piano stem weak; measured here
# (`tools/lab_measure.py --model 6`), not assumed.
HTDEMUCS_6S = Model(
    id="htdemucs6s-49df9b69",
    url=(
        "https://huggingface.co/StemSplitio/htdemucs-6s-onnx/resolve/"
        "49df9b6989cf2150840ea65b0bef77a2e471b678/htdemucs_6s.onnx"
    ),
    sha256="48f8e84945579f8ab340e083339e9221e03785dbe733a52c388200b6d3ca779a",
    size=258_159_781,
    filename="htdemucs6s-49df9b69.onnx",
    sources=("drums", "bass", "other", "vocals", "guitar", "piano"),
    label="6 stems: + guitar, piano",
)
MODELS: dict[str, Model] = {m.id: m for m in (HTDEMUCS, HTDEMUCS_6S)}
"""Every model a stem set may have been made with, by id."""


# --- the model file ----------------------------------------------------------


def cache_dir() -> Path:
    """Where model files live: `SYNTHLAB_MODEL_DIR`, else the user's cache
    dir (`%LOCALAPPDATA%\\synthlab\\models`, `~/Library/Caches/...`,
    `$XDG_CACHE_HOME` or `~/.cache`). Never inside a checkout."""
    env = os.environ.get(MODEL_ENV)
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "synthlab" / "models"


def model_path(model: Model = HTDEMUCS, directory: Path | None = None) -> Path:
    return Path(directory or cache_dir()) / model.filename


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


_VERIFIED: dict[tuple, bool] = {}  # (path, mtime_ns, size, sha256) -> ok


def verify(path: Path, model: Model = HTDEMUCS) -> bool:
    """Is `path` exactly the model: its size, then its SHA-256. Memoised by
    the file's stat, so asking before every separation costs one `stat`
    after the first 0.3 s hash."""
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_size != model.size:
        return False
    key = (str(path), st.st_mtime_ns, st.st_size, model.sha256)
    if key not in _VERIFIED:
        if len(_VERIFIED) > 32:
            _VERIFIED.clear()
        _VERIFIED[key] = file_sha256(path) == model.sha256
    return _VERIFIED[key]


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Follow a redirect (the hub answers with one, to its CDN) only to
    another https URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise StemsError("The model download was redirected off HTTPS.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str):
    if not url.lower().startswith("https://"):
        raise StemsError("Model downloads are HTTPS only.")
    opener = urllib.request.build_opener(_HttpsOnly)
    return opener.open(url, timeout=30)


def download(
    model: Model = HTDEMUCS,
    directory: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    opener: Callable | None = None,
) -> Path:
    """Fetch `model` into the cache and return its path — or return at once
    when a verified copy is already there.

    Written to a temp name beside the target and renamed only after its
    size and SHA-256 matched, so an interrupted or tampered download is
    never the model: it is deleted and this raises `StemsError`. Reads no
    more than `model.size` bytes whatever the server sends. `progress(done,
    total)` is called about once a megabyte; `cancel()` returning True
    stops it (`Cancelled`). `opener(url)` returns a readable response —
    the tests hand it a local file."""
    path = model_path(model, directory)
    if verify(path, model):
        return path
    opener = opener or _open  # looked up now: the suite replaces it
    if model.size > MAX_MODEL_BYTES:
        raise StemsError("That model is larger than this build accepts.")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.part")
    h, done = hashlib.sha256(), 0
    try:
        try:
            with opener(model.url) as resp, open(tmp, "wb") as out:
                while chunk := resp.read(_CHUNK):
                    done += len(chunk)
                    if done > model.size:
                        raise StemsError(
                            "The model download is larger than it should be."
                        )
                    h.update(chunk)
                    out.write(chunk)
                    if progress:
                        progress(done, model.size)
                    if cancel and cancel():
                        raise Cancelled("Download cancelled.")
                out.flush()
                os.fsync(out.fileno())
        except OSError as err:  # URLError, a dropped connection, a full disk
            raise StemsError(f"The model download failed: {err}") from err
        if done != model.size or h.hexdigest() != model.sha256:
            raise StemsError(
                "The downloaded model did not match its pinned SHA-256; it "
                "was deleted. Nothing was installed."
            )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    if not verify(path, model):  # re-read from disk: what will be loaded
        path.unlink(missing_ok=True)
        raise StemsError("The model file changed while it was being saved.")
    return path


def runtime():
    """The `onnxruntime` module, or `StemsError` naming what to install."""
    try:
        import onnxruntime
    except (ImportError, OSError) as err:  # OSError: its DLL failed to load
        raise StemsError(
            "Stem separation needs the onnxruntime package, which is not "
            "installed here (uv sync --all-extras, or --extra stems)."
        ) from err
    return onnxruntime


def availability(model: Model = HTDEMUCS, directory: Path | None = None) -> dict:
    """What a Separate button needs to know: `runtime` (is onnxruntime
    importable), `model` (is a verified model file in the cache),
    `megabytes` to download when it is not, and `reason` — "" when
    separation can start, else the sentence to show."""
    megabytes = round(model.size / 1e6)
    try:
        runtime()
    except StemsError as err:
        return {
            "runtime": False,
            "model": False,
            "megabytes": megabytes,
            "reason": str(err),
        }
    have = verify(model_path(model, directory), model)
    return {"runtime": True, "model": have, "megabytes": megabytes, "reason": ""}


def threads() -> int:
    """Intra-op threads for the session: half the logical CPUs, at most 8,
    at least 2 (`SYNTHLAB_STEMS_THREADS` overrides). Hybrid x86 CPUs are
    P+E — measured on an i9-14900K (8 P + 16 E, 32
    logical): 8 threads 1.38 s a segment, 16 threads 1.62 s, 24 threads
    1.47 s — and whatever is left keeps the studio answering."""
    env = os.environ.get(THREADS_ENV, "")
    if env.isdigit() and int(env) > 0:
        return min(int(env), 64)
    return max(2, min(8, (os.cpu_count() or 4) // 2))


class OnnxSeparator:
    """The model as `separate` wants it: `run(chunk)` for one segment."""

    def __init__(self, model: Model = HTDEMUCS, directory: Path | None = None):
        ort = runtime()
        path = model_path(model, directory)
        if not verify(path, model):
            raise StemsError("The separation model is not downloaded yet.")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads()
        opts.inter_op_num_threads = 1
        # No graph optimisation. Measured on an i9-14900K: any optimisation
        # level peaks at 4.7 GB while the session loads (constant folding)
        # and 5.2 GB running; with none it is 0.6 GB loaded and ~2.2 GB
        # running, the same output to 3e-7, and a segment takes 1.38 s
        # instead of 1.17 s. A studio that takes 5 GB to split a song is
        # not one a 16 GB laptop can keep open. (The memory arena stays on:
        # without it the job peaked at 1.9 GB but took a third longer.)
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        try:
            self.session = ort.InferenceSession(
                str(path), sess_options=opts, providers=["CPUExecutionProvider"]
            )
        except Exception as err:  # a corrupt graph, an unsupported opset
            raise StemsError(f"The separation model failed to load: {err}") from err
        self.model = model
        self.sources = model.sources
        self.segment = model.segment
        self.provider = self.session.get_providers()[0]

    def run(self, chunk: np.ndarray) -> np.ndarray:
        """(2, segment) float32 -> (sources, 2, segment) float32."""
        return self.session.run(["stems"], {"mix": chunk[None]})[0][0]


# --- chunked inference -------------------------------------------------------


def chunk_starts(total: int, segment: int = SEGMENT, overlap: float = OVERLAP):
    """Where each segment starts: a stride of segment less the overlap,
    enough of them to cover `total` samples."""
    lap = int(segment * overlap)
    stride = segment - lap
    count = max(1, -(-max(total - lap, 1) // stride))
    return [i * stride for i in range(count)], lap


def _window(length: int, lap: int, first: bool, last: bool) -> np.ndarray:
    """A segment's weight: 1, fading linearly over the overlap at each end
    that meets a neighbour (the track's own ends stay at full weight — a
    fade there would divide the first sample by nothing)."""
    w = np.ones(length, dtype=np.float32)
    if lap > 0:
        ramp = (np.arange(1, lap + 1, dtype=np.float32)) / (lap + 1)
        if not first:
            w[:lap] = ramp[:length]
        if not last:
            w[length - lap :] = ramp[::-1]  # never the last: a full segment
    return w


def separate(
    mix: np.ndarray,
    run: Callable[[np.ndarray], np.ndarray],
    *,
    sources: int = len(SOURCES),
    segment: int = SEGMENT,
    overlap: float = OVERLAP,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> np.ndarray:
    """`mix` (n, 2) at 44.1 kHz -> stems (sources, n, 2) float32.

    `run(chunk)` is the model on one segment: (2, segment) float32 in,
    (sources, 2, segment) out. The track is normalised as a whole, fed in
    overlapping segments (the last one zero-padded), and each output is
    added under `_window`; dividing by the summed weights makes every
    overlap a crossfade and everything else the model's own output.
    `progress(done, total)` once per segment; `cancel()` True raises
    `Cancelled` before the next one."""
    x = np.asarray(mix, dtype=np.float32)
    if x.ndim == 1:
        x = np.column_stack([x, x])
    if x.ndim != 2 or x.shape[1] != 2:
        raise StemsError("Separation takes stereo audio: (n, 2).")
    total = len(x)
    if total == 0:
        return np.zeros((sources, 0, 2), dtype=np.float32)
    ref = x.mean(axis=1)
    mean, std = float(ref.mean()), float(ref.std())
    if std < 1e-8:  # silence: every stem is silence
        return np.zeros((sources, total, 2), dtype=np.float32)
    x = ((x - mean) / std).T  # (2, n), contiguous per channel below
    out = np.zeros((sources, 2, total), dtype=np.float32)
    weight = np.zeros(total, dtype=np.float32)
    starts, lap = chunk_starts(total, segment, overlap)
    for i, start in enumerate(starts):
        if cancel and cancel():
            raise Cancelled("Separation cancelled.")
        end = min(start + segment, total)
        chunk = np.zeros((2, segment), dtype=np.float32)
        chunk[:, : end - start] = x[:, start:end]
        got = np.asarray(run(chunk), dtype=np.float32)
        if got.shape != (sources, 2, segment):
            raise StemsError(f"The model returned {got.shape}, not stems.")
        w = _window(end - start, lap, i == 0, i == len(starts) - 1)
        out[:, :, start:end] += got[:, :, : end - start] * w
        weight[start:end] += w
        if progress:
            progress(i + 1, len(starts))
    out /= np.maximum(weight, 1e-8)
    out *= std
    return out.transpose(0, 2, 1)


def sdr(reference: np.ndarray, estimate: np.ndarray) -> float:
    """Signal-to-distortion ratio in dB: the reference's energy over the
    energy of (reference - estimate). The plain definition — no allowed
    filtering or scaling, unlike BSS-eval's — so it reads lower than the
    numbers a paper quotes."""
    ref = np.asarray(reference, dtype=np.float64)
    err = ref - np.asarray(estimate, dtype=np.float64)
    num, den = float(np.sum(ref**2)), float(np.sum(err**2))
    if num <= 0:
        return float("-inf")
    return 10.0 * float(np.log10(num / max(den, 1e-20)))


__all__ = [
    "HTDEMUCS",
    "SEGMENT",
    "SOURCES",
    "SR",
    "Cancelled",
    "Model",
    "OnnxSeparator",
    "StemsError",
    "availability",
    "cache_dir",
    "download",
    "model_path",
    "sdr",
    "separate",
    "threads",
    "verify",
]
