"""Journey: the themes (studio docs/frontend.md, "Themes") and the
transport's pilot lamps.

Proves, in a real Chromium under the shipped CSP: a theme's swatch in the
Colours popover applies it — <html> carries `data-look`, the page's colours
move, the sliders show the theme's own hues, a slider moved from them says
"custom" and Reset goes back to the theme's own; the choice is kept per
browser and is on <html> again on a reload before the body is parsed (so
nothing paints in the default's colours first); the palette's "Theme: …"
command does the same; both decks read their colours again; and a
transport key's pilot lamp is dark glass while stopped and lit while its
player plays.

Does not prove any theme looks good: the contrast of every theme's pairs is
computed in tests/studio/test_design_system.py, the look is a screenshot a
person reads.
"""

from __future__ import annotations

# records <html>'s data-look the moment <body> is inserted: colours.js runs
# in <head>, so a stored theme must already be there
_AT_BODY = """
new MutationObserver((ms, ob) => {
  if (!document.body) return;
  window.__lookAtBody = document.documentElement.dataset.look ?? null;
  ob.disconnect();
}).observe(document, { childList: true, subtree: true });
"""


def _accent(page) -> str:
    return page.evaluate(
        "() => getComputedStyle(document.documentElement).getPropertyValue('--accent')"
    )


def _open_colours(page) -> None:
    page.click("#colours-btn")
    page.wait_for_selector("#colours-pop:popover-open")


def test_a_swatch_applies_a_theme_kept_before_the_first_paint(studio):
    page = studio.open("editor")
    page.context.add_init_script(_AT_BODY)
    before = _accent(page)
    inks = page.evaluate("() => window.synthlab.deckArt.inks()")
    _open_colours(page)
    assert page.inner_text("#look-now") == "Studio Teal"
    assert page.get_attribute('.look-sw[data-look="studio"]', "aria-pressed") == "true"
    page.click('.look-sw[data-look="warm-tube"]')
    assert page.get_attribute("html", "data-look") == "warm-tube"
    assert _accent(page) != before
    assert page.inner_text("#look-now") == "Warm Tube"
    warm = '.look-sw[data-look="warm-tube"]'
    assert page.get_attribute(warm, "aria-pressed") == "true"
    # the sliders show the theme's own values (Warm Tube's go is 50, orange)
    assert page.input_value('#colours-pop input[data-hue="go"]') == "50"
    assert page.input_value('#colours-pop input[data-depth="black"]') == "25"
    # the decks read the page's colours again
    assert page.evaluate("() => window.synthlab.deckArt.inks()") != inks
    stored = page.evaluate("() => JSON.parse(localStorage.getItem('synthlab.colours'))")
    assert stored == {"theme": "warm-tube"}
    # a dial moved from it: "custom"; Reset goes back to the theme's own
    page.eval_on_selector(
        '#colours-pop input[data-hue="accent"]',
        "el => { el.value = '120';"
        " el.dispatchEvent(new Event('input', {bubbles: true})); }",
    )
    assert page.inner_text("#look-now") == "Warm Tube · custom"
    page.click("#colours-reset")
    assert page.inner_text("#look-now") == "Warm Tube"
    assert page.get_attribute("html", "data-look") == "warm-tube"
    assert page.input_value('#colours-pop input[data-hue="accent"]') == "78"
    # kept per browser, and on <html> before the body exists
    page.reload()
    page.wait_for_selector('#view[data-view="editor"]')
    assert page.evaluate("() => window.__lookAtBody") == "warm-tube"
    # Studio Teal is the default: no attribute at all
    _open_colours(page)
    page.click('.look-sw[data-look="studio"]')
    assert page.get_attribute("html", "data-look") is None
    assert _accent(page) == before
    assert page.evaluate("() => localStorage.getItem('synthlab.colours')") is None


def test_the_palette_command_picks_a_theme(studio):
    page = studio.open("editor")
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette[open]")
    page.keyboard.type("theme phosphor")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.documentElement.dataset.look === 'phosphor'")


def test_a_transport_keys_pilot_lamp_lights_while_it_plays(studio):
    page = studio.open("perform")
    lamp = """() => {
      const s = getComputedStyle(document.querySelector('#view #play'), '::before');
      return { bg: s.backgroundImage, glow: s.boxShadow, w: s.width }; }"""
    off = page.evaluate(lamp)
    # dark glass, not absent
    assert off["w"] == "6px" and "radial-gradient" in off["bg"]
    assert "inset" in off["glow"]
    page.click("#view #play")
    page.wait_for_selector('#view #play[aria-pressed="true"]')
    on = page.evaluate(lamp)
    assert on["bg"] != off["bg"] and "inset" not in on["glow"]  # lit, blooming
    page.click("#view #play")
    page.wait_for_selector('#view #play[aria-pressed="false"]')
    assert page.evaluate(lamp)["bg"] == off["bg"]
