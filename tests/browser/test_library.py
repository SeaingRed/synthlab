"""Journey: the Library page (studio docs/sounds-and-breeding.md, "The
page").

Proves, in a real Chromium under the shipped CSP, in both densities (the
dock once existed only in dense.css, so the comfy density had none): no
key of any card is clipped at 1366 and 1920 px (each
button's scrollWidth against its clientWidth), nor a card's name or its
centre's read-out; the selected card's detail fills the dock at the right
and stays in the window; a card is the audition — it plays, lights its
lamp while it sounds and becomes the selection — and + and Use still post;
the filter's chips say how many each would show, a family it empties is one
"no match" line, nothing matching offers Clear filter; the arrow keys walk
the grid; cards or rows is kept per browser; and the page's text holds AA
contrast on what it is drawn on, in every theme, night and day.

Does not prove any of it looks good: the screenshots are for a person.
"""

from __future__ import annotations

import pytest

from tests.browser.conftest import _INIT, Studio

THEMES = (
    "studio",
    "warm-tube",
    "analog-moog",
    "neon-night",
    "mono-lab",
    "vintage-console",
    "phosphor",
)


@pytest.fixture
def library(chromium, server):
    """Open /view/library at a size, density, theme and look; the page fails
    the test on any console error, uncaught exception, CSP violation or 5xx."""
    made = []

    def open_(w=1920, h=1080, density="compact", theme="night", look="studio"):
        ctx = chromium.new_context(viewport={"width": w, "height": h})
        ctx.add_init_script(_INIT)
        ctx.set_default_timeout(10_000)
        ctx.add_cookies(
            [
                {"name": "theme", "value": theme, "url": server.url},
                {"name": "density", "value": density, "url": server.url},
            ]
        )
        if look != "studio":
            ctx.add_init_script(
                "localStorage.setItem('synthlab.colours',"
                f" JSON.stringify({{theme: '{look}'}}))"
            )
        st = Studio(ctx.new_page(), server)
        made.append((ctx, st))
        page = st.open("library")
        page.wait_for_timeout(300)  # the view's entry fade
        return page

    yield open_
    problems = []
    for ctx, st in made:
        problems += st.unexplained()
        st.page.goto("about:blank")
        ctx.close()
    assert not problems, "the page reported errors:\n  " + "\n  ".join(problems)


CLIPPED = """() => [...document.querySelectorAll('.library-view'
    + ' :is(.lib-acts > .btn, .lib-acts > .badge, .lib-hz, .tagfilter)')]
  .filter((b) => b.checkVisibility() && b.scrollWidth > b.clientWidth + 0.5)
  .map((b) => (b.closest('.lib-card')?.dataset.name || '')
    + ': ' + b.textContent.trim())"""


