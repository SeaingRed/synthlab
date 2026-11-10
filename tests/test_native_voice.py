"""Synth.note with the native unison stack (`Synth._native_stack`) against
the Python per-voice loop it replaces, and with the native post chain
(`Synth._native_post`) against the stages it replaces, over every factory +
library sound and synthetic patches that reach each branch of both."""

import os

import numpy as np
import pytest

from synthlab import _native
from synthlab.studio.factory import FACTORY
from synthlab.studio.library import LIBRARY
from synthlab.synth import Patch, Synth

pytestmark = pytest.mark.skipif(
    os.environ.get("SYNTHLAB_NATIVE") == "0", reason="SYNTHLAB_NATIVE=0"
)


def _sounds() -> list[Patch]:
    out = []
    for e in list(FACTORY) + list(LIBRARY):
        d = e.get("patch", e)
        out.append(d if isinstance(d, Patch) else Patch.from_dict(d))
    return out


SYNTHETIC = [
    Patch(osc1="saw", osc2="square", fm_amount=0.6, unison=3, unison_detune=18.0),
    Patch(osc1="sine", osc2="triangle", ring_mod=0.7, osc2_octave=1, osc2_detune=7.0),
    Patch(osc1="supersaw", osc2="supersaw", unison=2, sub_level=0.5),
    Patch(osc1="fm", fm_ratio=3.0, fm_index=4.0, fm_decay=0.3, unison=4),
    Patch(osc1="noise", noise_color="pink", osc2="saw", noise_level=0.2),
    Patch(osc1="square", pwm_depth=0.6, pwm_rate=3.0, unison=5, unison_spread=0.8),
    Patch(osc1="wavetable", wt_table="vocal", wt_pos=0.6, unison=3),
    Patch(osc1="wavetable", osc2="saw", fm_amount=0.5, ring_mod=0.3, wt_pos=1.0),
    Patch(osc1="sine", osc2="wavetable", wt_table="digital", wt_pos=0.25, unison=2),
    Patch(osc1="wavetable", wt_table="no-such-table", pitch_env=24.0, sub_level=0.4),
]


def _both(p: Patch, pitch=48, dur=0.8, **kw):
    native = Synth(p).note(pitch, dur, **kw)
    python = Synth(p)
    python._native_stack = lambda *a, **k: None  # the per-voice loop
    return native, python.note(pitch, dur, **kw)


@pytest.mark.parametrize("p", _sounds() + SYNTHETIC, ids=lambda p: p.name)
def test_native_stack_matches_the_python_loop(p):
    got, want = _both(p)
    peak = float(np.max(np.abs(want))) or 1.0
    # the two sum and scale in the same order; what differs is libm's pow
    # and sin against dsp::math's, a few ulp per sample
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9 * peak)


def test_native_stack_follows_curves_and_pan():
    n = 44100
    p = Patch(
        osc1="saw",
        osc2="saw",
        unison=3,
        osc1_level=np.linspace(0.2, 1.0, n),
        unison_detune=np.linspace(0.0, 30.0, n),
        osc2_detune=np.linspace(-12.0, 12.0, n),
    )
    got, want = _both(p, dur=1.0, pan=-0.4)
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9)


def test_native_stack_sweeps_a_wavetable():
    n = 44100
    p = Patch(
        osc1="wavetable",
        osc2="wavetable",
        wt_table="harmonic",
        wt_pos=np.linspace(0.0, 1.0, n),
        osc2_octave=2,
        unison=3,
        pitch_env=36.0,  # the mip level moves under the sweep
    )
    got, want = _both(p, dur=1.0, pan=0.3)
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9)


def _post_both(monkeypatch, p: Patch, pitch=48, dur=0.8, **kw):
    """(the note through the native post chain, the note with the chain's
    stages called one by one)"""
    assert _native.kernel("post") is not None
    native = Synth(p).note(pitch, dur, **kw)
    with monkeypatch.context() as m:
        m.setattr(_native, "OFF", _native.OFF | {"post"})
        loose = Synth(p).note(pitch, dur, **kw)
    return native, loose


_N = 35280  # 0.8 s
POST = {
    "ladder": Patch(filter_type="ladder", drive=2.4, resonance=0.7),
    "ladder-no-drive": Patch(filter_type="ladder", drive=0.0),
    "lowpass-driven": Patch(filter_type="lowpass", drive=3.0),
    "highpass": Patch(filter_type="highpass", cutoff=900.0, resonance=0.5),
    "bandpass": Patch(filter_type="bandpass", drive=1.5),
    "notch": Patch(filter_type="notch", cutoff=1500.0),
    "none-driven": Patch(filter_type="none", drive=2.0),
    "none": Patch(filter_type="none"),
    # no envelope, no LFO: the cutoff stands still (the biquad's static path)
    "static-ladder": Patch(filter_type="ladder", filter_env=0.0, drive=1.8),
    "static-lowpass": Patch(filter_type="lowpass", filter_env=0.0),
    "filter-lfo": Patch(filter_lfo_depth=900.0, filter_lfo_rate=7.0),
    "tremolo": Patch(tremolo_depth=0.6, tremolo_rate=6.0, filter_type="lowpass"),
    "vowel": Patch(formant_amount=0.8, formant_vowel=0.4, drive=2.0),
    "vowel-lowpass": Patch(formant_amount=1.0, filter_type="lowpass", drive=2.5),
    "routed": Patch(
        mod1_src="lfo",
        mod1_dst="cutoff",
        mod1_amt=0.5,
        mod2_src="aenv",
        mod2_dst="resonance",
        mod2_amt=0.6,
        mod3_src="fenv",
        mod3_dst="drive",
        mod3_amt=0.4,
    ),
    "curves-ladder": Patch(
        cutoff=np.geomspace(200.0, 6000.0, _N),
        resonance=np.linspace(0.0, 0.9, _N),
        drive=np.linspace(0.0, 4.0, _N),
        filter_env=np.linspace(0.0, 3000.0, _N),
    ),
    "curves-bandpass": Patch(
        filter_type="bandpass",
        cutoff=np.geomspace(4000.0, 300.0, 50),  # control rate: resampled
        resonance=np.linspace(0.9, 0.1, _N),
        drive=np.linspace(0.5, 3.0, _N),
        key_track=np.linspace(0.0, 1.0, _N),
        tremolo_depth=np.linspace(0.0, 0.5, _N),
    ),
    "curves-vowel": Patch(
        formant_amount=np.linspace(0.0, 1.0, _N),
        formant_vowel=np.linspace(0.0, 1.0, _N),
        filter_lfo_depth=np.linspace(0.0, 800.0, _N),
    ),
    # resonance moves, cutoff doesn't: the first ladder stage sweeps, the
    # second is static
    "ladder-res-curve": Patch(filter_env=0.0, resonance=np.linspace(0.0, 0.9, _N)),
    "stereo-source": Patch(osc1="pluck", unison=3, unison_spread=1.0),
}


