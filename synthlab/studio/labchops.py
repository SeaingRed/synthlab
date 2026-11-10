"""Chops: named spans of one source, cut by markers on the Lab's waveform,
cleaned (`labclean.py`) and kept as clips.

    {"id": "c1a2", "name": "verse 1", "start": 8.512, "end": 12.38,
     "stems": ["vocals"], "model": "htdemucs", "levels": {"vocals": -3.0},
     "clean": {...}, "notes": "", "clip": ""}

- **Data in the source's meta** (`meta["chops"]`, in time order, never
  overlapping; `meta["chop"]` is the one the region was last set from),
  held to type and range by `sources.clean_meta` like the region and the
  view. The Lab still keeps no session state, no schema node and no render
  key: everything here changes a meta, through `sources.update_meta`.
- **Seconds, not bars**: a chop does not follow a later tempo correction
  (a region in bars does). The grid only snaps (`snap`).
- **A chop keeps its own mix**: `stems` of the set `model` at `levels` (dB,
  sparse) — the lanes' ticks and sliders when it was made, or when "use
  the lanes' mix" was last pressed. Its audition and its clip read that,
  never the lanes at the moment of pressing, so "Update clip" makes the
  same mix again. `stems` empty is the recording itself.
- **Edges** move by the sampler's slice rule (`sampler.nudge`): an edge
  is held inside its own chop, and a neighbour that *touched* it follows.

The functions that change a meta change it in place and say what they did
by what they return; the routes (`routes/lab.py`) are thin callers.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
import time
from pathlib import Path

import numpy as np

from .. import stems as stem_models
from ..core import SR
from . import cliplib, lab, labclean, limits, sources
from .clips import clip_dir

MIN = limits.MIN_CHOP_SECONDS
TOUCH = 5e-4  # seconds: two edges this close are one (a meta keeps 4 places)
SNAP_SHARE = 0.01  # of the span on screen: how near a beat line an edge snaps
RULES = ("bars", "beats", "transients", "syllables")
_WORDS = {"bars": "bar", "beats": "beat", "transients": "hit", "syllables": "syllable"}


def find(meta: dict, cid: str) -> dict | None:
    return next((c for c in meta["chops"] if c["id"] == cid), None)


def _new_id(taken) -> str:
    """A short random hex id no chop of the source has."""
    while True:
        cid = secrets.token_hex(2)
        if cid not in taken:
            return cid


def _free_name(meta: dict) -> str:
    names = {c["name"] for c in meta["chops"]}
    return next(
        f"chop {i}" for i in range(1, len(names) + 2) if f"chop {i}" not in names
    )


def lane_mix(meta: dict) -> dict:
    """The mix the lanes show, as a chop holds it: the ticked stems, the
    set they are of, and their levels that are not 0 dB."""
    model = meta["stems"]["model"]
    picked = list(meta["picked"]) if model else []
    lv = sources.levels(meta)
    return {
        "stems": picked,
        "model": model if picked else "",
        "levels": {s: lv[s] for s in picked if lv.get(s)},
    }


def _make(meta: dict, start: float, end: float, name: str, taken, mix=None) -> dict:
    return {
        "id": _new_id(taken),
        "name": name,
        "start": round(start, 4),
        "end": round(end, 4),
        **copy.deepcopy(mix or lane_mix(meta)),
        "clean": dict(labclean.DEFAULTS),
        "notes": "",
        "clip": "",
    }


def _put(meta: dict, chops: list[dict]) -> None:
    meta["chops"] = sorted(chops, key=lambda c: c["start"])
    if find(meta, meta.get("chop", "")) is None:
        meta["chop"] = ""


def _free_span(meta: dict, at: float) -> tuple[float, float] | None:
    """The gap between chops that holds `at`, or None inside a chop."""
    lo, hi = 0.0, meta["seconds"]
    for c in meta["chops"]:
        if c["start"] - TOUCH < at < c["end"] - TOUCH:
            return None
        if c["end"] <= at + TOUCH:
            lo = max(lo, c["end"])
        elif c["start"] > at:
            hi = min(hi, c["start"])
    return lo, hi


def add(meta: dict, start: float, end: float, stems=None, name: str = ""):
    """A new chop from `start` to `end` seconds, cut back to the gap
    between chops `start` is in. `stems` (of the set the source shows) is
    its mix; None takes the lanes'. None — nothing changed — when `start`
    is inside a chop, the gap is too short, or the source is full."""
    start = min(max(0.0, start), meta["seconds"])
    gap = _free_span(meta, start)
    if gap is None or len(meta["chops"]) >= limits.MAX_LAB_CHOPS:
        return None
    end = min(end, gap[1])
    if end - start < MIN:
        return None
    mix = None
    if stems is not None:
        model = meta["stems"]["model"]
        names = [s for s in sources.stem_names(model) if s in stems] if model else []
        mix = {"stems": names, "model": model if names else "", "levels": {}}
    taken = {c["id"] for c in meta["chops"]}
    chop = _make(meta, start, end, name or _free_name(meta), taken, mix)
    _put(meta, [*meta["chops"], chop])
    return chop


def split(meta: dict, at: float):
    """A double-click on the waveform at `at` seconds. Inside a chop it
    cuts it in two (the second half takes the first's mix and cleaning, not
    its clip). Outside any chop it starts one that runs to the next chop or
    the region's end (past the region: the next chop, or a clip's length).
    The new chop, or None when nothing could be made there."""
    at = min(max(0.0, at), meta["seconds"])
    if len(meta["chops"]) >= limits.MAX_LAB_CHOPS:
        return None
    inside = next(
        (c for c in meta["chops"] if c["start"] - TOUCH < at < c["end"] - TOUCH), None
    )
    if inside is None:
        r0, rlen = lab.span(meta)
        end = r0 + rlen if at < r0 + rlen - MIN else at + limits.MAX_CLIP_SECONDS
        return add(meta, at, end)
    if at - inside["start"] < MIN or inside["end"] - at < MIN:
        return None
    taken = {c["id"] for c in meta["chops"]}
    mix = {k: inside[k] for k in ("stems", "model", "levels")}
    second = _make(meta, at, inside["end"], _free_name(meta), taken, mix)
    second["clean"] = dict(inside["clean"])
    inside["end"], inside["notes"] = round(at, 4), ""
    _put(meta, [*meta["chops"], second])
    return second


def move_edge(meta: dict, cid: str, edge: str, at: float):
    """Put the chop's `edge` ("start" | "end") at `at` seconds — the
    sampler's slice rule (`sampler.nudge` / `start_bounds`), for both
    edges: the edge stays `MIN_CHOP_SECONDS` inside its own chop's other
    edge (which wins), a neighbour that touched it follows and is itself
    never made shorter than that, and a neighbour that did not touch is a
    wall. The chop, or None when there is no such chop or edge."""
    chops = meta["chops"]
    i = next((k for k, c in enumerate(chops) if c["id"] == cid), None)
    if i is None or edge not in ("start", "end") or not -1e9 < at < 1e9:
        return None
    c = chops[i]
    if edge == "start":
        prev = chops[i - 1] if i > 0 else None
        follows = prev is not None and abs(prev["end"] - c["start"]) < TOUCH
        lo = 0.0 if prev is None else prev["start"] + MIN if follows else prev["end"]
        new = round(min(max(at, lo), c["end"] - MIN), 4)
        if follows and prev is not None:
            prev["end"], prev["notes"] = new, ""
    else:
        nxt = chops[i + 1] if i + 1 < len(chops) else None
        follows = nxt is not None and abs(nxt["start"] - c["end"]) < TOUCH
        hi = (
            meta["seconds"]
            if nxt is None
            else nxt["end"] - MIN
            if follows
            else nxt["start"]
        )
        new = round(max(min(at, hi), c["start"] + MIN), 4)
        if follows and nxt is not None:
            nxt["start"], nxt["notes"] = new, ""
    c[edge], c["notes"] = new, ""
    return c


def remove(meta: dict, cid: str) -> bool:
    kept = [c for c in meta["chops"] if c["id"] != cid]
    found = len(kept) < len(meta["chops"])
    _put(meta, kept)
    return found


def rename(meta: dict, cid: str, name: str):
    c = find(meta, cid)
    name = " ".join(str(name).split())[: limits.MAX_CHOP_NAME_CHARS]
    if c is not None and name:
        c["name"] = name
    return c


def set_clean(meta: dict, cid: str, settings):
    """The chop's cleaning (`labclean.clean_settings` holds it to range)."""
    c = find(meta, cid)
    if c is not None:
        c["clean"], c["notes"] = labclean.clean_settings(settings), ""
    return c


