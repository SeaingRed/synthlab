"""Note generators: bass, chords, arp, lead, pad and chops as ordinary note
patterns that follow the chord track.

Every generator is a pure function `(ctx, **params) -> lane`, where the
lane is a notes pattern's own fields (`bars`, `steps_per_bar`, `cells`,
`cuts`, `velocities`, `slides`) — what the drawer's grid edits. Nothing
here knows a song: `context()` reads one into a `Ctx` (the key, the chords
under every step, the kick's steps, the part's register, the seed).

- **Seeded per part.** A generator draws from `rng(ctx, kind)`, a stream
  made from `(seed, kind, role)` with crc32 — never `hash()`, which is
  salted per process — so the same seed writes the same notes in another
  process, and rolling the bass leaves the lead's stream alone.
- **In key, in register, on the grid.** A pitch is a scale tone inside the
  part's two-octave window (`Ctx.low` .. `low + 24`, the rows the drawer
  shows at that octave), on a sixteenth — or, in a key that is not
  seven-tone, a tone of the chord under it: those chords are the key's
  diatonic parent's (`chordtrack.chord_scale`), so A pentatonic minor's
  VI plays its F.
- **A note is a run of cells.** Two notes of one pitch side by side need a
  cut between them or they are one held note (`arrange._merge_runs`);
  `Lane.note` writes it.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass

from ...sequencer import midi, name_of
from . import chordtrack

SPB = 16  # every generated pattern is on a sixteenth grid

# where the bass sits whatever octave the song's root is written in: its
# root between E1 and D#2 (41-78 Hz)
BASS_LOW = (28, 39)
# a part's register, in octaves above the bass's root octave
REGISTER = {
    "sub": 0,
    "bass": 1,
    "chords": 2,
    "pad": 2,
    "arp": 3,
    "lead": 3,
    "vocal": 3,
}


@dataclass(frozen=True)
class Ctx:
    """What a generator writes against."""

    root: int  # MIDI of the key's root in the bass octave
    scale: tuple[int, ...]  # the key's tones, semitones above the root
    chords: tuple  # `chordtrack.resolve` over `bars` at 16 steps a bar
    bars: int
    kick: frozenset  # the steps of a bar (0..15) that hold a kick
    low: int  # the lowest MIDI note the part may use (a root of the key)
    seed: int
    role: str = ""

    @property
    def total(self) -> int:
        return self.bars * SPB

    def chord(self, step: int) -> dict:
        return chordtrack.chord_at(list(self.chords), step)


def rng(ctx: Ctx, kind: str) -> random.Random:
    """The part's own stream: (seed, kind, role), by crc32."""
    return random.Random(zlib.crc32(f"{ctx.seed}:{kind}:{ctx.role}".encode()))


def bass_root(song: dict) -> int:
    """The MIDI note of the key's root in the bass octave."""
    try:
        m = midi(song["root"])
    except Exception:  # noqa: BLE001 - a schema-clean root always parses
        m = 33
    while m < BASS_LOW[0]:
        m += 12
    while m > BASS_LOW[1]:
        m -= 12
    return m


def context(
    song: dict,
    section: dict | None = None,
    *,
    bars: int = 8,
    kick=(0, 4, 8, 12),
    register: str = "bass",
    seed: int = 0,
    role: str = "",
) -> Ctx:
    """A song (and the section whose chords are in force) as a `Ctx` for a
    part in `register` (a `REGISTER` name)."""
    root = bass_root(song)
    return Ctx(
        root=root,
        scale=tuple(chordtrack.scale_of(song)),
        chords=tuple(chordtrack.resolve(song, section, bars, SPB)),
        bars=int(bars),
        kick=frozenset(int(k) % SPB for k in kick),
        low=root + 12 * REGISTER.get(register, 1),
        seed=int(seed),
        role=role or register,
    )


