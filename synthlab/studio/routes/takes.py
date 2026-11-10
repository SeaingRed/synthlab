"""Keys takes (`keytakes.py`) and loops (`loops.py`): what ⏺ Rec in the
keyboard dock makes (static/keyrec.js), and the Loops list that picks one
(`looplib.py`: the dock's strip, Perform's bin, the clip library's group).

- `POST /take/keys`: a take — the key bus's audio (a 16-bit WAV), the notes
  played, its bpm and bars — into the clip store, kept, its notes in their
  sidecar, AND its loop: the notes as played placed on a grid of the take's
  bars at the meter's sixteenths, the sound that played them, the tempo,
  key and meter (`loops.place`). One Rec, one object. The lists come back
  by their watches.
- `POST /loop/edit`: the loop on Perform's note grid, as the loop: every
  edit there is written back to it (`StudioState.mirror_loop_edit`) until
  `/loop/edit/done`, another loop or section on the grid, or Ctrl+Z past
  its opening.
- `POST /loop/quantise`: place the notes as played again, at a rate and a
  strength (0 = as played .. 100).
- `POST /loop/song`: into the song — a section holding it as a pattern on
  a track with its sound, or its audio as a take on an audio track.
- A row of a take no loop names (`take=` in place of `name=`) makes its
  loop first (`_loop_named`): what was recorded before is never lost.
- `POST /take/keys/keep`: ★ Keep — named (the prompt), `kept: true` (a take
  from before one Rec made a loop; the list no longer offers it).
- `POST /take/keys/discard`, `/take/keys/clear`: ✕ one, or every unkept one.
- `POST /take/keys/notes`: → Perform as notes — onto the edited lane's note
  grid (`keytakes.to_grid`), one pattern undo step; the page lands on Perform.
- `POST /take/keys/sample`: → Perform as a sample — the take's audio is the
  edited lane's sampler sound (`sampler.use_clip`), landing on Perform.
- `POST /take/keys/clean`, `/take/keys/unclean`: ✦ Clean up its notes, ↶.
- `POST /loop/save`, `/loop/load`, `/loop/rename`, `/loop/delete`,
  `/loop/bounce`: loops (`loops.py`).

A clean-up writes only the take's notes sidecar, never its meta: nothing
the clip library or the hub draws moves.
"""

from __future__ import annotations

import copy
import json
import math
import time
import wave
from dataclasses import asdict, replace
from urllib.parse import quote

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ...audio import read_pcm16
from .. import keytakes as kt
from .. import (
    limits,
    looplib,
    loops,
    schema,
    slotrec,
    song_bridge,
    takeclean,
    takecoach,
    tosong,
)
from .. import meter as _meter
from .. import song_model as sm
from ..agent.client import AgentError
from ..clips import (
    clip_dir,
    clip_safe,
    delete_clip,
    free_clip_name,
    save_clip,
    write_meta,
)
from ..render import RenderError, loop_refusal
from ..routing import (
    ClipUpload,
    OutsideLock,
    StrForm,
    Svc,
    pattern_edit,
    prompt_value,
    render_outside_lock,
    studio_router,
)
from ..state import StudioState
from ..state.model import Arrangement, materialise
from ..words import plural
from .clips import kept_as
from .pattern import with_toast
from .sampler import use_clip
from .song import _commit

router = studio_router()


def _number(raw: str, lo: float, hi: float) -> float | None:
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if lo <= v <= hi else None  # NaN is neither


@router.post("/take/keys")
def take_keys(
    request: Request, form: StrForm, upload: ClipUpload, svc: Svc
) -> Response:
    """A keys take into the clip store, kept: its audio as recorded (the
    browser has cut it to its bars), with `keytakes`' meta and its notes'
    sidecar — and its loop (`_loop_from_take`): named as the clip, or —
    a name the clock gave ("keys-20261107-150616", keyrec.js's) — from
    what it is ("Acid bass riff · Am · 124 · 2b", `loops.auto_name`), which
    `X-Loop-Name` names (percent-encoded: a header is latin-1, a generated
    name holds "·"). Everything is checked before anything is written; a
    refusal is a 422 toast. Past `limits.MAX_LOOPS` the take is kept
    without one (the Loops list shows it as a take; its first action makes
    the loop)."""
    if upload.data is None:
        return svc.toast("That take is too large." if upload.too_large else "No take.")
    try:
        audio, sr = read_pcm16(upload.data)
    except (wave.Error, EOFError, ValueError):
        return svc.toast("Couldn't read that take as a 16-bit WAV.")
    if not limits.MIN_SR <= sr <= limits.MAX_SR or not len(audio):
        return svc.toast(f"Unsupported take ({sr} Hz).")
    try:
        notes = kt.parse_notes(form.get("notes", ""))
    except kt.TakeRefused as err:
        return svc.toast(str(err))
    bpm = _number(form.get("bpm", ""), *limits.BPM_RANGE)
    bars = _number(form.get("bars", ""), 0.01, limits.MAX_TAKE_BARS)
    if bpm is None or bars is None:
        return svc.toast("A take needs its tempo and its length in bars.")
    s: StudioState = svc.state
    asked = form.get("name", "").strip() or "keys"
    name = free_clip_name(s.data_dir, asked)
    # kept from the start: ✕ is the way a take goes
    meta = {"source": kt.SOURCE, "kept": True, "at": round(time.time(), 3)}
    meta |= {"bpm": round(bpm, 3), "bars": round(bars, 3)}
    # what played it: the whole sound (kept by content), the key, the meter
    a = s.arrangement
    meta |= {"root": a.root, "scale": a.scale_name}
    if a.meter != _meter.DEFAULT:
        meta["meter"] = a.meter
    payload = s.sound_payload()
    meta["sound"] = kt.save_sound(s.data_dir, payload)
    if form.get("beat") == "1":
        meta["beat"] = True
    kt.write_notes(s.data_dir, name, {"notes": notes})  # there before the clip is
    save_clip(s.data_dir, name, audio, sr=sr, meta=meta)
    resp = kept_as(HTMLResponse(""), asked, name)
    try:
        made = _loop_from_take(s, name, meta, notes, payload)
        resp.headers["X-Loop-Name"] = quote(made)  # "·": a header is latin-1
    except OSError as err:  # the store is full: the take stays, as a take
        return with_toast(resp, f"Kept the take “{name}” — no loop of it: {err}")
    at = slotrec.target(s, form)
    return _landed(svc, resp, made, at, notes) if at else resp


