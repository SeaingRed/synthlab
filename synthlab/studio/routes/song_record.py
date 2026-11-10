"""Recording onto the song. Note takes into a synth track's
pattern, with one undo step per take: the browser (static/songrec.js)
timestamps, `studio/record.py` quantizes. Mic takes onto an audio track:
the browser records, shifts by the latency and encodes PCM WAV; the route
stores the clip (capped at MAX_CLIP_SECONDS, kept) and places it. Edits go
through `routes/song.py`'s `_commit`, so they are atomic, schema-clean and
answer `song-changed` like every other song edit."""

from __future__ import annotations

import copy
import json
import math
import time
import wave

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from ... import transitions
from ...audio import read_pcm16
from .. import limits, record, schema, takeclean, takecoach
from .. import song_model as sm
from ..agent.client import AgentError
from ..clips import clip_dir, clip_safe, delete_clip, rename_clip, save_clip
from ..drumlab import DRUM_VOICES
from ..routing import (
    ClipUpload,
    OutsideLock,
    RawForm,
    StrForm,
    Svc,
    render_outside_lock,
    studio_router,
)
from .song import _commit, _int, _pattern, _section, _track

router = studio_router()


def _bad() -> sm.SongError:
    return sm.SongError("Unreadable take.")


def _events(raw, kind: str = "notes") -> list[dict]:
    """A post's events, validated: note-ons for a notes pattern (JSON:
    [{midi, t, vel}]), drum hits for a drums pattern ([{voice, t, vel}],
    a voice of the kit). Hostile input is a SongError (a 422 toast),
    never a 500."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        raise _bad() from None
    if not isinstance(data, list) or len(data) > limits.MAX_RECORD_EVENTS:
        raise _bad()
    key = "voice" if kind == "drums" else "midi"
    out = []
    for e in data:
        try:
            what, t, v = e[key], float(e["t"]), float(e.get("vel", 1.0))
        except (TypeError, KeyError, ValueError, AttributeError):
            raise _bad() from None
        if kind == "drums":
            if not isinstance(what, str) or what not in DRUM_VOICES:
                raise _bad()
        elif not (
            isinstance(what, int) and not isinstance(what, bool) and 0 <= what <= 127
        ):
            raise _bad()
        if not (math.isfinite(t) and math.isfinite(v)):
            raise _bad()
        if abs(t) <= limits.MAX_TAKE_WINDOW_SECONDS:
            out.append({key: what, "t": t, "vel": min(1.0, max(0.0, v))})
    return out


def _seconds(form: dict, key: str) -> float:
    try:
        x = float(form.get(key) or 0)
    except ValueError:
        raise _bad() from None
    if not math.isfinite(x):
        raise _bad()
    return min(limits.MAX_TAKE_WINDOW_SECONDS, max(0.0, x))


def _take_target(song: dict, pattern_id: str, form: dict) -> tuple[str, dict]:
    """A take's first post: the pattern it writes and its undo entry.
    `new` makes a notes pattern for an empty cell; `unique=1` forks the
    cell's shared pattern first ("make it unique" is the default)."""
    sid, tid = form.get("section_id"), form.get("track_id")
    if pattern_id != "new" and form.get("unique") != "1":
        before = copy.deepcopy(_pattern(song, pattern_id))
        return pattern_id, {"before": before}
    sec, track = _section(song, sid), _track(song, tid)
    if track["kind"] not in ("synth", "drums"):
        raise sm.SongError("Notes record onto a synth track, hits onto the drums.")
    prev = sec["cells"].get(tid)
    if pattern_id == "new":
        if prev is not None:
            raise sm.SongError("That cell already holds a pattern.")
        bars = max(b for b in limits.BAR_CHOICES if b <= max(1, sec["bars"]))
        name = f"Take {len(song['patterns']) + 1}"
        if track["kind"] == "drums":
            pid = sm.add_pattern(song, "drums", name, {"bars": bars, "steps": 16})
        else:
            pid = sm.add_pattern(
                song, "notes", name, {"bars": bars, "steps_per_bar": 16}
            )
        sm.set_cell(song, sid, tid, pid)
    else:
        if prev != pattern_id:
            raise sm.SongError("That cell holds another pattern.")
        pid = sm.make_unique(song, sid, tid)
    return pid, {"before": None, "cell": [sid, tid, prev]}


