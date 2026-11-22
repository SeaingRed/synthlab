"""The spectral oscillator (synthlab/spectral.py): the sampler's sample
resynthesised by a phase vocoder, time and pitch apart. Opt-in — a patch
that plays no "spectral" oscillator never reaches its code (pinned by the
gate below), so every older render and key stands — and held to what each
field says: pitch to the cent, the scan's time, the timbre shift's formant,
the partial cuts, the transients, the cache."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from synthlab import sampler, spectral
from synthlab import synth as synth_mod
from synthlab.core import SR, note_to_hz
from synthlab.params import HAND_SHAPES, MOD_DESTINATIONS, MOD_GENE_DESTINATIONS, PARAMS
from synthlab.spectral import Settings
from synthlab.studio.factory import FACTORY, get_factory
from synthlab.studio.library import LIBRARY, get_library
from synthlab.synth import Patch, Synth

SPECTRAL_FIELDS = (
    "spectral_pos spectral_scan spectral_timbre spectral_formants "
    "spectral_low spectral_high spectral_transient"
).split()

ROOT = 57  # A3
A3 = note_to_hz(ROOT)
T = np.arange(SR) / SR


def _register(x: np.ndarray) -> str:
    return sampler.register(x)


SINE = _register(0.5 * np.sin(2 * np.pi * A3 * T))
# a band-limited saw: every harmonic under 20 kHz at 1/h
SAW = _register(
    0.2 * sum(np.sin(2 * np.pi * h * A3 * T) / h for h in range(1, int(2e4 / A3)))
)
# A3 for half a second, then E4
E4 = A3 * 2 ** (7 / 12)
TWO = _register(
    np.concatenate(
        [
            0.4 * np.sin(2 * np.pi * A3 * T[: SR // 2]),
            0.4 * np.sin(2 * np.pi * E4 * T[: SR // 2]),
        ]
    )
)


def _play(sid: str, n: int = SR, key: float = A3, **kw) -> np.ndarray:
    st = Settings(sample=sid, root=ROOT, **kw)
    return spectral.render(np.full(n, key), n, SR, st, key)


def _peak_hz(y: np.ndarray, lo: float, hi: float) -> float:
    """The strongest frequency in lo..hi, parabolic over a long FFT."""
    size = 1 << 20
    Y = np.abs(np.fft.rfft(y * np.hanning(len(y)), n=size))
    fr = np.fft.rfftfreq(size, 1 / SR)
    m = (fr > lo) & (fr < hi)
    k = np.flatnonzero(m)[np.argmax(Y[m])]
    a, b, c = np.log(Y[k - 1 : k + 2])
    return (k + 0.5 * (a - c) / (a - 2 * b + c)) * SR / size


def _cents(hz: float, want: float) -> float:
    return 1200 * np.log2(hz / want)


def _level(y: np.ndarray, hz: float) -> float:
    t = np.arange(len(y)) / SR
    return abs(np.sum(y * np.hanning(len(y)) * np.exp(-2j * np.pi * hz * t)))


# --- the defaults: the old engine, untouched -------------------------------------


def test_patches_without_the_shape_never_reach_it(monkeypatch):
    """Every factory and library sound, every PRESET and the default patch
    render without touching the spectral code: the new fields cannot move
    their audio, so RENDER_REV stands."""

    def boom(*a, **k):
        raise AssertionError("the spectral path ran on a patch without the shape")

    monkeypatch.setattr(spectral, "render", boom)
    monkeypatch.setattr(spectral, "analysis", boom)
    monkeypatch.setattr(spectral.Settings, "of", boom)
    rows = [get_factory(e["name"]) for e in FACTORY]
    rows += [get_library(e["name"]) for e in LIBRARY]
    for p in [*rows, *synth_mod.PRESETS.values(), Patch()]:
        assert "spectral" not in (p.osc1, p.osc2)
        Synth(p).note("C3", 0.2, gate=0.15)


def test_the_fields_are_appended_hand_made_and_out_of_older_keys():
    from synthlab.studio.render import KEY_ADDED_FIELDS, keyed_patch

    names = list(PARAMS)
    # appended as one run at the end of the registry
    assert names[-len(SPECTRAL_FIELDS) :] == SPECTRAL_FIELDS
    for name in SPECTRAL_FIELDS:
        assert PARAMS[name].gene is None and PARAMS[name].nogene
        assert KEY_ADDED_FIELDS[name] == getattr(Patch(), name)
    assert not set(SPECTRAL_FIELDS) & set(keyed_patch(Patch()))
    assert "spectral" in HAND_SHAPES
    # a route target, never a gene's choice; drawable
    assert "spectral_pos" in MOD_DESTINATIONS
    assert "spectral_pos" not in MOD_GENE_DESTINATIONS
    assert PARAMS["spectral_pos"].curve == (0.0, 1.0)


# --- what it plays -------------------------------------------------------------------


def test_no_sample_or_no_region_is_silence():
    n = 1000
    assert np.array_equal(spectral.render(np.full(n, A3), n), np.zeros(n))
    st = Settings(sample="0123456789abcdef")  # a hash this machine doesn't hold
    assert np.array_equal(spectral.render(np.full(n, A3), n, SR, st), np.zeros(n))
    st = Settings(sample=SINE, start=0.5, end=0.5)
    assert np.array_equal(spectral.render(np.full(n, A3), n, SR, st), np.zeros(n))
    p = Patch(osc1="spectral", osc2=None, filter_type="none")
    assert not np.any(Synth(p).note("C4", 0.2))


def test_length_stereo_and_determinism():
    a, b = _play(SAW, 12345, key=A3 * 1.3), _play(SAW, 12345, key=A3 * 1.3)
    assert a.shape == (12345, 2)
    assert np.array_equal(a, b)  # no random stream anywhere
    assert np.array_equal(a[:, 0], a[:, 1])  # a mono sample: one channel twice
    left = 0.4 * np.sin(2 * np.pi * A3 * T)
    right = 0.4 * np.sin(2 * np.pi * A3 * 1.5 * T)
    wide = _register(np.column_stack([left, right]))
    y = _play(wide, SR // 2)
    assert y.shape == (SR // 2, 2)
    assert _peak_hz(y[:, 0], 150, 400) == pytest.approx(A3, rel=1e-4)
    assert _peak_hz(y[:, 1], 150, 400) == pytest.approx(A3 * 1.5, rel=1e-4)
    assert spectral.analysis(wide).spectra.shape[0] == 2
    assert spectral.analysis(SAW).spectra.shape[0] == 1


@pytest.mark.parametrize("sid", [SINE, SAW], ids=["sine", "saw"])
@pytest.mark.parametrize("semis", [-12, -5, 7, 12])
def test_the_pitch_is_the_key_to_the_cent(sid, semis):
    key = A3 * 2 ** (semis / 12)
    y = _play(sid, key=key)[:, 0]
    hz = _peak_hz(y[4000:-4000], key * 0.9, key * 1.1)
    assert abs(_cents(hz, key)) < 0.5


def test_key_track_off_plays_the_recordings_pitch():
    st = Settings(sample=SINE, root=ROOT, keytrack="off")
    key = A3 * 2
    y = spectral.render(np.full(SR, key), SR, SR, st, key)[:, 0]
    assert abs(_cents(_peak_hz(y[4000:-4000], 150, 300), A3)) < 0.5


def test_a_pitch_array_is_followed_frame_by_frame():
    # an octave glide over the first half, then held: the held half plays
    # the octave, and the glide passes through the fifth on its way
    n = SR
    f = np.full(n, 2 * A3)
    f[: n // 2] = A3 * 2 ** np.linspace(0, 1, n // 2)
    y = spectral.render(f, n, SR, Settings(sample=SINE, root=ROOT), A3)[:, 0]
    assert abs(_cents(_peak_hz(y[n // 2 + 4000 : -4000], 300, 600), 2 * A3)) < 1
    mid = y[n // 4 - 1500 : n // 4 + 1500]
    assert abs(_cents(_peak_hz(mid, 200, 600), A3 * 2**0.5)) < 30


def test_the_root_and_scan_one_play_the_recording():
    # at the root, from the start, at the recording's speed, the opening
    # frames take the analysis's own phases: the band-limited saw comes back
    # to within -30 dB, -50 past the first window
    x = sampler.get(SAW)[: SR // 2, 0].astype(np.float64)
    y = _play(SAW, SR // 2)[:, 0]

    def snr(a, b):
        return 10 * np.log10(np.sum(a**2) / np.sum((b - a) ** 2))

    assert snr(x, y) > 30
    assert snr(x[2048:-2048], y[2048:-2048]) > 50


@pytest.mark.parametrize("scan", [1.0, 0.5, 0.25])
def test_the_scan_moves_time_and_not_pitch(scan):
    n = int(SR / scan)
    y = _play(TWO, n, scan=scan)[:, 0]
    # the E4 half takes over when the read reaches it: half a second of the
    # recording, 0.5 / scan of output
    b = 441
    blocks = [y[k * b : (k + 1) * b] for k in range(len(y) // b)]
    switch = next(k for k, s in enumerate(blocks) if _level(s, E4) > _level(s, A3))
    assert switch * b / SR == pytest.approx(0.5 / scan, abs=0.015)
    assert abs(_cents(_peak_hz(y[int(0.05 * n) : int(0.4 * n)], 150, 280), A3)) < 1
    assert abs(_cents(_peak_hz(y[int(0.6 * n) : int(0.95 * n)], 280, 400), E4)) < 1


def test_scan_zero_freezes_and_negative_runs_backwards():
    y = _play(TWO, SR, pos=0.75, scan=0.0)[:, 0]
    assert abs(_cents(_peak_hz(y[4000:-4000], 280, 400), E4)) < 1
    first, last = np.std(y[2000 : SR // 2]), np.std(y[SR // 2 : -2000])
    assert first == pytest.approx(last, rel=0.02)  # a steady drone
    back = _play(TWO, SR, pos=0.95, scan=-1.0)[:, 0]  # held at the start
    early, late = back[2000:8000], back[-8000:-2000]
    assert _level(early, E4) > 10 * _level(early, A3)
    assert _level(late, A3) > 10 * _level(late, E4)


def test_the_loop_wraps_and_the_span_holds():
    # from 0.4 at scan 1 the read passes the span's end after 0.6 s: off,
    # it holds the last frame (the E4 sine, still sounding); with the loop
    # on the first half (A3), it comes back round to the A3
    n = SR
    held = _play(TWO, n, pos=0.4)[:, 0]
    assert _level(held[-6000:], E4) > 10 * _level(held[-6000:], A3)
    looped = _play(TWO, n, loop="on", loop_start=0.0, loop_end=0.5, pos=0.0)[:, 0]
    for at in (0.2, 0.7):  # every half second the loop starts again
        s = looped[int(at * SR) : int(at * SR) + 4000]
        assert _level(s, A3) > 10 * _level(s, E4)


def _formant_sample(f0: float = 110.0, fc: float = 1000.0) -> str:
    t = np.arange(SR) / SR
    hs = range(1, int(1.5e4 / f0))
    amp = [np.exp(-0.5 * ((h * f0 - fc) / 150.0) ** 2) + 0.02 for h in hs]
    sig = sum(a * np.sin(2 * np.pi * h * f0 * t) for a, h in zip(amp, hs, strict=True))
    return _register(0.05 * sig)


FORMANT = _formant_sample()


def _formant_hz(y: np.ndarray, f0: float) -> float:
    """The peak of the harmonics' levels, parabolic over the three round
    the loudest."""
    hs = np.arange(1, int(4000 / f0))
    levels = np.array([_level(y, h * f0) for h in hs])
    k = int(np.argmax(levels))
    a, b, c = np.log(levels[k - 1 : k + 2])
    return (hs[k] + 0.5 * (a - c) / (a - 2 * b + c)) * f0


@pytest.mark.parametrize(
    ("formants", "semis", "timbre", "want"),
    [
        ("move", 0, 5, 1000 * 2 ** (5 / 12)),
        ("move", 0, -5, 1000 * 2 ** (-5 / 12)),
        ("move", 0, 12, 2000.0),
        ("move", 7, 0, 1000 * 2 ** (7 / 12)),  # rides the pitch
        ("keep", 7, 0, 1000.0),  # stays where the recording has it
        ("keep", -5, 0, 1000.0),
        ("keep", 7, 5, 1000 * 2 ** (5 / 12)),
    ],
)
def test_the_timbre_shift_moves_the_formant(formants, semis, timbre, want):
    f0 = 110.0
    key = f0 * 2 ** (semis / 12)
    st = Settings(sample=FORMANT, root=45, timbre=timbre, formants=formants)
    y = spectral.render(np.full(SR, key), SR, SR, st, key)[:, 0][4000:-4000]
    # within half a harmonic: the envelope is only seen at the harmonics
    assert abs(_formant_hz(y, key) - want) < key / 2
    assert abs(_cents(_peak_hz(y, key * 0.9, key * 1.1), key)) < 0.5  # pitch kept


def test_the_partial_cuts_remove_whole_partials():
    def levels(**kw):
        y = _play(SAW, SR // 2, **kw)[4000:-4000, 0]
        return np.array([_level(y, h * A3) for h in range(1, 9)])

    full = levels()
    low = levels(low=3.0)  # partials 1 and 2 cut
    assert np.all(low[:2] < full[:2] * 1e-2)
    np.testing.assert_allclose(low[2:], full[2:], rtol=0.02)
    high = levels(high=4.0)  # partials 5 up cut
    np.testing.assert_allclose(high[:4], full[:4], rtol=0.02)
    assert np.all(high[5:] < full[5:] * 1e-2)
    assert spectral.cut_gain(np.arange(5.0), 0.0, spectral.HIGH[1]) is None


def _drums() -> tuple[str, list[float]]:
    """Four bars' worth of a decaying kick every half second."""
    t = np.arange(int(0.3 * SR)) / SR
    kick = 0.9 * np.sin(2 * np.pi * np.cumsum(50 + 120 * np.exp(-t * 30)) / SR)
    kick *= np.exp(-t * 9)
    x = np.zeros(2 * SR)
    hits = [0.5, 1.0, 1.5]
    for h in [0.0, *hits]:
        x[int(h * SR) : int(h * SR) + len(kick)] += kick
    return _register(x), hits


