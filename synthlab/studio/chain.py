"""The Synths page's signal-chain bar: one line per stage, in the order the
engine processes the sound (Source -> Filter -> Vowel -> Amp -> Character ->
layer FX -> master), plus the modulators currently moving it.

It doubles as navigation (each stage links to its panel) and as a read-out of
the whole sound at a glance, so it is a region (`service.REGIONS`): re-sent
out of band whenever an edit moves what it shows — patch posts swap nothing
else.
"""

from __future__ import annotations

from ..params import MOD_SLOTS, slot_active
from .fxrack import FX_UNITS
from .paramui import FILTER_LABELS, MOD_SOURCE_LABELS, MOD_TARGET_LABELS

VOWELS = "aeiou"


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):  # automation arrays never reach here, but
        return default  # a summary must not be what breaks the page


def _hz(v: float) -> str:
    return f"{v / 1000:.2f} kHz" if v >= 1000 else f"{v:.0f} Hz"


def _sec(v: float) -> str:
    return f"{v * 1000:.0f} ms" if v < 1 else f"{v:.2f} s"


def _fx_line(chain) -> str:
    names = [FX_UNITS.get(u.get("kind"), {}).get("label", "?") for u in chain or []]
    return " → ".join(names) if names else "none"


def _rate(hz, sync) -> str:
    """An LFO's speed as the user set it: the tempo division when synced
    (the Hz field is then ignored by the engine, synth.at_bpm), else Hz."""
    return str(sync) if sync and sync != "off" else f"{_f(hz):.2g} Hz"


def motion_lines(p) -> list[str]:
    """What is moving the sound right now, one short phrase each."""
    out = []
    for slot in MOD_SLOTS:
        if slot_active(p, slot):  # the engine's own rule: it plays this route
            src = getattr(p, f"{slot}_src")
            amt = _f(getattr(p, f"{slot}_amt"))
            dst = getattr(p, f"{slot}_dst")
            out.append(
                f"{MOD_SOURCE_LABELS.get(src, src)} → "
                f"{MOD_TARGET_LABELS.get(dst, dst)} {amt:+.0%}"
            )
    if _f(p.filter_lfo_depth) > 0 and p.filter_type not in (None, "", "none"):
        out.append(f"Filter LFO {_rate(p.filter_lfo_rate, p.filter_lfo_sync)} → cutoff")
    if _f(p.vibrato_depth) > 0:
        out.append(f"Vibrato {_f(p.vibrato_rate):.2g} Hz")
    if _f(p.tremolo_depth) > 0:
        out.append(f"Tremolo {_f(p.tremolo_rate):.2g} Hz")
    if _f(p.pwm_depth) > 0:
        out.append(f"Pulse width {_f(p.pwm_rate):.2g} Hz")
    if _f(p.pitch_env) != 0:
        out.append(f"Pitch envelope {_f(p.pitch_env):+.0f} st")
    return out


def chain_summary(p, fx_layer, fx_master) -> list[dict]:
    """[{id, title, text, off}] for each stage; `id` is the panel anchor."""
    osc2 = p.osc2 or "off"
    source = f"{p.osc1}" + (f" + {osc2}" if p.osc2 else " · osc 2 off")

    def _ft(kind) -> str:
        return FILTER_LABELS.get(kind, str(kind))

    filt = (
        "off"
        if p.filter_type in (None, "", "none")
        else f"{_ft(p.filter_type)} · {_hz(_f(p.cutoff))}"
    )
    # filter 2 after it (→) or beside it (‖)
    if p.filter2_route in ("serial", "parallel") and p.filter2_type != "none":
        join = " → " if p.filter2_route == "serial" else " ‖ "
        filt += f"{join}{_ft(p.filter2_type)} · {_hz(_f(p.filter2_cutoff))}"
    amt = _f(p.formant_amount)
    vowel_pos = min(max(_f(p.formant_vowel), 0.0), 1.0)
    vowel = "off" if amt <= 0 else f"'{VOWELS[round(vowel_pos * 4)]}' · {amt:.0%}"
    amp = f"{_sec(_f(p.attack))} in · {_sec(_f(p.release))} out"
    voices = int(_f(p.unison, 1))
    drive = _f(p.drive, 1.0)
    character = f"{voices} voice{'s' if voices != 1 else ''}" + (
        f" · drive {drive:.1f}" if drive > 1.0 else ""
    )
    return [
        {"id": "panel-source", "title": "Source", "text": source, "off": False},
        {"id": "panel-filter", "title": "Filter", "text": filt, "off": filt == "off"},
        {"id": "panel-vowel", "title": "Vowel", "text": vowel, "off": amt <= 0},
        {"id": "panel-amp-envelope", "title": "Amp", "text": amp, "off": False},
        {
            "id": "panel-character",
            "title": "Character",
            "text": character,
            "off": False,
        },
        {
            "id": "fx-rack",
            "title": "Layer FX",
            "text": _fx_line(fx_layer),
            "off": not fx_layer,
        },
        {
            "id": "fx-rack",
            "title": "Master",
            "text": _fx_line(fx_master),
            "off": not fx_master,
        },
    ]