@router.post("/take/keys/capture")
def take_capture(request: Request, form: StrForm, svc: Svc) -> Response:
    """⟲ Capture (and the keys arp's print, `arp`): what the keys sounded
    lately — keyrec.js's always-on buffer, notes only, cut to its bars — as
    a loop with no audio (`_loop_from_take`, named from what it is: the
    page's `keys-<time>` asks for that), the sound playing now, the
    page's tempo (`bpm`), the loop's key and meter. With a slot named
    (`lane`, `row`, `bar`) it lands there as a new loop when that slot is
    empty, else in the lane's next empty slot (`slotrec.capture_target`:
    a capture never overwrites, deliberately; `how` is
    not read), and none empty keeps it in the Loops only; the toast says
    which. `X-Loop-Name` names it. A refusal is a 422 toast."""
    try:
        notes = kt.parse_notes(form.get("notes", ""))
    except kt.TakeRefused as err:
        return svc.toast(str(err))
    if not notes:
        return svc.toast("Nothing was played on the keys to capture.")
    bpm = _number(form.get("bpm", ""), *limits.BPM_RANGE)
    bars = _number(form.get("bars", ""), 0.01, limits.MAX_TAKE_BARS)
    if bpm is None or bars is None:
        return svc.toast("A capture needs its tempo and its length in bars.")
    s: StudioState = svc.state
    a = s.arrangement
    meta = {"bpm": round(bpm, 3), "bars": round(bars, 3)}
    meta |= {"root": a.root, "scale": a.scale_name}
    if a.meter != _meter.DEFAULT:
        meta["meter"] = a.meter
    asked = form.get("name", "").strip() or "keys"
    try:
        made = _loop_from_take(s, asked, meta, notes, s.sound_payload(), clip=False)
    except OSError as err:
        return svc.toast(str(err))
    what = "Printed the keys arp" if form.get("arp") == "1" else "Captured"
    n = plural(len(notes), "note")
    said = f"{what}: “{_title(s, made)}”, {n} — in the Loops panel."
    at, moved = slotrec.capture_target(s, form)
    resp = with_toast(HTMLResponse(""), f"{said} {moved}" if moved and not at else said)
    resp.headers["X-Loop-Name"] = quote(made)
    return _landed(svc, resp, made, at, notes, moved) if at else resp


def _landed(
    svc, resp: Response, made: str, at: tuple, notes: list, moved: str = ""
) -> Response:
    """A take's (or a capture's) loop `made` into the launcher's slot `at`
    (`slotrec.land`): `resp` with the preview its launch or overdub cues
    (queued for a launch, as every Perform launch is) and what it did as
    its toast (`moved`, first: why a capture went elsewhere); refused, the
    loop stays in the Loops and the toast says why."""
    s: StudioState = svc.state
    try:
        said, queue = slotrec.land(s, made, at, notes)
    except slotrec.Refused as err:
        return with_toast(resp, f"Kept “{_title(s, made)}” — not in the slot: {err}")
    said = f"{moved} {said}" if moved else said
    s.save()
    try:
        trig = json.loads(svc.preview_trigger(view="perform")["HX-Trigger"])
    except RenderError as err:
        return with_toast(resp, f"{said} ({err})")
    if queue:
        trig["preview-ready"]["queue"] = True
    trig["studio-toast"] = {"message": said, "kind": "done"}
    resp.headers["HX-Trigger"] = json.dumps(trig)
    return resp


def _take_meter(meta: dict) -> str:
    m = meta.get("meter")
    return m if m in _meter.METERS else _meter.DEFAULT


def _loop_from_take(
    s: StudioState,
    take: str,
    meta: dict,
    notes: list,
    sound: dict | None,
    steps: int = 0,
    strength: int = 100,
    *,
    clip: bool = True,
) -> str:
    """A keys take's loop, kept under the take's name: its notes as played
    (`played`, never rewritten) placed on a grid of the take's bars
    (`loops.grid_bars`) at `steps` a bar (the meter's sixteenths unasked)
    and `strength` (`loops.place`), `sound` (the one that played it), the
    take's tempo, key and meter; its audio is the take (`clip` False: a
    ⟲ Capture's, which has none). The loop's name. OSError past
    `limits.MAX_LOOPS`."""
    meter = _take_meter(meta)
    steps = steps if steps in _meter.step_choices(meter) else loops.default_steps(meter)
    played = kt.bars_of(meta) or (
        max((n[0] + n[1] for n in notes), default=0) / _meter.bar_beats(meter)
    )
    bars = loops.grid_bars(played, steps)
    lane = loops.place(notes, meter, bars, steps, strength)
    root, scale = kt.key_of(meta) or (s.arrangement.root, s.arrangement.scale_name)
    bpm = meta.get("bpm")
    lo, hi = limits.BPM_RANGE
    bpm = float(bpm) if kt.meta_number(bpm) and lo <= bpm <= hi else s.arrangement.bpm
    arr = Arrangement(bpm=bpm, root=root, scale_name=scale, meter=meter, **lane)
    name = take
    if loops.is_auto(take):  # named by the clock: named from what it is
        played_on = (sound or {}).get("patch", {}).get("name", "")
        key = {"bpm": bpm, "root": root, "scale_name": scale}
        name = loops.auto_name(s.data_dir, played_on, lane, key, bars)
    return loops.save(
        s.data_dir,
        name,
        arr,
        sound,
        take=take if clip else None,
        played=notes,
        quantise=strength,
    )


def _loop_named(svc, form: StrForm) -> str | Response:
    """The loop a Loops row's action names — `name`, a loop; or `take`, a
    keys take no loop names yet (one from before a Rec made both), whose
    loop is made here first, from its notes as played and the sound that
    played them; or `slot`, a Drums bank slot the list shows as a beat,
    kept here first as a beat loop — or the toast saying it is gone."""
    s: StudioState = svc.state
    if _banked(form):
        return _slot_kept(svc, form)
    take = form.get("take", "").strip()
    if not take:
        name = form.get("name", "").strip()
        if not loops.exists(s.data_dir, name):
            return svc.toast("That loop is gone.")
        return loops.loop_safe(name)
    take = clip_safe(take)
    meta = kt.read_meta(s.data_dir, take)
    if meta is None:
        return svc.toast("That take is gone.")
    if (have := loops.of_take(s.data_dir, take)) is not None:
        return have
    doc = kt.read_notes(s.data_dir, take, meta)
    sound = kt.sound_payload(s.data_dir, meta.get("sound")) or s.sound_payload()
    try:
        return _loop_from_take(s, take, meta, doc.get("raw", doc["notes"]), sound)
    except OSError as err:
        return svc.toast(str(err))


def _banked(form: StrForm) -> bool:
    """Does a Loops row's post name a beat no loop holds yet — a bank slot
    (`slot`) or a beat of the old beat library (`lib`)?"""
    return bool(form.get("slot", "").strip() or form.get("lib", "").strip())


def _bank_slot(svc, form: StrForm) -> dict | None:
    """The Drums bank's pattern in the posted `slot` — or the old beat
    library's in `lib` — or None (no such slot, an empty one, no such
    beat)."""
    if not form.get("slot", "").strip():
        return svc.state.read_drum_pattern(form.get("lib", "").strip())
    try:
        slot = int(form.get("slot", ""))
    except ValueError:
        return None
    bank = svc.state.drum_bank
    return bank[slot] if 0 <= slot < len(bank) else None


