"""The source store: whole songs brought into the Sample Lab.

A third audio store, beside clips and samples, because a source is neither:
those are capped at 30 s and their content is hashed into render cache
keys; a source is minutes long, is never rendered by the engine, and
appears in no key, no `state.json` and no project export. Things *made*
from a source — a clip, a sampler sound — are copies in those stores, and
travel as they always did.

    <data_dir>/sources/<id>/meta.json            name, length, analysis, edits
                           /mix.flac             stereo 16-bit at SR
                           /mix.peaks.npy        the waveform overview (peak)
                           /mix.rms.npy          ... and its body (RMS)
                           /stems-<model>/<stem>.flac   once separated

- **Content-addressed** like the sampler: the id is the first 16 hex of the
  SHA-1 of the decoded 16-bit PCM, so the same song uploaded twice is one
  source and its stems are found again.
- **The upload is hostile.** It is decoded by libsndfile (`soundfile`:
  WAV, FLAC, MP3, OGG) from memory, in bounded blocks: the header's frame
  count is never trusted (an MP3's is an estimate, a crafted WAV's a lie),
  so reading stops at `MAX_SOURCE_SECONDS` whatever it says. Any decode
  failure is a `SourceError` whose text is a toast; none is a 500.
- **Writes are atomic**: a file under a temp name, then `os.replace`; a
  stem set in a temp folder, then one rename. `sweep` (run when a studio
  opens the store) deletes whatever temp is left from a crash, so a
  half-written stem set is never mistaken for a finished one.
- **`meta.json` is a file anyone can edit**: `clean_meta` holds every value
  to its type and range before anything draws or computes with it.

The store locks itself (`_LOCK`); nothing here touches `StudioState`.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import struct
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path

import numpy as np

from .. import sampler, stems
from ..analysis.key import MODES, NOTE_NAMES
from ..core import SR
from . import labclean, limits
from .clips import clip_safe
from .files import safe_name

MIX = "mix"
_LOCK = threading.RLock()
_BLOCK = 1 << 16  # frames per decode read
_PEAK_COLUMNS = 1200
STEM_NAMES = stems.SOURCES  # the four every stem set holds
# every stem any model makes: the four, then what a larger model adds
ALL_STEMS = tuple(dict.fromkeys(s for m in stems.MODELS.values() for s in m.sources))
# the keys a user may name a source's key as (the Lab's override menu)
KEY_NAMES = tuple(f"{n} {m}" for m in MODES for n in NOTE_NAMES)


class SourceError(ValueError):
    """An upload or a store operation refused; str(err) is the toast."""


valid_id = sampler.valid_id


def stem_names(model: str) -> tuple[str, ...]:
    """The stems a set made with `model` holds, in the model's order (the
    four of `STEM_NAMES` for a model this build does not know: a stem
    folder from another version is still four files if it is anything)."""
    known = stems.MODELS.get(model)
    return known.sources if known else STEM_NAMES


def _sf():
    """`soundfile`, or a `SourceError` saying it is missing (the `studio`
    extra carries it; the engine never needs it)."""
    try:
        import soundfile
    except (ImportError, OSError) as err:
        raise SourceError(
            "Importing audio needs the soundfile package (uv sync --all-extras)."
        ) from err
    return soundfile


def available() -> bool:
    """Can this machine import audio at all (is `soundfile` there)."""
    try:
        _sf()
    except SourceError:
        return False
    return True


def sources_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "sources"
    d.mkdir(parents=True, exist_ok=True)
    return d


def source_dir(data_dir: Path, sid: str) -> Path | None:
    """The source's folder, or None for anything that is not an id (the
    one check every path into the store passes through)."""
    if not valid_id(sid):
        return None
    return sources_dir(data_dir) / sid


# --- decoding ----------------------------------------------------------------


def decode(data: bytes) -> np.ndarray:
    """An uploaded file's audio as the store keeps it: stereo float32 at
    `SR`, peak at most 1. Raises `SourceError` for anything libsndfile
    cannot read, for no audio, an unsupported rate, or more than
    `MAX_SOURCE_SECONDS`."""
    sf = _sf()
    try:
        with sf.SoundFile(io.BytesIO(data)) as f:
            sr, channels = int(f.samplerate), int(f.channels)
            if not limits.MIN_SR <= sr <= limits.MAX_SR:
                raise SourceError(f"Unsupported sample rate: {sr} Hz.")
            if not 1 <= channels <= limits.MAX_SOURCE_CHANNELS:
                raise SourceError(f"Unsupported channel count: {channels}.")
            cap = int(limits.MAX_SOURCE_SECONDS * sr)
            blocks, total = [], 0
            while True:
                block = f.read(_BLOCK, dtype="float32", always_2d=True)
                if not len(block):
                    break
                total += len(block)
                if total > cap:
                    minutes = limits.MAX_SOURCE_SECONDS / 60
                    raise SourceError(
                        f"That file is longer than {minutes:g} minutes: trim it first."
                    )
                blocks.append(block[:, :2])
    except SourceError:
        raise
    except Exception as err:  # libsndfile's errors, a truncated file, MemoryError
        raise SourceError(
            "Couldn't read that file as audio (WAV, FLAC, MP3 and OGG work)."
        ) from err
    if not blocks:
        raise SourceError("That file holds no audio.")
    x = np.concatenate(blocks)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    if sr != SR:
        from scipy.signal import resample_poly

        g = math.gcd(SR, sr)
        x = resample_poly(x, SR // g, sr // g, axis=0).astype(np.float32)
        x = x[: int(limits.MAX_SOURCE_SECONDS * SR)]
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 1.0:  # an MP3 decodes past full scale; FLAC would clip it
        x = x / peak
    if peak < 1e-4:
        raise SourceError("That file is silent.")
    return np.ascontiguousarray(x, dtype=np.float32)


def _pcm(x: np.ndarray) -> np.ndarray:
    return np.round(np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2")


def content_id(audio: np.ndarray) -> str:
    h = hashlib.sha1(f"{audio.shape}".encode())
    h.update(_pcm(audio).tobytes())
    return h.hexdigest()[:16]


# --- files -------------------------------------------------------------------


def _write_flac(path: Path, audio: np.ndarray) -> None:
    sf = _sf()
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        sf.write(str(tmp), _pcm(audio), SR, format="FLAC", subtype="PCM_16")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _num(v, lo: float, hi: float, default):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    if not -1e12 < v < 1e12:  # nan, inf, a 400-digit int
        return default
    return float(min(max(v, lo), hi))


def _text(v, limit: int, default: str = "") -> str:
    return v[:limit] if isinstance(v, str) else default


def level_db(v) -> float:
    """A stem's level as the store keeps it: dB within `LAB_LEVEL_DB_RANGE`
    to a tenth, 0 (as separated) for anything that is not a number. Form
    and query values arrive as text."""
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            return 0.0
    return round(_num(v, *limits.LAB_LEVEL_DB_RANGE, 0.0), 1) + 0.0  # never -0.0


def _clean_levels(raw) -> dict:
    """`{model id: {stem: dB}}`, sparse: only the models this build knows,
    only their own stems, only levels that are not 0 dB — so a meta
    written before levels existed and one never touched are the same."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for model in stems.MODELS:
        given = raw.get(model) if isinstance(raw.get(model), dict) else {}
        kept = {s: level_db(given.get(s)) for s in stem_names(model) if s in given}
        kept = {s: db for s, db in kept.items() if db}
        if kept:
            out[model] = kept
    return out


