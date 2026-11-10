"""Journey: the workspace (static/workspace.js; frontend.md, "Workspace:
modules you can move"). A module is taken by its head or its bare frame
and dropped beside another one or into its tabs; it folds to its head,
pops out to a floating panel, takes the whole window; a page's layout is
kept per browser and drawn again after a reload; the regions still land
in a module that moved; the keyboard walks and moves them; a page adds a
module it does not draw.

The drags are trusted mouse gestures (Playwright's `mouse`): the island
measures once on the drag's start and previews by a transform, so what is
asserted is where the module ends up — its group, its neighbours' boxes —
and what the page keeps (`synthlab.workspace.state()`).
"""

from __future__ import annotations

import json

# a right click on a module's own frame (its corner), as the browser sends it
_CTX = """(sel) => {
  const el = document.querySelector(sel), r = el.getBoundingClientRect();
  el.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true,
    button: 2, clientX: r.left + 2, clientY: r.top + 2 }));
}"""
_STATE = "() => synthlab.workspace.state()"


def _mod(page, mid: str):
    return page.locator(f'[data-module="{mid}"]')


def _drag(page, src_box, to: tuple[float, float], at=(2, 2), steps: int = 8):
    """A mouse drag from `at` inside `src_box` to the page point `to`."""
    page.mouse.move(src_box["x"] + at[0], src_box["y"] + at[1])
    page.mouse.down()
    page.mouse.move(src_box["x"] + at[0] + 30, src_box["y"] + at[1] + 30, steps=3)
    page.mouse.move(*to, steps=steps)


def _drop(page, src_box, to, at=(2, 2)):
    _drag(page, src_box, to, at)
    page.mouse.up()
    page.wait_for_timeout(150)


def _menu(page, sel: str, row: str):
    page.evaluate(_CTX, sel)
    page.wait_for_selector("#ctx-menu:popover-open")
    page.locator("#ctx-menu .menu-item", has_text=row).click()
    page.wait_for_timeout(100)


def _ids_in_order(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('#view [data-module]')]"
        ".map((m) => m.dataset.module)"
    )


def test_a_module_dragged_by_its_frame_lands_beside_another_and_stays(studio):
    # (the pads are a tab of the Play module, which
    # moves; the launcher has a head of its own)
    page = studio.open("perform")
    pads, notes = (
        _mod(page, "perform.play").bounding_box(),
        _mod(page, "perform.notes").bounding_box(),
    )
    # the drop zone: the right half of the note grid, previewed by a transform only
    _drag(
        page, pads, (notes["x"] + notes["width"] - 20, notes["y"] + notes["height"] / 2)
    )
    shade = page.evaluate(
        "() => { const s = document.querySelector('.ws-drop');"
        " return [getComputedStyle(s).display, s.style.transform]; }"
    )
    assert (
        shade[0] == "block"
        and shade[1].startswith("translate(")
        and "scale(" in shade[1]
    )
    page.mouse.up()
    page.wait_for_timeout(150)
    after = _mod(page, "perform.play").bounding_box()
    notes = _mod(page, "perform.notes").bounding_box()
    assert after["x"] >= notes["x"] + notes["width"] - 2, (after, notes)
    assert (
        page.evaluate(
            "() => document.querySelector('[data-module=\"perform.play\"]')"
            ".parentElement.localName"
        )
        == "ws-group"
    )
    # kept per browser, and drawn again before the page shows (#view waits for it)
    page.reload()
    page.wait_for_function(
        "() => !document.documentElement.hasAttribute('data-ws-wait')"
    )
    again = _mod(page, "perform.play").bounding_box()
    page.wait_for_timeout(800)  # (the view's entrance moves it: motion.js)
    assert abs(again["x"] - after["x"]) < 2 and abs(again["width"] - after["width"]) < 2
    # Esc cancels a drag: nothing moves
    xy = _mod(page, "perform.slots").bounding_box()
    head = page.locator(
        "#perform-slots .sl-head h2"
    ).bounding_box()  # its head (`data-ws-head`)
    _drag(page, head, (again["x"] + again["width"] / 2, again["y"] + 10), at=(4, 4))
    page.keyboard.press("Escape")
    page.mouse.up()
    assert _mod(page, "perform.slots").bounding_box() == xy
    assert not page.evaluate(
        "() => document.documentElement.classList.contains('ws-dragging')"
    )


