"""More filter types, a second filter, and the deeper modulation — twelve
route slots, LFO 2 / 3 with the drawn / chaos / smooth random shapes,
envelopes 3 / 4, the mod wheel and aftertouch, the key and a random value
per note, and the extra route targets.

Each is opt-in: at its defaults a patch without it never reaches its code
(pinned here, as test_osc_sync_fm4_drift.py pins the oscillator extras; also
measured: the 293 shipped patches' renders hash equal with and without
them, native and NumPy). And
each is held to what it claims: stable at full resonance under a slammed
cutoff, the combs in tune, every LFO shape seeded from the note (a render
is the same every time; nothing reads a random stream)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from synthlab import core
from synthlab import synth as synth_mod
from synthlab.core import SR
from synthlab.params import (
    FILTER_TYPES,
    FILTER_TYPES_ALL,
    GENERAL_LFO_SHAPES,
    MOD_DESTINATIONS,
    MOD_GENE_DESTINATIONS,
    MOD_GENE_SOURCES,
    MOD_SOURCES,
    PARAMS,
    clamp,
)
from synthlab.studio.factory import FACTORY, get_factory
from synthlab.synth import Patch, Synth, at_bpm

ROUND6 = (
    "Comb Pluck",
    "Dual Filter Sweep",
    "Chaos Pad",
    "Wheel Growl",
    "Drawn Gate Pad",
)


def _note(p: Patch, pitch="C3", dur=0.6, gate=0.4, **kw) -> np.ndarray:
    return Synth(p).note(pitch, dur, gate=gate, **kw)


# --- the defaults: the old engine, untouched ---------------------------------------


def test_older_patches_never_reach_the_newer_code(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a round-six path ran on an older patch")

    for name in ("filt", "comb", "lfo_ext", "diode_clip"):
        monkeypatch.setattr(core, name, boom)
    monkeypatch.setattr(synth_mod, "_gated_env", boom)  # the filter env's is wrapped
    monkeypatch.setattr(synth_mod, "_filter_env", lambda p, n, sr, g, d: np.zeros(n))
    olds = [get_factory(e["name"]) for e in FACTORY if e["name"] not in ROUND6]
    for p in [*olds, *synth_mod.PRESETS.values(), Patch()]:
        _note(p, dur=0.3, gate=0.2)


def test_the_old_choices_keep_their_places_and_the_genes_draw_them():
    assert FILTER_TYPES_ALL[:6] == FILTER_TYPES
    assert PARAMS["filter_type"].gene == FILTER_TYPES
    assert MOD_SOURCES[:5] == MOD_GENE_SOURCES == PARAMS["mod1_src"].gene
    assert PARAMS["lfo_shape"].gene == GENERAL_LFO_SHAPES[:5]
    assert MOD_DESTINATIONS[-1] == "pitch"
    for d in ("filter2_cutoff", "wt_pos", "fm4_level1", "formant_vowel"):
        assert d in MOD_DESTINATIONS and d not in MOD_GENE_DESTINATIONS
    # none of these fields is a gene (a longer genome re-draws litters)
    for name in (
        "filter2_route",
        "env3_attack",
        "lfo2_shape",
        "mod12_src",
        "mod_wheel",
    ):
        assert PARAMS[name].gene is None and PARAMS[name].nogene


# --- the filters ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", FILTER_TYPES_ALL[6:])
def test_every_new_type_is_stable_at_full_resonance_under_a_slammed_cutoff(kind):
    rng = np.random.default_rng(3)
    n = SR
    x = rng.uniform(-0.5, 0.5, (n, 2))
    t = np.arange(n) / SR
    cut = np.where(np.sin(2 * np.pi * 8 * t) > 0, 15000.0, 40.0)
    y = core.filt(x, kind, cut, 0.97, 2.0)
    assert np.isfinite(y).all() and np.abs(y).max() < 8.0, (kind, np.abs(y).max())


def test_the_old_types_through_filt_are_the_calls_the_synth_always_made():
    rng = np.random.default_rng(1)
    x = rng.uniform(-0.5, 0.5, (4096, 2))
    cut = np.geomspace(200, 4000, 4096)
    assert np.array_equal(
        core.filt(x, "ladder", cut, 0.4, 2.0), core.ladder(x, cut, 0.4, 2.0)
    )
    q = 0.707 + 0.4 * 6
    assert np.array_equal(
        core.filt(x, "notch", cut, 0.4), core.biquad(x, "notch", cut, q)
    )


@pytest.mark.parametrize("kind, mult", [("comb_plus", 1.0), ("comb_minus", 0.5)])
@pytest.mark.parametrize("midi", [36, 60, 84])
def test_a_comb_rings_at_its_cutoff(kind, mult, midi):
    """The impulse response's peak nearest the note: comb + at the cutoff,
    comb - an octave under (odd harmonics). Measured: within 0.01 cent to
    C6, 0.05 at C7, 0.3 at A7 (the linear interpolation's phase)."""
    f0 = 440 * 2 ** ((midi - 69) / 12)
    m = 2 * SR
    imp = np.zeros(m)
    imp[0] = 1.0
    y = core.comb(imp, f0, 0.95, 1.0 if kind == "comb_plus" else -1.0)
    spec = np.abs(np.fft.rfft(y, 8 * m)) ** 2
    f = np.fft.rfftfreq(8 * m, 1 / SR)
    want = f0 * mult
    band = (f > want * 0.97) & (f < want * 1.03)
    k = int(np.argmax(np.where(band, spec, 0)))
    a, b, c = np.log(spec[k - 1 : k + 2])
    got = (k + 0.5 * (a - c) / (a - 2 * b + c)) * (f[1] - f[0])
    assert abs(1200 * np.log2(got / want)) < 0.1


def test_filter_2_serial_is_filter_1_then_filter_2_and_parallel_their_mean():
    base = Patch(filter_type="lowpass", cutoff=500.0, filter_env=0.0, key_track=0.0)
    two = dict(filter2_type="highpass", filter2_cutoff=900.0, filter2_resonance=0.3)
    off = _note(replace(base, **two))
    assert np.array_equal(off, _note(base))  # "off" is no filter at all
    ser = _note(replace(base, filter2_route="serial", **two))
    par = _note(replace(base, filter2_route="parallel", **two))
    one_hp = _note(replace(base, filter_type="highpass", cutoff=900.0, resonance=0.3))
    # parallel: the mean of the two filters on the voice
    assert np.allclose(par, 0.5 * (_note(base) + one_hp), atol=1e-9)
    assert np.sqrt(np.mean(ser**2)) < 0.7 * np.sqrt(np.mean(par**2))


def test_filter_2_follows_the_key_and_the_envelope():
    p = Patch(
        filter_type="none",
        filter2_route="serial",
        filter2_type="lowpass",
        filter2_cutoff=300.0,
        filter2_key_track=1.0,
    )

    def bright(q, pitch):
        y = _note(q, pitch, 0.5, 0.4)[:, 0]
        s = np.abs(np.fft.rfft(y))
        f = np.fft.rfftfreq(len(y), 1 / SR)
        return float((s * f).sum() / s.sum())

    assert bright(p, "C5") > 2 * bright(p, "C3") > 0
    opened = replace(p, filter2_key_track=0.0, filter2_env=4000.0, filter_sustain=1.0)
    assert bright(opened, "C3") > 1.5 * bright(replace(opened, filter2_env=0.0), "C3")


# --- the modulation ------------------------------------------------------------------


@pytest.mark.parametrize("shape", GENERAL_LFO_SHAPES)
def test_every_general_lfo_shape_is_bounded_and_the_same_every_render(shape):
    for k in (1, 2, 3):
        f = "lfo_shape" if k == 1 else f"lfo{k}_shape"
        p = Patch(**{f: shape}, mod_lfo_rate=5.0)
        if k > 1:
            p = replace(p, **{f"lfo{k}_rate": 5.0, f"lfo{k}_phase": 0.3})
        a = synth_mod.general_lfo(p, k, SR, SR, 261.63)
        b = synth_mod.general_lfo(p, k, SR, SR, 261.63)
        assert np.array_equal(a, b)
        assert np.abs(a).max() <= 1.0 + 1e-9 and np.ptp(a) > 0.2, (shape, k)


def test_the_chaos_line_is_the_logistic_map_and_smooth_random_is_continuous():
    x0 = core.chaos_x0(7)
    xs = [x0]
    for _ in range(9):
        xs.append(core.CHAOS_R * xs[-1] * (1.0 - xs[-1]))
    n = SR
    y = core.lfo_ext("chaos", 8.0, n, SR, 0.0, 7)
    acc = np.cumsum(np.full(n, 8.0)) / SR
    k = np.floor(acc).astype(int)
    hit = np.flatnonzero(np.diff(k))  # each new cycle starts at its map value
    for i in hit[:5]:
        assert y[i + 1] == pytest.approx(2 * xs[k[i + 1]] - 1, abs=1e-3)
    s = core.lfo_ext("smooth", 6.0, n, SR, 0.0, 11)
    assert np.abs(np.diff(s)).max() < 0.01  # no steps: it glides


def test_a_drawn_shape_is_cleaned_and_read_as_drawn():
    assert (
        core.clean_draw("0.5:2, 0:-1 ,x:y,1:1,0.25:0.1234") == "0:-1,0.25:0.123,0.5:1"
    )
    assert core.clean_draw(None) == "" == core.clean_draw("nonsense")
    p = clamp(Patch(lfo2_draw="0.75:9,0:0"))
    assert p.lfo2_draw == "0:0,0.75:1"
    y = core.lfo_ext("drawn", 1.0, SR, SR, 0.0, 0, "0:-1,0.5:1")
    t = np.cumsum(np.ones(SR)) / SR
    assert np.allclose(y, np.interp(t % 1, [-0.5, 0, 0.5, 1], [1, -1, 1, -1]))


def test_lfo_2_and_3_sync_and_run_free_from_the_notes_seed():
    p = Patch(lfo2_sync="1/8", lfo3_sync="1 bar")
    q = at_bpm(p, 120.0)
    assert (q.lfo2_rate, q.lfo3_rate) == (4.0, 0.5)
    free = Patch(lfo2_mode="free", lfo2_shape="saw")
    a = synth_mod.general_lfo(free, 2, 4410, SR, 261.63)
    b = synth_mod.general_lfo(free, 2, 4410, SR, 293.66)
    assert not np.array_equal(a, b)  # each note picks the cycle up elsewhere...
    assert np.array_equal(
        a, synth_mod.general_lfo(free, 2, 4410, SR, 261.63)
    )  # ...the same each time


def test_envelopes_3_and_4_are_gated_as_the_filter_envelope_is():
    p = Patch(env3_attack=0.05, env3_decay=0.1, env3_sustain=0.5, env3_release=0.1)
    n = int(0.6 * SR)
    e = synth_mod._mod_source(p, "env3", n, SR, 1.0, 0.3, 0.6, 261.63)
    assert e.max() == pytest.approx(1.0, abs=1e-3)
    assert e[int(0.25 * SR)] == pytest.approx(0.5, abs=1e-3)
    assert np.all(e[int(0.41 * SR) :] == 0.0)  # released over its own 0.1 s


def test_the_key_random_wheel_and_aftertouch_sources():
    src = synth_mod._mod_source
    p = Patch(mod_wheel=0.25, aftertouch=np.linspace(0, 1, 100))
    n = 1000
    assert src(p, "key", n, SR, 1.0, None, 0.1, 261.63)[0] == pytest.approx(
        0.0, abs=1e-5
    )
    assert src(p, "key", n, SR, 1.0, None, 0.1, 1046.5)[0] == pytest.approx(
        0.4, abs=1e-4
    )
    r1 = src(p, "random", n, SR, 1.0, None, 0.1, 261.63)
    r2 = src(p, "random", n, SR, 1.0, None, 0.1, 277.18)
    assert -1 <= r1[0] <= 1 and r1[0] != r2[0] and np.ptp(r1) == 0
    assert np.all(src(p, "wheel", n, SR, 1.0, None, 0.1, 261.63) == 0.25)
    at = src(p, "aftertouch", n, SR, 1.0, None, 0.1, 261.63)
    assert at[0] == 0.0 and at[-1] == 1.0  # a drawn pressure, per sample


@pytest.mark.parametrize("slot", ["mod5", "mod9", "mod12"])
def test_a_route_in_slots_5_to_12_plays(slot):
    base = Patch(filter_type="lowpass", cutoff=600.0)
    wired = replace(
        base, **{f"{slot}_src": "lfo2", f"{slot}_dst": "cutoff", f"{slot}_amt": 0.8}
    )
    assert np.abs(_note(wired) - _note(base)).max() > 0.01


@pytest.mark.parametrize(
    "dst, extra",
    [
        ("filter2_cutoff", {"filter2_route": "serial"}),
        ("filter2_resonance", {"filter2_route": "serial"}),
        ("wt_pos", {"osc1": "wavetable", "wt_table": "scan"}),
        ("fm_amount", {"osc2": "sine", "osc2_octave": 0}),
        ("fm_index", {"osc1": "fm"}),
        ("fm4_level2", {"osc1": "fm4"}),
        ("formant_vowel", {"formant_amount": 0.8}),
    ],
)
def test_every_new_target_moves_the_sound(dst, extra):
    base = Patch(**extra)
    wired = replace(base, mod1_src="env3", mod1_dst=dst, mod1_amt=0.9, env3_sustain=1.0)
    assert np.abs(_note(wired) - _note(base)).max() > 0.01, dst
