"""Riser / impact generators (`synthlab.transitions`): exact length, the
-1 dBFS peak, finite, deterministic, and each shaped the way its name
promises — a riser builds (louder and brighter towards its end), a
downlifter falls, an impact hits first and a reversed one lands last."""

from __future__ import annotations

import numpy as np
import pytest

from synthlab import transitions as tr
from synthlab.core import SR

CASES = [(k, d) for k in ("riser", "impact") for d in ("up", "down")]


def quarters_db(x: np.ndarray) -> list[float]:
    q = len(x) // 4
    return [
        20 * np.log10(np.sqrt((x[i * q : (i + 1) * q] ** 2).mean()) + 1e-12)
        for i in range(4)
    ]


def centroid(x: np.ndarray) -> float:
    m = x.mean(axis=1)
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return float((sp * f).sum() / max(sp.sum(), 1e-12))


@pytest.mark.parametrize("kind,direction", CASES)
@pytest.mark.parametrize("seconds", [0.5, 2.0, 8.0])
def test_length_peak_and_no_nan(kind, direction, seconds):
    x = tr.generate(kind, seconds, direction, 0.6, 0.4)
    assert x.shape == (int(round(seconds * SR)), 2)
    assert np.all(np.isfinite(x))
    assert float(np.max(np.abs(x))) == pytest.approx(0.89, abs=1e-9)
    # starts and ends without a step (declicked edges)
    assert float(np.max(np.abs(x[0]))) < 0.05 and float(np.max(np.abs(x[-1]))) < 0.01


@pytest.mark.parametrize("kind,direction", CASES)
def test_deterministic(kind, direction):
    a = tr.generate(kind, 2.0, direction, 0.3, 0.7)
    assert np.array_equal(a, tr.generate(kind, 2.0, direction, 0.3, 0.7))


def test_a_riser_builds_and_a_downlifter_falls():
    up = tr.riser(4.0, "up", 0.8, 0.3)
    down = tr.riser(4.0, "down", 0.8, 0.3)
    q = len(up) // 4
    assert np.all(np.diff(quarters_db(up)) > 3.0)  # louder every quarter
    assert np.all(np.diff(quarters_db(down)) < -3.0)
    assert centroid(up[-q:]) > 3 * centroid(up[:q])  # the filter opens
    assert centroid(down[:q]) > 3 * centroid(down[-q:])


def test_an_impact_hits_first_and_a_reversed_one_lands_last():
    hit = tr.impact(2.0, "down", 0.6, 0.5)
    swell = tr.impact(2.0, "up", 0.6, 0.5)
    assert np.argmax(quarters_db(hit)) == 0 and np.argmax(quarters_db(swell)) == 3
    peak = np.argmax(np.abs(hit).max(axis=1))
    assert peak < 0.05 * SR  # the hit is at the top
    # a sub drop: most of the energy under 150 Hz
    m = hit.mean(axis=1)
    sp = np.abs(np.fft.rfft(m)) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    assert sp[f < 150].sum() / sp.sum() > 0.4


def test_brightness_and_tail_do_what_they_say():
    for kind in ("riser", "impact"):
        dark = tr.generate(kind, 2.0, "down", 0.0, 0.4)
        bright = tr.generate(kind, 2.0, "down", 1.0, 0.4)
        assert centroid(bright) > 1.5 * centroid(dark), kind
    dry = tr.impact(4.0, "down", 0.6, 0.0)
    wet = tr.impact(4.0, "down", 0.6, 1.0)
    assert quarters_db(wet)[2] > quarters_db(dry)[2] + 6  # the room rings on


def test_refuses_unknown_kinds_and_directions_and_clamps_the_knobs():
    with pytest.raises(ValueError):
        tr.generate("laser", 1.0, "up", 0.5, 0.5)
    with pytest.raises(ValueError):
        tr.generate("riser", 1.0, "sideways", 0.5, 0.5)
    a = tr.riser(1.0, "up", 7.0, float("nan"))
    assert np.array_equal(a, tr.riser(1.0, "up", 1.0, 0.0))


# --- sweep, swell, sub drop, bed (the four added for the style packs) ----------

NEW = [(k, d) for k in ("sweep", "swell", "subdrop", "bed") for d in ("up", "down")]


