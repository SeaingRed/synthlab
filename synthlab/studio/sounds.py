"""Browse, audition, Use: every place the studio offers a sound from.

A browse surface — the Library shelf, the factory and saved sounds in the
Sounds dock, a Breed card, a point on the Map, Perform's sound launcher —
does three things with a sound: finds it by the id its page carries, plays
it (on its own, or in your mix), and Uses it. The last two used to be
hand-copied per route (stash, unmute, set the patch, baseline, save, land
on Synths from the source's own page), and the copies drifted: one forgot
the baseline, one landed without moving the URL, one auditioned a
different render from the one Use delivered.

Here a source only *finds* a `Sound`; everything after that is
`StudioService.audition(sound, mix=...)` and `StudioService.adopt(request,
sound)`, the same for every source. A new browse surface is one entry in
`SOURCES`, and `tests/studio/test_sound_sources.py` holds it to the
contract (mix audition == the landing == ▶ on Synths, alone == Synths in a
fresh session, baseline clean, lands on Synths from its home page only).
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..analysis.features import Features
from ..synth import Patch
from . import schema
from .audition import register_of
from .factory import get_factory
from .files import safe_name
from .library import get_library
from .recipes import get_recipe, recipe_chain, recipe_patch
from .state import _read_json, preset_from_json

if TYPE_CHECKING:
    from .service import StudioService


@dataclass(frozen=True)
class Sound:
    """A sound a browse surface offers.

    - `patch`: the sound itself.
    - `chain`: a format-2 preset's extras (`layers`, `main_fx`, `curves`,
      conformed): Use replaces the session's with them, so the audition
      renders them too. A layered breed card (or a Map point of one) is a
      Sound of the same shape. `layers` None: the chain brings main FX and
      curves but leaves the session's other lanes alone ("Use sound", a
      layered card's main only). None for every other source.
    - `label`: what the reset menu calls the baseline Use sets.
    - `home_view`: the page this source is browsed on. Use from there is a
      decision, so it lands on Synths (body and URL); Use from anywhere else
      stays put. None: the source's page is Synths itself, or Use there is
      a browse (the Map keeps you on the map).
    - `register`: the register the sound is heard in *on its own*
      (`audition.register_of`: "bass" / "pad" / "lead"), for the sources
      that measure their sounds — the audition log's rows. None: the
      phrase's usual listening register. "In your mix" and Use never read
      it: they play the session's own notes."""

    patch: Patch
    chain: dict | None = None
    label: str = ""
    home_view: str | None = None
    register: str | None = None


def _library(svc: StudioService, name: str) -> Sound | None:
    patch = get_library(name)
    return Sound(patch, None, patch.name, "library") if patch is not None else None


def _factory(svc: StudioService, name: str) -> Sound | None:
    """A factory sound: browsed in Synths' own Sounds dock (Use there stays
    put) and on the Library's
    Core shelf, which lists a few — Use from there lands on Synths as every
    shelf sound does."""
    patch = get_factory(name)
    return Sound(patch, None, patch.name, "library") if patch is not None else None


def _recipe(svc: StudioService, name: str) -> Sound | None:
    """A genre recipe (`recipes.py`): a format-2 Sound browsed on the
    Library's Recipes shelf, so Use from there lands on Synths."""
    entry = get_recipe(name)
    if entry is None:
        return None
    return Sound(recipe_patch(entry), recipe_chain(entry), entry["name"], "library")


def _preset(svc: StudioService, name: str) -> Sound | None:
    """A saved preset; its baseline is labelled with the name it was
    picked by (a v1 file's patch may carry another)."""
    path = svc.state.preset_dir() / f"{safe_name(name)}.json"
    if not path.exists():
        return None
    try:
        loaded = preset_from_json(_read_json(path))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if loaded is None:
        return None
    patch, chain = loaded
    return Sound(patch, chain, name)


def _row_chain(row: dict) -> dict | None:
    """A layered card's Sound (`genome.row_sound`, written beside `patch`),
    conformed as a v2 preset's chain is: one load for both. None for a
    plain row — every row written before layered breeding."""
    sound = row.get("sound")
    if not isinstance(sound, dict):
        return None
    chain = schema.conform({**sound, "patch": row["patch"]}, schema.nodes().PRESET_V2)
    chain.pop("patch", None)
    return chain


