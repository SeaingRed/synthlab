# ruff: noqa: E501  (selectors and page scripts read better on one line)
"""The input matrix: each surface's core edit by each input ALONE,
asserted on the scratch studio's state — the same state every input must
reach.

This file is shared by the surfaces; each keeps its rows in its own,
clearly named functions:

- **the step sequencer** (the beat workflow): every `*drum*` function
  below — mouse + wheel, keyboard only, pen, touch, deck / MIDI, on Drums,
  in Perform's rack and in the Song drawer, and rule 1 (under 5 % bare
  background) with the sequencer on screen.

What the step-sequencer rows prove, in a real Chromium under the shipped
CSP: the page's own listeners turn those events into the grid's own post,
and the server stores what the matrix says. What they do NOT prove — what
only a hand on real hardware can check:

- the pen is SYNTHETIC (`PointerEvent`s with `pointerType: "pen"`): the
  real pressure curve (is 0.25 + pressure a playable velocity?), palm
  rejection with a hand resting on the grid, the eraser end's button
  number, Windows Ink's press-and-hold ring;
- touch is Chromium's emulation: a long press is driven through the
  DevTools protocol where it produces a `contextmenu`, and how a finger
  feels on a 40 px step at 150-200 % scaling is not measured;
- the deck is the virtual one (`synthlab.deck.handle`), MIDI a learned
  binding fed by hand: no Stream Deck + and no controller was attached;
- the wheel is Playwright's (line deltas): a precision touchpad sends
  pixel deltas, many a gesture;
- nobody LISTENED to a ratchet, a nudged hat or a build.
"""

from __future__ import annotations

import json

import pytest

from tests.browser import test_page_layouts as b2
from tests.browser.conftest import _INIT, Studio
from tests.browser.test_pen import PEN
from tests.browser.test_song_playlist import use
from tests.studio.test_song_routes import song_dict

D = "window.synthlab.deck"
tiled = b2.sized  # the sized, dense, night pages rule 1 is measured on
GRIDS = {
    "drums": "#drum-grid",
    "perform": "#perform-drum-grid",
    "song": "#drawer-grid",
}


# --- the step sequencer: helpers ----------------------------------------------


def _wait(page, check, what: str, timeout: float = 6.0):
    waited = 0.0
    while waited < timeout:
        if check():
            return
        page.wait_for_timeout(50)
        waited += 0.05
    raise AssertionError(f"timed out waiting for {what}")


class _Beat:
    """The beat a step sequencer edits, wherever it is drawn: the working
    loop's (Drums, Perform) or the open cell's pool pattern (Song)."""

    def __init__(self, server, view: str):
        self.server, self.view, self.grid = server, view, GRIDS[view]

    def _p(self) -> dict:
        s = self.server.state.song
        return s["patterns"][s["sections"][0]["cells"]["td"]]

    @property
    def cells(self) -> set:
        if self.view == "song":
            return {tuple(c) for c in self._p()["drums"]}
        return {tuple(c) for c in self.server.state.arrangement.drums}

    @property
    def hits(self) -> dict:
        if self.view == "song":
            return self._p().get("hits") or {}
        return self.server.state.arrangement.drum_hits

    @property
    def swing(self) -> dict:
        if self.view == "song":
            return self._p().get("swing") or {}
        return self.server.state.arrangement.drum_row_swing

    def cell(self, voice: str, step: int) -> str:
        return f'{self.grid} input.cell[name="drum.{voice}.{step}"]'

    def lane(self, step: int) -> str:
        # (the lane under the grid: Kit draws its stacked hit lanes after it)
        return f'{self.grid} .velrow:not(.hitrow) .vel-lane[data-step="{step}"]'


def _open(studio, server, view: str, cells=(("kick", 0), ("snare", 4), ("hat", 2))):
    """The view with its step sequencer on screen and a small beat in it."""
    beat = _Beat(server, view)
    drums = sorted([v, s] for v, s in cells)
    if view == "song":
        song = song_dict()
        song["patterns"]["pd"]["drums"] = drums
        use(server, song)
        page = studio.open("song")
        page.click("#cell-s1-td .cell-btn")
        page.wait_for_selector("#song-drawer:not([hidden]) #drawer-grid")
    else:
        from dataclasses import replace

        st = server.state
        st.arrangement = replace(
            st.arrangement, drums=drums, drum_bars=1, drum_steps=16, drum_hits={}
        )
        page = studio.open(view)
    page.wait_for_selector(f"{beat.grid} .lane-bar")
    studio.settle()
    return page, beat


def _inside(page, grid: str) -> bool:
    """The keyboard is still inside the sequencer (its form, or its hit menu)."""
    return page.evaluate("(g) => !!document.activeElement?.closest(g)", grid)


def _no_scroll(page) -> bool:
    return page.evaluate("() => scrollX === 0 && scrollY === 0")


def _drag(page, selector: str, to: str | None = None, y0=0.5, y1=0.5, steps=6):
    """A real mouse drag from one element's box to another's (or within it)."""
    page.locator(selector).scroll_into_view_if_needed()
    a = page.locator(selector).bounding_box()
    b = page.locator(to).bounding_box() if to else a
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] * y0)
    page.mouse.down()
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] * y1, steps=steps)
    page.mouse.up()


# --- the step sequencer: mouse + wheel ----------------------------------------