def levels(meta: dict) -> dict[str, float]:
    """The level (dB) of every stem of the set the source shows; {} with
    no stems. A stem never moved is 0."""
    model = meta["stems"]["model"]
    if not model:
        return {}
    mine = meta.get("levels", {}).get(model, {})
    return {s: mine.get(s, 0.0) for s in stem_names(model)}


def set_level(meta: dict, stem: str, db) -> None:
    """Set one stem's level in the set the source shows (clamped)."""
    model = meta["stems"]["model"]
    if model and stem in stem_names(model):
        meta.setdefault("levels", {}).setdefault(model, {})[stem] = level_db(db)


_CHOP_ID = re.compile(r"[0-9a-f]{4,12}")


def _clean_chop(c, seconds: float) -> dict | None:
    """One chop held to type and range, or None when it is not one: no id,
    no span inside the source, shorter than `MIN_CHOP_SECONDS`."""
    if not isinstance(c, dict):
        return None
    cid = c.get("id")
    if not isinstance(cid, str) or not _CHOP_ID.fullmatch(cid):
        return None
    start = _num(c.get("start"), 0.0, seconds, None)
    end = _num(c.get("end"), 0.0, seconds, None)
    if start is None or end is None or end - start < limits.MIN_CHOP_SECONDS - 1e-6:
        return None
    # its own mix: the stem set it names, that set's stems, their levels
    model = c.get("model") if isinstance(c.get("model"), str) else ""
    given = c.get("stems") if isinstance(c.get("stems"), list) else []
    names = stem_names(model) if model in stems.MODELS else ()
    picked = [s for s in names if s in given]
    lv = c.get("levels") if isinstance(c.get("levels"), dict) else {}
    lv = {s: level_db(lv.get(s)) for s in picked if s in lv}
    clip = c.get("clip") if isinstance(c.get("clip"), str) else ""
    name = " ".join(_text(c.get("name"), 400).split())[: limits.MAX_CHOP_NAME_CHARS]
    return {
        "id": cid,
        "name": name or "chop",
        "start": round(start, 4),
        "end": round(end, 4),
        "stems": picked,
        "model": model if picked else "",
        "levels": {s: db for s, db in lv.items() if db},
        "clean": labclean.clean_settings(c.get("clean")),
        # what cleaning last did, in words (written when it is run)
        "notes": _text(c.get("notes"), 240),
        # the clip it was kept as ("" = not kept): a `clip_safe` name
        "clip": clip if clip and clip == clip_safe(clip) else "",
    }


