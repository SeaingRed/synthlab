"""The session's data model and the pure helpers around it.

No `StudioState` here: the `Arrangement`, the reset scopes, what a layer
slot holds and how it renders (`render_layer`, `materialise`, `Lane`), the
validation entry points onto `schema`, and the clip-reference traversal.
Everything is re-exported by the package, so `from synthlab.studio.state
import Arrangement` is still the import.
"""

from __future__ import annotations

import copy
import json
import logging
import math
from dataclasses import dataclass, field, fields
from pathlib import Path

from ...sequencer import SCALES, midi, name_of, scale
from ...synth import Patch
from .. import schema, song_bridge
from ..views import BENCH_VIEWS
from ..views import RESET_VIEWS as _VIEW_RESETS

# the one logger of the package: "synthlab.studio.state", as it was when
# this was one module (a log filter or caplog names it)
log = logging.getLogger(__package__)

# state.json / a project export's schema version, written as "v". v1 (no
# key at all) and v2 share one shape; v3 replaced v2's `scenes` + scene
# running order (`song` as a list) with the song model (`song` as a dict).
# StudioState._migrate upgrades anything older on load and on import.
STATE_VERSION = 3

DRUM_ROWS: list[str] = ["kick", "snare", "hat", "ohat", "clap", "tom", "rim", "crash"]
# Every key: each semitone from A0 to E3. Sharps, as `name_of` spells.
BASS_ROOTS: list[str] = ["A0", "E1", "A1", "E2", "A2", "E3"]  # Bass Lab's keys
ROOTS: list[str] = [name_of(m) for m in range(midi("A0"), midi("E3") + 1)]
SCALE_CHOICES: list[str] = list(SCALES.keys())

# Panel presets live in a folder per panel name. Some panels were renamed
# and regrouped; presets saved under the old names stay
# listed and loadable from their new panel.
LEGACY_PANELS: dict[str, tuple[str, ...]] = {
    "Source": ("Oscillators",),
    "Vowel": ("Voice",),
    "Amp envelope": ("Envelope",),
    "Motion": ("Modulation", "Mod Matrix"),
}

# --- resettable scopes -------------------------------------------------------
# Every page offers "revert" (back to the snapshot taken when this thing was
# last loaded or saved) and "defaults" (factory). A scope is just a list of
# keys: "patch"/"curves"/"layer_fx" are specials, "arr.x" is an Arrangement
# field, "state.x" a StudioState attribute.
RESET_SCOPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "sound": ("Sound", ("patch", "curves")),
    "sequence": (
        "Sequence",
        (
            "arr.cells",
            "arr.velocities",
            "arr.slides",
            "arr.cuts",
            "arr.bars",
            "arr.steps_per_bar",
            "arr.swing",
            "arr.root",
            "arr.scale_name",
            "arr.arp_mode",
            "arr.arp_octaves",
            "arr.strum",
            "arr.note_nudge",
            "arr.note_chance",
            "arr.chords",
            # Perform's launcher (its rows of slots; absent when unset, as
            # the optional arrangement fields are: `scopes.OPTIONAL`)
            "state.perform_slots",
        ),
    ),
    "fx": ("Effects", ("layer_fx", "state.fx_master")),
    "kit": ("Kit", ("arr.drum_params", "arr.drum_mix", "arr.drum_samples")),
    "pattern": (
        "Pattern",
        (
            "arr.drums",
            "arr.drum_vel",
            "arr.drum_bars",
            "arr.drum_steps",
            "arr.drum_swing",
            "arr.drum_humanize",
            "arr.drum_hits",
            "arr.drum_row_swing",
        ),
    ),
    "fx_drums": ("Drum FX", ("state.fx_drums",)),
    "macros": ("Macros", ("state.macros",)),
    "board": (
        "Breed board",
        ("state.breed_tiles", "state.breed_starred", "state.breed_history"),
    ),
    "perform": ("Loops mix", ("state.perform_parts",)),
    "song": ("Song", ("state.song",)),
}
# which scopes each view can reset (the things that page actually owns) —
# derived from the one view registry, `views.VIEWS`
RESET_VIEWS = _VIEW_RESETS


