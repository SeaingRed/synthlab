"""Journey: the pen (island static/pen.js).

Every pen here is SYNTHETIC: `PointerEvent`s dispatched in headless
Chromium with `pointerType: "pen"`, a `pressure` and the `button` /
`buttons` the Pointer Events spec gives a pen's tip (0 / 1), barrel button
(2 / 2) and eraser end (5 / 32). That proves what the page does with those
events — each assertion reads the scratch studio's state, written by the
grids' and forms' own posts. One journey also drives a TRUSTED pen through
the DevTools protocol (tip and barrel only: it has no eraser), which has a
live pointer the page can capture. None of it proves that a Surface pen sends
them: palm rejection, Windows Ink's flicks and press-and-hold ring, hover
height, the real pressure curve, and whether Chromium reports the eraser
as button 5 need a real pen and a person to check.
"""

from __future__ import annotations

NOTE_ROWS = "#transport .grid .gridrow:not(.sliderow)"

# pen(selector | element, type, {pressure, button, buttons}): one pen event
# at the element's centre. A tap's `click` is the browser's, made from a
# trusted pointer; a synthetic pointerup makes none, so `tap` adds it for
# the tip (button 0) only — a barrel or eraser contact fires no click.
PEN = """
(() => {
  const at = (el) => {
    const b = el.getBoundingClientRect();
    return { clientX: b.left + b.width / 2, clientY: b.top + b.height / 2 };
  };
  const node = (t) => (typeof t === "string" ? document.querySelector(t) : t);
  window.pen = (target, type, o = {}) => {
    const el = node(target);
    const hit = o.over ? node(o.over) : el;
    return el.dispatchEvent(new PointerEvent(type, {
      bubbles: true, cancelable: true, composed: true, isPrimary: true,
      pointerId: 7, pointerType: o.type || "pen",
      pressure: o.pressure ?? 0, button: o.button ?? 0, buttons: o.buttons ?? 0,
      ...at(hit), ...(o.dx ? { clientX: at(hit).clientX + o.dx } : {}),
    }));
  };
  window.penTap = (target, o = {}) => {
    const el = node(target), button = o.button ?? 0;
    pen(el, "pointerover"); pen(el, "pointerdown", { ...o, buttons: o.buttons ?? 1 });
    pen(el, "pointerup", { button });
    if (button === 0) el.click();
  };
})();
"""


def _wait(page, check, what: str, timeout: float = 5.0):
    waited = 0.0
    while waited < timeout:
        if check():
            return
        page.wait_for_timeout(50)
        waited += 0.05
    raise AssertionError(f"timed out waiting for {what}")


def _cells(server) -> set:
    return {tuple(c) for c in server.state.arrangement.cells}


def _perform(studio, server):
    studio.page.add_init_script(PEN)
    page = studio.open("perform")
    studio.settle()
    for pitch, step in sorted(_cells(server)):
        page.locator(f'#transport input.cell[name="cell.{pitch}.{step}"]').click()
    _wait(page, lambda: not _cells(server), "an empty note grid")
    studio.settle()
    row = page.locator(NOTE_ROWS).nth(0)
    pitch = row.locator(".rowlabel").get_attribute("data-pitch")
    return page, pitch


def _cell(pitch: str, step: int) -> str:
    return f'#transport input.cell[name="cell.{pitch}.{step}"]'


