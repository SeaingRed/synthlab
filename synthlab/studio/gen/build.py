"""Laying a song down: a style's starter, the working loop arranged into a
song, and one part rolled again.

    start(style, seed)            -> Built(song, clips, notes)
    arrange(song, section, seed)  -> Built: that section shaped into a song
    reroll(song, track, seed)     -> the names of the tracks it rewrote
    vary(song, track, op, amount) -> busier / sparser, in place
    write(song, track, kind, ...) -> one generator onto one track

Everything is pure and deterministic for its seed — ids included (crc32 of
the seed and a counter, never `secrets`), so the same seed is the same
song dict in another process. What is made is ordinary song model: tracks
with a role and a readable name, pool patterns (each carrying the `gen`
that wrote it, so it can be rolled again), sections with repeats, spans,
automation lanes and takes. Nothing here renders: a starter's transition
clips are named (`clip_base`) and listed in `Built.clips`, and the route
renders them into the clip store (`routes/song_start.py`).
"""

from __future__ import annotations

import copy
import random
import zlib
from dataclasses import dataclass, field

from .. import beatgen, limits, meterops, recipes, schema
from .. import song_model as sm
from ..clips import clip_safe
from ..curves import GAIN, GAIN_UNITY, fx_target
from ..drumfactory import DRUM_KITS
from ..fxrack import MAX_UNITS, clean_chain
from . import chordtrack, styles
from . import drums as dr
from . import notes as nt

PATTERN_BARS = 8  # a generated part is eight bars: an 8-bar progression fits
ROLE_GAIN = {
    "bass": 1.0,
    "lead": 0.75,
    "vocal": 0.8,
    "chords": 0.7,
    "arp": 0.6,
    "pad": 0.5,
}
TAKE_GAIN = {
    "riser": 0.6,
    "impact": 0.7,
    "sweep": 0.55,
    "swell": 0.6,
    "subdrop": 0.8,
    "bed": 1.0,
}
# the roles "From my loop" gives a loop's lanes, lowest first
_BY_HEIGHT = ("bass", "chords", "lead", "arp")


@dataclass
class Built:
    """What a build made: the song, the transition clips it places
    ({clip name: the generator's settings}) and notes for the toast."""

    song: dict
    clips: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


class _Ids:
    """8-hex ids from the seed: the same song for the same seed."""

    def __init__(self, salt: str, taken=()):
        self.salt, self.n, self.taken = salt, 0, set(taken)

    def __call__(self) -> str:
        while True:
            self.n += 1
            i = f"{zlib.crc32(f'{self.salt}:{self.n}'.encode()):08x}"
            if i not in self.taken:
                self.taken.add(i)
                return i


def part_seed(seed: int, part: str) -> int:
    """A part's own seed from the song's: (seed, part) by crc32."""
    return zlib.crc32(f"{int(seed)}:{part}".encode()) % limits.SEED_MODULO


def clip_base(spec: dict) -> str:
    """A transition clip's name from its settings — the one
    `/song/section/{id}/transition` gives the same settings."""
    return clip_safe(
        f"{spec['kind']}-{spec['direction']}-{spec['bars']}bar"
        f"-b{round(spec['brightness'] * 100)}-t{round(spec['tail'] * 100)}"
        f"-{spec['bpm']:g}bpm"
    )


def _take_spec(kind: str, direction: str, bars: int, bpm: float) -> dict:
    fits = [
        b
        for b in (1, 2, 4, 8)
        if b <= bars and b * 240.0 / bpm <= limits.MAX_CLIP_SECONDS
    ]
    return {
        "kind": kind,
        "direction": direction,
        "bars": max(fits, default=1),
        "brightness": 0.6,
        "tail": 0.4,
        "bpm": float(bpm),
    }


# --- drums ----------------------------------------------------------------------


def drum_lane(
    pipeline, seed: int, kit: dict | None = None, src: dict | None = None
) -> dict:
    """A style's drums variant: its pipeline of `gen/drums.py` calls."""
    lane = src
    for op, *args in pipeline:
        if op == "beat":
            lane = dr.beat(args[0], seed, args[1], vel=(kit or {}).get("vel"))
        elif op == "kit":
            pat = kit["pattern"]
            base = {
                "bars": pat["bars"],
                "steps": pat["steps_per_bar"],
                "drums": pat["steps"],
                "drum_vel": dict(kit.get("vel") or {}),
            }
            lane = dr.tile(
                dr._lane(
                    base["bars"],
                    base["steps"],
                    base["drums"],
                    {int(k): v for k, v in base["drum_vel"].items()},
                ),
                args[0],
            )
        elif op == "fill":  # args[2]: voices that keep playing under it
            lane = dr.fill(
                lane, seed, args[0], args[1], args[2] if len(args) > 2 else ()
            )
        elif op == "thin":
            lane = dr.thin(lane, keep=args[0])
        elif op == "build":
            lane = dr.build(args[0], seed)
        elif op == "tile":
            lane = dr.tile(lane, args[0])
        elif op == "drop":  # a voice out from bar `args[1]` (0-based) on
            edge = args[1] * lane["steps"]
            cells = [c for c in lane["drums"] if not (c[0] == args[0] and c[1] >= edge)]
            lane = dr._same(lane, cells)
    return lane


# --- one part -------------------------------------------------------------------