def test_two_modules_share_a_place_as_tabs(studio):
    page = studio.open("perform")
    head, pads = (
        page.locator("#perform-slots .sl-head h2").bounding_box(),
        _mod(page, "perform.play").bounding_box(),
    )
    _drop(
        page,
        head,
        (pads["x"] + pads["width"] / 2, pads["y"] + pads["height"] / 2),
        at=(4, 4),
    )
    tabs = page.locator("ws-tabs .ws-tab")
    assert tabs.all_text_contents() == ["Play", "Launcher"]
    assert (
        _mod(page, "perform.slots").is_visible()
        and not _mod(page, "perform.play").is_visible()
    )
    # a tab shows its module (the click that ended the drag was no tab's)
    tabs.first.click()
    assert (
        _mod(page, "perform.play").is_visible()
        and not _mod(page, "perform.slots").is_visible()
    )
    assert tabs.first.get_attribute("aria-selected") == "true"
    page.reload()
    assert (
        _mod(page, "perform.play").is_visible()
        and not _mod(page, "perform.slots").is_visible()
    )
    # a pad still plays from its tab: the pads' own island finds them
    page.locator("#perform-pads .hit-pad").first.click()


def test_a_module_folds_floats_and_takes_the_window_from_its_menu(studio):
    page = studio.open("perform")
    tall = _mod(page, "perform.slots").bounding_box()["height"]
    _menu(page, "#perform-slots", "Collapse to its head")
    assert _mod(page, "perform.slots").bounding_box()["height"] < 40 < tall
    _menu(page, "#perform-slots", "Expand")
    assert _mod(page, "perform.slots").bounding_box()["height"] > 40
    # popped out: a fixed panel over the page, kept across a reload
    _menu(page, "#perform-play", "Pop out")
    assert (
        page.evaluate(
            "() => { const f = document.querySelector('#perform-play').parentElement;"
            " return f.localName + ' ' + getComputedStyle(f).position; }"
        )
        == "ws-float fixed"
    )
    assert "perform.play" in page.evaluate(_STATE)["float"]
    float_box = page.locator("ws-float").bounding_box()
    # a floating panel is moved by its frame
    _drop(
        page,
        _mod(page, "perform.play").bounding_box(),
        (float_box["x"] - 198, float_box["y"] + 82),
    )
    moved = page.locator("ws-float").bounding_box()
    assert (
        # (leftwards: Play floats at the right)
        abs(moved["x"] - float_box["x"] + 200) < 3
        and abs(moved["y"] - float_box["y"] - 80) < 3
    )
    page.reload()
    page.wait_for_timeout(
        800
    )  # (the view's entrance moves it with the page: motion.js)
    again = page.locator("ws-float").bounding_box()
    assert abs(again["x"] - moved["x"]) < 1 and abs(again["y"] - moved["y"]) < 1
    _menu(page, "#perform-play", "Dock it back")
    assert page.locator("ws-float").count() == 0
    assert "perform.play" not in (page.evaluate(_STATE) or {"float": {}})["float"]
    # focus: the module alone, the whole window; Esc returns
    _menu(page, "#perform-lane", "Focus (Esc returns)")
    box = _mod(page, "perform.lane").bounding_box()
    assert (
        box["x"] == 0
        and box["y"] == 0
        and box["width"] == 1400
        and box["height"] == 900
    )
    page.keyboard.press("Escape")
    assert _mod(page, "perform.lane").bounding_box()["width"] < 1000
    assert page.evaluate("() => document.documentElement.dataset.wsMax") is None


def test_the_keys_dock_is_the_page_frames_and_folds_by_its_own_button(studio):
    # the keyboard dock is not in Synths' workspace — it is every
    # page's, after #view (base.html), where no layout moves it. Its menu
    # offers no workspace rows (collapse, pop out, focus), and its own
    # "⌨ keyboard" fold is what stays: a press folds it, kept per page
    page = studio.open("editor")
    page.evaluate("() => localStorage.setItem('synthlab.fold.keys-dock', '0')")
    page.reload()
    dock, key = page.locator("#keys-dock"), page.locator("#keys-dock .pkey").first
    assert page.evaluate(
        "() => !document.querySelector('#view #keys-dock')"
        " && !document.getElementById('keys-dock').hasAttribute('data-module')"
    )
    page.evaluate(_CTX, "#keys-dock")
    page.wait_for_timeout(150)
    menu = page.locator("#ctx-menu:popover-open .menu-item")
    rows = menu.all_text_contents() if menu.count() else []
    assert not any("Collapse to its head" in r or "Pop out" in r for r in rows)
    if rows:
        page.keyboard.press("Escape")
    assert key.is_visible() and dock.bounding_box()["height"] > 100
    page.click("#keys-dock .keys-fold")
    assert not key.is_visible() and dock.bounding_box()["height"] < 48
    assert page.evaluate(_STATE) is None  # nothing of the workspace's
    page.reload()
    assert not key.is_visible()
    page.click("#keys-dock .keys-fold")
    key.wait_for(state="visible")  # (the fold's entrance fades the keys in)


