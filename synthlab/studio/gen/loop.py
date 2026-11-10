"""The note generators on the WORKING LOOP's grid (Perform's piano roll):
`gen/notes.py`'s bass, chords, pad, arp and lead — until now reachable on
the Song page alone — written into the octaves the roll is showing.

Pure: an arrangement and the rows on screen in, the note lane's fields
out. `POST /fragment/gen` (`lane=notes`, routes/drums.py) holds the state,
the undo step and the answer.

**The harmony.** A generator follows, in this order: the loop's own chord
track (`Arrangement.chords`: Perform's chord lane, `POST
/fragment/chords/track`); the song's chord track when the
loop is in the song's key (a section opened in Perform, a loop being
written for the song); else one of `chordtrack.PRESETS` chosen by the
seed, a chord a bar over the loop's bars — so a roll again is another
line AND, on a loop with no chords behind it, may be another
progression. `harmony` says which; the route's toast names it.
`song_of` is the loop as the chord track's readers take a song, `lane`
the tiles Perform's chord lane draws.

**Where the notes land.** A generator writes in its register above the
bass root (`notes.KIND_REGISTER`), inside two octaves from a root of the
key (measured over every kind, 1-4 bars, three scales: the widest is 24
semitones). The lane is moved by whole octaves so that root is the
bottom row of the roll's window: what was generated is what is on
screen. The six-row view cannot show two octaves, so the route opens the
two-octave one (`ROWS_NEEDED`). A note may land on any semitone between
the window's rows: in a key that is not seven-tone the chords are its
parent's (A pentatonic minor's VI is F major), and the route's window
then widens to the chromatic rows (`StudioState.show_notes`).

**What it replaces.** What the grid shows, as every pattern tool does
(`patternops`): notes on rows the window hides, and steps past the grid's
end, are kept. A generator writes sixteenths; on a coarser or finer grid
the lane is moved as a song's layer is (`song_model._regrid`: a finer
note lands on the step it starts in, a coarser one fills the steps it
covers), with its velocities and slides.

Nobody listened to any of these on a loop: the generators' grammar is
tested in `tests/studio/test_gen_notes.py`, their fit to the loop in
`test_gen_loop.py`.
"""

from __future__ import annotations

import random
from dataclasses import replace

from ...sequencer import midi, name_of
from .. import meterops
from .. import song_model as sm
from . import chordtrack
from . import notes as nt

# what the roll's menu offers: (generator, label)
KINDS = (
    ("bass_root", "bass"),
    ("bass_rolling", "rolling bass"),
    ("chords", "chords"),
    ("pad", "pad"),
    ("arp", "arp"),
    ("lead", "lead"),
)
LABELS = dict(KINDS)
VARY = ("busier", "sparser")
ROWS_NEEDED = 2  # the roll's span that shows two octaves of the key


class Refused(ValueError):
    """The loop cannot take this generator; the message is the toast."""


def _key(arr) -> dict:
    return {"root": arr.root, "scale_name": arr.scale_name}


def song_of(arr) -> dict:
    """The loop as the chord track's readers (`chordtrack`) take a song:
    its key, and its own chords when it has them."""
    own = getattr(arr, "chords", None)
    return {**_key(arr), **({"chords": own} if own and own.get("slots") else {})}


