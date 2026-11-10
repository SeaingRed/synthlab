"""Make from a clip: the clip hub's "→ Beat", "→ Notes" and "→ Sound".

A clip is listened to (`analysis.drumhits`, `analysis.voiced`, the tempo
and key `cliphub.measure` found) and turned into a **candidate**: the
`Arrangement` fields a beat or a note part is, the drum kit or the sound
it is played by, the FX chain — never written to the session here. The
route renders the candidate as a preview (`StudioService.preview_inputs`)
and, on Apply, makes it again from the same plan and seed (the analyses
are cached, the generators seeded: it is the same candidate) and writes
it through the paths a hand edit takes (`StudioState.apply_pattern`,
`load_sound`, `routes.sampler.adopt_audio`).

**The plan** (`clean_plan`) is what to make and how: the target, the kit,
a beat style to blend with, swing / density / a fill / half- or
double-time, where the notes come from, the sound that plays them, a
transpose, an FX chain preset. The pickers on the page fill it; Gemini
(`clipmorph.py`) may fill it from a description and the clip's audio. It
is **untrusted either way**: every name must be one the studio has (a
kit, a style, a sound, a chain preset) and every number is clamped, or it
falls back to its default — and says so.

What the analysis can and cannot do (said to the player in `said`):

- a beat is read off the clip's onsets as kick / snare / hats
  (`drumhits`: four lanes, sixteenths, swing); a dense mix reads rough,
  and toms, claps, rims and crashes are not told apart;
- a melody is YIN on one line (`voiced.pitch_track`, 70-1100 Hz): a
  monophonic voice, lead or bass line reads well; chords, a full mix or a
  sub-bass under 70 Hz do not — the notes found are then sparse or wrong;
- the clip's grid is its meta's tempo when it states one (a bounce, a
  fitted Lab chop), else the measured tempo from its first beat, else the
  loop's tempo from its start.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field, replace

import numpy as np

from .. import sampler
from ..analysis import drumhits, voiced
from ..core import SR
from ..sequencer import SCALES, midi, name_of, scale
from . import beatgen, drumops, labmake, limits, samplerview
from .drumfactory import DRUM_KITS
from .fxrack import CHAIN_PRESETS, chain_preset
from .gen import drums as gd
from .gen import loop as gl
from .gen import notes as nt
from .lru import LRU
from .state import DRUM_ROWS, ROOTS
from .state.model import grid_rows

TARGETS = ("beat", "notes", "sound")
KITS = ("own", "keep", *DRUM_KITS)
STYLES = ("clip", *beatgen.STYLES)
DENSITIES = ("same", "busier", "sparser")
FEELS = ("as is", "half-time", "double-time")
FILLS = ("none", "half", "bar")
NOTE_SOURCES = ("melody", "rhythm", *(k for k, _ in gl.KINDS))
SOUND_SOURCES = ("keep", "clip", "library", "recipes", "factory")
SOUND_MODES = ("whole", "chops", "voice")
SNAPS = ("loop", "clip", "none")
FXS = ("keep", "none", *CHAIN_PRESETS)
MAX_TEXT = 200  # a plan's summary, a "why" line
MAX_WHY = 4
MIN_TEMPO_CONF = 0.2  # a measured grid under this is not trusted

DEFAULT_PLAN = {
    "target": "beat",
    "kit": "own",
    "style": "clip",
    "density": "same",
    "amount": 0.25,
    "swing": None,  # None: the clip's own
    "feel": "as is",
    "fill": "none",
    "loop_tempo": False,
    "notes_from": "melody",
    "sound": {"source": "keep", "name": ""},
    "sound_mode": "whole",
    "transpose": 0,
    "snap": "loop",
    "fx": "keep",
    "summary": "",
    "why": [],
}
# what a picker left on "auto" leaves to the description (or the default)
LOCKABLE = (
    "kit",
    "style",
    "density",
    "feel",
    "fill",
    "notes_from",
    "sound",
    "sound_mode",
    "snap",
    "fx",
)


def labels() -> dict:
    """What the pickers offer, by field (the template's options)."""
    return {
        "kits": [("own", "the clip's own hits"), ("keep", "the kit on Kit now")]
        + [(k, f"{k} kit") for k in DRUM_KITS],
        "styles": [("clip", "the clip's beat as it is")]
        + [(s, f"blend with {s}") for s in beatgen.STYLES],
        "notes_from": [
            ("melody", "the clip's melody"),
            ("rhythm", "the clip's rhythm, on the root"),
        ]
        + [(k, f"a {label} over the clip's key") for k, label in gl.KINDS],
        "fxs": [("keep", "keep the FX"), ("none", "no FX")]
        + [(k, v["label"]) for k, v in CHAIN_PRESETS.items()],
    }


# --- the plan -------------------------------------------------------------------


def _text(v, limit: int = MAX_TEXT) -> str:
    if not isinstance(v, str):
        return ""
    return " ".join(v.split())[:limit]


def _choice(v, allowed, default, name: str, notes: list[str]):
    if v is None or v == "":
        return default
    if isinstance(v, str) and v in allowed:
        return v
    notes.append(f"{name} “{_text(str(v), 40)}” is not one the studio has: {default}.")
    return default


def _number(v, lo: float, hi: float, default):
    if isinstance(v, bool):
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return min(hi, max(lo, x))


def clean_plan(raw, sounds=None) -> tuple[dict, list[str]]:
    """(the plan, what was refused) from anything — a form's fields or
    Gemini's JSON. Every name is checked against the studio's own lists
    and every number clamped; what does not fit is its default, and a line
    in the notes says so. `sounds(source, name) -> bool` says whether a
    library / recipe / factory sound of that name exists (None: any name
    is refused, so a sound can only be "keep" or "clip")."""
    raw = raw if isinstance(raw, dict) else {}
    notes: list[str] = []
    p = dict(DEFAULT_PLAN)
    p["target"] = _choice(raw.get("target"), TARGETS, "beat", "Target", notes)
    p["kit"] = _choice(raw.get("kit"), KITS, "own", "Kit", notes)
    p["style"] = _choice(raw.get("style"), STYLES, "clip", "Beat style", notes)
    p["density"] = _choice(raw.get("density"), DENSITIES, "same", "Density", notes)
    p["amount"] = round(_number(raw.get("amount"), 0.05, 1.0, 0.25), 2)
    sw = raw.get("swing")
    p["swing"] = (
        None
        if sw is None or sw == "" or sw == "auto"
        else round(_number(sw, 0.0, drumhits.MAX_SWING, 0.0), 2)
    )
    p["feel"] = _choice(raw.get("feel"), FEELS, "as is", "Feel", notes)
    p["fill"] = _choice(raw.get("fill"), FILLS, "none", "Fill", notes)
    p["loop_tempo"] = raw.get("loop_tempo") in (True, "on", "1", "true")
    p["notes_from"] = _choice(
        raw.get("notes_from"), NOTE_SOURCES, "melody", "Notes from", notes
    )
    p["sound_mode"] = _choice(
        raw.get("sound_mode"), SOUND_MODES, "whole", "Sampler mode", notes
    )
    p["transpose"] = int(round(_number(raw.get("transpose"), -24, 24, 0)))
    p["snap"] = _choice(raw.get("snap"), SNAPS, "loop", "Key", notes)
    p["fx"] = _choice(raw.get("fx"), FXS, "keep", "FX chain", notes)
    snd = raw.get("sound")
    if isinstance(snd, str):  # a picker posts "library:Name"
        source, _, name = snd.partition(":")
        snd = {"source": source, "name": name}
    snd = snd if isinstance(snd, dict) else {}
    source = _choice(snd.get("source"), SOUND_SOURCES, "keep", "Sound source", notes)
    name = _text(snd.get("name"), 80)
    if source in ("library", "recipes", "factory"):
        if not name or sounds is None or not sounds(source, name):
            notes.append(f"No {source} sound is called “{name}”: the sound stays.")
            source, name = "keep", ""
    else:
        name = ""
    p["sound"] = {"source": source, "name": name}
    p["summary"] = _text(raw.get("summary"))
    why = raw.get("why") if isinstance(raw.get("why"), list) else []
    p["why"] = [t for t in (_text(w) for w in why[:MAX_WHY]) if t]
    return p, notes


def locked(form) -> dict:
    """The pickers the player set (not "auto"), as plan fields: they win
    over what a description asks for."""
    out = {}
    get = form.get if hasattr(form, "get") else (lambda _k: None)
    for k in LOCKABLE:
        v = get(k)
        if isinstance(v, str) and v and v != "auto":
            out[k] = v
    for k, idle in (("transpose", 0.0), ("swing", None)):  # moved off its rest
        v = get(k)
        if isinstance(v, str) and v.strip():
            x = _number(v, -1e9, 1e9, idle)
            if x != idle:
                out[k] = x
    if get("loop_tempo") == "on":
        out["loop_tempo"] = True
    return out


def plan_from_form(form, sounds=None) -> tuple[dict, list[str]]:
    """The plan the pickers alone make (no description, no Gemini): every
    "auto" is the default."""
    raw = {k: form.get(k) for k in DEFAULT_PLAN if k not in ("summary", "why")}
    for k, v in list(raw.items()):
        if v == "auto":
            raw[k] = None
    return clean_plan(raw, sounds)


# --- the clip's grid --------------------------------------------------------------


@dataclass(frozen=True)
class Grid:
    bpm: float
    bar: float  # seconds
    offset: float  # seconds into the clip where bar 1 starts
    bars: int  # whole bars the loop takes (`limits.BAR_CHOICES`)
    said: str


def clip_grid(audio: np.ndarray, meta: dict, facts: dict, session_bpm: float) -> Grid:
    """Where the clip's bars are: its meta's tempo (a bounce, a fitted
    chop: bar 1 at its start), else the measured one (bar 1 at the first
    downbeat, a guess), else the loop's tempo from the start."""
    seconds = len(audio) / SR
    bpm, offset = 0.0, 0.0
    m = meta.get("bpm")
    lab = meta.get("lab") if isinstance(meta.get("lab"), dict) else {}
    if isinstance(m, (int, float)) and not isinstance(m, bool) and 30 <= m <= 300:
        bpm, said = float(m), f"its own {float(m):g} bpm (from the clip)"
    elif (
        isinstance(lab.get("source_bpm"), (int, float))
        and 30 <= lab["source_bpm"] <= 300
    ):
        bpm, said = (
            float(lab["source_bpm"]),
            f"the Lab song's {lab['source_bpm']:g} bpm",
        )
    elif facts.get("bpm") and facts.get("bpm_conf", 0.0) >= MIN_TEMPO_CONF:
        bpm = float(facts["bpm"])
        bar = 240.0 / bpm
        offset = float(facts.get("downbeat_offset") or 0.0) % bar
        if offset > bar * 0.5:  # a late first downbeat: start on the beat before
            offset = float(facts.get("beat_offset") or 0.0) % (60.0 / bpm)
        said = f"a measured ~{bpm:.0f} bpm (from {offset:.2f} s in)"
    else:
        bpm = float(session_bpm)
        said = f"no tempo of its own found: the loop's {bpm:g} bpm from its start"
    bar = 240.0 / bpm
    held = (seconds - offset) / bar
    fits = [b for b in limits.BAR_CHOICES if b <= held + 0.1]
    bars = max(fits) if fits else 1
    return Grid(round(bpm, 3), bar, round(offset, 4), bars, said)


def loop_bpm(bpm: float) -> float | None:
    """A clip's tempo as the loop can hold it (a whole bpm in range)."""
    lo, hi = limits.BPM_RANGE
    v = float(round(bpm))
    return v if lo <= v <= hi else None


# --- listening (cached by the clip's version) ---------------------------------------

_HEARD: LRU[object] = LRU(32)


def _heard(version: str, what: str, make):
    key = f"{version}|{what}"
    hit = _HEARD.get(key)
    if hit is None:
        hit = make()
        _HEARD.put(key, hit)
    return hit


def _span(audio: np.ndarray, g: Grid) -> np.ndarray:
    a = int(round(g.offset * SR))
    b = a + int(math.ceil(g.bars * g.bar * SR))
    return audio[a:b]


# --- the candidate ---------------------------------------------------------------


@dataclass
class Candidate:
    """What a plan makes of a clip, not yet in the session."""

    target: str
    arr: dict = field(default_factory=dict)  # Arrangement fields to replace
    hits: dict = field(default_factory=dict)  # own kit: lane -> one-shot audio
    hit_names: dict = field(default_factory=dict)  # lane -> the clip it becomes
    patch: object = None  # the sound that plays the notes (None: keep)
    chain: dict | None = None  # a recipe's layers / FX / curves
    sound_label: str = ""
    sampler: dict | None = None  # target sound: adopt_audio's chop / fields
    fx: list | None = None  # the drum bus's or the lane's chain (None: keep)
    style: str | None = None  # the beat style it was dressed in
    grid_span: int | None = None
    rows: list = field(default_factory=list)  # a beat's lanes as notation
    said: list = field(default_factory=list)
    refused: str = ""  # why nothing can be made (then nothing else is set)


def _lane_rows(lane: dict) -> list[tuple[str, str]]:
    total = int(lane["bars"]) * int(lane["steps"])
    out = []
    for voice in DRUM_ROWS:
        on = {int(s) for v, s in lane["drums"] if v == voice}
        if on:
            row = "".join("x" if i in on else "." for i in range(total))
            out.append((voice, row))
    return out


def _half_time(lane: dict) -> dict:
    """Every hit twice as far apart: a bar becomes two (the grid's cap
    kept: what does not fit is the first half of the beat)."""
    bars, steps = int(lane["bars"]), int(lane["steps"])
    cap = max(limits.BAR_CHOICES)
    keep_bars = max(1, min(bars, cap // 2))
    total = keep_bars * steps
    cells = [[v, int(s) * 2] for v, s in lane["drums"] if int(s) < total]
    vel = {int(k): v for k, v in (lane.get("drum_vel") or {}).items()}
    vel2 = {k * 2: v for k, v in vel.items() if k < total}
    return gd._lane(keep_bars * 2, steps, cells, vel2, None, lane.get("swing"))


def _double_time(lane: dict) -> dict:
    """Every hit twice as close: the beat played in half its length,
    twice over (on-steps only: an odd sixteenth has no half)."""
    bars, steps = int(lane["bars"]), int(lane["steps"])
    total = bars * steps
    half = [(v, int(s) // 2) for v, s in lane["drums"] if int(s) % 2 == 0]
    cells = [[v, s + k * (total // 2)] for v, s in half for k in (0, 1)]
    vel = {int(k): v for k, v in (lane.get("drum_vel") or {}).items()}
    vel2 = {}
    for k, v in vel.items():
        if k % 2 == 0:
            vel2[k // 2] = v
            vel2[k // 2 + total // 2] = v
    return gd._lane(bars, steps, cells, vel2, None, lane.get("swing"))


def _blend(clip_lane: dict, style: str, seed: int) -> dict:
    """The clip's kick and snare under the style's other rows (its hats,
    claps, percussion and their feel): what "like this, but techno" means
    to a drum grid. A clip with no kick or snare takes the style's."""
    bars, steps = int(clip_lane["bars"]), int(clip_lane["steps"])
    st = gd.beat(style, seed, bars, steps)
    ours = {"kick", "snare"} & {v for v, _s in clip_lane["drums"]}
    cells = [list(c) for c in clip_lane["drums"] if c[0] in ours]
    cells += [list(c) for c in st["drums"] if c[0] not in ours]
    vel = {int(k): float(v) for k, v in (st.get("drum_vel") or {}).items()}
    vel.update({int(k): float(v) for k, v in (clip_lane.get("drum_vel") or {}).items()})
    hits = {
        k: v for k, v in (st.get("hits") or {}).items() if k.split(".")[0] not in ours
    }
    return gd._lane(bars, steps, cells, vel, hits, st.get("swing"))


def make_beat(
    audio: np.ndarray,
    version: str,
    g: Grid,
    plan: dict,
    arr,
    seed: int,
    title: str,
) -> Candidate:
    """→ Beat: the clip's hits on the drum grid, dressed as the plan says."""
    c = Candidate("beat")
    span = _span(audio, g)
    pat, hits = _heard(
        version,
        f"beat:{g.offset}:{g.bar}:{g.bars}",
        lambda: drumhits.transcribe(span, g.bar, g.bars),
    )
    if not pat.steps and plan["style"] == "clip":
        c.refused = (
            "No drum hits found in that clip: pick a beat style to blend "
            "with, or a clip with a beat in it."
        )
        return c
    lane = gd._lane(
        g.bars,
        drumhits.STEPS_PER_BAR,
        pat.steps,
        {int(k): v for k, v in pat.vel.items()},
    )
    swing = pat.swing
    c.said.append(
        f"Heard {labmake.pattern_summary(pat)} over {g.bars} bar"
        f"{'s' if g.bars > 1 else ''} at {g.said}."
    )
    style = plan["style"]
    if style != "clip":
        lane = _blend(lane, style, seed)
        swing = beatgen.STYLE_SWING.get(style, swing)
        c.style = style
        c.said.append(
            f"Blended with {style}: the clip's kick and snare, its other rows."
        )
    if plan["feel"] == "half-time":
        lane = _half_time(lane)
        c.said.append("Half-time: every hit twice as far apart.")
    elif plan["feel"] == "double-time":
        lane = _double_time(lane)
        c.said.append("Double-time: the beat twice over in its length.")
    if plan["density"] != "same" and lane["drums"]:
        lane = (gd.busier if plan["density"] == "busier" else gd.sparser)(
            lane, plan["amount"], seed
        )
        c.said.append(f"{plan['density'].capitalize()} by {plan['amount']:.0%}.")
    if plan["fill"] != "none" and lane["drums"]:
        length = 0.5 if plan["fill"] == "half" or lane["bars"] == 1 else 1.0
        lane = gd.fill(lane, seed, length, None, style=c.style or None)
        c.said.append(f"A fill over the last {'half ' if length == 0.5 else ''}bar.")
    if plan["swing"] is not None:
        swing = plan["swing"]
    fields = drumops.loop_fields(lane)
    fields["drum_swing"] = float(swing)
    if plan["loop_tempo"]:
        bpm = loop_bpm(g.bpm)
        if bpm:
            fields["bpm"] = bpm
            c.said.append(f"The loop takes the clip's tempo, {bpm:g} bpm.")
    elif abs(g.bpm - arr.bpm) > 0.5:
        c.said.append(f"It plays at the loop's {arr.bpm:g} bpm, not the clip's.")
    kit = plan["kit"]
    samples = dict(arr.drum_samples)
    if kit == "own":
        shots = _heard(
            version,
            f"kit:{g.offset}:{g.bar}:{g.bars}",
            lambda: labmake.kit_hits(span, hits),
        )
        if shots:
            for lane_name, shot in shots.items():
                c.hits[lane_name] = shot
                c.hit_names[lane_name] = f"{title[:40]} {lane_name}"
                samples[lane_name] = c.hit_names[lane_name]
            c.said.append(
                f"Kit: the clip's own {', '.join(shots)} as one-shots on those rows."
            )
        else:
            c.said.append("No hit stood alone to cut a kit from: the kit stays.")
    elif kit != "keep":
        spec = DRUM_KITS[kit]
        swap = drumops.swap_kit(drumops.loop_lane(arr), spec, arr.drum_samples)
        fields["drum_params"] = dict(arr.drum_params) | swap["params"]
        for lane_name in drumhits.LANES:
            samples.pop(lane_name, None)
        c.style = c.style or spec["style"]
        c.said.append(f"Kit: {kit} (its voices; the rows' clips set back to synth).")
    if samples != dict(arr.drum_samples):
        fields["drum_samples"] = samples
    if plan["fx"] != "keep":
        c.fx = [] if plan["fx"] == "none" else chain_preset(plan["fx"]) or []
        c.said.append(
            "No FX on the drum bus."
            if plan["fx"] == "none"
            else f"Drum bus FX: {CHAIN_PRESETS[plan['fx']]['label']}."
        )
    c.arr = fields
    c.rows = _lane_rows(lane)
    return c


# --- notes -----------------------------------------------------------------------


def key_classes(root: str, scale_name: str) -> set[int]:
    return {m % 12 for m in scale(root, scale_name, octaves=1)}


def parse_key(name: str) -> tuple[str, str] | None:
    """A measured key ("A minor") as the loop's (root note, scale)."""
    if not isinstance(name, str):
        return None
    tonic, _, mode = name.partition(" ")
    if mode not in ("major", "minor"):
        return None
    for r in ROOTS:  # the bass-register root the loop keeps (A0..E3)
        if r[:-1] == tonic and "1" <= r[-1] <= "2":
            return r, mode
    return None


def melody_lane(
    audio: np.ndarray, g: Grid, classes: set[int] | None
) -> tuple[dict, int]:
    """The clip's line as a sixteenth note lane in absolute MIDI (the
    `notes.Lane` shape): each step the median of its voiced frames,
    snapped to `classes`, held while it stays, re-struck when the level
    jumps. (lane, notes found)."""
    span = _span(audio, g)
    track = voiced.pitch_track(span)
    out = nt.Lane(g.bars)
    total = g.bars * nt.SPB
    if not len(track.notes):
        return out.data(), 0
    step = g.bar / nt.SPB * SR
    centres = np.arange(len(track.notes)) * track.hop + track.span / 2
    per = []
    for k in range(total):
        sel = (centres >= k * step) & (centres < (k + 1) * step)
        on = sel & track.voiced
        if sel.sum() and on.sum() / sel.sum() >= 0.5:
            m = float(np.median(track.notes[on]))
            if classes:
                m = float(voiced.snap(np.array([m]), classes)[0])
            per.append((int(round(m)), float(np.median(track.level[on]))))
        else:
            per.append(None)
    count = 0
    k = 0
    while k < total:
        if per[k] is None:
            k += 1
            continue
        m, lvl = per[k]
        j = k + 1
        while j < total and per[j] is not None and per[j][0] == m:
            if per[j][1] > per[j - 1][1] * 1.8:  # a new attack on the same note
                break
            j += 1
        out.note(m, k, j - k, vel=0.45 + 0.55 * min(1.0, lvl))
        count += 1
        k = j
    return out.data(), count


def rhythm_lane(audio: np.ndarray, g: Grid, root_midi: int) -> tuple[dict, int]:
    """The clip's onsets as notes on the root: each held to the next onset
    (at most a beat). (lane, notes)."""
    span = _span(audio, g)
    hits = drumhits.find_hits(span)
    out = nt.Lane(g.bars)
    total = g.bars * nt.SPB
    step = g.bar / nt.SPB
    starts = sorted({int(round(h.time / step)) for h in hits if h.time / step < total})
    loud = max((h.level for h in hits), default=1.0) or 1.0
    by = {int(round(h.time / step)): h.level for h in hits}
    for i, s in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else total
        lvl = 10.0 ** ((by.get(s, loud) - loud) / 20.0)
        out.note(root_midi, s, max(1, min(4, nxt - s)), vel=0.5 + 0.5 * min(1.0, lvl))
    return out.data(), len(starts)


def _fit_shift(lane: dict, rows: list[str]) -> int:
    """Whole octaves that put the most of `lane` on the grid's rows."""
    shown = {midi(r) for r in rows}
    pitches = [midi(p) for p, _s in lane["cells"]]
    if not pitches:
        return 0
    best = max(
        range(-6, 7),
        key=lambda k: (sum(p + 12 * k in shown for p in pitches), -abs(k)),
    )
    return 12 * best


def make_notes(
    audio: np.ndarray,
    version: str,
    g: Grid,
    plan: dict,
    arr,
    grid_oct: int,
    seed: int,
    song: dict,
    facts: dict,
) -> Candidate:
    """→ Notes: a part on the working loop's note grid from the clip."""
    c = Candidate("notes")
    if arr.arp_mode != "off":
        c.refused = "The arp plays every step: switch it off to hear a written part."
        return c
    root, scale_name = arr.root, arr.scale_name
    if plan["snap"] == "clip":
        got = parse_key(facts.get("key", ""))
        if got:
            root, scale_name = got
            c.said.append(f"In the clip's key, {facts['key']} (the loop moves to it).")
        else:
            c.said.append("No key of its own found in the clip: the loop's key.")
    base = replace(arr, root=root, scale_name=scale_name, bars=g.bars, steps_per_bar=16)
    span = 2 if plan["snap"] != "none" else 3
    rows = grid_rows(base, span, grid_oct)
    classes = key_classes(root, scale_name) if plan["snap"] != "none" else None
    src = plan["notes_from"]
    if src in ("melody", "rhythm"):
        if src == "melody":
            lane, n = _heard(
                version,
                f"melody:{g.offset}:{g.bar}:{g.bars}:{root}:{scale_name}:{plan['snap']}",
                lambda: melody_lane(audio, g, classes),
            )
            if n < 2:
                c.said.append(
                    "No melody to follow in that clip (pitch needs one clear line): "
                    "its rhythm on the root instead."
                )
                src = "rhythm"
            else:
                c.said.append(f"Followed its melody: {n} notes over {g.bars} bar(s).")
        if src == "rhythm":
            lane, n = rhythm_lane(audio, g, nt.bass_root({"root": root}))
            if not n:
                c.refused = "Nothing to follow in that clip: no pitch and no onsets."
                return c
            c.said.append(f"Its rhythm on the root: {n} notes.")
        shift = _fit_shift(lane, rows) + plan["transpose"]
        fields, lost = gl._onto(lane, base, rows, shift)
        if lost:
            c.said.append(f"{lost} row(s) outside the grid's window were left out.")
    else:
        try:
            fields, note = gl.write(base, rows, src, seed, song)
        except gl.Refused as err:
            c.refused = str(err)
            return c
        if plan["transpose"]:
            moved = nt.transpose({**fields, "bars": g.bars}, plan["transpose"])
            fields = {**fields, "cells": moved["cells"], "cuts": moved["cuts"]}
        c.said.append(f"A {note}, at the clip's {g.bars} bar(s).")
    if plan["transpose"]:
        c.said.append(f"Transposed {plan['transpose']:+d} semitones.")
    cand = replace(base, **fields)
    if plan["density"] != "same":
        try:
            more, note = gl.vary(
                cand, rows, plan["density"], plan["amount"], seed, song
            )
            fields = {**fields, **more}
            c.said.append(note)
        except gl.Refused as err:
            c.said.append(str(err))
    if not fields.get("cells"):
        c.refused = "That made no notes on the grid's rows."
        return c
    c.arr = {**fields, "bars": g.bars, "steps_per_bar": 16}
    if (root, scale_name) != (arr.root, arr.scale_name):
        c.arr.update(root=root, scale_name=scale_name)
    if plan["loop_tempo"] and (bpm := loop_bpm(g.bpm)):
        c.arr["bpm"] = bpm
        c.said.append(f"The loop takes the clip's tempo, {bpm:g} bpm.")
    c.grid_span = span
    pitches = sorted(midi(p) for p, _s in fields["cells"])
    c.said.insert(
        0,
        f"{len(nt.runs(fields | {'bars': g.bars}))} notes, "
        f"{name_of(pitches[0])}–{name_of(pitches[-1])}, at {g.said}.",
    )
    return c


# --- the sound -------------------------------------------------------------------


def sampler_fields(audio: np.ndarray, mode: str) -> tuple[str, dict, str]:
    """(adopt_audio's chop, its Patch fields, what it found) for the clip
    as a sampler instrument: whole, chopped at its transients / syllables,
    or a held note looped as a voice (`labmake`)."""
    if mode == "voice":
        fields, said, _found = labmake.vocal_instrument(audio)
        return "", fields, said
    if mode == "chops":
        chop, said = labmake.vocal_chops(audio)
        return chop or "transients", {}, said or "Chopped at its transients."
    return "", {}, "The whole clip on the keys, from its detected root."


def sampler_patch(audio: np.ndarray, name: str, mode: str):
    """The sampler sound the clip makes, and what it found. Its audio is
    registered in memory only (`sampler.register`, content-addressed): a
    preview leaves nothing in the sample store, and Apply saves it there
    (the same id)."""
    sid = sampler.register(audio, SR)
    chop, fields, said = sampler_fields(audio, mode)
    patch = samplerview.sampler_patch(sid, name)
    if chop == "transients":
        patch, _note = samplerview.chop(patch, "transients")
    elif chop:
        patch = replace(patch, sampler_slices=sampler.clean_slices(chop))
    if fields:
        from .. import params

        patch = params.clamp(replace(patch, **fields))
    return patch, said


def make_sound(audio: np.ndarray, plan: dict) -> Candidate:
    """→ Sound: the clip as a playable sampler sound (Apply is
    `routes.sampler.adopt_audio`, the path "⤷ sampler" takes)."""
    c = Candidate("sound")
    chop, fields, said = sampler_fields(audio, plan["sound_mode"])
    c.sampler = {"chop": chop, "fields": fields}
    c.said.append(said)
    return c


def seed_of(ref: str, seed) -> int:
    """The plan's seed: the posted one, else one from the item's name (a
    first preview is repeatable)."""
    try:
        return int(seed) % limits.SEED_MODULO
    except (TypeError, ValueError):
        return zlib.crc32(ref.encode("utf-8")) % limits.SEED_MODULO


def scale_names() -> tuple[str, ...]:
    return tuple(SCALES)


# --- the sounds a part can be played by ----------------------------------------------

_CATALOGUE: dict = {}


def sound_catalogue() -> dict:
    """{source: [(group, [names])]} — the Library by family, the genre
    recipes, the factory sounds: what the "played by" picker offers and
    what Gemini may name (`clean_plan` holds a named sound to it). Built
    once: all three are the studio's own files."""
    if not _CATALOGUE:
        from .factory import FACTORY
        from .library import by_family
        from .recipes import cards

        _CATALOGUE.update(
            library=[
                (fam, [e["name"] for e in entries]) for fam, entries in by_family()
            ],
            recipes=[("recipes", [c["name"] for c in cards()])],
            factory=[("factory", [e["name"] for e in FACTORY])],
        )
    return _CATALOGUE


def sound_exists(source: str, name: str) -> bool:
    return any(name in names for _g, names in sound_catalogue().get(source, ()))
