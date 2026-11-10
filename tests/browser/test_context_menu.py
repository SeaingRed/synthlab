"""THE context menu (static/menu.js), on every view, by every way in.

Four packages each built one (a value control's, the Song grid's, the
shelves', the bench's); the integration pass made them one mechanism:
`data-ctx` + `data-menu` on a thing, `data-ctx-pop` on a thing that
already owns a menu panel, generated rows on a value control. This drives
one thing a view by a right click, by the keyboard (Shift+F10 — the Menu
key's twin — on the focused control: arrows move, Esc closes and the focus
returns) and by a pen's barrel tap (a `contextmenu` with pointerType
"pen"), and checks that the step grids keep their two older rules: a right
click on a step ERASES, and a pen's `contextmenu` on a pen surface (the
grids) opens nothing.

NOT covered: a real pen (the barrel as a right click is Chromium's
mapping; it is synthesised here), a finger's long press (the browser's own
`contextmenu`: nothing of ours to drive), how the menu looks.
"""

from __future__ import annotations

from pathlib import Path

from tests.browser.test_browse_views import _close, _open, _page, seeded  # noqa: F401
from tests.browser.test_song_playlist import playlist_song, use

STATIC = Path("synthlab/studio/static")

# view -> (what a pointer presses, the control the keyboard is on, the
# popover / panel that must open, whether a pen's barrel opens it too)
THINGS = {
    "editor": ("#lrow-0 .l-plays", "#l0-m", "#ctx-menu:popover-open", True),
    "perform": (
        "#perform-drums .rowlabel",
        "#perform-drums .rowbtn.mute",
        ".row-menu-panel:popover-open",
        False,
    ),
    "drums": (
        "#drum-grid .rowlabel",
        "#drum-grid .rowbtn.mute",
        ".row-menu-panel:popover-open",
        False,
    ),
    "song": (
        ".arr-cell.filled .cell-btn",
        ".arr-cell.filled .cell-btn",
        "#cell-menu:popover-open",
        True,
    ),
    "breed": (
        "#breed-board .tile .t-name",
        "#breed-board .tile .loop-btn",
        "#ctx-menu:popover-open",
        True,
    ),
    "map": (".map-row .map-name", ".map-row", "#ctx-menu:popover-open", True),
    "library": (
        ".lib-card .lib-name",
        ".lib-card .lib-play",
        "#ctx-menu:popover-open",
        True,
    ),
    "voice": ("#view select", "#view select", "#ctx-menu:popover-open", True),
    "lab": (
        ".lab-source .lab-meta",
        ".lab-source button",
        "#ctx-menu:popover-open",
        True,
    ),
}

PEN = """(sel) => {
  const el = document.querySelector(sel), r = el.getBoundingClientRect();
  const at = { clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
  const ev = (type, o) => el.dispatchEvent(new PointerEvent(type, { bubbles: true,
    cancelable: true, composed: true, isPrimary: true, pointerId: 7,
    pointerType: "pen", ...at, ...o }));
  ev("pointerover", {}); ev("pointerdown", { button: 2, buttons: 2, pressure: 0.4 });
  ev("pointerup", { button: 2 });
  return ev("contextmenu", { button: 2 });
}"""

CLOSE = """() => { for (const p of document.querySelectorAll(":popover-open"))
  if (p.matches("#ctx-menu, #cell-menu, .menu-pop, .row-menu-panel"))
    p.hidePopover(); }"""


