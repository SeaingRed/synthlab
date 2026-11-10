"""Free record: one button that records every key and its timing until
■ — no 4/4, no bar lines, just the playing — then shape, cut and morph it
to the size wanted.

⏺ Rec's **free** mode (static/keyrec.js: the dock's length "free", or the
Loops panel's ⏺ free) records from the first note to ■ — no count-in, no
bar line, any length up to `limits.MAX_FREE_TAKE_SECONDS` (60 s; every
other take stops at the clip cap, 30) — and the shaper
(static/shaper.js, `fragments/free_shaper.html`) makes a loop of it:

- `POST /take/keys/free`: the free take — its audio from its first note (a
  16-bit WAV), its notes ([start, length, midi, velocity] in beats at the
  page's tempo `bpm`; a fifth item 1 flags a note the keys arp played) —
  kept as a keys take (`keytakes`: meta `free: true`, the arp's notes in
  its sidecar's `arp`). No loop yet: the answer's `free-shape` opens the
  shaper on it.
- `GET /fragment/free/shaper?take=`: the shaper's body for a free take —
  its notes in seconds, the take's tempo and the page's, the meter's rates.
- `POST /take/keys/free/hear`: ▶ — the take as shaped (`played`: beats from
  the loop's start at `bpm`; `bars`, `steps`, `strength`, `swing`) placed
  as Make loop places it (`loops.place_free`) and rendered as a loop row's
  ▶ renders a loop (`takes._render_loop`): `X-Preview-Url`.
- `POST /take/keys/free/loop`: Make loop — that placement kept as a loop
  (`loops.save(free=...)`: the shaped notes its `played`, the strength its
  `quantise`), named from what it is, in the take's sound; into the
  selected slot when one is named (`lane`, `row`, `how`: `slotrec.land`,
  as ⏺ Rec's take lands).

The timing the shaper keeps is the note grid's `note_nudge` (a share of a
step on a note's start step: `arrange._lane_timeline`), so the loop plays
as shaped on the grid, in the render and in the MIDI file.
"""

from __future__ import annotations

import json
import time
import wave
from urllib.parse import quote

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ...audio import read_pcm16
from .. import keytakes as kt
from .. import limits, loops, slotrec
from .. import meter as _meter
from ..clips import clip_safe, free_clip_name, save_clip
from ..routing import ClipUpload, StrForm, Svc, studio_router
from ..state import StudioState
from ..state.model import Arrangement
from ..words import plural
from .clips import kept_as
from .pattern import with_toast
from .takes import _landed, _number, _render_loop, _title

router = studio_router()

SWING_MAX = loops.MAX_SWING


def _free_notes(text: str) -> tuple[list[list], list[int]]:
    """A free take's notes as posted, checked whole (`keytakes._note`):
    (the notes sorted by start, the indices of the keys arp's among them).
    Raises `keytakes.TakeRefused`."""
    if len(text) > limits.MAX_TAKE_NOTES_CHARS:
        raise kt.TakeRefused("That take holds too many notes.")
    try:
        raw = json.loads(text or "[]")
    except (ValueError, RecursionError) as err:
        raise kt.TakeRefused("The take's notes aren't readable.") from err
    if not isinstance(raw, list) or len(raw) > limits.MAX_TAKE_NOTES:
        raise kt.TakeRefused("That take holds too many notes.")
    pairs = []
    for item in raw:
        arp = isinstance(item, list) and len(item) == 5 and item[4] == 1
        note = kt._note(item[:4] if arp else item)
        if note is None:
            raise kt.TakeRefused("The take's notes aren't readable.")
        pairs.append((note, arp))
    pairs.sort(key=lambda p: p[0])
    return [n for n, _a in pairs], [i for i, (_n, a) in enumerate(pairs) if a]


