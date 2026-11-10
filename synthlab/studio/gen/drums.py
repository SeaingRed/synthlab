"""Drum generators: a beat in a style, a fill, a build's roll, and thinned
or busier variants — each a drums pattern's own fields (`bars`, `steps`,
`drums`, `drum_vel`, and the per-hit `hits` and row `swing`), what the
drawer's drum grid edits.

A hit may carry its own velocity, chance, ratchet, nudge and tune
(`arrange.drum_hits`; drums.md, "The per-hit model"), so a beat's ghosts
are quiet on their own steps, a style's percussion has a chance a hit, a
build's roll reaches 32nds by ratchet with the snare's pitch climbing, and
"busier" adds ghosts before it adds anything loud. Every lane here has
`hits` and `swing` (empty when it has none: the pool's schema drops an
empty one, so a pattern without per-hit data is stored as it always was).

Seeded with crc32 of `(seed, kind)`; pure.
"""

from __future__ import annotations

import random
import zlib

from .. import beatgen

SPB = 16

HATS = ("hat", "ohat", "crash")
# what `thin` keeps for each named variant of a beat
THIN = {
    "hats": ("hat", "ohat", "rim", "crash"),
    "kick": ("kick",),
    "nokick": ("snare", "hat", "ohat", "clap", "tom", "rim", "crash"),
    "kickhat": ("kick", "hat", "ohat"),
    "perc": ("hat", "ohat", "rim", "tom", "clap"),
}

# one bar of fill, in sixteenths: (voice, steps). The seed picks one a style.
FILLS: dict[str, tuple[tuple[tuple[str, tuple[int, ...]], ...], ...]] = {
    "roll": (
        (("kick", (0, 4)), ("snare", (8, 10, 12, 13, 14, 15))),
        (("kick", (0,)), ("snare", (4, 6, 8, 10, 12, 13, 14, 15))),
        (("kick", (0, 4)), ("tom", (8, 9, 12, 13)), ("snare", (10, 11, 14, 15))),
    ),
    "quiet": (
        (("kick", (0,)), ("rim", (11, 13, 15)), ("snare", (14,))),
        (("kick", (0, 10)), ("rim", (13,)), ("snare", (12, 14, 15))),
    ),
    "toms": (
        (
            ("kick", (0, 4, 8)),
            ("tom", (10, 11, 13, 14)),
            ("clap", (12,)),
            ("snare", (15,)),
        ),
        (("kick", (0, 4)), ("tom", (6, 9, 12, 14)), ("rim", (10, 13, 15))),
    ),
}
# the fill table a beat style reaches for (`fill(table=None, style=...)`):
# garage fills are short and quiet, techno's are toms, the rest roll
STYLE_FILL = {
    "two-step": "quiet",
    "garage-rim": "quiet",
    "garage-4x4": "quiet",
    "melodic-techno": "toms",
    "psych": "toms",
    "psych-haze": "toms",
    "sixties-toms": "toms",
    "hypnotic-perc": "toms",
    "techno": "toms",
    "breaks": "toms",
    # boom bap's fills are quiet, the 80s drum
    # machine's are toms, jungle's roll round the kit
    "boom-bap": "quiet",
    "synthwave": "toms",
    "synthpop": "toms",
    "jungle": "toms",
    # lo-fi's fills are quiet, the cinematic build's are toms
    "lofi": "quiet",
    "lofi-chill": "quiet",
    "cinematic-toms": "toms",
    "ambient-pulse": "toms",
}
VARIANTS = ("A", "B", "C", "D")  # a beat's variations, beside its fill


def rng(seed: int, kind: str) -> random.Random:
    return random.Random(zlib.crc32(f"{int(seed)}:{kind}".encode()))


def _lane(
    bars: int,
    steps: int,
    cells,
    vel: dict | None = None,
    hits: dict | None = None,
    swing: dict | None = None,
) -> dict:
    total = bars * steps
    on = sorted({(v, int(s)) for v, s in cells if 0 <= int(s) < total})
    names = {f"{v}.{s}" for v, s in on}
    return {
        "bars": bars,
        "steps": steps,
        "drums": [[v, s] for v, s in on],
        "drum_vel": {
            str(k): v for k, v in sorted((vel or {}).items()) if 0 <= k < total
        },
        # a hit's data needs its cell
        "hits": {
            k: dict(hits[k]) for k in sorted(hits or {}) if k in names and hits[k]
        },
        "swing": dict(swing or {}),
    }


