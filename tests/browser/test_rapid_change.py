"""Five quick steps on a value control leave the server on the fifth.

htmx 4 keeps the first request waiting on an element and drops the rest
("queue first"), so a control stepped faster than the server answers — a
held arrow key, wheel ticks, a MIDI knob — left the server on a stale value
while the page showed the last. `hx-sync="this:queue last"` on every
control that posts on `change` or `input` is the fix
(`tests/studio/test_change_posts.py` holds the attribute on every one);
this is the same thing seen from a keyboard, on one control of each kind:
a knob that posts itself and morphs its region (an FX parameter), two that
post themselves and swap nothing (a layer's level, a Song track's level),
and a debounced form (a sound parameter, in bursts either side of its
150 ms).

The steps are real key presses with no wait between them, and the proof is
the server's own page: loaded again, the control stands where the hand
left it. Nobody listened.
"""

from __future__ import annotations

import pytest

from tests.browser.test_song_playlist import use
from tests.studio.test_song_routes import song_dict

STEPS = 5
LAYER_FX = 'select.fx-adder[aria-label="add effect to Layer 1 FX"]'


def _value(page, selector: str) -> float:
    return float(page.eval_on_selector(selector, "(e) => e.value"))


def _step(page, selector: str, key: str, bursts: int = 1) -> float:
    """Focus the control and press `key` five times at once (`bursts` times,
    160 ms apart); the value the page is left showing."""
    page.focus(selector)
    for i in range(bursts):
        if i:
            page.wait_for_timeout(160)
        for _ in range(STEPS):
            page.keyboard.press(key)
    return _value(page, selector)


def _served(studio, view: str, selector: str) -> float:
    """What the server holds, as its page draws it."""
    studio.settle()
    studio.page.wait_for_timeout(250)  # a debounce still counting is not idle
    studio.settle()
    page = studio.open(view)
    page.wait_for_selector(selector)
    return _value(page, selector)


def test_an_fx_knob_stepped_five_times_lands_on_the_fifth(studio, server):
    page = studio.open("editor")
    page.select_option(LAYER_FX, "eq")
    page.wait_for_selector('.fx-unit button[aria-label="remove eq"]')
    studio.settle()
    knob = ".fx-unit label.fx-param input[type=range]"
    was = _value(page, knob)
    shown = _step(page, knob, "ArrowRight")
    assert shown > was
    studio.settle()
    # the answers to the earlier posts did not pull the knob back
    assert _value(page, knob) == shown
    assert _served(studio, "editor", knob) == pytest.approx(shown)


def test_a_layer_level_stepped_five_times_lands_on_the_fifth(studio, server):
    page = studio.open("editor")
    knob = "#l0-gain"
    page.wait_for_selector(knob)
    studio.settle()
    shown = _step(page, knob, "ArrowLeft")
    assert shown == pytest.approx(0.75)
    assert _served(studio, "editor", knob) == pytest.approx(shown)
    assert server.state.layers[0]["gain"] == pytest.approx(shown)


def test_a_song_tracks_level_stepped_five_times_lands_on_the_fifth(studio, server):
    use(server, song_dict())
    page = studio.open("song")
    knob = 'input.track-gain.knob[aria-label="Bass level"]'
    page.wait_for_selector(knob)
    studio.settle()
    shown = _step(page, knob, "ArrowLeft")
    assert shown == pytest.approx(0.75)
    assert _served(studio, "song", knob) == pytest.approx(shown)
    bass = next(t for t in server.state.song["tracks"] if t["id"] == "t1")
    assert bass["gain"] == pytest.approx(shown)


def test_a_debounced_sound_parameter_in_three_bursts_lands_on_the_last(studio, server):
    """The patch form waits 150 ms after the last input, which is no cover
    once a post is in flight: three bursts 160 ms apart are three posts.
    (A guard, not a reproduction: on a fast machine each post is answered
    inside the gap and this passed under htmx's default too; the three
    tests above are the ones that fail without the attribute.)"""
    page = studio.open("editor")
    knob = 'form[hx-post="/fragment/patch"] input[type=range]:not([disabled])'
    page.wait_for_selector(knob)
    studio.settle()
    was = _value(page, knob)
    lo, hi = (
        float(page.eval_on_selector(knob, f"(e) => e.{k}")) for k in ("min", "max")
    )
    key = "ArrowLeft" if was - lo > hi - was else "ArrowRight"
    shown = _step(page, knob, key, bursts=3)
    assert shown != was
    assert _served(studio, "editor", knob) == pytest.approx(shown, rel=1e-3)
