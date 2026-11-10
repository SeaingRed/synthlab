"""The Sample Lab: import a whole song, find its grid and key, separate it
into stems, and cut regions out as clips and sampler sounds
(`sources.py` the store, `lab.py` the logic, `labjobs.py` the separation
job; the engine halves are `analysis.rhythm` / `analysis.key` and
`synthlab/stems.py`).

The Lab is a page of its own with no region in `service.REGIONS`: every
POST answers with `#lab-body` (the status line, the source list and the
panel), and nothing it stores is session state — a source's grid, region
and picked stems live in the source's own meta. What leaves the Lab is a
clip (`clips.save_clip`) or a sampler sound (`routes.sampler.adopt_audio`,
the path "⤷ sampler" on a clip takes), and those are the session's as they
always were.

Every id is checked (`sources.valid_id`) before it reaches a path, every
number is parsed, checked finite and clamped, and anything unreadable is a
422 toast.
"""

from __future__ import annotations

import json
import math
import tempfile
import time
import zipfile
from dataclasses import replace

import numpy as np
from fastapi import Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from ...analysis import drumhits, voiced
from ...audio import wav_bytes
from ...core import SR
from .. import lab, labchops, labclean, labmake, limits, sources
from .. import song_model as sm
from ..clips import clip_dir, clip_safe, delete_clip, free_clip_name, save_clip
from ..files import safe_name, zip_write
from ..routing import (
    OutsideLock,
    SourceUpload,
    StrForm,
    Svc,
    lock_free,
    pattern_edit,
    render_outside_lock,
    studio_router,
)
from ..service import StudioService
from .sampler import adopt_audio
from .song import _commit, _moved
from .song_record import place_clip

router = studio_router()


def _number(raw, default: float | None = None) -> float | None:
    """A form or query value as a finite float, else `default`."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) and abs(v) < 1e15 else default


def body(
    svc: StudioService,
    selected: str = "",
    said: str = "",
    rows=(),
    go: dict | None = None,
) -> HTMLResponse:
    """The Lab's body. `said` is what the last action found or did, kept on
    the page (a toast is gone before a pattern can be read); `rows` are a
    transcription's lanes, drawn under it as the drum notation writes them;
    `go` is where the made thing went (`_went`): the page to open it on and
    the Lab's own undo of it."""
    ctx = svc.lab_context(selected)
    ctx["said"], ctx["rows"] = said, list(rows)
    ctx["go"] = go or {}
    html = svc.templates.get_template("fragments/lab_body.html").render(lab=ctx)
    return HTMLResponse(html)


def chops_oob(svc: StudioService, request: Request) -> str:
    """The open song's chop list, out of band, for a request the Lab page
    made that is not a Lab post: the clip library's rename and delete
    (`routes/clips.py`). A chop row says which clip it was kept as and
    offers "Update clip" only while that clip is there, so a clip renamed
    or deleted from the drawer on this page must redraw it. "" on any
    other page, or with no song open."""
    if svc.view_of(request) != "lab":
        return ""
    ctx = svc.lab_context()
    if not ctx["source"]:
        return ""
    tpl = svc.templates.get_template("fragments/lab_chops.html")
    return tpl.render(src=ctx["source"], chops_oob=True)


def _say(resp: Response, message: str) -> Response:
    resp.headers["HX-Trigger"] = json.dumps({"studio-toast": {"message": message}})
    return resp


def _meta(svc: StudioService, form: dict) -> dict | None:
    return sources.read_meta(svc.data_dir, form.get("source", ""))


# where a made thing lands: (the button's words, the page, a popover to open)
OPEN_ON = {
    "clips": ("Open in Clips", "", "clip-drawer"),
    "drums": ("Open on Kit", "/view/drums", ""),
    "song": ("Open on Song", "/view/song", ""),
}
# the song's undo steps the Lab makes: its undo takes back only these
POOL_LABEL, PLACE_LABEL = "beat from the Sample Lab", "place clip"


def _went(where: str, **undo: str) -> dict:
    """`go` for `body`: the page the made thing is on, and the fields
    `POST /lab/undo` takes it back with (none: no undo from here)."""
    label, href, popover = OPEN_ON[where]
    return {"label": label, "href": href, "popover": popover, "undo": undo}


def _lab_clip(data_dir, sid: str, name: str) -> bool:
    """Whether the clip `name` is one the Lab made from source `sid` (its
    meta's `lab.source`) — the only clips the Lab's undo deletes."""
    path = clip_dir(data_dir) / f"{clip_safe(name)}.meta.json"
    try:
        return json.loads(path.read_text("utf-8"))["lab"]["source"] == sid
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return False


@router.get("/fragment/lab")
def lab_fragment(request: Request, svc: Svc) -> Response:
    """The Lab's body for `?source=` (the most recently opened without)."""
    return body(svc, request.query_params.get("source", ""))


@router.get("/fragment/lab/job")
@lock_free
def lab_job(request: Request) -> Response:
    """The status line, polled once a second while a job runs. When the
    job it was watching has ended (`watch=1` and nothing running), the
    panel comes back out of band with it: the stem lanes, or the error.
    Lock-free: it reads the job's own state and the source store."""
    svc: StudioService = request.app.state.svc
    ctx = svc.lab_context(request.query_params.get("source", ""))
    ended = request.query_params.get("watch") == "1" and ctx["job"] is None
    html = svc.templates.get_template("fragments/lab_job.html").render(lab=ctx)
    if ended:
        for name in ("fragments/lab_panel.html", "fragments/lab_sources.html"):
            html += svc.templates.get_template(name).render(lab=ctx, oob=True)
    return HTMLResponse(html)


