"""The proof: a take plays back exactly as it was played (the owner's ask,
2026-11-22: "the same keyboard presses at the exact same times ... it
should sound 100% the same on playback as the loop").

One take is posted as keyrec.js posts it — a chord of mixed velocities
struck a little apart and off the grid, releases between the steps, a note
held over a bar line, a staccato note, a downbeat played early (onto the
line where a section's chunk starts), the pitch bend moved and a cutoff
knob swept — and its loop (placed "as played", unasked) is rendered three
ways: its ▶ (`takes._render_loop`'s inputs), its Edit on the grid
(Perform's render of the edited lane) and its → Song section (eight bars at
120 bpm: rendered in two chunks, `render.section_windows`). Each is held to
a render of the notes AS PLAYED — `Synth.note` on each raw event of the
take's `played`: its own start, its own held length (the gate), its own
velocity — in the loop's sound, folded and normalized as a loop is.

All three in float (no WAV between): ▶ and Edit are one render; both equal
the notes as played within one 16-bit step (2^-15) — measured 0 apart from
float rounding. The reference is `Song.notes`' arithmetic on each raw
event (an event of `length / NOTE_GATE`, gated at NOTE_GATE: its held
length); the schedule rounds a felt note's start and length to a billionth
of a beat so a take's own decimals come back as the very floats it posted
(a gate on a half-sample tie otherwise rounded one ulp the other way: 3e-3).
The section in its two chunks: the same within one 16-bit step without a
curve — the downbeat played early onto chunk 2's line plays in chunk 1.
With the curves, each chunk reads them cropped to its bars and spread over
its own samples (`song_model._crop_curve`, `curves.curve_array`), which the
whole loop does not: `SECTION_CURVE_BOUND` (measured 1.4e-3), and the early
downbeat, sounding in chunk 1 past its last line, reads the curve frozen at
that line (0.106 under the moving cutoff: the strict xfail; pre-existing
for any note held over a seam). The knob as turned against the curve the
loop keeps: the curve is the moves themselves, spread over the loop's
samples (`curves.curve_array`: sample i at i / (n - 1) of the loop, not
i / n), so it runs at most a sample ahead by the loop's end, and the step
at the first move may land a sample apart — `TURNED_BOUND` on the audio
(measured 2.2e-3). Nobody has listened.
"""

from __future__ import annotations

import io
import json
import wave
from dataclasses import replace
from urllib.parse import unquote

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.core import SR, n_samples
from synthlab.sequencer import Song, name_of, note_span
from synthlab.studio import loops, render
from synthlab.studio import song_model as sm
from synthlab.studio.arrange import NOTE_GATE, _curve_slice, loop_seconds
from synthlab.studio.curves import CURVABLE, curve_array
from synthlab.studio.routes import takes
from synthlab.studio.server import create_app
from synthlab.synth import Synth, at_bpm

LSB = 2.0**-15  # one 16-bit step
BPM = 120.0
BEAT = 60.0 / BPM

# [start, length, midi, velocity] in beats, as keyrec.js posts them
PLAYED = [
    # a chord struck a little apart, mixed velocities, let go between steps
    [0.03, 1.37, 60, 0.9],
    [0.05, 1.21, 64, 0.55],
    [0.062, 0.93, 67, 0.3],
    # a line: off the grid, a staccato note
    [1.52, 0.4, 72, 0.7],
    [2.26, 0.11, 74, 0.62],
    # held over the bar line (beat 4)
    [3.4, 1.83, 57, 0.8],
    # bar 5's downbeat played a hair early: the line a chunk starts on
    [19.94, 0.77, 62, 0.85],
    [20.3, 0.5, 65, 0.45],
    # bar 8, held to near the end
    [28.02, 3.85, 60, 0.75],
    [29.5, 0.24, 67, 0.5],
]
# the pitch bend moved (and sprung back), the cutoff swept up and back
BEND = [[3.6, 0.2], [3.8, 0.5], [4.0, 0.5], [4.4, 0.1], [4.5, 0.0]]
SWEEP = [[1.0 + 0.1 * i, 800.0 + 250.0 * i] for i in range(30)] + [
    [24.0, 1500.0],
    [26.0, 900.0],
]
MOVES = {"from": {"cutoff": 800.0}, "moves": {"pitch_bend": BEND, "cutoff": SWEEP}}

TURNED_BOUND = 3e-3  # the knob as turned vs the curve kept (measured 2.2e-3)
SECTION_CURVE_BOUND = 2e-3  # a chunk's cropped curve (measured 1.4e-3)


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * 2 * 44100)
    return buf.getvalue()


