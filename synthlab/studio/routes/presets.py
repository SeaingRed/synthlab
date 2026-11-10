"""Saved sounds: presets, factory sounds, panel presets, and swapping
back to the sound from before the last load."""

from __future__ import annotations

import json

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from .. import sounds
from ..factory import FACTORY
from ..routing import RawForm, StrForm, Svc, prompt_value, studio_router
from ..service import (
    StudioService,
    group_specs,
    library_by_family,
    recipe_cards,
)
from ..state import StudioState

router = studio_router()


def preset_list_fragment(svc: StudioService, just_saved: str = "") -> HTMLResponse:
    s: StudioState = svc.state
    html = svc.templates.get_template("fragments/presets.html").render(
        presets=sounds.yours(s),
        just_saved=just_saved,
        factory=[{k: e[k] for k in ("name", "tags", "spectrum")} for e in FACTORY],
        # the dock's other shelves (the page's own "library" section)
        library=library_by_family(),
        recipes=recipe_cards(),
    )
    return HTMLResponse(html)


@router.post("/fragment/preset/save")
def preset_save(request: Request, form: RawForm, svc: Svc) -> Response:
    name = str(form.get("name", "untitled"))
    svc.state.save_preset(name, include_layers=form.get("include_layers") == "on")
    svc.state.save()
    # where it went, said and shown: the row is lit and scrolled to (audio.js)
    kept = svc.state.patch.name
    resp = preset_list_fragment(svc, just_saved=kept)
    message = f"Saved “{kept}” — in Yours, the Sounds list"
    resp.headers["HX-Trigger"] = json.dumps({"studio-toast": {"message": message}})
    return resp


@router.post("/fragment/preset/rename")
def preset_rename(request: Request, form: RawForm, svc: Svc) -> Response:
    new = prompt_value(request) or str(form.get("new", ""))
    s: StudioState = svc.state
    if not new.strip() or not s.rename_preset(str(form.get("name", "")), new):
        return svc.toast("Couldn't rename — name taken or sound missing.")
    s.save()
    return preset_list_fragment(svc)


@router.post("/fragment/preset/load")
def preset_load(request: Request, form: RawForm, svc: Svc) -> Response:
    sound = sounds.find(svc, "preset", str(form.get("name", "")))
    if sound is None:
        return svc.toast("Preset not found.")
    return svc.adopt(request, sound, str(form.get("as", "")))


@router.post("/fragment/factory/load")
def factory_load(request: Request, form: RawForm, svc: Svc) -> Response:
    sound = sounds.find(svc, "factory", str(form.get("name", "")))
    if sound is None:
        return svc.toast("Factory sound not found.")
    return svc.adopt(request, sound, str(form.get("as", "")))


@router.post("/fragment/restore_prev")
def restore_prev(request: Request, svc: Svc) -> Response:
    """Swap the live sound with the pre-load stash — undo for a stray
    card click (a layered Sound's lanes, FX and curves included:
    `StudioState.swap_prev`). Swapping (not restoring) means pressing it
    twice is a no-op, never a loss."""
    s: StudioState = svc.state
    if not s.swap_prev():
        return svc.toast("Nothing to go back to — no sound has been loaded yet.")
    s.ab_live = None  # another sound: in neither A/B slot
    s.save()
    return svc.full_page_with_preview(request)


@router.get("/factory/{name}/audition.wav")
def factory_audition(name: str, svc: Svc, mix: bool = False) -> Response:
    sound = sounds.find(svc, "factory", name)
    return svc.audition(sound, mix=mix) if sound else Response(status_code=404)


def panel_presets_fragment(svc: StudioService, group: str) -> HTMLResponse:
    s: StudioState = svc.state
    html = svc.templates.get_template("fragments/panel_presets.html").render(
        group=group, presets=s.list_panel_presets(group)
    )
    return HTMLResponse(html)


@router.post("/fragment/panel/preset/save")
def panel_preset_save(request: Request, form: StrForm, svc: Svc) -> Response:
    group = form.get("group", "")
    if group_specs(group) is None:
        return Response(status_code=404)
    name = prompt_value(request) or form.get("name", "untitled")
    s: StudioState = svc.state
    s.save_panel_preset(group, name)
    return panel_presets_fragment(svc, group)


@router.post("/fragment/panel/preset/load")
def panel_preset_load(request: Request, form: StrForm, svc: Svc) -> Response:
    group = form.get("group", "")
    if group_specs(group) is None:
        return Response(status_code=404)
    s: StudioState = svc.state
    try:
        s.load_panel_preset(group, form.get("name", ""))
    except (OSError, ValueError, KeyError, TypeError):
        return svc.toast("Preset not found.")
    s.save()
    return svc.full_page_with_preview(request)


@router.post("/fragment/preset/delete")
def preset_delete(request: Request, form: RawForm, svc: Svc) -> Response:
    svc.state.delete_preset(str(form.get("name", "")))
    return preset_list_fragment(svc)


@router.post("/fragment/preset/star")
def preset_star(request: Request, form: RawForm, svc: Svc) -> Response:
    svc.state.toggle_star(str(form.get("name", "")))
    return preset_list_fragment(svc)


@router.get("/fragment/sound-menu")
def sound_menu(request: Request, svc: Svc) -> Response:
    """The one sound menu again (`fragments/sound_menu.html`, base.html's
    `#lane-sounds`): soundmenu.js asks for it as a lane head's menu opens,
    so a sound saved, starred or renamed since the page was loaded is in
    it — a view swap never redraws base.html's copy. Changes nothing (a
    missing meta file is backfilled, as Synths' list does)."""
    tpl = svc.templates.get_template("fragments/sound_menu.html")
    return HTMLResponse(tpl.render(sound_menu=sounds.menu(svc.state)))


@router.get("/preset/{name}/audition.wav")
def preset_audition(name: str, svc: Svc, mix: bool = False) -> Response:
    """A format-2 preset's ▶ renders the FILE's chain (`Sound.chain`)."""
    sound = sounds.find(svc, "preset", name)
    return svc.audition(sound, mix=mix) if sound else Response(status_code=404)