def test_one_menu_on_every_view_by_mouse_keyboard_and_pen(chromium, seeded):  # noqa: F811
    use(seeded, playlist_song())
    ctx, page, errors = _page(chromium, seeded, 1920, 1080)
    try:
        for view, (press, focus, opened, pen_opens) in THINGS.items():
            _open(page, seeded, view)
            page.wait_for_load_state("networkidle")
            assert page.locator(press).count(), (view, press)
            # a right click
            page.locator(press).first.click(button="right")
            page.wait_for_selector(opened)
            assert page.locator("#ctx-menu").count() <= 1  # one element, ever
            page.evaluate(CLOSE)
            # the keyboard: Shift+F10 on the focused control
            page.locator(focus).first.focus()
            page.keyboard.press("Shift+F10")
            page.wait_for_selector(opened)
            if opened.endswith(":popover-open"):
                inside = "() => !!document.activeElement.closest(':popover-open')"
                assert page.evaluate(inside), (view, "the menu took the focus")
                first = page.evaluate("() => document.activeElement.textContent")
                page.keyboard.press("ArrowDown")
                page.keyboard.press("ArrowUp")
                assert (
                    page.evaluate("() => document.activeElement.textContent") == first
                )
                page.keyboard.press("Escape")
                page.wait_for_selector(opened, state="hidden")
                back = page.evaluate(
                    "(sel) => document.activeElement === document.querySelector(sel)",
                    focus,
                )
                assert back, (view, "Esc hands the focus back")
            page.evaluate(CLOSE)
            # a pen's barrel tap
            cancelled = page.evaluate(PEN, press) is False
            assert cancelled, (view, "never the browser's own menu under a pen")
            if pen_opens:
                page.wait_for_selector(opened)
            else:  # a pen surface (the grids): pen.js cancels it, nothing opens
                page.wait_for_timeout(150)
                assert page.locator(opened).count() == 0, view
            page.evaluate(CLOSE)
        # a page's title is in no section: the browser's own menu
        _open(page, seeded, "bass")
        page.locator("#view h1").click(button="right")
        page.wait_for_timeout(150)
        assert page.locator(":popover-open").count() == 0
        # the grids keep their right click: a step erases, no menu
        _open(page, seeded, "drums")
        step = page.locator('#drum-grid input[name="drum.kick.0"]')
        if not step.is_checked():
            step.click()
        step.click(button="right")
        assert not step.is_checked()
        assert page.locator(":is(.row-menu-panel, #ctx-menu):popover-open").count() == 0
        # a value control inside a thing: its own rows, then the thing's
        _open(page, seeded, "editor")
        page.locator("#l0-gain").click(button="right")
        page.wait_for_selector("#ctx-menu:popover-open")
        rows = page.locator("#ctx-menu .menu-item").all_inner_texts()
        assert rows[:2] == ["Learn MIDI", "Reset to default"] and "Mute" in rows
        # (the control's, the layer's, then the section it sits in: the strip's)
        titles = page.locator("#ctx-menu .ctx-title").all_inner_texts()
        assert len(titles) == 3 and titles[2].lower() == "layers", titles
        assert not errors, errors
    finally:
        _close(ctx, page)


def test_there_is_one_context_menu_implementation():
    """One opener, one element, one declaration convention: no island but
    menu.js opens a menu on `contextmenu`; grid.js's listener is the step
    erase and pen.js's the pen surfaces' cancel."""
    code = {p.name: p.read_text(encoding="utf-8") for p in STATIC.glob("*.js")}
    listeners = {n for n, js in code.items() if 'addEventListener("contextmenu"' in js}
    # (steps.js's opens a HIT's menu from a long press or the Menu key on a
    # drum step — the one place a right click is taken, by the erase, so
    # menu.js never hears those two there)
    assert listeners == {"menu.js", "grid.js", "pen.js", "steps.js"}, listeners
    assert not (STATIC / "ctlmenu.js").exists()
    makers = {n for n, js in code.items() if '"ctx-menu"' in js or "ctl-menu" in js}
    assert makers == {"menu.js"}, makers
    for name in ("grid.js", "pen.js", "bench.js", "tweak.js"):
        assert "data-ctx" not in code[name].split("(() => {", 1)[1], name
        assert "dataset.ctx" not in code[name], name
    css = (STATIC / "style.css").read_text(encoding="utf-8") + (
        STATIC / "dense.css"
    ).read_text(encoding="utf-8")
    assert css.count("\n.ctx-menu {") == 1 and "#ctl-menu" not in css
    # a thing names its menu one of two ways, never both
    templates = Path("synthlab/studio/templates")
    for p in templates.rglob("*.html"):
        for tag in __import__("re").findall(
            r"<[a-z][^<>]*data-ctx[^<>]*>", p.read_text(encoding="utf-8")
        ):
            assert not ("data-ctx=" in tag and "data-ctx-pop=" in tag), (
                p.name,
                tag[:80],
            )


# what the open menu says: its captions (their text, not the CSS's capitals) and rows
_MENU = """() => [...document.querySelectorAll('#ctx-menu > *')].map((n) =>
  n.matches('hr') ? '--' : n.matches('.ctx-title') ? '# ' + n.textContent
  : n.firstChild.textContent + (n.hasAttribute('aria-haspopup') ? ' >' : ''))"""
# a right click's `contextmenu`, synthesised: true when nothing cancelled it
# (the browser's own menu would show)
_NATIVE = """(sel) => {
  const el = document.querySelector(sel), r = el.getBoundingClientRect();
  return el.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true,
    cancelable: true, button: 2, clientX: r.left + 4, clientY: r.top + r.height / 2 }));
}"""


