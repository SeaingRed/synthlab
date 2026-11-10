"""The clip hub (`/view/clips`): every clip and sample in one place — list,
listen, tune, send, and make into a beat, a note part or a sound
(`cliphub.py` the stores and the tune, `clipmake.py` the makers and the
plan, `clipmorph.py` Gemini's plan, `service/hubpage.py` the context).

Sending reuses each destination's own route, posted from the bench
(`/fragment/sampler/clip`, `/fragment/drums/sample`,
`/song/section/{id}/clip`, `/voice/take/clip`, `/voice/carrier/clip`,
"Open in Lab"); the routes here are the hub's own:

- GETs: the list, the bench, the player, the send panel (each answers
  `HX-Replace-Url`: the hub's choices are its URL), and `/hub/audio.wav`
  — the item tuned, lock-free (the clip and sample stores lock themselves);
- tune: keep the tuned audio as a new clip, or write it over the clip
  (behind a confirm naming where it is used; the hub's Undo puts it back);
- a sampler sample kept as a clip; a clip opened in the Lab as a song;
- make: a preview (`@render_outside_lock`: the listening and Gemini run
  with the lock released), Apply (made again from the same plan and seed,
  written through the paths a hand edit takes, ONE undo step: the page's
  own Ctrl+Z for what it holds, and the hub's ↶ Undo for all of it) and
  Undo.

Every input is untrusted: an item reference is parsed and checked
(`cliphub.parse_ref`), the tune clamped (`clean_tune`), a plan — the
pickers' or Gemini's — held to the studio's names and ranges
(`clipmake.clean_plan`), the description cut (`clipmorph.clean_describe`).
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ... import sampler
from ...core import SR
from .. import cliphub, clipmake, clipmorph, loops, sounds, sources
from .. import lab as lab_mod
from ..agent.client import AgentError
from ..cliphub import Made, Overwrite, Ref
from ..clips import clip_dir, clip_safe, free_clip_name, list_clips, save_clip
from ..render import RenderError, loop_refusal
from ..routing import (
    OutsideLock,
    StrForm,
    Svc,
    lock_free,
    render_outside_lock,
    studio_router,
)
from ..service import StudioService
from ..state import StudioState
from .pattern import with_toast
from .sampler import adopt_audio

router = studio_router()

MAX_PLAN_CHARS = 8000  # a posted plan's JSON (it is the server's own echo)


def _posted_plan(raw) -> dict | None:
    """A plan the page posts back (the preview's own echo, but a post is
    anyone's): JSON of a bounded size, an object, else None. What is in it
    is `clipmake.clean_plan`'s to check."""
    if not isinstance(raw, str) or not raw or len(raw) > MAX_PLAN_CHARS:
        return None
    try:
        got = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return got if isinstance(got, dict) else None


def _render(svc: StudioService, template: str, **ctx) -> str:
    return svc.templates.get_template(template).render(**ctx)


def _with_url(resp: Response, svc: StudioService, params: dict) -> Response:
    resp.headers["HX-Replace-Url"] = svc.hub_url(params)
    return resp


def _ref(svc: StudioService, raw, kinds=("clip", "sample")) -> Ref | None:
    ref = cliphub.parse_ref(raw)
    if ref is None or ref.kind not in kinds:
        return None
    if ref.kind != "stem" and cliphub.version(svc.state.data_dir, ref) is None:
        return None
    return ref


def _meta(data_dir: Path, ref: Ref) -> dict:
    if ref.kind != "clip":
        return {}
    return next((c for c in list_clips(data_dir) if c["name"] == ref.name), {})


def _title(ref: Ref) -> str:
    return ref.name if ref.kind == "clip" else f"sample {ref.name[:8]}"


# --- the page's parts -------------------------------------------------------------


@router.get("/fragment/hub/list")
def hub_list(request: Request, svc: Svc) -> Response:
    """The list for the filter (`find`, `show`, `order`): the list only,
    so the search box being typed in is never redrawn."""
    ctx = svc.hub_context(request, bench=False)
    html = _render(svc, "fragments/hub_list.html", hub=ctx, oob=False)
    return _with_url(HTMLResponse(html), svc, ctx["params"])


@router.get("/fragment/hub/bench")
def hub_bench(request: Request, svc: Svc) -> Response:
    """The selected item's bench (its facts, player, tune, send, make)."""
    ctx = svc.hub_context(request)
    if ctx["item"] is None:
        return svc.toast("That clip is gone.")
    html = _render(svc, "fragments/hub_bench.html", hub=ctx)
    return _with_url(HTMLResponse(html), svc, ctx["params"])


@router.get("/fragment/hub/player")
def hub_player(request: Request, svc: Svc) -> Response:
    """The player for the tune the form holds (its audio URL carries it)."""
    ctx = svc.hub_context(request)
    if ctx["bench"] is None:
        return svc.toast("That clip is gone.")
    return HTMLResponse(_render(svc, "fragments/hub_player.html", hub=ctx))


@router.get("/fragment/hub/send")
def hub_send(request: Request, svc: Svc) -> Response:
    """The send panel for the section the Place bar chose: the section is
    the Place route's path, which no form field can change."""
    ctx = svc.hub_context(request)
    if ctx["bench"] is None:
        return svc.toast("That clip is gone.")
    return HTMLResponse(_render(svc, "fragments/hub_send.html", hub=ctx))


@router.get("/hub/audio.wav")
@lock_free
def hub_audio(request: Request) -> Response:
    """A clip or a sample as the bench hears it: tuned as the query asks
    (`cliphub.clean_tune`), else as stored. Lock-free: it reads the clip
    and sample stores and its own memo, never the session."""
    svc: StudioService = request.app.state.svc
    q = request.query_params
    ref = cliphub.parse_ref(q.get("item", ""))
    if ref is None or ref.kind not in ("clip", "sample"):
        return Response(status_code=404)
    wav = cliphub.tuned_wav(svc.data_dir, ref, cliphub.clean_tune(q))
    if wav is None:
        return Response(status_code=404)
    return Response(wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})


