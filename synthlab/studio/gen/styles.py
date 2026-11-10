"""The styles, as data: what `gen/build.py` lays down for a starter.

A style is tempo and key habits, a pool of progressions, a kit and the
beats cut from it, a track list — each a role, a recipe Sound
(`studio/recipes.py`) and the generators that write its parts — and the
arrangement **shape**: the sections in order, what plays in each, and the
moves that make it an arrangement (a sweep, a rise, a cut, a transition on
a boundary, a part that enters late).

Styles, not songs: tempo, drum grammar, harmony, sound design, arrangement
shape and mix moves. No track or hook is recreated anywhere in this file.
A style's `label` may be a nod: a play on a name that is never the name
itself — Daft Rock, Fred Before, Just Ice, Nine Inch Wails, Psych Haze,
Sixties Studio, Anymore and Arm In Arm — and its blurb starts with the
genre; ids, recipes, kits' sounds and
everything else stay generic.

**The shape's vocabulary** (a section row):

    name, bars, repeats    `bars` is ONE pass; a section of 32 is 8 x 4
    energy                 0..1, the section's fullness (the card's chart)
    drums                  a `DRUMS` variant of the style, or None
    play                   {track key: part variant}
    moves                  see below

A section with a lane, a take or a span renders as one pass of its whole
length and every repeat replays it, so the moving sections are short
(8 or 16 bars) and the long ones (a 32-bar drop) are 8 bars four times.

**Moves:**

    ("sweep", a, b)             "Sweep all": the same low-pass lane, a -> b
                                (0..1 of 100 Hz..12 kHz), on every sounding
                                synth track
    ("lane", key, target, pts)  one lane: `target` a field ("cutoff",
                                "pitch_bend", "gain") or (fx kind, param)
                                on that track's chain; key "drums" too
    ("cut",)                    silence on the last beat: every sounding
                                track's level to 0
    ("take", kind, dir, bars, where)   a transition clip: "end" leads into
                                the next section's bar line, "start" comes
                                off this one's
    ("enter", key, bar)         the part comes in at `bar` (1-based): a
                                block span
    ("span", key, at, len)      a block's bars (0-based `at`); key "drums"

**A track row:** key, role (`song_model.TRACK_ROLES`), name, sound (a
recipe), register (`notes.REGISTER`), gain, optional `fx` (units appended
to the recipe's chain), `patch` (field overrides) and `parts`
{variant: (generator, params)}. A part with `"pair"` shares its seed with
the other half of the rolling bass.

**A drums variant** is a short pipeline of `gen/drums.py` calls:
("beat", style, bars), ("kit", bars), ("fill", bars of fill, table),
("thin", name), ("build", bars), ("tile", bars), ("drop", voice, from bar).
"""

from __future__ import annotations

DEFAULT = "melodic_techno"  # the first Start a fresh session offers

_OPEN = {"kind": "lowpass", "params": {"cutoff": 12000.0, "q": 1.2}}
_HP = {"kind": "highpass", "params": {"cutoff": 20.0, "q": 0.7}}
_STOP = {"kind": "stop", "params": {"beats": 1.0, "fall": 1.0, "mix": 0.0}}
# a bass held under its kick the way a bus compressor holds it: a light
# duck, no pump (for the packs whose recipes carry none)
_TUCK = {"kind": "sidechain", "params": {"depth": 0.35, "release": 0.12}}
# the drums' fronts lifted and their rooms tightened: Drum Punch's transient
# shaper, in front of what the style already squashes them with
_PUNCH = {"kind": "transient", "params": {"attack": 0.6, "sustain": -0.2}}
# a drop FX's one hit a pattern (`notes.hit`): the sound does the rest
_HIT = {"A": ("hit", {"bars": 1})}


def _row(name, bars, repeats, energy, drums, play, *moves) -> dict:
    return {
        "name": name,
        "bars": bars,
        "repeats": repeats,
        "energy": energy,
        "drums": drums,
        "play": play,
        "moves": moves,
    }


def _track(key, role, name, sound, register, gain, parts, fx=(), patch=None) -> dict:
    return {
        "key": key,
        "role": role,
        "name": name,
        "sound": sound,
        "register": register,
        "gain": gain,
        "parts": parts,
        "fx": list(fx),
        "patch": patch or {},
    }


_ROLL = {"A": ("bass_rolling", {"figure": "roll"})}