def note_lane(song: dict, gen: dict, bars: int, kick) -> dict:
    """The lane a notes pattern's `gen` writes: its generator in its
    register, on its part's stream, then the variant's thinning or
    thickening."""
    params = dict(gen.get("params") or {})
    part = str(params.pop("part", gen["kind"]))
    register = str(params.pop("reg", nt.KIND_REGISTER.get(gen["kind"], "bass")))
    pair = params.pop("pair", None)
    busy, thin = params.pop("busy", None), params.pop("thin", None)
    params.pop("var", None)
    ctx = nt.context(
        song,
        None,
        bars=bars,
        kick=kick,
        register=register,
        seed=int(gen.get("seed", 0)),
        role=part,
    )
    if gen["kind"] == "bass_rolling":
        lane = nt.bass_rolling(
            ctx, str(params.get("figure", "roll")), pops=pair != "sub"
        )
    else:
        lane = nt.generate(gen["kind"], ctx, **params)
    if thin:
        lane = nt.sparser(ctx, lane, float(thin))
    if busy:
        lane = nt.busier(ctx, lane, float(busy))
    return lane


def _kick_of(song: dict) -> tuple[int, ...]:
    """The kick's steps for a song's generators: its style's, else the
    sixteenths its busiest beat's kick lands on."""
    style = ((song.get("gen") or {}).get("params") or {}).get("style")
    if style in styles.STYLES:
        return tuple(styles.STYLES[style]["kick"])
    best: set[int] = set()
    for p in song["patterns"].values():
        if p["kind"] == "drums":
            got = dr.kick_steps(p)
            if len(got) > len(best):
                best = got
    return tuple(sorted(best)) or (0, 4, 8, 12)


# --- a starter ------------------------------------------------------------------


def start(
    style: str,
    seed: int,
    bpm: float | None = None,
    root: str | None = None,
    scale: str | None = None,
    variant: str = "standard",
) -> Built:
    """A whole song in `style`: key and tempo, the chord track, tracks with
    their sounds and roles, one pattern family a part, sections from the
    style's shape, and the shape's moves. `seed` decides everything that is
    drawn; `bpm`, `root` and `scale` override the style's."""
    st = styles.STYLES[style]
    seed = int(seed) % limits.SEED_MODULO
    r = random.Random(zlib.crc32(f"{seed}:start:{style}".encode()))
    key = r.choice(st["keys"])
    degrees, chord_bars = r.choice(st["progressions"])
    lo, hi = limits.BPM_RANGE
    tempo = min(hi, max(lo, float(bpm))) if bpm else st["bpm"]
    song = sm.new_song(tempo, root or key[0], scale or key[1])
    song["drum_swing"], song["swing"] = st["drum_swing"], st["swing"]
    song["title"] = f"{st['label']} {seed}"
    song["chords"] = chordtrack.from_degrees(degrees, chord_bars)
    song["gen"] = {
        "kind": "start",
        "seed": seed,
        "params": {"style": style, "variant": variant},
    }
    ids = _Ids(f"{seed}:{style}:{variant}")
    kit = DRUM_KITS[st["kit"]]
    rows = list(styles.shape_of(style, variant))
    notes: list[str] = []
    # held to the song's length cap: repeats come off the longest sections
    while (
        sum(x["bars"] * x["repeats"] for x in rows) * 240.0 / tempo
        > limits.MAX_SONG_SECONDS
    ):
        longest = max(
            range(len(rows)), key=lambda i: rows[i]["bars"] * rows[i]["repeats"]
        )
        if rows[longest]["repeats"] <= 1:
            raise sm.SongError(
                f"At {tempo:g} bpm this song would pass the "
                f"{limits.MAX_SONG_SECONDS:.0f} s cap: raise the tempo."
            )
        rows[longest] = {**rows[longest], "repeats": rows[longest]["repeats"] - 1}
        notes = ["Shortened to fit the length cap at this tempo."]

    drums_tid = ids()
    tracks = [
        {
            "id": drums_tid,
            "kind": "drums",
            "name": "Drums",
            "role": "drums",
            "drum_params": copy.deepcopy(kit["params"]),
            "fx": clean_chain(copy.deepcopy(list(st.get("drums_fx", ())))),
        }
    ]
    patterns: dict[str, dict] = {}
    color = iter(range(10_000))

    def add_pattern(kind, name, lane, gen, fam, var) -> str:
        pid = ids()
        patterns[pid] = {
            "id": pid,
            "kind": kind,
            "name": name[: limits.MAX_TITLE_CHARS],
            "color": sm.PALETTE[next(color) % len(sm.PALETTE)],
            **lane,
            "gen": gen,
            "fam": fam,
            **({"var": var} if var in sm.PATTERN_VARIANTS else {}),
        }
        return pid

    # drums: one pattern a variant the shape uses
    drum_fam, drum_pid = ids(), {}
    used = [x["drums"] for x in rows if x["drums"]]
    for n, var in enumerate(dict.fromkeys(used)):
        s = part_seed(seed, f"drums:{var}")
        gen = {"kind": "drums", "seed": s, "params": {"style": style, "var": var}}
        # a beat that ends in a fill says so (`var`): the checklist counts it
        # as something changing every pass
        fills = any(op[0] == "fill" for op in st["drums"][var])
        letter = "fill" if fills else sm.PATTERN_VARIANTS[min(n, 3)]
        drum_pid[var] = add_pattern(
            "drums",
            f"Beat · {var}",
            drum_lane(st["drums"][var], s, kit),
            gen,
            drum_fam,
            letter,
        )

    # synth tracks: the recipe's sound, the parts the shape plays
    tid_of, pid_of = {}, {}
    for t in st["tracks"]:
        played = [x["play"][t["key"]] for x in rows if t["key"] in x["play"]]
        if not played:
            continue
        entry = recipes.get_recipe(t["sound"])
        tid = tid_of[t["key"]] = ids()
        chain = clean_chain(copy.deepcopy(entry["main_fx"]) + copy.deepcopy(t["fx"]))
        tracks.append(
            {
                "id": tid,
                "kind": "synth",
                "name": t["name"],
                "role": t["role"],
                "gain": t["gain"],
                "patch": {**copy.deepcopy(entry["patch"]), **t["patch"]},
                "curves": copy.deepcopy(entry["curves"]),
                "fx": chain,
            }
        )
        fam = ids()
        for var in dict.fromkeys(played):
            kind, params = t["parts"][var]
            part = "bass" if params.get("pair") else t["key"]
            gen = {
                "kind": kind,
                "seed": part_seed(seed, part),
                "params": {**params, "part": part, "reg": t["register"], "var": var},
            }
            lane = note_lane(song, gen, PATTERN_BARS, st["kick"])
            pid_of[(t["key"], var)] = add_pattern(
                "notes", f"{t['name']} {var}", lane, gen, fam, var
            )

    song["tracks"], song["patterns"] = tracks, patterns
    sections, clips = [], {}
    fx_tid = None
    for i, x in enumerate(rows):
        sec = {
            "id": ids(),
            "name": x["name"],
            "bars": x["bars"],
            "repeats": x["repeats"],
            "color": sm.PALETTE[i % len(sm.PALETTE)],
            "energy": x["energy"],
            "cells": {},
            "audio": [],
        }
        if x["drums"]:
            sec["cells"][drums_tid] = drum_pid[x["drums"]]
        for key, var in x["play"].items():
            sec["cells"][tid_of[key]] = pid_of[(key, var)]
        sections.append(sec)
        keys = {"drums": drums_tid, **tid_of}
        for move in x["moves"]:
            if move[0] == "take" and fx_tid is None:
                fx_tid = ids()
                tracks.append(
                    {
                        "id": fx_tid,
                        "kind": "audio",
                        "name": "FX",
                        "role": "fx",
                        "gain": 0.8,
                    }
                )
            _move(song, sec, move, keys, fx_tid, ids, clips)
    song["sections"] = sections
    return Built(schema.conform(song, schema.nodes().SONG), clips, notes)