def _hub_page(request: Request, svc: StudioService, item: str, said: str) -> Response:
    """The whole hub with `item` selected (a clip just made, an update
    that changed what the bench draws), its URL pushed."""
    params = svc.hub_params(request) | {"item": item}
    request.state.hub = params  # what `hub_params` draws this page for
    resp = svc.full_view_page(
        request, {"HX-Push-Url": svc.hub_url(params)}, view="clips"
    )
    return with_toast(resp, said)


# --- tune --------------------------------------------------------------------------


@router.post("/hub/tune/keep")
def hub_tune_keep(request: Request, form: StrForm, svc: Svc) -> Response:
    """The tuned audio as a NEW clip ("<name> tuned", never over one that
    is there: `free_clip_name`), its meta saying where it came from and how
    (`cliphub.tuned_meta`). The hub then shows it."""
    s = svc.state
    ref = _ref(svc, form.get("item"))
    if ref is None:
        return svc.toast("That clip is gone.")
    t = cliphub.clean_tune(form)
    if cliphub.is_identity(t):
        return svc.toast("Nothing is tuned yet: move a control first.")
    got = cliphub.read(s.data_dir, ref)
    if got is None:
        return svc.toast("That clip is gone.")
    audio, sr = got
    out = cliphub.tune(audio, sr, t)
    if not len(out):
        return svc.toast("That trim leaves nothing.")
    kept = free_clip_name(s.data_dir, f"{_title(ref)[:60]} tuned")
    save_clip(
        s.data_dir,
        kept,
        out,
        sr=sr,
        meta=cliphub.tuned_meta(_meta(s.data_dir, ref), t, ref.text),
    )
    return _hub_page(request, svc, f"clip:{kept}", f"Kept as “{kept}”.")


