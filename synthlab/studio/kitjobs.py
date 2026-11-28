"""The Kit page's background job: download the acoustic kit (Naked Drums,
`synthlab.kits.fetch`) the first time a beat chooses it.

Modelled on `labjobs.py` (the Lab's model download):

- **One at a time.** `start` refuses a second with "busy" rather than
  queueing it; a kit already here answers "" and runs nothing.
- **Off the request thread, outside the studio lock.** A daemon thread that
  never touches `StudioState`: `fetch.download` writes each file beside its
  place, checks its size and SHA-256 and renames it in only then, so a job
  stopped anywhere (cancelled, killed) leaves the kit resumable and never
  half a file.
- **Progress** goes two ways: `events.Task` (the toast line on every open
  page: a step per megabyte) and `state()` (what the Kit page's status line
  polls, `GET /fragment/drums/kit/job`). `last` keeps how the previous job
  ended — its error is `KitError`'s own sentence.
- **When it ends** (however it ends) `arrange.forget_kit()` drops the
  render's 2 s belief about whether the kit is here, so the next render's
  inputs ask the disk again: their key moves with the answer
  (`RenderInputs.kit_ready`), and the loop plays the kit.

`fetch_kit` and `availability` are the seams the tests replace (no network).
"""

from __future__ import annotations

import logging
import threading
import time
from functools import lru_cache

from ..kits import fetch
from . import arrange
from .events import EventBus, Task

log = logging.getLogger("synthlab.studio")


@lru_cache(maxsize=1)
def kit_bytes() -> int:
    """The whole kit's size, from its committed manifest."""
    return sum(e["size"] for e in fetch.manifest(fetch.NAKED))


def size_label(megabytes: float | None = None) -> str:
    """A download's size as the button says it: "1.3 GB", "240 MB" — the
    whole kit by default."""
    mb = kit_bytes() / 1e6 if megabytes is None else megabytes
    return f"{mb / 1e3:.1f} GB" if mb >= 1000 else f"{max(1, round(mb))} MB"


class KitJobs:
    def __init__(self, bus: EventBus | None = None):
        self.bus = bus
        self._lock = threading.Lock()
        self._job: dict | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.last: dict | None = None  # {"error", "at"}: how the last ended

    # -- the seams the tests replace -----------------------------------------
    def availability(self) -> dict:
        return fetch.availability(fetch.NAKED)

    def fetch_kit(self, progress, cancel) -> None:
        fetch.download(fetch.NAKED, progress=progress, cancel=cancel)

    # -- the job -------------------------------------------------------------
    def state(self) -> dict | None:
        """The running job as the status line draws it, or None."""
        with self._lock:
            return dict(self._job) if self._job else None

    def busy(self) -> bool:
        return self.state() is not None

    def last_error(self) -> str:
        return (self.last or {}).get("error", "")

    def take_fresh(self) -> bool:
        """True once for a job that ended with the kit downloaded: the one
        poll that asks (`GET /fragment/drums/kit/job?watch=1`) forgets the
        renders made with the stand-in voices; every other poll, tab or
        failed job answers False."""
        with self._lock:
            if self.last and self.last.get("fresh"):
                self.last["fresh"] = False
                return True
            return False

    def start(self, repair: bool = False) -> str:
        """Begin downloading the kit. "" when it started (or the kit is
        already here), "busy" while one runs. `repair`: run even when every
        file is there by size — `fetch.download` hashes each one again and
        fetches what does not match (the kit met a damaged file)."""
        with self._lock:
            if self._job is not None:
                return "busy"
        if not repair and self.availability().get("ready"):
            arrange.forget_kit()
            return ""
        with self._lock:
            if self._job is not None:
                return "busy"
            self._job = {"label": "Starting", "done": 0, "total": 1}
            self._cancel.clear()
            self.last = None
            self._thread = threading.Thread(target=self._run, daemon=True)
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

    def _run(self) -> None:
        error = ""
        try:
            self._download()
        except fetch.Cancelled:
            error = "Cancelled."
        except fetch.KitError as err:
            error = str(err)
        except Exception:  # a daemon thread has no one above it to tell
            log.exception("kit: the download failed")
            error = "The kit's download failed: see the studio's log."
        arrange.forget_kit()  # the next render asks the disk again
        with self._lock:
            self._job = None
            self.last = {"error": error, "at": time.time(), "fresh": not error}

    def _download(self) -> None:
        megabytes = max(1, round(kit_bytes() / 1e6))
        label = f"Downloading {fetch.NAKED.label} ({megabytes} MB)"
        with Task(self.bus, label, megabytes) as task:
            seen = 0
            left = megabytes

            def progress(done: int, total: int) -> None:
                nonlocal seen, left
                left = max(1, round(total / 1e6))
                mb = min(left, done // 1_000_000)
                if mb > seen:
                    task.step(mb - seen)
                    seen = mb
                self._show(label, mb, left)

            self._show(label, 0, megabytes)
            self.fetch_kit(progress, self._cancel.is_set)


# what the Kit page shows while a beat plays the kit (and a project's notes)
CREDIT = "Naked Drums by Wilkinson Audio, CC-BY 4.0"
CREDIT_URL = "https://github.com/sfzinstruments/WilkinsonAudio.NakedDrums"
# the mic mixer: a fader's name, and where a mic a preset leaves off rests
MIC_LABELS = {
    "kick_in": "Kick In",
    "kick_sub": "Kick Sub",
    "snare_top": "Snare Top",
    "snare_bottom": "Snare Bottom",
    "toms": "Toms",
    "hats": "Hats",
    "ride": "Ride",
    "cymbals": "Cymbals",
    "overheads": "Overheads",
    "close_room": "Close Room",
    "far_room": "Far Room",
    "mid_side": "Mid-Side",
}
MUTED_DB = -12.0
# the presets' buttons (`kits.play.MIC_PRESETS`)
PRESET_LABELS = (("studio", "Studio"), ("close", "Close & dry"), ("room", "Big room"))
