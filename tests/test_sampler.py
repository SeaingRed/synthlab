"""The "sampler" oscillator shape (synthlab/sampler.py): content-addressed
samples, pitch by resampling or by a formant-keeping grain train, a
granular time-stretch, loops with a baked crossfade, and chops (slices on
the keys, each with its own pitch and reverse)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from synthlab import sampler
from synthlab.core import SR, formant, saw
from synthlab.explore.breed import mutate, random_patch
from synthlab.params import GENES, PARAMS, clamp
from synthlab.synth import Patch, Synth

A3 = 57  # 220 Hz


def _tone(hz: float = 220.0, seconds: float = 1.0, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    x = np.sin(2 * np.pi * hz * t) * amp
    return np.column_stack([x, x])


def _vowel(seconds: float = 2.0) -> np.ndarray:
    """A sung-vowel-like source: a 220 Hz saw through the vowel tract."""
    s = saw(220.0, int(seconds * SR))
    return formant(np.column_stack([s, s]), 0.0, 1.0, 1.0) * 0.5


def _patch(sid: str, **kw) -> Patch:
    base = Patch(
        osc1="sampler",
        osc1_level=1.0,
        filter_type="none",
        attack=0.001,
        sustain=1.0,
        release=0.02,
        sampler_sample=sid,
        sampler_root=A3,
    )
    return replace(base, **kw)


def _f0(x: np.ndarray, lo: float = 0.05, hi: float = 0.45) -> float:
    """The strongest line's frequency, 0.05-0.45 s in."""
    m = x.mean(axis=1) if x.ndim == 2 else x
    m = m[int(lo * SR) : int(hi * SR)]
    spec = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 17))
    return float(np.argmax(spec) * SR / (1 << 17))


def _centroid(x: np.ndarray) -> float:
    m = x.mean(axis=1)[int(0.05 * SR) : int(0.45 * SR)]
    spec = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return float((spec * f).sum() / spec.sum())


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x))))


# --- the store ------------------------------------------------------------------


def test_ids_are_content_hashes_and_files_reload_exactly(tmp_path):
    a, b = _tone(220), _tone(221)
    sid = sampler.save(tmp_path, a)
    assert sampler.valid_id(sid)
    assert sampler.save(tmp_path, a) == sid  # same audio, same id
    assert sampler.register(b) != sid  # other audio, other id
    assert (tmp_path / f"{sid}.wav").is_file()
    assert sampler.register(a[:, 0]) == sid  # mono in: stored stereo
    # what a render reads is exactly what the file holds
    kept = sampler.get(sid).copy()
    sampler._MEM._d.pop(sid)
    sampler.add_dir(tmp_path)
    assert np.array_equal(sampler.get(sid), kept)


def test_other_sample_rates_are_converted_and_length_is_capped():
    x = np.sin(np.arange(48000) * 2 * np.pi * 440 / 48000)
    sid = sampler.register(x, sr=48000)
    y = sampler.get(sid)
    assert abs(len(y) - SR) <= 1
    assert _f0(y, 0.1, 0.9) == pytest.approx(440, abs=2)
    long = sampler.register(np.zeros((int(40 * SR), 2)) + 0.1)
    assert len(sampler.get(long)) == int(sampler.MAX_SAMPLE_SECONDS * SR)


def test_a_missing_or_bad_sample_renders_silence():
    for sid in ("", "0123456789abcdef", "../../etc/passwd", None, 3):
        y = Synth(_patch("", sampler_sample=sid)).note(A3, 0.3)
        assert not np.any(y)


def test_a_damaged_or_foreign_file_is_a_missing_sample(tmp_path):
    """The store reads only what it wrote. A file cut short mid-frame used
    to raise ValueError out of `get` into `Synth.note`; one at another
    rate played at the wrong pitch."""
    import wave

    sampler.add_dir(tmp_path)
    sid = sampler.save(tmp_path, _tone(330, 0.2))
    path = tmp_path / f"{sid}.wav"
    whole = path.read_bytes()

    def other_rate() -> bytes:
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(whole[44:])
        return path.read_bytes()

    for bad in (whole[:-1], whole[:-3], whole[:-400], whole[:20], other_rate()):
        path.write_bytes(bad)
        sampler._MEM._d.pop(sid, None)
        assert sampler.get(sid) is None
        assert not np.any(Synth(_patch(sid)).note(A3, 0.2))
    path.write_bytes(whole)
    assert sampler.get(sid) is not None


