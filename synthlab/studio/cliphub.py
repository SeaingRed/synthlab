"""The clip hub: every clip and sample in one place (`/view/clips`).

The clip library drawer (`cliplib.py`) is the quick picker on every page;
this is the page where a clip is looked at: its waveform, its kind and
source, length, tempo and key (from its meta when it says, else measured
here), where the session uses it — and where it is listened to, tuned,
sent and made into something else (`clipmake.py`, `clipmorph.py`).

What it lists, by item reference (`ref`), each checked before it reaches
a path:

    clip:<name>               the clip store (`clips.py`)
    sample:<16 hex>           the sampler's content-addressed store
    stem:<source>:<model>:<stem>   a separated Lab song's stem (listen,
                              download, open in the Lab: a stem is minutes
                              long, so nothing here cuts or makes from it)

Pure functions over the stores and what the session hands in, plus a few
LRU caches keyed by the file's (path, mtime, size) — the waveform and the
measured tempo / key of a clip are read once per version of its file,
never by a region's watch. Nothing here touches the session.

**A meta is a file anyone can edit**: what the list reads off a clip's
meta goes through `clips.clean_meta` (`list_clips`) and `cliplib`, and the
numbers drawn here are checked again.

**Tune** (`clean_tune`, `tune`) is the hub's one audio edit: trim, reverse,
pitch in semitones and cents (length kept: resampled, then stretched back
with the sampler's WSOLA), tape rate (pitch and speed together) and gain.
It never writes over a clip by itself: a tuned clip is kept as a new clip
(`free_clip_name`) or written over its own with `update` — the routes
hold the confirm and the undo (`HubSession`).
"""

from __future__ import annotations

import copy
import math
import os
import re
import zlib
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote

import numpy as np

from .. import sampler
from ..audio import load as load_wav
from ..core import SR
from . import cliplib, limits, sources
from .clips import clip_dir, clip_safe, list_clips, meta_number
from .lru import LRU

KINDS = (
    ("all", "Everything"),
    ("lab", "Lab chops"),
    ("voice", "Voice takes"),
    ("vocoder", "Vocoder"),
    ("jam", "Jams"),
    ("bounce", "Bounces"),
    ("transition", "Risers / impacts"),
    ("other", "Other clips"),
    ("sample", "Sampler samples"),
    ("stem", "Lab stems"),
)
KIND_LABELS = dict(KINDS)
SORTS = (
    ("name", "Name"),
    ("newest", "Newest"),
    ("longest", "Longest"),
    ("shortest", "Shortest"),
    ("kind", "Kind"),
)
MAX_QUERY_CHARS = cliplib.MAX_QUERY_CHARS
LIST_COLUMNS = 64  # a row's waveform
BENCH_COLUMNS = 240  # the bench's waveform

_REF = re.compile(r"(clip|sample|stem):(.{1,200})\Z", re.S)
_STEM = re.compile(r"([0-9a-f]{8,64}):([A-Za-z0-9_.-]{1,40}):([a-z]{1,16})\Z")


# --- item references ----------------------------------------------------------


@dataclass(frozen=True)
class Ref:
    kind: str  # "clip" | "sample" | "stem"
    name: str  # the clip's name, the sample's id, the stem's name
    source: str = ""  # a stem's Lab source id
    model: str = ""  # a stem's set

    @property
    def text(self) -> str:
        if self.kind == "stem":
            return f"stem:{self.source}:{self.model}:{self.name}"
        return f"{self.kind}:{self.name}"


def parse_ref(raw) -> Ref | None:
    """An item reference from a form or a query, or None. A clip's name is
    sanitized as every clip path is (`clip_safe`); a sample's id must be
    the store's own shape; a stem's source an id the source store takes."""
    if not isinstance(raw, str):
        return None
    m = _REF.match(raw.strip())
    if not m:
        return None
    kind, rest = m.groups()
    if kind == "clip":
        name = clip_safe(rest)
        return Ref("clip", name) if rest.strip() else None
    if kind == "sample":
        return Ref("sample", rest) if sampler.valid_id(rest) else None
    s = _STEM.match(rest)
    if not s or not sources.valid_id(s.group(1)):
        return None
    return Ref("stem", s.group(3), s.group(1), s.group(2))