@pytest.mark.parametrize(
    "size, density",
    [((1920, 1080), "compact"), ((1366, 768), "compact"), ((1920, 1080), "comfy")],
    ids=["1920-dense", "1366-dense", "1920-comfy"],
)
def test_no_key_is_clipped_and_the_dock_shows_the_selection(library, size, density):
    page = library(*size, density=density)
    # every card's keys, read-out and every filter chip whole
    assert page.evaluate(CLIPPED) == []
    # the cards fill the width: several to a row, none narrower than 190 px
    widths = page.evaluate(
        "() => [...document.querySelectorAll('.shelf-recipes .lib-card')]"
        ".slice(0, 12).map((c) => c.getBoundingClientRect())"
        ".map((r) => [Math.round(r.top), r.width])"
    )
    first_row = [w for top, w in widths if top == widths[0][0]]
    assert len(first_row) >= 4 and min(first_row) >= 190, widths
    # the long names end in an ellipsis inside their card, never over the Hz
    over = page.evaluate(
        """() => [...document.querySelectorAll('.lib-card')].filter((c) => {
             const n = c.querySelector('.lib-name').getBoundingClientRect(),
                   h = c.querySelector('.lib-hz').getBoundingClientRect();
             return n.right > h.left + 0.5; }).length"""
    )
    assert over == 0
    # nothing selected: the dock's resting face
    dock = page.locator(".lib-dock")
    assert dock.is_visible() and page.locator(".lib-dock .empty-state").is_visible()
    card = page.locator(".lib-card").nth(9)
    name = card.get_attribute("data-name")
    with page.expect_response(lambda r: "/audition.wav" in r.url):
        card.locator(".lib-name").click()
    detail = card.locator(".lib-detail")
    page.wait_for_function(
        "() => document.querySelector('.lib-card.active .lib-d-name')"
        "?.checkVisibility() && !document"
        ".querySelector('.lib-card.active > .lib-detail').getAnimations().length"
    )  # (its slide in done)
    assert detail.locator(".lib-d-name").inner_text() == name
    box, frame = detail.bounding_box(), dock.bounding_box()
    assert abs(box["x"] - frame["x"]) < 2 and abs(box["width"] - frame["width"]) < 2
    assert box["x"] + box["width"] <= size[0] and box["y"] + box["height"] <= size[1]
    assert detail.locator(".meter").count() == 4  # bright, low, mid, high
    # the dock's keys say it all, and none is clipped there either
    bar = page.locator(".lib-d-acts")
    keys = [k.inner_text().strip() for k in bar.locator(".btn").all() if k.is_visible()]
    assert keys == ["▶ in your mix", "+ Layer", "Use on Synths"]
    b = bar.bounding_box()
    assert (
        b["x"] >= frame["x"] - 1
        and b["y"] + b["height"] <= frame["y"] + frame["height"] + 1
    )
    # (on a card the same keys read "▶ mix", "+", "Use": the <i> is the rest)
    short = page.locator(".lib-card:not(.active) .lib-acts").first.inner_text()
    assert short.split() == ["▶", "mix", "+", "Use"]
    # ... and the selected card keeps its own (were they only in the dock
    # with its detail, the card just clicked would lose its Use)
    assert card.locator(".lib-acts .lib-use").is_visible()
    assert card.locator(".lib-acts").inner_text().split() == ["▶", "mix", "+", "Use"]
    assert page.evaluate(CLIPPED) == []
    # the page never scrolls: the browser scrolls inside itself
    assert page.evaluate(
        "() => document.documentElement.scrollHeight <= innerHeight"
        " && document.documentElement.scrollWidth <= innerWidth"
    )


def test_a_card_auditions_lights_and_its_keys_still_post(library, server):
    page = library()
    card = page.locator(".shelf:not(.shelf-recipes):not(.shelf-core) .lib-card").nth(4)
    name = card.get_attribute("data-name")
    with page.expect_response(
        lambda r: "/audition.wav" in r.url and "mix=1" not in r.url
    ):
        card.locator(".lib-play").click()
    # the lamp: lit while it sounds, out when it ends
    page.wait_for_function(
        "(n) => document.querySelector(`.lib-card[data-name='${n}']`)"
        "?.classList.contains('playing')",
        arg=name,
    )
    page.wait_for_function(
        "(n) => !document.querySelector(`.lib-card[data-name='${n}']`)"
        "?.classList.contains('playing')",
        arg=name,
        timeout=15_000,
    )
    # ▶ mix on another card: in your mix, and that card is the selection
    other = page.locator(".shelf:not(.shelf-recipes):not(.shelf-core) .lib-card").nth(6)
    with page.expect_response(lambda r: "audition.wav?mix=1" in r.url):
        other.locator(".lib-mix").click()
    assert "active" in other.get_attribute("class")
    assert "active" not in card.get_attribute("class")
    # + (Layer), on a card that is not the selection
    third = page.locator(".shelf:not(.shelf-recipes):not(.shelf-core) .lib-card").nth(8)
    added = third.get_attribute("data-name")
    with page.expect_response("**/fragment/layer/add"):
        third.locator(".lib-layer").click()
    page.wait_for_function("() => !!window.htmx")
    names = [
        lay["patch"].get("name") for lay in server.state.layers if lay.get("patch")
    ]
    assert added in names, names
    # Use, from the dock: lands on Synths with the sound
    page.goto(f"{server.url}/view/library")
    page.wait_for_selector('#view[data-view="library"]')
    pick = page.locator(".shelf:not(.shelf-recipes):not(.shelf-core) .lib-card").nth(5)
    want = pick.get_attribute("data-name")
    pick.locator(".lib-play").click()
    with page.expect_response("**/fragment/library/load"):
        pick.locator(".lib-use").click()
    page.wait_for_selector('#view[data-view="editor"]')
    assert server.state.patch.name == want