def _take_id(raw) -> str | None:
    take = str(raw or "")
    plain = take.replace("-", "").replace("_", "")
    return take if 0 < len(take) <= 32 and plain.isascii() and plain.isalnum() else None


@router.post("/song/pattern/{pattern_id}/record")
def pattern_record(request: Request, pattern_id: str, form: StrForm, svc: Svc):
    """One loop wrap of a take (and once more on stop): `events` (JSON,
    seconds from the section start: note-ons for a synth track's notes
    pattern, hits for the drums track's drums pattern), `take` (its id),
    `mode` (overdub | replace), `grid` (1/8, 1/16, 1/32, off), `t0`/`t1`
    (the window this post covers). A take's first post may fork the cell
    (`unique=1` + `section_id`/`track_id`) or make a pattern for an empty
    cell (`pattern_id` "new"); later posts of the take follow it, whatever
    the path says. Replace clears the first post's window. One undo step
    per take (`/song/record/undo`). The take's undo entry also keeps what
    "✦ Clean up" works from (`/song/take/clean`): the raw events, the
    pattern before the take (`base`) and after it (`after`: a clean-up is
    refused once the pattern was edited since). Answers `song-recorded`."""
    s = svc.state
    take = _take_id(form.get("take"))
    mode, grid = form.get("mode", "overdub"), form.get("grid", "1/16")
    if take is None or mode not in record.MODES or grid not in record.GRIDS:
        return svc.toast("Unreadable take.")
    at = sm.section_index(s.song, form.get("section_id"))
    if at is not None and sm.timed(s.song, s.song["sections"][at]):
        # a take's times are folded onto steps at the song's 4/4 tempo
        # (`record.window_steps`): a section keeping its own would be
        # written to the wrong steps, so it is refused, said plainly
        return svc.toast(
            "Recording into a section with its own meter or tempo isn't "
            "supported yet: record it in a 4/4 section at the song's tempo, "
            "or draw it in the drawer."
        )
    known = next((h for h in s.take_history if h["take"] == take), None)
    entry: dict = {"take": take}
    missed: list = []
    heard: list = []  # this post's events, kept for the clean-up

    def change(song):
        clear: set[int] = set()
        if known is not None:
            pid = known["pattern_id"]
        else:
            pid, first = _take_target(song, pattern_id, form)
            entry.update(first)
        p = _pattern(song, pid)
        kind = p["kind"]
        events = _events(form.get("events"), kind)
        t0, t1 = _seconds(form, "t0"), _seconds(form, "t1")
        steps = p["steps"] if kind == "drums" else p["steps_per_bar"]
        shape = {"bars": p["bars"], "steps_per_bar": steps}
        # times are from the section's start: they fold by its cycle (the
        # pattern restarts at each) before they fold by the pattern. A
        # trimmed block (its span) starts its pattern at its own first bar:
        # times count from there, and a note outside it is not recorded
        si = sm.section_index(song, form.get("section_id"))
        sec = song["sections"][si] if si is not None else None
        cycle = sm.cycle_bars(song, sec) if sec else None
        block = (sec.get("spans") or {}).get(form.get("track_id")) if sec else None
        if block and sec["cells"].get(form.get("track_id")) != pid:
            block = None  # (the take is another cell's pattern)
        if known is None and mode == "replace":
            lead = block[0] * record.bar_seconds(song["bpm"]) if block else 0.0
            clear = record.window_steps(t0 - lead, t1 - lead, bpm=song["bpm"], **shape)
        if known is None:
            entry["base"] = copy.deepcopy(p)
            entry["clear"] = sorted(clear)
            entry["meta"] = {
                "kind": kind,
                "grid": grid,
                "cycle": cycle,
                "block": list(block) if block else None,
                "section_id": form.get("section_id"),
                "track_id": form.get("track_id"),
            }
        where = {
            "bpm": song["bpm"],
            "grid": grid,
            "cycle_bars": cycle,
            "block": tuple(block) if block else None,
            "missed": missed,
        }
        if kind == "drums":
            hits = record.quantize_hits(events, bars=p["bars"], steps=steps, **where)
            record.merge_hits(p, hits, clear=clear)
        else:
            notes = record.quantize(events, **shape, **where)
            record.merge(p, notes, clear=clear)
        if block:
            entry["block"] = f"bars {block[0] + 1}-{block[0] + block[1]}"
        entry["pattern_id"] = pid
        heard[:] = events

    def then():
        where = entry.pop("block", "")
        h = known
        if h is None:
            h = entry
            s.take_history.append(entry)
            del s.take_history[: -limits.MAX_TAKE_HISTORY]
        raw = h.setdefault("raw", [])
        raw.extend(heard[: max(0, limits.MAX_TAKE_RAW_EVENTS - len(raw))])
        h["after"] = copy.deepcopy(s.song["patterns"].get(h["pattern_id"]))
        said = {"song-recorded": {"pattern_id": h["pattern_id"], "take": take}}
        if missed:
            n = len(missed)
            said["studio-toast"] = {
                "message": f"{n} {'hit' if h['meta']['kind'] == 'drums' else 'note'}"
                f"{'' if n == 1 else 's'} fell outside the block "
                f"({where}) and {'was' if n == 1 else 'were'} not recorded."
            }
        return said

    # a take posts once a loop wrap for as long as it records: one step
    return _commit(
        svc,
        change,
        label="record take",
        key=f"take:{take}",
        window=math.inf,
        soon=True,
        then=then,
    )


