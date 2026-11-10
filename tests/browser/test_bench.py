# ruff: noqa: E501  (selectors and page scripts read better on one line)
"""The bench in a real browser (Synths, Bass, Voice).

- rule 1: at 1440, 1920 and 2560 the three views show under 5 % bare page
  background (pixels of `--bg` in the viewport's screenshot), in both
  themes;
- the mod matrix's patch bay draws one lead per active route, follows the
  form's fields, and a jack-to-jack drag sets those same fields;
- a value read-out takes a typed number through the control's own events;
- a right click on a row lists that row's own buttons;
- the rack's folds are open on a desktop window;
- the take's player and the docked agent drawer.
"""

from __future__ import annotations

import io
import time

import numpy as np
import pytest

from .conftest import wav_bytes

SIZES = ((1440, 900), (1920, 1080), (2560, 1440))
BG = {
    "night": (11, 13, 18),
    "day": (247, 245, 239),
}  # --bg: oklch(0.16 0.01 260) / (0.97 0.008 90)


def _wait(page, check, what: str, timeout: float = 6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = check()
        if got:
            return got
        page.wait_for_timeout(40)
    raise AssertionError(f"never: {what}")


def _bare(page, theme: str) -> float:
    """The share of the viewport's pixels that are the page background."""
    png = page.screenshot()
    try:
        from PIL import Image
    except ImportError:  # no Pillow in the venv: decode in the page instead
        return page.evaluate(
            """async ([b64, bg]) => {
              const img = new Image(); img.src = 'data:image/png;base64,' + b64; await img.decode();
              const c = new OffscreenCanvas(img.width, img.height), g = c.getContext('2d');
              g.drawImage(img, 0, 0);
              const d = g.getImageData(0, 0, img.width, img.height).data;
              let n = 0;
              for (let i = 0; i < d.length; i += 4)
                if (Math.abs(d[i] - bg[0]) <= 1 && Math.abs(d[i + 1] - bg[1]) <= 1 && Math.abs(d[i + 2] - bg[2]) <= 1) n++;
              return n / (d.length / 4);
            }""",
            [__import__("base64").b64encode(png).decode(), list(BG[theme])],
        )
    im = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(np.int16)
    return float((np.abs(im - np.array(BG[theme])).max(axis=2) <= 1).mean())


@pytest.mark.parametrize("theme", ["night", "day"])
def test_the_bench_views_show_under_five_percent_bare_background(studio, server, theme):
    page = studio.page
    page.context.add_cookies(
        [
            {"name": "theme", "value": theme, "url": server.url},
            {"name": "density", "value": "compact", "url": server.url},
        ]
    )
    worst = {}
    for view in ("editor", "bass", "voice"):
        for w, h in SIZES:
            page.set_viewport_size({"width": w, "height": h})
            studio.open(view)
            studio.settle()
            page.wait_for_timeout(250)
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), (view, w)
            worst[(view, w)] = round(_bare(page, theme) * 100, 1)
    assert max(worst.values()) < 5.0, worst


def _leads(page):
    return page.evaluate(
        """() => [...document.querySelectorAll('#patchbay .pb-cables g.cable')].map(g => ({
             slot: g.dataset.slot, src: g.dataset.src, neg: g.classList.contains('neg'),
             text: g.querySelector('text')?.textContent || '', d: g.querySelector('path').getAttribute('d') }))"""
    )