def _unit(track: dict, kind: str, ids, add: dict | None = None) -> dict | None:
    """The track's unit of `kind` a lane rides (the last one), with an id;
    `add`: appended when the chain has none and has room."""
    chain = track.setdefault("fx", [])
    unit = next((u for u in reversed(chain) if u["kind"] == kind), None)
    if unit is None:
        if add is None or len(chain) >= MAX_UNITS:
            return None
        unit = copy.deepcopy(add)
        chain.append(unit)
    if "id" not in unit:
        unit["id"] = ids()
    return unit


def _lane(sec: dict, tid: str, target: str, points) -> None:
    lanes = sec.setdefault("auto", {}).setdefault(tid, [])
    if len(lanes) < limits.MAX_CELL_LANES and all(
        ln["target"] != target for ln in lanes
    ):
        lanes.append({"target": target, "points": [list(p) for p in points]})


def _move(
    song: dict, sec: dict, move: tuple, keys: dict, fx_tid, ids, clips: dict
) -> None:
    """One of a shape's moves, written onto a section."""
    tracks = {t["id"]: t for t in song["tracks"]}
    sounding = [tid for tid, pid in sec["cells"].items() if pid]
    what = move[0]
    if what == "sweep":
        _a, lo, hi = move
        for tid in sounding:
            t = tracks[tid]
            if t["kind"] != "synth":
                continue  # everything but the drums: the kick stays whole
            unit = _unit(t, "lowpass", ids, styles._OPEN)
            if unit is not None:
                _lane(
                    sec,
                    tid,
                    fx_target(unit["id"], "lowpass", "cutoff"),
                    ((0.0, lo), (1.0, hi)),
                )
    elif what == "lane":
        _a, key, target, points = move
        tid = keys.get(key)
        if tid is None or tid not in sounding:
            return
        if isinstance(target, tuple):
            unit = _unit(tracks[tid], target[0], ids)
            if unit is None:
                return
            target = fx_target(unit["id"], target[0], target[1])
        _lane(sec, tid, target, points)
    elif what == "cut":
        beat = 1.0 / (sec["bars"] * 4)
        pts = (
            (0.0, GAIN_UNITY),
            (1.0 - beat - 0.004, GAIN_UNITY),
            (1.0 - beat, 0.0),
            (1.0, 0.0),
        )
        for tid in sounding:
            _lane(sec, tid, GAIN, pts)
    elif what == "take":
        _a, kind, direction, bars, where = move
        spec = _take_spec(kind, direction, min(bars, sec["bars"]), song["bpm"])
        name = clip_base(spec)
        clips[name] = spec
        at = (
            sec["bars"] - spec["bars"]
            if where == "end"
            else 0
            if where == "start"
            else int(where)
        )
        sec["audio"].append(
            {
                "track_id": fx_tid,
                "clip": name,
                "at_bar": max(0, min(at, sec["bars"] - 1)),
                "gain": TAKE_GAIN.get(kind, 0.7),
            }
        )
    elif what in ("enter", "span"):
        tid = keys.get(move[1])
        if tid is None or tid not in sounding:
            return
        at = move[2] - 1 if what == "enter" else move[2]
        length = sec["bars"] - at if what == "enter" else move[3]
        if 0 <= at < sec["bars"] and (at, length) != (0, sec["bars"]):
            sec.setdefault("spans", {})[tid] = [at, min(length, sec["bars"] - at)]