DRUMS, HITS = _drums()


def _rise_ms(y: np.ndarray, at: float) -> float:
    w = int(0.001 * SR)
    e = np.sqrt(np.convolve(y**2, np.ones(w) / w, mode="same"))
    seg = e[int((at - 0.03) * SR) : int((at + 0.05) * SR)]
    top = seg.max()
    return (np.argmax(seg > 0.9 * top) - np.argmax(seg > 0.1 * top)) / SR * 1000


def test_transients_keep_a_stretched_attack_sharp():
    scan = 0.25
    n = int(2 * SR / scan)
    soft = _play(DRUMS, n, key=note_to_hz(ROOT), scan=scan)[:, 0]
    sharp = _play(DRUMS, n, key=note_to_hz(ROOT), scan=scan, transient=1.0)[:, 0]
    for h in HITS:
        at = h / scan  # the attack lands where the stretch puts it
        assert _rise_ms(sharp, at) < 15 < 30 < _rise_ms(soft, at)
    # the analysis found each kick, to within a millisecond
    an = spectral.analysis(DRUMS)
    found = (an.attack[an.onset > 0.5] - spectral.PRE) * spectral.HA / SR
    for h in HITS:
        assert np.min(np.abs(found - h)) < 0.001
    # at the recording's speed it changes nothing
    a = _play(DRUMS, SR, scan=1.0)
    np.testing.assert_array_equal(a, _play(DRUMS, SR, scan=1.0, transient=1.0))