def test_reset_puts_the_page_back_as_the_studio_draws_it(studio):
    page = studio.open("perform")
    before = _ids_in_order(page)
    boxes = {
        m: _mod(page, m).bounding_box()
        for m in ("perform.play", "perform.notes", "perform.drums")
    }
    pads, drums = boxes["perform.play"], boxes["perform.drums"]
    _drop(
        page, pads, (drums["x"] + drums["width"] / 2, drums["y"] + drums["height"] - 10)
    )
    _menu(page, "#perform-slots", "Collapse to its head")
    assert _ids_in_order(page) != before
    page.click("#view .ws-open")
    page.click('#ws-menu [data-cmd="Reset the layout"]')
    assert _ids_in_order(page) == before
    assert (
        page.locator("#view ws-dock, #view ws-float, #view [data-ws-fold]").count() == 0
    )
    for m, b in boxes.items():
        now = _mod(page, m).bounding_box()
        assert all(abs(now[k] - b[k]) < 3 for k in b), (m, now, b)
    assert page.evaluate(_STATE) is None
    page.reload()
    assert _ids_in_order(page) == before


def test_a_region_still_lands_in_a_module_that_moved(studio):
    page = studio.open("editor")
    # the Filter panel by its legend into Amp envelope's tabs (it moves
    # inside the #patch form: its fields still post with the patch)
    legend = page.locator("#panel-filter > legend .panel-name").bounding_box()
    amp = _mod(page, "synths.amp-envelope").bounding_box()
    _drop(
        page,
        legend,
        (amp["x"] + amp["width"] / 2, amp["y"] + amp["height"] / 2),
        at=(4, 4),
    )
    page.locator("#patch ws-tabs .ws-tab", has_text="Filter").click()
    assert page.evaluate(
        "() => !!document.getElementById('panel-filter').closest('#patch ws-group')"
    )
    curve = page.inner_html("#filter-response-wrap")
    with page.expect_response("**/fragment/patch"):
        page.evaluate(
            """() => {
              const c = document.querySelector('#panel-filter input[name=cutoff]');
              c.value = String(Number(c.min) + (Number(c.max) - Number(c.min)) * 0.2);
              c.dispatchEvent(new Event('input', { bubbles: true })); }"""
        )
    page.wait_for_function(
        "(was) => document.getElementById('filter-response-wrap').innerHTML !== was",
        arg=curve,
    )
    assert page.locator("#filter-response-wrap").count() == 1
    assert page.evaluate(
        "() => !!document.getElementById('filter-response-wrap')"
        ".closest('#patch ws-group')"
    )
    # the layer strip (a region, morphed by id) to the left of the panels; a
    # new layer lands in it there
    title = page.locator("#ls-title").bounding_box()
    patch = _mod(page, "synths.panels").bounding_box()
    _drop(page, title, (patch["x"] + 12, patch["y"] + patch["height"] / 2), at=(4, 4))
    strip, patch = (
        _mod(page, "synths.layers").bounding_box(),
        _mod(page, "synths.panels").bounding_box(),
    )
    assert (
        strip["x"] + strip["width"] <= patch["x"] + 1
        and abs(strip["y"] - patch["y"]) < 2
    )
    rows = page.locator("#layer-strip .lrow:not(.empty)").count()
    with page.expect_response("**/fragment/layer/add"):
        page.locator('#layer-strip [data-cmd="Add a layer"]').first.click()
    page.wait_for_function(
        "() => document.querySelectorAll('#layer-strip .lrow:not(.empty)')"
        f".length > {rows}"
    )
    assert page.locator("#layer-strip").count() == 1
    assert (
        page.evaluate(
            "() => document.getElementById('layer-strip').parentElement.localName"
        )
        == "ws-group"
    )
    assert _mod(page, "synths.layers").bounding_box()["x"] == strip["x"]


