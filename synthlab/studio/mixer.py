"""The mixer's model: a track's pan and sends, the
return buses, the strip's duck and EQ, Balance, and what the Mix view
draws. Pure: every function takes the song (a plain dict) and returns or
edits plain data; nothing here renders, and nothing reads the session.

What a strip's controls ARE:

- **level / mute / solo**: the track's own `gain`, `mute`, `solo`;
- **pan** (`track["pan"]`, -1..1) and **sends** (`track["sends"]` =
  {return id: 0..1}): rendered by `render._cycle_stems` on the finished
  stem; both absent when unset, so a song without them keys and renders
  as it always did;
- **duck**: the depth of the track's first `sidechain` unit (added on the
  first touch above 0, its release read off the tempo);
- **eq**: the three gains of the track's first `eq` unit (added likewise).

A return (`song["returns"]`, at most `limits.MAX_RETURNS`) is `{id, name,
fx, gain}`. Its chain runs fully wet (`wet_chain`): the track's own level
is the dry path.
"""

from __future__ import annotations

import math

from . import fxrack, limits
from . import song_model as sm
from .fxrack import FX_UNITS, MAX_UNITS, clean_chain

# the two returns a first send makes, in slot order (A, B)
DEFAULT_RETURNS: tuple[tuple[str, list[dict]], ...] = (
    (
        "A Reverb",
        [{"kind": "reverb", "params": {"decay": 2.4, "mix": 1.0, "size": 0.85}}],
    ),
    (
        "B Delay",
        [
            {
                "kind": "delay",
                "params": {
                    "time": 0.375,
                    "feedback": 0.4,
                    "mix": 1.0,
                    "ping_pong": 1.0,
                    "sync": 3.0,  # a dotted eighth, on the song's tempo
                },
            }
        ],
    ),
)
SLOTS = "AB"

# Balance: a track's RMS under the drums', by role, in dB
ROLE_OFFSETS_DB: dict[str, float] = {
    "bass": -2.0,
    "lead": -3.0,
    "vocal": -3.0,
    "chords": -6.0,
    "arp": -7.0,
    "pad": -9.0,
    "fx": -10.0,
}
GAIN_RANGE = (0.05, 1.5)  # what Balance may set (a fader's travel is 0..1.5)
METER_FLOOR_DB = -48.0  # the bottom of a strip's meter

EQ_FIELDS = {"low": "low", "mid": "mid_gain", "high": "high"}
# which kinds of track have a chain the strip can reach into
CHAINED = ("synth", "drums")


def wet_chain(units) -> list[dict]:
    """A return's chain as it renders: every unit that has a `mix` at 1."""
    out = clean_chain(list(units or []))
    for u in out:
        if "mix" in u["params"]:
            u["params"]["mix"] = 1.0
    return out


def db(level: float) -> float:
    return 20.0 * math.log10(level) if level > 1e-9 else -math.inf


def meter_pct(level: float) -> float:
    """A linear level as a meter's lit length, 0..100 (`METER_FLOOR_DB`
    is empty, 0 dBFS full)."""
    d = db(level)
    if d <= METER_FLOOR_DB:
        return 0.0
    return round(min(1.0, 1.0 - d / METER_FLOOR_DB) * 100.0, 1)


# --- returns --------------------------------------------------------------------


def returns(song: dict) -> list[dict]:
    return song.get("returns") or []


def _fresh(song: dict) -> str:
    taken = {r["id"] for r in returns(song)} | {t["id"] for t in song["tracks"]}
    while (rid := sm.new_id()) in taken:  # pragma: no cover - 1 in 4e9
        pass
    return rid


def add_return(song: dict) -> str:
    """One more return, the next of `DEFAULT_RETURNS`; its id."""
    have = returns(song)
    if len(have) >= limits.MAX_RETURNS:
        raise sm.SongError(f"A song has at most {limits.MAX_RETURNS} returns.")
    kinds = {r["fx"][0]["kind"] for r in have if r.get("fx")}
    name, fx = next(
        (d for d in DEFAULT_RETURNS if d[1][0]["kind"] not in kinds),
        DEFAULT_RETURNS[len(have) % len(DEFAULT_RETURNS)],
    )
    rid = _fresh(song)
    song.setdefault("returns", []).append(
        {"id": rid, "name": name, "fx": clean_chain(fx), "gain": 1.0}
    )
    return rid