def use_lanes(meta: dict, cid: str):
    """ "Use the lanes' mix": the ticked stems and their levels, as they
    are now, become the chop's own."""
    c = find(meta, cid)
    if c is not None:
        c.update(copy.deepcopy(lane_mix(meta)), notes="")
    return c


def frame(meta: dict, c: dict) -> None:
    """Make the chop the region (and the selected one): in bars when it
    sits on bar lines — the drum reading wants a region in bars — else in
    seconds."""
    g = lab.grid(meta)
    length = c["end"] - c["start"]
    first = (c["start"] - g["offset"]) / g["bar"] if g["set"] else 0.5
    count = length / g["bar"] if g["set"] else 0.5
    on_bars = all(abs(v - round(v)) * g["bar"] < 0.001 for v in (first, count))
    fits = 1 <= round(count) <= limits.MAX_LAB_REGION_BARS and round(first) >= 0
    if g["set"] and on_bars and fits:
        lab.set_region(meta, "bars", round(first), round(count))
    else:
        lab.set_region(meta, "seconds", c["start"], length)
    meta["chop"] = c["id"]


def select(meta: dict, cid: str, have=()):
    """Make the chop the region, and the lanes its mix: the region takes
    its span (in bars when it sits on bar lines, else seconds), the boxes
    its stems and the sliders its levels. A chop of the other stem set
    brings that set up when the source still holds it (`have`: the sets on
    disk); when it does not, the lanes stay as they are."""
    c = find(meta, cid)
    if c is None:
        return None
    frame(meta, c)
    if not c["stems"]:
        meta["picked"] = []
        return c
    if c["model"] != meta["stems"]["model"]:
        if c["model"] not in have:
            return c
        meta["stems"]["model"] = c["model"]
    meta["picked"] = list(c["stems"])
    mine = meta.setdefault("levels", {}).setdefault(c["model"], {})
    for s in c["stems"]:
        mine[s] = c["levels"].get(s, 0.0)
    return c