def low_share(x: np.ndarray, hz: float = 150.0) -> float:
    m = x.mean(axis=1)
    sp = np.abs(np.fft.rfft(m)) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return float(sp[f < hz].sum() / sp.sum())


def test_the_six_kinds_and_what_they_default_to():
    assert list(tr.GENERATORS) == [
        "riser",
        "impact",
        "sweep",
        "swell",
        "subdrop",
        "bed",
    ]
    assert set(tr.DEFAULT_DIRECTION) == set(tr.PEAK) == set(tr.GENERATORS)
    assert set(tr.DEFAULT_DIRECTION.values()) <= set(tr.DIRECTIONS)
    # what ends on a bar line, and what starts on one
    lead = {k for k in tr.GENERATORS if tr.leads_in(k, "up")}
    assert lead == {"riser", "impact", "sweep", "swell", "subdrop"}
    assert not any(tr.leads_in(k, "down") for k in tr.GENERATORS)
    assert tr.PEAK["bed"] == pytest.approx(10 ** (-18 / 20))
    assert all(v == 0.89 for k, v in tr.PEAK.items() if k != "bed")


@pytest.mark.parametrize("kind,direction", NEW)
@pytest.mark.parametrize("seconds", [0.5, 2.0, 8.0])
def test_the_new_kinds_are_exact_in_length_at_their_peak_and_finite(
    kind, direction, seconds
):
    """Measured: 0.89 (-1 dBFS) for the sweep, the swell and the sub drop,
    0.1259 (-18 dBFS) for the bed; no DC (worst 9e-4 of full scale, the
    half-second sub drop — it is only a couple of dozen cycles long)."""
    x = tr.generate(kind, seconds, direction, 0.6, 0.4)
    assert x.shape == (int(round(seconds * SR)), 2)
    assert np.all(np.isfinite(x))
    assert float(np.max(np.abs(x))) == pytest.approx(tr.PEAK[kind], abs=1e-9)
    assert abs(float(x.mean())) < 2e-3
    if kind != "bed":  # declicked edges; a bed has none, it loops
        assert float(np.max(np.abs(x[0]))) < 0.05
        assert float(np.max(np.abs(x[-1]))) < 0.01


@pytest.mark.parametrize("kind,direction", NEW)
def test_the_new_kinds_are_deterministic(kind, direction):
    a = tr.generate(kind, 2.0, direction, 0.3, 0.7)
    assert np.array_equal(a, tr.generate(kind, 2.0, direction, 0.3, 0.7))


def test_a_sweep_is_noise_alone_rising_or_falling():
    """Each quarter louder than the last by more than 3 dB (measured
    8-13), the centroid climbing more than 2.5x (measured 2.0 -> 6.7 kHz at
    brightness 0.6); nothing under 150 Hz and no pitch — where the riser's
    supersaw puts lines in the spectrum, no bin of the sweep's last
    quarter holds 1 % of its energy."""
    up = tr.sweep(4.0, "up", 0.8, 0.3)
    down = tr.sweep(4.0, "down", 0.8, 0.3)
    q = len(up) // 4
    assert np.all(np.diff(quarters_db(up)) > 3.0)
    assert np.all(np.diff(quarters_db(down)) < -3.0)
    assert centroid(up[-q:]) > 2.5 * centroid(up[:q])
    assert centroid(down[:q]) > 2.5 * centroid(down[-q:])
    assert low_share(up) < 0.01
    # the resonant band: the last quarter's energy sits round the top of
    # the sweep, not spread flat like white noise
    sp = np.abs(np.fft.rfft(up[-q:].mean(axis=1))) ** 2
    f = np.fft.rfftfreq(q, 1 / SR)
    assert sp[(f > 2000) & (f < 14000)].sum() / sp.sum() > 0.8
    assert sp.max() / sp.sum() < 0.01  # no tone
    # two noises: it is wide
    assert abs(np.corrcoef(up[:, 0], up[:, 1])[0, 1]) < 0.3


