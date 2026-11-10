"""Journey 11: the shell — the command palette, the shortcut sheet,
tooltips, the status strip, and the pen / slider layer.

Proves, in a real Chromium under the shipped CSP: Ctrl+K opens the palette
from the page but not from a text field; typing narrows the page's own
declared commands and Enter runs one by activating its control (a nav
link, a button with an htmx post, a <select>'s option); a sound is found
by the server and loads through its own Use post; `?` opens the sheet,
which lists the declared shortcuts and narrows as you type; a `title`
becomes a tooltip in the top layer on hover and on keyboard focus and is
handed back; the status strip follows the session and carries the live
voice's meter on a page that never had one; a pen contact switches the
dense tier on for good (and "forget the pen" off again); Shift-drag is a
tenth of a slider's travel, a knob drags vertically, the wheel moves only
a focused slider.

Does not prove any of it looks right, and a synthetic pen event is not a
Surface pen: no test here has a digitizer.
"""

from __future__ import annotations

import pytest

HX = {"HX-Request": "true"}


def _open_palette(page):
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette[open]")


def _labels(page) -> list[str]:
    return page.eval_on_selector_all(
        "#pal-list .pal-item", "els => els.map(e => e.firstChild.textContent)"
    )


def test_the_palette_runs_the_pages_own_commands(studio):
    page = studio.open("editor")
    _open_palette(page)
    assert page.evaluate("document.activeElement.id") == "pal-q"
    first = _labels(page)
    assert "Add a layer" in first and "Go to Library" in first
    # the navigation leads (it fell off the end of an 80-row list when the
    # page's own commands came first), then the page's commands with its
    # primary action's group in front, then the shell's
    assert first[0] == "Go to Synths"
    assert first.index("Go to Library") < first.index("Add a layer")
    assert first.index("Save sound…") < first.index("Add a layer")
    assert first.index("Add a layer") < first.index("Density: dense / comfy")
    # narrowing, then a nav link run by name
    page.keyboard.type("go lib")
    assert _labels(page)[0] == "Go to Library"
    page.keyboard.press("Enter")
    page.wait_for_selector('#view[data-view="library"]')
    assert not page.evaluate("document.getElementById('palette').open")
    assert page.url.endswith("/view/library")
    # Esc closes; the list is harvested again for the new page
    _open_palette(page)
    assert "Add a layer" not in _labels(page)
    page.keyboard.press("Escape")
    page.wait_for_selector("#palette:not([open])", state="attached")
    studio.settle()


def test_the_palette_lists_the_control_surfaces_own_commands(studio):
    """The deck's actions are `data-cmd` buttons like any other (its rail
    button and the panel's own), so the palette finds them by name — with
    the panel shut — and running one is a click on that button."""
    page = studio.open("library")
    assert not page.is_visible("#deck-panel")
    _open_palette(page)
    first = _labels(page)
    # nothing typed: the rail's toggle is listed with the other View
    # commands, and the page's own still come first
    assert "Stream Deck: show / hide the virtual deck" in first
    page.keyboard.type("stream deck")
    found = _labels(page)
    for name in (
        "Stream Deck: show / hide the virtual deck",
        "Stream Deck: connect / disconnect",
        "Deck dials: macros / focus",
        "Deck strip: values / meters",
    ):
        assert name in found, (name, found)
    page.keyboard.press("Control+a")
    page.keyboard.type("show hide deck")
    assert _labels(page)[0] == "Stream Deck: show / hide the virtual deck"
    page.keyboard.press("Enter")
    page.wait_for_selector("html[data-deck] #deck-panel", state="visible")
    assert page.get_attribute("#deck-btn", "aria-pressed") == "true"
    # macros <-> focus, by name: the same button a deck key presses
    assert page.inner_text("#deck-mode") == "Macros"
    _open_palette(page)
    page.keyboard.type("dials macros focus")
    assert _labels(page)[0] == "Deck dials: macros / focus"
    page.keyboard.press("Enter")
    page.wait_for_function(
        "() => window.synthlab.deck.state().mode === 'focus'"
        " && document.getElementById('deck-mode').textContent === 'Focus'"
    )
    # the eight virtual keys are not commands themselves: no duplicates
    names = page.evaluate("() => window.synthlab.commands().map((c) => c.label)")
    assert len(names) == len(set(names))
    assert page.locator("#deck-keys [data-cmd]").count() == 0
    studio.settle()


