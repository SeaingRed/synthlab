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

What is sent: the loop as text — the tempo, the meter, the key, the swing,
the chord lane; each lane's sound, how it plays (its arp, strum, mute,
solo, level, the lanes that follow it, the saved loop it holds) and its
notes twice, as a grid and as a list by bar with each note's nudge, chance
and own feel; the beat as a grid with its kit, swing and each hit's
extras; the launcher's scenes; what the coach's rules found — the last few
lines of the thread, and the message. A spoken message is the phrase as a
16 kHz mono WAV (`clipmorph.audio_wav`, at most 8 s from the first
sound) with the same text; the reply says what it heard."""

from __future__ import annotations

import json
import math

from ..sequencer import midi, name_of
from . import loopcoach as lc
from . import takecoach
from .agent.client import AgentError, Reply, reply_cost, usage_split
from .agent.config import scrub
from .state.model import ALL_DRUM_ROWS

DRUM_VOICES = tuple(ALL_DRUM_ROWS)  # every row of either kit
ARP_MODES = ("off", "up", "down", "updown", "pattern")
OPS = (
    "add",
    "remove",
    "move",
    "velocity",
    "length",
    "clear",
    "transpose",
    "copy",
    "ramp",
    "arp",
    "strum",
    "swing",
    "chance",
    "ratchet",
)

MAX_EDITS = 64  # an 8- or 16-bar grid: thinning the hats is dozens
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
to to_step and/or to_pitch. velocity: the note starting at that step. length: \
the note of that pitch at that step made `length` steps long.
- Ranges (step to to_step, both included): clear (every note starting there), \
transpose (those notes `by` steps of the key's scale, + up), ramp (their \
velocities from velocity to to_velocity). copy: bar to to_bar (1-based), the \
destination bar replaced.
- How a lane plays: arp (mode off/up/down/updown, value = octaves 1-3; \
"pattern" only where the lane has one), strum (value: ms between a chord's \
notes, -60..60, 0 off). swing (any lane): the loop's swing, value 0-0.6.
- Drums: voice (one of the listed), step; add / remove / velocity / chance \
(value 0-1) / ratchet (value 1-4 hits in the step); clear / ramp over a range \
(voice optional: every row); copy bar to to_bar.
- `option` may name one of the coach's own options by its id (they are \
listed): it is made first, then your fixes and edits on top. Use one when it \
does what was asked (busier, sparser, a fill...) rather than many edits.
- Each lane also lists its notes by bar: "step pitch xlength v(1-9)", \
with its nudge (a share of a step early/late), its chance and its own feel \
(played between the lines) when it has them. Use those step numbers.
- A lane with an arp: its grid holds the chord(s) the arp walks, not the \
line heard; edit the chord tones, or say what to change in the arp. A lane \
"followed by" another is doubled there (that lane plays these notes). A \
MUTED lane is silent.
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
            "option": {"type": "string"},
            "edits": {
                "type": "array",
                "maxItems": MAX_EDITS,
                "items": {
                    "type": "object",
                    "properties": {
                        "lane": {"type": "string"},
                        "op": {"type": "string", "enum": list(OPS)},
                        "mode": {"type": "string", "enum": list(ARP_MODES)},
                        "value": {"type": "number"},
                        "by": {"type": "integer"},
                        "bar": {"type": "integer"},
                        "to_bar": {"type": "integer"},
                        "to_velocity": {"type": "number"},
                        "pitch": {"type": "string"},
                        "voice": {"type": "string", "enum": list(DRUM_VOICES)},
                        "step": {"type": "integer"},
                        "length": {"type": "integer"},
                        "to_step": {"type": "integer"},
                        "to_pitch": {"type": "string"},
                        "velocity": {"type": "number"},
                        "why": {"type": "string"},
                    },
                    "required": ["lane", "op"],
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


def notes_text(lane: dict, total: int, spb: int, main: dict | None = None) -> str:
    """A lane's notes as a list a bar a line, each "step pitch xlength v",
    the step counted across the lane (so nothing is counted off a grid)."""
    feel = lane.get("feel") or {}
    bars: dict[int, list[str]] = {}
    for n in lc.notes(lane):
        if n.start >= total:
            continue
        v = max(1, min(9, round(lc.vel_of(lane, n.start, main) * 9)))
        bit = f"{n.start} {n.pitch} x{n.n} v{v}"
        if (nd := feel.get("nudge", {}).get(str(n.start))) is not None:
            bit += f" nudge {nd:+.2f}"
        if (ch := feel.get("chance", {}).get(str(n.start))) is not None:
            bit += f" chance {round(ch * 100)}%"
        if f"{n.pitch}.{n.start}" in feel.get("notes", {}):
            bit += " (own feel)"
        bars.setdefault(n.start // spb, []).append(bit)
    return "\n".join(
        f"bar {b + 1}: " + ", ".join(bits) for b, bits in sorted(bars.items())
    )


def arp_text(mode, octaves, pattern) -> str:
    """How a lane's arp plays, in words ("" with none)."""
    if not mode or mode == "off":
        return ""
    if mode == "pattern" and pattern:
        steps = pattern.get("steps") or []
        on = [s for s in steps if s.get("on", True)]
        extra = []
        if any((s.get("rep") or 1) > 1 for s in on):
            extra.append("ratchets")
        if any(s.get("tr") for s in on):
            extra.append("steps transposed in the key")
        if any(
            float(s.get("chance", 1) if s.get("chance") is not None else 1) < 1
            for s in on
        ):
            extra.append("steps by chance")
        return (
            f"arp: a {len(steps)}-step pattern ({len(on)} on) at "
            f"{pattern.get('rate', 'grid')}, {pattern.get('mode', 'up')} over "
            f"{pattern.get('oct', 1)} octave(s)"
            + (f", with {', '.join(extra)}" if extra else "")
        )
    return f"arp: {mode} over {octaves or 1} octave(s), one note a step"


