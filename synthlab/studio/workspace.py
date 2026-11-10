"""The workspace's server half: each page's Layout menu — its named presets
and the modules it can add (frontend.md, "Workspace: modules you can move").

The layout itself is the browser's (static/workspace.js, localStorage): the
server's markup is always the default order, and nothing here is session
state. A preset is the record workspace.js keeps for a page, written as
data: `docks` — a tree per dock (`data-ws-dock` on the page: the view's key,
and Synths' "synths.panels") — and `fold`, the modules folded to their head.
A tree is a split `{"d": "row" | "col", "k": [nodes], "s": [sizes]}` (a
row's sizes are weights, a column's a height in px or None: its own) or a
group `{"t": [module ids], "a": the tab shown}`; a module a preset does not
name lands at the foot of its dock, so a new module is never lost.

A module a page can add is a visualiser (`vis`: the scope or the spectrum
slot's variation, drawn big from the shared frame), or a server fragment
(`url`, `GET /fragment/module/{key}`: `MODULES`), or the clip library's pin.
"""

from __future__ import annotations

from typing import Any

Tree = dict[str, Any]


def group(*ids: str, shown: int = 0) -> Tree:
    """One place: a module, or several as tabs (the first shown)."""
    return {"t": list(ids), "a": shown}


def row(*kids: Tree | str, weights: tuple[float, ...] | None = None) -> Tree:
    """Side by side; `weights` share the width (equal by default)."""
    k = [group(c) if isinstance(c, str) else c for c in kids]
    return {"d": "row", "k": k, "s": list(weights or [1] * len(k))}


def col(*kids: Tree | str) -> Tree:
    """One over another, each its own height."""
    k = [group(c) if isinstance(c, str) else c for c in kids]
    return {"d": "col", "k": k, "s": [None] * len(k)}


PANELS = tuple(
    f"synths.{n}"
    for n in (
        "source",
        "filter",
        "vowel",
        "amp-envelope",
        "character",
        "mod-matrix",
        "motion",
    )
)

# each busy page's presets, by view: (name, the page's record). The
# keyboard dock is not in them: it is every page's, in the
# page frame outside #view (fragments/keys_dock.html), where no layout moves
# it — its own fold (per page) is what stays of `synths.keys`
PRESETS: dict[str, list[tuple[str, dict]]] = {
    "editor": [
        # the panels as wide as the window allows
        (
            "Sound design",
            {
                "docks": {
                    "synths": row(
                        "synths.sounds",
                        col(
                            "synths.sound",
                            "synths.layers",
                            "synths.chain",
                            "synths.panels",
                            row("fx.layer", "fx.master"),
                            row(
                                col("synths.macros", "synths.xy"),
                                col("synths.bass", "synths.sub"),
                            ),
                        ),
                        weights=(1, 5),
                    ),
                },
            },
        ),
        # playing it: the macros and XY big; the panels one tab at a time
        (
            "Performance",
            {
                "docks": {
                    "synths": row(
                        col(
                            "synths.sound",
                            "synths.layers",
                            "synths.macros",
                            "synths.xy",
                        ),
                        col(
                            "synths.panels",
                            "synths.chain",
                            group("fx.layer", "fx.master"),
                            "synths.bass",
                            "synths.sub",
                        ),
                        group("synths.sounds"),
                        weights=(5, 4, 2),
                    ),
                    "synths.panels": group(*PANELS),
                },
                "fold": ["synths.chain"],
            },
        ),
        # the most on one screen: the sounds and the FX share a column, the
        # chain bar folded to its head, the play tools tabbed at the foot
        (
            "Compact",
            {
                "docks": {
                    "synths": row(
                        group("synths.sounds", "fx.layer", "fx.master"),
                        col(
                            "synths.sound",
                            "synths.layers",
                            "synths.chain",
                            "synths.panels",
                            group(
                                "synths.macros",
                                "synths.xy",
                                "synths.bass",
                                "synths.sub",
                            ),
                        ),
                        weights=(1, 4),
                    ),
                },
                "fold": ["synths.chain"],
            },
        ),
    ],
    "perform": [
        # the Loops page as the studio draws it: the Loops panel, the
        # launcher over the two grids, the right
        # column — → Song, the lane inspector (✦ Coach its tab), Play
        (
            "Grids",
            {
                "docks": {
                    "perform": row(
                        "perform.loops",
                        col(
                            "perform.transport",
                            "perform.slots",
                            "perform.notes",
                            "perform.drums",
                        ),
                        col("perform.song", "perform.lane", "perform.play"),
                        weights=(1, 5, 1.2),
                    )
                }
            },
        ),
        # launching: the launcher and Play beside the grids, which share a
        # tab
        (
            "Launch",
            {
                "docks": {
                    "perform": row(
                        "perform.loops",
                        col(
                            "perform.transport",
                            "perform.slots",
                            group("perform.notes", "perform.drums"),
                        ),
                        col("perform.song", "perform.play", "perform.lane"),
                        weights=(0.6, 4, 1.6),
                    )
                }
            },
        ),
        # the most on one screen: the panel, the inspector and Play share a
        # column's tabs, the grids beside it
        (
            "Compact",
            {
                "docks": {
                    "perform": row(
                        col(
                            "perform.transport",
                            group("perform.lane", "perform.loops", "perform.play"),
                            "perform.song",
                        ),
                        col("perform.slots", group("perform.notes", "perform.drums")),
                        weights=(1, 3),
                    )
                }
            },
        ),
    ],
}

# what a page's "+ module" adds: (label, attributes for its menu row)
_SCOPE = ("Scope, big", {"vis": "scope"})
_SPECTRUM = ("Spectrum, big", {"vis": "spectrum"})
_CLIPS = ("Clip library, pinned beside the page", {"pin": True})
_FX = ("FX rack", {"url": "/fragment/module/fx-rack", "id": "fx.rack", "el": "fx-rack"})
ADD: dict[str, list[tuple[str, dict]]] = {
    "editor": [_SCOPE, _SPECTRUM, _CLIPS],
    "perform": [_FX, _SCOPE, _SPECTRUM, _CLIPS],
    "drums": [_SCOPE, _SPECTRUM, _CLIPS],
    "clips": [_SCOPE, _SPECTRUM],
}

# the fragments `GET /fragment/module/{key}` draws (routes/views.py), and the
# pages that may ask: one a page draws already is never a second copy
MODULES: dict[str, frozenset[str]] = {"fx-rack": frozenset({"perform"})}


def menu(view: str) -> dict:
    """What fragments/ws_menu.html draws for `view`."""
    add = []
    for label, a in ADD.get(view, []):
        item = {"label": label, **a}
        item.setdefault("id", f"{view}.{a.get('vis', 'pin')}")
        add.append(item)
    return {"presets": PRESETS.get(view, []), "add": add}