def snap(meta: dict, at: float, free: bool = False) -> float:
    """Where an edge dropped at `at` seconds lands: on the nearest beat
    line when that line is within `SNAP_SHARE` of the span the waveform
    shows (so zooming in makes the snap finer), else where it was dropped.
    `free` (Alt held, an arrow nudge) never snaps."""
    g = lab.grid(meta)
    if free or not g["set"]:
        return at
    line = g["offset"] + round((at - g["offset"]) / g["beat"]) * g["beat"]
    near = abs(line - at) <= SNAP_SHARE * lab.view(meta)[1]
    return line if near and 0.0 <= line <= meta["seconds"] else at


def open_at(data_dir, sid: str, cid: str = "") -> str:
    """ "Open in Lab" (`GET /view/lab?source=&chop=`, what a kept chop's
    clip links to): make `sid` the source the Lab opens on and, with
    `cid`, that chop the region — brought into sight when the waveform is
    zoomed elsewhere. "" when it did, else what to tell the player (the
    song or the chop is gone); never an error."""
    found: dict = {}

    def change(m: dict) -> None:
        m["opened"] = time.time()
        c = select(m, cid, sources.models_on_disk(data_dir, sid)) if cid else None
        found["chop"] = c
        shown = lab.view(m)
        if c is not None and not (shown[0] <= c["start"] and c["end"] <= sum(shown)):
            lab.set_view(m, "region")

    if sources.update_meta(data_dir, sid, change) is None:
        return "That song is no longer in the Lab."
    if cid and found.get("chop") is None:
        return "That chop is no longer in the song (it was removed or cut again)."
    return ""


# --- auto --------------------------------------------------------------------


