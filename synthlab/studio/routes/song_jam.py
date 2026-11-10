"""The jam's one route: `POST /song/jam/commit`
turns the launcher's log into sections appended to the song.

The launcher itself has no route and no state: it plays the cell loops
`GET /song/cell/{section}/{track}.wav` already serves (`routes/song.py`)
and keeps its log in the page (`static/jam.js`). The edit is
`studio/jam.py` (pure); the route is what every Song route is — one
`_commit`, so ONE undo step ("Undo: capture the jam"), the regions by
their watches, a refusal a 422 toast and nothing changed, the length caps
checked on what it made. It answers `jam-captured` (the new sections'
ids) beside `song-changed`, which is how the page knows to drop its log;
a refusal carries neither, so the log is kept and can be captured again.

Hostile input: `events` that is not a JSON list of objects, or longer
than `MAX_JAM_EVENTS` (refused whole, never truncated), a `bars` that is
not a whole number, an id the song does not have — each a 422 toast.
Every event is conformed (`schema.nodes().JAM_EVENTS`): a bar is clamped
to the timeline; an id that is not one refuses the log (dropped to "none"
it would read as another event: "stop everything").
"""

from __future__ import annotations

import json

from fastapi import Request, Response

from .. import jam, limits, schema
from .. import song_model as sm
from ..routing import StrForm, Svc, studio_router
from .song import _commit

router = studio_router()

MAX_EVENTS_CHARS = 128 * limits.MAX_JAM_EVENTS  # an event is ~70 characters
TOO_MANY = f"A jam holds at most {limits.MAX_JAM_EVENTS} launches: capture sooner."
UNREADABLE = "The jam's log could not be read."
LOOSE = (
    " A lane or a trimmed block stayed behind where a part is not a whole"
    " pass of its section."
)


def _events(form: dict) -> list[dict]:
    raw = str(form.get("events", ""))
    if len(raw) > MAX_EVENTS_CHARS:
        raise sm.SongError(TOO_MANY)
    try:
        events = json.loads(raw) if raw.strip() else []
    except (ValueError, RecursionError):
        raise sm.SongError(UNREADABLE) from None
    if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
        raise sm.SongError(UNREADABLE)
    if len(events) > limits.MAX_JAM_EVENTS:
        raise sm.SongError(TOO_MANY)
    clean = schema.conform(events, schema.nodes().JAM_EVENTS)
    # an id the schema dropped must not read as "no id": a launch of one
    # track with a mangled id would become "stop everything"
    if len(clean) != len(events) or any(
        raw.get(k) is not None and ev.get(k) is None
        for raw, ev in zip(events, clean, strict=True)
        for k in ("track", "scene", "target")
    ):
        raise sm.SongError(UNREADABLE)
    return clean


def _bars(form: dict) -> int:
    raw = str(form.get("bars", "")).strip()
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 6):
        raise sm.SongError("The jam's length is a whole number of bars.")
    return min(int(raw), limits.MAX_LONG_BAR)


@router.post("/song/jam/commit")
def song_jam_commit(request: Request, form: StrForm, svc: Svc) -> Response:
    """Capture the jam: `events` (the launcher's log, JSON: `jam.py` says
    what one is) up to bar `bars` become sections at the end of the song —
    one where the sounding cells changed, repeats where a loop went round.
    One undo step. The toast counts them, and says when a lane or a
    trimmed block could not come along."""
    made: dict = {}

    def change(song):
        ids, loose = jam.commit(song, _events(form), _bars(form))
        made.update(ids=ids, loose=loose)

    def then():
        n = len(made["ids"])
        say = f"{n} section{'' if n == 1 else 's'} added from the jam."
        return {
            "jam-captured": {"sections": made["ids"]},
            "studio-toast": {"message": say + (LOOSE if made["loose"] else "")},
        }

    return _commit(svc, change, label="capture the jam", then=then)
