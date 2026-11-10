"""The sound agent's tools: declarations generated from the parameter
registry, and the functions that apply a call to the session.

Nothing here is written by hand per parameter. `set_params` offers every
Patch field the editor shows (`paramui.UI` with a panel) as one catalogue
line each (`param_catalogue`): the range `params.clamp` accepts
(`Param.cap`), the unit, the slider's own range as the "typical" hint, a
categorical's choices; fields sharing one set of choices share a line. It
was a JSON-schema property per field — 8 KB of the 15 KB every call
resends; the model reads the same facts from 3 KB of text, and
`coerce_params` checks what comes back exactly as before. The FX tool is
generated from `fxrack.FX_UNITS` the same way, curves from
`curves.CURVABLE`, macro targets from `paramui.MACRO_PARAMS`, routes
from `params.MOD_*`. A field or FX unit added later reaches the agent
without anyone touching this module (`tests/studio/test_agent.py` pins
that the schema covers the registry).

Every argument is untrusted model output: unknown names are rejected with
a suggestion, values are coerced and clamped (the clamp is reported back so
the model learns the range), and nothing here can raise on bad input — a
tool call answers `{"ok": False, "error": ...}` instead.

`apply_call` runs under the studio lock (see `loop.py`).
"""

from __future__ import annotations

import copy
import difflib
import math
from dataclasses import replace

from ...params import (
    MOD_DESTINATIONS,
    MOD_SLOTS,
    MOD_SOURCES,
    PARAMS,
    clamp,
    slot_active,
)
from ...synth import Patch
from ..arrange import FOLLOW_OCTAVES, LAYER_ROLES
from ..audition import PHRASES
from ..curves import CURVABLE
from ..fxrack import (
    DYNAMICS_UNITS,
    FX2_UNITS,
    FX_UNITS,
    MAX_UNITS,
    VOCAL_UNITS,
    clean_chain,
)
from ..paramui import MACRO_PARAMS, UI, spec_of

# every Patch field the editor has a control for, in panel order — except
# the performance controls (`Param.nogene`: the bend wheel and its range),
# which are the player's, not the sound's. Filter 2 and the general
# modulators are no genes either, but they are the sound's: they are here
# (routes 5-12 are `add_mod_route`'s, by slot)
_R6_AGENT = ("filter2_", "env3_", "env4_", "lfo2_", "lfo3_")
UI_PARAMS: tuple[str, ...] = tuple(
    n
    for n, u in UI.items()
    if u.group is not None and (not PARAMS[n].nogene or n.startswith(_R6_AGENT))
)
CURVE_SHAPES = ("rise", "fall", "dip", "swell", "points", "clear")
MAX_AGENT_CURVE_POINTS = 32
LEVEL_DB = (-40.0, 3.0)
# what a role's layer starts from (genome.ROLES has the recipe itself)
_ROLE_FX = {
    "pad": [
        {"kind": "highpass", "params": {"cutoff": 200.0}},
        {"kind": "chorus", "params": {}},
    ],
    "pluck": [{"kind": "delay", "params": {"sync": 2.0, "mix": 0.2}}],
    "air": [{"kind": "width", "params": {}}],
    "sub": [],
}
SOUND_SOURCES = ("library", "factory", "preset")
# tighter than FX_UNITS for the agent only: a width amount past 1.5 pushes the
# mix out of phase (stereo width > 1: it collapses in mono)
_AGENT_FX_CAPS = {("width", "amount"): 1.5}
# the units the agent may put on a sound: every one but the live vocal
# chain's (a mic's, not a synth's — and five more catalogue lines on every
# request), the FX2 units (nine more lines: past the schema's budget,
# tests/studio/test_agent.py) and the dynamics units (three more)
_NOT_OFFERED = VOCAL_UNITS | FX2_UNITS | DYNAMICS_UNITS
AGENT_FX = {k: v for k, v in FX_UNITS.items() if k not in _NOT_OFFERED}


# --- declarations -------------------------------------------------------------


def _fmt(v: float) -> str:
    return f"{v:g}"


def _span(lo: float, hi: float) -> str:
    return f"{_fmt(lo)}..{_fmt(hi)}"


