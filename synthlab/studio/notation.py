"""TidalCycles-style mini-notation for the drum grid.

    bd*4 [~ sn] hh*8            one layer: a bar split into 3 equal events
    bd*4, [~ sn]*2, hh*8        layers (",") run in parallel

Grammar (one bar per layer, repeated across bars):

    layers := seq ("," seq)*
    seq    := event+                 events split their span equally
    event  := atom ("*" INT)?        repeat within the event's slot
    atom   := NAME | "~" | "[" seq "]"

Anything that does not land on a grid step (e.g. ``bd*3`` on a 16-step
bar) is a ``NotationError`` rather than a silent quantise.
"""

from __future__ import annotations

import re
from fractions import Fraction

from .limits import MAX_NOTATION_DEPTH, MAX_NOTATION_NODES
from .state import DRUM_ROWS

NAMES: dict[str, str] = {
    "bd": "kick",
    "sn": "snare",
    "sd": "snare",
    "hh": "hat",
    "ch": "hat",
    "cp": "clap",
    "oh": "ohat",
    "tm": "tom",
    "rm": "rim",
    "cr": "crash",
    **{v: v for v in DRUM_ROWS},
}
SHORT: dict[str, str] = {
    "kick": "bd",
    "snare": "sn",
    "hat": "hh",
    "ohat": "oh",
    "clap": "cp",
    "tom": "tm",
    "rim": "rm",
    "crash": "cr",
}

_TOKEN = re.compile(r"\s*(\[|\]|,|\*\d+|~|[A-Za-z]+|\S)")
MAX_TEXT = 400  # characters
MAX_REPEAT = 64  # per "*n"
MAX_EVENTS = 2048  # placed atoms (pre-dedup) — bounds nested repeats


class NotationError(ValueError):
    pass


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            break
        pos = m.end()
        out.append(m.group(1))
    return out


class _Parser:
    """Bounds expansion cost *while* parsing, before `_place` ever runs.

    ``self.nodes`` tracks the total leaf count the fully expanded pattern
    would have (every rest and named hit, repeats included) — ``[atom]*n``
    would place ``n`` copies, so `event()` folds that multiplier straight
    into the running total instead of ever materialising the copies. A
    handful of nested ``*64``s would otherwise blow up combinatorially
    (64**5 placements) while the actual parsed structure stays tiny, since
    Python's ``[x] * n`` shares one object n times — so counting has to
    happen here, arithmetically, rather than by walking the built tree.
    """

    def __init__(self, tokens: list[str]):
        self.t = tokens
        self.i = 0
        self.nodes = 0
        self.depth = 0

    def peek(self) -> str | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self) -> str:
        tok = self.peek()
        if tok is None:
            raise NotationError("unexpected end of pattern")
        self.i += 1
        return tok

    def _grow(self, n: int) -> None:
        self.nodes += n
        if self.nodes > MAX_NOTATION_NODES:
            raise NotationError(f"pattern too dense (> {MAX_NOTATION_NODES} nodes)")

    def layers(self) -> list[list]:
        out = [self.seq()]
        while self.peek() == ",":
            self.take()
            out.append(self.seq())
        if self.peek() is not None:
            raise NotationError(f"unexpected {self.peek()!r}")
        return out

    def seq(self) -> list:
        events = []
        while self.peek() not in (None, ",", "]"):
            events.append(self.event())
        if not events:
            raise NotationError("empty sequence")
        return events

    def event(self):
        before = self.nodes
        atom = self.atom()
        size = self.nodes - before  # leaves in atom's subtree, already counted once
        tok = self.peek()
        if tok and tok.startswith("*"):
            self.take()
            n = int(tok[1:])
            if not 1 <= n <= MAX_REPEAT:
                raise NotationError(f"repeat count must be 1..{MAX_REPEAT}")
            self._grow((n - 1) * size)  # atom already contributed size once
            return [atom] * n  # a sub-sequence: n copies sharing this slot
        return atom

    def atom(self):
        tok = self.take()
        if tok == "[":
            self.depth += 1
            if self.depth > MAX_NOTATION_DEPTH:
                raise NotationError(f"pattern nested too deep (> {MAX_NOTATION_DEPTH})")
            inner = self.seq()
            if self.take() != "]":
                raise NotationError("missing ]")
            self.depth -= 1
            return inner
        if tok == "~":
            self._grow(1)
            return None
        if tok.isalpha():
            if tok.lower() not in NAMES:
                raise NotationError(
                    f"unknown drum {tok!r} (use {', '.join(sorted(set(NAMES)))})"
                )
            self._grow(1)
            return NAMES[tok.lower()]
        raise NotationError(f"unexpected {tok!r}")


def _place(node, start: Fraction, span: Fraction, hits: list[tuple[str, Fraction]]):
    if node is None:
        return
    if isinstance(node, str):
        if len(hits) >= MAX_EVENTS:
            raise NotationError(f"pattern too dense (> {MAX_EVENTS} events)")
        hits.append((node, start))
        return
    step = span / len(node)
    for k, child in enumerate(node):
        _place(child, start + k * step, step, hits)


def parse(text: str, steps_per_bar: int, bars: int = 1) -> list[list]:
    """Sorted ``[voice, step]`` pairs over ``bars * steps_per_bar`` steps."""
    text = text.strip()
    if not text:
        return []
    if len(text) > MAX_TEXT:
        raise NotationError(f"pattern too long (> {MAX_TEXT} characters)")
    hits: list[tuple[str, Fraction]] = []
    for layer in _Parser(_tokens(text)).layers():
        _place(layer, Fraction(0), Fraction(1), hits)
    cells: set[tuple[str, int]] = set()
    for voice, pos in hits:
        step = pos * steps_per_bar
        if step.denominator != 1:
            raise NotationError(
                f"'{text}' needs {step.denominator * steps_per_bar} steps per bar"
                f" (grid has {steps_per_bar})"
            )
        for bar in range(bars):
            cells.add((voice, int(step) + bar * steps_per_bar))
    return sorted([v, s] for v, s in cells)


def to_notation(steps: list[list], steps_per_bar: int, bars: int = 1) -> str:
    """One layer per voice, grouped per beat: ``[bd ~ ~ ~]*4`` when every
    beat matches, else each beat spelled out. Only the first bar is
    described (``parse`` repeats it), so multi-bar patterns that differ
    per bar are not round-trippable — the grid stays the source of truth."""
    per_beat = max(1, steps_per_bar // 4)
    layers: list[str] = []
    for voice in DRUM_ROWS:
        on = {s for v, s in steps if v == voice and s < steps_per_bar}
        if not on:
            continue
        beats = []
        for b in range(0, steps_per_bar, per_beat):
            toks = [SHORT[voice] if s in on else "~" for s in range(b, b + per_beat)]
            beats.append(toks[0] if per_beat == 1 else "[" + " ".join(toks) + "]")
        if len(set(beats)) == 1 and len(beats) > 1:
            layers.append(f"{beats[0]}*{len(beats)}")
        else:
            layers.append(" ".join(beats))
    return ", ".join(layers)