STYLES: dict[str, dict] = {
    # ~124 BPM, straight: a kick on every beat under Euclidean percussion, a
    # pulsing bass that leaves the kick's step, slow minor harmony, a long
    # arp whose filter opens through a build, a lead held back for the
    # breakdown and a wide stab for the second drop.
    "melodic_techno": {
        "label": "Anymore",
        "blurb": "Melodic techno: a pulsing Reese off the kick, an arp that opens",
        "bpm": 124.0,
        "bpm_range": (120.0, 126.0),
        "keys": (
            ("D2", "minor"),
            ("A1", "minor"),
            ("F1", "phrygian"),
            ("G1", "minor"),
            ("E1", "minor"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Melodic Techno",
        "kick": (0, 4, 8, 12),
        "progressions": (
            ((1, 6, 7, 7), 8),
            ((1, 5, 6, 4), 8),
            ((1, 1, 6, 7), 8),
            ((1, 6, 3, 7), 8),
        ),
        "drums_fx": (_HP,),
        "drums": {
            "perc": (("beat", "hypnotic-perc", 8),),
            "main": (("beat", "melodic-techno", 8),),
            "fill": (("beat", "melodic-techno", 8), ("fill", 1.0, "toms")),
            "build": (("build", 8),),
        },
        "tracks": (
            _track(
                "sub",
                "bass",
                "Sub",
                "Rolling Sidechained Sub",
                "sub",
                1.0,
                {"A": ("bass_rolling", {"figure": "roll", "pair": "sub"})},
            ),
            # the Reese for the mid bass, the dark
            # arp, the sung lead, and the drops' impact as a sound of its own
            # (the sub drop stays a take: a starter keeps a synth track
            # free, or "Add a bass" and its kin have no room)
            _track(
                "mid",
                "bass",
                "Reese",
                "Reese Bass",
                "bass",
                0.8,
                {"A": ("bass_rolling", {"figure": "roll", "pair": "mid"})},
            ),
            _track(
                "pad", "pad", "Pad", "Long Cinema Pad", "pad", 0.55, {"A": ("pad", {})}
            ),
            _track(
                "arp",
                "arp",
                "Arp",
                "Dark Arp Pluck",
                "arp",
                0.6,
                {"A": ("arp", {"mode": "up", "rate": 1, "octaves": 2})},
            ),
            _track(
                "lead",
                "lead",
                "Choir Lead",
                "Cinematic Choir Lead",
                "lead",
                0.7,
                {"A": ("lead", {"contour": "arch", "density": 0.4})},
            ),
            _track(
                "stab",
                "chords",
                "Stab",
                "Wide Unison Stab",
                "chords",
                0.6,
                {"A": ("chords", {"rhythm": "stabs", "voicing": "close"})},
            ),
            _track("impact", "fx", "Impact", "Impact", "sub", 0.5, _HIT),
        ),
        "shape": (
            _row("Intro", 8, 2, 0.2, "perc", {}),
            _row("Groove", 8, 2, 0.45, "main", {"sub": "A", "mid": "A"}),
            _row(
                "Build A",
                16,
                1,
                0.6,
                "main",
                {"sub": "A", "mid": "A", "arp": "A"},
                ("lane", "arp", "cutoff", ((0.0, 0.03), (1.0, 0.45))),
                ("take", "riser", "up", 4, "end"),
            ),
            _row(
                "Drop A",
                8,
                4,
                0.9,
                "fill",
                {"sub": "A", "mid": "A", "arp": "A", "pad": "A", "impact": "A"},
            ),
            _row(
                "Breakdown",
                16,
                1,
                0.3,
                None,
                {"pad": "A", "lead": "A"},
                ("enter", "lead", 9),
                ("take", "sweep", "down", 4, "start"),
            ),
            _row(
                "Breakdown B", 8, 2, 0.35, None, {"pad": "A", "lead": "A", "arp": "A"}
            ),
            _row(
                "Build B",
                16,
                1,
                0.7,
                "build",
                {"pad": "A", "lead": "A", "arp": "A"},
                ("span", "drums", 8, 8),
                ("lane", "arp", "cutoff", ((0.0, 0.05), (1.0, 0.55))),
                ("lane", "drums", ("highpass", "cutoff"), ((0.0, 0.04), (1.0, 0.5))),
                ("take", "riser", "up", 8, "end"),
            ),
            _row(
                "Drop B",
                8,
                4,
                1.0,
                "fill",
                {
                    "sub": "A",
                    "mid": "A",
                    "arp": "A",
                    "pad": "A",
                    "lead": "A",
                    "stab": "A",
                    "impact": "A",
                },
                ("take", "subdrop", "down", 2, "start"),
            ),
            _row("Outro", 8, 2, 0.2, "perc", {"sub": "A"}),
        ),
    },
    # ~122 BPM: a chopped, looped stab phrase under a resonant low-pass that
    # opens over the intro, a syncopated funk bass with slides, offbeat
    # stabs, a slow phaser and a hard pump; the filter and a dropped kick do
    # the fills' job.
    "filter_house": {
        "label": "Daft Rock",
        "blurb": "Filter house: a looped stab under an opening filter, a sliding bass",
        "bpm": 122.0,
        "bpm_range": (118.0, 126.0),
        "keys": (
            ("A1", "minor"),
            ("D2", "dorian"),
            ("E1", "minor"),
            ("G1", "dorian"),
            ("C2", "minor"),
        ),
        "swing": 0.0,
        "drum_swing": 0.04,
        "kit": "Filter House",
        "kick": (0, 4, 8, 12),
        "progressions": (
            (((1, "7th"), (4, "7th")), 2),
            (((6,), (4,), (1,), (5,)), 4),
            (((1, "7th"), (7,), (6,), (7,)), 4),
            (((1, "9th"), (4, "7th"), (6, "7th"), (5,)), 4),
        ),
        "drums_fx": (
            {"kind": "compress", "params": {"threshold": -18.0, "ratio": 4.0}},
        ),
        "drums": {
            "hats": (("beat", "filter-house", 8), ("thin", "hats")),
            "main": (("beat", "filter-house", 8),),
            "build": (
                ("beat", "filter-house", 8),
                ("drop", "kick", 6),
                ("fill", 1.0, "roll"),
            ),
            "break": (("beat", "filter-house-break", 8), ("thin", "nokick")),
        },
        "tracks": (
            # the funk bass through the envelope
            # filter, the string machine for the strings
            _track(
                "bass",
                "bass",
                "Bass",
                "Envelope Funk Bass",
                "bass",
                1.0,
                {"A": ("bass_walk", {})},
                fx=(_OPEN,),
            ),
            _track(
                "loop",
                "chords",
                "Stab Loop",
                "Chopped Stab Loop",
                "chords",
                0.8,
                {
                    "A": ("chords", {"rhythm": "stabs", "voicing": "close"}),
                    "B": (
                        "chords",
                        {"rhythm": "stabs", "voicing": "close", "busy": 0.35},
                    ),
                },
            ),
            _track(
                "pad",
                "pad",
                "Strings",
                "String Machine",
                "pad",
                0.55,
                {"A": ("pad", {})},
                fx=(_OPEN,),
            ),
            _track(
                "lead",
                "lead",
                "Talk Lead",
                "Vowel Lead",
                "lead",
                0.7,
                {"A": ("lead", {"contour": "fall", "density": 0.4})},
                fx=(_OPEN,),
            ),
        ),
        "shape": (
            _row("Intro", 16, 1, 0.25, "hats", {"loop": "A"}, ("sweep", 0.1, 0.5)),
            _row(
                "Groove",
                16,
                1,
                0.5,
                "main",
                {"loop": "A", "bass": "A"},
                ("sweep", 0.5, 0.8),
            ),
            _row(
                "Build",
                8,
                1,
                0.6,
                "build",
                {"loop": "A", "bass": "A"},
                ("take", "sweep", "up", 4, "end"),
            ),
            _row(
                "Drop A",
                8,
                4,
                0.9,
                "main",
                {"loop": "A", "bass": "A", "pad": "A", "lead": "A"},
                ("take", "impact", "down", 2, "start"),
            ),
            _row(
                "Break",
                16,
                1,
                0.3,
                "break",
                {"loop": "A", "pad": "A", "lead": "A"},
                ("sweep", 0.8, 0.3),
            ),
            _row(
                "Build B",
                8,
                1,
                0.65,
                "build",
                {"loop": "A", "bass": "A"},
                ("take", "sweep", "up", 4, "end"),
            ),
            _row(
                "Drop B",
                8,
                4,
                1.0,
                "main",
                {"loop": "B", "bass": "A", "pad": "A", "lead": "A"},
                (
                    "lane",
                    "loop",
                    ("phaser", "mix"),
                    ((0.0, 0.3), (0.5, 0.85), (1.0, 0.3)),
                ),
                ("take", "impact", "down", 2, "start"),
            ),
            _row(
                "Outro",
                16,
                1,
                0.2,
                "hats",
                {"loop": "A", "bass": "A"},
                ("span", "bass", 0, 8),
                ("sweep", 0.8, 0.12),
            ),
        ),
    },
    # ~132 BPM, swung: a two-step beat with ghosts in the verses and four on
    # the floor in the drops, a ducked sine sub, felt-like keys voiced wide,
    # pitched chops across the chord's tones, a room-tone bed, a cut before
    # the drop and a tape stop into the breakdown.
    "garage": {
        "label": "Fred Before",
        "blurb": "Emotive garage: a swung two-step, a ducked sub, worn keys, chops",
        "bpm": 132.0,
        "bpm_range": (128.0, 136.0),
        "keys": (
            ("F1", "minor"),
            ("A1", "minor"),
            ("D2", "minor"),
            ("G1", "minor"),
            ("C2", "minor"),
        ),
        "swing": 0.0,
        "drum_swing": 0.18,
        "kit": "Two-Step",
        "kick": (0, 10),
        "progressions": (
            (((1, "7th"), (6, "7th"), (3,), (7, "sus4")), 4),
            (((6, "7th"), (3,), (7,), (1, "9th")), 8),
            (((4, "7th"), (6,), (1, "7th"), (7,)), 4),
            (((1, "9th"), (5, "7th"), (6, "7th"), (4,)), 8),
        ),
        "drums_fx": (_STOP,),
        "drums": {
            "verse": (("kit", 8),),
            "drop": (("beat", "garage-4x4", 8),),
            "drop2": (("beat", "garage-4x4", 8), ("fill", 0.5, "quiet")),
            "build": (("build", 8),),
        },
        "tracks": (
            _track(
                "sub",
                "bass",
                "Sub",
                "Ducked Slide Sub",
                "sub",
                1.0,
                {"A": ("bass_2step", {})},
            ),
            # the felt piano keys, a vowel pad
            # squashed over the top for the breakdown and the last drop
            _track(
                "keys",
                "chords",
                "Keys",
                "Felt Piano Keys",
                "chords",
                0.85,
                {"A": ("chords", {"rhythm": "held", "voicing": "wide"})},
                fx=(_STOP,),
            ),
            _track(
                "vox",
                "vocal",
                "Vox Chops",
                "Vox Pluck",
                "vocal",
                0.8,
                {
                    "A": ("chops", {}),
                    "B": ("chops", {"thin": 0.4}),
                    "C": ("lead", {"contour": "arch", "density": 0.4}),
                },
                patch={"bend_range": 7.0},
            ),
            _track(
                "pad", "pad", "Bed Pad", "Chop Bed Pad", "pad", 0.5, {"A": ("pad", {})}
            ),
            _track(
                "stab",
                "chords",
                "Answer",
                "Offbeat Stab",
                "chords",
                0.6,
                {"A": ("chords", {"rhythm": "stabs", "voicing": "close"})},
            ),
            _track(
                "voxpad",
                "pad",
                "Vocal Pad",
                "Squashed Vocal Pad",
                "pad",
                0.5,
                {"A": ("pad", {"voicing": "close"})},
            ),
        ),
        "shape": (
            _row(
                "Intro",
                8,
                1,
                0.15,
                None,
                {"keys": "A"},
                ("take", "bed", "down", 8, "start"),
            ),
            _row("Verse", 8, 2, 0.45, "verse", {"sub": "A", "keys": "A", "vox": "B"}),
            _row(
                "Build",
                8,
                1,
                0.6,
                "build",
                {"keys": "A", "vox": "A"},
                ("lane", "vox", "pitch_bend", ((0.0, 0.5), (1.0, 1.0))),
                ("take", "swell", "up", 2, "end"),
                ("cut",),
            ),
            _row(
                "Drop",
                16,
                1,
                0.9,
                "drop",
                {"sub": "A", "keys": "A", "vox": "A", "pad": "A"},
                ("lane", "keys", ("stop", "mix"), ((0.0, 1.0), (1.0, 1.0))),
                ("lane", "drums", ("stop", "mix"), ((0.0, 1.0), (1.0, 1.0))),
                ("take", "impact", "down", 2, "start"),
            ),
            _row(
                "Breakdown",
                16,
                1,
                0.25,
                None,
                {"keys": "A", "vox": "C", "pad": "A", "voxpad": "A"},
                ("take", "bed", "down", 8, "start"),
                ("take", "bed", "down", 8, 8),
            ),
            _row(
                "Build B",
                8,
                1,
                0.65,
                "build",
                {"keys": "A", "vox": "A"},
                ("lane", "vox", "pitch_bend", ((0.0, 0.5), (1.0, 1.0))),
                ("take", "swell", "up", 2, "end"),
                ("cut",),
            ),
            _row(
                "Drop B",
                8,
                3,
                1.0,
                "drop2",
                {
                    "sub": "A",
                    "keys": "A",
                    "vox": "A",
                    "pad": "A",
                    "stab": "A",
                    "voxpad": "A",
                },
                ("enter", "stab", 5),
                ("take", "impact", "down", 2, "start"),
            ),
            _row(
                "Outro",
                8,
                1,
                0.15,
                None,
                {"keys": "A", "pad": "A"},
                ("take", "bed", "down", 8, "start"),
            ),
        ),
    },
    # ~138 BPM, straight: the rolling bass — three sixteenths after every
    # kick, never on it — as a mid bass over a sub, an open hat on every
    # offbeat, a long breakdown, a build with an accelerating roll under a
    # gate that closes in, and a supersaw lead over the climax.
    "trance": {
        "label": "Arm In Arm",
        "blurb": "Trance: the rolling bass, a long breakdown, a gated supersaw climax",
        "bpm": 138.0,
        "bpm_range": (132.0, 140.0),
        "keys": (
            ("F#1", "minor"),
            ("A1", "minor"),
            ("G1", "minor"),
            ("E1", "minor"),
            ("D2", "minor"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Trance",
        "kick": (0, 4, 8, 12),
        "progressions": (
            ((1, 6, 3, 7), 8),
            ((6, 7, 1, 1), 8),
            ((1, 7, 6, 7), 8),
            ((4, 6, 1, 7), 8),
        ),
        # the kick punched through: its front lifted, its room
        # tightened
        "drums_fx": (_HP, _PUNCH),
        "drums": {
            "intro": (("beat", "trance-break", 8),),
            "main": (("beat", "trance", 8),),
            "climax": (
                ("beat", "trance-climax", 8),
                ("fill", 0.5, "roll", ("kick", "ohat", "crash")),
            ),
            "build": (("build", 8),),
        },
        "tracks": (
            _track(
                "sub",
                "bass",
                "Sub",
                "Rolling Sub",
                "sub",
                1.0,
                {
                    "A": ("bass_rolling", {"figure": "roll", "pair": "sub"}),
                    "B": ("bass_rolling", {"figure": "offbeat", "pair": "sub"}),
                },
            ),
            _track(
                "mid",
                "bass",
                "Mid Bass",
                "Rolling Mid Bass",
                "bass",
                0.85,
                {
                    "A": ("bass_rolling", {"figure": "roll", "pair": "mid"}),
                    "B": ("bass_rolling", {"figure": "offbeat", "pair": "mid"}),
                },
            ),
            # the big-room pad, the pluck-lead under
            # the supersaw (a sub drop under the climax is a take: the
            # eighth synth track stays free)
            _track("pad", "pad", "Pad", "Big Room Pad", "pad", 0.5, {"A": ("pad", {})}),
            _track(
                "gate",
                "pad",
                "Gated Pad",
                "Gated Pad",
                "pad",
                0.55,
                {"A": ("chords", {"rhythm": "held", "voicing": "close"})},
            ),
            _track(
                "pluck",
                "arp",
                "Pluck",
                "Trance Pluck Lead",
                "arp",
                0.6,
                {"A": ("arp", {"mode": "updown", "rate": 1, "octaves": 2})},
            ),
            _track(
                "lead",
                "lead",
                "Lead",
                "Anthem Supersaw",
                "lead",
                0.75,
                {"A": ("lead", {"contour": "arch", "density": 0.7})},
            ),
            _track(
                "keys",
                "chords",
                "Keys",
                "Felt Keys",
                "chords",
                0.7,
                {"A": ("chords", {"rhythm": "held", "voicing": "anthem"})},
            ),
        ),
        "shape": (
            _row("Intro", 8, 2, 0.2, "intro", {}),
            _row("Intro B", 8, 2, 0.4, "main", {"sub": "B", "mid": "B"}),
            _row(
                "Breakdown",
                16,
                1,
                0.25,
                None,
                {"pad": "A", "keys": "A", "pluck": "A"},
                ("enter", "pluck", 9),
                ("take", "sweep", "down", 4, "start"),
            ),
            _row(
                "Breakdown B", 8, 2, 0.35, None, {"pad": "A", "keys": "A", "pluck": "A"}
            ),
            _row(
                "Build",
                16,
                1,
                0.7,
                "build",
                {"pad": "A", "gate": "A", "pluck": "A"},
                ("span", "drums", 8, 8),
                ("lane", "drums", ("highpass", "cutoff"), ((0.0, 0.04), (1.0, 0.6))),
                ("lane", "gate", ("gate", "depth"), ((0.0, 0.0), (1.0, 1.0))),
                ("take", "sweep", "up", 8, "end"),
                ("cut",),
            ),
            _row(
                "Climax",
                8,
                4,
                1.0,
                "climax",
                {
                    "sub": "A",
                    "mid": "A",
                    "lead": "A",
                    "gate": "A",
                    "pad": "A",
                    "pluck": "A",
                },
                ("take", "impact", "down", 2, "start"),
                ("take", "subdrop", "down", 2, "start"),
            ),
            _row("Mid", 8, 2, 0.6, "main", {"sub": "A", "mid": "A", "pluck": "A"}),
            _row("Outro", 8, 2, 0.2, "intro", {"sub": "B"}),
        ),
        # "long" doubles the two intros and the climax
        "long": ("Intro", "Intro B", "Climax"),
    },
    # Just Ice (French electro, ~120, straight): a driven four on the floor
    # with a pushed kick, a crushed saw bass and a disco pluck bass, a
    # pumped disco stab, a choir-like pad for the big open moments, a blown
    # lead for the drops, a half-time stomp in the middle and a siren into
    # the last drop.
    "electro": {
        "label": "Just Ice",
        "blurb": "French electro: crushed bass, disco stabs, a choir, a stomp",
        "bpm": 120.0,
        "bpm_range": (114.0, 126.0),
        "keys": (
            ("E1", "minor"),
            ("A1", "minor"),
            ("F#1", "minor"),
            ("D2", "minor"),
            ("G1", "phrygian"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Just Ice",
        "kick": (0, 4, 8, 12),
        "progressions": (
            ((1, 6, 7, 1), 4),
            ((1, 4, 6, 5), 4),
            ((6, 7, 1, 1), 4),
            ((1, 7, 6, 7), 4),
        ),
        "drums_fx": (
            _PUNCH,
            {"kind": "compress", "params": {"threshold": -20.0, "ratio": 6.0}},
            {"kind": "drive", "params": {"amount": 1.8, "mix": 0.5}},
        ),
        "drums": {
            "hats": (("beat", "electro-break", 8), ("thin", "hats")),
            "main": (("beat", "electro", 8),),
            "stomp": (("beat", "electro-stomp", 8),),
            "build": (
                ("beat", "electro", 8),
                ("drop", "kick", 6),
                ("fill", 1.0, "roll"),
            ),
            "break": (("beat", "electro-break", 8), ("thin", "nokick")),
        },
        "tracks": (
            _track(
                "bass",
                "bass",
                "Bass",
                "Crushed Saw Bass",
                "bass",
                0.9,
                {
                    "A": ("bass_offbeat", {}),
                    "B": ("bass_rolling", {"figure": "octave"}),
                },
                fx=(_OPEN,),
            ),
            _track(
                "disco",
                "bass",
                "Disco Bass",
                "Slap Pluck Bass",
                "bass",
                0.8,
                {"A": ("bass_walk", {})},
            ),
            _track(
                "stab",
                "chords",
                "Stab",
                "Gated Disco Stab",
                "chords",
                0.75,
                {
                    "A": ("chords", {"rhythm": "stabs", "voicing": "close"}),
                    "B": ("chords", {"rhythm": "pump", "voicing": "power"}),
                },
                fx=(_OPEN,),
            ),
            _track(
                "choir",
                "pad",
                "Choir",
                "Cathedral Choir Pad",
                "pad",
                0.6,
                {"A": ("pad", {"voicing": "wide"})},
            ),
            _track(
                "lead",
                "lead",
                "Lead",
                "Blown Lead",
                "lead",
                0.65,
                {"A": ("lead", {"contour": "rise", "density": 0.55})},
            ),
            # the siren into the last drop: held chord tones whose bend
            # curve (the recipe's) climbs an octave across the pattern
            _track(
                "siren",
                "fx",
                "Siren",
                "Siren Riser",
                "lead",
                0.45,
                {"A": ("pad", {"voicing": "close"})},
            ),
        ),
        "shape": (
            _row(
                "Intro",
                8,
                2,
                0.25,
                "hats",
                {"choir": "A", "stab": "A"},
                ("sweep", 0.1, 0.45),
            ),
            _row("Groove", 16, 1, 0.55, "main", {"stab": "A", "disco": "A"}),
            _row(
                "Build",
                8,
                1,
                0.65,
                "build",
                {"stab": "A", "bass": "A"},
                ("sweep", 0.4, 0.9),
                ("take", "sweep", "up", 4, "end"),
                ("cut",),
            ),
            _row(
                "Drop",
                8,
                4,
                1.0,
                "main",
                {"bass": "A", "stab": "B", "lead": "A", "choir": "A"},
                ("take", "impact", "down", 2, "start"),
            ),
            _row(
                "Stomp",
                16,
                1,
                0.7,
                "stomp",
                {"bass": "B", "choir": "A"},
                ("lane", "bass", ("drive", "mix"), ((0.0, 0.3), (1.0, 1.0))),
            ),
            _row(
                "Choir",
                16,
                1,
                0.3,
                "break",
                {"choir": "A", "lead": "A"},
                ("sweep", 0.8, 0.35),
                ("enter", "lead", 9),
            ),
            _row(
                "Build B",
                8,
                1,
                0.7,
                "build",
                {"stab": "A", "bass": "A", "choir": "A", "siren": "A"},
                ("take", "sweep", "up", 8, "end"),
                ("cut",),
            ),
            _row(
                "Drop B",
                8,
                4,
                1.0,
                "main",
                {"bass": "A", "disco": "A", "stab": "B", "lead": "A", "choir": "A"},
                ("take", "impact", "down", 2, "start"),
            ),
            _row(
                "Outro",
                8,
                2,
                0.2,
                "hats",
                {"stab": "A", "disco": "A"},
                ("sweep", 0.7, 0.1),
            ),
        ),
        "long": ("Groove", "Drop", "Drop B"),
    },
    # Nine Inch Wails (industrial, ~105, a machine's straight): quiet and
    # loud — a worn, ringing keys-and-drone score that a grinding bass, a
    # clanking machine beat and a snarling lead break open; a ticking pulse
    # under it all; a bridge that falls back to the keys alone.
    "industrial": {
        "label": "Nine Inch Wails",
        "blurb": "Industrial: a worn quiet score, then a grinding machine and a snarl",
        "bpm": 105.0,
        "bpm_range": (92.0, 118.0),
        "keys": (
            ("E1", "minor"),
            ("D2", "phrygian"),
            ("A1", "minor"),
            ("C2", "minor"),
            ("G1", "minor"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Nine Inch Wails",
        "kick": (0, 3, 8),
        "progressions": (
            ((1, 1, 6, 7), 4),
            ((1, 2, 1, 7), 4),
            ((6, 4, 1, 5), 4),
            ((1, 7, 6, 6), 4),
        ),
        "drums_fx": (
            {"kind": "bitcrush", "params": {"bits": 10.0, "downsample": 2.0}},
            {"kind": "compress", "params": {"threshold": -20.0, "ratio": 8.0}},
        ),
        "drums": {
            "pulse": (("beat", "industrial-pulse", 8),),
            "main": (("beat", "industrial", 8),),
            "march": (("beat", "industrial-march", 8), ("fill", 0.5, "toms")),
            "build": (("build", 8),),
        },
        "tracks": (
            _track(
                "keys",
                "chords",
                "Ghost Keys",
                "Ghost Keys",
                "chords",
                0.7,
                {"A": ("chords", {"rhythm": "held", "voicing": "wide"})},
            ),
            _track(
                "drone", "pad", "Drone", "Static Drone", "pad", 0.5, {"A": ("pad", {})}
            ),
            _track(
                "bass",
                "bass",
                "Bass",
                "Grinding Bass",
                "bass",
                0.9,
                {
                    "A": ("bass_root", {}),
                    "B": ("bass_rolling", {"figure": "gallop"}),
                },
                fx=(_OPEN,),
            ),
            _track(
                "pulse",
                "arp",
                "Pulse",
                "Machine Pulse",
                "arp",
                0.55,
                {"A": ("arp", {"mode": "up", "rate": 2, "octaves": 1})},
            ),
            _track(
                "metal",
                "fx",
                "Metal",
                "Struck Metal",
                "arp",
                0.5,
                {"A": ("chops", {})},
            ),
            _track(
                "lead",
                "lead",
                "Lead",
                "Fuzz Lead",
                "lead",
                0.6,
                {"A": ("lead", {"contour": "fall", "density": 0.5})},
            ),
        ),
        "shape": (
            _row("Intro", 16, 1, 0.15, None, {"keys": "A", "drone": "A"}),
            _row(
                "Verse",
                16,
                1,
                0.45,
                "pulse",
                {"keys": "A", "drone": "A", "pulse": "A", "bass": "A"},
                ("enter", "bass", 9),
            ),
            _row(
                "Rise",
                8,
                1,
                0.6,
                "build",
                {"pulse": "A", "bass": "A", "drone": "A"},
                ("sweep", 0.3, 0.9),
                ("take", "sweep", "up", 4, "end"),
            ),
            _row(
                "Chorus",
                8,
                2,
                0.95,
                "march",
                {"bass": "B", "lead": "A", "metal": "A", "pulse": "A", "drone": "A"},
                ("take", "impact", "down", 2, "start"),
            ),
            _row(
                "Verse B",
                16,
                1,
                0.5,
                "main",
                {"keys": "A", "pulse": "A", "bass": "A", "metal": "A"},
            ),
            _row(
                "Chorus B",
                8,
                2,
                1.0,
                "march",
                {"bass": "B", "lead": "A", "metal": "A", "pulse": "A", "drone": "A"},
                ("take", "impact", "down", 2, "start"),
            ),
            _row(
                "Bridge",
                16,
                1,
                0.2,
                None,
                {"keys": "A", "drone": "A"},
                ("sweep", 0.7, 0.25),
                ("take", "sweep", "up", 4, "end"),
            ),
            _row(
                "Last Chorus",
                8,
                4,
                1.0,
                "march",
                {
                    "bass": "B",
                    "lead": "A",
                    "metal": "A",
                    "pulse": "A",
                    "drone": "A",
                    "keys": "A",
                },
                ("take", "impact", "down", 2, "start"),
            ),
            _row("Outro", 16, 1, 0.1, None, {"keys": "A"}),
        ),
        "long": ("Verse", "Chorus", "Last Chorus"),
    },
    # Psych Haze (neo-psych, ~112, the drums swung loose): a drummer's
    # groove under a bass that swims, wobbling keys, a hazy pad, a phased
    # arp and a fuzzed, flanged lead into a tape echo; a disco-psych
    # chorus, a half-time haze that drifts off, a swell into each chorus.
    # The drums ride a phaser and a squash.
    "psych": {
        "label": "Psych Haze",
        "blurb": "Neo-psych: a loose swung groove, a swimming bass, phased haze, fuzz",
        "bpm": 112.0,
        "bpm_range": (96.0, 124.0),
        "keys": (
            ("E1", "dorian"),
            ("A1", "minor"),
            ("D2", "mixolydian"),
            ("F#1", "minor"),
            ("C2", "major"),
        ),
        "swing": 0.0,
        "drum_swing": 0.12,
        "kit": "Psych Haze",
        "kick": (0, 6, 10),
        "progressions": (
            (((1, "7th"), (4, "7th")), 2),
            ((1, 7, 4, 4), 4),
            ((6, 4, 1, 5), 4),
            (((2, "7th"), (5, "7th"), (1, "7th"), (1, "7th")), 4),
        ),
        "drums_fx": (
            {"kind": "phaser", "params": {"rate": 0.2, "depth": 0.5, "mix": 0.3}},
            {"kind": "compress", "params": {"threshold": -20.0, "ratio": 5.0}},
        ),
        "drums": {
            "haze": (("beat", "psych-haze", 8),),
            "main": (("beat", "psych", 8),),
            "build": (("beat", "psych", 8), ("fill", 1.0, "toms")),
            "disco": (("beat", "psych-disco", 8), ("fill", 0.5, "toms")),
        },
        "tracks": (
            _track(
                "bass",
                "bass",
                "Bass",
                "Chorus Fuzz Bass",
                "bass",
                0.9,
                {"A": ("bass_walk", {}), "B": ("bass_root", {})},
                fx=(_OPEN, _TUCK),
            ),
            _track(
                "keys",
                "chords",
                "Keys",
                "Wobble Keys",
                "chords",
                0.7,
                {
                    "A": ("chords", {"rhythm": "push", "voicing": "wide"}),
                    "B": ("chords", {"rhythm": "held", "voicing": "wide"}),
                },
            ),
            _track("pad", "pad", "Haze", "Haze Pad", "pad", 0.55, {"A": ("pad", {})}),
            _track(
                "arp",
                "arp",
                "Arp",
                "Phase Arp",
                "arp",
                0.5,
                {"A": ("arp", {"mode": "updown", "rate": 2, "octaves": 2})},
            ),
            _track(
                "lead",
                "lead",
                "Lead",
                "Phased Fuzz Lead",
                "lead",
                0.65,
                {"A": ("lead", {"contour": "arch", "density": 0.5})},
            ),
            # the swell into a chorus: held chord tones whose low-pass curve
            # (the recipe's) opens across the bar
            _track(
                "swell",
                "fx",
                "Swell",
                "Tape Swell",
                "pad",
                0.45,
                {"A": ("pad", {"voicing": "close"})},
            ),
        ),
        "shape": (
            _row(
                "Intro",
                8,
                2,
                0.2,
                "haze",
                {"pad": "A", "keys": "B"},
                ("sweep", 0.15, 0.6),
            ),
            _row(
                "Verse",
                16,
                1,
                0.45,
                "main",
                {"keys": "A", "bass": "A", "pad": "A"},
                ("enter", "bass", 5),
            ),
            _row(
                "Pre",
                8,
                1,
                0.6,
                "build",
                {"keys": "A", "bass": "A", "arp": "A", "swell": "A"},
                ("span", "swell", 6, 2),
                ("sweep", 0.4, 0.9),
                ("take", "swell", "up", 4, "end"),
            ),
            _row(
                "Chorus",
                8,
                2,
                0.9,
                "disco",
                {"bass": "B", "keys": "A", "pad": "A", "arp": "A", "lead": "A"},
            ),
            _row(
                "Haze",
                16,
                1,
                0.3,
                "haze",
                {"pad": "A", "arp": "A", "lead": "A"},
                ("enter", "lead", 9),
                ("sweep", 0.8, 0.3),
            ),
            _row(
                "Pre B",
                8,
                1,
                0.65,
                "build",
                {"keys": "A", "bass": "A", "arp": "A", "swell": "A"},
                ("span", "swell", 6, 2),
                ("sweep", 0.4, 0.9),
                ("take", "swell", "up", 4, "end"),
            ),
            _row(
                "Chorus B",
                8,
                4,
                1.0,
                "disco",
                {"bass": "B", "keys": "A", "pad": "A", "arp": "A", "lead": "A"},
                (
                    "lane",
                    "lead",
                    ("flanger", "mix"),
                    ((0.0, 0.2), (0.5, 0.8), (1.0, 0.2)),
                ),
            ),
            _row(
                "Outro",
                16,
                1,
                0.15,
                "haze",
                {"pad": "A", "keys": "B"},
                ("sweep", 0.7, 0.1),
            ),
        ),
        "long": ("Verse", "Chorus", "Chorus B"),
    },
    # Sixties Studio (60s studio, ~116, straight): tight dry drums, a round
    # bass, an organ through the rotary speaker, strings and a flute as a
    # tape-replay keyboard played them, a double-tracked lead with a
    # slapback; a tom-loop bridge, a backwards cymbal into the last chorus.
    # No reverb is on the drums; the bass is tucked under the kick (`_TUCK`).
    "sixties": {
        "label": "Sixties Studio",
        "blurb": "60s studio: tight dry drums, a rotary organ, tape strings, ADT",
        "bpm": 116.0,
        "bpm_range": (96.0, 136.0),
        "keys": (
            ("G1", "major"),
            ("C2", "major"),
            ("D2", "mixolydian"),
            ("A1", "major"),
            ("E1", "minor"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Sixties Studio",
        "kick": (0, 8),
        "progressions": (
            ((1, 6, 4, 5), 4),
            ((1, 4, 5, 4), 4),
            ((1, 7, 4, 1), 4),
            ((6, 4, 1, 5), 4),
        ),
        "drums_fx": (
            {"kind": "compress", "params": {"threshold": -16.0, "ratio": 3.0}},
        ),
        "drums": {
            "break": (("beat", "sixties-break", 8),),
            "main": (("beat", "sixties", 8), ("fill", 0.5, "toms")),
            "toms": (("beat", "sixties-toms", 8),),
        },
        "tracks": (
            _track(
                "bass",
                "bass",
                "Bass",
                "Round Bass",
                "bass",
                0.9,
                {"A": ("bass_walk", {}), "B": ("bass_root", {})},
                fx=(_TUCK,),
            ),
            _track(
                "organ",
                "chords",
                "Organ",
                "Rotary Organ",
                "chords",
                0.6,
                {
                    "A": ("chords", {"rhythm": "push", "voicing": "close"}),
                    "B": ("chords", {"rhythm": "held", "voicing": "wide"}),
                },
            ),
            _track(
                "strings",
                "pad",
                "Strings",
                "Tape Strings",
                "pad",
                0.5,
                {"A": ("pad", {"voicing": "wide"})},
            ),
            _track(
                "flute",
                "arp",
                "Flute",
                "Tape Flute",
                "arp",
                0.5,
                {"A": ("arp", {"mode": "updown", "rate": 2, "octaves": 1})},
            ),
            _track(
                "lead",
                "lead",
                "Lead",
                "Double Track Lead",
                "lead",
                0.6,
                {"A": ("lead", {"contour": "rise", "density": 0.5})},
            ),
            # a cymbal turned over: held chord tones, so it swells to each
            # chord change and stops dead on it
            _track(
                "cymbal",
                "fx",
                "Backwards Cymbal",
                "Backwards Cymbal",
                "lead",
                0.4,
                {"A": ("pad", {"voicing": "close"})},
            ),
        ),
        "shape": (
            _row("Intro", 8, 1, 0.2, "break", {"organ": "B", "strings": "A"}),
            _row(
                "Verse",
                16,
                1,
                0.45,
                "break",
                {"bass": "A", "organ": "A"},
                ("enter", "bass", 5),
            ),
            _row(
                "Chorus",
                8,
                2,
                0.85,
                "main",
                {"bass": "B", "organ": "A", "strings": "A", "lead": "A"},
            ),
            _row(
                "Verse B",
                16,
                1,
                0.5,
                "main",
                {"bass": "A", "organ": "A", "flute": "A"},
            ),
            _row(
                "Bridge",
                16,
                1,
                0.6,
                "toms",
                {
                    "bass": "B",
                    "strings": "A",
                    "flute": "A",
                    "lead": "A",
                    "cymbal": "A",
                },
                ("enter", "lead", 9),
                ("span", "cymbal", 15, 1),
                ("take", "swell", "up", 2, "end"),
            ),
            _row(
                "Chorus B",
                8,
                2,
                0.9,
                "main",
                {"bass": "B", "organ": "A", "strings": "A", "lead": "A"},
                ("take", "swell", "up", 2, "end"),
            ),
            _row(
                "Last Chorus",
                8,
                3,
                1.0,
                "main",
                {
                    "bass": "B",
                    "organ": "A",
                    "strings": "A",
                    "lead": "A",
                    "flute": "A",
                },
            ),
            _row(
                "Outro",
                8,
                2,
                0.2,
                "break",
                {"organ": "B", "strings": "A"},
                ("sweep", 0.7, 0.1),
            ),
        ),
        "long": ("Verse", "Chorus", "Last Chorus"),
    },
    # --- five more genres: hip-hop / trap, drum & bass, dubstep, synthwave,
    # future bass. Plain genre labels (no nods); every sound a library
    # recipe, every drum the library's kits and beat styles.
    #
    # Hip-hop / trap (~140, half time): the 808 on the kick with a glide
    # into the next root, hats in eighths that roll, dark bells, a choir, a
    # flute-ish line in the verses; a bridge with no drums, the distorted
    # 808 for the second hook.
    "hiphop": {
        "label": "Hip-Hop / Trap",
        "blurb": "Hip-hop / trap: a gliding 808 on the kick, rolling hats, dark bells",
        "bpm": 140.0,
        "bpm_range": (130.0, 150.0),
        "keys": (
            ("C2", "minor"),
            ("F1", "minor"),
            ("G1", "minor"),
            ("A1", "minor"),
            ("D2", "phrygian"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Hip-Hop 808",
        "kick": (0, 10),
        "progressions": (
            ((1, 6, 3, 7), 8),
            ((1, 4, 6, 5), 8),
            ((1, 7, 6, 7), 8),
            ((1, 6, 4, 4), 8),
        ),
        "drums_fx": (_HP, _PUNCH),
        "drums": {
            "hats": (("beat", "trap-808", 8), ("thin", "hats")),
            "main": (("beat", "trap-808", 8),),
            "hook": (("beat", "trap-808", 8), ("fill", 0.5, "roll")),
        },
        "tracks": (
            # the 808 lands with the kick (1 and the "and" of 3) and slides
            # into the next root: `bass_2step`'s grammar, tucked under the
            # kick
            _track(
                "808",
                "bass",
                "808",
                "808 Long",
                "sub",
                1.0,
                {"A": ("bass_2step", {})},
                fx=(_TUCK,),
            ),
            _track(
                "dist",
                "bass",
                "808 Dist",
                "808 Distorted",
                "sub",
                0.9,
                {"A": ("bass_2step", {})},
                fx=(_TUCK,),
            ),
            _track(
                "bells",
                "lead",
                "Bells",
                "Dark Bell Keys",
                "lead",
                0.65,
                {"A": ("lead", {"contour": "fall", "density": 0.6})},
            ),
            _track(
                "choir",
                "pad",
                "Choir",
                "Dark Choir Pad",
                "pad",
                0.5,
                {"A": ("pad", {})},
            ),
            _track(
                "flute",
                "lead",
                "Flute",
                "Airy Flute Lead",
                "lead",
                0.55,
                {"A": ("lead", {"contour": "arch", "density": 0.4})},
            ),
        ),
        "shape": (
            _row(
                "Intro",
                8,
                1,
                0.2,
                "hats",
                {"choir": "A", "bells": "A"},
                ("sweep", 0.2, 0.7),
            ),
            _row(
                "Verse",
                16,
                1,
                0.5,
                "main",
                {"808": "A", "bells": "A", "flute": "A"},
                ("enter", "flute", 9),
            ),
            _row(
                "Pre",
                8,
                1,
                0.6,
                "hats",
                {"choir": "A", "bells": "A"},
                ("take", "riser", "up", 4, "end"),
                ("cut",),
            ),
            _row(
                "Hook",
                8,
                4,
                1.0,
                "hook",
                {"808": "A", "bells": "A", "choir": "A"},
            ),
            _row(
                "Verse B",
                16,
                1,
                0.55,
                "main",
                {"808": "A", "flute": "A", "bells": "A"},
                ("lane", "bells", "cutoff", ((0.0, 0.3), (1.0, 0.7))),
            ),
            _row(
                "Bridge",
                8,
                1,
                0.3,
                None,
                {"choir": "A", "flute": "A"},
                ("take", "riser", "up", 4, "end"),
            ),
            _row(
                "Hook B",
                8,
                4,
                1.0,
                "hook",
                {"dist": "A", "bells": "A", "choir": "A", "flute": "A"},
            ),
            _row(
                "Outro",
                8,
                1,
                0.2,
                "hats",
                {"choir": "A", "bells": "A"},
                ("sweep", 0.7, 0.1),
            ),
        ),
        "long": ("Verse", "Hook", "Hook B"),
    },
    # Drum & bass (~174): the two-step under a neuro Reese and a rolling
    # sub, an atmospheric pad and a liquid stab; a breakdown of sixteen bars
    # with no drums, the second drop busier (the jungle-ish beat).
    "dnb": {
        "label": "Drum & Bass",
        "blurb": "Drum & bass: a two-step at 174, a neuro Reese over a rolling sub",
        "bpm": 174.0,
        "bpm_range": (168.0, 178.0),
        "keys": (
            ("F1", "minor"),
            ("E1", "minor"),
            ("G1", "minor"),
            ("D2", "minor"),
            ("A1", "minor"),
        ),
        "swing": 0.0,
        "drum_swing": 0.08,
        "kit": "Drum and Bass",
        "kick": (0, 10),
        "progressions": (
            ((1, 6, 7, 1), 8),
            ((1, 4, 6, 5), 8),
            ((6, 7, 1, 1), 8),
            ((1, 3, 6, 7), 8),
        ),
        "drums_fx": (_HP, _PUNCH),
        "drums": {
            "hats": (("beat", "dnb-roller", 8), ("thin", "hats")),
            "drop": (("beat", "dnb", 8), ("fill", 0.5, "roll")),
            "jungle": (("beat", "jungle", 8), ("fill", 1.0, "roll")),
            "build": (("build", 8),),
        },
        "tracks": (
            _track(
                "sub",
                "bass",
                "Sub",
                "Rolling DnB Sub",
                "sub",
                1.0,
                {"A": ("bass_root", {})},
                fx=(_TUCK,),
            ),
            # the Reese's chain is full (Neuro Grind, six units): its line
            # leaves the kick's steps instead of being ducked
            _track(
                "reese",
                "bass",
                "Reese",
                "Neuro Reese",
                "bass",
                0.8,
                {"A": ("bass_walk", {})},
            ),
            _track("pad", "pad", "Pad", "Atmos Pad", "pad", 0.5, {"A": ("pad", {})}),
            _track(
                "stab",
                "chords",
                "Stab",
                "Liquid Stab",
                "chords",
                0.6,
                {"A": ("chords", {"rhythm": "stabs", "voicing": "close"})},
            ),
            _track("impact", "fx", "Impact", "Impact", "sub", 0.5, _HIT),
        ),
        "shape": (
            _row(
                "Intro",
                16,
                1,
                0.2,
                "hats",
                {"pad": "A"},
                ("sweep", 0.1, 0.6),
            ),
            _row(
                "Build",
                8,
                1,
                0.55,
                "build",
                {"pad": "A", "stab": "A"},
                ("take", "riser", "up", 4, "end"),
            ),
            _row(
                "Drop",
                8,
                4,
                1.0,
                "drop",
                {"sub": "A", "reese": "A", "stab": "A", "impact": "A"},
            ),
            _row(
                "Breakdown",
                16,
                1,
                0.3,
                None,
                {"pad": "A", "stab": "A"},
                ("enter", "stab", 9),
                ("take", "sweep", "down", 4, "start"),
            ),
            _row(
                "Build B",
                8,
                1,
                0.65,
                "build",
                {"pad": "A", "stab": "A"},
                ("take", "riser", "up", 4, "end"),
                ("cut",),
            ),
            _row(
                "Drop B",
                8,
                4,
                1.0,
                "jungle",
                {
                    "sub": "A",
                    "reese": "A",
                    "stab": "A",
                    "pad": "A",
                    "impact": "A",
                },
            ),
            _row(
                "Outro",
                16,
                1,
                0.2,
                "hats",
                {"pad": "A", "sub": "A"},
                ("sweep", 0.6, 0.1),
            ),
        ),
        "long": ("Intro", "Drop", "Drop B"),
    },
    # Dubstep / bass music (~140, half time): a sub under a wobble synced to
    # the eighth; the second drop a riddim-sparse beat under a growl that
    # answers a triplet wobble (the same recipe, its LFO at the eighth
    # triplet); a screech in the builds, a cut before each drop, an impact
    # on it.
    "dubstep": {
        "label": "Dubstep",
        "blurb": "Dubstep: half time at 140, a wobble, a growl, a sub, a screech",
        "bpm": 140.0,
        "bpm_range": (136.0, 145.0),
        "keys": (
            ("F1", "minor"),
            ("E1", "phrygian"),
            ("G1", "minor"),
            ("D2", "minor"),
            ("F#1", "minor"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Dubstep",
        "kick": (0, 10),
        "progressions": (
            ((1, 1, 6, 7), 8),
            ((1, 6, 1, 7), 8),
            ((1, 2, 1, 6), 8),
            ((1, 1, 4, 6), 8),
        ),
        "drums_fx": (_HP, _PUNCH),
        "drums": {
            "hats": (("beat", "dubstep", 8), ("thin", "hats")),
            "main": (("beat", "dubstep", 8), ("fill", 0.5, "roll")),
            "riddim": (("beat", "riddim", 8), ("fill", 0.5, "roll")),
            "build": (("build", 8),),
        },
        "tracks": (
            _track(
                "sub",
                "bass",
                "Sub",
                "Half-Time Sub",
                "sub",
                1.0,
                {"A": ("bass_root", {})},
                fx=(_TUCK,),
            ),
            _track(
                "wobble",
                "bass",
                "Wobble",
                "Wobble Bass",
                "bass",
                0.75,
                {"A": ("bass_root", {})},
                fx=(_TUCK,),
            ),
            _track(
                "triplet",
                "bass",
                "Triplet Wobble",
                "Wobble Bass",
                "bass",
                0.75,
                {"A": ("bass_root", {})},
                fx=(_TUCK,),
                patch={"filter_lfo_sync": "1/8T"},
            ),
            # the growl's chain is full (Growl Squash): its line leaves the
            # kick's steps
            _track(
                "growl",
                "bass",
                "Growl",
                "Growl Bass",
                "bass",
                0.75,
                {"A": ("bass_walk", {})},
            ),
            _track(
                "lead",
                "lead",
                "Screech",
                "Screech Lead",
                "lead",
                0.55,
                {"A": ("lead", {"contour": "rise", "density": 0.5})},
            ),
            _track("pad", "pad", "Pad", "Atmos Pad", "pad", 0.45, {"A": ("pad", {})}),
            _track("impact", "fx", "Impact", "Impact", "sub", 0.5, _HIT),
        ),
        "shape": (
            _row(
                "Intro",
                16,
                1,
                0.2,
                "hats",
                {"pad": "A"},
                ("sweep", 0.1, 0.6),
            ),
            _row(
                "Build",
                8,
                1,
                0.6,
                "build",
                {"pad": "A", "lead": "A"},
                ("take", "riser", "up", 4, "end"),
                ("cut",),
            ),
            _row(
                "Drop",
                8,
                4,
                1.0,
                "main",
                {"sub": "A", "wobble": "A", "impact": "A"},
            ),
            _row(
                "Breakdown",
                16,
                1,
                0.3,
                None,
                {"pad": "A", "lead": "A"},
                ("enter", "lead", 9),
                ("take", "sweep", "down", 4, "start"),
            ),
            _row(
                "Build B",
                8,
                1,
                0.65,
                "build",
                {"pad": "A", "lead": "A"},
                ("take", "riser", "up", 4, "end"),
                ("cut",),
            ),
            # call and response: the growl in the first half of every pass,
            # the triplet wobble in the second
            _row(
                "Drop B",
                8,
                4,
                1.0,
                "riddim",
                {"sub": "A", "growl": "A", "triplet": "A", "impact": "A"},
                ("span", "growl", 0, 4),
                ("enter", "triplet", 5),
            ),
            _row(
                "Outro",
                16,
                1,
                0.2,
                "hats",
                {"pad": "A", "sub": "A"},
                ("sweep", 0.6, 0.1),
            ),
        ),
        "long": ("Intro", "Drop", "Drop B"),
    },
    # Synthwave / 80s (~100, straight): a drum machine through a gated room,
    # a sequenced bass in sixteenths, a chorused poly pad, brass stabs, lush
    # strings and a bright saw lead in the choruses; a bridge with no drums
    # swelling into the last chorus.
    "synthwave": {
        "label": "Synthwave",
        "blurb": "Synthwave: gated drums, a sequenced bass, chorused pads, brass",
        "bpm": 100.0,
        "bpm_range": (84.0, 112.0),
        "keys": (
            ("A1", "minor"),
            ("E1", "minor"),
            ("F#1", "minor"),
            ("D2", "dorian"),
            ("C2", "minor"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Synthwave",
        "kick": (0, 8),
        "progressions": (
            ((1, 6, 3, 7), 4),
            ((6, 4, 1, 5), 4),
            ((1, 7, 6, 7), 4),
            ((4, 5, 1, 1), 4),
        ),
        # the Gated Verb chain (fxrack.CHAIN_PRESETS["gated_verb"]): a big
        # room the gate shuts the moment a hit's level falls
        "drums_fx": (
            _HP,
            {"kind": "reverb", "params": {"decay": 2.5, "mix": 0.5, "size": 0.9}},
            {
                "kind": "vgate",
                "params": {
                    "threshold": -24.0,
                    "ratio": 20.0,
                    "depth": 70.0,
                    "release": 0.03,
                },
            },
        ),
        "drums": {
            "intro": (("beat", "synthwave", 8), ("thin", "kickhat")),
            "main": (("beat", "synthwave", 8),),
            "chorus": (("beat", "synthpop", 8), ("fill", 0.5, "toms")),
        },
        "tracks": (
            # the sequencer's bass: sixteenths, the root as a pedal between
            # the chord's tones — on the kick too, so it is tucked
            _track(
                "bass",
                "bass",
                "Bass",
                "Eighties Arp Bass",
                "bass",
                0.85,
                {"A": ("arp", {"mode": "pedal", "rate": 1, "octaves": 2})},
                fx=(_TUCK,),
            ),
            _track(
                "pad", "pad", "Pad", "Poly Chorus Pad", "pad", 0.5, {"A": ("pad", {})}
            ),
            _track(
                "brass",
                "chords",
                "Brass",
                "Poly Brass Stab",
                "chords",
                0.6,
                {
                    "A": ("chords", {"rhythm": "push", "voicing": "close"}),
                    "B": ("chords", {"rhythm": "stabs", "voicing": "close"}),
                },
            ),
            _track(
                "lead",
                "lead",
                "Lead",
                "Bright Saw Lead",
                "lead",
                0.65,
                {"A": ("lead", {"contour": "arch", "density": 0.6})},
            ),
            _track(
                "strings",
                "pad",
                "Strings",
                "Lush Strings",
                "pad",
                0.45,
                {"A": ("pad", {"voicing": "wide"})},
            ),
        ),
        "shape": (
            _row(
                "Intro",
                8,
                1,
                0.2,
                "intro",
                {"pad": "A", "strings": "A"},
                ("sweep", 0.15, 0.7),
            ),
            _row(
                "Verse",
                16,
                1,
                0.5,
                "main",
                {"bass": "A", "pad": "A", "brass": "A"},
                ("enter", "brass", 9),
            ),
            _row(
                "Chorus",
                8,
                2,
                0.9,
                "chorus",
                {"bass": "A", "pad": "A", "brass": "B", "lead": "A", "strings": "A"},
            ),
            _row(
                "Verse B",
                16,
                1,
                0.55,
                "main",
                {"bass": "A", "pad": "A", "brass": "A", "strings": "A"},
            ),
            _row(
                "Bridge",
                8,
                1,
                0.35,
                None,
                {"pad": "A", "strings": "A", "lead": "A"},
                ("take", "swell", "up", 2, "end"),
            ),
            _row(
                "Last Chorus",
                8,
                3,
                1.0,
                "chorus",
                {"bass": "A", "pad": "A", "brass": "B", "lead": "A", "strings": "A"},
            ),
            _row(
                "Outro",
                8,
                1,
                0.2,
                "intro",
                {"pad": "A", "strings": "A"},
                ("sweep", 0.7, 0.1),
            ),
        ),
        "long": ("Verse", "Chorus", "Last Chorus"),
    },
    # Future bass (~150, half time): supersaw chords squashed and pumped
    # hard on every beat (the Future Pump chain), vocal-like chops, a bent
    # pluck, a sub; a build whose snare roll climbs into each drop, an
    # impact on the first and a sub drop under the second.
    "future_bass": {
        "label": "Future Bass",
        "blurb": "Future bass: pumped supersaw chords, vocal chops, a half-time drop",
        "bpm": 150.0,
        "bpm_range": (140.0, 160.0),
        "keys": (
            ("C2", "major"),
            ("F1", "major"),
            ("A1", "minor"),
            ("D2", "major"),
            ("G1", "major"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Future Bass",
        "kick": (0, 10),
        "progressions": (
            ((4, 5, 6, 1), 8),
            ((6, 4, 1, 5), 8),
            ((1, 5, 6, 4), 8),
            ((4, 1, 5, 6), 8),
        ),
        # the snare's big room: a short one on the whole bus
        "drums_fx": (
            _HP,
            {"kind": "reverb", "params": {"decay": 2.0, "mix": 0.18, "size": 0.8}},
        ),
        "drums": {
            "hats": (("beat", "future-bass", 8), ("thin", "hats")),
            "main": (("beat", "future-bass", 8), ("fill", 0.5, "roll")),
            "build": (("beat", "future-build", 8),),
        },
        "tracks": (
            _track(
                "chords",
                "chords",
                "Chords",
                "Supersaw Chord Stab",
                "chords",
                0.65,
                {"A": ("chords", {"rhythm": "held", "voicing": "wide"})},
            ),
            _track(
                "sub",
                "bass",
                "Sub",
                "Half-Time Sub",
                "sub",
                1.0,
                {"A": ("bass_root", {})},
                fx=(_TUCK,),
            ),
            _track(
                "chops",
                "vocal",
                "Chops",
                "Vocal Chop Lead",
                "vocal",
                0.7,
                {"A": ("chops", {})},
            ),
            _track(
                "pluck",
                "arp",
                "Pluck",
                "Bent Pluck",
                "arp",
                0.55,
                {"A": ("arp", {"mode": "updown", "rate": 2, "octaves": 2})},
            ),
            _track("pad", "pad", "Pad", "Future Pad", "pad", 0.45, {"A": ("pad", {})}),
        ),
        "shape": (
            _row(
                "Intro",
                8,
                2,
                0.2,
                "hats",
                {"pad": "A", "pluck": "A"},
                ("sweep", 0.1, 0.6),
            ),
            _row(
                "Verse",
                16,
                1,
                0.45,
                "main",
                {"chords": "A", "sub": "A", "pluck": "A"},
                ("enter", "pluck", 9),
            ),
            _row(
                "Build",
                8,
                1,
                0.65,
                "build",
                {"chords": "A", "chops": "A"},
                ("lane", "chords", "cutoff", ((0.0, 0.2), (1.0, 0.9))),
                ("take", "riser", "up", 4, "end"),
            ),
            _row(
                "Drop",
                8,
                4,
                1.0,
                "main",
                {"chords": "A", "sub": "A", "chops": "A", "pluck": "A"},
                ("take", "impact", "down", 2, "start"),
            ),
            _row(
                "Breakdown",
                16,
                1,
                0.3,
                None,
                {"pad": "A", "chops": "A"},
                ("enter", "chops", 9),
                ("take", "sweep", "down", 4, "start"),
            ),
            _row(
                "Build B",
                8,
                1,
                0.7,
                "build",
                {"chords": "A", "chops": "A", "pad": "A"},
                ("take", "riser", "up", 4, "end"),
                ("cut",),
            ),
            _row(
                "Drop B",
                8,
                4,
                1.0,
                "main",
                {"chords": "A", "sub": "A", "chops": "A", "pluck": "A", "pad": "A"},
                ("take", "subdrop", "down", 2, "start"),
            ),
            _row(
                "Outro",
                16,
                1,
                0.2,
                "hats",
                {"pad": "A", "chords": "A"},
                ("sweep", 0.6, 0.1),
            ),
        ),
        "long": ("Verse", "Drop", "Drop B"),
    },
    # --- lo-fi, ambient, chiptune, hardstyle, phonk -----------------------
    # Lo-fi hip-hop (~80, swung hard): a dusty beat through the same tape as
    # the keys, FM e-piano chords pushed off the beat, a drifting jazz pad, a
    # soft sub under the kick, a muted pluck answering in the hooks.
    "lofi": {
        "label": "Lo-Fi Hip-Hop",
        "blurb": "Lo-fi hip-hop: a swung, dusty beat, e-piano sevenths, a soft sub",
        "bpm": 80.0,
        "bpm_range": (70.0, 92.0),
        "keys": (
            ("D2", "dorian"),
            ("A1", "minor"),
            ("F1", "major"),
            ("E1", "minor"),
            ("C2", "dorian"),
        ),
        "swing": 0.0,
        "drum_swing": 0.24,
        "kit": "Lo-Fi",
        "kick": (0, 10),
        "progressions": (
            (((2, "7th"), (5, "7th"), (1, "9th"), (1, "7th")), 4),
            (((1, "9th"), (4, "7th")), 2),
            (((6, "7th"), (2, "7th"), (5, "7th"), (1, "9th")), 4),
            (((1, "7th"), (6, "7th"), (4, "9th"), (5, "7th")), 4),
        ),
        # the Dusty Tape chain (fxrack.CHAIN_PRESETS["dusty_tape"]) on the
        # whole kit: the beat and the keys on the same worn tape
        "drums_fx": (
            _HP,
            {
                "kind": "tape",
                "params": {
                    "drive": 4.0,
                    "bump": 2.0,
                    "tone": 0.45,
                    "wow": 0.5,
                    "flutter": 0.35,
                    "hiss": 0.25,
                    "mix": 1.0,
                },
            },
            {"kind": "lowpass", "params": {"cutoff": 5500.0, "q": 0.7}},
            {"kind": "bitcrush", "params": {"bits": 12.0, "downsample": 1.0}},
        ),
        "drums": {
            "intro": (("beat", "lofi-chill", 8),),
            "main": (("beat", "lofi", 8),),
            "hook": (("beat", "lofi", 8), ("fill", 0.5, "quiet")),
        },
        "tracks": (
            _track(
                "keys",
                "chords",
                "Keys",
                "Dusty Keys",
                "chords",
                0.7,
                {
                    "A": ("chords", {"rhythm": "push", "voicing": "close"}),
                    "B": ("chords", {"rhythm": "held", "voicing": "close"}),
                },
            ),
            _track(
                "pad", "pad", "Pad", "Jazz Chord Pad", "pad", 0.45, {"A": ("pad", {})}
            ),
            _track(
                "sub",
                "bass",
                "Sub",
                "Soft Sub",
                "sub",
                1.0,
                {"A": ("bass_2step", {})},
                fx=(_TUCK,),
            ),
            _track(
                "pluck",
                "lead",
                "Pluck",
                "Muted Pluck",
                "lead",
                0.6,
                {"A": ("lead", {"contour": "fall", "density": 0.5})},
            ),
        ),
        "shape": (
            _row(
                "Intro",
                8,
                1,
                0.2,
                "intro",
                {"keys": "B", "pad": "A"},
                ("sweep", 0.15, 0.6),
            ),
            _row(
                "Verse",
                16,
                1,
                0.5,
                "main",
                {"keys": "A", "sub": "A", "pluck": "A"},
                ("enter", "pluck", 9),
            ),
            _row(
                "Hook",
                8,
                2,
                0.9,
                "hook",
                {"keys": "A", "pad": "A", "sub": "A", "pluck": "A"},
            ),
            _row(
                "Verse B",
                16,
                1,
                0.55,
                "main",
                {"keys": "A", "sub": "A", "pad": "A"},
            ),
            _row(
                "Break",
                8,
                1,
                0.3,
                None,
                {"keys": "B", "pad": "A"},
                ("lane", "keys", "cutoff", ((0.0, 0.3), (1.0, 0.7))),
                ("take", "swell", "up", 2, "end"),
            ),
            _row(
                "Hook B",
                8,
                2,
                1.0,
                "hook",
                {"keys": "A", "pad": "A", "sub": "A", "pluck": "A"},
            ),
            _row(
                "Outro",
                8,
                1,
                0.2,
                "intro",
                {"keys": "B", "pad": "A"},
                ("sweep", 0.6, 0.1),
            ),
        ),
        "long": ("Verse", "Hook", "Hook B"),
    },
    # Ambient / cinematic (~70): sparse — the drums in half the song at
    # most. A shimmer bed and a drone open it; a scanning pad (its table
    # swept by a lane) and a glass bell over a pulse; low toms build into a
    # climax with a braam on its first beat; a vowel pad drifts in the
    # quiet between the climaxes.
    "ambient": {
        "label": "Ambient / Cinematic",
        "blurb": "Ambient / cinematic: evolving pads, a shimmer, a drone, a braam",
        "bpm": 70.0,
        "bpm_range": (60.0, 90.0),
        "keys": (
            ("D2", "minor"),
            ("A1", "minor"),
            ("C2", "lydian"),
            ("E1", "minor"),
            ("F1", "major"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Cinematic",
        "kick": (0, 8),
        "progressions": (
            ((1, 6, 4, 5), 8),
            ((1, 4), 8),
            ((6, 4, 1, 1), 8),
            ((1, 7, 6, 4), 8),
        ),
        # the Huge Hall chain on the whole kit: toms in a cathedral
        "drums_fx": (
            {"kind": "highpass", "params": {"cutoff": 30.0, "q": 0.7}},
            {"kind": "reverb", "params": {"decay": 6.0, "mix": 0.5, "size": 1.0}},
            {"kind": "width", "params": {"amount": 1.5}},
        ),
        "drums": {
            "pulse": (("beat", "ambient-pulse", 8),),
            "toms": (("beat", "cinematic-toms", 8),),
            "climax": (("beat", "cinematic-toms", 8), ("fill", 1.0, "toms")),
        },
        "tracks": (
            # held under the toms' kick as a bus compressor would hold it
            _track(
                "drone",
                "bass",
                "Drone",
                "Deep Drone",
                "bass",
                0.8,
                {"A": ("bass_root", {})},
                fx=(_TUCK,),
            ),
            _track(
                "shimmer",
                "pad",
                "Shimmer",
                "Shimmer Bed",
                "pad",
                0.5,
                {"A": ("pad", {"voicing": "wide"})},
            ),
            _track(
                "scan",
                "pad",
                "Scan Pad",
                "Scanning Pad",
                "pad",
                0.5,
                {"A": ("pad", {})},
            ),
            _track(
                "vowel",
                "pad",
                "Vowel Pad",
                "Vowel Drift Pad",
                "pad",
                0.45,
                {"A": ("pad", {})},
            ),
            _track(
                "bells",
                "lead",
                "Bells",
                "Glass Bell Texture",
                "lead",
                0.55,
                {"A": ("lead", {"contour": "fall", "density": 0.35})},
            ),
            _track("braam", "fx", "Braam", "Cinematic Braam", "sub", 0.6, _HIT),
        ),
        "shape": (
            _row(
                "Opening",
                16,
                1,
                0.15,
                None,
                {"shimmer": "A", "drone": "A"},
                ("sweep", 0.1, 0.5),
            ),
            _row(
                "Rise",
                16,
                1,
                0.4,
                "pulse",
                {"scan": "A", "drone": "A", "bells": "A"},
                ("enter", "bells", 9),
                ("lane", "scan", "wt_pos", ((0.0, 0.1), (1.0, 0.9))),
            ),
            _row(
                "Swell",
                8,
                1,
                0.65,
                "toms",
                {"scan": "A", "shimmer": "A", "drone": "A"},
                ("take", "swell", "up", 4, "end"),
            ),
            _row(
                "Climax",
                8,
                2,
                0.95,
                "climax",
                {"scan": "A", "shimmer": "A", "drone": "A", "bells": "A", "braam": "A"},
            ),
            _row(
                "Drift",
                16,
                1,
                0.3,
                None,
                {"vowel": "A", "shimmer": "A", "bells": "A"},
                ("take", "sweep", "down", 4, "start"),
            ),
            _row(
                "Swell B",
                8,
                1,
                0.7,
                "toms",
                {"vowel": "A", "scan": "A", "drone": "A"},
                ("take", "swell", "up", 4, "end"),
            ),
            _row(
                "Climax B",
                8,
                2,
                1.0,
                "climax",
                {
                    "scan": "A",
                    "vowel": "A",
                    "shimmer": "A",
                    "drone": "A",
                    "bells": "A",
                    "braam": "A",
                },
            ),
            _row(
                "Fade",
                16,
                1,
                0.1,
                None,
                {"shimmer": "A", "drone": "A"},
                ("sweep", 0.5, 0.05),
            ),
        ),
        # (the first climax too would pass the length cap at 70 bpm)
        "long": ("Climax B",),
    },
    # Chiptune / 8-bit (~140, straight): three channels and a noise one — a
    # square lead, a thinner pulse lead in the second verse, a fast
    # arpeggio (each chord as one channel's notes in turn, a sixteenth
    # apart), a crushed triangle bass bouncing on the root, a gritty
    # wavetable lead over the choruses' chase beat. Dry: no reverb.
    "chiptune": {
        "label": "Chiptune",
        "blurb": "Chiptune: square leads, a fast arp, a triangle bass, noise drums",
        "bpm": 140.0,
        "bpm_range": (120.0, 160.0),
        "keys": (
            ("C2", "major"),
            ("A1", "minor"),
            ("E1", "minor"),
            ("F1", "major"),
            ("G1", "mixolydian"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Chiptune",
        "kick": (0, 8),
        "progressions": (
            ((1, 5, 6, 4), 4),
            ((6, 4, 1, 5), 4),
            ((1, 4, 5, 1), 4),
            ((1, 6, 2, 5), 4),
        ),
        # the 8-Bit Crush chain on the kit
        "drums_fx": (
            {"kind": "bitcrush", "params": {"bits": 6.0, "downsample": 3.0}},
            {"kind": "highpass", "params": {"cutoff": 40.0, "q": 0.7}},
            {
                "kind": "clip",
                "params": {"drive": 0.0, "ceiling": -1.0, "soft": 0.3, "mix": 1.0},
            },
        ),
        "drums": {
            "intro": (("beat", "chiptune", 8), ("thin", "kickhat")),
            "main": (("beat", "chiptune", 8), ("fill", 0.5, "roll")),
            "chase": (("beat", "chip-chase", 8), ("fill", 0.5, "roll")),
        },
        "tracks": (
            _track(
                "lead",
                "lead",
                "Square",
                "Square Lead",
                "lead",
                0.6,
                {"A": ("lead", {"contour": "arch", "density": 0.7})},
            ),
            _track(
                "pulse",
                "lead",
                "Pulse",
                "Pulse Lead",
                "lead",
                0.55,
                {"A": ("lead", {"contour": "rise", "density": 0.6})},
            ),
            _track(
                "arp",
                "arp",
                "Arp",
                "Thin Pulse Arp",
                "arp",
                0.5,
                {"A": ("arp", {"mode": "up", "rate": 1, "octaves": 2})},
            ),
            # the triangle bounces root to chord tone in eighths, an octave
            # over the subs (the console's bass sits high)
            _track(
                "bass",
                "bass",
                "Triangle",
                "Triangle Bass",
                "bass",
                0.9,
                {"A": ("arp", {"mode": "pedal", "rate": 2, "octaves": 1})},
                fx=(_TUCK,),
            ),
            _track(
                "crush",
                "lead",
                "Crush",
                "Crush Lead",
                "lead",
                0.5,
                {"A": ("lead", {"contour": "rise", "density": 0.7})},
            ),
        ),
        "shape": (
            _row(
                "Intro",
                8,
                1,
                0.3,
                "intro",
                {"arp": "A", "bass": "A"},
            ),
            _row(
                "Verse",
                16,
                1,
                0.55,
                "main",
                {"lead": "A", "bass": "A", "arp": "A"},
                ("enter", "arp", 9),
            ),
            _row(
                "Chorus",
                8,
                2,
                0.9,
                "chase",
                {"crush": "A", "arp": "A", "bass": "A"},
            ),
            _row(
                "Verse B",
                16,
                1,
                0.55,
                "main",
                {"pulse": "A", "bass": "A", "arp": "A"},
            ),
            _row(
                "Bridge",
                8,
                1,
                0.35,
                None,
                {"arp": "A", "pulse": "A"},
                ("take", "sweep", "up", 4, "end"),
            ),
            _row(
                "Last Chorus",
                8,
                3,
                1.0,
                "chase",
                {"crush": "A", "lead": "A", "arp": "A", "bass": "A"},
            ),
            # the console's ending: the volume faded out, no filter
            _row(
                "Outro",
                8,
                1,
                0.2,
                "intro",
                {"arp": "A", "bass": "A"},
                ("lane", "arp", "gain", ((0.0, 0.5), (1.0, 0.0))),
                ("lane", "bass", "gain", ((0.0, 0.5), (1.0, 0.0))),
            ),
        ),
        "long": ("Verse", "Chorus", "Last Chorus"),
    },
    # Hardstyle (~150): the kick is the genre — the kit's long pitched kick
    # through the Hard Kick Drive chain on the bus; a reverse bass on every
    # offbeat between the kicks; a hard-synced screech in the drops; a
    # euphoric break where a supersaw lead enters over a pumped pad; a
    # rawstyle beat under the climax.
    "hardstyle": {
        "label": "Hardstyle",
        "blurb": "Hardstyle: a distorted pitched kick, a reverse bass, a sync screech",
        "bpm": 150.0,
        "bpm_range": (145.0, 160.0),
        "keys": (
            ("F1", "minor"),
            ("G1", "minor"),
            ("A1", "minor"),
            ("D2", "minor"),
            ("E1", "harmonic_minor"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Hardstyle",
        "kick": (0, 4, 8, 12),
        "progressions": (
            ((6, 4, 1, 5), 8),
            ((1, 6, 3, 7), 8),
            ((1, 7, 6, 7), 8),
            ((6, 7, 1, 1), 8),
        ),
        # the Hard Kick Drive chain (fxrack.CHAIN_PRESETS["hard_kick_drive"])
        "drums_fx": (
            {"kind": "drive", "params": {"amount": 6.0, "mix": 1.0, "mode": 1.0}},
            {
                "kind": "clip",
                "params": {"drive": 4.0, "ceiling": -7.0, "soft": 0.2, "mix": 1.0},
            },
            {"kind": "highpass", "params": {"cutoff": 30.0, "q": 0.7}},
            {
                "kind": "transient",
                "params": {"attack": 0.5, "sustain": 0.2, "output": 0.0},
            },
            {
                "kind": "clip",
                "params": {"drive": 0.0, "ceiling": -1.0, "soft": 0.6, "mix": 1.0},
            },
        ),
        "drums": {
            "intro": (("beat", "hardstyle", 8), ("thin", "kickhat")),
            "main": (("beat", "hardstyle", 8), ("fill", 0.5, "roll")),
            "raw": (("beat", "rawstyle", 8), ("fill", 0.5, "roll")),
            "build": (("build", 8),),
        },
        "tracks": (
            _track(
                "bass",
                "bass",
                "Reverse Bass",
                "Reverse Bass",
                "bass",
                0.8,
                {"A": ("bass_offbeat", {})},
            ),
            _track(
                "screech",
                "lead",
                "Screech",
                "Sync Screech",
                "lead",
                0.5,
                {"A": ("lead", {"contour": "rise", "density": 0.6})},
            ),
            _track(
                "lead",
                "lead",
                "Euphoric",
                "Euphoric Supersaw",
                "lead",
                0.6,
                {"A": ("lead", {"contour": "arch", "density": 0.6})},
            ),
            _track(
                "pad", "pad", "Pad", "Euphoric Pad", "pad", 0.45, {"A": ("pad", {})}
            ),
            _track("impact", "fx", "Impact", "Impact", "sub", 0.5, _HIT),
        ),
        "shape": (
            _row(
                "Intro",
                16,
                1,
                0.3,
                "intro",
                {"pad": "A"},
                ("sweep", 0.15, 0.6),
            ),
            _row(
                "Build",
                8,
                1,
                0.6,
                "build",
                {"pad": "A", "lead": "A"},
                ("take", "riser", "up", 4, "end"),
                ("cut",),
            ),
            _row(
                "Drop",
                8,
                4,
                1.0,
                "main",
                {"bass": "A", "screech": "A", "impact": "A"},
            ),
            _row(
                "Break",
                16,
                1,
                0.3,
                None,
                {"pad": "A", "lead": "A"},
                ("enter", "lead", 9),
                ("take", "sweep", "down", 4, "start"),
            ),
            _row(
                "Build B",
                8,
                1,
                0.7,
                "build",
                {"pad": "A", "lead": "A"},
                ("take", "riser", "up", 4, "end"),
                ("cut",),
            ),
            _row(
                "Climax",
                8,
                4,
                1.0,
                "raw",
                {"bass": "A", "lead": "A", "screech": "A", "impact": "A"},
            ),
            _row(
                "Outro",
                16,
                1,
                0.2,
                "intro",
                {"pad": "A"},
                ("sweep", 0.6, 0.1),
            ),
        ),
        "long": ("Drop", "Climax"),
    },
    # Phonk (~140, drift phonk / memphis): a cowbell melody — the defining
    # sound — over a distorted 808 that lands with the kick, rolling hats,
    # a hard clap on 2 and 4, a dark vowel pad, a hard lead in the hooks;
    # the whole kit through the Phonk Crush chain.
    "phonk": {
        "label": "Phonk",
        "blurb": "Phonk: a cowbell melody, a distorted 808, rolling hats, a hard clap",
        "bpm": 140.0,
        "bpm_range": (130.0, 145.0),
        "keys": (
            ("C#2", "phrygian"),
            ("F1", "minor"),
            ("G1", "phrygian"),
            ("A1", "minor"),
            ("D2", "minor"),
        ),
        "swing": 0.0,
        "drum_swing": 0.0,
        "kit": "Phonk",
        "kick": (0, 10),
        "progressions": (
            ((1, 6, 7, 1), 8),
            ((1, 2, 1, 7), 8),
            ((1, 1, 6, 5), 8),
            ((1, 4, 6, 5), 8),
        ),
        # the Phonk Crush chain (fxrack.CHAIN_PRESETS["phonk_crush"]) on
        # the kit: fuzzed, crushed, the ceiling
        "drums_fx": (
            {"kind": "drive", "params": {"amount": 3.0, "mix": 0.6, "mode": 2.0}},
            {"kind": "bitcrush", "params": {"bits": 10.0, "downsample": 2.0}},
            {
                "kind": "clip",
                "params": {"drive": 2.0, "ceiling": -7.0, "soft": 0.3, "mix": 1.0},
            },
            {"kind": "highpass", "params": {"cutoff": 30.0, "q": 0.7}},
            {
                "kind": "clip",
                "params": {"drive": 0.0, "ceiling": -1.0, "soft": 0.6, "mix": 1.0},
            },
        ),
        "drums": {
            "hats": (("beat", "drift-phonk", 8), ("thin", "hats")),
            "main": (("beat", "drift-phonk", 8),),
            "memphis": (("beat", "memphis", 8), ("fill", 0.5, "roll")),
        },
        "tracks": (
            # the cowbell plays short pitched hits, call and answer, a
            # stutter at each phrase's end: what a cowbell melody does
            _track(
                "cowbell",
                "lead",
                "Cowbell",
                "Phonk Cowbell",
                "lead",
                0.65,
                {"A": ("chops", {})},
            ),
            _track(
                "808",
                "bass",
                "808",
                "Phonk 808",
                "sub",
                1.0,
                {"A": ("bass_2step", {})},
                fx=(_TUCK,),
            ),
            _track(
                "pad", "pad", "Pad", "Dark Vowel Pad", "pad", 0.45, {"A": ("pad", {})}
            ),
            _track(
                "lead",
                "lead",
                "Lead",
                "Hard Phonk Lead",
                "lead",
                0.5,
                {"A": ("lead", {"contour": "fall", "density": 0.5})},
            ),
        ),
        "shape": (
            _row(
                "Intro",
                8,
                1,
                0.2,
                "hats",
                {"pad": "A", "cowbell": "A"},
                ("sweep", 0.2, 0.7),
            ),
            _row(
                "Verse",
                16,
                1,
                0.6,
                "main",
                {"cowbell": "A", "808": "A", "pad": "A"},
                ("take", "riser", "up", 4, "end"),
            ),
            _row(
                "Hook",
                8,
                2,
                1.0,
                "memphis",
                {"cowbell": "A", "808": "A", "lead": "A"},
            ),
            _row(
                "Verse B",
                16,
                1,
                0.6,
                "main",
                {"cowbell": "A", "808": "A", "pad": "A"},
                ("enter", "pad", 9),
            ),
            _row(
                "Break",
                8,
                1,
                0.3,
                None,
                {"pad": "A", "cowbell": "A"},
                ("take", "riser", "up", 4, "end"),
            ),
            _row(
                "Hook B",
                8,
                2,
                1.0,
                "memphis",
                {"cowbell": "A", "808": "A", "lead": "A", "pad": "A"},
            ),
            _row(
                "Outro",
                8,
                1,
                0.2,
                "hats",
                {"pad": "A", "cowbell": "A"},
                ("sweep", 0.7, 0.1),
            ),
        ),
        "long": ("Verse", "Hook", "Hook B"),
    },
}

# "From my loop": the shape a working loop is arranged into when the song
# has no style of its own — by muting, thinning and filtering what the loop
# holds, never by writing new music (the one generated thing is the fill).
# `drums` names a `drums.THIN` variant ("all": the loop's beat with a fill
# in bar 8); `roles` are the roles that play.
GENERIC_SHAPE = (
    {
        "name": "Intro",
        "bars": 8,
        "repeats": 1,
        "energy": 0.2,
        "drums": "hats",
        "roles": ("chords", "pad", "arp"),
        "moves": (("sweep", 0.12, 0.6),),
    },
    {
        "name": "Verse",
        "bars": 8,
        "repeats": 2,
        "energy": 0.5,
        "drums": "all",
        "roles": ("bass", "chords", "pad"),
        "moves": (),
    },
    {
        "name": "Build",
        "bars": 8,
        "repeats": 1,
        "energy": 0.65,
        "drums": "nokick",
        "roles": ("chords", "pad", "arp", "lead"),
        "moves": (("sweep", 0.3, 0.9), ("take", "riser", "up", 4, "end")),
    },
    {
        "name": "Drop",
        "bars": 8,
        "repeats": 2,
        "energy": 1.0,
        "drums": "all",
        "roles": ("bass", "chords", "pad", "arp", "lead", "vocal", "fx"),
        "moves": (("take", "impact", "down", 2, "start"),),
    },
    {
        "name": "Break",
        "bars": 8,
        "repeats": 1,
        "energy": 0.3,
        "drums": None,
        "roles": ("chords", "pad", "lead", "vocal"),
        "moves": (("sweep", 0.7, 0.25),),
    },
    {
        "name": "Drop B",
        "bars": 8,
        "repeats": 2,
        "energy": 1.0,
        "drums": "all",
        "roles": ("bass", "chords", "pad", "arp", "lead", "vocal", "fx"),
        "moves": (("take", "impact", "down", 2, "start"),),
    },
    {
        "name": "Outro",
        "bars": 8,
        "repeats": 1,
        "energy": 0.2,
        "drums": "hats",
        "roles": ("bass", "pad"),
        "moves": (("sweep", 0.6, 0.1),),
    },
)


def shape_of(style: str, variant: str = "standard") -> tuple[dict, ...]:
    """A style's shape; `variant` "long" doubles the sections the style
    names (a style without any has no long form)."""
    st = STYLES[style]
    rows = st["shape"]
    if variant != "long" or not st.get("long"):
        return rows
    return tuple(
        {**r, "repeats": r["repeats"] * 2} if r["name"] in st["long"] else r
        for r in rows
    )


def variants(style: str) -> tuple[str, ...]:
    return ("standard", "long") if STYLES[style].get("long") else ("standard",)


def bars_of(style: str, variant: str = "standard") -> int:
    return sum(r["bars"] * r["repeats"] for r in shape_of(style, variant))


def card(style: str) -> dict:
    """What the starter sheet draws for a style: its tempo, its usual key
    and every key its seed may draw (`key_names`), its length, and its
    shape's energy by section (the bar chart)."""
    st = STYLES[style]
    bars = bars_of(style)
    seconds = bars * 240.0 / st["bpm"]
    root, scale = st["keys"][0]
    return {
        "key": style,
        "label": st["label"],
        "blurb": st["blurb"],
        "bpm": st["bpm"],
        "root": root,
        "scale": scale,
        "key_names": [f"{r[:-1]} {s.replace('_', ' ')}" for r, s in st["keys"]],
        "bars": bars,
        "time": f"{int(seconds // 60)}:{int(seconds % 60):02d}",
        "sections": [
            {"name": r["name"], "bars": r["bars"] * r["repeats"], "energy": r["energy"]}
            for r in st["shape"]
        ],
        "tracks": [t["name"] for t in st["tracks"]],
        "long": bool(st.get("long")),
    }
