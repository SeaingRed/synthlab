# ruff: noqa: E501  (selectors and page scripts read better on one line)
"""The phase warp, your own wavetables and one-voice modes in a real browser
(tests/test_osc_warp_wavetables.py and tests/studio/test_wavetable_routes.py hold
the engine and the routes):

- the warp: the mode's select and the amount's knob in oscillator 1's
  block post their fields, and what the keys play moves (the master
  analyser's spectral centre — nobody listens: it proves the sound
  changed, not that it is good);
- your own wavetable: drawn in the "draw…" popover with the pointer, made,
  picked in the picker (its own radio), the live keys playing it; a
  built-in picked after it goes back; one made from a clip;
- one voice: legato holds one voice under two keys and goes back to the
  first when the second is let go; mono the same count, poly two.
"""

from __future__ import annotations

import numpy as np

from synthlab.core import SR
from synthlab.studio.clips import save_clip

from .test_osc_panels import _centre, _shape, _wait


def test_the_warp_block_posts_its_fields_and_the_sound_moves(studio, server):
    page = _shape(studio, server, "sine")
    block = page.locator('.warp-block[aria-label="Oscillator 1 warp"]')
    assert block.is_visible()
    assert block.locator("#p-osc1_warp").count() == 1
    assert block.locator("#p-osc1_warp_amt.knob").count() == 1
    before = _centre(studio)
    page.select_option("#p-osc1_warp", "pd")
    _wait(page, lambda: server.state.patch.osc1_warp == "pd", "the mode posted")
    page.focus("#p-osc1_warp_amt")
    page.keyboard.press("End")
    _wait(page, lambda: server.state.patch.osc1_warp_amt == 1.0, "the amount posted")
    _wait(
        page,
        lambda: page.evaluate("() => window.synthlab.piano.patch().osc1_warp") == "pd",
        "the keys' patch follows",
    )
    assert page.evaluate("() => window.synthlab.piano.patch().worklet_ok") is True
    after = _centre(studio)
    assert after > before * 1.5, (before, after)


def test_a_drawn_wavetable_is_made_picked_and_played(studio, server):
    page = _shape(studio, server, "wavetable")
    page.click('button[popovertarget="wt-draw"]')
    canvas = page.locator(".wt-canvas")
    assert canvas.is_visible()
    box = canvas.bounding_box()
    # a stroke: a ramp up across the cycle, then frame 2 a square
    page.mouse.move(box["x"] + 2, box["y"] + box["height"] - 4)
    page.mouse.down()
    for k in range(1, 21):
        page.mouse.move(
            box["x"] + k * box["width"] / 20 - 2,
            box["y"] + box["height"] * (1 - k / 20) + 2,
        )
    page.mouse.up()
    page.click('.wt-frame-tabs label:has(input[value="1"])')
    page.click('[data-wt-shape="square"]')
    points = page.eval_on_selector('.wt-draw-form input[name="points"]', "i => i.value")
    assert points.count(";") == 1 and len(points.split(";")[0].split(",")) == 64
    page.fill('.wt-draw-form input[name="name"]', "Ramp to square")
    page.click('.wt-draw-form button[type="submit"]')
    _wait(page, lambda: server.state.patch.wt_custom, "the table made and picked")
    tid = server.state.patch.wt_custom
    own = page.locator(f'.wt-thumb.wt-own:has(input[value="own:{tid}"]:checked)')
    _wait(page, lambda: own.count() == 1, "its own radio, checked")
    assert "Ramp to square" in own.inner_text()
    # the live keys read it (GET /wavetable/live/<id>.bin) and play it
    _wait(
        page,
        lambda: (
            page.evaluate("() => window.synthlab.piano.patch().wt_live")
            == f"/wavetable/live/{tid}.bin"
        ),
        "/patch.json names its payload",
    )
    studio.start_voice()
    assert page.evaluate("() => window.synthlab.piano.patch().worklet_ok") is True
    assert _centre(studio) > 0
    # a built-in picked after it: back to the built-ins
    page.click('.wt-thumb:has(input[value="vocal"])')
    _wait(
        page,
        lambda: (
            (server.state.patch.wt_custom, server.state.patch.wt_table) == ("", "vocal")
        ),
        "the built-in posted, the own table let go",
    )


def test_a_wavetable_is_made_from_a_clip(studio, server):
    t = np.arange(SR) / SR
    save_clip(
        server.data_dir,
        "hum",
        0.4 * np.sin(2 * np.pi * 110 * t) * np.sin(2 * np.pi * 0.5 * t),
    )
    page = _shape(studio, server, "wavetable")
    page.click('button[popovertarget="wt-from-clip"]')
    _wait(
        page,
        lambda: page.locator('#wt-sources option[value="clip:hum"]').count() == 1,
        "the sources read",
    )
    page.fill('#wt-from-clip input[name="frames"]', "8")
    page.click('#wt-from-clip label:has(input[value="pitch"])')
    page.click('#wt-from-clip button[type="submit"]')
    _wait(page, lambda: server.state.patch.wt_custom, "the table made and picked")
    _wait(
        page,
        lambda: page.locator(".wt-thumb.wt-own:has(:checked)").count() == 1,
        "its own radio",
    )


def _voices(studio, mode: str):
    page = studio.open("editor")
    studio.settle()
    page.click(f'#row-voice_mode label:has(input[value="{mode}"])')
    _wait(page, lambda: studio.server.state.patch.voice_mode == mode, f"{mode} posted")
    _wait(
        page,
        lambda: page.evaluate("() => window.synthlab.piano.patch().voice_mode") == mode,
        "the keys follow",
    )
    studio.start_voice()
    studio.wait_silence()
    return page


def test_legato_holds_one_voice_under_two_keys(studio, server):
    page = _voices(studio, "legato")
    page.keyboard.down("a")  # C
    studio.wait_meter(r"1 voice")
    studio.wait_sound()
    page.keyboard.down("d")  # E over it: the same voice, re-aimed
    page.wait_for_timeout(200)
    studio.wait_meter(r"1 voice")
    page.keyboard.up("d")  # back to C, still sounding
    page.wait_for_timeout(200)
    studio.wait_meter(r"1 voice")
    assert studio.level() > 1e-3
    page.keyboard.up("a")
    studio.wait_meter(r"0 voices")
    studio.wait_silence()


def test_mono_takes_the_voice_and_poly_does_not(studio, server):
    page = _voices(studio, "mono")
    page.keyboard.down("a")
    studio.wait_meter(r"1 voice")
    page.keyboard.down("d")
    page.wait_for_timeout(200)
    studio.wait_meter(r"1 voice")  # the first faded out under the second
    page.keyboard.up("a")
    page.keyboard.up("d")
    studio.wait_meter(r"0 voices")
    page.click('#row-voice_mode label:has(input[value="poly"])')
    _wait(
        page,
        lambda: (
            page.evaluate("() => window.synthlab.piano.patch().voice_mode") == "poly"
        ),
        "poly",
    )
    page.keyboard.down("a")
    page.keyboard.down("d")
    studio.wait_meter(r"2 voices")
    page.keyboard.up("a")
    page.keyboard.up("d")
    studio.wait_meter(r"0 voices")
