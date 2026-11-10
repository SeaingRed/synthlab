"""Standard MIDI Files, written and read: notes, tempo and time signature,
no audio. Dependency-free on purpose (`struct` only) — the format is small
and a codec dependency would need a wheel on every machine this runs on.

Time is in **beats** (quarter notes) on both sides, so nothing here knows
about seconds: a file's tempo is carried as a fact (`MidiFile.bpm`), never
used to place a note.

`write` makes a format-1 file: track 0 holds the tempo and the time
signature — and, when the music changes either, every later change
(`MidiFile.tempos` / `.meters`: a song section in 6/8, a slower one, a
ramp as a tempo a beat) — then one MTrk per `Track` with its name, its notes (on / off
with velocity) and, when it has any, pitch-bend with the bend range set by
RPN 0. **Slides are not expressed**: a 303-style slide glides from the note
before it, which a MIDI channel can only say with a bend that would also
move every other note sounding on that channel — the studio exports a
slide as the plain note it starts (`studio/midi_bridge.py`). A drawn
`pitch_bend` curve is the wheel, and that maps one to one.

`read` takes any format 0/1/2 file and is written for hostile input:
every length is checked against the bytes that are there, track and event
counts are bounded, and whatever is wrong raises `MidiError` — never an
IndexError out of a slice. A track holding several channels (every
format-0 file) comes back as one `Track` per channel.
"""

from __future__ import annotations

import struct
from collections import deque
from dataclasses import dataclass, field

PPQ = 480  # ticks per quarter note: a 32-step bar's step is 60 ticks
DRUM_CHANNEL = 9  # "channel 10", counted from 0: General MIDI percussion
MAX_TRACKS = 64  # read(): chunks in one file
MAX_EVENTS = 200_000  # read(): events over the whole file

_NOTE_OFF, _NOTE_ON, _CONTROL, _BEND = 0x80, 0x90, 0xB0, 0xE0
_META, _SYSEX, _SYSEX_ESC = 0xFF, 0xF0, 0xF7
_END, _NAME, _TEMPO, _TIMESIG = 0x2F, 0x03, 0x51, 0x58
# data bytes after each channel status nibble
_DATA_BYTES = {0x8: 2, 0x9: 2, 0xA: 2, 0xB: 2, 0xC: 1, 0xD: 1, 0xE: 2}


class MidiError(ValueError):
    """The bytes are not a MIDI file `read` can use."""


@dataclass(frozen=True)
class Note:
    """One note: `start` and `length` in beats, `pitch` a MIDI number,
    `velocity` 1..127."""

    start: float
    length: float
    pitch: int
    velocity: int = 100


@dataclass
class Track:
    """One MTrk on one channel (0-15; `DRUM_CHANNEL` is percussion).
    `bends` are (beat, -1..1) wheel positions; `bend_range` (semitones) is
    what a full throw means, written as RPN 0 when set."""

    name: str = ""
    channel: int = 0
    notes: list[Note] = field(default_factory=list)
    bends: list[tuple[float, float]] = field(default_factory=list)
    bend_range: float | None = None


@dataclass
class MidiFile:
    bpm: float = 120.0
    tracks: list[Track] = field(default_factory=list)
    numerator: int = 4
    denominator: int = 4
    ppq: int = PPQ
    # what changes after beat 0: (beat, bpm) and (beat, numerator,
    # denominator), in time order. Empty for one tempo in one meter — the
    # file it always was
    tempos: list[tuple[float, float]] = field(default_factory=list)
    meters: list[tuple[float, int, int]] = field(default_factory=list)


# -- writing ---------------------------------------------------------------------


def _vlq(n: int) -> bytes:
    """A variable-length quantity: 7 bits a byte, high bit on all but the
    last."""
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([_META, kind]) + _vlq(len(data)) + data


def _chunk(events: list[tuple[int, int, bytes]]) -> bytes:
    """(tick, order, message) events as one MTrk: sorted by tick, then by
    `order` (a note-off before a note-on of the same tick), delta-timed,
    with the end-of-track meta."""
    body = bytearray()
    now = 0
    for tick, _order, message in sorted(events, key=lambda e: (e[0], e[1])):
        body += _vlq(tick - now) + message
        now = tick
    body += _vlq(0) + _meta(_END, b"")
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def note_ticks(notes: list[Note], ppq: int = PPQ) -> list[tuple[int, int, int, int]]:
    """`notes` as (on, off, pitch, velocity) in ticks, as `write` places
    them. A note is at least a tick long, and one still sounding when its
    own pitch starts again ends there — two note-ons of a pitch on one
    channel with no note-off between is what a tiled pattern's held note
    would otherwise write at every repeat. A second note on the same pitch
    and tick is dropped."""
    out: list[list[int]] = []
    last: dict[int, int] = {}  # pitch -> its newest note in `out`
    for n in sorted(notes, key=lambda n: (n.start, n.pitch)):
        if not 0 <= n.pitch <= 127:
            continue
        on = max(0, round(n.start * ppq))
        off = max(on + 1, round((n.start + n.length) * ppq))
        prev = last.get(n.pitch)
        if prev is not None and out[prev][1] > on:
            if out[prev][0] == on:
                continue
            out[prev][1] = on
        last[n.pitch] = len(out)
        out.append([on, off, n.pitch, min(127, max(1, int(n.velocity)))])
    return [(a, b, c, d) for a, b, c, d in out]