def test_the_core_shelf_comes_first_by_category_and_uses_its_own_source(
    library, server
):
    """The core library a shelf a category, before the
    genre recipes; "core" a chip and mallets / orchestral families of the
    filter; a factory sound it lists is heard and Used as the factory sound
    it is, landing on Synths like any shelf card."""
    page = library()
    first = page.locator(".library-view .shelf").first
    assert first.get_attribute("data-module") == "library.core-bass"
    shelves = page.locator(".shelf-core")
    assert shelves.count() == 8
    mallets = page.locator('.tagfilter[data-tag="mallets"]')
    n = int(mallets.get_attribute("data-n"))
    mallets.click()
    shown = page.locator(".lib-card:not([hidden])")
    assert (
        shown.count()
        == n
        == page.locator(".shelf-core .lib-card[data-tags~='mallets']").count()
    )
    mallets.click()
    core = page.locator('.tagfilter[data-tag="core"]')
    core.click()
    assert page.locator(".lib-card:not([hidden])").count() == int(
        core.get_attribute("data-n")
    )
    core.click()
    acid = page.locator('.shelf-core .lib-card[data-name="Acid Alley"]')
    with page.expect_response(lambda r: "/factory/Acid%20Alley/audition.wav" in r.url):
        acid.locator(".lib-play").click()
    with page.expect_response("**/fragment/factory/load"):
        acid.locator(".lib-use").click()
    page.wait_for_selector('#view[data-view="editor"]')
    assert server.state.patch.name == "Acid Alley"


def test_the_filter_counts_collapses_and_empties(library):
    page = library()
    every = page.locator(".lib-card").count()
    trance = page.locator('.tagfilter[data-tag="trance"]')
    n = int(trance.get_attribute("data-n"))
    assert 0 < n < every
    trance.click()
    assert page.locator(".lib-card:not([hidden])").count() == n
    assert page.inner_text("#lib-count") == f"{n} of {every}"
    # the families it empties are one line each, saying so
    empty = page.locator(".shelf.lib-nomatch")
    assert empty.count() >= 1
    assert all(b["height"] < 40 for b in [s.bounding_box() for s in empty.all()])
    # (the Core shelf lists a trance sound too: the Anthem Supersaw)
    here = page.locator(".shelf-recipes .lib-card:not([hidden])").count()
    assert 0 < here < n
    assert page.locator(".shelf-recipes .shelf-title small").text_content() == (
        f"{here} of {page.locator('.shelf-recipes .lib-card').count()}"
    )
    # a chip says what it would show with this one: bass among the trance
    bass = int(page.get_attribute('.tagfilter[data-tag="bass"]', "data-n"))
    assert bass == page.locator('.lib-card:not([hidden])[data-tags~="bass"]').count()
    # nothing matches: Clear filter, which clears the words and the tags
    page.fill("#lib-search", "zzzz")
    assert page.locator(".lib-none").is_visible()
    page.click("[data-lib-clear]")
    assert page.input_value("#lib-search") == ""
    assert page.locator(".lib-card:not([hidden])").count() == every
    assert page.get_attribute('.tagfilter[data-tag=""]', "aria-pressed") == "true"
    assert page.locator(".shelf.lib-nomatch").count() == 0


def test_arrows_walk_the_grid_and_rows_are_kept(library):
    page = library()
    first = page.locator(".shelf-recipes .lib-card .lib-play").first
    first.focus()
    page.keyboard.press("ArrowRight")
    name = "() => document.activeElement.closest('.lib-card').dataset.name"
    cards = page.locator(".shelf-recipes .lib-card")
    assert page.evaluate(name) == cards.nth(1).get_attribute("data-name")
    page.keyboard.press("ArrowDown")  # the card under it, the same column
    at = page.evaluate(
        "() => { const r = document.activeElement.getBoundingClientRect();"
        " return [r.left, r.top]; }"
    )
    second = cards.nth(1).bounding_box()
    assert abs(at[0] - second["x"]) < 2 and at[1] > second["y"]
    page.keyboard.press("ArrowUp")
    assert page.evaluate(name) == cards.nth(1).get_attribute("data-name")
    # rows: one line a sound, kept per browser across a reload
    page.click('[data-lib-view="rows"]')
    assert page.get_attribute("html", "data-lib-view") == "rows"
    tall = cards.first.bounding_box()["height"]
    assert tall < 36
    assert page.evaluate(CLIPPED) == []
    page.reload()
    page.wait_for_selector('#view[data-view="library"]')
    page.wait_for_function("() => !!window.htmx")
    assert page.get_attribute("html", "data-lib-view") == "rows"
    assert page.get_attribute('[data-lib-view="rows"]', "aria-pressed") == "true"
    page.click('[data-lib-view=""]')
    assert page.get_attribute("html", "data-lib-view") == ""