def _clean_chops(raw, seconds: float) -> list[dict]:
    """A source's chops in time order: each held to type and range, ids
    unique, none overlapping one before it, `MAX_LAB_CHOPS` at most. Only
    the first few hundred entries of a hostile list are looked at."""
    raw = raw if isinstance(raw, list) else []
    seen: set[str] = set()
    found = []
    for c in raw[: limits.MAX_LAB_CHOPS * 4]:
        chop = _clean_chop(c, seconds)
        if chop is not None and chop["id"] not in seen:
            seen.add(chop["id"])
            found.append(chop)
    out: list[dict] = []
    for chop in sorted(found, key=lambda c: (c["start"], c["end"])):
        if out and chop["start"] < out[-1]["end"] - 1e-6:
            continue  # overlaps the one before it
        out.append(chop)
    return out[: limits.MAX_LAB_CHOPS]


def clean_meta(raw, sid: str) -> dict:
    """A meta as the Lab may use it: every known key held to its type and
    range, everything else dropped. The audio's own length is never the
    meta's word (`seconds` is refreshed from the file when it is read)."""
    raw = raw if isinstance(raw, dict) else {}
    an = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    region = raw.get("region") if isinstance(raw.get("region"), dict) else {}
    stems = raw.get("stems") if isinstance(raw.get("stems"), dict) else {}
    view = raw.get("view") if isinstance(raw.get("view"), dict) else {}
    seconds = _num(raw.get("seconds"), 0.0, limits.MAX_SOURCE_SECONDS, 0.0)
    lo, hi = limits.LAB_BPM_RANGE
    scores = an.get("key_scores") if isinstance(an.get("key_scores"), list) else []
    picked = raw.get("picked") if isinstance(raw.get("picked"), list) else []
    chops = _clean_chops(raw.get("chops"), seconds)
    return {
        "id": sid,
        "name": safe_name(_text(raw.get("name"), 200), "song")[
            : limits.MAX_SOURCE_NAME_CHARS
        ],
        "seconds": seconds,
        "added": _num(raw.get("added"), 0.0, 1e11, 0.0),
        "opened": _num(raw.get("opened"), 0.0, 1e11, 0.0),
        "analysis": {
            "bpm": _num(an.get("bpm"), 0.0, hi, 0.0),
            "confidence": _num(an.get("confidence"), 0.0, 1.0, 0.0),
            "beat_offset": _num(an.get("beat_offset"), 0.0, 60.0, 0.0),
            "downbeat_offset": _num(an.get("downbeat_offset"), 0.0, 60.0, 0.0),
            "meter_guess": 3
            if an.get("meter_guess") == 3
            else 4
            if an.get("meter_guess") == 4
            else 0,
            "meter_sure": _num(an.get("meter_sure"), 0.0, 1.0, 0.0),
            "key": _text(an.get("key"), 20),
            "key_confidence": _num(an.get("key_confidence"), 0.0, 1.0, 0.0),
            "key_scores": [
                [_text(s[0], 20), _num(s[1], -1.0, 1.0, 0.0)]
                for s in scores[:4]
                if isinstance(s, (list, tuple)) and len(s) == 2
            ],
            "key_from": _text(an.get("key_from"), 20, MIX),
            "key_margin": _num(an.get("key_margin"), -2.0, 2.0, 0.0),
            "key_tonic": "bass" if an.get("key_tonic") == "bass" else "",
        },
        # what the user set: None = follow the analysis
        "user": {
            "bpm": _num(user.get("bpm"), lo, hi, None),
            "offset": _num(user.get("offset"), 0.0, 60.0, None),
            "key": user.get("key") if user.get("key") in KEY_NAMES else None,
        },
        "region": {
            "mode": "seconds" if region.get("mode") == "seconds" else "bars",
            "start": _num(region.get("start"), 0.0, limits.MAX_SOURCE_SECONDS, 0.0),
            "length": _num(region.get("length"), 0.0, limits.MAX_SOURCE_SECONDS, 4.0),
        },
        # the part of the song the waveform shows (length 0 = all of it)
        "view": {
            "start": _num(view.get("start"), 0.0, limits.MAX_SOURCE_SECONDS, 0.0),
            "length": _num(view.get("length"), 0.0, limits.MAX_SOURCE_SECONDS, 0.0),
        },
        # the stems the region plays and sends ([] = the mix)
        "picked": [s for s in ALL_STEMS if s in picked],
        # each stem's level in what the region plays and sends, in dB, per
        # stem set (0 dB is not stored): never a render input
        "levels": _clean_levels(raw.get("levels")),
        # named spans of the song (labchops.py), in time order, and the one
        # the region was last set from ("" = none)
        "chops": chops,
        "chop": raw.get("chop") if raw.get("chop") in [c["id"] for c in chops] else "",
        "stems": {
            "model": safe_name(_text(stems.get("model"), 60), "")
            if stems.get("model")
            else "",
            "seconds": _num(stems.get("seconds"), 0.0, 1e6, 0.0),
        },
    }


