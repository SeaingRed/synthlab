"""What the sound agent tells the model, and what it measures.

- `describe(state)`: the current sound, compact — the edited patch's
  non-default fields only, the layer strip, the FX chains, curves, macros,
  the phrase and key. A few hundred tokens, not the whole genome.
- `measure(result)`: numbers from the ▶ render (`svc.render("editor")`),
  the same audio the user hears — centroid/brightness, sub share, loudness,
  stereo width (with the side level and the L/R balance: exact numbers
  the model gets as text, whatever the clip keeps of the image), movement,
  flatness, crest, attack. Loudness alone is taken before the loop's
  normalisation (below).
- `expectations(request)`: the directions a request names ("darker" ->
  centroid falls) that can be checked against those numbers; the loop
  hands the check back to the model once (the self-check) and the eval
  harness scores by it.
- `snapshot` / `restore` / `diff`: the whole editable stack around a turn
  (Undo turn, "Before last load", the per-turn changes list — computed
  from state, never taken from the model's own account).
- `audio_clip(result)`: ~2 s of the render as a 16 kHz WAV, stereo when
  the sound is (side above `MONO_SIDE_DB`), mono when L = R.

The ▶ render is percentile-normalised (`render.normalize_master`), so its
own loudness barely moved between versions of a sound: halving the
oscillator levels (-6 dB) measured -0.8 dB. `render_loop` keeps the gain it
applied (`RenderResult.norm_gain_db`, one float — no second copy of the
audio), and `measure` subtracts it: loudness is the loudest 50 ms of the
summed mix *before* normalisation, exact up to what the master limiter
shaved off peaks above its ceiling. The other features are scale-free or
spectral and read the render as heard.
"""

from __future__ import annotations

import copy
import io
import math
import re
import wave

import numpy as np

from ...analysis import extract, texture
from ...core import SR
from ...params import MOD_SLOTS, PARAMS, slot_active
from ...synth import Patch
from ..audition import PHRASES
from ..fxrack import FX_UNITS, clean_chain
from ..paramui import UI
from .tools import UI_PARAMS

_DEFAULTS = Patch()

SYSTEM = """You are the sound-design assistant inside synthlab studio, a \
subtractive/FM/wavetable synth with layers, FX racks, curves, macros and a mod \
matrix. The user describes a sound or a change; you make it with the tools.

How to work:
- Make the smallest set of changes that clearly achieves the request. Prefer \
set_params on the edited layer; add layers, FX, routes or curves when the \
request needs them (width, movement, weight, space).
- After your tool calls you get measurements of the rendered loop and an \
automatic check of the request's direction. If a check failed, fix it once. \
You have at most {rounds} rounds of tool calls per request.
- The loop you hear is normalised to one level, but the loudness number is \
measured before that normalisation: it follows volume, drive and filter changes.
- Keep sounds usable: no harsh resonance above 0.85 unless asked, keep the \
low end mono and clean (sub layers dry), don't stack reverbs. Stereo width \
reads 0 = mono, ~1 = fully decorrelated, above 1 = out of phase (it collapses \
in mono): aim wide sounds at 0.4-0.9 and keep the width FX amount <= 1.5.
- When you are done, reply in 1-3 short sentences: what you changed and why, \
in sound-design words. No lists of raw numbers, no questions back.

Style cues (generic, use when the request points there): melodic techno \
leads = saw/supersaw, moderate unison, filter envelope pluck or slow cutoff \
movement, synced delay, big reverb on the master; UK garage = short organ/ \
square stabs, swung feel, pitched vocal-chop timbres (vowel filter), punchy \
sub bass under them; festival lead = supersaw unison 5-7, detune ~15-25 ct, \
wide spread, bright cutoff, a little drive, chorus/width and reverb; \
vocal-chop feel = vowel filter (formant) with the vowel moving, short gated \
notes."""


def _num(v) -> str:
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v == 0:
            return "0"
        return f"{v:.3g}" if abs(v) < 1000 else f"{v:.0f}"
    return str(v)


def _is_default(name: str, value) -> bool:
    d = getattr(_DEFAULTS, name)
    if isinstance(value, np.ndarray):
        return False
    if isinstance(value, float) and isinstance(d, int | float):
        return math.isclose(value, float(d), rel_tol=1e-6, abs_tol=1e-9)
    return value == d


