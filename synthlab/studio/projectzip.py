"""A project with its audio: `GET /project.zip`, and the bundle half of
`POST /project/import` (routes/export.py).

project.json holds state only. A sampler sound names its sample by content
id (`studio_data/samples/<id>.wav`), and clip lanes, drum one-shots and
song takes name clips (`studio_data/clips/<name>.wav`), so a project moved
to another machine rendered silence. The bundle carries them:

    project.json              exactly what GET /project.json serves
    samples/<16 hex>.wav      every sample a patch in it names (the main
                              sound, layers, A/B, "before last load", song
                              tracks, baselines — anywhere in the payload;
                              preset files are not part of a project)
    clips/<name>.wav          every clip it places, as the clip store
    clips/<name>.meta.json    keeps it, and its meta when it has one
    takenotes/<name>.json     a keys take's notes, when it is one
                              (`keytakes.py`: the clip's sidecar)

Reading one (`read`) is strict, and refuses the whole bundle before
anything is written: only those names (no directories, no other paths —
so no traversal and no absolute names), no duplicates (case-folded, as
Windows' filesystem sees them), at most `MAX_PROJECT_ZIP_MEMBERS` entries
and `MAX_PROJECT_ZIP_UNCOMPRESSED` bytes unpacked (each entry read no
further than it declares), a clip that is a plain 16-bit PCM WAV the clip
store could have written, a meta that is a JSON object, a take's notes
that are JSON — held to `keytakes.clean_doc`, the one reader of a take's
notes. A take from before the sidecar carries its notes in its meta: they
are moved into a sidecar on the way in.

Installing (`install`, after the project itself has loaded):

  * a sample whose audio doesn't hash to its name (`sampler.sample_id`) is
    skipped, not installed: the id IS the content, so a mismatch is damage
    and installing it would file audio under another sound's name. Samples
    already here are left alone (same id, same audio).
  * a clip never overwrites. The same name holding the same bytes is
    reused; a name this studio already uses for different audio installs
    the incoming clip under a free name ("jam 2", clear of every name in
    the bundle too) and every reference the project makes to it is
    rewritten (`StudioState.remap_clip_refs`, all at once) — so neither
    this studio's clip nor the project's is lost, and the project plays
    what it played where it was made.
"""

from __future__ import annotations

import io
import json
import logging
import re
import wave
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import sampler
from . import keytakes, limits
from .clips import (
    META_SETTINGS,
    TAKE_NOTES,
    clean_meta,
    clip_dir,
    clip_safe,
    free_clip_name,
    meta_number,
    sidecar,
)
from .files import zip_write
from .state import StudioState, clip_refs

log = logging.getLogger("synthlab.studio")

PROJECT = "project.json"
_SAMPLE = re.compile(r"samples/([0-9a-f]{16})\.wav")
_CLIP = re.compile(r"clips/([^/\\]+?)(\.meta\.json|\.wav)")
_NOTES = re.compile(rf"{TAKE_NOTES}/([^/\\]+?)\.json")
_WAV_HEADER = 4096  # room for a WAV's chunks beside its audio
_SAMPLE_BYTES = int(sampler.MAX_SAMPLE_SECONDS * sampler.SR) * 4 + _WAV_HEADER
_CLIP_BYTES = int(limits.MAX_STORED_CLIP_SECONDS * limits.MAX_SR) * 4 + _WAV_HEADER


class BundleError(ValueError):
    """Not a project bundle this studio will read."""


