"""Journey: the page's life (studio docs/frontend.md, "The light model" and
"The energy").

Proves, in a real Chromium under the shipped CSP: the energy is published
once — `--energy` on <body> moves while the loop plays, `html[data-alive]`
is set, and a running step grid's module lights its edge (a ::after whose
opacity follows the level) — and settles to rest after ■: `--energy` back
to exactly 0, `data-alive` gone, the edge dark; a moved knob leaves an
afterglow that clears itself; under calm motion (the Motion setting,
colours.js) none of it is written (the energy stays 0, no afterglow) while
the static depth stays; the OS's reduced motion is not followed (with
Chromium emulating `reduce`, the default still animates the entrance,
writes the energy and pulses the now-playing LED; calm, switched on in the
Colours popover mid-play, stops all three at once); and every view opens
with no console error, CSP violation or 5xx (the fixture's own check).

Does not prove any of it looks good: that is a screenshot a person reads.
"""

from __future__ import annotations

import pytest

from synthlab.studio.views import VIEW_NAMES

_READ = "getComputedStyle(document.body).getPropertyValue('--energy')"
_ENERGY = f"() => {_READ}.trim()"
_ALIVE = "document.documentElement.hasAttribute('data-alive')"
_EDGE = (
    "() => Number(getComputedStyle("
    "document.getElementById('perform-drums'), '::after').opacity)"
)
_KNOB = "#patch input[type=range].knob"
_TWEAK = (
    "el => window.synthlab.tweak(el, Number(el.value) + Number(el.step || 1), true)"
)
_GLOWS = "el => el.classList.contains('afterglow')"
# a view entrance seen: the page transition (pagemotion.js, not
# motion.js's Web Animations on #view's sections) — htmx's view
# transition begun. `studio.nav` waits for the motion to land, so it is
# watched from before the move (_WATCH) and read after (_ENTERED)
_WATCH = """() => { window.__entered = false;
  document.addEventListener('htmx:before:viewTransition',
    () => { window.__entered = true; }, { once: true }); }"""
_ENTERED = "() => window.__entered === true"
# the now-playing LED's beat: the most --np-pulse reached over ~1.2 s
_PULSE_MAX = """() => new Promise((ok) => {
  const box = document.getElementById('now-playing'), t0 = performance.now();
  let most = 0;
  const f = () => {
    most = Math.max(most, Number(box.style.getPropertyValue('--np-pulse')) || 0);
    if (performance.now() - t0 < 1200) requestAnimationFrame(f); else ok(most);
  };
  requestAnimationFrame(f);
})"""
_CALM = "localStorage.setItem('synthlab.motion', 'calm')"


def _motion(page, mode: str) -> None:
    """Motion: full | calm, the way a user sets it: the Colours popover."""
    page.click("#colours-btn")
    page.wait_for_selector("#colours-pop:popover-open")
    page.click(f'#colours-pop [data-set-motion="{mode}"]')
    page.keyboard.press("Escape")


def test_the_energy_moves_while_the_loop_plays_and_settles_to_rest(studio):
    page = studio.open("perform")
    assert page.evaluate(_EDGE) == 0
    page.click("#view #play")
    page.wait_for_selector('#view #play[aria-pressed="true"]')
    page.wait_for_function(f"() => {_ALIVE}", timeout=15000)
    page.wait_for_function(f"() => Number({_READ}) > 0.05", timeout=15000)
    assert page.evaluate("() => window.synthlab.energy()") > 0
    # the grids run, and their modules' edges are lit
    page.wait_for_function(
        "() => document.querySelector('#perform-drums .grid[data-running]')"
    )
    assert page.evaluate(_EDGE) > 0.3
    page.click("#view #play")
    page.wait_for_selector('#view #play[aria-pressed="false"]')
    page.wait_for_function(f"() => !{_ALIVE} && Number({_READ}) === 0", timeout=10000)
    assert page.evaluate("() => window.synthlab.energy()") == 0
    assert not page.query_selector(".grid[data-running]")
    page.wait_for_function(f"() => ({_EDGE})() === 0")


