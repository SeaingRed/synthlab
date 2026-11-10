"""The clip library: what the drawer on every page draws.

The clip store (`clips.py`) is a flat folder of named WAVs. The drawer
(`fragments/clip_library.html`, region `clip-library`) shows it by where
each clip came from, which its meta says:

    Lab          one group per Lab song and the stems the clip plays
    Voice takes  a kept Voice-lab take, a mic take recorded on the Song page
    Vocoder      a kept Voice-lab result (the take vocoded over a carrier)
    Takes        ⏺ Rec in the keyboard dock (`keytakes.py`): drawn as
                 the Loops at the drawer's top (looplib.py), not here
    Jams         ⏺ resample on the Loops page (it was ⏺ jam), and the
                 keyboard dock's recordings from before takes: source
                 "keys", no `kept`
    Bounces      ⏺ Bounce (the loop's bpm and bars, its sound as `source`)
    Risers / impacts   generated on the Song page
    Other        anything else — an imported bundle's, a hand-made file
    Takes, not kept   a take from before one Rec kept it (the same)

and, last, the separated songs in the Sample Lab's source store with the
stems each holds: links to download them, never a copy in the clip store.

Pure functions on what `clips.list_clips` and the source store hand over;
nothing here touches the session. **A meta is a file anyone can edit**:
`clips.clean_meta` has already held the top-level keys to their types,
and everything read from inside `lab` here is checked again — a wrong
shape groups the clip under Other, it never raises.

What a separated source holds is `sources.stem_sets`' to say (a source
keeps both the 4- and the 6-stem set, and its meta names only the current
one); `stem_sets` here is the drawer's view of it. `sources_sig` is the
watch value for the same thing — folder names only, no file opened, so it
can run around every request: it moves whenever that listing can.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from . import sources
from .clips import clip_dir, list_clips, meta_number
from .files import dir_sig
from .lru import LRU

MAX_QUERY_CHARS = 60  # the search box's `q`, cut before it is compared
_LAB_PREFIX = "lab: "
_CHOP_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")
_STEMS_DIR = "stems-"

# the fixed groups, in drawer order (the Lab's come first, one per song)
GROUPS = (
    ("voice", "Voice takes"),
    ("vocoder", "Vocoder"),
    ("take", "Takes"),
    ("jam", "Jams"),
    ("bounce", "Bounces"),
    ("transition", "Risers / impacts"),
    ("other", "Other"),
    ("take-new", "Takes, not kept"),
)
# the groups a keys take is filed in: the drawer lists them as Loops
LOOP_GROUPS = frozenset({"take", "take-new"})


def clean_query(q) -> str:
    return q.strip()[:MAX_QUERY_CHARS] if isinstance(q, str) else ""


# --- which group a clip is in ---------------------------------------------------


def _lab_stems(lab: dict) -> list[str]:
    stems = lab.get("stems")
    if not isinstance(stems, list):
        return []
    return [s for s in sources.ALL_STEMS if s in stems]


def _lab_song(clip: dict) -> str:
    source = clip.get("source")
    if isinstance(source, str) and source.startswith(_LAB_PREFIX):
        return source[len(_LAB_PREFIX) :].strip()[:80] or "Lab"
    return "Lab"


def lab_ref(clip: dict) -> tuple[str, str]:
    """(source id, chop id) a Lab clip's meta names, each "" when it is not
    there or not an id. Both shapes of `lab` are read: a kept chop's
    (`source` + `chop`) and a region sent as a clip (`source` alone)."""
    lab = clip.get("lab")
    if not isinstance(lab, dict):
        return "", ""
    sid, chop = lab.get("source"), lab.get("chop")
    sid = sid if isinstance(sid, str) and sources.valid_id(sid) else ""
    chop = chop if isinstance(chop, str) and _CHOP_ID.fullmatch(chop) else ""
    return sid, chop


def kind_of(clip: dict) -> tuple[str, str]:
    """(group key, heading) for a clip as `list_clips` returns it."""
    source = clip.get("source")
    lab = clip.get("lab")
    if isinstance(lab, dict):
        stems = _lab_stems(lab)
        label = f"{_lab_song(clip)} · {' + '.join(stems) if stems else 'mix'}"
        return f"lab:{label.casefold()}", label
    if isinstance(clip.get("transition"), dict) or source == "transition":
        return "transition", ""
    # a kept Voice-lab result: read before the bounce rule, since one made
    # over a tempo-locked carrier states bpm and bars too
    if isinstance(clip.get("vocoder"), dict) or source == "vocoder":
        return "vocoder", ""
    # a keys take (the keyboard dock's ⏺ Rec, keytakes.py) carries its bpm
    # and bars too: read before the bounce rule. Every take says whether it
    # is kept; a "keys" clip that does not is the dock's recording from
    # before takes (`POST /clip/upload`, as ⏺ resample posts): audio and no
    # notes, filed with the jams it was recorded like
    if source == "keys":
        kept = clip.get("kept")
        if isinstance(kept, bool):
            return ("take", "") if kept else ("take-new", "")
        return "jam", ""
    # the live vocal chain's ⏺ (vocal.py): the processed take and the dry
    # one beside it carry the loop's clock too, and are voice takes
    if source in ("vocal", "vocal dry"):
        return "voice", ""
    # a bounce carries the loop's clock, and its sound's name as the source
    # (which may be any text: it is read after the shapes above, before the
    # words below)
    if isinstance(source, str) and "bpm" in clip and "bars" in clip:
        return "bounce", ""
    if isinstance(source, str) and source.startswith(_LAB_PREFIX):
        # a drum hit cut for a kit: it names its song and nothing else
        label = _lab_song(clip)
        return f"lab:{label.casefold()}", label
    # a take of the voice: kept from the Voice page, recorded by the live
    # talkbox, or treated in the booth
    if source in ("voice take", "take", "talkbox", "booth") or isinstance(
        clip.get("booth"), dict
    ):
        return "voice", ""
    if source == "jam":
        return "jam", ""
    return "other", ""


def _detail(clip: dict) -> str:
    """ "1.5s · 124 bpm · 2 bars": what the row says after the name."""
    parts = [f"{clip.get('seconds', 0.0):g}s"]
    bpm, bars = clip.get("bpm"), clip.get("bars")
    if meta_number(bpm) and 0 < bpm < 1000:
        parts.append(f"{bpm:.0f} bpm")
    if meta_number(bars) and 0 < bars <= 1024:
        parts.append(f"{bars:g} bar{'' if bars == 1 else 's'}")
    return " · ".join(parts)


# what an undo step that holds the clip does, by what is about to happen to it
_UNDO_DELETE = "Ctrl+Z would bring it back, silent"
_UNDO_UPDATE = "Ctrl+Z brings the take back with the new audio"


def where_used(use: dict | None, undo_note: str = _UNDO_DELETE) -> str:
    """Where the session holds a clip, in words ("" when nowhere): one
    entry of `StudioState.clip_usage` — its takes in the song, its places
    on the clip lane, drum rows, saved revert points, and how many of the
    song's undo / redo steps hold it. The one wording of it: deleting a
    clip (`_used`) and the Lab's "Update clip" (`update_note`) both say
    this about the same clip."""
    if not use:
        return ""

    def n(count: int, one: str, many: str) -> str:
        return f"{count} {one if count == 1 else many}"

    where = []
    if use.get("song"):
        where.append(n(use["song"], "take in the song", "takes in the song"))
    if use.get("lane"):
        where.append(
            n(use["lane"], "place on the clip lane", "places on the clip lane")
        )
    if use.get("drums"):
        where.append(n(use["drums"], "drum row", "drum rows"))
    if use.get("saved"):
        where.append(n(use["saved"], "saved revert point", "saved revert points"))
    if use.get("undo"):
        steps = n(use["undo"], "undo step", "undo steps")
        where.append(f"{steps} of the song ({undo_note})")
    return ", ".join(where)


def where_used_short(use: dict | None) -> str:
    """`where_used` as a list row says it ("" when nowhere): what plays it
    — takes, the clip lane, drum rows, revert points — without the undo
    ring's count, which is how a delete is weighed, not what a clip is
    for. Held only by undo steps: "in the song's undo history"."""
    if not use:
        return ""
    plays = where_used({k: v for k, v in use.items() if k != "undo"})
    return plays or ("in the song's undo history" if use.get("undo") else "")


def update_note(use: dict | None) -> str:
    """`where_used` for a clip about to be written over (the Lab's "Update
    clip"): an undo step keeps the clip by name, so it plays the new audio."""
    return where_used(use, _UNDO_UPDATE)


def _used(name: str, use: dict | None) -> str:
    """What deleting a clip in use asks first ("" when nothing uses it):
    where the session holds it, and how many of the song's undo / redo
    steps would bring a take of it back."""
    used = where_used(use)
    if not used:
        return ""
    return f"“{name}” is in use: {used}. Deleting it leaves silence there. Delete it?"


def groups(clips: list[dict], q: str = "", usage: dict | None = None, alive=()) -> list:
    """The drawer's groups, in order, each `{key, label, clips}`; a clip is
    `{name, detail, used, lab}` (`lab`: its "Open in Lab" link, None for a
    clip that names no Lab source, `{"href": ""}` when that song is gone).
    `q` keeps the clips whose name contains it, whatever the case."""
    needle = clean_query(q).casefold()
    usage = usage or {}
    found: dict[str, dict] = {}
    # by name as it reads ("bounce" before "bounce 2"), not as its file sorts
    for clip in sorted(clips, key=lambda c: (c["name"].casefold(), c["name"])):
        name = clip["name"]
        if needle and needle not in name.casefold():
            continue
        key, label = kind_of(clip)
        row = {
            "name": name,
            "detail": _detail(clip),
            "used": _used(name, usage.get(name)),
        }
        sid, chop = lab_ref(clip)
        row["lab"] = None
        if sid:  # a kit hit names its song and no source: no link to offer
            href = ""
            if sid in alive:
                href = f"/view/lab?source={sid}" + (f"&chop={chop}" if chop else "")
            row["lab"] = {"href": href}
        found.setdefault(key, {"key": key, "label": label, "clips": []})
        found[key]["clips"].append(row)
    out = sorted(
        (g for k, g in found.items() if k.startswith("lab:")), key=lambda g: g["key"]
    )
    for g in out:
        g["key"] = "lab"
    for key, label in GROUPS:
        if key in found:
            out.append({**found[key], "label": label})
    return out


# --- the source store: separated songs --------------------------------------------


# (sources folder | source id) -> (the folder's mtime, its stem folders or
# None when it holds no source). A watch asks on every request: it lists
# the store once and opens a source's folder only when that folder's own
# mtime has moved (a stem set renamed in, a meta rewritten).
_SETS: LRU[tuple] = LRU(256)


def _folders(path: str) -> tuple[str, ...] | None:
    try:
        with os.scandir(path) as it:
            names = {e.name for e in it}
    except OSError:
        return None
    if f"{sources.MIX}.flac" not in names or "meta.json" not in names:
        return None
    return tuple(
        sorted(n for n in names if n.startswith(_STEMS_DIR) and ".tmp" not in n)
    )


def _scan(data_dir: Path, fresh: bool = False) -> list[tuple[str, tuple[str, ...]]]:
    """(source id, its `stems-<model>` folder names) for every source
    folder holding a mix: names only, nothing opened. A stem set is renamed
    into place whole (`sources.save_stems`), so a folder that is there is a
    finished set; its temp name is not one. `fresh` reads every folder (a
    render); without it a folder whose mtime has not moved is not read
    again (a watch)."""
    root = os.path.join(str(data_dir), "sources")
    try:
        with os.scandir(root) as it:
            found = sorted(
                (e.name, e.stat().st_mtime_ns)
                for e in it
                if sources.valid_id(e.name) and e.is_dir()
            )
    except OSError:
        return []
    out = []
    for sid, stamp in found:
        key = f"{root}|{sid}"
        hit = None if fresh else _SETS.get(key)
        if hit is None or hit[0] != stamp:
            hit = (stamp, _folders(os.path.join(root, sid)))
            _SETS.put(key, hit)
        if hit[1] is not None:
            out.append((sid, hit[1]))
    return out


# which Lab sources a store's clips name, by the store's listing: the metas
# are read again only when the listing (names, mtimes, sizes) has moved
_REFS: LRU[tuple] = LRU(8)


# inside `sig_memo`: each store's listing, read once
_SIG_MEMO: ContextVar[dict | None] = ContextVar("clips_sig_memo", default=None)


def clips_sig(data_dir: Path) -> tuple:
    """The clip store's listing — every file's name, mtime and size
    (`files.dir_sig`) — as `service.regions._clips_sig` takes it. Inside
    `sig_memo` it is read once."""
    memo, key = _SIG_MEMO.get(), str(data_dir)
    if memo is not None and key in memo:
        return memo[key]
    sig = dir_sig(clip_dir(data_dir))
    if memo is not None:
        memo[key] = sig
    return sig


@contextmanager
def sig_memo():
    """Answer each `clips_sig` once inside the block — for readers that
    change nothing (`StudioService.watch_page`: the clip library's, the
    hub's, the drum rack's and the takes' watches each read the listing).
    The `is_dirty` memo's twin (`StudioState.dirty_memo`)."""
    if _SIG_MEMO.get() is not None:
        yield
        return
    token = _SIG_MEMO.set({})
    try:
        yield
    finally:
        _SIG_MEMO.reset(token)


def _clip_sources(data_dir: Path, listing: tuple | None) -> frozenset[str]:
    listing = clips_sig(data_dir) if listing is None else listing
    return _REFS.by_sig(
        str(data_dir),
        listing,
        lambda: frozenset(lab_ref(c)[0] for c in list_clips(data_dir)) - {""},
    )


def sources_sig(data_dir: Path, listing: tuple | None = None) -> tuple:
    """A watch value for what the drawer reads from the source store: the
    separated songs with their stem sets (the Stems group), and which of
    the songs a clip came from are still there ("Open in Lab"). A song that
    is neither — imported, not separated, no clip made from it — is not in
    it, so importing one re-sends nothing. `listing` is the clip store's
    (`clips_sig`), when the caller has it already."""
    scan = _scan(data_dir)
    named = _clip_sources(data_dir, listing)
    return (
        tuple((sid, sets) for sid, sets in scan if sets),
        tuple(sid for sid, _ in scan if sid in named),
    )


def stem_sets(data_dir: Path) -> list[dict]:
    """Every separated source, by name: `{id, name, sets}`, each set
    `{model, stems, count}`, the set the source shows first. The drawer's
    view of `sources.stem_sets`, which is the one listing of what a source
    holds — the sets the download routes serve."""
    out = []
    for src in sources.stem_sets(data_dir):
        sets = [
            {"model": model, "stems": list(names), "count": len(names)}
            for model, names in src["sets"].items()
        ]
        sets.sort(key=lambda s: (s["model"] != src["model"], s["model"]))
        out.append({"id": src["id"], "name": src["name"], "sets": sets})
    return sorted(out, key=lambda s: (s["name"].casefold(), s["id"]))


# --- placing on the song ----------------------------------------------------------


def place_context(song: dict, section: str = "", at_bar="", track: str = "") -> dict:
    """The Song page's "Place into" bar: the sections, the chosen one
    (`section`, else the first), its bars and the audio tracks. None when
    the song has no section to place into."""
    secs = [
        {"id": s["id"], "name": s["name"], "bars": s["bars"]} for s in song["sections"]
    ]
    if not secs:
        return {}
    chosen = next((s for s in secs if s["id"] == section), secs[0])
    try:
        bar = int(at_bar)
    except (TypeError, ValueError):
        bar = 0
    tracks = [
        {"id": t["id"], "name": t["name"]}
        for t in song["tracks"]
        if t["kind"] == "audio"
    ]
    return {
        "sections": secs,
        "section": chosen["id"],
        "bars": chosen["bars"],
        "at_bar": min(max(0, bar), max(0, chosen["bars"] - 1)),
        "tracks": tracks,
        "track": track if any(t["id"] == track for t in tracks) else "",
    }


def place_sig(song: dict) -> tuple:
    """What `place_context` reads off the song, as a watch value."""
    return (
        [(s["id"], s["name"], s["bars"]) for s in song["sections"]],
        [(t["id"], t["name"]) for t in song["tracks"] if t["kind"] == "audio"],
    )


def library(
    data_dir: Path,
    view: str,
    song: dict,
    usage: dict,
    *,
    q: str = "",
    section: str = "",
    at_bar="",
    track: str = "",
    clips: list[dict] | None = None,
) -> dict:
    """Everything `clip_library.html` draws, for `view`'s page."""
    clips = list_clips(data_dir) if clips is None else clips
    q = clean_query(q)
    scan = _scan(data_dir, fresh=True)
    stems = [s for s in stem_sets(data_dir) if q.casefold() in s["name"].casefold()]
    found = groups(clips, q, usage, {sid for sid, _ in scan})
    return {
        "count": len(clips),
        # the clips that are not a loop's audio (a keys take is one of the
        # Loops): the rail button's "· clips"
        "clips": sum(kind_of(c)[0] not in LOOP_GROUPS for c in clips),
        "q": q,
        # a keys take is one of the Loops (looplib.py, drawn above the
        # groups): its audio and its loop are one row there, not two
        "groups": [g for g in found if g["key"] not in LOOP_GROUPS],
        "stems": stems,
        "view": view,
        "place": place_context(song, section, at_bar, track) if view == "song" else {},
    }
