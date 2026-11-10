"""Library, Breed, Map, the Sample Lab and the clip list, in a real
browser.

Rule 1 of the direction is "no wasted space": the share of a window that is
bare page background (pixels within 1/255 of `--bg`)
stays under 5 % on each of the four views at 1440x900, 1920x1080 and
2560x1440, night and day, in the dense tier — and nothing scrolls sideways.
Also here: one primary action a view, the docked detail, the context menu
(a right click is the pen's barrel button), and the empty states.

NOT covered: how any of it looks to a person, and a real pen.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np
import pytest

from tests.studio.lab_helpers import FilterBank, song_wav

VIEWS = ("library", "breed", "map", "lab")
SIZES = ((1440, 900), (1920, 1080), (2560, 1440))
LIMIT = 5.0  # per cent of the viewport


def _rgb(png: bytes) -> np.ndarray:
    """A screenshot's pixels (8-bit RGB / RGBA, not interlaced: what
    Chromium writes) without an imaging library."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    at, data, w = 8, b"", 0
    while at < len(png):
        (n,), kind = struct.unpack(">I", png[at : at + 4]), png[at + 4 : at + 8]
        body = png[at + 8 : at + 8 + n]
        if kind == b"IHDR":
            w, h, depth, colour, _, _, lace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and colour in (2, 6) and lace == 0
            bpp = 3 if colour == 2 else 4
        elif kind == b"IDAT":
            data += body
        at += 12 + n
    raw = np.frombuffer(zlib.decompress(data), dtype=np.uint8).reshape(h, 1 + w * bpp)
    out = np.zeros((h, w * bpp), dtype=np.uint8)
    prev = np.zeros(w * bpp, dtype=np.int32)
    for y in range(h):
        f, line = int(raw[y, 0]), raw[y, 1:].astype(np.int32)
        if f == 1:  # sub
            row = line.reshape(w, bpp).cumsum(axis=0).reshape(-1) & 255
        elif f == 2:  # up
            row = (line + prev) & 255
        elif f == 0:
            row = line
        else:  # average / paeth: byte by byte
            row = np.zeros(w * bpp, dtype=np.int32)
            for i in range(w * bpp):
                a = int(row[i - bpp]) if i >= bpp else 0
                b = int(prev[i])
                c = int(prev[i - bpp]) if i >= bpp else 0
                if f == 3:
                    pred = (a + b) >> 1
                else:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
                row[i] = (int(line[i]) + pred) & 255
        out[y], prev = row, row
    return out.reshape(h, w, bpp)[:, :, :3]


BG = """() => { const c = document.createElement("canvas"); c.width = c.height = 1;
  const x = c.getContext("2d");
  x.fillStyle = getComputedStyle(document.body).backgroundColor;
  x.fillRect(0, 0, 1, 1); return [...x.getImageData(0, 0, 1, 1).data].slice(0, 3); }"""


def bare(page) -> float:
    px = _rgb(page.screenshot()).astype(np.int16)
    bg = np.array(page.evaluate(BG), dtype=np.int16)
    return float((np.abs(px - bg).max(axis=2) <= 1).mean() * 100)


@pytest.fixture
def seeded(server):
    """A board, a map with points, and a separated song with two chops —
    through the app's own routes, the fake separator standing in."""
    from fastapi.testclient import TestClient

    from synthlab.studio import sources

    c = TestClient(server.app, base_url=server.url)
    hx = {"HX-Request": "true", "HX-Current-URL": f"{server.url}/view/lab"}
    assert c.post("/fragment/breed/generation", data={}, headers=hx).status_code == 200
    jobs = server.app.state.svc.lab
    jobs.make_separator = lambda: FilterBank()
    jobs.availability = lambda: {
        "runtime": True,
        "model": True,
        "megabytes": 1,
        "reason": "",
    }
    up = c.post(
        "/lab/upload",
        files={"file": ("my song.wav", song_wav(), "audio/wav")},
        headers=hx,
    )
    assert up.status_code == 200
    sid = sources.list_sources(server.data_dir)[0]["id"]
    c.post("/lab/separate", data={"source": sid}, headers=hx)
    jobs.join()
    for at in ("1.0", "2.5"):
        c.post("/lab/chop", data={"source": sid, "op": "split", "at": at}, headers=hx)
    return server