def test_saving_leaves_no_temp_file_and_never_shares_one(tmp_path, monkeypatch):
    """Two saves of the same audio wrote the same `<id>.tmp`, so one could
    publish the other's half-written file."""
    from pathlib import Path

    names = []
    write = Path.write_bytes

    def spy(self, data):
        names.append(self.name)
        return write(self, data)

    monkeypatch.setattr(Path, "write_bytes", spy)
    a = _tone(123, 0.1)
    sid = sampler.save(tmp_path, a)
    (tmp_path / f"{sid}.wav").unlink()
    assert sampler.save(tmp_path, a) == sid
    assert len(set(names)) == 2 and f"{sid}.tmp" not in names
    assert [f.name for f in tmp_path.iterdir()] == [f"{sid}.wav"]


def test_clamp_cleans_the_sampler_strings():
    p = clamp(_patch("../x", sampler_slices="0,0.5,0,0;junk;0.5,2,99,1"))
    assert p.sampler_sample == ""
    assert p.sampler_slices == "0.00000,0.50000,0,0;0.50000,1.00000,24,1"
    good = _patch("0123456789abcdef", sampler_slices=p.sampler_slices)
    assert clamp(good) == good


# --- pitch --------------------------------------------------------------------


def _osc(p: Patch, key: int, seconds: float) -> np.ndarray:
    """The sampler oscillator alone (no dc blocker, envelope or filter)."""
    n = int(seconds * SR)
    hz = sampler.midi_hz(key)
    return sampler.render(np.full(n, hz), n, SR, sampler.Settings.of(p), hz)


def test_the_root_plays_the_sample_as_recorded():
    x = _tone(220)
    sid = sampler.register(x)
    # the grain train within its window-sum ripple (measured 0.7 %)
    for mode, tol in (("resample", 1e-6), ("formant", 5e-3)):
        y = _osc(_patch(sid, sampler_mode=mode), A3, 1.0)
        mid = slice(int(0.1 * SR), int(0.8 * SR))
        assert np.allclose(y[mid], sampler.get(sid)[mid], atol=tol), mode
    # through the whole voice: the level as recorded
    y = Synth(_patch(sid)).note(A3, 1.0)
    assert _rms(y[int(0.1 * SR) : int(0.8 * SR)]) == pytest.approx(_rms(x), rel=0.01)


def test_resample_moves_pitch_formants_and_length_together():
    sid = sampler.register(_tone(220, 1.0))
    up = Synth(_patch(sid)).note(A3 + 12, 1.0)
    assert _f0(up, 0.05, 0.4) == pytest.approx(440, abs=3)
    # an octave up reads twice as fast: the one-second tone ends at 0.5 s
    assert _rms(up[int(0.55 * SR) :]) < 1e-3 < _rms(up[: int(0.45 * SR)])
    down = Synth(_patch(sid)).note(A3 - 12, 1.0)
    assert _f0(down) == pytest.approx(110, abs=3)


def test_key_tracking_off_plays_every_key_at_the_root_pitch():
    sid = sampler.register(_tone(220))
    for key in (A3 - 7, A3 + 5, A3 + 19):
        y = Synth(_patch(sid, sampler_keytrack="off")).note(key, 0.6)
        assert _f0(y) == pytest.approx(220, abs=3)


def test_formant_mode_keeps_the_timbre_when_shifting_far():
    """An octave up by resampling doubles the spectral centroid; the
    formant mode moves the pitch just as far and the centroid barely."""
    sid = sampler.register(_vowel())
    here = _centroid(Synth(_patch(sid)).note(A3, 0.6))
    # at the root the grain train is the recording (measured 1316 vs 1318 Hz)
    kept_here = _centroid(Synth(_patch(sid, sampler_mode="formant")).note(A3, 0.6))
    assert kept_here == pytest.approx(here, rel=0.02)
    # measured (octaves of centroid movement, tape / formant): +12 0.96 /
    # 0.27, -12 -0.83 / 0.02, +24 1.81 / 0.56
    for shift in (12, -12, 24):
        tape = Synth(_patch(sid)).note(A3 + shift, 0.6)
        kept = Synth(_patch(sid, sampler_mode="formant")).note(A3 + shift, 0.6)
        moved = abs(np.log2(_centroid(tape) / here))
        stayed = abs(np.log2(_centroid(kept) / here))
        assert moved > 0.6 * abs(shift) / 12, shift
        assert stayed < 0.35 * moved, shift
        # level held within ~3 dB across the shift
        ref = _rms(Synth(_patch(sid, sampler_mode="formant")).note(A3, 0.6))
        assert abs(20 * np.log10(_rms(kept) / ref)) < 3.5, shift
    # and the pitch really moves: an octave up, the lines sit on 440 Hz
    # multiples; the source's odd 220 Hz harmonics are gone
    kept = Synth(_patch(sid, sampler_mode="formant")).note(A3 + 12, 0.6)
    m = kept.mean(axis=1)[int(0.05 * SR) : int(0.45 * SR)]
    spec = np.abs(np.fft.rfft(m * np.hanning(len(m)), 1 << 17))
    hz = np.fft.rfftfreq(1 << 17, 1 / SR)

    def line(f: float) -> float:
        i = int(np.argmin(np.abs(hz - f)))
        return float(spec[i - 3 : i + 4].max())

    even = sum(line(440.0 * k) for k in range(1, 10))
    odd = sum(line(220.0 * (2 * k + 1)) for k in range(0, 9))
    assert odd < 0.1 * even


