"""FX racks: add, remove, reorder and set units in the layer, master or
drum-bus chain.

**Where the keyboard goes.** A parameter change morphs the rack, so the
slider keeps its node and its focus. Add, move and remove change the
rack's shape and replace it (`outerHTML`), which destroys the control that
was pressed — and no morph can say "focus the unit that was just made" or
follow a unit to its new place (units have no identity: a chain is a list
of `{kind, params}`, and an id on each would change what a session saves —
only a unit a song's automation lane targets carries one, `fxrack.unit_id`,
and add / set / move keep it).
So the route, which knows what it did, names the control that should have
the keyboard next (`fx_focus` = (scope, index, which)), the template puts
`autofocus` on it, and htmx's own settle focuses it (htmx 4 focuses the
first `[autofocus]` in swapped-in content). No script restores anything.

**Undo.** Every edit that moves a chain is one step of the patch ring
(`state.push_fx_undo`): "Undo the last tweak" (Ctrl+Z on Synths, the reset
menu's ↩ on every page that has it) takes it back. A knob's drag is one
step, as a patch slider's is.
"""

from __future__ import annotations

import copy
import json

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ..fxrack import (
    CHAIN_PRESETS,
    FX_UNITS,
    MAX_UNITS,
    RACK_PRESSES,
    RackError,
    chain_preset,
    clean_chain,
    edit_chain,
)
from ..render import RenderError
from ..routing import StrForm, Svc, autoplay, studio_router
from ..service import StudioService
from ..state import StudioState

router = studio_router()


Focus = tuple[str, int, str]
"""(scope, unit index, which): the rack control a response gives the
keyboard — a unit's first "param", its "earlier" / "later" / "remove"
button, or the scope's "adder" (index unused)."""


def fx_rack_fragment(
    svc: StudioService, view: str = "editor", focus: Focus | None = None
) -> str:
    s: StudioState = svc.state
    return svc.templates.get_template("fragments/fx_rack.html").render(
        fx_units=FX_UNITS,
        chain_presets=CHAIN_PRESETS,
        fx_scopes=svc.fx_scopes_for(view),
        active_layer=s.active_layer,
        fx_focus=focus,
    )


def _first_control(scope: str, chain: list, index: int) -> Focus:
    """A unit's first control: its first parameter, or — a unit with none —
    its remove button."""
    has = bool(FX_UNITS[chain[index]["kind"]].get("params"))
    return (scope, index, "param" if has else "remove")


def _fx_response(
    svc: StudioService,
    request: Request | None = None,
    focus: Focus | None = None,
    undo: tuple[str, list, str | None] | None = None,
) -> Response:
    """The racks again, after an edit; `undo` (scope, the chain as it was,
    the key a drag's posts share) makes the edit a step of the patch ring."""
    s: StudioState = svc.state
    if undo is not None:
        s.push_fx_undo(*undo)
    s.save_soon()
    view = svc.view_of(request) if request else "editor"
    if view == "mixer":
        # the Mix view's rack is the master chain the SONG plays through:
        # there is no loop preview to cue there; the player re-fetches the
        # plan on `song-changed` (every section's key holds the chain)
        changed = {"song-changed": {"sections": []}}
        return HTMLResponse(
            fx_rack_fragment(svc, view, focus),
            headers={"HX-Trigger": json.dumps(changed)},
        )
    try:
        headers = svc.preview_trigger(view=view)
    except RenderError as err:
        return svc.toast(str(err))
    return HTMLResponse(fx_rack_fragment(svc, view, focus), headers=headers)