def _lane(name: str) -> dict:
    """Field metadata tagging which slice of the arrangement a field
    belongs to — the one place that grouping is decided. Server-side form
    handlers (`_merge_arrangement`, `_drums_from_form`) derive their
    keep-sets from `lane_fields()` instead of repeating field-name tuples
    that silently go stale when a field is added."""
    return {"lane": name}


@dataclass
class Arrangement:
    # "shared": tempo/key — global, not owned by one lane, relevant to
    # both the synth grid and the drum machine.
    bpm: float = field(default=124.0, metadata=_lane("shared"))
    root: str = field(default="A1", metadata=_lane("shared"))
    scale_name: str = field(default="pentatonic_minor", metadata=_lane("shared"))
    # "grid": the synth step grid's own shape/feel.
    bars: int = field(default=1, metadata=_lane("grid"))
    steps_per_bar: int = field(default=4, metadata=_lane("grid"))
    swing: float = field(default=0.0, metadata=_lane("grid"))
    arp_mode: str = field(default="off", metadata=_lane("grid"))  # off|up|down|updown
    arp_octaves: int = field(default=1, metadata=_lane("grid"))
    # "cell": the note grid's own content.
    cells: list[list] = field(
        default_factory=lambda: [["A1", 0], ["A1", 2]], metadata=_lane("cell")
    )
    velocities: dict[str, float] = field(default_factory=dict, metadata=_lane("cell"))
    slides: list[int] = field(  # steps that glide in
        default_factory=list, metadata=_lane("cell")
    )
    # [pitch, step] cells that start a new note although the step before
    # is on too: two notes side by side instead of one held run (the grid's
    # Shift/Alt-click; `arrange._row_breaks`). Empty on every older lane
    cuts: list[list] = field(default_factory=list, metadata=_lane("cell"))
    # a step's chance (Perform's chance lane): step -> 0..1, the
    # odds that the notes starting on it play — rolled on a hash, never the
    # dice (`arrange.note_plays`, drums.md "Chance: a hash"). Only a value
    # under 1 is kept, and the field is ABSENT when empty wherever an
    # arrangement is written down (`schema.ARR_OPTIONAL`), as the drum
    # tables below: no saved state, loop key or stem key gains it
    note_chance: dict[str, float] = field(default_factory=dict, metadata=_lane("cell"))
    # "drum": the drum machine's own grid, clock + mixer (tempo is the
    # song's, not the drum lane's own).
    drums: list[list] = field(default_factory=list, metadata=_lane("drum"))
    drum_params: dict[str, dict] = field(default_factory=dict, metadata=_lane("drum"))
    drum_bars: int = field(default=1, metadata=_lane("drum"))
    drum_steps: int = field(default=16, metadata=_lane("drum"))
    drum_swing: float = field(default=0.0, metadata=_lane("drum"))
    drum_humanize: float = field(  # 0..1: timing + level jitter
        default=0.0, metadata=_lane("drum")
    )
    drum_vel: dict[str, float] = field(  # step -> 0..1
        default_factory=dict, metadata=_lane("drum")
    )
    # voice -> clip-store name: the row plays the clip instead of dr.hit
    # Groove machinery (vel/humanize/prob/choke) is unchanged.
    drum_samples: dict[str, str] = field(default_factory=dict, metadata=_lane("drum"))
    # voice -> {"level": 0..1.5, "mute": bool, "solo": bool, "prob": 0..1}
    drum_mix: dict[str, dict] = field(default_factory=dict, metadata=_lane("drum"))
    # "clip": the clip lane: [{"clip": name, "at_bar": int,
    # "gain": f}] — bounced phrases / kept takes placed on the Perform
    # timeline.
    clips: list[dict] = field(default_factory=list, metadata=_lane("clip"))
    # per-hit drum data beside the cells (`arrange.drum_hits`, drums.md "The
    # per-hit model"): "<voice>.<step>" -> {v, p, r, n, t} — velocity,
    # chance, ratchet, nudge (of a step), tune (semitones) — and a row's
    # own swing (voice -> 0..0.6) over the pattern's. Both are ABSENT when
    # unset wherever an arrangement is written down (`arr_dict`): the
    # session file, a loop key, a stem key, a baseline
    drum_hits: dict[str, dict] = field(default_factory=dict, metadata=_lane("drum"))
    drum_row_swing: dict[str, float] = field(
        default_factory=dict, metadata=_lane("drum")
    )
    # ms between the notes of a chord (notes that start on one step), low
    # to high; negative: high to low (`arrange._strummed`).
    # The note grid's own, per slot (`seq_of`), and ABSENT at 0 wherever an
    # arrangement is written down, as the two above
    strum: float = field(default=0.0, metadata=_lane("grid"))
    # the bar: one of `meter.METERS` — what a bar holds (3/4: three beats,
    # a 12-step sixteenth grid); both grids' step counts are its own
    # (`meter.step_choices`). `ramp`: [to_bpm, over_beats, at_beats], a
    # tempo moving across the render (`meter.Clock`) — set only by a song
    # section's (`song_model.section_arrangement`), never on the loop.
    # Both ABSENT when unset (4/4, no ramp) wherever an arrangement is
    # written down, as the three above: no saved state or key moves
    meter: str = field(default="4/4", metadata=_lane("shared"))
    ramp: list = field(default_factory=list, metadata=_lane("shared"))
    # the loop's chord track (`schema.CHORDS`, gen/chordtrack.py:
    # degrees of the loop's key, `at` / `len` in beats): what Perform's chord
    # lane draws and the loop's generators follow. It renders nothing — no
    # render key carries it (`RenderInputs.key`) — and is ABSENT when unset
    # wherever an arrangement is written down, as the ones above
    chords: dict | None = field(default=None, metadata=_lane("chords"))
    # timing inside a step (free record's shaped loops): step ->
    # how far the notes starting there are moved, a share of a step
    # (-0.5..0.5; `arrange._lane_timeline` — the drum hit's nudge, for
    # notes). Like a velocity it sits on a note's START step, shared by
    # every note starting there (the roll's decision 3). ABSENT when unset
    # wherever an arrangement is written down, as the ones above
    note_nudge: dict[str, float] = field(default_factory=dict, metadata=_lane("cell"))

    @classmethod
    def from_dict(cls, d: dict) -> Arrangement:
        """Tolerant load: unknown keys (a field renamed or removed since
        the file was written) are dropped with a warning, never fatal."""
        names = {f.name for f in fields(cls)}
        unknown = set(d) - names
        if unknown:
            log.warning("Arrangement: ignoring unknown fields %s", sorted(unknown))
        return cls(**{k: v for k, v in d.items() if k in names})