@router.post("/take/keys/free")
def take_free(
    request: Request, form: StrForm, upload: ClipUpload, svc: Svc
) -> Response:
    """A free take into the clip store, kept (`keytakes`, meta `free`):
    its audio from its first note, its notes from it, the page's tempo and
    the length in bars at it, the key, the meter, the sound that played
    it. No loop: `HX-Trigger`'s `free-shape` opens the shaper on it (the
    take stays in the Loops list, ✂ Shape, until a loop is made). A
    refusal is a 422 toast, nothing written."""
    if upload.data is None:
        return svc.toast("That take is too large." if upload.too_large else "No take.")
    try:
        audio, sr = read_pcm16(upload.data)
    except (wave.Error, EOFError, ValueError):
        return svc.toast("Couldn't read that take as a 16-bit WAV.")
    if not limits.MIN_SR <= sr <= limits.MAX_SR or not len(audio):
        return svc.toast(f"Unsupported take ({sr} Hz).")
    try:
        notes, arp = _free_notes(form.get("notes", ""))
    except kt.TakeRefused as err:
        return svc.toast(str(err))
    if not notes:
        return svc.toast("Nothing was played: a free take starts on its first note.")
    bpm = _number(form.get("bpm", ""), *limits.BPM_RANGE)
    bars = _number(form.get("bars", ""), 0.01, limits.MAX_TAKE_BARS)
    if bpm is None or bars is None:
        return svc.toast("A take needs its tempo and its length in bars.")
    s: StudioState = svc.state
    asked = form.get("name", "").strip() or "keys"
    name = free_clip_name(s.data_dir, asked)
    a = s.arrangement
    meta = {"source": kt.SOURCE, "kept": True, "free": True}
    meta |= {"at": round(time.time(), 3), "bpm": round(bpm, 3), "bars": round(bars, 3)}
    meta |= {"root": a.root, "scale": a.scale_name}
    if a.meter != _meter.DEFAULT:
        meta["meter"] = a.meter
    meta["sound"] = kt.save_sound(s.data_dir, s.sound_payload())
    kt.write_notes(s.data_dir, name, {"notes": notes, "arp": arp})
    save_clip(
        s.data_dir, name, audio, sr=sr, meta=meta, cap=limits.MAX_FREE_TAKE_SECONDS
    )
    secs = min(len(audio) / sr, limits.MAX_FREE_TAKE_SECONDS)
    said = (
        f"Took a free take, {secs:.1f} s · {plural(len(notes), 'note')}: "
        "shape it into a loop (✂ Shape in the Loops list opens it again)."
    )
    resp = kept_as(HTMLResponse(""), asked, name, said)
    trig = json.loads(resp.headers.get("HX-Trigger") or "{}")
    trig["free-shape"] = {"take": name}
    resp.headers["HX-Trigger"] = json.dumps(trig)
    return resp


def _free_take(svc, name: str) -> tuple[str, dict] | Response:
    """(the clip's name, its meta) for a free take, or the toast."""
    s: StudioState = svc.state
    name = clip_safe(name.strip())
    meta = kt.read_meta(s.data_dir, name) if name else None
    if meta is None:
        return svc.toast("That take is gone.")
    return name, meta


def _take_bpm(s: StudioState, meta: dict) -> float:
    bpm = meta.get("bpm")
    lo, hi = limits.BPM_RANGE
    return float(bpm) if kt.meta_number(bpm) and lo <= bpm <= hi else s.arrangement.bpm


def _take_meter(meta: dict) -> str:
    m = meta.get("meter")
    return m if m in _meter.METERS else _meter.DEFAULT


@router.get("/fragment/free/shaper")
def free_shaper(request: Request, svc: Svc) -> Response:
    """The shaper's body for the free take `take` (shaper.js draws and
    edits it): its notes in seconds from its first note — the arp's flagged
    — its length, the tempo it was played at and the page's, the meter's
    bar and rates, the bars a loop may be. Read-only."""
    got = _free_take(svc, request.query_params.get("take", ""))
    if isinstance(got, Response):
        return got
    name, meta = got
    s: StudioState = svc.state
    doc = kt.read_notes(s.data_dir, name, meta)
    played_at = _take_bpm(s, meta)
    sec = 60.0 / played_at
    arp = set(doc.get("arp") or ())
    notes = [
        [round(n[0] * sec, 4), round(n[1] * sec, 4), n[2], n[3], int(i in arp)]
        for i, n in enumerate(doc["notes"])
    ]
    m = _take_meter(meta)
    length = max(
        kt.bars_of(meta) * _meter.bar_beats(m) * sec,
        max((n[0] + n[1] for n in notes), default=0.0),
    )
    root, scale = kt.key_of(meta) or (s.arrangement.root, s.arrangement.scale_name)
    data = {
        "take": name,
        "notes": notes,
        "seconds": round(length, 4),
        "bpm": round(played_at, 3),
        "page_bpm": round(s.arrangement.bpm, 3),
        "bar_beats": _meter.bar_beats(m),
        "steps": loops.default_steps(m),
        "bars": list(loops.GRID_BARS),
        "swing_max": SWING_MAX,
        "bpm_range": list(limits.BPM_RANGE),
    }
    ctx = {
        "take": name,
        "title": name,
        "data": data,
        "rates": loops.rates(m),
        "meter": m,
        "key": loops.key_short(root, scale),
        "sound": kt.sound_name(s.data_dir, meta.get("sound")),
        "arp": bool(arp),
    }
    html = svc.templates.get_template("fragments/free_shaper.html").render(**ctx)
    return HTMLResponse(html)


