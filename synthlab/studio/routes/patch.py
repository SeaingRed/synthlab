"""Shaping the sound on Synths: patch and key/tempo posts, Beat ▾ (the
beat the phrase plays over), the chord bar, curves, morph, A/B slots,
layers, and the panel reset/dice tools."""

from __future__ import annotations

import json
import math
from dataclasses import replace

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from ...params import MOD_SLOTS, clamp, lerp
from ...sequencer import midi
from ...synth import Patch
from .. import beatpick, chords, modmixes, schema
from .. import patternops as po
from ..arrange import FOLLOW_OCTAVES, arrangement_from_form
from ..audition import CHOICES as PHRASE_CHOICES
from ..breedboard import WILDNESS
from ..curves import CURVABLE, curve_svg, toggle_point
from ..forms import patch_from_form
from ..limits import MAX_GRID_STEPS
from ..paramui import PARAM_GROUPS, spec_of
from ..render import RenderError, loop_refusal
from ..routing import StrForm, Svc, pattern_edit, studio_router
from ..schema import valid_pitch
from ..service import StudioService, group_specs
from ..sounds import SOURCES, find
from ..state import Arrangement, StudioState, lane_fields, pitch_rows
from ..state.model import grid_rows
from .macros import param_rows_oob

router = studio_router()


def curve_fragment(svc: StudioService, param: str) -> str:
    s: StudioState = svc.state
    svg = curve_svg(s.curves.get(param, []), param)
    spec = spec_of(param)
    return svc.templates.get_template("fragments/curve.html").render(
        param=param, svg=svg, label=spec.label if spec else param
    )


