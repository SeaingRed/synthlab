"""Perform's launcher (studio `docs/loop-and-perform.md`, "Slots and
scenes"): lanes x rows of slots,
each a saved loop. A launch — a slot, a scene (a row), an idea or a song
section opened as a scene — compiles to the working loop
(`StudioState.launch`) and answers as every Perform launch does: the loop
changes at the next quantise point (`preview-ready` with `queue`), the
keys at once. The grid's own edits (place, move, clear, name) touch
nothing that plays; each is a pattern undo step of its own."""

from __future__ import annotations

import json
from dataclasses import asdict, replace

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from .. import jam, limits, looplib, loops, schema, song_bridge, tosong
from .. import song_model as sm
from ..render import RenderError
from ..routing import StrForm, Svc, prompt_value, studio_router
from ..service import StudioService
from ..state import Arrangement, StudioState
from ..words import plural
from . import song_jam as jam_routes
from .pattern import with_toast
from .song import _commit

router = studio_router()


def _at(s: StudioState, form: StrForm, lane: str = "lane", row: str = "row"):
    """The slot a post names (lane, row), or None."""
    try:
        at = int(form.get(lane, "")), int(form.get(row, ""))
    except ValueError:
        return None
    ok = 0 <= at[0] < s.SLOT_LANES and 0 <= at[1] < limits.MAX_SLOT_ROWS
    return at if ok else None


def _row_of(form: StrForm) -> int | None:
    try:
        row = int(form.get("row", ""))
    except ValueError:
        return None
    return row if 0 <= row < limits.MAX_SLOT_ROWS else None


def _shown(s: StudioState, name: str) -> str:
    """What the Loops lists call loop `name` (`looplib.titles`)."""
    return looplib.titles(s.data_dir).get(name, name)


def _lane_word(s: StudioState, lane: int) -> str:
    return "the drums" if lane == s.DRUM_LANE else f"lane {lane + 1}"


def _launched(svc: StudioService, said: str) -> Response:
    """A launch's answer: saved, the next preview queued for the quantise
    point (as `routes/perform._queued`), and what it did as a toast."""
    s: StudioState = svc.state
    s.save()
    try:
        headers = svc.preview_trigger(view="perform")
    except RenderError as err:
        return svc.toast(str(err))
    trig = json.loads(headers["HX-Trigger"])
    trig["preview-ready"]["queue"] = True
    trig["studio-toast"] = {"message": said}
    return HTMLResponse("", headers={"HX-Trigger": json.dumps(trig)})


def _launch(svc: StudioService, picks: dict, rows: dict, said: str, setup=None):
    """`picks` (lane -> a loaded loop, None to stop it) launched from
    `rows` — `setup` first, inside the same undo step (an idea's or a
    section's tempo and key) — or the toast saying why not."""
    s: StudioState = svc.state
    titles = looplib.titles(s.data_dir)  # a refusal names a loop as the lists do
    for lp in picks.values():
        if lp is not None:
            lp["title"] = titles.get(lp["name"], lp["name"])
    refused = s.launch(picks, rows, setup=setup)
    if refused:
        return svc.toast(refused)
    return _launched(svc, said)


def _scene_picks(s: StudioState, row: int) -> tuple[dict, dict]:
    """A row as a scene launch: each lane's slot's loop — an empty slot
    (or a loop gone from the store) stops its lane, as an empty slot of a
    scene does in every launcher."""
    slots = (s.slot_rows()[row]["slots"] if row < len(s.slot_rows()) else None) or []
    picks = {}
    for lane in range(s.SLOT_LANES):
        name = slots[lane] if lane < len(slots) else None
        picks[lane] = loops.load(s.data_dir, name) if name else None
    return picks, dict.fromkeys(range(s.SLOT_LANES), row)


# --- the grid ----------------------------------------------------------------------


