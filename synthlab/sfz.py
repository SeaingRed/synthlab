"""SFZ import: a `.zip` holding an `.sfz` and its samples -> sampler zones.

Pure (no studio imports): `load(data, save, limits)` reads the archive,
parses the instrument and hands each sample's audio to `save(audio, sr) ->
id` (the studio passes `sampler.save` on its sample directory), returning
an `Instrument` whose `zones` the studio puts on a sampler sound
(`sampler.format_zones`). The input is an untrusted archive, so nothing in
here ever extracts to disk: a member is read by name from the zip, every
size, count and depth is bounded by `Limits`, and anything wrong is a
`SfzError` (one type, always a sentence the user can read).

What is read, and how it maps (`docs/engine.md`, "Zones"; the studio's
`sampler-and-clips.md`, "Importing an SFZ instrument"):

  * headers `<control>` (`default_path`, `octave_offset`, `note_offset`),
    `<global>`, `<master>`, `<group>`, `<region>` — a lower header
    inherits the opcodes of the ones above it, a new higher header ends
    the lower ones. `<curve>`, `<effect>`, `<midi>` and the rest are
    listed, not read;
  * `#define $VAR value` and `$VAR` substitution, `#include "file"` — only
    files of the archive, to a depth and a count, no cycles; comments
    (`//`, `/* */`);
  * `sample` (WAV / FLAC / OGG / anything libsndfile decodes, by content,
    never by extension), `lokey` / `hikey` / `key` / `pitch_keycenter`
    (numbers or note names, `c#4` = 61, both octave conventions through
    `octave_offset` / `note_offset`), `lovel` / `hivel`, `tune` (cents;
    past a semitone the root moves instead), `transpose`, `volume` (dB)
    and `amplitude` (percent), `offset` / `end` (frames), `loop_mode`
    `loop_start` / `loop_end` (else the WAV's own loop), `seq_length` /
    `seq_position` (the take), `trigger` (`attack` only: a release trigger
    is a region left out), and the first region's `ampeg_attack` /
    `decay` / `sustain` / `release` (the sound's amplitude envelope);
  * everything else is **counted by opcode and reported**, never dropped
    silently (`Instrument.unsupported`, `Instrument.skipped`,
    `Instrument.notes`, `summary`).
"""

from __future__ import annotations

import io
import math
import posixpath
import re
import struct
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from . import sampler


class SfzError(ValueError):
    """Not an SFZ archive this importer will read; the message says why."""


@dataclass(frozen=True)
class Limits:
    """Every bound of the importer (the studio's values are in
    `studio/limits.py`, `MAX_SFZ_*`)."""

    members: int = 1024  # entries in the archive
    uncompressed: int = 256_000_000  # all entries, as they declare themselves
    member_bytes: int = 48_000_000  # one entry
    ratio: int = 100  # declared / compressed, for an entry over `ratio_floor`
    ratio_floor: int = 1 << 20
    text_bytes: int = 1_000_000  # one .sfz or include
    lines: int = 60_000  # all files together
    include_depth: int = 8
    includes: int = 64  # files read as text, the .sfz included
    defines: int = 512
    define_chars: int = 4096  # one variable's value, expanded
    line_chars: int = 16_384  # one line, once substituted
    expanded: int = 4_000_000  # the whole text, once substituted
    headers: int = 20_000
    regions: int = 4096
    audio_seconds: float = 1200.0  # all samples decoded, as recorded
    min_sr: int = 8_000
    max_sr: int = 192_000
    max_channels: int = 8


# --- the archive ----------------------------------------------------------------

_DRIVE = re.compile(r"[A-Za-z]:")


def _show(text: str, n: int = 60) -> str:
    """A hostile name or value, made safe to put in a sentence."""
    t = "".join(c if c.isprintable() else "?" for c in str(text))
    return t if len(t) <= n else t[: n - 1] + "…"