def lane_meta(state, i: int) -> list[str]:
    """How lane `i` plays besides its notes: its arp, strum and voice, its
    mix, the lanes doubling it and the saved loop it holds."""
    a, lay = state.arrangement, state.layers[i]
    if i == state.active_layer:
        mode, octs, pat = a.arp_mode, a.arp_octaves, a.arp_pattern
        strum, voice = a.strum, state.patch.voice_mode
    else:
        seq = lay.get("seq") if isinstance(lay.get("seq"), dict) else None
        if seq is None:  # never stashed: it plays the edited lane's sequencing
            mode, octs, pat, strum = a.arp_mode, a.arp_octaves, a.arp_pattern, a.strum
        else:
            mode, octs = seq.get("arp_mode", "off"), seq.get("arp_octaves", 1)
            pat, strum = seq.get("arp_pattern") or {}, seq.get("strum") or 0
        voice = (lay.get("patch") or {}).get("voice_mode", "poly")
    out = [x for x in (arp_text(mode, octs, pat),) if x]
    if strum:
        out.append(f"strummed ({strum:g} ms between a chord's notes)")
    if voice and voice != "poly":
        out.append(f"one voice ({voice})")
    if lay.get("role"):
        out.append(f"role: {lay['role']}")
    if lay.get("muted"):
        out.append("MUTED")
    if lay.get("solo"):
        out.append("soloed")
    gain = float(lay.get("gain") or 1.0)
    if abs(gain - 1.0) > 0.01:
        out.append(f"level {20 * math.log10(max(gain, 1e-4)):+.1f} dB")
    main = state.main_index()
    for j, other in enumerate(state.layers):
        if j != i and main == i and other.get("patch") and state.follows(j):
            octave = int((other.get("follow") or {}).get("octave") or 0)
            where = (
                f" {abs(octave)} octave(s) {'up' if octave > 0 else 'down'}"
                if octave
                else ""
            )
            out.append(f"followed by lane {j + 1}{where}")
    if e := state.loop_edit_of(i):
        out.append(f'holds the saved loop "{e["name"]}" (edits save to it)')
    return out