@router.post("/lab/upload")
@render_outside_lock
def lab_upload(upload: SourceUpload, svc: Svc) -> Response | OutsideLock:
    """Bring a song in: decode, resample, analyse and store it — seconds of
    work, so it runs with the studio lock released (it touches the source
    store only, which locks itself)."""
    if upload.too_large:
        mb = limits.MAX_SOURCE_UPLOAD_BYTES // 1_000_000
        return svc.toast(f"That file is over {mb} MB: use FLAC or MP3, or trim it.")
    if upload.data is None:
        return svc.toast("Choose an audio file first.")
    data, name = upload.data, upload.name

    def run() -> Response:
        try:
            meta = sources.add(svc.data_dir, data, name, analyse=lab.analyse)
        except sources.SourceError as err:
            return svc.toast(str(err))
        return body(svc, meta["id"])

    return OutsideLock(run)


@router.post("/lab/select")
def lab_select(form: StrForm, svc: Svc) -> Response:
    meta = sources.update_meta(
        svc.data_dir, form.get("source", ""), lambda m: m.update(opened=time.time())
    )
    if meta is None:
        return svc.toast("That song is gone.")
    return body(svc, meta["id"])


@router.post("/lab/delete")
def lab_delete(form: StrForm, svc: Svc) -> Response:
    """Remove a source and its stems. Clips and sampler sounds made from
    it are copies and stay."""
    sid = form.get("source", "")
    job = svc.lab.state()
    if job and job["source"] == sid:
        return svc.toast("That song is being separated: cancel it first.")
    if not sources.delete(svc.data_dir, sid):
        return svc.toast("That song is gone.")
    return body(svc)


@router.post("/lab/grid")
def lab_grid(form: StrForm, svc: Svc) -> Response:
    """The tempo and the first bar line: `action` set (a typed `bpm`),
    half, double, nudge (`beats`, one of the offered steps) or reset."""
    action = form.get("action", "set")
    if action not in ("set", "half", "double", "nudge", "reset"):
        return Response(status_code=422)
    bpm = _number(form.get("bpm"))
    nudge = _number(form.get("beats"), 0.0)
    if action == "set" and (bpm is None or bpm <= 0):
        return svc.toast("Type a tempo in bpm.")
    if action == "nudge" and nudge not in lab.NUDGE_BEATS:
        return Response(status_code=422)
    meta = sources.update_meta(
        svc.data_dir,
        form.get("source", ""),
        lambda m: lab.set_grid(m, action, bpm, nudge or 0.0),
    )
    if meta is None:
        return svc.toast("That song is gone.")
    return body(svc, meta["id"])


@router.post("/lab/key")
@render_outside_lock
def lab_key(form: StrForm, svc: Svc) -> Response | OutsideLock:
    """Name the song's key by hand (`key`, one of `sources.KEY_NAMES`), or
    go back to the detected one (empty). It labels what leaves the Lab and
    is the root a sampler sound falls back on; no audio depends on it.
    `reread=1` reads the key again from the stems instead (a song separated
    before the bass line had a say) — seconds of analysis, so it runs with
    the studio lock released, on the source store only."""
    sid, key = form.get("source", ""), form.get("key", "")
    if form.get("reread") == "1":

        def run() -> Response:
            meta = lab.reread_key(svc.data_dir, sid)
            if meta is None:
                return svc.toast("That song is gone.")
            return body(svc, meta["id"])

        return OutsideLock(run)
    if key and key not in sources.KEY_NAMES:
        return Response(status_code=422)
    meta = sources.update_meta(
        svc.data_dir, sid, lambda m: m["user"].update(key=key or None)
    )
    if meta is None:
        return svc.toast("That song is gone.")
    return body(svc, meta["id"])


@router.post("/lab/view")
def lab_view(form: StrForm, svc: Svc) -> Response:
    """Zoom and scroll the waveform: `action` in | out | left | right |
    region (frame the region) | all | set (`start` and `seconds`: the span
    the page already shows — labview.js zooms on the wheel at once and
    posts where it ended, for the lanes drawn sharp over it). The view is
    the source's own, like its region; nothing but the drawing reads it."""
    action = form.get("action", "")
    if action not in lab.VIEW_ACTIONS:
        return Response(status_code=422)
    at, seconds = _number(form.get("start")), _number(form.get("seconds"))
    if action == "set" and (at is None or seconds is None or seconds <= 0):
        return Response(status_code=422)
    meta = sources.update_meta(
        svc.data_dir,
        form.get("source", ""),
        lambda m: lab.set_view(m, action, at, seconds),
    )
    if meta is None:
        return svc.toast("That song is gone.")
    return body(svc, meta["id"])


