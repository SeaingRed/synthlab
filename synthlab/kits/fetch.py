"""Fetch a sample kit file by file against a committed manifest.

The kit (Naked Drums by Wilkinson Audio, CC-BY 4.0) is about 1.3 GB of FLAC
and is not in the repo and never will be. Its manifest (`naked_drums.json`:
path, size and SHA-256 of every file, at one pinned commit) is. `download`
fetches each missing file over HTTPS from the commit's raw URL into a
per-user directory (`kit_dir`, `SYNTHLAB_KIT_DIR` overrides), checks size
and SHA-256 and only then renames it into place, so a run that stops can
be resumed and a file that is wrong is never used. Modelled on
`synthlab/stems.py`, whose `download` does the same for one model file.
"""

from __future__ import annotations

import email.utils
import hashlib
import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

KIT_ENV = "SYNTHLAB_KIT_DIR"
_CHUNK = 1 << 20
WORKERS = 4  # files fetched at once (~4,262 files: a TLS handshake each)
RETRIES = 5  # attempts a file gets on a throttle, a 5xx or a network fault
BACKOFF = 1.0  # seconds before a file's 2nd attempt, doubling after
MAX_WAIT = 120.0  # seconds a file may spend waiting between its attempts
_sleep = time.sleep  # the tests replace it
_HERE = Path(__file__).parent


class KitError(RuntimeError):
    """A kit cannot be fetched or is wrong; str(err) is for the user."""


class Cancelled(KitError):
    """The download was stopped by the caller."""


@dataclass(frozen=True)
class Kit:
    id: str
    label: str
    base_url: str
    manifest: str  # a json filename beside this module (or an absolute path)


NAKED = Kit(
    "naked-drums",
    "Naked Drums",
    "https://raw.githubusercontent.com/sfzinstruments/"
    "WilkinsonAudio.NakedDrums/407732a548606ac715ad8f3fbac63d9f4df77c0d/",
    "naked_drums.json",
)


def kit_dir(kit: Kit = NAKED) -> Path:
    """Where a kit lives: `SYNTHLAB_KIT_DIR`, else `%LOCALAPPDATA%\\synthlab\\
    kits\\<id>` (non-Windows `~/.cache/synthlab/kits/<id>`). Never in a
    checkout."""
    env = os.environ.get(KIT_ENV)
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "synthlab" / "kits" / kit.id