@router.post("/song/record/undo")
def record_undo(request: Request, svc: Svc) -> Response:
    """Undo the last note take: its pattern goes back to how it was, or —
    a pattern the take made or forked — the cell points where it did and
    the take's pattern leaves the pool."""
    s = svc.state
    if not s.take_history:
        return svc.toast("No take to undo.")
    h = s.take_history[-1]

    def change(song):
        pid = h["pattern_id"]
        if h["before"] is not None:
            if pid in song["patterns"]:
                song["patterns"][pid] = copy.deepcopy(h["before"])
            return
        sid, tid, prev = h["cell"]
        here = sm.section_index(song, sid), sm.track_index(song, tid)
        if None not in here:
            sm.set_cell(song, sid, tid, prev if prev in song["patterns"] else None)
        if pid in song["patterns"] and not sm.pattern_usage(song, pid):
            sm.remove_pattern(song, pid)

    def then():
        s.take_history.pop()

    return _commit(svc, change, label="undo take", then=then)


# --- ✦ Clean up a take -----------------------------------------------------------


def _coach_info(song: dict, h: dict, cleaned: takeclean.Cleaned, steps: int) -> dict:
    """What Gemini is told about a cleaned take (`takecoach.brief`)."""
    meta, pid = h["meta"], h["pattern_id"]
    sec = next((x for x in song["sections"] if x["id"] == meta["section_id"]), None)
    names = {t["id"]: t["name"] for t in song["tracks"]}
    others = [
        names[tid]
        for tid in (sec["cells"] if sec else {})
        if tid != meta["track_id"] and tid in names
    ]
    return {
        "kind": meta["kind"],
        "title": song.get("title", ""),
        "style": (song.get("gen") or {}).get("style", ""),
        "bpm": song["bpm"],
        "key": takeclean.key_name(song["root"], song["scale_name"]),
        "section": sec["name"] if sec else "?",
        "others": others,
        "track": names.get(meta["track_id"], "?"),
        "bars": cleaned.pattern["bars"],
        "steps": steps,
        "grid": takeclean.grid_text(cleaned.pattern, meta["kind"], steps),
        "fixes": cleaned.fixes,
        "stats": cleaned.stats,
        "pattern": song["patterns"][pid]["name"],
    }