@router.post("/fragment/fx/add")
def fx_add(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    chain = s.fx_chain(form.get("scope", ""))
    kind = form.get("kind", "")
    if chain is None or kind not in FX_UNITS:
        return Response(status_code=422)
    if len(chain) >= MAX_UNITS:
        return svc.toast(f"That rack is full ({MAX_UNITS} units).")
    before = copy.deepcopy(chain)
    chain.append({"kind": kind, "params": {}})
    chain[:] = clean_chain(chain)
    # the keyboard follows the new unit: its first control
    scope = form["scope"]
    focus = _first_control(scope, chain, len(chain) - 1)
    resp = _fx_response(svc, request, focus, (scope, before, None))
    if not form.get("play"):
        return resp
    # `play` (an empty rack's "+ Reverb"): the first unit is heard at once —
    # the loop on a loop view; on the Mix view, where the rack is the
    # song's master, the song's first section (song.js, `song-autoplay`)
    sections = s.song["sections"]
    if svc.view_of(request) == "mixer" and sections:
        trig = json.loads(resp.headers.get("HX-Trigger") or "{}")
        trig["song-autoplay"] = {"section": sections[0]["id"]}
        resp.headers["HX-Trigger"] = json.dumps(trig)
    return autoplay(resp)


@router.post("/fragment/fx/chain")
def fx_chain(request: Request, form: StrForm, svc: Svc) -> Response:
    """The rack's "+ add chain…": a whole chain preset (`fxrack.CHAIN_PRESETS`) onto
    the end of a scope's chain, as ordinary editable units — all of it or,
    when the rack hasn't room, none of it (half a Phone Mic is no phone)."""
    s: StudioState = svc.state
    chain = s.fx_chain(form.get("scope", ""))
    units = chain_preset(form.get("chain", ""))
    if chain is None or units is None:
        return Response(status_code=422)
    if len(chain) + len(units) > MAX_UNITS:
        label = CHAIN_PRESETS[form["chain"]]["label"]
        free = MAX_UNITS - len(chain)
        return svc.toast(
            f"{label} needs {len(units)} free units; that rack has {free}."
        )
    before, first = copy.deepcopy(chain), len(chain)
    chain.extend(units)
    chain[:] = clean_chain(chain)
    # the first unit the preset added
    scope = form["scope"]
    focus = _first_control(scope, chain, first)
    return _fx_response(svc, request, focus, (scope, before, None))


@router.post("/fragment/fx/remove")
def fx_remove(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    chain = s.fx_chain(form.get("scope", ""))
    try:
        index = int(form.get("index", ""))
    except ValueError:
        return Response(status_code=422)
    if chain is None or not 0 <= index < len(chain):
        return Response(status_code=422)
    before = copy.deepcopy(chain)
    del chain[index]
    # the unit that took its place (the next one), else the scope's adder
    scope = form["scope"]
    focus = (scope, index, "remove") if index < len(chain) else (scope, 0, "adder")
    return _fx_response(svc, request, focus, (scope, before, None))


@router.post("/fragment/fx/move")
def fx_move(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    chain = s.fx_chain(form.get("scope", ""))
    try:
        index = int(form.get("index", ""))
        delta = int(form.get("dir", ""))
    except ValueError:
        return Response(status_code=422)
    if chain is None or delta not in (-1, 1) or not 0 <= index < len(chain):
        return Response(status_code=422)
    other = index + delta
    which = "earlier" if delta < 0 else "later"
    scope = form["scope"]
    if not 0 <= other < len(chain):
        # edge: nothing to do — but redraw *this* view's racks (Drums has
        # the drum bus, not the layer + master pair); the button keeps focus
        focus = (scope, index, which)
        return HTMLResponse(fx_rack_fragment(svc, svc.view_of(request), focus))
    before = copy.deepcopy(chain)
    chain[index], chain[other] = chain[other], chain[index]
    # the same button on the unit that moved, at its new place: pressing
    # it again carries the unit on
    return _fx_response(svc, request, (scope, other, which), (scope, before, None))


@router.post("/fragment/fx/set")
def fx_set(request: Request, form: StrForm, svc: Svc) -> Response:
    """One setting of a unit: `fxrack.edit_chain`'s set, as every rack's —
    a parameter, the pattern, a step of it, its length, a gate's rhythm, or
    a preset recalled by name (`name=recall`, `value` a name or
    "defaults"). Refused (422): whatever `edit_chain` refuses."""
    s: StudioState = svc.state
    scope = form.get("scope", "")
    chain = s.fx_chain(scope)
    if chain is None:
        return Response(status_code=422)
    try:
        edited, _said, at = edit_chain(chain, "set", form)
    except RackError:
        return Response(status_code=422)
    before = copy.deepcopy(chain)
    chain[:] = edited
    # a knob's posts join one undo step; a press is a step of its own
    name = form.get("name", "")
    key = None if name in RACK_PRESSES else f"fx:{scope}:{at}:{name}"
    return _fx_response(svc, request, undo=(scope, before, key))
