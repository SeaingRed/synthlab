"""Chord mode + the keyboard "code line": scale-correct chords for the
Synths keyboard, and a tiny parser for typed sequences.

    scale_chords("A1", "minor", "triad")  -> 7 degree chords (Am, Bdim, C ...)
    chord_for_midi(61, "A1", "minor", "7th") -> the in-scale 7th on the
        nearest scale tone to C#4
    parse_code("C4 E4 G4", ...)   -> [[60], [64], [67]]
    parse_code("Am7 F C G", ...)  -> four chords at octave 3
    parse_code("I V vi IV", ...)  -> scale-degree chords

Everything is MIDI ints; names are for display.
"""

from __future__ import annotations

import re

from ..sequencer import NOTE_NAMES, SCALES, midi, name_of, scale
from .gen.chordtrack import numeral

VOICINGS: dict[str, tuple[int, ...] | None] = {
    "triad": (0, 2, 4),
    "7th": (0, 2, 4, 6),
    "9th": (0, 2, 4, 6, 8),
    "power": None,  # root + fifth
    "octave": None,  # root + octave
}

# Chord qualities: (display suffix, semitones above the root, category).
# Categories are the chord-chart sections (library rows + key-row
# surfaces); the parser matches the suffixes (normalised) as base
# qualities, then applies modifiers (add9, #11, b5, no3 ...) on top.
QUALITIES: list[tuple[str, tuple[int, ...], str]] = [
    ("", (0, 4, 7), "major"),
    ("5", (0, 7), "major"),
    ("m", (0, 3, 7), "minor"),
    ("7", (0, 4, 7, 10), "seventh"),
    ("maj7", (0, 4, 7, 11), "seventh"),
    ("m7", (0, 3, 7, 10), "seventh"),
    ("mMaj7", (0, 3, 7, 11), "seventh"),
    ("9", (0, 4, 7, 10, 14), "extended"),
    ("maj9", (0, 4, 7, 11, 14), "extended"),
    ("m9", (0, 3, 7, 10, 14), "extended"),
    ("11", (0, 4, 7, 10, 14, 17), "extended"),
    ("m11", (0, 3, 7, 10, 14, 17), "extended"),
    ("13", (0, 4, 7, 10, 14, 21), "extended"),
    ("maj13", (0, 4, 7, 11, 14, 21), "extended"),
    ("m13", (0, 3, 7, 10, 14, 21), "extended"),
    ("sus2", (0, 2, 7), "sus"),
    ("sus4", (0, 5, 7), "sus"),
    ("7sus4", (0, 5, 7, 10), "sus"),
    ("9sus4", (0, 5, 7, 10, 14), "sus"),
    ("dim", (0, 3, 6), "dim"),
    ("dim7", (0, 3, 6, 9), "dim"),
    ("m7b5", (0, 3, 6, 10), "dim"),
    ("aug", (0, 4, 8), "aug"),
    ("aug7", (0, 4, 8, 10), "aug"),
    ("maj7#5", (0, 4, 8, 11), "aug"),
    ("add9", (0, 4, 7, 14), "add"),
    ("madd9", (0, 3, 7, 14), "add"),
    ("add11", (0, 4, 7, 17), "add"),
    ("6", (0, 4, 7, 9), "add"),
    ("m6", (0, 3, 7, 9), "add"),
    ("6/9", (0, 4, 7, 9, 14), "add"),
    ("m6/9", (0, 3, 7, 9, 14), "add"),
    ("7b5", (0, 4, 6, 10), "altered"),
    ("7#5", (0, 4, 8, 10), "altered"),
    ("7b9", (0, 4, 7, 10, 13), "altered"),
    ("7#9", (0, 4, 7, 10, 15), "altered"),
    ("7#11", (0, 4, 7, 10, 18), "altered"),
    ("9b5", (0, 4, 6, 10, 14), "altered"),
    ("9#5", (0, 4, 8, 10, 14), "altered"),
    ("9#11", (0, 4, 7, 10, 14, 18), "altered"),
    ("maj7#11", (0, 4, 7, 11, 18), "altered"),
    ("7alt", (0, 4, 6, 10, 13, 15), "altered"),
]
CATEGORIES = [
    ("major", "Major"),
    ("minor", "Minor"),
    ("seventh", "Seventh"),
    ("extended", "Extended"),
    ("sus", "Sus"),
    ("dim", "Diminished"),
    ("aug", "Augmented"),
    ("add", "Add"),
    ("altered", "Altered"),
]


