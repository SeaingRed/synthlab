"""The one registry of studio views.

Every per-view table the studio used to keep by hand — the view names the
router accepts, the parts each view's preview renders, the bench views that
hear the reference phrase, and the reset scopes each view owns — is derived
from `VIEWS`. Adding a view is one entry here.

This module imports nothing from the studio, so `state.py` (which owns
`RESET_VIEWS`) and the web layer can both depend on it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

from markupsafe import Markup, escape


@dataclass(frozen=True)
class ViewSpec:
    parts: tuple[str, ...]
    """Which stems the view's preview renders (a workshop hears only its
    own parts; Perform's clip lane is added in `StudioService.view_parts`)."""
    bench: bool
    """Judges a *sound*: renders the fixed reference phrase, not the
    arrangement, so swapping patches never swaps what you hear them play."""
    reset_scopes: tuple[str, ...]
    """The `RESET_SCOPES` this view's reset menu owns. Empty means the
    view has no entry of its own and falls back to ("sound",)."""


# insertion order is the historical VIEW_NAMES order
VIEWS: dict[str, ViewSpec] = {
    # the note grid moved to Perform, so Synths no longer owns "sequence"
    "editor": ViewSpec(("synth",), True, ("sound", "fx", "macros")),
    "breed": ViewSpec(("synth",), True, ("sound", "board")),
    "drums": ViewSpec(("drums",), False, ("kit", "pattern", "fx_drums")),
    "perform": ViewSpec(
        ("synth", "drums"), False, ("perform", "sound", "sequence", "pattern")
    ),
    "map": ViewSpec(("synth",), True, ("sound",)),
    "bass": ViewSpec(("synth",), True, ("sound",)),
    "library": ViewSpec(("synth",), True, ("sound",)),
    "voice": ViewSpec(("synth",), False, ()),
    # the Sample Lab plays its own auditions (a region of an imported song);
    # the loop preview a stray ▶ would render is the synth, as on Voice
    "lab": ViewSpec(("synth",), False, ()),
    # the clip hub: every clip and sample; it plays its own auditions, and a
    # stray ▶ renders the synth, as on the Lab
    "clips": ViewSpec(("synth",), False, ()),
    # the arranger: every section plays its drums, synth tracks and takes
    "song": ViewSpec(("synth", "drums", "clips"), False, ("song",)),
    # the mixer: the song's tracks as strips; it plays the song's plan
    "mixer": ViewSpec(("synth", "drums", "clips"), False, ("song",)),
    # the Visuals page: scopes and spectrums to swap into the top dock; its
    # ▶ plays the working loop (Perform's parts), something to watch move
    "visuals": ViewSpec(("synth", "drums"), False, ()),
}

VIEW_NAMES: tuple[str, ...] = tuple(VIEWS)
VIEW_PARTS: dict[str, tuple[str, ...]] = {v: s.parts for v, s in VIEWS.items()}
BENCH_VIEWS: frozenset[str] = frozenset(v for v, s in VIEWS.items() if s.bench)
RESET_VIEWS: dict[str, tuple[str, ...]] = {
    v: s.reset_scopes for v, s in VIEWS.items() if s.reset_scopes
}

# what each page is called (the rail's labels; Bass check has no rail
# button): the browser tab's <title>, on a cold load and on every view swap
# (`routes/views._view_response` puts it in the swap; htmx applies it)
VIEW_LABELS: dict[str, str] = {
    "editor": "Synths",
    "drums": "Kit",
    "perform": "Loops",
    "song": "Song",
    "mixer": "Mix",
    "library": "Library",
    "breed": "Breed",
    "map": "Map",
    "voice": "Voice",
    "lab": "Lab",
    "clips": "Clips",
    "visuals": "Visuals",
    "bass": "Bass check",
}


def view_link(path: str) -> Markup:
    """The attributes of a link to another page that SWAPS the page
    instead of loading it (`href` kept for a new tab, Ctrl+click, a cold
    start): a reload stops the audio, the held keys, the song. Every
    in-page `<a>` to `/view/...` is `<a class="..." {{ view_link(path) }}>`
    (a template global, `app.make_templates`), held by
    `tests/studio/test_frontend_contracts.py`; the rail's buttons are the same
    attributes written out."""
    p = escape(path)
    return Markup(
        f'href="{p}" hx-get="{p}" hx-target="#view" hx-swap="outerHTML"'
        ' hx-push-url="true"'
    )


def view_title(view: str) -> str:
    """The page's <title>: its name, then the studio's."""
    label = VIEW_LABELS.get(view)
    return f"{label} · synthlab studio" if label else "synthlab studio"