def test_pen_pressure_is_the_velocity_of_the_note_it_draws(studio, server):
    page, pitch = _perform(studio, server)
    # a light tap, then a hard one
    page.evaluate("(s) => penTap(s, { pressure: 0.3 })", _cell(pitch, 2))
    _wait(page, lambda: _cells(server) == {(pitch, 2)}, "the light note")
    assert server.state.arrangement.velocities["2"] == 0.55  # 0.25 + pressure
    page.evaluate("(s) => penTap(s, { pressure: 0.9 })", _cell(pitch, 3))
    _wait(page, lambda: _cells(server) == {(pitch, 2), (pitch, 3)}, "the hard note")
    assert server.state.arrangement.velocities["3"] == 1.0
    assert server.state.arrangement.velocities["2"] == 0.55
    # the lane under the grid shows it, as if it had been dragged there
    lane = page.locator('#transport .velrow:not(.chancerow) .vel-lane[data-step="2"]')
    assert lane.get_attribute("aria-valuenow") == "0.55"
    # a mouse reports pressure 0.5 and means nothing by it: no velocity is written
    page.locator(_cell(pitch, 0)).click()
    _wait(page, lambda: (pitch, 0) in _cells(server), "a mouse's note")
    assert server.state.arrangement.velocities.get("0", 0.9) == 0.9
    # a pen turning a note OFF leaves its velocity alone
    page.evaluate("(s) => penTap(s, { pressure: 0.1 })", _cell(pitch, 2))
    _wait(page, lambda: (pitch, 2) not in _cells(server), "the note off")
    assert server.state.arrangement.velocities["2"] == 0.55
    studio.settle()


def test_a_pen_stroke_paints_each_step_at_its_own_pressure(studio, server):
    page, pitch = _perform(studio, server)
    page.locator(_cell(pitch, 0)).scroll_into_view_if_needed()
    page.evaluate(
        """([a, b, c]) => {
          pen(a, "pointerover"); pen(a, "pointerdown", { pressure: 0.2, buttons: 1 });
          // pressing harder on the first step, onto the second, and
          // lightly across the third
          pen(a, "pointermove", { pressure: 0.4, buttons: 1 });
          pen(a, "pointermove", { pressure: 0.5, buttons: 1, over: b });
          pen(a, "pointermove", { pressure: 0.1, buttons: 1, over: c });
          pen(a, "pointerup", { over: c });
        }""",
        [_cell(pitch, 0), _cell(pitch, 1), _cell(pitch, 2)],
    )
    _wait(page, lambda: _cells(server) == {(pitch, s) for s in (0, 1, 2)}, "the stroke")
    vel = server.state.arrangement.velocities
    assert (vel["0"], vel["1"], vel["2"]) == (0.65, 0.75, 0.35)
    studio.settle()
    # the drum grid is the same markup with its own lane and its own form
    page.evaluate(
        "() => penTap('#perform-drum-grid .drumrow input.cell', { pressure: 0.5 })"
    )
    _wait(page, lambda: server.state.arrangement.drums, "a pen-drawn hit")
    assert server.state.arrangement.drum_vel["0"] == 0.75
    studio.settle()


def test_the_eraser_end_erases_a_tap_and_a_drag(studio, server):
    page, pitch = _perform(studio, server)
    other = (
        page.locator(NOTE_ROWS).nth(1).locator(".rowlabel").get_attribute("data-pitch")
    )
    for step in (0, 1, 2, 3):
        page.locator(_cell(pitch, step)).click()
    _wait(page, lambda: len(_cells(server)) == 4, "four notes")
    studio.settle()
    # a tap with the eraser end: that step goes (no click is made for button 5)
    page.evaluate(
        "(s) => penTap(s, { button: 5, buttons: 32, pressure: 0.5 })", _cell(pitch, 3)
    )
    _wait(page, lambda: _cells(server) == {(pitch, s) for s in (0, 1, 2)}, "one erased")
    # the eraser on an empty step draws nothing
    page.evaluate(
        "(s) => penTap(s, { button: 5, buttons: 32, pressure: 0.5 })", _cell(other, 0)
    )
    # a drag with it: every step it crosses goes, as a right-drag does
    page.evaluate(
        """([a, b]) => {
          pen(a, "pointerdown", { button: 5, buttons: 32, pressure: 0.5 });
          pen(a, "pointermove", { buttons: 32, pressure: 0.5, over: b });
          pen(a, "pointerup", { button: 5, over: b });
        }""",
        [_cell(pitch, 0), _cell(pitch, 1)],
    )
    _wait(page, lambda: _cells(server) == {(pitch, 2)}, "two erased by the drag")
    studio.settle()
    assert (other, 0) not in _cells(server)


