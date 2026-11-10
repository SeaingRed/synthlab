"""Which patches the AudioWorklet live voice can play exactly.

A whitelist, not a blacklist: every `Patch` field's `Ui.worklet` in
`paramui.UI` says it is reproduced by `static/voice-worklet.js` ("exact",
any value), reproduced only for certain values (a predicate), has no
audible effect on a single live note ("ignored"), or is not reproduced at
all ("default": only its Patch default plays live — what a new field
defaults to). A new field is therefore WAV-fallback by construction until
someone teaches the worklet — never silently wrong on the keys. The
views below (`WORKLET_FIELDS`, `GATED`, `IGNORED_FIELDS`)
are read from that one table.

"Reproduced" is measured, not claimed: `tests/studio/test_live_parity_corpus.py`
runs the worklet's own code under node against `Synth.note()` for every
factory + library sound this admits, and for one synthetic case per field
admitted here (`FIELD_CASES` there must name one for every entry below).
Before that test existed the gate admitted the default filter and supersaw
on the strength of the worklet *reading* them; none of the admitted sounds
matched the engine.

This used to be `workletCanPlay` in piano.js: a hand-written JS mirror of
the worklet's capabilities that could drift from voice-worklet.js without
either file failing to load. Moving the gate here means there is exactly
one place that says what the live voice can do, and `/patch.json` is the
one wire format that carries its verdict (`worklet_ok` / `worklet_blockers`)
to the browser.
"""

from __future__ import annotations

from collections.abc import Callable

from ..params import MOD_RANGES, MOD_SLOTS, MOD_SOURCES
from ..synth import Patch
from . import voicetables
from .paramui import FILTERS, MAX_UNISON, SHAPES, UI, worklet_blockers

__all__ = ["FILTERS", "MAX_UNISON", "SHAPES", "blockers", "can_play", "voice_tables"]

_DEFAULTS = Patch()


def _is_default(name: str) -> Callable[[Patch], bool]:
    return lambda p: getattr(p, name) == getattr(_DEFAULTS, name)


# Fields voice-worklet.js reproduces for ANY value a Patch can hold.
WORKLET_FIELDS: frozenset[str] = frozenset(
    n for n, u in UI.items() if u.worklet == "exact"
)
# Fields with no audible effect on a single live note.
IGNORED_FIELDS: frozenset[str] = frozenset(
    n for n, u in UI.items() if u.worklet == "ignored"
)
# Fields reproduced only for certain values (a "default" field: only one).
GATED: dict[str, Callable[[Patch], bool]] = {
    n: u.worklet if callable(u.worklet) else _is_default(n)
    for n, u in UI.items()
    if callable(u.worklet) or u.worklet == "default"
}


def voice_tables() -> dict:
    """What the worklet reads besides the patch, carried by /patch.json: the
    engine's own mod-matrix tables (so `params.slot_active` and each
    route's span are never retyped in JS) and the URL of the engine's
    fixed tables (voicetables.py)."""
    return {
        "mod_slots": list(MOD_SLOTS),
        "mod_sources": list(MOD_SOURCES),
        "mod_ranges": {d: list(span) for d, span in MOD_RANGES.items()},
        "tables": voicetables.url(),
    }


def blockers(p: Patch) -> list[str]:
    """Field names forcing the WAV fallback; `[]` means the worklet can play
    this patch exactly."""
    return worklet_blockers(p)


def can_play(p: Patch) -> bool:
    """True when the AudioWorklet live voice reproduces `p` exactly."""
    return not worklet_blockers(p)