def test_a_swell_grows_into_its_end_and_has_no_sub_or_click():
    """The reversed burst: louder every quarter, its peak in the last tenth
    of a second, and — unlike an impact run "up" — nothing under 150 Hz."""
    up = tr.swell(4.0, "up", 0.6, 0.5)
    down = tr.swell(4.0, "down", 0.6, 0.5)
    assert np.all(np.diff(quarters_db(up)) > 3.0)
    assert np.argmax(quarters_db(down)) == 0
    assert np.argmax(np.abs(up).max(axis=1)) > len(up) - 0.1 * SR
    assert np.argmax(np.abs(down).max(axis=1)) < 0.1 * SR
    assert low_share(up) < 0.01
    assert low_share(tr.impact(4.0, "up", 0.6, 0.5)) > 0.3
    # the room is the swell: a dry one is over in the last quarter
    dry = tr.swell(4.0, "up", 0.6, 0.0)
    assert quarters_db(up)[1] > quarters_db(dry)[1] + 10


def _pitch(x: np.ndarray) -> float:
    m = x[:, 0]
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), n=1 << 17))
    return float(np.fft.rfftfreq(1 << 17, 1 / SR)[np.argmax(sp)])


def test_a_sub_drop_is_one_falling_sine_in_mono():
    """Starts above 100 Hz and ends under 40; over 90 % of the energy under
    150 Hz (measured 98 %); left is right."""
    x = tr.subdrop(4.0, "down", 0.6, 0.5)
    assert np.array_equal(x[:, 0], x[:, 1])
    assert low_share(x) > 0.9
    e = len(x) // 8
    first, last = _pitch(x[:e]), _pitch(x[-e:])
    assert 90 < first < 150 and 28 < last < 40, (first, last)
    assert np.all(np.diff(quarters_db(x)) < -2.0)  # it dies away
    # brightness lifts where it starts; tail lets it ring
    assert _pitch(tr.subdrop(4.0, "down", 1.0, 0.5)[:e]) > _pitch(
        tr.subdrop(4.0, "down", 0.0, 0.5)[:e]
    )
    assert (
        quarters_db(tr.subdrop(4.0, "down", 0.6, 1.0))[3]
        > quarters_db(tr.subdrop(4.0, "down", 0.6, 0.0))[3] + 10
    )
    up = tr.subdrop(4.0, "up", 0.6, 0.5)
    assert np.argmax(quarters_db(up)) == 3 and _pitch(up[-e:]) > _pitch(up[:e])


def test_a_bed_is_quiet_steady_and_loops_without_a_seam():
    """-18 dBFS at its loudest tick, about -35 dBFS RMS, the same level in
    every quarter (within 2 dB), no direction — and placed twice in a row
    the join is an ordinary sample step (measured 0.007 against a 99th
    percentile of 0.007 and a largest step of 0.07 inside the clip)."""
    b = tr.bed(4.0, "down", 0.6, 0.3)
    assert np.array_equal(b, tr.bed(4.0, "up", 0.6, 0.3))
    q = quarters_db(b)
    assert max(q) - min(q) < 2.0 and -45 < np.mean(q) < -28
    two = np.vstack([b, b])
    step = np.abs(np.diff(two, axis=0))
    assert np.all(step[len(b) - 1] < np.percentile(step, 99.9, axis=0))
    # the join is no louder than anywhere else: 20 ms either side of it
    # against the clip's own level
    k = int(0.02 * SR)
    around = two[len(b) - k : len(b) + k]
    assert 0.5 < np.sqrt((around**2).mean()) / np.sqrt((b**2).mean()) < 2.0
    # brightness opens the tone
    assert centroid(tr.bed(4.0, "down", 1.0, 0.3)) > 1.3 * centroid(
        tr.bed(4.0, "down", 0.0, 0.3)
    )
    assert abs(np.corrcoef(b[:, 0], b[:, 1])[0, 1]) < 0.2  # wide: two noises


def test_the_new_kinds_clamp_their_knobs_too():
    for kind in ("sweep", "swell", "subdrop", "bed"):
        a = tr.GENERATORS[kind](1.0, "up", 7.0, float("nan"))
        assert np.array_equal(a, tr.GENERATORS[kind](1.0, "up", 1.0, 0.0)), kind
        assert tr.GENERATORS[kind](0.0).shape == (int(round(0.05 * SR)), 2)
