"""The Sample Lab's logic: a source's grid, its region, what a region
becomes (a clip, a sampler sound), and what the page draws.

Everything here works from a source's meta (`sources.py`) — the Lab keeps
no session state of its own, and nothing it holds is a render input. The
routes (`routes/lab.py`) are thin callers; the separation job is
`labjobs.py`.

- **The grid** is the analysis' tempo and first bar line
  (`analysis.rhythm`), each overruled by what the user typed or nudged
  (`meta["user"]`): `grid(meta)` is the one place that decides.
- **A region** is stored as the user set it — "bars" (a start bar and a
  length in bars, on the grid) or "seconds" (free) — and `span(meta)` turns
  it into seconds inside the source. Stored in bars, a region follows the
  grid when the tempo is corrected.
- **Sending** a region copies its audio out: `clip_audio` (optionally
  stretched to the session tempo with the sampler's WSOLA, so 4 bars stay
  4 bars) or the sampler path, with `chop_slices` laying one slice per
  beat or bar. Both are capped by the stores they land in (30 s).
"""

from __future__ import annotations

import math

import numpy as np

from .. import sampler
from ..analysis import detect_key, tempo
from ..analysis.key import NOTE_NAMES
from ..core import SR
from . import limits, sources

W = 1200  # a lane's viewBox width (= the peaks' column count)
H_LANE = 100  # its viewBox height: the lane's CSS height stretches it
H_STRIP = 24  # the overview strip's
BEATS_PER_BAR = 4
NUDGE_BEATS = (-1.0, -0.25, 0.25, 1.0)  # the offset buttons, in beats
CHOPS = ("none", "transients", "beats", "bars")
EARLY = 0.06  # seconds a first bar line may sit before the audio starts
KEY_CLOSE = 0.15  # a relative within this margin is named beside the key


def analyse(audio: np.ndarray) -> dict:
    """A source's `analysis` block: tempo, grid and key of the mix."""
    g = tempo(audio)
    return {
        "bpm": g.bpm,
        "confidence": g.confidence,
        "beat_offset": g.beat_offset,
        "downbeat_offset": g.downbeat_offset,
        # three beats a bar or four, as heard (`rhythm._meter_guess`): said
        # beside the tempo, never applied (the Lab's grid stays in fours)
        "meter_guess": g.meter_guess,
        "meter_sure": g.meter_sure,
        **key_block(audio, sources.MIX),
    }


def key_block(audio: np.ndarray, read_from: str, bass=None, on: dict | None = None):
    """The analysis' key fields, read from `audio`; with the separated
    `bass` (and the grid `on` which it plays) the bass line settles key
    versus relative (`analysis.key`)."""
    bar, offset = (on["bar"], on["offset"]) if on and on["set"] else (0.0, 0.0)
    k = detect_key(audio, bass=bass, bar=bar, offset=offset)
    return {
        "key": k.name,
        "key_confidence": k.confidence,
        "key_scores": [list(s) for s in k.scores],
        "key_from": read_from,
        "key_margin": k.margin,
        "key_tonic": k.tonic_from,
    }


def relatives(a: str, b: str) -> bool:
    """Are two key names a relative pair (F major / D minor)."""
    try:
        (ta, ma), (tb, mb) = (
            (NOTE_NAMES.index(k.split()[0]), k.split()[1]) for k in (a, b)
        )
    except (ValueError, IndexError):
        return False
    major, minor = (ta, tb) if ma == "major" else (tb, ta)
    return ma != mb and (major - minor) % 12 == 3


def better_key(old: dict, new: dict) -> bool:
    """Should a re-read (`new`, a `key_block`) replace the key the source
    has (`old`)? Never with nothing, and never with a less confident key —
    the re-read from the drumless stems once turned a right D minor into
    its relative on a *higher* raw score, so "newer" is no argument. One
    question confidence does not measure (it is about the scale, and a
    relative pair shares its scale): between a key and its relative, the
    reading that leads its relative by more wins — `key_margin`, where the
    bass line's say is counted."""
    if not new.get("key"):
        return False
    if not old.get("key"):
        return True
    if relatives(old["key"], new["key"]):
        return new.get("key_margin", 0.0) > old.get("key_margin", 0.0)
    return new["key_confidence"] >= old["key_confidence"]


def reread_key(data_dir, sid: str) -> dict | None:
    """Read a separated source's key again from its stems — everything but
    the drums, with the bass line's say on the grid in force — and keep it
    if it is the better reading (`better_key`). The source's meta, or None
    when it is gone; a source with no stems is left as it is."""
    meta = sources.read_meta(data_dir, sid)
    if meta is None or not meta["stems"]["model"]:
        return meta
    pitched = sources.read_sum(
        data_dir,
        meta,
        [s for s in sources.stem_names(meta["stems"]["model"]) if s != "drums"],
    )
    block = key_block(
        pitched, "stems", bass=sources.read(data_dir, meta, "bass"), on=grid(meta)
    )

    def keep(m: dict) -> None:
        if better_key(m["analysis"], block):
            m["analysis"].update(block)

    return sources.update_meta(data_dir, sid, keep)