def param_spec(name: str) -> str:
    """What one Patch field accepts, as the catalogue says it: a
    categorical's choices ("a|b|c", "off" for None), else `params.clamp`'s
    cap ("int" for whole numbers) with the unit, and the slider's range as
    the typical hint where the cap is wider."""
    p, ui = PARAMS[name], UI[name]
    if p.kind == "cat":
        return "|".join("off" if c is None else str(c) for c in p.cap)
    lo, hi = p.cap
    spec = spec_of(name)
    out = ("int " if p.kind == "int" else "") + _span(lo, hi)
    if ui.unit:
        out += f" {ui.unit}"
    if spec is not None and (spec.min, spec.max) != (lo, hi):
        out += f", typical {_span(spec.min, spec.max)}"
    return out


def _squash(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum())


def param_title(name: str) -> str:
    """The field as the catalogue names it: with the panel's label when
    that says more than the name does ("fm_amount (Cross FM)", but plain
    "osc1_level": its label is "Osc 1 level")."""
    label = UI[name].label
    return name if _squash(label) == _squash(name) else f"{name} ({label})"


def param_groups() -> list[tuple[tuple[str, ...], str]]:
    """(fields, spec) per catalogue line, in panel order. Categoricals
    with the same choices share a line (the four routes' sources and
    targets, the two LFOs' shapes and syncs); a number has its own, but
    for a numbered run of one range (fm4_ratio1..4: an operator each)."""
    groups: dict[tuple, list[str]] = {}
    for n in UI_PARAMS:
        spec = param_spec(n)
        run = n.rstrip("0123456789") + "#" if n[-1].isdigit() else n
        key = (spec,) if PARAMS[n].kind == "cat" else (spec, run)
        groups.setdefault(key, []).append(n)
    return [(tuple(names), key[0]) for key, names in groups.items()]


def param_catalogue() -> str:
    """Every field `set_params` takes, one line per `param_groups` entry:
    "cutoff: 60..16000 Hz"."""
    return "\n".join(
        f"{', '.join(param_title(n) for n in names)}: {spec}"
        for names, spec in param_groups()
    )


def fx_catalogue() -> str:
    """Every FX unit and its params' ranges, one line each, from FX_UNITS:
    the unit schema stays a small {kind, params} object (a JSON-schema
    `anyOf` of twenty units was a third of every request's tokens) and
    `clean_chain` clamps whatever comes back."""
    lines = []
    for kind, spec in AGENT_FX.items():
        ps = ", ".join(
            f"{n} {_fmt(lo)}-{_fmt(hi)}" for n, (lo, hi, _d) in spec["params"].items()
        )
        extra = "; pattern 'x.x.' (x = open)" if "pattern" in spec else ""
        for name, labels in spec.get("choices", {}).items():
            extra += f"; {name} " + " ".join(f"{i}={c}" for i, c in enumerate(labels))
        lines.append(f"{kind}: {ps}{extra}")
    return "\n".join(lines)


def _layer_index_schema(desc: str) -> dict:
    return {"type": "integer", "minimum": 1, "maximum": 3, "description": desc}