@router.post("/fragment/perform/slot/place")
def slot_place(request: Request, form: StrForm, svc: Svc) -> Response:
    """A loop (`name`) into a slot — a Loops panel row dropped on it: a
    beat in the drums' lane, notes in a layer slot's. One undo step."""
    s: StudioState = svc.state
    at = _at(s, form)
    lp = loops.load(s.data_dir, form.get("name", "").strip())
    if at is None:
        return Response(status_code=422)
    if lp is None or lp["kind"] == "idea":
        return svc.toast(
            "That loop is gone." if lp is None else "An idea opens as a scene."
        )
    if (at[0] == s.DRUM_LANE) != (lp["kind"] == "beat"):
        where = "the drums" if lp["kind"] == "beat" else "a lane of notes"
        return svc.toast(f"“{_shown(s, lp['name'])}” goes in {where}.")
    s.set_slot(*at, lp["name"])
    s.save()
    return HTMLResponse("")


@router.post("/fragment/perform/slot/keep")
def slot_keep(request: Request, form: StrForm, svc: Svc) -> Response:
    """An empty slot's ＋: what its lane plays now kept as a new loop in
    that slot — a lane's notes and sound, or the drum grid's beat (a beat
    a loop already holds is that loop) — and open on the lane from now on:
    it is the slot that plays."""
    s: StudioState = svc.state
    at = _at(s, form)
    if at is None:
        return Response(status_code=422)
    lane, row = at
    try:
        name = _keep_lane(s, lane)
    except OSError as err:
        return svc.toast(str(err))
    if name is None:
        what = "beat" if lane == s.DRUM_LANE else "notes"
        return svc.toast(
            f"{_lane_word(s, lane).capitalize()} has no {what} to keep: "
            "draw some, or drag a loop here."
        )
    s.set_slot(lane, row, name)
    s.open_slot_loop(name, lane, row)
    _select(s, form, lane)  # (once it holds the loop: never an empty lane)
    s.save()
    return with_toast(HTMLResponse(""), f"Kept “{_shown(s, name)}” in the launcher.")


@router.post("/fragment/perform/slot/select")
def slot_select(request: Request, form: StrForm, svc: Svc) -> Response:
    """An empty slot's click: it is selected — slots.js keeps the
    selection in the page, per tab: where ⏺ Rec, ⟲ Capture and the keys
    arp's print land (`slotrec`) — and its lane becomes the edited one when
    it already has a sound, so the keys play what the slot will hold (a
    lane with none keeps the edited one: the take carries that sound
    there). Nothing plays, renders or becomes an undo step."""
    s: StudioState = svc.state
    at = _at(s, form)
    if at is None:
        return Response(status_code=422)
    lane = at[0]
    sounded = lane == s.active_layer or bool(
        lane < len(s.layers) and s.layers[lane].get("patch")
    )
    if lane != s.DRUM_LANE and lane != s.active_layer and sounded:
        s.select_layer(lane)
        s.save()
    return HTMLResponse("")


def _keep_lane(s: StudioState, lane: int) -> str | None:
    """`lane`'s notes (and its sound) or the drum grid's beat as a loop,
    named from what it is; its name (an existing beat's), or None when
    the lane holds nothing."""
    a = s.arrangement
    key = {"bpm": a.bpm, "root": a.root, "scale_name": a.scale_name}
    if lane == s.DRUM_LANE:
        if not a.drums:
            return None
        word = (s.beat_style or "beat").capitalize()
        pattern = s.pattern_from_arrangement(word)
        want = loops.beat_key(pattern)
        for r in loops.listing(s.data_dir):
            if r["beat_key"] == want:
                return r["name"]
        name = loops.auto_name(s.data_dir, word, {}, key, a.drum_bars, beat=True)
        return loops.save_beat(s.data_dir, name, pattern, a)
    fields = s.lane_state(lane)
    if not fields["cells"]:
        return None
    sound = s.lane_sound_payload(lane)
    patch = (sound or {}).get("patch") or {}
    name = loops.auto_name(s.data_dir, patch.get("name") or "Lane", fields, key, a.bars)
    return loops.save(s.data_dir, name, replace(a, **fields), sound)


