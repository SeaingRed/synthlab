"""Pages and per-view actions: the editor and /view/{name}, reset and
undo, the Play fallback, and the Map and Bass views' own panels."""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import asdict
from html import escape
from types import SimpleNamespace

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ...core import SR
from ...explore.probe import probe
from .. import labchops, limits, palette, sounds, workspace
from ..bassview import bass_panels
from ..breedboard import WILDNESS, new_generation
from ..mapview import HERE_ID, MAX_POINTS, map_facts, map_svg, shelf_rows
from ..render import RenderError, preview_url
from ..routing import (
    OutsideLock,
    StrForm,
    Svc,
    autoplay,
    render_outside_lock,
    studio_router,
)
from ..service import StudioService
from ..state import ROOTS, StudioState
from ..views import RESET_VIEWS, VIEW_NAMES, VIEW_PARTS
from .fx import fx_rack_fragment

log = logging.getLogger("synthlab.studio")

router = studio_router()


@router.post("/fragment/reset")
def fragment_reset(request: Request, form: StrForm, svc: Svc) -> Response:
    """Revert a scope to its baseline (how it was when last loaded or
    saved), or reset it to factory defaults. "all" = every scope this
    view owns."""
    s: StudioState = svc.state
    view = svc.view_of(request)
    allowed = RESET_VIEWS.get(view, ("sound",))
    scope, mode = form.get("scope", ""), form.get("mode", "revert")
    targets = list(allowed) if scope == "all" else [scope] if scope in allowed else []
    if not targets or mode not in ("revert", "defaults"):
        return Response(status_code=422)
    before = s.patch_snapshot()
    done = [
        t
        for t in targets
        if (s.reset_defaults(t) if mode == "defaults" else s.revert(t))
    ]
    s.push_undo_step(before)  # a reset is one Ctrl+Z, like a load
    if not done:
        return svc.toast(
            "Nothing to revert — nothing here has been loaded or saved yet."
        )
    s.save()
    try:
        headers = svc.preview_trigger(view=view)
    except RenderError as err:  # the reset still happened; say why it is quiet
        said = {"message": str(err), "kind": "error"}
        headers = {"HX-Trigger": json.dumps({"studio-toast": said})}
    return svc.full_view_page(request, headers)


def _active_row_id(svc: StudioService, rows: list[dict]) -> str | None:
    """Identify the logged row (if any) matching the studio's current
    patch, the same way it's identified everywhere else — its stripped
    patch dict, per patch_key/SpaceStore.add. If the current patch was
    never logged to the store, return None and let map_svg fall back to
    the most-recently-logged point."""
    s: StudioState = svc.state
    current = s.patch.to_json()
    matches = [r for r in rows if r.get("patch") == current]
    if not matches:
        return None
    return max(matches, key=lambda r: r.get("t", 0)).get("id")


# the sound on Synths, measured on the breeder's probe: by its patch, so the
# map measures an edit once (a probe is ~10 ms; a few entries is all it needs)
_HERE_MEMO: dict[str, dict] = {}


def _here_row(svc: StudioService) -> dict | None:
    """The sound on Synths as a map row that is never stored (mapview.HERE_ID):
    its probe's features, so "you are here" is where it IS, not the newest
    logged point standing in for it."""
    patch = svc.state.patch
    key = json.dumps(patch.to_json(), sort_keys=True)
    features = _HERE_MEMO.get(key)
    if features is None:
        measured = probe(patch)
        if measured is None:
            return None
        features = measured.features.to_dict()
        if len(_HERE_MEMO) >= 16:
            _HERE_MEMO.pop(next(iter(_HERE_MEMO)))
        _HERE_MEMO[key] = features
    return {"id": HERE_ID, "patch": {"name": patch.name}, "features": features}


def map_fragment(svc: StudioService, active: str | None = None) -> str:
    # bounded: 592 logged auditions produced a 228 KB / 2374-node SVG; the
    # shipped sounds (a frozen ~290, `mapview.shelf_rows`) are the faint
    # field behind them, fitted with them (never an empty well)
    shelf = shelf_rows()
    store = svc.space_store()
    rows = store.project_2d(limit=MAX_POINTS, background=shelf)
    if active is None:
        active = _active_row_id(svc, rows)
        if active is None and rows and (here := _here_row(svc)) is not None:
            rows = store.project_2d(limit=MAX_POINTS, extra=here, background=shelf)
            active = HERE_ID
    # the plot, and beside it the same rows in words (fragments/map_body.html)
    return svc.templates.get_template("fragments/map_body.html").render(
        svg=map_svg(rows, active_id=active), map=map_facts(rows, active_id=active)
    )