def test_the_keyboard_walks_the_modules_and_moves_one(studio):
    page = studio.open("perform")
    page.locator("#perform-transport .ptempo").click()
    page.keyboard.press("F6")
    assert (
        page.evaluate("() => document.activeElement.dataset.module") == "perform.loops"
    )
    page.keyboard.press("Shift+F6")
    assert (
        page.evaluate("() => document.activeElement.dataset.module")
        == "perform.transport"
    )
    # the pads, then Alt+Shift+→ puts them right of the nearest module that way
    page.evaluate(
        "() => { const m = document.getElementById('perform-play');"
        " m.tabIndex = -1; m.focus(); }"
    )
    before = _mod(page, "perform.play").bounding_box()
    page.keyboard.press("Alt+Shift+ArrowLeft")
    after = _mod(page, "perform.play").bounding_box()
    assert after["x"] < before["x"] - 100, (before, after)
    assert (
        page.evaluate("() => document.activeElement.dataset.module") == "perform.play"
    )
    page.keyboard.press("Alt+Shift+ArrowRight")
    assert _mod(page, "perform.play").bounding_box()["x"] > after["x"]
    # the menu row says how, and puts the keyboard on the module
    _menu(page, "#perform-slots", "Move with the keyboard")
    assert page.evaluate("() => document.activeElement.id") == "perform-slots"


def test_a_preset_lays_the_page_out_and_a_page_adds_a_module(studio):
    page = studio.open("editor")
    page.click("#view .ws-open")
    page.click('#ws-menu [data-cmd="Layout: Performance"]')
    # the panels one tab at a time beside the play tools
    tabs = page.locator("#patch ws-tabs .ws-tab")
    assert tabs.count() == 7 and tabs.first.text_content() == "Source"
    assert (
        _mod(page, "synths.source").is_visible()
        and not _mod(page, "synths.filter").is_visible()
    )
    # (the keyboard dock is the page frame's, in no preset: the macros and
    # XY stand in the left column)
    play, panels = (
        _mod(page, "synths.macros").bounding_box(),
        _mod(page, "synths.panels").bounding_box(),
    )
    assert play["x"] < panels["x"]
    page.reload()
    assert page.locator("#patch ws-tabs .ws-tab").count() == 7
    # Perform adds the FX rack (the server's fragment) and a big scope
    page = studio.open("perform")
    page.click("#view .ws-open")
    with page.expect_response("**/fragment/module/fx-rack"):
        page.click('#ws-menu [data-cmd="Add module: FX rack"]')
    page.wait_for_selector('#fx-rack[data-module="fx.rack"] select.fx-adder')
    page.click("#view .ws-open")
    page.click('#ws-menu [data-cmd="Add module: Scope, big"]')
    assert _mod(page, "perform.scope").locator("canvas").bounding_box()["height"] > 100
    # the rack's own post answers on Perform (its preview is Perform's)
    with page.expect_response("**/fragment/fx/add"):
        page.select_option("#fx-rack select.fx-adder[name=kind] >> nth=1", "reverb")
    page.wait_for_selector("#fx-rack .fx-unit")
    page.reload()
    page.wait_for_selector("#fx-rack .fx-unit")
    assert _mod(page, "perform.scope").count() == 1
    _menu(page, '[data-module="perform.scope"]', "Remove from this page")
    assert _mod(page, "perform.scope").count() == 0
    assert [a["id"] for a in page.evaluate(_STATE)["add"]] == ["fx.rack"]


# ---- the sound list a member; the room under a column ----------
# On Synths at 2560 px comfy the sounds area is a movable module too, and
# anything can be moved into the right column, under Layer 3 and Master FX,
# where there is room below.


def _wide(studio, w: int = 2560, h: int = 1440):
    page = studio.page
    page.set_viewport_size({"width": w, "height": h})
    page.context.add_cookies([{"name": "density", "value": "comfy", "url": studio.url}])
    page = studio.open("editor")
    # the page settles first (its fetches land, the view's entrance ends):
    # a drag measures once, at its start
    studio.settle()
    page.wait_for_timeout(800)
    return page


# how far down a module's own content reaches (what workspace.js `filled`
# measures): a module stretched to its column's foot has room under it
_FILLED = """(sel) => { const m = document.querySelector(sel);
  return Math.max(...[...m.children].map((c) => c.getBoundingClientRect().bottom)); }"""


def _foot_of_fx(page):
    """A point in the empty room under the FX racks. Master FX, the
    column's last module, keeps its own height (no stretched last
    module); its wrapper (#fx-rack) runs as tall as the
    panels, and the room under the module is the column's foot."""
    master = _mod(page, "fx.master").bounding_box()
    rack = page.locator("#fx-rack").bounding_box()
    bottom = master["y"] + master["height"]
    assert page.evaluate(_FILLED, '[data-module="fx.master"]') <= bottom + 1
    assert rack["y"] + rack["height"] - bottom > 200
    return master["x"] + master["width"] / 2, bottom + 120