_HIT = {"p": "chance", "r": "ratchet", "n": "nudge", "t": "tune", "a": "art"}


def drums_meta(state) -> list[str]:
    """The beat besides its grid: the kit, its swing and feel, the rows'
    mix and every hit's extras (chance, ratchet, nudge, tune, articulation)."""
    a = state.arrangement
    kit = (
        "acoustic (Naked Drums, a recorded kit)"
        if a.drum_kit == "naked"
        else "electronic"
    )
    out = [f"kit: {kit}"]
    if a.drum_swing:
        out.append(f"swing {a.drum_swing:g}")
    if a.drum_humanize:
        out.append(f"humanize {a.drum_humanize:g}")
    if a.drum_row_swing:
        out.append(
            "row swing: " + ", ".join(f"{k} {v:g}" for k, v in a.drum_row_swing.items())
        )
    for v, m in sorted((a.drum_mix or {}).items()):
        bits = [
            w for w, on in (("muted", m.get("mute")), ("soloed", m.get("solo"))) if on
        ]
        if m.get("level") is not None and abs(float(m["level"]) - 1) > 0.01:
            bits.append(f"level {float(m['level']):g}")
        if bits:
            out.append(f"{v}: {', '.join(bits)}")
    extras = []
    for k, h in sorted((a.drum_hits or {}).items(), key=lambda kv: str(kv[0])):
        if not isinstance(h, dict) or "." not in str(k):
            continue
        bits = [
            f"{_HIT[x]} {h[x]:g}"
            if isinstance(h[x], int | float)
            else f"{_HIT[x]} {h[x]}"
            for x in _HIT
            if x in h
        ]
        if bits:
            voice, step = str(k).rsplit(".", 1)
            extras.append(f"{voice} {step}: {', '.join(bits)}")
    if extras:
        out.append("hits with extras: " + "; ".join(extras[:32]))
    if e := state.loop_edit_of(state.DRUM_LANE):
        out.append(f'holds the saved beat "{e["name"]}" (edits save to it)')
    return out