def test_the_barrel_button_is_shift_and_a_held_pen_opens_no_menu(studio, server):
    page, pitch = _perform(studio, server)
    for step in (1, 2):
        page.locator(_cell(pitch, step)).click()
    _wait(page, lambda: len(_cells(server)) == 2, "a two-step run")
    studio.settle()
    assert not server.state.arrangement.cuts
    # barrel + tap on the run's second step: a Shift-click — a cut, not an erase
    page.evaluate(
        """(s) => {
          penTap(s, { button: 2, buttons: 2, pressure: 0.4 });
          // the browser's own answer to a barrel tap: a contextmenu, which
          // grid.js reads as "right-click erases"
          window.__menu = document.querySelector(s).dispatchEvent(
            new PointerEvent("contextmenu", {
              bubbles: true, cancelable: true, pointerType: "pen", button: 2 }));
        }""",
        _cell(pitch, 2),
    )
    _wait(page, lambda: server.state.arrangement.cuts == [[pitch, 2]], "the cut")
    assert _cells(server) == {(pitch, 1), (pitch, 2)}
    assert page.evaluate("window.__menu") is False  # cancelled: no menu, no erase
    # a pen held still on a step (Windows' press-and-hold = right-click)
    # would be read as an erase; a mouse's right-click still is
    page.evaluate(
        """(s) => { const c = document.querySelector(s);
          c.dispatchEvent(new PointerEvent("contextmenu", {
            bubbles: true, cancelable: true, pointerType: "pen" })); }""",
        _cell(pitch, 1),
    )
    page.wait_for_timeout(400)
    assert (pitch, 1) in _cells(server)
    page.wait_for_timeout(500)  # (the pen's last contact is behind us)
    page.locator(_cell(pitch, 1)).click(button="right")
    _wait(page, lambda: (pitch, 1) not in _cells(server), "a mouse's right-click erase")
    studio.settle()


# Every `input` / `change` a slider fires, and whether its contextmenu was
# cancelled: who wrote the value, how often, and in what order.
WATCH = """
(() => {
  window.__ev = [];
  for (const t of ["input", "change", "contextmenu"]) {
    document.addEventListener(t, (e) => {
      if (e.target.matches?.("input[type=range]")) {
        window.__ev.push(t === "contextmenu" ? `menu:${e.defaultPrevented}`
          : `${t}:${e.target.value}`);
      }
    });
  }
})();
"""


def _trusted_pen(page, cdp, kind: str, x: float, y: float, barrel: bool) -> None:
    """A TRUSTED pen event (CDP): a live pointer id the page can capture, as
    a device's is — which a `dispatchEvent`ed PointerEvent's is not. CDP
    offers the tip and the barrel button; it has no eraser (button 5).
    `barrel` False is a hover: nothing held."""
    held = barrel and kind != "mouseReleased"
    cdp.send(
        "Input.dispatchMouseEvent",
        {
            "type": kind,
            "x": x,
            "y": y,
            "pointerType": "pen",
            "force": 0.5,
            "button": "right" if barrel else "none",
            "buttons": 2 if held else 0,
            "clickCount": 1 if barrel and kind != "mouseMoved" else 0,
        },
    )


