"""The sound library: audition a shelf sound, and Use it (landing on
Synths) — the generated families and the genre recipes (`recipes.py`)."""

from __future__ import annotations

from fastapi import Request, Response

from .. import sounds
from ..routing import RawForm, Svc, studio_router

router = studio_router()


@router.post("/fragment/library/load")
def library_load(request: Request, form: RawForm, svc: Svc) -> Response:
    """Use: from the shelf it lands on Synths, URL and all (`svc.adopt`)."""
    sound = sounds.find(svc, "library", str(form.get("name", "")))
    if sound is None:
        return svc.toast("Library sound not found.")
    return svc.adopt(request, sound, str(form.get("as", "")))


@router.post("/fragment/recipe/load")
def recipe_load(request: Request, form: RawForm, svc: Svc) -> Response:
    """Use a genre recipe from the Recipes shelf: its patch, FX, curves and
    (when it has them) layers, landing on Synths like any shelf sound."""
    sound = sounds.find(svc, "recipes", str(form.get("name", "")))
    if sound is None:
        return svc.toast("Recipe not found.")
    return svc.adopt(request, sound, str(form.get("as", "")))


@router.get("/recipe/{name}/audition.wav")
def recipe_audition(name: str, svc: Svc, mix: bool = False) -> Response:
    """On its own by default (its own FX and layers); `?mix=1` = exactly
    what Use lands on Synths."""
    sound = sounds.find(svc, "recipes", name)
    return svc.audition(sound, mix=mix) if sound else Response(status_code=404)


@router.get("/library/{name}/audition.wav")
def library_audition(name: str, svc: Svc, mix: bool = False) -> Response:
    """On its own by default; `?mix=1` = exactly what Use lands on Synths."""
    sound = sounds.find(svc, "library", name)
    return svc.audition(sound, mix=mix) if sound else Response(status_code=404)