def lane(arr) -> list[dict]:
    """Perform's chord lane: a tile a chord over the loop's steps, the
    progression tiled from bar 1 (`chordtrack.resolve`) — [{"lo", "span"
    (steps), "index" (the slot's place in the track), "first" (inside the
    progression's first play), "roman", "name", "deg", "q", "inv", "beat"
    (where it starts, beats from the progression's start), "semis" (its
    tones above the key's root, root position)}]. Empty for a loop with no
    chords of its own."""
    song = song_of(arr)
    if "chords" not in song:
        return []
    track = song["chords"]
    order = sorted(range(len(track["slots"])), key=lambda i: track["slots"][i]["at"])
    index = {id(track["slots"][i]): k for k, i in enumerate(order)}
    spb, bars = int(arr.steps_per_bar), max(1, int(arr.bars))
    period = int(track["bars"]) * spb
    out = []
    for c in chordtrack.resolve(song, None, bars, spb):
        s = c["slot"]
        out.append(
            {
                "lo": c["lo"],
                "span": c["hi"] - c["lo"],
                "index": index[id(s)],
                "first": c["lo"] < period,
                "roman": chordtrack.roman(song, s),
                "name": chordtrack.name(song, s),
                "deg": s["deg"],
                "q": s.get("q", "triad"),
                "inv": s.get("inv", 0),
                "beat": s["at"],
                "semis": chordtrack.semis(song, s),
            }
        )
    return out


def harmony(arr, song: dict | None, seed: int) -> tuple[dict, str]:
    """(the chord track the loop's generators follow, where it came from)."""
    key = _key(arr)
    own = song_of(arr).get("chords")
    if own:  # the loop's own (Perform's chord lane) leads
        return own, "the loop's chords"
    held = (song or {}).get("chords")
    same_key = bool(song) and all(song.get(k) == v for k, v in key.items())
    if same_key and held and held.get("slots"):
        return held, "the song's chords"
    pick = random.Random(int(seed)).choice(chordtrack.PRESETS)
    bars = max(1, int(arr.bars))
    # a chord a bar; a loop shorter than the progression plays its opening
    track = chordtrack.from_degrees(pick[:bars] if bars < len(pick) else pick, bars)
    said = "–".join(chordtrack.roman(key, s) for s in track["slots"])
    return track, said


def _kick(arr) -> tuple[int, ...]:
    """The sixteenths of a bar the loop's kick lands on (its first bar)."""
    per = max(1, int(arr.drum_steps))
    got = sorted(
        {
            int(step) * 16 // per
            for voice, step in arr.drums
            if voice == "kick" and 0 <= int(step) < per
        }
    )
    return tuple(got) or (0, 4, 8, 12)


def _ctx(arr, song, kind: str, seed: int) -> tuple[nt.Ctx, str]:
    track, said = harmony(arr, song, seed)
    ctx = nt.context(
        {**_key(arr), "chords": track},
        None,
        bars=max(1, int(arr.bars)),
        kick=_kick(arr),
        register=nt.KIND_REGISTER.get(kind, "lead"),
        seed=seed,
        role=f"loop:{kind}",
    )
    return ctx, said


def _window_low(rows: list[str], root_pc: int) -> int:
    """The lowest row that is a root of the key (the window's bottom)."""
    ms = sorted(midi(r) for r in rows)
    return next((m for m in ms if m % 12 == root_pc), ms[0])


def _between(rows: list[str]) -> list[str]:
    """Every semitone from the window's lowest row to its highest."""
    ms = [midi(r) for r in rows]
    return [name_of(m) for m in range(min(ms), max(ms) + 1)]