def launcher_text(state) -> list[str]:
    """The launcher's scenes (at most 8): what each holds a lane."""
    out = []
    for r, row in enumerate((state.perform_slots or [])[:8]):
        held = [
            f"{'drums' if k == state.DRUM_LANE else f'lane {k + 1}'}: {n}"
            for k, n in enumerate(row.get("slots") or [])
            if n
        ]
        if held:
            name = f' "{row["name"]}"' if row.get("name") else ""
            out.append(f"scene {r + 1}{name}: " + ", ".join(held))
    return out


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
                "notes": notes_text(lane, f.total, f.spb, f.main_vel),
                "meta": lane_meta(state, i),
            }
        )
    finds = lc.assess(doc, f)
    offered = [
        f"{o.id}: {o.label} — {o.why}" for o in lc.options(state, doc, f) if o.after
    ]
    return {
        "options": offered,
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
        "drums_meta": drums_meta(state),
        "swing": float(state.arrangement.swing or 0),
        "launcher": launcher_text(state),
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
        f"Tempo {info['bpm']:g} bpm, {info['meter']}, key {info['key']}"
        + (f", swing {info['swing']:g}" if info.get("swing") else "")
        + ".",
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
        if lane.get("meta"):
            lines.append("  " + "; ".join(lane["meta"]))
        lines.append(lane["grid"])
        if lane.get("notes"):
            lines += ["Its notes by bar:", lane["notes"]]
        lines.append("")
    lines.append("Beat (drums):")
    if info.get("drums_meta"):
        lines.append("  " + "; ".join(info["drums_meta"]))
    lines += [info["drums"], ""]
    if info.get("launcher"):
        lines += ["Launcher scenes (saved loops by lane):", *info["launcher"], ""]
    if info["findings"]:
        lines.append("The coach's checks found (id: what):")
        lines += [f"- {x}" for x in info["findings"]]
    if info["said"]:
        lines += [f"- (no fix) {x}" for x in info["said"]]
    if info.get("options"):
        lines += ["", "The coach's options (id: what), for `option`:"]
        lines += [f"- {x}" for x in info["options"]]
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
        "option": str(data.get("option") or "")[:40],
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


def _num(raw) -> float | None:
    try:
        v = None if raw is None else float(raw)
    except (TypeError, ValueError):
        return None
    return v if v is not None and math.isfinite(v) else None


def _vel(raw) -> float | None:
    v = _num(raw)
    return None if v is None else round(min(1.0, max(0.05, v)), 3)


def _span(step, to_step, total: int) -> tuple[int, int] | None:
    """A range of steps, both ends included, inside the lane (None if
    neither end is)."""
    if step is None:
        return None
    lo, hi = sorted((step, step if to_step is None else to_step))
    lo, hi = max(0, lo), min(total - 1, hi)
    return (lo, hi) if lo <= hi else None


def _one(doc: dict, f: lc.Facts, e) -> str | None:
    if not isinstance(e, dict):
        return None
    op, lane = e.get("op"), str(e.get("lane") or "").strip().lower()
    why = str(e.get("why") or "").strip()[:120]
    tail = f" — {why}" if why else ""
    step, to_step = _int(e.get("step")), _int(e.get("to_step"))
    vel = _vel(e.get("velocity"))
    if op == "swing":  # the loop's, whichever lane names it
        v = _num(e.get("value"))
        if v is None or "swing" not in doc:
            return None
        doc["swing"] = round(min(0.6, max(0.0, v)), 3)
        return f"the loop's swing at {doc['swing']:g}{tail}"
    if lane == "drums":
        return _drum(doc, f, op, e, step, to_step, vel, tail)
    slot = _int(lane)
    key = None if slot is None else str(slot - 1)
    if key not in doc["lanes"]:
        return None
    ln = doc["lanes"][key]
    name = lc.lane_name(int(key))
    if op in ("arp", "strum"):
        return _play(ln, op, e, name, tail)
    ns = lc.notes(ln)
    where = lambda s: lc._where(s, f.spb, f.beat)  # noqa: E731
    if op == "copy":
        b, tb = _int(e.get("bar")), _int(e.get("to_bar"))
        if (
            b is None
            or tb is None
            or b == tb
            or not (1 <= b <= f.bars and 1 <= tb <= f.bars)
        ):
            return None
        out = lc.with_notes(ln, lc._bar_copy(ns, b - 1, tb - 1, f.spb, f.total))
        if out["velocities"] is not None:
            vs = {
                k: v
                for k, v in out["velocities"].items()
                if not (tb - 1) * f.spb <= int(k) < tb * f.spb
            }
            for k, v in ln["velocities"].items():
                if (b - 1) * f.spb <= int(k) < b * f.spb:
                    vs[str(int(k) + (tb - b) * f.spb)] = v
            out["velocities"] = vs
        doc["lanes"][key] = out
        return f"{name}: bar {b} copied onto bar {tb}{tail}"
    if op in ("clear", "transpose", "ramp"):
        span = _span(step, to_step, f.total)
        if span is None:
            return None
        lo, hi = span
        inside = [n for n in ns if lo <= n.start <= hi]
        if not inside:
            return None
        rng = f"{where(lo)} to {where(hi)}"
        if op == "clear":
            doc["lanes"][key] = lc.with_notes(ln, [n for n in ns if n not in inside])
            return f"{name}: cleared {lc._plural(len(inside), 'note')}, {rng}{tail}"
        if op == "transpose":
            by = _int(e.get("by"))
            if not by:
                return None
            rest = [n for n in ns if n not in inside]
            moved = [lc.Note(lc._step_in_key(n.m, by, f), n.start, n.n) for n in inside]
            pool = rest + moved
            for a in moved:  # never onto a note of the same pitch
                if any(
                    x is not a and x.m == a.m and x.start < a.end and x.end > a.start
                    for x in pool
                ):
                    return None
            carry = {
                (o.pitch, o.start): (m.pitch, m.start)
                for o, m in zip(inside, moved, strict=True)
            }
            doc["lanes"][key] = lc.with_notes(ln, pool, carry=carry)
            way = f"{'up' if by > 0 else 'down'} {abs(by)}"
            count = lc._plural(len(inside), "note")
            return f"{name}: {count} {way} in the key, {rng}{tail}"
        to_vel = _vel(e.get("to_velocity"))
        if vel is None or to_vel is None or ln["velocities"] is None:
            return None
        starts = sorted({n.start for n in inside})
        vs = dict(ln["velocities"])
        for s in starts:
            k = 0.0 if hi == lo else (s - lo) / (hi - lo)
            vs[str(s)] = round(vel + (to_vel - vel) * k, 3)
        ln["velocities"] = vs
        ramp = f"{round(vel * 100)}% to {round(to_vel * 100)}%"
        return f"{name}: velocities {ramp}, {rng}{tail}"
    if step is None or not 0 <= step < f.total:
        return None
    m = _pitch(e.get("pitch"))
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
    if op == "length":
        n = _int(e.get("length"))
        if n is None or n < 1:
            return None
        n = min(n, f.total - hit.start)
        longer = lc.Note(hit.m, hit.start, n)
        rest = [x for x in ns if x != hit]
        if n == hit.n or any(
            x.m == hit.m and x.start < longer.end and x.end > hit.start for x in rest
        ):
            return None
        doc["lanes"][key] = lc.with_notes(ln, rest + [longer])
        long = lc._plural(n, "step")
        return f"{name}: {hit.pitch} at {where(hit.start)} now {long}{tail}"
    if op == "move":
        to_s = hit.start if to_step is None else to_step
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
        doc["lanes"][key] = lc.with_notes(
            ln,
            rest + [moved],
            keep_vel=keep,
            carry={(hit.pitch, hit.start): (moved.pitch, to_s)},
        )
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


def _play(ln: dict, op: str, e: dict, name: str, tail: str) -> str | None:
    """A lane's arp or strum: on the lane's `play` (a lane read without one
    — none here — takes no setting)."""
    play = ln.get("play")
    if play is None:
        return None
    if op == "strum":
        v = _num(e.get("value"))
        if v is None:
            return None
        play["strum"] = round(min(60.0, max(-60.0, v)), 1)
        return (
            f"{name}: strum {play['strum']:g} ms{tail}"
            if play["strum"]
            else f"{name}: no strum{tail}"
        )
    mode = e.get("mode")
    if mode not in ARP_MODES or (mode == "pattern" and play["arp_mode"] != "pattern"):
        return None  # (a pattern is drawn on the lane's arp panel, not made here)
    octs = _int(e.get("value"))
    play["arp_mode"] = mode
    if octs is not None:
        play["arp_octaves"] = max(1, min(3, octs))
    if mode == "off":
        return f"{name}: arp off{tail}"
    return f"{name}: arp {mode} over {play['arp_octaves']} octave(s){tail}"


def _drum(doc, f: lc.Facts, op, e: dict, step, to_step, vel, tail: str) -> str | None:
    d = doc["drums"]
    voice = e.get("voice")
    if voice is not None and voice not in DRUM_VOICES:
        return None
    beat = max(1, f.drum_spb // 4)
    where = lambda s: lc._where(s, f.drum_spb, beat)  # noqa: E731
    if op == "copy":
        bars = f.drum_total // f.drum_spb
        b, tb = _int(e.get("bar")), _int(e.get("to_bar"))
        if (
            b is None
            or tb is None
            or b == tb
            or not (1 <= b <= bars and 1 <= tb <= bars)
        ):
            return None
        lo, src = (tb - 1) * f.drum_spb, (b - 1) * f.drum_spb
        inside = lambda s, at: at <= int(s) < at + f.drum_spb  # noqa: E731
        cells = [c for c in d["drums"] if not inside(c[1], lo)]
        hits = {
            k: v
            for k, v in d["drum_hits"].items()
            if "." not in k or not inside(k.rsplit(".", 1)[1], lo)
        }
        for v, s in d["drums"]:
            if inside(s, src):
                cells.append([v, int(s) - src + lo])
                if (h := d["drum_hits"].get(f"{v}.{s}")) is not None:
                    hits[f"{v}.{int(s) - src + lo}"] = dict(h)
        d["drums"], d["drum_hits"] = sorted(cells, key=lambda c: (c[1], c[0])), hits
        return f"beat: bar {b} copied onto bar {tb}{tail}"
    if op in ("clear", "ramp"):
        span = _span(step, to_step, f.drum_total)
        if span is None:
            return None
        lo, hi = span
        picked = [
            c for c in d["drums"] if lo <= int(c[1]) <= hi and voice in (None, c[0])
        ]
        if not picked:
            return None
        rng = f"{where(lo)} to {where(hi)}"
        if op == "clear":
            d["drums"] = [c for c in d["drums"] if c not in picked]
            for v, s in picked:
                d["drum_hits"].pop(f"{v}.{s}", None)
            of = f" of the {voice}" if voice else ""
            count = lc._plural(len(picked), "hit")
            return f"beat: cleared {count}{of}, {rng}{tail}"
        to_vel = _vel(e.get("to_velocity"))
        if vel is None or to_vel is None:
            return None
        for v, s in picked:
            k = 0.0 if hi == lo else (int(s) - lo) / (hi - lo)
            lc._set_hit_vel(d, v, int(s), vel + (to_vel - vel) * k)
        ramp = f"{round(vel * 100)}% to {round(to_vel * 100)}%"
        return f"beat: velocities {ramp}, {rng}{tail}"
    if voice is None or step is None or not 0 <= step < f.drum_total:
        return None
    on = any(v == voice and int(s) == step for v, s in d["drums"])
    key = f"{voice}.{step}"
    if op == "add" and not on:
        d["drums"] = sorted(d["drums"] + [[voice, step]], key=lambda c: (c[1], c[0]))
        if vel is not None:
            lc._set_hit_vel(d, voice, step, vel)
        return f"beat: added a {voice} at {where(step)}{tail}"
    if op == "remove" and on:
        d["drums"] = [
            c for c in d["drums"] if not (c[0] == voice and int(c[1]) == step)
        ]
        d["drum_hits"].pop(key, None)
        return f"beat: removed the {voice} at {where(step)}{tail}"
    if op == "velocity" and on and vel is not None:
        lc._set_hit_vel(d, voice, step, vel)
        return f"beat: the {voice} at {where(step)} at {round(vel * 100)}%{tail}"
    if op in ("chance", "ratchet") and on:
        v = _num(e.get("value"))
        if v is None:
            return None
        h = dict(d["drum_hits"].get(key) or {})
        if op == "chance":
            v = round(min(1.0, max(0.0, v)), 3)
            h.pop("p", None) if v >= 1 else h.update(p=v)
            odds = f"{round(v * 100)}% of the time"
            said = f"beat: the {voice} at {where(step)} plays {odds}{tail}"
        else:
            v = int(max(1, min(4, round(v))))
            h.pop("r", None) if v == 1 else h.update(r=v)
            said = f"beat: the {voice} at {where(step)} as {lc._plural(v, 'hit')}{tail}"
        if h:
            d["drum_hits"][key] = h
        else:
            d["drum_hits"].pop(key, None)
        return said
    return None