def read_meta(data_dir: Path, sid: str) -> dict | None:
    """The source's cleaned meta, or None when there is no such source
    (no folder, no mix, or a meta that is not JSON)."""
    d = source_dir(data_dir, sid)
    if d is None or not (d / f"{MIX}.flac").is_file():
        return None
    try:
        raw = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    meta = clean_meta(raw, sid)
    if not stem_dir_ok(d, meta["stems"]["model"]):
        meta["stems"] = {"model": "", "seconds": 0.0}
    have = stem_names(meta["stems"]["model"]) if meta["stems"]["model"] else ()
    meta["picked"] = [s for s in meta["picked"] if s in have]
    return meta


def write_meta(data_dir: Path, meta: dict) -> None:
    d = source_dir(data_dir, meta.get("id", ""))
    if d is None or not d.is_dir():
        return
    with _LOCK:
        _write_json(d / "meta.json", clean_meta(meta, meta["id"]))


def update_meta(data_dir: Path, sid: str, change) -> dict | None:
    """Read, `change(meta)`, write — one step under the store's lock, so a
    job finishing and a form posting never lose each other's edit."""
    with _LOCK:
        meta = read_meta(data_dir, sid)
        if meta is None:
            return None
        change(meta)
        write_meta(data_dir, meta)
        return read_meta(data_dir, sid)


