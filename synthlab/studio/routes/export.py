"""Export and import: the song as audio (WAV, FLAC or MP3) and as MIDI,
the stems zip, the loop as MIDI, and the project file. (A MIDI file comes
*in* through the note grid's own route, `routes/pattern.py`.)"""

from __future__ import annotations

import datetime as _dt
import io
import json
import logging
import zipfile
from typing import Annotated

import numpy as np
from fastapi import Query, Request, Response
from fastapi.responses import HTMLResponse

from ... import midifile
from ...audio import EXPORT_FORMATS, CodecUnavailable, codec, encode
from ...effects import to_stereo
from .. import limits, midi_bridge, projectzip, song_play
from ..files import zip_write
from ..render import _to_wav_bytes
from ..routing import (
    OutsideLock,
    ProjectUpload,
    Svc,
    render_outside_lock,
    studio_router,
)
from ..state import StudioState

log = logging.getLogger("synthlab.studio")

router = studio_router()


def _attachment(ext: str) -> dict:
    stamp = _dt.date.today().isoformat()
    return {"Content-Disposition": f'attachment; filename="{stamp}.synthlab.{ext}"'}


@router.get("/project.json")
def project_export(svc: Svc) -> Response:
    s: StudioState = svc.state
    s.flush()
    return Response(
        projectzip.project_text(s.project_payload()),
        media_type="application/json",
        headers=_attachment("json"),
    )


@router.get("/project.zip")
def project_bundle(svc: Svc) -> Response:
    """The project with its audio (`projectzip`): project.json as
    /project.json serves it, plus every sample and clip it references, so
    it plays on another machine. Import takes it back."""
    s: StudioState = svc.state
    s.flush()
    return Response(
        projectzip.build(s.project_payload(), s.data_dir),
        media_type="application/zip",
        headers=_attachment("zip"),
    )


@router.post("/project/import")
def project_import(request: Request, upload: ProjectUpload, svc: Svc) -> Response:
    """A project.json (the state alone) or a bundle from /project.zip (the
    state and its audio: `projectzip.read` validates the whole bundle and
    the project loads before any of its audio is written)."""
    if upload.data is None and not upload.too_large:
        return HTMLResponse("missing file", status_code=422)
    bundle = None
    try:
        if upload.data is None:
            raise ValueError("upload too large")
        if projectzip.is_zip(upload.data):
            bundle = projectzip.read(upload.data)
            data = bundle.project
        elif len(upload.data) > limits.MAX_PROJECT_UPLOAD_BYTES:
            # the path's body cap is the bundle's; a bare project.json keeps
            # its own
            return Response("project file too large", status_code=413)
        else:
            data = json.loads(upload.data.decode("utf-8"))
        svc.state.load_project(data)
    except Exception:
        # Exception, not a list of types: whatever a hostile file makes
        # the parser or the loader raise (RecursionError from deep
        # nesting included) is a rejected import. load_project rolls the
        # session back first, so a 422 always means "nothing changed".
        log.warning("project import failed", exc_info=True)
        return svc.toast("That isn't a valid synthlab project file.")
    try:
        if bundle is not None:
            projectzip.install(bundle, svc.state)
    except Exception:
        # the session has loaded; audio that couldn't be written plays as
        # silence, exactly as a project.json without it would. Exception,
        # as above: only OSError was caught, and anything else left the
        # new session behind a 500 with the old session's caches.
        log.warning("project import: audio not installed", exc_info=True)
    finally:
        # the session changed whatever became of its audio
        svc.reset_caches()
    if request.headers.get("HX-Request"):
        # htmx posts with fetch(), which follows a 303 by itself: htmx only
        # saw the redirected page's 200 (no HX-Redirect on it) under
        # hx-swap="none", and the page kept showing the old session. A 200
        # carrying HX-Refresh reaches htmx, which reloads the page onto the
        # imported state (a 204 is one of base.html's noSwap statuses).
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    return Response(status_code=303, headers={"Location": "/"})


Loudness = Annotated[str, Query(max_length=16)]


