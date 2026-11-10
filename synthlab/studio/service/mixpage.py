"""The Mix view's context: the song's tracks as strips, the returns, the
master strip (`studio/mixer.py` is the model).

`mixer_context` is what `views/mixer.html` and the `mixer-strips` region
draw. Drawing only, and no level: a strip's meter is lit by the page
(mixer.js) from the plan's `levels`, which the song's render records
(`song.py`, `stem_levels`) — so the region's HTML depends on the song
alone and its watch (`regions._w_mixer`) can be exact.

A mixin of `StudioService` (the package docstring says why mixins).
"""

from __future__ import annotations

from .. import mixer, song_play
from .. import song_model as sm
from ..state import StudioState


class MixPage:
    """`mixer_context`."""

    # the service's fields this unit reads (set in StudioService.__init__;
    # test_service_split.py holds this list to the code)
    state: StudioState

    def mixer_context(self) -> dict:
        s = self.state
        song = s.song
        return {
            "mix": mixer.context(song),
            "mix_title": song.get("title") or "Untitled",
            "mix_undo": s.song_undo_state(),
            "mix_roles": [(r, sm.ROLE_LABELS[r]) for r in sm.TRACK_ROLES],
            "mix_bars": sum(sec["bars"] * sec["repeats"] for sec in song["sections"]),
            "loudness_targets": song_play.LOUDNESS_TARGETS,
        }
