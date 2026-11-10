"""Tempo, beat grid and key of a whole recording (`analysis.rhythm`,
`analysis.key`), against loops the engine rendered at a known tempo in a
known key (`tests/songs.py`)."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from synthlab.analysis import Grid, detect_key, tempo
from synthlab.analysis.key import NO_KEY, chroma, pitchgram, tonic_weights
from synthlab.analysis.rhythm import NO_GRID, onset_envelope
from synthlab.core import SR
from tests.songs import PAIR_BAR as BAR_S
from tests.songs import loop, pair_song, tones

# (bpm, key, seconds of silence before the first beat)
CASES = [
    (128, "A minor", 0.0),
    (93, "C major", 0.137),
    (140, "F# minor", 0.25),
    (110, "A minor", 0.4),
]


def _wrapped(error: float, period: float) -> float:
    """`error` as a distance around a circle of `period` seconds."""
    return (error + period / 2) % period - period / 2


@pytest.fixture(scope="module")
def loops() -> dict:
    return {case: loop(case[0], case[1], 8, case[2]) for case in CASES}


@pytest.mark.parametrize("case", CASES)
def test_a_rendered_loop_reads_its_tempo_within_one_percent(loops, case):
    bpm = case[0]
    grid = tempo(loops[case])
    assert abs(grid.bpm - bpm) / bpm < 0.01, grid
    assert grid.confidence > 0.4, grid


@pytest.mark.parametrize("case", CASES)
def test_the_beat_phase_lands_within_thirty_milliseconds(loops, case):
    bpm, _key, lead_in = case
    grid = tempo(loops[case])
    assert abs(_wrapped(grid.beat_offset - lead_in, 60.0 / bpm)) < 0.030, grid


@pytest.mark.parametrize("case", CASES)
def test_the_downbeat_guess_finds_the_bar_line_where_the_chords_change(loops, case):
    bpm, _key, lead_in = case
    grid = tempo(loops[case])
    assert abs(_wrapped(grid.downbeat_offset - lead_in, 240.0 / bpm)) < 0.030, grid


@pytest.mark.parametrize("case", CASES)
def test_a_rendered_loop_reads_its_key(loops, case):
    found = detect_key(loops[case])
    assert found.name == case[1], found
    assert found.scores[0][0] == case[1]


def test_a_tempo_outside_the_range_reads_as_its_octave():
    """174 bpm is as much 87: the grid is the same beats, every other one.
    The Lab's halve / double buttons are for this."""
    grid = tempo(loop(174, "A minor", 8))
    assert min(abs(grid.bpm - 174), abs(grid.bpm - 87) * 2) / 174 < 0.01, grid


def test_the_grid_lists_its_beats_and_bars():
    grid = Grid(bpm=120.0, confidence=1.0, beat_offset=0.25, downbeat_offset=0.75)
    assert grid.beat_seconds == 0.5 and grid.bar_seconds == 2.0
    assert np.allclose(grid.beats(2.0), [0.25, 0.75, 1.25, 1.75])
    assert len(NO_GRID.beats(10.0)) == 0


@pytest.mark.parametrize("seconds", [0.0, 0.002, 0.5, 5.0])
def test_silence_and_scraps_have_no_grid_and_no_key(seconds):
    quiet = np.zeros((int(seconds * SR), 2))
    assert tempo(quiet) == NO_GRID
    assert detect_key(quiet) == NO_KEY
    assert chroma(quiet).sum() == 0
    assert not np.any(onset_envelope(quiet))


def test_noise_reads_as_unsure_never_as_an_error():
    noise = np.random.default_rng(3).standard_normal(SR * 8) * 0.1
    grid, found = tempo(noise), detect_key(noise)
    assert grid.confidence < 0.2, grid
    assert found.confidence < 0.3, found


def test_a_sustained_pad_has_a_key_but_no_sure_tempo():
    """No onsets to fold: the key still reads, the tempo says it is unsure."""
    t = np.arange(SR * 6) / SR
    pad = sum(np.sin(2 * np.pi * f * t) for f in (220.0, 261.63, 329.63)) * 0.2
    assert tempo(pad).confidence < 0.2
    assert detect_key(pad).name == "A minor"


def test_mono_and_stereo_read_the_same(loops):
    stereo = loops[CASES[0]]
    mono = stereo.mean(axis=1)
    assert tempo(mono) == tempo(stereo)
    assert detect_key(mono) == detect_key(stereo)


def test_non_finite_samples_do_not_reach_the_answer(loops):
    x = loops[CASES[0]].copy()
    x[1000:1010] = np.nan
    x[5000] = np.inf
    assert abs(tempo(x).bpm - 128) < 2
    assert detect_key(x).tonic == "A"