def test_the_reads_are_retimed_round_an_attack():
    frames = np.arange(40) * 0.125  # a quarter of the recording's speed
    out, hit = spectral.transient_reads(frames, np.array([2.0]), 0.5)
    at = int(np.flatnonzero(hit)[0])
    assert at == 16 and out[at] == pytest.approx(2.0)
    # the attack's frames at the recording's speed, the edges held
    np.testing.assert_allclose(np.diff(out[at - 4 : at + 5]), 0.5)
    assert np.all(out[:12] <= 2.0 - spectral.ATTACK + 1e-9)
    assert np.all(np.diff(out) >= -1e-12)  # never backwards


# --- the analysis cache ---------------------------------------------------------


def test_the_analysis_is_computed_once_and_bounded(monkeypatch):
    calls = []
    real = spectral.analyse

    def counting(x):
        calls.append(len(x))
        return real(x)

    monkeypatch.setattr(spectral, "analyse", counting)
    store = spectral._Store(spectral.CACHE_BYTES)
    monkeypatch.setattr(spectral, "STORE", store)
    sid = _register(0.3 * np.sin(2 * np.pi * 300 * T))
    a = spectral.analysis(sid)
    assert spectral.analysis(sid) is a and len(calls) == 1
    assert a.nbytes == a.spectra.nbytes + a.onset.nbytes + a.attack.nbytes
    # past its bytes the least recently used goes
    store.max_bytes = int(a.nbytes * 1.5)
    other = _register(0.3 * np.sin(2 * np.pi * 400 * T))
    spectral.analysis(other)
    spectral.analysis(sid)
    assert len(calls) == 3