def _slot_kept(svc, form: StrForm) -> str | Response:
    """A bank slot's beat (or the library's) as a beat loop (a ★, tags or
    a rename on its row keep it): the loop that already holds that beat,
    or a new one under the slot's name. The slot itself is untouched."""
    s: StudioState = svc.state
    pattern = _bank_slot(svc, form)
    if pattern is None:
        return svc.toast("That bank slot is empty.")
    want = loops.beat_key(pattern)
    for row in loops.listing(s.data_dir):
        if row["beat_key"] == want:
            return row["name"]
    name = loops.free_name(s.data_dir, pattern.get("name") or "beat")
    try:
        return loops.save_beat(s.data_dir, name, pattern, s.arrangement)
    except OSError as err:
        return svc.toast(str(err))


def _take_or_toast(svc, form: StrForm) -> tuple[str, dict] | Response:
    """The take a post names (`name`) and its meta — or the toast saying
    it is gone, for the route to return."""
    name = clip_safe(form.get("name", "").strip())
    meta = kt.read_meta(svc.state.data_dir, name)
    return (name, meta) if meta is not None else svc.toast("That take is gone.")


def _lane_refused(svc) -> Response | None:
    """The toast for a post that writes the edited lane's notes while that
    lane follows the main one (its notes are the main lane's), else None."""
    s: StudioState = svc.state
    if s.follows(s.active_layer):
        return svc.toast("This lane follows the main one: “draw its own” first.")
    return None


@router.post("/take/keys/keep")
def take_keep(request: Request, form: StrForm, svc: Svc) -> Response:
    """★ Keep: the take is named (the prompt; blank keeps its name) and
    kept — the strip lets it go, the clip library files it under Takes.
    A rename moves every reference to the clip, as the library's does
    (`StudioState.rename_clip_everywhere`), and its notes' sidecar with it.
    The meta's one rewrite, so a take from before the sidecar has its
    notes moved out of it here (`keytakes.migrate`)."""
    s: StudioState = svc.state
    got = _take_or_toast(svc, form)
    if isinstance(got, Response):
        return got
    name, meta = got
    raw = (prompt_value(request) or form.get("new", "")).strip()
    new = clip_safe(raw) if raw else name
    if new != name and not s.rename_clip_everywhere(name, new):
        return svc.toast(f"There is already a clip called “{new}”.")
    write_meta(s.data_dir, new, {**kt.migrate(s.data_dir, new, meta), "kept": True})
    return with_toast(HTMLResponse(""), f"Kept “{new}”: Clips → Takes.")


@router.post("/take/keys/discard")
def take_discard(request: Request, form: StrForm, svc: Svc) -> Response:
    """✕: the take's clip is deleted (a kept one's button asks first)."""
    got = _take_or_toast(svc, form)
    if isinstance(got, Response):
        return got
    delete_clip(svc.state.data_dir, got[0])
    return HTMLResponse("")


@router.post("/take/keys/clear")
def take_clear(request: Request, svc: Svc) -> Response:
    """Clear takes: every unkept take is deleted — the one way the strip is
    pruned (nothing goes by itself)."""
    s: StudioState = svc.state
    gone = [t["name"] for t in kt.takes(s.data_dir) if not t["kept"]]
    for name in gone:
        delete_clip(s.data_dir, name)
    return with_toast(HTMLResponse(""), f"Cleared {plural(len(gone), 'unkept take')}.")


def _play_sound(s: StudioState, sound, label: str) -> bool:
    """The sound a take or a loop was made with, live on the edited lane
    (with its other lanes, FX and curves: `load_sound`), one sound undo
    step; False when there is none to load."""
    if sound is None:
        return False
    before = s.patch_snapshot()
    s.load_sound(*sound, label=label)
    s.push_undo_step(before)
    return True


def _adopt(arr, bpm, key) -> tuple:
    """`arr` at a take's or a loop's tempo and key: (arr, a word on it)."""
    words = []
    lo, hi = limits.BPM_RANGE
    if isinstance(bpm, int | float) and lo <= bpm <= hi and bpm != arr.bpm:
        arr = replace(arr, bpm=float(bpm))
        words.append(f"{bpm:g} bpm")
    if key and (key[0], key[1]) != (arr.root, arr.scale_name):
        arr = replace(arr, root=key[0], scale_name=key[1])
        words.append(f"{key[0].rstrip('-0123456789')} {key[1].replace('_', ' ')}")
    return arr, " · ".join(words)


@router.post("/take/keys/notes")
@pattern_edit
def take_notes(request: Request, form: StrForm, svc: Svc) -> Response:
    """→ Perform as notes: the take as it was played — its notes onto the
    note grid of the lane being edited (`keytakes.to_grid`: quantised to
    its step, over the take's length, the grid as long as the take) AND the
    sound that played it (the whole stack it kept, `keytakes.load_sound`),
    its bpm and key adopted when the grid had no notes. The notes are one
    pattern undo step (Ctrl+Z on Perform), the sound one sound undo step;
    the page lands on Perform, its grid showing them."""
    s: StudioState = svc.state
    got = _take_or_toast(svc, form)
    if isinstance(got, Response):
        return got
    name, meta = got
    if refused := _lane_refused(svc):
        return refused
    notes = kt.read_notes(s.data_dir, name, meta)["notes"]
    if not notes:
        return svc.toast("That take has no notes: play the keys while it records.")
    empty = not s.arrangement.cells
    sounded = _play_sound(s, kt.load_sound(s.data_dir, meta.get("sound")), name)
    try:
        arr, span, octave, said = kt.to_grid(
            s.arrangement, name, notes, kt.bars_of(meta), s.grid_span, s.grid_oct
        )
    except kt.TakeRefused as err:
        return svc.toast(str(err))
    if empty:
        arr, word = _adopt(arr, meta.get("bpm"), kt.key_of(meta))
        said = said.rstrip(".") + (f" · {word}, the take's." if word else ".")
    s.arrangement = arr
    s.grid_span, s.grid_oct = span, octave
    s.end_loop_edit()  # the grid holds the take now, not a saved loop
    s.save()
    if sounded:
        said = said.rstrip(".") + ", in the sound that played it."
    return svc.land_on(request, "perform", f"{said} Ctrl+Z takes the notes back.")


# --- ✦ Clean up ----------------------------------------------------------------


