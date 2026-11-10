"""The oscillator extras: hard sync, the 4-operator FM shape, eight more
wavetables, analog drift. Each is opt-in — its defaults leave the audio of
every patch without it bit for bit as if it did not exist (pinned here by the
gate: a render of a patch with the feature off never reaches its code) —
and each is held to what it claims: sync band-limited against a naive
reset, fm4's algorithms wired as named, the tables band-limited per mip,
drift seeded from the note and slow."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from synthlab import core
from synthlab import synth as synth_mod
from synthlab.core import SR, note_to_hz
from synthlab.studio.factory import FACTORY, get_factory
from synthlab.synth import (
    DRIFT_CENTS,
    DRIFT_CUT_OCT,
    Patch,
    Synth,
    drift_cents,
    drift_cutoff,
    drift_phase,
    drift_seed,
    drift_u,
)

ROUND5 = ("Sync Lead", "FM4 Tine Keys", "Wavetable Scan", "Drifting Poly")


def _alias_db(y: np.ndarray, f0: float) -> float:
    """Energy away from the multiples of f0 over the energy on them, dB."""
    p = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
    f = np.fft.rfftfreq(len(y), 1 / SR)
    near = np.abs(f / f0 - np.round(f / f0)) * f0 < 25
    return float(10 * np.log10(p[~near].sum() / p[near].sum()))


def _harmonics_db(y: np.ndarray, f0: float, k: int = 12) -> np.ndarray:
    p = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    f = np.fft.rfftfreq(len(y), 1 / SR)
    out = []
    for h in range(1, k + 1):
        i = int(np.argmin(np.abs(f - h * f0)))
        out.append(20 * np.log10(p[i - 2 : i + 3].max() + 1e-12))
    return np.array(out)


# --- the defaults: the old engine, untouched ---------------------------------------


def test_older_patches_never_reach_the_newer_code(monkeypatch):
    """Every factory sound but those built on them, every PRESET and the
    default patch render without touching sync, fm4 or drift: their audio
    is unchanged by them, so no RENDER_REV bump (also measured: all 390
    shipped-patch renders hash equal with and without them, native and
    NumPy)."""

    def boom(*a, **k):
        raise AssertionError("a round-five path ran on an older patch")

    monkeypatch.setattr(core, "sync", boom)
    monkeypatch.setattr(core, "fm4", boom)
    monkeypatch.setattr(synth_mod, "drift_cents", boom)
    monkeypatch.setattr(synth_mod, "drift_phase", boom)
    olds = [get_factory(e["name"]) for e in FACTORY if e["name"] not in ROUND5]
    for p in [*olds, *synth_mod.PRESETS.values(), Patch()]:
        Synth(p).note("C3", 0.3, gate=0.2)


def test_the_first_four_tables_keep_their_places():
    assert core.WAVETABLE_NAMES[:4] == ("basic", "harmonic", "vocal", "digital")
    assert core.WAVETABLE_NAMES[4:] == (
        "scan",
        "organ",
        "bell",
        "stack",
        "pulse",
        "formant",
        "crush",
        "analog",
    )


# --- hard sync -------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["saw", "square"])
@pytest.mark.parametrize("master,ratio", [(220.0, 2.7), (880.0, 2.3), (1760.0, 1.6)])
def test_sync_is_band_limited_against_a_naive_reset(shape, master, ratio):
    """The polyBLEP residual at every step — the wave's own edges and each
    reset, of whatever height the reset cuts — takes 15-17 dB off the
    aliasing of a plain reset (measured: saw at 220 / 880 / 1760 Hz,
    -34.3 / -30.3 / -26.3 dB against -18.7 / -13.4 / -11.3; the square
    -35.9 / -32.0 / -29.6 against -20.0 / -15.1 / -12.2), which leaves it
    near the unsynced PolyBLEP saw's own floor (-38.3 / -32.5 / -28.7)."""
    blep = core.sync(shape, master, master * ratio, SR, SR)[1000:]
    naive = core.sync(shape, master, master * ratio, SR, SR, naive=True)[1000:]
    a, b = _alias_db(blep, master), _alias_db(naive, master)
    assert a < b - 12.0, (a, b)
    assert a < -25.0, a


@pytest.mark.parametrize("shape", ["saw", "square", "triangle", "sine"])
def test_sync_keeps_the_ideal_harmonics(shape):
    """Against the same reset rendered at 32x and band-limited by FFT, the
    first twelve harmonics (of the master) within 0.5 dB at 220 Hz (0.1
    measured): the residuals take the alias, not the sound."""
    os_, master, ratio = 32, 220.0, 2.7
    hi = core.sync(shape, master, master * ratio, SR * os_, SR * os_, naive=True)
    spec = np.fft.rfft(hi)
    spec[np.fft.rfftfreq(len(hi), 1 / (SR * os_)) > SR / 2 * 0.98] = 0
    ref = np.fft.irfft(spec, len(hi))[::os_]
    got = core.sync(shape, master, master * ratio, SR, SR)
    hr, hg = _harmonics_db(ref[2000:], master), _harmonics_db(got[2000:], master)
    loud = hr > hr.max() - 50
    assert np.max(np.abs(hg - hr)[loud]) < 0.5