@router.get("/", response_class=HTMLResponse)
def editor(request: Request, svc: Svc) -> Response:
    return svc.templates.TemplateResponse(
        request, "editor.html", svc.page_context(request)
    )


@router.get("/view/{name}", response_class=HTMLResponse)
@render_outside_lock
def view_page(request: Request, name: str, svc: Svc) -> Response | OutsideLock:
    if name not in VIEW_NAMES:
        return Response(status_code=404)
    # Breed opened on an empty board: one grey sentence and a button.
    # A generation costs a few hundred ms, so just show the offspring — the
    # five steering buttons only make sense next to something to steer. It
    # renders outside the studio lock from a snapshot of the parents, like
    # /fragment/breed/generation, and the page is built back under it.
    st: StudioState = svc.state
    if name == "breed" and not st.breed_tiles:
        parents = SimpleNamespace(
            breed_starred=list(st.breed_starred),
            patch=copy.deepcopy(st.patch),
            data_dir=st.data_dir,
            bpm=st.arrangement.bpm,
        )
        amount = WILDNESS[st.breed_wildness]
        depth = st.breed_depth

        def run() -> Response:
            try:
                tiles = new_generation(
                    parents, None, amount=amount, store=svc.space_store(), depth=depth
                )
            except Exception:
                # an empty board is still a usable page
                log.exception("view_page: initial breed generation failed")
                tiles = None
            with st.lock:
                if tiles is not None and not st.breed_tiles:
                    st.breed_tiles = [asdict(t) for t in tiles]
                    cap = limits.MAX_BREED_HISTORY
                    st.breed_history = (st.breed_history + [st.breed_tiles])[-cap:]
                    # starred parents are consumed by a generation
                    st.breed_starred = []
                    st.save_soon()
                return _view_response(request, name, svc)

        return OutsideLock(run)
    return _view_response(request, name, svc)


def _view_response(request: Request, name: str, svc: StudioService) -> Response:
    notice = ""
    if name == "lab" and "source" in request.query_params:
        # "Open in Lab" (a kept chop's clip, a stem in the library):
        # /view/lab?source=<id>&chop=<id> opens that song with that chop
        # the region. Either gone: the Lab as it is, and a line saying so.
        q = request.query_params
        notice = labchops.open_at(
            svc.data_dir, q["source"][:80], q.get("chop", "")[:80]
        )
    ctx = svc.page_context(request, name)
    if notice:
        ctx["lab"]["notice"] = notice
    if request.headers.get("HX-Request"):
        # the clip library sits outside #view and differs by page (what a
        # clip can do here): it rides every view swap out of band, morphed,
        # so an open drawer stays open and follows the page
        drawer = svc.templates.get_template("fragments/clip_library.html")
        html = svc.templates.get_template(f"views/{name}.html").render(ctx)
        # and the status strip: its tempo and key are what the page plays
        # (the song's on Song and Mix)
        strip = svc.templates.get_template("fragments/status_bar.html")
        oob = {**ctx, "oob": True}
        # the tab's name (htmx applies a swap's <title>, svg titles aside)
        title = f"<title>{escape(ctx['view_title'])}</title>"
        resp = HTMLResponse(title + html + drawer.render(oob) + strip.render(oob))
        # one URL, two answers (the fragment, the whole page): a cache must
        # never hand the fragment to a cold load, nor the page to a swap
        resp.headers["Vary"] = "HX-Request"
        if notice:
            toast = {"studio-toast": {"message": notice}}
            resp.headers["HX-Trigger"] = json.dumps(toast)
        if name in ("song", "mixer", "lab", "clips"):
            # the Song page plays /song/plan.json, never a loop preview:
            # rendering one here was pure cost on every swap to it (and
            # the Sample Lab auditions regions of an imported song, the
            # clip hub its clips)
            return resp
        try:  # cue this view's own mix so Play is right immediately
            for k, v in svc.preview_trigger(view=name).items():
                resp.headers[k] = v
        except RenderError:
            pass  # the view still loads; Play will surface the toast
        return resp
    ctx["view_template"] = f"views/{name}.html"
    resp = svc.templates.TemplateResponse(request, "view_page.html", ctx)
    resp.headers["Vary"] = "HX-Request"
    return resp