def clean_member(name: str) -> str:
    """The archive's name for an entry, or a `SfzError`: forward slashes
    only, relative, no `..`, no empty segment, no control character, no
    drive letter. (A name is only ever used as a key into the archive;
    this is for a hostile archive's sake all the same, and so that the
    answer does not depend on the platform's separator.)"""
    bad = (
        not name
        or len(name) > 255
        or "\\" in name
        or name.startswith("/")
        or _DRIVE.match(name)
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    )
    parts = name.rstrip("/").split("/")
    if bad or any(p in ("", "..", ".") for p in parts):
        raise SfzError(f"The zip holds a path it will not read: {_show(name)!r}.")
    return name


def _check_directory(data: bytes, members: int) -> None:
    """Walk the zip's central directory ourselves, with a running count,
    before `zipfile` builds its table from it: the end record's entry count
    is only a claim (a forged one, or a ZIP64 record, would pass a check of
    that number alone while the directory is huge). The directory is found
    where `zipfile` finds it — its size from the end record (ZIP64's when
    the plain one says 0xFFFF / 0xFFFFFFFF), ending where that record
    starts — and refused if it is larger than the upload, has more records
    than `members`, or does not end exactly at its own end."""
    tail_at = max(0, len(data) - (65_535 + 22))
    at = data.rfind(b"PK\x05\x06", tail_at)
    if at < 0 or len(data) < at + 22:
        raise SfzError("That is not a zip file.")
    entries, cd_size = (
        struct.unpack_from("<H", data, at + 10)[0],
        struct.unpack_from("<I", data, at + 12)[0],
    )
    end = at
    zip64 = data[at - 20 : at - 16] == b"PK\x06\x07"  # a ZIP64 locator before it
    if entries == 0xFFFF or cd_size == 0xFFFFFFFF or zip64:
        loc = at - 20
        if loc < 0 or data[loc : loc + 4] != b"PK\x06\x07":
            raise SfzError("That zip's directory is inconsistent.")
        # where `zipfile` reads the record: 56 bytes before the locator, the
        # locator's own offset ignored. Ours must be the same place, or a
        # decoy at the offset it names would be checked while another is used
        rec = loc - 56
        named = struct.unpack_from("<Q", data, loc + 8)[0]
        if rec < 0 or named != rec or data[rec : rec + 4] != b"PK\x06\x06":
            raise SfzError("That zip's directory is inconsistent.")
        entries, cd_size = struct.unpack_from("<QQ", data, rec + 32)
        end = rec
    if entries > members:
        raise SfzError(f"The zip holds more than {members} files.")
    if cd_size > end:
        raise SfzError("That zip's directory is inconsistent.")
    pos, stop, count = end - cd_size, end, 0
    while pos < stop:
        if data[pos : pos + 4] != b"PK\x01\x02" or pos + 46 > stop:
            raise SfzError("That zip's directory is inconsistent.")
        count += 1
        if count > members:
            raise SfzError(f"The zip holds more than {members} files.")
        n, m, k = struct.unpack_from("<HHH", data, pos + 28)
        pos += 46 + n + m + k
    if pos != stop:
        raise SfzError("That zip's directory is inconsistent.")


