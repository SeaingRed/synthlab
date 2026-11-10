"""Append-only JSONL log of every sound you audition, and a 2D map of it.

Each row: {"id", "t", "patch", "features", "parents"}, plus "sound" (the
layers, main FX and curves) for a layered breed card. The 2D projection
is PCA over standardized feature vectors — plain NumPy SVD, no sklearn.
A corrupt trailing line (crash mid-write) is skipped, never fatal.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from ..analysis.features import Features
from ..synth import Patch

# The log is append-only, so without a cap it grows for the life of the
# project: one real session reached 592 rows / 892 KB, and every map render
# re-read, re-parsed and re-SVD'd all of it. We keep the most recent
# MAX_ROWS and drop older ones — comfortably more than the breeder's five
# generations of eight can reference, so no live tile id is ever cut.
MAX_ROWS = 2000
# rewriting on every add past the cap would mean a full read+write per
# audition; let the file drift this far over first, then compact once
_COMPACT_SLACK = 1.25


class SpaceStore:
    """The log on disk plus an in-memory copy of its rows and an id index.

    One store lives for the studio's lifetime (`StudioService.space_store`),
    so a lookup is a dict hit: it used to re-read and re-parse the whole
    file on every `get` (79 ms at 2,400 rows), from a new store per call.
    The copy is keyed by the file's (mtime, size) and re-read whenever
    those change under it (another process, a hand edit, a restore);
    `add` and `compact` keep it current themselves. Thread-safe: the
    breeder appends from one request while another looks tiles up."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._rows: list[dict] | None = None
        self._index: dict[str, dict] = {}
        self._sig: tuple[int, int] | None = None
        self._count: int | None = None

    def _stat(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return st.st_mtime_ns, st.st_size

    def _parse(self) -> list[dict]:
        out = []
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # crash-truncated line: skip, never fatal
                    if not isinstance(row, dict) or "id" not in row:
                        continue  # valid JSON but no id: unusable, skip
                    out.append(row)
        return out

    def _load(self) -> list[dict]:
        """The rows, re-read only when the file changed since they were
        (call under `_lock`)."""
        sig = self._stat()
        if self._rows is None or sig != self._sig:
            self._rows = self._parse()
            self._index = {row["id"]: row for row in self._rows}
            self._sig = sig
            self._count = None  # recounted from disk on the next add
        return self._rows

    def compact(self, max_rows: int = MAX_ROWS) -> int:
        """Trim the log to its most recent `max_rows`. Returns rows kept."""
        with self._lock:
            rows = self._load()
            if len(rows) <= max_rows:
                return len(rows)
            keep = rows[-max_rows:]
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for row in keep:
                    f.write(json.dumps(row) + "\n")
            tmp.replace(self.path)  # atomic: a crash leaves the old log intact
            self._rows = list(keep)
            self._index = {row["id"]: row for row in self._rows}
            self._sig = self._stat()
            self._count = len(keep)
            return len(keep)

    def _row_count(self) -> int:
        """Rows on disk, counted once then tracked in memory. Re-reading the
        file on every add would make writing the log quadratic."""
        if self._count is None:
            if not self.path.exists():
                self._count = 0
            else:
                with open(self.path, "rb") as f:
                    self._count = sum(1 for line in f if line.strip())
        return self._count

    def add(
        self,
        patch: Patch,
        features: Features,
        parents: Sequence[str] = (),
        sound: dict | None = None,
    ) -> str:
        """`sound`: a layered breed card's format-2 extras (its layers,
        main FX and curves — `studio/genome.row_sound`), written beside
        `patch` only when there is one, so a plain row is as it always was
        and every reader of `patch` (the map, the tiles) keeps working."""
        row_id = uuid.uuid4().hex[:12]
        patch_dict = patch.to_json()
        row = {
            "id": row_id,
            "t": time.time(),
            "patch": patch_dict,
            "features": features.to_dict(),
            "parents": list(parents),
        }
        if sound is not None:
            row["sound"] = sound
        with self._lock:
            # the in-memory rows stay good only if nothing else changed the
            # file since they were read; otherwise the next read reloads
            current = self._rows is not None and self._stat() == self._sig
            count = self._row_count()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            if current:
                assert self._rows is not None
                self._rows.append(row)
                self._index[row_id] = row
                self._sig = self._stat()
            else:
                self._rows = None
            self._count = count + 1
            if self._count > MAX_ROWS * _COMPACT_SLACK:
                self.compact()
        return row_id

    def rows(self) -> list[dict]:
        with self._lock:
            return list(self._load())

    def get(self, row_id: str) -> dict | None:
        with self._lock:
            self._load()
            return self._index.get(row_id)

    def project_2d(
        self,
        limit: int | None = None,
        extra: dict | None = None,
        background: Sequence[dict] = (),
    ) -> list[dict]:
        """`limit` keeps only the most recent N rows — the map plots a bounded
        number of points however long the log gets, and the PCA is fitted to
        what's actually drawn. `extra`: one more row (never stored), placed by
        the same fit and returned last — the map's "you are here" when the
        sound on Synths was never logged; the logged rows land exactly where
        they would without it. `background`: rows that are not the log's
        (the shipped sounds: the map's faint field) fitted WITH
        the logged ones — one space, steady as the log grows — and returned
        before them."""
        rows = self.rows()
        if limit is not None and len(rows) > limit:
            rows = rows[-limit:]
        rows = [*background, *rows]
        if not rows:
            return []
        vecs, kept = [], []
        for row in rows:
            try:
                vecs.append(Features(**row["features"]).vector())
                kept.append(row)
            except TypeError:
                continue  # written by an older/newer schema: skip, never fatal
        rows = kept
        if not rows:
            return []
        vecs = np.array(vecs)
        place = None
        if extra is not None:
            try:
                place = np.array(Features(**extra["features"]).vector())
            except (TypeError, KeyError):
                place = None
        if len(rows) < 2:
            coords = np.zeros((len(rows), 2))
            here = np.zeros(2)
        else:
            mean = vecs.mean(axis=0)
            std = vecs.std(axis=0)
            std[std < 1e-9] = 1.0
            x = (vecs - mean) / std
            _, _, vt = np.linalg.svd(x, full_matrices=False)
            coords = x @ vt[:2].T
            here = ((place - mean) / std) @ vt[:2].T if place is not None else None
            if coords.shape[1] < 2:  # fewer features than 2 can't happen,
                coords = np.column_stack([coords, np.zeros(len(rows))])
        out = [
            {**row, "x": float(cx), "y": float(cy)}
            for row, (cx, cy) in zip(rows, coords, strict=True)
        ]
        if place is not None and here is not None and len(here) >= 2:
            out.append({**extra, "x": float(here[0]), "y": float(here[1])})
        return out