@router.post("/hub/tune/update")
def hub_tune_update(request: Request, form: StrForm, svc: Svc) -> Response:
    """The tuned audio written OVER the clip — the hub's one overwrite,
    behind a confirm naming where the clip is used (every render that
    places it changes: its content hash is in their keys). Its old file
    and meta are kept for the hub's Undo; the caches are reset, as the
    Lab's "Update clip" does."""
    s = svc.state
    ref = _ref(svc, form.get("item"), kinds=("clip",))
    if ref is None:
        return svc.toast("That clip is gone.")
    t = cliphub.clean_tune(form)
    if cliphub.is_identity(t):
        return svc.toast("Nothing is tuned yet: move a control first.")
    got = cliphub.read(s.data_dir, ref)
    path = cliphub.clip_path(s.data_dir, ref.name)
    if got is None or path is None:
        return svc.toast("That clip is gone.")
    audio, sr = got
    out = cliphub.tune(audio, sr, t)
    if not len(out):
        return svc.toast("That trim leaves nothing.")
    meta_path = path.with_name(f"{path.stem}.meta.json")
    old_meta = meta_path.read_bytes() if meta_path.is_file() else None
    old_wav = path.read_bytes()
    meta = _meta(s.data_dir, ref)
    meta = {k: v for k, v in meta.items() if k not in ("name", "seconds", "sr")}
    meta |= {k: v for k, v in cliphub.tuned_meta(meta, t, ref.text).items()}
    if t["start"] or t["end"] or t["rate"] != 1.0:
        meta.pop("bars", None)
    save_clip(s.data_dir, ref.name, out, sr=sr, meta=meta)
    st = path.stat()
    svc.hub.push(
        Overwrite(
            f"update “{ref.name}”",
            ref.name,
            old_wav,
            old_meta,
            (st.st_mtime_ns, st.st_size),
        )
    )
    svc.reset_caches()
    return _hub_page(
        request,
        svc,
        ref.text,
        f"“{ref.name}” is the tuned audio now (↶ Undo puts it back).",
    )


@router.post("/hub/keep")
def hub_keep(request: Request, form: StrForm, svc: Svc) -> Response:
    """A sampler sample kept as a clip: then every send and make a clip
    has is there for it."""
    s = svc.state
    ref = _ref(svc, form.get("item"), kinds=("sample",))
    if ref is None:
        return svc.toast("That sample is gone.")
    got = cliphub.read(s.data_dir, ref)
    if got is None:
        return svc.toast("That sample is gone.")
    kept = free_clip_name(s.data_dir, f"sample {ref.name[:8]}")
    save_clip(s.data_dir, kept, got[0], sr=got[1], meta={"source": "sampler sample"})
    return _hub_page(request, svc, f"clip:{kept}", f"Kept as the clip “{kept}”.")


@router.post("/hub/lab")
@render_outside_lock
def hub_lab(request: Request, form: StrForm, svc: Svc) -> Response | OutsideLock:
    """Open a clip in the Sample Lab: a clip cut from a Lab song opens that
    song (its chop the region); any other is brought in as a song of its
    own (`sources.add`, the Lab's import: decoded, analysed — seconds of
    work, so with the lock released) and opened."""
    ref = _ref(svc, form.get("item"), kinds=("clip",))
    if ref is None:
        return svc.toast("That clip is gone.")
    path = cliphub.clip_path(svc.data_dir, ref.name)
    if path is None:
        return svc.toast("That clip is gone.")
    if not sources.available():
        return svc.toast("The Lab needs the soundfile package (uv sync --all-extras).")
    data = path.read_bytes()

    def run() -> Response:
        try:
            meta = sources.add(
                svc.data_dir, data, f"{ref.name}.wav", analyse=lab_mod.analyse
            )
        except sources.SourceError as err:
            return svc.toast(str(err))
        loc = {
            "path": f"/view/lab?source={meta['id']}",
            "target": "#view",
            "swap": "outerHTML",
        }
        return Response(status_code=200, headers={"HX-Location": json.dumps(loc)})

    return OutsideLock(run)


# --- make --------------------------------------------------------------------------


def _snapshot(s: StudioState) -> dict:
    """Everything an Apply may change, as plain data: what the hub's Undo
    puts back, and (taken again after it) how Undo knows nothing moved
    since."""
    return {
        "patch": s.patch.to_json(),
        "layers": copy.deepcopy(s.layers),
        "curves": copy.deepcopy(s.curves),
        "fx_drums": copy.deepcopy(s.fx_drums),
        "arr": copy.deepcopy(s.arrangement),
        "beat_style": s.beat_style,
        "beat_seed": s.beat_seed,
        "active_slot": s.active_slot,
        "grid_span": s.grid_span,
        "main_layer": s.main_layer,
        "active_layer": s.active_layer,
    }


def _restore(s: StudioState, snap: dict) -> None:
    from ...synth import Patch

    s.patch = Patch.from_dict(snap["patch"])
    s.layers = copy.deepcopy(snap["layers"])
    s.curves = copy.deepcopy(snap["curves"])
    s.fx_drums = copy.deepcopy(snap["fx_drums"])
    s.arrangement = copy.deepcopy(snap["arr"])
    s.beat_style, s.beat_seed = snap["beat_style"], snap["beat_seed"]
    s.active_slot = snap["active_slot"]
    s.grid_span = snap["grid_span"]
    s.main_layer = snap["main_layer"]


