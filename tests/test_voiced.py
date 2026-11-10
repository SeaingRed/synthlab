"""A sung line read for the sampler (`analysis.voiced`): the held note a
vocal instrument is built on, and the syllables vocal chops are cut at —
against a line the engine's formant voice sang (`tests/songs.py`)."""

from __future__ import annotations

import numpy as np
import pytest

from synthlab.analysis import voiced
from synthlab.core import SR
from synthlab.sampler import detect_pitch
from tests.songs import VOICE_HELD, VOICE_LINE, drum_loop, voice_line


@pytest.fixture(scope="module")
def line() -> np.ndarray:
    return voice_line()


def _notes() -> list[tuple[float, float, float]]:
    """`VOICE_LINE`'s sung notes as (note, start, end) in seconds."""
    at, out = 0.0, []
    for note, seconds in VOICE_LINE:
        if note is not None:
            out.append((note, at, at + seconds))
        at += seconds
    return out


def _held_span() -> tuple[float, float]:
    return next((a, b) for note, a, b in _notes() if note == VOICE_HELD)


def test_the_best_run_is_the_held_note_not_the_whole_line(line):
    """The line's median pitch would be whichever note was sung most, and
    its first sample an attack; the run is the long note, inside it."""
    run = voiced.best_run(line)
    a, b = _held_span()
    assert run is not None and a - 0.05 <= run.start / SR and run.end / SR <= b + 0.05
    assert run.seconds > 1.2 and run.clarity > 0.9 and 0.3 < run.level <= 1.0
    # the note that was sung, to a few cents, vibrato and all
    assert abs(run.midi - VOICE_HELD) < 0.05
    assert run.root == 60 and abs(run.cents - 12) < 5
    heard, sure = detect_pitch(line[run.start : run.end])
    assert sure > 0.8 and abs(run.midi - heard) < 0.1


def test_every_run_is_one_note_held_and_vibrato_does_not_break_it(line):
    runs = voiced.steady_runs(voiced.pitch_track(line))
    assert [round(r.midi) for r in runs] == [round(n) for n, _a, _b in _notes()]
    for r, (_note, a, b) in zip(runs, _notes(), strict=True):
        assert r.seconds >= voiced.MIN_RUN_SECONDS
        assert r.wobble < voiced.STEADY_SEMITONES
        assert a - 0.05 <= r.start / SR and r.end / SR <= b + 0.05
    assert runs[1].wobble > 0.1 > runs[0].wobble  # the vibrato is in the run


def test_a_frame_is_voiced_only_where_the_voice_sounds(line):
    track = voiced.pitch_track(line)
    t = np.arange(len(track.voiced)) * track.hop / SR
    a, b = _held_span()
    inside = (t > a + 0.05) & (t < b - 0.15)
    gap = (t > 0.35) & (t < 0.5)  # the rest after the first note
    assert track.voiced[inside].mean() > 0.9
    assert not track.voiced[gap].any()


@pytest.mark.parametrize(
    "audio",
    [
        np.zeros((SR, 2)),
        np.zeros(10),
        np.random.default_rng(1).standard_normal(SR * 2) * 0.2,
        drum_loop({"kick": "x...x...x...x...", "hat": "..x...x...x...x."})[0],
    ],
    ids=["silence", "a scrap", "noise", "drums"],
)
def test_no_held_note_is_found_where_there_is_none(audio):
    assert voiced.best_run(audio) is None


def test_non_finite_samples_do_not_break_the_track(line):
    x = line.copy()
    x[5000:5010] = np.nan
    x[9000] = np.inf
    assert voiced.best_run(x) is not None


def test_syllables_are_the_sung_spans_not_the_silence_between(line):
    spans = voiced.syllables(line)
    assert len(spans) == len(_notes())
    for (a, b), (_note, start, end) in zip(spans, _notes(), strict=True):
        # a span is its note: it ends with it, the rest is not in the chop
        assert abs(a / SR - start) < 0.03 and abs(b / SR - end) < 0.03


def test_more_syllables_than_keys_joins_the_weakest_breaks(line):
    many = np.concatenate([line] * 4)
    assert len(voiced.syllables(many, max_spans=5)) <= 5
    assert len(voiced.syllables(many)) >= 8
    assert voiced.syllables(np.zeros((SR, 2))) == []
    assert voiced.syllables(np.zeros(3)) == []


# --- melody: the sung line as a curve a synth follows (the Voice lab) ------------


def _middles():
    """(note, first sample, last sample) of each sung note, 0.1 s in from
    either end: where the note is held, clear of the attack and release."""
    return [(n, int((a + 0.1) * SR), int((b - 0.1) * SR)) for n, a, b in _notes()]