def list_sources(data_dir: Path) -> list[dict]:
    """Every source, most recently opened first."""
    out = []
    for d in sources_dir(data_dir).iterdir():
        if d.is_dir() and valid_id(d.name):
            meta = read_meta(data_dir, d.name)
            if meta is not None:
                out.append(meta)
    return sorted(out, key=lambda m: (-m["opened"], m["id"]))


def store_bytes(data_dir: Path) -> int:
    total = 0
    for p in sources_dir(data_dir).rglob("*"):
        try:
            total += p.stat().st_size if p.is_file() else 0
        except OSError:
            pass
    return total


def sweep(data_dir: Path) -> int:
    """Remove what a crash left behind: temp files, temp stem folders, and
    folders with no mix. Returns how many it removed. Run once when a
    studio opens the store — never while a job is writing."""
    removed = 0
    with _LOCK:
        root = sources_dir(data_dir)
        for d in list(root.iterdir()):
            if not d.is_dir():
                continue
            for p in list(d.iterdir()):
                if ".tmp" in p.name:
                    shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(
                        missing_ok=True
                    )
                    removed += 1
            if valid_id(d.name) and not (d / f"{MIX}.flac").is_file():
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    return removed


def add(data_dir: Path, data: bytes, filename: str, analyse=None) -> dict:
    """Decode an upload and keep it. Returns the source's meta — the
    existing one when this audio is already in the store (then only its
    "opened" time moves). `analyse(audio) -> dict` fills `analysis`.
    Raises `SourceError` (the toast) when the file cannot be read or the
    store is full."""
    audio = decode(data)
    sid = content_id(audio)
    with _LOCK:
        if read_meta(data_dir, sid) is not None:
            return update_meta(data_dir, sid, lambda m: m.update(opened=time.time()))  # type: ignore[return-value]
        if len(list_sources(data_dir)) >= limits.MAX_SOURCES:
            raise SourceError(
                f"The Lab holds {limits.MAX_SOURCES} songs: delete one first."
            )
        if store_bytes(data_dir) + len(audio) * 2 > limits.MAX_SOURCES_BYTES:
            raise SourceError("The Lab's disk budget is full: delete a song first.")
    analysis = analyse(audio) if analyse else {}
    now = time.time()
    meta = {
        "name": Path(filename.replace("\\", "/")).stem if filename else "song",
        "seconds": len(audio) / SR,
        "added": now,
        "opened": now,
        "analysis": analysis,
    }
    with _LOCK:
        d = sources_dir(data_dir) / sid
        d.mkdir(parents=True, exist_ok=True)
        _write_flac(d / f"{MIX}.flac", audio)
        _write_json(d / "meta.json", clean_meta(meta, sid))
        _save_peaks(d, MIX, audio)
    return read_meta(data_dir, sid)  # type: ignore[return-value]


def delete(data_dir: Path, sid: str) -> bool:
    d = source_dir(data_dir, sid)
    if d is None or not d.is_dir():
        return False
    root = sources_dir(data_dir).resolve()
    if d.resolve().parent != root:  # a link out of the store is not ours
        return False
    with _LOCK:
        shutil.rmtree(d, ignore_errors=True)
    return True


# --- stems -------------------------------------------------------------------


def _stem_dir(d: Path, model: str) -> Path:
    return d / f"stems-{model}"


def stem_dir_ok(d: Path, model: str) -> bool:
    """A finished stem set: its folder, with every stem in it."""
    if not model:
        return False
    sd = _stem_dir(d, model)
    return all((sd / f"{s}.flac").is_file() for s in stem_names(model))


