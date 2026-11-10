"""One driver an input, for the input matrix (`test_input_matrix.py`).

- `Mouse`: Playwright's own mouse (trusted events) and its wheel, with
  modifiers.
- `Keys`: nothing but the keyboard. `pointers()` is how many pointer or
  mouse presses the page has seen: a keyboard journey asserts it is 0.
- `Pen`: SYNTHETIC `PointerEvent`s with `pointerType: "pen"`, a pressure and
  the `button` / `buttons` the Pointer Events spec gives the tip (0 / 1),
  the barrel (2 / 2) and the eraser end (5 / 32) — test_pen.py's method.
- `Touch`: TRUSTED touches through the DevTools protocol
  (`Input.dispatchTouchEvent`) in a context with a touch screen: the
  browser makes the pointer events, the click and its own gestures from
  them. A long press is the one synthetic piece (see `Touch.long_press`).

What none of them is: a hand. See the module docstring of
`test_input_matrix.py` for what that leaves unproven.
"""

from __future__ import annotations

COUNT_POINTERS = """
window.__pointers = 0;
for (const t of ["pointerdown", "mousedown", "touchstart"])
  addEventListener(t, (e) => { if (e.isTrusted) window.__pointers++; }, true);
"""

PEN = """
(() => {
  const node = (t) => (typeof t === "string" ? document.querySelector(t) : t);
  // one pen event on `target`, at fraction (fx, fy) of its box (default: the middle)
  // (scrolled into its pane first, as Mouse.drag does: a pen cannot reach a
  // step its pane has scrolled away — the roll's lower rows, since the chord
  // lane and the meter's strip take a row each above them)
  window.penAt = (target, type, o = {}) => {
    const el = node(target);
    el.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    const b = el.getBoundingClientRect();
    return el.dispatchEvent(new PointerEvent(type, {
      bubbles: true, cancelable: true, composed: true, isPrimary: true,
      pointerId: 7, pointerType: "pen",
      pressure: o.pressure ?? 0, button: o.button ?? 0, buttons: o.buttons ?? 0,
      clientX: b.left + b.width * (o.fx ?? 0.5),
      clientY: b.top + b.height * (o.fy ?? 0.5),
    }));
  };
  // a tap: the browser makes a tip's `click` from a trusted pointer; a
  // synthetic pointerup makes none, so the tip's is added here
  window.penTap = (target, o = {}) => {
    const el = node(target), button = o.button ?? 0;
    penAt(el, "pointerover");
    penAt(el, "pointerdown", { ...o, buttons: o.buttons ?? 1 });
    penAt(el, "pointerup", { ...o, button });
    if (button === 0) el.click();
  };
  // a stroke through `targets`: down on the first, a move over each, up on the last
  window.penDrag = (targets, o = {}) => {
    const els = targets.map(node), buttons = o.buttons ?? 1;
    penAt(els[0], "pointerover");
    penAt(els[0], "pointerdown", { ...o, buttons, fx: o.fx0 ?? o.fx });
    for (const el of els.slice(1)) penAt(el, "pointermove", { ...o, buttons });
    penAt(els.at(-1), "pointerup", { ...o });
  };
})();
"""


def clear_of_dock(page, selector: str) -> None:
    """`selector` scrolled into view and clear of the keyboard dock stuck to
    the window's foot (Playwright's own scroll counts a step under a sticky
    dock as in view: since the chord lane and the meter's strip take a row
    each above it, the roll's lower rows can sit under it)."""
    loc = page.locator(selector).first
    loc.scroll_into_view_if_needed()
    loc.evaluate(
        """(e) => { const under = e.getBoundingClientRect().bottom - (innerHeight
             - (document.getElementById("keys-dock")?.offsetHeight || 0));
           if (under > 0) scrollBy(0, under + 8); }"""
    )


