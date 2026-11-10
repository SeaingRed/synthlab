"""Breeding board: draw a new generation of patches, measure each once on
the breeder's probe, pick eight, and log every pick to the SpaceStore.

Every candidate is rendered exactly once — on `explore.probe`'s listening
note (A3, a phrase-length gate and the patch's own release, synced LFOs at
the session tempo) — and that one render serves the quiet gate, the
steering score, the novelty gate, the diversity pick, the tile's spectrum
and numbers, and its name. Steering used to render a 40-candidate pool at
0.5 s and then render the 20 it kept all over again.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np

from ..explore import DIRECTIONS, SpaceStore, generation
from ..explore.breed import litter_profile
from ..explore.probe import PROBE_BPM, PROBE_PITCH, Probe, distance, probe, probe_many
from ..params import PARAMS, norm
from ..params import live as live_genes
from ..synth import Patch
from . import genome
from .namegen import sound_name
from .paramui import spec_of
from .soundmeta import band_magnitudes

log = logging.getLogger(__name__)

# the probe's note, for anything that wants to play what a tile was measured
# on (routes/breed.py's /breed/note/ preview)
NOTE_PITCH = PROBE_PITCH
NOTE_DURATION = 0.6

# minimum standardised distance (explore/scale.py: one unit = one library
# spread per measure) from every parent for a child to count as audibly new.
# The old 0.15 was on raw features, where crest factor alone moved 1-5
# units, so it rejected nothing. On the standardised scale an inaudible
# change (glide on one note, a 10 % cutoff nudge) measures < 0.1, a one-knob
# move 0.3-0.9, and a normal litter's median child ~3; 0.75 rejects ~10 % of
# a normal pool. Tame is *meant* to make one-knob moves (its median child
# is ~0.5), so its gate only throws out the near-clones.
_NOVELTY_EPS = 0.75
_NOVELTY_EPS_TAME = 0.3


def _novelty_eps(amount: float) -> float:
    tame = litter_profile(amount).level == "tame"
    return _NOVELTY_EPS_TAME if tame else _NOVELTY_EPS


@dataclass
class Tile:
    id: str
    name: str
    sub_pct: float
    centroid: int
    spectrum: list[float] = field(default_factory=list)
    badges_genes: list[str] = field(default_factory=list)
    # measured against the parent it came from: "+0.6 oct", "-0.3 oct"
    vs_parent: str = ""
    # a layered Sound's parts, for the card's chips: [{"role", "label"}]
    # (main first); empty for a plain patch
    chips: list[dict] = field(default_factory=list)
    layered: bool = False
    # what the card changed from its parent, biggest first (`changes`):
    # [{"label", "from", "to", "a", "b"}]; empty on a board saved by an
    # older version (the card then shows its gene badges alone)
    changes: list[dict] = field(default_factory=list)


def _genome_key(patch: Patch) -> str:
    """Stable serialization of the genome, excluding `name` and `voice_fx`
    (neither is part of the genome, and the name must not feed its own
    hash)."""
    d = patch.to_json()
    d.pop("name", None)
    return json.dumps(d, sort_keys=True)


def _named_child(child: Patch, m: Probe) -> Patch:
    # the namer's "textured" bank reads flatness: give it the per-frame one
    # (whole-note flatness is ~0 for anything pitched, so it never fired)
    features = replace(m.features, flatness=m.texture.flatness)
    return replace(child, name=sound_name(features, _genome_key(child)))


# how many candidates are measured per generation before the 8 most
# mutually different are kept (maximin over the standardised vectors); a
# steered litter measures more so the direction has something to choose
_POOL = 20
_STEER_POOL = 40
_STEER_KEEP = 0.3  # steering keeps the best-scoring 30 % (at least 8)
_PICK = 8
WILDNESS = {"tame": 0.35, "normal": 0.75, "wild": 1.3, "feral": 2.0}

# a parent is measured once per (genome, tempo), not once per generation
_PARENT_CACHE: OrderedDict[tuple[str, float], Probe | None] = OrderedDict()
_PARENT_CACHE_MAX = 32
# A generation runs outside the studio lock (`routes/breed.py`,
# `OutsideLock`), so two can overlap: every read and write of the parent
# caches takes this lock. Unlocked, one request's eviction between
# another's `in` and its `move_to_end` was a KeyError. (Not `lru.LRU`: a
# cached value may be None — a parent too quiet to measure — which its
# `get` reports as a miss.)
_CACHE_LOCK = threading.Lock()


def _measured(cache: OrderedDict, key: tuple, measure: Callable) -> Probe | None:
    """`cache[key]`, measured (outside the lock: it renders) and kept on a
    miss, the oldest entries evicted. Two requests missing the same key at
    once both measure it — the same answer, stored twice."""
    with _CACHE_LOCK:
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
    m = measure()
    with _CACHE_LOCK:
        cache[key] = m
        cache.move_to_end(key)
        while len(cache) > _PARENT_CACHE_MAX:
            cache.popitem(last=False)
    return m


def _donors() -> list[Patch]:
    return [Patch.from_dict(e["patch"]) for e in _library()]


def _library() -> list[dict]:
    from .library import LIBRARY  # lazy: the library JSON is optional

    return LIBRARY


def _parent_probe(p: Patch, bpm: float) -> Probe | None:
    key = (_genome_key(p), float(bpm))
    return _measured(_PARENT_CACHE, key, lambda: probe(p, bpm))


@dataclass(eq=False)  # compared by identity: it carries arrays
class _Candidate:
    patch: Patch
    badges: list[str]
    m: Probe
    score: float = 0.0


def _pick_diverse(
    pool: list[_Candidate], anchors: list[np.ndarray], n: int
) -> list[_Candidate]:
    """Greedy maximin: repeatedly take the candidate farthest from every
    parent and every sibling already chosen. Soundalikes lose; the board
    ends up as eight genuinely different directions."""
    chosen: list[_Candidate] = []
    rest = list(pool)
    while rest and len(chosen) < n:
        refs = anchors + [c.m.vector for c in chosen]
        gaps = [min((distance(c.m.vector, v) for v in refs), default=1e9) for c in rest]
        chosen.append(rest.pop(int(np.argmax(gaps))))
    return chosen


def _measure_all(kids: list[tuple[Patch, list[str]]], bpm: float) -> list[_Candidate]:
    probes = probe_many([kid for kid, _ in kids], bpm)
    return [
        _Candidate(kid, badges, m)
        for (kid, badges), m in zip(kids, probes, strict=True)
        if m is not None
    ]


def pick(
    rendered: list[_Candidate],
    parent_vecs: list[np.ndarray],
    direction: str | None,
    n: int = _PICK,
    eps: float = _NOVELTY_EPS,
) -> list[_Candidate]:
    """Choose the board from measured candidates, in order of preference:
    audible ones (never a card you can't hear while `n` audible exist),
    steered to the best-scoring `_STEER_KEEP`, novel against every parent; then
    fall back through soundalikes to quiet ones so the board stays full.

    A steered board keeps `genome.EXPLORERS` of its slots for the whole
    audible pool, as the layered pick always has: the most different
    candidates the litter made, whatever they score. Without them every
    card came from the best-scoring 30 %, which are each other's
    neighbours, and starring the best three bred the board into one sound
    (`tools/breed_spread.py`, normal wildness, mean sibling distance by
    generation: 3.90 3.54 3.27 2.92 2.68 2.60; with the explorers 4.90
    5.10 5.15 4.97 4.75 4.89, the three best cards scoring the same).
    An unsteered pick is unchanged: its explorers would come from the pool
    the maximin already chose from."""
    audible = [c for c in rendered if c.m.audible]
    quiet = [c for c in rendered if not c.m.audible]
    everyone = audible
    if direction is not None:
        score = DIRECTIONS[direction]
        for c in audible:
            c.score = float(score(c.m))
        # stable sort on score: ties keep litter order (deterministic)
        ranked = sorted(audible, key=lambda c: -c.score)
        keep = max(n, math.ceil(len(ranked) * _STEER_KEEP))
        audible, spare = ranked[:keep], ranked[keep:]
        explorers = [everyone]
        main_n = max(0, n - genome.EXPLORERS)
    else:
        spare, explorers, main_n = [], [], n

    def novel(c: _Candidate) -> bool:
        return all(distance(c.m.vector, pv) >= eps for pv in parent_vecs)

    chosen = _pick_diverse([c for c in audible if novel(c)], parent_vecs, main_n)
    for tier in (
        *([c for c in pool if novel(c)] for pool in explorers),
        [c for c in audible if not novel(c)],  # soundalikes beat an empty slot
        spare,  # off-direction but audible
        quiet,
    ):
        if len(chosen) >= n:
            break
        taken = {id(c) for c in chosen}
        rest = [c for c in tier if id(c) not in taken]
        chosen += _pick_diverse(
            rest, parent_vecs + [c.m.vector for c in chosen], n - len(chosen)
        )
    return chosen


def _vs_parent(m: Probe, parents: list[Probe]) -> str:
    """Brightness change against the nearest parent, in octaves: the one
    number you can check by ear against the card you starred."""
    if not parents:
        return ""
    near = min(parents, key=lambda pm: distance(m.vector, pm.vector))
    octs = math.log2(max(m.features.centroid, 20.0) / max(near.features.centroid, 20.0))
    if abs(octs) < 0.05:
        return "same brightness"
    return f"{octs:+.1f} oct"


def new_generation(
    state,
    direction: str | None,
    rng: np.random.Generator | None = None,
    amount: float = WILDNESS["normal"],
    store: SpaceStore | None = None,
    depth: str = "single",
) -> list[Tile]:
    """`store`: the studio's long-lived SpaceStore (`svc.space_store()`);
    a fresh one reading `state.data_dir` when not given. `state.bpm` (when
    present, else `state.arrangement.bpm`) is the session tempo synced LFOs
    are probed at. `depth` (`genome.DEPTH`): "single" breeds patches, as
    the breeder always has; "layered"/"deep" breed whole Sounds
    (`_layered_generation`)."""
    r = rng if rng is not None else np.random.default_rng()
    if store is None:
        store = SpaceStore(state.data_dir / "space.jsonl")
    arr = getattr(state, "arrangement", None)
    bpm = float(getattr(state, "bpm", None) or getattr(arr, "bpm", None) or PROBE_BPM)

    starred_ids = list(state.breed_starred)
    stacks: list[genome.Stack] = []
    for tid in starred_ids:
        row = store.get(tid)
        if row is not None:
            stacks.append(genome.stack_of_row(row))
    if not stacks:
        stacks = [genome.Stack(state.patch)]
    if direction is not None and direction not in DIRECTIONS:
        direction = None
    if genome.depth_of(depth) != "single":
        return _layered_generation(
            stacks, direction, r, amount, store, starred_ids, bpm, depth
        )
    parents = [st.patch for st in stacks]
    donors = _donors()

    pool = _STEER_POOL if direction is not None else _POOL
    rendered = _measure_all(
        generation(
            parents, n=pool, amount=amount, rng=r, donors=donors, bias=direction
        ),
        bpm,
    )
    # renders can fail and candidates can be silent: top up (a few times at
    # most) so the pick has eight audible ones to choose from
    for _ in range(3):
        short = _PICK - sum(c.m.audible for c in rendered)
        if short <= 0:
            break
        more = generation(
            parents, n=short, amount=amount, rng=r, donors=donors, bias=direction
        )
        rendered += _measure_all(more, bpm)

    parent_probes = [m for m in (_parent_probe(p, bpm) for p in parents) if m]
    parent_vecs = [m.vector for m in parent_probes]
    picked = pick(rendered, parent_vecs, direction, eps=_novelty_eps(amount))

    tiles: list[Tile] = []
    for c in picked:
        candidate = _named_child(c.patch, c.m)
        row_id = store.add(candidate, c.m.features, parents=starred_ids)
        near = _nearest_parent(candidate, parents)
        diff = changes(near, candidate) if near is not None else []
        tiles.append(
            _tile(row_id, candidate.name, c.m, c.badges, parent_probes, diff=diff)
        )
    return tiles


# a card's "what changed" list: this many rows at most, and a numeric gene
# that moved less than this share of its range is no change you would hear
_MAX_CHANGES = 8
_MIN_MOVE = 0.03


def _value_text(name: str, v) -> str:
    """A gene's value as a card prints it: a choice by its menu name, a
    number to three figures with its unit ("1.2 kHz", "180 ms", "0.42")."""
    spec = spec_of(name)
    if PARAMS[name].kind == "cat":
        if v in (None, ""):
            return "off"
        names = dict(spec.options) if spec else {}
        return str(names.get(v, v))[:16]
    x = float(v)
    unit = spec.unit if spec else ""
    if unit == "Hz" and abs(x) >= 1000:
        return f"{x / 1000:.3g} kHz"
    if unit == "s" and abs(x) < 1:
        return f"{x * 1000:.3g} ms"
    if PARAMS[name].kind == "int":
        return f"{int(round(x))}{' ' + unit if unit else ''}"
    return f"{x:.3g}{' ' + unit if unit else ''}"


def changes(parent: Patch, child: Patch, limit: int = _MAX_CHANGES) -> list[dict]:
    """The genes a child moved from its parent, biggest move first: what
    the card says changed. Only genes heard in either sound (a dead osc's
    detune is a change in the genome, not in the sound). Each row is
    {"label", "from", "to", "a", "b"}: `a` / `b` the two values' places
    across the gene's range, 0..1 (the card's bar pair), -1 for a choice,
    which counts as the biggest move there is."""
    heard = live_genes(parent) | live_genes(child)
    rows: list[tuple[float, dict]] = []
    for name in heard:
        was, now = getattr(parent, name), getattr(child, name)
        if isinstance(was, np.ndarray) or isinstance(now, np.ndarray) or was == now:
            continue
        spec = spec_of(name)
        label = (spec.label if spec else name.replace("_", " "))[:28]
        if PARAMS[name].numeric:
            try:
                a, b = (min(1.0, max(0.0, norm(name, float(x)))) for x in (was, now))
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            if abs(b - a) < _MIN_MOVE:
                continue
            move = abs(b - a)
        else:
            a = b = -1.0
            move = 1.0
        row = {
            "label": label,
            "from": _value_text(name, was),
            "to": _value_text(name, now),
            "a": round(a, 3),
            "b": round(b, 3),
        }
        rows.append((move, row))
    rows.sort(key=lambda r: (-r[0], r[1]["label"]))
    return [r for _, r in rows[:limit]]


def _nearest_parent(child: Patch, parents: list[Patch]) -> Patch | None:
    """The parent a child is told against: the one it differs from in the
    fewest genes (a crossover's nearer side)."""
    if not parents:
        return None
    return min(parents, key=lambda p: len(changes(p, child, limit=10_000)))


def _tile(row_id, name, m: Probe, badges, parent_probes, stack=None, diff=()) -> Tile:
    f = m.features
    return Tile(
        id=row_id,
        name=name,
        sub_pct=round(100 * (f.sub_20_60 + f.sub_60_120), 1),
        centroid=round(f.centroid),
        spectrum=band_magnitudes(m.audio),
        badges_genes=badges,
        vs_parent=_vs_parent(m, parent_probes),
        chips=genome.chips(stack) if stack is not None and not stack.plain else [],
        layered=stack is not None and not stack.plain,
        changes=list(diff),
    )


# a parent Sound is measured once per (stack, tempo), like a parent patch
_STACK_CACHE: OrderedDict[tuple[str, float], Probe | None] = OrderedDict()


def _parent_stack_probe(st: genome.Stack, bpm: float) -> Probe | None:
    if st.plain:
        return _parent_probe(st.patch, bpm)
    key = (genome.key_of(st), float(bpm))
    return _measured(_STACK_CACHE, key, lambda: genome.measure_stack(st.copy(), bpm))


_MAX_BADGES = 4
# a steered layered litter measures 30, not 40: a deep stack costs ~2.5
# renders, and 40 of them ran past the 1 s budget on the ARM dev machine
# (median ~0.9 s, worst 1.4 s); the direction's pull comes as much from the
# biased moves (genome.STEERS) as from the selection
_STACK_STEER_POOL = 30


def _layered_generation(
    stacks: list[genome.Stack],
    direction: str | None,
    r: np.random.Generator,
    amount: float,
    store: SpaceStore,
    starred_ids: list[str],
    bpm: float,
    depth: str,
) -> list[Tile]:
    """Breed whole Sounds (`genome`): main patches as the single-depth
    breeder makes them, structure grown on each child's origin parent, one
    probe of each stack, then the layered pick (combined distance, two
    explorer slots). Each card's row carries its layers/FX/curves (`sound`)
    so the card, "Use with its layers" and a later breed read one thing."""
    library = _library()
    profiles = [_parent_probe(st.patch, bpm) for st in stacks]
    pool = _STACK_STEER_POOL if direction is not None else _POOL

    def breed_some(n: int) -> list[genome.Candidate]:
        kids = genome.litter(stacks, n, amount, depth, r, library, direction, profiles)
        return genome.measure_all(kids, bpm)

    rendered = breed_some(pool)
    for _ in range(3):
        short = _PICK - sum(genome.card_ok(c) for c in rendered)
        if short <= 0:
            break
        rendered += breed_some(short)

    anchors = [
        (m.vector, st)
        for st in stacks
        if (m := _parent_stack_probe(st, bpm)) is not None
    ]
    parent_probes = [
        m for st in stacks if (m := _parent_stack_probe(st, bpm)) is not None
    ]
    score = DIRECTIONS[direction] if direction is not None else None
    picked = genome.pick(
        rendered, anchors, score, _PICK, _novelty_eps(amount), _STEER_KEEP
    )
    tiles: list[Tile] = []
    for c in picked:
        main = _named_child(c.stack.patch, c.m)
        c.stack.patch = main
        badges = genome.structure_badges(c.stack, c.origin) + c.badges
        row_id = store.add(
            main, c.m.features, parents=starred_ids, sound=genome.row_sound(c.stack)
        )
        diff = changes(c.origin.patch, main)
        tiles.append(
            _tile(
                row_id,
                main.name,
                c.m,
                badges[:_MAX_BADGES],
                parent_probes,
                c.stack,
                diff,
            )
        )
    return tiles
