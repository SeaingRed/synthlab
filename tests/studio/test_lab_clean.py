"""Cleaning a chop (`studio/labclean.py`): each step alone against a signal
built to show it, then the defaults, the settings' own cleaning and the
order of the steps. Pure: no studio, no files.

What none of this proves is how a gated, faded or stretched chop *sounds*.
"""

from __future__ import annotations

import numpy as np
import pytest

from synthlab.core import SR
from synthlab.studio import labclean, limits
from synthlab.studio.labclean import DEFAULTS, clean

MS5 = int(0.005 * SR)


def tone(seconds: float, level: float = 0.5, hz: float = 220.0) -> np.ndarray:
    t = np.arange(int(round(seconds * SR))) / SR
    x = level * np.sin(2 * np.pi * hz * t)
    return np.column_stack([x, x])


def hush(seconds: float) -> np.ndarray:
    return np.zeros((int(round(seconds * SR)), 2))


def only(**settings) -> dict:
    """The given step alone: no fades, nothing else on."""
    return {**DEFAULTS, "fade_in": 0.0, "fade_out": 0.0, **settings}


def test_the_defaults_change_nothing_but_the_first_and_last_5_ms():
    x = tone(1.0)
    out, notes = clean(x, DEFAULTS)
    assert notes == [] and out.shape == x.shape
    assert np.array_equal(out[MS5:-MS5], x[MS5:-MS5])  # the recording, bit for bit
    assert np.all(out[0] == 0.0) and np.max(np.abs(out[-1])) < 1e-3
    assert not np.array_equal(out[:MS5], x[:MS5])


def test_the_input_is_never_written_to():
    x = tone(0.5)
    before = x.copy()
    clean(x, {**DEFAULTS, "gate_db": -30, "trim_db": -30, "level": "normalise"})
    assert np.array_equal(x, before)


def test_trim_cuts_to_the_burst_and_keeps_5_ms_either_side():
    x = np.concatenate([hush(0.4), tone(0.3), hush(0.5)])
    out, notes = clean(x, only(trim_db=-40))
    first = int(0.4 * SR)
    # the burst is a sine from zero: its first sample over the threshold is
    # a few samples in, so allow a millisecond
    assert abs(len(out) - (int(0.3 * SR) + 2 * MS5)) <= SR // 1000
    assert np.allclose(
        out[MS5 + 60 : MS5 + 2000], x[first + 60 : first + 2000], atol=0.02
    )
    assert notes == ["trimmed 0.89 s"]


def test_trim_is_measured_from_the_chops_own_peak_so_a_quiet_one_is_kept():
    quiet = np.concatenate([hush(0.2), tone(0.3, level=0.004), hush(0.2)])
    out, _ = clean(quiet, only(trim_db=-20))
    assert abs(len(out) / SR - 0.31) < 0.002  # not trimmed away whole


def test_the_gate_closes_a_minus_50_db_floor_and_keeps_the_phrase():
    rng = np.random.default_rng(3)
    floor = 0.5 * 10 ** (-50 / 20)
    bleed = rng.uniform(-floor, floor, (int(0.6 * SR), 2))
    x = np.concatenate([bleed, tone(0.5), bleed, tone(0.4), bleed])
    out, notes = clean(x, only(gate_db=-40))
    a = int(0.6 * SR)
    b = a + int(0.5 * SR)
    # the phrase is untouched (a sample of settling at its ends aside)
    assert np.allclose(out[a + 8 : b - 8], x[a + 8 : b - 8])
    # the floor is silence once the hold (80 ms) and the release (60 ms) ran
    done = int((labclean.GATE_HOLD + labclean.GATE_RELEASE) * SR) + 8
    gap = out[b + done : b + int(0.6 * SR) - int(labclean.GATE_ATTACK * SR) - 8]
    assert len(gap) > SR // 4 and np.all(gap == 0.0)
    assert np.all(out[: a - int(labclean.GATE_ATTACK * SR) - 8] == 0.0)
    assert len(notes) == 1 and notes[0].startswith("gate closed ")
    share = int(notes[0].split()[2].rstrip("%"))
    assert 45 <= share <= 65  # 1.8 s of 2.7 s is floor, less the hold


