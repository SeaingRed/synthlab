"""What the command palette's search finds: sounds and clips by name.

The palette (static/palette.js, fragments/palette.html) lists the page's
own commands — every element that declares `data-cmd` — and asks the
server for the things that are not on the page: the sounds of every shelf
and the clips in the store. `search` answers with rows; the template
(fragments/palette_found.html) draws each as the control the app already
has for it (a shelf sound's Use post, the clip drawer's opener), so a
result goes through the same route as a click on its card.

Names only: nothing is rendered, measured or opened (a clip's WAV is
never read — its name is its file's stem).
"""

from __future__ import annotations

from pathlib import Path

from .clips import clip_dir
from .factory import FACTORY
from .library import LIBRARY
from .recipes import RECIPES

MAX_QUERY_CHARS = 60
PER_GROUP = 8

# (group, hint, the route its Use posts to)
_YOURS = ("Your sounds", "use this sound", "/fragment/preset/load")
_FACTORY = ("Factory sounds", "use this sound", "/fragment/factory/load")
_LIBRARY = ("Library", "use this sound", "/fragment/library/load")
_RECIPES = ("Recipes", "use this sound, with its effects", "/fragment/recipe/load")


def _match(q: str, name: str, tags=()) -> bool:
    hay = f"{name} {' '.join(tags)}".lower()
    return all(word in hay for word in q.split())


def _sounds(q: str, shelf: tuple[str, str, str], entries) -> list[dict]:
    group, hint, post = shelf
    # a library sound is found by its older name too (its `was`: "glass"
    # still finds what was the Glass Ray)
    names = [
        e["name"]
        for e in entries
        if _match(q, e["name"], (*e.get("tags", ()), e.get("was", "")))
    ]
    # a name that starts with the query before one that merely holds it
    names.sort(key=lambda n: (not n.lower().startswith(q), n.lower()))
    return [
        {"kind": "sound", "name": n, "group": group, "hint": hint, "post": post}
        for n in names[:PER_GROUP]
    ]


def search(data_dir: Path, presets: list[dict], query: str) -> list[dict]:
    """Rows for `query` (cut to `MAX_QUERY_CHARS`): the user's sounds, the
    factory ones, the library, the recipes, then clips. Empty for an empty
    query — the palette then shows the page's commands alone."""
    q = " ".join(query[:MAX_QUERY_CHARS].lower().split())
    if not q:
        return []
    rows = (
        _sounds(q, _YOURS, presets)
        + _sounds(q, _FACTORY, FACTORY)
        + _sounds(q, _LIBRARY, LIBRARY)
        + _sounds(q, _RECIPES, RECIPES)
    )
    clips = sorted(p.stem for p in clip_dir(data_dir).glob("*.wav"))
    hits = [c for c in clips if _match(q, c)]
    hits.sort(key=lambda n: (not n.lower().startswith(q), n.lower()))
    rows += [{"kind": "clip", "name": c} for c in hits[:PER_GROUP]]
    return rows
