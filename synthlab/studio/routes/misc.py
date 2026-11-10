"""Housekeeping routes: the theme toggle, /health and /debug/stats — all lock-free."""

from __future__ import annotations

import os

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from ..render import NOTE_MEMO
from ..routing import StrForm, Svc, lock_free, studio_router
from ..service import theme_of

router = studio_router()


@router.get("/health")
@lock_free
async def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/debug/stats")
@lock_free
def debug_stats(svc: Svc) -> Response:
    if not os.environ.get("SYNTHLAB_DEBUG"):
        return Response(status_code=404)
    return JSONResponse(
        svc.perf.snapshot()
        | {
            "render_cache": svc.cache.stats(),
            "note_cache": svc.notes.stats(),
            "stem_cache": svc.stems.stats(),
            # the loop renders' notes by content (render.NOTE_MEMO)
            "note_memo": NOTE_MEMO.lru.stats(),
            # the push streams: open now, published, dropped on overflow
            "events": svc.events.stats(),
        }
    )


@router.post("/theme")
@lock_free
def theme(request: Request, form: StrForm) -> Response:
    # under "system" the server cannot see the OS's scheme: the toggle posts
    # the theme in force (`from`, shell.js), so the first press always
    # changes what is seen (it set "day" on a light OS: nothing moved)
    now = theme_of(request)
    if now == "system":
        now = form.get("from") if form.get("from") in ("day", "night") else "night"
    new = "night" if now == "day" else "day"
    resp = Response(status_code=204, headers={"HX-Refresh": "true"})
    resp.set_cookie("theme", new, max_age=365 * 24 * 3600, samesite="lax")
    return resp