@router.post("/song/take/clean")
@render_outside_lock
def take_clean(request: Request, form: StrForm, svc: Svc) -> Response | OutsideLock:
    """ "✦ Clean up" on the last take (or `take`): `takeclean.clean` from
    the take's raw events and the pattern before it — the lean and a
    latency shift, the grid, doubles, velocities, the key — then, with
    `ai=1` (the default) and a Gemini key, `takecoach.coach`'s feedback and
    edits, applied by `takeclean.apply_edits`. The deterministic half runs
    under the lock; Gemini's call outside it (`render_outside_lock`), and
    the result is committed under it again — one song undo step, "clean up
    take" (Ctrl+Z gives the take back as it was recorded). Refused when the
    take's pattern was edited since the take (or since its last clean-up,
    which may be run again: it always starts from the raw take). Answers
    `take-cleaned` with what was done and said."""
    s = svc.state
    want = _take_id(form.get("take")) if form.get("take") else None
    h = next(
        (
            x
            for x in reversed(s.take_history)
            if (want is None or x["take"] == want) and x.get("raw") is not None
        ),
        None,
    )
    if h is None:
        return svc.toast("Record a take first: ✦ Clean up works on the last one.")
    pid, meta = h["pattern_id"], h["meta"]
    song = s.song
    p = song["patterns"].get(pid)
    if p is None or p != h["after"]:
        return svc.toast(
            "That take's pattern was edited since — ✦ Clean up works on a take "
            "as it was recorded."
        )
    steps = p["steps"] if meta["kind"] == "drums" else p["steps_per_bar"]
    cleaned = takeclean.clean(
        takeclean.Take(
            kind=meta["kind"],
            raw=h["raw"],
            bpm=song["bpm"],
            bars=p["bars"],
            steps=steps,
            grid=meta["grid"],
            base=h["base"],
            cycle_bars=meta["cycle"],
            block=tuple(meta["block"]) if meta["block"] else None,
            clear=h["clear"],
            root=song["root"],
            scale_name=song["scale_name"],
        )
    )
    config = takecoach.load_config()
    why_not = takecoach.available(config) if form.get("ai", "1") == "1" else "off"
    info = _coach_info(song, h, cleaned, steps)
    key = (song["root"], song["scale_name"])

    def commit(pattern: dict, report: dict) -> Response:
        """Under the lock: the cleaned pattern in, if the take's pattern
        is still as the take left it."""
        if s.song["patterns"].get(pid) != h["after"]:
            return svc.toast("The pattern changed while it was being cleaned up.")

        def change(song):
            song["patterns"][pid] = copy.deepcopy(pattern)

        def then():
            h["after"] = copy.deepcopy(s.song["patterns"].get(pid))
            return {"take-cleaned": report}

        return _commit(svc, change, label="clean up take", then=then)

    report = {
        "take": h["take"],
        "pattern": info["pattern"],
        "track": info["track"],
        "fixes": cleaned.fixes,
        "feedback": [],
        "edits": [],
        "ai": "off" if why_not == "off" else ("" if why_not is None else why_not),
    }
    if why_not is not None:
        return commit(cleaned.pattern, report)

    def run() -> Response:
        pattern = copy.deepcopy(cleaned.pattern)
        try:
            got = takecoach.coach(info, config=config)
        except AgentError as err:
            report["ai"] = str(err)
        else:
            report["feedback"] = got["feedback"]
            report["edits"] = takeclean.apply_edits(
                pattern,
                meta["kind"],
                got["edits"],
                steps=steps,
                root=key[0],
                scale_name=key[1],
            )
            report["tokens"] = got["tokens"]
        with s.lock:
            return commit(pattern, report)

    return OutsideLock(run)


# --- mic takes (Task 10) --------------------------------------------------------