# --- one part again -------------------------------------------------------------


def _family(song: dict, track_id) -> list[dict]:
    """The generated patterns a track plays, and the rest of their
    families in the pool."""
    mine = {s["cells"].get(track_id) for s in song["sections"]} - {None}
    fams = {song["patterns"][p].get("fam") for p in mine if p in song["patterns"]} - {
        None
    }
    return [
        p
        for pid, p in song["patterns"].items()
        if p.get("gen") and (pid in mine or p.get("fam") in fams)
    ]


def _rewrite(song: dict, p: dict, seed: int) -> bool:
    """A generated pattern written again from its `gen`, with `seed`."""
    gen = p["gen"]
    params = gen.get("params") or {}
    if p["kind"] == "notes":
        if gen["kind"] not in nt.GENERATORS:
            return False
        gen["seed"] = seed
        p.update(note_lane(song, gen, p["bars"], _kick_of(song)))
        return True
    style = styles.STYLES.get(params.get("style"))
    if gen["kind"] == "drums" and style and params.get("var") in style["drums"]:
        gen["seed"] = seed
        p.update(
            drum_lane(style["drums"][params["var"]], seed, DRUM_KITS[style["kit"]])
        )
        return True
    if gen["kind"] == "beat" and params.get("style") in beatgen.STYLES:
        gen["seed"] = seed
        m = sm.pattern_meter(p)  # its own bar, rolled again in it
        p.update(dr.beat(str(params["style"]), seed, p["bars"], p["steps"], meter=m))
        return True
    src = song["patterns"].get(params.get("src"))
    if gen["kind"] == "loopfill" and src:
        gen["seed"] = seed
        p.update(_tilefill(src, seed))
        return True
    return False


def reroll(song: dict, track_id, seed: int) -> list[str]:
    """Roll one part again: every generated pattern the track plays is
    rewritten from its `gen` with a new seed — and so is the other half of
    a pair (the rolling bass's sub and mid share one stream). Nothing else
    in the song moves. The names of the tracks rewritten; SongError when
    the track holds nothing a generator wrote."""
    track = sm._track(song, track_id)
    pats = _family(song, track_id)
    parts = {(p["gen"].get("params") or {}).get("part") for p in pats} - {None}
    if parts:  # the pair's other half: the same part, another track
        pats += [
            p
            for p in song["patterns"].values()
            if p.get("gen")
            and p not in pats
            and (p["gen"].get("params") or {}).get("part") in parts
        ]
    done = set()
    for p in pats:
        part = (p["gen"].get("params") or {}).get("part") or p["gen"]["kind"]
        var = (p["gen"].get("params") or {}).get("var", "")
        salt = part if p["kind"] == "notes" else f"drums:{var}"
        if _rewrite(song, p, part_seed(seed, salt)):
            done.add(p["id"])
    if not done:
        raise sm.SongError(
            f"Nothing on {track['name']} was generated: there is nothing to roll."
        )
    names = [
        t["name"]
        for t in song["tracks"]
        if any(s["cells"].get(t["id"]) in done for s in song["sections"])
    ]
    return names or [track["name"]]


def vary(song: dict, track_id, op: str, amount: float = 0.25, seed: int = 0) -> int:
    """`busier` or `sparser` on every pattern a track plays (a shared
    pattern once): how many changed. The patterns keep their `gen`, so
    rolling again starts from the generator's own density."""
    track = sm._track(song, track_id)
    pids = dict.fromkeys(s["cells"].get(track_id) for s in song["sections"])
    changed = 0
    kick = _kick_of(song)
    for pid in pids:
        p = song["patterns"].get(pid)
        if p is None:
            continue
        before = copy.deepcopy(p)
        if p["kind"] == "drums":
            fn = dr.busier if op == "busier" else dr.sparser
            p.update(
                fn(
                    dr.lane_of(p),
                    amount,
                    seed,
                )
            )
        else:
            ctx = nt.context(
                song,
                None,
                bars=p["bars"],
                kick=kick,
                seed=seed,
                register=nt.KIND_REGISTER.get((p.get("gen") or {}).get("kind"), "bass"),
                role=track.get("role") or "part",
            )
            if p["steps_per_bar"] != nt.SPB:
                continue  # the generators write sixteenths
            lane = {
                k: p.get(k) or ([] if k != "velocities" else {})
                for k in (
                    "bars",
                    "steps_per_bar",
                    "cells",
                    "cuts",
                    "velocities",
                    "slides",
                )
            }
            p.update((nt.busier if op == "busier" else nt.sparser)(ctx, lane, amount))
        changed += p != before
    if not changed:
        raise sm.SongError(f"{track['name']} has nothing to make {op}.")
    return changed


def _style_of(song: dict, p: dict) -> str | None:
    """The beat style a pattern was generated in, if any (a fill's table
    follows it: `drums.STYLE_FILL`)."""
    params = (p.get("gen") or {}).get("params") or {}
    style = params.get("style")
    if style in styles.STYLES:  # a starter's drums name the song style
        return DRUM_KITS[styles.STYLES[style]["kit"]]["style"]
    return style if style in beatgen.STYLES else None