@router.post("/take/keys/clean")
@render_outside_lock
def take_clean(request: Request, form: StrForm, svc: Svc) -> Response | OutsideLock:
    """✦ Clean up a take's notes: the Song take's clean-up
    (`keytakes.clean` → `takeclean.clean`: the lean, the 1/16 grid,
    doubles, velocities, the take's key) and — `ai=1` with a Gemini key —
    `takecoach.coach`'s edits (`takeclean.apply_edits`), the call outside
    the lock. Always from the notes as played (`raw`, kept on the first
    clean-up), so it may be run again; ↶ (`/take/keys/unclean`) puts them
    back. The audio is the take as played: only the notes are cleaned, in
    their sidecar."""
    s: StudioState = svc.state
    got = _take_or_toast(svc, form)
    if isinstance(got, Response):
        return got
    name, meta = got
    doc = kt.read_notes(s.data_dir, name, meta)
    raw = doc["raw"] if "raw" in doc else doc["notes"]
    if not raw:
        return svc.toast("That take has no notes to clean up.")
    bpm = meta.get("bpm") if kt.meta_number(meta.get("bpm")) else s.arrangement.bpm
    bpm = min(max(float(bpm), limits.BPM_RANGE[0]), limits.BPM_RANGE[1])
    root, scale = kt.key_of(meta) or (s.arrangement.root, s.arrangement.scale_name)
    bars = kt.bars_of(meta) or max(n[0] + n[1] for n in raw) / 4
    notes, cleaned = kt.clean(raw, bpm, bars, root, scale)
    config = takecoach.load_config()
    why_not = takecoach.available(config) if form.get("ai", "1") == "1" else "off"

    def commit(notes: list, fixes: list) -> Response:
        if kt.read_meta(s.data_dir, name) is None:
            return svc.toast("That take went while it was cleaned up.")
        kt.write_notes(s.data_dir, name, {"notes": notes, "raw": raw, "fixes": fixes})
        _place_take_loop(s, name, notes, 100)
        return with_toast(HTMLResponse(""), " ".join(fixes) or "Nothing to clean up.")

    if why_not is not None:
        return commit(notes, cleaned.fixes)
    info = {
        "kind": "notes",
        "title": name,
        "style": "",
        "bpm": bpm,
        "key": takeclean.key_name(root, scale),
        "section": "a take on the keys",
        "others": ["the loop"],
        "track": "keys",
        "bars": cleaned.pattern["bars"],
        "steps": kt.CLEAN_STEPS,
        "grid": takeclean.grid_text(cleaned.pattern, "notes", kt.CLEAN_STEPS),
        "fixes": cleaned.fixes,
        "stats": cleaned.stats,
        "pattern": name,
    }

    def run() -> Response:
        fixes, out = list(cleaned.fixes), notes
        try:
            got = takecoach.coach(info, config=config)
        except AgentError as err:
            fixes.append(f"(Gemini: {err})")
        else:
            pattern = copy.deepcopy(cleaned.pattern)
            done = takeclean.apply_edits(
                pattern,
                "notes",
                got["edits"],
                steps=kt.CLEAN_STEPS,
                root=root,
                scale_name=scale,
            )
            out = kt.edited_notes(notes, pattern, cleaned.grid)
            fixes += [f"✦ {x}." for x in done] + [str(f) for f in got["feedback"]]
        with s.lock:
            return commit(out, fixes)

    return OutsideLock(run)


@router.post("/take/keys/unclean")
def take_unclean(request: Request, form: StrForm, svc: Svc) -> Response:
    """↶ the clean-up: the take's notes as they were played."""
    s: StudioState = svc.state
    got = _take_or_toast(svc, form)
    if isinstance(got, Response):
        return got
    name, meta = got
    doc = kt.read_notes(s.data_dir, name, meta)
    if "raw" not in doc:
        return svc.toast("That take has no clean-up to undo.")
    kt.write_notes(s.data_dir, name, {"notes": doc["raw"]})
    _place_take_loop(s, name, doc["raw"], None)
    return with_toast(HTMLResponse(""), f"“{name}”: the notes as played, back.")


def _place_take_loop(s: StudioState, take: str, notes: list, strength) -> None:
    """The loop whose audio `take` is, placed again from `notes` — the
    ✦ clean-up's (on the grid as they came: `strength` 100) or, after ↶,
    the notes as played at the loop's own strength (`None`). Its notes as
    played stay as they were; a loop open on the grid follows there, one
    pattern undo step."""
    name = loops.of_take(s.data_dir, take)
    loop = loops.load(s.data_dir, name) if name else None
    if loop is None:
        return
    lane0 = loop["lane"]
    q = loop["quantise"] if strength is None else strength
    lane = loops.place(notes, loop["meter"], lane0["bars"], lane0["steps_per_bar"], q)
    loops.update(s.data_dir, name, arr=lane, quantise=q, edited=False)
    e = s.loop_edit_of()
    if e is not None and e["name"] == name:
        before = s.pattern_snapshot()
        s.arrangement = replace(s.arrangement, **lane)
        s.push_pattern_undo(before)
        s.loop_edit_synced()
        s.save()


# --- loops (loops.py) -----------------------------------------------------------------


@router.post("/loop/save")
def loop_save(request: Request, form: StrForm, svc: Svc) -> Response:
    """Save as loop: `name` (a keys take: its notes on a fresh 1/16 grid,
    its sound, bpm and key) or `lane=1` (Perform's edited lane as it is:
    its grid, the sound, the loop's bpm and key), `bars` 1, 2 or 4 (a
    take's notes past them are dropped; unasked, a take's own length — the
    first choice that holds it), named by the prompt (or `loop`)."""
    s: StudioState = svc.state
    try:
        bars = int(form.get("bars", "") or 0)
    except ValueError:
        bars = 0
    asked = (prompt_value(request) or form.get("loop", "")).strip()
    if form.get("lane") == "1":
        if bars not in loops.BAR_CHOICES:
            bars = s.arrangement.bars
        arr = replace(s.arrangement, bars=bars)
        sound = s.sound_payload()
        lane = {"cells": arr.cells, "cuts": arr.cuts}
        key = {"bpm": arr.bpm, "root": arr.root, "scale_name": arr.scale_name}
        asked = asked or loops.title(s.patch.name, lane, key, bars)
    else:
        got = _take_or_toast(svc, form)
        if isinstance(got, Response):
            return got
        name, meta = got
        if bars not in loops.BAR_CHOICES:
            want = round(kt.bars_of(meta) or 1)
            bars = next((b for b in loops.BAR_CHOICES if b >= want), 4)
        key = kt.key_of(meta) or (s.arrangement.root, s.arrangement.scale_name)
        fresh = Arrangement(
            bpm=s.arrangement.bpm,
            root=key[0],
            scale_name=key[1],
            bars=bars,
            steps_per_bar=kt.CLEAN_STEPS,
            cells=[],
        )
        fresh, _word = _adopt(fresh, meta.get("bpm"), None)
        notes = kt.read_notes(s.data_dir, name, meta)["notes"]
        try:
            arr, *_ = kt.to_grid(fresh, name, notes, bars, 3, 0)
        except kt.TakeRefused as err:
            return svc.toast(str(err))
        sound = kt.sound_payload(s.data_dir, meta.get("sound")) or s.sound_payload()
        asked = asked or name
    try:
        kept = loops.save(s.data_dir, asked, arr, sound)
    except OSError as err:
        return svc.toast(str(err))
    said = f"Saved the loop “{kept}”: {plural(bars, 'bar')}."
    if form.get("lane") == "1" and bars == s.arrangement.bars:
        # the grid IS the loop now: what is drawn next saves to it
        s.open_loop_edit(kept)
        said += " The grid edits it now: every change saves to it."
    return with_toast(HTMLResponse(""), said)