@pytest.mark.parametrize("p", _sounds(), ids=lambda p: p.name)
def test_native_post_matches_its_stages_on_every_sound(monkeypatch, p):
    got, want = _post_both(monkeypatch, p)
    # the same kernels in the same order on the same numbers
    np.testing.assert_array_equal(got, want)


@pytest.mark.parametrize("name", POST)
@pytest.mark.parametrize("gate", [None, 0.3, 5.0], ids=["gate-none", "gate", "held"])
def test_native_post_matches_its_stages(monkeypatch, name, gate):
    got, want = _post_both(monkeypatch, POST[name], gate=gate, velocity=0.8, pan=-0.3)
    assert got.shape == want.shape == (_N, 2)
    np.testing.assert_array_equal(got, want)


@pytest.mark.parametrize(
    "name", ["ladder", "lowpass-driven", "vowel", "none", "routed"]
)
@pytest.mark.parametrize(
    "dur", [0.0, 1 / 44100, 63 / 44100, 64 / 44100, 65 / 44100, 0.004, 0.06]
)
def test_native_post_matches_its_stages_on_short_notes(monkeypatch, name, dur):
    got, want = _post_both(monkeypatch, POST[name], dur=dur)
    assert len(got) == len(want) >= 1
    np.testing.assert_array_equal(got, want)


def test_native_post_matches_the_numpy_engine(monkeypatch):
    """The whole note, native against every kernel on its NumPy twin: what
    separates them is each kernel's own rounding (the swept filter's bar,
    through a ladder's two stages)."""
    for name in ("ladder", "bandpass", "curves-ladder", "vowel", "tremolo"):
        native = Synth(POST[name]).note(48, 0.8)
        with monkeypatch.context() as m:
            m.setattr(_native, "ENABLED", False)
            numpy = Synth(POST[name]).note(48, 0.8)
        peak = float(np.max(np.abs(numpy))) or 1.0
        np.testing.assert_allclose(
            native, numpy, rtol=0, atol=1e-7 * peak, err_msg=name
        )


def test_native_post_is_one_call(monkeypatch):
    """The chain goes to the kernel whole (twice around the vowel filter),
    and not at all once it, or a kernel it is made of, is switched off."""
    calls = []
    real = _native.kernel("post")

    def spy(x, sr, stages):
        calls.append([s[0] for s in stages])
        return real(x, sr, stages)

    monkeypatch.setattr(_native._dsp, "post", spy, raising=False)
    Synth(POST["ladder"]).note(48, 0.2)
    Synth(POST["lowpass-driven"]).note(48, 0.2)
    Synth(POST["vowel"]).note(48, 0.2)
    assert calls == [
        ["dc", "svf", "clip", "svf", "gain"],
        ["dc", "svf", "gain", "clip"],
        ["dc", "svf", "clip", "svf"],
        ["gain"],
    ]
    calls.clear()
    for off in ("post", "swept_svf", "soft_clip", "dc_block", "lfilter"):
        monkeypatch.setattr(_native, "OFF", frozenset({off}))
        Synth(POST["ladder"]).note(48, 0.2)
        assert calls == [], off


def test_a_wavetable_goes_back_to_the_loop_with_its_kernel_off(monkeypatch):
    p = Patch(osc1="wavetable", unison=2)
    assert Synth(p)._native_stack(p, np.full(64, 220.0), 64, 2, 0.0) is not None
    monkeypatch.setattr(_native, "OFF", frozenset({"wavetable"}))
    assert Synth(p)._native_stack(p, np.full(64, 220.0), 64, 2, 0.0) is None


def test_native_stack_draws_noise_through_core(monkeypatch):
    """The parity corpus reseeds the engine by swapping `core.noise`; the
    native stack must pick the swap up exactly as `core.osc` does."""
    from synthlab import core

    plain = core.noise
    monkeypatch.setattr(
        core,
        "noise",
        lambda n, color="white", seed=None: plain(n, color, (seed or 0) + 7),
    )
    p = Patch(osc1="noise", osc2="saw", noise_level=0.3, unison=2)
    got, want = _both(p)
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9)
