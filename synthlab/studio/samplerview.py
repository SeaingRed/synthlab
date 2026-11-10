"""The sampler instrument's studio side: building a sampler sound from a
clip or a render, the chop edits, and what the Source panel's sampler block
(`fragments/sampler_panel.html`, region `sampler-panel`) draws.

The engine half is `synthlab/sampler.py`. Samples live content-addressed in
`<data_dir>/samples/<id>.wav` (`StudioState.sample_dir`); a sampler sound's
Patch holds the id, so every render key carries the audio by content, and
the sound survives its source clip being re-recorded, renamed or deleted
(it keeps the audio it was made from — "Use as sampler" again takes the new
take).
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .. import sampler
from ..sequencer import name_of
from ..synth import Patch

SAMPLING = "sampler"
NUDGE_SECONDS = 0.005  # one ◀/▶ press moves a slice start 5 ms
MIN_SLICE_SECONDS = 0.01  # a nudge or a dragged marker leaves slices this long
CHOPS = ("8", "16", "transients", "clear")
SLICE_ACTIONS = (
    "pitch_up",
    "pitch_down",
    "reverse",
    "nudge_left",
    "nudge_right",
    "move",  # a dragged marker: the start goes to `at` (0..1 of the sample)
)
# pitch detection (`sampler.sample_pitch`): a new sampler sound takes the
# detected root at PITCH_SURE; the panel offers it ("use") from PITCH_SHOWN
PITCH_SURE = 0.8
PITCH_SHOWN = 0.5
DEFAULT_ROOT = 60


def sampling(p: Patch) -> bool:
    return SAMPLING in (p.osc1, p.osc2)


def detected_root(sid: str, sure: float = PITCH_SURE) -> int | None:
    """The sample's pitch as a MIDI key when the detector is at least
    `sure` of it, else None."""
    midi, confidence = sampler.sample_pitch(sid)
    if confidence < sure or not math.isfinite(midi):
        return None
    return min(127, max(0, round(midi)))


def sampler_patch(sid: str, name: str, root: int | None = None) -> Patch:
    """A fresh sampler sound: the sample on osc 1, alone, unfiltered, a
    fast attack and a short release — a recording should sound like itself
    until the player shapes it. Its root is the sample's detected pitch
    when the detector is sure of it (`PITCH_SURE`), else C4 (60): formant
    mode needs the root to be the recording's real pitch."""
    if root is None:
        found = detected_root(sid)
        root = DEFAULT_ROOT if found is None else found
    return Patch(
        name=name[:60] or "sample",
        osc1=SAMPLING,
        osc1_level=1.0,
        osc2=None,
        filter_type="none",
        key_track=0.0,
        attack=0.002,
        decay=0.2,
        sustain=1.0,
        release=0.12,
        sampler_sample=sid,
        sampler_root=int(root),
    )


def chop(p: Patch, mode: str) -> tuple[Patch, str]:
    """(the patch with its chops set by `mode`, a note for the player —
    "" when all went as asked)."""
    if mode == "clear":
        return replace(p, sampler_slices=""), ""
    if mode == "transients":
        found = sampler.transient_slices(p.sampler_sample)
        if len(found) < 2:
            found = sampler.grid_slices(8)
            note = "No clear transients: chopped into 8 instead."
        else:
            note = ""
        return replace(p, sampler_slices=sampler.format_slices(found)), note
    count = int(mode) if mode in ("8", "16") else 8
    start, end = (
        float(np.asarray(p.sampler_start).ravel()[0]),
        float(np.asarray(p.sampler_end).ravel()[0]),
    )
    grid = sampler.grid_slices(count, start, end)
    return replace(p, sampler_slices=sampler.format_slices(grid)), ""


def _min_len(p: Patch) -> float:
    """MIN_SLICE_SECONDS as a fraction of the sample (never under the
    engine's own 1e-3)."""
    secs = max(sampler.seconds(p.sampler_sample), 1e-3)
    return min(0.25, max(1e-3, MIN_SLICE_SECONDS / secs))


