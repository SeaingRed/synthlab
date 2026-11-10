"""One declarative schema for everything the studio persists.

state.json, an imported project, a stored baseline (revert), a scene and a
format-2 preset all reach the live session through `conform(value, node)`,
so there is exactly one definition of what each persisted value may be.
Three rounds of per-site cleaners kept missing members of the same class
(a container of the wrong type, a non-finite number, an unhashable enum
value, an uncapped list); a schema closes it by construction, because every
node enforces the same rules in the same order:

1. the container type first — anything else is `Invalid`;
2. element schemas, recursively (only as deep as the *schema*, never as
   deep as the data, so hostile nesting cannot recurse);
3. numbers: finite, never a bool, clamped (or rejected) to a range;
4. enums: hashable-safe membership (non-scalars are rejected before lookup);
5. lists: deduped where the domain says so, and capped (`limits.py`).

`Invalid` never escapes a container: a list drops the bad element, a record
falls back to the field's default (or drops the key, for partial records),
and the top-level `conform` returns the node's default. `conform_strict`
lets it escape, which is how a project missing its patch is rejected.

Domain rules that already had one definition stay where they are and are
called as leaf validators: `params.clamp` (each field's `cap`,
its gene range widened where hand-made sounds go further), `drumlab`'s
per-voice specs,
`fxrack.clean_chain`, `curves.CURVABLE`, and the no-drop hidden-notes rule
(a step is kept whenever 0 <= step < MAX_GRID_STEPS, whatever the current
grid size).
"""

from __future__ import annotations

import copy
import logging
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import replace
from functools import cache
from types import SimpleNamespace

from .. import stems as _stems
from ..params import clamp
from ..sequencer import midi
from ..synth import Patch
from . import limits
from . import meter as _meter
from .clips import clip_safe
from .curves import CURVABLE, GAIN, LANE_TARGETS, parse_fx
from .drumlab import DRUM_PARAMS
from .files import safe_name
from .paramui import CONTINUOUS_PARAMS, MACRO_PARAMS

# Arrangement fields that are ABSENT when unset wherever an arrangement is
# written down (the session file, a loop key, a baseline): the per-hit drum
# table and the rows' own swing came after every saved state and pinned
# key, and none of those may gain a key (`arr_dict`) — nor the strum, nor
# the loop's chord track, which no render key carries at all.
ARR_OPTIONAL = (
    "drum_hits",
    "drum_row_swing",
    "strum",
    "meter",
    "ramp",
    "chords",
    # the note grid's timing inside a step (free record)
    "note_nudge",
    # the note grid's chance lane: absent while no step has one
    "note_chance",
)
# an optional field whose unset value is not falsy: the meter, unset at 4/4
# (`meter.py`; a loop or a section in another meter, and a section's ramp,
# came after every saved state and pinned key)
ARR_NEUTRAL = {"meter": "4/4"}


def arr_unset(key: str, value) -> bool:
    """Is this optional Arrangement field unset (`ARR_OPTIONAL`)?"""
    if key in ARR_NEUTRAL:
        return value is None or value == ARR_NEUTRAL[key]
    return not value


def clean_nudge(nudge, cells) -> dict:
    """A note lane's timing inside a step (`Arrangement.note_nudge`) as it
    is kept: only on a step a note of `cells` sits on, never a 0 — so a
    note drawn off or moved leaves none behind (the cuts' rule)."""
    on = {str(int(c[1])) for c in cells or ()}
    return {str(k): float(v) for k, v in (nudge or {}).items() if str(k) in on and v}


# what a hit's data says by not being there: certain, once, on the grid, at
# the voice's own pitch (a velocity falls back to the step's `drum_vel`)
HIT_NEUTRAL = {"p": 1.0, "r": 1, "n": 0.0, "t": 0.0}


def arr_dict(arr) -> dict:
    """`asdict(arr)` as it is written down: without the optional fields
    that are unset (`ARR_OPTIONAL`)."""
    from dataclasses import asdict

    out = asdict(arr)
    for k in ARR_OPTIONAL:
        if arr_unset(k, out.get(k)):
            out.pop(k, None)
    return out


def _ramp(x) -> list:
    """An arrangement's tempo ramp (`meter.Clock`): [to_bpm, over_beats,
    at_beats], or [] for none."""
    if x in (None, []):
        return []
    if not isinstance(x, list) or len(x) != 3:
        raise Invalid("not a ramp")
    try:
        to, over, at = (float(v) for v in x)
    except (TypeError, ValueError):
        raise Invalid("not a ramp") from None
    if not all(math.isfinite(v) for v in (to, over, at)) or over <= 0:
        raise Invalid("not a ramp")
    lo, hi = limits.BPM_RANGE
    cap = limits.MAX_SECTION_BARS * 6.0  # a 64-bar 12/8 section, in beats
    return [min(hi, max(lo, to)), min(cap, over), min(max(0.0, at), cap)]


def _meter_name(x) -> str:
    if x not in _meter.METERS:
        raise Invalid("not a meter")
    return x


def _fit_steps(out: dict, meter_name: str, fields=("steps_per_bar", "drum_steps")):
    """A grid's step counts held to its meter's (`meter.step_choices`): 4/4
    keeps exactly the four it always had, and a count another meter's grid
    has is that field's default here, as any stray value always was."""
    ok = _meter.step_choices(meter_name)
    for k, d in zip(fields, (4, 16), strict=False):
        if k in out and out[k] not in ok:
            out[k] = _meter.like(meter_name, d)


log = logging.getLogger(__name__)


class Invalid(ValueError):
    """This node can make nothing of the value. Containers catch it."""


def _hashable_scalar(x) -> bool:
    return isinstance(x, str | int | float) and not isinstance(x, bool)


class Node:
    """A schema node. `conform` returns a fresh, clean value or raises
    Invalid; it never returns (or mutates) a container it was handed, so
    the result can be assigned into live state without a deepcopy."""

    def __init__(self, default=None):
        self._default = default

    def default(self):
        d = self._default
        return d() if callable(d) else copy.deepcopy(d)

    def conform(self, x):  # pragma: no cover - abstract
        raise NotImplementedError

    def children(self) -> Iterable[tuple[object, Node]]:
        """(path step, child) pairs — `"*"` for every element of a list or
        map, an int for a fixed position, a key for a record field. Lets
        tests walk every node the schema has."""
        return ()


def defaulted(node: Node, default) -> Node:
    """`node` with a different default (same validation)."""
    n = copy.copy(node)
    n._default = default
    return n


class Num(Node):
    """A finite real number (never a bool) in [lo, hi]. `integer`
    truncates to int *after* the finiteness check — int(inf) raises
    OverflowError, which is what kept escaping the older cleaners.
    `integral` rejects a fractional value instead of truncating it.
    Out of range: clamped, or Invalid when `clamp` is False. `example`:
    a value that is written down, for a field whose default is not (one
    that is absent while unset — the schema tests' fixed point)."""

    def __init__(
        self,
        lo: float = -math.inf,
        hi: float = math.inf,
        default=None,
        *,
        integer: bool = False,
        integral: bool = False,
        clamp: bool = True,
        example=None,
    ):
        super().__init__(default)
        self.example = example
        self.lo, self.hi = lo, hi
        self.integer, self.integral, self.clamp = integer, integral, clamp

    def conform(self, x):
        if isinstance(x, bool) or not isinstance(x, int | float):
            raise Invalid("not a number")
        try:
            f = float(x)  # a 400-digit JSON int overflows here
        except OverflowError as err:
            raise Invalid("number too large") from err
        if not math.isfinite(f):
            raise Invalid("not finite")
        if self.integral and f != int(f):
            raise Invalid("not an integer")
        if self.integer:
            f = float(int(f))
        if not self.lo <= f <= self.hi:
            if not self.clamp:
                raise Invalid("out of range")
            f = min(self.hi, max(self.lo, f))
        return int(f) if self.integer else f


class Choice(Node):
    """One of a fixed set of scalars; returns the canonical option (so
    `2.0` for an int option comes back as `2`). Non-scalars — lists, dicts,
    bools — are rejected before the lookup, so an unhashable value can
    never raise TypeError here."""

    def __init__(self, options: Iterable, default=None):
        self.options = tuple(options)
        super().__init__(self.options[0] if default is None else default)
        self._canon = {o: o for o in self.options}

    def conform(self, x):
        if not _hashable_scalar(x):
            raise Invalid("not a scalar")
        try:
            return self._canon[x]
        except KeyError as err:
            raise Invalid("not an option") from err


class Str(Node):
    """A str (nothing else is coerced), truncated to `max_len`, then passed
    through `fn` (e.g. a filesystem sanitizer)."""

    def __init__(
        self,
        default: str = "",
        *,
        max_len: int = limits.MAX_NAME_CHARS,
        nonempty: bool = False,
        fn: Callable[[str], str] | None = None,
    ):
        super().__init__(default)
        self.max_len, self.nonempty, self.fn = max_len, nonempty, fn

    def conform(self, x):
        if not isinstance(x, str):
            raise Invalid("not a string")
        x = x[: self.max_len]
        if self.fn is not None:
            x = self.fn(x)
        if self.nonempty and not x:
            raise Invalid("empty")
        return x


