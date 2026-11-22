"""A unit's band, the utility and the convolution on the live keys: a held
note through a banded worklet unit, a banded native unit (between its
split and join nodes), a utility, and a convolution on a clip picked from
the unit's impulse select. Signal flow only (the master analyser's level,
the worklet's voice count): nobody has listened."""

from __future__ import annotations

import numpy as np

from synthlab.core import SR
from synthlab.studio.clips import save_clip

LAYER_FX = 'select.fx-adder[aria-label="add effect to Layer 1 FX"]'
MASTER_FX = 'select.fx-adder[aria-label="add effect to Master FX"]'


def _held(studio):
    page = studio.open("editor")
    studio.settle()
    studio.start_voice()
    studio.wait_silence()
    page.evaluate("window.synthlab.piano.noteOn('held', 60, 1)")
    studio.wait_meter(r"1 voice")
    return page


def test_a_banded_unit_keeps_the_keys_live_and_sounding(studio):
    page = _held(studio)
    page.select_option(LAYER_FX, "drive")
    page.wait_for_selector('.fx-unit button[aria-label="remove drive"]')
    page.select_option('select.fx-band[aria-label="Drive band"]', "low")
    page.wait_for_function(
        "() => window.synthlab.piano.patch().fx?.[0]?.band === 'low'"
    )
    studio.settle()
    assert page.evaluate("window.synthlab.piano.patch().worklet_ok") is True
    page.wait_for_timeout(300)
    assert studio.level() > 0.01
    # a native unit on the side: its split and join nodes round the reverb
    page.select_option(MASTER_FX, "reverb")
    page.wait_for_selector('.fx-unit button[aria-label="remove reverb"]')
    page.select_option('select.fx-band[aria-label="Reverb band"]', "ms_side")
    page.wait_for_function(
        "() => window.synthlab.piano.patch().master?.[0]?.band === 'ms_side'"
    )
    studio.settle()
    assert page.evaluate("window.synthlab.piano.patch().worklet_ok") is True
    page.wait_for_timeout(300)
    assert studio.level() > 0.01 and "1 voice" in studio.meter()
    # and back to the whole signal
    page.select_option('select.fx-band[aria-label="Drive band"]', "full")
    page.wait_for_function(
        "() => { const u = window.synthlab.piano.patch().fx?.[0];"
        " return !!u && !u.band; }"
    )
    page.evaluate("window.synthlab.piano.noteOff('held')")
    studio.wait_meter(r"0 voices")


def test_a_utility_and_a_convolution_on_a_clip_play_under_a_held_note(studio):
    t = np.arange(int(0.4 * SR)) / SR
    room = (
        np.random.default_rng(3).standard_normal((len(t), 2))
        * np.exp(-t / 0.1)[:, None]
    )
    save_clip(studio.server.data_dir, "room tone", room * 0.3)
    page = _held(studio)
    page.select_option(LAYER_FX, "utility")
    page.wait_for_selector('.fx-unit button[aria-label="remove utility"]')
    studio.settle()
    assert page.evaluate("window.synthlab.piano.patch().worklet_ok") is True
    page.select_option(MASTER_FX, "convolve")
    page.wait_for_selector('.fx-unit button[aria-label="remove convolve"]')
    imp = 'select[aria-label="Convolution impulse"]'
    assert page.locator(f"{imp} option[value='clip:room tone']").count() == 1
    page.select_option(imp, "clip:room tone")
    page.wait_for_function(
        "() => (window.synthlab.piano.patch().master?.[0]?.ir || '')"
        ".includes('kind=clip')"
    )
    studio.settle()
    assert page.evaluate("window.synthlab.piano.patch().worklet_ok") is True
    page.wait_for_timeout(400)
    assert studio.level() > 0.01 and "1 voice" in studio.meter()
    page.evaluate("window.synthlab.piano.noteOff('held')")
    studio.wait_meter(r"0 voices")