# a note lane as a pattern, a take or a loop holds it: its clock and its
# cell-lane fields (one list: the pattern tools, the song's patterns, a
# take written to the grid and a saved loop all copy these)
NOTE_LANE = (
    "bars",
    "steps_per_bar",
    "cells",
    "cuts",
    "velocities",
    "slides",
    # timing inside a step: {} here, absent where it is written down unset
    "note_nudge",
)
# Perform's launcher (studio loop-and-perform.md, "Slots and scenes"): its
# lanes are the layer slots, then the drums — a beat's lane, `DRUM_LANE`
SLOT_LANES = schema.SLOT_LANES
DRUM_LANE = SLOT_LANES - 1
# what a beat open on the drum lane holds of the arrangement (`loop_edit`)
DRUM_LOOP_FIELDS = (
    "drums",
    "drum_bars",
    "drum_steps",
    "drum_swing",
    "drum_hits",
    "drum_row_swing",
)
# what a saved loop keeps of an Arrangement (loops.py): the note lane, the
# tempo, the key and the meter — and what an open loop edit mirrors back
LOOP_FIELDS = NOTE_LANE + ("bpm", "root", "scale_name", "meter")


def lane_fields(lane: str) -> frozenset[str]:
    """Every Arrangement field tagged with this lane."""
    return frozenset(
        f.name for f in fields(Arrangement) if f.metadata.get("lane") == lane
    )


def drum_grid(arr: Arrangement) -> tuple[int, int]:
    """The drum machine's own clock (bars, steps_per_bar) — separate from
    the synth grid's. Every ``beatgen`` call must pass this, not
    ``arr.bars, arr.steps_per_bar``, or a pattern longer than the synth
    grid gets truncated to it."""
    return arr.drum_bars, arr.drum_steps


