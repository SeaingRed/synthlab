"""The Breed board: generations, stars, history, tile auditions and adoption."""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ...explore.probe import probe_audio
from .. import genome, limits, sounds
from ..breedboard import WILDNESS, new_generation
from ..genome import DEPTHS
from ..render import RenderError, RenderResult, _to_wav_bytes, preview_url
from ..routing import (
    OutsideLock,
    StrForm,
    Svc,
    render_outside_lock,
    studio_router,
)
from ..service import StudioService
from ..state import StudioState

log = logging.getLogger("synthlab.studio")

router = studio_router()


def breed_board_fragment(svc: StudioService, active: str | None = None) -> str:
    s: StudioState = svc.state
    return svc.templates.get_template("fragments/breed_board.html").render(
        tiles=s.breed_tiles,
        starred=set(s.breed_starred),
        active=active,
        busy_lanes=s.lanes_with_notes(),
    )


def _board_response(svc: StudioService, active: str | None = None, **kw) -> Response:
    """The board (the breed posts' target). The Breed page's other views of
    its state — the "Breeding from ..." line and the generation strip —
    come back out of band when a star, a generation or a restore moved
    them (`service.REGIONS`)."""
    return HTMLResponse(breed_board_fragment(svc, active), **kw)


@router.post("/fragment/breed/generation")
@render_outside_lock
def breed_generation(request: Request, form: StrForm, svc: Svc) -> OutsideLock:
    """A new litter. Rendering the ~20 candidates takes a few hundred ms, so
    it runs outside the studio lock (`render_outside_lock`) from a snapshot
    of the parents (the starred ids and the current patch); the board is
    written back under the lock. The SpaceStore has its own lock.

    A litter is bred for the board it started from. If that board was
    replaced meanwhile — a project import, or the board reverted / reset
    (`StudioState.board_epoch`) — the litter is dropped with a toast: it
    used to overwrite the new session's board, history and stars with the
    old one's."""
    s: StudioState = svc.state
    direction = form.get("direction") or None
    wild = form.get("wildness") or "normal"
    s.breed_wildness = wild if wild in WILDNESS else "normal"
    amount = WILDNESS[s.breed_wildness]
    depth = form.get("depth") or s.breed_depth
    s.breed_depth = depth if depth in DEPTHS else "single"
    depth = s.breed_depth
    parents = SimpleNamespace(
        breed_starred=list(s.breed_starred),
        patch=copy.deepcopy(s.patch),
        data_dir=s.data_dir,
        # synced LFOs are measured at the session tempo, as the loop plays them
        bpm=s.arrangement.bpm,
    )
    epoch = s.board_epoch

    def run() -> Response:
        tiles = new_generation(
            parents, direction, amount=amount, store=svc.space_store(), depth=depth
        )
        with s.lock:
            if s.board_epoch != epoch:
                return svc.toast(
                    "The session changed while this litter was breeding, so "
                    "it was dropped — generate again."
                )
            s.breed_tiles = [asdict(t) for t in tiles]
            cap = limits.MAX_BREED_HISTORY
            s.breed_history = (s.breed_history + [s.breed_tiles])[-cap:]
            # the starred parents are consumed: they bred this litter, and a
            # star on a card no longer on the board could never be unstarred
            # (the old generation's cards are gone), yet kept parenting
            shown = {t["id"] for t in s.breed_tiles}
            s.breed_starred = [x for x in s.breed_starred if x in shown]
            s.save()
            return _board_response(svc)

    return OutsideLock(run)


@router.post("/fragment/breed/wildness")
def breed_wildness(request: Request, form: StrForm, svc: Svc) -> Response:
    """The wildness select saves on change, not only when a litter is bred —
    leaving the page used to forget a level you had picked."""
    s: StudioState = svc.state
    wild = form.get("wildness") or ""
    if wild in WILDNESS and wild != s.breed_wildness:
        s.breed_wildness = wild
        s.save_soon()
    return Response(status_code=204)


@router.post("/fragment/breed/depth")
def breed_depth(request: Request, form: StrForm, svc: Svc) -> Response:
    """The depth select (how much of a Sound a litter breeds:
    `genome.DEPTH`) saves on change, like the wildness."""
    s: StudioState = svc.state
    depth = form.get("depth") or ""
    if depth in DEPTHS and depth != s.breed_depth:
        s.breed_depth = depth
        s.save_soon()
    return Response(status_code=204)