@router.post("/fragment/patch")
def fragment_patch(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    asked = None
    if any(k.startswith("cell.") or k == "bpm" for k in form):
        asked = _merge_arrangement(svc, form)
        refusal = loop_refusal(s.arrangement, asked)
        if refusal:  # before anything is stored (`render.loop_refusal`)
            return svc.toast(refusal, still_playing=True)
    after = patch_from_form(form, s.patch)
    s.push_history(after)  # (its moved fields: what may join a drag's step)
    s.patch = after
    if asked is not None:
        s.arrangement = asked
    return svc.do_render(request)


def _merge_arrangement(svc: StudioService, form: dict) -> Arrangement:
    """`arrangement_from_form` rebuilds an Arrangement from scratch, so any
    field the posting form doesn't carry would silently reset to its
    default. Every view posts only its own slice of the arrangement, so
    each absent slice is carried over from current state instead. The
    keep-sets come from each field's `lane` tag on `Arrangement`
    (state.py) — not a hand-copied tuple — so a new drum-lane or
    clip-lane field is preserved automatically, never silently reset:

      * drum clock/mixer  — never on the synth form
      * drum cells        — only when the form posts `drum.*`
      * the note grid     — only when the form posts `cell.*` (the grid
        moved to Perform; the Synths sound bar posts key + bpm alone)
      * grid shape/feel   — only when the form posts `bars`/`steps`
    """
    s: StudioState = svc.state
    new = arrangement_from_form(form, meter=s.arrangement.meter)
    # the drum machine's clock + mixer are never on the synth form —
    # "drums" (the step cells) is the one drum-lane field this route can
    # carry, so it's excluded here and handled on its own below
    keep = {
        k: getattr(s.arrangement, k)
        for k in (lane_fields("drum") - {"drums"}) | lane_fields("clip")
    }
    if not any(k.startswith("drum.") for k in form):
        keep["drums"] = s.arrangement.drums
    # each cell-lane field is carried by its own prefix — the bass
    # quick-key posts vel.* with no cell.*, so one shared test would
    # drop the velocities. The note grid's own post carries `span`
    # (transport.html): an unchecked box posts nothing, so a grid emptied by
    # hand has no `cell.*` at all — read as "not posted", the last note (or
    # slide) could never be clicked off: the page showed an empty grid and
    # the loop kept playing the note (tests/browser/test_perform.py).
    grid = "span" in form
    for field_name, prefix in (
        ("cells", "cell."),
        ("cuts", "cell."),
        ("velocities", "vel."),
        ("slides", "slide."),
    ):
        if not (grid or any(k.startswith(prefix) for k in form)):
            keep[field_name] = getattr(s.arrangement, field_name)
        else:
            keep[field_name] = _carry_over(
                field_name, s.arrangement, new, form, _span(s, form), _oct(s, form)
            )
    # a cut belongs to a note: none may outlive its cell
    on = {(c[0], int(c[1])) for c in keep["cells"]}
    keep["cuts"] = [c for c in keep["cuts"] if (c[0], int(c[1])) in on]
    # ... and a step's timing a note on its step (no form carries it)
    keep["note_nudge"] = schema.clean_nudge(s.arrangement.note_nudge, keep["cells"])
    if "bpm" not in form:
        keep["bpm"] = s.arrangement.bpm
    if "bars" not in form and "steps" not in form:
        for k in lane_fields("grid"):
            keep[k] = getattr(s.arrangement, k)
    if "strum" not in form:  # a grid form drawn without the field
        keep["strum"] = s.arrangement.strum
    # the chance lane's fields are hidden inputs: they post whenever the
    # lane is drawn, so a form without one (another grid's) keeps it
    if any(k.startswith("nchance.") for k in form):
        keep["note_chance"] = _carry_over("note_chance", s.arrangement, new, form)
    else:
        keep["note_chance"] = s.arrangement.note_chance
    for k in lane_fields("chords"):  # the loop's chord track: its own route sets it
        keep[k] = getattr(s.arrangement, k)
    return replace(new, **keep)


def _posted_steps(form: dict, prefix: str) -> dict[str, str]:
    """`<prefix><rest>` keys of a grid post, by `<rest>`."""
    return {k[len(prefix) :]: v for k, v in form.items() if k.startswith(prefix)}


def _step(x: str) -> int | None:
    # length first: int() refuses a digit string past Python's 4300-digit
    # limit with a ValueError (`cell.C4.<5000 nines>` was a 500)
    if not (x.isascii() and x.isdigit() and len(x) <= 9):
        return None
    return int(x) if int(x) < MAX_GRID_STEPS else None


def _span(s: StudioState, form) -> int:
    """The range the posting grid was drawn with: the transport posts its
    own (`span`), so a page drawn before a range change still posts
    against the rows it shows."""
    raw = form.get("span", "")
    return int(raw) if raw in ("1", "2", "3") else s.grid_span


def _oct(s: StudioState, form) -> int:
    """... and the octave window it was drawn at (the roll posts `oct`)."""
    raw = form.get("oct", "")
    return int(raw) if raw in ("-1", "0", "1", "2", "3") else s.grid_oct


def _grow(
    cur: Arrangement, new: Arrangement, span: int, octave: int = 0
) -> Arrangement:
    """More bars repeat the ones you have — the drum transport's rule,
    so a 1-bar riff grown to 4 bars never plays once and then goes
    silent for three. Bars that already hold
    notes (hidden by an earlier shrink) come back as they were instead."""
    old, total = cur.bars * cur.steps_per_bar, new.bars * new.steps_per_bar
    if new.steps_per_bar != cur.steps_per_bar or total <= old:
        return new
    rows = set(grid_rows(new, span, octave))
    return replace(
        new,
        cells=po.repeat_into(new.cells, old, total, rows),
        cuts=po.repeat_into(new.cuts, old, total, rows),
        velocities=po.repeat_lane(new.velocities, old, total),
        note_chance=po.repeat_lane(new.note_chance, old, total),
        slides=po.repeat_steps(new.slides, old, total),
    )


def _carry_over(
    field_name: str,
    cur: Arrangement,
    new: Arrangement,
    form,
    span: int = 1,
    octave: int = 0,
) -> object:
    """A grid post's `cells` / `velocities` / `slides`, keeping what the
    posted grid could not show. The form's checkboxes are the grid it was
    drawn with — the current key's rows over the current bars x steps — so
    a note outside that (written under another key, or past a shrunk grid)
    was never posted and is kept, hidden (limits.py: a shrunk grid hides
    notes, it doesn't delete them). A posted note the *new* key or length
    has no place for is kept hidden the same way: changing the root, the
    scale or the bars never deletes a note (the Song drawer's `_notes_grid`
    keeps them too). `span` and `octave` are the range and the octave
    window the grid was drawn at."""
    rows = set(grid_rows(cur, span, octave))
    shown = cur.bars * cur.steps_per_bar
    if field_name == "cells":
        posted = []
        for rest in _posted_steps(form, "cell."):
            pitch, _dot, raw = rest.rpartition(".")
            st = _step(raw)
            if valid_pitch(pitch) and st is not None:
                posted.append([pitch, st])
        hidden = [
            list(c) for c in cur.cells if not (c[0] in rows and int(c[1]) < shown)
        ]
        seen = {(c[0], int(c[1])) for c in [*new.cells, *posted, *hidden]}
        return [list(c) for c in sorted(seen)]
    if field_name == "cuts":
        # a cell posted as "cut" starts a new note; hidden cells keep theirs
        posted = []
        for rest, value in _posted_steps(form, "cell.").items():
            pitch, _dot, raw = rest.rpartition(".")
            st = _step(raw)
            if value == "cut" and valid_pitch(pitch) and st is not None:
                posted.append([pitch, st])
        hidden = [list(c) for c in cur.cuts if not (c[0] in rows and int(c[1]) < shown)]
        return [list(c) for c in sorted({(c[0], int(c[1])) for c in posted + hidden})]
    if field_name == "velocities":
        vel = {k: v for k, v in cur.velocities.items() if int(k) >= shown}
        for raw, v in _posted_steps(form, "vel.").items():
            st = _step(raw)
            try:
                val = float(v)
            except ValueError:
                continue
            if st is not None and math.isfinite(val):
                vel[str(st)] = min(1.0, max(0.0, val))
        return vel
    if field_name == "note_chance":  # as the velocities; 1 is not stored
        out = {k: v for k, v in cur.note_chance.items() if int(k) >= shown}
        out.update(new.note_chance)
        return out
    posted_slides = {_step(raw) for raw in _posted_steps(form, "slide.")}
    kept = {x for x in cur.slides if x >= shown}
    return sorted(kept | {x for x in posted_slides if x is not None})


def _structure_of(arr) -> tuple:
    return (arr.bars, arr.steps_per_bar, arr.root, arr.scale_name)


@router.post("/fragment/arrangement")
@pattern_edit
def fragment_arrangement(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    before = _structure_of(s.arrangement)
    try:
        submitted_bpm = float(form.get("bpm", s.arrangement.bpm))
    except ValueError:
        submitted_bpm = s.arrangement.bpm
    asked = _grow(
        s.arrangement, _merge_arrangement(svc, form), _span(s, form), _oct(s, form)
    )
    refusal = loop_refusal(s.arrangement, asked)
    if refusal:  # before anything is stored (`render.loop_refusal`)
        return svc.toast(refusal, still_playing=True)
    if form.get("phrase") in PHRASE_CHOICES:  # a phrase, or none: the beat alone
        s.audition = form["phrase"]  # rides on the Synths sound bar's post
        # checkbox on the same bar (Beat ▾ sets it on its own post)
        s.vs_beat = form.get("vs_beat") == "on"
    if form.get("edit_hear") in ("mix", "alone"):
        # a song track's edit: ▶ in the mix or alone (sound_bar.html)
        s.song_edit_hear = form["edit_hear"]
    s.arrangement = asked
    bpm_clamped = submitted_bpm != s.arrangement.bpm
    if _structure_of(s.arrangement) == before and not bpm_clamped:
        # the strip that posted already shows what was typed or clicked:
        # redrawing it would drop the gesture (and a phrase pill's click
        # would never reach audio.js). A new key, length or a clamped bpm
        # reshapes it, so then it comes back.
        svc.suppress("sound-bar", "transport")
    return svc.do_render(request)


@router.post("/fragment/beat/pick")
@pattern_edit
def fragment_beat_pick(request: Request, form: StrForm, svc: Svc) -> Response:
    """Synths' Beat ▾ and its kit (`beatpick.py`): `beat_pick` (a value of
    `beatpick.options`: a variation, a bank slot, a new beat in a style,
    or `none`) or `beat_kit` (a kit's name, put under the beat). Each
    select posts itself with the other blanked; "" — its first option, the
    beat as it is — moves nothing. A pick is the loop's beat, so one
    pattern undo step (Ctrl+Z on Perform), and turns "over the beat" on
    (`none` off). The sound bar comes back by its watch (`beatpick.watch`),
    its selects on "" again; the answer is the preview."""
    s: StudioState = svc.state
    if kit := form.get("beat_kit", ""):
        if not beatpick.swap_kit_under(s, kit):
            return svc.toast("No such kit.")
        s.vs_beat = True
    elif pick := form.get("beat_pick", ""):
        if why := beatpick.apply(s, pick):
            return svc.toast(why, still_playing=True)
    else:
        return HTMLResponse("")
    return svc.do_render(request)


def _parse_code(svc: StudioService, form: dict) -> list[list[int]]:
    s: StudioState = svc.state
    return chords.parse_code(
        form.get("code", ""),
        s.arrangement.root,
        s.arrangement.scale_name,
        form.get("voicing", "triad"),
    )


@router.get("/fragment/chordlib")
def fragment_chordlib(request: Request, svc: Svc) -> Response:
    """The chord poster, fetched on first open. Rendering it into every
    page cost ~500 buttons and most of the editor's HTML weight."""
    return HTMLResponse(
        svc.templates.get_template("fragments/chord_library.html").render(
            chord_library=chords.chord_library(
                svc.state.arrangement.root.rstrip("-0123456789") + "3",
                svc.state.arrangement.scale_name,
            )
        )
    )


@router.post("/fragment/chords/code")
def fragment_chords_code(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    try:
        parsed = _parse_code(svc, form)
    except chords.CodeError as err:
        return svc.toast(str(err))
    return Response(
        json.dumps({"chords": parsed, "beat_seconds": 60.0 / s.arrangement.bpm}),
        media_type="application/json",
    )


@router.post("/fragment/chords/grid")
@pattern_edit
def fragment_chords_grid(request: Request, form: StrForm, svc: Svc) -> Response:
    """Write the sequence into the step grid, one chord per step. The grid
    has 6 rows — the key's first scale tones from the (bass) root — while
    chords are voiced at octaves 3-4, so every note folds onto the row of
    its pitch class (`_grid_row`). The sequence replaces what the grid
    shows (notes hidden under another key's rows or past the grid's
    length stay, as `_carry_over` keeps them); a sequence with nothing to
    land leaves the grid alone."""
    s: StudioState = svc.state
    if not form.get("code", "").strip():  # "→ grid" pressed with nothing typed
        return svc.toast(
            "Type a progression in the code line first — e.g. Am F C G or I V vi IV."
        )
    try:
        parsed = _parse_code(svc, form)
    except chords.CodeError as err:
        return svc.toast(str(err))
    arr = s.arrangement
    rows = pitch_rows(arr)
    total = arr.bars * arr.steps_per_bar
    landed: set[tuple[str, int]] = set()
    snapped = 0
    for step, chord in enumerate(parsed[:total]):
        for m in chord:
            row, exact = _grid_row(int(m), rows)
            landed.add((row, step))
            snapped += not exact
    if not landed:
        return svc.toast("Nothing to write — that sequence is all rests.")
    kept = [list(c) for c in arr.cells if not (c[0] in rows and int(c[1]) < total)]
    cells = sorted({(c[0], int(c[1])) for c in kept} | landed)
    s.arrangement = replace(arr, cells=[list(c) for c in cells])
    s.show_notes()  # the six rows it wrote on, wherever the window was
    # the grid itself lives in Perform now: the notes land there
    resp = svc.do_render(request, view="editor")
    if snapped and resp.status_code == 200:
        trig = json.loads(resp.headers["HX-Trigger"])
        trig["studio-toast"] = {
            "message": f"{snapped} note(s) folded onto the nearest grid row "
            "(outside the key, or above/below the grid's rows)"
        }
        resp.headers["HX-Trigger"] = json.dumps(trig)
    return resp


def _grid_row(m: int, rows: list[str]) -> tuple[str, bool]:
    """The grid row a chord note lands on, and whether it is that note's
    own pitch class. The rows span about an octave above the root (A1-G2
    in A1 minor) while chord pads sit at octaves 3-4, so matching names
    landed nothing. The note folds onto the row of its pitch class — the
    one nearest once folded into the rows' octave — and a tone outside
    the key onto the nearest row by pitch class."""
    mids = [midi(r) for r in rows]
    lo, hi = min(mids), max(mids)
    folded = m
    while folded > hi:
        folded -= 12
    while folded < lo:
        folded += 12

    def dist(r: int) -> tuple[int, int]:
        pc = abs(r - m) % 12
        return (min(pc, 12 - pc), abs(r - folded))

    best = min(range(len(rows)), key=lambda i: dist(mids[i]))
    return rows[best], dist(mids[best])[0] == 0


@router.post("/fragment/morph")
def fragment_morph(request: Request, form: StrForm, svc: Svc) -> Response:
    """Blend the live patch toward slot A/B through the genome
    (`params.lerp`). One morph per gesture — the slider posts on change,
    from the current sound."""
    s: StudioState = svc.state
    target = s.slot_a if form.get("slot", _ab_other(s)) == "a" else s.slot_b
    if target is None:
        return svc.toast("That slot is empty — store a sound in it first.")
    try:
        t = min(1.0, max(0.0, float(form.get("t", 0))))
    except ValueError:
        return Response(status_code=422)
    s.push_history()
    before = s.patch
    s.patch = lerp(before, target, t)
    # the slider posts with hx-swap="none", so the panels would keep the
    # pre-morph values — and the next edit would post them straight back.
    # Every row the blend moved comes back OOB (like a macro move), and the
    # A/B strip too, which springs the morph slider back to 0 for the next
    # gesture.
    moved = [
        spec.name
        for _, specs in PARAM_GROUPS
        for spec in specs
        if repr(getattr(before, spec.name)) != repr(getattr(s.patch, spec.name))
    ]
    ab = svc.templates.get_template("fragments/ab_slots.html").render(
        slot_a=s.slot_a, slot_b=s.slot_b, ab_live=s.ab_live, ab_oob=True
    )
    return svc.do_render(request, param_rows_oob(svc, moved) + ab)


@router.post("/fragment/panel/reset")
def panel_reset(request: Request, form: StrForm, svc: Svc) -> Response:
    group = form.get("group", "")
    specs = group_specs(group)
    if specs is None:
        return Response(status_code=404)
    s: StudioState = svc.state
    defaults = Patch()
    s.push_history()  # the panel's own tooltip promises Ctrl+Z undoes it
    s.patch = replace(
        s.patch, **{spec.name: getattr(defaults, spec.name) for spec in specs}
    )
    s.save()
    return svc.full_page_with_preview(request)


@router.post("/fragment/panel/dice")
def panel_dice(request: Request, form: StrForm, svc: Svc) -> Response:
    """Randomize ONE panel at the current breed wildness: mutate() the
    whole genome, keep only this panel's fields. mutate() draws each gene
    it can hear by chance (never a `nogene` field, never one gated off by
    `params.live`) from a fresh unseeded generator, so one draw can leave
    every field of this panel alone: up to five draws, and an undo step
    only when one of them changed something."""
    from ...explore.breed import mutate

    group = form.get("group", "")
    specs = group_specs(group)
    if specs is None:
        return Response(status_code=404)
    s: StudioState = svc.state
    names = {spec.name for spec in specs}
    amount = WILDNESS.get(s.breed_wildness, WILDNESS["normal"])
    for _ in range(5):
        mutated, _ = mutate(s.patch, amount)
        changes = {
            n: getattr(mutated, n)
            for n in names
            if getattr(mutated, n) != getattr(s.patch, n)
        }
        if changes:
            s.push_history()
            s.patch = replace(s.patch, **changes)
            break
    s.save()
    return svc.full_page_with_preview(request)


@router.post("/fragment/mod/mix")
def mod_mix(request: Request, form: StrForm, svc: Svc) -> Response:
    """The mod matrix in one move (modmixes.py): a named mix, "random" (a
    musical draw: two to four routes and an LFO), or "clear" (every route
    unplugged). One undo step; the toast says what it routed."""
    name = form.get("name", "")
    s: StudioState = svc.state
    if name == "random":
        words, fields = modmixes.random_mix()
        said = f"Random mod mix: {words} (Ctrl+Z undoes it)"
    elif name == "clear":
        fields = {f"{slot}_src": "none" for slot in MOD_SLOTS}
        said = "Every mod route unplugged (Ctrl+Z undoes it)"
    else:
        fields = modmixes.mix_fields(name)
        if fields is None:
            return Response(status_code=404)
        said = f"Mod mix: {modmixes.MIXES[name][0]} — {modmixes.MIXES[name][1]}"
    s.push_history()
    s.patch = clamp(replace(s.patch, **fields))
    s.save()
    resp = svc.full_page_with_preview(request)
    trig = json.loads(
        resp.headers.get("HX-Trigger") or "{}"
    )  # the page's own triggers stay
    resp.headers["HX-Trigger"] = json.dumps(trig | {"studio-toast": {"message": said}})
    return resp


@router.get("/fragment/curve-close")
def close_curve(request: Request) -> Response:
    """Done on an inline curve: empties the row's .curve-slot. The curve
    itself is untouched — it lives in state, not in the editor."""
    return HTMLResponse("")


@router.get("/fragment/curve/{param}")
def get_curve(request: Request, param: str, svc: Svc) -> Response:
    if param not in CURVABLE:
        return Response(status_code=404)
    return HTMLResponse(curve_fragment(svc, param))


def _curve_response(svc: StudioService, request: Request, param: str) -> Response:
    """post_curve/clear_curve's shared tail: re-render, prefix the
    curve's own SVG onto whatever do_render answers. The curve point was
    already toggled/cleared in state, so even a failed render should
    still show the curve's new shape next to the toast — do_render's
    RenderError is therefore caught HERE (not left to the global
    handler) so it can be composed with curve_fragment(svc, param) rather
    than replacing it outright."""
    try:
        render_resp = svc.do_render(request)
    except RenderError as err:
        err_resp = svc.toast(str(err), still_playing=True)
        return HTMLResponse(
            curve_fragment(svc, param) + bytes(err_resp.body).decode(),
            status_code=err_resp.status_code,
        )
    headers = {}
    trigger = render_resp.headers.get("HX-Trigger")
    if trigger:
        headers["HX-Trigger"] = trigger
    return HTMLResponse(
        curve_fragment(svc, param) + bytes(render_resp.body).decode(),
        status_code=render_resp.status_code,
        headers=headers,
    )


@router.post("/fragment/curve/{param}")
def post_curve(request: Request, param: str, form: StrForm, svc: Svc) -> Response:
    if param not in CURVABLE:
        return Response(status_code=404)
    s: StudioState = svc.state
    try:
        x, y, w, h = (
            float(form.get("x", 0)),
            float(form.get("y", 0)),
            float(form.get("w", 1)) or 1.0,
            float(form.get("h", 1)) or 1.0,
        )
    except ValueError:
        x, y, w, h = 0.0, 0.0, 1.0, 1.0
    t, v = x / w, 1 - y / h
    s.curves[param] = toggle_point(s.curves.get(param, []), t, v)
    return _curve_response(svc, request, param)


@router.post("/fragment/curve/{param}/set")
def set_curve(request: Request, param: str, form: StrForm, svc: Svc) -> Response:
    """A whole curve at once: the keyboard's "record bend" posts one loop of
    the bend wheel as the edited lane's pitch_bend curve (curves are the
    main lane's only, by design). `points` is JSON [[t, v], ...] in 0..1,
    conformed by the same schema node state.json's curves go through."""
    if param not in CURVABLE:
        return Response(status_code=404)
    try:
        raw = json.loads(form.get("points", ""))
    except ValueError:
        return Response(status_code=422)
    points = schema.conform({param: raw}, schema.nodes().CURVES).get(param)
    if not points or len(points) < 2:
        return Response(status_code=422)
    s: StudioState = svc.state
    s.push_history()
    s.curves[param] = sorted((list(p) for p in points), key=lambda p: p[0])
    return _curve_response(svc, request, param)


@router.post("/fragment/curve/{param}/clear")
def clear_curve(request: Request, param: str, svc: Svc) -> Response:
    if param not in CURVABLE:
        return Response(status_code=404)
    s: StudioState = svc.state
    s.curves[param] = []
    return _curve_response(svc, request, param)


def _ab_other(s: StudioState) -> str:
    """The slot ⇄ and the morph go to: the one you're not hearing (A while
    neither is live, unless only B holds a sound)."""
    if s.ab_live == "a":
        return "b"
    if s.ab_live == "b":
        return "a"
    return "a" if s.slot_a is not None or s.slot_b is None else "b"


@router.post("/fragment/ab/{action}")
def ab(request: Request, action: str, svc: Svc) -> Response:
    """A/B compare. The live patch is one of the two slots (`ab_live`) or
    neither: pressing a filled slot saves the edits into the live one, then
    puts the pressed one live — its button lit, the other shown only as
    holding a sound (●). An empty slot stores the live sound as it is and
    becomes the live one (the slot you were in keeps what it held), so A,
    tweak, B gives two versions to flip between. ⇄ goes to the other slot.

    It used to swap: a filled slot traded places with the live patch, so a
    slot's contents moved on every press, nothing said which one you were
    hearing, and two filled slots lit up the same — as if both were on."""
    s: StudioState = svc.state
    if action == "swap":
        if s.slot_a is None and s.slot_b is None:
            return svc.toast("Nothing in A or B yet — store the current sound first.")
        action = f"slot_{_ab_other(s)}"
    if action in ("slot_a", "slot_b"):
        name = action[-1]
        target = getattr(s, action)
        if target is None:
            # an empty slot branches: the sound as it is now goes into it,
            # and the live slot keeps what it held — "A, tweak, B" leaves
            # two versions, not two copies of the tweak
            setattr(s, action, s.patch)
        elif s.ab_live is not None:
            setattr(s, f"slot_{s.ab_live}", s.patch)  # the edits stay in their slot
        if target is not None and s.ab_live != name:
            # switching is a load: undoable, "before last load" holds it
            s.stash_for_load()
            s.patch = target
        s.ab_live = name
        s.save()
        return svc.full_page_with_preview(request)
    if action in ("copy_a", "copy_b"):
        setattr(s, f"slot_{action[-1]}", s.patch)
        s.ab_live = action[-1]
    else:
        return Response(status_code=404)
    s.save()
    return Response(status_code=204)


@router.post("/fragment/layer/select")
def layer_select(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    try:
        index = int(form.get("index", ""))
    except ValueError:
        return Response(status_code=422)
    if not 0 <= index < len(s.layers):
        return Response(status_code=422)
    s.select_layer(index)
    s.save()
    return svc.full_page_with_preview(request)


@router.post("/fragment/layer/mute")
def layer_mute(request: Request, form: StrForm, svc: Svc) -> Response:
    s: StudioState = svc.state
    try:
        index = int(form.get("index", ""))
    except ValueError:
        return Response(status_code=422)
    if not 0 <= index < len(s.layers):
        return Response(status_code=422)
    s.set_layer_muted(index, not s.layers[index].get("muted", False))
    s.save()
    return svc.do_render(request)


def _layer_index(s: StudioState, form) -> int | None:
    """The slot a layer-strip post names, or None when it names none."""
    try:
        index = int(form.get("index", ""))
    except ValueError:
        return None
    return index if 0 <= index < len(s.layers) else None


@router.post("/fragment/layer/solo")
def layer_solo(request: Request, form: StrForm, svc: Svc) -> Response:
    """S: while any layer is soloed, only soloed layers are heard (mute
    still wins). Monitoring — never saved into a preset or the song."""
    s: StudioState = svc.state
    index = _layer_index(s, form)
    if index is None:
        return Response(status_code=422)
    s.set_layer_solo(index, not s.layers[index].get("solo"))
    s.save()
    return svc.do_render(request)


@router.post("/fragment/layer/gain")
def layer_gain(request: Request, form: StrForm, svc: Svc) -> Response:
    """The strip's level slider. Its own row is not redrawn under the hand."""
    s: StudioState = svc.state
    index = _layer_index(s, form)
    try:
        gain = float(form.get("gain", ""))
    except ValueError:
        return Response(status_code=422)
    if index is None or not math.isfinite(gain):
        return Response(status_code=422)
    s.set_layer_gain(index, gain)
    svc.suppress("layer-strip")
    return svc.do_render(request)


@router.post("/fragment/layer/follow")
def layer_follow(request: Request, form: StrForm, svc: Svc) -> Response:
    """Follow main: the layer plays the main lane's notes (the phrase on
    Synths, the grid on Perform) at `octave`; `on` 0 gives it back its own
    notes. The main lane can't follow itself."""
    s: StudioState = svc.state
    index = _layer_index(s, form)
    if index is None:
        return Response(status_code=422)
    on = form.get("on", "1") not in ("0", "", "off", "false")
    octave = None
    if on:
        try:
            octave = int(form.get("octave", "0"))
        except ValueError:
            return Response(status_code=422)
        if octave not in FOLLOW_OCTAVES:
            return Response(status_code=422)
        if index == s.main_index():
            return svc.toast(
                f"Layer {index + 1} is the main lane — the other layers follow it."
            )
    s.set_follow(index, octave)
    s.save()
    return svc.do_render(request)


@router.post("/fragment/layer/focus")
def layer_focus(request: Request, form: StrForm, svc: Svc) -> Response:
    """▶ hears all layers (0) or only the one being edited (1)."""
    s: StudioState = svc.state
    s.layer_focus = form.get("on", "0") == "1"
    s.save()
    return svc.do_render(request)


@router.post("/fragment/layer/add")
def layer_add(request: Request, form: StrForm, svc: Svc) -> Response:
    """+ Add layer: a sound from the Sounds dock or the Library (`source` +
    `name`), or a copy of the edited sound, into a free slot — following
    the main lane, and now the layer being edited. `own=1` (Perform's lane
    switch): the new lane does not follow — it has its own empty grid and
    sounds as soon as a note is drawn on it — and the page stays where it
    is. Either way the main lane is pinned first, so what the other lanes
    play does not move.

    A picked sound is never refused: with every slot full, or while Synths
    edits a song track's sound (a new layer is the loop's and would end
    that edit), it replaces the sound being edited — the load a click on
    the card's name makes (`svc.adopt`; "before last load" brings the old
    one back)."""
    s: StudioState = svc.state
    source, name = form.get("source", ""), form.get("name", "")
    fx = s.layers[s.active_layer].get("fx") or []
    patch = s.patch
    if source:
        if source not in SOURCES:
            return Response(status_code=404)
        sound = find(svc, source, name)
        if sound is None:
            return svc.toast("That sound is gone.")
        if s.free_slot() is None or s.song_edit_track is not None:
            # the one sound: its patch and its own FX and curves, never the
            # layers a whole Sound brings (they would replace every lane),
            # and no landing elsewhere
            chain = sound.chain and {**sound.chain, "layers": None}
            return svc.adopt(request, replace(sound, chain=chain, home_view=None))
        patch, fx = sound.patch, (sound.chain or {}).get("main_fx") or []
    if s.free_slot() is None:
        return svc.toast("Every layer holds a sound — clear one first (✕).")
    made = s.add_layer(patch, fx)
    own = form.get("own") == "1" and made is not None
    if own:
        s.set_follow(made, None)
    s.save()
    if svc.view_of(request) != "editor" and not own:
        return svc.full_page_with_preview(
            request, view="editor", push_url="/view/editor"
        )
    return svc.full_page_with_preview(request)


@router.post("/fragment/layer/clear")
def layer_clear(request: Request, form: StrForm, svc: Svc) -> Response:
    """✕: empty a layer. The one being edited too — the panels move to the
    nearest layer with a sound (`StudioState.clear_layer`), so the page
    comes back whole; only the last sound there is stays."""
    s: StudioState = svc.state
    index = _layer_index(s, form)
    if index is None:
        return Response(status_code=422)
    edited = index == s.active_layer
    if edited and s.song_edit_track is not None:
        # the panels show a song track's sound, not this slot's: clearing
        # "it" would end the edit and empty a layer of the loop nobody saw
        return svc.toast(
            "This is a song track's sound — close the edit, or delete the "
            "track on the Song page."
        )
    if not s.clear_layer(index):
        return svc.toast("That's the only sound here — load another one over it.")
    s.save()
    if edited:
        return svc.full_page_with_preview(request)
    return svc.do_render(request)