def cell_variant(
    song: dict,
    section_id,
    track_id,
    var: str,
    seed: int,
    *,
    exact: bool = False,
    amount: float = 0.25,
) -> str:
    """This cell's beat as variant `var` of its family (A-D, or "fill"),
    made when the family has none, and the cell pointed at it; the id.

    A letter is a copy of the cell's beat with a few light hits traded
    (`drums.variation`; `exact`: an untouched copy — "copy A to B"). The
    fill is the beat with a seeded fill in its last bar, in its style's
    own table; asked for again it is ROLLED again in place (every section
    playing it follows). The cell's own beat joins the family as the first
    free letter if it was in none. Other sections keep what they play."""
    if var not in sm.PATTERN_VARIANTS:
        raise sm.SongError("A variation is A, B, C, D or the fill.")
    sec = sm._section(song, section_id)
    sm._track(song, track_id)
    p = song["patterns"].get(sec["cells"].get(track_id))
    if p is None or p["kind"] != "drums":
        raise sm.SongError("Variations are a beat's: put a beat in this cell first.")
    fam = p.get("fam") or sm._fresh_id(song)
    p["fam"] = fam
    have = {
        q["var"]: q
        for q in song["patterns"].values()
        if q.get("fam") == fam and q.get("var") and q["kind"] == "drums"
    }
    if not p.get("var"):
        p["var"] = next((v for v in dr.VARIANTS if v not in have), "A")
        have[p["var"]] = p
    src = have.get("A", p) if var == "fill" and p.get("var") == "fill" else p
    lane = dr.lane_of(src)
    if var == "fill":
        made = dr.fill(lane, seed, 1.0, None, style=_style_of(song, src))
        gen = {"kind": "fill", "seed": seed, "params": {"src": src["id"]}}
        name = f"{src['name']} · fill"
    else:
        made = lane if exact else dr.variation(lane, seed, amount)
        gen = {"kind": "variation", "seed": seed, "params": {"src": src["id"]}}
        name = f"{src['name'].removesuffix(' ' + str(src.get('var')))} {var}"
    old = have.get(var)
    if old is not None and old is not p and var != "fill":
        sec["cells"][track_id] = old["id"]  # it exists: the cell picks it
        return old["id"]
    if old is not None and var == "fill":
        for k in ("hits", "swing"):
            old.pop(k, None)
        old.update(made, gen=gen)
        sec["cells"][track_id] = old["id"]
        return old["id"]
    if old is p:
        return p["id"]
    new = sm.add_pattern(
        song, "drums", name[:40], {**made, "gen": gen, "fam": fam, "var": var}
    )
    song["patterns"][new]["color"] = p["color"]
    sec["cells"][track_id] = new
    return new


def write(
    song: dict,
    track_id,
    kind: str,
    seed: int,
    params: dict | None = None,
    section_id=None,
) -> list[str]:
    """One generator onto one track: every pattern the track plays is
    rewritten by `kind` (a notes generator, or "fill" / "build" on the
    drums); a track that plays nothing gets one 8-bar pattern in every
    section (or in `section_id` alone). The ids of the patterns written."""
    track = sm._track(song, track_id)
    params = dict(params or {})
    if track["kind"] == "drums":
        return _write_drums(song, track, kind, seed, section_id)
    if track["kind"] != "synth" or kind not in nt.GENERATORS:
        raise sm.SongError("That generator does not fit this track.")
    secs = [s for s in song["sections"] if section_id in (None, s["id"])]
    if not secs:
        raise sm.SongError("No such section.")
    register = params.pop("reg", None) or (
        "sub" if kind == "bass_2step" else nt.KIND_REGISTER[kind]
    )
    gen = {
        "kind": kind,
        "seed": seed,
        "params": {**params, "part": f"t:{track_id}", "reg": register},
    }
    try:
        lane = note_lane(song, gen, PATTERN_BARS, _kick_of(song))
    except TypeError:
        raise sm.SongError("That generator does not take those settings.") from None
    # written in 4/4; in the bar its sections play (a section's own meter
    # when it is one section, else the song's) its notes fall on that
    # meter's beats (`meterops.beat_fit`) and the pattern says so
    m = sm.section_meter(song, secs[0] if section_id is not None else None)
    if m != "4/4":
        lane = {**meterops.beat_fit(lane, m), "meter": m}
    used = [p for p in dict.fromkeys(s["cells"].get(track_id) for s in secs) if p]
    shared = {
        pid
        for pid in used
        if len(sm.pattern_usage(song, pid))
        > len([s for s in secs if s["cells"].get(track_id) == pid])
    }
    out = []
    if used and not shared:
        for pid in used:
            p = song["patterns"][pid]
            keep = {k: p[k] for k in ("id", "kind", "name", "color")}
            keep |= {k: p[k] for k in ("fam", "var") if k in p}
            song["patterns"][pid] = {**keep, **lane, "gen": copy.deepcopy(gen)}
            out.append(pid)
    else:
        label = sm.ROLE_LABELS.get(track.get("role"), track["name"])
        pid = sm.add_pattern(
            song,
            "notes",
            f"{label} · {kind.replace('_', ' ')}"[:40],
            {**lane, "gen": gen},
        )
        for s in secs:
            if section_id is not None or s["cells"].get(track_id) or not used:
                sm.set_cell(song, s["id"], track_id, pid)
        out.append(pid)
    if not track.get("role"):
        track["role"] = _KIND_ROLE.get(kind, "lead")
    return out


_KIND_ROLE = {
    "bass_rolling": "bass",
    "bass_root": "bass",
    "bass_offbeat": "bass",
    "bass_walk": "bass",
    "bass_2step": "bass",
    "chords": "chords",
    "pad": "pad",
    "arp": "arp",
    "lead": "lead",
    "chops": "vocal",
    "hit": "fx",
}