@pytest.mark.parametrize("view", ["drums", "perform", "song"])
def test_drum_steps_by_mouse_a_hit_its_velocity_and_a_ratchet(studio, server, view):
    """The core edit, by the mouse alone, on each page the sequencer is
    drawn: a hit (a click), its velocity (a drag in the lane, the row
    selected) and a ratchet (the hit menu, from a right click on its lane
    bar) — and a right click on the step still erases."""
    page, beat = _open(studio, server, view)
    page.click(beat.cell("hat", 6))
    _wait(page, lambda: ("hat", 6) in beat.cells, "the hit")
    assert not beat.hits
    # the row's name selects it: the lane under the grid is ITS hits' velocity
    page.click(f'{beat.grid} .rowlabel[data-voice="hat"]')
    assert (
        page.get_attribute(f"{beat.grid} .rowlabel[data-voice='hat']", "aria-pressed")
        == "true"
    )
    assert page.inner_text(f"{beat.grid} .lane-of") == "hat"
    _drag(page, beat.lane(6), y0=0.6, y1=0.7)
    _wait(page, lambda: beat.hits.get("hat.6", {}).get("v") == 0.3, "the velocity")
    # ... of that hit alone: the step's shared lane is untouched
    if view != "song":
        assert server.state.arrangement.drum_vel.get("6", 1.0) == 1.0
    assert _no_scroll(page)
    # a right click on the lane bar: the hit's menu (the one context menu)
    page.click(beat.lane(6), button="right")
    menu = f"#hitmenu-{beat.grid[1:]}"
    page.wait_for_selector(f"{menu}:popover-open")
    page.click(f'{menu} [data-hit-set="r=3"]')
    _wait(page, lambda: beat.hits.get("hat.6") == {"v": 0.3, "r": 3}, "the ratchet")
    assert page.get_attribute(beat.cell("hat", 6), "data-r") == "3"  # the LED says so
    assert _inside(page, beat.grid)
    # a right click on a STEP still erases — and the hit's data goes with it
    page.click(beat.cell("hat", 6), button="right")
    _wait(page, lambda: ("hat", 6) not in beat.cells and not beat.hits, "the erase")
    assert not page.locator(f"{menu}:popover-open").count()
    studio.settle()


def test_drum_steps_by_mouse_every_field_a_drag_paints_and_the_wheel(studio, server):
    """Each per-hit field from the lane (the lane switch, a drag across
    steps: one post, one undo step), the wheel on the FOCUSED step only,
    the row's swing knob in its head, Euclid, and undo. (900 px tall: the
    lane switch; from 1000 Kit stacks the five lanes instead —
    test_page_layouts.py, "kit fills".)"""
    studio.page.set_viewport_size({"width": 1920, "height": 900})
    page, beat = _open(studio, server, "drums", [("hat", s) for s in (2, 6, 10, 14)])
    g = beat.grid
    # drag-paint a row of steps (audio.js's stroke): one post
    _drag(page, beat.cell("rim", 0), beat.cell("rim", 3), steps=12)
    _wait(page, lambda: {("rim", s) for s in range(4)} <= beat.cells, "the stroke")
    page.click(f'{g} .rowlabel[data-voice="hat"]')
    want = {"p": 0.5, "r": 3, "n": 0.0, "t": 0.0}
    for f, at in (("p", 0.5), ("r", 0.34), ("n", 0.25), ("t", 0.25)):
        page.click(f'{g} .lane-pick [data-lane="{f}"]')
        assert page.get_attribute(f"{g} .velrow", "data-lane") == f
        # across two steps: both hits take the value, in one post
        a, b = (
            page.locator(beat.lane(2)).bounding_box(),
            page.locator(beat.lane(6)).bounding_box(),
        )
        page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] * at)
        page.mouse.down()
        page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] * at, steps=8)
        page.mouse.up()
        _wait(
            page,
            lambda f=f: (
                f in beat.hits.get("hat.2", {}) and f in beat.hits.get("hat.6", {})
            ),
            f"lane {f}",
        )
    hit = beat.hits["hat.2"]
    assert hit["p"] == want["p"] and hit["r"] == want["r"]
    assert (
        hit["n"] == 0.25 and hit["t"] == 6.0
    )  # a quarter of the way down from +half / +12
    assert beat.hits["hat.6"] == hit
    assert "hat.10" not in beat.hits  # an idle bar under the stroke takes nothing
    # one Ctrl+Z step a drag: the tune goes, the rest stay
    page.click("#drum-bank [hx-post='/fragment/pattern/undo']")
    _wait(page, lambda: "t" not in beat.hits.get("hat.2", {}), "the undo")
    assert beat.hits["hat.2"] == {"p": 0.5, "r": 3, "n": 0.25}
    # the wheel: nothing over a step that is not focused, with or without a modifier
    page.click(f'{g} .lane-pick [data-lane="p"]')
    box = page.locator(beat.cell("hat", 10)).bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, 120)
    page.wait_for_timeout(400)
    assert "hat.10" not in beat.hits
    page.focus(beat.cell("hat", 10))
    for mod in ("Control", "Shift"):
        page.keyboard.down(mod)
        page.mouse.wheel(0, 120)
        page.keyboard.up(mod)
    page.wait_for_timeout(400)
    assert "hat.10" not in beat.hits
    # focused: a notch a click, the lane's field
    page.mouse.wheel(0, 120)
    page.mouse.wheel(0, 120)
    _wait(page, lambda: beat.hits.get("hat.10") == {"p": 0.9}, "the wheel")
    page.mouse.wheel(0, -120)
    _wait(page, lambda: beat.hits.get("hat.10") == {"p": 0.95}, "the wheel back")
    assert _no_scroll(page) and _inside(page, g)
    # the row's own swing: a knob in its head at this width (tweak.js's drag)
    knob = f"{g} .drumrow[data-voice='hat'] [data-row='swing']"
    assert page.locator(knob).is_visible()
    kb = page.locator(knob).bounding_box()
    page.mouse.move(kb["x"] + kb["width"] / 2, kb["y"] + kb["height"] / 2)
    page.mouse.down()
    page.mouse.move(kb["x"] + kb["width"] / 2 + 40, kb["y"] + kb["height"] / 2, steps=5)
    page.mouse.up()
    _wait(page, lambda: beat.swing.get("hat", 0) > 0.1, "the row's swing")
    assert set(beat.swing) == {"hat"}
    assert "own" in page.get_attribute(knob, "class")
    page.dblclick(knob)  # the default: the kit's swing — no swing of its own
    _wait(page, lambda: not beat.swing, "the row following the kit again")
    # a row's level, from its head: the kit strip's own knob posts it
    lev = f"{g} .drumrow[data-voice='hat'] [data-row='level']"
    lb = page.locator(lev).bounding_box()
    page.mouse.move(lb["x"] + lb["width"] / 2, lb["y"] + lb["height"] / 2)
    page.mouse.down()
    page.mouse.move(lb["x"] + lb["width"] / 2 - 40, lb["y"] + lb["height"] / 2, steps=5)
    page.mouse.up()
    _wait(
        page,
        lambda: server.state.arrangement.drum_mix.get("hat", {}).get("level", 1) < 0.8,
        "the level",
    )
    level = server.state.arrangement.drum_mix["hat"]["level"]
    assert float(page.input_value('#drumkit [name="hat.level"]')) == pytest.approx(
        level
    )
    # Euclid a row from its ⋯
    page.click(f"{g} .drumrow[data-voice='tom'] .row-menu > button")  # (a popover)
    page.fill(f"{g} [aria-label='tom: Euclid hits']", "5")
    page.fill(f"{g} [aria-label='tom: Euclid steps']", "16")
    page.click(f"{g} [aria-label='apply Euclid to the tom row']")
    _wait(
        page,
        lambda: {s for v, s in beat.cells if v == "tom"} == {0, 4, 7, 10, 13},
        "Euclid",
    )
    assert beat.hits["hat.2"] == {"p": 0.5, "r": 3, "n": 0.25}  # nothing else moved
    studio.settle()