def test_pitch_is_array_safe_vibrato_glide_and_bend_move_it():
    sid = sampler.register(_tone(220, 2.0))
    bent = Synth(_patch(sid, pitch_bend=1.0, bend_range=12.0)).note(A3, 0.6)
    assert _f0(bent) == pytest.approx(440, abs=3)
    curve = np.linspace(0.0, 1.0, int(0.6 * SR))  # a drawn bend
    swept = Synth(_patch(sid, pitch_bend=curve, bend_range=12.0)).note(A3, 0.6)
    assert _f0(swept, 0.02, 0.12) < _f0(swept, 0.45, 0.58)
    vib = Synth(_patch(sid, vibrato_depth=0.5, vibrato_delay=0.0)).note(A3, 0.6)
    assert np.all(np.isfinite(vib)) and _rms(vib) > 0.1
    glide = Synth(_patch(sid, glide=0.2)).note(A3, 0.6, from_pitch=A3 - 12)
    assert _f0(glide, 0.0, 0.05) < _f0(glide, 0.3, 0.55)


# --- time -----------------------------------------------------------------------


def test_stretch_changes_length_not_pitch():
    sid = sampler.register(_tone(220, 0.5))
    for factor in (2.0, 0.5):
        y = Synth(_patch(sid, sampler_stretch=factor)).note(A3, 1.5)
        env = np.abs(y.mean(axis=1))
        last = np.nonzero(env > 1e-3)[0][-1] / SR
        assert last == pytest.approx(0.5 * factor, abs=0.06), factor
        assert _f0(y, 0.05, 0.2) == pytest.approx(220, abs=3)
        ref = _rms(sampler.get(sid))
        assert abs(20 * np.log10(_rms(y[int(0.05 * SR) : int(0.2 * SR)]) / ref)) < 1.5


def test_a_drawn_stretch_curve_renders():
    sid = sampler.register(_tone(220, 0.5))
    curve = np.linspace(1.0, 3.0, int(0.8 * SR))
    y = Synth(_patch(sid, sampler_stretch=curve)).note(A3, 0.8)
    assert np.all(np.isfinite(y)) and _rms(y) > 0.1


def test_a_loop_sustains_past_the_sample_without_a_seam():
    sid = sampler.register(_tone(220, 0.5))
    p = _patch(sid, sampler_loop="on", sampler_loop_start=0.2, sampler_loop_end=0.7)
    y = Synth(p).note(A3, 3.0, gate=2.9)
    tail = y[int(1.0 * SR) : int(2.8 * SR), 0]
    assert _rms(tail) > 0.3
    # no click at the loop point: the sample-to-sample step stays that of
    # the 220 Hz sine itself (max 2*pi*220/SR * amp)
    step = np.max(np.abs(np.diff(tail)))
    assert step < 1.2 * 2 * np.pi * 220 / SR * 0.5
    # without the loop the one-shot ends with the sample
    one = Synth(_patch(sid)).note(A3, 3.0, gate=2.9)
    assert _rms(one[int(0.6 * SR) : int(2.8 * SR)]) < 1e-4


def test_a_start_point_fades_in_whether_or_not_the_sample_loops():
    """A start inside the audio fades its first 2 ms in. With the loop on
    it did not: a degenerate loop faded the buffer's first sample instead
    of the start point, a real one faded nothing."""
    sid = sampler.register(np.full((SR, 2), 0.5))
    x = sampler.get(sid)
    one, s0 = sampler._whole_source(x, sampler.Settings(sample=sid, start=0.5))
    at = s0 + np.arange(200.0)
    want = one.read(at)
    assert want[0, 0] == 0.0 and want[150, 0] == pytest.approx(0.5, abs=1e-4)
    loops = {
        "around the start": dict(loop_start=0.0, loop_end=1.0),
        "after the start": dict(loop_start=0.7, loop_end=0.9),
        "too short to loop": dict(loop_start=0.6, loop_end=0.6),
    }
    for name, kw in loops.items():
        st = sampler.Settings(sample=sid, start=0.5, loop="on", **kw)
        src, got_s0 = sampler._whole_source(x, st)
        assert got_s0 == s0, name
        np.testing.assert_allclose(src.read(at), want, atol=1e-12, err_msg=name)
    # the fade is the first pass's alone: every later pass of a loop that
    # holds the start point reads the audio itself
    st = sampler.Settings(sample=sid, start=0.5, loop="on")
    src, _ = sampler._whole_source(x, st)
    later = src.read(s0 + np.arange(0.5 * SR, 3.5 * SR))
    assert np.all(np.abs(later - 0.5) < 1e-4)
    # and a start at the top of the sample is the loop it always was
    plain, _ = sampler._whole_source(x, sampler.Settings(sample=sid, loop="on"))
    assert (plain.lo, plain.hi, len(plain.buf)) == (0, SR, SR + 1)