class Lane:
    """A notes pattern being written: runs of cells, the cuts between two
    notes of one pitch, a velocity a step, slides."""

    def __init__(self, bars: int):
        self.bars, self.total = bars, bars * SPB
        self.cells: set[tuple[int, int]] = set()
        self.cuts: set[tuple[int, int]] = set()
        self.vel: dict[int, float] = {}
        self.slides: set[int] = set()

    def note(self, m: int, step: int, length: int = 1, vel=None, slide=False) -> None:
        if not 0 <= step < self.total:
            return
        length = max(1, min(int(length), self.total - step))
        if (m, step - 1) in self.cells:
            self.cuts.add((m, step))  # or it would join the note before it
        if (m, step + length) in self.cells:
            self.cuts.add((m, step + length))
        for s in range(step, step + length):
            self.cells.add((m, s))
            if s > step:
                self.cuts.discard((m, s))
        if vel is not None:
            self.vel[step] = round(min(1.0, max(0.05, float(vel))), 2)
        if slide:
            self.slides.add(step)

    def data(self) -> dict:
        order = sorted(self.cells, key=lambda c: (c[1], c[0]))
        return {
            "bars": self.bars,
            "steps_per_bar": SPB,
            "cells": [[name_of(m), s] for m, s in order],
            "cuts": [
                [name_of(m), s]
                for m, s in sorted(self.cuts, key=lambda c: (c[1], c[0]))
            ],
            "velocities": {str(s): v for s, v in sorted(self.vel.items())},
            "slides": sorted(self.slides),
        }


# --- pitches --------------------------------------------------------------------


def above(low: int, pc: int) -> int:
    """The lowest note of pitch class `pc` at or above `low`."""
    return low + ((pc - low) % 12)


def ladder(ctx: Ctx, low: int | None = None, span: int = 24) -> list[int]:
    """The key's tones from `low` (default: the part's) up `span` semitones."""
    low = ctx.low if low is None else low
    pcs = {(ctx.root + s) % 12 for s in ctx.scale}
    return [m for m in range(low, low + span + 1) if m % 12 in pcs]


def tones(ctx: Ctx, chord: dict, low: int | None = None, span: int = 24) -> list[int]:
    """A chord's tones from `low` up `span` semitones, ascending."""
    low = ctx.low if low is None else low
    pcs = {(ctx.root + s) % 12 for s in chord["tones"]}
    return [m for m in range(low, low + span + 1) if m % 12 in pcs]


def nearest(pitches: list[int], target: float) -> int:
    return min(pitches, key=lambda m: (abs(m - target), m))


# --- bass -----------------------------------------------------------------------

# the sixteenths of a beat each rolling figure plays (0 is the kick's)
FIGURES: dict[str, tuple[int, ...]] = {
    "roll": (1, 2, 3),
    "octave": (1, 2, 3),
    "gallop": (2, 3),
    "offbeat": (2,),
}
_ROLL_VEL = {1: 0.8, 2: 0.95, 3: 0.86}


def bass_rolling(ctx: Ctx, figure: str = "roll", pops: bool = True) -> dict:
    """The rolling bass: on the sixteenths after each kick and never on the
    kick's step — `roll` plays steps 1, 2 and 3 of every beat, `octave` the
    same with the third an octave up, `gallop` 2 and 3, `offbeat` 2 alone —
    each one step long with a cut between them, on the root of the chord
    under that beat. The seed moves where a third note pops up a fifth or
    an octave (`pops`; the sub half of a pair takes none)."""
    steps = FIGURES.get(figure, FIGURES["roll"])
    r = rng(ctx, f"bass_rolling:{figure}")
    lane = Lane(ctx.bars)
    beats = ctx.bars * 4
    # which beats of each two bars lift their last note, and by how much
    popped = {}
    if pops and figure in ("roll", "gallop"):
        for b in r.sample(range(8), r.randint(1, 3)):
            popped[b] = r.choice((7, 12))
    for beat in range(beats):
        base = beat * 4
        root = above(ctx.low, ctx.chord(base)["root"])
        for k in steps:
            step = base + k
            if step % SPB in ctx.kick:
                continue  # never the kick's step
            m = root
            if figure == "octave" and k == 3:
                m = root + 12
            elif k == steps[-1] and beat % 8 in popped:
                # a pop: the chord's own fifth (in key whatever the scale), or
                # the octave
                tones_ = ctx.chord(base)["tones"]
                fifth = (ctx.root + tones_[min(2, len(tones_) - 1)]) % 12
                m = root + 12 if popped[beat % 8] == 12 else above(root + 1, fifth)
            lane.note(m, step, 1, _ROLL_VEL[k])
    return lane.data()