def test_a_moved_knob_leaves_an_afterglow_that_clears(studio):
    page = studio.open("editor")
    page.eval_on_selector(_KNOB, _TWEAK)
    assert page.eval_on_selector(_KNOB, _GLOWS)
    filt = page.eval_on_selector(_KNOB, "el => getComputedStyle(el).filter")
    assert "drop-shadow" in filt
    page.wait_for_function(
        f"() => !document.querySelector('{_KNOB}').classList.contains('afterglow')",
        timeout=3000,
    )


def test_calm_motion_writes_no_energy_and_no_afterglow(studio):
    studio.page.context.add_init_script(_CALM)
    page = studio.open("perform")
    assert page.evaluate("() => document.documentElement.dataset.motion") == "calm"
    page.click("#view #play")
    page.wait_for_selector('#view #play[aria-pressed="true"]')
    page.wait_for_function("() => window.synthlab.energy() > 0.05", timeout=15000)
    page.wait_for_timeout(400)
    assert page.evaluate(_ENERGY) in ("", "0")
    assert not page.evaluate(f"() => {_ALIVE}")
    page.click("#view #play")
    studio.nav("editor")
    page.eval_on_selector(_KNOB, _TWEAK)
    assert not page.eval_on_selector(_KNOB, _GLOWS)


def test_the_os_reduced_motion_is_not_followed_and_calm_is_on_request(studio):
    """Deliberately: with the system asking for reduced
    motion and the setting at its default (full), a view switch animates
    its entrance, the energy is written while the loop plays and the
    now-playing LED pulses on the beat; Motion: calm, from the Colours
    popover mid-play, takes the light down at once and stops the pulse, and
    a view switch then enters still. The choice survives a reload."""
    page = studio.page
    page.emulate_media(reduced_motion="reduce")
    studio.open("editor")
    assert page.evaluate("() => matchMedia('(prefers-reduced-motion: reduce)').matches")
    assert page.evaluate("() => window.synthlab.calm()") is False
    assert (
        page.get_attribute("#colours-pop [data-set-motion=full]", "aria-pressed")
        == "true"
    )
    page.evaluate(_WATCH)
    studio.nav("perform")
    assert page.evaluate(_ENTERED)
    page.click("#view #play")
    page.wait_for_selector('#view #play[aria-pressed="true"]')
    page.wait_for_function(f"() => {_ALIVE}", timeout=15000)
    page.wait_for_function(f"() => Number({_READ}) > 0.05", timeout=15000)
    assert page.evaluate(_PULSE_MAX) > 0.3
    # calm, mid-play: the light goes at once, the LED holds still
    _motion(page, "calm")
    assert page.evaluate("() => document.documentElement.dataset.motion") == "calm"
    page.wait_for_function(f"() => !{_ALIVE} && Number({_READ}) === 0", timeout=3000)
    assert page.evaluate("() => window.synthlab.energy()") > 0  # (still measured)
    assert page.evaluate(_PULSE_MAX) == 0
    page.click("#view #play")
    page.wait_for_selector('#view #play[aria-pressed="false"]')
    page.evaluate(_WATCH)
    studio.nav("editor")
    page.wait_for_timeout(200)
    assert not page.evaluate(_ENTERED)
    # kept per browser: still calm after a reload, and full again on request
    page.reload()
    page.wait_for_selector('#view[data-view="editor"]')
    assert page.evaluate("() => window.synthlab.calm()") is True
    assert (
        page.get_attribute("#colours-pop [data-set-motion=calm]", "aria-pressed")
        == "true"
    )
    _motion(page, "full")
    assert page.evaluate("() => localStorage.getItem('synthlab.motion')") is None
    page.evaluate(_WATCH)
    studio.nav("perform")
    assert page.evaluate(_ENTERED)


@pytest.mark.parametrize("view", VIEW_NAMES)
def test_every_view_opens_clean_with_the_light_model(studio, view):
    page = studio.open(view)
    # the light model's tokens resolve on the page (a stale registration once
    # made --glow a percentage, and every glow drew nothing)
    glow = page.evaluate(
        "() => getComputedStyle(document.documentElement).getPropertyValue('--glow')"
    )
    assert glow.strip().startswith(("oklch", "color", "rgb", "lab")), glow
