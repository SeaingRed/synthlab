"""What the piano roll draws behind its notes.

The roll is the note grid (`templates/grids.html`): a form of checkboxes,
one a (row, step). This module works out, for a window of rows, what the
server paints on that grid that is NOT the pattern: which rows are out of
the key (chromatic rows only), which are the key's root, the other lanes'
notes as ghosts, and the chord track's tones under each step. All of it is
classes on cells the grid already has: no input is added, nothing here is
posted, and none of it is a render input.

Pure: `context()` takes plain lists and returns the dict the macro reads.
"""

from __future__ import annotations

from ..sequencer import SCALES, midi

GHOST_TINTS = 6  # `.g0` .. `.g5` (style.css section 25)


def context(
    rows: list[str],
    total: int,
    *,
    root: str,
    scale_name: str,
    ghosts=(),
    chords=(),
    chord_track: bool = False,
) -> dict:
    """`rows`: the window's pitch names, top to bottom; `total`: its steps.
    `ghosts`: [(lane index, lane name, cells)] — another lane's `[pitch,
    step]` cells on this grid's steps. `chords`: [(lo, hi, root pitch
    class, tones)] — steps [lo, hi) and the chord's tones as semitones
    above the KEY's root (`gen/chordtrack.resolve`).

    Returns `off` (rows outside the key), `roots` (the key's root rows),
    `marks` ({(pitch, step): classes}: "ghost g<n>", "ct" a chord tone,
    "cr" the chord's root), `ghost_names` and `chord_track`."""
    try:
        key = midi(root) % 12
    except Exception:  # noqa: BLE001 - a schema-clean root always parses
        key = 9
    in_key = {(key + s) % 12 for s in SCALES.get(scale_name, SCALES["minor"])}
    pc = {r: midi(r) % 12 for r in rows}
    marks: dict[tuple[str, int], str] = {}
    for lo, hi, chord_root, tones in chords:
        tone_pcs = {(key + t) % 12 for t in tones}
        for r in rows:
            if pc[r] not in tone_pcs:
                continue
            cls = "ct cr" if pc[r] == chord_root % 12 else "ct"
            for s in range(max(0, lo), min(total, hi)):
                marks[(r, s)] = cls
    names = []
    for i, name, cells in ghosts:
        hit = False
        for pitch, step in cells:
            if pitch in pc and 0 <= int(step) < total:
                k = (pitch, int(step))
                was = marks.get(k, "")
                if "ghost" not in was:
                    marks[k] = f"{was} ghost g{i % GHOST_TINTS}".strip()
                hit = True
        if hit:
            names.append(name)
    return {
        "off": {r for r in rows if pc[r] not in in_key},
        "roots": {r for r in rows if pc[r] == key},
        "marks": marks,
        "ghost_names": names,
        "chord_track": chord_track,
    }
