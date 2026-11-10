"""The toast, and which view a request came from.

`toast` is the one place the toast fragment is rendered. `view_of` reads
the asking page off htmx's `HX-Current-URL`; `view_parts` is what that
view's loop plays (Perform's own toggles, vs-beat on a bench) and
`fx_scopes_for` which FX racks it shows. Every other unit of the service
calls these through `self`.

A mixin of `StudioService` (the package docstring says why mixins).
"""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..fxrack import rack_rows
from ..state import StudioState
from ..views import BENCH_VIEWS, VIEW_PARTS


class Respond:
    """The toast; which view asked, what it plays and which racks it shows."""

    # the service's fields this unit reads (all set in
    # StudioService.__init__; test_service_split.py holds this list to the code)
    state: StudioState
    templates: Jinja2Templates

    # -- responses ---------------------------------------------------------
    def toast(self, message: str, status: int = 422, **ctx) -> HTMLResponse:
        """The one place the toast fragment template is rendered — no 422
        toast builds its own copy inline. `**ctx` carries template extras like
        `still_playing`; a call site that needs a different status or extra
        response headers builds on this rather than duplicating the render."""
        html = self.templates.get_template("fragments/toast.html").render(
            message=message, **ctx
        )
        return HTMLResponse(html, status_code=status)

    # -- which view --------------------------------------------------------
    def view_of(self, request: Request) -> str:
        """Which view issued this request: htmx sends HX-Current-URL on
        every request; /view/<name> names it, "/" is the editor."""
        url = request.headers.get("HX-Current-URL") or ""
        m = re.search(r"/view/([a-z]+)", url)
        return m.group(1) if m and m.group(1) in VIEW_PARTS else "editor"

    def asked(self, request: Request, name: str) -> str:
        """The first value `name` has in the request's query, else in the
        form it posted (`routing`'s form dependencies leave the parsed form
        on `request.state.form`), else "". For what is handed the request
        but not the handler's form — a region's context: the clip library
        redraws with the search and the section its own controls sent. The
        first, because `htmx.ajax` sweeps every named input on the page and
        the drawer's come first."""
        # (a request built by hand, in a test, may carry no query at all)
        has_query = "query_string" in request.scope
        got = request.query_params.getlist(name) if has_query else []
        if not got:
            form = getattr(request.state, "form", None)
            got = form.getlist(name) if hasattr(form, "getlist") else []
        return got[0] if got and isinstance(got[0], str) else ""

    def view_parts(self, view: str) -> tuple[str, ...]:
        s = self.state
        if view == "perform":
            # the clip lane always rides Perform: empty by default, and the
            # synth/drums toggles keep their existing meanings
            return (*(tuple(s.perform_parts) or ("synth", "drums")), "clips")
        parts = VIEW_PARTS.get(view, ("synth", "drums"))
        # vs-beat: the bench phrase plays over the current drum loop
        if s.vs_beat and view in BENCH_VIEWS and "drums" not in parts:
            parts = (*parts, "drums")
        return parts

    def fx_scopes_for(self, view: str) -> list[tuple[str, str, list]]:
        """Which FX racks a view shows: the drum bus in Drums, layer +
        master in the editor; each is (scope, title, the chain's
        `rack_rows`: what fragments/fx_units.html draws)."""
        s = self.state
        if view == "drums":
            return [("drums", "Drum bus FX", rack_rows(s.fx_drums))]
        if view == "mixer":  # the master strip's rack: the song plays through it
            return [("master", "Master FX", rack_rows(s.fx_master))]
        layer_fx = rack_rows(s.layers[s.active_layer].get("fx"))
        return [
            ("layer", f"Layer {s.active_layer + 1} FX", layer_fx),
            ("master", "Master FX", rack_rows(s.fx_master)),
        ]
