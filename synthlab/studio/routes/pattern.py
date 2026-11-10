"""Pattern tools: the channel-rack / piano-roll edits over the working
loop's step grids (`studio/patternops.py`, whose `notes_tool` /
`drums_tool` the Song drawer's pool patterns share), a MIDI file into the
note grid (`studio/midi_bridge.py`), the grids' own undo, and the note
grid's range.

Every edit acts on what the grid shows and keeps what it hides (another
key's notes, steps past a shrunk grid), and is one pattern undo step
(`@pattern_edit`). A note-grid tool answers like a grid post
(`do_render`: the transport region comes back by its watch); a drum tool
answers like every other drum tool (`_bank_response`: #drum-bank on Drums,
the Perform rack by its watch).
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import replace

from fastapi import Request, Response

from ... import midifile
from ...sequencer import SCALES, midi, name_of
from .. import drumops, limits, midi_bridge, schema, songops
from .. import meter as _meter
from .. import patternops as po
from .. import song_model as sm
from ..gen import chordtrack
from ..gen import loop as gl
from ..render import loop_refusal
from ..routing import (
    MidiUpload,
    StrForm,
    Svc,
    autoplay,
    pattern_edit,
    studio_router,
)
from ..state import StudioState, key_ladder
from ..state.model import GRID_OCTAVES, NOTE_LANE, grid_rows
from .drums import _bank_response
from .patch import _oct, _span

log = logging.getLogger("synthlab.studio")

router = studio_router()


def with_toast(resp: Response, message: str) -> Response:
    """A done edit that still has something to say: the note rides the
    response's HX-Trigger (perform.js shows it), never a 422."""
    trig = json.loads(resp.headers.get("HX-Trigger") or "{}")
    trig["studio-toast"] = {"message": message}
    resp.headers["HX-Trigger"] = json.dumps(trig)
    return resp


@router.post("/fragment/pattern/notes/{op}")
@pattern_edit
def pattern_notes(request: Request, op: str, form: StrForm, svc: Svc) -> Response:
    """The note grid's tools: shift ◀ ▶, reverse, ×2, clear, transpose by a
    key tone or an octave, velocity humanise / ramp / accent / reset, and
    "tidy" (drop the notes the grid keeps but cannot show) — and the piano
    roll's (`patternops.ROLL_TOOLS`: move, copy, delete, resize, legato,
    half, double, stamp), which read the form: `sel`, `dx` / `dy`, `by`,
    `row` / `step` / `q` / `len`, and the `span` / `oct` the grid was
    drawn at."""
    s: StudioState = svc.state
    a = s.arrangement
    rows = grid_rows(a, _span(s, form), _oct(s, form))
    lane = {k: getattr(a, k) for k in NOTE_LANE}
    # the working loop's chance lane moves with its notes (no pattern has one)
    lane["note_chance"] = a.note_chance
    now = None  # the roll's selection after the edit (roll.js follows it)
    try:
        if po.is_roll(op, form):
            args = dict(form)
            if (
                op == "stamp" and form.get("q") == "track"
            ):  # the loop's chord lane's chord there
                args["tones"] = ",".join(_track_tones(a, form, rows))
            lane, note, now = po.roll_tool(
                op, lane, rows=rows, ladder=key_ladder(a), args=args
            )
        else:
            lane, note = po.notes_tool(
                op,
                lane,
                rows=set(rows),
                ladder=key_ladder(a),
                octave=len(SCALES[a.scale_name]),
                choices=limits.BAR_CHOICES,
                seed=random.randrange(1 << 30),
                arp_on=a.arp_mode != "off",
            )
    except KeyError:
        return Response(status_code=404)
    except po.ToolRefused as err:
        return svc.toast(str(err))
    s.arrangement = replace(a, **lane)
    resp = svc.do_render(request)
    if now is not None:
        trig = json.loads(resp.headers.get("HX-Trigger") or "{}")
        resp.headers["HX-Trigger"] = json.dumps(trig | {"roll-sel": {"sel": now}})
    return with_toast(resp, note) if note else resp


