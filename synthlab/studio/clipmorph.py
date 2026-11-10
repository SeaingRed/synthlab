"""Gemini's half of the clip hub's morph: "make it darker and half-time,
like the Trance kit", "turn it into a trance pluck arp".

One `generate_content` call that LISTENS — the clip's first seconds as a
short WAV (`audio_wav`: at most `MORPH_SECONDS`, 16 kHz mono, like the
sound agent's clip) — and reads the player's words, what the analysis
heard and the studio's catalogue (kits, beat styles, note sources,
sounds, FX chain presets), and answers with a plan as JSON against
`schema()` (`response_json_schema`). The plan is NOT trusted: the route
hands it to `clipmake.clean_plan`, which keeps only names the studio has
and clamps every number, and the player's own picks (`clipmake.locked`)
win over it. Nothing is written until Apply.

The key, model and timeout are the sound agent's (`agent.config.
load_config`: `GEMINI_API_KEY` in the checkout's `.env`), so is the scrub
(`config.scrub`) of everything that came off the network, and so is the
SDK client: `takecoach.client_for`, read at call time — the suite's guard
(`tests/conftest.py`) replaces it, so no test reaches Gemini. Not logged
in `agent_log.json`, not counted against the agent's budget; the tokens
an answer cost are shown with it.
"""

from __future__ import annotations

import io
import json
import math
import wave

import numpy as np

from ..core import SR
from . import clipmake, takecoach
from .agent.client import AgentError, usage_split
from .agent.config import AgentConfig, scrub

MORPH_SECONDS = 8.0  # the most of a clip that is sent
MORPH_RATE = 16000
MAX_DESCRIBE = 300  # characters of the player's words

SYSTEM = """You are a music producer in a small web studio. The user has a \
short audio clip (you can hear it) and wants it turned into something they \
can play: a BEAT on the drum grid, NOTES (a part on the note grid, played by \
a synth or sampler sound), or a SOUND (the clip itself as a playable sampler \
instrument). Read their words, listen, and choose a plan from the catalogue.

Rules:
- Use ONLY names from the catalogue for kit, style, notes_from, sound and \
fx. Never invent a name.
- target: "beat" for drums / groove / kit / rhythm requests; "notes" for a \
bassline, arp, pluck, lead, chords, melody played by a sound; "sound" when \
they want the clip itself on the keys.
- half-time / double-time -> feel; "busier", "more hats", "fuller" -> \
density busier; "sparser", "stripped", "minimal" -> sparser; amount 0.1-0.6.
- "like <a genre or kit>" -> the closest kit and/or beat style in the \
catalogue. kit "own" keeps the clip's own drum sounds.
- For notes: notes_from "melody" follows the clip's tune, "rhythm" puts its \
rhythm on the key's root, a generator (bass_root, bass_rolling, chords, pad, \
arp, lead) writes a new part in the clip's key. Pick a sound whose name and \
family fit the request (a pluck for "pluck", a pad for "pad", a sub or bass \
for "bass"); "darker" / "warmer" / "brighter" steer the sound and the fx.
- transpose in semitones (12 = an octave up), 0 unless asked.
- summary: one short line saying what you made. why: up to 4 short lines \
naming what you heard in the clip and why you chose this.
- Keep every choice the user locked exactly as given."""


def schema() -> dict:
    """The JSON shape asked for: every choice an enum of the studio's own
    names (the sound's name is a string: the catalogue lists them)."""

    def enum(values) -> dict:
        return {"type": "string", "enum": list(values)}

    return {
        "type": "object",
        "properties": {
            "target": enum(clipmake.TARGETS),
            "kit": enum(clipmake.KITS),
            "style": enum(clipmake.STYLES),
            "density": enum(clipmake.DENSITIES),
            "amount": {"type": "number"},
            "swing": {"type": "number"},
            "feel": enum(clipmake.FEELS),
            "fill": enum(clipmake.FILLS),
            "loop_tempo": {"type": "boolean"},
            "notes_from": enum(clipmake.NOTE_SOURCES),
            "sound": {
                "type": "object",
                "properties": {
                    "source": enum(clipmake.SOUND_SOURCES),
                    "name": {"type": "string"},
                },
                "required": ["source"],
            },
            "sound_mode": enum(clipmake.SOUND_MODES),
            "transpose": {"type": "integer"},
            "snap": enum(clipmake.SNAPS),
            "fx": enum(clipmake.FXS),
            "summary": {"type": "string"},
            "why": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        },
        "required": ["target", "summary"],
    }


