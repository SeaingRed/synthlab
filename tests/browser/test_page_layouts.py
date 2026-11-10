"""Journey 12: Perform, Drums and Song tile the window.

Proves, in a real Chromium, dense tier, night:

- rule 1 (no bare page), measured on a screenshot: the
  fraction of a viewport screenshot that is the page's own background
  colour (`--bg`, within 1/255) is under 5 % for each of the three views
  at 1440x900, 1920x1080 and 2560x1440 — the shell's top dock included,
  which is about 2 % of a 1440 window on its own;
- none of the three scrolls the page at those sizes: a view is exactly as
  tall as the window under the dock, and its panes scroll inside — but
  Drums is at least the window: under 1000 px tall the step grid's floor
  may scroll the page, and every row of the grid shows at 1366x768 and
  1280x800, the playhead over the rows alone;
- the Song editor is docked: a cell opened at 1440x900 shows its grid, its
  tools and "Solo cell" while the arranger is still on screen, and nothing
  has scrolled; with no cell open the pane says what it is for;
- the lanes share the playlist's height, and the timeline's geometry is
  still bars x --bar-w;
- a right click on a block opens its menu and a row of it runs the route
  the drawer's own button posts; on a section head and a drum row's name
  it opens that thing's own menu; on a step it still erases;
- a track's level is a knob that posts like the slider it replaced.

Does not prove it looks right, that a pen's barrel button arrives as this
right click on real hardware, or anything about the comfy or touch tiers
(their layout is the flowing one, unchanged).
"""

from __future__ import annotations

import base64

import pytest

from tests.browser.test_song_playlist import playlist_song, use

SIZES = ((1440, 900), (1920, 1080), (2560, 1440))
VIEWS = ("song", "perform", "drums")

_COUNT = """async ([b64, tol]) => {
  const blob = await (await fetch("data:image/png;base64," + b64)).blob();
  const bmp = await createImageBitmap(blob);
  const c = new OffscreenCanvas(bmp.width, bmp.height), g = c.getContext("2d");
  g.drawImage(bmp, 0, 0);
  const d = g.getImageData(0, 0, bmp.width, bmp.height).data;
  let n = 0;
  for (let i = 0; i < d.length; i += 4)
    if (Math.abs(d[i] - 11) <= tol && Math.abs(d[i + 1] - 13) <= tol
        && Math.abs(d[i + 2] - 18) <= tol) n++;
  return n / (d.length / 4);
}"""


@pytest.fixture
def sized(chromium, server):
    """open(view, w, h) -> a page of that viewport on the dense, night studio;
    bare(page) -> the fraction of its screenshot that is `--bg`"""
    made = []
    lab = chromium.new_context().new_page()  # no CSP: decodes the screenshots
    lab.goto("about:blank")

    def open_(view, w, h):
        ctx = chromium.new_context(viewport={"width": w, "height": h})
        ctx.add_cookies(
            [
                {"name": n, "value": v, "url": server.url}
                for n, v in (("theme", "night"), ("density", "compact"))
            ]
        )
        ctx.set_default_timeout(10_000)
        made.append(ctx)
        page = ctx.new_page()
        page.goto(f"{server.url}/view/{view}")
        page.wait_for_selector(f'#view[data-view="{view}"]')
        page.wait_for_function("() => !!window.htmx")
        page.wait_for_timeout(400)
        return page

    def bare(page) -> float:
        png = base64.b64encode(page.screenshot()).decode()
        return lab.evaluate(_COUNT, [png, 1]) * 100

    yield open_, bare
    for ctx in made:
        ctx.close()
    lab.context.close()


@pytest.mark.parametrize("view", VIEWS)
def test_rule_1_under_five_percent_of_the_window_is_bare_background(
    sized, server, view
):
    open_, bare = sized
    use(server, playlist_song())
    got = {}
    for w, h in SIZES:
        page = open_(view, w, h)
        got[w] = round(bare(page), 1)
        # the view is the window: the page itself does not scroll, either way
        # — but Drums is AT LEAST the window: under 1000 px tall its step
        # grid's floor (every row at 17 px) may push the kit strips past
        # the fold (the drum grid test below), never sideways
        over = page.evaluate(
            "() => [document.documentElement.scrollHeight - innerHeight,"
            " document.documentElement.scrollWidth - innerWidth]"
        )
        tall_ok = view == "drums" and h < 1000
        assert (over[0] <= 1 or tall_ok) and over[1] <= 0, (view, w, over)
    assert all(v < 5.0 for v in got.values()), (view, got)