def bass_pair(ctx: Ctx, figure: str = "roll") -> tuple[dict, dict]:
    """The rolling bass as its two tracks from one seed: the mid bass (in
    `ctx`'s register, with its pops) and the sub an octave under it, the
    same figure on the roots alone."""
    sub = Ctx(**{**ctx.__dict__, "low": ctx.low - 12})
    return bass_rolling(ctx, figure), bass_rolling(sub, figure, pops=False)


# a bar of held roots, as (step, length): the seed picks one a chord
_ROOT_CELLS = (
    ((0, 6), (8, 6)),
    ((0, 3), (6, 2), (8, 6)),
    ((0, 8), (10, 4)),
    ((0, 14),),
    ((0, 4), (6, 4), (12, 3)),
)


def bass_roots(ctx: Ctx) -> dict:
    """Long roots under the chords: one rhythm a bar drawn by the seed, the
    note before a chord change sliding into the next root."""
    r = rng(ctx, "bass_root")
    lane = Lane(ctx.bars)
    cell = r.choice(_ROOT_CELLS)
    other = r.choice(_ROOT_CELLS)
    for bar in range(ctx.bars):
        shape = other if bar % 4 == 3 else cell
        for step, length in shape:
            at = bar * SPB + step
            lane.note(above(ctx.low, ctx.chord(at)["root"]), at, length, 0.9)
    return lane.data()


def bass_offbeat(ctx: Ctx) -> dict:
    """A root on every offbeat eighth, two steps long; the seed adds an
    octave on some bars' last offbeat and a sixteenth pickup."""
    r = rng(ctx, "bass_offbeat")
    lane = Lane(ctx.bars)
    lift = set(r.sample(range(4), r.randint(1, 2)))
    pickup = r.randrange(4)
    for beat in range(ctx.bars * 4):
        at = beat * 4 + 2
        root = above(ctx.low, ctx.chord(at)["root"])
        bar = beat // 4
        up = 12 if beat % 4 == 3 and bar % 4 in lift else 0
        lane.note(root + up, at, 1 if up else 2, 0.92)
        if beat % 4 == 3 and bar % 4 == pickup and (at + 1) % SPB not in ctx.kick:
            lane.note(root, at + 1, 1, 0.7)
    return lane.data()


# a bar of a funk line, as (step, length, what): r root, 5 fifth, 8 octave,
# a an approach tone under the next root; the kick's steps are left out
_WALK_CELLS = (
    ((2, 1, "r"), (3, 1, "r"), (6, 2, "8"), (10, 1, "r"), (11, 1, "5"), (14, 2, "r")),
    ((2, 2, "r"), (6, 1, "5"), (7, 1, "r"), (10, 2, "8"), (14, 1, "r"), (15, 1, "a")),
    ((1, 1, "r"), (2, 1, "r"), (6, 2, "r"), (9, 1, "5"), (10, 1, "8"), (14, 2, "5")),
    (
        (2, 1, "r"),
        (3, 1, "8"),
        (6, 1, "r"),
        (7, 1, "r"),
        (10, 2, "5"),
        (13, 1, "r"),
        (14, 1, "r"),
    ),
)


def bass_walk(ctx: Ctx) -> dict:
    """A syncopated sixteenth line on roots, fifths and octaves, short
    notes, with a slide into the octave and an approach tone into the next
    bar: two bar-shapes drawn by the seed, alternating."""
    r = rng(ctx, "bass_walk")
    lane = Lane(ctx.bars)
    a, b = r.sample(_WALK_CELLS, 2)
    scale = ladder(ctx, ctx.low - 12, 48)
    for bar in range(ctx.bars):
        for step, length, what in a if bar % 2 == 0 else b:
            at = bar * SPB + step
            if at % SPB in ctx.kick:
                continue
            root = above(ctx.low, ctx.chord(at)["root"])
            fifth = root + 7 if root + 7 in scale else root + 12
            nxt = above(ctx.low, ctx.chord((at + SPB) % ctx.total)["root"])
            under = max((m for m in scale if ctx.low <= m < nxt), default=None)
            if under is None:  # no room under it: come down onto it instead
                under = min((m for m in scale if m > nxt), default=nxt)
            m = {"r": root, "5": fifth, "8": root + 12, "a": under}[what]
            # the octave is slid into: one slide a bar at least
            lane.note(m, at, length, 0.95 if what == "8" else 0.82, slide=what == "8")
    return lane.data()


