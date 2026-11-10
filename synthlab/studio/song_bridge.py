"""The bridges between the working loop and the song model, and the v2 -> v3
state migration built on them. Pure functions over plain JSON-able dicts,
like `song_model`; `StudioState` wraps them.

The working loop is `StudioState`'s patch + arrangement + 3 layer slots.
Its *lanes* are what a legacy scene froze and what a section holds: the
main lane (the active slot: patch, notes, curves, its FX) first, then the
other unmuted layers that have a patch and notes, in slot order.

A section maps back onto those lanes by one rule, `section_loop`: its synth
tracks with a pattern in them, **in song track order** — the first is the
main lane, the rest are layers. So placing a loop into the song
(`place_lanes`) must put its main lane's track before its layers' tracks;
where no existing track fits in that order, it adds one (a same-sound copy
is named after its original, primed). That is what lets a migrated scene
reproduce the scene's own `RenderInputs.key()` exactly.

Song-wide values (tempo, key, swing, groove, the drum kit and its bus FX,
the master FX) are one per song. A legacy scene that differed from the
session in one of those loses that difference on migration — per-section
overrides are out of scope (spec, "Out of scope").
"""

from __future__ import annotations

import copy
import logging
import math

from . import limits, schema
from . import song_model as sm

log = logging.getLogger(__name__)

# the Arrangement fields each part of a section supplies (the rest are song-wide)
_NOTE_FIELDS = (
    "bars",
    "steps_per_bar",
    "cells",
    "velocities",
    "slides",
    "cuts",
    "arp_mode",
    "arp_octaves",
)
# ... and what a pattern holds only when set (`schema` drops it at 0)
# (timing inside a step and the chance lane too: a song pattern plays
# exactly what its loop plays)
_NOTE_OPTIONAL = ("strum", "note_nudge", "note_chance")
SONG_WIDE = ("bpm", "root", "scale_name", "swing", "drum_swing", "drum_humanize")
KIT = ("drum_params", "drum_mix", "drum_samples")
# a lane's own sequencing: velocities, slides, arp (state.LAYER_SEQ_KEYS)
_SEQ = ("velocities", "slides", "arp_mode", "arp_octaves", "cuts")


def _n():
    return schema.nodes()


# --- lanes --------------------------------------------------------------------


def main_lane(patch, arr: dict, layer: dict, curves) -> dict:
    """The working loop's (or a scene's) main lane, with its slot's gain.
    A song plays every track's gain, the main lane's included, so a
    section -> loop -> section round trip keeps its track and its gain.
    The loop's own render doesn't play a main gain (layer and main gain in
    loops are Task 5's). Migration passes 1.0 (`_legacy_main`): a v2 main
    lane never played its slot's gain, so a migrated scene still renders
    what it rendered."""
    return {
        "role": "main",
        "patch": patch,
        "cells": arr.get("cells", []),
        "fx": layer.get("fx") or [],
        "curves": curves or {},
        "gain": layer.get("gain", 1.0),
        "mute": own_mute(layer),
    }


def own_mute(layer: dict) -> bool:
    """A slot's own mute: `muted`, unless the song's mix put it there
    (`silent`: the track was silenced by a solo elsewhere when the section
    was opened). A lane carries this, so a round trip lands back on the
    track it came from instead of a permanently muted copy."""
    return bool(layer.get("muted")) and not layer.get("silent")


def _legacy_main(layer: dict) -> dict:
    """A v2 slot as a migrated main lane's layer: gain 1.0, which is what
    the v2 main lane played whatever its slot said."""
    return {**layer, "gain": 1.0}


def layer_lane(lay: dict) -> dict:
    return {
        "role": "layer",
        "patch": lay.get("patch"),
        "cells": lay.get("cells", []),
        "fx": lay.get("fx") or [],
        "curves": {},
        "gain": lay.get("gain", 1.0),
        "mute": own_mute(lay),
        # the slot's own velocities/slides/arp (None: never stashed)
        "seq": lay.get("seq"),
    }


def sounding_layers(layers: list[dict]) -> list[dict]:
    """The layers a loop render plays (`StudioState.other_layers`' rule):
    unmuted, with a patch and notes."""
    return [
        lay
        for lay in layers
        if not lay.get("muted") and lay.get("patch") and lay.get("cells")
    ]


def _track_candidate(lane: dict, curves_default: dict) -> dict:
    """The synth track a lane would become, conformed like a real one."""
    return schema.conform_strict(
        {
            "id": "candidate",
            "kind": "synth",
            "patch": lane["patch"],
            "fx": lane["fx"],
            "curves": lane["curves"] if lane["role"] == "main" else curves_default,
            "gain": lane["gain"],
            "mute": lane["mute"],
        },
        _n().TRACK,
    )


def _fits(track: dict, want: dict, role: str) -> bool:
    """Does `track` reproduce this lane? Every lane plays the track's gain;
    the main lane also plays its curves (a layer ignores them)."""
    same = all(track[k] == want[k] for k in ("patch", "fx", "mute", "gain"))
    if role == "main":
        return same and track["curves"] == want["curves"]
    return same


def _synth_name(song: dict) -> str:
    taken = {t["name"] for t in song["tracks"]}
    n = 1
    while f"Synth {n}" in taken:
        n += 1
    return f"Synth {n}"


class TrackCap(sm.SongError):
    """No synth track is left for lane `lane` (its sound `sound`) and no
    choice was made for it: the routes answer with the choice — put it on a
    track the section does not use yet (`free`: their ids), replacing that
    track's sound or keeping it (`onto`, `place_lanes`) — not a refusal.
    A `SongError`, so a caller that only toasts still says why."""

    def __init__(self, message: str, lane: int, sound: str, free: list[str]):
        super().__init__(message)
        self.lane, self.sound, self.free = lane, sound, free


ONTO_HOW = ("replace", "keep")


def _onto(song: dict, pick: list, want: dict, out: list[str]) -> dict:
    """The track a cap choice names (`pick`: [track id, "replace" | "keep"])
    for a lane: a synth track this section does not use yet; "replace"
    gives it the lane's sound (every section it plays in hears it), "keep"
    plays the lane's notes in the track's own sound."""
    tid, how = (list(pick) + ["", ""])[:2]
    i = sm.track_index(song, tid)
    track = song["tracks"][i] if i is not None else None
    if track is None or track["kind"] != "synth" or tid in out:
        raise sm.SongError("That track is gone, or the section uses it already.")
    if how not in ONTO_HOW:
        raise sm.SongError("Replace the track's sound, or keep it.")
    if how == "replace":
        for k in ("patch", "fx", "curves", "gain", "mute"):
            track[k] = copy.deepcopy(want[k])
    return track


