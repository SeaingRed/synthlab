"""Long audio: a whole stem of a Sample Lab source under the song.

The song model says where a long item sits (`song_model.pass_cuts`: one
**cut** per item per pass); this module is the other half — the files. It
is the only place a render reaches into the source store, and it reaches
in by range: `read` decodes one pass's samples of each stem and nothing
else, so no render ever holds a whole stem.

- `read(data_dir, cut, n)`: the cut's audio for a pass `n` samples long.
  Each stem times its level, summed in float — never `sources.read_sum`,
  whose "a boosted sum is scaled back under full scale" is decided per
  read and would step the level from one pass to the next. An item's own
  first and last `LONG_FADE_SECONDS` are faded (a stem entered mid-phrase
  clicks); a seam between two passes is not: the next pass reads the next
  sample. Audio that is not on disk is silence, never an error.
- `keyed(data_dir, cuts)`: what a pass's render key carries for its cuts
  — the cut itself (source id = a content hash, model id, stems, levels,
  sample positions, gain) and each file's size on disk, None when it is
  missing. So a source deleted or separated again is another key, and
  the same song on the same audio is the same key.
- `status` / `overview` / `rows`: what the Song page draws.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..core import SR
from . import limits, sources
from . import song_model as sm


def _files(data_dir: Path, source: str, model: str, stems) -> list[tuple[str, Path]]:
    """(stem name, its FLAC) for a cut's stems — the mix itself when it
    names none. Empty when the source, the stem set or a stem is not on
    disk: a mix of three stems with one missing is not that mix."""
    names = list(stems) if model else []
    out = []
    for name in names or [sources.MIX]:
        path = sources.stem_path(data_dir, source, name, model)
        if path is None or not path.is_file():
            return []
        out.append((name, path))
    return out


def keyed(data_dir, cuts) -> list:
    """`cuts` as a render key holds them (see the module docstring)."""
    seen: dict[tuple, list] = {}
    out = []
    for cut in cuts:
        ident = (cut["source"], cut["model"], tuple(cut["stems"]))
        if ident not in seen:
            files = _files(Path(data_dir), *ident) if data_dir is not None else []
            seen[ident] = [[n, p.stat().st_size] for n, p in files] or None
        out.append({**cut, "files": seen[ident]})
    return out


def _fade(out: np.ndarray, a: int, b: int) -> None:
    """Raised-cosine ramps over the item's own first and last samples
    (`a`, `b`: its extent relative to this buffer), in place."""
    k = int(limits.LONG_FADE_SECONDS * SR)
    k = min(k, max(0, (b - a) // 2))
    if k <= 0:
        return
    ramp = 0.5 - 0.5 * np.cos(np.pi * (np.arange(k) + 0.5) / k)
    n = len(out)
    lo, hi = max(a, 0), min(a + k, n)
    if hi > lo:
        out[lo:hi] *= ramp[lo - a : hi - a, None]
    lo, hi = max(b - k, 0), min(b, n)
    if hi > lo:
        out[lo:hi] *= ramp[::-1][lo - (b - k) : hi - (b - k), None]


def read(data_dir, cut: dict, n: int) -> np.ndarray:
    """One cut over a pass of `n` samples, (n, 2) float64 (module
    docstring). Samples the source does not have — before its start, past
    its end, or all of it when it is not on disk — are silence."""
    out = np.zeros((n, 2))
    lo, hi = max(0, int(cut["a"])), min(n, int(cut["b"]))
    files = _files(Path(data_dir), cut["source"], cut["model"], cut["stems"])
    if hi <= lo or not files:
        return out
    first = int(cut["frm"]) + lo  # the source sample at out[lo]
    skip = max(0, -first)
    want = hi - lo - skip
    if want <= 0:
        return out
    try:
        sf = sources._sf()
        for name, path in files:
            level = 10.0 ** (sources.level_db(cut["levels"].get(name)) / 20.0)
            with sf.SoundFile(str(path)) as f:
                if f.samplerate != SR or f.channels != 2 or first + skip >= f.frames:
                    continue
                f.seek(first + skip)
                got = f.read(want, dtype="float32", always_2d=True)
            out[lo + skip : lo + skip + len(got)] += got * level
    except Exception:  # noqa: BLE001 - a damaged file is silence, not a 500
        return np.zeros((n, 2))
    _fade(out, int(cut["a"]), int(cut["b"]))
    gain = float(cut["gain"])
    return out * gain if gain != 1.0 else out


def under(data_dir, cuts, n: int) -> np.ndarray | None:
    """Every cut of a pass, summed; None when it plays no long audio."""
    if not cuts:
        return None
    out = np.zeros((n, 2))
    for cut in cuts:
        out += read(data_dir, cut, n)
    return out


# --- what the page shows ------------------------------------------------------------


def status(data_dir, item: dict) -> str:
    """ "" when the item's audio is on disk, else why it is silent, in
    words for its row."""
    if _files(Path(data_dir), item["source"], item["model"], item["stems"]):
        return ""
    if sources.read_meta(Path(data_dir), item["source"]) is None:
        return "missing — import this song in the Lab again"
    return "missing — separate this song in the Lab again"


def what(item: dict) -> str:
    return " + ".join(item["stems"]) if item["stems"] else "the recording"


def from_source(data_dir, sid: str, model: str, stems, levels: dict | None) -> dict:
    """A long item's source fields for stems of a source in the Lab: the
    reference, the source's length and tempo, and `start` at the Lab
    grid's first bar line, so the stem's bar 1 lands on a bar line of the
    song. Raises `sm.SongError` (a toast) for a source or a stem set that
    is not there."""
    from . import lab

    meta = sources.read_meta(Path(data_dir), sid)
    if meta is None:
        raise sm.SongError("That song is not in the Lab any more.")
    names = [s for s in sources.stem_names(model) if s in stems] if model else []
    if (stems and not names) or not _files(Path(data_dir), sid, model, names):
        raise sm.SongError("Those stems are not on disk — separate the song first.")
    grid = lab.grid(meta)
    levels = {s: sources.level_db((levels or {}).get(s)) for s in names}
    return {
        "source": sid,
        "model": model if names else "",
        "stems": names,
        "levels": levels,
        "name": f"{meta['name']} · {' + '.join(names) if names else 'mix'}",
        "start": round(grid["offset"], 6) if grid["set"] else 0.0,
        "bpm": round(grid["bpm"], 3) if grid["set"] else 0.0,
        "seconds": meta["seconds"],
    }


W = 1200  # the stored peaks' columns (sources._PEAK_COLUMNS)


def _peaks(data_dir, item: dict) -> np.ndarray | None:
    """The item's whole-source overview: its stems' stored peaks, each at
    its level, the largest a column (None when it is not on disk)."""
    meta = sources.read_meta(Path(data_dir), item["source"])
    if meta is None or status(data_dir, item):
        return None
    meta = sources.with_model(meta, item["model"]) if item["model"] else meta
    cols = [
        sources.peaks(Path(data_dir), meta, s)
        * 10.0 ** (sources.level_db(item["levels"].get(s)) / 20.0)
        for s in (item["stems"] or [sources.MIX])
    ]
    return np.clip(np.max(cols, axis=0), 0.0, 1.0)


def _wave(pk: np.ndarray | None, seconds: float, t0: float, t1: float) -> str:
    """An SVG path (viewBox 0 0 100 20) of the source's `t0..t1` seconds."""
    if pk is None or seconds <= 0 or t1 <= t0:
        return ""
    cols = 64
    edges = np.linspace(t0, t1, cols + 1) / seconds * len(pk)
    top = []
    for a, b in zip(edges, edges[1:], strict=False):
        a, b = int(max(0, a)), int(min(len(pk), max(b, a + 1)))
        top.append(float(pk[a:b].max()) if b > a and a < len(pk) else 0.0)
    xs = [100.0 * (i + 0.5) / cols for i in range(cols)]
    up = " ".join(f"{x:.1f},{10 - 9 * v:.1f}" for x, v in zip(xs, top, strict=True))
    down = " ".join(
        f"{x:.1f},{10 + 9 * v:.1f}"
        for x, v in zip(reversed(xs), reversed(top), strict=True)
    )
    return f"M{up} L{down}Z"


