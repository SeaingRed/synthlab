"""Opt-in: what the Sample Lab makes of stems on a real record, through the real
model. Needs both `SYNTHLAB_STEMS_MODEL_DIR` (the model file) and
`SYNTHLAB_TEST_AUDIO` (a folder holding the song); without them every test
here is skipped, and the default suite never runs the model or the network.

The song is "Circles" by Josh Woodward (CC BY 4.0), whose page lists
D minor and 93 bpm. There is no ground truth for its stems, its sung notes
or its drum pattern: what is held here is what was measured when this was
written and can be checked without ears — the key against the page, a
voice that is a held note of the song's scale, a drum reading that is the
same pattern from one verse to the next. None of it is a claim that it
*sounds* right (`tools/lab_measure.py` prints the detail to read)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from synthlab import stems
from synthlab.analysis import drumhits, voiced
from synthlab.core import SR

MODEL_DIR = os.environ.get("SYNTHLAB_STEMS_MODEL_DIR", "")
AUDIO_DIR = os.environ.get("SYNTHLAB_TEST_AUDIO", "")
pytestmark = pytest.mark.skipif(
    not (MODEL_DIR and AUDIO_DIR),
    reason="set SYNTHLAB_STEMS_MODEL_DIR and SYNTHLAB_TEST_AUDIO",
)
D_MINOR = {2, 4, 5, 7, 9, 10, 0}  # D E F G A Bb C, as pitch classes


@pytest.fixture(scope="module")
def song(tmp_path_factory):
    """(data dir, the separated source's meta): one separation for the file."""
    from synthlab.studio import lab, sources
    from synthlab.studio.labjobs import LabJobs

    data_dir = tmp_path_factory.mktemp("lab-real")
    full = next(
        p for p in sorted(Path(AUDIO_DIR).glob("*.mp3")) if "NoVox" not in p.name
    )
    meta = sources.add(data_dir, full.read_bytes(), full.name, analyse=lab.analyse)
    jobs = LabJobs(data_dir)
    jobs.model_dir = Path(MODEL_DIR)
    assert jobs.start(meta["id"], stems.HTDEMUCS.id) == ""
    jobs.join(1800)
    assert jobs.last and jobs.last["error"] == "", jobs.last
    return data_dir, meta, sources.read_meta(data_dir, meta["id"])


def test_the_bass_stem_makes_the_real_songs_key_d_minor_by_a_real_margin(song):
    """The regression: from the drumless stems the chroma alone says F
    major (0.818 to 0.736). Measured with the bass line asked: D minor,
    +0.113 over F major, where the mix had it by the tie rule at -0.033."""
    _data_dir, before, after = song
    mix, now = before["analysis"], after["analysis"]
    assert mix["key"] == "D minor" and mix["key_margin"] < 0  # the tie rule's
    assert now["key"] == "D minor" and now["key_from"] == "stems"
    assert now["key_tonic"] == "bass" and now["key_margin"] > 0.05
    assert now["key_confidence"] >= mix["key_confidence"]
    assert [k for k, _r in now["key_scores"][:2]] == ["D minor", "F major"]


@pytest.mark.parametrize("bar", [0, 16, 32, 48])
def test_a_vocal_region_gives_a_held_note_of_the_songs_scale(song, bar):
    """Measured: A3 (1.54 s), F4 (0.95 s), C4 (0.92 s), E4 (0.55 s), each
    96-99 % periodic and within 4 cents of the key."""
    from synthlab.studio import lab, labmake, sources

    data_dir, _before, meta = song
    g = lab.grid(meta)
    start = int((g["offset"] + bar * g["bar"]) * SR)
    audio = sources.read(data_dir, meta, "vocals", start, int(4 * g["bar"] * SR))
    run = voiced.best_run(audio)
    assert run is not None and run.seconds > 0.4 and run.clarity > 0.9
    assert run.root % 12 in D_MINOR and abs(run.cents) < 20
    fields, said, found = labmake.vocal_instrument(audio)
    assert found and fields["sampler_mode"] == "formant" and "held" in said
    n = len(audio)
    assert (
        run.start <= fields["sampler_loop_start"] * n < fields["sampler_loop_end"] * n
    )
    assert fields["sampler_loop_end"] * n <= run.end
    assert 2 <= len(voiced.syllables(audio)) <= 32


def test_the_drum_stem_reads_as_the_same_groove_from_verse_to_verse(song):
    """No ground truth. Measured on three 4-bar regions a verse apart: 9-13
    hits a bar, kicks and snares only (its hats are 35 dB down and gated),
    a snare on beat 2 of every bar, and the kick's first bar the same row
    in all three."""
    from synthlab.studio import lab, sources

    data_dir, _before, meta = song
    g = lab.grid(meta)
    first_bars = []
    for bar in (32, 40, 56):
        start = int(round((g["offset"] + bar * g["bar"]) * SR))
        audio = sources.read(data_dir, meta, "drums", start, int(4 * g["bar"] * SR))
        pat, _hits = drumhits.transcribe(audio, g["bar"], 4)
        per_bar = [sum(1 for _v, s in pat.steps if s // 16 == b) for b in range(4)]
        assert all(6 <= n <= 16 for n in per_bar), per_bar
        assert set(pat.counts) <= {"kick", "snare", "hat", "ohat"}
        assert pat.counts["kick"] >= 16 and pat.counts["snare"] >= 6
        for b in range(4):
            assert ["snare", b * 16 + 4] in pat.steps, (bar, b)  # beat 2
        first_bars.append(pat.row("kick")[:8])
    assert len(set(first_bars)) == 1, first_bars


@pytest.mark.parametrize("bar", [16, 44])
def test_the_vocal_stem_drives_the_vocoder(song, bar):
    """The Voice lab's obvious modulator: eight bars of the vocal stem
    No ground truth and nobody
    listening; what is held is what was measured at bar 44 —

    - 62% of the frames voiced, and the followed line has no step of more
      than a fifth between two voiced frames (the raw track had 26 in 20 s
      before `voiced.melody` put its octave errors back on the line);
    - the followed carrier is on that line: median 0.8 cents off, 99.7%
      of voiced frames within 50; snapped, 96% of them on a D minor note;
    - over a held D minor chord the result follows the voice's level
      (r 0.82 on 20 ms frames) and is 36 dB down where the voice is silent;
    - with a sine carrier, which has nothing above 4 kHz, the consonants
      are at -110 dB there without the noise path and -33 dB with
      sibilance at 0.5, and the voiced frames' level does not move."""
    import numpy as np

    from synthlab import effects as fx
    from synthlab.studio import lab, sources, voice
    from synthlab.synth import Patch

    data_dir, _before, meta = song
    g = lab.grid(meta)
    start = int((g["offset"] + bar * g["bar"]) * SR)
    audio = sources.read(data_dir, meta, "vocals", start, int(8 * g["bar"] * SR))
    take = audio.mean(axis=1)
    take = take / np.max(np.abs(take)) * 0.9
    n = len(take)
    track = voiced.pitch_track(take)
    line = voiced.melody(take)
    snapped = voiced.melody(take, pitch_classes=sorted(D_MINOR))
    assert line is not None and line.voiced > 0.4
    sung = np.flatnonzero(track.voiced)
    at = np.minimum(sung * track.hop + 1024, n - 1)
    assert int((np.abs(np.diff(line.midi[at])) > 7).sum()) <= 2
    on_scale = np.isin(np.round(snapped.midi[at]).astype(int) % 12, sorted(D_MINOR))
    assert on_scale.mean() > 0.9
    # the carrier is where the line says
    carrier, share = voice.follow_carrier(Patch(), take, None)
    assert carrier.shape == (n, 2) and share == line.voiced
    heard = voiced.pitch_track(carrier)
    both = np.flatnonzero(track.voiced & heard.voiced)
    cents = (heard.notes[both] - line.midi[np.minimum(both * 512 + 1024, n - 1)]) * 100
    assert np.median(np.abs(cents)) < 10 and np.mean(np.abs(cents) < 50) > 0.95

    def frames(x, hop=882):
        x = x.mean(axis=1) if x.ndim == 2 else x
        return np.sqrt(np.mean(x[: len(x) // hop * hop].reshape(-1, hop) ** 2, axis=1))

    chord = voice.chord_carrier(Patch(), [[50, 53, 57]], n, 93.0, "1")
    out = voice.render_voice(Patch(), take, bands=16, carrier=chord)
    said, got = frames(take), frames(out)
    assert np.corrcoef(said, got)[0, 1] > 0.6
    quiet, loud = said < said.max() * 0.01, said > said.max() * 0.25
    if quiet.sum() > 5:
        assert 20 * np.log10(got[loud].mean() / (got[quiet].mean() + 1e-12)) > 25
    # the consonant path, where the carrier has no top end of its own
    dark = Patch(osc1="sine")
    hum = voice.chord_carrier(dark, [[50, 53, 57]], n, 93.0, "1")
    dry = voice.render_voice(dark, take, bands=16, carrier=hum)
    wet = voice.render_voice(dark, take, bands=16, carrier=hum, sibilance=0.5)
    level = frames(take, 512)
    hiss = frames(fx.unvoiced(take), 512)
    heard_at = level > level.max() * 10 ** (-30 / 20)

    def top(x):
        x = x.mean(axis=1)
        for _ in range(2):
            x = fx.biquad(x, "highpass", 4000.0, 0.707, SR)
        return frames(x, 512)

    ess, vowel = heard_at & (hiss > 0.5), heard_at & (hiss < 0.05)
    assert ess.sum() > 10 and vowel.sum() > 100
    assert 20 * np.log10(top(wet)[ess].mean() / (top(dry)[ess].mean() + 1e-12)) > 40
    moved = 20 * np.log10(
        frames(wet, 512)[vowel].mean() / frames(dry, 512)[vowel].mean()
    )
    assert abs(moved) < 1.0