def _sound_finder(svc: StudioService):
    def find(source: str, name: str):
        if source not in ("library", "recipes", "factory"):
            return None
        return sounds.find(svc, source, name)

    return find


class _Job:
    """What a make reads under the lock: a copy of what the makers look at."""

    def __init__(self, svc: StudioService, ref: Ref):
        s = svc.state
        self.ref = ref
        self.data_dir = s.data_dir
        self.sample_dir = s.sample_dir
        self.version = cliphub.version(s.data_dir, ref) or ""
        self.meta = _meta(s.data_dir, ref)
        self.arr = copy.deepcopy(s.arrangement)
        self.song = copy.deepcopy(s.song)
        self.grid_oct = s.grid_oct
        self.patch = copy.deepcopy(s.patch)
        self.title = clip_safe(_title(ref))[:40]


def _build(svc: StudioService, job: _Job, plan: dict, seed: int):
    """(the candidate, the audio at SR, the grid) — or (a candidate that
    says why not, ...). Runs with the lock released: it reads only the
    job's copies and the stores."""
    got = cliphub.read(job.data_dir, job.ref)
    if got is None:
        return (
            clipmake.Candidate(plan["target"], refused="That clip is gone."),
            None,
            None,
        )
    audio = cliphub.at_sr(*got)
    facts = cliphub.measure(job.data_dir, job.ref)
    g = clipmake.clip_grid(audio, job.meta, facts, job.arr.bpm)
    if plan["target"] == "beat":
        c = clipmake.make_beat(audio, job.version, g, plan, job.arr, seed, job.title)
    elif plan["target"] == "notes":
        c = clipmake.make_notes(
            audio, job.version, g, plan, job.arr, job.grid_oct, seed, job.song, facts
        )
        if not c.refused:
            src = plan["sound"]
            if src["source"] == "clip":
                c.patch, said = clipmake.sampler_patch(
                    audio, job.title, plan["sound_mode"]
                )
                c.sound_label = f"{job.title} (sampler)"
                c.said.append(f"Played by the clip itself: {said}")
            elif src["source"] != "keep":
                found = _sound_finder(svc)(src["source"], src["name"])
                if found is not None:
                    c.patch, c.chain, c.sound_label = (
                        found.patch,
                        found.chain,
                        found.label,
                    )
                    c.said.append(f"Played by “{src['name']}” ({src['source']}).")
            if plan["fx"] != "keep":
                from ..fxrack import chain_preset

                c.fx = [] if plan["fx"] == "none" else chain_preset(plan["fx"]) or []
    else:
        c = clipmake.make_sound(audio, plan)
        c.patch, _said = clipmake.sampler_patch(audio, job.title, plan["sound_mode"])
    return c, audio, g


def _preview_root(job: _Job, c: clipmake.Candidate) -> Path | None:
    """A beat whose kit is the clip's own hits is heard from a clip root of
    its own (a temp folder, never the clip store: nothing is kept before
    Apply) holding those hits and the other rows' clips."""
    if not c.hits:
        return None
    import hashlib

    from ...audio import save as save_wav

    h = hashlib.sha1()
    for lane in sorted(c.hits):
        h.update(lane.encode())
        h.update(c.hits[lane].tobytes())
    root = Path(tempfile.gettempdir()) / "synthlab-hub" / h.hexdigest()[:16]
    clips = root / "clips"
    if not clips.is_dir():
        clips.mkdir(parents=True, exist_ok=True)
        for lane, shot in c.hits.items():
            save_wav(
                str(clips / f"{clip_safe(c.hit_names[lane])}.wav"),
                shot,
                sr=SR,
                bit_depth=16,
            )
        for name in set(c.arr.get("drum_samples", {}).values()) - set(
            c.hit_names.values()
        ):
            src = clip_dir(job.data_dir) / f"{clip_safe(name)}.wav"
            if src.is_file():
                shutil.copyfile(src, clips / src.name)
    return root