@router.post("/fragment/preview")
def fragment_preview(request: Request, form: StrForm, svc: Svc) -> Response:
    """Render the current view's mix (audio.js's Play fallback). `play`:
    and start it — an empty state's "Play the loop"
    (`fragments/empty_play.html`), where no ▶ of the page's own is near."""
    view = form.get("view") or svc.view_of(request)
    resp = svc.do_render(request, view=view if view in VIEW_PARTS else "perform")
    return autoplay(resp) if form.get("play") else resp


@router.post("/fragment/undo")
def fragment_undo(request: Request, svc: Svc) -> Response:
    """Pop one undo step. Answers like /fragment/reset — the current
    view's full page — so the sliders show the restored values."""
    s: StudioState = svc.state
    if not s.pop_history():
        return Response(status_code=204)
    s.save()
    try:
        headers = svc.preview_trigger(view=svc.view_of(request))
    except RenderError as err:
        said = {"message": str(err), "kind": "error"}
        headers = {"HX-Trigger": json.dumps({"studio-toast": said})}
    return svc.full_view_page(request, headers)


@router.get("/fragment/map")
def fragment_map(request: Request, svc: Svc) -> Response:
    return HTMLResponse(map_fragment(svc))


@router.get("/fragment/module/{key}")
def fragment_module(request: Request, key: str, svc: Svc) -> Response:
    """A module a page does not draw by default, added from its Layout menu
    (`workspace.MODULES` says which page may ask for which). It is drawn by
    the fragment that draws it where it is at home, for the asking page:
    the FX rack on Perform is the loop's layer and master racks, its posts
    re-cue Perform's preview (routes/fx.py `view_of`). No region watches
    it: workspace.js fetches it again after the page's own posts."""
    view = svc.view_of(request)
    if view not in workspace.MODULES.get(key, ()):
        return Response(status_code=404)
    return HTMLResponse(fx_rack_fragment(svc, view))


@router.post("/fragment/bass/key")
def bass_key(request: Request, form: StrForm, svc: Svc) -> Response:
    """Bass Lab's quick key: moves the "bass" view's phrase to `root`
    (`render_inputs`), never the grid or the arrangement's key."""
    r = form.get("root", "")
    if r not in ROOTS:
        return Response(status_code=404)
    svc.state.bass_root = r
    return svc.do_render(request, view="bass")


@router.get("/fragment/bass")
def fragment_bass(request: Request, svc: Svc) -> Response:
    """Bass Lab's panels, measured on the requesting view's ▶ render."""
    try:
        audio = svc.render(svc.view_of(request)).audio()
    except RenderError as err:
        return svc.toast(str(err))
    panels = bass_panels(audio, SR)
    # Synths: the sub spectrum is a module of its own there,
    # sent out of band beside the Bass check's cards
    split = svc.view_of(request) == "editor"
    html = svc.templates.get_template("fragments/bass_panels.html").render(
        **panels, split=split
    )
    return HTMLResponse(html)


@router.post("/fragment/bass/phone-audition")
def phone_audition(request: Request, svc: Svc) -> Response:
    """The requesting view's ▶ render on a phone speaker (`POSTS["phone"]`)."""
    try:
        result = svc.render(svc.view_of(request), post="phone")
    except RenderError as err:
        return svc.toast(str(err))
    trigger = json.dumps({"preview-ready": {"url": preview_url(result)}})
    return HTMLResponse("", headers={"HX-Trigger": trigger})


@router.get("/fragment/palette")
def palette_found(request: Request, svc: Svc) -> Response:
    """The command palette's search: sounds of every shelf and clips whose
    name holds the query, as the app's own controls for them
    (`palette.search`, fragments/palette_found.html). Reads names only."""
    q = request.query_params.get("q", "")[: palette.MAX_QUERY_CHARS]
    s: StudioState = svc.state
    found = palette.search(svc.data_dir, s.list_presets_meta(), q)
    tpl = svc.templates.get_template("fragments/palette_found.html")
    return HTMLResponse(tpl.render({"found": found, "q": q}))


@router.post("/fragment/load")
def fragment_load(request: Request, form: StrForm, svc: Svc) -> Response:
    """Use on the Map: loads the point's sound and stays on the map."""
    sound = sounds.find(svc, "map", form.get("id", ""))
    if sound is None:
        return svc.toast("Sound not found — try the map again.")
    return svc.adopt(request, sound)