def test_a_barrel_drag_on_a_slider_is_fine_exactly_once(studio, server):
    """The overlap this pass removed: tweak.js (a tenth) and pen.js (an
    eighth) both took a pen's barrel + drag on a slider. With a trusted pen
    both wrote the value on every move — 0.27 then 0.26, 0.30 then 0.29 —
    the last writer (a tenth) won and two `change`s followed. Now one island
    owns it: one `input` a move, one `change`, a tenth of the travel."""
    studio.page.add_init_script(PEN + WATCH)
    page = studio.open("editor")
    studio.settle()
    page.evaluate(
        "() => document.getElementById('p-resonance')"
        ".scrollIntoView({ block: 'center' })"
    )
    scroll = page.evaluate("() => [window.scrollX, window.scrollY]")
    lo, hi, step = (
        float(page.get_attribute("#p-resonance", a)) for a in ("min", "max", "step")
    )
    box = page.locator("#p-resonance").bounding_box()
    start = server.state.patch.resonance
    # a slider never pans under any pointer (style.css; pen.js no longer sets it)
    touch = "() => { const s = document.getElementById('p-resonance');"
    touch += " return [getComputedStyle(s).touchAction, s.style.touchAction]; }"
    assert page.evaluate(touch) == ["none", ""]
    # hover comes before contact: the value, as the slider's tooltip (tweak.js)
    page.evaluate("() => pen('#p-resonance', 'pointerover')")
    assert page.evaluate(touch) == ["none", ""]
    assert page.get_attribute("#p-resonance", "title") == page.inner_text(
        'output[for="p-resonance"]'
    )

    def fine(dx: float) -> float:
        return round((start + (dx / box["width"]) * (hi - lo) * 0.1) / step) * step

    # --- a trusted pen: the pointer is captured, as on a device ----------------
    cdp = page.context.new_cdp_session(page)
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    _trusted_pen(page, cdp, "mouseMoved", x, y, False)
    page.evaluate("window.__ev.length = 0")
    _trusted_pen(page, cdp, "mousePressed", x, y, True)
    for dx in (20, 40, 60, 80):
        _trusted_pen(page, cdp, "mouseMoved", x + dx, y, True)
    _trusted_pen(page, cdp, "mouseReleased", x + 80, y, True)
    _wait(
        page,
        lambda: abs(server.state.patch.resonance - fine(80)) < 1e-6,
        "the fine drag: a tenth, not an eighth and not both",
    )
    events = page.evaluate("window.__ev.splice(0)")
    values = [float(e.split(":")[1]) for e in events if e.startswith("input:")]
    # one write a move, each further than the last: nobody wrote it twice
    assert values == sorted(set(values)) and 2 <= len(values) <= 4, events
    assert [e for e in events if e.startswith("change:")] == [f"change:{values[-1]:g}"]
    # the barrel button is a right click: no browser menu, and after a DRAG
    # no menu of ours either
    page.wait_for_timeout(200)
    assert not page.is_visible("#ctx-menu")
    assert page.evaluate("'pen' in document.documentElement.dataset")  # (tweak.js)
    assert page.evaluate("() => [window.scrollX, window.scrollY]") == scroll
    studio.settle()
    # --- the same with synthetic events (no live pointer to capture) -----------
    page.dispatch_event("#p-resonance", "dblclick")  # back to the default
    _wait(page, lambda: server.state.patch.resonance == start, "the reset")
    page.evaluate("window.__ev.length = 0")
    down = page.evaluate(
        """() => {
          const down = pen('#p-resonance', 'pointerdown',
            { button: 2, buttons: 2, pressure: 0.5 });
          pen('#p-resonance', 'pointermove', { buttons: 2, pressure: 0.5, dx: 80 });
          pen('#p-resonance', 'pointerup', { button: 2, dx: 80 });
          return down;
        }"""
    )
    assert down is False  # the browser's own drag (a jump to the pen) was cancelled
    _wait(
        page,
        lambda: abs(server.state.patch.resonance - fine(80)) < 1e-6,
        "the synthetic fine drag",
    )
    want = f"{fine(80):g}"
    assert page.evaluate("window.__ev") == [f"input:{want}", f"change:{want}"]
    # the grid's own touch rule (sideways paints, vertical scrolls) is a
    # finger's; under a pen it does not pan at all, and a mouse hands it back
    studio.nav("perform")
    page.wait_for_selector("#transport .grid")
    grid_touch = "() => document.querySelector('#transport .grid').style.touchAction"
    page.evaluate("() => pen('#transport .grid .cell', 'pointerover')")
    assert page.evaluate(grid_touch) == "none"
    page.evaluate(
        "() => pen('#transport .grid .cell', 'pointerover', { type: 'mouse' })"
    )
    assert page.evaluate(grid_touch) == ""
    studio.settle()