def _preview(svc: StudioService, job: _Job, c: clipmake.Candidate, with_beat: bool):
    """The candidate's inputs (under the lock) for `render_preview`."""
    s = svc.state
    if c.target == "beat":
        fields = {"arr": c.arr}
        if c.fx is not None:
            fields["drums_fx"] = c.fx
        root = _preview_root(job, c)
        if root is not None:
            fields["clip_root"] = root
        return svc.preview_inputs("drums", **fields)
    if c.target == "notes":
        extra = {"arr": c.arr, "parts": ("synth", "drums") if with_beat else ("synth",)}
        if c.fx is not None:
            extra["main_fx"] = c.fx
        patch = c.patch if c.patch is not None else s.patch
        return svc.preview_inputs(
            "perform", patch=patch, chain=c.chain, mix=False, **extra
        )
    return svc.preview_inputs("editor", patch=c.patch, mix=False)


def _result(
    svc, request, *, job, plan, seed, c, key, notes, ai, applied=""
) -> Response:
    ctx = {
        "item": job.ref.text,
        "title": _title(job.ref),
        "plan": plan,
        "plan_json": json.dumps(plan),
        "seed": seed,
        "c": c,
        "key": key,
        "notes": notes,
        "ai": ai,
        "applied": applied,
        "next_seed": (seed + 1) % 1_000_000,
    }
    return HTMLResponse(_render(svc, "fragments/hub_result.html", r=ctx))


@router.post("/hub/make")
@render_outside_lock
def hub_make(request: Request, form: StrForm, svc: Svc) -> Response | OutsideLock:
    """A preview of what the clip makes: the plan the pickers say — or,
    with `ai=1` and a description, the plan Gemini answers after hearing
    the clip (`clipmorph.ask`; the player's own picks win over it) — made
    and rendered, nothing written. `seed` re-rolls ("another take")."""
    s = svc.state
    ref = _ref(svc, form.get("item"))
    if ref is None:
        return svc.toast("That clip is gone.")
    seed = clipmake.seed_of(ref.text, form.get("seed"))
    posted = _posted_plan(form.get("plan", ""))  # "another take" of a plan shown
    if posted is not None:
        base, notes = clipmake.clean_plan(posted, clipmake.sound_exists)
    else:
        base, notes = clipmake.plan_from_form(form, clipmake.sound_exists)
    ai = form.get("ai") == "1"
    describe = clipmorph.clean_describe(form.get("describe"))
    if ai:
        why_not = clipmorph.available()
        if why_not:
            return svc.toast(
                f"✦ Describe needs Gemini: {why_not}. The pickers work without it."
            )
        if not describe:
            return svc.toast("Say what to make of it first (the box above ✦).")
    job = _Job(svc, ref)
    picked = clipmake.locked(form)
    with_beat = form.get("with_beat") == "on"
    again = seed if form.get("again") == "1" else 0  # Gemini asked again: its seed
    a = s.arrangement
    loop = (
        f"{a.root[:-1]} {a.scale_name.replace('_', ' ')}, {a.bpm:g} bpm, "
        f"beat style {s.beat_style or 'none'}"
    )

    def run() -> Response:
        plan, said = base, list(notes)
        info = {"tokens": 0, "summary": "", "why": [], "describe": describe}
        if ai:
            got = cliphub.read(job.data_dir, job.ref)
            if got is None:
                return svc.toast("That clip is gone.")
            audio = cliphub.at_sr(*got)
            facts = cliphub.measure(job.data_dir, job.ref)
            g = clipmake.clip_grid(audio, job.meta, facts, job.arr.bpm)
            ask = _ask_info(job, describe, facts, g, picked, loop)
            try:
                answer = clipmorph.ask(ask, clipmorph.audio_wav(audio), seed=again)
            except AgentError as err:
                return svc.toast(f"✦ Gemini: {err}")
            plan, more = clipmake.clean_plan(
                clipmorph.merge(answer["plan"], picked), clipmake.sound_exists
            )
            said += more
            info.update(
                tokens=answer["tokens"], summary=plan["summary"], why=plan["why"]
            )
        c, _audio, _g = _build(svc, job, plan, seed)
        if c.refused:
            return svc.toast(c.refused)
        with s.lock:
            inp = _preview(svc, job, c, with_beat)
        try:
            result = svc.render_preview(inp)
        except RenderError as err:
            return svc.toast(str(err))
        return _result(
            svc,
            request,
            job=job,
            plan=plan,
            seed=seed,
            c=c,
            key=result.key,
            notes=said,
            ai=info,
        )

    return OutsideLock(run)


