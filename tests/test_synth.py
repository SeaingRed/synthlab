from dataclasses import replace

import numpy as np

from synthlab.synth import Patch, Synth


def test_note_survives_release_longer_than_duration():
    audio = Synth(replace(Patch(), release=2.0)).note("C3", 1.0)
    assert float(np.max(np.abs(audio))) > 0.1


# --- new modulation routes: each must audibly change the render ------------


def _render(patch):
    return Synth(patch).note("A2", 0.5)


def _differs(a, b):
    return not np.allclose(a, b, atol=1e-6)


def test_old_preset_json_still_loads():
    # presets saved before the new fields existed lack those keys entirely
    old = {"name": "legacy", "osc1": "saw", "cutoff": 800.0}
    p = Patch(**old)
    assert p.pitch_env == 0.0 and p.fm_amount == 0.0
    assert np.all(np.isfinite(_render(p)))


def test_filter_lfo_sweeps_the_cutoff():
    base = replace(Patch(), cutoff=600, filter_env=0)
    swept = replace(base, filter_lfo_rate=4.0, filter_lfo_depth=2000.0)
    assert _differs(_render(base), _render(swept))
    assert np.all(np.isfinite(_render(swept)))


def test_pitch_env_bends_the_start():
    base = Patch()
    zap = replace(base, pitch_env=-12.0, pitch_env_decay=0.1)
    assert _differs(_render(base), _render(zap))
    up = replace(base, pitch_env=24.0, pitch_env_decay=0.05)
    assert np.all(np.isfinite(_render(up)))


def test_fm_needs_osc2_and_changes_sound():
    base = replace(Patch(), osc2="sine", osc2_octave=0)
    fm = replace(base, fm_amount=0.6)
    assert _differs(_render(base), _render(fm))
    # without osc2 there is no modulator: fm_amount must be a no-op
    solo = replace(Patch(), fm_amount=0.6)
    assert not _differs(_render(Patch()), _render(solo))


def test_ring_mod_needs_osc2_and_changes_sound():
    base = replace(Patch(), osc2="sine", osc2_octave=0)
    ring = replace(base, ring_mod=0.8)
    assert _differs(_render(base), _render(ring))
    solo = replace(Patch(), ring_mod=0.8)
    assert not _differs(_render(Patch()), _render(solo))


def test_array_automation_on_every_curvable_param():
    """Drawn curves hand Synth.note per-sample arrays for these params —
    the engine's scalar-truth gates must all accept them."""
    from synthlab.studio.curves import CURVABLE

    n = int(0.5 * 44100 * 1.0)  # longer than the render: as_array resamples
    ramp = np.linspace(0.0, 1.0, n)
    for param, (lo, hi) in CURVABLE.items():
        p = replace(Patch(), osc2="sine", **{param: lo + ramp * (hi - lo)})
        audio = Synth(p).note("A2", 0.5)
        assert np.all(np.isfinite(audio)), param


def test_array_automation_of_any_length_is_resampled():
    """A curve that is not one value per sample (control rate, or drawn for
    a longer note) is stretched over the note, as `as_array` promises. Most
    fields raised a broadcast error instead, and the native stack — which
    did resample — disagreed with the Python oscillator loop. The reference
    is the same curve handed in already resampled, which must not move."""
    from synthlab.core import as_array
    from synthlab.studio.curves import CURVABLE

    n = int(0.5 * 44100)
    shapes = {"wt_pos": "wavetable"}  # the shapes only the Python loop plays
    for param, (lo, hi) in CURVABLE.items():
        if param == "sampler_stretch":
            continue  # no sample: silence either way (tests/test_sampler.py)
        base = replace(Patch(), osc1=shapes.get(param, "saw"), osc2="sine")
        for length in (64, n + 4321):
            curve = lo + np.linspace(0.0, 1.0, length) ** 2 * (hi - lo)
            exact = replace(base, **{param: as_array(curve, n)})
            for loop in (False, True):
                got, want = Synth(replace(base, **{param: curve})), Synth(exact)
                if loop:  # the per-voice loop, as SYNTHLAB_NATIVE=0 runs it
                    got._native_stack = want._native_stack = lambda *a, **k: None
                np.testing.assert_array_equal(
                    got.note("A2", 0.5), want.note("A2", 0.5), str((param, length))
                )
    # a pluck too, and a routed field: the route adds a per-sample source
    p = replace(Patch(), osc1="pluck", mod1_src="lfo", mod1_dst="cutoff", mod1_amt=0.4)
    curve = np.linspace(300.0, 4000.0, 64)
    np.testing.assert_array_equal(
        Synth(replace(p, cutoff=curve)).note("A2", 0.5),
        Synth(replace(p, cutoff=as_array(curve, n))).note("A2", 0.5),
    )


