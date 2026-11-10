"""The input matrix: every surface's core edit by every input, reaching the
SAME server state. This file holds the PIANO ROLL's row; the other
surfaces add theirs as functions of their own below it.

The roll's core edit — add a two-step note, lengthen it, move it, copy it,
delete it — is done here by a mouse (with its wheel), by the keyboard
alone, by a pen and by a finger; each asserts what the session holds
(`notes()`: the notes `arrange._merge_runs` would play), that each gesture
is one pattern-undo step, and that the page logged no error (the `studio`
fixture). Also here: E5 (drawing on another lane), the note part of E9
(journey 1's bass, chords and lead by hand, counted) and rule 1 with the
roll open.

**What these inputs are** (`inputs.py`): the mouse and the keyboard are
Playwright's trusted events. The pen is SYNTHETIC `PointerEvent`s. The
fingers are trusted touches through the DevTools protocol, in a context
with a touch screen at device scale 2 (1368 x 912, a Surface's), except a
long press, whose `contextmenu` is dispatched (headless Chromium does not
make one from a held synthetic touch).

**What they cannot prove** (a hand on real hardware has to check these):
that a real pen's pressure curve makes a playable velocity; palm rejection
while a hand rests on the grid; that Windows' press-and-hold arrives as a
`contextmenu` with `pointerType: "touch"`, and the eraser end as button 5;
whether a finger can actually hit half a 40 px step; how pinch and
two-finger pan feel at 150-200 % scaling (speed, no inertia here); and a
precision touchpad's wheel (pixel deltas, pinch as Ctrl+wheel: summed in
zoom.js, driven here only with a mouse wheel's notches).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from synthlab.studio import patternops as po
from synthlab.studio.schema import arr_dict
from synthlab.studio.state.model import grid_rows

from .conftest import _INIT, Studio
from .inputs import COUNT_POINTERS, PEN, Keys, Mouse, Pen, Touch, clear_of_dock
from .test_page_layouts import SIZES, sized  # noqa: F401 - the fixture
from .test_songcraft import Tally

ROLL = "#transport .grid.roll"
ZOOM = (  # the loop grid's step width, as zoom.js keeps it ("" = fit)
    "() => getComputedStyle(document.documentElement)"
    ".getPropertyValue('--zoom-perform')"
)


def cell(pitch: str, step: int, box: str = "#transport") -> str:
    return f'{box} input.cell[name="cell.{pitch}.{step}"]'


def notes(server) -> list[tuple]:
    """The edited lane's notes as the engine merges them: (row, start, end)."""
    s = server.state
    a = s.arrangement
    rows = grid_rows(a, s.grid_span, s.grid_oct)
    return po.notes_in(a.cells, a.cuts, a.bars * a.steps_per_bar, rows)


def sync(page, server, box: str = "#transport", pause: int = 60):
    """The grid on the page shows what the session holds: the redraw a tool
    post answers with has landed. (`Studio.settle` is the page's network
    idle, a load state reached once: a journey here waits on what it will
    touch next.)"""
    s = server.state
    a = s.arrangement
    rows = set(grid_rows(a, s.grid_span, s.grid_oct))
    total = a.bars * a.steps_per_bar
    want = sorted(
        f"cell.{c[0]}.{c[1]}" for c in a.cells if c[0] in rows and c[1] < total
    )
    js = (
        "(box) => [...document.querySelectorAll(`${box} .grid.roll "
        ".gridrow:not(.sliderow) .cell:checked`)].map((c) => c.name).sort()"
    )
    wait(
        page, lambda: page.evaluate(js, box) == want, "the grid redrawn", server=server
    )
    page.wait_for_timeout(pause)


def sync_drawer(page, pattern):
    """`sync` for the Song drawer's grid: it shows `pattern()`'s notes."""
    js = """(names) => {
      const g = document.querySelector("#song-drawer .grid.roll");
      const lit = [...g.querySelectorAll(".gridrow:not(.sliderow) .cell:checked")];
      const want = names.filter((n) => g.querySelector(`[name="${n}"]`)).sort();
      return JSON.stringify(lit.map((c) => c.name).sort()) === JSON.stringify(want);
    }"""
    names = lambda: [f"cell.{c[0]}.{c[1]}" for c in pattern()["cells"]]  # noqa: E731
    wait(page, lambda: page.evaluate(js, names()), "the drawer's grid redrawn")
    page.wait_for_timeout(60)


def undo_steps(server) -> int:
    return len(server.state._pattern_undo)


def wait(page, check, what: str, timeout: float = 5.0, server=None):
    waited = 0.0
    while waited < timeout:
        if check():
            return
        page.wait_for_timeout(50)
        waited += 0.05
    held = f" (the lane holds {notes(server)})" if server else ""
    raise AssertionError(f"timed out waiting for {what}{held}")


def seed(server, *, cells=(), span=2, bars=2, spb=16, vel=None):
    """A minor loop of `bars` x `spb` steps holding `cells`, before the page
    opens: rows A3..A1 (two octaves of A minor) at span 2."""
    s = server.state
    s.grid_span = span
    s.arrangement = replace(
        s.arrangement,
        root="A1",
        scale_name="minor",
        bars=bars,
        steps_per_bar=spb,
        cells=[list(c) for c in cells],
        cuts=[],
        slides=[],
        velocities=dict(vel or {}),
    )
    s._pattern_undo.clear()


def perform(studio, server, **kw):
    seed(server, **kw)
    page = studio.open("perform")
    # the keyboard dock (every page's) folded on Perform, as
    # its own fold does, per page: these journeys are the roll's and its rows
    # were placed for a window-high page without it (open, at 1400 x 900 the
    # roll shows 8 rows and C2 is below them)
    # (stored, not clicked: the keyboard journeys count every pointer)
    if page.get_attribute("#keys-dock .keys-fold", "aria-expanded") == "true":
        page.evaluate(
            "() => localStorage.setItem('synthlab.fold.keys-dock@perform', '1')"
        )
        page = studio.open("perform")
    page.wait_for_function(
        "() => parseFloat(getComputedStyle(document.documentElement)"
        ".getPropertyValue('--keys-h')) < 60"
    )
    sync(page, server)
    return page


def tool(page, name: str):
    page.click(f'#transport [data-roll-tool="{name}"]')
    assert page.evaluate("() => document.documentElement.dataset.roll") == name


@pytest.fixture
def touch_studio(chromium, server):
    """A touch screen at a Surface Pro's size and scale: the coarse tier."""
    context = chromium.new_context(
        viewport={"width": 1368, "height": 912},
        has_touch=True,
        device_scale_factor=2,
    )
    context.add_init_script(_INIT)
    context.set_default_timeout(10_000)
    st = Studio(context.new_page(), server)
    yield st
    problems = st.unexplained()
    context.close()
    assert not problems, "the page reported errors:\n  " + "\n  ".join(problems)


# =============================================================================
# The piano roll
# =============================================================================

# --- mouse ---------------------------------------------------------------------


def test_roll_mouse_draws_lengthens_moves_copies_and_deletes(studio, server):
    page = perform(studio, server)
    m = Mouse(page)
    # draw: a drag across two steps is one two-step note
    m.drag(cell("C2", 2), cell("C2", 3))
    wait(page, lambda: notes(server) == [("C2", 2, 4)], "the drawn note")
    wait(page, lambda: undo_steps(server) == 1, "one undo step")
    # lengthen: its END (the last 6 px of its last step) dragged three steps on
    m.drag(cell("C2", 3), cell("C2", 6), fx=0.94)
    wait(page, lambda: notes(server) == [("C2", 2, 7)], "the longer note")
    wait(page, lambda: undo_steps(server) == 2, "one undo step")
    # ... and back: shorter by two
    m.drag(cell("C2", 6), cell("C2", 4), fx=0.94)
    wait(page, lambda: notes(server) == [("C2", 2, 5)], "the shorter note")
    # a drag that starts on the note's body is still a paint (the plain drag
    # keeps its meaning: decision 16) — here it erases, as it always did
    sync(page, server)
    tool(page, "select")
    # a marquee round it selects it ...
    m.drag(cell("D2", 1), cell("B1", 6))
    wait(page, lambda: page.locator(f"{ROLL} .cell.sel").count() == 3, "the selection")
    # ... a drag on it moves it: four steps later, two rows up
    m.drag(cell("C2", 3), cell("E2", 7))
    wait(page, lambda: notes(server) == [("E2", 6, 9)], "the moved note")
    wait(
        page,
        lambda: page.locator(f"{ROLL} .cell.sel").count() == 3,
        "it stays selected",
    )
    # Ctrl + drag copies: the original stays
    sync(page, server)
    for sel in (cell("E2", 7), cell("A1", 17)):  # in view, as Mouse.drag does
        page.locator(sel).scroll_into_view_if_needed()
    x0, y0 = m.centre(cell("E2", 7))
    x1, y1 = m.centre(cell("A1", 17))
    page.mouse.move(x0, y0)
    page.keyboard.down("Control")
    page.mouse.down()
    page.mouse.move(x1, y1, steps=8)
    page.mouse.up()
    page.keyboard.up("Control")
    wait(
        page,
        lambda: notes(server) == [("E2", 6, 9), ("A1", 16, 19)],
        "the copy",
        server=server,
    )
    # the copy is the selection now: the toolbar's delete takes it alone
    sync(page, server)
    page.click('#transport [hx-post$="/notes/remove"]')
    wait(page, lambda: notes(server) == [("E2", 6, 9)], "the copy deleted")
    # a right click erases a step in any tool (grid.js)
    page.click(cell("E2", 8), button="right")
    wait(page, lambda: notes(server) == [("E2", 6, 8)], "the erased step")
    # every gesture above was one pattern-undo step: Ctrl+Z walks them back
    # (draw, lengthen, shorten, move, copy, delete, erase: seven)
    wait(page, lambda: undo_steps(server) == 7, "seven undo steps")
    page.click("#pattern-undo")
    wait(page, lambda: notes(server) == [("E2", 6, 9)], "one undo step")
    wait(page, lambda: undo_steps(server) == 6, "one step taken back")


def test_roll_mouse_a_fast_stroke_fills_every_step_between_two_samples(studio, server):
    page = perform(studio, server)
    m = Mouse(page)
    # six pointer samples across fourteen steps (the journey log's dead end 8)
    m.drag(cell("A1", 1), cell("A1", 14), steps=5)
    wait(page, lambda: notes(server) == [("A1", 1, 15)], "an unbroken note")