def test_a_command_is_its_controls_own_post(studio):
    page = studio.open("editor")
    layers = "() => document.querySelectorAll('#layer-strip .lrow:not(.empty)').length"
    before = page.evaluate(layers)
    _open_palette(page)
    page.keyboard.type("add a layer")
    with page.expect_response("**/fragment/layer/add"):
        page.keyboard.press("Enter")
    page.wait_for_function(f"n => ({layers})() === n + 1", arg=before)
    # a <select> is one command per option: the FX rack's adder
    _open_palette(page)
    page.keyboard.type("master reverb")
    assert any("Reverb" in x and "Master" in x for x in _labels(page))
    with page.expect_response("**/fragment/fx/add"):
        page.keyboard.press("Enter")
    page.wait_for_function(
        "() => [...document.querySelectorAll('#fx-rack .fx-scope')].some("
        "s => /master/i.test(s.querySelector('legend').textContent)"
        " && /Reverb/.test("
        "s.textContent.replace(s.querySelector('legend').textContent, '')))"
    )
    studio.settle()


def test_the_palette_keeps_out_of_a_text_field_and_moves_by_keyboard(studio):
    page = studio.open("editor")
    page.click("#dock-filter")
    page.keyboard.press("Control+k")
    page.wait_for_timeout(150)
    assert not page.evaluate("document.getElementById('palette').open")
    page.keyboard.press("Escape")
    page.evaluate("document.activeElement.blur()")
    _open_palette(page)
    selected = "() => document.querySelector('#pal-list [aria-selected=\"true\"]').id"
    assert page.evaluate(selected) == "pal-0"
    page.keyboard.press("ArrowDown")
    assert page.evaluate(selected) == "pal-1"
    assert page.get_attribute("#pal-q", "aria-activedescendant") == "pal-1"
    page.keyboard.press("ArrowUp")
    page.keyboard.press("ArrowUp")  # wraps to the last
    assert page.evaluate(selected) != "pal-0"
    page.keyboard.press("Control+k")  # the same key closes it
    page.wait_for_selector("#palette:not([open])", state="attached")
    studio.settle()


def test_the_palette_finds_a_saved_sound_through_the_server(studio):
    page = studio.open("library")
    page.request.post(f"{studio.url}/fragment/preset/save", form={"name": "glass pad"})
    _open_palette(page)
    with page.expect_response("**/fragment/palette?q=*"):
        page.keyboard.type("glass pad")
    page.wait_for_function(
        "() => [...document.querySelectorAll('#pal-list .pal-item')]"
        ".some(e => e.firstChild.textContent === 'glass pad')"
    )
    assert _labels(page)[0] == "glass pad"
    with page.expect_response("**/fragment/preset/load"):
        page.keyboard.press("Enter")
    # the page re-renders around the new sound (a preset's Use is not a
    # move to Synths: `svc.adopt`), and the status strip names it
    page.wait_for_function(
        "() => document.querySelector('#status-bar .st-sound b').textContent"
        " === 'glass pad'"
    )
    studio.settle()


def test_the_shortcut_sheet_lists_declared_shortcuts_and_narrows(studio):
    page = studio.open("song")
    page.keyboard.press("?")
    page.wait_for_selector("#shortcuts[open]")
    commands = page.inner_text("#sc-commands")
    # (an empty song's Play is disabled: its key is still its key)
    assert "Command palette" in commands and "Play the song" in commands
    assert "Undo the last song edit" in commands and "Space" in commands
    page.fill("#sc-q", "octave down")
    shown = page.eval_on_selector_all(
        "#shortcuts dd", "els => els.filter(e => !e.hidden).map(e => e.textContent)"
    )
    # the static sheet's line first, then the keyboard dock's own octave
    # commands (every page's): nothing but the octave
    assert shown[0] == "octave down / up"
    assert all("octave" in s.lower() for s in shown), shown
    page.fill("#sc-q", "zzzz")
    assert page.is_visible("#sc-none")
    page.keyboard.press("Escape")
    page.wait_for_selector("#shortcuts:not([open])", state="attached")
    studio.settle()


