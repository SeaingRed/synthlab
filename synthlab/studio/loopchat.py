"""The loop coach's talk: one Gemini call per message typed
(or said: 🎙) into Perform's ✦ Coach — a few lines back, and at most
`MAX_EDITS` edits across the lanes and the beat, which `apply` checks one
by one before they become a proposal (`loopcoach.propose`). Nothing
reaches the loop until the player accepts it.

The key, the model, the timeout and the scrub are the sound agent's
(`takecoach.load_config`, read at call time — the one patch point a test
uses for a key — and `config.scrub`), and so is the SDK client
(`takecoach.client_for`, which `tests/conftest.py` replaces: no test can
reach Gemini). Not the agent: not logged in `agent_log.json`, not counted
against its budget — each reply carries its own tokens and "≈ $", shown
in the thread.

What is sent: the loop as text — the tempo, the meter, the key, the chord
lane, each lane's sound name and its notes as a grid, the beat as a grid,
what the coach's rules found — the last few lines of the thread, and the
message. A spoken message is the phrase as a 16 kHz mono WAV
(`clipmorph.audio_wav`, at most 8 s from the first sound) with the same
text; the reply says what it heard."""

from __future__ import annotations

import json
import math

from ..sequencer import midi, name_of
from . import loopcoach as lc
from . import takecoach
from .agent.client import AgentError, Reply, reply_cost, usage_split
from .agent.config import scrub
from .drumlab import DRUM_VOICES

MAX_EDITS = 16
HISTORY = 6  # thread lines the model sees

SYSTEM = """You are a producer sitting beside someone writing a short loop in \
a step sequencer (a piano roll for each synth lane, a drum grid). They ask you \
things about it in plain words: to change it ("make the bass follow the kick", \
"less busy in bar 3"), or why something sounds off. Answer like a musician: \
specific, short, naming bar and beat.

You may propose edits; the player hears them and accepts or discards them, so \
propose what they asked for and nothing else. Rules:
- Steps count from 0 across the whole lane; bars are split by '|' in the grids. \
A digit 1-9 is a note starting there (its velocity), '=' the note held on, '.' \
nothing.
- Lanes are named by number ("1", "2" ...) as in the grids; the beat is "drums".
- add: pitch (like A3), step, length in steps, velocity 0-1. remove: the note \
of that pitch sounding at that step. move: the note of that pitch at that step \
to to_step and/or to_pitch. velocity: the note starting at that step.
- Drums: voice (one of the listed), step; add / remove / velocity.
- Stay in the key unless asked; keep what the player clearly meant.
- `fixes` may name findings of the coach's own checks by their id to apply \
them as they stand (they are listed); use them when one does what was asked.
- `reply`: at most 4 short lines. No preamble. If you only explain, return no \
edits.
- If the message was spoken, put what you heard in `heard`."""


def schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "reply": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
            "heard": {"type": "string"},
            "fixes": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "edits": {
                "type": "array",
                "maxItems": MAX_EDITS,
                "items": {
                    "type": "object",
                    "properties": {
                        "lane": {"type": "string"},
                        "op": {
                            "type": "string",
                            "enum": ["add", "remove", "move", "velocity"],
                        },
                        "pitch": {"type": "string"},
                        "voice": {"type": "string", "enum": list(DRUM_VOICES)},
                        "step": {"type": "integer"},
                        "length": {"type": "integer"},
                        "to_step": {"type": "integer"},
                        "to_pitch": {"type": "string"},
                        "velocity": {"type": "number"},
                        "why": {"type": "string"},
                    },
                    "required": ["lane", "op", "step"],
                },
            },
        },
        "required": ["reply", "edits"],
    }


def available(config=None) -> str | None:
    """None when the coach can talk, else why not (a sentence)."""
    config = config or takecoach.load_config()
    why = takecoach.available(config)  # the SDK, the key: one answer for both
    if why and not config.has_key:
        return (
            "add GEMINI_API_KEY=… to the checkout's .env (then ask again: no restart)"
        )
    return why


# --- the loop as text -----------------------------------------------------------------