# --- through the synth ----------------------------------------------------------------


def _spectral_patch(**kw) -> Patch:
    base = {
        "osc1": "spectral",
        "osc2": None,
        "filter_type": "none",
        "sampler_sample": SAW,
        "sampler_root": ROOT,
        "attack": 0.002,
        "sustain": 1.0,
        "release": 0.05,
    }
    return Patch(**{**base, **kw})


def test_the_patch_plays_it_through_the_voice():
    p = _spectral_patch(spectral_scan=0.5, spectral_timbre=3.0)
    y = Synth(p).note("C4", 0.5)
    assert y.shape[1] == 2 and np.all(np.isfinite(y)) and np.abs(y).max() > 0.05
    assert np.array_equal(y, Synth(p).note("C4", 0.5))
    # NumPy's own loop (the native stack never takes the shape)
    s = Synth(p)
    s._native_stack = lambda *a, **k: None
    np.testing.assert_array_equal(s.note("C4", 0.5), y)


def test_a_route_and_a_curve_move_the_position():
    p = _spectral_patch(sampler_sample=TWO, spectral_pos=0.2, spectral_scan=0.0)
    still = Synth(p).note("A3", 0.5)
    routed = replace(p, mod1_src="lfo", mod1_dst="spectral_pos", mod1_amt=0.5)
    assert not np.allclose(Synth(routed).note("A3", 0.5), still)
    drawn = replace(p, spectral_pos=np.linspace(0.2, 0.9, 512))
    assert not np.allclose(Synth(drawn).note("A3", 0.5), still)