def edit_slice(p: Patch, index: int, action: str, at: float | None = None) -> Patch:
    """One slice edit. `move` puts the slice's start at `at` (a dragged
    marker); it and the nudges keep every slice at least MIN_SLICE_SECONDS
    long, and a touching neighbour's end follows the start."""
    slices = sampler.parse_slices(p.sampler_slices)
    if not 0 <= index < len(slices):
        return p
    s = slices[index]
    if action == "pitch_up":
        slices[index] = replace(s, pitch=min(sampler.SLICE_PITCH, s.pitch + 1))
    elif action == "pitch_down":
        slices[index] = replace(s, pitch=max(-sampler.SLICE_PITCH, s.pitch - 1))
    elif action == "reverse":
        slices[index] = replace(s, rev=not s.rev)
    elif action in ("nudge_left", "nudge_right"):
        secs = max(sampler.seconds(p.sampler_sample), 1e-3)
        step = NUDGE_SECONDS / secs * (1 if action == "nudge_right" else -1)
        slices = sampler.nudge(slices, index, step, _min_len(p))
    elif action == "move" and at is not None:
        slices = sampler.move_start(slices, index, at, _min_len(p))
    return replace(p, sampler_slices=sampler.format_slices(slices))


# --- zones: a multi-sample instrument ------------------------------------------

ZONE_ACTIONS = ("set", "step", "move", "remove", "detect")
ZONE_FIELDS = ("lo", "hi", "root", "vlo", "vhi", "rr", "gain", "tune")
ZONES_ACTIONS = ("spread", "chops", "clear")
MAP_MODES = ("pitch", "keys")  # auto-map: spread by detected pitch, or a key each
KEYS_FROM = 60  # "a key each" starts on C4, as chops do from the root


def zones_of(p: Patch) -> list[sampler.Zone]:
    return sampler.parse_zones(p.sampler_zones)


def with_zones(p: Patch, zones: list[sampler.Zone]) -> Patch:
    """`p` playing `zones`; its single sample (what "clear" goes back to)
    is the first zone's when it had none."""
    p = replace(p, sampler_zones=sampler.format_zones(zones))
    if zones and not sampler.valid_id(p.sampler_sample):
        p = replace(p, sampler_sample=zones[0].sample, sampler_root=zones[0].root)
    return p


def as_zones(p: Patch) -> list[sampler.Zone]:
    """The sound's zone map — or, a single-sample sound, its sample as
    the first zone (its root, every key; the patch's span and loop)."""
    zones = zones_of(p)
    if zones or not sampler.valid_id(p.sampler_sample):
        return zones
    return [sampler.Zone(p.sampler_sample, root=int(p.sampler_root))]


def _root_for(sid: str, zones: list[sampler.Zone]) -> int:
    """A new zone's root: its sample's pitch where the detector finds one
    (`PITCH_SHOWN`), else the key above the map's highest root (C4 for
    the first)."""
    found = detected_root(sid, PITCH_SHOWN)
    if found is not None:
        return found
    return min(127, max((z.root for z in zones), default=DEFAULT_ROOT - 1) + 1)


def add_zone(p: Patch, sid: str) -> Patch:
    """`sid` as one more zone (`sampler.place_zone`: its own layer's free
    keys around its root, half a neighbour's, or a key's next take)."""
    zones = as_zones(p)
    zone = sampler.Zone(sid, root=_root_for(sid, zones))
    return with_zones(p, sampler.place_zone(zones, zone))


def auto_map(sids: list[str], mode: str) -> list[sampler.Zone]:
    """Several samples as one instrument. "pitch": each at its detected
    pitch (a sample without one on the next key up), placed lowest first,
    so the keyboard splits at the midpoints and samples sharing a pitch
    become its round-robin takes. "keys": one key each from C4, in order
    — a kit."""
    sids = sids[: sampler.MAX_ZONES]
    if mode == "keys":
        return [
            sampler.Zone(sid, k, k, k)
            for k, sid in zip(range(KEYS_FROM, 128), sids, strict=False)
        ]
    found = [(detected_root(sid, PITCH_SHOWN), i, sid) for i, sid in enumerate(sids)]
    zones: list[sampler.Zone] = []
    for root, _i, sid in sorted(found, key=lambda f: (f[0] is None, f[0] or 0, f[1])):
        zone = sampler.Zone(
            sid, root=root if root is not None else _root_for(sid, zones)
        )
        zones = sampler.place_zone(zones, zone)
    return zones