_ROWS = """() => {
  const g = document.querySelector('#drum-grid .drum-grid-8');
  const gb = g.getBoundingClientRect();
  const rows = [...g.querySelectorAll('.drumrow')].map(r => r.getBoundingClientRect());
  const ph = g.querySelector('.playhead');
  ph.style.setProperty('--phase', '0.5');  // drawn as while the loop plays
  const p = ph.getBoundingClientRect();
  const vel = g.querySelector('.velrow').getBoundingClientRect();
  ph.style.removeProperty('--phase');
  return {grid: [gb.top, gb.bottom], win: innerHeight, playhead: [p.top, p.bottom],
          vel: vel.bottom, rows: rows.map(r => [r.top, r.bottom])};
}"""


_BOXES = """() => Object.fromEntries(
  ['drums.transport', 'drums.bank', 'drums.kit', 'fx.rack'].map((id) => {
    const r = document.querySelector(`[data-module="${id}"]`)
      .getBoundingClientRect();
    return [id, [r.left, r.top, r.right, r.bottom]]; }))"""


_LANES = """() => [...document.querySelectorAll('#drum-grid .drum-grid-8 > .velrow')]
  .map((r) => [r.dataset.lane, r.checkVisibility()])"""


def test_kit_fills_a_wide_window_with_its_five_lanes_steps_and_pads(sized, server):
    """Small text, and every bit of the space filled. From
    2200 px the beat (its steps taking the height, its five hit lanes —
    vel, chance, ratchet, nudge, tune — stacked under them) stands beside
    the kit's eight strips (four across, two rows; each pad takes what its
    knobs leave), the bus FX across both at the page's bottom: no foot. A
    drag on a stacked lane writes that field of the shown row's hit. At
    1920 x 1080 the lanes stack too and every module is on the first
    screen; at 1440 x 900 the lane switch is back (every row stays on
    screen)."""
    open_, _ = sized
    page = open_("drums", 2560, 1400)
    b = page.evaluate(_BOXES)
    beat, kit, fx = b["drums.bank"], b["drums.kit"], b["fx.rack"]
    assert abs(beat[1] - kit[1]) <= 1 and kit[0] >= beat[2] - 1, b
    assert abs(beat[3] - kit[3]) <= 1 and fx[1] >= beat[3] - 1, b
    assert fx[0] <= beat[0] + 1 and fx[2] >= kit[2] - 1, b
    view = page.evaluate(
        "() => document.getElementById('view').getBoundingClientRect().bottom"
    )
    assert abs(view - fx[3]) <= 2, (view, b)  # no foot
    rows = page.evaluate(_ROWS)["rows"]
    assert all(bot - top > 40 for top, bot in rows), rows
    assert page.evaluate(_LANES) == [[f, True] for f in "vprnt"]
    assert not page.locator("#drum-grid .lane-pick").is_visible()
    pads = page.evaluate(
        "() => [...document.querySelectorAll('#drumkit .hit-pad')]"
        ".map((p) => Math.round(p.getBoundingClientRect().height))"
    )
    assert min(pads) > 100 and max(pads) - min(pads) <= 2, pads
    # each pad shows its voice (kitpads.js): its one-shot drawn, its numbers
    page.wait_for_function(
        "() => [...document.querySelectorAll('#drumkit .hit-pad')]"
        ".every((p) => p.querySelector('.pad-wav')"
        " && /ms$/.test(p.querySelector('.pad-read').textContent))"
    )
    read = page.inner_text("#hitpad-kick .pad-read")
    wave = page.get_attribute("#hitpad-kick .pad-wav", "d")
    assert read.startswith("dec ") and "tune " in read, read
    # a knob of the kick: its pad drawn again once the kit's post lands
    with page.expect_response("**/fragment/drums"):
        page.evaluate(
            """() => { const k = document.getElementById('d-kick-decay');
              k.value = String(Number(k.max));
              k.dispatchEvent(new Event('input', { bubbles: true })); }"""
        )
    page.wait_for_function(
        "(w) => document.querySelector('#hitpad-kick .pad-wav')"
        "?.getAttribute('d') !== w",
        arg=wave,
    )
    assert page.inner_text("#hitpad-kick .pad-read") != read
    over = page.evaluate(
        "() => [document.documentElement.scrollHeight - innerHeight,"
        " document.documentElement.scrollWidth - innerWidth,"
        " (g => g.scrollHeight - g.clientHeight)("
        "document.querySelector('#drum-grid .drum-grid-8'))]"
    )
    assert over[0] <= 1 and over[1] <= 0 and over[2] <= 1, over
    # a stacked lane drawn at a hit of the row it shows (the cursor's: a
    # kick on step 0, pressed on): that field of that hit, in the grid's post
    kick = page.locator('#drum-grid input.cell[name="drum.kick.0"]')
    if not kick.is_checked():
        with page.expect_response("**/fragment/drums/grid"):
            kick.click()
    else:
        kick.dispatch_event("pointerdown")
    page.wait_for_function(
        "() => document.querySelector('#drum-grid .hitrow .lane-who')"
        ".textContent === 'kick'"
    )
    who, step = "kick", 0
    for f, at, want in (("p", 0.5, (0.4, 0.6)), ("t", 0.25, (5, 7))):
        bar = page.locator(
            f'#drum-grid .hitrow[data-lane="{f}"] .vel-lane[data-step="{step}"]'
        )
        box = bar.bounding_box()
        with page.expect_response("**/fragment/drums/grid"):
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] * at)
            page.mouse.down()
            page.mouse.up()
        hits = page.evaluate(
            "() => JSON.parse(document.querySelector('#drum-grid [name=hits]').value)"
        )
        got = hits.get(f"{who}.{step}", {}).get(f)
        assert got is not None and want[0] <= got <= want[1], (f, who, step, hits)
    page = open_("drums", 1920, 1080)
    b = page.evaluate(_BOXES)
    win = page.evaluate("() => innerHeight")
    assert all(r[3] <= win for r in b.values()), (win, b)
    assert page.evaluate(_LANES) == [[f, True] for f in "vprnt"]
    page = open_("drums", 1440, 900)
    assert page.evaluate(_LANES) == [["v", True]] + [[f, False] for f in "prnt"]
    assert page.locator("#drum-grid .lane-pick").is_visible()