# an idea (a scene of Perform's launcher kept) is no lane of notes
_IDEA = "An idea opens as a scene: Open it in the Loops panel."


def _loop(svc, form: StrForm):
    return loops.load(svc.state.data_dir, form.get("name", "").strip())


def _loop_lane(s: StudioState, loop: dict) -> tuple[Arrangement, str]:
    """The loop arrangement with `loop` on the edited lane — its grid, its
    tempo and key — and the words for what moved (`_adopt`). Nothing in
    the session is touched."""
    arr = replace(s.arrangement, **loop["lane"])
    key = loop["key"]
    return _adopt(arr, key["bpm"], (key["root"], key["scale_name"]))


@router.post("/loop/load")
@pattern_edit
def loop_load(request: Request, form: StrForm, svc: Svc) -> Response:
    """→ Perform: the loop into the lane being edited — its notes (one
    pattern undo step), its sound with its lanes (one sound undo step), its
    bpm and key — landing on Perform."""
    s: StudioState = svc.state
    loop = _loop(svc, form)
    if loop is None:
        return svc.toast("That loop is gone.")
    if loop["kind"] == "idea":
        return svc.toast(_IDEA)
    if refused := _lane_refused(svc):
        return refused
    _play_sound(s, loop["sound"], loop["name"])
    s.arrangement, word = _loop_lane(s, loop)
    s.show_notes()  # the roll's window on its notes, as Edit's
    s.end_loop_edit()  # a copy on the grid: edits stay the working loop's
    s.save()
    said = f"“{_title(s, loop['name'])}” is on the grid" + (
        f" · {word}" if word else ""
    )
    return svc.land_on(request, "perform", f"{said}. Ctrl+Z takes the notes back.")


@router.post("/loop/edit")
def loop_edit(request: Request, form: StrForm, svc: Svc) -> Response:
    """Edit: the loop on Perform's note grid AS THE LOOP — its notes on the
    edited lane, the sound that plays it (with its layers: one sound undo
    step), its tempo, key and meter (the loop's other grids fitted to the
    meter, `set_meter`) — one pattern undo step, which is the edit's mark
    (`StudioState.open_loop_edit`). From then on every request that moves
    the grid's notes writes them back to the loop (`mirror_loop_edit`,
    routing): it saves as it goes, and can be opened and edited again
    later. It ends at Done (`/loop/edit/done`), another loop, take or
    section put on the grid, or Ctrl+Z past this step (the grid is then the
    working loop's as it was; the loop keeps every edit made). The grid's
    octave window moves to where the notes are. Lands on Perform."""
    s: StudioState = svc.state
    got = _loop_named(svc, form)
    if isinstance(got, Response):
        return got
    loop = loops.load(s.data_dir, got)
    if loop is None:
        return svc.toast("That loop is gone.")
    if loop["kind"] == "idea":
        return svc.toast(_IDEA)
    if loop["kind"] == "beat":  # a beat opens on the drum grid
        return loop_beat(request=request, form=form, svc=svc)
    if refused := _lane_refused(svc):
        return refused
    a = s.arrangement
    asked, word = _loop_lane(s, loop)
    if refusal := loop_refusal(a, replace(asked, meter=loop["meter"])):
        return svc.toast(refusal)
    before = s.pattern_snapshot()
    if loop["meter"] != a.meter:
        s.set_meter(loop["meter"])  # the beat and the other lanes follow
        asked, word = _loop_lane(s, loop)
    _play_sound(s, loop["sound"], loop["name"])
    s.arrangement = asked
    s.show_notes()
    s.push_pattern_undo(before)
    s.open_loop_edit(loop["name"])
    s.save()
    said = f"Editing the loop “{_title(s, loop['name'])}”: every change saves to it"
    said += f" · {word}" if word else ""
    return svc.land_on(request, "perform", said + ". Done ends it; Ctrl+Z too.")


@router.post("/loop/edit/done")
def loop_edit_done(request: Request, form: StrForm, svc: Svc) -> Response:
    """Done: the grid stops editing the saved loop — it keeps every edit
    made, and the notes stay on the grid as the working loop's."""
    name = svc.state.end_loop_edit()
    if name is None:
        return svc.toast("No saved loop is open on the grid.")
    return with_toast(
        HTMLResponse(""),
        f"“{_title(svc.state, name)}” is saved. The grid is the working loop again.",
    )


@router.post("/loop/quantise")
@pattern_edit
def loop_quantise(request: Request, form: StrForm, svc: Svc) -> Response:
    """Quantise: the loop's notes as played placed on its grid again
    (`loops.place`) at `steps` a bar (the meter's step choices: the rate)
    and `strength` 0-100 — 0 "as played", each note in the step it began
    in; 100 every note on its nearest step. Never destructive: the notes as
    played stay as they were, and placing again starts from them (an edit
    made on the grid is replaced: the button says so first). A loop open on
    the grid is placed there too, one pattern undo step."""
    s: StudioState = svc.state
    got = _loop_named(svc, form)
    if isinstance(got, Response):
        return got
    loop = loops.load(s.data_dir, got)
    if loop is None:
        return svc.toast("That loop is gone.")
    if not loop["played"]:
        return svc.toast("That loop has no notes as played to place again.")
    m = loop["meter"]
    try:
        steps = int(form.get("steps") or loop["lane"]["steps_per_bar"])
        strength = int(form.get("strength") or loop["quantise"])
    except ValueError:
        return svc.toast("Quantise needs a rate and a strength.")
    if steps not in _meter.step_choices(m) or not 0 <= strength <= 100:
        return svc.toast("Quantise needs a rate and a strength.")
    if loop["free"] is not None:  # free record's: its bars as shaped, its timing kept
        bars = loop["lane"]["bars"]
        if bars * steps > limits.MAX_GRID_STEPS:
            return svc.toast("Quantise needs a rate and a strength.")
        swing = loop["free"].get("swing") or 0.0
        lane = loops.place_free(loop["played"], m, bars, steps, strength, swing)
    else:
        took = max(n[0] + n[1] for n in loop["played"]) / _meter.bar_beats(m)
        bars = loops.grid_bars(max(loop["lane"]["bars"], math.ceil(took - 1e-6)), steps)
        lane = loops.place(loop["played"], m, bars, steps, strength)
    lane.setdefault("note_nudge", {})  # (placed on the steps: no timing inside one)
    loops.update(s.data_dir, got, arr=lane, quantise=strength, edited=False)
    e = s.loop_edit_of()
    if e is not None and e["name"] == loop["name"]:
        s.arrangement = replace(s.arrangement, **lane)
        s.loop_edit_synced()
        s.save()
    rate = loops.rate_word(m, steps)
    how = "as played" if strength == 0 else f"quantise {strength}%"
    return with_toast(HTMLResponse(""), f"“{_title(s, got)}”: {rate}, {how}.")