class Mouse:
    def __init__(self, page):
        self.page = page

    def centre(self, selector: str, fx: float = 0.5, fy: float = 0.5):
        box = self.page.locator(selector).first.bounding_box()
        assert box, f"{selector} is not on screen"
        return box["x"] + box["width"] * fx, box["y"] + box["height"] * fy

    def drag(self, a: str, b: str, *, fx: float = 0.5, steps: int = 8, button="left"):
        """Press on `a` (at fraction `fx` of its width), move to `b`'s
        middle in `steps` moves, release. Both are scrolled into view
        first: a pointer cannot reach a step its pane has scrolled away."""
        for sel in (b, a):
            clear_of_dock(self.page, sel)
        x0, y0 = self.centre(a, fx)
        x1, y1 = self.centre(b)
        m = self.page.mouse
        m.move(x0, y0)
        m.down(button=button)
        m.move(x1, y1, steps=steps)
        m.up(button=button)

    def wheel(self, selector: str, dx: float = 0, dy: float = 0, key: str = ""):
        x, y = self.centre(selector)
        self.page.mouse.move(x, y)
        if key:
            self.page.keyboard.down(key)
        self.page.mouse.wheel(dx, dy)
        if key:
            self.page.keyboard.up(key)
        self.page.wait_for_timeout(120)


class Keys:
    def __init__(self, page):
        self.page = page
        self.pressed = 0

    def press(self, key: str, times: int = 1):
        for _ in range(times):
            self.page.keyboard.press(key)
            self.pressed += 1

    def pointers(self) -> int:
        return self.page.evaluate("() => window.__pointers")

    def focused(self) -> str:
        """The focused control's name, else its aria-label."""
        return self.page.evaluate(
            "() => document.activeElement?.name"
            " || document.activeElement?.getAttribute('aria-label') || ''"
        )


class Pen:
    def __init__(self, page):
        self.page = page

    def tap(self, selector: str, **o):
        self.page.evaluate("([s, o]) => penTap(s, o)", [selector, o])

    def drag(self, selectors: list[str], **o):
        self.page.evaluate("([s, o]) => penDrag(s, o)", [selectors, o])

    def hover(self, selector: str):
        self.page.evaluate("(s) => penAt(s, 'pointerover')", selector)


class Touch:
    """Trusted fingers (DevTools protocol)."""

    def __init__(self, page):
        self.page = page
        self.cdp = page.context.new_cdp_session(page)

    def _send(self, kind: str, points: list[tuple[float, float]]):
        self.cdp.send(
            "Input.dispatchTouchEvent",
            {
                "type": kind,
                "touchPoints": [
                    {"x": x, "y": y, "id": i, "radiusX": 6, "radiusY": 6, "force": 1}
                    for i, (x, y) in enumerate(points)
                ],
            },
        )

    def tap(self, x: float, y: float):
        self._send("touchStart", [(x, y)])
        self._send("touchEnd", [])
        self.page.wait_for_timeout(60)

    def drag(self, a, b, steps: int = 8):
        """One finger from `a` to `b`."""
        self._send("touchStart", [a])
        for i in range(1, steps + 1):
            t = i / steps
            self._send(
                "touchMove", [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)]
            )
            self.page.wait_for_timeout(16)
        self.page.wait_for_timeout(150)  # the finger stops before it lifts
        self._send("touchEnd", [])
        self.page.wait_for_timeout(60)

    def two(self, a0, b0, a1, b1, steps: int = 10):
        """Two fingers, from (a0, b0) to (a1, b1): a pinch or a pan."""
        self._send("touchStart", [a0])
        self._send("touchStart", [a0, b0])
        for i in range(1, steps + 1):
            t = i / steps
            pa = (a0[0] + (a1[0] - a0[0]) * t, a0[1] + (a1[1] - a0[1]) * t)
            pb = (b0[0] + (b1[0] - b0[0]) * t, b0[1] + (b1[1] - b0[1]) * t)
            self._send("touchMove", [pa, pb])
            self.page.wait_for_timeout(16)
        self._send("touchEnd", [])
        self.page.wait_for_timeout(80)

    def long_press(self, selector: str):
        """A finger held on `selector`. Headless Chromium on Windows does
        not turn a held synthetic touch into a `contextmenu` (the OS's
        press-and-hold does that for a real finger), so the event the
        browser would send — a `contextmenu` with `pointerType: "touch"` —
        is dispatched here. What the page does with it is real."""
        self.page.evaluate(
            """(s) => {
              const el = document.querySelector(s), b = el.getBoundingClientRect();
              el.dispatchEvent(new PointerEvent("contextmenu", {
                bubbles: true, cancelable: true, composed: true, button: 0,
                pointerType: "touch", pointerId: 9,
                clientX: b.left + b.width / 2, clientY: b.top + b.height / 2 }));
            }""",
            selector,
        )
        self.page.wait_for_timeout(80)
