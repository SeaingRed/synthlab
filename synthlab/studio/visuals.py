"""The Visuals page's variations: what the gallery lists (views/visuals.html).

The drawing is the client's — `static/visuals.js` (and `visuals2.js`, the
second batch) registers each id with its `draw` — and this table is only
what the page says about each: its name, what it draws, a line about it
and its family (the gallery's groups, in `FAMILIES` order), so the cards
(and their `data-cmd`s) are rendered here like every other page's
controls. `tests/studio/test_visuals.py` holds the two lists to each
other: a variation added to one and not the other fails there.
"""

from __future__ import annotations

from typing import NamedTuple

FAMILIES: tuple[str, ...] = ("Scopes", "Spectrums", "Meters", "Generative")


class Variation(NamedTuple):
    id: str
    name: str
    kind: str  # "scope" | "spectrum" | "both": what it is drawn from
    note: str
    family: str  # one of FAMILIES: where the gallery lists it


def _family(family: str, *rows: tuple[str, str, str, str]) -> tuple[Variation, ...]:
    return tuple(Variation(*row, family) for row in rows)


VARIATIONS: tuple[Variation, ...] = (
    *_family(
        "Scopes",
        ("classic-scope", "Classic scope", "scope", "The trace as it was."),
        (
            "phosphor",
            "Phosphor",
            "scope",
            "A CRT trace that leaves an afterglow on a faint graticule.",
        ),
        (
            "lissajous",
            "Lissajous",
            "scope",
            "The signal against itself a moment later, drawn X/Y.",
        ),
        (
            "triggered",
            "Triggered scope",
            "scope",
            "Held on a rising zero cross: four cycles that stand still.",
        ),
        (
            "goniometer",
            "Goniometer",
            "scope",
            "True left against right, mid up and side across, with the correlation.",
        ),
        (
            "polar",
            "Polar scope",
            "scope",
            "Six cycles wound round a ring: a petal each, turning on the kick.",
        ),
        (
            "afterimage",
            "Afterimage",
            "scope",
            "A Lissajous whose last few figures cool and fade behind it.",
        ),
        (
            "horizon",
            "Horizon",
            "scope",
            "Slices of the wave flying in from the horizon, a terrain.",
        ),
    ),
    *_family(
        "Spectrums",
        (
            "classic-spectrum",
            "Classic spectrum",
            "spectrum",
            "The one signal graphic: a smooth area under a thin stroke.",
        ),
        (
            "mirror",
            "Mirror bars",
            "spectrum",
            "Bars mirrored about the middle; their caps fall with gravity.",
        ),
        (
            "breath",
            "Breath",
            "spectrum",
            "A log-frequency area whose glow breathes on the kick.",
        ),
        ("radial", "Radial", "spectrum", "The spectrum around a ring that pumps."),
        (
            "waterfall",
            "Waterfall",
            "spectrum",
            "A scrolling spectrogram: time runs right to left, bass at the foot.",
        ),
        (
            "ridgeline",
            "Ridgeline",
            "spectrum",
            "The last few seconds of spectra, stacked into a mountain range.",
        ),
        (
            "surface",
            "Spectrum surface",
            "spectrum",
            "The last second of spectra as a 3D surface, slowly turning.",
        ),
        (
            "reflect",
            "Reflection bars",
            "spectrum",
            "Bars on a glossy floor, their caps falling slowly.",
        ),
        (
            "bloom",
            "Bloom",
            "spectrum",
            "Bars of light whose halos add up; the hottest tips run white.",
        ),
        (
            "octave",
            "1/3-octave",
            "spectrum",
            "An analyser on the ISO third-octave centres, with peak hold.",
        ),
        (
            "centroid",
            "Brightness ribbon",
            "spectrum",
            "The spectral centroid over time: high is bright, wide is loud.",
        ),
        (
            "chroma",
            "Chroma wheel",
            "spectrum",
            "The twelve pitch classes round the circle of fifths: the key.",
        ),
    ),
    *_family(
        "Meters",
        (
            "needles",
            "VU needles",
            "both",
            "Two needles with a little mass: the average and the peak.",
        ),
        (
            "ladder",
            "LED ladder",
            "spectrum",
            "Segmented LEDs, coloured like the meter.",
        ),
        (
            "lcd",
            "LCD panel",
            "both",
            "Seven segments: the note it hears, the peak in dB, two bars.",
        ),
        (
            "beatgrid",
            "Beat grid",
            "both",
            "Sixteen steps lit where the kicks and the hats land.",
        ),
        (
            "pitch",
            "Pitch trace",
            "scope",
            "The dominant pitch over time on note lanes, named in cents.",
        ),
    ),
    *_family(
        "Generative",
        (
            "particles",
            "Particles",
            "both",
            "A dust field that bursts on every kick.",
        ),
        (
            "tunnel",
            "Tunnel",
            "both",
            "A starfield that speeds up with the level and lurches on the kick.",
        ),
        (
            "plasma",
            "Plasma",
            "both",
            "A field the lows ring, the mids stir and the highs grain.",
        ),
        (
            "ripples",
            "Ripples",
            "both",
            "A kick drops a stone, the music a pebble now and then.",
        ),
    ),
)

IDS: frozenset[str] = frozenset(v.id for v in VARIATIONS)
