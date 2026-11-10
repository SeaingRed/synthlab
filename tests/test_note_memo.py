"""Song.notes renders each distinct note once and reuses the buffer.

The optimisation is only sound while Synth.note/.chord stay deterministic and
Track.render keeps treating the audio as read-only — both are pinned here.
"""

from __future__ import annotations

import numpy as np

from synthlab.sequencer import Song, note_span
from synthlab.synth import Patch, Synth


def test_synth_note_is_deterministic():
    """The memo hands the same buffer to several events; if note() ever became
    non-deterministic (unseeded noise, a per-call RNG) that would be wrong."""
    for kw in (
        {"noise_level": 1.0},
        {"noise_level": 0.8, "noise_color": "pink"},
        {"osc1": "supersaw", "unison": 7, "unison_detune": 20},
        {"osc1": "pluck"},
        {"osc1": "fm", "fm_ratio": 2.5, "fm_index": 4.0},
    ):
        s = Synth(Patch(**kw))
        assert np.array_equal(s.note("A4", 0.4, 0.9), s.note("A4", 0.4, 0.9)), kw


def test_repeated_notes_render_once():
    calls = []
    synth = Synth(Patch())
    real = synth.note

    def counting(pitch, dur, velocity=1.0, **kw):
        # from_pitch is part of the memo key, so it must be part of the tally
        calls.append(
            (
                pitch,
                round(float(dur), 6),
                round(float(velocity), 6),
                kw.get("from_pitch"),
            )
        )
        return real(pitch, dur, velocity, **kw)

    synth.note = counting
    # a steady row of one pitch: the first note glides from nothing, the rest
    # all glide from C4, so the whole run collapses to two distinct renders
    seq = [("C4", 0.25)] * 8
    song = Song(bpm=120)
    song.track("t")
    song.notes("t", synth, seq)
    assert len(calls) == 2, f"expected 2 distinct renders, got {len(calls)}: {calls}"
    assert len(set(calls)) == len(calls), f"a duplicate slipped through: {calls}"


def test_memo_does_not_change_the_render():
    synth = Synth(Patch(osc1="saw", noise_level=0.2))
    seq = [("C4", 0.5), ("C4", 0.5), ("E4", 0.25), ("C4", 0.5), (None, 0.25)]
    song = Song(bpm=120)
    song.track("t")
    memoed = song.notes("t", synth, seq).render(normalize=0)

    # same schedule, but every note rendered independently (pre-memo behaviour)
    manual = Song(bpm=120)
    manual.track("t")
    t, prev = 0.0, None
    for pitch, length in seq:
        if pitch is not None:
            dur = length * manual.beat_seconds
            manual.play(
                "t",
                synth.note(
                    pitch,
                    note_span(dur, synth.patch),
                    0.9,
                    gate=dur * 0.9,
                    from_pitch=prev,
                ),
                t,
                1.0,
            )
            prev = pitch
        t += length
    assert np.array_equal(memoed, manual.render(normalize=0))


def test_track_render_does_not_mutate_the_shared_buffer():
    """One buffer backs several events; an in-place write in Track.render
    would corrupt every later repeat of that note."""
    audio = Synth(Patch()).note("C4", 0.4, 0.9)
    original = audio.copy()
    song = Song(bpm=120)
    song.track("t", gain=0.5, pan=0.3)
    for beat in (0.0, 1.0, 2.0):
        song.play("t", audio, beat, 0.8)
    song.render(normalize=0)
    assert np.array_equal(audio, original), "Track.render wrote into the note buffer"