def _norm(suffix: str) -> str:
    """Canonical spelling of a quality suffix: C△7 / CM7 / Cmaj7 / CMAJ7 ->
    'maj7'; Cmin7 / C-7 -> 'm7'; Gdom7 -> '7'; A° -> 'dim'; Aø -> 'm7b5';
    F(#5) -> '#5'; C6/9 -> '69'; 'add(m2)' -> 'addb2'. Case matters only
    for the lone 'M' (major) vs 'm' (minor)."""
    s = suffix.replace(" ", "")
    s = s.replace("△", "maj").replace("Δ", "maj").replace("°", "dim")
    s = s.replace("ø7", "m7b5").replace("ø", "m7b5").replace("+", "aug")
    s = re.sub(r"(?i)maj", "maj", s)
    s = re.sub(r"(?i)min", "m", s)
    s = re.sub(r"(?i)dom", "", s)
    s = re.sub(r"M(?!aj)", "maj", s)
    s = s.lower().replace("-", "m", 1) if s.startswith("-") else s.lower()
    s = re.sub(r"add\(?m(\d+)\)?", r"addb\1", s)
    s = s.replace("6/9", "69").replace("(", "").replace(")", "")
    return s


_BASES: dict[str, tuple[int, ...]] = {
    **{_norm(suffix): iv for suffix, iv, _ in QUALITIES},
    "maj": (0, 4, 7),
}
_DEGREE = {2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 10, 9: 14, 10: 16, 11: 17, 13: 21}
_MOD_RE = re.compile(r"(add|no)?([#b]?)(\d+)")
_ROOT_RE = re.compile(r"^([A-Ga-g][#b]?)(.*)$")
# "C4".."C6" are notes; "C7", "C9", "C-7" are chords (octaves 7+ aren't
# playable here anyway)
_NOTE_RE = re.compile(r"^([A-Ga-g][#b]?)(-1|[0-6])$")
_BASS_RE = re.compile(r"^[A-Ga-g][#b]?$")


def _quality(suffix: str) -> list[int]:
    """Normalised suffix -> intervals: longest known base, then modifiers."""
    s = _norm(suffix)
    base = max((k for k in _BASES if s.startswith(k)), key=len)
    tones = list(_BASES[base])
    rest = s[len(base) :]
    pos = 0
    while pos < len(rest):
        m = _MOD_RE.match(rest, pos)
        if not m:
            raise CodeError(f"unknown chord quality {suffix!r}")
        pos = m.end()
        # a degree is one or two digits (2..13). Checked before int(): a run
        # of thousands is past what int() converts, and its ValueError is
        # no CodeError (the routes answered 500, not a toast)
        if len(m.group(3)) > 2:
            raise CodeError(f"unknown chord degree in {suffix[:24]!r}")
        kind, acc, deg = m.group(1), m.group(2), int(m.group(3))
        if deg not in _DEGREE:
            raise CodeError(f"unknown chord degree {deg} in {suffix!r}")
        natural = _DEGREE[deg]
        if kind == "no":
            drop = {natural, natural - 1, natural + 1} if deg in (3, 5) else {natural}
            if deg == 3:
                drop = {3, 4}
            tones = [t for t in tones if t not in drop]
            continue
        tone = natural + {"#": 1, "b": -1, "": 0}[acc]
        # an accidental on a tone the chord already has alters it (F(#5) is
        # augmented); anything else is an extra tone (maj7#11 adds the 11)
        if acc and kind != "add" and natural in tones:
            tones = [tone if t == natural else t for t in tones]
        elif tone not in tones:
            tones.append(tone)
    return sorted(set(tones))


class CodeError(ValueError):
    pass


def _scale_midis(root: str, scale_name: str) -> list[int]:
    """Three octaves of the scale from the root, so stacked-third voicings
    above the top degree stay inside the list."""
    if scale_name not in SCALES:
        raise CodeError(f"unknown scale {scale_name!r}")
    return scale(root, scale_name, octaves=3)