def drum_row_sounds(arr: Arrangement, voice: str) -> bool:
    """Does this drum row play at all: not muted, and — once any row is
    soloed — soloed itself (mute wins over solo). The one definition:
    the scheduler (`arrange.drum_hits`) and the sidechain's kick triggers
    (`fxrack.kick_steps`) both ask it, so a kick nobody hears ducks
    nothing."""
    mix = getattr(arr, "drum_mix", {}) or {}
    m = mix.get(voice) or {}
    if m.get("mute"):
        return False
    return bool(m.get("solo")) or not any((v or {}).get("solo") for v in mix.values())


GRID_SPANS = (1, 2, 3)  # six key tones, two octaves of the key, chromatic
GRID_OCTAVES = (-1, 3)  # how far the roll's window moves from the root


def window_root(root: str, octave: int = 0) -> str:
    """`root` moved `octave` whole octaves, kept where two octaves above
    it are still notes (MIDI 0..127): the bottom of the roll's window."""
    m = midi(root)
    k = int(octave)
    while k > 0 and m + 12 * k + 24 > 127:
        k -= 1
    while m + 12 * k < 0:
        k += 1
    return name_of(m + 12 * k)


def pitch_rows(arr: Arrangement, span: int = 1) -> list[str]:
    """The note grid's rows, highest first (top-to-bottom UI rows). span 1:
    the key's first 6 tones from the root (every grid before the range
    choice). span 2: two whole octaves of the key, root to root — a
    seven-tone scale is 15 rows, and the 7th degree a 6-row grid never
    showed is there. span 3: chromatic, the 24 semitones from the root
    (`MAX_GRID_PITCHES`)."""
    return grid_rows(arr, span)


def grid_rows(arr: Arrangement, span: int = 1, octave: int = 0) -> list[str]:
    """`pitch_rows` in the piano roll's octave window: `octave` moves the
    window's bottom whole octaves from the root (0 is every grid before
    there was a window)."""
    root = window_root(arr.root, octave) if octave else arr.root
    if span >= 3:
        low = midi(root)
        return [name_of(low + k) for k in reversed(range(24))]
    if span == 2:
        notes = scale(root, arr.scale_name, octaves=2)
    else:
        notes = scale(root, arr.scale_name, octaves=1)[:6]
    return [name_of(n) for n in reversed(notes)]


def notes_window(arr: Arrangement, midis: list[int], span: int, octave: int):
    """(span, octave, rows) of the roll's window that shows the most of
    `midis` where they are — the notes are never moved to fit: the window
    moves (the nearest octave first), and the range widens to two octaves
    of the key, or to the chromatic one when a note is out of the key
    (`StudioState.show_notes`; a keys take's, keytakes.py)."""
    names = [name_of(m) for m in midis]
    spans = [span] if span == 3 else [span, 2, 3] if span == 1 else [2, 3]
    if not {m % 12 for m in midis} <= {midi(n) % 12 for n in key_ladder(arr)}:
        spans = [3]
    lo, hi = GRID_OCTAVES
    octaves = sorted(range(lo, hi + 1), key=lambda o: (abs(o - octave), o))
    best = None
    for sp in spans:
        for o in octaves:
            rows = grid_rows(arr, sp, o)
            shown = set(rows)
            fit = sum(n in shown for n in names)
            if fit == len(names):
                return sp, o, rows
            if best is None or fit > best[0]:
                best = (fit, sp, o, rows)
    return best[1], best[2], best[3]


def key_ladder(arr: Arrangement) -> list[str]:
    """The key's tones low to high over the octaves a note can reach from
    the root (-2..+3): the ladder transposition walks (`patternops`)."""
    root = midi(arr.root)
    low = root - 24 if root - 24 >= 12 else root - 12 if root - 12 >= 12 else root
    notes = scale(name_of(low), arr.scale_name, octaves=6)
    return [name_of(n) for n in notes if n <= 108]


def _read_json(path: Path):
    """A JSON file from the data dir. Any parse failure — RecursionError
    from a deeply nested file included — is a ValueError, so every caller's
    "bad file" branch catches it; OSError (missing file) passes through."""
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as err:
        raise ValueError(f"{path.name}: not valid JSON ({type(err).__name__})") from err