def _clamp(name: str, value: float):
    lo, hi = {
        "gain": sampler.ZONE_GAIN_DB,
        "tune": (-sampler.ZONE_TUNE, sampler.ZONE_TUNE),
        "rr": (0, sampler.MAX_TAKES - 1),
    }.get(name, (0, 127))
    v = min(hi, max(lo, value))
    return round(v, 2) if name in ("gain", "tune") else int(round(v))


def edit_zone(p: Patch, index: int, action: str, values: dict) -> Patch:
    """One zone edit: `set` its fields (`values`, clamped; an edge set past
    the other one stops there), `step` one (`field` by `by`, from where it
    stands: a row's − / +, so two quick presses are two steps), `move` its
    keys by `dk` and its velocities by `dv` (the range kept whole, inside
    0..127), `remove` it, or `detect` its root (its sample's pitch, when
    there is a clear one)."""
    zones = zones_of(p)
    if not 0 <= index < len(zones):
        return p
    z = zones[index]
    if action == "step":
        field = values.get("field")
        if field not in ZONE_FIELDS:
            return p
        action, values = "set", {field: getattr(z, field) + float(values.get("by", 0))}
    if action == "remove":
        return replace(
            p, sampler_zones=sampler.format_zones(zones[:index] + zones[index + 1 :])
        )
    if action == "detect":
        found = detected_root(z.sample, PITCH_SHOWN)
        z = z if found is None else replace(z, root=found)
    elif action == "move":
        dk = int(values.get("dk", 0))
        dv = int(values.get("dv", 0))
        dk = min(127 - z.hi, max(-z.lo, dk))
        dv = min(127 - z.vhi, max(-z.vlo, dv))
        z = replace(z, lo=z.lo + dk, hi=z.hi + dk, vlo=z.vlo + dv, vhi=z.vhi + dv)
    elif action == "set":
        kw = {k: _clamp(k, float(v)) for k, v in values.items() if k in ZONE_FIELDS}
        if "lo" in kw and "hi" not in kw:
            kw["lo"] = min(kw["lo"], z.hi)
        if "hi" in kw and "lo" not in kw:
            kw["hi"] = max(kw["hi"], z.lo)
        if "vlo" in kw and "vhi" not in kw:
            kw["vlo"] = min(kw["vlo"], z.vhi)
        if "vhi" in kw and "vlo" not in kw:
            kw["vhi"] = max(kw["vhi"], z.vlo)
        z = replace(z, **kw)
        if z.lo > z.hi:
            z = replace(z, lo=z.hi, hi=z.lo)
        if z.vlo > z.vhi:
            z = replace(z, vlo=z.vhi, vhi=z.vlo)
    zones[index] = z
    return replace(p, sampler_zones=sampler.format_zones(zones))


def zones_tool(p: Patch, action: str) -> tuple[Patch, str]:
    """(the patch, a note for the player — "" when all went as asked):
    `spread` each layer over the keys by its roots, `chops` (the chops
    as a zone map: each slice on its own key, `sampler.zones_from_slices`)
    or `clear` (the zones gone: the single sample again)."""
    if action == "clear":
        return replace(p, sampler_zones=""), ""
    if action == "spread":
        zones = zones_of(p)
        return (
            (with_zones(p, sampler.spread(zones)), "")
            if zones
            else (p, "No zones to spread.")
        )
    slices = sampler.parse_slices(p.sampler_slices)
    if not slices or not sampler.valid_id(p.sampler_sample):
        return p, "Chop the sample first: each slice becomes a zone."
    zones = sampler.zones_from_slices(p.sampler_sample, slices, int(p.sampler_root))
    note = "" if len(zones) == len(slices) else "Some slices ran past the top key."
    return replace(p, sampler_zones=sampler.format_zones(zones)), note


