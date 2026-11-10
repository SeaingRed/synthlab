"""Evocative names for bred patches.

Pure module: `Features` + a stable genome string in, a two-word name out.
Deterministic (same inputs -> same name) so the SpaceStore log, the tile,
and the adopted patch always agree. No I/O, no randomness — Rust-ready.
"""

from __future__ import annotations

import hashlib

from ..analysis.features import Features

ADJECTIVES: dict[str, list[str]] = {
    "dark": [
        "Velvet",
        "Umbra",
        "Charcoal",
        "Midnight",
        "Obsidian",
        "Deep",
        "Dusk",
        "Cinder",
        "Iron",
        "Sable",
        "Hollow",
        "Slate",
        "Mono",
        "Fathom",
        "Onyx",
        "Murk",
        "Shade",
        "Ember",
    ],
    "mid": [
        "Amber",
        "Drift",
        "Glass",
        "Copper",
        "Prism",
        "Wave",
        "Neon",
        "Field",
        "Echo",
        "Coil",
        "Pulse",
        "Vapor",
        "Halo",
        "Signal",
        "Flux",
        "Lumen",
        "Tide",
        "Arc",
    ],
    "bright": [
        "Static",
        "Crystal",
        "Solar",
        "Chrome",
        "Spark",
        "Ivory",
        "Laser",
        "Glint",
        "Aurora",
        "Shimmer",
        "Zenith",
        "Stellar",
        "Radiant",
        "Quartz",
        "Flash",
        "Nova",
        "Photon",
        "Gleam",
    ],
}

NOUNS: dict[str, list[str]] = {
    "deep": [
        "Depth",
        "Bass",
        "Root",
        "Floor",
        "Core",
        "Anchor",
        "Rumble",
        "Weight",
        "Abyss",
        "Sub",
        "Boom",
        "Ground",
        "Mass",
        "Bed",
        "Well",
        "Bottom",
        "Gravity",
        "Hull",
    ],
    "textured": [
        "Ember",
        "Grain",
        "Static",
        "Fuzz",
        "Gravel",
        "Crackle",
        "Dust",
        "Rust",
        "Fray",
        "Hiss",
        "Grit",
        "Ash",
        "Buzz",
        "Thorn",
        "Ripple",
        "Friction",
        "Noise",
        "Burr",
    ],
    "clean": [
        "Ray",
        "Tone",
        "Line",
        "Bell",
        "Air",
        "Chord",
        "Voice",
        "Wire",
        "Beam",
        "Note",
        "Song",
        "Call",
        "Glow",
        "Path",
        "Thread",
        "Stream",
        "Chime",
        "Curve",
    ],
}

_CENTROID_LO = 400.0
_CENTROID_HI = 1800.0
_BODY_THRESHOLD = 0.35
_TEXTURE_THRESHOLD = 0.3


def _adjective_bank(features: Features) -> str:
    if features.centroid < _CENTROID_LO:
        return "dark"
    if features.centroid < _CENTROID_HI:
        return "mid"
    return "bright"


def _noun_bank(features: Features) -> str:
    if features.sub_20_60 + features.sub_60_120 > _BODY_THRESHOLD:
        return "deep"
    if features.flatness > _TEXTURE_THRESHOLD:
        return "textured"
    return "clean"


def sound_name(features: Features, genome_key: str) -> str:
    digest = hashlib.sha1(genome_key.encode()).hexdigest()
    half = len(digest) // 2
    adj_slice, noun_slice = digest[:half], digest[half:]

    adjectives = ADJECTIVES[_adjective_bank(features)]
    nouns = NOUNS[_noun_bank(features)]

    adj = adjectives[int(adj_slice, 16) % len(adjectives)]
    noun = nouns[int(noun_slice, 16) % len(nouns)]
    return f"{adj} {noun}"