def _ask_info(
    job: _Job, describe: str, facts: dict, g, picked: dict, loop: str
) -> dict:
    cat = clipmake.sound_catalogue()
    kind = (
        job.meta.get("source")
        if isinstance(job.meta.get("source"), str)
        else job.ref.kind
    )
    return {
        "describe": describe,
        "name": _title(job.ref),
        "kind": str(kind)[:60],
        "seconds": float(facts.get("seconds") or 0.0),
        "tempo": g.said,
        "key": facts.get("key") or "",
        "heard": _heard_line(job, g),
        "loop": loop,
        "locked": picked,
        "catalogue": {
            "kits": list(clipmake.KITS[2:]),
            "styles": list(clipmake.STYLES[1:]),
            "sounds": cat,
            "fx": [(k, v) for k, v in clipmake.labels()["fxs"][2:]],
        },
    }


def _heard_line(job: _Job, g) -> str:
    return f"{g.bars} bar(s) at {g.said}"


@router.post("/hub/apply")
@render_outside_lock
def hub_apply(request: Request, form: StrForm, svc: Svc) -> Response | OutsideLock:
    """Apply a previewed plan: made again from the same plan (held to the
    studio's names again: the page posts it back) and seed, then written
    as a hand edit writes it — a beat through `apply_pattern` (one pattern
    undo step: Ctrl+Z on Drums), a note part onto the working loop's grid
    (one pattern undo step) and its sound through `load_sound` (one patch
    undo step), a sound through `adopt_audio` (landing on Synths). The
    hub's ↶ Undo takes ALL of it back in one step."""
    ref = _ref(svc, form.get("item"))
    if ref is None:
        return svc.toast("That clip is gone.")
    plan_raw = _posted_plan(form.get("plan", ""))
    if plan_raw is None:
        return svc.toast("Preview it first: there is no plan to apply.")
    plan, notes = clipmake.clean_plan(plan_raw, clipmake.sound_exists)
    seed = clipmake.seed_of(ref.text, form.get("seed"))
    job = _Job(svc, ref)
    s = svc.state

    def run() -> Response:
        c, audio, _g = _build(svc, job, plan, seed)
        if c.refused:
            return svc.toast(c.refused)
        with s.lock:
            return _commit(request, svc, job, c, audio, plan, seed, notes)

    return OutsideLock(run)


def _keep_as_loop(s: StudioState, ref: Ref, target: str) -> str:
    """A note part made from a clip, kept in the Loops list as a loop
    (loops.py): the lane as applied, the sound now playing it, the tempo,
    key and meter — its audio the clip it was made from, so audio -> loop
    is one object — and open on the grid (`open_loop_edit`: an edit there
    saves to it). "" for a beat or a sample (a loop is a note lane), or
    when the store is full."""
    if target != "notes":
        return ""
    title = ref.name if ref.kind == "clip" else f"sample {ref.name[:8]}"
    try:
        name = loops.save(
            s.data_dir,
            f"{title} notes",
            s.arrangement,
            s.sound_payload(),
            take=ref.name if ref.kind == "clip" else None,
        )
    except OSError:
        return ""
    s.open_loop_edit(name)
    return name