def place_lanes(
    song: dict,
    lanes: list[dict],
    curves_default: dict,
    *,
    crowded: list | None = None,
    onto: list | None = None,
) -> list[str]:
    """A synth track id for each lane, in increasing song order (so the
    first lane is the section's main lane), reusing a track whose sound
    reproduces the lane and adding one where none fits.

    Past the synth-track cap no track can be added. That raises SongError
    ("Add loop to song" toasts it and the song stays as it was): the lane
    used to land, silently, on a track with another sound. A caller that
    must keep every section (the v2 migration) passes `crowded`, a list:
    the lane then falls back to a same-sound track out of order, then to
    any unused one, and its index is appended there, so the caller can say
    which lanes no longer play as they did.

    `onto` (the 8-track cap's choice): a list of
    [track id, "replace" | "keep"] taken in order by each lane that needs a
    track of its own when none can be added (`_onto`); once it runs out the
    next such lane raises `TrackCap`, which carries what the choice needs."""
    out: list[str] = []
    prev = -1
    choices = list(onto or [])
    for n, lane in enumerate(lanes):
        want = _track_candidate(lane, curves_default)
        synth = [
            (i, t)
            for i, t in enumerate(song["tracks"])
            if t["kind"] == "synth" and t["id"] not in out
        ]
        pick = next(
            (t for i, t in synth if i > prev and _fits(t, want, lane["role"])), None
        )
        if pick is None:
            try:
                twin = next((t for _i, t in synth if _fits(t, want, "main")), None)
                twin = twin or next(
                    (t for _i, t in synth if _fits(t, want, "layer")), None
                )
                name = sm._primed(twin["name"]) if twin else _synth_name(song)
                init = {k: want[k] for k in ("patch", "fx", "curves", "gain", "mute")}
                tid = sm.add_track(song, "synth", name, **init)
                pick = song["tracks"][sm.track_index(song, tid)]
            except sm.SongError as err:
                if crowded is None and choices:
                    pick = _onto(song, choices.pop(0), want, out)
                    out.append(pick["id"])
                    prev = max(prev, sm.track_index(song, pick["id"]))
                    continue
                if crowded is None:
                    # how many are left over, so the fix can be sized: this
                    # lane and every later one no free track reproduces
                    short = sum(
                        not any(
                            _fits(
                                t, _track_candidate(late, curves_default), late["role"]
                            )
                            for _i, t in synth
                        )
                        for late in lanes[n:]
                    )
                    raise TrackCap(
                        f"{err} {short} of this loop's {len(lanes)} sound(s) "
                        "would need a synth track of their own and none is "
                        "left: remove a track, or use sounds the song's tracks "
                        "already have.",
                        n,
                        (lane["patch"] or {}).get("name") or "its sound",
                        [t["id"] for _i, t in synth],
                    ) from None
                pick = next(
                    (t for _i, t in synth if _fits(t, want, lane["role"])), None
                )
                pick = pick or (synth[0][1] if synth else None)
                crowded.append(n)
                log.warning("song: no room for a track that fits; lane reuses one")
        if pick is None:  # no synth track at all and none can be added
            continue
        out.append(pick["id"])
        prev = max(prev, sm.track_index(song, pick["id"]))
    return out


# --- patterns -----------------------------------------------------------------


def pattern_index(song: dict) -> dict[str, str]:
    idx: dict[str, str] = {}
    for pid, p in song["patterns"].items():
        idx.setdefault(sm.content_hash(p), pid)
    return idx


def intern_pattern(song: dict, index: dict, kind: str, name: str, data: dict) -> str:
    """The pool id of a pattern with this content: an existing one when the
    content hash matches (identical patterns are shared), else a new one."""
    probe = schema.conform_strict(
        {**data, "id": "probe", "kind": kind, "name": name}, _n().SONG_PATTERN
    )
    h = sm.content_hash(probe)
    if h not in index:
        index[h] = sm.add_pattern(song, kind, name, data)
    return index[h]


def drum_data(arr: dict) -> dict:
    """An arrangement's drum lane as a drums pattern — its clock renamed:
    `drum_bars`/`drum_steps` are the pattern's `bars`/`steps`."""
    return {
        "bars": arr["drum_bars"],
        "steps": arr["drum_steps"],
        "drums": arr["drums"],
        "drum_vel": arr["drum_vel"],
        # per-hit data and the rows' own swing: a pattern's `hits` / `swing`
        # (absent when the loop has none, so its content hash holds)
        **({"hits": arr["drum_hits"]} if arr.get("drum_hits") else {}),
        **({"swing": arr["drum_row_swing"]} if arr.get("drum_row_swing") else {}),
    }


def apply_kit(track: dict, arr: dict) -> None:
    """The kit of `arr` (a conformed arrangement: `KIT`, the voices'
    shaping, the mixer, the rows' samples) onto the drums `track`, in
    place — copies, so the two never share a list."""
    for k in KIT:
        track[k] = copy.deepcopy(arr[k])


def beat_lane(beat: dict) -> dict:
    """A drums pattern as the Arrangement fields it plays through — the
    inverse of `drum_data`. `drum_hits` / `drum_row_swing` are always
    here (empty without any) so a pattern put on a loop REPLACES the
    loop's; `schema.ARRANGEMENT` and `arr_dict` drop the empty ones."""
    return {
        "drums": copy.deepcopy(beat["drums"]),
        "drum_vel": copy.deepcopy(beat.get("drum_vel") or {}),
        "drum_bars": beat["bars"],
        "drum_steps": beat["steps"],
        "drum_hits": copy.deepcopy(beat.get("hits") or {}),
        "drum_row_swing": dict(beat.get("swing") or {}),
    }


def bank_data(slot: dict) -> dict:
    """A drum-bank slot (schema.PATTERN) as a drums pattern: its hits are
    `steps`, its clock `bars`/`steps_per_bar`. The slot's own swing has no
    place in a pattern (groove is song-wide) and is dropped."""
    return {
        "bars": slot["bars"],
        "steps": slot["steps_per_bar"],
        "drums": slot["steps"],
    }


