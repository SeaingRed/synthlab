"""Perform: launches queued at the loop wrap (pool beats, a lane's sound),
the loop's parts and the drum XY pad. The launcher's slots and scenes are
routes/slots.py's. The song itself is arranged on the Song page:
Perform links there."""

from __future__ import annotations

import json

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from .. import sounds
from ..render import RenderError
from ..routing import StrForm, Svc, pattern_edit, studio_router
from ..service import DXY_AXES, StudioService, dxy_spec
from ..state import StudioState

router = studio_router()


@router.post("/fragment/perform/xy")
def fragment_perform_xy(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    try:
        nx = min(1.0, max(0.0, float(form.get("x", 0))))
        ny = min(1.0, max(0.0, float(form.get("y", 0))))
    except ValueError:
        nx = ny = 0.0
    for (voice, param), n in zip(DXY_AXES, (nx, ny), strict=True):
        spec = dxy_spec(voice, param)
        merged = dict(s.arrangement.drum_params.get(voice, {}))
        merged[param] = spec["min"] + n * (spec["max"] - spec["min"])
        s.arrangement.drum_params[voice] = merged
    return svc.do_render(request)


@router.post("/fragment/perform/launch/{pattern_id}")
@pattern_edit
def fragment_perform_launch(request: Request, pattern_id: str, svc: Svc) -> Response:
    """Launch a pool drums pattern on the working loop at the wrap."""
    s: StudioState = svc.state
    if not s.apply_pool_beat(pattern_id):
        return Response(status_code=404)
    # the launcher itself is left alone: perform.js is showing this slot
    # "queued" until the wrap, and a re-render would wipe that
    svc.suppress("launcher")
    return _queued(svc)


@router.post("/fragment/perform/bank/{slot}")
@pattern_edit
def fragment_perform_bank(request: Request, slot: int, svc: Svc) -> Response:
    """The Beats launcher while the song's pool holds no drums pattern: a
    Drums bank slot, loaded as the bank loads it (`bank_load`: the active
    slot follows), at the wrap like a pool beat."""
    s: StudioState = svc.state
    if not s.bank_load(slot):
        return Response(status_code=404)
    svc.suppress("launcher")  # perform.js shows the slot "queued"
    return _queued(svc)


def _queued(svc: StudioService) -> Response:
    """The launch/parts/sound/scene tail: save, queue the next preview
    at the loop wrap, RenderError -> a plain toast (no still_playing —
    the loop that's still playing is the point of "queued"). The regions
    the launch changed come back OOB by themselves."""
    s: StudioState = svc.state
    s.save()
    try:
        headers = svc.preview_trigger(view="perform")
    except RenderError as err:
        return svc.toast(str(err))
    trig = json.loads(headers["HX-Trigger"])
    trig["preview-ready"]["queue"] = True
    return HTMLResponse("", headers={"HX-Trigger": json.dumps(trig)})


@router.post("/fragment/perform/parts")
def fragment_perform_parts(request: Request, form: StrForm, svc: Svc) -> Response:
    """Which parts the Perform loop carries: synth, drums, or both."""
    s: StudioState = svc.state
    part = form.get("part", "")
    if part not in ("synth", "drums"):
        return Response(status_code=422)
    parts = set(s.perform_parts)
    parts ^= {part}
    if not parts:
        parts = {part}  # never nothing: toggling the last one keeps it
    s.perform_parts = [p for p in ("synth", "drums") if p in parts]
    return _queued(svc)


@router.post("/fragment/perform/lane/sound")
def fragment_perform_lane_sound(request: Request, form: StrForm, svc: Svc) -> Response:
    """A lane head's sound ▾ (transport.html; the menu is soundmenu.js
    over base.html's `#lane-sounds`): `sound` ("<source>:<name>", as
    `sounds.find` knows it) into lane `index`, replacing what `as` says
    (`StudioState.LOAD_AS`) — `StudioState.lane_sound`: that lane becomes
    the edited one, one pattern undo step. Answered as a launch is: the
    loop changes at the wrap, the keys at once (the preview's
    `preview-ready` re-reads the live voice's patch, as a Synths load
    does)."""
    s: StudioState = svc.state
    try:
        index = int(form.get("index", ""))
    except ValueError:
        return Response(status_code=422)
    source, _, name = form.get("sound", "").partition(":")
    how = form.get("as", "")
    if (
        not 0 <= index < len(s.layers)
        or source not in sounds.SOURCES
        or not name
        or how not in s.LOAD_AS
    ):
        return Response(status_code=422)
    sound = sounds.find(svc, source, name)
    if sound is None:
        return svc.toast("That sound is gone.")
    if not s.lane_sound(index, sound.patch, sound.chain, how=how, label=sound.label):
        return svc.toast("Every layer holds a sound — clear one first.")
    return _queued(svc)


# (a song section is not launched here: the Loops panel's "sections" open
# one as a scene of the launcher — routes/slots.py,
# `/fragment/perform/scene/section`)