def _meta_ok(meta) -> bool:
    """A preset's .meta.json as `preset_meta` writes it: v2, `tags` a list
    of strings, `spectrum` a list of finite numbers (not all zero unless
    verified). Anything else — hand-edited, truncated, another version — is
    recomputed, never trusted into a template or a sort key."""
    if not isinstance(meta, dict) or meta.get("v") != 2:
        return False
    tags, spectrum = meta.get("tags"), meta.get("spectrum")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return False
    if not isinstance(spectrum, list) or not all(
        isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
        for x in spectrum
    ):
        return False
    return bool(meta.get("verified")) or any(spectrum)


def _patch_to_json(p: Patch | None) -> dict | None:
    if p is None:
        return None
    return p.to_json()


def _patch_from_json(d: dict | None) -> Patch | None:
    """Trusted, in-session JSON (the undo ring) back to a Patch."""
    return Patch.from_dict(d) if isinstance(d, dict) else None


# --- validation: everything untrusted goes through studio/schema.py ---------
# These are thin, named entry points onto the one schema — not cleaners of
# their own. Anything loaded from state.json, an imported project, a stored
# baseline, a scene or a preset file is conformed by the same nodes.


def patch_from_json(d) -> Patch | None:
    """Untrusted JSON to a clamped Patch; None if it isn't a patch at all."""
    try:
        return Patch.from_dict(schema.conform_strict(d, schema.nodes().PATCH))
    except schema.Invalid:
        return None


def preset_from_json(raw) -> tuple[Patch, dict | None] | None:
    """A preset file's content, validated: (patch, format-2 extras or None).
    A format-2 preset carries its other lanes, main-lane FX and curves too
    (`layers`/`main_fx`/`curves`, already conformed). None when the file
    holds no patch at all."""
    if isinstance(raw, dict) and raw.get("format") == 2:
        # the patch strictly, as a v1 file's is: a v2 file whose patch is
        # missing or not a patch is refused, never loaded as the default
        patch = patch_from_json(raw.get("patch"))
        if patch is None:
            return None
        v2 = schema.conform(raw, schema.nodes().PRESET_V2)
        return patch, v2
    p = patch_from_json(raw)
    return (p, None) if p is not None else None


def _clean_arrangement(d) -> Arrangement:
    """An Arrangement from untrusted JSON (schema.ARRANGEMENT)."""
    return Arrangement(**schema.conform(d, schema.nodes().ARRANGEMENT))


# a v2 file's scene running order, as the migration reads it
_clean_song = song_bridge.clean_legacy_order


def unwrap_preset(raw: dict) -> dict:
    """The Patch dict inside a preset file — format 2 wraps it ({"format":
    2, "patch": ..., "layers": ..., ...}); v1 IS the Patch dict."""
    if isinstance(raw, dict) and raw.get("format") == 2:
        return raw.get("patch") or {}
    return raw


BANK_SLOTS = 8


def _clean_pattern(p) -> dict:
    """A drum pattern with only valid voices/steps and sane structure
    (schema.PATTERN: steps inside its own bars x steps_per_bar, sorted)."""
    return schema.conform(p, schema.nodes().PATTERN)


# A layer slot's own sequencing — what the arrangement holds for whichever
# slot is active, alongside its cells. `select_layer` swaps these with the
# cells, so a velocity, slide or arp edit on one slot never lands on another.
LAYER_SEQ_KEYS: tuple[str, ...] = (
    "velocities",
    "slides",
    "arp_mode",
    "arp_octaves",
    "cuts",
)
# ... and the part of it that is absent while unset (a slot's `seq` from
# before it existed reads the same): the strum, 0 when not there, and the
# steps' chance and the timing inside a step (free record),
# {} when not there
LAYER_SEQ_OPTIONAL: tuple[str, ...] = ("strum", "note_chance", "note_nudge")


def seq_of(arr) -> dict:
    """The slot's own sequencing as its `seq` holds it."""
    out = {k: copy.deepcopy(getattr(arr, k)) for k in LAYER_SEQ_KEYS}
    return out | {
        k: copy.deepcopy(getattr(arr, k)) for k in LAYER_SEQ_OPTIONAL if getattr(arr, k)
    }