def test_a_title_becomes_a_tooltip_and_is_handed_back(studio):
    page = studio.open("editor")
    title = page.get_attribute("#click-btn", "title")
    assert title.startswith("metronome")
    page.hover("#click-btn")
    page.wait_for_selector("#tip:popover-open")
    assert page.inner_text("#tip") == title
    assert page.get_attribute("#click-btn", "title") is None  # lent, not shown twice
    tip = page.locator("#tip").bounding_box()
    vw = page.viewport_size
    assert 0 <= tip["x"] and tip["x"] + tip["width"] <= vw["width"]
    page.mouse.move(700, 500)
    page.wait_for_selector("#tip:not(:popover-open)", state="attached")
    assert page.get_attribute("#click-btn", "title") == title
    # a press never leaves a tooltip behind
    page.hover("#keys-help")
    page.click("#keys-help")
    page.wait_for_selector("#shortcuts[open]")
    page.wait_for_timeout(700)
    assert not page.evaluate("document.getElementById('tip').matches(':popover-open')")
    page.keyboard.press("Escape")
    # keyboard focus shows it at once: the tooltip of whatever Tab landed on,
    # and the control keeps its title while it has the focus
    page.focus("#keys-help")
    page.keyboard.press("Tab")
    page.wait_for_function(
        "() => document.getElementById('tip').matches(':popover-open')"
        " && document.activeElement.id === 'density-toggle'"
        " && document.getElementById('tip').textContent"
        " === document.activeElement.getAttribute('title')"
    )
    assert "dense" in page.inner_text("#tip")
    page.keyboard.press("Escape")
    page.wait_for_selector("#tip:not(:popover-open)", state="attached")
    studio.settle()


def test_the_page_head_keeps_its_instructions_behind_a_button(studio):
    page = studio.open("song")
    assert not page.is_visible("#head-how")
    page.click(".head-help")
    page.wait_for_selector("#head-how:popover-open")
    box = page.locator("#head-how").bounding_box()
    assert box["width"] > 200 and box["x"] >= 0
    assert box["x"] + box["width"] <= page.viewport_size["width"]
    page.keyboard.press("Escape")
    page.wait_for_selector("#head-how:not(:popover-open)", state="attached")
    studio.settle()


_ACCENT = (
    "() => getComputedStyle(document.documentElement).getPropertyValue('--accent')"
)


def test_the_colours_popover_turns_a_hue_and_the_page_keeps_it(studio):
    """The rail's Colours button opens a popover of hue sliders (style.css
    section 1's --hue-* variables): moving the accent's moves every accent
    on the page, the choice survives a reload — applied by colours.js in
    <head>, before the first paint — and Reset gives the theme its own
    hue back. Stored per browser (localStorage), never posted."""
    page = studio.open("editor")
    studio.settle()
    posts = []
    # (the scope's idle picture primes its own preview: motion.js, not ours)
    page.on(
        "request",
        lambda r: (
            r.method == "POST"
            and "/fragment/preview" not in r.url
            and posts.append(r.url)
        ),
    )
    own = page.evaluate(_ACCENT)  # resolved: the theme's amber (the day's, here)
    depth, hue = own.rsplit(" ", 1)
    hue = int(hue.rstrip(")"))
    turned = f"{depth} {hue + 5})"
    page.click("#colours-btn")
    page.wait_for_selector("#colours-pop:popover-open")
    box = page.locator("#colours-pop").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"]
    accent = page.locator('#colours-pop input[data-hue="accent"]')
    assert accent.input_value() == accent.get_attribute("data-default") == str(hue)
    assert page.inner_text("#colours-pop label:has([data-hue=accent]) output") == (
        f"{hue} °"
    )
    accent.focus()
    for _ in range(5):
        page.keyboard.press("ArrowRight")
    assert page.evaluate(_ACCENT) == turned
    # the focus ring is the accent too: one variable
    assert page.evaluate(_ACCENT.replace("--accent", "--focus")).strip() == turned
    page.keyboard.press("Escape")
    assert not posts  # (the slider has no name and posts nothing)
    page.reload()
    page.wait_for_selector('#view[data-view="editor"]')
    assert page.evaluate(
        "() => document.documentElement.style.getPropertyValue('--hue-accent')"
    ) == str(hue + 5)
    assert page.evaluate(_ACCENT) == turned
    page.click("#colours-btn")
    page.wait_for_selector("#colours-pop:popover-open")
    assert accent.input_value() == str(hue + 5)
    page.click("#colours-reset")
    assert page.evaluate(_ACCENT) == own
    assert accent.input_value() == str(hue)
    assert page.evaluate("() => localStorage.getItem('synthlab.colours')") is None
    studio.settle()