def save_stems(data_dir: Path, sid: str, model: str, stems: dict, seconds: float):
    """Keep a finished separation: every stem written into a temp folder,
    which becomes `stems-<model>` in one rename, and only then named in the
    meta. `stems` maps each of the model's stems (`stem_names`) to (n, 2)
    audio."""
    d = source_dir(data_dir, sid)
    model = safe_name(model, "")
    if d is None or not d.is_dir() or not model:
        raise SourceError("That song is gone.")
    names = stem_names(model)
    if set(stems) != set(names):
        raise SourceError("The separator returned the wrong stems.")
    tmp = d / f"stems-{model}.tmp-{uuid.uuid4().hex}"
    tmp.mkdir()
    try:
        for name in names:
            _write_flac(tmp / f"{name}.flac", stems[name])
            _save_peaks(tmp, name, stems[name])
        with _LOCK:
            final = _stem_dir(d, model)
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            # Windows refuses the rename while a scanner or a reader still
            # holds one of the files just written: it lets go within moments
            for attempt in range(20):
                try:
                    os.replace(tmp, final)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.05)
            update_meta(
                data_dir,
                sid,
                lambda m: m.update(stems={"model": model, "seconds": seconds}),
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def models_on_disk(data_dir: Path, sid: str) -> tuple[str, ...]:
    """The ids of the stem sets a source holds, finished, in `stems.MODELS`
    order (a source keeps the 4-stem and the 6-stem set once both are made)."""
    d = source_dir(data_dir, sid)
    if d is None or not d.is_dir():
        return ()
    return tuple(m for m in stems.MODELS if stem_dir_ok(d, m))


def with_model(meta: dict, model: str) -> dict:
    """`meta` as if `model`'s stem set were the one it shows: what `read`
    and `read_sum` take to read a set that is on disk but not current (a
    chop keeps the set it was made from). Nothing is written."""
    if model == meta["stems"]["model"]:
        return meta
    return {**meta, "stems": {**meta["stems"], "model": model}}


def stem_path(data_dir: Path, sid: str, name: str, model: str = "") -> Path | None:
    """The stored FLAC of a source's mix (`name` "mix") or of one stem of
    `model`'s set (default: the set the source shows) — None for a bad id,
    a set that is not on disk, or a stem that set does not have. The one
    way a download reaches a path."""
    meta = read_meta(data_dir, sid)
    d = source_dir(data_dir, sid)
    if meta is None or d is None:
        return None
    if name == MIX:
        return d / f"{MIX}.flac"
    model = model or meta["stems"]["model"]
    # a model this build knows, or the one the (cleaned) meta itself names:
    # nothing else is ever joined into a path
    known = model in stems.MODELS or model == meta["stems"]["model"]
    if not model or not known or not stem_dir_ok(d, model):
        return None
    if name not in stem_names(model):
        return None
    return _stem_dir(d, model) / f"{name}.flac"


def wav_blocks(path: Path):
    """(the byte length, an iterator of its bytes): a stored FLAC as a
    16-bit PCM WAV, decoded and handed over a block at a time — a
    ten-minute stem is 100 MB as WAV and is never whole in memory. None
    when the file cannot be opened."""
    try:
        info = _sf().info(str(path))
        frames, sr, channels = (
            int(info.frames),
            int(info.samplerate),
            int(info.channels),
        )
    except Exception:  # a file damaged on disk is a 404, not a 500
        return None
    data = frames * channels * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sr,
        sr * channels * 2,
        channels * 2,
        16,
        b"data",
        data,
    )

    def blocks():
        yield header
        left = frames
        with _sf().SoundFile(str(path)) as f:
            while left > 0:
                block = f.read(min(_BLOCK, left), dtype="int16", always_2d=True)
                if not len(block):
                    break
                left -= len(block)
                yield block.astype("<i2").tobytes()
        while left > 0:  # the file ended early: the length promised is kept
            yield bytes(min(_BLOCK, left) * channels * 2)
            left -= _BLOCK

    return len(header) + data, blocks()


def stem_sets(data_dir: Path) -> list[dict]:
    """Every separated source with the stem sets it holds — the one listing
    of downloadable stems (the clip library's Stems group is drawn from it,
    `cliplib.stem_sets`): `{"id", "name", "seconds", "model" (the set it
    shows), "sets": {model id: (stem names)}}`, most recently opened first.
    A set is listed exactly when `stem_path` serves it: a finished set of a
    model this build knows, or the set the meta itself names (one made by
    another build). Read-only."""
    out = []
    for meta in list_sources(data_dir):
        models = models_on_disk(data_dir, meta["id"])
        shown = meta["stems"]["model"]  # on disk: `read_meta` checked it
        if shown and shown not in models:
            models += (shown,)
        sets = {m: stem_names(m) for m in models}
        if sets:
            out.append(
                {
                    "id": meta["id"],
                    "name": meta["name"],
                    "seconds": meta["seconds"],
                    "model": shown,
                    "sets": sets,
                }
            )
    return out