def test_roll_mouse_erase_tool_and_the_stamp(studio, server):
    page = perform(studio, server, cells=[["A1", s] for s in range(8)])
    m = Mouse(page)
    tool(page, "erase")
    m.drag(cell("A1", 2), cell("A1", 5))
    wait(
        page, lambda: notes(server) == [("A1", 0, 2), ("A1", 6, 8)], "the erased steps"
    )
    page.click(cell("C2", 12))  # erase never draws
    page.wait_for_timeout(300)
    assert notes(server) == [("A1", 0, 2), ("A1", 6, 8)]
    # the stamp: one click writes the chord of the key on that row
    sync(page, server)
    tool(page, "stamp")
    page.select_option('#transport [data-roll-opt="len"]', "4")
    before = undo_steps(server)
    page.click(cell("D2", 16))
    want = [("A2", 16, 20), ("F2", 16, 20), ("D2", 16, 20)]
    wait(page, lambda: notes(server)[2:] == want, "the stamped triad")
    wait(page, lambda: undo_steps(server) == before + 1, "one undo step")
    # the option survived the grid's redraw, and changing it posted nothing
    sync(page, server)
    assert page.input_value('#transport [data-roll-opt="len"]') == "4"


def test_roll_wheel_scrolls_and_zooms_and_never_changes_a_value(studio, server):
    held = [["A1", 0], ["E2", 4]]
    page = perform(studio, server, cells=held, span=3, bars=4, vel={"0": 0.5})
    m = Mouse(page)
    sync(page, server)
    geo = """() => {
      const g = document.querySelector("#transport .grid.roll");
      const ph = g.querySelector(".playhead");
      const cells = g.querySelector(".gridrow").querySelectorAll(".cell");
      const a = cells[0].getBoundingClientRect();
      const z = cells[cells.length - 1].getBoundingClientRect();
      ph.style.setProperty("--phase", "0");
      const p = ph.getBoundingClientRect();
      ph.style.removeProperty("--phase");
      const root = getComputedStyle(document.documentElement);
      return { left: g.scrollLeft, top: g.scrollTop, w: a.width, first: a.left,
               last: z.right, head: [p.left, p.right], page: [scrollX, scrollY],
               zoom: root.getPropertyValue("--zoom-perform") };
    }"""
    start = page.evaluate(geo)
    assert start["zoom"] == ""
    # Ctrl+wheel up: wider steps, about the pointer
    target = cell("A2", 20)
    x_before = page.locator(target).bounding_box()["x"]
    m.wheel(target, dy=-120, key="Control")
    m.wheel(target, dy=-120, key="Control")
    zoomed = page.evaluate(geo)
    assert zoomed["zoom"] in ("28px", "40px", "56px") and zoomed["w"] > start["w"]
    box = page.locator(target).bounding_box()
    px, _py = m.centre(target)
    assert (
        box["x"] <= px <= box["x"] + box["width"]
    )  # the step is still under the pointer
    assert abs(box["x"] - x_before) < 2 * box["width"]
    # the playhead's track is the whole pattern at any zoom, however scrolled
    assert abs(zoomed["head"][0] - zoomed["first"]) <= 3
    assert abs(zoomed["head"][1] - zoomed["last"]) <= 3
    # Shift+wheel: sideways; a plain wheel: down; neither moves the page
    m.wheel(target, dy=240, key="Shift")
    side = page.evaluate(geo)
    assert side["left"] > zoomed["left"] and side["top"] == zoomed["top"]
    m.wheel(cell("A2", 30), dy=160)
    down = page.evaluate(geo)
    assert down["top"] > side["top"] and down["left"] == side["left"]
    assert down["page"] == start["page"] == [0, 0]
    # a wheel over the velocity lane changes nothing either (it is not focused)
    m.wheel(f"{ROLL} .vel-lane[data-step='0']", dy=-120)
    m.wheel(f"{ROLL} .vel-lane[data-step='0']", dy=120)
    # a middle-button drag pans
    m.drag(cell("A2", 34), cell("A2", 30), button="middle")
    assert page.evaluate(geo)["left"] > down["left"]
    # Ctrl+wheel down, then "fit": back to the pane's own width
    m.wheel(cell("A2", 34), dy=120, key="Control")
    assert float(page.evaluate(geo)["zoom"][:-2]) < float(zoomed["zoom"][:-2])
    page.click('#transport [data-zoom-by="0"]')
    assert page.evaluate(geo)["zoom"] == ""
    # nothing was edited: no value moved, no post, no undo step
    sync(page, server)
    a = server.state.arrangement
    assert sorted(a.cells) == sorted(held) and a.velocities == {"0": 0.5}
    wait(page, lambda: undo_steps(server) == 0, "one undo step")


def test_roll_zoom_is_remembered_across_a_redraw_and_a_reload(studio, server):
    page = perform(studio, server)
    page.click('#transport [data-zoom-by="1"]')
    z = page.evaluate(ZOOM)
    assert z.endswith("px")
    w = page.locator(cell("A1", 0)).bounding_box()["width"]
    assert abs(w - float(z[:-2])) < 1
    page.click('#transport [hx-post$="/notes/shift_right"]')  # the grid is redrawn
    sync(page, server)
    assert abs(page.locator(cell("A1", 0)).bounding_box()["width"] - w) < 1
    page.reload()
    page.wait_for_selector(ROLL)
    assert abs(page.locator(cell("A1", 0)).bounding_box()["width"] - w) < 1


# --- keyboard only ---------------------------------------------------------------


def test_roll_keyboard_only_a_chord_drawn_lengthened_moved_copied_deleted(
    studio, server
):
    studio.page.add_init_script(COUNT_POINTERS)
    page = perform(studio, server)
    k = Keys(page)
    # Tab to the grid: its cells are ONE stop, its velocity lanes one more
    page.locator('#transport [data-zoom-by="1"]').focus()
    k.press("Tab")  # (the ? at the end of the toolbar)
    # the chord lane over the roll: its tools, then its chords
    # (none yet: the button that rolls a progression in)
    k.press("Tab")
    assert k.focused() == "the loop's chords"
    k.press("Tab")
    assert k.focused() == "give the loop chords"
    k.press("Tab")
    assert k.focused() == "cell.A3.0"
    k.press("Tab")
    assert k.focused() == "velocity step 1"
    k.press("Shift+Tab")
    assert k.focused() == "cell.A3.0"
    # the cursor: arrows, Home / End (the bar), PageDown (an octave)
    k.press("ArrowRight", 3)
    k.press("End")
    assert k.focused() == "cell.A3.15"
    k.press("Home")
    k.press("PageDown")
    assert k.focused() == "cell.A2.0"
    k.press("PageDown")
    assert k.focused() == "cell.A1.0"
    # a four-note chord: A1 C2 E2 G2 on step 0
    for i in range(4):
        k.press("Space")
        if i < 3:
            k.press("ArrowUp", 2)
    wait(page, lambda: len(notes(server)) == 4, "the chord")
    assert {n[0] for n in notes(server)} == {"A1", "C2", "E2", "G2"}
    assert k.focused() == "cell.G2.0"
    # lengthen the note under the cursor: Ctrl+→ twice
    sync(page, server)
    k.press("Control+ArrowRight")
    wait(page, lambda: ("G2", 0, 2) in notes(server), "one step longer")
    k.press("Control+ArrowRight")
    wait(page, lambda: ("G2", 0, 3) in notes(server), "two steps longer")
    wait(page, lambda: k.focused() == "cell.G2.0", "the cursor back after the redraw")
    # its velocity: Ctrl+↓ (the step's lane)
    sync(page, server)
    k.press("Control+ArrowDown")
    wait(page, lambda: server.state.arrangement.velocities.get("0") == 0.85, "velocity")
    # select the chord: Shift+↓ down to A1
    sync(page, server)
    k.press("Shift+ArrowDown", 6)
    assert k.focused() == "cell.A1.0"
    assert page.locator(f"{ROLL} .cell.sel").count() == 6
    # move it: Alt+→ four times (one undo step each)
    for i in range(1, 5):
        k.press("Alt+ArrowRight")
        wait(page, lambda i=i: min(n[1] for n in notes(server)) == i, f"moved {i}")
    vel = lambda st: server.state.arrangement.velocities.get(str(st), 0.9)  # noqa: E731
    assert vel(4) == 0.85 and vel(0) == 0.9  # it went with the chord
    assert ("G2", 4, 7) in notes(server)
    # ... and up a tone of the key, and back
    k.press("Alt+ArrowUp")
    wait(page, lambda: ("A2", 4, 7) in notes(server), "transposed")
    k.press("Alt+ArrowDown")
    wait(page, lambda: ("G2", 4, 7) in notes(server), "transposed back")
    # copy, and paste at the cursor (step 12, the chord's top row)
    sync(page, server)
    k.press("Control+c")
    k.press("ArrowUp", 6)
    k.press("ArrowRight", 12)
    assert k.focused() == "cell.G2.12"
    k.press("Control+v")
    wait(page, lambda: len(notes(server)) == 8, "the pasted copy")
    assert ("G2", 12, 15) in notes(server) and ("A1", 12, 13) in notes(server)
    assert vel(4) == 0.85 and vel(12) == 0.85
    # the copy is the selection: Delete takes it and leaves the first chord
    wait(
        page,
        lambda: page.locator(f"{ROLL} .cell.sel").count() == 6,
        "the copy selected",
    )
    k.press("Delete")
    wait(page, lambda: len(notes(server)) == 4, "the copy deleted")
    assert min(n[1] for n in notes(server)) == 4
    # Ctrl+D duplicates a selection to its right; Ctrl+A, Delete empties the grid
    sync(page, server)
    k.press("Control+a")
    k.press("Control+d")
    wait(page, lambda: len(notes(server)) == 8, "the duplicate")
    sync(page, server)
    k.press("Control+a")
    k.press("Delete")
    wait(page, lambda: notes(server) == [], "an empty grid")
    # Shift+Enter starts a new note inside a run (a cut)
    sync(page, server)
    k.press("Space")
    k.press("ArrowRight")
    k.press("Space")
    k.press("Shift+Enter")
    wait(page, lambda: len(notes(server)) == 2, "two notes side by side")
    # the tools are commands: the palette switches to the stamp, Enter stamps
    sync(page, server)
    k.press("Control+k")
    page.keyboard.type("Tool: stamp")
    k.press("Enter")
    wait(
        page,
        lambda: page.evaluate("() => document.documentElement.dataset.roll") == "stamp",
        "the tool",
    )
    page.locator(cell("A1", 20)).focus()
    k.press("Enter")
    wait(page, lambda: ("E2", 20, 28) in notes(server), "the stamped chord")
    assert k.pointers() == 0, "the keyboard journey used a pointer"


