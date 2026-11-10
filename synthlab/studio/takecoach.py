"""Gemini's half of a take's clean-up: after `takeclean`
has fixed the timing, the doubles, the velocities and the key, one call
asks Gemini for a few lines of feedback and at most `takeclean.MAX_EDITS`
musical edits (a missing backbeat, a stray hit, an accent, a pickup into
the next bar). The edits come back as JSON against `SCHEMA` and are
applied by `takeclean.apply_edits`, which checks every one.

The key, the model and the timeout are the sound agent's
(`agent.config.load_config`: `GEMINI_API_KEY` in the checkout's `.env`),
and so is the rule that nothing off the network reaches a page or a log
unscrubbed (`config.scrub`). What is sent: the song's tempo, key, style
and section, the names of the other tracks playing there, the cleaned
pattern as text and the take's timing figures. No audio.

`client_for` builds the SDK client — `tests/conftest.py` replaces it, so
no test can reach Gemini; a test hands `coach` a stand-in instead."""

from __future__ import annotations

import json

from .agent.client import AgentError, sdk_available, usage_split
from .agent.config import AgentConfig, load_config, scrub
from .drumlab import DRUM_VOICES

SYSTEM = """You are a producer and session musician listening back to a part \
someone just recorded into their song by finger-drumming on a keyboard or \
pads. A deterministic pass has already fixed their timing, merged double \
hits, evened velocities and (for notes) moved wrong notes into the key. \
Your job: up to 8 small edits that make the part groove and sit with the \
song, and a few lines of feedback.

Rules:
- Keep what the player clearly meant. Never rewrite the part or add a \
whole new groove; fix and finish it.
- Good edits: a missing or doubled backbeat, a stray hit that fights the \
groove, an accent pattern on the hats, a kick that clashes with the bass \
rhythm, a pickup or small fill in the last beat before the pattern loops, \
a hit that makes a bar match the others when one bar was clearly a slip.
- Steps are numbered from 0 across the whole pattern; the grid text shows \
bars split by '|', '.' for empty and 1-9 for velocity.
- Feedback: at most 4 short lines, specific and encouraging, the way a \
musician would say it, naming bar and beat. No preamble.
- If the part is already good, say so and return no edits."""

SCHEMA = {
    "type": "object",
    "properties": {
        "feedback": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "edits": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["add", "remove", "velocity"]},
                    "voice": {"type": "string", "enum": list(DRUM_VOICES)},
                    "pitch": {"type": "string"},
                    "step": {"type": "integer"},
                    "velocity": {"type": "number"},
                    "why": {"type": "string"},
                },
                "required": ["op", "step"],
            },
        },
    },
    "required": ["feedback", "edits"],
}


def available(config: AgentConfig | None = None) -> str | None:
    """None when Gemini can be asked, else why not (a sentence)."""
    config = config or load_config()
    if not sdk_available():
        return "the Gemini SDK is not installed (uv sync --all-extras)"
    if not config.has_key:
        return "no GEMINI_API_KEY in .env"
    return None


def client_for(config: AgentConfig):
    """The SDK client (one per call: a clean-up is one request)."""
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=config.api_key,
        # one call: the whole turn's deadline, not one round's
        http_options=types.HttpOptions(timeout=int(config.turn_deadline_s * 1000)),
    )


def brief(info: dict) -> str:
    """The request's text: what the take is and where it sits."""
    kind = "drum pattern" if info["kind"] == "drums" else "note pattern"
    lines = [
        f"Song: {info.get('title') or 'untitled'}"
        + (f" ({info['style']})" if info.get("style") else ""),
        f"Tempo {info['bpm']:g} bpm, 4/4, key {info['key']}.",
        f"Section: {info['section']} — also playing: "
        f"{', '.join(info['others']) or 'nothing else'}.",
        f"The take: a {kind} on the '{info['track']}' track, {info['bars']} bar(s) "
        f"of {info['steps']} steps (step {info['steps'] // 4} is beat 2).",
        "",
        "The part after clean-up:",
        info["grid"],
        "",
        "What the clean-up already did:",
        *[f"- {f}" for f in info["fixes"]],
        "",
        "How the player sat against the grid before the fix (ms, + is late):",
        *[
            f"- {name}: {s['hits']} hits, "
            f"lean {s['lean_ms']:+d}, spread {s['spread_ms']}"
            for name, s in info["stats"].items()
        ],
    ]
    if info["kind"] == "notes":
        lines.append("For notes, give `pitch` as a name like G3; stay in the key.")
    else:
        lines.append(f"For drums, give `voice` as one of: {', '.join(DRUM_VOICES)}.")
    return "\n".join(lines)


def coach(info: dict, *, config: AgentConfig | None = None, client=None) -> dict:
    """Ask Gemini about a cleaned take: {"feedback": [str], "edits": [dict],
    "tokens": int}. Raises AgentError (its message scrubbed) when the
    call fails or the answer is not the JSON asked for."""
    config = config or load_config()
    secrets = (config.api_key,) if config.api_key else ()
    try:
        from google.genai import types

        client = client or client_for(config)
        reply = client.models.generate_content(
            model=config.model,
            contents=brief(info),
            config=types.GenerateContentConfig(
                # as the agent's turns: no tool loop, little thinking (a
                # thinking model left alone ran past the 30 s deadline)
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
                thinking_config=types.ThinkingConfig(thinking_level="low"),
                system_instruction=SYSTEM,
                response_mime_type="application/json",
                response_json_schema=SCHEMA,
                temperature=0.4,
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
    usage = usage_split(getattr(reply, "usage_metadata", None))
    feedback = [
        scrub(str(f), secrets)[:200]
        for f in data.get("feedback") or []
        if str(f).strip()
    ][:4]
    edits = [e for e in data.get("edits") or [] if isinstance(e, dict)]
    for e in edits:
        if "why" in e:
            e["why"] = scrub(str(e["why"]), secrets)
    return {
        "feedback": feedback,
        "edits": edits,
        "tokens": usage["tokens_in"] + usage["tokens_out"],
    }
