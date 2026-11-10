"""The loop render path: `render`, the one entry point, and its caches.

`render_inputs` is everything that decides what a view sounds like;
`render` renders it (or a named post-process of it, `POSTS`) through
`_ensure`: cache lookup, timed render, `perf.record_render`, cache put.
`_ensure` is also how the song's sections are cached (`song.py`'s
`section_result` hands it a `SectionJob`), so `svc.cache` and `perf` are
touched here and nowhere else. `do_render` / `preview_trigger` are the
render plus the `preview-ready` answer; `reset_caches` forgets every
render, the song's remembered levels and gain included.

Runs under the studio lock, as its callers do: it reads `self.state`
directly and never from a snapshot.

A mixin of `StudioService` (the package docstring says why mixins).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import replace

import numpy as np
from fastapi import Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ...sequencer import midi, name_of
from ...synth import Patch
from .. import song_bridge
from .. import song_model as sm
from ..audition import NO_PHRASE, has_slides, phrase_arrangement
from ..bassview import phone_speaker
from ..lru import LRU
from ..paramui import idle
from ..perf import PerfStats
from ..render import (
    LOOP_CAP_SECONDS,
    NOTE_MEMO,
    NoteCache,
    RenderCache,
    RenderInputs,
    RenderResult,
    SectionJob,
    StemCache,
    preview_url,
    result_of,
)
from ..state import StudioState, audible_layers
from ..state.model import Arrangement
from ..views import BENCH_VIEWS

log = logging.getLogger("synthlab.studio")


def render_loop(*args, **kwargs):
    """`render.render_loop`, read off the package on every call: it has
    always been patchable as `synthlab.studio.service.render_loop`
    (tests/studio/test_routes.py, test_concurrency.py, test_request_costs.py,
    test_sound_sources.py, test_song_routes.py), and stays so."""
    from . import render_loop as real

    return real(*args, **kwargs)


class LoopRender:
    """`render_inputs`, `render`, `_ensure`, the preview answers and the
    caches' reset."""

    # the service's fields this unit reads or writes (all set in
    # StudioService.__init__; test_service_split.py holds this list to the code)
    state: StudioState
    cache: RenderCache
    notes: NoteCache
    stems: StemCache
    song_levels: LRU[tuple[float, float]]
    song_memo: LRU[dict]
    stem_levels: LRU[dict]
    perf: PerfStats
    templates: Jinja2Templates

    def reset_caches(self) -> None:
        """Forget every render: after a project import, whatever became of
        its audio. Everything here is keyed by content, and a sampler patch
        names its audio by id — so what was rendered while a sample was
        missing (a project.json imported before its bundle: silence) has
        the key the sound has once the sample is there. That goes for the
        loop and stem caches, and as much for the notes behind them
        (`render.NOTE_MEMO`, which kept playing the silence) and the song's
        remembered levels and gain."""
        self.cache = RenderCache()
        self.notes = NoteCache()
        self.stems = StemCache()
        self.song_levels = LRU(1024)
        self.song_memo = LRU(16)
        self.stem_levels = LRU(4096)
        NOTE_MEMO.clear()

    # -- the render path ---------------------------------------------------
    # Post-processes `render(..., post=name)` may apply to a view's loop.
    # Each is keyed by name, so a post's cache key is the loop's key plus
    # that name and nothing else.
    POSTS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
        "phone": phone_speaker,  # Bass Lab: the mix on a phone speaker
    }

    def render_inputs(
        self,
        view: str,
        *,
        patch: Patch | None = None,
        chain: dict | None = None,
        mix: bool | None = None,
        register: str | None = None,
        arrangement: Arrangement | None = None,
        parts: tuple[str, ...] | None = None,
    ) -> RenderInputs:
        """`_view_inputs` (what `view` sounds like: its docstring) as the
        loop plays it: a loop whose notes rest by chance is several passes
        of its grid, each rolled afresh (`song_model.loop_passes`; every
        other loop is its one pass, key for key)."""
        inp = self._view_inputs(
            view,
            patch=patch,
            chain=chain,
            mix=mix,
            register=register,
            arrangement=arrangement,
            parts=parts,
        )
        return sm.loop_passes(inp, LOOP_CAP_SECONDS)

    def _view_inputs(
        self,
        view: str,
        *,
        patch: Patch | None = None,
        chain: dict | None = None,
        mix: bool | None = None,
        register: str | None = None,
        arrangement: Arrangement | None = None,
        parts: tuple[str, ...] | None = None,
    ) -> RenderInputs:
        """Everything that decides what `view` sounds like, as one typed
        object. `render` is the one caller that renders
        it; tests and the stem-key checks read it directly.

        Bench views (`BENCH_VIEWS`) hear the constant reference phrase, not
        the arrangement: they judge sounds, and a browsing view that
        rendered `s.arrangement` synth-only was silent whenever the Perform
        grid held no synth notes (▶ on a Library sound played it, Use gave
        nothing). Bass Lab (`"bass"`) moves the phrase down to its quick
        key's root (`state.bass_root`), where subs live or die — the grid
        and the arrangement's key are never touched.

        A browse card's audition substitutes a sound (`patch`, plus a
        format-2 preset's `chain` of layers/main FX/curves) and is heard
        with the slot unmuted, as Use leaves it:
        - `mix=True` — in your mix: exactly what Synths plays after Use
          (the session's other layers, beat, FX and curves).
        - `mix=False`/None — on its own: the same phrase with no other
          layers, beat, session FX or curves (a preset's own chain stays),
          so eight siblings can be told apart (in a busy session the mix
          compressed their differences ~3x, and a muted slot made them
          identical).
        Without a `patch`, `mix` is ignored: the view as it stands.

        `register` (`audition.register_of`) moves a bench view's phrase to
        a sound's own register — a breed card on its own, so a bass is
        heard at A1 and a pad at A2 rather than everything at A3. The
        notes are part of the arrangement, so they are in
        `RenderInputs.key()`: one sound in two registers is two cache
        entries.

        `arrangement` (a loop view, not a bench) is the loop as it would be
        with that on the edited lane, heard as the main lane — the notes, the
        clock, the tempo and key of a kept loop, rendered without being put
        on the grid (`/loop/bounce`, with the loop's sound as `patch` /
        `chain`); `parts` the parts heard in place of the view's own
        (`view_parts`). Neither touches the session."""
        s = self.state
        if (
            view == "editor"
            and s.audition == NO_PHRASE
            and patch is None
            and arrangement is None
            and parts is None
            and s.track_edit_view() != "editor"
        ):
            # Synths' phrase "none": ▶ is the beat alone, the very render ▶
            # beat plays (the Drums view's) — to play the keys over just the
            # drums. A sound's audition still hears the phrase (DEFAULT)
            return self.render_inputs("drums")
        bench = view in BENCH_VIEWS
        # the main lane's notes: the phrase stands in for them on a bench
        src = (
            phrase_arrangement(
                s.arrangement, s.audition, keep_drums=s.vs_beat, register=register
            )
            if bench
            else arrangement or s.main_arrangement()
        )
        if view == "bass" and s.bass_root is not None:
            shift = midi(s.bass_root) - midi(src.root)
            cells = [[name_of(midi(p) + shift), st] for p, st in src.cells]
            src = replace(src, root=s.bass_root, cells=cells)
        # the edited lane (at its octave when it follows), the other lanes
        # (followers on the main lane's notes), mute, solo and "This layer"
        # (the strip's listening toggle: a bench ▶ hears only the edited
        # layer) — `audible_layers`, the predicate the live keys play too
        lanes = audible_layers(s, view)
        # (a synth track's: the drums track's kit is edited from the Drum Lab)
        if view == "editor" and s.track_edit_view() == "editor":
            # a track's sound is heard as the song plays it: its own notes,
            # or the phrase when no section plays it yet, on the song's key
            # and tempo and on its own (the loop's are another tune's)
            alone = song_bridge.track_alone(s.song, s.song_edit_track)
            wide = {k: s.song[k] for k in song_bridge.SONG_WIDE}
            src = (
                alone[0]
                if alone
                else phrase_arrangement(
                    replace(s.arrangement, **wide), s.audition, register=register
                )
            )
            lanes = [ln for ln in lanes if ln.edited]
        lane = None if bench or arrangement is None else src
        arr, layers, main_muted = s.loop_mix(src, bench=bench, lanes=lanes, lane=lane)
        inp = RenderInputs(
            patch=s.patch,
            arr=arr,
            curves=s.curves,
            layers=layers,
            main_muted=main_muted,
            main_fx=s.layers[s.active_layer].get("fx") or [],
            # the slot's own gain, as an inactive layer plays it — or which
            # layer is being edited was audible (1.0 keeps every key as it was)
            main_gain=float(s.layers[s.active_layer].get("gain", 1.0)),
            master_fx=s.fx_master,
            drums_fx=s.fx_drums,
            parts=self.view_parts(view) if parts is None else parts,
            clip_root=s.data_dir,
        )
        if patch is None:
            return inp
        inp = replace(inp, patch=patch, main_muted=False, main_gain=s.landing_gain())
        # a sound heard as the lane's own sound — alone, or a whole Sound
        # whose load makes the edited slot the main lane again — plays the
        # main lane's notes, not the octave a following slot sits at
        unshifted = replace(inp, arr=src) if s.follows(s.active_layer) else inp
        if chain is not None:
            # a format-2 preset (or a layered breed card) replaces layers/
            # FX/curves on load, so its audition uses its own chain, not the
            # session's. `layers` None ("Use sound": a card's main lane)
            # brings FX and curves and leaves the session's lanes as they are
            inp = replace(inp, main_fx=chain["main_fx"], curves=chain["curves"])
            if chain["layers"] is not None:
                inp = replace(inp, arr=unshifted.arr, layers=chain["layers"])
            if not mix:
                inp = replace(inp, arr=unshifted.arr, master_fx=(), parts=("synth",))
                if chain["layers"] is None:
                    inp = replace(inp, layers=(), main_gain=1.0)
        elif not mix:
            inp = replace(
                inp,
                arr=unshifted.arr,
                layers=(),
                main_fx=(),
                master_fx=(),
                curves={},
                main_gain=1.0,
                parts=("synth",),
            )
        # "This layer" wherever the view's own mix is heard — a card's "in
        # your mix" too, so it still equals what Use lands on — except a
        # Sound bringing its own layers, whose load turns This layer off
        brings_layers = chain is not None and chain["layers"] is not None
        focus = bench and s.layer_focus and bool(mix) and not brings_layers
        return replace(inp, layers=()) if focus else inp

    def beat_key(self) -> str | None:
        """The key of the beat Synths' ▶ beat plays (`GET /beat.wav`: the
        Drums view's render), None while the loop has no beat. The sound
        bar draws it on `#play-beat` and its watch holds it, so the player
        refetches only when the beat it would hear moved. A key, no render:
        cheap enough for a watch (tens of µs)."""
        if not self.state.arrangement.drums:
            return None
        return self.render_inputs("drums").key()

    def render(
        self,
        view: str,
        *,
        patch: Patch | None = None,
        chain: dict | None = None,
        mix: bool | None = None,
        post: str | None = None,
        register: str | None = None,
        arrangement: Arrangement | None = None,
        parts: tuple[str, ...] | None = None,
    ) -> RenderResult:
        """THE way the studio renders a loop: what `view` sounds like
        (`render_inputs`, with an audition's `patch`/`chain`/`mix`, or a
        bounce's `arrangement`/`parts`), cached
        in `svc.cache` under `RenderInputs.key()` and timed into
        `perf.record_render`. ▶, every preview, the browse auditions, Bass
        Lab's panels and phone test and the clip bounce all come through
        here, so each hears the very render ▶ plays for that view — they
        used to choose between render_loop, render_audio and a cached lookup
        and build their inputs by hand, and a bounce once dropped an input.

        `post` names one of `POSTS`: the loop's audio (decoded from the
        cached result) through that process, cached under the loop's key
        plus the name. A reader wanting samples takes `result.audio()`.

        A `RenderError` propagates: the caller decides whether that means
        the global toast handler or its own composed response."""
        inp = self.render_inputs(
            view,
            patch=patch,
            chain=chain,
            mix=mix,
            register=register,
            arrangement=arrangement,
            parts=parts,
        )
        result = self._ensure(inp)
        if post is None:
            return result
        process = self.POSTS[post]
        key = hashlib.sha1(f"{result.key}:{post}".encode()).hexdigest()[:12]

        def make(_stems: StemCache | None) -> RenderResult:
            out = result_of(key, process(result.audio()), analyse=False)
            out.passes = result.passes  # the loop's passes, processed
            return out

        return self._ensure(SectionJob(key, make))

    def preview_inputs(
        self,
        view: str,
        *,
        patch: Patch | None = None,
        chain: dict | None = None,
        mix: bool | None = None,
        arr: dict | None = None,
        **fields,
    ) -> RenderInputs:
        """`render_inputs(view, ...)` with an edit not made yet: `arr`, the
        `Arrangement` fields it would replace (merged into the view's own
        arrangement), and any of `RenderInputs`' fields (`parts`,
        `drums_fx`, `main_fx`, `clip_root`) — the clip hub's Make, heard
        before it is applied. Under the lock, as `render_inputs` is;
        `render_preview` renders what it returns."""
        inp = self._view_inputs(view, patch=patch, chain=chain, mix=mix)
        if arr:
            fields["arr"] = replace(inp.arr, **arr)
        return sm.loop_passes(replace(inp, **fields), LOOP_CAP_SECONDS)

    def render_preview(self, inp: RenderInputs) -> RenderResult:
        """`preview_inputs`' render, through the one cache and timer
        (`_ensure`): keyed by content, served by `/preview/{key}.wav`. The
        inputs are a snapshot, so this may run with the lock released."""
        return self._ensure(inp)

    def _ensure(self, inp: RenderInputs | SectionJob) -> RenderResult:
        """Cache lookup, timed render, `perf.record_render`, cache put — for
        `render` and the song's sections (`section_result`). Previews once
        rendered and cached without being timed, so a whole class of them
        (reset/bank/perform-launch) was invisible in /debug/stats."""
        key = inp.key()
        result = self.cache.get(key)
        if result is None:
            t0 = time.perf_counter()
            if isinstance(inp, SectionJob):
                result = inp.make(self.stems)
            else:
                result = render_loop(inp, stem_cache=self.stems)
            ms = (time.perf_counter() - t0) * 1000
            self.perf.record_render(ms)
            log.debug("render %s %.1fms", key[:12], ms)
            self.cache.put(result)
        return result

    def heard(self, view: str) -> RenderResult:
        """What ▶ plays on `view`: `render(view)`, except on Synths while
        it edits a song track's sound — then the song's section at the
        song's level (`track_heard`). Only ▶'s answers (`do_render`,
        `preview_trigger`) take it; the sound agent's measure, a bounce and a
        resample keep the sound's own render."""
        if view == "editor" and (track := self.track_heard()) is not None:
            return track
        return self.render(view)

    def do_render(
        self, request: Request, extra_html: str = "", view: str | None = None
    ) -> Response:
        """Shared by patch/arrangement posts: render, cache, respond. A
        `RenderError` from `render` is left to propagate to the
        `RenderError` exception handler (still_playing=True, 422) — this
        function has nothing left to do once the render has failed."""
        s = self.state
        v = view or self.view_of(request)
        result = self.heard(v)
        s.save_soon()
        html = self.templates.get_template("fragments/analysis.html").render(
            analysis=result.analysis
        )
        events: dict = {"preview-ready": {"url": preview_url(result)}}
        # the panels: which controls change nothing now (paramui.idle)
        if v == "editor":
            events["patch-idle"] = idle(s.patch, has_slides(s.audition))
        trigger = json.dumps(events)
        # the page's other regions (reset menu, chain bar, drawings, ...)
        # are appended by the route wrapper when what they show moved
        return HTMLResponse(html + extra_html, headers={"HX-Trigger": trigger})

    def preview_trigger(
        self, request: Request | None = None, view: str | None = None
    ) -> dict:
        v = view or (self.view_of(request) if request else "perform")
        result = self.heard(v)
        return {
            "HX-Trigger": json.dumps({"preview-ready": {"url": preview_url(result)}})
        }