def _commit(
    request, svc: StudioService, job: _Job, c, audio, plan, seed, notes
) -> Response:
    """Under the lock: the candidate into the session, one step."""
    s = svc.state
    before = _snapshot(s)
    kept: list[str] = []
    label = f"{c.target} from “{_title(job.ref)}”"
    if c.target == "sound":
        name = f"{job.title} voice" if plan["sound_mode"] == "voice" else job.title
        resp = adopt_audio(
            request,
            svc,
            audio,
            SR,
            name[:60],
            chop=c.sampler["chop"],
            fields=c.sampler["fields"],
            say=" ".join(c.said),
        )
        if resp.status_code == 200:
            svc.hub.push(Made(label, before, _snapshot(s)))
        return resp
    pattern_before = s.pattern_snapshot()
    fields = dict(c.arr)
    if c.target == "beat":
        asked = replace(s.arrangement, **fields)
        refusal = loop_refusal(s.arrangement, asked)
        if refusal:
            return svc.toast(refusal)
        samples = dict(fields.get("drum_samples", s.arrangement.drum_samples))
        for lane, shot in c.hits.items():
            name = free_clip_name(s.data_dir, c.hit_names[lane])
            save_clip(
                s.data_dir, name, shot, meta={"source": f"hub: {_title(job.ref)[:60]}"}
            )
            kept.append(name)
            samples[lane] = name
        extra = {k: fields[k] for k in ("drum_params", "bpm") if k in fields}
        s.arrangement = replace(
            s.arrangement,
            drum_vel=dict(fields["drum_vel"]),
            drum_samples=samples,
            **extra,
        )
        s.apply_pattern(
            {
                "name": f"{job.title} beat",
                "steps": fields["drums"],
                "bars": fields["drum_bars"],
                "steps_per_bar": fields["drum_steps"],
                "swing": fields["drum_swing"],
                "hits": fields["drum_hits"],
                "row_swing": fields["drum_row_swing"],
            }
        )
        if c.fx is not None:
            s.fx_drums = copy.deepcopy(c.fx)
        if c.style:
            s.beat_style, s.beat_seed = c.style, seed
        s.active_slot = None
        where = "Kit"
    else:
        if s.follows(s.active_layer):
            return svc.toast("This lane follows the main one: “draw its own” first.")
        asked = replace(s.arrangement, **fields)
        refusal = loop_refusal(s.arrangement, asked)
        if refusal:
            return svc.toast(refusal)
        s.arrangement = asked
        if c.grid_span:
            s.grid_span = max(s.grid_span, c.grid_span)
        s.show_notes()
        if c.patch is not None:
            if plan["sound"]["source"] == "clip":  # its sample, kept on disk now
                sampler.save(s.sample_dir, audio, SR)
            patch_before = s.patch_snapshot()
            s.load_sound(c.patch, c.chain, label=c.sound_label or c.patch.name)
            s.push_undo_step(patch_before)
        if c.fx is not None:
            s.layers[s.active_layer]["fx"] = copy.deepcopy(c.fx)
        where = "the Loops page"
    if s.pattern_snapshot()["arr"] != pattern_before["arr"]:
        s.push_pattern_undo(pattern_before)
    loop = _keep_as_loop(s, job.ref, c.target)
    s.save()
    svc.hub.push(Made(label, before, _snapshot(s), kept, loop))
    said = f"Applied: {label} — on {where} now. ↶ Undo (here) takes all of it back."
    resp = _result(
        svc,
        request,
        job=job,
        plan=plan,
        seed=seed,
        c=c,
        key="",
        notes=notes,
        ai={"tokens": 0, "summary": plan["summary"], "why": plan["why"]},
        applied=where,
    )
    return with_toast(resp, said)


@router.post("/hub/undo")
def hub_undo(request: Request, form: StrForm, svc: Svc) -> Response:
    """The hub's ↶ Undo: its newest step — an Apply (the session as it was
    before, in full; clips it cut that nothing uses now are deleted) or an
    Update (the clip's old audio and meta written back). Refused when what
    the step left has been edited since: the page's own Ctrl+Z is then the
    way back."""
    s = svc.state
    step = svc.hub.last
    if step is None:
        return svc.toast("Nothing made here to undo.")
    if isinstance(step, Made):
        if _snapshot(s) != step.after:
            return svc.toast(
                f"The session moved since “{step.label}”: Ctrl+Z on its page "
                "takes back what it holds."
            )
        _restore(s, step.before)
        if step.loop:  # the loop the note part was kept as goes with it
            loops.delete(s.data_dir, step.loop)
            s.end_loop_edits(step.loop)
            s.slots_renamed(step.loop, None)
        used = s.clip_usage()
        for name in step.clips:
            if name not in used:
                (clip_dir(s.data_dir) / f"{clip_safe(name)}.wav").unlink(
                    missing_ok=True
                )
                (clip_dir(s.data_dir) / f"{clip_safe(name)}.meta.json").unlink(
                    missing_ok=True
                )
        s.save()
    else:
        path = clip_dir(s.data_dir) / f"{clip_safe(step.name)}.wav"
        st = path.stat() if path.is_file() else None
        if st is None or (st.st_mtime_ns, st.st_size) != step.stamp:
            svc.hub.steps.pop()
            return svc.toast(
                f"“{step.name}” changed since the update: nothing was undone."
            )
        path.write_bytes(step.wav)
        meta = path.with_name(f"{path.stem}.meta.json")
        if step.meta is None:
            meta.unlink(missing_ok=True)
        else:
            meta.write_bytes(step.meta)
        svc.reset_caches()
    svc.hub.steps.pop()
    return with_toast(HTMLResponse(""), f"Undone: {step.label}.")