def lane_text(lane: dict, total: int, spb: int, main: dict | None = None) -> str:
    """A lane's notes as rows of text, high to low: a digit where a note
    starts (its velocity 1-9), '=' while it is held, '.' empty, '|' a bar."""
    rows: dict[int, list[str]] = {}
    for n in lc.notes(lane):
        row = rows.setdefault(n.m, ["."] * total)
        for s in range(n.start, min(n.end, total)):
            row[s] = "="
        if n.start < total:
            v = lc.vel_of(lane, n.start, main)
            row[n.start] = str(max(1, min(9, round(v * 9))))
    lines = []
    for m in sorted(rows, reverse=True):
        cells = "".join(
            ("|" if s and s % spb == 0 else "") + c for s, c in enumerate(rows[m])
        )
        lines.append(f"{name_of(m):<4} {cells}")
    return "\n".join(lines) or "(no notes)"


def drums_text(drums: dict, total: int, spb: int) -> str:
    rows: dict[str, list[str]] = {}
    for v, s in drums["drums"]:
        s = int(s)
        if 0 <= s < total:
            lvl = lc.hit_vel(drums, v, s)
            rows.setdefault(v, ["."] * total)[s] = str(max(1, min(9, round(lvl * 9))))
    lines = []
    for v in DRUM_VOICES:
        if v in rows:
            cells = "".join(
                ("|" if s and s % spb == 0 else "") + c for s, c in enumerate(rows[v])
            )
            lines.append(f"{v:<6} {cells}")
    return "\n".join(lines) or "(no beat)"


def info(state) -> dict:
    """What a message is sent with: the loop, read now (under the lock)."""
    doc, f = lc.read(state), lc.facts(state)
    chords = []
    from .gen import loop as gl

    for t in gl.lane(state.arrangement):
        chords.append(
            f"{t['name']} ({t['roman']}) from step {t['lo']} for {t['span']} steps"
        )
    lanes = []
    for key, lane in doc["lanes"].items():
        i = int(key)
        lanes.append(
            {
                "lane": str(i + 1),
                "name": f.names.get(i, ""),
                "edited": i == f.active,
                "grid": lane_text(lane, f.total, f.spb, f.main_vel),
            }
        )
    finds = lc.assess(doc, f)
    return {
        "bpm": f.bpm,
        "meter": f.meter,
        "key": f.key,
        "bars": f.bars,
        "spb": f.spb,
        "drum_spb": f.drum_spb,
        "drum_bars": f.drum_total // f.drum_spb,
        "chords": chords,
        "lanes": lanes,
        "drums": drums_text(doc["drums"], f.drum_total, f.drum_spb),
        "findings": [f"{x.id}: {x.title}" for x in finds if x.after],
        "said": [x.title for x in finds if not x.after],
        "history": history(state),
    }


def history(state) -> list[str]:
    out = []
    for e in (state.coach or {}).get("thread", []):
        if e["kind"] == "you":
            out.append(f"Player: {e['text']}")
        elif e["kind"] == "coach":
            out.append("You: " + " ".join(e.get("lines") or []))
        elif e["kind"] == "accepted":
            out.append(f"(The player accepted: {e['label']})")
    return out[-HISTORY:]


def brief(info: dict, message: str, spoken: bool = False) -> str:
    lines = [
        f"Tempo {info['bpm']:g} bpm, {info['meter']}, key {info['key']}.",
        f"The loop: {info['bars']} bar(s) of {info['spb']} steps on the note "
        f"grid; the beat {info['drum_bars']} bar(s) of {info['drum_spb']} steps.",
        "Chord lane: " + ("; ".join(info["chords"]) or "none"),
        "",
    ]
    for lane in info["lanes"]:
        lines.append(
            f"Lane {lane['lane']}{' (the one being edited)' if lane['edited'] else ''}"
            f" — sound: {lane['name'] or 'untitled'}"
        )
        lines.append(lane["grid"])
        lines.append("")
    lines += ["Beat (drums):", info["drums"], ""]
    if info["findings"]:
        lines.append("The coach's checks found (id: what):")
        lines += [f"- {x}" for x in info["findings"]]
    if info["said"]:
        lines += [f"- (no fix) {x}" for x in info["said"]]
    if info["history"]:
        lines += ["", "The conversation so far:", *info["history"]]
    lines += [
        "",
        "The player's message is the attached audio (spoken)."
        if spoken
        else f"The player says: {message}",
        f"For drums, `voice` is one of: {', '.join(DRUM_VOICES)}.",
    ]
    return "\n".join(lines)


