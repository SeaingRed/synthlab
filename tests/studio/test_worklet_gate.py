"""Which patches the AudioWorklet live voice can play, decided in Python.

Before this, the gate was `workletCanPlay` in piano.js: a JS mirror of the
worklet's own capabilities that could silently drift from what
voice-worklet.js actually does. `worklet.py` is the one classification of
every Patch field, and `/patch.json` carries its verdict so piano.js only
ever reads `worklet_ok` — it never re-derives the answer."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import worklet
from synthlab.studio.server import create_app
from synthlab.synth import Patch


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path))


def test_default_patch_is_playable():
    assert worklet.can_play(Patch())
    assert worklet.blockers(Patch()) == []


@pytest.mark.parametrize(
    "change",
    [
        # what the worklet still can't reproduce (the parity corpus test
        # proves everything the gate does admit)
        {"unison": 9},
        # the engine clips a non-ladder note whole once any sample's drive
        # passes 1: an LFO that starts on the wrong side only gets there later
        {
            "filter_type": "lowpass",
            "mod3_src": "lfo",
            "mod3_dst": "drive",
            "mod3_amt": 0.4,
        },
    ],
)
def test_gated_changes_force_wav(change):
    p = replace(Patch(), **change)
    assert not worklet.can_play(p)
    assert set(change) & set(worklet.blockers(p))


def test_mod_slot_gate_is_inert_with_zero_amount():
    """A wired source with amt == 0 has no audible effect (_apply_mod_matrix
    skips it too), so it must not force the WAV fallback."""
    p = replace(Patch(), mod2_src="lfo", mod2_amt=0.0)
    assert worklet.can_play(p)


@pytest.mark.parametrize(
    "change",
    [
        # each measured by test_live_parity_corpus.py
        {"formant_amount": 0.5},
        {"mod3_src": "lfo", "mod3_amt": 0.4},
        {"osc1": "pluck"},
        {"osc1": "noise"},
        {"osc2": "wavetable"},
        {"lfo_sync": "1/4"},
        {"lfo_shape": "square"},
        {"filter_lfo_shape": "noise"},
        {"filter_lfo_sync": "1/8"},
        {"noise_level": 0.1, "noise_color": "brown"},
        # a ladder's drive route is per sample, as the engine's is
        {"mod3_src": "lfo", "mod3_dst": "drive", "mod3_amt": 0.4},
        # a positive-going source clips the whole note from its first sample
        {
            "filter_type": "lowpass",
            "mod1_src": "aenv",
            "mod1_dst": "drive",
            "mod1_amt": 0.4,
        },
    ],
)
def test_live_play_2_admits(change):
    assert worklet.can_play(replace(Patch(), **change))


def test_every_factory_and_library_sound_plays_live():
    from synthlab.studio.factory import FACTORY, get_factory
    from synthlab.studio.library import LIBRARY, get_library

    rows = [get_factory(e["name"]) for e in FACTORY] + [
        get_library(e["name"]) for e in LIBRARY
    ]
    assert [p.name for p in rows if not worklet.can_play(p)] == []


def test_patch_json_carries_the_mod_tables_and_resolved_rates(client):
    """The worklet shares the engine's mod-matrix tables rather than
    retyping them, and gets synced LFO rates resolved against the bpm, as
    /note and the loop render them (synth.at_bpm)."""
    from synthlab.params import MOD_RANGES, MOD_SLOTS, MOD_SOURCES
    from synthlab.synth import synced_rate

    client.post("/fragment/patch", data={"lfo_sync": "1/8", "filter_lfo_sync": "1/4"})
    j = client.get("/patch.json").json()
    assert j["mod_slots"] == list(MOD_SLOTS)
    assert j["mod_sources"] == list(MOD_SOURCES)
    assert j["mod_ranges"] == {k: list(v) for k, v in MOD_RANGES.items()}
    bpm = client.app.state.svc.state.arrangement.bpm
    assert j["mod_lfo_rate"] == pytest.approx(synced_rate("1/8", bpm))
    assert j["filter_lfo_rate"] == pytest.approx(synced_rate("1/4", bpm))
    assert j["tables"].startswith("/wavetables.bin?v=")


def test_glide_does_not_force_wav():
    """Factory patches with glide > 0 must stay worklet-playable."""
    assert worklet.can_play(replace(Patch(), glide=0.35))


def test_patch_json_carries_gate(client):
    j = client.get("/patch.json").json()
    assert j["worklet_ok"] is True
    assert j["worklet_blockers"] == []
    assert "voice_fx" not in j  # to_json()'s one exclusion, unchanged


def test_patch_json_reports_blockers_for_a_gated_patch(client):
    client.post("/fragment/patch", data={"unison": "12"})
    j = client.get("/patch.json").json()
    assert j["worklet_ok"] is False
    assert "unison" in j["worklet_blockers"]


@pytest.mark.parametrize(
    "change",
    [
        {"osc2": "saw"},
        {"osc1": "supersaw"},
        {"osc1": "fm"},
        {"filter_type": "bandpass"},
        {"drive": 2.0},
        {"unison": 3},
        {"sub_level": 0.2},
        {"vibrato_depth": 0.1},
        {"pwm_depth": 0.3},
        {"tremolo_depth": 0.2},
        {"filter_lfo_depth": 500.0},
        {"pitch_env": 4.0},
        {"osc2": "sine", "fm_amount": 0.5, "ring_mod": 0.4},
        {"osc1_octave": 1, "osc1_detune": 5.0},
    ],
)
def test_the_worklet_plays_the_engine_chain(change):
    """Admitted since the worklet runs the engine's own chain — each one
    measured against Synth.note by test_live_parity_corpus.py."""
    assert worklet.can_play(replace(Patch(), **change))