def _cols(page, dock: str = "synths") -> list[list[list[str]]]:
    """Every column of groups in a dock's saved tree, as its groups' ids."""
    out = []

    def walk(n):
        if "t" in n:
            return
        if n["d"] == "col" and all("t" in k for k in n["k"]):
            out.append([k["t"] for k in n["k"]])
        for k in n["k"]:
            walk(k)

    walk(page.evaluate(_STATE)["docks"][dock])
    return out


def _shade(page):
    return page.evaluate(
        "() => { const s = document.querySelector('.ws-drop');"
        " return { on: getComputedStyle(s).display === 'block' && !s.hidden,"
        " no: s.hasAttribute('data-no'), t: s.style.transform,"
        " chip: document.querySelector('.ws-chip').textContent }; }"
    )


def test_the_sound_list_moves_under_the_fx_racks_folds_there_and_resets(studio):
    page = _wide(studio)
    home = _mod(page, "synths.sounds").bounding_box()
    assert home["x"] < 10
    # taken by its title row (the filter field and the buttons are its own)
    title = page.locator("#sound-dock .dock-title").bounding_box()
    _drag(page, title, _foot_of_fx(page), at=(10, 6))
    shade = _shade(page)
    content = page.evaluate(_FILLED, '[data-module="fx.master"]')
    y0 = float(shade["t"].split("px, ")[1].split("px")[0])
    assert shade["on"]
    assert y0 >= content - 2
    page.mouse.up()
    page.wait_for_timeout(150)
    fx, dock = (
        _mod(page, "fx.master").bounding_box(),
        _mod(page, "synths.sounds").bounding_box(),
    )
    assert abs(dock["x"] - fx["x"]) < 2 and abs(dock["width"] - fx["width"]) < 2
    assert dock["y"] >= fx["y"] + fx["height"] - 1
    assert [["fx.layer"], ["fx.master"], ["synths.sounds"]] in _cols(page)
    # its head and filter stay put while the list scrolls inside it
    page.evaluate("() => { document.getElementById('sound-dock').scrollTop = 400; }")
    head = page.locator("#sound-dock .sounds-head").bounding_box()
    assert page.evaluate("() => document.getElementById('sound-dock').scrollTop") > 0
    assert head["y"] - dock["y"] < 16 and page.locator("#dock-filter").is_visible()
    # ‹ folds it where it stands: its head stays, "Sounds ›" opens it in place
    page.click("#sound-dock .dock-title .dock-toggle")
    folded = _mod(page, "synths.sounds").bounding_box()
    assert folded["height"] < 60 and abs(folded["x"] - dock["x"]) < 2
    page.click(".view-head > .dock-expand")
    assert _mod(page, "synths.sounds").bounding_box()["height"] > 300
    # kept, and drawn there again
    page.reload()
    page.wait_for_timeout(
        800
    )  # (the view's entrance moves it with the page: motion.js)
    # (still under the racks; the whole page sits 6 px higher after a reload)
    again, fx = (
        _mod(page, "synths.sounds").bounding_box(),
        _mod(page, "fx.master").bounding_box(),
    )
    assert abs(again["x"] - dock["x"]) < 2 and abs(again["y"] - dock["y"]) < 12
    assert again["y"] >= fx["y"] + fx["height"] - 1
    # back at the left: beside the panels, a list's width (not half the page)
    patch = _mod(page, "synths.panels").bounding_box()
    title = page.locator("#sound-dock .dock-title").bounding_box()
    _drop(page, title, (patch["x"] + 8, patch["y"] + 300), at=(10, 6))
    left = _mod(page, "synths.sounds").bounding_box()
    assert left["x"] < 4 and 300 < left["width"] < 520, left
    # alone in a row's column, ‹ takes the column away, as at home
    page.click("#sound-dock .dock-title .dock-toggle")
    assert not _mod(page, "synths.sounds").is_visible()
    assert _mod(page, "synths.panels").bounding_box()["x"] < 4
    page.click(".view-head > .dock-expand")
    # Reset: the server's place, the left column
    page.click("#view .ws-open")
    page.click('#ws-menu [data-cmd="Reset the layout"]')
    back = _mod(page, "synths.sounds").bounding_box()
    assert abs(back["x"] - home["x"]) < 2 and abs(back["width"] - home["width"]) < 2
    assert (
        page.evaluate("() => document.getElementById('sound-dock').parentElement.id")
        == "view"
    )
    # a phone's sheet still opens from the bar
    page.set_viewport_size({"width": 800, "height": 900})
    page.click(".sb-sounds")
    page.wait_for_selector("#sound-dock:popover-open")
    assert (
        page.evaluate(
            "() => getComputedStyle(document.getElementById('sound-dock')).position"
        )
        == "fixed"
    )