_DEPTH = """(name) => { const p = document.createElement('i'); document.body.append(p);
  p.style.color = `var(${name})`; const c = getComputedStyle(p).color; p.remove();
  return parseFloat(c.slice(c.indexOf('(') + 1)); }"""


def test_contrast_and_black_level_move_the_ladder_and_the_page_keeps_them(studio):
    """The Colours popover's two depth sliders: black level takes the
    night's page to L 0.04 (an OLED black) with its panels still a step
    above it, contrast pulls the text off the page — both as two variables
    on <html> (`--black-level`, `--contrast`) that every `--l-*` depth is
    derived from, said with `synthlab-colours` (the deck and the visuals
    re-read on it), kept per browser before the first paint, and lifted by
    Reset."""
    page = studio.open("editor")
    studio.settle()
    page.evaluate("() => { document.documentElement.dataset.theme = 'night'; }")
    said = "() => window.__said || 0"
    page.evaluate(
        "() => document.addEventListener('synthlab-colours',"
        " () => { window.__said = (window.__said || 0) + 1; })"
    )
    bg, raised, text = (
        page.evaluate(_DEPTH, n) for n in ("--bg", "--bg-raised", "--text")
    )
    assert abs(bg - 0.16) < 0.005 and abs(text - 0.92) < 0.005
    page.click("#colours-btn")
    page.wait_for_selector("#colours-pop:popover-open")
    black = page.locator('#colours-pop input[data-depth="black"]')
    contrast = page.locator('#colours-pop input[data-depth="contrast"]')
    assert black.get_attribute("data-default") == "0"
    assert contrast.get_attribute("data-default") == "100"
    black.focus()
    page.keyboard.press("End")
    assert (
        page.inner_text("#colours-pop label:has([data-depth=black]) output") == "100 %"
    )
    oled = page.evaluate(_DEPTH, "--bg")
    assert (
        oled < 0.045
        and page.evaluate(_DEPTH, "--bg-raised") - oled >= raised - bg - 0.005
    )
    assert page.evaluate(said) >= 1
    contrast.focus()
    page.keyboard.press("End")
    assert page.evaluate(_DEPTH, "--text") > text
    assert page.evaluate(
        "() => JSON.parse(localStorage.getItem('synthlab.colours'))"
    ) == {"black": 100, "contrast": 140}
    page.keyboard.press("Escape")
    # before the first paint, after a reload
    page.reload()
    page.wait_for_selector('#view[data-view="editor"]')
    style = page.evaluate("() => document.documentElement.style.cssText")
    assert "--black-level: 1" in style and "--contrast: 1.4" in style, style
    page.evaluate("() => { document.documentElement.dataset.theme = 'night'; }")
    assert page.evaluate(_DEPTH, "--bg") < 0.045
    page.click("#colours-btn")
    page.wait_for_selector("#colours-pop:popover-open")
    assert black.input_value() == "100" and contrast.input_value() == "140"
    page.click("#colours-reset")
    assert abs(page.evaluate(_DEPTH, "--bg") - 0.16) < 0.005
    assert black.input_value() == "0" and contrast.input_value() == "100"
    assert page.evaluate("() => localStorage.getItem('synthlab.colours')") is None
    studio.settle()


