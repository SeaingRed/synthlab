"""`GET /events`: the server-push stream (`studio/events.py`)."""

from __future__ import annotations

from fastapi import Response
from fastapi.responses import StreamingResponse

from ..routing import Svc, lock_free, studio_router

router = studio_router()


@router.get("/events")
@lock_free
def events(svc: Svc) -> Response:
    """Server-Sent Events: `hello`, then `progress` / `done` / `failed` of
    the long renders and a `changed` after every state-changing request,
    until the page goes or the studio stops. A plain `def` that does no
    work: the stream itself is an async generator on the event loop, so an
    open page holds no threadpool thread, and it never takes the studio
    lock. 503 past `limits.MAX_EVENT_STREAMS` (or while shutting down); the
    page then runs as it did before there was a stream.

    The slot is reserved here (`EventBus.reserve`, atomic) and handed to
    the stream, which gives it back when it ends: checking `bus.full` here
    and subscribing when the body started let two requests both pass, and
    the loser's empty 200 had EventSource reconnecting for ever instead of
    reading this 503's Retry-After."""
    bus = svc.events
    sub = bus.reserve()
    if sub is None:
        return Response(status_code=503, headers={"Retry-After": "30"})
    return StreamingResponse(
        bus.stream(sub),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # nginx-style proxies buffer a response unless told not to
            "X-Accel-Buffering": "no",
        },
    )