@router.post("/song/take")
def song_take(request: Request, form: RawForm, upload: ClipUpload, svc: Svc):
    """A mic take (a 16-bit PCM WAV, latency already shifted out by the
    browser) onto an audio track: saved to the clip store as
    `take-YYYYmmdd-HHMMSS` (cut at MAX_CLIP_SECONDS and kept), then placed
    in `section_id` on `track_id` at `at_bar` (0-based, clamped into the
    section). BodySizeLimit caps the body first."""
    s = svc.state
    sid, tid = str(form.get("section_id", "")), str(form.get("track_id", ""))
    si, ti = sm.section_index(s.song, sid), sm.track_index(s.song, tid)
    if si is None or ti is None or s.song["tracks"][ti]["kind"] != "audio":
        return svc.toast("Arm an audio track in a section that exists.")
    if upload.data is None:
        return svc.toast("That take is too large." if upload.too_large else "No take.")
    try:
        audio, sr = read_pcm16(upload.data)
    except (wave.Error, EOFError, ValueError):
        return svc.toast("Couldn't read that take as a 16-bit WAV.")
    if not limits.MIN_SR <= sr <= limits.MAX_SR or not len(audio):
        return svc.toast(f"Unsupported take ({sr} Hz).")
    try:
        at_bar = int(str(form.get("at_bar", "0")))
    except ValueError:
        at_bar = 0
    base = f"take-{time.strftime('%Y%m%d-%H%M%S')}"
    name, n = base, 1
    while (clip_dir(s.data_dir) / f"{clip_safe(name)}.wav").exists():
        n += 1
        name = f"{base}-{n}"
    kept = len(audio) / sr
    if len(s.song["sections"][si]["audio"]) >= limits.MAX_LANE_CLIPS:
        # refused before anything is written: no orphan clip in the store
        return svc.toast(f"A section holds at most {limits.MAX_LANE_CLIPS} takes.")
    save_clip(s.data_dir, name, audio, sr=sr, meta={"source": "take"})
    name = clip_safe(name)

    def change(song):
        sm.place_take(song, sid, tid, name, at_bar)

    def then():
        msg = f"Kept {name}"
        if kept > limits.MAX_CLIP_SECONDS:
            msg += f" — the first {limits.MAX_CLIP_SECONDS:g} s"
        return {"studio-toast": {"message": msg + "."}}

    resp = _commit(svc, change, label="record audio take", then=then)
    if resp.status_code >= 400:
        # the song refused the take (a cap, a length limit): nothing
        # refers to the clip, so it doesn't stay in the store either
        delete_clip(s.data_dir, name)
    return resp


@router.post("/song/take/{clip}/rename")
def song_take_rename(request: Request, clip: str, form: StrForm, svc: Svc):
    """Rename a take inline: the clip itself, and every reference to it
    (every section's takes, one-shots, baselines) follows."""
    s = svc.state
    old, raw = clip_safe(clip), form.get("name", "").strip()
    if not (clip_dir(s.data_dir) / f"{old}.wav").exists():
        return svc.toast("No such take.")
    if not raw:
        return svc.toast("A take needs a name.")
    new = clip_safe(raw)
    if new != old and not rename_clip(s.data_dir, old, new):
        return svc.toast(f"There is already a clip called “{new}”.")

    def change(song):
        pass

    def then():
        if new != old:  # every reference, the undo ring's, the Lab chop's
            s.follow_clip_rename(old, new)

    return _commit(svc, change, then=then)


@router.post("/song/take/edit")
def song_take_edit(request: Request, form: StrForm, svc: Svc) -> Response:
    """Move or remove a placed take. A take has no id: it is named as the
    page draws it — `section_id`, `track_id`, `clip`, `at_bar` (0-based;
    the first match).

    - `op=move`: to `to_section` / `to_track` (default: where it is) at
      `to_bar` (0-based; 1-based with `bar_base=1`, the take menu's field),
      clamped into the section. The destination follows `place_take`'s
      rules (an audio track, MAX_LANE_CLIPS a section).
    - `op=remove`: the take leaves the song; its clip stays in the store.

    One undo step each ("move take" / "remove take"). A take that is no
    longer there, or a refused destination, is a 422 toast."""
    op = form.get("op", "")
    if op not in ("move", "remove"):
        return Response(status_code=422)
    sid, tid, clip = (form.get(k, "") for k in ("section_id", "track_id", "clip"))
    said: dict = {}

    def change(song):
        at = _int(form, "at_bar", "The bar")
        if op == "remove":
            sm.remove_take(song, sid, tid, clip, at)
            said["text"] = f"Removed {clip} from {_section(song, sid)['name']}."
            return
        to = _section(song, form.get("to_section") or sid)
        bar = at
        if form.get("to_bar", "").strip():
            bar = _int(form, "to_bar", "The bar") - (form.get("bar_base") == "1")
        landed = sm.move_take(
            song, sid, tid, clip, at, to["id"], form.get("to_track") or tid, bar
        )
        said["text"] = f"Moved {clip} to {to['name']}, bar {landed + 1}."

    def then():
        return {"studio-toast": {"message": said["text"]}}

    return _commit(svc, change, label=f"{op} take", then=then)


