"""The clip store: named recorded/bounced audio under studio_data/clips/.

The one storage primitive the recording features build on:
bounces, promoted voice takes, drum one-shots and jam captures all land
here as <name>.wav + <name>.meta.json (bpm, bars, source, content hash).
Rendered previews only ever lived in bounded LRU caches before this —
nothing a musician made on the bench could be kept as audio.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from ..audio import load as load_wav
from ..audio import save as save_wav
from ..core import SR
from .files import free_name, rename_file, safe_name
from .limits import (
    MAX_CLIP_SECONDS,  # re-exported: existing importers unchanged
    MAX_STORED_CLIP_SECONDS,
)
from .lru import LRU


def clip_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


def clip_safe(name: str) -> str:
    """The clip store's own sanitizer: `safe_name` with this store's
    fallback word ("clip" for a degenerate name, not the shared
    "untitled"). Public on purpose — every route that turns
    a raw clip name into a path (`/clip/{name}.wav`,
    `/song/take`) must agree with `save_clip` on what a
    degenerate name becomes, or a clip saved under the fallback name is
    unreachable by the same fallback a different call site computed."""
    return safe_name(name, fallback="clip")


def _file_sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


# (path, mtime_ns, size) -> content hash. Every render key that places a
# clip hashes it, and a song's plan keys every section twice: reading each
# take's bytes again for each key was O(sections^2) reads under the lock.
_HASHES: LRU[str] = LRU(512)


def clip_hash(data_dir: Path, name: str) -> str:
    """Content hash for cache keys — a re-recorded clip must invalidate
    every stem that placed it. Memoized by the file's (path, mtime_ns,
    size), and `save_clip` hashes what it wrote (a same-length rewrite
    within one timer tick keeps both)."""
    path = clip_dir(data_dir) / f"{clip_safe(name)}.wav"
    try:
        st = path.stat()
    except OSError:
        return "missing"
    found = _HASHES.get(_hash_key(path, st))
    if found is None:
        found = _file_sha1(path)
        _HASHES.put(_hash_key(path, st), found)
    return found


def _hash_key(path: Path, st) -> str:
    return f"{path.resolve()}|{st.st_mtime_ns}|{st.st_size}"


def free_clip_name(data_dir: Path, name: str, claimed=frozenset()) -> str:
    """`name` (sanitized) when no clip holds it, else "name 2", "name 3", ...
    `save_clip` writes over whatever has the name, so a second bounce called
    "bounce" (the default) silently changed the audio of a clip already
    placed in the song. Names are compared case-folded, as Windows'
    filesystem sees them, against every clip and stray meta in the store
    plus `claimed` (case-folded names the caller has spoken for: a bundle's
    own, `projectzip.install`) — `files.free_name`, as loops' names are."""
    return free_name(
        clip_dir(data_dir), name, clip_safe, (".wav", ".meta.json"), claimed
    )


# Files kept beside a clip, by its name, outside clips/ — so writing one
# never moves the clip store's listing (what the clip library's and the
# hub's watches read): studio_data/<kind>/<clip>.json. `delete_clip` and
# `rename_clip` carry them with the clip.
TAKE_NOTES = "takenotes"  # a keys take's notes (keytakes.py)
SIDECARS = (TAKE_NOTES,)


def sidecar(data_dir: Path, kind: str, name: str) -> Path:
    """Where the `kind` sidecar (`SIDECARS`) of the clip `name` lives; its
    folder is made when it is first written."""
    return Path(data_dir) / kind / f"{clip_safe(name)}.json"


def _meta_path(data_dir: Path, name: str) -> Path:
    return clip_dir(data_dir) / f"{clip_safe(name)}.meta.json"