def ask(
    info: dict, message: str, *, audio: bytes | None = None, config=None, client=None
) -> dict:
    """One call: {"reply": [str], "heard": str, "fixes": [str], "edits":
    [dict], "tokens": int, "cost": float | None}. Raises AgentError (its
    message scrubbed) when the call fails or the answer is not JSON."""
    config = config or takecoach.load_config()  # read at call time: one patch point
    secrets = (config.api_key,) if config.api_key else ()
    try:
        from google.genai import types

        client = client or takecoach.client_for(config)
        text = brief(info, message, spoken=audio is not None)
        contents = (
            [
                types.Part.from_text(text=text),
                types.Part.from_bytes(data=audio, mime_type="audio/wav"),
            ]
            if audio is not None
            else text
        )
        reply = client.models.generate_content(
            model=config.model,
            contents=contents,
            config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
                thinking_config=types.ThinkingConfig(thinking_level="low"),
                system_instruction=SYSTEM,
                response_mime_type="application/json",
                response_json_schema=schema(),
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
    model = str(getattr(reply, "model_version", "") or "")
    cost = reply_cost(
        Reply(text="", tokens=usage["tokens_in"] + usage["tokens_out"], **usage),
        config.price(model or None),
    )
    edits = [e for e in data.get("edits") or [] if isinstance(e, dict)]
    for e in edits:
        if "why" in e:
            e["why"] = scrub(str(e["why"]), secrets)[:160]
    return {
        "reply": [
            scrub(str(x), secrets)[:240]
            for x in data.get("reply") or []
            if str(x).strip()
        ][:4],
        "heard": scrub(str(data.get("heard") or ""), secrets)[:240],
        "fixes": [str(x)[:40] for x in data.get("fixes") or []][:8],
        "edits": edits[: MAX_EDITS * 2],
        "tokens": usage["tokens_in"] + usage["tokens_out"],
        "cost": cost,
        "model": model,
    }


# --- the edits, checked ---------------------------------------------------------------


def _int(raw) -> int | None:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    return v


def _pitch(raw) -> int | None:
    try:
        m = midi(str(raw))
    except (KeyError, ValueError, IndexError, TypeError):
        return None
    return m if 0 <= m <= 127 else None


def apply(doc: dict, f: lc.Facts, edits) -> tuple[dict, list[str], int]:
    """Gemini's edits on a copy of the doc, each checked: a lane that plays
    its own notes (by its number) or "drums"; a pitch in the key (or a tone
    of the chord there); a step inside the lane; an add only where its
    pitch is free, a remove / move / velocity only on a note that is
    there. Anything else is skipped. At most `MAX_EDITS`. Returns (the
    doc, a sentence per edit made, how many were skipped)."""
    import copy

    out = copy.deepcopy(doc)
    done: list[str] = []
    skipped = 0
    for e in list(edits or [])[: MAX_EDITS * 2]:
        if len(done) >= MAX_EDITS:
            break
        said = _one(out, f, e)
        if said:
            done.append(said)
        else:
            skipped += 1
    return out, done, skipped


def _ok_pitch(f: lc.Facts, m: int, step: int) -> bool:
    pcs = lc.chord_pcs(f, step) or set()
    return m % 12 in f.keys or m % 12 in pcs


def _one(doc: dict, f: lc.Facts, e) -> str | None:
    if not isinstance(e, dict):
        return None
    op, lane = e.get("op"), str(e.get("lane") or "").strip().lower()
    why = str(e.get("why") or "").strip()[:120]
    tail = f" — {why}" if why else ""
    step = _int(e.get("step"))
    vel = e.get("velocity")
    try:
        vel = None if vel is None else float(vel)
    except (TypeError, ValueError):
        vel = None
    if vel is not None and not math.isfinite(vel):
        vel = None
    if vel is not None:
        vel = round(min(1.0, max(0.05, vel)), 3)
    if step is None:
        return None
    if lane == "drums":
        return _drum(doc, f, op, e.get("voice"), step, vel, tail)
    slot = _int(lane)
    key = None if slot is None else str(slot - 1)
    if key not in doc["lanes"] or not 0 <= step < f.total:
        return None
    ln = doc["lanes"][key]
    ns = lc.notes(ln)
    name = lc.lane_name(int(key))
    m = _pitch(e.get("pitch"))
    where = lambda s: lc._where(s, f.spb, f.beat)  # noqa: E731
    if op == "add":
        if m is None or not _ok_pitch(f, m, step):
            return None
        n = max(1, min(_int(e.get("length")) or 1, f.total - step))
        if any(x.m == m and x.start < step + n and x.end > step for x in ns):
            return None
        new = lc.Note(m, step, n)
        lane_out = lc.with_notes(ln, ns + [new])
        if vel is not None and lane_out["velocities"] is not None:
            lane_out["velocities"] = {**lane_out["velocities"], str(step): vel}
        doc["lanes"][key] = lane_out
        return f"{name}: added {new.pitch} at {where(step)}{tail}"
    hit = next(
        (x for x in ns if (m is None or x.m == m) and x.start <= step < x.end), None
    )
    if hit is None or (m is None and op != "velocity"):
        return None
    if op == "remove":
        doc["lanes"][key] = lc.with_notes(ln, [x for x in ns if x != hit])
        return f"{name}: removed {hit.pitch} at {where(hit.start)}{tail}"
    if op == "move":
        to_s = _int(e.get("to_step"))
        to_s = hit.start if to_s is None else to_s
        to_m = _pitch(e.get("to_pitch")) if e.get("to_pitch") else hit.m
        if to_m is None or not 0 <= to_s < f.total or not _ok_pitch(f, to_m, to_s):
            return None
        moved = lc.Note(to_m, to_s, max(1, min(hit.n, f.total - to_s)))
        rest = [x for x in ns if x != hit]
        if any(x.m == to_m and x.start < moved.end and x.end > to_s for x in rest):
            return None
        keep = (
            {to_s: lc.vel_of(ln, hit.start, f.main_vel)} if to_s != hit.start else None
        )
        doc["lanes"][key] = lc.with_notes(ln, rest + [moved], keep_vel=keep)
        return (
            f"{name}: moved {hit.pitch} at {where(hit.start)} "
            f"to {moved.pitch} at {where(to_s)}{tail}"
        )
    if op == "velocity" and vel is not None and ln["velocities"] is not None:
        if hit.start != step:
            return None
        ln["velocities"] = {**ln["velocities"], str(step): vel}
        return f"{name}: {where(step)} at {round(vel * 100)}%{tail}"
    return None


def _drum(doc, f: lc.Facts, op, voice, step: int, vel, tail: str) -> str | None:
    d = doc["drums"]
    if voice not in DRUM_VOICES or not 0 <= step < f.drum_total:
        return None
    on = any(v == voice and int(s) == step for v, s in d["drums"])
    where = lc._where(step, f.drum_spb, max(1, f.drum_spb // 4))
    if op == "add" and not on:
        d["drums"] = sorted(d["drums"] + [[voice, step]], key=lambda c: (c[1], c[0]))
        if vel is not None:
            lc._set_hit_vel(d, voice, step, vel)
        return f"beat: added a {voice} at {where}{tail}"
    if op == "remove" and on:
        d["drums"] = [
            c for c in d["drums"] if not (c[0] == voice and int(c[1]) == step)
        ]
        d["drum_hits"].pop(f"{voice}.{step}", None)
        return f"beat: removed the {voice} at {where}{tail}"
    if op == "velocity" and on and vel is not None:
        lc._set_hit_vel(d, voice, step, vel)
        return f"beat: the {voice} at {where} at {round(vel * 100)}%{tail}"
    return None