@router.post("/fragment/breed/restore")
def breed_restore(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    try:
        index = int(form.get("index", ""))
    except ValueError:
        index = -1
    if 0 <= index < len(s.breed_history):
        s.breed_tiles = s.breed_history[index]
        s.save()
    return _board_response(svc)


@router.post("/fragment/breed/star")
def breed_star(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    tid = form.get("id", "")
    # any star can be taken off; one can be put on a card the board (or a
    # restorable generation) shows
    known = {t.get("id") for gen in (s.breed_tiles, *s.breed_history) for t in gen}
    if tid in s.breed_starred:
        s.breed_starred.remove(tid)
    elif tid in known:
        s.breed_starred.append(tid)
    # Defence in depth against stale/duplicated persisted state too:
    # dedupe (keeping order) and keep only the most recent MAX_STARRED.
    deduped: list[str] = []
    for x in s.breed_starred:
        if x not in deduped:
            deduped.append(x)
    s.breed_starred = deduped[-limits.MAX_STARRED :]
    s.save()
    return _board_response(svc)


@router.post("/fragment/breed/audition")
def breed_audition(request: Request, form: StrForm, svc: Svc) -> Response:
    """ "In your mix": the tile looped through the session, exactly what Use
    lands on Synths (`svc.render(..., mix=True)`, the card's `?mix=1`) — a
    layered card's whole Sound, as "Use with its layers" lands it."""
    tid = form.get("id", "")
    sound = sounds.find(svc, "breed", tid)
    if sound is None:
        return svc.toast("Tile not found — try generating again.")
    try:
        result = svc.render(
            svc.AUDITION_VIEW, patch=sound.patch, chain=sound.chain, mix=True
        )
    except RenderError as err:
        return svc.toast(str(err))
    # autoplay: hearing the tile in the loop is the point of the click,
    # so a stopped transport starts instead of silently stashing the URL
    trigger = json.dumps(
        {"preview-ready": {"url": preview_url(result), "autoplay": True}}
    )
    return HTMLResponse(
        breed_board_fragment(svc, active=tid), headers={"HX-Trigger": trigger}
    )


@router.get("/breed/note/{tid}.wav")
def breed_note(tid: str, svc: Svc) -> Response:
    """The tile's measuring note: exactly the probe its numbers and spectrum
    were measured on (explore.probe: A3, gate + release, synced LFOs at the
    session tempo), no loop render involved. Always A3, whatever register
    the card's ▶ plays the phrase in (`audition.register_of`): this is the
    note the numbers came from, not the audition."""
    row = svc.space_store().get(tid)
    if row is None:
        return Response(status_code=404)
    bpm = float(svc.state.arrangement.bpm)
    key = f"breed-note:{tid}:{bpm:g}"
    result = svc.notes.get(key)
    if result is None:
        try:
            stack = genome.stack_of_row(row)
            # a layered card was measured on its whole stack, at its gains
            audio = (
                probe_audio(stack.patch, bpm)
                if stack.plain
                else genome.stack_audio(stack, bpm)
            )
        except Exception:
            log.exception("breed_note: patch %r failed to render", tid)
            return Response(status_code=422)
        if not np.all(np.isfinite(audio)):
            return Response(status_code=422)
        result = RenderResult(key=key, wav=_to_wav_bytes(audio), analysis={})
        svc.notes.put(result)
    return Response(
        result.wav, media_type="audio/wav", headers={"Cache-Control": "no-store"}
    )


@router.get("/breed/{tid}/audition.wav")
def breed_tile_audition(
    tid: str, svc: Svc, mix: bool = False, main: bool = False
) -> Response:
    """The card's own ▶: the reference phrase, the sound on its own (so the
    eight siblings can be told apart) — a layered card's whole stack — in
    the sound's own register (`Sound.register`: a bass at A1, a pad at A2,
    a lead at A3); `?mix=1` is the sound in your mix, exactly what Use
    gives you on Synths (`svc.audition`), in the session's register.
    `?main=1` is a layered card's main lane alone ("▶ main only"), exactly
    what "Use sound" gives you."""
    sound = sounds.find(svc, "breed_main" if main else "breed", tid)
    return svc.audition(sound, mix=mix) if sound else Response(status_code=404)


@router.post("/fragment/breed/adopt")
def breed_adopt(request: Request, form: StrForm, svc: Svc) -> Response:
    """Use: from the board it lands on Synths, as the Library does. A
    layered card's "Use with its layers" is the whole Sound (its lanes
    replace the session's: the one load a v2 preset takes, stashed first
    so "Before last load" brings the old stack back); `main=1` is "Use
    sound": the main patch with its FX and curves, the session's lanes
    kept."""
    source = "breed_main" if form.get("main") == "1" else "breed"
    sound = sounds.find(svc, source, form.get("id", ""))
    if sound is None:
        return svc.toast("Tile not found — try generating again.")
    return svc.adopt(request, sound)