@lru_cache(maxsize=8)
def _entries(path: str) -> tuple[dict, ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(data["files"])


def manifest(kit: Kit = NAKED) -> list[dict]:
    """Every file of the kit: `path` (forward slashes), `size`, `sha256`."""
    p = Path(kit.manifest)
    return [dict(e) for e in _entries(str(p if p.is_absolute() else _HERE / p))]


def _target(directory: Path, entry: dict) -> Path:
    rel = entry["path"]
    if rel.startswith(("/", "\\")) or ".." in rel.replace("\\", "/").split("/"):
        raise KitError(f"The kit manifest names an unsafe path: {rel}")
    return directory.joinpath(*rel.split("/"))


def missing(kit: Kit = NAKED, directory: Path | None = None) -> list[dict]:
    """Entries whose file is absent or the wrong size (a stat, no hashing)."""
    d = Path(directory or kit_dir(kit))
    out = []
    for e in manifest(kit):
        try:
            ok = _target(d, e).stat().st_size == e["size"]
        except OSError:
            ok = False
        if not ok:
            out.append(e)
    return out


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


_VERIFIED: dict[tuple, bool] = {}  # (path, mtime_ns, size, sha256) -> ok


def _verify_one(path: Path, entry: dict) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_size != entry["size"]:
        return False
    key = (str(path), st.st_mtime_ns, st.st_size, entry["sha256"])
    ok = _VERIFIED.get(key)
    if ok is None:
        ok = file_sha256(path) == entry["sha256"]
        if len(_VERIFIED) > 8192:
            _VERIFIED.clear()  # another thread may clear it too: `ok` holds
        _VERIFIED[key] = ok
    return ok


def verified(kit: Kit = NAKED, directory: Path | None = None) -> bool:
    """Is every file there with its SHA-256 (memoised by path, mtime, size)."""
    d = Path(directory or kit_dir(kit))
    return all(_verify_one(_target(d, e), e) for e in manifest(kit))


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise KitError("The kit download was redirected off HTTPS.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str):
    if not url.lower().startswith("https://"):
        raise KitError("Kit downloads are HTTPS only.")
    return urllib.request.build_opener(_HttpsOnly).open(url, timeout=30)


def _url(kit: Kit, entry: dict) -> str:
    return kit.base_url.rstrip("/") + "/" + urllib.parse.quote(entry["path"])


_PART = re.compile(r"(?P<name>.+)\.[0-9a-f]{32}\.part")


def _sweep(kit: Kit, d: Path) -> None:
    """A killed run's leftovers: this module's own `<file>.<32 hex>.part`
    names, beside a file of the manifest, in the manifest's own folders —
    never another program's `.part` (`SYNTHLAB_KIT_DIR` may be any folder).
    A part another job holds open is left (`OSError`)."""
    names: dict[Path, set[str]] = {}
    for e in manifest(kit):
        t = _target(d, e)
        names.setdefault(t.parent, set()).add(t.name)
    for folder, mine in names.items():
        try:
            listing = list(os.scandir(folder))
        except OSError:
            continue
        for item in listing:
            m = _PART.fullmatch(item.name)
            if m and m["name"] in mine:
                try:
                    os.unlink(item.path)
                except OSError:
                    pass


class _Retry(Exception):
    """One attempt at a file failed in a way a later one may not."""

    def __init__(self, err: BaseException, after: float | None = None):
        super().__init__(str(err))
        self.err, self.after = err, after


def _retry_after(err: urllib.error.HTTPError) -> float | None:
    """A 429's or 503's `Retry-After`, in seconds (a number or a date)."""
    raw = err.headers.get("Retry-After") if err.headers is not None else None
    if not raw:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return float(raw)
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def download(
    kit: Kit = NAKED,
    directory: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    opener: Callable | None = None,
) -> Path:
    """Fetch what the kit is missing into `directory` and return it.

    Each file goes to `<file>.<uuid>.part`, is held to its manifest size and
    SHA-256 and renamed only then; one that does not match is deleted and
    raises `KitError` naming it. Files already right are skipped, so a
    second call resumes. Reads no more than an entry's size whatever the
    server sends. `WORKERS` files are fetched at once; an attempt that
    meets a 429, a 5xx, a network fault or a timeout (or ends early) is
    made again, up to `RETRIES` attempts, after `BACKOFF` doubling — or the
    server's `Retry-After` — while the file's waits stay within `MAX_WAIT`
    seconds; then it is a `KitError` naming the file.
    `progress(done_bytes, total_bytes)` counts the bytes still to fetch
    (one call at a time; a failed attempt's bytes come off again);
    `cancel()` returning True raises `Cancelled`, the files in flight
    stopped and their parts deleted. `opener(url)` returns a readable
    response (the tests hand it bytes)."""
    if not kit.base_url.lower().startswith("https://"):
        raise KitError("Kit downloads are HTTPS only.")
    d = Path(directory or kit_dir(kit))
    opener = opener or _open  # looked up now: the suite replaces it
    if d.is_dir():
        _sweep(kit, d)
    todo = [e for e in manifest(kit) if not _verify_one(_target(d, e), e)]
    total = sum(e["size"] for e in todo)
    lock, stop = threading.Lock(), threading.Event()
    done = [0]
    errors: list[tuple[int, BaseException]] = []

    def moved(n: int) -> None:
        with lock:
            done[0] += n
            if progress and n:
                progress(done[0], total)

    def stopped() -> bool:
        if not stop.is_set() and cancel and cancel():
            stop.set()
        return stop.is_set()

    def work(i: int) -> None:
        if stopped():
            return
        try:
            _fetch(kit, d, todo[i], opener, moved, stopped)
        except BaseException as err:  # re-raised below, on the caller's thread
            with lock:
                errors.append((i, err))
            stop.set()

    if todo:
        with ThreadPoolExecutor(max_workers=min(WORKERS, len(todo))) as pool:
            list(pool.map(work, range(len(todo))))
    # the first file's error in the manifest's order (the pool's is not)
    real = sorted(
        (x for x in errors if not isinstance(x[1], Cancelled)), key=lambda x: x[0]
    )
    if real:
        raise real[0][1]
    if errors or stop.is_set() or (cancel and cancel()):
        raise Cancelled("Download cancelled.")
    return d


def _fetch(kit: Kit, d: Path, e: dict, opener, moved, stopped) -> None:
    """One file, with its retries (`download`)."""
    path = _target(d, e)
    path.parent.mkdir(parents=True, exist_ok=True)
    waited = 0.0
    for attempt in range(RETRIES):
        try:
            _attempt(kit, path, e, opener, moved, stopped)
            return
        except _Retry as r:
            pause = r.after if r.after is not None else BACKOFF * 2**attempt
            if attempt == RETRIES - 1 or waited + pause > MAX_WAIT:
                if isinstance(r.err, KitError):
                    raise r.err from None
                raise KitError(
                    f"The download of {e['path']} failed: {r.err}"
                ) from r.err
            waited += pause
            while pause > 0:  # in short steps: a cancel is heard while waiting
                if stopped():
                    raise Cancelled("Download cancelled.") from None
                _sleep(min(pause, 0.25))
                pause -= 0.25


def _attempt(kit: Kit, path: Path, e: dict, opener, moved, stopped) -> None:
    """One attempt at one file: into its part, checked, renamed in."""
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.part")
    h, got = hashlib.sha256(), 0
    try:
        try:
            with opener(_url(kit, e)) as resp, open(tmp, "wb") as out:
                while chunk := resp.read(min(_CHUNK, e["size"] + 1 - got)):
                    got += len(chunk)
                    if got > e["size"]:
                        raise KitError(f"{e['path']} is larger than it should be.")
                    h.update(chunk)
                    out.write(chunk)
                    moved(len(chunk))
                    if stopped():
                        raise Cancelled("Download cancelled.")
                out.flush()
                os.fsync(out.fileno())
        except urllib.error.HTTPError as err:
            if err.code == 429 or err.code >= 500:
                raise _Retry(err, _retry_after(err)) from err
            raise KitError(f"The download of {e['path']} failed: {err}") from err
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,
        ) as err:  # refused, dropped or stalled: another attempt may do
            raise _Retry(err) from err
        except OSError as err:  # the disk: no second attempt helps
            raise KitError(f"The download of {e['path']} failed: {err}") from err
        if got < e["size"]:
            raise _Retry(KitError(f"{e['path']} ended early; it was deleted."))
        if h.hexdigest() != e["sha256"]:
            raise KitError(
                f"{e['path']} did not match its pinned SHA-256; it was deleted."
            )
        os.replace(tmp, path)
    except BaseException:
        moved(-got)  # this attempt's bytes come off the count
        raise
    finally:
        tmp.unlink(missing_ok=True)


def availability(kit: Kit = NAKED, directory: Path | None = None) -> dict:
    """What a button needs: `ready` (every file there at its size), `megabytes`
    still to fetch, and `reason` — "" when ready, else the sentence to show.
    Size only (a stat a file): the SHA-256 is held when `download` writes a
    file and by `verified`, never hashed per request here."""
    left = missing(kit, directory)
    mb = round(sum(e["size"] for e in left) / 1e6)
    if not left:
        return {"ready": True, "megabytes": 0, "reason": ""}
    return {
        "ready": False,
        "megabytes": mb,
        "reason": f"{kit.label} is not downloaded yet ({mb} MB to fetch).",
    }