def grid_spans(meta: dict, rule: str) -> list[tuple[float, float, str]]:
    """The region cut on its grid lines — `rule` "bars" or "beats" — as
    (start, end, name): `lab.chop_slices`' line walk, in seconds. A bar is
    named by its number in the song. [] with no grid."""
    g = lab.grid(meta)
    if rule not in ("bars", "beats") or not g["set"]:
        return []
    start, length = lab.span(meta)
    end = start + length
    step = g["bar"] if rule == "bars" else g["beat"]
    first = int(np.ceil((start - g["offset"]) / step - 1e-6))
    edges = [start]
    t = g["offset"] + first * step
    while t < end - 1e-6:
        if t > start + 1e-6:
            edges.append(t)
        t += step
    edges.append(end)
    out = []
    for a, b in zip(edges, edges[1:], strict=False):
        n = (a - g["offset"]) / step
        on_line = abs(n - round(n)) * step < 0.002
        if rule == "bars":
            name = f"bar {round(n) + 1}" if on_line else "pickup"
        else:
            bar, beat = divmod(round(n), lab.BEATS_PER_BAR)
            name = f"beat {bar + 1}.{beat + 1}" if on_line else "pickup"
        out.append((a, b, name))
    return out


def auto(meta: dict, rule: str, spans) -> tuple[int, int]:
    """Fill the region with chops at `spans` ((start, end) or (start, end,
    name), seconds), replacing the chops that lie wholly inside it. A span
    is cut back where it would run over a chop that stays; past
    `MAX_LAB_CHOPS` the first ones are kept. (made, left out); with nothing
    made the meta is as it was."""
    r0, rlen = lab.span(meta)
    r1 = r0 + rlen
    stay = [
        c
        for c in meta["chops"]
        if not (c["start"] >= r0 - TOUCH and c["end"] <= r1 + TOUCH)
    ]
    taken = {c["id"] for c in stay}
    mix = lane_mix(meta)
    word = _WORDS.get(rule, "chop")
    new: list[dict] = []
    left = 0
    for i, sp in enumerate(sorted(spans, key=lambda s: s[0])):
        a, b = max(float(sp[0]), r0), min(float(sp[1]), r1)
        for c in stay:
            if c["start"] < b and c["end"] > a:
                a, b = (max(a, c["end"]), b) if c["start"] <= a else (a, c["start"])
        if new:
            a = max(a, new[-1]["end"])
        if b - a < MIN:
            continue
        if len(stay) + len(new) >= limits.MAX_LAB_CHOPS:
            left += 1
            continue
        name = sp[2] if len(sp) > 2 else f"{word} {i + 1}"
        new.append(_make(meta, a, b, name, taken, mix))
        taken.add(new[-1]["id"])
    if new:  # nothing found: the chops that were there are not thrown away
        _put(meta, [*stay, *new])
    return len(new), left


# --- a chop's audio ----------------------------------------------------------


def seconds(chop: dict) -> float:
    return chop["end"] - chop["start"]


def needs(models, chop: dict) -> str:
    """ "" when the chop can be played — it is the recording, or its stem
    set is on disk (`models`: `sources.models_on_disk`) — else what is
    missing: "needs the 6-stem separation". Never played from another set."""
    if not chop["stems"] or chop["model"] in models:
        return ""
    known = stem_models.MODELS.get(chop["model"])
    count = len(known.sources) if known else len(sources.STEM_NAMES)
    return f"needs the {count}-stem separation"


def audio(data_dir, meta: dict, chop: dict, cap: float = limits.MAX_CLIP_SECONDS):
    """The chop's own mix, uncleaned: its stems of its set summed at its
    levels (`sources.read_sum`), or the recording; at most `cap` seconds."""
    return sources.read_sum(
        data_dir,
        sources.with_model(meta, chop["model"]) if chop["stems"] else meta,
        chop["stems"],
        int(round(chop["start"] * SR)),
        int(round(min(seconds(chop), cap) * SR)),
        levels=chop["levels"],
    )