def key_in_force(meta: dict) -> str:
    """The key the Lab labels things with: the user's, else the reading."""
    return meta["user"]["key"] or meta["analysis"]["key"]


def tonic_midi(key: str) -> int | None:
    """A key's tonic as the MIDI note in the octave from C4 (60..71): the
    root a sampler sound is given when its audio has no single pitch of its
    own (a chord loop), so the key's own note plays it as recorded."""
    name = key.split()[0] if key else ""
    return 60 + NOTE_NAMES.index(name) if name in NOTE_NAMES else None


# --- grid and region ---------------------------------------------------------


def grid(meta: dict) -> dict:
    """The grid in force: `bpm` and `offset` (the first bar line, seconds)
    as the user set them, else as analysed; `beat` / `bar` in seconds;
    `set` False when there is no tempo to draw a grid from."""
    an, user = meta["analysis"], meta["user"]
    bpm = user["bpm"] if user["bpm"] else an["bpm"]
    ok = bpm >= limits.LAB_BPM_RANGE[0]
    beat = 60.0 / bpm if ok else 0.0
    bar = beat * BEATS_PER_BAR
    offset = an["downbeat_offset"] if user["offset"] is None else user["offset"]
    if ok:
        offset = offset % bar  # the first bar line at or after 0 ...
        if offset > bar - EARLY:
            # ... or a hair before it: a song that starts on its downbeat
            # analyses to a bar line a few ms either side of 0, and read as
            # "just under one bar in" the whole grid sat a bar late
            offset -= bar
    return {
        "bpm": bpm if ok else 0.0,
        "set": ok,
        "beat": beat,
        "bar": bar,
        "offset": offset if ok else 0.0,
        "user_bpm": bool(user["bpm"]),
        "user_offset": user["offset"] is not None,
    }


def bars_in(meta: dict) -> int:
    """Whole bars between the first bar line and the end."""
    g = grid(meta)
    if not g["set"]:
        return 0
    return max(0, int((meta["seconds"] - g["offset"]) / g["bar"] + 1e-6))


def span(meta: dict) -> tuple[float, float]:
    """The region as (start, length) in seconds, inside the source. A
    "bars" region with no grid to stand on reads as seconds."""
    r, g = meta["region"], grid(meta)
    total = meta["seconds"]
    if r["mode"] == "bars" and g["set"]:
        start = g["offset"] + round(r["start"]) * g["bar"]
        length = max(1, round(r["length"])) * g["bar"]
    else:
        start, length = r["start"], max(r["length"], 0.05)
    end = min(start + length, total)
    start = min(max(0.0, start), max(0.0, total - 0.05))
    return start, max(0.0, end - start)


def set_region(meta: dict, mode: str, start: float, length: float) -> None:
    """Store a region as typed: whole bars (start >= 0, 1 .. MAX bars) or
    seconds. Clamped here; `span` clamps to the source again on reading."""
    g = grid(meta)
    if mode == "bars" and g["set"]:
        last = max(0, bars_in(meta) - 1)
        start = float(min(max(0, round(start)), last))
        length = float(min(max(1, round(length)), limits.MAX_LAB_REGION_BARS))
        meta["region"] = {"mode": "bars", "start": start, "length": length}
    else:
        start = min(max(0.0, start), meta["seconds"])
        length = min(max(0.05, length), meta["seconds"])
        meta["region"] = {"mode": "seconds", "start": start, "length": length}


def drag_region(meta: dict, a: float, b: float, free: bool = False) -> None:
    """A drag across the waveform, from `a` to `b` seconds: in bars mode it
    snaps to the bars it touched; in seconds mode each end lands on the
    nearest beat line of the grid in force (never two on one line) — or,
    with `free` (Alt held) or no grid, where it was let go."""
    total = meta["seconds"]
    a, b = sorted((min(max(0.0, a), total), min(max(0.0, b), total)))
    g = grid(meta)
    if meta["region"]["mode"] == "bars" and g["set"]:
        first = math.floor((a - g["offset"]) / g["bar"] + 1e-3)
        last = math.ceil((b - g["offset"]) / g["bar"] - 1e-3)
        set_region(meta, "bars", max(0, first), max(1, last - max(0, first)))
        return
    if g["set"] and not free:
        beat, off = g["beat"], g["offset"]
        a = max(0.0, off + round((a - off) / beat) * beat)
        b = off + round((b - off) / beat) * beat
        if b - a < beat - 1e-6:  # a drag inside one beat is that beat
            b = a + beat
        b = min(total, b)
    set_region(meta, "seconds", a, b - a)