def test_the_gate_holds_through_a_sines_zero_crossings():
    x = tone(0.5, hz=40.0)  # crosses zero every 12.5 ms
    out, _ = clean(x, only(gate_db=-20))
    # (it opens over the first samples, where the sine is still under the
    # threshold: the first 10 ms are its attack)
    assert np.allclose(out[SR // 100 :], x[SR // 100 :])
    assert np.max(np.abs(out[: SR // 100] - x[: SR // 100])) < 0.5 * 10 ** (-20 / 20)


def test_the_gate_is_measured_from_the_chops_own_peak():
    quiet = tone(0.5, level=0.003)
    out, _ = clean(quiet, only(gate_db=-20))
    # a quiet stem is not gated away whole
    assert np.allclose(out[SR // 100 :], quiet[SR // 100 :])


@pytest.mark.parametrize(
    ("fade_in", "fade_out"), [(5.0, 5.0), (50.0, 200.0), (0.0, 30.0)]
)
def test_fades_start_and_end_at_zero_and_are_raised_cosines(fade_in, fade_out):
    x = np.ones((SR, 2)) * 0.5
    out, notes = clean(x, {**DEFAULTS, "fade_in": fade_in, "fade_out": fade_out})
    assert notes == []
    n_in, n_out = int(round(fade_in / 1000 * SR)), int(round(fade_out / 1000 * SR))
    if n_in:
        assert out[0, 0] == 0.0
        assert abs(out[n_in // 2, 0] - 0.25) < 0.01  # half way: half gain
        assert np.all(np.diff(out[:n_in, 0]) >= 0)
    else:
        assert out[0, 0] == 0.5
    assert abs(out[-1, 0]) < 1e-3
    assert abs(out[-(n_out // 2) - 1, 0] - 0.25) < 0.01
    assert np.all(np.diff(out[-n_out:, 0]) <= 0)
    assert np.all(out[n_in : len(out) - n_out] == 0.5)


def test_a_fade_is_never_longer_than_half_the_chop():
    x = np.ones((SR // 10, 2))  # 100 ms, asked for 500 ms each way
    out, _ = clean(x, {**DEFAULTS, "fade_in": 500.0, "fade_out": 500.0})
    assert len(out) == len(x) and abs(out[len(x) // 2, 0] - 1.0) < 0.01


def test_normalise_peaks_at_minus_1_dbfs():
    out, notes = clean(tone(0.5, level=0.2), only(level="normalise"))
    assert abs(np.max(np.abs(out)) - 10 ** (-1 / 20)) < 1e-6
    assert notes == ["normalised +13.0 dB"]


def test_gain_is_that_many_db_and_is_held_under_full_scale():
    x = tone(0.5, level=0.25)
    out, notes = clean(x, only(level="gain", gain_db=6.0))
    assert np.allclose(out, x * 10 ** (6 / 20)) and notes == ["gain +6 dB"]
    loud, notes = clean(x, only(level="gain", gain_db=24.0))
    assert abs(np.max(np.abs(loud)) - 1.0) < 1e-9
    assert notes == ["gain +24 dB, held under full scale"]
    same, notes = clean(x, only(level="off", gain_db=12.0))  # gain needs "gain"
    assert np.array_equal(same, x) and notes == []


def test_fit_stretches_to_the_tempo_and_lands_on_whole_bars():
    """Two bars at 120 bpm (4 s), a touch short, fitted to 100 bpm: two
    bars of 2.4 s, to the sample."""
    x = tone(3.95)
    plan = {"factor": 1.2, "source_bpm": 120.0, "bpm": 100.0, "bar": 2.4}
    out, notes = clean(x, only(fit="session"), plan)
    assert len(out) == int(round(2 * 2.4 * SR))
    assert notes == ["stretched 120 → 100 bpm", "held to 2 bars"]
    # the pitch stays: the same 220 Hz
    spec = np.abs(np.fft.rfft(out[SR : 2 * SR, 0]))
    assert abs(int(np.argmax(spec)) - 220) <= 1


def test_fit_leaves_a_chop_that_is_not_near_whole_bars_at_its_stretched_length():
    plan = {"factor": 1.2, "source_bpm": 120.0, "bpm": 100.0, "bar": 2.4}
    out, notes = clean(tone(1.0), only(fit="song"), plan)  # half a bar
    assert abs(len(out) - int(1.2 * SR)) <= 2
    assert notes == ["stretched 120 → 100 bpm"]


def test_fit_pads_a_short_chop_up_to_its_bar_with_silence():
    plan = {"factor": 1.0, "source_bpm": 120.0, "bpm": 120.0, "bar": 2.0}
    out, notes = clean(tone(1.93), only(fit="session"), plan)
    assert len(out) == 2 * SR and np.all(out[int(1.93 * SR) + 1 :] == 0.0)
    assert notes == ["held to 1 bar"]  # same tempo: nothing to stretch


def test_fit_that_cannot_run_says_why_and_changes_nothing():
    x = tone(1.0)
    out, notes = clean(x, only(fit="session"), {"reason": "no tempo set"})
    assert np.array_equal(out, x) and notes == ["not fitted (no tempo set)"]
    out, notes = clean(x, only(fit="session"), None)
    assert np.array_equal(out, x) and notes == ["not fitted (no tempo set)"]
    out, notes = clean(x, only(fit="off"), {"factor": 2.0, "bar": 2.0})
    assert np.array_equal(out, x) and notes == []  # nobody asked


def test_the_steps_run_gate_trim_fit_fades_level_in_that_order():
    rng = np.random.default_rng(5)
    bleed = rng.uniform(-0.001, 0.001, (SR // 2, 2))
    x = np.concatenate([bleed, tone(1.93), bleed])
    plan = {"factor": 1.0, "source_bpm": 120.0, "bpm": 120.0, "bar": 2.0}
    settings = {
        "gate_db": -40,
        "trim_db": -40,
        "fit": "session",
        "fade_in": 10,
        "fade_out": 10,
        "level": "normalise",
        "gain_db": 0,
    }
    out, notes = clean(x, settings, plan)
    assert [n.split()[0] for n in notes] == ["gate", "trimmed", "held", "normalised"]
    assert len(out) == 2 * SR  # trimmed to the tone, then held to its bar
    assert out[0, 0] == 0.0 and abs(np.max(np.abs(out)) - 10 ** (-1 / 20)) < 1e-6


def test_silence_goes_through_every_step_without_a_division():
    out, _ = clean(
        hush(0.3), {**DEFAULTS, "gate_db": -30, "trim_db": -30, "level": "normalise"}
    )
    assert out.shape == (int(0.3 * SR), 2) and not np.any(out)
    out, _ = clean(np.zeros((0, 2)), {**DEFAULTS, "gate_db": -30, "trim_db": -30})
    assert out.shape == (0, 2)


def test_mono_comes_back_stereo():
    out, _ = clean(tone(0.2)[:, 0], DEFAULTS)
    assert out.shape == (int(0.2 * SR), 2)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "x",
        [],
        {"gate_db": "loud", "trim_db": [], "fit": 7, "level": {}, "gain_db": "x"},
        {"gate_db": float("nan"), "fade_in": float("inf"), "fade_out": True},
        {"gate_db": 10**400, "fade_in": -5, "fade_out": 1e9, "gain_db": -1e9},
    ],
)
def test_hostile_settings_are_held_to_type_and_range(raw):
    s = labclean.clean_settings(raw)
    assert set(s) == set(DEFAULTS)
    lo, hi = limits.LAB_CLEAN_DB_RANGE
    assert s["gate_db"] is None or lo <= s["gate_db"] <= hi
    assert s["trim_db"] is None or lo <= s["trim_db"] <= hi
    assert s["fit"] in labclean.FITS and s["level"] in labclean.LEVELS
    assert 0 <= s["fade_in"] <= 500 and 0 <= s["fade_out"] <= 500
    assert -24 <= s["gain_db"] <= 24
    clean(tone(0.1), raw if isinstance(raw, dict) else {})  # and it runs


def test_form_values_arrive_as_text_and_off_is_a_threshold_switched_off():
    s = labclean.clean_settings(
        {
            "gate_db": "-45",
            "trim_db": "",
            "fit": "song",
            "fade_in": "12",
            "gain_db": "3.5",
        }
    )
    assert s["gate_db"] == -45.0 and s["trim_db"] is None and s["fit"] == "song"
    assert s["fade_in"] == 12.0 and s["fade_out"] == 5.0 and s["gain_db"] == 3.5
    assert labclean.clean_settings({"gate_db": "off"})["gate_db"] is None
    assert labclean.clean_settings({"gate_db": "-99"})["gate_db"] == -60.0
    assert labclean.is_default({}) and not labclean.is_default({"trim_db": -30})


def test_whole_bars_is_within_5_percent_of_them():
    assert labclean.whole_bars(int(4.0 * SR), 2.0) == 2
    assert labclean.whole_bars(int(3.85 * SR), 2.0) == 2
    assert labclean.whole_bars(int(3.7 * SR), 2.0) == 0
    assert labclean.whole_bars(int(0.9 * SR), 2.0) == 0  # never zero bars
    assert labclean.whole_bars(SR, 0.0) == 0