def remove_return(song: dict, rid) -> None:
    """The return and every send to it."""
    have = returns(song)
    if not any(r["id"] == rid for r in have):
        raise sm.SongError("No such return.")
    song["returns"] = [r for r in have if r["id"] != rid]
    for t in song["tracks"]:
        (t.get("sends") or {}).pop(rid, None)


def slot_return(song: dict, slot: int, make: bool) -> str | None:
    """The id of the return in `slot` (0 = A, 1 = B); with `make`, the
    default returns up to it are created first."""
    if not 0 <= slot < limits.MAX_RETURNS:
        raise sm.SongError("No such send.")
    while make and len(returns(song)) <= slot:
        add_return(song)
    have = returns(song)
    return have[slot]["id"] if slot < len(have) else None


def _return(song: dict, rid) -> dict:
    for r in returns(song):
        if r["id"] == rid:
            return r
    raise sm.SongError("No such return.")


def set_return(song: dict, rid, values: dict) -> str:
    """A return's `gain` (0..1.5) and its first unit's parameters
    (`p.<name>`, clamped to the unit's range by the schema); what moved,
    for the undo step's name."""
    r = _return(song, rid)
    said = "return"
    for key, raw in values.items():
        if key == "gain":
            r["gain"] = min(1.5, max(0.0, _num(raw, "Level")))
            said = "return level"
        elif key.startswith("p.") and r["fx"]:
            name = key[2:]
            unit = r["fx"][0]
            if name == "mix" or name not in FX_UNITS[unit["kind"]]["params"]:
                raise sm.SongError("No such return parameter.")
            unit["params"][name] = _num(raw, name)
            said = f"return {name}"
    return said


# --- a strip ----------------------------------------------------------------------


def _num(raw, what: str) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = math.nan
    if not math.isfinite(v):
        raise sm.SongError(f"{what.capitalize()} must be a number.")
    return v


def _flag(raw, what: str) -> bool:
    if raw in ("on", "off"):
        return raw == "on"
    raise sm.SongError(f"{what.capitalize()} is on or off.")


def _unit(track: dict, kind: str) -> dict | None:
    return next((u for u in track.get("fx") or [] if u["kind"] == kind), None)


def _own_unit(track: dict, kind: str, params: dict) -> dict:
    """The track's first `kind` unit, appended to its chain if it has
    none (refused when the chain is full)."""
    unit = _unit(track, kind)
    if unit is None:
        chain = track.setdefault("fx", [])
        if len(chain) >= MAX_UNITS:
            raise sm.SongError(
                f"{track['name']}'s chain is full ({MAX_UNITS} units): "
                f"remove one to add {FX_UNITS[kind]['label']}."
            )
        unit = clean_chain([{"kind": kind, "params": params}])[0]
        chain.append(unit)
    return unit


def duck_release(bpm: float) -> float:
    """A new duck's release, from the tempo: 0.28 s at 122 bpm, 0.18 s at
    138, a straight line between and beyond (held to the unit's range)."""
    lo, hi, _d = FX_UNITS["sidechain"]["params"]["release"]
    return min(hi, max(lo, round(0.28 - (bpm - 122.0) * 0.1 / 16.0, 3)))


def duck_of(track: dict) -> float:
    unit = _unit(track, "sidechain")
    return float(unit["params"]["depth"]) if unit else 0.0


def eq_of(track: dict) -> dict:
    unit = _unit(track, "eq")
    return {k: float(unit["params"][f]) if unit else 0.0 for k, f in EQ_FIELDS.items()}


