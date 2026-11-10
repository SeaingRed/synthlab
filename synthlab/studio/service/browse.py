"""Browse: a card's audition, and Use.

Every browse surface (Library, recipes, factory, presets, the breed board,
the map) finds a `sounds.Sound` and hands it to one of these two: `audition`
is the card's WAV through `LoopRender.render`, `adopt` loads the sound and
answers with the page (`Page.full_page_with_preview`).

A mixin of `StudioService` (the package docstring says why mixins).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request, Response

from ..state import StudioState

if TYPE_CHECKING:
    from ..sounds import Sound


class Browse:
    """`audition` and `adopt`, for every source of sounds."""

    # the service's field this unit reads (set in StudioService.__init__;
    # test_service_split.py holds this list to the code)
    state: StudioState

    # -- browse, audition, Use (sounds.py) -----------------------------------
    # Every browse surface finds a `sounds.Sound` and hands it to these two;
    # tests/studio/test_sound_sources.py holds every source to them.
    AUDITION_VIEW = "editor"  # Use lands on Synths: auditions hear its mix

    def audition(self, sound: Sound, *, mix: bool = False) -> Response:
        """A browse card's ▶, as a WAV. `mix=True` is exactly what Use lands
        on Synths (byte for byte, and the same cache entry, so Use after ▶
        renders nothing); the default is the sound on its own (see
        `render_inputs`). Either way the reference phrase, never a bare
        note: the bare `Synth(patch).note("C3")` these used to be promised a
        sound Use did not deliver.

        On its own, a sound that knows its register (`Sound.register`: a
        breed card) plays the phrase there — a bass at A1, a pad at A2 —
        so that is the one audition which is *not* what Synths plays after
        Use: Use keeps the session's notes, as "in your mix" does."""
        result = self.render(
            self.AUDITION_VIEW,
            patch=sound.patch,
            chain=sound.chain,
            mix=mix,
            register=None if mix else sound.register,
        )
        return Response(
            result.wav, media_type="audio/wav", headers={"Cache-Control": "no-store"}
        )

    def adopt(self, request: Request, sound: Sound, how: str = "") -> Response:
        """Use = "I want this sound". Load it (`StudioState.load_sound`:
        "before last load" stash, the slot unmuted, the sound — and a
        preset's FX — as the baseline, so Synths doesn't call it "edited"),
        save, and answer with the page and its preview. From the source's
        own page (`sound.home_view`) Use is a decision and lands on Synths,
        body *and* URL (`full_page_with_preview`'s `push_url`); from
        anywhere else the current view re-renders around the new sound.
        `how` says what it replaces (`load_sound_as`: Synths' Load asks
        when there are layers); "" is what a load has always done."""
        s = self.state
        if how not in s.LOAD_AS:
            return Response(status_code=422)
        if not s.load_sound_as(sound.patch, sound.chain, how=how, label=sound.label):
            return self.toast("Every layer holds a sound — clear one first (✕).")
        s.save()
        if sound.home_view and self.view_of(request) == sound.home_view:
            return self.full_page_with_preview(
                request, view="editor", push_url="/view/editor"
            )
        return self.full_page_with_preview(request)