def _tilefill(p: dict, seed: int, table: str = "roll") -> dict:
    """A beat tiled to eight bars (or as far as its grid goes) with a fill
    in its last bar: bars 1-7 are the beat as it was."""
    lane = dr.lane_of(p)
    bars = max(
        b for b in sm.bar_choices(p["steps"]) if b % p["bars"] == 0 or b == p["bars"]
    )
    return dr.fill(dr.tile(lane, bars), seed, 1.0, table)


def _write_drums(
    song: dict, track: dict, kind: str, seed: int, section_id
) -> list[str]:
    """ "fill": every beat the drums play becomes eight bars of itself with
    a fill in bar 8; "build": the section's beat becomes a roll."""
    if kind not in ("fill", "build"):
        raise sm.SongError("The drums take a fill or a build.")
    tid, out = track["id"], []
    made: dict[str, str] = {}
    for s in song["sections"]:
        if section_id not in (None, s["id"]):
            continue
        pid = s["cells"].get(tid)
        p = song["patterns"].get(pid)
        if kind == "build":
            if section_id is None:
                raise sm.SongError("Pick the section that builds.")
            bars = max(b for b in (2, 4, 8) if b <= max(2, s["bars"]))
            name = f"{s['name']} build"[:40]
            gen = {"kind": "build", "seed": seed, "params": {"bars": bars}}
            new = sm.add_pattern(
                song, "drums", name, {**dr.build(bars, seed), "gen": gen}
            )
            sm.set_cell(song, s["id"], tid, new)
            if s["bars"] > bars:
                sm.set_span(song, s["id"], tid, s["bars"] - bars, bars)
            out.append(new)
            continue
        if (
            p is None
            or (p.get("gen") or {}).get("kind") == "loopfill"
            or p.get("var") == "fill"
            # a beat thinned to its hats has no kick to come back in on
            or not any(v == "kick" for v, _s in p["drums"])
        ):
            continue
        if pid not in made:
            gen = {"kind": "loopfill", "seed": seed, "params": {"src": pid}}
            data = {**_tilefill(p, seed), "gen": gen, "var": "fill"}
            if p.get("fam"):
                data["fam"] = p["fam"]
            made[pid] = sm.add_pattern(song, "drums", f"{p['name']} · fill"[:40], data)
        if s["bars"] % song["patterns"][made[pid]]["bars"] == 0:
            s["cells"][tid] = made[pid]
            out.append(made[pid])
    if not out:
        raise sm.SongError("Every beat already ends in a fill.")
    return out


# --- a loop -> a song -----------------------------------------------------------


def guess_roles(song: dict, section: dict) -> dict[str, str]:
    """A role for each synth track of a section that has none: the lowest
    lane is the bass, a lane of chords (three notes or more at once) the
    chords, and the rest by height — what a hand-built loop never says."""
    heights = []
    for t in song["tracks"]:
        p = song["patterns"].get(section["cells"].get(t["id"]))
        if t["kind"] != "synth" or p is None or t.get("role"):
            continue
        pitches = [sm.midi(c[0]) for c in p["cells"]] or [60]
        by_step: dict[int, int] = {}
        for _pitch, step in p["cells"]:
            by_step[step] = by_step.get(step, 0) + 1
        chordal = bool(by_step) and max(by_step.values()) >= 3
        heights.append((sum(pitches) / len(pitches), t["id"], chordal))
    heights.sort()
    out, free = {}, list(_BY_HEIGHT)
    taken = {t.get("role") for t in song["tracks"]}
    for i, (_h, tid, chordal) in enumerate(heights):
        if i == 0 and "bass" not in taken:
            role = "bass"
        elif chordal:
            role = "chords"
        else:
            role = next(
                (x for x in free if x not in taken and x not in ("bass", "chords")),
                "lead",
            )
        taken.add(role)
        out[tid] = role
    return out


def name_tracks(song: dict, roles: dict[str, str]) -> None:
    """Tracks named for what they are for: "Bass · Sub Bomb" instead of
    "Synth 2" (a name someone gave a track is kept)."""
    for t in song["tracks"]:
        role = roles.get(t["id"])
        if not role:
            continue
        t["role"] = role
        sound = str((t.get("patch") or {}).get("name") or "").strip()
        if t["name"].startswith("Synth ") or t["name"].endswith(sm.PRIME):
            label = sm.ROLE_LABELS[role]
            t["name"] = (f"{label} · {sound}" if sound and sound != "init" else label)[
                :40
            ]