def set_strip(song: dict, track_id, values: dict) -> tuple[str, str | None]:
    """Any of a strip's controls, only those posted: `gain`, `pan`,
    `mute`, `solo`, `send.<slot>` (0 = A, 1 = B; the first one turned up
    makes the default returns), `duck`, `eq.low` / `eq.mid` / `eq.high`.
    Returns (the undo step's name, the key one gesture's posts share — None
    for a button)."""
    i = sm.track_index(song, track_id)
    if i is None:
        raise sm.SongError("No such track.")
    t = song["tracks"][i]
    said, key = "mix", None
    for name, raw in values.items():
        if name in ("mute", "solo"):
            t[name] = _flag(raw, name)
            said = f"{name} track"
        elif name == "gain":
            t["gain"] = min(1.5, max(0.0, _num(raw, "Level")))
            said, key = "track level", f"gain:{track_id}"
        elif name == "pan":
            _needs_chain(t, "pan")
            t["pan"] = min(1.0, max(-1.0, _num(raw, "Pan")))
            said, key = "track pan", f"pan:{track_id}"
        elif name.startswith("send."):
            _needs_chain(t, "send")
            slot = name[5:]
            if slot not in ("0", "1"):
                raise sm.SongError("No such send.")
            v = min(1.0, max(0.0, _num(raw, "Send")))
            rid = slot_return(song, int(slot), make=v > 0)
            if rid is not None:
                t.setdefault("sends", {})[rid] = v
            said, key = f"send {SLOTS[int(slot)]}", f"send{slot}:{track_id}"
        elif name == "duck":
            if t["kind"] != "synth":
                raise sm.SongError("Only a synth track ducks under the kick.")
            v = min(1.0, max(0.0, _num(raw, "Duck")))
            if v > 0 or _unit(t, "sidechain"):
                unit = _own_unit(
                    t, "sidechain", {"depth": v, "release": duck_release(song["bpm"])}
                )
                unit["params"]["depth"] = v
            said, key = "track duck", f"duck:{track_id}"
        elif name.startswith("eq."):
            # every track has a chain: an audio track's runs over its takes
            field = EQ_FIELDS.get(name[3:])
            if field is None:
                raise sm.SongError("No such EQ band.")
            lo, hi, _d = FX_UNITS["eq"]["params"][field]
            v = min(hi, max(lo, _num(raw, "EQ")))
            if v != 0 or _unit(t, "eq"):
                _own_unit(t, "eq", {})["params"][field] = v
            said, key = "track EQ", f"{name}:{track_id}"
    return said, key


def _needs_chain(track: dict, what: str) -> None:
    if track["kind"] not in CHAINED:
        raise sm.SongError(f"An audio track has no {what} yet.")


# --- an audio track's rack ----------------------------------------------------------
#
# A synth or a drums track's chain is its sound's: edited in the dock (Synths
# or the Drum Lab, mirrored into the track). An audio track has no sound to
# edit, so its rack is edited here, on the song itself (`POST
# /song/track/{id}/fx`, the Song page's drawer on the track's row): a unit
# added, removed, moved one place, or a parameter set — each one song edit.
# A move carries the unit as it is, id included, so a lane follows it.

RACK_OPS = fxrack.RACK_OPS


def edit_rack(song: dict, track_id, op: str, values: dict) -> tuple[str, str | None]:
    """An audio track's rack: `fxrack.edit_chain` (`op` add / remove / move
    / set) on the track's `fx`. Returns (the undo step's name, the key a
    slider's posts share — None for a button). Refused for a track that is
    not audio, and for whatever `edit_chain` refuses (an unknown kind, a
    full rack, a unit that is gone, a parameter it does not have)."""
    i = sm.track_index(song, track_id)
    if i is None:
        raise sm.SongError("No such track.")
    t = song["tracks"][i]
    if t["kind"] != "audio":
        raise sm.SongError(
            f"{t['name']}'s effects are its sound's: edit them in the dock."
        )
    try:
        t["fx"], said, at = fxrack.edit_chain(t.get("fx") or [], op, values)
    except fxrack.RackError as err:
        raise sm.SongError(str(err)) from err
    # a knob's posts join one step; a press (a preset, a step) is its own
    name = values.get("name", "")
    joins = at is not None and name not in fxrack.RACK_PRESSES
    return said, f"rack:{track_id}:{at}:{name}" if joins else None


# --- Balance ------------------------------------------------------------------------


def loudest(levels: dict) -> str | None:
    """The section whose stems carry the most energy: `levels` is
    {section id: {track id: (peak, rms)}}."""
    best, top = None, 0.0
    for sid, rows in levels.items():
        power = sum(rms * rms for _p, rms in rows.values())
        if power > top:
            best, top = sid, power
    return best