def test_sync_is_periodic_at_the_master_and_one_sample_late():
    """Synced, osc2's period is osc1's whatever its own pitch (the point of
    sync), and the output runs one sample behind: sample 0 holds only the
    residual of a step in sample 0 (none at phase 0)."""
    master = SR / 100  # a period of exactly 100 samples
    y = core.sync("saw", master, master * 2.37, 2000, SR)
    assert y[0] == 0.0
    np.testing.assert_allclose(y[300:400], y[400:500], atol=1e-9)
    assert not np.allclose(y[300:350], y[350:400], atol=1e-3)


def test_sync_reaches_osc2_only_when_switched_on_and_syncable():
    base = Patch(osc2="saw", osc2_octave=0, osc2_sync="on", sync_pitch=7.0)
    on = Synth(base).note("C3", 0.4, gate=0.3)
    off = Synth(replace(base, osc2_sync="off")).note("C3", 0.4, gate=0.3)
    assert not np.allclose(on, off)
    # fm4 / supersaw / wavetable on osc2 ignore the switch and the pitch
    for shape in ("wavetable", "fm4", "pluck"):
        p = replace(base, osc2=shape)
        a = Synth(p).note("C3", 0.3, gate=0.2)
        b = Synth(replace(p, osc2_sync="off", sync_pitch=0.0)).note("C3", 0.3, gate=0.2)
        np.testing.assert_array_equal(a, b)


def test_sync_pitch_sweeps_from_a_route_and_a_curve():
    """The sweep: the filter envelope routed to sync_pitch moves osc2's
    harmonics where a fixed sync_pitch does not, and so does a drawn
    curve (array-safe, CURVABLE)."""
    p = Patch(osc2="saw", osc2_octave=0, osc2_sync="on", sync_pitch=3.0, osc1_level=0.0)
    still = Synth(p).note("C3", 0.5, gate=0.4)
    routed = replace(p, mod1_src="fenv", mod1_dst="sync_pitch", mod1_amt=0.8)
    swept = Synth(routed).note("C3", 0.5, gate=0.4)
    assert not np.allclose(still, swept)
    n = core.n_samples(0.5, SR)
    curve = replace(p, sync_pitch=np.linspace(0.0, 19.0, n))
    drawn = Synth(curve).note("C3", 0.5, gate=0.4)
    assert np.all(np.isfinite(drawn)) and not np.allclose(still, drawn)


# --- fm4 ---------------------------------------------------------------------------


def test_fm4_with_quiet_modulators_is_operator_one():
    f, n = 220.0, 4000
    y = core.fm4(f, n, SR, "stack", (1.5, 2, 1, 1), (0.8, 0, 0, 0), (0, 0, 0, 0))
    want = 0.8 * np.sin(2 * np.pi * core._phase(f * 1.5, n, SR))
    np.testing.assert_allclose(y, want, atol=1e-12)


@pytest.mark.parametrize("algo", core.FM4_ALGO_NAMES)
def test_fm4_algorithms_are_wired_as_named(algo):
    """Each algorithm's carriers are heard at their own ratios; a modulator
    is not (it shows only as sidebands). Ratios 1 / 3 / 5 / 7 on ops 1-4,
    modulators at a whisper (index 0.06): a carrier's ratio is a line
    within 20 dB of the loudest, a modulator's is not (or it is a sideband
    of a carrier at most 30 dB down)."""
    _mods, carriers = core.FM4_ALGOS[algo]
    f, n = 110.0, SR
    y = core.fm4(f, n, SR, algo, (1, 3, 5, 7), (1, 0.01, 0.01, 0.01), (0, 0, 0, 0))
    if algo == "additive":
        y = core.fm4(f, n, SR, algo, (1, 3, 5, 7), (1, 1, 1, 1), (0, 0, 0, 0))
    elif len(carriers) > 1:
        lv = [1.0 if k in carriers else 0.01 for k in (1, 2, 3, 4)]
        y = core.fm4(f, n, SR, algo, (1, 3, 5, 7), lv, (0, 0, 0, 0))
    h = _harmonics_db(y, f, 8)
    for k, ratio in zip((1, 2, 3, 4), (1, 3, 5, 7), strict=True):
        if k in carriers:
            assert h[ratio - 1] > h.max() - 20, (algo, k, h)
        else:
            assert h[ratio - 1] < h.max() - 30, (algo, k, h)


def test_fm4_operator_decay_fades_a_modulator():
    """A 14:1 modulator decaying over 0.1 s: bright at the strike, a sine
    by half a second (the tine)."""
    f = 220.0
    y = core.fm4(f, SR, SR, "stack", (1, 14, 1, 1), (1, 0.4, 0, 0), (0, 0.1, 0, 0))
    early, late = y[:2048], y[int(0.5 * SR) : int(0.5 * SR) + 2048]

    def top(x):  # share of the energy above 2 kHz
        p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        return p[np.fft.rfftfreq(len(x), 1 / SR) > 2000].sum() / p.sum()

    assert top(early) > 0.2 and top(late) < 1e-4


