"""FastAPI app: server-rendered studio. htmx swaps fragments; the audio
island listens for the `preview-ready` trigger and crossfades the loop.

`create_app` wires the pieces: the security boundary (outermost), gzip and
timing middleware, static files, the Jinja environment, the `StudioService`
on `app.state.svc`, the `RenderError` toast handler, and one router per
area (`routes/`). Every route is a `routing.StudioRoute`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import percussion
from ..params import taper
from . import beatgen, limits, meter, modmixes, oscfaces, security, workspace
from .fxrack import chain_browser
from .mapview import bright_pct
from .paramui import (
    MOD_SOURCE_LABELS,
    MOD_TARGET_LABELS,
    ROUTE_FIELDS,
    SUBHEADS,
    spec_of,
)
from .perf import route_key
from .render import RenderError
from .routes import ROUTERS
from .routing import StudioRoute, mount
from .service import StudioService
from .sigdraw import sig_box, sig_path, smooth_path
from .views import view_link
from .words import hz, plural

STUDIO_DIR = Path(__file__).parent

log = logging.getLogger("synthlab.studio")


def default_data_dir() -> Path:
    """Resolved fresh on every call (inside `create_app`), not once at
    import time — a module-level `Path.cwd()` freezes whatever directory
    happened to be current when the module was first imported, which is
    the test runner's cwd, not the caller's."""
    return Path.cwd() / "studio_data"


def gene_label(badge: str) -> str:
    """A breed badge as words: "filter_type→bandpass" -> "Filter type →
    bandpass", "mod1_src→lfo" -> "Route 1 source → LFO 1", "sustain↓" ->
    "Sustain ↓", "cross:Glass Ray" -> "× Glass Ray". The stored badge stays
    the raw form (it is persisted with the board); this is display only."""
    if badge == "jump":
        return "big jump"
    if badge == "wild":
        return "random"
    if badge.startswith("cross:"):
        return "× " + badge[len("cross:") :]
    for sep in ("→", "↑", "↓"):
        if sep in badge:
            field, _, value = badge.partition(sep)
            spec = spec_of(field)
            label = spec.label if spec else field.replace("_", " ")
            if sep != "→":
                return f"{label} {sep}"
            value = (
                MOD_SOURCE_LABELS.get(value)
                or MOD_TARGET_LABELS.get(value)
                or ("off" if value in ("None", "") else value)
            )
            return f"{label} → {value}"
    return badge


def octave_label(octave) -> str:
    """-1 -> "−1 oct", 1 -> "+1 oct", 0 -> "" (a layer chip, a follower)."""
    try:
        o = int(octave)
    except (TypeError, ValueError):
        return ""
    if o == 0:
        return ""
    return f"{'+' if o > 0 else '−'}{abs(o)} oct"


class RevvedStaticFiles(StaticFiles):
    """`/static`, with revved URLs cached for good. base.html asks for
    every asset through `asset("/static/<name>")`: `?v=<the file's own
    hash>` (`static_hashes`, its bytes hashed once at startup), so a URL
    carrying a file's current hash names these exact bytes and can be
    `immutable` — a page load revalidates nothing, and editing one file
    re-downloads that file alone (one rev for the whole folder, the newest
    mtime's, sent all ~82 again after any edit). Any other URL (no `v`, a
    stale one, the worklet module) keeps the default ETag/Last-Modified
    revalidation."""

    def __init__(self, *args, hashes: dict[str, str], **kwargs):
        super().__init__(*args, **kwargs)
        self.hashes = hashes

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        own = self.hashes.get(path.replace("\\", "/"))
        if response.status_code in (200, 304) and own and query.get("v") == [own]:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def static_hashes(static_dir: Path) -> dict[str, str]:
    """Each static file's own rev: the first 8 hex of a sha1 of its
    bytes, keyed by its name. Read once, when the templates are made (the
    server does not hot-reload: a restart picks an edit up)."""
    return {
        f.name: hashlib.sha1(f.read_bytes()).hexdigest()[:8]
        for f in sorted(static_dir.iterdir())
        if f.is_file()
    }


def make_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=STUDIO_DIR / "templates")
    # value -> 0..1 slider position for log-taper ranges (param_row.html)
    templates.env.filters["logpos"] = lambda v, lo, hi: (
        0.0
        if not v or float(v) <= float(lo)
        else min(1.0, taper(float(v), float(lo), float(hi)))
    )
    # `asset("/static/x.js")`: the URL with that file's own hash (RevvedStaticFiles)
    hashes = static_hashes(STUDIO_DIR / "static")
    templates.env.globals["static_hashes"] = hashes

    def asset(url: str) -> str:
        name = url.removeprefix("/static/")
        return f"{url}?v={hashes[name]}" if name in hashes else url

    templates.env.globals["asset"] = asset
    # named grid/tempo bounds (BPM_RANGE, STEP_CHOICES, ...), so a template
    # iterates the one source instead of repeating [1, 2, 3, 4] etc.
    templates.env.globals["limits"] = limits
    # the bar (meter.py): the pickers' meters, each grid's step choices
    # and its accents (`meter.step_choices`, `meter.accents`)
    templates.env.globals["meter"] = meter
    # row headings inside a panel (paramui.SUBHEADS): a global, because a
    # macro move renders param_row.html OOB with its own small context
    templates.env.globals["subheads"] = SUBHEADS
    # the oscillators' drawn controls (oscfaces.py: the fm4 algorithms' wiring,
    # the wavetables' frames, the FM4 ratio presets) and the rows that draw
    # them (param_row.html reaches a sibling field: editor.html's FM4 panel)
    templates.env.globals["osc_faces"] = oscfaces
    templates.env.globals["spec_of"] = spec_of
    # every route slot's three fields (twelve slots): the mod
    # matrix draws them, every other panel leaves them out
    templates.env.globals["route_fields"] = ROUTE_FIELDS
    # the FX racks' chain browser: every chain preset, grouped (fx_rack.html)
    templates.env.globals["chain_browser"] = chain_browser()
    # a drum row's "Percussion" choices (synthlab/percussion.py)
    templates.env.globals["perc_kinds"] = percussion.KINDS
    templates.env.globals["perc_clips"] = {f"perc-{k}" for k in percussion.KINDS}
    # the mod matrix's one-move mixes (modmixes.py): (id, label, what you hear)
    templates.env.globals["mod_mixes"] = [
        (k, m[0], m[1]) for k, m in modmixes.MIXES.items()
    ]
    # each page's Layout menu: its presets and the modules it can add
    # (workspace.py; the layout itself is the browser's, workspace.js)
    templates.env.globals["workspace_menu"] = workspace.menu
    # a link to another page that swaps it in place (views.view_link)
    templates.env.globals["view_link"] = view_link
    templates.env.filters["gene_label"] = gene_label
    templates.env.filters["octave_label"] = octave_label
    # a spectral centre as a meter's length (Breed's cards, the Map's rack)
    templates.env.filters["bright_pct"] = bright_pct
    # the one signal graphic: a smooth area + top stroke as one path (sigdraw.py)
    templates.env.filters["sig_path"] = sig_path
    templates.env.filters["sig_box"] = sig_box
    templates.env.filters["smooth_path"] = smooth_path
    # "1 bar" / "2 bars": the one rule, Python's and the templates' (words.py)
    templates.env.filters["plural"] = plural
    # a frequency read-out: "233 Hz", "12.7 kHz" (words.py)
    templates.env.filters["hz"] = hz
    # a beat style as a menu shows it ("filter-house-break": "Filter house break")
    templates.env.filters["style_name"] = beatgen.style_name
    # a post's values with more of them (the Loops list: a row's `ref` and
    # the choice a button makes, `{{ (r.ref | combine({...})) | tojson }}`)
    templates.env.filters["combine"] = lambda a, b: {**a, **b}
    return templates


def create_app(
    data_dir: Path | None = None,
    *,
    allowed_hosts=None,
    share_token: str | None = None,
) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        # end the /events streams (under uvicorn the signal handler has
        # already: it waits for open connections before it gets here)
        app.state.svc.events.close()
        app.state.svc.state.flush()  # a pending debounced save must not be lost

    app = FastAPI(title="synthlab studio", lifespan=lifespan)
    app.router.route_class = StudioRoute
    # ponytail: the editor page is ~266KB of HTML (509 chord pads, 456 SVG
    # rects) and compresses 11.7x. minimum_size skips the small fragment
    # responses, where the round-trip already dominates.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.state.data_dir = Path(data_dir) if data_dir else default_data_dir()
    svc = StudioService(app.state.data_dir, make_templates())
    app.state.svc = svc
    # the wavetable picker's own tables (oscfaces.own_faces), this
    # studio's store, drawn wherever param_row.html draws the picker
    svc.templates.env.globals["own_tables"] = lambda: oscfaces.own_faces(
        svc.state.wavetable_dir
    )
    # the session state under its historical name (tests and tools read it)
    app.state.studio = svc.state
    # SYNTHLAB_DEBUG=1 turns the per-request/per-render debug lines on in
    # the terminal; /debug/stats and the Server-Timing header are always on.
    if os.environ.get("SYNTHLAB_DEBUG"):
        log.setLevel(logging.DEBUG)
        if not log.handlers:
            log.addHandler(logging.StreamHandler())

    @app.middleware("http")
    async def timing(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000
        path = request.url.path
        if not path.startswith("/static"):
            # by route, not by URL: the table stays as small as the route list
            svc.perf.record_request(request.method, route_key(request.scope), ms)
            response.headers["Server-Timing"] = f"app;dur={ms:.1f}"
            log.debug("%s %s %d %.1fms", request.method, path, response.status_code, ms)
        return response

    # Starlette runs the last-added middleware first, so this must be added
    # after `timing` to sit outermost — the boundary rejects a bad
    # host/origin before anything else on the request path runs.
    security.install(app, allowed_hosts, share_token)

    app.mount(
        "/static",
        RevvedStaticFiles(
            directory=STUDIO_DIR / "static",
            hashes=svc.templates.env.globals["static_hashes"],
        ),
        name="static",
    )

    @app.exception_handler(RenderError)
    def render_error_toast(request: Request, exc: RenderError) -> HTMLResponse:
        """The default RenderError -> 422 toast. `StudioRoute` holds the
        studio lock only around the handler call, so a RenderError raised
        inside it still unwinds the `with` block (releasing the lock)
        before reaching here. Call sites that need a different status, no
        `still_playing`, or extra headers/composition (the curve editor)
        catch RenderError themselves and call `toast()` directly instead of
        relying on this handler."""
        return svc.toast(str(exc), still_playing=True)

    for router in ROUTERS:
        mount(app, router)
    return app