def balance(song: dict, levels: dict) -> list[tuple[str, float, float]]:
    """Set each track that has a role so its RMS in the loudest section
    sits at the role's offset under the drums (`ROLE_OFFSETS_DB`); with no
    drums sounding there, under the loudest track with a role. Edits
    `song`; returns [(track name, old gain, new gain)] for what moved.

    A track's gain sits before its chain, so a driven or compressed track
    lands near its target, not on it; pressing Balance again closes in."""
    sid = loudest(levels)
    if sid is None:
        return []
    rows = levels[sid]
    tracks = {t["id"]: t for t in song["tracks"]}
    drums = None
    for t in song["tracks"]:
        if t["kind"] == "drums" and rows.get(t["id"], (0.0, 0.0))[1] > 1e-6:
            drums = rows[t["id"]][1]
            break
    roled = {
        tid: rms
        for tid, (_p, rms) in rows.items()
        if tid in tracks
        and tracks[tid]["kind"] == "synth"
        and tracks[tid].get("role") in ROLE_OFFSETS_DB
        and rms > 1e-6
    }
    if not roled:
        return []
    ref = drums if drums is not None else max(roled.values())
    moved = []
    for tid, rms in roled.items():
        t = tracks[tid]
        target = ref * 10 ** (ROLE_OFFSETS_DB[t["role"]] / 20.0)
        new = round(min(GAIN_RANGE[1], max(GAIN_RANGE[0], t["gain"] * target / rms)), 3)
        if abs(new - t["gain"]) >= 0.005:
            moved.append((t["name"], t["gain"], new))
            t["gain"] = new
    return moved


# --- what the Mix view draws ----------------------------------------------------------


def _rack(track: dict) -> list[str]:
    return [FX_UNITS[u["kind"]]["label"] for u in clean_chain(track.get("fx"))]


def context(song: dict, levels: dict | None = None, focus: str | None = None) -> dict:
    """The Mix view: one strip a track, the returns, the section the
    meters read (`focus`, else the loudest known, else the first). `levels`
    is {section id: {track or return id: (peak, rms)}} at the song's gain —
    whatever is already rendered; a strip with none shows an unlit meter."""
    levels = levels or {}
    ids = [sec["id"] for sec in song["sections"]]
    at = focus if focus in ids else (loudest(levels) or (ids[0] if ids else None))
    rows = levels.get(at) or {}
    buses = returns(song)
    solo = any(t["solo"] for t in song["tracks"])
    strips = []
    for t in song["tracks"]:
        chained = t["kind"] in CHAINED
        peak, rms = rows.get(t["id"], (0.0, 0.0))
        sends = t.get("sends") or {}
        strips.append(
            {
                "id": t["id"],
                "name": t["name"],
                "kind": t["kind"],
                "role": t.get("role"),
                "gain": t["gain"],
                "mute": t["mute"],
                "solo": t["solo"],
                "heard": sm.audible(song, t),
                "chained": chained,
                "pan": float(t.get("pan") or 0.0),
                "sends": [
                    float(sends.get(buses[i]["id"], 0.0)) if i < len(buses) else 0.0
                    for i in range(limits.MAX_RETURNS)
                ],
                "ducks": t["kind"] == "synth",
                "duck": duck_of(t),
                "eq": eq_of(t),
                "rack": _rack(t),
                "level": meter_pct(rms),
                "peak": meter_pct(peak),
            }
        )
    outs = []
    for i, r in enumerate(buses):
        unit = r["fx"][0] if r.get("fx") else None
        peak, rms = rows.get(r["id"], (0.0, 0.0))
        params = []
        if unit:
            for name, (lo, hi, default) in FX_UNITS[unit["kind"]]["params"].items():
                if name != "mix":
                    value = unit["params"].get(name, default)
                    params.append((name, lo, hi, default, value))
        outs.append(
            {
                "id": r["id"],
                "slot": SLOTS[i],
                "name": r["name"],
                "gain": float(r.get("gain", 1.0)),
                "unit": FX_UNITS[unit["kind"]]["label"] if unit else "",
                "params": params[:4],
                "rack": [FX_UNITS[u["kind"]]["label"] for u in r.get("fx") or []],
                "feeds": sum(
                    1 for t in song["tracks"] if (t.get("sends") or {}).get(r["id"])
                ),
                "level": meter_pct(rms),
                "peak": meter_pct(peak),
            }
        )
    return {
        "strips": strips,
        "returns": outs,
        "slots": list(SLOTS[: limits.MAX_RETURNS]),
        "can_add_return": len(buses) < limits.MAX_RETURNS,
        "sections": [(sec["id"], sec["name"]) for sec in song["sections"]],
        "section": at,
        "soloed": solo,
        "metered": bool(rows),
        "empty": not song["tracks"] or not song["sections"],
    }