def _jack(page, sel):
    box = page.locator(f"#patchbay {sel} .jack").bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_the_patch_bay_draws_the_routes_and_a_drag_patches_one(studio, server):
    page = studio.page
    page.set_viewport_size({"width": 1920, "height": 1080})
    studio.open("editor")
    studio.settle()
    assert _leads(page) == []  # the default sound routes nothing
    # the form's own controls: the bay is a view of them
    page.select_option('[name="mod1_dst"]', "resonance")
    page.select_option('[name="mod1_src"]', "lfo")
    _wait(page, lambda: len(_leads(page)) == 1, "a lead for route 1")
    page.evaluate(
        "() => window.synthlab.tweak(document.querySelector('[name=mod1_amt]'), -0.4, true)"
    )
    lead = _wait(
        page,
        lambda: [x for x in _leads(page) if x["neg"]],
        "the lead goes dashed for a negative amount",
    )[0]
    assert (lead["slot"], lead["src"], lead["text"]) == ("mod1", "lfo", "-40%")
    _wait(
        page,
        lambda: (
            server.state.patch.mod1_src == "lfo"
            and server.state.patch.mod1_dst == "resonance"
        ),
        "the post",
    )

    # the lead runs from the source's jack to the target's jack
    # (read again until it does: the post's redraw can move the jacks a few
    # px after the lead above was read, and the bay redraws on the next frame)
    def on_the_jack():
        box = page.locator("#patchbay").bounding_box()
        sx, sy = _jack(page, '[data-src="lfo"]')
        now = [x for x in _leads(page) if x["neg"]]
        start = now[0]["d"].split("C")[0][1:].split() if now else None
        return (
            start
            and abs(float(start[0]) - (sx - box["x"])) < 2
            and abs(float(start[1]) - (sy - box["y"])) < 2
        )

    _wait(page, on_the_jack, "the lead starts on the source's jack")
    assert page.get_attribute('#patchbay [data-dst="resonance"]', "data-on") == "mod1"
    # a drag from a source jack to a target jack takes the first unused slot
    tx, ty = _jack(page, '[data-dst="drive"]')
    ax, ay = _jack(page, '[data-src="velocity"]')
    page.mouse.move(ax, ay)
    page.mouse.down()
    page.mouse.move((ax + tx) / 2, (ay + ty) / 2, steps=4)
    assert page.locator("#patchbay .cable.drag").count() == 1
    page.mouse.move(tx, ty, steps=4)
    page.mouse.up()
    _wait(
        page,
        lambda: server.state.patch.mod2_src == "velocity",
        "the dragged route reaches the server",
    )
    p = server.state.patch
    assert (p.mod2_dst, p.mod2_amt) == ("drive", 0.5)
    assert page.input_value('[name="mod2_src"]') == "velocity"
    assert {x["slot"] for x in _leads(page)} == {"mod1", "mod2"}
    # the row's own button unplugs it; undo (a swap) redraws from the server's fields
    page.click('.route[data-slot="mod2"] [data-route="off"]')
    _wait(
        page, lambda: [x["slot"] for x in _leads(page)] == ["mod1"], "the lead is gone"
    )
    _wait(
        page,
        lambda: server.state.patch.mod2_src == "none",
        "the unplug reaches the server",
    )
    studio.settle()
    page.wait_for_timeout(
        1600
    )  # (audio.js leaves a control alone for 1.5 s after its last edit)
    with page.expect_response("**/fragment/undo"):
        page.keyboard.press("Control+z")
    studio.settle()
    p = server.state.patch
    live = {
        s for s in ("mod1", "mod2", "mod3", "mod4") if getattr(p, f"{s}_src") != "none"
    }
    assert live != {
        "mod1"
    }  # the undo moved a route (one step or two: the ring's business)
    _wait(
        page,
        lambda: {x["slot"] for x in _leads(page)} == live,
        "the leads are the server's routes again",
    )
    studio.settle()


def test_a_read_out_takes_a_typed_value(studio, server):
    page = studio.page
    page.set_viewport_size({"width": 1920, "height": 1080})
    studio.open("editor")
    studio.settle()
    # a linear slider: click the read-out, type, Enter
    page.click('output[for="p-resonance"]')
    box = page.locator('output[for="p-resonance"] input.val-edit')
    box.fill("0.61")
    box.press("Enter")
    _wait(
        page,
        lambda: abs(server.state.patch.resonance - 0.61) < 1e-6,
        "the typed resonance",
    )
    assert page.inner_text('output[for="p-resonance"]') == "0.61"
    assert page.evaluate("document.activeElement.id") == "p-resonance"
    # a log slider posts exactly what was typed, not the snapped position
    page.focus("#p-cutoff")
    page.keyboard.press("Enter")
    box = page.locator('output[for="p-cutoff"] input.val-edit')
    box.fill("1234")
    box.press("Enter")
    _wait(page, lambda: server.state.patch.cutoff == 1234.0, "the typed cutoff")
    assert page.inner_text('output[for="p-cutoff"]') == "1234 Hz"
    # Esc leaves it as it was; out of range is clamped
    page.click('output[for="p-resonance"]')
    page.locator("input.val-edit").fill("0.2")
    page.keyboard.press("Escape")
    assert page.inner_text('output[for="p-resonance"]') == "0.61"
    page.click('output[for="p-osc1_level"]')  # a knob's read-out
    page.locator("input.val-edit").fill("7")
    page.keyboard.press("Enter")
    hi = float(page.get_attribute("#p-osc1_level", "max"))
    _wait(page, lambda: server.state.patch.osc1_level == hi, "the clamp")
    studio.settle()