def _shaped(svc, form: StrForm):
    """The take as the shaper shaped it, placed: `(take, meta, played,
    lane, loop)` — `played` the notes as shaped (beats from the loop's
    start at `bpm`), `lane` their placement keeping the timing
    (`loops.place_free` at `steps`, `strength`, `swing`), `loop` the loop
    dict a Loops row's ▶ renders (`takes._render_loop`) — or the toast."""
    got = _free_take(svc, form.get("take", ""))
    if isinstance(got, Response):
        return got
    name, meta = got
    s: StudioState = svc.state
    try:
        played = kt.parse_notes(form.get("played", ""))
    except kt.TakeRefused as err:
        return svc.toast(str(err))
    if not played:
        return svc.toast("Nothing is left to make a loop of: undo a cut (Ctrl+Z).")
    m = _take_meter(meta)
    bpm = _number(form.get("bpm", ""), *limits.BPM_RANGE)
    try:
        bars, steps = int(form.get("bars", "")), int(form.get("steps", ""))
        strength = int(form.get("strength", "") or 0)
        swing = float(form.get("swing", "") or 0.0)
    except ValueError:
        return svc.toast("A loop needs its tempo, bars, rate and quantise.")
    if (
        bpm is None
        or bars not in loops.GRID_BARS
        or steps not in _meter.step_choices(m)
        or bars * steps > limits.MAX_GRID_STEPS
        or not 0 <= strength <= 100
        or not 0.0 <= swing <= SWING_MAX  # (NaN is neither)
    ):
        return svc.toast("A loop needs its tempo, bars, rate and quantise.")
    lane = loops.place_free(played, m, bars, steps, strength, swing)
    if not lane["cells"]:
        return svc.toast("No note lands inside the loop: a longer loop, or fit.")
    root, scale = kt.key_of(meta) or (s.arrangement.root, s.arrangement.scale_name)
    loop = {
        "name": name,
        "kind": "notes",
        "key": {"bpm": round(bpm, 3), "root": root, "scale_name": scale},
        "meter": m,
        "lane": lane,
        "sound": kt.load_sound(s.data_dir, meta.get("sound")),
        "strength": strength,
        "swing": round(swing, 3),
    }
    return name, meta, played, lane, loop


@router.post("/take/keys/free/hear")
def free_hear(request: Request, form: StrForm, svc: Svc) -> Response:
    """▶ in the shaper: the take as shaped, rendered as its loop will be —
    `X-Preview-Url` (shaper.js loops it from the next bar line of what
    plays). Nothing is kept but the render (`svc.render`'s cache)."""
    got = _shaped(svc, form)
    if isinstance(got, Response):
        return got
    *_rest, loop = got
    made = _render_loop(svc, loop)
    if isinstance(made, Response):
        return made
    return HTMLResponse("", headers={"X-Preview-Url": f"/preview/{made.key}.wav"})


@router.post("/take/keys/free/loop")
def free_loop(request: Request, form: StrForm, svc: Svc) -> Response:
    """Make loop: the take as shaped kept as a free loop — its notes on
    the grid keeping their timing (`loops.place_free`), the shaped notes its
    `played` (Q places them again, keeping the timing below 100%), the
    take's sound, tempo as shaped, key and meter — named from what it is.
    With a slot named (`lane`, `row`, `how`) it lands there as ⏺ Rec's take
    does (`slotrec.land`: one undo step). `X-Loop-Name` names it."""
    got = _shaped(svc, form)
    if isinstance(got, Response):
        return got
    take, meta, played, lane, loop = got
    s: StudioState = svc.state
    key = loop["key"]
    arr = Arrangement(
        bpm=key["bpm"],
        root=key["root"],
        scale_name=key["scale_name"],
        meter=loop["meter"],
        **lane,
    )
    payload = kt.sound_payload(s.data_dir, meta.get("sound")) or s.sound_payload()
    played_on = (payload or {}).get("patch", {}).get("name", "")
    asked = loops.auto_name(s.data_dir, played_on, lane, key, lane["bars"])
    try:
        made = loops.save(
            s.data_dir,
            asked,
            arr,
            payload,
            played=played,
            quantise=loop["strength"],
            free={"swing": loop["swing"], "take": take},
        )
    except OSError as err:
        return svc.toast(str(err))
    rate = loops.rate_word(loop["meter"], lane["steps_per_bar"])
    how = "as shaped" if not loop["strength"] else f"quantise {loop['strength']}%"
    said = (
        f"Made the loop “{_title(s, made)}”: {plural(lane['bars'], 'bar')}, "
        f"{rate}, {how} — in the Loops panel: Edit it on the Loops page, or → Song."
    )
    resp = with_toast(HTMLResponse(""), said)
    resp.headers["X-Loop-Name"] = quote(made)
    at = slotrec.target(s, form)
    notes = [[n[0], n[1], n[2], n[3]] for n in played]
    return _landed(svc, resp, made, at, notes) if at else resp