def pick_lane(meta: dict, lane: str, add: bool = False) -> None:
    """A drag on a lane picks what the region plays: the mix lane is the
    recording (no stem ticked), a stem's lane that stem alone — or, with
    `add` (Shift held), that stem beside the ones already ticked."""
    model = meta["stems"]["model"]
    names = sources.stem_names(model) if model else ()
    if lane == sources.MIX and not add:
        meta["picked"] = []
    elif lane in names:
        on = set(meta["picked"]) | {lane} if add else {lane}
        meta["picked"] = [s for s in names if s in on]


def _unit(meta: dict) -> float:
    """A beat of the grid in force; a quarter second with none."""
    g = grid(meta)
    return g["beat"] if g["set"] else 0.25


def step_region(meta: dict, op: str, n: int) -> None:
    """The keyboard on the lanes: `move` the region by `n` beats (four is a
    bar: a region in bars stays in bars when `n` is whole bars, else it
    becomes beats, since it is no longer on the bar lines) or `grow` it by
    `n` steps of its snap — bars in bars, beats in beats — never under one
    step and always inside the song."""
    g, r, total = grid(meta), meta["region"], meta["seconds"]
    bars = r["mode"] == "bars" and g["set"]
    if op == "move" and bars and n % BEATS_PER_BAR == 0:
        length = max(1, round(r["length"]))
        last = max(0, bars_in(meta) - length)
        start = min(max(0, round(r["start"]) + n // BEATS_PER_BAR), last)
        meta["region"] = {
            "mode": "bars",
            "start": float(start),
            "length": float(length),
        }
        return
    if op == "grow" and bars:
        start = round(r["start"])
        cap = min(limits.MAX_LAB_REGION_BARS, max(1, bars_in(meta) - start))
        length = min(max(1, round(r["length"]) + n), cap)
        meta["region"] = {
            "mode": "bars",
            "start": float(start),
            "length": float(length),
        }
        return
    start, length = span(meta)
    unit = _unit(meta)
    if op == "move":
        start = min(max(0.0, start + n * unit), max(0.0, total - length))
    else:
        length = min(max(unit, length + n * unit), total - start)
    set_region(meta, "seconds", start, length)


def move_region_edge(meta: dict, edge: str, at: float, free: bool = False) -> None:
    """A region's edge dragged to `at` seconds: to the nearest bar line in
    bars, the nearest beat line in beats — or, `free` (Alt held) or with no
    grid, where it was let go, which takes a bars region into seconds. The
    edge never crosses the other: a region keeps one step (a bar, a beat,
    50 ms)."""
    g, r, total = grid(meta), meta["region"], meta["seconds"]
    at = min(max(0.0, at), total)
    if r["mode"] == "bars" and g["set"] and not free:
        first = round(r["start"])
        last = first + max(1, round(r["length"]))
        line = round((at - g["offset"]) / g["bar"])
        if edge == "start":
            first = min(max(0, line), last - 1)
        else:
            last = min(max(first + 1, line), max(first + 1, bars_in(meta)))
        set_region(meta, "bars", first, last - first)
        return
    start, length = span(meta)
    end = start + length
    least = 0.05
    if g["set"] and not free:
        at = g["offset"] + round((at - g["offset"]) / g["beat"]) * g["beat"]
        least = g["beat"]
    if edge == "start":
        start = min(max(0.0, at), end - least)
    else:
        end = min(max(start + least, at), total)
    set_region(meta, "seconds", start, end - start)


def switch_mode(meta: dict, mode: str) -> None:
    """Bars <-> seconds, keeping the audio the region covers (to the
    nearest bar going to bars)."""
    start, length = span(meta)
    g = grid(meta)
    if mode == "bars" and g["set"]:
        set_region(
            meta,
            "bars",
            round((start - g["offset"]) / g["bar"]),
            round(length / g["bar"]),
        )
    else:
        set_region(meta, "seconds", start, length)


def set_grid(meta: dict, action: str, bpm: float | None, nudge: float = 0.0) -> None:
    """The grid controls: `set` (a typed tempo), `half` / `double`, `nudge`
    (the first bar line moved by `nudge` beats), `reset` (back to the
    analysis)."""
    g = grid(meta)
    lo, hi = limits.LAB_BPM_RANGE
    user = meta["user"]
    if action == "reset":
        user["bpm"], user["offset"] = None, None
    elif action == "set" and bpm is not None:
        user["bpm"] = min(max(bpm, lo), hi)
    elif action in ("half", "double") and g["set"]:
        new = g["bpm"] * (0.5 if action == "half" else 2.0)
        if lo <= new <= hi:
            user["bpm"] = new
    elif action == "nudge" and g["set"]:
        user["offset"] = (g["offset"] + nudge * g["beat"]) % g["bar"]


def region_audio(data_dir, meta: dict, cap: float = limits.MAX_CLIP_SECONDS):
    """The region's audio — the picked stems summed, each at its level
    (`sources.levels`), else the mix — as float32 (n, 2), at most `cap`
    seconds of it. What the audition plays and every "send" sends."""
    start, length = span(meta)
    frames = int(round(min(length, cap) * SR))
    return sources.read_sum(
        data_dir,
        meta,
        meta["picked"],
        int(round(start * SR)),
        frames,
        levels=sources.levels(meta),
    )


def mix_levels(meta: dict) -> dict[str, float]:
    """The picked stems whose level is not 0 dB: what a clip's meta says
    about the mix beyond the stems' names."""
    lv = sources.levels(meta)
    return {s: lv[s] for s in meta["picked"] if lv.get(s)}


def db_text(db: float) -> str:
    """ "+3 dB", "0 dB", "−4.5 dB"."""
    return f"{'+' if db > 0 else '−' if db < 0 else ''}{abs(db):g} dB"


def solo(meta: dict, stem: str) -> None:
    """The quick solo: that stem alone — or, pressed on the stem that
    already plays alone, every stem again (the stems' mix at their levels;
    the untouched recording is no box ticked)."""
    model = meta["stems"]["model"]
    names = sources.stem_names(model) if model else ()
    if stem in names:
        meta["picked"] = list(names) if meta["picked"] == [stem] else [stem]


def selected_chop(meta: dict) -> str:
    """The id of the chop (`labchops.py`) that is the region now: the one
    last selected, while the region still is its span. "" for none."""
    c = next((c for c in meta["chops"] if c["id"] == meta["chop"]), None)
    if c is None:
        return ""
    start, length = span(meta)
    same = abs(start - c["start"]) < 0.002 and abs(start + length - c["end"]) < 0.002
    return c["id"] if same else ""


def too_long(meta: dict) -> bool:
    return span(meta)[1] > limits.MAX_CLIP_SECONDS + 1e-6


def what(meta: dict) -> str:
    """The region's material in words: "mix", "vocals", "drums + bass"."""
    picked = meta["picked"] if meta["stems"]["model"] else []
    return " + ".join(picked) if picked else "mix"


def pick_name(meta: dict) -> str:
    """What a thing made from the region is called, by what it plays and
    where: "vocals bars 9-12", "drums-bass bar 3", "mix bar 5.3 (6 beats)"
    off the bar lines, "vocals 12.5s (3.0s)" with no grid — a clip name's
    characters (`files.safe_name`: no "+", no en dash)."""
    w = what(meta).replace(" + ", "-")
    g, r = grid(meta), meta["region"]
    start, length = span(meta)
    if r["mode"] == "bars" and g["set"]:
        first, n = int(round(r["start"])) + 1, max(1, int(round(r["length"])))
        return f"{w} bar {first}" if n == 1 else f"{w} bars {first}-{first + n - 1}"
    if g["set"]:
        beats = round(max(0.0, start - g["offset"]) / g["beat"], 2)
        bar, beat = divmod(beats, BEATS_PER_BAR)
        at = f"{int(bar) + 1}.{round(beat + 1, 1):g}"
        return f"{w} bar {at} ({round(length / g['beat'], 1):g} beats)"
    return f"{w} {start:.1f}s ({length:.1f}s)"


# --- sending -----------------------------------------------------------------


def stretch_plan(meta: dict, session_bpm: float) -> dict:
    """Whether "fit to the session tempo" can run and what it would do:
    `factor` is the length factor (source bpm / session bpm), `possible`
    False with the `reason` when there is no grid or the factor is outside
    `LAB_STRETCH_RANGE`, `needed` False when the tempos already agree."""
    g = grid(meta)
    if not g["set"]:
        return {"possible": False, "needed": False, "reason": "no tempo set"}
    factor = g["bpm"] / session_bpm
    lo, hi = limits.LAB_STRETCH_RANGE
    if not lo <= factor <= hi:
        return {
            "possible": False,
            "needed": True,
            "factor": factor,
            "reason": f"{g['bpm']:.0f} to {session_bpm:.0f} bpm is too far to stretch",
        }
    return {
        "possible": True,
        "needed": abs(factor - 1.0) > 1e-3,
        "factor": factor,
        "reason": "",
    }


def clip_audio(data_dir, meta: dict, session_bpm: float, fit: bool):
    """(audio, meta for the clip, "" or why not): the region as a clip.
    With `fit` and a usable grid it is stretched to `session_bpm`, so its
    bars are the session's bars; the clip's meta records where it came
    from either way."""
    if too_long(meta):
        return None, {}, _too_long_message(meta)
    audio = region_audio(data_dir, meta)
    if not len(audio) or not np.any(np.abs(audio) > 1e-4):
        return None, {}, "That region is silent."
    start, length = span(meta)
    g, r = grid(meta), meta["region"]
    plan = stretch_plan(meta, session_bpm)
    fitted = bool(fit and plan["possible"] and plan["needed"])
    out = np.asarray(audio, dtype=np.float64)
    if fitted:
        out = sampler.time_stretch(out, plan["factor"])
        if len(out) / SR > limits.MAX_CLIP_SECONDS:
            return (
                None,
                {},
                (
                    "Stretched to the session tempo that region is longer than "
                    f"{limits.MAX_CLIP_SECONDS:g} s: pick fewer bars."
                ),
            )
    info = {
        "source": f"lab: {meta['name']}"[: limits.MAX_NAME_CHARS],
        "lab": {
            "source": meta["id"],
            "start": round(start, 4),
            "seconds": round(length, 4),
            "stems": list(meta["picked"]),
            "source_bpm": round(g["bpm"], 3),
            "fitted": fitted,
            "key": key_in_force(meta),
        },
    }
    if mix_levels(meta):  # the stems' levels (dB), when any is not 0
        info["lab"]["levels"] = mix_levels(meta)
    if g["set"]:
        on_grid = fit and plan["possible"]
        info["bpm"] = round(session_bpm if on_grid else g["bpm"], 3)
        if r["mode"] == "bars":
            info["bars"] = int(round(r["length"]))
    return out, info, ""


def _too_long_message(meta: dict) -> str:
    return (
        f"That region is {span(meta)[1]:.1f} s; a clip or a sample holds "
        f"{limits.MAX_CLIP_SECONDS:g} s. Pick a shorter one."
    )


def sampler_audio(data_dir, meta: dict):
    """(audio, "" or why not): the region for the sampler, uncut and
    unstretched (a sampler sound keeps the recording's own time)."""
    if too_long(meta):
        return None, _too_long_message(meta)
    audio = region_audio(data_dir, meta)
    if not len(audio):
        return None, "That region is empty."
    return audio, ""


def chop_slices(meta: dict, mode: str) -> tuple[str, str]:
    """(what `routes.sampler.adopt_audio` takes as `chop`, a note for the
    player): "" for the whole region, "transients", or one slice per beat
    / bar — the grid lines that fall inside the region, as fractions of
    it."""
    if mode == "transients":
        return "transients", ""
    g = grid(meta)
    if mode not in ("beats", "bars") or not g["set"]:
        return "", ""
    start, length = span(meta)
    step = g["beat"] if mode == "beats" else g["bar"]
    first = math.ceil((start - g["offset"]) / step - 1e-6)
    lines = []
    t = g["offset"] + first * step
    while t < start + length - 1e-6:
        if t > start + 1e-6:
            lines.append((t - start) / length)
        t += step
    slices = sampler.edge_slices([0.0, *lines, 1.0])
    note = ""
    if len(lines) + 1 > len(slices):
        note = f"The first {len(slices)} {mode} are on the keys (a kit holds 32)."
    if len(slices) < 2:
        return "", ""
    return sampler.format_slices(slices), note


# --- the view: which part of the song the waveform shows ---------------------

VIEW_ACTIONS = ("in", "out", "left", "right", "all", "region", "set")
MAX_ZOOM = 512  # the view is never shorter than a 512th of the song ...
MIN_VIEW_SECONDS = 1.0  # ... or than a second (Ctrl + wheel zooms to a beat)


def view(meta: dict) -> tuple[float, float]:
    """The part of the song on screen as (start, length) in seconds: the
    whole song unless the source was zoomed, always inside it."""
    total = meta["seconds"]
    length = meta["view"]["length"]
    if length <= 0 or length >= total:
        return 0.0, total
    length = max(length, min(total, MIN_VIEW_SECONDS), total / MAX_ZOOM)
    return min(max(0.0, meta["view"]["start"]), total - length), length


def set_view(
    meta: dict, action: str, at: float | None = None, seconds: float | None = None
) -> None:
    """The zoom controls: `in` / `out` halve or double the span (kept on
    the region when it is in sight, else on the middle of what is shown),
    `left` / `right` move it half a screen, `region` frames the region with
    a little room either side, `all` shows the whole song, and `set` is the
    span the page already shows (`at`, `seconds`: Ctrl + wheel, the
    overview's box — labview.js zooms at once and says where it ended)."""
    total = meta["seconds"]
    start, length = view(meta)
    r0, rlen = span(meta)
    if action == "all":
        meta["view"] = {"start": 0.0, "length": 0.0}
        return
    if action == "set":
        if at is None or seconds is None:
            return
        start, length = at, seconds
    elif action in ("in", "out"):
        centre = start + length / 2
        if start <= r0 + rlen / 2 <= start + length:
            centre = r0 + rlen / 2
        length = length / 2 if action == "in" else length * 2
        start = centre - length / 2
    elif action in ("left", "right"):
        start += (-0.5 if action == "left" else 0.5) * length
    elif action == "region":
        length = rlen * 1.25
        start = r0 - rlen * 0.125
    if length >= total:
        meta["view"] = {"start": 0.0, "length": 0.0}
        return
    length = max(length, min(total, MIN_VIEW_SECONDS), total / MAX_ZOOM)
    meta["view"] = {"start": min(max(0.0, start), total - length), "length": length}


# --- drawing -----------------------------------------------------------------


def _x(seconds: float, shown: tuple[float, float]) -> float:
    """A time as an x of the viewBox, within the span on screen."""
    start, length = shown
    if length <= 0:
        return 0.0
    return round(min(max((seconds - start) / length, 0.0), 1.0) * W, 1)


def _outline(col: np.ndarray, height: float) -> str:
    """A column array (0..1) as a closed shape centred on the lane's middle
    line: its top edge left to right, its mirror back — never thinner than
    a hair, so a quiet passage still reads as there."""
    mid = height / 2
    v = np.maximum(np.asarray(col, dtype=np.float64), 0.006) * (mid - 1)
    xs = np.linspace(0, W, len(v))
    top = " ".join(f"{x:.0f},{mid - y:.1f}" for x, y in zip(xs, v, strict=True))
    bot = " ".join(
        f"{x:.0f},{mid + y:.1f}" for x, y in zip(xs[::-1], v[::-1], strict=True)
    )
    return f"{top} {bot}"


def display_gain(pk: np.ndarray) -> float:
    """How much a lane's waveform is drawn up: its loudest moment in the
    whole song to 95 % of the lane (at most x8), so a quiet stem is a shape
    and not a hairline. Drawing only — the meter says the level."""
    top = float(np.max(pk)) if len(pk) else 0.0
    return min(8.0, 0.95 / top) if top > 1e-4 else 1.0


def wave_svg(cols: np.ndarray, meta: dict, label: str, gain: float = 1.0) -> str:
    """One lane over the span on screen: the peaks as the waveform's
    outline and the RMS as its solid body (`cols`: `sources.window_columns`,
    (2, n)), centred and drawn up by `gain` (`display_gain`); the chops as
    shaded spans and the region as a band. The grid is not here: labview.js
    draws the bar and beat lines and the ruler over every lane at once, for
    whatever span the page shows."""
    shown = view(meta)
    cols = np.asarray(cols)
    if cols.ndim == 1:
        cols = np.stack([cols, cols * 0.5])
    cols = np.minimum(cols * gain, 1.0)
    parts = [
        f'<svg class="lab-svg" viewBox="0 0 {W} {H_LANE}" preserveAspectRatio="none"'
        f' role="img" aria-label="{label}">',
        f'<polygon class="lw-wave" points="{_outline(cols[0], H_LANE)}"/>',
        f'<polygon class="lw-rms" points="{_outline(cols[1], H_LANE)}"/>',
    ]
    # the chops (labchops.py): a shaded span each, on every lane
    chosen = selected_chop(meta)
    for c in meta["chops"]:
        if c["start"] < sum(shown) and c["end"] > shown[0]:
            a, b = _x(c["start"], shown), _x(c["end"], shown)
            cls = "lw-chop lw-chop-on" if c["id"] == chosen else "lw-chop"
            parts.append(
                f'<rect class="{cls}" x="{a}" y="0" width="{max(b - a, 1.0):.1f}"'
                f' height="{H_LANE}"/>'
            )
    start, length = span(meta)
    if start < sum(shown) and start + length > shown[0]:
        a, b = _x(start, shown), _x(start + length, shown)
        parts.append(
            f'<rect class="lw-region" x="{a}" y="0" width="{max(b - a, 1.0):.1f}"'
            f' height="{H_LANE}"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def strip_svg(cols: np.ndarray, meta: dict) -> str:
    """The overview strip over the lanes: the whole song's mix, the region
    and the chops on it — the window on what the lanes show is labview.js's
    box over it, dragged to scroll."""
    whole = (0.0, float(meta["seconds"]))
    cols = np.asarray(cols)
    parts = [
        f'<svg class="lab-strip-svg" viewBox="0 0 {W} {H_STRIP}"'
        ' preserveAspectRatio="none" aria-hidden="true">',
        f'<polygon class="lw-wave" points="{_outline(cols[0], H_STRIP)}"/>',
    ]
    for c in meta["chops"]:
        a, b = _x(c["start"], whole), _x(c["end"], whole)
        parts.append(
            f'<rect class="lw-chop" x="{a}" y="0" width="{max(b - a, 1.0):.1f}"'
            f' height="{H_STRIP}"/>'
        )
    start, length = span(meta)
    a, b = _x(start, whole), _x(start + length, whole)
    parts.append(
        f'<rect class="lw-region" x="{a}" y="0" width="{max(b - a, 2.0):.1f}"'
        f' height="{H_STRIP}"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


REGION_LENGTHS = (1, 2, 4, 8, 16)  # the one-press lengths, in bars


def at_x(seconds: float, shown: tuple[float, float]) -> float:
    """A time as a percent of what the lanes show (may fall outside 0..100:
    labview.js places and hides what is out of sight)."""
    return round((seconds - shown[0]) / shown[1] * 100.0, 3) if shown[1] > 0 else 0.0


def region_label(meta: dict) -> str:
    """The region in the words the ruler writes on it: "bars 3–5 · 3 bars",
    or its clock times when it is not on the bar lines."""
    g, r = grid(meta), meta["region"]
    start, length = span(meta)
    if r["mode"] == "bars" and g["set"]:
        first, n = int(round(r["start"])) + 1, max(1, int(round(r["length"])))
        bars = f"bar {first}" if n == 1 else f"bars {first}–{first + n - 1}"
        return f"{bars} · {n} bar{'s' if n > 1 else ''}"
    beats = f" · {round(length / g['beat'], 1):g} beats" if g["set"] else ""
    return f"{clock(start)} – {clock(start + length)}{beats}"


def stem_url(meta: dict, stem: str = "") -> str:
    """The audition of the region as ONE stem reads it (a one-stem make's
    preview: the vocals for a voice, the drums for a beat) — the mix when
    the song is not separated or the stem is not in its set."""
    start, length = span(meta)
    model = meta["stems"]["model"]
    stems = stem if model and stem in sources.stem_names(model) else ""
    return (
        f"/lab/{meta['id']}/region.wav?start={start:.4f}&length={length:.4f}"
        f"&levels=&stems={stems}"
    )


METER_FLOOR_DB = -48.0  # the left end of a lane's meter


def region_meter(pk: np.ndarray, meta: dict, gain_db: float = 0.0) -> dict:
    """A lane's level meter, read off what is stored: the highest of the
    stem's overview peaks (|x|, the whole song in `W` columns) inside the
    region, plus the lane's level, in dBFS. Static and honest — nothing is
    rendered or analysed for it, and it says what the region will send,
    not what is sounding. `pct` is its length on a meter whose left end is
    `METER_FLOOR_DB`; `clip` when the level pushes the peak over full scale."""
    start, length = span(meta)
    total = max(float(meta["seconds"]), 1e-9)
    a = int(np.clip(np.floor(start / total * len(pk)), 0, max(len(pk) - 1, 0)))
    b = int(np.clip(np.ceil((start + length) / total * len(pk)), a + 1, len(pk)))
    top = float(pk[a:b].max()) if len(pk) else 0.0
    if top <= 1e-6:
        return {"db": None, "text": "silent", "pct": 0, "clip": False}
    db = 20.0 * math.log10(top) + gain_db
    pct = round(min(1.0, max(0.0, 1.0 - db / METER_FLOOR_DB)) * 100)
    text = f"{db:+.1f} dBFS".replace("-", "\u2212")
    return {"db": round(db, 1), "text": text, "pct": pct, "clip": db > 0.0}


def clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def source_context(data_dir, meta: dict, session_bpm: float) -> dict:
    """What the panel draws for the selected source."""
    g, an, r = grid(meta), meta["analysis"], meta["region"]
    start, length = span(meta)
    bars = r["mode"] == "bars" and g["set"]
    has_stems = bool(meta["stems"]["model"])
    shown = view(meta)
    zoomed = shown[1] < meta["seconds"] - 1e-6
    whole = sources.overview(data_dir, meta, sources.MIX)

    def lane_cols(stem: str) -> np.ndarray:
        if zoomed:
            return sources.window_columns(data_dir, meta, stem, *shown)
        return whole if stem == sources.MIX else sources.overview(data_dir, meta, stem)

    lv = sources.levels(meta)
    picked = meta["picked"] if has_stems else []
    pk = {name: sources.peaks(data_dir, meta, name) for name in lv}
    lanes = [
        {
            "name": name,
            "picked": name in meta["picked"],
            "level": db,
            "level_text": db_text(db),
            "solo": meta["picked"] == [name],
            "svg": wave_svg(
                lane_cols(name), meta, f"{name} stem", display_gain(pk[name])
            ),
            # the lane's meter: the stem's peak over the region, at its level
            "meter": region_meter(pk[name], meta, db),
        }
        for name, db in lv.items()
    ]
    # the grid labview.js draws (the ruler, the bar and beat lines) and the
    # span the lanes were drawn over; all in seconds of the song
    lanes_data = {
        "total": round(meta["seconds"], 4),
        "beat": round(g["beat"], 6),
        "offset": round(g["offset"], 6),
        "bars": bars_in(meta),
        "per_bar": BEATS_PER_BAR,
        "view_start": round(shown[0], 4),
        "view_seconds": round(shown[1], 4),
        "min_view": round(
            max(min(meta["seconds"], MIN_VIEW_SECONDS), meta["seconds"] / MAX_ZOOM), 4
        ),
        "sel_start": round(start, 4),
        "sel_seconds": round(length, 4),
        "mode": "bars" if bars else "seconds",
    }
    scores = an["key_scores"]
    plan = stretch_plan(meta, session_bpm)
    fitted = length * plan["factor"] if plan.get("possible") else length
    return {
        "id": meta["id"],
        "name": meta["name"],
        "seconds": round(meta["seconds"], 3),
        "duration": clock(meta["seconds"]),
        "grid": g,
        "bpm": round(g["bpm"], 2),
        "bpm_detected": round(an["bpm"], 2),
        "bpm_percent": round(an["confidence"] * 100),
        # "sounds in 3": a waltz, or a 6/8 felt in two (0: no guess, or 4)
        "meter_guess": int(an.get("meter_guess") or 0),
        "meter_percent": round(float(an.get("meter_sure") or 0.0) * 100),
        "key": key_in_force(meta),
        "key_detected": an["key"],
        "key_user": meta["user"]["key"] or "",
        "key_names": sources.KEY_NAMES,
        "key_percent": round(an["key_confidence"] * 100),
        # the relative, named while the tonic is a close call
        "key_alt": scores[1][0]
        if len(scores) > 1 and an["key_margin"] < KEY_CLOSE
        else "",
        "key_scores_alt": scores[1][0] if len(scores) > 1 else "its relative",
        "key_from": an["key_from"],
        "key_tonic": an["key_tonic"],
        "offset": round(g["offset"], 3),
        "bars": bars_in(meta),
        "nudges": NUDGE_BEATS,
        "region": {
            "mode": "bars" if bars else "seconds",
            # the form counts bars from 1
            "start": int(round(r["start"])) + 1 if bars else round(start, 2),
            "length": int(round(r["length"])) if bars else round(length, 2),
            "start_s": round(start, 3),
            "length_s": round(length, 3),
            "from": clock(start),
            "to": clock(start + length),
            "too_long": too_long(meta),
            "label": region_label(meta),
            # where it sits over the lanes, in percent of what they show
            "x": at_x(start, shown),
            "x_end": at_x(start + length, shown),
            "end_s": round(start + length, 4),
        },
        # the one-press lengths that fit the song, from the region's bar
        "lengths": [
            n for n in REGION_LENGTHS if bars and n <= limits.MAX_LAB_REGION_BARS
        ],
        "what": what(meta),
        # what a thing made from the region is called ("vocals bars 9-12")
        "pick_name": pick_name(meta),
        # the one-stem makes' previews: the region as that stem reads it
        "vocals_url": stem_url(meta, "vocals"),
        "drums_url": stem_url(meta, "drums"),
        "stem_names": sources.stem_names(meta["stems"]["model"]) if has_stems else (),
        "svg": wave_svg(
            lane_cols(sources.MIX), meta, "the mix", display_gain(whole[0])
        ),
        "strip": strip_svg(whole, meta),
        "lanes_data": lanes_data,
        # the lane's own audio from a point (a click on a lane plays from there)
        "lane_url": f"/lab/{meta['id']}/region.wav",
        # what the lanes show
        "view": {
            "start": round(shown[0], 4),
            "seconds": round(shown[1], 4),
            "zoom": round(meta["seconds"] / shown[1]) if zoomed else 1,
            "from": clock(shown[0]),
            "to": clock(sum(shown)),
            "can_in": shown[1]
            > max(MIN_VIEW_SECONDS, meta["seconds"] / MAX_ZOOM) + 1e-6,
            "at_start": shown[0] <= 1e-6,
            "at_end": sum(shown) >= meta["seconds"] - 1e-6,
            # the overview's box: what the lanes show, in percent of the song
            "box_x": round(shown[0] / max(meta["seconds"], 1e-9) * 100, 3),
            "box_w": round(min(1.0, shown[1] / max(meta["seconds"], 1e-9)) * 100, 3),
        },
        # the recording's own peak over the region (the mix lane's meter)
        "meter": region_meter(sources.peaks(data_dir, meta, sources.MIX), meta),
        "has_stems": has_stems,
        "stems_model": meta["stems"]["model"],
        # Auto by syllables is offered when the vocals stem is what plays
        "vocals_alone": has_stems and meta["picked"] == ["vocals"],
        "max_chops": limits.MAX_LAB_CHOPS,
        "clean_db": limits.LAB_CLEAN_DB_RANGE,
        "fade_ms": limits.LAB_FADE_MS_RANGE,
        "gain_db": limits.LAB_GAIN_DB_RANGE,
        "chop_rows": [],  # `labchops.rows`, filled by the service's lab_context
        "lanes": lanes,
        "picked": meta["picked"],
        "session_bpm": round(session_bpm, 1),
        "stretch": plan,
        "fitted_seconds": round(fitted, 2),
        "max_region": limits.MAX_CLIP_SECONDS,
        "max_bars": limits.MAX_LAB_REGION_BARS,
        "chops": CHOPS,
        "level_range": limits.LAB_LEVEL_DB_RANGE,
        # the URL names the whole mix (the stems and their levels, in step),
        # so a level moved is another URL and the <audio> loads again
        "audio_url": (
            f"/lab/{meta['id']}/region.wav?start={start:.4f}&length={length:.4f}"
            f"&levels={','.join(f'{lv[s]:g}' for s in picked)}"
            f"&stems={','.join(picked)}"
        ),
    }
