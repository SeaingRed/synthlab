"""Journeys 1 and 9: every view loads cold and through the nav, clean.

Proves: each view's HTML, scripts and styles load under the shipped CSP
with no console error, uncaught exception or 5xx (the `studio` fixture
fails the test on any), and the nav's htmx swap lands the view it names
and pushes its URL. Does not prove anything looks right.
"""

from __future__ import annotations

import pytest

from synthlab.studio.views import VIEW_NAMES, view_title

# the nav's order (base.html); "bass" has no nav link — it is reached from
# Synths' Bass check — and is loaded cold below
NAV = (  # (the walk's order, not the rail's)
    "library",
    "breed",
    "drums",
    "song",
    "mixer",
    "perform",
    "voice",
    "lab",
    "clips",  # the clip hub ("Clips" in the rail)
    "visuals",  # the dock's scope / spectrum variations
    "map",
    "editor",
)


@pytest.mark.parametrize("view", VIEW_NAMES)
def test_view_loads_cold_without_errors(studio, view):
    page = studio.open(view)
    assert page.title() == view_title(view)  # the page's name first
    # the page is the real app, not an error body: the dock and nav are there
    assert page.locator("nav.views a.view").count() == len(NAV)
    assert page.locator("#scope").count() == 1
    # every island ran to the end (each publishes onto window.synthlab)
    for name in ("ensureCtx", "playKey", "live", "piano", "keysBlocked"):
        assert page.evaluate(f"typeof window.synthlab.{name}") != "undefined", name
    # let the view's own startup requests land (patch.json, previews, plan)
    page.wait_for_load_state("networkidle")


def test_nav_switches_through_every_view(studio):
    page = studio.open("editor")
    for view in NAV:
        studio.nav(view)
        assert page.url.endswith(f"/view/{view}")
        active = page.locator('nav.views a[aria-current="page"]')
        assert active.count() == 1
        assert active.get_attribute("href") == f"/view/{view}"
    page.wait_for_load_state("networkidle")


def test_root_opens_the_studio_and_serves_its_boundary_headers(studio):
    """Journey 9: opened normally (loopback, no token) the page just loads,
    and the response carries the policy the page then runs under."""
    resp = studio.page.goto(studio.url + "/")
    assert resp.status == 200
    csp = resp.headers["content-security-policy"]
    assert "script-src 'self' 'wasm-unsafe-eval'" in csp
    assert "unsafe-eval'" not in csp.replace("'wasm-unsafe-eval'", "")
    assert resp.headers["x-frame-options"]
    studio.page.wait_for_selector("#view")
    studio.page.wait_for_load_state("networkidle")
    # a same-origin POST from the page passes the Origin check
    status = studio.page.evaluate(
        "fetch('/theme', {method: 'POST'}).then((r) => r.status)"
    )
    assert status < 400


def test_the_bass_page_plays_at_the_root_it_shows_pressed(studio):
    """The Bass check page has the bench's ▶ (Space) and shows the quick
    key in force pressed; its sub spectrum is THE signal graphic."""
    page = studio.open("bass")
    studio.settle()
    assert page.inner_text("#view h1") == "Bass check"
    play = '#view #play[data-keys="Space"][data-player="loop"]'
    assert page.locator(play).count() == 1
    with page.expect_response("**/fragment/bass/key"):
        page.click(".quickkeys button:has-text('E1')")
    pressed = page.locator('.quickkeys button[aria-pressed="true"]')
    assert pressed.all_inner_texts() == ["E1"]
    assert page.inner_text('[data-cue-for="play"]').startswith("the phrase at E1")
    page.wait_for_selector(".sub-plot svg.sig path")
