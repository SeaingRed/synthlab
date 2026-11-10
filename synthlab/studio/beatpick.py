"""Synths' Beat ▾: which beat the phrase plays over, picked from the bench.

The beat it picks IS the working loop's beat — the drum lane Perform's
rack and the Drums page show — so a pick here changes it there too
(`docs/synths.md`, "vs beat"). Nothing here is a second beat: each choice
is the move another page already makes on the loop's drum lane, through
the same state methods —

- `var:<A-D>`  a variation of the loop's beat that holds hits
  (`DrumBank.beat_variation`, as `POST /fragment/drums/var`);
- `bank:<n>`   a drum bank slot (`DrumBank.bank_load`, as
  `POST /fragment/drums/bank/load/{slot}`);
- `style:<name>:<seed>` a new beat in a `beatgen` style (`roll_style`, the
  Randomise of `POST /fragment/drums/randomise`). The seed is drawn when
  the menu is drawn, so the same pick posted twice (the sound bar's form
  queues its posts) writes the same beat;
- `none`       the phrase alone: "over the beat" off, the beat untouched.

and a kit under the beat (`kit:<name>` on its own select: `swap_kit_under`,
the Drum page's "Swap kit, keep beat"). Every pick but `none` turns "over
the beat" on. Pure over a `StudioState`; the route (`POST
/fragment/beat/pick`, `routes/patch.py`) owns the undo step and the
answer, and the sound bar comes back by its region's watch (`watch`).
"""

from __future__ import annotations

import random
from dataclasses import replace

from . import audition, beatgen, drumops, limits
from .drumfactory import DRUM_KITS
from .render import loop_refusal
from .state import StudioState, drum_grid
from .words import plural


def kit_of(s: StudioState) -> str | None:
    """The genre kit the beat's voices come from, as far as the session
    knows: the kit whose style the beat's Randomise style is (a kit load
    and a kit swap both set it) — else None."""
    return next((k for k, v in DRUM_KITS.items() if v["style"] == s.beat_style), None)


def _has_hits(lane: dict | None) -> bool:
    return bool(lane and lane.get("drums"))


def variations(s: StudioState) -> list[tuple[str, int]]:
    """(letter, bars) of every variation that holds hits — the one on the
    grid included."""
    arr = s.arrangement
    out = []
    for letter in s.VARIANTS:
        if letter == s.beat_var:
            if arr.drums:
                out.append((letter, int(arr.drum_bars)))
        elif _has_hits(lane := s.beat_vars.get(letter)):
            out.append((letter, int(lane["drum_bars"])))
    return out


def beat_name(s: StudioState) -> str:
    """What the beat is called: its bank slot's name, else its kit's, else
    its style's — and its letter when it has other variations."""
    arr = s.arrangement
    if not arr.drums:
        return "no beat"
    slot = s.drum_bank[s.active_slot] if s.active_slot is not None else None
    style = beatgen.style_name(s.beat_style) if s.beat_style else ""
    name = slot["name"] if slot else kit_of(s) or style or "your beat"
    if len(variations(s)) > 1:
        name = f"{name} · {s.beat_var}"
    return name


def label(s: StudioState) -> str:
    """▶ beat's cue: "the beat · Filter House · 2 bars"."""
    return f"the beat · {beat_name(s)} · {plural(int(s.arrangement.drum_bars), 'bar')}"


def options(s: StudioState) -> list[tuple[str, list]]:
    """The menu: (group, [(value, text, title)]). The new beats' seeds
    follow from the beat's own (`beat_seed`), so the same state draws the
    same menu (a region's HTML is a function of its watch) and a beat
    rolled from it draws fresh ones."""
    rng = random.Random(s.beat_seed * 7919 + 17)
    groups: list[tuple[str, list]] = []
    var = [
        (
            f"var:{letter}",
            f"{letter} · {plural(bars, 'bar')}",
            f"variation {letter} of the loop's beat",
        )
        for letter, bars in variations(s)
    ]
    if var:
        groups.append(("the loop's variations", var))
    bank = [
        (f"bank:{i}", f"{i + 1} · {p['name']}", f"bank slot {i + 1}")
        for i, p in enumerate(s.drum_bank)
        if p
    ]
    if bank:
        groups.append(("the bank", bank))
    # a new beat, by family and display name (the value keeps the slug);
    # the seeds are drawn in STYLES order, whatever order the menu shows
    seeds = {st: rng.randrange(limits.SEED_MODULO) for st in beatgen.STYLES}
    for family, styles in beatgen.style_groups():
        groups.append(
            (
                f"new: {family.lower()}",
                [
                    (
                        f"style:{st}:{seeds[st]}",
                        beatgen.style_name(st),
                        f"a new {beatgen.style_name(st)} beat (Randomise on Kit)",
                    )
                    for st in styles
                ],
            )
        )
    groups.append(
        ("off", [("none", "none", "the phrase on its own: the beat stays as it is")])
    )
    return groups


