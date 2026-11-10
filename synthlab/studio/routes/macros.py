"""Macro knobs and the XY pad: moves, assignments, axes and gestures."""

from __future__ import annotations

import json
import math
from dataclasses import replace

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ...params import PARAMS
from ..curves import CURVABLE
from ..paramui import CONTINUOUS_PARAMS, MACRO_PARAMS, PERFORMANCE_PARAMS, spec_of
from ..routing import RawForm, StrForm, Svc, studio_router
from ..service import StudioService, xy_value
from ..state import StudioState

router = studio_router()


def macro_fragment(svc: StudioService) -> str:
    return svc.templates.get_template("fragments/macros.html").render(
        **svc.macro_context()
    )


def apply_macro(s: StudioState, index: int) -> list[str]:
    """Write a macro's targets onto the patch. A target's `amt` is a
    signed fraction of the param's full range: +1 sweeps min->max as the
    knob goes 0->1, -1 sweeps max->min. Reuses _xy_value so a macro and
    the XY pad map a knob onto a param the same (log-aware) way. Never a
    performance control (`MACRO_PARAMS`): the pad refuses to write
    `pitch_bend` into the patch, and so does a macro."""
    m = s.macros[index]
    v = min(1.0, max(0.0, float(m.get("value") or 0.0)))
    moved: list[str] = []
    for t in m.get("targets") or []:
        name, amt = t.get("param"), float(t.get("amt") or 0.0)
        if spec_of(name) is None or name not in MACRO_PARAMS:
            continue
        n = v * amt if amt >= 0 else 1.0 + v * amt
        s.patch = replace(s.patch, **{name: xy_value(name, min(1.0, max(0.0, n)))})
        moved.append(name)
    return moved


@router.post("/fragment/macro/{index}")
def fragment_macro(index: int, request: Request, form: StrForm, svc: Svc) -> Response:
    """Move one macro. Posts like any live tweak: the patch changes and
    the normal preview path re-renders."""
    s: StudioState = svc.state
    if not 0 <= index < len(s.macros):
        return Response(status_code=404)
    try:
        s.macros[index]["value"] = min(1.0, max(0.0, float(form.get("value", 0))))
    except ValueError:
        return Response(status_code=422)
    s.push_history()
    moved = apply_macro(s, index)
    s.save_soon()
    # the moved macro also moves its target sliders, so those rows come
    # back OOB. Swapping the whole body instead would rebuild ~2800 nodes
    # on every drag frame, which is exactly what a macro drag cannot afford.
    return svc.do_render(request, param_rows_oob(svc, moved), view="editor")


def param_rows_oob(svc: StudioService, names: list[str]) -> str:
    s: StudioState = svc.state
    tpl = svc.templates.get_template("fragments/param_row.html")
    return "".join(
        tpl.render(
            spec=spec_of(n),
            patch_values={n: getattr(s.patch, n)},
            curvable=set(CURVABLE),
            has_curve={k for k, pts in s.curves.items() if len(pts) >= 2},
            oob=True,
        )
        for n in names
        if spec_of(n) is not None
    )


@router.post("/fragment/macro/{index}/assign")
def fragment_macro_assign(
    index: int, request: Request, form: RawForm, svc: Svc
) -> Response:
    """Set one macro's targets: repeated `param` + `amt` form fields."""
    s: StudioState = svc.state
    if not 0 <= index < len(s.macros):
        return Response(status_code=404)
    params = [str(v) for v in form.getlist("param")]
    amts = [str(v) for v in form.getlist("amt")]
    targets = []
    for name, amt in zip(params, amts, strict=False):
        if name in MACRO_PARAMS:
            try:
                value = float(amt)
            except ValueError:
                continue
            # nan/inf used to be stored and pinned the target at an extreme
            if math.isfinite(value):
                targets.append({"param": name, "amt": max(-1.0, min(1.0, value))})
    s.macros[index]["targets"] = targets[:3]
    name = str(form.get("name") or "").strip()
    if name:
        s.macros[index]["name"] = name[:20]
    s.save()
    return HTMLResponse(macro_fragment(svc))