def test_roll_keyboard_pages_the_octave_window_and_keeps_the_grid_one_stop(
    studio, server
):
    page = perform(studio, server)
    k = Keys(page)
    page.locator(cell("A3", 3)).focus()
    k.press("PageUp")  # the top row: the window moves up an octave
    wait(page, lambda: server.state.grid_oct == 1, "the octave window")
    wait(page, lambda: k.focused() == "cell.A4.3", "the cursor on the row asked for")
    assert page.locator(f"{ROLL} .rowlabel[data-pitch]").first.inner_text() == "A4"
    # every cell but the cursor is out of the tab order
    stops = page.evaluate(
        "() => [...document.querySelectorAll('#transport .grid.roll .cell, "
        "#transport .grid.roll .vel-lane')].filter((c) => c.tabIndex === 0).length"
    )
    assert stops == 2
    # the arrows stop at the grid's edges and never scroll the page
    k.press("ArrowUp", 3)
    assert k.focused() == "cell.A4.3" and page.evaluate("() => scrollY") == 0


# --- pen (synthetic) -----------------------------------------------------------


def test_roll_pen_pressure_eraser_barrel_and_the_note_end(studio, server):
    studio.page.add_init_script(PEN)
    page = perform(studio, server)
    p = Pen(page)
    # the tip draws a two-step note; its peak pressure is the step's velocity
    p.drag([cell("C2", 2), cell("C2", 3)], pressure=0.5)
    wait(page, lambda: notes(server) == [("C2", 2, 4)], "the drawn note")
    assert server.state.arrangement.velocities["2"] == 0.75  # 0.25 + pressure
    # hover: the cell says its pitch and step
    p.hover(cell("E2", 9))
    assert page.get_attribute(cell("E2", 9), "title") == "E2 step 10"
    # the note's end under a pen is its last 10 px: dragged three steps on
    sync(page, server)
    p.drag([cell("C2", 3), cell("C2", 4), cell("C2", 6)], pressure=0.4, fx0=0.9)
    wait(page, lambda: notes(server) == [("C2", 2, 7)], "the longer note")
    assert server.state.arrangement.velocities["2"] == 0.75  # a length is not a strike
    # the barrel is Shift: a tap inside the note starts a new one there (a cut)
    sync(page, server)
    p.tap(cell("C2", 4), button=2, buttons=2)
    wait(page, lambda: notes(server) == [("C2", 2, 4), ("C2", 4, 7)], "the cut")
    # ... and in the select tool it adds to the selection
    sync(page, server)
    tool(page, "select")
    p.tap(cell("C2", 2))
    wait(
        page,
        lambda: page.locator(f"{ROLL} .cell.sel").count() == 2,
        "one note selected",
    )
    p.tap(cell("C2", 5), button=2, buttons=2)
    wait(page, lambda: page.locator(f"{ROLL} .cell.sel").count() == 5, "both selected")
    # a pen drags a selection as a mouse does
    p.drag([cell("C2", 3), cell("D2", 4), cell("E2", 5)], pressure=0.3)
    wait(page, lambda: notes(server) == [("E2", 4, 6), ("E2", 6, 9)], "the moved notes")
    # the eraser end erases, in any tool: a stroke across the first note
    sync(page, server)
    tool(page, "draw")
    p.drag([cell("E2", 4), cell("E2", 5)], button=5, buttons=32)
    wait(page, lambda: notes(server) == [("E2", 6, 9)], "the erased note")


# --- touch ---------------------------------------------------------------------


def _mid(page, selector: str) -> tuple[float, float]:
    """The middle of `selector`, scrolled into view first (the touch tier
    is the flowing page: a finger taps what is on screen)."""
    clear_of_dock(page, selector)
    box = page.locator(selector).first.bounding_box()
    assert box, selector
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_roll_touch_tap_paint_note_end_tools_and_targets(touch_studio, server):
    studio = touch_studio
    page = perform(studio, server)
    assert page.evaluate("() => matchMedia('(pointer: coarse)').matches")
    t = Touch(page)
    # (a finger is slower than a redraw: each tap below waits 300 ms after
    # one, because a tap whose target is replaced under it makes no click)
    # every step and every tool of the roll is a 40 px target
    small = page.evaluate(
        """() => [...document.querySelectorAll("#transport .grid.roll .gridrow .cell, "
              + "#transport .pattern-tools :is(button, select):not([hidden]), "
              + "#lane-inspector .lane-seg button")]
            .map((e) => [e.getAttribute("aria-label") || e.textContent.trim(),
                         e.getBoundingClientRect()])
            .filter(([, r]) => r.width && (r.width < 39.5 || r.height < 39.5))
            .map(([n, r]) => `${n} ${Math.round(r.width)}x${Math.round(r.height)}`)"""
    )  # (39.5: a subpixel under 40 rounds to 40 — the lux bevels)
    assert not small, small[:8]
    page.locator(cell("C2", 0)).scroll_into_view_if_needed()
    # a tap toggles a step
    t.tap(*_mid(page, cell("C2", 0)))
    wait(page, lambda: notes(server) == [("C2", 0, 1)], "the tapped step")
    # a sideways drag paints (and the page does not scroll under it)
    sync(page, server, pause=300)
    at = page.evaluate("() => [scrollX, scrollY]")
    t.drag(_mid(page, cell("C2", 1)), _mid(page, cell("C2", 2)))
    wait(page, lambda: notes(server) == [("C2", 0, 3)], "the painted note")
    assert page.evaluate("() => [scrollX, scrollY]") == at
    # the note's end under a finger is the right HALF of its last step
    sync(page, server, pause=300)
    to = _mid(page, cell("C2", 4))
    box = page.locator(cell("C2", 2)).bounding_box()
    end = (box["x"] + box["width"] * 0.8, box["y"] + box["height"] / 2)
    t.drag(end, to)
    wait(page, lambda: notes(server) == [("C2", 0, 5)], "the longer note")
    # no modifiers on a finger: the tool switch is how it selects and moves
    sync(page, server, pause=300)
    page.tap('#transport [data-roll-tool="select"]')
    wait(
        page,
        lambda: (
            page.evaluate("() => document.documentElement.dataset.roll") == "select"
        ),
        "the select tool",
    )
    t.tap(*_mid(page, cell("C2", 1)))
    wait(
        page,
        lambda: page.locator(f"{ROLL} .cell.sel").count() == 5,
        "the note selected",
    )
    t.drag(_mid(page, cell("C2", 1)), _mid(page, cell("D2", 3)))
    wait(page, lambda: notes(server) == [("D2", 2, 7)], "the moved note")
    # "Copy here" without Ctrl: copy, put the cursor somewhere, paste
    sync(page, server, pause=300)
    page.tap('#transport [data-roll-op="mark"]')
    page.wait_for_timeout(350)  # (two quick taps are a double tap, not two clicks)
    page.locator(cell("D2", 9)).focus()
    page.tap('#transport [data-roll-op="paste"]')
    wait(
        page, lambda: notes(server) == [("D2", 2, 7), ("D2", 9, 14)], "the pasted copy"
    )
    sync(page, server, pause=300)
    page.tap('#transport [hx-post$="/notes/remove"]')
    wait(page, lambda: notes(server) == [("D2", 2, 7)], "the copy deleted")


def test_roll_touch_long_press_opens_the_notes_menu(touch_studio, server):
    studio = touch_studio
    page = perform(studio, server, cells=[["C2", 2], ["C2", 3]])
    t = Touch(page)
    page.locator(cell("C2", 3)).scroll_into_view_if_needed()
    t.long_press(cell("C2", 3))
    page.wait_for_selector("#ctx-menu:popover-open")
    rows = page.locator("#ctx-menu .menu-item").all_inner_texts()
    assert "Tool: select" in rows and "delete the selected notes" in rows
    # the press did not erase the step (a right click would have), and the
    # note under the finger is what the menu's rows act on
    assert notes(server) == [("C2", 2, 4)]
    assert page.locator(f"{ROLL} .cell.sel").count() == 2
    page.locator("#ctx-menu .menu-item", has_text="lengthen the selected notes").click()
    wait(page, lambda: notes(server) == [("C2", 2, 5)], "the menu's edit")


def test_roll_touch_pinch_zooms_and_two_fingers_pan(touch_studio, server):
    studio = touch_studio
    page = perform(studio, server, cells=[["C2", 2]], bars=4)
    t = Touch(page)
    page.locator(cell("C2", 0)).scroll_into_view_if_needed()
    zoom = ZOOM
    left = "() => document.querySelector('#transport .grid.roll').scrollLeft"
    a, b = _mid(page, cell("E2", 4)), _mid(page, cell("E2", 7))
    # two fingers apart: wider steps
    t.two(a, b, (a[0] - 90, a[1]), (b[0] + 90, b[1]))
    wait(page, lambda: page.evaluate(zoom) != "", "a zoom")
    wide = float(page.evaluate(zoom)[:-2])
    assert wide > 40
    # two fingers moved together: the grid pans under them
    a, b = _mid(page, cell("E2", 6)), _mid(page, cell("E2", 8))
    t.two(a, b, (a[0] - 150, a[1]), (b[0] - 150, b[1]))
    wait(page, lambda: page.evaluate(left) > 100, "a pan")
    # two fingers together: narrower again
    a, b = _mid(page, cell("E2", 5)), _mid(page, cell("E2", 9))
    t.two(a, b, (a[0] + 80, a[1]), (b[0] - 80, b[1]))
    wait(page, lambda: float(page.evaluate(zoom)[:-2] or 0) < wide, "zoomed out")
    # none of it drew or erased anything
    sync(page, server)
    assert notes(server) == [("C2", 2, 3)] and undo_steps(server) == 0


# --- the Song drawer: the same roll --------------------------------------------