class Archive:
    """A validated zip held in memory. `read(path)` is the only way in."""

    def __init__(self, data: bytes, lim: Limits):
        self.lim = lim
        if data[:4] != b"PK\x03\x04":
            raise SfzError(
                "Send a .zip holding the .sfz and its samples "
                "(a bare .sfz has no audio with it)."
            )
        _check_directory(data, lim.members)
        try:
            self._z = zipfile.ZipFile(io.BytesIO(data))
            infos = self._z.infolist()
        except (zipfile.BadZipFile, OSError, ValueError, EOFError) as err:
            raise SfzError("That is not a zip file.") from err
        if len(infos) > lim.members:
            raise SfzError(f"The zip holds more than {lim.members} files.")
        self.members: dict[str, zipfile.ZipInfo] = {}
        self._fold: dict[str, str] = {}
        total = 0
        for info in infos:
            name = clean_member(info.filename)
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise SfzError("The zip is password protected.")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise SfzError(
                    "The zip uses a compression this importer does not read."
                )
            if info.file_size > lim.member_bytes:
                raise SfzError(f"{_show(name)!r} in the zip is too large.")
            total += info.file_size
            if total > lim.uncompressed:
                mb = lim.uncompressed // 1_000_000
                raise SfzError(f"The zip unpacks to more than {mb} MB.")
            if info.file_size > lim.ratio_floor and info.file_size > lim.ratio * max(
                info.compress_size, 1
            ):
                raise SfzError(
                    f"{_show(name)!r} in the zip is compressed suspiciously well."
                )
            if name.casefold() in self._fold:
                raise SfzError(f"The zip holds {_show(name)!r} twice.")
            self.members[name] = info
            self._fold[name.casefold()] = name
        self._read_total = 0

    def find(self, path: str) -> str | None:
        """The member `path` names (exact, else any case), or None."""
        if path in self.members:
            return path
        return self._fold.get(path.casefold())

    def read(self, path: str) -> bytes:
        name = self.find(path)
        if name is None:
            raise SfzError(f"The zip has no file {_show(path)!r}.")
        info = self.members[name]
        # a total budget as well as each entry's own declared size: reading
        # never goes past what the entry says it is, and the sum of what is
        # read never past what the archive said it unpacks to
        self._read_total += info.file_size
        if self._read_total > self.lim.uncompressed:
            raise SfzError("The zip's files add up to too much to read.")
        try:
            with self._z.open(info) as f:
                raw = f.read(info.file_size + 1)
        except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError):
            raise SfzError(f"Could not unpack {_show(name)!r} from the zip.") from None
        if len(raw) != info.file_size:
            raise SfzError(f"{_show(name)!r} is not the size the zip declares.")
        return raw


# --- text -> a flat token stream --------------------------------------------------

_COMMENT = re.compile(r"//[^\n]*|/\*.*?(?:\*/|\Z)", re.S)
_DEFINE = re.compile(r"#define\s+(\$[A-Za-z0-9_]+)[ \t]*(.*)\Z")
_INCLUDE = re.compile(r'#include\s+"([^"\n]*)"')
_VAR = re.compile(r"\$[A-Za-z0-9_]+")


@dataclass
class Report:
    """What the import did not do: opcode -> regions it was on."""

    unsupported: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)  # reason -> regions
    notes: dict[str, int] = field(default_factory=dict)  # an approximation

    def count(self, bucket: dict[str, int], key: str, by: int = 1) -> None:
        bucket[key] = bucket.get(key, 0) + by


class _Source:
    """The substituted text of the .sfz and its includes."""

    def __init__(self, arc: Archive, lim: Limits, report: Report):
        self.arc, self.lim, self.report = arc, lim, report
        self.defines: dict[str, str] = {}
        self.out: list[str] = []
        self.lines = 0
        self.chars = 0
        self.files = 0

    def expand(self, text: str, cap: int) -> str:
        if "$" not in text:
            return text
        # bounded while it is built: a long define used many times never
        # makes a string past the cap (a `sub` would build it, then check)
        parts: list[str] = []
        size = pos = 0
        for m in _VAR.finditer(text):
            rep = self.defines.get(m.group(0), m.group(0))
            size += m.start() - pos + len(rep)
            if size > cap:
                raise SfzError("A #define expands past what this importer will read.")
            parts += (text[pos : m.start()], rep)
            pos = m.end()
        size += len(text) - pos
        if size > cap:
            raise SfzError("A #define expands past what this importer will read.")
        parts.append(text[pos:])
        out = "".join(parts)
        return out

    def feed(self, path: str, depth: int, stack: tuple[str, ...]) -> None:
        lim = self.lim
        if depth > lim.include_depth:
            raise SfzError(f"#include nests deeper than {lim.include_depth} files.")
        key = path.casefold()
        if key in stack:
            raise SfzError(f"#include loops back to {_show(path)!r}.")
        self.files += 1
        if self.files > lim.includes:
            raise SfzError(f"The instrument reads more than {lim.includes} files.")
        raw = self.arc.read(path)
        if len(raw) > lim.text_bytes:
            raise SfzError(f"{_show(path)!r} is too long for an .sfz.")
        text = _COMMENT.sub(" ", raw.decode("utf-8-sig", errors="replace"))
        here = posixpath.dirname(path)
        for line in text.splitlines():
            self.lines += 1
            if self.lines > lim.lines:
                raise SfzError(f"The instrument is longer than {lim.lines} lines.")
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                self._directive(s, here, depth, stack + (key,))
                continue
            line = self.expand(s, lim.line_chars)
            self.chars += len(line) + 1
            if self.chars > lim.expanded:
                raise SfzError(
                    "The instrument is too large once its #defines are applied."
                )
            self.out.append(line)

    def _directive(self, s: str, here: str, depth: int, stack: tuple[str, ...]) -> None:
        m = _DEFINE.match(s)
        if m:
            if len(self.defines) >= self.lim.defines and m.group(1) not in self.defines:
                raise SfzError(
                    f"The instrument has more than {self.lim.defines} #defines."
                )
            self.defines[m.group(1)] = self.expand(
                m.group(2).strip(), self.lim.define_chars
            )
            return
        m = _INCLUDE.match(s)
        if m:
            target = m.group(1).replace("\\", "/")
            if target.startswith("/") or _DRIVE.match(target):
                raise SfzError(
                    f"#include names a path outside the zip: {_show(target)!r}."
                )
            path = posixpath.normpath(posixpath.join(here, self.expand(target, 512)))
            if path == ".." or path.startswith("../"):
                raise SfzError(f"#include reaches outside the zip: {_show(target)!r}.")
            self.feed(path, depth + 1, stack)
            return
        self.report.count(self.report.unsupported, s.split()[0][:24])


