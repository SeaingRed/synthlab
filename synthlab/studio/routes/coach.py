"""Perform's ✦ Coach (`loopcoach.py` the rules, the options and
the session, `loopchat.py` the talk): every route answers with nothing
but its headers — the panel (region `coach-panel`) comes back by its
watch, and an accepted change brings the grids with it by theirs.

Nothing changes the loop until **Accept**: a fix, Clean up all, an option
or a reply's edits is a proposal (`/fragment/coach/propose`, the chat),
heard with ▶ (`/fragment/coach/audition`, a render of the loop as it would
be, through the one cache), then accepted — ONE pattern undo step holding
the whole loop — or discarded. ↶ Reverse undoes an accepted change while
its step is the newest, else restores the loop before it as a new step.
"""

from __future__ import annotations

import json
import wave

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ...audio import read_pcm16
from .. import clipmorph, limits, loopchat, takecoach
from .. import loopcoach as lc
from ..agent.client import AgentError
from ..render import preview_url
from ..routing import (
    OutsideLock,
    RawForm,
    StrForm,
    Svc,
    TalkUpload,
    render_outside_lock,
    studio_router,
)
from .pattern import with_toast

router = studio_router()

PLAYS = "✦ the coach's proposal"  # what Now playing calls an audition


def _heard(svc, request: Request, message: str = "") -> Response:
    """The loop as it now is, cued (an accepted or reversed change; a
    discard after ▶ proposed), with a toast."""
    svc.state.save_soon()
    resp = HTMLResponse("", headers=svc.preview_trigger(request))
    return with_toast(resp, message) if message else resp


@router.post("/fragment/coach/propose")
def coach_propose(request: Request, form: StrForm, svc: Svc) -> Response:
    """A proposal from the coach's own rules: `what` = `fix:<finding id>`,
    `fix:all` (Clean up all) or `option:<id>`. It replaces any proposal
    waiting; the loop does not change."""
    s = svc.state
    what = form.get("what", "")
    if what.startswith("fix:"):
        got, source = lc.finding_after(s, what[4:]), "fix"
    elif what.startswith("option:"):
        got, source = lc.option_after(s, what[7:]), "option"
    else:
        return svc.toast("No such proposal.")
    if got is None:
        return svc.toast("That is not on offer for the loop as it is now.")
    base, after, label = got
    if lc.propose(s, label, source, base, after) is None:
        return svc.toast("That would change nothing.")
    return HTMLResponse("")


@router.post("/fragment/coach/show")
def coach_show(request: Request, form: StrForm, svc: Svc) -> Response:
    """Show them: the roll's rows moved to the notes it hides
    (`StudioState.show_notes`). The notes stay; nothing to undo."""
    s = svc.state
    was = (s.grid_span, s.grid_oct)
    s.show_notes()
    if (s.grid_span, s.grid_oct) == was:
        return svc.toast("The rows already show every note they can.")
    s.save_soon()
    return HTMLResponse("")


@router.post("/fragment/coach/audition")
def coach_audition(request: Request, form: StrForm, svc: Svc) -> Response:
    """▶ proposed: the loop as it would be with the proposal (`which` =
    `proposed`), rendered through the one cache and cued with autoplay;
    `current`: the loop as it is. The session never holds the proposal."""
    s = svc.state
    if form.get("which") == "current":
        result, plays = svc.render("perform"), ""
    else:
        p = (s.coach or {}).get("proposal")
        if p is None:
            return svc.toast("Nothing to hear: ask for a fix, an option or a change.")
        if lc.fingerprint(lc.read(s)) != lc.fingerprint(p["base"]):
            return svc.toast("The loop changed since this was proposed — ask again.")
        with lc.with_doc(s, p["after"]):
            inp = svc.render_inputs("perform")
        result, plays = svc.render_preview(inp), PLAYS
    cue = {"url": preview_url(result), "autoplay": True}
    if plays:
        cue["plays"] = plays
    return HTMLResponse("", headers={"HX-Trigger": json.dumps({"preview-ready": cue})})


@router.post("/fragment/coach/accept")
def coach_accept(request: Request, form: StrForm, svc: Svc) -> Response:
    """The proposal into the loop: one pattern undo step (Ctrl+Z, or ↶
    Reverse in the thread). An open loop edit saves it back to its Loop,
    as any edit of the lane does (`mirror_loop_edit`)."""
    s = svc.state
    entry, why = lc.accept(s)
    if entry is None:
        return svc.toast(why)
    s.show_notes()  # the roll's window on the notes (an octave moved, …)
    return _heard(svc, request, f"Accepted: {entry['label']} — Ctrl+Z takes it back")


@router.post("/fragment/coach/discard")
def coach_discard(request: Request, form: StrForm, svc: Svc) -> Response:
    """Drop the proposal; the loop as it is plays again (after ▶ proposed)."""
    if lc.discard(svc.state) is None:
        return svc.toast("Nothing to discard.")
    return _heard(svc, request)


