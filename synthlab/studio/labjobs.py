"""The Sample Lab's background job: download the separation model if it is
not there yet, separate one source, keep its stems.

- **One at a time.** A separation holds about 1.5 GB and most of the P-cores
  for a minute or two; `start` refuses a second with "busy" rather than
  queueing it.
- **Off the request thread, outside the studio lock.** The job is a daemon
  thread that never touches `StudioState`: it reads the source store
  (self-locking), runs the model, and writes the stems through
  `sources.save_stems` (a temp folder, one rename, then the meta). A
  studio killed mid-job leaves only a temp folder, which `sources.sweep`
  removes when the next one starts — so a stem set is either whole or
  absent.
- **Progress** goes two ways: `events.Task` (the toast line on every open
  page: a step per megabyte downloaded, then per segment separated) and
  `state()` (what the Lab's own status line polls, so it works with no
  event stream too). `last` keeps how the previous job ended.
- **Stems are cached** per (source id, model id) on disk; `start` on a
  source that already has them answers "done" without running — also when
  they were made earlier with the *other* model and the source has since
  been separated again: the meta is pointed back at the folder that is
  still there.
- **Two models** (`stems.MODELS`): the 4-stem one and the 6-stem one that
  adds guitar and piano. `start` takes the one to use; `self.model` is the
  model of the job that is running (or ran last), and the three seams
  below read it.

`make_separator` and `fetch_model` are the two seams the tests replace: a
fake separator behind the same `run / sources / segment` interface, and no
network.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from .. import stems
from ..core import SR
from . import lab, sources
from .events import EventBus, Task

log = logging.getLogger("synthlab.studio")


class LabJobs:
    def __init__(self, data_dir: Path, bus: EventBus | None = None):
        self.data_dir = Path(data_dir)
        self.bus = bus
        self.model = stems.HTDEMUCS
        self.model_dir: Path | None = None  # None = stems.cache_dir()
        self._lock = threading.Lock()
        self._job: dict | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.last: dict | None = None  # {"source", "error", "at"}: how the last ended
        try:
            sources.sweep(self.data_dir)
        except OSError:
            log.exception("lab: sweeping the source store failed")

    # -- the seams the tests replace -----------------------------------------
    def availability(self) -> dict:
        return stems.availability(self.model, self.model_dir)

    def fetch_model(self, progress, cancel) -> None:
        stems.download(self.model, self.model_dir, progress=progress, cancel=cancel)

    def make_separator(self):
        return stems.OnnxSeparator(self.model, self.model_dir)

    # -- the job -------------------------------------------------------------
    def state(self) -> dict | None:
        """The running job as the status line draws it, or None."""
        with self._lock:
            return dict(self._job) if self._job else None

    def busy(self) -> bool:
        return self.state() is not None

    def models(self) -> list[dict]:
        """The models a Separate button offers: id, label, size, and
        whether the file is already in the cache."""
        return [
            {
                "id": m.id,
                "label": m.label,
                "megabytes": round(m.size / 1e6),
                "have": stems.verify(stems.model_path(m, self.model_dir), m),
            }
            for m in stems.MODELS.values()
        ]

    def start(self, sid: str, model_id: str = "") -> str:
        """Begin separating `sid` with `model_id` (default: the 4-stem
        model). "" when it started (or its stems already exist), else why
        not — "busy" included."""
        model = stems.MODELS.get(model_id) if model_id else stems.HTDEMUCS
        if model is None:
            return "No such separation model."
        meta = sources.read_meta(self.data_dir, sid)
        if meta is None:
            return "That song is gone."
        if meta["stems"]["model"] == model.id:
            return ""
        with self._lock:
            if self._job is not None:
                return "busy"
            folder = sources.source_dir(self.data_dir, sid)
            if folder is not None and sources.stem_dir_ok(folder, model.id):
                # made before, with this model: the stems are still there
                sources.update_meta(
                    self.data_dir, sid, lambda m: m["stems"].update(model=model.id)
                )
                return ""
            self.model = model
        have = self.availability()
        if have["reason"]:
            return have["reason"]
        with self._lock:
            if self._job is not None:
                return "busy"
            self._job = {
                "source": sid,
                "name": meta["name"],
                "label": "Starting",
                "done": 0,
                "total": 1,
            }
            self._cancel.clear()
            self.last = None
            self._thread = threading.Thread(
                target=self._run, args=(sid, meta["name"]), daemon=True
            )
            self._thread.start()
        return ""

    def cancel(self) -> None:
        self._cancel.set()

    def join(self, timeout: float = 60.0) -> None:
        """Wait for the running job (tests; nothing in the studio blocks)."""
        t = self._thread
        if t is not None:
            t.join(timeout)

    def _show(self, label: str, done: int, total: int) -> None:
        with self._lock:
            if self._job is not None:
                self._job.update(label=label, done=done, total=max(1, total))

    def _run(self, sid: str, name: str) -> None:
        error = ""
        try:
            self._download()
            self._separate(sid, name)
        except stems.Cancelled:
            error = "Cancelled."
        except (stems.StemsError, sources.SourceError) as err:
            error = str(err)
        except Exception:  # a daemon thread has no one above it to tell
            log.exception("lab: separation failed")
            error = "Separation failed: see the studio's log."
        with self._lock:
            self._job = None
            self.last = {"source": sid, "error": error, "at": time.time()}

    def _download(self) -> None:
        if self.availability()["model"]:
            return
        megabytes = max(1, round(self.model.size / 1e6))
        label = f"Downloading the separation model ({megabytes} MB)"
        with Task(self.bus, label, megabytes) as task:
            seen = 0

            def progress(done: int, _total: int) -> None:
                nonlocal seen
                mb = min(megabytes, done // 1_000_000)
                if mb > seen:
                    task.step(mb - seen)
                    seen = mb
                    self._show(label, mb, megabytes)

            self._show(label, 0, megabytes)
            self.fetch_model(progress, self._cancel.is_set)

    def _separate(self, sid: str, name: str) -> None:
        meta = sources.read_meta(self.data_dir, sid)
        if meta is None:
            raise sources.SourceError("That song is gone.")
        label = f"Separating “{name}”"
        started = time.monotonic()
        with Task(self.bus, label, 1) as task:
            self._show(label + ": loading the model", 0, 1)
            separator = self.make_separator()
            if tuple(separator.sources) != tuple(self.model.sources):
                # a model file that is not the model it was asked for
                raise sources.SourceError("The separator returned the wrong stems.")
            mix = sources.read(self.data_dir, meta)
            if not len(mix):
                raise sources.SourceError("That song's audio could not be read.")

            def progress(done: int, total: int) -> None:
                task.plan(total + 1)
                task.step()
                self._show(label, done, total + 1)

            out = stems.separate(
                mix,
                separator.run,
                sources=len(separator.sources),
                segment=separator.segment,
                progress=progress,
                cancel=self._cancel.is_set,
            )
            del separator  # the session holds about a gigabyte
            by_name = {s: out[i] for i, s in enumerate(self.model.sources)}
            sources.save_stems(
                self.data_dir, sid, self.model.id, by_name, time.monotonic() - started
            )
            del out, by_name
            # the key, read again without the drums (a kick is a pitched
            # thump and the mix's reading counts it as a note) and with the
            # bass line asked where the music rests. It replaces the mix's
            # reading only when it is the better one (`lab.better_key`)
            lab.reread_key(self.data_dir, sid)
            task.step()
        log.info(
            "lab: separated %s (%.0f s of audio) in %.1f s",
            sid,
            len(mix) / SR,
            time.monotonic() - started,
        )