@router.get("/song/calibration.json")
def calibration_get(svc: Svc) -> Response:
    """This machine's mic latency (ms): what songrec.js shifts a take by
    when the browser reports no input/output latency of its own."""
    return JSONResponse(svc.calibration())


@router.post("/song/calibration")
def calibration_set(form: StrForm, svc: Svc) -> Response:
    try:
        ms = float(form.get("latency_ms", ""))
    except ValueError:
        return svc.toast("Latency is a number of milliseconds.")
    clean = schema.conform({"latency_ms": ms}, schema.nodes().CALIBRATION)
    (svc.state.data_dir / "calibration.json").write_text(
        json.dumps(clean), encoding="utf-8"
    )
    return JSONResponse(clean)


# --- generated takes: risers and impacts -----------------------------------------

TRANSITION_BARS = (1, 2, 4, 8)


def _transition_spec(form: dict, sec: dict, bpm: float) -> dict:
    """The generator's settings from a form, validated: kind, direction,
    bars (a TRANSITION_BARS choice, cut to the section and the clip cap),
    brightness and tail (0..1, two decimals, so the clip name is exact)."""
    kind = form.get("kind", "")
    if kind not in transitions.GENERATORS:
        raise sm.SongError(
            f"Pick a transition: {', '.join(transitions.GENERATORS)}; up or down."
        )
    # no direction posted: the way the kind's name reads (a riser up, an
    # impact down). A bed has none, so both name the one clip.
    direction = form.get("direction") or transitions.DEFAULT_DIRECTION[kind]
    if direction not in transitions.DIRECTIONS:
        raise sm.SongError(
            f"Pick a transition: {', '.join(transitions.GENERATORS)}; up or down."
        )
    if kind in transitions.DIRECTIONLESS:
        direction = transitions.DEFAULT_DIRECTION[kind]
    try:
        bars = int(form.get("bars") or 2)
        bright = float(form.get("brightness") or 0.6)
        tail = float(form.get("tail") or 0.4)
    except ValueError:
        raise sm.SongError("Bars, brightness and tail are numbers.") from None
    if not (math.isfinite(bright) and math.isfinite(tail)):
        raise sm.SongError("Bars, brightness and tail are numbers.")
    fits = [
        b
        for b in TRANSITION_BARS
        if b <= max(1, bars)
        and b <= sec["bars"]
        and b * 240.0 / bpm <= limits.MAX_CLIP_SECONDS
    ]
    return {
        "kind": kind,
        "direction": direction,
        "bars": max(fits, default=1),
        "brightness": round(min(1.0, max(0.0, bright)), 2),
        "tail": round(min(1.0, max(0.0, tail)), 2),
        "bpm": bpm,
    }