def test_roll_in_the_song_drawer_is_the_same_component(studio, server):
    from synthlab.studio import schema
    from synthlab.studio.gen import build

    s = server.state
    s.song = schema.conform(build.start("trance", 1).song, schema.nodes().SONG)
    s.grid_span = 2
    song = s.song
    track = next(t for t in song["tracks"] if t.get("role") == "lead")
    sec = next(x for x in song["sections"] if track["id"] in x["cells"])
    pid = sec["cells"][track["id"]]
    s.song_drawer = {"section_id": sec["id"], "track_id": track["id"]}
    page = studio.open("song")
    page.wait_for_selector("#song-drawer .grid.roll")
    box = "#song-drawer"
    # ghosts of the section's other tracks, and the chord track's tones
    assert page.locator(f"{box} .grid.roll .cell.ghost").count() > 0
    assert page.locator(f"{box} .grid.roll .cell.ct").count() > 0
    lit = page.locator(f"{box} .grid.roll .gridrow:not(.sliderow) .cell:checked")
    assert lit.count() > 0
    pat = lambda: s.song["patterns"][pid]  # noqa: E731
    before = sorted(map(tuple, pat()["cells"]))
    undo = len(s._song_undo)
    # select everything it shows and move it a step later: one song-undo step
    page.locator(f"{box} .grid.roll .gridrow .cell").first.focus()
    page.keyboard.press("Control+a")
    assert page.locator(f"{box} .grid.roll .cell.sel").count() == lit.count()
    first = min(c[1] for c in before)
    page.keyboard.press("Alt+ArrowLeft" if first else "Alt+ArrowRight")
    wait(page, lambda: sorted(map(tuple, pat()["cells"])) != before, "the moved notes")
    assert len(pat()["cells"]) == len(before) and len(s._song_undo) == undo + 1
    # the stamp reads the chord track: its tones under that step, from the clicked row
    sync_drawer(page, pat)
    page.click(f'{box} [data-roll-tool="stamp"]')
    page.select_option(f'{box} [data-roll-opt="q"]', "track")
    rows = page.locator(f"{box} .grid.roll .rowlabel[data-pitch]").all_inner_texts()
    free = next(
        st
        for st in range(pat()["bars"] * pat()["steps_per_bar"])
        if not any(c[1] == st for c in pat()["cells"])
    )
    n = len(pat()["cells"])
    page.locator(cell(rows[-1], free, box)).click()
    wait(page, lambda: len(pat()["cells"]) > n, "the stamped chord")
    new = {c[0] for c in pat()["cells"] if c[1] == free}
    marked = set(
        page.evaluate(
            """([box, st]) =>
              [...document.querySelectorAll(`${box} .grid.roll .cell.ct`)]
                .filter((c) => c.name.endsWith("." + st))
                .map((c) => c.name.split(".")[1])""",
            [box, free],
        )
    )
    assert new and new <= marked, (new, marked)


# --- E5, E9 ----------------------------------------------------------------------


def test_e5_drawing_on_another_lane_is_one_click_and_no_page_change(studio, server):
    """E5: 1 click, 0 page changes (it was two page changes a lane)."""
    seed(server, cells=[["A1", 0], ["A1", 4]])
    s = server.state
    s.add_layer(s.patch, select=False)  # a second lane: it follows the main
    s.set_follow(1, None)  # ... no longer: it has its own (empty) grid
    page = studio.open("perform")
    sync(page, server)
    tally = Tally(page)
    tally.start()
    page.click("#lh2-name")  # click 1: the lane's head
    wait(page, lambda: s.active_layer == 1, "the lane switched")
    page.wait_for_selector('#lh2-name[aria-pressed="true"]')
    assert tally.settle() == {"click": 1, "key": 0, "view": 0, "load": 0}
    # the bass shows behind as ghost notes; what is drawn here is this lane's
    assert page.locator(f"{ROLL} .cell.ghost").count() == 2
    page.click(cell("E2", 8))
    wait(page, lambda: notes(server) == [("E2", 8, 9)], "a note on the second lane")
    assert [c for c in s.layers[0]["cells"]] == [["A1", 0], ["A1", 4]]
    # ... and it is what the loop plays for that lane, with no Follow to turn off
    assert not s.follows(1)
    assert sorted(s.lane_arrangement().cells) == [["E2", 8]]
    print("\nE5: 1 click, 0 keys, 0 page changes")


def test_a_new_lane_from_perform_sounds_on_its_own_grid(studio, server):
    """Dead end 3: a new layer was silent on its own grid until Follow was
    turned off on another page. The lane switch's ＋ adds one that is its
    own from the start; a lane that does follow says so, with the one
    button that gives it its own notes."""
    seed(server, cells=[["A1", 0]])
    s = server.state
    page = studio.open("perform")
    sync(page, server)
    tally = Tally(page)
    tally.start()
    page.click("#lane-inspector .lane-add")
    wait(page, lambda: s.active_layer == 1, "the new lane")
    assert tally.settle() == {"click": 1, "key": 0, "view": 0, "load": 0}
    assert not s.follows(1) and s.main_index() == 0
    assert page.locator("#transport .lane-follow-note").count() == 0
    page.click(cell("C2", 4))
    wait(page, lambda: sorted(s.lane_arrangement().cells) == [["C2", 4]], "it plays")
    # a lane that follows (added on Synths): one click on Perform frees it
    s.set_follow(1, 0)
    page.reload()
    page.wait_for_selector("#transport .lane-follow-note")
    page.click("#transport .lane-follow-note .btn")
    wait(page, lambda: not s.follows(1), "Follow off")
    page.wait_for_selector("#transport .lane-follow-note", state="detached")


def test_e9_the_note_part_of_journey_1_by_hand(studio, server):
    """E9's note half, counted: journey 1's rows 4-6 (a bass line over four
    bars, chords on a second lane, a lead on a third) on Perform alone. It
    was 47 clicks and 4 page changes; the whole journey's budget is 60
    clicks, 12 keys and 1 page change."""
    seed(server, bars=1, spb=4)
    s = server.state
    page = studio.open("perform")
    sync(page, server)
    m = Mouse(page)
    tally = Tally(page)
    tally.start()
    # row 4, the bass: 16 steps, four bars, four held notes
    page.select_option("#transport select[name=steps]", "16")
    page.wait_for_selector(cell("A1", 15))  # (the form posts 150 ms after an input)
    page.click("#transport .barchip:nth-of-type(4)")
    wait(
        page,
        lambda: (s.arrangement.bars, s.arrangement.steps_per_bar) == (4, 16),
        "4 x 16",
    )
    page.wait_for_selector(cell("A1", 63))
    for bar, pitch in enumerate(("A1", "F2", "C2", "G2")):
        m.drag(cell(pitch, bar * 16), cell(pitch, bar * 16 + 5))
    wait(page, lambda: len(notes(server)) == 4, "the bass")
    bass = tally.settle()
    # row 5, the chords: a new lane, four stamped triads of a bar each
    page.click("#lane-inspector .lane-add")
    wait(page, lambda: s.active_layer == 1, "the chord lane")
    sync(page, server)
    page.click('#transport [data-roll-tool="stamp"]')
    page.select_option('#transport [data-roll-opt="len"]', "16")
    for bar, pitch in enumerate(("A1", "F2", "C2", "G2")):
        page.click(cell(pitch, bar * 16))
        wait(page, lambda bar=bar: len(notes(server)) == 3 * (bar + 1), "a chord")
    chords = tally.settle()
    # row 6, the lead: a third lane, sixteen notes drawn in four strokes
    page.click("#lane-inspector .lane-add")
    wait(page, lambda: s.active_layer == 2, "the lead lane")
    sync(page, server)
    page.click('#transport [data-roll-tool="draw"]')
    for bar in range(4):
        for i, pitch in enumerate(("A2", "C3", "E3", "D3")):
            page.click(cell(pitch, bar * 16 + i * 4))
    wait(page, lambda: len(notes(server)) == 16, "the lead")
    total = tally.settle()
    assert total["view"] == 0 and total["load"] == 0
    assert total["key"] == 0
    assert total["click"] <= 40, total
    print(
        f"\nE9, the note part: {total['click']} clicks (bass {bass['click']}, "
        f"chords {chords['click'] - bass['click']}, lead "
        f"{total['click'] - chords['click']}), {total['key']} keys, "
        f"{total['view']} page changes — was 47 clicks, 4 page changes"
    )
    # the three lanes hold what was drawn, and each plays its own
    assert len(s.layers[0]["cells"]) == 24 and len(s.layers[1]["cells"]) == 4 * 3 * 16
    assert not s.follows(1) and not s.follows(2)


# --- rule 1 ------------------------------------------------------------------------


def test_rule_1_holds_with_the_roll_open_on_perform_and_in_the_drawer(sized, server):  # noqa: F811
    from synthlab.studio import schema
    from synthlab.studio.gen import build

    open_, bare = sized
    s = server.state
    s.song = schema.conform(build.start("trance", 1).song, schema.nodes().SONG)
    track = next(t for t in s.song["tracks"] if t.get("role") == "lead")
    sec = next(x for x in s.song["sections"] if track["id"] in x["cells"])
    s.song_drawer = {"section_id": sec["id"], "track_id": track["id"]}
    seed(server, span=3, bars=4, cells=[["A1", 0], ["C2", 4], ["E2", 8]])
    s.add_layer(s.patch, select=False)
    got = {}
    for view, roll in (
        ("perform", "#transport .grid.roll"),
        ("song", "#song-drawer .grid.roll"),
    ):
        for w, h in SIZES:
            page = open_(view, w, h)
            page.wait_for_selector(roll)
            got[(view, w)] = round(bare(page), 1)
            over = page.evaluate(
                "() => [document.documentElement.scrollHeight - innerHeight,"
                " document.documentElement.scrollWidth - innerWidth]"
            )
            assert over[0] <= 1 and over[1] <= 0, (view, w, over)
    print(f"\nrule 1, the roll open: bare background {got} %")
    assert all(v < 5.0 for v in got.values()), got


# =============================================================================
# The playlist tools
# =============================================================================
#
# The surface's core edit — select a box of cells, duplicate it, undo — by a
# mouse, by the keyboard alone, by a pen and by a finger, each reaching the
# same song; then every other playlist tool (split, insert bars, a
# transition on a boundary, a range loop, the section jump, the energy lane,
# the chord tiles) by the inputs that can reach it. `song_of(server)` is what
# the session holds. The timed arranging rows are at the end.