def _page(chromium, server, w, h, theme="night"):
    ctx = chromium.new_context(viewport={"width": w, "height": h})
    ctx.add_cookies(
        [
            {"name": "theme", "value": theme, "url": server.url},
            {"name": "density", "value": "compact", "url": server.url},
        ]
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    return ctx, page, errors


def _close(ctx, page):
    """Leave the studio before the context goes: an open event stream or a
    render still answering would hold the scratch server's shutdown."""
    page.goto("about:blank")
    ctx.close()


def _open(page, server, view):
    page.goto(f"{server.url}/view/{view}")
    page.wait_for_selector(f'#view[data-view="{view}"]')
    page.wait_for_function("() => !!window.htmx")
    if view == "map":
        page.wait_for_selector("#map-svg .map-plot, #map-svg .map-empty")
    page.wait_for_timeout(350)  # the view's entry fade


@pytest.mark.parametrize("size", SIZES, ids=lambda s: str(s[0]))
@pytest.mark.parametrize("theme", ("night", "day"))
def test_rule_1_under_five_per_cent_bare_background(chromium, seeded, size, theme):
    ctx, page, errors = _page(chromium, seeded, *size, theme)
    try:
        got = {}
        for view in VIEWS:
            _open(page, seeded, view)
            got[view] = round(bare(page), 1)
            # the page neither scrolls sideways nor down: the view is the window
            over = page.evaluate(
                "() => [document.documentElement.scrollWidth - innerWidth,"
                " document.documentElement.scrollHeight - innerHeight]"
            )
            assert over[0] <= 0 and over[1] <= 0, (view, size, over)
        assert all(v < LIMIT for v in got.values()), (size, theme, got)
        assert not errors, errors
    finally:
        _close(ctx, page)


def test_one_primary_action_a_view(chromium, seeded):
    """Breed had eight orange "Use on Synths"; it has one primary now (the
    next generation). Library: the selected sound's Use. Lab: the first
    separation, or the marked chop's Keep. Map: none."""
    ctx, page, _ = _page(chromium, seeded, 1920, 1080)
    # (a Library card's own Use is drawn as a plain key — style.css 9b1 —
    # the selected card's too: the dock's is the primary)
    seen = (
        "() => [...document.querySelectorAll('#view .btn-primary')]"
        ".filter((b) => b.checkVisibility()"
        " && !b.matches('.lib-card .lib-use'))"
        ".map((b) => b.textContent.trim())"
    )
    try:
        _open(page, seeded, "breed")
        assert page.evaluate(seen) == ["Breed next generation"]
        assert page.locator("#breed-board .tile").count() == 8
        _open(page, seeded, "library")
        assert page.evaluate(seen) == []  # nothing selected yet
        page.locator(".lib-card .lib-play").nth(5).click()
        assert page.evaluate(seen) == ["Use on Synths"]
        _open(page, seeded, "map")
        assert page.evaluate(seen) == []
        # the Lab separated: the selection's Keep clip (a marked chop's
        # Keep is not a second one)
        _open(page, seeded, "lab")
        assert page.evaluate(seen) == ["Keep clip"]
        page.locator(".chop-pick").first.click()
        page.wait_for_selector(".chop-row.on")
        assert page.evaluate(seen) == ["Keep clip"]
    finally:
        _close(ctx, page)


def test_the_library_docks_the_selected_sound_beside_the_list(chromium, seeded):
    """A card is the audition and the selection: its detail — the measured
    meters, the facts, mix / + Layer / Use — shows in the dock at the right,
    inside the window, and the filter narrows the rows with a real empty
    state when nothing matches."""
    ctx, page, errors = _page(chromium, seeded, 1920, 1080)
    try:
        _open(page, seeded, "library")
        assert page.locator(".lib-dock .empty-state").is_visible()
        row = page.locator(".lib-card").nth(20)
        name = row.get_attribute("data-name")
        with page.expect_response(lambda r: "/audition.wav" in r.url):
            row.locator(".lib-play").click()
        detail = row.locator(".lib-detail")
        assert (
            detail.is_visible() and detail.locator(".lib-d-name").inner_text() == name
        )
        page.wait_for_function(  # its slide in, done
            "() => !document.querySelector('.lib-card.active > .lib-detail')"
            ".getAnimations().length"
        )
        box, dock = detail.bounding_box(), page.locator(".lib-dock").bounding_box()
        assert abs(box["x"] - dock["x"]) < 2 and abs(box["width"] - dock["width"]) < 2
        assert box["x"] + box["width"] <= 1920 and box["y"] + box["height"] <= 1080
        assert detail.locator(".meter").count() == 4  # bright, low, mid, high
        # another row takes the dock over
        page.locator(".lib-card").nth(40).locator(".lib-play").click()
        # (a card off the dock still shows its keys: its name and facts go)
        assert not detail.locator(".lib-d-name").is_visible()
        assert page.locator(".lib-d-name:visible").count() == 1
        # every family is a section, one under the next, its cards a grid
        # across the width (not columns)
        tops = page.evaluate(
            "() => [...document.querySelectorAll('.shelf')]"
            ".map((s) => Math.round(s.getBoundingClientRect().top))"
        )
        # (the core library's eight, the recipes, the six families)
        assert len(tops) == 15 and tops == sorted(set(tops))
        across = page.evaluate(
            "() => new Set([...document.querySelectorAll('.shelf-recipes .lib-card')]"
            ".map((c) => Math.round(c.getBoundingClientRect().top))).size"
        )
        assert across < page.locator(".shelf-recipes .lib-card").count() / 4
        page.fill("#lib-search", "zzzz")
        assert page.locator(".lib-none").is_visible()
        page.fill("#lib-search", "")
        assert not page.locator(".lib-none").is_visible()
        assert not errors, errors
    finally:
        _close(ctx, page)


def test_a_right_click_opens_the_things_own_actions_as_a_menu(chromium, seeded):
    """menu.js: the menu of a row / card / chop / song is built from its own
    `data-menu` controls, and an item is a click on that control — the same
    route as a hand. Esc closes it; it never closes the clip drawer it is in."""
    ctx, page, errors = _page(chromium, seeded, 1440, 900)
    try:
        _open(page, seeded, "breed")
        tile = page.locator("#breed-board .tile").nth(1)
        tile.locator(".t-name").click(button="right")
        menu = page.locator("#ctx-menu")
        assert menu.is_visible()
        items = menu.locator(".menu-item").all_inner_texts()
        assert items[0] == "Hear on its own" and "Use on Synths" in items
        assert any(i.startswith("Star") for i in items)
        with page.expect_response("**/fragment/breed/star"):
            menu.locator(".menu-item", has_text="Star").click()
        page.wait_for_selector("#breed-board .tile.starred")
        assert list(seeded.state.breed_starred) == [tile.get_attribute("data-tile-id")]
        # the keyboard: arrows move, Esc closes
        page.locator("#breed-board .tile .t-name").first.click(button="right")
        page.keyboard.press("ArrowDown")
        assert page.evaluate("() => document.activeElement.textContent") == items[1]
        page.keyboard.press("Escape")
        assert not menu.is_visible()

        _open(page, seeded, "library")
        page.locator(".lib-card .lib-name").nth(3).click(button="right")
        assert page.locator("#ctx-menu .menu-item").all_inner_texts() == [
            "Hear on its own",
            "Hear in your mix",
            "Add as a layer on Synths",
            "Use on Synths",
        ]
        page.keyboard.press("Escape")

        _open(page, seeded, "lab")
        page.locator(".chop-row .chop-what").first.click(button="right")
        chop = page.locator("#ctx-menu .menu-item").all_inner_texts()
        assert {"Hear", "Make it the region", "Keep as a clip", "Remove"} <= set(chop)
        with page.expect_response("**/lab/chop/keep"):
            page.locator("#ctx-menu .menu-item", has_text="Keep as a clip").click()
        page.wait_for_selector("[id^=chop-update-]")
        page.locator(".lab-source .lab-meta").first.click(button="right")
        assert page.locator("#ctx-menu .menu-item").all_inner_texts() == [
            "Open",
            "Delete from the Lab…",
        ]
        page.keyboard.press("Escape")

        # a clip row, inside the drawer: the menu nests, the drawer stays
        page.click('[popovertarget="clip-drawer"]:not(.cd-close)')
        page.wait_for_selector("#clip-drawer:popover-open .cd-clip")
        page.locator("#clip-drawer .cd-clip .sc-name").first.click(button="right")
        clip = page.locator("#ctx-menu .menu-item").all_inner_texts()
        assert clip[0] == "Hear" and {"Rename…", "Delete…"} <= set(clip)
        page.keyboard.press("Escape")
        assert page.locator("#clip-drawer").evaluate(
            "(d) => d.matches(':popover-open')"
        )
        # a text field keeps the browser's own menu
        page.locator("#clip-q").click(button="right")
        assert not page.locator("#ctx-menu").is_visible()
        assert not errors, errors
    finally:
        _close(ctx, page)


def test_the_lab_tiles_the_window_and_opens_its_folds_when_wide(chromium, seeded):
    """At 1920 the chop list and what else a selection becomes sit BESIDE
    the lanes, each a column, the make cards one above another; at 1440 the
    chops sit over the cards in one column beside the lanes. The lanes take
    the height; each stem has its colour, its meter and its level knob."""
    ctx, page, errors = _page(chromium, seeded, 1920, 1080)
    try:
        _open(page, seeded, "lab")
        box = lambda sel: page.locator(sel).bounding_box()  # noqa: E731
        wave, chops, make = box("#lab-wave"), box("#lab-chops"), box("#lab-make")
        assert wave["x"] + wave["width"] <= chops["x"] < make["x"]
        assert abs(chops["y"] - make["y"]) < 2 and wave["height"] > 700
        cards = page.locator("#lab-make .lab-card")
        assert cards.count() == 5
        one, two = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
        assert abs(one["x"] - two["x"]) < 2 and two["y"] > one["y"]  # a column
        # a chop's "clean" starts open only on the marked chop (every
        # chop's open would make the list a column of forms)
        opened = page.evaluate(
            """() => [...document.querySelectorAll('.chop-clean')].map((d) =>
                 [d.open, !!d.closest('[aria-current="true"]')])"""
        )
        assert opened and all(o == marked for o, marked in opened), opened
        assert page.locator("#lab-wave .lab-lane .meter").count() == 5
        assert page.locator("#lab-wave input.knob[name^='level.']").count() == 4
        # a stem a colour: its swatch and its wave, none the same
        colours = page.evaluate(
            """() => [...document.querySelectorAll('#lab-wave .lab-lane .lab-swatch')]
                 .map((s) => getComputedStyle(s).backgroundColor)"""
        )
        assert len(colours) == 5 and len(set(colours)) == 5, colours
        # a ticked stem feeds the selection: its head lit, the others' waves dimmed
        with page.expect_response("**/lab/region"):
            page.check('#lab-wave input[name="stem.drums"]')
        page.wait_for_selector("#lab-wave .lab-lane.lane-drums.feeds", state="attached")
        assert page.inner_text(".lab-pick-what .lab-what") == "drums"
        assert not errors, errors
    finally:
        _close(ctx, page)
    ctx, page, _ = _page(chromium, seeded, 1440, 900)
    try:
        _open(page, seeded, "lab")
        box = lambda sel: page.locator(sel).bounding_box()  # noqa: E731
        wave, chops, make = box("#lab-wave"), box("#lab-chops"), box("#lab-make")
        assert wave["x"] + wave["width"] <= chops["x"]
        assert abs(chops["x"] - make["x"]) < 2 and make["y"] > chops["y"]
    finally:
        _close(ctx, page)


def test_empty_states_and_the_map_rack(chromium, server):
    """A fresh studio: the Lab says there is no song (with the button that
    brings one), the clip drawer that there are no clips, the Map that it is
    empty — and once a generation is bred the Map has its rack."""
    ctx, page, errors = _page(chromium, server, 1440, 900)
    try:
        _open(page, server, "lab")
        # ONE empty state, with one action on topic (no "Start a song")
        assert page.locator("#view .empty-state").count() == 1
        assert page.locator("#lab-sources .lab-none").is_visible()
        state = page.locator("#lab-panel .empty-state")
        assert state.locator("label[for=lab-file]").is_visible()
        assert state.locator("button, a, label").count() == 1
        assert "split it into stems" in state.inner_text()
        page.click('[popovertarget="clip-drawer"]:not(.cd-close)')
        page.wait_for_selector("#clip-drawer:popover-open .empty-state.cd-empty")
        page.keyboard.press("Escape")
        _open(page, server, "map")
        # nothing logged: never an empty well — the shipped
        # sounds, faint, the sound on Synths among them, its neighbours listed
        page.wait_for_selector("#map-svg circle.point.shelf")
        assert page.locator("#map-svg .empty-state").count() == 0
        assert page.locator("#map-svg circle.point.shelf").count() > 250
        assert page.locator(".map-near .map-row").count() == 8
        _open(page, server, "breed")  # an empty board breeds its first litter
        page.wait_for_selector("#breed-board .tile")
        _open(page, server, "map")
        newest = ".map-list:not(.map-near) .map-row"
        assert page.locator(newest).count() == 8
        assert page.locator(".map-rack .meter").count() == 2
        plot, rack = (
            page.locator(".map-plot").bounding_box(),
            page.locator(".map-rack").bounding_box(),
        )
        # (the keyboard dock is every page's: the window-high
        # view leaves it its height, --keys-h)
        dock = page.locator("#keys-dock").bounding_box()["height"]
        assert plot["width"] > 1000 and rack["x"] > plot["x"]
        assert plot["height"] > 780 - dock
        # the plot's captions are 10-11 px type, not the SVG's scaled 25 px
        assert not page.locator("#map-svg .axis-label").first.is_visible()
        assert page.locator(".map-ax-x").is_visible()
        name = page.locator(f"{newest} .map-name").nth(2).inner_text()
        with page.expect_response("**/fragment/load"):
            page.locator(newest).nth(2).click()
        page.wait_for_function(
            "(n) => window.location.pathname === '/view/editor'"
            " || document.body.textContent.includes(n)",
            arg=name,
        )
        assert server.state.patch.name == name
        assert not errors, errors
    finally:
        _close(ctx, page)
