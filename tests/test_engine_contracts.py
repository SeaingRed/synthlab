"""Engine defects, held down by tests.

One test (or a small group) per verified engine defect. Each docstring
names what the broken code did, measured with its repro,
so a regression moves a number rather than just flipping a verdict.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import lfilter

from synthlab import effects as fx
from synthlab.core import (
    SR,
    _biquad,
    _phase,
    _swept_biquad,
    adsr,
    biquad,
    osc,
    wavetable,
)
from synthlab.params import live as _active_genes
from synthlab.studio.arrange import build_song
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch, Synth, _apply_mod_matrix, synced_rate

# ------------------------------------------------------------ 1. mod matrix


@pytest.mark.parametrize(
    "dst",
    [
        "osc1_level",
        "osc2_level",
        "sub_level",
        "noise_level",
        "osc1_detune",
        "osc2_detune",
        "pwm_depth",
    ],
)
def test_a_mod_route_to_an_oscillator_target_is_heard(dst):
    """`note()` resolved the mod matrix into `p`, then `_osc_stack` read
    `self.patch` — so every route to a level, detune or PWM depth rendered
    bit-identical to no route at all."""
    base = Patch(osc1="square", osc2="saw", filter_type="none", unison=1)
    routed = base.tweak(mod1_src="velocity", mod1_dst=dst, mod1_amt=1.0)
    dry = Synth(base).note("C3", 0.3)
    wet = Synth(routed).note("C3", 0.3)
    assert np.max(np.abs(dry - wet)) > 1e-3, f"route to {dst} did nothing"


# ------------------------------------------------------- 4. mod-route clip


@pytest.mark.parametrize(
    ("field", "value"),
    [("cutoff", 12000.0), ("filter_env", -4000.0), ("noise_level", 0.9)],
)
def test_a_mod_route_does_not_clip_the_base_value_into_its_range(field, value):
    """Any active route clipped base + mod into the route range, so a 12 kHz
    cutoff became a flat 8 kHz, filter_env -4000 became 0 and noise 0.9
    became 0.4 the moment a tiny LFO touched them."""
    p = Patch(**{field: value}).tweak(mod1_src="lfo", mod1_dst=field, mod1_amt=0.01)
    out = _apply_mod_matrix(p, SR // 4, SR, 1.0, None)
    arr = np.asarray(getattr(out, field))
    assert abs(float(np.mean(arr)) - value) < 0.05 * abs(value)


def test_route_depth_is_still_a_fraction_of_the_route_span():
    p = Patch(cutoff=1000.0).tweak(mod1_src="velocity", mod1_dst="cutoff", mod1_amt=0.5)
    out = _apply_mod_matrix(p, 100, SR, 1.0, None)
    assert np.allclose(out.cutoff, 1000.0 + 0.5 * (8000.0 - 60.0))


# ----------------------------------------------------- 2. the swept filter


def _saw(n=SR, f=110.0):
    return osc("saw", f, n)


@pytest.mark.parametrize("res", [0.25, 0.6, 0.95])
def test_a_cutoff_step_does_not_blow_up(res):
    """State carried across blocks in transposed direct form while the
    coefficients jumped: a 6470 -> 20 Hz step peaked at 76x its input."""
    x = _saw()
    q = 0.707 + res * 6
    fc = np.where(np.arange(len(x)) < len(x) // 2, 6470.0, 20.0)
    swept = biquad(x, "lowpass", fc, q)
    static = max(np.max(np.abs(biquad(x, "lowpass", f, q))) for f in (6470.0, 20.0))
    assert np.max(np.abs(swept)) < 1.5 * static


@pytest.mark.parametrize("res", [0.25, 0.95])
def test_random_cutoff_jumps_stay_bounded(res):
    """Every 64-sample block lands on a random cutoff: the worst case for a
    filter whose state stops meaning anything when coefficients change."""
    rng = np.random.default_rng(3)
    x = _saw(SR // 2)
    fc = np.repeat(np.exp(rng.uniform(np.log(20), np.log(16000), len(x) // 64 + 1)), 64)
    q = 0.707 + res * 6
    y = biquad(x, "lowpass", fc[: len(x)], q)
    assert np.max(np.abs(y)) < 1.5 * q * np.max(np.abs(x))


def test_a_square_filter_lfo_on_the_default_patch_stays_bounded():
    """A second repro: default patch, square filter LFO at
    2500 Hz depth, peaked 35x."""
    plain = Synth(Patch()).note("C3", 1.0)
    lfo = Synth(
        Patch(filter_lfo_depth=2500.0, filter_lfo_shape="square", filter_lfo_rate=4.0)
    ).note("C3", 1.0)
    assert np.max(np.abs(lfo)) < 1.5 * np.max(np.abs(plain))


@pytest.mark.parametrize("kind", ["lowpass", "highpass", "bandpass", "notch"])
@pytest.mark.parametrize(("fc", "q"), [(20.0, 6.4), (440.0, 0.707), (9000.0, 3.0)])
def test_the_swept_path_matches_the_static_filter_at_a_steady_cutoff(kind, fc, q):
    """Same transfer function per block: a sweep that holds still must sound
    exactly like the one-shot static filter."""
    x = np.column_stack([_saw(SR // 4), _saw(SR // 4, 330.0)])
    b, a = _biquad(kind, fc, q, SR)
    ref = lfilter(b, a, x, axis=0)
    nb = -(-len(x) // 64)
    got = _swept_biquad(x, kind, np.full(nb, fc), q, SR, 64)
    assert np.max(np.abs(ref - got)) < 1e-9


def test_the_swept_filter_accepts_mono_stereo_and_ragged_lengths():
    x = _saw(1000)  # not a multiple of the block
    fc = np.linspace(200, 4000, len(x))
    mono = biquad(x, "lowpass", fc, 2.0)
    st = biquad(np.column_stack([x, x]), "lowpass", fc, 2.0)
    assert mono.shape == x.shape and st.shape == (len(x), 2)
    assert np.allclose(st[:, 0], mono) and np.allclose(st[:, 1], mono)


def test_phase_is_the_fractional_part():
    f = np.linspace(20, 20000, 5000)
    c = np.cumsum(f) / SR + 0.3
    assert np.allclose(_phase(f, len(f), SR, 0.3), c % 1.0, atol=1e-12)


def test_the_phaser_uses_scipys_raw_linear_filter():
    """`_allpass_sweep` calls `_linear_filter` directly to skip lfilter's
    array-API wrapper. It is a private scipy name: if an upgrade moves it,
    this test says so before the phaser does."""
    from scipy.signal._sigtools import _linear_filter

    x = _saw(640)
    b, a = np.array([0.3, 1.0]), np.array([1.0, 0.3])
    y1, z1 = _linear_filter(b, a, x, -1, np.zeros(1))
    y2, z2 = lfilter(b, a, x, zi=np.zeros(1))
    assert np.array_equal(y1, y2) and np.array_equal(z1, z2)
    out = fx._phaser(np.column_stack([x, x]), mix=1.0)
    assert np.all(np.isfinite(out))


# ------------------------------------------------------------- 3. ADSR


def test_a_long_attack_is_cut_by_a_short_gate_not_squeezed_into_it():
    """A 1 s attack on a 0.1 s gate used to reach full level at the gate:
    the ramp was fitted to the gate. It must reach 0.1 and release from
    there."""
    gate_n = int(0.1 * SR)
    env = adsr(SR, attack=1.0, decay=0.2, sustain=0.5, release=0.3, gate=0.1)
    assert env.max() == pytest.approx(0.1, abs=0.01)
    assert env[gate_n - 1] == pytest.approx(0.1, abs=0.01)
    assert env[gate_n] <= env[gate_n - 1] + 1e-12  # release starts from there
    assert env[-1] == pytest.approx(0.0, abs=1e-3)


def test_a_long_decay_is_cut_by_the_gate():
    gate_n = int(0.1 * SR)
    env = adsr(SR, attack=0.01, decay=1.0, sustain=0.0, release=0.3, gate=0.1)
    # squeezed, the decay reached sustain (0) by the gate; cut, it is still
    # most of the way up
    assert env[gate_n - 1] > 0.5
    assert env[-1] == pytest.approx(0.0, abs=1e-3)


def test_an_envelope_that_fits_its_gate_is_unchanged():
    env = adsr(SR, attack=0.1, decay=0.1, sustain=0.6, release=0.2, gate=0.5)
    a_n = int(0.1 * SR)
    assert np.allclose(env[:a_n], np.linspace(0, 1, a_n))
    assert env[int(0.3 * SR)] == pytest.approx(0.6)


# ---------------------------------------------------- 5-7. the arranger


def test_a_layer_resolves_its_synced_lfo_at_the_loop_tempo():
    """Layer patches never went through `at_bpm`, so a synced filter LFO on
    a layer played its raw Hz field instead of the division."""
    arr = Arrangement(bpm=128.0, cells=[])
    synced = Patch(filter_lfo_depth=2000.0, filter_lfo_sync="1/16", filter_lfo_rate=0.1)
    as_hz = synced.tweak(
        filter_lfo_sync="off", filter_lfo_rate=synced_rate("1/16", arr.bpm)
    )

    def render(p):
        layer = {"patch": p.to_json(), "cells": [["A1", 0]]}
        return build_song(Patch(), arr, layers=[layer], main_muted=True).render(
            normalize=0
        )

    assert np.allclose(render(synced), render(as_hz))


def _single_note_render(**arr_kw):
    pad = Patch(release=1.5, attack=0.01)
    arr = Arrangement(bpm=120.0, steps_per_bar=16, cells=[["A1", 0]], **arr_kw)
    return pad, arr


def test_a_drawn_curve_does_not_chop_the_release():
    """The curve path padded notes by `dur * 1.15` instead of `note_span`,
    so drawing any curve cut a 1.5 s release down to a few ms."""
    pad, arr = _single_note_render()
    plain = build_song(pad, arr).render(normalize=0)
    flat = {"drive": [[0.0, 0.0], [1.0, 0.0]]}  # drive 1.0 = the patch's own
    curved = build_song(pad, arr, curves=flat).render(normalize=0)
    n = min(len(plain), len(curved))
    assert np.allclose(plain[:n], curved[:n], atol=1e-9)


def test_a_slide_does_not_chop_the_release():
    pad, arr = _single_note_render()
    plain = build_song(pad, arr).render(normalize=0)
    _, slid = _single_note_render(slides=[0])
    slide = build_song(pad, slid).render(normalize=0)
    n = min(len(plain), len(slide))
    assert np.allclose(plain[:n], slide[:n], atol=1e-9)


def _ohat_lengths(**kw):
    arr = Arrangement(bpm=120.0, drum_steps=16, drum_bars=1, **kw)
    song = build_song(Patch(), arr, main_muted=True)
    return [len(a) for _, a, _ in song.tracks["drum_ohat"].events]


def test_the_open_hat_chokes_at_the_swung_closed_hat():
    """The choke length was counted on the straight grid, so against a swung
    closed hat the open hat stopped a swing-width early."""
    straight = _ohat_lengths(drums=[["ohat", 0], ["hat", 1]])[0]
    swung = _ohat_lengths(drums=[["ohat", 0], ["hat", 1]], drum_swing=0.5)[0]
    step_s = 0.25 * 60.0 / 120.0
    assert straight == pytest.approx(step_s * SR, abs=2)
    assert swung == pytest.approx(1.5 * step_s * SR, abs=2)


def test_a_muted_closed_hat_does_not_choke_the_open_hat():
    free = _ohat_lengths(drums=[["ohat", 0]])[0]
    muted = _ohat_lengths(
        drums=[["ohat", 0], ["hat", 1]], drum_mix={"hat": {"mute": True}}
    )[0]
    soloed_away = _ohat_lengths(
        drums=[["ohat", 0], ["hat", 1]], drum_mix={"ohat": {"solo": True}}
    )[0]
    assert muted == free and soloed_away == free


# ----------------------------------------------------------- 8. shimmer


def test_shimmer_never_plays_a_sound_before_it_happens():
    """The octave-up read ran at 2x speed from the buffer start, so a blip
    at 1.6 s shimmered at 0.8 s."""
    n = 2 * SR
    src = np.zeros((n, 2))
    at = int(1.6 * SR)
    src[at : at + 441] = osc("saw", 440.0, 441)[:, None]
    wet = fx._shimmer(src, decay=1.0, mix=1.0, shift=12.0)
    assert np.max(np.abs(wet[:at])) < 1e-9
    assert np.max(np.abs(wet[at:])) > 1e-3


def test_shimmer_actually_shifts_up_an_octave():
    f0 = 441.0
    src = np.column_stack([osc("sine", f0, SR)] * 2)
    shifted = fx._pitch_shift_causal(src, 2.0)[SR // 4 :, 0]
    spec = np.abs(np.fft.rfft(shifted * np.hanning(len(shifted)))) ** 2
    freqs = np.fft.rfftfreq(len(shifted), 1 / SR)

    def band(f):
        return spec[np.abs(freqs - f) < 50.0].sum()

    # grain overlap leaves sidebands near the target, but the energy sits
    # an octave up, not at the source pitch
    assert band(2 * f0) > 0.7 * spec.sum()
    assert band(f0) < 0.05 * spec.sum()


# --------------------------------------------------------- 9. wavetable


def _hf_share(y, above=5000.0):
    spec = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
    freqs = np.fft.rfftfreq(len(y), 1 / SR)
    return float(spec[freqs > above].sum() / spec.sum())


def test_a_pitch_envelope_does_not_dull_the_whole_wavetable_note():
    """The mip was picked once from the note's peak pitch, so a +24 st
    pitch envelope band-limited the entire note for its first milliseconds."""
    n = SR
    f0 = 220.0
    t = np.arange(n) / SR
    zap = f0 * 2 ** (24 * np.exp(-t / 0.02) / 12)
    steady = wavetable(np.full(n, f0), n, table="basic", pos=1.0)[n // 2 :]
    swept = wavetable(zap, n, table="basic", pos=1.0)[n // 2 :]
    assert _hf_share(swept) > 0.8 * _hf_share(steady)
    assert _hf_share(steady) > 0.01


def test_the_wavetable_still_never_aliases_while_the_pitch_moves():
    n = SR // 2
    f = np.geomspace(200.0, 8000.0, n)
    y = wavetable(f, n, table="basic", pos=1.0)
    # the brightest mip allowed per block keeps every partial under Nyquist:
    # a crude check that nothing folds down to a huge level
    assert np.max(np.abs(y)) <= 1.0 + 1e-9


# ------------------------------------------------------- 10. noise shape


def test_the_noise_oscillator_shape_follows_noise_color():
    white = Synth(Patch(osc1="noise", noise_color="white", filter_type="none"))
    brown = Synth(Patch(osc1="noise", noise_color="brown", filter_type="none"))
    a = white.note("C3", 0.5)
    b = brown.note("C3", 0.5)
    assert _hf_share(b[:, 0], 2000.0) < 0.5 * _hf_share(a[:, 0], 2000.0)


# ---------------------------------------------------------- 11. breeding

_FENV = {"filter_attack", "filter_decay", "filter_sustain", "filter_release"}


def test_filter_adsr_genes_are_dead_without_filter_env_depth():
    assert not _FENV & _active_genes(Patch(filter_env=0.0))
    assert _FENV <= _active_genes(Patch(filter_env=2500.0))


def test_filter_adsr_genes_stay_live_while_a_mod_slot_reads_fenv():
    routed = Patch(filter_env=0.0, mod1_src="fenv", mod1_dst="drive", mod1_amt=0.5)
    assert _FENV <= _active_genes(routed)
    bypassed = routed.tweak(filter_type="none")
    assert _FENV <= _active_genes(bypassed)


def test_a_bypassed_filter_kills_every_filter_gene():
    live = _active_genes(Patch(filter_type="none", filter_lfo_depth=3000.0))
    for dead in (
        "cutoff",
        "resonance",
        "filter_env",
        "key_track",
        "filter_lfo_rate",
        "filter_lfo_depth",
        "filter_lfo_shape",
        "filter_lfo_sync",
        *_FENV,
    ):
        assert dead not in live, dead


# ------------------------------------------------------- 12. beat repeat


@pytest.mark.parametrize("pitch", [12.0, 7.0, 0.5])
def test_a_beat_repeat_pitched_up_stays_inside_its_slice(pitch):
    """A positive pitch reads the captured slice faster than it plays, and
    read on past its end: `fx.beat_repeat(pitch=12.0)(x)` was an IndexError.
    The repeat now ends where its slice does, and the effect is still the
    periodic remap its loop-safety rests on."""
    rng = np.random.default_rng(5)
    bar = int(round(4 * 60.0 / 124.0 * SR))  # the default period: 4 beats
    x = rng.uniform(-0.5, 0.5, (bar, 2))
    unit = fx.beat_repeat(pitch=pitch, pattern="....x.......x...", grid=3)
    y = unit(x)
    assert y.shape == x.shape and np.all(np.isfinite(y))
    assert np.max(np.abs(y - x)) > 0.1
    # what a repeat plays is read from inside its own slice
    assert np.max(np.abs(y)) <= np.max(np.abs(x)) + 1e-12
    # one period after another: each bar comes out as the bar alone does
    two = unit(np.vstack([x, x]))
    np.testing.assert_allclose(two[:bar], y, atol=1e-12)
    np.testing.assert_allclose(two[bar:], y, atol=1e-12)


# ----------------------------------------------------- 13. the room cache


def test_a_long_buffers_room_spectrum_is_not_kept():
    """The room's spectrum is cached per FFT length within a byte budget. A
    loop's is ~10 MB; a five-minute buffer's is ~210 MB and four of them
    stayed. Past `_IR_SPECTRUM_MAX_BYTES` it is computed and dropped — the
    same numbers — and the budget keeps the most recently used."""
    room = (2.0, 0.8, 0.35, 0.02, SR)
    fx._IR_SPECTRA.clear()
    small = 1 << 16
    big = fx._IR_SPECTRUM_MAX_BYTES // 32 * 2  # (nfft/2 + 1) x 2 ch x 16 B
    assert fx._room_spectrum(room, small) is fx._room_spectrum(room, small)
    spec = fx._room_spectrum(room, big)
    assert list(fx._IR_SPECTRA) == [("reverb", room, small)]
    want = np.fft.rfft(np.asarray(fx._room(*room)), big, axis=0)
    np.testing.assert_allclose(spec, want, rtol=1e-12, atol=1e-9)
    # the budget: the oldest goes first
    sizes = range(1 << 20, (1 << 20) + 9 * 1024, 1024)
    for nfft in sizes:
        fx._room_spectrum(room, nfft)
    kept = sum(v.nbytes for v in fx._IR_SPECTRA.values())
    assert kept <= fx._IR_SPECTRA_BYTES
    assert ("reverb", room, small) not in fx._IR_SPECTRA
    assert ("reverb", room, sizes[-1]) in fx._IR_SPECTRA
    fx._IR_SPECTRA.clear()


def test_the_plate_and_spring_spectra_are_cached_and_change_nothing():
    """A plate's and a spring's IR spectrum come from the cache on a second
    call with the same arguments and length: bit for bit what computing it
    again gives."""
    fx._IR_SPECTRA.clear()
    x = np.random.default_rng(4).standard_normal((30_000, 2)) * 0.3
    for unit, kind in (
        (fx.plate(decay=1.1), "plate"),
        (fx.spring(decay=0.9), "spring"),
    ):
        first = unit(x)
        assert [k[0] for k in fx._IR_SPECTRA].count(kind) == 1
        np.testing.assert_array_equal(unit(x), first)
        fx._IR_SPECTRA.clear()
        np.testing.assert_array_equal(unit(x), first)
    fx._IR_SPECTRA.clear()