# --- the step sequencer: keyboard only -----------------------------------------


def _no_pointer(page) -> None:
    """From here on a pointer is a test failure."""

    def refuse(*a, **k):
        raise AssertionError("a keyboard-only journey touched the pointer")

    for name in ("click", "move", "down", "up", "dblclick", "wheel"):
        setattr(page.mouse, name, refuse)
    page.click = page.dblclick = page.hover = page.tap = refuse


def _focused(page) -> str:
    return page.evaluate("() => document.activeElement?.name || ''")


def _keys(page, *keys: str) -> None:
    for k in keys:
        page.keyboard.press(k)


def test_drum_steps_by_keyboard_only_a_bar_its_hits_a_variation_a_fill_undo(
    studio, server
):
    """No pointer event anywhere: Tab to the grid, program a kick / snare /
    hat bar, set a ratchet and a chance, nudge a hat, switch to variation
    B, add a fill from the command palette, undo."""
    from dataclasses import replace

    st = server.state
    st.arrangement = replace(st.arrangement, drums=[], drum_bars=1, drum_steps=16)
    page = studio.open("drums")
    studio.settle()
    _no_pointer(page)
    arr = lambda: server.state.arrangement  # noqa: E731
    cells = lambda: {tuple(c) for c in arr().drums}  # noqa: E731
    # the grid is ONE Tab stop: Tab until the keyboard is on a step
    for _ in range(80):
        if _focused(page).startswith("drum."):
            break
        page.keyboard.press("Tab")
    assert _focused(page) == "drum.kick.0"
    stops = page.evaluate(
        "() => document.querySelectorAll('#drum-grid .cell[tabindex=\"0\"]').length"
    )
    assert stops == 1
    # the kick, four on the floor
    for i in range(4):
        page.keyboard.press("Space")
        if i < 3:
            _keys(page, *["ArrowRight"] * 4)
    _wait(page, lambda: cells() == {("kick", s) for s in (0, 4, 8, 12)}, "the kick")
    # the snare on 2 and 4 (Enter toggles as Space does)
    page.keyboard.press("ArrowDown")
    assert _focused(page) == "drum.snare.12"
    page.keyboard.press("Enter")
    _keys(page, *["ArrowLeft"] * 8)
    page.keyboard.press("Enter")
    _wait(page, lambda: {("snare", 4), ("snare", 12)} <= cells(), "the snare")
    # the hat on the offbeats
    page.keyboard.press("ArrowDown")
    _keys(page, "ArrowLeft", "ArrowLeft")
    for i in range(4):
        page.keyboard.press("Space")
        if i < 3:
            _keys(page, *["ArrowRight"] * 4)
    hats = {("hat", s) for s in (2, 6, 10, 14)}
    _wait(page, lambda: hats <= cells(), "the hats")
    assert _focused(page) == "drum.hat.14" and len(cells()) == 10
    # ↑ ↓ selected the row as they went: the lane is the hat's
    assert (
        page.evaluate("() => document.querySelector('#drum-grid').dataset.row") == "hat"
    )
    # a ratchet: a digit on the hit
    page.keyboard.press("3")
    _wait(page, lambda: arr().drum_hits.get("hat.14") == {"r": 3}, "the ratchet")
    # a chance: PageDown turns the lane to chance, − takes the hit down a notch each
    page.keyboard.press("PageDown")
    assert page.get_attribute("#drum-grid .velrow", "data-lane") == "p"
    _keys(page, *["-"] * 10)
    _wait(
        page, lambda: arr().drum_hits.get("hat.14") == {"r": 3, "p": 0.5}, "the chance"
    )
    _keys(page, "Alt+ArrowUp")  # (the other key for the same thing)
    _wait(page, lambda: arr().drum_hits["hat.14"]["p"] == 0.55, "Alt+Up")
    # nudge another hat late: Alt+→
    _keys(page, *["ArrowLeft"] * 4)
    _keys(page, "Alt+ArrowRight", "Alt+ArrowRight")
    _wait(page, lambda: arr().drum_hits.get("hat.10") == {"n": 0.1}, "the nudge")
    assert page.get_attribute('#drum-grid [name="drum.hat.10"]', "data-n") == "+"
    # the lane bar is in the Tab order, and its arrows are the lane's own notch
    page.keyboard.press("Tab")
    assert page.evaluate(
        "() => document.activeElement.matches('#drum-grid .vel-lane[data-step=\"10\"]')"
    )
    _keys(page, "ArrowDown", "ArrowDown")
    _wait(
        page,
        lambda: arr().drum_hits["hat.10"] == {"n": 0.1, "p": 0.9},
        "the lane's keys",
    )
    page.keyboard.press("Shift+Tab")
    assert _focused(page) == "drum.hat.10"
    # Delete: the hit's data, not the hit
    page.keyboard.press("Delete")
    _wait(page, lambda: "hat.10" not in arr().drum_hits, "the hit's data cleared")
    assert ("hat", 10) in cells()
    # the hit menu from the keyboard: the Menu key, arrows, Enter
    _keys(page, *["ArrowRight"] * 4)
    page.keyboard.press("ContextMenu")
    page.wait_for_selector("#hitmenu-drum-grid:popover-open")
    assert ("hat", 14) in cells()  # the Menu key did not erase it
    assert (
        page.inner_text("#hitmenu-drum-grid .hit-menu-title").lower() == "hat · step 15"
    )
    page.keyboard.press("Enter")  # the first row: ratchet ×2
    _wait(page, lambda: arr().drum_hits["hat.14"]["r"] == 2, "the menu's row")
    assert _focused(page) == "drum.hat.14"
    studio.settle()
    before = sorted(cells())
    # variation B: Alt+2 (an empty letter starts as a copy), and back, by key
    page.keyboard.press("Alt+2")
    _wait(page, lambda: st.beat_var == "B", "variation B")
    studio.settle()
    assert sorted(cells()) == before
    assert _focused(page) == "drum.hat.14"  # the swap kept the keyboard's place
    # a fill, from the command palette
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette[open]")
    page.keyboard.type("Add a fill")
    page.keyboard.press("Enter")
    _wait(page, lambda: sorted(cells()) != before, "the fill")
    studio.settle()
    assert {c for c in cells() if c[1] < 8} == {
        c for c in map(tuple, before) if c[1] < 8
    }
    # undo: the fill goes; again: the variation switch has nothing to take back
    page.keyboard.press("Control+z")
    _wait(page, lambda: sorted(cells()) == before, "the undo")
    assert st.beat_var == "B"
    page.keyboard.press("Alt+1")
    _wait(page, lambda: st.beat_var == "A", "variation A")
    studio.settle()


