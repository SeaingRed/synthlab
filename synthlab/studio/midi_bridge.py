"""The loop and the song as Standard MIDI Files, and a MIDI file into the
note grid (`synthlab.midifile` is the format; this is what goes in it).

**Export is the schedule, not a second reading of the grid.** A synth
lane's notes are `arrange.note_schedule` — the playing steps, the held
runs, cuts, swing, velocities and arp `build_song` renders — held for
`arrange.NOTE_GATE` of their length, and the beat is `arrange.drum_hits`,
the hits `_schedule_drums` places (one seeded probability / humanise roll
for both). So a note hidden past a shrunk grid, a muted lane or a hit a
solo silences is in neither the audio nor the file.

What MIDI cannot say is left out, deliberately:
- a **slide** is exported as the plain note it starts (`midifile`'s
  docstring says why);
- a drum hit is a short note on channel 10 at its General MIDI key
  (`GM_DRUMS`); the open hat's choke is the receiving kit's business;
- audio tracks, takes and FX have no notes.
A drawn `pitch_bend` curve on the main lane is the wheel and is written as
pitch-bend, with the patch's `bend_range` as RPN 0.

**Import** (`to_grid`) quantises one track onto the grid that is on screen
and replaces what that grid shows, keeping what it hides — the rule every
pattern tool follows (`patternops`).
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import midifile as mf
from ..sequencer import midi as midi_number
from ..sequencer import name_of
from . import meter as _meter
from . import patternops as po
from . import song_model as sm
from . import song_play
from .arrange import NOTE_GATE, bar_beats, drum_hits, loop_bars, note_schedule
from .curves import curve_array

# General MIDI percussion keys for the drum machine's voices (state.DRUM_ROWS)
GM_DRUMS = {
    "kick": 36,
    "rim": 37,
    "snare": 38,
    "clap": 39,
    "hat": 42,
    "tom": 45,
    "ohat": 46,
    "crash": 49,
}
DRUM_NOTE_BEATS = 0.25  # a hit is a one-shot: the note is only a trigger
BEND_POINTS_PER_BEAT = 24  # how finely a drawn bend curve is sampled


def _velocity(v: float) -> int:
    return min(127, max(1, round(float(v) * 127)))


def _lane_notes(events: list[tuple], offset: float) -> list[mf.Note]:
    """`note_schedule` events as notes, `offset` beats on. A pitch outside
    MIDI's 0..127 has no key and is left out."""
    out = []
    for start, dur, pitch, vel, _slide in events:
        number = midi_number(pitch)
        if 0 <= number <= 127:
            out.append(mf.Note(offset + start, dur * NOTE_GATE, number, _velocity(vel)))
    return out


def _drum_notes(arr, offset: float) -> list[mf.Note]:
    """One pass of `arr`'s beat (`drum_hits`), `offset` beats on. Velocity
    is the hit's gain (level x the hit's or the step's velocity x
    humanise), full scale at 1. Per-hit data is in the file as the audio
    has it: a ratchet is that many notes inside the step, each ended where
    the next begins; a nudge is the note's own time (a hit nudged to before
    the first beat is at the pass's end, where it sounds); a chance is the
    ROLLED result — the hits this pattern's seed plays, not the odds. A
    hit's tune has no place on a General MIDI drum key and is left out."""
    step_beats = bar_beats(arr) / max(1, int(arr.drum_steps))
    length = min(DRUM_NOTE_BEATS, step_beats)
    return [
        mf.Note(
            offset + h[0],
            length if h.until is None else min(length, h.until - h[0]),
            GM_DRUMS[voice],
            _velocity(h[1]),
        )
        for voice, hits in drum_hits(arr)
        for h in hits
        if h[1] > 0
    ]


def _bends(points, lane_beats: float, offset: float) -> list[tuple[float, float]]:
    """A drawn `pitch_bend` curve over one pass of its lane as (beat, -1..1)
    wheel positions: sampled `BEND_POINTS_PER_BEAT` to the beat, a position
    written only when it moves."""
    n = max(2, round(lane_beats * BEND_POINTS_PER_BEAT) + 1)
    values = curve_array(points or [], "pitch_bend", n)
    if values is None:
        return []
    out: list[tuple[float, float]] = []
    for i, v in enumerate(values[:-1]):  # the last sample is the next pass's 0
        if not out or round(out[-1][1] * 8191) != round(float(v) * 8191):
            out.append((offset + i * lane_beats / (n - 1), float(v)))
    return out


def _channels():
    """Channels for synth tracks, in order: every one but percussion's,
    round again past fifteen tracks."""
    while True:
        yield from (c for c in range(16) if c != mf.DRUM_CHANNEL)


