"""The Visuals page: every variation draws, the swap reaches the dock.

Proves: each variation in the registry draws without a console error, and
with the demo signal on puts non-blank pixels on its card; "Use for scope"
makes the top dock's scope draw that variation on Synths, named in the
dock, and it is still there after a reload; under calm motion (the
Motion setting, colours.js) the swap flies nothing and leaves no tween;
every variation draws a frame under 1 ms at a card's size (about the
dock's), headless; ♥ moves a card up into Favourites, Shuffle deals the
dock a new pair from them, and the big preview's pick, the favourites
and the choice survive a reload; the 2× dock draws the new ones (the
goniometer's own stereo tap, the third-octave analyser) at double height.
Does not prove they look good.
"""

from __future__ import annotations

from synthlab.studio.visuals import FAMILIES, VARIATIONS

# how many card canvases hold a pixel that is not transparent
_INKED = """() => [...document.querySelectorAll('#view .vis-card')].filter((card) => {
  const c = card.querySelector('canvas');
  if (!c.width || !c.height) return false;
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  for (let i = 3; i < d.length; i += 4) if (d[i] > 8) return true;
  return false;
}).map((card) => card.dataset.vis)"""
# which variation last drew a dock slot's canvas
_DRAWN = "((id) => window.synthlab.visuals?.drawnBy(document.getElementById(id)))"
# whether a canvas (by id) holds a pixel that is not transparent
_INK = """(id) => { const c = document.getElementById(id);
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  for (let i = 3; i < d.length; i += 4) if (d[i] > 8) return true;
  return false; }"""


def test_every_variation_draws_with_the_demo_signal(studio):
    # tall enough that every card is on screen: a card draws only while seen
    studio.page.set_viewport_size({"width": 1600, "height": 3400})
    page = studio.open("visuals")
    page.wait_for_function("() => !!window.synthlab?.visuals?.demo")
    assert page.locator("#view .vis-card").count() == len(VARIATIONS)
    # at rest (no dead display): every card holds a
    # still of the sound on Synths — one note of it read offline, drawn by
    # the variation as that note would draw live
    page.wait_for_function(
        f"() => ({_INKED})().length === {len(VARIATIONS)}", timeout=10000
    )
    page.click("#vis-demo")
    assert page.get_attribute("#vis-demo", "aria-pressed") == "true"
    studio.wait_sound()
    page.wait_for_timeout(1500)
    inked = page.evaluate(_INKED)
    assert sorted(inked) == sorted(v.id for v in VARIATIONS)
    # each was drawn by its own entry, and timed: under the budget, 1 ms a
    # frame at a card's size (the time to record it; the raster of a canvas
    # this small is the CPU's, in it). The dearest are the two trails
    over = {}
    for _ in range(3):  # a GC pause or a busy machine can lift one reading
        costs = page.evaluate("() => window.synthlab.visuals.costs()")
        assert set(costs) == {v.id for v in VARIATIONS}
        over = {k: ms for k, ms in costs.items() if ms is None or ms >= 1.0}
        if not over:
            break
        page.wait_for_timeout(1000)
    assert not over, over
    assert page.locator('#view .vis-card .vis-cost:text-matches("ms$")').count() > 0
    # the transport hears it: the dock's stop ends it
    page.click("#global-stop")
    page.wait_for_function(
        "() => document.getElementById('vis-demo').ariaPressed === 'false'"
    )


def test_use_for_scope_swaps_the_dock_on_synths_and_survives_a_reload(studio):
    page = studio.open("visuals")
    page.wait_for_function("() => !!window.synthlab?.visuals")
    page.click('#view .vis-card[data-vis="phosphor"] [data-use="scope"]')
    # the swap flies a still of the card up to the dock, then takes it away
    page.wait_for_selector(".vis-ghost", state="attached", timeout=1000)
    page.wait_for_selector(".vis-ghost", state="detached", timeout=3000)
    page.click('#view .vis-card[data-vis="ridgeline"] [data-use="spectrum"]')
    assert (
        page.get_attribute(
            '#view .vis-card[data-vis="phosphor"] [data-use="scope"]', "aria-pressed"
        )
        == "true"
    )
    assert page.text_content("#vis-in-scope") == "Phosphor"
    studio.nav("editor")
    page.wait_for_function(f"() => {_DRAWN}('scope') === 'phosphor'")
    assert page.text_content('.viz-name[data-viz-slot="scope"]') == "Phosphor"
    assert page.text_content('.viz-name[data-viz-slot="spectrum"]') == "Ridgeline"
    page.reload()
    page.wait_for_selector('#view[data-view="editor"]')
    page.wait_for_function(
        f"() => {_DRAWN}('scope') === 'phosphor'"
        f" && {_DRAWN}('spectrum') === 'ridgeline'"
    )
    assert page.evaluate("() => window.synthlab.visuals.choice('scope')") == "phosphor"