# --- the step sequencer: pen ----------------------------------------------------


def test_drum_steps_by_pen_pressure_the_eraser_and_the_lane(studio, server):
    """A SYNTHETIC pen. With a row selected its pressure is that row's
    hits' own velocity (0.25 + pressure, as on the note grid); with none it
    is the step's, as before. The eraser erases the hit and its data. The
    lane takes a pen's drag as a mouse's. No menu under a pen on the grid:
    the hit menu is its ⋯ button's."""
    studio.page.add_init_script(PEN)
    page, beat = _open(studio, server, "drums", [("kick", 0)])
    g = beat.grid
    arr = lambda: server.state.arrangement  # noqa: E731
    # no row selected: the step's shared velocity (pen.js, unchanged)
    page.evaluate("(s) => penTap(s, { pressure: 0.3 })", beat.cell("hat", 2))
    _wait(page, lambda: ("hat", 2) in beat.cells, "a pen-drawn hit")
    assert arr().drum_vel["2"] == 0.55 and not beat.hits
    # the row selected (a pen tap on its name): the hit's own velocity
    page.evaluate("(s) => penTap(s)", f'{g} .rowlabel[data-voice="snare"]')
    assert page.inner_text(f"{g} .lane-of") == "snare"
    page.evaluate("(s) => penTap(s, { pressure: 0.15 })", beat.cell("snare", 4))
    _wait(page, lambda: beat.hits.get("snare.4") == {"v": 0.4}, "the hit's velocity")
    assert arr().drum_vel["4"] == 1.0  # the step's own: a kick there keeps it
    # a stroke: each step at its own pressure, in one post
    page.evaluate(
        """([a, b]) => {
          pen(a, "pointerover"); pen(a, "pointerdown", { pressure: 0.2, buttons: 1 });
          pen(a, "pointermove", { pressure: 0.5, buttons: 1, over: b });
          pen(a, "pointerup", { over: b });
        }""",
        [beat.cell("snare", 8), beat.cell("snare", 9)],
    )
    _wait(page, lambda: beat.hits.get("snare.9") == {"v": 0.75}, "the stroke")
    assert beat.hits["snare.8"] == {"v": 0.45}
    # the pen's drag in the lane (as a mouse's): the chance of a hit
    page.evaluate("(s) => penTap(s)", f'{g} .lane-pick [data-lane="p"]')
    page.evaluate(
        """(s) => { const el = document.querySelector(s), b = el.getBoundingClientRect();
          const at = (type, y) => el.dispatchEvent(new PointerEvent(type, { bubbles: true,
            cancelable: true, pointerId: 7, pointerType: "pen", pressure: 0.5, buttons: 1,
            clientX: b.left + b.width / 2, clientY: b.top + b.height * y }));
          at("pointerdown", 0.5); at("pointermove", 0.75); at("pointerup", 0.75); }""",
        beat.lane(4),
    )
    _wait(page, lambda: beat.hits.get("snare.4") == {"v": 0.4, "p": 0.25}, "the lane")
    # the hit menu: its button (a barrel tap on a step is a Shift-click, not a menu)
    page.evaluate(
        "(s) => penTap(s)", beat.cell("snare", 9)
    )  # (off, then on again: the cursor)
    _wait(page, lambda: ("snare", 9) not in beat.cells, "the tap")
    page.evaluate("(s) => penTap(s, { pressure: 0.5 })", beat.cell("snare", 9))
    _wait(page, lambda: ("snare", 9) in beat.cells, "the hit back")
    page.evaluate("(s) => penTap(s)", f"{g} .hit-menu-btn")
    page.wait_for_selector("#hitmenu-drum-grid:popover-open")
    page.evaluate("(s) => penTap(s)", '#hitmenu-drum-grid [data-hit-set="r=2"]')
    _wait(
        page, lambda: beat.hits.get("snare.9", {}).get("r") == 2, "the menu's ratchet"
    )
    # a pen's contextmenu on a step opens nothing and erases nothing
    page.evaluate(
        """(s) => { const el = document.querySelector(s);
          el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, pointerType: "pen", button: 2, buttons: 2 }));
          el.dispatchEvent(new PointerEvent("contextmenu", { bubbles: true, cancelable: true, pointerType: "pen", button: 2 })); }""",
        beat.cell("snare", 4),
    )
    page.wait_for_timeout(300)
    assert ("snare", 4) in beat.cells
    assert not page.locator("#hitmenu-drum-grid:popover-open").count()
    # the eraser end: the hit goes, and its data with it
    page.evaluate("(s) => penTap(s, { button: 5, buttons: 32 })", beat.cell("snare", 4))
    _wait(page, lambda: ("snare", 4) not in beat.cells, "the eraser")
    _wait(page, lambda: "snare.4" not in beat.hits, "the erased hit's data")
    assert _no_scroll(page)
    studio.settle()