def arrange(song: dict, section_id, seed: int, shape=None) -> Built:
    """ "Make this loop a song": one section (the loop, as "Add loop to
    song" lays it down) becomes intro / verse / build / drop / break /
    outro **by muting, thinning and filtering — never by writing new
    music**. Each new section plays the loop's own patterns, by reference,
    on the tracks its row names; the beat's variants are derived patterns
    ("no kick", "hats only": only ever fewer hits); the full beat is tiled
    to eight bars with a fill in bar 8 — the one thing generated. The new
    sections take the source section's place."""
    src = sm._section(song, section_id)
    at = sm.section_index(song, section_id)
    shape = shape or styles.GENERIC_SHAPE
    seed = int(seed) % limits.SEED_MODULO
    ids = _Ids(
        f"{seed}:arrange",
        {t["id"] for t in song["tracks"]}
        | set(song["patterns"])
        | {s["id"] for s in song["sections"]},
    )
    roles = guess_roles(song, src)
    name_tracks(song, roles)
    tracks = {t["id"]: t for t in song["tracks"]}
    synths = [
        tid
        for tid, pid in src["cells"].items()
        if pid and tracks[tid]["kind"] == "synth"
    ]
    drums_tid = next(
        (
            tid
            for tid, pid in src["cells"].items()
            if pid and tracks[tid]["kind"] == "drums"
        ),
        None,
    )
    beats: dict[str, str] = {}
    if drums_tid:
        tracks[drums_tid].setdefault("role", "drums")
        beat = song["patterns"][src["cells"][drums_tid]]
        fam = beat.get("fam") or ids()
        beat["fam"] = fam
        lane = dr.lane_of(beat)
        gen = {"kind": "loopfill", "seed": seed, "params": {"src": beat["id"]}}
        beats["all"] = sm.add_pattern(
            song,
            "drums",
            f"{beat['name']} · fill"[:40],
            {**_tilefill(beat, seed), "gen": gen, "fam": fam, "var": "fill"},
        )
        for var in ("hats", "nokick", "kick"):
            thin = dr.thin(lane, keep=var)
            if thin["drums"]:
                beats[var] = sm.add_pattern(
                    song, "drums", f"{beat['name']} · {var}"[:40], {**thin, "fam": fam}
                )
    made, clips, fx_tid = [], {}, None
    for i, x in enumerate(shape):
        sec = {
            "id": ids(),
            "name": x["name"],
            "bars": x["bars"],
            "repeats": x["repeats"],
            "color": sm.PALETTE[i % len(sm.PALETTE)],
            "energy": x["energy"],
            "cells": {},
            "audio": [],
        }
        if drums_tid and x["drums"] and beats.get(x["drums"]):
            sec["cells"][drums_tid] = beats[x["drums"]]
        play = [tid for tid in synths if tracks[tid].get("role") in x["roles"]]
        if not play and synths:  # a loop of one lane still has an intro
            play = synths[:1] if x["energy"] < 0.9 else list(synths)
        if x["energy"] >= 0.9:
            play = list(synths)  # the drop is the whole loop
        for tid in play:
            sec["cells"][tid] = src["cells"][tid]
        longest = max(
            (song["patterns"][p]["bars"] for p in sec["cells"].values()), default=1
        )
        sec["bars"] = max(sec["bars"], longest)
        made.append(sec)
    song["sections"][at : at + 1] = made
    for sec, x in zip(made, shape, strict=True):
        for move in x["moves"]:
            if move[0] == "take":
                if fx_tid is None:
                    fx_tid = next(
                        (t["id"] for t in song["tracks"] if t["kind"] == "audio"), None
                    )
                if fx_tid is None:
                    if len(song["tracks"]) >= limits.MAX_TRACKS:
                        continue
                    fx_tid = sm.add_track(song, "audio", "FX", role="fx", gain=0.8)
            _move(song, sec, move, {}, fx_tid, ids, clips)
    song.setdefault("gen", {"kind": "arrange", "seed": seed, "params": {}})
    return Built(song, clips, [])


# --- the checklist's fixes ------------------------------------------------------


def add_edge(song: dict, which: str, seed: int) -> str:
    """An intro (or an outro) for a song that starts (ends) at full tilt:
    eight bars of its first (last) section thinned — the hats alone of its
    beat, the chords and pads without the bass and the lead — under a
    low-pass that opens (closes). No new music. The new section's name."""
    if not song["sections"]:
        raise sm.SongError("The song has no sections yet.")
    intro = which != "outro"
    src = song["sections"][0 if intro else -1]
    ids = _Ids(
        f"{seed}:edge:{which}",
        {t["id"] for t in song["tracks"]}
        | set(song["patterns"])
        | {s["id"] for s in song["sections"]},
    )
    tracks = {t["id"]: t for t in song["tracks"]}
    name = "Intro" if intro else "Outro"
    sec = {
        "id": ids(),
        "name": name,
        "bars": 8,
        "repeats": 1,
        "color": sm.PALETTE[len(song["sections"]) % len(sm.PALETTE)],
        "energy": 0.2,
        "cells": {},
        "audio": [],
    }
    roles = guess_roles(song, src)
    quiet = [
        tid
        for tid, pid in src["cells"].items()
        if pid
        and tracks[tid]["kind"] == "synth"
        and (tracks[tid].get("role") or roles.get(tid)) in ("chords", "pad", "arp")
    ]
    synths = [t for t, p in src["cells"].items() if p and tracks[t]["kind"] == "synth"]
    for tid in quiet or synths[:1]:
        sec["cells"][tid] = src["cells"][tid]
    for tid, pid in src["cells"].items():
        if pid and tracks[tid]["kind"] == "drums":
            beat = song["patterns"][pid]
            lane = dr.lane_of(beat)
            thin = dr.thin(lane, keep="hats")
            if thin["drums"]:
                data = {**thin, **({"fam": beat["fam"]} if beat.get("fam") else {})}
                sec["cells"][tid] = sm.add_pattern(
                    song, "drums", f"{beat['name']} · hats"[:40], data
                )
    sec["bars"] = max(
        [8, *(song["patterns"][p]["bars"] for p in sec["cells"].values())]
    )
    song["sections"].insert(0 if intro else len(song["sections"]), sec)
    _move(
        song,
        sec,
        ("sweep", 0.12, 0.6) if intro else ("sweep", 0.6, 0.1),
        {},
        None,
        ids,
        {},
    )
    return name