def patch_summary(patch: Patch, *, limit: int | None = None) -> dict:
    """The patch's non-default UI fields, by name ("off" for osc2 None).
    Routes that don't run are left out."""
    out: dict = {}
    for name in UI_PARAMS:
        v = getattr(patch, name)
        if _is_default(name, v):
            continue
        slot = next((s for s in MOD_SLOTS if name.startswith(f"{s}_")), None)
        if slot and not slot_active(patch, slot):
            continue
        out[name] = (
            "off"
            if v is None
            else (round(float(np.median(v)), 4) if isinstance(v, np.ndarray) else v)
        )
    if limit is not None and len(out) > limit:
        out = dict(list(out.items())[:limit])
    return out


def _fx_text(chain) -> str:
    units = clean_chain(chain)
    if not units:
        return "none"
    parts = []
    for u in units:
        ps = u["params"]
        dflt = {k: d for k, (_lo, _hi, d) in FX_UNITS[u["kind"]]["params"].items()}
        moved = {k: v for k, v in ps.items() if not math.isclose(v, dflt[k])}
        inner = ", ".join(f"{k} {_num(v)}" for k, v in moved.items())
        parts.append(f"{u['kind']}({inner})" if inner else u["kind"])
    return " > ".join(parts)


def _curve_text(points) -> str:
    ys = [p[1] for p in points]
    if len(ys) >= 3 and ys[1] != ys[0]:
        shape = "dip" if ys[1] < ys[0] else "swell"
    else:
        shape = "rise" if ys[-1] >= ys[0] else "fall"
    return f"{shape} {ys[0]:.2f}->{ys[-1]:.2f} ({len(points)} pts)"


def layer_rows(state) -> list[str]:
    main, act = state.main_index(), state.active_layer
    rows = []
    for i, lay in enumerate(state.layers):
        editing = i == act
        if not editing and not lay.get("patch"):
            rows.append(f"layer {i + 1}: empty")
            continue
        patch = state.patch if editing else Patch.from_dict(lay["patch"])
        tags = []
        if i == main:
            tags.append("MAIN")
        if editing:
            tags.append("editing")
        if lay.get("role"):
            tags.append(lay["role"])
        if state.follows(i):
            tags.append(f"follows main {lay['follow']['octave']:+d} oct")
        gain = float(lay.get("gain", 1.0))
        tags.append(
            f"level {20 * math.log10(gain):+.1f} dB" if gain > 0 else "level -inf"
        )
        if lay.get("muted"):
            tags.append("muted")
        if lay.get("solo"):
            tags.append("solo")
        head = f'layer {i + 1} "{patch.name or "Untitled"}" [{", ".join(tags)}]'
        body = patch_summary(patch, limit=None if editing else 18)
        params = ", ".join(f"{k}={_num(v)}" for k, v in body.items()) or "all defaults"
        rows.append(f"{head}\n  params: {params}\n  fx: {_fx_text(lay.get('fx'))}")
    return rows


def describe(state) -> str:
    """The current sound for the model: compact, the edited layer in full."""
    arr = state.arrangement
    lines = [
        f"Key {arr.root} {arr.scale_name}, {arr.bpm:g} bpm. Reference phrase: "
        f"{state.audition} ({', '.join(PHRASES)} available).",
        *layer_rows(state),
        f"master fx: {_fx_text(state.fx_master)}",
    ]
    curves = {k: v for k, v in state.curves.items() if len(v) >= 2}
    if curves:
        lines.append(
            "curves (edited layer): "
            + "; ".join(f"{k} {_curve_text(v)}" for k, v in curves.items())
        )
    macros = [m for m in state.macros if m.get("targets")]
    if macros:
        lines.append(
            "macros: "
            + "; ".join(
                f"{m['name']}->" + ",".join(t["param"] for t in m["targets"])
                for m in macros
            )
        )
    return "\n".join(lines)


# --- measuring ----------------------------------------------------------------

FEATURE_KEYS = (
    "centroid_hz",
    "brightness",
    "sub_pct",
    "loudness_db",
    "width",
    "side_db",
    "balance_db",
    "movement",
    "flatness",
    "crest_db",
    "attack_ms",
)


# a side signal this far under the mid is no stereo image: L = R within 1%
MONO_SIDE_DB = -40.0
_STEREO_DB = (-80.0, 40.0)  # the range the two stereo levels are reported in


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def _ratio_db(a: float, b: float) -> float:
    lo, hi = _STEREO_DB
    if a <= 1e-9 and b <= 1e-9:
        return 0.0
    return float(min(hi, max(lo, 20 * math.log10(max(a, 1e-12) / max(b, 1e-12)))))