def test_the_melody_is_the_sung_notes_sample_by_sample(line):
    mel = voiced.melody(line)
    assert mel is not None and len(mel.midi) == len(line)
    assert np.all(np.isfinite(mel.midi))
    for note, a, b in _middles():
        seg = mel.midi[a:b]
        assert abs(float(seg.mean()) - note) < 0.05, (note, seg.mean())
        # it follows the singer's vibrato (0.25 st on the long note)
        assert float(np.max(np.abs(seg - note))) < 0.3
    assert abs(mel.centre - VOICE_HELD) < 0.1  # the note sung longest
    assert 0.6 < mel.voiced < 0.9  # 2.2 s of the line's 3.0 are sung


def test_silence_between_notes_holds_the_last_one(line):
    """A consonant or a breath has no pitch: the curve stays where it was
    rather than dropping to nothing or leaping about."""
    mel = voiced.melody(line)
    (first, _a, end), (second, start, _b) = _middles()[0], _middles()[1]
    gap = mel.midi[end + int(0.15 * SR) : start - int(0.2 * SR)]
    assert len(gap) and float(np.max(np.abs(gap - first))) < 0.5
    assert first < float(mel.midi[start]) <= second + 0.3


def test_a_snapped_melody_sits_on_the_scales_notes(line):
    c_major = [0, 2, 4, 5, 7, 9, 11]
    mel = voiced.melody(line, pitch_classes=c_major)
    for note, a, b in _middles():
        seg = mel.midi[a:b]
        # 60.12 with its vibrato is C; the others already were
        assert float(np.max(np.abs(seg - round(note)))) < 0.02, note
    # a scale without the sung note moves it to the nearest one it has
    only = voiced.melody(line, pitch_classes=[2])  # D
    assert {round(float(only.midi[a])) for _n, a, _b in _middles()} == {62}


def test_a_snapped_step_is_a_slide_not_a_jump(line):
    mel = voiced.melody(line, pitch_classes=range(12), glide=0.03)
    hard = voiced.melody(line, pitch_classes=range(12), glide=0.0)
    assert float(np.max(np.abs(np.diff(hard.midi)))) >= 1.0
    assert float(np.max(np.abs(np.diff(mel.midi)))) < 0.05  # semitones a sample


def test_snap_takes_the_nearest_allowed_note():
    got = voiced.snap(np.array([60.4, 60.6, 61.5, 62.5, 59.0, 0.2, 127.0]), [0, 4, 7])
    assert got.tolist() == [60.0, 60.0, 60.0, 64.0, 60.0, 0.0, 127.0]
    same = np.array([60.4, 61.0])
    assert voiced.snap(same, []).tolist() == same.tolist()


@pytest.mark.parametrize(
    "audio",
    [
        np.zeros(SR),
        np.random.default_rng(0).standard_normal(SR) * 0.2,
        np.zeros(100),
        np.zeros((0, 2)),
    ],
)
def test_no_melody_where_nothing_is_sung(audio):
    assert voiced.melody(audio) is None


def test_the_edges_of_noise_bursts_are_not_notes():
    """A frame that is mostly silence beside a burst reads as perfectly
    periodic on its own; one frame is no sung note, so gated noise —
    speech without pitch — has no melody."""
    n = SR * 4
    gate = np.sin(2 * np.pi * 2 * np.arange(n) / SR) > 0
    bursts = np.random.default_rng(0).standard_normal(n) * 0.4 * gate
    assert voiced.pitch_track(bursts).voiced.sum() >= 4  # the lone frames
    assert voiced.melody(bursts) is None


def _tone_line(parts: list[tuple[float, float]]) -> np.ndarray:
    """A harmonic tone on each (midi note, seconds) in turn, phase kept."""
    midi = np.concatenate([np.full(int(s * SR), m) for m, s in parts])
    phase = 2 * np.pi * np.cumsum(440.0 * 2.0 ** ((midi - 69.0) / 12.0)) / SR
    return sum(np.sin(h * phase) / h for h in range(1, 9)) * 0.2


def test_a_short_excursion_far_off_the_line_is_a_tracking_error():
    """On a real stem the tracker lands an octave off for a few frames at
    a time. Under a quarter second, more than a fifth from everything
    around it: the curve stays on the line."""
    line = _tone_line([(57, 0.6), (69, 0.1), (57, 0.6)])
    mel = voiced.melody(line)
    assert float(np.max(np.abs(mel.midi - 57))) < 0.3
    # the same excursion a fourth away is a note, and is followed
    near = voiced.melody(_tone_line([(57, 0.6), (62, 0.1), (57, 0.6)]))
    assert float(np.max(near.midi)) > 61.0


def test_a_held_leap_keeps_its_place():
    """An octave that is sung and held is not one of those: it lands where
    it was sung, not half a second late."""
    mel = voiced.melody(_tone_line([(57, 0.7), (69, 0.7)]))
    at = int(0.7 * SR)
    assert abs(float(mel.midi[at - int(0.1 * SR)]) - 57) < 0.1
    assert abs(float(mel.midi[at + int(0.1 * SR)]) - 69) < 0.1
