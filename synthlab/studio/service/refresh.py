"""The out-of-band refresh: which regions moved, sending and announcing them.

`routing.StudioRoute` snapshots `watch_page` around every state-changing
request, takes `moved` once and hands it to `refresh` (the regions
appended to the response, less the suppressed and the already drawn) and
to `announce` (the change notice for the pages open elsewhere).
`oob_regions` renders named regions; `suppress` / `take_suppressed` are
the per-request "do not redraw the form that posted" set — `_suppressed`,
written and taken under the studio lock, and this unit's alone. The table
itself is `regions.py`.

A mixin of `StudioService` (the package docstring says why mixins).
"""

from __future__ import annotations

from collections.abc import Hashable

from fastapi import Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..cliplib import sig_memo
from ..events import ORIGIN_HEADER, EventBus
from ..state import StudioState
from .regions import PAGE_REGIONS, REGIONS


class Refresh:
    """`oob_regions`, `watch_page`, `moved`, `announce`, `suppress`,
    `refresh`."""

    # the service's fields this unit reads or writes (all set in
    # StudioService.__init__; test_service_split.py holds this list to the code)
    state: StudioState
    templates: Jinja2Templates
    events: EventBus
    _suppressed: set[str]

    # -- out-of-band regions ---------------------------------------------
    def oob_regions(
        self,
        request: Request,
        *names: str,
        view: str | None = None,
        oob: bool = True,
    ) -> str:
        """The named `REGIONS`, rendered for `view` (default: the view the
        request came from) and concatenated, out of band unless `oob` is
        False. A region with a lean `context` renders from it; the rest
        share one `page_context` over the union of their sections."""
        v = view or self.view_of(request)
        full = [n for n in names if REGIONS[n].context is None]
        shared: dict = {}
        if full:
            need = frozenset().union(*(REGIONS[n].needs for n in full))
            shared = self.page_context(request, v, need=need)
        out = []
        for n in names:
            region = REGIONS[n]
            ctx = region.context(self, request, v) if region.context else shared
            tpl = self.templates.get_template(region.template)
            out.append(tpl.render({**ctx, "oob": oob}))
        return "".join(out)

    def watch_page(self, view: str) -> dict[str, Hashable]:
        """Every region on `view`'s page -> its watch value, now. Each
        `is_dirty` and the clip store's listing are read once for the lot
        (the watches only read)."""
        with self.state.dirty_memo(), sig_memo():
            return {n: REGIONS[n].watch(self, view) for n in PAGE_REGIONS.get(view, ())}

    def moved(self, view: str, before: dict[str, Hashable]) -> list[str]:
        """The regions of `view`'s page whose watch moved since `before`,
        in send order: what `refresh` sends from, and what `announce` tells
        the pages open elsewhere."""
        now = self.watch_page(view)
        return [n for n, sig in before.items() if now[n] != sig]

    def announce(
        self,
        request: Request,
        view: str,
        moved: list[str],
        mark: int,
        response=None,
    ) -> None:
        """Tell the other open pages what a state-changing request just
        committed (`events.EventBus.changed`): the regions of the
        requesting page whose watch moved — the set `refresh` sends from,
        *before* it leaves out the suppressed and the already-drawn, since
        another page has neither — and whether the answer was a whole page.
        Nothing is said when no watch moved, the answer was not a whole
        page and nothing was saved since `mark` (`StudioState.edits`): a
        refused request, a toast. `origin` is the asking page's own id
        (`ORIGIN_HEADER`), so it can ignore its echo."""
        headers = getattr(response, "headers", {})
        whole = getattr(response, "whole_page", False) or "hx-refresh" in headers
        if moved or whole or self.state.edits != mark:
            origin = request.headers.get(ORIGIN_HEADER, "")[:64]
            self.events.changed(origin, view, moved, whole)

    def suppress(self, *names: str) -> None:
        """This request must not re-send these regions even though their
        state moved: the page is already showing it. The form the user is
        typing or dragging in (the grid, a slider strip) posted the change
        itself, and redrawing it under the pointer would lose the gesture;
        a launcher perform.js shows "queued" keeps that state until the
        loop wraps. Cleared by the route wrapper after each handler."""
        self._suppressed |= set(names)

    def take_suppressed(self) -> frozenset[str]:
        out, self._suppressed = frozenset(self._suppressed), set()
        return out

    def refresh(
        self,
        request: Request,
        view: str,
        before: dict[str, Hashable],
        response,
        suppressed: frozenset[str] = frozenset(),
        moved: list[str] | None = None,
    ):
        """Append to `response` every region of `view`'s page whose watch
        moved since `before`, out of band — except one the response already
        draws (its main target, or an outer region carrying an inner one)
        and the `suppressed`. Only an HTML answer that swaps part of the
        page gets regions: a whole page (`full_view_page`), a redirect or
        an `HX-Location`, an error or a toast, JSON or a WAV need none.
        A bodiless 204 whose state moved becomes a 200 carrying them.
        `moved` is `self.moved(view, before)` when the caller already has
        it (the route wrapper, which also announces it: `announce`)."""
        if not isinstance(response, Response) or getattr(response, "whole_page", False):
            return response
        headers = response.headers
        if any(h in headers for h in ("hx-location", "hx-redirect", "hx-refresh")):
            return response
        status = response.status_code
        if status == 204:
            body = ""
        elif status == 200 and headers.get("content-type", "").startswith("text/html"):
            body = bytes(response.body).decode("utf-8")
        else:
            return response
        if moved is None:
            moved = self.moved(view, before)
        stale = [n for n in moved if n not in suppressed]
        send: list[str] = []
        for n in stale:
            inside = REGIONS[n].inside
            if f'id="{n}"' in body or (inside in send):
                continue  # already drawn by this response
            send.append(n)
        if not send:
            return response
        html = body + self.oob_regions(request, *send, view=view)
        if status == 204:
            return HTMLResponse(
                html,
                headers={k: v for k, v in headers.items() if k != "content-length"},
            )
        response.body = html.encode("utf-8")
        headers["content-length"] = str(len(response.body))
        return response