def test_the_status_strip_follows_the_session_on_every_page(studio):
    page = studio.open("library")
    bpm = studio.server.state.arrangement.bpm
    assert page.inner_text("#status-bar .st-tempo").replace("\n", " ") == f"{bpm:g} bpm"
    studio.nav("editor")
    page.fill('#sound-bar input[name="bpm"]', "98")
    page.keyboard.press("Tab")
    page.wait_for_function(
        "() => document.querySelector('#status-bar .st-tempo b').textContent === '98'"
    )
    # the live voice's meter, on a page that never drew one
    studio.nav("library")
    studio.start_voice()
    assert "voice" in studio.meter()
    assert page.is_visible("#cpu-meter")
    studio.settle()


_TOASTS = """() => [...document.querySelectorAll('#toasts .toast')].map(t =>
  [t.className, t.getAttribute('role'), getComputedStyle(t).borderLeftColor])"""


def test_a_toast_wears_its_kind_and_lets_the_pointer_through(studio):
    """A toast is not always `toast error role=alert` with a red edge
    ("Saved…" included), and it does not sit over the head's tools for 8 s."""
    page = studio.open("editor")
    studio.settle()
    page.evaluate("() => window.synthlab.toast('Added Loop 1 to the song')")
    [(cls, role, info_edge)] = page.evaluate(_TOASTS)
    assert (cls, role) == ("toast", "status")
    page.evaluate("() => window.synthlab.toast('Kept it', 'done')")
    assert page.evaluate(_TOASTS)[0][:2] == ["toast done", "status"]
    # the server's HX-Trigger carries the kind (perform.js hands it on)
    page.evaluate(
        "() => document.body.dispatchEvent(new CustomEvent('studio-toast',"
        " {bubbles: true, detail: {message: 'The render failed', kind: 'error'}}))"
    )
    [(cls, role, error_edge)] = page.evaluate(_TOASTS)
    assert (cls, role) == ("toast error", "alert") and error_edge != info_edge
    # a press on the toast lands on what it covers, which can be seen
    box = page.locator("#toasts .toast").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    under = page.evaluate(
        "([x, y]) => !document.elementFromPoint(x, y).closest('#toasts')", [x, y]
    )
    assert under
    page.mouse.move(x, y)
    page.wait_for_selector("#toasts .toast.see-through")
    page.mouse.move(5, 500)
    page.wait_for_selector("#toasts .toast:not(.see-through)")
    studio.settle()


_FOCUS = """() => { const a = document.activeElement;
  return [a.id, a.className, a.closest('#view') ? 'view' : '',
          a.closest('.sound-card')?.dataset.name || ''] }"""


def test_the_keyboard_skips_to_the_page_and_the_sounds_are_one_stop(studio):
    """The keyboard must not take 501 Tabs to ▶ on Synths (the rail, then
    3-6 stops a sound card), nor 96 to Perform's first note cell."""
    page = studio.open("editor")
    studio.settle()
    asked = []
    page.on("request", lambda r: "/load" in r.url and asked.append(r.url))
    page.keyboard.press("Tab")
    assert "skip-link" in page.evaluate(_FOCUS)[1]
    page.keyboard.press("Enter")
    tabs, at = 0, None
    while tabs < 40:
        page.keyboard.press("Tab")
        tabs += 1
        at = page.evaluate(_FOCUS)
        if at[0] == "play":
            break
    assert at[0] == "play", (tabs, at)  # it was ~500
    # the list is one stop: only the cursor's card takes Tab
    stops = page.evaluate(
        "() => [...document.querySelectorAll("
        "'#preset-list .sound-card :is(button, summary)')]"
        ".filter(b => b.tabIndex >= 0).length"
    )
    assert stops == 1
    first = page.locator("#preset-list .sound-card .load[tabindex='0']")
    first.focus()
    name = page.evaluate(_FOCUS)[3]
    page.keyboard.press("ArrowDown")
    nxt = page.evaluate(_FOCUS)
    assert nxt[3] and nxt[3] != name and "load" in nxt[1]
    page.keyboard.press("ArrowUp")
    assert page.evaluate(_FOCUS)[3] == name
    page.keyboard.press("Space")  # hears it: no load
    page.wait_for_timeout(300)
    assert not asked
    # Perform: two keys from the page to the note grid's one stop
    studio.nav("perform")
    page.wait_for_selector("#transport .grid .cell[tabindex='0']", state="attached")
    page.focus(".skip-link[href='#view']")
    page.keyboard.press("Enter")
    page.keyboard.press("Tab")
    assert "Skip to the note grid" in page.evaluate(
        "() => document.activeElement.textContent"
    )
    page.keyboard.press("Enter")
    page.wait_for_function(
        "() => document.activeElement.matches('#transport .grid .cell')"
    )
    studio.settle()