@router.post("/lab/region")
def lab_region(form: StrForm, svc: Svc) -> Response:
    """The region and the stems it plays. `drag_a` / `drag_b` (a drag
    across the lanes, in seconds of the song) win over typed numbers; with
    `lane` (the mix, or a stem) the drag also picks what the region plays —
    that lane alone, or added to the ticked ones with `add=1` (Shift held:
    `lab.pick_lane`). `edge` (start | end) with `at` is one of the region's
    edges dragged; `step` (move | grow) with `by` is the keyboard on the
    lanes (`lab.step_region`: beats, or steps of the snap). Typed, a region
    is `mode` bars with `bar` (from 1) and `bars`, or `mode` seconds with
    `start` and `length`; a `mode` posted without its own numbers (the
    snap's bar | beat) converts the region it has. `stem.<name>` boxes pick
    the stems (none = the mix), `level.<name>` is a stem's level in dB
    (clamped to `limits.LAB_LEVEL_DB_RANGE`; not a number = 0) and `solo`
    names the one stem to play alone (again on it: every stem). Alt held
    (`free=1`) never snaps."""
    free = form.get("free") == "1"

    def change(m: dict) -> None:
        if form.get("stems") == "1" and m["stems"]["model"]:
            names = sources.stem_names(m["stems"]["model"])
            m["picked"] = [s for s in names if form.get(f"stem.{s}")]
            for s in names:
                if f"level.{s}" in form:
                    sources.set_level(m, s, form.get(f"level.{s}"))
            lab.solo(m, form.get("solo", ""))
        a, b = _number(form.get("drag_a")), _number(form.get("drag_b"))
        if a is not None and b is not None:
            lab.drag_region(m, a, b, free)
            if form.get("lane"):
                lab.pick_lane(m, form.get("lane", ""), form.get("add") == "1")
            return
        at, edge = _number(form.get("at")), form.get("edge")
        if at is not None and edge in ("start", "end"):
            lab.move_region_edge(m, edge, at, free)
            return
        by = _number(form.get("by"))
        if form.get("step") in ("move", "grow") and by is not None:
            n = int(max(-64, min(64, round(by))))
            if n:
                lab.step_region(m, form.get("step", ""), n)
            return
        mode = form.get("mode")
        names = ("bar", "bars") if mode == "bars" else ("start", "length")
        start, length = (_number(form.get(n)) for n in names)
        if mode not in ("bars", "seconds"):
            return
        if start is None or length is None:
            lab.switch_mode(m, mode)
        else:
            lab.set_region(m, mode, start - (mode == "bars"), length)

    meta = sources.update_meta(svc.data_dir, form.get("source", ""), change)
    if meta is None:
        return svc.toast("That song is gone.")
    return body(svc, meta["id"])