def _steps(song: dict, loudness: str = "peak") -> int:
    """A mixdown's progress steps (`events.Task`): each section's two
    passes for the song's gain, each section stitched, and the mastering
    when there is a loudness target."""
    return 3 * len(song["sections"]) + (loudness != "peak")


def _mastered(svc, song: dict, master: list, loudness: str, task):
    """The export's audio at a loudness target, and what it measures."""
    target = song_play.LOUDNESS_TARGETS[loudness]["lufs"]
    mix = svc.song_mixdown(song, master, task)
    if target is not None:
        # the master works in float32 (`song_play.master`): hand it that, so
        # the float64 mixdown (339 MB at the length cap) is gone first
        mix = mix.astype(np.float32)
    task.say("Mastering")
    audio = song_play.master(mix, target)
    task.step()
    return audio, song_play.loudness_report(audio)


def _readout(report: dict) -> str:
    """ "-14.0 LUFS · true peak -1.0 dB", or why there is no number."""
    if report["lufs"] is None:
        return "too quiet or too short to measure"
    return f"{report['lufs']:.1f} LUFS · true peak {report['true_peak_db']:.1f} dB"


def _refused_format(svc: Svc, fmt: str) -> Response | None:
    """Why an export can't be written as `fmt`, as the 422 toast — asked
    before anything renders: a name that is not one of `EXPORT_FORMATS`
    (the whitelist; the value never reaches a filename or a library call
    otherwise), or a codec this machine doesn't have."""
    try:
        codec(fmt)
    except KeyError:
        return svc.toast(f"Export format must be one of: {', '.join(EXPORT_FORMATS)}.")
    except CodecUnavailable as err:
        return svc.toast(str(err))
    return None


def _encoded(audio, fmt: str) -> bytes:
    """An export's audio in `fmt`. WAV is the encoder it always was, so
    those bytes are what they were before there was a choice."""
    if fmt == "wav":
        return _to_wav_bytes(audio)
    return encode(to_stereo(np.asarray(audio, dtype=np.float64)), fmt)


@router.get("/export/song.wav")
@render_outside_lock
def export_song(
    svc: Svc, loudness: Loudness = "peak", format: str = "wav"
) -> Response | OutsideLock:
    """The song, rebuilt on the song model: the same section passes the
    player queues from `/song/plan.json` (same renders, order, tail folds
    and repeats), the master chain once over the whole of it, and the
    song-wide gain (`StudioService.song_mixdown`). The length caps are
    checked from the song's numbers before anything renders (a RenderError
    is the app's 422 toast). Renders outside the studio lock, from a
    snapshot of the song (`render_outside_lock`).

    `loudness` names a `song_play.LOUDNESS_TARGETS` entry (anything else
    is a 422). The default, "peak", is that mixdown and nothing more: the
    response it always was, byte for byte. A loudness target masters the
    mixdown (`song_play.master`) and says what came out, in the file's name
    and in `X-Loudness-LUFS` / `X-Peak-dBFS` / `X-True-Peak-dBTP`.

    `?format=` picks the file (`audio.EXPORT_FORMATS`: wav, flac, mp3) —
    the same audio either way; FLAC holds the WAV's own 16-bit samples."""
    if loudness not in song_play.LOUDNESS_TARGETS:
        return Response(status_code=422)
    if refused := _refused_format(svc, format):
        return refused
    song, master = svc.song_snapshot()
    if not song["sections"]:
        return Response(status_code=404)
    media, ext = EXPORT_FORMATS[format][:2]

    def run() -> Response:
        name, extra = "synthlab-song", {}
        # progress for the pages that are open (`events.Task`): the render
        # is the same with nobody watching
        with svc.events.task("Exporting song", _steps(song, loudness)) as task:
            if loudness == "peak":
                audio = svc.song_mixdown(song, master, task)
            else:
                audio, report = _mastered(svc, song, master, loudness, task)
                if report["lufs"] is not None:
                    name += f" ({report['lufs']:.1f} LUFS)"
                keys = ("X-Loudness-LUFS", "X-Peak-dBFS", "X-True-Peak-dBTP")
                extra = {
                    k: f"{v:.1f}"
                    for k, v in zip(keys, report.values(), strict=True)
                    if v is not None
                }
            body = _encoded(audio, format)
        disposition = f'attachment; filename="{name}.{ext}"'
        return Response(
            body,
            media_type=media,
            headers={"Content-Disposition": disposition, **extra},
        )

    return OutsideLock(run)