_PEN = """([sel, type, buttons]) => {
  const el = document.querySelector(sel), r = el.getBoundingClientRect();
  el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true,
    pointerType: "pen", pointerId: 7, buttons, button: buttons & 32 ? 5 : 0,
    clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
}"""


def test_a_pen_turns_the_dense_tier_on_for_good(studio):
    page = studio.open("editor")
    assert page.get_attribute("#dense-css", "media") == "(pointer: fine)"
    page.evaluate(_PEN, ["#view h1", "pointerdown", 1])
    assert page.evaluate("'pen' in document.documentElement.dataset")
    assert page.get_attribute("#dense-css", "media") == "all"
    assert "pen=1" in page.evaluate("document.cookie")
    # the server draws it that way from now on
    page.reload()
    page.wait_for_function("() => !!window.synthlab?.piano")
    assert page.evaluate("'pen' in document.documentElement.dataset")
    assert page.get_attribute("#dense-css", "media") == "all"
    # the eraser on a slider is the page's own reset (double-click)
    page.evaluate(
        "() => { const s = document.getElementById('p-osc1_level'); s.value = 0.2;"
        " s.dispatchEvent(new Event('input', { bubbles: true })); }"
    )
    page.evaluate(_PEN, ["#p-osc1_level", "pointerdown", 32])
    page.wait_for_function(
        "() => { const s = document.getElementById('p-osc1_level');"
        " return Math.abs(+s.value - +s.dataset.default) < 1e-6; }"
    )
    # and it can be forgotten
    page.keyboard.press("?")
    page.click("#pen-off")
    assert not page.evaluate("'pen' in document.documentElement.dataset")
    assert page.get_attribute("#dense-css", "media") == "(pointer: fine)"
    page.keyboard.press("Escape")
    studio.settle()


def test_shift_drag_is_fine_and_the_wheel_needs_focus(studio):
    page = studio.open("editor")
    slider = page.locator("#volume")
    slider.scroll_into_view_if_needed()
    box = slider.bounding_box()
    y = box["y"] + box["height"] / 2
    value = "() => +document.getElementById('volume').value"
    start = page.evaluate(value)
    # the wheel over an unfocused slider is the page's scroll, not a tweak
    page.mouse.move(box["x"] + box["width"] / 2, y)
    page.mouse.wheel(0, -120)
    assert page.evaluate(value) == start
    # Shift + a drag across half the track moves a twentieth of the travel
    page.keyboard.down("Shift")
    page.mouse.move(box["x"] + box["width"] * 0.25, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.75, y, steps=6)
    page.mouse.up()
    page.keyboard.up("Shift")
    moved = page.evaluate(value) - start
    assert 0.03 <= moved <= 0.07, moved
    # focused, the wheel steps it
    slider.focus()
    at = page.evaluate(value)
    page.mouse.move(box["x"] + box["width"] / 2, y)
    page.mouse.wheel(0, 120)
    page.wait_for_function(f"v => ({value})() < v", arg=at)
    studio.settle()