def rows(data_dir, song: dict) -> dict:
    """Everything the Song page draws for long audio:

    - `items`: one per item — itself, plus `status` ("" or why it is
      silent), `what` (its stems), `bars` / `end_bar` (the timeline bars it
      plays, 1-based, None when its bar is past the song's end), `cut`
      (True when the song ends before it does), `own_bars` (its whole
      length in song bars), `off` (True when its tempo is not the song's);
    - `parts`: {(section id, track id): [{id, name, left, width, path,
      head, tail, at, bars, skip, status}]} — the strip each section's
      audio cell shows: the item's share of the section's WHOLE span (all
      its repeats), as percentages of the cell, and that share's waveform.
      `head` / `tail`: the strip the item starts / ends in (its name; the
      handles its edges are dragged by). `at` / `bars`: the item's own
      first timeline bar and how many it plays; `skip`: how many of them
      lie before this strip — what a drag needs to keep the grab offset."""
    items = sm.long_items(song)
    if not items:
        return {"items": [], "parts": {}}
    spans = sm.long_spans(song)
    bpm = song["bpm"]
    bar = sm.bar_sample(1, bpm)
    out, parts = [], {}
    bounds, at = [], 0
    for sec in song["sections"]:
        n = sum(n for _s, n in sm.section_timeline(song, sec))
        bounds.append((sec["id"], at, at + n))
        at += n
    for x in items:
        st = status(data_dir, x)
        pk = _peaks(data_dir, x)
        span = spans.get(x["id"])
        whole = (x["seconds"] or 0.0) - x["start"]
        whole = min(whole, x["length"]) if x["length"] else whole
        row = {
            **x,
            "status": st,
            "what": what(x),
            "bars": None,
            "end_bar": None,
            "cut": False,
            "own_bars": max(1, int(round(whole * SR / bar))) if whole > 0 else 0,
            # the length the user set, in song bars (0: to its end)
            "set_bars": int(round(x["length"] * SR / bar)) if x["length"] else 0,
            "off": bool(x["bpm"]) and abs(x["bpm"] - bpm) > 0.005,
        }
        if span is not None:
            a, b = span
            row["bars"] = x["at_bar"] + 1
            row["end_bar"] = x["at_bar"] + max(1, int(np.ceil((b - a) / bar - 1e-6)))
            row["cut"] = whole > 0 and (b - a) < int(round(whole * SR)) - 1
            for sid, s0, s1 in bounds:
                lo, hi = max(a, s0), min(b, s1)
                if hi <= lo:
                    continue
                t0 = x["start"] + (lo - a) / SR
                parts.setdefault((sid, x["track_id"]), []).append(
                    {
                        "id": x["id"],
                        "name": x["name"],
                        "left": round(100.0 * (lo - s0) / (s1 - s0), 3),
                        "width": round(100.0 * (hi - lo) / (s1 - s0), 3),
                        "path": _wave(pk, x["seconds"], t0, t0 + (hi - lo) / SR),
                        "head": lo == a,
                        "tail": hi == b,
                        "at": x["at_bar"],
                        "bars": row["end_bar"] - x["at_bar"],
                        "skip": int(round((lo - a) / bar)),
                        "status": st,
                        "mute": x["mute"],
                    }
                )
        out.append(row)
    return {"items": out, "parts": parts}


def sig(data_dir, song: dict) -> tuple:
    """What `rows` reads beyond the song itself, as a watch value: whether
    each item's audio is on disk."""
    return tuple((x["id"], status(data_dir, x)) for x in sm.long_items(song))


def delete_note(use: dict | None) -> str:
    """What the Lab's delete confirm adds for a source the song plays
    (`StudioState.long_usage`'s entry): "" when nothing does."""
    if not use:
        return ""
    names, steps = use["names"], use["undo"]
    said = []
    if names:
        listed = ", ".join(f"“{n}”" for n in names[:4])
        said.append(f"the song plays it as {listed}, which will go silent")
    if steps:
        said.append(
            f"{steps} of the song's undo steps hold{'s' if steps == 1 else ''} it"
        )
    return " Note: " + " and ".join(said) + "."