def seq_changes(seq: dict) -> dict:
    """A slot's `seq` as `replace(arrangement, **...)` takes it."""
    out = {k: copy.deepcopy(seq[k]) for k in LAYER_SEQ_KEYS if k in seq}
    out["strum"] = float(seq.get("strum") or 0.0)
    out["note_chance"] = dict(seq.get("note_chance") or {})
    out["note_nudge"] = dict(seq.get("note_nudge") or {})
    return out


# -- a note lane (`NOTE_LANE`'s fields) on another grid: Perform's launcher ----
def loop_window(lane: dict, bars: int) -> dict:
    """`lane` over `bars` bars: its first bars, or — longer — itself
    repeated (a 1-bar loop on a 4-bar grid plays four times). What lies
    past its own end is not carried (a hidden note is not the loop's)."""
    from .. import song_model as sm  # lazy: as song_bridge's

    spb = int(lane["steps_per_bar"])
    period, total = max(1, int(lane["bars"])) * spb, max(1, int(bars)) * spb
    slides = [[0, int(x)] for x in lane.get("slides") or ()]
    return {
        "bars": max(1, int(bars)),
        "steps_per_bar": spb,
        "cells": sm._tile(lane.get("cells") or [], period, total),
        "cuts": sm._tile(lane.get("cuts") or [], period, total),
        "velocities": sm._tile_keys(lane.get("velocities") or {}, period, total),
        "slides": sorted({x for _z, x in sm._tile(slides, period, total)}),
        "note_nudge": sm._tile_keys(lane.get("note_nudge") or {}, period, total),
        # the chance lane (a lane state carries it, {} when none), as velocities
        **(
            {"note_chance": sm._tile_keys(lane["note_chance"] or {}, period, total)}
            if "note_chance" in lane
            else {}
        ),
    }


def regrid_lane(lane: dict, to: int) -> dict:
    """`lane` on a grid of `to` steps a bar (a whole multiple or fraction
    of its own: one meter's step choices are): a note keeps its length on
    a finer grid (`song_model._regrid`), its velocity, slide and cut stay
    on the step it starts."""
    from .. import song_bridge as sb  # lazy, as above
    from .. import song_model as sm

    bars, spb = max(1, int(lane["bars"])), int(lane["steps_per_bar"])
    if spb == to:
        return dict(lane)

    def starts(step) -> list[int]:
        return sb._regrid_steps([step], bars, spb, to)

    vel = lane.get("velocities") or {}
    return {
        **lane,
        "steps_per_bar": to,
        "cells": sm._regrid(lane.get("cells") or [], bars, spb, to),
        "cuts": [[c[0], s] for c in lane.get("cuts") or () for s in starts(c[1])],
        "velocities": {str(s): v for k, v in vel.items() for s in starts(k)},
        "slides": sorted({s for x in lane.get("slides") or () for s in starts(x)}),
        # a share of a step: as many of the new grid's steps (`to / spb`
        # times as fine), held inside one of them
        "note_nudge": {
            str(s): max(-0.5, min(0.5, float(v) * to / spb))
            for k, v in (lane.get("note_nudge") or {}).items()
            for s in starts(k)[:1]
        },
        # the working loop's chance lane, when a lane has one: as velocities
        **(
            {
                "note_chance": {
                    str(s): v for k, v in lane["note_chance"].items() for s in starts(k)
                }
            }
            if lane.get("note_chance")
            else {}
        ),
    }


# The reset scopes whose values belong to the active slot: each slot keeps
# its own baselines (and undo ring) while inactive (`select_layer`).
LAYER_SCOPES: tuple[str, ...] = ("sound", "fx", "sequence")
# A layer dict's keys that are loop *state* rather than render input: the
# renderer (and `RenderInputs.key()`) only ever sees `render_layer(lay)`.
_LAYER_STATE_KEYS = frozenset({"seq", "curves", "silent", "solo", "sound_fx"})


def _empty_layer() -> dict:
    """An unused slot. `seq`/`curves` None: never stashed (the slot takes
    over the live values when first selected); `silent`: muted only because
    the song's mix silenced the track it came from (solo elsewhere), not by
    its own mute — kept apart so a round trip doesn't make it a muted
    track (song_bridge.main_lane/layer_lane)."""
    return {
        "patch": None,
        "cells": [],
        "muted": False,
        "gain": 1.0,
        "fx": [],
        "silent": False,
        "seq": None,
        "curves": None,
    }