def available(config: AgentConfig | None = None) -> str | None:
    """None when Gemini can be asked, else why not (a sentence)."""
    return takecoach.available(config)


def clean_describe(text) -> str:
    return " ".join(text.split())[:MAX_DESCRIBE] if isinstance(text, str) else ""


def audio_wav(audio: np.ndarray, sr: int = SR) -> bytes:
    """The clip's first `MORPH_SECONDS` from its first sound, 16 kHz mono
    16-bit (~256 KB at most): what Gemini hears."""
    from scipy.signal import resample_poly

    x = np.asarray(audio, dtype=np.float64)
    mono = x.mean(axis=1) if x.ndim == 2 else x
    loud = np.flatnonzero(np.abs(mono) > 1e-3)
    start = int(loud[0]) if len(loud) else 0
    seg = mono[start : start + int(MORPH_SECONDS * sr)]
    g = math.gcd(MORPH_RATE, int(sr))
    y = resample_poly(seg, MORPH_RATE // g, int(sr) // g) if len(seg) else seg
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 1.0:
        y = y / peak
    pcm = (np.clip(y, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(MORPH_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def brief(info: dict) -> str:
    """The request's text: the words, the clip, the loop, what is locked
    and the catalogue."""
    cat = info["catalogue"]
    lines = [
        f'The user\'s words: "{info["describe"]}"',
        "",
        f"The clip: “{info['name']}”, {info['kind']}, {info['seconds']:.1f} s; "
        f"tempo {info['tempo']}; key {info['key'] or 'none heard'}.",
        f"What the analysis heard: {info['heard']}",
        f"The loop now: {info['loop']}.",
    ]
    if info.get("locked"):
        locks = ", ".join(f"{k} = {v}" for k, v in info["locked"].items())
        lines.append(f"The user locked (keep exactly): {locks}.")
    lines += [
        "",
        "Catalogue:",
        f"- kit: own (the clip's own hits), keep (the kit now), "
        f"{', '.join(cat['kits'])}",
        f"- style: clip (the beat as heard), {', '.join(cat['styles'])}",
        f"- notes_from: {', '.join(clipmake.NOTE_SOURCES)}",
        "- sound: {source: keep} (the sound now), {source: clip} (the clip "
        "itself as a sampler), or one of these by source and exact name:",
    ]
    for source, groups in cat["sounds"].items():
        for group, names in groups:
            lines.append(f"  - {source} / {group}: {', '.join(names)}")
    lines.append(
        "- fx: keep, none, " + ", ".join(f"{k} ({label})" for k, label in cat["fx"])
    )
    lines.append("- sound_mode (the clip as a sampler): whole, chops, voice")
    return "\n".join(lines)


def ask(
    info: dict,
    wav: bytes,
    *,
    seed: int = 0,
    config: AgentConfig | None = None,
    client=None,
) -> dict:
    """Ask Gemini for a plan: {"plan": dict (raw, unchecked), "tokens":
    int}. Raises AgentError (its message scrubbed) when the call fails or
    the answer is not JSON."""
    config = config or takecoach.load_config()  # read at call time: one patch point
    secrets = (config.api_key,) if config.api_key else ()
    try:
        from google.genai import types

        client = client or takecoach.client_for(config)
        reply = client.models.generate_content(
            model=config.model,
            contents=[
                types.Part.from_text(text=brief(info)),
                types.Part.from_bytes(data=wav, mime_type="audio/wav"),
            ],
            config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
                thinking_config=types.ThinkingConfig(thinking_level="low"),
                system_instruction=SYSTEM,
                response_mime_type="application/json",
                response_json_schema=schema(),
                temperature=0.4 if not seed else 0.9,
                seed=int(seed) % (1 << 31),
            ),
        )
        data = json.loads(reply.text or "{}")
    except AgentError:
        raise
    except Exception as err:  # the network, the SDK, the JSON: one message
        raise AgentError(
            scrub(f"Gemini could not be reached: {err}", secrets)
        ) from None
    if not isinstance(data, dict):
        raise AgentError("Gemini's answer was not the shape asked for.")
    for k in ("summary",):
        if isinstance(data.get(k), str):
            data[k] = scrub(data[k], secrets)
    if isinstance(data.get("why"), list):
        data["why"] = [scrub(str(w), secrets) for w in data["why"][:8]]
    usage = usage_split(getattr(reply, "usage_metadata", None))
    return {"plan": data, "tokens": usage["tokens_in"] + usage["tokens_out"]}


def merge(plan: dict, picked: dict) -> dict:
    """Gemini's raw plan with the player's locked picks over it."""
    out = dict(plan)
    out.update(picked)
    return out