def _loop_lanes(s: StudioState, loop: dict, arr: dict) -> list[dict]:
    """A saved loop's note lanes as the song takes a loop's (`state
    ._loop_lanes`' shape): the main lane — its notes in the loop's sound,
    with the main lane's FX and curves — then each other lane of its sound
    that plays (a following one as the notes it plays). No sound kept: the
    session's patch plays it."""
    sound = loop["sound"]
    patch, extras = sound if sound else (s.patch, None)
    extras = extras or {}
    main = song_bridge.main_lane(
        patch.to_json(),
        arr,
        {"fx": extras.get("main_fx") or [], "gain": 1.0},
        extras.get("curves") or {},
    )
    grid = Arrangement(**schema.conform(arr, schema.nodes().ARRANGEMENT))
    others = [materialise(lay, grid) for lay in extras.get("layers") or []]
    return [
        main,
        *(
            song_bridge.layer_lane(lay)
            for lay in others
            if lay.get("patch") and lay.get("cells")
        ),
    ]


def _beat_arr(s: StudioState, pattern: dict, bpm: float) -> Arrangement:
    """The working loop with `pattern` (a drum PATTERN) on its drum lane at
    `bpm`: what a beat sounds like through the session's kit. Nothing in
    the session is touched."""
    lane = s.pattern_fields(pattern)
    return replace(s.arrangement, **lane, drum_vel={}, bpm=bpm)


def _render_loop(svc, loop: dict, bpm: float | None = None):
    """A loop rendered as it is — a loop of notes: its synths, on its
    clock, key and sound (`svc.render("perform", arrangement=, parts=
    ("synth",))`); a beat: the Drums view's render of it through the
    session's kit — at its own tempo, or `bpm`. The `RenderResult`, or the
    toast."""
    s: StudioState = svc.state
    own = loop["key"]["bpm"]
    if loop["kind"] == "idea":
        return svc.toast(_IDEA)
    try:
        if loop["kind"] == "beat":
            return svc.render(
                "drums", arrangement=_beat_arr(s, loop["beat"], bpm or own)
            )
        arr, _word = _loop_lane(s, loop)
        sound = loop["sound"]
        heard = {"patch": sound[0], "chain": sound[1], "mix": True} if sound else {}
        arr = replace(arr, bpm=bpm or own)
        return svc.render("perform", arrangement=arr, parts=("synth",), **heard)
    except RenderError as err:
        return svc.toast(str(err))


def _bounce(svc, loop: dict) -> str | Response:
    """The loop rendered into a clip (`loop_bounce`, `_render_loop`): the
    clip's name, or the toast."""
    s: StudioState = svc.state
    made = _render_loop(svc, loop)
    if isinstance(made, Response):
        return made
    bars = loop["beat"]["bars"] if loop["kind"] == "beat" else loop["lane"]["bars"]
    kept = free_clip_name(s.data_dir, loop["name"])
    meta = {"bpm": loop["key"]["bpm"], "bars": bars, "source": f"loop: {loop['name']}"}
    save_clip(s.data_dir, kept, made.audio(), meta=meta)
    return kept


@router.post("/loop/song")
def loop_song(request: Request, form: StrForm, svc: Svc) -> Response:
    """→ Song: the loop as a new last section of its bars — `as=pattern`
    (the default): its notes a pool pattern on a synth track that plays its
    sound (its other lanes too; a track already playing that sound is
    reused: `song_bridge.add_lanes_section`), a beat its pattern on the
    drums track; `as=audio`: its audio (the take; a loop with none is
    bounced first, and the clip is its audio from then on) as a take from
    the section's first bar on an audio track. The first loop into an empty
    song sets its tempo and key. One song undo step.

    Where (a drag from the drawer — songdrag.js): `section_id` +
    `track_id` (+ `at_bar`, the bar of the pass under the pointer) puts its
    main lane — or a beat — on that lane as a pattern, played by the
    track's own sound (from that bar to the section's end; bar 0 fills
    it); `at_section` makes the new section there in the running order.
    Asked, not refused (`tosong.Ask`): past the 8 synth tracks, which track
    takes a lane (`onto`); and a plain → Song that the song holds already,
    whether to place it again (`again`)."""
    s: StudioState = svc.state
    got = _loop_named(svc, form)
    if isinstance(got, Response):
        return got
    loop = loops.load(s.data_dir, got)
    if loop is None:
        return svc.toast("That loop is gone.")
    if loop["kind"] == "idea":
        return svc.toast(_IDEA)
    audio = form.get("as") == "audio"
    name, beat = loop["name"], loop["kind"] == "beat"
    title = looplib.titles(s.data_dir).get(name, name)
    lane_drop = bool(form.get("track_id")) and not audio
    at_section = _int_or_none(form.get("at_section"))
    # the loop as an arrangement, conformed as the song keeps one
    arr = tosong.loop_arr(loop)
    clip = loop["take"] if audio else None
    if audio and not (clip and clips_exist(s, clip)):
        made = _bounce(svc, loop)
        if isinstance(made, Response):
            return made
        loops.update(s.data_dir, name, take=made)  # its audio from now on
        clip = made
    said: list[str] = []

    def change(song: dict) -> None:
        if not song["sections"]:
            song |= tosong.song_wide(arr)
        index = song_bridge.pattern_index(song)
        try:
            if lane_drop:
                said.append(_onto_lane(s, song, index, form, loop, arr, title))
                return
            if audio:
                bars = loop["beat"]["bars"] if beat else arr["bars"]
                sid = song_bridge.add_audio_section(
                    song, name, bars, loop["meter"], clip
                )
                if at_section is not None:
                    sm.move_section(song, sid, at_section)
                how = "as audio on an audio track"
            elif beat:
                sid = song_bridge.add_scene_section(
                    song,
                    index,
                    name,
                    [],
                    loop["beat"],
                    # a single loop takes the song's tempo, as notes do
                    **_scene_clock(s, loop["meter"], song["bpm"]),
                    at=at_section,
                )
                how = "as a beat on the drums track"
            else:
                lanes = _loop_lanes(s, loop, arr)
                sid = song_bridge.add_lanes_section(
                    song,
                    index,
                    name,
                    arr,
                    lanes,
                    s.curves,
                    onto=tosong.onto_of(form),
                    at=at_section,
                )
                how = "as a pattern on a track with its sound"
        except song_bridge.TrackCap as err:
            raise tosong.cap_ask(song, err, form) from None
        plain = at_section is None and form.get("again") != "1"
        if plain and (twin := tosong.already(song, sid)) is not None:
            raise tosong.already_ask(song, twin, title, form)
        n = sm.section_index(song, sid) + 1
        bars = plural(song["sections"][n - 1]["bars"], "bar")
        said.append(f"Added “{title}” to the song {how}: section {n}, {bars}.")

    where = "on a lane of" if lane_drop else "to"
    try:
        resp = _commit(svc, change, label=f"add loop “{title}” {where} the song")
    except tosong.Ask as ask:
        return tosong.ask_response("/loop/song", ask)
    return with_toast(resp, said[0]) if resp.status_code == 200 and said else resp


