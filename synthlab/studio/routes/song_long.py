"""Long audio on the song: a whole stem of a Sample Lab source on an audio
track, under the arrangement (the model is `song_model`'s "long audio",
the files `longaudio.py`).

One route, `POST /song/long`, whose `op` is one song edit — add, set,
remove, tempo — through `routes/song.py`'s `_commit`: atomic, schema-clean,
one undo step, answered like every other song edit (`song-changed`, the
regions by their watches, a toast). The clip library's Stems group and the
Sample Lab's "→ Song track, whole" both post `op=add`."""

from __future__ import annotations

import math

from fastapi import Request, Response

from .. import limits, longaudio, sources
from .. import song_model as sm
from ..routing import StrForm, Svc, studio_router
from .song import _commit

router = studio_router()

OPS = ("add", "set", "remove", "tempo")


def _num(form: dict, key: str, what: str) -> float:
    try:
        v = float(form[key])
    except (KeyError, ValueError):
        raise sm.SongError(f"{what} is a number.") from None
    if not math.isfinite(v) or abs(v) > 1e9:
        raise sm.SongError(f"{what} is a number.")
    return v


def _take_tempo(song: dict, item: dict) -> str:
    """The song's tempo becomes the item's (to a thousandth: a whole stem
    drifts by tenths of a second on a rounded tempo). What to say."""
    bpm = item["bpm"]
    if not limits.BPM_RANGE[0] <= bpm <= limits.BPM_RANGE[1]:
        lo, hi = limits.BPM_RANGE
        if not bpm:
            raise sm.SongError("The Lab has no tempo for that song: set one there.")
        raise sm.SongError(f"{bpm:g} bpm is outside the song's {lo:g}-{hi:g}.")
    if abs(song["bpm"] - bpm) < 0.0005:
        return ""
    song["bpm"] = round(bpm, 3)
    return f" The song's tempo is now {bpm:g} bpm."


def _source_fields(svc, form: dict) -> dict:
    """What `op=add` names: stems of a source, as posted (`stems`,
    `levels`, `model`) or — `mix=lanes` — the Lab's ticked stems at the
    lanes' levels, of the stem set the source shows."""
    sid = form.get("source", "")
    meta = sources.read_meta(svc.data_dir, sid)
    if meta is None:
        raise sm.SongError("That song is not in the Lab any more.")
    model = form.get("model") or meta["stems"]["model"]
    if form.get("mix") == "lanes":
        model = meta["stems"]["model"]
        names = list(meta["picked"]) if model else []
        levels = sources.levels(meta)
    else:
        names = [s for s in form.get("stems", "").split(",") if s][:8]
        given = form.get("levels", "").split(",")
        levels = dict(zip(names, given, strict=False))
    return longaudio.from_source(svc.data_dir, sid, model, names, levels)


def _said(svc, song: dict, item_id: str) -> str:
    """Where a just-placed item plays, in bars."""
    rows = longaudio.rows(svc.data_dir, song)["items"]
    row = next(r for r in rows if r["id"] == item_id)
    if row["bars"] is None:
        total = sm.song_bars(song)
        if not total:
            return " The song has no sections yet: add some under it."
        return f" The song ends at bar {total}: it starts after that and is not heard."
    said = f" It plays bars {row['bars']}-{row['end_bar']}"
    if row["cut"]:
        return said + f" of its {row['own_bars']}: the song ends there."
    return said + "."


@router.post("/song/long")
def song_long(request: Request, form: StrForm, svc: Svc) -> Response:
    """One edit to the song's long audio. `op`:

    - `add`: `source` with `stems` / `levels` / `model`, or `mix=lanes`;
      `track_id` (default the first audio track, a new "Audio" when there
      is none); `section_id` + `at_bar` (0-based: that bar of that
      section's first pass; 1-based with `bar_base=1`, the Lab's form),
      or `bar` (1-based bar of the TIMELINE, as `set` takes it: a drop on
      the playlist, which may land on a repeat's pass), or none of them
      (the song's first bar); `tempo=on` (the song takes the stem's tempo).
    - `set` (`id`): any of `name`, `track_id`, `bar` (1-based), `section_id`
      (start at that section), `start` (seconds into the source), `bars`
      (how many song bars of it play; 0 or empty = to its end), `gain`,
      `mute` (on / off), `trim` (signed bars off its head).
    - `remove` (`id`); `tempo` (`id`): the song's tempo becomes the item's.

    A refusal is a 422 toast and changes nothing."""
    op = form.get("op", "")
    if op not in OPS:
        return Response(status_code=422)
    item_id = form.get("id", "")
    said: dict = {}

    def add(song):
        fields = _source_fields(svc, form)
        track = form.get("track_id", "")
        if not track:
            track = next((t["id"] for t in song["tracks"] if t["kind"] == "audio"), "")
        if not track:
            track = sm.add_track(song, "audio", "Audio")
        bar = 0
        if form.get("bar", "").strip():  # the timeline's own bar (a drop)
            bar = max(0, int(_num(form, "bar", "The bar")) - 1)
        elif form.get("section_id"):
            try:  # the Lab's form counts bars from 1 (`bar_base`)
                at = int(form.get("at_bar") or 0) - (form.get("bar_base") == "1")
            except ValueError:
                raise sm.SongError("The bar is a whole number.") from None
            bar = sm.section_bar(song, form["section_id"], at)
        new = sm.add_long(song, track, fields.pop("source"), at_bar=bar, **fields)
        item = sm._long(song, new)
        tempo = _take_tempo(song, item) if form.get("tempo") == "on" else ""
        said["label"] = "place stem"
        said["text"] = f"Placed {item['name']}." + _said(svc, song, new) + tempo

    def change(song):
        item = sm._long(song, item_id)
        fields: dict = {}
        if "name" in form:
            fields["name"] = form["name"].strip() or item["name"]
        if form.get("track_id"):
            fields["track_id"] = form["track_id"]
        if form.get("section_id"):
            fields["at_bar"] = sm.section_bar(song, form["section_id"])
        elif "bar" in form:
            fields["at_bar"] = int(_num(form, "bar", "The bar")) - 1
        if "start" in form:
            fields["start"] = _num(form, "start", "The start")
        if "bars" in form:
            n = _num(form, "bars", "The length") if form["bars"].strip() else 0.0
            fields["length"] = max(0.0, n) * 4 * 60.0 / song["bpm"]
        if "gain" in form:
            fields["gain"] = _num(form, "gain", "The level")
        if form.get("mute") in ("on", "off"):
            fields["mute"] = form["mute"] == "on"
        sm.set_long(song, item_id, **fields)
        if form.get("trim"):
            sm.trim_long(song, item_id, int(_num(form, "trim", "The trim")))
        said["label"] = "edit stem"

    def remove(song):
        said["text"] = f"Removed {sm._long(song, item_id)['name']} from the song."
        sm.remove_long(song, item_id)
        said["label"] = "remove stem"

    def tempo(song):
        said["text"] = _take_tempo(song, sm._long(song, item_id)).strip()
        said["label"] = "song tempo"

    def then():
        text = said.get("text")
        return {"studio-toast": {"message": text}} if text else None

    return _commit(
        svc,
        {"add": add, "set": change, "remove": remove, "tempo": tempo}[op],
        label=lambda: said.get("label", "stem"),
        # a dragged level posts many times a gesture
        key=f"long:{item_id}:gain" if op == "set" and "gain" in form else None,
        then=then,
    )