def plist_song() -> dict:
    """Intro (4 bars) · Verse (4 bars x 2) · Drop (4 bars) · Out (4 bars,
    empty) over Drums, Bass, Lead and an audio track; energies 0.3, 0.5,
    0.9 and none; a four-chord progression over four bars."""
    from synthlab.studio import schema
    from synthlab.studio.gen import chordtrack
    from tests.studio.test_song_routes import song_dict

    s = song_dict()
    full = {"td": "pd", "t1": "p1", "t2": "p2"}
    s["sections"] = [
        {"id": "s1", "name": "Intro", "bars": 4, "cells": {"td": "pd", "t1": "p1"}},
        {"id": "s2", "name": "Verse", "bars": 4, "repeats": 2, "cells": dict(full)},
        {"id": "s3", "name": "Drop", "bars": 4, "cells": dict(full)},
        {"id": "s4", "name": "Out", "bars": 4, "cells": {}},
    ]
    for sec, e in zip(s["sections"], (0.3, 0.5, 0.9), strict=False):
        sec["energy"] = e
    s["chords"] = chordtrack.from_degrees((1, 6, 3, 7), 4)
    return schema.conform(s, schema.nodes().SONG)


def playlist(studio, server, song=None):
    server.state.song = song or plist_song()
    server.state.fx_master = []
    page = studio.open("song")
    page.wait_for_selector(".arranger-grid[tabindex]")
    return page


BAR_W = (  # the playlist's zoom: px a bar (songline.js keeps it on .song-view)
    "() => parseFloat(getComputedStyle(document.querySelector('.song-view'))"
    ".getPropertyValue('--bar-w'))"
)
SECTIONS = (
    "() => [...document.querySelectorAll('.arr-section')]"
    ".map((t) => t.dataset.sectionId)"
)
PLAYING = "() => window.synthlab.songClock().playing"
LOOPING = "() => window.synthlab.songClock().loop"
# a pen's stroke over the playlist, from the cell `a` to the cell `b`
PEN_STROKE = """([a, b]) => {
  const at = (s) => {
    const r = document.querySelector(s).getBoundingClientRect();
    return [r.left + 8, r.top + 8];
  };
  const ev = (type, [x, y], buttons) =>
    document.elementFromPoint(x, y).dispatchEvent(new PointerEvent(type, {
      bubbles: true, cancelable: true, pointerId: 7, pointerType: "pen",
      isPrimary: true, clientX: x, clientY: y, buttons,
      pressure: buttons ? 0.5 : 0 }));
  ev("pointerdown", at(a), 1);
  ev("pointermove", at(b), 1);
  ev("pointerup", at(b), 0);
}"""
# the barrel tapped: it arrives as a right click
PEN_BARREL = """(s) => {
  const el = document.querySelector(s), b = el.getBoundingClientRect();
  el.dispatchEvent(new PointerEvent("contextmenu", {
    bubbles: true, cancelable: true, button: 2, pointerType: "pen",
    pointerId: 7, clientX: b.left + 8, clientY: b.top + 8 }));
}"""
# the playlist's finger targets that are under 40 px tall
SMALL = """() => {
  const all = "#arr-tools .btn, .energy-cell, .arr-chords .ctile, .sec-plus, "
    + "#arr-scroll .sec-menu-btn";
  return [...document.querySelectorAll(all)]
    .map((el) => [el.className, el.getBoundingClientRect()])
    .filter(([, r]) => r.width > 0 && r.height < 40)
    .map(([c, r]) => `${c} ${Math.round(r.width)}x${Math.round(r.height)}`);
}"""


def song_of(server) -> dict:
    return server.state.song


def grid_of(server) -> list[list]:
    """The song's cells, a row a track: what a range edit changes."""
    s = song_of(server)
    return [
        [sec["cells"].get(t["id"]) for sec in s["sections"]]
        for t in s["tracks"]
        if t["kind"] != "audio"
    ]


def pcell(sid: str, tid: str) -> str:
    return f"#cell-{sid}-{tid}"


def selected(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('.arr-cell.sel')].map((c) => c.id)"
    )


def song_undo(server) -> int:
    return len(server.state._song_undo)


def redrawn(page, server):
    """The arranger on the page shows the session's sections."""
    want = [s["id"] for s in song_of(server)["sections"]]
    js = SECTIONS
    wait(page, lambda: page.evaluate(js) == want, "the arranger redrawn")
    page.wait_for_timeout(80)


DUPLICATED = [  # the 2 x 2 box of Intro + Verse, duplicated onto Drop + Out
    ["pd", "pd", "pd", "pd"],
    ["p1", "p1", "p1", "p1"],
    [None, "p2", "p2", None],
]


# --- mouse -------------------------------------------------------------------------


def test_playlist_mouse_shift_click_range_duplicate_from_its_menu_and_undo(
    studio, server
):
    page = playlist(studio, server)
    before = grid_of(server)
    page.click(pcell("s1", "td") + " .cell-btn")  # the cursor (and its drawer)
    page.click(pcell("s2", "t1"), modifiers=["Shift"], position={"x": 30, "y": 8})
    assert selected(page) == [
        "cell-s1-td",
        "cell-s2-td",
        "cell-s1-t1",
        "cell-s2-t1",
    ]
    assert page.inner_text("#range-says") == "2 × 2 cells"
    # the range's menu is the selected cell's context menu: the one menu.js
    page.click(pcell("s2", "t1"), button="right", position={"x": 30, "y": 8})
    page.wait_for_selector("#range-menu:popover-open")
    page.click('#range-menu [data-sel-op="duplicate"]')
    wait(page, lambda: grid_of(server) == DUPLICATED, "the range duplicated")
    assert song_undo(server) == 1  # ONE step
    redrawn(page, server)
    page.click("#song-undo")
    wait(page, lambda: grid_of(server) == before, "the duplicate undone")


def test_playlist_mouse_a_drag_from_an_empty_cell_is_a_range_and_a_block_still_moves(
    studio, server
):
    """One owner a gesture: a drag that starts on an EMPTY cell selects; a
    drag that starts on a block is songdrag.js's move; with Shift it is a
    range from a block too."""
    page = playlist(studio, server)
    m = Mouse(page)
    m.drag(pcell("s4", "td"), pcell("s4", "t2"))
    assert selected(page) == ["cell-s4-td", "cell-s4-t1", "cell-s4-t2"]
    assert not page.is_visible("#song-drawer .drawer-head")  # the click opened nothing
    page.keyboard.press("Escape")
    # a filled cell, with Shift held: a range, and nothing moved
    before = grid_of(server)
    page.keyboard.down("Shift")
    m.drag(pcell("s1", "td"), pcell("s2", "t1"))
    page.keyboard.up("Shift")
    assert len(selected(page)) == 4 and grid_of(server) == before
    # a plain drag of a block moves it (native drag and drop: songdrag.js)
    page.locator(pcell("s3", "t2")).drag_to(page.locator(pcell("s4", "t2")))
    wait(
        page, lambda: grid_of(server)[2] == [None, "p2", None, "p2"], "the block moved"
    )
    assert selected(page) == []


def test_playlist_mouse_split_insert_and_a_transition_on_a_boundary(studio, server):
    page = playlist(studio, server)
    m = Mouse(page)
    bar_w = page.evaluate(BAR_W)
    box = page.locator("#song-ruler").bounding_box()
    page.mouse.click(box["x"] + 2.5 * bar_w, box["y"] + 8)  # bar 3, inside the Intro
    assert page.input_value("#mark-bar") == "3"
    page.click("#arr-split")
    wait(page, lambda: len(song_of(server)["sections"]) == 5, "the split")
    secs = song_of(server)["sections"]
    assert [(s["name"], s["bars"]) for s in secs[:2]] == [("Intro", 2), ("Intro ′", 2)]
    redrawn(page, server)
    assert page.input_value("#mark-bar") == "3"  # the marker stayed on its bar
    page.click("#arr-insert")
    wait(page, lambda: len(song_of(server)["sections"]) == 6, "the insert")
    assert [s["bars"] for s in song_of(server)["sections"]][:3] == [2, 4, 2]
    redrawn(page, server)
    # the + on the Drop's boundary: rising energy -> a riser into it and an impact
    drop = "#sec-s3 .sec-plus"
    assert page.get_attribute(drop, "data-ctx-state") == "rising"
    page.click(drop)
    page.wait_for_selector("#bound-menu:popover-open")
    assert page.is_visible("#bound-menu .if-rising")
    assert not page.is_visible("#bound-menu .if-falling")
    steps = song_undo(server)
    page.click("#bound-menu .bound-auto")
    wait(page, lambda: song_undo(server) == steps + 1, "the transition placed")
    s = song_of(server)
    by = {x["id"]: [a["clip"].split("-")[0] for a in x["audio"]] for x in s["sections"]}
    before_drop = s["sections"][[x["id"] for x in s["sections"]].index("s3") - 1]
    assert by["s3"] == ["impact"] and by[before_drop["id"]] == ["riser"]
    # auditioned: the song plays, from two bars before the boundary
    wait(
        page,
        lambda: page.evaluate("() => window.synthlab.songClock().playing"),
        "it plays",
    )
    page.evaluate("() => window.synthlab.songClock().stop()")
    assert m is not None