def bass_2step(ctx: Ctx) -> dict:
    """A sub on the chord's root in long notes with gaps — one from the
    downbeat, one from the "and" of three — and, where the seed says, a
    fifth sliding into the second."""
    r = rng(ctx, "bass_2step")
    lane = Lane(ctx.bars)
    slid = set(r.sample(range(4), r.randint(1, 2)))
    for bar in range(ctx.bars):
        base = bar * SPB
        root = above(ctx.low, ctx.chord(base)["root"])
        lane.note(root, base, 5, 0.95)
        if bar % 4 in slid:
            tones_ = ctx.chord(base)["tones"]
            fifth = (ctx.root + tones_[min(2, len(tones_) - 1)]) % 12
            lane.note(above(root + 1, fifth), base + 7, 2, 0.7)
            lane.note(root, base + 10, 4, 0.9, slide=True)
        else:
            lane.note(root, base + 10, 4, 0.9)
    return lane.data()


# --- chords, pad ----------------------------------------------------------------


def voice(
    ctx: Ctx, chord: dict, style: str = "close", prev: list[int] | None = None
) -> list[int]:
    """A chord's notes in the part's window. `close`: the inversion nearest
    the one before it (the least movement), or — the first chord — the one
    its slot names, around the middle of the window; `wide`: the root low
    and the rest above it; `anthem`: root, fifth and octave low, the third
    and the fifth above; `power`: root and fifth."""
    pcs = [(ctx.root + s) % 12 for s in chord["tones"]]
    low, top = ctx.low, ctx.low + 24
    root = above(low, pcs[0])
    if style == "power":
        return [root, above(root + 1, pcs[min(2, len(pcs) - 1)])]
    if style == "anthem":
        third = above(root + 12, pcs[1]) if len(pcs) > 1 else root + 12
        fifth = pcs[2] if len(pcs) > 2 else (pcs[0] + 7) % 12
        stack = [root, above(root, fifth), root + 12, third, above(third, fifth)]
        return sorted({m for m in stack if m <= top})
    if style == "wide":
        rest = [above(root + 7, pc) for pc in pcs[1:]]
        return sorted({m for m in [root, *rest] if m <= top})
    centre = low + 12
    best, cost = None, None
    for r in range(len(pcs)):
        order = pcs[r:] + pcs[:r]
        for start in (above(low, order[0]), above(low, order[0]) + 12):
            notes, at = [], start
            for pc in order:
                at = above(at, pc)
                notes.append(at)
                at += 1
            if notes[-1] > top:
                continue
            if prev:
                c = sum(min(abs(n - p) for p in prev) for n in notes)
            else:
                want = int(chord["slot"].get("inv", 0)) % len(pcs)
                c = abs(sum(notes) / len(notes) - centre) + (0 if r == want else 6)
            if cost is None or (c, r, start) < cost:
                best, cost = notes, (c, r, start)
    return best or [root, *[above(root, pc) for pc in pcs[1:]]]


RHYTHMS = ("held", "stabs", "pump", "push")
VOICINGS = ("close", "wide", "anthem", "power")


