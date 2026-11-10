"""The chord track: a song's harmony as data, and the one reader of it.

    song["chords"]    = {"bars": 4, "slots": [{"at", "deg", "q", "inv", "len"}]}
    section["chords"] = the same, overriding the song's for that section

A slot is a chord by DEGREE of the song's key (1..7), so a key change
carries the progression; `at` and `len` are beats from the progression's
start and `bars` is the length that repeats. The track renders nothing: it
is guidance — the generators (`gen/notes.py`), the strip on the Song page
and the checklist read it through `resolve`, and notes a generator wrote
are plain cells that do not follow a later chord edit until they are
rolled again.

Every key works: a chord is built by stacking the scale's own tones, and
a key that is not seven-tone (a pentatonic, the blues scale, whole tone)
counts its degrees and stacks its chords in its diatonic PARENT
(`chord_scale`: major when the key has a major third, else natural minor),
so a progression written for seven tones
plays as written — (1, 6, 3, 7) in A pentatonic minor is Am F C G, A
natural minor's i VI III VII, not a degree wrapped round to the first.

Pure functions over the plain song dict; no state, no I/O.
"""

from __future__ import annotations

from ...sequencer import NOTE_NAMES, SCALES, midi
from .. import limits

# which scale tones above the chord's root a quality stacks (in scale
# degrees: 2 is "the third", 4 "the fifth")
TONES: dict[str, tuple[int, ...]] = {
    "triad": (0, 2, 4),
    "7th": (0, 2, 4, 6),
    "9th": (0, 2, 4, 6, 8),
    "sus2": (0, 1, 4),
    "sus4": (0, 3, 4),
    "power": (0, 4),
}
QUALITY_LABELS = {
    "triad": "triad",
    "7th": "7th",
    "9th": "9th",
    "sus2": "sus2",
    "sus4": "sus4",
    "power": "5th only",
}
_ROMAN = ("i", "ii", "iii", "iv", "v", "vi", "vii")

# Eight progressions by degree, good in any seven-tone key — and in any
# other, read in its parent (`chord_scale`): what the strip's menu lists
# beside the style's own. Each plays one chord a bar.
PRESETS: tuple[tuple[int, ...], ...] = (
    (1, 6, 3, 7),
    (1, 7, 6, 7),
    (6, 7, 1, 1),
    (1, 4, 6, 5),
    (1, 5, 6, 4),
    (4, 6, 1, 7),
    (2, 5, 1, 1),
    (1, 1, 6, 7),
)


def scale_of(song: dict) -> list[int]:
    """The key's tones, semitones above its root."""
    return list(SCALES.get(song.get("scale_name"), SCALES["minor"]))


_MAJOR = (0, 2, 4, 5, 7, 9, 11)
_MINOR = (0, 2, 3, 5, 7, 8, 10)


def parent(scale: list[int]) -> list[int]:
    """The seven tones a key's chords and numerals are counted in: a
    seven-tone key's own; any other key's diatonic parent — major when it
    has a major third, else natural minor."""
    if len(scale) == 7:
        return list(scale)
    return list(_MAJOR if 4 in scale else _MINOR)


def chord_scale(song: dict) -> list[int]:
    """THE rule for a chord by degree: the scale a slot's
    degree counts in and its chord stacks — `parent(scale_of(song))`. Every
    reader of a slot goes through it (`semis`, `roman`, `from_cells`, so
    the lane, the strip, the pads that follow the loop's chords and the
    generators), so a preset written for seven tones never wraps."""
    return parent(scale_of(song))


def key_pc(song: dict) -> int:
    """The key root's pitch class (0 = C)."""
    try:
        return midi(song["root"]) % 12
    except Exception:  # noqa: BLE001 - a schema-clean root always parses
        return 9