def fit_plan(meta: dict, chop: dict, session_bpm: float, song_bpm: float):
    """What `labclean.clean` takes as `plan` for this chop: None when its
    cleaning does not fit it; else the stretch to the tempo it names —
    "session" is the loop's bpm (what `/lab/send` fits a clip to), "song"
    the song's (what `/lab/place` fits a take to) — or `{"reason"}` when
    `lab.stretch_plan` says it cannot be done."""
    fit = chop["clean"]["fit"]
    if fit == "off":
        return None
    target = float(session_bpm if fit == "session" else song_bpm)
    plan = lab.stretch_plan(meta, target)
    if not plan["possible"]:
        return {"reason": plan["reason"]}
    return {
        "factor": plan["factor"],
        "source_bpm": round(lab.grid(meta)["bpm"], 2),
        "bpm": round(target, 3),
        "bar": 240.0 / target,
    }


def render(data_dir, meta: dict, chop: dict, session_bpm: float, song_bpm: float):
    """(audio, notes, plan, "" or why not): the chop as it is played and
    kept — its own mix, cleaned with its own settings."""
    why = needs(sources.models_on_disk(data_dir, meta["id"]), chop)
    if why:
        return None, [], None, f"That chop {why}."
    raw = audio(data_dir, meta, chop)
    if not len(raw):
        return None, [], None, "That chop is empty."
    plan = fit_plan(meta, chop, session_bpm, song_bpm)
    out, notes = labclean.clean(raw, chop["clean"], plan)
    return out, notes, plan, ""


def clip_info(meta: dict, chop: dict, out: np.ndarray, plan: dict | None) -> dict:
    """The meta a kept chop's clip carries: where it came from (`source`
    "lab: <song>", and `lab` — the source and chop ids, the span, the mix,
    the tempo it was cut at, whether it was stretched, the key, the
    cleaning), and `bpm` / `bars` when it is on a grid."""
    g = lab.grid(meta)
    fits = bool(plan and "factor" in plan)
    info: dict = {
        "source": f"lab: {meta['name']}"[: limits.MAX_NAME_CHARS],
        "lab": {
            "source": meta["id"],
            "chop": chop["id"],
            "start": round(chop["start"], 4),
            "seconds": round(seconds(chop), 4),
            "stems": list(chop["stems"]),
            "model": chop["model"],
            "levels": dict(chop["levels"]),
            "source_bpm": round(g["bpm"], 3),
            "fitted": bool(plan and fits and abs(plan["factor"] - 1.0) > 1e-3),
            "key": lab.key_in_force(meta),
            "clean": dict(chop["clean"]),
        },
    }
    if g["set"]:
        bpm = plan["bpm"] if plan and fits else g["bpm"]
        info["bpm"] = round(bpm, 3)
        bar = 240.0 / bpm
        bars = labclean.whole_bars(len(out), bar)
        # unfitted, only a chop that really is whole bars says so
        if bars and (fits or abs(len(out) / SR - bars * bar) < 0.02):
            info["bars"] = bars
    return info


def own_clip(data_dir, sid: str, chop: dict) -> str:
    """The name of the clip this chop was kept as, while that clip is still
    in the store and still says it is this chop's (`lab.source` /
    `lab.chop` in its meta) — the only clip "Update clip" may write over.
    "" otherwise: a clip deleted, renamed away or replaced by another."""
    name = chop["clip"]
    d = clip_dir(Path(data_dir))
    if not name or not (d / f"{name}.wav").is_file():
        return ""
    try:
        meta = json.loads((d / f"{name}.meta.json").read_text(encoding="utf-8"))
        origin = meta["lab"]
        mine = origin["source"] == sid and origin["chop"] == chop["id"]
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return ""
    return name if mine else ""


def clip_renamed(data_dir, old: str, new: str) -> None:
    """A clip was renamed (`clips.rename_clip` has moved it): the chop it
    was kept from follows, so "Update clip" still writes over that clip
    and a later Keep does not quietly make a second one. Only the chop the
    clip's own meta names (`lab.source` / `lab.chop`), and only while that
    chop still points at the old name — the two-way check `own_clip` makes.
    Anything else (not a Lab clip, the song or the chop gone) is nothing
    to do; never an error."""
    d = clip_dir(Path(data_dir))
    try:
        origin = json.loads((d / f"{new}.meta.json").read_text(encoding="utf-8"))["lab"]
        sid, cid = origin["source"], origin["chop"]
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return
    if not isinstance(sid, str) or not isinstance(cid, str):
        return

    def change(m: dict) -> None:
        c = find(m, cid)
        if c is not None and c["clip"] == old:
            c["clip"] = new

    meta = sources.read_meta(data_dir, sid) if sources.valid_id(sid) else None
    chop = find(meta, cid) if meta else None
    if chop is not None and chop["clip"] == old:
        sources.update_meta(data_dir, sid, change)