class Bool(Node):
    """Truthiness, like the `bool(...)` every loader used — never Invalid
    (a stored "yes" for mute has always meant muted)."""

    def __init__(self, default: bool = False):
        super().__init__(default)

    def conform(self, x):
        return bool(x)


class Opt(Node):
    """None, or a valid `node` value; anything invalid becomes None."""

    def __init__(self, node: Node):
        super().__init__(None)
        self.node = node

    def conform(self, x):
        if x is None:
            return None
        try:
            return self.node.conform(x)
        except Invalid:
            return None

    def children(self):
        return (("?", self.node),)


class Leaf(Node):
    """A domain validator that already exists elsewhere: `fn(x)` returns the
    clean value or raises Invalid."""

    def __init__(self, fn: Callable, default=None, *, example=None):
        super().__init__(default)
        self.fn = fn
        self.example = example

    def conform(self, x):
        return self.fn(x)


class Tuple(Node):
    """A JSON list of exactly len(nodes), each position its own node.
    Any bad position invalidates the whole tuple."""

    def __init__(self, *nodes: Node, default=None):
        super().__init__(default)
        self.nodes = nodes

    def conform(self, x):
        if not isinstance(x, list) or len(x) != len(self.nodes):
            raise Invalid("wrong shape")
        return [n.conform(v) for n, v in zip(self.nodes, x, strict=True)]

    def children(self):
        return tuple(enumerate(self.nodes))


def _raw_key(item):
    """A cheap hashable identity for an as-yet-unvalidated element (a
    scalar, or a flat list of scalars) — lets ListOf skip exact duplicates
    before paying for validation. 600k copies of one cell is the probe."""
    if _hashable_scalar(item):
        return (type(item), item)
    if isinstance(item, list) and len(item) <= 4 and all(map(_hashable_scalar, item)):
        return tuple((type(v), v) for v in item)
    return None


class ListOf(Node):
    """A list of `elem`, bad elements dropped. `unique` (a key function on
    the *clean* element) dedupes, first occurrence wins; `max_len` caps the
    result, keeping the first or the last elements."""

    def __init__(
        self,
        elem: Node,
        *,
        max_len: int,
        unique: Callable | None = None,
        keep: str = "first",
        min_len: int = 0,
        default=list,
    ):
        super().__init__(default)
        self.elem, self.max_len, self.unique = elem, max_len, unique
        self.keep, self.min_len = keep, min_len

    def conform(self, x):
        if not isinstance(x, list):
            raise Invalid("not a list")
        items = reversed(x) if self.keep == "last" else x
        out: list = []
        seen_raw: set = set()
        seen: set = set()
        for item in items:
            if len(out) >= self.max_len:
                break
            if self.unique is not None:
                rk = _raw_key(item)
                if rk is not None:
                    if rk in seen_raw:
                        continue
                    seen_raw.add(rk)
            try:
                v = self.elem.conform(item)
            except Invalid:
                continue
            if self.unique is not None:
                k = self.unique(v)
                if k in seen:
                    continue
                seen.add(k)
            out.append(v)
        if self.keep == "last":
            out.reverse()
        if len(out) < self.min_len:
            raise Invalid("too short")
        return out

    def children(self):
        return (("*", self.elem),)


class FixedList(Node):
    """Exactly `n` elements (the UI indexes them directly). A bad or
    missing element becomes `fill(i)`; `strict` rejects a list of any
    other length outright."""

    def __init__(
        self,
        elem: Node,
        n: int,
        *,
        fill: Callable[[int], object],
        default,
        strict: bool = True,
        post: Callable[[list], list] | None = None,
    ):
        super().__init__(default)
        self.elem, self.n, self.fill = elem, n, fill
        self.strict, self.post = strict, post

    def conform(self, x):
        if not isinstance(x, list) or (self.strict and len(x) != self.n):
            raise Invalid("wrong shape")
        out = []
        for i in range(self.n):
            if i < len(x):
                try:
                    out.append(self.elem.conform(x[i]))
                    continue
                except Invalid:
                    pass
            out.append(self.fill(i))
        return self.post(out) if self.post else out

    def children(self):
        return (("*", self.elem),)


def _is_empty(v) -> bool:
    return isinstance(v, dict | list) and not v


class MapOf(Node):
    """A dict whose keys conform to `key` and values to `value`; a bad
    entry is dropped, as is an empty one when `drop_empty`."""

    def __init__(
        self, key: Node, value: Node, *, max_len: int, drop_empty: bool = False
    ):
        super().__init__(dict)
        self.key, self.value = key, value
        self.max_len, self.drop_empty = max_len, drop_empty

    def conform(self, x):
        if not isinstance(x, dict):
            raise Invalid("not a dict")
        out: dict = {}
        for k, v in x.items():
            if len(out) >= self.max_len:
                break
            try:
                ck, cv = self.key.conform(k), self.value.conform(v)
            except Invalid:
                continue
            if self.drop_empty and _is_empty(cv):
                continue
            out[ck] = cv
        return out

    def children(self):
        return (("*", self.value),)


class Chances(MapOf):
    """A step -> chance table (the note grid's chance lane): a `MapOf`
    whose neutral value, 1 (the step always plays), is never stored."""

    def conform(self, x):
        return {k: v for k, v in super().conform(x).items() if v < 1.0}


class Record(Node):
    """A dict with known fields, unknown keys dropped (logged when
    `label` names the record). A missing field takes its default, or —
    `partial` — stays missing; an invalid one likewise. A field in
    `required` must be present; a field in `key` must be valid, or the
    whole record is Invalid (a breed tile without an id, a song entry
    without a scene). `post(out, raw)` sees the clean dict and the raw one
    for cross-field rules."""

    def __init__(
        self,
        fields: dict[str, Node],
        *,
        required: Iterable[str] = (),
        key: Iterable[str] = (),
        partial: bool = False,
        drop_empty: bool = False,
        post: Callable[[dict, dict], dict] | None = None,
        label: str | None = None,
        default=None,
    ):
        super().__init__(default if default is not None else self._defaults)
        self.fields = fields
        self.required, self.key = frozenset(required), frozenset(key)
        self.partial, self.drop_empty = partial, drop_empty
        self.post, self.label = post, label

    def _defaults(self) -> dict:
        if self.partial:
            return {}
        return {name: node.default() for name, node in self.fields.items()}

    def conform(self, x):
        if not isinstance(x, dict):
            raise Invalid("not a dict")
        if self.label is not None:
            unknown = [k for k in x if k not in self.fields]
            if unknown:
                log.warning("%s: ignoring unknown fields %s", self.label, unknown[:20])
        out: dict = {}
        for name, node in self.fields.items():
            if name not in x:
                if name in self.required or name in self.key:
                    raise Invalid(f"missing {name!r}")
                if not self.partial:
                    out[name] = node.default()
                continue
            try:
                v = node.conform(x[name])
            except Invalid:
                if name in self.key:
                    raise
                if self.partial:
                    continue
                v = node.default()
            if self.drop_empty and _is_empty(v):
                continue
            out[name] = v
        return self.post(out, x) if self.post else out

    def children(self):
        return tuple(self.fields.items())


class Tagged(Node):
    """A dict that is one of several records, picked by the scalar in its
    `tag` field (each variant carries that field as a one-option Choice, so
    the tag survives conforming). An unknown or non-scalar tag is Invalid.
    `children` lists every variant's fields once, so a schema walk reaches
    each of them by name."""

    def __init__(self, tag: str, variants: dict[str, Record]):
        super().__init__(lambda: next(iter(variants.values())).default())
        self.tag, self.variants = tag, variants

    def conform(self, x):
        if not isinstance(x, dict):
            raise Invalid("not a dict")
        t = x.get(self.tag)
        if not _hashable_scalar(t) or t not in self.variants:
            raise Invalid(f"unknown {self.tag}")
        return self.variants[t].conform(x)

    def children(self):
        seen: dict[str, Node] = {}
        for rec in self.variants.values():
            for name, node in rec.fields.items():
                seen.setdefault(name, node)
        return tuple(seen.items())


def conform(value, node: Node):
    """`value` made valid for `node` — the node's default when nothing
    usable is left of it. Never raises for any JSON value."""
    try:
        return node.conform(value)
    except Invalid:
        return node.default()


def conform_strict(value, node: Node):
    """As `conform`, but a value the node can make nothing of (e.g. a
    project missing its patch) raises Invalid (a ValueError)."""
    return node.conform(value)


# --- leaf validators ----------------------------------------------------------


def _patch(x) -> dict:
    """A Patch dict: tolerant field load (unknown keys dropped), then
    params.clamp, then back to its one serialization."""
    if not isinstance(x, dict):
        raise Invalid("not a patch")
    p = clamp(Patch.from_dict(x))
    if not isinstance(p.name, str):
        p = replace(p, name=Patch().name)
    elif len(p.name) > limits.MAX_NAME_CHARS:
        p = replace(p, name=p.name[: limits.MAX_NAME_CHARS])
    return p.to_json()