def _onto(
    lane: dict, arr, rows: list[str], shift: int, land: list[str] | None = None
) -> tuple[dict, int]:
    """A generated sixteenth lane as the loop's note-lane fields: moved
    `shift` semitones, onto the loop's step grid, over what the grid hides.
    A note lands on `land` (default: the rows) — what the rows hide stays
    as it was. Also how many generated notes had no row to land on."""
    spb, bars = int(arr.steps_per_bar), max(1, int(arr.bars))
    total = bars * spb
    # a loop in another meter: the 4/4 lane on its beats first
    # (`meterops.beat_fit`), at that meter's sixteenths
    lane = meterops.beat_fit(nt.transpose(lane, shift), arr.meter)
    src = int(lane.get("steps_per_bar", nt.SPB))
    shown, fits = set(rows), set(land or rows)
    cells = sm._regrid(lane["cells"], bars, src, spb)
    cuts = sm._regrid(lane.get("cuts") or [], bars, src, spb)
    lost = len({c[0] for c in cells if c[0] not in fits})
    cells = [c for c in cells if c[0] in fits]
    on = {(c[0], c[1]) for c in cells}

    def moved(step) -> int | None:
        s = int(step)
        if not 0 <= s < bars * src:
            return None
        return s * spb // src if spb <= src else s * (spb // src)

    vel: dict[str, float] = {}
    for k, v in (lane.get("velocities") or {}).items():
        if (x := moved(k)) is not None:
            vel.setdefault(str(x), v)
    slides = sorted(
        {x for s in lane.get("slides") or [] if (x := moved(s)) is not None}
    )
    hidden = [
        list(c)
        for c in arr.cells
        if (c[0] not in shown or not 0 <= int(c[1]) < total)
        and (c[0], int(c[1])) not in on
    ]
    hid = {(c[0], int(c[1])) for c in hidden}
    return (
        {
            "cells": sorted(cells + hidden, key=lambda c: (c[0], c[1])),
            "cuts": sorted(
                [c for c in cuts if (c[0], c[1]) in on]
                + [list(c) for c in arr.cuts if (c[0], int(c[1])) in hid],
                key=lambda c: (c[0], c[1]),
            ),
            "velocities": vel,
            "slides": slides,
        },
        lost,
    )


def write(arr, rows: list[str], kind: str, seed: int, song=None) -> tuple[dict, str]:
    """`kind` written over what the grid shows: (the note lane's fields for
    `replace(arr, **...)`, what to say)."""
    if kind not in LABELS:
        raise Refused("No such generator.")
    if arr.arp_mode != "off":
        raise Refused("The arp plays every step: switch it off to hear a written part.")
    ctx, said = _ctx(arr, song, kind, seed)
    lane = nt.generate(kind, ctx)
    if not lane["cells"]:
        raise Refused("That generator wrote nothing here.")
    shift = _window_low(rows, ctx.root % 12) - ctx.low
    fields, lost = _onto(lane, arr, rows, shift, land=_between(rows))
    note = f"{LABELS[kind]} over {said} · seed {seed}"
    if lost:
        note += f" ({lost} row{'s' if lost != 1 else ''} above the window left out)"
    return fields, note


def vary(arr, rows: list[str], kind: str, amount: float, seed: int, song=None):
    """Busier or sparser, on the notes the grid shows: (fields, note)."""
    if kind not in VARY:
        raise Refused("No such generator.")
    if int(arr.steps_per_bar) != nt.SPB or arr.meter != "4/4":
        raise Refused(f"{kind.capitalize()} works on a 16-step grid in 4/4.")
    shown = set(rows)
    total = max(1, int(arr.bars)) * nt.SPB
    mine = [list(c) for c in arr.cells if c[0] in shown and 0 <= int(c[1]) < total]
    if not mine:
        raise Refused(f"There are no notes to make {kind} yet.")
    on = {(c[0], int(c[1])) for c in mine}
    lane = {
        "bars": max(1, int(arr.bars)),
        "steps_per_bar": nt.SPB,
        "cells": mine,
        "cuts": [list(c) for c in arr.cuts if (c[0], int(c[1])) in on],
        "velocities": dict(arr.velocities),
        "slides": list(arr.slides),
    }
    ctx, _said = _ctx(arr, song, "lead", seed)
    # a new note is a chord tone inside the window, near the notes there
    ctx = replace(ctx, low=_window_low(rows, ctx.root % 12))
    out = (nt.busier if kind == "busier" else nt.sparser)(ctx, lane, amount)
    if kind == "busier":  # a new note sits where the grid can show it
        out = {**out, "cells": [c for c in out["cells"] if c[0] in shown]}
    fields, _lost = _onto(out, arr, rows, 0)
    return fields, f"{kind} · seed {seed}"
