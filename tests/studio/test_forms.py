import json
import math

from synthlab.params import clamp
from synthlab.studio.factory import FACTORY
from synthlab.studio.forms import patch_from_form
from synthlab.studio.library import LIBRARY_PATH
from synthlab.studio.paramui import PARAM_GROUPS
from synthlab.synth import Patch, preset


def _all_specs():
    return [spec for _, specs in PARAM_GROUPS for spec in specs]


def test_patch_from_form_parses_and_clamps():
    base = preset("bass_reese")
    p = patch_from_form(
        {"cutoff": "999999", "resonance": "0.5", "osc2": "", "unison": "3"}, base
    )
    assert p.cutoff == 16000.0  # clamped to the range's max (16k, not 8k)
    assert p.resonance == 0.5
    assert p.osc2 is None  # "" encodes None
    assert p.unison == 3
    assert p.attack == base.attack  # missing field keeps base value


def test_patch_from_form_ignores_garbage():
    base = Patch()
    p = patch_from_form({"cutoff": "banana", "nonsense": "1"}, base)
    assert p.cutoff == base.cutoff


def test_roundtrip_default_patch():
    base = preset("lead_supersaw")
    form = {}
    for s in _all_specs():
        v = getattr(base, s.name)
        form[s.name] = "" if v is None else str(v)
    p = patch_from_form(form, Patch())
    for s in _all_specs():
        assert getattr(p, s.name) == getattr(base, s.name), s.name


def test_specs_carry_units_and_defaults():
    """Readouts were bare numbers — '1200' under 'F.Atk' teaches nothing
    Range specs carry a unit and the Patch default so the chip
    can say '1200 Hz' and double-click can restore the default."""
    from synthlab.studio.paramui import spec_of

    assert spec_of("cutoff").unit == "Hz"
    assert spec_of("attack").unit == "s"
    assert spec_of("osc1_detune").unit == "ct"
    assert spec_of("cutoff").default == Patch().cutoff
    assert spec_of("cutoff").log is True
    assert spec_of("resonance").log is False


def test_form_accepts_new_filter_types_and_16k():
    """The engine implements highpass/notch/none (core.biquad) but the UI
    pills rejected them and capped cutoff at 8 kHz."""
    p = patch_from_form({"filter_type": "notch", "cutoff": "16000"}, Patch())
    assert p.filter_type == "notch"
    assert p.cutoff == 16000.0
    p = patch_from_form({"filter_type": "none"}, Patch())
    assert p.filter_type == "none"


# --- params.clamp -------------------------------------------------------


def test_clamp_patch_is_identity_on_default_and_presets():
    assert clamp(Patch()) == Patch()
    for p in (preset("bass_reese"), preset("lead_supersaw")):
        assert clamp(p) == p


def test_clamp_patch_is_identity_on_every_factory_sound():
    """Clamping must never rewrite a legitimate shipped sound — including
    ones that intentionally go outside the breed/UI musical range (a
    negative filter_env, osc1='noise')."""
    for entry in FACTORY:
        p = Patch(**entry["patch"])
        assert clamp(p) == p, entry["name"]


def test_clamp_patch_is_identity_on_every_library_sound():
    entries = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    assert entries, "library.json should be non-empty"
    for entry in entries:
        p = Patch(**entry["patch"])
        assert clamp(p) == p, entry["name"]


def test_clamp_patch_bounds_unison_and_envelope_times():
    p = clamp(Patch(unison=100_000_000, attack=1e7, decay=1e7, release=1e7))
    assert p.unison <= 16
    assert p.attack <= 20.0
    assert p.decay <= 20.0
    assert p.release <= 20.0


def test_clamp_patch_replaces_non_finite_with_default():
    p = clamp(Patch(cutoff=float("nan")))
    assert math.isfinite(p.cutoff)
    assert p.cutoff == Patch().cutoff
    p = clamp(Patch(resonance=float("inf")))
    assert p.resonance == Patch().resonance


def test_clamp_patch_replaces_bad_category_with_default():
    p = clamp(Patch(osc1="not-a-shape"))
    assert p.osc1 == Patch().osc1
    p = clamp(Patch(filter_type="not-a-filter"))
    assert p.filter_type == Patch().filter_type


def test_clamp_patch_leaves_automation_arrays_alone():
    import numpy as np

    curve = np.array([0.0, 100.0, 500.0, 9999.0])  # per-sample automation
    p = clamp(Patch(cutoff=curve))
    assert p.cutoff is curve


def test_clamp_patch_replaces_a_json_list_with_default():
    """JSON can never hold a legitimate automation array (those are
    np.ndarray, only ever built at render time — never persisted as a
    Patch field), so a plain list in a numeric field is untrusted input
    impersonating automation, not automation. It must be replaced, not
    passed through: a list surviving into the engine breaks every render
    that touches the field."""
    p = clamp(Patch(cutoff=[1e9]))
    assert p.cutoff == Patch().cutoff
    assert isinstance(p.cutoff, float)


def test_clamp_patch_leaves_untabled_fields_alone():
    p = clamp(Patch(name="a very silly name indeed"))
    assert p.name == "a very silly name indeed"