def valid_pitch(name) -> bool:
    """A real note name inside MIDI 0..127. `midi("C99999")` parses fine,
    and note_to_hz() then overflows a float three calls later — so the
    range check is part of validity, not a nicety."""
    if not isinstance(name, str) or not 0 < len(name) <= 8:
        return False
    try:
        m = midi(name)
    except Exception:  # noqa: BLE001 - any parse failure means "not a pitch"
        return False
    return 0 <= m <= 127


def _pitch(x) -> str:
    if not valid_pitch(x):
        raise Invalid("not a pitch")
    return x


def _step_key(x) -> str:
    """A velocity-lane key: the step number as a str, normalised."""
    if not isinstance(x, str) or len(x) > 6:
        raise Invalid("not a step key")
    try:
        n = int(x)
    except ValueError as err:
        raise Invalid("not a step key") from err
    if not 0 <= n < limits.MAX_GRID_STEPS:
        raise Invalid("step out of range")
    return str(n)


def _clip_name(x) -> str:
    if not _hashable_scalar(x):
        raise Invalid("not a clip name")
    return clip_safe(str(x)[: limits.MAX_NAME_CHARS])


# Perform's launcher (`perform_slots`): a lane a layer slot, then the drums
SLOT_LANES = limits.LAYER_SLOTS + 1
# the session's keys ABSENT WHEN UNSET (`_payload_post`), as `ARR_OPTIONAL`
# are an arrangement's: none of them is in a file that never used it
PAYLOAD_OPTIONAL = ("perform_slots",)


def _slot_row_empty(row: dict) -> bool:
    return not row.get("name") and not any(row.get("slots") or ())


def _payload_post(out: dict, raw: dict) -> dict:
    """The session's last word: Perform's launcher is absent while no row
    holds anything (its rows past the last one that does are dropped)."""
    rows = list(out.get("perform_slots") or ())
    while rows and _slot_row_empty(rows[-1]):
        rows.pop()
    if rows:
        out["perform_slots"] = rows
    else:
        out.pop("perform_slots", None)
    return out


def _loop_name(x) -> str:
    """A loop's name as the loop store keeps it (`loops.loop_safe`: the
    generated names' "·" and "#" allowed)."""
    if not _hashable_scalar(x) or not str(x).strip(" ."):
        raise Invalid("not a loop name")
    return safe_name(str(x)[: limits.MAX_NAME_CHARS], "loop", extra="·#")


def _fx(x) -> list[dict]:
    from .fxrack import clean_chain  # lazy: fxrack imports state

    return clean_chain(x)


_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def _song_id(x) -> str:
    """A song-model id (track, pattern, section): short and markup/URL-safe,
    since ids end up in element ids, data attributes and route paths."""
    if not isinstance(x, str) or not 0 < len(x) <= 32 or not set(x) <= _ID_CHARS:
        raise Invalid("not an id")
    return x


_HEX = frozenset("0123456789abcdefABCDEF")
_HEX_LOWER = frozenset("0123456789abcdef")
_MODEL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
# every stem name a separation model makes (sources.ALL_STEMS, read off
# the engine here: sources.py imports far more than a schema should)
_ALL_STEMS = tuple(dict.fromkeys(s for m in _stems.MODELS.values() for s in m.sources))


def _color(x) -> str:
    """`#rrggbb` only — a color lands in a style attribute."""
    if not isinstance(x, str) or len(x) != 7 or x[0] != "#" or not set(x[1:]) <= _HEX:
        raise Invalid("not a color")
    return x.lower()


def _drum_spec_node(spec: dict) -> Node:
    lo, hi = spec["min"], spec["max"]
    if spec["kind"] == "bool":
        num = Num(lo, hi)

        def as_bool(x):
            return x if isinstance(x, bool) else bool(num.conform(x))

        return Leaf(as_bool, spec["default"], example=True)
    return Num(lo, hi, spec["default"])


def _pattern_post(out: dict, raw: dict) -> dict:
    """A drum pattern's steps must fit its own grid (bars x steps_per_bar),
    sorted — patterns are small, self-contained and always re-sorted."""
    total = out["bars"] * out["steps_per_bar"]
    out["steps"] = [
        list(t) for t in sorted({(v, s) for v, s in out["steps"] if s < total})
    ]
    # a slot's per-hit data and row swings (absent when it has none: every
    # saved bank reads and writes as it did); a hit's data needs its cell
    on = {f"{v}.{s}" for v, s in out["steps"]}
    out["hits"] = {k: v for k, v in (out.get("hits") or {}).items() if k in on}
    for k in ("hits", "row_swing"):
        if not out.get(k):
            out.pop(k, None)
    return out


# --- the schema ---------------------------------------------------------------