def note_data(arr: dict, cells) -> dict:
    return {
        **{k: arr[k] for k in _NOTE_FIELDS},
        **{k: arr[k] for k in _NOTE_OPTIONAL if arr.get(k)},
        "cells": cells,
    }


def layer_note_data(arr: dict, cells, seq: dict | None = None) -> dict:
    """A layer's notes: the loop's grid, and the slot's own velocities,
    slides and arp when it has them (`seq`, kept per slot by
    `StudioState.select_layer`) — never the main lane's, which belong to
    the main lane (arrange.build_song).

    A pattern with no velocities plays the main lane's (`lane_render`, the
    rule a migrated scene's key depends on), while a slot's empty lane is
    its own, all 0.9. So an empty lane the main lane's would override is
    written out as explicit 0.9s at the steps the layer plays."""
    out = {"bars": arr["bars"], "steps_per_bar": arr["steps_per_bar"], "cells": cells}
    if isinstance(seq, dict):
        out |= {k: copy.deepcopy(seq[k]) for k in _SEQ if k in seq}
        out |= {k: seq[k] for k in _NOTE_OPTIONAL if seq.get(k)}
        if not seq.get("velocities"):
            total = max(1, arr["bars"]) * arr["steps_per_bar"]
            own = lane_render(
                {**seq, "velocities": {}}, cells, arr["velocities"], total
            )
            if "velocities" in own:
                out["velocities"] = own["velocities"]
    return out


def lane_render(seq: dict, cells, main_vel: dict | None, total: int) -> dict:
    """A layer's own sequencing as the render keys `arrange.build_song`
    reads off a layer dict, holding only what the render can hear, so a
    layer that sounds as it did keeps its `RenderInputs.key()`:

    - `velocities`: the lane's values at the steps the layer reads (every
      step under an arp, else its note steps), and only when they differ
      from `main_vel` there — a layer dict without them plays the main
      lane's. `seq["velocities"]` None: the lane has none of its own (a
      pattern with an empty lane) and plays the main lane's. `main_vel`
      None: always carried (a tiled cycle, where the lanes loop at
      different lengths). A step it has no value for plays 0.9.
    - `slides`: the ones on its note steps (no others are heard).
    - `cuts`: the ones on its own cells (two notes side by side).
    - `arp_mode`/`arp_octaves`: only when the arp is on.
    - `strum`: only when set, and never under an arp (one note a step).
    - `note_chance`: on the steps it reads, only when any.
    """
    out: dict = {}
    arp_on = seq.get("arp_mode", "off") != "off"
    note_steps = sorted({int(c[1]) for c in cells})
    own = seq.get("velocities")
    if own is not None:
        steps = [str(x) for x in (range(total) if arp_on else note_steps)]
        mine = {k: own[k] for k in steps if k in own}
        if main_vel is None or [mine.get(k, 0.9) for k in steps] != [
            main_vel.get(k, 0.9) for k in steps
        ]:
            # never an empty dict: that would read as "no lane of its own"
            out["velocities"] = mine or dict.fromkeys(steps, 0.9)
    slides = sorted({int(x) for x in seq.get("slides") or []} & set(note_steps))
    if slides:
        out["slides"] = slides
    mine = {(c[0], int(c[1])) for c in cells}
    cuts = sorted(
        [c[0], int(c[1])] for c in seq.get("cuts") or [] if (c[0], int(c[1])) in mine
    )
    if cuts:
        out["cuts"] = cuts
    if arp_on:
        out["arp_mode"] = seq["arp_mode"]
        out["arp_octaves"] = int(seq.get("arp_octaves", 1))
    elif seq.get("strum"):
        out["strum"] = float(seq["strum"])
    if not arp_on and (nudge := seq.get("note_nudge")):
        # timing inside a step: only on its note steps (no other is heard)
        mine = {str(x) for x in note_steps}
        if heard := {k: float(v) for k, v in nudge.items() if k in mine and v}:
            out["note_nudge"] = heard
    # a step's chance: on the steps it reads (as its velocities), absent
    # when none (the working loop's own: no pattern carries one yet)
    chance = seq.get("note_chance") or {}
    heard = range(total) if arp_on else note_steps
    chance = {str(x): chance[str(x)] for x in heard if str(x) in chance}
    if chance:
        out["note_chance"] = chance
    return out


def pattern_seq(pat: dict, spb: int) -> dict:
    """A notes pattern's velocities/slides/arp moved onto an `spb` grid
    (one pattern length, as `song_model._regrid` moves its cells), in
    `lane_render`'s form: `velocities` None when the pattern has none of
    its own (it plays the main lane's)."""
    b, s = pat["bars"], pat["steps_per_bar"]
    moved: dict = {}
    for k, v in pat["velocities"].items():
        for x in _regrid_steps([k], b, s, spb):
            moved.setdefault(str(x), v)
    chance: dict = {}
    for k, v in (pat.get("note_chance") or {}).items():
        for x in _regrid_steps([k], b, s, spb):
            chance.setdefault(str(x), v)
    nudge = _regrid_nudge(pat, spb)
    return {
        "velocities": moved if pat["velocities"] else None,
        "slides": sorted(set(_regrid_steps(pat["slides"], b, s, spb))),
        "cuts": sm._regrid(pat.get("cuts") or [], b, s, spb),
        "arp_mode": pat["arp_mode"],
        "arp_octaves": pat["arp_octaves"],
        **({"strum": pat["strum"]} if pat.get("strum") else {}),
        # timing inside a step and a step's chance: absent
        # when the pattern has none, so no lane's key gains them
        **({"note_nudge": nudge} if nudge else {}),
        **({"note_chance": chance} if chance else {}),
    }