def test_a_module_drops_into_the_room_under_a_column(studio):
    page = _wide(studio)
    # the layer strip into the room under the FX racks: a column there
    _drop(page, page.locator("#ls-title").bounding_box(), _foot_of_fx(page), at=(4, 4))
    fx, strip = (
        _mod(page, "fx.master").bounding_box(),
        _mod(page, "synths.layers").bounding_box(),
    )
    assert abs(strip["x"] - fx["x"]) < 2 and strip["y"] >= fx["y"] + fx["height"] - 1
    # laid out, a column's last module keeps its own height too (no
    # stretched last module): the room under the sub
    # spectrum, to the foot of its column, is that column's foot
    sub = _mod(page, "synths.sub").bounding_box()
    bottom = sub["y"] + sub["height"]
    col = page.evaluate(
        "() => document.querySelector('[data-module=\"synths.sub\"]')"
        ".closest('ws-split[data-d=col]').getBoundingClientRect().bottom"
    )
    assert page.evaluate(_FILLED, '[data-module="synths.sub"]') <= bottom + 1
    assert col - bottom > 100
    under = (sub["x"] + sub["width"] / 2, bottom + 50)
    _drag(page, page.locator("#ls-title").bounding_box(), under, at=(4, 4))
    assert _shade(page)["on"]
    page.mouse.up()
    page.wait_for_timeout(150)
    strip, sub = (
        _mod(page, "synths.layers").bounding_box(),
        _mod(page, "synths.sub").bounding_box(),
    )
    assert abs(strip["x"] - sub["x"]) < 2
    assert strip["y"] >= sub["y"] + sub["height"] - 1
    assert ["synths.bass"] in next(c for c in _cols(page) if ["synths.sub"] in c)
    assert _cols(page) and any(
        c[-2:] == [["synths.sub"], ["synths.layers"]] for c in _cols(page)
    )
    # a panel at the foot of a shorter column of panels (Vowel's, under
    # Filter): Vowel keeps its own height, the room under it (to the foot
    # of its column of panels) takes the drop
    vowel = _mod(page, "synths.vowel").bounding_box()
    pcol = page.evaluate(
        "() => document.getElementById('panel-vowel').parentElement"
        ".getBoundingClientRect().bottom"
    )
    assert pcol - (vowel["y"] + vowel["height"]) > 100
    amp = page.locator("#panel-amp-envelope > legend .panel-name").bounding_box()
    to = (vowel["x"] + vowel["width"] / 2, vowel["y"] + vowel["height"] + 30)
    _drop(page, amp, to, at=(4, 4))
    moved = _mod(page, "synths.amp-envelope").bounding_box()
    vowel = _mod(page, "synths.vowel").bounding_box()
    assert abs(moved["x"] - vowel["x"]) < 2
    assert moved["y"] >= vowel["y"] + vowel["height"] - 1
    assert page.evaluate(
        "() => !!document.getElementById('panel-amp-envelope').closest('#patch')"
    )


