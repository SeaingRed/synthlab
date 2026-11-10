"""The Loops list: every loop, every keys take, every beat, one list.

Saved loops are easy to find again, in a panel that slides out. One ⏺ Rec
makes a take that is both its audio clip and an editable
loop (`loops.py`); a loop the clip hub made from a clip's notes is the same
object, and so is a beat kept from a grid. This is the one listing every
place draws them from:

- the clip library drawer's **Loops** group, at its top (every page);
- Perform's **Loops panel** (region `loops-panel`, a slide-out module: its
  search, sort and filters are the page's, over these rows: loopspanel.js);
- the keyboard dock's strip: this session's, the quick view, with "All
  loops" opening the drawer.

A row is a loop (`kind` "loop"), a keys take no loop names (`kind` "take",
from before one Rec made both, or a take whose loop was deleted: its first
action makes its loop, `routes/takes.py` `_loop_named`) — so nothing
recorded before is lost — or one of the Drums bank's 8 slots (`kind`
"bank": a view of the session's bank, shown as a beat; a slot whose beat a
beat loop already holds is not listed twice). `what` is what it plays:
"notes" or "beat". Newest first, the bank's slots last. Read through the
stores' own caches (`loops.listing`, `keytakes.takes`): no file is opened
that has not moved.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import keytakes, loops, schema
from .cliplib import clean_query, clips_sig
from .words import clock, plural

SESSION_MAX = 6  # the dock's strip: this session's newest, then "N more"


def key_words(root: str, scale: str) -> str:
    """ "A pentatonic minor": a key as a row says it (the root's octave
    dropped, the scale's underscores spaces)."""
    return f"{root.rstrip('-0123456789')} {scale.replace('_', ' ')}"


def when(at: float) -> str:
    """When a row was made, short: "15:06" today, "7 Nov" before (it moves
    at midnight only: a list's watch holds it)."""
    if not at:
        return ""
    t, now = time.localtime(at), time.localtime()
    if t[:3] == now[:3]:
        return time.strftime("%H:%M", t)
    return f"{t.tm_mday} {time.strftime('%b', t)}"


# A row's facts: what it is beside its name, once, as read-out
# cells in a fixed order — every list draws them in aligned columns
# (loop_rows.html `facts`), so a name never repeats them ("Acid bass riff ·
# Am · 132 · 1b  1 bar · 1/4 · 132 bpm · A minor · Acid bass" was one row).
# A cell a row has no value for is drawn empty, holding its column.
FACTS = ("len", "grid", "bpm", "key", "time", "more")


def facts(
    *,
    seconds: float = 0.0,
    bars: float | int | None = None,
    grid: str = "",
    bpm: float | int | None = None,
    root: str = "",
    scale: str = "",
    meter: str = "",
    more: list[str] | tuple[str, ...] = (),
) -> dict:
    """A row's fact cells, `FACTS` -> short text ("" none): its length as a
    clock, its bars, its grid (a step as a note value), its tempo, its key
    as a name says it ("Am", "F#m pent") — and `more` (hits, lanes, a
    meter other than 4/4, the sound when the name does not say it)."""
    if meter and meter != "4/4":
        more = [meter, *more]
    return {
        "time": clock(seconds) if seconds else "",
        "len": plural(bars, "bar") if bars else "",
        "grid": grid,
        "bpm": str(round(bpm)) if bpm else "",
        "key": loops.key_short(root, scale) if root else "",
        "more": " · ".join(m for m in more if m),
    }


def said(f: dict, root: str = "", scale: str = "") -> str:
    """The facts in words, one line (a row's tooltip, the drawer's detail):
    "0:03 · 2 bars · 1/16 · 124 bpm · A minor"."""
    bits = [f["time"], f["len"], f["grid"], f"{f['bpm']} bpm" if f["bpm"] else ""]
    bits += [key_words(root, scale) if root else "", f["more"]]
    return " · ".join(b for b in bits if b)


def _sound_said(sound: str, title: str) -> list[str]:
    """The sound, as a fact, only when the row's name does not say it."""
    return [sound] if sound and sound.casefold() not in title.casefold() else []


def _wavs(data_dir: Path) -> set[str]:
    return {n[:-4] for n, *_ in clips_sig(data_dir) if n.endswith(".wav")}


def _find(r: dict) -> str:
    """What a search reads of a row: its title and name, sound, key (both
    ways: "Am", "a minor"), tempo, kind, meter and tags ("#dark")."""
    bits = [r["title"], r["name"], r.get("sound", ""), r["what"], r["kind"]]
    if r.get("root") and r["what"] in ("notes", "idea"):  # a beat has no key
        bits += [
            loops.key_short(r["root"], r["scale"]),
            key_words(r["root"], r["scale"]),
        ]
    if r.get("bpm"):
        bits.append(f"{r['bpm']} bpm")
    if r.get("meter"):
        bits.append(r["meter"])
    bits += [f"#{t}" for t in r.get("tags", ())]
    if r.get("star"):
        bits.append("★")
    return " ".join(b for b in bits if b).casefold()


def matches(r: dict, q: str) -> bool:
    """Does row `r` hold every word of `q` (a search: "acid am 124",
    "#dark", "beat")? The panel's search (loopspanel.js) reads the same
    words off the row's `data-find`."""
    return all(w in r["find"] for w in q.casefold().split())


def _idea_row(lp: dict) -> dict:
    """An idea (a scene of Perform's launcher kept): what it holds."""
    f = facts(
        bpm=lp["bpm"],
        root=lp["root"],
        scale=lp["scale"],
        meter=lp["meter"],
        more=[plural(lp["notes"], "lane")],
    )
    return {
        **lp,
        "kind": "loop",
        "what": "idea",
        "ref": {"name": lp["name"]},
        "audio": "",
        "seconds": 0.0,
        "facts": f,
        "detail": said(f, lp["root"], lp["scale"]),
        "rates": [],
        "cleanable": False,
        "cleaned": False,
        "fixes": [],
    }


def _section_rows(sections) -> list[dict]:
    """The song's sections (Perform's Loops panel: "sections"), each to
    open as a scene of new slots — a view, as the bank's slots are."""
    return [
        {
            "kind": "section",
            "what": "section",
            "name": sec["name"],
            "title": sec["name"],
            "ref": {"section": sec["id"]},
            "audio": "",
            "seconds": 0.0,
            "at": 0.0,
            "bars": sec["bars"],
            "played": 0,
            "edited": False,
            "star": False,
            "tags": [],
            "facts": facts(bars=sec["bars"], more=[f"song section {i + 1}"]),
            "detail": f"song section {i + 1} · {plural(sec['bars'], 'bar')}",
            "roll": {"d": "", "w": 1, "h": 1},
        }
        for i, sec in enumerate(sections or ())
    ]


def _loop_row(lp: dict, wavs: set, takes: dict) -> dict:
    if lp["kind"] == "idea":
        return _idea_row(lp)
    audio = lp["take"] if lp["take"] in wavs else ""
    # a keys take's ✦ clean-up places its loop (routes/takes.py)
    take = takes.get(lp["take"]) if audio else None
    beat = lp["kind"] == "beat"
    # a take's length as a clock first ("0:03 · 2 bars"): it stops where
    # the hand did, and its grid is its bars rounded up
    seconds = take["seconds"] if take is not None else 0.0
    root, scale = ("", "") if beat else (lp["root"], lp["scale"])
    f = facts(
        seconds=seconds,
        bars=lp["bars"],
        grid=loops.rate_word(lp["meter"], lp["steps"]),
        bpm=lp["bpm"],
        root=root,
        scale=scale,
        meter=lp["meter"],
        more=[plural(lp["notes"], "hit")]
        if beat
        else _sound_said(lp["sound"], lp["title"]),
    )
    return {
        **lp,
        "kind": "loop",
        "what": "beat" if beat else "notes",
        "ref": {"name": lp["name"]},
        "audio": audio,
        "seconds": seconds,
        "facts": f,
        "detail": said(f, root, scale),
        "rates": [] if beat else loops.rates(lp["meter"]),
        "cleanable": take is not None and take["notes"] > 0,
        "cleaned": take is not None and take["cleaned"],
        "fixes": take["fixes"] if take is not None else [],
        # a free loop: ✂ Shape again from the free take it was shaped from
        "shape": lp["free_take"] if lp.get("free_take") in wavs else "",
    }


def _take_row(t: dict) -> dict:
    key = t["key"] or ("", "")
    # a take named by the clock shows what it is, as a loop does: plainly,
    # its facts in their cells
    what = f"{t['sound_name'] or 'Keys'} take"
    title = what if loops.is_auto(t["name"]) else t["name"]
    more = [plural(t["notes"], "note"), "+ beat" if t["beat"] else ""]
    f = facts(
        seconds=t["seconds"],
        bars=round(t["bars"], 1) if t["bars"] else None,
        bpm=t["bpm"],
        root=key[0],
        scale=key[1],
        more=[*_sound_said(t["sound_name"], title), *more],
    )
    return {
        "kind": "take",
        "what": "notes",
        "name": t["name"],
        "title": title,
        "ref": {"take": t["name"]},
        "audio": t["name"],
        "seconds": t["seconds"],
        "at": t["at"],
        "bars": t["bars"],
        "bpm": t["bpm"],
        "root": key[0],
        "scale": key[1],
        "sound": t["sound_name"],
        "played": t["notes"],
        "edited": False,
        "star": False,
        "tags": [],
        "facts": f,
        "detail": said(f, *key),
        "roll": t["roll"],
        # a free take (free record): ✂ Shape opens the shaper on it
        "shape": t["name"] if t.get("free") else "",
    }


def _bank_rows(bank, kept: set, lib=()) -> list[dict]:
    """The Drums bank's filled slots as beats (a view: nothing is copied
    until one is kept — ★, tags or a rename make it a beat loop) — and,
    since the Kit page has no lists of its own, each
    beat of the old beat library (`lib`: its patterns, `presets/drums`) no
    loop or slot holds, the same way (`kind` "lib", `ref {"lib": name}`)."""
    out = []
    picks = [(i, p, {"slot": i}) for i, p in enumerate(bank or ())]
    held = kept | {loops.beat_key(p) for p in bank or () if p}  # a slot's: once
    picks += [(None, p, {"lib": p.get("name", "")}) for p in lib or ()]
    for i, p, ref in picks:
        if not p or loops.beat_key(p) in (kept if i is not None else held):
            continue
        p = schema.conform(p, schema.nodes().PATTERN)
        out.append(
            {
                "kind": "bank" if i is not None else "lib",
                "what": "beat",
                "name": p["name"],
                "title": p["name"],
                "slot": i,
                "ref": ref,
                "audio": "",
                "seconds": 0.0,
                "at": 0.0,
                "bars": p["bars"],
                "steps": p["steps_per_bar"],
                "notes": len(p["steps"]),
                "played": 0,
                "edited": False,
                "star": False,
                "tags": [],
                "facts": facts(
                    bars=p["bars"],
                    more=[
                        plural(len(p["steps"]), "hit"),
                        f"bank slot {i + 1}" if i is not None else "beat library",
                    ],
                ),
                "detail": " · ".join(
                    [
                        f"bank slot {i + 1}" if i is not None else "beat library",
                        plural(p["bars"], "bar"),
                        plural(len(p["steps"]), "hit"),
                    ]
                ),
                "roll": loops.beat_roll(p),
            }
        )
    return out


def _unique_titles(out: list[dict]) -> None:
    """A title shown once: a name the clock gave shows what the loop is,
    and two such can be the same thing ("… 2", the older first)."""
    seen = {r["title"].casefold() for r in out if r["title"] == r["name"]}
    for r in sorted(out, key=lambda x: (x["at"], x["name"])):
        if r["title"] == r["name"]:
            continue
        base, n = r["title"], 1
        while r["title"].casefold() in seen:
            n += 1
            r["title"] = f"{base} {n}"
        seen.add(r["title"].casefold())


def rows(data_dir: Path, bank=(), sections=None, lib=()) -> list[dict]:
    """Every loop, every take no loop names and every slot of `bank` (the
    session's Drums bank) and beat of `lib` (the old beat library's
    patterns) no beat loop holds, newest first — each `{kind,
    what, name, title, ref, audio, seconds, at, bars, detail, roll, star,
    tags, find, rank, when, ...}`; `ref` is what a row's actions post
    (`{"name": loop}`, `{"take": clip}`, `{"slot": n}`, `{"lib": name}`
    or `{"section": id}`). `sections` (Perform's Loops panel: the song's,
    else None) also lists the ideas — scenes of its launcher kept — and
    those sections, to open as a scene; the other lists hold neither."""
    wavs = _wavs(data_dir)
    takes = {t["name"]: t for t in keytakes.takes(data_dir)}
    out, named, kept = [], set(), set()
    for lp in loops.listing(data_dir):
        named.add(lp["take"])
        named.add(lp.get("free_take"))  # a free take is its free loop's
        kept.add(lp.pop("beat_key"))
        if lp["kind"] == "idea" and sections is None:
            continue
        out.append(_loop_row(lp, wavs, takes))
    out += [_take_row(t) for t in takes.values() if t["name"] not in named]
    _unique_titles(out)
    out.sort(key=lambda r: (-r["at"], r["name"]))
    out += _bank_rows(bank, kept, lib)
    out += _section_rows(sections)
    for r in out:
        r["find"] = _find(r)
        root = r.get("root") if r["what"] in ("notes", "idea") else ""  # a beat: no key
        r["rank"] = loops.key_rank(root, r.get("scale") or "") if root else -1
        r["when"] = when(r["at"])
    return out


def titles(data_dir: Path) -> dict[str, str]:
    """Every loop's name -> what the Loops lists call it (`rows`' title: a
    name the clock gave shows what the loop is, "… 2" told apart) — the
    one display name everywhere that names a loop (the edit banner, the
    launcher's slots, the toasts), never the file's clock name."""
    return {r["name"]: r["title"] for r in rows(data_dir) if r["kind"] == "loop"}


def context(
    data_dir: Path,
    view: str,
    *,
    q: str = "",
    session: bool = False,
    editing: tuple[str, ...] = (),
    bank=(),
    sections=None,
    lib=(),
) -> dict:
    """What a Loops list draws on `view`'s page: every row (`rows`), those
    holding every word of `q` (the drawer's search: `matches`), or —
    `session`, the dock's strip — this studio's own since it started, the
    newest `SESSION_MAX`, and how many more; `total`, every loop and take
    the store holds (the rail button's count: the bank's slots and the
    song's sections are not loops); `tags`, every tag in use. `editing`:
    the loops open on the grids (`StudioState.loop_edit`, one a lane),
    marked."""
    found = rows(data_dir, bank, sections, lib)
    total = sum(r["kind"] in ("loop", "take") for r in found)  # whatever the search
    tags = sorted({t for r in found for t in r["tags"]})
    q = clean_query(q)
    if q:
        found = [r for r in found if matches(r, q)]
    more = 0
    if session:
        found = [r for r in found if r["at"] >= keytakes.SESSION_START]
        more, found = max(0, len(found) - SESSION_MAX), found[:SESSION_MAX]
    for r in found:
        r["earlier"] = r["at"] < keytakes.SESSION_START
        r["editing"] = r["kind"] == "loop" and r["name"] in editing
    return {
        "view": view,
        "rows": found,
        "count": len(found),
        "total": total,
        "beats": sum(r["what"] == "beat" for r in found),
        "tags": tags,
        "more": more,
        "editing": editing,
        "session": session,
        "loop_bars": loops.BAR_CHOICES,
    }


def watch(ctx: dict) -> tuple:
    """A Loops list's watch value from its context: every row as drawn.
    A loop open on a grid is drawn by its name, its audio and "editing"
    alone (loop_rows.html: what its notes are is Perform's grid, and its
    Quantise is the grid's banner), so a stroke on the grid — which writes
    that loop (`StudioState.mirror_loop_edit`) — sends no list again; the
    lists catch up when the edit ends. (A slot of Perform's launcher that
    plays is such a loop: one open on its lane.)"""
    return tuple(
        (r["name"], "editing", r["audio"], r["kind"], r["title"], r["star"], r["tags"])
        if r["editing"]
        else tuple(sorted(map(repr, r.items())))
        for r in ctx["rows"]
    ) + (ctx["more"], ctx["editing"])