# --- the step sequencer: touch -------------------------------------------------


@pytest.fixture
def surface(chromium, server):
    """1368 x 912 at device scale 2 with a touch screen: a Surface Pro's."""
    context = chromium.new_context(
        viewport={"width": 1368, "height": 912}, device_scale_factor=2, has_touch=True
    )
    context.add_init_script(_INIT)
    context.set_default_timeout(10_000)
    st = Studio(context.new_page(), server)
    yield st
    problems = st.unexplained()
    context.close()
    assert not problems, "the page reported errors:\n  " + "\n  ".join(problems)


def _touch(cdp, kind: str, x: float = 0, y: float = 0) -> None:
    points = [] if kind == "touchEnd" else [{"x": x, "y": y}]
    cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})


def test_drum_steps_by_touch_tap_drag_long_press_and_the_lane(surface, server):
    """Chromium's touch emulation, fingers driven through the DevTools
    protocol: a tap is a hit, a sideways drag paints, a long press on a hit
    opens its menu (and erases nothing), a vertical drag in the lane sets
    the value — and every target is 40 px or more."""
    page, beat = _open(surface, server, "drums", [("kick", 0)])
    g = beat.grid
    assert page.evaluate("() => matchMedia('(pointer: coarse)').matches")
    assert "pen" not in page.evaluate("() => ({...document.documentElement.dataset})")
    sizes = page.evaluate(
        """(g) => Object.fromEntries(Object.entries({
             step: `${g} .cell`, lane: `${g} .vel-lane`, name: `${g} .rowlabel`,
             mute: `${g} .rowbtn.mute`, pick: `${g} .lane-pick button`,
             knob: `${g} .hit-insp .knob`, menu: `${g} .hit-menu-btn`,
             letter: "#drum-bank .var-pick button", fill: "#drum-bank .beat-bar .pt",
           }).map(([k, s]) => [k, Math.round(document.querySelector(s).getBoundingClientRect().height)]))""",
        g,
    )
    assert all(v >= 40 for v in sizes.values()), sizes
    cdp = page.context.new_cdp_session(page)
    at = lambda sel, fy=0.5: (  # noqa: E731
        (b := page.locator(sel).bounding_box())["x"] + b["width"] / 2,
        b["y"] + b["height"] * fy,
    )
    page.locator(beat.cell("hat", 2)).scroll_into_view_if_needed()
    # a tap
    page.tap(beat.cell("hat", 2))
    _wait(page, lambda: ("hat", 2) in beat.cells, "the tap")
    # a sideways drag paints the row
    x0, y0 = at(beat.cell("rim", 4))
    x1, _y = at(beat.cell("rim", 7))
    _touch(cdp, "touchStart", x0, y0)
    for i in range(1, 9):
        _touch(cdp, "touchMove", x0 + (x1 - x0) * i / 8, y0)
        page.wait_for_timeout(15)
    _touch(cdp, "touchEnd")
    _wait(page, lambda: {("rim", s) for s in (4, 5, 6, 7)} <= beat.cells, "the stroke")
    # a long press on a hit: its menu — and the hit is still there
    x, y = at(beat.cell("hat", 2))
    _touch(cdp, "touchStart", x, y)
    try:
        page.wait_for_selector("#hitmenu-drum-grid:popover-open", timeout=2500)
        real = True
    except Exception:  # noqa: BLE001 - headless Chromium made no long-press menu
        real = False
    _touch(cdp, "touchEnd")
    if not real:
        # headless Chromium turned the hold into a plain tap (the hit went
        # off): put it back, and send what a long press sends by hand — a
        # touch `contextmenu`
        page.wait_for_timeout(300)
        if ("hat", 2) not in beat.cells:
            page.tap(beat.cell("hat", 2))
            _wait(page, lambda: ("hat", 2) in beat.cells, "the hit back on")
        surface.settle()
        page.evaluate(
            """(s) => document.querySelector(s).dispatchEvent(new PointerEvent("contextmenu",
                 { bubbles: true, cancelable: true, pointerType: "touch", button: 0 }))""",
            beat.cell("hat", 2),
        )
        page.wait_for_selector("#hitmenu-drum-grid:popover-open")
    print(
        f"\nlong press -> hit menu: {'the browser`s own' if real else 'a synthetic contextmenu'}"
    )
    assert ("hat", 2) in beat.cells
    assert (
        page.inner_text("#hitmenu-drum-grid .hit-menu-title").lower() == "hat · step 3"
    )
    row = page.locator('#hitmenu-drum-grid [data-hit-set="p=0.5"]')
    assert row.bounding_box()["height"] >= 40
    row.tap()
    _wait(page, lambda: beat.hits.get("hat.2") == {"p": 0.5}, "the menu's chance")
    # the lane: select the row (a tap on its name), a vertical drag sets the value
    page.tap(f'{g} .rowlabel[data-voice="hat"]')
    page.tap(f'{g} .lane-pick [data-lane="r"]')
    page.locator(beat.lane(2)).scroll_into_view_if_needed()
    x, ytop = at(beat.lane(2), 0.9)
    _x, yend = at(beat.lane(2), 0.05)
    _touch(cdp, "touchStart", x, ytop)
    for i in range(1, 7):
        _touch(cdp, "touchMove", x, ytop + (yend - ytop) * i / 6)
        page.wait_for_timeout(15)
    _touch(cdp, "touchEnd")
    _wait(page, lambda: beat.hits.get("hat.2") == {"p": 0.5, "r": 4}, "the lane drag")
    # the page did not scroll sideways under the drags
    assert page.evaluate("() => scrollX") == 0
    surface.settle()