def test_filter_release_longer_than_the_note_keeps_its_sweep():
    """Without a gate the hold is what the release leaves, and a release as
    long as the note left none: `adsr` came back all zeros. The amp
    envelope was guarded against that, the filter envelope and the
    fenv/aenv mod sources were not, so the filter sat shut at its cutoff."""
    from synthlab.synth import _mod_routes

    p = replace(Patch(), cutoff=200.0, filter_env=6000.0, filter_release=2.0)
    assert _differs(_render(p), _render(replace(p, filter_env=0.0)))
    n = int(0.5 * 44100)
    for src in ("fenv", "aenv"):
        wired = replace(
            p, release=2.0, mod1_src=src, mod1_dst="resonance", mod1_amt=0.5
        )
        routed = _mod_routes(wired, n, 44100, 1.0, None)[0].resonance
        assert float(np.ptp(routed)) > 0.1, src
    # a gate holds the key itself, and the filter then closes over its own
    # release from the key-up (it used not to be read at all: every release
    # spanned the buffer's tail): a shorter one shuts the filter sooner
    gated = Synth(p).note("A2", 0.5, gate=0.3)
    short = Synth(replace(p, filter_release=0.1)).note("A2", 0.5, gate=0.3)
    up = round(0.3 * 44100)
    np.testing.assert_array_equal(gated[:up], short[:up])
    assert _differs(gated[up:], short[up:])
    # ... and the buffer does not set it: one longer than the 0.2 s left is
    # cut where the note ends, not fitted into it (the amp envelope lands
    # the note; fitted, 2.0 s and 0.2 s would be one sound)
    fits = Synth(replace(p, filter_release=0.2)).note("A2", 0.5, gate=0.3)
    assert _differs(gated[up:], fits[up:])
    # so with an amp release that fits too, a longer buffer holds the same
    # note: the same samples, then silence
    q = replace(p, release=0.1)
    note, longer = (Synth(q).note("A2", d, gate=0.3) for d in (0.5, 0.9))
    np.testing.assert_allclose(longer[: len(note)], note, atol=1e-12)
    assert np.any(note[up:]) and not np.any(longer[up + round(0.1 * 44100) :])


def test_array_resonance_actually_sweeps():
    flat = replace(Patch(), resonance=0.1)
    n = int(0.5 * 44100)
    swept = replace(Patch(), resonance=np.linspace(0.0, 0.95, n))
    assert _differs(_render(flat), _render(swept))


def test_mod_matrix_routes_change_sound():
    base = Patch()
    for src in ("lfo", "fenv", "aenv"):
        wired = replace(base, mod1_src=src, mod1_dst="cutoff", mod1_amt=-0.6)
        assert _differs(_render(base), _render(wired)), src
        assert np.all(np.isfinite(_render(wired))), src
    # velocity source scales with the played velocity
    vel_mod = replace(base, mod1_src="velocity", mod1_dst="resonance", mod1_amt=0.8)
    soft = Synth(vel_mod).note("A2", 0.5, velocity=0.2)
    hard = Synth(vel_mod).note("A2", 0.5, velocity=1.0)
    assert _differs(soft * 5.0, hard)


def test_mod_matrix_inactive_is_a_noop():
    base = Patch()
    off = replace(base, mod1_src="none", mod1_amt=0.9)
    zero = replace(base, mod1_src="lfo", mod1_amt=0.0)
    np.testing.assert_allclose(_render(base), _render(off))
    np.testing.assert_allclose(_render(base), _render(zero))


def test_mod_matrix_two_slots_stack_on_one_target():
    one = replace(Patch(), mod1_src="lfo", mod1_dst="cutoff", mod1_amt=-0.5)
    both = replace(one, mod2_src="fenv", mod2_dst="cutoff", mod2_amt=0.5)
    assert _differs(_render(one), _render(both))
    assert np.all(np.isfinite(_render(both)))


def test_extreme_new_params_stay_finite():
    p = replace(
        Patch(),
        osc2="saw",
        fm_amount=1.0,
        ring_mod=1.0,
        pitch_env=24.0,
        pitch_env_decay=0.02,
        filter_lfo_rate=8.0,
        filter_lfo_depth=4000.0,
        resonance=0.95,
        drive=4.0,
    )
    assert np.all(np.isfinite(_render(p)))


def test_new_filter_types_render():
    """highpass/notch/none were engine-only (JSON-load-only) until the UI
    grew pills for them — pin that all three render finite, non-silent audio."""
    from dataclasses import replace

    for ft in ("highpass", "notch", "none"):
        x = Synth(replace(Patch(), filter_type=ft)).note(220, 0.2)
        assert np.all(np.isfinite(x)), ft
        assert float(np.max(np.abs(x))) > 0, ft


def test_from_dict_drops_unknown_fields(caplog):
    p = Patch.from_dict({**Patch().to_json(), "retired_knob": 3})
    assert p == Patch()
    assert "retired_knob" in caplog.text


def test_to_json_excludes_voice_fx_only():
    p = Patch()
    d = p.to_json()
    assert "voice_fx" not in d
    assert d == {k: v for k, v in vars(p).items() if k != "voice_fx"}


def test_from_dict_ignores_voice_fx_even_if_present():
    # voice_fx holds callables, never JSON — from_dict must not try to pass
    # it through to the dataclass constructor.
    p = Patch.from_dict({**Patch().to_json(), "voice_fx": ["not callable"]})
    assert p.voice_fx == []