def _track_tones(a, form: dict, rows: list[str]) -> list[str]:
    """The chord-track stamp on Perform: the chord the loop's chord lane
    holds under the stamped step, root position from the nearest root at
    or below the clicked row (`chordtrack.voiced`, the drawer's rule). A
    loop with no chords of its own: the key's first chord."""
    try:
        step = int(str(form.get("step", "0"))[:6])
    except ValueError:
        step = 0
    spb, bars = int(a.steps_per_bar), max(1, int(a.bars))
    chord = chordtrack.chord_at(
        chordtrack.resolve(gl.song_of(a), None, bars, spb), step
    )
    at = midi(form["row"]) if form.get("row") in rows else midi(rows[-1])
    return [name_of(m) for m in chordtrack.voiced(chord, at)]


@router.post("/fragment/chords/track")
@pattern_edit
def chords_track(request: Request, form: StrForm, svc: Svc) -> Response:
    """The working loop's chord track — Perform's chord lane over the note
    grid (`Arrangement.chords`, degrees of the loop's key; the song's
    chord tiles are the model). `op`: the song's chord edits
    (`songops.CHORD_EDITS`: preset `prog`, roll `seed`, set `slot` `deg` /
    `q` / `inv`, edge `slot` `at`, split, add, remove `slot`, bars) on the
    loop's track — one bar of the key's first chord when it has none — or
    `clear` (none again). It renders nothing: no render key moves, the
    notes on the grid stay as they are; the generators on the loop
    (`gen/loop.harmony`) follow it from the next roll, the roll's chord
    tones and its chord-track stamp at once. One pattern undo step."""
    s: StudioState = svc.state
    a = s.arrangement
    op = str(form.get("op", ""))
    song = gl.song_of(a)
    try:
        if op == "clear":
            track = None
        elif op in songops.CHORD_EDITS:
            # (chord_args: the song strip's own reader of a chord edit's fields)
            from .song_start import chord_args

            track = songops.edit_chords(
                song, chordtrack.track_of(song), op, chord_args(form, op)
            )
            track = schema.conform(track, schema.nodes().CHORDS)
        else:
            return svc.toast("No such chord edit.")
    except sm.SongError as err:
        return svc.toast(str(err))
    if track == a.chords:
        return svc.toast("No change.")
    s.arrangement = replace(a, chords=track)
    resp = svc.do_render(request)
    if op not in ("roll", "preset", "clear"):
        return resp  # a tile's edit: the lane shows it
    said = chordtrack.label(song, track) if track else "none of its own"
    return with_toast(resp, f"The loop's chords: {said} — Ctrl+Z takes it back")


_GEN_KEY = "gen:notes:"
_GEN_LANE = ("cells", "cuts", "velocities", "slides")


def gen_notes(request: Request, form: StrForm, svc: Svc) -> Response:
    """`POST /fragment/gen` with `lane=notes` (routes/drums.py holds the
    route): a note generator on the working loop's grid (`gen/loop.py`).
    `kind` = one of `gen.loop.KINDS` (bass, rolling bass, chords, pad, arp,
    lead: written over what the grid shows, in the octaves it shows),
    `again` (the part last written, with a new seed), `busier` / `sparser`
    (`amount`). Seeded (`seed`, else a fresh one) and it plays.

    **One pattern undo step however often a part is rolled again** — a
    roll on the grid the same generator left joins that roll's step, as
    the beat's fills do; busier and sparser are a step each. A part needs
    two octaves of rows: from the six-row view the roll opens the
    two-octave one."""
    s: StudioState = svc.state
    kind = form.get("kind", "")
    last = s.beat_gen[0] if s.beat_gen else ""
    if kind == "again":
        if not last.startswith(_GEN_KEY):
            return svc.toast("Write a part first — there is nothing to roll again.")
        kind = last[len(_GEN_KEY) :]
    try:
        seed = int(form.get("seed", "") or random.randrange(limits.SEED_MODULO))
        amount = float(form.get("amount", "") or 0.25)
    except ValueError:
        return svc.toast("The seed and the amount are numbers.")
    seed %= limits.SEED_MODULO
    amount = min(1.0, max(0.05, amount)) if amount == amount else 0.25
    if s.follows(s.active_layer):
        return svc.toast("This lane follows the main one: “draw its own” first.")
    a = s.arrangement
    writes = kind in gl.LABELS
    span = max(_span(s, form), gl.ROWS_NEEDED) if writes else _span(s, form)
    rows = grid_rows(a, span, _oct(s, form))
    try:
        if writes:
            fields, note = gl.write(a, rows, kind, seed, s.song)
        else:
            fields, note = gl.vary(a, rows, kind, amount, seed, s.song)
    except gl.Refused as err:
        return svc.toast(str(err))
    now = {k: getattr(a, k) for k in _GEN_LANE}
    if fields == now:
        return svc.toast("No change.")
    key = _GEN_KEY + kind if writes else ""
    before = s.pattern_snapshot()
    again = bool(key) and s.gen_again(key, now)
    s.arrangement = replace(a, **fields)
    s.grid_span = span
    s.show_notes()  # the window on the notes, as any load onto the grid
    if not again:
        s.push_pattern_undo(before)
    if key:
        s.gen_done(key, {k: getattr(s.arrangement, k) for k in _GEN_LANE})
    resp = svc.do_render(request)
    # it plays: the loop starts if it was stopped (audio.js, `autoplay`)
    return with_toast(autoplay(resp), f"{note} — Ctrl+Z takes it back")