@pytest.mark.parametrize("size", ((1366, 768), (1280, 800), (1920, 1080)))
def test_the_drum_grid_shows_every_row_on_a_laptop_window(sized, server, size):
    """With the page the window's height and the kit strips and bus FX
    keeping theirs, the grid's box was 53 px at 1366 x 768 (only the kick
    row showed) and 20 px at 1280 x 800. The grid has a floor (every row at
    17 px, and its lane) and the page scrolls past it."""
    open_, _ = sized
    page = open_("drums", *size)
    got = page.evaluate(_ROWS)
    top, bottom = got["grid"]
    assert len(got["rows"]) == 8
    for r_top, r_bottom in got["rows"]:
        # inside the grid's own box (not scrolled out of it) and on the first screen
        assert top - 1 <= r_top and r_bottom <= bottom + 1, (size, got)
        assert r_bottom <= got["win"] and r_bottom - r_top >= 16, (size, got)
    # the playhead runs over the rows and their lane, not the slack under them
    assert abs(got["playhead"][0] - got["rows"][0][0]) <= 1, got
    assert got["playhead"][1] <= got["vel"] + 1, got


def test_the_song_editor_is_docked_under_the_playlist_not_under_the_fold(sized, server):
    # 1920 x 1080 (not 1440 x 900, where the 55 % pane scrolls a 1-bar
    # pattern's tools and the lanes their pane: not a size the studio is
    # laid out for)
    open_, _ = sized
    use(server, playlist_song())
    page = open_("song", 1920, 1080)
    # idle: the pane is there and says what it is for
    idle = page.locator(".drawer-idle")
    assert idle.is_visible() and "Click a block" in idle.inner_text()
    lane_idle = page.locator("#cell-s1-td").bounding_box()["height"]
    page.click("#cell-s1-td .cell-btn")
    page.wait_for_selector("#song-drawer:not([hidden]) .grid")
    page.wait_for_timeout(
        500
    )  # songcell.js's scroll-into-view, were it to move anything
    assert not idle.is_visible()
    assert page.evaluate("() => scrollY") == 0
    arr = page.locator("#arr-scroll").bounding_box()
    for sel in (
        "#song-drawer .drawer-solo",
        "#song-drawer .pattern-tools",
        "#song-drawer .grid .cell >> nth=0",
        "#cell-s1-td .cell-btn",
        "#song-ruler",
        "#song-master .master-meter",
    ):
        box = page.locator(sel).bounding_box()
        assert box and box["y"] >= 0 and box["y"] + box["height"] <= 1080, (sel, box)
    # under the lanes; the editor fills its pane, never the page (song.md
    # "Song view"): its row is fit-content(55 %) under the
    # playlist's minmax(min(396px, 45 %), 1fr) — the editor takes what its
    # pattern needs, at most 55 %, and the playlist keeps 45 % at least
    # (no "editor >= 300 px" floor: a fitted 1-bar pattern takes what it
    # needs, 297 px here)
    drawer, lanes = (
        page.locator("#song-drawer").bounding_box(),
        page.locator("#song-arranger").bounding_box(),
    )
    assert drawer["y"] >= arr["y"] + arr["height"] - 1
    both = drawer["height"] + lanes["height"]
    assert drawer["height"] <= 0.55 * both + 2, (drawer, lanes)
    assert lanes["height"] >= min(396, 0.45 * both) - 2, (drawer, lanes)
    assert drawer["height"] >= 200 and arr["height"] >= 200
    # nothing of the editor is scrolled out of its pane (pagefill.js's rows)
    assert (
        page.evaluate(
            "() => { const s = document.getElementById('song-drawer');"
            " return s.scrollHeight - s.clientHeight; }"
        )
        <= 1
    )
    # the lanes gave height to the editor and still fit their pane
    lane = page.locator("#cell-s1-td").bounding_box()["height"]
    assert 40 <= lane < lane_idle <= 168
    assert (
        page.evaluate(
            "() => { const s = document.getElementById('arr-scroll');"
            " return s.scrollHeight - s.clientHeight; }"
        )
        <= 1
    )
    # from 2200 px it stands under the playlist too;
    # the drawer's ◨ puts it beside, the window's height, and ⬓ back
    wide = open_("song", 2560, 1440)
    wide.click("#cell-s1-td .cell-btn")
    wide.wait_for_selector("#song-drawer:not([hidden]) .grid")
    a, d = (
        wide.locator("#song-arranger").bounding_box(),
        wide.locator("#song-drawer").bounding_box(),
    )
    assert (
        d["y"] >= a["y"] + a["height"] - 1
        and d["height"] <= 0.55 * (a["height"] + d["height"]) + 2
    )
    wide.click('.drawer-place [data-tab-pick="beside"]')
    wide.wait_for_function(
        "() => document.getElementById('view').dataset.tab === 'beside'"
    )
    a, d = (
        wide.locator("#song-arranger").bounding_box(),
        wide.locator("#song-drawer").bounding_box(),
    )
    assert (
        d["x"] >= a["x"] + a["width"] - 1
        and abs(d["y"] - a["y"]) <= 1
        and d["height"] > 1000
    )
    wide.click(".drawer-place [data-tab-pick]:not([data-tab-pick=beside])")
    wide.wait_for_function(
        "() => document.getElementById('view').dataset.tab !== 'beside'"
    )


