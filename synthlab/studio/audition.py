"""The Synths view's reference phrase.

The note grid lives in Perform. Synths is a sound-design bench, so its ▶ plays
a *fixed* phrase instead: swap patches and the phrase never moves, which is the
whole point — you are comparing sounds, not arrangements.

Nothing new renders these. `phrase_arrangement` returns a plain `Arrangement`
built from the current key, so the existing stem cache, curve and FX path all
apply unchanged.
"""

from __future__ import annotations

from dataclasses import replace

from ..analysis.features import Features
from ..sequencer import midi
from ..synth import Patch
from .soundmeta import derive_tags
from .state import Arrangement

# key -> (label, steps_per_bar, [(row, step)], slides)
# `row` indexes pitch_rows(): 0 is the highest of the six scale tones, 5 the
# lowest (the root). Writing phrases against the row index rather than a pitch
# name keeps them in key for free when the root or scale changes.
PHRASES: dict[str, dict] = {
    "hold": {
        "label": "hold",
        "steps": 4,
        "notes": [(5, 0), (3, 0), (1, 0)],  # root + third + fifth, one long stab
        "slides": [],
    },
    "arp": {
        "label": "arp",
        "steps": 8,
        "notes": [(5, 0), (3, 1), (1, 2), (0, 3), (1, 4), (3, 5), (5, 6), (3, 7)],
        "slides": [],
    },
    "riff": {
        "label": "riff",
        "steps": 8,
        "notes": [(5, 0), (5, 3), (4, 4), (5, 6)],
        "slides": [4, 6],  # 303-style glide into the turnaround
    },
    "stab": {
        "label": "stab",
        "steps": 8,
        "notes": [(5, 2), (3, 2), (1, 2), (5, 6), (2, 6), (0, 6)],
        "slides": [],
    },
}
DEFAULT = "hold"
# no phrase: Synths' ▶ plays the beat alone (`render_inputs`), to play the
# keys over just the drums. Not a phrase — every other bench render, and a
# sound's audition, falls back to DEFAULT under it.
NO_PHRASE = "none"
CHOICES = (*PHRASES, NO_PHRASE)


def has_slides(phrase: str) -> bool:
    """Does this phrase slide anywhere — the only notes the loop glides into
    (paramui.idle: glide is idle on a phrase without one)."""
    return bool(PHRASES.get(phrase, PHRASES[DEFAULT])["slides"])


# The grid's root is a *bass* root (A1 by default — it drove a bassline
# sequencer). Auditioning a patch down there renders a 55 Hz mush: three
# stacked sub notes through the filter read as static, and ~99% of the energy
# lands in the bottom FFT bin, so the spectrum display has nothing to draw.
# The chord pads already solve this by sitting at octave 3 whatever the grid
# root is; the phrase uses the same listening register.
LISTEN_OCTAVE = "3"

# One register does not suit every sound a breed card can hold: a bass two
# octaves above where it will be played is a thin lead, and a pad up there
# loses its body. A card heard *on its own* (the board's ▶) plays the phrase
# in the sound's own register instead. The pitches are the ones the library
# measures each family at (`library.FAMILIES[...]["pitch"]`, pinned in
# tests/studio/test_audition_register.py): keys and plucks share the lead's.
REGISTER_PITCH = {"bass": "A1", "pad": "C3", "lead": "C4"}


def register_of(
    patch: Patch, features: Features | None = None, layer_octaves: tuple = ()
) -> str:
    """The register a sound is auditioned in: "bass", "pad" or "lead".

    Read off what the breeder already measured, with the tags the library
    and the preset cards use (`soundmeta.derive_tags`): **bass** when over
    half the probe's energy sits under 120 Hz — below the A3 it was played
    at, so the sound lives under its key — or when the patch plays its main
    oscillator an octave down; **pad** when the measured attack is slow
    (> 0.15 s); everything else (leads, keys, plucks) is **lead**. On the
    library's own sounds, probed as a breed card is, that puts 15 of 16
    basses, 16 of 16 pads and 44 of 48 leads/keys/plucks in their family's
    register.

    `layer_octaves`: a layered Sound's follow octaves. One whose layer
    already sits under the main is never moved down to the bass register:
    its sub layer would land at A0 or below, where no speaker follows.

    `features` are the row's own (the breeder's probe at A3, which every
    breeding decision keeps using — see `explore/probe.py`); without them
    only the patch's octave can say "bass"."""
    tags = derive_tags(features) if features is not None else []
    low = "bass" in tags or int(patch.osc1_octave) < 0
    if low and min((0, *layer_octaves)) >= 0:
        return "bass"
    return "pad" if "pad" in tags else "lead"


def listening_root(root: str, register: str | None = None) -> str:
    """`root`'s pitch class in the audible register (A1 -> A3). With a
    `register` (`register_of`), in the octave nearest that register's
    pitch instead: A -> A1 for a bass, A2 for a pad, A3 for a lead."""
    pitch_class = root.rstrip("-0123456789")
    if register not in REGISTER_PITCH:
        return pitch_class + LISTEN_OCTAVE
    centre = midi(REGISTER_PITCH[register])
    # ties (a tritone either side) go to the lower octave: min keeps the first
    octave = min(range(7), key=lambda o: abs(midi(f"{pitch_class}{o}") - centre))
    return f"{pitch_class}{octave}"


def phrase_arrangement(
    arr: Arrangement,
    phrase: str,
    keep_drums: bool = False,
    register: str | None = None,
) -> Arrangement:
    """`arr`'s key and tempo, the phrase's notes, in the listening register
    (or a sound's own `register`: a breed card on its own).
    One bar; drums are dropped unless `keep_drums` (the vs-beat toggle) —
    plain bench previews render `parts=("synth",)` so the lanes would never
    be heard anyway."""
    spec = PHRASES.get(phrase) or PHRASES[DEFAULT]
    # imported here: arrange imports state, and state must not import arrange
    from .arrange import pitch_rows

    root = listening_root(arr.root, register)
    # pitch_rows reads root+scale off an Arrangement, so ask it about the
    # transposed key rather than re-deriving the scale here
    rows = pitch_rows(replace(arr, root=root))
    cells = [[rows[r], step] for r, step in spec["notes"] if r < len(rows)]
    return replace(
        arr,
        root=root,
        bars=1,
        steps_per_bar=spec["steps"],
        cells=cells,
        # the grid's cuts are not the phrase's: left in, a cut drawn in
        # Perform moved every bench render's key (and re-rendered its main
        # stem) though no phrase has two notes side by side to cut
        cuts=[],
        slides=list(spec["slides"]),
        velocities={},
        swing=0.0,
        arp_mode="off",
        arp_octaves=1,
        drums=list(arr.drums) if keep_drums else [],
    )