def test_calm_motion_skips_every_tween(studio):
    page = studio.page
    page.context.add_init_script("localStorage.setItem('synthlab.motion', 'calm')")
    studio.open("visuals")
    page.wait_for_function("() => !!window.synthlab?.visuals")
    page.hover('#view .vis-card[data-vis="tunnel"]')
    page.click('#view .vis-card[data-vis="tunnel"] [data-use="scope"]')
    page.click('#view .vis-card[data-vis="radial"] [data-big]')
    page.click("#vis-demo")
    studio.wait_sound()
    page.wait_for_timeout(800)
    # the swap happened, without a flight
    assert page.evaluate("() => window.synthlab.visuals.choice('scope')") == "tunnel"
    assert page.locator(".vis-ghost").count() == 0
    tweens = page.evaluate(
        """() => window.gsap.getTweensOf([...document.querySelectorAll(
          '#view .vis-card, .viz-name, #scope, #spectrum, #vis-big, .btn-play,'
          + ' #vis-demo')]).length"""
    )
    assert tweens == 0
    page.click("#vis-demo")


def test_the_demo_kick_trips_the_kick_detector_and_pumps_a_play_button(studio):
    """The demo's kick is a real one (a sine falling 120 -> 50 Hz, ~-6 dBFS,
    the chord above the kick's band): motion.js's detector — the one the
    deck's keys and every ▶ pump on — reads it. A 110 Hz root held the low
    band full, so `synthlab.kick()` stayed 0 throughout."""
    page = studio.open("visuals")
    page.wait_for_function("() => !!window.synthlab?.visuals?.demo")
    page.click("#vis-demo")
    studio.wait_sound()
    seen = page.evaluate(
        """async () => {
          const kicks = [], pumps = new Set();
          for (let i = 0; i < 120; i++) {
            await new Promise((r) => setTimeout(r, 25));
            kicks.push(window.synthlab.kick());
            pumps.add(document.getElementById('play').style.getPropertyValue('--kick'));
          }
          return { top: Math.max(...kicks), pumps: [...pumps] };
        }"""
    )
    assert seen["top"] > 0.5, seen
    # the ▶'s --kick moves with it (motion.js writes it on every .btn-play)
    assert len({p for p in seen["pumps"] if p}) > 2, seen
    page.click("#vis-demo")


def test_two_x_doubles_the_dock_visualisers_and_is_remembered(studio):
    """The visualisers can be doubled in size wherever they are shown:
    2× in the top bar, kept per browser."""
    page = studio.open("editor")
    h = lambda: page.evaluate(  # noqa: E731
        "() => document.getElementById('scope').getBoundingClientRect().height"
    )
    small = h()
    page.click("#viz-size")
    assert page.get_attribute("#viz-size", "aria-pressed") == "true"
    page.wait_for_function(
        f"() => document.getElementById('scope').getBoundingClientRect().height"
        f" > {small * 1.8}"
    )
    page.reload()
    page.wait_for_selector('#view[data-view="editor"]')
    assert (
        h() > small * 1.8 and page.get_attribute("#viz-size", "aria-pressed") == "true"
    )
    page.click("#viz-size")
    page.wait_for_function(
        f"() => document.getElementById('scope').getBoundingClientRect().height"
        f" < {small * 1.2}"
    )