def _bend_range(channel: int, semitones: float) -> list[bytes]:
    """RPN 0 (pitch-bend sensitivity) = `semitones`, then the null RPN so
    a later data-entry message changes nothing."""
    whole = min(127, max(0, int(semitones)))
    cents = min(99, max(0, round((semitones - whole) * 100)))
    cc = _CONTROL | channel
    pairs = ((101, 0), (100, 0), (6, whole), (38, cents), (101, 127), (100, 127))
    return [bytes([cc, a, b]) for a, b in pairs]


def _track_chunk(track: Track, ppq: int) -> bytes:
    ch = track.channel & 0x0F
    events: list[tuple[int, int, bytes]] = []
    if track.name:
        events.append((0, 0, _meta(_NAME, track.name.encode("utf-8")[:127])))
    if track.bends and track.bend_range is not None:
        events += [(0, 0, m) for m in _bend_range(ch, track.bend_range)]
    for on, off, pitch, vel in note_ticks(track.notes, ppq):
        events.append((off, 1, bytes([_NOTE_OFF | ch, pitch, 0])))
        events.append((on, 3, bytes([_NOTE_ON | ch, pitch, vel])))
    for beat, value in track.bends:
        v = min(16383, max(0, 8192 + round(min(1.0, max(-1.0, value)) * 8191)))
        events.append(
            (max(0, round(beat * ppq)), 2, bytes([_BEND | ch, v & 0x7F, v >> 7]))
        )
    return _chunk(events)


def write(mf: MidiFile) -> bytes:
    """`mf` as a format-1 Standard MIDI File."""
    if not 1 <= mf.ppq <= 0x7FFF:
        raise ValueError("ppq out of range")

    def tempo(bpm: float) -> bytes:
        us = min(0xFFFFFF, max(1, round(60_000_000 / bpm)))
        return _meta(_TEMPO, us.to_bytes(3, "big"))

    def timesig(num: int, den: int) -> bytes:
        log2 = max(0, int(den).bit_length() - 1)
        return _meta(_TIMESIG, bytes([int(num) & 0x7F, log2, 24, 8]))

    events = [
        (0, 0, timesig(mf.numerator, mf.denominator)),
        (0, 0, tempo(mf.bpm)),
    ]
    events += [
        (max(0, round(beat * mf.ppq)), 0, timesig(num, den))
        for beat, num, den in mf.meters
    ]
    events += [(max(0, round(beat * mf.ppq)), 1, tempo(bpm)) for beat, bpm in mf.tempos]
    conductor = _chunk(events)
    chunks = [conductor, *(_track_chunk(t, mf.ppq) for t in mf.tracks)]
    return b"MThd" + struct.pack(">IHHH", 6, 1, len(chunks), mf.ppq) + b"".join(chunks)


# -- reading ---------------------------------------------------------------------


class _Reader:
    """A cursor over bytes whose every read is checked against the end."""

    def __init__(self, data: bytes, pos: int = 0, end: int | None = None):
        self.data, self.pos = data, pos
        self.end = len(data) if end is None else end

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > self.end:
            raise MidiError("truncated file")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def byte(self) -> int:
        return self.take(1)[0]

    def vlq(self) -> int:
        n = 0
        for _ in range(4):  # the format allows four bytes, no more
            b = self.byte()
            n = (n << 7) | (b & 0x7F)
            if not b & 0x80:
                return n
        raise MidiError("bad variable-length quantity")