def _row_register(patch: Patch, row: dict, chain: dict | None) -> str:
    """The register a row's sound is auditioned in on its own
    (`audition.register_of`), from the features the breeder logged with it
    and the follow octaves of the layers this Sound brings."""
    try:
        features = Features(**row["features"])
    except (KeyError, TypeError):
        features = None  # no usable measure on the row: the patch decides
    layers = (chain or {}).get("layers") or []
    octaves = tuple(
        int((lay.get("follow") or {}).get("octave", 0))
        for lay in layers
        if lay.get("patch")
    )
    return register_of(patch, features, octaves)


def _space(
    home_view: str | None, main_only: bool = False
) -> Callable[[StudioService, str], Sound | None]:
    """A row of the audition log (`svc.space_store()`), by id: Breed's
    cards and the Map's points are both rows of it. A layered card is its
    whole Sound; `main_only` is its main patch with its main FX and curves
    and the session's own other lanes ("Use sound")."""

    def find(svc: StudioService, row_id: str) -> Sound | None:
        row = svc.space_store().get(row_id)
        if row is None:
            return None
        patch = Patch.from_dict(row["patch"])
        chain = _row_chain(row)
        if chain is not None and main_only:
            chain = {**chain, "layers": None}
        register = _row_register(patch, row, chain)
        return Sound(patch, chain, patch.name, home_view, register)

    return find


SOURCES: dict[str, Callable[[StudioService, str], Sound | None]] = {
    "library": _library,
    "factory": _factory,
    "recipes": _recipe,
    "preset": _preset,
    "breed": _space("breed"),
    # a layered card's "Use sound" / "▶ main only": the main lane alone
    "breed_main": _space("breed", main_only=True),
    "map": _space(None),
}


def find(svc: StudioService, source: str, ident: str) -> Sound | None:
    """The sound `ident` names in `source`, or None when there is none."""
    return SOURCES[source](svc, ident)


# --- the one sound menu ---------------------------------------------------------
# Every sound there is, as one menu: base.html's `#lane-sounds`, drawn once a
# page — a Song lane's <select> takes a copy as it is reached for, Perform's
# lane heads search it (soundmenu.js). An item is (source, name, layered,
# starred): the `SOURCES` name and the name `find` knows it by; whether it
# brings layers of its own (Load then asks what it replaces); a ★ of yours.

MenuItem = tuple[str, str, bool, bool]


@functools.lru_cache(maxsize=1)
def _shipped() -> tuple[tuple[str, tuple[MenuItem, ...]], ...]:
    """The sounds that ship — factory, recipes, the core library's
    categories, the library's families — as menu groups. Read once: they
    are files of the package, and reading the library and the recipes'
    cards for every Song context (each page, each region an edit redraws)
    took the Song page from 26 ms to 358."""
    from .factory import FACTORY
    from .library import by_family
    from .recipes import cards, core_cards

    return (
        ("Factory", tuple(("factory", e["name"], False, False) for e in FACTORY)),
        (
            "Recipes · with their FX",
            tuple(("recipes", c["name"], c["layered"], False) for c in cards()),
        ),
        *(
            (
                f"Core · {cat}",
                tuple((c["kind"], c["name"], c["layered"], False) for c in cs),
            )
            for cat, cs in core_cards()
        ),
        *(
            (fam, tuple(("library", m["name"], False, False) for m in ms))
            for fam, ms in by_family()
        ),
    )


@functools.lru_cache(maxsize=256)
def _layered_file(path: str, mtime_ns: int) -> bool:
    """Whether a saved sound brings layers (a format-2 file whose `layers`
    hold a sound), read once per version of the file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):  # (a hostile file: no layers)
        return False
    lanes = data.get("layers") if isinstance(data, dict) else None
    if not isinstance(lanes, list):
        return False
    return any(isinstance(lay, dict) and lay.get("patch") for lay in lanes)


def yours(state) -> list[dict]:
    """The saved sounds as Synths lists them (`list_presets_meta`: ★
    first), each with `layered` too: whether it brings layers of its own
    (`_layered_file`), so Load knows what it would replace."""
    out = []
    for meta in state.list_presets_meta():
        path = state.preset_dir() / f"{safe_name(meta['name'])}.json"
        try:
            layered = _layered_file(str(path), path.stat().st_mtime_ns)
        except OSError:
            layered = False
        out.append({**meta, "layered": layered})
    return out


def menu(state, saved: list[dict] | None = None) -> list[tuple[str, list[MenuItem]]]:
    """(group label, items): yours (`saved`, else read: `yours`), then the
    shipped groups (`_shipped`); an empty group is left out."""
    mine = [
        ("preset", m["name"], m["layered"], m["starred"])
        for m in (yours(state) if saved is None else saved)
    ]
    groups = [("Yours", mine), *((label, list(items)) for label, items in _shipped())]
    return [(label, items) for label, items in groups if items]