def _path(data_dir: Path, meta: dict, stem: str) -> Path | None:
    d = source_dir(data_dir, meta["id"])
    if d is None:
        return None
    if stem == MIX:
        return d / f"{MIX}.flac"
    if meta["stems"]["model"] and stem in stem_names(meta["stems"]["model"]):
        return _stem_dir(d, meta["stems"]["model"]) / f"{stem}.flac"
    return None


def read(
    data_dir: Path, meta: dict, stem: str = MIX, start: int = 0, frames: int = -1
) -> np.ndarray:
    """`frames` frames of a source's mix or stem from `start` (samples at
    SR), as float32 (n, 2); the whole file by default. Only the span asked
    for is decoded. Empty when there is nothing there."""
    path = _path(data_dir, meta, stem)
    empty = np.zeros((0, 2), dtype=np.float32)
    if path is None or not path.is_file():
        return empty
    try:
        with _sf().SoundFile(str(path)) as f:
            if f.samplerate != SR or f.channels != 2:
                return empty
            start = min(max(0, int(start)), f.frames)
            f.seek(start)
            return f.read(int(frames), dtype="float32", always_2d=True)
    except Exception:  # a file damaged on disk reads as nothing, not a 500
        return empty


def read_sum(
    data_dir: Path,
    meta: dict,
    stems: list[str],
    start: int = 0,
    frames: int = -1,
    levels: dict | None = None,
) -> np.ndarray:
    """The mix (no stems named), one stem, or several summed — "everything
    but the vocals" is three of them. `levels` (stem -> dB, missing = 0)
    weights each stem; the mix itself has no level. With every level at 0
    the sum is the plain one, bit for bit. A boosted sum that passes full
    scale is scaled down to it, not clipped (the store's rule for a decode
    that does: a clip is 16-bit)."""
    model = meta["stems"]["model"]
    names = [s for s in stem_names(model) if s in stems] if model else []
    if not names:
        return read(data_dir, meta, MIX, start, frames)
    parts = [read(data_dir, meta, s, start, frames) for s in names]
    n = min(len(p) for p in parts)
    gains = [10.0 ** (level_db((levels or {}).get(s)) / 20.0) for s in names]
    if all(g == 1.0 for g in gains):
        return np.sum([p[:n] for p in parts], axis=0, dtype=np.float32)
    parts = [p[:n] * np.float32(g) for p, g in zip(parts, gains, strict=True)]
    out = np.sum(parts, axis=0, dtype=np.float32)
    peak = float(np.max(np.abs(out))) if n else 0.0
    if peak > 1.0 and max(gains) > 1.0:
        out = out / np.float32(peak)
    return out


def _save_peaks(d: Path, stem: str, audio: np.ndarray) -> None:
    cols = _columns(audio)
    for kind, arr in (("peaks", cols[0]), ("rms", cols[1])):
        tmp = d / f"{stem}.{kind}.{uuid.uuid4().hex}.tmp.npy"
        np.save(tmp, arr)
        os.replace(tmp, d / f"{stem}.{kind}.npy")


def _columns(audio: np.ndarray, n: int = _PEAK_COLUMNS) -> np.ndarray:
    """(2, n) float32: each column's peak of |x| (the louder channel) and
    its RMS (the channels' mean square) — a lane draws the peak as the
    waveform's outline and the RMS as its solid body."""
    if not len(audio):
        return np.zeros((2, n), np.float32)
    a = np.asarray(audio, dtype=np.float32)
    m = np.max(np.abs(a), axis=1)
    sq = np.mean(a * a, axis=1)
    edges = np.linspace(0, len(m), n + 1).astype(int)
    out = np.zeros((2, n), np.float32)
    for i, (lo, hi) in enumerate(zip(edges, edges[1:], strict=False)):
        if hi > lo:
            out[0, i] = m[lo:hi].max()
            out[1, i] = math.sqrt(float(sq[lo:hi].mean()))
    return out