# --- the step sequencer: the deck and MIDI -----------------------------------------


def test_drum_steps_by_deck_dials_and_keys_and_a_learned_cc(studio, server):
    """The virtual deck (`synthlab.deck.handle`, a device's own path): in
    focus mode the four dials are the selected row's level and swing and
    the cursor hit's velocity and chance, the next page its ratchet, nudge
    and tune; the keys' third page is variation A-D, Fill, Sparser,
    Busier. A learned MIDI CC (fed to midilearn.js as midi.js feeds it: no
    port is opened) moves a hit's knob the same way."""
    page, beat = _open(studio, server, "drums", [("kick", 0), ("hat", 2), ("hat", 6)])
    st = server.state
    page.click(beat.cell("hat", 6))  # off ...
    page.click(beat.cell("hat", 6))  # ... and on: the cursor is on a hit
    _wait(page, lambda: ("hat", 6) in beat.cells, "the hit")
    studio.settle()
    page.evaluate(
        f"() => {D}.handle({{ type: 'keydown', index: 6 }})"
    )  # Macros -> Focus
    page.wait_for_function(f"() => {D}.state().mode === 'focus'")
    page.focus("#drum-grid-hit-v")  # touch the inspector: its panel is the deck's
    page.wait_for_function(
        f"() => {D}.slots().some((el) => el && el.id === 'drum-grid-hit-v')"
    )
    ids = page.evaluate(f"() => {D}.slots().map((el) => el && el.id)")
    assert ids == [
        "drum-grid-row-level",
        "drum-grid-row-swing",
        "drum-grid-hit-v",
        "drum-grid-hit-p",
    ]
    turn = lambda i, n: page.evaluate(  # noqa: E731
        f"([i, n]) => {D}.handle({{ type: 'turn', index: i, ticks: n }})", [i, n]
    )
    turn(3, -6)  # the chance, down: a tick is a notch
    _wait(
        page,
        lambda: 0 < beat.hits.get("hat.6", {}).get("p", 1) < 0.8,
        "the chance dial",
    )
    turn(2, -8)  # the velocity
    _wait(
        page, lambda: beat.hits.get("hat.6", {}).get("v", 1) < 0.7, "the velocity dial"
    )
    turn(1, 25)  # the row's swing
    _wait(page, lambda: beat.swing.get("hat", 0) > 0.05, "the swing dial")
    turn(0, -20)  # the row's level
    _wait(
        page,
        lambda: st.arrangement.drum_mix.get("hat", {}).get("level", 1) < 0.95,
        "the level dial",
    )
    page.evaluate(f"() => {D}.handle({{ type: 'keydown', index: 7 }})")  # More
    page.evaluate(f"() => {D}.handle({{ type: 'keydown', index: 5 }})")  # Page ▶
    page.wait_for_function(f"() => {D}.slots()[0]?.id === 'drum-grid-hit-r'")
    assert page.evaluate(f"() => {D}.slots().map((el) => el && el.id)") == [
        "drum-grid-hit-r",
        "drum-grid-hit-n",
        "drum-grid-hit-t",
        None,
    ]
    turn(0, 127)  # the ratchet, all the way
    _wait(page, lambda: beat.hits["hat.6"].get("r") == 4, "the ratchet dial")
    # the dials follow the CURSOR: another hit, the same four knobs
    page.focus(beat.cell("hat", 2))
    turn(1, 40)  # nudge
    _wait(
        page,
        lambda: beat.hits.get("hat.2", {}).get("n", 0) > 0,
        "the nudge dial on the new hit",
    )
    studio.settle()
    # the keys: the third page is the beat's
    page.evaluate(f"() => {D}.handle({{ type: 'keydown', index: 7 }})")  # More: page 3
    face = page.evaluate(f"() => {D}.face().keys.map((k) => [k.label, k.off, k.on])")
    assert [k[0] for k in face] == [
        "A",
        "B",
        "C",
        "D",
        "Fill",
        "Sparser",
        "Busier",
        "More",
    ]
    assert (
        not any(k[1] for k in face[:7]) and face[0][2] is True
    )  # all runnable; A is lit
    page.evaluate(f"() => {D}.handle({{ type: 'keydown', index: 1 }})")  # B
    _wait(page, lambda: st.beat_var == "B", "the B key")
    studio.settle()
    n = len(beat.cells)
    page.evaluate(f"() => {D}.handle({{ type: 'keydown', index: 6 }})")  # Busier
    _wait(page, lambda: len(beat.cells) > n, "the Busier key")
    studio.settle()
    page.evaluate(f"() => {D}.handle({{ type: 'keydown', index: 5 }})")  # Sparser
    _wait(page, lambda: len(beat.cells) <= n, "the Sparser key")
    studio.settle()
    before = sorted(beat.cells)
    page.evaluate(f"() => {D}.handle({{ type: 'keydown', index: 4 }})")  # Fill
    _wait(page, lambda: sorted(beat.cells) != before, "the Fill key")
    studio.settle()

    # MIDI: a CC learned to the hit's tune knob moves it through the same hook
    # the Fill's redraw can land after settle() and replace the cell that was
    # just focused (a focus on the detached one moves no cursor): focus until
    # the inspector names the hit
    def on_the_kick():
        page.focus(beat.cell("kick", 0))
        return page.text_content("#drum-grid .hit-at").strip() == "kick 1"

    _wait(page, on_the_kick, "the cursor on kick 1")
    learned = page.evaluate(
        """() => { const L = window.synthlab.midiLearn, D = window.synthlab.deck;
             const el = document.getElementById("drum-grid-hit-t");
             L.arm(D.toOf(el), D.nameOf(el));
             const took = L.feed(0xb0, 21, 64);  // the CC that is learned
             return { to: D.toOf(el), took, list: L.list() }; }"""
    )
    assert learned["to"] == "id:drum-grid-hit-t" and learned["took"]
    assert [b["cc"] for b in learned["list"] if b["to"] == learned["to"]] == [21]
    # midilearn.js's own path for every later message of that CC (midi.js feeds it)
    page.evaluate("() => window.synthlab.midiLearn.feed(0xb0, 21, 127)")
    _wait(page, lambda: beat.hits.get("kick.0", {}).get("t") == 12.0, "the learned CC")
    page.evaluate("() => window.synthlab.midiLearn.feed(0xb0, 21, 0)")
    _wait(page, lambda: beat.hits.get("kick.0", {}).get("t") == -12.0, "the CC, down")
    page.evaluate("() => window.synthlab.midiLearn.forget('id:drum-grid-hit-t')")
    studio.settle()