@router.post("/fragment/perform/slot/clear")
def slot_clear(request: Request, form: StrForm, svc: Svc) -> Response:
    """✕ on a slot: it is empty (the loop stays in the Loops). One undo
    step; what plays goes on playing."""
    s: StudioState = svc.state
    at = _at(s, form)
    if at is None:
        return Response(status_code=422)
    if s.slot_at(*at) is None:
        return svc.toast("That slot is empty.")
    s.set_slot(*at, None)
    s.save()
    return HTMLResponse("")


@router.post("/fragment/perform/slot/move")
def slot_move(request: Request, form: StrForm, svc: Svc) -> Response:
    """A slot dragged onto another of a lane of its kind: moved, or —
    `copy`, a Ctrl-drag — copied. One undo step."""
    s: StudioState = svc.state
    frm, to = _at(s, form), _at(s, form, "to_lane", "to_row")
    if frm is None or to is None:
        return Response(status_code=422)
    if s.slot_at(*frm) is None:
        return svc.toast("That slot is empty.")
    if (frm[0] == s.DRUM_LANE) != (to[0] == s.DRUM_LANE):
        return svc.toast("A beat stays in the drums, notes in a lane of notes.")
    if frm != to:
        s.move_slot(frm, to, keep=form.get("copy") == "1")
        s.save()
    return HTMLResponse("")


# --- launching ------------------------------------------------------------------------


def _select(s: StudioState, form: StrForm, lane: int) -> None:
    """`select`: the post came from a slot itself (a click, Enter), which
    selects it — the editor opens its lane (selection, not launch,
    moves the editor; a scene's ▶, Alt+1…8 or the deck's scene keys do
    not). A layer slot becomes the edited one, as its lane head's name
    does; the drums' kind is the page's (slots.js turns Notes | Drums)."""
    if form.get("select") == "1" and lane != s.DRUM_LANE:
        s.select_layer(lane)


@router.post("/fragment/perform/slot/launch")
def slot_launch(request: Request, form: StrForm, svc: Svc) -> Response:
    """Launch one slot: its loop on its lane at the next quantise point
    (`StudioState.launch`: one undo step; the lane's slot that played
    gives way). From the slot itself (`select`) it selects it first — and
    the slot that already plays is only selected: nothing to launch."""
    s: StudioState = svc.state
    at = _at(s, form)
    if at is None:
        return Response(status_code=422)
    name = s.slot_at(*at)
    lp = loops.load(s.data_dir, name) if name else None
    if lp is None:
        return svc.toast("That slot is empty." if not name else "That loop is gone.")
    lp["title"] = _shown(s, lp["name"])
    refused = s.launch_refusal({at[0]: lp})
    if refused:  # (before the selection: it never leaves a lane made for nothing)
        return svc.toast(refused)
    _select(s, form, at[0])
    if form.get("select") == "1" and s.slot_playing(at[0]) == at[1]:
        if not s.lane_stopped(at[0]):
            s.save()
            return HTMLResponse("")
    said = f"“{_shown(s, lp['name'])}” on {_lane_word(s, at[0])}. Ctrl+Z takes it back."
    return _launch(svc, {at[0]: lp}, {at[0]: at[1]}, said)


@router.post("/fragment/perform/scene/launch")
def scene_launch(request: Request, form: StrForm, svc: Svc) -> Response:
    """Launch a scene: every lane's slot in the row at once — one render,
    one undo step; a lane whose slot there is empty stops."""
    s: StudioState = svc.state
    row = _row_of(form)
    if row is None:
        return Response(status_code=422)
    picks, rows = _scene_picks(s, row)
    if not any(picks.values()):
        return svc.toast(f"Scene {row + 1} is empty: drag loops onto its slots.")
    said = f"Scene {row + 1}. Ctrl+Z takes it back."
    return _launch(svc, picks, rows, said)