def _int_or_none(raw) -> int | None:
    raw = str(raw or "").strip()
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 4 else None


def _scene_clock(s: StudioState, meter: str, bpm: float) -> dict:
    """A section's clock and kit for `add_scene_section`: `meter`, `bpm`,
    the session's kit and drum bus (what plays a beat on Perform)."""
    return {
        "meter": meter,
        "bpm": bpm,
        "kit": asdict(s.arrangement),
        "fx_drums": s.fx_drums,
        "curves": s.curves,
    }


def _onto_lane(s, song, index, form, loop, arr, title) -> str:
    """A loop dropped on a lane (`section_id`, `track_id`, `at_bar`): its
    main lane's notes — or its beat — a pool pattern in that cell, from
    the bar under the pointer to the section's end, in the track's own
    sound. What the toast says."""
    sid, tid = form.get("section_id", ""), form.get("track_id", "")
    i, j = sm.section_index(song, sid), sm.track_index(song, tid)
    if i is None or j is None:
        raise sm.SongError("That lane is gone.")
    track = song["tracks"][j]
    beat = loop["kind"] == "beat"
    if track["kind"] != ("drums" if beat else "synth"):
        what = "A beat goes on the drums" if beat else "Notes go on a synth lane"
        raise sm.SongError(f"{what}, not on {track['name']}.")
    meter = loop["meter"]
    if beat:
        data = song_bridge.beat_data(loop["beat"])
        kind, own = "drums", f"{loop['name']} beat"
    else:
        data = song_bridge.note_data(arr, arr["cells"])
        kind, own = "notes", f"{loop['name']} {track['name']}"
    if meter != "4/4":
        data["meter"] = meter
    pid = song_bridge.intern_pattern(song, index, kind, own, data)
    bar = _int_or_none(form.get("at_bar")) or 0
    at = tosong.place_on_lane(song, sid, tid, bar, pid)
    sec = song["sections"][i]
    where = f"from bar {at + 1} of" if at else "in"
    plays = (track.get("patch") or {}).get("name") or "its sound"
    sound = "" if beat else f", played by its sound ({plays})"
    return f"“{title}” on {track['name']} {where} “{sec['name']}”{sound}."


@router.post("/loop/rename")
def loop_rename(request: Request, form: StrForm, svc: Svc) -> Response:
    """Rename a loop (the prompt, or `new`) — or a take no loop names yet
    (`take=`: its loop is made first). The clip of its audio keeps its own
    name; a loop open on the grid stays open under the new one."""
    s: StudioState = svc.state
    new = (prompt_value(request) or form.get("new", "")).strip()
    if not new:
        return svc.toast("A loop needs a name.")
    old = _loop_named(svc, form)
    if isinstance(old, Response):
        return old
    kept = loops.rename(s.data_dir, old, new)
    if kept is None:
        return svc.toast(f"There is already a loop called “{new}”.")
    s.rename_loop_edits(old, kept)
    s.slots_renamed(old, kept)  # Perform's launcher holds it by its name
    return HTMLResponse("")


@router.post("/loop/delete")
def loop_delete(request: Request, form: StrForm, svc: Svc) -> Response:
    """✕: the loop is deleted — with `audio=1` (the Loops list's ✕) its
    audio too, the take clip, unless the song or the loop uses that clip
    (it then stays in the clip library, and the toast says so). A loop
    open on the grid stops being edited; its notes stay on the grid."""
    s: StudioState = svc.state
    name = loops.loop_safe(form.get("name", "").strip())
    loop = loops.load(s.data_dir, name)
    if loop is None or not loops.delete(s.data_dir, name):
        return svc.toast("That loop is gone.")
    s.end_loop_edits(name)
    s.slots_renamed(name, None)  # its slots in Perform's launcher are empty now
    take = loop["take"]
    if form.get("audio") != "1" or not take or not clips_exist(s, take):
        return HTMLResponse("")
    if take in s.clip_usage():
        return with_toast(
            HTMLResponse(""),
            f"Deleted the loop “{name}”. Its audio, “{take}”, is in use: it stays "
            "in the clip library.",
        )
    delete_clip(s.data_dir, take)
    return HTMLResponse("")


def clips_exist(s: StudioState, name: str) -> bool:
    return (clip_dir(s.data_dir) / f"{clip_safe(name)}.wav").is_file()


@router.post("/loop/bounce")
def loop_bounce(request: Request, form: StrForm, svc: Svc) -> Response:
    """⤓ Bounce to audio: the loop's synths — its notes on its clock, tempo
    and key, played by the sound it kept with its layers (the session's
    when it kept none) — through the one render entry
    (`svc.render("perform", arrangement=, parts=("synth",))`: Perform's
    render as it would be with the loop on the grid, the drums left out)
    into the clip store as `<loop name>` (never over a clip), with its bpm
    and bars. The session is not touched — the loop is not put on the grid
    — so the page stays where it is and the clip library comes back by its
    watch."""
    loop = _loop(svc, form)
    if loop is None:
        return svc.toast("That loop is gone.")
    kept = _bounce(svc, loop)
    if isinstance(kept, Response):
        return kept
    return with_toast(
        HTMLResponse(""), f"Bounced the loop “{loop['name']}” to the clip “{kept}”."
    )


# --- the Loops panel: ★, tags, ▶ in time, beats --------------------------------------


def _title(s: StudioState, name: str) -> str:
    """What the lists call loop `name` (`looplib.titles`: a name the clock
    gave shows what the loop is)."""
    return looplib.titles(s.data_dir).get(name, name)


@router.post("/loop/star")
def loop_star(request: Request, form: StrForm, svc: Svc) -> Response:
    """★: a loop is a favourite, or no longer (the panel's ★ filter lists
    them). A take or a bank slot is kept as a loop first (`_loop_named`)."""
    s: StudioState = svc.state
    got = _loop_named(svc, form)
    if isinstance(got, Response):
        return got
    loop = loops.load(s.data_dir, got)
    if loop is None:
        return svc.toast("That loop is gone.")
    loops.update(s.data_dir, got, star=not loop["star"])
    return HTMLResponse("")


@router.post("/loop/tag")
def loop_tag(request: Request, form: StrForm, svc: Svc) -> Response:
    """# tags: the loop's tags are the words of the prompt (or `tags`) —
    lower case, no "#", each once (`loops.clean_tags`); none clears them.
    A take or a bank slot is kept as a loop first."""
    s: StudioState = svc.state
    got = _loop_named(svc, form)
    if isinstance(got, Response):
        return got
    words = prompt_value(request) or form.get("tags", "")
    loops.update(s.data_dir, got, tags=loops.clean_tags(words))
    return HTMLResponse("")