def _transition_clip(data_dir, spec: dict) -> tuple[str, bool]:
    """The clip holding `spec`'s render: its name, and whether this call
    wrote it. The generators are deterministic, so a clip already saved
    from the same settings (its meta says so) is placed again rather than
    rendered twice; a clip that merely has the name gets a suffix."""
    base = (
        f"{spec['kind']}-{spec['direction']}-{spec['bars']}bar"
        f"-b{round(spec['brightness'] * 100)}-t{round(spec['tail'] * 100)}"
        f"-{spec['bpm']:g}bpm"
    )
    d = clip_dir(data_dir)
    name, n = clip_safe(base), 1
    while (d / f"{name}.wav").exists():
        try:
            meta = json.loads((d / f"{name}.meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        if meta.get("transition") == spec:
            return name, False
        n += 1
        name = clip_safe(f"{base}-{n}")
    seconds = spec["bars"] * 240.0 / spec["bpm"]
    audio = transitions.generate(
        spec["kind"], seconds, spec["direction"], spec["brightness"], spec["tail"]
    )
    meta = {
        "source": "transition",
        "bpm": spec["bpm"],
        "bars": spec["bars"],
        "transition": spec,
    }
    save_clip(data_dir, name, audio, meta=meta)
    return name, True


@router.post("/song/section/{section_id}/transition")
def song_transition(request: Request, section_id: str, form: StrForm, svc: Svc):
    """ "Add riser / impact" (a section's ⋯ menu): render a transition
    (`synthlab.transitions.GENERATORS`: riser, impact, sweep, swell,
    subdrop, bed — deterministic) at the song's bpm into
    the clip store and place it as a take in the section, on `track_id`
    (an audio track) or, when that is empty, on the first audio track —
    one named "FX" is added when the song has none. What leads *into* a
    downbeat (`transitions.leads_in`: a rising riser or sweep, a swell, a
    reversed impact or sub drop) ends on the section's last bar line; what
    comes *off* one (a downlifter, an impact, a sub drop) and a bed start
    on its first. The section's end cuts a take, as it cuts every take."""
    s = svc.state
    si = sm.section_index(s.song, section_id)
    if si is None:
        return svc.toast("No such section.")
    sec = s.song["sections"][si]
    try:
        spec = _transition_spec(form, sec, sm.section_bpm(s.song, sec))
    except sm.SongError as err:
        return svc.toast(str(err))
    if len(sec["audio"]) >= limits.MAX_LANE_CLIPS:
        return svc.toast(f"A section holds at most {limits.MAX_LANE_CLIPS} takes.")
    audio_tracks = [t["id"] for t in s.song["tracks"] if t["kind"] == "audio"]
    tid = form.get("track_id", "")
    if tid and tid not in audio_tracks:
        return svc.toast("Riser and impact takes go on an audio track.")
    if not tid and not audio_tracks and len(s.song["tracks"]) >= limits.MAX_TRACKS:
        return svc.toast(f"A song holds at most {limits.MAX_TRACKS} tracks.")
    name, wrote = _transition_clip(s.data_dir, spec)
    # a riser, an uplifting sweep, a swell, an impact or a sub drop reversed
    lead_in = transitions.leads_in(spec["kind"], spec["direction"])

    def change(song):
        track = tid or (audio_tracks[0] if audio_tracks else None)
        if track is None:
            track = sm.add_track(song, "audio", "FX")
        here = _section(song, section_id)
        bar = max(0, here["bars"] - spec["bars"]) if lead_in else 0
        sm.place_take(song, section_id, track, name, bar)

    def then():
        return {"studio-toast": {"message": f"Added {name}."}}

    resp = _commit(svc, change, label=f"add {spec['kind']}", then=then)
    if resp.status_code >= 400 and wrote:
        delete_clip(s.data_dir, name)  # nothing refers to it
    return resp


# --- a clip from the store, placed ------------------------------------------------


def place_clip(
    svc, section_id: str, track_id: str, clip: str, at_bar: int, say: str = ""
) -> Response:
    """Place a clip that is already in the clip store as a take: in
    `section_id`, on `track_id` (an audio track) or — empty — on the song's
    first audio track, one named "Audio" being added when it has none, at
    `at_bar`. The half `/song/take` never had (it stores an upload and then
    places it): the Sample Lab's "→ Song" calls this, and so does
    `/song/section/{id}/clip`. Answers like every song edit (`_commit`); a
    refusal is a 422 toast and the clip stays in the store, untouched."""
    s = svc.state
    name = clip_safe(clip)
    if not (clip_dir(s.data_dir) / f"{name}.wav").is_file():
        return svc.toast("That clip is gone.")
    landed: dict = {}

    def change(song):
        track = track_id
        if not track:
            track = next((t["id"] for t in song["tracks"] if t["kind"] == "audio"), "")
        if not track:
            track = sm.add_track(song, "audio", "Audio")
        sec = _section(song, section_id)
        landed["bar"] = sm.place_take(song, section_id, track, name, at_bar)
        landed["section"] = sec["name"]

    def then():
        where = f"{landed['section']}, bar {landed['bar'] + 1}"
        return {"studio-toast": {"message": say or f"Placed {name} in {where}."}}

    return _commit(svc, change, label="place clip", then=then)


@router.post("/song/section/{section_id}/clip")
def song_place_clip(request: Request, section_id: str, form: StrForm, svc: Svc):
    """ "Place this clip": `clip` (a clip-store name) as a take in the
    section, on `track_id` (default: the first audio track) at `at_bar`
    (0-based, default 0, clamped into the section)."""
    try:
        at_bar = int(form.get("at_bar") or 0)
    except ValueError:
        return svc.toast("The bar is a whole number.")
    return place_clip(
        svc, section_id, form.get("track_id", ""), form.get("clip", ""), at_bar
    )