@router.post("/fragment/perform/slot/stop")
def slot_stop(request: Request, form: StrForm, svc: Svc) -> Response:
    """■ a lane (`lane`; "all": every lane): it stops at the next
    quantise point — a layer slot muted, the drums out of what ▶ plays —
    and its notes stay. Launching a slot there plays it again."""
    s: StudioState = svc.state
    raw = form.get("lane", "")
    if raw == "all":
        lanes = list(range(s.SLOT_LANES))
    else:
        try:
            lanes = [int(raw)]
        except ValueError:
            return Response(status_code=422)
        if not 0 <= lanes[0] < s.SLOT_LANES:
            return Response(status_code=422)
    picks = {lane: None for lane in lanes if not s.lane_stopped(lane)}
    if not picks:
        return svc.toast("Nothing plays there.")
    who = "Every lane" if raw == "all" else _lane_word(s, lanes[0]).capitalize()
    return _launch(svc, picks, {}, f"{who} stops. Ctrl+Z takes it back.")


# --- ideas: a scene kept, and a song's section as a scene ----------------------------


@router.post("/fragment/perform/scene/keep")
def scene_keep(request: Request, form: StrForm, svc: Svc) -> Response:
    """A scene kept as an idea (a loop of kind "idea" in the Loops panel):
    the loop in each of its slots, the tempo, key and meter — named by the
    prompt (blank: the scene's name, else "Idea")."""
    s: StudioState = svc.state
    row = _row_of(form)
    if row is None:
        return Response(status_code=422)
    rows = s.slot_rows()
    slots = rows[row]["slots"] if row < len(rows) else []
    if not any(slots):
        return svc.toast(f"Scene {row + 1} is empty: nothing to keep.")
    asked = (prompt_value(request) or form.get("new", "")).strip()
    name = asked or rows[row]["name"] or "Idea"
    try:
        kept = loops.save_idea(s.data_dir, name, slots, s.arrangement)
    except OSError as err:
        return svc.toast(str(err))
    if rows[row]["name"] != kept:
        s.name_scene(row, kept)
    s.save()
    return with_toast(HTMLResponse(""), f"Kept scene {row + 1} as the idea “{kept}”.")


@router.post("/fragment/perform/scene/idea")
def scene_idea(request: Request, form: StrForm, svc: Svc) -> Response:
    """Open an idea: its slots as a new scene (the first empty row),
    launched — at its tempo and key (and meter) — one undo step."""
    s: StudioState = svc.state
    idea = loops.load(s.data_dir, form.get("name", "").strip())
    if idea is None or idea["kind"] != "idea":
        return svc.toast("That idea is gone.")
    row = s.free_scene_row()
    if row is None:
        return svc.toast(f"The launcher's {limits.MAX_SLOT_ROWS} scenes are full.")
    slots = list(idea["idea"])
    picks = {i: loops.load(s.data_dir, n) if n else None for i, n in enumerate(slots)}
    key = idea["key"] | {"meter": idea["meter"]}

    def setup() -> None:
        s.fill_scene(row, slots, idea["name"])
        s.adopt_tempo(key)

    said = f"The idea “{idea['name']}” is scene {row + 1}. Ctrl+Z takes it back."
    return _launch(svc, picks, dict.fromkeys(range(s.SLOT_LANES), row), said, setup)