@router.post("/fragment/pattern/import")
@pattern_edit
def pattern_import(
    request: Request, upload: MidiUpload, form: StrForm, svc: Svc
) -> Response:
    """A .mid file into the note grid (`midi_bridge.to_grid`): the first
    track with notes that is not percussion — or the form's `track`, the
    n-th with notes — quantised to the grid's steps, moved by whole octaves
    to where most of it lands on the grid's rows, and clamped to its rows
    and bars; the toast says what was dropped. It replaces what the grid
    shows and keeps what it hides, and is one pattern undo step.

    The file is untrusted: the body is capped (`MAX_MIDI_UPLOAD_BYTES`,
    here and in `BodySizeLimit`), the reader bounds its tracks and events,
    and whatever it raises is a 422 toast with the grid untouched."""
    s: StudioState = svc.state
    a = s.arrangement
    if upload.too_large:
        return svc.toast("That MIDI file is too large.", status=413)
    if upload.data is None:
        return svc.toast("Choose a .mid file to import.")
    try:
        file = midifile.read(
            upload.data,
            max_tracks=limits.MAX_MIDI_TRACKS,
            max_events=limits.MAX_MIDI_EVENTS,
        )
    except Exception:
        # Exception, not MidiError alone: the reader checks every length,
        # but a rejected import must never be a 500 whatever a hostile file
        # finds to raise (the project import's rule, routes/export.py)
        log.warning("midi import failed", exc_info=True)
        return svc.toast("That isn't a MIDI file this grid can read.")
    raw = form.get("track", "")
    try:
        # short as well as digits: int() refuses a number past 4300 digits
        # with a plain ValueError, which was a 500
        if raw and not (raw.isascii() and raw.isdigit() and len(raw) <= 6):
            raise midi_bridge.ImportRefused("Track must be a number.")
        track = midi_bridge.pick_track(file, int(raw) if raw else None)
    except midi_bridge.ImportRefused as err:
        return svc.toast(str(err))
    # the file's bar: a time signature the studio has becomes the loop's
    # meter first (every grid fitted, `set_meter`); one it has not leaves
    # the loop's and says so. Its tempo is not taken: the grid is in beats
    sig = f"{file.numerator}/{file.denominator}"
    want = midi_bridge.file_meter(file)
    said, before = "", None
    if want and want != a.meter and not loop_refusal(a, replace(a, meter=want)):
        before = (s.arrangement, list(s.layers), dict(s.beat_vars))
        s.set_meter(want)
        said = f" The loop is in {_meter.parse(want).label} now, as the file."
    elif not want:
        said = (
            f" The file is in {sig}, which the studio has no grid for: kept {a.meter}."
        )
    a = s.arrangement
    try:
        lane, note = midi_bridge.to_grid(
            track,
            {k: getattr(a, k) for k in NOTE_LANE} | {"meter": a.meter},
            grid_rows(a, s.grid_span, s.grid_oct),
        )
    except midi_bridge.ImportRefused as err:
        if before:  # nothing placed: the meter goes back too
            s.arrangement, s.layers, s.beat_vars = before[0], before[1], before[2]
        return svc.toast(str(err))
    s.arrangement = replace(a, **lane)
    s.show_notes()
    return with_toast(svc.do_render(request), note + said)