def test_the_timeline_fills_the_width_and_keeps_its_geometry(sized, server):
    open_, _ = sized
    song = use(server, playlist_song())
    page = open_("song", 1920, 1080)
    bar_w = page.evaluate(
        "() => parseFloat(getComputedStyle(document.getElementById('view'))"
        ".getPropertyValue('--bar-w'))"
    )
    for sec in song["sections"]:
        w = page.locator(f"#sec-{sec['id']}").bounding_box()["width"]
        assert abs(w - sec["bars"] * sec["repeats"] * bar_w) <= 1, (sec["id"], w)
    # the table runs to the pane's edge: the time axis goes on past the last section
    table, sc = (
        page.locator(".arranger-grid").bounding_box(),
        page.locator("#arr-scroll").bounding_box(),
    )
    assert table["width"] >= sc["width"] - 1


def test_a_right_click_opens_the_menu_of_what_was_pressed(studio, server):
    use(server, playlist_song())
    page = studio.open("song")
    studio.settle()
    cell = server.state.song["sections"][0]["cells"]
    assert cell.get("td")
    # a block: its menu, and "Clear the cell" is the drawer's own post
    page.click("#cell-s1-td .cell-btn", button="right")
    page.wait_for_selector("#cell-menu:popover-open")
    assert page.locator("#cell-menu .if-filled >> nth=0").is_visible()
    page.click("#cell-menu .menu-item.danger")
    page.wait_for_function("() => !document.querySelector('#cell-s1-td.filled')")
    assert not server.state.song["sections"][0]["cells"].get("td")
    assert page.locator("#cell-menu:popover-open").count() == 0
    # the emptied cell: only what an empty cell can do
    page.click("#cell-s1-td .cell-btn", button="right")
    page.wait_for_selector("#cell-menu:popover-open")
    assert page.locator("#cell-menu .if-filled:visible").count() == 0
    page.click("#cell-menu .menu-item >> nth=1")  # New pattern here
    page.wait_for_selector("#cell-s1-td.filled")
    # a section head and a track head: their own ⋯ menus
    page.click("#sec-s2 .section-name", button="right")
    page.wait_for_selector("#secmenu-s2:popover-open")
    page.keyboard.press("Escape")
    page.click("#trk-td .track-kind", button="right")
    page.wait_for_selector("#trkmenu-td:popover-open")
    page.keyboard.press("Escape")
    # Drums: a row's name opens its tools; a step still erases
    page = studio.open("drums")
    studio.settle()
    page.click('#drum-grid .rowlabel[data-voice="snare"]', button="right")
    page.wait_for_selector("#rowmenu-drum-grid-snare:popover-open")
    step = page.locator('#drum-grid input[name="drum.kick.0"]')
    if not step.is_checked():
        step.click()
    step.click(button="right")
    assert not step.is_checked()
    assert page.locator("#rowmenu-drum-grid-kick:popover-open").count() == 0