@router.post("/fragment/perform/scene/section")
def scene_section(request: Request, form: StrForm, svc: Svc) -> Response:
    """A song section as a scene of new slots (it was Perform's Sections
    launcher, which replaced the working loop): each of its synth lanes and
    its beat kept as a loop (one already holding the same, reused), laid
    in the first empty row and launched — at the section's tempo, key and
    meter, with its kit — one undo step."""
    s: StudioState = svc.state
    i = sm.section_index(s.song, form.get("section", ""))
    if i is None:
        return svc.toast("No such section.")
    row = s.free_scene_row()
    if row is None:
        return svc.toast(f"The launcher's {limits.MAX_SLOT_ROWS} scenes are full.")
    sec = s.song["sections"][i]
    try:
        slots, p = _section_loops(s, sec)
    except OSError as err:
        return svc.toast(str(err))
    picks = {j: loops.load(s.data_dir, n) if n else None for j, n in enumerate(slots)}
    arr = p["arr"]
    key = {k: arr[k] for k in ("bpm", "root", "scale_name")}
    key["meter"] = sm.section_meter(s.song, sec)

    def setup() -> None:
        s.fill_scene(row, slots, sec["name"])
        s.adopt_tempo(
            key, kit={k: arr[k] for k in song_bridge.KIT}, fx_drums=p["fx_drums"]
        )

    said = f"The section “{sec['name']}” is scene {row + 1}. Ctrl+Z takes it back."
    return _launch(svc, picks, dict.fromkeys(range(s.SLOT_LANES), row), said, setup)


def _section_loops(s: StudioState, sec: dict) -> tuple[list, dict]:
    """A section's lanes as loops (`song_bridge.section_loop` / `to_loop`:
    its main lane, then each other synth lane, on one grid; its beat): the
    names in launcher order (a lane past the fourth is left out, as
    `section_to_loop` leaves it) and the section's loop pieces."""
    heard = sm.in_meter(s.song, sec)
    p = song_bridge.section_loop(heard, sec)
    song_bridge.to_loop(heard, p)
    node = schema.nodes().ARRANGEMENT
    arr = Arrangement(
        **schema.conform({**p["arr"], "meter": sm.section_meter(s.song, sec)}, node)
    )
    slots: list = [None] * s.SLOT_LANES
    lanes = []
    if p["patch"] is not None and arr.cells:
        main = {
            "patch": p["patch"],
            "main_fx": p["main"]["fx"],
            "curves": p["curves"] or {},
        }
        lanes.append((arr, main))
    for lay in p["layers"]:
        seq = lay.get("seq") or {}
        lane = replace(
            arr,
            cells=lay["cells"],
            velocities=seq.get("velocities") or {},
            slides=seq.get("slides") or [],
            cuts=seq.get("cuts") or [],
        )
        sound = {
            "patch": lay["patch"],
            "main_fx": lay.get("fx") or [],
            "curves": lay.get("curves") or {},
        }
        lanes.append((lane, sound))
    for j, (lane, sound) in enumerate(lanes[: s.DRUM_LANE]):
        word = (sound["patch"] or {}).get("name") or "lane"
        slots[j] = _reuse_or_save(s, f"{sec['name']} · {word}", lane, sound)
    if arr.drums:
        beat = {
            "name": sec["name"],
            "steps": arr.drums,
            "bars": arr.drum_bars,
            "steps_per_bar": arr.drum_steps,
            "swing": arr.drum_swing,
            "hits": arr.drum_hits,
            "row_swing": arr.drum_row_swing,
        }
        slots[s.DRUM_LANE] = loops.save_beat(
            s.data_dir, f"{sec['name']} beat", beat, arr
        )
    return slots, p


def _reuse_or_save(s: StudioState, name: str, arr: Arrangement, sound: dict) -> str:
    """The loop `name` when it holds these notes in this sound already (a
    section opened again), else a new loop ("… 2" when the name is taken
    by an edited one)."""
    have = loops.load(s.data_dir, loops.loop_safe(name))
    if have is not None and have["kind"] == "notes" and have["sound"] is not None:
        notes = sorted((p, int(st)) for p, st in have["lane"]["cells"])
        same = notes == sorted((p, int(st)) for p, st in arr.cells)
        if same and have["sound"][0].name == (sound["patch"] or {}).get("name"):
            return have["name"]
    return loops.save(s.data_dir, name, arr, {"format": 2, "layers": [], **sound})


# --- to the Song: a scene as a section; the launches as played ----------------------