def stereo_facts(audio: np.ndarray) -> tuple[float, float]:
    """(side level relative to mid, right level relative to left), in dB.
    Side is (L-R)/2 against (L+R)/2: at or under `MONO_SIDE_DB` the sound
    is mono, 0 dB is as much side as mid, above 0 the mix thins or cancels
    in mono. A mono array is (the floor, 0)."""
    x = np.asarray(audio, dtype=float)
    if x.ndim != 2 or x.shape[1] < 2 or not len(x):
        return _STEREO_DB[0], 0.0
    left, right = x[:, 0], x[:, 1]
    mid, side = _rms((left + right) / 2), _rms((left - right) / 2)
    if side <= 1e-9:
        return _STEREO_DB[0], _ratio_db(_rms(right), _rms(left))
    return _ratio_db(side, mid), _ratio_db(_rms(right), _rms(left))


def measure_audio(audio: np.ndarray, sr: int = SR) -> dict:
    """The numbers the agent (and its checks) read, from one render."""
    f = extract(audio, sr)
    side_db, balance_db = stereo_facts(audio)
    t = texture(audio, sr)
    centroid = float(f.centroid)
    # 0 at 150 Hz .. 1 at 8 kHz, log: the meter's "dark .. bright"
    bright = math.log(max(centroid, 150.0) / 150.0) / math.log(8000.0 / 150.0)
    return {
        "centroid_hz": round(centroid),
        "brightness": round(min(1.0, max(0.0, bright)), 3),
        "sub_pct": round(100 * (f.sub_20_60 + f.sub_60_120), 1),
        "loudness_db": round(t.loudness_db, 1),
        "width": round(float(f.stereo_width), 3),
        "side_db": round(side_db, 1),
        "balance_db": round(balance_db, 1),
        "movement": round(float(t.movement), 3),
        "flatness": round(float(t.flatness), 3),
        "crest_db": round(float(f.crest), 1),
        "attack_ms": round(1000 * float(f.attack_time), 1),
    }


def measure(result) -> dict:
    """`measure_audio` of the render, with loudness read before the loop's
    normalisation when the render says what gain that was (a `render_loop`
    result; the editor's ▶ always is)."""
    m = measure_audio(result.audio())
    gain = getattr(result, "norm_gain_db", None)
    m["loudness_pre_norm"] = gain is not None
    if gain is not None:
        m["loudness_db"] = round(m["loudness_db"] - gain, 1)
    return m


def stereo_text(m: dict) -> str:
    """The stereo image in numbers: what a clip the service may fold to
    mono cannot carry."""
    side = float(m.get("side_db", _STEREO_DB[0]))
    if side <= MONO_SIDE_DB:
        return "mono: L = R"
    bal = float(m.get("balance_db", 0.0))
    lean = (
        "centred" if abs(bal) < 0.5 else f"{'R' if bal > 0 else 'L'} +{abs(bal):.1f} dB"
    )
    return (
        f"L/R correlation {1 - m['width']:.2f}, side {side:+.1f} dB vs mid, "
        f"balance {lean}"
        f"{', out of phase!' if m['width'] > 1 else ''}"
    )


def features_text(m: dict) -> str:
    return (
        f"centroid {m['centroid_hz']} Hz (brightness {m['brightness']:.2f}), "
        f"sub {m['sub_pct']}%, loudness {m['loudness_db']} dBFS"
        f"{' (before normalisation)' if m.get('loudness_pre_norm') else ''}, "
        f"stereo width {m['width']:.3f} ({stereo_text(m)}), "
        f"movement {m['movement']:.3f}, "
        f"noisiness {m['flatness']:.3f}, crest {m['crest_db']} dB, "
        f"attack {m['attack_ms']} ms"
    )