def test_families_favourites_shuffle_and_the_big_pick_survive_a_reload(studio):
    """The gallery grouped by family, ♥ favourites
    listed first, a Shuffle for the dock, and the big preview remembering
    its pick."""
    page = studio.open("visuals")
    page.wait_for_function("() => !!window.synthlab?.visuals?.demo")
    heads = page.locator("#view .vis-family:not([hidden]) > .vis-fam-head")
    assert heads.all_text_contents() == list(FAMILIES)  # no favourites yet
    fav = '#view .vis-family[data-family="favourites"]'
    for vid in ("plasma", "goniometer"):
        page.click(f'#view .vis-card[data-vis="{vid}"] [data-fav]')
    # up into Favourites, in the registry's order, and pressed
    assert page.is_visible(fav)
    cards = page.locator(f"{fav} .vis-card")
    assert [cards.nth(i).get_attribute("data-vis") for i in range(2)] == [
        "goniometer",
        "plasma",
    ]
    assert (
        page.get_attribute(
            f'{fav} .vis-card[data-vis="plasma"] [data-fav]', "aria-pressed"
        )
        == "true"
    )
    assert (
        page.locator(
            '#view .vis-family[data-family="generative"] [data-vis="plasma"]'
        ).count()
        == 0
    )
    # Shuffle deals from the two favourites: one in each slot
    page.click("#vis-shuffle")
    pair = page.evaluate(
        "() => [window.synthlab.visuals.choice('scope'),"
        " window.synthlab.visuals.choice('spectrum')]"
    )
    assert sorted(pair) == ["goniometer", "plasma"], pair
    page.click('#view .vis-card[data-vis="chroma"] [data-big]')
    assert page.text_content("#vis-big-name") == "Chroma wheel"
    page.reload()
    page.wait_for_selector('#view[data-view="visuals"]')
    page.wait_for_function("() => !!window.synthlab?.visuals?.demo")
    assert page.text_content("#vis-big-name") == "Chroma wheel"
    assert page.locator(f"{fav} .vis-card").count() == 2
    assert (
        page.evaluate(
            "() => [window.synthlab.visuals.choice('scope'),"
            " window.synthlab.visuals.choice('spectrum')]"
        )
        == pair
    )
    # un-♥ goes home, in its family's order
    page.click(f'{fav} .vis-card[data-vis="plasma"] [data-fav]')
    gen = page.locator('#view .vis-family[data-family="generative"] .vis-card')
    assert [gen.nth(i).get_attribute("data-vis") for i in range(gen.count())] == [
        v.id for v in VARIATIONS if v.family == "Generative"
    ]
    # Shuffle with fewer than two favourites: anything, but something new
    page.click(f'{fav} .vis-card[data-vis="goniometer"] [data-fav]')
    assert page.is_hidden(fav)
    page.click("#vis-shuffle")
    now = page.evaluate(
        "() => [window.synthlab.visuals.choice('scope'),"
        " window.synthlab.visuals.choice('spectrum')]"
    )
    assert now[0] != pair[0] and now[1] != pair[1] and now[0] != now[1], (pair, now)


def test_the_two_x_dock_draws_the_new_ones(studio):
    """The goniometer (its own stereo tap) and the third-octave analyser in
    the dock's slots, at 28 px and at 2×: both draw, by their own entries,
    under 1 ms."""
    page = studio.open("visuals")
    page.wait_for_function("() => !!window.synthlab?.visuals?.demo")
    page.click('#view .vis-card[data-vis="goniometer"] [data-use="scope"]')
    page.click('#view .vis-card[data-vis="octave"] [data-use="spectrum"]')
    page.click("#vis-demo")
    studio.wait_sound()
    for double in (False, True):
        if double:
            small = page.evaluate(
                "() => document.getElementById('scope').getBoundingClientRect().height"
            )
            page.click("#viz-size")
            page.wait_for_function(
                "(h) => document.getElementById('scope')"
                ".getBoundingClientRect().height > h * 1.8",
                arg=small,
            )
        page.wait_for_timeout(900)
        for slot, vid in (("scope", "goniometer"), ("spectrum", "octave")):
            assert page.evaluate(f"() => {_DRAWN}('{slot}')") == vid
            assert page.evaluate(_INK, slot), (slot, double)
            ms = page.evaluate(
                f"() => window.synthlab.visuals.cost(document.getElementById('{slot}'))"
            )
            assert ms is not None and ms < 1.0, (slot, double, ms)
    page.click("#viz-size")
    page.click("#vis-demo")