def zone_keys(z: sampler.Zone) -> str:
    """ "C3–B3, root A3" (and its take, past the first)."""
    keys = name_of(z.lo) if z.lo == z.hi else f"{name_of(z.lo)}–{name_of(z.hi)}"
    return f"{keys}, root {name_of(z.root)}" + (f", take {z.rr + 1}" if z.rr else "")


def zone_context(p: Patch) -> dict | None:
    """What the zone map draws: each zone as a box on a key (x) by
    velocity (y) plane — its takes inset — and a row of its own."""
    zones = zones_of(p)
    if not zones:
        return None
    rows = []
    for i, z in enumerate(zones):
        secs = sampler.seconds(z.sample)
        rows.append(
            {
                "i": i,
                "short": z.sample[:8],
                "missing": secs == 0.0,
                "seconds": round(secs, 2),
                "lo": z.lo,
                "hi": z.hi,
                "span": name_of(z.lo)
                if z.lo == z.hi
                else f"{name_of(z.lo)}–{name_of(z.hi)}",
                "root": z.root,
                "root_name": name_of(z.root),
                "vlo": z.vlo,
                "vhi": z.vhi,
                "rr": z.rr,
                "gain": z.gain,
                "tune": z.tune,
                "own": z.start is not None or z.own_loop,
                # the box: 0..100 % of the plane, keys 0..127 by velocity
                "x": round(z.lo / 128 * 100, 3),
                "w": round((z.hi - z.lo + 1) / 128 * 100, 3),
                "y": round(z.vlo / 128 * 100, 3),
                "h": round((z.vhi - z.vlo + 1) / 128 * 100, 3),
                "rx": round((z.root + 0.5) / 128 * 100, 3),
            }
        )
    return {
        "zones": rows,
        "takes": len({z.rr for z in zones}),
        "full": len(zones) >= sampler.MAX_ZONES,
        "keyboard": _keys_svg(),
        "octaves": [
            {"name": f"C{o - 1}", "x": round(o * 12 / 128 * 100, 3)} for o in range(11)
        ],
    }


def _keys_svg() -> str:
    """The keyboard under the zone map: 128 equal keys, the black ones
    dark (a key per column, so a zone's box sits exactly over its keys)."""
    black = {1, 3, 6, 8, 10}
    rects = "".join(
        f'<rect class="zk-b" x="{k}" y="0" width="1" height="1"/>'
        for k in range(128)
        if k % 12 in black
    )
    return (
        '<svg class="zone-keys" viewBox="0 0 128 1" preserveAspectRatio="none"'
        f' aria-hidden="true">{rects}</svg>'
    )


# --- drawing -----------------------------------------------------------------

_W, _H = 480, 64


def _x(frac: float) -> float:
    return round(min(max(frac, 0.0), 1.0) * _W, 1)


