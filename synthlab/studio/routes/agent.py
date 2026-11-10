"""The Sound agent drawer on Synths: a turn, and what to do with one.

`/fragment/agent/turn` is `@render_outside_lock`: its handler runs under
the studio lock only to validate and snapshot (`AgentService.begin`), and
the Gemini calls happen after the lock is released — a 10 s model answer
must never stall /health, a slider or a key. Each round of tool calls
re-takes the lock to apply and render. Every answer is the whole page (the
panels, layer strip, FX racks and the drawer's log all moved) or a toast.
"""

from __future__ import annotations

from fastapi import Request, Response

from ..routing import OutsideLock, StrForm, Svc, render_outside_lock, studio_router

router = studio_router()


@router.post("/fragment/agent/turn")
@render_outside_lock
def agent_turn(request: Request, form: StrForm, svc: Svc) -> Response:
    started = svc.agent.begin(svc, request, form.get("message", ""))
    if isinstance(started, Response):
        return started
    return OutsideLock(started)


@router.post("/fragment/agent/undo")
def agent_undo(request: Request, svc: Svc) -> Response:
    """Undo turn: the whole stack as it was before the newest turn."""
    err = svc.agent.undo(svc)
    return svc.toast(err) if err else svc.full_page_with_preview(request)


@router.post("/fragment/agent/ab")
def agent_ab(request: Request, svc: Svc) -> Response:
    """Store as A/B: before the newest turn in A, after it in B."""
    err = svc.agent.store_ab(svc)
    return svc.toast(err) if err else svc.full_page_with_preview(request)


@router.post("/fragment/agent/budget")
def agent_budget(request: Request, svc: Svc) -> Response:
    """New budget: tokens, turns and the money estimate back to zero (the
    log stays). The budget persists across restarts, so this is the one
    way to start another."""
    svc.agent.new_budget()
    return svc.full_view_page(request)


@router.post("/fragment/agent/apply")
def agent_apply(request: Request, form: StrForm, svc: Svc) -> Response:
    """Apply to layer N / a new layer: the result moves onto that layer and
    the edited one goes back to how the turn found it."""
    err = svc.agent.apply_to_layer(svc, form.get("target", "new"))
    return svc.toast(err) if err else svc.full_page_with_preview(request)