def test_a_sections_own_menu_on_every_kind_of_panel(chromium, seeded):  # noqa: F811
    """A right click on a section's background lists its own actions — each
    control's `data-cmd`, generated, and its dials onto the deck — under its
    name; the sections round it are submenus (→ opens, ← comes back). Text
    fields, a link and selected text keep the browser's menu."""
    ctx, page, errors = _page(chromium, seeded, 1600, 1000)
    try:
        _open(page, seeded, "editor")
        # a Synths panel: its legend's name, its dials, its ⋯'s commands
        page.locator("#panel-filter .panel-name").click(button="right")
        page.wait_for_selector("#ctx-menu:popover-open")
        menu = page.evaluate(_MENU)
        assert menu[0] == "# Filter", menu
        assert menu[1].startswith("Put these on the deck (")
        assert {"Add to ★ Yours", "MIDI learn these"} <= set(menu)
        assert {"Defaults for the Filter panel", "Randomise the Filter panel"} <= set(
            menu
        )
        with page.expect_response("**/fragment/panel/dice"):
            page.locator(
                "#ctx-menu .menu-item", has_text="Randomise the Filter"
            ).click()
        page.wait_for_selector("#ctx-menu", state="hidden")
        # nested: an FX unit (a thing that is a section), then its rack's
        # fieldset and the whole rack, as submenus
        with page.expect_response("**/fragment/fx/add"):
            page.evaluate(
                "() => htmx.ajax('POST', '/fragment/fx/add', {values: {scope: 'layer',"
                " kind: 'reverb'}, target: '#fx-rack', swap: 'outerHTML'})"
            )
        page.wait_for_timeout(300)
        unit = page.locator("#fx-rack .fx-unit").first
        unit.scroll_into_view_if_needed()
        unit.locator("header strong").click(button="right")
        page.wait_for_selector("#ctx-menu:popover-open")
        menu = page.evaluate(_MENU)
        assert menu[0].startswith("# Reverb"), menu
        assert "Remove from the chain" in menu and "Put these on the deck" in menu
        subs = [m for m in menu if m.endswith(" >")]
        assert subs and subs[-1] == "Effects >", menu
        page.locator("#ctx-menu [aria-haspopup]").first.focus()
        page.keyboard.press("ArrowRight")
        page.wait_for_selector("#ctx-menu .menu-back")
        inner = page.evaluate(_MENU)
        assert inner[0].startswith("◂ Reverb") and inner[1] == "# " + subs[0][:-2], (
            inner
        )
        assert any(m.startswith("Put these on the deck") for m in inner)
        page.keyboard.press("ArrowLeft")
        page.wait_for_function("() => !document.querySelector('#ctx-menu .menu-back')")
        assert page.evaluate(_MENU) == menu
        page.keyboard.press("Escape")
        page.wait_for_selector("#ctx-menu", state="hidden")
        # the browser's own menu: a link in a section (the chain bar's), selected text
        assert page.evaluate(_NATIVE, "#signal-chain a.stage")
        page.evaluate(
            "() => getSelection().selectAllChildren("
            "document.querySelector('#panel-filter .panel-name'))"
        )
        assert page.evaluate(_NATIVE, "#panel-filter .panel-name")
        page.evaluate("() => getSelection().removeAllRanges()")
        assert not page.evaluate(_NATIVE, "#panel-filter .panel-name")  # the menu again
        page.keyboard.press("Escape")
        # the Clips page: its search field is the browser's
        _open(page, seeded, "clips")
        assert page.evaluate(_NATIVE, '#hub-filter input[name="find"]')
        assert page.locator("#ctx-menu:popover-open").count() == 0
        # the Visuals page: a card's menu runs the card's own buttons
        _open(page, seeded, "visuals")
        card = page.locator('#view .vis-card[data-vis="phosphor"]')
        card.locator(".vis-note").click(button="right")
        page.wait_for_selector("#ctx-menu:popover-open")
        menu = page.evaluate(_MENU)
        assert menu[0] == "# Phosphor" and "Dock scope: Phosphor" in menu, menu
        page.locator("#ctx-menu .menu-item", has_text="Dock scope: Phosphor").click()
        page.wait_for_function(
            "() => window.synthlab.visuals.choice('scope') === 'phosphor'"
        )
        # the gallery round it has nothing of its own: it lists the sections in it
        card.locator(".vis-note").click(button="right")
        page.wait_for_selector("#ctx-menu:popover-open")
        assert page.evaluate(_MENU)[-1] == "Variations >"
        page.locator("#ctx-menu .menu-item", has_text="Variations").click()
        page.wait_for_selector("#ctx-menu .menu-back")
        menu = page.evaluate(_MENU)
        assert menu[1] == "# Variations" and "Phosphor >" in menu, menu
        page.keyboard.press("Escape")
        assert not errors, errors
    finally:
        _close(ctx, page)