@router.post("/fragment/pattern/drums/{op}")
@pattern_edit
def pattern_drums(request: Request, op: str, svc: Svc) -> Response:
    """The drum grid's whole-pattern tools: shift ◀ ▶, reverse, ×2, clear
    and the velocity lane's humanise / ramp / accent / reset."""
    s: StudioState = svc.state
    a = s.arrangement
    try:
        lane = drumops.drums_tool(
            op,
            drumops.loop_lane(a),  # its per-hit data and row swings ride along
            choices=limits.BAR_CHOICES,
            seed=random.randrange(1 << 30),
        )
    except KeyError:
        return Response(status_code=404)
    except po.ToolRefused as err:
        return svc.toast(str(err))
    s.arrangement = replace(a, **drumops.loop_fields(lane))
    s.active_slot = None  # the beat is no longer the slot's
    return _bank_response(svc, request)


@router.post("/fragment/pattern/undo")
def pattern_undo(request: Request, svc: Svc) -> Response:
    """Ctrl+Z on Perform and Drums: the step grids as they were before the
    last pattern edit (a stroke, a tool, a load of a beat)."""
    s: StudioState = svc.state
    why = s.pop_pattern_undo()
    if why:
        return svc.toast(why)
    if svc.view_of(request) == "drums":
        return _bank_response(svc, request)
    return svc.do_render(request)


@router.post("/fragment/grid/span")
def grid_span(request: Request, form: StrForm, svc: Svc) -> Response:
    """The note grid's rows: `span` 1 = six tones of the key from the root,
    2 = two octaves of the key, 3 = chromatic (24 semitones); `oct_by`
    +1 / -1 moves the octave window (the loop's `state.grid_oct` on
    Perform; on the Song page the open drawer's own, kept with the drawer).
    A view setting — nothing is changed, so nothing renders; the grid
    comes back by its watch."""
    s: StudioState = svc.state
    raw, by = form.get("span", ""), form.get("oct_by", "")
    if raw not in ("", "1", "2", "3") or by not in ("", "1", "-1") or not raw + by:
        return Response(status_code=422)
    if raw:
        s.grid_span = int(raw)
    if by and svc.view_of(request) == "song":
        d = svc._song_drawer() if s.song_drawer else None
        if d:
            octave = min(4, max(-2, d["oct"] + int(by)))
            s.song_drawer = {**s.song_drawer, "oct": octave}
    elif by:
        lo, hi = GRID_OCTAVES
        s.grid_oct = min(hi, max(lo, s.grid_oct + int(by)))
    s.save_soon()
    return Response(status_code=204)


@router.post("/fragment/meter")
@pattern_edit
def loop_meter(request: Request, form: StrForm, svc: Svc) -> Response:
    """The loop's meter (`meter.METERS`, the picker by the bpm on Perform
    and Drums): every grid it holds — the note grid, the beat, each layer,
    each variation of the beat — fitted bar by bar (`StudioState.
    set_meter`: a bar keeps its first beats, the step length kept). One
    pattern undo step; refused before anything moves when the longer bar
    would take the loop past its cap (`loop_refusal`)."""
    s: StudioState = svc.state
    to = form.get("meter", "")
    if to not in _meter.METERS:
        return svc.toast("That is not a meter the studio has.")
    a = s.arrangement
    if to == a.meter:
        return Response(status_code=204)
    refusal = loop_refusal(a, replace(a, meter=to))
    if refusal:
        return svc.toast(refusal, still_playing=True)
    lost = s.set_meter(to)
    name = _meter.parse(to).label
    note = f"The loop is in {name} now"
    if lost:
        what = "note or hit" if lost == 1 else "notes and hits"
        note += f" — {lost} {what} past the shorter bar gone (Ctrl+Z brings them back)"
    resp = (
        _bank_response(svc, request)
        if svc.view_of(request) == "drums"
        else svc.do_render(request)
    )
    return with_toast(resp, note + ".")
