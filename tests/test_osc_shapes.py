"""The "fm" and "pluck" oscillator kinds, plus a render test over every shape."""

from dataclasses import replace

import numpy as np
import pytest

from synthlab.core import SR, fm, pluck
from synthlab.params import PARAMS
from synthlab.params import live as _active_genes
from synthlab.synth import Patch, Synth


def _spectrum(x):
    return np.abs(np.fft.rfft(x * np.hanning(len(x))))


def _peak_hz(x, sr=SR):
    mag = _spectrum(x)
    return float(np.argmax(mag) * sr / len(x))


def _pitch_hz(x, sr=SR, lo=50.0, hi=2000.0):
    """Fundamental via autocorrelation (the loudest FFT bin of a bright
    plucked string is often a harmonic, not the fundamental)."""
    x = x - x.mean()
    ac = np.correlate(x, x, mode="full")[len(x) - 1 :]
    lag_lo, lag_hi = int(sr / hi), int(sr / lo)
    lag = lag_lo + int(np.argmax(ac[lag_lo:lag_hi]))
    return sr / lag


# -- fm ------------------------------------------------------------------------


def test_fm_index_zero_is_a_pure_sine():
    x = fm(220.0, SR // 2, SR, ratio=2.0, index=0.0)
    mag = _spectrum(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    assert abs(_peak_hz(x) - 220) < 3
    # nothing above -40 dB outside the fundamental
    mask = np.abs(f - 220) > 10
    assert mag[mask].max() < mag.max() * 0.01


def test_fm_index_adds_sidebands_at_carrier_plus_minus_ratio():
    x = fm(220.0, SR // 2, SR, ratio=2.0, index=3.0)
    mag = _spectrum(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)

    def energy_at(hz):
        return mag[np.abs(f - hz) < 4].max()

    assert energy_at(220 + 440) > mag.max() * 0.05
    assert energy_at(220 + 880) > mag.max() * 0.02


def test_fm_index_decay_gets_darker():
    n = SR
    x = fm(220.0, n, SR, ratio=3.0, index=6.0, index_decay=0.15)
    head, tail = x[: n // 8], x[-n // 8 :]
    f = np.fft.rfftfreq(len(head), 1 / SR)
    hi = f > 1500
    assert _spectrum(head)[hi].sum() > 5 * _spectrum(tail)[hi].sum()


def test_fm_accepts_array_freq_ratio_and_index():
    n = SR // 4
    ramp = np.linspace(0, 1, n)
    x = fm(np.full(n, 220.0) * (1 + ramp), n, SR, ratio=1 + ramp, index=ramp * 4)
    assert np.all(np.isfinite(x)) and np.abs(x).max() <= 1.0


# -- pluck ---------------------------------------------------------------------


def test_pluck_rings_at_the_requested_pitch():
    for hz in (110.0, 220.0, 440.0):
        x = pluck(hz, SR, SR, damping=0.3)
        assert abs(_pitch_hz(x[: SR // 4]) - hz) / hz < 0.02, hz


def test_pluck_damping_shortens_the_note():
    def tail_energy(d):
        x = pluck(220.0, SR, SR, damping=d)
        return float(np.sum(x[-SR // 4 :] ** 2))

    assert tail_energy(0.1) > 3 * tail_energy(0.9)


def test_pluck_uses_first_freq_sample_and_stays_bounded():
    n = SR // 2
    x = pluck(np.linspace(220, 440, n), n, SR)
    assert abs(_pitch_hz(x[: n // 2]) - 220) < 5
    assert np.abs(x).max() <= 0.8 + 1e-9


def test_pluck_is_deterministic_per_seed():
    assert np.array_equal(
        pluck(220.0, 4000, SR, seed=3), pluck(220.0, 4000, SR, seed=3)
    )
    assert not np.array_equal(
        pluck(220.0, 4000, SR, seed=3), pluck(220.0, 4000, SR, seed=4)
    )


# -- every shape renders through the synth ------------------------------------


@pytest.mark.parametrize("shape", PARAMS["osc1"].gene)
def test_every_osc1_shape_renders(shape):
    s = Synth(replace(Patch(), osc1=shape, fm_decay=0.2))
    out = s.note("A2", 0.5)
    assert np.all(np.isfinite(out)) and np.abs(out).max() > 0.05


@pytest.mark.parametrize("shape", [c for c in PARAMS["osc2"].gene if c])
def test_every_osc2_shape_renders_with_fm_and_ring(shape):
    s = Synth(replace(Patch(), osc2=shape, fm_amount=0.5, ring_mod=0.5))
    out = s.note("A2", 0.5)
    assert np.all(np.isfinite(out)) and np.abs(out).max() > 0.05


def test_pluck_with_glide_and_unison_does_not_crash():
    p = replace(Patch(), osc1="pluck", glide=0.2, unison=3, pitch_env=12.0)
    out = Synth(p).note("C3", 0.6, from_pitch="C2")
    assert np.all(np.isfinite(out))


def test_old_preset_json_without_new_fields_loads():
    d = {
        k: v
        for k, v in Patch().__dict__.items()
        if not k.startswith(("fm_r", "fm_i", "fm_d", "pluck"))
    }
    p = Patch(**d)
    assert (p.fm_ratio, p.fm_index, p.fm_decay, p.pluck_damping, p.pluck_bright) == (
        2.0,
        2.0,
        0.0,
        0.5,
        0.9,
    )


# -- breed -----------------------------------------------------------------------


def test_fm_and_pluck_genes_follow_the_shape():
    dead = _active_genes(Patch())
    assert not (
        {"fm_ratio", "fm_index", "fm_decay", "pluck_damping", "pluck_bright"} & dead
    )
    assert {"fm_ratio", "fm_index", "fm_decay"} <= _active_genes(
        replace(Patch(), osc1="fm")
    )
    assert {"pluck_damping", "pluck_bright"} <= _active_genes(
        replace(Patch(), osc2="pluck")
    )