def test_the_eraser_resets_a_slider_and_the_barrel_tap_opens_its_menu(studio, server):
    """A value control under a pen is tweak.js's and nothing of pen.js's:
    the eraser end is the default (on a step it erases:
    test_the_eraser_end_erases_a_tap_and_a_drag), a barrel TAP is the
    control's own menu (on a step it is a Shift-click)."""
    studio.page.add_init_script(PEN + WATCH)
    page = studio.open("editor")
    studio.settle()
    page.evaluate(
        "() => document.getElementById('p-cutoff').scrollIntoView({ block: 'center' })"
    )
    default = float(page.get_attribute("#p-cutoff", "data-default"))
    page.evaluate(
        "() => window.synthlab.tweak(document.getElementById('p-cutoff'), 0.9, true)"
    )
    _wait(page, lambda: server.state.patch.cutoff > default * 2, "a move to reset")
    studio.settle()
    page.evaluate("window.__ev.length = 0")
    down = page.evaluate(
        "() => { pen('#p-cutoff', 'pointerover');"
        " const d = pen('#p-cutoff', 'pointerdown',"
        " { button: 5, buttons: 32, pressure: 0.4 });"
        " pen('#p-cutoff', 'pointerup', { button: 5 }); return d; }"
    )
    assert down is False  # not the browser's jump-to-the-pointer
    # THE default: exactly what a double-click posts (a log slider's is
    # between two positions), written once
    _wait(page, lambda: server.state.patch.cutoff == default, "the eraser's reset")
    events = page.evaluate("window.__ev.splice(0)")
    assert len(events) == 1 and events[0].startswith("input:"), events
    studio.settle()
    # a barrel tap: the press, the release, then the right click it is
    page.evaluate(
        """() => {
          pen('#p-cutoff', 'pointerdown', { button: 2, buttons: 2, pressure: 0.4 });
          pen('#p-cutoff', 'pointerup', { button: 2 });
          window.__menu = document.getElementById('p-cutoff').dispatchEvent(
            new PointerEvent('contextmenu', { bubbles: true, cancelable: true,
              pointerType: 'pen', button: 2, clientX: 300, clientY: 300 }));
        }"""
    )
    assert page.evaluate("window.__menu") is False  # never the browser's menu
    page.wait_for_selector("#ctx-menu:popover-open")
    assert page.locator("#ctx-menu .menu-item").first.inner_text() == "Learn MIDI"
    # a tap moved nothing and posted nothing
    assert [e for e in page.evaluate("window.__ev") if not e.startswith("menu")] == []
    assert server.state.patch.cutoff == default
    page.keyboard.press("Escape")
    page.wait_for_selector("#ctx-menu", state="hidden")
    studio.settle()


def test_pen_pressure_is_a_piano_keys_velocity(studio, server):
    studio.page.add_init_script(PEN)
    page = studio.open("editor")
    studio.settle()
    page.evaluate(
        """() => { window.__vel = [];
          const was = window.synthlab.onNoteOn;
          window.synthlab.onNoteOn = (m, v) => {
            window.__vel.push(v); was?.(m, v); }; }"""
    )
    key = "#piano .pkey"
    page.evaluate(
        "(k) => { pen(k, 'pointerdown', { pressure: 0.4, buttons: 1 });"
        " pen(k, 'pointerup'); }",
        key,
    )
    page.evaluate(
        "(k) => { pen(k, 'pointerdown', { pressure: 0.9, buttons: 1 });"
        " pen(k, 'pointerup'); }",
        key,
    )
    # a mouse: "no pressure information" (0.5) plays at full velocity
    page.evaluate(
        "(k) => { pen(k, 'pointerdown', { type: 'mouse', pressure: 0.5, buttons: 1 });"
        " pen(k, 'pointerup', { type: 'mouse' }); }",
        key,
    )
    assert [round(v, 6) for v in page.evaluate("window.__vel")] == [0.65, 1, 1]
    studio.wait_sound()
    page.evaluate("() => window.synthlab.piano.noteOff?.(7)")
    studio.settle()