def _tone(scale: list[int], index: int) -> int:
    """Scale tone `index` (0-based, any size), in semitones above the key."""
    n = len(scale)
    return scale[index % n] + 12 * (index // n)


def semis(song: dict, slot: dict) -> list[int]:
    """A slot's chord tones in root position, semitones above the KEY's
    root, ascending (the chord's own root first, 0..11)."""
    scale = chord_scale(song)
    at = (int(slot["deg"]) - 1) % len(scale)
    return [_tone(scale, at + k) for k in TONES.get(slot.get("q"), TONES["triad"])]


def pitch_classes(song: dict, slot: dict) -> list[int]:
    """A slot's chord tones as pitch classes, the root first."""
    key = key_pc(song)
    return [(key + s) % 12 for s in semis(song, slot)]


def numeral(scale: list[int], semis: int) -> str:
    """The lower-case numeral of a chord root `semis` above the key's root.
    A seven-tone key numbers its own degrees; any other (the pentatonics,
    blues, whole tone) numbers by its diatonic parent — major when the key
    has a major third, else natural minor — as anyone reading numerals
    does: C in A pentatonic minor is "iii", not "ii" (its 2nd of five).
    A root outside the parent is a ♭ of the degree above ("♭v")."""
    pc = semis % 12
    if len(scale) == 7:
        return _ROMAN[scale.index(pc)] if pc in scale else _ROMAN[0]
    seven = parent(scale)
    if pc in seven:
        return _ROMAN[seven.index(pc)]
    above = next((i for i, s in enumerate(seven) if s > pc), None)
    return "♭" + _ROMAN[above] if above is not None else "♯" + _ROMAN[6]


def roman(song: dict, slot: dict) -> str:
    """ "i", "VI", "ii°", "V7": the degree as the key spells it — lower
    case for a minor third, a ° for a diminished fifth — and the quality.
    Outside a seven-tone key the degree is the diatonic parent's
    (`chord_scale`: counted in the scale itself, A pentatonic minor's C
    would read "II", and its "VI" would wrap to a second "I")."""
    scale = chord_scale(song)
    at = (int(slot["deg"]) - 1) % len(scale)
    third = _tone(scale, at + 2) - _tone(scale, at)
    fifth = _tone(scale, at + 4) - _tone(scale, at)
    r = _ROMAN[at]
    out = r.upper() if third >= 4 else r
    out += "°" if fifth == 6 else "+" if fifth == 8 else ""
    q = slot.get("q", "triad")
    return out + {"triad": "", "7th": "7", "9th": "9", "power": "5"}.get(q, q)


def name(song: dict, slot: dict) -> str:
    """ "F#m", "D", "Amaj7", "Esus4": the chord in the song's key."""
    tones = semis(song, slot)
    root = NOTE_NAMES[(key_pc(song) + tones[0]) % 12]
    q = slot.get("q", "triad")
    if q in ("sus2", "sus4"):
        return root + q
    if q == "power":
        return root + "5"
    third, fifth = tones[1] - tones[0], tones[2] - tones[0]
    minor = third == 3
    body = "m" if minor else ""
    if fifth == 6:
        body = "dim" if minor else "b5"
    elif fifth == 8:
        body = "aug"
    if q == "triad":
        return root + body
    seventh = tones[3] - tones[0]
    top = "9" if q == "9th" else "7"
    if seventh == 11:
        return root + body + "maj" + top
    return root + body + top


def from_degrees(degrees, bars: int | None = None, q: str = "triad") -> dict:
    """A chord track from a list of degrees — `3`, or `(3, "7th")`, or
    `(3, "7th", 1)` with an inversion — spread evenly over `bars` (default:
    one chord a bar, held to a `limits.CHORD_BAR_CHOICES` length)."""
    items = [d if isinstance(d, tuple | list) else (d,) for d in degrees]
    items = items[: limits.MAX_CHORD_SLOTS] or [(1,)]
    if bars is None:
        bars = next(
            (b for b in limits.CHORD_BAR_CHOICES if b >= len(items)),
            limits.CHORD_BAR_CHOICES[-1],
        )
    each = bars * 4.0 / len(items)
    return {
        "bars": bars,
        "slots": [
            {
                "at": round(i * each * 4) / 4,
                "deg": int(d[0]),
                "q": d[1] if len(d) > 1 else q,
                "inv": int(d[2]) if len(d) > 2 else 0,
                "len": each,
            }
            for i, d in enumerate(items)
        ],
    }


DEFAULT = from_degrees((1,), 1)


def track_of(song: dict, section: dict | None = None) -> dict:
    """The chord track in force: the section's own when it has one, else
    the song's, else one bar of the key's first chord."""
    own = (section or {}).get("chords")
    if own and own.get("slots"):
        return own
    held = song.get("chords")
    return held if held and held.get("slots") else DEFAULT


def resolve(
    song: dict,
    section: dict | None = None,
    bars: int | None = None,
    steps_per_bar: int = 16,
) -> list[dict]:
    """The chord under every step of `bars` bars (default: the section's,
    else the progression's), the progression tiled from bar 1:

        [{"lo", "hi": steps [lo, hi), "slot", "root": pitch class,
          "tones": semitones above the key root, root position}]

    in time order, covering every step once (a gap before the first slot
    or between two holds the chord before it; a slot is cut at the next
    one's start and at the progression's end). The ONE reader of a chord
    track."""
    track = track_of(song, section)
    if bars is None:
        bars = int(section["bars"]) if section else int(track["bars"])
    per_beat = steps_per_bar / 4.0
    period = int(track["bars"]) * steps_per_bar
    total = max(1, int(bars)) * steps_per_bar
    slots = sorted(track["slots"], key=lambda s: s["at"])
    starts = [min(period - 1, int(round(s["at"] * per_beat))) for s in slots]
    key = key_pc(song)
    out: list[dict] = []
    for base in range(0, total, period):
        for i, slot in enumerate(slots):
            lo = base + (0 if i == 0 else starts[i])
            hi = base + (starts[i + 1] if i + 1 < len(slots) else period)
            lo, hi = min(lo, total), min(hi, total)
            if hi <= lo:
                continue
            tones = semis(song, slot)
            out.append(
                {
                    "lo": lo,
                    "hi": hi,
                    "slot": slot,
                    "root": (key + tones[0]) % 12,
                    "tones": tones,
                }
            )
    return out


def chord_at(resolved: list[dict], step: int) -> dict:
    """The chord sounding at `step` (the last one past the end)."""
    for c in resolved:
        if c["lo"] <= step < c["hi"]:
            return c
    return resolved[-1]


def voiced(chord: dict, row: int) -> list[int]:
    """A resolved chord (`resolve`'s) as MIDI notes in root position, its
    root the nearest one at or below `row` (the stamp's clicked row), the
    notes above 127 left out: the chord-track stamp, in the drawer and on
    Perform."""
    base = row - (row - chord["root"]) % 12
    low = chord["tones"][0]
    return [base + t - low for t in chord["tones"] if base + t - low <= 127]


def tiles(song: dict, section: dict) -> list[dict]:
    """What the strip draws over one pass of a section, in bars:
    [{"at", "len" (bars, fractional), "roman", "name", "index" (the slot's
    place in its track), "first" (inside the progression's first play)}]."""
    track = track_of(song, section)
    order = sorted(range(len(track["slots"])), key=lambda i: track["slots"][i]["at"])
    index = {id(track["slots"][i]): i for i in order}
    period = int(track["bars"]) * 16
    out = []
    for c in resolve(song, section, int(section["bars"]), 16):
        out.append(
            {
                "at": c["lo"] / 16,
                "len": (c["hi"] - c["lo"]) / 16,
                "roman": roman(song, c["slot"]),
                "name": name(song, c["slot"]),
                "index": index[id(c["slot"])],
                "first": c["lo"] < period,
            }
        )
    return out


def label(song: dict, track: dict | None = None) -> str:
    """ "i VI III VII": a chord track (default: the song's) in one line."""
    track = track or track_of(song)
    slots = sorted(track["slots"], key=lambda s: s["at"])
    return " ".join(roman(song, s) for s in slots)


def from_cells(song: dict, pattern: dict) -> dict | None:
    """Read a chord PATTERN back into a chord track (the inverse of the
    chord generator, so a hand-played progression can lead): wherever the
    set of sounding pitch classes changes, the degree and quality whose
    tones are exactly that set — a triad before a seventh before a
    suspension, and the reading whose root is the lowest note first. None
    when the pattern holds no chord the key can name."""
    spb = int(pattern.get("steps_per_bar", 16))
    total = int(pattern.get("bars", 1)) * spb
    by_step: dict[int, list[int]] = {}
    for pitch, step in pattern.get("cells") or []:
        if 0 <= int(step) < total:
            try:
                by_step.setdefault(int(step), []).append(midi(pitch))
            except Exception:  # noqa: BLE001 - a schema-clean cell always parses
                continue
    scale, key = chord_scale(song), key_pc(song)
    readings = {}
    for q in ("triad", "7th", "9th", "power", "sus2", "sus4"):
        for deg in range(1, len(scale) + 1):
            slot = {"deg": deg, "q": q}
            pcs = pitch_classes(
                {"root": song["root"], "scale_name": song.get("scale_name")}, slot
            )
            readings.setdefault((frozenset(pcs), pcs[0]), (deg, q, pcs))
            readings.setdefault((frozenset(pcs), None), (deg, q, pcs))
    slots, last = [], None
    for step in sorted(by_step):
        notes = sorted(by_step[step])
        pcs = frozenset(n % 12 for n in notes)
        if pcs == last or len(pcs) < 2:
            continue
        got = readings.get((pcs, notes[0] % 12)) or readings.get((pcs, None))
        if got is None:
            continue
        deg, q, tones = got
        low = notes[0] % 12
        inv = tones.index(low) if low in tones else 0
        slots.append({"at": step * 4.0 / spb, "deg": deg, "q": q, "inv": min(inv, 2)})
        last = pcs
    if not slots:
        return None
    del key
    beats = total * 4.0 / spb
    for i, s in enumerate(slots):
        s["len"] = (slots[i + 1]["at"] if i + 1 < len(slots) else beats) - s["at"]
    bars = int(pattern.get("bars", 1))
    fit = next((b for b in limits.CHORD_BAR_CHOICES if b >= bars), 16)
    return {"bars": fit, "slots": slots[: limits.MAX_CHORD_SLOTS]}