def _regrid_nudge(pat: dict, spb: int) -> dict:
    """A pattern's timing inside a step (`note_nudge`, a share of ITS step)
    on an `spb` grid: the same time, as a share of the step its note
    lands on (`_regrid_steps`)."""
    nudge = pat.get("note_nudge") or {}
    if not nudge:
        return {}
    b, s = max(1, pat["bars"]), pat["steps_per_bar"]
    if s == spb:
        return {str(k): float(v) for k, v in nudge.items()}
    out: dict = {}
    for k, v in nudge.items():
        x = int(k)
        if not 0 <= x < b * s:
            continue
        if s <= spb:  # a coarse step is k fine ones: the share grows
            out.setdefault(str(x * max(1, round(spb / s))), float(v) * spb / s)
        else:  # a fine step lands in the coarse one it starts in
            k_ = max(1, round(s / spb))
            out.setdefault(str(x // k_), ((x % k_) + float(v)) / k_)
    return {k: v for k, v in out.items() if v}


# --- tracks -------------------------------------------------------------------


def ensure_drums_track(song: dict, arr: dict, fx_drums) -> str:
    for t in song["tracks"]:
        if t["kind"] == "drums":
            return t["id"]
    return sm.add_track(
        song, "drums", "Drums", fx=fx_drums or [], **{k: arr[k] for k in KIT}
    )


def ensure_audio_track(song: dict) -> str | None:
    for t in song["tracks"]:
        if t["kind"] == "audio":
            return t["id"]
    try:
        return sm.add_track(song, "audio", "Audio 1")
    except sm.SongError:
        return None


# --- loop -> section ----------------------------------------------------------


def add_loop_section(
    song: dict,
    index: dict,
    name: str,
    arr: dict,
    lanes: list[dict],
    fx_drums,
    curves_default: dict,
    repeats: int = 1,
    *,
    crowded: list | None = None,
    beats: list[tuple[str, dict]] = (),
    letter: str = "",
    pooled: list | None = None,
) -> str:
    """Append a section holding one loop: its drum lane, its note lanes
    (placed on tracks by `place_lanes`) and its clip lane. `arr` is a
    conformed arrangement dict. Raises SongError on a song cap — a lane
    with no track to play its sound included, unless `crowded` is given
    (`place_lanes`).

    `beats`: the beat's other variations, (letter, arrangement dict) —
    a section holds one pattern a track, so they go into the pool beside
    the one it plays (`letter`, the grid's), named "<name> beat <letter>",
    for a cell to take later; `pooled` gets (letter, pattern id) of each
    that is not the section's own beat over again. A loop with no beat
    (no hits) puts none in the section or the pool: an empty "<name>
    beat" was a launcher on Perform that launched silence."""
    bars = math.lcm(max(1, arr["bars"]), max(1, arr["drum_bars"]))
    sid = sm.add_section(song, name, bars)
    sec = song["sections"][sm.section_index(song, sid)]
    sec["repeats"] = max(1, min(int(repeats), limits.MAX_SECTION_REPEATS))
    # a loop in another meter: its section keeps that bar (unless it is the
    # song's) and its patterns say what they were written in
    m = arr.get("meter") or "4/4"
    if m != sm.section_meter(song, None):
        sec["meter"] = m

    def metered(data: dict) -> dict:
        return {**data, "meter": m} if m != "4/4" else data

    drums_tid = ensure_drums_track(song, arr, fx_drums)
    own = f"{name} beat {letter}" if beats and letter else f"{name} beat"
    beat = None
    if arr["drums"]:
        beat = intern_pattern(song, index, "drums", own, metered(drum_data(arr)))
        sm.set_cell(song, sid, drums_tid, beat)
    for other, var_arr in beats:
        pid = intern_pattern(
            song, index, "drums", f"{name} beat {other}", metered(drum_data(var_arr))
        )
        if pid != beat and pooled is not None:
            pooled.append((other, pid))
    _note_cells(song, index, sid, name, arr, lanes, curves_default, metered, crowded)
    if arr["clips"]:
        audio = ensure_audio_track(song)
        if audio is not None:
            takes = [
                {
                    "track_id": audio,
                    "clip": c["clip"],
                    "at_bar": min(int(c["at_bar"]), limits.MAX_SECTION_BARS - 1),
                    "gain": c["gain"],
                }
                for c in arr["clips"]
            ]
            sec["audio"] = schema.conform(takes, _n().SECTION.fields["audio"])
    return sid


def _note_cells(
    song, index, sid, name, arr, lanes, curves_default, metered, crowded, onto=None
):
    """Each note lane on a synth track (`place_lanes`) as a pool pattern
    in section `sid`'s cell: the main lane's notes with the loop's
    sequencing, a layer's with its own (`layer_note_data`). A lane that
    carries its own arrangement (`arr`: a scene's slot, each loop on its own
    grid) is written from it whole."""
    placed = place_lanes(song, lanes, curves_default, crowded=crowded, onto=onto)
    for lane, tid in zip(lanes, placed, strict=False):
        track = song["tracks"][sm.track_index(song, tid)]
        if lane.get("arr") is not None:
            data = note_data(lane["arr"], lane["cells"])
        elif lane["role"] == "main":
            data = note_data(arr, lane["cells"])
        else:
            data = layer_note_data(arr, lane["cells"], lane.get("seq"))
        pid = intern_pattern(
            song, index, "notes", f"{name} {track['name']}", metered(data)
        )
        sm.set_cell(song, sid, tid, pid)


def _new_section(
    song: dict, name: str, bars: int, meter: str, at: int | None = None
) -> str:
    """A section of `bars` in `meter` (said only when it is not the
    song's): last, or at index `at` of the running order."""
    sid = sm.add_section(song, name, max(1, int(bars)), at)
    if meter != sm.section_meter(song, None):
        song["sections"][sm.section_index(song, sid)]["meter"] = meter
    return sid


def add_lanes_section(
    song: dict,
    index: dict,
    name: str,
    arr: dict,
    lanes: list[dict],
    curves: dict,
    *,
    onto: list | None = None,
    at: int | None = None,
) -> str:
    """Append a section of a saved loop's bars holding its note lanes alone
    (loops.py, "→ Song as a pattern"): each lane a pool pattern on a synth
    track that plays its sound (reused when one reproduces it), no beat and
    no drums track. `arr` is a conformed arrangement dict. `at`: the
    running order's index it goes in (a drop on the timeline), else last;
    `onto`: the track cap's choice (`place_lanes`). Raises SongError on a
    song cap (`TrackCap` when the choice is wanted)."""
    m = arr.get("meter") or "4/4"
    sid = _new_section(song, name, arr["bars"], m, at)

    def metered(data: dict) -> dict:
        return {**data, "meter": m} if m != "4/4" else data

    _note_cells(song, index, sid, name, arr, lanes, curves, metered, None, onto)
    return sid


def beat_data(beat: dict) -> dict:
    """A beat loop's PATTERN (the Drums bank's shape: `steps`, `bars`,
    `steps_per_bar`, per-hit `hits`, the rows' `row_swing`) as a drums
    pattern — `bank_data` and the per-hit data (absent when none, so its
    content hash is a bank slot's)."""
    return {
        **bank_data(beat),
        **({"hits": copy.deepcopy(beat["hits"])} if beat.get("hits") else {}),
        **({"swing": dict(beat["row_swing"])} if beat.get("row_swing") else {}),
    }


def add_scene_section(
    song: dict,
    index: dict,
    name: str,
    lanes: list[dict],
    beat: dict | None,
    *,
    meter: str,
    bpm: float,
    kit: dict,
    fx_drums,
    curves: dict,
    onto: list | None = None,
    at: int | None = None,
) -> str:
    """A section from a scene of Perform's launcher: each
    slot's loop of notes a pool pattern on a synth track that plays its
    sound (`lanes`: `main_lane`s, each with its own `arr` — a slot keeps its
    own grid), the beat (`beat`: a beat loop's PATTERN) on the drums track
    (made from `kit`, an arrangement dict, and `fx_drums` when the song has
    none). As long as its longest loop (the launcher plays a shorter one
    round within it), in the scene's `meter`, at its `bpm` when that is
    not the song's. Raises SongError on a cap (`TrackCap`: the choice)."""
    longest = [ln["arr"]["bars"] for ln in lanes] + ([beat["bars"]] if beat else [])
    sid = _new_section(song, name, max([1, *longest]), meter, at)
    sec = song["sections"][sm.section_index(song, sid)]
    if round(float(bpm), 3) != round(float(song["bpm"]), 3):
        sec["bpm"] = round(float(bpm), 3)

    def metered(data: dict) -> dict:
        return {**data, "meter": meter} if meter != "4/4" else data

    if beat is not None:
        drums = ensure_drums_track(song, kit, fx_drums)
        data = metered(beat_data(beat))
        pid = intern_pattern(song, index, "drums", f"{name} beat", data)
        sm.set_cell(song, sid, drums, pid)
    if lanes:
        first = lanes[0]["arr"]
        _note_cells(song, index, sid, name, first, lanes, curves, metered, None, onto)
    return sid


def add_audio_section(
    song: dict, name: str, bars: int, meter: str, clip: str, track: str = ""
) -> str:
    """Append a section of `bars` holding `clip` as a take from its first
    bar on an audio track (`track`, else the first, else a new one: a saved
    loop's audio, "→ Song as audio"). Raises SongError when no audio track
    can be added."""
    tid = track if any(t["id"] == track for t in song["tracks"]) else ""
    tid = tid or ensure_audio_track(song)
    if tid is None:
        raise sm.SongError("The song has no room for an audio track.")
    sid = _new_section(song, name, bars, meter)
    sec = song["sections"][sm.section_index(song, sid)]
    take = {"track_id": tid, "clip": clip, "at_bar": 0, "gain": 1.0}
    sec["audio"] = schema.conform([take], _n().SECTION.fields["audio"])
    return sid


# --- section -> loop ----------------------------------------------------------


def section_loop(song: dict, section: dict) -> dict:
    """A section as working-loop pieces: `arr` (a full Arrangement dict),
    the main lane (`patch`, `curves`, `main` = {fx, muted, gain}; all None
    when the song has no synth track), `layers` (every other lane with a
    pattern, stash format, muted ones included) and `fx_drums` (None with
    no drums track). Main lane = the first synth track, in song order,
    with a pattern in this section. A track that is muted, or not soloed
    while another is (`sm.audible`), plays as muted."""
    pool = song["patterns"]
    silent = {t["id"] for t in song["tracks"] if not sm.audible(song, t)}
    cells = section["cells"]
    arr: dict = {k: copy.deepcopy(song[k]) for k in SONG_WIDE}
    defaults = _n().ARRANGEMENT.default()
    synth = [t for t in song["tracks"] if t["kind"] == "synth"]
    lanes = [
        (t, pool[cells[t["id"]]])
        for t in synth
        if cells.get(t["id"]) is not None and cells[t["id"]] in pool
    ]
    if lanes:
        main_t, main_p = lanes[0]
        arr |= {k: copy.deepcopy(main_p[k]) for k in _NOTE_FIELDS}
        arr |= {k: main_p[k] for k in _NOTE_OPTIONAL if main_p.get(k)}
    else:
        main_t = synth[0] if synth else None
        arr |= {k: defaults[k] for k in _NOTE_FIELDS} | {"cells": []}
    drums_t = next((t for t in song["tracks"] if t["kind"] == "drums"), None)
    beat = pool.get(cells.get(drums_t["id"])) if drums_t else None
    if beat is not None:
        # a muted drums track keeps its pattern's clock (bars/steps), so the
        # section's cycle doesn't change; only the hits go
        arr |= beat_lane(beat)
        if drums_t["id"] in silent:
            arr |= {"drums": [], "drum_hits": {}}
        for k in schema.ARR_OPTIONAL:  # absent when unset, as everywhere
            if schema.arr_unset(k, arr.get(k)):
                arr.pop(k, None)
    else:
        arr |= {
            k: defaults[k] for k in ("drums", "drum_vel", "drum_bars", "drum_steps")
        }
    for k in KIT:
        arr[k] = copy.deepcopy(drums_t[k]) if drums_t else defaults[k]
    audio = {t["id"]: t for t in song["tracks"] if t["kind"] == "audio"}
    arr["clips"] = [
        {
            "clip": a["clip"],
            "at_bar": a["at_bar"],
            "gain": min(1.5, a["gain"] * audio[a["track_id"]]["gain"]),
        }
        for a in section["audio"]
        if a["track_id"] in audio and a["track_id"] not in silent
    ]
    layers = [
        {
            "patch": copy.deepcopy(t["patch"]),
            "cells": copy.deepcopy(p["cells"]),
            "muted": t["id"] in silent,
            "gain": t["gain"],
            "fx": copy.deepcopy(t["fx"]),
        }
        for t, p in lanes[1:]
    ]
    return {
        "arr": arr,
        "patch": copy.deepcopy(main_t["patch"]) if main_t else None,
        "curves": copy.deepcopy(main_t["curves"]) if main_t else None,
        "main": (
            {
                "fx": copy.deepcopy(main_t["fx"]),
                "muted": main_t["id"] in silent,
                # muted by the mix (a solo elsewhere), not by its own mute
                "silent": main_t["id"] in silent and not main_t["mute"],
                "gain": main_t["gain"],
            }
            if main_t
            else None
        ),
        "layers": layers,
        "fx_drums": copy.deepcopy(drums_t["fx"]) if drums_t else None,
        # the pattern ids behind the main lane and `layers`, in that order
        # (section rendering needs each lane's own grid; a layer dict has
        # only its cells)
        "lane_patterns": [p["id"] for _t, p in lanes],
        # ... and the tracks (`to_loop`: each lane's own mute, curves and
        # sequencing when the section is opened as the working loop)
        "lane_tracks": [t["id"] for t, _p in lanes],
    }


def _regrid_steps(steps, bars: int, spb: int, to: int) -> list[int]:
    """Note-start steps on an `spb` grid moved onto a `to`-step grid, as
    `song_model._regrid` moves a note's start (a velocity or slide belongs
    to where its note starts)."""
    own = [int(x) for x in steps if 0 <= int(x) < max(1, bars) * spb]
    if spb <= to:
        return [x * max(1, round(to / spb)) for x in own]
    return list(dict.fromkeys(x // max(1, round(spb / to)) for x in own))


def _lane_seq(pat: dict, spb: int, period: int, total: int, main_vel: dict) -> dict:
    """A notes pattern's own velocities/slides/arp, on the loop's `spb`
    grid, looping every `period` steps out to `total`. A pattern with no
    velocities plays the main lane's (`lane_render`); a slot's lane is
    always its own, so it gets a copy of the main lane's (`main_vel`, the
    loop's) — what it played in the song."""
    seq = pattern_seq(pat, spb)
    vel = seq["velocities"]
    slides = [[0, x] for x in seq["slides"]]
    return {
        "velocities": (
            sm._tile_keys(vel, period, total) if vel is not None else dict(main_vel)
        ),
        "slides": sorted({x for _z, x in sm._tile(slides, period, total)}),
        "cuts": sm._tile(seq["cuts"], period, total),
        "arp_mode": seq["arp_mode"],
        "arp_octaves": seq["arp_octaves"],
        **({"strum": seq["strum"]} if seq.get("strum") else {}),
        # a slot's own timing inside a step and chance lane (absent unset)
        **(
            {"note_nudge": sm._tile_keys(seq["note_nudge"], period, total)}
            if seq.get("note_nudge")
            else {}
        ),
        **(
            {"note_chance": sm._tile_keys(seq["note_chance"], period, total)}
            if seq.get("note_chance")
            else {}
        ),
    }


def to_loop(song: dict, p: dict) -> None:
    """`section_loop`'s pieces as working-loop state, in place (what
    `StudioState.section_to_loop` stores; never a render input):

    - every lane on the main lane's grid over the whole cycle: a loop has
      one step grid, and renders a layer's cells on it. When a layer's
      pattern has other bars or steps than the main lane's, the loop grows
      to the lcm of the lanes' bars (at most the grid's 4) with the main
      lane's notes, velocities, slides and curves tiled onto it, and each
      layer regridded (`song_model._regrid`) and tiled to its own length.
    - each layer's own `seq` (its pattern's velocities, slides, arp) and
      `curves` (its track's), for when that slot is selected.
    - `silent` on each layer: silenced by the mix (a solo elsewhere), not
      by its own mute — `own_mute` keeps a round trip on its own track.
    """
    pool, arr = song["patterns"], p["arr"]
    # the loop's grid stops at 4 bars: a longer pool pattern opens as its
    # first four (the rest stays, hidden, past the grid's end)
    for k in ("bars", "drum_bars"):
        arr[k] = min(int(arr[k]), max(limits.BAR_CHOICES))
    tracks = {t["id"]: t for t in song["tracks"]}
    pats = [pool[pid] for pid in p.get("lane_patterns", [])[1:]]
    tids = p.get("lane_tracks", [])[1:]
    bars, spb = max(1, arr["bars"]), arr["steps_per_bar"]
    total = bars * spb
    if any((pat["bars"], pat["steps_per_bar"]) != (bars, spb) for pat in pats):
        loop = math.lcm(bars, *(max(1, pat["bars"]) for pat in pats))
        if loop > max(limits.BAR_CHOICES):
            log.warning("to_loop: lanes realign every %d bars; the loop is cut", loop)
            loop = max(limits.BAR_CHOICES)
        total = loop * spb
        period = bars * spb
        arr["cells"] = sm._tile(arr["cells"], period, total)
        arr["cuts"] = sm._tile(arr.get("cuts") or [], period, total)
        arr["velocities"] = sm._tile_keys(arr["velocities"], period, total)
        arr["slides"] = [
            x for _z, x in sm._tile([[0, x] for x in arr["slides"]], period, total)
        ]
        for k in ("note_nudge", "note_chance"):  # absent when the lane has none
            if arr.get(k):
                arr[k] = sm._tile_keys(arr[k], period, total)
        arr["bars"] = loop
        if p.get("curves"):
            p["curves"] = {
                k: sm._tile_curve(v, bars, loop) for k, v in p["curves"].items()
            }
        for lay, pat in zip(p["layers"], pats, strict=True):
            cells = sm._regrid(lay["cells"], pat["bars"], pat["steps_per_bar"], spb)
            lay["cells"] = sm._tile(cells, max(1, pat["bars"]) * spb, total)
    for lay, pat, tid in zip(p["layers"], pats, tids, strict=True):
        t = tracks[tid]
        lay["seq"] = _lane_seq(
            pat, spb, max(1, pat["bars"]) * spb, total, arr["velocities"]
        )
        lay["curves"] = copy.deepcopy(t.get("curves") or {})
        lay["silent"] = bool(lay["muted"]) and not t["mute"]


def section_render_inputs(song: dict, section: dict, *, master_fx, clip_root):
    """What the working loop would render after `section_to_loop` — the
    RenderInputs of a section (all parts), for export and for checking a
    migrated scene against its legacy render."""
    from ..synth import Patch
    from .render import RenderInputs
    from .state import Arrangement

    p = section_loop(song, section)
    main = p["main"] or {"fx": [], "muted": False, "gain": 1.0}
    arr = schema.conform(p["arr"], _n().ARRANGEMENT)
    spb = arr["steps_per_bar"]
    total = max(1, arr["bars"]) * spb
    pats = [song["patterns"][pid] for pid in p["lane_patterns"][1:]]
    layers = [
        lay | lane_render(pattern_seq(pat, spb), lay["cells"], arr["velocities"], total)
        for lay, pat in zip(p["layers"], pats, strict=True)
        if sounding_layers([lay])
    ]
    return RenderInputs(
        patch=Patch.from_dict(p["patch"]) if p["patch"] else Patch(),
        arr=Arrangement(**arr),
        curves=p["curves"] or {},
        layers=layers,
        main_muted=bool(main["muted"]),
        main_fx=main["fx"],
        master_fx=master_fx,
        drums_fx=p["fx_drums"] or [],
        parts=("synth", "drums", "clips"),
        clip_root=clip_root,
        main_gain=float(main["gain"]),
    )


def track_alone(song: dict, track_id):
    """What Synths plays while it edits a synth track's sound: (the track's
    own pattern as an Arrangement on the song's tempo, key and groove, the
    section it was read from) — the first section, in song order, that
    plays the track, with nothing else in it (no beat, no take, no other
    lane, no automation). None when no section plays the track.

    The bench's reference phrase is on the working loop's key and tempo,
    which are not the song's: a bass made for 122 bpm in D was heard at
    75 bpm in A0, and nothing on Synths sounded like the track."""
    i = track_section(song, track_id)
    if i is None:
        return None
    sec = song["sections"][i]
    one = {k: v for k, v in sec.items() if k not in ("spans", "auto")}
    one |= {"cells": {track_id: sec["cells"][track_id]}, "audio": []}
    return sm.section_arrangement(song, one).arr, sec["name"]


def heard_section(song: dict, track_id, drawer: dict | None = None) -> int | None:
    """The section the sound dock's ▶ plays a track in: the block open in
    the Song page's drawer when it is this track's (`state.song_drawer`, a
    cell), else the first section where the drawer's pool pattern plays on
    this track, else the track's first section (`track_section`). It was
    always the first: a click on the lane's block in Breakdown B played
    the Intro's pattern."""
    d = drawer or {}
    if d.get("track_id") == track_id:
        i = sm.section_index(song, d.get("section_id"))
        if (
            i is not None
            and song["sections"][i]["cells"].get(track_id) in song["patterns"]
        ):
            return i
    if d.get("pattern_id"):
        for i, sec in enumerate(song["sections"]):
            if sec["cells"].get(track_id) == d["pattern_id"]:
                return i
    return track_section(song, track_id)


def pattern_lane(song: dict, pattern_id, want=None) -> str | None:
    """The track a pool pattern is auditioned through (the pattern
    editor's ▶ Play, `/song/pattern/{id}/play.wav`): `want` when it is a
    track of the pattern's kind (drums <- a drums pattern, synth <- notes),
    else the first track that plays the pattern, else the first track of
    its kind; None when the song has no such track."""
    p = song["patterns"].get(pattern_id)
    if p is None:
        return None
    kind = "drums" if p["kind"] == "drums" else "synth"
    fits = [t["id"] for t in song["tracks"] if t["kind"] == kind]
    if want in fits:
        return want
    for sec in song["sections"]:
        for tid, pid in sec["cells"].items():
            if pid == pattern_id and tid in fits:
                return tid
    return fits[0] if fits else None


def stand_in_lane(song: dict, kind: str, loop: dict) -> str:
    """Add a lane to `song` (a copy: an audition's) that plays a pool
    pattern when the song has no lane of its kind yet — the working loop's
    sound: its kit and drum-bus FX for a beat (`loop` holds `KIT` and
    `fx_drums`), its patch, layer FX and curves for notes. What the
    pattern editor's ▶ Play hears in a song with no drums track, say."""
    if kind == "drums":
        return ensure_drums_track(song, loop, loop.get("fx_drums"))
    return sm.add_track(
        song,
        "synth",
        "Synths",
        patch=loop["patch"],
        fx=loop.get("fx") or [],
        curves=loop.get("curves") or {},
    )


def track_section(song: dict, track_id) -> int | None:
    """The index of the first section, in song order, that plays the
    track (a cell holding a pattern the pool has), or None."""
    for i, sec in enumerate(song["sections"]):
        pid = sec["cells"].get(track_id)
        if pid is not None and pid in song["patterns"]:
            return i
    return None


# --- v2 -> v3 ----------------------------------------------------------------


def clean_legacy_order(raw, n_scenes: int) -> list[dict]:
    """A v2 running order (`song` as a list), each entry naming one of
    `n_scenes` scenes."""
    return [e for e in schema.conform(raw, _n().LEGACY_SONG) if e["scene"] < n_scenes]


def legacy_scene_lanes(sc: dict) -> list[dict]:
    """A (conformed) legacy scene's lanes: main first, then its layers."""
    main = main_lane(
        sc["patch"],
        sc["arr"],
        {"fx": sc["main_fx"], "muted": sc["main_muted"], "gain": 1.0},
        sc["curves"],
    )
    return [main, *(layer_lane(lay) for lay in sounding_layers(sc["layers"]))]


_GROOVE = ("swing", "drum_swing", "drum_humanize")


def scene_divergence(sc: dict, src: dict, fx_drums, master_fx) -> list[str]:
    """What of a (conformed) legacy scene the song can't keep: each value a
    song holds once — tempo, key, groove, kit (with its drum-bus FX),
    master FX — that differs from the one the song adopted."""
    a, out = sc["arr"], []
    if a["bpm"] != src["bpm"]:
        out.append(f"tempo {a['bpm']:g} -> {src['bpm']:g} bpm")
    if (a["root"], a["scale_name"]) != (src["root"], src["scale_name"]):
        out.append("key")
    if any(a[k] != src[k] for k in _GROOVE):
        out.append("groove")
    if any(a[k] != src[k] for k in KIT) or sc["fx_drums"] != fx_drums:
        out.append("kit")
    if sc["master_fx"] != master_fx:
        out.append("master FX")
    return out


def migration_note(
    diverged: list[tuple[str, list[str]]], crowded: list[str] | tuple = ()
) -> str:
    """The one-time message the Song page shows (Task 6): which scenes now
    play with the song's shared values, which (`crowded`) play a sound from
    another scene's track because the song ran out of synth tracks, and
    where the old save is."""
    if not diverged and not crowded:
        return ""
    out = []
    if diverged:
        shown = [f"{name} ({', '.join(what)})" for name, what in diverged[:5]]
        more = f" and {len(diverged) - 5} more" if len(diverged) > 5 else ""
        out.append(
            "A song has one tempo, key, groove, kit and master FX, so these "
            f"scenes now play with the song's: {'; '.join(shown)}{more}."
        )
    if crowded:
        more = f" and {len(crowded) - 5} more" if len(crowded) > 5 else ""
        out.append(
            f"A song has at most {limits.MAX_SYNTH_TRACKS} synth tracks, so "
            "these scenes now play a sound from another scene's track: "
            f"{'; '.join(crowded[:5])}{more}."
        )
    out.append("The pre-migration save is kept as state.json.v2.bak.")
    return " ".join(out)[: limits.MAX_MIGRATION_NOTE_CHARS]


def migrate_v2(data: dict) -> dict:
    """A v1/v2 state or project dict as v3: the legacy `scenes` + `song`
    running order become the song model, and neither is kept.
    Everything is conformed first, so no input makes this raise; `_apply`
    then conforms the result as a whole.

    - song-wide values (tempo, key, groove) and the drums track's kit and
      drum-bus FX come from the first scene in running order: the song is
      made of scenes, so they decide (as the first loop into an empty song
      does in `loop_to_section`). With no scenes they are the session's.
      The master FX stay the session's — one chain, run over the song.
      Every scene that differed in any of these is logged and named in the
      song's one-time `migration_note`.
    - tracks: that drums track; one synth track per current layer with a
      sound (the active slot first, then the others in slot order, named
      "Synth <slot + 1>"); more synth tracks where a scene's sound fits
      none of those (`place_lanes`) — past the synth-track cap a scene is
      kept, on a track that exists and with that track's sound, and is
      logged and named in the note too; one audio track if any clip lane
      anywhere holds a clip.
    - pool: every drum-bank slot (names kept), then each scene's drum lane,
      main grid and non-empty layers, deduplicated by content hash.
    - sections: one per running-order entry, in order, with its repeats (a
      scene listed twice is two sections); then any scene the running order
      left out, so no scene is lost. No scenes at all: the working loop
      becomes the one section "Loop 1".
    """
    n = _n()
    out = {k: v for k, v in data.items() if k != "scenes"}
    out["v"] = 3
    arr = schema.conform(data.get("arrangement"), n.ARRANGEMENT)
    scenes = schema.conform(data.get("scenes"), n.LEGACY_SCENES)
    order = clean_legacy_order(data.get("song"), len(scenes))
    patch = schema.conform(data.get("patch"), n.PATCH)
    curves = schema.conform(data.get("curves"), n.CURVES)
    layers = schema.conform(data.get("layers"), n.PAYLOAD.fields["layers"])
    active = schema.conform(data.get("active_layer"), n.PAYLOAD.fields["active_layer"])
    fx_drums = schema.conform(data.get("fx_drums"), n.FX)
    fx_master = schema.conform(data.get("fx_master"), n.FX)
    bank = schema.conform(data.get("drum_bank"), n.PAYLOAD.fields["drum_bank"])

    listed = [(e["scene"], e["repeats"]) for e in order]
    seen = {i for i, _r in listed}
    listed += [(i, 1) for i in range(len(scenes)) if i not in seen]
    first = scenes[listed[0][0]] if listed else None
    src = first["arr"] if first else arr
    src_fxd = first["fx_drums"] if first else fx_drums

    song = sm.new_song(src["bpm"], src["root"], src["scale_name"])
    song |= {k: src[k] for k in _GROOVE}
    index: dict[str, str] = {}
    ensure_drums_track(song, src, src_fxd)
    slots = [active, *(i for i in range(len(layers)) if i != active)]
    for i in slots:
        lay = layers[i]
        if i == active:
            lane = main_lane(patch, arr, _legacy_main(lay), curves)
        elif lay["patch"] is not None:
            lane = {**layer_lane(lay), "curves": curves}
        else:
            continue
        init = {k: lane[k] for k in ("patch", "fx", "curves", "gain", "mute")}
        sm.add_track(song, "synth", f"Synth {i + 1}", **init)
    if arr["clips"] or any(sc["arr"]["clips"] for sc in scenes):
        ensure_audio_track(song)
    for slot in bank:
        if slot is not None:
            intern_pattern(song, index, "drums", slot["name"], bank_data(slot))

    crowded: list[str] = []  # scenes with a lane that found no track of its own

    def add(name: str, arr_: dict, lanes: list[dict], cur, reps: int) -> None:
        full: list[int] = []
        try:
            add_loop_section(
                song, index, name, arr_, lanes, src_fxd, cur, reps, crowded=full
            )
        except sm.SongError as err:
            log.warning("migration: section %r not added: %s", name, err)
        if full and name not in crowded:
            crowded.append(name)
            log.warning(
                "migration: scene %r now plays a sound from another scene's "
                "track (the song is out of synth tracks)",
                name,
            )

    diverged: list[tuple[str, list[str]]] = []
    if not scenes:
        lanes = [
            main_lane(patch, arr, _legacy_main(layers[active]), curves),
            *(
                layer_lane(lay)
                for lay in sounding_layers(
                    [lay for i, lay in enumerate(layers) if i != active]
                )
            ),
        ]
        add("Loop 1", arr, lanes, curves, 1)
    else:
        named: set[int] = set()
        for i, reps in listed:
            sc = scenes[i]
            name = sc["name"] or f"scene {i + 1}"
            what = scene_divergence(sc, src, src_fxd, fx_master)
            if what and i not in named:
                named.add(i)
                diverged.append((name, what))
                log.warning(
                    "migration: scene %r now plays with the song's %s",
                    name,
                    ", ".join(what),
                )
            add(name, sc["arr"], legacy_scene_lanes(sc), sc["curves"], reps)
    song["migration_note"] = migration_note(diverged, crowded)
    out["song"] = song
    return out