def declarations() -> list[dict]:
    """The tools, as neutral `{name, description, parameters}` dicts whose
    `parameters` is a JSON schema (the client adapter wraps them)."""
    params_obj = {
        "type": "object",
        "description": (
            "field -> new value; only the fields you change. The fields "
            "(name: range and unit, or its choices):\n" + param_catalogue()
        ),
    }
    return [
        {
            "name": "set_params",
            "description": (
                "Change synth parameters of one layer's patch (default: the layer "
                "being edited). Values outside the range are clamped and reported."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "params": params_obj,
                    "layer": _layer_index_schema("layer 1-3; omit for the edited one"),
                },
                "required": ["params"],
            },
        },
        {
            "name": "add_layer",
            "description": (
                "Stack a new layer that follows the main lane's notes. `role` makes "
                "it to a recipe (sub: sine an octave down, low-passed; pad: slow, "
                "wide, soft; pluck: short with an echo; air: a thin bright band an "
                "octave up). The base is a copy of the edited sound, or `from_sound` "
                "(a Library/factory/saved sound by name). Returns its layer number: "
                "shape it further with set_params(layer=N)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": list(LAYER_ROLES)},
                    "follow_octave": {
                        "type": "integer",
                        "minimum": FOLLOW_OCTAVES[0],
                        "maximum": FOLLOW_OCTAVES[-1],
                    },
                    "from_sound": {"type": "string", "description": "sound name"},
                    "level_db": {
                        "type": "number",
                        "minimum": LEVEL_DB[0],
                        "maximum": LEVEL_DB[1],
                        "description": "level relative to unity (default by role)",
                    },
                },
            },
        },
        {
            "name": "set_layer",
            "description": "Mix one layer: level, mute, solo, follow the main lane.",
            "parameters": {
                "type": "object",
                "properties": {
                    "layer": _layer_index_schema("layer 1-3"),
                    "level_db": {
                        "type": "number",
                        "minimum": LEVEL_DB[0],
                        "maximum": LEVEL_DB[1],
                    },
                    "mute": {"type": "boolean"},
                    "solo": {"type": "boolean"},
                    "follow": {"type": "boolean", "description": "play the main notes"},
                    "follow_octave": {
                        "type": "integer",
                        "minimum": FOLLOW_OCTAVES[0],
                        "maximum": FOLLOW_OCTAVES[-1],
                    },
                },
                "required": ["layer"],
            },
        },
        {
            "name": "set_fx_chain",
            "description": (
                f"Replace a whole FX chain (max {MAX_UNITS} units, in order). scope "
                "'layer' = a layer's own chain (default the edited layer), 'master' "
                "= the bus every layer goes through. Pass the chain's existing units "
                "too if you want to keep them; [] clears it. pump ducks on the beat "
                "(sidechain feel without drums); sidechain needs a kick."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["layer", "master"]},
                    "layer": _layer_index_schema("for scope layer; omit = edited"),
                    "units": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": list(AGENT_FX)},
                                "params": {
                                    "type": "object",
                                    "description": "param -> value; omitted = default",
                                },
                                "pattern": {"type": "string"},
                            },
                            "required": ["kind"],
                        },
                        "description": "units and their params:\n" + fx_catalogue(),
                    },
                },
                "required": ["scope", "units"],
            },
        },
        {
            "name": "add_mod_route",
            "description": (
                "Route a modulation source to a target in the edited patch's "
                f"{len(MOD_SLOTS)}-slot matrix (lfo = LFO 1 at mod_lfo_rate / "
                "lfo_sync; lfo2/lfo3 at lfoN_rate; "
                "fenv/aenv/env3/env4 = filter/amp/general envelopes; wheel, "
                "aftertouch the player's; key = note position; random = per "
                "note). amount is a signed fraction of "
                "the target's range. Uses the first free slot unless `slot` is given; "
                "source 'none' clears that slot."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": list(MOD_SOURCES)},
                    "target": {"type": "string", "enum": list(MOD_DESTINATIONS)},
                    "amount": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                    "slot": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": len(MOD_SLOTS),
                    },
                },
                "required": ["source"],
            },
        },
        {
            "name": "draw_curve",
            "description": (
                "Automate a parameter across each note of the edited layer. "
                "rise/fall/dip/swell move it around its current value by `depth` "
                "(0-1 of its range, default 0.3); 'points' takes [t, v] pairs in "
                "0-1 (t = time through the note, v = position in the range); "
                "'clear' removes the curve."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "param": {"type": "string", "enum": list(CURVABLE)},
                    "shape": {"type": "string", "enum": list(CURVE_SHAPES)},
                    "depth": {"type": "number", "minimum": 0.02, "maximum": 1.0},
                    "points": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        },
                    },
                },
                "required": ["param", "shape"],
            },
        },
        {
            "name": "set_macro",
            "description": (
                "Assign a performance macro knob (1-4): up to 3 targets, each a "
                "signed fraction of the param's range the knob sweeps. `value` "
                "(0-1) also turns the knob now, which moves those params."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "minimum": 1, "maximum": 4},
                    "name": {"type": "string"},
                    "targets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "param": {
                                    "type": "string",
                                    "enum": list(MACRO_PARAMS),
                                },
                                "amount": {
                                    "type": "number",
                                    "minimum": -1.0,
                                    "maximum": 1.0,
                                },
                            },
                            "required": ["param", "amount"],
                        },
                    },
                    "value": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": ["index", "targets"],
            },
        },
        {
            "name": "choose_phrase",
            "description": (
                "Pick the reference phrase the loop plays the sound with: hold "
                "(one long chord: pads), arp (eighths), riff (bassline with "
                "slides), stab (short chords)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"phrase": {"type": "string", "enum": list(PHRASES)}},
                "required": ["phrase"],
            },
        },
    ]