def test_a_right_click_lists_the_rows_own_buttons(studio, server):
    page = studio.page
    page.set_viewport_size({"width": 1920, "height": 1080})
    studio.open("editor")
    with page.expect_response("**/fragment/layer/add"):
        page.click("#ls-add")
    studio.settle()
    page.click("#lrow-1", button="right", position={"x": 300, "y": 10})
    page.wait_for_selector("#ctx-menu:popover-open")
    items = page.locator("#ctx-menu .menu-item").all_inner_texts()
    assert page.inner_text("#ctx-menu .ctx-title").lower().startswith("layer 2")
    assert items[:5] == [
        "Edit in the panels",
        "Mute",
        "Solo",
        "Follow the main lane",
        "Clear the layer",
    ]
    # then the layer strip's own (menu.js: a section's menu)
    assert items[5].startswith("Put these on the deck")
    # the edited row can be cleared too (the panels move to another sound)
    assert page.is_enabled("#ctx-menu .menu-item:has-text('Clear the layer')")
    with page.expect_response("**/fragment/layer/mute"):
        page.locator("#ctx-menu .menu-item", has_text="Mute").click()
    _wait(
        page,
        lambda: server.state.layers[1].get("muted"),
        "the menu clicked the row's own mute",
    )
    assert page.locator("#ctx-menu:popover-open").count() == 0
    # an FX unit: its move and remove
    page.select_option('select.fx-adder[aria-label="add effect to Master FX"]', "delay")
    page.wait_for_selector('.fx-unit button[aria-label="remove delay"]')
    page.locator(".fx-unit header strong", has_text="Delay").click(button="right")
    with page.expect_response("**/fragment/fx/remove"):
        page.locator("#ctx-menu .menu-item", has_text="Remove from the chain").click()
    page.wait_for_selector(
        '.fx-unit button[aria-label="remove delay"]', state="detached"
    )
    studio.settle()


def test_the_rack_is_open_and_one_action_is_primary(studio):
    page = studio.page
    page.set_viewport_size({"width": 1920, "height": 1080})
    studio.open("editor")
    studio.settle()
    # macros and the XY pad are reachable without opening a disclosure
    page.locator("#xypad").scroll_into_view_if_needed()
    assert page.locator("#xypad").is_visible() and page.locator("#macro-0").is_visible()
    assert not page.evaluate("document.getElementById('perform-tools').open")
    # the Bass check loads when it comes into view
    page.wait_for_selector(".bass-check .meter-card")
    # (a dialog's submit is its own primary, seen only while it is open)
    for view, n in (("editor", 1), ("voice", 1), ("bass", 0)):
        studio.open(view)
        primary = page.locator("#view .btn-primary:not(.dialog .btn-primary)")
        assert primary.count() == n, view
    studio.settle()


def test_the_take_plays_in_the_themed_player(studio, server):
    page = studio.page
    studio.open("voice")
    assert (
        page.locator("#mic-btn").bounding_box()["width"] < 200
    )  # a button, not a banner
    page.set_input_files(
        "#voice-file",
        {"name": "say.wav", "mimeType": "audio/wav", "buffer": wav_bytes(1.0)},
    )
    page.wait_for_selector('#voice-panel .voice-take[data-has-take="true"]')
    assert page.locator("audio[controls]").count() == 0
    page.click(".player-play")
    page.wait_for_function(
        "() => document.querySelector('.player-play').getAttribute('aria-pressed') === 'true'"
    )
    page.wait_for_function(
        "() => parseFloat(getComputedStyle(document.querySelector('.player-bar')).getPropertyValue('--at')) > 0"
    )
    page.click(".player-play")
    page.wait_for_function("() => document.querySelector('.player audio').paused")
    studio.settle()


def test_the_agent_drawer_docks_beside_the_page(studio):
    page = studio.page
    page.set_viewport_size({"width": 1920, "height": 1080})
    studio.open("editor")
    page.click(".sb-agent")
    page.wait_for_selector("#agent-drawer:popover-open")
    wide = page.evaluate(
        "document.getElementById('view').getBoundingClientRect().right"
    )
    page.click("#agent-pin")
    page.wait_for_function("() => document.documentElement.dataset.agent === 'pinned'")
    drawer = page.locator("#agent-drawer").bounding_box()
    assert drawer["x"] + drawer["width"] >= 1919 and drawer["height"] >= 1070
    assert (
        page.evaluate("document.getElementById('view').getBoundingClientRect().right")
        <= drawer["x"] + 1
        < wide
    )
    page.reload()  # remembered per browser
    page.wait_for_function("() => document.documentElement.dataset.agent === 'pinned'")
    assert page.locator("#agent-drawer").is_visible()
    page.click("#agent-pin")
    page.wait_for_function("() => !('agent' in document.documentElement.dataset)")
    studio.settle()