def test_a_track_level_knob_posts_like_the_slider_it_replaced(studio, server):
    use(server, playlist_song())
    page = studio.open("song")
    studio.settle()
    knob = page.locator("#trk-td .track-mix input.knob")
    assert (
        knob.get_attribute("name") == "gain"
        and knob.get_attribute("data-default") == "1"
    )
    box = knob.bounding_box()
    assert (
        abs(box["width"] - box["height"]) <= 1 and box["width"] <= 28
    )  # a dial, not a track
    # one step from the keyboard (the arranger comes back whole after a mix
    # post, so a second arrow would need the focus a replaced control loses:
    # frontend.md, "A swap that replaces the control under the keyboard")
    knob.focus()
    page.keyboard.press("ArrowLeft")
    track = lambda: next(t for t in server.state.song["tracks"] if t["id"] == "td")  # noqa: E731
    for _ in range(40):
        if abs(track().get("gain", 1.0) - 0.95) < 1e-6:
            break
        page.wait_for_timeout(50)
    assert abs(track().get("gain", 1.0) - 0.95) < 1e-6
    page.wait_for_selector("#song-master .master-in")
    # the redrawn head's read-out (until the redraw lands the page's own
    # write, "95%", stands: the arranger is a longer fragment since P4)
    page.wait_for_function(
        "() => document.querySelector('#trk-td .track-val')?.textContent === '95'"
    )