TOOL_NAMES = tuple(d["name"] for d in declarations())


# --- coercion -------------------------------------------------------------------


class ToolError(ValueError):
    """A call the tool refuses: its message goes back to the model."""


def _suggest(name: str, choices) -> str:
    near = difflib.get_close_matches(str(name), [str(c) for c in choices], n=3)
    return f" (did you mean {', '.join(near)}?)" if near else ""


def _number(raw, what: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise ToolError(f"{what}: expected a number, got {raw!r}"[:120])
    try:
        v = float(raw)
    except (TypeError, ValueError, OverflowError):
        raise ToolError(f"{what}: expected a number, got {raw!r}"[:120]) from None
    if not math.isfinite(v):
        raise ToolError(f"{what}: {raw!r} is not a finite number")
    return v


def _layer(state, raw, *, default_edited: bool = True) -> int:
    if raw is None:
        if default_edited:
            return state.active_layer
        raise ToolError("layer: required (1-3)")
    i = int(_number(raw, "layer"))
    if not 1 <= i <= len(state.layers):
        raise ToolError(f"layer: {i} is not 1-{len(state.layers)}")
    return i - 1


def _level_gain(db: float) -> float:
    db = min(LEVEL_DB[1], max(LEVEL_DB[0], db))
    return min(1.5, 10 ** (db / 20.0))


def coerce_params(values, base: Patch) -> tuple[Patch, list[str], list[str]]:
    """`values` ({field: value}, untrusted) onto `base`: each one checked
    against the registry and clamped with `params.clamp`. Returns the new
    patch, the fields that changed, and notes (rejections, clamps)."""
    if not isinstance(values, dict):
        raise ToolError("params: expected an object of field -> value")
    notes: list[str] = []
    changes: dict = {}
    for name, raw in values.items():
        if name not in UI_PARAMS:
            notes.append(f"unknown param {name!r} ignored{_suggest(name, UI_PARAMS)}")
            continue
        p = PARAMS[name]
        if p.kind == "cat":
            value = None if raw in ("off", None, "none", "") and None in p.cap else raw
            try:
                ok = value in set(p.cap)
            except TypeError:
                ok = False
            if not ok:
                choices = ["off" if c is None else c for c in p.cap]
                notes.append(f"{name}: {raw!r} is not one of {choices}")
                continue
            changes[name] = value
            continue
        try:
            v = _number(raw, name)
        except ToolError as err:
            notes.append(str(err))
            continue
        lo, hi = float(p.cap[0]), float(p.cap[1])
        cv = min(hi, max(lo, v))
        if cv != v:
            notes.append(f"{name}: {_fmt(v)} clamped to {_fmt(cv)}")
        spec = spec_of(name)
        if p.kind == "int" or (spec is not None and spec.step == 1.0):
            cv = int(round(cv))  # an int, as the slider posts it (forms.py)
        changes[name] = cv
    new = clamp(replace(base, **changes)) if changes else base
    moved = [n for n in changes if repr(getattr(base, n)) != repr(getattr(new, n))]
    return new, moved, notes


# --- the tools --------------------------------------------------------------------


def _patch_of(state, i: int) -> Patch:
    if i == state.active_layer:
        return state.patch
    raw = state.layers[i].get("patch")
    if not raw:
        raise ToolError(f"layer {i + 1} is empty")
    return clamp(Patch.from_dict(raw))


def _set_patch_of(state, i: int, patch: Patch) -> None:
    if i == state.active_layer:
        state.patch = patch
    else:
        state.layers[i]["patch"] = patch.to_json()


def t_set_params(state, svc, args: dict) -> dict:
    i = _layer(state, args.get("layer"))
    base = _patch_of(state, i)
    new, moved, notes = coerce_params(args.get("params"), base)
    _set_patch_of(state, i, new)
    return {"ok": True, "layer": i + 1, "changed": moved, "notes": notes}


def _find_sound(svc, name: str):
    from ..library import LIBRARY
    from ..sounds import find

    name = str(name)[:80]
    for source in SOUND_SOURCES:
        try:
            sound = find(svc, source, name)
        except Exception:  # a hostile name must never escape as a 500
            sound = None
        if sound is not None:
            return sound
    from ..factory import FACTORY

    known = [e["name"] for e in LIBRARY] + [e["name"] for e in FACTORY]
    raise ToolError(f"no sound named {name!r}{_suggest(name, known)}")


def t_add_layer(state, svc, args: dict) -> dict:
    from ..genome import ROLES, recipe

    if state.free_slot() is None:
        raise ToolError("every layer holds a sound: mix or replace one instead")
    role = args.get("role")
    if role is not None and role not in LAYER_ROLES:
        raise ToolError(f"role: {role!r} is not one of {list(LAYER_ROLES)}")
    notes: list[str] = []
    fx: list = []
    if args.get("from_sound"):
        sound = _find_sound(svc, args["from_sound"])
        patch = sound.patch
        fx = list((sound.chain or {}).get("main_fx") or [])
    else:
        patch = state.patch
    if role is not None:
        import numpy as np

        patch = recipe(role, patch, np.random.default_rng(0), fresh=False)
        if not fx:
            fx = copy.deepcopy(_ROLE_FX[role])
    octave = ROLES[role].octaves[0] if role else 0
    if args.get("follow_octave") is not None:
        o = int(_number(args["follow_octave"], "follow_octave"))
        octave = min(FOLLOW_OCTAVES[-1], max(FOLLOW_OCTAVES[0], o))
    db = sum(ROLES[role].db) / 2 if role else -6.0
    if args.get("level_db") is not None:
        db = _number(args["level_db"], "level_db")
    name = args.get("from_sound") or (role or "layer")
    patch = replace(clamp(patch), name=str(name)[:40])
    i = state.add_layer(
        patch,
        clean_chain(fx),
        select=False,
        octave=octave,
        gain=_level_gain(db),
        role=role,
    )
    if i is None:  # free_slot said otherwise a moment ago
        raise ToolError("no free layer")
    return {
        "ok": True,
        "layer": i + 1,
        "follows_octave": octave,
        "level_db": round(db, 1),
        "notes": notes,
    }


def t_set_layer(state, svc, args: dict) -> dict:
    i = _layer(state, args.get("layer"), default_edited=False)
    lay = state.layers[i]
    if i != state.active_layer and not lay.get("patch"):
        raise ToolError(f"layer {i + 1} is empty")
    done: list[str] = []
    notes: list[str] = []
    if args.get("level_db") is not None:
        state.set_layer_gain(i, _level_gain(_number(args["level_db"], "level_db")))
        done.append("level")
    if args.get("mute") is not None:
        state.set_layer_muted(i, bool(args["mute"]))
        done.append("mute")
    if args.get("solo") is not None:
        state.set_layer_solo(i, bool(args["solo"]))
        done.append("solo")
    follow = args.get("follow")
    octave = args.get("follow_octave")
    if follow is not None or octave is not None:
        if follow is False:
            state.set_follow(i, None)
            done.append("follow off")
        elif i == state.main_index():
            notes.append(f"layer {i + 1} is the main lane: it can't follow itself")
        else:
            o = 0 if octave is None else int(_number(octave, "follow_octave"))
            o = min(FOLLOW_OCTAVES[-1], max(FOLLOW_OCTAVES[0], o))
            state.set_follow(i, o)
            done.append(f"follows at {o:+d} oct")
    return {"ok": True, "layer": i + 1, "changed": done, "notes": notes}


def t_set_fx_chain(state, svc, args: dict) -> dict:
    scope = args.get("scope")
    units = args.get("units")
    if scope not in ("layer", "master"):
        raise ToolError("scope: 'layer' or 'master'")
    if not isinstance(units, list):
        raise ToolError("units: expected a list of {kind, params}")
    notes: list[str] = []
    kept = []
    for u in units:
        kind = u.get("kind") if isinstance(u, dict) else None
        if kind not in AGENT_FX:
            notes.append(f"unknown FX {kind!r} dropped{_suggest(kind, AGENT_FX)}")
            continue
        params = u.get("params") if isinstance(u.get("params"), dict) else {}
        spec = FX_UNITS[kind]["params"]
        for pname, raw in params.items():
            if pname not in spec:
                notes.append(f"{kind}.{pname} is not a parameter of {kind}")
                continue
            lo, hi, _d = spec[pname]
            try:
                v = _number(raw, f"{kind}.{pname}")
            except ToolError as err:
                notes.append(str(err))
                continue
            if not lo <= v <= hi:
                notes.append(f"{kind}.{pname}: {_fmt(v)} clamped to {lo:g}-{hi:g}")
            if (kind, pname) in _AGENT_FX_CAPS and v > _AGENT_FX_CAPS[kind, pname]:
                # the rack allows more, but past this it only adds phase trouble
                cap = _AGENT_FX_CAPS[kind, pname]
                params = {**params, pname: cap}
                u = {**u, "params": params}
                notes.append(
                    f"{kind}.{pname}: {_fmt(v)} capped at {_fmt(cap)} (mono-safe)"
                )
        kept.append(u)
    if len(kept) > MAX_UNITS:
        notes.append(f"only the first {MAX_UNITS} units kept")
    chain = clean_chain(kept)
    if scope == "master":
        state.fx_master[:] = chain
        where = "master"
    else:
        i = _layer(state, args.get("layer"))
        if i != state.active_layer and not state.layers[i].get("patch"):
            raise ToolError(f"layer {i + 1} is empty")
        state.layers[i]["fx"] = chain
        where = f"layer {i + 1}"
    return {
        "ok": True,
        "chain": where,
        "units": [u["kind"] for u in chain],
        "notes": notes,
    }


def t_add_mod_route(state, svc, args: dict) -> dict:
    src = args.get("source")
    if src not in MOD_SOURCES:
        raise ToolError(f"source: one of {list(MOD_SOURCES)}")
    p = state.patch
    if args.get("slot") is not None:
        k = int(_number(args["slot"], "slot"))
        if not 1 <= k <= len(MOD_SLOTS):
            raise ToolError(f"slot: 1-{len(MOD_SLOTS)}")
        slot = MOD_SLOTS[k - 1]
    elif src == "none":
        raise ToolError("source 'none' clears a slot: pass `slot`")
    else:
        free = [s for s in MOD_SLOTS if not slot_active(p, s)]
        if not free:
            raise ToolError("all routes are in use: pass `slot` to replace one")
        slot = free[0]
    if src == "none":
        state.patch = replace(p, **{f"{slot}_src": "none", f"{slot}_amt": 0.0})
        return {"ok": True, "slot": MOD_SLOTS.index(slot) + 1, "cleared": True}
    dst = args.get("target")
    if dst not in MOD_DESTINATIONS:
        raise ToolError(f"target: one of {list(MOD_DESTINATIONS)}")
    amt = _number(args.get("amount", 0.5), "amount")
    notes = []
    if not -1.0 <= amt <= 1.0:
        notes.append(f"amount {_fmt(amt)} clamped to -1..1")
        amt = min(1.0, max(-1.0, amt))
    state.patch = clamp(
        replace(p, **{f"{slot}_src": src, f"{slot}_dst": dst, f"{slot}_amt": amt})
    )
    return {"ok": True, "slot": MOD_SLOTS.index(slot) + 1, "notes": notes}


def _median(x) -> float:
    import numpy as np

    return float(np.median(np.asarray(x, dtype=float)))


def t_draw_curve(state, svc, args: dict) -> dict:
    param, shape = args.get("param"), args.get("shape")
    if param not in CURVABLE:
        raise ToolError(f"param: not drawable{_suggest(param, CURVABLE)}")
    if shape not in CURVE_SHAPES:
        raise ToolError(f"shape: one of {list(CURVE_SHAPES)}")
    if shape == "clear":
        state.curves.pop(param, None)
        return {"ok": True, "param": param, "cleared": True}
    lo, hi = CURVABLE[param]
    if shape == "points":
        raw = args.get("points")
        if not isinstance(raw, list):
            raise ToolError("points: a list of [t, v] pairs")
        pts = []
        for pt in raw[:MAX_AGENT_CURVE_POINTS]:
            if not isinstance(pt, list | tuple) or len(pt) != 2:
                raise ToolError("points: each point is [t, v]")
            t, v = _number(pt[0], "t"), _number(pt[1], "v")
            pts.append([min(1.0, max(0.0, t)), min(1.0, max(0.0, v))])
        pts.sort(key=lambda q: q[0])
        if len(pts) < 2:
            raise ToolError("points: at least two")
    else:
        d = _number(args.get("depth", 0.3), "depth")
        d = min(1.0, max(0.02, d))
        v = (_median(getattr(state.patch, param)) - lo) / ((hi - lo) or 1.0)
        v = min(0.98, max(0.02, v))
        pts = {
            "rise": [[0.0, v - d / 2], [1.0, v + d / 2]],
            "fall": [[0.0, v + d / 2], [1.0, v - d / 2]],
            "dip": [[0.0, v], [0.5, v - d], [1.0, v]],
            "swell": [[0.0, v], [0.5, v + d], [1.0, v]],
        }[shape]
        pts = [[t, round(min(1.0, max(0.0, y)), 4)] for t, y in pts]
    state.curves[param] = pts
    return {"ok": True, "param": param, "points": len(pts)}


def t_set_macro(state, svc, args: dict) -> dict:
    from ..routes.macros import apply_macro

    k = int(_number(args.get("index"), "index"))
    if not 1 <= k <= len(state.macros):
        raise ToolError(f"index: 1-{len(state.macros)}")
    raw = args.get("targets")
    if not isinstance(raw, list):
        raise ToolError("targets: a list of {param, amount}")
    notes, targets = [], []
    for t in raw:
        name = t.get("param") if isinstance(t, dict) else None
        if name not in MACRO_PARAMS:
            notes.append(
                f"{name!r} can't be a macro target{_suggest(name, MACRO_PARAMS)}"
            )
            continue
        amt = min(1.0, max(-1.0, _number(t.get("amount", 1.0), "amount")))
        targets.append({"param": name, "amt": amt})
    if len(targets) > 3:
        notes.append("only the first 3 targets kept")
    m = state.macros[k - 1]
    m["targets"] = targets[:3]
    if args.get("name"):
        m["name"] = str(args["name"]).strip()[:20] or m["name"]
    moved: list[str] = []
    if args.get("value") is not None:
        m["value"] = min(1.0, max(0.0, _number(args["value"], "value")))
        moved = apply_macro(state, k - 1)
    return {
        "ok": True,
        "macro": k,
        "targets": len(m["targets"]),
        "moved": moved,
        "notes": notes,
    }


def t_choose_phrase(state, svc, args: dict) -> dict:
    phrase = args.get("phrase")
    if phrase not in PHRASES:
        raise ToolError(f"phrase: one of {list(PHRASES)}")
    state.audition = phrase
    return {"ok": True, "phrase": phrase}


TOOLS = {
    "set_params": t_set_params,
    "add_layer": t_add_layer,
    "set_layer": t_set_layer,
    "set_fx_chain": t_set_fx_chain,
    "add_mod_route": t_add_mod_route,
    "draw_curve": t_draw_curve,
    "set_macro": t_set_macro,
    "choose_phrase": t_choose_phrase,
}
assert tuple(TOOLS) == TOOL_NAMES


def apply_call(state, svc, name: str, args) -> dict:
    """Run one tool call against the session (the caller holds the lock).
    Never raises: a refusal or a bug in a tool comes back as an error for
    the model, and the session keeps whatever the tool had not yet done
    (every tool validates before it writes)."""
    fn = TOOLS.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool {name!r}{_suggest(name, TOOLS)}"}
    if not isinstance(args, dict):
        return {"ok": False, "error": "arguments must be an object"}
    try:
        return fn(state, svc, args)
    except ToolError as err:
        return {"ok": False, "error": str(err)[:300]}
    except Exception as err:  # noqa: BLE001 - model input must never 500 a request
        return {"ok": False, "error": f"{type(err).__name__}: {str(err)[:200]}"}
