"""The one filename sanitizer the studio uses: clip names, presets, drum
patterns, panel presets — and the store helpers built
on it: a free name in a folder (`free_name`), a rename that can change
only the case on Windows (`rename_file`), a folder's listing as a watch
value (`dir_sig`).

Before this, `clips.py` and `state.py` each carried their own copy — and
`clips._safe` was missing the Windows reserved-device-name guard the
preset one had, so a clip literally named "CON" or "NUL" could be handed
to `open()` on Windows and hang or fail in ways that have nothing to do
with a bad clip name. One sanitizer, one guard, used everywhere a name
becomes part of a path.
"""

from __future__ import annotations

import os
import re
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path

RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_name(raw: str, fallback: str = "untitled", extra: str = "") -> str:
    """A free-text name, made safe to use as a filename stem: a character
    whitelist, trimmed leading/trailing dots and spaces (Windows drops
    them), capped at 80 chars, and never a reserved device name (CON, NUL,
    COM1, ...) — checked against the part before the first '.', so
    "con.txt" is caught exactly like "con" (Windows treats both as the
    same device). Empty or entirely-reserved input falls back to
    `fallback`. `extra`: characters a store also allows (the loop store's
    "·" and "#": `loops.loop_safe`) — none a path or a device name reads."""
    allow = re.escape(extra) if extra else ""
    safe = re.sub(rf"[^A-Za-z0-9 _().!{allow}-]", "_", raw).strip(" .")[:80]
    while safe.lower().endswith(".meta"):
        safe = safe[: -len(".meta")].rstrip(" .") or fallback
    if not safe or safe.split(".", 1)[0].upper() in RESERVED:
        return fallback
    return safe


def free_name(
    folder: Path,
    name: str,
    sanitise: Callable[[str], str],
    suffixes: Iterable[str],
    claimed=frozenset(),
) -> str:
    """`name` (through `sanitise`) when no file in `folder` holds it, else
    "name 2", "name 3", ... A stem is taken by a file with any of
    `suffixes` (a clip's WAV or a stray meta, `clips.free_clip_name`; a
    loop's JSON, `loops.free_name`) or by `claimed` (case-folded names the
    caller has spoken for). Compared case-folded, as Windows' filesystem
    sees them ("BOUNCE" is "bounce" there)."""
    taken = set(claimed)
    for suffix in suffixes:
        taken |= {
            p.name[: -len(suffix)].casefold() for p in Path(folder).glob(f"*{suffix}")
        }
    safe = sanitise(name)
    if safe.casefold() not in taken:
        return safe
    for k in range(2, 10_000):
        cand = sanitise(f"{safe[:72]} {k}")
        if cand.casefold() not in taken:
            return cand
    raise OSError(f"no free name for {name!r}")


def rename_file(src: Path, dst: Path) -> None:
    """`src.rename(dst)`, tolerant of a same-file case-only rename: on
    Windows' case-insensitive filesystem `dst` can already BE `src`
    (`jam.wav` -> `Jam.wav`), and renaming a path onto itself is a no-op,
    so the case never actually changes without going through a temp name
    first (minor #6)."""
    if src != dst and dst.exists() and src.samefile(dst):
        tmp = src.with_name(f".{src.name}.rename-tmp")
        src.replace(tmp)  # a temp left by an interrupted rename is ours
        src = tmp
    src.rename(dst)


def dir_sig(folder: Path, suffix: str = "") -> tuple:
    """Which files (ending in `suffix`) sit in `folder`, with their mtime
    and size: a listing's signature without reading a file — the regions'
    watches, and the stores that cache what they read by it."""
    try:
        with os.scandir(folder) as it:
            stats = [(e.name, e.stat()) for e in it if e.name.endswith(suffix)]
    except OSError:
        return ()
    return tuple(sorted((n, st.st_mtime_ns, st.st_size) for n, st in stats))


# Every zip the studio writes (the stems, the project bundle) gives its
# entries this date — the zip format's own epoch — instead of the wall
# clock, so the same content is always the same bytes. `writestr(name, ..)`
# stamps "now" at 2 s resolution: two exports of one song differed from one
# tick to the next. Nothing reads an entry's date back.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def zip_write(z: zipfile.ZipFile, name: str, data: bytes | str, compress=None) -> None:
    """`z.writestr(name, data)` with nothing of the moment or the machine
    in the entry: the fixed date, a fixed creating system (0, what Windows
    writes; elsewhere zipfile says 3) and the mode `writestr` gives a
    name (`0o600`). `compress` overrides the archive's compression for
    this entry (`zipfile.ZIP_STORED` for audio)."""
    info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
    info.compress_type = z.compression if compress is None else compress
    info.create_system = 0
    info.external_attr = 0o600 << 16
    z.writestr(info, data)