def clip_usage(state) -> dict:
    """Where the session — and the song's undo ring — holds each clip
    (`StudioState.clip_usage`, what the library's delete asks): what
    "Update clip" names before it changes them all."""
    with state.lock:
        return state.clip_usage()


# --- the page ----------------------------------------------------------------


def source_context(data_dir, meta: dict, state) -> dict:
    """`lab.source_context` with the chop list filled in: the one thing
    the Lab's panel reads from the session beyond the tempos — the loop's
    and the song's bpm (what a chop is fitted to) and where a kept chop's
    clip is used."""
    session_bpm, song_bpm = state.arrangement.bpm, float(state.song["bpm"])
    ctx = lab.source_context(data_dir, meta, session_bpm)
    ctx["song_bpm"] = round(song_bpm, 1)
    ctx["chop_rows"] = rows(
        data_dir, meta, session_bpm, song_bpm, uses=lambda: clip_usage(state)
    )
    ctx["models_on_disk"] = sources.models_on_disk(data_dir, meta["id"])
    return ctx


def _bars_text(length: float, g: dict) -> str:
    if not g["set"]:
        return ""
    bars = f"{length / g['bar']:.2f}".rstrip("0").rstrip(".")
    return f"{bars} bar{'' if bars == '1' else 's'}"


def rows(data_dir, meta: dict, session_bpm: float, song_bpm: float, uses=None):
    """What the chop list and the markers draw, a dict a chop. `uses` is
    called (once, only when a chop has a clip) for `clip_usage`."""
    g, shown = lab.grid(meta), lab.view(meta)
    models = sources.models_on_disk(data_dir, meta["id"]) if meta["chops"] else ()
    current, chosen = meta["stems"]["model"], lab.selected_chop(meta)
    counts = None
    out = []
    for c in meta["chops"]:
        length = seconds(c)
        clip = own_clip(data_dir, meta["id"], c)
        if clip and counts is None:
            counts = uses() if uses else {}
        stamp = json.dumps([c, session_bpm, song_bpm, g["bpm"]], sort_keys=True)
        known = stem_models.MODELS.get(c["model"])
        # both edges, in seconds of the song: labview.js places them over
        # whatever span the lanes show and hides the ones out of sight
        marks = [
            {
                "edge": edge,
                "at": round(c[edge], 4),
                "x": lab.at_x(c[edge], shown),
                "clock": lab.clock(c[edge]),
                "seen": shown[0] - 1e-6 <= c[edge] <= sum(shown) + 1e-6,
            }
            for edge in ("start", "end")
        ]
        out.append(
            {
                "id": c["id"],
                "name": c["name"],
                "seconds": round(length, 2),
                "from": lab.clock(c["start"]),
                "bars": _bars_text(length, g),
                "what": " + ".join(c["stems"]) or "mix",
                "levels": ", ".join(
                    f"{s} {lab.db_text(db)}" for s, db in c["levels"].items()
                ),
                # named when it is not the set the lanes show
                "set": known.label if known and c["model"] != current else "",
                "needs": needs(models, c),
                "selected": c["id"] == chosen,
                "too_long": length > limits.MAX_CLIP_SECONDS + 1e-6,
                "clean": c["clean"],
                "cleaned": not labclean.is_default(c["clean"]),
                "notes": c["notes"],
                "clip": clip,
                # where its clip is used, in the library's own words
                "used": cliplib.update_note(counts.get(clip)) if counts else "",
                "marks": marks,
                "url": (
                    f"/lab/{meta['id']}/chop/{c['id']}.wav"
                    f"?v={hashlib.sha1(stamp.encode()).hexdigest()[:10]}"
                ),
            }
        )
    return out