def audio_clip(
    result, seconds: float = 2.0, rate: int = 16000, stereo: bool = True
) -> bytes:
    """~`seconds` of the render from its first sound, `rate` Hz, 16-bit
    WAV: stereo (~128 KB for 2 s) when `stereo` and the clip has an image
    (its side above `MONO_SIDE_DB`), else mono (~64 KB) — a mono sound's
    second channel would be bytes for no information.

    The audio is billed by its length (~32 tokens a second, as Google's
    audio page had it: 64 tokens either way), so stereo costs upload
    bytes, not tokens. 24 kHz was weighed and left out: half as many bytes
    again for a band the same page says is resampled to 16 kHz. That page
    also says channels are folded to one, which the SDK neither confirms
    nor denies: if so the stereo clip is heard as the old mono one, and
    the image reaches the model only as `stereo_text`'s numbers."""
    from scipy.signal import resample_poly

    x = result.audio()
    mono = x.mean(axis=1) if x.ndim == 2 else x
    loud = np.nonzero(np.abs(mono) > 1e-3)[0]
    start = int(loud[0]) if len(loud) else 0
    stop = start + int(seconds * SR)
    seg = mono[start:stop]
    if stereo and x.ndim == 2 and stereo_facts(x[start:stop])[0] > MONO_SIDE_DB:
        seg = x[start:stop]
    g = math.gcd(rate, SR)
    y = resample_poly(seg, rate // g, SR // g, axis=0)
    pcm = (np.clip(y, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2 if pcm.ndim == 2 else 1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())  # (n, 2) in C order: L R L R ...
    return buf.getvalue()


def clip_note(clip: bytes) -> str:
    """The prompt's line about the attached clip, read off its header."""
    with wave.open(io.BytesIO(clip), "rb") as w:
        channels, rate = w.getnchannels(), w.getframerate()
        secs = w.getnframes() / rate
    return (
        f"(Attached: ~{secs:.0f} s of the loop, {rate / 1000:g} kHz "
        f"{'stereo' if channels == 2 else 'mono'}: broad character only; "
        "for width trust the measured numbers.)"
    )


# --- expectations (the self-check) ---------------------------------------------
# (words, feature, direction, the smallest change that counts)
_RULES = [
    (
        r"\bdark(er|en)?\b|\bwarm(er)?\b|\bmuffled\b|\bduller\b|\bmellow(er)?\b",
        "centroid_hz",
        -1,
        0.05,
    ),
    (
        r"\bbright(er|en)?\b|\bcrisp(er)?\b|\bairy\b|\bairier\b|\bsparkl",
        "centroid_hz",
        +1,
        0.05,
    ),
    (
        r"\bwide(r|n)?\b|\bwidth\b|\bstereo\b|\bspacious\b|\bsupersaw\b|\bfestival\b",
        "width",
        +1,
        0.02,
    ),
    (r"\bnarrow(er)?\b|\bmono\b", "width", -1, 0.02),
    (r"\bmovement\b|\bmoving\b|\bevolv|\banimat|\bwobbl|\bsweep", "movement", +1, 0.01),
    (r"\bstatic\b|\bstill(er)?\b|\bsteady\b", "movement", -1, 0.01),
    (r"\bpunch(y|ier)?\b|\bsnapp(y|ier)\b|\btight(er)?\b", "crest_db", +1, 0.3),
    (
        r"\bmore (sub|bass|low end|weight)\b|\bheav(y|ier)\b|\bfatter\b|\bweight\b",
        "sub_pct",
        +1,
        1.0,
    ),
    (r"\bless (sub|bass|low end)\b|\bthinn?(er)?\b", "sub_pct", -1, 1.0),
    (
        r"\bnois(y|ier)\b|\bgritt(y|ier)\b|\bdirt(y|ier)\b|\bdistort",
        "flatness",
        +1,
        0.005,
    ),
    (r"\bclean(er)?\b|\bpur(e|er)\b", "flatness", -1, 0.005),
]


def expectations(request: str) -> list[tuple[str, int, float]]:
    """(feature, direction, min change) for every direction `request` names.
    A feature named both ways ("darker but brighter attack") is dropped."""
    text = request.lower()
    found: dict[str, tuple[int, float]] = {}
    clash: set[str] = set()
    for pattern, feat, sign, tol in _RULES:
        if re.search(pattern, text):
            if feat in found and found[feat][0] != sign:
                clash.add(feat)
            found[feat] = (sign, tol)
    return [(f, s, t) for f, (s, t) in found.items() if f not in clash]


def check(expect, before: dict, after: dict) -> list[dict]:
    """Each expectation against the before/after numbers. Centroid is
    judged as a ratio (5% = 0.05), the rest as plain differences."""
    out = []
    for feat, sign, tol in expect:
        a, b = float(before[feat]), float(after[feat])
        delta = (b - a) / max(a, 1.0) if feat == "centroid_hz" else b - a
        out.append(
            {
                "feature": feat,
                "want": "up" if sign > 0 else "down",
                "before": before[feat],
                "after": after[feat],
                "met": delta * sign >= tol,
            }
        )
    return out


def check_text(results: list[dict]) -> str:
    if not results:
        return "No automatic check applies to this request: judge by the numbers."
    parts = [
        f"{r['feature']} should go {r['want']}: {r['before']} -> {r['after']} "
        f"({'met' if r['met'] else 'NOT met'})"
        for r in results
    ]
    return "Automatic check: " + "; ".join(parts) + "."


# --- the stack a turn can change ------------------------------------------------


def snapshot(state) -> dict:
    """Everything the agent's tools can write, as plain data."""
    return {
        "patch": state.patch.to_json(),
        "layers": copy.deepcopy(state.layers),
        "curves": copy.deepcopy(state.curves),
        "macros": copy.deepcopy(state.macros),
        "fx_master": copy.deepcopy(state.fx_master),
        "audition": state.audition,
        "main_layer": state.main_layer,
        "active_layer": state.active_layer,
    }


def restore(state, snap: dict) -> None:
    state.patch = Patch.from_dict(snap["patch"])
    state.layers = copy.deepcopy(snap["layers"])
    state.curves = copy.deepcopy(snap["curves"])
    state.macros = copy.deepcopy(snap["macros"])
    state.fx_master = copy.deepcopy(snap["fx_master"])
    state.audition = snap["audition"]
    state.main_layer = snap["main_layer"]


def _label(name: str) -> str:
    return UI[name].label if name in UI else name


def _val(name: str, v) -> str:
    if v is None:
        return "off"
    if isinstance(v, np.ndarray):
        return "curve"
    unit = UI[name].unit if name in UI else ""
    s = _num(round(v, 4) if isinstance(v, float) else v)
    return f"{s} {unit}".strip()


def _patch_changes(a: dict | None, b: dict | None, prefix: str = "") -> list[str]:
    if not a or not b:
        return []
    out = []
    for name in UI_PARAMS:
        va, vb = a.get(name), b.get(name)
        if va == vb or (
            isinstance(va, float)
            and isinstance(vb, float)
            and math.isclose(va, vb, rel_tol=1e-9)
        ):
            continue
        out.append(f"{prefix}{_label(name)}: {_val(name, va)} → {_val(name, vb)}")
    return out


def diff(before: dict, after: dict) -> list[str]:
    """What a turn changed, in the panels' own words."""
    out = _patch_changes(before["patch"], after["patch"])
    for i, (la, lb) in enumerate(zip(before["layers"], after["layers"], strict=False)):
        n = i + 1
        if i == after["active_layer"]:
            if (la.get("fx") or []) != (lb.get("fx") or []):
                out.append(
                    f"Layer {n} FX: {_fx_text(la.get('fx'))} → {_fx_text(lb.get('fx'))}"
                )
            continue
        if not la.get("patch") and lb.get("patch"):
            role = f" ({lb['role']})" if lb.get("role") else ""
            oct_ = (lb.get("follow") or {}).get("octave")
            follow = f", follows {oct_:+d} oct" if oct_ is not None else ""
            out.append(f"+ Layer {n}{role}: {lb['patch'].get('name') or 'new'}{follow}")
            if lb.get("fx"):
                out.append(f"Layer {n} FX: {_fx_text(lb.get('fx'))}")
            continue
        out += _patch_changes(la.get("patch"), lb.get("patch"), f"Layer {n} ")
        if (la.get("fx") or []) != (lb.get("fx") or []):
            out.append(
                f"Layer {n} FX: {_fx_text(la.get('fx'))} → {_fx_text(lb.get('fx'))}"
            )
        ga, gb = float(la.get("gain", 1.0)), float(lb.get("gain", 1.0))
        if not math.isclose(ga, gb):
            db = lambda g: f"{20 * math.log10(g):+.1f} dB" if g > 0 else "-inf"  # noqa: E731
            out.append(f"Layer {n} level: {db(ga)} → {db(gb)}")
        for key, word in (("muted", "mute"), ("solo", "solo")):
            if bool(la.get(key)) != bool(lb.get(key)):
                out.append(f"Layer {n} {word} {'on' if lb.get(key) else 'off'}")
        if la.get("follow") != lb.get("follow"):
            f = lb.get("follow")
            out.append(f"Layer {n} follow: {f'{f["octave"]:+d} oct' if f else 'off'}")
    if before["fx_master"] != after["fx_master"]:
        was, now = _fx_text(before["fx_master"]), _fx_text(after["fx_master"])
        out.append(f"Master FX: {was} → {now}")
    for param in sorted(set(before["curves"]) | set(after["curves"])):
        ca, cb = before["curves"].get(param), after["curves"].get(param)
        if ca != cb:
            label = _label(param)
            out.append(
                f"Curve {label}: {_curve_text(cb)}"
                if cb and len(cb) >= 2
                else f"Curve {label} removed"
            )
    for ma, mb in zip(before["macros"], after["macros"], strict=False):
        if ma != mb:
            tg = ", ".join(
                f"{_label(t['param'])} {t['amt']:+.2f}" for t in mb.get("targets") or []
            )
            out.append(f"Macro {mb.get('name')}: {tg or 'no targets'}")
    if before["audition"] != after["audition"]:
        out.append(f"Phrase: {before['audition']} → {after['audition']}")
    return out


assert set(UI_PARAMS) <= set(PARAMS)