def _scene_of(s: StudioState, form: StrForm) -> tuple[list, str, dict] | str:
    """The scene a post names — `row`, the launcher's, at the loop's tempo,
    key and meter; or `idea`, a kept one at its own — as (its slots'
    names, its name, its key with `meter`), or why not."""
    a = s.arrangement
    if form.get("idea", "").strip():
        idea = loops.load(s.data_dir, form.get("idea", "").strip())
        if idea is None or idea["kind"] != "idea":
            return "That idea is gone."
        return list(idea["idea"]), idea["name"], idea["key"] | {"meter": idea["meter"]}
    row = _row_of(form)
    rows = s.slot_rows()
    if row is None or row >= len(rows) or not any(rows[row]["slots"]):
        return "That scene is empty: drag loops onto its slots."
    key = {"bpm": a.bpm, "root": a.root, "scale_name": a.scale_name, "meter": a.meter}
    return list(rows[row]["slots"]), rows[row]["name"] or f"Scene {row + 1}", key


def _scene_section(s: StudioState, song: dict, index: dict, slots, name, key, **kw):
    """Scene `slots` (names) as a section of `song` (`add_scene_section`):
    its id and the loops left out (another meter, gone)."""
    picks = [loops.load(s.data_dir, n) if n else None for n in slots]
    gone = [n for n, lp in zip(slots, picks, strict=True) if n and lp is None]
    lanes, beat, out = tosong.scene_parts(s, picks, key["meter"])
    if not lanes and beat is None:
        raise sm.SongError(f"“{name}” holds nothing the song can take.")
    sid = song_bridge.add_scene_section(
        song,
        index,
        name,
        lanes,
        beat,
        meter=key["meter"],
        bpm=key["bpm"],
        kit=asdict(s.arrangement),
        fx_drums=s.fx_drums,
        curves=s.curves,
        **kw,
    )
    return sid, gone + out


def _first_in(song: dict, s: StudioState, key: dict) -> None:
    """The first section of an empty song gives it its tempo and key (and
    the loop's groove)."""
    if not song["sections"]:
        song |= tosong.song_wide(asdict(s.arrangement) | key)


@router.post("/fragment/perform/scene/song")
def scene_song(request: Request, form: StrForm, svc: Svc) -> Response:
    """A scene → Song (a launcher row's →§, an idea's → Song in the Loops
    panel): a new last section — each slot's loop a pattern on a synth
    track that plays its sound, its beat on the drums track, its tempo
    (kept by the section when it is not the song's), key (the song's, when
    it is the first) and meter. One song undo step. Past the 8 synth
    tracks it asks which track takes a lane (`onto`); a scene the song
    holds already asks whether to place it again (`again`)."""
    s: StudioState = svc.state
    got = _scene_of(s, form)
    if isinstance(got, str):
        return svc.toast(got)
    slots, name, key = got
    said: list[str] = []

    def change(song: dict) -> None:
        _first_in(song, s, key)
        index = song_bridge.pattern_index(song)
        try:
            sid, left = _scene_section(
                s, song, index, slots, name, key, onto=tosong.onto_of(form)
            )
        except song_bridge.TrackCap as err:
            raise tosong.cap_ask(song, err, form) from None
        if form.get("again") != "1" and (twin := tosong.already(song, sid)):
            raise tosong.already_ask(song, twin, name, form)
        n = sm.section_index(song, sid) + 1
        sec = song["sections"][n - 1]
        own = f" at its own {sec['bpm']:g} bpm" if sec.get("bpm") else ""
        bars = plural(sec["bars"], "bar")
        say = f"“{name}” is section {n} of the song{own}: {bars}."
        if left:
            titles = looplib.titles(s.data_dir)
            say += " Left out: " + ", ".join(titles.get(x, x) for x in left) + "."
        said.append(say + " Ctrl+Z on Song takes it back.")

    try:
        resp = _commit(svc, change, label=f"add “{name}” to the song")
    except tosong.Ask as ask:
        return tosong.ask_response("/fragment/perform/scene/song", ask)
    return with_toast(resp, said[0]) if resp.status_code == 200 and said else resp