def test_another_sample_rate_reads_the_same_tempo(loops):
    half = loops[CASES[0]][::2]  # 22.05 kHz, crudely
    assert abs(tempo(half, SR // 2).bpm - 128) / 128 < 0.01


def test_the_pitchgram_puts_a_bass_note_on_its_own_semitone():
    """A bin is 5.4 Hz and a semitone at A2 is 6.5: without interpolating
    the peak, A1 read as A#1 and every key came out a semitone sharp."""
    t = np.arange(SR * 2) / SR
    gram, _fps = pitchgram(np.sin(2 * np.pi * 110.0 * t))
    assert int(np.argmax(gram.sum(axis=0))) + 36 == 45  # A2


# --- key or relative: the bass line's say ---------------------------------------


@pytest.mark.parametrize(
    ("roots", "key"), [("D D D D", "D minor"), ("F F F F", "F major")]
)
def test_the_bass_line_settles_a_key_against_its_relative(roots, key):
    """The same chords twice; only where the bass rests differs."""
    pads, bass = pair_song(roots)
    found = detect_key(pads, bass=bass, bar=BAR_S)
    assert found.name == key and found.tonic_from == "bass", found
    assert found.margin > 0.3, found
    assert found.confidence == detect_key(pads).confidence  # the scale's, unmoved
    # the pair leads the ranking either way, so the page can name both
    assert {found.scores[0][0], found.scores[1][0]} == {"D minor", "F major"}


def test_the_bass_counts_harder_on_downbeats_and_over_the_last_bars():
    """A bass that walks, but lands on D on every bar line and ends there."""
    beat = BAR_S / 4
    bar = np.concatenate([tones(n, beat, 0.5) for n in "D C Bb A".split()])
    walk = np.concatenate([bar] * 6 + [tones("D", BAR_S, 0.5)] * 2)
    flat = tonic_weights(walk)
    on_grid = tonic_weights(walk, bar=BAR_S)
    d = 2  # D's pitch class
    assert abs(flat.sum() - 1) < 1e-9 and abs(on_grid.sum() - 1) < 1e-9
    assert int(np.argmax(on_grid)) == d and on_grid[d] > flat[d] + 0.1


def test_the_bass_is_asked_about_the_relative_pair_and_nothing_else(loops):
    """A bass sitting on the fifth all song long does not move the key
    there: in A minor's loop the relative (C major) is nowhere near."""
    case = CASES[0]
    drone = tones("E", len(loops[case]) / SR, 0.5)
    found = detect_key(loops[case], bass=drone, bar=240.0 / case[0])
    assert found.name == "A minor" and found.tonic_from == "", found


def test_a_silent_or_missing_bass_leaves_the_reading_as_it_was(loops):
    x = loops[CASES[0]]
    plain = detect_key(x)
    assert detect_key(x, bass=np.zeros((SR * 4, 2)), bar=2.0) == plain
    assert not tonic_weights(np.zeros(SR * 4)).any()
    assert not tonic_weights(np.zeros(4)).any()
    assert tonic_weights(tones("D", 4.0, 0.5), bar=float("nan")).argmax() == 2


AUDIO_DIR = os.environ.get("SYNTHLAB_TEST_AUDIO", "")


@pytest.mark.skipif(not AUDIO_DIR, reason="set SYNTHLAB_TEST_AUDIO to a song folder")
def test_a_real_song_reads_its_published_tempo_and_its_key_or_the_relative():
    """Opt-in: "Circles" by Josh Woodward (CC BY 4.0), whose page lists
    93 bpm and D minor. Measured: 93.002 bpm; D minor by the tonic rule,
    with F major — the relative — the higher raw correlation (0.777 to
    0.744, a margin of -0.033). From the mix alone the key is therefore
    held to the pair; with the separated bass it is D minor by +0.113
    (`tests/test_lab_real.py`, which needs the model)."""
    import soundfile as sf

    files = sorted(Path(AUDIO_DIR).glob("*.mp3"))
    full = next(p for p in files if "NoVox" not in p.name)
    audio, sr = sf.read(str(full), dtype="float32", always_2d=True)
    assert abs(tempo(audio, sr).bpm - 93.0) / 93.0 < 0.01
    assert detect_key(audio, sr).name in ("D minor", "F major")


def _waltz(bpm: float, bars: int = 12) -> np.ndarray:
    """A 3/4 loop the engine rendered: the kick on one, the snare on two
    and three, a bass root and a chord a bar, i-iv-v-i in A minor."""
    from synthlab import Song, Synth, preset
    from synthlab.sequencer import chord
    from tests.songs import KICK, PROGRESSIONS

    song = Song(bpm=bpm, beats_per_bar=3)
    prog, roots = PROGRESSIONS["A minor"]
    song.drum("drums", "kick", "x...........", bars=bars, **KICK)
    song.drum("drums", "snare", "....x...x...", bars=bars, gain=0.6)
    song.drum("drums", "hat", "x...x...x...", bars=bars, gain=0.3)
    bass, keys = Synth(preset("bass_reese")), Synth(preset("keys_ep"))
    for b in range(bars):
        root, (name, quality) = roots[b % 4], prog[b % 4]
        song.notes("bass", bass, [(root, 3.0)], start_beat=b * 3, gain=0.8)
        stab = chord(name, quality, octave_shift=1)
        song.notes("keys", keys, [(stab, 3.0)], start_beat=b * 3, gain=0.6)
    x = song.render(tail_beats=0)
    return x[: int(round(bars * 3 * 60.0 / bpm * SR))]


def test_the_meter_guess_hears_three_beats_in_a_waltz_and_four_in_a_loop(loops):
    """A cheap meter read (`rhythm._meter_guess`): which bar period, three
    beats or four, the beat-by-beat novelty repeats at more strongly."""
    for bpm in (120, 150):
        grid = tempo(_waltz(bpm))
        assert grid.meter_guess == 3, (bpm, grid)
        assert 0.0 < grid.meter_sure <= 1.0
    for case in CASES:
        assert tempo(loops[case]).meter_guess == 4, case
    assert NO_GRID.meter_guess == 0