# --- chops ----------------------------------------------------------------------


def test_slices_sit_on_the_keys_from_the_root_and_wrap():
    # four distinct pitches, one per quarter
    x = np.concatenate([_tone(hz, 0.25) for hz in (200, 300, 400, 500)])
    sid = sampler.register(x)
    p = _patch(sid, sampler_slices=sampler.format_slices(sampler.grid_slices(4)))
    for i, hz in enumerate((200, 300, 400, 500)):
        y = Synth(p).note(A3 + i, 0.3)
        assert _f0(y, 0.02, 0.2) == pytest.approx(hz, abs=5), i
    wrapped = Synth(p).note(A3 + 5, 0.3)  # 5 % 4 = slice 1
    assert _f0(wrapped, 0.02, 0.2) == pytest.approx(300, abs=5)
    # a slice ends where it ends: nothing of the next one
    assert _rms(Synth(p).note(A3, 0.6)[int(0.3 * SR) :]) < 1e-3


def test_each_slice_has_its_own_pitch_and_reverse():
    ramp = np.linspace(-0.5, 0.5, SR // 2)  # a one-way ramp shows direction
    sid = sampler.register(np.column_stack([ramp, ramp]))
    sl = sampler.grid_slices(2)
    sl[0] = replace(sl[0], rev=True)
    sl[1] = replace(sl[1], pitch=12)
    p = _patch(sid, sampler_slices=sampler.format_slices(sl))
    rev = _osc(p, A3, 0.3)[:, 0]
    mid = slice(int(0.02 * SR), int(0.2 * SR))
    assert np.all(np.diff(rev[mid]) < 0)  # the rising ramp, played backwards
    up = _osc(p, A3 + 1, 0.3)[:, 0]
    # an octave up: the quarter-second half plays in an eighth
    assert np.abs(up[int(0.14 * SR) :]).max() < 1e-3 < np.abs(up[int(0.05 * SR)])


def test_transient_chops_find_the_hits_and_nudge_keeps_order():
    n = 2 * SR
    x = np.zeros(n)
    hits = [0.0, 0.4, 0.9, 1.5]
    rng = np.random.default_rng(1)
    for t in hits:
        i = int(t * SR)
        x[i : i + 4000] += rng.standard_normal(4000) * np.exp(-np.arange(4000) / 600)
    sid = sampler.register(x * 0.5)
    found = sampler.transient_slices(sid)
    assert len(found) == len(hits)
    for s, t in zip(found, hits, strict=True):
        assert t - 0.012 <= s.a * 2.0 <= t  # just ahead of the attack
    moved = sampler.nudge(found, 2, 0.01)
    assert moved[2].a == pytest.approx(found[2].a + 0.01)
    assert moved[1].b == moved[2].a  # the neighbour follows
    far = sampler.nudge(found, 2, -5.0)  # never past the previous start
    assert far[2].a > far[1].a


def test_transients_hear_a_kick_after_a_snare():
    """Spectral flux alone heard the snares of a kick/snare beat and missed
    every kick (its energy is in a few low bins); the level rise finds it."""
    rng = np.random.default_rng(3)
    t = np.arange(int(0.3 * SR)) / SR
    kick = np.sin(2 * np.pi * (50 + 120 * np.exp(-t * 30)) * t) * np.exp(-t * 9)
    snare = rng.standard_normal(len(t)) * np.exp(-t * 25) * 0.6
    beat = np.zeros(2 * SR)
    for i, hit in enumerate([kick, snare, kick, snare, kick, kick, snare, snare]):
        at = beat[int(i * 0.25 * SR) :][: len(hit)]
        at += hit[: len(at)]
    found = sampler.transient_slices(sampler.register(beat * 0.7))
    assert [round(s.a * 8) for s in found] == list(range(8))


# --- registry --------------------------------------------------------------------


def test_breeding_never_draws_a_sampler_field():
    names = [n for n in PARAMS if n.startswith("sampler_")]
    assert len(names) == 13  # + sampler_zones
    assert not set(names) & set(GENES)
    for n in names:
        if PARAMS[n].kind != "meta":
            assert PARAMS[n].nogene
    assert "sampler" not in PARAMS["osc1"].gene
    rng = np.random.default_rng(0)
    base = _patch("0123456789abcdef", sampler_start=0.3, sampler_slices="0,0.5,0,0")
    for _ in range(30):
        child = random_patch(Patch(), rng=rng)
        assert all(getattr(child, n) == getattr(Patch(), n) for n in names)
        mutated, _ = mutate(base, 0.8, rng=rng)
        assert all(getattr(mutated, n) == getattr(base, n) for n in names)


def test_every_other_shape_still_renders_mono_voices():
    """Only a sampler voice is stereo: the oscillator stack of every other
    patch keeps its mono shape (and so its render)."""
    s = Synth(Patch(osc2="square", sub_level=0.3, noise_level=0.1))
    assert s._osc_stack(np.full(512, 220.0), 512).shape == (512,)


# --- pitch detection ------------------------------------------------------------


def _saw(hz: float, seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    x = (2.0 * ((hz * t) % 1.0) - 1.0) * 0.4
    return np.column_stack([x, x])


@pytest.mark.parametrize("midi", [28, 36, 45, 57, 60, 64, 72, 84, 93])
@pytest.mark.parametrize("wave", ["sine", "saw"])
def test_detect_pitch_finds_sines_and_saws(midi, wave):
    """Well inside a semitone (in fact within a few cents) from E1 to A6,
    and sure of it."""
    hz = sampler.midi_hz(midi)
    audio = _tone(hz) if wave == "sine" else _saw(hz)
    found, confidence = sampler.detect_pitch(audio)
    assert abs(found - midi) < 0.1, (wave, midi, found)
    assert confidence > 0.9


def test_detect_pitch_is_not_quantised():
    """Between keys it says so: a quarter-tone sharp reads as one."""
    found, confidence = sampler.detect_pitch(_tone(sampler.midi_hz(60.5)))
    assert abs(found - 60.5) < 0.05 and confidence > 0.9


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_noise_has_no_clear_pitch(seed):
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(SR) * 0.3
    assert sampler.detect_pitch(white)[1] < 0.3
    from synthlab.core import colour_noise

    for colour in ("pink", "brown"):
        x = colour_noise(rng.standard_normal(SR), colour)
        assert sampler.detect_pitch(x / np.abs(x).max() * 0.5)[1] < 0.3, colour


@pytest.mark.parametrize(
    "shape,note,midi",
    [
        ("pluck", "A2", 45),
        ("pluck", "C4", 60),
        ("supersaw", "A1", 33),
        ("supersaw", "E5", 76),
        ("fm", "C4", 60),
        ("wavetable", "A2", 45),
    ],
)
def test_detect_pitch_on_engine_renders(shape, note, midi):
    """Real instrument notes (no fixture WAVs ship with the tests): a
    Karplus-Strong pluck that decays, a detuned supersaw, FM, a wavetable —
    each rounds to the note that was played."""
    audio = Synth(Patch(osc1=shape)).note(note, 1.0)
    found, confidence = sampler.detect_pitch(audio)
    assert round(found) == midi and abs(found - midi) < 0.5, (shape, note, found)
    assert confidence > 0.8


def test_detect_pitch_on_the_loud_part_of_a_recording():
    """Silence around a note (a take's lead-in and tail) doesn't move it."""
    tone = _tone(sampler.midi_hz(50), 0.6)
    quiet = np.zeros((int(0.8 * SR), 2))
    found, confidence = sampler.detect_pitch(np.concatenate([quiet, tone, quiet]))
    assert abs(found - 50) < 0.1 and confidence > 0.9


def test_detect_pitch_of_nothing():
    for audio in (np.zeros((SR, 2)), np.zeros(100), _tone(220.0, 0.02)):
        found, confidence = sampler.detect_pitch(audio)
        assert np.isnan(found) and confidence == 0.0


def test_sample_pitch_reads_the_store_and_not_a_missing_sample():
    sid = sampler.register(_tone(220.0))
    assert round(sampler.sample_pitch(sid)[0]) == A3
    assert sampler.sample_pitch("0" * 16) == (pytest.approx(np.nan, nan_ok=True), 0.0)
    assert sampler.sample_pitch("nope")[1] == 0.0


# --- moving a slice's start (a dragged marker) -------------------------------------


def test_move_start_clamps_and_carries_the_touching_neighbour():
    grid = sampler.grid_slices(4)  # 0, .25, .5, .75
    moved = sampler.move_start(grid, 2, 0.4)
    assert moved[2].a == 0.4 and moved[1].b == 0.4 and moved[2].b == 0.75
    assert moved[0] == grid[0] and moved[3] == grid[3]
    low = sampler.move_start(grid, 2, -1.0, min_len=0.05)
    assert low[2].a == pytest.approx(0.3) and low[1].b == pytest.approx(0.3)
    high = sampler.move_start(grid, 2, 2.0, min_len=0.05)
    assert high[2].a == pytest.approx(0.7)
    assert sampler.move_start(grid, 0, -1.0)[0].a == 0.0
    # a gap before the slice (a transient chop's) is not closed by the move
    gapped = [sampler.Slice(0.0, 0.2), sampler.Slice(0.3, 0.6)]
    moved = sampler.move_start(gapped, 1, 0.4)
    assert moved[1].a == 0.4 and moved[0].b == 0.2
    # nothing to move, or nowhere real to put it
    assert sampler.move_start(grid, 9, 0.5) == grid
    assert sampler.move_start(grid, 1, float("nan")) == grid


def test_start_bounds_are_what_the_clamp_uses():
    grid = sampler.grid_slices(4)
    assert sampler.start_bounds(grid, 0, 0.01) == (0.0, 0.24)
    lo, hi = sampler.start_bounds(grid, 3, 0.01)
    assert lo == pytest.approx(0.51) and hi == pytest.approx(0.99)


# --- the store's file form (a project bundle carries it) --------------------------


def test_wav_bytes_round_trip_to_the_same_id():
    sid = sampler.register(_tone(220.0, 0.3))
    data = sampler.wav_bytes(sid)
    x = sampler.from_wav_bytes(data)
    assert x is not None and sampler.sample_id(x) == sid
    assert sampler.wav_bytes("0" * 16) is None


def test_from_wav_bytes_is_strict():
    import io
    import wave as wavmod

    def wav(channels=2, width=2, rate=SR, frames=100):
        buf = io.BytesIO()
        with wavmod.open(buf, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(width)
            w.setframerate(rate)
            w.writeframes(b"\0" * frames * channels * width)
        return buf.getvalue()

    assert sampler.from_wav_bytes(wav()) is not None
    for bad in (
        wav(channels=1),
        wav(width=3),
        wav(rate=48000),
        wav()[:-10],  # truncated data
        b"not a wav",
        b"",
    ):
        assert sampler.from_wav_bytes(bad) is None


# --- zones: a multi-sample instrument -------------------------------------------


def _zoned(zones: str, **kw) -> Patch:
    return _patch("", sampler_zones=zones, **kw)


def test_zones_parse_clean_and_round_trip():
    a, b = sampler.register(_tone(110)), sampler.register(_tone(220))
    text = f"{a},0,59,45;{b},60,127,57,0,63,1,-3.5,12.5,0.1,0.9,0.2,0.6"
    zones = sampler.parse_zones(text)
    assert [(z.lo, z.hi, z.root) for z in zones] == [(0, 59, 45), (60, 127, 57)]
    z = zones[1]
    assert (z.vlo, z.vhi, z.rr, z.gain, z.tune) == (0, 63, 1, -3.5, 12.5)
    assert (z.start, z.end, z.loop_start, z.loop_end) == (0.1, 0.9, 0.2, 0.6)
    assert zones[0].start is None and not zones[0].own_loop
    assert sampler.parse_zones(sampler.format_zones(zones)) == zones
    # trailing defaults are left off; a written map is its own clean form
    assert sampler.format_zones(zones[:1]) == f"{a},0,59,45"
    assert sampler.clean_zones(text) == sampler.format_zones(zones)
    # hostile: bad ids, junk numbers, backwards ranges, out of range values
    junk = (
        f"../x,0,1;{a},90,20,999,127,0,99,80,-900;{a},nan;"
        f"{b},1,2,3,4,5,6,7,8,9,10,11,12,13"
    )
    (z,) = sampler.parse_zones(junk)
    assert (z.lo, z.hi, z.root, z.vlo, z.vhi) == (20, 90, 127, 0, 127)
    assert (z.rr, z.gain, z.tune) == (15, 12.0, -100.0)
    # a half-given or empty span is the patch's
    (z,) = sampler.parse_zones(f"{a},0,127,60,0,127,0,0,0,0.5,,0.3,0.3")
    assert z.start is None and z.end is None and not z.own_loop
    assert sampler.parse_zones(";".join([f"{a}"] * 99)) == [sampler.Zone(a)] * 64
    p = clamp(_zoned(junk))
    assert p.sampler_zones == sampler.format_zones(sampler.parse_zones(junk))


def test_the_pick_by_key_velocity_and_take():
    Z = sampler.Zone
    zones = [
        Z("a", 0, 59),
        Z("b", 60, 127),
        Z("c", 60, 127, rr=1),
        Z("d", 60, 127, vlo=100),
    ]
    pick = sampler.pick_zones
    assert pick(zones, 59, 127, 0) == [(0, 1.0)]
    assert pick(zones, 59, 127, 5) == [(0, 1.0)]  # one take: every strike
    # takes alternate; d (100..127) crossfades over b's range from 100 up
    assert pick(zones, 60, 50, 0) == [(1, 1.0)]
    assert pick(zones, 60, 50, 1) == [(2, 1.0)]
    assert pick(zones, 60, 50, 2) == [(1, 1.0)]
    (s, ws), (h, wh) = pick(zones, 60, 110, 0)
    assert (s, h) == (1, 3) and ws**2 + wh**2 == pytest.approx(1.0)
    assert pick(zones, 60, 127, 0) == [(3, 1.0)]  # the top of the overlap
    assert pick(zones, 60, 100, 0) == [(1, 1.0)]  # its bottom
    assert pick([Z("a", 10, 20)], 9, 127, 0) == []  # outside every zone
    assert pick([Z("a", vhi=60)], 60, 61, 0) == []
    assert sampler.velocity_step(0.9) == 114 and sampler.velocity_step(1.0) == 127
    assert sampler.key_step(sampler.midi_hz(61)) == 61


def test_one_zone_over_the_keyboard_is_the_sample_itself():
    """A map of one zone at 0 dB is the single-sample sound, bit for bit —
    in both modes, through the whole voice."""
    sid = sampler.register(_tone(220))
    for mode in ("resample", "formant"):
        one = _patch(sid, sampler_mode=mode)
        zoned = replace(one, sampler_zones=f"{sid},0,127,{A3}")
        for key in (A3 - 5, A3, A3 + 7):
            a = Synth(one).note(key, 0.5, 0.8)
            b = Synth(zoned).note(key, 0.5, 0.8)
            assert np.array_equal(a, b), (mode, key)


def test_each_key_plays_its_own_zone_at_its_own_root():
    lo, hi = sampler.register(_tone(110)), sampler.register(_tone(440))
    p = _zoned(f"{lo},0,59,45;{hi},60,127,69")
    assert _f0(Synth(p).note(45, 0.6)) == pytest.approx(110, abs=3)
    assert _f0(Synth(p).note(69, 0.6)) == pytest.approx(440, abs=3)
    # A3 is lo's, an octave over its root
    assert _f0(Synth(p).note(57, 0.6)) == pytest.approx(220, abs=3)
    # a key no zone holds is silence; a missing zone's sample too
    gap = _zoned(f"{lo},0,40,45;{hi},80,127,69")
    assert not np.any(Synth(gap).note(60, 0.3))
    assert not np.any(Synth(_zoned("0123456789abcdef,0,127,60")).note(60, 0.3))
    # the zone's fine tune and gain
    y = Synth(_zoned(f"{hi},0,127,69,0,127,0,-6,100")).note(69, 0.6)
    assert _f0(y) == pytest.approx(440 * 2 ** (1 / 12), abs=3)
    flat = Synth(_zoned(f"{hi},0,127,69")).note(69, 0.6)
    assert _rms(y) / _rms(flat) == pytest.approx(10 ** (-6 / 20), rel=0.02)


def test_velocity_layers_and_their_crossfade():
    soft = sampler.register(_tone(220, amp=0.2))
    hard = sampler.register(_tone(220, amp=0.8))
    p = _zoned(f"{soft},0,127,{A3},0,90;{hard},0,127,{A3},60,127")

    def level(v):  # Synth.note's velocity also scales the amp: divided out
        return _rms(Synth(p).note(A3, 0.5, v)[: int(0.4 * SR)]) / v

    rms = {k: _rms(sampler.get(k)) for k in (soft, hard)}
    assert level(0.3) == pytest.approx(rms[soft], rel=0.03)
    assert level(1.0) == pytest.approx(rms[hard], rel=0.03)
    # half way through the overlap: both, equal power (the same tone, so
    # their levels add)
    both = np.sqrt(0.5) * (rms[soft] + rms[hard])
    assert level(75 / 127) == pytest.approx(both, rel=0.03)
    # no step on the way across: from the soft layer's level up, never
    # past what two equal-power gains of the same tone can add to
    top = np.hypot(rms[soft], rms[hard])
    for v in range(55, 100, 3):
        assert rms[soft] * 0.97 <= level(v / 127) <= top * 1.03, v


def test_round_robin_is_deterministic_and_alternates_in_a_phrase():
    """A repeated key takes the next take on each strike, the same way on
    every render; the note memo keeps each take its own render; another
    key counts on its own; and a patch without takes renders exactly as
    it always did (nothing passed)."""
    from synthlab.sequencer import Song

    t1, t2 = sampler.register(_tone(220)), sampler.register(_tone(330))
    p = _zoned(f"{t1},0,127,{A3};{t2},0,127,{A3},0,127,1")
    assert sampler.round_robin(p) and not sampler.round_robin(_patch(t1))
    assert _f0(Synth(p).note(A3, 0.4, rr=0)) == pytest.approx(220, abs=3)
    assert _f0(Synth(p).note(A3, 0.4, rr=1)) == pytest.approx(330, abs=3)
    assert np.array_equal(Synth(p).note(A3, 0.4, rr=2), Synth(p).note(A3, 0.4, rr=0))

    def phrase():
        song = Song(bpm=120)
        seq = [(A3, 1, 0.9), (A3, 1, 0.9), (A3, 1, 0.9), (A3 + 12, 1, 0.9)]
        song.notes("x", Synth(p), seq)
        return song.render(normalize=0, tail_beats=0)

    a = phrase()
    assert np.array_equal(a, phrase())
    beat = int(0.5 * SR)
    f = [_f0(a[k * beat : (k + 1) * beat], 0.02, 0.4) for k in range(4)]
    assert f[0] == pytest.approx(220, abs=3) and f[2] == pytest.approx(220, abs=3)
    assert f[1] == pytest.approx(330, abs=3)
    # A4 is its key's first strike: take 0, an octave up
    assert f[3] == pytest.approx(440, abs=4)


def test_round_robin_through_the_studio_schedulers():
    """The loop's per-row lane, its timeline (a slide) and a one-voice
    patch count the strikes the same way: the k-th note of a key in the
    lane plays take k (modulo the takes)."""
    from synthlab.studio.arrange import build_song
    from synthlab.studio.state import Arrangement

    t1, t2 = sampler.register(_tone(220)), sampler.register(_tone(330))
    p = _zoned(f"{t1},0,127,{A3};{t2},0,127,{A3},0,127,1", release=0.01)
    cells = [["A3", s] for s in (0, 4, 8, 12)]
    step = int(0.25 * 60 / 124 * SR)
    for kw in ({}, {"slides": [8]}):
        arr = replace(Arrangement(), cells=cells, steps_per_bar=16, bpm=124.0, **kw)
        for patch in (p, replace(p, voice_mode="mono")):
            y = build_song(patch, arr).render(normalize=0, tail_beats=0)
            f = [_f0(y[s * step : (s + 3) * step], 0.0, 3 * step / SR) for s in (0, 4)]
            want = [pytest.approx(220, abs=8), pytest.approx(330, abs=8)]
            assert f == want, (kw, patch.voice_mode)


def test_place_zone_and_spread():
    Z = sampler.Zone
    zones = sampler.place_zone([], Z("a", root=48))
    assert [(z.lo, z.hi) for z in zones] == [(0, 127)]  # the first: everything
    zones = sampler.place_zone(zones, Z("b", root=60))  # splits a at the midpoint
    assert [(z.lo, z.hi) for z in zones] == [(0, 54), (55, 127)]
    zones = sampler.place_zone(zones, Z("c", root=60))  # same root: its next take
    assert (zones[2].lo, zones[2].hi, zones[2].rr) == (55, 127, 1)
    # a soft layer is its own layer: it spans everything at its velocities
    zones = sampler.place_zone(zones, Z("d", root=60, vhi=50))
    assert (zones[3].lo, zones[3].hi) == (0, 127)
    # spread re-lays each layer by its roots
    out = sampler.spread([Z("a", 5, 5, 40), Z("b", 9, 9, 52), Z("c", 1, 1, 70)])
    assert [(z.lo, z.hi) for z in out] == [(0, 46), (47, 61), (62, 127)]
    # chops as zones: each slice alone on its key, at its own pitch
    sl = sampler.parse_slices("0,0.5,0,0;0.5,1,3,0")
    zz = sampler.zones_from_slices("e", sl, 60)
    assert [(z.lo, z.hi, z.root, z.start, z.end) for z in zz] == [
        (60, 60, 60, 0.0, 0.5),
        (61, 61, 58, 0.5, 1.0),
    ]


def test_a_zone_plays_its_own_span_and_loop():
    ramp = np.linspace(-0.5, 0.5, SR)
    sid = sampler.register(np.column_stack([ramp, ramp]))
    x = sampler.get(sid)
    whole = _osc(_zoned(f"{sid},0,127,{A3}"), A3, 0.2)
    half = _osc(_zoned(f"{sid},0,127,{A3},0,127,0,0,0,0.5,1"), A3, 0.2)
    assert half[200, 0] == pytest.approx(x[SR // 2 + 200, 0], abs=1e-3)
    assert whole[200, 0] == pytest.approx(x[200, 0], abs=1e-3)
    looped = _osc(_zoned(f"{sid},0,127,{A3},0,127,0,0,0,,,0.1,0.2"), A3, 2.0)
    assert _rms(looped[int(1.5 * SR) :]) > 0.01  # past the 1 s sample: it loops