def add_bass(song: dict, seed: int) -> str:
    """A bass that follows the chord track, on a track of its own: the
    style's bass sound and figure when the song has a style, else a sine
    sub on long roots — in every section that has a kick (all of them when
    none has). The new track's id."""
    style = styles.STYLES.get(
        ((song.get("gen") or {}).get("params") or {}).get("style")
    )
    spec = next(
        (t for t in (style or {}).get("tracks", ()) if t["role"] == "bass"), None
    )
    entry = recipes.get_recipe(spec["sound"] if spec else "Sine Sub")
    kind, params = next(iter(spec["parts"].values())) if spec else ("bass_root", {})
    fx = clean_chain(copy.deepcopy(entry["main_fx"]))
    if not any(u["kind"] in ("pump", "sidechain") for u in fx) and len(fx) < MAX_UNITS:
        fx.append(
            {"kind": "pump", "params": {"depth": 0.8, "release": 0.25, "beats": 1.0}}
        )
    tid = sm.add_track(
        song,
        "synth",
        "Bass",
        role="bass",
        patch=copy.deepcopy(entry["patch"]),
        fx=clean_chain(fx),
    )
    params = {k: v for k, v in params.items() if k != "pair"}
    params["reg"] = spec["register"] if spec else "sub"
    kicked = [
        s["id"]
        for s in song["sections"]
        if any(
            (p := song["patterns"].get(pid)) is not None
            and p["kind"] == "drums"
            and any(v == "kick" for v, _s in p["drums"])
            for pid in s["cells"].values()
        )
    ]
    pid = write(song, tid, kind, seed, params)[0]
    if kicked:
        for s in song["sections"]:
            if s["id"] not in kicked:
                sm.set_cell(song, s["id"], tid, None)
            else:
                sm.set_cell(song, s["id"], tid, pid)
    return tid


def duck(song: dict, track_id) -> str:
    """A pump on a track's chain — the duck under every beat that needs no
    audible kick — unless it has one or its chain is full. The track's
    name."""
    track = sm._track(song, track_id)
    if track["kind"] != "synth":
        raise sm.SongError("Only a synth track is ducked.")
    chain = track.setdefault("fx", [])
    if any(u["kind"] in ("pump", "sidechain") for u in chain):
        raise sm.SongError(f"{track['name']} is ducked already.")
    if len(chain) >= MAX_UNITS:
        raise sm.SongError(f"{track['name']}'s effects are full: take one out first.")
    chain.append(
        {"kind": "pump", "params": {"depth": 0.8, "release": 0.25, "beats": 1.0}}
    )
    track["fx"] = clean_chain(chain)
    return track["name"]


def progressions(song: dict) -> list[dict]:
    """The chord strip's menu: the song's style's own progressions, then
    the eight generic ones, each as {label, bars, track} in this key."""
    style = ((song.get("gen") or {}).get("params") or {}).get("style")
    pool = list(styles.STYLES[style]["progressions"]) if style in styles.STYLES else []
    pool += [(p, 4) for p in chordtrack.PRESETS]
    out, seen = [], set()
    for degrees, bars in pool:
        track = chordtrack.from_degrees(degrees, bars)
        text = chordtrack.label(song, track)
        if (text, bars) not in seen:
            seen.add((text, bars))
            out.append({"label": text, "bars": bars, "track": track})
    return out


def add_part(song: dict, what: str, seed: int) -> str:
    """A first pattern, written and PLACED so that it sounds (an empty
    pool's next step): `beat` — a beat in the song's style's kit on the
    drums track (added when there is none) — or `riff` — a line on the
    chord track, on a synth track that plays nothing yet (or a new one). It
    goes into the first section with room for it, and into a new section
    when the song has none. The section's id (the page loops it)."""
    style = styles.STYLES.get(
        ((song.get("gen") or {}).get("params") or {}).get("style")
    )
    style = style or styles.STYLES[styles.DEFAULT]
    if not song["sections"]:
        sm.add_section(song, "Section 1", 4 if what == "beat" else PATTERN_BARS)
    if what == "beat":
        kit = DRUM_KITS[style["kit"]]
        drums = next((t for t in song["tracks"] if t["kind"] == "drums"), None)
        tid = (
            drums["id"]
            if drums
            else sm.add_track(
                song,
                "drums",
                "Drums",
                role="drums",
                drum_params=copy.deepcopy(kit["params"]),
            )
        )
        gen = {"kind": "beat", "seed": seed, "params": {"style": kit["style"]}}
        lane = dr.beat(kit["style"], seed, 2, vel=kit.get("vel"))
        pid = sm.new_pattern(song, "drums")
        song["patterns"][pid].update(lane, gen=gen)
        sec = next((x for x in song["sections"] if not x["cells"].get(tid)), None)
        if sec is None:
            raise sm.SongError("Every section has a beat: it is in the pool to place.")
        sm.set_cell(song, sec["id"], tid, pid)
        return sec["id"]
    if what != "riff":
        raise sm.SongError("No such part to add.")
    idle = next(
        (
            t
            for t in song["tracks"]
            if t["kind"] == "synth"
            and not any(x["cells"].get(t["id"]) for x in song["sections"])
        ),
        None,
    )
    if idle is None:
        spec = next((t for t in style["tracks"] if t["role"] in ("lead", "arp")), None)
        entry = recipes.get_recipe(spec["sound"]) if spec else None
        init = (
            {
                "patch": copy.deepcopy(entry["patch"]),
                "fx": clean_chain(copy.deepcopy(entry["main_fx"])),
            }
            if entry
            else {}
        )
        tid = sm.add_track(song, "synth", "Riff", role="lead", **init)
    else:
        tid = idle["id"]
    write(song, tid, "lead", seed, {"density": 0.6})
    return next(x["id"] for x in song["sections"] if x["cells"].get(tid))