def lane_of(pattern: dict) -> dict:
    """A drums pattern (or any dict with its fields) as a lane."""
    return _lane(
        int(pattern["bars"]),
        int(pattern["steps"]),
        pattern["drums"],
        _vel(pattern),
        pattern.get("hits"),
        pattern.get("swing"),
    )


def _vel(lane: dict) -> dict[int, float]:
    return {int(k): float(v) for k, v in (lane.get("drum_vel") or {}).items()}


def _hits(lane: dict) -> dict[str, dict]:
    return {k: dict(v) for k, v in (lane.get("hits") or {}).items()}


def _same(lane: dict, cells, vel=None, hits=None) -> dict:
    """The lane with other cells (and velocities / per-hit data): its
    shape and row swings kept."""
    return _lane(
        lane["bars"],
        lane["steps"],
        cells,
        _vel(lane) if vel is None else vel,
        _hits(lane) if hits is None else hits,
        lane.get("swing"),
    )


def beat(
    style: str,
    seed: int,
    bars: int = 1,
    steps: int = SPB,
    density: float | None = None,
    vel: dict | None = None,
    meter: str = "4/4",
) -> dict:
    """`beatgen`'s beat in `style` over `bars` (of `meter`: `steps` is
    then that meter's step count), with the style's feel
    (`beatgen.dress`: ghosts, a chance a hit, the rows' own swing);
    `density` 0..1 scales the style's sprinkles (0.5 is the style as
    written); `vel` is a kit's own velocity lane for one bar
    (`drumfactory.DRUM_KITS[...]["vel"]`), tiled."""
    cells = beatgen.generate(
        style, int(seed), bars, steps, density=density, meter=meter
    )
    lane = {int(k): float(v) for k, v in (vel or {}).items()}
    tiled = {
        k + b * steps: v for b in range(bars) for k, v in lane.items() if k < steps
    }
    feel = beatgen.dress(style, int(seed), cells, bars, steps, tiled, meter=meter)
    return _lane(bars, steps, feel["drums"], tiled, feel["hits"], feel["swing"])


def tile(lane: dict, bars: int) -> dict:
    """The lane repeated out to `bars` (hits, velocities, per-hit data)."""
    period = lane["bars"] * lane["steps"]
    reps = range(0, bars * lane["steps"], period)
    cells = [(v, s + k) for v, s in lane["drums"] if s < period for k in reps]
    vel = {s + k: x for s, x in _vel(lane).items() if s < period for k in reps}
    hits = {}
    for key, h in _hits(lane).items():
        voice, _dot, raw = key.partition(".")
        if int(raw) < period:
            for k in reps:
                hits[f"{voice}.{int(raw) + k}"] = dict(h)
    return _lane(bars, lane["steps"], cells, vel, hits, lane.get("swing"))


def thin(lane: dict, keep=None, drop=None) -> dict:
    """The lane with voices taken out: only `keep`'s, or all but `drop`'s
    (a `THIN` name, or voices). Only ever removes."""
    keep = THIN.get(keep, keep) if isinstance(keep, str) else keep
    drop = THIN.get(drop, drop) if isinstance(drop, str) else drop
    cells = [
        (v, s)
        for v, s in lane["drums"]
        if (keep is None or v in keep) and (drop is None or v not in drop)
    ]
    return _same(lane, cells)