@cache
def nodes() -> SimpleNamespace:
    """Build the schema once. Lazy because it reads constants from
    the state package, fxrack, audition and breedboard, all of which import state."""
    from dataclasses import fields

    from .arrange import FOLLOW_OCTAVES, LAYER_ROLES
    from .audition import CHOICES as PHRASE_CHOICES
    from .breedboard import WILDNESS
    from .drumfactory import DRUM_FACTORY
    from .genome import DEPTHS
    from .state import (
        BANK_SLOTS,
        DRUM_ROWS,
        LAYER_SEQ_KEYS,
        RESET_SCOPES,
        ROOTS,
        SCALE_CHOICES,
        Arrangement,
        _empty_layer,
    )

    unit = Num(0.0, 1.0)
    step = Num(0, limits.MAX_GRID_STEPS - 1, integer=True, clamp=False)
    grid_choice = Choice(limits.BAR_CHOICES)
    spb_choice = Choice(limits.STEP_CHOICES)
    # a grid in any meter (`meter.step_choices`); the record's post holds
    # it to its own meter's
    spb_any = Choice(_meter.ALL_STEP_CHOICES, limits.STEP_CHOICES[0])
    # a meter by name (`meter.METERS`); 2/4 the example: its grid takes the
    # 4/4 grid's default step counts, so an example stays a fixed point
    meter_name = Leaf(_meter_name, _meter.DEFAULT, example="2/4")

    patch = Leaf(_patch, lambda: Patch().to_json(), example=Patch().to_json())
    fx = Leaf(_fx, list, example=_fx([{"kind": "reverb"}]))
    cells = ListOf(
        Tuple(Leaf(_pitch, example="A1"), step),
        max_len=limits.MAX_GRID_CELLS,
        unique=tuple,
    )
    step_values = MapOf(
        Leaf(_step_key, example="0"), unit, max_len=limits.MAX_GRID_STEPS
    )
    note_chance = Chances(
        Leaf(_step_key, example="0"), unit, max_len=limits.MAX_GRID_STEPS
    )
    curves = MapOf(
        Choice(CURVABLE),
        ListOf(Tuple(unit, unit), max_len=limits.MAX_CURVE_POINTS),
        max_len=len(CURVABLE),
        drop_empty=True,
    )
    drum_voice = Choice(DRUM_ROWS)

    def _hit_key(x) -> str:
        """`<voice>.<step>`: a drum cell's name, as the grid posts it."""
        if not isinstance(x, str) or x.count(".") != 1:
            raise Invalid("not a hit")
        voice, step = x.split(".")
        key = limits.step_key(step)
        if voice not in DRUM_ROWS or key is None:
            raise Invalid("not a hit")
        return f"{voice}.{key}"

    def _hit_post(out: dict, raw: dict) -> dict:
        """A value that says nothing is not written down: one hit, certain,
        on the grid, at the voice's own pitch (`HIT_NEUTRAL`). A velocity
        always says something: the step's own may differ."""
        return {k: v for k, v in out.items() if HIT_NEUTRAL.get(k) != v}

    # per-hit drum data beside a drum grid's cells (drums.md, "The per-hit
    # model"): velocity, chance, ratchet, nudge (of a step), tune (st)
    drum_hits = MapOf(
        Leaf(_hit_key, example="kick.0"),
        Record(
            {
                "v": Num(0.05, 1.0),
                "p": Num(0.0, 1.0),
                # (2: a partial record never fills a default in, and the
                # schema's own example must be a value that is written down)
                "r": Num(1, 4, 2, integer=True),
                "n": Num(-0.5, 0.5),
                "t": Num(-12.0, 12.0),
            },
            partial=True,
            post=_hit_post,
        ),
        max_len=limits.MAX_DRUM_HITS,
        drop_empty=True,
    )
    row_swing = MapOf(drum_voice, Num(0.0, 0.6), max_len=len(DRUM_ROWS))
    # the note grid's timing inside a step (`Arrangement.note_nudge`): step
    # -> a share of a step, early (< 0) or late; 0 says nothing (dropped)
    note_nudge = MapOf(
        Leaf(_step_key, example="0"),
        Num(-0.5, 0.5, example=0.25),
        max_len=limits.MAX_GRID_STEPS,
    )

    # (the chord track's nodes come before the Arrangement's: the loop has one too)
    from .song_model import CHORD_QUALITIES

    def chords_post(out: dict, raw: dict) -> dict:
        """Slots on a quarter-beat grid, in time order, one a start, inside
        the progression's bars (a slot running past the end is cut)."""
        total = out["bars"] * 4.0
        slots, seen = [], set()
        for s in sorted(out["slots"], key=lambda s: s["at"]):
            at = round(s["at"] * 4) / 4
            if at >= total or at in seen:
                continue
            seen.add(at)
            length = min(max(0.25, round(s["len"] * 4) / 4), total - at)
            slots.append({**s, "at": at, "len": length})
        out["slots"] = slots
        return out

    # the chord track (gen/chordtrack.py): a progression in DEGREES of the
    # song's key, so a key change carries it. `at` / `len` are beats from
    # the progression's start; `bars` is the length that repeats
    chord_slot = Record(
        {
            "at": Num(0.0, 64.0, 0.0),
            "deg": Num(1, 7, 1, integer=True),
            "q": Choice(CHORD_QUALITIES, "triad"),
            "inv": Num(0, 2, 0, integer=True),
            "len": Num(0.25, 64.0, 4.0),
        },
        key=("at", "deg"),
    )
    chord_track = Record(
        {
            "bars": Choice(limits.CHORD_BAR_CHOICES, 4),
            "slots": ListOf(chord_slot, max_len=limits.MAX_CHORD_SLOTS),
        },
        post=chords_post,
    )

    arr_fields: dict[str, Node] = {
        "bpm": Num(*limits.BPM_RANGE),
        "bars": grid_choice,
        "steps_per_bar": spb_any,
        "root": Choice(ROOTS),
        "scale_name": Choice(SCALE_CHOICES),
        "cells": cells,
        "velocities": step_values,
        # timing inside a step (free record's shaped loops): a share of a
        # step, on a note's start step; absent when unset (`ARR_OPTIONAL`)
        "note_nudge": note_nudge,
        "note_chance": note_chance,
        "drums": ListOf(
            Tuple(drum_voice, step), max_len=limits.MAX_DRUM_CELLS, unique=tuple
        ),
        "swing": Num(0.0, 0.6),
        "drum_params": Record(
            {
                voice: Record(
                    {spec["name"]: _drum_spec_node(spec) for spec in specs},
                    partial=True,
                )
                for voice, specs in DRUM_PARAMS
            },
            partial=True,
            drop_empty=True,
        ),
        "slides": ListOf(step, max_len=limits.MAX_GRID_STEPS, unique=int),
        "cuts": cells,
        "arp_mode": Choice(("off", "up", "down", "updown")),
        "arp_octaves": Num(1, 3, integer=True),
        "drum_bars": grid_choice,
        "drum_steps": spb_any,
        "drum_swing": Num(0.0, 0.6),
        "drum_humanize": Num(0.0, 1.0),
        "drum_vel": step_values,
        "drum_samples": MapOf(drum_voice, Str(nonempty=True), max_len=len(DRUM_ROWS)),
        "drum_mix": MapOf(
            drum_voice,
            Record(
                {
                    "level": Num(0.0, 1.5),
                    "mute": Bool(),
                    "solo": Bool(),
                    "prob": Num(0.0, 1.0),
                },
                partial=True,
            ),
            max_len=len(DRUM_ROWS),
            drop_empty=True,
        ),
        "clips": ListOf(
            Record(
                {
                    "clip": Leaf(_clip_name, "clip", example="take"),
                    "at_bar": Num(0, 100_000, 0, integer=True),
                    "gain": Num(0.0, 1.5, 1.0),
                }
            ),
            max_len=limits.MAX_LANE_CLIPS,
        ),
        "drum_hits": drum_hits,
        "drum_row_swing": row_swing,
        "strum": Num(*limits.STRUM_MS_RANGE, 0.0, example=20.0),
        # the bar (meter.py): absent at 4/4; a ramp only a section's render
        # sets (`song_model.section_arrangement`), never a saved loop
        "meter": meter_name,
        "ramp": Leaf(_ramp, list, example=[140.0, 16.0, 0.0]),
        # the loop's chord track (Perform's chord lane): absent when unset
        "chords": Opt(chord_track),
    }
    # every field's default IS the dataclass default — one source of truth
    arr_defaults = Arrangement()
    names = [f.name for f in fields(Arrangement)]
    assert set(names) == set(arr_fields), "schema.py must cover every Arrangement field"
    arr_fields = {
        n: defaulted(arr_fields[n], lambda n=n: copy.deepcopy(getattr(arr_defaults, n)))
        for n in names
    }

    def arr_post(out: dict, raw: dict) -> dict:
        if "drum_bars" not in raw:  # pre-split state: drums shared the synth clock
            out["drum_bars"], out["drum_steps"] = out["bars"], out["steps_per_bar"]
            out["drum_swing"] = out["swing"]
        if out.get("drum_hits"):  # a hit's data needs its cell (the cuts rule)
            on = {f"{v}.{int(st)}" for v, st in out["drums"]}
            out["drum_hits"] = {k: v for k, v in out["drum_hits"].items() if k in on}
        if out.get("note_nudge"):  # a step's timing needs a note on it; 0 is none
            out["note_nudge"] = clean_nudge(out["note_nudge"], out["cells"])
        _fit_steps(out, out.get("meter", _meter.DEFAULT))
        for k in ARR_OPTIONAL:  # absent when unset: no older file gains a key
            if arr_unset(k, out.get(k)):
                out.pop(k, None)
        return out

    arrangement = Record(arr_fields, post=arr_post, label="Arrangement")
    # a default skips the post: it must not carry the unset optional keys
    # either (`raw` with its own drum clock, so nothing is taken for
    # pre-split state)
    arrangement = defaulted(
        arrangement, lambda rec=arrangement: arr_post(rec._defaults(), {"drum_bars": 1})
    )

    # a lane as a render plays it (a scene's / preset's other lanes)
    # `follow` (a bred Sound's layer plays the main lane's notes at an
    # octave, arrange.follow_layer) and `role` (sub/pad/pluck/air, what the
    # breeder made it as) are optional: absent on every lane that has
    # neither, so no older layer, preset or render key gains a key
    layer_render = {
        "patch": Opt(patch),
        "cells": defaulted(cells, list),
        "muted": Bool(),
        "gain": Num(0.0, 1.5, 1.0),
        "fx": fx,
        "follow": Opt(
            Record(
                {"octave": Num(FOLLOW_OCTAVES[0], FOLLOW_OCTAVES[-1], 0, integer=True)}
            )
        ),
        "role": Opt(Choice(LAYER_ROLES)),
    }

    def drop_unset(out: dict, raw: dict) -> dict:
        for k in ("follow", "role"):
            if out.get(k) is None:
                out.pop(k, None)
        return out

    def seq_post(out: dict) -> dict:
        for k in ("strum", "note_nudge", "note_chance"):
            if not out.get(k):
                out.pop(k, None)
        return out

    def drop_slot_unset(out: dict, raw: dict) -> dict:
        out = drop_unset(out, raw)
        if not out.get("solo"):  # absent unless soloed: no slot gains a key
            out.pop("solo", None)
        if out.get("sound_fx") is None:
            out.pop("sound_fx", None)
        return out

    # a working-loop slot (state.layers): the lane plus loop state that is
    # never render input (state.render_layer) — muted by the song's mix
    # rather than its own mute; the slot's own sequencing and curves (None
    # until the slot is first stashed); `solo` (the layer strip's S: while
    # any slot is soloed only soloed slots are heard — monitoring, never
    # carried into a preset, a scene or the song); `sound_fx` (the FX the
    # slot's last Sound load brought — a patch-only load drops them while
    # the rack still equals them: `StudioState.load_sound`)
    layer = Record(
        {
            **layer_render,
            "silent": Bool(),
            "seq": Opt(
                Record(
                    {
                        **{k: arr_fields[k] for k in LAYER_SEQ_KEYS},
                        # absent while unset: no stashed slot gains a key
                        "strum": Opt(arr_fields["strum"]),
                        "note_nudge": Opt(arr_fields["note_nudge"]),
                        "note_chance": Opt(arr_fields["note_chance"]),
                    },
                    post=lambda out, raw: seq_post(out),
                )
            ),
            "curves": Opt(curves),
            "solo": Opt(Bool()),
            "sound_fx": Opt(fx),
        },
        post=drop_slot_unset,
    )
    layers_fixed = FixedList(
        layer,
        limits.LAYER_SLOTS,
        fill=lambda i: _empty_layer(),
        default=lambda: [_empty_layer() for _ in range(limits.LAYER_SLOTS)],
        # a save from before the fourth slot pads, never resets its layers
        strict=False,
    )
    # a scene's / preset's other lanes
    layers_other = ListOf(
        Record(layer_render, post=drop_unset), max_len=limits.LAYER_SLOTS - 1
    )

    scene = Record(
        {
            "name": Str(),
            "patch": patch,
            "arr": arrangement,
            "curves": curves,
            "layers": layers_other,
            "main_muted": Bool(),
            "main_fx": fx,
            "master_fx": fx,
            "fx_drums": fx,
        }
    )

    pattern = Record(
        {
            "name": Str("pattern", max_len=40),
            "steps": ListOf(
                Tuple(drum_voice, step), max_len=limits.MAX_DRUM_CELLS, unique=tuple
            ),
            "bars": Num(1, 4, 1, integer=True),
            "steps_per_bar": defaulted(spb_choice, 16),
            "swing": Num(0.0, 1.0, 0.0),
            "hits": drum_hits,
            "row_swing": row_swing,
        },
        post=_pattern_post,
    )

    def default_macro(i: int) -> dict:
        return {"name": f"Macro {i + 1}", "value": 0.0, "targets": []}

    def name_macros(out: list) -> list:
        for i, m in enumerate(out):
            if not m["name"]:
                m["name"] = default_macro(i)["name"]
        return out

    macro = Record(
        {
            "name": Str(max_len=40),
            "value": Num(0.0, 1.0, 0.0),
            "targets": ListOf(
                Record(
                    # not a performance control: a saved pitch_bend target
                    # is dropped on load, like any unknown one
                    {"param": Choice(MACRO_PARAMS), "amt": Num()},
                    key=("param", "amt"),
                ),
                max_len=3,
            ),
        }
    )
    macros = FixedList(
        macro,
        4,
        fill=default_macro,
        default=lambda: [default_macro(i) for i in range(4)],
        strict=False,
        post=name_macros,
    )

    tile = Record(
        {
            "id": Str(nonempty=True),
            "name": Str(),
            "sub_pct": Num(0.0, 100.0, 0.0),
            "centroid": Num(0, limits.MAX_SR, 0, integer=True),
            "spectrum": ListOf(Num(), max_len=limits.MAX_TILE_SPECTRUM),
            "badges_genes": ListOf(Str(), max_len=limits.MAX_TILE_SPECTRUM),
            "vs_parent": Str(max_len=40),
            # a layered Sound's card: its parts, for the chips
            "chips": ListOf(
                Record({"role": Str(max_len=8), "label": Str(max_len=24)}),
                max_len=4,
            ),
            "layered": Bool(),
            # what the card changed from its parent (breedboard.changes): a
            # gene's name, its two values, their places on its range (-1: a
            # choice); a board saved before it gets none
            "changes": ListOf(
                Record(
                    {
                        "label": Str(max_len=28),
                        "from": Str(max_len=24),
                        "to": Str(max_len=24),
                        "a": Num(-1.0, 1.0, -1.0),
                        "b": Num(-1.0, 1.0, -1.0),
                    }
                ),
                max_len=8,
            ),
        },
        key=("id",),
    )
    tiles = ListOf(tile, max_len=limits.MAX_BREED_TILES)

    legacy_song = ListOf(
        Record(
            {
                "scene": Num(
                    0, limits.MAX_SCENES - 1, integer=True, integral=True, clamp=False
                ),
                "repeats": Num(1, 16, 1, integer=True),
            },
            key=("scene",),
        ),
        max_len=limits.MAX_SONG_ENTRIES,
    )

    # --- the song arranger (song_model.py) --------------------------------
    # Tracks are rows, the pattern pool holds the notes, sections are the
    # columns. Leaves are the Arrangement's own nodes, so a pattern's lanes
    # have exactly the Arrangement's names and value shapes.
    from .song_model import (
        CELL_KIND,
        CHORD_QUALITIES,
        PALETTE,
        PATTERN_VARIANTS,
        TRACK_KINDS,
        TRACK_ROLES,
        new_id,
    )

    sid = Leaf(_song_id, new_id, example="a1b2c3d4")
    color = Leaf(_color, PALETTE[0], example=PALETTE[1])
    title = Str(max_len=limits.MAX_TITLE_CHARS)

    # --- song creation (studio docs/songcraft.md) ------------------------
    # Every node below is OPTIONAL and ABSENT WHEN UNSET: a post hook takes
    # the key out again, so no song saved before them gains a key, no
    # content hash or render key moves, and a save of an old session is the
    # bytes it was. None of them is a render input until a renderer reads
    # it (the chord track never is: it is guidance for the generators).

    def _gen_param(x):
        if isinstance(x, bool):
            return x
        if isinstance(x, str):
            return x[:24]
        if isinstance(x, int | float) and math.isfinite(x) and abs(x) <= 1e6:
            return x
        raise Invalid("not a generator parameter")

    # what "roll again", "busier" and "sparser" read off a generated
    # pattern (and off a started song): which generator, its seed, its few
    # scalar parameters. A hand edit leaves it.
    gen = Record(
        {
            "kind": Str(max_len=24, nonempty=True),
            "seed": Num(0, limits.SEED_MODULO - 1, 0, integer=True),
            "params": MapOf(
                Str(max_len=16, nonempty=True),
                Leaf(_gen_param, example="roll"),
                max_len=12,
            ),
        },
        key=("kind",),
    )

    strum = Num(*limits.STRUM_MS_RANGE, 0.0)

    def _unset(out: dict, *names: str) -> dict:
        for k in names:
            if not out.get(k):  # None, "", 0, {} and []: all "unset"
                out.pop(k, None)
        return out

    def _tag(x: str) -> str:
        """A loop's tag: lower case, no "#", letters, digits, "-" and "_"."""
        return re.sub(r"[^a-z0-9_-]", "", x.strip().lstrip("#").casefold())

    def _loop_post(out: dict, raw: dict) -> dict:
        # a beat is a loop with a beat, an idea one with a scene's slots;
        # `kind` says so, and is absent on a loop of notes, as `star`
        # unset and no `tags` are
        idea = out.get("idea")
        if idea is not None and not any(idea):
            out["idea"] = idea = None  # a scene of nothing is no idea
        out["kind"] = "idea" if idea else "beat" if out.get("beat") else None
        if idea:
            out["beat"] = None
        return _unset(out, "kind", "star", "tags", "beat", "idea", "free")

    def track_post(out: dict, raw: dict) -> dict:
        if out.get("sends"):
            out["sends"] = {k: v for k, v in out["sends"].items() if v > 0}
        return _unset(out, "role", "pan", "sends")

    def track_kind(kind: str, extra: dict[str, Node]) -> Node:
        rec = Record(
            {
                "id": sid,
                "name": defaulted(title, "Track"),
                "kind": Choice((kind,)),
                "mute": Bool(),
                "solo": Bool(),
                "gain": Num(0.0, 1.5, 1.0),
                "fx": fx,
                **extra,
                # what the track is FOR (starters, the checklist, Balance
                # and auto-arrange read it; a hand-made track has none)
                "role": Opt(Choice(TRACK_ROLES)),
                # the mixer's (render.py renders them on the stems)
                "pan": Num(-1.0, 1.0, 0.0),
                "sends": MapOf(sid, unit, max_len=limits.MAX_RETURNS),
            },
            key=("id", "kind"),
            post=track_post,
        )
        # a default skips the post: it must not carry the unset keys either
        return defaulted(rec, lambda: track_post(rec._defaults(), {}))

    track_drums = track_kind(
        "drums",
        {k: arr_fields[k] for k in ("drum_params", "drum_mix", "drum_samples")},
    )
    track_synth = track_kind("synth", {"patch": patch, "curves": curves})
    track_audio = track_kind("audio", {})
    track = Tagged(
        "kind", {"drums": track_drums, "synth": track_synth, "audio": track_audio}
    )

    def pool_post(out: dict, raw: dict) -> dict:
        """A pool pattern may be 8 bars only while bars x steps a bar fits
        the grid (`limits.PATTERN_BAR_CHOICES`); the creation keys are
        absent when unset; a hit's data needs its cell."""
        # a pattern written in another meter says so (absent at 4/4), and
        # its steps are its meter's (`meter.step_choices`)
        if out.get("meter") in (None, _meter.DEFAULT):
            out.pop("meter", None)
        _fit_steps(out, out.get("meter", _meter.DEFAULT), ("steps_per_bar",))
        if "steps" in out and out["steps"] not in _meter.step_choices(
            out.get("meter", _meter.DEFAULT)
        ):
            out["steps"] = _meter.like(out.get("meter", _meter.DEFAULT), 16)
        per_bar = out.get("steps", out.get("steps_per_bar", 16))
        fits = [
            b
            for b in limits.PATTERN_BAR_CHOICES
            if b <= out["bars"] and b * per_bar <= limits.MAX_GRID_STEPS
        ]
        out["bars"] = max(fits, default=1)
        if out.get("hits"):
            on = {f"{v}.{int(s)}" for v, s in out["drums"]}
            out["hits"] = {k: v for k, v in out["hits"].items() if k in on}
        if out.get("note_nudge"):  # a step's timing needs a note on it
            out["note_nudge"] = clean_nudge(out["note_nudge"], out["cells"])
        return _unset(
            out,
            *("gen", "fam", "var", "strum", "hits", "swing"),
            *("note_nudge", "note_chance"),
        )

    def pattern_kind(kind: str, extra: dict[str, Node]) -> Node:
        rec = Record(
            {
                "id": sid,
                "name": defaulted(title, "Pattern"),
                "color": color,
                "kind": Choice((kind,)),
                "bars": Choice(limits.PATTERN_BAR_CHOICES, 1),
                **extra,
                # song creation: how it was generated, and the family of
                # variants (A-D and a fill) it belongs to
                "gen": Opt(gen),
                "fam": Opt(sid),
                "var": Opt(Choice(PATTERN_VARIANTS)),
                # the meter its steps were written in (absent: 4/4)
                "meter": Opt(meter_name),
            },
            key=("id", "kind"),
            post=pool_post,
        )
        return defaulted(rec, lambda: pool_post(rec._defaults(), {}))

    pattern_drums = pattern_kind(
        "drums",
        {
            "steps": Choice(_meter.ALL_STEP_CHOICES, 16),
            "drums": arr_fields["drums"],
            "drum_vel": arr_fields["drum_vel"],
            "hits": drum_hits,
            "swing": row_swing,
        },
    )
    pattern_notes = pattern_kind(
        "notes",
        {
            "steps_per_bar": arr_fields["steps_per_bar"],
            "cells": defaulted(cells, list),
            "velocities": arr_fields["velocities"],
            "slides": arr_fields["slides"],
            "cuts": arr_fields["cuts"],
            "arp_mode": arr_fields["arp_mode"],
            "arp_octaves": arr_fields["arp_octaves"],
            "strum": strum,
            # a loop's timing inside a step and its chance lane, carried
            # into the song: absent when unset, so no pattern
            # from before them gains a key or moves its content hash
            "note_nudge": note_nudge,
            "note_chance": note_chance,
        },
    )
    song_pattern = Tagged("kind", {"drums": pattern_drums, "notes": pattern_notes})

    take = Record(
        {
            "track_id": sid,
            "clip": Leaf(_clip_name, "clip", example="take"),
            "at_bar": Num(0, limits.MAX_SECTION_BARS - 1, 0, integer=True),
            "gain": Num(0.0, 1.5, 1.0),
        },
        key=("track_id",),
    )

    def lane_post(out: dict, raw: dict) -> dict:
        out["points"] = sorted(out["points"], key=lambda p: p[0])
        return out

    # one automation lane of a (section, track) cell (song_model.set_lane):
    # breakpoints [position 0..1 across one pass of the section, value 0..1
    # of the target's range], kept in position order. A lane with an unknown
    # target, or without a start and an end, is no lane at all. A target is
    # the level, a curvable field, or a whitelisted parameter of an FX unit
    # named by id (`curves.parse_fx`; whether the unit exists is the
    # render's question — a lane whose unit is gone is kept, and inert).
    def lane_target(x) -> str:
        if isinstance(x, str) and (x in LANE_TARGETS or parse_fx(x)):
            return x
        raise Invalid("not a lane target")

    lane = Record(
        {
            "target": Leaf(lane_target, GAIN, example="fx:a1b2c3d4:reverb:mix"),
            "points": ListOf(
                Tuple(unit, unit), max_len=limits.MAX_LANE_POINTS, min_len=2
            ),
        },
        key=("target", "points"),
        post=lane_post,
    )

    def section_post(out: dict, raw: dict) -> dict:
        # absent unless a cell is automated: no older section gains a key
        if not out.get("auto"):
            out.pop("auto", None)
        # ... nor unless a block is trimmed (`song_post` holds each span to
        # its section and its cell)
        if not out.get("spans"):
            out.pop("spans", None)
        # ... nor unless it has an energy or a chord track of its own
        if out.get("energy") is None:
            out.pop("energy", None)
        if not (out.get("chords") or {}).get("slots"):
            out.pop("chords", None)
        # ... nor unless it keeps a meter, a tempo or a ramp of its own
        # (`song_model.section_clock`; `song_post` drops one equal to the
        # song's)
        for k in ("meter", "bpm", "ramp"):
            if out.get(k) is None:
                out.pop(k, None)
        return out

    # a block's span (song_model.set_span): [at, len] in bars — the cell's
    # pattern plays from bar `at` (0-based) of each pass of its section for
    # `len` bars, tiled, and is silent outside
    span = Tuple(
        Num(0, limits.MAX_SECTION_BARS - 1, integer=True),
        Num(1, limits.MAX_SECTION_BARS, integer=True),
    )

    section = Record(
        {
            "id": sid,
            "name": defaulted(title, "Section"),
            "color": color,
            "bars": Num(1, limits.MAX_SECTION_BARS, 4, integer=True),
            "repeats": Num(1, limits.MAX_SECTION_REPEATS, 1, integer=True),
            "cells": MapOf(sid, Opt(sid), max_len=limits.MAX_TRACKS),
            "audio": ListOf(take, max_len=limits.MAX_LANE_CLIPS),
            # {track id: lanes}, one per target
            "auto": MapOf(
                sid,
                ListOf(
                    lane, max_len=limits.MAX_CELL_LANES, unique=lambda a: a["target"]
                ),
                max_len=limits.MAX_TRACKS,
                drop_empty=True,
            ),
            # {track id: [at, len]}: absent for a track = the whole section
            "spans": MapOf(sid, span, max_len=limits.MAX_TRACKS),
            # song creation: how full the section is meant to be (0..1, the
            # arranger's energy lane), and a chord track that overrides the
            # song's for this section
            "energy": Opt(unit),
            "chords": Opt(chord_track),
            # its own bar and tempo (meter.py): a meter, a bpm, and the bpm
            # a ramp reaches at the end of each pass
            "meter": Opt(meter_name),
            "bpm": Opt(Num(*limits.BPM_RANGE)),
            "ramp": Opt(Num(*limits.BPM_RANGE)),
        },
        key=("id",),
        post=section_post,
    )
    # ... and neither does the default section (a default skips the post)
    section = defaulted(section, lambda: section_post(section._defaults(), {}))

    # a long audio item on the song's timeline (song_model, "long audio"):
    # a whole stem (or a mix of stems) of a Lab source, by reference — the
    # source's content id, the stem set's model, the stems and their
    # levels, as a Lab chop names its own mix. Whether that audio is on
    # disk is the render's question (a missing one is silence).
    def _source_id(x) -> str:
        if not isinstance(x, str) or len(x) != 16 or not set(x) <= _HEX_LOWER:
            raise Invalid("not a source id")
        return x

    def _model_id(x) -> str:
        if not isinstance(x, str) or len(x) > 40 or not set(x) <= _MODEL_CHARS:
            raise Invalid("not a model id")
        return x

    def long_post(out: dict, raw: dict) -> dict:
        # sparse levels, of the item's own stems, to a tenth of a dB
        out["levels"] = {
            k: round(v, 1)
            for k, v in out["levels"].items()
            if k in out["stems"] and round(v, 1)
        }
        if not out["model"]:
            out["stems"], out["levels"] = [], {}  # the recording itself
        return out

    stem_name = Choice(_ALL_STEMS)
    long_item = Record(
        {
            "id": sid,
            "track_id": sid,
            "name": defaulted(title, "Stem"),
            "source": Leaf(_source_id, "", example="901dcda285c05ebf"),
            "model": Leaf(_model_id, "", example="htdemucs-e6b688a9"),
            "stems": ListOf(stem_name, max_len=len(_ALL_STEMS), unique=lambda s: s),
            "levels": MapOf(
                stem_name, Num(*limits.LAB_LEVEL_DB_RANGE), max_len=len(_ALL_STEMS)
            ),
            "at_bar": Num(0, limits.MAX_LONG_BAR, 0, integer=True),
            "start": Num(*limits.LONG_START_RANGE, 0.0),
            "length": Num(0.0, limits.MAX_SOURCE_SECONDS, 0.0),
            "gain": Num(0.0, 1.5, 1.0),
            "mute": Bool(),
            "bpm": Num(0.0, limits.LAB_BPM_RANGE[1], 0.0),
            # the source's own length when placed (0: unknown): where a
            # `length` of 0 ends, without the song model opening a file
            "seconds": Num(0.0, limits.MAX_SOURCE_SECONDS, 0.0),
        },
        key=("id", "track_id", "source"),
        post=long_post,
    )

    # a return bus (the mixer's: a shared reverb, a shared delay)
    return_bus = Record(
        {
            "id": sid,
            "name": defaulted(title, "Return"),
            "fx": fx,
            # the return's own fader (`studio/mixer.py`)
            "gain": Num(0.0, 1.5, 1.0),
        },
        key=("id",),
    )
    # a jam's launch log (POST /song/jam/commit; never persisted): at `bar`
    # a track's cell of section `target` was launched (`track`), or a whole
    # section (`scene`), or a track was stopped (`target` None)
    jam_event = Record(
        {
            "bar": Num(0, limits.MAX_LONG_BAR, 0, integer=True),
            "track": Opt(sid),
            "scene": Opt(sid),
            "target": Opt(sid),
        },
        key=("bar",),
    )

    def song_post(out: dict, raw: dict) -> dict:
        """Referential integrity: per-kind track caps; a pattern's id is its
        pool key; a cell names a drums/synth track and a pattern of the
        matching kind (a dangling or wrong-kind pattern becomes null, a
        missing or audio track drops the cell); a take names an audio track;
        a section is at least as long as its longest pattern; automation
        lanes sit on a cell that holds a pattern (an audio track's, on its
        row in a section that holds one of its takes), and a drums cell's
        (an audio row's) lanes are its level and its FX units' parameters;
        a block's span sits on
        a cell that holds a pattern and inside its section (`at` in
        0..bars-1, `len` in 1..bars-at), and one that covers the section
        is no span at all."""
        counts = dict.fromkeys(TRACK_KINDS, 0)
        tracks = []
        for t in out["tracks"]:
            if counts[t["kind"]] < TRACK_KINDS[t["kind"]]:
                counts[t["kind"]] += 1
                tracks.append(t)
        out["tracks"] = tracks[: limits.MAX_TRACKS]
        pool = out["patterns"]
        for pid, p in pool.items():
            p["id"] = pid
        kinds = {t["id"]: t["kind"] for t in out["tracks"]}
        song_meter = out.get("meter") or _meter.DEFAULT
        for sec in out["sections"]:
            if sec.get("meter") == song_meter:
                del sec["meter"]
            if sec.get("bpm") == out["bpm"]:
                del sec["bpm"]
            if "ramp" in sec and sec["ramp"] == sec.get("bpm", out["bpm"]):
                del sec["ramp"]
            cells_out: dict = {}
            longest = 1
            for tid, pid in sec["cells"].items():
                want = CELL_KIND.get(kinds.get(tid))
                if want is None:
                    continue
                p = pool.get(pid) if pid is not None else None
                if p is None or p["kind"] != want:
                    cells_out[tid] = None
                    continue
                cells_out[tid] = pid
                longest = max(longest, p["bars"])
            sec["cells"] = cells_out
            sec["audio"] = [
                a for a in sec["audio"] if kinds.get(a["track_id"]) == "audio"
            ]
            sec["bars"] = min(limits.MAX_SECTION_BARS, max(sec["bars"], longest))
            # an audio track's lanes sit on its row, while it has a take here
            taking = {a["track_id"] for a in sec["audio"]}
            auto = {
                tid: keep
                for tid, lanes in sec.get("auto", {}).items()
                if (cells_out.get(tid) is not None or tid in taking)
                and (
                    keep := [
                        ln
                        for ln in lanes
                        if kinds[tid] == "synth"
                        or ln["target"] == GAIN
                        or parse_fx(ln["target"])
                    ]
                )
            }
            sec.pop("auto", None)
            if auto:
                sec["auto"] = auto
            spans: dict = {}
            for tid, (at, n) in sec.get("spans", {}).items():
                if cells_out.get(tid) is None:
                    continue
                at = min(at, sec["bars"] - 1)
                n = min(n, sec["bars"] - at)
                if (at, n) != (0, sec["bars"]):
                    spans[tid] = [at, n]
            sec.pop("spans", None)
            if spans:
                sec["spans"] = spans
        # long audio sits on an audio track; absent unless the song holds
        # one, so no older song gains a key
        held = [x for x in out.get("long", ()) if kinds.get(x["track_id"]) == "audio"]
        out.pop("long", None)
        if held:
            out["long"] = held
        # song creation: a title, a chord track with slots, returns and how
        # the song was started — each absent unless set; a send names a
        # return the song has
        buses = {r["id"] for r in out.get("returns") or ()}
        for t in out["tracks"]:
            if "sends" in t:
                t["sends"] = {k: v for k, v in t["sends"].items() if k in buses}
                if not t["sends"]:
                    del t["sends"]
        if not (out.get("chords") or {}).get("slots"):
            out.pop("chords", None)
        if out.get("meter") == _meter.DEFAULT:
            out.pop("meter", None)
        return _unset(out, "title", "returns", "gen", "meter")

    song = Record(
        {
            **{
                k: arr_fields[k]
                for k in (
                    "bpm",
                    "root",
                    "scale_name",
                    "swing",
                    "drum_swing",
                    "drum_humanize",
                )
            },
            "tracks": ListOf(
                track, max_len=4 * limits.MAX_TRACKS, unique=lambda t: t["id"]
            ),
            "patterns": MapOf(sid, song_pattern, max_len=limits.MAX_PATTERNS),
            "sections": ListOf(
                section, max_len=limits.MAX_SECTIONS, unique=lambda s: s["id"]
            ),
            # set once by the v2 -> v3 migration (song_bridge.migration_note)
            # when scenes had to give up a tempo/key/groove/kit/master FX of
            # their own; the Song page shows it once, then clears it
            "migration_note": Str(max_len=limits.MAX_MIGRATION_NOTE_CHARS),
            "long": ListOf(
                long_item, max_len=limits.MAX_LONG_AUDIO, unique=lambda x: x["id"]
            ),
            # song creation (absent unless set — `song_post`)
            "title": title,
            "chords": Opt(chord_track),
            "returns": ListOf(
                return_bus, max_len=limits.MAX_RETURNS, unique=lambda r: r["id"]
            ),
            "gen": Opt(gen),
            # the song's own bar (meter.py; absent at 4/4): a section keeps
            # it unless it has one of its own
            "meter": Opt(meter_name),
        },
        post=song_post,
    )
    # the default song holds no "long" either (a default skips the post)
    song = defaulted(song, lambda: song_post(song._defaults(), {}))

    # a loop's name in a slot of Perform's launcher (`perform_slots`) or of
    # an idea (a scene kept as a loop, `LOOP.idea`)
    slot_name = Opt(Str(nonempty=True, fn=_loop_name))
    slot_lanes = FixedList(
        slot_name, SLOT_LANES, fill=lambda i: None, default=lambda: [None] * SLOT_LANES
    )
    slot_row = Record({"slots": slot_lanes, "name": Str(max_len=limits.MAX_NAME_CHARS)})

    def factory_bank() -> list:
        bank: list = [dict(p) for p in DRUM_FACTORY[:BANK_SLOTS]]
        return bank + [None] * (BANK_SLOTS - len(bank))

    top_fields: dict[str, Node] = {
        "patch": patch,
        "arrangement": arrangement,
        "slot_a": Opt(patch),
        "slot_b": Opt(patch),
        "slot_prev": Opt(patch),
        "curves": curves,
        "breed_tiles": tiles,
        "breed_starred": ListOf(
            Str(), max_len=limits.MAX_STARRED, unique=str, keep="last"
        ),
        "breed_history": ListOf(tiles, max_len=limits.MAX_BREED_HISTORY, keep="last"),
        "breed_wildness": Choice(WILDNESS, "normal"),
        # how much of a Sound a litter breeds (genome.DEPTH): single is the
        # patch breeder as it always was
        "breed_depth": Choice(DEPTHS, "single"),
        "audition": Choice(PHRASE_CHOICES, "hold"),
        "vs_beat": Bool(),
        # Bass Lab's quick key: the low root its bench render transposes
        # the phrase to (None: the phrase's own register). Never the grid's.
        "bass_root": Opt(Choice(ROOTS)),
        "macros": macros,
        "fx_drums": fx,
        "perform_parts": ListOf(
            Choice(("synth", "drums")),
            max_len=2,
            unique=str,
            min_len=1,
            default=lambda: ["synth", "drums"],
        ),
        "baselines": None,  # filled in below: its values are the scope nodes
        "xy_axes": Tuple(
            Choice(CONTINUOUS_PARAMS),
            Choice(CONTINUOUS_PARAMS),
            default=lambda: ["cutoff", "resonance"],
        ),
        "xy_gesture": ListOf(
            Tuple(
                Num(0.0, clamp=False),
                Num(0.0, 1.0, clamp=False),
                Num(0.0, 1.0, clamp=False),
            ),
            max_len=limits.MAX_XY_GESTURE_POINTS,
        ),
        "layers": layers_fixed,
        # "before last load" of a whole Sound (a v2 preset, a layered breed
        # card): the lanes, main FX and curves it replaced (swap_prev)
        "stack_prev": Opt(
            Record({"layers": layers_fixed, "main_fx": fx, "curves": curves})
        ),
        "active_layer": Num(0, limits.LAYER_SLOTS - 1, 0, integer=True),
        # the main lane: the slot following layers follow (None: the active
        # one, as before there was a choice — `StudioState.main_index`)
        "main_layer": Opt(Num(0, limits.LAYER_SLOTS - 1, integer=True, clamp=False)),
        # Synths' ▶ listening: False hears the whole stack, True only the
        # layer being edited (`StudioService.render_inputs`)
        "layer_focus": Bool(),
        # the note grid's range (`state.pitch_rows`): 1 = the key's first six
        # tones (every grid before there was a choice), 2 = two octaves
        # 3 = chromatic: the 24 semitones from the window's bottom
        "grid_span": Num(1, 3, 1, integer=True),
        "fx_master": fx,
        # the song arranger (v3). The legacy `scenes` + scene running order
        # it replaced are read only by the v2 -> v3 migration
        # (song_bridge.migrate_v2, through LEGACY_SCENES / LEGACY_SONG) and
        # never written again.
        "song": song,
        "drum_bank": FixedList(
            Opt(pattern), BANK_SLOTS, fill=lambda i: None, default=factory_bank
        ),
        "active_slot": Opt(Num(0, BANK_SLOTS - 1, integer=True, clamp=False)),
        # Perform's launcher (studio loop-and-perform.md, "Slots and
        # scenes"): rows of slots — a scene
        # each — one a lane (the four layer slots, then the drums), each a
        # loop's name or empty, and the scene's name. ABSENT WHEN UNSET (no
        # row holds anything: `_payload_post`), so no session saved before
        # it, nor one that never used it, gains a key
        "perform_slots": Opt(ListOf(slot_row, max_len=limits.MAX_SLOT_ROWS)),
    }

    # one node per resettable key: what revert/reset (_scope_set) and a
    # stored baseline both validate against
    scopes: dict[str, Node] = {"patch": patch, "curves": curves, "layer_fx": fx}
    for _label, keys in RESET_SCOPES.values():
        for k in keys:
            if k in scopes:
                continue
            kind, name = k.split(".", 1)
            scopes[k] = arr_fields[name] if kind == "arr" else top_fields[name]
    baselines = Record(
        {
            scope: Record(
                {
                    "label": Str(),
                    "data": Record({k: scopes[k] for k in keys}, partial=True),
                }
            )
            for scope, (_label, keys) in RESET_SCOPES.items()
        },
        partial=True,
    )
    top_fields["baselines"] = baselines

    payload = Record(
        top_fields,
        required=("patch", "arrangement", "slot_a", "slot_b"),
        post=_payload_post,
    )

    preset_v2 = Record(
        {"patch": patch, "layers": layers_other, "main_fx": fx, "curves": curves}
    )

    return SimpleNamespace(
        PAYLOAD=payload,
        ARRANGEMENT=arrangement,
        PATCH=patch,
        FX=fx,
        CURVES=curves,
        LAYER=layer,
        LAYERS_OTHER=layers_other,
        SCENE=scene,
        LEGACY_SCENES=ListOf(scene, max_len=limits.MAX_SCENES),
        PATTERN=pattern,
        MACROS=macros,
        TILES=tiles,
        SONG=song,
        LEGACY_SONG=legacy_song,
        TRACK=track,
        PATTERN_DRUMS=pattern_drums,
        PATTERN_NOTES=pattern_notes,
        SONG_PATTERN=song_pattern,
        SECTION=section,
        LANE=lane,
        LONG=long_item,
        # song creation: the chord track (the song's, a section's, and the
        # session's loop's), a generator's record, a return, and the
        # loop-level nodes that are not fields of the Arrangement (one that
        # becomes a field adds its line to `arr_fields`: an Arrangement
        # field is in every loop key)
        CHORDS=chord_track,
        CHORD_SLOT=chord_slot,
        GEN=gen,
        RETURN=return_bus,
        DRUM_HITS=drum_hits,
        ROW_SWING=row_swing,
        STRUM=strum,
        JAM_EVENTS=ListOf(jam_event, max_len=limits.MAX_JAM_EVENTS),
        BASELINES=baselines,
        SCOPES=scopes,
        PRESET_V2=preset_v2,
        # studio_data/calibration.json: the per-machine mic latency, used
        # when the browser reports none (routes/song_record.py)
        CALIBRATION=Record(
            {"latency_ms": Num(0.0, limits.MAX_LATENCY_MS, default=0.0)}
        ),
        AGENT_LOG=_agent_log(),
        # studio_data/loops/<name>.json (loops.py): a saved loop — its note
        # lane, tempo and key (an Arrangement's fields; the rest default)
        # and the whole sound that plays it, as a format-2 preset holds it.
        # From ⏺ Rec (one Rec makes one, loops.py): `take`, the clip
        # of its audio; `played`, the notes as played — [start, length, midi,
        # velocity], beats from the take's first downbeat, never rewritten;
        # `quantise`, the strength (0-100) the grid places them at; `edited`
        # once the grid was changed by hand. An older loop without them reads
        # as one with no audio and nothing played. The Loops panel's (loops.py),
        # each ABSENT WHEN UNSET — an older loop file
        # reads, and is written back, as it was: `kind` "beat" (a beat: its
        # drum lane in `beat`, a PATTERN as the bank keeps one — never in
        # `arr`, whose note lane stays empty), `star` (★), `tags`
        LOOP=Record(
            {
                "v": Choice((1,)),
                "name": Str(max_len=limits.MAX_NAME_CHARS),
                "at": Num(0.0, 1e12, 0.0),
                "arr": arrangement,
                "sound": Opt(preset_v2),
                "take": Opt(Str(max_len=limits.MAX_NAME_CHARS, nonempty=True)),
                "played": ListOf(
                    Tuple(
                        Num(0.0, 4.0 * limits.MAX_TAKE_BARS),
                        Num(0.001, 4.0 * limits.MAX_TAKE_BARS),
                        Num(0, 127, integer=True),
                        Num(0.0, 1.0),
                    ),
                    max_len=limits.MAX_TAKE_NOTES,
                ),
                "quantise": Num(0, 100, 100, integer=True),
                "edited": Bool(),
                "kind": Opt(Choice(("beat", "idea"))),
                "star": Bool(),
                "tags": ListOf(
                    Str(max_len=limits.MAX_TAG_CHARS, nonempty=True, fn=_tag),
                    max_len=limits.MAX_LOOP_TAGS,
                    unique=str,
                ),
                "beat": Opt(pattern),
                # an idea (Perform's launcher): a scene
                # kept as a loop — the loop in each lane's slot (the four
                # layer slots, then the drums), `arr` its tempo, key and meter
                "idea": Opt(slot_lanes),
                # a free loop (free record's shaper): `played`
                # is the take as shaped, placed KEEPING ITS TIMING
                # (`loops.place_free`: a step's `note_nudge`), `swing` the
                # odd steps' lean, `take` the free take it was shaped from
                # (its audio as played: never the loop's, which is the
                # shaped notes). Absent on every other loop
                "free": Opt(
                    Record(
                        {
                            "swing": Num(0.0, 0.5, 0.0),
                            "take": Opt(
                                Str(max_len=limits.MAX_NAME_CHARS, nonempty=True)
                            ),
                        }
                    )
                ),
            },
            post=_loop_post,
        ),
    )


