"""Drums: the kit panels, the pattern bank and step grid, per-row tools,
the beat library, and the one-shot hits the pads play."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, replace

import numpy as np
from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ... import drums, percussion
from ...kits import play as kit_play
from ...kits.fetch import KitError
from .. import (
    arrange,
    beatgen,
    beatpick,
    drumops,
    limits,
    loops,
    meter,
    notation,
    song_bridge,
)
from .. import patternops as po
from .. import song_model as sm
from ..arrange import _voice_sample, arrangement_from_form, clean_hits
from ..clips import clip_dir, clip_hash, save_clip
from ..drumfactory import DRUM_KITS
from ..drumlab import DRUM_PARAMS, DRUM_VOICES, defaults_of, preset_of
from ..gen import drums as gd
from ..render import RenderError, RenderResult, _to_wav_bytes, loop_refusal
from ..routing import (
    OutsideLock,
    StrForm,
    Svc,
    lock_free,
    pattern_edit,
    render_outside_lock,
    studio_router,
)
from ..schema import _mics as clean_mics
from ..service import StudioService
from ..state import (
    ALL_DRUM_ROWS,
    ARTICULATIONS,
    DRUM_ROWS,
    StudioState,
    drum_grid,
    kit_rows,
    lane_fields,
)
from ..state import DRUM_KITS as DRUM_SOURCES

router = studio_router()


@router.post("/fragment/drums")
def fragment_drums(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    for voice, specs in DRUM_PARAMS:
        changes: dict = {}
        for spec in specs:
            key = f"{voice}.{spec['name']}"
            if key not in form:
                continue
            try:
                v = float(form[key])
            except ValueError:
                continue
            v = min(spec["max"], max(spec["min"], v))
            changes[spec["name"]] = bool(v) if spec["kind"] == "bool" else v
        if changes:
            merged = dict(s.arrangement.drum_params.get(voice, {}))
            merged.update(changes)
            s.arrangement.drum_params[voice] = merged
        mixc: dict = {}
        for key, lo, hi in (("level", 0.0, 1.5), ("prob", 0.0, 1.0)):
            if f"{voice}.{key}" in form:
                try:
                    mixc[key] = min(hi, max(lo, float(form[f"{voice}.{key}"])))
                except ValueError:
                    pass
        if mixc:
            mix = dict(s.arrangement.drum_mix)
            mix[voice] = {**mix.get(voice, {}), **mixc}
            s.arrangement = replace(s.arrangement, drum_mix=mix)
    # a row head's level knob shows this too: steps.js moves it with the
    # strip's (the grid is never redrawn for a kit tweak)
    svc.suppress("drum-bank", "perform-drums", "drum-panels")
    return svc.do_render(request)


@router.post("/fragment/drums/preset")
def fragment_drums_preset(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    voice = form.get("voice", "")
    if voice not in DRUM_VOICES:
        return Response(status_code=404)
    params = preset_of(voice, form.get("preset", ""))
    if params is None:
        s.arrangement.drum_params.pop(voice, None)  # unknown/"Default" -> reset
    else:
        s.arrangement.drum_params[voice] = params
    s.save()
    try:
        headers = svc.preview_trigger(view="drums")
    except RenderError as err:
        return svc.toast(str(err))
    return HTMLResponse(svc.drum_panels_fragment(request), headers=headers)


def _vel_step(field: str) -> str | None:
    """A `dvel.<step>` form field's step as the velocity lane keys it, or
    None when it is no step of any grid (`limits.step_key`, the one rule
    for both velocity lanes: a plain number under `MAX_GRID_STEPS`, minor
    #3 and the 5000-digit key included)."""
    return limits.step_key(field[5:]) if field.startswith("dvel.") else None


def _drums_from_form(svc: StudioService, form: dict) -> None:
    """Drum cells (+ bars/steps) from the drum-only grid; other
    arrangement fields untouched. `arrangement_from_form` needs a full
    set of fields to parse the grid, so the current shared/grid-lane
    values (state.py's `lane_fields`) seed it — only `drums`,
    `drum_bars`, `drum_steps` and `drum_vel` are actually applied back
    below, so this is just avoiding arrangement_from_form's own
    hardcoded fallbacks, never a source of behaviour change."""
    s: StudioState = svc.state
    merged = {
        k: v
        for k, v in asdict(s.arrangement).items()
        if k in lane_fields("shared") | lane_fields("grid")
    }
    merged = {k: str(v) for k, v in merged.items()}
    merged["scale"] = s.arrangement.scale_name
    merged["bars"] = form.get("bars", str(s.arrangement.drum_bars))
    merged["steps"] = form.get("steps", str(s.arrangement.drum_steps))
    merged.update({k: v for k, v in form.items() if k.startswith("drum.")})
    parsed = arrangement_from_form(merged, meter=s.arrangement.meter)
    vel = dict(s.arrangement.drum_vel)
    for k, v in form.items():
        step = _vel_step(k)
        if step is not None:
            try:
                vel[step] = min(1.0, max(0.0, float(v)))
            except ValueError:
                pass
    arr = s.arrangement
    # the per-hit table (the grid's hidden `hits`, or `hit.*` fields); a hit
    # without a cell is dropped. The rows the grid does not draw (the other
    # kit's: `kit_rows`) keep their cells and hits
    drums, hits = drumops.keep_hidden(
        parsed.drums,
        drumops.hits_from_form(form, parsed.drums, arr.drum_hits),
        arr.drums,
        arr.drum_hits,
        drumops.drawn_rows(form, kit_rows(arr.drum_kit, arr.drums)),
        form,
    )
    s.arrangement = replace(
        arr,
        drums=drums,
        drum_bars=parsed.bars,
        drum_steps=parsed.steps_per_bar,
        drum_vel=vel,
        drum_hits=hits,
        # the rows' own swing
        drum_row_swing=drumops.row_swing_from_form(
            form, arr.drum_row_swing, arr.drum_swing
        ),
    )
    levels = drumops.levels_from_form(form)
    if levels:  # a row's level, from the knob in its head (the kit's mixer)
        mix = dict(s.arrangement.drum_mix)
        for voice, level in levels.items():
            mix[voice] = {**mix.get(voice, {}), "level": level}
        s.arrangement = replace(s.arrangement, drum_mix=mix)


@router.post("/fragment/drums/transport")
@pattern_edit
def fragment_drums_transport(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    changes: dict = {}
    try:
        if "bpm" in form and math.isfinite(float(form["bpm"])):
            # (min / max pass a NaN through as the lower bound)
            changes["bpm"] = min(
                limits.BPM_RANGE[1], max(limits.BPM_RANGE[0], float(form["bpm"]))
            )
        if "swing" in form:
            changes["drum_swing"] = min(0.6, max(0.0, float(form["swing"])))
        if "humanize" in form:
            changes["drum_humanize"] = min(1.0, max(0.0, float(form["humanize"])))
        if "bars" in form and int(form["bars"]) in limits.BAR_CHOICES:
            changes["drum_bars"] = int(form["bars"])
        if "steps" in form and int(form["steps"]) in meter.straight_choices(
            s.arrangement.meter
        ):
            changes["drum_steps"] = int(form["steps"])
    except ValueError:
        pass
    structural = {"drum_bars", "drum_steps"} & set(changes)
    arr = s.arrangement
    new_bars = changes.get("drum_bars", arr.drum_bars)
    if new_bars > arr.drum_bars and changes.get("drum_steps", arr.drum_steps) == (
        arr.drum_steps
    ):
        # more bars repeat the ones you have — empty ones would leave a
        # 1-bar beat silent for the second half of the loop.
        # A bar that still holds hits from before a shrink comes back as it
        # was, velocities included, like the note grid's (`patch._grow`)
        old, total = arr.drum_bars * arr.drum_steps, new_bars * arr.drum_steps
        changes["drums"] = beatgen.extend_bars(
            arr.drums, arr.drum_bars, new_bars, arr.drum_steps
        )
        offsets = beatgen.repeat_offsets(arr.drums, old, total)
        changes["drum_vel"] = _extend_vel(arr.drum_vel, old, total, offsets)
        changes["drum_hits"] = _extend_hits(arr.drum_hits, old, total, offsets)
    asked = replace(arr, **changes)
    refusal = loop_refusal(arr, asked)
    if refusal:
        # refused before anything is stored: the tempo used to be kept and
        # then fail to render, so the page showed it while the old loop
        # played on, and every later edit was a 422 until it was raised
        return svc.toast(refusal, still_playing=True)
    s.arrangement = asked
    if structural:
        # the grid changes shape: the bank and these chips come back out of
        # band (a bank answered without hx-swap-oob would be thrown away)
        s.save()
    else:
        # a bpm/swing/humanise edit: the transport shows what was typed or
        # dragged, and redrawing it would drop the gesture (the rows' swing
        # knobs rest on the kit's: steps.js moves them, the grid stays)
        svc.suppress("drum-transport", "drum-bank", "perform-drums")
    # the transport shows on Perform too: render the view it was posted
    # from (a bpm edit on Perform re-rendered the loop drums-only)
    return svc.do_render(request)


def _bank_response(svc: StudioService, request: Request, **kw) -> Response:
    """`svc.bank_response`, previewing the view the request came from.
    bank_response cues the Drums mix, which from Perform (the drum
    transport and grid show there too) swapped the loop for drums-only."""
    resp = svc.bank_response(request, **kw)
    view = svc.view_of(request)
    if view == "drums" or "HX-Trigger" not in resp.headers:
        return resp
    trig = json.loads(resp.headers["HX-Trigger"])
    if "preview-ready" not in trig:
        return resp
    try:
        ours = json.loads(svc.preview_trigger(view=view)["HX-Trigger"])
    except RenderError as err:
        trig = {"studio-toast": {"message": str(err), "kind": "error"}}
    else:
        extra = {k: v for k, v in trig["preview-ready"].items() if k != "url"}
        trig["preview-ready"] = {**ours["preview-ready"], **extra}
    resp.headers["HX-Trigger"] = json.dumps(trig)
    return resp


def _extend_vel(
    vel: dict, old_total: int, total: int, offsets: list[int] | None = None
) -> dict:
    """The drum velocity lane follows its hits into the repeated bars:
    every phrase-long block of the new range, or only `offsets` (the
    blocks `beatgen.repeat_offsets` says took a repeat — one that kept its
    own hidden hits keeps its own velocities).
    Defense in depth against the same non-ASCII-digit key `isdigit()`
    accepts but `int()` rejects (minor #3) — belt and suspenders alongside
    the ingestion-side guard, in case a hostile key ever reaches here by
    another path (a hand-edited state.json, an older save)."""
    if offsets is None:
        offsets = list(range(old_total, total, old_total))
    out = dict(vel)
    for k, v in vel.items():
        step = int(k) if k.isascii() and k.isdigit() else total
        if step < old_total:
            for dst in (step + off for off in offsets):
                if dst < total:
                    out[str(dst)] = v
    return out


def _extend_hits(hits: dict, old_total: int, total: int, offsets: list[int]) -> dict:
    """The per-hit table follows its hits into the repeated bars, as the
    velocity lane does (`_extend_vel`: the same blocks)."""
    out = dict(hits)
    for key, h in hits.items():
        voice, _dot, raw = key.partition(".")
        if raw.isascii() and raw.isdigit() and int(raw) < old_total:
            for off in offsets:
                if int(raw) + off < total:
                    out[f"{voice}.{int(raw) + off}"] = dict(h)
    return out


def _lane_answer(svc: StudioService, request: Request, lane: dict, **kw) -> Response:
    """Put a drums lane (`drumops`) on the working loop, unless the loop
    could not play it (a longer beat at this tempo): a toast, nothing
    changed."""
    s: StudioState = svc.state
    asked = replace(s.arrangement, **drumops.loop_fields(lane))
    refusal = loop_refusal(s.arrangement, asked)
    if refusal:
        return svc.toast(refusal, still_playing=True)
    s.arrangement = asked
    s.active_slot = None  # the beat is no longer the slot's
    return _bank_response(svc, request, **kw)


def _percussion_clip(data_dir, kind: str) -> str:
    """The clip holding percussion `kind`: `perc-<kind>`, rendered into the
    store the first time. A clip of that name that the store did not make
    (an upload or a rename: its meta says so) is never taken for it — the
    one-shot gets a suffix instead."""
    d, n = clip_dir(data_dir), 1
    name = f"perc-{kind}"
    while (d / f"{name}.wav").exists():
        try:
            meta = json.loads((d / f"{name}.meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        if meta.get("source") == "percussion" and meta.get("percussion") == kind:
            return name
        n += 1
        name = f"perc-{kind}-{n}"
    meta = {"source": "percussion", "percussion": kind}
    save_clip(data_dir, name, percussion.generate(kind), meta=meta)
    return name


@router.post("/fragment/drums/sample")
def fragment_drums_sample(request: Request, form: StrForm, svc: Svc) -> Response:
    """Point one drum voice at a clip from the store — or
    back at its synthesized hit with an empty clip name."""
    voice = form.get("voice", "")
    if voice not in ALL_DRUM_ROWS:
        return Response(status_code=404)
    s: StudioState = svc.state
    # each row's select is `clip.<voice>`: they all sit inside #drum-grid,
    # so htmx posts all eight, and a shared `clip` name meant the first
    # row's value (the kick's, usually "synth") won for every row
    clip = form.get(f"clip.{voice}", form.get("clip", "")).strip()
    if clip.startswith("perc:"):
        # the percussion the kit's voices lack: rendered once into the store
        kind = clip[5:]
        if kind not in percussion.KINDS:
            return svc.toast("No such percussion.")
        clip = _percussion_clip(s.data_dir, kind)
    samples = dict(s.arrangement.drum_samples)
    if clip:
        samples[voice] = clip
    else:
        samples.pop(voice, None)
    s.arrangement = replace(s.arrangement, drum_samples=samples)
    return _bank_response(svc, request)


@router.post("/fragment/drums/row/{voice}/{op}")
@pattern_edit
def fragment_drums_row(
    request: Request, voice: str, op: str, form: StrForm, svc: Svc
) -> Response:
    """Per-row tools: randomise / mutate / clear the row, fill every 1 / 2
    / 4 / 8 steps, shift it a step either way, mute, solo — and `euclid`
    (`hits` pulses over `of` steps, turned by `rotate`), `hitclear` (the
    row's per-hit data, its hits kept). A hit's data follows its cell
    through a shift and goes with a cell that goes."""
    s: StudioState = svc.state
    if voice not in ALL_DRUM_ROWS:
        return Response(status_code=404)
    arr = s.arrangement
    others = [c for c in arr.drums if c[0] != voice]
    mine = [c for c in arr.drums if c[0] == voice]
    hits = arr.drum_hits
    if op == "euclid":
        try:
            n, of = int(form.get("hits", "")), int(form.get("of", ""))
            rotate = int(form.get("rotate", "0") or 0)
        except ValueError:
            return svc.toast("Euclid takes whole numbers: hits, steps, rotate.")
        if not (0 <= n <= of and 1 <= of <= limits.MAX_GRID_STEPS):
            return svc.toast("Euclid: 0 to all of the steps, over 1 or more steps.")
        if abs(rotate) > limits.MAX_GRID_STEPS:
            return svc.toast("Euclid: the rotation is past the grid.")
        return _lane_answer(
            svc,
            request,
            drumops.euclid_row(drumops.loop_lane(arr), voice, n, of, rotate),
        )
    if op == "hitclear":
        kept = {k: v for k, v in hits.items() if not k.startswith(f"{voice}.")}
        s.arrangement = replace(arr, drum_hits=kept)
        return _bank_response(svc, request)
    if op == "randomise":
        seed = random.randrange(1_000_000)
        style = form.get("style") or s.beat_style
        style = style if style in beatgen.STYLES else beatgen.STYLES[0]
        mine = beatgen.generate_row(
            voice, style, seed, *drum_grid(arr), meter=arr.meter
        )
    elif op == "mutate":
        mutated = beatgen.mutate(
            arr.drums,
            random.randrange(1_000_000),
            *drum_grid(arr),
            rate=0.2,
            voice=voice,
        )
        mine = [c for c in mutated if c[0] == voice]
    elif op == "clear":
        mine = []
    elif op in ("fill1", "fill2", "fill4", "fill8"):
        # FL's "fill each N steps": the row set to a hit every N steps
        bars, steps = drum_grid(arr)
        mine = [
            c for c in po.fill(mine, voice, bars * steps, int(op[4:])) if c[0] == voice
        ]
    elif op in ("shift_left", "shift_right"):
        bars, steps = drum_grid(arr)
        total, by = bars * steps, -1 if op == "shift_left" else 1
        mine = po.shift(mine, total, by)
        row = {k: v for k, v in hits.items() if k.startswith(f"{voice}.")}
        rest = {k: v for k, v in hits.items() if not k.startswith(f"{voice}.")}
        hits = rest | drumops.remap_hits(row, total, po.shift_fn(total, by))
    elif op in ("mute", "solo"):
        mix = dict(arr.drum_mix)
        m = dict(mix.get(voice, {}))
        m[op] = not m.get(op, False)
        mix[voice] = m
        s.arrangement = replace(arr, drum_mix=mix)
        return _bank_response(svc, request)
    else:
        return Response(status_code=404)
    drums = sorted(others + mine)
    s.arrangement = replace(arr, drums=drums, drum_hits=clean_hits(hits, drums))
    return _bank_response(svc, request)


@router.post("/fragment/drums/grid")
@pattern_edit
def fragment_drums_grid(request: Request, form: StrForm, svc: Svc) -> Response:
    _drums_from_form(svc, form)
    # the grid posted it: never redraw under a stroke (Drums' bank, or
    # Perform's rack)
    svc.suppress("drum-bank", "perform-drums")
    return svc.do_render(request)


@router.post("/fragment/drums/bank/load/{slot}")
@pattern_edit
def fragment_bank_load(request: Request, slot: int, svc: Svc) -> Response:
    s: StudioState = svc.state
    if not s.bank_load(slot):
        return Response(status_code=404)
    # a new beat lands on the bar, never mid-phrase (like Perform's launcher)
    return _bank_response(svc, request, extra={"queue": True})


@router.post("/fragment/drums/bank/save/{slot}")
def fragment_bank_save(
    request: Request, slot: int, form: StrForm, svc: Svc
) -> Response:
    s: StudioState = svc.state
    # the form's slot select wins; a blank one means "wherever it's safe":
    # the first empty slot. Overwriting a named slot is confirmed
    # client-side (the form's hx-confirm follows the selected option).
    raw = form.get("slot", str(slot)).strip()
    try:
        slot = int(raw) if raw else s.default_save_slot()
    except ValueError:
        pass
    if not s.bank_save(slot, form.get("name", "").strip()):
        return Response(status_code=404)
    _keep_beat(s, s.drum_bank[slot])
    return _bank_response(svc, request)


def _keep_beat(s: StudioState, pattern: dict) -> None:
    """Drums' Save lands in the Loops too: the saved beat as a
    beat loop of its name — written over when one is there, the same beat
    saved again (`loops.save_beat`). A full loop store keeps the bank's or
    the library's save, quietly."""
    try:
        loops.save_beat(s.data_dir, pattern["name"], pattern, s.arrangement)
    except OSError:
        pass


@router.post("/fragment/drums/bank/factory")
def fragment_bank_factory(request: Request, svc: Svc) -> Response:
    """Put the factory patterns back in the bank — offered from
    the Drums reset menu, behind a confirm. The grid is untouched."""
    svc.state.bank_restore_factory()
    svc.state.save()
    return svc.full_view_page(request)


@router.post("/fragment/drums/kit")
@pattern_edit
def fragment_drums_kit(request: Request, form: StrForm, svc: Svc) -> Response:
    """A genre kit (`drumfactory.DRUM_KITS`): its pattern (swing included)
    and velocity lane on the grid, its voices' Drum Lab shaping, and its
    style/seed for Randomise. The kit panels move too, so the whole view
    comes back (with this view's preview).

    `keep=pattern` swaps the kit under the beat: only the voices' shaping
    (and the Randomise style) load — the pattern, its swing and velocity
    lane, the per-hit data, the rows' own swing and the rows' sample
    assignments all stay, row for row (`drumops.swap_kit`: every kit is the
    same eight voices, so a row's part stays on its row). Nothing in the
    grid moves, so it is no pattern undo step; "revert kit" takes it back."""
    s: StudioState = svc.state
    kit = DRUM_KITS.get(form.get("kit", ""))
    if kit is None:
        return svc.toast("No such kit.")
    if form.get("keep") == "pattern":
        # (Synths' Beat ▾ kit makes the same swap: beatpick.swap_kit_under)
        beatpick.swap_kit_under(s, form["kit"])
        s.save()
        return svc.full_page_with_preview(request)
    s.arrangement = replace(s.arrangement, drum_vel=dict(kit["vel"]))
    s.apply_pattern(kit["pattern"])
    params = dict(s.arrangement.drum_params)
    params.update({v: dict(p) for v, p in kit["params"].items()})
    s.arrangement = replace(s.arrangement, drum_params=params)
    s.mark_baseline("kit", kit["pattern"]["name"])
    s.beat_style, s.beat_seed = kit["style"], kit["seed"]
    s.active_slot = None  # its beat is not any slot's
    s.save()
    return svc.full_page_with_preview(request)


@router.post("/fragment/drums/randomise")
@pattern_edit
def fragment_drums_randomise(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    style = form.get("style", beatgen.STYLES[0])
    if style not in beatgen.STYLES:
        return Response(status_code=404)
    try:
        seed = int(form.get("seed", "") or random.randrange(1_000_000))
    except ValueError:
        seed = random.randrange(1_000_000)
    # the style's cells, then its feel (`beatgen.dress`): ghosts with a
    # velocity of their own, a chance a hit, the rows' own swing — or none
    # of them, for a style that has no feel: the old per-hit data goes
    # with the old beat either way (Synths' Beat ▾ "new:" rolls the same)
    beatpick.roll_style(s, style, seed)
    return _bank_response(svc, request)


@router.post("/fragment/drums/mutate")
@pattern_edit
def fragment_drums_mutate(request: Request, svc: Svc) -> Response:
    s: StudioState = svc.state
    arr = s.arrangement
    seed = random.randrange(1_000_000)
    drums = beatgen.mutate(arr.drums, seed, *drum_grid(arr))
    s.arrangement = replace(
        arr, drums=drums, drum_hits=clean_hits(arr.drum_hits, drums)
    )
    s.beat_seed = seed
    return _bank_response(svc, request)


# --- the beat's variations, and its generators -------------------------------------

GEN_KINDS = ("fill", "build", "busier", "sparser", "variation")
FILL_LENGTHS = {"0.5": 0.5, "1": 1.0, "2": 2.0}


@router.post("/fragment/drums/var")
@pattern_edit
def fragment_drums_var(request: Request, form: StrForm, svc: Svc) -> Response:
    """The beat's variations A-D (`DrumBank.beat_variation`): `var` puts
    that one on the grid — the one showing is set aside under its own
    letter, and a letter that holds nothing yet starts as a copy of it.
    `copy` (a letter) overwrites `var` with that variation first: "copy A
    to B". One pattern undo step (Ctrl+Z puts the grid and its letter
    back); the new beat lands on the bar."""
    s: StudioState = svc.state
    letter = form.get("var", "")
    copy_from = form.get("copy") or None
    was, prev = s.arrangement, s.beat_var
    if not s.beat_variation(letter, copy_from):
        return svc.toast("No such variation.")
    refusal = loop_refusal(was, s.arrangement)
    if refusal:  # a longer variation than this tempo can loop
        s.beat_variation(prev)
        return svc.toast(refusal, still_playing=True)
    s.active_slot = None
    return _bank_response(svc, request, extra={"queue": True})


@router.post("/fragment/gen")
def fragment_gen(request: Request, form: StrForm, svc: Svc) -> Response:
    """A generator on the working loop's beat (`gen/drums.py`; the Song
    page's is `POST /song/gen`): `kind` =
    - `fill`: the last `len` bars (0.5, 1 or 2) become a seeded fill from
      the style's own table; `every` (1, 2 or 4) puts one at the end of
      every N-th bar instead;
    - `build`: the beat becomes a roll over the grid's bars (two at least)
      — quarters, eighths, sixteenths, 32nds by ratchet, the snare's
      velocity and tune climbing;
    - `busier` / `sparser` (`amount`): ghosts and offbeats in, or the
      weakest hits out — never the kick on a beat or the backbeat;
    - `variation`: a few light hits traded, the groove kept.
    Seeded (`seed`, else a fresh one), pure, and it plays (the loop starts
    if it was stopped). **One pattern undo step however often a fill or a
    build is rolled again**: a roll on the grid the same generator left
    joins that roll's step. Busier and sparser are a step each.

    `lane=notes`: the same for the note grid (`routes/pattern.gen_notes`,
    `gen/loop.py`) — a bass, chords, a pad, an arp or a lead written into
    the octaves the roll shows, `again`, `busier`, `sparser`."""
    if form.get("lane") == "notes":
        from .pattern import gen_notes  # lazy: pattern imports this module

        return gen_notes(request, form, svc)
    s: StudioState = svc.state
    kind = form.get("kind", "")
    if kind not in GEN_KINDS:
        return svc.toast("No such generator.")
    try:
        seed = int(form.get("seed", "") or random.randrange(limits.SEED_MODULO))
        amount = float(form.get("amount", "") or 0.25)
        every = int(form.get("every", "") or 0)
    except ValueError:
        return svc.toast("The seed, the amount and “every” are numbers.")
    if not math.isfinite(amount) or every not in (0, 1, 2, 4):
        return svc.toast("“Every” is 1, 2 or 4 bars.")
    seed %= limits.SEED_MODULO
    amount = min(1.0, max(0.05, amount))
    arr = s.arrangement
    # a one-bar beat keeps its first half: the fill is its last half bar
    length = FILL_LENGTHS.get(form.get("len") or ("0.5" if arr.drum_bars == 1 else "1"))
    if length is None:
        return svc.toast("A fill is half a bar, one bar or two.")
    was = drumops.loop_lane(arr)
    lane = gd.lane_of(was)
    style = s.beat_style
    if kind == "fill":
        if not lane["drums"]:
            return svc.toast("There is no beat to fill yet.")
        if every:
            lane = gd.fill_every(lane, seed, every, length, style)
        else:
            lane = gd.fill(lane, seed, length, None, style=style)
    elif kind == "build":
        bars = max(2, arr.drum_bars)
        lane = gd.build(bars, seed, arr.drum_steps)
    elif kind == "variation":
        lane = gd.variation(lane, seed, amount, style)
    else:
        if not lane["drums"]:
            return svc.toast(f"There is no beat to make {kind} yet.")
        lane = (gd.busier if kind == "busier" else gd.sparser)(lane, amount, seed)
    if kind != "build":  # what the grid hides past its end stays
        lane = drumops.with_hidden(lane, was)
    key = f"gen:{kind}" if kind in ("fill", "build") else ""
    before = s.pattern_snapshot()
    again = bool(key) and s.gen_again(key)
    asked = replace(arr, **drumops.loop_fields(lane))
    refusal = loop_refusal(arr, asked)
    if refusal:
        return svc.toast(refusal, still_playing=True)
    if drumops.loop_fields(lane) == drumops.loop_fields(drumops.loop_lane(arr)):
        return svc.toast(f"Nothing to make {kind}." if kind != "fill" else "No change.")
    s.arrangement = asked
    if not again:
        s.push_pattern_undo(before)
    if key:
        s.gen_done(key)
    s.active_slot = None
    resp = _bank_response(svc, request, extra={"autoplay": True})
    # what was made says so (the note rides the answer's HX-Trigger)
    trig = json.loads(resp.headers.get("HX-Trigger") or "{}")
    trig["studio-toast"] = {"message": f"{kind} · seed {seed} — Ctrl+Z takes it back"}
    resp.headers["HX-Trigger"] = json.dumps(trig)
    return resp


@router.post("/fragment/drums/notation")
@pattern_edit
def fragment_drums_notation(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    arr = s.arrangement
    text = form.get("notation", "")
    # A pattern finer than the grid bumps the grid (4 -> 8 -> 16 -> 32)
    # rather than failing — same thing the editor's steps select does.
    err: notation.NotationError | None = None
    for spb in (g for g in meter.straight_choices(arr.meter) if g >= arr.drum_steps):
        try:
            steps = notation.parse(text, spb, arr.drum_bars)
        except notation.NotationError as e:
            if err is None or "steps per bar" not in str(e):
                err = e
            if "steps per bar" not in str(e):
                break
            continue
        # notation spells today's eight rows (`DRUM_ROWS`): an acoustic
        # beat's further rows keep their cells and hits (`keep_hidden`, as a
        # grid post), moved onto a finer grid with the rows it spells
        was_drums, was_hits = _regrid_hidden(arr, spb)
        drums_, hits = drumops.keep_hidden(
            steps, was_hits, was_drums, was_hits, DRUM_ROWS, {}
        )
        s.arrangement = replace(arr, drums=drums_, drum_hits=hits, drum_steps=spb)
        return _bank_response(svc, request)
    return svc.toast(str(err))


def _regrid_hidden(arr, spb: int) -> tuple[list, dict]:
    """The beat's cells and hits of the rows notation does not spell, on a
    grid of `spb` steps a bar: a step lands where it sounded when `spb` is
    a whole multiple of the grid it was on (a nudge, a share of a step,
    scaled with it), else it stays where it was (as the steps select
    leaves cells)."""
    k, rem = divmod(spb, max(1, arr.drum_steps))
    if spb == arr.drum_steps or rem or not k:
        return arr.drums, dict(arr.drum_hits or {})
    drums_ = [
        [v, int(st) * k] if v not in DRUM_ROWS else [v, st] for v, st in arr.drums
    ]
    hits = {}
    for key, h in (arr.drum_hits or {}).items():
        v, _dot, raw = key.partition(".")
        if v in DRUM_ROWS:
            hits[key] = h
            continue
        h = dict(h)
        if "n" in h:
            h["n"] = max(-0.5, min(0.5, float(h["n"]) * k))
        hits[f"{v}.{int(raw) * k}"] = h
    return drums_, hits


@router.post("/fragment/drums/clear")
@pattern_edit
def fragment_drums_clear(request: Request, svc: Svc) -> Response:
    s: StudioState = svc.state
    s.arrangement = replace(s.arrangement, drums=[])
    return _bank_response(svc, request)


@router.post("/fragment/drums/lib/save")
def fragment_drums_lib_save(request: Request, form: StrForm, svc: Svc) -> Response:
    name = form.get("name", "").strip()
    if not name:
        return Response(status_code=422)
    path = svc.state.save_drum_pattern(name)
    _keep_beat(svc.state, svc.state.pattern_from_arrangement(path.stem))
    return _bank_response(svc, request)


@router.post("/fragment/drums/lib/load")
@pattern_edit
def fragment_drums_lib_load(request: Request, form: StrForm, svc: Svc) -> Response:
    if svc.state.load_drum_pattern(form.get("name", "")) is None:
        return Response(status_code=404)
    return _bank_response(svc, request, extra={"queue": True})


@router.post("/fragment/drums/lib/delete")
def fragment_drums_lib_delete(request: Request, form: StrForm, svc: Svc) -> Response:
    svc.state.delete_drum_pattern(form.get("name", ""))
    return _bank_response(svc, request)


@router.get("/drum/{voice}/hit.wav")
@render_outside_lock
def drum_hit(voice: str, svc: Svc) -> Response | OutsideLock:
    """A row's one-shot, what its pad and its name play: the row's clip
    when it has one (on the pads as in the loop), on the acoustic kit its
    default articulation struck hard (`_acoustic_pad`), else its
    electronic voice — a further row's stand-in (`arrange._electronic`).
    A cold acoustic pad (the kit's SFZ read, its FLACs decoded) renders
    with the lock released, from the arrangement read under it."""
    if voice not in ALL_DRUM_ROWS:
        return Response(status_code=404)
    key = "drum-hit:" + voice + ":" + svc.drum_pad_hash(voice)
    result = svc.notes.get(key)
    if result is not None:
        return _hit_wav(result)
    s = svc.state
    arr = s.arrangement  # replaced, never mutated: a snapshot
    clip = _voice_sample(arr, voice, s.data_dir)
    acoustic = clip is None and svc.drum_pad_acoustic(voice)
    values = svc.drum_voice_values(voice) if voice in drums.KIT else None

    def run() -> Response:
        audio = _acoustic_pad(arr, voice) if acoustic else clip
        if audio is None and values is not None:
            audio = drums.hit(voice, **values)
        if audio is None:
            base, params = arrange._electronic(arr, voice)
            audio = drums.hit(base, **params)
        made = RenderResult(key=key, wav=_to_wav_bytes(audio), analysis={})
        svc.notes.put(made)
        return _hit_wav(made)

    return OutsideLock(run) if acoustic else run()


def _hit_wav(result: RenderResult) -> Response:
    return Response(
        result.wav, media_type="audio/wav", headers={"Cache-Control": "no-store"}
    )


PAD_VELOCITY = 0.9  # how hard a pad strikes the acoustic kit


def _acoustic_pad(arr, voice: str):
    """`voice`'s default articulation on the acoustic kit (`ARTICULATIONS`'
    first, "hit" for a row with none) at `PAD_VELOCITY`, its first round
    robin, through the beat's mic mix and `arrange.KIT_GAIN` — as the loop
    plays it, held under full scale (a one-shot WAV has no headroom to
    give). None when the kit cannot play it (the electronic voice then)."""
    kit = arrange.acoustic_kit(arr)
    if kit is None:
        return None
    art = ARTICULATIONS.get(voice, ("hit",))[0]
    q = round(PAD_VELOCITY * 127) / 127
    try:
        audio = kit_play.render_hit(
            kit[0], kit[1], voice, art, q, 0, kit_play.levels(arr.drum_mics)
        )
    except KitError:
        return None
    if not len(audio):
        return None
    audio = audio * arrange.KIT_GAIN
    peak = float(np.max(np.abs(audio)))
    return audio * (0.99 / peak) if peak > 0.99 else audio


# --- the kit's sound: electronic or the acoustic kit ---------------------------------


@router.post("/fragment/drums/source")
@pattern_edit
def fragment_drums_source(request: Request, form: StrForm, svc: Svc) -> Response:
    """The beat's kit sound (`Arrangement.drum_kit`): "" the electronic
    voices, "naked" the acoustic kit. The rows the page draws change, so
    the page comes back whole (with its preview). Choosing the acoustic
    kit puts the open hats on the hat row, played "open"
    (`drumops.open_hats_to_hat`); going back puts them on the open hat's
    row again (`drumops.hat_to_open_hats`) — either way one pattern undo
    step with the kit."""
    s: StudioState = svc.state
    src = form.get("source", "")
    if src not in DRUM_SOURCES:
        return svc.toast("No such kit sound.")
    a = s.arrangement
    if src != a.drum_kit:
        if src == "naked":
            moved = drumops.open_hats_to_hat(a)
        else:
            moved = drumops.hat_to_open_hats(a)
        s.arrangement = replace(a, drum_kit=src, **moved)
        s.save()
    return svc.full_page_with_preview(request)


@router.post("/fragment/drums/mics")
def fragment_drums_mics(request: Request, form: StrForm, svc: Svc) -> Response:
    """The acoustic kit's mic mixer (`Arrangement.drum_mics`): `preset`
    puts every mic at its levels (the overrides and mutes go), `<mic>=<dB>`
    sets one (held to `limits.MIC_DB_RANGE`), `<mic>=off` turns it off,
    `mute.<mic>=1` / `0` mutes / unmutes it over its level (which stays:
    a -3 dB mic comes back at -3). What equals the
    preset is not written down (`schema`'s cleaner). A fader's own post is
    not drawn again under the hand; a mute or a preset redraws the mixer."""
    s: StudioState = svc.state
    mics = dict(s.arrangement.drum_mics or {})
    redraw = False
    if "preset" in form:
        if form["preset"] not in kit_play.MIC_PRESETS:
            return svc.toast("No such mic preset.")
        mics, redraw = {"preset": form["preset"]}, True
    was = kit_play.levels(mics)
    muted = set(mics.get("muted") or ())
    for mic in kit_play.MICS:
        flag = form.get(f"mute.{mic}", "")
        if flag in ("1", "0"):  # a mute over the mic's level: the level stays
            (muted.add if flag == "1" else muted.discard)(mic)
            redraw = True
        raw = form.get(mic, "").strip()
        if raw == "off":
            mics[mic], redraw = None, True
        elif raw:
            try:
                x = float(raw)
            except ValueError:
                continue
            if math.isfinite(x):
                redraw |= was.get(mic) is None  # a fader lifts a mic out of mute
                muted.discard(mic)
                mics[mic] = x
    mics["muted"] = sorted(muted)
    s.arrangement = replace(s.arrangement, drum_mics=clean_mics(mics))
    if not redraw:
        svc.suppress("drum-panels")
    return svc.do_render(request)


def _kit_source(svc: StudioService, **extra) -> str:
    """The kit-source region's HTML (in place, not out of band)."""
    kit = svc.kit_context() | extra
    tpl = svc.templates.get_template("fragments/drum_kit_source.html")
    return tpl.render(kit=kit, oob=False)


@router.post("/fragment/drums/kit/download")
@render_outside_lock
def fragment_kit_download(
    request: Request, form: StrForm, svc: Svc
) -> Response | OutsideLock:
    """Fetch the acoustic kit (`kitjobs`: one at a time, its own thread,
    every file checked); `repair=1` runs even when every file is there by
    size (the kit met a damaged one: each is hashed again). Asking whether
    it is here stats each of its files, so that runs with the lock released
    — and is asked again there before the lock is taken, so the answer
    drawn under it is the memo's (`arrange.kit_ready`). The answer is the
    kit line, polling the job."""
    repair = form.get("repair") == "1"

    def run() -> Response:
        problem = svc.kits.start(repair=repair)
        if problem == "busy":
            return svc.toast("The kit is already downloading.")
        arrange.kit_availability()  # the stat, outside the lock
        with svc.state.lock:
            here = svc.kits.state() is None
            return HTMLResponse(_kit_source(svc, just_ready=here))

    return OutsideLock(run)


@router.post("/fragment/drums/kit/cancel")
def fragment_kit_cancel(request: Request, svc: Svc) -> Response:
    """Stop the kit's download: the files that landed stay (the next
    Download carries on); the line keeps polling until the job has ended."""
    svc.kits.cancel()
    return HTMLResponse(_kit_source(svc))


@router.get("/fragment/drums/kit/job")
@lock_free
def fragment_kit_job(request: Request) -> Response:
    """The kit line, polled once a second while the download runs: it reads
    the job's own state and the beat's kit without the studio lock (as the
    Lab's `/fragment/lab/job`). The one answer that finds the job ended
    (`watch=1`) takes the lock to draw the kit's panel too (its pads are
    the kit's now) and, when the kit is here, asks the page for its loop
    again; the first such answer after a job that brought the kit drops
    the caches (`KitJobs.take_fresh`)."""
    svc: StudioService = request.app.state.svc
    if not (request.query_params.get("watch") == "1" and not svc.kits.busy()):
        return HTMLResponse(_kit_source(svc))
    arrange.kit_availability()  # the stat, outside the lock
    with svc.state.lock:
        # renders made while the kit was missing or damaged hold the
        # stand-in voices under keys that may not move: forget them — once
        # a job, and only when it brought the kit (not for each polling tab)
        if svc.kits.take_fresh():
            svc.reset_caches()
        html = _kit_source(svc, just_ready=not svc.kits.last_error())
        html += svc.oob_regions(request, "drum-panels", view="drums")
    return HTMLResponse(html)


@router.get("/song/track/{track_id}/hit/{voice}.wav")
def song_drum_hit(track_id: str, voice: str, svc: Svc) -> Response:
    """One voice of a song drums track's OWN kit (its `drum_params` /
    `drum_samples`, `song_bridge.KIT`): what the Song page's finger drums
    play while the drums lane is armed (songtake.js). `/drum/{voice}/hit.wav`
    plays the working loop's kit, which is another kit. Cached in the note
    memo by the voice's content, like that one."""
    s = svc.state
    i = sm.track_index(s.song, track_id)
    if voice not in drums.KIT or i is None or s.song["tracks"][i]["kind"] != "drums":
        return Response(status_code=404)
    track = s.song["tracks"][i]
    arr = replace(s.arrangement, **{k: track[k] for k in song_bridge.KIT if k in track})
    values = defaults_of(voice) | (arr.drum_params.get(voice) or {})
    sample = (arr.drum_samples or {}).get(voice)
    ident = {"params": values}
    if sample:
        ident["sample"] = [sample, clip_hash(s.data_dir, sample)]
    blob = json.dumps(ident, sort_keys=True).encode()
    key = "song-hit:" + voice + ":" + hashlib.sha1(blob).hexdigest()[:8]
    result = svc.notes.get(key)
    if result is None:
        audio = _voice_sample(arr, voice, s.data_dir)
        if audio is None:
            audio = drums.hit(voice, **values)
        result = RenderResult(key=key, wav=_to_wav_bytes(audio), analysis={})
        svc.notes.put(result)
    return Response(
        result.wav, media_type="audio/wav", headers={"Cache-Control": "no-store"}
    )
