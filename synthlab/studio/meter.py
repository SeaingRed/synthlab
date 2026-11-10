"""Meters and tempo: what a bar holds, and how long it lasts.

Everything was 4/4 at one tempo: a bar four beats, a step `4 /
steps_per_bar` beats. A meter (`Arrangement.meter`, a song's and a
section's `meter`, a pattern's) says what a bar holds; a clock (`Clock`)
says how long its beats last — one tempo, or a ramp across a section
(accelerando / ritardando). Pure: no state, no I/O.

    m = parse("6/8")           # 12 sixteenths a bar, grouped 3 + 3
    m.bar_beats                # 3.0 (quarter-note beats: what bpm counts)
    Clock(120.0, 3.0).seconds(4)

**4/4 at one tempo is the arithmetic it always was, float for float**:
`Clock(bpm).seconds(bars)` is `bars * 4.0 * (60.0 / bpm)`, the formula
`arrange.loop_seconds` and `song_model.cycle_samples` used, so no render
and no key moves (`tests/studio/test_meter.py`).

The beat bpm counts is always the quarter note, in every meter: a 6/8 bar
at 120 bpm lasts three quarter beats, 1.5 s. A meter is one of `METERS`
(a whitelist: a saved value that is not one reads as 4/4).
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import NamedTuple

from ..core import SR, n_samples

DEFAULT = "4/4"
# the grouping of an x/8 meter is part of its name: 7/8 is 2+2+3, and
# "7/8:3+2+2" the other way round
METERS: tuple[str, ...] = (
    "4/4",
    "2/4",
    "3/4",
    "5/4",
    "6/8",
    "7/8",
    "7/8:3+2+2",
    "12/8",
)
# how a bar is grouped, in its own unit (1/den): the heads of these are the
# accents the grid draws and the metronome clicks lighter than the one
_GROUPS: dict[str, tuple[int, ...]] = {
    "4/4": (4,),
    "2/4": (2,),
    "3/4": (3,),
    "5/4": (3, 2),
    "6/8": (3, 3),
    "7/8": (2, 2, 3),
    "7/8:3+2+2": (3, 2, 2),
    "12/8": (3, 3, 3, 3),
}
# a step's length in sixteenths, coarsest first: a quarter, an eighth, a
# sixteenth, a thirty-second (the 4/4 grid's 4 / 8 / 16 / 32 steps)
_STEP_LENGTHS = (Fraction(4), Fraction(2), Fraction(1), Fraction(1, 2))
MAX_STEPS_A_BAR = 32  # bars x steps fits limits.MAX_GRID_STEPS at 4 bars


class Meter(NamedTuple):
    name: str
    num: int
    den: int
    groups: tuple[int, ...]

    @property
    def bar_beats(self) -> float:
        """A bar in quarter-note beats (what bpm counts): 4.0 for 4/4."""
        return self.num * 4.0 / self.den

    @property
    def sixteenths(self) -> int:
        """A bar in sixteenths: 16 for 4/4, 12 for 3/4 and 6/8, 14 for 7/8."""
        return self.num * 16 // self.den

    @property
    def label(self) -> str:
        """What a picker shows: "7/8 (3+2+2)" for a grouping of its own."""
        if ":" in self.name:
            return f"{self.num}/{self.den} ({self.name.split(':', 1)[1]})"
        if self.name == "7/8":
            return "7/8 (2+2+3)"
        return self.name


def parse(name) -> Meter:
    """A meter by name; anything not in `METERS` is 4/4."""
    name = name if name in METERS else DEFAULT
    sig = name.split(":", 1)[0]
    num, den = (int(x) for x in sig.split("/"))
    return Meter(name, num, den, _GROUPS[name])


def clean(name) -> str:
    return name if name in METERS else DEFAULT


def bar_beats(name) -> float:
    return parse(name).bar_beats


def step_choices(name) -> tuple[int, ...]:
    """The step counts a bar of this meter may have, coarsest first: the
    4/4 grid's 4, 8, 16, 32; 3/4's 3, 6, 12, 24; an x/8 meter has no
    quarter-note step (its beat is not one), so 6/8's are 6, 12, 24. At
    most `MAX_STEPS_A_BAR`."""
    m = parse(name)
    out = []
    for length in _STEP_LENGTHS:
        if m.den == 8 and length > 2:
            continue
        n = Fraction(m.sixteenths) / length
        if n.denominator == 1 and n <= MAX_STEPS_A_BAR:
            out.append(int(n))
    return tuple(out)


ALL_STEP_CHOICES: tuple[int, ...] = tuple(
    sorted({n for m in METERS for n in step_choices(m)})
)


def sixteenths_choice(name) -> int:
    """The meter's sixteenth grid: the drum machine's default."""
    return parse(name).sixteenths


def like(name, steps: int, from_name=DEFAULT) -> int:
    """The step count in `name` with the step length `steps` has in
    `from_name` (16 in 4/4 -> 12 in 3/4, 14 in 7/8); the nearest finer
    choice where that length has none (a quarter step in 7/8 -> eighths)."""
    length = step_length(from_name, steps)
    choices = step_choices(name)
    six = parse(name).sixteenths
    for n in choices:  # coarsest first: the first at or finer than `length`
        if Fraction(six, n) <= length:
            return n
    return choices[-1]


def step_length(name, steps: int) -> Fraction:
    """One step in sixteenths."""
    return Fraction(parse(name).sixteenths, max(1, int(steps)))


def beats(name) -> list[tuple[float, int]]:
    """The metronome's clicks in one bar: (sixteenths into the bar, level)
    — level 2 the bar's first beat, 1 a group's head, 0 any other. A /4
    meter clicks its quarters, an /8 meter its eighths. 4/4 is
    [(0, 2), (4, 0), (8, 0), (12, 0)]: the bar line higher, as it was."""
    m = parse(name)
    unit = 16 // m.den
    heads = set()
    at = 0
    for g in m.groups:
        heads.add(at)
        at += g
    return [
        (float(i * unit), 2 if i == 0 else 1 if i in heads else 0) for i in range(m.num)
    ]


def accents(name, steps: int) -> list[int]:
    """Per step of one bar: 2 the bar's first, 1 a group's head, 0 a beat
    (a click of `beats`), -1 anything else — what the step grids shade."""
    length = step_length(name, steps)
    marks = {Fraction(int(at)): lvl for at, lvl in beats(name)}
    out = []
    for i in range(int(steps)):
        at = i * length
        out.append(marks.get(at, -1) if at.denominator == 1 else -1)
    return out


def describe(name) -> str:
    m = parse(name)
    groups = "+".join(str(g) for g in m.groups)
    return f"{m.num}/{m.den}" + (f" ({groups})" if len(m.groups) > 1 else "")


# --- fitting a grid from one meter onto another ---------------------------


def refit(steps: int, from_name, to_name) -> tuple[int, int]:
    """(the step count a bar of `to_name` takes for a `steps`-step grid in
    `from_name`, the new steps one old step covers: > 1 when the old step
    length has no grid there and a finer one is used, e.g. quarters into
    7/8 become eighths, 2 each). The step length is kept: a bar is fitted,
    never stretched — "the bar as it is"."""
    to = like(to_name, steps, from_name)
    old = step_length(from_name, steps)
    new = step_length(to_name, to)
    return to, max(1, int(old / new)) if old > new else 1


def refit_step(step: int, steps: int, from_name, to_steps: int, to_name) -> int | None:
    """Where step `step` of a `steps`-a-bar grid in `from_name` lands on a
    `to_steps`-a-bar grid in `to_name`: the same bar, the same place in it
    (sixteenths from the bar line); None when the bar is shorter and the
    place is past its end (a 4/4 bar's fourth beat in 3/4)."""
    if from_name == to_name and steps == to_steps:
        return int(step)
    bar, i = divmod(int(step), max(1, int(steps)))
    at = i * step_length(from_name, steps)
    if at >= parse(to_name).sixteenths:
        return None
    return bar * int(to_steps) + int(at // step_length(to_name, to_steps))


# --- the clock: how long beats last ----------------------------------------


class Clock(NamedTuple):
    """One tempo, or a ramp. `ramp` = (to_bpm, over_beats, at_beats): the
    tempo moves in a straight line, per beat, from `bpm` at beat 0 of the
    whole ramp to `to_bpm` at beat `over_beats`, and stays there after;
    this clock starts `at_beats` into it (a chunk of a ramped section is
    the same ramp seen from its own first bar). Without a ramp every
    method is the 4/4 arithmetic it replaced, float for float."""

    bpm: float
    bar_beats: float = 4.0
    ramp: tuple = ()

    def _abs(self, x: float) -> float:
        """Seconds from the ramp's own beat 0 to its beat `x`."""
        to, over, _at = self.ramp
        b0 = float(self.bpm)
        k = (float(to) - b0) / float(over)
        head = min(x, float(over))
        secs = (
            60.0 / k * math.log((b0 + k * head) / b0)
            if abs(k) > 1e-12
            else head * 60.0 / b0
        )
        if x > over:
            secs += (x - float(over)) * 60.0 / float(to)
        return secs

    def at(self, beats: float) -> float:
        """Seconds from this clock's beat 0 to `beats`."""
        if not self.ramp:
            return beats * (60.0 / self.bpm)
        a = float(self.ramp[2])
        return self._abs(a + beats) - self._abs(a)

    def span(self, start: float, beats: float) -> float:
        """How many seconds `beats` beats from beat `start` last."""
        if not self.ramp:
            return beats * (60.0 / self.bpm)
        return self.at(start + beats) - self.at(start)

    def beat_at(self, seconds: float) -> float:
        """The beat sounding `seconds` after this clock's beat 0."""
        if not self.ramp:
            return seconds * self.bpm / 60.0
        to, over, a = (float(x) for x in self.ramp)
        b0 = float(self.bpm)
        t = seconds + self._abs(a)
        end = self._abs(over)
        if t >= end:
            x = over + (t - end) * to / 60.0
        else:
            k = (to - b0) / over
            x = (
                b0 * (math.exp(t * k / 60.0) - 1.0) / k
                if abs(k) > 1e-12
                else t * b0 / 60.0
            )
        return x - a

    def tempo(self, beats: float) -> float:
        """The bpm at beat `beats`."""
        if not self.ramp:
            return float(self.bpm)
        to, over, a = (float(x) for x in self.ramp)
        x = min(a + beats, over)
        return float(self.bpm) + (to - float(self.bpm)) * x / over

    def seconds(self, bars: float) -> float:
        """`bars` bars in seconds (`bars * 4.0 * (60.0 / bpm)` in 4/4)."""
        if not self.ramp:
            return bars * self.bar_beats * (60.0 / self.bpm)
        return self.at(bars * self.bar_beats)

    def samples(self, bars: float) -> int:
        """`bars` bars as a length in samples (`cycle_samples`)."""
        return n_samples(self.seconds(bars), SR)

    def bar_sample(self, bar: float) -> int:
        """Where bar `bar` starts, in samples (`bar_sample`)."""
        return int(round(self.seconds(bar) * SR))

    def window(self, bar: float) -> Clock:
        """This clock seen from bar `bar` on: the same ramp, a later start."""
        if not self.ramp:
            return self
        to, over, a = self.ramp
        return self._replace(ramp=(to, over, float(a) + bar * self.bar_beats))


def arr_clock(arr) -> Clock:
    """An arrangement's clock: its tempo, its meter's bar, its ramp."""
    ramp = tuple(getattr(arr, "ramp", None) or ())
    return Clock(float(arr.bpm), bar_beats(getattr(arr, "meter", DEFAULT)), ramp)


def ramp_of(bpm: float, to_bpm, bars: int, meter=DEFAULT) -> list:
    """An arrangement's `ramp` for a section of `bars` bars ramping from
    `bpm` to `to_bpm` across them: [to, over_beats, 0.0], or [] for none."""
    if to_bpm is None or abs(float(to_bpm) - float(bpm)) < 1e-9:
        return []
    return [float(to_bpm), float(bars) * bar_beats(meter), 0.0]


def beats_at(clock: Clock, seconds):
    """`Clock.beat_at` over an array of seconds (numpy)."""
    import numpy as np

    t = np.asarray(seconds, dtype=np.float64)
    if not clock.ramp:
        return t * clock.bpm / 60.0
    to, over, a = (float(x) for x in clock.ramp)
    b0 = float(clock.bpm)
    k = (to - b0) / over
    t = t + clock._abs(a)
    end = clock._abs(over)
    head = (
        b0 * (np.exp(np.minimum(t, end) * k / 60.0) - 1.0) / k
        if abs(k) > 1e-12
        else np.minimum(t, end) * b0 / 60.0
    )
    return np.where(t >= end, over + (t - end) * to / 60.0, head) - a


def warp(values, clock: Clock, total_beats: float, at: int = 0):
    """A per-sample lane laid out linearly over a cycle (how
    `curves.lane_array` lays it: a point at x of the cycle sits x of the
    way through its samples), re-read under the clock's ramp, so a lane
    drawn in bars stays in bars: the true sample at t seconds plays the
    lane where the beat sounding at t is. `values` runs from sample `at`
    of the cycle (a chunk's) to its end. No ramp: the array itself."""
    import numpy as np

    if not clock.ramp or values is None or len(values) < 2 or total_beats <= 0:
        return values
    n = at + len(values)  # the whole cycle
    t = (at + np.arange(len(values))) / SR
    pos = beats_at(clock, t) / total_beats * max(1, n - 1) - at
    idx = np.clip(pos, 0, len(values) - 1)
    return np.interp(idx, np.arange(len(values)), values)


def mark(acc, step: int, lead: str = "") -> str:
    """A step grid cell's accent classes (`accents`, per step of a bar):
    "beat" on a beat, "beat group" on a group's head (6/8's fourth eighth,
    7/8's), nothing between."""
    lv = acc[int(step) % len(acc)] if acc else -1
    return lead + ("beat group" if lv == 1 else "beat") if lv >= 0 else ""


def grid_accents(name, steps: int):
    """`accents` for a grid, or None in 4/4 (whose grids mark every fourth
    step as they always did). An /8 meter's grid marks its groups, not
    each eighth, so 6/8 reads as 3 + 3 and 7/8 as 2 + 2 + 3."""
    if clean(name) == DEFAULT:
        return None
    acc = accents(name, steps)
    return [-1 if lv == 0 else lv for lv in acc] if parse(name).den == 8 else acc


def clicks_attr(name) -> str:
    """`beats` for a page attribute (`data-clicks`, click.js): "at:level"
    pairs in quarter beats, a space between — "0:2 1:0 2:0" for 3/4."""
    return " ".join(f"{at / 4:g}:{lvl}" for at, lvl in beats(name))


# --- a generator's 4/4 bar onto a meter's beats ---------------------------


def beat_heads(name) -> list[tuple[int, int]]:
    """Each beat of a bar as (its first sixteenth, its length): a /4
    meter's quarters, an /8 meter's groups (6/8's two dotted quarters,
    7/8's 2+2+3)."""
    m = parse(name)
    if m.den == 4:
        return [(4 * i, 4) for i in range(m.num)]
    out, at = [], 0
    for g in m.groups:
        out.append((2 * at, 2 * g))
        at += g
    return out


def beat_step(s16: int, name) -> int | None:
    """Where a generated 4/4 bar's sixteenth `s16` (0..15) goes in a bar
    of `name`: 4/4 beat j onto the meter's beat j, the place inside the
    beat scaled to its length (6/8: a straight eighth becomes the third
    eighth of a group of three), or None past the meter's last beat. The
    note generators and the chord track are written in 4/4; this is how
    their notes fall on another meter's beats (`meterops.beat_fit`)."""
    beats = beat_heads(name)
    j, r = divmod(int(s16), 4)
    if j >= len(beats):
        return None
    a, n = beats[j]
    return a + (r * n) // 4