@router.post("/fragment/xy")
def fragment_xy(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    try:
        nx = min(1.0, max(0.0, float(form.get("x", 0))))
        ny = min(1.0, max(0.0, float(form.get("y", 0))))
    except ValueError:
        nx = ny = 0.0
    xparam, yparam = s.xy_axes
    moves = _patch_axes({xparam: nx, yparam: ny})
    if not moves:  # both axes bend: a performance, nothing to render
        return Response(status_code=204)
    s.push_history()
    s.patch = replace(s.patch, **moves)
    # the pad moved two sliders: their rows come back OOB (as a macro
    # move's do), or the next slider edit posts their stale values back.
    # The rows (and the pad) are on Synths only.
    rows = param_rows_oob(svc, list(moves))
    return svc.do_render(request, rows if svc.view_of(request) == "editor" else "")


# XY axes that are performances, not sound edits: "Pitch bend" on the pad is
# the keyboard's bend wheel (piano.js setWheel: every lane and key, springing
# back on release), so the pad never writes it into the patch — a static
# bend would transpose the loop. A drawn bend is the pitch_bend curve.
# The one set (`paramui.PERFORMANCE_PARAMS`): macros refuse it as a target.
PERFORMANCE_AXES = PERFORMANCE_PARAMS


def _patch_axes(positions: dict[str, float]) -> dict:
    return {
        name: xy_value(name, n)
        for name, n in positions.items()
        if name not in PERFORMANCE_AXES
    }


@router.post("/fragment/bend-range")
def fragment_bend_range(request: Request, form: StrForm, svc: Svc) -> Response:
    """The keyboard's bend-range menu (±2/±7/±12/±24): the edited sound's
    `bend_range`, which the live wheel and a drawn bend both scale by. Its
    Motion-panel row comes back OOB, or its next post would put it back."""
    s: StudioState = svc.state
    try:
        value = float(form.get("value", ""))
    except ValueError:
        return Response(status_code=422)
    if not math.isfinite(value):
        return Response(status_code=422)
    lo, hi = PARAMS["bend_range"].cap
    s.push_history()
    s.patch = replace(s.patch, bend_range=min(hi, max(lo, value)))
    rows = param_rows_oob(svc, ["bend_range"])
    return svc.do_render(request, rows if svc.view_of(request) == "editor" else "")


@router.post("/fragment/xy/center")
def fragment_xy_center(request: Request, svc: Svc) -> Response:
    """Snap both axes to 50%. The cross tracks the real parameter values,
    so it pins to an edge whenever one sits at its minimum — this moves
    the params rather than faking a centred cross."""
    s: StudioState = svc.state
    xparam, yparam = s.xy_axes
    moves = _patch_axes({xparam: 0.5, yparam: 0.5})
    # an edit like a pad move: without its own undo step, Ctrl+Z skipped
    # the values it replaced and undid the edit before it as well
    s.push_history()
    s.patch = replace(s.patch, **moves)
    s.save_soon()
    extra = param_rows_oob(svc, list(moves)) + svc.xy_fragment(oob=True)
    return svc.do_render(request, extra, view="editor")


@router.post("/fragment/xy/axes")
def fragment_xy_axes(request: Request, form: StrForm, svc: Svc) -> Response:
    xp, yp = form.get("xy_x", ""), form.get("xy_y", "")
    if xp not in CONTINUOUS_PARAMS or yp not in CONTINUOUS_PARAMS:
        return Response(status_code=404)
    s: StudioState = svc.state
    s.xy_axes = [xp, yp]
    s.save()
    return HTMLResponse(svc.xy_fragment())


@router.post("/fragment/xy/gesture")
def fragment_xy_gesture(request: Request, form: StrForm, svc: Svc) -> Response:
    try:
        points = json.loads(form.get("points", ""))
    except (ValueError, TypeError):
        return Response(status_code=422)
    if not isinstance(points, list) or len(points) > 300:
        return Response(status_code=422)
    cleaned: list[list[float]] = []
    for p in points:
        if not isinstance(p, list) or len(p) != 3:
            return Response(status_code=422)
        try:
            phase, x, y = float(p[0]), float(p[1]), float(p[2])
        except (TypeError, ValueError, OverflowError):  # a 400-digit JSON int
            return Response(status_code=422)
        if not (math.isfinite(phase) and math.isfinite(x) and math.isfinite(y)):
            return Response(status_code=422)
        if phase < 0 or not (0.0 <= x <= 1.0) or not (0.0 <= y <= 1.0):
            return Response(status_code=422)
        cleaned.append([phase, x, y])
    s: StudioState = svc.state
    s.xy_gesture = cleaned
    s.save()
    return HTMLResponse(svc.xy_fragment())