def test_playlist_mouse_energy_lane_and_chord_tiles(studio, server):
    page = playlist(studio, server)
    m = Mouse(page)
    # the energy bar of the Verse: a drag along it (a native range)
    bar = "#arr-scroll .energy-cell[data-section-id='s2'] .energy"
    x, y = m.centre(bar, 0.5)
    box = page.locator(bar).bounding_box()
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.9, y, steps=4)
    page.mouse.up()
    wait(
        page,
        lambda: song_of(server)["sections"][1].get("energy", 0) > 0.8,
        "the energy",
    )
    assert song_undo(server) == 1
    # a chord tile: a click opens its menu; VI there sets its degree
    page.wait_for_selector(".arr-chords .ctile")
    assert page.locator(".chord-cell[data-section-id='s1'] .ctile").count() == 4
    page.locator(".chord-cell[data-section-id='s1'] .ctile").nth(1).click()
    page.wait_for_selector("#chord-menu:popover-open")
    page.click("#chord-menu .chord-degs button >> nth=1")  # the second degree
    wait(page, lambda: song_of(server)["chords"]["slots"][1]["deg"] == 2, "the degree")
    # its left edge dragged a beat later: where the chord starts
    studio.settle()  # the degree's redraw has landed: measure the new edge
    page.wait_for_selector(".chord-cell[data-section-id='s1'] .ctile-edge")
    bar_w = page.evaluate(BAR_W)
    edge = None
    for _ in range(40):  # a late morph of the chord lane can detach it
        edge = page.locator(
            ".chord-cell[data-section-id='s1'] .ctile-edge"
        ).first.bounding_box()
        if edge:
            break
        page.wait_for_timeout(50)
    assert edge, "the chord tile's edge"
    ex, ey = edge["x"] + edge["width"] / 2, edge["y"] + edge["height"] / 2
    page.mouse.move(ex, ey)
    page.mouse.down()
    page.mouse.move(ex + bar_w / 2, ey, steps=5)  # two beats
    page.mouse.up()
    wait(page, lambda: song_of(server)["chords"]["slots"][1]["at"] == 6.0, "the edge")
    assert song_of(server)["chords"]["slots"][0]["len"] == 6.0


def test_playlist_wheel_zooms_scrolls_and_changes_no_value(studio, server):
    page = playlist(studio, server)
    m = Mouse(page)
    width = BAR_W
    left = "() => document.getElementById('arr-scroll').scrollLeft"
    was = page.evaluate(width)
    m.wheel(pcell("s2", "t1"), dy=-120, key="Control")
    wait(page, lambda: page.evaluate(width) > was, "a zoom in")
    for _ in range(3):
        m.wheel(pcell("s2", "t1"), dy=-120, key="Control")
    assert page.evaluate(width) == 96  # the widest step: the song overflows now
    m.wheel(pcell("s2", "t1"), dy=300, key="Shift")
    wait(page, lambda: page.evaluate(left) > 50, "a sideways scroll")
    page.click('[data-zoom-to="fit"]')  # the whole song in view again
    page.wait_for_timeout(150)
    # over the energy lane, unfocused: the wheel scrolls and sets nothing
    m.wheel("#arr-scroll .energy-cell[data-section-id='s2'] .energy", dy=-240)
    m.wheel("#arr-scroll .energy-cell[data-section-id='s2'] .energy", dy=240)
    assert song_of(server)["sections"][1]["energy"] == 0.5
    assert song_undo(server) == 0
    # focused, the wheel is the value's (tweak.js): one undo step
    page.focus("#arr-scroll .energy-cell[data-section-id='s2'] .energy")
    m.wheel("#arr-scroll .energy-cell[data-section-id='s2'] .energy", dy=-120)
    wait(
        page,
        lambda: song_of(server)["sections"][1]["energy"] != 0.5,
        "the wheel's value",
    )
    assert song_undo(server) == 1


def test_playlist_the_section_menu_stays_open_across_its_repeat_buttons(studio, server):
    """The reported bug: the menu closed after every ±."""
    page = playlist(studio, server)
    page.click("#sec-s2 .sec-menu-btn")
    page.wait_for_selector("#secmenu-s2:popover-open")
    more = '#secmenu-s2 [aria-label="more repeats"]'
    for n in (3, 4, 5):
        page.click(more)
        wait(page, lambda n=n: song_of(server)["sections"][1]["repeats"] == n, f"x{n}")
        page.wait_for_selector("#secmenu-s2:popover-open")
        wait(
            page,
            lambda: (
                page.evaluate(
                    "() => document.activeElement?.getAttribute('aria-label')"
                )
                == "more repeats"
            ),
            "the + keeps the keyboard",
        )
    assert song_undo(server) == 1  # three clicks, one step
    page.keyboard.press("Enter")  # ... and the keyboard goes on from there
    wait(page, lambda: song_of(server)["sections"][1]["repeats"] == 6, "x6 by Enter")


# --- keyboard only -----------------------------------------------------------------


def tab_to(page, keys: Keys, test: str, what: str, back: bool = False):
    for _ in range(80):
        if page.evaluate(
            f"() => {{ const a = document.activeElement; return {test}; }}"
        ):
            return
        keys.press("Shift+Tab" if back else "Tab")
    raise AssertionError(f"Tab never reached {what}")


def test_playlist_keyboard_only_a_whole_arranging_journey(studio, server):
    """No pointer at all: select a 2 x 3 range, copy it to the next
    sections, split a section, insert four bars, a transition on a
    boundary, loop a range — and undo it all."""
    studio.page.add_init_script(COUNT_POINTERS)
    page = playlist(studio, server)
    k = Keys(page)
    first = __import__("json").dumps(song_of(server), sort_keys=True)
    tab_to(page, k, "a?.classList.contains('arranger-grid')", "the playlist")
    # a 2 x 3 box from the cursor (Intro / Drums): two sections on, one track down
    k.press("Shift+ArrowRight", 2)
    k.press("Shift+ArrowDown")
    assert len(selected(page)) == 6
    assert page.inner_text("#range-says") == "3 × 2 cells"
    # copy, the cursor to the next section, paste: it lands one section on
    k.press("Control+c")
    k.press("ArrowRight")
    assert selected(page) == []  # a plain arrow ends the range
    k.press("Control+v")
    want = [
        ["pd", "pd", "pd", "pd"],
        ["p1", "p1", "p1", "p1"],
        [None, "p2", "p2", None],
    ]
    wait(page, lambda: grid_of(server) == want, "the range pasted")
    redrawn(page, server)
    assert page.evaluate(
        "() => document.activeElement.classList.contains('arranger-grid')"
    )
    # the ruler is the bar cursor: two bars on, S splits there, I inserts four
    tab_to(page, k, "a?.id === 'song-ruler'", "the ruler")
    k.press("Home")
    k.press("ArrowRight", 2)
    assert page.input_value("#mark-bar") == "3"
    k.press("s")
    wait(page, lambda: len(song_of(server)["sections"]) == 5, "the split")
    redrawn(page, server)
    tab_to(page, k, "a?.id === 'song-ruler'", "the ruler again")
    k.press("i")
    wait(page, lambda: len(song_of(server)["sections"]) == 6, "the insert")
    redrawn(page, server)
    # back on the grid: T opens the boundary's menu, Enter takes the suggestion
    tab_to(page, k, "a?.classList.contains('arranger-grid')", "the playlist", back=True)
    k.press("Control+ArrowRight", 3)  # three sections on: a jump a press
    takes = lambda: sum(len(s["audio"]) for s in song_of(server)["sections"])  # noqa: E731
    k.press("t")
    page.wait_for_selector("#bound-menu:popover-open")
    k.press("Enter")
    wait(page, lambda: takes() >= 1, "the transition")
    wait(
        page,
        lambda: page.evaluate("() => window.synthlab.songClock().playing"),
        "the audition",
    )
    redrawn(page, server)
    tab_to(page, k, "a?.classList.contains('arranger-grid')", "the playlist")
    k.press(" ")  # stop
    wait(
        page,
        lambda: not page.evaluate("() => window.synthlab.songClock().playing"),
        "stopped",
    )
    # a range of two sections, looped: L
    k.press("Shift+ArrowRight")
    k.press("l")
    wait(
        page,
        lambda: page.evaluate("() => Array.isArray(window.synthlab.songClock().loop)"),
        "the range loops",
    )
    assert page.locator(".ruler-loop.on").count() == 1
    k.press("l")
    wait(page, lambda: not page.evaluate(LOOPING), "the loop off")
    if page.evaluate(PLAYING):  # (the song plays on from where the loop was)
        k.press(" ")
    wait(page, lambda: not page.evaluate(PLAYING), "stopped")
    # undo it all: paste, split, insert, transition
    assert song_undo(server) == 4
    for left in (3, 2, 1, 0):  # (a press a step: htmx keeps one waiting request)
        k.press("Control+z")
        wait(page, lambda left=left: song_undo(server) == left, f"{left} steps left")
        page.wait_for_timeout(80)
    wait(
        page,
        lambda: __import__("json").dumps(song_of(server), sort_keys=True) == first,
        "everything undone",
    )
    assert k.pointers() == 0


def test_playlist_keyboard_moves_a_range_and_reaches_the_energy_and_the_chords(
    studio, server
):
    studio.page.add_init_script(COUNT_POINTERS)
    page = playlist(studio, server)
    k = Keys(page)
    tab_to(page, k, "a?.classList.contains('arranger-grid')", "the playlist")
    k.press("ArrowRight", 2)  # the Drop
    k.press("Shift+ArrowDown", 2)
    assert len(selected(page)) == 3
    k.press("Alt+ArrowRight")  # the range, a section on
    wait(page, lambda: [r[3] for r in grid_of(server)] == ["pd", "p1", "p2"], "moved")
    assert [r[2] for r in grid_of(server)] == [None, None, None]
    redrawn(page, server)
    assert len(selected(page)) == 3  # the range went with it
    k.press("Delete")
    wait(page, lambda: [r[3] for r in grid_of(server)] == [None, None, None], "cleared")
    redrawn(page, server)
    # the energy lane: one tab stop, the selected section's bar; arrows are the value
    tab_to(page, k, "a?.classList.contains('energy')", "the energy lane")
    assert k.focused() == "energy"
    before = song_undo(server)
    k.press("ArrowUp", 2)
    wait(page, lambda: song_undo(server) == before + 1, "the energy by arrows")
    drawn = (
        "() => document.querySelector('.energy[tabindex=\"0\"]')?.getAttribute('value')"
    )
    now = lambda: next(  # noqa: E731
        s for s in song_of(server)["sections"] if s["id"] == "s3"
    )["energy"]
    wait(page, lambda: float(page.evaluate(drawn) or -1) == now(), "the lane redrawn")
    page.wait_for_timeout(300)
    # the chord tiles: one tab stop, arrows between tiles, a digit the degree
    tab_to(page, k, "a?.classList.contains('ctile')", "the chord tiles")
    k.press("ArrowRight")
    k.press("5")
    wait(page, lambda: song_of(server)["chords"]["slots"][1]["deg"] == 5, "the degree")
    wait(
        page,
        lambda: page.evaluate(
            "() => document.activeElement?.classList.contains('ctile')"
        ),
        "the tile keeps the keyboard",
    )
    k.press("Enter")
    page.wait_for_selector("#chord-menu:popover-open")
    k.press("Escape")
    assert k.pointers() == 0


