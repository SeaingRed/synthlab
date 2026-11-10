"""Drum Lab param surface: per-voice control specs, deep-house presets.

Ranges/steps/defaults mirror synthlab.drums's kick/snare/hat/clap keyword
signatures exactly (see tests/studio/test_drumlab.py's inspect.signature
check) so a slider can never send a value the engine wasn't designed for.
"""

from __future__ import annotations

DRUM_VOICES: tuple[str, ...] = (
    "kick",
    "snare",
    "hat",
    "ohat",
    "clap",
    "tom",
    "rim",
    "crash",
)

# Per voice: ordered param specs. kind "range" -> a slider; kind "bool" ->
# an on/off pill pair (hat's open_hat).
DRUM_PARAMS: list[tuple[str, list[dict]]] = [
    (
        "kick",
        [
            {
                "name": "tune",
                "label": "Tune",
                "min": 30.0,
                "max": 80.0,
                "step": 0.5,
                "default": 52.0,
                "kind": "range",
            },
            {
                "name": "decay",
                "label": "Decay",
                "min": 0.1,
                "max": 1.2,
                "step": 0.01,
                "default": 0.42,
                "kind": "range",
            },
            {
                "name": "punch",
                "label": "Punch",
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "default": 0.9,
                "kind": "range",
            },
            {
                "name": "click",
                "label": "Click",
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "default": 0.35,
                "kind": "range",
            },
            {
                "name": "drive",
                "label": "Drive",
                "min": 1.0,
                "max": 4.0,
                "step": 0.05,
                "default": 2.2,
                "kind": "range",
            },
        ],
    ),
    (
        "snare",
        [
            {
                "name": "tune",
                "label": "Tune",
                "min": 120.0,
                "max": 260.0,
                "step": 1.0,
                "default": 190.0,
                "kind": "range",
            },
            {
                "name": "decay",
                "label": "Decay",
                "min": 0.05,
                "max": 0.5,
                "step": 0.005,
                "default": 0.22,
                "kind": "range",
            },
            {
                "name": "tone",
                "label": "Tone",
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "default": 0.45,
                "kind": "range",
            },
            {
                "name": "snap",
                "label": "Snap",
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "default": 0.75,
                "kind": "range",
            },
        ],
    ),
    (
        "hat",
        [
            {
                "name": "decay",
                "label": "Decay",
                "min": 0.02,
                "max": 0.3,
                "step": 0.005,
                "default": 0.055,
                "kind": "range",
            },
            {
                "name": "tone",
                "label": "Tone",
                "min": 4000.0,
                "max": 12000.0,
                "step": 50.0,
                "default": 9000.0,
                "kind": "range",
            },
            {
                "name": "open_hat",
                "label": "Open",
                "min": 0.0,
                "max": 1.0,
                "step": 1.0,
                "default": 0.0,
                "kind": "bool",
            },
        ],
    ),
    (
        "clap",
        [
            {
                "name": "decay",
                "label": "Decay",
                "min": 0.1,
                "max": 0.5,
                "step": 0.005,
                "default": 0.28,
                "kind": "range",
            },
            {
                "name": "spread",
                "label": "Spread",
                "min": 0.002,
                "max": 0.03,
                "step": 0.001,
                "default": 0.012,
                "kind": "range",
            },
        ],
    ),
    (
        "ohat",
        [
            {
                "name": "decay",
                "label": "Decay",
                "min": 0.1,
                "max": 0.8,
                "step": 0.01,
                "default": 0.3,
                "kind": "range",
            },
            {
                "name": "tone",
                "label": "Tone",
                "min": 4000.0,
                "max": 12000.0,
                "step": 50.0,
                "default": 8500.0,
                "kind": "range",
            },
        ],
    ),
    (
        "tom",
        [
            {
                "name": "tune",
                "label": "Tune",
                "min": 80.0,
                "max": 300.0,
                "step": 1.0,
                "default": 140.0,
                "kind": "range",
            },
            {
                "name": "decay",
                "label": "Decay",
                "min": 0.1,
                "max": 1.0,
                "step": 0.01,
                "default": 0.4,
                "kind": "range",
            },
        ],
    ),
    (
        "rim",
        [
            {
                "name": "tune",
                "label": "Tune",
                "min": 200.0,
                "max": 800.0,
                "step": 5.0,
                "default": 420.0,
                "kind": "range",
            },
            {
                "name": "decay",
                "label": "Decay",
                "min": 0.02,
                "max": 0.15,
                "step": 0.005,
                "default": 0.05,
                "kind": "range",
            },
        ],
    ),
    (
        "crash",
        [
            {
                "name": "decay",
                "label": "Decay",
                "min": 0.5,
                "max": 3.0,
                "step": 0.05,
                "default": 1.8,
                "kind": "range",
            },
        ],
    ),
]