class _Parse:
    """What a file's tracks add up to as they are read."""

    def __init__(self, ppq: int, max_events: int):
        self.ppq, self.left = ppq, max_events
        self.tempo: int | None = None
        self.timesig: tuple[int, int] | None = None
        self.tracks: list[Track] = []
        # every change after tick 0: (tick, us a beat), (tick, num, den)
        self.tempos: list[tuple[int, int]] = []
        self.timesigs: list[tuple[int, int, int]] = []

    def event(self) -> None:
        self.left -= 1
        if self.left < 0:
            raise MidiError("too many events")

    def track(self, r: _Reader) -> None:
        name = ""
        tick = 0
        status = 0
        # a deque: taking the first of a list is linear, and a file of
        # 200,000 note-ons of one key that are never released (inside every
        # cap) took seconds to close, under the studio lock
        open_notes: dict[tuple[int, int], deque[tuple[int, int]]] = {}
        notes: dict[int, list[Note]] = {}
        bends: dict[int, list[tuple[float, float]]] = {}

        def close(ch: int, pitch: int) -> None:
            stack = open_notes.get((ch, pitch))
            if stack:
                on, vel = stack.popleft()  # first on, first off
                length = max(tick - on, 1) / self.ppq
                notes.setdefault(ch, []).append(Note(on / self.ppq, length, pitch, vel))

        while r.pos < r.end:
            tick += r.vlq()
            self.event()
            b = r.byte()
            if b == _META:
                kind, data = r.byte(), r.take(r.vlq())
                if kind == _END:
                    break
                if kind == _NAME and not name:
                    name = data.decode("utf-8", "replace")[:127]
                elif kind == _TEMPO and len(data) == 3:
                    if self.tempo is None:
                        self.tempo = int.from_bytes(data, "big")
                    if tick > 0 and int.from_bytes(data, "big"):
                        self.tempos.append((tick, int.from_bytes(data, "big")))
                elif kind == _TIMESIG and len(data) >= 2:
                    sig = (data[0], 1 << min(data[1], 7))
                    if self.timesig is None:
                        self.timesig = sig
                    if tick > 0 and sig[0]:
                        self.timesigs.append((tick, *sig))
                continue
            if b in (_SYSEX, _SYSEX_ESC):
                r.take(r.vlq())
                continue
            if b & 0x80:
                if b >= 0xF0:
                    raise MidiError("unexpected system message")
                status, first = b, r.byte()
            elif status:
                first = b  # running status: the last channel status again
            else:
                raise MidiError("data byte with no status")
            kind, ch = status >> 4, status & 0x0F
            second = r.byte() if _DATA_BYTES[kind] == 2 else 0
            if first & 0x80 or second & 0x80:
                raise MidiError("bad data byte")
            if kind == 0x9 and second:
                open_notes.setdefault((ch, first), deque()).append((tick, second))
            elif kind in (0x8, 0x9):  # a note-on at velocity 0 is a note-off
                close(ch, first)
            elif kind == 0xE:
                value = ((second << 7) | first) - 8192
                bends.setdefault(ch, []).append(
                    (tick / self.ppq, max(-1.0, value / 8191))
                )
        for ch, pitch in list(open_notes):  # never released: ends with the track
            while open_notes[(ch, pitch)]:
                close(ch, pitch)
        for ch in sorted(set(notes) | set(bends)):
            self.tracks.append(
                Track(
                    name=name,
                    channel=ch,
                    notes=sorted(notes.get(ch, []), key=lambda n: (n.start, n.pitch)),
                    bends=bends.get(ch, []),
                )
            )


def read(
    data: bytes, *, max_tracks: int = MAX_TRACKS, max_events: int = MAX_EVENTS
) -> MidiFile:
    """A Standard MIDI File as a `MidiFile`: one `Track` per (MTrk,
    channel) that holds a note or a bend, in file order; the first tempo
    and time signature found (120 bpm, 4/4 when there is none), and every
    change of either after the first beat (`tempos`, `meters`). Raises
    `MidiError` for anything else: not a MIDI file, SMPTE time (frames,
    not beats), a length that runs past the bytes, more than `max_tracks`
    chunks or `max_events` events."""
    r = _Reader(bytes(data))
    if r.take(4) != b"MThd":
        raise MidiError("not a MIDI file")
    size = struct.unpack(">I", r.take(4))[0]
    header = _Reader(r.data, r.pos, r.pos + size)
    r.take(size)
    fmt, count, division = struct.unpack(">HHH", header.take(6))
    if fmt > 2:
        raise MidiError("unknown MIDI format")
    if division & 0x8000:
        raise MidiError("SMPTE-timed files are not supported")
    if division == 0:
        raise MidiError("bad time division")
    if count > max_tracks:
        raise MidiError("too many tracks")
    parse = _Parse(division, max_events)
    chunks = 0
    while r.pos < r.end:
        kind = r.take(4)
        size = struct.unpack(">I", r.take(4))[0]
        body = _Reader(r.data, r.pos, r.pos + size)
        r.take(size)
        chunks += 1
        if chunks > max_tracks:
            raise MidiError("too many tracks")
        if kind == b"MTrk":  # any other chunk type is skipped, as the spec asks
            parse.track(body)
    num, den = parse.timesig or (4, 4)
    tempo = parse.tempo or 500_000
    return MidiFile(
        bpm=60_000_000 / tempo,
        tracks=parse.tracks,
        numerator=num or 4,
        denominator=den,
        ppq=division,
        tempos=[(t / division, 60_000_000 / us) for t, us in sorted(parse.tempos)],
        meters=[(t / division, n, d) for t, n, d in sorted(parse.timesigs)],
    )