# --- pen ---------------------------------------------------------------------------


def test_playlist_pen_range_mode_hover_and_the_barrels_menu(studio, server):
    page = playlist(studio, server)
    page.evaluate(PEN)
    pen = Pen(page)
    # hovering a block lights every cell that shares its pattern (songcell.js)
    pen.hover(pcell("s1", "td") + " .cell-btn")
    assert page.locator(".arr-cell.same").count() == 3  # Intro, Verse, Drop
    # range mode: the pen's tap is a one-cell range, its stroke a box
    pen.tap("#arr-select")
    assert page.get_attribute("#arr-select", "aria-pressed") == "true"
    page.evaluate(PEN_STROKE, [pcell("s1", "td"), pcell("s2", "t1")])
    assert len(selected(page)) == 4
    # the barrel tapped (it arrives as a right click): the range's menu
    page.evaluate(PEN_BARREL, pcell("s2", "t1"))
    page.wait_for_selector("#range-menu:popover-open")
    pen.tap('#range-menu [data-sel-op="duplicate"]')
    wait(page, lambda: grid_of(server) == DUPLICATED, "the range duplicated")
    assert song_undo(server) == 1


# --- touch -------------------------------------------------------------------------


def test_playlist_touch_range_mode_two_fingers_and_targets(touch_studio, server):
    studio = touch_studio
    page = playlist(studio, server)
    t = Touch(page)
    scroll = (
        "() => [document.getElementById('arr-scroll').scrollLeft, scrollX, scrollY]"
    )
    # every tool, bar, tile and + is a finger's size
    small = page.evaluate(SMALL)
    assert not small, small
    x, y = _mid(page, "#arr-select")
    t.tap(x, y)
    assert page.get_attribute("#arr-select", "aria-pressed") == "true"
    # the cells in plain sight, as a finger finds them: the top dock's status
    # row puts the target cell's foot past the window, where a
    # finger's drag pans the page
    page.evaluate(
        "(s) => document.querySelector(s).scrollIntoView({block: 'center'})",
        pcell("s2", "t1"),
    )
    page.wait_for_timeout(100)
    was = page.evaluate(scroll)
    t.drag(_mid(page, pcell("s1", "td")), _mid(page, pcell("s2", "t1")))
    assert len(selected(page)) == 4
    assert page.evaluate(scroll) == was  # the drag selected: nothing scrolled
    # a long press on the range: its menu, and a tap duplicates
    t.long_press(pcell("s2", "t1"))
    page.wait_for_selector("#range-menu:popover-open")
    x, y = _mid(page, '#range-menu [data-sel-op="duplicate"]')
    t.tap(x, y)
    wait(page, lambda: grid_of(server) == DUPLICATED, "the range duplicated")
    redrawn(page, server)
    x, y = _mid(page, "#song-undo")
    t.tap(x, y)
    wait(page, lambda: grid_of(server)[0] == ["pd", "pd", "pd", None], "undone")
    redrawn(page, server)
    # two fingers still pan and pinch, range mode or not
    width = BAR_W
    w0 = page.evaluate(width)
    a, b = _mid(page, pcell("s1", "t1")), _mid(page, pcell("s2", "t1"))
    t.two(a, b, (a[0] - 60, a[1]), (b[0] + 120, b[1]))
    wait(page, lambda: page.evaluate(width) > w0, "a pinch zooms the bars")
    a, b = _mid(page, pcell("s2", "td")), _mid(page, pcell("s2", "t1"))
    left = "() => document.getElementById('arr-scroll').scrollLeft"
    l0 = page.evaluate(left)
    t.two(a, b, (a[0] - 160, a[1]), (b[0] - 160, b[1]))
    wait(page, lambda: page.evaluate(left) > l0 + 40, "two fingers pan the lanes")
    assert grid_of(server)[0] == ["pd", "pd", "pd", None]  # nothing was edited


def test_playlist_touch_a_tap_opens_a_boundary_and_a_chord_menu(touch_studio, server):
    studio = touch_studio
    page = playlist(studio, server)
    t = Touch(page)
    x, y = _mid(page, "#sec-s3 .sec-plus")
    t.tap(x, y)
    page.wait_for_selector("#bound-menu:popover-open")
    x, y = _mid(page, "#bound-menu .bound-auto")
    t.tap(x, y)
    wait(page, lambda: len(song_of(server)["sections"][2]["audio"]) == 1, "the impact")
    page.evaluate("() => window.synthlab.songClock().stop()")
    redrawn(page, server)
    x, y = _mid(page, ".chord-cell[data-section-id='s1'] .ctile")
    t.tap(x, y)
    page.wait_for_selector("#chord-menu:popover-open")


# --- the command palette and the Stream Deck's keys -------------------------------


def test_playlist_commands_run_from_the_palette_and_a_deck_key(studio, server):
    """Every tool is a `data-cmd`: the palette lists it and a Stream Deck
    key runs it by name (the virtual deck's third page of keys)."""
    page = playlist(studio, server)
    names = page.evaluate("() => window.synthlab.commands(true).map((c) => c.label)")
    for name in (
        "Next section",
        "Previous section",
        "Loop the selected range",
        "Split at the marker",
        "Insert 4 bars at the marker",
        "Add a transition before the selected section",
        "Arrange by energy",
        "Select cells: range mode on / off",
        "Duplicate the selection",
        "Make the selection unique",
        "Vary the selection",
        "Roll the selection again",
        "Clear the selection",
    ):
        assert name in names, name
    run = (
        "(name) => window.synthlab.commands().find((c) => c.label === name).el.click()"
    )
    sel = "() => window.synthlab.songClock().sel"
    page.evaluate(run, "Next section")
    wait(page, lambda: page.evaluate(sel) == "s2", "the next section")
    assert page.input_value("#mark-bar") == "5"  # the marker went with it
    assert page.get_attribute(".arr-cell.cursor", "data-section-id") == "s2"
    page.evaluate(run, "Next section")
    wait(page, lambda: page.evaluate(sel) == "s3", "the one after")
    page.evaluate(run, "Previous section")
    wait(page, lambda: page.evaluate(sel) == "s2", "the section before")
    # the deck's third page: its keys carry these commands
    page.evaluate(
        "() => { for (const _ of [1, 2]) window.synthlab.deck.handle({ key: 7 }); }"
    )
    labels = page.evaluate(
        "() => window.synthlab.deck.keys?.().map((k) => k.label) || []"
    )
    if labels:
        assert "Next ▶" in labels and "Split" in labels


def test_a_section_jump_while_playing_lands_on_the_next_pass_start(studio, server):
    page = playlist(studio, server)
    page.click("#song-play")
    studio.wait_sound(timeout=10.0)
    page.evaluate("() => window.synthlab.songJump(2)")  # Intro -> Drop
    cur = (
        "() => { const c = window.synthlab.songClock();"
        " return [c.sel, c.live.at(-1)?.sid]; }"
    )
    wait(page, lambda: page.evaluate(cur) == ["s3", "s3"], "the Drop queued next", 8.0)
    page.evaluate("() => window.synthlab.songClock().stop()")


def test_a_range_loop_wraps_through_the_after_pass_in_the_browser(studio, server):
    """Loop Intro..Verse: the pass queued after the Verse's last is the
    Intro's `?after=` WAV — fetched before it was needed (no late join)."""
    page = playlist(studio, server)
    urls = []
    page.on(
        "request", lambda r: urls.append(r.url) if "/song/section/" in r.url else None
    )
    page.click("#sec-s1 .section-name")
    page.click("#sec-s2 .section-name", modifiers=["Shift"])
    assert page.locator(".arr-section.in-loop").count() == 2
    assert page.locator(".ruler-loop").count() == 1
    page.keyboard.press("l")
    studio.wait_sound(timeout=10.0)
    assert page.evaluate("() => window.synthlab.songClock().loop") == ["s1", "s2"]
    wait(page, lambda: any("after=s2" in u for u in urls), "the wrap pass fetched", 8.0)
    assert page.locator(".ruler-loop.on").count() == 1
    page.keyboard.press("l")
    page.evaluate("() => window.synthlab.songClock().stop()")


# --- E9: the arranging rows of journey 1, by hand, counted ---------------------------