@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("exact")
    with TestClient(create_app(data_dir=tmp)) as c:
        s, svc = c.app.state.studio, c.app.state.svc
        s.layers[s.active_layer]["fx"] = []
        s.curves = {}
        s.patch = replace(s.patch, cutoff=2400.0, release=0.12)  # where the knob ends
        r = c.post(
            "/take/keys",
            data={
                "name": "keys-1",
                "bpm": str(BPM),
                "bars": "8",
                "notes": json.dumps(PLAYED),
                "moves": json.dumps(MOVES),
            },
            files={"file": ("take.wav", _wav(), "audio/wav")},
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200, r.text[:300]
        name = unquote(r.headers["X-Loop-Name"])
        loop = loops.load(s.data_dir, name)
        assert loop["quantise"] == 0 and len(loop["lane"]["note_feel"]) == len(PLAYED)
        patch, extras = loop["sound"]
        assert patch.cutoff == 800.0 and set(extras["curves"]) == {
            "cutoff",
            "pitch_bend",
        }

        arr, _w = takes._loop_lane(s, loop)
        arr = replace(arr, bpm=loop["key"]["bpm"])
        inp = svc.render_inputs(
            "perform",
            arrangement=arr,
            parts=("synth",),
            patch=patch,
            chain=extras,
            mix=True,
        )
        assert not inp.master_fx and not inp.layers
        played = render._render(inp, None)[0]

        r = c.post("/loop/edit", data={"name": name}, headers={"HX-Request": "true"})
        assert r.status_code == 200
        edit_inp = svc.render_inputs("perform", parts=("synth",))
        edited = render._render(edit_inp, None)[0]

        r = c.post("/loop/song", data={"name": name}, headers={"HX-Request": "true"})
        assert r.status_code == 200
        song, master = svc.song_snapshot()
        (sec,) = song["sections"]
        parts = sm.section_arrangement(song, sec)
        assert render.section_windows(parts) == [(0, 5), (5, 8)]  # two chunks
        cyc = render.section_master_audio(parts, master)
        folded = cyc.base.copy()
        folded[: len(cyc.tail)] += cyc.tail[: len(folded)]
        heard = render._normalize(folded)[0]
        yield {
            "played": played,
            "edited": edited,
            "heard": heard,
            "patch": patch,
            "curves": extras["curves"],
            "arr": arr,
            "inp": inp,
        }


def _as_played(ctx, trajectory) -> np.ndarray:
    """The notes as played, each its own `Synth.note` at its own start,
    held its own length, at its own velocity, its automation read by
    `trajectory(param, start_sample, n)`; folded and normalized as the loop
    is (`render._wrap_tile`, `render._normalize`)."""
    patch = at_bpm(ctx["patch"], BPM)
    song = Song(bpm=BPM)
    for start, length, midi, vel in PLAYED:
        # `Song.notes`' arithmetic for an event held `length` beats: it
        # gates an event at NOTE_GATE of its length, so the event is
        # length / NOTE_GATE long — its buffer that plus the release
        dur = length / NOTE_GATE * BEAT
        span = note_span(dur, patch)
        at = song.beats_to_samples(start)
        n = n_samples(span, SR)
        tweaks = {p: trajectory(p, at, n) for p in ctx["curves"]}
        audio = Synth(patch.tweak(**tweaks)).note(
            name_of(midi), span, vel, gate=dur * NOTE_GATE
        )
        song.play("x", audio, start)
    raw = song.render(normalize=0)
    own = n_samples(loop_seconds(ctx["arr"], ("synth",)), SR)
    return render._normalize(render._wrap_tile(raw, own, own))[0]


def _the_loops_curve(ctx):
    own = n_samples(loop_seconds(ctx["arr"], ("synth",)), SR)
    full = {p: curve_array(pts, p, own) for p, pts in ctx["curves"].items()}
    return lambda p, at, n: _curve_slice(full[p], at, n)


def _as_turned(p: str, at: int, n: int) -> np.ndarray:
    """The control as it was turned, in time: its start value until its
    first move, then the moves (linear between), the last one held; the
    wheel's on the patch's own 0."""
    pts = BEND if p == "pitch_bend" else SWEEP
    start = 0.0 if p == "pitch_bend" else 800.0
    beats = (at + np.arange(n)) / SR / BEAT
    v = np.interp(beats, [b for b, _v in pts], [v for _b, v in pts])
    return np.where(beats < pts[0][0], start, v)


def _diff(a: np.ndarray, b: np.ndarray) -> float:
    assert a.shape == b.shape, (a.shape, b.shape)
    return float(np.abs(a - b).max())


def test_play_and_edit_are_one_render(renders):
    np.testing.assert_array_equal(renders["played"], renders["edited"])


def test_the_loop_plays_the_notes_as_played(renders):
    """Every press at its own time, length and velocity, with the bend and
    the sweep the loop keeps: within one 16-bit step."""
    ref = _as_played(renders, _the_loops_curve(renders))
    assert _diff(renders["played"], ref) < LSB


def _seam_note(x: np.ndarray) -> np.ndarray:
    """`x` with the span of the downbeat played early onto chunk 2's first
    line (beat 19.94, 0.77 held + its release) zeroed."""
    out = x.copy()
    out[int(19.9 * BEAT * SR) : int(20.95 * BEAT * SR)] = 0.0
    return out


def test_the_song_section_plays_the_notes_as_played(renders):
    """The section in its two chunks: each chunk reads the loop's curves
    cropped to its bars (`SECTION_CURVE_BOUND`, measured 1.4e-3)."""
    ref = _as_played(renders, _the_loops_curve(renders))
    assert _diff(_seam_note(renders["heard"]), _seam_note(ref)) < SECTION_CURVE_BOUND


@pytest.mark.xfail(
    strict=True,
    reason="a chunk reads a drawn curve cropped to its bars and holds its "
    "value at its last line past it (song_model.SectionParts.window, "
    "_crop_curve; arrange._curve_slice): a note felt early onto the next "
    "chunk's line sounds in this chunk and reads the curve frozen there "
    "(measured 0.106 under the swept cutoff). Pre-existing for any note held "
    "over a seam; reading a cycle-long curve per chunk moves every chunked "
    "section with a curve: RENDER_REV",
)
def test_a_downbeat_felt_early_onto_a_seam_under_a_curve_is_not_yet_exact(renders):
    ref = _as_played(renders, _the_loops_curve(renders))
    assert _diff(renders["heard"], ref) < SECTION_CURVE_BOUND


def test_the_section_without_curves_is_within_one_16_bit_step(tmp_path):
    """The same take, no control moved: the chunked section is the notes
    as played within one 16-bit step (the early downbeat on chunk 2's
    first line plays in chunk 1, `song_model._window_notes`)."""
    with TestClient(create_app(data_dir=tmp_path)) as c:
        s, svc = c.app.state.studio, c.app.state.svc
        s.layers[s.active_layer]["fx"] = []
        s.curves = {}
        s.patch = replace(s.patch, release=0.12)
        r = c.post(
            "/take/keys",
            data={
                "name": "keys-1",
                "bpm": str(BPM),
                "bars": "8",
                "notes": json.dumps(PLAYED),
            },
            files={"file": ("take.wav", _wav(), "audio/wav")},
            headers={"HX-Request": "true"},
        )
        name = unquote(r.headers["X-Loop-Name"])
        loop = loops.load(s.data_dir, name)
        patch, _extras = loop["sound"]
        arr = replace(takes._loop_lane(s, loop)[0], bpm=BPM)
        c.post("/loop/song", data={"name": name}, headers={"HX-Request": "true"})
        song, master = svc.song_snapshot()
        parts = sm.section_arrangement(song, song["sections"][0])
        cyc = render.section_master_audio(parts, master)
        folded = cyc.base.copy()
        folded[: len(cyc.tail)] += cyc.tail[: len(folded)]
        heard = render._normalize(folded)[0]
        ref = _as_played({"patch": patch, "curves": {}, "arr": arr}, None)
        assert _diff(heard, ref) < LSB


def test_the_knob_as_turned_against_the_curve_the_loop_keeps(renders):
    ref = _as_played(renders, _as_turned)
    got = _diff(renders["played"], ref)
    assert got < TURNED_BOUND, got
    # the curve's values are the moves': at most a sample's slope apart
    own = n_samples(loop_seconds(renders["arr"], ("synth",)), SR)
    lo, hi = CURVABLE["cutoff"]
    kept = curve_array(renders["curves"]["cutoff"], "cutoff", own)
    turned = _as_turned("cutoff", 0, own)
    step = max(
        abs(b[1] - a[1]) / ((b[0] - a[0]) * BEAT * SR)
        for a, b in zip(SWEEP, SWEEP[1:], strict=False)
    )
    away = np.abs(kept - turned)
    away[np.abs(np.diff(turned, prepend=turned[0])) > 100] = (
        0  # the step at the first move
    )
    assert away.max() <= 2 * step + 1e-6 * (hi - lo)