def is_follower(lay: dict) -> bool:
    """A layer that plays the main lane's notes (`arrange.follow_layer`)."""
    return isinstance(lay, dict) and isinstance(lay.get("follow"), dict)


def sounds_in_loop(lay: dict) -> bool:
    """Would this slot play in a loop render: a sound, and notes of its own
    or the main lane's to follow."""
    return bool(lay.get("patch") and (lay.get("cells") or is_follower(lay)))


@dataclass(frozen=True)
class Lane:
    """One slot that sounds (`StudioState.lanes`): `slot` its 0-based
    index; `octave` None when it plays its own notes, else the octave it
    plays the main lane's notes at (the phrase on a bench view, the key
    that was pressed from the keyboard); `edited` for the panels' slot."""

    slot: int
    octave: int | None
    edited: bool


def audible_layers(state, view: str, *, for_keys: bool = False) -> list[Lane]:
    """THE set of slots `view`'s ▶ hears — or, `for_keys`, what a pressed
    key sounds on (`fxlive.live_stack`, the WAV fallback's `/note`). One
    predicate for both, so the loop and the keys can't drift:
    `render_inputs` renders these lanes, the keys play these lanes.
    "This layer" (`layer_focus`) is a bench view's listening toggle: only
    the edited slot, muted or not. See `StudioState.lanes`."""
    bench = view in BENCH_VIEWS
    return state.lanes(
        bench=bench, focus=bench and bool(state.layer_focus), keys=for_keys
    )


def materialise(lay: dict, arr: Arrangement) -> dict:
    """A following layer as a plain slot with its own cells and sequencing:
    the main lane's notes at its octave, the main lane's velocities, slides
    and arp. It renders exactly as it did following (`render_layer` drops
    the velocities again where they equal the main lane's). Used where the
    main lane stops being the one it follows — a layer switch, and the song
    bridge, whose tracks and patterns have no notion of following."""
    if not is_follower(lay):
        return lay
    from ..arrange import follow_layer  # lazy: arrange imports this module

    plain = follow_layer(lay, arr)
    out = {k: v for k, v in lay.items() if k != "follow"}
    out["cells"] = plain["cells"]
    out["seq"] = {
        "velocities": copy.deepcopy(arr.velocities),
        "slides": list(plain.get("slides", [])),
        "cuts": [list(c) for c in plain.get("cuts", [])],
        "arp_mode": arr.arp_mode,
        "arp_octaves": arr.arp_octaves,
        **({"strum": arr.strum} if arr.strum else {}),
        **({"note_nudge": dict(arr.note_nudge)} if arr.note_nudge else {}),
        **({"note_chance": dict(arr.note_chance)} if arr.note_chance else {}),
    }
    return out


def render_layer(lay: dict, arr: Arrangement) -> dict:
    """A layer dict as the render path takes it, next to the live lane
    `arr`. The loop-state keys are dropped, except the slot's own
    sequencing (`seq`: velocities, slides, arp), which rides along as the
    render keys `arrange.build_song` reads (`song_bridge.lane_render`):
    only what the layer can be heard to play, and velocities only where
    they differ from the live lane's, so a layer that sounds as it did
    keeps its `RenderInputs.key()`. Without them an inactive layer played
    the *active* lane's velocities and no arp or slides, so merely
    switching layers changed the loop. A slot never stashed (`seq` None,
    an old save) plays as it always did: the live lane's velocities, no
    arp or slides. Curves stay behind on purpose: a layer ignores them, in
    a song as in the loop (song_bridge `_track_candidate`); they are the
    slot's, for when it is selected again."""
    out = {k: v for k, v in lay.items() if k not in _LAYER_STATE_KEYS}
    seq = lay.get("seq")
    if isinstance(seq, dict):
        seq = {**seq, "velocities": seq.get("velocities") or {}}
        total = max(1, int(arr.bars)) * int(arr.steps_per_bar)
        out |= song_bridge.lane_render(seq, lay.get("cells", []), arr.velocities, total)
    return out