def write_meta(data_dir: Path, name: str, meta: dict) -> None:
    """A clip's meta, written whole: `meta` with the clip's own name."""
    payload = {**meta, "name": clip_safe(name)}
    _meta_path(data_dir, name).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def read_meta(data_dir: Path, name: str) -> dict | None:
    """A clip's meta through `clean_meta`, or None when there is no such
    clip (no WAV: a meta alone describes nothing) or its meta is not
    readable."""
    if not (clip_dir(data_dir) / f"{clip_safe(name)}.wav").exists():
        return None
    try:
        raw = json.loads(_meta_path(data_dir, name).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return clean_meta(raw) if isinstance(raw, dict) else None


def save_clip(
    data_dir: Path,
    name: str,
    audio: np.ndarray,
    sr: int = SR,
    meta: dict | None = None,
    cap: float = MAX_CLIP_SECONDS,
) -> Path:
    """Write a clip and its meta, cut at `cap` s (a clip's 30; the booth's
    treated results `limits.MAX_TREATED_SECONDS`, a free take's
    `limits.MAX_FREE_TAKE_SECONDS`: never past `MAX_STORED_CLIP_SECONDS`)."""
    n = int(min(cap, MAX_STORED_CLIP_SECONDS) * sr)
    audio = np.asarray(audio, dtype=np.float64)[:n]
    safe = clip_safe(name)
    path = clip_dir(data_dir) / f"{safe}.wav"
    save_wav(str(path), audio, sr=sr, bit_depth=16)
    # hashed now: a rewrite of the same length within one timer tick keeps
    # (mtime_ns, size), and the memo would answer with the old content's hash
    _HASHES.put(_hash_key(path, path.stat()), _file_sha1(path))
    write_meta(data_dir, safe, {"name": safe, "sr": sr, **(meta or {})})
    return path


def load_clip(data_dir: Path, name: str) -> tuple[np.ndarray, int] | None:
    path = clip_dir(data_dir) / f"{clip_safe(name)}.wav"
    if not path.exists():
        return None
    return load_wav(str(path))


def delete_clip(data_dir: Path, name: str) -> None:
    (clip_dir(data_dir) / f"{clip_safe(name)}.wav").unlink(missing_ok=True)
    _meta_path(data_dir, name).unlink(missing_ok=True)
    for kind in SIDECARS:
        sidecar(data_dir, kind, name).unlink(missing_ok=True)


def _move(src: Path, dst: Path) -> None:
    """A file that goes with the clip, to its new name. A `dst` that is
    not `src` itself (a case-only rename) belongs to no clip — `dst`'s
    clip is free or IS this one — and goes first: `Path.rename` onto it
    raised FileExistsError on Windows after the audio had already moved."""
    if dst.exists() and not (src.exists() and src.samefile(dst)):
        dst.unlink()
    if src.exists():
        rename_file(src, dst)


def rename_clip(data_dir: Path, old: str, new: str) -> bool:
    """Move a clip (audio + meta) to a new name. False — nothing touched —
    when the clip doesn't exist, the name is unchanged, or `new` is taken
    by a genuinely different clip. A rename that only changes case
    (`jam` -> `Jam`) used to be refused outright: `dst.exists()` alone
    can't tell a real collision from the same file under a different case
    on a case-insensitive filesystem, where `samefile` can.

    A `<new>.meta.json` with no `<new>.wav` beside it describes no clip (a
    crash between the two writes, a hand-deleted WAV): it is removed
    before anything moves. `Path.rename` onto it raised FileExistsError on
    Windows *after* the audio had already moved, leaving the clip renamed
    with its meta behind; and when the clip had no meta of its own, the
    stray would have become the renamed clip's bpm/bars. The same holds
    for its sidecars (`SIDECARS`), which move with it."""
    d = clip_dir(data_dir)
    old, new = clip_safe(old), clip_safe(new)
    if old == new:
        return False
    src, dst = d / f"{old}.wav", d / f"{new}.wav"
    if not src.exists():
        return False
    if dst.exists() and not src.samefile(dst):
        return False
    meta_src, meta_dst = _meta_path(data_dir, old), _meta_path(data_dir, new)
    # `dst` is free or IS `src` (case-only), so a `meta_dst` that is not
    # `meta_src` itself belongs to no clip
    if meta_dst.exists() and not (meta_src.exists() and meta_src.samefile(meta_dst)):
        meta_dst.unlink()
    rename_file(src, dst)
    if meta_src.exists():
        try:
            payload = json.loads(meta_src.read_text(encoding="utf-8"))
        except (ValueError, RecursionError):
            payload = {}
        rename_file(meta_src, meta_dst)
        write_meta(data_dir, new, payload if isinstance(payload, dict) else {})
    for kind in SIDECARS:
        _move(sidecar(data_dir, kind, old), sidecar(data_dir, kind, new))
    return True


# What the studio writes into a clip's meta, by type: `save_clip`'s own
# name/sr, a bounce's bpm/bars/source, a generated take's settings
# (`routes/song_record._transition_clip`, compared whole to re-place the
# same clip). A meta is a file anyone can edit and a bundle can carry, so
# both readers — `list_clips` and `projectzip`'s import — hold it to these.
# "at": when a keys take was taken; its "kept" (a bool) is read through
# keytakes.is_take / is_kept, and its notes are a sidecar (`TAKE_NOTES`:
# a take from before them kept "notes" here, read through keytakes.notes_of)
META_NUMBERS = ("sr", "bpm", "bars", "seconds", "at")
META_STRINGS = ("name", "source")
# "lab": where a Sample Lab region came from (routes/lab.py lab_send);
# "vocoder": what made a kept Voice-lab result (voice.result_meta);
# "tune": how the clip hub tuned the clip it was kept from (cliphub.tuned_meta);
# "booth": what the Voice page's booth treated and how (booth.result_meta)
META_SETTINGS = ("transition", "lab", "vocoder", "tune", "booth")


def meta_number(v) -> bool:
    """A finite, sane number (never a bool). Bounds rather than
    `math.isfinite`, which raises OverflowError on a 400-digit int."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and -1e12 < v < 1e12


def clean_meta(meta) -> dict:
    """A clip meta with every known key of the wrong type dropped (a
    non-object is no meta at all). Templates do arithmetic on the numbers
    (`c.bpm | round | int`): a `"bpm": "fast"` on disk was a 500 on every
    page that drew the clip shelf, the one holding its delete button
    included. (The clip library that replaced it is on every page:
    `cliplib.py` holds what is inside `lab` to the same rule.)"""
    if not isinstance(meta, dict):
        return {}
    out = dict(meta)
    for k in META_NUMBERS:
        if k in out and not meta_number(out[k]):
            del out[k]
    for k in META_STRINGS:
        if k in out and not isinstance(out[k], str):
            del out[k]
    for k in META_SETTINGS:
        if k in out and not isinstance(out[k], dict):
            del out[k]
    return out


def list_clips(data_dir: Path) -> list[dict]:
    out = []
    for p in sorted(clip_dir(data_dir).glob("*.wav")):
        meta_path = p.with_suffix("").with_name(p.stem + ".meta.json")
        meta: dict = {}
        try:
            meta = clean_meta(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, RecursionError):
            pass
        # the file is the clip: its name and length are never the meta's word
        out.append({**meta, "name": p.stem, "seconds": _seconds(p)})
    return out


def seconds(data_dir: Path, name: str) -> float:
    """A clip's length from its WAV header (0.0 when it is not there)."""
    return _seconds(clip_dir(data_dir) / f"{clip_safe(name)}.wav")


def _seconds(path: Path) -> float:
    import wave

    try:
        with wave.open(str(path)) as w:
            return round(w.getnframes() / w.getframerate(), 2)
    except (OSError, wave.Error):
        return 0.0