def _agent_log() -> Record:
    """studio_data/agent_log.json: the sound agent's chat log and budget
    across restarts (agent/service.py). Display fields only — a turn's
    stack snapshots stay runtime, so Undo turn never reaches a turn from
    an earlier run — and never a secret (the key is not a field)."""
    from .agent.context import FEATURE_KEYS

    count = Num(0, 1e12, 0, integer=True)
    line = Str(max_len=limits.MAX_AGENT_LINE_CHARS)
    lines = ListOf(line, max_len=limits.MAX_AGENT_LINES)
    features = Opt(
        Record(
            {k: Num(-1e6, 1e6, 0.0) for k in FEATURE_KEYS}
            | {"loudness_pre_norm": Bool()}
        )
    )
    check = Record(
        {
            "feature": Choice(FEATURE_KEYS),
            "want": Choice(("up", "down")),
            "before": Num(-1e6, 1e6, 0.0),
            "after": Num(-1e6, 1e6, 0.0),
            "met": Bool(),
        },
        key=("feature", "want"),
    )
    turn = Record(
        {
            "id": Num(1, 1e12, 1, integer=True),
            "request": Str(max_len=limits.MAX_AGENT_REQUEST_CHARS),
            "reply": Str(max_len=limits.MAX_AGENT_REPLY_CHARS),
            "changes": lines,
            "notes": lines,
            "before": features,
            "after": features,
            "checks": ListOf(check, max_len=len(FEATURE_KEYS)),
            "tokens": count,
            "tokens_cached": count,
            "rounds": Num(0, 100, 0, integer=True),
            "error": line,
            "undone": Bool(),
            "model": Str(max_len=80),
            "cost_usd": Opt(Num(0.0, 1e6)),
        },
        key=("id",),
    )
    return Record(
        {
            "v": Choice((1,)),
            "tokens_used": count,
            "turns_used": count,
            "cost_usd": Num(0.0, 1e9, 0.0),
            "unpriced_turns": count,
            "next_id": Num(1, 1e12, 1, integer=True),
            "turns": ListOf(turn, max_len=limits.MAX_AGENT_LOG_TURNS, keep="last"),
        }
    )