# schema.PAYLOAD's top-level fields, minus the ones StudioState rebuilds
# into objects (Patch / Arrangement) rather than keeping as plain JSON —
# those five are handled by hand everywhere this set is used.
_OBJECT_KEYS = frozenset({"patch", "arrangement", "slot_a", "slot_b", "slot_prev"})


# --- clip references ------------------------------------------------------------
# Where a clip name is persisted (`StudioState.rename_clip_refs` says why):
# an arrangement's lane (`clips[].clip`) and drum one-shot slots
# (`drum_samples`), the song's takes and drums track slots, and any baseline
# holding those. One traversal, `fn` applied to each name: a rename, an
# import's remap, or a read (`map_clip_refs` on a project payload).


def _map_lane(clips, fn) -> list:
    if not isinstance(clips, list):
        return clips
    return [
        {**c, "clip": fn(c["clip"])}
        if isinstance(c, dict) and isinstance(c.get("clip"), str)
        else c
        for c in clips
    ]


def _map_slots(m, fn) -> dict:
    if not isinstance(m, dict):
        return m
    return {v: fn(c) if isinstance(c, str) else c for v, c in m.items()}


def _map_song(sg, fn) -> None:
    if not isinstance(sg, dict):
        return
    for t in sg.get("tracks") or []:
        if isinstance(t, dict) and isinstance(t.get("drum_samples"), dict):
            t["drum_samples"] = _map_slots(t["drum_samples"], fn)
    for sec in sg.get("sections") or []:
        if isinstance(sec, dict) and isinstance(sec.get("audio"), list):
            sec["audio"] = _map_lane(sec["audio"], fn)


def _map_baselines(baselines, fn) -> None:
    for base in (baselines or {}).values() if isinstance(baselines, dict) else ():
        data = base.get("data") if isinstance(base, dict) else None
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("arr.clips"), list):
            data["arr.clips"] = _map_lane(data["arr.clips"], fn)
        if isinstance(data.get("arr.drum_samples"), dict):
            data["arr.drum_samples"] = _map_slots(data["arr.drum_samples"], fn)
        _map_song(data.get("state.song"), fn)


def map_clip_refs(payload: dict, fn) -> None:
    """`remap_clip_refs` on a persisted payload (project.json's dict), in
    place: every clip name `c` in it becomes `fn(c)`."""
    arr = payload.get("arrangement")
    if isinstance(arr, dict):
        for key, how in (("clips", _map_lane), ("drum_samples", _map_slots)):
            if key in arr:
                arr[key] = how(arr[key], fn)
    _map_song(payload.get("song"), fn)
    _map_baselines(payload.get("baselines"), fn)


def clip_refs(payload: dict) -> set[str]:
    """Every clip name a persisted payload references."""
    found: set[str] = set()

    def note(c: str) -> str:
        found.add(c)
        return c

    map_clip_refs(copy.deepcopy(payload), note)
    return found


def _lane_names(clips) -> list[str]:
    if not isinstance(clips, list):
        return []
    return [
        c["clip"]
        for c in clips
        if isinstance(c, dict) and isinstance(c.get("clip"), str)
    ]


def _slot_names(m) -> list[str]:
    return [c for c in m.values() if isinstance(c, str)] if isinstance(m, dict) else []


def song_clip_names(sg) -> tuple[list[str], list[str]]:
    """(takes, drum one-shots): every clip name a song holds, read only —
    what `_map_song` rewrites, without building anything. A name is listed
    once per reference."""
    takes: list[str] = []
    shots: list[str] = []
    if not isinstance(sg, dict):
        return takes, shots
    for t in sg.get("tracks") or []:
        if isinstance(t, dict):
            shots += _slot_names(t.get("drum_samples"))
    for sec in sg.get("sections") or []:
        if isinstance(sec, dict):
            takes += _lane_names(sec.get("audio"))
    return takes, shots


def _plain_keys() -> tuple[str, ...]:
    """StudioState attributes that round-trip verbatim through
    schema.PAYLOAD — one call onto the schema instead of a field list
    hand-copied into `__init__`, `_apply` and `_payload` (and liable to
    drift out of step with it, and with each other, whenever a field is
    added). Lazy: schema.nodes() imports from this module."""
    return tuple(k for k in schema.nodes().PAYLOAD.fields if k not in _OBJECT_KEYS)