@router.get("/lab/{sid}/region.wav")
@lock_free
def lab_region_wav(sid: str, request: Request) -> Response:
    """Audition: `start` / `length` seconds of the source — `stems` (a
    comma list) summed, each at its `levels` entry (dB, in step with
    `stems`; missing or unreadable = 0, clamped to
    `limits.LAB_LEVEL_DB_RANGE`), else the mix — as a WAV. The span is
    clamped into the source and cut at `MAX_CLIP_SECONDS`; at most 8 stems
    are read. Lock-free: it decodes a few seconds of a FLAC from the
    source store and touches nothing else."""
    svc: StudioService = request.app.state.svc
    meta = sources.read_meta(svc.data_dir, sid) if sources.valid_id(sid) else None
    if meta is None:
        return Response(status_code=404)
    q = request.query_params
    start, length = _number(q.get("start"), 0.0), _number(q.get("length"), 0.0)
    start = min(max(0.0, start or 0.0), meta["seconds"])
    length = min(max(0.0, length or 0.0), limits.MAX_CLIP_SECONDS)
    asked = q.get("stems", "")[:200].split(",")[:8]
    dbs = q.get("levels", "")[:200].split(",")[:8]
    levels = {s: sources.level_db(db) for s, db in zip(asked, dbs, strict=False)}
    names = [s for s in asked if s in sources.ALL_STEMS]
    audio = sources.read_sum(
        svc.data_dir,
        meta,
        names,
        int(round(start * SR)),
        int(round(length * SR)),
        levels=levels,
    )
    if not len(audio):
        return Response(status_code=404)
    return Response(
        wav_bytes(audio),
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


# --- chops (labchops.py): markers on the waveform, cleaned, kept as clips -----

CHOP_OPS = (
    "split",
    "add",
    "auto",
    "move",
    "remove",
    "rename",
    "select",
    "lanes",
    "clean",
)


def _heard_spans(data_dir, meta: dict, rule: str) -> list[tuple[float, float]]:
    """Where Auto cuts by listening — `rule` transients (a chop from each
    hit to the next, `drumhits.find_hits`) or syllables (`voiced.syllables`)
    — on what the region plays, its first `MAX_LAB_AUTO_SECONDS`."""
    start, length = lab.span(meta)
    audio = lab.region_audio(data_dir, meta, cap=limits.MAX_LAB_AUTO_SECONDS)
    heard = len(audio) / SR
    if rule == "syllables":
        found = voiced.syllables(audio, limits.MAX_LAB_CHOPS)
        return [(start + a / SR, start + b / SR) for a, b in found]
    times = [start + h.time for h in drumhits.find_hits(audio)]
    return list(zip(times, [*times[1:], start + min(length, heard)], strict=False))


@router.post("/lab/chop")
@render_outside_lock
def lab_chop(form: StrForm, svc: Svc) -> Response | OutsideLock:
    """Every edit of a source's chops, by `op` (each one change to the
    source's meta, `labchops.py`):

    - split: a double-click at `at` — cuts the chop there in two, or starts
      one that runs to the next chop or the region's end;
    - add: the region as a chop (the selection's ✂ Chop), named for what
      it plays and where (`lab.pick_name`), with the lanes' mix — cut back
      at a chop it runs into; refused when it starts inside one;
    - move: the chop's `edge` (start | end) to `at`;
    - auto: fill the region by `rule` bars | beats | transients | syllables,
      replacing the chops wholly inside it;
    - remove, rename (`name`), select (the chop becomes the region and the
      lanes its mix), lanes (the lanes' mix becomes the chop's), clean (its
      cleaning: `gate_db`, `trim_db`, `fit`, `fade_in`, `fade_out`,
      `level`, `gain_db`).

    `at` is seconds of the song, as a drag is. An edge snaps to a beat line
    within 1% of the span the lanes show unless `free=1` — decided here,
    from the grid in force and the view.

    Runs with the studio lock released: the listening rules and the notes
    of a cleaning read seconds of audio, and all of it touches the source
    store only (which locks itself)."""
    op, sid, cid = form.get("op", ""), form.get("source", ""), form.get("chop", "")
    rule = form.get("rule", "")
    if op not in CHOP_OPS or (op == "auto" and rule not in labchops.RULES):
        return Response(status_code=422)
    data_dir = svc.data_dir
    tempos = (svc.state.arrangement.bpm, float(svc.state.song["bpm"]))
    at, free = _number(form.get("at")), form.get("free") == "1"
    edge = form.get("edge", "")
    done: dict = {}

    def change(m: dict) -> None:
        have = sources.models_on_disk(data_dir, sid)
        where = None if at is None else labchops.snap(m, at, free)
        if op == "split" and where is not None:
            done["chop"] = labchops.split(m, where)
        elif op == "add":
            start, length = lab.span(m)
            name = lab.pick_name(m)[: limits.MAX_CHOP_NAME_CHARS]
            done["chop"] = labchops.add(m, start, start + length, name=name)
            if done["chop"]:
                m["chop"] = done["chop"]["id"]  # the region is this chop
        elif op == "move" and where is not None:
            was_region = lab.selected_chop(m) == cid
            done["chop"] = labchops.move_edge(m, cid, edge, where)
            if done["chop"] and was_region:
                labchops.frame(m, done["chop"])  # the region is still the chop
        elif op == "remove":
            done["chop"] = labchops.remove(m, cid) or None
        elif op == "rename":
            done["chop"] = labchops.rename(m, cid, form.get("name", ""))
        elif op == "select":
            done["chop"] = labchops.select(m, cid, have)
        elif op == "lanes":
            done["chop"] = labchops.use_lanes(m, cid)
        elif op == "clean":
            settings = {k: form.get(k) for k in labclean.DEFAULTS}
            done["chop"] = labchops.set_clean(m, cid, settings)
        elif op == "auto":
            done["auto"] = labchops.auto(m, rule, done["spans"])

    def run() -> Response:
        meta = sources.read_meta(data_dir, sid)
        if meta is None:
            return svc.toast("That song is gone.")
        if op == "auto":
            if rule in ("bars", "beats"):
                done["spans"] = labchops.grid_spans(meta, rule)
                if not done["spans"]:
                    return svc.toast(f"Set a tempo first: {rule} are cut on its grid.")
            elif rule == "syllables" and meta["picked"] != ["vocals"]:
                return svc.toast(
                    "Tick the vocals stem alone first: syllables are read from it."
                )
            else:
                done["spans"] = _heard_spans(data_dir, meta, rule)
        meta = sources.update_meta(data_dir, sid, change)
        if meta is None:
            return svc.toast("That song is gone.")
        if op == "auto":
            made, left = done["auto"]
            if not made:
                return svc.toast(f"No {rule} found in that region.")
            said = f"{made} chop{'s' if made > 1 else ''} cut by {rule}."
            if left:
                said += (
                    f" {left} more did not fit: a song holds "
                    f"{limits.MAX_LAB_CHOPS} chops, and the first ones were kept."
                )
            return body(svc, sid, said=said)
        if done.get("chop") is None:
            full = len(meta["chops"]) >= limits.MAX_LAB_CHOPS
            return svc.toast(
                (f"A song holds {limits.MAX_LAB_CHOPS} chops: remove one first.")
                if op in ("split", "add") and full
                else "No room for a chop there."
                if op == "split"
                else "The selection starts inside a chop: it is in the list already."
                if op == "add"
                else "That chop is gone."
            )
        if op == "add":
            c = done["chop"]
            said = f"Chop “{c['name']}” cut, {c['end'] - c['start']:.2f} s: in Chops."
            return body(svc, sid, said=said)
        if op == "clean":
            _note_cleaning(data_dir, sid, cid, tempos)
        return body(svc, sid)

    return OutsideLock(run)


def _note_cleaning(data_dir, sid: str, cid: str, tempos) -> None:
    """Run the chop's cleaning once and keep what it did, in words, on the
    chop (`notes`: the row shows it). A chop too long to keep is not run."""
    meta = sources.read_meta(data_dir, sid)
    chop = labchops.find(meta, cid) if meta else None
    if chop is None or labchops.seconds(chop) > limits.MAX_CLIP_SECONDS:
        return
    out, notes, _plan, problem = labchops.render(data_dir, meta, chop, *tempos)
    said = problem if out is None else " · ".join(notes)

    def keep(m: dict) -> None:
        c = labchops.find(m, cid)
        if c is not None and c["clean"] == chop["clean"]:
            c["notes"] = said[:240]

    sources.update_meta(data_dir, sid, keep)


@router.get("/lab/{sid}/chop/{cid}.wav")
@lock_free
def lab_chop_wav(sid: str, cid: str, request: Request) -> Response:
    """Audition a chop: its own mix (its stems of its stem set at its
    levels — never the lanes'), cleaned with its own settings, as a WAV cut
    at `MAX_CLIP_SECONDS`. 404 for a chop that is gone or whose stem set is
    not on disk. Lock-free: it reads the source store and two tempos."""
    svc: StudioService = request.app.state.svc
    meta = sources.read_meta(svc.data_dir, sid) if sources.valid_id(sid) else None
    chop = labchops.find(meta, cid) if meta else None
    if meta is None or chop is None:
        return Response(status_code=404)
    s = svc.state
    out, _notes, _plan, _problem = labchops.render(
        svc.data_dir, meta, chop, s.arrangement.bpm, float(s.song["bpm"])
    )
    if out is None or not len(out):
        return Response(status_code=404)
    return Response(
        wav_bytes(out[: int(limits.MAX_CLIP_SECONDS * SR)]),
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/lab/chop/keep")
def lab_chop_keep(form: StrForm, svc: Svc) -> Response:
    """Bake a chop into the clip store: its own mix, cleaned, under a free
    name (`free_clip_name`: never over a clip), with where it came from in
    the clip's meta (`labchops.clip_info`); the chop remembers the clip.

    `update=1` is the one deliberate overwrite in the studio: it writes
    over the chop's *own* clip — the one its `clip` names, while that clip
    still says it is this chop's (`labchops.own_clip`); anything else is
    kept as a new clip — and then forgets every render
    (`StudioService.reset_caches`), since the clip sounds different
    wherever it is placed. Nothing in the session changes either way: a
    kept chop is in no render key until it is placed somewhere.

    Refused, with nothing written: a chop over `MAX_CLIP_SECONDS` (before
    or after fitting), one that is silent once cleaned, one whose stem set
    is not on disk."""
    meta = _meta(svc, form)
    if meta is None:
        return svc.toast("That song is gone.")
    cid = form.get("chop", "")
    chop = labchops.find(meta, cid)
    if chop is None:
        return svc.toast("That chop is gone.")
    s = svc.state
    cap, length = limits.MAX_CLIP_SECONDS, labchops.seconds(chop)
    if length > cap + 1e-6:
        return svc.toast(
            f"That chop is {length:.1f} s; a clip holds {cap:g} s. Shorten it, "
            "or download the stem instead."
        )
    out, notes, plan, problem = labchops.render(
        svc.data_dir, meta, chop, s.arrangement.bpm, float(s.song["bpm"])
    )
    if out is None:
        return svc.toast(problem)
    if len(out) / SR > cap + 1e-6:
        return svc.toast(
            f"Fitted to {plan['bpm']:g} bpm that chop is {len(out) / SR:.1f} s, "
            f"longer than a clip's {cap:g} s: shorten it."
        )
    if not len(out) or not np.any(np.abs(out) > labclean.SILENT):
        return svc.toast("That chop is silent after cleaning: nothing was kept.")
    mine = ""
    if form.get("update") == "1":
        mine = labchops.own_clip(s.data_dir, meta["id"], chop)
    kept = mine or free_clip_name(s.data_dir, clip_safe(chop["name"]))
    save_clip(s.data_dir, kept, out, meta=labchops.clip_info(meta, chop, out, plan))

    def remember(m: dict) -> None:
        c = labchops.find(m, cid)
        if c is not None:
            c["clip"], c["notes"] = kept, " · ".join(notes)[:240]

    sources.update_meta(svc.data_dir, meta["id"], remember)
    if mine:
        svc.reset_caches()  # every render that placed it is stale
        said = f"Updated clip “{kept}”: it sounds new everywhere it is used."
        go = _went("clips")
    else:
        said = f"Kept as clip “{kept}” (chop “{chop['name']}”), in Clips."
        go = _went("clips", what="clip", clip=kept)
    resp = _say(body(svc, meta["id"], said=said, go=go), said)
    resp.headers["X-Clip-Name"] = kept
    return resp


# --- stem download -----------------------------------------------------------


def _attachment(meta: dict, what: str, ext: str) -> dict:
    """The headers of a download: `<song> - <what>.<ext>` as an attachment,
    and an encoding named so the gzip middleware passes it through (audio
    does not compress, and the length stays known)."""
    name = safe_name(f"{meta['name']} - {what}", "stems")
    return {
        "Content-Disposition": f'attachment; filename="{name}.{ext}"',
        "Content-Encoding": "identity",
        "Cache-Control": "no-store",
    }


def _stem(request: Request, sid: str, name: str):
    """(the source's meta, the stored FLAC of `name` in `?model=`'s set)
    or (None, None): a bad id, stem or model."""
    svc: StudioService = request.app.state.svc
    model = request.query_params.get("model", "")[:80]
    path = sources.stem_path(svc.data_dir, sid, name, model)
    if path is None or not path.is_file():
        return None, None
    return sources.read_meta(svc.data_dir, sid), path


@router.get("/lab/{sid}/stem/{name}.flac")
@lock_free
def lab_stem_flac(sid: str, name: str, request: Request) -> Response:
    """A whole stem (`name`: mix, or a stem of the set `?model=` names —
    default the one the source shows) as the stored FLAC, from disk."""
    meta, path = _stem(request, sid, name)
    if meta is None or path is None:
        return Response(status_code=404)
    return FileResponse(
        path, media_type="audio/flac", headers=_attachment(meta, name, "flac")
    )


@router.get("/lab/{sid}/stem/{name}.wav")
@lock_free
def lab_stem_wav(sid: str, name: str, request: Request) -> Response:
    """The same stem as 16-bit PCM WAV, decoded and written in blocks
    (`sources.wav_blocks`): never the whole stem in memory."""
    meta, path = _stem(request, sid, name)
    stream = sources.wav_blocks(path) if path is not None else None
    if meta is None or stream is None:
        return Response(status_code=404)
    size, blocks = stream
    headers = {**_attachment(meta, name, "wav"), "Content-Length": str(size)}
    return StreamingResponse(blocks, media_type="audio/wav", headers=headers)


@router.get("/lab/{sid}/stems.zip")
@lock_free
def lab_stems_zip(sid: str, request: Request) -> Response:
    """Every stem of one set (`?model=`, default the one the source shows)
    as FLAC in a zip — stored, not deflated (FLAC does not compress
    further), deterministic (`files.zip_write`), built in a spooled temp
    file and sent from it in blocks."""
    svc: StudioService = request.app.state.svc
    meta = sources.read_meta(svc.data_dir, sid) if sources.valid_id(sid) else None
    model = request.query_params.get("model", "")[:80]
    model = model or (meta["stems"]["model"] if meta else "")
    names = sources.stem_names(model) if model else ()
    paths = [sources.stem_path(svc.data_dir, sid, n, model) for n in names]
    if meta is None or not paths or any(p is None for p in paths):
        return Response(status_code=404)
    song = safe_name(meta["name"], "song")
    tmp = tempfile.SpooledTemporaryFile(max_size=8_000_000)  # noqa: SIM115
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
            for name, path in zip(names, paths, strict=True):
                zip_write(z, f"{song} - {name}.flac", path.read_bytes())
    except OSError:
        tmp.close()
        return Response(status_code=404)
    size = tmp.tell()
    tmp.seek(0)

    def blocks():
        try:
            while chunk := tmp.read(1 << 16):
                yield chunk
        finally:
            tmp.close()

    headers = {**_attachment(meta, "stems", "zip"), "Content-Length": str(size)}
    return StreamingResponse(blocks(), media_type="application/zip", headers=headers)


@router.post("/lab/send")
def lab_send(request: Request, form: StrForm, svc: Svc) -> Response:
    """Send the region somewhere: `target` clip (into the clip store,
    `fit` = stretched to the session tempo) or sampler (a new sampler
    sound, `chop` none | transients | beats | bars, landing on Synths)."""
    meta = _meta(svc, form)
    if meta is None:
        return svc.toast("That song is gone.")
    target = form.get("target", "")
    data_dir = svc.state.data_dir
    # named for what it plays and where: "vocals bars 9-12" (`lab.pick_name`)
    label = clip_safe(form.get("name", "").strip() or lab.pick_name(meta))
    if target == "clip":
        bpm = svc.state.arrangement.bpm
        audio, info, problem = lab.clip_audio(
            svc.data_dir, meta, bpm, form.get("fit") == "on"
        )
        if audio is None:
            return svc.toast(problem)
        kept = free_clip_name(data_dir, label)
        save_clip(data_dir, kept, audio, meta=info)
        how = f", stretched to {bpm:g} bpm" if info["lab"]["fitted"] else ""
        said = f"Kept as clip “{kept}” (the region's {lab.what(meta)}){how}, in Clips."
        go = _went("clips", what="clip", clip=kept)
        resp = _say(body(svc, meta["id"], said=said, go=go), said)
        resp.headers["X-Clip-Name"] = kept
        return resp
    if target == "sampler":
        mode = form.get("chop", "none")
        if mode not in lab.CHOPS:
            return Response(status_code=422)
        audio, problem = lab.sampler_audio(svc.data_dir, meta)
        if audio is None:
            return svc.toast(problem)
        chop, _note = lab.chop_slices(meta, mode)
        # a region with no single pitch of its own (a chord loop) takes the
        # song's tonic as its root, so that key plays it as recorded
        hint = lab.tonic_midi(lab.key_in_force(meta))
        return adopt_audio(request, svc, audio, SR, label[:60], chop=chop, root=hint)
    return Response(status_code=422)


@router.post("/lab/vocal")
def lab_vocal(request: Request, form: StrForm, svc: Svc) -> Response:
    """The region's vocal as a sampler sound, landing on Synths: `what`
    instrument (the longest held note found, looped, rooted on its pitch,
    formant mode — `labmake.vocal_instrument`) or chops (a slice a
    syllable). Read from the vocals stem when the song is separated, else
    from what the region plays. What was found is the toast it lands with;
    nothing pitched (or one syllable) falls back to the plain region on the
    keys, and says so."""
    meta = _meta(svc, form)
    if meta is None:
        return svc.toast("That song is gone.")
    what = form.get("what", "")
    if what not in ("instrument", "chops"):
        return Response(status_code=422)
    audio, where, problem = labmake.stem_region(svc.data_dir, meta, "vocals")
    if audio is None:
        return svc.toast(problem)
    hint = lab.tonic_midi(lab.key_in_force(meta))
    base = clip_safe(form.get("name", "").strip() or meta["name"][:40])
    note = "" if where == "vocals" else f" Read from the {where}, not a vocal stem."
    if what == "chops":
        chop, said = labmake.vocal_chops(audio)
        return adopt_audio(
            request, svc, audio, SR, f"{base} chops"[:60], chop=chop, say=said + note
        )
    fields, said, found = labmake.vocal_instrument(audio)
    name = f"{base} voice" if found else base
    return adopt_audio(
        request, svc, audio, SR, name[:60], fields=fields, root=hint, say=said + note
    )


@router.post("/lab/drums")
@pattern_edit
def lab_drums(request: Request, form: StrForm, svc: Svc) -> Response:
    """The region's drums read back into the studio (`analysis.drumhits`,
    from the drums stem when there is one). `to`:

    - grid: the pattern onto the drum grid (bars, steps, swing, velocity
      lane), through `StudioState.apply_pattern` — the bank's and the kits'
      own path — as one pattern undo step (`@pattern_edit`);
    - pool: the pattern as a new beat in the song's pattern pool, through
      `song_model.add_pattern` and the song's `_commit`;
    - kit: one clean hit a lane cut into the clip store and put on that
      drum row's sample slot (`drum_samples`, the row's own picker).

    A pattern needs the region on the bar lines and takes its first
    `labmake.MAX_PATTERN_BARS` bars."""
    meta = _meta(svc, form)
    if meta is None:
        return svc.toast("That song is gone.")
    to = form.get("to", "")
    if to not in ("grid", "pool", "kit"):
        return Response(status_code=422)
    s = svc.state
    audio, where, problem = labmake.stem_region(svc.data_dir, meta, "drums")
    if audio is None:
        return svc.toast(problem)
    note = "" if where == "drums" else f" (read from the {where}, not a drum stem)"
    title = meta["name"][:24]
    if to == "kit":
        shots = labmake.kit_hits(audio, drumhits.classify(drumhits.find_hits(audio)))
        if not shots:
            return svc.toast(
                "No drum hit stands alone in that region: try a sparser one."
            )
        samples = dict(s.arrangement.drum_samples)
        for lane, shot in shots.items():
            kept = free_clip_name(s.data_dir, clip_safe(f"{title} {lane}"))
            save_clip(s.data_dir, kept, shot, meta={"source": f"lab: {meta['name']}"})
            samples[lane] = kept
        was = {lane: s.arrangement.drum_samples.get(lane, "") for lane in shots}
        s.arrangement = replace(s.arrangement, drum_samples=samples)
        s.save()
        missing = [lane for lane in labmake.KIT_LANES if lane not in shots]
        said = (
            f"Kit from the {where}{note}: {', '.join(shots)} are clips on their "
            "drum rows now (a row's own picker on Kit sets it back to synth)."
        )
        if missing:
            said += f" No {' or '.join(missing)} stood alone in the region."
        go = _went(
            "drums",
            what="kit",
            was=json.dumps(was, sort_keys=True),
            now=json.dumps({lane: samples[lane] for lane in shots}, sort_keys=True),
        )
        return body(svc, meta["id"], said=said, go=go)
    bars, problem = labmake.pattern_bars(meta)
    if problem:
        return svc.toast(problem)
    pat, _hits = labmake.drum_pattern(meta, audio, bars)
    if not pat.steps:
        return svc.toast("No drum hits found in that region.")
    first = int(round(meta["region"]["start"])) + 1
    name = f"{title} b{first}"
    summary = f"{bars} bar{'s' if bars > 1 else ''}: {labmake.pattern_summary(pat)}"
    rows = [(lane, pat.row(lane)) for lane in drumhits.LANES if lane in pat.counts]
    if to == "grid":
        was = s.arrangement.bpm
        tempo = labmake.loop_tempo(meta) if form.get("loop_tempo") == "on" else None
        s.arrangement = replace(s.arrangement, drum_vel=dict(pat.vel), bpm=tempo or was)
        s.apply_pattern(
            {
                "name": name,
                "steps": pat.steps,
                "bars": bars,
                "steps_per_bar": pat.steps_per_bar,
                "swing": pat.swing,
            }
        )
        s.active_slot = None  # its beat is not any slot's
        s.save()
        swing = f", swing {pat.swing:g}" if pat.swing else ""
        if tempo and tempo != was:
            speed = (
                f"The loop's tempo is now {tempo:g} bpm, the song's (it was "
                f"{was:g}: set it back on Kit or the Loops page)"
            )
        elif tempo:
            speed = f"It plays at {tempo:g} bpm, the song's tempo and the loop's"
        else:
            speed = (
                f"It plays at the loop's {was:g} bpm, not the song's "
                f"{lab.grid(meta)['bpm']:.4g}"
            )
        said = (
            f"On the drum grid{note}, {summary}{swing}. {speed}; Ctrl+Z on "
            "Kit takes the pattern back."
        )
        go = _went("drums", what="pattern", bpm=f"{was:g}")
        return body(svc, meta["id"], said=said, rows=rows, go=go)

    def change(song: dict) -> None:
        data = {"bars": bars, "steps": pat.steps_per_bar, "drums": pat.steps}
        sm.add_pattern(song, "drums", name, {**data, "drum_vel": dict(pat.vel)})

    resp = _commit(svc, change, label=POOL_LABEL)
    if resp.status_code >= 400:
        return resp
    said = f"“{name}” is a beat in the song's pattern pool{note}, {summary}."
    if pat.swing:
        said += f" Its swing ({pat.swing:g}) is not part of a pool pattern."
    go = _went("song", what="song", label=POOL_LABEL)
    return body(svc, meta["id"], said=said, rows=rows, go=go)


@router.post("/lab/place")
def lab_place(request: Request, form: StrForm, svc: Svc) -> Response:
    """The region onto the song: kept as a clip stretched to the *song's*
    tempo and held to whole bars (`labmake.song_take`), then placed in
    `section_id` at `at_bar` (from 1) on the song's first audio track by
    the song's own take placement (`routes.song_record.place_clip`). A clip
    the song refuses is not left in the store."""
    meta = _meta(svc, form)
    if meta is None:
        return svc.toast("That song is gone.")
    s = svc.state
    section_id = form.get("section_id", "")
    if sm.section_index(s.song, section_id) is None:
        return svc.toast("Pick a section of the song (add one on Song first).")
    at_bar = _number(form.get("at_bar"), 1.0)
    at_bar = int(min(max(1.0, at_bar or 1.0), limits.MAX_SECTION_BARS)) - 1
    bpm = float(s.song["bpm"])
    audio, info, bars, fitted, problem = labmake.song_take(svc.data_dir, meta, bpm)
    if audio is None:
        return svc.toast(problem)
    label = clip_safe(form.get("name", "").strip() or lab.pick_name(meta))
    kept = free_clip_name(s.data_dir, label)
    save_clip(s.data_dir, kept, audio, meta=info)
    resp = place_clip(svc, section_id, "", kept, at_bar)
    if resp.status_code >= 400:
        delete_clip(s.data_dir, kept)  # nothing refers to it
        return resp
    where = json.loads(resp.headers["HX-Trigger"])["studio-toast"]["message"]
    how = (
        f" It is {bars} bar{'s' if bars > 1 else ''} at the song's {bpm:g} bpm"
        + (
            f", stretched from {info['lab']['source_bpm']:g}."
            if info["lab"]["fitted"]
            else "."
        )
        if fitted
        else " It plays at the recording's own speed (no tempo to fit it by)."
    )
    go = _went("song", what="song", label=PLACE_LABEL, clip=kept)
    return body(svc, meta["id"], said=where + how, go=go)


@router.post("/lab/separate")
def lab_separate(form: StrForm, svc: Svc) -> Response:
    """Start separating the source into stems (and the model's download,
    the first time), with `model` (an id of `stems.MODELS`; default the
    4-stem one). The job runs on its own thread; this answers at once with
    the status line polling it. A song already separated with that model
    just shows those stems again."""
    sid = form.get("source", "")
    if not sources.valid_id(sid):
        return svc.toast("That song is gone.")
    problem = svc.lab.start(sid, form.get("model", ""))
    if problem == "busy":
        return svc.toast("Another song is being separated: one at a time.")
    if problem:
        return svc.toast(problem)
    return body(svc, sid)


@router.post("/lab/cancel")
def lab_cancel(form: StrForm, svc: Svc) -> Response:
    svc.lab.cancel()
    return body(svc, form.get("source", ""))


@router.post("/lab/undo")
def lab_undo(form: StrForm, svc: Svc) -> Response:
    """Take back what a make just did, from the line that says so (`go`'s
    `undo` fields), and only that: `what`

    - clip: delete the clip a region or a chop was kept as (`clip`), while
      it is still the Lab's own from this song and nothing uses it;
    - pattern: the drum grid as it was (the pattern undo step `/lab/drums`
      pushed: Ctrl+Z on Drums) and the loop's tempo back to `bpm`;
    - song: the song's newest undo step, while it is still the Lab's
      (`label`: the beat in the pool, the take placed) — and the take's
      clip (`clip`) too when nothing else uses it;
    - kit: the drum rows the kit set (`now`, JSON) back to what they held
      (`was`), each only while it still holds the kit's clip.

    Anything that moved since is left alone, with a toast saying where to
    undo it instead."""
    meta = _meta(svc, form)
    if meta is None:
        return svc.toast("That song is gone.")
    s, sid, what = svc.state, meta["id"], form.get("what", "")
    clip = clip_safe(form.get("clip", "")) if form.get("clip") else ""

    def drop_clip() -> bool:
        if not clip or not _lab_clip(s.data_dir, sid, clip):
            return False
        if clip in s.clip_usage():
            return False
        delete_clip(s.data_dir, clip)
        return True

    if what == "clip":
        if not drop_clip():
            return svc.toast(f"“{clip}” is in use or gone: delete it from Clips.")
        return body(svc, sid, said=f"Undone: clip “{clip}” deleted.")
    if what == "pattern":
        why = s.pop_pattern_undo()
        if why:
            return svc.toast(why)
        bpm = _number(form.get("bpm"))
        lo, hi = limits.BPM_RANGE
        if bpm is not None and lo <= bpm <= hi:
            s.arrangement = replace(s.arrangement, bpm=bpm)
        s.save()
        return body(svc, sid, said="Undone: the drum grid is as it was.")
    if what == "song":
        label = form.get("label", "")
        if label not in (POOL_LABEL, PLACE_LABEL):
            return Response(status_code=422)
        if s.song_undo_state()["undo"] != label:
            return svc.toast("The song changed since: undo it on the Song page.")
        old = s.song
        s.song_undo()
        moved = _moved(svc, old, f"Undid: {label}")
        what_it_was = "beat in the song's pool" if label == POOL_LABEL else "take"
        said = f"Undone: the {what_it_was} is off the song."
        # the take's clip stays while the redo step holds it (Song's redo
        # must not bring back a reference to a deleted file)
        if clip and drop_clip():
            said += f" Its clip “{clip}” is deleted."
        elif clip:
            said += f" Its clip “{clip}” stays in Clips (Song's redo puts it back)."
        resp = body(svc, sid, said=said)
        resp.headers["HX-Trigger"] = moved.headers["HX-Trigger"]
        return resp
    if what == "kit":
        try:
            was, now = json.loads(form.get("was", "")), json.loads(form.get("now", ""))
        except ValueError:
            return Response(status_code=422)
        if not (isinstance(was, dict) and isinstance(now, dict)):
            return Response(status_code=422)
        samples = dict(s.arrangement.drum_samples)
        back = []
        for lane in labmake.KIT_LANES:
            if lane in now and samples.get(lane) == now[lane]:
                old = was.get(lane)
                if isinstance(old, str) and old and clip_safe(old) == old:
                    samples[lane] = old
                else:
                    samples.pop(lane, None)
                back.append(lane)
        if not back:
            return svc.toast("The drum rows changed since: set them on Kit.")
        s.arrangement = replace(s.arrangement, drum_samples=samples)
        s.save()
        rows = ", ".join(back)
        return body(svc, sid, said=f"Undone: {rows} are back as they were.")
    return Response(status_code=422)