def _captured(raw: str) -> list[dict]:
    """The launcher's log of scene launches as played (slots.js): a JSON
    list of `{bar, slots, name}` (a scene sounded from `bar`) and `{bar}`
    (everything stopped) — or a SongError for anything else, whole."""
    if len(raw) > jam_routes.MAX_EVENTS_CHARS * 4:
        raise sm.SongError(jam_routes.TOO_MANY)
    try:
        got = json.loads(raw) if raw.strip() else []
    except (ValueError, RecursionError):
        got = None
    if not isinstance(got, list) or len(got) > limits.MAX_JAM_EVENTS:
        raise sm.SongError(jam_routes.UNREADABLE)
    out = []
    for ev in got:
        bar = ev.get("bar") if isinstance(ev, dict) else None
        whole = isinstance(bar, int) and not isinstance(bar, bool)
        if not whole or not 0 <= bar < limits.MAX_LONG_BAR:
            raise sm.SongError(jam_routes.UNREADABLE)
        if "slots" not in ev:
            out.append({"bar": bar})
            continue
        slots, name = ev.get("slots"), ev.get("name") or ""
        ok = isinstance(slots, list) and len(slots) == StudioState.SLOT_LANES
        ok = ok and isinstance(name, str)
        ok = ok and all(x is None or (isinstance(x, str) and x) for x in slots)
        if not ok or not any(slots):
            raise sm.SongError(jam_routes.UNREADABLE)
        clean = [loops.loop_safe(x) if x else None for x in slots]
        out.append({"bar": bar, "slots": clean, "name": name[: limits.MAX_TITLE_CHARS]})
    return out


@router.post("/fragment/perform/scene/capture")
def scene_capture(request: Request, form: StrForm, svc: Svc) -> Response:
    """Scene → Song: the launcher's
    scene launches as they were played (`events`, slots.js's log; `bars`,
    its length) become sections at the end of the song, in order — the
    jam's capture (`jam.commit`) over scenes made into sections it never
    adds itself (`scenes`): a scene left to loop is its section repeated,
    one cut short plays its first bars.
    One song undo step; `scenes-captured` tells the page to drop its log
    (a refusal keeps it). The 8-track cap refuses here: → Song a scene
    first to choose its tracks."""
    s: StudioState = svc.state
    a = s.arrangement
    key = {"bpm": a.bpm, "root": a.root, "scale_name": a.scale_name, "meter": a.meter}
    made: dict = {}

    def change(song: dict) -> None:
        log = _captured(str(form.get("events", "")))
        bars = jam_routes._bars(form)
        _first_in(song, s, key)
        index = song_bridge.pattern_index(song)
        scenes: dict[str, dict] = {}
        by: dict[tuple, str] = {}
        events, left = [], []
        for ev in log:
            if "slots" not in ev:
                events.append({"bar": ev["bar"]})
                continue
            names = tuple(ev["slots"])
            if names not in by:
                if any(n and not loops.exists(s.data_dir, n) for n in names):
                    raise sm.SongError("A loop of the captured scenes is gone.")
                sid, out = _scene_section(
                    s, song, index, list(names), ev["name"] or "Scene", key
                )
                scenes[sid] = song["sections"].pop(sm.section_index(song, sid))
                by[names] = sid
                left += out
            events.append({"bar": ev["bar"], "scene": by[names]})
        ids, _loose = jam.commit(song, events, bars, scenes)
        made.update(ids=ids, left=left)

    def then():
        n = len(made["ids"])
        say = f"{n} section{'' if n == 1 else 's'} added from the launcher."
        if made["left"]:
            say += f" {len(set(made['left']))} loop(s) in another meter stayed out."
        return {
            "scenes-captured": {"sections": made["ids"]},
            "studio-toast": {"message": say + " Ctrl+Z on Song takes it back."},
        }

    return _commit(svc, change, label="capture the launcher's scenes", then=then)
