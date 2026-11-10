"""What a song is missing, and what makes a style that style — both read
off the song MODEL alone: nothing here renders and nothing here listens.

`check(song)` is the Song page's checklist ("✓ 3"): each finding is a fact
with, where there is one, the button that fixes it — a route the page
already has, with its form values.

`signature(song, style)` is section 6 of the song-creation spec as machine
checks: the grammar a style's starter must show (the rolling bass never on
the kick's step, a gate on a pad with a lane on its depth, a breakdown of
sixteen bars without drums ...). They say the grammar is PRESENT, never
that it is music. Every check is a pass or a fail: the two that waited on
the per-hit drum table (a roll that reaches 32nds with the snare's tune
rising, a chance on single hits) read it now (`_roll_peaks`, `_chancy`).
"""

from __future__ import annotations

import math

from .. import limits
from .. import song_model as sm
from ..curves import GAIN, parse_fx
from . import drums as dr
from . import notes as nt
from . import styles

FULL = 0.85  # a section this full (its `energy`) is a drop
DUCKS = ("sidechain", "pump")
LEAD_INS = ("riser", "sweep", "swell")


# --- reading the model ----------------------------------------------------------


def _tracks(song: dict, role: str | None = None, kind: str | None = None) -> list[dict]:
    return [
        t
        for t in song["tracks"]
        if (role is None or t.get("role") == role)
        and (kind is None or t["kind"] == kind)
    ]


def _pattern(song: dict, sec: dict, tid) -> dict | None:
    return song["patterns"].get(sec["cells"].get(tid))


def _sounding(song: dict, sec: dict, t: dict) -> bool:
    p = _pattern(song, sec, t["id"])
    if p is None or t.get("mute"):
        return False
    return bool(p.get("drums") if p["kind"] == "drums" else p.get("cells"))


def _drums(song: dict, sec: dict) -> dict | None:
    t = next(iter(_tracks(song, kind="drums")), None)
    p = _pattern(song, sec, t["id"]) if t and not t.get("mute") else None
    return p if p and p.get("drums") else None


def _block(sec: dict, tid) -> tuple[int, int]:
    """(first bar, bars) a track's block plays in a pass of its section."""
    return sm.cell_span(sec, tid)