def test_a_knob_is_a_range_input_dragged_vertically(studio):
    """The component and its drag are proven on one built here from the
    same markup a view would use."""
    page = studio.open("library")
    page.evaluate(
        """() => {
          const k = Object.assign(document.createElement("input"), {
            type: "range", id: "tk", className: "knob", min: "0", max: "100",
            step: "1" });
          k.value = "50"; k.style.setProperty("--fill", "50%");
          window.__k = { input: 0, change: 0 };
          k.addEventListener("input", () => window.__k.input++);
          k.addEventListener("change", () => window.__k.change++);
          document.querySelector(".view-head").append(k);
        }"""
    )
    box = page.locator("#tk").bounding_box()
    assert abs(box["width"] - box["height"]) < 1 and box["width"] <= 44  # a dial
    assert (
        page.evaluate("getComputedStyle(document.getElementById('tk')).touchAction")
        == "none"
    )
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx, cy + 40, steps=4)  # down = less: 40 px of 160 = a quarter
    page.mouse.up()
    assert page.evaluate("+document.getElementById('tk').value") == 25
    counts = page.evaluate("window.__k")
    assert counts["input"] >= 1 and counts["change"] == 1
    # the keyboard is the native slider's
    page.focus("#tk")
    page.keyboard.press("ArrowUp")
    assert page.evaluate("+document.getElementById('tk').value") == 26
    studio.settle()


@pytest.mark.parametrize("view", ["editor", "song", "perform", "library"])
def test_the_dense_page_never_scrolls_sideways(studio, view):
    page = studio.open(view)
    page.wait_for_load_state("networkidle")
    wide = page.evaluate(
        "() => document.documentElement.scrollWidth"
        " - document.documentElement.clientWidth"
    )
    assert wide <= 0, f"{view} is {wide}px wider than the window"
    # the visualisers share the width half and half; the status strip is a
    # thin row under them, across the dock
    scope = page.locator("#scope").bounding_box()
    spectrum = page.locator("#spectrum").bounding_box()
    strip = page.locator(".statusbar").bounding_box()
    assert abs(scope["width"] - spectrum["width"]) <= 1
    assert strip["y"] >= scope["y"] + scope["height"] and strip["height"] <= 24
    dock = page.locator(".topdock").bounding_box()
    top_h = page.evaluate(
        "() => parseFloat(getComputedStyle(document.documentElement)"
        ".getPropertyValue('--top-h'))"
    )
    assert abs(dock["height"] - top_h) <= 1, (dock["height"], top_h)


def test_the_clip_library_pins_beside_the_page_on_a_wide_window(studio):
    page = studio.page
    page.set_viewport_size({"width": 1920, "height": 1080})
    studio.open("song")
    page.click("#clip-library .clips-btn")
    page.wait_for_selector("#clip-drawer:popover-open")
    width = "() => document.getElementById('view').getBoundingClientRect().width"
    before = page.evaluate(width)
    page.click("#clip-pin")
    assert page.evaluate("document.documentElement.dataset.clips") == "pinned"
    assert page.get_attribute("#clip-pin", "aria-pressed") == "true"
    assert "clips=pinned" in page.evaluate("document.cookie")
    # the page makes room: the drawer is a column, not a sheet over it
    assert before - page.evaluate(width) == pytest.approx(340, abs=2)
    page.keyboard.press("Escape")  # closes the popover; pinned, it stays drawn
    page.wait_for_selector("#clip-drawer:not(:popover-open)")
    box = page.locator("#clip-drawer").bounding_box()
    assert (
        box["height"] == pytest.approx(1080, abs=2) and box["x"] + box["width"] == 1920
    )
    # the server draws it that way on the next load, and a view swap keeps it
    page.reload()
    page.wait_for_function("() => !!window.synthlab?.piano && !!window.htmx")
    assert page.is_visible("#clip-drawer")
    studio.nav("library")
    assert page.is_visible("#clip-drawer")
    assert page.get_attribute("#clip-pin", "aria-pressed") == "true"
    page.click("#clip-pin")
    assert not page.is_visible("#clip-drawer")
    assert "clips=pinned" not in page.evaluate("document.cookie")
    # under 1600 px there is no pin and no column
    page.set_viewport_size({"width": 1400, "height": 900})
    page.click("#clip-library .clips-btn")
    page.wait_for_selector("#clip-drawer:popover-open")
    assert not page.is_visible("#clip-pin")
    page.keyboard.press("Escape")
    studio.settle()