@router.post("/fragment/coach/reverse")
def coach_reverse(request: Request, form: StrForm, svc: Svc) -> Response:
    """↶ Reverse an accepted change (`entry`): undone when its step is the
    newest and nothing moved since, else the loop before it restored as a
    new undo step."""
    s = svc.state
    try:
        entry = int(form.get("entry", ""))
    except ValueError:
        return svc.toast("No such change.")
    how, why = lc.reverse(s, entry)
    if not how:
        return svc.toast(why)
    s.show_notes()
    said = (
        "Reversed: undone (it was the last step)."
        if how == "undone"
        else "Reversed: the loop as it was before it, as a new step — "
        "Ctrl+Z puts the change back."
    )
    return _heard(svc, request, said)


@router.post("/fragment/coach/chat")
@render_outside_lock
def coach_chat(request: Request, form: StrForm, svc: Svc) -> Response | OutsideLock:
    """A message to the coach (`coach_say`): one Gemini call, outside the
    lock (`loopchat.ask`); its reply lands in the thread and its edits —
    checked by `loopchat.apply` — become the proposal. Without a key the
    thread says how to add one; the checks and fixes still work."""
    say = " ".join(form.get("coach_say", "").split())[: limits.MAX_COACH_SAY]
    if not say:
        return svc.toast("Type something to the coach first.")
    return _talk(svc, say)


@router.post("/fragment/coach/talk")
@render_outside_lock
def coach_talk(
    request: Request, form: RawForm, upload: TalkUpload, svc: Svc
) -> Response | OutsideLock:
    """🎙 a spoken message: a 16-bit WAV the browser recorded while the
    button was held. Gemini hears its first 8 s from the first sound
    (`clipmorph.audio_wav`, 16 kHz mono) with the same text a typed message
    is sent with, and says what it heard."""
    config = takecoach.load_config()
    if not config.audio:
        return svc.toast(
            "Audio to Gemini is off (SYNTHLAB_AGENT_AUDIO=0): type it instead."
        )
    if upload.data is None:
        return svc.toast(
            "That was too long to send."
            if upload.too_large
            else "Nothing was recorded."
        )
    try:
        audio, sr = read_pcm16(upload.data)
    except (wave.Error, EOFError, ValueError):
        return svc.toast("Couldn't read that recording as a 16-bit WAV.")
    if not limits.MIN_SR <= sr <= limits.MAX_SR or not len(audio):
        return svc.toast("Nothing was recorded.")
    return _talk(svc, "", clipmorph.audio_wav(audio, sr))


def _talk(svc, say: str, audio: bytes | None = None) -> Response | OutsideLock:
    s = svc.state
    you = lc.add_entry(s, {"kind": "you", "text": say or "🎙 …"})
    why = loopchat.available()
    if why:
        lc.add_entry(
            s,
            {
                "kind": "coach",
                "lines": [
                    f"I can only talk with a Gemini key: {why}. "
                    "The checks, the fixes and the options above work without one."
                ],
                "error": True,
            },
        )
        return HTMLResponse("")
    info = loopchat.info(s)
    base, f = lc.read(s), lc.facts(s)

    def run() -> Response:
        try:
            got = loopchat.ask(info, say, audio=audio)
        except AgentError as err:
            with s.lock:
                lc.add_entry(s, {"kind": "coach", "lines": [str(err)], "error": True})
            return HTMLResponse("")
        with s.lock:
            if audio is not None:
                you["text"] = f"🎙 “{got['heard']}”" if got["heard"] else "🎙 (spoken)"
            doc, done = base, []
            if got["fixes"]:
                doc, fixed = lc.apply_ids(doc, f, got["fixes"])
                done += [f"fix: {t}" for t in fixed]
            doc, made, skipped = loopchat.apply(doc, f, got["edits"])
            done += made
            lines = got["reply"] or (["Done — have a listen."] if done else ["…"])
            changed = lc.fingerprint(doc) != lc.fingerprint(base)
            if changed and lc.fingerprint(lc.read(s)) != lc.fingerprint(base):
                lines = [*lines, "(The loop changed while I was thinking — ask again.)"]
                changed = False
            lc.add_entry(
                s,
                {
                    "kind": "coach",
                    "lines": lines,
                    "done": done[: lc.MAX_LINES * 2],
                    "skipped": skipped,
                    "tokens": got["tokens"],
                    "cost": got["cost"],
                },
            )
            if changed:
                said = say or you["text"]
                if len(said) > 64:  # cut at a word
                    said = said[:63].rsplit(" ", 1)[0] + "…"
                lc.propose(s, f"✦ {said}", "chat", base, doc, done)
        return HTMLResponse("")

    return OutsideLock(run)