@dataclass
class _Cycle:
    """One cycle of a loop or a section, as the renderer is handed it."""

    arr: object
    layers: tuple = ()
    main_muted: bool = False
    breaks: tuple = ()
    arp: tuple = ()
    curves: dict | None = None
    bars: int = 1  # the cycle's length: every lane tiles up to it
    parts: tuple = ("synth", "drums")


def _synth_lanes(c: _Cycle, offset: float) -> list[tuple[list, list]]:
    """(notes, bends) per synth lane of one cycle at `offset` beats — the
    main lane first, then each layer. A lane shorter than the cycle repeats
    inside it, as the renderer tiles it; bends are the main lane's."""
    if "synth" not in c.parts:
        return []
    lane_beats = max(1, int(c.arr.bars)) * bar_beats(c.arr)
    reps = max(1, c.bars // max(1, int(c.arr.bars)))
    lanes = note_schedule(c.arr, list(c.layers), c.main_muted, c.breaks, c.arp)
    out = []
    for i, events in enumerate(lanes):
        notes: list[mf.Note] = []
        bends: list[tuple[float, float]] = []
        for r in range(reps):
            at = offset + r * lane_beats
            notes += _lane_notes(events, at)
            if i == 0 and not c.main_muted and c.curves:
                bends += _bends(c.curves.get("pitch_bend"), lane_beats, at)
        out.append((notes, bends))
    return out


def loop_file(inp) -> mf.MidiFile:
    """A loop's `RenderInputs` (`StudioService.render_inputs`) as a MIDI
    file: one track per synth lane that plays a note — the edited lane,
    then each other layer — and the beat on channel 10, each tiled over
    `loop_bars` exactly as the loop's stems are. Only the parts the loop
    carries are written."""
    arr = inp.arr
    cycle = _Cycle(
        arr=arr,
        layers=tuple(inp.layers or ()),
        main_muted=inp.main_muted,
        breaks=tuple(inp.breaks or ()),
        arp=tuple(inp.arp or ()),
        curves=inp.curves,
        bars=loop_bars(arr, tuple(p for p in inp.parts if p in ("synth", "drums"))),
        parts=tuple(inp.parts),
    )
    num, den = _signature(arr.meter)
    out = mf.MidiFile(bpm=float(arr.bpm), numerator=num, denominator=den)
    channels = _channels()
    names = [inp.patch.name, *(lay["patch"].get("name", "") for lay in cycle.layers)]
    # no synth part: no lanes, and the names go unused
    for (notes, bends), name in zip(_synth_lanes(cycle, 0.0), names, strict=False):
        channel = next(channels)
        if notes:
            out.tracks.append(
                mf.Track(
                    name=str(name or "synth"),
                    channel=channel,
                    notes=notes,
                    bends=bends,
                    bend_range=float(inp.patch.bend_range) if bends else None,
                )
            )
    if "drums" in cycle.parts:
        drum_bars = max(1, int(arr.drum_bars))
        notes = [
            n
            for r in range(max(1, cycle.bars // drum_bars))
            for n in _drum_notes(arr, r * drum_bars * bar_beats(arr))
        ]
        if notes:
            out.tracks.append(
                mf.Track(name="drums", channel=mf.DRUM_CHANNEL, notes=notes)
            )
    return out


def _signature(name) -> tuple[int, int]:
    m = _meter.parse(name)
    return m.num, m.den


def _song_clock(song: dict) -> dict:
    """The song's tempo map and time signatures as `MidiFile` takes them:
    the first section's tempo and meter, and each change after it — a
    section in another meter, at another tempo, and a ramp written as a
    tempo a beat (each beat at the tempo half-way through it, so the
    file's beats last what the render's do, to well under a millisecond a
    beat). A 4/4 song at one tempo has none: the file it always was."""
    secs = song["sections"]
    first = secs[0] if secs else None
    bpm = sm.section_bpm(song, first)
    sig = _signature(sm.section_meter(song, first))
    tempos: list[tuple[float, float]] = []
    meters: list[tuple[float, int, int]] = []
    now_bpm, now_sig, at = bpm, sig, 0.0
    for sec in secs:
        clock = sm.section_clock(song, sec)
        s = _signature(sm.section_meter(song, sec))
        if s != now_sig:
            meters.append((at, *s))
            now_sig = s
        bars = int(sec["bars"])
        for _rep in range(int(sec["repeats"])):
            if clock.ramp:
                beats = int(round(bars * clock.bar_beats))
                for k in range(beats):
                    t = round(clock.tempo(k + 0.5), 3)
                    if t != now_bpm:
                        tempos.append((at + k, t))
                        now_bpm = t
            elif clock.bpm != now_bpm:
                tempos.append((at, clock.bpm))
                now_bpm = clock.bpm
            at += bars * clock.bar_beats
    return {
        "bpm": bpm,
        "numerator": sig[0],
        "denominator": sig[1],
        "tempos": tempos,
        "meters": meters,
    }


def _section_drums(parts, offset: float) -> list[mf.Note]:
    """One cycle of a section's beat. A cycle the renderer cuts into chunks
    (a flat one, or one past the loop cap: `render.section_windows`) rolls
    its probability and humanise per chunk, so the hits are read chunk by
    chunk here too; any other cycle is its beat tiled."""
    from .render import section_windows

    windows = section_windows(parts)
    if parts.flat or len(windows) > 1:
        return [
            n
            for b0, b1 in windows
            for n in _drum_notes(
                parts.window(b0, b1).arr, offset + b0 * bar_beats(parts.arr)
            )
        ]
    drum_bars = max(1, int(parts.arr.drum_bars))
    return [
        n
        for r in range(max(1, parts.cycle_bars // drum_bars))
        for n in _drum_notes(parts.arr, offset + r * drum_bars * bar_beats(parts.arr))
    ]


def song_file(song: dict) -> mf.MidiFile:
    """The song as a MIDI file: one track per sounding drums / synth track
    (`song_play.stem_songs`: the tracks the stems export writes, audio
    tracks left out), named as the song names it, its sections laid end to
    end with their repeats. Each track is read the way its stem is
    rendered — the song with every other track muted, through
    `section_arrangement`, one cycle per pass of `section_timeline` — so a
    section's cycle, tiling and repeat attacks are the render's own."""
    out = mf.MidiFile(**_song_clock(song))
    channels = _channels()
    for _entry, solo in song_play.stem_songs(song):
        track = next(t for t in solo["tracks"] if not t.get("mute"))
        if track["kind"] == "audio":
            continue
        drums = track["kind"] == "drums"
        notes: list[mf.Note] = []
        bends: list[tuple[float, float]] = []
        bend_range = None
        at = 0.0  # the section's start, in beats
        for section in solo["sections"]:
            parts = sm.section_arrangement(solo, section)
            cycle_beats = parts.cycle_bars * bar_beats(parts.arr)
            for i in range(len(sm.section_timeline(solo, section))):
                offset = at + i * cycle_beats
                if drums:
                    notes += _section_drums(parts, offset)
                    continue
                # pass i rolls its chance afresh, as its render does
                one = sm.repass(parts, i)
                cycle = _Cycle(
                    arr=one.arr,
                    layers=one.layers,
                    main_muted=one.main_muted,
                    breaks=one.breaks,
                    arp=one.arp,
                    curves=one.curves,
                    bars=one.cycle_bars,
                    parts=("synth",),
                )
                for lane_notes, lane_bends in _synth_lanes(cycle, offset):
                    notes += lane_notes
                    bends += lane_bends
                if not parts.main_muted and bend_range is None:
                    bend_range = float(parts.main_patch.get("bend_range", 2.0))
            at += (
                float(section["bars"]) * int(section["repeats"]) * bar_beats(parts.arr)
            )
        if notes:
            out.tracks.append(
                mf.Track(
                    name=str(track.get("name") or track["kind"]),
                    channel=mf.DRUM_CHANNEL if drums else next(channels),
                    notes=notes,
                    bends=bends,
                    bend_range=bend_range if bends else None,
                )
            )
    return out


# -- import ----------------------------------------------------------------------

OCTAVE_REACH = 5  # how far an import may move a track to meet the grid's rows


class ImportRefused(ValueError):
    """Nothing in the file can go on this grid; the message is the toast."""


def pick_track(file: mf.MidiFile, track: int | None = None) -> mf.Track:
    """The track to import: the `track`-th with notes (from 1), else the
    first that is not percussion, else the first."""
    tracks = [t for t in file.tracks if t.notes]
    if not tracks:
        raise ImportRefused("That MIDI file has no notes.")
    if track is not None:
        if not 1 <= track <= len(tracks):
            raise ImportRefused(
                f"That MIDI file has {len(tracks)} track"
                f"{'' if len(tracks) == 1 else 's'} with notes."
            )
        return tracks[track - 1]
    melodic = [t for t in tracks if t.channel != mf.DRUM_CHANNEL]
    return (melodic or tracks)[0]


def _best_octave(notes, rows: set[str]) -> int:
    """The whole-octave move that lands most notes on the grid's rows —
    the nearest one on a tie. A file written around C4 would otherwise put
    nothing on a grid rooted at A1."""

    def hits(octave: int) -> int:
        return sum(
            0 <= (m := n.pitch + 12 * octave) <= 127 and name_of(m) in rows
            for n in notes
        )

    reach = sorted(range(-OCTAVE_REACH, OCTAVE_REACH + 1), key=lambda o: (abs(o), o))
    return max(reach, key=hits)  # max keeps the first of equals: the nearest


def file_meter(file: mf.MidiFile) -> str | None:
    """The file's first time signature as one of the studio's meters
    (`meter.METERS`; 7/8 as 2+2+3), or None for one it does not have."""
    name = f"{int(file.numerator)}/{int(file.denominator)}"
    return name if name in _meter.METERS else None


def to_grid(track: mf.Track, lane: dict, rows: list[str]) -> tuple[dict, str]:
    """`track`'s notes onto the note grid, as (the lane's new fields, a
    note saying what happened). `lane`: the grid's `bars`, `steps_per_bar`,
    `cells`, `cuts`, `velocities`, `slides`; `rows`: the pitches it shows.

    Each note starts on its nearest step and is held for its length in
    whole steps (at least one, cut at the grid's end); one starting where
    its row is already sounding is a cut. Its velocity goes on its start
    step — the lane is one per step, so the loudest of a chord wins. The
    track is moved by whole octaves to where most of it fits
    (`_best_octave`). Notes off the grid's rows and notes past its last
    bar are dropped and counted in the note. The import replaces what the
    grid shows and keeps what it hides; with nothing to place it raises
    `ImportRefused` and changes nothing."""
    spb = int(lane["steps_per_bar"])
    total = int(lane["bars"]) * spb
    # a bar of the grid's meter (`lane["meter"]`, 4/4 when it has none)
    step_beats = _meter.bar_beats(lane.get("meter", "4/4")) / spb
    shown = set(rows)
    octave = _best_octave(track.notes, shown)
    cells: set[tuple[str, int]] = set()
    cuts: set[tuple[str, int]] = set()
    starts: set[tuple[str, int]] = set()
    vel: dict[int, float] = {}
    off_rows = past_end = 0
    for n in sorted(track.notes, key=lambda n: (n.start, n.pitch)):
        number = n.pitch + 12 * octave
        pitch = name_of(number) if 0 <= number <= 127 else None
        if pitch not in shown:
            off_rows += 1
            continue
        start = round(n.start / step_beats)
        if start >= total:
            past_end += 1
            continue
        if (pitch, start) in starts:
            continue  # the same note twice: one cell
        if (pitch, start) in cells or (pitch, start - 1) in cells:
            cuts.add((pitch, start))
        starts.add((pitch, start))
        end = min(total, start + max(1, round(n.length / step_beats)))
        cells |= {(pitch, s) for s in range(start, end)}
        vel[start] = max(vel.get(start, 0.0), round(n.velocity / 127, 3))
    if not starts:
        raise ImportRefused(_refusal(off_rows, past_end, int(lane["bars"])))

    def hidden(step) -> bool:
        return not 0 <= int(step) < total

    _inside, kept = po._split(lane["cells"], total, shown)
    _inside, kept_cuts = po._split(lane["cuts"], total, shown)
    out = {
        "cells": po._join(cells, kept),
        "cuts": po._join(cuts, kept_cuts),
        "velocities": {
            **{k: v for k, v in lane["velocities"].items() if hidden(po._step(k))},
            **{str(s): v for s, v in sorted(vel.items())},
        },
        "slides": sorted(int(s) for s in lane["slides"] if hidden(s)),
    }
    name = f" from “{track.name}”" if track.name else ""
    said = [f"Imported {len(starts)} note{'' if len(starts) == 1 else 's'}{name}"]
    if octave:
        way = "up" if octave > 0 else "down"
        said.append(
            f"moved {way} {abs(octave)} octave{'' if abs(octave) == 1 else 's'}"
        )
    dropped = _dropped(off_rows, past_end, int(lane["bars"]))
    if dropped:
        said.append("dropped " + dropped)
    return out, " · ".join(said) + "."


def _dropped(off_rows: int, past_end: int, bars: int) -> str:
    bits = []
    if off_rows:
        bits.append(f"{off_rows} off the grid's rows")
    if past_end:
        bits.append(f"{past_end} past bar {bars}")
    return ", ".join(bits)


def _refusal(off_rows: int, past_end: int, bars: int) -> str:
    return (
        "No note in that MIDI file fits this grid "
        f"({_dropped(off_rows, past_end, bars)}). Try the two-octave range, "
        "another key, or more bars."
    )