def context(s: StudioState) -> dict:
    """What the sound bar draws: the menu, the kit, ▶ beat's cue."""
    arr = s.arrangement
    return {
        "name": beat_name(s),
        "bars": int(arr.drum_bars),
        "label": label(s),
        "empty": not arr.drums,
        "options": options(s),
        "kit": kit_of(s),
        "kits": list(DRUM_KITS),
    }


def watch(s: StudioState) -> tuple:
    """Everything `context` draws, cheaply: the sound bar's watch holds it."""
    arr = s.arrangement
    return (
        label(s),
        tuple(variations(s)),
        tuple(p["name"] if p else None for p in s.drum_bank),
        s.beat_seed,
        kit_of(s),
        bool(arr.drums),
    )


def roll_style(s: StudioState, style: str, seed: int) -> None:
    """A new beat in `style` on the loop's grid: the style's cells, then its
    feel (`beatgen.dress`: ghosts, a chance a hit, the rows' own swing — or
    none, for a style without one) and its swing. The Drums page's
    Randomise (`POST /fragment/drums/randomise`) and Beat ▾'s "new:".
    A style written in another meter (`beatgen.STYLE_METER`: a waltz, a
    6/8) takes the loop there first (`set_meter`: every grid fitted), so a
    waltz is three beats a bar, not a 4/4 bar with one left empty; a 4/4
    style on a loop in another meter is fitted to its bar."""
    want = beatgen.STYLE_METER.get(style)
    if want and want != s.arrangement.meter:
        s.set_meter(want)
    arr = s.arrangement
    feel = (
        {"drum_swing": beatgen.STYLE_SWING[style]}
        if style in beatgen.STYLE_SWING
        else {}
    )
    cells = beatgen.generate(style, seed, *drum_grid(arr), meter=arr.meter)
    dressed = beatgen.dress(
        style, seed, cells, *drum_grid(arr), vel=arr.drum_vel, meter=arr.meter
    )
    s.arrangement = replace(
        arr,
        drums=dressed["drums"],
        drum_hits=dressed["hits"],
        drum_row_swing=dressed["swing"],
        **feel,
    )
    s.beat_style, s.beat_seed = style, seed


def swap_kit_under(s: StudioState, kit_name: str) -> bool:
    """The kit's voices under the beat on the grid (`drumops.swap_kit`):
    the pattern, its swing, velocities, per-hit data and the rows' samples
    stay. The Drums page's "Swap kit, keep beat" and Beat ▾'s kit. False
    for an unknown kit."""
    kit = DRUM_KITS.get(kit_name)
    if kit is None:
        return False
    swap = drumops.swap_kit(
        drumops.loop_lane(s.arrangement), kit, s.arrangement.drum_samples
    )
    params = dict(s.arrangement.drum_params) | swap["params"]
    s.arrangement = replace(s.arrangement, drum_params=params)
    s.beat_style, s.beat_seed = kit["style"], kit["seed"]
    return True


# what a pick may change, put back whole when the beat is refused
_BEAT_STATE = (
    "arrangement",
    "beat_var",
    "beat_vars",
    "active_slot",
    "beat_style",
    "beat_seed",
)


def apply(s: StudioState, pick: str) -> str:
    """Put the picked beat on the loop (see the module docstring) and turn
    "over the beat" on; "" when done, else why not (nothing changed)."""
    kind, _, rest = pick.partition(":")
    if kind == "none" and not rest:
        s.vs_beat = False
        if s.audition == audition.NO_PHRASE:  # no beat and no phrase is silence
            s.audition = audition.DEFAULT
        return ""
    was = {k: getattr(s, k) for k in _BEAT_STATE}
    was["beat_vars"] = dict(was["beat_vars"])
    if kind == "var":
        if rest != s.beat_var:
            if not _has_hits(s.beat_vars.get(rest)):
                return "That variation holds no beat."
            s.beat_variation(rest)
            s.active_slot = None
    elif kind == "bank":
        try:
            slot = int(rest)
        except ValueError:
            return "No such bank slot."
        if not s.bank_load(slot):
            return "That bank slot is empty."
    elif kind == "style":
        style, _, raw = rest.partition(":")
        if style not in beatgen.STYLES:
            return "No such beat style."
        try:
            seed = int(raw) % limits.SEED_MODULO
        except ValueError:
            seed = random.randrange(limits.SEED_MODULO)
        roll_style(s, style, seed)
        s.active_slot = None
    else:
        return "No such beat."
    refusal = loop_refusal(was["arrangement"], s.arrangement)
    if refusal:  # a longer beat than this tempo can loop: as it was
        for k, v in was.items():
            setattr(s, k, v)
        return refusal
    s.vs_beat = True
    return ""