def kick_set(song: dict, sec: dict, bars: int | None = None) -> set[int]:
    """The sixteenths of a pass of the section a kick lands on."""
    p = _drums(song, sec)
    if p is None:
        return set()
    t = _tracks(song, kind="drums")[0]
    at, n = _block(sec, t["id"])
    per, total = p["steps"], p["bars"] * p["steps"]
    out = set()
    for bar in range(at, at + n):
        for v, s in p["drums"]:
            if v == "kick" and s < total and (s // per) == (bar - at) % p["bars"]:
                out.add(bar * 16 + (s % per) * 16 // per)
    return out if bars is None else {s for s in out if s < bars * 16}


def note_ons(song: dict, sec: dict, t: dict) -> set[int]:
    """The sixteenths of a pass of the section a track's notes start on."""
    p = _pattern(song, sec, t["id"])
    if p is None or p["kind"] != "notes":
        return set()
    at, n = _block(sec, t["id"])
    per = p["steps_per_bar"]
    ons = {
        (s // per, (s % per) * 16 // per) for s in nt.note_ons(p) if s < p["bars"] * per
    }
    return {
        bar * 16 + step
        for bar in range(at, at + n)
        for pbar, step in ons
        if pbar == (bar - at) % p["bars"]
    }


def _unit(t: dict, *kinds: str) -> dict | None:
    return next((u for u in t.get("fx") or () if u["kind"] in kinds), None)


def _takes(sec: dict, *kinds: str) -> list[dict]:
    return [a for a in sec.get("audio") or () if a["clip"].split("-")[0] in kinds]


def _take_bars(take: dict) -> int:
    """A transition clip's bars, from its name ("riser-up-4bar-...")."""
    for bit in take["clip"].split("-"):
        if bit.endswith("bar") and bit[:-3].isdigit():
            return int(bit[:-3])
    return 1


def _lanes(sec: dict, tid) -> list[dict]:
    return (sec.get("auto") or {}).get(tid, [])


def _fx_lane(sec: dict, t: dict, kind: str, param: str) -> dict | None:
    for ln in _lanes(sec, t["id"]):
        fx = parse_fx(ln["target"])
        if fx and fx[1] == kind and fx[2] == param:
            return ln
    return None


def _rises(lane: dict | None) -> bool:
    return bool(lane) and lane["points"][-1][1] > lane["points"][0][1]


def is_drop(sec: dict) -> bool:
    return (sec.get("energy") or 0.0) >= FULL


def _named(song: dict, word: str) -> list[dict]:
    return [s for s in song["sections"] if s["name"].lower().startswith(word)]


def quiet_runs(song: dict, voice: str | None = None) -> list[tuple[int, int]]:
    """(index of the first section, bars) of every run of consecutive
    sections with no drums — or, `voice` "kick", with no kick."""
    runs, start, bars = [], None, 0
    for i, sec in enumerate(song["sections"]):
        p = _drums(song, sec)
        quiet = p is None or (voice and not any(v == voice for v, _s in p["drums"]))
        if quiet:
            start = i if start is None else start
            bars += sec["bars"] * sec["repeats"]
        elif start is not None:
            runs.append((start, bars))
            start, bars = None, 0
    if start is not None:
        runs.append((start, bars))
    return runs


def _changes_inside(song: dict, sec: dict) -> bool:
    """Whether something moves INSIDE a pass of the section: a lane, a
    trimmed block, a take, or a pattern that ends in a fill."""
    if sec.get("auto") or sec.get("spans") or sec.get("audio"):
        return True
    return any(
        (p := song["patterns"].get(pid)) is not None and p.get("var") == "fill"
        for pid in sec["cells"].values()
    )


def still_runs(song: dict) -> list[tuple[int, int]]:
    """(index, bars) of every stretch where nothing changes: consecutive
    sections playing the same patterns with no lane, span, take or fill."""
    runs, start, bars, last = [], None, 0, None
    for i, sec in enumerate(song["sections"]):
        cells = {t: p for t, p in sec["cells"].items() if p}
        if _changes_inside(song, sec):
            n = sec["bars"]  # each pass is itself a change
            cells = None
        else:
            n = sec["bars"] * sec["repeats"]
        if cells is not None and cells == last:
            bars += n
        else:
            if start is not None:
                runs.append((start, bars))
            start, bars = i, n
        last = cells
    if start is not None:
        runs.append((start, bars))
    return runs


def energy_steps(song: dict, start: int, bars: int, step: float = 0.15) -> bool:
    """Whether the energy lane moves inside the stretch of `bars` bars
    that starts at section `start`: two neighbouring sections there whose
    drawn energies differ by `step` or more (the playlist's energy lane;
    an undrawn section says nothing)."""
    left, last = bars, None
    for sec in song["sections"][start:]:
        if left <= 0:
            break
        e = sec.get("energy")
        if e is not None and last is not None and abs(e - last) >= step:
            return True
        last = e if e is not None else last
        left -= sec["bars"] * sec["repeats"]
    return False


# --- the checklist --------------------------------------------------------------


def _finding(key: str, text: str, fix: dict | None = None, info: bool = False) -> dict:
    return {"key": key, "text": text, "fix": fix, "info": info}


def _fix(label: str, post: str, **vals) -> dict:
    return {"label": label, "post": post, "vals": {k: str(v) for k, v in vals.items()}}


def check(song: dict, gain: dict | None = None) -> list[dict]:
    """What the song is missing: [{key, text, fix, info}], worst first.
    `fix` is {label, post, vals} — a route and its form — or None; `info`
    marks a fact that is not a fault (a short song is allowed to be short).
    `gain`, when the plan has been rendered: {"peak", "ref"} of its loudest
    pass, for the one finding that needs audio (peaks holding the level
    down). An empty song has one finding: start one."""
    secs = song["sections"]
    out: list[dict] = []
    if not secs:
        return [_finding("empty", "The song has no sections yet.")]
    drums = next(iter(_tracks(song, kind="drums")), None)

    def roles(sec: dict) -> set[str]:
        return {
            t["id"]
            for t in song["tracks"]
            if t["kind"] == "synth" and _sounding(song, sec, t)
        }

    def full(sec: dict) -> bool:
        return bool(kick_set(song, sec)) and len(roles(sec)) >= 4

    if len(secs) > 1 or full(secs[0]):
        if full(secs[0]):
            out.append(
                _finding(
                    "intro",
                    f"No intro: {secs[0]['name']} starts with everything on.",
                    _fix("Add an intro", "/song/arrange", add="intro"),
                )
            )
        if full(secs[-1]):
            out.append(
                _finding(
                    "ending",
                    f"No ending: {secs[-1]['name']} stops at full tilt.",
                    _fix("Add an outro", "/song/arrange", add="outro"),
                )
            )
    for i, bars in still_runs(song):
        if bars >= 32:
            fix = (
                _fix(
                    "Add a fill every 8 bars",
                    "/song/gen",
                    track_id=drums["id"],
                    kind="fill",
                )
                if drums and _drums(song, secs[i])
                else None
            )
            said = f"Nothing changes for {bars} bars from {secs[i]['name']}."
            if energy_steps(song, i, bars):
                # the energy lane was drawn to move here and the parts do
                # not follow it: one gesture makes them (the playlist tools)
                said = said[:-1] + ", though the energy lane does."
                fix = _fix("Arrange by energy", "/song/energy", op="arrange")
            out.append(_finding(f"still:{secs[i]['id']}", said, fix))
    loud = max(secs, key=lambda s: (s.get("energy") or 0.0, s["bars"] * s["repeats"]))
    basses = [t for t in _tracks(song, "bass") if _sounding(song, loud, t)]
    if not basses and any(_sounding(song, loud, t) for t in song["tracks"]):
        out.append(
            _finding(
                "bass",
                f"No bass in {loud['name']}, the fullest section.",
                _fix("Add a bass that follows the chords", "/song/gen", add="bass"),
            )
        )
    for t in basses:
        kicks = kick_set(song, loud)
        if kicks & note_ons(song, loud, t) and not _unit(t, *DUCKS):
            out.append(
                _finding(
                    f"duck:{t['id']}",
                    f"{t['name']} plays on the kick's steps with nothing ducking it.",
                    _fix(
                        "Duck the bass under the kick",
                        "/song/gen",
                        track_id=t["id"],
                        kind="duck",
                    ),
                )
            )
    at = secs.index(loud)
    if at > 0 and is_drop(loud):
        prev = secs[at - 1]
        lead = [
            a
            for a in _takes(prev, *LEAD_INS, "impact", "subdrop")
            if a["at_bar"] + _take_bars(a) >= prev["bars"]
        ]
        if not lead:
            out.append(
                _finding(
                    "transition",
                    f"Nothing leads into {loud['name']}.",
                    _fix(
                        "Add a riser",
                        f"/song/section/{prev['id']}/transition",
                        kind="riser",
                        direction="up",
                        bars=min(4, prev["bars"]),
                    ),
                )
            )
    for t in song["tracks"]:
        if t["kind"] == "audio":
            heard = any(
                a["track_id"] == t["id"] for s in secs for a in s["audio"]
            ) or bool(sm.long_items(song, t["id"]))
            if heard and not t["mute"]:
                continue
            if not heard:
                continue  # an empty audio track is a place to record, not a fault
        used = any(s["cells"].get(t["id"]) for s in secs)
        if t["mute"] and (used or t["kind"] == "audio"):
            out.append(
                _finding(
                    f"muted:{t['id']}",
                    f"{t['name']} is muted in every section.",
                    _fix("Unmute", f"/song/track/{t['id']}/mix", mute="off"),
                )
            )
        elif not used and t["kind"] != "audio":
            out.append(
                _finding(
                    f"unused:{t['id']}",
                    f"{t['name']} plays in no section.",
                    _fix("Remove the track", f"/song/track/{t['id']}/delete"),
                )
            )
    seconds = sm.song_seconds(song)
    bar = 240.0 / song["bpm"]
    for sec in secs:
        if sm.cycle_bars(song, sec) * bar > limits.MAX_SECTION_SECONDS:
            out.append(
                _finding(
                    f"cycle:{sec['id']}",
                    f"{sec['name']} is too long to render as one pass: split it.",
                )
            )
    if seconds > limits.MAX_SONG_SECONDS:
        out.append(
            _finding(
                "cap",
                f"The song is {seconds:.0f} s, over the cap: trim some repeats.",
            )
        )
    elif seconds < 120:
        out.append(
            _finding(
                "short",
                f"{int(seconds // 60)}:{int(seconds % 60):02d} long: under 2 minutes.",
                info=True,
            )
        )
    if gain and gain.get("peak", 0) > 0 and gain.get("ref", 0) > 0:
        # song_play.gain_of: the body to TARGET unless a peak holds it lower
        if 0.97 / gain["peak"] < 0.89 / gain["ref"] * 0.8:
            db = 20 * math.log10((0.89 / gain["ref"]) / (0.97 / gain["peak"]))
            out.append(
                _finding(
                    "peaks",
                    f"A few peaks hold the whole song {db:.0f} dB under its body.",
                    info=True,
                )
            )
    return out


def open_items(findings: list[dict]) -> list[dict]:
    """The findings that are faults (not `info`)."""
    return [f for f in findings if not f["info"]]


# --- signatures -----------------------------------------------------------------


def _c(text: str, ok: bool) -> dict:
    return {"text": text, "ok": bool(ok)}


def _tempo(song: dict, lo: float, hi: float) -> dict:
    return _c(f"tempo {lo:g}-{hi:g}", lo <= song["bpm"] <= hi)


def _ducked(t: dict, depth: float = 0.0) -> bool:
    u = _unit(t, *DUCKS)
    return u is not None and u["params"]["depth"] >= depth


def _synced_delay(t: dict, ping: bool = False) -> bool:
    return any(
        u["kind"] == "delay"
        and u["params"].get("sync", 0) >= 1
        and (not ping or u["params"].get("ping_pong", 0) >= 0.5)
        for u in t.get("fx") or ()
    )


def _breakdown(song: dict, bars: int, voice: str | None = None) -> bool:
    return any(n >= bars for _i, n in quiet_runs(song, voice))


def _lead_in(song: dict, drop: dict, kinds=LEAD_INS) -> bool:
    i = song["sections"].index(drop)
    if i == 0:
        return False
    prev = song["sections"][i - 1]
    return any(
        a["at_bar"] + _take_bars(a) >= prev["bars"] for a in _takes(prev, *kinds)
    )


def _build_rates(song: dict, sec: dict) -> tuple[bool, int]:
    """(a bar never has fewer snare hits than the one before, how many
    distinct rates) of a section's drum roll."""
    p = _drums(song, sec)
    if p is None:
        return False, 0
    per = dr.hits_per_bar(p, "snare")
    return all(b >= a for a, b in zip(per, per[1:], strict=False)), len(set(per) - {0})


def _roll_peaks(song: dict, sec: dict) -> bool:
    """A section's drum roll reaches 32nds — a snare step ratcheted to two
    hits or more on a sixteenth grid, or finer — while the snare's tune
    never falls from hit to hit and ends five semitones or more above
    where it began."""
    p = _drums(song, sec)
    if p is None:
        return False
    data = p.get("hits") or {}
    total = p["bars"] * p["steps"]
    roll = sorted(int(s) for v, s in p["drums"] if v == "snare" and int(s) < total)
    if len(roll) < 2:
        return False
    hit = [data.get(f"snare.{s}") or {} for s in roll]
    fast = any(int(h.get("r", 1)) * p["steps"] >= 32 for h in hit)
    tune = [float(h.get("t", 0.0)) for h in hit]
    rising = all(b >= a for a, b in zip(tune, tune[1:], strict=False))
    return fast and rising and tune[-1] - tune[0] >= 5.0


def _chancy(song: dict) -> int:
    """How many hits of the beats the song plays have a chance under 1."""
    seen, n = set(), 0
    for sec in song["sections"]:
        p = _drums(song, sec)
        if p is None or p["id"] in seen:
            continue
        seen.add(p["id"])
        on = {f"{v}.{int(s)}" for v, s in p["drums"]}
        n += sum(
            1
            for k, h in (p.get("hits") or {}).items()
            if k in on and float(h.get("p", 1.0)) < 1.0
        )
    return n


# --- the drop FX as sounds -------------------------------------------------------
# A drop's hit is a take (a transition clip) or a track of its own playing a
# drop-FX recipe: read here by the sound's grammar, never by its name.


def _hits_first_beat(song: dict, sec: dict, t: dict) -> bool:
    return _sounding(song, sec, t) and 0 in note_ons(song, sec, t)


def _impact(t: dict) -> bool:
    """An fx track whose sound falls onto its note fast and dies: a hit."""
    p = t.get("patch") or {}
    return (
        float(p.get("pitch_env", 0.0)) >= 12.0
        and float(p.get("pitch_env_decay", 1.0)) <= 0.3
        and float(p.get("sustain", 1.0)) == 0.0
    )


def _subdrop(t: dict) -> bool:
    """An fx track whose sound is a sine falling onto its note (and on, a
    bend curve going down), nothing over 500 Hz: a sub drop."""
    p = t.get("patch") or {}
    bend = (t.get("curves") or {}).get("pitch_bend") or []
    falls = float(p.get("pitch_env", 0.0)) > 0 or (
        len(bend) > 1 and bend[-1][1] < bend[0][1]
    )
    return p.get("osc1") == "sine" and float(p.get("cutoff", 2200.0)) <= 500 and falls


def _drop_hit(song: dict, drop: dict, take: str, sound) -> bool:
    """A `take` clip on the drop's first bar line, or a track of `sound`'s
    grammar hitting its first beat."""
    return any(a["at_bar"] == 0 for a in _takes(drop, take)) or any(
        sound(t) and _hits_first_beat(song, drop, t) for t in _tracks(song, "fx")
    )


def _punched(song: dict) -> bool:
    """A transient shaper lifting the fronts on the drum bus."""
    return any(
        (u := _unit(t, "transient")) and u["params"]["attack"] > 0
        for t in _tracks(song, kind="drums")
    )


def _first_sounds(song: dict, t: dict) -> int | None:
    return next(
        (i for i, s in enumerate(song["sections"]) if _sounding(song, s, t)), None
    )


def _trance(song: dict) -> list[dict]:
    drops = [s for s in song["sections"] if is_drop(s)]
    basses = _tracks(song, "bass")
    pads = _tracks(song, "pad")
    builds = _named(song, "build")
    on_kick, after, cut = False, True, True
    for sec in drops:
        kicks = kick_set(song, sec)
        for t in basses:
            if not _sounding(song, sec, t):
                continue
            ons = note_ons(song, sec, t)
            on_kick |= bool(ons & kicks)
            after &= all({k + 1, k + 2, k + 3} <= ons for k in kicks)
            p = _pattern(song, sec, t["id"])
            cut &= all(n == 1 for _p, _s, n in nt.runs(p))
    gated = [
        t
        for t in pads
        if (u := _unit(t, "gate"))
        and "x" in u.get("pattern", "")
        and "." in u.get("pattern", "")
    ]
    rates = [_build_rates(song, s) for s in builds]
    return [
        _tempo(song, 132, 140),
        _c(
            "in every climax bar no bass note starts on a kick's step",
            drops and basses and not on_kick,
        ),
        _c(
            "a bass note on each of the three sixteenths after every climax kick",
            drops and basses and after,
        ),
        _c(
            "a cut between the bass's notes (each is one step long)",
            drops and basses and cut,
        ),
        _c(
            "both bass tracks ducked",
            len(basses) >= 2 and all(_ducked(t) for t in basses),
        ),
        _c(
            "a gate with a pattern on a pad track, and a lane on its depth",
            any(
                any(_fx_lane(s, t, "gate", "depth") for s in song["sections"])
                for t in gated
            ),
        ),
        _c(
            "a build whose roll never thins and takes three rates or more",
            any(ok and n >= 3 for ok, n in rates),
        ),
        _c(
            "the roll reaches 32nds with the snare's tune rising",
            any(_roll_peaks(song, s) for s in builds),
        ),
        _c(
            "a riser or sweep ending on the climax's bar line",
            any(_lead_in(song, d) for d in drops),
        ),
        _c("a breakdown of 16 bars or more with no drums", _breakdown(song, 16)),
        _c(
            "a supersaw lead (unison of 5 or more) in the climax",
            any(
                t["patch"].get("unison", 1) >= 5
                and any(_sounding(song, d, t) for d in drops)
                for t in _tracks(song, "lead")
            ),
        ),
        _c(
            "an open hat on every offbeat of the climax",
            bool(drops)
            and all(
                (p := _drums(song, d)) is not None
                and all(
                    ["ohat", b * p["steps"] + k * p["steps"] // 16] in p["drums"]
                    for b in range(p["bars"])
                    for k in (2, 6, 10, 14)
                )
                for d in drops
            ),
        ),
        _c(
            "a synced ping-pong delay on the pluck",
            any(_synced_delay(t, ping=True) for t in _tracks(song, "arp")),
        ),
        _c(
            "a sub drop under the climax's first beat",
            any(_drop_hit(song, d, "subdrop", _subdrop) for d in drops),
        ),
        _c("the drums punched (a transient shaper on their bus)", _punched(song)),
    ]


def _techno(song: dict) -> list[dict]:
    secs = song["sections"]
    drops = [s for s in secs if is_drop(s)]
    basses = _tracks(song, "bass")
    on_kick = any(
        note_ons(song, s, t) & kick_set(song, s)
        for s in secs
        for t in basses
        if _sounding(song, s, t)
    )
    quiet = [i for i, n in quiet_runs(song, "kick") if n >= 16]
    late = bool(quiet) and any(
        (f := _first_sounds(song, t)) is not None and f >= quiet[0]
        for t in _tracks(song, kind="synth")
    )
    return [
        _tempo(song, 120, 126),
        _c("the bass never on a kick's step", bool(basses) and not on_kick),
        _c(
            "a synth track with a synced delay",
            any(_synced_delay(t) for t in _tracks(song, kind="synth")),
        ),
        _c(
            "a pad with a reverb of 3 s or more",
            any(
                (u := _unit(t, "reverb")) and u["params"]["decay"] >= 3.0
                for t in _tracks(song, "pad")
            ),
        ),
        _c("a breakdown of 16 bars or more without a kick", bool(quiet)),
        _c("a track that first sounds in or after the breakdown", late),
        _c(
            "a riser ending on a drop's first bar line",
            any(_lead_in(song, d, ("riser",)) for d in drops),
        ),
        _c(
            "an impact starting on a drop's first bar line",
            any(_drop_hit(song, d, "impact", _impact) for d in drops),
        ),
        _c(
            "a sub drop under a drop's first beat",
            any(_drop_hit(song, d, "subdrop", _subdrop) for d in drops),
        ),
        _c("four hits or more with a chance under 1", _chancy(song) >= 4),
        _c(
            "a rising cutoff lane over a build",
            any(
                _rises(ln)
                for s in _named(song, "build")
                for lanes in (s.get("auto") or {}).values()
                for ln in lanes
                if ln["target"] == "cutoff"
                or (parse_fx(ln["target"]) or ("", "", ""))[2] == "cutoff"
            ),
        ),
    ]


def _house(song: dict) -> list[dict]:
    secs = song["sections"]
    drops = [s for s in secs if is_drop(s)]
    basses, loops = _tracks(song, "bass"), _tracks(song, "chords")
    intro = secs[0] if secs else None
    swept = []
    if intro:
        for t in _tracks(song, kind="synth"):
            if _sounding(song, intro, t):
                swept.append(_fx_lane(intro, t, "lowpass", "cutoff"))
    same = (
        bool(swept)
        and all(_rises(ln) for ln in swept)
        and len({str(ln["points"]) for ln in swept if ln}) == 1
    )
    beats = all(
        {b * 16 + k for b in range(s["bars"]) for k in (0, 4, 8, 12)}
        <= kick_set(song, s)
        for s in drops
    )
    stabs = any(
        (p := _pattern(song, s, t["id"])) is not None
        and nt.note_ons(p)
        and sum(x % 4 == 2 for x in nt.note_ons(p)) / len(nt.note_ons(p)) >= 0.75
        for t in loops
        for s in secs
    )
    return [
        _tempo(song, 118, 126),
        _c("a kick on every beat of each drop", bool(drops) and beats),
        _c(
            "the same rising low-pass lane on every sounding synth track of the intro",
            same,
        ),
        _c("a phaser on the loop's track", any(_unit(t, "phaser") for t in loops)),
        _c(
            "a duck of depth 0.8 or more on the bass and on the loop",
            any(_ducked(t, 0.8) for t in basses)
            and any(_ducked(t, 0.8) for t in loops),
        ),
        _c(
            "a slide in the bass",
            any(
                p.get("slides")
                for t in basses
                for s in secs
                if (p := _pattern(song, s, t["id"]))
            ),
        ),
        _c("chord stabs on the offbeats", stabs),
        _c(
            "a bitcrush or a compressor on a track",
            any(_unit(t, "bitcrush", "compress") for t in song["tracks"]),
        ),
        _c(
            "the bass through an envelope filter (an auto-filter its notes open)",
            any(
                (u := _unit(t, "autofilter")) and u["params"]["env"] > 0 for t in basses
            ),
        ),
    ]


def _garage(song: dict) -> list[dict]:
    secs = song["sections"]
    verses = _named(song, "verse")
    kicks = [kick_set(song, s, 1) for s in verses]
    ghosts = []
    for s in verses:
        p = _drums(song, s)
        if p:
            per = [0] * p["bars"]
            for v, step in p["drums"]:  # a hit's own velocity, else its step's
                if dr._ghost(p, v, step) <= 0.4:
                    per[step // p["steps"]] += 1
            ghosts.append(min(per))
    vox = _tracks(song, "vocal")
    pitches = {
        c[0]
        for t in vox
        for s in secs
        if (p := _pattern(song, s, t["id"]))
        for c in p["cells"]
    }
    cut = _cut_into_drop(song)
    all_kicks = set().union(*(kick_set(song, s) for s in secs)) if secs else set()
    return [
        _tempo(song, 128, 136),
        _c(
            "the hats swung by 0.12 of a sixteenth or more, the kick straight",
            song["drum_swing"] >= 0.12
            and bool(all_kicks)
            and all(k % 2 == 0 for k in all_kicks),
        ),
        _c(
            "in the verses no kick on beat 2 or 4, and one on an offbeat",
            bool(kicks)
            and all(
                k and not k & {4, 12} and any(x % 4 == 2 for x in k) for k in kicks
            ),
        ),
        _c(
            "two hits a bar or more at a velocity of 0.4 or less (the verses)",
            bool(ghosts) and min(ghosts) >= 2,
        ),
        _c("a vocal track holding four distinct pitches or more", len(pitches) >= 4),
        _c("a sub with a duck", any(_ducked(t) for t in _tracks(song, "bass"))),
        _c("a room-tone bed", any(_takes(s, "bed") for s in secs)),
        _c("a breakdown of 16 bars or more without drums", _breakdown(song, 16)),
        _c("a vinyl unit", any(_unit(t, "vinyl") for t in song["tracks"])),
        _c("a cut before a drop", cut),
        _c(
            "a pad squashed over the top (an OTT at a depth of 0.3 or more)",
            any(
                (u := _unit(t, "ott")) and u["params"]["depth"] >= 0.3
                for t in _tracks(song, "pad")
            ),
        ),
    ]


def _cut_into_drop(song: dict) -> bool:
    """A section before a drop whose every sounding track's level lane ends
    at 0: the beat of silence before it lands."""
    secs = song["sections"]
    for i, s in enumerate(secs[:-1]):
        if not is_drop(secs[i + 1]):
            continue
        lanes = [
            ln
            for t in song["tracks"]
            for ln in _lanes(s, t["id"])
            if ln["target"] == GAIN
        ]
        sounding = [
            t for t in song["tracks"] if t["kind"] != "audio" and _sounding(song, s, t)
        ]
        if (
            sounding
            and len(lanes) >= len(sounding)
            and all(ln["points"][-1][1] == 0 for ln in lanes)
        ):
            return True
    return False


_DIRT = ("drive", "fold", "bitcrush", "clip")


def _electro(song: dict) -> list[dict]:
    """Just Ice (French electro)."""
    secs = song["sections"]
    drops = [s for s in secs if is_drop(s)]
    basses = _tracks(song, "bass")
    drums = _tracks(song, kind="drums")
    beats = all(
        {b * 16 + k for b in range(s["bars"]) for k in (0, 4, 8, 12)}
        <= kick_set(song, s)
        for s in drops
    )
    # half time: a kick on 1 and none on beat 3, the snare's place
    stomp = any(
        (k := kick_set(song, s)) and 0 in k and 8 not in k and 4 not in k for s in secs
    )
    choir = any(
        float(t["patch"].get("formant_amount", 0.0)) > 0
        for t in _tracks(song, "pad", kind="synth")
    )
    return [
        _tempo(song, 114, 126),
        _c("a kick on every beat of each drop", bool(drops) and beats),
        _c("the bass driven, folded or crushed", any(_unit(t, *_DIRT) for t in basses)),
        _c("a duck on the bass", any(_ducked(t, 0.5) for t in basses)),
        _c("a half-time stomp: the kick on 1 and none on 3", stomp),
        _c("a pad sung through the vowel filter", choir),
        _c("a cut before a drop", _cut_into_drop(song)),
        _c(
            "the drums squashed (a compressor on their bus)",
            any(_unit(t, "compress") for t in drums),
        ),
        _c("the drums punched (a transient shaper on their bus)", _punched(song)),
    ]


def _industrial(song: dict) -> list[dict]:
    """Nine Inch Wails (industrial)."""
    secs = song["sections"]
    energy = [float(s.get("energy") or 0.0) for s in secs]
    drums = _tracks(song, kind="drums")
    metal = any(
        _unit(t, "comb")
        or (
            t["patch"].get("osc1") == "fm"
            and float(t["patch"].get("fm_ratio", 1.0)) % 1 > 0.05
        )
        for t in _tracks(song, kind="synth")
    )
    snarl = any(
        (u := _unit(t, "drive")) is not None and float(u["params"]["amount"]) >= 3.0
        for t in _tracks(song, "lead")
    )
    return [
        _tempo(song, 92, 118),
        _c(
            "quiet and loud: a section at 0.2 energy or under, one at 0.9 or over",
            bool(energy) and min(energy) <= 0.2 and max(energy) >= 0.9,
        ),
        _c(
            "a section with no drums at all (the score)",
            any(not kick_set(song, s) and _drums(song, s) is None for s in secs),
        ),
        _c(
            "the bass folded, crushed or driven",
            any(_unit(t, *_DIRT) for t in _tracks(song, "bass")),
        ),
        _c("the drums crushed", any(_unit(t, "bitcrush", "clip") for t in drums)),
        _c("struck metal: an inharmonic FM voice or a comb", metal),
        _c("a lead driven hard", snarl),
    ]


def _psych(song: dict) -> list[dict]:
    """Psych Haze (neo-psych)."""
    secs = song["sections"]
    drums = _tracks(song, kind="drums")
    synths = _tracks(song, kind="synth")
    moving = ("bass", "lead")
    swim = [t for t in synths if t.get("role") in moving]
    long_room = any(
        (u := _unit(t, "reverb")) is not None and float(u["params"]["decay"]) >= 4.0
        for t in synths
    )
    # half time: a kick on 1, the snare's 3 free of one
    haze = any(
        (k := kick_set(song, s)) and 0 in k and 8 not in k and 4 not in k for s in secs
    )
    return [
        _tempo(song, 96, 124),
        _c("the drums swung (0.08 of a sixteenth or more)", song["drum_swing"] >= 0.08),
        _c(
            "a phaser or a flanger on the bass and on a lead",
            all(
                any(_unit(t, "phaser", "flanger") for t in swim if t["role"] == r)
                for r in moving
            ),
        ),
        _c(
            "a chorus on the bass",
            any(_unit(t, "chorus") for t in _tracks(song, "bass")),
        ),
        _c(
            "a tape echo or a synced delay on a synth track",
            any(_unit(t, "tape_echo") or _synced_delay(t) for t in synths),
        ),
        _c("a long room: a reverb of 4 s or more", long_room),
        _c("the drums phased", any(_unit(t, "phaser") for t in drums)),
        _c("a half-time haze: a kick on 1 and none on 2 or 3", haze),
    ]


def _sixties(song: dict) -> list[dict]:
    """Sixties Studio (60s studio)."""
    secs = song["sections"]
    drums = _tracks(song, kind="drums")
    synths = _tracks(song, kind="synth")
    slap = any(
        (u := _unit(t, "tape_echo")) is not None and float(u["params"]["time"]) <= 0.15
        for t in synths
    )
    tape_keys = any(
        float(t["patch"].get("vibrato_depth", 0.0)) > 0
        for t in _tracks(song, "pad", kind="synth")
    )
    backwards = any(
        t["patch"].get("osc1") == "noise" and float(t["patch"].get("attack", 0)) >= 1.0
        for t in _tracks(song, "fx", kind="synth")
    )
    songs = _named(song, "verse") + _named(song, "chorus")
    plain = bool(songs) and all({0, 8} <= kick_set(song, s, 1) for s in songs)
    toms = any(
        (p := _drums(song, s)) is not None
        and min(dr.hits_per_bar(p, "tom"), default=0) >= 2
        for s in secs
    )
    return [
        _tempo(song, 96, 136),
        _c(
            "the drums dry: no reverb or plate on their bus",
            bool(drums) and not any(_unit(t, "reverb", "plate") for t in drums),
        ),
        _c("the kick on 1 and 3 in the verses and choruses", plain),
        _c("a tom loop: toms in every bar of a section", toms),
        _c("a rotating speaker on a track", any(_unit(t, "rotary") for t in synths)),
        _c("a part double-tracked", any(_unit(t, "doubler") for t in synths)),
        _c("a tape slapback (an echo 150 ms or under)", slap),
        _c("a plate", any(_unit(t, "plate") for t in synths)),
        _c("a pad whose pitch wavers with the tape", tape_keys),
        _c("a backwards cymbal: noise that swells and stops", backwards),
    ]


# --- hip-hop, drum & bass, dubstep, synthwave, future bass -----------------------


def _first_bar(song: dict, sec: dict, voices: tuple[str, ...]) -> set[int]:
    """The sixteenths of a section beat's first bar where any of `voices`
    hits."""
    p = _drums(song, sec)
    if p is None:
        return set()
    per = p["steps"]
    return {int(s) * 16 // per for v, s in p["drums"] if v in voices and int(s) < per}


def _half_time(song: dict, sec: dict) -> bool:
    """The backbeat on beat 3 alone: a snare or a clap on step 8 of the
    first bar and none on 4 or 12."""
    back = _first_bar(song, sec, ("snare", "clap"))
    return 8 in back and not back & {4, 12}


def _rolled(song: dict, sec: dict, voice: str) -> bool:
    """A hit of `voice` in the section's beat struck twice or more inside
    its step (a ratchet: a trap hat roll, a jungle edit)."""
    p = _drums(song, sec)
    if p is None:
        return False
    on = {f"{v}.{int(s)}" for v, s in p["drums"] if v == voice}
    return any(
        k in on and int(h.get("r", 1)) >= 2 for k, h in (p.get("hits") or {}).items()
    )


def _ghosts(song: dict, sec: dict, voice: str = "snare", level: float = 0.4) -> int:
    """How many of `voice`'s hits in the section's beat are at a velocity of
    `level` or less (a hit's own, else its step's)."""
    p = _drums(song, sec)
    if p is None:
        return 0
    return sum(1 for v, s in p["drums"] if v == voice and dr._ghost(p, v, s) <= level)


def _sine_sub(t: dict) -> bool:
    p = t.get("patch") or {}
    return p.get("osc1") == "sine" and float(p.get("cutoff", 2200.0)) <= 300


def _hiphop(song: dict) -> list[dict]:
    """Hip-hop / trap."""
    secs = song["sections"]
    drops = [s for s in secs if is_drop(s)]
    basses = _tracks(song, "bass")
    glide = [
        t
        for t in basses
        if t["patch"].get("osc1") == "sine"
        and float(t["patch"].get("glide", 0.0)) > 0
        and float(t["patch"].get("pitch_env", 0.0)) > 0
    ]
    with_kick = any(
        note_ons(song, s, t) & kick_set(song, s)
        for s in drops
        for t in glide
        if _sounding(song, s, t)
    )
    bell = any(
        t["patch"].get("osc1") in ("fm", "pluck")
        and float(t["patch"].get("sustain", 1.0)) == 0.0
        for t in _tracks(song, "lead")
    )
    return [
        _tempo(song, 130, 150),
        _c(
            "half time in every hook: the snare on 3, none on 2 or 4",
            bool(drops) and all(_half_time(song, d) for d in drops),
        ),
        _c(
            "hat rolls in the hooks (a hat struck twice or more in its step)",
            bool(drops) and all(_rolled(song, d, "hat") for d in drops),
        ),
        _c("an 808: a sine bass falling onto its note, gliding", bool(glide)),
        _c(
            "a slide in the 808's line",
            any(
                p.get("slides")
                for t in glide
                for s in secs
                if (p := _pattern(song, s, t["id"]))
            ),
        ),
        _c("the 808 lands with the kick in the hooks", with_kick),
        _c(
            "a distorted 808 (a drive or a clipper on a bass)",
            any(_unit(t, "drive") for t in glide),
        ),
        _c("a struck melody that dies (a bell or a pluck lead)", bell),
        _c("a section of 8 bars or more with no drums", _breakdown(song, 8)),
    ]


def _dnb(song: dict) -> list[dict]:
    """Drum & bass."""
    secs = song["sections"]
    drops = [s for s in secs if is_drop(s)]
    basses = _tracks(song, "bass")
    two_step = bool(drops) and all(
        {0, 10} <= (k := kick_set(song, d, 1))
        and not k & {4, 12}
        and {4, 12} <= _first_bar(song, d, ("snare",))
        for d in drops
    )
    reese = any(
        t["patch"].get("osc1") == "saw"
        and t["patch"].get("osc2") == "saw"
        and abs(float(t["patch"].get("osc2_detune", 0.0))) >= 10.0
        for t in basses
    )
    subs = [t for t in basses if _sine_sub(t)]
    return [
        _tempo(song, 168, 178),
        _c("the two-step in every drop: kick on 1 and the 'and' of 3", two_step),
        _c(
            "the hats shuffled (0.05 of a sixteenth or more)",
            song["drum_swing"] >= 0.05,
        ),
        _c("a Reese: two saws detuned 10 cents or more on a bass", reese),
        _c("a sine sub under it, ducked", any(_ducked(t) for t in subs)),
        _c("a breakdown of 16 bars or more without drums", _breakdown(song, 16)),
        _c(
            "a pad with a reverb of 4 s or more",
            any(
                (u := _unit(t, "reverb")) and u["params"]["decay"] >= 4.0
                for t in _tracks(song, "pad")
            ),
        ),
        _c(
            "a busier drop: two ghost snares a pass or more",
            any(_ghosts(song, d) >= 2 for d in drops),
        ),
        _c(
            "a riser ending on a drop's first bar line",
            any(_lead_in(song, d, ("riser",)) for d in drops),
        ),
    ]


def _dubstep(song: dict) -> list[dict]:
    """Dubstep / bass music."""
    secs = song["sections"]
    drops = [s for s in secs if is_drop(s)]
    basses = _tracks(song, "bass")
    synced = {
        t["patch"].get("filter_lfo_sync")
        for t in basses
        if t["patch"].get("filter_lfo_sync", "off") != "off"
        and float(t["patch"].get("filter_lfo_depth", 0.0)) > 0
    }
    growl = any(
        (
            float(t["patch"].get("formant_amount", 0.0)) > 0
            or t["patch"].get("osc1") in ("fm", "fm4")
        )
        and _unit(t, "drive", "fold")
        for t in basses
    )
    return [
        _tempo(song, 136, 145),
        _c(
            "half time in every drop: the snare on 3, none on 2 or 4",
            bool(drops) and all(_half_time(song, d) for d in drops),
        ),
        _c("a wobble: a bass whose filter LFO is synced to the clock", bool(synced)),
        _c("wobbles at two clock rates or more", len(synced) >= 2),
        _c("a growl: FM or the vowel filter on a bass, driven or folded", growl),
        _c("a sine sub, ducked", any(_ducked(t) for t in basses if _sine_sub(t))),
        _c("a cut before a drop", _cut_into_drop(song)),
        _c(
            "an impact starting on a drop's first bar line",
            any(_drop_hit(song, d, "impact", _impact) for d in drops),
        ),
        _c(
            "a riser ending on a drop's first bar line",
            any(_lead_in(song, d, ("riser",)) for d in drops),
        ),
    ]


def _synthwave(song: dict) -> list[dict]:
    """Synthwave / 80s."""
    secs = song["sections"]
    choruses = [s for s in secs if is_drop(s)]
    drums = _tracks(song, kind="drums")
    gated = False
    for t in drums:
        kinds = [u["kind"] for u in t.get("fx") or ()]
        gated |= "reverb" in kinds and "vgate" in kinds[kinds.index("reverb") :]
    brass = any(
        float(t["patch"].get("filter_attack", 0.0)) >= 0.02
        and float(t["patch"].get("filter_env", 0.0)) > 0
        for t in _tracks(song, "chords")
    )
    seq = any(
        min(
            (
                len(
                    {s for s in note_ons(song, sec, t) if bar * 16 <= s < bar * 16 + 16}
                )
                for bar in range(sec["bars"])
            ),
            default=0,
        )
        >= 8
        for sec in choruses
        for t in _tracks(song, "bass")
        if _sounding(song, sec, t)
    )
    return [
        _tempo(song, 84, 112),
        _c("the drums straight (no swing)", song["drum_swing"] == 0.0),
        _c("a gated reverb on the drums (a room, then a gate)", gated),
        _c(
            "the backbeat on 2 and 4 in every chorus",
            bool(choruses)
            and all({4, 12} <= _first_bar(song, c, ("snare",)) for c in choruses),
        ),
        _c(
            "a chorused pad",
            any(_unit(t, "chorus") for t in _tracks(song, "pad")),
        ),
        _c("a brass stab whose filter swells open after the note", brass),
        _c("a sequenced bass: eight notes a bar or more in the choruses", seq),
        _c(
            "a lead with a synced delay",
            any(_synced_delay(t) for t in _tracks(song, "lead")),
        ),
    ]


def _future(song: dict) -> list[dict]:
    """Future bass."""
    secs = song["sections"]
    drops = [s for s in secs if is_drop(s)]
    chords = _tracks(song, "chords")
    bent = any(
        abs(float(t["patch"].get("pitch_env", 0.0))) > 0
        or any(
            t["patch"].get(f"mod{k}_dst") == "pitch"
            and t["patch"].get(f"mod{k}_src", "none") != "none"
            for k in range(1, 5)
        )
        for t in _tracks(song, "arp") + _tracks(song, "lead")
    )
    return [
        _tempo(song, 140, 160),
        _c(
            "half time in every drop: the snare on 3, none on 2 or 4",
            bool(drops) and all(_half_time(song, d) for d in drops),
        ),
        _c(
            "supersaw chords (a unison of 5 or more)",
            any(int(t["patch"].get("unison", 1)) >= 5 for t in chords),
        ),
        _c(
            "the chords pumped hard (a duck of 0.8 or more)",
            any(_ducked(t, 0.8) for t in chords),
        ),
        _c("the chords squashed (an OTT)", any(_unit(t, "ott") for t in chords)),
        _c(
            "vocal chops through the vowel filter",
            any(
                float(t["patch"].get("formant_amount", 0.0)) > 0
                for t in _tracks(song, "vocal")
            ),
        ),
        _c("a pitch-bent pluck or lead", bent),
        _c(
            "hat rolls in the drops (a hat struck twice or more in its step)",
            bool(drops) and all(_rolled(song, d, "hat") for d in drops),
        ),
        _c(
            "the roll reaches 32nds with the snare's tune rising",
            any(_roll_peaks(song, s) for s in _named(song, "build")),
        ),
        _c(
            "a riser ending on a drop's first bar line",
            any(_lead_in(song, d, ("riser",)) for d in drops),
        ),
    ]


# --- lo-fi, ambient, chiptune, hardstyle, phonk ----------------------------------


def _bus(song: dict) -> list[dict]:
    """The drum bus's units (the drums track's chain)."""
    return [u for t in _tracks(song, kind="drums") for u in t.get("fx") or ()]


def _kit(song: dict, voice: str) -> dict:
    """The drum track's own parameters for `voice` (the kit's)."""
    t = next(iter(_tracks(song, kind="drums")), None)
    return ((t or {}).get("drum_params") or {}).get(voice) or {}


def _rising_lane(song: dict, t: dict, target: str) -> bool:
    """A curve on the track's sound, or a lane in a section, on `target`
    whose last point is over its first."""
    pts = (t.get("curves") or {}).get(target) or []
    if len(pts) > 1 and pts[-1][1] > pts[0][1]:
        return True
    return any(
        ln["target"] == target and _rises(ln)
        for s in song["sections"]
        for ln in _lanes(s, t["id"])
    )


def _per_bar(song: dict, sec: dict, t: dict) -> int:
    """The fewest note-ons a track has in any bar of a section's pass."""
    ons = note_ons(song, sec, t)
    return min(
        (
            len({s for s in ons if bar * 16 <= s < bar * 16 + 16})
            for bar in range(sec["bars"])
        ),
        default=0,
    )


def _lofi(song: dict) -> list[dict]:
    """Lo-fi hip-hop."""
    secs = song["sections"]
    hooks = [s for s in secs if is_drop(s)]
    keys = _tracks(song, "chords")

    def straight(sec: dict) -> bool:
        p = _drums(song, sec)
        rows = (p or {}).get("swing") or {}
        return p is not None and all(
            float(rows.get(v, 1.0)) == 0.0 for v in ("kick", "snare")
        )

    return [
        _tempo(song, 70, 92),
        _c(
            "the drums swung hard (0.18 of a sixteenth or more)",
            song["drum_swing"] >= 0.18,
        ),
        _c(
            "the kick and the snare held straight under the swung hats",
            bool(hooks) and all(straight(h) for h in hooks),
        ),
        _c(
            "a ghost kick in the hooks (a kick at 0.5 or under)",
            any(_ghosts(song, h, "kick", 0.5) for h in hooks),
        ),
        _c(
            "e-piano keys from 4-operator FM",
            any(t["patch"].get("osc1") == "fm4" for t in keys),
        ),
        _c(
            "the keys on tape (wow and flutter)",
            any(
                (u := _unit(t, "tape"))
                and u["params"]["wow"] + u["params"]["flutter"] > 0
                for t in keys
            ),
        ),
        _c("the drums on tape too", any(u["kind"] == "tape" for u in _bus(song))),
        _c(
            "a drifting pad",
            any(float(t["patch"].get("drift", 0.0)) > 0 for t in _tracks(song, "pad")),
        ),
        _c(
            "a soft sine sub, ducked",
            any(_ducked(t) for t in _tracks(song, "bass") if _sine_sub(t)),
        ),
        _c("a section of 8 bars or more with no drums", _breakdown(song, 8)),
    ]


def _braam(t: dict) -> bool:
    """An fx track whose sound is a low brass-ish hit: saws behind a filter
    that opens a moment after the note, dying."""
    p = t.get("patch") or {}
    return (
        p.get("osc1") in ("saw", "supersaw")
        and float(p.get("filter_attack", 0.0)) >= 0.05
        and float(p.get("filter_env", 0.0)) > 0
        and float(p.get("sustain", 1.0)) == 0.0
    )


def _ambient(song: dict) -> list[dict]:
    """Ambient / cinematic."""
    secs = song["sections"]
    climaxes = [s for s in secs if is_drop(s)]
    pads = _tracks(song, "pad")
    quiet = sum(n for _i, n in quiet_runs(song))
    toms = any(
        i > 0 and len(_first_bar(song, secs[i - 1], ("tom",))) >= 6
        for i, s in enumerate(secs)
        if is_drop(s)
    )
    return [
        _tempo(song, 60, 90),
        _c(
            "sparse drums: a third of the song or more with none",
            quiet * 3 >= sm.song_bars(song),
        ),
        _c("it opens with no drums", bool(secs) and _drums(song, secs[0]) is None),
        _c(
            "a wavetable pad whose table moves (a rising lane on its position)",
            any(
                t["patch"].get("osc1") == "wavetable"
                and _rising_lane(song, t, "wt_pos")
                for t in pads
            ),
        ),
        _c("a shimmer on a pad", any(_unit(t, "shimmer") for t in pads)),
        _c(
            "a hall of 5 s or more on a pad",
            any(
                (u := _unit(t, "reverb")) and u["params"]["decay"] >= 5.0 for t in pads
            ),
        ),
        _c(
            "a drone: a bass swelling in over a second or more, held, drifting",
            any(
                float(t["patch"].get("attack", 0.0)) >= 1.0
                and float(t["patch"].get("sustain", 0.0)) >= 0.9
                and float(t["patch"].get("drift", 0.0)) > 0
                for t in _tracks(song, "bass")
            ),
        ),
        _c(
            "a struck bell: 4-operator FM that dies",
            any(
                t["patch"].get("osc1") == "fm4"
                and float(t["patch"].get("sustain", 1.0)) == 0.0
                for t in song["tracks"]
                if t["kind"] == "synth" and t.get("role") != "bass"
            ),
        ),
        _c(
            "a braam on a climax's first beat",
            any(
                _braam(t) and _hits_first_beat(song, c, t)
                for c in climaxes
                for t in _tracks(song, "fx")
            ),
        ),
        _c("low toms building into a climax (six a bar or more)", toms),
    ]


def _chiptune(song: dict) -> list[dict]:
    """Chiptune / 8-bit."""
    secs = song["sections"]
    choruses = [s for s in secs if is_drop(s)]
    leads = _tracks(song, "lead")
    verb = ("reverb", "plate", "spring", "shimmer")
    fast = bool(choruses) and all(
        any(_per_bar(song, c, t) >= 12 for t in _tracks(song, "arp")) for c in choruses
    )
    return [
        _tempo(song, 120, 160),
        _c("the drums straight (no swing)", song["drum_swing"] == 0.0),
        _c(
            "a square lead with no filter",
            any(
                t["patch"].get("osc1") == "square"
                and t["patch"].get("filter_type") == "none"
                for t in leads
            ),
        ),
        _c(
            "a thinner pulse lead (the pulse table off its square frame)",
            any(
                t["patch"].get("osc1") == "wavetable"
                and t["patch"].get("wt_table") == "pulse"
                and float(t["patch"].get("wt_pos", 0.0)) > 0
                for t in leads
            ),
        ),
        _c("a fast arpeggio: a note every sixteenth in the choruses", fast),
        _c(
            "a triangle bass crushed to a few bits (6 or fewer)",
            any(
                t["patch"].get("osc1") == "triangle"
                and (u := _unit(t, "bitcrush"))
                and u["params"]["bits"] <= 6
                for t in _tracks(song, "bass")
            ),
        ),
        _c(
            "a noise snare (the kit's: no body, tone 0.1 or under)",
            float(_kit(song, "snare").get("tone", 1.0)) <= 0.1,
        ),
        _c(
            "a crushed lead (the crush table, or a bitcrush)",
            any(
                t["patch"].get("wt_table") == "crush"
                and t["patch"].get("osc1") == "wavetable"
                or _unit(t, "bitcrush")
                for t in leads
            ),
        ),
        _c(
            "dry: no reverb on any track or the drums",
            not any(_unit(t, *verb) for t in song["tracks"]),
        ),
        _c("a section of 8 bars or more with no drums", _breakdown(song, 8)),
    ]


def _hardstyle(song: dict) -> list[dict]:
    """Hardstyle."""
    secs = song["sections"]
    drops = [s for s in secs if is_drop(s)]
    kick = _kit(song, "kick")
    bus = {u["kind"] for u in _bus(song)}
    leads = _tracks(song, "lead")
    reverse = [
        t for t in _tracks(song, "bass") if float(t["patch"].get("attack", 0.0)) >= 0.08
    ]
    offbeat = any(
        (ons := note_ons(song, d, t)) and not ons & kick_set(song, d)
        for d in drops
        for t in reverse
        if _sounding(song, d, t)
    )
    return [
        _tempo(song, 145, 160),
        _c(
            "four on the floor in every drop",
            bool(drops) and all({0, 4, 8, 12} <= kick_set(song, d, 1) for d in drops),
        ),
        _c(
            "a long, driven kick (the kit's: 0.6 s or more, drive 3 or more)",
            float(kick.get("decay", 0.0)) >= 0.6 and float(kick.get("drive", 0.0)) >= 3,
        ),
        _c(
            "the kick driven again on the bus (a drive and a clipper)",
            {"drive", "clip"} <= bus,
        ),
        _c(
            "a reverse bass: notes that swell in between the kicks, never on one",
            offbeat,
        ),
        _c(
            "a screech: a hard-synced lead",
            any(t["patch"].get("osc2_sync") == "on" for t in leads),
        ),
        _c(
            "a euphoric supersaw lead (a unison of 5 or more)",
            any(
                t["patch"].get("osc1") == "supersaw"
                and int(t["patch"].get("unison", 1)) >= 5
                for t in leads
            ),
        ),
        _c("a breakdown of 16 bars or more without drums", _breakdown(song, 16)),
        _c("a cut before a drop", _cut_into_drop(song)),
        _c(
            "a riser ending on a drop's first bar line",
            any(_lead_in(song, d, ("riser",)) for d in drops),
        ),
    ]


def _phonk(song: dict) -> list[dict]:
    """Phonk (drift phonk, memphis-style)."""
    secs = song["sections"]
    hooks = [s for s in secs if is_drop(s)]
    cowbells = [
        t
        for t in _tracks(song, "lead")
        if t["patch"].get("osc1") == "fm4"
        and float(t["patch"].get("sustain", 1.0)) == 0.0
        and any(
            float(t["patch"].get(f"fm4_ratio{k}", 1.0)) % 1 > 0.05 for k in range(1, 5)
        )
    ]
    eights = [
        t
        for t in _tracks(song, "bass")
        if t["patch"].get("osc1") == "sine"
        and float(t["patch"].get("pitch_env", 0.0)) > 0
        and float(t["patch"].get("glide", 0.0)) > 0
    ]
    with_kick = any(
        note_ons(song, h, t) & kick_set(song, h)
        for h in hooks
        for t in eights
        if _sounding(song, h, t)
    )
    rims = any(
        v == "rim" for s in secs if (p := _drums(song, s)) for v, _s in p["drums"]
    )
    return [
        _tempo(song, 130, 145),
        _c(
            "a cowbell melody in every hook (a struck, inharmonic 4-op lead)",
            bool(hooks)
            and all(any(_sounding(song, h, t) for t in cowbells) for h in hooks),
        ),
        _c(
            "a distorted 808: a sine falling onto its note, gliding, driven",
            any(_unit(t, *_DIRT) for t in eights),
        ),
        _c("the 808 lands with the kick in the hooks", with_kick),
        _c(
            "hat rolls in the hooks (a hat struck twice or more in its step)",
            bool(hooks) and all(_rolled(song, h, "hat") for h in hooks),
        ),
        _c(
            "a hard clap on 2 and 4 in every hook",
            bool(hooks)
            and all({4, 12} <= _first_bar(song, h, ("clap",)) for h in hooks),
        ),
        _c(
            "the drums crushed (a bitcrush on the drum bus)",
            any(u["kind"] == "bitcrush" for u in _bus(song)),
        ),
        _c(
            "a cowbell in the kit (the rim tuned to 500 Hz or more, playing)",
            float(_kit(song, "rim").get("tune", 0.0)) >= 500 and rims,
        ),
        _c(
            "a dark pad (a low-pass at 2.5 kHz or under)",
            any(
                t["patch"].get("filter_type") in ("lowpass", "ladder")
                and float(t["patch"].get("cutoff", 20000.0)) <= 2500
                for t in _tracks(song, "pad")
            ),
        ),
        _c("a section of 8 bars or more with no drums", _breakdown(song, 8)),
    ]


SIGNATURES = {
    "trance": _trance,
    "melodic_techno": _techno,
    "filter_house": _house,
    "garage": _garage,
    "electro": _electro,
    "industrial": _industrial,
    "psych": _psych,
    "sixties": _sixties,
    "hiphop": _hiphop,
    "dnb": _dnb,
    "dubstep": _dubstep,
    "synthwave": _synthwave,
    "future_bass": _future,
    "lofi": _lofi,
    "ambient": _ambient,
    "chiptune": _chiptune,
    "hardstyle": _hardstyle,
    "phonk": _phonk,
}


def signature(song: dict, style: str | None = None) -> list[dict]:
    """A style's signature checklist against a song: [{text, ok}].
    `style` defaults to the one the song was started in; [] for none."""
    style = style or ((song.get("gen") or {}).get("params") or {}).get("style")
    fn = SIGNATURES.get(style)
    return fn(song) if fn and style in styles.STYLES else []


def context(song: dict) -> dict:
    """What the Song page's checklist draws: the findings, how many are
    open (the badge), and the style's signature for a started song."""
    found = check(song)
    return {
        "found": found,
        "open": len(open_items(found)),
        "signature": signature(song),
    }