def test_a_panel_leaves_the_panels_for_the_fx_column_and_still_posts(studio):
    # anything can be moved under the Master FX column — a panel is not
    # refused outside #patch. It goes anywhere on the page
    # now: its controls name the form (`form="patch"`), their input reaches
    # the form (`ws-input`), its rules read `.patch-panel`
    page = _wide(studio)
    legend = page.locator("#panel-filter > legend .panel-name").bounding_box()
    _drag(page, legend, _foot_of_fx(page), at=(4, 4))
    shade = _shade(page)
    assert shade["on"] and shade["chip"] == "Filter"
    page.mouse.up()
    page.wait_for_timeout(150)
    fx, filt = (
        _mod(page, "fx.master").bounding_box(),
        _mod(page, "synths.filter").bounding_box(),
    )
    assert abs(filt["x"] - fx["x"]) < 2 and filt["y"] >= fx["y"] + fx["height"] - 1
    assert page.evaluate(
        "() => !document.getElementById('panel-filter').closest('#patch')"
        " && document.querySelector('#panel-filter input[name=cutoff]')"
        ".form.id === 'patch'"
    )
    assert any(c[-1] == ["synths.filter"] for c in _cols(page))
    # styled where it stands (a .patch-panel's rows, not a bare fieldset's)
    assert (
        page.evaluate(
            "() => getComputedStyle("
            "document.querySelector('#panel-filter .param')).display"
        )
        == "grid"
    )
    # its slider still posts the patch, its region still lands in it
    curve = page.inner_html("#filter-response-wrap")
    with page.expect_response("**/fragment/patch") as resp:
        page.evaluate(
            """() => {
              const c = document.querySelector('#panel-filter input[name=cutoff]');
              c.value = String(Number(c.min) + (Number(c.max) - Number(c.min)) * 0.2);
              c.dispatchEvent(new Event('input', { bubbles: true })); }"""
        )
    assert "cutoff=" in resp.value.request.post_data
    page.wait_for_function(
        "(was) => document.getElementById('filter-response-wrap').innerHTML !== was",
        arg=curve,
    )
    assert page.locator("#filter-response-wrap").count() == 1
    # kept: drawn out of the form again after a reload; Reset puts it home
    page.reload()
    page.wait_for_timeout(800)
    assert page.evaluate(
        "() => !document.getElementById('panel-filter').closest('#patch')"
        " && !!document.getElementById('panel-filter').closest('ws-group')"
    )
    page.click("#view .ws-open")
    page.click('#ws-menu [data-cmd="Reset the layout"]')
    assert page.evaluate(
        "() => document.getElementById('panel-filter')"
        ".parentElement.matches('#patch .pcol')"
    )


def test_every_right_hand_block_moves_by_its_frame_and_resets(studio):
    # at 2560 every component moves and glows. Macros, the XY pad, the
    # Bass check, the sub spectrum and each
    # FX rack are modules of the page (their wrappers only places): each is
    # taken by its bare frame into another column and the room under one
    page = _wide(studio)
    mods = [
        "synths.macros",
        "synths.xy",
        "synths.bass",
        "synths.sub",
        "fx.layer",
        "fx.master",
    ]
    home = {m: _mod(page, m).bounding_box() for m in mods}
    # every block a panel (the bench's face, its lit edge, the live glow)
    assert page.evaluate(
        '(ids) => ids.every((id) => document.querySelector(`[data-module="${id}"]`)'
        ".matches('.panel.module[data-ws-m]'))",
        mods,
    )

    def frame(mid):  # a press on its bare frame: the module itself, no control
        b = _mod(page, mid).bounding_box()
        return {"x": b["x"] + b["width"] - 4, "y": b["y"] + 2, "width": 0, "height": 0}

    # the macros under Master FX (its column's foot)
    _drop(page, frame("synths.macros"), _foot_of_fx(page), at=(0, 0))
    # the XY pad to the right of the Layer FX rack
    lay = _mod(page, "fx.layer").bounding_box()
    _drop(
        page,
        frame("synths.xy"),
        (lay["x"] + lay["width"] - 8, lay["y"] + 40),
        at=(0, 0),
    )
    # the Bass check and the sub spectrum into the FX column, under the macros
    for mid in ("synths.bass", "synths.sub"):
        mac = _mod(page, "synths.macros").bounding_box()
        content = page.evaluate(_FILLED, '[data-module="synths.macros"]')
        _drop(page, frame(mid), (mac["x"] + mac["width"] / 2, content + 30), at=(0, 0))
    # Master FX into the right column (the room the blocks left)
    sub = _mod(page, "synths.sub").bounding_box()
    _drop(
        page,
        frame("fx.master"),
        (sub["x"] + sub["width"] / 2, sub["y"] + sub["height"] - 20),
        at=(0, 0),
    )
    moved = {m: _mod(page, m).bounding_box() for m in mods}
    assert page.evaluate(
        "(ids) => ids.every((id) => document.querySelector("
        '`[data-module="${id}"]`).parentElement.localName === "ws-group")',
        mods,
    )
    assert any(
        moved[m]["x"] != home[m]["x"] or moved[m]["y"] != home[m]["y"] for m in mods
    )
    tree = json.dumps(page.evaluate(_STATE)["docks"]["synths"])
    assert all(f'"{m}"' in tree for m in mods)
    state = page.evaluate(_STATE)
    # the wrappers emptied go (no bare frame where the racks stood)
    assert page.evaluate(
        "() => [...document.querySelectorAll('#view > [data-ws-pass]')]"
        ".every((w) => getComputedStyle(w).display === 'none'"
        " || w.querySelector('[data-module]'))"
    )
    # kept
    page.reload()
    page.wait_for_timeout(800)
    assert page.evaluate(_STATE) == state
    again = {m: _mod(page, m).bounding_box() for m in mods}
    assert all(abs(again[m]["x"] - moved[m]["x"]) < 2 for m in mods), (again, moved)
    # a unit added to the moved Master FX: its rack alone answers, where it stands
    with page.expect_response("**/fragment/fx/add"):
        page.select_option("#fx-scope-master select.fx-adder", "reverb")
    page.wait_for_selector("#fx-scope-master .fx-unit")
    assert page.locator("#fx-scope-master").count() == 1
    assert (
        abs(_mod(page, "fx.master").bounding_box()["x"] - again["fx.master"]["x"]) < 2
    )
    # Reset: the server's page again
    page.click("#view .ws-open")
    page.click('#ws-menu [data-cmd="Reset the layout"]')
    back = {m: _mod(page, m).bounding_box() for m in mods}
    assert all(abs(back[m]["x"] - home[m]["x"]) < 3 for m in mods), (back, home)
    assert page.evaluate(_STATE) is None