def wave_svg(p: Patch) -> str:
    """The sample's waveform with its markers: the played region (start to
    end; outside it dimmed), the loop band, each chop's start line."""
    pk = sampler.peaks(p.sampler_sample, 240)
    if pk is None:
        return ""
    mid = _H / 2
    xs = np.linspace(0, _W, len(pk))
    top = " ".join(
        f"{x:.1f},{mid - v * (mid - 2):.1f}" for x, v in zip(xs, pk, strict=True)
    )
    bot = " ".join(
        f"{x:.1f},{mid + v * (mid - 2):.1f}"
        for x, v in zip(xs[::-1], pk[::-1], strict=True)
    )
    parts = [
        f'<svg class="sampler-svg" viewBox="0 0 {_W} {_H}" preserveAspectRatio="none"'
        ' role="img" aria-label="sample waveform with its start, end, loop and '
        'slice markers">',
        f'<polygon class="sw-wave" points="{top} {bot}"/>',
    ]
    slices = sampler.parse_slices(p.sampler_slices)
    if slices:
        for s in slices:
            cls = "sw-slice sw-rev" if s.rev else "sw-slice"
            parts.append(
                f'<line class="{cls}" x1="{_x(s.a)}" x2="{_x(s.a)}" y1="0" y2="{_H}"/>'
            )
    else:
        a = float(np.asarray(p.sampler_start).ravel()[0])
        b = float(np.asarray(p.sampler_end).ravel()[0])
        parts.append(
            f'<rect class="sw-off" x="0" y="0" width="{_x(a)}" height="{_H}"/>'
        )
        parts.append(
            f'<rect class="sw-off" x="{_x(b)}" y="0" width="{_W - _x(b)}"'
            f' height="{_H}"/>'
        )
        if p.sampler_loop == "on":
            lo, hi = _x(p.sampler_loop_start), _x(p.sampler_loop_end)
            if hi > lo:
                parts.append(
                    f'<rect class="sw-loop" x="{lo}" y="0" width="{hi - lo:.1f}"'
                    f' height="{_H}"/>'
                )
        for cls, frac in (("sw-start", a), ("sw-end", b)):
            parts.append(
                f'<line class="{cls}" x1="{_x(frac)}" x2="{_x(frac)}"'
                f' y1="0" y2="{_H}"/>'
            )
    parts.append("</svg>")
    return "".join(parts)


def context(p: Patch) -> dict | None:
    """What the sampler block draws; None while no oscillator samples."""
    if not sampling(p):
        return None
    sid = p.sampler_sample
    secs = sampler.seconds(sid)
    root = int(p.sampler_root)
    parsed = sampler.parse_slices(p.sampler_slices)
    min_len = _min_len(p)
    slices = []
    for i, s in enumerate(parsed):
        lo, hi = sampler.start_bounds(parsed, i, min_len)
        slices.append(
            {
                "i": i,
                "key": name_of(root + i) if 0 <= root + i <= 127 else "—",
                "start": round(s.a * secs, 3),
                "end": round(s.b * secs, 3),
                "pitch": s.pitch,
                "rev": s.rev,
                # the draggable marker (layout.js): where it sits and may go,
                # 0..1 of the sample, and its keyboard steps
                "a": round(s.a, 5),
                "lo": round(lo, 5),
                "hi": round(hi, 5),
                "pct": round(s.a * 100, 3),
            }
        )
    return {
        "id": sid,
        "short": sid[:8],
        "missing": bool(sid) and secs == 0.0,
        "seconds": round(secs, 2),
        "svg": wave_svg(p) if sid else "",
        "slices": slices,
        "root": name_of(root),
        "pitch": pitch_context(p) if sid and secs else None,
        "step": round(NUDGE_SECONDS / max(secs, 1e-3), 6),
        "secs": round(secs, 4),
        "zones": zone_context(p),
    }


def pitch_context(p: Patch) -> dict:
    """The detector's reading for the panel: "detected A3 · 97%" and whether
    "use" would change the root, or "no clear pitch"."""
    midi, confidence = sampler.sample_pitch(p.sampler_sample)
    if confidence < PITCH_SHOWN or not math.isfinite(midi):
        return {"found": False}
    key = min(127, max(0, round(midi)))
    return {
        "found": True,
        "key": key,
        "name": name_of(key),
        "cents": round((midi - key) * 100),
        "percent": round(confidence * 100),
        "is_root": key == int(p.sampler_root),
    }


def watch(p: Patch) -> tuple:
    """Everything `context` reads, cheaply (the sample's length is read
    from the memory cache once loaded)."""
    if not sampling(p):
        return (None,)
    return (
        p.sampler_sample,
        sampler.seconds(p.sampler_sample),
        p.sampler_root,
        p.sampler_slices,
        repr(p.sampler_start),
        repr(p.sampler_end),
        p.sampler_loop,
        repr(p.sampler_loop_start),
        repr(p.sampler_loop_end),
        p.sampler_zones,
        tuple(sampler.seconds(z) for z in sampler.zone_samples(p.sampler_zones)),
    )


def silent(audio: np.ndarray) -> bool:
    return not np.any(np.abs(np.asarray(audio)) > 1e-4)