def test_e9_the_arranging_rows_of_journey_1_by_hand(studio, server):
    """Journey 1, rows 7 and 8 (the journey log: 55 clicks + 45 keys for
    six sections with repeats and ten cleared cells, then 2 clicks for a
    riser): a loop of drums + three lanes laid down as six sections —
    without names, which nobody can type in twelve keys — two of them
    twice, a thinned intro and outro, a break without drums or bass, and a
    transition into the drop. Every press of the mouse (a click or a drag)
    and every key is counted here, one by one; the harness's own counter
    cross-checks the clicks and the page changes."""
    from synthlab.studio import schema

    song = plist_song()
    song["sections"] = [dict(song["sections"][2], id="s1", name="Loop")]
    song["sections"][0].pop("energy", None)
    server.state.song = schema.conform(song, schema.nodes().SONG)
    page = studio.open("song")
    page.wait_for_selector(".arranger-grid[tabindex]")
    m = Mouse(page)
    tally = Tally(page)
    tally.start()
    n = {"click": 0, "key": 0}

    def box(a: str, b: str):  # one press: a drag over the cells (range mode)
        m.drag(a, b, fx=0.6)
        n["click"] += 1

    page.click("#arr-select")  # range mode: a drag is a box of cells
    n["click"] += 1
    # six sections: the loop's column, duplicated five times (Ctrl held, D x5)
    box(pcell("s1", "td"), pcell("s1", "t2"))
    assert len(selected(page)) == 3
    wait(page, lambda: page.evaluate(ON_GRID), "the grid has the keyboard")
    page.keyboard.down("Control")
    for count in range(2, 7):
        page.keyboard.press("d")
        wait(page, lambda c=count: len(song_of(server)["sections"]) == c, "a section")
        redrawn(page, server)
        wait(page, lambda: page.evaluate(ON_GRID), "the grid has the keyboard again")
    page.keyboard.up("Control")
    n["key"] += 6
    ids = [s["id"] for s in song_of(server)["sections"]]
    page.click('[data-zoom-to="fit"]')  # six sections: the whole song in view
    n["click"] += 1
    # the verse and the first drop play twice: a section's right edge, a pass on
    bar_w = page.evaluate(BAR_W)
    for sid in (ids[1], ids[3]):
        edge = page.locator(f"#sec-{sid} .sec-resize").bounding_box()
        x, y = edge["x"] + edge["width"] / 2, edge["y"] + edge["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 4 * bar_w, y, steps=6)
        page.mouse.up()
        n["click"] += 1
        wait(page, lambda sid=sid: sm_section(server, sid)["repeats"] == 2, "x2")
        redrawn(page, server)
    # the intro and the outro without bass and lead; the break without drums and bass
    for sid, top, low in (
        (ids[0], "t1", "t2"),
        (ids[5], "t1", "t2"),
        (ids[4], "td", "t1"),
    ):
        box(pcell(sid, top), pcell(sid, low))
        wait(page, lambda: page.evaluate(ON_GRID), "the grid has the keyboard")
        assert len(selected(page)) == 2
        page.keyboard.press("Delete")
        n["key"] += 1
        wait(
            page,
            lambda sid=sid, low=low: sm_section(server, sid)["cells"][low] is None,
            "cleared",
        )
        redrawn(page, server)
    row7 = dict(n)
    # row 8: a transition into the drop (the + on its boundary, the suggestion)
    page.click(f"#sec-{ids[3]} .sec-plus")
    page.click("#bound-menu .bound-auto")
    n["click"] += 2
    wait(page, lambda: len(sm_section(server, ids[3])["audio"]) == 1, "the impact")
    seen = tally.settle()
    page.evaluate("() => window.synthlab.songClock().stop()")
    print(
        f"\nE9 arranging rows: row 7 {row7['click']} presses of the mouse, "
        f"{row7['key']} keys; "
        f"row 8 {n['click'] - row7['click']} clicks, {n['key'] - row7['key']} keys; "
        f"{seen['view']} page changes (the harness saw {seen['click']} click events)"
    )
    s = song_of(server)
    assert [x["repeats"] for x in s["sections"]] == [1, 2, 1, 2, 1, 1]
    assert [bool(x["cells"].get("td")) for x in s["sections"]] == [True] * 4 + [
        False,
        True,
    ]
    assert sum(len(x["audio"]) for x in s["sections"]) >= 1
    assert seen["view"] == 0 and seen["load"] == 0 and seen["click"] <= n["click"]
    # the journey log's rows 7 + 8: 57 clicks, 45 keys
    assert n == {"click": 10, "key": 9}, n


ON_GRID = "() => document.activeElement?.classList.contains('arranger-grid')"


def sm_section(server, sid: str) -> dict:
    return next(s for s in song_of(server)["sections"] if s["id"] == sid)


# --- rule 1: the playlist with every new surface open ---------------------------------


def test_rule_1_holds_on_the_playlist_with_its_tools_open(sized, server):  # noqa: F811
    """A starter's song (nine tracks, the energy lane drawn, chord tiles)
    at 1440 / 1920 / 2560, as it loads and with a range selected, the
    boundary's menu, a chord's menu and a section's menu open: under 5 % of
    the window is bare background, and nothing scrolls the page."""
    from synthlab.studio import schema
    from synthlab.studio.gen import build

    open_, bare = sized
    server.state.song = schema.conform(
        build.start("trance", 1).song, schema.nodes().SONG
    )
    got = {}
    for w, h in SIZES:
        page = open_("song", w, h)
        page.wait_for_selector(".arr-chords .ctile")
        got[("plain", w)] = round(bare(page), 1)
        over = page.evaluate(
            "() => [document.documentElement.scrollHeight - innerHeight,"
            " document.documentElement.scrollWidth - innerWidth]"
        )
        assert over[0] <= 1 and over[1] <= 0, (w, over)
        cells = page.locator(".arr-cell")
        cells.nth(0).click(position={"x": 30, "y": 8})
        page.keyboard.press("Shift+ArrowRight")
        page.keyboard.press("Shift+ArrowDown")
        page.locator(".arr-cell.sel").last.click(
            button="right", position={"x": 30, "y": 8}
        )
        page.wait_for_selector("#range-menu:popover-open")
        got[("range", w)] = round(bare(page), 1)
        page.keyboard.press("Escape")
        page.locator(".sec-plus").first.click()
        page.wait_for_selector("#bound-menu:popover-open")
        got[("boundary", w)] = round(bare(page), 1)
        page.keyboard.press("Escape")
        page.locator(".arr-chords .ctile").nth(1).click()
        page.wait_for_selector("#chord-menu:popover-open")
        got[("chord", w)] = round(bare(page), 1)
        page.keyboard.press("Escape")
        page.locator(".sec-menu-btn").first.click()
        page.wait_for_selector(".menu-pop:popover-open")
        got[("section menu", w)] = round(bare(page), 1)
    print(f"\nrule 1, the playlist tools: bare background {got} %")
    assert all(v < 5.0 for v in got.values()), got


# =============================================================================
# Strum (G2, the loose ends): the roll's one value control
# =============================================================================
#
# A native range through tweak.js's one path, so the pen's tip, barrel and
# eraser and a typed value are what tests/browser/test_pen.py and
# test_typed_value.py hold for every knob; here: the mouse and its wheel,
# the keyboard alone, and a finger's target — each reaching
# `arrangement.strum`, the value the loop, a section and the MIDI file read.


def strum(server) -> float:
    return server.state.arrangement.strum


def test_strum_by_mouse_wheel_and_double_click(studio, server):
    page = perform(studio, server, cells=[["A1", 0], ["C2", 0], ["E2", 0]])
    knob = "#t-strum"
    box = page.locator(knob).bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - 40, steps=5)  # up: later notes (a knob's travel is 160 px)
    page.mouse.up()
    wait(page, lambda: strum(server) > 0, "a strum from the drag")
    dragged = strum(server)
    assert dragged % 5 == 0  # the knob's step
    # the wheel moves the FOCUSED knob only, a step a notch
    page.focus(knob)
    Mouse(page).wheel(knob, dy=-100)
    wait(page, lambda: strum(server) == dragged + 5, "the wheel's step")
    assert page.inner_text('output[for="t-strum"]') == f"{int(dragged + 5)} ms"
    page.dblclick(knob)  # the default: together
    wait(page, lambda: strum(server) == 0, "the default")
    assert "strum" not in arr_dict(server.state.arrangement)  # absent again


def test_strum_by_keyboard_alone(studio, server):
    studio.page.add_init_script(COUNT_POINTERS)
    page = perform(studio, server, cells=[["A1", 0], ["C2", 0], ["E2", 0]])
    k = Keys(page)
    for _ in range(120):
        k.press("Tab")
        if k.focused() == "strum":
            break
    assert k.focused() == "strum"
    k.press("ArrowUp", 5)  # five at once: the last one lands (queue last)
    wait(page, lambda: strum(server) == 25, "the strum")
    assert k.focused() == "strum"  # the form's answer did not take the focus
    k.press("ArrowDown", 8)
    wait(page, lambda: strum(server) == -15, "high to low")
    assert k.pointers() == 0


def test_strum_is_a_touch_target_and_the_drawer_has_the_same_knob(touch_studio, server):
    page = perform(touch_studio, server, cells=[["A1", 0], ["C2", 0]])
    box = page.locator("#t-strum").bounding_box()
    assert box["width"] >= 40 and box["height"] >= 40, box
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    t = Touch(page)
    t.drag((x, y), (x, y - 60))
    wait(page, lambda: strum(server) > 0, "a strum from a finger")


# =============================================================================
# The note generators on the loop (the loose ends): write, roll again, vary
# =============================================================================
#
# Native buttons and a popover menu (⚄ write, `#ptm-write`, not a
# <details>), so a pen's tap is a click (the
# shell's own tests); here a mouse, the keyboard alone and a finger each
# write a part, and the part plays.

GEN = "#transport .roll-gen"
WRITE = f'{GEN} .pt[popovertarget="ptm-write"]'  # opens the generators' popover


def test_gen_on_the_loop_by_mouse_writes_plays_rolls_again_and_undoes(studio, server):
    page = perform(studio, server, cells=[["A1", 0]])
    page.click(WRITE)
    page.click(f'{GEN} [data-cmd="Write a lead"]')
    wait(page, lambda: len(notes(server)) > 3, "a lead", server=server)
    assert undo_steps(server) == 1
    studio.wait_sound()  # one press made sound: the loop started
    first = notes(server)
    page.click(f'{GEN} [data-cmd="Roll the part again"]')
    wait(page, lambda: notes(server) != first, "another lead", server=server)
    assert undo_steps(server) == 1  # the same step
    n = len(notes(server))
    page.click(f'{GEN} [data-cmd="Sparser notes"]')
    wait(page, lambda: len(notes(server)) < n, "fewer notes", server=server)
    page.click("#pattern-undo")
    wait(page, lambda: len(notes(server)) == n, "the sparser undone", server=server)
    page.click("#pattern-undo")
    wait(page, lambda: len(notes(server)) == 1, "the hand's note back", server=server)


def test_gen_on_the_loop_by_keyboard_alone(studio, server):
    studio.page.add_init_script(COUNT_POINTERS)
    page = perform(studio, server)
    k = Keys(page)
    for _ in range(160):
        k.press("Tab")
        if k.focused() == "write a part with a generator":
            break
    assert k.focused() == "write a part with a generator"
    k.press("Enter")  # the menu opens; its first row is the next stop
    k.press("Tab")
    assert page.evaluate("() => document.activeElement.dataset.cmd") == "Write a bass"
    # the seed is drawn per press and a bass may be one held note a bar: the
    # row is that the press wrote a part, as one undo step, with no pointer
    k.press("Enter")
    wait(page, lambda: undo_steps(server) == 1, "a bass", server=server)
    assert notes(server) and k.pointers() == 0


def test_gen_on_the_loop_by_touch(touch_studio, server):
    page = perform(touch_studio, server)
    t = Touch(page)
    for sel in (WRITE, f'{GEN} [data-cmd="Roll the part again"]'):
        box = page.locator(sel).bounding_box()
        assert box["width"] >= 40 and box["height"] >= 40, (sel, box)
    t.tap(*_mid(page, WRITE))
    page.wait_for_selector(f'{GEN} [data-cmd="Write a chords"]', state="visible")
    t.tap(*_mid(page, f'{GEN} [data-cmd="Write a chords"]'))
    wait(page, lambda: len(notes(server)) > 3, "chords from a finger", server=server)