# --- the step sequencer: the Song drawer, and its family -----------------------------


def test_drum_steps_in_the_song_drawer_a_variation_and_a_fill(studio, server):
    """The same component on a pool beat: its family's letters (a press on
    an empty one copies the beat into it for this cell), its fill, and a
    song-undo step each."""
    page, beat = _open(studio, server, "song")
    song = lambda: server.state.song  # noqa: E731
    cell = lambda: song()["sections"][0]["cells"]["td"]  # noqa: E731
    assert cell() == "pd"
    assert (
        page.get_attribute("#song-drawer .var-pick [data-var='A']", "aria-pressed")
        == "true"
    )
    page.click("#song-drawer .var-pick [data-var='B']")
    _wait(page, lambda: cell() != "pd", "variation B")
    studio.settle()
    b = cell()
    assert (
        song()["patterns"][b]["var"] == "B" and song()["patterns"]["pd"]["var"] == "A"
    )
    page.wait_for_selector("#song-drawer .var-pick [data-var='B'][aria-pressed='true']")
    # B's own hit, with a ratchet, in the drawer's grid
    page.click(beat.cell("hat", 10))
    _wait(page, lambda: ("hat", 10) in beat.cells, "B's hit")
    page.keyboard.press("2")
    _wait(page, lambda: beat.hits.get("hat.10") == {"r": 2}, "B's ratchet")
    assert ["hat", 10] not in song()["patterns"]["pd"]["drums"]
    studio.settle()
    # back to A: this section picks a variation that exists
    page.click("#song-drawer .var-pick [data-var='A']")
    _wait(page, lambda: cell() == "pd", "variation A")
    studio.settle()
    # the fill, and one song-undo for it
    page.click("#song-drawer .var-pick [data-var='fill']")
    _wait(page, lambda: song()["patterns"][cell()].get("var") == "fill", "the fill")
    studio.settle()
    assert cell() not in ("pd", b)
    page.keyboard.press("Control+z")
    _wait(page, lambda: cell() == "pd", "the undo")
    studio.settle()