def test_fm4_feedback_turns_operator_four_toward_a_saw():
    f, n = 220.0, SR
    plain = core.fm4(f, n, SR, "additive", (1, 1, 1, 1), (0, 0, 0, 1), (0,) * 4)
    fed = core.fm4(f, n, SR, "additive", (1, 1, 1, 1), (0, 0, 0, 1), (0,) * 4, 0.8)
    hp, hf = _harmonics_db(plain, f, 4), _harmonics_db(fed, f, 4)
    assert hp[1] < hp[0] - 80  # a sine
    assert hf[0] - 12 < hf[1] < hf[0] and hf[2] < hf[1]  # falling harmonics
    assert np.max(np.abs(fed)) <= 0.25 + 1e-9  # one carrier of four


def test_fm4_renders_through_the_patch():
    p = get_factory("FM4 Tine Keys")
    a = Synth(p).note("C4", 1.0, gate=0.6)
    assert np.all(np.isfinite(a)) and 0.05 < np.max(np.abs(a)) < 1.5
    other = Synth(replace(p, fm4_algo="stack")).note("C4", 1.0, gate=0.6)
    assert not np.allclose(a, other)


# --- the new wavetables -------------------------------------------------------


@pytest.mark.parametrize("name", core.WAVETABLE_NAMES[4:])
def test_new_tables_are_band_limited_per_mip(name):
    """Each mip holds no harmonic over its cap (the shape never aliases)
    and every frame peaks at 1."""
    stack = core._WT_STACK[name]
    assert stack.shape == (len(core._WT_MIPS), 4, core._WT_LEN)
    for m, cap in enumerate(core._WT_MIPS):
        for frame in stack[m]:
            assert abs(np.max(np.abs(frame)) - 1.0) < 1e-12
            spec = np.abs(np.fft.rfft(frame))
            assert spec[cap + 1 :].max() < 1e-9 * spec.max(), (name, cap)


def test_new_tables_differ_frame_to_frame():
    for name in core.WAVETABLE_NAMES[4:]:
        top = core._WT_STACK[name][0]
        for a, b in zip(top[:-1], top[1:], strict=True):
            assert np.max(np.abs(a - b)) > 0.05, name


# --- analog drift -------------------------------------------------------------


def test_drift_seed_and_hash_are_pinned():
    """The live voice reimplements these bit for bit (voice-worklet.js
    crc32 / driftU): a change here must be made there too."""
    s = drift_seed(note_to_hz("C4"))
    assert s == 295279105
    assert drift_u(s, 0) == pytest.approx(0.4420167226344347, abs=0)
    assert drift_u(s, 0x20000) == pytest.approx(0.053977484814822674, abs=0)
    assert drift_seed(note_to_hz("C#4")) != s


def test_drift_is_seeded_from_the_note():
    p = get_factory("Drifting Poly")
    a = Synth(p).note("C4", 0.6, gate=0.4)
    np.testing.assert_array_equal(a, Synth(p).note("C4", 0.6, gate=0.4))
    still = Synth(replace(p, drift=0.0)).note("C4", 0.6, gate=0.4)
    assert not np.allclose(a, still)


def test_drift_wanders_slowly_and_within_its_range():
    s, n = drift_seed(220.0), 20 * SR
    c = drift_cents(s, 1.0, n, SR)
    assert np.max(np.abs(c)) <= 3 * DRIFT_CENTS + 1e-9
    assert np.std(c) > 0.2 * DRIFT_CENTS  # it does wander
    p = np.abs(np.fft.rfft(c - c.mean())) ** 2
    f = np.fft.rfftfreq(n, 1 / SR)
    assert p[f > 1.0].sum() < 0.01 * p.sum()  # well under 1 Hz
    assert np.max(np.abs(np.diff(c))) < 1e-3  # no steps
    np.testing.assert_allclose(drift_cents(s, 0.5, n, SR), 0.5 * c, atol=1e-12)


def test_drift_cutoff_and_phases():
    s = drift_seed(330.0)
    assert 2**-DRIFT_CUT_OCT <= drift_cutoff(s, 1.0) <= 2**DRIFT_CUT_OCT
    assert drift_cutoff(s, 0.0) == 1.0
    phases = {drift_phase(s, slot, u) for slot in (0, 1) for u in range(8)}
    assert len(phases) == 16 and all(0 <= x < 1 for x in phases)


@pytest.mark.parametrize("name", ROUND5)
def test_the_oscillator_demos_render(name):
    p = get_factory(name)
    for pitch in ("C2", "C4", "C6"):
        a = Synth(p).note(pitch, 0.8, gate=0.5)
        assert np.all(np.isfinite(a)) and np.max(np.abs(a)) > 0.02, (name, pitch)