_TOKEN = re.compile(r"<([A-Za-z_]\w*)>|(?:(?<=[\s>])|^)([A-Za-z_][A-Za-z0-9_]*)=", re.M)

HEADERS = ("control", "global", "master", "group", "region")
_RANK = {"control": 0, "global": 1, "master": 2, "group": 3, "region": 4}


@dataclass
class _Node:
    header: str
    ops: dict[str, str] = field(default_factory=dict)
    used: int = 0  # regions that inherited from it


def parse(text: str, lim: Limits, report: Report) -> tuple[dict[str, str], list[dict]]:
    """The substituted text -> (the `<control>` opcodes, one dict of
    effective opcodes per region: global, master, group, region merged).
    Unknown headers and opcodes of no region are put in `report`."""
    control: dict[str, str] = {}
    nodes: list[_Node] = []
    chain: dict[int, _Node] = {}
    regions: list[dict] = []
    skipped_header: str | None = None
    headers = 0
    tokens = list(_TOKEN.finditer(text))
    for i, m in enumerate(tokens):
        if m.group(1):
            headers += 1
            if headers > lim.headers:
                raise SfzError(f"The instrument has more than {lim.headers} headers.")
            name = m.group(1).lower()
            if name not in _RANK:
                report.count(report.unsupported, f"<{_show(name, 20)}>")
                skipped_header = name
                continue
            skipped_header = None
            rank = _RANK[name]
            node = _Node(name)
            if name == "region":
                if len(regions) >= lim.regions:
                    raise SfzError(
                        f"The instrument has more than {lim.regions} regions."
                    )
                merged: dict[str, str] = {}
                for r in (1, 2, 3):
                    if r in chain:
                        merged.update(chain[r].ops)
                        chain[r].used += 1
                regions.append(merged)
                chain[4] = node
            else:
                for r in [r for r in chain if r >= rank]:
                    del chain[r]
                chain[rank] = node
                nodes.append(node)
            continue
        if skipped_header is not None:
            continue  # an unknown header's opcodes: counted as the header
        end = tokens[i + 1].start() if i + 1 < len(tokens) else len(text)
        nl = text.find("\n", m.end(), end)
        value = text[m.end() : nl if nl >= 0 else end].strip()
        key = m.group(2).lower()
        if 4 in chain:  # inside a region
            regions[-1][key] = value
        elif chain:
            top = chain[max(chain)]
            top.ops[key] = value
            if top.header == "control":
                control[key] = value
        else:
            report.count(report.unsupported, key)  # before any header
    # opcodes of a header with no region under it are still counted, once
    for node in nodes:
        if node.used == 0:
            for key in node.ops:
                if key not in SUPPORTED:
                    report.count(report.unsupported, key)
    return control, regions