def chords(
    ctx: Ctx, rhythm: str = "held", voicing: str = "close", lead: bool = True
) -> dict:
    """The progression voiced in the part's register: `held` (one note a
    chord), `stabs` (the offbeat eighths), `pump` (every eighth), `push`
    (each chord a sixteenth early). `lead` False keeps every chord in the
    inversion its slot names (no voice leading: what `from_cells` reads
    back exactly)."""
    r = rng(ctx, f"chords:{rhythm}")
    lane = Lane(ctx.bars)
    prev: list[int] | None = None
    # the seed thins or thickens the stabs: which bar of four drops its
    # first stab, which adds a sixteenth pickup
    drop, add = r.randrange(4), r.randrange(4)
    long = r.random() < 0.5
    for c in ctx.chords:
        notes = voice(ctx, c, voicing, prev if lead else None)
        prev = notes
        lo, hi = c["lo"], c["hi"]
        if rhythm == "stabs":
            hits = [
                (s, 2 if long and s % 8 == 6 else 1)
                for s in range(lo, hi)
                if s % 4 == 2
            ]
            hits = [
                h for h in hits if not (h[0] % SPB == 2 and (h[0] // SPB) % 4 == drop)
            ]
            hits += [
                (s, 1) for s in range(lo, hi) if s % SPB == 15 and (s // SPB) % 4 == add
            ]
        elif rhythm == "pump":
            hits = [(s, 1) for s in range(lo, hi) if s % 2 == 0]
        elif rhythm == "push":
            hits = [(max(0, lo - 1), hi - max(0, lo - 1) - 1)]
        else:
            hits = [(lo, hi - lo)]
        for step, length in sorted(hits):
            for m in notes:
                lane.note(m, step, length)
            lane.vel[step] = 0.9 if step % 4 == 0 else 0.82
    return lane.data()


def pad(ctx: Ctx, voicing: str = "wide") -> dict:
    """Held chords, wide: the bed under everything."""
    return chords(ctx, "held", voicing)


# --- arp, lead, chops -----------------------------------------------------------

ARP_MODES = ("up", "down", "updown", "random", "pedal")


def arp(
    ctx: Ctx, mode: str = "up", rate: int = 1, gate: int = 1, octaves: int = 2
) -> dict:
    """The chord's tones as explicit notes, one every `rate` sixteenths
    (1 or 2), each `gate` steps long, over `octaves`: up, down, up and
    down, a seeded walk, or a pedal on the root between the others. The
    figure restarts at every chord."""
    r = rng(ctx, f"arp:{mode}")
    lane = Lane(ctx.bars)
    rate = 2 if int(rate) >= 2 else 1
    gate = max(1, min(int(gate), rate))
    for c in ctx.chords:
        pool = tones(ctx, c, ctx.low, 12 * max(1, min(int(octaves), 2)))
        if not pool:
            continue
        if mode == "down":
            seq = pool[::-1]
        elif mode == "updown":
            seq = pool + pool[-2:0:-1]
        elif mode == "pedal":
            seq = [x for m in pool[1:] for x in (pool[0], m)] or pool
        else:
            seq = list(pool)
        last = None
        for i, step in enumerate(range(c["lo"], c["hi"], rate)):
            if mode == "random":
                m = r.choice([p for p in pool if p != last] or pool)
            else:
                m = seq[i % len(seq)]
            last = m
            lane.note(m, step, gate, 0.92 if step % 4 == 0 else 0.76)
    return lane.data()


# a bar of melody, as (step, length); two are drawn a motif
_LEAD_CELLS = (
    ((0, 3), (4, 2), (8, 4), (14, 2)),
    ((0, 2), (3, 1), (6, 2), (8, 3), (12, 4)),
    ((2, 2), (4, 2), (8, 2), (10, 2), (12, 4)),
    ((0, 4), (6, 2), (8, 2), (11, 1), (12, 3)),
    ((0, 6), (8, 2), (10, 2), (12, 2), (14, 2)),
)
_CONTOURS = {
    "arch": (1, 1, 1, -1, -1, 0),
    "rise": (1, 0, 1, 1, -1, 1),
    "fall": (-1, 0, -1, 1, -1, -1),
}


def lead(ctx: Ctx, contour: str = "arch", density: float = 0.6) -> dict:
    """A two-bar motif on chord tones with passing scale tones, answered in
    bars 3-4 (the same rhythm and steps, fitted to their chords, landing on
    a chord tone), then both again in 5-8 with a note or two changed and
    the last bar coming home to the root."""
    r = rng(ctx, "lead")
    lane = Lane(ctx.bars)
    scale = ladder(ctx)
    shapes = r.sample(_LEAD_CELLS, 2)
    if density < 0.45:
        shapes = [s[::2] for s in shapes]
    moves = [
        r.choice((-2, -1, 1, 2)) * (1 if d >= 0 else -1) if d else r.choice((-1, 1))
        for d in _CONTOURS.get(contour, _CONTOURS["arch"])
    ]
    start = r.choice((0.35, 0.5, 0.65))
    changed = set(r.sample(range(8), 2))
    for phrase in range(0, ctx.bars, 2):
        at = scale.index(nearest(scale, ctx.low + 24 * start))
        n = 0
        for half in range(2):
            bar = phrase + half
            if bar >= ctx.bars:
                break
            for step, length in shapes[half]:
                pos = bar * SPB + step
                chord_tones = tones(ctx, ctx.chord(pos))
                m = scale[max(0, min(len(scale) - 1, at))]
                last = (step, length) == shapes[half][-1]
                if step % 8 == 0 or (last and half):
                    m = nearest(chord_tones, m)  # a strong beat lands on the chord
                if last and half and bar == ctx.bars - 1:
                    m = nearest(
                        [p for p in chord_tones if p % 12 == ctx.chord(pos)["root"]]
                        or chord_tones,
                        m,
                    )
                lane.note(m, pos, length, 0.95 if step % 4 == 0 else 0.8)
                at = scale.index(m) if m in scale else at
                move = moves[n % len(moves)]
                if phrase >= 4 and n in changed:
                    move = -move
                at += move
                n += 1
    return lane.data()


def chops(ctx: Ctx) -> dict:
    """Short pitched hits across the chord's tones, call and answer, with a
    stutter (one pitch three sixteenths running) at the end of each
    phrase: what a chopped vocal plays."""
    r = rng(ctx, "chops")
    lane = Lane(ctx.bars)
    calls = ((0, 3, 6, 8), (0, 2, 6, 10), (3, 6, 8, 11), (0, 4, 7, 10))
    call, answer = r.sample(calls, 2)
    turn = r.randrange(4)
    for bar in range(ctx.bars):
        pool = tones(ctx, ctx.chord(bar * SPB), ctx.low + 5, 14)
        if not pool:
            continue
        shape = call if bar % 2 == 0 else answer
        for i, step in enumerate(shape):
            m = pool[(i + turn + bar // 2) % len(pool)]
            lane.note(
                m,
                bar * SPB + step,
                2 if i == len(shape) - 1 else 1,
                0.9 if i == 0 else 0.78,
            )
        if bar % 2 == 1:  # the phrase's end stutters
            m = pool[(turn + bar) % len(pool)]
            for k in (13, 14, 15):
                lane.note(m, bar * SPB + k, 1, 0.55 + 0.15 * (k - 13))
    return lane.data()


def hit(ctx: Ctx, bars: int = 1) -> dict:
    """One note, the key's root at the bottom of the part's window, on the
    pattern's first step, held `bars` bars: a drop FX's single
    hit — an impact, a sub drop, a downlifter — whose sound does the rest."""
    lane = Lane(ctx.bars)
    root = above(ctx.low, ctx.root % 12)
    lane.note(root, 0, max(1, min(int(bars), ctx.bars)) * SPB, 1.0)
    return lane.data()


# --- derived variants -----------------------------------------------------------


def runs(lane: dict) -> list[tuple[str, int, int]]:
    """A lane's notes as (pitch, start, length), in time order."""
    cuts = {(c[0], int(c[1])) for c in lane.get("cuts") or []}
    by: dict[str, list[int]] = {}
    for p, s in lane.get("cells") or []:
        by.setdefault(p, []).append(int(s))
    out = []
    for p, steps in by.items():
        steps.sort()
        start = prev = steps[0]
        for s in steps[1:]:
            if s != prev + 1 or (p, s) in cuts:
                out.append((p, start, prev - start + 1))
                start = s
            prev = s
        out.append((p, start, prev - start + 1))
    return sorted(out, key=lambda n: (n[1], midi(n[0]), n[0]))


def note_ons(lane: dict) -> set[int]:
    """The steps a note starts on."""
    return {start for _p, start, _n in runs(lane)}


def _strength(ctx: Ctx, pitch: str, step: int) -> tuple:
    """How much a note matters: on the beat, then a chord tone."""
    pcs = {(ctx.root + s) % 12 for s in ctx.chord(step % ctx.total)["tones"]}
    grid = 3 if step % 16 == 0 else 2 if step % 4 == 0 else 1 if step % 2 == 0 else 0
    return (grid, midi(pitch) % 12 in pcs)


def sparser(ctx: Ctx, lane: dict, amount: float = 0.25) -> dict:
    """The lane with its weakest notes gone — off the beat and not a chord
    tone first — about `amount` of them, never the last one. Only removes."""
    notes = runs(lane)
    count = min(len(notes) - 1, max(1, round(len(notes) * max(0.0, min(1.0, amount)))))
    if count <= 0:
        return dict(lane)
    r = rng(ctx, "sparser")
    order = sorted(notes, key=lambda n: (_strength(ctx, n[0], n[1]), r.random()))
    gone = {(p, s) for p, start, n in order[:count] for s in range(start, start + n)}
    cells = [list(c) for c in lane["cells"] if (c[0], int(c[1])) not in gone]
    on = {(c[0], int(c[1])) for c in cells}
    return {
        **lane,
        "cells": cells,
        "cuts": [list(c) for c in lane.get("cuts") or [] if (c[0], int(c[1])) in on],
    }


def busier(ctx: Ctx, lane: dict, amount: float = 0.25) -> dict:
    """The lane with chord tones added on silent offbeats, about `amount`
    as many again as it has, one step long each. Only adds."""
    notes = runs(lane)
    taken = {int(c[1]) for c in lane.get("cells") or []}
    free = [
        s
        for s in range(int(lane["bars"]) * SPB)
        if s not in taken and s % SPB not in ctx.kick
    ]
    count = min(len(free), max(1, round(len(notes) * max(0.0, min(1.0, amount)))))
    r = rng(ctx, "busier")
    centre = sum(midi(p) for p, _s, _n in notes) / len(notes) if notes else ctx.low + 12
    # the lane as it is, note for note (its cuts with it), then the new ones:
    # `Lane.note` cuts a new note off a neighbour of its own pitch
    out = Lane(int(lane["bars"]))
    for pitch, start, length in notes:
        out.note(midi(pitch), start, length)
    out.vel = {int(k): v for k, v in (lane.get("velocities") or {}).items()}
    out.slides = {int(x) for x in lane.get("slides") or []}
    for step in sorted(r.sample(free, count)):
        pool = tones(ctx, ctx.chord(step % ctx.total))
        out.note(nearest(pool, centre) if pool else ctx.low, step, 1)
        out.vel.setdefault(step, 0.7)
    return {**lane, **out.data()}


def transpose(lane: dict, semitones: int) -> dict:
    """A lane moved by whole semitones (an octave keeps it in key)."""

    def up(cells):
        return [[name_of(midi(p) + semitones), s] for p, s in cells]

    return {**lane, "cells": up(lane["cells"]), "cuts": up(lane.get("cuts") or [])}


GENERATORS = {
    "bass_rolling": bass_rolling,
    "bass_root": bass_roots,
    "bass_offbeat": bass_offbeat,
    "bass_walk": bass_walk,
    "bass_2step": bass_2step,
    "chords": chords,
    "pad": pad,
    "arp": arp,
    "lead": lead,
    "chops": chops,
    "hit": hit,
}
# the register a kind is written in unless the track says otherwise
KIND_REGISTER = {
    "bass_rolling": "bass",
    "bass_root": "bass",
    "bass_offbeat": "bass",
    "bass_walk": "bass",
    "bass_2step": "sub",
    "chords": "chords",
    "pad": "pad",
    "arp": "arp",
    "lead": "lead",
    "chops": "vocal",
    "hit": "sub",
}


def generate(kind: str, ctx: Ctx, **params) -> dict:
    """`kind`'s lane for `ctx`; unknown parameters are refused by the
    generator (a TypeError), an unknown kind is a KeyError."""
    return GENERATORS[kind](ctx, **params)