def _quality_name(intervals: list[int]) -> str:
    """Display name suffix from the intervals above the root."""
    third = 4 if 4 in intervals else 3 if 3 in intervals else None
    fifth = (
        7 if 7 in intervals else 6 if 6 in intervals else 8 if 8 in intervals else None
    )
    if third == 4:
        q = "aug" if fifth == 8 else ""
    elif third == 3:
        q = "dim" if fifth == 6 else "m"
    elif 5 in intervals:
        q = "sus4"
    elif 2 in intervals:
        q = "sus2"
    else:
        q = "5"
    if 11 in intervals:
        q += "maj7" if q == "" else "7"
    elif 10 in intervals:
        q += "7"
    if 14 in intervals:
        q += "9" if q.endswith("7") else "add9"
    return q


# chord tones as semitone targets above the root; each resolves to the
# nearest scale tone within a semitone (so a 5-note scale still yields
# real triads: A minor pentatonic -> Am, C, D5, Em, G)
_TARGETS: dict[str, tuple[tuple[int, ...], ...]] = {
    "triad": ((3, 4), (7,)),
    "7th": ((3, 4), (7,), (10, 11)),
    "9th": ((3, 4), (7,), (10, 11), (14,)),
}


def _voice(degrees: list[int], i: int, voicing: str) -> list[int]:
    root = degrees[i]
    if voicing == "power":
        return [root, root + 7]
    if voicing == "octave":
        return [root, root + 12]
    targets = _TARGETS.get(voicing)
    if targets is None:
        raise CodeError(f"unknown voicing {voicing!r}")
    tones = set(degrees)
    notes = [root]
    for options in targets:
        hit = next((root + t for t in options if root + t in tones), None)
        if hit is None and len(options) == 1:  # fifth/ninth: allow a semitone off
            hit = next(
                (
                    root + t + d
                    for d in (-1, 1)
                    for t in options
                    if root + t + d in tones
                ),
                None,
            )
        if hit is not None:
            notes.append(hit)
    return notes


def scale_chords(root: str, scale_name: str, voicing: str = "triad") -> list[dict]:
    """One chord per scale degree (first octave), rooted at the grid's root."""
    degrees = _scale_midis(root, scale_name)
    per_octave = len(SCALES[scale_name])
    out = []
    for i in range(per_octave):
        notes = _voice(degrees, i, voicing)
        rel = [n - notes[0] for n in notes]
        out.append(
            {
                "degree": i + 1,
                # outside a seven-tone key, the diatonic parent's number
                # (C in A pentatonic minor is "iii": `chordtrack.numeral`);
                # the code line reads these too ("iii" plays it)
                "roman": numeral(SCALES[scale_name], notes[0] - degrees[0]),
                "name": NOTE_NAMES[notes[0] % 12] + _quality_name(rel),
                "notes": notes,
            }
        )
    return out


def scale_notes(root: str, scale_name: str, octaves: int = 2) -> list[dict]:
    """Every tone of the key across `octaves` octaves (plus the top root):
    the note pads under the keyboard. `degree` is 1-based within the
    octave so the tonic pads can be marked."""
    if scale_name not in SCALES:
        raise CodeError(f"unknown scale {scale_name!r}")
    per_octave = len(SCALES[scale_name])
    out = []
    for i, m in enumerate(scale(root, scale_name, octaves=octaves)):
        out.append({"midi": m, "name": name_of(m), "degree": i % per_octave + 1})
    return out


# semitones above the fundamental for partials 1..12 (equal-tempered nearest)
_PARTIALS = (0, 12, 19, 24, 28, 31, 34, 36, 38, 40, 41, 43)


def harmonic_notes(root: str, octave: int = 2) -> list[dict]:
    """The harmonic series of the key's root, fundamental at `octave`:
    the "harmonics" pad row / key row."""
    base = midi(root.rstrip("-0123456789") + str(octave))
    return [
        {"midi": base + iv, "name": name_of(base + iv), "partial": i + 1}
        for i, iv in enumerate(_PARTIALS)
    ]