@router.post("/loop/preview")
def loop_preview(request: Request, form: StrForm, svc: Svc) -> Response:
    """▶ in time (loopspanel.js): the audio a row plays, named by
    `X-Preview-Url`, which the page starts on the next bar line of what
    plays. `bpm`: the tempo of what plays (none when nothing does). At the
    loop's own tempo — or with nothing playing — its audio: the take, or,
    for a loop that has none, a render of it made now and kept as its
    audio from then on (`_bounce`: the clip library holds it, the row's ▶
    plays it next time without asking). At another tempo, a render of its
    notes (or its beat) at that tempo, from the render cache
    (`/preview/{key}.wav`): in time and in tune, where the take's audio
    would drift or, sped up, change key. A bank slot (`slot`) is rendered
    through the session's kit at the tempo asked, or the loop's."""
    s: StudioState = svc.state
    bpm = _number(form.get("bpm", ""), *limits.BPM_RANGE)
    if _banked(form):
        pattern = _bank_slot(svc, form)
        if pattern is None:
            return svc.toast("That bank slot is empty.")
        try:
            made = svc.render(
                "drums", arrangement=_beat_arr(s, pattern, bpm or s.arrangement.bpm)
            )
        except RenderError as err:
            return svc.toast(str(err))
        return HTMLResponse("", headers={"X-Preview-Url": f"/preview/{made.key}.wav"})
    got = _loop_named(svc, form)
    if isinstance(got, Response):
        return got
    loop = loops.load(s.data_dir, got)
    if loop is None:
        return svc.toast("That loop is gone.")
    if bpm is None or abs(bpm - loop["key"]["bpm"]) < 0.01:
        clip = loop["take"]
        if not (clip and clips_exist(s, clip)):
            made = _bounce(svc, loop)
            if isinstance(made, Response):
                return made
            loops.update(s.data_dir, got, take=made)  # its audio from now on
            clip = made
        url = f"/clip/{quote(clip_safe(clip))}.wav"
    else:
        made = _render_loop(svc, loop, bpm)
        if isinstance(made, Response):
            return made
        url = f"/preview/{made.key}.wav"
    return HTMLResponse("", headers={"X-Preview-Url": url})


@router.post("/loop/beat")
@pattern_edit
def loop_beat(request: Request, form: StrForm, svc: Svc) -> Response:
    """Open a beat on the drum grid: a beat loop (`name`) or a Drums bank
    slot (`slot`, loaded as the bank loads one: the active slot follows) —
    the drum lane only, the notes, sound and tempo stay; one pattern undo
    step. On Perform it takes over at the loop's wrap, as a launch does;
    from another page it lands on Perform."""
    s: StudioState = svc.state
    if form.get("lib", "").strip() and not form.get("slot", "").strip():
        # a beat of the old beat library (the Kit page does not list it)
        pattern = s.load_drum_pattern(form.get("lib", "").strip())
        if pattern is None:
            return svc.toast("That beat is gone.")
        s.end_loop_edit(s.DRUM_LANE)  # the library's beat now, not a loop's
        said = pattern.get("name") or "the beat"
    elif form.get("slot", "").strip():
        pattern = _bank_slot(svc, form)
        if pattern is None or not s.bank_load(int(form.get("slot"))):
            return svc.toast("That bank slot is empty.")
        said = pattern.get("name") or "the beat"
    else:
        got = _loop_named(svc, form)
        if isinstance(got, Response):
            return got
        loop = loops.load(s.data_dir, got)
        if loop is None or loop["kind"] != "beat":
            return svc.toast("That beat is gone.")
        before = s.pattern_snapshot()
        s.apply_pattern(loop["beat"])
        s.active_slot = None
        # (open on the drum lane — an edit of the beat saves to it,
        # as a loop's of notes does on its lane; the step undone ends it)
        s.push_pattern_undo(before)
        s.open_loop_edit(got, s.DRUM_LANE)
        said = _title(s, got)
    s.save()
    say = f"“{said}” is on the drum grid. Ctrl+Z takes it back."
    view = svc.view_of(request)
    # the Kit page has the grid too (its Beats ▸ opens beats here)
    if view not in ("perform", "drums"):
        return svc.land_on(request, "perform", say)
    try:
        trig = json.loads(svc.preview_trigger(view=view)["HX-Trigger"])
    except RenderError as err:
        return svc.toast(str(err))
    trig["preview-ready"]["queue"] = True  # at the wrap, as a launch
    trig["studio-toast"] = {"message": say}
    return HTMLResponse("", headers={"HX-Trigger": json.dumps(trig)})


@router.post("/loop/beat/keep")
def loop_beat_keep(request: Request, form: StrForm, svc: Svc) -> Response:
    """✚ Keep this beat (Perform's drum rack): the drum grid's beat as a
    beat loop in the Loops, named from what it is ("Techno beat · 124 ·
    1b": the bank slot it came from, else the generator's style) — or, the
    Kit page's ✚ Save beat (the page's only save), the
    `name` posted, "… 2" when a loop has it (never written over). A beat a
    loop already holds is not kept twice."""
    s: StudioState = svc.state
    a = s.arrangement
    if not a.drums:
        return svc.toast("The drum grid is empty: nothing to keep.")
    slot = s.active_slot
    held = s.drum_bank[slot] if slot is not None and slot < len(s.drum_bank) else None
    asked = form.get("name", "").strip()[: limits.MAX_NAME_CHARS]
    word = asked or (held or {}).get("name") or (s.beat_style or "beat").capitalize()
    pattern = s.pattern_from_arrangement(word)
    want = loops.beat_key(pattern)
    for row in loops.listing(s.data_dir):
        if row["beat_key"] == want:
            said = f"That beat is in the Loops already: “{row['title']}”."
            return with_toast(HTMLResponse(""), said)
    key = {"bpm": a.bpm, "root": a.root, "scale_name": a.scale_name}
    name = (
        loops.free_name(s.data_dir, loops.loop_safe(asked))
        if asked
        else loops.auto_name(s.data_dir, word, {}, key, a.drum_bars, beat=True)
    )
    try:
        kept = loops.save_beat(s.data_dir, name, pattern, a)
    except OSError as err:
        return svc.toast(str(err))
    return with_toast(HTMLResponse(""), f"Kept the beat “{kept}” in the Loops.")


@router.post("/take/keys/sample")
def take_sample(request: Request, form: StrForm, svc: Svc) -> Response:
    """→ Perform as a sample: the take's audio becomes the edited lane's
    sampler sound (the clip library's "⤷ sampler"), one undo step, landing
    on Perform: its grid plays it."""
    got = _take_or_toast(svc, form)
    if isinstance(got, Response):
        return got
    say = f"“{got[0]}” is the sampler sound: the note grid plays it."
    return use_clip(request, svc, got[0], say=say, land="perform")