def kick_steps(lane: dict) -> set[int]:
    """The sixteenths of a bar on which the lane's kick ever lands."""
    per = lane["steps"]
    return {int(s) % per * SPB // per for v, s in lane["drums"] if v == "kick"}


def fill(
    lane: dict,
    seed: int,
    length: float = 1.0,
    table: str | None = "roll",
    keep=(),
    style: str | None = None,
    at_bar: int | None = None,
) -> dict:
    """The lane with the last `length` bars (1/2, 1 or 2) of bar `at_bar`
    (1-based; default the lane's last) replaced by a seeded snare / tom /
    kick figure from `table` (`FILLS`; None picks the `style`'s own:
    `STYLE_FILL`), its velocity rising to the bar line and its last two
    sixteenths ratcheted. What comes before the fill is untouched; `keep`
    names voices that play on under it (a style whose kick and open hat
    never stop)."""
    steps, total = lane["steps"], lane["bars"] * lane["steps"]
    table = table or STYLE_FILL.get(style or "", "roll")
    end = total if at_bar is None else min(total, max(1, int(at_bar)) * steps)
    n = max(steps // 2, min(end, int(round(length * steps))))
    start = end - n
    figure = rng(seed, f"fill:{table}").choice(FILLS.get(table, FILLS["roll"]))
    scale = steps / SPB

    def out(s: int) -> bool:  # outside the fill's window
        return s < start or s >= end

    cells = [(v, s) for v, s in lane["drums"] if out(s) or v in keep]
    vel = {s: x for s, x in _vel(lane).items() if out(s)}
    hits = {
        k: h
        for k, h in _hits(lane).items()
        if out(int(k.partition(".")[2])) or k.partition(".")[0] in keep
    }
    last_bar = end - steps
    wrote: list[tuple[str, int]] = []
    for voice, at in figure:
        for s16 in at:
            if not (s16 * scale).is_integer():
                continue
            step = last_bar + int(s16 * scale)
            if step >= start:
                wrote.append((voice, step))
    cells += wrote
    if n > steps:  # a two-bar fill: the bar before it thins to the kick and hats
        cells += [
            (v, s)
            for v, s in lane["drums"]
            if start <= s < last_bar and v in ("kick", *HATS)
        ]
    for step in sorted({s for _v, s in cells if max(start, last_bar) <= s < end}):
        vel[step] = round(0.5 + 0.5 * (step - last_bar) / max(1, steps - 1), 2)
    for voice, step in wrote:  # the last eighth of a roll doubles up
        if voice == "snare" and table == "roll" and step >= end - max(1, steps // 8):
            hits[f"{voice}.{step}"] = {"r": 2}
    return _same(lane, cells, vel, hits)


def fill_every(
    lane: dict, seed: int, every: int, length: float = 1.0, style: str | None = None
) -> dict:
    """A fill at the end of every `every`-th bar of the lane (the lane's
    last bar always): bars `every`, 2 x `every`, ... each from its own
    seeded draw, the bars between untouched."""
    every = max(1, int(every))
    marks = sorted({*range(every, lane["bars"] + 1, every), lane["bars"]})
    for bar in marks:
        lane = fill(lane, seed + bar, min(length, 1.0), None, style=style, at_bar=bar)
    return lane


def build(bars: int = 8, seed: int = 0, steps: int = SPB) -> dict:
    """A build's roll over `bars`: the snare in quarters, then eighths,
    then sixteenths, then 32nds — the last by RATCHET (two hits a step) —
    so a bar never has fewer hits than the one before. Each snare hit
    carries its own velocity, 0.4 rising to full, and its own tune, rising
    seven semitones (`beatgen.ROLL_TUNE`), while the kick thins out —
    every beat, every other, then none. The seed moves where the claps
    answer. The kick, the claps and the hats keep their own level: the
    velocity is the roll's, not the step's."""
    bars = max(2, int(bars))
    total = bars * steps
    r = rng(seed, "build")
    q, e = steps // 4, steps // 8
    cells: list[tuple[str, int]] = []
    hits: dict[str, dict] = {}
    clap_at = r.choice((2, 3))
    for bar in range(bars):
        t = bar / bars
        every = q if t < 0.25 else e if t < 0.5 else max(1, steps // 16)
        base = bar * steps
        cells += [("snare", base + s) for s in range(0, steps, every)]
        if t < 0.5:
            cells += [("kick", base + s) for s in range(0, steps, q)]
        elif t < 0.75:
            cells += [("kick", base + s) for s in range(0, steps, 2 * q)]
        if t < 0.5 and bar % 2 == 1:
            cells.append(("clap", base + clap_at * q))
        if t >= 0.5:
            cells += [("hat", base + s) for s in range(e, steps, q)]
    roll = sorted(s for v, s in cells if v == "snare")
    lo, hi = beatgen.ROLL_VELOCITY
    for s in roll:
        t = s / max(1, total - 1)
        hit: dict = {"v": round(lo + (hi - lo) * t, 2)}
        if round(beatgen.ROLL_TUNE * t, 1):
            hit["t"] = round(beatgen.ROLL_TUNE * t, 1)
        if s >= total * 3 // 4:  # the last quarter: 32nds
            hit["r"] = 2
        hits[f"snare.{s}"] = hit
    hits[f"snare.{roll[-1]}"]["v"] = hi
    hits[f"snare.{roll[-1]}"]["t"] = beatgen.ROLL_TUNE
    return _lane(bars, steps, cells, {}, hits)


def _weight(voice: str, step: int, steps: int) -> int:
    """How much a hit carries the beat: a kick or a backbeat on the beat
    most, a sprinkle between the eighths least."""
    q = steps // 4
    on = 2 if step % q == 0 else 1 if step % max(1, q // 2) == 0 else 0
    return on + (3 if voice == "kick" else 2 if voice in ("clap", "snare") else 0)


def _held(lane: dict) -> set[tuple[str, int]]:
    """The hits "sparser" never takes: a kick on a beat, and the backbeat
    — a clap or a snare on a beat (2 and 4, or a half-time 3) that is no
    ghost."""
    q = lane["steps"] // 4
    return {
        (v, s)
        for v, s in ((v, int(s)) for v, s in lane["drums"])
        if s % q == 0
        and (v == "kick" or (v in ("clap", "snare") and _ghost(lane, v, s) > 0.4))
    }


def _ghost(lane: dict, voice: str, step: int) -> float:
    """A hit's velocity: its own, else its step's."""
    h = (lane.get("hits") or {}).get(f"{voice}.{step}") or {}
    return float(h.get("v", (lane.get("drum_vel") or {}).get(str(step), 1.0)))


def sparser(lane: dict, amount: float = 0.25, seed: int = 0) -> dict:
    """The lane with its weakest hits gone, about `amount` of them: the
    ghosts first (a velocity of 0.4 or less), then what sits off the
    eighths, then off the beat — never a kick on a beat and never the
    backbeat. Only removes."""
    hits = [(v, int(s)) for v, s in lane["drums"]]
    held = _held(lane)
    free = [h for h in hits if h not in held]
    count = min(len(free), max(1, round(len(hits) * max(0.0, min(1.0, amount)))))
    if count <= 0:
        return _same(lane, hits)
    r = rng(seed, "sparser")
    order = sorted(
        free,
        key=lambda h: (
            _ghost(lane, *h) > 0.4,
            _weight(h[0], h[1], lane["steps"]),
            r.random(),
        ),
    )
    gone = set(order[:count])
    return _same(lane, [h for h in hits if h not in gone])


def busier(lane: dict, amount: float = 0.25, seed: int = 0) -> dict:
    """The lane with hits added, about `amount` as many again as it has:
    ghosts first — a snare or a rim at a velocity of 0.3 on the sixteenth
    before or after a backbeat —, then hats on the empty offbeat eighths,
    then quiet hats and rims on the sixteenths between. Never a kick, never
    a loud snare: the backbeat stays the loudest thing on its beat. Only
    adds."""
    hits = {(v, int(s)) for v, s in lane["drums"]}
    steps, total = lane["steps"], lane["bars"] * lane["steps"]
    q, e = steps // 4, max(1, steps // 8)
    count = max(1, round(len(hits) * max(0.0, min(1.0, amount))))
    r = rng(seed, "busier")
    back = sorted(
        s for v, s in hits if v in ("clap", "snare") and s % steps in (q, 3 * q)
    )
    ghosts = [
        (v, s, 0.3)
        for b in back
        for s in (b - 1, b + 1)
        if 0 <= s < total
        for v in ("snare", "rim")
    ]
    offbeat = [("hat", s, None) for s in range(e, total, 2 * e)]
    between = [(v, s, 0.5) for v in ("hat", "rim") for s in range(total)]
    out, data, added = set(hits), _hits(lane), 0
    # a third of what is added may be ghosts; the offbeats and the
    # sixteenths between take the rest, in that order
    for pool, most in (
        (ghosts, max(1, round(count / 3))),
        (offbeat, count),
        (between, count),
    ):
        free = [c for c in pool if (c[0], c[1]) not in out]
        r.shuffle(free)
        steps_used: set[int] = set()
        took = 0
        for voice, s, v in free:
            if added >= count or took >= most:
                break
            if pool is ghosts and s in steps_used:
                continue  # one ghost a step
            out.add((voice, s))
            steps_used.add(s)
            if v is not None:
                data[f"{voice}.{s}"] = {"v": v}
            added += 1
            took += 1
    return _same(lane, out, None, data)


def variation(
    lane: dict, seed: int, amount: float = 0.25, style: str | None = None
) -> dict:
    """A variation of a beat: the same groove, moved a little — about
    `amount` of its light hits traded (the weakest out, as many ghosts and
    offbeats in), its kicks on the beat and its backbeat untouched."""
    return busier(sparser(lane, amount, seed), amount, seed + 1)


def hits_per_bar(lane: dict, voice: str | None = None) -> list[int]:
    """How many hits each bar holds (one voice's, or all), a ratcheted
    step counted as the hits it plays."""
    per = lane["steps"]
    out = [0] * lane["bars"]
    data = lane.get("hits") or {}
    for v, s in lane["drums"]:
        if (voice is None or v == voice) and 0 <= s < per * lane["bars"]:
            out[int(s) // per] += int((data.get(f"{v}.{s}") or {}).get("r", 1))
    return out