def _peak_columns(audio: np.ndarray) -> np.ndarray:
    return _columns(audio)[0]


@lru_cache(maxsize=64)
def _window_columns(path: str, stamp: int, start: int, frames: int) -> np.ndarray:
    """A span of a stored file as (2, `_PEAK_COLUMNS`): peak and RMS.
    Cached by the file's path and mtime: a zoomed page redraws five lanes
    on every post, and decoding 90 s of five FLACs each time would be a
    second a click."""
    try:
        with _sf().SoundFile(path) as f:
            f.seek(min(max(0, start), f.frames))
            audio = f.read(frames, dtype="float32", always_2d=True)
    except Exception:  # a file damaged on disk draws flat, not a 500
        return np.zeros((2, _PEAK_COLUMNS), dtype=np.float32)
    return np.clip(np.nan_to_num(_columns(audio)), 0.0, 1.0)


def window_peaks(
    data_dir: Path, meta: dict, stem: str, start: float, length: float
) -> np.ndarray:
    """The peaks of `length` seconds from `start` — the zoomed waveform:
    read from the audio itself, since the stored peaks are 1200 columns of
    the whole song (zoomed x16 that is 75 of them)."""
    return window_columns(data_dir, meta, stem, start, length)[0]


def window_columns(
    data_dir: Path, meta: dict, stem: str, start: float, length: float
) -> np.ndarray:
    """(2, `_PEAK_COLUMNS`): the peak and RMS columns of `length` seconds
    from `start`, read from the audio (zeros for a stem not on disk)."""
    path = _path(data_dir, meta, stem)
    if path is None or not path.is_file():
        return np.zeros((2, _PEAK_COLUMNS), dtype=np.float32)
    first, frames = int(round(start * SR)), max(1, int(round(length * SR)))
    return _window_columns(str(path), path.stat().st_mtime_ns, first, frames)


def peaks(data_dir: Path, meta: dict, stem: str = MIX) -> np.ndarray:
    """The stem's waveform overview: `_PEAK_COLUMNS` peaks of |x|, 0..1,
    written beside the audio when it was stored (zeros when missing)."""
    return _stored(data_dir, meta, stem, "peaks")


def _stored(data_dir: Path, meta: dict, stem: str, kind: str) -> np.ndarray:
    path = _path(data_dir, meta, stem)
    flat = np.zeros(_PEAK_COLUMNS, dtype=np.float32)
    if path is None:
        return flat
    try:
        pk = np.load(path.with_name(f"{stem}.{kind}.npy"), allow_pickle=False)
    except (OSError, ValueError):
        return flat
    if pk.shape != (_PEAK_COLUMNS,) or pk.dtype.kind != "f":
        return flat
    return np.clip(np.nan_to_num(pk), 0.0, 1.0)


def overview(data_dir: Path, meta: dict, stem: str = MIX) -> np.ndarray:
    """(2, `_PEAK_COLUMNS`): the whole stem's peak and RMS columns, as
    stored. A source stored before the RMS was kept has it worked out once,
    from the audio, and written beside the peaks (`<stem>.rms.npy`)."""
    pk = peaks(data_dir, meta, stem)
    path = _path(data_dir, meta, stem)
    if path is None or not path.is_file():
        return np.stack([pk, pk])
    if not path.with_name(f"{stem}.rms.npy").is_file():
        cols = window_columns(data_dir, meta, stem, 0.0, meta["seconds"])
        with _LOCK:
            tmp = path.with_name(f"{stem}.rms.{uuid.uuid4().hex}.tmp.npy")
            try:
                np.save(tmp, cols[1])
                os.replace(tmp, path.with_name(f"{stem}.rms.npy"))
            except OSError:  # a read-only store still draws
                return cols
    return np.stack([pk, _stored(data_dir, meta, stem, "rms")])