# --- the step sequencer: rule 1 ------------------------------------------------------


@pytest.mark.parametrize("view", ["drums", "perform", "song"])
def test_drum_rule_1_under_five_percent_bare_with_the_sequencer_on_screen(
    tiled, server, view
):
    """Drums, Perform and the Song page with the drum drawer open, at 1440,
    1920 and 2560: under 5 % of the window is bare page background, and the
    page itself does not scroll."""
    from dataclasses import replace

    from synthlab.studio import beatgen

    open_, bare = tiled
    song = song_dict()
    cells = beatgen.generate("two-step", 4, 1, 16)
    feel = beatgen.dress("two-step", 4, cells)
    song["patterns"]["pd"] |= {
        "drums": feel["drums"],
        "hits": feel["hits"],
        "swing": feel["swing"],
    }
    use(server, song)
    st = server.state
    st.arrangement = replace(
        st.arrangement,
        drums=feel["drums"],
        drum_hits=feel["hits"],
        drum_row_swing=feel["swing"],
        drum_swing=0.18,
    )
    got = {}
    for w, h in b2.SIZES:
        page = open_(view, w, h)
        if view == "song":
            st.song_drawer = {"section_id": "s1", "track_id": "td"}
            page.reload()
            page.wait_for_selector("#song-drawer:not([hidden]) #drawer-grid .lane-bar")
            page.wait_for_timeout(400)
        assert page.locator(f"{GRIDS[view]} .lane-bar").is_visible()
        assert page.locator(f"{GRIDS[view]} .hit-insp").is_visible()
        got[w] = round(bare(page), 1)
        over = page.evaluate(
            "() => [document.documentElement.scrollHeight - innerHeight,"
            " document.documentElement.scrollWidth - innerWidth]"
        )
        # the keyboard dock is every page's: Drums floors
        # its rows (the steps never fold to the kick row), so where the
        # window is short of them by the dock's height (1440 x 900) the page
        # scrolls by at most that — the window-high views never do
        keys_h = page.evaluate(
            "() => parseFloat(getComputedStyle(document.documentElement)"
            ".getPropertyValue('--keys-h'))"
        )
        room = keys_h if view == "drums" else 0
        assert over[0] <= room + 1 and over[1] <= 0, (view, w, over, keys_h)
        # the row's own knobs ride in its head from 1900 px
        knobs = page.locator(f"{GRIDS[view]} .row-knobs").first.is_visible()
        assert knobs == (w >= 1900), (view, w)
    print(f"\nrule 1, {view} with the step sequencer: bare background {got} %")
    assert all(v < 5.0 for v in got.values()), (view, got)


def test_drum_hits_json_is_the_one_copy_on_the_page(studio, server):
    """The grid's hidden `hits` field after edits by three inputs is what
    the server stores — nothing rides beside it, and a hit turned off and
    on again comes back plain."""
    page, beat = _open(studio, server, "perform", [("hat", 2)])
    g = beat.grid
    page.focus(beat.cell("hat", 2))
    _keys(page, "4", "Alt+ArrowLeft")
    _wait(page, lambda: beat.hits.get("hat.2") == {"r": 4, "n": -0.05}, "the keys")
    assert json.loads(page.input_value(f'{g} [name="hits"]')) == beat.hits
    page.keyboard.press("Space")  # off
    _wait(page, lambda: not beat.cells, "the hit off")
    page.keyboard.press("Space")  # on again: a plain hit
    _wait(page, lambda: ("hat", 2) in beat.cells, "the hit on")
    assert not beat.hits and page.input_value(f'{g} [name="hits"]') == "{}"
    assert page.get_attribute(beat.cell("hat", 2), "data-r") is None
    studio.settle()