def is_zip(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"


def project_text(payload: dict) -> str:
    """project.json's text: the one serialisation both exports use."""
    return json.dumps(payload, indent=2)


def sample_refs(payload) -> set[str]:
    """Every valid sample id under a `sampler_sample` key anywhere in a
    persisted payload, and every zone's under a `sampler_zones` key (a
    multi-sample instrument)."""
    found: set[str] = set()
    stack = [payload]
    while stack:
        node = stack.pop()
        items = node.items() if isinstance(node, dict) else enumerate(node)
        for key, value in items:
            if key == "sampler_sample" and sampler.valid_id(value):
                found.add(value)
            elif key == "sampler_zones" and isinstance(value, str):
                found.update(sampler.zone_samples(value))
            elif isinstance(value, (dict, list)):
                stack.append(value)
    return found


# --- writing -------------------------------------------------------------------


def build(payload: dict, data_dir: Path) -> bytes:
    """The bundle for `payload` (a `project_payload`), with every sample
    and clip it references that this studio has. Deterministic: the
    entries are in sorted order and carry no wall clock (`files.zip_write`),
    so one session's bundle is the same bytes whenever it is built."""
    buf = io.BytesIO()
    clips = clip_dir(data_dir)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        zip_write(z, PROJECT, project_text(payload))
        # audio is stored, not deflated: 16-bit PCM barely compresses (the
        # stems export measured deflating it as half its time)
        for sid in sorted(sample_refs(payload)):
            data = sampler.wav_bytes(sid)
            if data is not None:
                zip_write(z, f"samples/{sid}.wav", data, zipfile.ZIP_STORED)
        seen: set[str] = set()
        for name in sorted({clip_safe(c) for c in clip_refs(payload)}):
            wav = clips / f"{name}.wav"
            if name.casefold() in seen or not wav.is_file():
                continue  # one file on a case-insensitive disk; or missing
            seen.add(name.casefold())
            zip_write(z, f"clips/{name}.wav", wav.read_bytes(), zipfile.ZIP_STORED)
            meta = clips / f"{name}.meta.json"
            if meta.is_file():
                zip_write(z, f"clips/{name}.meta.json", meta.read_bytes())
            notes = sidecar(data_dir, TAKE_NOTES, name)
            if notes.is_file():
                zip_write(z, f"{TAKE_NOTES}/{name}.json", notes.read_bytes())
    return buf.getvalue()


# --- reading -------------------------------------------------------------------


@dataclass
class Bundle:
    project: dict
    samples: dict[str, bytes] = field(default_factory=dict)  # id -> WAV bytes
    clips: dict[str, tuple[bytes, dict | None]] = field(default_factory=dict)
    notes: dict[str, dict] = field(default_factory=dict)  # a take's, clean_doc'd


def _member(z: zipfile.ZipFile, info: zipfile.ZipInfo, cap: int) -> bytes:
    """An entry's bytes, never reading past `cap` or past what it declares
    (a zip bomb's small declared size gets exactly that, then a refusal)."""
    if info.file_size > cap:
        raise BundleError(f"{info.filename}: too large")
    with z.open(info) as f:
        raw = f.read(info.file_size + 1)
    if len(raw) != info.file_size:
        raise BundleError(f"{info.filename}: not the size it declares")
    return raw


def _check_clip(name: str, data: bytes) -> bytes:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            ch, width, sr, n = (
                w.getnchannels(),
                w.getsampwidth(),
                w.getframerate(),
                w.getnframes(),
            )
            raw = w.readframes(n)
    except (wave.Error, EOFError, OSError) as err:
        raise BundleError(f"clip {name!r}: not a WAV") from err
    ok = (
        width == 2
        and ch in (1, 2)
        and limits.MIN_SR <= sr <= limits.MAX_SR
        and n <= int(limits.MAX_STORED_CLIP_SECONDS * sr)
        and len(raw) == n * ch * width
    )
    if not ok:
        raise BundleError(f"clip {name!r}: not a clip this studio could have made")
    return data


def _json(name: str, raw: bytes, what: str):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as err:
        raise BundleError(f"clip {name!r}: {what} isn't JSON") from err


def _clean_meta(name: str, raw: bytes | None) -> dict | None:
    """A clip's meta: a JSON object. Scalar values are kept, and a
    generated take's flat `transition` settings; the keys the studio
    itself writes are held to their types (`clips.clean_meta`), so a
    `"bpm": "fast"` is dropped here instead of failing every page that
    draws the clip shelf. A keys take's `notes` from before the sidecar
    are kept as a list for `read` to move out (`keytakes.clean_doc` reads
    them)."""
    if raw is None:
        return None
    meta = _json(name, raw, "meta")
    if not isinstance(meta, dict):
        raise BundleError(f"clip {name!r}: meta isn't an object")

    def scalar(v) -> bool:
        if isinstance(v, str):
            return len(v) <= limits.MAX_NAME_CHARS
        return v is None or isinstance(v, bool) or meta_number(v)

    def keep(k, v) -> bool:
        if k == "notes":  # an older keys take's: `read` moves them
            return isinstance(v, list)
        if k in META_SETTINGS and isinstance(v, dict):
            return len(v) <= 16 and all(
                isinstance(sk, str) and scalar(sv) for sk, sv in v.items()
            )
        return scalar(v)

    return clean_meta({k: v for k, v in meta.items() if keep(k, v)})


def read(data: bytes) -> Bundle:
    """Validate a bundle (see the module doc) and hold its parts in memory.
    Raises BundleError on anything it won't read; nothing is written."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError) as err:
        raise BundleError("not a zip") from err
    if len(infos) > limits.MAX_PROJECT_ZIP_MEMBERS:
        raise BundleError("too many entries")
    if sum(i.file_size for i in infos) > limits.MAX_PROJECT_ZIP_UNCOMPRESSED:
        raise BundleError("too large unpacked")
    names = [i.filename.casefold() for i in infos]
    if len(set(names)) != len(names):
        raise BundleError("duplicate entries")
    project = None
    samples: dict[str, bytes] = {}
    wavs: dict[str, bytes] = {}
    metas: dict[str, bytes] = {}
    notes: dict[str, dict] = {}
    for info in infos:
        name = info.filename
        if info.is_dir() or info.flag_bits & 0x1:  # a directory; encrypted
            raise BundleError(f"unexpected entry {name!r}")
        if name == PROJECT:
            raw = _member(z, info, limits.MAX_PROJECT_UPLOAD_BYTES)
            project = json.loads(raw.decode("utf-8"))
        elif m := _SAMPLE.fullmatch(name):
            samples[m[1]] = _member(z, info, _SAMPLE_BYTES)
        elif (m := _CLIP.fullmatch(name)) and clip_safe(m[1]) == m[1]:
            if m[2] == ".wav":
                wavs[m[1]] = _check_clip(m[1], _member(z, info, _CLIP_BYTES))
            else:
                metas[m[1]] = _member(z, info, limits.MAX_CLIP_META_BYTES)
        elif (m := _NOTES.fullmatch(name)) and clip_safe(m[1]) == m[1]:
            raw = _member(z, info, limits.MAX_TAKE_NOTES_BYTES)
            notes[m[1]] = keytakes.clean_doc(_json(m[1], raw, "notes"))
        else:
            raise BundleError(f"unexpected entry {name!r}")
    if not isinstance(project, dict):
        raise BundleError("no project.json")
    if set(metas) - set(wavs):
        raise BundleError("a clip's meta without its clip")
    if set(notes) - set(wavs):
        raise BundleError("a take's notes without its clip")
    clips = {}
    for n, w in wavs.items():
        meta = _clean_meta(n, metas.get(n))
        # an older take's notes, in its meta: out, into a sidecar
        legacy = meta.pop("notes", None) if meta is not None else None
        if legacy is not None and n not in notes and keytakes.is_take(meta):
            notes[n] = keytakes.clean_doc({"notes": legacy})
        clips[n] = (w, meta)
    return Bundle(project, samples, clips, notes)


# --- installing ----------------------------------------------------------------


@dataclass
class Installed:
    samples: list[str] = field(default_factory=list)  # ids now here
    skipped: list[str] = field(default_factory=list)  # ids that didn't match
    renamed: dict[str, str] = field(default_factory=dict)  # clip -> new name


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.import-tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def install(bundle: Bundle, state: StudioState) -> Installed:
    """Put the bundle's samples and clips into this studio (module doc: a
    mismatched sample is skipped, a clip never overwrites), and point the
    loaded session's references at any clip that had to take a new name.
    Call after `state.load_project(bundle.project)`, under the lock."""
    done = Installed()
    for sid, data in sorted(bundle.samples.items()):
        x = sampler.from_wav_bytes(data)
        if x is None or sampler.sample_id(x) != sid:
            done.skipped.append(sid)
            log.warning("project import: sample %s doesn't match its name", sid)
            continue
        sampler.save(state.sample_dir, x)
        done.samples.append(sid)
    d = clip_dir(state.data_dir)
    claimed = {n.casefold() for n in bundle.clips}
    for name, (wav, meta) in sorted(bundle.clips.items()):
        target = d / f"{name}.wav"
        if target.exists():
            if target.read_bytes() == wav:
                continue  # this studio already has it
            name_here = free_clip_name(state.data_dir, name, claimed)
            claimed.add(name_here.casefold())
            done.renamed[name] = name_here
            target = d / f"{name_here}.wav"
        else:
            name_here = name
        _write(target, wav)
        if meta is not None:
            text = json.dumps({**meta, "name": name_here}, indent=2)
            _write(d / f"{name_here}.meta.json", text.encode("utf-8"))
        if name in bundle.notes:
            keytakes.write_notes(state.data_dir, name_here, bundle.notes[name])
        else:  # a stray sidecar here would be read as this clip's notes
            sidecar(state.data_dir, TAKE_NOTES, name_here).unlink(missing_ok=True)
    if done.renamed:
        state.remap_clip_refs(lambda c: done.renamed.get(clip_safe(c), c))
        state.save()
    return done