# --- opcodes -> a zone -------------------------------------------------------------

SUPPORTED = frozenset(
    {
        "sample",
        "lokey",
        "hikey",
        "key",
        "pitch_keycenter",
        "lovel",
        "hivel",
        "tune",
        "transpose",
        "volume",
        "amplitude",
        "offset",
        "end",
        "loop_mode",
        "loopmode",
        "loop_start",
        "loopstart",
        "loop_end",
        "loopend",
        "seq_length",
        "seq_position",
        "trigger",
        "default_path",
        "octave_offset",
        "note_offset",
        "ampeg_attack",
        "ampeg_decay",
        "ampeg_sustain",
        "ampeg_release",
    }
)
_CONTROL_ONLY = frozenset({"default_path", "octave_offset", "note_offset"})
_ENVELOPE = (
    ("ampeg_attack", "attack"),
    ("ampeg_decay", "decay"),
    ("ampeg_sustain", "sustain"),
    ("ampeg_release", "release"),
)
_NOTE = re.compile(r"([a-g])([#b]?)(-?\d{1,2})\Z")
_PITCH_CLASS = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
LIMIT_BOUND = 1e9


def number(text: str) -> float:
    """A finite number of sane size, or ValueError (NaN, inf, 1e999)."""
    f = float(text.strip())
    if not math.isfinite(f) or abs(f) > LIMIT_BOUND:
        raise ValueError(text)
    return f


def key_number(text: str) -> int:
    """A key as MIDI: an integer, or a note name — c4 = 60 (c-1 = 0), `#`
    and `b` accidentals, either case."""
    t = text.strip().lower()
    m = _NOTE.match(t)
    if m:
        return (
            12 * (int(m.group(3)) + 1)
            + _PITCH_CLASS[m.group(1)]
            + {"#": 1, "b": -1, "": 0}[m.group(2)]
        )
    return round(number(t))


@dataclass
class Instrument:
    name: str
    zones: list[sampler.Zone]
    envelope: dict[str, float]
    unsupported: dict[str, int]
    skipped: dict[str, int]
    notes: dict[str, int]
    sfz_path: str
    regions: int  # regions the file holds
    samples: int  # distinct samples decoded
    truncated: int = 0  # regions past `sampler.MAX_ZONES`, not imported
    sfz_files: int = 1  # .sfz files the zip holds