# every visible piece of text on the page against what it is drawn on: the
# colours resolved by the canvas (oklch, color-mix), the backgrounds
# composited up the tree (a gradient's base colour stands for it)
CONTRAST = """() => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const x = cv.getContext('2d', { willReadFrequently: true });
  const rgba = (c) => { x.clearRect(0, 0, 1, 1); x.fillStyle = '#000'; x.fillStyle = c;
    x.fillRect(0, 0, 1, 1); const d = x.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255]; };
  const over = (top, bot) =>
    [0, 1, 2].map((i) => top[i] * top[3] + bot[i] * (1 - top[3])).concat(1);
  const lum = (c) => {
    const f = (v) => (v /= 255) <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ground = (el) => { const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const c = rgba(getComputedStyle(e).backgroundColor);
      if (c[3] > 0) { layers.push(c); if (c[3] >= 1) break; } }
    let g = rgba(getComputedStyle(document.body).backgroundColor);
    for (const l of layers.reverse()) g = over(l, g); return g; };
  const sel = '.library-view :is(.lib-name, .lib-hz, .tagchips > i, .shelf-title,'
    + ' .shelf-title small, .lib-legend, .tagfilter, .lib-tag-group > .caps,'
    + ' .lib-count, .lib-acts > .btn, .lib-acts > .badge, .lib-d-name,'
    + ' .lib-facts dt, .lib-facts dd, .lib-chain > i, .view-sub, .empty-state p)';
  const bad = [];
  for (const el of document.querySelectorAll(sel)) {
    const text = el.textContent.trim().slice(0, 16);
    if (!el.checkVisibility({ opacityProperty: true }) || !text) continue;
    const g = ground(el), fg = over(rgba(getComputedStyle(el).color), g);
    const a = lum(fg), b = lum(g);
    const r = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    if (r < 4.5) bad.push(`${el.className || el.tagName} "${text}" ${r.toFixed(2)}`);
  }
  return [...new Set(bad)];
}"""


@pytest.mark.parametrize("theme", ("night", "day"))
@pytest.mark.parametrize("look", THEMES)
def test_every_theme_keeps_the_library_legible(library, theme, look):
    page = library(1366, 768, theme=theme, look=look)
    assert page.get_attribute("html", "data-look") == (
        None if look == "studio" else look
    )
    page.locator(".lib-card .lib-play").nth(3).click()  # the dock and an active card
    page.locator('.tagfilter[data-tag="bass"]').click()  # a pressed chip
    page.wait_for_timeout(250)
    assert page.evaluate(CONTRAST) == [], (look, theme)


# every buffer source the page starts, and whether something stopped it
COUNT_SOURCES = """() => {
  const S = (window.__srcs = []), P = AudioBufferSourceNode.prototype;
  const start = P.start, stop = P.stop;
  P.start = function (...a) { const r = { stopped: false };
    S.push(r); this.addEventListener('ended', () => (r.stopped = true));
    this.__r = r; return start.apply(this, a); };
  P.stop = function (...a) { if (this.__r) this.__r.stopped = true;
    return stop.apply(this, a); };
}"""


def test_one_audition_at_a_time(library):
    """Sounds must not stick when another is picked: a card's ▶ must not
    start its phrase over the ones still sounding. The next audition fades
    the last one out."""
    page = library()
    page.evaluate(COUNT_SOURCES)
    cards = page.locator(".shelf:not(.shelf-recipes):not(.shelf-core) .lib-card")
    for n, i in enumerate((2, 5, 9), 1):
        with page.expect_response(lambda r: "/audition.wav" in r.url):
            cards.nth(i).locator(".lib-play").click()
        page.wait_for_function("(n) => window.__srcs.length >= n", arg=n)
    page.wait_for_timeout(200)
    alive = page.evaluate("() => window.__srcs.filter((r) => !r.stopped).length")
    assert alive == 1, page.evaluate("() => window.__srcs")
