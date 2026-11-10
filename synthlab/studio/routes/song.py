"""The Song page's routes.

**Playback.** The plan and the section WAVs it points at. The export of the
same song is `routes/export.py`; both go through
`StudioService.song_plan` / `section_result` / `song_mixdown`, which share
one timeline (`song_model.section_timeline`), so what the player queues is
what the export writes. An over-long song is refused (a 422 toast, via the
app's RenderError handler) before anything renders.

**Arranging.** Sections, tracks, the pattern pool, cells, the drawer's
pattern grid, and the two loop bridges. Every edit:

- runs on a copy of the song and is committed only whole and
  schema-clean (`_commit`). A refusal (`SongError`, the model's own
  message) is a 422 toast and changes nothing. So is an edit that would
  take the song past its length caps (`song_play.check_length`), unless
  the song was already past them (an imported project is never trapped);
- answers with the page's regions whose state it moved, out of band
  (`song-arranger`, `song-pool`, `song-topbar`, `song-drawer`, the
  `reset-menu`, whose "changed" dot tracks the song — the route wrapper
  finds them, `service.REGIONS`), so the page can post with
  `hx-swap="none"`;
- carries `HX-Trigger: {"song-changed": {"sections": [ids]}}`: the sections
  whose render idents changed (`svc._song_idents`: each section's first
  pass and loop, as `section_key` plus what folds into the head). That is
  render-free — no audio is rendered inside an edit request — and names
  every section whose own audio changed: a rename lists nothing, an edit to
  a shared pattern lists every section using it plus the head of each
  section after one. It does NOT see a song-wide gain shift (the gain needs
  the audio); the player covers that, because it re-fetches
  `/song/plan.json` on every song-changed and diffs the WAV URLs, which
  carry the gain. The list is best-effort: the edit is saved first, and if
  the idents can't be built (an over-long song) the list is empty;
- announces a section that grew to fit a pattern with a `studio-toast`;
- is ONE step of the song's undo ring (`_commit` pushes the song it
  replaced, under the `label` its route gave it — `state/history.py`,
  studio docs/song.md "Undo and redo"). A route says `key` when its
  control posts many times a gesture (a fader, a lane's slider, a take):
  those join into one step. `POST /song/undo` / `/song/redo` move along
  the ring and answer like an edit.

The drawer edits pool patterns by id and never touches the working loop.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import random
import re
import secrets
from collections.abc import Callable, Iterable

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from ...sequencer import SCALES, midi, name_of
from .. import beatgen, beatpick, drumops, limits, meter, schema, song_bridge, song_play
from .. import patternops as po
from .. import song_model as sm
from ..arrange import arrangement_from_form
from ..events import ORIGIN_HEADER
from ..gen import build, chordtrack
from ..gen import drums as gd
from ..render import RenderError
from ..routing import (
    OutsideLock,
    StrForm,
    Svc,
    render_outside_lock,
    studio_router,
)
from ..service import StudioService
from ..state import DRUM_ROWS, Arrangement, key_ladder
from ..state.model import NOTE_LANE
from ..words import plural
from .drums import _extend_hits, _extend_vel

router = studio_router()
log = logging.getLogger("synthlab.studio")

# --- playback ---------------------------------------------------------------------


@router.get("/song/plan.json")
@render_outside_lock
def song_plan(svc: Svc, head: bool = False) -> OutsideLock:
    """Every section's place in the song and its two WAVs. Shape (Task 7's
    player reads it):

        {sr, bpm, gain, song_samples, song_seconds,
         sections: [{section_id, name, index, bars, repeats, start_bar,
                     cycle_bars, cycles, passes, cycle_samples,
                     cycle_seconds, start_sample, start_seconds, url,
                     loop_url}]}

    Play `url` once, then `loop_url` `passes - 1` times, back to back from
    `start_sample`; `cycles` is `bars / cycle_bars` (loops per repeat),
    `passes` is `cycles * repeats`, and `start_bar` counts every bar of
    every repeat before this section.

    `gain` is the song-wide gain and is ALREADY APPLIED to every WAV (and
    to the export): it is reported for display only. The player must not
    apply it again.

    The first plan of a song renders every section (for the gain), so it
    runs outside the studio lock, from a snapshot (`render_outside_lock`).

    `head=1` (a starter's first request: `song-autoplay` in song.js) asks
    for the plan to START on while that happens — the first section alone,
    looping, at `svc.song_head_gain` (`"head": true`); the page plays it
    and swaps the whole plan in at a pass start. Once the song's gain is
    known the answer is the whole plan."""
    song, master = svc.song_snapshot()

    def run() -> Response:
        # the plan is where an edited song's sections render (its gain
        # reads every pass): a step per pass, remembered ones included
        steps = 4 if head else 2 * len(song["sections"])
        with svc.events.task("Rendering song", steps) as task:
            return JSONResponse(svc.song_plan(song, master, task, head=head))

    return OutsideLock(run)


@router.get("/song/section/{section_id}.wav")
@render_outside_lock
def song_section_wav(
    section_id: str,
    svc: Svc,
    loop: bool = False,
    p: int | None = None,
    head: bool = False,
    after: str | None = None,
) -> Response | OutsideLock:
    """One pass of a section: its first pass in the song (the previous
    section's tail folded in), or `loop=1`, the section folded onto itself.
    The song-wide gain and the master chain are already applied (play it
    as is). The gain and every section's render key are memoized by the
    song's content, so fetching a plan's WAVs re-derives neither.
    The plan's `k` query (the render key) only makes an edited section a
    new URL; this always serves the current song. `p` is the pass, for a
    section under the song's long audio (the plan's `pass_urls`): there
    each pass plays the next slice of the stem. `head` is a head plan's
    WAV: at `svc.song_head_gain`, which does not wait for every section
    (the song-wide gain itself once that is known). `after` is the wrap
    of a RANGE loop (songsel.js): this section's first pass as it sounds
    straight after section `after` — that section's ring folded into its
    head, the fold every first pass has, from another neighbour. Renders
    outside the studio lock, from a snapshot (`render_outside_lock`)."""
    song, master = svc.song_snapshot()
    index = sm.section_index(song, section_id)
    if index is None:
        return Response(status_code=404)
    came = sm.section_index(song, after) if after is not None else None
    if after is not None and came is None:
        return Response(status_code=404)

    def run() -> Response:
        gain = (svc.song_head_gain if head else svc.song_gain)(song, master)
        result = svc.section_result(
            song, index, loop=loop, gain=gain, master_fx=master, at=p, after=came
        )
        return Response(
            result.wav, media_type="audio/wav", headers={"Cache-Control": "no-cache"}
        )

    return OutsideLock(run)


@router.get("/song/cell/{section_id}/{track_id}.wav")
@render_outside_lock
def song_cell_wav(section_id: str, track_id: str, svc: Svc) -> Response | OutsideLock:
    """ "Solo this cell" (the drawer's audition): one loop of the section
    with only this track playing — its pattern through its own sound, FX
    and lanes, for the section's cycle, folded onto itself like any section
    loop. The stems export's render, a section of it: the song with every
    other track muted (`song_play.solo_song`) through `section_result`, at
    the mix's song-wide gain and without the master chain, so the cell is
    heard at the level it has in the mix. Nothing here is a second render
    path. Its cache key is the solo song's own `section_key` (other tracks'
    sounds are not in it), so it never stands in for the section's normal
    render, and that one is untouched. 404 for an unknown or empty cell.
    Renders outside the studio lock, from a snapshot."""
    song, master = svc.song_snapshot()
    index = sm.section_index(song, section_id)
    if index is None or song["sections"][index]["cells"].get(track_id) is None:
        return Response(status_code=404)

    def run() -> Response:
        gain = svc.song_gain(song, master)
        solo = song_play.solo_song(song, track_id)
        result = svc.section_result(solo, index, loop=True, gain=gain)
        return Response(
            result.wav, media_type="audio/wav", headers={"Cache-Control": "no-cache"}
        )

    return OutsideLock(run)


@router.get("/song/pattern/{pattern_id}/play.wav")
@render_outside_lock
def pattern_play_wav(
    pattern_id: str, svc: Svc, track: str | None = None
) -> Response | OutsideLock:
    """The pattern editor's ▶ Play on a pool pattern: one loop of it
    through `track`'s sound (`song_bridge.pattern_lane`: that track when it
    fits the pattern, else the first lane that plays it, else the first of
    its kind; a song with no lane of its kind yet hears the working loop's
    kit or sound, `song_bridge.stand_in_lane`), alone, at the song's level
    (`StudioService.pattern_heard`). 404 for an unknown pattern. Renders
    outside the studio lock, from a snapshot."""
    song, master = svc.song_snapshot()
    p = song["patterns"].get(pattern_id)
    if p is None:
        return Response(status_code=404)
    tid = song_bridge.pattern_lane(song, pattern_id, track)
    if tid is None:
        # no lane of its kind yet: the working loop's kit or sound stands in
        s = svc.state
        loop = {k: getattr(s.arrangement, k) for k in song_bridge.KIT}
        loop |= {
            "fx_drums": copy.deepcopy(s.fx_drums),
            "patch": s.patch.to_json(),
            "fx": copy.deepcopy(s.layers[s.active_layer].get("fx") or []),
            "curves": copy.deepcopy(s.curves),
        }
        kind = "drums" if p["kind"] == "drums" else "synth"
        try:
            tid = song_bridge.stand_in_lane(song, kind, loop)
        except sm.SongError:
            return Response(status_code=404)

    def run() -> Response:
        result = svc.pattern_heard(song, master, pattern_id, tid)
        return Response(
            result.wav, media_type="audio/wav", headers={"Cache-Control": "no-cache"}
        )

    return OutsideLock(run)


# --- the edit machinery -----------------------------------------------------------


def _title(raw) -> str:
    """A section / track / pattern name from a form: whitespace (newlines
    included) collapsed, capped. Markup is left as typed; templates
    autoescape it."""
    return " ".join(str(raw).split())[: limits.MAX_TITLE_CHARS]


def _named(form: dict, what: str) -> str:
    name = _title(form.get("name", ""))
    if not name:
        raise sm.SongError(f"Give the {what} a name.")
    return name


def _int(form: dict, key: str, what: str) -> int:
    """A whole number from a form field, or a SongError (a toast)."""
    try:
        return int(str(form.get(key, "")).strip())
    except ValueError:
        raise sm.SongError(f"{what} must be a whole number.") from None


def _section(song: dict, section_id) -> dict:
    i = sm.section_index(song, section_id)
    if i is None:
        raise sm.SongError("No such section.")
    return song["sections"][i]


def _track(song: dict, track_id) -> dict:
    i = sm.track_index(song, track_id)
    if i is None:
        raise sm.SongError("No such track.")
    return song["tracks"][i]


def _pattern(song: dict, pattern_id) -> dict:
    p = song["patterns"].get(pattern_id)
    if p is None:
        raise sm.SongError("No such pattern.")
    return p


def _listed(names: Iterable[str]) -> str:
    names = list(dict.fromkeys(names))
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def _check_length(old: dict, new: dict) -> None:
    """Refuse an edit that takes the song past its length caps — unless it
    already was past them, so an over-long import can still be cut down."""
    try:
        song_play.check_length(new)
    except RenderError as err:
        try:
            song_play.check_length(old)
        except RenderError:
            return
        raise sm.SongError(str(err)) from None


def _idents(svc: StudioService, song: dict) -> dict[str, tuple]:
    """{section id: (head ident, loop ident)}: what each section's two WAVs
    depend on before the song gain, computed without rendering."""
    entry = svc._song_idents(song, svc.state.fx_master)
    rows = zip(song["sections"], entry["passes"], strict=True)
    return {
        # under long audio, every pass's ident (`each`)
        sec["id"]: (head[0], loop[0], *(one[0] for one in entry["each"].get(i, ())))
        for i, (sec, (head, loop)) in enumerate(rows)
    }


def _changed_ids(svc: StudioService, old: dict, new: dict) -> list[str]:
    """The sections whose audio (before gain) an edit changed, in song
    order. Best-effort: an edit is already committed and saved when this
    runs, so a failure is logged and answers [] rather than a 500."""
    try:
        before, after = _idents(svc, old), _idents(svc, new)
    except Exception:  # noqa: BLE001 - never fail a committed edit
        log.warning("song-changed: idents unavailable", exc_info=True)
        return []
    return [i for i in after if before.get(i) != after[i]]


def _grown(old: dict, new: dict) -> str | None:
    """A toast for the sections an edit lengthened to fit a pattern."""
    was = {sec["id"]: sec["bars"] for sec in old["sections"]}
    grew = [
        f"{sec['name']} grew to {sec['bars']} bars"
        for sec in new["sections"]
        if sec["id"] in was and sec["bars"] > was[sec["id"]]
    ]
    if not grew:
        return None
    return _listed(grew) + " to fit the pattern."


def _answer(trigger: dict | None = None) -> HTMLResponse:
    """An edit's answer: no body of its own (the page posts with
    hx-swap="none"); the route wrapper appends the regions it moved."""
    headers = {"HX-Trigger": json.dumps(trigger)} if trigger else {}
    return HTMLResponse("", headers=headers)


def _changed(
    svc: StudioService,
    old: dict,
    *,
    announce: bool = True,
    soon: bool = False,
    extra: dict | None = None,
    at_once: bool = False,
) -> HTMLResponse:
    """Answer an edit already committed to `svc.state.song` (`old` is the
    song before it): save it first, then the song-changed trigger
    (render-free, best-effort: `_changed_ids`; `now` when the edit may be
    heard mid-pass, `_commit`'s `at_once`)."""
    s = svc.state
    new = s.song
    s.save_soon() if soon else s.save()
    trigger: dict = {"song-changed": {"sections": _changed_ids(svc, old, new)}}
    if at_once:
        trigger["song-changed"]["now"] = True
    if announce and (msg := _grown(old, new)):
        trigger["studio-toast"] = {"message": msg}
    return _answer(trigger | (extra or {}))


Said = str | Callable[[], str | None] | None


def _said(x: Said) -> str | None:
    return x() if callable(x) else x


def _commit(
    svc: StudioService,
    change: Callable[[dict], None],
    *,
    label: Said = "song edit",
    key: Said = None,
    window: float | None = None,
    announce: bool = True,
    soon: bool = False,
    then: Callable[[], dict | None] | None = None,
    at_once: bool = False,
) -> Response:
    """Run `change` on a copy of the song; commit it only if it is whole,
    schema-clean and within the length caps. `then` runs after a commit
    (state beyond the song); a dict it returns joins the HX-Trigger.

    A commit that moved the song is one undo step named `label` ("Undo:
    <label>" on the Song page). `key` names the control when one gesture
    posts many times: commits with the same key within `window` seconds
    (default: the patch ring's `UNDO_COALESCE_S`) are one step. Both may
    be callables, read after `change` ran (a route that only then knows
    what it did). `at_once`: the edit keeps the timing (a sound swap), so
    the drawer's audition may take it mid-pass (`song-changed`'s `now`)."""
    s = svc.state
    old = s.song
    song = copy.deepcopy(old)
    try:
        change(song)
        new = schema.conform(song, schema.nodes().SONG)
        _check_length(old, new)
    except sm.SongError as err:
        return svc.toast(str(err))
    s.song = new
    s.song_undo_push(old, _said(label) or "song edit", _said(key), window)
    more = then() if then is not None else None
    return _changed(svc, old, announce=announce, soon=soon, extra=more, at_once=at_once)


# --- undo and redo ----------------------------------------------------------------


def _moved(svc: StudioService, old: dict, said: str) -> Response:
    """Answer an undo or a redo: the song is another one now. A take whose
    step was undone has no undo of its own left (`state.take_history`
    restores a pattern by id, from a song the ring just rewound past), and
    a drawer whose cell or pattern is gone closes for good. Then like any
    edit: saved, `song-changed`, the regions by their watches."""
    s = svc.state
    undone = set(s.song_undo_state()["undone"])
    s.take_history = [h for h in s.take_history if f"take:{h['take']}" not in undone]
    if svc._song_drawer() is None:
        s.song_drawer = None
    said_it = {"studio-toast": {"message": said}}
    return _changed(svc, old, announce=False, extra=said_it)


@router.post("/song/undo")
def song_undo(request: Request, svc: Svc) -> Response:
    """Ctrl+Z on the Song page: the song as it was before its newest edit
    (any `_commit`, "Add loop to song", a track's sound edited on Synths).
    Every stroke, tool, cell, section, track, lane and take is one step."""
    s = svc.state
    old = s.song
    label = s.song_undo()
    if label is None:
        return svc.toast("Nothing to undo in the song.")
    return _moved(svc, old, f"Undid: {label}")


@router.post("/song/redo")
def song_redo(request: Request, svc: Svc) -> Response:
    """Ctrl+Shift+Z / Ctrl+Y: take back the last undo. A new edit after an
    undo leaves nothing to redo."""
    s = svc.state
    old = s.song
    label = s.song_redo()
    if label is None:
        return svc.toast("Nothing to redo in the song.")
    return _moved(svc, old, f"Redid: {label}")


# --- sections ---------------------------------------------------------------------


@router.post("/song/section/add")
def section_add(request: Request, form: StrForm, svc: Svc) -> Response:
    """A new empty section: `name` (default "Section N"), `bars` (default
    4, clamped to 1..MAX_SECTION_BARS), `at` (an index; default last)."""

    def change(song):
        name = _title(form.get("name", "")) or f"Section {len(song['sections']) + 1}"
        bars = _int(form, "bars", "Bars") if form.get("bars") else 4
        at = _int(form, "at", "The position") if form.get("at") else None
        sm.add_section(song, name, bars, at)

    return _commit(svc, change, label="add section")


@router.post("/song/section/{section_id}/rename")
def section_rename(request: Request, section_id: str, form: StrForm, svc: Svc):
    def change(song):
        _section(song, section_id)["name"] = _named(form, "section")

    return _commit(svc, change, label="rename section")


@router.post("/song/section/{section_id}/bars")
def section_bars(request: Request, section_id: str, form: StrForm, svc: Svc):
    """`bars`, clamped to 1..MAX_SECTION_BARS; never shorter than the
    section's longest pattern (refused, naming it)."""

    def change(song):
        sec = _section(song, section_id)
        bars = min(limits.MAX_SECTION_BARS, max(1, _int(form, "bars", "Bars")))
        used = [song["patterns"][p] for p in sec["cells"].values() if p]
        longest = max(used, key=sm.pattern_bars, default=None)
        if longest is not None and bars < sm.pattern_bars(longest):
            raise sm.SongError(
                f"{sec['name']} can't be shorter than its longest pattern, "
                f"{longest['name']} ({sm.pattern_bars(longest)} bars)."
            )
        # a take starts at its own bar: a section ending before it would
        # keep the take but never play it
        last = max(sec["audio"], key=lambda a: a["at_bar"], default=None)
        if last is not None and bars <= last["at_bar"]:
            raise sm.SongError(
                f"{sec['name']} can't end before its take {last['clip']}, "
                f"which starts at bar {last['at_bar'] + 1}."
            )
        # ... and so does a trimmed block: one that would be left with no
        # bar at all is in the way (a shorter one is cut to fit: the schema
        # holds every span to its section when the song is committed)
        late = max(
            (sec.get("spans") or {}).items(), key=lambda kv: kv[1][0], default=None
        )
        if late is not None and bars <= late[1][0]:
            block = song["patterns"][sec["cells"][late[0]]]["name"]
            raise sm.SongError(
                f"{sec['name']} can't end before its block {block}, "
                f"which starts at bar {late[1][0] + 1}."
            )
        sec["bars"] = bars

    return _commit(svc, change, label="section length", announce=False)


@router.post("/song/section/{section_id}/repeats")
def section_repeats(request: Request, section_id: str, form: StrForm, svc: Svc):
    """`repeats` (absolute) or `delta` (±), clamped to
    1..MAX_SECTION_REPEATS."""

    def change(song):
        sec = _section(song, section_id)
        if "delta" in form:
            n = sec["repeats"] + _int(form, "delta", "Repeats")
        else:
            n = _int(form, "repeats", "Repeats")
        sec["repeats"] = min(limits.MAX_SECTION_REPEATS, max(1, n))

    return _commit(svc, change, label="section repeats", key=f"repeats:{section_id}")


@router.post("/song/section/{section_id}/time")
def section_time(request: Request, section_id: str, form: StrForm, svc: Svc):
    """A section's own bar and tempo (the section menu's `meter`, `bpm`
    and `ramp`; only what is posted changes, and an empty value is the
    song's again). `meter`: one of `meter.METERS` — the section's patterns
    play fitted to it bar by bar (`song_model.in_meter`), the pool keeps
    them as written. `bpm`: its tempo. `ramp`: the tempo it reaches at
    the end of each pass, from its own at the start (an accelerando or a
    ritardando; "" or "off": none). A section equal to the song's keeps
    nothing (`schema` drops it). Refused past the song's length cap like
    any edit (`_check_length`)."""
    did: list[str] = []

    def tempo(key: str, what: str) -> float | None:
        raw = str(form.get(key, "")).strip()
        if raw in ("", "off"):
            return None
        try:
            v = float(raw)
        except ValueError:
            v = math.nan
        lo, hi = limits.BPM_RANGE
        if not math.isfinite(v) or not lo <= v <= hi:
            raise sm.SongError(f"{what} is a tempo from {lo:g} to {hi:g} bpm.")
        return round(v, 3)

    def change(song):
        sec = _section(song, section_id)
        if "meter" in form:
            m = form["meter"]
            if m and m not in meter.METERS:
                raise sm.SongError("That is not a meter the studio has.")
            sec["meter"] = m or None
            did.append("meter")
        if "bpm" in form:
            sec["bpm"] = tempo("bpm", "A section's tempo")
            did.append("tempo")
        if "ramp" in form:
            sec["ramp"] = tempo("ramp", "A ramp's end")
            did.append("ramp")
        for k in ("meter", "bpm", "ramp"):
            if sec.get(k) is None:
                sec.pop(k, None)

    return _commit(
        svc,
        change,
        label=lambda: f"section {did[0]}" if did else "section tempo",
        key=lambda: f"time:{section_id}:{did[0]}" if did else None,
    )


@router.post("/song/section/{section_id}/duplicate")
def section_duplicate(request: Request, section_id: str, svc: Svc) -> Response:
    """A copy right after it: the same patterns by reference, a primed name."""

    def change(song):
        sm.duplicate_section(song, _section(song, section_id)["id"])

    return _commit(svc, change, label="duplicate section")


@router.post("/song/section/{section_id}/move")
def section_move(request: Request, section_id: str, form: StrForm, svc: Svc):
    """To index `to`, or by `delta` places (-1 left, +1 right); clamped."""

    def change(song):
        _section(song, section_id)
        if "delta" in form:
            here = sm.section_index(song, section_id)
            to = here + _int(form, "delta", "The move")
        else:
            to = _int(form, "to", "The position")
        sm.move_section(song, section_id, max(0, to))

    return _commit(svc, change, label="move section")


@router.post("/song/section/{section_id}/delete")
def section_delete(request: Request, section_id: str, svc: Svc) -> Response:
    """Take the section out of the song; its patterns stay in the pool."""

    def change(song):
        sm.remove_section(song, _section(song, section_id)["id"])

    return _commit(svc, change, label="delete section")


# --- tracks -----------------------------------------------------------------------


@router.post("/song/track/add")
def track_add(request: Request, form: StrForm, svc: Svc) -> Response:
    """`kind` (drums / synth / audio) and `name` (default "Synth N" etc.).
    A new synth track starts on the default patch. Refused past the caps."""

    def change(song):
        kind = str(form.get("kind", ""))
        n = sum(t["kind"] == kind for t in song["tracks"]) + 1
        default = "Drums" if kind == "drums" else f"{kind.capitalize()} {n}"
        sm.add_track(song, kind, _title(form.get("name", "")) or default)

    return _commit(svc, change, label="add track")


@router.post("/song/track/{track_id}/rename")
def track_rename(request: Request, track_id: str, form: StrForm, svc: Svc):
    def change(song):
        _track(song, track_id)["name"] = _named(form, "track")

    return _commit(svc, change, label="rename track")


_TRUE = {"1", "on", "true", "yes"}
_FALSE = {"0", "off", "false", "no", ""}


def _flag(form: dict, key: str) -> bool:
    v = str(form[key]).strip().lower()
    if v in _TRUE | _FALSE:
        return v in _TRUE
    raise sm.SongError(f"{key.capitalize()} is on or off.")


@router.post("/song/track/{track_id}/sound")
def track_sound(request: Request, track_id: str, form: StrForm, svc: Svc):
    """A synth lane's sound menu: `sound` is "<source>:<name>" as
    `sounds.find` knows it (`preset`, `factory`, `recipes`, `library`).
    The track takes the sound's patch; a Sound that brings its own FX and
    curves (a recipe, a saved format-2 sound) brings those too, a plain
    patch leaves the lane's FX as they are. One undo step; the notes, the
    level and the mix stay. A track open in the sound dock follows
    (`state.follow_track_edit`, after every Song page request).

    `browse` (the drawer's sound picker, soundmenu.js: a token for one
    open of the menu, or one run of ↑ ↓ / the wheel on its button): every
    sound auditioned under the same token is ONE undo step — Ctrl+Z gives
    back the sound from before the browse. The answer's `song-changed`
    says `now`: the drawer's ▶ Play takes the new sound mid-pass."""
    from ..sounds import SOURCES, find

    source, _, name = form.get("sound", "").partition(":")
    if source not in SOURCES or not name:
        return Response(status_code=422)
    sound = find(svc, source, name)
    if sound is None:
        return svc.toast("That sound is gone.")
    n = schema.nodes()

    def change(song):
        t = _track(song, track_id)
        if t["kind"] != "synth":
            raise sm.SongError("Only a synth track has a sound.")
        t["patch"] = schema.conform(sound.patch.to_json(), n.PATCH)
        if sound.chain is not None:
            t["fx"] = schema.conform(copy.deepcopy(sound.chain["main_fx"]), n.FX)
            t["curves"] = schema.conform(copy.deepcopy(sound.chain["curves"]), n.CURVES)

    browse = re.sub(r"[^A-Za-z0-9]", "", form.get("browse", ""))[:16]
    if browse:
        ti = sm.track_index(svc.state.song, track_id)
        named = svc.state.song["tracks"][ti]["name"] if ti is not None else "track"
        return _commit(
            svc,
            change,
            label=f"sound of {named}"[:40],
            key=f"sound:{track_id}:{browse}",
            window=math.inf,
            soon=True,
            at_once=True,
        )
    return _commit(svc, change, label=f"sound: {name}"[:40], at_once=True)


@router.post("/song/track/{track_id}/mix")
def track_mix(request: Request, track_id: str, form: StrForm, svc: Svc):
    """Any of `mute`, `solo` (on/off), `gain` (0..1.5, clamped) and `role`
    (a `song_model.TRACK_ROLES` name, or empty for none); only the fields
    posted change. A soloed track silences every unsoloed one."""

    def change(song):
        t = _track(song, track_id)
        for key in ("mute", "solo"):
            if key in form:
                t[key] = _flag(form, key)
        if "gain" in form:
            try:
                g = float(form["gain"])
            except ValueError:
                g = math.nan
            if not math.isfinite(g):
                raise sm.SongError("Gain must be a number.")
            t["gain"] = min(1.5, max(0.0, g))
        if "role" in form:
            role = str(form["role"]).strip()
            if role and role not in sm.TRACK_ROLES:
                raise sm.SongError("No such role.")
            t["role"] = role or None

    # a fader drag posts all the way: one step a gesture
    if "gain" in form:
        return _commit(
            svc, change, label="track level", key=f"gain:{track_id}", soon=True
        )
    if "role" in form and not {"mute", "solo"} & set(form):
        return _commit(svc, change, label="track role", soon=True)
    label = "solo track" if "solo" in form else "mute track"
    return _commit(svc, change, label=label, soon=True)


@router.post("/song/track/{track_id}/delete")
def track_delete(request: Request, track_id: str, svc: Svc) -> Response:
    """Refused (naming the sections) while any section uses the track.
    Deleting the track whose sound Synths is editing ends that edit — here,
    not only through routing's view check (a request naming no view
    doesn't end it)."""
    s = svc.state

    def change(song):
        t = _track(song, track_id)
        if using := sm.remove_track(song, track_id):
            raise sm.SongError(
                f"{t['name']} is used in {_listed(using)} — clear those cells first."
            )

    def then():
        if s.song_edit_track == track_id:
            s.end_track_edit()

    return _commit(svc, change, label="delete track", then=then)


@router.post("/song/track/{track_id}/edit-sound")
def track_edit_sound(
    request: Request, track_id: str, form: StrForm, svc: Svc
) -> Response:
    """Open a synth track's sound on Synths: `state.edit_track` sets the
    loop's patch aside and mirrors every patch change there into the track
    until the user leaves Synths and the Song page. Answers with
    `HX-Location` to the editor view, which shows the "Editing <Track>
    track · ← back to Song" banner (`fragments/track_edit_banner.html`).

    `dock=1` (a row's ✎): stay on the Song page — the answer is the
    `song-dock` region, Synths itself in a frame beside the playlist."""
    s = svc.state
    if not _open_sound(s, track_id, request):
        return svc.toast("Only a synth or drums track has a sound to edit.")
    if form.get("dock") == "1":
        return _answer()
    # the page the edit is made from: Synths, or the Drum Lab for the kit
    path = f"/view/{s.track_edit_view()}"
    loc = {"path": path, "target": "#view", "swap": "outerHTML"}
    return Response(status_code=200, headers={"HX-Location": json.dumps(loc)})


def _open_sound(s, track_id, request: Request) -> bool:
    """Start (or keep) the sound edit of `track_id`: a synth track's sound,
    the drums track's kit. False for a track with none (audio, unknown).
    The edit writes the track's sound into the song in place, outside
    `_commit`: whatever it leaves is one undo step, taken when the Song
    page next uses the ring."""
    if s.song_edit_track == track_id:
        return True
    if not s.edit_track(track_id):
        return False
    # the page that opened it (events.ORIGIN_HEADER): routing tells it from
    # another tab looking on, whose reads must not end the edit
    s._edit_saved["page"] = request.headers.get(ORIGIN_HEADER)
    name = s.song["tracks"][sm.track_index(s.song, track_id)]["name"]
    s.song_undo_sound(track_id, f"sound of {name}")
    return True


def _dock_follows(s, track_id, request: Request) -> None:
    """While the sound dock is open it shows what was last reached for: a
    cell or a pattern opened in the drawer moves it to that lane's sound
    (the drums' kit for a beat). A lane with no sound (audio) leaves it."""
    if s.song_edit_track is not None and track_id is not None:
        _open_sound(s, track_id, request)


@router.post("/song/dock/close")
def dock_close(request: Request, svc: Svc) -> Response:
    """The dock's ✕: the track-sound edit ends (the loop's own patch, undo
    ring and FX come back, `end_track_edit`); the track keeps its sound."""
    svc.state.end_track_edit()
    return _answer()


# --- the pattern pool -------------------------------------------------------------


@router.post("/song/pattern/add")
def pattern_add(request: Request, form: StrForm, svc: Svc) -> Response:
    """An empty pattern: `kind` (drums / notes), `name` (default "Beat N" /
    "Riff N"). Refused past MAX_PATTERNS."""

    def change(song):
        sm.new_pattern(song, str(form.get("kind", "")), _title(form.get("name", "")))

    return _commit(svc, change, label="add pattern")


@router.post("/song/pattern/{pattern_id}/duplicate")
def pattern_duplicate(request: Request, pattern_id: str, svc: Svc) -> Response:
    """A copy of a pool pattern ("Name ′") in the pool, in no cell yet; the
    drawer opens on the copy. Refused past MAX_PATTERNS."""
    made: list[str] = []

    def change(song):
        made.append(sm.copy_pattern(song, pattern_id))

    def then():
        svc.state.song_drawer = {"pattern_id": made[0]}

    return _commit(svc, change, label="duplicate pattern", then=then)


@router.post("/song/pattern/{pattern_id}/rename")
def pattern_rename(request: Request, pattern_id: str, form: StrForm, svc: Svc):
    def change(song):
        _pattern(song, pattern_id)["name"] = _named(form, "pattern")

    return _commit(svc, change, label="rename pattern")


@router.post("/song/pattern/{pattern_id}/delete")
def pattern_delete(request: Request, pattern_id: str, svc: Svc) -> Response:
    """Refused, naming the sections, while any cell uses the pattern."""

    def change(song):
        p = _pattern(song, pattern_id)
        if using := sm.remove_pattern(song, pattern_id):
            raise sm.SongError(
                f"{p['name']} is used in {_listed(using)} — clear those cells first."
            )

    return _commit(svc, change, label="delete pattern")


# --- cells ------------------------------------------------------------------------


@router.post("/song/cell")
def cell_set(request: Request, form: StrForm, svc: Svc) -> Response:
    """Point a cell (`section_id`, `track_id`) at `pattern_id`, or clear it
    with "". The kinds must match; a longer pattern grows the section (and
    a toast says so)."""

    def change(song):
        pid = form.get("pattern_id") or None
        sm.set_cell(song, form.get("section_id"), form.get("track_id"), pid)

    label = "set cell" if form.get("pattern_id") else "clear cell"
    return _commit(svc, change, label=label)


@router.post("/song/cell/move")
def cell_move(request: Request, form: StrForm, svc: Svc) -> Response:
    """Move a cell's pattern to another cell (`from_section`, `from_track`
    -> `section_id`, `track_id`): the destination plays it and the source
    is cleared, in ONE commit — one undo step, where a copy and a clear
    posted one after the other were two. The playlist's plain drag of a
    block (songdrag.js; Ctrl or Alt copies, through `/song/cell`). Along a
    track the cell's automation lanes go with it; across tracks they are
    left behind; a trimmed block keeps its span, held to where it lands
    (`sm.move_cell`). The kinds must match; a longer pattern grows the
    destination. A drawer open on the source follows the pattern."""
    s = svc.state
    src = {"section_id": form.get("from_section"), "track_id": form.get("from_track")}
    dst = {"section_id": form.get("section_id"), "track_id": form.get("track_id")}

    def change(song):
        sm.move_cell(song, *src.values(), *dst.values())

    def then():
        if {k: (s.song_drawer or {}).get(k) for k in src} == src:
            s.song_drawer = dst

    return _commit(svc, change, label="move cell", then=then)


@router.post("/song/cell/span")
def cell_span(request: Request, form: StrForm, svc: Svc) -> Response:
    """Where a cell's block sits in its section (`section_id`, `track_id`):
    `at` (0-based bar) and `len` (bars) — the pattern plays bars [at, at +
    len) of each pass, restarting at `at`, and is silent outside. Both are
    held inside the section; a span that covers it clears it ("Fill
    section" posts exactly that). With `base=1` the two fields are the
    drawer's `from` / `to`, 1-based and inclusive.

    Spill: `to_section` + `to_len` carry the block on past its section's
    end into the following sections on the same track, as far as `to_len`
    bars into `to_section` (`sm.spill_span`: empty cells take the pattern,
    the same pattern is extended, another pattern or a section too short
    for it stops it; nothing is overwritten). The toast says how far it
    got.

    One undo step ("trim block" / "extend block" / "slide block"); a held
    Alt+arrow, or the drawer's field stepped, posts many times a gesture
    and joins them (`key="span:<section>:<track>"`). A spill never joins:
    it is its own step."""
    sid, tid = form.get("section_id", ""), form.get("track_id", "")
    said: dict = {}

    def change(song):
        sec = _section(song, sid)
        was = sm.cell_span(sec, tid)
        if form.get("base") == "1":  # the drawer: bars `from`..`to`, 1-based
            at = _int(form, "from", "The first bar") - 1
            end = _int(form, "to", "The last bar")
            at = min(max(0, at), sec["bars"] - 1)
            now = sm.set_span(song, sid, tid, at, max(1, end - at))
        elif form.get("to_section"):
            _track(song, tid)
            reached = sm.spill_span(
                song, sid, tid, form["to_section"], _int(form, "to_len", "The length")
            )
            now = sm.cell_span(sec, tid)
            name = song["patterns"][sec["cells"][tid]]["name"]
            said["text"] = (
                f"{name} now runs on through {_listed(reached)}."
                if reached
                else f"{name} runs to the end of {sec['name']}: the next section "
                "plays another pattern on this track, or is too short for it."
            )
            said["label"] = "extend block"
        else:
            now = sm.set_span(
                song,
                sid,
                tid,
                _int(form, "at", "The bar"),
                _int(form, "len", "The length"),
            )
        if "label" not in said:
            said["label"] = (
                "slide block"
                if now[1] == was[1]
                else "extend block"
                if now[1] > was[1]
                else "trim block"
            )

    def then():
        text = said.get("text")
        return {"studio-toast": {"message": text}} if text else None

    return _commit(
        svc,
        change,
        label=lambda: said.get("label", "trim block"),
        key=None if form.get("to_section") else f"span:{sid}:{tid}",
        then=then,
    )


def _open_cell(svc: StudioService, form: dict) -> Callable[[], None]:
    def then():
        svc.state.song_drawer = {
            "section_id": form.get("section_id"),
            "track_id": form.get("track_id"),
        }

    return then


@router.post("/song/cell/new")
def cell_new(request: Request, form: StrForm, svc: Svc) -> Response:
    """A fresh empty pattern straight into a cell (`section_id`,
    `track_id`): one step instead of "+ pattern" in the pool and then
    assigning it. What the cell held stays in the pool. The drawer opens on
    the cell. It is named "Beat N" / "Riff N" and takes no `name`: the
    arranger's N key posts through `htmx.ajax`, which sends every field on
    the page along (the drawer's rename box, the section menus' names)."""

    def change(song):
        sm.new_cell_pattern(song, form.get("section_id"), form.get("track_id"))

    return _commit(svc, change, label="new pattern", then=_open_cell(svc, form))


@router.post("/song/cell/unique")
def cell_unique(request: Request, form: StrForm, svc: Svc) -> Response:
    """Fork this cell's pattern ("Name ′") for this cell alone; the drawer
    opens on the copy."""

    def change(song):
        sm.make_unique(song, form.get("section_id"), form.get("track_id"))

    return _commit(svc, change, label="make unique", then=_open_cell(svc, form))


@router.post("/song/cell/variation")
def cell_variation(request: Request, form: StrForm, svc: Svc) -> Response:
    """Fork this cell's pattern and mutate the copy (`seed` optional, for a
    repeatable variation); the drawer opens on it.

    A beat's family (`gen/build.cell_variant`): `var` = A-D makes that
    variation of the cell's beat (a few light hits traded, by `amount`;
    `copy=1`: an exact copy) — or picks it, when the family has one — and
    `fill=1` its fill, rolled again in place when it has one. The cell
    plays the result; a section picks a variation that exists with
    `POST /song/cell`. Rolling a fill again is the same undo step."""
    var = "fill" if form.get("fill") else form.get("var") or ""

    def change(song):
        if form.get("seed"):
            seed = _int(form, "seed", "The seed")
        else:
            seed = secrets.randbelow(2**31)
        if not var:
            sm.variation(song, form.get("section_id"), form.get("track_id"), seed)
            return
        amount = _unit(form, "amount", "The amount") if form.get("amount") else 0.25
        build.cell_variant(
            song,
            form.get("section_id"),
            form.get("track_id"),
            var,
            seed % limits.SEED_MODULO,
            exact=bool(form.get("copy")),
            amount=max(0.05, amount),
        )

    cell = f"{form.get('section_id')}:{form.get('track_id')}"
    opened = _open_cell(svc, form)

    def then():
        opened()
        # a generator's result plays by itself (the fill; a varied letter)
        made = var == "fill" or (var and not form.get("copy"))
        return {"song-autoplay": {"section": form.get("section_id")}} if made else None

    return _commit(
        svc,
        change,
        label=f"variation {var}".strip()[:40],
        key=f"fill:{cell}" if var == "fill" else None,
        window=math.inf if var == "fill" else None,
        then=then,
    )


def _unit(form: dict, key: str, what: str) -> float:
    """A 0..1 slider value from a form, or a SongError (a toast)."""
    try:
        v = float(str(form.get(key, "")).strip())
    except ValueError:
        v = math.nan
    if not math.isfinite(v):
        raise sm.SongError(f"{what} must be a number.")
    return min(1.0, max(0.0, v))


@router.post("/song/cell/auto")
def cell_auto(request: Request, form: StrForm, svc: Svc) -> Response:
    """A cell's automation lanes (`song_model.set_lane`): `section_id`,
    `track_id`, `target` ("gain", a curvable field, or a parameter of one
    of the track's FX units: "fx:<unit id>:<kind>:<param>") and `op`:

    - `add`: a new lane, flat at where the sound rests (`sm.lane_rest`), so
      nothing moves until it is shaped;
    - `set`: `start` and `end` (0..1 of the target's range), and with
      `mid_on` a third breakpoint `mid` at `mid_at` (defaults: halfway
      between them, at the middle of the pass);
    - `remove` (also of a lane whose FX unit is gone).

    The drawer's menu names an FX unit that has no id yet by its place
    ("fx:@<index>:<kind>:<param>"); `add` gives the unit its id
    (`sm.claim_fx_target`) in the same commit, so one Ctrl+Z takes both
    back. A lane makes the section render as one pass, so a section too long for
    that (`MAX_SECTION_SECONDS`) refuses the lane. The drawer and the cell's
    marker come back by their region watches."""
    op, posted = form.get("op"), form.get("target")
    sid, tid = form.get("section_id"), form.get("track_id")

    def change(song):
        sec, track = _section(song, sid), _track(song, tid)
        if op == "remove":
            sm.remove_lane(song, sid, tid, posted)
            return
        target = sm.claim_fx_target(track, posted) if op == "add" else posted
        if target not in sm.lane_targets(track):
            raise sm.SongError(f"{track['name']} can't automate that.")
        if op == "add":
            if any(ln["target"] == target for ln in sm.cell_lanes(sec, tid)):
                raise sm.SongError("That is automated here already.")
            rest = sm.lane_rest(track, target)
            points = [[0.0, rest], [1.0, rest]]
        elif op == "set":
            start, end = _unit(form, "start", "Start"), _unit(form, "end", "End")
            points = [[0.0, start], [1.0, end]]
            if form.get("mid_on"):
                at = _unit(form, "mid_at", "The mid point") if "mid_at" in form else 0.5
                mid = _unit(form, "mid", "Mid") if "mid" in form else (start + end) / 2
                points.insert(1, [min(0.95, max(0.05, at)), mid])
        else:
            raise sm.SongError("No such lane edit.")
        sm.set_lane(song, sid, tid, target, points)
        seconds = sm.cycle_bars(song, sec) * 4 * 60.0 / song["bpm"]
        if seconds > limits.MAX_SECTION_SECONDS:
            raise sm.SongError(
                f"Automation plays {sec['name']} as one {seconds:.0f}s pass — the "
                f"cap is {limits.MAX_SECTION_SECONDS:.0f}s. Shorten the section "
                "or raise the bpm."
            )

    if op == "set":  # a lane's sliders post on every change: one step a drag
        lane = f"auto:{sid}:{tid}:{posted}"
        return _commit(svc, change, label="automation", key=lane, soon=True)
    label = "remove automation lane" if op == "remove" else "add automation lane"
    return _commit(svc, change, label=label, soon=True)


@router.post("/song/drawer")
def drawer_open(request: Request, form: StrForm, svc: Svc) -> Response:
    """Open the drawer on a cell (`section_id` + `track_id`: it follows
    whatever the cell holds) or on a pool pattern (`pattern_id`); neither,
    or ids that don't resolve, closes it. Changes no audio, so no
    song-changed."""
    s = svc.state
    song = s.song
    sid, tid, pid = form.get("section_id"), form.get("track_id"), form.get("pattern_id")
    if (
        sm.section_index(song, sid) is not None
        and sm.track_index(song, tid) is not None
    ):
        s.song_drawer = {"section_id": sid, "track_id": tid}
        _dock_follows(s, tid, request)
    elif pid in song["patterns"]:
        s.song_drawer = {"pattern_id": pid}
        # ▶ Play's lane ("through"): kept while this pattern stays open
        through = form.get("through")
        if song_bridge.pattern_lane(song, pid, through) == through:
            s.song_drawer["through"] = through
            _dock_follows(s, through, request)
        # a pool pattern: the first lane, in song order, that plays it
        _dock_follows(
            s,
            next(
                (
                    t
                    for sec in song["sections"]
                    for t, p in sec["cells"].items()
                    if p == pid
                ),
                None,
            ),
            request,
        )
    else:
        s.song_drawer = None
    # the sound dock holding a synth lane: its ▶ follows the block opened
    # (`StudioService.track_heard`), so the frame renders it again
    # (songdock.js); a lane it moved to reloads the frame anyway
    if s.track_edit_view() == "editor":
        return _answer({"dock-reheard": True})
    return _answer()


@router.post("/song/migration-note/clear")
def migration_note_clear(svc: Svc) -> Response:
    """The Song page shows the v2 -> v3 migration note once: the toast
    posts this as it appears (and again from its close button, which
    swaps it away with the empty answer)."""
    s = svc.state
    if s.clear_migration_note():
        s.save()
    return HTMLResponse("")


# --- the drawer's pattern grid ----------------------------------------------------


def _choice(form: dict, key: str, choices, current: int) -> int:
    """A grid-shape field, or the pattern's own value when it's missing or
    not one of the choices (hostile input never reshapes a pattern)."""
    try:
        v = int(str(form.get(key, "")).strip())
    except ValueError:
        return current
    return v if v in choices else current


def _bars(form: dict, per_bar: int, p: dict) -> int:
    """A pool pattern's bars from a grid form: one of the bars its
    resolution allows (`sm.bar_choices`: up to 8 while bars x steps
    fits the grid), else what it has, held to what still fits (finer
    steps on an 8-bar pattern shorten it to 4)."""
    fits = sm.bar_choices(per_bar)
    held = max((b for b in fits if b <= p["bars"]), default=1)
    return _choice(form, "bars", fits, held)


def _octave(form: dict) -> int:
    """The octave window the drawer's note grid was drawn at (its
    hidden `oct`): whole octaves from the song's root, -2..+4."""
    try:
        return min(4, max(-2, int(str(form.get("oct", "0")).strip())))
    except ValueError:
        return 0


def _drums_grid(song: dict, p: dict, form: dict) -> None:
    """The Drums view's grid form (`drum.<voice>.<step>`, `dvel.<step>`,
    `bars`, `steps`) into a drums pattern: the hits are replaced, velocities
    merged. More bars at the same resolution repeat the ones there
    (`beatgen.extend_bars`), as the Drums transport does."""
    m = sm.pattern_meter(p)
    steps = _choice(form, "steps", meter.step_choices(m), p["steps"])
    bars = _bars(form, steps, p)
    merged = {"bars": str(bars), "steps": str(steps)}
    merged |= {k: v for k, v in form.items() if k.startswith("drum.")}
    drums = arrangement_from_form(merged, sm.bar_choices(steps), m).drums
    vel = dict(p["drum_vel"])
    for k, v in form.items():
        key = k[5:]
        if k.startswith("dvel.") and key.isascii() and key.isdigit():
            try:
                x = float(v)
            except ValueError:
                continue
            if math.isfinite(x):
                vel[str(int(key))] = min(1.0, max(0.0, x))
    # the per-hit table (the grid's hidden `hits`, or `hit.*` fields) and
    # the rows' own swing (`rswing`): a hit without a cell is dropped
    hits = drumops.hits_from_form(form, drums, p.get("hits"))
    swing = drumops.row_swing_from_form(form, p.get("swing"), song["drum_swing"])
    if bars > p["bars"] and steps == p["steps"]:
        old, total = p["bars"] * steps, bars * steps
        offsets = beatgen.repeat_offsets(drums, old, total)
        drums = beatgen.extend_bars(drums, p["bars"], bars, steps)
        vel = _extend_vel(vel, old, total)
        hits = _extend_hits(hits, old, total, offsets)
    p.update(drums=drums, drum_vel=vel, bars=bars, steps=steps)
    _set_optional(p, hits=hits, swing=swing)


def _set_optional(p: dict, **values) -> None:
    """A pattern's optional keys: set, or absent when empty."""
    for k, v in values.items():
        if v:
            p[k] = v
        else:
            p.pop(k, None)


def _notes_grid(song: dict, p: dict, form: dict) -> None:
    """Perform's note-grid form (`cell.<pitch>.<step>`, `vel.<step>`,
    `slide.<step>`, `bars`, `steps`, `arp_mode`, `arp_octaves`) into a
    notes pattern, parsed at the song's key. A post carrying the grid's
    shape (`bars`/`steps`, which the grid form always holds) replaces the
    notes, slides and velocities; otherwise only the lanes it posts. A note
    on a pitch outside the key's rows (written under another key) can't be
    posted, so it is kept, not deleted."""
    m = sm.pattern_meter(p)
    spb = _choice(form, "steps", meter.step_choices(m), p["steps_per_bar"])
    bars = _bars(form, spb, p)
    merged = {"bars": str(bars), "steps": str(spb)}
    merged |= {"arp_mode": p["arp_mode"], "arp_octaves": str(p["arp_octaves"])}
    merged["strum"] = str(p.get("strum", 0.0))
    lanes = ("vel.", "slide.", "arp_mode", "arp_octaves", "strum")
    merged |= {k: v for k, v in form.items() if k.startswith(lanes)}
    arr = arrangement_from_form(merged, sm.bar_choices(spb), m)
    whole = "bars" in form or "steps" in form
    # the rows the drawer's grid was drawn at: it posts its own `span`
    # and the octave window (`oct`) it was showing
    span = int(form["span"]) if form.get("span") in ("1", "2", "3") else 1
    rows = set(sm.window_rows(song, span, _octave(form)))
    total = bars * spb
    posted, posted_cuts = [], []
    for k, v in form.items():
        if not k.startswith("cell."):
            continue
        pitch, _dot, raw = k[5:].rpartition(".")
        st = limits.step_key(raw)
        if pitch in rows and st is not None and int(st) < total:
            posted.append([pitch, int(st)])
            if v == "cut":  # starts a new note (Arrangement.cuts)
                posted_cuts.append([pitch, int(st)])
    posted.sort(key=lambda c: (c[0], c[1]))
    posted_cuts.sort(key=lambda c: (c[0], c[1]))
    hidden = [list(c) for c in p["cells"] if c[0] not in rows and c[1] < total]
    p.update(
        bars=arr.bars,
        steps_per_bar=arr.steps_per_bar,
        arp_mode=arr.arp_mode,
        arp_octaves=arr.arp_octaves,
    )
    if arr.strum:  # absent when unset, as the schema writes it
        p["strum"] = arr.strum
    else:
        p.pop("strum", None)
    for field, prefix in (
        ("velocities", "vel."),
        ("slides", "slide."),
    ):
        if whole or any(k.startswith(prefix) for k in form):
            p[field] = copy.deepcopy(getattr(arr, field))
    if whole or any(k.startswith("cell.") for k in form):
        p["cells"] = posted + hidden
        hid = {(c[0], c[1]) for c in hidden}
        p["cuts"] = posted_cuts + [
            list(c) for c in p.get("cuts") or [] if (c[0], c[1]) in hid
        ]
    # a loop's timing inside a step stays on the notes still there; its
    # chance lane on the steps still in the grid (absent when none)
    _set_optional(
        p,
        note_nudge=schema.clean_nudge(p.get("note_nudge"), p["cells"]),
        note_chance={
            k: v for k, v in (p.get("note_chance") or {}).items() if int(k) < total
        },
    )


@router.post("/song/pattern/{pattern_id}/grid")
def pattern_grid(request: Request, pattern_id: str, form: StrForm, svc: Svc):
    """Edit a pool pattern from the drawer, posting the same field names as
    the Drums grid (drums) or Perform's note grid (notes). Never touches
    the working loop. A pattern that grows past a section using it grows
    that section (a toast says so). The drawer grid posts with
    hx-swap="none", so the drawer itself is only redrawn when the
    pattern's shape (bars / steps) changed."""

    did: dict = {}

    def change(song):
        p = _pattern(song, pattern_id)
        shape = (p["bars"], p.get("steps"), p.get("steps_per_bar"))
        hits = copy.deepcopy((p.get("cells"), p.get("cuts"), p.get("drums")))
        swing = dict(p.get("swing") or {})
        (_drums_grid if p["kind"] == "drums" else _notes_grid)(song, p, form)
        if (p["bars"], p.get("steps"), p.get("steps_per_bar")) == shape:
            # the grid posted it: never rebuild it under the pointer
            svc.suppress("song-drawer")
            # the same notes: a velocity bar (or a slide, the arp) moved.
            # A dragged bar posts all the way and is one undo step
            if hits == (p.get("cells"), p.get("cuts"), p.get("drums")):
                did.update(label="pattern velocity", key=f"vel:{pattern_id}")
                if swing != (p.get("swing") or {}):  # a row's swing knob
                    did.update(label="row swing", key=f"rswing:{pattern_id}")
        else:
            did["label"] = "pattern length"

    return _commit(
        svc,
        change,
        label=lambda: did.get("label", "edit pattern"),
        key=lambda: did.get("key"),
        soon=True,
    )


# the beat workflow's tools on a pool beat (the generators the loop's
# `POST /fragment/gen` and row tools run)
_BEAT_TOOLS = ("euclid", "hitclear", "busier", "sparser", "fill")


def _beat_tool(song: dict, p: dict, op: str, form: dict, seed: int) -> dict:
    """One of `_BEAT_TOOLS` on a pool beat: the lane it leaves."""
    lane = gd.lane_of(p)
    if op in ("euclid", "hitclear"):
        voice = form.get("voice", "")
        if voice not in DRUM_ROWS:
            raise sm.SongError("No such drum row.")
        if op == "hitclear":
            kept = {
                k: v for k, v in lane["hits"].items() if not k.startswith(f"{voice}.")
            }
            return {**lane, "hits": kept}
        n = _int(form, "hits", "Euclid's hits")
        of = _int(form, "of", "Euclid's steps")
        rotate = _int(form, "rotate", "Euclid's turn") if form.get("rotate") else 0
        if not (0 <= n <= of and 1 <= of <= limits.MAX_GRID_STEPS):
            raise sm.SongError("Euclid: 0 to all of the steps, over 1 or more steps.")
        if abs(rotate) > limits.MAX_GRID_STEPS:
            raise sm.SongError("Euclid: the rotation is past the grid.")
        return drumops.euclid_row(lane, voice, n, of, rotate)
    if not lane["drums"]:
        raise sm.SongError("There is no beat here yet.")
    if op == "fill":
        return gd.fill(lane, seed, 1.0, None, style=build._style_of(song, p))
    return (gd.busier if op == "busier" else gd.sparser)(lane, 0.25, seed)


def _track_tones(svc, song: dict, p: dict, form: dict, rows: list[str]) -> list[str]:
    """The chord-track stamp: the chord under the stamped step (the open
    cell's section's own chords, else the song's — `chordtrack.resolve`),
    root position, its root the nearest one at or below the clicked row."""
    d = svc._song_drawer() or {}
    try:
        step = int(str(form.get("step", "0"))[:6])
    except ValueError:
        step = 0
    chord = chordtrack.chord_at(
        chordtrack.resolve(song, d.get("section"), p["bars"], p["steps_per_bar"]),
        step,
    )
    at = midi(form["row"]) if form.get("row") in rows else midi(rows[-1])
    return [name_of(m) for m in chordtrack.voiced(chord, at)]


@router.post("/song/pattern/{pattern_id}/tool/{op}")
def pattern_tool(request: Request, pattern_id: str, op: str, form: StrForm, svc: Svc):
    """The drawer's pattern tools on a pool pattern: the same edits as
    Perform's grids (`patternops.notes_tool` / `drums_tool`) — shift,
    reverse, x2, clear, transpose along the song's key, the velocity tools
    — and the piano roll's (`patternops.roll_tool`, reading the form's
    `sel`, `dx` / `dy`, `by`, the stamp's fields and the `span` / `oct`
    the grid was drawn at; a stamp with `q=track` writes the chord the
    chord track holds under that step, voiced from the clicked row).
    Every section using the pattern follows (it is shared); one that the
    doubled pattern outgrows grows with it (the song schema's rule, and a
    toast). Never touches the working loop."""
    note, now = "", None
    span = svc.state.grid_span
    if form.get("span") in ("1", "2", "3"):
        span = int(form["span"])

    def change(song):
        nonlocal note, now
        p = _pattern(song, pattern_id)
        seed = random.randrange(1 << 30)
        try:
            if p["kind"] == "drums":
                if op in _BEAT_TOOLS:
                    lane = _beat_tool(song, p, op, form, seed % limits.SEED_MODULO)
                else:
                    fits = sm.bar_choices(p["steps"])
                    lane = drumops.drums_tool(
                        op, gd.lane_of(p), choices=fits, seed=seed
                    )
                p.update({k: lane[k] for k in ("bars", "steps", "drums", "drum_vel")})
                _set_optional(p, hits=lane.get("hits"), swing=lane.get("swing"))
                return
            key = Arrangement(root=song["root"], scale_name=song["scale_name"])
            # its timing inside a step and its chance lane travel with the
            # notes as the loop's do (`patternops`: in the lane, kept)
            lane = {
                k: p.get(k) or ([] if k not in ("velocities", "note_nudge") else {})
                for k in NOTE_LANE
            }
            lane["note_chance"] = dict(p.get("note_chance") or {})
            # the window the drawer shows this pattern at (a roll edit
            # posts the one its grid was drawn with)
            octave = _octave(form) if "oct" in form else svc.drawer_octave(p, span)
            rows = sm.window_rows(song, span, octave)
            if po.is_roll(op, form):
                args = dict(form)
                if op == "stamp" and form.get("q") == "track":
                    args["tones"] = ",".join(_track_tones(svc, song, p, form, rows))
                lane, note, now = po.roll_tool(
                    op, lane, rows=rows, ladder=key_ladder(key), args=args
                )
            else:
                lane, note = po.notes_tool(
                    op,
                    lane,
                    rows=set(rows),
                    ladder=key_ladder(key),
                    octave=len(SCALES[key.scale_name]),
                    choices=sm.bar_choices(p["steps_per_bar"]),
                    seed=seed,
                    arp_on=p.get("arp_mode", "off") != "off",
                )
            optional = {k: lane.pop(k, None) for k in ("note_nudge", "note_chance")}
            p.update(lane)
            _set_optional(
                p,
                note_nudge=schema.clean_nudge(optional["note_nudge"], p["cells"]),
                note_chance=optional["note_chance"],
            )
        except po.ToolRefused as err:
            raise sm.SongError(str(err)) from None
        except KeyError:
            raise sm.SongError("No such pattern tool.") from None

    def then():
        said = {"studio-toast": {"message": note}} if note else {}
        # the roll's selection after the edit (roll.js follows it)
        return said | ({"roll-sel": {"sel": now}} if now is not None else {}) or None

    label = "clear pattern" if op == "clear" else f"pattern {op.replace('_', ' ')}"
    return _commit(svc, change, label=label[:40], then=then)


# --- the loop bridges -------------------------------------------------------------


@router.post("/song/from-loop")
def song_from_loop(request: Request, form: StrForm, svc: Svc) -> Response:
    """ "Add loop to song": the working loop becomes a new last section
    (`state.loop_to_section`; its lanes become pool patterns, shared when
    identical, and the beat's other variations that hold hits go into the
    pool beside the one it plays). `name` optional.

    Asked from another page (Perform's and Synths' → Song) the answer has
    a body, `fragments/to_song_note.html`, for the button's note: where
    the loop went, a link to Song and — when the song already had a drums
    track whose kit is not the loop's (the section plays the loop's beat
    through that track's kit) — the offer to put the loop's kit on it
    (`POST /song/drums/loop-kit`); and a toast saying the same."""
    s = svc.state
    # the loop's own patch, curves and FX are what goes into the song: a
    # track-sound edit (kept open on the Song page: the sound dock) has the
    # track's in their place until it ends
    s.end_track_edit()
    old = s.song
    try:
        sid, pooled = s.loop_into_song(_title(form.get("name", "")))
        _check_length(old, s.song)
    except sm.SongError as err:
        s.song = old
        return svc.toast(str(err))
    s.song_undo_push(old, "add loop to song")
    if svc.view_of(request) == "song":
        return _changed(svc, old)
    note = _sent_note(svc, old, sid, pooled)
    resp = _changed(svc, old, extra={"studio-toast": {"message": note["said"]}})
    html = svc.templates.get_template("fragments/to_song_note.html").render(**note)
    return HTMLResponse(html, headers={"HX-Trigger": resp.headers["HX-Trigger"]})


def _loop_kit_differs(svc: StudioService, track: dict) -> bool:
    """Does the song's drums `track` play another kit than the loop's
    (`song_bridge.KIT`: the voices' shaping, the mixer, the rows' samples)?"""
    arr = svc.state.loop_arr_dict()
    return any(track[k] != arr[k] for k in song_bridge.KIT)


def _drums_track_index(song: dict) -> int | None:
    """Where the song's drums track is in its tracks (a song has one at
    most), or None."""
    return next((n for n, t in enumerate(song["tracks"]) if t["kind"] == "drums"), None)


def _sent_note(svc: StudioService, old: dict, sid: str, pooled: list) -> dict:
    """→ Song's note: the section the loop became, what went to the pool,
    and the kit offer (`to_song_note.html`)."""
    s = svc.state
    i = sm.section_index(s.song, sid)
    sec = s.song["sections"][i]
    said = (
        f"Added “{sec['name']}” to the song: section {i + 1}, "
        f"{plural(sec['bars'], 'bar')}"
    )
    if not s.loop_arr_dict()["drums"]:
        said += ". The loop has no beat, so none went with it"
    if pooled:
        names = [s.song["patterns"][pid]["name"] for _letter, pid in pooled]
        letters = [letter for letter, _ in pooled]
        said += (
            f". It plays beat {s.beat_var}; a section holds one beat, so "
            f"{_listed(letters)} {'is' if len(letters) == 1 else 'are'} in the "
            f"song's pool ({_listed([f'“{n}”' for n in names])}) for a cell to take"
        )
    had = _drums_track_index(old)
    track = (
        s.song["tracks"][sm.track_index(s.song, old["tracks"][had]["id"])]
        if had is not None
        else None
    )
    offer = None
    if track is not None and _loop_kit_differs(svc, track):
        offer = {"track": track["name"], "kit": beatpick.kit_of(s) or "the loop's kit"}
        said += f". Its beat plays through the song's {track['name']} kit"
    return {"said": said + ".", "offer": offer}


@router.post("/song/drums/loop-kit")
def song_drums_loop_kit(request: Request, form: StrForm, svc: Svc) -> Response:
    """→ Song's offer: the working loop's kit (`song_bridge.KIT`: the
    voices' shaping, the mixer, the rows' samples) on the song's drums
    track — every section's beat then plays through it. One song undo
    step. Its body is the note it replaces (`to_song_note.html`)."""
    s = svc.state
    s.end_track_edit()
    old = s.song
    i = _drums_track_index(old)
    if i is None:
        return svc.toast("The song has no drums track.")
    if not _loop_kit_differs(svc, old["tracks"][i]):
        return svc.toast("The song's drums already play the loop's kit.")
    song = copy.deepcopy(old)
    song_bridge.apply_kit(song["tracks"][i], s.loop_arr_dict())
    s.song = schema.conform(song, schema.nodes().SONG)
    s.song_undo_push(old, "the loop's kit on the drums")
    name = song["tracks"][i]["name"]
    said = (
        f"The song's {name} track plays {beatpick.kit_of(s) or 'the loop’s kit'} "
        "now: every section's beat goes through it (Ctrl+Z on Song takes it back)."
    )
    resp = _changed(svc, old, extra={"studio-toast": {"message": said}})
    html = svc.templates.get_template("fragments/to_song_note.html").render(
        said=said, offer=None
    )
    return HTMLResponse(html, headers={"HX-Trigger": resp.headers["HX-Trigger"]})


@router.post("/song/section/{section_id}/to-loop")
def section_to_loop(request: Request, section_id: str, svc: Svc) -> Response:
    """ "Open in Perform": load the section's patterns and track sounds into
    the working loop (`state.section_to_loop`), then go to Perform
    (`HX-Location`). The song itself is unchanged."""
    s = svc.state
    i = sm.section_index(s.song, section_id)
    trimmed = s.song["sections"][i].get("spans") if i is not None else None
    if not s.section_to_loop(section_id):
        return svc.toast("No such section.")
    s.save()
    loc = {"path": "/view/perform", "target": "#view", "swap": "outerHTML"}
    headers = {"HX-Location": json.dumps(loc)}
    if trimmed:
        # the loop has one pattern a lane, start to end: no spans
        n = len(trimmed)
        said = (
            f"The loop has no trimmed blocks: {n} block{'' if n == 1 else 's'} of "
            "this section opened as the whole pattern."
        )
        headers["HX-Trigger"] = json.dumps({"studio-toast": {"message": said}})
    return Response(status_code=200, headers=headers)