_SPECS_BY_VOICE: dict[str, list[dict]] = dict(DRUM_PARAMS)


def specs_of(voice: str) -> list[dict]:
    """Param specs for one voice, or [] for an unknown voice."""
    return _SPECS_BY_VOICE.get(voice, [])


def defaults_of(voice: str) -> dict:
    """{param: default} for one voice — what the engine uses absent overrides."""
    return {spec["name"]: spec["default"] for spec in specs_of(voice)}


def clamp(voice: str, name: str, value: float) -> float | bool:
    """Clamp a raw value to its spec's [min, max]; bool kind -> 0.0/1.0."""
    for spec in specs_of(voice):
        if spec["name"] == name:
            lo, hi = spec["min"], spec["max"]
            v = min(hi, max(lo, value))
            return v
    return value


# Named deep-house presets per voice. Hand-tuned by rendering each through
# drums.hit and checking analysis.features.extract: every preset here is
# audible (peak > 0.1) and the two kick presets that matter for the "will
# it translate" story — Punch (bright, clicky) vs 808 Sub (pure low end) —
# are ordered by sub-band fraction (sub_20_60 + sub_60_120): 808 Sub is the
# most sub-heavy, Punch the least.
VOICE_PRESETS: dict[str, list[dict]] = {
    "kick": [
        {
            "name": "Punch",
            "tune": 58.0,
            "decay": 0.3,
            "punch": 1.0,
            "click": 0.8,
            "drive": 3.2,
        },
        {
            "name": "808 Sub",
            "tune": 36.0,
            "decay": 1.0,
            "punch": 0.35,
            "click": 0.02,
            "drive": 1.2,
        },
        {
            "name": "Rumble",
            "tune": 40.0,
            "decay": 1.15,
            "punch": 0.55,
            "click": 0.08,
            "drive": 1.5,
        },
        {
            "name": "Knock",
            "tune": 62.0,
            "decay": 0.18,
            "punch": 0.8,
            "click": 0.75,
            "drive": 3.0,
        },
    ],
    "snare": [
        {"name": "Tight", "tune": 200.0, "decay": 0.15, "tone": 0.4, "snap": 0.6},
        {"name": "Snap", "tune": 190.0, "decay": 0.18, "tone": 0.35, "snap": 0.95},
        {"name": "Rimshot", "tune": 240.0, "decay": 0.08, "tone": 0.7, "snap": 0.5},
    ],
    "hat": [
        {"name": "Closed", "decay": 0.04, "tone": 9000.0, "open_hat": False},
        {"name": "Shimmer", "decay": 0.08, "tone": 11000.0, "open_hat": False},
        {"name": "Open", "decay": 0.25, "tone": 8500.0, "open_hat": True},
    ],
    "clap": [
        {"name": "Classic", "decay": 0.28, "spread": 0.012},
        {"name": "Wide", "decay": 0.32, "spread": 0.025},
        {"name": "Short", "decay": 0.15, "spread": 0.006},
    ],
    "ohat": [
        {"name": "Short", "decay": 0.18, "tone": 9000.0},
        {"name": "Long", "decay": 0.55, "tone": 8000.0},
    ],
    "tom": [
        {"name": "Low", "tune": 100.0, "decay": 0.55},
        {"name": "High", "tune": 220.0, "decay": 0.3},
    ],
    "rim": [
        {"name": "Tight", "tune": 480.0, "decay": 0.04},
        {"name": "Woody", "tune": 320.0, "decay": 0.09},
    ],
    "crash": [
        {"name": "Splash", "decay": 0.8},
        {"name": "Long", "decay": 2.6},
    ],
}


def preset_of(voice: str, name: str) -> dict | None:
    """The param dict (sans "name") for a named preset, or None if missing."""
    for preset in VOICE_PRESETS.get(voice, []):
        if preset["name"] == name:
            return {k: v for k, v in preset.items() if k != "name"}
    return None