def chord_library(root: str, scale_name: str, octave: int = 3) -> list[dict]:
    """The chord-chart poster: every category -> its qualities -> twelve
    roots as pads. Chords sit at `octave` like typed symbols do; `in_key`
    marks the ones whose tones all belong to the current scale."""
    if scale_name not in SCALES:
        raise CodeError(f"unknown scale {scale_name!r}")
    tones = {m % 12 for m in scale(root, scale_name, octaves=1)}
    base = midi(f"C{octave}")
    cats = []
    for key, label in CATEGORIES:
        qualities = []
        for suffix, intervals, cat in QUALITIES:
            if cat != key:
                continue
            pads = [
                {
                    "name": NOTE_NAMES[pc] + suffix,
                    "notes": [base + pc + i for i in intervals],
                    "in_key": all((pc + i) % 12 in tones for i in intervals),
                }
                for pc in range(12)
            ]
            qualities.append({"label": suffix or "maj", "pads": pads})
        cats.append({"key": key, "label": label, "qualities": qualities})
    return cats


def chord_for_midi(
    m: int, root: str, scale_name: str, voicing: str = "triad"
) -> list[int]:
    """The in-scale chord whose root is the scale tone nearest to `m`
    (transposed so the root sits exactly at that tone's octave)."""
    degrees = _scale_midis(root, scale_name)
    per_octave = len(SCALES[scale_name])
    pcs = [d % 12 for d in degrees[:per_octave]]
    # nearest pitch class (prefer the lower neighbour on a tie)
    best = min(
        range(per_octave), key=lambda i: (min((m - pcs[i]) % 12, (pcs[i] - m) % 12), i)
    )
    shape = [n - degrees[best] for n in _voice(degrees, best, voicing)]
    base = m - ((m - pcs[best]) % 12)
    if (pcs[best] - m) % 12 < (m - pcs[best]) % 12:
        base = m + ((pcs[best] - m) % 12)
    return [base + s for s in shape]


def _symbol(token: str) -> list[int] | None:
    """'C4' -> one note; 'Am7', 'E/G#', 'C6/9', 'Abmaj7(#11)' -> a chord.
    A slash bass (E/G#, E7/B, C/D, C/C) goes in below the chord: an
    inversion when it's a chord tone, a doubled root, or an added bass."""
    mt = _NOTE_RE.match(token)
    if mt:
        return [midi(mt.group(1)[0].upper() + mt.group(1)[1:] + mt.group(2))]
    mc = _ROOT_RE.match(token)
    if not mc:
        return None
    suffix, bass = mc.group(2).replace("6/9", "69"), None
    if "/" in suffix:
        suffix, bass = suffix.split("/", 1)
        if not _BASS_RE.match(bass):
            raise CodeError(f"unknown bass note in {token!r}")
    cap = lambda n: n[0].upper() + n[1:]  # noqa: E731 — keep the 'b' flat
    base = midi(cap(mc.group(1)) + "3")  # chord symbols sit at octave 3
    notes = [base + i for i in _quality(suffix)]
    if bass:
        pc = midi(cap(bass) + "0") % 12
        b = notes[0] - 1
        while b % 12 != pc:
            b -= 1
        notes.insert(0, b)
    return notes


def parse_code(
    text: str, root: str, scale_name: str, voicing: str = "triad"
) -> list[list[int]]:
    """A whitespace-separated sequence of notes (C4), chord symbols (Am7)
    and roman numerals (I, vi) -> list of MIDI chords, one per token."""
    tokens: list[str] = []
    for tok in text.replace(",", " ").replace("|", " ").split():
        # "Bm11 add(m2)" / "F (#5)": a modifier-only word belongs to the
        # chord before it
        if tokens and re.match(r"^(add|no|\(|[#b]\d|/)", tok, re.I):
            tokens[-1] += tok
        else:
            tokens.append(tok)
    if len(tokens) > 64:
        raise CodeError("sequence too long (max 64)")
    chords = scale_chords(root, scale_name, voicing) if tokens else []
    by_roman = {c["roman"]: c["notes"] for c in chords}
    out: list[list[int]] = []
    for tok in tokens:
        low = tok.lower()
        if low in by_roman:
            out.append(list(by_roman[low]))
            continue
        if low == "~":
            out.append([])
            continue
        notes = _symbol(tok)
        if notes is None:
            raise CodeError(f"can't read {tok!r} — try C4, Am7 or IV")
        out.append(notes)
    return out