def row_id(ref: str) -> str:
    """A DOM id for an item: names are any text, so a checksum of the ref."""
    return f"hub-{zlib.crc32(ref.encode('utf-8')):08x}"


# --- reading an item's audio ---------------------------------------------------


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def clip_path(data_dir: Path, name: str) -> Path | None:
    """The clip's WAV, inside the store, or None."""
    root = clip_dir(data_dir).resolve()
    path = (root / f"{clip_safe(name)}.wav").resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def version(data_dir: Path, ref: Ref) -> str | None:
    """What a cache of this item's audio is keyed by: a clip's file as it
    is now (path, mtime, size), a sample's id (it IS its content). None
    when the item is gone."""
    if ref.kind == "sample":
        return ref.name if sampler.get(ref.name) is not None else None
    if ref.kind != "clip":
        return None
    path = clip_path(data_dir, ref.name)
    st = _stat(path) if path else None
    return f"{path}|{st[0]}|{st[1]}" if st else None


def _stereo(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return np.nan_to_num(x[:, :2], nan=0.0, posinf=0.0, neginf=0.0)


def read(data_dir: Path, ref: Ref) -> tuple[np.ndarray, int] | None:
    """(stereo float audio, its rate) of a clip or a sample, else None — a
    stem is never read here (minutes long: the Lab is where it is cut)."""
    if ref.kind == "sample":
        x = sampler.get(ref.name)
        return (_stereo(x), SR) if x is not None else None
    if ref.kind != "clip":
        return None
    path = clip_path(data_dir, ref.name)
    if path is None:
        return None
    try:
        x, sr = load_wav(str(path))
    except Exception:  # noqa: BLE001 - a damaged or foreign file draws flat
        return None
    if not limits.MIN_SR <= sr <= limits.MAX_SR:
        return None
    return _stereo(x)[: int(limits.MAX_STORED_CLIP_SECONDS * sr) + sr], int(sr)


def at_sr(audio: np.ndarray, sr: int, to: int = SR) -> np.ndarray:
    """`audio` at the engine's rate (the analyses and the makers read SR)."""
    if sr == to or not len(audio):
        return audio
    from scipy.signal import resample_poly

    g = math.gcd(int(to), int(sr))
    return resample_poly(audio, to // g, sr // g, axis=0)


# --- the waveform ---------------------------------------------------------------

_PEAKS: LRU[np.ndarray] = LRU(1024)


def _columns(x: np.ndarray, columns: int) -> np.ndarray:
    m = np.max(np.abs(x), axis=1) if len(x) else np.zeros(1)
    edges = np.linspace(0, len(m), columns + 1).astype(int)
    out = np.array(
        [
            float(m[a:b].max()) if b > a else 0.0
            for a, b in zip(edges, edges[1:], strict=False)
        ]
    )
    return np.clip(np.nan_to_num(out), 0.0, 1.0)


def peaks(data_dir: Path, ref: Ref, columns: int = BENCH_COLUMNS) -> np.ndarray:
    """`columns` peaks of |x|, 0..1 (zeros when the item is gone), read once
    per version of the file."""
    ver = version(data_dir, ref)
    if ver is None:
        return np.zeros(columns)
    key = f"{ver}|{columns}"
    hit = _PEAKS.get(key)
    if hit is None:
        got = read(data_dir, ref)
        hit = _columns(got[0], columns) if got else np.zeros(columns)
        _PEAKS.put(key, hit)
    return hit


def wave_path(pk: np.ndarray) -> str:
    """The peaks as one closed SVG path in a `0 0 N 2` box (mirrored about
    y=1): drawn in `currentColor`, stretched by the box it sits in."""
    n = len(pk)
    if not n:
        return ""
    top = " ".join(
        f"{i + 0.5:g} {1 - min(1.0, p) * 0.96:.3f}" for i, p in enumerate(pk)
    )
    bottom = " ".join(
        f"{i + 0.5:g} {1 + min(1.0, p) * 0.96:.3f}"
        for i, p in reversed(list(enumerate(pk)))
    )
    return f"M0 1L{top}L{n} 1L{bottom}Z"


# --- what a clip measures to: tempo and key ----------------------------------------

_FACTS: LRU[dict] = LRU(512)  # (an LRU locks itself)


def measured(data_dir: Path, ref: Ref) -> dict | None:
    """What `measure` found for this version of the item, if it was
    measured (no file is read: a region's watch and the list ask)."""
    ver = version(data_dir, ref)
    return _FACTS.get(ver) if ver else None


def measure(data_dir: Path, ref: Ref) -> dict:
    """The item's tempo and key as the Lab finds a song's
    (`analysis.tempo`, `analysis.detect_key`), measured once per version of
    its file: `{bpm, bpm_conf, key, key_conf, seconds}` (bpm 0 / key ""
    when there is none — audio under two seconds has no tempo)."""
    from ..analysis import detect_key, tempo

    ver = version(data_dir, ref)
    if ver is None:
        return {"bpm": 0.0, "bpm_conf": 0.0, "key": "", "key_conf": 0.0, "seconds": 0.0}
    hit = _FACTS.get(ver)
    if hit is not None:
        return hit
    got = read(data_dir, ref)
    facts = {"bpm": 0.0, "bpm_conf": 0.0, "key": "", "key_conf": 0.0, "seconds": 0.0}
    if got is not None:
        x = at_sr(*got)
        facts["seconds"] = round(len(x) / SR, 2)
        grid = tempo(x)
        if grid.bpm > 0:
            facts["bpm"], facts["bpm_conf"] = float(grid.bpm), float(grid.confidence)
            facts["beat_offset"] = float(grid.beat_offset)
            facts["downbeat_offset"] = float(grid.downbeat_offset)
        key = detect_key(x)
        if key.tonic:
            facts["key"], facts["key_conf"] = key.name, float(key.confidence)
    _FACTS.put(ver, facts)
    return facts


# --- tune ----------------------------------------------------------------------

TUNE_DEFAULTS = {
    "semis": 0,
    "cents": 0,
    "gain_db": 0.0,
    "start": 0.0,
    "end": 0.0,  # 0: to the end
    "reverse": False,
    "rate": 1.0,
}
TUNE_RANGES = {
    "semis": (-24, 24),
    "cents": (-100, 100),
    "gain_db": (-24.0, 12.0),
    "start": (0.0, limits.MAX_CLIP_SECONDS),
    "end": (0.0, limits.MAX_CLIP_SECONDS),
    "rate": (0.5, 2.0),
}


def _num(raw, default: float) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) and abs(v) < 1e9 else default


def clean_tune(raw) -> dict:
    """The tune settings a form or a query asks for, each parsed, finite
    and clamped (`TUNE_RANGES`); anything else is its default."""
    raw = raw if hasattr(raw, "get") else {}
    out = dict(TUNE_DEFAULTS)
    for k, (lo, hi) in TUNE_RANGES.items():
        v = min(hi, max(lo, _num(raw.get(k), TUNE_DEFAULTS[k])))
        out[k] = int(round(v)) if k in ("semis", "cents") else round(v, 3)
    out["reverse"] = raw.get("reverse") in ("on", "1", "true", True)
    if out["end"] and out["end"] <= out["start"]:
        out["end"] = 0.0
    return out


def is_identity(t: dict) -> bool:
    return t == TUNE_DEFAULTS or (
        not t["semis"]
        and not t["cents"]
        and not t["gain_db"]
        and not t["start"]
        and not t["end"]
        and not t["reverse"]
        and t["rate"] == 1.0
    )


def tune_query(t: dict) -> str:
    """The settings as the query string the player's audio URL carries
    (only what moved)."""
    parts = []
    for k, v in t.items():
        if k not in TUNE_DEFAULTS or v == TUNE_DEFAULTS[k]:
            continue
        parts.append(f"{k}=on" if isinstance(v, bool) else f"{k}={v:g}")
    return "&".join(parts)


def _resample(x: np.ndarray, ratio: float) -> np.ndarray:
    """`x` read `ratio` times as fast (fewer samples for ratio > 1)."""
    from scipy.signal import resample_poly

    if abs(ratio - 1.0) < 1e-6 or len(x) < 8:
        return x
    f = Fraction(1.0 / ratio).limit_denominator(240)
    up, down = max(1, f.numerator), max(1, f.denominator)
    return resample_poly(x, up, down, axis=0)


def tune(audio: np.ndarray, sr: int, t: dict) -> np.ndarray:
    """`audio` (stereo at `sr`) as `t` asks: trimmed to start..end, reversed,
    pitched by semitones + cents with its length kept, played at `rate`
    (tape: pitch and speed together), at `gain_db`; never longer than a
    clip may be (`limits.MAX_CLIP_SECONDS`)."""
    x = np.asarray(audio, dtype=np.float64)
    n = len(x)
    a = min(n, int(t["start"] * sr))
    b = min(n, int(t["end"] * sr)) if t["end"] else n
    x = x[a:b] if b > a else x[:0]
    if t["reverse"]:
        x = x[::-1]
    shift = t["semis"] + t["cents"] / 100.0
    if shift and len(x) > 64:
        ratio = 2.0 ** (shift / 12.0)
        x = sampler.time_stretch(_resample(x, ratio), ratio, sr)
    if t["rate"] != 1.0 and len(x) > 8:
        x = _resample(x, t["rate"])
    x = x * 10.0 ** (t["gain_db"] / 20.0)
    return np.ascontiguousarray(x[: int(limits.MAX_CLIP_SECONDS * sr)])


_TUNED: LRU[bytes] = LRU(6)


def tuned_wav(data_dir: Path, ref: Ref, t: dict) -> bytes | None:
    """The item tuned, as WAV bytes — what the bench's player plays —
    memoised by (version, settings): a seek asks again."""
    from ..audio import wav_bytes

    ver = version(data_dir, ref)
    if ver is None:
        return None
    key = f"{ver}|{sorted(t.items())}"
    hit = _TUNED.get(key)
    if hit is None:
        got = read(data_dir, ref)
        if got is None:
            return None
        audio, sr = got
        hit = wav_bytes(tune(audio, sr, t) if not is_identity(t) else audio, sr)
        _TUNED.put(key, hit)
    return hit


def tuned_meta(meta: dict, t: dict, origin: str) -> dict:
    """A tuned copy's meta: where it came from and how, and the clock it
    still keeps — a bounce's bpm moves with the rate, and its bars hold
    only while nothing changed its length."""
    out = {k: copy.deepcopy(meta[k]) for k in ("source", "lab") if k in meta}
    bpm = meta.get("bpm")
    if meta_number(bpm) and 0 < bpm < 1000:
        out["bpm"] = round(float(bpm) * t["rate"], 3)
        if not t["start"] and not t["end"] and meta_number(meta.get("bars")):
            out["bars"] = meta["bars"]
    out["tune"] = {**t, "from": origin[:80]}
    return out


# --- where the session uses a sample ----------------------------------------------


def _samples_of(patch) -> list[str]:
    """The sound's sample, and its zone map's (a multi-sample instrument)."""
    get = patch.get if isinstance(patch, dict) else lambda k: getattr(patch, k, "")
    sid, zones = get("sampler_sample"), get("sampler_zones")
    out = [sid] if isinstance(sid, str) and sampler.valid_id(sid) else []
    if isinstance(zones, str):
        out += [z for z in sampler.zone_samples(zones) if z not in out]
    return out


def sample_usage(state) -> dict[str, list[str]]:
    """`{sample id: [where]}`: the edited sound, the other layers and the
    song's tracks whose sampler plays it (as its sample or a zone's)."""
    out: dict[str, list[str]] = {}

    def note(sids: list[str], where: str) -> None:
        for sid in sids:
            out.setdefault(sid, []).append(where)

    note(_samples_of(state.patch), f"the edited sound “{state.patch.name}”")
    for i, lay in enumerate(state.layers):
        if i != state.active_layer and isinstance(lay, dict):
            note(_samples_of(lay.get("patch")), f"layer {i + 1}")
    for t in state.song.get("tracks") or []:
        if isinstance(t, dict):
            note(
                _samples_of(t.get("patch")),
                f"song track “{str(t.get('name', ''))[:40]}”",
            )
    return out


# --- the list ---------------------------------------------------------------------


def _sample_listing(data_dir: Path) -> list[tuple[str, int, int]]:
    root = Path(data_dir) / "samples"
    try:
        with os.scandir(root) as it:
            found = [(e.name[:-4], e.stat()) for e in it if e.name.endswith(".wav")]
    except OSError:
        return []
    return sorted(
        (sid, st.st_mtime_ns, st.st_size) for sid, st in found if sampler.valid_id(sid)
    )


def samples_sig(data_dir: Path) -> tuple:
    """The sample store's listing (names, mtimes, sizes): a watch value."""
    return tuple(_sample_listing(data_dir))


def facts_sig(data_dir: Path, listing: tuple) -> tuple:
    """Which of the listed clips have been measured, and to what — the
    list draws a measured tempo / key — without opening a file."""
    root = clip_dir(data_dir).resolve()
    out = []
    for name, mtime, size in listing:
        if name.endswith(".wav"):
            f = _FACTS.get(f"{root / name}|{mtime}|{size}")
            if f is not None:
                out.append((name, f.get("bpm"), f.get("key")))
    return tuple(out)


def _detail_numbers(meta: dict, facts: dict | None) -> dict:
    """bpm / key as the row says them: the meta's when it states them (a
    bounce's clock, a Lab chop's key), else what was measured (`~`)."""
    out = {"bpm": "", "key": ""}
    bpm = meta.get("bpm")
    lab = meta.get("lab") if isinstance(meta.get("lab"), dict) else {}
    if meta_number(bpm) and 0 < bpm < 1000:
        out["bpm"] = f"{bpm:.0f}"
    elif meta_number(lab.get("source_bpm")) and 0 < lab["source_bpm"] < 1000:
        out["bpm"] = f"{lab['source_bpm']:.0f}"
    elif facts and facts.get("bpm"):
        out["bpm"] = f"~{facts['bpm']:.0f}"
    key = lab.get("key")
    if isinstance(key, str) and 0 < len(key) <= 12:
        out["key"] = key
    elif facts and facts.get("key"):
        out["key"] = f"~{facts['key']}"
    return out


@dataclass
class Item:
    ref: str
    kind: str  # one of KINDS' keys (never "all")
    name: str
    source: str
    seconds: float
    mtime: int = 0
    bpm: str = ""
    key: str = ""
    used: str = ""
    used_by: list = field(default_factory=list)
    lab_href: str | None = None
    download: str = ""
    audio: str = ""  # what ▶ plays
    wave: str = ""  # the row's waveform path

    @property
    def id(self) -> str:
        return row_id(self.ref)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


def _clip_source(clip: dict, kind: str, label: str) -> str:
    src = clip.get("source")
    if kind == "lab":
        return label
    if kind == "bounce" and isinstance(src, str):
        return f"bounce of “{src[:40]}”"
    if isinstance(src, str) and src:
        return src[:60]
    return "the clip store"


def items(
    data_dir: Path,
    usage: dict | None = None,
    samples_used: dict | None = None,
    *,
    with_waves: bool = True,
) -> list[Item]:
    """Every clip, sample and Lab stem as a list row (unfiltered, unsorted)."""
    usage = usage or {}
    samples_used = samples_used or {}
    stamps = {}
    for name, mtime, _size in cliplib.clips_sig(data_dir):
        if name.endswith(".wav"):
            stamps[name[:-4]] = mtime
    alive = {sid for sid, _sets in cliplib._scan(data_dir)}
    out: list[Item] = []
    for clip in list_clips(data_dir):
        name = clip["name"]
        ref = Ref("clip", name)
        key, label = cliplib.kind_of(clip)
        kind = "lab" if key.startswith("lab") else key
        sid, chop = cliplib.lab_ref(clip)
        href = None
        if sid:
            href = (
                f"/view/lab?source={sid}" + (f"&chop={chop}" if chop else "")
                if sid in alive
                else ""
            )
        nums = _detail_numbers(clip, measured(data_dir, ref))
        out.append(
            Item(
                ref=ref.text,
                kind=kind,
                name=name,
                source=_clip_source(clip, kind, label),
                seconds=float(clip.get("seconds") or 0.0),
                mtime=stamps.get(name, 0),
                bpm=nums["bpm"],
                key=nums["key"],
                used=cliplib.where_used_short(usage.get(name)),
                lab_href=href,
                download=f"/clip/{quote(name)}.wav",
                audio=f"/clip/{quote(name)}.wav",
                wave=wave_path(peaks(data_dir, ref, LIST_COLUMNS))
                if with_waves
                else "",
            )
        )
    for sid_, mtime, _size in _sample_listing(data_dir):
        ref = Ref("sample", sid_)
        where = samples_used.get(sid_, [])
        out.append(
            Item(
                ref=ref.text,
                kind="sample",
                name=f"sample {sid_[:8]}",
                source="the sampler's store",
                seconds=round(sampler.seconds(sid_), 2) if with_waves else 0.0,
                mtime=mtime,
                used=", ".join(where),
                used_by=where,
                audio=f"/hub/audio.wav?item={quote(ref.text)}",
                wave=wave_path(peaks(data_dir, ref, LIST_COLUMNS))
                if with_waves
                else "",
            )
        )
    for src in cliplib.stem_sets(data_dir):
        for s in src["sets"]:
            for stem in s["stems"]:
                ref = Ref("stem", stem, src["id"], s["model"])
                out.append(
                    Item(
                        ref=ref.text,
                        kind="stem",
                        name=f"{src['name']} · {stem}",
                        source=f"Lab stems ({s['count']}-stem set)",
                        seconds=0.0,
                        lab_href=f"/view/lab?source={src['id']}",
                        download=f"/lab/{src['id']}/stem/{stem}.flac?model={s['model']}",
                        audio=f"/lab/{src['id']}/stem/{stem}.wav?model={s['model']}",
                    )
                )
    return out


def clean_query(q) -> str:
    return cliplib.clean_query(q)


def view(
    found: list[Item], q: str = "", kind: str = "all", sort: str = "name"
) -> list[Item]:
    """`found` filtered by name (`q`, any case) and kind, then sorted."""
    needle = clean_query(q).casefold()
    kind = kind if kind in KIND_LABELS else "all"
    sort = sort if sort in dict(SORTS) else "name"
    rows = [
        it
        for it in found
        if (kind == "all" or it.kind == kind)
        and (not needle or needle in it.name.casefold())
    ]
    by_name = lambda it: (it.name.casefold(), it.name)  # noqa: E731
    rows.sort(key=by_name)
    if sort == "newest":
        rows.sort(key=lambda it: -it.mtime)
    elif sort == "longest":
        rows.sort(key=lambda it: -it.seconds)
    elif sort == "shortest":
        rows.sort(key=lambda it: it.seconds)
    elif sort == "kind":
        order = [k for k, _ in KINDS]
        rows.sort(key=lambda it: order.index(it.kind) if it.kind in order else 99)
    return rows


def counts(found: list[Item]) -> dict[str, int]:
    out = {"all": len(found)}
    for it in found:
        out[it.kind] = out.get(it.kind, 0) + 1
    return out


# --- the hub's own runtime: what an Apply undoes, what an Update replaced -----------


@dataclass
class Made:
    """One Apply from the hub: the session before and after it (what Undo
    puts back, and how it knows nothing moved since), the clips it cut
    into the store, and what it was."""

    label: str
    before: dict
    after: dict
    clips: list[str] = field(default_factory=list)
    # the loop a note part was kept as (loops.py): Undo deletes it again
    loop: str = ""


@dataclass
class Overwrite:
    """A clip written over by "Update this clip": its file and meta as they
    were (Undo writes them back), and the file as the update left it (how
    Undo knows nothing wrote over it since)."""

    label: str
    name: str
    wav: bytes
    meta: bytes | None
    stamp: tuple | None = None


class HubSession:
    """The hub's runtime state, never saved (after a restart an Apply is
    history, as an agent turn is): its undo steps — an Apply (`Made`) or an
    Update (`Overwrite`) — newest last, at most `MAX_STEPS`. An Update keeps
    the clip's old bytes in memory (a clip is at most 30 s: ~5 MB)."""

    MAX_STEPS = 4

    def __init__(self) -> None:
        self.steps: list[Made | Overwrite] = []

    def push(self, step: Made | Overwrite) -> None:
        self.steps.append(step)
        del self.steps[: -self.MAX_STEPS]

    @property
    def last(self) -> Made | Overwrite | None:
        return self.steps[-1] if self.steps else None
