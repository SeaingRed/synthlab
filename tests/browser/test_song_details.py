"""UI fixes held in a real browser: what a page
says it plays, the Song page on a laptop window with a cell open, the
ledger and the marker, the Library's rows by keyboard and its empty
filter, and the sound agent drawer's Esc.

What none of this proves: that any of it reads well (a screenshot was
looked at, once)."""

from __future__ import annotations

from tests.browser.conftest import _INIT, Studio
from tests.browser.test_song_playlist import playlist_song, use


def _strip(page) -> tuple[str, str]:
    return page.evaluate(
        """() => [document.querySelector('#status-bar .st-tempo').textContent.trim(),
                  document.querySelector('#status-bar .st-key').textContent.trim()]"""
    )


def test_the_status_strip_says_what_the_page_plays(studio, server):
    """The song's tempo and key on Song and Mix, the loop's elsewhere — and
    a nav swap carries the strip (it said the loop's on Song: two tempos on
    one screen)."""
    use(server, playlist_song(bpm=97))
    loop = server.state.arrangement
    assert loop.bpm != 97
    page = studio.open("library")
    assert _strip(page)[0].replace("\n", " ") == f"{loop.bpm:g} bpm"
    assert _strip(page)[1] == f"{loop.root[:-1]} {loop.scale_name.replace('_', ' ')}"
    studio.nav("song")
    page.wait_for_function(
        "() => document.querySelector('#status-bar .st-tempo b').textContent === '97'"
    )
    assert _strip(page)[1] == "A minor"  # a key, never a register ("A1")
    assert "song" in page.get_attribute("#status-bar .st-tempo", "title")
    studio.nav("mixer")
    assert _strip(page)[0].startswith("97")
    studio.nav("library")
    tempo = "() => document.querySelector('#status-bar .st-tempo b').textContent"
    page.wait_for_function(f"{tempo} === '{loop.bpm:g}'")


def test_song_on_a_laptop_with_a_cell_open(chromium, server):
    """1366 x 768, dense, a cell open: the playlist's tool bar is one line,
    the editor's seam has its grip, and the drawer's "to" field shows its
    number whole."""
    use(server)
    ctx = chromium.new_context(viewport={"width": 1366, "height": 768})
    ctx.add_init_script(_INIT)
    ctx.set_default_timeout(10_000)
    st = Studio(ctx.new_page(), server)
    try:
        page = st.open("song")
        page.locator(".blk").first.click()
        page.wait_for_selector("#song-drawer:not([hidden]) .drawer-span")
        box = page.locator("#arr-tools").bounding_box()
        assert box["height"] <= 30, box  # one line (two were 46 px)
        # the words are still the buttons' names
        name = page.evaluate(
            "() => document.querySelector('#arr-select').textContent.trim()"
        )
        assert name == "▭ Select"
        grip = page.evaluate(
            """() => { const s = document.querySelector('.drawer-splitter-y');
                 const g = getComputedStyle(s, '::before');
                 return [getComputedStyle(s).display, g.content, g.width]; }"""
        )
        assert grip[0] != "none" and grip[1] != "none" and grip[2] == "48px", grip
        to = page.locator('.drawer-span [name="to"]')
        fits = to.evaluate("(el) => el.scrollWidth <= el.clientWidth")
        assert fits
    finally:
        problems = st.unexplained()
        ctx.close()
    assert not problems, problems


def test_the_ledger_follows_the_marker(studio, server):
    use(server)
    page = studio.open("song")
    page.focus("#song-ruler")
    for _ in range(9):
        page.keyboard.press("ArrowRight")
    # bar 10 of 21: Verse's second pass (Intro 2 + Verse 4 x 2)
    at = "() => document.querySelector('#song-position').textContent"
    page.wait_for_function(f"{at}.startsWith('bar 10 / 21')")
    assert "Verse" in page.inner_text("#song-position")


def test_the_library_by_arrow_and_its_empty_filter(studio, server):
    page = studio.open("library")
    # a grid of cards (library.js): ← / →
    # the previous / next card, ↓ the one under it
    cards = page.locator(".shelf-recipes .lib-card")
    first = cards.first.locator(".lib-play")
    first.focus()
    page.keyboard.press("ArrowRight")
    name = "() => document.activeElement.closest('.lib-card').dataset.name"
    assert page.evaluate(name) == cards.nth(1).get_attribute("data-name")
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowDown")
    top = "() => document.activeElement.getBoundingClientRect().top"
    assert page.evaluate(top) > cards.first.bounding_box()["y"] + 10
    shelf = "() => document.activeElement.closest('.shelf').dataset.module"
    assert page.evaluate(shelf) == "library.recipes"
    page.fill("#lib-search", "no-such-sound-at-all")
    every = page.locator(".lib-card").count()
    page.wait_for_function(
        f"() => document.querySelector('#lib-count').textContent === '0 of {every}'"
    )
    page.click("[data-lib-clear]")
    assert page.input_value("#lib-search") == ""
    assert page.locator(".lib-card:not([hidden])").count() == every


def test_the_agent_drawer_closes_on_escape(studio, server):
    page = studio.open("editor")
    opener = page.locator('[popovertarget="agent-drawer"]:not(.ad-close)').first
    opener.click()
    page.wait_for_selector("#agent-drawer:popover-open")
    inside = page.evaluate(
        "() => document.querySelector('#agent-drawer').contains(document.activeElement)"
    )
    assert inside  # its field, or its ✕ when there is no key
    page.keyboard.press("Escape")
    page.wait_for_selector("#agent-drawer:not(:popover-open)", state="attached")
    assert page.evaluate(
        "() => document.activeElement?.getAttribute('popovertarget') === 'agent-drawer'"
    )