def test_regions_land_in_the_moved_sound_list_and_fx_rack(studio, server):
    page = _wide(studio)
    title = page.locator("#sound-dock .dock-title").bounding_box()
    _drop(page, title, _foot_of_fx(page), at=(10, 6))
    assert (
        page.evaluate(
            "() => document.getElementById('sound-dock').parentElement.localName"
        )
        == "ws-group"
    )
    # a save refreshes #preset-list where the list now stands
    page.click(".sb-save")
    page.wait_for_selector("#save-dialog:popover-open")
    page.fill("#save-dialog input[name=name]", "Moved Save")
    page.click("#save-dialog button[type=submit]")
    page.wait_for_selector('#sound-dock .sound-card[data-name="Moved Save"]')
    assert page.locator("#preset-list").count() == 1
    assert page.evaluate(
        "() => !!document.getElementById('preset-list').closest('ws-group')"
    )
    # a unit added to Master FX: the rack (its own fieldset, `hx-select`) is
    # replaced in its group, the list still under it
    with page.expect_response("**/fragment/fx/add"):
        page.select_option("#fx-scope-master select.fx-adder", "reverb")
    page.wait_for_selector("#fx-scope-master .fx-unit")
    studio.settle()
    assert page.locator("#fx-scope-master").count() == 1
    assert (
        page.evaluate(
            "() => document.getElementById('fx-scope-master').parentElement.localName"
        )
        == "ws-group"
    )
    fx, dock = (
        _mod(page, "fx.master").bounding_box(),
        _mod(page, "synths.sounds").bounding_box(),
    )
    assert abs(dock["x"] - fx["x"]) < 2 and dock["y"] >= fx["y"] + fx["height"] - 1


def test_kit_fills_its_page_and_a_module_still_drops_under_the_bus_fx(studio):
    # every bit of the space filled — Kit (dense, 2560) ends
    # at the page's bottom with the bus FX (no foot); a module dragged onto
    # the FX's lower edge lands under it, and laid out the bus FX keeps its
    # own height (the beat, a step grid, takes the room)
    page = studio.page
    page.set_viewport_size({"width": 2560, "height": 1400})
    page = studio.open("drums")
    studio.settle()
    page.wait_for_timeout(800)
    fx = _mod(page, "fx.rack").bounding_box()
    foot = page.evaluate(
        "() => document.getElementById('view').getBoundingClientRect().bottom"
    )
    assert abs(foot - (fx["y"] + fx["height"])) <= 2
    # the clock strip, by its bare frame (the room between its controls)
    clock = _mod(page, "drums.transport").bounding_box()
    src = {
        "x": clock["x"] + clock["width"] / 2,
        "y": clock["y"],
        "width": 0,
        "height": 0,
    }
    edge = (fx["x"] + fx["width"] / 2, fx["y"] + fx["height"] - 6)
    _drag(page, src, edge, at=(0, clock["height"] / 2))
    assert _shade(page)["on"]
    page.mouse.up()
    page.wait_for_timeout(150)
    fx, clock, beat = (
        _mod(page, "fx.rack").bounding_box(),
        _mod(page, "drums.transport").bounding_box(),
        _mod(page, "drums.bank").bounding_box(),
    )
    assert clock["y"] >= fx["y"] + fx["height"] - 1 and beat["y"] < fx["y"]
    tree = page.evaluate(_STATE)["docks"]["drums"]
    assert tree["d"] == "col" and tree["k"][-1]["t"] == ["drums.transport"], tree
    filled = page.evaluate(_FILLED, '[data-module="fx.rack"]')
    assert fx["y"] + fx["height"] - filled < 24