@router.get("/export/song/loudness")
@render_outside_lock
def export_loudness(svc: Svc, loudness: Loudness = "peak") -> Response | OutsideLock:
    """The Song page's "measure": what `/export/song.wav` at this target
    measures, as the `#song-loudness` readout — the same render (its
    sections are memoized, so the export after it renders nothing again),
    measured the same way. Renders outside the studio lock."""
    if loudness not in song_play.LOUDNESS_TARGETS:
        return Response(status_code=422)
    song, master = svc.song_snapshot()
    if not song["sections"]:
        return Response(status_code=404)

    def run() -> Response:
        with svc.events.task("Measuring loudness", _steps(song) + 1) as task:
            _audio, report = _mastered(svc, song, master, loudness, task)
        return HTMLResponse(
            '<output id="song-loudness" class="song-loudness" aria-live="polite">'
            f"{_readout(report)}</output>"
        )

    return OutsideLock(run)


@router.get("/export/stems.zip")
@render_outside_lock
def export_stems(svc: Svc, format: str = "wav") -> Response | OutsideLock:
    """One file per sounding track, each the whole song with only that
    track playing (`StudioService.song_stems`). Entry names are fixed by
    kind and index — `drums.wav`, `synth-1.wav`, ..., `audio-1.wav` — and
    never carry user text (a track named `../../x` used to be a path).
    Renders outside the studio lock, from a snapshot of the song.
    `?format=` (as `/export/song.wav`) writes each stem as that format,
    under its extension."""
    if refused := _refused_format(svc, format):
        return refused
    song, master = svc.song_snapshot()
    ext = EXPORT_FORMATS[format][1]

    def run() -> Response:
        buf = io.BytesIO()
        with svc.events.task("Exporting stems") as task:  # a step per stem
            stems = svc.song_stems(song, master, task)
            if not stems:
                return Response(status_code=404)
            # stored, not deflated: 16-bit PCM barely compresses and
            # deflating it was half the export's time (1.85 s of 3.5 s).
            # `zip_write`: no wall clock in the entries, so the same song
            # is the same zip whenever it is exported
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
                for name, stem in stems:
                    entry = name if format == "wav" else f"{name[:-4]}.{ext}"
                    zip_write(z, entry, _encoded(stem, format))
        zip_name = "synthlab-stems" if format == "wav" else f"synthlab-stems-{ext}"
        return Response(
            buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{zip_name}.zip"'},
        )

    return OutsideLock(run)


# --- MIDI: the notes, not the audio (studio/midi_bridge.py) ---------------------


def _midi(file, name: str) -> Response:
    if not file.tracks:
        return Response(status_code=404)  # nothing plays a note
    return Response(
        midifile.write(file),
        media_type="audio/midi",
        headers={"Content-Disposition": f'attachment; filename="{name}.mid"'},
    )


@router.get("/export/loop.mid")
def export_loop_midi(svc: Svc) -> Response:
    """The Perform loop as a Standard MIDI File: one track per synth lane
    that plays (the edited lane, then the other layers) and the beat on
    channel 10 at General MIDI drum keys, at the loop's tempo. Read from
    Perform's own render inputs, so it holds the notes ▶ plays there — the
    same parts, mutes and solos — and nothing the grid hides. No render."""
    return _midi(midi_bridge.loop_file(svc.render_inputs("perform")), "synthlab-loop")


@router.get("/export/song.mid")
def export_song_midi(svc: Svc) -> Response:
    """The song as a Standard MIDI File: one track per sounding drums /
    synth track, sections end to end with their repeats
    (`midi_bridge.song_file`). The song's length caps apply as they do to
    the WAV (`check_length`: a RenderError is the app's 422 toast), which
    also bounds the file."""
    song = svc.state.song
    if not song["sections"]:
        return Response(status_code=404)
    song_play.check_length(song)
    return _midi(midi_bridge.song_file(song), "synthlab-song")
