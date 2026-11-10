"""The page transition (pagemotion.js, style.css 45; frontend.md, "The page
transition"): a move to another page is htmx's view transition (the old
page leaving) and a GSAP cascade (the new one arriving), the docks still.
Driven with the real pointer and keyboard: the motion runs and lands with
nothing left behind, a second move mid-sequence lands on the last page, a
held key sounds through it, a click goes through it, and calm motion keeps
a plain swap. What it looks like is not judged here: that needs eyes on
the frames."""

import pytest

pytestmark = pytest.mark.browser

# whether a transition or an arrival was seen since `_WATCH`
_WATCH = """() => {
  window.__pm = { vt: false, arriving: false };
  const root = document.documentElement;
  new MutationObserver(() => {
    if (root.hasAttribute('data-arriving')) window.__pm.arriving = true;
  }).observe(root, { attributes: true, attributeFilter: ['data-arriving'] });
  document.addEventListener('htmx:before:viewTransition', () => {
    window.__pm.vt = true;
  });
}"""
_IDLE = "() => !window.synthlab.pageMotion.busy()"
# what the motion writes and must take back: a module's inline motion, a
# value's swept property, a transition's name
_LEFT = """() => [...document.querySelectorAll('[style]')].filter((el) =>
  /opacity|translate|scale|filter|--ignite|will-change|view-transition-name/
    .test(el.getAttribute('style'))
  && el.closest('#view, .topdock, .keys-dock'))
  .map((el) => el.dataset.module || el.id || el.className)"""
_HERE = """() => document.querySelector('nav.views a[aria-current="page"]')
  ?.getAttribute('href')"""


def _settled(studio, view: str) -> None:
    page = studio.page
    page.wait_for_selector(f'#view[data-view="{view}"]')
    page.wait_for_function(_IDLE, timeout=4000)
    assert page.evaluate(_LEFT) == []
    assert page.evaluate(_HERE) == f"/view/{view}"


def test_a_page_change_leaves_and_arrives_and_lands_clean(studio):
    page = studio.open("editor")
    page.evaluate(_WATCH)
    studio.nav("drums")
    _settled(studio, "drums")
    assert page.evaluate("() => window.__pm") == {"vt": True, "arriving": True}
    assert page.title() == "Kit · synthlab studio"
    # the cascade's band of light is the page's, under both docks
    assert (
        page.evaluate(
            "() => getComputedStyle(document.querySelector('.pm-sweep')).opacity"
        )
        == "0"
    )
    # back up the rail: the same, the other way
    page.evaluate(_WATCH)
    page.go_back()
    _settled(studio, "editor")
    assert page.evaluate("() => window.__pm.vt")
    assert studio.unexplained() == []


def test_a_second_move_mid_sequence_lands_on_the_last_page(studio):
    page = studio.open("editor")
    page.click('nav.views a[href="/view/drums"]')
    page.wait_for_function(
        "() => document.documentElement.hasAttribute('data-arriving')"
    )
    page.click('nav.views a[href="/view/perform"]')
    _settled(studio, "perform")
    assert page.evaluate("() => document.querySelectorAll('#view').length") == 1
    assert studio.unexplained() == []


def test_a_held_key_sounds_and_stays_lit_through_a_page_change(studio):
    page = studio.open("editor")
    studio.start_voice()
    page.keyboard.down("a")
    studio.wait_sound()
    page.click('nav.views a[href="/view/drums"]')
    levels = []
    for _ in range(10):  # ~0.5 s: the whole transition
        levels.append(studio.rms())
        page.wait_for_timeout(50)
    assert min(levels) > 1e-3, levels
    _settled(studio, "drums")
    assert (
        page.evaluate("() => document.querySelectorAll('.keys-dock .pkey.down').length")
        >= 1
    )
    page.keyboard.up("a")
    studio.wait_silence()


def test_a_click_goes_through_the_transition(studio):
    page = studio.open("drums")
    was = page.get_attribute("#hold-toggle", "aria-pressed")
    page.click('nav.views a[href="/view/editor"]')
    page.wait_for_function("() => !!document.activeViewTransition", timeout=3000)
    page.click("#hold-toggle")  # the keyboard dock, while the page changes
    assert page.get_attribute("#hold-toggle", "aria-pressed") != was
    _settled(studio, "editor")
    page.click("#hold-toggle")
    assert page.get_attribute("#hold-toggle", "aria-pressed") == was


def test_calm_motion_keeps_a_plain_swap(studio):
    page = studio.open("editor")
    page.evaluate("() => localStorage.setItem('synthlab.motion', 'calm')")
    page = studio.open("editor")
    assert page.evaluate("() => window.synthlab.calm()") is True
    page.evaluate(_WATCH)
    studio.nav("drums")
    page.wait_for_timeout(300)
    assert page.evaluate("() => window.__pm") == {"vt": False, "arriving": False}
    assert page.evaluate(_LEFT) == []
    page.evaluate("() => localStorage.removeItem('synthlab.motion')")