def _wav_loop(data: bytes) -> tuple[int, int] | None:
    """A WAV's first loop (`smpl` chunk) as inclusive frames, or None."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos = 12
    while pos + 8 <= len(data):
        cid, size = struct.unpack_from("<4sI", data, pos)
        body = pos + 8
        if cid == b"smpl" and size >= 60 and body + 60 <= len(data):
            if struct.unpack_from("<I", data, body + 28)[0] >= 1:
                a, b = struct.unpack_from("<II", data, body + 44)
                return (a, b) if b > a else None
            return None
        pos = body + size + (size & 1)
    return None


@dataclass
class _Decoded:
    sid: str
    frames: int  # the frames kept, at the sample's own rate
    loop: tuple[int, int] | None
    seconds: float


def _decode(raw: bytes, lim: Limits) -> tuple[np.ndarray, int]:
    try:
        import soundfile as sf
    except (ImportError, OSError) as err:
        raise SfzError(
            "Importing audio needs the soundfile package (uv sync --all-extras)."
        ) from err
    try:
        with sf.SoundFile(io.BytesIO(raw)) as f:
            sr, ch = int(f.samplerate), int(f.channels)
            if not lim.min_sr <= sr <= lim.max_sr:
                raise SfzError(f"Unsupported sample rate: {sr} Hz.")
            if not 1 <= ch <= lim.max_channels:
                raise SfzError(f"Unsupported channel count: {ch}.")
            cap = int(sampler.MAX_SAMPLE_SECONDS * sr)
            x = f.read(cap, dtype="float32", always_2d=True)[:, :2]
    except SfzError:
        raise
    except Exception as err:  # libsndfile's, a cut file, MemoryError
        raise SfzError("could not be read as audio") from err
    if not len(x):
        raise SfzError("holds no audio")
    return np.nan_to_num(x), sr


class _Regions:
    """Regions -> zones, with the decode of each sample done once."""

    def __init__(self, arc, save, lim, report, root_of, here, control):
        self.arc, self.save, self.lim, self.report = arc, save, lim, report
        self.root_of, self.here = root_of, here
        self.default_path = control.get("default_path", "").replace("\\", "/")
        self.shift = 0
        for key, scale in (("octave_offset", 12), ("note_offset", 1)):
            try:
                self.shift += (
                    round(number(control[key])) * scale if key in control else 0
                )
            except ValueError:
                report.count(report.notes, f"{key} is not a number")
        self.decoded: dict[str, _Decoded | None] = {}
        self.seconds = 0.0

    def sample(self, name: str) -> _Decoded | None:
        """The sample `name` (a region's `sample=`), decoded and stored;
        None, with the reason in the report, when it cannot be."""
        rel = (self.default_path + name).replace("\\", "/")
        if rel.startswith("/") or _DRIVE.match(rel) or "\0" in rel:
            self.report.count(self.report.skipped, "a sample path outside the zip")
            return None
        path = posixpath.normpath(posixpath.join(self.here, rel))
        if path == ".." or path.startswith("../"):
            self.report.count(self.report.skipped, "a sample path outside the zip")
            return None
        if path in self.decoded:
            return self.decoded[path]
        got = self._load(path, name)
        self.decoded[path] = got
        return got

    def _load(self, path: str, name: str) -> _Decoded | None:
        found = self.arc.find(path)
        if found is None:
            self.report.count(
                self.report.skipped, f"sample not in the zip: {_show(name, 40)}"
            )
            return None
        raw = self.arc.read(found)
        try:
            audio, sr = _decode(raw, self.lim)
        except SfzError as err:
            self.report.count(self.report.skipped, f"{_show(name, 40)} {err}")
            return None
        self.seconds += len(audio) / sr
        if self.seconds > self.lim.audio_seconds:
            raise SfzError(
                f"The samples add up to over {self.lim.audio_seconds:g} s of audio."
            )
        if len(audio) >= int(sampler.MAX_SAMPLE_SECONDS * sr):
            self.report.count(
                self.report.notes, f"samples cut to {sampler.MAX_SAMPLE_SECONDS:g} s"
            )
        sid = self.save(audio, sr)
        return _Decoded(sid, len(audio), _wav_loop(raw), len(audio) / sr)

    # -- one region ------------------------------------------------------------------

    def zone(self, ops: dict[str, str]) -> sampler.Zone | None:
        rep = self.report
        if ops.get("trigger", "attack").strip().lower() != "attack":
            rep.count(rep.skipped, "release / first / legato triggers")
            return None
        name = ops.get("sample", "").strip().strip('"')
        if not name:
            rep.count(rep.skipped, "regions with no sample")
            return None
        if name.startswith("*"):
            rep.count(rep.skipped, f"generated waveforms ({_show(name, 12)})")
            return None
        bad: set[str] = set()

        def num(key: str, default: float | None = None) -> float | None:
            if key not in ops:
                return default
            try:
                return number(ops[key])
            except ValueError:
                bad.add(key)
                return default

        def key_of(key: str) -> int | None:
            if key not in ops:
                return None
            if key == "pitch_keycenter" and ops[key].strip().lower() == "sample":
                return None
            try:
                return key_number(ops[key]) + self.shift
            except ValueError:
                bad.add(key)
                return None

        end_op = num("end")
        if end_op is not None and end_op < 0:  # `end=-1`: the region does not play
            rep.count(rep.skipped, "regions switched off (end=-1)")
            return None
        got = self.sample(name)
        if got is None:
            return None
        lo, hi, single = key_of("lokey"), key_of("hikey"), key_of("key")
        if single is not None:
            lo = hi = single
        root = key_of("pitch_keycenter")
        if root is None:
            if ops.get("pitch_keycenter", "").strip().lower() == "sample":
                root = self.root_of(got.sid) if self.root_of else None
                if root is None:
                    rep.count(
                        rep.notes, "pitch_keycenter=sample with no clear pitch: C4"
                    )
            if root is None:
                root = single if single is not None else 60
        lo = 0 if lo is None else lo
        hi = 127 if hi is None else hi
        if lo > hi:
            lo, hi = hi, lo
        vlo, vhi = num("lovel", 0), num("hivel", 127)
        if vlo > vhi:
            vlo, vhi = vhi, vlo
        # tune in cents: whole semitones past ±100 move the root instead
        tune = num("tune", 0.0)
        semis = round(tune / 100.0)
        tune -= semis * 100.0
        root = root - semis - round(num("transpose", 0.0))
        gain = num("volume", 0.0)
        amp = num("amplitude")
        if amp is not None and amp > 0:
            gain += 20.0 * math.log10(amp / 100.0)
        take = 0
        if "seq_position" in ops:
            pos = num("seq_position")
            take = 0 if pos is None else round(pos) - 1
        n = got.frames
        a = num("offset", 0.0) / n
        b = 1.0 if end_op is None else (end_op + 1) / n
        a, b = min(max(a, 0.0), 1.0), min(max(b, 0.0), 1.0)
        span = (a, b) if (a > 0.0 or b < 1.0) and b > a else (None, None)
        loop = self.loop(ops, got, num, span)
        for key in bad:
            rep.count(
                rep.skipped if key == "sample" else rep.notes, f"{key} is not a number"
            )
        clamps = {
            "keys outside 0-127": not (0 <= lo and hi <= 127 and 0 <= root <= 127),
            "velocities outside 0-127": not (0 <= vlo and vhi <= 127),
            "volume outside -36 to +12 dB": not (
                sampler.ZONE_GAIN_DB[0] <= gain <= sampler.ZONE_GAIN_DB[1]
            ),
            f"takes past {sampler.MAX_TAKES}": not (0 <= take < sampler.MAX_TAKES),
        }
        for why, hit in clamps.items():
            if hit:
                rep.count(rep.notes, f"{why}: clamped")
        clip = lambda v, a, b: min(max(int(v), a), b)  # noqa: E731
        return sampler.Zone(
            got.sid,
            lo=clip(lo, 0, 127),
            hi=clip(hi, 0, 127),
            root=clip(root, 0, 127),
            vlo=clip(vlo, 0, 127),
            vhi=clip(vhi, 0, 127),
            rr=clip(take, 0, sampler.MAX_TAKES - 1),
            gain=min(max(gain, sampler.ZONE_GAIN_DB[0]), sampler.ZONE_GAIN_DB[1]),
            tune=tune,
            start=span[0],
            end=span[1],
            loop_start=loop[0],
            loop_end=loop[1],
        )

    def loop(self, ops, got: _Decoded, num, span) -> tuple[float | None, float | None]:
        mode = (ops.get("loop_mode") or ops.get("loopmode") or "").strip().lower()
        if mode == "one_shot":
            self.report.count(self.report.notes, "one_shot plays as a held note")
        if mode not in ("loop_continuous", "loop_sustain"):
            return None, None
        n = got.frames
        start = num("loop_start", num("loopstart"))
        end = num("loop_end", num("loopend"))
        if start is None and end is None and got.loop:
            start, end = got.loop
        if start is None or end is None:
            start, end = 0.0, n - 1.0
            self.report.count(
                self.report.notes, "a loop with no points loops the whole sample"
            )
        return min(max(start / n, 0.0), 1.0), min(max((end + 1) / n, 0.0), 1.0)


def _envelope(ops: dict[str, str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, field_ in _ENVELOPE:
        if key in ops:
            try:
                v = number(ops[key])
            except ValueError:
                continue
            out[field_] = max(v, 0.0) / 100.0 if field_ == "sustain" else max(v, 0.0)
    return out


def sfz_paths(arc: Archive) -> list[str]:
    """The archive's .sfz files, the shallowest first."""
    found = [n for n in arc.members if n.lower().endswith(".sfz")]
    return sorted(found, key=lambda n: (n.count("/"), n.casefold()))


def load(
    data: bytes,
    save: Callable[[np.ndarray, int], str],
    lim: Limits | None = None,
    *,
    root_of: Callable[[str], int | None] | None = None,
) -> Instrument:
    """The archive `data` as an `Instrument`; `save(audio, sr)` stores one
    sample (stereo or mono float, `sr` its rate) and returns its id.
    Raises `SfzError` — and only that — for anything it will not read."""
    lim = lim or Limits()
    try:
        return _load(data, save, lim, root_of)
    except SfzError:
        raise
    except Exception as err:  # a hostile archive finds something to raise
        raise SfzError("That is not an SFZ archive this importer can read.") from err


def _load(data, save, lim, root_of) -> Instrument:
    arc = Archive(data, lim)
    paths = sfz_paths(arc)
    if not paths:
        raise SfzError("The zip holds no .sfz file.")
    report = Report()
    src = _Source(arc, lim, report)
    src.feed(paths[0], 0, ())
    control, rows = parse("\n".join(src.out), lim, report)
    here = posixpath.dirname(paths[0])
    maker = _Regions(arc, save, lim, report, root_of, here, control)
    zones: list[sampler.Zone] = []
    envelopes: list[dict[str, float]] = []
    seen = 0
    over = 0
    for ops in rows:
        for key in ops:
            if key not in SUPPORTED:
                report.count(report.unsupported, key)
        if len(zones) >= sampler.MAX_ZONES:
            over += 1
            continue
        seen += 1
        zone = maker.zone(ops)
        if zone is not None:
            zones.append(zone)
            envelopes.append(_envelope(ops))
    if not zones:
        why = "; ".join(f"{n} × {k}" for k, n in list(report.skipped.items())[:4])
        raise SfzError(
            "No region of that instrument could be imported"
            + (f" ({why})." if why else ".")
        )
    firsts = envelopes[0]
    if any(e != firsts for e in envelopes[1:]):
        report.count(
            report.notes,
            "the amplitude envelope differs between regions: the first one's is used",
        )
    name = posixpath.splitext(posixpath.basename(paths[0]))[0]
    return Instrument(
        name=_show(name, 60) or "sfz",
        zones=zones,
        envelope=firsts,
        unsupported=report.unsupported,
        skipped=report.skipped,
        notes=report.notes,
        sfz_path=paths[0],
        regions=len(rows),
        samples=len({z.sample for z in zones}),
        truncated=over,
        sfz_files=len(paths),
    )


# --- words ---------------------------------------------------------------------------


def _listed(counts: dict[str, int], shown: int = 6) -> str:
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    bits = [k if n == 1 else f"{k} ×{n}" for k, n in items[:shown]]
    if len(items) > shown:
        bits.append(f"and {len(items) - shown} more")
    return ", ".join(bits)


def summary(inst: Instrument) -> str:
    """What the import did, in a toast's words: the zones, then what was
    not imported (every opcode by name and count), what was left out, and
    what was approximated."""
    z = len(inst.zones)
    zs, ss = ("s" if z != 1 else ""), ("s" if inst.samples != 1 else "")
    out = [f"Imported {inst.name}: {z} zone{zs} from {inst.samples} sample{ss}."]
    if inst.sfz_files > 1:
        out.append(
            f"The zip holds {inst.sfz_files} .sfz files; "
            f"{_show(inst.sfz_path)} was read."
        )
    if inst.truncated:
        out.append(
            f"It has {inst.regions} regions and a sound holds "
            f"{sampler.MAX_ZONES} zones: "
            f"the first {z} that could be imported were, {inst.truncated} were not."
        )
    if inst.skipped:
        out.append(f"Left out: {_listed(inst.skipped)}.")
    if inst.unsupported:
        out.append(f"Not imported: {_listed(inst.unsupported)}.")
    if inst.notes:
        out.append(f"Approximated: {_listed(inst.notes)}.")
    return " ".join(out)
